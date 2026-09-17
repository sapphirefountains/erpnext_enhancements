/*
 * Time Kiosk — the Map view (window.KioskViews.map): the employee's own trail
 * for one day, from get_my_trail.
 *
 * Targets: the Time Kiosk PWA front-end. Loaded by www/kiosk.html after ui.js
 * and geo.js, before app.js — NOT through hooks.py. app.js mounts it into
 * #tk-panel-map with a context object (see app.js init).
 *
 * Leaflet is loaded LAZILY, the first time the tab is opened, from frappe's
 * vendored copy at /assets/frappe/js/lib/leaflet/leaflet.js + leaflet.css. It is
 * deliberately NOT in the service worker's PRECACHE: the worker is registered at
 * root scope and may only answer for the kiosk's own shell
 * (tests/test_kiosk_service_worker.py), and a vendored library never changes,
 * so the browser's own HTTP cache is the right cache for it. Offline, the tab
 * says "The map needs a connection" instead — the tiles need one anyway.
 *
 * Tiles follow the theme: OpenStreetMap in light, CARTO dark_all in dark, swapped
 * live through KioskUI.theme.onChange (which also fires on a system-scheme
 * change while in system mode).
 *
 * Drawing: one polyline per Job Interval (palette per interval), fixes as small
 * circles — Low Accuracy points hollow — start/end anchors as larger rings, and a
 * geofence circle per interval that carries site coordinates.
 */
