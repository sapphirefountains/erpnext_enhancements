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
//   getLesson({course, lesson_key})   → {attempt, course, outline, lesson,
//                                        progress, resume, status}
//                                       lesson_key omitted ⇒ the server picks
//                                       the resume lesson.
//   heartbeat(payload)                → {coverage, credited, flags}
//   openCheckpoint({lesson_key, block_key, at})   → next unanswered checkpoint
//   answerCheckpoint({...})           → {correct, explanation, ...}
//   startQuiz({lesson_key})           → {run, questions, pass_score, attempts_left}
//   submitQuiz({lesson_key, run, answers}) → {score, passed, per_question}
//   completeLesson({lesson_key})      → {ok, reasons, next_lesson_key, status}
//   mediaUrl({lesson_key, block_key}) → a URL string
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

		var shell = el("div", "tr-shell");
		var head = el("header", "tr-subhead");
		var main = el("main", "tr-view");
		var foot = el("footer", "tr-bottom");
		shell.appendChild(head);
		shell.appendChild(main);
		shell.appendChild(foot);
		clear(rootEl);
		rootEl.appendChild(shell);
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
			shell.classList.toggle("is-busy", state.busy);
			if (state.busy) shell.setAttribute("aria-busy", "true");
			else shell.removeAttribute("aria-busy");
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

			var courseMin = pct((state.course && state.course.min_video_coverage) || 0);
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
				var need = pct(quiz.pass_score || (state.course && state.course.passing_score) || 0);
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
					var need = pct(block.min_coverage || (state.course && state.course.min_video_coverage) || 0);
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
				heartbeat: function (payload) {
					var body = payload || {};
					// Never taken from the caller: the server derives the learner
					// from the session, and the attempt from the lesson it is on.
					body.lesson_key = state.lessonKey;
					return call("heartbeat", body).then(function (response) {
						mergeHeartbeat(body.block_key, response);
						renderBottomBar();
						return response || {};
					});
				},
				mediaUrl: function (block) {
					return call("mediaUrl", {
						lesson_key: state.lessonKey,
						block_key: block.block_key,
					}).catch(function () {
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
			if (view === "catalog") renderCatalog();
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

		function renderCatalog() {
			head.appendChild(el("h1", "tr-title", t("Your training")));
			var courses = b.courses || (b.catalog && b.catalog.courses) || [];
			if (!courses.length) {
				main.appendChild(
					el("p", "tr-empty", t("Nothing is assigned to you right now, and nothing is overdue."))
				);
				return;
			}
			var grid = el("div", "tr-cards");
			courses.forEach(function (course) {
				grid.appendChild(courseCard(course));
			});
			main.appendChild(grid);
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
			body.appendChild(el("h2", "tr-card-title", course.title || course.course_title || course.course));

			var chips = el("div", "tr-card-chips");
			chips.appendChild(chip(course.weight === "Required" ? t("Required") : t("Optional"),
				course.weight === "Required" ? "required" : "optional"));
			var due = dueChip(course);
			if (due) chips.appendChild(due);
			var minutes = course.estimated_minutes || course.minutes;
			if (minutes) chips.appendChild(chip(fmt(t("{0} min"), [minutes])));
			body.appendChild(chips);

			if (course.summary) body.appendChild(el("p", "tr-card-summary", course.summary));

			var progress = pct(course.progress_percent);
			if (progress > 0) {
				body.appendChild(meter(progress, t("Course progress")));
				body.appendChild(el("p", "tr-card-progress", fmt(t("{0}% done"), [progress])));
			}

			var action = el("div", "tr-card-actions");
			if (course.status === "Awaiting Sign-off") {
				action.appendChild(chip(t("Waiting for your supervisor"), "pending"));
			}
			var verb =
				course.status === "Completed"
					? t("Review")
					: progress > 0 || course.status === "In Progress"
						? t("Resume")
						: t("Start");
			action.appendChild(
				button(verb, "tr-button tr-button-primary", function () {
					openCourse(course.course || course.name);
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

		function load(courseName, lessonKey) {
			setBusy(true);
			return call("getLesson", { course: courseName, lesson_key: lessonKey || null })
				.then(function (payload) {
					payload = payload || {};
					state.attempt = payload.attempt || state.attempt;
					state.course = payload.course || state.course;
					state.courseName = (payload.course && payload.course.name) || courseName;
					state.outline = payload.outline || [];
					state.progress = payload.progress || { lessons: {} };
					state.resume = payload.resume || state.resume;
					state.status = payload.status || state.status;
					if (payload.lesson) {
						state.lesson = payload.lesson;
						state.lessonKey = payload.lesson.lesson_key;
					}
					setBusy(false);
					return payload;
				})
				.catch(function (err) {
					setBusy(false);
					throw err;
				});
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

		function outlineRow(row, index) {
			var item = el("li", "tr-outline-row" + (row.locked ? " is-locked" : ""));
			var glyph = row.locked ? "🔒" : row.status === "done" ? "✓" : row.status === "in_progress" ? "◐" : "○";
			var mark = el("span", "tr-outline-glyph", glyph);
			mark.setAttribute("aria-hidden", "true");
			item.appendChild(mark);

			var text = el("div", "tr-outline-text");
			text.appendChild(
				el("span", "tr-outline-title", row.title || fmt(t("Lesson {0}"), [index + 1]))
			);
			var meta = el("div", "tr-outline-meta");
			meta.appendChild(el("span", "tr-sr-only",
				row.locked ? t("Locked") : row.status === "done" ? t("Finished") :
					row.status === "in_progress" ? t("In progress") : t("Not started")));
			if (row.minutes) meta.appendChild(chip(fmt(t("{0} min"), [row.minutes])));
			if (row.has_quiz) meta.appendChild(chip(t("Quiz")));
			text.appendChild(meta);

			// The reason is inline and always visible. A greyed-out row with the
			// explanation hidden behind a tooltip is unreadable on a phone and is
			// the difference between "finish lesson 2 first" and "this is broken".
			if (row.locked) {
				text.appendChild(el("p", "tr-outline-reason", row.lock_reason || t("Finish the earlier lessons first.")));
			}
			item.appendChild(text);

			if (!row.locked) {
				item.appendChild(
					button(row.status === "done" ? t("Review") : t("Open"), "tr-button tr-button-quiet", function () {
						openLesson(row.lesson_key);
					})
				);
			}
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

			renderBottomBar();
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

		function finishLesson() {
			setBusy(true);
			flush();
			call("completeLesson", { lesson_key: state.lessonKey })
				.then(function (result) {
					setBusy(false);
					result = result || {};
					if (!result.ok) {
						showRefusal(result.reasons || [t("This lesson is not finished yet.")]);
						return;
					}
					if (result.status) state.status = result.status;
					if (result.status === "Awaiting Sign-off") {
						go("signoff");
						return;
					}
					if (result.next_lesson_key) {
						openLesson(result.next_lesson_key);
						return;
					}
					openCourse(state.courseName);
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

			call("startQuiz", { lesson_key: state.lessonKey })
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
					TR.Quiz.mount(main, state.quiz, {
						transport: api,
						attempt: state.attempt,
						lesson: lesson,
						lessonKey: state.lessonKey,
						settings: b.settings || {},
						t: t,
						onTeardown: function (fn) {
							teardowns.push(fn);
						},
						submit: function (answers) {
							return call("submitQuiz", {
								lesson_key: state.lessonKey,
								run: state.quiz.run,
								answers: answers,
							});
						},
						onGraded: function (result) {
							state.result = result || {};
							recordQuizRun(state.result);
							go("results");
						},
						onCancel: function () {
							go("lesson");
						},
					});
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
			summary.appendChild(el("div", "tr-result-score", pct(result.score) + "%"));
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
