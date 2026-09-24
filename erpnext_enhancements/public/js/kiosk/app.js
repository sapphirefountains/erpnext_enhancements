/*
 * Time Kiosk — standalone PWA application (state machine + the Clock view).
 *
 * Targets: the Time Kiosk PWA front-end (mounts into #kiosk-root).
 * Loaded via: the web page www/kiosk.html — NOT through hooks.py — after ui.js,
 * geo.js and the view modules (myday.js, map.js, settings.js). The page injects
 * a boot payload on `window.KIOSK_BOOT` (employee, settings, current status,
 * photo_gate, csrf_token), the CSRF token on `window.KIOSK_CSRF` and the deploy
 * token on `window.KIOSK_BUILD`; a service worker (/kiosk-sw.js) handles durable
 * queueing/upload of location points.
 *
 * Self-contained: does NOT depend on the Frappe desk bundle. Talks to whitelisted
 * `erpnext_enhancements.api.time_kiosk.*` endpoints via fetch (+ injected CSRF
 * token) and drives KioskGeo (geo.js) for location tracking, which runs only while
 * clocked in AND active (status "Open").
 *
 * Layout: a state-coloured hero (idle / working / break / day complete), one
 * panel per bottom tab (Clock · My Day · Map · Settings) and a tab bar. The
 * Clock panel lives here; the other three are modules on window.KioskViews that
 * receive a small context object (api, state, pickers) from init().
 *
 * Every interruption is a KioskUI bottom sheet (ui.js) — the project picker,
 * the break presets, the off-site warning, the shift summary, the maintenance
 * warning, the photo gate and its skip reason, the attachments nudge. There is
 * no window.confirm / prompt / alert in this directory.
 *
 * Flow: init() builds the shell, wires events, configures KioskGeo + warms up the
 * location permission, registers the service worker (versioned per deploy via
 * window.KIOSK_BUILD, with foreground/hourly update checks), seeds the UI from
 * the boot status then confirms via get_current_status. renderState() is the
 * central state machine: idle form / working / break / day complete, and it
 * starts/stops KioskGeo accordingly.
 *
 * Clock events take an ANCHOR fix first (KioskGeo.anchorFix — high accuracy, a
 * few seconds, never fails) and send it to log_time as lat/lng/accuracy. Before
 * Start and Switch, when the chosen project has site coordinates, the geofence
 * radius is > 0, the anchor is outside it and Time Kiosk Settings.offsite_warn
 * is on, the off-site sheet asks first and the request carries
 * offsite_acknowledged: 1. The server flags off-site starts regardless.
 *
 * Clock Out: get_shift_summary → review sheet → Confirm → the existing gates in
 * the existing order (maintenance warning → photo gate → attachments nudge) →
 * log_time Stop → the "Day complete" screen.
 *
 * Maintenance forms: while clocked into a project with an Active Maintenance
 * Contract (or Active form template), the card shows a link to the visit form
 * (get_maintenance_context — open draft or prefilled new record, opened in a
 * new tab so the clock keeps running). Clock-out and cross-project switches
 * warn when no form was submitted during the interval.
 */
