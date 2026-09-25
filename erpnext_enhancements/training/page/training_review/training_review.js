// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// The AI question review queue — /desk/training-review. The publish gate's other half.
//
// WHY IT EXISTS. `submit_for_review` and `publish_version` both refuse a course that
// still holds an AI-drafted question nobody has accepted, and that gate has worked
// since Phase 4. What has never existed is anywhere to do the accepting. Measured on
// production 2026-09-15: 128 unreviewed questions across all 11 Draft courses, plus
// 239 more in the ten Technician Program drafts. Every one of those courses is Draft
// because of this, and this page is the only place that work can be done.
//
// THE LAYOUT IS THE PRODUCT IDEA, not a preference. `review.get_review_lesson` hands
// back a lesson's content and its pending questions in one reply because the question
// a reviewer cannot answer from a list is *"could a learner have got this from the
// lesson?"*. So the lesson reads on the left and its questions sit on the right, on
// screen together, and the unit of work is the lesson rather than the question.
//
// THE COST PER QUESTION IS A KEYSTROKE, AND THAT IS THE WHOLE ERGONOMIC BUDGET.
// 367 repetitions is a morning's work. Hence a / e / r / j / k, optimistic removal so
// a verdict never costs a wait, and the next lesson fetched the moment this one
// empties — but only once every verdict in flight has landed, or the server would
// hand back the lesson we are still emptying.
//
// THERE IS NO BULK ACCEPT AND ONE MUST NOT BE ADDED. review.py's docstring says why:
// a button that clears a course in one click turns the gate into theatre, and the gate
// is the only thing standing between a machine-written answer key and somebody's
// compliance record. Everything here aims at making the work fast, never at skipping
// it — which is also why the page flags a question whose key is already indefensible
// instead of letting the reviewer wave it through and collect a server error.
//
// WHAT IT TAKES FROM training_insights.js: frappe's own desk tokens and no palette of
// its own, so it follows the desk theme for free and pulls no learner stylesheet.
// Hence `tq-` and nothing else.

// The shared Training rail, in a stylesheet and a script. Loaded here rather than
// included globally, for the same reason training_insights.js does it: TR.loadAssets
// is already in the desk bundle and can pull these on the pages that want them.
const TQ_NAV_ASSETS = [
	"/assets/erpnext_enhancements/css/training/desk_nav.css",
	"/assets/erpnext_enhancements/js/training/desk_nav.js",
];

frappe.pages["training-review"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Question Review"),
		// Two-column for the rail: TR.deskNav appends into page.sidebar, which only
		// exists on a two-column page.
		single_column: false,
	});
	wrapper.training_review = new TrainingReview(page, wrapper);
	tq_mount_nav(page);

	// What accepting and rejecting actually do, in the one place a desk page can put
	// four paragraphs without spending the reviewer's vertical space on them every
	// session. The one-line version is on screen permanently — see build().
	page.add_menu_item(__("What this screen is for"), () => tq_explain());
};

frappe.pages["training-review"].on_page_show = function (wrapper) {
	if (wrapper.training_review) wrapper.training_review.refresh();
};

// --------------------------------------------------------------------- the route
//
// Where the reviewer is working is carried in the route, so Back returns to the course
// or the lesson they were on before and Forward goes back to it:
//
//     training-review                    the whole queue
//     training-review/course/<course>    one course's queue (the "Working through" filter)
//     training-review/lesson/<lesson>    one lesson, from "Open a specific lesson"
//
// Segments, never route_options: v16's push_state writes the path alone, so anything
// held in route_options is gone after Back, Forward or a reload. Course and lesson
// names come from naming series, so they are safe as path segments.
//
// Only the moves the reviewer makes are entries. The next lesson the queue hands out
// when one empties is not: that lesson is finished, and Back into it would open onto
// nothing. The moves only route; on_page_show (refresh) reads the route and loads.
const TQ_ROUTE = "training-review";

function tq_view_key(view) {
	if (!view) return "";
	return view.lesson ? "lesson:" + view.lesson : "course:" + (view.course || "");
}

function tq_view_route(view) {
	if (view && view.lesson) return [TQ_ROUTE, "lesson", view.lesson];
	if (view && view.course) return [TQ_ROUTE, "course", view.course];
	return [TQ_ROUTE];
}

function tq_mount_nav(page) {
	// Guarded, and the guard is the point: a missing global bundle must cost the
	// sidebar and nothing else. An uncaught TypeError here would put an error where
	// 367 questions should be, to save a list of links.
	if (!window.TR || typeof TR.loadAssets !== "function") return;
	const version = (frappe.boot.versions && frappe.boot.versions.erpnext_enhancements) || "0";
	TR.loadAssets(TQ_NAV_ASSETS, version)
		.then(() => {
			if (typeof TR.deskNav !== "function") return;
			// No `setLearner`: this page holds no learner boot payload, so its rail
			// offers My Trainings as a door rather than as a list — the same choice
			// training_insights.js makes and for the same reason.
			TR.deskNav({ page: page, active: { view: "review" } });
		})
		.catch(() => {
			// Navigation is not the page.
		});
}

function tq_explain() {
	frappe.msgprint({
		title: __("Reviewing drafted questions"),
		indicator: "blue",
		message: [
			"<p>",
			__(
				"Every question an AI path writes is stamped as machine-drafted with no reviewer, and a course cannot be submitted for review or published while it holds one. That gate is why eleven courses are sitting in Draft."
			),
			"</p><p>",
			__(
				"Accepting a question records <b>you</b> as the person who vouched for its answer key. The name is taken from your session and cannot be sent by this page — a signature you can address to somebody else is not a signature."
			),
			"</p><p>",
			__(
				"Rejecting takes the question out of every draft lesson that draws it and deletes it if nothing else wants it. The reason you give is written to the course version's timeline, and it is the only surviving record that the question was ever drafted — which is why it is required."
			),
			"</p><p>",
			__(
				"There is no accept-all, on purpose. Reading each question against its lesson is the work; a button that cleared a course in one click would make the gate theatre."
			),
			"</p>",
		].join(""),
	});
}

// ---------------------------------------------------------------- the server half

// The four method paths are written out in full at each of the four call sites rather
// than assembled from a shared prefix or held in constants. `tests/test_training_review`
// reads this file and asserts by SET EQUALITY that the page calls exactly the methods
// review.py exposes — no typo, and no fifth one — and a dotted path built out of a
// variable is a path that check cannot see.
//
// Every `get_review_lesson` reply already carries the queue under `queue`, so the
// working loop never asks for it separately — two slightly different answers to "how
// many are left" on one screen is how a reviewer stops believing either of them.
// `get_review_queue` is only ever asked on its own for the two questions the lesson
// reply cannot answer: coming back to the page after an hour elsewhere (sync_queue —
// the counters go true again WITHOUT refetching the lesson, which would throw away a
// half-finished edit), and confirming that a course really has emptied before telling
// somebody it can be published (check_course_cleared).

// Mirrors MAX_REASON in review.py. Checked here as well so an over-long reason is
// caught with the words still in the box, rather than thrown away by a round trip.
const TQ_MAX_REASON = 2000;

// Mirror MIN_OPTIONS / MAX_OPTIONS in training_question.py. The flags below restate
// that controller's rules so a reviewer meets them as guidance in the card, not as a
// validation error after they have already committed to accepting.
const TQ_MIN_OPTIONS = 2;
const TQ_MAX_OPTIONS = 6;

// The two types `TrainingQuestion._validate_options` allows exactly one correct
// option on. Everything about the editor's radio-versus-checkbox follows from this.
const TQ_ONE_ANSWER_TYPES = ["Single Choice", "True-False"];

function tq_call(method, args) {
	// `frappe.xcall` rejects with `r.message`, and a `frappe.throw` never sets that —
	// so the server's sentence would arrive here as `undefined`. This page NEEDS that
	// sentence: the empty-quiz refusal names the lesson that would be left holding a
	// quiz with nothing to ask, and that sentence is the entire content of the confirm
	// put in front of the reviewer. So the words are read off the response by a handler,
	// which frappe hands the whole reply to.
	//
	// Registering a handler does a second thing, and it is just as deliberate:
	// `frappe.request.cleanup` puts a throw up as a modal only when NO handler exists
	// for its exception type, so this turns that modal off. A reviewer is doing hundreds
	// of these. A refusal belongs on the card it happened to, where the next attempt
	// already is, rather than in a dialog that has to be dismissed before the work can
	// continue.
	let said = [];
	const capture = (response) => {
		said = tq_messages(response);
	};
	return frappe
		.xcall(method, args, "POST", {
			// `_reviewer` throws PermissionError and everything else throws
			// ValidationError; between them that is every refusal review.py makes.
			error_handlers: { ValidationError: capture, PermissionError: capture },
		})
		.catch(() => Promise.reject({ messages: said }));
}

function tq_messages(r) {
	// A throw comes back 417 with the text in `_server_messages`: a JSON array of JSON
	// strings, each of them an object with a `message` key — except when the server
	// queued a bare string, which older paths still do.
	let raw = [];
	try {
		raw = JSON.parse((r && r._server_messages) || "[]");
	} catch (e) {
		raw = [];
	}
	const out = [];
	(raw || []).forEach((entry) => {
		let parsed = entry;
		try {
			parsed = JSON.parse(entry);
		} catch (e) {
			// A bare string. Keep it as it stands.
		}
		const text = parsed && typeof parsed === "object" ? parsed.message : parsed;
		// Server messages are HTML. Everything downstream renders them with .text(),
		// so they are reduced to words here rather than trusted to a markup context.
		const plain = tq_plain(text);
		if (plain) out.push(plain);
	});
	return out;
}

function tq_first(err) {
	const messages = (err && err.messages) || [];
	return messages.length ? messages.join(" ") : "";
}

