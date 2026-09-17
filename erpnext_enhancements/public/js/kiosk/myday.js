/*
 * Time Kiosk — the My Day view (window.KioskViews.myday).
 *
 * Targets: the Time Kiosk PWA front-end. Loaded by www/kiosk.html after ui.js
 * and geo.js, before app.js — NOT through hooks.py. app.js mounts it into
 * #tk-panel-myday with a context object (api, state, pickers; see app.js init).
 *
 * What it shows, for the session employee only (every endpoint derives the
 * employee from the session):
 *   - a 14-day strip from get_my_history — one bar per calendar day, zero days
 *     included, tap to change the day;
 *   - the chosen day from get_my_day — totals, then one row per Job Interval
 *     with its badges (tracking health, off-site start, auto-closed, corrected,
 *     planned break, photos); tapping a row opens a detail sheet;
 *   - "Request a correction" from the detail sheet (Adjust Times / Change
 *     Project / Missed Clock-Out) or "Missed an entry?" for the day (Missed
 *     Entry) → submit_correction_request; the employee's requests from
 *     get_my_correction_requests, cancellable while Requested via
 *     cancel_correction_request.
 *
 * Datetimes cross the wire as Frappe "YYYY-MM-DD HH:MM:SS" (site-local); the
 * datetime-local inputs use "YYYY-MM-DDTHH:MM". KioskUI.fmt converts both ways.
 */
