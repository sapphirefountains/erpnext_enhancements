/*
 * Time Kiosk — shared UI layer (theme, bottom sheets, toasts, node builder,
 * formatting). Exposes window.KioskUI. No app state, no network.
 *
 * Targets: the Time Kiosk PWA front-end. Loaded by www/kiosk.html BEFORE
 * geo.js / the view modules / app.js — NOT through hooks.py. Precached by
 * kiosk-sw.js; every consumer reads it through window.KioskUI.
 *
 * THEME. Three modes, stored in localStorage.tk_theme: 'system' (default),
 * 'light', 'dark'. The mode is expressed as <html data-theme="light|dark">, and
 * the attribute is ABSENT for system — the stylesheet's dark palette then
 * follows prefers-color-scheme (css/kiosk/kiosk.css, header comment). The
 * inline script in kiosk.html's head applies the stored mode before the
 * stylesheet is parsed (no flash); this module owns every later change and
 * keeps <meta name="theme-color"> equal to the computed --tk-bg, on a user
 * change AND on a matchMedia change while in system mode, so the browser chrome
 * never disagrees with the page.
 *
 * SHEETS. Every interruption in the app is a bottom sheet from here — there is
 * no window.confirm / prompt / alert anywhere under public/js/kiosk/
 * (tests/test_kiosk_frontend.py fails the build on one). A sheet is a
 * role="dialog" aria-modal="true" panel over a backdrop: Escape and a backdrop
 * tap dismiss it (unless `dismissible: false`), Tab is trapped inside it, and
 * focus returns to the element that opened it. Sheets stack; only the top one
 * owns the keyboard. `ask()` and `askText()` are the promise-shaped replacements
 * for confirm() and prompt().
 *
 * Style: ES5 (var, function), promises only — this runs on older Android
 * WebViews in the field. No optional chaining, no class fields, no arrows.
 */
