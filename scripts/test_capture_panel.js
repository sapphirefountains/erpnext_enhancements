#!/usr/bin/env node
/**
 * Guards what the capture panel sends (WI-079 slice 2) — the parts that are pure and can be
 * checked without a browser.
 *
 * The rules pinned here are the ones a quiet edit could break without anything looking wrong:
 *
 *   - **the query string never leaves a web or kiosk page.** Token pages carry secrets there
 *     (ADR 0016 §4). On the Desk it is kept, from the snapshot the person saw;
 *   - **the payload holds only fields the server accepts** (`SUBMIT_ALLOWED_FIELDS` in
 *     `api/feedback.py`) — anything else comes back in `rejected` and is a bug here — and
 *     never a key named `sid`, which makes Frappe fake a session-expired error;
 *   - **the snapshot is fitted under the server's 200 KB limit before it is shown**, so what
 *     the person reviews is what is sent;
 *   - **the error wording** the WI specifies for 429 and 403, and that a network failure is
 *     classified as "offline" (which saves a draft) rather than as an error (which loses the
 *     report).
 *
 * Imports `panel.js` for real, which also imports `transport.js`, `annotate.js` and
 * `drafts.js`: all four must stay importable without a DOM. If one grows a top-level browser
 * dependency this fails at import rather than silently asserting nothing.
 *
 * Run: node scripts/test_capture_panel.js
 */

const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");

const CAPTURE = path.join(__dirname, "..", "erpnext_enhancements", "public", "js", "capture");
const TARGET = path.join(CAPTURE, "panel.js");
const FEEDBACK_API = path.join(__dirname, "..", "erpnext_enhancements", "api", "feedback.py");

let failures = 0;

function check(label, actual, expected) {
	const a = JSON.stringify(actual);
	const e = JSON.stringify(expected);
	if (a === e) {
		console.log(`  ok   ${label}`);
	} else {
		failures += 1;
		console.error(`  FAIL ${label}: got ${a}, want ${e}`);
	}
}

function truthy(label, value) {
	check(label, !!value, true);
}

/** The server's allowlist, read from the source so the two cannot drift apart unnoticed. */
function serverAllowedFields() {
	const text = fs.readFileSync(FEEDBACK_API, "utf8");
	const match = text.match(/SUBMIT_ALLOWED_FIELDS\s*=\s*frozenset\(\s*\{([\s\S]*?)\}\s*\)/);
	if (!match) return null;
	return new Set(Array.from(match[1].matchAll(/"([a-z_]+)"/g), (m) => m[1]));
}

function keysDeep(value, out) {
	const keys = out || [];
	if (Array.isArray(value)) value.forEach((item) => keysDeep(item, keys));
	else if (value && typeof value === "object") {
		for (const key of Object.keys(value)) {
			keys.push(key);
			keysDeep(value[key], keys);
		}
	}
	return keys;
}

