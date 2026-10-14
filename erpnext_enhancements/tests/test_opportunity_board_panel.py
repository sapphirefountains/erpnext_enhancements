"""Bench-free tests for the side panel (form kit P4.4) and the board's part C (P6.6, P6.7; v1.675.0).

Four halves:

* **Under node**: the side panel's pure decisions (``public/js/form_kit/panel.js``: a move's required
  fields, quick add's start, the prefill a create carries, the Undo values), the save queue's
  ``manual`` mode (a move's fields wait for the commit), and the board's part C
  (``public/js/opportunity_board/board.js``: the side panel's routes, which panel an address may
  show, quick add's columns, the selection and a bulk request's per-deal answer). Both folders are
  copied beside a ``{"type": "module"}`` package.json, as the board's other suites do.
* **The bulk endpoint** (``opportunity_board.bulk_update``) against a frappe stub, with the form kit's
  saver replaced by a recorder: every deal through the single-change path, write permission and the
  ``modified`` lock per deal, a savepoint per deal so one refusal never undoes the others, and no
  stage rule bypassed.
* **One renderer**: the guided page and the side panel run the same record editor
  (``public/js/form_kit/ui/editor.js``), never a fork.
* **The wiring**: routes, history, the drawer, the on-demand bundles, the stylesheets.

Run: python -m unittest erpnext_enhancements.tests.test_opportunity_board_panel -v
"""

import datetime
import importlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
JS = APP / "public" / "js"
CSS_DIR = APP / "public" / "css"
BOARD_JS = JS / "opportunity_board"
FORM_KIT_JS = JS / "form_kit"
EDITOR_JS = FORM_KIT_JS / "ui" / "editor.js"
PANEL_JS = FORM_KIT_JS / "ui" / "side_panel.js"
DRAWER_JS = JS / "planner_kit" / "drawer.js"
GUIDED_BUNDLE = JS / "guided_entry.bundle.js"
PAGE_JS = APP / "crm_enhancements" / "page" / "opportunity_board" / "opportunity_board.js"
GUIDED_JS = APP / "enhancements_core" / "page" / "guided_entry" / "guided_entry.js"
MODULE = "erpnext_enhancements.crm_enhancements.page.opportunity_board.opportunity_board"
SAVER = "erpnext_enhancements.form_kit.saver"
USER = "nik@example.com"
TODAY = "2026-10-14"


def _text(path):
	return path.read_text(encoding="utf-8")


def _code(path):
	src = re.sub(r"/\*[\s\S]*?\*/", "", _text(path))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def _body(code, head):
	"""One method's body, from its head to the first line that closes it at the class's indent."""
	start = code.index(head)
	return code[start : code.index("\n\t}\n", start)]


# ---------------------------------------------------------------------------------- under node

