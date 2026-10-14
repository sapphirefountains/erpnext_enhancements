"""Bench-free tests for the guided entry page (form kit P4-B).

The page (``enhancements_core/page/guided_entry``) keeps its decisions in pure ES modules under
``public/js/form_kit/``: routes, which steps apply, what is left to fill per step, the phone
keyboard per field, the text-list table, conflict diffing and the one save queue. Those run here
under node, copied to a temp dir beside a ``package.json`` that says ``"type": "module"`` (the
repo's own does not, and node will not load ES module syntax from a ``.js`` file without it), the
way ``test_desk_display`` runs ``desk_display.js``. Skips the node half cleanly when ``node`` is
not on PATH.

The rest is static: the Page record (name, module, roles), the page script's wiring (every request
through ``sf_kit.call`` quiet, steps moved with ``frappe.set_route``, a ``beforeunload`` warning, no
``/app/`` path), the narrow opt-out added to ``field_description_icons.js``, the stylesheet's
tokens, and two copies held to their originals (``rule_applies`` to ``form_kit/steps.py``'s
``applies``, ``STREAM_PILLAR`` to ``print_style``).

Run: python -m unittest erpnext_enhancements.tests.test_guided_entry -v
"""

import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
JS = APP / "public" / "js"
FORM_KIT_JS = JS / "form_kit"
BUNDLE = JS / "guided_entry.bundle.js"
CSS = APP / "public" / "css" / "guided_entry.bundle.css"
PAGE_DIR = APP / "enhancements_core" / "page" / "guided_entry"
PAGE_JSON = PAGE_DIR / "guided_entry.json"
PAGE_JS = PAGE_DIR / "guided_entry.js"
ICONS = JS / "global_enhancements" / "field_description_icons.js"
OPPORTUNITY_JS = JS / "crm_enhancements" / "opportunity.js"
DEFAULT_JS = JS / "guided_default.js"
DESK_BUNDLE = JS / "erpnext_enhancements.bundle.js"
STEPS_PY = APP / "form_kit" / "steps.py"


def _text(path):
	return path.read_text(encoding="utf-8")


def _code(path):
	"""The script with comments stripped: an absence check must not be satisfied (or broken) by
	the comment that explains it."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", _text(path))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def _python_applies():
	"""form_kit/steps.py ``applies``, executed on its own (the module imports frappe)."""
	tree = ast.parse(_text(STEPS_PY))
	node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "applies")
	namespace = {}
	exec(compile(ast.Module(body=[node], type_ignores=[]), str(STEPS_PY), "exec"), namespace)
	return namespace["applies"]


STREAM = {"field": "custom_value_stream", "row_field": "value_stream", "in": ["Design"]}
APPLIES_CASES = [
	[None, {}],
	[STREAM, {}],
	[STREAM, {"custom_value_stream": []}],
	[STREAM, {"custom_value_stream": [{"value_stream": "Build"}]}],
	[STREAM, {"custom_value_stream": [{"value_stream": "Build"}, {"value_stream": "Design"}]}],
	[{"field": "status", "in": ["Lost", "Closed Won"]}, {"status": "Lost"}],
	[{"field": "status", "in": ["Lost"]}, {"status": "Open"}],
	[{"field": "status", "in": ["Lost"]}, {"status": None}],
	[{"field": "rows", "in": [None]}, {"rows": [{"x": 1}]}],
	[{"field": "rows", "in": ["a"]}, {"rows": [{"x": "a"}]}],
	[{"field": "missing", "in": []}, {"missing": "anything"}],
]

STEPS = [
	{
		"key": "basics",
		"create": 1,
		"fields": [
			{
				"fieldname": "party_name",
				"fieldtype": "Dynamic Link",
				"options": "opportunity_from",
				"reqd": 1,
			},
			{"fieldname": "opportunity_from", "fieldtype": "Link", "options": "DocType", "reqd": 1},
			{"fieldtype": "Column Break", "fieldname": "cb1"},
			{"fieldname": "custom_value_stream", "fieldtype": "Table MultiSelect", "reqd": 1},
			{"fieldname": "naming_series", "fieldtype": "Select", "reqd": 1, "read_only": 1},
		],
	},
	{
		"key": "contact",
		"fields": [{"fieldname": "primary_contact", "fieldtype": "Link", "options": "Contact"}],
	},
	{
		"key": "scope_design",
		"applies": STREAM,
		"fields": [{"fieldname": "custom_design_customer_requests", "fieldtype": "Table"}],
	},
	{
		"key": "scope_build",
		"applies": {"field": "custom_value_stream", "row_field": "value_stream", "in": ["Build"]},
		"fields": [{"fieldname": "custom_build_customer_requests", "fieldtype": "Table"}],
	},
	{
		"key": "close",
		"fields": [
			{"fieldname": "status", "fieldtype": "Select"},
			{"fieldname": "party_name", "fieldtype": "Data"},
		],
	},
]

RUNNER = r"""
import { pathToFileURL } from "node:url";
const [dir, inputJson] = process.argv.slice(2);
const kit = await import(pathToFileURL(dir + "/form_kit/index.js").href);
const input = JSON.parse(inputJson);
const out = {};

// ---- routes
out.route = {
	bare: kit.parse_route(["guided-entry"]),
	other_page: kit.parse_route(["contract-builder", "X"]),
	not_array: kit.parse_route("guided-entry"),
	new_: kit.parse_route(["guided-entry", "Opportunity", "new"]),
	doctype_only: kit.parse_route(["guided-entry", "Opportunity"]),
	record: kit.parse_route(["guided-entry", "Opportunity", "CRM-OPP-2026-00123"]),
	step: kit.parse_route(["guided-entry", "Opportunity", "CRM-OPP-2026-00123", "scope"]),
	spaced: kit.parse_route(["guided-entry", "Sales Order", "SO/1", "items"]),
	build_new: kit.build_route("Opportunity", null, null),
	build_new_ignores_step: kit.build_route("Opportunity", "", "contact"),
	build_record: kit.build_route("Opportunity", "CRM-OPP-1", null),
	build_step: kit.build_route("Opportunity", "CRM-OPP-1", "budget"),
	build_none: kit.build_route("", null, null),
	key_new: kit.route_key({ doctype: "Opportunity", is_new: true, name: "", step: "x" }),
	key_step: kit.route_key({ doctype: "Opportunity", name: "A", step: "s" }),
	key_none: kit.route_key(null),
};
out.route.round_trip = ["basics", "", null].map((step) => kit.parse_route(kit.build_route("Opportunity", "N 1", step)));

// ---- applies, held to steps.py
out.applies = input.applies.map(([rule, values]) => kit.rule_applies(rule, values));

// ---- steps
const steps = input.steps;
const design = { custom_value_stream: [{ value_stream: "Design" }] };
const both = { custom_value_stream: [{ value_stream: "Design" }, { value_stream: "Build" }] };
out.steps = {
	active_none: kit.active_steps(steps, {}).map((s) => s.key),
	active_design: kit.active_steps(steps, design).map((s) => s.key),
	next_from_contact_none: kit.neighbor(steps, {}, "contact", 1),
	next_from_contact_both: kit.neighbor(steps, both, "contact", 1),
	prev_from_close_design: kit.neighbor(steps, design, "close", -1),
	next_from_inactive: kit.neighbor(steps, {}, "scope_design", 1),
	prev_from_first: kit.neighbor(steps, {}, "basics", -1),
	next_from_last: kit.neighbor(steps, {}, "close", 1),
	next_from_unknown: kit.neighbor(steps, {}, "nope", 1),
	resolve_empty: kit.resolve_step(steps, {}, ""),
	resolve_ok: kit.resolve_step(steps, design, "scope_design"),
	resolve_inactive: kit.resolve_step(steps, {}, "scope_design"),
	resolve_unknown: kit.resolve_step(steps, {}, "nope"),
	resolve_no_steps: kit.resolve_step([], {}, "x"),
	position_design: kit.position(steps, design, "close"),
	position_none: kit.position(steps, {}, "close"),
	field_steps: kit.field_steps(steps),
	missing: kit.missing_by_step(steps, [
		{ fieldname: "party_name", label: "Account" },
		{ fieldname: "custom_design_customer_requests", label: "Customer Requests" },
		{ fieldname: "status", label: "Status" },
		{ fieldname: "unreadable", label: "Hidden" },
	]),
	missing_required_empty: kit.missing_required(steps[0].fields, { custom_value_stream: [] }).map((f) => f.fieldname),
	missing_required_full: kit.missing_required(steps[0].fields, {
		party_name: "Smith",
		opportunity_from: "Customer",
		custom_value_stream: [{ value_stream: "Design" }],
	}).map((f) => f.fieldname),
	missing_required_blank: kit.missing_required(steps[0].fields, { party_name: "  ", opportunity_from: "Customer", custom_value_stream: [{}] }).map((f) => f.fieldname),
	editable: kit.editable_fieldnames(steps[0]),
};

