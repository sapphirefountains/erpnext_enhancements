#!/usr/bin/env node
/**
 * Browser Back and Forward on three training Desk pages — executed, not grepped.
 *
 * The rule (the product owner's): Back returns to the previous step, screen or tab of
 * the page and Forward restores it; Back from the page's first screen leaves it
 * normally. A pending autosave is flushed before a step change Back or Forward causes,
 * and a stale reply never paints the wrong view.
 *
 * Each page is loaded whole, from its real file, into a `vm` context beside a fake of
 * the v16 Desk router that behaves the way the real one does where it matters here:
 *
 *   * `set_route` pushes the PATH only. An object argument becomes `route_options`, and
 *     push_state compares pathname + search and declines a no-op — so a route that
 *     carries its state in `route_options` pushes an entry identical to the last one
 *     (frappe/public/js/frappe/router.js, origin/version-16, 350-387 and 495-503).
 *   * `frappe.route_flags.replace_route` makes that a replaceState; frappe only clears
 *     the flag ~100ms later, and the fake does too.
 *   * `route()` is async; it hides the open dialog, then fires the page's on_page_show
 *     on every route change INTO it (views/container.js triggers "show" outside its
 *     is-this-a-different-page check), and "hide" on the page being left.
 *   * `back()` / `forward()` move the history index and re-run `route()`, which is what
 *     the popstate listener does.
 *
 * The three pages:
 *
 *   learn     /desk/learn hosts TR.Player. The player half is the REAL routing code --
 *             routeState/route, openCourse, load, adoptAttempt, openLesson, go, loading,
 *             start -- cut out of public/js/training/player.js by name, with the render
 *             functions stubbed and a fake transport behind `call`. Only the pixels are
 *             fake, so the key the player writes and the key the route reads are both the
 *             shipped ones. renderCourse is cut separately and run for real, for the
 *             outline's Resume button.
 *   canvas    /desk/training-canvas, the authoring canvas: the real class with its
 *             DOM-drawing methods stubbed and a fake `frappe.call` server.
 *   glossary  /desk/training-glossary-review: the real page over a small fake jQuery
 *             that keeps a real element tree, so typed text and cards can be checked.
 *
 * Run: node scripts/test_training_desk_history.mjs [learn|canvas|glossary]
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.join(HERE, "..", "erpnext_enhancements");
const LEARN_JS = path.join(APP, "training", "page", "learn", "learn.js");
const PLAYER_JS = path.join(APP, "public", "js", "training", "player.js");
const CANVAS_JS = path.join(APP, "training", "page", "training_canvas", "training_canvas.js");
const GLOSSARY_JS = path.join(
	APP,
	"training",
	"page",
	"training_glossary_review",
	"training_glossary_review.js"
);

const ONLY = process.argv[2] || "";
let failures = 0;
let checks = 0;
const queue = [];

function test(section, name, fn) {
	if (ONLY && ONLY !== section) return;
	queue.push({ section, name, fn });
}

async function runAll() {
	let last = null;
	for (const { section, name, fn } of queue) {
		if (section !== last) {
			console.log(`\n${section}`);
			last = section;
		}
		checks += 1;
		try {
			await fn();
			console.log("  ok   " + name);
		} catch (err) {
			failures += 1;
			console.log("  FAIL " + name);
			console.log("       " + (err && err.stack ? err.stack.split("\n").slice(0, 4).join("\n       ") : String(err)));
		}
	}
	console.log(`\n${checks - failures}/${checks} passed`);
	if (failures) process.exit(1);
	// Glossary verdicts remove their card on a 150ms timer; nothing else is pending.
	process.exit(0);
}

// Drains every promise chain and microtask the pages start. The fake router's route()
// is async like the real one, and the pages' own chains are several `then`s deep.
async function settle() {
	for (let i = 0; i < 30; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

function deferred() {
	let resolve;
	let reject;
	const promise = new Promise((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

function fmt(text, args) {
	return String(text).replace(/\{(\d+)\}/g, (m, i) => ((args || [])[Number(i)] ?? ""));
}

// ------------------------------------------------------------------ the fake Desk router

function makeDesk(pageName, opts) {
	const options = opts || {};
	const log = [];
	const entries = [{ path: options.path || "/desk/" + pageName, search: options.search || "" }];
	let index = 0;
	const location = { pathname: entries[0].path, search: entries[0].search };
	const wrapper = { page_name: pageName, __handlers: {} };
	let loaded = false;
	let showing = false;

	const frappe = {
		pages: { [pageName]: {} },
		route_options: null,
		route_flags: {},
		boot: { versions: { erpnext_enhancements: "test" } },
		csrf_token: "x",
		utils: {
			get_url_arg(name) {
				const params = new URLSearchParams(location.search);
				return params.get(name) || "";
			},
			escape_html: (s) => String(s),
		},
		ui: {},
		msgprint(message) {
			log.push(["msgprint", message]);
		},
		show_alert() {},
		confirm(message, yes) {
			yes();
		},
		get_route: () => desk.current_route,
		set_route(...args) {
			return desk.set_route(args);
		},
	};

	const desk = {
		frappe,
		location,
		wrapper,
		log,
		current_route: null,
		cur_dialog: null,
		get entries() {
			return entries.map((e) => e.path + e.search);
		},
		get index() {
			return index;
		},
		get url() {
			return location.pathname + location.search;
		},
		set_route(args) {
			let route = args.length === 1 && Array.isArray(args[0]) ? args[0].slice() : args.slice();
			if (route.length === 1 && typeof route[0] === "string" && route[0].includes("/")) {
				route = route[0].split("/");
			}
			const parts = [];
			for (const arg of route) {
				if (arg && typeof arg === "object") frappe.route_options = arg;
				else parts.push(encodeURIComponent(String(arg)));
			}
			const target = "/desk/" + parts.join("/");
			const query = Object.entries(frappe.route_options || {})
				.map(([key, value]) => `${key}=` + encodeURIComponent(JSON.stringify(value)))
				.join("&");
			const search = query ? "?" + query : "";
			if (location.pathname !== target || location.search !== search) {
				const replace = !!frappe.route_flags.replace_route;
				// history[method](null, null, path): the PATH only, never the query string.
				if (replace) {
					entries[index] = { path: target, search: "" };
				} else {
					entries.splice(index + 1);
					entries.push({ path: target, search: "" });
					index = entries.length - 1;
				}
				location.pathname = target;
				location.search = "";
				log.push([replace ? "replace" : "push", target]);
				desk.route();
			}
			// frappe clears route_flags in set_route's finally, after a 100ms after_ajax wait.
			setTimeout(() => {
				frappe.route_flags = {};
			}, 100).unref();
			return Promise.resolve();
		},
		async route() {
			await null;
			const segments = location.pathname
				.replace(/^\/desk\/?/, "")
				.split("/")
				.filter((s) => s !== "")
				.map((s) => decodeURIComponent(s));
			desk.current_route = segments;
			if (!frappe.route_options) frappe.route_options = {};
			for (const [key, value] of new URLSearchParams(location.search)) frappe.route_options[key] = value;
			// set_history -> frappe.ui.hide_open_dialog()
			if (desk.window.cur_dialog) {
				desk.window.cur_dialog.display = false;
				log.push(["dialog closed"]);
				desk.window.cur_dialog = null;
			}
			if (segments[0] === pageName) {
				if (!loaded) {
					loaded = true;
					frappe.pages[pageName].on_page_load(wrapper);
				}
				showing = true;
				frappe.pages[pageName].on_page_show(wrapper);
			} else if (showing) {
				showing = false;
				(wrapper.__handlers.hide || []).forEach((fn) => fn());
			}
		},
		back() {
			assert.ok(index > 0, "nothing to go back to");
			index -= 1;
			location.pathname = entries[index].path;
			location.search = entries[index].search;
			return desk.route();
		},
		forward() {
			assert.ok(index < entries.length - 1, "nothing to go forward to");
			index += 1;
			location.pathname = entries[index].path;
			location.search = entries[index].search;
			return desk.route();
		},
		window: null,
	};
	return desk;
}

// A jQuery stand-in for pages whose DOM this harness does not inspect: every method
// returns another stand-in, so any chain the page writes runs. `.on()` handlers are
// recorded against the selector or HTML they were bound through, so a test can press a
// button the page drew. `$(obj).on("hide")` on a page wrapper is recorded on the wrapper,
// which is how the fake router delivers frappe's "hide" event.
function makeChainQuery(registry, element) {
	function chain(label, target) {
		const fn = function () {};
		const proxy = new Proxy(fn, {
			get(_, prop) {
				if (prop === Symbol.toPrimitive) return () => "";
				if (prop === "then") return undefined;
				if (prop === "length") return 1;
				if (prop === "on") {
					return (event, handler) => {
						if (target && target.__handlers && typeof handler === "function") {
							(target.__handlers[event] = target.__handlers[event] || []).push(handler);
						} else if (typeof handler === "function") {
							registry.push({ label, event, handler });
						}
						return proxy;
					};
				}
				if (prop === "find") return (selector) => chain(selector);
				if (prop === "get") return () => element;
				return () => proxy;
			},
			apply() {
				return proxy;
			},
		});
		return proxy;
	}
	return function $(arg) {
		if (typeof arg === "string") return chain(arg);
		return chain("", arg);
	};
}

function fakeElement(tag) {
	const node = {
		tagName: String(tag || "div").toUpperCase(),
		children: [],
		attributes: {},
		style: {},
		className: "",
		textContent: "",
		offsetWidth: 0,
		parentNode: null,
		classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
		get firstChild() {
			return node.children[0] || null;
		},
		appendChild(child) {
			node.children.push(child);
			child.parentNode = node;
			return child;
		},
		insertBefore(child) {
			node.children.unshift(child);
			child.parentNode = node;
			return child;
		},
		removeChild(child) {
			node.children = node.children.filter((c) => c !== child);
			child.parentNode = null;
			return child;
		},
		setAttribute(key, value) {
			node.attributes[key] = String(value);
		},
		getAttribute: (key) => node.attributes[key],
		removeAttribute(key) {
			delete node.attributes[key];
		},
		addEventListener() {},
		removeEventListener() {},
		focus() {},
	};
	return node;
}

function translate(text, args) {
	return fmt(text, args);
}

// ================================================================================ learn

const PLAYER_SRC = fs.readFileSync(PLAYER_JS, "utf8");

// The body of `function <name>(` ... its matching brace, by scanning — strings and
// comments skipped. None of the functions cut here uses a template literal or a regex
// literal, which is asserted, so the scanner needs no more than this.
function cutFunction(src, name) {
	const at = src.indexOf("function " + name + "(");
	assert.ok(at !== -1, `player.js has no function ${name}() -- the harness anchors moved`);
	return cutBlock(src, at);
}

function cutBlock(src, at) {
	let i = src.indexOf("{", at);
	let depth = 0;
	let quote = null;
	for (; i < src.length; i += 1) {
		const ch = src[i];
		const next = src[i + 1];
		if (quote) {
			if (ch === "\\") i += 1;
			else if (ch === quote) quote = null;
			continue;
		}
		if (ch === "/" && next === "/") {
			i = src.indexOf("\n", i);
			continue;
		}
		if (ch === "/" && next === "*") {
			i = src.indexOf("*/", i) + 1;
			continue;
		}
		if (ch === '"' || ch === "'") {
			quote = ch;
			continue;
		}
		assert.notEqual(ch, "`", "a template literal inside a cut function -- extend the scanner");
		if (ch === "{") depth += 1;
		if (ch === "}") {
			depth -= 1;
			if (depth === 0) return src.slice(at, i + 1);
		}
	}
	throw new Error("unbalanced braces from " + at);
}

