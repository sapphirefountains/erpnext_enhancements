#!/usr/bin/env node
/**
 * Back and Forward on /training_preview -- executed, not grepped.
 *
 * THE BUG
 * =======
 *
 * The preview mounted the real player with `history: false` and no router, so moving catalog ->
 * course -> lesson -> quiz added no history entry. Opened in a new tab from the Desk, Back was
 * disabled; opened any other way, it left the page from every view. The Desk's "Preview as a
 * learner" link (`?course=X&lesson=Y`) opened the canned catalog instead of the lesson, and a
 * reload did the same.
 *
 * WHAT IS PINNED
 * ==============
 *
 *   - The page's own start re-stamps the entry it opened on and pushes nothing, so Back from the
 *     first view leaves the page as it always has.
 *   - A tap that moves to another PLACE pushes one entry: the catalog, a course's outline, a
 *     lesson, the record. A lesson's quiz and results share the lesson's entry, as on the Desk.
 *   - Back and Forward reopen the place through the player's own doors, and the write that
 *     follows is dropped: walking adds nothing.
 *   - A view the player opened without a tap re-stamps the entry instead of pushing (Chrome's
 *     Back skips an entry a page pushed without one), and so does a scenario button's remount.
 *   - Every address keeps the page's path and, in draft mode, ?course= from the page's own URL
 *     -- never the player's course, which can be the canned card's. A lesson adds ?lesson=.
 *   - Draft mode opens on the draft, and on the Desk's lesson when the link names one.
 *
 * HOW
 * ===
 *
 * The harness script is lifted out of www/training_preview.html and run in a vm context with a
 * fake document and a fake browser history. `TR.Player` there is a stand-in built from the REAL
 * `routeState()`/`route()` and `start()` of player.js, extracted the way
 * scripts/test_training_route.mjs extracts them, so what the harness is handed is what the
 * player really reports. The stand-in's course and lesson come from the harness's own canned
 * transport.
 *
 * Run: node scripts/test_training_preview_history.mjs
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.join(HERE, "..", "erpnext_enhancements");
const PREVIEW = path.join(APP, "www", "training_preview.html");
const PLAYER = path.join(APP, "public", "js", "training", "player.js");

let failures = 0;
let checks = 0;
function check(label, actual, expected) {
	checks += 1;
	const a = JSON.stringify(actual);
	const e = JSON.stringify(expected);
	if (a === e) {
		console.log(`  ok   ${label}`);
	} else {
		failures += 1;
		console.log(`  FAIL ${label}: got ${a}, want ${e}`);
	}
}

function die(message) {
	console.error("MARKERS NOT FOUND: " + message);
	process.exit(2);
}

// ---------------------------------------------------------------- the real code

const html = fs.readFileSync(PREVIEW, "utf8");
const HARNESS = html.slice(html.lastIndexOf("<script>") + "<script>".length, html.lastIndexOf("</script>"));
if (!HARNESS.includes("var transport = {") || !HARNESS.includes("new window.TR.Player(")) {
	die("the last <script> of training_preview.html is no longer the harness");
}
if (/\{\{|\{%/.test(HARNESS)) die("the harness script now holds Jinja; it can no longer run as plain JS here");

const player = fs.readFileSync(PLAYER, "utf8");
const cut = (from, to, what) => {
	const a = player.indexOf(from);
	const b = player.indexOf(to, a);
	if (a === -1 || b === -1) die(`player.js ${what}: the extraction anchors moved`);
	return player.slice(a, b);
};
const ROUTING = cut("var COURSE_SCOPED_VIEWS", "function queryParam(", "route()");
const START = cut("function start() {", "\n\t\tstart();", "start()");
if (!ROUTING.includes("b.router.write(next)")) die("route() no longer hands the router what it reports");

/**
 * TR.Player, as far as routing goes: the real routeState()/route()/start(), with go, openCourse,
 * openLesson and openPerson reduced to what they do to `state` and when they call route().
 * openCourse settles on a later microtask, as the real one does behind its transport calls.
 */