(function () {
  'use strict';

  var THEME_KEY = 'tk_theme';
  var THEME_MODES = ['system', 'light', 'dark'];

  // -- DOM helpers ---------------------------------------------------------

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // h('button', { class: 'tk-btn', on: { click: fn }, 'aria-label': 'x' }, ['text', node])
  // Children are text nodes or nodes — never HTML strings — so server data can
  // never be rendered as markup.
  function h(tag, attrs, children) {
    var node = document.createElement(tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v == null || v === false) return;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k === 'on') {
        Object.keys(v).forEach(function (evt) { node.addEventListener(evt, v[evt]); });
      } else if (k === 'style' && typeof v === 'object') {
        Object.keys(v).forEach(function (p) { node.style[p] = v[p]; });
      } else if (k === 'value' || k === 'checked' || k === 'disabled' || k === 'hidden') {
        node[k] = v;
      } else if (k === 'dataset') {
        Object.keys(v).forEach(function (d) { node.dataset[d] = v[d]; });
      } else node.setAttribute(k, v === true ? '' : v);
    });
    appendChildren(node, children);
    return node;
  }

  function appendChildren(node, children) {
    if (children == null) return;
    if (!Array.isArray(children)) children = [children];
    children.forEach(function (c) {
      if (c == null || c === false) return;
      if (Array.isArray(c)) { appendChildren(node, c); return; }
      if (typeof c === 'string' || typeof c === 'number') node.appendChild(document.createTextNode(String(c)));
      else node.appendChild(c);
    });
  }

  function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }

  function skeleton(lines, opts) {
    opts = opts || {};
    var box = h('div', { class: 'tk-skel-group', 'aria-busy': 'true', 'aria-label': 'Loading' });
    for (var i = 0; i < (lines || 3); i++) {
      box.appendChild(h('span', { class: 'tk-skel ' + (opts.block ? 'tk-skel-block' : 'tk-skel-line' + (i % 2 ? ' is-short' : '')) }));
    }
    return box;
  }

  function reducedMotion() {
    try { return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches); }
    catch (e) { return false; }
  }

  function isStandalone() {
    if (window.navigator.standalone === true) return true; // iOS home-screen app
    try {
      return !!(window.matchMedia && (
        window.matchMedia('(display-mode: standalone)').matches ||
        window.matchMedia('(display-mode: fullscreen)').matches ||
        window.matchMedia('(display-mode: minimal-ui)').matches
      ));
    } catch (e) { return false; }
  }

  function platform() {
    var ua = navigator.userAgent || '';
    // iPadOS 13+ reports as Macintosh; the touch-points check separates it.
    if (/iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return 'ios';
    if (/Android/.test(ua)) return 'android';
    return 'other';
  }

  // -- Toasts --------------------------------------------------------------

  function toast(message, kind, ms) {
    var box = document.getElementById('tk-toasts');
    if (!box) return;
    var t = h('div', { class: 'tk-toast' + (kind ? ' is-' + kind : ''), role: 'status', text: message });
    box.appendChild(t);
    setTimeout(function () {
      t.style.opacity = '0';
      setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 220);
    }, ms || 3500);
  }

  // -- Theme ---------------------------------------------------------------

  var themeListeners = [];
  var darkQuery = null;
  try { darkQuery = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null; } catch (e) { darkQuery = null; }

  function readTheme() {
    try {
      var v = localStorage.getItem(THEME_KEY);
      return THEME_MODES.indexOf(v) === -1 ? 'system' : v;
    } catch (e) { return 'system'; }
  }

  function applyTheme(mode) {
    var root = document.documentElement;
    if (mode === 'light' || mode === 'dark') root.setAttribute('data-theme', mode);
    else root.removeAttribute('data-theme');
    syncThemeMeta();
  }

  function effectiveTheme() {
    var mode = readTheme();
    if (mode === 'light' || mode === 'dark') return mode;
    return darkQuery && darkQuery.matches ? 'dark' : 'light';
  }

  // <meta name="theme-color"> tells the browser what colour to paint its own
  // chrome (status bar, title bar in an installed app). Read the resolved --tk-bg
  // so there is exactly one source for the colour: the stylesheet.
  function syncThemeMeta() {
    var meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) return;
    var bg = '';
    try { bg = getComputedStyle(document.documentElement).getPropertyValue('--tk-bg').trim(); } catch (e) { bg = ''; }
    if (bg) meta.setAttribute('content', bg);
  }

  function notifyTheme() {
    var eff = effectiveTheme();
    themeListeners.forEach(function (cb) { try { cb(eff, readTheme()); } catch (e) { /* listener's problem */ } });
  }

  var theme = {
    MODES: THEME_MODES,
    get: readTheme,
    effective: effectiveTheme,
    set: function (mode) {
      if (THEME_MODES.indexOf(mode) === -1) mode = 'system';
      try {
        if (mode === 'system') localStorage.removeItem(THEME_KEY);
        else localStorage.setItem(THEME_KEY, mode);
      } catch (e) { /* storage blocked — still apply for this session */ }
      applyTheme(mode);
      notifyTheme();
    },
    apply: function () { applyTheme(readTheme()); },
    syncMeta: syncThemeMeta,
    onChange: function (cb) { themeListeners.push(cb); },
  };

  if (darkQuery) {
    var onScheme = function () { syncThemeMeta(); notifyTheme(); };
    if (darkQuery.addEventListener) darkQuery.addEventListener('change', onScheme);
    else if (darkQuery.addListener) darkQuery.addListener(onScheme);
  }

  // -- Bottom sheets -------------------------------------------------------

  var sheetStack = [];
  var FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
    'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function sheetHost() {
    var host = document.getElementById('tk-sheets');
    if (!host) {
      host = h('div', { class: 'tk-sheet-host', id: 'tk-sheets' });
      document.body.appendChild(host);
    }
    return host;
  }

  function focusables(node) {
    return Array.prototype.slice.call(node.querySelectorAll(FOCUSABLE)).filter(function (n) {
      return n.offsetParent !== null || n === document.activeElement;
    });
  }

  function onSheetKeydown(ev) {
    var top = sheetStack[sheetStack.length - 1];
    if (!top) return;
    if (ev.key === 'Escape' || ev.key === 'Esc') {
      if (top.opts.dismissible !== false) { ev.preventDefault(); top.close('dismiss'); }
      return;
    }
    if (ev.key !== 'Tab') return;
    var list = focusables(top.el);
    if (!list.length) { ev.preventDefault(); return; }
    var first = list[0], last = list[list.length - 1];
    if (ev.shiftKey && (document.activeElement === first || !top.el.contains(document.activeElement))) {
      ev.preventDefault(); last.focus();
    } else if (!ev.shiftKey && document.activeElement === last) {
      ev.preventDefault(); first.focus();
    }
  }
  document.addEventListener('keydown', onSheetKeydown);

  function buildActions(foot, actions, handle) {
    clear(foot);
    (actions || []).forEach(function (a) {
      var kind = a.kind || 'outline';
      var b = h('button', {
        type: 'button',
        class: 'tk-btn tk-btn-' + kind + (a.large ? ' tk-btn-lg' : ''),
        disabled: !!a.disabled,
        text: a.label,
      });
      b.addEventListener('click', function () {
        var r = a.onClick ? a.onClick(handle, b) : undefined;
        if (a.close !== false && r !== false) handle.close('action');
      });
      if (a.id) b.id = a.id;
      foot.appendChild(b);
    });
  }

  /**
   * Open a bottom sheet.
   *   opts.title        heading text
   *   opts.body         Node | string (plain text) | function(bodyEl)
   *   opts.actions      [{ label, kind, onClick(handle, btn), close: true, large, disabled, id }]
   *   opts.full         full-screen sheet (pickers)
   *   opts.dismissible  false = no Escape / backdrop / close button (default true)
   *   opts.onClose(reason)  'action' | 'dismiss' | 'programmatic'
   *   opts.initialFocus  selector inside the sheet to focus first
   * Returns a handle: { el, body, close(reason), setActions(list), setTitle(t), opts }.
   */
  function openSheet(opts) {
    opts = opts || {};
    var host = sheetHost();
    var previouslyFocused = document.activeElement;
    var closed = false;

    var body = h('div', { class: 'tk-sheet-body' });
    var foot = h('div', { class: 'tk-sheet-foot' });
    var titleId = 'tk-sheet-title-' + Date.now().toString(36) + Math.floor(Math.random() * 1e4);
    var closeBtn = h('button', { type: 'button', class: 'tk-sheet-close', 'aria-label': 'Close', text: '×' });
    var head = h('div', { class: 'tk-sheet-head' }, [
      h('h2', { id: titleId, text: opts.title || '' }),
      opts.dismissible === false ? null : closeBtn,
    ]);
    var sheet = h('div', {
      class: 'tk-sheet' + (opts.full ? ' is-full' : ''),
      role: 'dialog',
      'aria-modal': 'true',
      'aria-labelledby': titleId,
      tabindex: '-1',
    }, [h('div', { class: 'tk-sheet-handle' }), head, body, foot]);
    var backdrop = h('div', { class: 'tk-sheet-backdrop' });
    var layer = h('div', { class: 'tk-sheet-layer' }, [backdrop, sheet]);

    var handle = {
      el: sheet,
      body: body,
      opts: opts,
      close: function (reason) {
        if (closed) return;
        closed = true;
        var idx = sheetStack.indexOf(handle);
        if (idx !== -1) sheetStack.splice(idx, 1);
        layer.classList.remove('is-open');
        var done = function () {
          if (layer.parentNode) layer.parentNode.removeChild(layer);
          if (!sheetStack.length) document.body.style.overflow = '';
        };
        if (reducedMotion()) done(); else setTimeout(done, 180);
        if (opts.onClose) { try { opts.onClose(reason || 'programmatic'); } catch (e) { /* noop */ } }
        if (previouslyFocused && previouslyFocused.focus && document.body.contains(previouslyFocused)) {
          try { previouslyFocused.focus(); } catch (e) { /* noop */ }
        }
      },
      setActions: function (list) { buildActions(foot, list, handle); },
      setTitle: function (t) { head.querySelector('h2').textContent = t; },
    };

    if (typeof opts.body === 'function') opts.body(body, handle);
    else if (typeof opts.body === 'string') body.appendChild(h('p', { text: opts.body }));
    else if (opts.body) body.appendChild(opts.body);
    buildActions(foot, opts.actions, handle);

    closeBtn.addEventListener('click', function () { handle.close('dismiss'); });
    backdrop.addEventListener('click', function () {
      if (opts.dismissible !== false) handle.close('dismiss');
    });

    host.appendChild(layer);
    sheetStack.push(handle);
    document.body.style.overflow = 'hidden';
    // Next frame so the transform transition runs from the off-screen state.
    requestAnimationFrame(function () {
      layer.classList.add('is-open');
      var target = opts.initialFocus ? sheet.querySelector(opts.initialFocus) : null;
      if (!target) {
        var list = focusables(body).concat(focusables(foot));
        target = list.length ? list[0] : (opts.dismissible === false ? sheet : closeBtn);
      }
      try { target.focus({ preventScroll: true }); } catch (e) { try { target.focus(); } catch (e2) { /* noop */ } }
    });
    return handle;
  }

  function closeAllSheets() {
    sheetStack.slice().reverse().forEach(function (s) { s.close('programmatic'); });
  }

  // Promise<boolean>. Replaces window.confirm: resolves true on `ok`, false on
  // `cancel`, Escape or backdrop.
  function ask(opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      var settled = false;
      var finish = function (v) { if (!settled) { settled = true; resolve(v); } };
      openSheet({
        title: opts.title || 'Are you sure?',
        body: opts.body || opts.message || '',
        onClose: function () { finish(false); },
        actions: [
          { label: opts.ok || 'OK', kind: opts.okKind || 'primary', onClick: function () { finish(true); } },
          { label: opts.cancel || 'Cancel', kind: 'ghost', onClick: function () { finish(false); } },
        ],
      });
    });
  }

  // Promise<string|null>. Replaces window.prompt: null when dismissed; the
  // trimmed text otherwise. `required` keeps the sheet open on empty input.
  function askText(opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      var settled = false;
      var finish = function (v) { if (!settled) { settled = true; resolve(v); } };
      var input = h(opts.multiline === false ? 'input' : 'textarea', {
        class: 'tk-input',
        placeholder: opts.placeholder || '',
        rows: '3',
        'aria-label': opts.title || 'Your answer',
      });
      var err = h('p', { class: 'tk-error', hidden: true, text: opts.requiredMessage || 'Please enter something.' });
      var body = h('div', { class: 'tk-stack' }, [
        opts.message ? h('p', { text: opts.message }) : null,
        h('div', { class: 'tk-field' }, [input]),
        err,
      ]);
      openSheet({
        title: opts.title || '',
        body: body,
        initialFocus: 'textarea, input',
        onClose: function () { finish(null); },
        actions: [
          {
            label: opts.ok || 'Continue', kind: opts.okKind || 'primary',
            onClick: function () {
              var v = (input.value || '').trim();
              if (opts.required && !v) { err.hidden = false; input.focus(); return false; }
              finish(v);
            },
          },
          { label: opts.cancel || 'Cancel', kind: 'ghost', onClick: function () { finish(null); } },
        ],
      });
    });
  }

  // -- Haptics / sound (break timer) ---------------------------------------

  var audioCtx = null;
  // Must be called from a user gesture at least once (Pause tap) or the beep
  // is silently blocked later.
  function armAudio() {
    try {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      if (!audioCtx) audioCtx = new AC();
      if (audioCtx.state === 'suspended') audioCtx.resume();
    } catch (e) { audioCtx = null; }
  }

  function buzz() {
    try { if (navigator.vibrate) navigator.vibrate([200, 80, 200]); } catch (e) { /* noop */ }
    try {
      if (!audioCtx) return;
      var osc = audioCtx.createOscillator();
      var gain = audioCtx.createGain();
      osc.type = 'sine';
      osc.frequency.value = 880;
      gain.gain.value = 0.15;
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      var t = audioCtx.currentTime;
      osc.start(t);
      osc.stop(t + 0.35);
    } catch (e) { /* audio is a nicety */ }
  }

  // -- Formatting ----------------------------------------------------------

  function pad2(n) { return n < 10 ? '0' + n : '' + n; }

  // Frappe datetimes arrive as "YYYY-MM-DD HH:MM:SS" (site-local). Treat them as
  // local wall time, which is what the kiosk's own clock shows.
  function parseDT(s) {
    if (!s) return null;
    if (s instanceof Date) return s;
    var d = new Date(String(s).replace(' ', 'T'));
    return isNaN(d.getTime()) ? null : d;
  }

  function toFrappeDT(d) {
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) +
      ' ' + pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
  }

  function toISODate(d) {
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
  }

  // "YYYY-MM-DD" -> local Date at midnight (new Date('YYYY-MM-DD') would be UTC).
  function parseISODate(s) {
    if (!s) return null;
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s));
    if (!m) return null;
    return new Date(+m[1], +m[2] - 1, +m[3]);
  }

  function hms(seconds) {
    var s = Math.max(0, Math.floor(seconds || 0));
    var hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
    return hh + ':' + pad2(mm) + ':' + pad2(ss);
  }

  function hm(seconds) {
    var s = Math.max(0, Math.floor(seconds || 0));
    var hh = Math.floor(s / 3600), mm = Math.round((s % 3600) / 60);
    if (mm === 60) { hh += 1; mm = 0; }
    if (!hh) return mm + 'm';
    return hh + 'h ' + pad2(mm) + 'm';
  }

  function hoursDec(seconds) { return ((seconds || 0) / 3600).toFixed(1) + ' h'; }

  function clock(d) {
    d = d || new Date();
    try { return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); }
    catch (e) { return pad2(d.getHours()) + ':' + pad2(d.getMinutes()); }
  }

  function timeOf(s) { var d = parseDT(s); return d ? clock(d) : '—'; }

  function dateLabel(s, opts) {
    var d = parseISODate(s) || parseDT(s);
    if (!d) return '';
    try { return d.toLocaleDateString([], opts || { weekday: 'short', month: 'short', day: 'numeric' }); }
    catch (e) { return toISODate(d); }
  }

  function distance(m) {
    if (m == null || isNaN(m)) return '';
    if (m < 950) return Math.round(m) + ' m';
    return (m / 1000).toFixed(1) + ' km';
  }

  function ago(when) {
    var t = 0;
    if (when instanceof Date) t = when.getTime();
    else if (typeof when === 'number') t = when;
    else { var d = parseDT(when); t = d ? d.getTime() : 0; }
    if (!t) return 'never';
    var s = Math.max(0, Math.round((Date.now() - t) / 1000));
    if (s < 60) return s + ' s ago';
    if (s < 3600) return Math.round(s / 60) + ' min ago';
    if (s < 86400) return Math.round(s / 3600) + ' h ago';
    return Math.round(s / 86400) + ' d ago';
  }

  window.KioskUI = {
    escapeHtml: escapeHtml,
    h: h,
    clear: clear,
    skeleton: skeleton,
    toast: toast,
    theme: theme,
    sheet: { open: openSheet, closeAll: closeAllSheets, depth: function () { return sheetStack.length; } },
    ask: ask,
    askText: askText,
    armAudio: armAudio,
    buzz: buzz,
    reducedMotion: reducedMotion,
    isStandalone: isStandalone,
    platform: platform,
    fmt: {
      pad2: pad2, parseDT: parseDT, toFrappeDT: toFrappeDT, toISODate: toISODate, parseISODate: parseISODate,
      hms: hms, hm: hm, hoursDec: hoursDec, clock: clock, timeOf: timeOf, dateLabel: dateLabel,
      distance: distance, ago: ago,
    },
  };

  // The inline head script already applied the stored mode; this call only
  // writes the theme-color meta, which needs the stylesheet to have loaded.
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', syncThemeMeta);
  else syncThemeMeta();
})();
