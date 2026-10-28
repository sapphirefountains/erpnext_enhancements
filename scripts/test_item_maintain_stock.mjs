// "Turn on Maintain Stock" on the Item form (v1.754.0): public/js/item_maintain_stock.js run against a
// stubbed Desk. Run by tests/test_maintain_stock.py (node on PATH), or directly:
//   node scripts/test_item_maintain_stock.mjs
// What it pins: the button (once, for the roles, never for a service account, without write
// permission, on a stock item or an asset); the dialog (checks escaped, links to the documents,
// unreadable ones counted, Close and no switch when one fails); the confirm (switch, reload, next
// steps escaped, a prefilled Stock Reconciliation reusing the blank row); and Save after ticking
// the box: stopped and the dialog opened ONLY when ERPNext's form would refuse, the person's save
// resumed after the switch with the next steps (a dialog of their own, never frappe.msgprint)
// shown once the save's request settles, or at once when validation stopped it first, and
// untouched when not locked, unanswered, offline or slower than the quiet check's time limit.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = fs.readFileSync(
	process.argv[2] || path.join(HERE, "..", "erpnext_enhancements", "public", "js", "item_maintain_stock.js"),
	"utf8"
);
const log = [];
let handlers = null;
let lastDialog = null;
let lastMsg = null;
let roles = ["Stock Manager"];
let checkAnswer = null;
let turnOnAnswer = null;
let fetchAnswer = null;
const timers = [];
const dialogs = [];
// frappe.request's in-flight count and its after_ajax queue.
const ajax = { count: 0, waiting: [] };
function finishAjax() {
	ajax.count = 0;
	ajax.waiting.splice(0).forEach((fn) => fn());
}
function runZeroTimers() {
	for (const t of timers) {
		if (t.ms === 0 && !t.cleared && !t.ran) {
			t.ran = true;
			t.fn();
		}
	}
}
const nextSteps = () => dialogs.find((d) => d.opts.title === "Maintain Stock is on");

function makeFrappe() {
	const frappe = {
		session: { user: "buyer@example.com" },
		user: { has_role: (r) => (Array.isArray(r) ? r : [r]).some((x) => roles.includes(x)) },
		utils: {
			escape_html: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"),
			get_form_link: (dt, name) => `/desk/${dt.toLowerCase().replace(/ /g, "-")}/${encodeURIComponent(name)}`,
		},
		ui: {
			form: { on: (dt, h) => { assert.equal(dt, "Item"); handlers = h; } },
			Dialog: class {
				constructor(opts) {
					this.opts = opts;
					this.fields_dict = {};
					for (const f of opts.fields || []) {
						this.fields_dict[f.fieldname] = { $wrapper: { content: "", html(v) { this.content = v; } } };
					}
					this.hidden = false;
					lastDialog = this;
					dialogs.push(this);
				}
				show() { log.push("dialog.show"); }
				hide() { this.hidden = true; log.push("dialog.hide"); }
				get_primary_btn() { return { prop: () => log.push("primary.disabled") }; }
			},
		},
		call(opts) {
			log.push(["call", opts.method.split(".").pop(), opts.type || "POST"]);
			const answer = opts.method.endsWith(".check") ? checkAnswer : turnOnAnswer;
			return Promise.resolve(answer === null ? null : { message: answer });
		},
		msgprint(opts) { lastMsg = opts; log.push("msgprint"); return { hide: () => log.push("msg.hide") }; },
		show_alert(o) { log.push(["alert", o.message]); },
		after_ajax(fn) {
			if (ajax.count) ajax.waiting.push(fn);
			else fn();
		},
		model: {
			can_create: () => true,
			set_value: (dt, dn, values) => log.push(["model.set_value", dt, dn, values]),
		},
		route_hooks: {},
		new_doc: (dt, opts) => { log.push(["new_doc", dt, opts]); },
		validated: true,
	};
	return frappe;
}