(function () {
  'use strict';

  var UI = window.KioskUI;
  var h = UI.h;
  var fmt = UI.fmt;

  var LEAFLET_JS = '/assets/frappe/js/lib/leaflet/leaflet.js';
  var LEAFLET_CSS = '/assets/frappe/js/lib/leaflet/leaflet.css';
  var TILES = {
    light: { url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', attribution: '&copy; OpenStreetMap contributors', maxZoom: 19 },
    dark: { url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', attribution: '&copy; OpenStreetMap contributors &copy; CARTO', maxZoom: 19 },
  };
  var COLORS = ['#0b63c4', '#1d7a3e', '#b45f06', '#7c3aed', '#b42318', '#0e7490', '#a21caf'];

  var ctx = null;
  var el = {};
  var st = { date: null, map: null, tile: null, tileTheme: null, layer: null, loading: null, trail: null, visible: false };

  function mount(container, c) {
    ctx = c;
    el.date = h('div', { class: 'tk-map-date' }, [h('span', { id: 'tk-map-date-main' }), h('small', { id: 'tk-map-date-sub' })]);
    el.map = h('div', { class: 'tk-map', id: 'tk-map', role: 'region', 'aria-label': 'Map of your location trail' });
    el.msg = h('div', { class: 'tk-map-msg', hidden: true });
    el.legend = h('div', { class: 'tk-map-legend' });
    el.map.appendChild(el.msg);
    container.appendChild(h('div', { class: 'tk-map-head' }, [
      h('button', { type: 'button', class: 'tk-btn tk-btn-outline tk-btn-icon', 'aria-label': 'Previous day', text: '‹', on: { click: function () { shiftDay(-1); } } }),
      el.date,
      h('button', { type: 'button', class: 'tk-btn tk-btn-outline tk-btn-icon', 'aria-label': 'Next day', text: '›', on: { click: function () { shiftDay(1); } } }),
    ]));
    container.appendChild(el.map);
    container.appendChild(el.legend);
    UI.theme.onChange(function (eff) { if (st.map) applyTiles(eff); });
    window.addEventListener('online', function () { if (st.visible) show(); });
  }

  function show() {
    st.visible = true;
    if (!st.date) st.date = fmt.toISODate(new Date());
    renderDate();
    if (!window.L && navigator.onLine === false) { message('The map needs a connection.'); return; }
    loadLeaflet().then(function () {
      initMap();
      loadTrail();
    }).catch(function () {
      message('The map could not load. Check your connection and try again.');
    });
  }

  function hide() { st.visible = false; }

  function shiftDay(n) {
    var d = fmt.parseISODate(st.date) || new Date();
    d.setDate(d.getDate() + n);
    if (d > new Date()) return;
    st.date = fmt.toISODate(d);
    renderDate();
    if (st.map) loadTrail();
  }

  function renderDate() {
    var main = document.getElementById('tk-map-date-main');
    var sub = document.getElementById('tk-map-date-sub');
    if (main) main.textContent = fmt.dateLabel(st.date, { weekday: 'long', month: 'short', day: 'numeric' });
    if (sub) sub.textContent = st.date === fmt.toISODate(new Date()) ? 'Today' : '';
  }

  function message(text) {
    el.msg.hidden = !text;
    el.msg.textContent = text || '';
  }

  // -- Leaflet ---------------------------------------------------------------
  function loadLeaflet() {
    if (window.L && window.L.map) return Promise.resolve();
    if (st.loading) return st.loading;
    st.loading = new Promise(function (resolve, reject) {
      if (!document.getElementById('tk-leaflet-css')) {
        document.head.appendChild(h('link', { id: 'tk-leaflet-css', rel: 'stylesheet', href: LEAFLET_CSS }));
      }
      var s = document.createElement('script');
      s.src = LEAFLET_JS;
      s.async = true;
      s.onload = function () { if (window.L && window.L.map) resolve(); else reject(new Error('leaflet missing')); };
      s.onerror = function () { st.loading = null; reject(new Error('leaflet failed')); };
      document.head.appendChild(s);
    });
    return st.loading;
  }

  function initMap() {
    if (st.map) { st.map.invalidateSize(); return; }
    st.map = window.L.map(el.map, { zoomControl: true, attributionControl: true, tap: false });
    st.map.setView([40.76, -111.89], 10); // fitBounds replaces this the moment there is a trail
    st.layer = window.L.layerGroup().addTo(st.map);
    applyTiles(UI.theme.effective());
    // The panel was display:none when Leaflet measured it.
    setTimeout(function () { if (st.map) st.map.invalidateSize(); }, 50);
  }

  function applyTiles(theme) {
    var want = theme === 'dark' ? 'dark' : 'light';
    if (st.tileTheme === want) return;
    if (st.tile) { st.map.removeLayer(st.tile); st.tile = null; }
    var t = TILES[want];
    st.tile = window.L.tileLayer(t.url, { attribution: t.attribution, maxZoom: t.maxZoom }).addTo(st.map);
    st.tileTheme = want;
  }

  // -- Trail -----------------------------------------------------------------
  function loadTrail() {
    var want = st.date;
    UI.clear(el.legend);
    el.legend.appendChild(UI.skeleton(2));
    message('');
    ctx.api(ctx.API + 'get_my_trail', { date: want }, { method: 'GET' })
      .then(function (data) {
        if (want !== st.date) return;
        st.trail = data || { points: [], intervals: [] };
        draw(st.trail);
      })
      .catch(function (e) {
        if (want !== st.date) return;
        UI.clear(el.legend);
        el.legend.appendChild(h('p', { class: 'tk-error', text: ctx.humanError(e) }));
        message(navigator.onLine === false ? 'The map needs a connection.' : 'Could not load your trail.');
      });
  }

  function draw(data) {
    var L = window.L;
    st.layer.clearLayers();
    UI.clear(el.legend);
    var points = data.points || [];
    var intervals = data.intervals || [];
    var bounds = [];
    var byInterval = {};
    var colorOf = {};

    intervals.forEach(function (iv, i) { colorOf[iv.name] = COLORS[i % COLORS.length]; byInterval[iv.name] = []; });
    points.forEach(function (p) {
      if (p.latitude == null || p.longitude == null) return;
      var key = p.job_interval && byInterval[p.job_interval] ? p.job_interval : '_none';
      if (!byInterval[key]) byInterval[key] = [];
      byInterval[key].push(p);
    });

    Object.keys(byInterval).forEach(function (key) {
      var list = byInterval[key];
      if (!list.length) return;
      var color = colorOf[key] || '#6b7280';
      var latlngs = list.map(function (p) { return [p.latitude, p.longitude]; });
      latlngs.forEach(function (ll) { bounds.push(ll); });
      if (latlngs.length > 1) L.polyline(latlngs, { color: color, weight: 4, opacity: 0.85 }).addTo(st.layer);
      list.forEach(function (p, i) {
        var low = p.log_status === 'Low Accuracy';
        var isEnd = i === 0 || i === list.length - 1;
        L.circleMarker([p.latitude, p.longitude], {
          radius: isEnd ? 7 : 4,
          color: isEnd ? '#ffffff' : color,
          weight: isEnd ? 3 : (low ? 2 : 1),
          fillColor: color,
          fillOpacity: low ? 0 : (isEnd ? 1 : 0.9),
        }).bindPopup(popupText(p)).addTo(st.layer);
      });
    });

    intervals.forEach(function (iv) {
      if (iv.site_latitude == null || iv.site_longitude == null) return;
      var r = iv.site_radius_m || data.radius_m || 0;
      var color = colorOf[iv.name] || '#6b7280';
      L.circleMarker([iv.site_latitude, iv.site_longitude], { radius: 6, color: color, weight: 2, fillColor: '#ffffff', fillOpacity: 1 })
        .bindPopup(escapeText(iv.project_title || iv.name) + ' (site)').addTo(st.layer);
      if (r > 0) L.circle([iv.site_latitude, iv.site_longitude], { radius: r, color: color, weight: 1, fillColor: color, fillOpacity: 0.08, dashArray: '4 4' }).addTo(st.layer);
      bounds.push([iv.site_latitude, iv.site_longitude]);
    });

    if (bounds.length) {
      try { st.map.fitBounds(bounds, { padding: [24, 24], maxZoom: 17 }); } catch (e) { /* noop */ }
      message('');
    } else {
      message(intervals.length ? 'No location points were recorded for this day.' : 'No jobs on this day.');
    }

    // Legend
    var total = points.length;
    el.legend.appendChild(h('p', { class: 'tk-note', text: total + (total === 1 ? ' point' : ' points') + ' · ' + intervals.length + (intervals.length === 1 ? ' job' : ' jobs') + (points.some(function (p) { return p.log_status === 'Low Accuracy'; }) ? ' · hollow = low accuracy' : '') }));
    intervals.forEach(function (iv) {
      var n = (byInterval[iv.name] || []).length;
      el.legend.appendChild(h('div', { class: 'tk-row is-static', style: { minHeight: '40px', padding: '4px' } }, [
        h('span', { class: 'tk-swatch', style: { background: colorOf[iv.name] } }),
        h('div', { class: 'tk-row-body' }, [
          h('div', { class: 'tk-row-main', text: iv.project_title || iv.name }),
          h('div', { class: 'tk-row-sub', text: fmt.timeOf(iv.start_time) + ' – ' + (iv.end_time ? fmt.timeOf(iv.end_time) : 'now') + ' · ' + n + ' pts' }),
        ]),
      ]));
    });
  }

  function escapeText(s) { return UI.escapeHtml(s); }

  function popupText(p) {
    var bits = [fmt.timeOf(p.timestamp)];
    if (p.accuracy != null) bits.push('±' + Math.round(p.accuracy) + ' m');
    if (p.fix_source) bits.push(p.fix_source);
    if (p.log_status === 'Low Accuracy') bits.push('low accuracy');
    return escapeText(bits.join(' · '));
  }

  window.KioskViews = window.KioskViews || {};
  window.KioskViews.map = { mount: mount, show: show, hide: hide };
})();
