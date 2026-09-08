// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// The learner player: the shell, the routing and the five views a person
// actually moves through — catalog → course outline → lesson → quiz → results,
// with awaiting-sign-off as the terminal state for a course that needs a
// supervisor.
//
// Two constraints shape every line here, and both are easy to break by accident.
//
// 1. It must run for a Website User with `desk_access = 0`. That user never
//    loads frappe's desk bundle, so `frappe.call`, `frappe.msgprint`, `__()` and
//    jQuery do not exist for them. Every one of those works perfectly while a
//    developer tests the page logged in as themselves, and throws a
//    ReferenceError for every customer. Plain DOM only.
//
// 2. `TR.Player(rootEl, boot, transport)` knows nothing about where it lives. It
//    never reads `window.TRAINING_BOOT`, never calls `fetch` itself, and assumes
//    no `www/` page around it. The /training page injects a fetch transport; the
//    Phase-3 course builder injects a preview transport that grades for real and
//    writes no progress. Reach for one global here and the builder has to fork
//    the player, which is the single most expensive mistake available in this
//    module.
//
// Resume is server-authoritative. It arrives on the boot payload and on every
// `getLesson`, and is never read from localStorage. A customer starts a course
// on a phone at lunch and finishes it on a laptop that evening; a cached local
// pointer would put them back at lesson one, and — worse — would disagree with
// the compliance record about what they had done.
//
// The gates shown at the bottom of a lesson are advisory. `evaluate_gates` on
// the server is the real one, and `completeLesson` is entitled to refuse for a
// reason this file never computed. That is why a refusal renders the server's
// reasons rather than the ones it guessed.
//
// ── transport ────────────────────────────────────────────────────────────────
// Every method returns a Promise.
//   getCourse({course})               → {course, gates, version, chapters, toc,
//                                       assignment, attempt}
//   startAttempt({course})            → {attempt, status, next_lesson_key, ...}
//   getLesson({attempt, lesson_key})  → {attempt, lesson,
//                                        progress, resume, status}
//                                       lesson_key omitted ⇒ the server picks
//                                       the resume lesson.
//   heartbeat(payload)                → {coverage, credited, flags}
//   openCheckpoint({lesson_key, block_key, at})   → next unanswered checkpoint
//   answerCheckpoint({...})           → {correct, explanation, ...}
//   startQuiz({attempt, lesson_key})  → {run, questions, pass_score, attempts_left}
//   submitQuiz({attempt, lesson_key, answers}) → {score, passed, per_question}
//   completeLesson({attempt, lesson_key}) → {ok, coverage, next_lesson_key}
//   mediaUrl({attempt, block_key})     → {url, embed_url, reason, poster, ...}
//                                       (unwrapped to a string for blocks.js)
//
// ── boot ─────────────────────────────────────────────────────────────────────
//   {courses: [...], settings: {...}, resume: {...}, view: "catalog",
//    start: {course, lesson_key}, route_base: "/training", history: true,
//    translate: fn}
// `history: false` and an explicit `view`/`start` are what the builder preview
// uses to drop straight into one lesson without touching the address bar.
(function () {
	"use strict";

	var TR = (window.TR = window.TR || {});

	// ------------------------------------------------------------------ helpers

	function el(tag, className, text) {
		var node = document.createElement(tag);
		if (className) node.className = className;
		if (text != null) node.textContent = String(text);
		return node;
	}

	function button(label, className, onClick) {
		var node = el("button", className, label);
		node.type = "button";
		if (onClick) node.addEventListener("click", onClick);
		return node;
	}

	function clear(node) {
		while (node.firstChild) node.removeChild(node.firstChild);
	}

	function pct(value) {
		var n = Math.round(Number(value) || 0);
		return Math.max(0, Math.min(100, n));
	}

	// Sentences are written whole and interpolated, never concatenated from
	// translated fragments — word order differs between languages and "Watch
	// more of " + name only ever reads correctly in English.
	function fmt(template, values) {
		return String(template).replace(/\{(\d+)\}/g, function (match, index) {
			var value = (values || [])[Number(index)];
			return value == null ? "" : String(value);
		});
	}

	// "2026-08-10" through `new Date()` is parsed as UTC midnight, which reads as
	// the previous day for anyone west of Greenwich. A course would show "due
	// tomorrow" on the morning it went overdue. Build a local date explicitly.
	function parseDate(value) {
		var match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ""));
		if (!match) return null;
		return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
	}

	function daysUntil(value) {
		var due = parseDate(value);
		if (!due) return null;
		var today = new Date();
		today.setHours(0, 0, 0, 0);
		return Math.round((due - today) / 86400000);
	}

	function meter(percent, srLabel) {
		var wrap = el("div", "tr-meter");
		wrap.setAttribute("role", "progressbar");
		wrap.setAttribute("aria-valuemin", "0");
		wrap.setAttribute("aria-valuemax", "100");
		wrap.setAttribute("aria-valuenow", String(pct(percent)));
		if (srLabel) wrap.setAttribute("aria-label", srLabel);
		var fill = el("div", "tr-meter-fill");
		fill.style.width = pct(percent) + "%";
		wrap.appendChild(fill);
		return wrap;
	}

	function chip(text, tone) {
		return el("span", "tr-chip" + (tone ? " tr-chip-" + tone : ""), text);
	}

	// ------------------------------------------------------------------ motion

	function prefersReduced() {
		return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
	}

	// rAF count-up for a number node (the completion score and reward tiles).
	// Reduced motion writes the final value and never starts the loop.
	function countUp(node, to, duration, suffix) {
		suffix = suffix || "";
		to = Number(to) || 0;
		if (prefersReduced()) {
			node.textContent = String(Math.round(to)) + suffix;
			return;
		}
		var start = null;
		function step(ts) {
			if (start == null) start = ts;
			var progress = Math.min(1, (ts - start) / duration);
			var eased = 1 - Math.pow(1 - progress, 3);
			node.textContent = String(Math.round(to * eased)) + suffix;
			if (progress < 1) requestAnimationFrame(step);
			else node.textContent = String(Math.round(to)) + suffix;
		}
		requestAnimationFrame(step);
	}

	// A short confetti burst behind the completion card. A hand-rolled canvas, not
	// a library: the learner portal is a no-CDN page (customer Website Users have
	// no desk bundle), so a dependency here would either be inlined or blocked. The
	// caller skips this entirely under reduced motion; it clears itself after ~1.6s.
	function confetti(canvas) {
		var host = canvas.parentNode;
		if (!host || !canvas.getContext) return;
		var width = (canvas.width = host.clientWidth || 320);
		var height = (canvas.height = host.clientHeight || 480);
		var ctx = canvas.getContext("2d");
		var colors = ["#00a0dd", "#0077b6", "#d99b1f", "#2e9e4f", "#7fe0ff"];
		var pieces = [];
		for (var i = 0; i < 80; i++) {
			pieces.push({
				x: width / 2 + (Math.random() - 0.5) * 80,
				y: height * 0.3,
				vx: (Math.random() - 0.5) * 7,
				vy: -(Math.random() * 8 + 4),
				g: 0.22 + Math.random() * 0.12,
				size: 4 + Math.random() * 5,
				color: colors[i % colors.length],
				rot: Math.random() * 6.28,
				vr: (Math.random() - 0.5) * 0.3,
			});
		}
		var startTs = null;
		var duration = 1600;
		function frame(ts) {
			if (startTs == null) startTs = ts;
			var elapsed = ts - startTs;
			ctx.clearRect(0, 0, width, height);
			for (var j = 0; j < pieces.length; j++) {
				var p = pieces[j];
				p.vy += p.g;
				p.x += p.vx;
				p.y += p.vy;
				p.rot += p.vr;
				p.vx *= 0.99;
				ctx.save();
				ctx.translate(p.x, p.y);
				ctx.rotate(p.rot);
				ctx.globalAlpha = Math.max(0, 1 - elapsed / duration);
				ctx.fillStyle = p.color;
				ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size * 0.6);
				ctx.restore();
			}
			if (elapsed < duration) requestAnimationFrame(frame);
			else ctx.clearRect(0, 0, width, height);
		}
		requestAnimationFrame(frame);
	}

	// ------------------------------------------------------------------- player

	function Player(rootEl, boot, transport) {
		if (!rootEl) throw new Error("TR.Player needs a root element");

		var b = boot || {};
		var api = transport || {};
		var t = typeof b.translate === "function" ? b.translate : function (s) { return s; };

		var state = {
			view: "catalog",
			courseName: null,
			course: null,
			outline: [],
			lesson: null,
			lessonKey: null,
			progress: {},
			attempt: null,
			resume: b.resume || null,
			quiz: null,
			result: null,
			status: null,
			busy: false,
		};

		// Reset on every view change. Anything that owns a timer, an interval or a
		// media element registers here so leaving a lesson actually stops it —
		// a <video> left running in a detached node keeps downloading.
		var teardowns = [];
		// Registered by blocks and by TR.Video: called when the tab is being
		// hidden or the view is leaving, so a phone locking mid-video does not
		// lose the seconds since the last beat.
		var flushers = [];

		// NO second .tr-shell here. The Jinja template already gives the mount
		// point that class, and this used to build another one inside it — so
		// .tr-shell's grid applied twice, nested, and at >= 900px the inner grid
		// was laid out inside one 260px column of the outer one. The result was
		// two narrow columns of one-word-per-line text, which is what the page
		// actually looked like. These three are direct children of the mount.
		var head = el("header", "tr-subhead");
		var main = el("main", "tr-view");
		var foot = el("footer", "tr-bottom");
		clear(rootEl);
		rootEl.appendChild(head);
		rootEl.appendChild(main);
		rootEl.appendChild(foot);
		rootEl.removeAttribute("aria-busy");

		// -------------------------------------------------------------- routing

		// replaceState rather than pushState: the player is one page with one
		// place in history. Pushing would mean the back button walked backwards
		// through every block card a learner scrolled past, and on a phone that
		// reads as "back is broken". Replacing keeps refresh landing where they
		// were and lets back mean "leave the course".
		function route() {
			if (b.history === false || !window.history || !window.history.replaceState) return;
			var base = b.route_base || window.location.pathname;
			var params = [];
			if (state.courseName) params.push("course=" + encodeURIComponent(state.courseName));
			if (state.lessonKey && state.view !== "catalog" && state.view !== "course") {
				params.push("lesson=" + encodeURIComponent(state.lessonKey));
			}
			if (state.view === "quiz" || state.view === "results") params.push("view=" + state.view);
			try {
				window.history.replaceState(
					{ tr: { view: state.view, course: state.courseName, lesson: state.lessonKey } },
					"",
					base + (params.length ? "?" + params.join("&") : "")
				);
			} catch (err) {
				// A sandboxed iframe (the builder preview) refuses replaceState.
				// Routing is a convenience; losing it must not stop the lesson.
			}
		}

		function queryParam(name) {
			var match = new RegExp("[?&]" + name + "=([^&]*)").exec(window.location.search);
			return match ? decodeURIComponent(match[1].replace(/\+/g, " ")) : "";
		}

		// -------------------------------------------------------------- plumbing

		function runTeardowns() {
			flush();
			teardowns.forEach(function (fn) {
				try {
					fn();
				} catch (err) {
					/* a failing teardown must not block the next view */
				}
			});
			teardowns = [];
			flushers = [];
		}

		function flush() {
			flushers.forEach(function (fn) {
				try {
					fn();
				} catch (err) {
					/* best effort by definition */
				}
			});
		}

		function setBusy(on) {
			state.busy = !!on;
			rootEl.classList.toggle("is-busy", state.busy);
			if (state.busy) rootEl.setAttribute("aria-busy", "true");
			else rootEl.removeAttribute("aria-busy");
		}

		function fail(node, err) {
			var message =
				(err && (err.message || err.error)) ||
				t("Something went wrong. Please try again in a moment.");
			var box = el("div", "tr-error", message);
			box.setAttribute("role", "alert");
			node.appendChild(box);
		}

		function call(name, args) {
			var fn = api[name];
			if (typeof fn !== "function") {
				return Promise.reject(new Error(t("This action is not available here.")));
			}
			return Promise.resolve(fn(args || {}));
		}

		// ---------------------------------------------------------- progress view

		function lessonProgress(lessonKey) {
			var lessons = state.progress.lessons || {};
			return lessons[lessonKey] || {};
		}

		function blockProgress(blockKey) {
			var lesson = lessonProgress(state.lessonKey);
			return (lesson.blocks || {})[blockKey] || {};
		}

		function mergeHeartbeat(blockKey, response) {
			if (!response) return;
			var lessons = (state.progress.lessons = state.progress.lessons || {});
			var lesson = (lessons[state.lessonKey] = lessons[state.lessonKey] || {});
			var blocks = (lesson.blocks = lesson.blocks || {});
			var block = (blocks[blockKey] = blocks[blockKey] || {});
			if (response.coverage != null) block.cov = Number(response.coverage) / 100;
			if (response.ack != null) block.ack = response.ack;
		}

		// The advisory gate. It exists so the bottom bar can say *why* the button
		// is not going to work before the learner presses it — not to decide
		// anything. `evaluate_gates` is the authority and is allowed to disagree.
		function localGates() {
			var reasons = [];
			var lesson = state.lesson;
			if (!lesson) return { ok: false, reasons: reasons };

			// `state.gates`, not `state.course`. get_course groups the thresholds
			// under a `gates` key of its own — `load()` has always stored it — and
			// these two reads were the only consumers, both looking in the wrong
			// object. So the advisory panel showed 0% for every course, which reads
			// as "no requirement" rather than as a bug.
			var courseMin = pct((state.gates && state.gates.min_video_coverage) || 0);
			(lesson.blocks || []).forEach(function (block) {
				if (!block.required) return;
				var stored = blockProgress(block.block_key);
				if (block.type === "Video") {
					var need = pct(block.min_coverage || courseMin);
					if (!need) return;
					var have = pct((stored.cov || 0) * 100);
					if (have < need) {
						reasons.push(
							fmt(t("Watch more of {0} — {1}% of {2}% so far."), [
								block.heading || t("the video"),
								have,
								need,
							])
						);
					}
				} else if (block.type === "PDF" || block.type === "Downloadable File") {
					// `ack` is an extension the server may not record yet. Absent
					// means "not tracked", not "not done" — a learner must never be
					// stuck on a gate nobody is evaluating.
					if (stored.ack === 0) {
						reasons.push(
							fmt(t("Confirm you have read {0}."), [block.heading || t("the document")])
						);
					}
				}
			});

			var quiz = (lesson.quiz || {});
			if (quiz.enabled) {
				var runs = (lessonProgress(state.lessonKey).quiz || {});
				var best = Number(runs.best || 0);
				var need = pct(quiz.pass_score || (state.gates && state.gates.passing_score) || 0);
				if (!runs.runs || best < need) {
					reasons.push(t("Pass the quiz to finish this lesson."));
				}
			}
			return { ok: reasons.length === 0, reasons: reasons };
		}

		function lessonPercent() {
			var lesson = state.lesson;
			if (!lesson) return 0;
			var required = (lesson.blocks || []).filter(function (block) {
				return block.required;
			});
			if (!required.length) return lessonProgress(state.lessonKey).status === "done" ? 100 : 0;
			var done = 0;
			required.forEach(function (block) {
				var stored = blockProgress(block.block_key);
				if (block.type === "Video") {
					var need = pct(block.min_coverage || (state.gates && state.gates.min_video_coverage) || 0);
					if (!need || pct((stored.cov || 0) * 100) >= need) done += 1;
				} else if (block.type === "PDF" || block.type === "Downloadable File") {
					if (stored.ack !== 0) done += 1;
				} else {
					done += 1;
				}
			});
			return Math.round((done / required.length) * 100);
		}

		// ------------------------------------------------------------- block ctx

		// The single object every block renderer and TR.Video sees. It is the
		// only route a block has to the network, which is what keeps the
		// transport seam intact all the way down.
		function blockContext() {
			return {
				transport: api,
				attempt: state.attempt,
				course: state.course,
				lesson: state.lesson,
				lessonKey: state.lessonKey,
				settings: b.settings || {},
				t: t,
				blockProgress: blockProgress,
				// `{block_key: at_seconds}` from get_lesson. Written since Phase 2 and
				// read by nothing until TASK-2026-01177 — see the seeding comment in
				// video.js for the hole it closes.
				nextCheckpoints: state.nextCheckpoints || {},
				heartbeat: function (beat) {
					var body = beat || {};
					// Never taken from the caller: the server derives the learner
					// from the session, and the attempt from the lesson it is on.
					body.lesson_key = state.lessonKey;
					// The endpoint is `heartbeat(attempt, payload)`, so the beat has
					// to travel UNDER `payload` — not spread across the top level.
					// Spread, Frappe bound `attempt` and left `payload` at its
					// default of None, the server recorded an empty beat, and watch
					// coverage sat at 0% forever. Nothing errored: an empty beat is
					// a perfectly valid beat that happens to credit nothing.
					return call("heartbeat", { attempt: state.attempt, payload: body }).then(function (response) {
						mergeHeartbeat(body.block_key, response);
						renderBottomBar();
						return response || {};
					});
				},
				// blocks.js and video.js both want a URL STRING here. get_media_url
				// returns {url, embed_url, reason, poster, duration_seconds, ...}, so
				// this has to unwrap it — handing the object straight back set
				// img.src to "[object Object]". It also sent `lesson_key`, which is
				// not a parameter, and omitted `attempt`, which is required.
				mediaUrl: function (block) {
					return call("mediaUrl", {
						attempt: state.attempt,
						block_key: block.block_key,
					})
						.then(function (media) {
							if (!media) return "";
							if (typeof media === "string") return media;
							// `reason` is the server explaining why there is no URL —
							// an unverified duration, a retired asset. Worth saying out
							// loud rather than rendering an empty block.
							if (!media.url && !media.embed_url && media.reason) {
								console.warn("training: no media for " + block.block_key + " — " + media.reason);
							}
							return media.url || media.embed_url || "";
						})
						.catch(function (err) {
							// Swallowed so one dead asset cannot take the lesson down,
							// but never silently: a blank video block with nothing in
							// the console is undebuggable.
							console.error("training: media lookup failed for " + block.block_key, err);
							return "";
						});
				},
				requestGateRefresh: renderBottomBar,
				onTeardown: function (fn) {
					teardowns.push(fn);
				},
				onFlush: function (fn) {
					flushers.push(fn);
				},
			};
		}

		// ------------------------------------------------------------------ view

		function go(view) {
			runTeardowns();
			state.view = view;
			clear(head);
			clear(main);
			clear(foot);
			head.classList.remove("is-sticky");
			route();
			if (view === "unavailable") renderUnavailable();
			else if (view === "catalog") renderCatalog();
			else if (view === "course") renderCourse();
			else if (view === "lesson") renderLesson();
			else if (view === "quiz") renderQuiz();
			else if (view === "results") renderResults();
			else if (view === "signoff") renderSignoff();
			else if (view === "complete") renderComplete();
			else if (view === "record") renderRecord();
			// A light entrance so a view change reads as a transition, not a cut.
			// .is-entering is a state class (exempt from the CSS class contract) and
			// player.css disables it under prefers-reduced-motion. The forced reflow
			// between remove and add restarts the animation on every view, not just
			// the first paint.
			main.classList.remove("is-entering");
			void main.offsetWidth;
			main.classList.add("is-entering");
			// Moving to a new view is a navigation; a screen reader should be told
			// where it landed rather than left on the button that was pressed.
			main.setAttribute("tabindex", "-1");
			main.focus({ preventScroll: true });
			window.scrollTo(0, 0);
		}

		// Blanks the whole shell and shows a spinner line. Used instead of `go()`
		// while a fetch is in flight: rendering the target view first would paint
		// the *previous* course's outline for a beat, which reads as the wrong
		// course having opened.
		function loading(message) {
			runTeardowns();
			clear(head);
			clear(main);
			clear(foot);
			head.classList.remove("is-sticky");
			// A shape-matched skeleton rather than a blank spinner: a title bar and a
			// few card placeholders that shimmer, so a hop between views reads as the
			// next screen loading rather than the current one emptying. The status
			// text stays for screen readers, which get nothing from the shimmer.
			var wrap = el("div", "tr-skeleton");
			wrap.setAttribute("role", "status");
			wrap.setAttribute("aria-live", "polite");
			wrap.appendChild(el("span", "tr-sr-only", message || t("Loading…")));
			wrap.appendChild(skel("42%", "28px"));
			for (var i = 0; i < 3; i++) wrap.appendChild(skel("100%", "72px"));
			main.appendChild(wrap);
		}

		function skel(width, height) {
			var node = el("div", "tr-skel");
			node.style.width = width;
			node.style.height = height;
			node.setAttribute("aria-hidden", "true");
			return node;
		}

		// --------------------------------------------------------------- catalog

		// The module is switched off, and says so in the server's own words.
		//
		// `Training Settings.training_enabled` is the staged-rollout switch, and
		// the server has always answered a dormant site with
		// `{enabled: false, message}` (api/training._unavailable). The player never
		// read either key, so it fell through to the catalogue and told every
		// visitor "Nothing is assigned to you right now" — which is a statement
		// about that person, is wrong, and is the one sentence guaranteed to stop
		// them asking why. A deliberately dormant module should say it is dormant.
		function renderUnavailable() {
			head.appendChild(el("h1", "tr-title", t("Training")));
			// The server's message, not one invented here: it is the only side that
			// knows *why* — not open yet, or turned off for maintenance — and a
			// second copy of that sentence in the client is a second thing to keep
			// true. The fallback exists only for a payload with no message at all.
			main.appendChild(
				el("p", "tr-empty", b.message || t("Training is not available yet."))
			);
		}

		function renderCatalog() {
			head.appendChild(el("h1", "tr-title", t("Your training")));

			// Announcements first — a pinned notice is the most important thing on the
			// page. Author/manager-posted, scoped to everyone, a course, or a batch.
			var announcements = announcementBlock();
			if (announcements) main.appendChild(announcements);

			// The learner's own points / streak / badges, when they have earned any.
			// Server-fed on the boot payload; hidden for a clean slate rather than
			// shown as a row of zeros (see youStrip).
			var strip = youStrip();
			if (strip) main.appendChild(strip);

			// The cohort(s) this learner is part of, when they are in any. Shown above
			// the courses because it is context for them — the assigned cards below are
			// the work; this says which group owes it. Rendered before the empty-catalog
			// early return so a learner in a cohort always sees it.
			var cohorts = cohortBlock();
			if (cohorts) main.appendChild(cohorts);

			// Upcoming / live sessions for those cohorts, with the join link. Above the
			// cards too: a session starting soon is the most time-sensitive thing here.
			var sessions = liveClassBlock();
			if (sessions) main.appendChild(sessions);

			// Practical evaluations booked for this learner — a supervisor watching them
			// do the thing. Shown here too so a booked slot is not buried below the cards.
			var evaluations = evaluationBlock();
			if (evaluations) main.appendChild(evaluations);

			// The learner's own work submissions and grades. A "Needs rework" is the one
			// thing on this page that is waiting on them, so it sits above the cards too.
			var submissions = submissionBlock();
			if (submissions) main.appendChild(submissions);

			// `assigned` and `library`, which is what get_learner_bootstrap actually
			// returns. This read `b.courses` and `b.catalog.courses` -- neither of
			// which the server has ever sent -- so the page reported "nothing is
			// assigned to you" to every learner, always, however much was assigned.
			// The server separates the two deliberately: assigned work is owed, the
			// library is optional, and merging them buries a due course among
			// things nobody has to do.
			var assigned = b.assigned || [];
			var library = b.library || [];

			if (!assigned.length && !library.length) {
				main.appendChild(
					el("p", "tr-empty", t("Nothing is assigned to you right now, and nothing is overdue."))
				);
				main.appendChild(recordOpen());
				main.appendChild(renderLeaderboard());
				return;
			}

			if (assigned.length) {
				var grid = el("div", "tr-cards");
				assigned.forEach(function (course) {
					grid.appendChild(courseCard(course));
				});
				main.appendChild(grid);
			} else {
				main.appendChild(el("p", "tr-empty", t("Nothing is assigned to you right now.")));
			}

			if (library.length) {
				main.appendChild(el("h2", "tr-section-title", t("Available to you")));
				var shelf = el("div", "tr-cards");
				library.forEach(function (course) {
					shelf.appendChild(courseCard(course));
				});
				main.appendChild(shelf);
			}

			main.appendChild(recordOpen());
			main.appendChild(renderLeaderboard());
		}

		// ------------------------------------------------------------------ the board
		//
		// gamification.py has computed points, badges and streaks since v1.215.0 --
		// awarded on completion, decayed nightly -- and its one whitelisted function
		// had no caller. Every one of those numbers existed and none of them was ever
		// shown to the person who earned it.

		var boardState = { open: false, busy: false, data: null, error: null };
		var boardWrap = null;
		var qaWrap = null;

		// The two lazy panels — the leaderboard and the "Ask the author" Q&A — were
		// each written to call a bare `render()` on every state change, and it was
		// never defined: from the day each was wired, every Leaderboard / Ask tap threw
		// `render is not defined` in the console and the panel never updated (reported
		// from prod against the live player). This is that function. It re-renders
		// whichever panel is currently mounted, IN PLACE — never the whole view, so a
		// question asked mid-lesson does not tear down and re-mount the video. Only one
		// panel is ever mounted at a time (the board lives on the catalog, the Q&A on
		// the lesson); the other's node has been cleared by go() and its parentNode is
		// null, so it is skipped.
		function render() {
			if (boardWrap && boardWrap.parentNode) {
				var oldBoard = boardWrap;
				oldBoard.parentNode.replaceChild(renderLeaderboard(), oldBoard);
			}
			if (qaWrap && qaWrap.parentNode) {
				var oldQa = qaWrap;
				oldQa.parentNode.replaceChild(renderQuestions(state.lesson || {}), oldQa);
			}
		}

		function renderLeaderboard() {
			var wrap = el("div", "tr-board");
			// Captured so render() can swap this exact node in place (see render()).
			boardWrap = wrap;

			// `enabled: false` renders NOTHING, not an empty panel. The feature ships
			// off (`gamification_enabled`), and a permanently empty "Leaderboard"
			// heading on every learner's page is worse than its absence: it reads as
			// broken rather than as switched off.
			if (boardState.data && boardState.data.enabled === false) return wrap;

			var toggle = button(
				t("Leaderboard"),
				"tr-button tr-button-quiet tr-board-toggle",
				function () {
					boardState.open = !boardState.open;
					// Lazily, like the lesson Q&A panel and for the same reason: the
					// catalog is the first screen, and it is opened on phones on site.
					if (boardState.open && !boardState.data && !boardState.busy) loadBoard();
					else render();
				}
			);
			toggle.setAttribute("aria-expanded", boardState.open ? "true" : "false");
			toggle.setAttribute("aria-controls", "board-region");
			wrap.appendChild(toggle);

			var region = el("div", "tr-board-body");
			region.id = "board-region";
			if (!boardState.open) {
				region.hidden = true;
				wrap.appendChild(region);
				return wrap;
			}

			if (boardState.busy) region.appendChild(el("p", "tr-muted", t("Loading…")));
			if (boardState.error) fail(region, boardState.error);

			var rows = (boardState.data && boardState.data.rows) || [];
			if (!boardState.busy && !boardState.error && !rows.length) {
				region.appendChild(el("p", "tr-muted", t("Nobody has finished a course yet.")));
			}

			if (rows.length) {
				var table = el("table", "tr-board-table");
				var head_ = el("tr");
				[t("#"), t("Name"), t("Points"), t("Courses"), t("Badges"), t("Streak")].forEach(
					function (label) {
						head_.appendChild(el("th", null, label));
					}
				);
				table.appendChild(el("thead", null)).appendChild(head_);
				var body = el("tbody");
				rows.forEach(function (row) {
					// `is_me` is decided by the server against the session user, never by
					// comparing names here — two people share a name far more often than
					// anyone expects, and the row a learner looks for is their own.
					var tr = el("tr", row.is_me ? "is-me" : null);
					[
						row.rank,
						row.full_name,
						row.points,
						row.courses_completed,
						row.badges_earned,
						fmt(t("{0} d"), [row.current_streak_days]),
					].forEach(function (value) {
						tr.appendChild(el("td", null, value));
					});
					body.appendChild(tr);
				});
				table.appendChild(body);
				region.appendChild(table);
			}

			wrap.appendChild(region);
			return wrap;
		}

		function loadBoard() {
			boardState.busy = true;
			render();
			// No `scope` argument. It is a convenience the server resolves — a manager
			// gets the board they ask for, everybody else their own — and sending one
			// from here would look like the client choosing, which it never does.
			return call("leaderboard", {})
				.then(function (data) {
					boardState.busy = false;
					boardState.data = data || {};
					boardState.error = null;
					render();
				})
				.catch(function (err) {
					boardState.busy = false;
					boardState.error = err;
					render();
				});
		}

		function courseCard(course) {
			var card = el("article", "tr-card");
			if (course.cover_image) {
				var cover = el("img", "tr-card-cover");
				cover.src = course.cover_image;
				cover.alt = "";
				cover.loading = "lazy";
				cover.decoding = "async";
				card.appendChild(cover);
			}

			var body = el("div", "tr-card-body");
			body.appendChild(el("h2", "tr-card-title", course.title || course.course));

			var chips = el("div", "tr-card-chips");
			chips.appendChild(chip(course.weight === "Required" ? t("Required") : t("Optional"),
				course.weight === "Required" ? "required" : "optional"));
			var due = dueChip(course);
			if (due) chips.appendChild(due);
			// `minutes`, not `estimated_minutes` — that is the DocType's field name,
			// not the card's. The fallback made it work while reading as though the
			// server might send either.
			var minutes = course.minutes;
			if (minutes) chips.appendChild(chip(fmt(t("{0} min"), [minutes])));
			body.appendChild(chips);

			if (course.summary) body.appendChild(el("p", "tr-card-summary", course.summary));

			// percent_complete and assignment_status are the server's names. Reading
			// `progress_percent` and `status` meant the bar never appeared and the
			// action always read "Start", even mid-course.
			var progress = pct(course.percent_complete);
			if (progress > 0) {
				body.appendChild(meter(progress, t("Course progress")));
				body.appendChild(el("p", "tr-card-progress", fmt(t("{0}% done"), [progress])));
			}

			var action = el("div", "tr-card-actions");
			if (course.assignment_status === "Awaiting Sign-off") {
				action.appendChild(chip(t("Waiting for your supervisor"), "pending"));
			}
			var verb =
				course.assignment_status === "Completed"
					? t("Review")
					: progress > 0 || course.assignment_status === "In Progress"
						? t("Resume")
						: t("Start");
			action.appendChild(
				button(verb, "tr-button tr-button-primary", function () {
					// `course`, not `name` — the card's own identifier field. The
					// `|| course.name` fallback was dead and read as though the
					// server might send either.
					openCourse(course.course);
				})
			);
			body.appendChild(action);
			card.appendChild(body);
			return card;
		}

		function dueChip(course) {
			if (!course.due_date) return null;
			var days = daysUntil(course.due_date);
			if (days == null) return null;
			if (days < 0) return chip(fmt(t("Overdue by {0} days"), [Math.abs(days)]), "overdue");
			if (days === 0) return chip(t("Due today"), "due-soon");
			if (days <= 7) return chip(fmt(t("Due in {0} days"), [days]), "due-soon");
			return chip(fmt(t("Due {0}"), [course.due_date]), "due");
		}

		// ---------------------------------------------------------------- course

		function openCourse(courseName, lessonKey) {
			state.courseName = courseName;
			state.view = "course";
			loading(t("Opening the course…"));
			load(courseName, lessonKey)
				.then(function () {
					// The server said where they were; honour it rather than
					// guessing from anything cached in this browser.
					go(lessonKey ? "lesson" : "course");
				})
				.catch(function (err) {
					clear(main);
					fail(main, err);
					main.appendChild(button(t("Back"), "tr-button", function () {
						go("catalog");
					}));
				});
		}

		// Two calls, not one, because that is the API that exists.
		//
		// This used to be a single `getLesson({course, lesson_key})` that was
		// expected to open the course, mint an attempt and return the lesson all at
		// once. No such endpoint was ever written. The real runtime keeps the attempt
		// explicit: `get_course` describes the course and hands back the learner's
		// open attempt if they have one, `start_attempt` mints one if they do not,
		// and every call after that carries `attempt`. The old shape 500'd on the
		// first click of any course, because `attempt` is a required argument and it
		// was sending `course` instead.
		function load(courseName, lessonKey) {
			setBusy(true);
			return call("getCourse", { course: courseName })
				.then(function (payload) {
					payload = payload || {};
					state.course = payload.course || state.course;
					state.courseName = (payload.course && payload.course.course) || courseName;
					state.outline = payload.toc || [];
					state.chapters = payload.chapters || [];
					state.gates = payload.gates || {};
					state.assignment = payload.assignment || null;
					state.version = payload.version || null;
					return payload.attempt || null;
				})
				.then(function (attempt) {
					// An attempt is only started when the learner is actually going
					// into a lesson. Opening a course to look at its outline must not
					// mint one — that would mark the assignment In Progress for
					// somebody who only glanced at it.
					if (attempt) return attempt;
					if (!lessonKey && !state.lessonKey) return null;
					return call("startAttempt", { course: courseName });
				})
				.then(function (attempt) {
					adoptAttempt(attempt);
					var wanted = lessonKey || state.lessonKey;
					if (!state.attempt || !wanted) {
						setBusy(false);
						return {};
					}
					return call("getLesson", { attempt: state.attempt, lesson_key: wanted })
						.then(function (payload) {
							payload = payload || {};
							adoptAttempt(payload.attempt);
							if (payload.lesson) {
								state.lesson = payload.lesson;
								state.lessonKey = payload.lesson.lesson_key || wanted;
							}
							// MERGE, never assign. `get_lesson` sends the progress of the
							// ONE lesson it was asked for — {status, blocks, checkpoints,
							// quiz} — and this slot holds the whole {lessons: {...}} map
							// adopted at attempt start. Assigning one over the other left
							// `state.progress.lessons` undefined, so `lessonProgress()`
							// returned {} for every lesson including the one just opened,
							// and the outline showed a course the learner had half
							// finished as entirely not started. Opening lesson B forgot
							// lesson A; opening A again forgot B.
							//
							// Merging on the client rather than reshaping the endpoint:
							// `mergeHeartbeat` a few lines up already folds a single
							// block's reply into this same map the same way, so this is
							// the shape the file already speaks.
							var key = state.lessonKey || wanted;
							var lessons = (state.progress.lessons = state.progress.lessons || {});
							if (payload.progress) lessons[key] = payload.progress;
							state.nextCheckpoints = payload.next_checkpoints || {};
							setBusy(false);
							return payload;
						});
				})
				.catch(function (err) {
					setBusy(false);
					throw err;
				});
		}

		// `attempt` arrives either as the _attempt_state dict or, from get_lesson, as
		// the bare name. Normalise once so nothing downstream has to care which.
		function adoptAttempt(attempt) {
			if (!attempt) return;
			if (typeof attempt === "string") {
				state.attempt = attempt;
				return;
			}
			state.attempt = attempt.attempt || state.attempt;
			if (attempt.status) state.status = attempt.status;
			// Carried separately from the attempt's own status because they are
			// different records with different vocabularies; see renderSignoff.
			if (course.assignment_status) state.assignmentStatus = course.assignment_status;
			if (course.signoff_with) state.signoffWith = course.signoff_with;
			// The attempt carries the per-lesson progress map, and the course view
			// needs it: without this `state.progress` is only ever populated by
			// get_lesson, so the outline had nothing to read and every lesson showed
			// as not started even after it was finished.
			if (attempt.lessons) {
				state.progress = state.progress || {};
				state.progress.lessons = attempt.lessons;
			}
			if (attempt.next_lesson_key && !state.lessonKey) {
				state.lessonKey = attempt.next_lesson_key;
			}
		}

		function renderCourse() {
			var course = state.course || {};
			var bar = el("div", "tr-subhead-row");
			bar.appendChild(button("← " + t("All courses"), "tr-button tr-button-quiet", function () {
				go("catalog");
			}));
			bar.appendChild(el("h1", "tr-title", course.title || state.courseName || ""));
			head.appendChild(bar);

			if (course.summary) main.appendChild(el("p", "tr-course-summary", course.summary));
			// The ASSIGNMENT's status, not the attempt's. Training Attempt.status is
			// In Progress / Passed / Failed / Abandoned -- "Awaiting Sign-off" is not
			// one of its options and never was, so testing state.status against it
			// here could not have matched whatever the server wrote. That is a
			// separate bug from nothing setting the status, and it would have
			// outlived the fix for it.
			if (state.assignmentStatus === "Awaiting Sign-off") {
				go("signoff");
				return;
			}

			var list = el("ol", "tr-outline");
			var lastChapter = null;
			(state.outline || []).forEach(function (row, index) {
				if (row.chapter_title && row.chapter_title !== lastChapter) {
					lastChapter = row.chapter_title;
					list.appendChild(el("li", "tr-outline-chapter", lastChapter));
				}
				list.appendChild(outlineRow(row, index));
			});
			main.appendChild(list);

			var resumeKey = (state.resume && state.resume.lesson_key) || firstOpenLesson();
			if (resumeKey) {
				var resumeRow = rowFor(resumeKey);
				var resumeLabel = state.resume && state.resume.lesson_key ? t("Resume: {0}") : t("Start: {0}");
				foot.appendChild(
					button(
						fmt(resumeLabel, [(resumeRow && resumeRow.title) || ""]),
						"tr-button tr-button-primary tr-button-wide",
						function () {
							openLesson(resumeKey);
						}
					)
				);
			}
		}

		function rowFor(lessonKey) {
			var found = null;
			(state.outline || []).forEach(function (row) {
				if (row.lesson_key === lessonKey) found = row;
			});
			return found;
		}

		function firstOpenLesson() {
			var key = null;
			(state.outline || []).forEach(function (row) {
				if (key || row.locked) return;
				if (row.status !== "done") key = row.lesson_key;
			});
			return key;
		}

		// A toc row is {lesson_key, chapter_key, title, minutes, has_quiz, blocks} —
		// and nothing else. It carries no status, so this derives one from the
		// attempt's own progress map. It used to read `row.status` and `row.locked`
		// straight off the row: both were always undefined, so every lesson showed
		// the "not started" circle and offered "Open" even after it had been
		// finished, and the learner had no way to see how far through they were.
		//
		// There is no locking. `_next_lesson_key` recommends an order; it does not
		// enforce one, and the outline deliberately lets a learner open any lesson.
		// The old `row.locked` branch was UI for a feature the server does not have.
		function lessonStatus(lessonKey) {
			var progress = lessonProgress(lessonKey);
			if (progress.status === "done") return "done";
			// Anything recorded at all — a block watched, a quiz attempted — means
			// they have been in here.
			if (progress.blocks || progress.quiz || progress.checkpoints) return "in_progress";
			return "not_started";
		}

		function outlineRow(row, index) {
			var status = lessonStatus(row.lesson_key);
			var item = el("li", "tr-outline-row is-" + status.replace("_", "-"));
			var glyph = status === "done" ? "✓" : status === "in_progress" ? "◐" : "○";
			var mark = el("span", "tr-outline-glyph", glyph);
			mark.setAttribute("aria-hidden", "true");
			item.appendChild(mark);

			var text = el("div", "tr-outline-text");
			text.appendChild(
				el("span", "tr-outline-title", row.title || fmt(t("Lesson {0}"), [index + 1]))
			);
			var meta = el("div", "tr-outline-meta");
			meta.appendChild(el("span", "tr-sr-only",
				status === "done" ? t("Finished")
					: status === "in_progress" ? t("In progress")
						: t("Not started")));
			if (row.minutes) meta.appendChild(chip(fmt(t("{0} min"), [row.minutes])));
			if (row.has_quiz) meta.appendChild(chip(t("Quiz")));
			text.appendChild(meta);

			item.appendChild(text);

			item.appendChild(
				button(status === "done" ? t("Review") : status === "in_progress" ? t("Resume") : t("Open"),
					"tr-button tr-button-quiet", function () {
						openLesson(row.lesson_key);
					})
			);
			return item;
		}

		// ---------------------------------------------------------------- lesson

		function openLesson(lessonKey) {
			state.lessonKey = lessonKey;
			state.view = "lesson";
			loading(t("Opening the lesson…"));
			load(state.courseName, lessonKey)
				.then(function () {
					go("lesson");
				})
				.catch(function (err) {
					clear(main);
					fail(main, err);
					main.appendChild(button(t("Back to the course"), "tr-button", function () {
						go("course");
					}));
				});
		}

		function renderLesson() {
			var lesson = state.lesson;
			if (!lesson) {
				loading(t("Opening the lesson…"));
				return;
			}

			var bar = el("div", "tr-subhead-row");
			bar.appendChild(button("← " + t("Course"), "tr-button tr-button-quiet", function () {
				go("course");
			}));
			bar.appendChild(el("h1", "tr-title", lesson.title || ""));
			head.appendChild(bar);
			head.appendChild(meter(lessonPercent(), t("Lesson progress")));
			head.classList.add("is-sticky");

			if (lesson.summary) main.appendChild(el("p", "tr-lesson-summary", lesson.summary));

			var column = el("div", "tr-blocks");
			var ctx = blockContext();
			(lesson.blocks || []).forEach(function (block) {
				column.appendChild(TR.renderBlock(block, ctx));
			});
			main.appendChild(column);

			// A lesson that asks for a hand-in (requires_submission) shows the submit
			// box between the content and the Q&A. Its "already submitted / graded"
			// state comes from b.submissions, so no extra round-trip on lesson render.
			if (intOf(lesson.requires_submission)) {
				main.appendChild(renderSubmission(lesson));
			}

			main.appendChild(renderQuestions(lesson));

			renderBottomBar();
		}

		// ------------------------------------------------------------------ ask the author
		//
		// training/qa.py has held this entire feature -- the visibility gate, the author
		// resolution, the notification -- since v1.215.0, and had NO caller anywhere in the
		// repo until v1.303.0. The backend was complete and the learner had no way to reach
		// it, so the feature shipped and then did nothing.

		var qaState = { lessonKey: null, open: false, busy: false, data: null, error: null };

		// The lesson-view work-submission box (WI-071 Phase F). Reset per lesson, same
		// as qaState: a different lesson is a different hand-in.
		var submitState = { lessonKey: null, busy: false, error: null };

		function renderSubmission(lesson) {
			var key = lesson.lesson_key || "";
			if (submitState.lessonKey !== key) {
				submitState = { lessonKey: key, busy: false, error: null };
			}

			var wrap = el("section", "tr-submit");
			wrap.appendChild(el("h2", "tr-submit-title", t("Submit your work")));
			wrap.appendChild(
				el("p", "tr-submit-intro", t("This lesson asks you to hand in your work. A trainer will review it."))
			);

			// The latest hand-in for this lesson, from the boot payload. Shows the
			// learner where they stand before the form: passed, waiting, or sent back.
			var latest = latestSubmissionFor(key);
			if (latest) wrap.appendChild(submissionRow(latest));

			// Passed is terminal — the work is accepted, so no form to submit again.
			if (latest && latest.status === "Passed") return wrap;

			if (latest && (latest.status === "Submitted" || latest.status === "Under Review")) {
				wrap.appendChild(
					el("p", "tr-muted", t("Your work is in and waiting to be graded. You can send an updated version if you need to."))
				);
			}

			wrap.appendChild(renderSubmitForm(lesson, key));
			if (submitState.error) fail(wrap, submitState.error);
			return wrap;
		}

		function renderSubmitForm(lesson, key) {
			var form = el("div", "tr-submit-form");
			var slug = String(key || "lesson").replace(/[^A-Za-z0-9_-]/g, "-");

			var fileId = "submit-file-" + slug;
			var fileLabel = el("label", "tr-submit-label", t("Attach your file"));
			fileLabel.setAttribute("for", fileId);
			var fileInput = el("input", "tr-submit-file");
			fileInput.type = "file";
			fileInput.id = fileId;
			form.appendChild(fileLabel);
			form.appendChild(fileInput);

			var noteId = "submit-note-" + slug;
			var noteLabel = el("label", "tr-submit-label", t("Add a note (optional)"));
			noteLabel.setAttribute("for", noteId);
			var note = el("textarea", "tr-submit-note");
			note.rows = 2;
			note.id = noteId;
			form.appendChild(noteLabel);
			form.appendChild(note);

			var hint = el("p", "tr-submit-hint");
			hint.setAttribute("role", "status");
			form.appendChild(hint);

			var send = button(t("Submit work"), "tr-button tr-button-primary", function () {
				if (submitState.busy) return;
				var file = fileInput.files && fileInput.files[0];
				var text = (note.value || "").trim();
				if (!file && !text) {
					// Not a server round-trip's worth of error: say it in place and stop.
					hint.textContent = t("Attach a file or write a note first.");
					return;
				}
				submitState.busy = true;
				submitState.error = null;
				render();

				var upload = file ? call("uploadFile", file) : Promise.resolve("");
				upload
					.then(function (fileUrl) {
						var url = fileUrl || "";
						return call("submitWork", {
							course: state.course && state.course.name,
							lesson_key: key,
							file: url,
							text: text,
						}).then(function () {
							// The reply's own keys are ignored on purpose, the way
							// askQuestion ignores its thread id: a fresh hand-in is always
							// "Submitted", and the record's name is never shown. Reading them
							// off the reply would put keys on the wire the boundary contract
							// cannot see through the delegating endpoint. So the optimistic
							// row is built from what we already know.
							return url;
						});
					})
					.then(function (url) {
						submitState.busy = false;
						recordSubmitted(lesson, key, url);
						render();
					})
					.catch(function (err) {
						submitState.busy = false;
						submitState.error = err;
						render();
					});
			});
			if (submitState.busy) {
				send.disabled = true;
				send.textContent = t("Submitting…");
			}
			form.appendChild(send);
			return form;
		}

		// Fold a just-made submission into b.submissions so the box and the home strip
		// both reflect it immediately, without waiting for the next boot. The server is
		// the authority on the real record; this is an optimistic echo of what it just
		// accepted (always "Submitted" — a fresh hand-in is never pre-graded).
		function recordSubmitted(lesson, key, fileUrl) {
			if (!b.submissions) b.submissions = [];
			b.submissions.unshift({
				name: "",
				course_title: (state.course && state.course.title) || (state.course && state.course.name) || "",
				lesson_key: key,
				lesson_title: lesson.title || "",
				status: "Submitted",
				grade: "",
				feedback: "",
				file: fileUrl || "",
				submitted_on: t("just now"),
				graded_on: "",
			});
		}

		function renderQuestions(lesson) {
			var key = lesson.lesson_key || "";
			if (qaState.lessonKey !== key) {
				// A different lesson answers a different question. Keeping the old payload
				// would show one lesson's threads under another's heading, which reads as
				// data loss rather than as a stale cache.
				qaState = { lessonKey: key, open: false, busy: false, data: null, error: null };
			}

			var wrap = el("div", "tr-qa");
			// Captured so render() can swap just this panel, leaving the video alone.
			qaWrap = wrap;
			var slug = String(key || "lesson").replace(/[^A-Za-z0-9_-]/g, "-");
			var regionId = "qa-region-" + slug;

			var toggle = button(
				t("Ask the author"),
				"tr-button tr-button-quiet tr-qa-toggle",
				function () {
					qaState.open = !qaState.open;
					// Fetch on first open, never on lesson render: otherwise this is an extra
					// round trip per lesson on a portal that is opened on phones, on site.
					if (qaState.open && !qaState.data && !qaState.busy) loadQuestions(key);
					else render();
				}
			);
			toggle.setAttribute("aria-expanded", qaState.open ? "true" : "false");
			toggle.setAttribute("aria-controls", regionId);
			wrap.appendChild(toggle);

			var region = el("div", "tr-qa-body");
			region.id = regionId;
			if (!qaState.open) {
				region.hidden = true;
				wrap.appendChild(region);
				return wrap;
			}

			if (qaState.busy) region.appendChild(el("p", "tr-muted", t("Loading…")));
			if (qaState.error) fail(region, qaState.error);

			var data = qaState.data || {};
			if (data.enabled === false) {
				region.appendChild(el("p", "tr-muted", t("Questions are not available here.")));
				wrap.appendChild(region);
				return wrap;
			}

			region.appendChild(renderAskBox(key));

			var mine = data.mine || [];
			if (mine.length) {
				region.appendChild(el("h2", "tr-qa-heading", t("Your questions")));
				mine.forEach(function (row) {
					region.appendChild(renderThread(row, true));
				});
			}

			var shared = data.public || [];
			if (shared.length) {
				region.appendChild(el("h2", "tr-qa-heading", t("Answers for everyone")));
				shared.forEach(function (row) {
					region.appendChild(renderThread(row, false));
				});
			}

			if (!qaState.busy && !mine.length && !shared.length) {
				region.appendChild(el("p", "tr-muted", t("No questions on this lesson yet.")));
			}

			wrap.appendChild(region);
			return wrap;
		}

		function renderAskBox(key) {
			var form = el("div", "tr-qa-ask");
			var fieldId = "qa-ask-" + String(key || "lesson").replace(/[^A-Za-z0-9_-]/g, "-");
			var label = el("label", "tr-qa-label", t("Ask a question about this lesson"));
			label.setAttribute("for", fieldId);
			var field = el("textarea", "tr-qa-input");
			field.rows = 3;
			field.id = fieldId;
			form.appendChild(label);
			form.appendChild(field);

			form.appendChild(
				button(t("Send to the author"), "tr-button tr-button-primary", function () {
					var text = (field.value || "").trim();
					if (!text) return;
					qaState.busy = true;
					qaState.error = null;
					var args = {
						course: state.course && state.course.name,
						lesson_key: key,
						question: text,
					};
					// The video position is what turns "I don't understand this" into
					// something an author can act on. Omitted rather than sent as 0 when the
					// lesson has no video: 0 is a real timestamp and would point every text
					// question at the first frame.
					var at = currentVideoSecond();
					if (at != null) args.at_seconds = at;
					render();
					call("askQuestion", args)
						.then(function () {
							return loadQuestions(key);
						})
						.catch(function (err) {
							qaState.busy = false;
							qaState.error = err;
							render();
						});
				})
			);
			return form;
		}

		function renderThread(row, isMine) {
			var item = el("div", "tr-qa-thread");
			if (isMine) item.appendChild(el("p", "tr-qa-question", row.question || ""));
			if (row.answer) {
				item.appendChild(el("p", "tr-qa-answer", row.answer));
			} else if (isMine) {
				// The whole reason `mine` is a separate list: an unanswered question still
				// sitting there is the only way a learner knows that asking did anything.
				item.appendChild(el("p", "tr-muted", t("Waiting for an answer.")));
			}
			return item;
		}

		function currentVideoSecond() {
			var media = rootEl.querySelector("video");
			if (!media || typeof media.currentTime !== "number") return null;
			var at = Math.floor(media.currentTime);
			return at > 0 ? at : null;
		}

		function loadQuestions(key) {
			qaState.busy = true;
			render();
			return call("lessonQuestions", {
				course: state.course && state.course.name,
				lesson_key: key,
			})
				.then(function (data) {
					qaState.busy = false;
					qaState.data = data || {};
					qaState.error = null;
					render();
				})
				.catch(function (err) {
					qaState.busy = false;
					qaState.error = err;
					render();
				});
		}

		function renderBottomBar() {
			if (state.view !== "lesson") return;
			clear(foot);
			var lesson = state.lesson || {};
			var gates = localGates();

			var status = el("div", "tr-bottom-status");
			status.setAttribute("role", "status");
			status.setAttribute("aria-live", "polite");
			if (gates.reasons.length) {
				var list = el("ul", "tr-gate-reasons");
				gates.reasons.forEach(function (reason) {
					list.appendChild(el("li", null, reason));
				});
				status.appendChild(list);
			} else {
				status.appendChild(el("span", "tr-gate-ok", t("Ready to finish this lesson.")));
			}
			foot.appendChild(status);

			var actions = el("div", "tr-bottom-actions");
			var quiz = lesson.quiz || {};
			var runs = lessonProgress(state.lessonKey).quiz || {};
			var needsQuiz = quiz.enabled && (!runs.runs || pct(runs.best) < pct(quiz.pass_score));

			if (needsQuiz) {
				actions.appendChild(
					button(runs.runs ? t("Retake the quiz") : t("Start the quiz"),
						"tr-button tr-button-primary", startQuiz)
				);
			} else {
				var finishBtn = button(t("Finish this lesson"), "tr-button tr-button-primary", finishLesson);
				// Never hard-disabled: the local gate is a guess, and a disabled
				// button with no way to ask why is how a learner ends up emailing
				// support. Let them press it and let the server answer.
				if (!gates.ok) finishBtn.classList.add("is-tentative");
				actions.appendChild(finishBtn);
			}
			foot.appendChild(actions);
		}

		// The course-level counterpart of finishLesson. Separated because the two
		// refuse for different reasons: a lesson refuses on its own gates, a course
		// refuses because some OTHER lesson is unfinished, and telling a learner
		// "this lesson is not finished" when it is would send them looking in the
		// wrong place.
		function finishCourse() {
			setBusy(true);
			call("finishAttempt", { attempt: state.attempt })
				.then(function (result) {
					setBusy(false);
					result = result || {};
					if (result.status) state.status = result.status;
					if (result.assignment_status) state.assignmentStatus = result.assignment_status;
					if (result.signoff_with) state.signoffWith = result.signoff_with;

					// Not thrown, deliberately: the server reports what is left
					// rather than erroring, so the learner can be sent back to it.
					var outstanding = result.outstanding || [];
					if (!result.passed) {
						state.outstanding = outstanding;
						go("course");
						if (outstanding.length) {
							showRefusal(
								outstanding.map(function (row) {
									var why = (row.reasons || []).join(" ");
									return row.title ? row.title + ": " + why : why;
								})
							);
						}
						return;
					}

					state.completion = result.completion || null;
					state.result = {
						passed: true,
						score: result.score,
						completion: result.completion,
					};
					// Additive reward block (points, streak, new badges, certificate),
					// best-effort from the server; renderComplete shows only what came.
					state.reward = result.reward || {};
					// A course that wants hands-on verification is passed but not
					// finished — the sign-off view says who has to watch them. Reads
					// the assignment status for the reason given at the course view.
					// Otherwise the course is genuinely done: a real completion screen,
					// not the self-looping quiz-results view it used to land on.
					go(state.assignmentStatus === "Awaiting Sign-off" ? "signoff" : "complete");
				})
				.catch(function (err) {
					setBusy(false);
					clear(foot);
					fail(foot, err);
				});
		}

		function finishLesson() {
			setBusy(true);
			flush();
			call("completeLesson", { attempt: state.attempt, lesson_key: state.lessonKey })
				.then(function (result) {
					setBusy(false);
					result = result || {};
					if (!result.ok) {
						showRefusal(result.reasons || [t("This lesson is not finished yet.")]);
						return;
					}
					if (result.next_lesson_key) {
						openLesson(result.next_lesson_key);
						return;
					}
					// Last lesson done, so the COURSE has to be finished — and
					// nothing ever did this. `finish_attempt` is what checks every
					// gate across the whole course, writes the Training Completion,
					// issues the certificate and closes the assignment. Without it a
					// learner could complete every lesson and simply be returned to
					// the course page, with no record that they had passed anything.
					// `finishAttempt` was mapped in the transport from the first day
					// and called from nowhere.
					finishCourse();
				})
				.catch(function (err) {
					setBusy(false);
					clear(foot);
					fail(foot, err);
				});
		}

		function showRefusal(reasons) {
			clear(foot);
			var box = el("div", "tr-refusal");
			box.setAttribute("role", "alert");
			box.appendChild(el("p", null, t("Not quite finished:")));
			var list = el("ul", "tr-gate-reasons");
			(reasons || []).forEach(function (reason) {
				list.appendChild(el("li", null, reason));
			});
			box.appendChild(list);
			foot.appendChild(box);
			foot.appendChild(button(t("OK"), "tr-button", renderBottomBar));
		}

		// ------------------------------------------------------------------ quiz

		function startQuiz() {
			flush();
			go("quiz");
		}

		function renderQuiz() {
			var lesson = state.lesson || {};
			head.appendChild(
				button("← " + t("Back to the lesson"), "tr-button tr-button-quiet", function () {
					go("lesson");
				})
			);
			head.appendChild(el("h1", "tr-title", fmt(t("{0} — Quiz"), [lesson.title || ""])));

			var pending = el("div", "tr-loading", t("Drawing your questions…"));
			pending.setAttribute("role", "status");
			main.appendChild(pending);

			call("startQuiz", { attempt: state.attempt, lesson_key: state.lessonKey })
				.then(function (payload) {
					clear(main);
					state.quiz = payload || {};
					if (!TR.Quiz || typeof TR.Quiz.mount !== "function") {
						fail(main, new Error(t("The quiz did not load. Please refresh the page.")));
						return;
					}
					// TR.Quiz owns everything inside this view. It submits through
					// the callback below rather than the transport directly, so the
					// lesson key and run number are stamped in exactly one place and
					// a client-supplied score has nowhere to enter.
					// quiz.js's signature is `mount(root, ctx, transport)` where the
					// questions live at `ctx.quiz`. This used to pass the payload as
					// `ctx` itself and put everything else in the third argument, so
					// `normalise(ctx.quiz)` got undefined and the quiz rendered with
					// no questions, no attempt and no lesson key — and Submit went
					// nowhere, because quiz.js looks for `transport.submitQuiz`.
					//
					// The builder's preview patched the shapes together at runtime,
					// which is why this only ever failed for learners.
					TR.Quiz.mount(
						main,
						{
							quiz: state.quiz,
							attempt: state.attempt,
							lessonKey: state.lessonKey,
							onResult: function (result) {
								state.result = result || {};
								recordQuizRun(state.result);
								go("results");
							},
							onExit: function () {
								go("lesson");
							},
						},
						{
							// Submitting goes through here rather than the raw
							// transport so the lesson key and attempt are stamped in
							// exactly one place and a client-supplied score has
							// nowhere to enter. No `run`: submit_quiz takes
							// (attempt, lesson_key, answers) and derives it.
							submitQuiz: function (answers) {
								return call("submitQuiz", {
									attempt: state.attempt,
									lesson_key: state.lessonKey,
									answers: answers,
								});
							},
							startQuiz: function () {
								return call("startQuiz", {
									attempt: state.attempt,
									lesson_key: state.lessonKey,
								});
							},
						}
					);
				})
				.catch(function (err) {
					clear(main);
					fail(main, err);
					main.appendChild(button(t("Back to the lesson"), "tr-button", function () {
						go("lesson");
					}));
				});
		}

		function recordQuizRun(result) {
			var lessons = (state.progress.lessons = state.progress.lessons || {});
			var lesson = (lessons[state.lessonKey] = lessons[state.lessonKey] || {});
			var quiz = (lesson.quiz = lesson.quiz || { runs: 0, best: 0 });
			quiz.runs = (quiz.runs || 0) + 1;
			quiz.best = Math.max(Number(quiz.best || 0), Number(result.score || 0));
		}

		// --------------------------------------------------------------- results

		function renderResults() {
			var result = state.result || {};
			var passed = !!result.passed;
			head.appendChild(el("h1", "tr-title", passed ? t("Passed") : t("Not passed yet")));

			var summary = el("div", "tr-result" + (passed ? " is-passed" : " is-failed"));
			// No score is not a score of zero. `pct(undefined)` is 0, so this used
			// to state "0%" with complete confidence for any attempt whose score
			// did not reach it — which is exactly how a course passed at full marks
			// re-rendered as "Passed – 0%". The server now sends the recorded score
			// on every path, but an older attempt that predates score_percent still
			// has none, and saying nothing is the honest answer to not knowing.
			if (result.score != null) {
				summary.appendChild(el("div", "tr-result-score", pct(result.score) + "%"));
			}
			summary.appendChild(
				el("p", "tr-result-line",
					passed
						? t("You met the passing score for this lesson.")
						: t("You did not reach the passing score. You can try again."))
			);
			if (result.attempts_left != null) {
				summary.appendChild(
					el("p", "tr-result-line", fmt(t("{0} attempt(s) left."), [result.attempts_left]))
				);
			}
			main.appendChild(summary);

			var perQuestion = result.per_question || [];
			if (perQuestion.length) {
				var list = el("ol", "tr-result-questions");
				perQuestion.forEach(function (item) {
					var row = el("li", "tr-result-question" + (item.correct ? " is-correct" : " is-wrong"));
					var mark = el("span", "tr-result-glyph", item.correct ? "✓" : "✗");
					mark.setAttribute("aria-hidden", "true");
					row.appendChild(mark);
					row.appendChild(el("span", "tr-sr-only", item.correct ? t("Correct") : t("Wrong")));
					if (item.text) row.appendChild(el("p", "tr-result-text", item.text));
					// Only ever what the server chose to send back. The correct
					// option is not in this payload and must not be inferred here.
					if (item.explanation) row.appendChild(el("p", "tr-result-explanation", item.explanation));
					list.appendChild(row);
				});
				main.appendChild(list);
			}

			if (passed) {
				foot.appendChild(
					button(t("Continue"), "tr-button tr-button-primary", function () {
						state.result = null;
						go("lesson");
						finishLesson();
					})
				);
			} else {
				foot.appendChild(
					button(t("Try again"), "tr-button tr-button-primary", function () {
						state.result = null;
						go("quiz");
					})
				);
				foot.appendChild(
					button(t("Back to the lesson"), "tr-button", function () {
						state.result = null;
						go("lesson");
					})
				);
			}
		}

		// ------------------------------------------------------- learner stats + record
		//
		// The reward backend — Training Learner Stat, Training Badge Award, and the
		// get_my_transcript endpoint — shipped complete and unseen: points, streaks
		// and badges were computed on every completion and shown to the learner
		// nowhere, and the transcript method was mapped in the transport with no
		// caller. These surfaces show them. All server-fed and best-effort.

		// The home strip. null for a learner who has earned nothing, so the catalog
		// leads with courses rather than a row of zeros. statTile is shared with the
		// completion screen, so the two can never drift.
		function youStrip() {
			var stats = b.stats;
			if (!stats) return null;
			var points = intOf(stats.points);
			var streak = intOf(stats.streak_days);
			var badges = intOf(stats.badges_earned);
			if (!points && !streak && !badges) return null;
			var row = el("div", "tr-stat-row");
			row.appendChild(statTile(points, t("Points")));
			row.appendChild(statTile(streak, streak === 1 ? t("Day") : t("Days")));
			row.appendChild(statTile(badges, badges === 1 ? t("Badge") : t("Badges")));
			return row;
		}

		function intOf(value) {
			var n = Math.round(Number(value));
			return isFinite(n) && n > 0 ? n : 0;
		}

		function statTile(value, label) {
			var tile = el("div", "tr-stat");
			tile.appendChild(el("div", "tr-stat-num", String(value)));
			tile.appendChild(el("div", "tr-stat-label", label));
			return tile;
		}

		// The learner's active cohorts, server-fed on the boot payload (b.batches).
		// null/empty draws nothing — a learner in no batch sees exactly today's page.
		// The courses themselves are the assigned cards below; this only names the
		// group and how much it owes, so it never duplicates them.
		function cohortBlock() {
			var batches = b.batches;
			if (!batches || !batches.length) return null;
			var section = el("section", "tr-cohorts");
			section.appendChild(el("h2", "tr-section-title", t("Your cohorts")));
			batches.forEach(function (batch) {
				var row = el("div", "tr-cohort");
				row.appendChild(el("div", "tr-cohort-name", batch.title || batch.batch));
				var bits = [];
				var n = intOf(batch.course_count);
				bits.push(n === 1 ? t("1 course") : fmt(t("{0} courses"), [n]));
				if (batch.end_date) bits.push(fmt(t("due {0}"), [batch.end_date]));
				row.appendChild(el("div", "tr-cohort-meta", bits.join(" · ")));
				section.appendChild(row);
			});
			return section;
		}

		// Upcoming / live sessions for the learner's cohorts (b.live_classes). The
		// join link opens the meeting the manager set; the player never hosts video.
		// null/empty draws nothing.
		function liveClassBlock() {
			var sessions = b.live_classes;
			if (!sessions || !sessions.length) return null;
			var section = el("section", "tr-sessions");
			section.appendChild(el("h2", "tr-section-title", t("Upcoming live sessions")));
			sessions.forEach(function (session) {
				var row = el("div", "tr-session");
				var info = el("div", "tr-session-info");
				info.appendChild(el("div", "tr-session-title", session.title));
				var bits = [];
				if (session.batch_title) bits.push(session.batch_title);
				if (session.starts_on) bits.push(fmt(t("starts {0}"), [session.starts_on]));
				info.appendChild(el("div", "tr-session-meta", bits.join(" · ")));
				row.appendChild(info);
				// http(s) only — the URL is manager-set, but a `javascript:` link would
				// run on click, so the anchor is rendered only for a safe scheme (the
				// doctype refuses others on save; this is the second lock).
				var url = session.join_url || "";
				if (/^https?:\/\//i.test(url)) {
					var join = el("a", "tr-session-join", t("Join"));
					join.href = url;
					join.target = "_blank";
					join.rel = "noopener noreferrer";
					row.appendChild(join);
				}
				section.appendChild(row);
			});
			return section;
		}

		// Practical evaluations booked for this learner (b.evaluations): a supervisor
		// watching them demonstrate a competency. When, who, and where — the verdict
		// itself lives on the sign-off, never here. null/empty draws nothing.
		function evaluationBlock() {
			var evaluations = b.evaluations;
			if (!evaluations || !evaluations.length) return null;
			var section = el("section", "tr-evals");
			section.appendChild(el("h2", "tr-section-title", t("Upcoming evaluations")));
			evaluations.forEach(function (evaluation) {
				var row = el("div", "tr-eval");
				row.appendChild(el("div", "tr-eval-title", evaluation.course_title));
				var bits = [];
				if (evaluation.evaluator) bits.push(fmt(t("with {0}"), [evaluation.evaluator]));
				if (evaluation.scheduled_on) bits.push(fmt(t("on {0}"), [evaluation.scheduled_on]));
				if (evaluation.location) bits.push(evaluation.location);
				row.appendChild(el("div", "tr-eval-meta", bits.join(" · ")));
				section.appendChild(row);
			});
			return section;
		}

		// Announcements relevant to this learner (b.announcements) — everyone's, their
		// courses', their batches'. Rendered as plain text via textContent, so a body
		// cannot smuggle markup; pinned ones come first from the server. Empty draws
		// nothing.
		function announcementBlock() {
			var announcements = b.announcements;
			if (!announcements || !announcements.length) return null;
			var section = el("section", "tr-announcements");
			section.appendChild(el("h2", "tr-section-title", t("Announcements")));
			announcements.forEach(function (announcement) {
				var row = el("div", "tr-announcement");
				if (announcement.pinned) row.classList.add("is-pinned");
				row.appendChild(el("div", "tr-announcement-title", announcement.title));
				row.appendChild(el("div", "tr-announcement-body", announcement.body));
				section.appendChild(row);
			});
			return section;
		}

		// The catalog's link into the transcript, shown in both the empty and the
		// populated catalog — a learner between assignments still has a record.
		// The learner's own work submissions and their grades (b.submissions).
		// A "Needs rework" is the most action-needing thing here, so the strip sits
		// high on the home page; the feedback is plain text via textContent so a
		// grader's note cannot smuggle markup. null/empty draws nothing.
		function submissionBlock() {
			var subs = b.submissions;
			if (!subs || !subs.length) return null;
			var section = el("section", "tr-submissions");
			section.appendChild(el("h2", "tr-section-title", t("Your submissions")));
			subs.forEach(function (sub) {
				section.appendChild(submissionRow(sub));
			});
			return section;
		}

		// One submission, as a row. Shared by the home strip and the lesson-view box
		// so a status and its feedback always read the same way in both places.
		function submissionRow(sub) {
			var row = el("div", "tr-submission " + submissionStateClass(sub.status));
			var top = el("div", "tr-submission-head");
			top.appendChild(el("div", "tr-submission-title", sub.lesson_title || sub.course_title || ""));
			top.appendChild(el("span", "tr-submission-status", submissionStatusLabel(sub.status)));
			row.appendChild(top);
			var bits = [];
			if (sub.lesson_title && sub.course_title) bits.push(sub.course_title);
			if (sub.grade) bits.push(fmt(t("grade: {0}"), [sub.grade]));
			if (sub.submitted_on) bits.push(fmt(t("submitted {0}"), [sub.submitted_on]));
			if (bits.length) row.appendChild(el("div", "tr-submission-meta", bits.join(" · ")));
			if (sub.feedback) {
				var fb = el("div", "tr-submission-feedback");
				fb.appendChild(el("div", "tr-submission-feedback-label", t("Feedback")));
				fb.appendChild(el("div", "tr-submission-feedback-body", sub.feedback));
				row.appendChild(fb);
			}
			return row;
		}

		function submissionStatusLabel(status) {
			if (status === "Passed") return t("Passed");
			if (status === "Needs Rework") return t("Needs rework");
			if (status === "Under Review") return t("Under review");
			return t("Submitted");
		}

		// State classes only (is-*), which the css-contract exempts. The palette that
		// colours passed/rework/pending lives in player.css.
		function submissionStateClass(status) {
			if (status === "Passed") return "is-passed";
			if (status === "Needs Rework") return "is-rework";
			return "is-pending";
		}

		// The learner's newest submission for one lesson, matched by lesson_key.
		// b.submissions is newest-first from the server, so the first match wins.
		function latestSubmissionFor(key) {
			var subs = b.submissions || [];
			for (var i = 0; i < subs.length; i++) {
				if (subs[i].lesson_key === key) return subs[i];
			}
			return null;
		}

		function recordOpen() {
			var wrap = el("div", "tr-record-open");
			wrap.appendChild(
				button(t("My record & certificates"), "tr-button tr-button-quiet", openRecord)
			);
			return wrap;
		}

		function openRecord() {
			go("record");
		}

		function renderRecord() {
			var bar = el("div", "tr-subhead-row");
			bar.appendChild(button("← " + t("All courses"), "tr-button tr-button-quiet", function () {
				go("catalog");
			}));
			bar.appendChild(el("h1", "tr-title", t("My record")));
			head.appendChild(bar);

			var pending = el("div", "tr-loading", t("Loading your record…"));
			pending.setAttribute("role", "status");
			main.appendChild(pending);

			call("getTranscript", {})
				.then(function (data) {
					clear(main);
					data = data || {};
					var rows = data.completions || [];
					if (!rows.length) {
						main.appendChild(
							el("p", "tr-empty", t("You have not finished a course yet. Your certificates will appear here."))
						);
						return;
					}
					var list = el("div", "tr-record");
					rows.forEach(function (row) {
						list.appendChild(recordRow(row));
					});
					main.appendChild(list);
				})
				.catch(function (err) {
					clear(main);
					fail(main, err);
					main.appendChild(button(t("Back"), "tr-button", function () {
						go("catalog");
					}));
				});
		}

		function recordRow(row) {
			var item = el("div", "tr-record-row");
			var body = el("div", "tr-record-main");
			body.appendChild(el("div", "tr-record-title", row.course_title || row.course || t("Course")));
			var meta = el("div", "tr-record-meta");
			// score arrives only since v1.363.0 fixed the dropped score_percent field.
			if (row.score != null) meta.appendChild(chip(fmt(t("Score {0}%"), [pct(row.score)])));
			if (row.completed_on) meta.appendChild(chip(fmt(t("Passed {0}"), [String(row.completed_on).slice(0, 10)])));
			if (row.status) meta.appendChild(chip(row.status));
			if (row.expires_on) meta.appendChild(chip(fmt(t("Expires {0}"), [String(row.expires_on).slice(0, 10)])));
			body.appendChild(meta);
			item.appendChild(body);
			// Opens the existing /training_certificate page in a new tab so the
			// learner keeps their place in the record.
			if (row.certificate_url) {
				var link = el("a", "tr-button tr-button-quiet tr-record-cert", t("Certificate"));
				link.href = row.certificate_url;
				link.target = "_blank";
				link.rel = "noopener";
				item.appendChild(link);
			}
			return item;
		}

		// ----------------------------------------------------------- completion

		// The end of a course. Replaces the self-looping quiz-results screen —
		// renderResults' "Continue" ran finishLesson on the already-finished last
		// lesson and looped back with no exit but the browser's Back button. This is
		// a real completion view that surfaces the reward finish_attempt now returns
		// (points, streak, freshly earned badges) and links the certificate. Every
		// piece is optional: a course with gamification off shows the verdict and an
		// exit, and nothing untrue.
		function renderComplete() {
			var result = state.result || {};
			var reward = state.reward || {};

			head.appendChild(el("h1", "tr-title", t("Course complete")));

			var wrap = el("div", "tr-complete");

			// Behind the card, inert, cleared after ~1.6s (and skipped when the OS
			// asks for reduced motion). Appended first so it sits under everything.
			var canvas = el("canvas", "tr-confetti");
			canvas.setAttribute("aria-hidden", "true");
			wrap.appendChild(canvas);

			var mark = el("div", "tr-complete-mark", "✓");
			mark.setAttribute("aria-hidden", "true");
			wrap.appendChild(mark);

			wrap.appendChild(el("p", "tr-complete-line", t("You finished this course.")));
			var scoreNode = null;
			if (result.score != null) {
				// Starts at 0% and counts up once on the page; countUp writes the
				// final value outright under reduced motion.
				scoreNode = el("div", "tr-complete-score", pct(0) + "%");
				wrap.appendChild(scoreNode);
			}

			var tiles = el("div", "tr-stat-row");
			var counters = [];
			var shown = 0;
			function addCounter(value, label) {
				var tile = statTile(0, label);
				tiles.appendChild(tile);
				counters.push({ node: tile.querySelector(".tr-stat-num"), value: value });
				shown++;
			}
			if (reward.points != null) addCounter(intOf(reward.points), t("Points"));
			if (intOf(reward.streak_days)) {
				addCounter(intOf(reward.streak_days), reward.streak_days === 1 ? t("Day") : t("Days"));
			}
			if (reward.badges_earned != null) addCounter(intOf(reward.badges_earned), t("Badges"));
			if (shown) wrap.appendChild(tiles);

			var newBadges = reward.new_badges || [];
			if (newBadges.length) {
				wrap.appendChild(
					el("h2", "tr-section-title", newBadges.length === 1 ? t("Badge unlocked") : t("Badges unlocked"))
				);
				var badges = el("div", "tr-new-badges");
				newBadges.forEach(function (badge) {
					badges.appendChild(newBadgeCard(badge));
				});
				wrap.appendChild(badges);
			}

			main.appendChild(wrap);

			// Now that the card is on the page: sweep the numbers up and — unless the
			// OS asks for stillness — a short confetti burst behind it.
			if (scoreNode) countUp(scoreNode, pct(result.score), 900, "%");
			counters.forEach(function (counter) {
				countUp(counter.node, counter.value, 900);
			});
			if (!prefersReduced()) confetti(canvas);

			if (reward.certificate_url) {
				var cert = el("a", "tr-button tr-button-primary", t("View your certificate"));
				cert.href = reward.certificate_url;
				cert.target = "_blank";
				cert.rel = "noopener";
				foot.appendChild(cert);
			}
			foot.appendChild(
				button(t("Back to your courses"), "tr-button", function () {
					state.result = null;
					state.reward = null;
					go("catalog");
				})
			);
		}

		function newBadgeCard(badge) {
			var card = el("div", "tr-new-badge");
			var medal = el("div", "tr-new-badge-medal");
			medal.setAttribute("aria-hidden", "true");
			if (badge.image) {
				var img = el("img", "tr-new-badge-img");
				img.src = badge.image;
				img.alt = "";
				img.loading = "lazy";
				medal.appendChild(img);
			} else {
				medal.textContent = "★";
			}
			card.appendChild(medal);
			var text = el("div", "tr-new-badge-text");
			text.appendChild(el("div", "tr-new-badge-name", badge.name || t("Badge")));
			if (badge.description) text.appendChild(el("div", "tr-new-badge-desc", badge.description));
			card.appendChild(text);
			return card;
		}

		// --------------------------------------------------------------- signoff

		function renderSignoff() {
			var course = state.course || {};
			head.appendChild(el("h1", "tr-title", t("Waiting for sign-off")));
			var box = el("div", "tr-signoff");
			box.appendChild(
				el("p", null,
					fmt(
						t("You have finished everything in {0}. A supervisor needs to confirm it " +
							"before it counts as complete."),
						[course.title || t("this course")]
					))
			);
			if (course.signoff_instructions) {
				box.appendChild(el("p", "tr-signoff-what", course.signoff_instructions));
			}
			// Who it is with. Finishing the course raises the request server-side and
			// emails them, so this is a statement about something that has actually
			// happened rather than a hint to go and find somebody -- which is what
			// this view said while `request_signoff` had no caller.
			if (state.signoffWith) {
				box.appendChild(
					el("p", "tr-signoff-who", fmt(t("Asked {0} to confirm it."), [state.signoffWith]))
				);
			}
			// Deliberately no "remind them" button: chasing a supervisor is the
			// escalation job's business, and a learner-triggered nag would be one
			// email per refresh.
			box.appendChild(el("p", "tr-signoff-note", t("Nothing else is needed from you.")));
			main.appendChild(box);
			foot.appendChild(button(t("Back to your courses"), "tr-button", function () {
				go("catalog");
			}));
		}

		// ------------------------------------------------------------------ boot

		// A phone locking, an app switch, or a tab close is where telemetry goes
		// missing: the interval never fires again and the last chunk of watching
		// is lost. `visibilitychange` fires reliably on iOS where `unload` does
		// not, so it is the one that matters here.
		function onVisibility() {
			if (document.visibilityState === "hidden") flush();
		}
		document.addEventListener("visibilitychange", onVisibility);
		window.addEventListener("pagehide", flush);

		// Only fires if something outside the player moves history. Re-rendering
		// from the URL keeps that landing on the right view instead of a stale one.
		window.addEventListener("popstate", function () {
			if (b.history === false) return;
			var course = queryParam("course");
			if (!course) {
				go("catalog");
				return;
			}
			openCourse(course, queryParam("lesson") || null);
		});

		function start() {
			// Before anything else, and before any deep link. A course URL opened on
			// a dormant site must not fetch — `get_course` throws once the runtime
			// gate refuses, so the learner would get an error page where the server
			// had a sentence ready for them.
			if (b.enabled === false) {
				go("unavailable");
				return;
			}
			var startAt = b.start || {};
			var course = startAt.course || (b.history === false ? "" : queryParam("course"));
			var lessonKey = startAt.lesson_key || (b.history === false ? "" : queryParam("lesson"));
			if (course) {
				openCourse(course, lessonKey || null);
				return;
			}
			go(b.view || "catalog");
		}

		start();

		return {
			// Exposed for the host page and the builder preview; the player itself
			// never needs them.
			go: go,
			openCourse: openCourse,
			openLesson: openLesson,
			flush: flush,
			destroy: function () {
				runTeardowns();
				document.removeEventListener("visibilitychange", onVisibility);
				window.removeEventListener("pagehide", flush);
				clear(rootEl);
			},
		};
	}

	TR.Player = Player;
})();
