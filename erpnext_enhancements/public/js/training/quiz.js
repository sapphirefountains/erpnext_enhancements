/* Training quiz + results screen (TR.Quiz).
 *
 * Runs inside the learner player, which must work for Website Users whose
 * `desk_access` is 0 — so there is deliberately no `frappe.*` in this file, not
 * even `__()`. Every request goes through the injected transport object, which
 * is the same seam the Phase-3 authoring preview swaps out.
 *
 * What is load-bearing here:
 *
 *   - This file has no notion of a correct answer and must never be given one.
 *     Answers are held and submitted as `option_key` values, so the client never
 *     learns the canonical order of the options; that is the whole reason
 *     server-side shuffling buys anything. Grading happens in
 *     `training/grading.py` against the answer key, and a client-supplied score
 *     is ignored there.
 *   - The results screen renders *only the fields the server put in the
 *     response*. It never derives "the ones I did not pick must be right", and
 *     it never fills a gap with a guess — a review pass that reconstructs the
 *     key from omissions leaks it just as surely as sending it would.
 *   - Submit is latched while a request is in flight. A quiz attempt is a
 *     limited resource; a double-tap that burns one is experienced by the
 *     learner as the app stealing a try, and only a Training Manager can give
 *     it back.
 *
 * Layout is mobile-first: one question per screen below 720px with Back/Next
 * and progress dots, a single scrolling column above it. Both modes render the
 * same nodes — the breakpoint only changes which are visible — so an answer
 * survives a rotation or a desktop window being dragged narrow.
 */
