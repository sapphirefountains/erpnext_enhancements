"""Bench-free tests for the Opportunity board (form kit P6, TASK-2026-02774).

Three halves, each in its own class:

* **Under node.** The board's decisions are one pure ES module,
  ``public/js/opportunity_board/board.js`` (it imports ``public/js/form_kit/summary.js`` for the
  pillars). Both folders are copied to a temp dir beside a ``package.json`` that says
  ``"type": "module"``, the way ``test_guided_entry`` runs ``public/js/form_kit/``, and a runner
  feeds them one realistic payload. Skips cleanly when ``node`` is not on PATH.
* **The SQL check.** The forecast strip is "done when the figures match a SQL check"
  (P6.5). The check is written once, in ``crm_enhancements/README.md``; this suite reads it from
  there, runs it on an in-memory SQLite copy of the same deals (plus a closed and a historical
  one it must leave out), and holds node's figures to it.
* **The endpoint and the page.** ``get_board`` against a frappe stub (the caller's permissions
  through ``frappe.get_list``, guarded custom fields, ``str(modified)`` for the form kit's lock,
  stream rows and completeness in batches), and static checks on the page script, its Page
  record, the stylesheet and the CRM sidebar entry.

Run: python -m unittest erpnext_enhancements.tests.test_opportunity_board -v
"""

import ast
import datetime
import json
import re
import shutil
import sqlite3
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
BOARD_JS = JS / "opportunity_board"
FORM_KIT_JS = JS / "form_kit"
BUNDLE = JS / "opportunity_board.bundle.js"
CSS = APP / "public" / "css" / "opportunity_board.bundle.css"
PAGE_DIR = APP / "crm_enhancements" / "page" / "opportunity_board"
PAGE_JSON = PAGE_DIR / "opportunity_board.json"
PAGE_JS = PAGE_DIR / "opportunity_board.js"
PAGE_PY = PAGE_DIR / "opportunity_board.py"
PIPELINE_PY = APP / "crm_enhancements" / "page" / "sales_pipeline" / "sales_pipeline.py"
PLANNER_JS = APP / "project_enhancements" / "page" / "project_planner" / "project_planner.js"
CRM_README = APP / "crm_enhancements" / "README.md"
MODULE = "erpnext_enhancements.crm_enhancements.page.opportunity_board.opportunity_board"

TODAY = "2026-10-13"
THRESHOLDS = {"amber": 7, "red": 14}

STAGES = [
	{"label": "Qualification", "kind": "open"},
	{"label": "Needs Analysis", "kind": "open"},
	{"label": "Proposal/Price Quote", "kind": "open"},
	{"label": "Negotiation/Review", "kind": "open"},
	{"label": "On Hold", "kind": "parked"},
	{"label": "Lost", "kind": "close", "gate": "lost"},
	{"label": "Closed Won", "kind": "close", "gate": "won"},
]


def _deal(name, status, amount=None, probability=None, closing=None, since="2026-10-10", streams=(), **extra):
	row = {
		"name": name,
		"title": extra.pop("title", f"Deal {name}"),
		"account": extra.pop("account", "Smith Ponds"),
		"status": status,
		"amount": amount,
		"probability": probability,
		"expected_closing": closing,
		"since": since,
		"modified": f"2026-10-{10 + len(name) % 3} 09:00:00.123456",
		"streams": list(streams),
		"completeness": extra.pop("completeness", None),
	}
	row.update(extra)
	return row


# The open deals the endpoint would send: multi-stream, no amount, no probability, On Hold with
# a value, an open status the options no longer list, ages either side of both thresholds, close
# dates this month, next month, later and none.
DEALS = [
	_deal("OPP-1", "Qualification", 5000, 10, "2026-10-30", "2026-10-13", ["Design", "Build"]),
	_deal("OPP-2", "Qualification", None, 10, None, "2026-10-01", ["Service"]),
	_deal("OPP-3", "Needs Analysis", 12000.5, 25, "2026-11-02", "2026-09-29", ["Build", "Events", "Design"]),
	_deal("OPP-4", "Needs Analysis", 0, 25, "2026-10-01", "2026-10-06", []),
	_deal("OPP-5", "Proposal/Price Quote", 40000, 50, "2026-11-30", "2026-10-06", ["Products"]),
	_deal("OPP-6", "Proposal/Price Quote", 7500, None, "2026-12-01", "2026-09-01", ["Delivery", "Build"]),
	_deal("OPP-7", "Negotiation/Review", 90000, 80, "2026-10-15", "2026-10-12", ["Design"]),
	_deal("OPP-8", "On Hold", 25000, 40, "2026-10-20", "2026-05-01", ["Events"]),
	_deal("OPP-9", "On Hold", None, None, None, "2026-08-01", ["Service", "Design"]),
	_deal("OPP-10", "Replied", 3000, 60, "2026-11-11", "2026-10-11", ["Fountain Art"]),
	_deal("OPP-11", "Qualification", 1200, 150, "2026-10-02", None, ["Service"]),
]
PAYLOAD = {
	"stages": STAGES,
	"deals": DEALS,
	"thresholds": THRESHOLDS,
	"today": TODAY,
	"currency": "USD",
	"truncated": False,
}

# The rows the SQL check sees that the board must not: a closed deal and a historical one.
EXCLUDED = [
	{
		"name": "OPP-90",
		"status": "Closed Won",
		"amount": 99999,
		"probability": 100,
		"closing": "2026-10-05",
		"historical": 0,
	},
	{
		"name": "OPP-91",
		"status": "Lost",
		"amount": 5000,
		"probability": 0,
		"closing": "2026-10-05",
		"historical": 0,
	},
	{
		"name": "OPP-92",
		"status": "Qualification",
		"amount": 77777,
		"probability": 10,
		"closing": "2026-10-05",
		"historical": 1,
	},
]

DROPS = [
	["OPP-1", "needs-analysis"],
	["OPP-1", "qualification"],
	["OPP-1", "on-hold"],
	["OPP-8", "qualification"],
	["OPP-1", "closed-won"],
	["OPP-1", "lost"],
	["OPP-1", "replied"],
	["OPP-10", "qualification"],
	["OPP-1", "nowhere"],
	["NOPE", "qualification"],
]

