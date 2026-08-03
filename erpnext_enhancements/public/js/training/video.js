// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/*
 * TR.Video — the attention sampler behind every video block in the learner player.
 *
 * This file is where the module's compliance claim is either earned or quietly
 * faked, so the design is stated up front:
 *
 *   Credit is granted by comparing TWO CLOCKS. A 250ms interval measures wall
 *   time (performance.now) and media time (video.currentTime) and credits the
 *   media span only when the media advance is consistent with dtWall * rate.
 *   A forward seek, a 10x playbackRate, or `video.currentTime = 3600` typed into
 *   the console all make media outrun wall and therefore credit NOTHING.
 *
 * Deliberately NOT built on `timeupdate` deltas. `timeupdate` fires during
 * seeking, its cadence varies by browser and by tab state, and
 * `video.dispatchEvent(new Event("timeupdate"))` is one console line. Anyone
 * "simplifying" this back to timeupdate deltas is removing the measurement,
 * not the ceremony.
 *
 * Backwards seeking (rewatching) credits nothing new and is not punished — the
 * per-second bitmap makes rewatching naturally idempotent.
 *
 * The number on screen is the SERVER's coverage, never the local estimate. The
 * local bitmap exists to decide what to send and when to beat; the learner is
 * shown the figure that will actually be graded, so "it said 82%" and "the
 * server says 61%" can never be different conversations.
 *
 * No frappe.* anything: this runs for Website Users with desk_access = 0, where
 * the desk bundle does not exist. All I/O goes through the injected transport
 * (mediaUrl / heartbeat / openCheckpoint / answerCheckpoint) — the same seam the
 * Phase-3 builder swaps for a preview transport.
 *
 * Mounted by TR.Player; owns everything inside the wrapper element it is given.
 */
