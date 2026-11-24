#!/usr/bin/env node
/**
 * The QuickBooks Record Matching page's Excel Apply run, executed rather than grepped (v1.841.1).
 *
 * On 2026-11-24 an import stopped at row 30 of 532: a Link that merged a duplicate ran rename_doc,
 * which ends with frappe.clear_cache() and took the preview's plan with it, so every batch after
 * it came back 417 "This preview has expired", and no "Import finished" box ever appeared. The
 * server now keeps the plan (tests/test_quickbooks_matching_excel.py). This file holds the page's
 * half:
 *
 * - every batch goes out silent, with the uploaded file's name and each row's sheet, row number
 *   and QBO keys beside its index;
 * - a batch that comes back expired (exc_type MatchingPreviewExpired, never the wording) gets the
 *   same file previewed again, and is retried once under the fresh preview's numbers, found by the
 *   row's place in the file. A server that checks those identities, as the real one does, would
 *   skip a row sent under the wrong number, so a wrong remap fails here;
 * - if the fresh preview fails, or the retry is expired too, the run stops and every row not
 *   sent is reported as such: no loop, no further requests;
 * - an ordinary failed batch (a 500, an answer that is not a list, a call that throws) is
 *   reported against its rows and the run carries on;
 * - whatever happens, the run ends in an "Import finished" Dialog of the page's own, never
 *   frappe.msgprint's shared dialog, and the screen is unfrozen.
 *
 * The page script runs the way the Desk loads it: inside `new Function`, with a stub `frappe`, `__`
 * and a lenient `$`, then on_page_load. The test clicks through the real flow: the Import from Excel
 * button, the uploader's on_success, the preview dialog's Apply, the confirm. A small fake server
 * answers preview_matching_import and apply_matching_import the way matching_excel does.
 *
 * Run: node scripts/test_qbo_matching_import.mjs
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PAGE_JS = path.join(
	HERE,
	"..",
	"erpnext_enhancements",
	"quickbooks_online",
	"page",
	"quickbooks_record_matching",
	"quickbooks_record_matching.js"
);
const SOURCE = fs.readFileSync(PAGE_JS, "utf8");
const EXPIRED = "MatchingPreviewExpired";
const API = "erpnext_enhancements.quickbooks_online.core.api.";

const tests = [];
function test(name, fn) {
	tests.push([name, fn]);
}

const translate = (text, args) =>
	String(text).replace(/\{(\d+)\}/g, (match, i) => (args && args[i] !== undefined ? String(args[i]) : match));

/** A jQuery object, page or control that accepts anything: every property is callable and
 * returns the same stand-in, it reads as "" in a string and has no length. */
function anything() {
	const proxy = new Proxy(function () {}, {
		get(_target, key) {
			if (key === Symbol.toPrimitive) return () => "";
			if (key === "then") return undefined;
			if (key === "length") return 0;
			return proxy;
		},
		apply: () => proxy,
		construct: () => proxy,
	});
	return proxy;
}

/** A real object whose missing members are `anything()`. */
function lenient(object) {
	return new Proxy(object, {
		get(target, key) {
			if (key in target || typeof key === "symbol") return target[key];
			return anything();
		},
	});
}

/** A 417 the way jQuery rejects frappe.call's promise with it. */
function rejection(exc_type, message) {
	return {
		status: 417,
		responseJSON: {
			exc_type,
			_server_messages: JSON.stringify([JSON.stringify({ message: `<b>${message}</b>`, indicator: "red" })]),
		},
	};
}

/**
 * A stand-in for matching_excel's preview/apply. `file` is the parsed file: [{sheet, row_number,
 * qbo_type, qbo_id, decision}]. A preview lists every row with its index and a fresh token. apply
 * checks each identity against the row under that index (matching_excel.same_row) and refuses a
 * token it no longer holds. `onApply(server, row)` lets a test wipe the plans, as a merge did.
 */
class FakeServer {
	constructor(file, { shift = 0 } = {}) {
		this.file = file;
		this.plans = new Map();
		this.applied = [];
		this.previews = 0;
		this.previewAttempts = 0;
		this.shiftEachPreview = shift;
		this.onApply = () => {};
		this.previewFails = false;
		this.applyScript = [];
	}