function cutVar(src, name) {
	const at = src.indexOf("var " + name + " = ");
	assert.ok(at !== -1, `player.js has no var ${name} -- the harness anchors moved`);
	const open = src.indexOf("{", at);
	const end = src.indexOf(";", at);
	if (open !== -1 && open < end) return cutBlock(src, at) + ";";
	return src.slice(at, end + 1);
}

const ROUTING = (() => {
	const from = PLAYER_SRC.indexOf("var COURSE_SCOPED_VIEWS");
	const to = PLAYER_SRC.indexOf("function queryParam(");
	assert.ok(from !== -1 && to > from, "route() anchors moved");
	return PLAYER_SRC.slice(from, to);
})();

const RENDERED_VIEWS = [
	"Unavailable",
	"Catalog",
	"Course",
	"Lesson",
	"Quiz",
	"Results",
	"Signoff",
	"Complete",
	"Record",
	"Queue",
	"Directory",
	"Feed",
	"Person",
	"BoardView",
];

const PLAYER_FACTORY = new Function(
	"hooks",
	`
	var document = hooks.document;
	var window = hooks.window;
	var TR = {};
	${cutFunction(PLAYER_SRC, "el")}
	${cutFunction(PLAYER_SRC, "button")}
	${cutFunction(PLAYER_SRC, "clear")}
	return function Player(rootEl, boot, transport) {
		var b = boot || {};
		var t = function (s) { return s; };
		${cutVar(PLAYER_SRC, "state")}
		var teardowns = [];
		var flushers = [];
		var head = document.createElement("header");
		var main = document.createElement("main");
		var foot = document.createElement("footer");
		${ROUTING}
		function queryParam() { return ""; }
		${cutVar(PLAYER_SRC, "viewSeq")}
		${cutVar(PLAYER_SRC, "STALE")}
		${cutFunction(PLAYER_SRC, "runTeardowns")}
		${cutFunction(PLAYER_SRC, "flush")}
		${cutFunction(PLAYER_SRC, "setBusy")}
		${cutFunction(PLAYER_SRC, "fail")}
		function call(name, args) { return hooks.call(name, args || {}); }
		${cutFunction(PLAYER_SRC, "go")}
		${cutFunction(PLAYER_SRC, "loading")}
		${cutFunction(PLAYER_SRC, "skel")}
		${RENDERED_VIEWS.map((v) => `function render${v}() { hooks.rendered("${v.toLowerCase()}", state); }`).join("\n")}
		${cutFunction(PLAYER_SRC, "openPerson")}
		${cutFunction(PLAYER_SRC, "forgetCourse")}
		${cutFunction(PLAYER_SRC, "openCourse")}
		${cutFunction(PLAYER_SRC, "load")}
		${cutFunction(PLAYER_SRC, "adoptAttempt")}
		${cutFunction(PLAYER_SRC, "openLesson")}
		${cutFunction(PLAYER_SRC, "start")}
		start();
		return {
			go: go,
			openCourse: openCourse,
			openLesson: openLesson,
			openPerson: openPerson,
			flush: flush,
			destroy: function () { runTeardowns(); hooks.destroyed(); },
			state: state,
		};
	};
`
);