const frappe = makeFrappe();
const ctx = {
	window: {},
	frappe,
	__: (s, args) => (args ? s.replace(/\{(\d+)\}/g, (_, i) => args[i]) : s),
	cint: (v) => parseInt(v || 0, 10) || 0,
	fetch: (url, opts) => {
		log.push(["fetch", url]);
		if (fetchAnswer === "throw") return Promise.reject(new Error("offline"));
		if (fetchAnswer === "hang") {
			// Never answers; only an abort ends it.
			return new Promise((_, reject) => {
				if (opts && opts.signal) opts.signal.addEventListener("abort", () => reject(new Error("aborted")));
			});
		}
		return Promise.resolve({ ok: true, json: async () => ({ message: fetchAnswer }) });
	},
	setTimeout: (fn, ms) => {
		timers.push({ fn, ms, cleared: false });
		return timers.length;
	},
	clearTimeout: (id) => {
		if (timers[id - 1]) timers[id - 1].cleared = true;
	},
	AbortController,
	Promise,
	console,
	encodeURIComponent,
	JSON,
};
vm.createContext(ctx);
vm.runInContext(SRC, ctx);
vm.runInContext(SRC, ctx); // loaded twice: registers once
assert.ok(handlers, "registered");

function makeFrm(doc, dirty = false) {
	return {
		doc: Object.assign({ name: "TST-PUMP-100", is_stock_item: 0, is_fixed_asset: 0 }, doc),
		is_new: () => false,
		is_dirty() { return dirty; },
		has_perm: () => true,
		buttons: [],
		add_custom_button(label, fn, group) { this.buttons.push({ label, fn, group }); },
		refresh_field: (f) => log.push(["refresh_field", f]),
		reload_doc: () => { log.push("reload_doc"); return Promise.resolve(); },
		save: () => { log.push("frm.save"); return Promise.resolve(); },
	};
}
const tick = () => new Promise((r) => setTimeout(r, 0));

const passing = {
	item_code: "TST-PUMP-100",
	checks: [{ key: "still_off", label: "Maintain Stock is still off", ok: true, detail: "", examples: [] }],
	can_switch: true,
	receipts: { count: 1, examples: [] },
	locked_by: { doctype: "Purchase Receipt", name: "PR-T-1" },
};
const switched = {
	item_code: "TST-PUMP-100", is_stock_item: 1, modified: "2026-10-28 10:00:01", company: "Test Co",
	account: "5119 - Stock Adjustment - TC", account_is_company_default: false, warehouse: "Bin - TC",
	next_steps: "Maintain Stock is on <b>x</b>.",
};

// 1. Button: shown once for a role holder on a non-stock item; not for others / stock items / assets.
let frm = makeFrm({});
handlers.refresh(frm);
assert.equal(frm.buttons.length, 1);
assert.equal(frm.buttons[0].group, "Actions");
assert.equal(frm.__sf_saved_stock, 0);
roles = ["Purchase User"];
let other = makeFrm({});
handlers.refresh(other);
assert.equal(other.buttons.length, 0);
roles = ["Item Manager"];
for (const d of [{ is_stock_item: 1 }, { is_fixed_asset: 1 }]) {
	const f = makeFrm(d);
	handlers.refresh(f);
	assert.equal(f.buttons.length, 0);
}
// A service account, whatever it holds, and a person who can't write the Item see no button.
roles = ["Stock Manager", "Item Manager"];
for (const user of ["Administrator", "triton@sapphirefountains.com"]) {
	frappe.session.user = user;
	const f = makeFrm({});
	handlers.refresh(f);
	assert.equal(f.buttons.length, 0, user);
}
frappe.session.user = "buyer@example.com";
const readOnly = makeFrm({});
readOnly.has_perm = (ptype) => ptype !== "write";
handlers.refresh(readOnly);
assert.equal(readOnly.buttons.length, 0, "no write permission");
roles = ["Item Manager"];