(function () {
  "use strict";

  var TR = (window.TR = window.TR || {});

  // -- Constants -----------------------------------------------------------

  var TICK_MS = 250;

  // A tick that took much longer than TICK_MS cannot vouch for the interval it
  // spans: background tabs get throttled to ~1Hz and mobile Safari stops timers
  // outright. On the tick after such a gap both dtWall and dtMedia are large and
  // *consistent*, so the two-clock check would happily credit minutes the
  // learner never saw. Any oversized tick rebaselines and credits nothing.
  var MAX_TICK_SECONDS = 1.5;

  // Slop for real jank (GC pause, decoder hiccup): media may legitimately run a
  // little ahead of the sampler's own scheduling error.
  var JANK_SLOP = 1.6;
  var JANK_FLOOR_SECONDS = 0.4;

  // The client cap is DERIVED from the server, not chosen: progress.clamp_new_seconds
  // truncates claimed seconds to elapsed * 1.25. Allowing 2x here would mean every
  // learner using the speed menu gets half their watch time silently discarded and
  // an integrity flag for their trouble. Cap where the server clamps.
  var MAX_PLAYBACK_RATE = 1.25;

  // Beat every N seconds of CREDITED playback — not wall time. A paused video
  // must not beat, or "watched" drifts toward "left open".
  var DEFAULT_BEAT_SECONDS = 10;
  var MIN_BEAT_SECONDS = 5;
  var MAX_BEAT_SECONDS = 120;

  // Transient focus loss (clicking the scrubber, an OS notification stealing
  // focus) is not "walked away". Only sustained blur stops credit.
  var BLUR_GRACE_MS = 2000;

  var ON_SCREEN_RATIO = 0.5;
  var CHECKPOINT_TOLERANCE = 0.75;
  var MAX_QUEUED_BEATS = 60;

  // How many times one beat may be refused before it is abandoned. Generous
  // enough to ride out an outage, finite so a beat the server will NEVER accept
  // cannot sit at the head of the queue forever holding up everything behind it.
  var MAX_BEAT_ATTEMPTS = 8;

  // The queue drains on credited playback; this is what drains it when there is
  // none — a paused tab, or a backlog left over from an outage. Backs off to a
  // minute so a server that is down is not hammered by every open lesson.
  var RETRY_BASE_MS = 4000;
  var RETRY_MAX_MS = 60000;

  // Consecutive delivery failures before the learner is told. Two is past a
  // one-off blip and well short of nagging.
  var STUCK_AFTER_FAILURES = 2;

  // How long a drain may be in flight before the latch is considered stranded
  // and a new flush is allowed past it. Longer than the transport's own 20s
  // deadline, so a well-behaved transport always releases the latch itself and
  // this only ever fires for one that does not.
  var FLUSH_STUCK_MS = 45000;

  var MAX_SOURCE_RECOVERIES = 3;
  var CHANNEL_NAME = "training-video";

  // -- Utilities -----------------------------------------------------------

  function nowMs() {
    return window.performance && performance.now ? performance.now() : Date.now();
  }

  function cint(v) {
    var n = parseInt(v, 10);
    return isNaN(n) ? 0 : n;
  }

  function flt(v) {
    var n = parseFloat(v);
    return isNaN(n) ? 0 : n;
  }

  function clamp(n, lo, hi) {
    return n < lo ? lo : n > hi ? hi : n;
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    // textContent, never innerHTML: question text, options and explanations are
    // author-entered and arrive from the server as plain Small Text.
    if (text != null) node.textContent = String(text);
    return node;
  }

  function randomId() {
    return Math.random().toString(36).slice(2) + Date.now().toString(36);
  }

  // Structural CSS only, injected once. The module stylesheet owns the looks, but
  // the scrim's position/z-index is load-bearing anti-skip machinery — an overlay
  // that fails to cover the video because a stylesheet did not load is not an
  // overlay, so those few rules travel with the code that depends on them.
  var STYLE_ID = "tr-video-structural-css";
  var STRUCTURAL_CSS = [
    ".tr-video{position:relative;display:block;width:100%;background:#000;}",
    ".tr-video-el{display:block;width:100%;height:auto;}",
    ".tr-video-scrim,.tr-video-takeover{position:absolute;inset:0;z-index:20;display:flex;",
    "  align-items:center;justify-content:center;padding:16px;overflow:auto;",
    "  background:rgba(0,0,0,.86);color:#fff;}",
    ".tr-video-scrim[hidden],.tr-video-takeover[hidden]{display:none;}",
    ".tr-video-panel{max-width:44rem;width:100%;}",
    ".tr-video-options{list-style:none;margin:12px 0;padding:0;}",
    ".tr-video-option{display:flex;gap:8px;align-items:flex-start;padding:6px 0;cursor:pointer;}",
    ".tr-video-meter{display:flex;align-items:center;gap:8px;padding:6px 8px;",
    "  background:rgba(0,0,0,.06);font-size:12px;}",
    ".tr-video-meter-track{flex:1;height:6px;border-radius:3px;background:rgba(0,0,0,.15);}",
    ".tr-video-meter-fill{height:100%;width:0;border-radius:3px;background:#2b8a3e;transition:width .3s;}",
    ".tr-video-notice{position:absolute;left:50%;bottom:12%;transform:translateX(-50%);z-index:30;",
    "  max-width:80%;padding:8px 12px;border-radius:6px;background:rgba(0,0,0,.82);color:#fff;",
    "  font-size:13px;text-align:center;}",
    ".tr-video-notice[hidden]{display:none;}",
    ".tr-video:fullscreen{width:100vw;height:100vh;}",
    ".tr-video:fullscreen .tr-video-el{width:100%;height:100%;object-fit:contain;}",
  ].join("");

  function ensureStyles() {
    if (document.getElementById(STYLE_ID)) return;
    var style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = STRUCTURAL_CSS;
    (document.head || document.documentElement).appendChild(style);
  }

  // ------------------------------------------------------------------ bitmap

  // One byte per second of the video. A 2-hour lesson costs 7.2KB, which buys
  // free deduplication of rewatching and an exact "is this second new?" answer.
  // What goes over the wire is run-length encoded — see rle() — because a bitmap
  // on the wire would be both larger and unreadable in an audit.
  function createBitmap(durationSeconds) {
    var size = Math.max(1, cint(durationSeconds) + 2);
    var bits = new Uint8Array(size);

    return {
      resize: function (durationSeconds) {
        var want = Math.max(1, cint(durationSeconds) + 2);
        if (want <= bits.length) return;
        var next = new Uint8Array(want);
        next.set(bits);
        bits = next;
      },
      // Marks the integer seconds touched by the credited media span [from, to).
      // Edges round outward, so the first tick of a play can over-credit by under
      // a second. That is deliberate: the alternative is fractional accounting
      // that the server's integer intervals cannot represent anyway.
      mark: function (from, to, out) {
        var start = Math.max(0, Math.floor(from));
        var end = Math.min(bits.length - 1, Math.ceil(to));
        var added = 0;
        for (var s = start; s < end; s++) {
          if (!bits[s]) {
            bits[s] = 1;
            added++;
            if (out) out.push(s);
          }
        }
        return added;
      },
      has: function (second) {
        var s = Math.floor(second);
        return s >= 0 && s < bits.length && !!bits[s];
      },
      count: function () {
        var n = 0;
        for (var i = 0; i < bits.length; i++) n += bits[i];
        return n;
      },
    };
  }

  // [3,4,5,9,10] -> [[3,6],[9,11]] — half-open, matching progress_json's `iv`.
  function rle(seconds) {
    if (!seconds.length) return [];
    var sorted = seconds.slice().sort(function (a, b) {
      return a - b;
    });
    var out = [];
    var start = sorted[0];
    var prev = sorted[0];
    for (var i = 1; i < sorted.length; i++) {
      var s = sorted[i];
      if (s === prev) continue;
      if (s === prev + 1) {
        prev = s;
        continue;
      }
      out.push([start, prev + 1]);
      start = s;
      prev = s;
    }
    out.push([start, prev + 1]);
    return out;
  }

  // ------------------------------------------------------------------- queue

  // A phone that loses signal mid-lesson must not lose the minutes it already
  // watched. Every beat is written to sessionStorage BEFORE it is sent and
  // removed only on success, so a failure, a tab crash or a pagehide beacon that
  // never lands all end the same way: replayed on the next beat. Union-merging on
  // the server makes a duplicate replay a no-op, which is why queue-then-send is
  // safe and send-then-queue-on-error is not.
  function createBeatQueue(attempt) {
    var key = "tr.video.beats." + String(attempt || "anon");
    var memory = null; // private-mode fallback

    function read() {
      if (memory) return memory;
      try {
        var raw = window.sessionStorage.getItem(key);
        return raw ? JSON.parse(raw) || [] : [];
      } catch (e) {
        memory = memory || [];
        return memory;
      }
    }

    function write(list) {
      if (memory) {
        memory = list;
        return;
      }
      try {
        window.sessionStorage.setItem(key, JSON.stringify(list));
      } catch (e) {
        memory = list;
      }
    }

    return {
      push: function (payload) {
        var list = read();
        list.push(payload);
        // Bounded: an offline learner watching for an hour must not blow the
        // 5MB quota and take the whole page down with a QuotaExceededError.
        // Oldest go first — the newest beats carry the most recent state.
        while (list.length > MAX_QUEUED_BEATS) list.shift();
        write(list);
      },
      drop: function (clientId) {
        write(
          read().filter(function (p) {
            return p.client_id !== clientId;
          })
        );
      },
      // A beat the server refused. Counted, moved to the BACK of the queue, and
      // abandoned after MAX_BEAT_ATTEMPTS.
      //
      // It used to stay at the head. `drainQueue` sends oldest-first and the
      // whole chain rejected on the first failure, so a single beat the server
      // would never accept — a stale attempt, a lesson key from a version that
      // has since been republished — blocked every newer beat behind it, on
      // every drain, for the life of the tab. The learner kept watching into a
      // queue that could not empty, and the only outward sign was a meter that
      // had quietly stopped moving.
      //
      // Returns whether the beat was given up on, so the caller can say so.
      fail: function (clientId) {
        var list = read();
        var abandoned = false;
        var kept = [];
        var retry = null;
        list.forEach(function (p) {
          if (p.client_id !== clientId) {
            kept.push(p);
            return;
          }
          p.attempts = cint(p.attempts) + 1;
          if (p.attempts >= MAX_BEAT_ATTEMPTS) {
            abandoned = true;
          } else {
            retry = p;
          }
        });
        if (retry) kept.push(retry);
        write(kept);
        return abandoned;
      },
      all: function () {
        return read();
      },
      clear: function () {
        write([]);
      },
    };
  }

  // ---------------------------------------------------------------- tab lock

  // Two tabs playing the same attempt is either a mistake or a doubling trick.
  // The OLDER claim wins and the newer tab pauses with a Take over action —
  // whichever way the learner meant it, they end up with one playing video and
  // an explanation rather than a mystery pause.
  function createTabLock(attempt, onYield) {
    var channel = null;
    try {
      channel = new BroadcastChannel(CHANNEL_NAME);
    } catch (e) {
      channel = null; // Safari < 15.4 and friends: single-tab honour system.
    }

    var id = randomId();
    var claimedAt = Date.now();

    function post(type) {
      if (!channel) return;
      try {
        channel.postMessage({ t: type, attempt: attempt, id: id, ts: claimedAt });
      } catch (e) {
        /* channel closed */
      }
    }

    if (channel) {
      channel.onmessage = function (ev) {
        var msg = ev && ev.data;
        if (!msg || msg.attempt !== attempt || msg.id === id) return;
        if (msg.t === "hello") {
          // Announce ourselves so the newcomer can see it arrived second.
          post("here");
        } else if (msg.t === "here" && msg.ts < claimedAt) {
          onYield("older-tab");
        } else if (msg.t === "take") {
          onYield("taken-over");
        }
      };
      post("hello");
    }

    return {
      // After an explicit takeover this tab becomes the oldest claim, so a third
      // tab opening later yields to it rather than restarting the argument.
      takeOver: function () {
        post("take");
        claimedAt = 1;
      },
      destroy: function () {
        post("bye");
        if (channel) {
          try {
            channel.close();
          } catch (e) {
            /* already closed */
          }
        }
      },
    };
  }

  // ------------------------------------------------------------------- video

  /**
   * TR.Video(wrapper, spec, transport)
   *
   * spec: {
   *   attempt, lesson_key, block_key,
   *   duration,                  // server-declared seconds; authoritative
   *   poster, title,
   *   checkpoints_enabled, min_coverage_percent,
   *   settings: { heartbeat_interval_seconds, max_playback_rate }
   * }
   *
   * transport: { mediaUrl, heartbeat, openCheckpoint, answerCheckpoint,
   *              heartbeatBeacon? } — every method returns a Promise except
   * heartbeatBeacon, which is synchronous and used on pagehide.
   */
  function Video(wrapper, spec, transport) {
    if (!(this instanceof Video)) return new Video(wrapper, spec, transport);
    ensureStyles();

    var self = this;
    spec = spec || {};
    transport = transport || {};
    var settings = spec.settings || {};

    var beatSeconds = clamp(
      cint(settings.heartbeat_interval_seconds) || DEFAULT_BEAT_SECONDS,
      MIN_BEAT_SECONDS,
      MAX_BEAT_SECONDS
    );
    var rateCap = Math.min(flt(settings.max_playback_rate) || MAX_PLAYBACK_RATE, MAX_PLAYBACK_RATE);
    var gateTarget = flt(spec.min_coverage_percent);

    var duration = flt(spec.duration);
    var bitmap = createBitmap(duration);
    var queue = createBeatQueue(spec.attempt);
    var listeners = {};

    var st = {
      pendingSeconds: [], // integer seconds credited since the last beat
      creditedSinceBeat: 0,
      creditedTotal: 0,
      serverCoverage: null, // the ONLY number ever displayed
      serverFlags: [],
      gateAnnounced: false,
      seq: 0,
      lastWall: 0,
      lastMedia: 0,
      lastBeatWall: 0,
      seeking: false,
      seeksForward: 0,
      seeksBackward: 0,
      blurredSince: 0,
      onScreen: true,
      stalled: false,
      ratedToasted: false,
      recoveries: 0,
      selfHealed: 0, // latches cleared by the watchdog in tick(); reported on the beat
      deliveryFailures: 0,
      offlineToasted: false,
      armed: null, // { checkpoint_key, at_seconds, ... }
      scrimOpen: false,
      yielded: false,
      destroyed: false,
      discount: { hidden: 0, blur: 0, offscreen: 0, muted: 0, stalled: 0, gap: 0 },
    };

    // -- DOM ---------------------------------------------------------------

    wrapper.classList.add("tr-video");
    wrapper.innerHTML = "";

    var video = el("video", "tr-video-el");
    video.preload = "metadata";
    video.controls = true;
    // playsinline keeps iOS from handing playback to its own fullscreen player,
    // which lives outside our DOM and would take the checkpoint scrim with it.
    // disablePictureInPicture is the same hazard through a different door.
    video.playsInline = true;
    video.setAttribute("playsinline", "");
    video.setAttribute("webkit-playsinline", "");
    video.disablePictureInPicture = true;
    video.setAttribute("disablepictureinpicture", "");
    video.setAttribute("controlsList", "nodownload noremoteplayback");
    if (spec.poster) video.poster = spec.poster;
    if (spec.title) video.setAttribute("aria-label", String(spec.title));
    wrapper.appendChild(video);

    var scrim = el("div", "tr-video-scrim");
    scrim.hidden = true;
    scrim.setAttribute("role", "dialog");
    scrim.setAttribute("aria-modal", "true");
    wrapper.appendChild(scrim);

    var takeover = el("div", "tr-video-takeover");
    takeover.hidden = true;
    wrapper.appendChild(takeover);

    var notice = el("div", "tr-video-notice");
    notice.hidden = true;
    notice.setAttribute("role", "status");
    wrapper.appendChild(notice);

    var meter = el("div", "tr-video-meter");
    var meterTrack = el("div", "tr-video-meter-track");
    var meterFill = el("div", "tr-video-meter-fill");
    meterTrack.appendChild(meterFill);
    var meterLabel = el("span", "tr-video-meter-label", "Watched —");
    meter.appendChild(meterTrack);
    meter.appendChild(meterLabel);
    meter.setAttribute("role", "status");
    wrapper.appendChild(meter);

    var noticeTimer = null;
    function showNotice(text, ms) {
      notice.textContent = text;
      notice.hidden = false;
      if (noticeTimer) clearTimeout(noticeTimer);
      noticeTimer = setTimeout(function () {
        notice.hidden = true;
      }, ms || 4000);
      emit("notice", { message: text });
    }

    function emit(name, payload) {
      (listeners[name] || []).forEach(function (fn) {
        try {
          fn(payload, self);
        } catch (e) {
          if (window.console) console.error("[TR.Video] listener failed", name, e);
        }
      });
    }

    // ------------------------------------------------------------ helpers

    function effectiveDuration() {
      // The server-declared duration wins. Coverage divides by it, and a browser
      // that reports Infinity (a still-buffering stream) or a slightly different
      // figure would move the denominator out from under the grade.
      if (duration > 0) return duration;
      var d = flt(video.duration);
      return isFinite(d) && d > 0 ? d : 0;
    }

    function isMuted() {
      return video.muted || flt(video.volume) === 0;
    }

    function creditBlocked() {
      // Everything that means "the seconds are passing but nobody is watching".
      if (document.visibilityState === "hidden") return "hidden";
      if (!st.onScreen) return "offscreen";
      if (st.blurredSince && nowMs() - st.blurredSince > BLUR_GRACE_MS) return "blur";
      if (st.stalled) return "stalled";
      return "";
    }

    function localCoverage() {
      var d = effectiveDuration();
      return d > 0 ? clamp(st.creditedTotal / d, 0, 1) * 100 : 0;
    }

    function renderMeter() {
      // Rule: the displayed figure is the server's. Until the first beat comes
      // back there is no server figure, and we say so rather than substituting
      // the local estimate — which is exactly the number a learner would quote
      // back at us if it were ever higher than the graded one.
      if (st.serverCoverage == null) {
        meterLabel.textContent = st.creditedTotal ? "Watched — syncing…" : "Watched —";
        meterFill.style.width = "0%";
        return;
      }
      var pct = clamp(flt(st.serverCoverage), 0, 100);
      meterFill.style.width = pct.toFixed(1) + "%";
      meterLabel.textContent =
        "Watched " + Math.round(pct) + "%" + (gateTarget > 0 ? " of " + Math.round(gateTarget) + "% needed" : "");
      if (gateTarget > 0 && pct + 0.001 >= gateTarget) {
        wrapper.classList.add("tr-video-gate-met");
        if (!st.gateAnnounced) {
          st.gateAnnounced = true;
          emit("gate", { coverage: pct, target: gateTarget });
        }
      }
    }

    // ------------------------------------------------------------ sampler

    var ticker = null;

    function rebaseline() {
      st.lastWall = nowMs();
      st.lastMedia = flt(video.currentTime);
    }

    function tick() {
      if (st.destroyed) return;

      var wall = nowMs();
      var media = flt(video.currentTime);
      var dtWall = (wall - st.lastWall) / 1000;
      var dtMedia = media - st.lastMedia;

      st.lastWall = wall;
      st.lastMedia = media;

      // Unstick the latches, BEFORE anything reads them.
      //
      // `seeking`, `stalled`, `blurredSince` and `onScreen` are each set by one
      // event and cleared by a different one, and every one of them stops credit
      // dead. If the clearing event never arrives — an aborted seek that fires no
      // `seeked`, a `stalled` the browser fires at an idle paused video, a window
      // that never re-focuses, an IntersectionObserver that goes quiet when the
      // element enters native fullscreen — the learner watches the rest of the
      // lesson for nothing and is told nothing. Four separate ways to produce the
      // same silent freeze, which is three too many to keep guessing between.
      //
      // Media time advancing on a playing element is proof the latch is lying, so
      // that is what clears it. This gives away no credit: a real seek still
      // fails the two-clock allowance check below, which is the actual anti-skip
      // machinery — these flags were only ever belt to its braces.
      if (!video.paused && !video.ended && dtMedia > 0 && dtWall > 0 && dtWall <= MAX_TICK_SECONDS) {
        if (st.seeking) {
          st.seeking = false;
          st.selfHealed++;
        }
        if (st.stalled) {
          st.stalled = false;
          st.selfHealed++;
        }
        if (st.blurredSince && document.hasFocus && document.hasFocus()) {
          st.blurredSince = 0;
          st.selfHealed++;
        }
        if (!st.onScreen && document.fullscreenElement && wrapper.contains(document.fullscreenElement)) {
          st.onScreen = true;
          st.selfHealed++;
        }
      }

      if (video.paused || video.ended || st.seeking || st.scrimOpen || st.yielded) return;

      // The sampler itself went away (throttled background tab, sleeping phone).
      // Both clocks moved together, so the consistency check would pass and hand
      // out minutes nobody watched. Credit nothing and note the gap.
      if (dtWall > MAX_TICK_SECONDS) {
        st.discount.gap += Math.round(dtWall);
        return;
      }

      var blocked = creditBlocked();
      if (blocked) {
        st.discount[blocked] += dtWall;
        return;
      }
      if (isMuted()) {
        // Counted, not withheld: a screencast with burned-in captions is watched
        // perfectly well on mute, and refusing credit for it produces support
        // tickets rather than integrity. The count travels with the beat so a
        // reviewer can see a whole course was taken in silence.
        st.discount.muted += dtWall;
      }

      if (dtMedia <= 0) return; // paused between ticks, or a rewind: nothing new.

      var rate = flt(video.playbackRate) || 1;
      var allowance = dtWall * rate * JANK_SLOP + JANK_FLOOR_SECONDS;
      if (dtMedia > allowance) {
        // Media outran wall: a forward seek, a rate the element reports late, or
        // a currentTime assignment. This is the whole point of the file.
        st.seeksForward++;
        return;
      }

      var added = bitmap.mark(media - dtMedia, media, st.pendingSeconds);
      if (added) {
        st.creditedSinceBeat += added;
        st.creditedTotal += added;
      }

      maybeFireCheckpoint(media - dtMedia, media);

      if (st.creditedSinceBeat >= beatSeconds) flush("interval");
    }

    // ---------------------------------------------------------- heartbeat

    function buildPayload(reason) {
      var payload = {
        client_id: randomId(),
        attempt: spec.attempt,
        lesson_key: spec.lesson_key,
        block_key: spec.block_key,
        duration: Math.round(effectiveDuration()),
        ranges: rle(st.pendingSeconds),
        claimed_seconds: st.pendingSeconds.length,
        client_elapsed_ms: Math.round(nowMs() - (st.lastBeatWall || nowMs())),
        position: Math.round(flt(video.currentTime)),
        playback_rate: flt(video.playbackRate) || 1,
        seeks: { forward: st.seeksForward, backward: st.seeksBackward },
        discount: {
          hidden: Math.round(st.discount.hidden),
          blur: Math.round(st.discount.blur),
          offscreen: Math.round(st.discount.offscreen),
          muted: Math.round(st.discount.muted),
          stalled: Math.round(st.discount.stalled),
          gap: Math.round(st.discount.gap),
        },
        // How many times the watchdog in tick() had to clear a latch that should
        // have cleared itself. Zero on a healthy browser; anything else names a
        // player bug that would otherwise present as "the meter stopped" and be
        // impossible to tell apart from a learner who walked away.
        self_healed: cint(st.selfHealed),
        reason: reason,
        seq: ++st.seq,
      };
      // No user, no employee, no score, no coverage: rule 6. Everything the
      // server needs about identity it takes from frappe.session.user, and
      // everything about time from its own clock.
      st.pendingSeconds = [];
      st.creditedSinceBeat = 0;
      st.seeksForward = 0;
      st.seeksBackward = 0;
      st.discount = { hidden: 0, blur: 0, offscreen: 0, muted: 0, stalled: 0, gap: 0 };
      st.selfHealed = 0;
      st.lastBeatWall = nowMs();
      return payload;
    }

    function applyBeatResult(res) {
      if (!res) return;
      if (res.coverage != null) st.serverCoverage = flt(res.coverage);
      if (res.flags && res.flags.length) st.serverFlags = res.flags;
      renderMeter();
      emit("coverage", {
        coverage: st.serverCoverage,
        credited: cint(res.credited),
        flags: res.flags || [],
      });
    }

    // Sends the queue oldest-first, then resolves. A beat is dropped from the
    // queue only once the server has taken it.
    function drainQueue() {
      if (typeof transport.heartbeat !== "function") return Promise.resolve(null);
      var pending = queue.all();
      if (!pending.length) return Promise.resolve(null);

      var last = null;
      var failures = 0;
      return pending
        .reduce(function (chain, payload) {
          return chain.then(function () {
            return Promise.resolve(transport.heartbeat(payload)).then(
              function (res) {
                queue.drop(payload.client_id);
                st.deliveryFailures = 0;
                last = res;
              },
              function (err) {
                // Caught PER BEAT. Attaching one `.catch` to the whole chain
                // meant the first refusal skipped every beat behind it, and the
                // refused payload stayed at the head of the queue to do it again
                // on the next drain — a permanent, silent stop.
                failures++;
                st.deliveryFailures++;
                var abandoned = queue.fail(payload.client_id);
                if (window.console) {
                  console.warn("[TR.Video] beat " + (abandoned ? "abandoned" : "deferred"), err);
                }
                if (abandoned) emit("beat-abandoned", { seq: payload.seq, error: err });
              }
            );
          });
        }, Promise.resolve())
        .then(function () {
          if (failures) noticeIfStuck();
          return last;
        });
    }

    // Somebody has to say so. `emit("offline")` had no subscriber anywhere in the
    // app, so a queue that could not drain looked exactly like a lesson nobody
    // was watching — and the learner's own coverage meter is the thing that stops
    // moving. Better a line of text than a number they will argue about later.
    function noticeIfStuck() {
      if (st.offlineToasted || st.deliveryFailures < STUCK_AFTER_FAILURES) return;
      st.offlineToasted = true;
      showNotice(
        "Your progress is not reaching the server right now. Keep watching — it is " +
          "saved on this device and will catch up.",
        8000
      );
    }

    var flushing = false;
    var flushStartedAt = 0;
    var retryTimer = null;

    // The queue's own heartbeat. Backs off so a server that is down is not
    // hammered by every open lesson, and stops as soon as the queue is empty.
    function scheduleRetry() {
      if (retryTimer || st.destroyed) return;
      if (!queue.all().length) return;
      var delay = Math.min(RETRY_BASE_MS * Math.pow(2, Math.min(st.deliveryFailures, 5)), RETRY_MAX_MS);
      retryTimer = setTimeout(function () {
        retryTimer = null;
        flush("retry");
      }, delay);
    }

    function flush(reason) {
      if (st.destroyed && reason !== "destroy") return Promise.resolve(null);
      var hasWork = st.pendingSeconds.length || queue.all().length;
      if (!hasWork && reason === "interval") return Promise.resolve(null);
      if (st.pendingSeconds.length) queue.push(buildPayload(reason));
      // The latch must not be able to strand delivery. It is released only when
      // the drain's promise settles, and a transport that never settles — a
      // socket an intermediary drops without a FIN, which `fetch` will wait on
      // forever — used to hold it true for the life of the page. Every later
      // beat then queued itself and returned without sending, so the learner
      // watched the whole lesson into a queue nothing would ever drain.
      //
      // Belt as well as braces: the page transport now sets an AbortSignal, but
      // the player is handed its transport by the caller and cannot assume one.
      if (flushing) {
        if (nowMs() - flushStartedAt < FLUSH_STUCK_MS) return Promise.resolve(null);
        if (window.console) console.warn("[TR.Video] a heartbeat never settled; releasing the latch");
        st.selfHealed++;
      }

      flushing = true;
      flushStartedAt = nowMs();
      return drainQueue()
        .then(function (res) {
          flushing = false;
          applyBeatResult(res);
          // Beats built while that drain was in flight are in the queue but were
          // not in its snapshot, and the only thing that ever started a drain was
          // another beat's worth of credited playback. A learner who paused right
          // after a flush left them there indefinitely. Retry until it is empty.
          scheduleRetry();
          return res;
        })
        .catch(function (err) {
          flushing = false;
          scheduleRetry();
          // Left in the queue on purpose — the next beat replays it. Nothing is
          // said to the learner: an offline blip that self-heals is not news.
          if (window.console) console.warn("[TR.Video] heartbeat deferred", err);
          emit("offline", { error: err });
          return null;
        });
    }

    // pagehide is the only flush that cannot await a Promise. sendBeacon cannot
    // set headers, so the beacon endpoint takes the CSRF token in the body —
    // that is the transport's business, not ours. When no beacon variant is
    // injected the beat still survives in sessionStorage and replays on return.
    function flushOnUnload() {
      if (st.pendingSeconds.length) queue.push(buildPayload("pagehide"));
      var pending = queue.all();
      if (!pending.length) return;
      if (typeof transport.heartbeatBeacon !== "function") return;
      pending.forEach(function (payload) {
        try {
          if (transport.heartbeatBeacon(payload)) queue.drop(payload.client_id);
        } catch (e) {
          /* keep it queued */
        }
      });
    }

    // --------------------------------------------------------- checkpoints

    // Never a list. The runtime holds exactly one armed checkpoint and asks for
    // the next only after this one is settled — a list of at_seconds is a map of
    // where to skip to, which is the same as publishing the answer key's index.
    function armNext() {
      if (!spec.checkpoints_enabled || typeof transport.openCheckpoint !== "function") return Promise.resolve(null);
      return Promise.resolve(
        transport.openCheckpoint({
          attempt: spec.attempt,
          // `at` is REQUIRED and was not being sent, so every call raised a
          // TypeError before it reached the endpoint. It is the playhead: the
          // server returns the next unanswered checkpoint at or before this
          // position, and refuses one whose timestamp the stored watch intervals
          // do not cover. Sending 0 would arm nothing; omitting it armed nothing
          // either, but noisily.
          at: Math.round(flt(video.currentTime)),
          lesson_key: spec.lesson_key,
          block_key: spec.block_key,
        })
      )
        .then(function (cp) {
          st.armed = cp && cp.checkpoint_key ? cp : null;
          return st.armed;
        })
        .catch(function (err) {
          // A checkpoint we cannot fetch must not block the video; the server
          // still refuses completion until it is answered.
          if (window.console) console.warn("[TR.Video] checkpoint fetch failed", err);
          st.armed = null;
          return null;
        });
    }

    function maybeFireCheckpoint(from, to) {
      if (!st.armed || st.scrimOpen) return;
      var at = flt(st.armed.at_seconds);
      // Only ever fired from inside a CREDITED span, so "reached by a seek" is
      // structurally impossible here rather than separately guarded.
      if (at < from - CHECKPOINT_TOLERANCE || at > to) return;
      openCheckpoint(st.armed);
    }

    function openCheckpoint(cp) {
      st.scrimOpen = true;
      video.pause();
      // The scrubber is how you skip a question. Take it away for the duration —
      // and only for the duration.
      video.controls = false;
      flush("checkpoint");
      renderCheckpoint(cp, null);
      scrim.hidden = false;
      emit("checkpoint-open", { checkpoint: cp });
    }

    function closeCheckpoint(resume) {
      scrim.hidden = true;
      scrim.innerHTML = "";
      st.scrimOpen = false;
      video.controls = true;
      rebaseline();
      if (resume && !st.yielded) {
        var p = video.play();
        if (p && p.catch) p.catch(function () {});
      }
    }

    function renderCheckpoint(cp, result) {
      scrim.innerHTML = "";
      var panel = el("div", "tr-video-panel");

      if (!result) {
        panel.appendChild(el("h4", "tr-video-question", cp.question_text || "Quick check"));
        var multi = String(cp.question_type || "") === "Multiple Choice";
        var list = el("ul", "tr-video-options");
        var inputs = [];
        (cp.options || []).forEach(function (opt) {
          var item = el("li", "tr-video-option");
          var label = el("label", "tr-video-option-label");
          var input = document.createElement("input");
          input.type = multi ? "checkbox" : "radio";
          input.name = "tr-cp-" + cp.checkpoint_key;
          input.value = opt.option_key;
          label.appendChild(input);
          label.appendChild(el("span", null, opt.text || opt.option_text || ""));
          item.appendChild(label);
          list.appendChild(item);
          inputs.push(input);
        });
        panel.appendChild(list);

        if (multi) panel.appendChild(el("p", "tr-video-hint", "Select every answer that applies."));

        var actions = el("div", "tr-video-actions");
        var submit = el("button", "tr-video-btn tr-video-btn-primary", "Answer");
        submit.type = "button";
        submit.addEventListener("click", function () {
          var picked = inputs
            .filter(function (i) {
              return i.checked;
            })
            .map(function (i) {
              return i.value;
            });
          if (!picked.length) {
            showNotice("Choose an answer first.", 2500);
            return;
          }
          submit.disabled = true;
          answerCheckpoint(cp, picked, submit);
        });
        actions.appendChild(submit);

        if (cint(cp.allow_skip)) {
          var skip = el("button", "tr-video-btn tr-video-btn-link", "Skip");
          skip.type = "button";
          skip.addEventListener("click", function () {
            closeCheckpoint(true);
            armNext();
          });
          actions.appendChild(skip);
        }
        panel.appendChild(actions);
      } else {
        panel.appendChild(
          el("h4", "tr-video-verdict", result.correct ? "Correct" : result.exhausted ? "Here is the answer" : "Not quite")
        );
        if (result.explanation) panel.appendChild(el("p", "tr-video-explanation", result.explanation));
        if (!result.correct && !result.exhausted && result.rewound) {
          panel.appendChild(
            el("p", "tr-video-hint", "We have rewound a little — those seconds already count, so this costs time, not score.")
          );
        }
        var next = el("div", "tr-video-actions");
        var cont = el("button", "tr-video-btn tr-video-btn-primary", result.correct || result.exhausted ? "Continue" : "Watch again");
        cont.type = "button";
        cont.addEventListener("click", function () {
          closeCheckpoint(true);
          // Correct or out of attempts: move to the following checkpoint. Wrong
          // with attempts left: keep this one armed so rewatching re-asks it.
          if (result.correct || result.exhausted) armNext();
        });
        next.appendChild(cont);
        panel.appendChild(next);
      }

      scrim.appendChild(panel);
      var focusable = scrim.querySelector("input, button");
      if (focusable) focusable.focus();
    }

    function answerCheckpoint(cp, optionKeys, submitBtn) {
      if (typeof transport.answerCheckpoint !== "function") {
        closeCheckpoint(true);
        return;
      }
      Promise.resolve(
        // No lesson_key or block_key: answer_checkpoint takes
        // (attempt, checkpoint_key, option_keys, response_ms) and resolves the
        // lesson and block from the checkpoint itself -- deliberately, so a caller
        // cannot answer one checkpoint while claiming to be in another block.
        // Frappe dropped the extra kwargs, so they were never more than source
        // claiming the server did something it does not.
        transport.answerCheckpoint({
          attempt: spec.attempt,
          checkpoint_key: cp.checkpoint_key,
          option_keys: optionKeys,
        })
      )
        .then(function (res) {
          res = res || {};
          var attemptsUsed = cint(res.attempts_used || res.attempts);
          var maxAttempts = cint(cp.max_attempts);
          // Out of attempts SHOWS THE ANSWER AND CONTINUES. A learner trapped in
          // a question they cannot pass abandons the course, and it arrives back
          // as a support ticket rather than as a completion.
          var exhausted = !!res.exhausted || (maxAttempts > 0 && attemptsUsed >= maxAttempts);
          var rewound = false;

          if (!res.correct && !exhausted) {
            var back = cint(cp.rewind_seconds_on_wrong);
            if (back > 0) {
              video.currentTime = Math.max(0, flt(cp.at_seconds) - back);
              rebaseline();
              rewound = true;
            }
          }

          emit("checkpoint-answered", { checkpoint: cp, correct: !!res.correct, exhausted: exhausted });
          renderCheckpoint(cp, {
            correct: !!res.correct,
            explanation: res.explanation || "",
            exhausted: exhausted,
            rewound: rewound,
          });
          // The answer changed the record; get it on the server now rather than
          // at the next beat, which a closed laptop may never send.
          flush("checkpoint");
        })
        .catch(function (err) {
          if (submitBtn) submitBtn.disabled = false;
          showNotice("That did not save. Check your connection and try again.");
          if (window.console) console.warn("[TR.Video] answer failed", err);
        });
    }

    // ------------------------------------------------------------- takeover

    function renderTakeover(reason) {
      takeover.innerHTML = "";
      var panel = el("div", "tr-video-panel");
      panel.appendChild(el("h4", null, "Playing in another tab"));
      panel.appendChild(
        el(
          "p",
          null,
          reason === "taken-over"
            ? "Another tab took over this course."
            : "This course is already open in another tab, so playback is paused here."
        )
      );
      var btn = el("button", "tr-video-btn tr-video-btn-primary", "Take over here");
      btn.type = "button";
      btn.addEventListener("click", function () {
        lock.takeOver();
        st.yielded = false;
        takeover.hidden = true;
        rebaseline();
      });
      panel.appendChild(btn);
      takeover.appendChild(panel);
      takeover.hidden = false;
    }

    function yieldToOtherTab(reason) {
      if (st.yielded) return;
      st.yielded = true;
      video.pause();
      flush("tab-yield");
      renderTakeover(reason);
    }

    var lock = createTabLock(spec.attempt, yieldToOtherTab);

    // --------------------------------------------------------------- source

    function resolveSource() {
      if (spec.src) return Promise.resolve({ url: spec.src });
      if (typeof transport.mediaUrl !== "function") return Promise.resolve(null);
      return Promise.resolve(
        // get_media_url is (attempt, block_key). The block already identifies its
        // lesson.
        transport.mediaUrl({
          attempt: spec.attempt,
          block_key: spec.block_key,
        })
      ).then(function (res) {
        if (!res) return null;
        if (typeof res === "string") return { url: res };
        return { url: res.url || res.signed_url || res.href || "" };
      });
    }

    // A signed playback URL outlives its TTL by design (it cannot be revoked, so
    // the TTL is short). A lesson longer than the TTL therefore loses its source
    // mid-watch, and the only symptom is an opaque media error. Re-sign and
    // resume at the same second rather than making the learner reload.
    function recoverSource() {
      if (st.recoveries >= MAX_SOURCE_RECOVERIES) {
        showNotice("This video could not be loaded. Reload the page to try again.", 8000);
        emit("error", { stage: "source" });
        return;
      }
      st.recoveries++;
      var at = flt(video.currentTime);
      var wasPlaying = !video.paused;
      resolveSource().then(function (src) {
        if (!src || !src.url) return;
        video.src = src.url;
        video.load();
        var restore = function () {
          video.removeEventListener("loadedmetadata", restore);
          try {
            video.currentTime = at;
          } catch (e) {
            /* browser refused the seek; start over rather than fail */
          }
          rebaseline();
          if (wasPlaying) {
            var p = video.play();
            if (p && p.catch) p.catch(function () {});
          }
        };
        video.addEventListener("loadedmetadata", restore);
      });
    }

    // --------------------------------------------------------------- events

    var bound = [];

    function on(target, type, fn, opts) {
      target.addEventListener(type, fn, opts);
      bound.push([target, type, fn, opts]);
    }

    on(video, "loadedmetadata", function () {
      var reported = flt(video.duration);
      if (!duration && isFinite(reported) && reported > 0) duration = reported;
      bitmap.resize(duration);
      if (spec.duration && isFinite(reported) && reported > 0 && Math.abs(reported - flt(spec.duration)) > 2) {
        // The declared duration is the coverage denominator. If it disagrees
        // with the file, somebody's gate is being scored against the wrong
        // total — say so loudly in the telemetry rather than quietly grading.
        emit("integrity", { flag: "duration_mismatch", declared: flt(spec.duration), actual: reported });
      }
      rebaseline();
    });

    on(video, "play", function () {
      rebaseline();
      if (st.yielded) video.pause();
    });
    on(video, "pause", function () {
      flush("pause");
    });
    on(video, "ended", function () {
      flush("ended");
      emit("ended", {});
    });

    on(video, "seeking", function () {
      st.seeking = true;
      if (flt(video.currentTime) < st.lastMedia) st.seeksBackward++;
    });
    on(video, "seeked", function () {
      st.seeking = false;
      rebaseline();
    });

    on(video, "waiting", function () {
      st.stalled = true;
    });
    on(video, "stalled", function () {
      st.stalled = true;
    });
    on(video, "playing", function () {
      st.stalled = false;
      rebaseline();
    });
    on(video, "canplay", function () {
      st.stalled = false;
    });
    on(video, "error", function () {
      recoverSource();
    });

    on(video, "ratechange", function () {
      if (flt(video.playbackRate) > rateCap) {
        video.playbackRate = rateCap;
        if (!st.ratedToasted) {
          st.ratedToasted = true;
          showNotice("Speed is capped at " + rateCap + "x so your watch time still counts.", 6000);
        }
      }
      rebaseline();
    });

    on(document, "visibilitychange", function () {
      if (document.visibilityState === "hidden") flush("hidden");
      rebaseline();
    });
    on(window, "blur", function () {
      st.blurredSince = nowMs();
    });
    on(window, "focus", function () {
      st.blurredSince = 0;
      rebaseline();
    });
    // pagehide, not unload: unload does not fire on mobile Safari and blocks the
    // back/forward cache everywhere else.
    on(window, "pagehide", flushOnUnload);

    var observer = null;
    if (window.IntersectionObserver) {
      observer = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            st.onScreen = entry.isIntersecting && entry.intersectionRatio >= ON_SCREEN_RATIO;
          });
          rebaseline();
        },
        { threshold: [0, ON_SCREEN_RATIO, 1] }
      );
      observer.observe(wrapper);
    }

    // ------------------------------------------------------------ lifecycle

    self.ready = resolveSource()
      .then(function (src) {
        if (!src || !src.url) {
          showNotice("This video is not available right now.", 8000);
          emit("error", { stage: "source" });
          return null;
        }
        video.src = src.url;
        rebaseline();
        st.lastBeatWall = nowMs();
        ticker = setInterval(tick, TICK_MS);
        // Anything left from a dropped connection in a previous lesson goes
        // first, so the learner's coverage is right before they watch a second
        // more of it.
        return flush("resume").then(function () {
          return armNext();
        });
      })
      .then(function () {
        renderMeter();
        return self;
      });

    // ------------------------------------------------------------ public API

    self.el = wrapper;
    self.video = video;

    self.on = function (name, fn) {
      (listeners[name] = listeners[name] || []).push(fn);
      return self;
    };

    self.off = function (name, fn) {
      listeners[name] = (listeners[name] || []).filter(function (f) {
        return f !== fn;
      });
      return self;
    };

    self.pause = function () {
      video.pause();
      return self;
    };

    self.flush = function (reason) {
      return flush(reason || "manual");
    };

    self.requestFullscreen = function () {
      // The WRAPPER, never the <video>: fullscreening the element hands the page
      // to the browser's own chrome and the checkpoint scrim — a sibling node —
      // stops existing as far as the learner is concerned.
      var fn = wrapper.requestFullscreen || wrapper.webkitRequestFullscreen;
      if (fn) fn.call(wrapper);
      return self;
    };

    self.state = function () {
      return {
        coverage: st.serverCoverage, // server figure, or null before the first beat
        local_estimate: localCoverage(), // diagnostics only — never render this
        credited_seconds: st.creditedTotal,
        duration: effectiveDuration(),
        gate_target: gateTarget,
        gate_met: gateTarget > 0 && st.serverCoverage != null && flt(st.serverCoverage) >= gateTarget,
        flags: st.serverFlags.slice(),
        checkpoint_pending: !!st.armed,
      };
    };

    self.destroy = function () {
      if (st.destroyed) return;
      video.pause();
      flush("destroy");
      st.destroyed = true;
      if (ticker) clearInterval(ticker);
      if (retryTimer) clearTimeout(retryTimer);
      if (noticeTimer) clearTimeout(noticeTimer);
      if (observer) observer.disconnect();
      lock.destroy();
      bound.forEach(function (b) {
        b[0].removeEventListener(b[1], b[2], b[3]);
      });
      bound = [];
      listeners = {};
      video.removeAttribute("src");
      try {
        video.load();
      } catch (e) {
        /* detached */
      }
    };

    return self;
  }

  // Exposed for the player's own tests and for anyone tempted to re-derive them.
  Video.rle = rle;
  Video.MAX_PLAYBACK_RATE = MAX_PLAYBACK_RATE;

  // The entry point blocks.js actually calls. Its absence is why every Video
  // block rendered "The video player did not load. Please refresh the page." —
  // blocks.js checks `typeof TR.Video.mount === "function"` and this file
  // exported only the constructor, so the guard fired on a player that had loaded
  // perfectly well.
  //
  // The builder's preview papered over it by patching a `mount` onto TR.Video at
  // runtime, which is worse than the bug: the preview showed the author a working
  // lesson while every learner got the failure notice. There is one definition
  // now, and it lives with the constructor it builds.
  //
  // `(wrapper, block, ctx)` — a published block and the player's block context.
  // Translating between those two shapes is the whole job.
  Video.mount = function (wrapper, block, ctx) {
    block = block || {};
    ctx = ctx || {};
    var transport = ctx.transport || {};

    var video = new Video(
      wrapper,
      {
        attempt: ctx.attempt,
        lesson_key: ctx.lessonKey,
        block_key: block.block_key,
        // `duration_s` on the wire, `duration` in the spec. Coverage is a
        // fraction of this, so a zero here silently disables the gate.
        duration: block.duration_s,
        poster: block.poster,
        title: block.heading,
        checkpoints_enabled: block.checkpoints_enabled,
        min_coverage_percent: block.min_coverage,
        settings: ctx.settings || {},
      },
      {
        mediaUrl: transport.mediaUrl,
        openCheckpoint: transport.openCheckpoint,
        answerCheckpoint: transport.answerCheckpoint,
        heartbeatBeacon: transport.heartbeatBeacon,
        // `ctx.heartbeat`, not `transport.heartbeat`. The player wraps the raw
        // call to stamp the lesson key, merge the response into its progress and
        // refresh the gate — go straight to the transport and the coverage meter
        // and the Finish button both stop updating while the video plays.
        heartbeat:
          typeof ctx.heartbeat === "function" ? ctx.heartbeat : transport.heartbeat,
      }
    );

    // A <video> left running in a detached node keeps downloading, and a phone
    // locking mid-lesson must not lose the seconds since the last beat.
    if (typeof ctx.onTeardown === "function") {
      ctx.onTeardown(function () {
        video.destroy();
      });
    }
    if (typeof ctx.onFlush === "function") {
      ctx.onFlush(function (reason) {
        video.flush(reason);
      });
    }
    return video;
  };

  TR.Video = Video;
})();