// ---- fields
const registry = { fields: { Opportunity: { contact_mobile: "phone", contact_email: "email", website: "website" } } };
const mode = (df, doctype) => kit.inputmode_for(df, registry, doctype === undefined ? "Opportunity" : doctype);
out.inputmode = {
	phone_registered: mode({ fieldname: "contact_mobile", fieldtype: "Data" }),
	phone_by_parent: mode({ fieldname: "contact_mobile", fieldtype: "Data", parent: "Opportunity" }, null),
	email_registered: mode({ fieldname: "contact_email", fieldtype: "Data" }),
	website_registered: mode({ fieldname: "website", fieldtype: "Data" }),
	other_doctype: mode({ fieldname: "website", fieldtype: "Data" }, "Lead"),
	int_: mode({ fieldname: "n", fieldtype: "Int" }),
	float_: mode({ fieldname: "n", fieldtype: "Float" }),
	currency: mode({ fieldname: "opportunity_amount", fieldtype: "Currency" }),
	percent: mode({ fieldname: "p", fieldtype: "Percent" }),
	phone_type: mode({ fieldname: "p", fieldtype: "Phone" }),
	email_option: mode({ fieldname: "e", fieldtype: "Data", options: "Email" }),
	url_option: mode({ fieldname: "u", fieldtype: "Data", options: "URL" }),
	plain: mode({ fieldname: "x", fieldtype: "Data" }),
	link: mode({ fieldname: "x", fieldtype: "Link", options: "Customer" }),
	no_registry: kit.inputmode_for({ fieldname: "contact_mobile", fieldtype: "Data" }, null, "Opportunity"),
	none: kit.inputmode_for(null, registry, "Opportunity"),
};
const table = (child_fields, fieldtype = "Table") => ({ fieldname: "t", fieldtype, child_fields });
out.text_list = {
	long_text: kit.text_list_column(table([{ fieldname: "design_customer_requests", fieldtype: "Long Text" }])),
	data: kit.text_list_column(table([{ fieldname: "d", fieldtype: "Data" }])),
	small: kit.text_list_column(table([{ fieldname: "d", fieldtype: "Small Text" }])),
	text: kit.text_list_column(table([{ fieldname: "d", fieldtype: "Text" }])),
	editor: kit.text_list_column(table([{ fieldname: "d", fieldtype: "Text Editor" }])),
	link: kit.text_list_column(table([{ fieldname: "d", fieldtype: "Link" }])),
	two: kit.text_list_column(table([{ fieldname: "a", fieldtype: "Data" }, { fieldname: "b", fieldtype: "Data" }])),
	none: kit.text_list_column(table([])),
	multiselect: kit.text_list_column(table([{ fieldname: "value_stream", fieldtype: "Data" }], "Table MultiSelect")),
	not_table: kit.text_list_column({ fieldname: "x", fieldtype: "Data" }),
};
const rows = [
	{ name: "r1", idx: 1, c: "one" },
	{ name: "r2", idx: 2, c: "two" },
	{ name: "r3", idx: 3, c: "three" },
];
out.rows = {
	add: kit.add_row(rows, "c", ""),
	add_empty: kit.add_row(null, "c"),
	remove_middle: kit.remove_row(rows, 1),
	remove_out_of_range: kit.remove_row(rows, 9),
	move_up: kit.move_row(rows, 2, -1),
	move_down: kit.move_row(rows, 0, 1),
	move_first_up: kit.move_row(rows, 0, -1),
	move_last_down: kit.move_row(rows, 2, 1),
	set_text: kit.set_row_text(rows, 1, "c", "TWO"),
	untouched: rows,
	clean: kit.clean_rows(
		[
			{ name: "r1", idx: 1, c: "one", doctype: "X", parent: "P", __unedited: true },
			{ name: "abc12345", __islocal: true, idx: 2, c: "new" },
			{ name: "new-x-1", c: "newer" },
			{ c: "no name", other: 1 },
		],
		["c"]
	),
};
const steps_party = [{ key: "a", fields: [{ fieldname: "party_name", fieldtype: "Dynamic Link", options: "opportunity_from" }] }];
const party = kit.party_of(steps_party, { opportunity_from: "Customer", party_name: "CUST-1" });
out.party = {
	party,
	no_name: kit.party_of(steps_party, { opportunity_from: "Customer" }),
	no_field: kit.party_of([{ key: "a", fields: [] }], { party_name: "x" }),
	contact: kit.link_query({ fieldtype: "Link", options: "Contact" }, party),
	address: kit.link_query({ fieldtype: "Link", options: "Address" }, party),
	customer: kit.link_query({ fieldtype: "Link", options: "Customer" }, party),
	no_party: kit.link_query({ fieldtype: "Link", options: "Contact" }, null),
};

// ---- conflicts
out.same = {
	blanks: [kit.same_value(null, ""), kit.same_value(undefined, "  "), kit.same_value(null, [])],
	numbers: [kit.same_value(1000, "1000.0"), kit.same_value(1000, 1000.0), kit.same_value(0, null), kit.same_value("1", "1.0")],
	strings: [kit.same_value("a", "a"), kit.same_value("a", "b"), kit.same_value("a", "a ")],
	rows: [
		kit.same_value([{ name: "x", idx: 1, c: "one" }], [{ c: "one", __islocal: true, doctype: "T" }]),
		kit.same_value([{ c: "one" }], [{ c: "two" }]),
		kit.same_value([{ c: "one" }], [{ c: "one" }, { c: "two" }]),
		kit.same_value([{ c: "one" }, { c: "two" }], [{ c: "two" }, { c: "one" }]),
		kit.same_value([], ""),
		kit.same_value([{ c: "a" }], "a"),
	],
};
out.split = kit.split_conflict(
	{ a: "base", b: "base", c: "base", d: "base", e: [{ c: "x" }] },
	{ a: "mine", b: "mine", c: "same", d: "mine", e: [{ c: "y" }], f: "not reported" },
	{ a: "base", b: "theirs", c: "same", d: "theirs", e: [{ name: "r", c: "z" }] }
);
out.resolved = kit.resolve(out.split.resend, out.split.conflicts, { b: "theirs", e: "mine" });
out.value_text = [kit.value_text([{ name: "r", idx: 1, c: "one", d: "two" }, { c: "" }]), kit.value_text(null), kit.value_text(12)];

// ---- summary
out.summary = {
	pillars: Object.fromEntries(Object.keys(kit.STREAM_PILLAR).map((s) => [s, kit.pillar_for(s)])),
	unknown: kit.pillar_for("Consulting"),
	streams: kit.streams_of("Opportunity", {
		custom_value_stream: [{ value_stream: "Events" }, { value_stream: "Design" }, { value_stream: "Events" }, {}],
	}),
	lead: kit.lead_pillar("Opportunity", { custom_value_stream: [{ value_stream: "Service" }] }),
	lead_none: kit.lead_pillar("Opportunity", {}),
	lead_other_doctype: kit.lead_pillar("Lead", { custom_value_stream: [{ value_stream: "Service" }] }),
	model: kit.summary_model(
		"Opportunity",
		"CRM-OPP-1",
		{
			custom_opportunity_name: "Smith Residence - Pond",
			party_name: "Smith",
			opportunity_amount: "2500.5",
			status: "Open",
			custom_value_stream: [{ value_stream: "Build" }],
		},
		{ stage: "Qualification", done: 4, total: 6, missing: [{ fieldname: "x", label: "X" }] }
	),
	model_empty: kit.summary_model("Lead", "LEAD-1", {}, null),
};

