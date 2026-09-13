#!/usr/bin/env node
/**
 * The training player's URL routing — executed, not grepped.
 *
 * THE BUG
 * =======
 *
 * Open /training, click a course, then click "All Courses" to come back. The page
 * showed the catalogue and the address bar still read:
 *
 *     /training?course=TRN-CRS-00002
 *
 * `go("catalog")` sets `state.view` and clears the DOM; it does not clear
 * `state.courseName`. `route()` then faithfully re-emitted the course it was still
 * holding, so the stale param outlived the course.
 *
 * That is not cosmetic, and this is the part worth keeping: **two code paths read the
 * URL back as the source of truth.** `start()` runs on every page load and does
 * `queryParam("course")`, and the `popstate` handler does the same on browser-back. So
 * a stale param meant refreshing the catalogue silently reopened the last course, and
 * pressing back landed on it instead of leaving. The user's word for it was "poisons
 * the next course I click on", which is exactly right: the URL had become a second,
 * disagreeing source of truth for which course was open.
 *
 * Note the shape of the original:
 *
 *     if (state.courseName) params.push("course=" + ...);                        // unguarded
 *     if (state.lessonKey && state.view !== "catalog" && state.view !== "course") // guarded
 *
 * `lesson=` was guarded by view and `course=` was not. One half of the rule was written
 * and the other assumed — an oversight, not a wrong idea, and invisible to any static
 * check because both lines read as deliberate.
 *
 * The same staleness applied to the transcript, queue, feed, directory and person
 * views: all reached from the catalogue, none of them about a course, all of them
 * leaving `?course=` in the bar. Those are covered below too.
 *
 * WHY A RUNNING TEST
 * ==================
 *
 * The fix is a predicate over a view name. Asserting that `COURSE_SCOPED_VIEWS` exists,
 * or that the file contains the word "catalog", would pass just as happily on a table
 * with the wrong members in it. So this extracts the real `route()` out of player.js
 * and calls it once per view, asserting the URL it hands to `history.replaceState`.
 *
 * Run: node scripts/test_training_route.mjs
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PLAYER_JS = path.join(
	HERE,
	"..",
	"erpnext_enhancements",
	"public",
	"js",
	"training",
	"player.js"
);

let failures = 0;
let checks = 0;

function test(name, fn) {
	checks += 1;
	try {
		fn();
		console.log("  ok   " + name);
	} catch (err) {
		failures += 1;
		console.log("  FAIL " + name);
		console.log("       " + (err && err.message ? err.message : String(err)));
	}
}

// ---------------------------------------------------------------- extract the real code

const source = fs.readFileSync(PLAYER_JS, "utf8");

const START = "var COURSE_SCOPED_VIEWS";
const END = "function queryParam(";
const from = source.indexOf(START);
const to = source.indexOf(END);
assert.ok(from !== -1, "COURSE_SCOPED_VIEWS not found — did route() get rewritten?");
assert.ok(to > from, "queryParam() not found after route() — the extraction anchors moved");
const FRAGMENT = source.slice(from, to);

// The fragment must actually be the routing code, or every assertion below is vacuous.
assert.ok(FRAGMENT.includes("function route()"), "extracted fragment does not contain route()");
assert.ok(FRAGMENT.includes("replaceState"), "extracted fragment does not call replaceState");

/** Run the REAL route() for one (view, courseName, lessonKey) and return what it did. */
function routeWith(view, courseName, lessonKey, opts) {
	const options = opts || {};
	const calls = [];
	const sandbox = {
		state: { view: view, courseName: courseName, lessonKey: lessonKey },
		b: { history: options.history, route_base: "/training" },
		window: {
			location: { pathname: "/training", search: "" },
			history: {
				replaceState: function (stateObj, title, url) {
					if (options.throwOnReplace) throw new Error("SecurityError: sandboxed");
					calls.push({ stateObj: stateObj, url: url });
				},
			},
		},
		encodeURIComponent: encodeURIComponent,
	};
	vm.createContext(sandbox);
	vm.runInContext(FRAGMENT + "\nroute();", sandbox);
	return { calls: calls, url: calls.length ? calls[calls.length - 1].url : null };
}

const COURSE = "TRN-CRS-00002";
const LESSON = "lesson-3";

console.log("training player routing");

// ------------------------------------------------------------------ THE REPORTED BUG