RUNNER = r"""
import { pathToFileURL } from "node:url";
const [dir, inputJson] = process.argv.slice(2);
const kit = await import(pathToFileURL(dir + "/opportunity_board/board.js").href);
const input = JSON.parse(inputJson);
const out = {};

const model = kit.prepare(input.payload);
out.stages = model.stages;
out.deals = model.deals.map((d) => ({
	name: d.name, stage: d.stage, kind: d.kind, days: d.days, stale: d.stale, amount: d.amount,
	probability: d.probability, streams: d.streams, title: d.title, since: d.since,
}));
out.columns = kit.columns(model).map((c) => ({ key: c.stage.key, names: c.deals.map((d) => d.name), count: c.count, total: c.total }));
out.board_totals = kit.totals(model.deals);
out.rows = kit.stream_rows(model).map((r) => ({
	stream: r.stream, pillar: r.pillar, count: r.count, total: r.total,
	items: r.items.map((i) => ({ name: i.deal.name, also: i.also })),
	cells: kit.row_cells(model, r).map((c) => ({ key: c.stage.key, names: c.items.map((i) => i.deal.name), count: c.count, total: c.total })),
}));
const f = kit.forecast(model);
out.forecast = {
	stages: f.stages.map((s) => ({ key: s.stage.key, label: s.stage.label, count: s.count, total: s.total, weighted: s.weighted })),
	overall: f.overall,
	months: f.months,
};
out.drops = input.drops.map(([name, key]) => kit.drop_action(model, model.by_name[name], key));
out.choices = kit.move_choices(model, model.by_name["OPP-1"]).map((c) => [c.stage.key, c.action, c.current]);

const moved = kit.move_deal(model, "OPP-2", "Needs Analysis");
out.move = {
	stage: moved.by_name["OPP-2"].stage, status: moved.by_name["OPP-2"].status, days: moved.by_name["OPP-2"].days,
	stale: moved.by_name["OPP-2"].stale, since: moved.by_name["OPP-2"].since,
	old_stage: model.by_name["OPP-2"].stage, old_days: model.by_name["OPP-2"].days,
	count: moved.deals.length, column: kit.columns(moved).find((c) => c.stage.key === "needs-analysis").deals.map((d) => d.name),
	to_close_is_same: kit.move_deal(model, "OPP-2", "Closed Won") === model,
	unknown_is_same: kit.move_deal(model, "NOPE", "Needs Analysis") === model,
};
const back = kit.replace_deal(moved, model.by_name["OPP-2"]);
out.put_back = { stage: back.by_name["OPP-2"].stage, in_list: back.deals.find((d) => d.name === "OPP-2").stage };

out.routes = {
	bare: kit.parse_route(["opportunity-board"]),
	other: kit.parse_route(["sales-pipeline"]),
	not_array: kit.parse_route("opportunity-board"),
	streams: kit.parse_route(["opportunity-board", "streams"]),
	bad_layout: kit.parse_route(["opportunity-board", "kanban"]),
	tab: kit.parse_route(["opportunity-board", "stages", "on-hold"]),
	panel: kit.parse_route(["opportunity-board", "stages", "move", "CRM-OPP-2026-00123"]),
	tab_panel: kit.parse_route(["opportunity-board", "streams", "qualification", "move", "CRM-OPP-2026-00123"]),
	build_bare: kit.build_route({}),
	build_tab: kit.build_route({ layout: "streams", stage: "on-hold" }),
	build_panel: kit.build_route({ layout: "stages", stage: "", panel: "OPP-1" }),
	build_tab_panel: kit.build_route({ layout: "stages", stage: "lost", panel: "OPP-1" }),
	round_trip: kit.parse_route(kit.build_route({ layout: "streams", stage: "qualification", panel: "OPP-1" })),
	keys_differ: kit.route_key({ layout: "stages", stage: "a", panel: "" }) !== kit.route_key({ layout: "stages", stage: "a", panel: "x" }),
	keys_same: kit.route_key(kit.parse_route(["opportunity-board"])) === kit.route_key({ layout: "stages", stage: "", panel: "" }),
};
out.keys = input.labels.map((l) => kit.stage_key(l));
out.days = input.days.map(([t, s]) => kit.days_between(t, s));
out.stale = input.stale.map(([d, a, r, k]) => kit.stale_level(d, { amber: a, red: r }, k));
out.months = [kit.month_key("2026-10-13"), kit.month_key("2026-10-13 10:00:00"), kit.month_key(""), kit.add_months("2026-12", 1), kit.add_months("2026-01", -1), kit.add_months("2026-10", 0)];
out.meter = [kit.meter({ done: 7, total: 9, missing: [{ label: "Lead Source" }, { fieldname: "x" }] }), kit.meter(null), kit.meter({ done: 0, total: 0 }), kit.meter({ done: 12, total: 9 })];
out.band = [kit.band({ streams: ["Design", "Build"] }), kit.band({ streams: [] }), kit.band({ streams: ["Delivery", "Products", "Events", "Service", "Odd"] })];
out.current_stage = [kit.current_stage(model, "negotiation-review"), kit.current_stage(model, "nope"), kit.current_stage(model, "lost"), kit.current_stage(model, "")];
out.phone = [kit.is_phone(390), kit.is_phone(767), kit.is_phone(768), kit.is_phone(0)];
out.consts = { HOLD_MS: kit.HOLD_MS, DRAG_PX: kit.DRAG_PX, PHONE_MAX: kit.PHONE_MAX, STREAM_ORDER: kit.STREAM_ORDER, PAGE: kit.PAGE, MOVE: kit.MOVE };
out.amounts = [kit.amount_of({ amount: 5 }), kit.amount_of({ amount: 0 }), kit.amount_of({ amount: -3 }), kit.amount_of({ amount: null }), kit.amount_of({ amount: "12.5" })];
out.probabilities = [kit.probability_of({ probability: 150 }), kit.probability_of({ probability: -5 }), kit.probability_of({ probability: null }), kit.probability_of({ probability: "25" })];
out.loads = [[0, 0, 0], [0, 1, 0], [3, 3, 1], [0, 1, 1], [2, 2, 0], [5, 5, 1]].map(([s, e, b]) => kit.load_is_current(s, e, b));
out.empty = { model: kit.prepare(null).deals.length, columns: kit.columns(kit.prepare({ stages: input.payload.stages })).length, forecast: kit.forecast(kit.prepare({ stages: input.payload.stages })).overall };
console.log(JSON.stringify(out));
"""

LABELS = ["Proposal/Price Quote", "On Hold", "  Needs  Analysis ", "", "Négociation"]
DAYS = [
	[TODAY, "2026-10-13"],
	[TODAY, "2026-10-01"],
	[TODAY, "2026-10-13 23:59:59"],
	[TODAY, "2026-11-01"],
	[TODAY, ""],
	[None, "2026-10-01"],
	["2026-03-09", "2026-03-07"],
]
STALE = [
	[6, 7, 14, "open"],
	[7, 7, 14, "open"],
	[13, 7, 14, "open"],
	[14, 7, 14, "open"],
	[400, 7, 14, "parked"],
	[None, 7, 14, "open"],
	[30, 0, 14, "open"],
	[30, 7, 0, "open"],
	[30, 0, 0, "open"],
	[45, 45, 90, "other"],
]


def _text(path):
	return path.read_text(encoding="utf-8")