// Lesson keys are ten-character hashes on the real site (training_lesson.py), so
// these are too: a fourth route segment "quiz" is told apart from one by length.
const COURSE_A = "TRN-CRS-00001";
const COURSE_B = "TRN-CRS-00002";
const LESSONS = {
	[COURSE_A]: ["a1a1a1a1a1", "a2a2a2a2a2", "a3a3a3a3a3"],
	[COURSE_B]: ["b1b1b1b1b1", "b2b2b2b2b2"],
};

function makeLearnServer() {
	const server = {
		calls: [],
		minted: [],
		errors: [],
		// An in-progress course: the attempt says where the learner is up to.
		attempts: { [COURSE_A]: { next: LESSONS[COURSE_A][1] } },
		hold: null,
		held: [],
		call(name, args) {
			server.calls.push([name, args]);
			const answer = () => server.answer(name, args);
			if (server.hold && server.hold(name, args)) {
				const d = deferred();
				server.held.push({ name, args, release: () => answer().then(d.resolve, d.reject) });
				return d.promise;
			}
			return answer();
		},
		answer(name, args) {
			if (name === "getCourse") {
				const lessons = LESSONS[args.course] || [];
				const open = server.attempts[args.course];
				return Promise.resolve({
					course: { course: args.course, title: args.course },
					toc: lessons.map((key) => ({ lesson_key: key })),
					attempt: open ? { attempt: "ATT-" + args.course, next_lesson_key: open.next, lessons: {} } : null,
				});
			}
			if (name === "startAttempt") {
				server.minted.push(args.course);
				server.attempts[args.course] = { next: LESSONS[args.course][0] };
				return Promise.resolve({ attempt: "ATT-" + args.course, next_lesson_key: LESSONS[args.course][0] });
			}
			if (name === "getLesson") {
				const course = String(args.attempt).replace(/^ATT-/, "");
				if ((LESSONS[course] || []).indexOf(args.lesson_key) === -1) {
					const err = new Error("That lesson is not part of this course.");
					server.errors.push(err.message);
					return Promise.reject(err);
				}
				return Promise.resolve({
					attempt: args.attempt,
					lesson: { lesson_key: args.lesson_key, title: args.lesson_key },
					progress: {},
				});
			}
			return Promise.resolve({});
		},
	};
	return server;
}

async function openLearn(opts) {
	const options = opts || {};
	const desk = makeDesk("learn", { path: options.path || "/desk/learn" });
	const server = makeLearnServer();
	const rendered = [];
	const context = {
		players: [],
		destroyed: 0,
	};
	const hooks = {
		document: { createElement: fakeElement },
		window: { scrollTo() {} },
		call: (name, args) => server.call(name, args),
		rendered: (view, state) => rendered.push({ view, course: state.courseName, lesson: state.lessonKey }),
		destroyed: () => {
			context.destroyed += 1;
		},
	};
	const Player = PLAYER_FACTORY(hooks);
	const root = fakeElement("div");
	const sandbox = {
		frappe: desk.frappe,
		__: translate,
		$: makeChainQuery([], root),
		console,
		setTimeout,
		clearTimeout,
		Promise,
		Error,
		TR: {
			loadAssets: () => Promise.resolve(),
			makeTransport: () => ({ bootstrap: () => Promise.resolve({}) }),
			Player: function (rootEl, boot, transport) {
				const player = Player(rootEl, boot, transport);
				context.players.push(player);
				return player;
			},
		},
	};
	sandbox.window = sandbox;
	desk.window = sandbox;
	desk.frappe.ui.make_app_page = () => makeChainQuery([], root)("page");
	vm.createContext(sandbox);
	vm.runInContext(fs.readFileSync(LEARN_JS, "utf8"), sandbox, { filename: LEARN_JS });
	desk.route();
	await settle();
	return {
		desk,
		server,
		rendered,
		get page() {
			return desk.wrapper.learn;
		},
		get player() {
			return context.players[context.players.length - 1];
		},
		get view() {
			const state = context.players[context.players.length - 1].state;
			return state.view;
		},
		context,
	};
}

const url = (...parts) => "/desk/" + parts.join("/");

test("learn", "the outline gets its own history step, and Forward returns to the lesson", async () => {
	const t = await openLearn();
	t.player.openCourse(COURSE_A); // a catalogue card
	await settle();
	assert.equal(t.desk.url, url("learn", COURSE_A));
	t.player.openLesson(LESSONS[COURSE_A][1]); // the outline's Resume row
	await settle();
	assert.equal(t.desk.url, url("learn", COURSE_A, LESSONS[COURSE_A][1]), "Resume pushed nothing");
	await t.desk.back();
	await settle();
	assert.equal(t.view, "course");
	await t.desk.forward();
	await settle();
	assert.equal(t.view, "lesson", "Forward to the lesson left the outline on screen");
	assert.equal(t.player.state.lessonKey, LESSONS[COURSE_A][1]);
});

test("learn", "an in-progress course loads once, not twice", async () => {
	const t = await openLearn();
	t.player.openCourse(COURSE_A);
	await settle();
	const loads = t.server.calls.filter(([name]) => name === "getCourse").length;
	assert.equal(loads, 1, `getCourse ran ${loads} times for one click`);
});

test("learn", "'← Course' from a lesson pushes the outline, so Back returns to the lesson", async () => {
	const t = await openLearn({ path: url("learn", COURSE_A, LESSONS[COURSE_A][0]) });
	assert.equal(t.view, "lesson");
	const before = t.desk.entries.length;
	t.player.go("course");
	await settle();
	assert.equal(t.desk.url, url("learn", COURSE_A));
	assert.equal(t.desk.entries.length, before + 1, "the outline shared the lesson's entry");
	await t.desk.back();
	await settle();
	assert.equal(t.view, "lesson");
	assert.equal(t.desk.index, 0, "Back went somewhere other than the lesson it came from");
});

test("learn", "the quiz is a step: Back returns to its lesson and Forward to the quiz", async () => {
	const lesson = LESSONS[COURSE_A][1];
	const t = await openLearn({ path: url("learn", COURSE_A, lesson) });
	t.player.go("quiz"); // startQuiz
	await settle();
	assert.equal(t.desk.url, url("learn", COURSE_A, lesson, "quiz"));
	const withQuiz = t.desk.entries.length;
	t.player.go("results"); // a submit
	await settle();
	assert.equal(t.desk.entries.length, withQuiz, "results pushed an entry nobody can return to");
	await t.desk.back();
	await settle();
	assert.equal(t.view, "lesson", "Back from the quiz skipped its lesson");
	assert.equal(t.player.state.lessonKey, lesson);
	await t.desk.forward();
	await settle();
	assert.equal(t.view, "quiz", "Forward did not restore the quiz");
});

test("learn", "a reloaded quiz URL opens on the quiz and adds no step", async () => {
	const lesson = LESSONS[COURSE_A][2];
	const t = await openLearn({ path: url("learn", COURSE_A, lesson, "quiz") });
	assert.equal(t.view, "quiz");
	assert.deepEqual(t.desk.entries, [url("learn", COURSE_A, lesson, "quiz")]);
});

test("learn", "finishing a course pushes the course; Back returns to the last lesson", async () => {
	const last = LESSONS[COURSE_A][2];
	const t = await openLearn({ path: url("learn", COURSE_A, last) });
	t.player.go("complete"); // finishCourse
	await settle();
	assert.equal(t.desk.url, url("learn", COURSE_A));
	await t.desk.back();
	await settle();
	assert.equal(t.view, "lesson");
	assert.equal(t.player.state.lessonKey, last);
});