	preview() {
		this.previewAttempts += 1;
		if (this.previewFails) {
			return Promise.reject(rejection("DoesNotExistError", "File FILE-1 not found"));
		}
		// Each preview may number the rows differently (a parser changed in a deploy): a leading
		// row the file did not have before pushes every index along.
		const offset = this.previews * this.shiftEachPreview;
		this.previews += 1;
		const token = `t${this.previews}-digest`;
		const rows = this.file.map((row, i) => ({
			...row,
			index: i + offset,
			category: this.applied.includes(row.row_number) ? "unchanged" : "change",
			message: "",
			warnings: [],
		}));
		this.plans.set(token, new Map(rows.map((row) => [row.index, row])));
		return Promise.resolve({ message: { token, file: "FILE-1", rows, summary: {}, errors: [], apply_chunk: 10 } });
	}

	apply(args) {
		const scripted = this.applyScript.shift();
		if (scripted) return scripted();
		const plan = this.plans.get(args.token);
		if (!plan) {
			return Promise.reject(rejection(EXPIRED, "This preview has expired. Import the file again to get a fresh preview."));
		}
		const out = [];
		for (const entry of JSON.parse(JSON.stringify(args.indexes))) {
			const row = plan.get(entry.index);
			const same = row && ["sheet", "row_number", "qbo_type", "qbo_id"].every((key) => String(row[key]) === String(entry[key]));
			if (!same) {
				out.push({ ...entry, ok: false, skipped: true, category: "invalid", message: "different row" });
				continue;
			}
			if (this.applied.includes(row.row_number)) {
				out.push({ ...entry, ok: false, skipped: true, category: "unchanged", message: "Already done" });
				continue;
			}
			this.applied.push(row.row_number);
			out.push({ ...entry, ok: true, merge_failed: false, message: `${row.decision} done.` });
			this.onApply(this, row);
		}
		return Promise.resolve({ message: out });
	}
}

function makeFile(count, { retryAt = [] } = {}) {
	return Array.from({ length: count }, (_, i) => ({
		sheet: "Vendors",
		row_number: i + 2,
		qbo_type: "Vendor",
		qbo_id: String(100 + i),
		decision: retryAt.includes(i) ? "Retry" : "Link",
	}));
}