function tq_empty_quiz_refusal(messages) {
	// `reject_question` throws ValidationError for all six of its refusals, so there is
	// nothing structural in the response to tell them apart — only the words. This app
	// ships no translation files, so the sentence arrives as review.py writes it; if
	// that ever stops being true, this match is what needs revisiting, and the caller
	// falls back on the structural prediction below so the flow degrades rather than
	// disappears.
	return (messages || []).some((m) => /no quiz|nothing to ask/i.test(m));
}

// ------------------------------------------------------------------ text helpers

function tq_plain(html) {
	// `question_text`, `explanation` and the option feedback are Text Editor / Small
	// Text fields, so what comes back may be markup. The authoring canvas reduces them
	// the same way before putting them in a textarea, and this page shows and edits the
	// same reduction — which is also why an untouched field is never sent back (see
	// collect_edits): reducing it and saving it would silently strip an author's markup.
	//
	// DOMParser rather than the canvas's `div.innerHTML = html`: a detached div still
	// starts image loads, so an `<img src=x onerror=…>` sitting in an author field
	// would run. A DOMParser document fetches nothing and executes nothing.
	if (html === null || html === undefined || html === "") return "";
	try {
		const doc = new DOMParser().parseFromString(String(html), "text/html");
		return (doc.body.textContent || "").replace(/\s+/g, " ").trim();
	} catch (e) {
		// There is no browser the Desk runs in without DOMParser. The fallback is here so
		// that the contract — this returns plain text — holds anyway: handing the raw
		// markup back would put `<p>` into a .text() node, where it reads as a bug in the
		// question rather than as a bug here.
		return String(html)
			.replace(/<[^>]*>/g, " ")
			.replace(/\s+/g, " ")
			.trim();
	}
}

function tq_lines(text) {
	// `correct_text_answers` is newline-separated and `_validate_short_answer` rewrites
	// it that way on every save, so this is the shape it is stored in.
	return String(text || "")
		.split("\n")
		.map((line) => line.trim())
		.filter(Boolean);
}

function tq_num(value) {
	return cint(value);
}

// ------------------------------------------------------------------- answer flags

// `TrainingQuestion.validate` restated as data the card can show. Two jobs, and the
// second is the one worth having:
//
//   * a `blocking` flag is a key that `doc.save()` would refuse, so accepting the
//     question as it stands CANNOT succeed. The card says so and disables Accept
//     rather than letting the reviewer spend a keystroke on a guaranteed error.
//   * the rest are soft — a Multiple Choice with one right answer saves perfectly
//     well and is still, nine times in ten, a drafting slip worth a second look.
//
// `editable` says whether this screen can fix it. `accept_question` takes the stem,
// the explanation and the options and nothing else, so a Short Answer question with no
// accepted answers has to go back to its own form.
function tq_flags(q) {
	const flags = [];
	const type = q.question_type || "";
	const options = q.options || [];

	if (type === "Short Answer") {
		if (!tq_lines(q.correct_text_answers).length) {
			flags.push({
				text: __("No accepted answer is listed, so nobody can ever get this right."),
				blocking: true,
				editable: false,
			});
		}
		return flags;
	}

	if (options.length < TQ_MIN_OPTIONS || options.length > TQ_MAX_OPTIONS) {
		flags.push({
			text: __("{0} options. A question needs between {1} and {2}.", [
				options.length,
				TQ_MIN_OPTIONS,
				TQ_MAX_OPTIONS,
			]),
			blocking: true,
			editable: true,
		});
	}

	const correct = options.filter((o) => tq_num(o.is_correct));
	if (!options.length) {
		// Nothing more to say; the count flag above already covers it.
	} else if (!correct.length) {
		flags.push({
			text: __("Nothing is ticked correct, so the question can never be passed."),
			blocking: true,
			editable: true,
		});
	} else if (correct.length === options.length) {
		flags.push({
			text: __("Every option is ticked correct, so it cannot be got wrong."),
			blocking: true,
			editable: true,
		});
	} else if (TQ_ONE_ANSWER_TYPES.indexOf(type) !== -1 && correct.length > 1) {
		flags.push({
			text: __("{0} allows one correct option and {1} are ticked.", [type, correct.length]),
			blocking: true,
			editable: true,
		});
	} else if (type === "Multiple Choice" && correct.length === 1) {
		flags.push({
			text: __("A Multiple Choice question with a single right answer. Worth a look."),
			blocking: false,
			editable: true,
		});
	}

	const seen = {};
	let blank_reported = false;
	options.forEach((o) => {
		const key = tq_plain(o.option_text).toLowerCase();
		if (!key) {
			// option_text is mandatory on the child table, so a blank one is refused.
			if (!blank_reported) {
				flags.push({ text: __("An option has no text."), blocking: true, editable: true });
				blank_reported = true;
			}
		} else if (seen[key]) {
			flags.push({
				text: __("Two options both read “{0}”. A learner cannot tell them apart.", [
					tq_plain(o.option_text),
				]),
				blocking: true,
				editable: true,
			});
		}
		seen[key] = true;
	});

	if (type === "True-False") {
		// `_normalise_true_false` refuses anything but the literal pair rather than
		// guessing which of Yes/No is the true one — it used to guess, and silently
		// rewrote answer keys.
		const texts = options.map((o) => tq_plain(o.option_text).toLowerCase()).sort();
		if (options.length !== 2 || texts[0] !== "false" || texts[1] !== "true") {
			flags.push({
				text: __("True-False needs exactly two options, spelled True and False."),
				blocking: true,
				editable: true,
			});
		}
	}

	return flags;
}

function tq_blocker(q) {
	return tq_flags(q).filter((f) => f.blocking)[0] || null;
}

// ------------------------------------------------------------------------ the page

class TrainingReview {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;

		this.queue = { total: 0, reviewed: 0, courses: [] };
		this.lesson = null;
		this.cards = [];
		this.focused = -1;
		this.course_filter = null;
		// The view the last load was for, {lesson, course}: what refresh compares the route
		// against. See "the route" above.
		this.view = null;
		// The route the reviewer chose not to follow, to keep an edit (follow).
		this.declined = null;
		this.loading = false;
		this.loaded = false;
		this.syncing = false;
		// Verdicts whose round trip has not landed. The next lesson is not fetched
		// while any of these are outstanding — see maybe_advance.
		this.inflight = 0;
		this.session = { accepted: 0, rejected: 0 };
		// The queue total when this page opened, so the progress bar can say something
		// true about this sitting rather than about the app's whole history.
		this.opened_total = null;
		// course -> title, remembered across loads. A course leaves `queue.courses` the
		// moment its last question is accepted, and both the filter control and the
		// finished state still want to be able to name it.
		this.seen_courses = {};
		// course -> "this one has already been announced as cleared", so finishing a
		// course says so once rather than on every verdict that follows it.
		this.cleared = {};

		this.build();

		// Teardown is correctness, not tidiness. frappe creates a page div once
		// (views/container.js add_page) and never removes it, so a document-level
		// keydown handler that is never removed goes on firing on every other page in
		// the Desk — j and k would scroll nothing and `r` would try to reject a card
		// that is not on screen. There is no on_page_hide hook in v16; this listens for
		// the jQuery "hide" event frappe fires on the outgoing page, the idiom learn.js
		// uses for the same reason.
		$(wrapper).on("hide", () => this.destroy());