test("learn", "a different course starts clean: no stale lesson, no attempt minted", async () => {
	const t = await openLearn({ path: url("learn", COURSE_A, LESSONS[COURSE_A][1]) });
	// The rail, or Back/Forward between two course URLs.
	t.desk.frappe.set_route("learn", COURSE_B);
	await settle();
	assert.deepEqual(t.server.errors, [], "course B was asked for course A's lesson");
	assert.deepEqual(t.server.minted, [], "a glance at B's outline minted an attempt");
	assert.equal(t.view, "course");
	assert.equal(t.player.state.courseName, COURSE_B);
	assert.equal(t.player.state.lessonKey, null);
});

test("learn", "a late reply for a course already left paints nothing and routes nowhere", async () => {
	const t = await openLearn();
	t.server.hold = (name) => name === "getCourse";
	t.desk.frappe.set_route("learn", COURSE_A);
	await settle();
	t.desk.frappe.set_route("learn", COURSE_B);
	await settle();
	const [forA, forB] = t.server.held;
	assert.equal(forA.args.course, COURSE_A);
	forB.release();
	await settle();
	forA.release(); // arrives last
	await settle();
	assert.equal(t.player.state.courseName, COURSE_B, "A's late reply rewrote the state");
	assert.equal(t.view, "course");
	assert.equal(t.desk.url, url("learn", COURSE_B), "A's late reply routed the learner back to A");
	const last = t.rendered[t.rendered.length - 1];
	assert.equal(last.course, COURSE_B);
});

test("learn", "person to person still walks back (regression)", async () => {
	const t = await openLearn({ path: url("learn", "person", "one@example.com") });
	t.player.openPerson("two@example.com");
	await settle();
	assert.equal(t.desk.url, url("learn", "person", encodeURIComponent("two@example.com")));
	await t.desk.back();
	await settle();
	assert.equal(t.player.state.viewingUser, "one@example.com");
});

test("learn", "leaving the page tears the player down and forgets what it showed", async () => {
	const t = await openLearn({ path: url("learn", COURSE_A) });
	t.desk.frappe.set_route("sales-invoice");
	await settle();
	assert.equal(t.context.destroyed, 1);
	assert.equal(t.page.showing, null);
	await t.desk.back();
	await settle();
	assert.equal(t.view, "course", "the page did not come back on Back");
});

// The outline's footer button, from the REAL renderCourse, rowFor and firstOpenLesson.
// Everything else renderCourse draws is stubbed: only the button's label and where it
// goes are under test. `boot.resume` names the learner's most recent attempt -- ONE
// course -- and every outline used to read it.
const RENDER_COURSE = new Function(
	"hooks",
	`
	var document = hooks.document;
	${cutFunction(PLAYER_SRC, "el")}
	${cutFunction(PLAYER_SRC, "button")}
	return function (state) {
		var t = function (s) { return s; };
		var fmt = hooks.fmt;
		var head = document.createElement("header");
		var main = document.createElement("main");
		var foot = document.createElement("footer");
		function go() {}
		function signoffBox() { return document.createElement("div"); }
		function courseCounter() { return { done: 0, total: 0 }; }
		function meter() { return document.createElement("div"); }
		function outlineRow() { return document.createElement("li"); }
		function openLesson(key) { hooks.opened.push(key); }
		${cutFunction(PLAYER_SRC, "renderCourse")}
		${cutFunction(PLAYER_SRC, "rowFor")}
		${cutFunction(PLAYER_SRC, "firstOpenLesson")}
		renderCourse();
		return foot;
	};
`
);

function outlineFooter(state) {
	const opened = [];
	const document = {
		createElement(tag) {
			const node = fakeElement(tag);
			node.addEventListener = (event, fn) => {
				node["on" + event] = fn;
			};
			return node;
		},
	};
	const foot = RENDER_COURSE({ document, fmt, opened })(state);
	const button = foot.children[0];
	assert.ok(button, "the outline drew no Start/Resume button");
	return {
		label: button.textContent,
		press() {
			button.onclick();
			return opened[opened.length - 1];
		},
	};
}

function outlineOf(course) {
	return LESSONS[course].map((key) => ({ lesson_key: key, title: "Title " + key }));
}

test("learn", "the outline's Resume is this course's, never another course's", async () => {
	const resume = { course: COURSE_A, lesson_key: LESSONS[COURSE_A][1] };
	const onA = outlineFooter({ courseName: COURSE_A, course: {}, outline: outlineOf(COURSE_A), resume });
	assert.equal(onA.label, "Resume: Title " + LESSONS[COURSE_A][1]);
	assert.equal(onA.press(), LESSONS[COURSE_A][1]);
	const onB = outlineFooter({ courseName: COURSE_B, course: {}, outline: outlineOf(COURSE_B), resume });
	assert.equal(onB.label, "Start: Title " + LESSONS[COURSE_B][0], "course B offered course A's Resume");
	assert.equal(onB.press(), LESSONS[COURSE_B][0], "course B's button opened course A's lesson");
});

test("learn", "a Resume lesson the outline does not have falls back to Start", async () => {
	// The attempt is on an older version whose lesson is gone from this outline.
	const resume = { course: COURSE_A, lesson_key: "zzzzzzzzzz" };
	const onA = outlineFooter({ courseName: COURSE_A, course: {}, outline: outlineOf(COURSE_A), resume });
	assert.equal(onA.label, "Start: Title " + LESSONS[COURSE_A][0], "a blank 'Resume: ' was drawn");
});

// ================================================================================ canvas

const CANVAS_COURSES = {
	"TRN-CRS-00010": ["TRN-LSN-00101", "TRN-LSN-00102", "TRN-LSN-00103"],
	"TRN-CRS-00020": ["TRN-LSN-00201", "TRN-LSN-00202"],
};
const CA = "TRN-CRS-00010";
const CB = "TRN-CRS-00020";

function makeCanvasServer() {
	const server = {
		calls: [],
		saves: [],
		failSave: false,
		created: [],
		hold: null,
		held: [],
		call(opts) {
			const method = String(opts.method).split(".").pop();
			server.calls.push([method, opts.args]);
			const answer = () => server.answer(method, opts.args || {});
			if (server.hold && server.hold(method, opts.args || {})) {
				const d = deferred();
				server.held.push({ method, args: opts.args, release: () => answer().then(d.resolve, d.reject) });
				return d.promise;
			}
			return answer();
		},
		answer(method, args) {
			if (method === "get_builder_bootstrap") {
				const lessons = CANVAS_COURSES[args.course] || [];
				return Promise.resolve({
					message: {
						course: { name: args.course, course_title: "Course " + args.course },
						version: { name: "VER-" + args.course, docstatus: 0, modified: "m0" },
						lessons: lessons.map((name) => ({ name, lesson_title: name, blocks: [] })),
						chapters: [],
					},
				});
			}
			if (method === "save_draft_version") {
				server.saves.push(JSON.parse(args.payload));
				if (server.failSave) return Promise.reject(new Error("Server unreachable"));
				// Another author saved the same draft: the canvas's `modified` is stale.
				if (server.staleSave) {
					return Promise.reject(new Error("Document has been modified after you have opened it"));
				}
				const created = server.created;
				server.created = [];
				return Promise.resolve({ message: { modified: "m" + server.saves.length, created_lessons: created } });
			}
			if (method === "list_starters") {
				return Promise.resolve({ message: { starters: [{ key: "video", label: "Video lesson", lessons: 3 }] } });
			}
			if (method === "create_from_starter") {
				CANVAS_COURSES["TRN-CRS-00099"] = ["TRN-LSN-00991"];
				return Promise.resolve({ message: { course: "TRN-CRS-00099" } });
			}
			return Promise.resolve({ message: {} });
		},
	};
	return server;
}