// 2. Button path: check (GET) -> dialog -> confirm -> turn_on -> reload -> next steps -> reconciliation.
checkAnswer = passing;
turnOnAnswer = switched;
log.length = 0;
frm.buttons[0].fn();
await tick(); await tick();
assert.deepEqual(log[0], ["call", "check", "GET"]);
assert.ok(lastDialog && lastDialog.fields_dict.checks.$wrapper.content.includes("Passes"));
assert.ok(lastDialog.fields_dict.checks.$wrapper.content.includes('href="/desk/purchase-receipt/PR-T-1"'));
assert.equal(lastDialog.opts.primary_action_label, "Turn on Maintain Stock");
lastDialog.opts.primary_action();
await tick(); await tick(); await tick();
assert.ok(log.some((l) => Array.isArray(l) && l[1] === "turn_on"));
assert.ok(log.includes("reload_doc"));
assert.ok(!log.includes("msgprint"), "never frappe.msgprint: it would share frappe.msg_dialog");
const steps = nextSteps();
assert.ok(steps, "next steps shown");
assert.ok(steps.fields_dict.steps.$wrapper.content.includes("&lt;b&gt;"), "next steps escaped");
assert.equal(steps.opts.primary_action_label, "Open a Stock Reconciliation");
assert.equal(frm.__sf_saved_stock, 1);
steps.opts.primary_action();
assert.ok(steps.hidden);
const nd = log.find((l) => Array.isArray(l) && l[0] === "new_doc");
assert.deepEqual(JSON.parse(JSON.stringify(nd[2])), { purpose: "Stock Reconciliation", company: "Test Co", expense_account: "5119 - Stock Adjustment - TC" });
// The new form loads: the hook fills the blank first row.
const rows = [{ doctype: "Stock Reconciliation Item", name: "row-1" }];
const reco = { doctype: "Stock Reconciliation", is_new: () => true, doc: { items: rows, expense_account: "5119 - Stock Adjustment - TC" },
	set_value: () => log.push("reco.set_value"), add_child: () => { throw new Error("should reuse blank row"); },
	refresh_field: () => {} };
frappe.route_hooks.after_load(reco);
const sv = log.find((l) => Array.isArray(l) && l[0] === "model.set_value");
assert.deepEqual(JSON.parse(JSON.stringify(sv.slice(1))), ["Stock Reconciliation Item", "row-1", { item_code: "TST-PUMP-100", warehouse: "Bin - TC" }]);

// 2b. Documents the person can't open are counted, and an unreadable "locked by" stays unnamed.
checkAnswer = Object.assign({}, passing, {
	locked_by: { doctype: null, name: null },
	checks: [{ key: "x", label: "L", ok: true, detail: "", examples: [{ doctype: "Sales Invoice", name: "SI-T-2" }], hidden_examples: 2 }],
});
frm = makeFrm({});
handlers.refresh(frm);
frm.buttons[0].fn();
await tick(); await tick();
assert.ok(lastDialog.fields_dict.checks.$wrapper.content.includes("2 you can&#39;t open") ||
	lastDialog.fields_dict.checks.$wrapper.content.includes("2 you can't open"));
assert.ok(lastDialog.fields_dict.checks.$wrapper.content.includes("a document you can"));
assert.ok(lastDialog.fields_dict.checks.$wrapper.content.includes('href="/desk/sales-invoice/SI-T-2"'));

// 3. Failing checks: dialog says Close, confirm does not call turn_on.
checkAnswer = Object.assign({}, passing, { can_switch: false, checks: [{ key: "x", label: "L", ok: false, detail: "D <i>", examples: [] }] });
log.length = 0;
frm = makeFrm({});
handlers.refresh(frm);
frm.buttons[0].fn();
await tick(); await tick();
assert.equal(lastDialog.opts.primary_action_label, "Close");
assert.ok(lastDialog.fields_dict.checks.$wrapper.content.includes("D &lt;i&gt;"));
lastDialog.opts.primary_action();
await tick();
assert.ok(!log.some((l) => Array.isArray(l) && l[1] === "turn_on"));

