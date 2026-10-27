/*
 * Time Kiosk — the Photos view (window.KioskViews.photos): the job photos the
 * signed-in technician has taken, newest job first.
 *
 * Targets: the Time Kiosk PWA front-end. Loaded by www/kiosk.html after ui.js
 * and myday.js, before app.js — NOT through hooks.py. app.js mounts it into
 * #tk-panel-photos with a context object (see app.js init).
 *
 * Nik's picks (2026-10-27): a tab of its own, the last 7 days by default, grouped
 * by day and then by job, own photos only, view only. The range chips are Today /
 * 7 days / 30 days / All, plus a date picker for one day. Ten jobs load at a time
 * and "Load more" fetches the next ten; a job is never split across pages.
 *
 * Data: get_my_photos and photo_thumbnail in api/kiosk_photos.py, which derive
 * the employee from the session. Read that module's docstring for where a kiosk
 * photo is stored and why these endpoints are NOT under api.time_kiosk (the
 * service worker caches that prefix per URL rather than per person). So the tab
 * needs a connection, like the Map, and says so.
 *
 * A thumbnail is the server's small JPEG, cached by the browser; tapping it opens
 * the full photo in a full-screen sheet through Frappe's permission-checked file
 * URL. The viewer steps through every photo on screen (‹ › buttons, a swipe, or
 * the arrow keys) without touching the browser history: it is one sheet, so Back
 * closes it, exactly like every other sheet in the kiosk (ui.js, "History").
 */