const CANVAS_SRC = fs.readFileSync(CANVAS_JS, "utf8");

async function openCanvas(opts) {
	const options = opts || {};
	const desk = makeDesk("training-canvas", { path: options.path, search: options.search });
	const server = makeCanvasServer();
	const registry = [];
	const sheets = [];
	const sandbox = {
		frappe: desk.frappe,
		__: translate,
		$: makeChainQuery(registry, fakeElement("div")),
		console,
		setTimeout,
		clearTimeout,
		Promise,
		Error,
		JSON,
		document: { visibilityState: "visible" },
		TR: { loadAssets: () => Promise.resolve() },
		cur_dialog: null,
	};
	sandbox.window = sandbox;
	desk.window = sandbox;
	desk.frappe.call = (o) => server.call(o);
	desk.frappe.ui.make_app_page = () => makeChainQuery(registry, null)("page");
	vm.createContext(sandbox);
	vm.runInContext(CANVAS_SRC, sandbox, { filename: CANVAS_JS });
	const Canvas = vm.runInContext("TrainingCanvas", sandbox);
	// The drawing, stubbed: everything that paints a lesson or a status pip. What is left
	// is the routing, the loading, the flush and the flags that decide the screen.
	const proto = Canvas.prototype;
	proto.build_rt_toolbar = function () {};
	proto.render_rail = function () {};
	proto.render_readiness = function () {};
	proto.render_lesson_settings = function () {};
	proto.paint_status = function () {};
	proto.load_home_courses = function () {};
	proto.render_sheet = function () {
		sheets.push(this.lesson_name);
	};
	desk.route();
	await settle();
	const t = {
		desk,
		server,
		registry,
		sheets,
		sandbox,
		get canvas() {
			return desk.wrapper.training_canvas;
		},
		get screen() {
			const c = desk.wrapper.training_canvas;
			if (c._starters) return "starters";
			if (c._home) return "home";
			if (c.course) return `${c.course.name}/${c.lesson_name}`;
			if (c._loading) return "loading " + c._loading;
			return "?";
		},
		press(label, event) {
			const hit = registry.filter((r) => r.label.indexOf(label) !== -1 && r.event === (event || "click")).pop();
			assert.ok(hit, `nothing bound to ${label}`);
			hit.handler({ key: "Enter", preventDefault() {} });
		},
	};
	return t;
}

const cv = (...parts) => "/desk/" + ["training-canvas"].concat(parts).join("/");

test("canvas", "opening a course puts it in the route, naming the lesson on screen", async () => {
	const t = await openCanvas();
	assert.equal(t.screen, "home");
	t.canvas.open_course(CA); // a home row
	await settle();
	assert.equal(t.screen, `${CA}/${CANVAS_COURSES[CA][0]}`);
	assert.deepEqual(t.desk.entries, [cv(), cv(CA, CANVAS_COURSES[CA][0])]);
});

test("canvas", "Back and Forward walk the lessons picked on the rail", async () => {
	const [l1, l2, l3] = CANVAS_COURSES[CA];
	const t = await openCanvas({ path: cv(CA, l1) });
	t.canvas.select_lesson(l2);
	await settle();
	t.canvas.select_lesson(l3);
	await settle();
	assert.equal(t.desk.url, cv(CA, l3));
	await t.desk.back();
	await settle();
	assert.equal(t.screen, `${CA}/${l2}`);
	assert.equal(t.sheets[t.sheets.length - 1], l2);
	await t.desk.back();
	await settle();
	assert.equal(t.screen, `${CA}/${l1}`);
	await t.desk.forward();
	await settle();
	assert.equal(t.screen, `${CA}/${l2}`);
});

test("canvas", "Back to the home screen flushes the pending autosave first", async () => {
	const [l1] = CANVAS_COURSES[CA];
	const t = await openCanvas();
	t.canvas.open_course(CA);
	await settle();
	const lesson = t.canvas.current_lesson();
	lesson.summary = "typed a second ago";
	t.canvas.dirty_lesson(lesson).summary = lesson.summary;
	t.canvas.mark_dirty(); // the 1200ms debounce is still pending
	await t.desk.back();
	await settle();
	assert.equal(t.server.saves.length, 1, "the edit was not saved before leaving");
	assert.equal(t.server.saves[0].lessons[0].summary, "typed a second ago");
	assert.equal(t.screen, "home");
	assert.equal(t.canvas.has_dirty(), false);
	await t.desk.forward();
	await settle();
	assert.equal(t.screen, `${CA}/${l1}`, "Forward did not restore the course");
});

test("canvas", "a failed flush keeps the course and its edits on screen", async () => {
	const t = await openCanvas();
	t.canvas.open_course(CA);
	await settle();
	t.server.failSave = true;
	const lesson = t.canvas.current_lesson();
	t.canvas.dirty_lesson(lesson).summary = "must not be lost";
	t.canvas.mark_dirty();
	await t.desk.back();
	await settle();
	assert.equal(t.screen, `${CA}/${lesson.name}`, "the course was dropped with its edits");
	assert.equal(t.canvas.has_dirty(), true, "the unsaved batch was not put back");
	assert.ok(t.desk.log.some(([kind]) => kind === "msgprint"), "nobody was told why it stayed");
});

// Out of date is the failure that does not look like one. enter_conflict() makes the
// canvas read-only, so save() declines to send and returns a resolved promise -- and the
// flush used to pass that on as "stored", so leaving reset() the edits away in silence.
async function openConflictedCanvas() {
	const t = await openCanvas();
	t.canvas.open_course(CA);
	await settle();
	const lesson = t.canvas.current_lesson();
	lesson.summary = "typed before the conflict";
	t.canvas.dirty_lesson(lesson).summary = lesson.summary;
	t.server.staleSave = true;
	await t.canvas.save().catch(() => {}); // the autosave, refused as stale
	assert.equal(t.canvas._conflict, true, "the stale save did not put the canvas out of date");
	assert.equal(t.canvas.has_dirty(), true, "the refused batch was not put back");
	return { t, lesson };
}

function saidOutOfDate(t) {
	return t.desk.log.some(([kind, m]) => kind === "msgprint" && /changed somewhere else/.test(m.message));
}

test("canvas", "out of date: Back keeps the course and its edits, and says why", async () => {
	const { t, lesson } = await openConflictedCanvas();
	await t.desk.back();
	await settle();
	assert.equal(t.screen, `${CA}/${lesson.name}`, "Back reset the course away with its edits");
	assert.equal(t.canvas.has_dirty(), true, "the unsaved edits were dropped");
	assert.equal(t.canvas.dirty.lessons[lesson.name].summary, "typed before the conflict");
	assert.equal(t.server.saves.length, 1, "a read-only canvas sent a save");
	assert.ok(saidOutOfDate(t), "nobody was told why Back did not leave");
});

test("canvas", "out of date: the course-name link keeps the edits too", async () => {
	const { t, lesson } = await openConflictedCanvas();
	const entries = t.desk.entries.length;
	t.canvas.go_home();
	await settle();
	assert.equal(t.screen, `${CA}/${lesson.name}`);
	assert.equal(t.canvas.has_dirty(), true);
	assert.equal(t.desk.entries.length, entries, "the link pushed home without the course leaving");
	assert.ok(saidOutOfDate(t));
});