def _code(path):
	"""The script with comments stripped: an absence check must not be satisfied (or broken) by
	the comment that explains it."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", _text(path))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def _amount(deal):
	value = deal.get("amount")
	return float(value) if value not in (None, "") and float(value) > 0 else 0.0


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestTheBoardUnderNode(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		node = shutil.which("node")
		with tempfile.TemporaryDirectory() as tmp:
			folder = Path(tmp)
			(folder / "package.json").write_text('{"type": "module"}', encoding="utf-8")
			shutil.copytree(BOARD_JS, folder / "opportunity_board")
			shutil.copytree(FORM_KIT_JS, folder / "form_kit")
			(folder / "runner.mjs").write_text(RUNNER, encoding="utf-8")
			payload = json.dumps(
				{"payload": PAYLOAD, "drops": DROPS, "labels": LABELS, "days": DAYS, "stale": STALE}
			)
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
		cls.deals = {d["name"]: d for d in cls.out["deals"]}

	# ---- stages and the model

	def test_columns_come_from_the_status_options_in_order(self):
		keys = [(s["key"], s["kind"]) for s in self.out["stages"]]
		self.assertEqual(
			keys,
			[
				("qualification", "open"),
				("needs-analysis", "open"),
				("proposal-price-quote", "open"),
				("negotiation-review", "open"),
				# An open status the options no longer list gets its own column, before On Hold.
				("replied", "other"),
				("on-hold", "parked"),
				("lost", "close"),
				("closed-won", "close"),
			],
		)
		self.assertEqual(
			[c["key"] for c in self.out["columns"]][-1], "on-hold", "closing stages are not columns"
		)

	def test_every_open_deal_is_on_the_board_once(self):
		names = [n for c in self.out["columns"] for n in c["names"]]
		self.assertEqual(sorted(names), sorted(d["name"] for d in DEALS))
		self.assertEqual(len(names), len(set(names)))

	def test_a_column_is_stalest_first(self):
		qual = next(c for c in self.out["columns"] if c["key"] == "qualification")
		self.assertEqual(qual["names"], ["OPP-2", "OPP-1", "OPP-11"])
		# OPP-11 has no `since`, so no age: it sorts last.
		self.assertIsNone(self.deals["OPP-11"]["days"])

	def test_amounts_and_no_amount(self):
		self.assertEqual(self.out["amounts"], [5, None, None, None, 12.5])
		self.assertIsNone(self.deals["OPP-2"]["amount"])
		self.assertIsNone(self.deals["OPP-4"]["amount"], "0 is 'No amount', not $0")
		self.assertEqual(self.deals["OPP-3"]["amount"], 12000.5)

	def test_probability_is_clamped_and_blank_is_zero(self):
		self.assertEqual(self.out["probabilities"], [100, 0, 0, 25])
		self.assertEqual(self.deals["OPP-11"]["probability"], 100)
		self.assertEqual(self.deals["OPP-6"]["probability"], 0)

	def test_streams_are_kept_in_order_once(self):
		self.assertEqual(self.deals["OPP-3"]["streams"], ["Build", "Events", "Design"])

	# ---- ages

	def test_days_in_stage_are_calendar_days_from_the_server_dates(self):
		self.assertEqual(self.out["days"], [0, 12, 0, 0, None, None, 2])
		self.assertEqual(self.deals["OPP-2"]["days"], 12)
		self.assertEqual(self.deals["OPP-6"]["days"], 42)

	def test_stale_levels_match_the_sales_pipeline_rule(self):
		self.assertEqual(self.out["stale"], [0, 1, 1, 2, 0, 0, 2, 1, 0, 1])
		# The Python rule the TV board uses, executed on its own, agrees for every open case.
		tree = ast.parse(_text(PIPELINE_PY))
		node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_stale_level")
		ns = {}
		exec(compile(ast.Module(body=[node], type_ignores=[]), str(PIPELINE_PY), "exec"), ns)
		for (days, amber, red, kind), level in zip(STALE, self.out["stale"], strict=True):
			if kind == "parked" or days is None:
				continue
			with self.subTest(days=days, amber=amber, red=red):
				self.assertEqual(ns["_stale_level"](days, amber, red), level)

	def test_on_hold_never_glows(self):
		self.assertEqual(self.deals["OPP-8"]["stale"], 0)
		self.assertGreater(self.deals["OPP-8"]["days"], 14)
		self.assertEqual(self.deals["OPP-2"]["stale"], 1)
		self.assertEqual(self.deals["OPP-6"]["stale"], 2)

	# ---- the card

	def test_the_band_splits_by_stream_and_uses_the_one_pillar_source(self):
		self.assertEqual(self.out["band"][0], ["design", "build"])
		self.assertEqual(self.out["band"][1], ["neutral"])
		# Delivery and Products are Build; Events is Rent; an unknown stream is neutral.
		self.assertEqual(self.out["band"][2], ["build", "build", "rent", "service", "neutral"])
		self.assertIn('import { pillar_for } from "../form_kit/summary.js";', _text(BOARD_JS / "board.js"))

	def test_the_meter(self):
		full, none, zero, over = self.out["meter"]
		self.assertEqual(full, {"done": 7, "total": 9, "percent": 78, "missing": ["Lead Source"]})
		self.assertIsNone(none)
		self.assertIsNone(zero)
		self.assertEqual((over["done"], over["percent"]), (9, 100))

	# ---- rows by value stream (P6.2)

	def test_rows_are_the_six_streams_then_others_then_none(self):
		streams = [r["stream"] for r in self.out["rows"]]
		self.assertEqual(
			streams, ["Design", "Build", "Service", "Events", "Products", "Delivery", "Fountain Art", ""]
		)
		pillars = {r["stream"]: r["pillar"] for r in self.out["rows"]}
		self.assertEqual(pillars["Delivery"], "build")
		self.assertEqual(pillars["Products"], "build")
		self.assertEqual(pillars["Events"], "rent")
		self.assertEqual(pillars[""], "neutral")

	def test_a_deal_with_several_streams_is_in_each_row_marked_also_in(self):
		rows = {r["stream"]: r for r in self.out["rows"]}
		design = {i["name"]: i["also"] for i in rows["Design"]["items"]}
		self.assertEqual(design["OPP-3"], ["Build", "Events"])
		self.assertEqual(design["OPP-7"], [])
		build = {i["name"]: i["also"] for i in rows["Build"]["items"]}
		self.assertEqual(build["OPP-3"], ["Events", "Design"])
		self.assertEqual(build["OPP-6"], ["Delivery"])
		self.assertIn("OPP-4", [i["name"] for i in rows[""]["items"]])

	def test_each_row_counts_a_deal_in_full_and_the_board_counts_it_once(self):
		rows = {r["stream"]: r for r in self.out["rows"]}
		self.assertEqual(rows["Design"]["total"], 5000 + 12000.5 + 90000)
		self.assertEqual(rows["Build"]["total"], 5000 + 12000.5 + 7500)
		row_sum = sum(r["total"] for r in self.out["rows"])
		board = self.out["board_totals"]
		self.assertGreater(row_sum, board["total"], "the rows double count multi-stream deals, by design")
		self.assertEqual(board["count"], len(DEALS))
		self.assertAlmostEqual(board["total"], sum(_amount(d) for d in DEALS))

	def test_the_overall_totals_match_the_plain_board(self):
		"""P6.2 is done when the overall totals match the plain board's."""
		cols = self.out["columns"]
		self.assertEqual(sum(c["count"] for c in cols), self.out["board_totals"]["count"])
		self.assertAlmostEqual(sum(c["total"] for c in cols), self.out["board_totals"]["total"])
		self.assertEqual(self.out["forecast"]["overall"]["count"], self.out["board_totals"]["count"])
		self.assertAlmostEqual(self.out["forecast"]["overall"]["total"], self.out["board_totals"]["total"])

	def test_row_cells_line_up_with_the_columns(self):
		keys = [c["key"] for c in self.out["columns"]]
		for row in self.out["rows"]:
			with self.subTest(row=row["stream"]):
				self.assertEqual([c["key"] for c in row["cells"]], keys)
				self.assertEqual(sum(c["count"] for c in row["cells"]), row["count"])
				self.assertAlmostEqual(sum(c["total"] for c in row["cells"]), row["total"])

	# ---- forecast (P6.5)

	def test_the_forecast_matches_an_independent_count(self):
		by_stage = {s["label"]: s for s in self.out["forecast"]["stages"]}
		for label in {d["status"] for d in DEALS}:
			deals = [d for d in DEALS if d["status"] == label]
			with self.subTest(stage=label):
				self.assertEqual(by_stage[label]["count"], len(deals))
				self.assertAlmostEqual(by_stage[label]["total"], sum(_amount(d) for d in deals))
				if label == "On Hold":
					self.assertIsNone(by_stage[label]["weighted"])
				else:
					expect = sum(
						_amount(d) * min(max(float(d["probability"] or 0), 0), 100) / 100 for d in deals
					)
					self.assertAlmostEqual(by_stage[label]["weighted"], expect)

	def test_on_hold_is_out_of_every_weighted_figure(self):
		overall = self.out["forecast"]["overall"]
		on_hold_value = 25000 * 0.40
		weighted_all = sum(
			_amount(d) * min(max(float(d["probability"] or 0), 0), 100) / 100
			for d in DEALS
			if d["status"] != "On Hold"
		)
		self.assertAlmostEqual(overall["weighted"], weighted_all)
		self.assertNotAlmostEqual(overall["weighted"], weighted_all + on_hold_value)
		october = self.out["forecast"]["months"][0]
		# OPP-8 (On Hold, closing 2026-10-20) counts in October's deals and value, not its weighted.
		self.assertEqual(october["key"], "2026-10")
		self.assertEqual(october["count"], 5)
		self.assertAlmostEqual(october["total"], 5000 + 90000 + 25000 + 1200)
		self.assertAlmostEqual(october["weighted"], 5000 * 0.10 + 90000 * 0.80 + 1200 * 1.0)

	def test_this_month_and_next_by_expected_close_date(self):
		months = self.out["forecast"]["months"]
		self.assertEqual([m["key"] for m in months], ["2026-10", "2026-11"])
		november = months[1]
		self.assertEqual(november["count"], 3)
		self.assertAlmostEqual(november["total"], 12000.5 + 40000 + 3000)
		self.assertAlmostEqual(november["weighted"], 12000.5 * 0.25 + 40000 * 0.5 + 3000 * 0.6)
		self.assertEqual(self.out["months"], ["2026-10", "2026-10", "", "2027-01", "2025-12", "2026-10"])

	def test_a_load_from_before_a_move_is_never_drawn(self):
		"""Review of #1266: a get_board in flight when a card is dropped answers with the old stage
		and old `modified`; drawn, the card jumps back and the save's `modified` lands on the stale
		copy, so Undo does nothing."""
		self.assertEqual(
			self.out["loads"],
			[
				True,  # nothing moved, nothing saving
				False,  # a move began after the load did
				False,  # same epoch, but a save is still in flight
				False,  # a move began and is still saving
				True,  # the fresh load started after the save settled
				False,  # a drag counts as busy
			],
		)

	def test_an_empty_board(self):
		self.assertEqual(self.out["empty"]["model"], 0)
		self.assertEqual(self.out["empty"]["columns"], 5)
		self.assertEqual(self.out["empty"]["forecast"], {"count": 0, "total": 0, "weighted": 0})

	# ---- moving a card

	def test_which_drops_save_and_which_open_the_close_step(self):
		self.assertEqual(
			self.out["drops"],
			["save", "none", "save", "save", "close", "close", "refuse", "save", "none", "none"],
		)

	def test_the_move_panel_offers_every_stage_but_the_unlisted_one(self):
		self.assertEqual(
			self.out["choices"],
			[
				["qualification", "none", True],
				["needs-analysis", "save", False],
				["proposal-price-quote", "save", False],
				["negotiation-review", "save", False],
				["on-hold", "save", False],
				["lost", "close", False],
				["closed-won", "close", False],
			],
		)

	def test_a_moved_card_starts_its_clock_again_and_the_old_model_is_untouched(self):
		move = self.out["move"]
		self.assertEqual(
			(move["stage"], move["status"], move["days"], move["stale"]),
			("needs-analysis", "Needs Analysis", 0, 0),
		)
		self.assertEqual(move["since"], TODAY)
		self.assertEqual((move["old_stage"], move["old_days"]), ("qualification", 12))
		self.assertEqual(move["count"], len(DEALS))
		self.assertIn("OPP-2", move["column"])
		self.assertTrue(move["to_close_is_same"], "a closing stage is never a client-side move")
		self.assertTrue(move["unknown_is_same"])
		self.assertEqual(self.out["put_back"], {"stage": "qualification", "in_list": "qualification"})

	# ---- routes and phones

	def test_routes(self):
		r = self.out["routes"]
		# Part B (v1.663.0) added `view` (and `bare`, the address with no segment at all, where the
		# remembered view may apply); part C (v1.675.0) `side`, the side panel, none on these. The
		# board's own routes read as they did in part A.
		board = {"view": "board", "bare": False, "side": None}
		self.assertEqual(
			r["bare"],
			{"view": "board", "layout": "stages", "stage": "", "panel": "", "side": None, "bare": True},
		)
		self.assertIsNone(r["other"])
		self.assertIsNone(r["not_array"])
		self.assertEqual(r["streams"]["layout"], "streams")
		self.assertEqual(r["bad_layout"]["layout"], "stages")
		self.assertEqual(r["tab"], {**board, "layout": "stages", "stage": "on-hold", "panel": ""})
		self.assertEqual(
			r["panel"], {**board, "layout": "stages", "stage": "", "panel": "CRM-OPP-2026-00123"}
		)
		self.assertEqual(
			r["tab_panel"],
			{**board, "layout": "streams", "stage": "qualification", "panel": "CRM-OPP-2026-00123"},
		)
		self.assertEqual(r["build_bare"], ["opportunity-board", "stages"])
		self.assertEqual(r["build_tab"], ["opportunity-board", "streams", "on-hold"])
		self.assertEqual(r["build_panel"], ["opportunity-board", "stages", "move", "OPP-1"])
		self.assertEqual(r["build_tab_panel"], ["opportunity-board", "stages", "lost", "move", "OPP-1"])
		self.assertEqual(
			r["round_trip"], {**board, "layout": "streams", "stage": "qualification", "panel": "OPP-1"}
		)
		self.assertTrue(r["keys_differ"])
		self.assertTrue(r["keys_same"])

	def test_stage_keys_hold_no_slash(self):
		self.assertEqual(
			self.out["keys"], ["proposal-price-quote", "on-hold", "needs-analysis", "none", "n-gociation"]
		)
		self.assertNotIn(
			"move", [s["key"] for s in self.out["stages"]], "a stage key must not read as the panel segment"
		)

	def test_the_phone_layout_is_below_768(self):
		self.assertEqual(self.out["phone"], [True, True, False, False])
		self.assertEqual(
			self.out["current_stage"],
			["negotiation-review", "qualification", "qualification", "qualification"],
		)

	def test_the_gesture_numbers_are_the_planners(self):
		consts = self.out["consts"]
		planner = _text(PLANNER_JS)
		self.assertEqual(consts["HOLD_MS"], int(re.search(r"\bhold_ms:\s*(\d+)", planner).group(1)))
		self.assertEqual(consts["DRAG_PX"], int(re.search(r"\bdrag_px:\s*(\d+)", planner).group(1)))
		self.assertEqual(consts["PHONE_MAX"], 767)
		self.assertEqual(
			consts["STREAM_ORDER"], ["Design", "Build", "Service", "Events", "Products", "Delivery"]
		)
		self.assertEqual(consts["PAGE"], json.loads(_text(PAGE_JSON))["name"])


