#!/usr/bin/env node
/**
 * The Estimate form's pricing engine under node: public/js/estimating/estimate_math.js against the
 * same cases tests/test_estimate_math.py holds the Python engine to
 * (erpnext_enhancements/tests/data/estimate_math_cases.json). If the two engines ever disagree, the
 * form shows a total the server will not save; this suite and the Python one together stop that.
 *
 * Run: node scripts/test_estimate_math.mjs
 */

import fs from "node:fs";
import path from "node:path";
import assert from "node:assert/strict";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.join(HERE, "..", "erpnext_enhancements");
const em = await import(pathToFileURL(path.join(APP, "public/js/estimating/estimate_math.js")).href);
const CASES = JSON.parse(fs.readFileSync(path.join(APP, "tests/data/estimate_math_cases.json"), "utf8"));

let failures = 0;
let checks = 0;
const test = (name, fn) => {
	try {
		fn();
		checks += 1;
		console.log(`ok   ${name}`);
	} catch (err) {
		failures += 1;
		console.log(`FAIL ${name}\n     ${err.message.split("\n").slice(0, 12).join("\n     ")}`);
	}
};

test("round_money matches the shared cases", () => {
	for (const [value, expected] of CASES.round_money) assert.equal(em.round_money(value), expected, String(value));
	for (const [value, expected] of CASES.round_money_1dp) assert.equal(em.round_money(value, 1), expected);
	assert.equal(em.round_money(2.1 * 2.45), 5.14);
});

test("parse_rate and phase_chain match the shared cases", () => {
	for (const [raw, expected] of CASES.parse_rate) assert.equal(em.parse_rate(raw), expected, String(raw));
	assert.throws(() => em.parse_rate("five"));
	for (const [number, chain] of CASES.phase_chain) assert.deepEqual(em.phase_chain(number), chain);
});

test("back-fill matches the shared cases", () => {
	for (const c of CASES.backfill) {
		const uc = em.backfill_unit_cost(c.price, c.qty, c.cost_type, c.rates);
		assert.equal(uc, c.unit_cost);
		assert.equal(em.price_line(c.qty, uc, c.cost_type, c.rates).amount, c.reprices_to);
	}
});

// Python's json writes 2.0 as 2.0 and JS reads it as 2, so compare as numbers, key by key.
const close = (got, want, where) => {
	if (typeof want === "number" || typeof want === "boolean") {
		assert.equal(got, want, where);
		return;
	}
	if (Array.isArray(want)) {
		assert.equal(got.length, want.length, `${where}.length`);
		want.forEach((w, i) => close(got[i], w, `${where}[${i}]`));
		return;
	}
	assert.deepEqual(Object.keys(got).sort(), Object.keys(want).sort(), `${where} keys`);
	for (const k of Object.keys(want)) close(got[k], want[k], `${where}.${k}`);
};

for (const c of CASES.estimates) {
	test(`price_estimate agrees with Python: ${c.name}`, () => {
		close(em.price_estimate(c.estimate, c.phases, c.lines), c.expect, c.name);
	});
}

// The editor (public/js/estimating/editor.js) touches frappe only when called, so node can import it.
const ed = await import(pathToFileURL(path.join(APP, "public/js/estimating/editor.js")).href);

test("the editor's phase labels are rules.DEFAULT_PHASE_LABELS", () => {
	const src = fs.readFileSync(path.join(APP, "estimating/rules.py"), "utf8");
	const block = src.split("DEFAULT_PHASE_LABELS = {")[1].split("}")[0];
	const py = {};
	for (const m of block.matchAll(/(\d+):\s*"([^"]*)"/g)) py[m[1]] = m[2];
	const js = Object.fromEntries(Object.entries(ed.DEFAULT_PHASE_LABELS).map(([k, v]) => [String(k), v]));
	assert.deepEqual(js, py);
});

test("phase_tree nests design stages under 100 and keeps unphased lines last", () => {
	const tree = ed.phase_tree([{ phase_number: 300 }], [{ phase_number: 120 }, { phase_number: 0 }, { phase_number: 500 }]);
	assert.deepEqual(tree, [
		{ number: 100, children: [120] },
		{ number: 300, children: [] },
		{ number: 500, children: [] },
		{ number: 0, children: [] },
	]);
	assert.deepEqual(ed.phase_tree([], []), [{ number: 0, children: [] }]);
});