test("canvas", "out of date: a door into another course keeps these edits", async () => {
	const { t, lesson } = await openConflictedCanvas();
	t.desk.frappe.route_options = { course: CB }; // a door, e.g. "Edit visually"
	t.desk.frappe.set_route("training-canvas");
	await settle();
	assert.equal(t.canvas.course.name, CA, "course B replaced course A and its unsaved edits");
	assert.equal(t.canvas.has_dirty(), true);
	assert.equal(t.server.calls.filter(([m]) => m === "get_builder_bootstrap").length, 1);
	assert.ok(saidOutOfDate(t));
	assert.equal(lesson.summary, "typed before the conflict");
});

test("canvas", "the course-name link pushes home, so Back returns to the lesson", async () => {
	const [, l2] = CANVAS_COURSES[CA];
	const t = await openCanvas({ path: cv(CA, l2) });
	t.canvas.go_home();
	await settle();
	assert.equal(t.screen, "home");
	assert.deepEqual(t.desk.entries, [cv(CA, l2), cv()]);
	await t.desk.back();
	await settle();
	assert.equal(t.screen, `${CA}/${l2}`);
});

test("canvas", "an old door's route_options entry is rewritten to name the course", async () => {
	const t = await openCanvas({ path: "/desk/sales-invoice" });
	// training_course.js open_canvas / training_review.js, unchanged.
	t.desk.frappe.route_options = { course: CB };
	t.desk.frappe.set_route("training-canvas");
	await settle();
	assert.equal(t.screen, `${CB}/${CANVAS_COURSES[CB][0]}`);
	assert.deepEqual(t.desk.entries, ["/desk/sales-invoice", cv(CB, CANVAS_COURSES[CB][0])]);
	await t.desk.back();
	await settle();
	await t.desk.forward();
	await settle();
	assert.equal(t.screen, `${CB}/${CANVAS_COURSES[CB][0]}`, "the entry forgot which course it was");
	const loads = t.server.calls.filter(([m]) => m === "get_builder_bootstrap").length;
	assert.equal(loads, 1, "Forward to the same course reloaded it");
});

test("canvas", "a ?course= bookmark lands on the course and keeps one entry", async () => {
	const t = await openCanvas({ path: cv(), search: "?course=" + CA });
	assert.equal(t.screen, `${CA}/${CANVAS_COURSES[CA][0]}`);
	assert.deepEqual(t.desk.entries, [cv(CA, CANVAS_COURSES[CA][0])]);
});

test("canvas", "the starter gallery is a step between home and the new course", async () => {
	const t = await openCanvas();
	t.press(".tc-home-new");
	await settle();
	assert.equal(t.screen, "starters");
	assert.equal(t.desk.url, cv("new"));
	await t.desk.back();
	await settle();
	assert.equal(t.screen, "home");
	await t.desk.forward();
	await settle();
	assert.equal(t.screen, "starters");
	t.press('class="tc-starter"');
	t.press(".tc-starter-go");
	await settle();
	assert.equal(t.screen, "TRN-CRS-00099/TRN-LSN-00991");
	// The gallery's entry was replaced: Back from the new course goes home, not to the form.
	assert.deepEqual(t.desk.entries, [cv(), cv("TRN-CRS-00099", "TRN-LSN-00991")]);
});

test("canvas", "Back from the course to the gallery clears the course chrome", async () => {
	const t = await openCanvas({ path: cv("new") });
	assert.equal(t.screen, "starters");
	t.canvas.open_course(CA);
	await settle();
	await t.desk.back();
	await settle();
	assert.equal(t.screen, "starters");
	assert.equal(t.canvas.course, null);
});

test("canvas", "the page's Reload redraws the screen the route names", async () => {
	const t = await openCanvas({ path: cv("new") });
	t.canvas.reload();
	await settle();
	assert.equal(t.screen, "starters", "Reload on the gallery drew the home screen under /new");
	t.server.hold = (method) => method === "get_builder_bootstrap";
	t.canvas.open_course(CA);
	await settle();
	t.canvas.reload(); // mid-load
	t.server.held.forEach((h) => h.release());
	await settle();
	assert.equal(t.screen, `${CA}/${CANVAS_COURSES[CA][0]}`);
});

test("canvas", "a course that lands after Back has left it paints nothing", async () => {
	const t = await openCanvas();
	t.server.hold = (method) => method === "get_builder_bootstrap";
	t.canvas.open_course(CA);
	await settle();
	await t.desk.back();
	await settle();
	assert.equal(t.screen, "home");
	t.server.held[0].release();
	await settle();
	assert.equal(t.screen, "home", "the late bootstrap drew the course over the home screen");
	assert.equal(t.desk.url, cv(), "the late bootstrap routed");
});

test("canvas", "a new lesson is routed once its first save names it, never by temp id", async () => {
	const [l1] = CANVAS_COURSES[CA];
	const t = await openCanvas({ path: cv(CA, l1) });
	const fresh = { __temp: "new-1", lesson_title: "New", blocks: [], quiz: [], checkpoints: [] };
	t.canvas.lessons.push(fresh);
	t.canvas.select_lesson("new-1");
	await settle();
	assert.equal(t.desk.url, cv(CA, l1), "a temp id reached the address bar");
	t.canvas.dirty_lesson(fresh).lesson_title = "New";
	t.server.created = [{ temp_id: "new-1", name: "TRN-LSN-00109" }];
	await t.canvas.save();
	await settle();
	assert.equal(t.desk.url, cv(CA, "TRN-LSN-00109"));
	await t.desk.back();
	await settle();
	assert.equal(t.screen, `${CA}/${l1}`);
});

test("canvas", "a save landing while a dialog is open does not route (it would close it)", async () => {
	const [l1] = CANVAS_COURSES[CA];
	const t = await openCanvas({ path: cv(CA, l1) });
	const fresh = { __temp: "new-2", lesson_title: "New", blocks: [], quiz: [], checkpoints: [] };
	t.canvas.lessons.push(fresh);
	t.canvas.select_lesson("new-2");
	t.canvas.dirty_lesson(fresh).lesson_title = "New";
	t.server.created = [{ temp_id: "new-2", name: "TRN-LSN-00110" }];
	t.sandbox.cur_dialog = { display: true };
	await t.canvas.save();
	await settle();
	assert.equal(t.desk.url, cv(CA, l1));
	assert.ok(!t.desk.log.some(([kind]) => kind === "dialog closed"), "Course settings was closed under the author");
});

test("canvas", "Back to a lesson that has gone stays put and corrects the entry", async () => {
	const [l1, l2] = CANVAS_COURSES[CA];
	const t = await openCanvas({ path: cv(CA, l1) });
	t.canvas.select_lesson(l2);
	await settle();
	t.canvas.lessons = t.canvas.lessons.filter((l) => l.name !== l1);
	await t.desk.back();
	await settle();
	assert.equal(t.screen, `${CA}/${l2}`);
	assert.equal(t.desk.url, cv(CA, l2), "the address bar still names a lesson that is not there");
});

test("canvas", "a route to where it already is does not re-route (no loop)", async () => {
	const [l1] = CANVAS_COURSES[CA];
	const t = await openCanvas({ path: cv(CA, l1) });
	const before = t.desk.log.length;
	t.canvas.select_lesson(l1);
	t.canvas.sync_route({ replace: true });
	await settle();
	assert.equal(t.desk.log.length, before);
});