def _readme_sql(heading):
	"""The SQL under ``heading`` in the CRM README's board section, as written there."""
	text = _text(CRM_README)
	start = text.index(heading)
	block = re.search(r"```sql\n([\s\S]*?)```", text[start:])
	assert block, f"no sql block after {heading!r}"
	return block.group(1)


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestTheForecastMatchesTheSqlCheck(unittest.TestCase):
	"""P6.5: "Done when the figures match a SQL check." The check in the README, run on SQLite."""

	@classmethod
	def setUpClass(cls):
		TestTheBoardUnderNode.setUpClass()
		cls.out = TestTheBoardUnderNode.out
		db = sqlite3.connect(":memory:")
		db.execute(
			"create table `tabOpportunity` (name text, status text, opportunity_amount real, probability real,"
			" expected_closing text, custom_is_historical integer)"
		)
		rows = [
			(d["name"], d["status"], d["amount"], d["probability"], d["expected_closing"], 0) for d in DEALS
		] + [
			(e["name"], e["status"], e["amount"], e["probability"], e["closing"], e["historical"])
			for e in EXCLUDED
		]
		db.executemany("insert into `tabOpportunity` values (?, ?, ?, ?, ?, ?)", rows)
		cls.db = db

	def _run(self, heading, **params):
		sql = _readme_sql(heading)
		for key, value in params.items():
			sql = sql.replace(key, value)
		return self.db.execute(sql).fetchall()

	def test_per_stage(self):
		by_stage = {s["label"]: s for s in self.out["forecast"]["stages"]}
		result = self._run("Forecast check, per stage")
		self.assertEqual({row[0] for row in result}, {s for s in by_stage if by_stage[s]["count"]})
		for status, deals, total, weighted in result:
			with self.subTest(stage=status):
				board = by_stage[status]
				self.assertEqual(board["count"], deals)
				self.assertAlmostEqual(board["total"], total)
				if status == "On Hold":
					self.assertIsNone(board["weighted"])
					self.assertEqual(weighted, 0)
				elif status == "Qualification":
					# OPP-11 carries 150%: SQL takes it as written; the board caps it at 100.
					self.assertAlmostEqual(board["weighted"], weighted - 1200 * 0.5)
				else:
					self.assertAlmostEqual(board["weighted"], weighted)

	def test_by_month(self):
		months = {m["key"]: m for m in self.out["forecast"]["months"]}
		result = self._run("Forecast check, by month", **{"2026-10": "2026-10", "2026-11": "2026-11"})
		self.assertEqual({row[0] for row in result}, set(months))
		for month, deals, total, weighted in result:
			with self.subTest(month=month):
				self.assertEqual(months[month]["count"], deals)
				self.assertAlmostEqual(months[month]["total"], total)
				cap = 1200 * 0.5 if month == "2026-10" else 0
				self.assertAlmostEqual(months[month]["weighted"], weighted - cap)


