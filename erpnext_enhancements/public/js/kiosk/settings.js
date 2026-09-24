/*
 * Time Kiosk — the Settings view (window.KioskViews.settings).
 *
 * Targets: the Time Kiosk PWA front-end. Loaded by www/kiosk.html after ui.js
 * and geo.js, before app.js — NOT through hooks.py. app.js mounts it into
 * #tk-panel-settings with a context object (see app.js init).
 *
 * Sections:
 *   Appearance      System / Light / Dark segmented control → KioskUI.theme.set
 *                   (System is the default; the choice lives in
 *                   localStorage.tk_theme and is applied before first paint by
 *                   the inline script in kiosk.html).
 *   Location        live diagnostics from KioskGeo.getDiagnostics() (status with
 *                   its reason, permission, last fix age + accuracy, wake lock,
 *                   secure context, installed / browser tab, online), refreshed
 *                   every 5 s while the tab is open; queued points (asked of the
 *                   service worker over a MessageChannel — KioskGeo.queuedCount)
 *                   and queued photos (the localStorage photo queue); a
 *                   per-platform fix-it guide; re-check permission; send now.
 *   Install         beforeinstallprompt when the browser offers it, the
 *                   Share → Add to Home Screen steps on iOS, otherwise the
 *                   browser-menu hint.
 *   App             Refresh app (SW update check + reload — the old standalone
 *                   nav bar's refresh button moved here), version (KIOSK_BUILD),
 *                   who is signed in, and the location consent text.
 */
