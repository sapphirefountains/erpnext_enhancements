#!/usr/bin/env node
/**
 * The Purchase Order's package banner (v1.819.0). Runs the REAL public/js/po_package_tracking.js in a
 * vm against a stubbed Desk.
 *
 * Holds: the form is never dirtied and frm.doc is never written; the read is a GET and nothing is
 * asked on a new or canceled order; the banner escapes what it shows, opens carrier pages in a new
 * tab with rel="noopener noreferrer" and never draws a javascript: link; the purchaser's Expected
 * Delivery Date is shown beside the carrier's estimate, not replaced by it; Add, Remove and Refresh
 * appear only for the caller the server says may use them; and no route change happens.
 *
 * Run: node scripts/test_po_package_tracking.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SCRIPT = path.join(__dirname, "..", "erpnext_enhancements", "public", "js", "po_package_tracking.js");

let checks = 0;
let failures = 0;
function check(label, actual, expected) {
	checks += 1;
	if (actual !== expected) {
		failures += 1;
		console.log(`  FAIL ${label}\n    expected ${JSON.stringify(expected)}\n    actual   ${JSON.stringify(actual)}`);
	}
}

// --------------------------------------------------------------------------- the stub Desk

const handlers = {};
const calls = [];
const routes = [];
let nextAnswer = { message: {} };

function jq() {
	// A chainable jQuery stand-in: enough for slot(), draw() and the click wiring.
	const node = {
		length: 1,
		_html: "",
		children() {
			return node;
		},
		prependTo() {
			return node;
		},
		html(value) {
			node._html = value;
			return node;
		},
		empty() {
			node._html = "";
			return node;
		},
		find() {
			return { on() {} };
		},
		on() {
			return node;
		},
		attr() {
			return "";
		},
		prop() {
			return node;
		},
		text() {
			return node;
		},
	};
	return node;
}
const theSlot = jq();

const escapeHtml = (s) =>
	String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");

const sandbox = {
	console,
	document: { getElementById: () => null, createElement: () => ({}), head: { appendChild() {} } },
	__: (text, args) => (args || []).reduce((out, arg, i) => out.replace(`{${i}}`, arg), text),
	$: () => theSlot,
	frappe: {
		provide(name) {
			const parts = name.split(".");
			let obj = sandbox;
			for (const part of parts) obj = obj[part] = obj[part] || {};
		},
		utils: { escape_html: escapeHtml },
		datetime: { str_to_user: (d) => `D(${d})`, prettyDate: (d) => `P(${d})` },
		call(opts) {
			calls.push(opts);
			return { then: (ok) => ok(nextAnswer) };
		},
		set_route: (...a) => routes.push(a),
		ui: {
			form: {
				on(doctype, events) {
					handlers[doctype] = events;
				},
			},
		},
		show_alert() {},
		confirm(message, yes) {
			yes();
		},
	},
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(SCRIPT, "utf8"), sandbox, { filename: SCRIPT });
const ns = sandbox.erpnext_enhancements.package_tracking;

function makeFrm(doc, isNew) {
	const frm = {
		doc,
		dirtied: 0,
		layout: { wrapper: {} },
		is_new: () => isNew,
		dirty() {
			frm.dirtied += 1;
		},
		set_value() {
			frm.dirtied += 1;
			return Promise.resolve();
		},
		reload_doc() {
			frm.dirtied += 1;
		},
	};
	return frm;
}

// --------------------------------------------------------------------------- the form handler

check("registers on Purchase Order", typeof (handlers["Purchase Order"] || {}).refresh, "function");

const doc = { name: "PO-TEST-1", docstatus: 1, custom_expected_delivery_date: "2026-11-20" };
const before = JSON.stringify(doc);
const frm = makeFrm(doc, false);
nextAnswer = {
	message: {
		packages: [
			{
				name: "tp-1",
				tracking_number: "1Z0000000000000001",
				resolved_carrier: "UPS",
				tracking_url: "https://www.ups.com/track?loc=en_US&tracknum=1Z0000000000000001",
				status: "In Transit",
				estimated_delivery: "2026-11-16",
				last_checked: "2026-11-13 09:00:00",
			},
		],
		live_carriers: ["UPS"],
		live: true,
		can_add: true,
		can_remove: true,
		can_refresh: true,
	},
};
handlers["Purchase Order"].refresh(frm);
check("one read", calls.length, 1);
check("the read is a GET", calls[0].type, "GET");
check("the read names the order", calls[0].args.purchase_order, "PO-TEST-1");
check("the form is never dirtied", frm.dirtied, 0);
check("frm.doc is never written", JSON.stringify(doc), before);
check("no route change", routes.length, 0);
const html = theSlot._html;
check("draws the banner", html.includes("Packages on their way"), true);
check("purchaser's date shown", html.includes("Your expected date: D(2026-11-20)"), true);
check("carrier's estimate shown beside it", html.includes("Carrier says it arrives D(2026-11-16)"), true);
check("plain words, not the code", html.includes("On its way"), true);
check("carrier link opens a new tab", html.includes('target="_blank" rel="noopener noreferrer"'), true);
check("last checked", html.includes("checked P(2026-11-13 09:00:00)"), true);
check("add button", html.includes("ee-pkg-add"), true);
check("refresh button", html.includes("ee-pkg-refresh"), true);
check("remove button", html.includes("ee-pkg-remove"), true);

calls.length = 0;
handlers["Purchase Order"].refresh(makeFrm({ name: "new-purchase-order-1", docstatus: 0 }, true));
check("nothing asked on a new order", calls.length, 0);
nextAnswer = {
	message: {
		enabled: true,
		packages: [{ name: "tp-9", tracking_number: "1Z0000000000000009", resolved_carrier: "UPS", status: "In Transit" }],
		live_carriers: ["UPS"],
		can_add: false,
		can_remove: false,
		can_refresh: false,
	},
};
handlers["Purchase Order"].refresh(makeFrm({ name: "PO-TEST-X", docstatus: 2 }, false));
check("a canceled order is asked (its packages stay readable)", calls.length, 1);
check("a canceled order's packages are shown", theSlot._html.includes("1Z0000000000000009"), true);
check("read-only on a canceled order", /ee-pkg-(add|remove|refresh)/.test(theSlot._html), false);

calls.length = 0;
nextAnswer = { message: { enabled: false, packages: [], can_add: false } };
handlers["Purchase Order"].refresh(makeFrm({ name: "PO-TEST-1", docstatus: 1 }, false));
check("off means off: nothing drawn", theSlot._html, "");

// --------------------------------------------------------------------------- banner_html

check("nothing for someone who may not add and has no rows", ns.banner_html({ packages: [], can_add: false }, {}), "");
const invite = ns.banner_html({ enabled: true, packages: [], can_add: true }, {});
check("an order with none: one small Add button", invite.includes("ee-pkg-add") && invite.includes("btn-xs"), true);
check("an order with none: no empty box", invite.includes("ee-pkg-card") || invite.includes("No tracking numbers yet"), false);
check("an order that cannot take one: nothing", ns.banner_html({ enabled: true, packages: [], can_add: false }, {}), "");
check("feature off: nothing even with rows", ns.banner_html({ enabled: false, packages: [{ name: "x" }], can_add: true }, {}), "");
check(
	"a guessed carrier says so",
	ns.describe({ status: "Not Checked Yet", resolved_carrier: "FedEx", carrier_guessed: 1 }, ["FedEx"]).carrier,
	"FedEx (guessed)"
);

const hostile = ns.banner_html(
	{
		packages: [
			{
				name: "tp-2",
				tracking_number: '<img src=x onerror="alert(1)">',
				resolved_carrier: "Other",
				tracking_url: "javascript:alert(1)",
				status: "Not Tracked",
				status_description: "<b>hi</b>",
			},
		],
		live_carriers: [],
		can_add: false,
		can_remove: false,
		can_refresh: false,
	},
	{}
);
check("escapes the number", hostile.includes("<img"), false);
check("never draws a javascript: link", hostile.includes("javascript:"), false);
check("no buttons without rights", /ee-pkg-(add|remove|refresh)/.test(hostile), false);

const delivered = ns.describe(
	{ status: "Delivered", resolved_carrier: "FedEx", delivered_on: "2026-11-12 14:14:00", estimated_delivery: "2026-11-12" },
	["FedEx"]
);
check("delivered shows the carrier's local time", delivered.when, "Delivered D(2026-11-12) at 14:14");

const off = ns.banner_html(
	{ packages: [{ name: "tp-3", tracking_number: "100000000001", resolved_carrier: "FedEx", status: "Not Checked Yet" }], live_carriers: [], can_add: true },
	{}
);
check("says when live status is off", off.includes("switched off"), true);
check("no refresh while off", off.includes("ee-pkg-refresh"), false);

const stopped = ns.describe(
	{ status: "In Transit", resolved_carrier: "UPS", polling_stopped: 1, stop_reason: "No news from the carrier for three weeks." },
	["UPS"]
);
check("why it stopped", stopped.note, "No news from the carrier for three weeks.");

const other = ns.describe({ status: "Not Tracked", resolved_carrier: "Other", tracking_url: "http://example.com/t" }, []);
check("Other keeps an http link", other.url, "http://example.com/t");
check("Other is not 'checked'", other.checked, "");

// Review round 2, S3: a guessed carrier that answered stays marked, says where it is going, and how to correct it.
const guessed = ns.banner_html(
	{
		enabled: true,
		packages: [
			{
				name: "tp-g",
				tracking_number: "100000000001",
				resolved_carrier: "FedEx",
				carrier_guessed: 1,
				status: "In Transit",
				last_success: "2026-11-13 09:00:00",
				destination: "Somewhere Else, NV, US",
			},
		],
		live_carriers: ["FedEx"],
	},
	{}
);
check("a guessed row stays marked after an answer", guessed.includes("FedEx (guessed)"), true);
check("a guessed row says where it is going", guessed.includes("Going to Somewhere Else, NV, US"), true);
check("a guessed row says how to correct it", guessed.includes("The carrier was guessed from the number"), true);
const sure = ns.banner_html(
	{ enabled: true, packages: [{ name: "tp-s", tracking_number: "1Z0000000000000001", resolved_carrier: "UPS",
		status: "In Transit", destination: "Somewhere Else, NV, US" }], live_carriers: ["UPS"] },
	{}
);
check("a certain carrier gets no guess note", sure.includes("guessed"), false);

console.log(`${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