// 4. Save path: box ticked on a locked item -> save stopped, dialog opens; confirm resumes the save.
log.length = 0;
frm = makeFrm({});
handlers.refresh(frm);
frm.doc.is_stock_item = 1;
fetchAnswer = passing;
frappe.validated = true;
await handlers.validate(frm);
assert.equal(frappe.validated, false);
assert.ok(log.some((l) => Array.isArray(l) && l[0] === "fetch" && l[1].includes("maintain_stock.check?item_code=TST-PUMP-100")));
const switchDialog = lastDialog;
const dirtyFrm = frm;
dirtyFrm.is_dirty = () => true;
// The resumed save validates on a promise chain, then sends its request, which stays in flight.
// Its promise never settles (as when the server refuses and nothing resolves it).
dirtyFrm.save = () => {
	log.push("frm.save");
	Promise.resolve().then(() => { ajax.count = 1; });
	return new Promise(() => {});
};
log.length = 0;
dialogs.length = 0;
switchDialog.opts.primary_action();
await tick(); await tick(); await tick();
assert.equal(dirtyFrm.doc.modified, "2026-10-28 10:00:01");
assert.ok(log.includes("frm.save"));
assert.equal(nextSteps(), undefined, "not before the save has even sent its request");
runZeroTimers();
assert.equal(nextSteps(), undefined, "not while the save's request is in flight");
finishAjax();
assert.ok(nextSteps(), "next steps once the save's request settles");
assert.ok(!log.includes("msgprint"));
assert.ok(!log.includes("reload_doc"));

// The same, when the form's own validation stops the save before any request: shown at once.
dialogs.length = 0;
dirtyFrm.save = () => {
	log.push("frm.save");
	return new Promise(() => {});
};
{
	checkAnswer = passing;
	fetchAnswer = passing;
	dirtyFrm.__sf_saved_stock = 0;
	dirtyFrm.doc.is_stock_item = 1;
	frappe.validated = true;
	await handlers.validate(dirtyFrm);
	const again = lastDialog;
	dialogs.length = 0;
	again.opts.primary_action();
	await tick(); await tick(); await tick();
	assert.equal(nextSteps(), undefined, "asked one macrotask later");
	runZeroTimers();
	assert.ok(nextSteps(), "no request in flight: shown at once, never hangs");
}

// 5. Save path, not locked / check fails / no role / box already on: save untouched.
for (const [answer, why] of [[Object.assign({}, passing, { locked_by: false }), "not locked"], [null, "no answer"], ["throw", "offline"]]) {
	frm = makeFrm({});
	handlers.refresh(frm);
	frm.doc.is_stock_item = 1;
	fetchAnswer = answer;
	frappe.validated = true;
	await handlers.validate(frm);
	assert.equal(frappe.validated, true, why);
}
// A check that never answers: the save goes on once the time limit passes.
frm = makeFrm({});
handlers.refresh(frm);
frm.doc.is_stock_item = 1;
fetchAnswer = "hang";
frappe.validated = true;
timers.length = 0;
let settled = false;
const pending = Promise.resolve(handlers.validate(frm)).then(() => { settled = true; });
await tick();
assert.equal(settled, false, "waits for the check at first");
const limit = timers.find((t) => !t.cleared);
assert.ok(limit && limit.ms > 0 && limit.ms <= 5000, "a time limit of a few seconds");
limit.fn();
await pending;
assert.equal(frappe.validated, true, "timed out: save untouched");

frm = makeFrm({ is_stock_item: 1 });
handlers.refresh(frm);
log.length = 0;
assert.equal(handlers.validate(frm), undefined);
assert.ok(!log.length, "no request when the box was already on");

console.log("item_maintain_stock: all checks passed");
