// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// TR.makeTransport -- the learner runtime's HTTP surface, and the ONE place the
// endpoint names appear.
//
// This lived inside the {% block script %} of www/training.html until v1.428.1,
// which was fine while there was exactly one host. There are now two: the portal
// page and the Desk Page. A transport that lives in a template cannot be loaded by
// a page that has no template, and copying it would give the two hosts
// independently drifting maps of the same 25 endpoints -- the failure this module
// already has a name for. api/training.py renaming a method is still a one-line
// fix, and the line is still in one file.
//
// It takes `csrf` as a function rather than a value because a Desk session can
// outlive the token the page booted with; the portal, which renders its token into
// the page once, passes a closure over that constant and never notices.
//
// DECLARATION ORDER IS LOAD-BEARING: the METHOD map must stay declared above the
// PREFIX constant. Four test suites extract the map by slicing this file between
// those two declarations, and str.index returns the smaller offset for whichever
// appears first -- so swapping them yields an empty slice, an empty map, and a set
// of set-difference assertions that all pass over nothing. test_training_boot_wire
// carries a parse guard for exactly that, added in v1.428.0.
//
// Note this paragraph spells neither declaration out in full, deliberately. Those
// extractors slice on the raw text and do not strip comments, so a comment quoting
// the opening literal becomes the match -- the slice then starts inside this
// sentence and ends a few words later, which is how the guard first fired when this
// file was written.
//
// NO frappe.* ANYWHERE. This file is bound by the same rule as the four player
// files, and test_training_endpoint_surface enforces it across all five: a Website
// User with desk_access = 0 never loads the desk bundle, so frappe.call,
// frappe.msgprint, __() and jQuery do not exist for them -- all of which work
// perfectly while a developer tests logged in as themselves.
//
// It is also why the Desk host does NOT swap this for frappe.call, which is the
// obvious move. Three reasons, each read out of frappe v16 rather than assumed:
//   * request.js builds its ajax args from a fixed key list with no `timeout`, and
//     jQuery has no default. The 20s deadline below is not decoration -- the
//     heartbeat holds a `flushing` latch released only when its promise settles, so
//     one socket dropped without a FIN stops every later beat for the life of the
//     page and the learner's coverage meter never moves again.
//   * frappe.xcall rejects with `r && r.message`, which a frappe.throw does not
//     carry -- it sends exc_type and _server_messages on a 417. Every server refusal
//     would collapse to "Something went wrong", and the advisory-gates doctrine (the
//     server refuses for a reason the client never computed, and the player renders
//     that reason) would die silently.
//   * sendBeacon cannot use it at all.