test("leaving a course for the catalogue drops ?course= from the URL", () => {
	// The exact sequence: /training -> course -> "All Courses". `state.courseName` is
	// still set at this point, because go("catalog") does not clear it.
	const { url } = routeWith("catalog", COURSE, LESSON);
	assert.equal(url, "/training", `catalogue URL still carried a param: ${url}`);
});

test("the poisoning is gone: nothing for start()/popstate to misread", () => {
	const { url } = routeWith("catalog", COURSE, LESSON);
	assert.ok(!url.includes("course="), "start() would reopen the stale course on refresh");
	assert.ok(!url.includes("lesson="), "popstate would land on the stale lesson");
});

// --------------------------------------------------------------- course-scoped views

test("the course outline names the course but no lesson", () => {
	const { url } = routeWith("course", COURSE, LESSON);
	assert.equal(url, "/training?course=" + COURSE);
});

test("a lesson names both", () => {
	const { url } = routeWith("lesson", COURSE, LESSON);
	assert.equal(url, "/training?course=" + COURSE + "&lesson=" + LESSON);
});

test("quiz and results keep the course and say which view", () => {
	assert.equal(
		routeWith("quiz", COURSE, LESSON).url,
		"/training?course=" + COURSE + "&lesson=" + LESSON + "&view=quiz"
	);
	assert.equal(
		routeWith("results", COURSE, LESSON).url,
		"/training?course=" + COURSE + "&lesson=" + LESSON + "&view=results"
	);
});

test("signoff and complete are still about a course, so the deep link survives a refresh", () => {
	assert.ok(routeWith("signoff", COURSE, LESSON).url.includes("course=" + COURSE));
	assert.ok(routeWith("complete", COURSE, LESSON).url.includes("course=" + COURSE));
});

// ----------------------------------------------------- the other views reached from it

test("no non-course view carries a course param", () => {
	// Every one of these is reached FROM the catalogue and is not about a course. They
	// had the same bug as the reported one; it simply had not been noticed.
	for (const view of ["record", "queue", "people", "feed", "person", "unavailable", "catalog"]) {
		const { url } = routeWith(view, COURSE, LESSON);
		assert.equal(url, "/training", `${view} carried: ${url}`);
	}
});

// ------------------------------------------------------------------ the history entry

test("the history state entry agrees with the URL", () => {
	// popstate reads the URL, but the entry is what a later handler would trust.
	const { calls } = routeWith("catalog", COURSE, LESSON);
	assert.equal(calls[0].stateObj.tr.course, null);
	assert.equal(calls[0].stateObj.tr.lesson, null);
	assert.equal(calls[0].stateObj.tr.view, "catalog");

	const inCourse = routeWith("lesson", COURSE, LESSON);
	assert.equal(inCourse.calls[0].stateObj.tr.course, COURSE);
	assert.equal(inCourse.calls[0].stateObj.tr.lesson, LESSON);
});

// ------------------------------------------------------------------------- the guards

test("history:false touches the address bar not at all", () => {
	// The builder preview drops straight into one lesson; it must not rewrite the URL
	// of the page hosting the iframe.
	const { calls } = routeWith("lesson", COURSE, LESSON, { history: false });
	assert.equal(calls.length, 0);
});

test("a sandboxed iframe refusing replaceState does not break the lesson", () => {
	assert.doesNotThrow(() => routeWith("lesson", COURSE, LESSON, { throwOnReplace: true }));
});

// ------------------------------------------------------- the test cannot pass vacuously

test("it would catch the original unguarded line", () => {
	// Reproduce the bug in miniature against the SAME harness: if `course=` is emitted
	// without consulting the view, the catalogue keeps the param.
	const broken = FRAGMENT.replace(
		"if (inCourse && state.courseName) {",
		"if (state.courseName) {"
	);
	assert.notEqual(broken, FRAGMENT, "the mutation anchor no longer matches route()");
	const calls = [];
	const sandbox = {
		state: { view: "catalog", courseName: COURSE, lessonKey: null },
		b: { history: true, route_base: "/training" },
		window: {
			location: { pathname: "/training", search: "" },
			history: { replaceState: (s, t, url) => calls.push(url) },
		},
		encodeURIComponent: encodeURIComponent,
	};
	vm.createContext(sandbox);
	vm.runInContext(broken + "\nroute();", sandbox);
	assert.equal(calls[0], "/training?course=" + COURSE, "the harness cannot see the bug");
});

console.log(`\n${checks - failures}/${checks} passed`);
if (failures) process.exit(1);