test("canvas", "the replace flag does not outlive the replace it was for", async () => {
	const [l1, l2] = CANVAS_COURSES[CA];
	const t = await openCanvas({ path: cv(), search: "?course=" + CA }); // a replace ran
	t.canvas.select_lesson(l2); // well inside frappe's 100ms
	await settle();
	assert.deepEqual(t.desk.entries, [cv(CA, l1), cv(CA, l2)], "the rail click replaced instead of pushing");
});

// ================================================================================ glossary

// A small jQuery with a real element tree: enough for the page to draw cards whose
// textareas hold what was typed, and for a test to find and press its buttons.
class El {
	constructor(tag, attrs) {
		this.tag = tag;
		this.attrs = attrs || {};
		this.classes = new Set(String(this.attrs.class || "").split(/\s+/).filter(Boolean));
		this.children = [];
		this.parent = null;
		this.handlers = {};
		this.text = "";
		this.value = "";
		this.props = {};
		this.shown = true;
	}
	all() {
		const out = [];
		const walk = (node) => {
			node.children.forEach((child) => {
				out.push(child);
				walk(child);
			});
		};
		walk(this);
		return out;
	}
	allText() {
		return this.text + this.children.map((c) => c.allText()).join("");
	}
	fire(event, payload) {
		(this.handlers[event] || []).forEach((fn) => fn(payload || { target: { checked: !!this.props.checked } }));
	}
}

function parseHtml(html) {
	const root = new El("#root");
	let at = root;
	const re = /<(\/?)([a-zA-Z0-9]+)([^>]*)>|([^<]+)/g;
	let m;
	while ((m = re.exec(html))) {
		if (m[4] !== undefined) {
			at.text += m[4];
			continue;
		}
		if (m[1]) {
			at = at.parent || root;
			continue;
		}
		const attrs = {};
		m[3].replace(/([a-zA-Z-]+)=("([^"]*)"|'([^']*)')/g, (_, key, __, dq, sq) => {
			attrs[key] = dq !== undefined ? dq : sq;
		});
		const node = new El(m[2].toLowerCase(), attrs);
		node.parent = at;
		at.children.push(node);
		if (!/^(input|br|img)$/.test(node.tag)) at = node;
	}
	const top = root.children[0];
	top.parent = null;
	return top;
}

function makeGlossaryQuery() {
	const boxes = new WeakMap();
	function wrap(el) {
		const api = {
			__el: el,
			appendTo(parent) {
				const p = unwrap(parent);
				detach(el);
				p.children.push(el);
				el.parent = p;
				return api;
			},
			append(...items) {
				items.forEach((item) => {
					const child = unwrap(item);
					detach(child);
					el.children.push(child);
					child.parent = el;
				});
				return api;
			},
			insertBefore(ref) {
				const r = unwrap(ref);
				detach(el);
				const p = r.parent;
				p.children.splice(p.children.indexOf(r), 0, el);
				el.parent = p;
				return api;
			},
			empty() {
				el.children.forEach((c) => (c.parent = null));
				el.children = [];
				return api;
			},
			text(value) {
				if (value === undefined) return el.text;
				el.text = String(value);
				return api;
			},
			val(value) {
				if (value === undefined) return el.value;
				el.value = String(value);
				return api;
			},
			on(event, fn) {
				(el.handlers[event] = el.handlers[event] || []).push(fn);
				return api;
			},
			find(selector) {
				const hit = el.all().find((n) => (selector[0] === "." ? n.classes.has(selector.slice(1)) : n.tag === selector));
				return wrap(hit || new El("none"));
			},
			prop(key, value) {
				if (value === undefined) return el.props[key];
				el.props[key] = value;
				return api;
			},
			addClass(name) {
				el.classes.add(name);
				return api;
			},
			toggleClass(name, on) {
				if (on) el.classes.add(name);
				else el.classes.delete(name);
				return api;
			},
			toggle(on) {
				el.shown = !!on;
				return api;
			},
			remove() {
				detach(el);
				return api;
			},
		};
		return api;
	}
	function detach(el) {
		if (el.parent) el.parent.children = el.parent.children.filter((c) => c !== el);
		el.parent = null;
	}
	function unwrap(item) {
		if (item instanceof El) return item;
		if (item && item.__el) return item.__el;
		if (!boxes.has(item)) boxes.set(item, new El("object"));
		return boxes.get(item);
	}
	function $(arg) {
		if (typeof arg === "string") return wrap(parseHtml(arg));
		return wrap(unwrap(arg));
	}
	return $;
}

function glossaryTerms(start, count) {
	return Array.from({ length: count }, (_, i) => ({
		name: "GT-" + String(start + i).padStart(3, "0"),
		term: "term " + (start + i),
		short_definition: "defined " + (start + i),
		explanation: "",
		example: "",
	}));
}

async function openGlossary(opts) {
	const options = opts || {};
	const desk = makeDesk("training-glossary-review", { path: options.path });
	const $ = makeGlossaryQuery();
	const calls = [];
	const server = { hold: null, held: [], accepted: [], pageSize: 3, pending: 7 };
	desk.frappe.call = (o) => {
		const method = String(o.method).split(".").pop();
		const args = o.args || {};
		calls.push([method, args]);
		const answer = () => {
			if (method === "get_glossary_queue") {
				const left = Math.max(0, Math.min(server.pageSize, server.pending - args.start));
				return Promise.resolve({ message: { terms: glossaryTerms(args.start, left), start: args.start, pending: server.pending, total: 10 } });
			}
			if (method === "get_collisions") {
				return Promise.resolve({ message: { clusters: [{ spelling: "vaults", holders: ["Vault", "Reservoir"] }] } });
			}
			if (method === "accept_term") {
				if (server.failAccept) return Promise.reject(new Error("Not permitted"));
				server.accepted.push(args);
				return Promise.resolve({ message: {} });
			}
			return Promise.resolve({ message: {} });
		};
		if (server.hold && server.hold(method)) {
			const d = deferred();
			server.held.push({ method, release: () => answer().then(d.resolve, d.reject) });
			return d.promise;
		}
		return answer();
	};
	const page = { main: new El("main"), set_primary_action(label, fn) { page.refresh = fn; } };
	desk.frappe.ui.make_app_page = () => page;
	const sandbox = { frappe: desk.frappe, __: translate, $, console, setTimeout, clearTimeout, Promise, Object };
	sandbox.window = sandbox;
	desk.window = sandbox;
	vm.createContext(sandbox);
	vm.runInContext(fs.readFileSync(GLOSSARY_JS, "utf8"), sandbox, { filename: GLOSSARY_JS });
	desk.route();
	await settle();
	const t = {
		desk,
		calls,
		server,
		page,
		get review() {
			return desk.wrapper.glossary;
		},
		get body() {
			return desk.wrapper.glossary.body.__el;
		},
		loads(method) {
			return calls.filter(([m]) => m === method).length;
		},
		cards() {
			return t.body.children.filter((c) => c.classes.has("gr-card"));
		},
		textareas() {
			return t.body.all().filter((n) => n.tag === "textarea");
		},
		button(label, within) {
			const hit = (within || page.main).all().find((n) => n.tag === "button" && n.allText().trim() === label);
			assert.ok(hit, `no button "${label}"`);
			return hit;
		},
		get toggle() {
			return desk.wrapper.glossary.trapToggle.__el.all().find((n) => n.tag === "input");
		},
	};
	return t;
}