(function () {
	"use strict";

	var TR = (window.TR = window.TR || {});

	// Options: { csrf: string | function }. Everything else -- the endpoint names,
	// the prefix, the deadline -- belongs to this file and to no host.
	TR.makeTransport = function (options) {
		var settings = options || {};

		function csrf() {
			var token = settings.csrf;
			return (typeof token === "function" ? token() : token) || "";
		}


		// The one place the learner runtime's endpoint names appear. If api/training.py
		// renames a method, this map is the whole fix — the player only knows the
		// transport's function names.
		var METHOD = {
			// The boot payload. The portal page never dials this -- www/training.py
			// imports get_learner_bootstrap and renders the result into the document, so
			// the page paints in one round trip on a phone on site. A Desk Page has no
			// server-side template render and must ask for it over HTTP.
			//
			// Deliberately NOT put on frappe.boot via extend_bootinfo: this does a dozen
			// get_all reads, and bootinfo is paid for on every desk page load by every
			// user, most of whom are not opening training.
			bootstrap: "get_learner_bootstrap",
			getLesson: "get_lesson",
			heartbeat: "heartbeat",
			openCheckpoint: "open_checkpoint",
			answerCheckpoint: "answer_checkpoint",
			startQuiz: "get_quiz",
			submitQuiz: "submit_quiz",
			completeLesson: "complete_lesson",
			mediaUrl: "get_media_url",
			// video.js flushes the last beat through navigator.sendBeacon on pagehide,
			// which cannot set headers -- so that path posts the CSRF token in the body
			// instead. Same endpoint; different delivery.
			heartbeatBeacon: "heartbeat",
			startAttempt: "start_attempt",
			finishAttempt: "finish_attempt",
			getCourse: "get_course",
			getTranscript: "get_my_transcript",
			// Ask-the-author. training/qa.py has held this whole feature since v1.215.0 and had
			// NO caller anywhere until v1.303.0 -- the backend was complete and the learner had
			// no way to reach it. Both dial api.training re-exports rather than training.qa
			// directly: PREFIX is single, and this map is the one place endpoint names appear.
			askQuestion: "ask_lesson_question",
			lessonQuestions: "lesson_questions",
			// Help. Ask-the-author answers "why does this work like that"; this answers
			// "what does that word mean", which has the same answer every time and should
			// not cost a round trip through a human. Dialled with the question on screen
			// when there is one -- the server reads that question itself to decide what to
			// withhold, so the panel stays open during a quiz without becoming an open book.
			lessonHelp: "lesson_help",
			// Work submissions (WI-071 Phase F). The learner uploads the file through
			// Frappe's own upload_file (a different prefix -- see transport.uploadFile
			// below) and then this hands its URL in. Grading is not here: it is a Desk
			// action for a Training Manager, not part of the learner runtime.
			submitWork: "submit_lesson_work",
			// The board. gamification.py has computed points, badges and streaks since
			// v1.215.0 and its one endpoint had no caller, so every number existed and
			// none was ever shown to the person who earned it.
			leaderboard: "leaderboard",
			// Sign-off from the field (WI-072). Tiered authority is only worth having if
			// the person holding the tier can act on it, and here that is a technician
			// standing beside a basin -- there was no non-desk sign-off surface at all
			// before this, so a Senior Technician had the power and nowhere to use it.
			signoffQueue: "my_signoff_queue",
			recordSignoff: "record_field_signoff",
			// The profile and the directory (WI-072). `profile` with no argument is your
			// own working record; with a user it is a colleague's directory entry, which
			// the server builds as a DIFFERENT, smaller payload rather than a filtered
			// one -- so a field added later is invisible to colleagues until somebody
			// deliberately lists it.
			profile: "get_profile",
			directory: "get_directory",
			// The team feed (WI-072). Reactions hang on Training Achievement, a
			// deliberately public row carrying a snapshot title and nothing else --
			// never on Training Completion, which holds scores, attempt counts and
			// revocation reasons, and which reacting to would have meant publishing all
			// of that to the whole company.
			feed: "get_feed",
			sendKudos: "send_kudos",
			feedPrefs: "get_feed_preferences",
			setFeedPrefs: "set_feed_preferences"
		};
		var PREFIX = "/api/method/erpnext_enhancements.api.training.";

		// Every call is a POST: these all mutate progress or mint a signed URL, and a
		// GET would put lesson/attempt keys in the query string and in the access log.
		// fetch has NO default timeout, and a socket dropped by an intermediary
		// without a FIN leaves its promise pending forever. That is not academic: the
		// player's heartbeat holds a `flushing` latch that is released only when this
		// promise settles, so one hung request stops every later beat for the life of
		// the page — the learner keeps watching and their coverage meter never moves
		// again. Twenty seconds is far longer than any of these calls should take and
		// far shorter than a lesson.
		var CALL_TIMEOUT_MS = 20000;

		function abortSignal() {
			// AbortSignal.timeout is 2022-era; the fallback covers older iOS Safari,
			// which is exactly the population most likely to lose a socket.
			if (typeof AbortSignal !== "undefined" && AbortSignal.timeout) {
				return AbortSignal.timeout(CALL_TIMEOUT_MS);
			}
			if (typeof AbortController === "undefined") return undefined;
			var controller = new AbortController();
			setTimeout(function () { controller.abort(); }, CALL_TIMEOUT_MS);
			return controller.signal;
		}

		function call(method, payload) {
			return fetch(PREFIX + method, {
				method: "POST",
				credentials: "same-origin",
				signal: abortSignal(),
				headers: {
					"Content-Type": "application/json",
					"Accept": "application/json",
					"X-Frappe-CSRF-Token": csrf()
				},
				body: JSON.stringify(payload || {})
			}).then(function (response) {
				return response.text().then(function (body) {
					var data = null;
					try { data = body ? JSON.parse(body) : null; } catch (e) { data = null; }
					if (!response.ok) {
						// Frappe puts the frappe.throw message in _server_messages as a
						// JSON-encoded list of JSON-encoded dicts. Unwrap far enough to
						// show the learner something truthful; fall back to the status.
						var message = "";
						try {
							var messages = JSON.parse((data && data._server_messages) || "[]");
							message = JSON.parse(messages[0] || "{}").message || "";
						} catch (e2) { message = ""; }
						var error = new Error(message || (data && data.exc_type) || ("HTTP " + response.status));
						error.status = response.status;
						throw error;
					}
					// Whitelisted methods return their value under `message`; pass it
					// through untouched so the transport adds no shape of its own.
					return data ? data.message : null;
				});
			});
		}

		var transport = {};
		Object.keys(METHOD).forEach(function (name) {
			transport[name] = function (payload) { return call(METHOD[name], payload); };
		});

		// A learner's work submission is the one thing the player sends that is not
		// JSON: a file. It goes through Frappe's own upload_file -- a DIFFERENT prefix
		// from every other call, and a multipart body, so it cannot ride the generic
		// wrapper above. The file is uploaded PRIVATE; submit_lesson_work then
		// re-parents it onto the submission so access follows the record. The browser
		// sets the multipart boundary itself, so we must NOT set Content-Type here.
		// Resolves to the new file's URL.
		transport.uploadFile = function (file) {
			if (!file) return Promise.reject(new Error("No file selected."));
			var form = new FormData();
			form.append("file", file, file.name || "submission");
			form.append("is_private", "1");
			form.append("folder", "Home");
			return fetch("/api/method/upload_file", {
				method: "POST",
				credentials: "same-origin",
				signal: abortSignal(),
				headers: {
					"Accept": "application/json",
					"X-Frappe-CSRF-Token": csrf()
				},
				body: form
			}).then(function (response) {
				return response.text().then(function (body) {
					var data = null;
					try { data = body ? JSON.parse(body) : null; } catch (e) { data = null; }
					if (!response.ok) {
						var message = "";
						try {
							var messages = JSON.parse((data && data._server_messages) || "[]");
							message = JSON.parse(messages[0] || "{}").message || "";
						} catch (e2) { message = ""; }
						var error = new Error(message || ("HTTP " + response.status));
						error.status = response.status;
						throw error;
					}
					return data && data.message ? data.message.file_url : null;
				});
			});
		};

		// The last beat on pagehide, and the one place fetch will not do: the page is
		// going away, and an in-flight fetch goes with it. sendBeacon is the only
		// transport the browser promises to finish.
		//
		// It must be SYNCHRONOUS and return a boolean, because video.js drops a beat
		// from its retry queue on a truthy return. The generic wrapper above returns a
		// Promise, which is always truthy — so every queued beat was dropped as
		// "delivered" whether or not it ever left the machine.
		//
		// sendBeacon cannot set headers, so the CSRF token travels in the body. The
		// beat itself goes under `payload`, matching `heartbeat(attempt, payload)`.
		transport.heartbeatBeacon = function (beat) {
			if (!navigator.sendBeacon || !beat) return false;
			try {
				var body = new Blob(
					[JSON.stringify({
						attempt: beat.attempt,
						payload: beat,
						csrf_token: csrf()
					})],
					{ type: "application/json" }
				);
				return navigator.sendBeacon(PREFIX + METHOD.heartbeatBeacon, body);
			} catch (e) {
				return false;
			}
		};

		return transport;
	};
})();