/** Load the page script as the Desk does and open it; returns the handles a test drives. */
function openPage(server, { dialogThrowsFor = null } = {}) {
	const world = { calls: [], dialogs: [], buttons: {}, uploader: null, msgprints: [], errors: [], timers: [], frozen: 0, maxFrozen: 0, loads: 0 };
	class Dialog {
		constructor(opts) {
			if (dialogThrowsFor && opts.title === dialogThrowsFor) throw new Error("dialog broke");
			this.opts = opts;
			this.title = opts.title;
			this.fields_dict = new Proxy({}, { get: () => ({ $wrapper: anything() }) });
			this.shown = false;
			world.dialogs.push(this);
		}
		set_primary_action(label, fn) {
			this.primary = fn;
		}
		get_primary_btn() {
			return anything();
		}
		show() {
			this.shown = true;
		}
		hide() {
			this.shown = false;
		}
	}
	const frappe = lenient({
		pages: { "quickbooks-record-matching": {} },
		ui: lenient({
			make_app_page: () =>
				lenient({
					body: anything(),
					add_inner_button: (label, fn) => {
						world.buttons[label] = fn;
					},
				}),
			Dialog,
			FileUploader: class {
				constructor(opts) {
					world.uploader = opts;
				}
			},
		}),
		utils: lenient({ escape_html: (value) => String(value ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`) }),
		dom: {
			freeze: () => {
				world.frozen += 1;
				world.maxFrozen = Math.max(world.maxFrozen, world.frozen);
			},
			unfreeze: () => {
				world.frozen = Math.max(0, world.frozen - 1);
			},
		},
		msgprint: (message) => world.msgprints.push(message),
		confirm: (_message, yes) => yes(),
		show_alert: () => {},
		set_route: () => {},
		get_route: () => ["quickbooks-record-matching"],
		call(opts) {
			const method = String(opts.method).replace(API, "");
			world.calls.push({ method, args: opts.args, silent: !!opts.silent });
			if (method === "preview_matching_import") {
				const reply = server.preview();
				if (opts.callback) reply.then((data) => opts.callback(data));
				return reply;
			}
			if (method === "apply_matching_import") {
				return server.apply(opts.args);
			}
			if (method === "get_match_queue") world.loads += 1;
			return new Promise(() => {}); // the queue reload: never answers here
		},
	});
	const quiet = { log() {}, warn() {}, error: (error) => world.errors.push(error) };
	// The page's pauses before a resend, recorded and not waited out.
	const timer = (fn, ms) => {
		world.timers.push(ms);
		return setTimeout(fn, 0);
	};
	const load = new Function(
		"frappe",
		"__",
		"$",
		"window",
		"document",
		"console",
		"setTimeout",
		`${SOURCE}\nreturn { qbo_rm_run_import, qbo_rm_import_chunks, qbo_rm_failure, qbo_rm_identity };`
	);
	const helpers = load(frappe, translate, anything(), lenient({}), anything(), quiet, timer);
	const wrapper = {};
	frappe.pages["quickbooks-record-matching"].on_page_load(wrapper);
	world.helpers = helpers;
	return world;
}

/** Click through Import from Excel → upload → preview → Apply → confirm, and wait for the end. */
async function runImport(world) {
	world.buttons["Import from Excel"]();
	assert.ok(world.uploader, "the Import button opens Frappe's uploader");
	world.uploader.on_success({ name: "FILE-1" });
	await settleAll();
	const preview = world.dialogs.find((d) => d.title === "Import preview");
	assert.ok(preview && preview.shown, "the preview dialog opened");
	preview.primary();
	for (let i = 0; i < 200 && !world.dialogs.some((d) => d.title === "Import finished") && !world.msgprints.length; i++) {
		await settleAll();
	}
	return world.dialogs.find((d) => d.title === "Import finished");
}

const settleAll = () => new Promise((resolve) => setTimeout(resolve, 0));
const applies = (world) => world.calls.filter((c) => c.method === "apply_matching_import");
const reportHtml = (dialog) => dialog.opts.fields[0].options;

// ------------------------------------------------------------------ the run, end to end

test("a merge that wipes the preview mid-run: the page previews again and every row lands once", async () => {
	const file = makeFile(25);
	const server = new FakeServer(file, { shift: 1 }); // each fresh preview numbers the rows one further on
	// The pre-fix server: the first Link of every batch merges a duplicate and clear_cache takes
	// every plan with it, so the batch after it always comes back expired.
	server.onApply = (srv, row) => {
		if ((row.row_number - 2) % 10 === 4) srv.plans.clear();
	};
	const world = openPage(server);
	const report = await runImport(world);
	assert.ok(report && report.shown, "the run ends in the Import finished dialog");
	assert.deepEqual(server.applied, file.map((row) => row.row_number), "every row applied, once, in order");
	assert.match(reportHtml(report), /<p>25 applied\.<\/p>/);
	assert.doesNotMatch(reportHtml(report), /failed|not sent|skipped/);
	assert.equal(report.opts.indicator, "green");
	assert.equal(server.previews, 3, "the upload's preview, then one renewal for each of batches 2 and 3");
	const sent = applies(world);
	// batch 1, batch 2 (expired) + its retry, batch 3 (expired) + its retry
	assert.equal(sent.length, 5);
	assert.ok(sent.every((call) => call.silent), "apply calls are silent: no shared message dialog mid-run");
	assert.ok(sent.every((call) => call.args.file_name === "FILE-1"), "every batch names the uploaded file");
	assert.deepEqual(Object.keys(sent[0].args.indexes[0]).sort(), ["index", "qbo_id", "qbo_type", "row_number", "sheet"]);
	assert.equal(sent[2].args.token, "t2-digest", "the retry uses the fresh preview's token");
	assert.equal(sent[2].args.indexes[0].index, 11, "and its numbers: row 12 is index 11 there, not 10");
	assert.equal(world.msgprints.length, 0, "nothing went through frappe.msgprint");
	assert.equal(world.frozen, 0, "the screen is unfrozen");
	assert.ok(world.loads >= 2, "the queue reloads after the run");
	assert.deepEqual(world.errors, [], "the page logged no error");
});

test("the fresh preview fails too: the run stops, says which rows were never sent, sends nothing more", async () => {
	const file = makeFile(25);
	const server = new FakeServer(file);
	server.onApply = (srv, row) => {
		if (row.row_number === 6) {
			srv.plans.clear();
			srv.previewFails = true; // the uploaded file is gone
		}
	};
	const world = openPage(server);
	const report = await runImport(world);
	assert.ok(report && report.shown);
	assert.equal(server.applied.length, 10);
	assert.equal(applies(world).length, 2, "batch 1, then batch 2 once; batch 3 never goes");
	assert.equal(server.previewAttempts, 2, "the upload's preview and one renewal attempt, no loop");
	const html = reportHtml(report);
	assert.match(html, /<p>10 applied\.<\/p>/);
	assert.match(html, /<p>15 were not sent:<\/p>/);
	assert.match(html, /could not be renewed \(File FILE-1 not found\)/, "the server's own words, markup stripped");
	assert.equal(report.opts.indicator, "red");
	assert.equal(world.frozen, 0);
});

test("a retry that is expired again stops the run after one renewal", async () => {
	const file = makeFile(20);
	const server = new FakeServer(file);
	server.onApply = (srv) => srv.plans.clear();
	// Every plan dies the moment it is made from now on, so the retry is expired as well.
	const preview = server.preview.bind(server);
	let calls = 0;
	server.preview = () => {
		calls += 1;
		const reply = preview();
		if (calls > 1) server.plans.clear();
		return reply;
	};
	const world = openPage(server);
	const report = await runImport(world);
	assert.ok(report && report.shown);
	assert.equal(calls, 2, "the upload's preview and exactly one renewal");
	assert.equal(applies(world).length, 3, "batch 1, batch 2, batch 2's one retry");
	assert.match(reportHtml(report), /<p>10 were not sent:<\/p>/);
	assert.match(reportHtml(report), /could not be renewed \(the fresh preview expired at once too\)/);
});

test("an ordinary failed batch is reported against its rows and the run carries on", async () => {
	const file = makeFile(25);
	const server = new FakeServer(file);
	server.applyScript = [
		() => Promise.reject({ status: 500, responseText: "<html>Internal server error</html>" }),
		() => Promise.resolve({ message: { not: "a list" } }),
	];
	const world = openPage(server);
	const report = await runImport(world);
	assert.ok(report && report.shown);
	assert.equal(applies(world).length, 3, "all three batches went");
	assert.deepEqual(server.applied, file.slice(20).map((row) => row.row_number));
	const html = reportHtml(report);
	assert.match(html, /<p>20 failed:<\/p>/);
	assert.match(html, /The request failed; import the file again to see whether it landed\./);
	assert.match(html, /The server&#39;s answer was not understood|The server's answer was not understood/);
	assert.equal(server.previews, 1, "a non-expired failure never re-previews");
	assert.deepEqual(world.timers, [], "a 500 is not resent");
});

test("a deploy restart (503) mid-run: the page waits, sends the same batch again, and every row lands", async () => {
	const file = makeFile(25);
	const server = new FakeServer(file);
	const unavailable = () => Promise.reject({ status: 503, responseText: "<html>Service Unavailable</html>" });
	// One entry per apply call: batch 1 runs normally, then batch 2 meets two 503s.
	server.applyScript = [null, unavailable, unavailable];
	const world = openPage(server);
	const report = await runImport(world);
	assert.ok(report && report.shown);
	assert.deepEqual(server.applied, file.map((row) => row.row_number), "every row applied once");
	assert.deepEqual(world.timers, [5000, 10000], "waited 5 s, then 10 s");
	const sent = applies(world);
	assert.equal(sent.length, 5, "batch 1, batch 2 three times, batch 3");
	assert.deepEqual(sent[1].args, sent[2].args, "the resend is the same batch under the same token");
	assert.deepEqual(sent[2].args, sent[3].args);
	assert.match(reportHtml(report), /<p>25 applied\.<\/p>/);
	assert.equal(world.frozen, 0);
});

test("a Retry goes alone and the report survives a broken Dialog", async () => {
	const file = makeFile(12, { retryAt: [3] });
	const world = openPage(new FakeServer(file), { dialogThrowsFor: "Import finished" });
	await runImport(world);
	const sizes = applies(world).map((call) => call.args.indexes.length);
	// The Retry goes the moment it comes up; the three Links before it wait for the next batch
	// of ten. The order this page has always sent in: each row is its own decision on the server.
	assert.deepEqual(sizes, [1, 10, 1], "the Retry by itself, the Links in tens");
	assert.equal(world.errors.length, 1, "the broken Dialog was logged");
	assert.equal(world.msgprints.length, 1, "the fallback line still says the run is over");
	assert.equal(world.msgprints[0].title, "Import finished");
	assert.match(world.msgprints[0].message, /12 of 12 rows applied/);
});

// ------------------------------------------------------------------ the helpers on their own

test("qbo_rm_failure reads the exception class and the server's words, and survives anything", () => {
	const { qbo_rm_failure } = openPage(new FakeServer([])).helpers;
	assert.deepEqual(qbo_rm_failure(rejection(EXPIRED, "This preview has expired.")), {
		status: 417,
		exc_type: EXPIRED,
		error: "This preview has expired.",
	});
	assert.deepEqual(
		qbo_rm_failure({ status: 403, responseText: JSON.stringify({ exc_type: "PermissionError", _server_messages: "[]" }) }),
		{ status: 403, exc_type: "PermissionError", error: "" }
	);
	assert.deepEqual(qbo_rm_failure({ status: 0, responseText: "" }), { status: 0, exc_type: "", error: "" }, "no connection is 0, not missing");
	for (const odd of [undefined, null, {}, { responseText: "<html>" }, { responseJSON: { _server_messages: "not json" } }]) {
		const out = qbo_rm_failure(odd);
		assert.equal(out.status, null);
		assert.equal(typeof out.exc_type, "string");
		assert.equal(typeof out.error, "string");
	}
});

/** qbo_rm_run_import with a scripted `send`: one reply (or reply-maker) per call, in order. */
async function runScripted(rows, replies, { repreview, size = 10 } = {}) {
	const { qbo_rm_run_import } = openPage(new FakeServer([])).helpers;
	const sent = [];
	const pauses = [];
	let frozen = 0;
	let finished = null;
	await qbo_rm_run_import({
		token: "t1-digest",
		rows,
		size,
		send: (token, entries) => {
			sent.push({ token, entries });
			const next = replies.length ? replies.shift() : "ok";
			if (next === "ok") return Promise.resolve({ ok: true, message: entries.map((e) => ({ ...e, ok: true, message: "done" })) });
			return Promise.resolve(typeof next === "function" ? next(entries) : next);
		},
		repreview: repreview || (() => Promise.resolve({ ok: false })),
		freeze: () => (frozen += 1),
		unfreeze: () => (frozen -= 1),
		wait: (ms) => {
			pauses.push(ms);
			return Promise.resolve();
		},
		finish: (results) => (finished = results),
	});
	assert.equal(frozen, 0, "every freeze is undone");
	return { sent, pauses, finished };
}

const indexed = (count) => makeFile(count).map((row, index) => ({ ...row, index }));

test("a 504 is never sent again: the worker may still be running it", async () => {
	const { sent, pauses, finished } = await runScripted(indexed(15), [{ ok: false, status: 504, exc_type: "", error: "" }]);
	assert.equal(sent.length, 2, "batch 1 once, then batch 2");
	assert.deepEqual(pauses, []);
	assert.equal(finished.filter((r) => !r.ok && /see whether it landed/.test(r.message)).length, 10);
	assert.equal(finished.filter((r) => r.ok).length, 5, "the run carried on");
});

test("a dead connection is tried five times over 75 seconds, then the run stops", async () => {
	const down = { ok: false, status: 0, exc_type: "", error: "" };
	const { sent, pauses, finished } = await runScripted(indexed(25), [down, down, down, down, down]);
	assert.equal(sent.length, 5, "batch 1 and its four resends; batches 2 and 3 never go");
	assert.ok(sent.every((call) => call.token === "t1-digest" && call.entries.length === 10 && call.entries[0].index === 0));
	assert.deepEqual(pauses, [5000, 10000, 20000, 40000]);
	assert.equal(finished.length, 25);
	assert.equal(finished.filter((r) => !r.not_sent && /see whether it landed/.test(r.message)).length, 10, "batch 1 may have landed");
	const unsent = finished.filter((r) => r.not_sent);
	assert.equal(unsent.length, 15);
	assert.match(unsent[0].message, /stopped answering \(no connection\) for over a minute/);
});

test("a renewal the server does not answer stops the run, with the batch reported as not sent", async () => {
	const expired = { ok: false, status: 417, exc_type: EXPIRED, error: "expired" };
	const { sent, pauses, finished } = await runScripted(indexed(15), [expired], {
		repreview: () => Promise.resolve({ ok: false, status: 502, exc_type: "", error: "" }),
	});
	assert.equal(sent.length, 1);
	assert.deepEqual(pauses, [5000, 10000, 20000, 40000], "the renewal was resent too");
	assert.equal(finished.filter((r) => r.not_sent).length, 15, "batch 1 never ran; nothing after it went");
});

test("a batch the fresh preview emptied is not sent", async () => {
	const rows = indexed(12); // batches of 10 and 2
	const expired = { ok: false, status: 417, exc_type: EXPIRED, error: "expired" };
	const { sent, finished } = await runScripted(rows, [expired], {
		repreview: () => Promise.resolve({ ok: true, token: "t2-digest", rows: rows.slice(0, 10).map((r) => ({ ...r, index: r.index + 3 })) }),
	});
	assert.equal(sent.length, 2, "batch 1, its retry, and no empty batch 2");
	assert.equal(sent[1].entries[0].index, 3);
	assert.equal(finished.filter((r) => r.ok).length, 10);
	assert.equal(finished.filter((r) => /no longer lists this row/.test(r.message)).length, 2);
});

test("a send that throws, or a finish handed to the run directly, still ends the run", async () => {
	const { qbo_rm_run_import } = openPage(new FakeServer([])).helpers;
	const rows = makeFile(3).map((row, index) => ({ ...row, index }));
	let finished = null;
	let frozen = 0;
	await qbo_rm_run_import({
		token: "t",
		rows,
		size: 10,
		send: () => {
			throw new Error("boom");
		},
		repreview: () => Promise.resolve({ ok: false }),
		freeze: () => (frozen += 1),
		unfreeze: () => (frozen -= 1),
		finish: (results) => (finished = results),
	});
	assert.equal(frozen, 0);
	assert.equal(finished.length, 3);
	assert.ok(finished.every((r) => !r.ok && !r.skipped && /The request failed/.test(r.message)));
});

test("a row the fresh preview no longer lists is reported, and the others still go", async () => {
	const { qbo_rm_run_import } = openPage(new FakeServer([])).helpers;
	const rows = makeFile(4).map((row, index) => ({ ...row, index }));
	const sent = [];
	let first = true;
	let finished = null;
	await qbo_rm_run_import({
		token: "old-digest",
		rows,
		size: 10,
		send: (token, entries) => {
			sent.push({ token, entries });
			if (first) {
				first = false;
				return Promise.resolve({ ok: false, exc_type: EXPIRED, error: "expired" });
			}
			return Promise.resolve({ ok: true, message: entries.map((e) => ({ ...e, ok: true, message: "done" })) });
		},
		repreview: () =>
			Promise.resolve({ ok: true, token: "new-digest", rows: rows.filter((r) => r.row_number !== 3).map((r) => ({ ...r, index: r.index + 7 })) }),
		freeze: () => {},
		unfreeze: () => {},
		finish: (results) => (finished = results),
	});
	assert.equal(sent.length, 2);
	assert.equal(sent[1].token, "new-digest");
	assert.deepEqual(sent[1].entries.map((e) => [e.row_number, e.index]), [
		[2, 7],
		[4, 9],
		[5, 10],
	]);
	const lost = finished.find((r) => r.row_number === 3);
	assert.ok(lost && !lost.ok && /no longer lists this row/.test(lost.message));
	assert.equal(finished.filter((r) => r.ok).length, 3);
});

test("the page wires the run the way this file assumes", () => {
	const body = (name) => {
		const start = SOURCE.indexOf(`function ${name}(`);
		assert.ok(start >= 0, `${name} exists`);
		return SOURCE.slice(start, SOURCE.indexOf("\n\t}\n", start));
	};
	const apply = body("applyImport");
	assert.match(apply, /qbo_rm_run_import\(/);
	assert.match(apply, /file_name: fileName/);
	assert.equal((apply.match(/\{ silent: true \}/g) || []).length, 2, "apply and the renewal preview are silent");
	const report = body("reportImport");
	assert.match(report, /new frappe\.ui\.Dialog\(/);
	assert.match(report, /title: __\("Import finished"\)/);
	assert.match(SOURCE, /applyImport\(data\.token, data\.file, changes/);
	assert.match(SOURCE, /const EXPIRED = "MatchingPreviewExpired";/);
});

// ------------------------------------------------------------------ runner

let failures = 0;
for (const [name, fn] of tests) {
	try {
		await fn();
		console.log(`ok - ${name}`);
	} catch (error) {
		failures += 1;
		console.log(`not ok - ${name}`);
		console.log(error);
	}
}
console.log(`\n${tests.length - failures}/${tests.length} passed`);
process.exit(failures ? 1 : 0);