const gr = (...parts) => "/desk/" + ["training-glossary-review"].concat(parts).join("/");

test("glossary", "opening the page loads the queue once, not twice", async () => {
	const t = await openGlossary();
	assert.equal(t.loads("get_glossary_queue"), 1);
	assert.equal(t.cards().length, 3);
});

test("glossary", "the tabs are steps: Back returns to the queue, Forward to the collisions", async () => {
	const t = await openGlossary();
	t.button("Same word, two entries").fire("click");
	await settle();
	assert.equal(t.desk.url, gr("collisions"));
	assert.equal(t.loads("get_collisions"), 1, "a tab click loaded twice");
	await t.desk.back();
	await settle();
	assert.equal(t.desk.url, gr());
	assert.ok(t.body.all().some((n) => n.classes.has("gr-term") && n.text === "term 0"), "the queue is not back");
	await t.desk.forward();
	await settle();
	assert.ok(t.body.all().some((n) => n.classes.has("gr-cluster")), "Forward did not restore the collisions");
});

test("glossary", "the trade-trap toggle is a step too, and Back unticks it", async () => {
	const t = await openGlossary();
	t.toggle.props.checked = true;
	t.toggle.fire("change");
	await settle();
	assert.equal(t.desk.url, gr("traps"));
	assert.equal(t.calls[t.calls.length - 1][1].only_traps, 1);
	await t.desk.back();
	await settle();
	assert.equal(t.toggle.props.checked, false);
	assert.equal(t.calls[t.calls.length - 1][1].only_traps, 0);
});

test("glossary", "Back from 'Open the record' keeps what was typed (no reload)", async () => {
	const t = await openGlossary();
	const box = t.textareas()[0];
	box.value = "a better definition";
	box.fire("input");
	const loads = t.loads("get_glossary_queue");
	t.desk.frappe.set_route("training-glossary-term", "GT-000"); // the same-tab link
	await settle();
	await t.desk.back();
	await settle();
	assert.equal(t.loads("get_glossary_queue"), loads, "returning to the page reloaded it");
	assert.equal(t.textareas()[0], box);
	assert.equal(box.value, "a better definition");
});

test("glossary", "a correction survives a tab switch and Back", async () => {
	const t = await openGlossary();
	const box = t.textareas()[0];
	box.value = "kept across tabs";
	box.fire("input");
	t.button("Same word, two entries").fire("click");
	await settle();
	await t.desk.back();
	await settle();
	assert.equal(t.textareas()[0].value, "kept across tabs");
	t.button("Accept", t.cards()[0]).fire("click");
	await settle();
	assert.equal(t.server.accepted[0].short_definition, "kept across tabs");
	assert.deepEqual(Object.keys(t.review.drafts), [], "a sent correction was kept as a draft");
});

test("glossary", "a queue reply that lands after the tab changed paints nothing", async () => {
	const t = await openGlossary();
	t.server.hold = (method) => method === "get_glossary_queue";
	t.review.go(t.review.view); // reload the queue: held
	t.button("Same word, two entries").fire("click");
	await settle();
	t.server.held[0].release();
	await settle();
	assert.ok(t.body.all().some((n) => n.classes.has("gr-cluster")), "collisions were painted over");
	assert.ok(!t.body.all().some((n) => n.tag === "textarea"), "the late queue reply painted the queue");
});

test("glossary", "Next after a verdict skips nothing", async () => {
	const t = await openGlossary();
	t.button("Accept", t.cards()[0]).fire("click");
	await settle();
	t.server.pending -= 1; // term 0 has left the pending set
	t.button("Next").fire("click");
	await settle();
	const last = t.calls.filter(([m]) => m === "get_glossary_queue").pop();
	assert.equal(last[1].start, 2, "Next advanced by the page drawn, skipping an unreviewed entry");
	assert.ok(t.button("Previous"), "the pager's back button should say Previous");
});

function lastQueueStart(t) {
	const last = t.calls.filter(([m]) => m === "get_glossary_queue").pop();
	return last[1].start;
}

test("glossary", "Next pressed while a verdict is in flight waits for it, and skips nothing", async () => {
	const t = await openGlossary();
	t.server.hold = (method) => method === "accept_term";
	t.button("Accept", t.cards()[0]).fire("click"); // the reply is held
	t.button("Next").fire("click"); // straight away
	await settle();
	assert.equal(t.loads("get_glossary_queue"), 1, "Next asked for a page before the verdict was answered");
	t.server.pending -= 1; // the server has taken term 0 out of the set
	t.server.held[0].release();
	await settle();
	assert.equal(t.loads("get_glossary_queue"), 2);
	assert.equal(lastQueueStart(t), 2, "Next advanced by the page drawn, skipping an unreviewed entry");
});

test("glossary", "Next after a verdict that failed advances by the whole page", async () => {
	const t = await openGlossary();
	t.server.failAccept = true;
	t.button("Accept", t.cards()[0]).fire("click");
	await settle();
	assert.equal(t.cards().length, 3, "a refused verdict removed its card");
	t.button("Next").fire("click");
	await settle();
	assert.equal(lastQueueStart(t), 3, "a verdict the server refused was counted as one that left the set");
});

test("glossary", "Refresh puts back the server's text in the cards on screen, after asking", async () => {
	const t = await openGlossary();
	const asked = [];
	t.desk.frappe.confirm = (message, yes) => {
		asked.push(message);
		yes();
	};
	const kept = t.textareas()[0];
	kept.value = "typed on page one";
	kept.fire("input");
	t.button("Next").fire("click");
	await settle();
	const dropped = t.textareas()[0];
	dropped.value = "typed on page two";
	dropped.fire("input");
	t.page.refresh(); // the primary Refresh
	await settle();
	assert.equal(t.textareas()[0].value, "defined 3", "Refresh no longer puts back the server's text");
	assert.equal(asked.length, 1, "Refresh threw typing away without asking");
	assert.deepEqual(Object.keys(t.review.drafts), ["GT-000"], "Refresh dropped typing on a page not on screen");
	t.button("Previous").fire("click");
	await settle();
	assert.equal(t.textareas()[0].value, "typed on page one");
});

test("glossary", "Refresh with nothing typed asks nothing and reloads", async () => {
	const t = await openGlossary();
	t.desk.frappe.confirm = () => assert.fail("asked with nothing to lose");
	t.page.refresh();
	await settle();
	assert.equal(t.loads("get_glossary_queue"), 2);
});

test("glossary", "Back to the queue returns to the page it was left on", async () => {
	const t = await openGlossary();
	t.button("Next").fire("click");
	await settle();
	t.button("Same word, two entries").fire("click");
	await settle();
	await t.desk.back();
	await settle();
	const last = t.calls.filter(([m]) => m === "get_glossary_queue").pop();
	assert.equal(last[1].start, 3);
});

test("glossary", "pressing the tab already open reloads it in place, adding no step", async () => {
	const t = await openGlossary();
	const before = t.desk.entries.length;
	t.button("To review").fire("click");
	await settle();
	assert.equal(t.desk.entries.length, before);
	assert.equal(t.loads("get_glossary_queue"), 2);
});

test("glossary", "a deep link to a tab opens on it", async () => {
	const t = await openGlossary({ path: gr("collisions") });
	assert.equal(t.loads("get_glossary_queue"), 0);
	assert.equal(t.loads("get_collisions"), 1);
});

await runAll();
