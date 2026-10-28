// "Turn on Maintain Stock" on the Item form (v1.754.0): public/js/item_maintain_stock.js run against a
// stubbed Desk. Run by tests/test_maintain_stock.py (node on PATH), or directly:
//   node scripts/test_item_maintain_stock.mjs
// What it pins: the button (once, for the roles, never on a stock item or an asset); the dialog
// (checks escaped, links to the documents, Close and no switch when one fails); the confirm (switch,
// reload, next steps escaped, a prefilled Stock Reconciliation reusing the blank row); and Save
// after ticking the box: stopped and the dialog opened ONLY when ERPNext's form would refuse, the
// person's save finished after the switch, and untouched when not locked, unanswered or offline.
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

function makeFrappe() {
	const frappe = {
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
					const html = { content: "", html(v) { this.content = v; } };
					this.fields_dict = { checks: { $wrapper: html } };
					this.hidden = false;
					lastDialog = this;
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
	fetch: async (url) => {
		log.push(["fetch", url]);
		if (fetchAnswer === "throw") throw new Error("offline");
		return { ok: true, json: async () => ({ message: fetchAnswer }) };
	},
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
assert.ok(log.includes("msgprint"));
assert.ok(lastMsg.message.includes("&lt;b&gt;"), "next steps escaped");
assert.equal(frm.__sf_saved_stock, 1);
lastMsg.primary_action.action();
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
const dirtyFrm = frm;
dirtyFrm.is_dirty = () => true;
lastDialog.opts.primary_action();
await tick(); await tick(); await tick();
assert.equal(dirtyFrm.doc.modified, "2026-10-28 10:00:01");
assert.ok(log.includes("frm.save"));
assert.ok(!log.includes("reload_doc"));

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
frm = makeFrm({ is_stock_item: 1 });
handlers.refresh(frm);
log.length = 0;
assert.equal(handlers.validate(frm), undefined);
assert.ok(!log.length, "no request when the box was already on");

console.log("item_maintain_stock: all checks passed");