RUNNER = r"""
import { pathToFileURL } from "node:url";
const [dir, inputJson] = process.argv.slice(2);
const fk = await import(pathToFileURL(dir + "/form_kit/index.js").href);
const kit = await import(pathToFileURL(dir + "/opportunity_board/board.js").href);
const input = JSON.parse(inputJson);
const out = {};
const flow = input.flow;

// ---- the side panel's decisions
out.gate_won = fk.gate_for(flow, "Closed Won");
out.gate_lost = fk.gate_for(flow, "Lost");
out.gate_open = fk.gate_for(flow, "Needs Analysis");
out.gate_none = fk.gate_for({}, "Closed Won");
out.defs = fk.field_defs(flow, ["custom_budget_rank", "nope", "custom_scope_rank"]).map((f) => [f.fieldname, f.anchor]);
const step = fk.panel_step("move", "T", "I", fk.field_defs(flow, flow.won.fields));
out.step = [step.key, step.synthetic, step.fields.length];
out.missing_all = fk.gate_missing(step.fields, {}).map((f) => f.fieldname);
out.missing_some = fk.gate_missing(step.fields, { custom_scope_rank: "1", custom_schedule_rank: " ", custom_budget_rank: 3 }).map((f) => f.fieldname);
out.lost_fields = fk.lost_fields(flow.lost, (s) => "~" + s).map((f) => [f.fieldname, f.fieldtype, f.options || "", f.reqd || 0, f.label]);
const create_step = {
	fields: [
		{ fieldname: "custom_opportunity_name", fieldtype: "Data", create: 1 },
		{ fieldname: "custom_opportunity_summary", fieldtype: "Data" },
		{ fieldname: "probability", fieldtype: "Percent" },
		{ fieldname: "territory", fieldtype: "Link", read_only: 1 },
		{ fieldname: "custom_value_stream", fieldtype: "Table MultiSelect" },
		{ fieldtype: "Column Break", fieldname: "cb" },
	],
};
out.extras = fk.create_extras(
	create_step,
	{ custom_opportunity_name: "X", custom_opportunity_summary: "A pond", probability: "100", territory: "West", custom_value_stream: [{ value_stream: "Design" }] },
	{ probability: 100, custom_opportunity_summary: null }
);
out.quick = fk.quick_start(input.new_flow, "nik@example.com", "Needs Analysis");
out.quick_owner_kept = fk.quick_start(Object.assign({}, input.new_flow, { values: { opportunity_owner: "lisa@example.com" } }), "nik@example.com", "").values.opportunity_owner;
out.quick_none = fk.quick_start({ values: {} }, "u", "s");
out.keys = [
	fk.panel_key({ kind: "edit", name: "A", step: "x" }),
	fk.panel_key({ kind: "move", name: "A", status: "Lost" }),
	fk.panel_key({ kind: "new", stage: "Qualification" }),
	fk.panel_key(null),
];
out.undo = fk.undo_values({ a: 1, b: "x", c: null, d: 5 }, { a: "1.0", b: "y", c: "z", e: 9 });
// No Undo for an edit that moved the record into or out of a closing status (review of #1286).
const closed_flow = Object.assign({}, flow, { closed: { field: "status", in: ["Closed Won", "Lost", "Closed", "Converted"] } });
out.undo_allowed = {
	plain: fk.undo_allowed(closed_flow, { status: "Qualification", x: 1 }, { x: 2 }),
	open_to_open: fk.undo_allowed(closed_flow, { status: "Qualification" }, { status: "Needs Analysis" }),
	into_won: fk.undo_allowed(closed_flow, { status: "Proposal/Price Quote" }, { status: "Closed Won", custom_scope_rank: "1" }),
	into_lost: fk.undo_allowed(closed_flow, { status: "Qualification" }, { status: "Lost" }),
	into_converted: fk.undo_allowed(closed_flow, { status: "Qualification" }, { status: "Converted" }),
	out_of_won: fk.undo_allowed(closed_flow, { status: "Closed Won" }, { status: "Qualification" }),
	unchanged_won: fk.undo_allowed(closed_flow, { status: "Closed Won" }, { status: "Closed Won", x: 1 }),
	won_without_closed: fk.undo_allowed(flow, { status: "Qualification" }, { status: "Closed Won" }),
	// v1.677.0: fails closed. No flow, or one that says nothing about closing, cannot be checked.
	no_flow: fk.undo_allowed(null, { x: 1 }, { x: 2 }),
	no_rules: fk.undo_allowed({ steps: [] }, { x: 1 }, { x: 2 }),
};

// ---- the save queue's manual mode: nothing autosaves; flush sends it all at once
const timers = [];
const sent = [];
const q = fk.create_save_queue({
	manual: true,
	send: (values, at) => {
		sent.push([values, at]);
		return Promise.resolve({ modified: "m2", values });
	},
	set_timeout: (fn, ms) => timers.push([fn, ms]),
	clear_timeout: () => {},
});
q.set_modified("m1");
q.change("custom_scope_rank", "1");
q.change("status", "Closed Won");
out.manual_timers = timers.length;
out.manual_state = q.state;
await q.flush();
out.manual_sent = sent;
out.manual_after = [q.state, q.modified];

// ---- board part C: routes
const P = (r) => kit.parse_route(r);
out.routes = {
	edit: P(["opportunity-board", "stages", "edit", "CRM-OPP-1"]),
	edit_step: P(["opportunity-board", "streams", "on-hold", "edit", "CRM-OPP-1", "close"]),
	closing: P(["opportunity-board", "table", "closing", "CRM-OPP-1", "closed-won"]),
	closing_short: P(["opportunity-board", "stages", "closing", "CRM-OPP-1"]),
	add: P(["opportunity-board", "stages", "new", "qualification"]),
	bulk: P(["opportunity-board", "attention", "bulk", "owner"]),
	bulk_bad: P(["opportunity-board", "attention", "bulk", "delete"]),
	move: P(["opportunity-board", "stages", "move", "CRM-OPP-1"]),
	build_edit: kit.build_route({ view: "board", layout: "stages", stage: "", side: { kind: "edit", name: "N", step: "basics" } }),
	build_closing: kit.build_route({ view: "timeline", side: { kind: "closing", name: "N", stage: "lost" } }),
	build_new: kit.build_route({ view: "board", layout: "streams", stage: "on-hold", side: { kind: "new", stage: "on-hold" } }),
	build_bulk: kit.build_route({ view: "table", side: { kind: "bulk", action: "snooze" } }),
	build_side_wins: kit.build_route({ view: "board", layout: "stages", panel: "OLD", side: { kind: "edit", name: "N" } }),
	round: P(kit.build_route({ view: "board", layout: "stages", stage: "qualification", side: { kind: "edit", name: "N 1", step: "scope" } })),
	key_step_same: kit.side_key({ kind: "edit", name: "N", step: "a" }) === kit.side_key({ kind: "edit", name: "N", step: "b" }),
	keys: [kit.side_key({ kind: "closing", name: "N", stage: "lost" }), kit.side_key({ kind: "new", stage: "q" }), kit.side_key({ kind: "bulk", action: "owner" }), kit.side_key(null)],
	route_keys_differ: kit.route_key(P(["opportunity-board", "stages", "edit", "A"])) !== kit.route_key(P(["opportunity-board", "stages"])),
	stage_words: ["New", "Move", "Edit", "Closing", "Bulk", "Qualification"].map(kit.stage_key),
};

// ---- the model: gates, quick add's columns, what a side panel may show
const model = kit.prepare(input.payload);
out.closing = model.stages.filter((s) => s.kind === "close").map((s) => [s.key, s.gate]);
out.can_add = model.stages.map((s) => [s.key, kit.can_add(model, s.key)]);
out.closing_stage = [kit.closing_stage(model, "lost") && kit.closing_stage(model, "lost").gate, kit.closing_stage(model, "qualification")];
out.side_ok = {
	edit_any: kit.side_ok(model, { kind: "edit", name: "NOT-ON-BOARD" }, []),
	closing: kit.side_ok(model, { kind: "closing", name: "A", stage: "closed-won" }, []),
	closing_gone: kit.side_ok(model, { kind: "closing", name: "GONE", stage: "closed-won" }, []),
	closing_open_stage: kit.side_ok(model, { kind: "closing", name: "A", stage: "qualification" }, []),
	new_open: kit.side_ok(model, { kind: "new", stage: "qualification" }, []),
	new_hold: kit.side_ok(model, { kind: "new", stage: "on-hold" }, []),
	new_closing: kit.side_ok(model, { kind: "new", stage: "lost" }, []),
	bulk_empty: kit.side_ok(model, { kind: "bulk", action: "owner" }, []),
	bulk: kit.side_ok(model, { kind: "bulk", action: "owner" }, ["A"]),
	none: kit.side_ok(model, null, ["A"]),
};
const removed = kit.remove_deal(model, "A");
out.removed = [removed.deals.map((d) => d.name), !!removed.by_name.A, model.deals.length, kit.remove_deal(model, "ZZ") === model];

// ---- selection and bulk
out.toggle = [kit.toggle_pick(["A"], "B"), kit.toggle_pick(["A", "B"], "A"), kit.toggle_pick(null, ""), kit.toggle_pick(["A"], " ")];
out.clean = kit.clean_picks(model, ["A", "GONE", "B", "A", 7, null]);
const many = { by_name: {} };
for (let i = 0; i < 150; i++) many.by_name["D" + i] = { name: "D" + i };
out.clean_cap = kit.clean_picks(many, Object.keys(many.by_name)).length;
out.bulk_stages = kit.bulk_stages(model).map((s) => s.key);
out.request = kit.bulk_request(model, ["B", "GONE", "A"], "owner", { owner: "lisa@example.com" });
const big = { by_name: {} };
for (let i = 0; i < 40; i++) big.by_name["D" + i] = { name: "D" + i, modified: "m" };
const picks40 = Object.keys(big.by_name);
out.caps = [kit.bulk_cap("stage"), kit.bulk_cap("owner"), kit.bulk_cap("snooze"), kit.BULK_CAPS];
out.over_stage = [kit.bulk_request(big, picks40, "stage", {}).names.length, kit.bulk_request(big, picks40, "stage", {}).over];
out.over_owner = [kit.bulk_request(big, picks40, "owner", {}).names.length, kit.bulk_request(big, picks40, "owner", {}).over];
const outcome = kit.bulk_outcome(["A", "B", "C", "D"], [
	{ name: "A", ok: 1, modified: "m" },
	{ name: "B", conflict: 1, modified_by: "office@example.com" },
	{ name: "C", ok: 0, message: "Closed Won needs its ranks" },
]);
out.outcome = outcome;
out.after = kit.after_bulk(["A", "B", "C", "D"], outcome);
out.constants = [kit.MAX_BULK, kit.BULK_ACTIONS, kit.PICKS_KEY];
console.log(JSON.stringify(out));
"""

STAGES = [
	{"label": "Qualification", "kind": "open"},
	{"label": "Needs Analysis", "kind": "open"},
	{"label": "On Hold", "kind": "parked"},
	{"label": "Lost", "kind": "close", "gate": "lost"},
	{"label": "Closed Won", "kind": "close", "gate": "won"},
]


def _deal(name, status):
	return {
		"name": name,
		"title": f"Deal {name}",
		"account": "Example Co",
		"status": status,
		"amount": 1000,
		"probability": 10,
		"expected_closing": "2026-11-01",
		"since": "2026-10-01",
		"modified": f"2026-10-13 09:00:00.{name}",
		"streams": ["Design"],
		"completeness": None,
		"owner": USER,
		"owner_name": "Nik",
		"snoozed_until": None,
		"snooze_reason": "",
	}


