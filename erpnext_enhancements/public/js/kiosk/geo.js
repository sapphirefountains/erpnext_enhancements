/*
 * Time Kiosk — main-thread geolocation acquisition.
 *
 * Targets: the Time Kiosk PWA front-end (used by app.js and settings.js).
 * Loaded via: www/kiosk.html after ui.js — NOT through hooks.py.
 *
 * Geolocation is ONLY available on the main thread (not in Web/Service Workers),
 * so this module owns watchPosition + a heartbeat timer, applies a movement
 * distance-filter, and hands accepted points to the service worker for durable
 * queueing + upload. Tracking runs only while started (i.e. clocked in AND active).
 *
 * Sampling model:
 *  - warmup(): on page visit, prime the browser location permission (and reflect
 *    it in the indicator) WITHOUT recording anything, so it's granted before the
 *    first clock-in.
 *  - start(intervalName): take one immediate fix, then run BOTH a continuous
 *    watchPosition stream AND a heartbeat setInterval. Each candidate fix goes
 *    through consider(): it is dropped if accuracy is worse than min_accuracy_m,
 *    and otherwise recorded only if the device moved >= distance_filter_m from the
 *    last recorded point OR the heartbeat interval has elapsed (heartbeat ticks and
 *    the foreground catch-up fix pass force=true to bypass that gate). Accepted
 *    points are posted to the service worker ('enqueue') which owns batching/upload.
 *    Every queued point carries `fix_source`: 'Watch' (the stream), 'Heartbeat'
 *    (the timer) or 'Catch-up' (the fix taken on returning to the foreground or
 *    regaining connectivity) — the server stores it on Time Kiosk Log and the
 *    Location Timeline reads it.
 *  - anchorFix(): the clock-event fix. Clock In / Switch / Clock Out want ONE good
 *    high-accuracy position rather than whatever the stream last saw: up to three
 *    getCurrentPosition attempts inside ~15 s, resolving early once the target
 *    accuracy is reached and otherwise with the best fix seen. It never rejects —
 *    a clock-out must not fail because GPS is slow — so a caller gets `null` and
 *    proceeds without coordinates. The anchor is sent to log_time as lat/lng/
 *    accuracy; it is not queued as a point (the interval may not exist yet).
 *  - On returning to the foreground (visibilitychange) or regaining connectivity
 *    (online), it re-acquires the wake lock, grabs a catch-up fix, and flushes the
 *    SW queue. stop() clears the watch + heartbeat and flushes any remainder.
 *
 * Status vocabulary (onStatus callback + getStatus + getDiagnostics().status):
 *   off          tracking disabled in settings, or not clocked in yet
 *   ready        permission granted, not tracking (idle or on break)
 *   on           tracking, fixes flowing
 *   denied       the user refused location permission
 *   unavailable  no geolocation API, or the OS could not produce a fix
 *   insecure     not a secure context (plain http) — the API is withheld
 *   hidden       tracking, but the app is in the background (the OS throttles
 *                or suspends GPS; the catch-up fix on return fills the gap)
 *
 * Wake lock: requested whenever tracking starts and RE-REQUESTED on every
 * return to the foreground (the browser releases it the moment the page hides).
 * Time Kiosk Settings.keep_wake_lock defaults on server-side since v1.480.0.
 *
 * Exposes window.KioskGeo.
 */
