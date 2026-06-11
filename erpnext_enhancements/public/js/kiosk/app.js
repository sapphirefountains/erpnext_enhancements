/*
 * Time Kiosk — standalone PWA application (UI / clock state machine).
 *
 * Targets: the Time Kiosk PWA front-end (mounts into #kiosk-root).
 * Loaded via: the web page www/kiosk.html — NOT through hooks.py. The page injects
 * a boot payload on `window.KIOSK_BOOT` (employee, settings, current status,
 * csrf_token); this script is loaded together with geo.js and a service worker
 * (/kiosk-sw.js) that handles durable queueing/upload of location points.
 *
 * Self-contained: does NOT depend on the Frappe desk bundle. Talks to whitelisted
 * `erpnext_enhancements.api.time_kiosk.*` endpoints via fetch (+ injected CSRF
 * token) and drives KioskGeo (geo.js) for location tracking, which runs only while
 * clocked in AND active (status "Open").
 *
 * Flow: init() injects the template, caches DOM refs, wires events (incl. the
 * standalone-mode back/forward/refresh bar — see setupNav), configures KioskGeo +
 * warms up the location permission, registers the service worker (versioned per
 * deploy via window.KIOSK_BUILD, with foreground/hourly update checks), seeds
 * the UI from the boot status then confirms via get_current_status. renderState()
 * is the central state machine that switches the card between the idle/clock-in
 * form, the active (Open) view, and the paused (break) view, and starts/stops
 * KioskGeo accordingly. Clock-in/pause/resume/switch/clock-out all post to the
 * log_time endpoint; attachments upload via /api/method/upload_file then link.
 *
 * Maintenance forms: while clocked into a project with an Active Maintenance
 * Contract (or Active form template), the card shows a link to the visit form
 * (get_maintenance_context — open draft or prefilled new record, opened in a
 * new tab so the clock keeps running). Clock-out and cross-project switches
 * warn when no form was submitted during the interval.
 */