# ---------------------------------------------------------------------------------- the endpoint

STATE = {}


class _Field:
	def __init__(self, fieldname, options=None):
		self.fieldname = fieldname
		self.options = options


class _Meta:
	def __init__(self, fields):
		self.fields = {f.fieldname: f for f in fields}

	def get_field(self, fieldname):
		return self.fields.get(fieldname)

	def has_field(self, fieldname):
		return fieldname in self.fields


def _opportunity_meta(missing=()):
	names = [
		(
			"status",
			"Qualification\nNeeds Analysis\nProposal/Price Quote\nNegotiation/Review\nLost\nClosed Won\nOn Hold",
		),
		("custom_opportunity_name", None),
		("customer_name", None),
		("party_name", None),
		("opportunity_amount", None),
		("probability", None),
		("expected_closing", None),
		("custom_stage_changed_on", None),
		("custom_is_historical", None),
		("custom_value_stream", "Value Stream"),
		("opportunity_owner", "User"),
		("custom_snoozed_until", None),
		("custom_snooze_reason", None),
	]
	return _Meta([_Field(n, o) for n, o in names if n not in missing])


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe.__path__ = []

	def whitelist(methods=None, allow_guest=False, **kwargs):
		def wrap(fn):
			fn.whitelisted = {"methods": methods, "allow_guest": allow_guest}
			return fn

		return wrap

	frappe.whitelist = whitelist
	frappe._ = lambda text, *a, **k: text
	frappe._dict = dict

	class PermissionError(Exception):
		pass

	class ValidationError(Exception):
		pass

	frappe.PermissionError = PermissionError
	frappe.ValidationError = ValidationError

	def throw(message, exc=ValidationError, title=None):
		raise exc(message)

	frappe.throw = throw

	def has_permission(doctype, ptype="read", doc=None, throw=False, **kwargs):
		STATE["perm"].append((doctype, ptype, throw))
		if not STATE["allowed"] and throw:
			raise PermissionError(f"No {ptype} permission on {doctype}")
		return STATE["allowed"]

	frappe.has_permission = has_permission

	def get_meta(doctype):
		if doctype == "Opportunity":
			return STATE["meta"]
		if doctype == "Value Stream":
			return _Meta([_Field("value_stream", "Value Streams")])
		raise AssertionError(f"unexpected meta {doctype}")

	frappe.get_meta = get_meta

	def get_list(doctype, filters=None, fields=None, order_by=None, limit_page_length=None, **kwargs):
		STATE["get_list"].append(
			{
				"doctype": doctype,
				"filters": filters,
				"fields": list(fields or []),
				"order_by": order_by,
				"limit": limit_page_length,
				"kwargs": kwargs,
			}
		)
		rows = [dict(r) for r in STATE["records"]]
		return [{f: r.get(f) for f in fields} for r in rows][:limit_page_length]

	frappe.get_list = get_list

	def get_all(*args, **kwargs):
		raise AssertionError("the board reads through get_list, never get_all")

	frappe.get_all = get_all

	def sql(query, values=None, as_dict=False):
		STATE["sql"].append((query, values))
		out = []
		for parent in values["names"]:
			for value in STATE["streams"].get(parent, []):
				out.append({"parent": parent, "value": value})
		return out

	frappe.db = types.SimpleNamespace(sql=sql, has_column=lambda doctype, column: True)

	class _Settings(dict):
		def get(self, key, default=None):
			return dict.get(self, key, default)

	frappe.get_single = lambda doctype: _Settings(STATE["settings"])
	frappe.defaults = types.SimpleNamespace(get_global_default=lambda key: "USD")

	def log_error(title=None, message=None, **kwargs):
		STATE["errors"].append((title, message))

	frappe.log_error = log_error
	frappe.get_traceback = lambda: "Traceback ..."
	frappe.session = types.SimpleNamespace(user="sales@example.com")

	utils = types.ModuleType("frappe.utils")

	def flt(value, precision=None):
		try:
			return float(value or 0)
		except (TypeError, ValueError):
			return 0.0

	def cint(value):
		try:
			return int(float(value or 0))
		except (TypeError, ValueError):
			return 0

	def getdate(value=None):
		if value is None:
			return datetime.date(2026, 10, 13)
		if isinstance(value, datetime.datetime):
			return value.date()
		if isinstance(value, datetime.date):
			return value
		return datetime.date.fromisoformat(str(value)[:10])

	utils.flt = flt
	utils.cint = cint
	utils.getdate = getdate
	utils.nowdate = lambda: TODAY
	utils.date_diff = lambda a, b: (getdate(a) - getdate(b)).days
	utils.get_datetime = lambda v: v
	utils.now_datetime = lambda: datetime.datetime(2026, 10, 13, 12, 0, 0)
	utils.escape_html = lambda s: str(s)
	utils.formatdate = lambda value: str(getdate(value))
	utils.get_fullname = lambda user: {"nik@example.com": "Nik Bradshaw"}.get(user, user)
	frappe.utils = utils
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	return frappe