(function () {
  'use strict';

  var DEFAULTS = {
    enable_tracking: 1,
    distance_filter_m: 25,
    heartbeat_seconds: 300,
    high_accuracy: 0,
    min_accuracy_m: 100,
    keep_wake_lock: 1,
    anchor_accuracy_m: 50,
  };

  var state = {
    running: false,
    intervalName: null,
    watchId: null,
    heartbeatTimer: null,
    last: null,          // last RECORDED point { lat, lng, t }
    lastFix: null,       // last fix SEEN by anything here { lat, lng, accuracy, t }
    wakeLock: null,
    settings: Object.assign({}, DEFAULTS),
    statusCb: null,
    status: 'off',
    permission: 'unknown', // granted | denied | prompt | unknown
  };

  function setStatus(s) {
    state.status = s;
    if (typeof state.statusCb === 'function') {
      try { state.statusCb(s); } catch (e) { /* noop */ }
    }
  }

  function secure() {
    return window.isSecureContext !== false; // undefined on very old engines → assume ok
  }

  function toRad(d) { return (d * Math.PI) / 180; }

  // Distance between two lat/lng in meters (haversine).
  function distanceM(a, b) {
    var R = 6371000;
    var dLat = toRad(b.lat - a.lat);
    var dLng = toRad(b.lng - a.lng);
    var lat1 = toRad(a.lat);
    var lat2 = toRad(b.lat);
    var h = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) * Math.sin(dLng / 2);
    return 2 * R * Math.asin(Math.sqrt(h));
  }

  function pad(n) { return n < 10 ? '0' + n : '' + n; }

  // Local "YYYY-MM-DD HH:MM:SS" — matches what the Frappe backend expects.
  function nowLocal() {
    var d = new Date();
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
      ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }

  function uuid() {
    if (self.crypto && self.crypto.randomUUID) return self.crypto.randomUUID();
    return 'p-' + Date.now() + '-' + Math.random().toString(16).slice(2);
  }

  function sendToSW(message) {
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker.ready.then(function (reg) {
      var target = reg.active || navigator.serviceWorker.controller;
      if (target) target.postMessage(message);
    }).catch(function () { /* noop */ });
  }

  function rememberFix(pos) {
    var c = pos.coords;
    state.lastFix = {
      lat: c.latitude,
      lng: c.longitude,
      accuracy: c.accuracy != null ? c.accuracy : null,
      t: Date.now(),
    };
  }

  function buildPoint(pos, source) {
    var c = pos.coords;
    return {
      client_id: uuid(),
      job_interval: state.intervalName || null,
      timestamp: nowLocal(),
      latitude: c.latitude,
      longitude: c.longitude,
      accuracy: c.accuracy != null ? c.accuracy : null,
      speed: c.speed != null ? c.speed : null,
      heading: c.heading != null ? c.heading : null,
      altitude: c.altitude != null ? c.altitude : null,
      log_status: navigator.onLine ? 'Success' : 'Offline Sync',
      fix_source: source || 'Watch',
      device_agent: navigator.userAgent,
    };
  }

  // Decide whether to record this fix. force=true bypasses the distance/heartbeat
  // gate (used for the heartbeat tick and the foreground catch-up fix).
  function consider(pos, force, source) {
    rememberFix(pos);
    var c = pos.coords;
    var minAcc = state.settings.min_accuracy_m;
    if (minAcc && c.accuracy && c.accuracy > minAcc) {
      // Too imprecise to be useful. (The server may keep it as Low Accuracy when
      // keep_low_accuracy_fixes is on — but only points we send reach it, and a
      // fix worse than the threshold is not worth the battery of an upload.)
      return;
    }
    var here = { lat: c.latitude, lng: c.longitude };
    var moved = !state.last || distanceM(state.last, here) >= state.settings.distance_filter_m;
    var stale = !state.last ||
      (Date.now() - state.last.t) >= state.settings.heartbeat_seconds * 1000;

    if (!force && !moved && !stale) return;

    sendToSW({ type: 'enqueue', data: buildPoint(pos, source) });
    state.last = { lat: here.lat, lng: here.lng, t: Date.now() };
    if (!document.hidden) setStatus('on');
  }

  function onError(err) {
    if (err && err.code === 1) {
      state.permission = 'denied';
      setStatus('denied');
    } else {
      setStatus('unavailable');
    }
  }

  function getOnce(force, source) {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      function (pos) { consider(pos, force, source); },
      onError,
      { enableHighAccuracy: !!state.settings.high_accuracy, maximumAge: 10000, timeout: 20000 }
    );
  }

  function acquireWakeLock() {
    if (!state.settings.keep_wake_lock) return;
    if (!('wakeLock' in navigator) || document.hidden) return;
    try {
      navigator.wakeLock.request('screen').then(function (lock) {
        state.wakeLock = lock;
        // The browser releases the lock itself when the page hides; forget it so
        // the next foreground return requests a fresh one.
        try { lock.addEventListener('release', function () { if (state.wakeLock === lock) state.wakeLock = null; }); } catch (e) { /* noop */ }
      }).catch(function () { /* user/agent may refuse — non-fatal */ });
    } catch (e) { /* noop */ }
  }

  function releaseWakeLock() {
    if (state.wakeLock) {
      try { state.wakeLock.release(); } catch (e) { /* noop */ }
      state.wakeLock = null;
    }
  }

  function onVisibility() {
    if (document.visibilityState === 'visible') {
      if (state.running) {
        acquireWakeLock();              // wake locks drop when hidden; re-acquire
        getOnce(true, 'Catch-up');      // catch-up fix the instant we return
        sendToSW({ type: 'flush' });
        setStatus('on');
      }
    } else if (state.running) {
      setStatus('hidden');
    }
  }

  function onOnline() {
    if (state.running) {
      getOnce(true, 'Catch-up');
      sendToSW({ type: 'flush' });
    }
  }

  function watchPermission() {
    if (!(navigator.permissions && navigator.permissions.query)) return Promise.resolve('unknown');
    return navigator.permissions.query({ name: 'geolocation' }).then(function (perm) {
      state.permission = perm.state || 'unknown';
      try {
        perm.onchange = function () {
          state.permission = perm.state || 'unknown';
          if (state.running) return;
          if (perm.state === 'denied') setStatus('denied');
          else if (perm.state === 'granted' && state.settings.enable_tracking) setStatus('ready');
        };
      } catch (e) { /* noop */ }
      return state.permission;
    }).catch(function () { return 'unknown'; });
  }

  var KioskGeo = {
    configure: function (settings) {
      if (settings) state.settings = Object.assign({}, DEFAULTS, settings);
      return this;
    },

    onStatus: function (cb) { state.statusCb = cb; return this; },

    isRunning: function () { return state.running; },

    getStatus: function () { return state.status; },

    // The most recent fix anything in this module has seen (warmup, stream,
    // heartbeat, anchor) — the picker's "Nearest" group and the off-site check
    // read it rather than asking the OS again.
    lastFix: function () { return state.lastFix; },

    distanceM: distanceM,

    // Surface the browser's location permission prompt up front, on page visit,
    // so access is granted before the first clock-in. We do NOT log anything here
    // (points are only recorded while clocked in) — this just primes the
    // permission and reflects the result in the tracking indicator.
    warmup: function () {
      if (!state.settings.enable_tracking) { setStatus('off'); return; }
      if (!secure()) { setStatus('insecure'); return; }
      if (!navigator.geolocation) { setStatus('unavailable'); return; }

      function ask() {
        navigator.geolocation.getCurrentPosition(
          function (pos) {
            rememberFix(pos);
            state.permission = 'granted';
            if (!state.running) setStatus('ready');
          },
          function (err) {
            if (err && err.code === 1) { state.permission = 'denied'; setStatus('denied'); }
            else if (!state.running) setStatus('unavailable');
          },
          { enableHighAccuracy: false, maximumAge: 600000, timeout: 20000 }
        );
      }

      // Avoid a redundant prompt when the permission is already decided.
      watchPermission().then(function (p) {
        if (p === 'granted') { if (!state.running) setStatus('ready'); ask(); } // ask() = warm the fix, no prompt
        else if (p === 'denied') { setStatus('denied'); }
        else { ask(); }
      });
    },

    /**
     * One good fix for a clock event. Resolves { lat, lng, accuracy } or null;
     * never rejects. High accuracy, `attempts` tries (default 3) inside roughly
     * `timeoutMs` total (default 15 s), returning early once a fix at or under
     * `targetAccuracy` metres (default settings.anchor_accuracy_m) arrives, else
     * the best one seen.
     */
    anchorFix: function (opts) {
      opts = opts || {};
      var target = opts.targetAccuracy || state.settings.anchor_accuracy_m || 50;
      var attempts = Math.max(1, opts.attempts || 3);
      var total = opts.timeoutMs || 15000;
      var perTry = Math.max(2000, Math.floor(total / attempts));
      if (!navigator.geolocation || !secure()) return Promise.resolve(null);

      return new Promise(function (resolve) {
        var best = null;
        var done = false;
        var n = 0;
        var deadline = setTimeout(function () { finish(); }, total + 1000);

        function finish() {
          if (done) return;
          done = true;
          clearTimeout(deadline);
          resolve(best ? { lat: best.lat, lng: best.lng, accuracy: best.accuracy } : null);
        }

        function attempt() {
          if (done) return;
          n += 1;
          navigator.geolocation.getCurrentPosition(
            function (pos) {
              rememberFix(pos);
              state.permission = 'granted';
              var c = pos.coords;
              var acc = c.accuracy != null ? c.accuracy : 99999;
              if (!best || acc < best.accuracy) best = { lat: c.latitude, lng: c.longitude, accuracy: acc };
              if (acc <= target || n >= attempts) finish();
              else attempt();
            },
            function (err) {
              if (err && err.code === 1) { state.permission = 'denied'; if (!state.running) setStatus('denied'); finish(); return; }
              if (n >= attempts) finish(); else attempt();
            },
            { enableHighAccuracy: true, maximumAge: 0, timeout: perTry }
          );
        }
        attempt();
      });
    },

    start: function (intervalName) {
      if (!state.settings.enable_tracking) { setStatus('off'); return; }
      if (!secure()) { setStatus('insecure'); return; }
      if (!navigator.geolocation) { setStatus('unavailable'); return; }

      // Already tracking this interval — nothing to do.
      if (state.running && state.intervalName === intervalName) return;
      if (state.running) this.stop();

      state.running = true;
      state.intervalName = intervalName || null;
      state.last = null;

      getOnce(true, 'Heartbeat'); // immediate first fix

      var hb = Math.max(state.settings.heartbeat_seconds || 300, 30) * 1000;
      state.heartbeatTimer = setInterval(function () { getOnce(true, 'Heartbeat'); }, hb);

      state.watchId = navigator.geolocation.watchPosition(
        function (pos) { consider(pos, false, 'Watch'); },
        onError,
        { enableHighAccuracy: !!state.settings.high_accuracy, maximumAge: 15000, timeout: 25000 }
      );

      acquireWakeLock();
      setStatus(document.hidden ? 'hidden' : 'on');
    },

    stop: function () {
      if (!state.running) return;
      state.running = false;
      state.intervalName = null;
      state.last = null;
      if (state.watchId != null && navigator.geolocation) {
        navigator.geolocation.clearWatch(state.watchId);
      }
      state.watchId = null;
      if (state.heartbeatTimer) clearInterval(state.heartbeatTimer);
      state.heartbeatTimer = null;
      releaseWakeLock();
      sendToSW({ type: 'flush' }); // push whatever is still queued
      if (!state.settings.enable_tracking) setStatus('off');
      else if (state.permission === 'denied') setStatus('denied');
      else if (!secure()) setStatus('insecure');
      else setStatus(state.permission === 'granted' ? 'ready' : 'off');
    },

    // Ask the service worker how many points are still queued in IndexedDB.
    // Resolves a number, or null when there is no worker / no answer in 1.5 s.
    queuedCount: function () {
      if (!('serviceWorker' in navigator) || typeof MessageChannel === 'undefined') return Promise.resolve(null);
      return new Promise(function (resolve) {
        var settled = false;
        var timer = setTimeout(function () { if (!settled) { settled = true; resolve(null); } }, 1500);
        navigator.serviceWorker.ready.then(function (reg) {
          var target = reg.active || navigator.serviceWorker.controller;
          if (!target) { clearTimeout(timer); if (!settled) { settled = true; resolve(null); } return; }
          var ch = new MessageChannel();
          ch.port1.onmessage = function (ev) {
            clearTimeout(timer);
            if (!settled) { settled = true; resolve(ev.data && typeof ev.data.queued === 'number' ? ev.data.queued : null); }
          };
          target.postMessage({ type: 'stats' }, [ch.port2]);
        }).catch(function () { clearTimeout(timer); if (!settled) { settled = true; resolve(null); } });
      });
    },

    // Snapshot for the Settings diagnostics panel.
    getDiagnostics: function () {
      return {
        status: state.status,
        permission: state.permission,
        lastFixAt: state.lastFix ? state.lastFix.t : null,
        lastAccuracy: state.lastFix ? state.lastFix.accuracy : null,
        wakeLock: !!state.wakeLock,
        wakeLockSupported: 'wakeLock' in navigator,
        wakeLockWanted: !!state.settings.keep_wake_lock,
        secure: secure(),
        standalone: window.KioskUI ? window.KioskUI.isStandalone() : false,
        online: navigator.onLine !== false,
        running: state.running,
        enabled: !!state.settings.enable_tracking,
      };
    },
  };

  document.addEventListener('visibilitychange', onVisibility);
  window.addEventListener('online', onOnline);

  window.KioskGeo = KioskGeo;
})();
