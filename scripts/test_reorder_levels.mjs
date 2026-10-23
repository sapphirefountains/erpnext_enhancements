#!/usr/bin/env node
/**
 * The reorder boxes on the audit screens (v1.725.0) under node: the pure decisions (logic.js), the
 * editor's state and saves against a model of the server (editor.js), the cell markup, and the DOM
 * wiring (dom.js) over a model of the few DOM calls it makes.
 *
 * What it pins, in the words of the failures it prevents:
 *   - what is typed is read strictly (no "1e3", no "-2", no "Infinity") and a blur that changes nothing
 *     saves nothing;
 *   - a level with no quantity is refused in words before a request;
 *   - a rule is edited on the warehouse it sits on, and an item with none gets one on the screen's;
 *   - two quick blurs on one item are two saves in turn, the second carrying the first's new stamp;
 *   - a changed Item is reloaded, and its numbers shown with the sentence that says so;
 *   - a refused save leaves the typed text in its box, and a box with focus is never overwritten;
 *   - nothing here touches history (the screens' Back/Forward are the pages' own);
 *   - everything the server sends is escaped.
 *
 * Run: node scripts/test_reorder_levels.mjs   (from tests/test_reorder_levels.py)
 */

import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const JS = path.join(HERE, "..", "erpnext_enhancements", "public", "js", "reorder_levels");
const logic = await import(pathToFileURL(path.join(JS, "logic.js")).href);
const { ReorderEditor, cell_html } = await import(pathToFileURL(path.join(JS, "editor.js")).href);
const dom = await import(pathToFileURL(path.join(JS, "dom.js")).href);

let checks = 0;
let failures = 0;
const tests = [];
const test = (name, fn) => tests.push({ name, fn });