_SAVED = {}
_STUBBED = ("frappe", "frappe.utils")


def _ours(name):
	return (
		name.startswith("erpnext_enhancements.crm_enhancements")
		or name.startswith("erpnext_enhancements.form_kit")
		or name == "erpnext_enhancements.feature_flags"
	)


def setUpModule():
	for name in list(sys.modules):
		if name in _STUBBED or _ours(name):
			_SAVED[name] = sys.modules.pop(name)
	_install_frappe_stub()


def tearDownModule():
	for name in list(sys.modules):
		if name in _STUBBED or _ours(name):
			sys.modules.pop(name, None)
	sys.modules.update(_SAVED)


def _reset(records, streams=None, meta=None, allowed=True, settings=None):
	STATE.clear()
	STATE.update(
		{
			"records": records,
			"streams": streams or {},
			"meta": meta or _opportunity_meta(),
			"allowed": allowed,
			"settings": settings
			if settings is not None
			else {"pipeline_stale_amber_days": 7, "pipeline_stale_red_days": 14},
			"perm": [],
			"get_list": [],
			"sql": [],
			"errors": [],
		}
	)


def _module():
	import importlib

	return importlib.import_module(MODULE)


def _record(name, **values):
	row = {
		"name": name,
		"status": "Qualification",
		"modified": datetime.datetime(2026, 10, 12, 9, 30, 15, 123456),
		"custom_opportunity_name": f"{name} pond",
		"customer_name": "Smith Ponds",
		"party_name": "CUST-0001",
		"opportunity_amount": 1000,
		"probability": 10,
		"expected_closing": datetime.date(2026, 10, 30),
		"custom_stage_changed_on": datetime.datetime(2026, 10, 1, 8, 0),
		"opportunity_owner": "nik@example.com",
		"custom_snoozed_until": None,
		"custom_snooze_reason": None,
	}
	row.update(values)
	return row


class TestTheEndpoint(unittest.TestCase):
	def setUp(self):
		self.mod = _module()
		self.calls = []
		self.mod.completeness_for = self._completeness

	def _completeness(self, doctype, names):
		self.calls.append((doctype, list(names)))
		return {name: {"stage": "Qualification", "done": 3, "total": 6, "missing": []} for name in names}

	def test_it_is_a_get_and_checks_read_permission(self):
		self.assertEqual(self.mod.get_board.whitelisted, {"methods": ["GET"], "allow_guest": False})
		_reset([], allowed=False)
		with self.assertRaises(sys.modules["frappe"].PermissionError):
			self.mod.get_board()
		self.assertEqual(STATE["perm"], [("Opportunity", "read", True)])

	def test_it_reads_through_get_list_with_open_and_not_historical(self):
		_reset([_record("OPP-1")])
		self.mod.get_board()
		(call,) = STATE["get_list"]
		self.assertEqual(call["doctype"], "Opportunity")
		self.assertIn(["status", "not in", ["Closed Won", "Lost", "Closed", "Converted"]], call["filters"])
		self.assertIn(["custom_is_historical", "!=", 1], call["filters"])
		self.assertNotIn("ignore_permissions", call["kwargs"])
		self.assertEqual(call["limit"], self.mod.MAX_DEALS + 1)

	def test_missing_custom_fields_are_never_named(self):
		missing = (
			"custom_opportunity_name",
			"custom_stage_changed_on",
			"custom_is_historical",
			"custom_value_stream",
		)
		_reset([_record("OPP-1")], meta=_opportunity_meta(missing=missing))
		data = self.mod.get_board()
		(call,) = STATE["get_list"]
		for fieldname in missing:
			self.assertNotIn(fieldname, call["fields"])
		self.assertEqual(
			call["filters"], [["status", "not in", ["Closed Won", "Lost", "Closed", "Converted"]]]
		)
		self.assertEqual(STATE["sql"], [], "no stream table, no stream query")
		deal = data["deals"][0]
		self.assertEqual(deal["title"], "Smith Ponds")
		self.assertEqual(deal["since"], "2026-10-12", "falls back to modified")
		self.assertEqual(deal["streams"], [])

	def test_the_card_fields(self):
		_reset(
			[
				_record("OPP-1"),
				_record(
					"OPP-2",
					opportunity_amount=0,
					probability=None,
					expected_closing=None,
					custom_opportunity_name="",
					customer_name="",
					custom_stage_changed_on=None,
					status="On Hold",
				),
			],
			streams={"OPP-1": ["Design", "Build", "Design", ""]},
		)
		data = self.mod.get_board()
		one, two = data["deals"]
		self.assertEqual(
			one,
			{
				"name": "OPP-1",
				"title": "OPP-1 pond",
				"account": "Smith Ponds",
				"status": "Qualification",
				"amount": 1000.0,
				"probability": 10.0,
				"expected_closing": "2026-10-30",
				"since": "2026-10-01",
				"modified": "2026-10-12 09:30:15.123456",
				"streams": ["Design", "Build"],
				"completeness": {"stage": "Qualification", "done": 3, "total": 6, "missing": []},
				# Part B: the owner filter and table, and the snooze.
				"owner": "nik@example.com",
				"owner_name": "Nik Bradshaw",
				"snoozed_until": None,
				"snooze_reason": "",
			},
		)
		self.assertIsNone(two["amount"], "no amount is None, so the card says so")
		self.assertIsNone(two["probability"])
		self.assertIsNone(two["expected_closing"])
		self.assertEqual(two["title"], "CUST-0001")
		self.assertEqual(two["streams"], [])

	def test_modified_is_the_string_the_form_kit_lock_compares(self):
		_reset([_record("OPP-1", modified=datetime.datetime(2026, 10, 12, 9, 30, 15))])
		data = self.mod.get_board()
		self.assertEqual(data["deals"][0]["modified"], str(datetime.datetime(2026, 10, 12, 9, 30, 15)))
		saver = _text(APP / "form_kit" / "saver.py")
		self.assertIn("if str(doc.modified) != str(modified):", saver)

	def test_stages_thresholds_and_today(self):
		_reset([], settings={"pipeline_stale_amber_days": 7, "pipeline_stale_red_days": 14})
		data = self.mod.get_board()
		self.assertEqual(data["stages"], STAGES)
		self.assertEqual(data["thresholds"], {"amber": 7, "red": 14})
		self.assertEqual((data["today"], data["currency"], data["truncated"]), (TODAY, "USD", False))
		self.assertEqual(data["deals"], [])
		self.assertEqual(self.calls, [], "no names, no completeness call")

	def test_blank_thresholds_fall_back_like_the_tv_board(self):
		_reset([], settings={})
		self.assertEqual(self.mod.get_board()["thresholds"], {"amber": 45, "red": 90})

	def test_streams_and_completeness_are_batched(self):
		records = [_record(f"OPP-{i}") for i in range(1203)]
		_reset(records, streams={"OPP-0": ["Service"], "OPP-1200": ["Events"]})
		data = self.mod.get_board()
		self.assertTrue(data["truncated"])
		self.assertEqual(len(data["deals"]), self.mod.MAX_DEALS)
		self.assertEqual([len(names) for _, names in self.calls], [500, 500])
		self.assertEqual([len(values["names"]) for _, values in STATE["sql"]], [500, 500])
		for query, values in STATE["sql"]:
			self.assertIn("from `tabValue Stream`", query)
			self.assertIn("%(names)s", query)
			self.assertEqual((values["doctype"], values["field"]), ("Opportunity", "custom_value_stream"))
		self.assertEqual(data["deals"][0]["streams"], ["Service"])

	def test_a_completeness_failure_leaves_the_meters_off(self):
		def boom(doctype, names):
			raise RuntimeError("boom")

		self.mod.completeness_for = boom
		_reset([_record("OPP-1")])
		data = self.mod.get_board()
		self.assertIsNone(data["deals"][0]["completeness"])
		self.assertEqual(len(STATE["errors"]), 1)
		self.assertEqual(STATE["errors"][0][0], "Opportunity board: completeness failed")