(function () {
  'use strict';

  var UI = window.KioskUI;
  var h = UI.h;
  var fmt = UI.fmt;
  var toast = UI.toast;

  var BOOT = window.KIOSK_BOOT || {};
  var CSRF = window.KIOSK_CSRF || BOOT.csrf_token || '';
  var SETTINGS = BOOT.settings || {};
  // Job photo capture gate config (WP-2). A UX hint ONLY — the server re-reads
  // Time Kiosk Settings inside log_time on every call and is what actually
  // decides, so a device serving a stale cached bundle cannot talk its way past
  // the requirement. See workforce/photo_gate.py.
  var PHOTO_GATE = BOOT.photo_gate || {};
  // localStorage key for photos captured while offline. The bytes stay in this
  // queue until they upload; the SERVER-side row is registered separately and
  // immediately, so the gate is satisfied the moment the shutter fires.
  var PHOTO_QUEUE_KEY = 'tk_photo_queue_v1';
  // Per-deploy cache-bust token (kiosk.py::get_deploy_version, injected by
  // kiosk.html). Versions the service-worker registration so every deploy
  // rotates the SW cache automatically.
  var BUILD = window.KIOSK_BUILD || '';
  var API = 'erpnext_enhancements.api.time_kiosk.';

  var app = {
    status: null,            // 'Open' | 'Paused' | 'Idle'
    currentInterval: null,
    attachments: [],
    photoCount: 0,           // photos captured for the ACTIVE interval, incl. ones still queued offline
    loading: false,
    maintenance: null,       // { project, ctx } — get_maintenance_context for the active job
    options: { projects: [], activity_types: [], recent_projects: [], radius_m: 0 },
    tab: 'clock',
    draft: { project: null, task: null, activity: '', note: '' },
    breakPlan: null,         // { minutes, startedAt } — the preset chosen at Pause
    breakAlerted: false,
    dayComplete: null,       // shift summary shown after a successful Stop
    swReg: null,
    installPrompt: null,     // the deferred beforeinstallprompt event
    badges: {},
  };

  var el = {};

  // -- API -----------------------------------------------------------------
  function api(method, args, opts) {
    opts = opts || {};
    var isGet = opts.method === 'GET';
    var url = '/api/method/' + method;
    var headers = { 'Accept': 'application/json', 'X-Frappe-CSRF-Token': CSRF };
    var init = { method: isGet ? 'GET' : 'POST', headers: headers, credentials: 'same-origin' };

    if (isGet) {
      var qs = new URLSearchParams(args || {}).toString();
      if (qs) url += '?' + qs;
    } else {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(args || {});
    }

    return fetch(url, init).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (data) {
        if (!res.ok) {
          var m = (data && (data._server_messages || data.exception)) || ('HTTP ' + res.status);
          throw new Error(m);
        }
        return data ? data.message : null;
      });
    });
  }

  function humanError(e) {
    var msg = (e && e.message) || 'Something went wrong.';
    // Try to surface Frappe's _server_messages if present.
    try {
      var parsed = JSON.parse(msg);
      if (Array.isArray(parsed) && parsed.length) {
        var first = JSON.parse(parsed[0]);
        if (first && first.message) return String(first.message).replace(/<[^>]*>/g, '');
      }
    } catch (ignore) { /* not JSON */ }
    return String(msg).replace(/<[^>]*>/g, '').slice(0, 200);
  }

  function cint(v) { var n = parseInt(v, 10); return isNaN(n) ? 0 : n; }
  function $(id) { return document.getElementById(id); }

  // -- Shell ---------------------------------------------------------------
  var TAB_ICONS = {
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    myday: '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/>',
    map: '<path d="M9 18l-6 3V6l6-3 6 3 6-3v15l-6 3-6-3z"/><path d="M9 3v15M15 6v15"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  };
  var TABS = [
    { id: 'clock', label: 'Clock' },
    { id: 'myday', label: 'My Day' },
    { id: 'map', label: 'Map' },
    { id: 'settings', label: 'Settings' },
  ];

  function tabButton(t) {
    // Static markup from this file, never data. Parsed through a div so the SVG
    // lands in its namespace on engines where SVGElement.innerHTML is missing.
    var wrap = document.createElement('div');
    wrap.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true">' + TAB_ICONS[t.id] + '</svg>';
    var svg = wrap.firstChild;
    var b = h('button', {
      type: 'button', class: 'tk-tab', id: 'tk-tab-' + t.id, role: 'tab',
      on: { click: function () { setTab(t.id); } },
    }, [svg, h('span', { text: t.label }), h('span', { class: 'tk-tab-badge', id: 'tk-badge-' + t.id, hidden: true })]);
    return b;
  }

  function buildShell(root) {
    UI.clear(root);
    var hero = h('header', { class: 'tk-hero is-idle', id: 'tk-hero' }, [
      h('div', { class: 'tk-hero-clock', id: 'tk-clock', text: '--:--' }),
      h('div', { class: 'tk-hero-elapsed', id: 'tk-elapsed', hidden: true, text: '0:00:00' }),
      h('div', { class: 'tk-countdown', id: 'tk-countdown', hidden: true, text: '00:00' }),
      h('p', { class: 'tk-hero-title', id: 'tk-status', text: 'Ready to work' }),
      h('p', { class: 'tk-hero-sub', id: 'tk-hero-sub', text: BOOT.employee_name || '' }),
      h('p', { class: 'tk-hero-project', id: 'tk-hero-project', hidden: true }),
      h('div', { class: 'tk-hero-chips' }, [
        h('span', { class: 'tk-hero-chip', id: 'tk-chip-activity', hidden: true }),
        h('span', { class: 'tk-hero-chip', id: 'tk-chip-photos', hidden: true }),
        h('span', { class: 'tk-hero-chip tk-track', id: 'tk-track' }, [
          h('span', { class: 'tk-track-dot' }),
          h('span', { id: 'tk-track-text', text: 'Tracking off' }),
        ]),
      ]),
    ]);

    var idle = h('div', { class: 'tk-stack', id: 'tk-idle' }, [
      h('div', { class: 'tk-card', id: 'tk-geo-suggest', hidden: true }, [
        h('p', { class: 'tk-card-title', text: 'Nearby visit' }),
        h('p', { id: 'tk-geo-suggest-text', style: { margin: '0 0 10px' } }),
        h('button', { type: 'button', class: 'tk-btn tk-btn-primary', id: 'tk-geo-suggest-btn', text: 'Use this project' }),
      ]),
      h('div', { class: 'tk-card' }, [
        h('p', { class: 'tk-card-title', text: 'Start a job' }),
        h('div', { class: 'tk-stack' }, [
          h('div', { class: 'tk-field' }, [h('label', { text: 'Project', for: 'tk-pick-project' }), pickButton('tk-pick-project', 'Choose a project')]),
          h('div', { class: 'tk-field' }, [h('label', { text: 'Task (optional)', for: 'tk-pick-task' }), pickButton('tk-pick-task', 'Choose a task')]),
          h('div', { class: 'tk-field' }, [h('span', { class: 'tk-label', text: 'Activity' }), h('div', { class: 'tk-chips', id: 'tk-activity', role: 'group', 'aria-label': 'Activity type' })]),
          h('div', { class: 'tk-field' }, [h('label', { text: 'Note (optional)', for: 'tk-note' }), h('textarea', { id: 'tk-note', rows: '2', placeholder: 'What are you working on?' })]),
          h('button', { type: 'button', class: 'tk-btn tk-btn-go tk-btn-lg', id: 'tk-clock-in', text: 'Clock In' }),
          h('button', { type: 'button', class: 'tk-btn tk-btn-link', id: 'tk-forgot-clock-in', text: 'Add missed time' }),
        ]),
      ]),
      h('div', { class: 'tk-card', id: 'tk-visits', hidden: true }, [
        h('p', { class: 'tk-card-title', text: "Today's visits" }),
        h('div', { class: 'tk-list', id: 'tk-visits-list' }),
      ]),
    ]);

    var active = h('div', { class: 'tk-stack', id: 'tk-active', hidden: true }, [
      h('div', { class: 'tk-card' }, [
        h('div', { class: 'tk-btn-row' }, [
          h('button', { type: 'button', class: 'tk-btn tk-btn-break', id: 'tk-pause', text: 'Take a break' }),
          h('button', { type: 'button', class: 'tk-btn tk-btn-go', id: 'tk-resume', hidden: true, text: 'Resume work' }),
          h('button', { type: 'button', class: 'tk-btn tk-btn-outline', id: 'tk-switch', text: 'Switch job' }),
        ]),
        h('button', { type: 'button', class: 'tk-btn tk-btn-stop tk-btn-lg', id: 'tk-clock-out', style: { marginTop: '10px' }, text: 'Clock Out' }),
      ]),
      h('div', { class: 'tk-card', id: 'tk-maintenance', hidden: true }, [
        h('p', { class: 'tk-card-title', text: 'Maintenance visit' }),
        h('a', { class: 'tk-btn tk-btn-outline', id: 'tk-maintenance-link', target: '_blank', rel: 'noopener', text: 'Maintenance form' }),
      ]),
      h('div', { class: 'tk-card', id: 'tk-note-card' }, [
        h('p', { class: 'tk-card-title', text: 'Note' }),
        h('p', { id: 'tk-readonly-note', style: { margin: '0' } }),
      ]),
      h('div', { class: 'tk-card', id: 'tk-attachments' }, [
        h('div', { class: 'tk-card-head' }, [
          h('p', { class: 'tk-card-title', text: 'Photos & files' }),
          h('span', { class: 'tk-chip', id: 'tk-photo-chip', hidden: true }),
        ]),
        h('div', { class: 'tk-attachment-list', id: 'tk-attachment-list' }),
        h('div', { class: 'tk-btn-row', style: { marginTop: '10px' } }, [
          h('button', { type: 'button', class: 'tk-btn tk-btn-primary', id: 'tk-take-pic', text: 'Take photo' }),
          h('button', { type: 'button', class: 'tk-btn tk-btn-outline', id: 'tk-add-attach', text: 'Add files' }),
        ]),
        h('input', { type: 'file', id: 'tk-file-input', multiple: true, hidden: true }),
        h('input', { type: 'file', id: 'tk-camera-input', accept: 'image/*', capture: 'environment', hidden: true }),
      ]),
    ]);

    var done = h('div', { class: 'tk-stack', id: 'tk-done', hidden: true }, [
      h('div', { class: 'tk-card' }, [
        h('p', { class: 'tk-card-title', text: 'Day complete' }),
        h('div', { class: 'tk-tiles', id: 'tk-done-tiles' }),
        h('p', { class: 'tk-note', id: 'tk-done-sites', style: { marginTop: '10px' } }),
        h('button', { type: 'button', class: 'tk-btn tk-btn-go tk-btn-lg', id: 'tk-start-another', style: { marginTop: '12px' }, text: 'Start another job' }),
      ]),
    ]);

    var clockPanel = h('section', { class: 'tk-panel is-clock', id: 'tk-panel-clock', role: 'tabpanel' }, [idle, active, done]);
    var view = h('div', { class: 'tk-view', id: 'tk-view' }, [
      hero,
      clockPanel,
      h('section', { class: 'tk-panel', id: 'tk-panel-myday', role: 'tabpanel', hidden: true }),
      h('section', { class: 'tk-panel is-map', id: 'tk-panel-map', role: 'tabpanel', hidden: true }),
      h('section', { class: 'tk-panel', id: 'tk-panel-settings', role: 'tabpanel', hidden: true }),
    ]);
    var tabbar = h('nav', { class: 'tk-tabbar', 'aria-label': 'Sections' }, [
      h('div', { class: 'tk-tabbar-inner', role: 'tablist' }, TABS.map(tabButton)),
    ]);
    root.appendChild(view);
    root.appendChild(tabbar);
    root.appendChild(h('div', { class: 'tk-toasts', id: 'tk-toasts', 'aria-live': 'polite' }));
  }

  function pickButton(id, placeholder) {
    return h('button', { type: 'button', class: 'tk-pick', id: id, 'aria-haspopup': 'dialog' }, [
      h('span', { class: 'tk-pick-text is-placeholder', text: placeholder }),
      h('span', { class: 'tk-row-chev', 'aria-hidden': 'true', text: '›' }),
    ]);
  }

  function setPick(btn, main, sub, placeholder) {
    var t = btn.querySelector('.tk-pick-text');
    UI.clear(t);
    if (main) {
      t.classList.remove('is-placeholder');
      t.appendChild(document.createTextNode(main));
      if (sub) t.appendChild(h('span', { class: 'tk-pick-sub', text: sub }));
    } else {
      t.classList.add('is-placeholder');
      t.textContent = placeholder;
    }
  }

  function cacheEls() {
    el.hero = $('tk-hero');
    el.clock = $('tk-clock');
    el.elapsed = $('tk-elapsed');
    el.countdown = $('tk-countdown');
    el.status = $('tk-status');
    el.heroSub = $('tk-hero-sub');
    el.heroProject = $('tk-hero-project');
    el.chipActivity = $('tk-chip-activity');
    el.chipPhotos = $('tk-chip-photos');
    el.track = $('tk-track');
    el.trackText = $('tk-track-text');
    el.idle = $('tk-idle');
    el.active = $('tk-active');
    el.done = $('tk-done');
    el.doneTiles = $('tk-done-tiles');
    el.doneSites = $('tk-done-sites');
    el.geoSuggest = $('tk-geo-suggest');
    el.geoSuggestText = $('tk-geo-suggest-text');
    el.geoSuggestBtn = $('tk-geo-suggest-btn');
    el.pickProject = $('tk-pick-project');
    el.pickTask = $('tk-pick-task');
    el.activity = $('tk-activity');
    el.note = $('tk-note');
    el.clockIn = $('tk-clock-in');
    el.forgotClockIn = $('tk-forgot-clock-in');
    el.visits = $('tk-visits');
    el.visitsList = $('tk-visits-list');
    el.pause = $('tk-pause');
    el.resume = $('tk-resume');
    el.switchBtn = $('tk-switch');
    el.clockOut = $('tk-clock-out');
    el.maintenance = $('tk-maintenance');
    el.maintenanceLink = $('tk-maintenance-link');
    el.noteCard = $('tk-note-card');
    el.readonlyNote = $('tk-readonly-note');
    el.attachmentList = $('tk-attachment-list');
    el.photoChip = $('tk-photo-chip');
    el.startAnother = $('tk-start-another');
  }

  // -- Tabs ----------------------------------------------------------------
  function views() { return window.KioskViews || {}; }

  function setTab(name) {
    var prev = app.tab;
    app.tab = name;
    TABS.forEach(function (t) {
      var panel = $('tk-panel-' + t.id);
      var btn = $('tk-tab-' + t.id);
      var on = t.id === name;
      if (panel) panel.hidden = !on;
      if (btn) {
        if (on) btn.setAttribute('aria-current', 'page'); else btn.removeAttribute('aria-current');
        btn.setAttribute('aria-selected', on ? 'true' : 'false');
      }
    });
    el.hero.hidden = name !== 'clock';
    var v = views();
    if (prev !== name && v[prev] && v[prev].hide) { try { v[prev].hide(); } catch (e) { /* noop */ } }
    if (v[name] && v[name].show) { try { v[name].show(); } catch (e) { /* view's problem */ } }
    try { window.scrollTo(0, 0); } catch (e) { /* noop */ }
  }

  function setBadge(tab, n) {
    var b = $('tk-badge-' + tab);
    if (!b) return;
    app.badges[tab] = n;
    b.hidden = !n;
    b.textContent = n > 99 ? '99+' : String(n || '');
  }

  // -- Options + pickers ---------------------------------------------------
  function projectByName(name) {
    for (var i = 0; i < app.options.projects.length; i++) {
      if (app.options.projects[i].value === name) return app.options.projects[i];
    }
    return null;
  }

  function loadOptions() {
    return api(API + 'get_kiosk_options', {}, { method: 'GET' })
      .then(function (opts) {
        opts = opts || {};
        app.options.projects = opts.projects || [];
        app.options.activity_types = opts.activity_types || [];
        app.options.recent_projects = opts.recent_projects || [];
        app.options.radius_m = cint(opts.radius_m);
        renderActivityChips();
        if (app.draft.project && !projectByName(app.draft.project.value)) setDraftProject(null);
        requestDeviceFix();
      })
      .catch(function () { toast('Could not load projects.', 'red'); });
  }

  function renderActivityChips() {
    UI.clear(el.activity);
    app.options.activity_types.forEach(function (a) {
      var b = h('button', {
        type: 'button', class: 'tk-choice', text: a.label,
        'aria-pressed': app.draft.activity === a.value ? 'true' : 'false',
        on: { click: function () {
          app.draft.activity = app.draft.activity === a.value ? '' : a.value;
          renderActivityChips();
        } },
      });
      el.activity.appendChild(b);
    });
    if (!app.options.activity_types.length) el.activity.appendChild(h('p', { class: 'tk-note', text: 'No activity types configured.' }));
  }

  function setDraftProject(p) {
    app.draft.project = p || null;
    app.draft.task = null;
    setPick(el.pickProject, p ? p.label : '', p && p.value !== p.label ? p.value : '', 'Choose a project');
    setPick(el.pickTask, '', '', 'Choose a task');
    el.pickTask.disabled = !p;
  }

  // One shared device fix for nearest-first sorting and the geofenced
  // suggestion; refreshed at most every 2 minutes. KioskGeo remembers every fix
  // it sees, so this only asks the OS when nothing recent is known.
  function requestDeviceFix(cb) {
    var fix = window.KioskGeo.lastFix();
    if (fix && Date.now() - fix.t < 120000) { if (cb) cb(fix); return; }
    if (!('geolocation' in navigator)) { if (cb) cb(fix); return; }
    navigator.geolocation.getCurrentPosition(function () {
      if (cb) cb(window.KioskGeo.lastFix());
    }, function () { if (cb) cb(fix); /* stale fix beats none */ },
    { maximumAge: 120000, timeout: 8000 });
  }

  function withDistance(p, fix) {
    var o = { value: p.value, label: p.label, lat: p.lat, lng: p.lng, distance_m: null };
    if (fix && p.lat != null && p.lng != null) {
      o.distance_m = window.KioskGeo.distanceM({ lat: fix.lat, lng: fix.lng }, { lat: p.lat, lng: p.lng });
    }
    return o;
  }

  /**
   * Full-screen project picker: Recent / Nearest / All, with a search box that
   * matches title or PRJ-#. opts.selected (docname), opts.title, opts.onPick(p).
   */
  function openProjectPicker(opts) {
    opts = opts || {};
    var search = h('input', { type: 'search', class: 'tk-input', placeholder: 'Search project name or PRJ-#', 'aria-label': 'Search projects', autocomplete: 'off' });
    var list = h('div', { class: 'tk-stack' });
    var body = h('div', { class: 'tk-stack' }, [h('div', { class: 'tk-search' }, [search]), list]);
    var handle = null;

    function row(p) {
      return h('button', {
        type: 'button', class: 'tk-row' + (p.value === opts.selected ? ' is-selected' : ''),
        on: { click: function () { if (handle) handle.close('action'); if (opts.onPick) opts.onPick(projectByName(p.value) || p); } },
      }, [
        h('div', { class: 'tk-row-body' }, [
          h('div', { class: 'tk-row-main', text: p.label }),
          p.value !== p.label ? h('div', { class: 'tk-row-sub', text: p.value }) : null,
        ]),
        p.distance_m != null ? h('span', { class: 'tk-dist', text: fmt.distance(p.distance_m) }) : null,
      ]);
    }

    function group(title, items) {
      if (!items.length) return null;
      return h('div', {}, [h('p', { class: 'tk-group-title', text: title }), h('div', { class: 'tk-list' }, items.map(row))]);
    }

    function render() {
      UI.clear(list);
      var fix = window.KioskGeo.lastFix();
      var all = app.options.projects.map(function (p) { return withDistance(p, fix); });
      var q = (search.value || '').trim().toLowerCase();
      if (!all.length) { list.appendChild(h('p', { class: 'tk-empty', text: 'No active projects.' })); return; }
      if (q) {
        var hits = all.filter(function (p) {
          return String(p.label).toLowerCase().indexOf(q) !== -1 || String(p.value).toLowerCase().indexOf(q) !== -1;
        });
        hits.sort(function (a, b) { return String(a.label).localeCompare(String(b.label)); });
        list.appendChild(hits.length ? h('div', { class: 'tk-list' }, hits.map(row)) : h('p', { class: 'tk-empty', text: 'No matching projects.' }));
        return;
      }
      var byName = {};
      all.forEach(function (p) { byName[p.value] = p; });
      var recent = app.options.recent_projects.map(function (n) { return byName[n]; }).filter(Boolean).slice(0, 5);
      var nearest = all.filter(function (p) { return p.distance_m != null; });
      nearest.sort(function (a, b) { return a.distance_m - b.distance_m; });
      nearest = nearest.slice(0, 5);
      var everything = all.slice().sort(function (a, b) { return String(a.label).localeCompare(String(b.label)); });
      var gRecent = group('Recent', recent);
      var gNear = group('Nearest', nearest);
      if (gRecent) list.appendChild(gRecent);
      if (gNear) list.appendChild(gNear);
      else if (!fix) list.appendChild(h('p', { class: 'tk-note', text: 'Nearest sites appear once your location is known.' }));
      list.appendChild(group('All projects', everything));
    }

    search.addEventListener('input', render);
    handle = UI.sheet.open({ title: opts.title || 'Choose a project', full: true, body: body, initialFocus: 'input' });
    render();
    requestDeviceFix(function () { if (UI.sheet.depth()) render(); });
    return handle;
  }

  function openTaskPicker(project, onPick) {
    var search = h('input', { type: 'search', class: 'tk-input', placeholder: 'Search tasks', 'aria-label': 'Search tasks', autocomplete: 'off' });
    var list = h('div', {}, [UI.skeleton(4)]);
    var body = h('div', { class: 'tk-stack' }, [h('div', { class: 'tk-search' }, [search]), list]);
    var tasks = null;
    var handle = UI.sheet.open({ title: 'Choose a task', full: true, body: body, initialFocus: 'input' });

    function row(t) {
      return h('button', {
        type: 'button', class: 'tk-row',
        on: { click: function () { handle.close('action'); onPick(t); } },
      }, [h('div', { class: 'tk-row-body' }, [h('div', { class: 'tk-row-main', text: t ? t.label : 'No task' }), t && t.value !== t.label ? h('div', { class: 'tk-row-sub', text: t.value }) : null])]);
    }
    function render() {
      UI.clear(list);
      if (!tasks) { list.appendChild(UI.skeleton(4)); return; }
      var q = (search.value || '').trim().toLowerCase();
      var hits = tasks.filter(function (t) {
        return !q || String(t.label).toLowerCase().indexOf(q) !== -1 || String(t.value).toLowerCase().indexOf(q) !== -1;
      });
      var rows = [row(null)].concat(hits.map(row));
      list.appendChild(h('div', { class: 'tk-list' }, rows));
      if (!hits.length) list.appendChild(h('p', { class: 'tk-empty', text: tasks.length ? 'No matching tasks.' : 'This project has no open tasks.' }));
    }
    search.addEventListener('input', render);
    api(API + 'get_tasks_for_project', { project: project }, { method: 'GET' })
      .then(function (t) { tasks = t || []; render(); })
      .catch(function () { tasks = []; render(); });
    return handle;
  }

  // -- Status / rendering --------------------------------------------------
  function setLoading(on, label) {
    app.loading = on;
    ['tk-clock-in', 'tk-pause', 'tk-resume', 'tk-switch', 'tk-clock-out'].forEach(function (id) {
      var b = $(id);
      if (b) b.disabled = on || (id === 'tk-clock-in' && !BOOT.employee);
    });
    if (el.clockIn) el.clockIn.textContent = on && label ? label : 'Clock In';
    if (el.clockOut) el.clockOut.textContent = on && label ? label : 'Clock Out';
  }

  function applyStatus(message) {
    if (message && message.name) {
      app.status = message.status;
      app.currentInterval = message;
      app.attachments = message.attachments || [];
      // Trust the server's count when it sends one, but never let it DROP a
      // photo this device knows it took: an offline capture is real even though
      // the server has not heard about it yet, and lowering the count here would
      // re-block a technician who has already done the right thing.
      if (typeof message.photo_count === 'number') {
        app.photoCount = Math.max(message.photo_count, app.photoCount || 0);
      }
      app.dayComplete = null;
    } else {
      app.status = 'Idle';
      app.currentInterval = null;
      app.attachments = [];
      app.photoCount = 0;
      app.breakPlan = null;
    }
    renderState();
  }

  var statusFetchedAt = 0;
  // When the server last answered a status fetch (not when one was started: statusFetchedAt
  // is stamped before the request, so it cannot say whether the kiosk is actually in touch).
  // Reported by "Report a problem" (WI-079 slice 2).
  var lastSyncOkAt = null;
  function fetchStatus() {
    statusFetchedAt = Date.now();
    setLoading(true);
    return api(API + 'get_current_status', {}, { method: 'GET' })
      .then(function (res) { lastSyncOkAt = new Date().toISOString(); return applyStatus(res); })
      .catch(function (e) { toast(humanError(e), 'red'); })
      .then(function () { setLoading(false); });
  }

  function renderState() {
    var ci = app.currentInterval || {};
    var active = (app.status === 'Open' || app.status === 'Paused');
    el.hero.className = 'tk-hero ' + (active ? (app.status === 'Open' ? 'is-working' : 'is-break') : (app.dayComplete ? 'is-done' : 'is-idle'));
    el.chipActivity.hidden = !(active && ci.time_category);
    el.chipActivity.textContent = ci.time_category || '';
    el.heroSub.textContent = '';

    if (active) {
      el.status.textContent = app.status === 'Open' ? 'Working' : 'On break';
      el.idle.hidden = true; el.done.hidden = true; el.active.hidden = false;
      el.elapsed.hidden = app.status !== 'Open';
      el.countdown.hidden = app.status !== 'Paused';
      el.heroProject.hidden = false;
      var title = ci.project_title || ci.project || '';
      el.heroProject.textContent = title;
      el.heroSub.textContent = ci.task ? (ci.task_title || ci.task) : ('Since ' + fmt.timeOf(ci.start_time));
      el.pause.hidden = app.status !== 'Open';
      el.resume.hidden = app.status !== 'Paused';
      el.readonlyNote.textContent = ci.description || '';
      el.noteCard.hidden = !ci.description;
      renderAttachments();
      renderPhotoChip();
      loadMaintenanceContext();
      if (app.status === 'Paused') app.breakAlerted = false;
      el.geoSuggest.hidden = true;

      // Tracking: only while genuinely active (Open), never on break (Paused).
      if (app.status === 'Open' && ci.name) window.KioskGeo.start(ci.name);
      else window.KioskGeo.stop();
    } else {
      el.elapsed.hidden = true; el.countdown.hidden = true; el.heroProject.hidden = true;
      el.chipPhotos.hidden = true;
      el.active.hidden = true;
      el.maintenance.hidden = true;
      app.maintenance = null;
      UI.clear(el.attachmentList);
      window.KioskGeo.stop();
      if (app.dayComplete) {
        el.status.textContent = 'Day complete';
        el.heroSub.textContent = 'Nice work.';
        el.idle.hidden = true; el.done.hidden = false;
        renderDayComplete();
      } else {
        el.status.textContent = BOOT.employee ? 'Ready to work' : 'No employee linked to your user';
        el.heroSub.textContent = BOOT.employee_name || '';
        el.idle.hidden = false; el.done.hidden = true;
        if (!BOOT.employee) el.clockIn.disabled = true;
        loadVisitsToday();
        maybeSuggestNearby();
      }
    }
    tick();
  }

  function renderPhotoChip() {
    var n = app.photoCount || 0;
    el.chipPhotos.hidden = false;
    el.chipPhotos.textContent = n === 1 ? '1 photo' : n + ' photos';
    el.photoChip.hidden = !PHOTO_GATE.require_job_photos;
    var needed = PHOTO_GATE.min_photos_per_interval || 1;
    el.photoChip.className = 'tk-chip ' + (n >= needed ? 'is-green' : 'is-amber');
    el.photoChip.textContent = n >= needed ? 'Photo requirement met' : ('Needs ' + (needed - n) + ' more photo' + (needed - n === 1 ? '' : 's'));
  }

  function renderDayComplete() {
    var s = app.dayComplete || {};
    UI.clear(el.doneTiles);
    var tiles = [
      ['Today', fmt.hm(s.today_seconds || 0)],
      ['Last job', fmt.hm(s.interval_seconds || 0)],
      ['Photos', String(s.photo_count || 0)],
      ['Tracking', s.coverage_pct != null ? Math.round(s.coverage_pct) + '%' : '—'],
    ];
    tiles.forEach(function (t) {
      el.doneTiles.appendChild(h('div', { class: 'tk-tile' }, [h('div', { class: 'tk-tile-label', text: t[0] }), h('div', { class: 'tk-tile-value', text: t[1] })]));
    });
    var sites = s.sites || [];
    el.doneSites.textContent = sites.length ? 'Sites: ' + sites.join(', ') : '';
  }

  // -- Today's visits + geofenced suggestion (idle screen) -------------------
  var visitsLoadedAt = 0;
  function loadVisitsToday() {
    if (Date.now() - visitsLoadedAt < 60000) return; // renderState re-fires often
    visitsLoadedAt = Date.now();
    api(API + 'get_my_visits_today', {}, { method: 'GET' })
      .then(function (visits) {
        UI.clear(el.visitsList);
        if (!visits || !visits.length) { el.visits.hidden = true; return; }
        visits.forEach(function (v) {
          var label = v.project_title || v.project || '';
          var sub = v.visit_label || v.serial_no || '';
          el.visitsList.appendChild(h('a', { class: 'tk-row', href: v.route, target: '_blank', rel: 'noopener' }, [
            h('span', { class: 'tk-row-icon is-accent', 'aria-hidden': 'true', text: '✓' }),
            h('div', { class: 'tk-row-body' }, [h('div', { class: 'tk-row-main', text: label }), sub ? h('div', { class: 'tk-row-sub', text: sub }) : null]),
            h('span', { class: 'tk-row-chev', 'aria-hidden': 'true', text: '›' }),
          ]));
        });
        el.visits.hidden = false;
      })
      .catch(function () { el.visits.hidden = true; });
  }

  var geoSuggestChecked = false;
  function maybeSuggestNearby() {
    if (geoSuggestChecked) return;
    geoSuggestChecked = true; // one position fix per page load is plenty
    requestDeviceFix(function (fix) {
      if (!fix) return; // permission denied / unavailable — no suggestion
      if (app.status !== 'Idle') return;
      api(API + 'get_nearby_visit', { lat: fix.lat, lng: fix.lng }, { method: 'GET' })
        .then(function (site) {
          if (!site || app.status !== 'Idle') return;
          el.geoSuggestText.textContent =
            'You’re near ' + (site.project_title || site.project) +
            ' (~' + site.distance_m + ' m) and a visit is due.';
          el.geoSuggestBtn.onclick = function () {
            setDraftProject(projectByName(site.project) || { value: site.project, label: site.project_title || site.project });
            el.geoSuggest.hidden = true;
            toast('Project selected — ready to clock in.', 'green');
          };
          el.geoSuggest.hidden = false;
        })
        .catch(function () { /* best-effort */ });
    });
  }

  // -- Maintenance forms -----------------------------------------------------
  function loadMaintenanceContext() {
    var ci = app.currentInterval;
    if (!ci || !ci.project) { app.maintenance = null; renderMaintenance(); return; }
    if (app.maintenance && app.maintenance.project === ci.project) { renderMaintenance(); return; }
    app.maintenance = { project: ci.project, ctx: null };
    api(API + 'get_maintenance_context', { project: ci.project }, { method: 'GET' })
      .then(function (ctx) {
        // The interval may have ended or switched while the request ran.
        if (!app.maintenance || app.maintenance.project !== ci.project) return;
        app.maintenance.ctx = ctx || { required: false };
        renderMaintenance();
        if (ctx && ctx.required && !app.maintenance.noticeShown) {
          app.maintenance.noticeShown = true;
          toast('This project requires a maintenance visit form.', 'orange', 5000);
        }
      })
      .catch(function () { /* offline — link/warning are best-effort */ });
  }

  function renderMaintenance() {
    var active = (app.status === 'Open' || app.status === 'Paused');
    var ctx = app.maintenance && app.maintenance.ctx;
    if (active && ctx && ctx.required && ctx.form_route) {
      el.maintenanceLink.href = ctx.form_route;
      el.maintenanceLink.textContent = ctx.draft ? 'Open the draft form' : 'New maintenance form';
      el.maintenance.hidden = false;
    } else {
      el.maintenance.hidden = true;
    }
  }

  // Before leaving a maintenance project (clock-out or switch), re-check the
  // server: was a form submitted by this user since clock-in? If not, a sheet
  // offers the form or lets them continue. Offline or non-maintenance projects
  // proceed silently. Resolves true to proceed.
  function warnIfMaintenancePending(verb) {
    var ci = app.currentInterval;
    if (!ci || !ci.project) return Promise.resolve(true);
    return api(API + 'get_maintenance_context', { project: ci.project, since: ci.start_time }, { method: 'GET' })
      .then(function (ctx) {
        if (!(ctx && ctx.required && !ctx.submitted_since)) return true;
        if (app.maintenance && app.maintenance.project === ci.project) {
          app.maintenance.ctx = ctx;
          renderMaintenance();
        }
        return new Promise(function (resolve) {
          var settled = false;
          var finish = function (v) { if (!settled) { settled = true; resolve(v); } };
          var actions = [];
          if (ctx.form_route) {
            actions.push({ label: 'Open the form', kind: 'primary', onClick: function () {
              try { window.open(ctx.form_route, '_blank', 'noopener'); } catch (e) { /* noop */ }
              finish(false);
            } });
          }
          actions.push({ label: 'Go back', kind: ctx.form_route ? 'outline' : 'primary', onClick: function () { finish(false); } });
          actions.push({ label: capitalize(verb) + ' anyway', kind: 'ghost', onClick: function () { finish(true); } });
          UI.sheet.open({
            title: 'Maintenance form not submitted',
            body: 'No maintenance form has been submitted for this visit yet.',
            actions: actions,
            onClose: function () { finish(false); },
          });
        });
      })
      .catch(function () { return true; });
  }

  function capitalize(s) { s = String(s || ''); return s.charAt(0).toUpperCase() + s.slice(1); }

  // -- Attachments ---------------------------------------------------------
  function renderAttachments() {
    UI.clear(el.attachmentList);
    if (!app.attachments.length) {
      el.attachmentList.appendChild(h('p', { class: 'tk-note', text: 'No files attached yet.' }));
      return;
    }
    app.attachments.forEach(function (att) {
      el.attachmentList.appendChild(h('div', { class: 'tk-attachment-item' }, [
        h('span', { 'aria-hidden': 'true', text: '📎' }),
        h('span', { text: att.file_name || 'Attachment' }),
      ]));
    });
  }

  function linkFile(fileName) {
    var ci = app.currentInterval || {};
    api(API + 'link_attachment', {
      file_name: fileName, project: ci.project, task: ci.task || null,
    }).then(function (r) {
      if (r && r.status === 'success') {
        app.attachments.push({ file_name: r.file_name, file_url: r.file_url });
        renderAttachments();
        toast('Attachment added.', 'green');
        countAttachedImageAsJobPhoto(r.file_name, r.file_url);
      }
    }).catch(function (e) { toast(humanError(e), 'red'); });
  }

  // An attached IMAGE is a job photo, and now counts as one.
  //
  // The gate counts `Job Interval Photo` rows, which only the camera path wrote.
  // Attaching a photo therefore left the chip reading "Needs 1 more photo"
  // forever, however many photos you added — the warning was telling the truth
  // about the gate and lying about the situation, which is worse than either.
  //
  // Only images, and only while clocked in: a PDF quote is an attachment, not
  // evidence that the job was photographed. The registration is best-effort —
  // the gate that actually blocks a clock-out is server-side in
  // workforce/photo_gate.py, so a failure here costs the chip, not the rule.
  function countAttachedImageAsJobPhoto(fileName, fileUrl) {
    var ci = app.currentInterval || {};
    if (!ci.name) return;
    if (!/\.(jpe?g|png|heic|heif|webp|gif|bmp)$/i.test(String(fileName || fileUrl || ''))) return;

    api(API + 'record_job_photo', {
      job_interval: ci.name,
      client_uid: mintCaptureId(),
      file_name: fileName,
    }).then(function () {
      app.photoCount = (app.photoCount || 0) + 1;
      renderPhotoChip();
    }).catch(function () {
      // Leave the chip alone rather than moving it on a write that did not land.
    });
  }

  function uploadFile(file) {
    var ci = app.currentInterval || {};
    var fd = new FormData();
    fd.append('file', file, file.name);
    fd.append('is_private', '0');
    fd.append('doctype', 'Job Interval');
    fd.append('docname', ci.name);
    fd.append('folder', 'Home/Attachments');
    toast('Uploading…', null, 2000);
    fetch('/api/method/upload_file', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-Frappe-CSRF-Token': CSRF },
      body: fd,
    }).then(function (r) { return r.json(); })
      .then(function (data) {
        if (data && data.message && data.message.name) linkFile(data.message.name);
        else toast('Upload failed.', 'red');
      })
      .catch(function () { toast('Upload failed.', 'red'); });
  }

  // -- Job photos (WP-2 capture gate) --------------------------------------
  //
  // The ordering here is the entire design. A photo is REGISTERED on the server
  // the instant it is taken, carrying only a device-minted id; the bytes are
  // queued and uploaded whenever the connection allows. So:
  //
  //   * the capture gate is satisfied immediately, offline or not;
  //   * a technician on a site with no signal can still clock out;
  //   * nothing is lost — the queue survives a page reload and drains later.
  //
  // Registration failing while offline is fine too: the entry stays queued and
  // registration is retried on the next drain, so the only cost of no signal is
  // that the gate falls back to the skip-reason path.

  function photoQueue() {
    try { return JSON.parse(localStorage.getItem(PHOTO_QUEUE_KEY) || '[]'); }
    catch (e) { return []; }
  }

  function savePhotoQueue(queue) {
    try { localStorage.setItem(PHOTO_QUEUE_KEY, JSON.stringify(queue)); }
    catch (e) { /* storage may be full or blocked; the photo is still on the camera roll */ }
  }

  function mintCaptureId() {
    // Not crypto — it only has to be unique per device. Date+random is plenty,
    // and crypto.randomUUID is missing on some of the older Android handsets in
    // the field, which is exactly the population this feature exists for.
    return 'cap-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
  }

  function capturePhoto(file) {
    var ci = app.currentInterval || {};
    if (!ci.name) { toast('Clock in before taking photos.', 'orange'); return; }

    var uid = mintCaptureId();
    var entry = { uid: uid, interval: ci.name, at: new Date().toISOString(), registered: false };

    // Count it locally FIRST, so the gate prompt reflects reality even if every
    // network call below fails.
    app.photoCount = (app.photoCount || 0) + 1;
    renderPhotoChip();
    var queue = photoQueue();
    queue.push(entry);
    savePhotoQueue(queue);

    registerPhoto(entry).then(function () {
      return uploadCapturedPhoto(file, entry);
    }).catch(function () {
      toast('Photo saved on this device — it will upload when you have signal.', 'orange', 5000);
    });
  }

  function registerPhoto(entry) {
    return api(API + 'record_job_photo', {
      job_interval: entry.interval,
      client_uid: entry.uid,
      captured_on: entry.at,
    }).then(function () {
      entry.registered = true;
      savePhotoQueue(photoQueue().map(function (q) {
        return q.uid === entry.uid ? entry : q;
      }));
    });
  }

  function uploadCapturedPhoto(file, entry) {
    var fd = new FormData();
    fd.append('file', file, file.name || (entry.uid + '.jpg'));
    // Private: these are photographs of the inside of customers' property, and a
    // public file URL is unauthenticated and effectively permanent.
    fd.append('is_private', '1');
    fd.append('doctype', 'Job Interval');
    fd.append('docname', entry.interval);
    fd.append('folder', 'Home/Attachments');

    return fetch('/api/method/upload_file', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-Frappe-CSRF-Token': CSRF },
      body: fd,
    }).then(function (r) { return r.json(); })
      .then(function (data) {
        if (!(data && data.message && data.message.name)) throw new Error('upload failed');
        return api(API + 'record_job_photo', {
          job_interval: entry.interval,
          client_uid: entry.uid,
          file_name: data.message.name,
        });
      })
      .then(function () {
        savePhotoQueue(photoQueue().filter(function (q) { return q.uid !== entry.uid; }));
        toast('Photo saved.', 'green');
      });
  }

  function flushPhotoQueue() {
    var queue = photoQueue();
    if (!queue.length || !navigator.onLine) return;
    // Only re-registration is retried here. The BYTES cannot be replayed from
    // localStorage (a queued File object does not survive a reload), so a
    // capture whose upload was interrupted stays visible as "Pending" on the Job
    // Interval and in the Job Photo Compliance report rather than silently
    // vanishing. That honesty is the point: a pending row says a photo exists
    // somewhere, which is true.
    queue.filter(function (q) { return !q.registered; }).forEach(function (entry) {
      registerPhoto(entry).catch(function () { /* still offline; try again later */ });
    });
  }

  // -- The capture gate ------------------------------------------------------

  function photoGateSatisfied() {
    if (!PHOTO_GATE.require_job_photos) return true;
    var needed = PHOTO_GATE.min_photos_per_interval || 1;
    return (app.photoCount || 0) >= needed;
  }

  /**
   * Resolves { ok, reason } — ok=false means "stay". The server enforces the same
   * rule independently — this only saves the technician a round trip and a
   * rejection. Sheet instead of confirm/prompt; same three outcomes.
   */
  function withPhotoGate(verb) {
    if (photoGateSatisfied()) return Promise.resolve({ ok: true, reason: null });

    var needed = PHOTO_GATE.min_photos_per_interval || 1;
    var have = app.photoCount || 0;

    if (!PHOTO_GATE.allow_photo_skip) {
      toast('Take ' + (needed - have) + ' more photo(s) before you ' + verb + '.', 'red', 6000);
      return Promise.resolve({ ok: false, reason: null });
    }

    return new Promise(function (resolve) {
      var settled = false;
      var finish = function (v) { if (!settled) { settled = true; resolve(v); } };
      UI.sheet.open({
        title: 'No photo of this job yet',
        body: 'Take a photo before you ' + verb + ', or continue without one.',
        onClose: function () { finish({ ok: false, reason: null }); },
        actions: [
          { label: 'Take a photo now', kind: 'primary', onClick: function () {
            finish({ ok: false, reason: null });
            var cam = $('tk-camera-input');
            if (cam) cam.click();
          } },
          { label: capitalize(verb) + ' without a photo', kind: 'ghost', onClick: function (handle) {
            settled = true; // this branch resolves below, not from onClose
            handle.close('action');
            if (!PHOTO_GATE.require_skip_reason) { resolve({ ok: true, reason: null }); return; }
            UI.askText({
              title: 'Why no photo?',
              message: 'A short reason is needed to ' + verb + ' without a photo (for example: customer declined, nothing visible to photograph, camera not working).',
              placeholder: 'Reason',
              required: true,
              requiredMessage: 'A reason is needed to finish without a photo.',
              ok: capitalize(verb),
            }).then(function (reason) {
              resolve(reason ? { ok: true, reason: reason } : { ok: false, reason: null });
            });
          } },
        ],
      });
    });
  }

  // Resolves true to proceed. A photo counts as an attachment here — the nudge
  // is "did you record anything about this job", not "did you use the paperclip".
  function promptIfNoAttachments(verb) {
    if (app.attachments.length || (app.photoCount || 0) > 0) return Promise.resolve(true);
    return new Promise(function (resolve) {
      var settled = false;
      var finish = function (v) { if (!settled) { settled = true; resolve(v); } };
      UI.sheet.open({
        title: 'Nothing attached yet',
        body: 'No photos or files have been added to this job.',
        onClose: function () { finish(false); }, // Escape / backdrop = go back
        actions: [
          { label: 'Go back and add some', kind: 'primary', onClick: function () { finish(false); } },
          { label: capitalize(verb) + ' anyway', kind: 'ghost', onClick: function () { finish(true); } },
        ],
      });
    });
  }

  function maybeConsent() {
    if (!SETTINGS.enable_tracking) return;
    try {
      if (localStorage.getItem('tk_consent_shown')) return;
      localStorage.setItem('tk_consent_shown', '1');
    } catch (e) { /* storage may be blocked */ }
    toast('Your location is recorded only while you are clocked in and active.', 'orange', 6000);
  }

  // -- Off-site check --------------------------------------------------------
  function offsiteWarnEnabled() {
    return SETTINGS.offsite_warn == null ? true : !!cint(SETTINGS.offsite_warn);
  }

  // Resolves true to proceed (with the acknowledgement flag decided by the
  // caller from `needsAck`), false to stay. Only asks when every condition holds;
  // the server flags an off-site start whether or not the sheet was shown.
  function offsiteCheck(project, anchor, verb) {
    var radius = app.options.radius_m || 0;
    if (!project || !anchor || project.lat == null || project.lng == null || radius <= 0 || !offsiteWarnEnabled()) {
      return Promise.resolve({ proceed: true, ack: 0 });
    }
    var d = window.KioskGeo.distanceM({ lat: anchor.lat, lng: anchor.lng }, { lat: project.lat, lng: project.lng });
    if (d <= radius) return Promise.resolve({ proceed: true, ack: 0 });
    return UI.ask({
      title: 'You’re not at the site',
      message: 'You’re ' + fmt.distance(d) + ' from ' + (project.label || project.value) + '. ' + capitalize(verb) + ' anyway?',
      ok: capitalize(verb) + ' anyway',
      okKind: 'break',
      cancel: 'Cancel',
    }).then(function (yes) { return { proceed: yes, ack: yes ? 1 : 0 }; });
  }

  // -- Actions -------------------------------------------------------------
  function postTime(args) {
    return api(API + 'log_time', args).then(function (r) {
      if (r && r.status === 'success') {
        toast(r.message || 'Done.', 'green');
        return r;
      }
      throw new Error((r && r.message) || 'The server refused the action.');
    });
  }

  // A row of activity-type chips for use inside a sheet. Returns { el, get, set }.
  //
  // Shared because there are now four places that need one — the Clock tab, the
  // Switch sheet, and both hand-entry sheets (the second of which lives in
  // myday.js and reaches this through ctx). `log_time` and `add_manual_interval`
  // both REQUIRE a category and `Time Kiosk Settings.default_time_category` is
  // blank on this site, so a sheet without this row is a sheet whose only
  // possible outcome is "Activity type is required. Please pick one."
  function activityChipRow(initial) {
    var chosen = initial || '';
    var row = h('div', { class: 'tk-chips', role: 'group', 'aria-label': 'Activity type' });
    function render() {
      UI.clear(row);
      var types = app.options.activity_types || [];
      if (!types.length) {
        row.appendChild(h('p', { class: 'tk-note', text: 'No activity types configured.' }));
        return;
      }
      types.forEach(function (a) {
        row.appendChild(h('button', {
          type: 'button', class: 'tk-choice', text: a.label,
          'aria-pressed': chosen === a.value ? 'true' : 'false',
          on: { click: function () { chosen = chosen === a.value ? '' : a.value; render(); } },
        }));
      });
    }
    render();
    return {
      el: row,
      get: function () { return chosen; },
      set: function (v) { chosen = v || ''; render(); },
    };
  }

  function openBackdatedStartSheet() {
    var p = app.draft.project;
    var task = app.draft.task;
    var pickBtn = pickButton('tk-backdate-project', 'Choose a project');
    
    function renderP() {
      setPick(pickBtn, p ? p.label : '', p && p.value !== p.label ? p.value : '', 'Choose a project');
    }
    renderP();

    pickBtn.onclick = function() {
      openProjectPicker({ selected: p ? p.value : null, onPick: function(picked) {
        p = picked; task = null; renderP();
      }});
    };

    // Default the start to a round quarter-hour that is genuinely in the PAST.
    // Flooring `now` to 15 minutes is not that: at :45 it returns :45, so the
    // sheet opens pre-filled with the current minute and "I forgot to clock in"
    // silently records "I clocked in just now". Step back one quarter first.
    var now = new Date();
    now.setSeconds(0, 0);
    now.setMinutes(Math.floor(now.getMinutes() / 15) * 15 - 15);

    var startInput = h('input', { type: 'time', class: 'tk-input', value: hhmm(now), required: true });
    var endInput = h('input', { type: 'time', class: 'tk-input' });
    var reasonInput = h('textarea', { class: 'tk-input', rows: '2', placeholder: 'Why are you entering this by hand?', required: true });
    // Seeded from the Clock tab's selection, so picking one there and then
    // realising you forgot to clock in does not make you pick it twice.
    var activity = activityChipRow(app.draft.activity);
    var err = h('p', { class: 'tk-error', hidden: true });

    var handle = UI.sheet.open({
      title: 'Add missed time',
      body: h('div', { class: 'tk-stack' }, [
        h('div', { class: 'tk-field' }, [h('label', { text: 'Project' }), pickBtn]),
        h('div', { class: 'tk-field' }, [h('label', { text: 'Activity' }), activity.el]),
        h('div', { class: 'tk-field' }, [h('label', { text: 'Started at' }), startInput]),
        h('div', { class: 'tk-field' }, [
          h('label', { text: 'Finished at' }),
          endInput,
          h('small', { class: 'tk-hint', text: 'Leave blank if you are still on this job — it will clock you in from the start time.' }),
        ]),
        h('div', { class: 'tk-field' }, [h('label', { text: 'Reason' }), reasonInput]),
        err,
      ]),
      actions: [
        { label: 'Add time', kind: 'primary', onClick: function (hnd, btn) {
          if (!p) { toast('Choose a project.', 'orange'); return false; }
          // Checked here rather than left to the server: the refusal is certain
          // (there is no site default to fall back on) and a round trip to be
          // told to pick something you were never offered is the bug being fixed.
          if (!activity.get()) { err.hidden = false; err.textContent = 'Pick an activity.'; return false; }
          if (!startInput.value) { startInput.focus(); return false; }
          var reasonVal = reasonInput.value.trim();
          if (!reasonVal) { reasonInput.focus(); toast('A reason is required.', 'orange'); return false; }

          btn.disabled = true;
          err.hidden = true;
          setLoading(true, endInput.value ? 'Adding time…' : 'Clocking in…');
          api(API + 'add_manual_interval', {
            project: p.value,
            start_time: todayAt(startInput.value),
            end_time: endInput.value ? todayAt(endInput.value) : null,
            reason: reasonVal,
            task: task ? task.value : null,
            time_category: activity.get(),
            description: el.note ? el.note.value : null
          }).then(function (r) {
            handle.close('action');
            toast((r && r.message) || 'Time added.', 'green');
            return fetchStatus();
          }).catch(function (e) {
            setLoading(false);
            btn.disabled = false;
            // The server is the authority on overlaps and locked days, and its
            // message names the job it collided with. Show it, do not summarise it.
            err.hidden = false;
            err.textContent = humanError(e);
          });
          return false;
        } }
      ]
    });
  }

  function hhmm(d) {
    return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
  }

  // "HH:MM" from an <input type="time"> -> a Frappe datetime on today's date.
  function todayAt(timeVal) {
    var d = new Date();
    return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2)
      + " " + (timeVal.length === 5 ? timeVal + ":00" : timeVal);
  }

  function clockIn() {
    var p = app.draft.project;
    if (!p) { toast('Choose a project first.', 'orange'); return; }
    setLoading(true, 'Locating…');
    window.KioskGeo.anchorFix().then(function (anchor) {
      setLoading(true, 'Clocking in…');
      return offsiteCheck(p, anchor, 'clock in').then(function (r) {
        if (!r.proceed) { setLoading(false); return null; }
        return postTime({
          project: p.value,
          task: app.draft.task ? app.draft.task.value : null,
          action: 'Start',
          description: (el.note.value || '').trim(),
          time_category: app.draft.activity || null,
          skip_reason: null,
          lat: anchor ? anchor.lat : null,
          lng: anchor ? anchor.lng : null,
          accuracy: anchor ? anchor.accuracy : null,
          offsite_acknowledged: r.ack,
        }).then(function () {
          el.note.value = '';
          app.draft.activity = '';
          renderActivityChips();
          setDraftProject(null);
          app.photoCount = 0; // a new interval starts with no photos of its own
          maybeConsent();
          return fetchStatus();
        });
      });
    }).catch(function (e) {
      setLoading(false);
      toast(humanError(e), 'red');
    });
  }

  function openBreakSheet() {
    UI.armAudio();
    var custom = h('input', { type: 'number', class: 'tk-input', inputmode: 'numeric', min: '1', max: '480', placeholder: 'Custom minutes', 'aria-label': 'Custom break minutes' });
    
    function requestPermAndPause(m) {
      if (m > 0 && 'Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission().then(function () { pause(m); }).catch(function () { pause(m); });
      } else {
        pause(m);
      }
    }

    var handle = UI.sheet.open({
      title: 'How long a break?',
      body: h('div', { class: 'tk-stack' }, [
        h('p', { class: 'tk-note', text: 'The app counts down and buzzes once when time is up. Nothing is sent to your phone in the background.' }),
        h('div', { class: 'tk-presets' }, [15, 30, 45, 60].map(function (m) {
          return h('button', { type: 'button', class: 'tk-preset', on: { click: function () { handle.close('action'); requestPermAndPause(m); } } }, [String(m), h('small', { text: 'minutes' })]);
        })),
        h('div', { class: 'tk-field' }, [h('label', { text: 'Or a custom length' }), custom]),
      ]),
      actions: [
        { label: 'Start custom break', kind: 'primary', onClick: function () {
          var m = cint(custom.value);
          if (m <= 0) { custom.focus(); return false; }
          requestPermAndPause(m);
        } },
        { label: 'Break with no timer', kind: 'ghost', onClick: function () { pause(0); } },
      ],
    });
  }

  function pause(minutes) {
    setLoading(true);
    postTime({ action: 'Pause', break_minutes: minutes || null }).then(function () {
      app.breakPlan = minutes ? { minutes: minutes, startedAt: Date.now() } : null;
      app.breakAlerted = false;
      return fetchStatus();
    }).catch(function (e) { setLoading(false); toast(humanError(e), 'red'); });
  }

  function resume() {
    setLoading(true);
    postTime({ action: 'Resume' }).then(function () {
      app.breakPlan = null;
      return fetchStatus();
    }).catch(function (e) { setLoading(false); toast(humanError(e), 'red'); });
  }

  // Switch: attachments nudge → picker → confirm sheet (task / activity / note)
  // → photo gate → maintenance warning (only when leaving the project) →
  // anchor + off-site check → log_time Switch.
  function switchJob() {
    var ci = app.currentInterval || {};
    promptIfNoAttachments('switch').then(function (go) {
      if (!go) return;
      openProjectPicker({
        title: 'Switch to…',
        selected: ci.project,
        onPick: function (p) { openSwitchConfirm(p); },
      });
    });
  }

  function openSwitchConfirm(project) {
    var ci = app.currentInterval || {};
    var draft = { task: null, activity: ci.time_category || '', note: '' };
    var taskBtn = pickButton('tk-switch-task', 'Choose a task');
    var chips = h('div', { class: 'tk-chips', role: 'group', 'aria-label': 'Activity type' });
    var note = h('textarea', { rows: '2', placeholder: 'What are you working on?', 'aria-label': 'Note' });

    function renderChips() {
      UI.clear(chips);
      app.options.activity_types.forEach(function (a) {
        chips.appendChild(h('button', {
          type: 'button', class: 'tk-choice', text: a.label,
          'aria-pressed': draft.activity === a.value ? 'true' : 'false',
          on: { click: function () { draft.activity = draft.activity === a.value ? '' : a.value; renderChips(); } },
        }));
      });
    }
    renderChips();
    taskBtn.addEventListener('click', function () {
      openTaskPicker(project.value, function (t) {
        draft.task = t;
        setPick(taskBtn, t ? t.label : '', t && t.value !== t.label ? t.value : '', 'Choose a task');
      });
    });

    UI.sheet.open({
      title: 'Switch to ' + (project.label || project.value),
      body: h('div', { class: 'tk-stack' }, [
        h('p', { class: 'tk-note', text: 'This closes your current job and starts a new one on ' + (project.label || project.value) + '.' }),
        h('div', { class: 'tk-field' }, [h('span', { class: 'tk-label', text: 'Task (optional)' }), taskBtn]),
        h('div', { class: 'tk-field' }, [h('span', { class: 'tk-label', text: 'Activity' }), chips]),
        h('div', { class: 'tk-field' }, [h('span', { class: 'tk-label', text: 'Note (optional)' }), note]),
      ]),
      actions: [
        { label: 'Confirm switch', kind: 'primary', large: true, onClick: function () {
          draft.note = (note.value || '').trim();
          runSwitch(project, draft);
        } },
        { label: 'Cancel', kind: 'ghost' },
      ],
    });
  }

  function runSwitch(project, draft) {
    var ci = app.currentInterval || {};
    var leaving = !!(project.value && ci.project && project.value !== ci.project);
    // Photo gate first, then the maintenance warning: both can refuse, and
    // asking for a skip reason only to then be blocked on maintenance would be
    // the more annoying order.
    withPhotoGate('switch jobs').then(function (gate) {
      if (!gate.ok) return;
      return (leaving ? warnIfMaintenancePending('switch jobs') : Promise.resolve(true)).then(function (go) {
        if (!go) return;
        setLoading(true, 'Locating…');
        return window.KioskGeo.anchorFix().then(function (anchor) {
          return offsiteCheck(project, anchor, 'switch').then(function (r) {
            if (!r.proceed) { setLoading(false); return; }
            return postTime({
              project: project.value,
              task: draft.task ? draft.task.value : null,
              action: 'Switch',
              description: draft.note || '',
              time_category: draft.activity || null,
              skip_reason: gate.reason || null,
              lat: anchor ? anchor.lat : null,
              lng: anchor ? anchor.lng : null,
              accuracy: anchor ? anchor.accuracy : null,
              offsite_acknowledged: r.ack,
            }).then(function () {
              app.photoCount = 0; // a new interval starts with no photos of its own
              app.maintenance = null;
              return fetchStatus();
            });
          });
        });
      });
    }).catch(function (e) { setLoading(false); toast(humanError(e), 'red'); });
  }

  // Clock Out: summary sheet → Confirm → maintenance warning → photo gate →
  // attachments nudge → anchor → log_time Stop → Day complete.
  function clockOut() {
    var ci = app.currentInterval || {};
    var body = h('div', { class: 'tk-stack' }, [UI.skeleton(5)]);
    var summary = null;
    var handle = UI.sheet.open({
      title: 'Review your day',
      body: body,
      actions: [
        { label: 'Confirm clock out', kind: 'stop', large: true, onClick: function () { finishClockOut(summary); } },
        { label: 'Not yet', kind: 'ghost' },
      ],
    });

    function renderSummary(s, offline) {
      UI.clear(body);
      var kv = h('dl', { class: 'tk-kv' });
      function add(k, v, cls) { kv.appendChild(h('div', {}, [h('dt', { text: k }), h('dd', { class: cls || '', text: v })])); }
      add('Today', fmt.hm(s.today_seconds || 0));
      add('This job', fmt.hm(s.interval_seconds || 0));
      add('Sites', (s.sites && s.sites.length) ? s.sites.join(', ') : (ci.project_title || ci.project || '—'));
      add('Photos', String(s.photo_count != null ? s.photo_count : (app.photoCount || 0)));
      var cov = s.coverage_pct;
      add('Tracking coverage', cov != null ? Math.round(cov) + '%' : '—', cov == null ? '' : (cov >= 90 ? 'is-green' : 'is-amber'));
      if (s.last_fix_at) add('Last fix', fmt.ago(s.last_fix_at));
      body.appendChild(kv);
      if (offline) body.appendChild(h('p', { class: 'tk-note', text: 'Couldn’t reach the server — this is what the app knows on its own.' }));
    }

    api(API + 'get_shift_summary', {}, { method: 'GET' })
      .then(function (s) {
        summary = s || {};
        if (UI.sheet.depth()) renderSummary(summary, false);
      })
      .catch(function () {
        summary = { interval_seconds: elapsedSeconds(), today_seconds: elapsedSeconds(), photo_count: app.photoCount || 0, sites: [ci.project_title || ci.project].filter(Boolean) };
        if (UI.sheet.depth()) renderSummary(summary, true);
      });
    return handle;
  }

  function finishClockOut(summary) {
    warnIfMaintenancePending('clock out').then(function (go) {
      if (!go) return;
      return withPhotoGate('clock out').then(function (gate) {
        if (!gate.ok) return;
        return promptIfNoAttachments('clock out').then(function (go2) {
          if (!go2) return;
          setLoading(true, 'Locating…');
          return window.KioskGeo.anchorFix().then(function (anchor) {
            setLoading(true, 'Clocking out…');
            return postTime({
              action: 'Stop',
              skip_reason: gate.reason || null,
              lat: anchor ? anchor.lat : null,
              lng: anchor ? anchor.lng : null,
              accuracy: anchor ? anchor.accuracy : null,
            }).then(function () {
              var s = summary || {};
              app.dayComplete = {
                today_seconds: s.today_seconds || elapsedSeconds(),
                interval_seconds: s.interval_seconds || elapsedSeconds(),
                photo_count: s.photo_count != null ? s.photo_count : app.photoCount,
                coverage_pct: s.coverage_pct,
                sites: s.sites || [],
              };
              return api(API + 'get_current_status', {}, { method: 'GET' }).then(function (m) {
                var keep = app.dayComplete;
                applyStatus(m);
                if (app.status === 'Idle') { app.dayComplete = keep; renderState(); }
                setLoading(false);
              });
            });
          });
        });
      });
    }).catch(function (e) { setLoading(false); toast(humanError(e), 'red'); });
  }

  // -- Tracking indicator --------------------------------------------------
  var TRACK_TEXT = {
    on: 'Tracking on',
    ready: 'Location ready',
    off: 'Tracking off',
    denied: 'Location blocked',
    unavailable: 'No GPS fix',
    insecure: 'Needs a secure connection',
    hidden: 'Paused in background',
  };
  var TRACK_CLASS = { on: 'is-on', ready: 'is-ready', denied: 'is-error', insecure: 'is-error', unavailable: 'is-warn', hidden: 'is-warn' };
  var deniedToastShown = false;
  function renderTrack(status) {
    if (!el.track) return;
    el.track.className = 'tk-hero-chip tk-track ' + (TRACK_CLASS[status] || '');
    el.trackText.textContent = TRACK_TEXT[status] || TRACK_TEXT.off;
    if (status === 'denied' && !deniedToastShown) {
      deniedToastShown = true;
      toast('Location access is required while clocked in. See Settings for how to enable it.', 'orange', 6000);
    }
  }

  // -- Service worker ------------------------------------------------------
  function sendSWConfig() {
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker.ready.then(function (reg) {
      var target = reg.active || navigator.serviceWorker.controller;
      if (target) {
        target.postMessage({
          type: 'config',
          data: { csrf_token: CSRF, max_batch_size: SETTINGS.max_batch_size || 50 },
        });
      }
    }).catch(function () { /* noop */ });
  }

  function registerServiceWorker() {
    if (!('serviceWorker' in navigator)) return;
    // The ?v= token ties the worker to this deploy: a new build is a new
    // script URL, so the browser installs the new worker (skipWaiting) and
    // its activate step deletes the previous deploy's cache. No manual
    // CACHE-version bumps in kiosk-sw.js anymore.
    var swUrl = '/kiosk-sw.js' + (BUILD ? '?v=' + encodeURIComponent(BUILD) : '');
    var hadController = !!navigator.serviceWorker.controller;

    navigator.serviceWorker.register(swUrl)
      .then(function (reg) {
        app.swReg = reg;
        sendSWConfig();
        watchForUpdates(reg);
      })
      .catch(function () { /* SW optional; app still works online */ });
    navigator.serviceWorker.ready.then(sendSWConfig);

    // When an updated worker takes control mid-session, reload once so the
    // page runs the code it just precached — but never while the user is
    // looking at the app (a visible reload could eat a half-typed note), and
    // never on the very first install (the page is already current).
    var reloadScheduled = false;
    navigator.serviceWorker.addEventListener('controllerchange', function () {
      if (!hadController || reloadScheduled) return;
      reloadScheduled = true;
      if (document.hidden) { window.location.reload(); return; }
      document.addEventListener('visibilitychange', function onHide() {
        if (!document.hidden) return;
        document.removeEventListener('visibilitychange', onHide);
        window.location.reload();
      });
    });
  }

  // Installed PWAs can stay open for days, but the browser only checks for a
  // new worker on navigation — so also re-check whenever the app returns to
  // the foreground, and hourly while it stays open. With the reload hook
  // above, a deploy reaches even a kiosk that is never relaunched.
  function watchForUpdates(reg) {
    function check() {
      reg.update().catch(function () { /* offline — next check will retry */ });
    }
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible') check();
    });
    setInterval(check, 60 * 60 * 1000);
  }

  // Settings → "Refresh app": check for a new worker, then reload.
  function refreshApp() {
    var reload = function () { window.location.reload(); };
    if (app.swReg) app.swReg.update().then(reload, reload); else reload();
  }

  // -- Clock / timer -------------------------------------------------------
  function elapsedSeconds() {
    var ci = app.currentInterval;
    if (!ci || !ci.start_time) return 0;
    var start = fmt.parseDT(ci.start_time);
    if (!start) return 0;
    var pausedAt = app.status === 'Paused' && ci.last_pause_time ? fmt.parseDT(ci.last_pause_time) : null;
    var now = pausedAt ? pausedAt.getTime() : Date.now();
    var pausedMs = (ci.total_paused_seconds || 0) * 1000;
    return Math.max(0, Math.floor((now - start.getTime() - pausedMs) / 1000));
  }

  function breakRemainingSeconds() {
    var ci = app.currentInterval || {};
    var minutes = cint(ci.planned_break_minutes) || (app.breakPlan ? app.breakPlan.minutes : 0);
    if (!minutes) return null;
    var startedAt = ci.last_pause_time ? fmt.parseDT(ci.last_pause_time) : null;
    var t0 = startedAt ? startedAt.getTime() : (app.breakPlan ? app.breakPlan.startedAt : Date.now());
    return Math.floor((t0 + minutes * 60000 - Date.now()) / 1000);
  }

  function tick() {
    if (!el.clock) return;
    el.clock.textContent = fmt.clock(new Date());
    if (app.status === 'Open') {
      el.elapsed.textContent = fmt.hms(elapsedSeconds());
    } else if (app.status === 'Paused') {
      var rem = breakRemainingSeconds();
      if (rem == null) {
        var ci = app.currentInterval || {};
        var since = ci.last_pause_time ? fmt.parseDT(ci.last_pause_time) : null;
        var onBreak = since ? Math.max(0, Math.floor((Date.now() - since.getTime()) / 1000)) : 0;
        el.countdown.textContent = fmt.hms(onBreak);
        el.countdown.classList.remove('is-over');
        el.status.textContent = 'On break';
      } else if (rem > 0) {
        el.countdown.textContent = fmt.hms(rem);
        el.countdown.classList.remove('is-over');
        el.status.textContent = 'Break — time left';
      } else {
        el.countdown.textContent = '0:00:00';
        el.status.textContent = 'Break time is up';
        if (!app.breakAlerted) {
          app.breakAlerted = true;
          el.countdown.classList.add('is-over');
          UI.buzz();
          toast('Break time is up.', 'orange', 5000);
          
          if ('serviceWorker' in navigator && 'Notification' in window && Notification.permission === 'granted') {
            navigator.serviceWorker.ready.then(function (reg) {
              var ci = app.currentInterval || {};
              var p = ci.project_title || ci.project || 'your job';
              // Limitation: A web page cannot run a timer while the OS has suspended it.
              // So this fires reliably when the page is alive-but-hidden (screen on, app switched),
              // and cannot fire when the page has been frozen.
              reg.showNotification('Break is over', {
                body: 'Break is over — clock back in to ' + p,
                tag: 'break-over',
                renotify: true
              });
            }).catch(function () {});
          }
        }
      }
    }
  }

  // -- Wiring --------------------------------------------------------------
  function wire() {
    el.pickProject.addEventListener('click', function () {
      openProjectPicker({ selected: app.draft.project ? app.draft.project.value : null, onPick: setDraftProject });
    });
    el.pickTask.addEventListener('click', function () {
      if (!app.draft.project) { toast('Choose a project first.', 'orange'); return; }
      openTaskPicker(app.draft.project.value, function (t) {
        app.draft.task = t;
        setPick(el.pickTask, t ? t.label : '', t && t.value !== t.label ? t.value : '', 'Choose a task');
      });
    });
    el.clockIn.addEventListener('click', clockIn);
    if (el.forgotClockIn) el.forgotClockIn.addEventListener('click', openBackdatedStartSheet);
    el.pause.addEventListener('click', openBreakSheet);
    el.resume.addEventListener('click', resume);
    el.switchBtn.addEventListener('click', switchJob);
    el.clockOut.addEventListener('click', clockOut);
    el.startAnother.addEventListener('click', function () { app.dayComplete = null; renderState(); });

    $('tk-add-attach').addEventListener('click', function () {
      if (app.currentInterval) $('tk-file-input').click();
    });
    $('tk-take-pic').addEventListener('click', function () {
      if (app.currentInterval) $('tk-camera-input').click();
    });
    $('tk-file-input').addEventListener('change', function () {
      Array.prototype.slice.call(this.files).forEach(uploadFile);
      this.value = '';
    });
    // The camera goes through capturePhoto, NOT uploadFile: a camera capture is
    // the thing the gate counts, and it has to be registered on the server before
    // (and independently of) its bytes finishing an upload. The paperclip button
    // above keeps the plain attachment path — a PDF of a delivery note is not a
    // job photo and should not satisfy a photo requirement.
    $('tk-camera-input').addEventListener('change', function () {
      Array.prototype.slice.call(this.files).forEach(capturePhoto);
      this.value = '';
    });

    // Drain the offline registration queue whenever the device comes back.
    window.addEventListener('online', flushPhotoQueue);

    // Coming back to the foreground: confirm the interval (the hourly sweeper
    // may have auto-closed it) — at most every 30 s.
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible') {
        tick();
        if (Date.now() - statusFetchedAt > 30000 && !app.loading) fetchStatus();
      }
    });
  }

  // Captured as early as possible — the browser fires this once, before init.
  window.addEventListener('beforeinstallprompt', function (ev) {
    ev.preventDefault();
    app.installPrompt = ev;
  });

  function promptInstall() {
    var ev = app.installPrompt;
    if (!ev) return Promise.resolve(null);
    app.installPrompt = null;
    try {
      ev.prompt();
      return (ev.userChoice || Promise.resolve(null)).then(function (c) { return c && c.outcome; });
    } catch (e) { return Promise.resolve(null); }
  }

  // -- Init ----------------------------------------------------------------
  function init() {
    var root = $('kiosk-root');
    buildShell(root);
    root.removeAttribute('aria-busy');
    cacheEls();
    wire();
    UI.theme.syncMeta();

    window.KioskGeo.configure(SETTINGS).onStatus(renderTrack);
    // Ask for location permission on visit, so it's granted before clock-in.
    window.KioskGeo.warmup();
    registerServiceWorker();

    setInterval(tick, 1000);
    tick();

    // The context the view modules get. Everything they need from here, and
    // nothing they should not touch.
    var ctx = {
      api: api,
      API: API,
      humanError: humanError,
      state: app,
      boot: BOOT,
      settings: SETTINGS,
      build: BUILD,
      openProjectPicker: openProjectPicker,
      activityChipRow: activityChipRow,
      photoQueueLength: function () { return photoQueue().length; },
      flushQueues: function () {
        flushPhotoQueue();
        if ('serviceWorker' in navigator) {
          navigator.serviceWorker.ready.then(function (reg) {
            var t = reg.active || navigator.serviceWorker.controller;
            if (t) t.postMessage({ type: 'flush' });
          }).catch(function () { /* noop */ });
        }
      },
      refreshApp: refreshApp,
      promptInstall: promptInstall,
      canInstall: function () { return !!app.installPrompt; },
      setBadge: setBadge,
      setTab: setTab,
    };
    registerCaptureState();
    setTimeout(preloadCapturePanel, 8000);

    var v = views();
    TABS.forEach(function (t) {
      if (t.id !== 'clock' && v[t.id] && v[t.id].mount) {
        try { v[t.id].mount($('tk-panel-' + t.id), ctx); } catch (e) { /* a broken view must not take the clock down */ }
      }
    });
    setTab('clock');

    setDraftProject(null);
    loadOptions();
    // Seed instantly from the server boot payload, then confirm with a fetch.
    applyStatus(BOOT.status);
    fetchStatus();

    // Drain photos queued in a prior offline session. Reopening the app already online fires
    // no 'online' event, so without this the queue never registers those captures — the Job
    // Photo Compliance report then under-counts and photo_count stays low.
    if (navigator.onLine) flushPhotoQueue();
  }

  // WI-079 slice 2: what a report from the kiosk says about the kiosk, collected when someone
  // opens "Report a problem" (Settings). Status and counts only, never a position. The
  // capture recorder comes from capture.bundle.js, which is not precached, so on an offline
  // cold start it is absent and this quietly does nothing.
  var locationQueueSeen = null;
  function registerCaptureState() {
    var cap = window.ee_capture;
    if (!cap || typeof cap.registerCaptureState !== 'function') return;
    cap.registerCaptureState(function () {
      // The worker answers the location-queue count asynchronously, so report the last
      // answer and ask again for the next report. A missing answer reads as null, not 0.
      try {
        if (window.KioskGeo && window.KioskGeo.queuedCount) {
          window.KioskGeo.queuedCount().then(function (n) { locationQueueSeen = n; }).catch(function () { /* noop */ });
        }
      } catch (e) { /* the report must never break the kiosk */ }
      var diag = {};
      try { diag = (window.KioskGeo && window.KioskGeo.getDiagnostics && window.KioskGeo.getDiagnostics()) || {}; } catch (e) { diag = {}; }
      var queued = null;
      try { queued = photoQueue().length; } catch (e) { queued = null; }
      return {
        kiosk: {
          clock_status: app.status || null,
          interval_open: !!(app.currentInterval && app.currentInterval.name),
          photo_queue: queued,
          location_queue: locationQueueSeen,
          last_sync_ok_at: lastSyncOkAt,
          location_permission: diag.permission || null,
          location_status: diag.status || null,
          build: BUILD || null,
        },
      };
    });
  }

  // The report form is lazy everywhere else. The kiosk loads it once it is idle and online,
  // because neither capture bundle is precached: if the signal drops later, "Report a
  // problem" must still open, so the report can be saved on the device and sent on the next
  // tap once it is back. The recorder's own loader sees the global and does not load it twice.
  function preloadCapturePanel() {
    try {
      var cfg = window.EE_CAPTURE || {};
      if (!window.ee_capture || window.ee_capture_panel || !cfg.panel_url || !navigator.onLine) return;
      var s = document.createElement('script');
      s.src = cfg.panel_url;
      s.async = true;
      (document.head || document.documentElement).appendChild(s);
    } catch (e) { /* a missing report form must never cost the clock */ }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