(function () {
	"use strict";

	var TR = (window.TR = window.TR || {});

	var SHORT = "Short Answer";
	var MULTI = "Multiple Choice";

	var PAGED_BELOW = 720;
	var STYLE_ID = "tr-quiz-styles";

	// Parsed out of the authored HTML before it is put on the page. See scrub().
	var BANNED_TAGS = ["script", "iframe", "object", "embed", "link", "meta", "style", "form"];
	var URL_ATTRS = ["href", "src", "xlink:href", "action", "formaction"];

	// Optional string hook so a sibling module can install translations without
	// this file reaching for a desk global. Resolved per call — TR.t may be set
	// after this IIFE runs.
	function t(text) {
		return typeof TR.t === "function" ? TR.t(text) : text;
	}

	// ------------------------------------------------------------------ helpers

	function el(tag, cls, text) {
		var node = document.createElement(tag);
		if (cls) node.className = cls;
		if (text != null) node.textContent = String(text);
		return node;
	}

	function scrub(html) {
		// Question text and explanations are Text Editor fields, so they are HTML
		// and have to be inserted as HTML. Parse them in an inert document first:
		// assigning to `innerHTML` on a detached <div> still fires resource loads,
		// so an `<img onerror>` would run before we ever got to strip it.
		var doc = new DOMParser().parseFromString(String(html == null ? "" : html), "text/html");
		var nodes = doc.body.querySelectorAll("*");
		for (var i = nodes.length - 1; i >= 0; i--) {
			var node = nodes[i];
			if (BANNED_TAGS.indexOf(node.tagName.toLowerCase()) !== -1) {
				node.parentNode.removeChild(node);
				continue;
			}
			for (var j = node.attributes.length - 1; j >= 0; j--) {
				var attr = node.attributes[j];
				var name = attr.name.toLowerCase();
				var bad = name.indexOf("on") === 0;
				if (!bad && URL_ATTRS.indexOf(name) !== -1) {
					bad = /^\s*(javascript|data:text\/html)/i.test(attr.value || "");
				}
				if (bad) node.removeAttribute(attr.name);
			}
		}
		return doc.body.innerHTML;
	}

	function setHtml(node, html) {
		node.innerHTML = scrub(html);
		return node;
	}

	function pct(value) {
		var num = Number(value);
		if (!isFinite(num)) return "";
		return (Math.round(num * 10) / 10).toString().replace(/\.0$/, "") + "%";
	}

	function isArray(value) {
		return Object.prototype.toString.call(value) === "[object Array]";
	}

	/* The drawn quiz arrives either as the payload from `draw_quiz` wrapped in a
	 * envelope, or as the bare list. Accept both; the player and the Phase-3
	 * preview transport do not have to agree on the wrapper. */
	function normalise(quiz) {
		var raw = isArray(quiz) ? quiz : (quiz && (quiz.questions || quiz.items)) || [];
		var out = [];
		for (var i = 0; i < raw.length; i++) {
			var q = raw[i] || {};
			var options = [];
			var given = q.options || [];
			for (var j = 0; j < given.length; j++) {
				var opt = given[j] || {};
				// A missing option_key is not something the client can invent — an
				// index would be exactly the "it's the third one" leak the shuffle
				// exists to prevent. Drop it and let the question render short.
				if (!opt.option_key) continue;
				options.push({ key: String(opt.option_key), text: opt.text || opt.option_text || "" });
			}
			out.push({
				id: String(q.question || q.name || q.id || "q" + i),
				type: q.type || q.question_type || "Single Choice",
				text: q.text || q.question_text || "",
				points: Number(q.points) || 1,
				options: options,
			});
		}
		return out;
	}

	function optionTextFor(question, key) {
		for (var i = 0; i < question.options.length; i++) {
			if (question.options[i].key === key) return question.options[i].text;
		}
		return "";
	}

	function ensureStyles() {
		// Self-contained so the quiz is legible even when mounted somewhere the
		// training stylesheet was not loaded (the Phase-3 preview, a dialog).
		// Everything is expressed against custom properties with fallbacks, so a
		// host stylesheet re-themes it by setting the variables rather than by
		// out-specificity-ing these rules.
		if (document.getElementById(STYLE_ID)) return;
		var style = el("style");
		style.id = STYLE_ID;
		style.textContent = CSS;
		document.head.appendChild(style);
	}

	// ------------------------------------------------------------------ quiz view

	function mount(root, ctx, transport) {
		if (!root) return null;
		ensureStyles();
		ctx = ctx || {};
		transport = transport || {};

		var state = {
			attempt: ctx.attempt || "",
			lessonKey: ctx.lessonKey || ctx.lesson_key || "",
			run: Number((ctx.quiz && ctx.quiz.run) != null ? ctx.quiz.run : ctx.run) || 1,
			passScore: (ctx.quiz && ctx.quiz.pass_score) != null ? ctx.quiz.pass_score : null,
			questions: normalise(ctx.quiz),
			choices: {},
			texts: {},
			index: 0,
			paged: false,
			busy: false,
		};

		// Stashed so results() — which the contract fixes at (el, result,
		// transport) — can still offer Retry into the same root, and can label a
		// learner's own answer with the option text it was shown as.
		root.__trQuiz = {
			attempt: state.attempt,
			lessonKey: state.lessonKey,
			questions: state.questions,
			onExit: ctx.onExit || null,
			onResult: ctx.onResult || null,
		};

		root.innerHTML = "";
		var wrap = el("div", "tr-quiz");
		wrap.setAttribute("role", "form");
		wrap.setAttribute("aria-label", t("Quiz"));
		root.appendChild(wrap);

		var head = el("div", "tr-quiz-head");
		var meta = el("div", "tr-quiz-meta");
		var dots = el("div", "tr-quiz-dots");
		dots.setAttribute("role", "list");
		head.appendChild(meta);
		head.appendChild(dots);
		wrap.appendChild(head);

		var error = el("div", "tr-quiz-error");
		error.setAttribute("role", "alert");
		error.hidden = true;
		wrap.appendChild(error);

		var list = el("div", "tr-quiz-list");
		wrap.appendChild(list);

		var foot = el("div", "tr-quiz-foot");
		var back = el("button", "tr-btn tr-btn-ghost", t("Back"));
		var next = el("button", "tr-btn", t("Next"));
		var submit = el("button", "tr-btn tr-btn-primary", t("Submit"));
		[back, next, submit].forEach(function (button) {
			button.type = "button";
			foot.appendChild(button);
		});
		wrap.appendChild(foot);

		var cards = [];
		var dotButtons = [];
		state.questions.forEach(function (question, i) {
			var card = renderQuestion(question, i);
			cards.push(card);
			list.appendChild(card);

			var dot = el("button", "tr-dot");
			dot.type = "button";
			dot.setAttribute("role", "listitem");
			dot.addEventListener("click", function () {
				goTo(i);
			});
			dotButtons.push(dot);
			dots.appendChild(dot);
		});

		if (!state.questions.length) {
			list.appendChild(el("p", "tr-quiz-empty", t("This quiz has no questions.")));
			submit.disabled = true;
		}

		function renderQuestion(question, i) {
			var card = el("section", "tr-q");
			card.setAttribute("data-qid", question.id);

			var num = el("div", "tr-q-num");
			num.appendChild(el("span", null, t("Question") + " " + (i + 1)));
			if (question.points > 1) {
				num.appendChild(el("span", "tr-q-points", question.points + " " + t("pts")));
			}
			card.appendChild(num);

			var text = el("div", "tr-q-text");
			text.id = "tr-q-text-" + i;
			setHtml(text, question.text);
			card.appendChild(text);

			if (question.type === SHORT) {
				var input = el("input", "tr-input");
				input.type = "text";
				input.autocomplete = "off";
				input.setAttribute("aria-labelledby", text.id);
				input.placeholder = t("Type your answer");
				input.addEventListener("input", function () {
					state.texts[question.id] = input.value;
					paint();
				});
				card.appendChild(input);
				return card;
			}

			var multi = question.type === MULTI;
			if (multi) card.appendChild(el("div", "tr-q-hint", t("Choose every answer that applies.")));

			var group = el("div", "tr-q-options");
			group.setAttribute("role", multi ? "group" : "radiogroup");
			group.setAttribute("aria-labelledby", text.id);

			if (!question.options.length) {
				card.appendChild(group);
				card.appendChild(
					el("div", "tr-q-hint tr-q-broken", t("This question is missing its answer options."))
				);
				return card;
			}

			question.options.forEach(function (option) {
				var node = el("div", "tr-opt");
				node.setAttribute("role", multi ? "checkbox" : "radio");
				node.setAttribute("aria-checked", "false");
				node.setAttribute("data-key", option.key);
				// Every option is tabbable rather than the one-stop roving tabindex
				// an ARIA radiogroup normally uses: on a phone with a keyboard case
				// arrow keys are the thing people do not reach for. Arrow keys still
				// work, Tab just also does.
				node.tabIndex = 0;
				node.appendChild(el("span", "tr-opt-mark"));
				node.appendChild(el("span", "tr-opt-text", option.text));
				node.addEventListener("click", function () {
					choose(question, option.key, multi);
				});
				node.addEventListener("keydown", function (event) {
					if (event.key === " " || event.key === "Spacebar" || event.key === "Enter") {
						event.preventDefault();
						choose(question, option.key, multi);
						return;
					}
					if (multi) return;
					var step = 0;
					if (event.key === "ArrowDown" || event.key === "ArrowRight") step = 1;
					if (event.key === "ArrowUp" || event.key === "ArrowLeft") step = -1;
					if (!step) return;
					event.preventDefault();
					var nodes = group.querySelectorAll(".tr-opt");
					var at = Array.prototype.indexOf.call(nodes, node);
					var target = nodes[(at + step + nodes.length) % nodes.length];
					if (target) target.focus();
				});
				group.appendChild(node);
			});

			card.appendChild(group);
			return card;
		}

		function choose(question, key, multi) {
			var picked = state.choices[question.id] || [];
			if (multi) {
				var at = picked.indexOf(key);
				if (at === -1) picked = picked.concat([key]);
				else picked = picked.slice(0, at).concat(picked.slice(at + 1));
			} else {
				picked = picked.length === 1 && picked[0] === key ? picked : [key];
			}
			state.choices[question.id] = picked;
			paint();
		}

		function answered(question) {
			if (question.type === SHORT) return !!(state.texts[question.id] || "").trim();
			return (state.choices[question.id] || []).length > 0;
		}

		function unansweredCount() {
			var count = 0;
			state.questions.forEach(function (question) {
				if (!answered(question)) count++;
			});
			return count;
		}

		function goTo(i) {
			state.index = Math.max(0, Math.min(i, state.questions.length - 1));
			paint();
			var card = cards[state.index];
			if (!card) return;
			if (state.paged) card.scrollIntoView({ block: "start" });
			else card.scrollIntoView({ block: "nearest", behavior: "smooth" });
		}

		function paint() {
			state.questions.forEach(function (question, i) {
				var card = cards[i];
				card.hidden = state.paged && i !== state.index;
				card.classList.toggle("is-current", i === state.index);

				var picked = state.choices[question.id] || [];
				var nodes = card.querySelectorAll(".tr-opt");
				for (var k = 0; k < nodes.length; k++) {
					var on = picked.indexOf(nodes[k].getAttribute("data-key")) !== -1;
					nodes[k].setAttribute("aria-checked", on ? "true" : "false");
					nodes[k].classList.toggle("is-on", on);
				}

				var dot = dotButtons[i];
				dot.classList.toggle("is-done", answered(question));
				dot.classList.toggle("is-current", i === state.index);
				dot.setAttribute(
					"aria-label",
					t("Question") +
						" " +
						(i + 1) +
						", " +
						(answered(question) ? t("answered") : t("not answered"))
				);
			});

			var total = state.questions.length;
			var left = unansweredCount();
			var line = state.paged
				? t("Question") + " " + (state.index + 1) + " " + t("of") + " " + total
				: total + " " + t("questions");
			if (left) line += " · " + left + " " + t("unanswered");
			if (state.passScore) line += " · " + t("pass") + " " + pct(state.passScore);
			meta.textContent = line;

			back.hidden = !state.paged || state.index === 0;
			next.hidden = !state.paged || state.index >= total - 1;
			submit.disabled = state.busy || !total;
			submit.textContent = state.busy ? t("Submitting…") : t("Submit");
			submit.setAttribute("aria-busy", state.busy ? "true" : "false");
			wrap.classList.toggle("is-paged", state.paged);
		}

		back.addEventListener("click", function () {
			goTo(state.index - 1);
		});
		next.addEventListener("click", function () {
			goTo(state.index + 1);
		});
		submit.addEventListener("click", function () {
			if (state.busy) return;
			var left = unansweredCount();
			if (!left) return send();
			confirmUnanswered(wrap, left, send);
		});

		function payload() {
			// Always send an entry per drawn question, empty when skipped: the
			// server grades the set it drew, and a missing key is ambiguous between
			// "skipped" and "never shown".
			var answers = {};
			state.questions.forEach(function (question) {
				if (question.type === SHORT) {
					var typed = (state.texts[question.id] || "").trim();
					answers[question.id] = typed ? [typed] : [];
				} else {
					answers[question.id] = (state.choices[question.id] || []).slice();
				}
			});
			return {
				attempt: state.attempt,
				lesson_key: state.lessonKey,
				run: state.run,
				answers: answers,
			};
		}

		function send() {
			if (state.busy || typeof transport.submitQuiz !== "function") return;
			state.busy = true;
			error.hidden = true;
			paint();
			Promise.resolve(transport.submitQuiz(payload()))
				.then(function (result) {
					result = result || {};
					// Our own record of what was picked, so the review can say "you
					// answered X" without the server having to echo it back. Never a
					// source of correctness — only of what this learner chose.
					if (!result.submitted) result.submitted = payload().answers;
					if (root.__trQuiz && root.__trQuiz.onResult) root.__trQuiz.onResult(result);
					root.dispatchEvent(
						new CustomEvent("tr-quiz-submitted", { bubbles: true, detail: result })
					);
					results(root, result, transport);
				})
				.catch(function (err) {
					// The attempt is only spent if the server said so, so re-enable
					// and let them try the request again.
					state.busy = false;
					paint();
					error.hidden = false;
					error.textContent =
						(err && err.message) || t("That did not send. Check your connection and try again.");
				});
		}

		var media = window.matchMedia("(max-width: " + (PAGED_BELOW - 1) + "px)");
		function applyMode(event) {
			state.paged = !!(event && "matches" in event ? event.matches : media.matches);
			paint();
		}
		if (media.addEventListener) media.addEventListener("change", applyMode);
		else if (media.addListener) media.addListener(applyMode);
		applyMode(media);

		return {
			state: state,
			destroy: function () {
				if (media.removeEventListener) media.removeEventListener("change", applyMode);
				else if (media.removeListener) media.removeListener(applyMode);
			},
		};
	}

	function confirmUnanswered(wrap, count, proceed) {
		var overlay = el("div", "tr-modal");
		var box = el("div", "tr-modal-box");
		box.setAttribute("role", "alertdialog");
		box.setAttribute("aria-modal", "true");
		box.appendChild(el("h3", "tr-modal-title", t("Submit unfinished?")));
		box.appendChild(
			el(
				"p",
				"tr-modal-text",
				count === 1
					? t("One question is still unanswered. It will be marked wrong.")
					: count + " " + t("questions are still unanswered. They will be marked wrong.")
			)
		);
		var row = el("div", "tr-modal-row");
		var cancel = el("button", "tr-btn tr-btn-ghost", t("Keep answering"));
		var go = el("button", "tr-btn tr-btn-primary", t("Submit anyway"));
		cancel.type = go.type = "button";
		row.appendChild(cancel);
		row.appendChild(go);
		box.appendChild(row);
		overlay.appendChild(box);
		wrap.appendChild(overlay);

		function close() {
			document.removeEventListener("keydown", onKey);
			if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
		}
		function onKey(event) {
			if (event.key === "Escape") close();
		}
		document.addEventListener("keydown", onKey);
		overlay.addEventListener("click", function (event) {
			if (event.target === overlay) close();
		});
		cancel.addEventListener("click", close);
		go.addEventListener("click", function () {
			close();
			proceed();
		});
		cancel.focus();
	}

	// ------------------------------------------------------------------ results view

	function results(root, result, transport) {
		if (!root) return null;
		ensureStyles();
		result = result || {};
		transport = transport || {};
		var ctx = root.__trQuiz || {};
		var byId = {};
		var numberOf = {};
		(ctx.questions || []).forEach(function (question, i) {
			byId[question.id] = question;
			// The server is free to return per_question in its own order; number the
			// review by the position the learner actually saw, or "Question 4" here
			// points at a different question than "Question 4" did five seconds ago.
			numberOf[question.id] = i + 1;
		});

		root.innerHTML = "";
		var wrap = el("div", "tr-quiz tr-result");
		root.appendChild(wrap);

		var passed = result.passed === true;
		var head = el("div", "tr-result-head");
		head.appendChild(ring(result.score, passed));

		var side = el("div", "tr-result-side");
		var chip = el(
			"div",
			"tr-chip " + (passed ? "is-pass" : "is-fail"),
			passed ? t("Passed") : t("Not passed")
		);
		side.appendChild(chip);
		if (result.pass_score != null) {
			side.appendChild(el("div", "tr-muted", t("Passing score") + " " + pct(result.pass_score)));
		}
		if (result.correct_count != null && result.question_count != null) {
			side.appendChild(
				el(
					"div",
					"tr-muted",
					result.correct_count + " " + t("of") + " " + result.question_count + " " + t("correct")
				)
			);
		}
		var attemptsLine = attemptsText(result);
		if (attemptsLine) side.appendChild(el("div", "tr-muted", attemptsLine));
		head.appendChild(side);
		wrap.appendChild(head);

		if (result.message) wrap.appendChild(el("p", "tr-result-note", result.message));

		var review = isArray(result.per_question) ? result.per_question : [];
		if (review.length) {
			wrap.appendChild(el("h3", "tr-result-h", t("Review")));
			var list = el("div", "tr-review");
			review.forEach(function (entry, i) {
				list.appendChild(renderReview(entry || {}, i, byId, numberOf, result));
			});
			wrap.appendChild(list);
		}

		var foot = el("div", "tr-quiz-foot tr-result-foot");
		var error = el("div", "tr-quiz-error");
		error.setAttribute("role", "alert");
		error.hidden = true;
		wrap.appendChild(error);

		if (canRetry(result) && typeof transport.startQuiz === "function") {
			var retry = el("button", "tr-btn tr-btn-primary", t("Try again"));
			retry.type = "button";
			retry.addEventListener("click", function () {
				if (retry.disabled) return;
				retry.disabled = true;
				retry.textContent = t("Loading…");
				error.hidden = true;
				root.dispatchEvent(new CustomEvent("tr-quiz-retry", { bubbles: true }));
				Promise.resolve(
					transport.startQuiz({
						attempt: ctx.attempt || result.attempt || "",
						lesson_key: ctx.lessonKey || result.lesson_key || "",
					})
				)
					.then(function (quiz) {
						mount(root, {
							attempt: ctx.attempt || result.attempt || "",
							lessonKey: ctx.lessonKey || result.lesson_key || "",
							quiz: quiz,
							onExit: ctx.onExit,
							onResult: ctx.onResult,
						}, transport);
					})
					.catch(function (err) {
						retry.disabled = false;
						retry.textContent = t("Try again");
						error.hidden = false;
						error.textContent =
							(err && err.message) || t("Could not start another attempt. Try again shortly.");
					});
			});
			foot.appendChild(retry);
		}

		if (ctx.onExit) {
			var done = el("button", "tr-btn", t("Continue"));
			done.type = "button";
			done.addEventListener("click", function () {
				ctx.onExit(result);
			});
			foot.appendChild(done);
		}
		if (foot.childNodes.length) wrap.appendChild(foot);

		return { wrap: wrap };
	}

	function canRetry(result) {
		// Explicit server permission only. `attempts_remaining` is honoured because
		// it is also the server speaking, but silence means no — offering a retry
		// the server will refuse spends a learner's goodwill on a dead button.
		if (result.can_retry === true) return true;
		if (result.can_retry === false) return false;
		return typeof result.attempts_remaining === "number" && result.attempts_remaining > 0;
	}

	function attemptsText(result) {
		if (typeof result.attempts_remaining === "number") {
			if (result.attempts_remaining <= 0) return t("No attempts left");
			return result.attempts_remaining === 1
				? t("1 attempt left")
				: result.attempts_remaining + " " + t("attempts left");
		}
		if (typeof result.attempts_used === "number" && result.max_attempts) {
			return t("Attempt") + " " + result.attempts_used + " " + t("of") + " " + result.max_attempts;
		}
		if (typeof result.attempts_used === "number") {
			return t("Attempt") + " " + result.attempts_used;
		}
		return "";
	}

	function ring(score, passed) {
		var value = Math.max(0, Math.min(100, Number(score) || 0));
		var radius = 42;
		var circumference = 2 * Math.PI * radius;
		var box = el("div", "tr-ring" + (passed ? " is-pass" : " is-fail"));
		box.innerHTML =
			'<svg viewBox="0 0 100 100" role="img" aria-label="' +
			(Math.round(value * 10) / 10) +
			' percent"><circle class="tr-ring-bg" cx="50" cy="50" r="' +
			radius +
			'"></circle><circle class="tr-ring-fg" cx="50" cy="50" r="' +
			radius +
			'" stroke-dasharray="' +
			circumference.toFixed(2) +
			'" stroke-dashoffset="' +
			(circumference * (1 - value / 100)).toFixed(2) +
			'"></circle></svg>';
		box.appendChild(el("div", "tr-ring-label", pct(score)));
		return box;
	}

	function renderReview(entry, i, byId, numberOf, result) {
		var card = el("section", "tr-rev");
		var id = String(entry.question || entry.id || "");
		var known = byId[id];
		var number = numberOf[id] || i + 1;

		var head = el("div", "tr-rev-head");
		head.appendChild(el("span", "tr-rev-num", t("Question") + " " + number));
		// Only the server decides whether correctness is disclosed at all. No
		// chip when `correct` is absent — an "unknown" state is information the
		// author may have deliberately withheld until a later attempt.
		if (entry.correct === true || entry.correct === false) {
			head.appendChild(
				el(
					"span",
					"tr-chip tr-chip-sm " + (entry.correct ? "is-pass" : "is-fail"),
					entry.correct ? t("Correct") : t("Incorrect")
				)
			);
		}
		if (entry.earned != null && entry.points != null) {
			head.appendChild(el("span", "tr-rev-pts", entry.earned + "/" + entry.points));
		}
		card.appendChild(head);

		var text = entry.text || entry.question_text || (known && known.text) || "";
		if (text) card.appendChild(setHtml(el("div", "tr-rev-text"), text));

		var mine = pickedFor(entry, id, result);
		if (mine.length) {
			var labels = mine.map(function (value) {
				return (known && optionTextFor(known, value)) || value;
			});
			card.appendChild(el("div", "tr-rev-mine", t("Your answer") + ": " + labels.join(", ")));
		} else if (entry.answered === false) {
			card.appendChild(el("div", "tr-rev-mine tr-muted", t("Not answered")));
		}

		// Rendered only when the server sent it. Absence is never read as a hint.
		var revealed = entry.correct_option_keys || entry.correct_options;
		if (isArray(revealed) && revealed.length) {
			var shown = revealed.map(function (value) {
				if (value && typeof value === "object") return value.text || value.option_key || "";
				return (known && optionTextFor(known, value)) || value;
			});
			card.appendChild(el("div", "tr-rev-key", t("Correct answer") + ": " + shown.join(", ")));
		} else if (isArray(entry.accepted_text) && entry.accepted_text.length) {
			card.appendChild(
				el("div", "tr-rev-key", t("Accepted") + ": " + entry.accepted_text.join(", "))
			);
		}

		if (entry.explanation) card.appendChild(setHtml(el("div", "tr-rev-why"), entry.explanation));
		return card;
	}

	function pickedFor(entry, id, result) {
		if (isArray(entry.selected)) return entry.selected;
		if (isArray(entry.answer)) return entry.answer;
		var submitted = result && result.submitted;
		if (submitted && isArray(submitted[id])) return submitted[id];
		return [];
	}

	// ------------------------------------------------------------------ styles

	var CSS = [
		".tr-quiz{--tr-fg:var(--text-color,#1f272e);--tr-muted-c:var(--text-muted,#6b757f);",
		"--tr-bg:var(--card-bg,#fff);--tr-line:var(--border-color,#d8dee4);",
		"--tr-accent:var(--primary,#2c7be5);--tr-ok:var(--green-500,#1f9d55);",
		"--tr-bad:var(--red-500,#d64545);color:var(--tr-fg);font-size:16px;line-height:1.5;",
		"max-width:52rem;margin:0 auto;position:relative}",
		".tr-quiz *{box-sizing:border-box}",
		".tr-quiz-head{position:sticky;top:0;z-index:2;background:var(--tr-bg);padding:.5rem 0 .75rem}",
		".tr-quiz-meta{font-size:.875rem;color:var(--tr-muted-c)}",
		".tr-quiz-dots{display:flex;flex-wrap:wrap;gap:.375rem;margin-top:.5rem}",
		".tr-dot{width:.75rem;height:.75rem;padding:0;border-radius:50%;cursor:pointer;",
		"border:1px solid var(--tr-line);background:transparent}",
		".tr-dot.is-done{background:var(--tr-accent);border-color:var(--tr-accent)}",
		".tr-dot.is-current{box-shadow:0 0 0 3px rgba(44,123,229,.25)}",
		".tr-q{padding:1rem 0;border-top:1px solid var(--tr-line)}",
		".tr-q:first-child{border-top:0}",
		".tr-quiz.is-paged .tr-q{border-top:0}",
		".tr-q-num{display:flex;gap:.5rem;align-items:center;font-size:.8125rem;",
		"text-transform:uppercase;letter-spacing:.04em;color:var(--tr-muted-c)}",
		".tr-q-points{margin-left:auto;text-transform:none;letter-spacing:0}",
		".tr-q-text{margin:.5rem 0 .75rem;font-size:1.0625rem}",
		".tr-q-hint{font-size:.8125rem;color:var(--tr-muted-c);margin-bottom:.5rem}",
		".tr-q-broken{color:var(--tr-bad)}",
		".tr-q-options{display:flex;flex-direction:column;gap:.5rem}",
		".tr-opt{display:flex;gap:.75rem;align-items:flex-start;min-height:44px;padding:.625rem .75rem;",
		"border:1px solid var(--tr-line);border-radius:.5rem;cursor:pointer;background:var(--tr-bg)}",
		".tr-opt:focus-visible{outline:2px solid var(--tr-accent);outline-offset:2px}",
		".tr-opt.is-on{border-color:var(--tr-accent);background:rgba(44,123,229,.08)}",
		".tr-opt-mark{flex:0 0 auto;width:1.25rem;height:1.25rem;margin-top:.125rem;border-radius:50%;",
		"border:2px solid var(--tr-line)}",
		'.tr-opt[role="checkbox"] .tr-opt-mark{border-radius:.25rem}',
		".tr-opt.is-on .tr-opt-mark{border-color:var(--tr-accent);background:var(--tr-accent);",
		"box-shadow:inset 0 0 0 3px var(--tr-bg)}",
		".tr-opt-text{flex:1 1 auto}",
		".tr-input{width:100%;min-height:44px;font-size:16px;padding:.5rem .75rem;",
		"border:1px solid var(--tr-line);border-radius:.5rem;background:var(--tr-bg);color:inherit}",
		".tr-quiz-foot{display:flex;gap:.5rem;padding:1rem 0;position:sticky;bottom:0;",
		"background:var(--tr-bg)}",
		".tr-btn{min-height:44px;padding:.5rem 1.125rem;border-radius:.5rem;font-size:1rem;",
		"border:1px solid var(--tr-line);background:var(--tr-bg);color:inherit;cursor:pointer}",
		".tr-btn:focus-visible{outline:2px solid var(--tr-accent);outline-offset:2px}",
		".tr-btn[disabled]{opacity:.55;cursor:not-allowed}",
		".tr-btn-primary{margin-left:auto;background:var(--tr-accent);border-color:var(--tr-accent);",
		"color:#fff}",
		".tr-btn-ghost{background:transparent}",
		".tr-quiz-error{margin:.5rem 0;padding:.625rem .75rem;border-radius:.5rem;",
		"background:rgba(214,69,69,.1);color:var(--tr-bad);font-size:.9375rem}",
		".tr-quiz-empty{color:var(--tr-muted-c)}",
		".tr-modal{position:fixed;inset:0;z-index:20;display:flex;align-items:center;",
		"justify-content:center;padding:1rem;background:rgba(0,0,0,.45)}",
		".tr-modal-box{background:var(--tr-bg);border-radius:.75rem;padding:1.25rem;max-width:24rem;",
		"width:100%}",
		".tr-modal-title{margin:0 0 .5rem;font-size:1.0625rem}",
		".tr-modal-text{margin:0 0 1rem;color:var(--tr-muted-c)}",
		".tr-modal-row{display:flex;gap:.5rem;justify-content:flex-end}",
		".tr-modal-row .tr-btn-primary{margin-left:0}",
		".tr-result-head{display:flex;gap:1rem;align-items:center;padding:1rem 0}",
		".tr-result-side{display:flex;flex-direction:column;gap:.25rem}",
		".tr-ring{position:relative;width:96px;height:96px;flex:0 0 auto}",
		".tr-ring svg{width:100%;height:100%;transform:rotate(-90deg)}",
		".tr-ring circle{fill:none;stroke-width:8;stroke-linecap:round}",
		".tr-ring-bg{stroke:var(--tr-line)}",
		".tr-ring .tr-ring-fg{stroke:var(--tr-ok)}",
		".tr-ring.is-fail .tr-ring-fg{stroke:var(--tr-bad)}",
		".tr-ring-label{position:absolute;inset:0;display:flex;align-items:center;",
		"justify-content:center;font-size:1.25rem;font-weight:600}",
		".tr-chip{display:inline-block;padding:.125rem .625rem;border-radius:999px;",
		"font-size:.8125rem;font-weight:600}",
		".tr-chip.is-pass{background:rgba(31,157,85,.12);color:var(--tr-ok)}",
		".tr-chip.is-fail{background:rgba(214,69,69,.12);color:var(--tr-bad)}",
		".tr-chip-sm{margin-left:.5rem;text-transform:none;letter-spacing:0}",
		".tr-muted{color:var(--tr-muted-c);font-size:.875rem}",
		".tr-result-h{margin:1rem 0 .5rem;font-size:1rem}",
		".tr-result-note{color:var(--tr-muted-c)}",
		".tr-rev{padding:.75rem 0;border-top:1px solid var(--tr-line)}",
		".tr-rev-head{display:flex;align-items:center;font-size:.8125rem;",
		"text-transform:uppercase;letter-spacing:.04em;color:var(--tr-muted-c)}",
		".tr-rev-pts{margin-left:auto}",
		".tr-rev-text{margin:.375rem 0}",
		".tr-rev-mine,.tr-rev-key{font-size:.9375rem;margin-top:.25rem}",
		".tr-rev-key{color:var(--tr-ok)}",
		".tr-rev-why{margin-top:.5rem;padding:.625rem .75rem;border-radius:.5rem;",
		"background:rgba(127,127,127,.08);font-size:.9375rem}",
		".tr-result-foot .tr-btn-primary{margin-left:0}",
		"@media (min-width:720px){.tr-quiz{font-size:1rem}.tr-q{padding:1.25rem 0}}",
		"@media (prefers-reduced-motion:reduce){.tr-quiz *{scroll-behavior:auto}}",
	].join("");

	TR.Quiz = { mount: mount, results: results };
})();