(function () {
  'use strict';

  var UI = window.KioskUI;
  var h = UI.h;
  var fmt = UI.fmt;

  var ctx = null;
  var el = {};
  var timer = null;

  var STATUS_TEXT = {
    on: ['Tracking now', 'is-green'],
    ready: ['Ready — starts when you clock in', 'is-green'],
    off: ['Off', ''],
    denied: ['Blocked — location permission was refused', 'is-red'],
    unavailable: ['No fix — the phone could not find your position', 'is-amber'],
    insecure: ['Withheld — the page is not on a secure (https) connection', 'is-red'],
    hidden: ['Paused — the app is in the background', 'is-amber'],
  };
  var PERM_TEXT = { granted: 'Allowed', denied: 'Denied', prompt: 'Not asked yet', unknown: 'Unknown' };

  function mount(container, c) {
    ctx = c;
    container.appendChild(h('div', { class: 'tk-page-head' }, [h('div', {}, [h('h1', { text: 'Settings' })])]));

    // Appearance
    el.seg = h('div', { class: 'tk-seg', role: 'group', 'aria-label': 'Theme' });
    container.appendChild(h('div', { class: 'tk-card' }, [
      h('p', { class: 'tk-card-title', text: 'Appearance' }),
      el.seg,
      h('p', { class: 'tk-note', style: { marginTop: '8px' }, text: 'System follows your phone’s light or dark setting.' }),
    ]));
    renderSeg();

    // Location
    el.diag = h('dl', { class: 'tk-kv' });
    container.appendChild(h('div', { class: 'tk-card' }, [
      h('p', { class: 'tk-card-title', text: 'Location tracking' }),
      el.diag,
      h('div', { class: 'tk-stack', style: { marginTop: '12px' } }, [
        h('button', { type: 'button', class: 'tk-btn tk-btn-primary', text: 'How to fix location', on: { click: openFixItGuide } }),
        h('div', { class: 'tk-btn-row' }, [
          h('button', { type: 'button', class: 'tk-btn tk-btn-outline', text: 'Re-check permission', on: { click: function () { window.KioskGeo.warmup(); setTimeout(renderDiag, 800); } } }),
          h('button', { type: 'button', class: 'tk-btn tk-btn-outline', text: 'Send queued now', on: { click: function () { ctx.flushQueues(); UI.toast('Sending what is queued…', null, 2500); setTimeout(renderDiag, 2000); } } }),
        ]),
      ]),
    ]));

    // Help (WI-079 slice 2). The capture widget's kiosk entry point: no floating button here,
    // the clock screen has no room for one. Hidden when the capture code is not on the page
    // (an offline cold start, since its bundle is not precached).
    if (window.ee_capture && typeof window.ee_capture.open === 'function') {
      container.appendChild(h('div', { class: 'tk-card' }, [
        h('p', { class: 'tk-card-title', text: 'Help' }),
        h('button', { type: 'button', class: 'tk-btn tk-btn-primary', text: 'Report a problem', on: { click: function () {
          try { window.ee_capture.open({ surface: 'kiosk' }); } catch (e) { UI.toast('Could not open the report form.', 'red', 3000); }
        } } }),
        h('p', { class: 'tk-note', style: { marginTop: '12px' }, text: 'Describe what went wrong and add a screenshot if you have one. You see everything that will be sent, and it stays in ERPNext.' }),
      ]));
    }

    // Install
    el.install = h('div', { class: 'tk-stack' });
    container.appendChild(h('div', { class: 'tk-card' }, [h('p', { class: 'tk-card-title', text: 'Install' }), el.install]));

    // App
    var boot = ctx.boot || {};
    container.appendChild(h('div', { class: 'tk-card' }, [
      h('p', { class: 'tk-card-title', text: 'App' }),
      h('dl', { class: 'tk-kv' }, [
        kv('Version', ctx.build || 'dev'),
        kv('Employee', boot.employee_name || boot.employee || '—'),
        kv('Signed in as', boot.user || '—'),
      ]),
      h('button', { type: 'button', class: 'tk-btn tk-btn-outline', style: { marginTop: '12px' }, text: 'Refresh app', on: { click: function () { UI.toast('Refreshing…', null, 1500); ctx.refreshApp(); } } }),
      h('p', { class: 'tk-note', style: { marginTop: '12px' }, text: 'Your location is recorded only while you are clocked in and active. It stops when you take a break or clock out.' }),
    ]));
  }

  function kv(k, v, cls) {
    return h('div', {}, [h('dt', { text: k }), h('dd', { class: cls || '', text: v })]);
  }

  function show() {
    renderSeg();
    renderDiag();
    renderInstall();
    if (timer) clearInterval(timer);
    timer = setInterval(renderDiag, 5000);
  }

  function hide() {
    if (timer) clearInterval(timer);
    timer = null;
  }

  // -- Appearance ------------------------------------------------------------
  function renderSeg() {
    UI.clear(el.seg);
    var current = UI.theme.get();
    [['system', 'System'], ['light', 'Light'], ['dark', 'Dark']].forEach(function (m) {
      el.seg.appendChild(h('button', {
        type: 'button', text: m[1], 'aria-pressed': current === m[0] ? 'true' : 'false',
        on: { click: function () { UI.theme.set(m[0]); renderSeg(); } },
      }));
    });
  }

  // -- Diagnostics -----------------------------------------------------------
  function renderDiag() {
    if (!window.KioskGeo) return;
    var d = window.KioskGeo.getDiagnostics();
    UI.clear(el.diag);
    var s = STATUS_TEXT[d.status] || STATUS_TEXT.off;
    el.diag.appendChild(kv('Status', d.enabled ? s[0] : 'Off in Time Kiosk Settings', d.enabled ? s[1] : ''));
    el.diag.appendChild(kv('Permission', PERM_TEXT[d.permission] || d.permission, d.permission === 'denied' ? 'is-red' : (d.permission === 'granted' ? 'is-green' : '')));
    var fix = d.lastFixAt ? fmt.ago(d.lastFixAt) + (d.lastAccuracy != null ? ' · ±' + Math.round(d.lastAccuracy) + ' m' : '') : 'No fix yet';
    el.diag.appendChild(kv('Last fix', fix, d.lastFixAt && Date.now() - d.lastFixAt < 15 * 60000 ? 'is-green' : ''));
    var wl = !d.wakeLockWanted ? 'Off in settings' : (!d.wakeLockSupported ? 'Not supported on this phone' : (d.wakeLock ? 'Holding the screen on' : (d.running ? 'Not held' : 'Only while clocked in')));
    el.diag.appendChild(kv('Keep screen on', wl, d.wakeLock ? 'is-green' : ''));
    el.diag.appendChild(kv('Secure connection', d.secure ? 'Yes' : 'No — location is withheld', d.secure ? 'is-green' : 'is-red'));
    el.diag.appendChild(kv('Running as', d.standalone ? 'Installed app' : 'Browser tab'));
    el.diag.appendChild(kv('Network', d.online ? 'Online' : 'Offline', d.online ? 'is-green' : 'is-amber'));
    var photos = ctx.photoQueueLength();
    el.diag.appendChild(kv('Queued photos', String(photos), photos ? 'is-amber' : ''));
    var pointsRow = kv('Queued points', '…');
    el.diag.appendChild(pointsRow);
    window.KioskGeo.queuedCount().then(function (n) {
      var dd = pointsRow.querySelector('dd');
      if (!dd) return;
      dd.textContent = n == null ? 'Unknown (no worker)' : String(n);
      dd.className = n ? 'is-amber' : '';
    });
  }

  function openFixItGuide() {
    var p = UI.platform();
    var ios = h('div', { class: 'tk-stack' }, [
      h('p', {}, [h('strong', { text: 'iPhone / iPad (Safari or the Home-Screen app)' })]),
      h('p', { text: 'Settings → Privacy & Security → Location Services → Safari Websites → While Using the App, and turn on Precise Location.' }),
      h('p', { text: 'Keep the app open on screen while working. Low Power Mode pauses GPS.' }),
    ]);
    var android = h('div', { class: 'tk-stack' }, [
      h('p', {}, [h('strong', { text: 'Android (Chrome)' })]),
      h('p', { text: 'Tap the lock icon in the address bar → Permissions → Location → Allow, with Precise location on.' }),
      h('p', { text: 'Android Settings → Apps → Chrome → Battery → Unrestricted, so the system does not stop tracking in the background.' }),
      h('p', { text: 'Keep the screen on while working.' }),
    ]);
    var order = p === 'android' ? [android, ios] : [ios, android];
    UI.sheet.open({
      title: 'Fixing location tracking',
      body: h('div', { class: 'tk-stack' }, [
        h('p', { class: 'tk-note', text: 'After changing a setting, come back here and tap Re-check permission.' }),
        order[0], order[1],
      ]),
      actions: [{ label: 'Done', kind: 'primary' }],
    });
  }

  // -- Install ---------------------------------------------------------------
  function renderInstall() {
    UI.clear(el.install);
    if (UI.isStandalone()) {
      el.install.appendChild(h('p', { class: 'tk-note', text: 'Installed — you are running the Home-Screen app.' }));
      return;
    }
    if (ctx.canInstall()) {
      el.install.appendChild(h('p', { class: 'tk-note', text: 'Install the kiosk as an app for a full screen and a home-screen icon.' }));
      el.install.appendChild(h('button', { type: 'button', class: 'tk-btn tk-btn-primary', text: 'Install app', on: { click: function () {
        ctx.promptInstall().then(function (outcome) {
          if (outcome === 'accepted') UI.toast('Installing…', 'green');
          renderInstall();
        });
      } } }));
      return;
    }
    if (UI.platform() === 'ios') {
      el.install.appendChild(h('p', { text: 'In Safari: tap Share, then “Add to Home Screen”. Open the kiosk from that icon from now on.' }));
    } else {
      el.install.appendChild(h('p', { text: 'In your browser menu choose “Install app” or “Add to Home screen”.' }));
    }
  }

  window.KioskViews = window.KioskViews || {};
  window.KioskViews.settings = { mount: mount, show: show, hide: hide };
})();
