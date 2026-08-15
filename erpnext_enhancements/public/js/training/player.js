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
			var box = el("div", "tr-loading", message || t("Loading…"));
			box.setAttribute("role", "status");
			main.appendChild(box);
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

			main.appendChild(renderLeaderboard());
		}

		// ------------------------------------------------------------------ the board
		//
		// gamification.py has computed points, badges and streaks since v1.215.0 --
		// awarded on completion, decayed nightly -- and its one whitelisted function
		// had no caller. Every one of those numbers existed and none of them was ever
		// shown to the person who earned it.

		var boardState = { open: false, busy: false, data: null, error: null };

		function renderLeaderboard() {
			var wrap = el("div", "tr-board");

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
			if (state.status === "Awaiting Sign-off") {
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

		function renderQuestions(lesson) {
			var key = lesson.lesson_key || "";
			if (qaState.lessonKey !== key) {
				// A different lesson answers a different question. Keeping the old payload
				// would show one lesson's threads under another's heading, which reads as
				// data loss rather than as a stale cache.
				qaState = { lessonKey: key, open: false, busy: false, data: null, error: null };
			}

			var wrap = el("div", "tr-qa");
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
					// A course that wants hands-on verification is passed but not
					// finished — the sign-off view says who has to watch them.
					go(state.status === "Awaiting Sign-off" ? "signoff" : "results");
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