(function () {
  'use strict';

  var UI = window.KioskUI;
  var h = UI.h;
  var fmt = UI.fmt;

  var ctx = null;
  var root = null;
  var el = {};
  var st = { date: null, day: null, history: null, requests: null, loadingDay: false };

  var HEALTH = {
    Good: { cls: 'is-green', text: 'Tracking good' },
    Gaps: { cls: 'is-amber', text: 'Tracking gaps' },
    None: { cls: 'is-red', text: 'No tracking' },
    Off: { cls: '', text: 'Tracking off' },
    Pending: { cls: '', text: 'Tracking pending' },
  };
  var TYPES_WITH_INTERVAL = ['Adjust Times', 'Change Project', 'Missed Clock-Out'];
  var STATUS_CLS = { Requested: 'is-amber', Approved: 'is-green', Declined: 'is-red', Canceled: '' };

  // -- Build ---------------------------------------------------------------
  function mount(container, c) {
    ctx = c;
    root = container;
    el.sub = h('p', { class: 'tk-page-sub', id: 'tk-myday-sub', text: '' });
    el.strip = h('div', { class: 'tk-strip', role: 'group', 'aria-label': 'Last 14 days' }, [UI.skeleton(1, { block: true })]);
    el.tiles = h('div', { class: 'tk-tiles' });
    el.list = h('div', { class: 'tk-list' });
    el.pending = h('span', { class: 'tk-chip is-amber', hidden: true });
    el.requests = h('div', { class: 'tk-list' });
    root.appendChild(h('div', { class: 'tk-page-head' }, [
      h('div', {}, [h('h1', { text: 'My Day' }), el.sub]),
      h('button', { type: 'button', class: 'tk-btn tk-btn-outline', style: { width: 'auto', minHeight: '44px' }, text: 'Today', on: { click: function () { setDate(fmt.toISODate(new Date())); } } }),
    ]));
    root.appendChild(el.strip);
    root.appendChild(h('div', { class: 'tk-card' }, [el.tiles]));
    root.appendChild(h('div', { class: 'tk-card' }, [
      h('div', { class: 'tk-card-head' }, [
        h('p', { class: 'tk-card-title', text: 'Jobs' }),
        h('button', { type: 'button', class: 'tk-btn tk-btn-ghost', style: { width: 'auto', minHeight: '40px', padding: '6px 10px' }, text: 'Missed an entry?', on: { click: function () { openCorrectionSheet(null); } } }),
      ]),
      el.list,
    ]));
    root.appendChild(h('div', { class: 'tk-card' }, [
      h('div', { class: 'tk-card-head' }, [h('p', { class: 'tk-card-title', text: 'My correction requests' }), el.pending]),
      el.requests,
    ]));
  }

  function show() {
    if (!st.date) st.date = fmt.toISODate(new Date());
    refresh();
  }

  function hide() { /* nothing to stop */ }

  function refresh() {
    loadHistory();
    loadDay();
    loadRequests();
  }

  function setDate(d) {
    st.date = d;
    renderStrip();
    loadDay();
  }

  // -- History strip -------------------------------------------------------
  function loadHistory() {
    ctx.api(ctx.API + 'get_my_history', { days: 14 }, { method: 'GET' })
      .then(function (r) { st.history = (r && r.days) || []; renderStrip(); })
      .catch(function () { st.history = []; renderStrip(); });
  }

  function renderStrip() {
    UI.clear(el.strip);
    if (!st.history) { el.strip.appendChild(UI.skeleton(1, { block: true })); return; }
    var days = st.history.slice().reverse(); // API is newest first; show oldest → newest
    var max = 1;
    days.forEach(function (d) { if (d.total_seconds > max) max = d.total_seconds; });
    days.forEach(function (d) {
      var dt = fmt.parseISODate(d.date);
      var pct = d.total_seconds ? Math.max(8, Math.round((d.total_seconds / max) * 100)) : 0;
      var btn = h('button', {
        type: 'button', class: 'tk-day', 'aria-pressed': d.date === st.date ? 'true' : 'false',
        'aria-label': fmt.dateLabel(d.date) + ', ' + fmt.hm(d.total_seconds),
        on: { click: function () { setDate(d.date); } },
      }, [
        h('span', { class: 'tk-day-hours', text: d.total_seconds ? (d.total_seconds / 3600).toFixed(1) : '' }),
        h('span', { class: 'tk-day-bar' }, [h('i', { style: { height: pct + '%' } })]),
        h('span', { text: dt ? dt.toLocaleDateString([], { weekday: 'narrow' }) : '' }),
        h('span', { text: dt ? String(dt.getDate()) : '' }),
      ]);
      el.strip.appendChild(btn);
    });
    // Newest day on the right; start scrolled to it.
    requestAnimationFrame(function () { el.strip.scrollLeft = el.strip.scrollWidth; });
  }

  // -- The day -------------------------------------------------------------
  function loadDay() {
    var want = st.date;
    st.loadingDay = true;
    el.sub.textContent = fmt.dateLabel(want, { weekday: 'long', month: 'long', day: 'numeric' });
    UI.clear(el.tiles); el.tiles.appendChild(UI.skeleton(2, { block: true }));
    UI.clear(el.list); el.list.appendChild(UI.skeleton(4));
    ctx.api(ctx.API + 'get_my_day', { date: want }, { method: 'GET' })
      .then(function (d) {
        if (want !== st.date) return; // the user moved on
        st.day = d || { intervals: [] };
        renderDay();
      })
      .catch(function (e) {
        if (want !== st.date) return;
        UI.clear(el.tiles);
        UI.clear(el.list);
        el.list.appendChild(h('p', { class: 'tk-error', text: ctx.humanError(e) }));
      })
      .then(function () { st.loadingDay = false; });
  }

  function tile(label, value) {
    return h('div', { class: 'tk-tile' }, [h('div', { class: 'tk-tile-label', text: label }), h('div', { class: 'tk-tile-value', text: value })]);
  }

  function renderDay() {
    var d = st.day || {};
    var intervals = d.intervals || [];
    UI.clear(el.tiles);
    el.tiles.appendChild(tile('Worked', fmt.hm(d.total_seconds || 0)));
    el.tiles.appendChild(tile('Jobs', String(intervals.length)));
    el.tiles.appendChild(tile('Sites', String((d.sites || []).length)));
    el.tiles.appendChild(tile('First – last', d.first_start ? fmt.timeOf(d.first_start) + ' – ' + (d.last_end ? fmt.timeOf(d.last_end) : 'now') : '—'));

    UI.clear(el.list);
    if (!intervals.length) {
      el.list.appendChild(h('p', { class: 'tk-empty', text: 'No jobs on this day.' }));
    }
    intervals.forEach(function (iv) { el.list.appendChild(intervalRow(iv)); });

    var pending = d.pending_corrections || 0;
    el.pending.hidden = !pending;
    el.pending.textContent = pending + ' pending';
    if (ctx.setBadge) ctx.setBadge('myday', pending);
  }

  function badges(iv) {
    var out = [];
    var hl = HEALTH[iv.tracking_health];
    if (hl) out.push(h('span', { class: 'tk-chip ' + hl.cls, text: hl.text + (iv.tracking_coverage_pct != null && iv.tracking_health !== 'Off' ? ' · ' + Math.round(iv.tracking_coverage_pct) + '%' : '') }));
    if (iv.offsite_start) out.push(h('span', { class: 'tk-chip is-amber', text: 'Off-site start' }));
    if (iv.auto_closed) out.push(h('span', { class: 'tk-chip is-red', text: 'Auto-closed' }));
    if (iv.corrected) out.push(h('span', { class: 'tk-chip is-accent', text: 'Corrected' }));
    if (iv.planned_break_minutes) out.push(h('span', { class: 'tk-chip', text: 'Break ' + iv.planned_break_minutes + 'm' }));
    if (iv.photo_count) out.push(h('span', { class: 'tk-chip', text: iv.photo_count + (iv.photo_count === 1 ? ' photo' : ' photos') }));
    return out;
  }

  function statusIcon(iv) {
    if (iv.status === 'Open') return h('span', { class: 'tk-row-icon is-green', 'aria-hidden': 'true', text: '▶' });
    if (iv.status === 'Paused') return h('span', { class: 'tk-row-icon is-amber', 'aria-hidden': 'true', text: '‖' });
    return h('span', { class: 'tk-row-icon', 'aria-hidden': 'true', text: '✓' });
  }

  function intervalRow(iv) {
    var when = fmt.timeOf(iv.start_time) + ' – ' + (iv.end_time ? fmt.timeOf(iv.end_time) : 'now');
    var sub = when + (iv.task_title ? ' · ' + iv.task_title : '') + (iv.time_category ? ' · ' + iv.time_category : '');
    return h('button', { type: 'button', class: 'tk-row', on: { click: function () { openDetail(iv); } } }, [
      statusIcon(iv),
      h('div', { class: 'tk-row-body' }, [
        h('div', { class: 'tk-row-main', text: iv.project_title || iv.project || '' }),
        h('div', { class: 'tk-row-sub', text: sub }),
        h('div', { class: 'tk-interval-badges' }, badges(iv)),
      ]),
      h('div', { class: 'tk-row-aside' }, [h('strong', { text: fmt.hm(iv.worked_seconds || 0) })]),
    ]);
  }

  function openDetail(iv) {
    var kv = h('dl', { class: 'tk-kv' });
    function add(k, v, cls) { if (v == null || v === '') return; kv.appendChild(h('div', {}, [h('dt', { text: k }), h('dd', { class: cls || '', text: String(v) })])); }
    add('Project', iv.project_title || iv.project);
    add('Task', iv.task_title || iv.task);
    add('Activity', iv.time_category);
    add('Started', fmt.timeOf(iv.start_time));
    add('Ended', iv.end_time ? fmt.timeOf(iv.end_time) : 'still open');
    add('Worked', fmt.hm(iv.worked_seconds || 0));
    if (iv.paused_seconds) add('On break', fmt.hm(iv.paused_seconds));
    add('Photos', iv.photo_count || 0);
    var hl = HEALTH[iv.tracking_health];
    if (hl) add('Tracking', hl.text + (iv.tracking_coverage_pct != null ? ' (' + Math.round(iv.tracking_coverage_pct) + '% covered)' : ''), hl.cls);
    if (iv.offsite_start) add('Clock-in', 'Off-site', 'is-amber');
    if (iv.auto_closed) add('Closed by', 'the system (no clock-out)', 'is-red');
    if (iv.corrected) add('Corrected', 'Yes', 'is-green');
    add('Status', iv.status);
    add('Reference', iv.name);
    UI.sheet.open({
      title: iv.project_title || 'Job',
      body: h('div', { class: 'tk-stack' }, [kv, h('div', { class: 'tk-chips' }, badges(iv))]),
      actions: [
        { label: 'Request a correction', kind: 'primary', onClick: function () { openCorrectionSheet(iv); } },
        { label: 'Close', kind: 'ghost' },
      ],
    });
  }

  // -- Corrections ---------------------------------------------------------
  function toLocalInput(s) {
    var d = fmt.parseDT(s);
    if (!d) return '';
    return fmt.toISODate(d) + 'T' + fmt.pad2(d.getHours()) + ':' + fmt.pad2(d.getMinutes());
  }

  function fromLocalInput(v) {
    if (!v) return null;
    var d = new Date(v);
    if (isNaN(d.getTime())) return null;
    return fmt.toFrappeDT(d);
  }

  function openCorrectionSheet(iv) {
    var types = iv ? TYPES_WITH_INTERVAL : ['Missed Entry'];
    var typeSel = h('select', { 'aria-label': 'What needs correcting' }, types.map(function (t) { return h('option', { value: t, text: t }); }));
    var start = h('input', { type: 'datetime-local', 'aria-label': 'Start', value: iv ? toLocalInput(iv.start_time) : (st.date + 'T08:00') });
    var end = h('input', { type: 'datetime-local', 'aria-label': 'End', value: iv && iv.end_time ? toLocalInput(iv.end_time) : (iv ? '' : st.date + 'T17:00') });
    var project = { value: null, label: '' };
    var projBtn = h('button', { type: 'button', class: 'tk-pick', 'aria-haspopup': 'dialog' }, [
      h('span', { class: 'tk-pick-text is-placeholder', text: 'Choose the project' }),
      h('span', { class: 'tk-row-chev', 'aria-hidden': 'true', text: '›' }),
    ]);
    projBtn.addEventListener('click', function () {
      ctx.openProjectPicker({ title: 'Which project?', selected: project.value, onPick: function (p) {
        project = { value: p.value, label: p.label };
        var t = projBtn.querySelector('.tk-pick-text');
        t.classList.remove('is-placeholder');
        t.textContent = p.label;
      } });
    });
    var reason = h('textarea', { rows: '3', placeholder: 'What happened?', 'aria-label': 'Reason' });
    var err = h('p', { class: 'tk-error', hidden: true });

    var fStart = h('div', { class: 'tk-field' }, [h('span', { class: 'tk-label', text: 'Start' }), start]);
    var fEnd = h('div', { class: 'tk-field' }, [h('span', { class: 'tk-label', text: 'End' }), end]);
    var fProject = h('div', { class: 'tk-field' }, [h('span', { class: 'tk-label', text: 'Project' }), projBtn]);

    function applyType() {
      var t = typeSel.value;
      fStart.hidden = !(t === 'Adjust Times' || t === 'Missed Entry');
      fEnd.hidden = !(t === 'Adjust Times' || t === 'Missed Clock-Out' || t === 'Missed Entry');
      fProject.hidden = !(t === 'Change Project' || t === 'Missed Entry');
    }
    typeSel.addEventListener('change', applyType);
    applyType();

    function validate() {
      var t = typeSel.value;
      if (!(reason.value || '').trim()) return 'Please say why.';
      if ((t === 'Adjust Times' || t === 'Missed Entry') && !fromLocalInput(start.value)) return 'A start time is needed.';
      if ((t === 'Adjust Times' || t === 'Missed Clock-Out' || t === 'Missed Entry') && !fromLocalInput(end.value)) return 'An end time is needed.';
      if ((t === 'Change Project' || t === 'Missed Entry') && !project.value) return 'Choose the project.';
      if (fromLocalInput(start.value) && fromLocalInput(end.value) && !fStart.hidden && !fEnd.hidden && fromLocalInput(end.value) <= fromLocalInput(start.value)) return 'The end must be after the start.';
      return null;
    }

    UI.sheet.open({
      title: iv ? 'Request a correction' : 'Report a missed entry',
      body: h('div', { class: 'tk-stack' }, [
        iv ? h('p', { class: 'tk-note', text: (iv.project_title || iv.project) + ' · ' + fmt.timeOf(iv.start_time) + ' – ' + (iv.end_time ? fmt.timeOf(iv.end_time) : 'now') }) : null,
        h('div', { class: 'tk-field' }, [h('span', { class: 'tk-label', text: 'What needs correcting' }), typeSel]),
        fStart, fEnd, fProject,
        h('div', { class: 'tk-field' }, [h('span', { class: 'tk-label', text: 'Reason' }), reason]),
        err,
        h('p', { class: 'tk-note', text: 'Your supervisor reviews the request. Nothing changes until it is approved.' }),
      ]),
      actions: [
        { label: 'Send request', kind: 'primary', onClick: function (handle, btn) {
          var problem = validate();
          if (problem) { err.hidden = false; err.textContent = problem; return false; }
          btn.disabled = true;
          var t = typeSel.value;
          ctx.api(ctx.API + 'submit_correction_request', {
            request_type: t,
            reason: (reason.value || '').trim(),
            job_interval: iv ? iv.name : null,
            proposed_start: !fStart.hidden ? fromLocalInput(start.value) : null,
            proposed_end: !fEnd.hidden ? fromLocalInput(end.value) : null,
            proposed_project: !fProject.hidden ? project.value : null,
          }).then(function () {
            UI.toast('Correction request sent.', 'green');
            handle.close('action');
            loadRequests();
            loadDay();
          }).catch(function (e) {
            btn.disabled = false;
            err.hidden = false;
            err.textContent = ctx.humanError(e);
          });
          return false; // we close on success ourselves
        } },
        { label: 'Cancel', kind: 'ghost' },
      ],
    });
  }

  function loadRequests() {
    ctx.api(ctx.API + 'get_my_correction_requests', { limit: 20 }, { method: 'GET' })
      .then(function (list) { st.requests = list || []; renderRequests(); })
      .catch(function () { st.requests = []; renderRequests(); });
  }

  function renderRequests() {
    UI.clear(el.requests);
    if (!st.requests || !st.requests.length) {
      el.requests.appendChild(h('p', { class: 'tk-empty', text: 'No correction requests yet.' }));
      return;
    }
    st.requests.forEach(function (r) {
      var sub = (r.reason || '') + (r.review_note ? ' — ' + r.review_note : '');
      var when = r.requested_on ? fmt.dateLabel(r.requested_on) + ' ' + fmt.timeOf(r.requested_on) : '';
      var row = h('div', { class: 'tk-row is-static' }, [
        h('div', { class: 'tk-row-body' }, [
          h('div', { class: 'tk-row-main' }, [r.request_type + ' ', h('span', { class: 'tk-chip ' + (STATUS_CLS[r.status] || ''), text: r.status })]),
          h('div', { class: 'tk-row-sub is-wrap', text: sub }),
          h('div', { class: 'tk-row-sub', text: when + (r.job_interval ? ' · ' + r.job_interval : '') }),
        ]),
        r.status === 'Requested' ? h('button', {
          type: 'button', class: 'tk-btn tk-btn-danger-ghost', style: { width: 'auto', minHeight: '44px', padding: '6px 10px' }, text: 'Cancel',
          on: { click: function () { cancelRequest(r); } },
        }) : null,
      ]);
      el.requests.appendChild(row);
    });
  }

  function cancelRequest(r) {
    UI.ask({ title: 'Cancel this request?', message: r.request_type + ' — ' + (r.reason || ''), ok: 'Cancel the request', okKind: 'stop', cancel: 'Keep it' })
      .then(function (yes) {
        if (!yes) return;
        return ctx.api(ctx.API + 'cancel_correction_request', { name: r.name })
          .then(function () { UI.toast('Request canceled.', 'green'); loadRequests(); loadDay(); })
          .catch(function (e) { UI.toast(ctx.humanError(e), 'red'); });
      });
  }

  window.KioskViews = window.KioskViews || {};
  window.KioskViews.myday = { mount: mount, show: show, hide: hide, refresh: refresh };
})();
