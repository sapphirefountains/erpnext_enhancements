#!/usr/bin/env node
/**
 * The Stock Scan page's pure client logic, executed — not grepped.
 *
 * `/stock-scan` is a phone page that posts submitted stock vouchers, one tap per save. The
 * decisions it makes before it asks the server anything live in two plain ES modules with no
 * DOM, `public/js/stock_scan/logic.js` and `public/js/stock_scan/transport.js`, and this runs
 * them directly. Plain node, no runner and no npm install, the shape of the repo's other JS
 * guards (`scripts/test_marketing_client.js`).
 *
 * The assertions worth the most:
 *
 *   - **The page's twin of the label parser stays identical to the server's.** The server's
 *     `stock_scan_rules.parse_scan` decides every scan; the page's `parseScan` only names the
 *     label in the error it shows when the server could not answer (`scanFailure`). It runs
 *     over `erpnext_enhancements/tests/data/stock_scan_parse_vectors.json`, the same file
 *     `tests/test_stock_scan_rules.py` runs `parse_scan` over, so that name is always the one
 *     the server would have looked up.
 *   - **A signed-out phone is told to reload.** Frappe v16 runs an expired session as Guest
 *     and answers a 403 "… is not whitelisted" (`session_expired: 1` on the first response
 *     only). `isSignedOut` / `call()` turn that into a 401 `signedOut` error, so the page
 *     offers Reload instead of a Try again that can never work.
 *   - **A kept reference expires.** `keptRef` reuses a save's `client_ref` only inside
 *     `RETRY_WINDOW_MS`; the server keeps every reference forever, so an identical save an
 *     hour later must not be answered "already saved" and posted nowhere.
 *   - **A remembered job is the picker's own.** `rememberedJob` hands the phone's job back only
 *     to the user who picked it.
 *   - **A retried save carries a reference the server accepts.** `mintRef()` is checked against
 *     the server's own `_CLIENT_REF` pattern, read out of `api/stock_scan.py` here so the two
 *     cannot drift. A ref the server refuses is a save that fails every time, on every phone.
 *   - **An error is a sentence.** `errorMessage` picks the LAST message Frappe raised (a
 *     unique-key collision queues Frappe's own message before ours), strips ERPNext's HTML
 *     ("Insufficient Stock" carries links), reads list-valued messages, and turns a CSRF
 *     failure — whose server text is just "Invalid Request" — into "reload the page".
 *   - **A dropped connection is retryable, a refusal is not.** `call()` over a stubbed `fetch`:
 *     a network error or a timeout is `StockScanCallError` with status 0 and `retryable`, a 417
 *     is not, and the CSRF token is read from the boot when the call is made, not at import.
 *
 * Loads the modules by file URL. If one grows a DOM or `window` access at import time this
 * fails loudly (exit 2) rather than asserting nothing.
 *
 * Run: node scripts/test_stock_scan_client.mjs
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.join(HERE, "..", "erpnext_enhancements");
const CLIENT = path.join(APP, "public", "js", "stock_scan");
const VECTORS = path.join(APP, "tests", "data", "stock_scan_parse_vectors.json");
const API = path.join(APP, "api", "stock_scan.py");

let failures = 0;
let passes = 0;

function same(a, b) {
	// -0 and 0 are the same quantity; JSON.stringify already says so, and so does this.
	return JSON.stringify(a) === JSON.stringify(b);
}

function check(label, actual, expected) {
	if (same(actual, expected)) {
		passes += 1;
		console.log(`  ok   ${label}`);
	} else {
		failures += 1;
		console.error(`  FAIL ${label}: got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`);
	}
}

function truthy(label, value, detail) {
	if (value) {
		passes += 1;
		console.log(`  ok   ${label}`);
	} else {
		failures += 1;
		console.error(`  FAIL ${label}${detail ? `: ${detail}` : ""}`);
	}
}

function near(label, actual, expected) {
	truthy(label, typeof actual === "number" && Math.abs(actual - expected) < 1e-9, `got ${actual}, want ${expected}`);
}

async function load(name) {
	const file = path.join(CLIENT, name);
	if (!fs.existsSync(file)) {
		console.error(`MISSING ${path.relative(path.join(APP, ".."), file)}: the front end has not written it yet.`);
		process.exit(2);
	}
	try {
		return await import(pathToFileURL(file).href);
	} catch (err) {
		console.error(`COULD NOT LOAD ${name}: it must import without a DOM, window or fetch.`);
		console.error(err && err.stack ? err.stack : err);
		process.exit(2);
	}
}

function exported(mod, file, names) {
	const missing = names.filter((name) => !(name in mod));
	if (missing.length) {
		console.error(`${file} does not export ${missing.join(", ")} (the build spec names them).`);
		process.exit(2);
	}
}

/** Frappe's `_server_messages`: a JSON string of a list of JSON strings. */
function serverMessages(...messages) {
	return JSON.stringify(messages.map((m) => JSON.stringify(m)));
}

