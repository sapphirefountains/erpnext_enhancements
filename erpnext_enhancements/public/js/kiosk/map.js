/*
 * Time Kiosk — the Map view (window.KioskViews.map): the employee's own trail
 * for one day, from get_my_trail.
 *
 * Targets: the Time Kiosk PWA front-end. Loaded by www/kiosk.html after ui.js
 * and geo.js, before app.js — NOT through hooks.py. app.js mounts it into
 * #tk-panel-map with a context object (see app.js init).
 *
 * Leaflet has been replaced by Google Maps (Workstream W2). Google Maps
 * is loaded via the shared loader google_maps_loader.js.
 * Google Maps needs a connection and cannot be precached, so offline
 * the tab says "The map needs a connection."
 * If the API key is missing, it says "The map is not configured with an API key."
 *
 * Tiles follow the theme, swapped live through KioskUI.theme.onChange.
 * Platform constraint: mapId cannot be changed on an existing map via setOptions.
 * Because we use map_id_light and map_id_dark to style the map, we MUST rebuild
 * the map instance on a theme change to apply the new mapId, while preserving
 * the current center, zoom, and overlays.
 *
 * Drawing: one polyline per Job Interval (palette per interval), fixes as markers
 * using AdvancedMarkerElement or SVG (to keep pixel size constant across zooms) —
 * Low Accuracy points hollow — start/end anchors as larger rings, and a
 * geofence circle per interval that carries site coordinates.
 */