// ---- the save queue
function timers() {
	let id = 0;
	const live = new Map();
	return {
		set_timeout: (fn, ms) => {
			id += 1;
			live.set(id, { fn, ms });
			return id;
		},
		clear_timeout: (handle) => live.delete(handle),
		fire() {
			const list = [...live.values()];
			live.clear();
			list.forEach((t) => t.fn());
		},
		delays: () => [...live.values()].map((t) => t.ms),
	};
}
function deferred() {
	let resolve;
	let reject;
	const promise = new Promise((a, b) => {
		resolve = a;
		reject = b;
	});
	return { promise, resolve, reject };
}
const settle = () => new Promise((r) => setTimeout(r, 0));
function harness(extra) {
	const t = timers();
	const calls = [];
	const states = [];
	const saved = [];
	const conflicts = [];
	const queue = kit.create_save_queue(
		Object.assign(
			{
				set_timeout: t.set_timeout,
				clear_timeout: t.clear_timeout,
				send: (values, modified) => {
					const d = deferred();
					calls.push({ values: JSON.parse(JSON.stringify(values)), modified, d });
					return d.promise;
				},
				on_state: (state) => states.push(state),
				on_saved: (result, sent) => saved.push({ result, sent }),
			},
			extra || {}
		)
	);
	queue.set_modified("m0");
	return { t, calls, states, saved, conflicts, queue };
}
const shape = (h) => ({
	calls: h.calls.map((c) => ({ values: c.values, modified: c.modified })),
	state: h.queue.state,
	pending: h.queue.pending(),
	unsaved: h.queue.has_unsaved(),
});
const q = {};

{
	// Coalescing, one request at a time, modified carried forward.
	const h = harness();
	h.queue.change("a", 1);
	h.queue.change("b", 2);
	h.queue.change("a", 3);
	q.delays = h.t.delays();
	q.before_timer = shape(h);
	h.t.fire();
	q.first_out = shape(h);
	h.queue.change("a", 4);
	h.queue.change("c", 5);
	let flushed = "no";
	const flushing = h.queue.flush().then(() => (flushed = "yes"), () => (flushed = "rejected"));
	await settle();
	q.while_in_flight = shape(h);
	h.calls[0].d.resolve({ modified: "m1", values: { a: 3, b: 2 } });
	await settle();
	q.second_out = shape(h);
	q.flushed_early = flushed;
	h.calls[1].d.resolve({ modified: "m2", values: { a: 4, c: 5 } });
	await flushing;
	await settle();
	q.done = Object.assign(shape(h), { flushed, modified: h.queue.modified, saved_sent: h.saved.map((s) => s.sent) });
	q.states = h.states;
}
{
	// Discard: back to the saved value, nothing to send.
	const h = harness();
	h.queue.change("a", 1);
	h.queue.discard("a");
	h.t.fire();
	await settle();
	q.discard = shape(h);
}
{
	// A refusal: put back under newer typing, waiting flush rejects, the next change retries.
	const h = harness();
	h.queue.change("a", 1);
	h.queue.change("b", 1);
	const flushing = h.queue.flush().then(() => "resolved", (e) => e);
	h.queue.change("a", 2);
	h.calls[0].d.reject({ status: 417, message: "Ranks are required", exc_type: "ValidationError" });
	q.error_flush = await flushing;
	q.error = shape(h);
	q.error_message = h.queue.error && h.queue.error.message;
	h.queue.change("c", 3);
	q.error_retry_scheduled = h.t.delays();
	h.t.fire();
	await settle();
	q.error_retry = shape(h);
}
{
	// A lapsed session: no autosave until retry().
	const h = harness();
	h.queue.change("a", 1);
	h.t.fire();
	h.calls[0].d.reject({ status: 401, message: "signed out", session_expired: true });
	await settle();
	q.expired = shape(h);
	h.queue.change("b", 2);
	q.expired_timers = h.t.delays();
	h.queue.retry().catch(() => {});
	await settle();
	q.expired_retry = shape(h);
}
{
	// A conflict: waiting flush rejects, nothing more is sent until on_conflict answers, then the
	// answer goes out under the new modified with newer typing on top.
	const answer = deferred();
	const seen = [];
	const h = harness({
		on_conflict: (result, sent) => {
			seen.push({ result, sent });
			return answer.promise;
		},
	});
	h.queue.change("a", "mine");
	h.queue.change("b", "mine too");
	const flushing = h.queue.flush().then(() => "resolved", (e) => e);
	h.calls[0].d.resolve({ conflict: true, modified: "m9", modified_by: "x@example.com", server: { a: "theirs", b: "base" } });
	q.conflict_flush = await flushing;
	await settle();
	q.conflict_seen = seen.map((s) => s.sent);
	q.conflict_state = shape(h);
	q.conflict_second_flush = await h.queue.flush().then(() => "resolved", (e) => !!e.conflict);
	h.queue.change("b", "typed during");
	answer.resolve({ save: { a: "mine", b: "mine too" }, modified: "m9" });
	await settle();
	q.conflict_resent = shape(h);
	h.calls[1].d.resolve({ modified: "m10", values: {} });
	await settle();
	q.conflict_done = shape(h);
}
{
	// A conflict set aside (null): what was sent waits under the old modified.
	const h = harness({ on_conflict: () => Promise.resolve(null) });
	h.queue.change("a", 1);
	h.t.fire();
	h.calls[0].d.resolve({ conflict: true, modified: "m5", server: { a: 2 } });
	await settle();
	await settle();
	q.conflict_null = Object.assign(shape(h), { modified: h.queue.modified });
}
{
	// A send that throws synchronously is a failure, not an exception out of change().
	const h = harness({
		send: () => {
			throw new Error("boom");
		},
	});
	h.queue.change("a", 1);
	h.t.fire();
	await settle();
	q.throws = shape(h);
}
out.queue = q;