function makePlayerClass(log) {
	return function FakePlayer(root, b, transport) {
		const state = { view: null, courseName: null, lessonKey: null, viewingUser: null };
		const me = { b, views: [] };
		const ctx = {
			state,
			b,
			window: {},
			encodeURIComponent,
			queryParam: () => "",
			go(view) {
				state.view = view;
				me.views.push(view);
				ctx.route();
			},
			openCourse(course, lesson) {
				state.courseName = course;
				state.view = "course";
				return Promise.resolve().then(() => {
					const payload = transport.getCourse({ course }) || {};
					state.courseName = (payload.course && payload.course.course) || course;
					const wanted = lesson || state.lessonKey;
					if (wanted) {
						const got = transport.getLesson({ lesson_key: wanted }) || {};
						state.lessonKey = (got.lesson && got.lesson.lesson_key) || wanted;
					}
					ctx.go(lesson ? "lesson" : "course");
				});
			},
			openPerson(user) {
				state.viewingUser = user;
				ctx.go("person");
			},
		};
		vm.createContext(ctx);
		vm.runInContext(ROUTING + "\n" + START, ctx);
		me.go = ctx.go;
		me.openCourse = ctx.openCourse;
		me.openPerson = ctx.openPerson;
		me.openLesson = (key) => {
			state.lessonKey = key;
			ctx.go("lesson");
		};
		me.state = state;
		me.destroy = () => {
			me.destroyed = true;
		};
		log.push(me);
		ctx.start();
		return me;
	};
}

// ---------------------------------------------------------------- a fake page

function fakeHistory(entries, index) {
	const calls = [];
	let at = index === undefined ? entries.length - 1 : index;
	const listeners = [];
	const clone = (v) => (v === undefined || v === null ? null : structuredClone(v));
	const traverse = (delta) => {
		const target = at + delta;
		if (target < 0 || target >= entries.length) return false; // a browser would leave the page
		at = target;
		const ev = { type: "popstate", state: clone(entries[at].state) };
		for (const fn of listeners.slice()) fn(ev);
		return true;
	};
	const history = {
		get state() {
			return clone(entries[at].state);
		},
		pushState(state, title, url) {
			calls.push({ call: "push", args: arguments.length, url, state: clone(state) });
			entries.splice(at + 1);
			entries.push({ state: clone(state), url });
			at = entries.length - 1;
		},
		replaceState(state, title, url) {
			calls.push({ call: "replace", args: arguments.length, url, state: clone(state) });
			entries[at] = { state: clone(state), url: url === undefined ? entries[at].url : url };
		},
	};
	const location = {
		get pathname() {
			return entries[at].url.split("?")[0];
		},
		get search() {
			const q = entries[at].url.indexOf("?");
			return q === -1 ? "" : entries[at].url.slice(q);
		},
	};
	return { history, location, listeners, calls, entries, index: () => at, back: () => traverse(-1), forward: () => traverse(1) };
}

/** Mount the harness. `draft`: the tp-draft payload, or null for the canned workbench. */
function page(options) {
	const opts = options || {};
	const entries = opts.entries || [{ state: null, url: opts.url || "/training_preview" }];
	const h = fakeHistory(entries, opts.index);
	const players = [];
	let now = 1000000;
	const docListeners = [];
	const barListeners = [];
	const button = (attr, value, pressed) => {
		const attrs = { [attr]: value };
		if (pressed !== undefined) attrs["aria-pressed"] = pressed;
		const node = {
			attrs,
			hasAttribute: (n) => n in attrs,
			getAttribute: (n) => (n in attrs ? attrs[n] : null),
			setAttribute: (n, v) => (attrs[n] = String(v)),
		};
		node.closest = () => node;
		return node;
	};
	const scenarios = [button("data-scenario", "populated", "true"), button("data-scenario", "empty", "false"), button("data-scenario", "dormant", "false")];
	const jumps = { catalog: button("data-jump", "catalog"), course: button("data-jump", "course"), record: button("data-jump", "record") };
	const bar = {
		addEventListener: (type, fn) => barListeners.push(fn),
		querySelectorAll: (sel) => (sel === "[data-scenario]" ? scenarios : []),
	};
	const root = { setAttribute() {}, innerHTML: "" };
	const islands = {
		"tp-draft": opts.draft ? { textContent: JSON.stringify(opts.draft) } : null,
		"tp-csrf": opts.draft ? { textContent: JSON.stringify("csrf") } : null,
		"training-root": root,
	};
	const document = {
		getElementById: (id) => islands[id] || null,
		querySelector: (sel) => (sel === ".hp-bar" ? bar : null),
		addEventListener: (type, fn, capture) => docListeners.push({ type, fn, capture }),
	};
	const window = {
		TR: { Player: makePlayerClass(players) },
		history: h.history,
		location: h.location,
		addEventListener: (type, fn) => {
			if (type === "popstate") h.listeners.push(fn);
		},
	};
	const ctx = { window, document, URLSearchParams, console, fetch: () => Promise.reject(new Error("no network here")) };
	vm.createContext(ctx);
	ctx.__now = () => now;
	vm.runInContext("Date.now = function () { return __now(); };", ctx);
	vm.runInContext(HARNESS, ctx);
	/** A person's tap: the document's capture listener first, then (for a bar button) the bar's. */
	const tap = (target) => {
		for (const l of docListeners) if (l.type === "click" && l.capture) l.fn({ target });
		if (target) for (const fn of barListeners) fn({ target });
	};
	return {
		h,
		players,
		scenarios,
		jumps,
		tap,
		current: () => players[players.length - 1],
		advance: (ms) => (now += ms),
		pressed: () => scenarios.filter((b) => b.attrs["aria-pressed"] === "true").map((b) => b.attrs["data-scenario"]),
		docListeners,
	};
}