(function () {
  'use strict';

  var UI = window.KioskUI;
  var h = UI.h;
  var fmt = UI.fmt;

  var COLORS = ['#0b63c4', '#1d7a3e', '#b45f06', '#7c3aed', '#b42318', '#0e7490', '#a21caf'];

  var ctx = null;
  var el = {};
  var st = { date: null, map: null, layer: [], loading: null, trail: null, visible: false, currentTheme: null };

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
    UI.theme.onChange(function (eff) { if (st.map) applyTheme(eff); });
    window.addEventListener('online', function () { if (st.visible) show(); });
  }

  function show() {
    st.visible = true;
    if (!st.date) st.date = fmt.toISODate(new Date());
    renderDate();

    var mapsConfig = window.KIOSK_BOOT && window.KIOSK_BOOT.maps ? window.KIOSK_BOOT.maps : {};
    if (!mapsConfig.api_key) {
      message('The map is not configured with an API key.');
      return;
    }
    if (!window.google && navigator.onLine === false) { message('The map needs a connection.'); return; }

    loadGoogleMaps(mapsConfig).then(function () {
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

  // 0,0 is not a location, it is a missing one.
  //
  // The server writes NULL when it has no fix, but a site can still arrive as
  // 0/0 -- and `== null` does not catch a zero. One such point drags fitBounds
  // across the Atlantic and renders the whole world with two pins on it, which
  // is exactly what a technician saw. Mirrors workforce/sites.py::_valid_coords
  // and the desk timeline's hasCoords(), which already rejected it.
  function validCoords(lat, lng) {
    if (lat == null || lng == null) return false;
    lat = +lat; lng = +lng;
    if (!isFinite(lat) || !isFinite(lng)) return false;
    if (lat === 0 && lng === 0) return false;
    return lat >= -90 && lat <= 90 && lng >= -180 && lng <= 180;
  }

  function message(text) {
    el.msg.hidden = !text;
    el.msg.textContent = text || '';
  }

  // -- Google Maps -----------------------------------------------------------
  function loadGoogleMaps(mapsConfig) {
    // No early return on `window.google.maps` being present. The namespace exists
    // as soon as the bootstrap runs, but a library is only there once
    // importLibrary has been awaited for it — returning early here would hand back
    // a maps object with no `marker` and fail at the first AdvancedMarkerElement.
    // EEGoogleMaps.load is single-flight and idempotent, so calling it every time
    // is both correct and cheap.
    if (st.loading) return st.loading;
    if (!window.EEGoogleMaps) {
      // The shared loader is a separate <script> in kiosk.html. It is precached by
      // the service worker, so this should only be reachable on a device that has
      // never been online since the deploy -- in which case Google Maps could not
      // load anyway. Fail with a sentence, not a TypeError.
      return Promise.reject(new Error('loader-missing'));
    }
    st.loading = window.EEGoogleMaps.load({ apiKey: mapsConfig.api_key, libraries: ["marker"] })
      .then(function() { return window.google.maps; })
      .catch(function(e) { st.loading = null; throw e; });
    return st.loading;
  }

  function initMap() {
    if (st.map) {
      window.google.maps.event.trigger(st.map, 'resize');
      return;
    }
    st.currentTheme = UI.theme.effective();
    buildMapInstance();
  }

  function buildMapInstance() {
    var mapsConfig = window.KIOSK_BOOT && window.KIOSK_BOOT.maps ? window.KIOSK_BOOT.maps : {};
    var opts = window.EEGoogleMaps.mapOptions(mapsConfig, st.currentTheme);
    var center = st.map ? st.map.getCenter() : { lat: 40.76, lng: -111.89 };
    var zoom = st.map ? st.map.getZoom() : 10;

    var mapOpts = Object.assign({
      center: center,
      zoom: zoom,
      disableDefaultUI: false,
      zoomControl: true,
      mapTypeControl: false,
      streetViewControl: false,
      fullscreenControl: false
    }, opts);

    if (st.map) {
      clearOverlays();
      // Keep old map dom clean
      while (el.map.firstChild) {
        if (el.map.firstChild === el.msg) break; // keep msg
        el.map.removeChild(el.map.firstChild);
      }
      el.map.appendChild(el.msg); // ensure msg is still there
    }

    // Fill the container by ABSOLUTE positioning, not height:100%.
    //
    // `.tk-map` sizes itself from `min-height: 55vh` and carries no `height`
    // property, and its parent `.tk-panel` is an auto-height flex item. A
    // percentage height on this child therefore resolves against `auto` and
    // collapses to ZERO -- the container still paints its own background, so
    // the tab showed a correctly-sized grey box with an invisible map inside
    // it, and markers drew happily into a 0px div. Leaflet never hit this
    // because it attached to `.tk-map` itself rather than to an injected child.
    //
    // `.tk-map` is `position: relative`, so inset-0 fills it whatever its
    // height turns out to be. Longhands rather than the `inset` shorthand:
    // this ships to whatever WebView is on a field phone.
    var mapDiv = document.createElement('div');
    mapDiv.className = 'tk-map-canvas';
    mapDiv.style.position = 'absolute';
    mapDiv.style.top = '0';
    mapDiv.style.right = '0';
    mapDiv.style.bottom = '0';
    mapDiv.style.left = '0';
    el.map.appendChild(mapDiv);

    st.map = new window.google.maps.Map(mapDiv, mapOpts);

    if (st.trail) {
      draw(st.trail);
    }
  }

  function applyTheme(theme) {
    if (st.currentTheme === theme) return;
    st.currentTheme = theme;
    /*
     * Platform constraint: when a mapId is configured, the style is fixed at
     * map construction — calling setOptions({mapId}) on a live map does NOT
     * restyle it. Therefore, we rebuild the map instance on a theme change,
     * preserving center, zoom, and overlays.
     */
    buildMapInstance();
  }

  function clearOverlays() {
    st.layer.forEach(function (overlay) {
      overlay.setMap(null);
    });
    st.layer = [];
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

  function createMarkerIcon(color, isEnd, low) {
    // Return SVG data URI string for a marker so it keeps constant pixel size
    var r = isEnd ? 7 : 4;
    var w = isEnd ? 3 : (low ? 2 : 1);
    var stroke = isEnd ? '#ffffff' : color;
    var fillOpacity = low ? 0 : (isEnd ? 1 : 0.9);
    var size = (r + w) * 2;
    var center = size / 2;

    var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="' + size + '" height="' + size + '">';
    svg += '<circle cx="' + center + '" cy="' + center + '" r="' + r + '" ';
    svg += 'fill="' + color + '" fill-opacity="' + fillOpacity + '" ';
    svg += 'stroke="' + stroke + '" stroke-width="' + w + '" />';
    svg += '</svg>';

    return {
      url: 'data:image/svg+xml;charset=UTF-8,' + encodeURIComponent(svg),
      anchor: new window.google.maps.Point(center, center)
    };
  }

  function draw(data) {
    if (!st.map) return;
    clearOverlays();
    UI.clear(el.legend);

    var points = data.points || [];
    var intervals = data.intervals || [];
    var bounds = new window.google.maps.LatLngBounds();
    var hasBounds = false;
    var byInterval = {};
    var colorOf = {};

    intervals.forEach(function (iv, i) { colorOf[iv.name] = COLORS[i % COLORS.length]; byInterval[iv.name] = []; });
    points.forEach(function (p) {
      if (!validCoords(p.latitude, p.longitude)) return;
      var key = p.job_interval && byInterval[p.job_interval] ? p.job_interval : '_none';
      if (!byInterval[key]) byInterval[key] = [];
      byInterval[key].push(p);
    });

    Object.keys(byInterval).forEach(function (key) {
      var list = byInterval[key];
      if (!list.length) return;
      var color = colorOf[key] || '#6b7280';
      var path = list.map(function (p) { return { lat: p.latitude, lng: p.longitude }; });

      path.forEach(function (ll) { bounds.extend(ll); hasBounds = true; });

      if (path.length > 1) {
        var poly = new window.google.maps.Polyline({
          path: path,
          strokeColor: color,
          strokeOpacity: 0.85,
          strokeWeight: 4,
          map: st.map
        });
        st.layer.push(poly);
      }

      list.forEach(function (p, i) {
        var low = p.log_status === 'Low Accuracy';
        var isEnd = i === 0 || i === list.length - 1;

        var marker = new window.google.maps.Marker({
          position: { lat: p.latitude, lng: p.longitude },
          map: st.map,
          icon: createMarkerIcon(color, isEnd, low),
          title: popupText(p)
        });

        var infoWindow = new window.google.maps.InfoWindow({
          content: '<div class="tk-gpopup">' + escapeText(popupText(p)) + '</div>'
        });
        marker.addListener('click', function() {
          infoWindow.open(st.map, marker);
        });

        st.layer.push(marker);
      });
    });

    intervals.forEach(function (iv) {
      if (!validCoords(iv.site_latitude, iv.site_longitude)) return;
      var r = iv.site_radius_m || data.radius_m || 0;
      var color = colorOf[iv.name] || '#6b7280';
      var pos = { lat: iv.site_latitude, lng: iv.site_longitude };

      // Center marker for site
      var siteMarker = new window.google.maps.Marker({
        position: pos,
        map: st.map,
        icon: createMarkerIcon(color, false, false), // Or custom site icon
        title: escapeText(iv.project_title || iv.name) + ' (site)'
      });
      var infoWindow = new window.google.maps.InfoWindow({
        content: '<div class="tk-gpopup">' + escapeText(iv.project_title || iv.name) + ' (site)</div>'
      });
      siteMarker.addListener('click', function() {
        infoWindow.open(st.map, siteMarker);
      });
      st.layer.push(siteMarker);

      if (r > 0) {
        var circle = new window.google.maps.Circle({
          strokeColor: color,
          strokeOpacity: 0.8,
          strokeWeight: 1,
          fillColor: color,
          fillOpacity: 0.08,
          map: st.map,
          center: pos,
          radius: r
        });
        // We can't do dashArray directly in standard Google Maps Circle without custom SVG overlays,
        // but this gives the visual radius effect well enough.
        st.layer.push(circle);
      }
      bounds.extend(pos);
      hasBounds = true;
    });

    if (hasBounds) {
      try { st.map.fitBounds(bounds, 24); } catch (e) { /* noop */ }
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
