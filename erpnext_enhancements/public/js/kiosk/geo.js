/*
 * Time Kiosk — main-thread geolocation acquisition.
 *
 * Geolocation is ONLY available on the main thread (not in Web/Service Workers),
 * so this module owns watchPosition + a heartbeat timer, applies a movement
 * distance-filter, and hands accepted points to the service worker for durable
 * queueing + upload. Tracking runs only while started (i.e. clocked in AND active).
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
    keep_wake_lock: 0,
  };

  var state = {
    running: false,
    intervalName: null,
    watchId: null,
    heartbeatTimer: null,
    last: null,        // { lat, lng, t }
    wakeLock: null,
    settings: Object.assign({}, DEFAULTS),
    statusCb: null,
    status: 'off',
  };

  function setStatus(s) {
    state.status = s;
    if (typeof state.statusCb === 'function') {
      try { state.statusCb(s); } catch (e) { /* noop */ }
    }
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

  function buildPoint(pos) {
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
      device_agent: navigator.userAgent,
    };
  }

  // Decide whether to record this fix. force=true bypasses the distance/heartbeat
  // gate (used for the heartbeat tick and the foreground catch-up fix).
  function consider(pos, force) {
    var c = pos.coords;
    var minAcc = state.settings.min_accuracy_m;
    if (minAcc && c.accuracy && c.accuracy > minAcc) {
      return; // too imprecise to be useful
    }
    var here = { lat: c.latitude, lng: c.longitude };
    var moved = !state.last || distanceM(state.last, here) >= state.settings.distance_filter_m;
    var stale = !state.last ||
      (Date.now() - state.last.t) >= state.settings.heartbeat_seconds * 1000;

    if (!force && !moved && !stale) return;

    sendToSW({ type: 'enqueue', data: buildPoint(pos) });
    state.last = { lat: here.lat, lng: here.lng, t: Date.now() };
    setStatus('on');
  }

  function onError(err) {
    if (err && err.code === 1) {
      setStatus('denied');
    } else {
      setStatus('error');
    }
  }

  function getOnce(force) {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      function (pos) { consider(pos, force); },
      onError,
      { enableHighAccuracy: !!state.settings.high_accuracy, maximumAge: 10000, timeout: 20000 }
    );
  }

  async function acquireWakeLock() {
    if (!state.settings.keep_wake_lock) return;
    try {
      if ('wakeLock' in navigator) {
        state.wakeLock = await navigator.wakeLock.request('screen');
      }
    } catch (e) { /* user/agent may refuse — non-fatal */ }
  }

  function releaseWakeLock() {
    if (state.wakeLock) {
      try { state.wakeLock.release(); } catch (e) { /* noop */ }
      state.wakeLock = null;
    }
  }

  function onVisibility() {
    if (document.visibilityState === 'visible' && state.running) {
      acquireWakeLock();   // wake locks drop when hidden; re-acquire
      getOnce(true);       // catch-up fix the instant we return to foreground
      sendToSW({ type: 'flush' });
    }
  }

  function onOnline() {
    if (state.running) sendToSW({ type: 'flush' });
  }

  var KioskGeo = {
    configure: function (settings) {
      if (settings) state.settings = Object.assign({}, DEFAULTS, settings);
      return this;
    },

    onStatus: function (cb) { state.statusCb = cb; return this; },

    isRunning: function () { return state.running; },

    getStatus: function () { return state.status; },

    // Surface the browser's location permission prompt up front, on page visit,
    // so access is granted before the first clock-in. We do NOT log anything here
    // (points are only recorded while clocked in) — this just primes the
    // permission and reflects the result in the tracking indicator.
    warmup: function () {
      if (!state.settings.enable_tracking) return;
      if (!navigator.geolocation) { setStatus('error'); return; }

      function ask() {
        navigator.geolocation.getCurrentPosition(
          function () { if (!state.running) setStatus('ready'); },
          function (err) { if (err && err.code === 1) setStatus('denied'); },
          { enableHighAccuracy: false, maximumAge: 600000, timeout: 20000 }
        );
      }

      // Avoid a redundant prompt when the permission is already decided.
      if (navigator.permissions && navigator.permissions.query) {
        navigator.permissions.query({ name: 'geolocation' }).then(function (perm) {
          if (perm.state === 'granted') { if (!state.running) setStatus('ready'); }
          else if (perm.state === 'denied') { setStatus('denied'); }
          else { ask(); }
        }).catch(ask);
      } else {
        ask();
      }
    },

    start: function (intervalName) {
      if (!state.settings.enable_tracking) { setStatus('off'); return; }
      if (!navigator.geolocation) { setStatus('error'); return; }

      // Already tracking this interval — nothing to do.
      if (state.running && state.intervalName === intervalName) return;
      if (state.running) this.stop();

      state.running = true;
      state.intervalName = intervalName || null;
      state.last = null;

      getOnce(true); // immediate first fix

      var hb = Math.max(state.settings.heartbeat_seconds || 300, 30) * 1000;
      state.heartbeatTimer = setInterval(function () { getOnce(true); }, hb);

      state.watchId = navigator.geolocation.watchPosition(
        function (pos) { consider(pos, false); },
        onError,
        { enableHighAccuracy: !!state.settings.high_accuracy, maximumAge: 15000, timeout: 25000 }
      );

      acquireWakeLock();
      setStatus('on');
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
      setStatus('off');
    },
  };

  document.addEventListener('visibilitychange', onVisibility);
  window.addEventListener('online', onOnline);

  window.KioskGeo = KioskGeo;
})();