const flush = async () => {
	for (let i = 0; i < 6; i++) await new Promise((resolve) => setImmediate(resolve));
};
const place = (call) => call && call.state && call.state.tp;
/** Null-safe, so a regression fails a check rather than crashing the run. */
const brief = (call) => (call ? [call.call, call.url, place(call) && place(call).view] : null);

const DRAFT = {
	course: { course: "TRN-CRS-00042", title: "Pump Room Lockout" },
	gates: {},
	version: { version_number: 3 },
	chapters: [],
	toc: [{ lesson_key: "d1" }, { lesson_key: "d2" }],
	lessons: [
		{ lesson_key: "d1", title: "Why lock out", blocks: [] },
		{ lesson_key: "d2", title: "The lock box", blocks: [] },
	],
	quiz_draws: {},
	keys: {},
};

console.log("training preview — Back and Forward\n");

console.log("the page's own start adds no entry");
{
	const pg = page();
	check("one call, a replace, onto the catalog", pg.h.calls.map(brief), [["replace", "/training_preview", "catalog"]]);
	check("the player keeps history:false and is handed a router", [pg.current().b.history, typeof (pg.current().b.router || {}).write], [false, "function"]);
	check("the popstate listener is the harness's alone", pg.h.listeners.length, 1);
}

console.log("\na tap to another place pushes; the quiz and results share the lesson's entry");
{
	const pg = page();
	pg.tap(pg.jumps.course);
	await flush();
	pg.tap(null);
	pg.current().openLesson("l1");
	pg.tap(null);
	pg.current().go("quiz");
	pg.tap(null);
	pg.current().go("results");
	check("two pushes: the outline, then the lesson with ?lesson=", pg.h.calls.slice(1).map(brief), [
		["push", "/training_preview", "course"],
		["push", "/training_preview?lesson=l1", "lesson"],
	]);
	check("each with its place", pg.h.calls.slice(1).map((c) => [place(c).course, place(c).lesson]), [
		["TRN-CRS-PREVIEW", null],
		["TRN-CRS-PREVIEW", "l1"],
	]);

	console.log("\nBack and Forward walk them, and write nothing");
	const before = pg.h.calls.length;
	pg.h.back();
	await flush();
	check("Back: the outline", [pg.current().state.view, pg.h.index()], ["course", 1]);
	pg.h.back();
	await flush();
	check("Back: the catalog", [pg.current().state.view, pg.h.index()], ["catalog", 0]);
	check("Back from the first view leaves the page (nothing in it to step to)", pg.h.back(), false);
	pg.h.forward();
	await flush();
	pg.h.forward();
	await flush();
	check("Forward twice: the lesson", [pg.current().state.view, pg.current().state.lessonKey], ["lesson", "l1"]);
	check("no history call on the way", pg.h.calls.slice(before), []);
	check("one player throughout: Back reopens, it does not remount", pg.players.length, 1);
}

