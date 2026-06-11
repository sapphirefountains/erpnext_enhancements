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
    '    <div class="tk-field"><label>Project</label><select id="tk-project"></select></div>',
    '    <div class="tk-field"><label>Task (optional)</label><select id="tk-task"></select></div>',
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

  function loadOptions() {
    return api('erpnext_enhancements.api.time_kiosk.get_kiosk_options', {}, { method: 'GET' })
      .then(function (opts) {
        opts = opts || { projects: [], activity_types: [] };
        fillSelect(el.project, opts.projects, 'Select project…');
        fillSelect(el.activity, opts.activity_types, 'Select…');
        fillSelect(el.task, [], 'Select task… (optional)');
      })
      .catch(function () { toast('Could not load projects.', 'red'); });
  }

  function loadTasks(project) {
    fillSelect(el.task, [], 'Select task… (optional)');
    if (!project) return;
    api('erpnext_enhancements.api.time_kiosk.get_tasks_for_project',
      { project: project }, { method: 'GET' })
      .then(function (tasks) { fillSelect(el.task, tasks, 'Select task… (optional)'); })
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
    if (geoSuggestChecked || !('geolocation' in navigator)) return;
    geoSuggestChecked = true; // one position fix per page load is plenty
    navigator.geolocation.getCurrentPosition(function (pos) {
      if (app.status !== 'Idle') return;
      api('erpnext_enhancements.api.time_kiosk.get_nearby_visit',
        { lat: pos.coords.latitude, lng: pos.coords.longitude }, { method: 'GET' })
        .then(function (site) {
          if (!site || app.status !== 'Idle') return;
          el.geoSuggestText.textContent =
            'You’re near ' + (site.project_title || site.project) +
            ' (~' + site.distance_m + ' m) and a visit is due.';
          el.geoSuggestBtn.onclick = function () {
            el.project.value = site.project;
            loadTasks(site.project);
            hide(el.geoSuggest);
            toast('Project selected — ready to clock in.', 'green');
          };
          show(el.geoSuggest);
        })
        .catch(function () { /* best-effort */ });
    }, function () { /* permission denied / unavailable — no suggestion */ },
    { maximumAge: 120000, timeout: 8000 });
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
    el.project = $('tk-project');
    el.task = $('tk-task');
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
    el.project.addEventListener('change', function () { loadTasks(el.project.value); });
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