		this.page.set_secondary_action(__("Refresh"), () => this.refresh(true));
		this.page.add_menu_item(__("Open a specific lesson"), () => this.jump_to_lesson());
		this.page.add_menu_item(__("Courses still in Draft"), () =>
			frappe.set_route("List", "Training Course", { status: "Draft" })
		);
	}

	// ------------------------------------------------------------------ lifecycle

	refresh(force) {
		// Re-bound here rather than in the constructor because the teardown on "hide"
		// takes it away, and on_page_show is the only thing that runs on the way back in.
		this.bind_keys();

		// A "Stay" (follow) holds only while the route still names the view it was said to. Once
		// Back or Forward has moved on, a later return to that view is a new request, and the
		// catch-ups below (load's finally, maybe_advance) must not drop it as already declined.
		if (tq_view_key(this.route_view()) !== this.declined) this.declined = null;

		// A show that finds a load running is picked up when it ends (load's finally), and one
		// that finds a verdict in flight when the last of them lands (follow, maybe_advance).
		if (this.loading) return;
		// The route names a different course or lesson from the one on screen: Back, Forward,
		// a pasted link, or a move made on this page (see "the route").
		const wanted = this.route_view();
		if (wanted && (!this.loaded || tq_view_key(wanted) !== tq_view_key(this.view))) {
			this.follow(wanted);
			return;
		}
		if (force || !this.loaded) {
			this.load(this.view_args(this.view));
			return;
		}
		// A lesson is already open. on_page_show fires on every route change back into
		// this page, and refetching would throw away a half-written edit and the
		// reviewer's place in a lesson they are part-way through. So the lesson is left
		// exactly as it is and only the numbers beside it are brought up to date — which
		// is the whole job `get_review_queue` exists to do.
		this.sync_queue();
	}

	sync_queue() {
		if (this.syncing) return;
		this.syncing = true;
		tq_call("erpnext_enhancements.training.review.get_review_queue", {})
			.then((queue) => {
				if (!queue) return;
				this.adopt_queue(queue);
				if (!this.lesson && tq_num(this.queue.total) > 0) {
					// The finished panel is on screen and there is work again — somebody
					// has seeded a course since. Nothing here is half-written, so this is
					// the one case where taking the full lesson back costs nothing.
					this.load({ course: this.course_filter });
					return;
				}
				this.paint_bar();
				this.paint_courses();
			})
			.catch(() => {
				// Swallowed on purpose. Nobody asked for this; it is a top-up of a page
				// that is already showing work, and the numbers it failed to refresh are
				// still the ones the last load returned. Interrupting a reviewer to report
				// a failed background read would cost more than the stale count does.
			})
			.finally(() => {
				this.syncing = false;
			});
	}

	bind_keys() {
		$(document).off("keydown.tq-review").on("keydown.tq-review", (event) => this.on_key(event));
	}

	destroy() {
		$(document).off("keydown.tq-review");
	}

	// The view the route asks for, or null while this page is not the one on screen.
	route_view() {
		const route = frappe.get_route() || [];
		if (route[0] !== TQ_ROUTE) return null;
		if (route[1] === "lesson" && route[2]) return { lesson: route[2], course: null };
		if (route[1] === "course" && route[2]) return { lesson: null, course: route[2] };
		return { lesson: null, course: null };
	}

	view_args(view) {
		return view && view.lesson ? { lesson: view.lesson } : { course: this.course_filter };
	}

	// A move to another course or lesson. The reviewer's own moves are history entries;
	// `replace` is for the one the page makes by itself (leave_lesson). The show the route
	// fires does the loading. A move to the view the route already names loads it again,
	// the way "Look again" does.
	go(view, replace) {
		if (tq_view_key(view) === tq_view_key(this.route_view())) {
			this.follow(view);
			return;
		}
		if (replace) frappe.route_flags.replace_route = true;
		frappe.set_route(tq_view_route(view));
		// v16 clears route_flags only in set_route's .finally, after a 100 ms timer and
		// after_ajax, so a reviewer's own move made while a request is in flight would read a
		// stale replace and overwrite this entry. push_state has already read the flag.
		frappe.route_flags.replace_route = false;
		// set_route has written the entry by the time it returns (push_state runs before its
		// promise does). A lesson's entry is marked as pushed from this page, so leaving the
		// lesson can step back onto the view it was opened from rather than add one.
		if (view.lesson && !replace) {
			try {
				window.history.replaceState({ tq_opened: view.lesson }, "");
			} catch (e) {
				// Without the mark, leave_lesson hands the entry over instead.
			}
		}
	}

	// Out of a lesson opened by name, to the queue: a finished lesson is not somewhere Back
	// should reopen. When this page pushed the lesson's entry, the one behind it is the view
	// the reviewer opened it from, and that is where this goes. Otherwise (a pasted link, a
	// reload) the lesson's entry is handed to the queue.
	leave_lesson() {
		const here = this.route_view();
		let state = null;
		try {
			state = window.history.state;
		} catch (e) {
			state = null;
		}
		if (here && here.lesson && state && state.tq_opened === here.lesson) {
			window.history.back();
			return;
		}
		this.go({ lesson: null, course: this.course_filter }, true);
	}

	follow(view) {
		// Not while a verdict is in flight: maybe_advance catches up with the route once the
		// last one lands. A load now could be handed back the lesson that verdict is emptying,
		// its question still pending, which is the double accept maybe_advance waits to avoid.
		// And a save-and-accept's card is out of `cards` until its reply comes, so the edit
		// check below cannot see the typed corrections a refusal would put back on screen.
		if (this.inflight > 0) return;
		const open = () => {
			this.declined = null;
			if (!view.lesson) this.course_filter = view.course || null;
			this.load(this.view_args(view));
		};
		// A load paints a new lesson over the questions pane, and a question being edited is
		// half-written work that nothing else holds. Back must not throw it away unasked.
		if (!this.cards.some((rec) => rec.editing)) {
			open();
			return;
		}
		frappe.confirm(
			__("You are part-way through editing a question. Leave the edit and go?"),
			open,
			() => {
				// Staying: the lesson and the edit stay on screen, under the entry Back moved
				// to. The address is left alone, because writing over that entry would lose it,
				// and Forward returns to the one that matches. Nothing chases the route later
				// (load's finally, maybe_advance) while it still names this view, unless the
				// view on screen empties; refresh forgets the decline once the route moves on.
				this.declined = tq_view_key(view);
				this.paint_courses();
			}
		);
	}

	load(args) {
		if (this.loading) return;
		this.loading = true;
		this.page.set_indicator(__("Loading…"), "blue");

		const params = {};
		if (args && args.lesson) params.lesson = args.lesson;
		if (args && args.course) params.course = args.course;
		this.view = { lesson: params.lesson || null, course: params.lesson ? null : params.course || null };

		tq_call("erpnext_enhancements.training.review.get_review_lesson", params)
			.then((data) => {
				this.loaded = true;
				this.notice(null);
				this.adopt(data || {});
				this.paint();
			})
			.catch((err) => this.fail(err))
			.finally(() => {
				this.loading = false;
				// A Back or Forward that came while this was loading found `loading` set and
				// was dropped by refresh. The route is the truth: catch up with it now —
				// unless the reviewer has already said to stay (follow).
				const wanted = this.route_view();
				const key = tq_view_key(wanted);
				if (wanted && key !== tq_view_key(this.view) && key !== this.declined) this.follow(wanted);
			});
	}

	adopt(data) {
		this.adopt_queue(data.queue);
		this.lesson = data.lesson || null;
		if (this.lesson) this.seen_courses[this.lesson.course] = this.lesson.course_title;
	}

	// Shared by the lesson load and the background top-up, so the two cannot come to
	// different conclusions about the same payload.
	adopt_queue(queue) {
		this.queue = queue || { total: 0, reviewed: 0, courses: [] };
		(this.queue.courses || []).forEach((c) => {
			this.seen_courses[c.course] = c.course_title;
		});

		const total = tq_num(this.queue.total);
		// Re-baselined upwards rather than clamped: somebody seeding a new course
		// mid-session must not make the bar read as going backwards, and pretending the
		// new questions are not there would be the other kind of dishonest.
		if (this.opened_total === null || total > this.opened_total) this.opened_total = total;
	}

	fail(err) {
		this.page.set_indicator(__("Unavailable"), "red");
		const text = tq_first(err) || __("The review queue could not be loaded.");
		if (this.loaded) {
			// Something is already on screen and is still true. Say what went wrong
			// without taking the reviewer's lesson away from them.
			this.notice(text);
			return;
		}
		this.$split.hide();
		this.$done.hide();
		this.notice(text);
	}

	notice(text) {
		if (!text) {
			this.$notice.hide().empty();
			return;
		}
		this.$notice
			.empty()
			.append($('<span class="tq-notice-text"></span>').text(text))
			.append(
				$('<button type="button" class="tq-linkish"></button>')
					.text(__("Try again"))
					.on("click", () => this.load(this.view_args(this.view)))
			)
			.show();
	}

	jump_to_lesson() {
		// The queue hands out lessons in reading order and has no "skip" — so this is
		// the way back to one, and the way into a lesson somebody else mentioned.
		frappe.prompt(
			{
				fieldtype: "Link",
				options: "Training Lesson",
				label: __("Lesson"),
				fieldname: "lesson",
				reqd: 1,
			},
			// The prompt has closed by now (frappe.prompt hides, then calls back), so the
			// route change cannot take it with it.
			(values) => this.go({ lesson: values.lesson, course: null }),
			__("Which lesson?"),
			__("Open")
		);
	}

	// ------------------------------------------------------------------- the frame

	build() {
		this.$body = $('<div class="tq-page"></div>').appendTo(this.page.body);

		// The honest one-liner, permanently on screen, because what this screen asks of
		// somebody is not obvious from a list of buttons: accepting is not filing a
		// question away, it is putting your name against a machine's answer key.
		$('<div class="tq-stance"></div>')
			.text(
				__(
					"Read each drafted question against the lesson it came from. Accepting records you as the person who vouched for its answer key."
				)
			)
			.appendTo(this.$body);

		this.$notice = $('<div class="tq-notice"></div>').appendTo(this.$body).hide();

		const $bar = $('<div class="tq-bar"></div>').appendTo(this.$body);
		const $row = $('<div class="tq-bar-row"></div>').appendTo($bar);
		this.$stats = $('<div class="tq-stats"></div>').appendTo($row);

		const $controls = $('<div class="tq-controls"></div>').appendTo($row);
		const $label = $('<label class="tq-filter-wrap"></label>').appendTo($controls);
		$label.append($('<span class="tq-filter-label"></span>').text(__("Working through")));
		this.$course = $('<select class="form-control tq-filter"></select>').appendTo($label);
		this.$course.on("change", () => this.pick_course(this.$course.val()));

		this.$progress = $('<div class="tq-progress"></div>')
			.attr({ role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100 })
			.appendTo($bar);
		this.$progress_fill = $('<div class="tq-progress-fill"></div>').appendTo(this.$progress);
		this.$meta = $('<div class="tq-meta"></div>').appendTo($bar);

		this.$split = $('<div class="tq-split"></div>').appendTo(this.$body);
		this.$lesson = $('<div class="tq-pane tq-pane-lesson"></div>').appendTo(this.$split);
		this.$questions = $('<div class="tq-pane tq-pane-questions"></div>').appendTo(this.$split);
		this.$done = $('<div class="tq-done"></div>').appendTo(this.$body).hide();
	}

	// The bar is painted LAST on purpose: its "in this lesson" tile counts the cards
	// that are actually on screen, so it has to run after the cards exist. Counting the
	// payload instead would be one number that never changes as the pane empties.
	paint() {
		this.paint_courses();
		if (!this.lesson) {
			this.paint_finished();
			this.paint_bar();
			return;
		}
		this.$done.hide();
		this.$split.show();
		this.paint_lesson();
		this.paint_questions();
		this.paint_bar();
	}

	// --------------------------------------------------------------- the numbers

	paint_bar() {
		const total = tq_num(this.queue.total);
		const course = this.course_row(this.lesson && this.lesson.course);
		const done = this.session.accepted + this.session.rejected;

		this.$stats.empty();
		this.stat(
			total,
			__("waiting"),
			total ? __("across {0} courses", [(this.queue.courses || []).length]) : "",
			true
		);
		if (this.lesson) {
			this.stat(
				this.cards.length,
				__("in this lesson"),
				__("of {0} in its pool", [tq_num(this.lesson.pool_size)])
			);
			this.stat(
				course ? tq_num(course.pending) : 0,
				__("left in this course"),
				course ? __("across {0} lessons", [tq_num(course.lessons)]) : ""
			);
		}
		this.stat(done, __("done by you here"), __("{0} accepted, {1} rejected", [this.session.accepted, this.session.rejected]));

		// The bar measures this sitting, which is the only span of time the page can
		// speak about honestly. `queue.reviewed` counts every AI question that has ever
		// carried a reviewer — 14 of them before this backlog existed — so a bar drawn
		// from it would say "4% done" to somebody who has just cleared a whole course.
		const opened = Math.max(1, tq_num(this.opened_total));
		const cleared = Math.max(0, opened - total);
		const percent = Math.max(0, Math.min(100, Math.round((cleared / opened) * 100)));
		this.$progress.attr("aria-valuenow", percent);
		this.$progress_fill.css("width", percent + "%");

		this.$meta
			.empty()
			.append(
				$("<span></span>").text(
					__("{0} of the {1} that were waiting when you opened this page.", [cleared, opened])
				)
			)
			.append(
				$('<span class="tq-meta-dim"></span>').text(
					" " +
						__("{0} AI-drafted questions carry a reviewer in all.", [tq_num(this.queue.reviewed)])
				)
			);

		if (total > 0) this.page.set_indicator(__("{0} waiting", [total]), "orange");
		else this.page.set_indicator(__("All reviewed"), "green");
	}

	stat(value, label, sub, emphasise) {
		const $tile = $('<div class="tq-stat"></div>').appendTo(this.$stats);
		if (emphasise) $tile.addClass("is-lead");
		$tile.append($('<div class="tq-stat-value"></div>').text(tq_num(value)));
		$tile.append($('<div class="tq-stat-label"></div>').text(label));
		if (sub) $tile.append($('<div class="tq-stat-sub"></div>').text(sub));
		return $tile;
	}

	course_row(name) {
		if (!name) return null;
		return (this.queue.courses || []).filter((c) => c.course === name)[0] || null;
	}

	paint_courses() {
		const current = this.course_filter || "";
		this.$course.empty();
		$("<option></option>")
			.attr("value", "")
			.text(__("Everything — {0} waiting", [tq_num(this.queue.total)]))
			.appendTo(this.$course);

		let present = false;
		(this.queue.courses || []).forEach((c) => {
			if (c.course === current) present = true;
			$("<option></option>")
				.attr("value", c.course)
				.text(__("{0} — {1} waiting", [c.course_title, tq_num(c.pending)]))
				.appendTo(this.$course);
		});

		// A filtered course drops out of `queue.courses` the instant its last question
		// is accepted, and a <select> whose value no longer exists silently falls back
		// to its first option — which reads as the filter clearing itself just as the
		// reviewer finishes. Keep the course visible, and say it is done.
		if (current && !present) {
			$("<option></option>")
				.attr("value", current)
				.text(__("{0} — done", [this.seen_courses[current] || current]))
				.appendTo(this.$course);
		}
		this.$course.val(current);
	}

	// Routes only: the show that follows sets course_filter and loads (follow).
	pick_course(value) {
		this.go({ lesson: null, course: value || null });
	}

	// ----------------------------------------------------------------- the lesson

	paint_lesson() {
		const lesson = this.lesson;
		this.$lesson.empty();

		const $head = $('<header class="tq-lesson-head"></header>').appendTo(this.$lesson);
		const $crumb = $('<div class="tq-crumb"></div>').appendTo($head);
		$crumb.append(
			$('<button type="button" class="tq-linkish"></button>')
				.text(lesson.course_title)
				.on("click", () => frappe.set_route("Form", "Training Course", lesson.course))
		);
		$crumb.append($('<span class="tq-crumb-sep"></span>').text("·"));
		$crumb.append($("<span></span>").text(__("Version {0}", [tq_num(lesson.version_number)])));
		if (lesson.course_status) {
			$crumb.append(
				$('<span class="tq-status"></span>')
					.addClass("is-" + String(lesson.course_status).toLowerCase().replace(/\s+/g, "-"))
					.text(lesson.course_status)
			);
		}

		$head.append($('<h2 class="tq-lesson-title"></h2>').text(lesson.lesson_title || lesson.lesson));

		const $sub = $('<div class="tq-lesson-sub"></div>').appendTo($head);
		if (lesson.chapter_key) {
			$sub.append(
				$("<span></span>").text(
					__("{0}, lesson {1}", [lesson.chapter_key, tq_num(lesson.idx_in_chapter)])
				)
			);
		}
		$sub.append(
			$('<button type="button" class="tq-linkish"></button>')
				.text(__("Open in the course canvas"))
				.on("click", () => {
					// The escape hatch for "the question is fine, the lesson is not".
					// The canvas reads `course` off frappe.route_options.
					frappe.set_route("training-canvas", { course: lesson.course });
				})
		);

		const blocks = lesson.blocks || [];
		const $blocks = $('<div class="tq-blocks"></div>').appendTo(this.$lesson);
		if (!blocks.length) {
			// Not a neutral empty state. If the lesson taught nothing, no question drawn
			// from it is answerable, and that is the single most important thing this
			// screen can tell a reviewer.
			$blocks.append(
				$('<div class="tq-nocontent"></div>').text(
					__("This lesson has no content at all. Nothing here could have taught any of the answers beside it.")
				)
			);
			return;
		}

		let media = 0;
		blocks.forEach((block) => {
			if (!block.html && !(block.items || []).length && block.block_type !== "Divider") media += 1;
			$blocks.append(this.block_node(block));
		});

		if (media) {
			// Said once, at the end, rather than repeated under every placeholder.
			// `_blocks` does not fetch media on purpose — a signed URL each, and none of
			// them changes whether an option is defensible — but a reviewer has to know
			// that is a choice and not an empty lesson.
			$blocks.append(
				$('<div class="tq-medianote"></div>').text(
					__("Images, PDFs and video are not fetched into this screen. If a question depends on one, open the lesson in the canvas.")
				)
			);
		}
	}

	block_node(block) {
		const type = block.block_type || "";
		const $block = $('<section class="tq-block"></section>');

		if (type === "Divider") {
			// A rule is what a divider IS. A placeholder saying "Divider — not shown"
			// would be noise in a pane meant to read like the lesson.
			return $block.addClass("tq-block-rule").append($('<hr class="tq-rule" />'));
		}

		if (block.heading) $block.append($('<h4 class="tq-block-head"></h4>').text(block.heading));

		if (block.html) {
			if (type === "Callout") {
				$block.addClass("tq-callout");
				const tone = String(block.callout_tone || "").toLowerCase();
				if (tone) $block.addClass("tq-tone-" + tone);
			}
			// The one thing on this page that is injected. `_blocks` runs it through
			// `sanitize_html(…, always_sanitize=True)` on the way out precisely because
			// it lands in a manager's browser, and nothing else in the request has
			// cleaned it.
			$block.append($('<div class="tq-prose"></div>').html(block.html));
		} else if ((block.items || []).length) {
			const $list = $('<ul class="tq-items"></ul>');
			block.items.forEach((item) => {
				// Text, not markup — `block.html` is the ONE thing on this page that is
				// injected and this is not it. `_list_items` does sanitise these, but the
				// rule worth keeping is the simple one: a reader of this file should be
				// able to find every injection by looking for `.html(`.
				//
				// Run through tq_plain rather than straight into .text() all the same,
				// because sanitising escapes as it goes: a checklist step would otherwise
				// read "pump &amp; motor" on screen, which looks like a bug in the lesson.
				$list.append($("<li></li>").text(tq_plain(item)));
			});
			$block.append($list);
		} else {
			$block.append(
				$('<div class="tq-media"></div>').text(__("{0} — not shown here", [type || __("Content")]))
			);
		}

		if (block.caption) $block.append($('<div class="tq-caption"></div>').text(block.caption));
		return $block;
	}

	// -------------------------------------------------------------- the questions

	paint_questions() {
		this.cards = [];
		this.focused = -1;
		this.$questions.empty();

		const $head = $('<div class="tq-panehead"></div>').appendTo(this.$questions);
		this.$qcount = $('<div class="tq-panehead-count"></div>').appendTo($head);
		$head.append(this.keys_legend());

		this.$cards = $('<div class="tq-cards"></div>').appendTo(this.$questions);
		(this.lesson.questions || []).forEach((q) => {
			const rec = this.build_card(q);
			this.cards.push(rec);
			this.$cards.append(rec.$el);
		});

		this.renumber();
		this.paint_qcount();

		if (!this.cards.length) {
			// Only reachable through "Open a specific lesson": the queue never hands back
			// a lesson with nothing pending. Somebody who went looking at a finished
			// lesson needs a way back into the run.
			const $empty = $('<div class="tq-lesson-clear"></div>').appendTo(this.$cards);
			$empty.append(
				$("<div></div>").text(__("Nothing in this lesson is waiting on a reviewer."))
			);
			$('<button type="button" class="btn btn-primary btn-sm"></button>')
				.text(__("Back to the queue"))
				.on("click", () => this.leave_lesson())
				.appendTo($empty);
			return;
		}

		// The class, not the caret. A card is "focused" for a / e / r from the moment
		// the lesson lands, so the first verdict costs one keystroke and no click — but
		// stealing the real caret on arrival would yank it out of the awesomebar of
		// somebody who was still typing their way here.
		this.set_focus(0, false);
	}

	keys_legend() {
		const $legend = $('<div class="tq-keys"></div>');
		const keys = [
			[["A"], __("accept")],
			[["E"], __("edit")],
			[["R"], __("reject")],
			[["J", "K"], __("move")],
		];
		keys.forEach(([glyphs, what]) => {
			const $item = $('<span class="tq-keys-item"></span>');
			glyphs.forEach((glyph, n) => {
				if (n) $item.append(document.createTextNode("/"));
				$item.append($('<kbd class="tq-kbd"></kbd>').text(glyph));
			});
			$item.append($("<span></span>").text(what));
			$legend.append($item);
		});
		return $legend;
	}

	paint_qcount() {
		const waiting = this.cards.length;
		const pool = tq_num(this.lesson ? this.lesson.pool_size : 0);
		this.$qcount.text(
			waiting === 1
				? __("1 question waiting, of {0} in this lesson's quiz pool", [pool])
				: __("{0} questions waiting, of {1} in this lesson's quiz pool", [waiting, pool])
		);
	}

	renumber() {
		this.cards.forEach((rec, i) => rec.$num.text(i + 1));
	}

	build_card(q) {
		// The lesson is stamped on the card so a verdict that fails AFTER the reviewer
		// has moved on — Refresh, or a course change, while the call was in flight —
		// cannot put the card back into somebody else's lesson. See restore().
		const rec = { q: q, editing: false, busy: false, draft: null, lesson: this.lesson.lesson };
		rec.$el = $('<article class="tq-card" tabindex="0"></article>').attr("data-question", q.question);
		rec.$num = $('<span class="tq-card-n"></span>');

		const $top = $('<header class="tq-card-top"></header>').appendTo(rec.$el);
		$top.append(rec.$num);
		const $tags = $('<div class="tq-tags"></div>').appendTo($top);
		$tags.append($('<span class="tq-tag"></span>').text(q.question_type || __("No type")));
		if (q.difficulty) $tags.append($('<span class="tq-tag"></span>').text(q.difficulty));
		if (tq_num(q.points)) {
			$tags.append($('<span class="tq-tag"></span>').text(__("{0} pts", [tq_num(q.points)])));
		}

		rec.$error = $('<div class="tq-card-error"></div>').appendTo(rec.$el);
		rec.$body = $('<div class="tq-card-body"></div>').appendTo(rec.$el);
		rec.$acts = $('<footer class="tq-acts"></footer>').appendTo(rec.$el);

		// Clicking anywhere on a card makes it the one the keyboard is aimed at, so
		// mouse and keyboard cannot end up pointing at different questions.
		rec.$el.on("focusin mousedown", () => {
			const i = this.cards.indexOf(rec);
			if (i !== -1 && i !== this.focused) this.set_focus(i, false);
		});

		this.paint_read(rec);
		return rec;
	}

	paint_read(rec) {
		const q = rec.q;
		rec.editing = false;
		rec.$el.removeClass("is-editing");
		rec.$body.empty();
		rec.$acts.empty();

		rec.$body.append($('<div class="tq-stem"></div>').text(tq_plain(q.question_text)));
		rec.$body.append(this.answer_key(q));

		const explanation = tq_plain(q.explanation);
		if (explanation) {
			const $why = $('<div class="tq-why"></div>').appendTo(rec.$body);
			$why.append($('<div class="tq-why-head"></div>').text(__("Shown after grading")));
			$why.append($("<div></div>").text(explanation));
		} else {
			rec.$body.append(
				$('<div class="tq-why tq-why-missing"></div>').text(
					__("No explanation — the learner is told nothing about why they were wrong.")
				)
			);
		}

		const flags = tq_flags(q);
		if (flags.length) {
			const $flags = $('<div class="tq-flags"></div>').appendTo(rec.$body);
			flags.forEach((flag) => {
				$('<div class="tq-flag"></div>')
					.toggleClass("is-blocking", !!flag.blocking)
					.text(flag.text)
					.appendTo($flags);
			});
		}

		rec.$body.append(this.provenance(q));
		this.paint_actions(rec, tq_blocker(q));
	}

	answer_key(q) {
		const $box = $('<div class="tq-key"></div>');
		// Named, and named loudly. `_questions` sends `is_correct` on purpose — it is
		// the thing being reviewed — and a reviewer skimming a card has to be able to
		// see the key without hunting for it.
		$box.append($('<div class="tq-key-head"></div>').text(__("Answer key")));

		if ((q.question_type || "") === "Short Answer") {
			const answers = tq_lines(q.correct_text_answers);
			if (!answers.length) {
				$box.append($('<div class="tq-muted"></div>').text(__("Nothing is accepted.")));
				return $box;
			}
			const $list = $('<ul class="tq-answers"></ul>').appendTo($box);
			answers.forEach((answer) => $list.append($("<li></li>").text(answer)));
			$box.append(
				$('<div class="tq-muted"></div>').text(
					__("Accepted answers are edited on the question record, not here.")
				)
			);
			return $box;
		}

		const options = q.options || [];
		if (!options.length) {
			$box.append($('<div class="tq-muted"></div>').text(__("This question has no options.")));
			return $box;
		}

		const $list = $('<ul class="tq-opts"></ul>').appendTo($box);
		options.forEach((option) => {
			const right = tq_num(option.is_correct);
			const $item = $('<li class="tq-opt"></li>').toggleClass("is-correct", !!right).appendTo($list);
			$item.append($('<span class="tq-opt-mark" aria-hidden="true"></span>').text(right ? "✓" : ""));
			const $main = $('<div class="tq-opt-main"></div>').appendTo($item);
			const $text = $('<div class="tq-opt-text"></div>').text(tq_plain(option.option_text));
			if (right) {
				// Four signals for one fact and none of them colour alone: the tick, the
				// weight, the tint and this word. It is the line the whole screen exists to
				// put in front of somebody, so it is written out rather than left to a green
				// border that a reviewer has to know the meaning of.
				$text.append($('<span class="tq-opt-flag"></span>').text(__("correct")));
			}
			$main.append($text);
			const why = tq_plain(option.explanation);
			if (why) $main.append($('<div class="tq-opt-why"></div>').text(why));
		});
		return $box;
	}

	provenance(q) {
		const $prov = $('<div class="tq-prov"></div>');
		$prov.append(
			$("<span></span>").text(
				q.ai_model ? __("Drafted by {0}", [q.ai_model]) : __("Drafted by an AI, model not recorded")
			)
		);
		// `ai_source` is empty on all 367 — only `accept_ai_suggestions` ever records a
		// grounding quote — so it gets no row at all until there is something in it.
		if (q.ai_source) {
			const $details = $('<details class="tq-source"></details>').appendTo($prov);
			$details.append($("<summary></summary>").text(__("Grounded in")));
			$details.append($("<div></div>").text(tq_plain(q.ai_source)));
		}
		$prov.append(
			$('<button type="button" class="tq-linkish"></button>')
				.text(__("Question record"))
				.on("click", () => frappe.set_route("Form", "Training Question", q.question))
		);
		return $prov;
	}

	paint_actions(rec, blocker) {
		rec.$acts.empty();
		rec.$accept = this.action_button("btn-primary", __("Accept"), "A", () => this.accept(rec.q.question));
		rec.$acts.append(rec.$accept);
		rec.$acts.append(this.action_button("btn-default", __("Edit"), "E", () => this.edit(rec.q.question)));
		rec.$acts.append(
			this.action_button("btn-default tq-act-reject", __("Reject"), "R", () => this.reject(rec.q.question))
		);

		if (blocker) {
			// `accept_question` saves through `doc.save()`, so this key would be refused
			// by the controller. Disabling the button turns a guaranteed round trip and a
			// server error into a sentence the reviewer can act on.
			rec.$accept.prop("disabled", true).attr(
				"title",
				blocker.editable
					? __("Fix it first — press E. {0}", [blocker.text])
					: __("This one has to be fixed on the question record. {0}", [blocker.text])
			);
		}
	}

	action_button(classes, label, key, handler) {
		const $button = $('<button type="button" class="btn btn-sm tq-act"></button>')
			.addClass(classes)
			.on("click", handler);
		$button.append($("<span></span>").text(label));
		$button.append($('<kbd class="tq-kbd"></kbd>').text(key));
		return $button;
	}

	// -------------------------------------------------------------------- editing

	edit(name) {
		const found = this.find(name);
		if (!found || found.rec.busy || found.rec.editing) return;
		const rec = found.rec;
		const q = rec.q;

		// The plain-text reduction is the baseline every dirty check below compares
		// against, so an untouched field is recognised as untouched.
		rec.draft = {
			question_text: tq_plain(q.question_text),
			explanation: tq_plain(q.explanation),
			options: (q.options || []).map((option) => ({
				option_key: option.option_key || "",
				option_text: tq_plain(option.option_text),
				is_correct: tq_num(option.is_correct),
				explanation: tq_plain(option.explanation),
			})),
		};
		rec.editing = true;
		rec.$el.addClass("is-editing");
		rec.$error.removeClass("is-on").empty();
		this.paint_editor(rec);
	}

	paint_editor(rec) {
		const q = rec.q;
		rec.$body.empty();
		rec.$acts.empty();

		const $form = $('<form class="tq-editor"></form>').appendTo(rec.$body);
		$form.on("submit", (event) => {
			event.preventDefault();
			this.save_edit(rec);
		});
		// Escape belongs on the form rather than on the document handler, which skips
		// anything typed into an input on purpose — and the caret lives in an input for
		// the whole of an edit.
		$form.on("keydown", (event) => {
			if (event.key === "Escape") {
				event.stopPropagation();
				this.cancel_edit(rec);
			} else if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
				event.preventDefault();
				this.save_edit(rec);
			}
		});

		const $stem = $('<textarea class="form-control tq-etext" rows="3"></textarea>')
			.val(rec.draft.question_text)
			.on("input", () => {
				rec.draft.question_text = $stem.val();
				this.recheck_editor(rec);
			});
		$form.append(this.field(__("Question"), $stem));

		if ((q.question_type || "") === "Short Answer") {
			// `accept_question` takes the stem, the explanation and the options. It does
			// not take `correct_text_answers`, so saying so is better than offering a box
			// whose contents would be silently dropped.
			const $ro = $('<div class="tq-readonly"></div>');
			const answers = tq_lines(q.correct_text_answers);
			if (answers.length) {
				const $list = $('<ul class="tq-answers"></ul>').appendTo($ro);
				answers.forEach((answer) => $list.append($("<li></li>").text(answer)));
			} else {
				$ro.append($('<div class="tq-muted"></div>').text(__("Nothing is accepted.")));
			}
			$ro.append(
				$('<button type="button" class="tq-linkish"></button>')
					.text(__("Change them on the question record"))
					.on("click", () => frappe.set_route("Form", "Training Question", q.question))
			);
			$form.append(this.field(__("Accepted answers"), $ro));
		} else {
			rec.$options = $('<div class="tq-eopts"></div>');
			$form.append(this.field(this.options_label(q), rec.$options));
			this.paint_options(rec);
		}

		const $why = $('<textarea class="form-control tq-etext" rows="3"></textarea>')
			.attr("placeholder", __("Why is that the answer? The learner reads this after grading."))
			.val(rec.draft.explanation)
			.on("input", () => {
				rec.draft.explanation = $why.val();
			});
		$form.append(this.field(__("Explanation"), $why));

		rec.$problem = $('<div class="tq-problem"></div>').appendTo($form);

		const $acts = $('<div class="tq-eacts"></div>').appendTo($form);
		// One button, because correcting and accepting is one decision. review.py is
		// explicit about it: splitting them leaves a window where the question is fixed
		// and still unreviewed, and a reviewer who edits and then forgets to vouch.
		rec.$save = $('<button type="submit" class="btn btn-primary btn-sm"></button>')
			.text(__("Save and accept"))
			.appendTo($acts);
		$('<button type="button" class="btn btn-default btn-sm"></button>')
			.text(__("Cancel"))
			.on("click", () => this.cancel_edit(rec))
			.appendTo($acts);
		$acts.append(
			$('<span class="tq-ehint"></span>').text(__("Ctrl+Enter saves · Esc cancels"))
		);

		this.recheck_editor(rec);
		// Straight into the stem. An edit always starts by reading the question again.
		const el = $stem.get(0);
		if (el) {
			el.focus({ preventScroll: true });
			el.setSelectionRange(el.value.length, el.value.length);
		}
	}

	options_label(q) {
		return TQ_ONE_ANSWER_TYPES.indexOf(q.question_type || "") !== -1
			? __("Options — exactly one is correct")
			: __("Options — tick every correct one");
	}

	field(label, $control) {
		const $field = $('<div class="tq-field"></div>');
		$field.append($('<div class="tq-label"></div>').text(label));
		$field.append($control);
		return $field;
	}

	paint_options(rec) {
		const q = rec.q;
		const single = TQ_ONE_ANSWER_TYPES.indexOf(q.question_type || "") !== -1;
		const group = "tq-correct-" + q.question;
		rec.$options.empty();

		rec.draft.options.forEach((option, index) => {
			const $row = $('<div class="tq-erow"></div>').appendTo(rec.$options);

			const $mark = $('<label class="tq-ecorrect"></label>').appendTo($row);
			const $input = $("<input />")
				.attr("type", single ? "radio" : "checkbox")
				.attr("name", group)
				.prop("checked", !!option.is_correct)
				.appendTo($mark);
			$mark.append($('<span class="tq-sr"></span>').text(__("Correct")));
			$input.on("change", () => {
				if (single) {
					// A radio group enforces "one" for the reviewer; the draft has to be
					// told the same thing, or the untouched rows keep their old ticks.
					rec.draft.options.forEach((other) => {
						other.is_correct = 0;
					});
					option.is_correct = 1;
				} else {
					option.is_correct = $input.prop("checked") ? 1 : 0;
				}
				this.paint_options(rec);
				this.recheck_editor(rec);
			});

			const $fields = $('<div class="tq-efields"></div>').appendTo($row);
			const $text = $('<input type="text" class="form-control tq-eopt" />')
				.val(option.option_text)
				.on("input", () => {
					option.option_text = $text.val();
					this.recheck_editor(rec);
				})
				.appendTo($fields);
			// Round-tripped rather than dropped. `_apply_options` replaces the whole
			// child table from what it is sent, so an option's feedback that is not in
			// the payload is deleted — quietly, and only noticed by a learner who gets
			// the question wrong months later.
			const $why = $('<input type="text" class="form-control tq-ewhy" />')
				.attr("placeholder", __("Feedback for this option (optional)"))
				.val(option.explanation)
				.appendTo($fields);
			$why.on("input", () => {
				option.explanation = $why.val();
			});

			const $remove = $('<button type="button" class="tq-erm"></button>')
				.attr("title", __("Remove this option"))
				.attr("aria-label", __("Remove this option"))
				.text("×")
				.on("click", () => {
					rec.draft.options.splice(index, 1);
					this.paint_options(rec);
					this.recheck_editor(rec);
				})
				.appendTo($row);
			if (rec.draft.options.length <= TQ_MIN_OPTIONS) $remove.prop("disabled", true);
		});

		if (rec.draft.options.length < TQ_MAX_OPTIONS && (q.question_type || "") !== "True-False") {
			$('<button type="button" class="btn btn-default btn-xs tq-eadd"></button>')
				.text(__("Add an option"))
				.on("click", () => {
					// No `option_key`. The server mints a stable one in
					// `_assign_option_keys`; the keys on every row the reviewer did not add
					// are carried through untouched, which is what keeps a part-finished
					// attempt from being stranded.
					rec.draft.options.push({ option_key: "", option_text: "", is_correct: 0, explanation: "" });
					this.paint_options(rec);
					this.recheck_editor(rec);
					rec.$options.find(".tq-eopt").last().focus();
				})
				.appendTo(rec.$options);
		}
	}

	// The draft seen through the same lens as a stored question, so tq_flags is the one
	// implementation of the controller's rules on this page.
	draft_as_question(rec) {
		return {
			question_type: rec.q.question_type,
			correct_text_answers: rec.q.correct_text_answers,
			question_text: rec.draft.question_text,
			options: rec.draft.options,
		};
	}

	recheck_editor(rec) {
		const blocker = tq_blocker(this.draft_as_question(rec));
		const empty = !String(rec.draft.question_text || "").trim();
		const problem = empty ? { text: __("A question cannot be left without any text.") } : blocker;

		rec.$problem.toggleClass("is-on", !!problem).text(problem ? problem.text : "");
		// Blocked from saving rather than allowed to fail: the same rules would refuse
		// this in `doc.save()`, and a round trip is a worse way to learn them.
		if (rec.$save) rec.$save.prop("disabled", !!problem);
	}

	cancel_edit(rec) {
		rec.draft = null;
		this.paint_read(rec);
		rec.$el.get(0).focus({ preventScroll: true });
	}

	collect_edits(rec) {
		const q = rec.q;
		const payload = { question: q.question };

		// Only what actually changed. `question_text` and `explanation` are Text Editor
		// fields whose stored value may be markup, and the editor holds the plain-text
		// reduction of it — so sending back a field the reviewer never touched would
		// silently strip an author's formatting as a side effect of accepting.
		if (rec.draft.question_text !== tq_plain(q.question_text)) {
			payload.question_text = rec.draft.question_text;
		}
		if (rec.draft.explanation !== tq_plain(q.explanation)) {
			payload.explanation = rec.draft.explanation;
		}

		const before = (q.options || []).map((o) => ({
			option_key: o.option_key || "",
			option_text: tq_plain(o.option_text),
			is_correct: tq_num(o.is_correct),
			explanation: tq_plain(o.explanation),
		}));
		const after = rec.draft.options.map((o) => ({
			option_key: o.option_key || "",
			option_text: String(o.option_text || "").trim(),
			is_correct: tq_num(o.is_correct) ? 1 : 0,
			explanation: String(o.explanation || "").trim(),
		}));
		// A Short Answer question has none, and `_apply_options` throws on an empty
		// list — so the key is omitted entirely rather than sent as [].
		if (after.length && JSON.stringify(before) !== JSON.stringify(after)) payload.options = after;
		return payload;
	}

	save_edit(rec) {
		this.recheck_editor(rec);
		if (rec.$save && rec.$save.prop("disabled")) return;
		this.accept(rec.q.question, this.collect_edits(rec));
	}

	// ------------------------------------------------------------------- verdicts

	accept(name, payload) {
		const found = this.find(name);
		if (!found) return;
		const rec = found.rec;
		if (rec.busy) return;

		if (!payload) {
			const blocker = tq_blocker(rec.q);
			if (blocker) {
				// Accepting as it stands cannot succeed, so this says why instead of
				// spending a round trip finding out.
				frappe.show_alert(
					{
						message: frappe.utils.escape_html(
							blocker.editable ? __("{0} Press E to fix it.", [blocker.text]) : blocker.text
						),
						indicator: "red",
					},
					7
				);
				return;
			}
		}

		rec.busy = true;
		const index = this.detach(rec);
		this.inflight += 1;

		tq_call("erpnext_enhancements.training.review.accept_question", payload || { question: name })
			.then((res) => {
				this.session.accepted += 1;
				this.after_verdict(res);
			})
			.catch((err) => {
				// The card comes back with its editor and its draft intact if this was a
				// save-and-accept, so a refusal costs the reviewer the round trip and not
				// the corrections they had just typed.
				rec.busy = false;
				this.restore(rec, index, tq_first(err) || __("That question could not be accepted."));
			})
			.finally(() => {
				this.inflight -= 1;
				this.maybe_advance();
			});
	}

	reject(name) {
		const found = this.find(name);
		if (!found || found.rec.busy) return;
		const q = found.rec.q;

		const dialog = new frappe.ui.Dialog({
			title: __("Reject this question"),
			fields: [
				{ fieldtype: "HTML", fieldname: "about", options: this.reject_preamble(q) },
				{
					fieldtype: "Small Text",
					fieldname: "reason",
					label: __("Why is it being rejected?"),
					reqd: 1,
					description: __(
						"Up to {0} characters. The question is about to be deleted; this note goes on the course version's timeline and is the only record that it ever existed.",
						[TQ_MAX_REASON]
					),
				},
			],
			primary_action_label: __("Reject and remove"),
			primary_action: (values) => {
				// `reqd` is enough for the empty case: set_primary_action does not call
				// this at all when get_values() comes back empty-handed. Length is the one
				// the dialog cannot know about, and it is checked here rather than left to
				// the server because the server's refusal arrives with the words gone.
				const reason = String((values && values.reason) || "").trim();
				if (!reason) return;
				if (reason.length > TQ_MAX_REASON) {
					// Kept open, with the words still in the box.
					frappe.show_alert(
						{
							message: __("That is {0} characters. Keep it under {1}.", [
								reason.length,
								TQ_MAX_REASON,
							]),
							indicator: "red",
						},
						6
					);
					return;
				}
				dialog.hide();
				this.send_reject(name, reason, 0);
			},
		});

		dialog.$wrapper.on("shown.bs.modal", () => {
			const field = dialog.get_field("reason");
			if (field && field.$input) field.$input.focus();
		});
		dialog.$wrapper.on("hidden.bs.modal", () => this.set_focus(this.focused, true));
		dialog.show();
	}

	reject_preamble(q) {
		const parts = [
			'<div class="tq-dialog-stem">' + frappe.utils.escape_html(tq_plain(q.question_text)) + "</div>",
		];
		if (this.would_empty_a_quiz()) {
			parts.push(
				'<div class="tq-dialog-warn">' +
					frappe.utils.escape_html(
						__("This is the only question in this lesson's pool. You will be asked to confirm that the lesson should have no quiz at all.")
					) +
					"</div>"
			);
		}
		parts.push(
			'<div class="tq-dialog-note">' +
				frappe.utils.escape_html(
					__("Rejecting takes the question out of every draft lesson that draws it, and deletes it unless another pool still wants it.")
				) +
				"</div>"
		);
		return parts.join("");
	}

	would_empty_a_quiz() {
		// The client's half of the server's check. It cannot see the other lessons that
		// might draw the same question, so this only ever warns — the refusal itself
		// stays the server's to make.
		const lesson = this.lesson;
		return !!(lesson && tq_num(lesson.has_quiz) && tq_num(lesson.pool_size) <= 1);
	}

	send_reject(name, reason, drop_quiz) {
		const found = this.find(name);
		if (!found) return;
		const rec = found.rec;
		if (rec.busy) return;

		rec.busy = true;
		const index = this.detach(rec);
		this.inflight += 1;

		tq_call("erpnext_enhancements.training.review.reject_question", { question: name, reason: reason, drop_quiz: drop_quiz ? 1 : 0 })
			.then((res) => {
				this.session.rejected += 1;
				// Lesson titles are author-written and `frappe.show_alert` interpolates its
				// message straight into the alert's HTML, so they are escaped on the way in.
				const where = frappe.utils.escape_html(((res && res.removed_from) || []).join(", "));
				// The card simply vanishing is not enough feedback for an action that
				// deletes something: say what it was taken out of.
				frappe.show_alert(
					{
						message: where
							? __("Rejected and removed from {0}.", [where])
							: __("Rejected."),
						indicator: "orange",
					},
					5
				);
				this.after_verdict(res);
			})
			.catch((err) => {
				rec.busy = false;
				const messages = (err && err.messages) || [];
				// The words first, and the structural prediction only when there were no
				// words to read. `reject_question` throws ValidationError for all six of its
				// refusals, and offering to untick a quiz in answer to "that question has
				// already been accepted by somebody" would be this page inventing a decision
				// nobody was asked for.
				const refused =
					!drop_quiz &&
					(tq_empty_quiz_refusal(messages) || (!messages.length && this.would_empty_a_quiz()));
				this.restore(rec, index, tq_first(err) || __("That question could not be rejected."));
				if (refused) this.confirm_drop_quiz(name, reason, messages);
			})
			.finally(() => {
				this.inflight -= 1;
				this.maybe_advance();
			});
	}

	confirm_drop_quiz(name, reason, messages) {
		// `drop_quiz` is the reviewer confirming a consequence, never a flag this page
		// sends by default. `TrainingLesson._validate_quiz` throws when a ticked quiz has
		// an empty pool, so the alternative to asking is a lesson nobody can save until
		// somebody works out why.
		const said =
			messages[0] ||
			__("Rejecting this would leave the lesson holding a quiz with no questions in it.");
		frappe.confirm(
			frappe.utils.escape_html(said) +
				"<br><br>" +
				frappe.utils.escape_html(
					__("Confirming unticks the quiz on that lesson. It becomes a lesson with nothing to assess.")
				),
			() => this.send_reject(name, reason, 1),
			() => {
				// Declined. The question is back on the card where it was.
			}
		);
	}

	// Counters move HERE and nowhere else — only once the server has actually taken the
	// verdict. Optimistic removal is about the card, not about the numbers: a card that
	// comes back from a failure would otherwise need every tile unwound by hand, and an
	// unwind that is not the exact inverse of the step is a number that drifts all
	// morning without anybody being able to say when it went wrong.
	after_verdict(res) {
		const remaining = (res && res.remaining) || {};
		// `reviewed` is the server's; `total` is not, and the difference is real.
		// `_remaining_summary` counts every AI question with no reviewer, while
		// `get_review_queue.total` counts only those sitting in a draft course's lesson —
		// which is what this page can actually serve, and what the course list below adds
		// up to. So the tile is stepped down by hand and re-synced from `queue` on the
		// next lesson load, which is never more than a lesson's worth of verdicts away.
		if (remaining.reviewed !== undefined) this.queue.reviewed = tq_num(remaining.reviewed);
		this.queue.total = Math.max(0, tq_num(this.queue.total) - 1);
		const course = this.course_row(this.lesson && this.lesson.course);
		if (course) course.pending = Math.max(0, tq_num(course.pending) - 1);
		this.paint_bar();
		this.paint_courses();
		if (course && !tq_num(course.pending)) this.check_course_cleared(course.course);
	}

	// A course emptying is the moment this whole exercise is for: it is the thing eleven
	// of them have been held in Draft by, and a reviewer working through "everything"
	// would otherwise cross that line without ever being told.
	//
	// IT IS ALSO THE ONE NUMBER THIS PAGE MAY NOT GUESS. Everything above steps a course
	// down by one per verdict, which is arithmetic rather than a fact — a rejection can
	// empty two courses at once, and somebody else may be working the same queue from the
	// next desk. Fine for a figure on screen; not fine for a sentence telling somebody a
	// course is ready to publish. So the queue is re-read and the announcement is made
	// only if the server agrees the course has gone from it.
	check_course_cleared(name) {
		if (!name || this.cleared[name]) return;
		// Claimed before the call so two verdicts landing together do not ask twice;
		// released again below if the server disagrees.
		this.cleared[name] = true;
		tq_call("erpnext_enhancements.training.review.get_review_queue", {})
			.then((queue) => {
				if (!queue) return;
				this.adopt_queue(queue);
				this.paint_bar();
				this.paint_courses();
				if (this.course_row(name)) {
					// Still work in it. No announcement — and the counts on screen are the
					// server's now rather than this page's, which is the useful half anyway.
					this.cleared[name] = false;
					return;
				}
				frappe.show_alert(
					{
						message: __("{0} has nothing left in the queue. It can be submitted for review now.", [
							frappe.utils.escape_html(this.seen_courses[name] || name),
						]),
						indicator: "green",
					},
					8
				);
			})
			.catch(() => {
				// Nothing was claimed and nothing is said. The verdict itself has already
				// landed; this call only decided whether there was something to announce.
				this.cleared[name] = false;
			});
	}

	maybe_advance() {
		// The wait is the whole point of this method. Optimistic removal empties the
		// pane before the last accept has committed, and `_next_lesson` picks the first
		// lesson with a pending question — which would still be this one. The reviewer
		// would be handed back the lesson they just finished, with its last question on
		// it, and would accept it twice.
		if (this.inflight > 0) return;
		if (this.loading) return;
		// A Back or Forward that came while verdicts were in flight was held (follow). The
		// route is the truth: catch up with it now, unless the reviewer has already said to
		// stay. A refused save-and-accept is back in `cards` by now (restore runs first), so
		// follow asks before it paints over the corrections.
		const wanted = this.route_view();
		const key = tq_view_key(wanted);
		if (wanted && key !== tq_view_key(this.view) && key !== this.declined) {
			this.follow(wanted);
			return;
		}
		if (this.cards.length) return;
		// The route names another view, which the reviewer declined in order to keep an edit
		// here, and this view has now emptied: there is nothing left to keep, so the route is
		// followed after all. Neither advance below would do: each loads or leaves the view on
		// screen, and leave_lesson takes the route's entry to be that lesson's, so it would step
		// back past the entry the route names.
		if (wanted && key !== tq_view_key(this.view)) {
			this.follow(wanted);
			return;
		}
		// A lesson opened by name has emptied: back to the queue, without leaving a history
		// entry for Back to reopen it by. Only while the page is on screen: a verdict landing
		// after the reviewer has left must not route them back.
		if (this.view && this.view.lesson && this.route_view()) {
			this.leave_lesson();
			return;
		}
		this.load({ course: this.course_filter });
	}

	// ---------------------------------------------------------------- card juggling

	find(name) {
		for (let i = 0; i < this.cards.length; i++) {
			if (this.cards[i].q.question === name) return { rec: this.cards[i], index: i };
		}
		return null;
	}

	detach(rec) {
		// Optimistic, and deliberately so. 367 questions is 367 round trips; waiting for
		// each one turns a keystroke into a pause, and a pause into a rhythm nobody can
		// keep up. A failure puts the card back exactly where it was with the server's
		// sentence on it — which is rare, and unmissable when it happens.
		const index = this.cards.indexOf(rec);
		if (index === -1) return -1;
		this.cards.splice(index, 1);
		rec.$el.detach();
		this.renumber();
		this.paint_qcount();
		this.paint_bar();
		// The card under the caret has gone, so the next one down inherits it. At the
		// bottom of the list that means the one above, which is the only place left.
		if (this.focused >= this.cards.length) this.focused = this.cards.length - 1;
		this.set_focus(this.focused, false);
		// The gap between the last verdict in a lesson and the next lesson arriving is a
		// round trip long, and an empty pane for a round trip reads as a page that has
		// stopped. Cleared by the next paint_questions, or by a restore below.
		if (!this.cards.length) {
			this.$cards.append(
				$('<div class="tq-interstitial"></div>').text(__("Lesson cleared. Opening the next one…"))
			);
		}
		return index;
	}

	restore(rec, index, message) {
		if (index < 0) return;
		if (this.$cards) this.$cards.find(".tq-interstitial").remove();
		if (!this.lesson || rec.lesson !== this.lesson.lesson) {
			// The pane has moved on since the verdict was sent. Putting the card back
			// would drop a question from one lesson into the middle of another, which is
			// a far more confusing way to lose a refusal than not seeing the card at all.
			frappe.show_alert({ message: frappe.utils.escape_html(message), indicator: "red" }, 8);
			return;
		}
		const at = Math.max(0, Math.min(index, this.cards.length));
		this.cards.splice(at, 0, rec);
		if (at === 0) this.$cards.prepend(rec.$el);
		else this.cards[at - 1].$el.after(rec.$el);

		rec.$error.addClass("is-on").text(message);
		this.renumber();
		this.paint_qcount();
		this.paint_bar();
		// Taken back, because a card reappearing several screens above where the reviewer
		// is now working is a card nobody ever sees again.
		this.set_focus(at, true);
	}

	set_focus(index, take_caret) {
		if (!this.cards.length) {
			this.focused = -1;
			return;
		}
		this.focused = Math.max(0, Math.min(index, this.cards.length - 1));
		this.cards.forEach((rec, i) => rec.$el.toggleClass("is-focused", i === this.focused));
		if (!take_caret) return;
		const el = this.cards[this.focused].$el.get(0);
		// preventScroll, then scroll deliberately: focusing an element inside a
		// scrolling pane jumps it to the middle, which loses the reviewer's place in a
		// list they are working down.
		el.focus({ preventScroll: true });
		el.scrollIntoView({ block: "nearest" });
	}

	move_focus(step) {
		if (!this.cards.length) return;
		const next = this.focused < 0 ? 0 : this.focused + step;
		this.set_focus(Math.max(0, Math.min(next, this.cards.length - 1)), true);
	}

	// ------------------------------------------------------------------- keyboard

	on_key(event) {
		// Ctrl+R is a reload and Cmd+A is select-all. A page that eats them to save a
		// keystroke is a page people learn to distrust.
		if (event.ctrlKey || event.metaKey || event.altKey) return;
		// The page div outlives navigation, so the handler could still be bound on a
		// page the reviewer has left. Belt and braces with the teardown on "hide".
		if ((frappe.get_route() || [])[0] !== "training-review") return;
		// A reviewer typing the word "area" into a reason box must not accept three
		// questions on the way past.
		const $target = $(event.target);
		if ($target.is("input, textarea, select")) return;
		if ($target.closest('[contenteditable="true"], .ql-editor').length) return;
		// A dialog on top owns the keyboard, including its own buttons.
		if ($(".modal:visible").length) return;
		if (!this.cards.length) return;

		const key = String(event.key || "").toLowerCase();
		if (key === "j") {
			event.preventDefault();
			this.move_focus(1);
			return;
		}
		if (key === "k") {
			event.preventDefault();
			this.move_focus(-1);
			return;
		}
		if (["a", "e", "r"].indexOf(key) === -1) return;

		const rec = this.cards[this.focused];
		if (!rec) return;
		// Mid-edit the caret is in a textarea and the guard above has already returned;
		// this covers the reviewer who clicked the card's own border.
		if (rec.editing) return;

		event.preventDefault();
		if (key === "a") this.accept(rec.q.question);
		else if (key === "e") this.edit(rec.q.question);
		else this.reject(rec.q.question);
	}

	// -------------------------------------------------------------------- finished

	paint_finished() {
		this.$split.hide();
		this.cards = [];
		this.focused = -1;
		this.$done.empty().show();

		const total = tq_num(this.queue.total);
		if (this.course_filter && total > 0) {
			this.paint_course_finished(total);
			return;
		}
		if (total > 0) {
			// No filter, nothing handed back, and a count that is not zero. The two figures
			// come from the same scan, so this is the gap between them: somebody else has
			// taken the last lesson since this count was made. Saying "every question has a
			// reviewer" over a queue that says otherwise is the one thing this panel must
			// never do — a reviewer would go to a publish button that still refuses and have
			// no way to find out why.
			this.paint_race(total);
			return;
		}

		this.$done.append($('<h2 class="tq-done-head"></h2>').text(__("Every AI-drafted question has a reviewer.")));
		this.$done.append(
			$('<p class="tq-done-body"></p>').text(
				__("The publish gate refuses a course that still holds an AI-drafted question nobody has accepted. None are left, so that is no longer what is holding a course in Draft — though a course can still be waiting on something else.")
			)
		);
		this.$done.append(
			$('<p class="tq-done-body"></p>').text(
				__("You accepted {0} and rejected {1} here. {2} AI-drafted questions carry a reviewer in all.", [
					this.session.accepted,
					this.session.rejected,
					tq_num(this.queue.reviewed),
				])
			)
		);

		const names = Object.keys(this.seen_courses);
		if (names.length) {
			// The courses this session actually unblocked, which is where the next piece
			// of work is. The queue itself is empty by now and cannot name them.
			this.$done.append($('<div class="tq-done-label"></div>').text(__("Courses you cleared here")));
			const $list = $('<ul class="tq-done-list"></ul>').appendTo(this.$done);
			names.forEach((name) => {
				$("<li></li>")
					.append(
						$('<button type="button" class="tq-linkish"></button>')
							.text(this.seen_courses[name])
							.on("click", () => frappe.set_route("Form", "Training Course", name))
					)
					.appendTo($list);
			});
		}

		$('<button type="button" class="btn btn-primary btn-sm"></button>')
			.text(__("Open the Draft courses"))
			.on("click", () => frappe.set_route("List", "Training Course", { status: "Draft" }))
			.appendTo($('<div class="tq-done-acts"></div>').appendTo(this.$done));
	}

	paint_course_finished(total) {
		const title = this.seen_courses[this.course_filter] || this.course_filter;
		this.$done.append($('<h2 class="tq-done-head"></h2>').text(__("Nothing left in {0}.", [title])));
		this.$done.append(
			$('<p class="tq-done-body"></p>').text(
				__("It is no longer held in Draft by an unreviewed question. {0} are still waiting in other courses.", [
					total,
				])
			)
		);
		const $acts = $('<div class="tq-done-acts"></div>').appendTo(this.$done);
		$('<button type="button" class="btn btn-primary btn-sm"></button>')
			.text(__("Review everything else"))
			.on("click", () => {
				this.$course.val("");
				this.pick_course("");
			})
			.appendTo($acts);
		$('<button type="button" class="btn btn-default btn-sm"></button>')
			.text(__("Open {0}", [title]))
			.on("click", () => frappe.set_route("Form", "Training Course", this.course_filter))
			.appendTo($acts);
		this.paint_where_the_rest_is();
	}

	paint_race(total) {
		this.$done.append(
			$('<h2 class="tq-done-head"></h2>').text(__("Nothing was handed back, but the queue is not empty."))
		);
		this.$done.append(
			$('<p class="tq-done-body"></p>').text(
				__("{0} questions are counted as waiting and no lesson came back to review. The usual reason is that somebody else is working the same queue and took the last one between the count and the request.", [
					total,
				])
			)
		);
		$('<button type="button" class="btn btn-primary btn-sm"></button>')
			.text(__("Look again"))
			.on("click", () => this.load({ course: this.course_filter }))
			.appendTo($('<div class="tq-done-acts"></div>').appendTo(this.$done));
		this.paint_where_the_rest_is();
	}

	// Where the work that is left actually is. The course filter above can get somebody
	// there in one move, but only once they know which course to pick.
	paint_where_the_rest_is() {
		const rows = (this.queue.courses || []).filter((row) => tq_num(row.pending) > 0);
		if (!rows.length) return;

		this.$done.append($('<div class="tq-done-label"></div>').text(__("Where the rest of it is")));
		const $wrap = $('<div class="tq-table-wrap"></div>').appendTo(this.$done);
		const $table = $('<table class="tq-table"></table>').appendTo($wrap);
		$table.append(
			$("<thead></thead>").append(
				$("<tr></tr>").append(
					[__("Course"), __("Status"), __("Lessons"), __("Waiting"), ""].map((head) =>
						$("<th></th>").text(head)
					)
				)
			)
		);
		const $tbody = $("<tbody></tbody>").appendTo($table);
		rows.forEach((row) => {
			const $tr = $("<tr></tr>").appendTo($tbody);
			$tr.append($("<td></td>").text(row.course_title || row.course));
			$tr.append($("<td></td>").text(row.course_status || "—"));
			$tr.append($("<td></td>").text(tq_num(row.lessons)));
			$tr.append($("<td></td>").text(tq_num(row.pending)));
			$tr.append(
				$("<td></td>").append(
					$('<button type="button" class="btn btn-default btn-xs"></button>')
						.text(__("Review this one"))
						.on("click", () => {
							this.$course.val(row.course);
							this.pick_course(row.course);
						})
				)
			);
		});
	}
}
