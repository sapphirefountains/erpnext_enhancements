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
      return pending
        .reduce(function (chain, payload) {
          return chain.then(function () {
            return Promise.resolve(transport.heartbeat(payload)).then(function (res) {
              queue.drop(payload.client_id);
              last = res;
            });
          });
        }, Promise.resolve())
        .then(function () {
          return last;
        });
    }

    var flushing = false;

    function flush(reason) {
      if (st.destroyed && reason !== "destroy") return Promise.resolve(null);
      var hasWork = st.pendingSeconds.length || queue.all().length;
      if (!hasWork && reason === "interval") return Promise.resolve(null);
      if (st.pendingSeconds.length) queue.push(buildPayload(reason));
      if (flushing) return Promise.resolve(null);

      flushing = true;
      return drainQueue()
        .then(function (res) {
          flushing = false;
          applyBeatResult(res);
          return res;
        })
        .catch(function (err) {
          flushing = false;
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
        transport.answerCheckpoint({
          attempt: spec.attempt,
          lesson_key: spec.lesson_key,
          block_key: spec.block_key,
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
        transport.mediaUrl({
          attempt: spec.attempt,
          lesson_key: spec.lesson_key,
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

  TR.Video = Video;
})();