function withTimeout(promise, ms, label) {
	let timer;
	const limit = new Promise((resolve) => {
		timer = setTimeout(() => resolve({ timedOut: true, label }), ms);
	});
	return Promise.race([promise, limit]).finally(() => clearTimeout(timer));
}

(async () => {
	if (typeof globalThis.window !== "undefined") {
		console.error("a window global exists before the modules load; this test must load them without one");
		process.exit(2);
	}
	const L = await load("logic.js");
	const T = await load("transport.js");
	exported(L, "logic.js", [
		"parseScan", "plain", "clampChange", "describeChange", "mintRef", "CLIENT_REF_RE", "isSafeImage", "jobExpired",
		// Not in the spec's list, but app.js's retry rule is these; they are tested below.
		"mayHaveSaved", "saveKey", "keptRef", "RETRY_WINDOW_MS",
		// The review fixes: the offline scan error, the per-user job, the moved-on save message.
		"scanFailure", "rememberedJob", "saveLabel",
	]);
	exported(T, "transport.js", ["M", "call", "StockScanCallError", "errorMessage", "isSignedOut", "SIGNED_OUT"]);

	console.log("stock scan client\n");

	// ------------------------------------------------------------------ parseScan
	const vectors = JSON.parse(fs.readFileSync(VECTORS, "utf8"));
	truthy(`the shared vectors are substantial (${vectors.length})`, vectors.length >= 15);
	let parseOk = 0;
	for (const v of vectors) {
		const got = L.parseScan(v.raw);
		const want = { kind: v.kind, value: v.value };
		const pair = got && { kind: got.kind, value: got.value };
		if (same(pair, want)) parseOk += 1;
		else check(`parseScan(${JSON.stringify(v.raw)}) — ${v.why || ""}`, pair, want);
	}
	check(`parseScan agrees with stock_scan_rules.parse_scan on every vector`, parseOk, vectors.length);

	// ------------------------------------------------------------------ scanFailure
	// The one use the page makes of parseScan: naming the label when the server never answered.
	const why = "Could not reach the server. Check your signal and try again.";
	check(
		"scanFailure: a location label is named by its location",
		L.scanFailure("https://erp.sapphirefountains.com/stock-scan?w=Bin%20B1-2-10%20-%20SF", why),
		`Couldn't open Bin B1-2-10 - SF: ${why}`,
	);
	check(
		"scanFailure: + in a label decodes to a space, as on the server",
		L.scanFailure("https://erp.example.com/stock-scan/?w=Stores+-+SF", why),
		`Couldn't open Stores - SF: ${why}`,
	);
	check("scanFailure: an item label names the item", L.scanFailure("/stock-scan?item=PDT-0008", why), `Couldn't open PDT-0008: ${why}`);
	check("scanFailure: a typed code is quoted", L.scanFailure("  pump seal ", why), `Couldn't look up "pump seal": ${why}`);
	check(
		"scanFailure: another site's URL is text, never a location",
		L.scanFailure("https://evil.example.com/stock-scan-not?w=X", why),
		`Couldn't look up "https://evil.example.com/stock-scan-not?w=X": ${why}`,
	);
	check("scanFailure: a blank scan is just the reason", L.scanFailure("   ", why), why);
	const long = L.scanFailure("x".repeat(200), why);
	truthy("scanFailure: a very long code is cut to 60 characters", long.includes(`"${"x".repeat(59)}…"`) && !long.includes("x".repeat(60)), long);
	truthy("scanFailure: no reason is still a sentence", /\w/.test(L.scanFailure("Bin A1", "")) && !/undefined|null/.test(L.scanFailure("Bin A1", undefined)));

	// ------------------------------------------------------------------ plain
	// Expected values are Python's stock_scan_rules.plain on the same inputs.
	const plainCases = [
		[3, "3"],
		[3.0, "3"],
		[2.5, "2.5"],
		[0.1 + 0.2, "0.3"],
		[1 / 3, "0.333333"],
		[2.0000001, "2"],
		[12.125, "12.125"],
		[-3, "-3"],
		[-2.5, "-2.5"],
		[0, "0"],
		[100000, "100000"],
		[1234567.5, "1234567.5"],
	];
	for (const [value, want] of plainCases) check(`plain(${value})`, L.plain(value), want);

	// ------------------------------------------------------------------ clampChange
	check("clampChange: a take stops at what is available", L.clampChange(-5, 3), -3);
	check("clampChange: a take within stock is kept", L.clampChange(-3, 3), -3);
	check("clampChange: fractional stock bounds a take", L.clampChange(-10, 2.5), -2.5);
	check("clampChange: nothing available means nothing to take", L.clampChange(-1, 0), 0);
	check("clampChange: negative stock is not a license to take", L.clampChange(-4, -2), 0);
	check("clampChange: an add is not bounded by stock", L.clampChange(7, 5), 7);
	check("clampChange: an add stops at the per-save maximum", L.clampChange(200000, 5), 100000);
	check("clampChange: the maximum is 100000 by default", L.clampChange(100000, 0), 100000);
	check("clampChange: a custom maximum", L.clampChange(9, 5, 4), 4);
	check("clampChange: zero stays zero", L.clampChange(0, 12), 0);

	// ------------------------------------------------------------------ describeChange
	const take = L.describeChange(-3, "Unit", 12);
	check("describeChange: a take", [take.verb, take.label], ["take", "Take 3"]);
	near("describeChange: a take leaves on hand minus the change", take.after, 9);
	const add = L.describeChange(5, "Unit", 12);
	check("describeChange: an add", [add.verb, add.label], ["add", "Add 5"]);
	near("describeChange: an add raises on hand", add.after, 17);
	const none = L.describeChange(0, "Unit", 12);
	check("describeChange: no change is a disabled Save", [none.verb, none.label], ["none", "Save"]);
	const half = L.describeChange(-2.5, "Unit", 10);
	check("describeChange: decimals go through plain()", half.label, "Take 2.5");
	near("describeChange: and so does the arithmetic", half.after, 7.5);
	check("describeChange: float noise is not shown", L.describeChange(0.1 + 0.2, "Unit", 1).label, "Add 0.3");
	check("describeChange: no location means no 'after'", L.describeChange(4, "Unit", null).after, null);

	// ------------------------------------------------------------------ mintRef
	const source = fs.readFileSync(API, "utf8");
	const serverPattern = (source.match(/_CLIENT_REF\s*=\s*re\.compile\(r"([^"]+)"\)/) || [])[1];
	truthy("the server's _CLIENT_REF pattern was found in api/stock_scan.py", Boolean(serverPattern));
	const SERVER = new RegExp(serverPattern || "^(?!)$");
	truthy("CLIENT_REF_RE is a RegExp", L.CLIENT_REF_RE instanceof RegExp);
	const probes = [
		"ss-abc", "ss-12345", "a".repeat(7), "a".repeat(8), "a".repeat(80), "a".repeat(81),
		"ss-abc def", "ss-abc/def", "ss-abc_def", "ss.abc:de-f", "", "ss-é1234567", "SS-ABC.123:x-y",
		"ss-kf3z9a1-8qz0x4m2", "ss-<script>", "ss-abc;drop",
	];
	const disagree = probes.filter((p) => L.CLIENT_REF_RE.test(p) !== SERVER.test(p));
	check("CLIENT_REF_RE accepts exactly what the server's pattern accepts", disagree, []);
	const refs = new Set();
	let refsOk = true;
	for (let i = 0; i < 2000; i += 1) {
		const ref = L.mintRef();
		if (typeof ref !== "string" || !SERVER.test(ref) || !L.CLIENT_REF_RE.test(ref) || !ref.startsWith("ss-")) {
			refsOk = false;
			check("a minted ref the server accepts", ref, "ss-<time>-<random> matching " + serverPattern);
			break;
		}
		refs.add(ref);
	}
	truthy("2000 minted refs all match the server pattern and start ss-", refsOk);
	check("and no two are the same", refs.size, 2000);

	// ------------------------------------------------------------------ isSafeImage
	const images = [
		["/files/pump.jpg", true],
		["/private/files/pump.jpg", true],
		["/assets/erpnext/images/placeholder.png", true],
		["https://cdn.example.com/pump.jpg", true],
		["http://cdn.example.com/pump.jpg", false],
		["//evil.example.com/pixel.gif", false],
		["/\\evil.example.com/pixel.gif", false],
		["javascript:alert(1)", false],
		["data:image/png;base64,iVBORw0KGgo=", false],
		["files/pump.jpg", false],
		["", false],
		[null, false],
		[undefined, false],
	];
	for (const [src, want] of images) check(`isSafeImage(${JSON.stringify(src)})`, Boolean(L.isSafeImage(src)), want);

	// ------------------------------------------------------------------ jobExpired
	const HOUR = 60 * 60 * 1000;
	const now = Date.UTC(2026, 8, 23, 15, 0, 0);
	check("jobExpired: picked this morning", Boolean(L.jobExpired(now - 2 * HOUR, now)), false);
	check("jobExpired: picked a minute under 12 h ago", Boolean(L.jobExpired(now - 12 * HOUR + 60000, now)), false);
	check("jobExpired: picked yesterday", Boolean(L.jobExpired(now - 12 * HOUR - 60000, now)), true);
	check("jobExpired: picked last week", Boolean(L.jobExpired(now - 7 * 24 * HOUR, now)), true);
	check("jobExpired: no saved time is expired, not forever", Boolean(L.jobExpired(undefined, now)), true);

	// ------------------------------------------------------------------ rememberedJob
	// The phone remembers the job for the person who picked it, not for whoever holds it next.
	const tina = "tina@sapphirefountains.com";
	const job = { project: "PRJ-00598", project_name: "City Hall Plaza Fountain", customer: "City of Riverton", saved_at: now - HOUR, user: tina };
	check("rememberedJob: the picker gets their job back", L.rememberedJob(job, tina, now), job);
	check("rememberedJob: the next person on the phone does not", L.rememberedJob(job, "sam@sapphirefountains.com", now), null);
	check("rememberedJob: a job stored before jobs had an owner is nobody's", L.rememberedJob({ ...job, user: undefined }, tina, now), null);
	check("rememberedJob: no signed-in user, no job", L.rememberedJob(job, "", now), null);
	check("rememberedJob: a lapsed job is gone even for its picker", L.rememberedJob({ ...job, saved_at: now - 13 * HOUR }, tina, now), null);
	check("rememberedJob: garbage is no job", [L.rememberedJob(null, tina, now), L.rememberedJob("PRJ-1", tina, now), L.rememberedJob({ user: tina, saved_at: now }, tina, now)], [null, null, null]);

	// ------------------------------------------------------------------ the client_ref reuse rule
	// app.js keeps a save's client_ref for the next tap when mayHaveSaved(status) — the server
	// then hands back the first save instead of posting a second — and mints a fresh one when
	// saveKey() changes, i.e. when the person changed what the save would post.
	for (const status of [0, 409, 502, 503, 504]) {
		check(`mayHaveSaved(${status}): the save may have posted, so the retry keeps its ref`, Boolean(L.mayHaveSaved(status)), true);
	}
	for (const status of [400, 401, 403, 404, 417, 429, 500]) {
		check(`mayHaveSaved(${status}): refused and rolled back, so the next tap is a new save`, Boolean(L.mayHaveSaved(status)), false);
	}
	const base = { action: "take", item_code: "PDT-0008", warehouse: "Bin A1-3-1 - SF", qty: 3, project: "PRJ-00577" };
	check("saveKey: the same save is the same key", L.saveKey({ ...base }), L.saveKey({ ...base }));
	check("saveKey: float noise in the quantity is the same save", L.saveKey({ ...base, qty: 3.0000000001 }), L.saveKey(base));
	for (const [label, change] of [
		["the quantity", { qty: 4 }],
		["the item", { item_code: "PDT-0009" }],
		["the location", { warehouse: "Bin A1-3-2 - SF" }],
		["the source of a move", { from_warehouse: "Stores - SF" }],
		["the order line", { purchase_order_item: "abc123" }],
		["without a purchase order", { without_po: 1 }],
		["the job", { project: "PRJ-00578" }],
		["the direction", { action: "add" }],
	]) {
		truthy(`saveKey: changing ${label} is a different save`, L.saveKey({ ...base, ...change }) !== L.saveKey(base));
	}
	// A return from a job and found stock are two different adds (the job is on one only).
	const addBase = { action: "add", item_code: "PDT-0008", warehouse: "Bin A1-3-1 - SF", qty: 2, without_po: 1 };
	truthy("saveKey: 'Returned from <job>' and 'Not on a purchase order' are different saves", L.saveKey({ ...addBase, project: "PRJ-00598" }) !== L.saveKey(addBase));

	// ------------------------------------------------------------------ keptRef: the retry window
	// A kept ref is reused only inside RETRY_WINDOW_MS. The server's _already_saved has no age
	// limit, so a ref kept forever turns a new identical save into "already saved", posted nowhere.
	const MIN = 60 * 1000;
	check("RETRY_WINDOW_MS is ten minutes", L.RETRY_WINDOW_MS, 10 * MIN);
	const kept = { ref: "ss-kf3z9a1-8qz0x4m2ab", at: now };
	check("keptRef: a retry right away reuses the ref", L.keptRef(kept, now + 5000), kept.ref);
	check("keptRef: a retry at the edge of the window reuses it", L.keptRef(kept, now + 10 * MIN), kept.ref);
	check("keptRef: past the window it is a new save", L.keptRef(kept, now + 10 * MIN + 1), null);
	check("keptRef: an hour later it is certainly a new save", L.keptRef(kept, now + 60 * MIN), null);
	check("keptRef: a stamp well in the future (clock changed) is not trusted", L.keptRef({ ...kept, at: now + 30 * MIN }, now), null);
	check("keptRef: a bare string from the old shape is not trusted", L.keptRef(kept.ref, now), null);
	check("keptRef: nothing kept, nothing reused", [L.keptRef(undefined, now), L.keptRef(null, now), L.keptRef({ ref: kept.ref }, now)], [null, null, null]);
	check("keptRef: a ref the server would refuse is not reused", L.keptRef({ ref: "bad ref", at: now }, now), null);

	// ------------------------------------------------------------------ words for a moved-on save
	check("saveLabel: a take", L.saveLabel("take", 3, "Each", "Pump Seal Kit 3/4 in"), "Take 3 Each of Pump Seal Kit 3/4 in");
	check("saveLabel: a move keeps decimals plain", L.saveLabel("move", 7.5, "Foot", "PVC Pipe"), "Move 7.5 Foot of PVC Pipe");
	check("saveLabel: an add", L.saveLabel("add", 5, "", "Widget"), "Add 5 of Widget");
	const returned = { action: "Add Without PO", qty: 2, stock_uom: "Each", item_name: "Widget", warehouse_name: "Bin A1-3-1", project: "PRJ-00598" };
	check("logHeadline: an add with a job reads as a return", L.logHeadline(returned), "Returned 2 Each");
	check("logHeadline: an add without one reads as an add", L.logHeadline({ ...returned, project: null }), "Added 2 Each");
	check("undoQuestion: a return names the job", L.undoQuestion(returned), "Undo: returned 2 Each of Widget to Bin A1-3-1 from PRJ-00598?");
	check("undoQuestion: found stock does not", L.undoQuestion({ ...returned, project: null }), "Undo: added 2 Each of Widget to Bin A1-3-1?");

	// ------------------------------------------------------------------ M
	check("M carries exactly the eleven endpoints", Object.keys(T.M).sort(), [
		"ADD", "ITEM", "LOCATION", "MOVE", "RECENT", "RESOLVE", "SEARCH_ITEMS", "SEARCH_LOCATIONS",
		"SEARCH_PROJECTS", "TAKE", "UNDO",
	]);
	truthy(
		"every M value is a method of api/stock_scan.py",
		Object.values(T.M).every((m) => typeof m === "string" && m.startsWith("erpnext_enhancements.api.stock_scan.")),
	);

	// ------------------------------------------------------------------ errorMessage
	const ours = { message: "Only 3 on hand at Bin A1-3-1, so 4 cannot be taken.", title: "Message", indicator: "red", raise_exception: 1 };
	const theirs = { message: "Duplicate entry for Client Reference", title: "Message", indicator: "red", raise_exception: 1 };
	const info = { message: "Stock Entry STE-0001 created", title: "Message", indicator: "green" };
	check(
		"errorMessage: the LAST raised message wins (Frappe's own collision message comes first)",
		T.errorMessage({ exc_type: "ValidationError", _server_messages: serverMessages(theirs, ours) }, 417),
		ours.message,
	);
	check(
		"errorMessage: a raised message beats a later informational one",
		T.errorMessage({ exc_type: "ValidationError", _server_messages: serverMessages(ours, info) }, 417),
		ours.message,
	);
	check(
		"errorMessage: with nothing raised, the last message",
		T.errorMessage({ _server_messages: serverMessages({ message: "first" }, { message: "second" }) }, 417),
		"second",
	);
	const insufficient = T.errorMessage(
		{
			exc_type: "NegativeStockError",
			_server_messages: serverMessages({
				message: '1 units of <a href="/app/item/PDT-0008">Widget</a> needed in <strong>Bin A1-3-1 - SF</strong> to complete this transaction.',
				title: "Insufficient Stock",
				raise_exception: 1,
			}),
		},
		417,
	);
	truthy("errorMessage: ERPNext's HTML is stripped", !/[<>]/.test(insufficient), insufficient);
	truthy(
		"errorMessage: and the words survive",
		/Widget/.test(insufficient) && /Bin A1-3-1 - SF/.test(insufficient) && /needed/.test(insufficient),
		insufficient,
	);
	const listed = T.errorMessage(
		{ _server_messages: serverMessages({ message: ["Row 1: qty is required", "Row 2: warehouse is required"], raise_exception: 1, as_list: 1 }) },
		417,
	);
	truthy("errorMessage: a list-valued message is read", /Row 1: qty is required/.test(listed) && /Row 2: warehouse is required/.test(listed), listed);
	const denied = T.errorMessage(
		{ exc_type: "PermissionError", _server_messages: serverMessages({ message: "You are not permitted to use Stock Scan.", raise_exception: 1 }) },
		403,
	);
	check("errorMessage: a 403 with the server's sentence shows that sentence", denied, "You are not permitted to use Stock Scan.");

	const csrfBare = T.errorMessage({ exc_type: "CSRFTokenError" }, 400);
	truthy("errorMessage: a CSRF failure says to reload", /reload/i.test(csrfBare), csrfBare);
	// Frappe v16's validate_csrf_token raises frappe.throw(_("Invalid Request"), CSRFTokenError),
	// so the real payload carries that message; "Invalid Request" tells a technician nothing.
	const csrfReal = T.errorMessage(
		{ exc_type: "CSRFTokenError", _server_messages: serverMessages({ message: "Invalid Request", title: "Message", raise_exception: 1 }) },
		400,
	);
	truthy("errorMessage: a CSRF failure says to reload even when the server says 'Invalid Request'", /reload/i.test(csrfReal), csrfReal);

	// A signed-out session on Frappe v16, exactly as the server builds it: sessions.py runs the
	// request as Guest (session_expired: 1 on the first response only), validate_csrf_token
	// skips Guest, and is_whitelisted throws this PermissionError (HTTP 403) for every method here.
	const notWhitelisted = {
		message:
			"<details><summary>You are not permitted to access this resource. Login to access</summary>Function <strong>erpnext_enhancements.api.stock_scan.get_item</strong> is not whitelisted.</details>",
		title: "Method Not Allowed",
		indicator: "red",
		raise_exception: 1,
	};
	const guestLater = { exc_type: "PermissionError", _server_messages: serverMessages(notWhitelisted) };
	const guestFirst = { ...guestLater, session_expired: 1 };
	check("isSignedOut: the first response after expiry (session_expired: 1)", T.isSignedOut(guestFirst, 403), true);
	check("isSignedOut: every later one (403 'not whitelisted', cookie already gone)", T.isSignedOut(guestLater, 403), true);
	check("isSignedOut: a 401", T.isSignedOut({}, 401), true);
	check("isSignedOut: a real permission refusal is not a sign-out", T.isSignedOut({ exc_type: "PermissionError", _server_messages: serverMessages({ message: "You are not permitted to use Stock Scan.", raise_exception: 1 }) }, 403), false);
	check("isSignedOut: 'not whitelisted' only counts on a 403 PermissionError", T.isSignedOut({ exc_type: "ValidationError", _server_messages: serverMessages(notWhitelisted) }, 417), false);
	check("isSignedOut: a bare 403 is not a sign-out", T.isSignedOut({}, 403), false);
	check("isSignedOut: no payload at all", [T.isSignedOut(null, 0), T.isSignedOut(null, 500)], [false, false]);
	check("errorMessage: the Guest 403 says to sign in again, not 'not whitelisted'", T.errorMessage(guestLater, 403), T.SIGNED_OUT);
	check("errorMessage: so does the first response, flagged session_expired", T.errorMessage(guestFirst, 403), T.SIGNED_OUT);
	truthy("SIGNED_OUT says to reload", /reload/i.test(T.SIGNED_OUT) && /sign/i.test(T.SIGNED_OUT), T.SIGNED_OUT);

	const generic = T.errorMessage({}, 500);
	const statusCases = [
		[401, /sign|log|session|access|permi|allow/i],
		[403, /sign|log|session|access|permi|allow/i],
		[429, /too many|wait|moment|again|slow/i],
	];
	for (const [status, pattern] of statusCases) {
		const text = T.errorMessage({}, status);
		truthy(
			`errorMessage: status ${status} with no message is a sentence of its own`,
			typeof text === "string" && text.trim() && pattern.test(text) && !/undefined|\[object/.test(text) && text !== generic,
			JSON.stringify(text),
		);
	}
	// Status 0 never reaches the server, so call() words it itself (checked below); here it
	// only has to be a sentence, with no payload at all.
	const noAnswer = T.errorMessage(null, 0);
	truthy(
		"errorMessage: status 0 with no payload is a sentence",
		typeof noAnswer === "string" && noAnswer.trim().length > 0 && !/undefined|\[object|null/.test(noAnswer),
		JSON.stringify(noAnswer),
	);
	const garbage = T.errorMessage({ _server_messages: "not json" }, 417);
	truthy("errorMessage: a malformed _server_messages does not throw", typeof garbage === "string" && garbage.trim().length > 0, JSON.stringify(garbage));
	truthy("errorMessage: a bare 500 is still a sentence", typeof generic === "string" && generic.trim().length > 0 && !/undefined|\[object/.test(generic), JSON.stringify(generic));

	// ------------------------------------------------------------------ StockScanCallError
	const offline = new T.StockScanCallError("Could not reach the server.", 0);
	truthy("StockScanCallError is an Error", offline instanceof Error);
	check("status 0 is retryable", [offline.status, offline.retryable], [0, true]);
	const refused = new T.StockScanCallError("Only 3 on hand.", 417, { exc_type: "NegativeStockError" });
	check("a refusal is not retryable", [refused.status, refused.retryable], [417, false]);
	check("the exception type rides along", refused.excType, "NegativeStockError");

	// ------------------------------------------------------------------ call() over a stubbed fetch
	const realFetch = globalThis.fetch;
	const seen = [];
	globalThis.window = { EE_STOCK_SCAN_BOOT: { csrf_token: "tok-123" } };
	try {
		globalThis.fetch = async (url, init) => {
			seen.push({ url, init });
			return new Response(JSON.stringify({ message: { kind: "location" } }), {
				status: 200,
				headers: { "Content-Type": "application/json" },
			});
		};
		const args = { code: "Stores - SF", warehouse: null };
		const ok = await T.call(T.M.RESOLVE, args, { timeout: 2000 });
		check("call(): unwraps .message", ok, { kind: "location" });
		const sent = seen[0] || { init: {} };
		check("call(): POSTs to /api/method/<method>", [sent.url, sent.init.method], [`/api/method/${T.M.RESOLVE}`, "POST"]);
		const headers = new Headers(sent.init.headers || {});
		check("call(): sends the CSRF token read from the boot at call time", headers.get("X-Frappe-CSRF-Token"), "tok-123");
		check("call(): sends JSON", (headers.get("Content-Type") || "").split(";")[0], "application/json");
		check("call(): with the session cookie", sent.init.credentials, "same-origin");
		let body = null;
		try {
			body = JSON.parse(sent.init.body);
		} catch (e) {
			body = sent.init.body;
		}
		check("call(): the args are the JSON body", body, args);

		globalThis.fetch = async () => {
			throw new TypeError("Failed to fetch");
		};
		const dropped = await T.call(T.M.TAKE, { item_code: "X" }, { timeout: 2000 }).then(
			() => null,
			(e) => e,
		);
		truthy("call(): a network failure throws StockScanCallError", dropped instanceof T.StockScanCallError, String(dropped));
		check("call(): ... with status 0, retryable", dropped && [dropped.status, dropped.retryable], [0, true]);
		truthy(
			"call(): ... saying the connection is the problem",
			dropped && /connect|reach|network|signal|offline|internet/i.test(dropped.message),
			dropped && dropped.message,
		);

		globalThis.fetch = async () =>
			new Response(
				JSON.stringify({ exc_type: "NegativeStockError", _server_messages: serverMessages(ours) }),
				{ status: 417, headers: { "Content-Type": "application/json" } },
			);
		const failed = await T.call(T.M.TAKE, { item_code: "X" }, { timeout: 2000 }).then(
			() => null,
			(e) => e,
		);
		truthy("call(): a refusal throws StockScanCallError", failed instanceof T.StockScanCallError, String(failed));
		check(
			"call(): ... carrying the server's sentence, its status and type, not retryable",
			failed && [failed.message, failed.status, failed.excType, failed.retryable],
			[ours.message, 417, "NegativeStockError", false],
		);
		check("call(): ... and a refusal is not a sign-out", failed && [failed.signedOut, failed.needsReload], [false, false]);

		for (const [label, body] of [
			["the first response after the session expired", guestFirst],
			["a later one, the cookie already cleared", guestLater],
		]) {
			globalThis.fetch = async () => new Response(JSON.stringify(body), { status: 403, headers: { "Content-Type": "application/json" } });
			const out = await T.call(T.M.ITEM,{ item_code: "X" }, { timeout: 2000 }).then(
				() => null,
				(e) => e,
			);
			check(
				`call(): ${label} (403 'not whitelisted') is a 401 signedOut error saying to reload`,
				out && [out instanceof T.StockScanCallError, out.status, out.signedOut, out.needsReload, out.retryable, out.message],
				[true, 401, true, true, false, T.SIGNED_OUT],
			);
			check(`call(): ... and a save refused that way is not kept for a retry`, Boolean(out && L.mayHaveSaved(out.status)), false);
		}

		globalThis.fetch = async () =>
			new Response(JSON.stringify({ exc_type: "CSRFTokenError", _server_messages: serverMessages({ message: "Invalid Request", raise_exception: 1 }) }), {
				status: 400,
				headers: { "Content-Type": "application/json" },
			});
		const stale = await T.call(T.M.TAKE, { item_code: "X" }, { timeout: 2000 }).then(
			() => null,
			(e) => e,
		);
		check("call(): a stale CSRF token needs a reload but is not a sign-out", stale && [stale.status, stale.needsReload, stale.signedOut], [400, true, false]);

		globalThis.fetch = (url, init) =>
			new Promise((resolve, reject) => {
				const signal = init && init.signal;
				if (signal) signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
			});
		const slow = await withTimeout(
			T.call(T.M.TAKE, { item_code: "X" }, { timeout: 30 }).then(
				() => null,
				(e) => e,
			),
			3000,
			"call() never gave up",
		);
		truthy("call(): honors its timeout (aborts the fetch)", slow && !slow.timedOut, slow && slow.label);
		check(
			"call(): a timeout is StockScanCallError status 0, retryable with the same client_ref",
			slow && !slow.timedOut ? [slow instanceof T.StockScanCallError, slow.status, slow.retryable] : null,
			[true, 0, true],
		);
		truthy(
			"call(): ... and says so in words",
			slow && !slow.timedOut && typeof slow.message === "string" && slow.message.trim().length > 0 && !/abort/i.test(slow.message),
			slow && slow.message,
		);
	} finally {
		globalThis.fetch = realFetch;
		delete globalThis.window;
	}

	console.log(`\n${passes} passed, ${failures} failed`);
	process.exit(failures ? 1 : 0);
})().catch((err) => {
	console.error(err && err.stack ? err.stack : err);
	process.exit(2);
});