function check(label, actual, expected) {
	checks += 1;
	const a = JSON.stringify(actual);
	const e = JSON.stringify(expected);
	if (a !== e) {
		failures += 1;
		console.log(`  FAIL ${label}\n    expected ${e}\n    actual   ${a}`);
	}
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function settle() {
	for (let i = 0; i < 10; i++) await tick();
}

const esc = (v) =>
	String(v === null || v === undefined ? "" : v)
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/"/g, "&quot;");

const BIN = "Bin A1-1-1 - SF";
const ROOM = "Inventory Room - SF";

function entry(code, over) {
	return Object.assign(
		{
			item_code: code,
			has_rule: true,
			row_warehouse: BIN,
			warehouse_label: "Bin A1-1-1",
			elsewhere: false,
			level: 4,
			qty: 12,
			material_request_type: "Purchase",
			modified: "2026-10-22 09:00:00.000001",
		},
		over || {}
	);
}

// ------------------------------------------------------------------ logic

test("parse_amount reads digits only", () => {
	check("blank", logic.parse_amount("  "), { ok: true, value: null });
	check("integer", logic.parse_amount("12"), { ok: true, value: 12 });
	check("decimal", logic.parse_amount("2.5"), { ok: true, value: 2.5 });
	check("leading point", logic.parse_amount(".5"), { ok: true, value: 0.5 });
	check("trailing point", logic.parse_amount("3."), { ok: true, value: 3 });
	check("thousands comma", logic.parse_amount("1,200"), { ok: true, value: 1200 });
	check("grouped thousands with a decimal", logic.parse_amount("1,200.5"), { ok: true, value: 1200.5 });
	for (const euro of ["2,5", "1,00", "12,34", "1,2,3", ",5", "1,2000"]) check(`a comma that is not a thousands separator: ${euro}`, logic.parse_amount(euro), { ok: false });
	for (const bad of ["-2", "1e3", "Infinity", "NaN", "abc", "1.2.3", "+4", "0x10", "--"]) {
		check(`refuses ${bad}`, logic.parse_amount(bad), { ok: false });
	}
});

test("number_text shows a quantity the way a person writes it", () => {
	check("whole", logic.number_text(3), "3");
	check("float noise", logic.number_text(0.1 + 0.2), "0.3");
	check("none", logic.number_text(null), "");
	check("zero", logic.number_text(0), "0");
});

test("plan_save: nothing, invalid, or a save", () => {
	const ruled = entry("A");
	check("unchanged", logic.plan_save(ruled, "4", "12").kind, "none");
	check("unchanged, written differently", logic.plan_save(ruled, "4.0", "12.00").kind, "none");
	check("level changed", logic.plan_save(ruled, "6", "12"), { kind: "save", level: 6, qty: 12 });
	check("qty changed", logic.plan_save(ruled, "4", "20"), { kind: "save", level: 4, qty: 20 });
	check("a cleared box on a rule asks to remove it, never saves 0", logic.plan_save(ruled, "", "12"), { kind: "remove" });
	check("the other box cleared", logic.plan_save(ruled, "4", "").kind, "remove");
	check("both cleared", logic.plan_save(ruled, "", "").kind, "remove");
	check("a quantity of 0 is refused, in words about Buy only", logic.plan_save(ruled, "4", "0"), { kind: "invalid", field: "qty", message: logic.QTY_ABOVE_ZERO });
	check("that sentence is about Buy and does not talk about both boxes", [/^Buy must be more than 0/.test(logic.QTY_ABOVE_ZERO), /Reorder at/.test(logic.QTY_ABOVE_ZERO)], [true, false]);
	// Nik, 2026-10-22: "The reorder at line is refusing to be assigned a 0 even though running out of all
	// stock is a very normal spot to reorder items." ERPNext reorders when projected stock <= the level.
	check("a level of 0 is a rule: it saves", logic.plan_save(ruled, "0", "12"), { kind: "save", level: 0, qty: 12 });
	check("a blank level is not a 0: it asks to remove the rule", logic.plan_save(ruled, "", "12"), { kind: "remove" });
	check("0 and 0: the quantity is what is refused", logic.plan_save(ruled, "0", "0"), { kind: "invalid", field: "qty", message: logic.QTY_ABOVE_ZERO });
	check("a negative level is still not a number", logic.plan_save(ruled, "-1", "12"), { kind: "invalid", field: "level", message: "The reorder level must be a number, 0 or more." });
	check("an old rule left as it is (level 0 on prod) is not touched", logic.plan_save(entry("Z", { level: 0, qty: 1 }), "0", "1").kind, "none");
	check("the same level-0 rule edited keeps its 0", logic.plan_save(entry("Z", { level: 0, qty: 1 }), "0", "5"), { kind: "save", level: 0, qty: 5 });
	check("a level-0 rule left alone is unchanged, written any way", logic.plan_save(entry("Z", { level: 0, qty: 1 }), "0.0", "1.00").kind, "none");
	check("not a number: level", logic.plan_save(ruled, "x", "12").field, "level");
	check("not a number: qty", logic.plan_save(ruled, "4", "-3").field, "qty");
	const bare = entry("B", { has_rule: false, level: null, qty: null, modified: "m" });
	check("no rule, boxes empty", logic.plan_save(bare, "", "").kind, "none");
	check("no rule, both given", logic.plan_save(bare, "3", "10"), { kind: "save", level: 3, qty: 10 });
	check("no rule, one box: a gentle note, nothing created", logic.plan_save(bare, "3", ""), { kind: "incomplete", message: logic.BOTH_NEEDED });
	check("no rule, the other box only", logic.plan_save(bare, "", "10").kind, "incomplete");
	check("no rule, a level of 0 with a quantity is a rule", logic.plan_save(bare, "0", "10"), { kind: "save", level: 0, qty: 10 });
	check("no rule, a level of 0 alone is still one box", logic.plan_save(bare, "0", "").kind, "incomplete");
	check("no rule, a quantity of 0 is not a rule", logic.plan_save(bare, "3", "0"), { kind: "invalid", field: "qty", message: logic.QTY_ABOVE_ZERO });
	check("the ceiling is the server ceiling", logic.MAX_AMOUNT, 100000);
	check("at the ceiling", logic.plan_save(bare, "100000", "100000").kind, "save");
	for (const big of ["100001", "12345678905", "1,000,000"]) {
		check(`above the ceiling: ${big}`, [logic.plan_save(ruled, big, "12").kind, logic.plan_save(ruled, "4", big).field], ["invalid", "qty"]);
	}
	check("the sentence names the ceiling", /100,000/.test(logic.plan_save(bare, "999999", "1").message), true);
	const whole = entry("W", { whole_number: true });
	check("a fraction in a whole-number unit", logic.plan_save(whole, "4.5", "12"), { kind: "invalid", field: "level", message: "The reorder level must be a whole number for this item." });
	check("a whole number in a whole-number unit", logic.plan_save(whole, "5", "12").kind, "save");
	check("a fraction where fractions are fine", logic.plan_save(entry("F", { whole_number: false }), "4.5", "12").kind, "save");
	check("translated", logic.plan_save(ruled, "x", "1", (s) => "T:" + s).message.startsWith("T:"), true);
});

test("save_request edits the rule where it sits, or on the screen's warehouse", () => {
	const there = entry("A", { row_warehouse: ROOM, elsewhere: true });
	const req = logic.save_request(there, BIN, { kind: "save", level: 5, qty: 9 });
	check("method", req.method, "erpnext_enhancements.api.reorder_levels.set_reorder_level");
	check("remove call", logic.remove_request(there, BIN), {
		method: "erpnext_enhancements.api.reorder_levels.remove_reorder_rule",
		args: { item_code: "A", warehouse: ROOM, view_warehouse: BIN, modified: "2026-10-22 09:00:00.000001" },
	});
	check("args", req.args, {
		item_code: "A",
		warehouse: ROOM,
		view_warehouse: BIN,
		modified: "2026-10-22 09:00:00.000001",
		level: 5,
		qty: 9,
	});
	const none = entry("B", { has_rule: false, row_warehouse: BIN });
	check("new rule goes on the screen's warehouse", logic.save_request(none, BIN, { level: 1, qty: 2 }).args.warehouse, BIN);
	check("no place at all", logic.has_place(entry("C", { row_warehouse: null })), false);
	check("conflict by status", logic.is_conflict({ status: 409 }), true);
	check("conflict by words", logic.is_conflict({ message: "X was changed by someone else since" }), true);
	check("not a conflict", logic.is_conflict({ status: 417, message: "Row #1: Please set reorder quantity" }), false);
	check("error text strips markup", logic.error_text({ message: "<b>Row 1</b>:  bad" }, "x"), "Row 1 : bad");
	check("error text fallback", logic.error_text({}, "fallback"), "fallback");
});

// ------------------------------------------------------------------ the model server and editor

function makeWorld(options) {
	const o = options || {};
	const world = {
		calls: [],
		boxes: new Map(), // code -> { level, qty }
		painted: [],
		timers: [],
		asked: [],
		answer: "yes", // what the person says to "Remove this reorder rule?"
		entries: o.entries || { A: entry("A"), B: entry("B", { has_rule: false, level: null, qty: null, row_warehouse: BIN }) },
		can_edit: o.can_edit !== false,
		fail: new Map(),
		hold: new Map(), // method -> promise to wait for, once
		stamp: 1,
	};
	world.call = (method, args) => {
		world.calls.push({ method, args: JSON.parse(JSON.stringify(args)) });
		const name = method.split(".").pop();
		const wait = world.hold.get(name) || Promise.resolve();
		world.hold.delete(name);
		return wait.then(() => {
			const failure = world.fail.get(name);
			if (failure) {
				world.fail.delete(name);
				return Promise.reject(failure);
			}
			if (name === "get_reorder_levels") {
				const codes = JSON.parse(args.item_codes);
				const levels = {};
				codes.forEach((c) => {
					if (world.entries[c]) levels[c] = JSON.parse(JSON.stringify(world.entries[c]));
				});
				return { levels, can_edit: world.can_edit };
			}
			// set_reorder_level / remove_reorder_rule: the server stamp check, then the write
			const cur = world.entries[args.item_code];
			if (cur.modified !== args.modified) {
				return Promise.reject({ status: 409, message: `${args.item_code} was changed by someone else since this page loaded it.` });
			}
			world.stamp += 1;
			if (name === "remove_reorder_rule") {
				Object.assign(cur, { has_rule: false, level: null, qty: null, modified: `2026-10-22 09:00:0${world.stamp}.000001` });
				return { saved: true, message: "Removed.", entry: JSON.parse(JSON.stringify(cur)) };
			}
			Object.assign(cur, {
				has_rule: true,
				level: args.level,
				qty: args.qty,
				row_warehouse: args.warehouse,
				modified: `2026-10-22 09:00:0${world.stamp}.000001`,
			});
			return { saved: true, message: "Saved.", entry: JSON.parse(JSON.stringify(cur)) };
		});
	};
	world.env = {
		call: (m, a) => world.call(m, a),
		read: (code) => world.boxes.get(code) || { level: "", qty: "" },
		paint: (code, force) => world.painted.push([code, !!force]),
		paint_all: () => world.painted.push(["*", false]),
		later: (fn, ms) => world.timers.push({ fn, ms }),
		tr: (t) => t,
		confirm: (text, yes, no) => {
			world.asked.push(text);
			(world.answer === "yes" ? yes : no)();
		},
	};
	return world;
}

const writes = (world) => world.calls.filter((c) => /set_reorder_level|remove_reorder_rule/.test(c.method));

test("load reads once, keeps what is on screen for the same warehouse, and starts over for another", async () => {
	const world = makeWorld();
	const ed = new ReorderEditor(world.env);
	await ed.load(BIN, ["A", "B", "A", ""]);
	check("one read, unique codes", world.calls.map((c) => JSON.parse(c.args.item_codes)), [["A", "B"]]);
	check("quiet", world.calls[0].args.warehouse, BIN);
	check("ready", ed.ready, true);
	check("can edit", ed.can_edit, true);
	check("entry", ed.view("A").entry.level, 4);
	check("editable", ed.view("A").editable, true);
	check("an item with no rule yet but a place is editable", ed.view("B").editable, true);
	// same warehouse again: the numbers stay while the answer is out
	const hold = new Promise((r) => (world.release = r));
	world.hold.set("get_reorder_levels", hold);
	const again = ed.load(BIN, ["A"]);
	check("kept while reading", ed.view("A").entry.level, 4);
	world.release();
	await again;
	await ed.load(ROOM, ["A"]);
	check("another warehouse starts over", Object.keys(ed.entries).length, 1);
});

test("a read that began before a save does not undo it", async () => {
	const world = makeWorld();
	const ed = new ReorderEditor(world.env);
	await ed.load(BIN, ["A"]);
	// a read goes out and is answered from before the save, but arrives after it
	let release;
	world.hold.set("get_reorder_levels", new Promise((r) => (release = r)));
	const old = JSON.parse(JSON.stringify(world.entries.A));
	const reading = ed.load(BIN, ["A"]);
	await settle();
	world.boxes.set("A", { level: "6", qty: "12" });
	await ed.commit("A");
	check("saved", ed.view("A").entry.level, 6);
	world.entries.A = JSON.parse(JSON.stringify(old)); // the read's answer is the old numbers
	release();
	await reading;
	check("the saved numbers stay", ed.view("A").entry.level, 6);
	check("and the new stamp", ed.view("A").entry.modified !== old.modified, true);
});

test("a read that has been replaced is dropped, and a failed read is said in place", async () => {
	const world = makeWorld();
	const ed = new ReorderEditor(world.env);
	let release;
	world.hold.set("get_reorder_levels", new Promise((r) => (release = r)));
	const first = ed.load(BIN, ["A"]);
	world.entries.A.level = 99;
	const second = ed.load(BIN, ["A"]);
	await second;
	release();
	await first;
	check("the later read wins", ed.view("A").entry.level, 99);
	world.entries.A.level = 1;
	await ed.load(BIN, ["A"]);
	world.fail.set("get_reorder_levels", { status: 500, message: "boom" });
	await ed.load(BIN, ["A"]);
	check("error text", ed.view("A").error, "boom");
	const html = cell_html(esc, null, ed.view("A"), "A", "Pump");
	check("not available shown", html.includes("not available"), true);
	check("no boxes", html.includes("data-ro-field"), false);
});

test("a save sends both boxes with the item's stamp, and shows saved then clears", async () => {
	const world = makeWorld();
	const ed = new ReorderEditor(world.env);
	await ed.load(BIN, ["A"]);
	world.boxes.set("A", { level: "6", qty: "12" });
	await ed.commit("A");
	check("one write", writes(world).length, 1);
	check("args", writes(world)[0].args, {
		item_code: "A",
		warehouse: BIN,
		view_warehouse: BIN,
		modified: "2026-10-22 09:00:00.000001",
		level: 6,
		qty: 12,
	});
	check("entry replaced with the answer", ed.view("A").entry.level, 6);
	check("status saved", ed.view("A").status.state, "saved");
	check("a timer clears it", world.timers.length, 1);
	world.timers[0].fn();
	check("cleared", ed.view("A").status, null);
});

test("nothing is sent for a blur that changes nothing, or for text that is not a number", async () => {
	const world = makeWorld();
	const ed = new ReorderEditor(world.env);
	await ed.load(BIN, ["A"]);
	world.boxes.set("A", { level: "4", qty: "12" });
	await ed.commit("A");
	check("unchanged: no write", writes(world).length, 0);
	world.boxes.set("A", { level: "four", qty: "12" });
	await ed.commit("A");
	check("invalid: no write", writes(world).length, 0);
	check("invalid: said in the cell", ed.view("A").status.state, "error");
	world.boxes.set("A", { level: "4", qty: "12" });
	await ed.commit("A");
	check("back to the saved numbers clears the sentence", ed.view("A").status, null);
});

test("a person who may not edit gets no save, and an item with no place gets none either", async () => {
	const world = makeWorld({ can_edit: false });
	const ed = new ReorderEditor(world.env);
	await ed.load(BIN, ["A"]);
	world.boxes.set("A", { level: "9", qty: "12" });
	await ed.commit("A");
	check("read-only: no write", writes(world).length, 0);
	check("read-only: not editable", ed.view("A").editable, false);
	const text = cell_html(esc, null, ed.view("A"), "A", "Pump");
	check("read-only shows the numbers as text", text.includes("Reorder at 4, buy 12"), true);
	check("read-only has no boxes", text.includes("<input"), false);

	const world2 = makeWorld({ entries: { C: entry("C", { has_rule: false, level: null, qty: null, row_warehouse: null }) } });
	const ed2 = new ReorderEditor(world2.env);
	await ed2.load("", ["C"]);
	check("no warehouse and no rule: nowhere to put one", ed2.view("C").editable, false);
	check("says so", cell_html(esc, null, ed2.view("C"), "C", "x").includes("No reorder rule"), true);
});

test("two quick blurs on one item are two saves in turn, the second on the first's new stamp", async () => {
	const world = makeWorld();
	const ed = new ReorderEditor(world.env);
	await ed.load(BIN, ["A"]);
	let release;
	world.hold.set("set_reorder_level", new Promise((r) => (release = r)));
	world.boxes.set("A", { level: "6", qty: "12" });
	const first = ed.commit("A");
	await settle();
	world.boxes.set("A", { level: "6", qty: "20" });
	await ed.commit("A"); // while the first is out
	check("the second waits: one write so far", writes(world).length, 1);
	release();
	await first;
	await settle();
	check("then two writes", writes(world).length, 2);
	check("the second carries the new stamp", writes(world)[1].args.modified, "2026-10-22 09:00:02.000001");
	check("the second carries the second boxes", [writes(world)[1].args.level, writes(world)[1].args.qty], [6, 20]);
	check("never refused as stale", ed.view("A").status.state, "saved");
});

test("a changed item is reloaded, and its numbers are shown with the sentence", async () => {
	const world = makeWorld();
	const ed = new ReorderEditor(world.env);
	await ed.load(BIN, ["A"]);
	world.entries.A.level = 7; // someone else
	world.entries.A.modified = "2026-10-22 09:30:00.000001";
	world.boxes.set("A", { level: "6", qty: "12" });
	await ed.commit("A");
	await settle();
	check("the sentence", /changed by someone else/.test(ed.view("A").status.message), true);
	check("an error", ed.view("A").status.state, "error");
	check("their numbers are shown", ed.view("A").entry.level, 7);
	check("and nothing of ours was saved", world.entries.A.level, 7);
	check("it read the item again", world.calls.filter((c) => /get_reorder/.test(c.method)).length, 2);
});

test("a refusal from ERPNext is shown and the typed text is the page's to keep", async () => {
	const world = makeWorld();
	const ed = new ReorderEditor(world.env);
	await ed.load(BIN, ["A"]);
	world.boxes.set("A", { level: "6", qty: "12" });
	world.fail.set("set_reorder_level", { status: 417, message: "<b>Row #1</b>: Please set reorder quantity" });
	await ed.commit("A");
	check("the sentence, without markup", ed.view("A").status.message, "Row #1 : Please set reorder quantity");
	check("the item is as it was", ed.view("A").entry.level, 4);
	check("not busy any more: a retry goes out", (await ed.commit("A"), writes(world).length), 2);
});

test("a rule on another warehouse is edited there, and says where it is", async () => {
	const room = entry("A", { row_warehouse: ROOM, warehouse_label: "Inventory Room", elsewhere: true });
	const world = makeWorld({ entries: { A: room } });
	const ed = new ReorderEditor(world.env);
	await ed.load(BIN, ["A"]);
	const html = cell_html(esc, null, ed.view("A"), "A", "Pump");
	check("labelled", html.includes("Rule is on Inventory Room"), true);
	world.boxes.set("A", { level: "5", qty: "12" });
	await ed.commit("A");
	check("written to the room's rule", writes(world)[0].args.warehouse, ROOM);
	check("read against the screen's bin", writes(world)[0].args.view_warehouse, BIN);
});

test("a new rule is made on the screen's warehouse once both numbers are given", async () => {
	const world = makeWorld();
	const ed = new ReorderEditor(world.env);
	await ed.load(BIN, ["B"]);
	world.boxes.set("B", { level: "3", qty: "" });
	await ed.commit("B");
	check("one box: a gentle note, no request", [writes(world).length, ed.view("B").status.state, ed.view("B").status.message], [0, "note", logic.BOTH_NEEDED]);
	world.boxes.set("B", { level: "3", qty: "10" });
	await ed.commit("B");
	check("both: saved on the bin", [writes(world)[0].args.warehouse, writes(world)[0].args.level, writes(world)[0].args.qty], [BIN, 3, 10]);
	check("the item now has a rule", ed.view("B").entry.has_rule, true);
});

test("clearing a box asks first: yes removes the rule through the server, no puts the boxes back", async () => {
	const world = makeWorld();
	const ed = new ReorderEditor(world.env);
	await ed.load(BIN, ["A"]);
	world.boxes.set("A", { level: "", qty: "12" });
	world.answer = "no";
	await ed.commit("A");
	check("asked in the words", world.asked, ["Remove this reorder rule?"]);
	check("no: nothing sent", writes(world).length, 0);
	check("no: the boxes are put back (a forced repaint)", world.painted.at(-1), ["A", true]);
	check("no: the rule is still there", ed.view("A").entry.has_rule, true);
	world.answer = "yes";
	await ed.commit("A");
	check("yes: one remove call", writes(world).map((c) => c.method.split(".").pop()), ["remove_reorder_rule"]);
	check("yes: with the stamp, on the rule warehouse", [writes(world)[0].args.warehouse, writes(world)[0].args.modified], [BIN, "2026-10-22 09:00:00.000001"]);
	check("yes: the item has no rule now", ed.view("A").entry.has_rule, false);
	check("yes: said removed", ed.view("A").status.message, "Removed.");
	check("never a save of 0", world.calls.some((c) => /set_reorder_level/.test(c.method)), false);
});

test("a second blur while the question is up asks nothing more, and a refusal is shown", async () => {
	const world = makeWorld();
	const ed = new ReorderEditor(world.env);
	await ed.load(BIN, ["A"]);
	let answer;
	world.env.confirm = (text, yes, no) => {
		world.asked.push(text);
		answer = { yes, no };
	};
	world.boxes.set("A", { level: "", qty: "" });
	const first = ed.commit("A");
	await ed.commit("A");
	check("one question", world.asked.length, 1);
	answer.no();
	await first;
	check("answered once more is ignored", (answer.yes(), writes(world).length), 0);
	world.env.confirm = (text, yes) => yes();
	world.fail.set("remove_reorder_rule", { status: 417, message: "Item PDT-A is blocked." });
	await ed.commit("A");
	check("a refused removal is shown", [ed.view("A").status.state, ed.view("A").status.message], ["error", "Item PDT-A is blocked."]);
	check("and the rule stays", ed.view("A").entry.has_rule, true);
});

test("a level of 0 shows as 0, reads as a rule, and is never a blank box or 'no rule'", async () => {
	check("number_text keeps a 0", [logic.number_text(0), logic.number_text("0"), logic.number_text(null), logic.number_text("")], ["0", "0", "", ""]);
	const zero = entry("Z", { level: 0, qty: 12 });
	const view = { ready: true, error: "", entry: zero, editable: true, can_edit: true, status: null };
	const html = cell_html(esc, null, view, "Z", "Pump");
	check("the Reorder at box holds 0 and remembers it as shown", [html.includes('data-ro-field="level" value="0" data-ro-shown="0"'), html.includes('data-ro-field="qty" value="12"')], [true, true]);
	const text = cell_html(esc, null, { ...view, editable: false }, "Z", "Pump");
	check("read-only: 'Reorder at 0, buy 12', not 'No reorder rule'", [text.includes("Reorder at 0, buy 12"), text.includes("No reorder rule")], [true, false]);
	const none = cell_html(esc, null, { ...view, editable: false, entry: entry("N", { has_rule: false, level: null, qty: null }) }, "N", "Pump");
	check("a real 'no rule' is still said", none.includes("No reorder rule"), true);

	// load and reload keep the 0
	const world = makeWorld({ entries: { Z: zero } });
	const ed = new ReorderEditor(world.env);
	await ed.load(BIN, ["Z"]);
	check("loaded: level 0, a rule", [ed.view("Z").entry.level, ed.view("Z").entry.has_rule, ed.view("Z").editable], [0, true, true]);
	await ed.load(BIN, ["Z"]);
	check("reloaded: still 0", ed.view("Z").entry.level, 0);

	// saving a 0: the request carries 0, not 'nothing'
	world.boxes.set("Z", { level: "0", qty: "20" });
	await ed.commit("Z");
	check("a level of 0 is sent as 0 with the quantity", [writes(world).length, writes(world)[0].args.level, writes(world)[0].args.qty], [1, 0, 20]);
	check("the answer keeps it, and the box would show 0", [ed.view("Z").entry.level, logic.number_text(ed.view("Z").entry.level)], [0, "0"]);

	// a new rule at 0, and the quantity-only refusal says Buy
	const world2 = makeWorld();
	const ed2 = new ReorderEditor(world2.env);
	await ed2.load(BIN, ["B"]);
	world2.boxes.set("B", { level: "0", qty: "5" });
	await ed2.commit("B");
	check("a new rule at 0 is created", [writes(world2)[0].args.level, writes(world2)[0].args.qty, ed2.view("B").entry.has_rule, ed2.view("B").entry.level], [0, 5, true, 0]);
	world2.boxes.set("B", { level: "0", qty: "0" });
	await ed2.commit("B");
	check("then 0 and 0 says Buy, and sends nothing more", [ed2.view("B").status.message, writes(world2).length], [logic.QTY_ABOVE_ZERO, 1]);
});

test("dom: a level of 0 is painted into its box as 0, never blanked", async () => {
	const root = new El();
	const { l, q } = makeCell(root, "Z", "", "");
	const world = makeWorld({ entries: { Z: entry("Z", { level: 0, qty: 12 }) } });
	const ed = new ReorderEditor(world.env);
	await ed.load(BIN, ["Z"]);
	dom.paint(root, ed, esc, null, "Z", null, false, { Z: "Pump" });
	check("the boxes show 0 and 12", [l.value, q.value, l.getAttribute("data-ro-shown")], ["0", "12", "0"]);
});

test("cell_html escapes everything the server sends", () => {
	const view = {
		ready: true,
		error: "",
		entry: entry('X"><img src=x onerror=1>', { elsewhere: true, warehouse_label: "<script>alert(1)</script>", row_warehouse: "W" }),
		editable: true,
		can_edit: true,
		status: { state: "error", message: "<b>bad</b>" },
	};
	const html = cell_html(esc, null, view, 'X"><img src=x onerror=1>', "<i>Pump</i>");
	check("no raw tag from the label", /<script>|<img|<i>|<b>bad/.test(html), false);
	check("the label is in the box's name, escaped", html.includes("&lt;i&gt;Pump&lt;/i&gt;"), true);
	check("two boxes", (html.match(/<input/g) || []).length, 2);
	check("16px-safe inputmode", html.includes('inputmode="decimal"'), true);
	check("an error is an alert", html.includes('role="alert"'), true);
	check("not read yet", cell_html(esc, null, { ready: false }, "A", "x").includes("…"), true);
});

// ------------------------------------------------------------------ the DOM wiring, over a model

class El {
	constructor(attrs, parent) {
		this.attrs = Object.assign({}, attrs || {});
		this.parentNode = parent || null;
		this.kids = [];
		this.value = "";
		this.textContent = "";
		this.className = "";
		this.listeners = {};
		this.blurred = 0;
		this.outer = null;
		if (parent) parent.kids.push(this);
	}
	getAttribute(n) {
		return n in this.attrs ? this.attrs[n] : null;
	}
	setAttribute(n, v) {
		this.attrs[n] = String(v);
	}
	all() {
		return this.kids.flatMap((k) => [k, ...k.all()]);
	}
	match(sel) {
		let m = sel.match(/^\[([a-z-]+)="([^"]*)"\]$/);
		if (m) return this.attrs[m[1]] === m[2];
		m = sel.match(/^\[([a-z-]+)\]$/);
		if (m) return m[1] in this.attrs;
		m = sel.match(/^\.([a-z-]+)$/);
		if (m) return (this.className || "").split(/\s+/).includes(m[1]);
		throw new Error("unsupported selector " + sel);
	}
	querySelector(sel) {
		return this.all().find((e) => e.match(sel)) || null;
	}
	querySelectorAll(sel) {
		return this.all().filter((e) => e.match(sel));
	}
	addEventListener(type, fn) {
		(this.listeners[type] = this.listeners[type] || []).push(fn);
	}
	blur() {
		this.blurred += 1;
	}
	set outerHTML(html) {
		this.outer = html;
	}
}

function makeCell(root, code, level, qty) {
	const cell = new El({ "data-ro-item": code }, root);
	const l = new El({ "data-ro-field": "level", "data-ro-shown": level }, cell);
	l.value = level;
	const q = new El({ "data-ro-field": "qty", "data-ro-shown": qty }, cell);
	q.value = qty;
	const s = new El({}, cell);
	s.className = "ro-status";
	return { cell, l, q, s };
}

function fire(root, type, target, extra) {
	const event = Object.assign({ target, key: "", prevented: false, preventDefault() { this.prevented = true; } }, extra || {});
	(root.listeners[type] || []).forEach((fn) => fn(event));
	return event;
}

test("dom: change on a box commits that item, Enter leaves the box, Escape reverts", async () => {
	const root = new El();
	const { l, q } = makeCell(root, "A", "4", "12");
	const world = makeWorld();
	const ed = new ReorderEditor(Object.assign(world.env, { read: (code) => dom.read_boxes(root, code) }));
	await ed.load(BIN, ["A"]);
	const entered = [];
	dom.bind(root, ed, { after_enter: (code, box) => entered.push([code, box === q || box === l]) });
	l.value = "6";
	fire(root, "change", l);
	await settle();
	check("a write from the boxes", [writes(world).length, writes(world)[0].args.level, writes(world)[0].args.qty], [1, 6, 12]);
	const enter = fire(root, "keydown", q, { key: "Enter" });
	check("Enter is not a form submit", enter.prevented, true);
	check("Enter leaves the box", q.blurred, 1);
	check("and hands focus to the page own box", entered, [["A", true]]);
	const other = new El({}, root);
	fire(root, "change", other);
	fire(root, "keydown", other, { key: "Enter" });
	check("other elements are ignored", writes(world).length, 1);
	ed.status.A = { state: "error", message: "x" };
	fire(root, "keydown", l, { key: "Escape" });
	check("Escape clears the sentence and repaints with force", [ed.view("A").status, world.painted.at(-1)], [null, ["A", true]]);
});

test("dom: paint never overwrites a focused box, and keeps text a refusal left in its box", async () => {
	const root = new El();
	const { l, q, s } = makeCell(root, "A", "4", "12");
	const world = makeWorld();
	const ed = new ReorderEditor(world.env);
	await ed.load(BIN, ["A"]);
	const paint = (code, active, force) => dom.paint(root, ed, esc, null, code, active, force, { A: "Pump" });

	// the person typed 6 in the level box and moved to quantity, and the save is refused
	l.value = "6";
	ed.status.A = { state: "error", message: "Row #1: nope" };
	paint("A", q);
	check("typed text stays in an unfocused box after a refusal", l.value, "6");
	check("sentence shown", s.textContent, "Row #1: nope");
	check("status class", s.className, "ro-status ro-error");

	// the server's number changes (a reload after a conflict): an unfocused box takes it
	ed.entries.A.level = 9;
	paint("A", q);
	check("unfocused box takes the new number", [l.value, l.getAttribute("data-ro-shown")], ["9", "9"]);

	// the same, but the person is in the box
	ed.entries.A.qty = 30;
	q.value = "31";
	paint("A", q);
	check("focused box keeps what is typed", q.value, "31");
	paint("A", null);
	check("once they leave, the number is theirs to see", q.value, "30");

	// Escape forces
	l.value = "77";
	paint("A", l, true);
	check("force puts the box back even with focus", l.value, "9");
	ed.status.A = null;
	paint("A", null);
	check("status line emptied", [s.textContent, s.className], ["", "ro-status"]);
});

test("dom: a cell with no boxes yet is redrawn whole once it can have them", async () => {
	const root = new El();
	const cell = new El({ "data-ro-item": "A" }, root);
	const world = makeWorld();
	const ed = new ReorderEditor(world.env);
	await ed.load(BIN, ["A"]);
	dom.paint(root, ed, esc, null, "A", null, false, { A: "Pump" });
	check("redrawn with its boxes", cell.outer.includes('data-ro-field="level"') && cell.outer.includes('data-ro-field="qty"'), true);
	check("with the numbers", cell.outer.includes('value="4"') && cell.outer.includes('value="12"'), true);
	check("with the item's name", cell.outer.includes("Reorder at: Pump") && cell.outer.includes("Buy: Pump"), true);
});

// ------------------------------------------------------------------ run

for (const { name, fn } of tests) {
	const before = failures;
	try {
		await fn();
	} catch (e) {
		failures += 1;
		console.log(`  FAIL ${name}: threw ${e && e.stack}`);
	}
	console.log(`${failures === before ? "ok  " : "FAIL"} ${name}`);
}
console.log(`${checks} checks, ${failures} failed`);
if (failures) process.exit(1);
console.log(`${checks} checks passed`);