test("the form script asks for the level the bundle exports, and calls only real exports", () => {
	const bundle = fs.readFileSync(path.join(APP, "public/js/estimate_editor.bundle.js"), "utf8");
	const form = fs.readFileSync(path.join(APP, "estimating/doctype/estimate/estimate.js"), "utf8");
	const version = Number(/const VERSION = (\d+);/.exec(bundle)[1]);
	const required = Number(/const REQUIRED_EDITOR_LEVEL = (\d+);/.exec(form)[1]);
	assert.equal(required, version);
	for (const m of form.matchAll(/ed\.(\w+)\(/g)) assert.equal(typeof ed[m[1]], "function", m[1]);
});

test("nothing typed reaches the page unescaped", () => {
	const src = fs.readFileSync(path.join(APP, "public/js/estimating/editor.js"), "utf8");
	for (const field of ["description", "cost_code", "uom", "label", "scope_text", "rate_card", "quotation"]) {
		const raw = new RegExp(`\\$\\{\\s*(line|l|p|row|frm\\.doc)\\.${field}\\s*\\}`);
		assert.ok(!raw.test(src), `${field} interpolated without esc()`);
	}
});

// The pay application's G703/G702 twin against the cases the Python engine wrote
// (tests/data/pay_app_math_cases.json): a total the editor shows must be the one the server stores.
const pm = await import(pathToFileURL(path.join(APP, "public/js/estimating/pay_app_math.js")).href);
const pe = await import(pathToFileURL(path.join(APP, "public/js/estimating/pay_app_editor.js")).href);
const PAY_CASES = JSON.parse(fs.readFileSync(path.join(APP, "tests/data/pay_app_math_cases.json"), "utf8")).cases;
test("the pay application twin matches every shared case", () => {
	for (const c of PAY_CASES) {
		const got = pm.price_application(c.schedule, c.application, c.lines, c.previous);
		assert.deepEqual(got, c.expected, c.name);
	}
});

test("the pay application form asks for the level its bundle exports, and calls only real exports", async () => {
	const bundle = fs.readFileSync(path.join(APP, "public/js/pay_app_editor.bundle.js"), "utf8");
	const form = fs.readFileSync(path.join(APP, "estimating/doctype/pay_application/pay_application.js"), "utf8");
	const version = Number(/const VERSION = (\d+);/.exec(bundle)[1]);
	const required = Number(/const REQUIRED_PAY_EDITOR_LEVEL = (\d+);/.exec(form)[1]);
	assert.equal(required, version);
	for (const m of form.matchAll(/ed\.(\w+)\(/g)) assert.equal(typeof pe[m[1]], "function", m[1]);
	// phases in number order, change orders last, a design stage under its hundred
	const g = pe.groups([{ phase_number: 500 }, { is_change_order: 1 }, { phase_number: 120 }, { phase_number: 0 }]);
	assert.deepEqual(
		g.map(([k]) => k),
		[0, 100, 500, "co"]
	);
});

test("nothing typed reaches the pay application page unescaped", () => {
	const src = fs.readFileSync(path.join(APP, "public/js/estimating/pay_app_editor.js"), "utf8");
	for (const field of ["description", "item_no", "line_key", "name", "milestone"]) {
		const raw = new RegExp(`\\$\\{\\s*(line|l|row|frm\\.doc)\\.${field}\\s*\\}`);
		assert.ok(!raw.test(src), `${field} interpolated without esc()`);
	}
	assert.ok(!/href="\/app\//.test(src), "Desk links are /desk/");
});

// frm.save() at v16 swallows a server refusal and resolves anyway; the form stays dirty. Issue and New
// Revision must stop there, not issue from the stored version and reload over the unsaved edits.
const atest = async (name, fn) => {
	try {
		await fn();
		checks += 1;
		console.log(`ok   ${name}`);
	} catch (err) {
		failures += 1;
		console.log(`FAIL ${name}\n     ${err.message.split("\n").slice(0, 12).join("\n     ")}`);
	}
};
const fakeForm = (saveWorks) => {
	const calls = [];
	globalThis.__ = (s) => s;
	globalThis.frappe = {
		call: async (o) => (calls.push(o.method), { message: "SAL-QTN-0001" }),
		confirm: (m, yes) => (calls.push("confirm"), yes()),
		set_route: () => calls.push("set_route"),
		show_alert: () => {},
		utils: { escape_html: (s) => String(s) },
	};
	const frm = {
		__dirty: true,
		doc: { name: "EST-26-0001", revision: 1 },
		is_dirty() { return this.__dirty; },
		is_new() { return false; },
		save: async () => { calls.push("save"); if (saveWorks) frm.__dirty = false; },
		reload_doc: async () => calls.push("reload"),
	};
	return { frm, calls };
};

await atest("Issue stops when the save fails, and goes on when it works", async () => {
	let { frm, calls } = fakeForm(false);
	await ed.issue(frm);
	assert.deepEqual(calls, ["save"]);
	({ frm, calls } = fakeForm(true));
	await ed.issue(frm);
	assert.deepEqual(calls, ["save", "erpnext_enhancements.api.estimating.issue_quotation", "reload"]);
});

await atest("New Revision stops when the save fails, and goes on when it works", async () => {
	let { frm, calls } = fakeForm(false);
	await ed.new_revision(frm);
	assert.deepEqual(calls, ["save"]);
	({ frm, calls } = fakeForm(true));
	await ed.new_revision(frm);
	await new Promise((r) => setTimeout(r, 0));
	assert.deepEqual(calls, ["save", "confirm", "erpnext_enhancements.api.estimating.new_revision", "set_route"]);
});

console.log(`\n${checks} passed, ${failures} failed`);
process.exit(failures ? 1 : 0);