PAYLOAD = {
	"stages": [*STAGES, {"label": "Replied", "kind": "open"}],
	"deals": [
		_deal("A", "Qualification"),
		_deal("B", "Needs Analysis"),
		_deal("C", "On Hold"),
		_deal("R", "Old Status"),
	],
	"thresholds": {"amber": 7, "red": 14},
	"today": TODAY,
	"currency": "USD",
}

RANKS = ["custom_scope_rank", "custom_schedule_rank", "custom_budget_rank"]


def _flow_field(fieldname, fieldtype="Data", **extra):
	return {"fieldname": fieldname, "fieldtype": fieldtype, "label": fieldname, **extra}


FLOW = {
	"steps": [
		{"key": "basics", "create": 1, "fields": [_flow_field("custom_opportunity_name", reqd=1, create=1)]},
		{
			"key": "close",
			"fields": [
				_flow_field("status", "Select"),
				{"fieldtype": "Column Break", "fieldname": "cb"},
				*[_flow_field(f, "Select", options="\n1\n2\n3") for f in RANKS],
			],
		},
	],
	"won": {"status": "Closed Won", "status_field": "status", "fields": RANKS},
	"lost": {
		"status": "Lost",
		"status_field": "status",
		"reasons": "Opportunity Lost Reason Detail",
		"competitors": "Competitor Detail",
	},
}
NEW_FLOW = {
	"values": {"status": "Qualification", "opportunity_owner": None, "opportunity_from": "Lead"},
	"quick": {
		"fields": [
			"party_name",
			"custom_opportunity_name",
			"custom_value_stream",
			"custom_opportunity_summary",
		],
		"fixed": {"opportunity_from": "Customer"},
		"owner_field": "opportunity_owner",
		"stage_field": "status",
	},
}