console.log("\nno tap, no push");
{
	const pg = page();
	pg.current().go("record");
	check("a view the player opened by itself re-stamps the entry", pg.h.calls.slice(1).map(brief), [["replace", "/training_preview", "record"]]);
	pg.tap(null);
	pg.advance(6000);
	pg.current().go("people");
	check("nor does a tap from long before", pg.h.calls.slice(2).map(brief), [["replace", "/training_preview", "people"]]);
	check("still one entry", pg.h.entries.length, 1);
}

console.log("\na scenario button remounts in place; Back remounts the scenario it left");
{
	const pg = page();
	pg.tap(pg.jumps.course);
	await flush();
	pg.tap(pg.scenarios[1]); // Empty
	await flush();
	check("the remount replaces, even after a tap", pg.h.calls.slice(2).map((c) => [c.call, place(c).scenario, place(c).view]), [["replace", "empty", "catalog"]]);
	check("the Empty button is pressed", pg.pressed(), ["empty"]);
	const before = pg.h.calls.length;
	pg.h.back();
	await flush();
	check("Back: the populated scenario again, on its catalog", [pg.pressed(), pg.current().b.enabled, pg.current().state.view], [["populated"], true, "catalog"]);
	check("a fresh player, the old one destroyed", [pg.players.length, pg.players[1].destroyed], [3, true]);
	check("and nothing written: that entry already says where it is", pg.h.calls.slice(before), []);
	check("still one popstate listener after three mounts", pg.h.listeners.length, 1);
}

console.log("\na reload lands where its entry says");
{
	const kept = { tp: { scenario: "populated", view: "record", course: null, lesson: null, user: null } };
	const pg = page({ entries: [{ state: kept, url: "/training_preview" }] });
	check("the player starts on it, and nothing is written", [pg.current().state.view, pg.h.calls], ["record", []]);
}
{
	const pg = page({ url: "/training_preview?lesson=l2" });
	await flush();
	check("canned ?lesson= opens that lesson", [pg.current().state.view, pg.current().state.lessonKey], ["lesson", "l2"]);
	check("re-stamped with the same address", pg.h.calls.map(brief), [["replace", "/training_preview?lesson=l2", "lesson"]]);
}

console.log("\ndraft mode: the Desk's link, and ?course= on every address");
{
	const pg = page({ draft: DRAFT, url: "/training_preview?course=TRN-CRS-00042&lesson=d2" });
	await flush();
	check("the Desk's lesson opens, not the canned catalog", [pg.current().state.view, pg.current().state.courseName, pg.current().state.lessonKey], ["lesson", "TRN-CRS-00042", "d2"]);
	check("the first view re-stamps, keeping ?course= and ?lesson=", pg.h.calls.map(brief), [["replace", "/training_preview?course=TRN-CRS-00042&lesson=d2", "lesson"]]);
	pg.tap(pg.jumps.catalog);
	check("the catalog keeps ?course= (the server picks draft mode by it)", brief(pg.h.calls[1]), ["push", "/training_preview?course=TRN-CRS-00042", "catalog"]);
	pg.tap(pg.jumps.course); // "Open course": the canned card's name
	await flush();
	check("the canned card's name never reaches the address", brief(pg.h.calls[2]), ["push", "/training_preview?course=TRN-CRS-00042", "course"]);
	check("every address keeps the page's path", pg.h.calls.every((c) => c.url.startsWith("/training_preview?course=TRN-CRS-00042")), true);
	pg.h.back();
	pg.h.back();
	await flush();
	check("Back twice: the Desk's lesson again", [pg.current().state.view, pg.current().state.lessonKey, pg.h.calls.length], ["lesson", "d2", 3]);
}
{
	const pg = page({ draft: DRAFT, url: "/training_preview?course=TRN-CRS-00042" });
	await flush();
	check("with no ?lesson=, the draft's outline", [pg.current().state.view, brief(pg.h.calls[0])], ["course", ["replace", "/training_preview?course=TRN-CRS-00042", "course"]]);
}

console.log("\nan entry that is not the page's is left alone");
{
	const pg = page({ entries: [{ state: null, url: "/training_preview" }, { state: { ee_capture: "x" }, url: "/training_preview" }], index: 0 });
	const before = pg.players[0].views.length;
	pg.h.forward();
	await flush();
	check("no view change, no write", [pg.players[0].views.length - before, pg.h.calls.length], [0, 1]);
}

console.log(`\n${checks - failures}/${checks} passed`);
if (failures) process.exit(1);