// ---- where a record opens (P5.2)
{
	const guided = { Opportunity: { accepts: ["party_name", "opportunity_from", "customer_address", "contact_person", "status"] } };
	const base = { guided, page_open: true, full_form: [], hash: "", from_link: false, bypass: null, renamed: [], local: null, fresh: null, ignore: [] };
	const ctx = (extra) => Object.assign({}, base, extra || {});
	const fresh = {
		name: "new-opportunity-2",
		doctype: "Opportunity",
		__islocal: 1,
		__unsaved: 1,
		status: "Qualification",
		opportunity_from: "Customer",
		naming_series: "CRM-OPP-.YYYY.-",
		transaction_date: "2026-10-13",
		custom_value_stream: [],
	};
	const mapped = Object.assign({}, fresh, {
		name: "new-opportunity-1",
		party_name: "CUST-1",
		customer_address: "ADDR-1",
		contact_person: "CONT-1",
		customer_name: "Storybook",
	});
	const form = (name, extra) => kit.redirect_for(["Form", "Opportunity", name], ctx(extra));
	const held = (extra) => form("new-opportunity-1", Object.assign({ local: mapped, fresh, ignore: ["customer_name"] }, extra));
	out.opening = {
		existing: form("CRM-OPP-1"),
		slash_name: kit.redirect_for(["Form", "Opportunity", "A", "B"], ctx()),
		new_word: form("new"),
		new_name_unheld: form("new-opportunity-9"),
		other_doctype: kit.redirect_for(["Form", "Lead", "L-1"], ctx()),
		list_route: kit.redirect_for(["List", "Opportunity"], ctx()),
		no_name: kit.redirect_for(["Form", "Opportunity"], ctx()),
		not_array: kit.redirect_for("Form/Opportunity/X", ctx()),
		no_ctx: kit.redirect_for(["Form", "Opportunity", "X"], null),
		page_closed: form("CRM-OPP-1", { page_open: false }),
		hash: form("CRM-OPP-1", { hash: "#custom_process_tab" }),
		bare_hash: form("CRM-OPP-1", { hash: "#" }),
		full_form: form("CRM-OPP-1", { full_form: [kit.full_form_key("Opportunity", "CRM-OPP-1")] }),
		full_form_other: form("CRM-OPP-2", { full_form: [kit.full_form_key("Opportunity", "CRM-OPP-1")] }),
		renamed: form("CRM-OPP-5", { renamed: ["CRM-OPP-5"] }),
		mapped: held(),
		mapped_left: held({ local: Object.assign({}, mapped, { territory: "Utah" }) }),
		mapped_table: held({ local: Object.assign({}, mapped, { items: [{ item_code: "X" }] }) }),
		// The real shape from prod (2026-10-13): the Customer mapping copies the value streams and
		// sets every number field to 0.
		mapped_prod: held({
			local: Object.assign({}, mapped, {
				custom_value_stream: [
					{ name: "r1", idx: 1, doctype: "Opportunity Value Stream", parent: "new-opportunity-1", value_stream: "Design" },
					{ name: "r2", idx: 2, doctype: "Opportunity Value Stream", parent: "new-opportunity-1", value_stream: "Build", __unedited: 1 },
				],
				opportunity_amount: 0,
				annual_revenue: 0,
				conversion_rate: 0,
			}),
			guided: { Opportunity: { accepts: ["party_name", "customer_address", "contact_person", "custom_value_stream", "opportunity_amount"] } },
		}),
		from_link: held({ from_link: true }),
		// /desk/opportunity/new?party_name=... : what the address asked for comes along.
		new_with_options: form("new", { options: { party_name: "CUST-9", lead: "L-1", status: "", rows: [1], __x: 1, name: "N" } }),
		// 0 is a value when a fresh record holds something else (a Check that defaults to 1).
		zero_kept: held({
			local: Object.assign({}, mapped, { custom_flag: 0 }),
			fresh: Object.assign({}, fresh, { custom_flag: 1 }),
			guided: { Opportunity: { accepts: ["party_name", "customer_address", "contact_person", "custom_flag"] } },
		}),
		plain_local: form("new-opportunity-2", { local: fresh, fresh }),
		is_new: ["new", "new-opportunity-1", "newsletter", "CRM-OPP-1", "", null].map(kit.is_new_name),
		carry: kit.carry_over(mapped, fresh, ["party_name", "customer_address"], ["customer_name"]),
		carry_nothing: kit.carry_over(null, null, [], []),
		split: kit.split_prefill(
			{
				party_name: "CUST-1",
				opportunity_from: "Customer",
				contact_person: "C-1",
				lead: "L-1",
				campaign: "",
				__x: 1,
				name: "N",
				status: "Needs Analysis",
			},
			["party_name", "opportunity_from", "contact_person", "status"],
			["party_name", "opportunity_from"]
		),
		split_nothing: kit.split_prefill(null, null, null),
	};
}
// ---- what the phone keeps while there is no signal (P4.7)
{
	const entry = kit.make_entry({
		doctype: "Opportunity",
		name: "CRM-OPP-1",
		modified: "m1",
		unsent: { custom_opportunity_summary: "Pond, 3 pumps", opportunity_amount: 5000, territory: "Utah", status: "Needs Analysis" },
		base: { custom_opportunity_summary: "Pond", opportunity_amount: 4000, territory: "Idaho", status: "Qualification", gone: "x" },
		at: 1000,
	});
	out.journal = {
		key: kit.journal_key("Opportunity", "CRM-OPP-1"),
		entry,
		empty: kit.make_entry({ doctype: "Opportunity", name: "X", unsent: {}, base: {} }),
		no_base: kit.make_entry({ doctype: "Opportunity", name: "X", unsent: { a: 1 }, base: null, at: 5 }),
		usable: [
			kit.usable_entry(entry, 2000),
			kit.usable_entry(entry, 1000 + kit.JOURNAL_MAX_AGE_MS + 1),
			kit.usable_entry(null, 1),
			kit.usable_entry({ unsent: {} }, 1),
			kit.usable_entry("junk", 1),
		],
		// The server since: the summary untouched (put back), the amount already saved (dropped),
		// the territory changed by someone else (asked), the status untouched (put back).
		reconciled: kit.reconcile(entry, { custom_opportunity_summary: "Pond", opportunity_amount: 5000, territory: "Nevada", status: "Qualification" }),
		reconciled_rows: kit.reconcile(
			{ unsent: { t: [{ c: "a" }, { c: "b" }] }, base: { t: [{ c: "a" }] } },
			{ t: [{ name: "r1", idx: 1, c: "a" }] }
		),
		reconciled_nothing: kit.reconcile(null, {}),
		try_later: [
			{ status: 0 },
			{ status: 502 },
			{ status: 503 },
			{ status: 504 },
			{ status: 429 },
			{ status: 500 },
			{ status: 500, exc_type: "ValidationError" },
			{ status: 417, exc_type: "ValidationError" },
			{ status: 403 },
			{ status: 401, session_expired: true },
			null,
		].map(kit.try_later),
	};
}
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestGuidedEntryUnderNode(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		node = shutil.which("node")
		with tempfile.TemporaryDirectory() as tmp:
			folder = Path(tmp)
			(folder / "package.json").write_text('{"type": "module"}', encoding="utf-8")
			shutil.copytree(FORM_KIT_JS, folder / "form_kit")
			(folder / "runner.mjs").write_text(RUNNER, encoding="utf-8")
			payload = json.dumps({"applies": APPLIES_CASES, "steps": STEPS})
			proc = subprocess.run(
				[node, str(folder / "runner.mjs"), str(folder), payload],
				capture_output=True,
				text=True,
				encoding="utf-8",
				timeout=60,
			)
		if proc.returncode != 0:
			raise AssertionError(f"node failed:\n{proc.stderr}")
		cls.out = json.loads(proc.stdout)

	# ---- routes

	def test_routes_are_read_from_the_address(self):
		r = self.out["route"]
		self.assertIsNone(r["other_page"])
		self.assertIsNone(r["not_array"])
		self.assertEqual(r["bare"], {"doctype": "", "name": "", "step": "", "is_new": False})
		self.assertEqual(r["new_"], {"doctype": "Opportunity", "name": "", "step": "", "is_new": True})
		self.assertEqual(r["doctype_only"], r["new_"])
		self.assertEqual(
			r["record"], {"doctype": "Opportunity", "name": "CRM-OPP-2026-00123", "step": "", "is_new": False}
		)
		self.assertEqual(r["step"]["step"], "scope")
		self.assertEqual(
			r["spaced"], {"doctype": "Sales Order", "name": "SO/1", "step": "items", "is_new": False}
		)

	def test_routes_are_built_as_set_route_takes_them(self):
		r = self.out["route"]
		self.assertEqual(r["build_new"], ["guided-entry", "Opportunity", "new"])
		self.assertEqual(r["build_new_ignores_step"], ["guided-entry", "Opportunity", "new"])
		self.assertEqual(r["build_record"], ["guided-entry", "Opportunity", "CRM-OPP-1"])
		self.assertEqual(r["build_step"], ["guided-entry", "Opportunity", "CRM-OPP-1", "budget"])
		self.assertEqual(r["build_none"], ["guided-entry"])
		self.assertEqual([t["step"] for t in r["round_trip"]], ["basics", "", ""])
		self.assertTrue(all(t["name"] == "N 1" for t in r["round_trip"]))

	def test_one_key_per_screen(self):
		r = self.out["route"]
		self.assertEqual(r["key_new"], "Opportunity/new/")
		self.assertEqual(r["key_step"], "Opportunity/A/s")
		self.assertEqual(r["key_none"], "")

	def test_no_step_is_called_new(self):
		"""``new`` is the route segment of an uncreated record: a record or step by that name could
		never be reached."""
		from erpnext_enhancements.form_kit.config import CONFIGS

		for doctype, config in CONFIGS.items():
			with self.subTest(doctype=doctype):
				self.assertNotIn("new", [step["key"] for step in config["steps"]])

	# ---- steps

	def test_rule_applies_matches_the_engine(self):
		applies = _python_applies()
		expected = [applies(rule, values) for rule, values in APPLIES_CASES]
		self.assertEqual(self.out["applies"], expected)
		self.assertIn(True, expected)
		self.assertIn(False, expected)

	def test_steps_that_do_not_apply_are_skipped(self):
		s = self.out["steps"]
		self.assertEqual(s["active_none"], ["basics", "contact", "close"])
		self.assertEqual(s["active_design"], ["basics", "contact", "scope_design", "close"])
		self.assertEqual(s["next_from_contact_none"], "close")
		self.assertEqual(s["next_from_contact_both"], "scope_design")
		self.assertEqual(s["prev_from_close_design"], "scope_design")
		self.assertEqual(s["next_from_inactive"], "close")
		self.assertIsNone(s["prev_from_first"])
		self.assertIsNone(s["next_from_last"])
		self.assertIsNone(s["next_from_unknown"])

	def test_the_address_is_corrected_to_a_step_that_applies(self):
		s = self.out["steps"]
		self.assertEqual(s["resolve_empty"], {"key": "basics", "replace": True})
		self.assertEqual(s["resolve_ok"], {"key": "scope_design", "replace": False})
		self.assertEqual(s["resolve_inactive"], {"key": "close", "replace": True})
		self.assertEqual(s["resolve_unknown"], {"key": "basics", "replace": True})
		self.assertEqual(s["resolve_no_steps"], {"key": None, "replace": False})
		self.assertEqual(s["position_design"], {"index": 3, "number": 4, "total": 4})
		self.assertEqual(s["position_none"], {"index": 2, "number": 3, "total": 3})

	def test_what_is_missing_is_counted_on_the_step_that_holds_it(self):
		s = self.out["steps"]
		self.assertEqual(s["field_steps"]["party_name"], "basics", "the first step showing a field holds it")
		self.assertNotIn("cb1", s["field_steps"])
		missing = s["missing"]
		self.assertEqual([m["label"] for m in missing["basics"]], ["Account"])
		self.assertEqual([m["label"] for m in missing["scope_design"]], ["Customer Requests"])
		self.assertEqual([m["label"] for m in missing["close"]], ["Status"])
		self.assertEqual([m["label"] for m in missing[""]], ["Hidden"])

	def test_step_one_checks_its_required_fields(self):
		s = self.out["steps"]
		self.assertEqual(
			s["missing_required_empty"], ["party_name", "opportunity_from", "custom_value_stream"]
		)
		self.assertEqual(s["missing_required_full"], [])
		self.assertEqual(s["missing_required_blank"], ["party_name"])
		self.assertEqual(s["editable"], ["party_name", "opportunity_from", "custom_value_stream"])

	# ---- fields

	def test_each_field_asks_for_the_right_keyboard(self):
		m = self.out["inputmode"]
		self.assertEqual(m["phone_registered"], "tel")
		self.assertEqual(m["phone_by_parent"], "tel")
		self.assertEqual(m["email_registered"], "email")
		self.assertEqual(m["website_registered"], "url")
		self.assertIsNone(m["other_doctype"])
		self.assertEqual(m["int_"], "numeric")
		for key in ("float_", "currency", "percent"):
			self.assertEqual(m[key], "decimal", key)
		self.assertEqual(m["phone_type"], "tel")
		self.assertEqual(m["email_option"], "email")
		self.assertEqual(m["url_option"], "url")
		for key in ("plain", "link", "no_registry", "none"):
			self.assertIsNone(m[key], key)

	def test_only_a_one_text_column_table_becomes_a_list(self):
		t = self.out["text_list"]
		self.assertEqual(t["long_text"], "design_customer_requests")
		for key in ("data", "small", "text", "editor"):
			self.assertEqual(t[key], "d", key)
		for key in ("link", "two", "none", "multiselect", "not_table"):
			self.assertIsNone(t[key], key)

	def test_list_edits_keep_row_names_and_renumber(self):
		r = self.out["rows"]
		self.assertEqual([row.get("name") for row in r["add"]], ["r1", "r2", "r3", None])
		self.assertEqual([row["idx"] for row in r["add"]], [1, 2, 3, 4])
		self.assertEqual(r["add_empty"], [{"c": "", "idx": 1}])
		self.assertEqual([row["name"] for row in r["remove_middle"]], ["r1", "r3"])
		self.assertEqual([row["idx"] for row in r["remove_middle"]], [1, 2])
		self.assertEqual([row["name"] for row in r["remove_out_of_range"]], ["r1", "r2", "r3"])
		self.assertEqual([row["name"] for row in r["move_up"]], ["r1", "r3", "r2"])
		self.assertEqual([row["idx"] for row in r["move_up"]], [1, 2, 3])
		self.assertEqual([row["name"] for row in r["move_down"]], ["r2", "r1", "r3"])
		self.assertEqual([row["name"] for row in r["move_first_up"]], ["r1", "r2", "r3"])
		self.assertEqual([row["name"] for row in r["move_last_down"]], ["r1", "r2", "r3"])
		self.assertEqual(r["set_text"][1], {"name": "r2", "idx": 2, "c": "TWO"})
		self.assertEqual(r["untouched"][1]["c"], "two", "the edits return new arrays")

	def test_rows_are_sent_with_stored_names_and_columns_only(self):
		self.assertEqual(
			self.out["rows"]["clean"],
			[{"name": "r1", "c": "one"}, {"c": "new"}, {"c": "newer"}, {"c": "no name"}],
		)

	def test_contacts_and_addresses_come_from_the_party(self):
		p = self.out["party"]
		self.assertEqual(p["party"], {"doctype": "Customer", "name": "CUST-1"})
		self.assertIsNone(p["no_name"])
		self.assertIsNone(p["no_field"])
		self.assertEqual(
			p["contact"],
			{
				"query": "frappe.contacts.doctype.contact.contact.contact_query",
				"filters": {"link_doctype": "Customer", "link_name": "CUST-1"},
			},
		)
		self.assertEqual(p["address"]["query"], "frappe.contacts.doctype.address.address.address_query")
		self.assertIsNone(p["customer"])
		self.assertIsNone(p["no_party"])

	# ---- conflicts

	def test_values_compare_by_meaning(self):
		s = self.out["same"]
		self.assertEqual(s["blanks"], [True, True, True])
		self.assertEqual(s["numbers"], [True, True, False, False])
		self.assertEqual(s["strings"], [True, False, False])
		self.assertEqual(s["rows"], [True, False, False, False, True, False])

	def test_a_conflict_asks_only_about_fields_both_people_changed(self):
		split = self.out["split"]
		self.assertEqual(split["resend"], {"a": "mine", "f": "not reported"})
		self.assertEqual(
			split["conflicts"],
			[
				{"fieldname": "b", "mine": "mine", "theirs": "theirs"},
				{"fieldname": "d", "mine": "mine", "theirs": "theirs"},
				{"fieldname": "e", "mine": [{"c": "y"}], "theirs": [{"name": "r", "c": "z"}]},
			],
		)

	def test_choices_become_the_values_to_save(self):
		resolved = self.out["resolved"]
		self.assertEqual(resolved["save"], {"a": "mine", "f": "not reported", "d": "mine", "e": [{"c": "y"}]})
		self.assertEqual(resolved["take"], {"b": "theirs"})
		self.assertEqual(self.out["value_text"], ["one · two", "", "12"])

	# ---- summary

	def test_streams_take_their_pillar(self):
		from erpnext_enhancements import print_style

		s = self.out["summary"]
		self.assertEqual(s["pillars"], print_style.STREAM_PILLAR)
		self.assertEqual(s["unknown"], "neutral")
		self.assertEqual(
			s["streams"], [{"stream": "Events", "pillar": "rent"}, {"stream": "Design", "pillar": "design"}]
		)
		self.assertEqual(s["lead"], "service")
		self.assertEqual(s["lead_none"], "neutral")
		self.assertEqual(s["lead_other_doctype"], "neutral")

	def test_the_summary_model(self):
		m = self.out["summary"]["model"]
		self.assertEqual(m["title"], "Smith Residence - Pond")
		self.assertEqual(m["account"], "Smith")
		self.assertEqual(m["amount"], 2500.5)
		self.assertEqual(m["status"], "Open")
		self.assertEqual((m["done"], m["total"], m["percent"]), (4, 6, 67))
		self.assertEqual(m["stage"], "Qualification")
		empty = self.out["summary"]["model_empty"]
		self.assertEqual(empty["title"], "LEAD-1")
		self.assertIsNone(empty["amount"])
		self.assertEqual((empty["done"], empty["total"], empty["percent"]), (0, 0, 0))

	# ---- the save queue

	def test_autosave_waits_two_and_a_half_seconds_and_coalesces(self):
		q = self.out["queue"]
		self.assertEqual(q["delays"], [2500])
		self.assertEqual(q["before_timer"]["calls"], [])
		self.assertEqual(q["before_timer"]["state"], "dirty")
		self.assertEqual(q["first_out"]["calls"], [{"values": {"a": 3, "b": 2}, "modified": "m0"}])
		self.assertEqual(q["first_out"]["state"], "saving")

	def test_one_request_in_flight_and_modified_carried_forward(self):
		q = self.out["queue"]
		self.assertEqual(len(q["while_in_flight"]["calls"]), 1, "a second save waits for the first")
		self.assertEqual(q["while_in_flight"]["pending"], {"a": 4, "c": 5})
		self.assertEqual(q["second_out"]["calls"][1], {"values": {"a": 4, "c": 5}, "modified": "m1"})
		self.assertEqual(q["flushed_early"], "no", "flush() waits until nothing is left")
		done = q["done"]
		self.assertEqual(done["flushed"], "yes")
		self.assertEqual(done["state"], "saved")
		self.assertEqual(done["modified"], "m2")
		self.assertFalse(done["unsaved"])
		self.assertEqual(done["saved_sent"], [{"a": 3, "b": 2}, {"a": 4, "c": 5}])
		self.assertEqual(q["states"][0], "dirty")
		self.assertEqual(q["states"][-1], "saved")

	def test_a_change_undone_sends_nothing(self):
		d = self.out["queue"]["discard"]
		self.assertEqual(d["calls"], [])
		self.assertEqual(d["state"], "saved")
		self.assertFalse(d["unsaved"])

	def test_a_refusal_keeps_the_values_and_says_why(self):
		q = self.out["queue"]
		self.assertEqual(q["error_flush"]["status"], 417)
		self.assertEqual(q["error"]["state"], "error")
		self.assertEqual(q["error"]["pending"], {"a": 2, "b": 1}, "put back, the newer typing on top")
		self.assertTrue(q["error"]["unsaved"])
		self.assertEqual(q["error_message"], "Ranks are required")
		self.assertEqual(q["error_retry_scheduled"], [2500])
		self.assertEqual(q["error_retry"]["calls"][1]["values"], {"a": 2, "b": 1, "c": 3})

	def test_a_lapsed_session_stops_autosave_until_retry(self):
		q = self.out["queue"]
		self.assertEqual(q["expired"]["state"], "expired")
		self.assertEqual(q["expired"]["pending"], {"a": 1})
		self.assertEqual(q["expired_timers"], [], "no autosave into a refused session")
		self.assertEqual(q["expired_retry"]["calls"][1]["values"], {"a": 1, "b": 2})

	def test_a_conflict_waits_for_the_person_then_saves_on_the_new_modified(self):
		q = self.out["queue"]
		self.assertTrue(q["conflict_flush"]["conflict"])
		self.assertEqual(q["conflict_seen"], [{"a": "mine", "b": "mine too"}])
		self.assertEqual(q["conflict_state"]["state"], "conflict")
		self.assertEqual(len(q["conflict_state"]["calls"]), 1)
		self.assertTrue(q["conflict_state"]["unsaved"])
		self.assertTrue(q["conflict_second_flush"])
		resent = q["conflict_resent"]["calls"][1]
		self.assertEqual(resent["modified"], "m9")
		self.assertEqual(resent["values"], {"a": "mine", "b": "typed during"})
		self.assertEqual(q["conflict_done"]["state"], "saved")

	def test_a_conflict_set_aside_waits_under_the_old_modified(self):
		c = self.out["queue"]["conflict_null"]
		self.assertEqual(c["pending"], {"a": 1})
		self.assertEqual(c["modified"], "m0")
		self.assertEqual(c["state"], "dirty")

	def test_a_transport_that_throws_is_a_failure(self):
		t = self.out["queue"]["throws"]
		self.assertEqual(t["state"], "error")
		self.assertEqual(t["pending"], {"a": 1})

	# ---- what the phone keeps while there is no signal (P4.7)

	def test_journal_entries_keep_only_the_unsent_fields_and_their_base(self):
		j = self.out["journal"]
		self.assertEqual(j["key"], "ge-unsent:Opportunity/CRM-OPP-1")
		self.assertEqual(j["entry"]["base"], {
			"custom_opportunity_summary": "Pond", "opportunity_amount": 4000, "territory": "Idaho", "status": "Qualification"
		})
		self.assertEqual((j["entry"]["modified"], j["entry"]["at"]), ("m1", 1000))
		self.assertIsNone(j["empty"])
		self.assertEqual(j["no_base"]["base"], {"a": None})
		self.assertEqual(j["usable"], [True, False, False, False, False])

	def test_reconcile_never_overwrites_a_newer_server_edit(self):
		r = self.out["journal"]["reconciled"]
		self.assertEqual(r["reapply"], {"custom_opportunity_summary": "Pond, 3 pumps", "status": "Needs Analysis"})
		self.assertEqual(r["drop"], ["opportunity_amount"])
		self.assertEqual(r["conflicts"], [{"fieldname": "territory", "mine": "Utah", "theirs": "Nevada"}])
		rows = self.out["journal"]["reconciled_rows"]
		self.assertEqual(rows["reapply"], {"t": [{"c": "a"}, {"c": "b"}]})
		self.assertEqual(self.out["journal"]["reconciled_nothing"], {"reapply": {}, "drop": [], "conflicts": []})

	def test_only_the_networks_failures_wait_for_signal(self):
		self.assertEqual(
			self.out["journal"]["try_later"], [True, True, True, True, True, True, False, False, False, False, True]
		)

	# ---- where a record opens (P5.2). Nik, 2026-10-13: "I want these guided setups to be the
	# default for new and existing documents of the DocTypes we are making guided setups for."

	def test_opening_an_existing_record_opens_on_the_guided_page(self):
		o = self.out["opening"]
		self.assertEqual(o["existing"], {"route": ["guided-entry", "Opportunity", "CRM-OPP-1"], "prefill": None})
		self.assertEqual(o["slash_name"]["route"], ["guided-entry", "Opportunity", "A/B"])
		self.assertEqual(o["bare_hash"], o["existing"])

	def test_opening_a_new_record_opens_on_step_one(self):
		o = self.out["opening"]
		new = {"route": ["guided-entry", "Opportunity", "new"], "prefill": {}}
		self.assertEqual(o["new_word"], new)
		self.assertEqual(o["new_name_unheld"], new)
		self.assertEqual(o["plain_local"], new)
		self.assertEqual(o["is_new"], [True, True, False, False, False, False])

	def test_opening_the_form_stays_when_it_should(self):
		o = self.out["opening"]
		for key in ("other_doctype", "list_route", "no_name", "not_array", "no_ctx", "page_closed", "hash",
				"full_form", "renamed", "mapped_left", "mapped_table", "from_link"):
			with self.subTest(case=key):
				self.assertIsNone(o[key])
		self.assertIsNotNone(o["full_form_other"])

	def test_opening_a_mapped_record_carries_its_values(self):
		o = self.out["opening"]
		# ERPNext's Create > Opportunity on a Customer: the party, an address and a contact.
		expected = {
			"route": ["guided-entry", "Opportunity", "new"],
			"prefill": {"party_name": "CUST-1", "customer_address": "ADDR-1", "contact_person": "CONT-1"},
		}
		self.assertEqual(o["mapped"], expected)
		self.assertEqual(o["zero_kept"]["prefill"], dict(expected["prefill"], custom_flag=0))
		self.assertEqual(
			o["new_with_options"], {"route": ["guided-entry", "Opportunity", "new"], "prefill": {"party_name": "CUST-9", "lead": "L-1"}}
		)
		# Zeros are not values, and accepted tables come along as their own columns.
		prod = dict(expected["prefill"], custom_value_stream=[{"value_stream": "Design"}, {"value_stream": "Build"}])
		self.assertEqual(o["mapped_prod"], {"route": expected["route"], "prefill": prod})

	def test_opening_carry_over_tells_defaults_from_values(self):
		o = self.out["opening"]
		self.assertEqual(
			o["carry"], {"prefill": {"party_name": "CUST-1", "customer_address": "ADDR-1"}, "left": ["contact_person"]}
		)
		self.assertEqual(o["carry_nothing"], {"prefill": {}, "left": []})

	def test_opening_the_page_splits_what_it_was_started_with(self):
		o = self.out["opening"]
		self.assertEqual(
			o["split"],
			{
				"values": {"party_name": "CUST-1", "opportunity_from": "Customer"},
				"carry": {"contact_person": "C-1", "status": "Needs Analysis"},
				"dropped": ["lead"],
			},
		)
		self.assertEqual(o["split_nothing"], {"values": {}, "carry": {}, "dropped": []})


class TestThePage(unittest.TestCase):
	def test_the_page_record(self):
		page = json.loads(_text(PAGE_JSON))
		self.assertEqual(page["doctype"], "Page")
		self.assertEqual(page["name"], "guided-entry")
		self.assertEqual(page["page_name"], "guided-entry")
		self.assertEqual(page["standard"], "Yes")
		self.assertEqual(page["title"], "Guided Entry")
		modules = [line.strip() for line in _text(APP / "modules.txt").splitlines() if line.strip()]
		self.assertIn(page["module"], modules)
		self.assertEqual(page["module"].lower().replace(" ", "_"), PAGE_DIR.parents[1].name)
		roles = {row["role"] for row in page["roles"]}
		self.assertTrue({"Sales User", "Sales Manager", "System Manager"} <= roles)
		self.assertTrue((PAGE_DIR / "__init__.py").is_file())

	def test_the_slug_is_not_a_workspace(self):
		for path in APP.glob("*/workspace/*/*.json"):
			name = json.loads(_text(path)).get("name") or ""
			self.assertNotEqual(name.lower().replace(" ", "-"), "guided-entry", path)

	def test_it_loads_the_kit_and_its_own_bundles(self):
		code = _code(PAGE_JS)
		for asset in (
			"sf_kit.bundle.js",
			"sf_kit.bundle.css",
			"guided_entry.bundle.js",
			"guided_entry.bundle.css",
		):
			self.assertIn(f'"{asset}"', code)
		self.assertIn("frappe.require(GE_ASSETS", code)
		self.assertIn('import * as form_kit from "./form_kit/index.js";', _text(BUNDLE))
		self.assertIn("window.sf_form_kit", _text(BUNDLE))

	def test_every_request_goes_through_sf_kit_quietly(self):
		code = _code(PAGE_JS)
		for forbidden in (
			"frappe.call(",
			"frappe.xcall(",
			"fetch(",
			"$.ajax(",
			"$.get(",
			"$.post(",
			"XMLHttpRequest",
		):
			self.assertNotIn(forbidden, code)
		calls = [m.start() for m in re.finditer(r"\.call\(\s*GE_API", code)]
		self.assertGreaterEqual(len(calls), 4)
		for start in calls:
			depth, end = 0, start
			for end in range(code.index("(", start), len(code)):
				depth += {"(": 1, ")": -1}.get(code[end], 0)
				if depth == 0:
					break
			call = code[start : end + 1]
			with self.subTest(call=call[:60]):
				self.assertIn("quiet: true", call)
				self.assertTrue(call.startswith(".call(") and "sf" in code[max(0, start - 10) : start])
		for method in ("get_flow", "create_record", "save_step"):
			self.assertIn(f'GE_API + "{method}"', code)

	def test_reads_are_get_and_writes_are_post(self):
		code = _code(PAGE_JS)
		for match in re.finditer(r'GE_API \+ "(\w+)"[\s\S]*?\{ quiet: true(?:, method: "GET")? \}', code):
			with self.subTest(method=match.group(1)):
				is_get = 'method: "GET"' in match.group(0)
				self.assertEqual(is_get, match.group(1) in ("get_flow", "my_records"))

	def test_steps_move_with_set_route(self):
		code = _code(PAGE_JS)
		self.assertIn("frappe.set_route(route)", code)
		self.assertIn("frappe.route_flags.replace_route = true", code)
		self.assertIn("delete frappe.route_flags.replace_route", code)
		self.assertIn("this.kit.build_route(", code)
		self.assertNotIn("pushState", code)
		self.assertIsNone(re.search(r"location(\.href)?\s*=(?!=)", code), "never a hand-built navigation")
		self.assertNotIn("location.assign(", code)
		self.assertNotIn('addEventListener("popstate"', code)
		# The "new" entry is replaced, then the next step pushed.
		self.assertIn("this.go(first, true);", code)
		self.assertIn("if (next) this.go(next);", code)

	def test_no_hard_coded_app_paths(self):
		pattern = re.compile(r"""['"`(]/app/""")
		files = [PAGE_JS, BUNDLE, DEFAULT_JS, *FORM_KIT_JS.glob("*.js")]
		for path in files:
			with self.subTest(file=path.name):
				self.assertIsNone(pattern.search(_code(path)))

	def test_unsaved_work_is_never_lost_silently(self):
		code = _code(PAGE_JS)
		self.assertIn('window.addEventListener("beforeunload"', code)
		self.assertIn("this.has_unsaved()", code)
		self.assertIn('$(wrapper).on("hide"', code)

	def test_the_help_opt_out_is_set_and_narrow(self):
		icons = _code(ICONS)
		self.assertEqual(icons.count("ee_desc_inline"), 1, "read once, in decorate(), and nowhere else")
		decorate = icons[icons.index("function decorate(control)") :]
		decorate = decorate[: decorate.index("\n\t}\n")]
		self.assertIn("if (control.df.ee_desc_inline) return;", decorate)
		self.assertIn("df.ee_desc_inline = 1;", _code(PAGE_JS))
		# Nothing else in the app sets it.
		for path in APP.rglob("*.js"):
			if path in (ICONS, PAGE_JS) or "node_modules" in path.parts:
				continue
			with self.subTest(file=str(path.relative_to(APP))):
				self.assertNotIn("ee_desc_inline", _text(path))
		for path in APP.rglob("*.json"):
			if "node_modules" in path.parts:
				continue
			self.assertNotIn("ee_desc_inline", _text(path), path)

	def test_the_help_button_is_a_real_button(self):
		code = _code(PAGE_JS)
		self.assertIn('<button type="button" class="ge-help-btn" aria-expanded="false">', code)
		self.assertIn('.attr("aria-label", __("More about {0}", [label]))', code)
		self.assertIn('frappe.utils.get_form_link("Knowledge Article", article)', code)

	def test_no_settings_single_is_read(self):
		code = _code(PAGE_JS)
		self.assertNotIn("get_single_value", code)
		self.assertNotIn('"Settings"', code)

	def test_the_stylesheet_uses_tokens_only(self):
		css = re.sub(r"/\*[\s\S]*?\*/", "", _text(CSS))
		self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b", css), "a colour value of its own")
		self.assertNotIn("rgb(", css)
		self.assertNotIn("shadow", css.lower(), "the brand has no shadows in its UI")
		self.assertIn("@media (max-width: 767px)", css)
		self.assertIn("env(safe-area-inset-bottom", css)
		self.assertIn("var(--sf-text-input)", css)
		self.assertIn("var(--sf-touch-min)", css)

	def test_the_bundle_names_are_ours(self):
		from erpnext_enhancements.tests.test_bundle_name_collisions import UPSTREAM_BUNDLE_KEYS

		for keys in UPSTREAM_BUNDLE_KEYS.values():
			self.assertNotIn("guided_entry.bundle.js", keys)
			self.assertNotIn("guided_entry.bundle.css", keys)


class TestTheEntryPoint(unittest.TestCase):
	def test_the_opportunity_form_has_a_guided_button(self):
		code = _code(OPPORTUNITY_JS)
		self.assertIn('frm.add_custom_button(__("Guided")', code)
		self.assertIn('frappe.set_route("guided-entry", frm.doctype, frm.doc.name)', code)
		self.assertIn('frappe.boot.page_info["guided-entry"]', code)
		# It brings the record back to the guided page for good, not just this once.
		self.assertIn("window.sf_guided.to_guided(frm.doctype, frm.doc.name)", code)

	def test_the_form_script_is_wired_for_opportunity(self):
		hooks = _text(APP / "hooks.py")
		block = hooks[hooks.index('"Opportunity": [', hooks.index("doctype_js = {")) :]
		block = block[: block.index("],")]
		self.assertIn('"public/js/crm_enhancements/opportunity.js"', block)


class TestPhoneFixesFromTheLiveCheck(unittest.TestCase):
    """Found on prod at 375px on 2026-10-13: the Triton button covered Next, and Frappe's form
    padding set the fields 30px in from the step title."""

    def test_the_assistant_button_lifts_only_while_the_page_shows(self):
        js = (APP / "enhancements_core" / "page" / "guided_entry" / "guided_entry.js").read_text(encoding="utf-8")
        self.assertIn('const GE_BODY_CLASS = "ge-page-active";', js)
        self.assertIn("document.body.classList.add(GE_BODY_CLASS);", js)
        self.assertIn("document.body.classList.remove(GE_BODY_CLASS);", js)
        css = (APP / "public" / "css" / "guided_entry.bundle.css").read_text(encoding="utf-8")
        self.assertIn("body.ge-page-active .triton-fab", css)

    def test_phone_fields_use_the_full_width(self):
        css = (APP / "public" / "css" / "guided_entry.bundle.css").read_text(encoding="utf-8")
        phone = css[css.index("@media (max-width: 767px)") :]
        self.assertIn(".ge-fields .form-section .form-column {\n\t\tpadding-left: 0;", phone)


class TestTheDefaultIsWired(unittest.TestCase):
	def test_the_desk_bundle_loads_it(self):
		self.assertIn('import "./guided_default.js";', _code(DESK_BUNDLE))

	def test_new_goes_through_v16s_create_routes(self):
		code = _code(DEFAULT_JS)
		self.assertIn('frappe.create_routes[doctype] = ["guided-entry", doctype, "new"]', code)
		# "Open form" on a new record builds the draft itself: frappe.new_doc would open Quick Entry
		# (Opportunity has it) and drop the tables step 1 held.
		new_in_form = code[code.index("new_in_form(doctype, values) {") :]
		new_in_form = new_in_form[: new_in_form.index("\n\t},\n")]
		self.assertNotIn("frappe.new_doc(", new_in_form)
		self.assertIn("frappe.model.add_child(doc, df.options, fieldname)", new_in_form)
		self.assertIn("api.allow_form(doctype, doc.name);", new_in_form)
		self.assertIn('frappe.set_route("Form", doctype, doc.name);', new_in_form)

	def test_existing_goes_through_the_form_factorys_before_show(self):
		code = _code(DEFAULT_JS)
		self.assertIn("Factory.prototype.before_show = function", code)
		# A before_show already there runs first and keeps its say.
		self.assertIn("if (own && own.apply(this, args) === false) return false;", code)
		# The form's history entry is replaced, so Back leaves the way the person came.
		self.assertIn("frappe.route_flags.replace_route = true;", code)
		self.assertIn('import { redirect_for, full_form_key } from "./form_kit/opening.js";', code)

	def test_only_for_people_who_may_open_the_page(self):
		code = _code(DEFAULT_JS)
		self.assertIn('frappe.boot.page_info["guided-entry"]', code)
		self.assertIn("frappe.boot.sf_guided", code)

	def test_session_storage_never_breaks_it(self):
		code = _code(DEFAULT_JS)
		self.assertEqual(code.count("window.sessionStorage"), 2)
		for name in ("function read_store() {", "function write_store(list) {"):
			body = code[code.index(name) :]
			body = body[: body.index("\n}\n")]
			with self.subTest(function=name):
				self.assertIn("window.sessionStorage", body)
				self.assertLess(body.index("try {"), body.index("window.sessionStorage"))
				self.assertIn("} catch (e) {", body)

	def test_the_page_uses_the_full_form_way_out(self):
		code = _code(PAGE_JS)
		self.assertIn("window.sf_guided.to_form(this.doctype, this.name, tab)", code)
		self.assertIn("window.sf_guided.new_in_form(doctype, values)", code)
		self.assertIn("this.open_in_form(step.link.tab)", code)
		# Done goes back to the list: the form is the fallback now, not the destination.
		self.assertIn('.then(() => frappe.set_route("List", this.doctype))', code)
		self.assertNotIn('.then(() => frappe.set_route("Form", this.doctype, this.name))', code)

	def test_the_start_screen_lists_the_callers_records(self):
		code = _code(PAGE_JS)
		self.assertIn('GE_API + "my_records"', code)
		self.assertIn("frappe.new_doc(doctype)", code)

	def test_switching_between_form_and_guided_never_doubles_a_history_entry(self):
		code = _code(DEFAULT_JS)
		self.assertIn("if (!tab && tagged(`form:${key}`)) {", code)
		self.assertIn("if (tagged(`guided:${key}`)) {", code)
		self.assertEqual(code.count("window.history.back();"), 2)
		# The guided page's own replaces keep the tag the router's replaceState(null) would drop.
		self.assertIn("{ sf_toggle: toggle }", _code(PAGE_JS))

	def test_the_long_start_address_is_tidied(self):
		self.assertIn("window.location.pathname + window.location.hash", _code(PAGE_JS))

	def test_the_crm_sidebar_links_the_start_screen(self):
		tweaks = _text(APP / "setup" / "workspace_tweaks.py")
		block = tweaks[tweaks.index('"CRM": [') :]
		self.assertIn('"link_to": "guided-entry"', block)
		self.assertIn('"after": ("DocType", "Opportunity")', block)


class TestNoSignal(unittest.TestCase):
	"""Form kit P4.7: the guided page keeps working when the connection drops."""

	def test_every_change_and_every_save_state_is_journaled(self):
		code = _code(PAGE_JS)
		self.assertIn("this.write_journal(doctype, name, queue);", code)
		self.assertIn("this.write_journal(this.doctype, this.name, this.queue);", code)
		self.assertIn("this.note_base(fieldname);", code)
		# The journal is written for every queue, the retired ones included (not only current()).
		on_state = code[code.index("on_state: (state, error) => {") :]
		on_state = on_state[: on_state.index("},")]
		self.assertLess(on_state.index("this.write_journal"), on_state.index("if (current())"))

	def test_the_phone_copy_is_put_back_through_reconcile(self):
		code = _code(PAGE_JS)
		self.assertIn("const plan = this.kit.reconcile(entry, this.flow.values);", code)
		self.assertIn("const kept = this.is_new ? null : this.restore_kept();", code)
		self.assertIn("this.show_conflict({ modified_by: null }, { conflicts: kept.conflicts, resend: {} }", code)

	def test_signal_coming_back_sends_what_waited(self):
		code = _code(PAGE_JS)
		self.assertIn('window.addEventListener("online", () => this.signal_back());', code)
		self.assertIn("this.wait_for_signal();", code)
		self.assertIn("queue.retry().catch(() => {});", code)

	def test_storage_is_always_guarded_and_closing_warns_only_when_needed(self):
		code = _code(PAGE_JS)
		self.assertEqual(code.count("window.localStorage"), 1)
		for name in ("function ge_storage() {", "function ge_read_kept(key) {", "function ge_write_kept(key, entry) {"):
			body = code[code.index(name) :]
			body = body[: body.index("\n}\n")]
			with self.subTest(function=name):
				self.assertIn("} catch (e) {", body)
		self.assertIn("if (!this.has_unsaved() || this.kept_on_phone()) return;", code)

	def test_the_saving_line_says_kept_on_the_phone(self):
		code = _code(PAGE_JS)
		self.assertIn('tone = "offline";', code)
		self.assertIn(".ge-saveline--offline", _text(CSS))


class TestWhatTheFormScriptsDid(unittest.TestCase):
	"""Form kit P4.5: what the Opportunity form's own scripts do that the guided page must too."""

	def test_choosing_lost_asks_for_the_reasons(self):
		code = _code(PAGE_JS)
		self.assertIn("if (this.intercept_change(fieldname, value)) return;", code)
		self.assertIn('title: __("Set as Lost"),', code)
		self.assertIn('GE_API + "declare_lost"', code)
		self.assertIn("modified: this.queue ? this.queue.modified : this.flow.modified,", code)

	def test_the_closed_won_prompt_reloads_the_page(self):
		prompt = _code(JS / "crm_enhancements" / "create_project_prompt.js")
		self.assertIn('$(document).trigger("sf-record-changed", ["Opportunity", opportunity_name]);', prompt)
		code = _code(PAGE_JS)
		self.assertIn('$(document).on("sf-record-changed"', code)
		self.assertIn('frappe.realtime.on("project_creation_status"', code)
		self.assertIn('crm.confirm_won_next_step(this.name, { mode: "reopen" });', code)

	def test_the_reminder_is_only_for_a_deal_won_before_the_visit(self):
		code = _code(PAGE_JS)
		body = code[code.index("nudge_won_without_project() {") :]
		body = body[: body.index("\n\t}\n")]
		# Marked on the first load whatever the status, so a reload after the prompt's Yes (the
		# project still being made) never asks again and queues a second project.
		self.assertLess(body.index("this.nudged.add(this.name);"), body.index('v.status !== "Closed Won"'))
		self.assertIn("this.nudged.delete(this.name);", code)

	def test_nothing_unsaved_is_dropped_by_a_reload_or_declare_lost(self):
		code = _code(PAGE_JS)
		reload = code[code.index("reload_record() {") :]
		self.assertLess(reload.index("this.queue.has_unsaved()) return;"), reload.index("this.load(doctype, name, step)"))
		declare = code[code.index("declare_lost(dialog, values) {") :]
		self.assertLess(declare.index("this.queue.has_unsaved()"), declare.index('GE_API + "declare_lost"'))

	def test_project_messages_are_escaped(self):
		code = _code(PAGE_JS)
		self.assertIn("frappe.utils.escape_html(data.blocked_reason", code)
		self.assertIn("frappe.utils.escape_html(String(data.drive_error))", code)

	def test_clears_and_distinct_are_applied(self):
		code = _code(PAGE_JS)
		self.assertIn("((this.flow.clears || {})[fieldname] || []).forEach", code)
		self.assertIn("this.distinct_warnings()", code)


if __name__ == "__main__":
	unittest.main()