def _run_node():
	node = shutil.which("node")
	with tempfile.TemporaryDirectory() as tmp:
		folder = Path(tmp)
		(folder / "package.json").write_text('{"type": "module"}', encoding="utf-8")
		shutil.copytree(BOARD_JS, folder / "opportunity_board")
		shutil.copytree(FORM_KIT_JS, folder / "form_kit")
		(folder / "runner.mjs").write_text(RUNNER, encoding="utf-8")
		payload = {"flow": FLOW, "new_flow": NEW_FLOW, "payload": PAYLOAD}
		proc = subprocess.run(
			[node, str(folder / "runner.mjs"), str(folder), json.dumps(payload)],
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=60,
		)
	if proc.returncode != 0:
		raise AssertionError(f"node failed:\n{proc.stderr}")
	return json.loads(proc.stdout)


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestThePanelsDecisions(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.out = _run_node()

	def test_a_move_into_closed_won_carries_the_ranks_and_lost_its_reasons(self):
		o = self.out
		self.assertEqual(o["gate_won"], {"kind": "won", "status": "Closed Won", "fieldnames": RANKS})
		self.assertEqual(o["gate_lost"], {"kind": "lost", "status": "Lost"})
		self.assertIsNone(o["gate_open"], "an open stage needs nothing beyond its status")
		self.assertIsNone(o["gate_none"])

	def test_the_panels_own_step_is_built_from_the_flows_own_fields(self):
		o = self.out
		self.assertEqual(o["defs"], [["custom_budget_rank", "close"], ["custom_scope_rank", "close"]])
		self.assertEqual(o["step"], ["move", 1, 3])

	def test_every_required_field_of_a_move_is_required_whatever_its_reqd(self):
		self.assertEqual(self.out["missing_all"], RANKS)
		self.assertEqual(self.out["missing_some"], ["custom_schedule_rank"])

	def test_set_as_lost_asks_the_same_three_things_everywhere(self):
		self.assertEqual(
			self.out["lost_fields"],
			[
				["reasons", "Table MultiSelect", "Opportunity Lost Reason Detail", 1, "~Lost Reasons"],
				["competitors", "Table MultiSelect", "Competitor Detail", 0, "~Competitors"],
				["detail", "Small Text", "", 0, "~Detailed Reason"],
			],
		)

	def test_a_create_carries_what_step_one_showed_and_the_person_changed(self):
		# Not the create fields (create_record takes those), not a default, not read-only, not a table.
		self.assertEqual(self.out["extras"], {"custom_opportunity_summary": "A pond"})

	def test_quick_add_starts_from_the_defaults_fixed_values_the_person_and_the_column(self):
		q = self.out["quick"]
		self.assertEqual(q["fieldnames"], NEW_FLOW["quick"]["fields"])
		self.assertEqual(q["values"]["opportunity_from"], "Customer", "fixed wins over the default")
		self.assertEqual(q["values"]["opportunity_owner"], USER, "the person adding, when blank")
		self.assertEqual(q["carry"], {"status": "Needs Analysis"})
		self.assertEqual(self.out["quick_owner_kept"], "lisa@example.com", "a default owner is kept")
		self.assertIsNone(self.out["quick_none"])

	def test_panel_keys(self):
		self.assertEqual(self.out["keys"], ["edit:A", "move:A:Lost", "new:Qualification", ""])

	def test_undo_puts_back_only_what_changed(self):
		self.assertEqual(self.out["undo"], {"b": "x", "c": None})

	def test_no_undo_for_an_edit_that_moved_into_or_out_of_a_closing_status(self):
		"""Review of #1286: an Undo of a Close-step win is a plain save_step, which would skip
		revert_won_status's refusal once a project exists and leave the won date set."""
		u = self.out["undo_allowed"]
		self.assertTrue(u["plain"])
		self.assertTrue(u["open_to_open"])
		self.assertTrue(u["unchanged_won"], "editing a won deal's other fields keeps its Undo")
		for case in ("into_won", "into_lost", "into_converted", "out_of_won", "won_without_closed", "no_flow", "no_rules"):
			with self.subTest(case=case):
				self.assertFalse(u[case])

	def test_a_manual_queue_never_autosaves_and_sends_everything_in_one_save(self):
		o = self.out
		self.assertEqual(o["manual_timers"], 0)
		self.assertEqual(o["manual_state"], "dirty")
		self.assertEqual(o["manual_sent"], [[{"custom_scope_rank": "1", "status": "Closed Won"}, "m1"]])
		self.assertEqual(o["manual_after"], ["saved", "m2"])


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestTheBoardsPartC(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.out = _run_node()

	def test_each_side_panel_is_a_route_over_any_view(self):
		r = self.out["routes"]
		self.assertEqual(r["edit"]["side"], {"kind": "edit", "name": "CRM-OPP-1", "step": ""})
		self.assertEqual(r["edit"]["panel"], "")
		self.assertEqual(
			(r["edit_step"]["layout"], r["edit_step"]["stage"], r["edit_step"]["side"]),
			("streams", "on-hold", {"kind": "edit", "name": "CRM-OPP-1", "step": "close"}),
		)
		self.assertEqual(r["closing"]["view"], "table")
		self.assertEqual(
			r["closing"]["side"], {"kind": "closing", "name": "CRM-OPP-1", "stage": "closed-won"}
		)
		self.assertIsNone(r["closing_short"]["side"], "a closing move names its stage")
		self.assertEqual(r["add"]["side"], {"kind": "new", "stage": "qualification"})
		self.assertEqual(r["bulk"]["side"], {"kind": "bulk", "action": "owner"})
		self.assertIsNone(r["bulk_bad"]["side"])
		self.assertEqual((r["move"]["panel"], r["move"]["side"]), ("CRM-OPP-1", None))

	def test_routes_are_built_as_set_route_takes_them(self):
		r = self.out["routes"]
		self.assertEqual(r["build_edit"], ["opportunity-board", "stages", "edit", "N", "basics"])
		self.assertEqual(r["build_closing"], ["opportunity-board", "timeline", "closing", "N", "lost"])
		self.assertEqual(r["build_new"], ["opportunity-board", "streams", "on-hold", "new", "on-hold"])
		self.assertEqual(r["build_bulk"], ["opportunity-board", "table", "bulk", "snooze"])
		self.assertEqual(
			r["build_side_wins"], ["opportunity-board", "stages", "edit", "N"], "one panel at a time"
		)
		self.assertEqual(r["round"]["side"], {"kind": "edit", "name": "N 1", "step": "scope"})
		self.assertEqual(r["round"]["stage"], "qualification")

	def test_the_step_is_not_part_of_the_panels_identity(self):
		r = self.out["routes"]
		self.assertTrue(
			r["key_step_same"], "a step change replaces the entry; closing still steps back off it"
		)
		self.assertEqual(r["keys"], ["closing:N:lost", "new:q", "bulk:owner", ""])
		self.assertTrue(r["route_keys_differ"])

	def test_a_stage_named_like_a_panel_never_reads_as_one(self):
		self.assertEqual(
			self.out["routes"]["stage_words"],
			["new-stage", "move-stage", "edit-stage", "closing-stage", "bulk-stage", "qualification"],
		)

	def test_closing_stages_say_what_they_need(self):
		self.assertEqual(self.out["closing"], [["lost", "lost"], ["closed-won", "won"]])
		self.assertEqual(self.out["closing_stage"], ["lost", None])

	def test_quick_add_is_on_open_columns_and_on_hold_only(self):
		can = dict(self.out["can_add"])
		self.assertTrue(can["qualification"] and can["needs-analysis"] and can["on-hold"])
		self.assertFalse(can["lost"] or can["closed-won"], "a closing stage needs its fields first")
		self.assertFalse(can["old-status"], "a status the options no longer list")

	def test_which_side_panel_an_address_may_show(self):
		s = self.out["side_ok"]
		self.assertTrue(s["edit_any"], "the panel reads any deal itself")
		self.assertTrue(s["closing"])
		self.assertFalse(s["closing_gone"], "a deal that has left the board")
		self.assertFalse(s["closing_open_stage"])
		self.assertTrue(s["new_open"] and s["new_hold"])
		self.assertFalse(s["new_closing"])
		self.assertFalse(s["bulk_empty"], "a reload with nothing selected")
		self.assertTrue(s["bulk"])
		self.assertFalse(s["none"])

	def test_a_closed_deal_leaves_the_board_and_the_old_model_is_untouched(self):
		names, still, before, same = self.out["removed"]
		self.assertNotIn("A", names)
		self.assertFalse(still)
		self.assertEqual(before, 4)
		self.assertTrue(same)

	def test_the_selection(self):
		self.assertEqual(self.out["toggle"], [["A", "B"], ["B"], [], ["A"]])
		self.assertEqual(self.out["clean"], ["A", "B"], "on the board, each once")
		self.assertEqual(self.out["clean_cap"], 100)
		self.assertEqual(self.out["constants"], [100, ["stage", "owner", "close_date", "snooze"], "ob-picks"])

	def test_bulk_offers_every_stage_but_an_unlisted_one_and_closed_won(self):
		"""Closed Won is not a bulk move (review of #1286, a departure from P6.7's text): each won
		deal needs its own ranks and its own hand-off prompt."""
		self.assertEqual(
			self.out["bulk_stages"],
			["qualification", "needs-analysis", "on-hold", "lost", "replied"],
		)
		self.assertNotIn("old-status", self.out["bulk_stages"])
		self.assertNotIn("closed-won", self.out["bulk_stages"])

	def test_a_bulk_request_carries_each_deals_lock(self):
		req = self.out["request"]
		self.assertEqual(req["names"], ["B", "A"])
		self.assertEqual(req["modified"], {"B": "2026-10-13 09:00:00.B", "A": "2026-10-13 09:00:00.A"})
		self.assertEqual((req["action"], req["values"]), ("owner", {"owner": "lisa@example.com"}))
		self.assertEqual(req["over"], 0)

	def test_a_stage_move_takes_smaller_batches_and_says_when_a_selection_is_over(self):
		self.assertEqual(self.out["caps"], [25, 100, 100, {"stage": 25}])
		self.assertEqual(self.out["over_stage"], [25, 15])
		self.assertEqual(self.out["over_owner"], [40, 0])

	def test_the_answer_is_deal_by_deal_and_what_failed_stays_selected(self):
		o = self.out["outcome"]
		self.assertEqual(o["ok"], ["A"])
		self.assertEqual(o["conflicts"], [{"name": "B", "modified_by": "office@example.com"}])
		self.assertEqual(
			o["failed"],
			[{"name": "C", "message": "Closed Won needs its ranks"}, {"name": "D", "message": ""}],
		)
		self.assertEqual(o["total"], 4)
		self.assertEqual(self.out["after"], ["B", "C", "D"])


# ---------------------------------------------------------------------------------- the bulk endpoint

STATE = {}


class _Field:
	def __init__(self, fieldname, options=None):
		self.fieldname = fieldname
		self.options = options

	def get(self, key, default=None):
		return getattr(self, key, default)


class _Meta:
	def __init__(self):
		self.fields = {
			"status": _Field(
				"status",
				"Qualification\nNeeds Analysis\nProposal/Price Quote\nLost\nClosed Won\nOn Hold\nClosed\nConverted",
			),
			"custom_snoozed_until": _Field("custom_snoozed_until"),
			"custom_snooze_reason": _Field("custom_snooze_reason"),
		}

	def get_field(self, fieldname):
		return self.fields.get(fieldname)

	def has_field(self, fieldname):
		return fieldname in self.fields


class _Doc:
	def __init__(self, name, status="Qualification", **values):
		self.name = name
		self.doctype = "Opportunity"
		self.modified = datetime.datetime(2026, 10, 13, 9, 0, 0)
		self.values = {"status": status, **values}

	def get(self, key, default=None):
		return self.values.get(key, default)

	def check_permission(self, ptype):
		STATE["perm"].append((self.name, ptype))
		if self.name in STATE["no_write"]:
			raise sys.modules["frappe"].PermissionError(f"No permission to write {self.name}")


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe.__path__ = []

	def whitelist(methods=None, allow_guest=False, **kwargs):
		def wrap(fn):
			fn.whitelisted = {"methods": methods, "allow_guest": allow_guest}
			return fn

		return wrap

	class PermissionError(Exception):
		pass

	class ValidationError(Exception):
		pass

	class DoesNotExistError(ValidationError):
		pass

	def throw(message, exc=ValidationError, title=None):
		raise exc(message)

	frappe.whitelist = whitelist
	frappe._ = lambda text, *a, **k: text
	frappe._dict = dict
	frappe.PermissionError = PermissionError
	frappe.ValidationError = ValidationError
	frappe.DoesNotExistError = DoesNotExistError
	frappe.throw = throw
	frappe.session = types.SimpleNamespace(user=USER)
	frappe.parse_json = json.loads
	frappe.clear_messages = lambda: STATE["calls"].append(("clear_messages",))

	def has_permission(doctype, ptype="read", doc=None, throw=False, **kwargs):
		STATE["calls"].append(("has_permission", doctype, ptype))
		allowed = ptype not in STATE["deny"]
		if not allowed and throw:
			raise PermissionError(f"No {ptype} on {doctype}")
		return allowed

	frappe.has_permission = has_permission
	frappe.get_meta = lambda doctype: _Meta()

	def get_doc(doctype, name):
		if name not in STATE["docs"]:
			raise DoesNotExistError(f"Opportunity {name} not found")
		return STATE["docs"][name]

	frappe.get_doc = get_doc

	def exists(doctype, filters):
		STATE.setdefault("user_filters", []).append(dict(filters))
		user_type = STATE["users"].get(filters.get("name"))
		return bool(user_type) and filters.get("user_type", user_type) == user_type

	frappe.db = types.SimpleNamespace(
		savepoint=lambda name: STATE["calls"].append(("savepoint", name)),
		rollback=lambda save_point=None: STATE["calls"].append(("rollback", save_point)),
		exists=exists,
		has_column=lambda *a: True,
		sql=lambda *a, **k: [],
	)
	frappe.publish_realtime = lambda *a, **k: None
	frappe.log_error = lambda *a, **k: None
	frappe.get_traceback = lambda: ""
	frappe.defaults = types.SimpleNamespace(get_global_default=lambda key: "USD")
	frappe.get_single = lambda doctype: {}

	utils = types.ModuleType("frappe.utils")

	def getdate(value=None):
		if value is None:
			return datetime.date.fromisoformat(TODAY)
		if isinstance(value, datetime.date):
			return value
		return datetime.date.fromisoformat(str(value)[:10])

	utils.getdate = getdate
	utils.get_datetime = (
		lambda v: v if isinstance(v, datetime.datetime) else datetime.datetime.fromisoformat(str(v))
	)
	utils.flt = lambda v, p=None: float(v or 0)
	utils.cint = lambda v: int(float(v or 0))
	utils.nowdate = lambda: TODAY
	utils.add_days = lambda value, days: str(getdate(value) + datetime.timedelta(days=days))
	utils.now_datetime = lambda: datetime.datetime(2026, 10, 14, 12)
	utils.date_diff = lambda a, b: (getdate(a) - getdate(b)).days
	utils.escape_html = lambda s: str(s)
	utils.formatdate = lambda v: str(v)
	utils.strip_html = lambda s: re.sub(r"<[^>]+>", "", str(s))
	utils.get_fullname = lambda u: u
	frappe.utils = utils
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	return frappe


def _install_saver():
	"""The form kit's saver, replaced by a recorder that keeps the real one's rules: the lock on
	``modified`` (the conflict shape), write permission on the deal, Closed Won refused without its
	three ranks, and only what the flow lets the person write assigned."""
	saver = types.ModuleType(SAVER)
	frappe = sys.modules["frappe"]

	def _lock(doc, modified):
		if not modified:
			frappe.throw("The save did not say which version it was made on.")
		if str(doc.modified) != str(modified):
			return {
				"conflict": True,
				"name": doc.name,
				"modified": str(doc.modified),
				"modified_by": "office@example.com",
			}
		return None

	def save_step(doctype, name, values, modified, step=None):
		STATE["saves"].append(("save_step", name, dict(values), modified))
		doc = frappe.get_doc(doctype, name)
		doc.check_permission("write")
		conflict = _lock(doc, modified)
		if conflict:
			return conflict
		assigned = {k: v for k, v in values.items() if k not in STATE["not_writable"]}
		after = {**doc.values, **assigned}
		if after.get("status") == "Closed Won" and not all(
			str(after.get(f) or "") in ("1", "2", "3") for f in RANKS
		):
			frappe.throw("<b>Scope Rank</b>, Schedule Rank and Budget Rank must be 1, 2 or 3.")
		doc.values = after
		doc.modified = datetime.datetime(2026, 10, 14, 10, 0, len(STATE["saves"]))
		return {"name": name, "modified": str(doc.modified), "values": assigned, "completeness": {}}

	def declare_lost(doctype, name, reasons, competitors=None, detail=None, modified=None):
		STATE["saves"].append(("declare_lost", name, reasons, competitors, detail, modified))
		doc = frappe.get_doc(doctype, name)
		doc.check_permission("write")
		conflict = _lock(doc, modified)
		if conflict:
			return conflict
		doc.values["status"] = "Lost"
		return {
			"name": name,
			"modified": "2026-10-14 10:00:00",
			"values": {"status": "Lost"},
			"completeness": {},
		}

	saver.save_step = save_step
	saver.declare_lost = declare_lost
	sys.modules[SAVER] = saver
	import erpnext_enhancements.form_kit as form_kit_pkg

	form_kit_pkg.saver = saver
	return saver


_SAVED_MODULES = {}


def _ours(name):
	return (
		name in ("frappe", "frappe.utils", "erpnext_enhancements.feature_flags")
		or name.startswith("erpnext_enhancements.crm_enhancements")
		or name.startswith("erpnext_enhancements.form_kit")
	)


def setUpModule():
	for name in list(sys.modules):
		if _ours(name):
			_SAVED_MODULES[name] = sys.modules.pop(name)
	_install_frappe_stub()


def tearDownModule():
	for name in list(sys.modules):
		if _ours(name):
			sys.modules.pop(name, None)
	sys.modules.update(_SAVED_MODULES)


MOD = "2026-10-13 09:00:00"


class TestTheBulkEndpoint(unittest.TestCase):
	def setUp(self):
		self.mod = importlib.import_module(MODULE)
		_install_saver()
		STATE.clear()
		STATE.update(
			{
				"docs": {
					"A": _Doc("A"),
					"B": _Doc("B", "Needs Analysis"),
					"C": _Doc("C", custom_scope_rank="2", custom_schedule_rank="1", custom_budget_rank="3"),
					"W": _Doc("W", "Closed Won"),
				},
				"no_write": set(),
				"not_writable": set(),
				"deny": set(),
				"users": {"lisa@example.com": "System User", "portal@example.com": "Website User"},
				"calls": [],
				"perm": [],
				"saves": [],
			}
		)

	def bulk(self, names, action, values, modified=None):
		mods = modified if modified is not None else {n: MOD for n in names}
		return self.mod.bulk_update(json.dumps(names), action, json.dumps(values), json.dumps(mods))[
			"results"
		]

	def test_it_is_post_only_and_checks_write_first(self):
		self.assertEqual(self.mod.bulk_update.whitelisted["methods"], ["POST"])
		self.assertFalse(self.mod.bulk_update.whitelisted["allow_guest"])
		STATE["deny"].add("write")
		with self.assertRaises(sys.modules["frappe"].PermissionError):
			self.bulk(["A"], "owner", {"owner": "lisa@example.com"})
		self.assertEqual(STATE["saves"], [], "nothing is touched")

	def test_every_deal_takes_the_single_change_path_with_its_own_lock(self):
		results = self.bulk(["A", "B"], "owner", {"owner": "lisa@example.com"}, {"A": MOD, "B": MOD})
		self.assertEqual([r["ok"] for r in results], [1, 1])
		self.assertEqual(
			[s[:4] for s in STATE["saves"]],
			[
				("save_step", "A", {"opportunity_owner": "lisa@example.com"}, MOD),
				("save_step", "B", {"opportunity_owner": "lisa@example.com"}, MOD),
			],
		)
		self.assertIn(("A", "write"), STATE["perm"])
		self.assertIn(("B", "write"), STATE["perm"])

	def test_some_fail_and_the_rest_go_through(self):
		STATE["no_write"].add("B")
		results = self.bulk(
			["A", "B", "C", "GONE"], "close_date", {"date": "2026-12-15"}, {"A": MOD, "B": MOD, "C": "older"}
		)
		by = {r["name"]: r for r in results}
		self.assertEqual(by["A"]["ok"], 1)
		self.assertEqual(by["B"], {"name": "B", "ok": 0, "message": "No permission to write B"})
		self.assertEqual(by["C"], {"name": "C", "conflict": 1, "modified_by": "office@example.com"})
		self.assertEqual(by["GONE"]["ok"], 0)
		self.assertEqual([r["name"] for r in results], ["A", "B", "C", "GONE"], "in the order sent")
		# Each deal in its own savepoint; a refusal rolls back only that deal, and its message is cleared.
		calls = [c for c in STATE["calls"] if c[0] in ("savepoint", "rollback", "clear_messages")]
		self.assertEqual(calls.count(("savepoint", self.mod.BULK_SAVEPOINT)), 4)
		self.assertEqual(calls.count(("rollback", self.mod.BULK_SAVEPOINT)), 2)
		self.assertEqual(calls.count(("clear_messages",)), 2)
		self.assertEqual(STATE["docs"]["A"].values["expected_closing"], "2026-12-15")

	def test_a_change_without_its_lock_is_refused(self):
		results = self.bulk(["A"], "owner", {"owner": "lisa@example.com"}, {})
		self.assertEqual(results[0]["ok"], 0)
		self.assertIn("which version", results[0]["message"])

	def test_closed_won_is_never_a_bulk_move(self):
		"""Review of #1286: each won deal needs its own ranks and its own hand-off prompt (twenty
		stacked, none naming its deal, and Esc on one un-wins it), so a bulk win is refused before any
		deal is touched, with or without ranks."""
		ranks = {"custom_scope_rank": "1", "custom_schedule_rank": "2", "custom_budget_rank": "3"}
		for values in ({"status": "Closed Won"}, {"status": "Closed Won", "ranks": ranks}):
			with (
				self.subTest(values=values),
				self.assertRaises(sys.modules["frappe"].ValidationError) as caught,
			):
				self.bulk(["A", "C"], "stage", values)
			self.assertEqual(str(caught.exception), self.mod.WIN_ONE_AT_A_TIME)
		self.assertEqual(STATE["saves"], [])
		self.assertEqual(
			self.mod.WIN_ONE_AT_A_TIME, "Win deals one at a time: each needs its own ranks and hand-off."
		)

	def test_an_open_stage_move_carries_only_the_status(self):
		results = self.bulk(["A", "C"], "stage", {"status": "Proposal/Price Quote", "ranks": {"x": 1}})
		self.assertEqual([r["ok"] for r in results], [1, 1])
		self.assertEqual([s[2] for s in STATE["saves"]], [{"status": "Proposal/Price Quote"}] * 2)

	def test_a_stage_move_takes_at_most_25_deals(self):
		self.assertEqual(self.mod.MAX_BULK_STAGE, 25)
		names = [f"D{i}" for i in range(26)]
		with self.assertRaises(sys.modules["frappe"].ValidationError):
			self.bulk(names, "stage", {"status": "Needs Analysis"})
		results = self.bulk(names, "close_date", {"date": "2026-12-15"})
		self.assertEqual(len(results), 26, "owner, close date and snooze keep 100")

	def test_lost_runs_declare_lost_with_the_reasons(self):
		results = self.bulk(
			["A", "B"],
			"stage",
			{
				"status": "Lost",
				"reasons": [{"lost_reason": "Price"}],
				"competitors": [],
				"detail": "Too dear",
			},
		)
		self.assertEqual([r["ok"] for r in results], [1, 1])
		self.assertEqual(
			STATE["saves"][0], ("declare_lost", "A", [{"lost_reason": "Price"}], [], "Too dear", MOD)
		)

	def test_lost_without_a_reason_is_refused_before_any_deal(self):
		with self.assertRaises(sys.modules["frappe"].ValidationError):
			self.bulk(["A"], "stage", {"status": "Lost", "reasons": []})
		self.assertEqual(STATE["saves"], [])

	def test_a_stage_not_offered_is_refused(self):
		for status in ("", "Converted", "Closed", "Nonsense"):
			with self.subTest(status=status), self.assertRaises(sys.modules["frappe"].ValidationError):
				self.bulk(["A"], "stage", {"status": status})

	def test_a_deal_already_there_is_left_alone(self):
		results = self.bulk(["B"], "stage", {"status": "Needs Analysis"})
		self.assertEqual(results[0]["ok"], 1)
		self.assertEqual(STATE["saves"], [], "no save, so no new modified for anyone else")

	def test_a_closed_deal_is_not_changed(self):
		results = self.bulk(["W"], "owner", {"owner": "lisa@example.com"})
		self.assertEqual(results[0]["ok"], 0)
		self.assertIn("closed", results[0]["message"])

	def test_a_field_the_person_cannot_write_is_a_refusal_not_a_success(self):
		STATE["not_writable"].add("opportunity_owner")
		results = self.bulk(["A"], "owner", {"owner": "lisa@example.com"})
		self.assertEqual(results[0]["ok"], 0)
		self.assertIn("cannot change", results[0]["message"])

	def test_the_owner_must_be_an_enabled_system_user(self):
		with self.assertRaises(sys.modules["frappe"].ValidationError):
			self.bulk(["A"], "owner", {"owner": "nobody@example.com"})
		with self.assertRaises(sys.modules["frappe"].ValidationError):
			self.bulk(["A"], "owner", {"owner": "portal@example.com"})
		self.assertEqual(STATE["user_filters"][-1]["user_type"], "System User")
		self.assertEqual(STATE["user_filters"][-1]["enabled"], 1)

	def test_the_request_shape_is_checked(self):
		ValidationError = sys.modules["frappe"].ValidationError
		with self.assertRaises(ValidationError):
			self.bulk([], "owner", {"owner": "lisa@example.com"})
		with self.assertRaises(ValidationError):
			self.bulk(["A"], "delete", {})
		with self.assertRaises(ValidationError):
			self.bulk([f"D{i}" for i in range(self.mod.MAX_BULK + 1)], "owner", {"owner": "lisa@example.com"})
		with self.assertRaises(ValidationError):
			self.mod.bulk_update("not json", "owner", "{}", "{}")

	def test_snooze_goes_through_the_boards_own_snooze(self):
		calls = []
		original = self.mod.snooze
		self.mod.snooze = lambda name, until, reason, modified: calls.append(
			(name, until, reason, modified)
		) or {
			"name": name,
			"modified": "m",
		}
		try:
			results = self.bulk(["A", "B"], "snooze", {"until": "2026-10-20", "reason": "Vote"})
		finally:
			self.mod.snooze = original
		self.assertEqual([r["ok"] for r in results], [1, 1])
		self.assertEqual(calls, [("A", "2026-10-20", "Vote", MOD), ("B", "2026-10-20", "Vote", MOD)])

	def test_get_board_says_what_each_closing_stage_needs(self):
		closing = [s for s in self.mod._stages() if s["kind"] == "close"]
		# v1.688.0: Closed Won before On Hold, Lost last (Nik, 2026-10-14).
		self.assertEqual(
			closing,
			[
				{"label": "Closed Won", "kind": "close", "gate": "won"},
				{"label": "Lost", "kind": "close", "gate": "lost"},
			],
		)
		self.assertEqual(
			[s["label"] for s in self.mod._stages()][-3:], ["Closed Won", "On Hold", "Lost"]
		)


# ---------------------------------------------------------------------------------- one renderer


class TestOneRenderer(unittest.TestCase):
	def test_the_side_panel_is_the_record_editor(self):
		panel = _code(PANEL_JS)
		self.assertIn('import { RecordEditor, GE_API, ge_clone, ge_has } from "./editor.js";', panel)
		self.assertIn("export class SidePanel extends RecordEditor {", panel)
		# It draws a step with the editor's own render_fields, saves with the editor's queue, asks
		# about conflicts with its chooser, keeps the journal, creates with its create().
		for call in (
			"this.render_fields(",
			"this.restore_kept()",
			"this.after_restore(",
			"this.create()",
			"this.send_lost(",
			"this.lost_fields()",
			"super.on_conflict(",
			"super.on_saved(",
		):
			with self.subTest(call=call):
				self.assertIn(call, panel)
		for own in (
			"create_save_queue(",
			"split_conflict(",
			"localStorage",
			"new frappe.ui.Dialog",
			"make_entry(",
		):
			with self.subTest(own=own):
				self.assertNotIn(own, panel, "no second copy of what the editor does")

	def test_the_guided_page_runs_the_same_editor(self):
		page = _code(GUIDED_JS)
		self.assertIn("this.kit.mix_editor(GuidedEntry.prototype)", page)
		self.assertIn("this.render_fields(step, $fields, editable);", page)
		for moved in (
			"control_def(field, step, editable) {",
			"write_journal(doctype, name) {",
			"show_conflict(result, split, resolve) {",
			"\tcreate() {",
		):
			with self.subTest(method=moved):
				self.assertNotIn(moved, page, "the page keeps no copy of it")
				self.assertIn(moved, _code(EDITOR_JS))

	def test_the_bundle_carries_the_editor_and_the_panel(self):
		bundle = _text(GUIDED_BUNDLE)
		self.assertIn('import { RecordEditor, mix_editor } from "./form_kit/ui/editor.js";', bundle)
		self.assertIn('import { SidePanel } from "./form_kit/ui/side_panel.js";', bundle)
		# 2 brought the editor and the panel; 3 (form kit P8.1) the extensions; 4 (v1.684.0) the view
		# settings. A later version still carries them, and a tab holding an older one takes this one.
		version = re.search(r"const VERSION = (\d+);", bundle)
		self.assertTrue(version and int(version.group(1)) >= 4, "a tab holding an older kit must take this one")

	def test_every_request_is_quiet(self):
		for path in (EDITOR_JS, PANEL_JS):
			code = _code(path)
			with self.subTest(file=path.name):
				for forbidden in ("frappe.call(", "frappe.xcall(", "fetch(", "$.ajax("):
					self.assertNotIn(forbidden, code)
				for match in re.finditer(
					r"\.call\(\s*GE_API \+ \"(\w+)\"[\s\S]*?\{ quiet: true(, method: \"GET\")? \}", code
				):
					self.assertEqual(
						bool(match.group(2)),
						match.group(1) in ("get_flow", "find_created", "record_notes"),
						match.group(0)[:80],
					)

	def test_the_editor_is_kept_out_of_the_pure_modules(self):
		self.assertNotIn('from "./ui/', _code(FORM_KIT_JS / "index.js"), "the node suites import index.js")

	def test_a_move_keeps_nothing_on_the_phone_and_is_never_resent_by_itself(self):
		editor = _code(EDITOR_JS)
		self.assertIn("manual: !!this.hold_saves,", editor)
		# The queue's own flag decides (review of #1286), not a flag on the editor.
		journal = _body(editor, "\twrite_journal(doctype, name) {")
		self.assertIn(
			"if (this.queue && this.queue.manual && this.queue.record_key === key) return;", journal
		)
		self.assertIn("if (!queue || queue.manual || queue.record_key !== key) return;", journal)
		self.assertNotIn("hold_saves", journal)
		self.assertIn("if (!queue.manual) this.write_journal(doctype, name);", editor)
		self.assertIn('queue && !queue.manual && queue.state === "error"', editor)
		panel = _code(PANEL_JS)
		self.assertIn('this.hold_saves = spec.kind === "move";', panel)
		leave = _body(panel, "\tleave() {")
		self.assertIn("this.queue.discard(fieldname)", leave, "Cancel and Back drop a move's fields")

	def test_sign_in_works_in_both_shells(self):
		"""Review of #1286: the shared notice's "Sign in" called open_login, which only the guided
		page had; the panel threw."""
		editor = _code(EDITOR_JS)
		self.assertIn("\topen_login() {", editor)
		self.assertIn("on_action: () => this.open_login(),", editor)
		self.assertNotIn("\topen_login() {", _code(GUIDED_JS), "one copy, mixed onto the page")
		# The "carry" notice's action is offered only by a shell that has open_new_in_form.
		self.assertIn('action_label: typeof this.open_new_in_form === "function"', editor)

	def test_no_undo_after_a_close_step_win_and_the_guided_page_hears_of_an_undo(self):
		panel = _code(PANEL_JS)
		offer = _body(panel, "\toffer_undo({ queue, flow, opened, saved, doctype, name, title }) {")
		self.assertIn("if (!this.kit.undo_allowed(flow, opened || {}, saved || {})) {", offer)
		self.assertLess(offer.index("undo_allowed"), offer.index('action_label: __("Undo")'))
		undo = _body(panel, "\tundo({ queue, undo, doctype, name, title }) {")
		self.assertIn('$(document).trigger("sf-record-changed", [doctype, name]);', undo)
		api = _text(APP / "form_kit" / "api.py")
		self.assertIn('out["closed"] = {"field": open_rule["field"], "in": list(open_rule["not_in"])}', api)


# ---------------------------------------------------------------------------------- the wiring


class TestTheWiring(unittest.TestCase):
	def test_the_panel_lives_in_the_page_route_not_a_drawer_entry(self):
		drawer = _code(DRAWER_JS)
		self.assertIn("if (opts.history !== false) guard.open(overlay);", drawer)
		self.assertIn("history: false,", _code(PANEL_JS))
		self.assertIn("history: false,", _code(PAGE_JS), "the bulk panel too")

	def test_the_drawers_own_close_takes_the_panels_close_path(self):
		"""Review of #1286: the × called the drawer's close directly, past the panel's save-first."""
		drawer = _code(DRAWER_JS)
		self.assertIn(
			'if (current && typeof current.opts.request_close === "function") current.opts.request_close(current);',
			drawer,
		)
		self.assertIn("else api.close();", drawer, "the planners pass none and keep their ×")
		self.assertIn("request_close: () => this.request_close(),", _code(PANEL_JS))
		self.assertIn("request_close: () => this.close_side(),", _code(PAGE_JS))
		for planner in ("project_planner", "maintenance_planner"):
			path = APP / "project_enhancements" / "page" / planner / f"{planner}.js"
			if path.exists():
				self.assertNotIn("request_close", _text(path))

	def test_the_panels_files_load_on_demand(self):
		page = _code(PAGE_JS)
		panel = page[
			page.index("const OB_PANEL_ASSETS = [") : page.index(
				"];", page.index("const OB_PANEL_ASSETS = [")
			)
		]
		# The drawer's stylesheet with the drawer: since v1.673.0 the planner kit's CSS is its own
		# bundle, and a board that loaded only the script would draw an unstyled drawer.
		for asset in (
			"planner_kit.bundle.js",
			"planner_kit.bundle.css",
			"guided_entry.bundle.js",
			"guided_entry.bundle.css",
		):
			with self.subTest(asset=asset):
				self.assertIn(f'"{asset}"', panel)
		self.assertTrue((APP / "public" / "css" / "planner_kit.bundle.css").is_file())
		self.assertIn("frappe.require(OB_PANEL_ASSETS, resolve)", page)
		assets = page[page.index("const OB_ASSETS = [") : page.index("];", page.index("const OB_ASSETS = ["))]
		self.assertNotIn("planner_kit", assets, "the board's first paint does not wait for the panel")
		self.assertNotIn("guided_entry", assets)
		hooks = _text(APP / "hooks.py")
		self.assertNotIn("planner_kit.bundle", hooks)
		self.assertNotIn("guided_entry.bundle", hooks)

	def test_back_closes_a_panel_and_forward_reopens_it(self):
		page = _code(PAGE_JS)
		open_side = _body(page, "\topen_side(side, replace) {")
		self.assertIn("{ ob_side: this.kit.side_key(side) }", open_side)
		close_side = _body(page, "\tclose_side() {")
		self.assertIn("window.history.back();", close_side)
		self.assertIn("this.go(Object.assign({}, this.target, { side: null }), true);", close_side)
		replace_side = _body(page, "\treplace_side(side) {")
		self.assertIn('this.go(Object.assign({}, this.target, { panel: "", side }), true)', replace_side)
		self.assertNotIn("pushState", page)
		self.assertNotIn('addEventListener("popstate"', page)
		sync = _body(page, "\tsync_side() {")
		self.assertIn("panel.show(spec)", sync)
		self.assertIn('this.side.hide("route")', sync)

	def test_a_tap_edits_and_a_closing_drop_asks_for_its_fields(self):
		page = _code(PAGE_JS)
		drop = _body(page, "\tdrop(name, key) {")
		self.assertIn(
			'else if (action === "close") this.open_side({ kind: this.kit.CLOSING, name, stage: key });', drop
		)
		choose = _body(page, "\tchoose_stage(key) {")
		self.assertIn("this.open_side({ kind: this.kit.CLOSING, name, stage: key }, true);", choose)
		self.assertIn('this.open_side({ kind: this.kit.EDIT, name, step: "" });', page)
		committed = _body(page, "\tside_committed(info) {")
		self.assertIn("this.kit.remove_deal(this.model, info.name)", committed)
		self.assertIn("this.move_epoch += 1;", committed, "a load in flight cannot put the card back")

	def test_quick_add_creates_through_the_form_kit(self):
		panel = _code(PANEL_JS)
		start = _body(panel, "\tstart_new(spec) {")
		self.assertIn("this.kit.quick_start(", start)
		self.assertIn("this.restore_new();", start)
		self.assertIn('if (action === "create") this.create();', _code(PANEL_JS).replace("else if", "if"))
		editor = _code(EDITOR_JS)
		self.assertIn('GE_API + "create_record", args, { quiet: true }', editor)
		self.assertIn("const args = { doctype, values, client_id: this.client_id };", editor)
		self.assertIn("this.kit.create_extras(step, values,", editor)

	def test_bulk_goes_through_its_endpoint_quietly(self):
		page = _code(PAGE_JS)
		self.assertIn(
			'.call(OB_API + "bulk_update", { names: request.names, action: request.action, values: request.values, modified: request.modified }, { quiet: true })',
			page,
		)
		self.assertIn(
			"this.kit.bulk_request(this.model, this.picks || [], bulk.action, payload_values)", page
		)
		self.assertIn("this.set_picks(this.kit.after_bulk(this.picks || [], outcome));", page)
		# Over the action's cap nothing is sent, and the panel says so.
		self.assertIn("if (!request.names.length || request.over > 0) return this.render_bulk();", page)
		self.assertIn("const cap = this.kit.bulk_cap(bulk.action);", page)

	def test_the_bulk_panel_never_offers_or_sends_closed_won(self):
		page = _code(PAGE_JS)
		bulk = page[page.index("\trender_bulk() {") : page.index("\tbulk_outcome_html(outcome) {")]
		self.assertNotIn("payload_values.ranks", bulk)
		self.assertNotIn("flow.won", bulk)
		self.assertIn('if (stage.gate === "won") return;', bulk)
		self.assertIn("Win deals one at a time: each needs its own ranks and hand-off.", bulk)

	def test_the_selection_storage_is_guarded(self):
		page = _code(PAGE_JS)
		self.assertEqual(page.count("window.sessionStorage"), 3)
		for head in ("function ob_read_picks(key) {", "function ob_write_picks(key, list) {"):
			body = page[page.index(head) : page.index("\n}\n", page.index(head))]
			with self.subTest(function=head):
				self.assertLess(body.index("try {"), body.index("window.sessionStorage"))

	def test_what_the_board_draws_is_escaped(self):
		page = _code(PAGE_JS)
		for snippet in (
			'data-pick="${esc(deal.name)}"',
			'data-add="${esc(col.stage.key)}"',
			"<li>${esc(t)}</li>",
			"${esc(title(f.name))}",
		):
			with self.subTest(snippet=snippet):
				self.assertIn(snippet, page)

	def test_esc_closes_the_panel_only_when_nothing_on_top_owns_the_key(self):
		page = _code(PAGE_JS)
		self.assertIn(
			"if (e.defaultPrevented || (window.cur_dialog && window.cur_dialog.display)) return;", page
		)
		self.assertIn('if (t && t.getAttribute && t.getAttribute("aria-expanded") === "true") return;', page)

	def test_the_new_styles_use_tokens_only(self):
		for name in ("guided_entry.bundle.css", "opportunity_board.bundle.css"):
			css = re.sub(r"/\*[\s\S]*?\*/", "", _text(CSS_DIR / name))
			part = (
				css[css.index(".fk-panel {") :]
				if name.startswith("guided")
				else css[css.index(".ob-head-tools {") :]
			)
			with self.subTest(file=name):
				self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b", part))
				for forbidden in ("rgb(", "rgba(", "shadow"):
					self.assertNotIn(forbidden, part.lower())
				self.assertIn("var(--sf-touch-min)", part)
				self.assertIn("@media (max-width: 767px)", part)

	def test_the_planner_kit_styles_are_untouched(self):
		for name in ("styles.js", "conflicts.js", "blocks.js"):
			self.assertNotIn("fk-", _text(JS / "planner_kit" / name), name)


if __name__ == "__main__":
	unittest.main()