(async () => {
	let P;
	try {
		P = await import(pathToFileURL(TARGET).href);
	} catch (err) {
		console.error("COULD NOT LOAD panel.js — it and its imports must stay importable without a DOM.");
		console.error(err && err.message);
		process.exit(2);
	}

	const needed = [
		"openPanel",
		"validateFields",
		"descriptionCounter",
		"contextFields",
		"buildPayload",
		"resolveAppVersion",
		"resolveUser",
		"fitSnapshot",
		"minimalSnapshot",
		"summarizeSnapshot",
		"classifyError",
		"describeDraftOutcome",
		"sendDraftsLabel",
		"normalizeSurface",
		"sendSavedDrafts",
		"clearSavedDrafts",
	];
	for (const name of needed) {
		if (typeof P[name] !== "function") {
			console.error(`MARKER NOT FOUND: panel.js no longer exports ${name}()`);
			process.exit(2);
		}
	}

	console.log("\nvalidation mirrors the server: title required, <= 200; description >= 20");
	const good = {
		request_type: "Bug",
		impact: "Blocking my work",
		title: "Save does nothing",
		description: "x".repeat(20),
	};
	check("a good report passes", P.validateFields(good), { ok: true, errors: {} });
	check("empty title", Object.keys(P.validateFields({ ...good, title: "   " }).errors), ["title"]);
	check("201-char title", Object.keys(P.validateFields({ ...good, title: "t".repeat(201) }).errors), ["title"]);
	check("200-char title is fine", P.validateFields({ ...good, title: "t".repeat(200) }).ok, true);
	check("19 characters is short", Object.keys(P.validateFields({ ...good, description: "d".repeat(19) }).errors), [
		"description",
	]);
	check(
		"padding does not count toward 20",
		P.validateFields({ ...good, description: `   ${"d".repeat(19)}   ` }).ok,
		false
	);
	check("unknown type", Object.keys(P.validateFields({ ...good, request_type: "Idea" }).errors), ["request_type"]);
	check("Feature is a type", P.validateFields({ ...good, request_type: "Feature" }).ok, true);
	check("impact is required, as the server requires it", Object.keys(P.validateFields({ ...good, impact: "" }).errors), ["impact"]);
	check("an impact the Select does not know is refused", Object.keys(P.validateFields({ ...good, impact: "Urgent" }).errors), ["impact"]);
	check("the impacts are the Select's own", P.IMPACTS, ["Blocking my work", "Painful but I can work around it", "Nice to have"]);

	console.log("\nthe description counter");
	check("empty", P.descriptionCounter(""), { ok: false, text: "20 more characters needed" });
	check("one short", P.descriptionCounter("d".repeat(19)), { ok: false, text: "1 more character needed" });
	check("enough", P.descriptionCounter("d".repeat(25)), { ok: true, text: "25 characters" });

	console.log("\ncontext_url: path always; the query string on the Desk only");
	const deskSnap = {
		surface: "desk",
		page: { path: "/desk/todo/abc", query: "?view=1", form: { doctype: "ToDo", name: "abc", is_new: false } },
	};
	const env = (surface, extra) => ({
		surface,
		location: { pathname: "/from-location", search: "?token=SECRET" },
		userAgent: "UA",
		appVersion: "1.2.3",
		...(extra || {}),
	});
	const desk = P.contextFields(deskSnap, env("desk"));
	check("desk keeps the snapshot's query", desk.context_url, "/desk/todo/abc?view=1");
	check("desk form doctype", desk.context_doctype, "ToDo");
	check("desk form name", desk.context_docname, "abc");
	const web = P.contextFields({ surface: "web", page: { path: "/itinerary", query: "?token=SECRET" } }, env("web"));
	check("web drops a query even when the snapshot carries one", web.context_url, "/itinerary");
	const kiosk = P.contextFields(null, env("kiosk"));
	check("kiosk with no snapshot uses the path, never location.search", kiosk.context_url, "/from-location");
	const deskNoSnap = P.contextFields(null, env("desk"));
	check("desk with no snapshot falls back to location", deskNoSnap.context_url, "/from-location?token=SECRET");
	check(
		"desk query without its ? gets one",
		P.contextFields({ page: { path: "/desk", query: "a=1" } }, env("desk")).context_url,
		"/desk?a=1"
	);
	check(
		"desk snapshot with a null query adds nothing from location",
		P.contextFields({ page: { path: "/desk/todo", query: null } }, env("desk")).context_url,
		"/desk/todo"
	);
	check(
		"a new document's throwaway name is not sent",
		P.contextFields({ page: { path: "/desk/todo/new", form: { doctype: "ToDo", name: "new-todo-1", is_new: true } } }, env("desk"))
			.context_docname,
		""
	);
	const listCtx = P.contextFields({ page: { path: "/desk/todo", list: { doctype: "ToDo", view: "List" } } }, env("desk"));
	check("a list gives the doctype and no name", [listCtx.context_doctype, listCtx.context_docname], ["ToDo", ""]);
	check(
		"user agent capped at 300",
		P.contextFields(null, env("web", { userAgent: "u".repeat(400) })).context_user_agent.length,
		300
	);
	check("url capped at 500", P.contextFields({ page: { path: `/${"p".repeat(600)}` } }, env("web")).context_url.length, 500);
	check("app version passed through", desk.context_app_version, "1.2.3");

	console.log("\nthe payload holds only what the server accepts");
	const payload = P.buildPayload(
		{ request_type: "Bug", title: "  Save does nothing  ", description: "  It spins forever.  ", steps_to_reproduce: " 1. Click " },
		desk
	);
	check("title trimmed", payload.title, "Save does nothing");
	check("description trimmed", payload.description, "It spins forever.");
	check("steps kept for a bug", payload.steps_to_reproduce, "1. Click");
	check(
		"Feature drops steps",
		"steps_to_reproduce" in P.buildPayload({ request_type: "Feature", title: "t", description: "d", steps_to_reproduce: "s" }, desk),
		false
	);
	check("empty steps are omitted, not sent blank", "steps_to_reproduce" in P.buildPayload({ ...good, steps_to_reproduce: "  " }, desk), false);
	check("an unknown type becomes Bug", P.buildPayload({ request_type: "Idea" }, {}).request_type, "Bug");
	check("the chosen impact is sent", P.buildPayload({ ...good, impact: "Nice to have" }, desk).impact, "Nice to have");
	check("a missing impact sends the shown default", P.buildPayload({ request_type: "Bug" }, {}).impact, P.DEFAULT_IMPACT);
	const allowed = serverAllowedFields();
	if (!allowed || !allowed.size) {
		failures += 1;
		console.error("  FAIL could not read SUBMIT_ALLOWED_FIELDS from api/feedback.py");
	} else {
		const extra = Object.keys(payload).filter((key) => !allowed.has(key));
		check("every payload key is in SUBMIT_ALLOWED_FIELDS", extra, []);
	}
	check("no key named sid anywhere in the payload", keysDeep(payload).includes("sid"), false);

	console.log("\nthe app version: Desk boot, else the web template's build, else blank");
	check(
		"Desk boot wins",
		P.resolveAppVersion({
			frappe: { boot: { versions: { erpnext_enhancements: "1.530.0" } } },
			EE_CAPTURE: { build: "web" },
		}),
		"1.530.0"
	);
	check("web build", P.resolveAppVersion({ EE_CAPTURE: { build: "1.529.0" } }), "1.529.0");
	check("nothing", P.resolveAppVersion({}), "");
	check("no window", P.resolveAppVersion(undefined), "");

	console.log("\nwho the drafts belong to (a hint only; the server is asked before sending)");
	check("opts first", P.resolveUser({ user: "a@x.com" }, { EE_CAPTURE: { user: "b@x.com" } }), "a@x.com");
	check("then the recorder's config", P.resolveUser({}, { ee_capture: { config: { user: "c@x.com" } }, EE_CAPTURE: { user: "b@x.com" } }), "c@x.com");
	check("then the web template", P.resolveUser({}, { EE_CAPTURE: { user: "b@x.com" } }), "b@x.com");
	check("then the Desk session", P.resolveUser({}, { frappe: { session: { user: "d@x.com" } } }), "d@x.com");
	check("Guest is nobody", P.resolveUser({ user: "Guest" }, {}), "");
	const hostile = {};
	Object.defineProperty(hostile, "EE_CAPTURE", {
		get() {
			throw new Error("boom");
		},
	});
	hostile.frappe = { session: { user: "e@x.com" } };
	check("a throwing source is skipped", P.resolveUser({}, hostile), "e@x.com");

	console.log("\nthe snapshot is fitted under the limit before it is shown");
	const small = { schema: 1, page: { path: "/desk" }, console: [], requests: [], routes: [], app: { a: 1 } };
	const fittedSmall = P.fitSnapshot(small);
	check("a small snapshot is unchanged", fittedSmall, { snapshot: small, trimmed: false });
	fittedSmall.snapshot.app.a = 2;
	check("and it is a copy, not the recorder's object", small.app.a, 1);
	const huge = {
		schema: 1,
		captured_at: "2026-09-23T00:00:00Z",
		surface: "desk",
		page: { path: "/desk/todo/abc", title: "ToDo" },
		console: Array.from({ length: 20 }, (_, i) => ({ level: "error", message: "e".repeat(5000) + i, count: 1 })),
		requests: Array.from({ length: 20 }, () => ({ method: "POST", path: "/api/method/x", status: 500 })),
		routes: Array.from({ length: 10 }, (_, i) => ({ path: `/desk/${i}` })),
		app: { blob: "z".repeat(400 * 1024) },
	};
	const fittedHuge = P.fitSnapshot(huge);
	const size = Buffer.byteLength(JSON.stringify(fittedHuge.snapshot), "utf8");
	truthy(`an oversized snapshot fits (${size} bytes <= ${P.SNAPSHOT_MAX_BYTES})`, size <= P.SNAPSHOT_MAX_BYTES);
	check("it says it was shortened", [fittedHuge.trimmed, fittedHuge.snapshot.truncated], [true, true]);
	check("the page survives", fittedHuge.snapshot.page.path, "/desk/todo/abc");
	check("the recorder's object is untouched", huge.app.blob.length, 400 * 1024);
	check("a tighter limit still fits", Buffer.byteLength(JSON.stringify(P.fitSnapshot(huge, 2000).snapshot)) <= 2000, true);
	check("not an object -> null", P.fitSnapshot("nope"), { snapshot: null, trimmed: false });
	const loop = { a: 1 };
	loop.self = loop;
	check("circular -> null, not a throw", P.fitSnapshot(loop), { snapshot: null, trimmed: false });
	check("the limit leaves room under the server's 200 KB", P.SNAPSHOT_MAX_BYTES < 200 * 1024, true);

	console.log("\nthe fallback snapshot never carries a query string");
	const minimal = P.minimalSnapshot("desk", { pathname: "/desk/x", title: "X", userAgent: "UA" });
	check("schema 1", minimal.schema, 1);
	check("query is null even on the Desk", minimal.page.query, null);
	truthy("it says why it is thin", minimal.note);
	check("an unknown surface becomes web", P.minimalSnapshot("mars", {}).surface, "web");

	console.log("\nsummary line");
	check(
		"counts and plurals",
		P.summarizeSnapshot({ console: [1], requests: [1, 2], routes: [] }),
		"1 console error · 2 failed or slow requests · 0 recent pages"
	);
	check("missing arrays are zero", P.summarizeSnapshot(null), "0 console errors · 0 failed or slow requests · 0 recent pages");

	console.log("\nerrors: the WI's wording, and offline is a draft, not a failure");
	check("network failure is offline", P.classifyError({ status: 0 }).kind, "offline");
	check("429 wording", P.classifyError({ status: 429, message: "server text" }).message, "You've sent several reports just now. Try again in a minute.");
	check("403 wording", P.classifyError({ status: 403 }).message, "Only staff accounts can send reports.");
	check("417 shows the server's reason", P.classifyError({ status: 417, message: "New requests are paused right now." }), {
		kind: "refused",
		message: "New requests are paused right now.",
	});
	check("401 is an ended session", P.classifyError({ status: 401 }).kind, "session");
	check("SessionExpired is an ended session", P.classifyError({ status: 403, payload: { exc_type: "SessionExpired" } }).kind, "session");
	check("5xx is the server's problem", P.classifyError({ status: 502, message: "<html>Bad gateway</html>" }).kind, "server");
	check("413 names the screenshot", P.classifyError({ status: 413 }).kind, "refused");
	check("a plain Error keeps its message", P.classifyError(new Error("odd")), { kind: "error", message: "odd" });
	check("nothing at all", P.classifyError(null).kind, "error");

	console.log("\nsaved drafts: labels and outcomes");
	check("one", P.sendDraftsLabel(1), "Send 1 saved report");
	check("several", P.sendDraftsLabel(3), "Send 3 saved reports");
	check("all sent", P.describeDraftOutcome(["ER-1", "ER-2"], [], 0, null), {
		message: "Sent 2 saved reports (ER-1, ER-2).",
		tone: "ok",
	});
	check("throttled part-way", P.describeDraftOutcome(["ER-1"], [], 2, { kind: "throttled" }), {
		message: "Sent 1 saved report (ER-1). You've sent several reports just now. Try again in a minute. 2 reports still saved on this device.",
		tone: "warn",
	});
	check("refused and removed", P.describeDraftOutcome([], ["Title is required."], 0, null).tone, "bad");
	check("nothing to send", P.describeDraftOutcome([], [], 0, null).message, "There are no saved reports to send.");

	console.log("\nsurfaces");
	check("kiosk", P.normalizeSurface("KIOSK"), "kiosk");
	check("unknown falls back", P.normalizeSurface("tv", "desk"), "desk");
	check("unknown with no fallback is web", P.normalizeSurface(undefined), "web");

	console.log("\nthe stylesheet: prefixed, themed, above the Desk's modals, touch-sized");
	const css = P.PANEL_CSS || "";
	const classes = (css.match(/\.[a-zA-Z][\w-]*/g) || []).filter((c) => !/^\.\d/.test(c));
	check("every class selector is ee-cap-*", classes.filter((c) => !c.startsWith(".ee-cap-")), []);
	truthy("dark via the OS, unless the page chose light", css.includes('@media (prefers-color-scheme: dark)') && css.includes(':root:not([data-theme="light"])'));
	truthy("dark via the Desk/kiosk data-theme", css.includes(':root[data-theme="dark"]'));
	const zs = Array.from(css.matchAll(/z-index:\s*(\d+)/g), (m) => Number(m[1]));
	truthy("z-index above the Desk modal (1040) and the app's highest overlay (2050)", zs.length && Math.min(...zs) > 2050);
	truthy("44px controls by default", /--ee-cap-tap:\s*44px/.test(css));
	truthy("larger on the kiosk", /\.ee-cap-kiosk\s*\{\s*--ee-cap-tap:\s*56px/.test(css));

	console.log("\nsource rules for every panel-side file");
	// Comments stripped first: the comment explaining why a token is absent names the token.
	const stripComments = (source) => source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
	for (const name of ["panel.js", "annotate.js", "drafts.js"]) {
		const text = stripComments(fs.readFileSync(path.join(CAPTURE, name), "utf8"));
		check(`${name}: no innerHTML (the snapshot is page text)`, /\binnerHTML\b|outerHTML|insertAdjacentHTML/.test(text), false);
		check(`${name}: never the guest error logger`, text.includes("log_client_error"), false);
		check(`${name}: no sid key`, /["'\s{,]sid["']?\s*:/.test(text), false);
		check(`${name}: tabs, not spaces`, /^ {2,}\S/m.test(text.replace(/`[\s\S]*?`/g, "")), false);
	}

	console.log("");
	if (failures) {
		console.error(failures + " assertion(s) failed");
		process.exit(1);
	}
	console.log("capture panel: all assertions passed");
})();