class TestThePage(unittest.TestCase):
	def test_the_page_record(self):
		page = json.loads(_text(PAGE_JSON))
		self.assertEqual(page["doctype"], "Page")
		self.assertEqual(page["name"], "opportunity-board")
		self.assertEqual(page["page_name"], "opportunity-board")
		self.assertEqual(page["standard"], "Yes")
		self.assertEqual(page["title"], "Opportunity Board")
		modules = [line.strip() for line in _text(APP / "modules.txt").splitlines() if line.strip()]
		self.assertIn(page["module"], modules)
		self.assertEqual(page["module"].lower().replace(" ", "_"), PAGE_DIR.parents[1].name)
		self.assertEqual(
			{row["role"] for row in page["roles"]}, {"Sales User", "Sales Manager", "System Manager"}
		)
		self.assertTrue((PAGE_DIR / "__init__.py").is_file())

	def test_the_slug_is_not_a_workspace(self):
		paths = list(APP.glob("*/workspace/*/*.json"))
		self.assertGreater(len(paths), 5)
		for path in paths:
			name = json.loads(_text(path)).get("name") or ""
			self.assertNotEqual(name.lower().replace(" ", "-"), "opportunity-board", path)

	def test_it_loads_the_kit_and_its_own_bundles(self):
		code = _code(PAGE_JS)
		for asset in (
			"sf_kit.bundle.js",
			"sf_kit.bundle.css",
			"opportunity_board.bundle.js",
			"opportunity_board.bundle.css",
		):
			self.assertIn(f'"{asset}"', code)
		self.assertIn("frappe.require(OB_ASSETS", code)
		self.assertIn('import * as board from "./opportunity_board/board.js";', _text(BUNDLE))
		self.assertIn("window.sf_opportunity_board", _text(BUNDLE))
		self.assertTrue(CSS.is_file())

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
		calls = [m.start() for m in re.finditer(r"\.call\(\s*OB_", code)]
		# get_board, save_step; part B: snooze, and the four saved-view calls; part C: bulk_update,
		# and get_flow for the bulk panel's closing fields.
		self.assertEqual(len(calls), 9)
		for start in calls:
			depth, end = 0, start
			for end in range(code.index("(", start), len(code)):
				depth += {"(": 1, ")": -1}.get(code[end], 0)
				if depth == 0:
					break
			call = code[start : end + 1]
			with self.subTest(call=call[:60]):
				self.assertIn("quiet: true", call)
				self.assertIn("this.sf", code[max(0, start - 30) : start + 1])

	def test_reads_are_get_and_writes_are_post(self):
		code = _code(PAGE_JS)
		self.assertRegex(code, r'OB_API \+ "get_board", \{\}, \{ quiet: true, method: "GET" \}')
		save = code[code.index('OB_FORM_KIT + "save_step"') :]
		save = save[: save.index("{ quiet: true }") + len("{ quiet: true }")]
		self.assertIn("{ doctype: OB_DOCTYPE, name: deal.name, values: { status: stage.label }", save)
		self.assertNotIn("GET", save, "a write is a POST: sf_kit.call's default")
		self.assertIn('OB_FORM_KIT = "erpnext_enhancements.form_kit.api."', code)
		self.assertIn("modified: deal.modified", code)

	def test_a_conflict_is_never_overwritten(self):
		code = _code(PAGE_JS)
		code = code[code.index("\tmove(deal, key) {") :]
		self.assertIn("if (result && result.conflict) {", code)
		conflict = code[code.index("if (result && result.conflict) {") :]
		conflict = conflict[: conflict.index("return;")]
		self.assertIn("this.put_back(before);", conflict)
		self.assertIn("reload = true;", conflict)
		self.assertNotIn("save_step", conflict, "a conflict is never retried with the new modified")

	def test_closing_drops_open_the_guided_close_step(self):
		"""Part C (v1.675.0): a closing drop opens the side panel's required fields
		(test_opportunity_board_panel); the guided Close step stays as its fallback, for when the
		panel's files do not load."""
		code = _code(PAGE_JS)
		self.assertIn("frappe.set_route(OB_GUIDED_PAGE, OB_DOCTYPE, name, OB_CLOSE_STEP);", code)
		fallback = code[code.index("\tside_fallback(side) {") :]
		self.assertIn("this.open_close_step(side.name, true);", fallback[: fallback.index("\n\t}\n")])
		self.assertIn('const OB_GUIDED_PAGE = "guided-entry";', code)
		self.assertIn('const OB_CLOSE_STEP = "close";', code)
		keys = re.findall(r'"key": "(\w+)"', _text(APP / "form_kit" / "config" / "opportunity.py"))
		self.assertIn("close", keys, "the guided flow's Close step exists")

	def test_cards_open_the_record_through_the_form_route(self):
		self.assertIn('frappe.set_route("Form", OB_DOCTYPE, name);', _code(PAGE_JS))

	def test_screens_move_with_set_route(self):
		code = _code(PAGE_JS)
		self.assertIn("frappe.set_route(route);", code)
		self.assertIn("this.kit.build_route(next)", code)
		self.assertIn("frappe.route_flags.replace_route = true", code)
		self.assertIn("delete frappe.route_flags.replace_route", code)
		self.assertIn("window.history.back();", code)
		self.assertNotIn("pushState", code)
		self.assertNotIn('addEventListener("popstate"', code)
		self.assertIsNone(re.search(r"location(\.href)?\s*=(?!=)", code), "never a hand-built navigation")

	def test_no_hard_coded_app_paths(self):
		pattern = re.compile(r"""['"`(]/app/""")
		for path in (PAGE_JS, BUNDLE, BOARD_JS / "board.js"):
			with self.subTest(file=path.name):
				self.assertIsNone(pattern.search(_code(path)))

	def test_it_refreshes_on_the_pipeline_event(self):
		self.assertIn('frappe.realtime.on("sales_pipeline_updated"', _code(PAGE_JS))
		self.assertIn('frappe.publish_realtime("sales_pipeline_updated"', _text(PIPELINE_PY))

	def test_dragging_uses_pointer_events(self):
		code = _code(PAGE_JS)
		for event in ("pointerdown", "pointermove", "pointerup", "pointercancel"):
			self.assertIn(f'"{event}"', code)
		self.assertIn("this.kit.HOLD_MS", code)
		self.assertIn("{ passive: false }", code)
		for forbidden in ("draggable", "dragstart", "ondrop"):
			self.assertNotIn(forbidden, code)

	def test_the_phone_taps_to_move(self):
		"""Part C (v1.675.0, P6.6): a tap opens the deal in the side panel, on a phone too; the move
		panel is the card's Move button, on every width."""
		code = _code(PAGE_JS)
		self.assertIn('window.matchMedia("(max-width: 767px)")', code)
		self.assertIn('hit = el.closest("[data-move]");\n\t\tif (hit) return this.open_panel(hit.getAttribute("data-move"));', code)
		self.assertIn("this.open_side({ kind: this.kit.EDIT, name, step: \"\" });", code)
		self.assertNotIn("if (this.is_phone()) this.open_panel(name);", code)
		self.assertIn("ob_panel", code)

	def test_a_stale_load_is_dropped_and_followed_by_a_fresh_one(self):
		code = _code(PAGE_JS)
		refresh = code[code.index("\trefresh() {") :]
		refresh = refresh[: refresh.index("\n\t}\n")]
		self.assertIn("const started = this.move_epoch;", refresh)
		self.assertIn(
			"if (!this.kit.load_is_current(started, this.move_epoch, this.saving.size + (this.drag ? 1 : 0))) {",
			refresh,
		)
		drop = refresh[refresh.index("load_is_current") :]
		drop = drop[: drop.index("return;")]
		self.assertIn("this.refresh_waiting = true;", drop)
		self.assertNotIn("this.model =", drop, "a stale payload never becomes the model")
		self.assertIn(
			"if (dropped && this.refresh_waiting && !this.saving.size && !this.drag) this.refresh();", refresh
		)
		move = code[code.index("\tmove(deal, key) {") :]
		self.assertLess(move.index("this.move_epoch += 1;"), move.index("this.saving.add(deal.name);"))
		self.assertIn("if (reload || this.refresh_waiting) this.refresh();", move)

	def test_in_page_routes_do_not_scroll_to_the_top(self):
		"""v16's container.change_to scrolls to 0 on every route change unless the wrapper says not to."""
		code = _code(PAGE_JS)
		self.assertIn("wrapper.disable_scroll_to_top = true;", code)
		show = code[code.index('frappe.pages["opportunity-board"].on_page_show') :]
		show = show[: show.index("\n};")]
		self.assertIn("if (board.arriving) {", show)
		self.assertIn("frappe.utils.scroll_to(0)", show)
		hide = code[code.index("\ton_hide() {") :]
		self.assertIn("this.arriving = true;", hide[: hide.index("\n\t}\n")])

	def test_a_bundle_that_never_arrived_is_said_not_waited_on(self):
		"""v16's frappe.require calls back even when a file failed, so the callback proves nothing."""
		code = _code(PAGE_JS)
		self.assertIn("window.sf_kit && window.sf_opportunity_board ? window.sf_kit : null", code)
		self.assertIn("this.ready.then(() => (this.kit ? this.refresh() : this.show_broken()))", code)
		self.assertIn("show_broken() {", code)

	def test_no_settings_single_is_read(self):
		code = _code(PAGE_JS)
		self.assertNotIn("get_single_value", code)
		self.assertNotIn('"Settings"', code)

	def test_the_stylesheet_uses_tokens_only(self):
		css = re.sub(r"/\*[\s\S]*?\*/", "", _text(CSS))
		self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b", css), "a colour value of its own")
		for forbidden in ("rgb(", "rgba(", "hsl(", "shadow"):
			self.assertNotIn(forbidden, css.lower())
		named = re.findall(r":\s*(?:[^;]*\s)?(white|black|red|yellow|green|blue|gray|grey)\b", css)
		self.assertEqual(named, [], "a named colour of its own")
		self.assertIn("@media (max-width: 767px)", css)
		self.assertIn("env(safe-area-inset-bottom", css)
		self.assertIn("var(--sf-touch-min)", css)
		for pillar in ("design", "build", "service", "rent", "neutral"):
			with self.subTest(pillar=pillar):
				self.assertIn(f"var(--sf-pillar-{pillar}-deep)", css)
				self.assertIn(f"var(--sf-chip-edge-{pillar})", css)
		self.assertIn("var(--sf-warn)", css)
		self.assertIn("var(--sf-danger)", css)

	def test_the_bundle_names_are_ours(self):
		from erpnext_enhancements.tests.test_bundle_name_collisions import UPSTREAM_BUNDLE_KEYS

		for keys in UPSTREAM_BUNDLE_KEYS.values():
			self.assertNotIn("opportunity_board.bundle.js", keys)
			self.assertNotIn("opportunity_board.bundle.css", keys)

	def test_the_bundles_are_not_global(self):
		hooks = _text(APP / "hooks.py")
		self.assertNotIn("opportunity_board.bundle", hooks)

	def test_the_crm_sidebar_links_the_board_under_the_guided_page(self):
		tweaks = _text(APP / "setup" / "workspace_tweaks.py")
		block = tweaks[tweaks.index('"CRM": [') :]
		block = block[: block.index("\n\t],")]
		self.assertLess(
			block.index('"link_to": "guided-entry"'), block.index('"link_to": "opportunity-board"')
		)
		board = block[block.index('"link_to": "opportunity-board"') :]
		self.assertIn('"after": ("Page", "guided-entry")', board)


class TestWhatStaysForLater(unittest.TestCase):
	"""P6.10 retires the stock Kanban's patches and the list's redirect; this part must not."""

	def test_the_list_still_redirects_to_the_stock_kanban(self):
		code = _code(JS / "opportunity_list.js")
		self.assertIn("frappe.set_route('opportunity', 'view', 'kanban', 'Opportunity');", code)
		self.assertNotIn("opportunity-board", _text(JS / "opportunity_list.js"))

	def test_the_kanban_patches_still_load(self):
		bundle = _text(JS / "ee_kanban.bundle.js")
		for name in ("kanban_customization", "kanban_patches"):
			with self.subTest(patch=name):
				self.assertIn(name, bundle)
				self.assertTrue((JS / f"{name}.js").is_file())

	def test_the_tv_board_is_unchanged_in_kind(self):
		pipeline = _text(PIPELINE_PY)
		self.assertIn(
			"frappe.get_all(", pipeline, "the TV board still reads permission-free, as a wall display"
		)
		self.assertNotIn("opportunity_board", pipeline)


if __name__ == "__main__":
	unittest.main()