(function () {
  'use strict';

  var BOOT = window.KIOSK_BOOT || {};
  var CSRF = window.KIOSK_CSRF || BOOT.csrf_token || '';
  var SETTINGS = BOOT.settings || {};
  // Per-deploy cache-bust token (kiosk.py::get_deploy_version, injected by
  // kiosk.html). Versions the service-worker registration so every deploy
  // rotates the SW cache automatically.
  var BUILD = window.KIOSK_BUILD || '';

  var app = {
    status: null,            // 'Open' | 'Paused' | 'Idle'
    currentInterval: null,
    attachments: [],
    isSwitching: false,
    loading: false,
    maintenance: null,       // { project, ctx } — get_maintenance_context for the active job
    projectOptions: [],      // raw get_kiosk_options projects (with site lat/lng) — re-sorted as fixes arrive
  };

  // -- DOM refs (filled after template injection) --------------------------
  var el = {};

  var TEMPLATE = [
    '<div class="tk-nav" id="tk-nav" hidden>',
    '  <button class="tk-nav-btn" id="tk-nav-back" type="button" aria-label="Go back">&#8249;</button>',
    '  <button class="tk-nav-btn" id="tk-nav-forward" type="button" aria-label="Go forward">&#8250;</button>',
    '  <button class="tk-nav-btn" id="tk-nav-refresh" type="button" aria-label="Refresh">&#8635;</button>',
    '</div>',
    '<div class="tk-header">',
    '  <div class="tk-clock" id="tk-clock">--:--:--</div>',
    '  <p class="tk-status" id="tk-status">Ready to Work</p>',
    '</div>',
    '<div class="tk-card" id="tk-geo-suggest" style="display:none; margin-bottom:14px; text-align:center;">',
    '  <p id="tk-geo-suggest-text" style="margin:0 0 8px;"></p>',
    '  <button class="tk-btn tk-btn-success" id="tk-geo-suggest-btn" style="margin-top:0;">Select This Project</button>',
    '</div>',
    '<div class="tk-card">',
    '  <div class="tk-timer" id="tk-timer">--:--:--</div>',
    '  <div class="tk-active-project" id="tk-active-project" style="display:none;">',
    '    <span id="tk-active-project-name"></span>',
    '  </div>',
    '  <div id="tk-maintenance" style="display:none; text-align:center;">',
    '    <a class="tk-btn tk-btn-outline" id="tk-maintenance-link" target="_blank" rel="noopener"',
    '       style="margin-top:0; display:inline-block;">📋 Maintenance Form</a>',
    '  </div>',
    '  <div id="tk-inputs">',
    '    <div class="tk-field"><label>Project</label><div class="tk-combo" id="tk-project-combo"></div></div>',
    '    <div class="tk-field"><label>Task (optional)</label><div class="tk-combo" id="tk-task-combo"></div></div>',
    '    <div class="tk-field"><label>Activity Type</label><select id="tk-activity"></select></div>',
    '    <div class="tk-field"><label>Note (optional)</label>',
    '      <textarea id="tk-note" rows="3" placeholder="What are you working on?"></textarea></div>',
    '  </div>',
    '  <div id="tk-readonly" style="display:none;">',
    '    <p class="tk-readonly-note" id="tk-readonly-note"></p>',
    '    <p style="text-align:center;"><span class="tk-badge" id="tk-readonly-cat"></span></p>',
    '  </div>',
    '  <div id="tk-attachments" class="tk-attachments" style="display:none;">',
    '    <h6>Attachments</h6>',
    '    <div class="tk-attachment-list" id="tk-attachment-list"></div>',
    '    <div class="tk-row">',
    '      <button class="tk-btn tk-btn-outline" id="tk-add-attach" style="margin-top:0;">Add Files</button>',
    '      <button class="tk-btn tk-btn-outline" id="tk-take-pic" style="margin-top:0;">Take Picture</button>',
    '    </div>',
    '    <input type="file" id="tk-file-input" multiple style="display:none;">',
    '    <input type="file" id="tk-camera-input" accept="image/*" capture="environment" style="display:none;">',
    '  </div>',
    '  <button class="tk-btn tk-btn-success" id="tk-clock-in">Clock In</button>',
    '  <div id="tk-active-actions" style="display:none;">',
    '    <div class="tk-row">',
    '      <button class="tk-btn tk-btn-warning" id="tk-pause" style="margin-top:0;">Pause Break</button>',
    '      <button class="tk-btn tk-btn-info" id="tk-resume" style="margin-top:0; display:none;">Resume Work</button>',
    '      <button class="tk-btn tk-btn-secondary" id="tk-switch" style="margin-top:0;">Switch Task</button>',
    '    </div>',
    '    <button class="tk-btn tk-btn-danger" id="tk-clock-out">Clock Out</button>',
    '  </div>',
    '  <div class="tk-track" id="tk-track">',
    '    <span class="tk-track-dot"></span>',
    '    <span id="tk-track-text">Location tracking off</span>',
    '  </div>',
    '</div>',
    '<div class="tk-card" id="tk-visits" style="display:none; margin-top:14px;">',
    '  <h6 style="margin:0 0 8px;">Today&#39;s Visits</h6>',
    '  <div id="tk-visits-list"></div>',
    '</div>',
    '<a class="tk-btn tk-btn-outline" id="tk-history" href="/app/job-interval" style="margin-top:14px;">View My History</a>',
    '<div class="tk-toasts" id="tk-toasts"></div>',
  ].join('\n');

  // -- Utilities -----------------------------------------------------------
  function $(id) { return document.getElementById(id); }
  function show(node) { if (node) node.style.display = ''; }
  function hide(node) { if (node) node.style.display = 'none'; }
  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function toast(message, kind, ms) {
    var box = $('tk-toasts');
    if (!box) return;
    var t = document.createElement('div');
    t.className = 'tk-toast' + (kind ? ' is-' + kind : '');
    t.textContent = message;
    box.appendChild(t);
    setTimeout(function () {
      t.style.opacity = '0';
      setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 300);
    }, ms || 3500);
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

  // -- Pickers -------------------------------------------------------------
  function fillSelect(node, items, placeholder) {
    node.innerHTML = '';
    var ph = document.createElement('option');
    ph.value = '';
    ph.textContent = placeholder;
    node.appendChild(ph);
    (items || []).forEach(function (it) {
      var o = document.createElement('option');
      o.value = it.value;
      o.textContent = it.label;
      node.appendChild(o);
    });
  }

  // Searchable picker (project/task). A text input filters a dropdown of
  // options by label AND value — so "PRJ-0123" (the docname) finds a project
  // whose displayed title doesn't contain it. Options may carry distance_m
  // (rendered as a badge). Exposes a <select>-like surface: .value get/set,
  // onChange(cb), setOptions(items). The input doubles as the display of the
  // current selection; typing only filters — the value changes when an option
  // is chosen (or cleared), and blur reverts the text to the selection, so
  // the displayed text always matches .value by the time an action button
  // (whose tap blurs the input) reads it.
  function createCombo(container, cfg) {
    cfg = cfg || {};
    var input = document.createElement('input');
    input.type = 'text';
    input.placeholder = cfg.placeholder || '';
    input.autocomplete = 'off';
    input.setAttribute('inputmode', 'search');
    var clearBtn = document.createElement('button');
    clearBtn.type = 'button';
    clearBtn.className = 'tk-combo-clear';
    clearBtn.setAttribute('aria-label', 'Clear');
    clearBtn.innerHTML = '&times;';
    clearBtn.hidden = true;
    var panel = document.createElement('div');
    panel.className = 'tk-combo-panel';
    panel.hidden = true;
    container.appendChild(input);
    container.appendChild(clearBtn);
    container.appendChild(panel);

    var st = { options: [], value: '', typed: false, hi: -1, cbs: [] };

    function find(v) {
      for (var i = 0; i < st.options.length; i++) {
        if (st.options[i].value === v) return st.options[i];
      }
      return null;
    }
    function fire() { st.cbs.forEach(function (cb) { cb(st.value); }); }
    function syncClear() { clearBtn.hidden = !(st.value || input.value); }
    function matches(o, q) {
      return String(o.label || '').toLowerCase().indexOf(q) !== -1 ||
             String(o.value || '').toLowerCase().indexOf(q) !== -1;
    }
    function visible() {
      // Only filter on text the user actually typed — when the input is just
      // displaying the current selection, opening shows the full list.
      var q = st.typed ? input.value.trim().toLowerCase() : '';
      if (!q) return st.options;
      return st.options.filter(function (o) { return matches(o, q); });
    }

    function render() {
      var list = visible();
      panel.innerHTML = '';
      if (!list.length) {
        var empty = document.createElement('div');
        empty.className = 'tk-combo-empty';
        empty.textContent = cfg.emptyText || 'No matches.';
        panel.appendChild(empty);
        return;
      }
      list.forEach(function (o, idx) {
        var row = document.createElement('div');
        row.className = 'tk-combo-option' +
          (idx === st.hi ? ' is-hi' : '') +
          (o.value === st.value ? ' is-selected' : '');
        var txt = document.createElement('div');
        txt.className = 'tk-combo-text';
        var main = document.createElement('div');
        main.className = 'tk-combo-label';
        main.textContent = o.label;
        txt.appendChild(main);
        if (o.value && o.value !== o.label) {
          var sub = document.createElement('div');
          sub.className = 'tk-combo-sub';
          sub.textContent = o.value;
          txt.appendChild(sub);
        }
        row.appendChild(txt);
        if (o.distance_m != null) {
          var dist = document.createElement('span');
          dist.className = 'tk-combo-dist';
          dist.textContent = formatDistance(o.distance_m);
          row.appendChild(dist);
        }
        // mousedown would blur the input (closing the panel before click
        // lands) — suppress it so the tap's click event picks the option.
        row.addEventListener('mousedown', function (ev) { ev.preventDefault(); });
        row.addEventListener('click', function () { choose(o); });
        panel.appendChild(row);
      });
    }

    function openPanel() {
      if (cfg.onOpen) cfg.onOpen();
      st.hi = -1;
      render();
      panel.hidden = false;
    }
    function closePanel() { panel.hidden = true; }
    function revertText() {
      var o = find(st.value);
      input.value = o ? o.label : (st.value || '');
      st.typed = false;
      syncClear();
    }
    function choose(o) {
      st.value = o.value;
      st.typed = false;
      input.value = o.label;
      closePanel();
      syncClear();
      fire();
    }

    input.addEventListener('focus', function () {
      input.select();
      openPanel();
    });
    input.addEventListener('input', function () {
      st.typed = true;
      st.hi = -1;
      if (panel.hidden) panel.hidden = false;
      render();
      syncClear();
    });
    input.addEventListener('blur', function () {
      revertText();
      closePanel();
    });
    input.addEventListener('keydown', function (ev) {
      var list = visible();
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault();
        if (panel.hidden) { openPanel(); return; }
        var step = ev.key === 'ArrowDown' ? 1 : -1;
        st.hi = Math.max(0, Math.min(list.length - 1, st.hi + step));
        render();
        var hiRow = panel.children[st.hi];
        if (hiRow && hiRow.scrollIntoView) hiRow.scrollIntoView({ block: 'nearest' });
      } else if (ev.key === 'Enter') {
        ev.preventDefault();
        if (st.hi >= 0 && list[st.hi]) choose(list[st.hi]);
        else if (list.length === 1) choose(list[0]);
      } else if (ev.key === 'Escape') {
        revertText();
        closePanel();
        input.blur();
      }
    });
    clearBtn.addEventListener('click', function () {
      st.value = '';
      input.value = '';
      st.typed = false;
      closePanel();
      syncClear();
      fire();
    });

    return {
      get value() { return st.value; },
      set value(v) {
        st.value = v || '';
        revertText();
        closePanel();
        fire();
      },
      onChange: function (cb) { st.cbs.push(cb); },
      setOptions: function (items) {
        st.options = items || [];
        st.hi = -1;
        if (st.value && !find(st.value)) {
          // Selection no longer offered (e.g. task list of another project) —
          // drop it silently; callers reload dependents themselves.
          st.value = '';
          if (!st.typed) input.value = '';
          syncClear();
        } else if (st.value && !st.typed) {
          revertText();
        }
        if (!panel.hidden) render();
      },
    };
  }

  function formatDistance(m) {
    if (m < 950) return Math.round(m) + ' m';
    return (m / 1000).toFixed(1) + ' km';
  }

  // Same haversine as geo.js (private there); good enough to rank sites.
  function haversineM(a, b) {
    var R = 6371000;
    var toRad = function (d) { return d * Math.PI / 180; };
    var dLat = toRad(b.lat - a.lat);
    var dLng = toRad(b.lng - a.lng);
    var h = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) *
            Math.sin(dLng / 2) * Math.sin(dLng / 2);
    return 2 * R * Math.asin(Math.sqrt(h));
  }

  // One shared device fix for nearest-first sorting and the geofenced
  // suggestion; refreshed at most every 2 minutes.
  var deviceFix = null; // { lat, lng, t }
  function requestDeviceFix(cb) {
    if (!('geolocation' in navigator)) { if (cb) cb(deviceFix); return; }
    if (deviceFix && Date.now() - deviceFix.t < 120000) { if (cb) cb(deviceFix); return; }
    navigator.geolocation.getCurrentPosition(function (pos) {
      deviceFix = { lat: pos.coords.latitude, lng: pos.coords.longitude, t: Date.now() };
      if (cb) cb(deviceFix);
    }, function () { if (cb) cb(deviceFix); /* stale fix beats none */ },
    { maximumAge: 120000, timeout: 8000 });
  }

  // Nearest site first (projects without coordinates follow, alphabetical).
  function decorateProjects(projects) {
    var items = (projects || []).map(function (p) {
      var o = { value: p.value, label: p.label, distance_m: null };
      if (deviceFix && p.lat != null && p.lng != null) {
        o.distance_m = haversineM(deviceFix, { lat: p.lat, lng: p.lng });
      }
      return o;
    });
    items.sort(function (a, b) {
      var ad = a.distance_m == null ? Infinity : a.distance_m;
      var bd = b.distance_m == null ? Infinity : b.distance_m;
      if (ad !== bd) return ad - bd;
      return String(a.label).localeCompare(String(b.label));
    });
    return items;
  }

  function resortProjects() {
    if (app.projectOptions && app.projectOptions.length) {
      el.project.setOptions(decorateProjects(app.projectOptions));
    }
  }

  function loadOptions() {
    return api('erpnext_enhancements.api.time_kiosk.get_kiosk_options', {}, { method: 'GET' })
      .then(function (opts) {
        opts = opts || { projects: [], activity_types: [] };
        app.projectOptions = opts.projects || [];
        el.project.setOptions(decorateProjects(app.projectOptions));
        fillSelect(el.activity, opts.activity_types, 'Select…');
        el.task.setOptions([]);
        requestDeviceFix(resortProjects);
      })
      .catch(function () { toast('Could not load projects.', 'red'); });
  }

  function loadTasks(project) {
    el.task.setOptions([]);
    if (!project) return;
    api('erpnext_enhancements.api.time_kiosk.get_tasks_for_project',
      { project: project }, { method: 'GET' })
      .then(function (tasks) { el.task.setOptions(tasks); })
      .catch(function () { /* non-fatal */ });
  }

  // -- Status / rendering --------------------------------------------------
  function setLoading(on) {
    app.loading = on;
    ['tk-clock-in', 'tk-pause', 'tk-resume', 'tk-switch', 'tk-clock-out'].forEach(function (id) {
      var b = $(id);
      if (b) b.disabled = on;
    });
  }

  function applyStatus(message) {
    if (message && message.name) {
      app.status = message.status;
      app.currentInterval = message;
      app.attachments = message.attachments || [];
      app.isSwitching = false;
    } else {
      app.status = 'Idle';
      app.currentInterval = null;
      app.attachments = [];
    }
    renderState();
  }

  function fetchStatus() {
    setLoading(true);
    return api('erpnext_enhancements.api.time_kiosk.get_current_status', {}, { method: 'GET' })
      .then(applyStatus)
      .catch(function (e) { toast(humanError(e), 'red'); })
      .then(function () { setLoading(false); });
  }

  function renderState() {
    var ci = app.currentInterval || {};
    var active = (app.status === 'Open' || app.status === 'Paused');

    if (active) {
      el.status.textContent = app.status === 'Open' ? 'Clocked In' : 'On Break (Paused)';

      if (app.isSwitching) {
        show(el.inputs); hide(el.readonly);
        hide(el.clockIn); show(el.activeActions);
        el.switchBtn.textContent = 'Confirm & Switch';
        el.switchBtn.className = 'tk-btn tk-btn-primary';
        el.switchBtn.style.marginTop = '0';
      } else {
        hide(el.inputs); show(el.readonly);
        el.readonlyNote.textContent = ci.description || 'No description provided.';
        el.readonlyCat.textContent = ci.time_category || '';
        el.readonlyCat.style.display = ci.time_category ? '' : 'none';
        hide(el.clockIn); show(el.activeActions);
        el.switchBtn.textContent = 'Switch Task';
        el.switchBtn.className = 'tk-btn tk-btn-secondary';
        el.switchBtn.style.marginTop = '0';
      }

      el.pause.style.display = app.status === 'Open' ? '' : 'none';
      el.resume.style.display = app.status === 'Paused' ? '' : 'none';

      show(el.attachments);
      renderAttachments();
      hide(el.visits);
      hide(el.geoSuggest);

      show(el.activeProject);
      var title = ci.project_title || ci.project || '';
      if (ci.task) title += ' — ' + (ci.task_title || ci.task);
      el.activeProjectName.textContent = title;

      loadMaintenanceContext();

      // Tracking: only while genuinely active (Open), never on break (Paused).
      if (app.status === 'Open' && ci.name) {
        window.KioskGeo.start(ci.name);
      } else {
        window.KioskGeo.stop();
      }
    } else {
      el.status.textContent = BOOT.employee ? 'Ready to Work' : 'No employee linked to your user';
      show(el.inputs); show(el.clockIn);
      hide(el.activeActions); hide(el.readonly);
      hide(el.activeProject); hide(el.attachments);
      hide(el.maintenance);
      app.maintenance = null;
      loadVisitsToday();
      maybeSuggestNearby();
      el.attachmentList.innerHTML = '';
      app.attachments = [];
      el.timer.textContent = '--:--:--';
      if (!BOOT.employee) el.clockIn.disabled = true;
      window.KioskGeo.stop();
    }
  }

  // -- Today's visits + geofenced suggestion (idle screen) -------------------
  var visitsLoadedAt = 0;
  function loadVisitsToday() {
    if (Date.now() - visitsLoadedAt < 60000) return; // renderState re-fires often
    visitsLoadedAt = Date.now();
    api('erpnext_enhancements.api.time_kiosk.get_my_visits_today', {}, { method: 'GET' })
      .then(function (visits) {
        var box = el.visitsList;
        if (!box) return;
        box.innerHTML = '';
        if (!visits || !visits.length) { hide(el.visits); return; }
        visits.forEach(function (v) {
          var label = v.project_title || v.project || '';
          if (v.visit_label) label += ' — ' + v.visit_label;
          else if (v.serial_no) label += ' — ' + v.serial_no;
          var a = document.createElement('a');
          a.className = 'tk-attachment-item';
          a.style.display = 'flex';
          a.style.textDecoration = 'none';
          a.href = v.route;
          a.target = '_blank';
          a.rel = 'noopener';
          a.innerHTML = '<span>📋</span><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' +
            escapeHtml(label) + '</span>';
          box.appendChild(a);
        });
        show(el.visits);
      })
      .catch(function () { hide(el.visits); });
  }

  var geoSuggestChecked = false;
  function maybeSuggestNearby() {
    if (geoSuggestChecked) return;
    geoSuggestChecked = true; // one position fix per page load is plenty
    requestDeviceFix(function (fix) {
      if (!fix) return; // permission denied / unavailable — no suggestion
      resortProjects();
      if (app.status !== 'Idle') return;
      api('erpnext_enhancements.api.time_kiosk.get_nearby_visit',
        { lat: fix.lat, lng: fix.lng }, { method: 'GET' })
        .then(function (site) {
          if (!site || app.status !== 'Idle') return;
          el.geoSuggestText.textContent =
            'You’re near ' + (site.project_title || site.project) +
            ' (~' + site.distance_m + ' m) and a visit is due.';
          el.geoSuggestBtn.onclick = function () {
            el.project.value = site.project; // fires change -> loadTasks
            hide(el.geoSuggest);
            toast('Project selected — ready to clock in.', 'green');
          };
          show(el.geoSuggest);
        })
        .catch(function () { /* best-effort */ });
    });
  }

  // -- Maintenance forms -----------------------------------------------------
  // Projects under an Active Maintenance Contract (or with an Active form
  // template) require a maintenance visit form. While clocked into one, the
  // card shows a link to the form (an open draft when one exists, else a
  // prefilled new record); clock-out / project-switch warn when nothing was
  // submitted during the interval (warnIfMaintenancePending below).
  function loadMaintenanceContext() {
    var ci = app.currentInterval;
    if (!ci || !ci.project) {
      app.maintenance = null;
      renderMaintenance();
      return;
    }
    if (app.maintenance && app.maintenance.project === ci.project) {
      renderMaintenance();
      return;
    }
    app.maintenance = { project: ci.project, ctx: null };
    api('erpnext_enhancements.api.time_kiosk.get_maintenance_context',
      { project: ci.project }, { method: 'GET' })
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
      el.maintenanceLink.textContent = ctx.draft ? '📋 Maintenance Form (Draft)' : '📋 New Maintenance Form';
      show(el.maintenance);
    } else {
      hide(el.maintenance);
    }
  }

  // Before leaving a maintenance project (clock-out or switch), re-check the
  // server: was a form submitted by this user since clock-in? If not, warn —
  // OK = go back and complete it (same semantics as the attachments prompt),
  // Cancel = proceed anyway. Offline or non-maintenance projects proceed
  // silently.
  function warnIfMaintenancePending(actionLabel, proceed) {
    var ci = app.currentInterval;
    if (!ci || !ci.project) { proceed(); return; }
    api('erpnext_enhancements.api.time_kiosk.get_maintenance_context',
      { project: ci.project, since: ci.start_time }, { method: 'GET' })
      .then(function (ctx) {
        if (ctx && ctx.required && !ctx.submitted_since) {
          if (app.maintenance && app.maintenance.project === ci.project) {
            app.maintenance.ctx = ctx;
            renderMaintenance();
          }
          if (window.confirm(
            'No maintenance form has been submitted for this visit. ' +
            'Press OK to go back and complete it, or Cancel to ' + actionLabel + ' anyway.'
          )) return; // OK = stay
        }
        proceed();
      })
      .catch(function () { proceed(); });
  }

  // -- Attachments ---------------------------------------------------------
  function renderAttachments() {
    el.attachmentList.innerHTML = '';
    if (!app.attachments.length) {
      el.attachmentList.innerHTML = '<p class="text-muted" style="margin:0;color:var(--tk-muted);">No attachments yet.</p>';
      return;
    }
    app.attachments.forEach(function (att) {
      var fname = att.file_name || 'Attachment';
      var item = document.createElement('div');
      item.className = 'tk-attachment-item';
      item.innerHTML = '<span>📎</span><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' +
        escapeHtml(fname) + '</span>';
      el.attachmentList.appendChild(item);
    });
  }

  function linkFile(fileName) {
    var ci = app.currentInterval || {};
    api('erpnext_enhancements.api.time_kiosk.link_attachment', {
      file_name: fileName, project: ci.project, task: ci.task || null,
    }).then(function (r) {
      if (r && r.status === 'success') {
        app.attachments.push({ file_name: r.file_name, file_url: r.file_url });
        renderAttachments();
        toast('Attachment added.', 'green');
      }
    }).catch(function (e) { toast(humanError(e), 'red'); });
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

  // -- Actions -------------------------------------------------------------
  function handleAction(action) {
    var project = el.project.value;
    var task = el.task.value;
    var description = el.note.value;
    var category = el.activity.value;

    if ((action === 'Start' || action === 'Switch') && !project) {
      toast('Please select a project.', 'orange');
      return;
    }

    setLoading(true);
    api('erpnext_enhancements.api.time_kiosk.log_time', {
      project: project, task: task, action: action,
      description: description, time_category: category,
    }).then(function (r) {
      if (r && r.status === 'success') {
        toast(r.message, 'green');
        el.note.value = '';
        el.activity.value = '';
        if (action === 'Start') maybeConsent();
        return fetchStatus();
      }
      setLoading(false);
    }).catch(function (e) {
      setLoading(false);
      toast(humanError(e), 'red');
    });
  }

  function promptIfNoAttachments(message, cb) {
    if (app.attachments.length === 0) {
      // OK = go back and add; Cancel = continue without.
      if (!window.confirm(message)) cb();
    } else {
      cb();
    }
  }

  function maybeConsent() {
    if (!SETTINGS.enable_tracking) return;
    try {
      if (localStorage.getItem('tk_consent_shown')) return;
      localStorage.setItem('tk_consent_shown', '1');
    } catch (e) { /* storage may be blocked */ }
    toast('Your location is recorded while you are clocked in and active.', 'orange', 6000);
  }

  // -- Tracking indicator --------------------------------------------------
  var deniedToastShown = false;
  function renderTrack(status) {
    var box = el.track, text = el.trackText;
    if (!box) return;
    box.classList.remove('is-on', 'is-error', 'is-ready');
    if (status === 'on') {
      box.classList.add('is-on');
      text.textContent = 'Location tracking active';
    } else if (status === 'ready') {
      box.classList.add('is-ready');
      text.textContent = 'Location ready';
    } else if (status === 'denied') {
      box.classList.add('is-error');
      text.textContent = 'Location permission denied';
      if (!deniedToastShown) {
        deniedToastShown = true;
        toast('Location access is required while clocked in. Please enable it.', 'orange', 6000);
      }
    } else if (status === 'error') {
      box.classList.add('is-error');
      text.textContent = 'Location unavailable';
    } else {
      text.textContent = 'Location tracking off';
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

  // -- Installed-app navigation ---------------------------------------------
  // An installed PWA running standalone (or iOS "Add to Home Screen") has no
  // browser chrome, so back/forward/refresh are impossible — notably after
  // following the "View My History" link. Render our own bar in that case.
  // In a normal browser tab — or when the browser honors the manifest's
  // minimal-ui display_override and draws its own controls — it stays hidden.
  function isStandaloneDisplay() {
    if (window.navigator.standalone === true) return true; // iOS home-screen app
    return !!(window.matchMedia && (
      window.matchMedia('(display-mode: standalone)').matches ||
      window.matchMedia('(display-mode: fullscreen)').matches
    ));
  }

  function setupNav() {
    var bar = $('tk-nav');
    if (!bar || !isStandaloneDisplay()) return;
    bar.hidden = false;
    $('tk-nav-back').addEventListener('click', function () { window.history.back(); });
    $('tk-nav-forward').addEventListener('click', function () { window.history.forward(); });
    $('tk-nav-refresh').addEventListener('click', function () { window.location.reload(); });
  }

  // -- Clock / timer -------------------------------------------------------
  function tick() {
    el.clock.textContent = new Date().toLocaleTimeString();
    var ci = app.currentInterval;
    if ((app.status === 'Open' || app.status === 'Paused') && ci && ci.start_time) {
      var start = new Date(ci.start_time.replace(' ', 'T')).getTime();
      var now = (app.status === 'Paused' && ci.last_pause_time)
        ? new Date(ci.last_pause_time.replace(' ', 'T')).getTime()
        : Date.now();
      var pausedMs = (ci.total_paused_seconds || 0) * 1000;
      var diff = now - start - pausedMs;
      if (diff >= 0) {
        var h = Math.floor(diff / 3600000);
        var m = Math.floor((diff % 3600000) / 60000);
        var s = Math.floor((diff % 60000) / 1000);
        el.timer.textContent = pad2(h) + ':' + pad2(m) + ':' + pad2(s);
      }
    }
  }
  function pad2(n) { return n < 10 ? '0' + n : '' + n; }

  // -- Wiring --------------------------------------------------------------
  function cacheEls() {
    el.clock = $('tk-clock');
    el.status = $('tk-status');
    el.timer = $('tk-timer');
    el.activeProject = $('tk-active-project');
    el.activeProjectName = $('tk-active-project-name');
    el.maintenance = $('tk-maintenance');
    el.maintenanceLink = $('tk-maintenance-link');
    el.visits = $('tk-visits');
    el.visitsList = $('tk-visits-list');
    el.geoSuggest = $('tk-geo-suggest');
    el.geoSuggestText = $('tk-geo-suggest-text');
    el.geoSuggestBtn = $('tk-geo-suggest-btn');
    el.inputs = $('tk-inputs');
    // Searchable pickers expose a <select>-like surface (.value, onChange).
    el.project = createCombo($('tk-project-combo'), {
      placeholder: 'Search project name or PRJ-#…',
      emptyText: 'No matching projects.',
      onOpen: function () { requestDeviceFix(resortProjects); },
    });
    el.task = createCombo($('tk-task-combo'), {
      placeholder: 'Search task… (optional)',
      emptyText: 'No matching tasks.',
    });
    el.activity = $('tk-activity');
    el.note = $('tk-note');
    el.readonly = $('tk-readonly');
    el.readonlyNote = $('tk-readonly-note');
    el.readonlyCat = $('tk-readonly-cat');
    el.attachments = $('tk-attachments');
    el.attachmentList = $('tk-attachment-list');
    el.clockIn = $('tk-clock-in');
    el.activeActions = $('tk-active-actions');
    el.pause = $('tk-pause');
    el.resume = $('tk-resume');
    el.switchBtn = $('tk-switch');
    el.clockOut = $('tk-clock-out');
    el.track = $('tk-track');
    el.trackText = $('tk-track-text');
  }

  function wire() {
    el.project.onChange(function (project) { loadTasks(project); });
    el.clockIn.addEventListener('click', function () { handleAction('Start'); });
    el.pause.addEventListener('click', function () { handleAction('Pause'); });
    el.resume.addEventListener('click', function () { handleAction('Resume'); });

    el.clockOut.addEventListener('click', function () {
      warnIfMaintenancePending('clock out', function () {
        promptIfNoAttachments(
          'No attachments added. Press OK to go back and add them, or Cancel to clock out anyway.',
          function () { handleAction('Stop'); }
        );
      });
    });

    el.switchBtn.addEventListener('click', function () {
      if (!app.isSwitching) {
        promptIfNoAttachments(
          'No attachments added. Press OK to go back and add them, or Cancel to switch anyway.',
          function () { app.isSwitching = true; renderState(); }
        );
      } else {
        // Only the project being LEFT needs its form; switching tasks within
        // the same project keeps the visit going.
        var ci = app.currentInterval || {};
        var newProject = el.project.value;
        if (newProject && ci.project && newProject !== ci.project) {
          warnIfMaintenancePending('switch projects', function () { handleAction('Switch'); });
        } else {
          handleAction('Switch');
        }
      }
    });

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
    $('tk-camera-input').addEventListener('change', function () {
      Array.prototype.slice.call(this.files).forEach(uploadFile);
      this.value = '';
    });
  }

  // -- Init ----------------------------------------------------------------
  function init() {
    var root = $('kiosk-root');
    root.innerHTML = TEMPLATE;
    root.removeAttribute('aria-busy');
    cacheEls();
    wire();
    setupNav();

    window.KioskGeo.configure(SETTINGS).onStatus(renderTrack);
    // Ask for location permission on visit, so it's granted before clock-in.
    window.KioskGeo.warmup();
    registerServiceWorker();

    setInterval(tick, 1000);
    tick();

    loadOptions();
    // Seed instantly from the server boot payload, then confirm with a fetch.
    applyStatus(BOOT.status);
    fetchStatus();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