(function () {
  'use strict';

  var UI = window.KioskUI;
  var h = UI.h;
  var fmt = UI.fmt;

  // Deliberately not ctx.API — see the header.
  var PHOTOS_API = 'erpnext_enhancements.api.kiosk_photos.';

  var PERIODS = [
    { id: 'today', label: 'Today', sub: 'Today' },
    { id: '7d', label: '7 days', sub: 'The last 7 days' },
    { id: '30d', label: '30 days', sub: 'The last 30 days' },
    { id: 'all', label: 'All', sub: 'Every photo you have taken' },
  ];
  var DEFAULT_PERIOD = '7d';
  var SWIPE_PX = 50;

  var ctx = null;
  var el = {};
  var st = {
    period: DEFAULT_PERIOD, date: '', groups: [], flat: [], nextStart: 0, hasMore: false,
    seq: 0, visible: false, failed: false,
  };

  // -- Build ---------------------------------------------------------------
  function mount(container, c) {
    ctx = c;
    el.sub = h('p', { class: 'tk-page-sub', text: '' });
    el.seg = h('div', { class: 'tk-seg', role: 'group', 'aria-label': 'Which photos' }, PERIODS.map(function (p) {
      return h('button', {
        type: 'button', 'aria-pressed': 'false', dataset: { period: p.id }, text: p.label,
        on: { click: function () { setPeriod(p.id); } },
      });
    }));
    el.date = h('input', {
      type: 'date', id: 'tk-photos-date', max: fmt.toISODate(new Date()),
      on: { change: function () { setDate(el.date.value); } },
    });
    el.list = h('div', { class: 'tk-photo-list' });
    el.more = h('button', {
      type: 'button', class: 'tk-btn tk-btn-outline', hidden: true, text: 'Load more',
      on: { click: function () { load(false); } },
    });

    container.appendChild(h('div', { class: 'tk-page-head' }, [
      h('div', {}, [h('h1', { text: 'Photos' }), el.sub]),
    ]));
    container.appendChild(h('div', { class: 'tk-card tk-photo-filters' }, [
      el.seg,
      h('div', { class: 'tk-field' }, [h('label', { for: 'tk-photos-date', text: 'Or one day' }), el.date]),
    ]));
    container.appendChild(el.list);
    container.appendChild(el.more);
    renderFilters();
    window.addEventListener('online', function () { if (st.visible && st.failed) load(true); });
  }

  function show() {
    st.visible = true;
    // A photo taken on the Clock tab a minute ago should be here, so every visit reloads.
    load(true);
  }

  function hide() { st.visible = false; }

  // -- Filters -------------------------------------------------------------
  function setPeriod(id) {
    st.period = id;
    st.date = '';
    renderFilters();
    load(true);
  }

  function setDate(value) {
    st.date = value || '';
    renderFilters();
    load(true);
  }

  function renderFilters() {
    Array.prototype.forEach.call(el.seg.children, function (b) {
      b.setAttribute('aria-pressed', !st.date && b.dataset.period === st.period ? 'true' : 'false');
    });
    if (el.date.value !== st.date) el.date.value = st.date;
    el.date.setAttribute('max', fmt.toISODate(new Date()));
    if (st.date) {
      el.sub.textContent = fmt.dateLabel(st.date, { weekday: 'long', month: 'long', day: 'numeric' });
    } else {
      var p = PERIODS.filter(function (x) { return x.id === st.period; })[0];
      el.sub.textContent = p ? p.sub : '';
    }
  }

  // -- Loading -------------------------------------------------------------
  function load(reset) {
    var seq = ++st.seq;
    var args = { start: reset ? 0 : st.nextStart };
    if (st.date) args.date = st.date; else args.period = st.period;

    if (reset) {
      st.groups = [];
      st.hasMore = false;
      UI.clear(el.list);
      el.list.appendChild(UI.skeleton(3, { block: true }));
      el.more.hidden = true;
    } else {
      el.more.disabled = true;
      el.more.textContent = 'Loading…';
    }

    ctx.api(PHOTOS_API + 'get_my_photos', args, { method: 'GET' })
      .then(function (r) {
        if (seq !== st.seq) return; // a newer request owns the screen
        r = r || {};
        st.failed = false;
        st.groups = st.groups.concat(r.groups || []);
        st.nextStart = r.next_start || 0;
        st.hasMore = !!r.has_more;
        render();
      })
      .catch(function (e) {
        if (seq !== st.seq) return;
        var offline = navigator.onLine === false;
        if (reset) {
          st.failed = true;
          UI.clear(el.list);
          el.list.appendChild(h('p', {
            class: offline ? 'tk-empty' : 'tk-error',
            text: offline ? 'Photos need a connection. They will load when you are back online.' : ctx.humanError(e),
          }));
        } else {
          UI.toast(offline ? 'No connection.' : ctx.humanError(e), 'red');
        }
      })
      .then(function () {
        if (seq !== st.seq) return;
        el.more.disabled = false;
        el.more.textContent = 'Load more';
        el.more.hidden = !st.hasMore;
      });
  }

  // -- Rendering -----------------------------------------------------------
  function render() {
    UI.clear(el.list);
    st.flat = [];
    if (!st.groups.length) {
      el.list.appendChild(h('div', { class: 'tk-card' }, [
        h('p', { class: 'tk-empty', text: st.date ? 'No photos on this day.' : 'No photos in this period.' }),
        h('p', { class: 'tk-note', text: 'Photos you take on the Clock tab while you are clocked in show up here.' }),
      ]));
      return;
    }
    var lastDay = null;
    st.groups.forEach(function (g) {
      if (g.day !== lastDay) {
        lastDay = g.day;
        el.list.appendChild(h('h2', { class: 'tk-group-title tk-photo-day', text: dayLabel(g.day) }));
      }
      el.list.appendChild(groupCard(g));
    });
  }

  function dayLabel(day) {
    var now = new Date();
    if (day === fmt.toISODate(now)) return 'Today';
    var y = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
    if (day === fmt.toISODate(y)) return 'Yesterday';
    return fmt.dateLabel(day, { weekday: 'long', month: 'short', day: 'numeric' });
  }

  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

  function groupCard(g) {
    var photos = g.photos || [];
    var title = g.project_title || g.project || 'Job';
    var when = fmt.timeOf(g.start_time) + ' – ' + (g.end_time ? fmt.timeOf(g.end_time) : 'now');
    var grid = null;
    if (photos.length) {
      grid = h('div', { class: 'tk-photo-grid' });
      photos.forEach(function (p, i) {
        var index = st.flat.length;
        st.flat.push({ photo: p, group: g, n: i + 1, of: photos.length });
        grid.appendChild(thumb(p, index, title, i + 1, photos.length));
      });
    }
    return h('section', { class: 'tk-card', 'aria-label': title + ', ' + when }, [
      h('div', { class: 'tk-card-head' }, [
        h('div', { class: 'tk-photo-job' }, [
          h('p', { class: 'tk-photo-job-title', text: title }),
          h('p', { class: 'tk-note', text: when + (g.task_title ? ' · ' + g.task_title : '') }),
        ]),
        h('span', { class: 'tk-chip', text: plural(photos.length, 'photo', 'photos') }),
      ]),
      grid,
      // "Yet", not "never": a capture still uploading from this phone is Pending too.
      g.waiting ? h('p', { class: 'tk-note tk-photo-waiting' }, [
        h('span', { class: 'tk-chip is-amber', text: g.waiting + ' not uploaded yet' }),
        ' ' + plural(g.waiting, 'photo', 'photos') + ' from this job ' + (g.waiting === 1 ? 'hasn’t' : 'haven’t') + ' reached the office yet.',
      ]) : null,
    ]);
  }

  function thumb(p, index, title, n, of) {
    var label = 'Photo ' + n + ' of ' + of + ', ' + title + ', ' + fmt.timeOf(p.captured_on);
    var img = h('img', { src: p.thumb, alt: '', loading: 'lazy', decoding: 'async', width: '120', height: '120' });
    var none = h('span', { class: 'tk-photo-none', hidden: true, text: 'No preview' });
    // No preview (HEIC on a server without the plugin, a file gone from disk):
    // a plain tile that still opens the full photo, rather than a broken image.
    img.addEventListener('error', function () { img.hidden = true; none.hidden = false; });
    return h('button', {
      type: 'button', class: 'tk-photo', 'aria-label': label,
      on: { click: function () { openViewer(index); } },
    }, [img, none]);
  }

  // -- Full-screen viewer ----------------------------------------------------
  function openViewer(index) {
    if (!st.flat.length) return;
    var cur = Math.max(0, Math.min(index, st.flat.length - 1));
    var img = h('img', { class: 'tk-viewer-img', alt: '' });
    var fail = h('p', { class: 'tk-empty', hidden: true, text: 'This photo can’t be shown here. Use “Open full size”.' });
    var stage = h('div', { class: 'tk-viewer-stage is-loading' }, [img, fail]);
    var prev = h('button', { type: 'button', class: 'tk-btn tk-btn-outline tk-btn-icon', 'aria-label': 'Previous photo', text: '‹' });
    var next = h('button', { type: 'button', class: 'tk-btn tk-btn-outline tk-btn-icon', 'aria-label': 'Next photo', text: '›' });
    var count = h('span', { class: 'tk-viewer-count', 'aria-live': 'polite' });
    var caption = h('p', { class: 'tk-viewer-caption', hidden: true });
    var meta = h('p', { class: 'tk-note' });
    var open = h('a', { class: 'tk-btn tk-btn-outline', target: '_blank', rel: 'noopener', text: 'Open full size' });

    img.addEventListener('load', function () { stage.classList.remove('is-loading'); });
    img.addEventListener('error', function () {
      stage.classList.remove('is-loading');
      img.hidden = true;
      fail.hidden = false;
    });

    var handle = UI.sheet.open({
      title: '',
      full: true,
      body: h('div', { class: 'tk-viewer' }, [
        stage,
        h('div', { class: 'tk-viewer-nav' }, [prev, count, next]),
        caption,
        meta,
        open,
      ]),
    });

    function showAt(i) {
      if (i < 0 || i >= st.flat.length) return;
      cur = i;
      var item = st.flat[i];
      var p = item.photo;
      var g = item.group;
      handle.setTitle(g.project_title || g.project || 'Photo');
      stage.classList.add('is-loading');
      fail.hidden = true;
      img.hidden = false;
      img.setAttribute('src', p.url);
      img.setAttribute('alt', 'Photo ' + item.n + ' of ' + item.of + ' on ' + (g.project_title || 'this job'));
      open.setAttribute('href', p.url);
      count.textContent = (i + 1) + ' / ' + st.flat.length;
      caption.hidden = !p.caption;
      caption.textContent = p.caption || '';
      meta.textContent = [
        dayLabel(g.day) + ', ' + fmt.timeOf(p.captured_on),
        g.task_title || '',
        'photo ' + item.n + ' of ' + item.of + ' on this job',
      ].filter(Boolean).join(' · ');
      prev.disabled = i === 0;
      next.disabled = i === st.flat.length - 1;
    }

    prev.addEventListener('click', function () { showAt(cur - 1); });
    next.addEventListener('click', function () { showAt(cur + 1); });
    handle.el.addEventListener('keydown', function (ev) {
      if (ev.key === 'ArrowLeft') { ev.preventDefault(); showAt(cur - 1); }
      else if (ev.key === 'ArrowRight') { ev.preventDefault(); showAt(cur + 1); }
    });
    var touch = null;
    stage.addEventListener('touchstart', function (ev) {
      var t = ev.touches && ev.touches[0];
      touch = t && ev.touches.length === 1 ? { x: t.clientX, y: t.clientY } : null;
    });
    stage.addEventListener('touchend', function (ev) {
      var t = ev.changedTouches && ev.changedTouches[0];
      if (!touch || !t) return;
      var dx = t.clientX - touch.x;
      var dy = t.clientY - touch.y;
      touch = null;
      if (Math.abs(dx) < SWIPE_PX || Math.abs(dy) > Math.abs(dx)) return;
      showAt(dx < 0 ? cur + 1 : cur - 1);
    });

    showAt(cur);
    return handle;
  }

  window.KioskViews = window.KioskViews || {};
  window.KioskViews.photos = { mount: mount, show: show, hide: hide };
})();
