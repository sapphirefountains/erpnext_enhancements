"""Bench-free tests for the Opportunity board, part B (form kit P6.3, P6.4, P6.8; v1.663.0).

Part A's suite (``test_opportunity_board``) covers the board, rows, forecast and phone layout.
This one covers what part B adds, in four halves:

* **Under node**: the filter bar (My deals, owner, value stream, over $X, closing this month),
  snoozing (hidden until its date, then returning), the needs-attention rules and their order,
  the table's sort, the timeline's months, the views' routes, and a saved view's shape, all from
  ``public/js/opportunity_board/board.js``, copied beside ``public/js/form_kit`` into a temp dir
  with a ``{"type": "module"}`` package.json, as part A's suite does.
* **The attention SQL check** (P6.4: "done when the list matches a SQL count of the same rules").
  The deals go through the real ``get_board``, with the form kit's real ``completeness_for``
  (against a frappe stub that serves their child tables), into node's ``attention``. The SQL in
  ``crm_enhancements/README.md`` runs on an SQLite copy of the same deals and child tables, plus a
  closed, a historical and a snoozed deal it must leave out. Both must name the same deals, in the
  same order of value.
* **The snooze endpoint**: write permission, the ``modified`` lock and its conflict shape, the date
  rules, only two fields written with ``db_set``, a comment, and the realtime event.
* **Saved views and the fields**: ``planner_views_prefs`` keeps the board's views under the planner
  key ``board`` with the page's own schema; the two snooze fields are hidden, ``no_copy`` Opportunity
  custom fields; and the page's static wiring.

Run: python -m unittest erpnext_enhancements.tests.test_opportunity_board_focus -v
"""

import datetime
import importlib
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
PAGE_JS = APP / "crm_enhancements" / "page" / "opportunity_board" / "opportunity_board.js"
CSS = APP / "public" / "css" / "opportunity_board.bundle.css"
CRM_README = APP / "crm_enhancements" / "README.md"
FIXTURES = APP / "fixtures"
MODULE = "erpnext_enhancements.crm_enhancements.page.opportunity_board.opportunity_board"
PREFS = "erpnext_enhancements.api.planner_views_prefs"

TODAY = "2026-10-14"
USER = "nik@example.com"


def _text(path):
	return path.read_text(encoding="utf-8")


def _code(path):
	src = re.sub(r"/\*[\s\S]*?\*/", "", _text(path))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


# ---------------------------------------------------------------------------------- node fixtures

STAGES = [
	{"label": "Qualification", "kind": "open"},
	{"label": "Needs Analysis", "kind": "open"},
	{"label": "Proposal/Price Quote", "kind": "open"},
	{"label": "Negotiation/Review", "kind": "open"},
	{"label": "On Hold", "kind": "parked"},
	{"label": "Lost", "kind": "close"},
	{"label": "Closed Won", "kind": "close"},
]


def _deal(
	name,
	status,
	amount,
	closing,
	since,
	streams,
	owner,
	done=6,
	total=6,
	snoozed=None,
	title=None,
	probability=50,
):
	return {
		"name": name,
		"title": title or f"Deal {name}",
		"account": "Acct",
		"status": status,
		"amount": amount,
		"probability": probability,
		"expected_closing": closing,
		"since": since,
		"modified": "2026-10-12 09:00:00",
		"streams": streams,
		"completeness": {"stage": status, "done": done, "total": total, "missing": [{"label": "Lead Source"}]}
		if done < total
		else {"stage": status, "done": done, "total": total, "missing": []},
		"owner": owner,
		"owner_name": {"nik@example.com": "Nik", "lisa@example.com": "Lisa"}.get(owner, ""),
		"snoozed_until": snoozed,
		"snooze_reason": "",
	}


DEALS = [
	_deal("A", "Qualification", 48000, "2026-10-28", "2026-10-11", ["Design", "Build"], USER, title="Alpha"),
	_deal(
		"B", "Qualification", None, None, "2026-10-01", ["Service"], "lisa@example.com", 2, 6, title="Bravo"
	),
	_deal("C", "Needs Analysis", 125000, "2026-11-15", "2026-09-20", ["Design"], USER, 7, 9, title="Charlie"),
	_deal(
		"D",
		"Proposal/Price Quote",
		7800,
		"2026-10-02",
		"2026-10-09",
		["Delivery"],
		"lisa@example.com",
		11,
		11,
		title="Delta",
	),
	_deal("E", "On Hold", 22000, "2026-09-30", "2026-05-01", ["Service"], "lisa@example.com", title="Echo"),
	_deal(
		"F",
		"Negotiation/Review",
		15000,
		"2027-01-03",
		"2026-10-12",
		["Events"],
		USER,
		11,
		11,
		title="Foxtrot",
	),
	_deal(
		"G",
		"Qualification",
		9000,
		"2026-10-20",
		"2026-09-01",
		["Build"],
		USER,
		snoozed="2026-10-30",
		title="Golf",
	),
	_deal(
		"H", "Qualification", 3000, "2026-12-01", "2026-10-13", [], "", snoozed="2026-10-14", title="Hotel"
	),
	_deal("I", "Needs Analysis", 60000, "2026-10-31", "2026-10-13", ["Build"], USER, 9, 9, title="India"),
]
PAYLOAD = {
	"stages": STAGES,
	"deals": DEALS,
	"thresholds": {"amber": 7, "red": 14},
	"today": TODAY,
	"currency": "USD",
	"truncated": False,
	"can_snooze": True,
}

RUNNER = r"""
import { pathToFileURL } from "node:url";
const [dir, inputJson] = process.argv.slice(2);
const kit = await import(pathToFileURL(dir + "/opportunity_board/board.js").href);
const input = JSON.parse(inputJson);
const out = {};
const names = (deals) => deals.map((d) => d.name);

if (input.attention_only) {
	const model = kit.prepare(input.payload);
	const vm = kit.filtered(model, kit.DEFAULT_FILTERS, input.user);
	out.attention = kit.attention(vm).items.map((i) => [i.deal.name, i.reasons]);
	console.log(JSON.stringify(out));
	process.exit(0);
}

const model = kit.prepare(input.payload);
const user = input.user;
out.snooze = Object.fromEntries(model.deals.map((d) => [d.name, d.snooze]));
out.states = [
	kit.snooze_state({ snoozed_until: "2026-10-15" }, "2026-10-14"),
	kit.snooze_state({ snoozed_until: "2026-10-14" }, "2026-10-14"),
	kit.snooze_state({ snoozed_until: "2026-10-01 00:00:00" }, "2026-10-14"),
	kit.snooze_state({ snoozed_until: "" }, "2026-10-14"),
	kit.snooze_state({ snoozed_until: "nonsense" }, "2026-10-14"),
];
out.snooze_min = [kit.snooze_min("2026-10-14"), kit.snooze_min("2026-12-31"), kit.snooze_min("2028-02-28"), kit.snooze_min("")];

const f = (changes) => names(kit.filtered(model, Object.assign({}, kit.DEFAULT_FILTERS, changes), user).deals);
out.filters = {
	none: f({}),
	mine: f({ mine: true }),
	mine_no_user: names(kit.filtered(model, Object.assign({}, kit.DEFAULT_FILTERS, { mine: true }), "").deals),
	owner: f({ owner: "lisa@example.com" }),
	stream: f({ stream: "Design" }),
	no_stream: f({ stream: kit.NO_STREAM_FILTER }),
	over: f({ min_amount: "$15,000" }),
	closing: f({ closing_month: true }),
	snoozed: f({ show_snoozed: true }),
	combined: f({ mine: true, stream: "Build", min_amount: "10000" }),
};
const vm = kit.filtered(model, kit.DEFAULT_FILTERS, user);
out.counts = { snoozed: vm.snoozed, hidden: vm.hidden, shown: vm.deals.length, by_name: Object.keys(vm.by_name).length };
out.active = [
	kit.active_filters(kit.DEFAULT_FILTERS),
	kit.active_filters({ mine: true, hold_open: true, show_snoozed: true }),
	kit.active_filters({ owner: "x", stream: "y", min_amount: "5", closing_month: true, mine: true }),
];
out.clean = kit.clean_filters({ mine: "1", owner: "  a@b ", min_amount: "abc", closing_month: "true", junk: 1 });
out.limits = [kit.amount_limit("$25,000"), kit.amount_limit("-5"), kit.amount_limit(""), kit.amount_limit("1e3")];
out.owners = kit.owners(model);
out.stream_choices = kit.stream_choices(model);

const att = kit.attention(vm);
out.attention = att.items.map((i) => [i.deal.name, i.reasons]);
out.attention_counts = att.counts;
out.attention_with_snoozed = kit.attention(kit.filtered(model, Object.assign({}, kit.DEFAULT_FILTERS, { show_snoozed: true }), user)).items.map((i) => i.deal.name);

out.table = {};
for (const key of kit.SORT_KEYS) {
	out.table[key] = [names(kit.table_rows(vm, { sort: key, desc: false })), names(kit.table_rows(vm, { sort: key, desc: true }))];
}
out.next_sort = [
	kit.next_sort({ sort: "closing", desc: false }, "closing"),
	kit.next_sort({ sort: "closing", desc: false }, "amount"),
	kit.next_sort({ sort: "closing", desc: false }, "title"),
	kit.next_sort({ sort: "amount", desc: true }, "nope"),
];
out.timeline = kit.timeline(vm).map((g) => ({ key: g.key, past: g.past, current: g.current, names: names(g.deals), count: g.count, total: g.total, weighted: g.weighted }));

out.routes = {
	table: kit.parse_route(["opportunity-board", "table"]),
	timeline_panel: kit.parse_route(["opportunity-board", "timeline", "move", "X-1"]),
	attention: kit.parse_route(["opportunity-board", "attention"]),
	build_table: kit.build_route({ view: "table", layout: "streams", stage: "on-hold" }),
	build_att_panel: kit.build_route({ view: "attention", panel: "X-1" }),
	build_board: kit.build_route({ view: "board", layout: "streams", stage: "" }),
	key_table: kit.route_key({ view: "table", layout: "streams", stage: "x" }) === kit.route_key({ view: "table", layout: "", stage: "" }),
	key_views_differ: kit.route_key({ view: "table" }) !== kit.route_key({ view: "timeline" }),
};
out.views = kit.VIEWS;
out.schema = kit.VIEW_SCHEMA;
out.picked = kit.pick_view({ view: "table", layout: "rows", mine: "1", owner: 7, sort: "amount", desc: 0, junk: "x", min_amount: null });

const snoozed = kit.set_snooze(model, "A", "2026-10-20", "HOA vote", "2026-10-14 10:00:00");
out.set_snooze = [snoozed.by_name.A.snooze, snoozed.by_name.A.snooze_reason, snoozed.by_name.A.modified, model.by_name.A.snooze];
const woken = kit.set_snooze(snoozed, "A", "", "ignored", "2026-10-14 10:00:01");
out.woken = [woken.by_name.A.snooze, woken.by_name.A.snooze_reason, woken.by_name.A.snoozed_until];

// Review of #1278: an explicit board address wins over the last-used layout, and a click on the
// layout the address already shows must redraw (there is no route change to do it).
const P = (route) => kit.parse_route(route);
out.last_layout = [
	kit.last_layout_applies(P(["opportunity-board"])),
	kit.last_layout_applies(P(["opportunity-board", "stages"])),
	kit.last_layout_applies(P(["opportunity-board", "stages", "on-hold"])),
	kit.last_layout_applies(P(["opportunity-board", "streams", "move", "X"])),
	kit.last_layout_applies(P(["opportunity-board", "table"])),
	kit.last_layout_applies(null),
];
const lc = (route, layout) => kit.layout_change(P(route), layout);
out.layout_change = [
	lc(["opportunity-board", "stages"], "stages"),
	lc(["opportunity-board", "stages"], "streams"),
	lc(["opportunity-board", "streams", "on-hold"], "stages"),
	lc(["opportunity-board", "streams", "on-hold"], "streams"),
	lc(["opportunity-board"], "stages"),
	lc(["opportunity-board"], "streams"),
	lc(["opportunity-board", "stages"], "nonsense"),
];
// A saved owner or stream no deal has now stays a selected choice.
out.owners_kept = [
	kit.owners(model, "gone@example.com", "Gone Person").map((o) => o.id + "=" + o.name),
	kit.owners(model, "gone@example.com", "").find((o) => o.id === "gone@example.com"),
	kit.owners(model, user, "ignored").filter((o) => o.id === user && o.name === "Nik").length,
];
out.streams_kept = [kit.stream_choices(model, "Fountain Art"), kit.stream_choices(model, "Design")];
out.snooze_max = [kit.snooze_max("2026-10-14"), kit.snooze_max("2027-02-28"), kit.snooze_max(""), kit.MAX_SNOOZE_DAYS];
console.log(JSON.stringify(out));
"""


def _run_node(payload_extra):
	node = shutil.which("node")
	with tempfile.TemporaryDirectory() as tmp:
		folder = Path(tmp)
		(folder / "package.json").write_text('{"type": "module"}', encoding="utf-8")
		shutil.copytree(BOARD_JS, folder / "opportunity_board")
		shutil.copytree(FORM_KIT_JS, folder / "form_kit")
		(folder / "runner.mjs").write_text(RUNNER, encoding="utf-8")
		proc = subprocess.run(
			[node, str(folder / "runner.mjs"), str(folder), json.dumps(payload_extra)],
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=60,
		)
	if proc.returncode != 0:
		raise AssertionError(f"node failed:\n{proc.stderr}")
	return json.loads(proc.stdout)


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestFocusUnderNode(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.out = _run_node({"payload": PAYLOAD, "user": USER})

	# ---- snooze

	def test_snooze_states(self):
		self.assertEqual(self.out["states"], ["snoozed", "returning", "returning", "", ""])
		self.assertEqual(self.out["snooze"]["G"], "snoozed")
		self.assertEqual(self.out["snooze"]["H"], "returning", "its date has come: back, marked")
		self.assertEqual(self.out["snooze"]["A"], "")

	def test_the_earliest_snooze_is_tomorrow(self):
		self.assertEqual(self.out["snooze_min"], ["2026-10-15", "2027-01-01", "2028-02-29", ""])

	def test_a_snoozed_deal_is_hidden_and_counted(self):
		self.assertNotIn("G", self.out["filters"]["none"])
		self.assertIn("H", self.out["filters"]["none"], "a returning deal is back on the board")
		self.assertIn("G", self.out["filters"]["snoozed"])
		self.assertEqual(self.out["counts"], {"snoozed": 1, "hidden": 1, "shown": 8, "by_name": 9})

	def test_setting_and_waking_a_snooze(self):
		self.assertEqual(self.out["set_snooze"], ["snoozed", "HOA vote", "2026-10-14 10:00:00", ""])
		self.assertEqual(self.out["woken"], ["", "", ""])

	# ---- quick filters

	def test_quick_filters(self):
		f = self.out["filters"]
		self.assertEqual(f["mine"], ["A", "C", "F", "I"])
		self.assertEqual(f["mine_no_user"], [], "My deals with no user shows nothing, never everything")
		self.assertEqual(f["owner"], ["B", "D", "E"])
		self.assertEqual(f["stream"], ["A", "C"])
		self.assertEqual(f["no_stream"], ["H"])
		self.assertEqual(f["over"], ["A", "C", "E", "I"], "over $15,000: strictly above, no amount left out")
		self.assertEqual(f["closing"], ["A", "D", "I"], "closing in the site's current month")
		self.assertEqual(f["combined"], ["A", "I"])

	def test_filter_values(self):
		self.assertEqual(self.out["active"], [0, 1, 5])
		self.assertEqual(
			self.out["clean"],
			{
				"mine": True,
				"owner": "a@b",
				"stream": "",
				"min_amount": "",
				"closing_month": True,
				"show_snoozed": False,
				"hold_open": False,
			},
		)
		self.assertEqual(self.out["limits"], [25000, 0, 0, 1000])
		self.assertEqual(
			self.out["owners"],
			[{"id": "lisa@example.com", "name": "Lisa"}, {"id": USER, "name": "Nik"}],
		)
		self.assertEqual(
			self.out["stream_choices"], ["Design", "Build", "Service", "Events", "Delivery", "-"]
		)

	# ---- needs attention (P6.4)

	def test_needs_attention_rules_and_order(self):
		self.assertEqual(
			self.out["attention"],
			[
				["C", ["stale", "incomplete"]],  # 24 days in stage, 7 of 9
				["E", ["overdue"]],  # On Hold: never stale, but past its close date
				["D", ["overdue"]],  # closed by 10/02
				["B", ["stale", "no_amount", "incomplete"]],
			],
		)
		self.assertEqual(
			self.out["attention_counts"], {"stale": 2, "overdue": 2, "no_amount": 1, "incomplete": 2}
		)

	def test_a_snoozed_deal_needs_nothing_even_when_shown(self):
		self.assertNotIn("G", self.out["attention_with_snoozed"], "G is stale and snoozed: it waits")

	# ---- the table and the timeline (P6.3)

	def test_the_table_sorts_with_blanks_last_both_ways(self):
		t = self.out["table"]
		self.assertEqual(t["amount"][0], ["H", "D", "F", "E", "A", "I", "C", "B"])
		self.assertEqual(t["amount"][1], ["C", "I", "A", "E", "F", "D", "H", "B"])
		self.assertEqual(t["closing"][0], ["E", "D", "A", "I", "C", "H", "F", "B"])
		self.assertEqual(t["closing"][1][-1], "B", "no close date stays last when turned around")
		self.assertEqual(t["title"][0], ["A", "B", "C", "D", "E", "F", "H", "I"])
		self.assertEqual(t["stage"][0][:3], ["A", "B", "H"], "stage order, ties by title")
		self.assertEqual(t["owner"][0][-1], "H", "no owner last")
		self.assertEqual(
			t["weighted"][0][-2:], ["B", "E"], "On Hold and no amount have no weighted value: last, by title"
		)

	def test_turning_a_column(self):
		self.assertEqual(
			self.out["next_sort"],
			[
				{"sort": "closing", "desc": True},
				{"sort": "amount", "desc": True},
				{"sort": "title", "desc": False},
				{"sort": "amount", "desc": True},
			],
		)

	def test_the_timeline_by_month(self):
		tl = self.out["timeline"]
		self.assertEqual(
			[(g["key"], g["past"], g["current"]) for g in tl],
			[
				("2026-09", True, False),
				("2026-10", False, True),
				("2026-11", False, False),
				("2026-12", False, False),
				("2027-01", False, False),
				("", False, False),
			],
		)
		self.assertEqual(tl[1]["names"], ["D", "A", "I"], "by date within the month")
		self.assertEqual(tl[-1]["names"], ["B"])
		self.assertEqual(tl[0]["weighted"], 0, "On Hold is out of the weighted figure here too")
		self.assertEqual(sum(g["count"] for g in tl), 8, "every shown deal once")

	# ---- views, routes, saved views

	def test_each_view_has_its_own_route(self):
		r = self.out["routes"]
		self.assertEqual(
			r["table"], {"view": "table", "layout": "", "stage": "", "panel": "", "side": None, "bare": False}
		)
		self.assertEqual(r["timeline_panel"]["panel"], "X-1")
		self.assertEqual(r["attention"]["view"], "attention")
		self.assertEqual(
			r["build_table"], ["opportunity-board", "table"], "filters and stage never in the address"
		)
		self.assertEqual(r["build_att_panel"], ["opportunity-board", "attention", "move", "X-1"])
		self.assertEqual(r["build_board"], ["opportunity-board", "streams"])
		self.assertTrue(r["key_table"])
		self.assertTrue(r["key_views_differ"])
		self.assertEqual(self.out["views"], ["board", "table", "timeline", "attention"])

	def test_a_saved_view_holds_only_its_keys(self):
		self.assertEqual(
			self.out["picked"],
			{"view": "table", "mine": True, "owner": "7", "sort": "amount", "desc": False, "min_amount": ""},
		)

	# ---- review of #1278

	def test_an_explicit_board_address_wins_over_the_last_layout(self):
		"""/stages (with or without a tab or the panel) keeps its layout; a bare address, another
		view, or no route yet takes the remembered one."""
		self.assertEqual(self.out["last_layout"], [True, False, False, False, True, True])

	def test_a_layout_click_on_the_shown_layout_redraws_instead_of_navigating(self):
		def view(item):
			return (item["next"]["layout"], item["next"]["stage"], item["navigate"])

		self.assertEqual(
			[view(item) for item in self.out["layout_change"]],
			[
				("stages", "", False),  # already /stages: no route change, so the page redraws
				("streams", "", True),
				("stages", "on-hold", True),  # the phone's stage tab is kept
				("streams", "on-hold", False),
				("stages", "", False),  # bare is the board by stage already
				("streams", "", True),
				("stages", "", False),  # an unknown layout is the default one
			],
		)

	def test_a_saved_owner_or_stream_nobody_has_stays_selected(self):
		kept, unnamed, once = self.out["owners_kept"]
		self.assertIn("gone@example.com=Gone Person", kept)
		self.assertEqual(unnamed, {"id": "gone@example.com", "name": "gone@example.com"})
		self.assertEqual(once, 1, "an owner on the board keeps the board's name, once")
		with_extra, present = self.out["streams_kept"]
		self.assertEqual(with_extra[-1], "Fountain Art")
		self.assertEqual(present, self.out["stream_choices"], "a stream on the board is not added twice")

	def test_the_latest_snooze_date_is_the_servers_cap(self):
		self.assertEqual(self.out["snooze_max"], ["2027-10-15", "2028-02-29", "", 366])
		mod_text = _text(APP / "crm_enhancements" / "page" / "opportunity_board" / "opportunity_board.py")
		self.assertIn("MAX_SNOOZE_DAYS = 366", mod_text)
		self.assertIn(
			'max="${esc(\n\t\t\t\t\tthis.kit.snooze_max(this.model.today)',
			_text(PAGE_JS),
			"the date input carries it",
		)


# ---------------------------------------------------------------------------------- frappe stub

STATE = {}


class _Field:
	def __init__(self, fieldname, options=None, fieldtype="Data"):
		self.fieldname = fieldname
		self.options = options
		self.fieldtype = fieldtype

	def get(self, key, default=None):
		return getattr(self, key, default)


class _Meta:
	def __init__(self, fields):
		self.fields = {f.fieldname: f for f in fields}

	def get_field(self, fieldname):
		return self.fields.get(fieldname)

	def has_field(self, fieldname):
		return fieldname in self.fields


CHILD = {
	"custom_value_stream": ("Value Stream", "value_stream"),
	"custom_design_customer_requests": ("Design Customer Requests", "design_customer_requests"),
	"custom_build_customer_requests": ("Build Customer Requests", "build_customer_requests"),
	"custom_service_customer_requests": ("Service Customer Requests", "service_customer_requests"),
	"custom_rent_customer_requests": ("Rent Customer Requests", "rent_customer_requests"),
	"lost_reasons": ("Opportunity Lost Reason Detail", "lost_reason"),
}
SCALARS = (
	"status",
	"party_name",
	"customer_name",
	"custom_opportunity_name",
	"opportunity_owner",
	"custom_opportunity_summary",
	"primary_contact",
	"contact_person",
	"custom_lead_source",
	"expected_closing",
	"opportunity_amount",
	"primary_address",
	"probability",
	"custom_stage_changed_on",
	"custom_is_historical",
	"custom_snoozed_until",
	"custom_snooze_reason",
	"custom_scope_rank",
	"custom_schedule_rank",
	"custom_budget_rank",
)


def _opportunity_meta(missing=()):
	options = (
		"Qualification\nNeeds Analysis\nProposal/Price Quote\nNegotiation/Review\nLost\nClosed Won\nOn Hold"
	)
	fields = [_Field(name, options if name == "status" else None) for name in SCALARS if name not in missing]
	fields += [_Field(table, child, "Table") for table, (child, _) in CHILD.items()]
	return _Meta(fields)


def _day(value):
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	return datetime.date.fromisoformat(str(value)[:10])


def _passes_filters(row, filters):
	"""The list filters get_board sends, applied as the database would (a ``>=`` by the day)."""
	for fieldname, op, value in filters or []:
		have = row.get(fieldname)
		if op == "in" and have not in value:
			return False
		if op == "not in" and have in value:
			return False
		if op == "!=" and have == value:
			return False
		if op == ">=" and (have is None or _day(have) < _day(value)):
			return False
	return True


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe.__path__ = []

	def whitelist(methods=None, allow_guest=False, **kwargs):
		def wrap(fn):
			fn.whitelisted = {"methods": methods, "allow_guest": allow_guest}
			return fn

		return wrap

	class _dict(dict):
		def __getattr__(self, key):
			return self.get(key)

	class PermissionError(Exception):
		pass

	class ValidationError(Exception):
		pass

	def throw(message, exc=ValidationError, title=None):
		raise exc(message)

	frappe.whitelist = whitelist
	frappe._ = lambda text, *a, **k: text
	frappe._dict = _dict
	frappe.PermissionError = PermissionError
	frappe.ValidationError = ValidationError
	frappe.throw = throw
	frappe.session = types.SimpleNamespace(user=USER)

	def has_permission(doctype, ptype="read", doc=None, throw=False, **kwargs):
		STATE["perm"].append((doctype, ptype))
		allowed = STATE.get("allowed", True) and ptype not in STATE.get("deny", ())
		if not allowed and throw:
			raise PermissionError(f"No {ptype} on {doctype}")
		return allowed

	frappe.has_permission = has_permission

	def get_meta(doctype):
		if doctype == "Opportunity":
			return STATE["meta"]
		for child, column in CHILD.values():
			if doctype == child:
				return _Meta([_Field(column)])
		raise AssertionError(doctype)

	frappe.get_meta = get_meta

	def get_list(doctype, filters=None, fields=None, order_by=None, limit_page_length=None, **kwargs):
		rows = list(STATE["records"])
		if isinstance(filters, dict) and "name" in filters:
			wanted = set(filters["name"][1])
			rows = [r for r in rows if r["name"] in wanted]
		else:
			# get_board's own filters: open (or, since v1.688.0, recently closed), not historical.
			rows = [r for r in rows if _passes_filters(r, filters)]
		return [{f: r.get(f) for f in fields} for r in rows]

	frappe.get_list = get_list

	def sql(query, values=None, as_dict=False):
		STATE["sql"].append(query)
		parentfield = values.get("table") or values.get("field")
		out = []
		for parent in values["names"]:
			record = next(r for r in STATE["records"] if r["name"] == parent)
			column = CHILD[parentfield][1]
			for row in record.get(parentfield) or []:
				out.append({"parent": parent, "value": row.get(column)})
		return out

	frappe.db = types.SimpleNamespace(sql=sql, has_column=lambda d, c: True)

	class _Settings(dict):
		def get(self, key, default=None):
			return dict.get(self, key, default)

	frappe.get_single = lambda doctype: _Settings(
		{"pipeline_stale_amber_days": 7, "pipeline_stale_red_days": 14}
	)
	frappe.defaults = types.SimpleNamespace(get_global_default=lambda key: "USD")
	frappe.log_error = lambda title=None, message=None, **k: STATE["errors"].append(title)
	frappe.get_traceback = lambda: "Traceback ..."

	def publish_realtime(event, message=None, **kwargs):
		STATE["realtime"].append((event, message))

	frappe.publish_realtime = publish_realtime

	def get_doc(doctype, name):
		assert doctype == "Opportunity"
		return STATE["docs"][name]

	frappe.get_doc = get_doc

	utils = types.ModuleType("frappe.utils")

	def getdate(value=None):
		if value is None:
			return datetime.date.fromisoformat(TODAY)
		if isinstance(value, datetime.datetime):
			return value.date()
		if isinstance(value, datetime.date):
			return value
		return datetime.date.fromisoformat(str(value)[:10])

	def get_datetime(value):
		if isinstance(value, datetime.datetime):
			return value
		return datetime.datetime.fromisoformat(str(value))

	utils.flt = lambda v, p=None: float(v or 0)
	utils.cint = lambda v: int(float(v or 0))
	utils.getdate = getdate
	utils.get_datetime = get_datetime
	utils.nowdate = lambda: TODAY
	utils.add_days = lambda value, days: str(getdate(value) + datetime.timedelta(days=days))
	utils.date_diff = lambda a, b: (getdate(a) - getdate(b)).days
	utils.now_datetime = lambda: datetime.datetime(2026, 10, 14, 12, 0, 0)
	utils.escape_html = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
	utils.formatdate = lambda v: getdate(v).strftime("%m/%d/%Y")
	utils.get_fullname = lambda user: {"nik@example.com": "Nik", "lisa@example.com": "Lisa"}.get(user, user)
	frappe.utils = utils
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	return frappe


_SAVED = {}


def _ours(name):
	return (
		name in ("frappe", "frappe.utils", "erpnext_enhancements.feature_flags", PREFS)
		or name.startswith("erpnext_enhancements.crm_enhancements")
		or name.startswith("erpnext_enhancements.form_kit")
	)


def setUpModule():
	for name in list(sys.modules):
		if _ours(name):
			_SAVED[name] = sys.modules.pop(name)
	_install_frappe_stub()


def tearDownModule():
	for name in list(sys.modules):
		if _ours(name):
			sys.modules.pop(name, None)
	sys.modules.update(_SAVED)


def _reset(records=(), docs=None, allowed=True, meta=None):
	STATE.clear()
	STATE.update(
		{
			"records": list(records),
			"docs": docs or {},
			"allowed": allowed,
			"meta": meta or _opportunity_meta(),
			"perm": [],
			"sql": [],
			"errors": [],
			"realtime": [],
		}
	)


# ---------------------------------------------------------------------------------- the attention check


def _stream(*names):
	return [{"value_stream": n} for n in names]


def _complete(name, status, **values):
	"""A deal with everything its stage asks for, then `values` over it."""
	record = {
		"name": name,
		"status": status,
		"modified": datetime.datetime(2026, 10, 13, 9, 0, 0, 500000),
		"party_name": "CUST-1",
		"customer_name": "Customer",
		"custom_opportunity_name": f"{name} deal",
		"opportunity_owner": USER,
		"custom_opportunity_summary": "A pond",
		"primary_contact": "C-1",
		"contact_person": None,
		"custom_lead_source": "Website",
		"expected_closing": datetime.date(2026, 11, 30),
		"opportunity_amount": 10000,
		"primary_address": "ADDR-1",
		"probability": 25,
		"custom_stage_changed_on": datetime.datetime(2026, 10, 12, 8, 0),
		"custom_is_historical": 0,
		"custom_snoozed_until": None,
		"custom_snooze_reason": None,
		"custom_value_stream": _stream("Design"),
		"custom_design_customer_requests": [{"design_customer_requests": "A koi pond"}],
	}
	record.update(values)
	return record


# Each deal exercises one rule (or none). The amounts are distinct so the order of value is exact.
ATTENTION_RECORDS = [
	_complete("OK-1", "Qualification", opportunity_amount=11000),
	_complete("OK-2", "Proposal/Price Quote", opportunity_amount=12000),
	_complete(
		"OK-3", "On Hold", opportunity_amount=13000, custom_stage_changed_on=datetime.datetime(2026, 1, 1)
	),
	_complete("OK-4", "Needs Analysis", opportunity_amount=14000, contact_person="CP-1", primary_contact=" "),
	_complete(
		"ST-1",
		"Qualification",
		opportunity_amount=21000,
		custom_stage_changed_on=datetime.datetime(2026, 10, 7, 23, 0),
	),
	_complete(
		"ST-2",
		"Needs Analysis",
		opportunity_amount=22000,
		custom_stage_changed_on=None,
		modified=datetime.datetime(2026, 9, 1),
	),
	_complete(
		"FRESH",
		"Qualification",
		opportunity_amount=23000,
		custom_stage_changed_on=datetime.datetime(2026, 10, 8),
	),
	_complete(
		"OD-1", "Negotiation/Review", opportunity_amount=31000, expected_closing=datetime.date(2026, 10, 13)
	),
	_complete("OD-HOLD", "On Hold", opportunity_amount=32000, expected_closing=datetime.date(2026, 3, 1)),
	_complete(
		"TODAY", "Qualification", opportunity_amount=33000, expected_closing=datetime.date(2026, 10, 14)
	),
	_complete("NA-1", "Qualification", opportunity_amount=0),
	_complete("NA-2", "On Hold", opportunity_amount=None),
	_complete("Q-NAME", "Qualification", opportunity_amount=41000, custom_opportunity_name="  "),
	_complete("Q-STREAM", "Qualification", opportunity_amount=42000, custom_value_stream=[]),
	_complete("Q-CONTACT", "On Hold", opportunity_amount=43000, primary_contact=None),
	_complete("Q-OWNER", "Qualification", opportunity_amount=44000, opportunity_owner=""),
	_complete("NA-SOURCE", "Needs Analysis", opportunity_amount=51000, custom_lead_source=None),
	_complete(
		"NA-REQ",
		"Needs Analysis",
		opportunity_amount=52000,
		custom_design_customer_requests=[{"design_customer_requests": " "}],
	),
	_complete(
		"NA-PRODUCTS", "Needs Analysis", opportunity_amount=53000, custom_value_stream=_stream("Products")
	),
	_complete(
		"NA-EVENTS",
		"Negotiation/Review",
		opportunity_amount=54000,
		custom_value_stream=_stream("Products", "Events"),
		custom_design_customer_requests=[],
		custom_rent_customer_requests=[{"rent_customer_requests": "A gala"}],
	),
	_complete("P-ADDR", "Proposal/Price Quote", opportunity_amount=61000, primary_address=None),
	_complete("Q-NOADDR", "Qualification", opportunity_amount=62000, primary_address=None),
	_complete("NA-NODATE", "Needs Analysis", opportunity_amount=63000, expected_closing=None),
	_complete(
		"OTHER", "Replied", opportunity_amount=71000, custom_stage_changed_on=datetime.datetime(2026, 9, 1)
	),
	_complete(
		"SNOOZED", "Qualification", opportunity_amount=0, custom_snoozed_until=datetime.date(2026, 10, 15)
	),
	_complete(
		"BACK", "Qualification", opportunity_amount=0, custom_snoozed_until=datetime.date(2026, 10, 14)
	),
]
# In the table for SQL only: get_board never returns them.
SQL_ONLY = [
	_complete("CLOSED", "Closed Won", opportunity_amount=0),
	_complete("LOST", "Lost", opportunity_amount=0),
	_complete("OLD", "Qualification", opportunity_amount=0, custom_is_historical=1),
]
EXPECTED_ATTENTION = {
	"ST-1",
	"ST-2",
	"OD-1",
	"OD-HOLD",
	"NA-1",
	"NA-2",
	"Q-NAME",
	"Q-STREAM",
	"Q-CONTACT",
	"Q-OWNER",
	"NA-SOURCE",
	"NA-REQ",
	"NA-PRODUCTS",
	"P-ADDR",
	"NA-NODATE",
	"OTHER",
	"BACK",
}


def _readme_sql(heading):
	text = _text(CRM_README)
	start = text.index(heading)
	block = re.search(r"```sql\n([\s\S]*?)```", text[start:])
	assert block, heading
	return block.group(1)


def _sqlite(records):
	db = sqlite3.connect(":memory:")
	scalars = [s for s in SCALARS if s != "status"]
	# The column types MariaDB has: a Check is an integer, Currency and Percent are numbers.
	types_ = {"opportunity_amount": "real", "probability": "real", "custom_is_historical": "integer"}
	db.execute(
		"create table `tabOpportunity` (name text, status text, modified text, "
		+ ", ".join(f"{s} {types_.get(s, 'text')}" for s in scalars)
		+ ")"
	)
	tables = {}
	for parentfield, (child, column) in CHILD.items():
		db.execute(
			f"create table `tab{child}` (parent text, parenttype text, parentfield text, {column} text)"
		)
		tables[parentfield] = (child, column)
	for record in records:
		values = [record["name"], record["status"], str(record["modified"])]
		for s in scalars:
			value = record.get(s)
			values.append(None if value is None else value if isinstance(value, int | float) else str(value))
		db.execute(f"insert into `tabOpportunity` values ({', '.join('?' * len(values))})", values)
		for parentfield, (child, column) in tables.items():
			for row in record.get(parentfield) or []:
				db.execute(
					f"insert into `tab{child}` values (?, 'Opportunity', ?, ?)",
					(record["name"], parentfield, row.get(column)),
				)
	return db


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestNeedsAttentionMatchesTheSqlCheck(unittest.TestCase):
	"""P6.4: the page's list, from the real get_board and completeness, against the README's SQL."""

	@classmethod
	def setUpClass(cls):
		_reset(ATTENTION_RECORDS)
		mod = importlib.import_module(MODULE)
		cls.payload = mod.get_board()
		cls.node = _run_node({"payload": cls.payload, "user": USER, "attention_only": True})["attention"]
		db = _sqlite(ATTENTION_RECORDS + SQL_ONLY)
		try:
			cls.rows = db.execute(_readme_sql("### Needs attention check")).fetchall()
		finally:
			db.close()

	def test_the_fixture_reaches_every_rule(self):
		self.assertEqual({name for name, _ in self.node}, EXPECTED_ATTENTION)
		reasons = {reason for _, rs in self.node for reason in rs}
		self.assertEqual(reasons, {"stale", "overdue", "no_amount", "incomplete"})
		# The real completeness was computed (not the test's), and it reached the child tables.
		by_name = {d["name"]: d for d in self.payload["deals"]}
		self.assertEqual(by_name["OK-1"]["completeness"]["done"], by_name["OK-1"]["completeness"]["total"])
		self.assertLess(by_name["NA-REQ"]["completeness"]["done"], by_name["NA-REQ"]["completeness"]["total"])
		self.assertEqual(by_name["NA-EVENTS"]["completeness"]["missing"], [], "Events' requests count")

	def test_the_list_matches_the_sql_deal_for_deal_in_order(self):
		self.assertEqual(len(self.rows), len(self.node), "the SQL count")
		self.assertEqual([name for name, _ in self.rows], [name for name, _ in self.node])

	def test_what_the_sql_leaves_out(self):
		names = {name for name, _ in self.rows}
		for left_out in (
			"CLOSED",
			"LOST",
			"OLD",
			"SNOOZED",
			"FRESH",
			"TODAY",
			"OK-3",
			"Q-NOADDR",
			"NA-EVENTS",
		):
			with self.subTest(deal=left_out):
				self.assertNotIn(left_out, names)


# ---------------------------------------------------------------------------------- the snooze endpoint


class _Doc:
	def __init__(self, name, status="Qualification", **values):
		self.name = name
		self.doctype = "Opportunity"
		self.modified = datetime.datetime(2026, 10, 13, 9, 0, 0)
		self.values = {"status": status, "modified_by": "office@example.com", **values}
		self.writes = []
		self.notified = []
		self.comments = []
		self.saved = False
		self.can_write = True

	def get(self, key, default=None):
		return self.values.get(key, default)

	def check_permission(self, ptype):
		STATE["perm"].append(("doc", ptype))
		if not self.can_write:
			raise sys.modules["frappe"].PermissionError("No write")

	def db_set(self, values, value=None, update_modified=True, **kwargs):
		assert isinstance(values, dict)
		self.writes.append(dict(values))
		self.notified.append(kwargs.get("notify", False))
		self.values.update(values)
		# Frappe's db_set stamps now()'s text, microseconds always written.
		self.modified = "2026-10-14 12:00:00.000000"

	def save(self, *args, **kwargs):
		self.saved = True

	def add_comment(self, comment_type, text=None, **kwargs):
		self.comments.append((comment_type, text))


class TestTheSnoozeEndpoint(unittest.TestCase):
	def setUp(self):
		self.mod = importlib.import_module(MODULE)
		self.doc = _Doc("OPP-1")
		_reset(docs={"OPP-1": self.doc})
		self.modified = str(self.doc.modified)

	def snooze(self, **kwargs):
		args = {"name": "OPP-1", "until": "2026-10-20", "reason": "Budget vote", "modified": self.modified}
		args.update(kwargs)
		return self.mod.snooze(**args)

	def test_it_is_post_only(self):
		self.assertEqual(self.mod.snooze.whitelisted, {"methods": ["POST"], "allow_guest": False})

	def test_it_snoozes_two_fields_with_db_set(self):
		result = self.snooze(reason="  Budget\n vote  ")
		self.assertEqual(
			self.doc.writes,
			[{"custom_snoozed_until": datetime.date(2026, 10, 20), "custom_snooze_reason": "Budget vote"}],
		)
		self.assertFalse(self.doc.saved, "never save(): no unrelated validation may stop a snooze")
		self.assertEqual(self.doc.notified, [True], "an open form on the deal hears of it (notify_update)")
		self.assertEqual(
			result,
			{
				"name": "OPP-1",
				"modified": "2026-10-14 12:00:00",
				"snoozed_until": "2026-10-20",
				"snooze_reason": "Budget vote",
			},
		)
		self.assertEqual(
			self.doc.comments, [("Info", "Snoozed on the Opportunity Board until 10/20/2026: Budget vote")]
		)
		self.assertEqual(STATE["realtime"], [("sales_pipeline_updated", {"opportunity": "OPP-1"})])
		self.assertIn(("doc", "write"), STATE["perm"])

	def test_the_returned_modified_matches_what_the_next_lock_reads(self):
		"""db_set's now() text carries .000000; the table's datetime prints without it."""
		result = self.snooze()
		self.assertEqual(result["modified"], str(datetime.datetime(2026, 10, 14, 12, 0, 0)))

	def test_a_blank_date_wakes_it(self):
		self.doc.values.update(
			{"custom_snoozed_until": datetime.date(2026, 10, 20), "custom_snooze_reason": "x"}
		)
		result = self.snooze(until="", reason="ignored")
		self.assertEqual(self.doc.writes, [{"custom_snoozed_until": None, "custom_snooze_reason": None}])
		self.assertEqual((result["snoozed_until"], result["snooze_reason"]), (None, ""))
		self.assertEqual(self.doc.comments, [("Info", "Woken on the Opportunity Board")])

	def test_a_conflict_writes_nothing(self):
		result = self.snooze(modified="2026-10-01 00:00:00")
		self.assertEqual(
			result,
			{
				"conflict": True,
				"name": "OPP-1",
				"modified": self.modified,
				"modified_by": "office@example.com",
				"server": {"snoozed_until": None, "snooze_reason": ""},
			},
		)
		self.assertEqual((self.doc.writes, self.doc.comments, STATE["realtime"]), ([], [], []))

	def test_refusals(self):
		frappe = sys.modules["frappe"]
		cases = {
			"no modified": {"modified": None},
			"today": {"until": TODAY},
			"the past": {"until": "2026-10-01"},
			"over a year": {"until": "2027-10-16"},
		}
		for label, args in cases.items():
			with self.subTest(case=label):
				with self.assertRaises(frappe.ValidationError):
					self.snooze(**args)
		self.snooze(until="2027-10-15")  # exactly 366 days: allowed
		self.doc.values["status"] = "Closed Won"
		with self.assertRaises(frappe.ValidationError):
			self.snooze(modified=str(self.doc.modified))
		self.assertEqual(len(self.doc.writes), 1)

	def test_write_permission_is_checked_before_anything(self):
		self.doc.can_write = False
		with self.assertRaises(sys.modules["frappe"].PermissionError):
			self.snooze()
		self.assertEqual(self.doc.writes, [])

	def test_a_site_without_the_fields_refuses(self):
		_reset(docs={"OPP-1": self.doc}, meta=_opportunity_meta(missing=("custom_snooze_reason",)))
		with self.assertRaises(sys.modules["frappe"].ValidationError):
			self.snooze()

	def test_the_reason_is_escaped_in_the_comment_and_capped(self):
		self.snooze(reason="<b>x</b>" + "y" * 600)
		self.assertIn("&lt;b&gt;x&lt;/b&gt;", self.doc.comments[0][1])
		self.assertEqual(len(self.doc.writes[0]["custom_snooze_reason"]), self.mod.MAX_REASON)

	def test_get_board_says_whether_it_can_snooze(self):
		_reset([_complete("A", "Qualification")])
		self.assertTrue(self.mod.get_board()["can_snooze"])
		_reset([_complete("A", "Qualification")], meta=_opportunity_meta(missing=("custom_snoozed_until",)))
		data = self.mod.get_board()
		self.assertFalse(data["can_snooze"])
		self.assertIsNone(data["deals"][0]["snoozed_until"])
		# Review of #1278: a reader who may not write any Opportunity gets no snooze controls.
		_reset([_complete("A", "Qualification")])
		STATE["deny"] = ("write",)
		data = self.mod.get_board()
		self.assertFalse(data["can_snooze"])
		self.assertEqual(len(data["deals"]), 1, "reading the board is unaffected")
		self.assertIn(("Opportunity", "write"), STATE["perm"])


# ---------------------------------------------------------------------------------- saved views


class TestSavedViewsUseThePlannersStore(unittest.TestCase):
	def setUp(self):
		self.prefs = importlib.import_module(PREFS)
		_reset()

	def test_the_board_is_a_planner_key(self):
		self.assertEqual(self.prefs.PLANNERS["board"], "Opportunity Board")
		planner, doctype = self.prefs._require("board")
		self.assertEqual((planner, doctype), ("board", "Opportunity Board"))
		self.assertEqual(STATE["perm"], [("Opportunity", "read")])
		_reset(allowed=False)
		with self.assertRaises(sys.modules["frappe"].PermissionError):
			self.prefs._require("board")

	@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
	def test_the_server_schema_is_the_pages(self):
		page = _run_node({"payload": PAYLOAD, "user": USER})["schema"]
		server = self.prefs.SCHEMA["board"]
		self.assertEqual(set(page), set(server))
		for key, rule in server.items():
			with self.subTest(key=key):
				self.assertEqual(page[key][0], rule[0])
				if rule[0] == "enum":
					self.assertEqual(tuple(page[key][1]), tuple(rule[1]))

	def test_a_view_is_cleaned_on_the_way_in(self):
		view = self.prefs.clean_view(
			"board",
			{"view": "table", "layout": "rows", "mine": "1", "owner": " a@b ", "junk": 1, "sort": "x"},
		)
		self.assertEqual(view, {"view": "table", "mine": True, "owner": "a@b"})

	def test_the_planners_are_untouched(self):
		self.assertEqual(
			set(self.prefs.SCHEMA["project"]),
			{"view", "project", "pm", "group", "foreign", "over_only", "panel", "color_by"},
		)
		self.assertEqual(self.prefs.PLANNERS["project"], "Project Planner")
		self.assertEqual(self.prefs.PRINT_EYEBROW["board"], "Opportunity Board")


# ---------------------------------------------------------------------------------- the fields and the page


class TestTheSnoozeFields(unittest.TestCase):
	def test_two_hidden_no_copy_opportunity_fields(self):
		fields = {f["name"]: f for f in json.loads(_text(FIXTURES / "custom_field.json"))}
		expect = {
			"Opportunity-custom_snoozed_until": ("custom_snoozed_until", "Date"),
			"Opportunity-custom_snooze_reason": ("custom_snooze_reason", "Small Text"),
		}
		for name, (fieldname, fieldtype) in expect.items():
			with self.subTest(field=name):
				field = fields[name]
				self.assertEqual(
					(field["dt"], field["fieldname"], field["fieldtype"]),
					("Opportunity", fieldname, fieldtype),
				)
				self.assertEqual(
					(field["hidden"], field["no_copy"], field["reqd"], field["unique"]), (1, 1, 0, 0)
				)
				self.assertEqual(field["permlevel"], 0)
				self.assertIsNone(field["default"], "a default would reach every existing row (CLAUDE.md)")
		self.assertEqual(fields["Opportunity-custom_snooze_reason"]["insert_after"], "custom_snoozed_until")

	def test_the_guided_flow_never_offers_them(self):
		"""Hidden fields are not in the flow; nothing names them in the config or the help file."""
		for path in (
			APP / "form_kit" / "config" / "opportunity.py",
			APP / "form_kit" / "help_text" / "opportunity.json",
		):
			with self.subTest(file=path.name):
				self.assertNotIn("custom_snooze", _text(path))


class TestThePageWiring(unittest.TestCase):
	def test_saved_views_go_through_the_planners_endpoints_quietly(self):
		code = _code(PAGE_JS)
		self.assertIn('const OB_PREFS = "erpnext_enhancements.api.planner_views_prefs.";', code)
		self.assertIn('const OB_PREFS_PLANNER = "board";', code)
		self.assertIn(
			'OB_PREFS + "get_views", { planner: OB_PREFS_PLANNER }, { quiet: true, method: "GET" }', code
		)
		for method in ("save_last_view", "save_view", "delete_view"):
			with self.subTest(method=method):
				start = code.index(f'OB_PREFS + "{method}"')
				call = code[start : code.index("{ quiet: true }", start) + len("{ quiet: true }")]
				self.assertNotIn("GET", call)

	def test_snoozing_is_a_locked_post(self):
		code = _code(PAGE_JS)
		self.assertIn(
			'OB_API + "snooze", { name: deal.name, until, reason, modified: deal.modified }, { quiet: true }',
			code,
		)
		snooze = code[code.index("\tsend_snooze(deal, until, reason) {") :]
		snooze = snooze[: snooze.index("\n\t}\n")]
		self.assertIn("this.move_epoch += 1;", snooze, "a load in flight cannot undo a snooze either")
		conflict = snooze[snooze.index("if (result && result.conflict) {") :]
		self.assertIn("reload = true;", conflict[: conflict.index("return;")])
		self.assertNotIn("set_snooze", conflict[: conflict.index("return;")])

	def test_the_filters_are_page_state_not_the_address(self):
		code = _code(PAGE_JS)
		self.assertIn("this.filters = this.kit.clean_filters(", code)
		self.assertIn("this.vm = this.kit.filtered(this.model, this.filters, frappe.session.user);", code)
		# A view change is a route; the filters ride along unchanged.
		show = code[code.index("\tshow_view(view) {") :]
		show = show[: show.index("\n\t}\n")]
		self.assertIn('this.go({ view, layout: this.layout, stage: "", panel: "" });', show)
		self.assertNotIn("filters", show)

	def test_attention_opens_the_deal_until_the_side_panel_exists(self):
		"""Part C (v1.675.0): the side panel exists, and the attention list opens a deal in it."""
		raw = _text(PAGE_JS)
		self.assertIn("P6.6", raw[raw.index("attention_html() {") :][:1200])
		code = _code(PAGE_JS)
		self.assertIn('.ob-att-item[data-name], .ob-tl-item[data-name]");', code)
		self.assertIn('const name = hit.getAttribute("data-open") || hit.getAttribute("data-name");', code)
		self.assertIn('this.open_side({ kind: this.kit.EDIT, name, step: "" });', code)

	def test_the_page_follows_the_layout_rules(self):
		"""Review of #1278, the wiring half (the rules themselves run under node above)."""
		code = _code(PAGE_JS)
		self.assertIn("this.apply_state(last, false, this.kit.last_layout_applies(this.target));", code)
		self.assertIn("if (s.layout && take_layout) this.layout = s.layout;", code)
		click = code[code.index('hit = el.closest("[data-layout]");') :]
		click = click[: click.index("return;")]
		self.assertIn('this.kit.layout_change(this.target, hit.getAttribute("data-layout"))', click)
		self.assertIn("if (!change.navigate || !this.go(change.next)) {", click)
		self.assertIn("this.render();", click)

	def test_on_hold_folds_by_default(self):
		code = _code(PAGE_JS)
		self.assertIn('return !!stage && stage.kind === "parked" && !this.filters.hold_open;', code)
		self.assertIn('data-hold="open"', code)
		self.assertIn('data-hold="close"', code)

	def test_the_last_view_waits_for_its_load(self):
		code = _code(PAGE_JS)
		remember = code[code.index("\tremember() {") :]
		self.assertIn("if (!this.prefs_ready) return;", remember[: remember.index("\n\t}\n")])
		self.assertIn("return Promise.resolve(this.prefs).then(() => {", code)

	def test_the_new_styles_use_tokens_only(self):
		css = re.sub(r"/\*[\s\S]*?\*/", "", _text(CSS))
		self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b", css))
		self.assertNotIn("shadow", css.lower())
		self.assertIn("font-size: var(--sf-text-input);", css, "16px inputs: iOS zooms into anything smaller")
		self.assertIn(
			".ob-table td::before", css, "the table stacks on a phone instead of scrolling sideways"
		)


if __name__ == "__main__":
	unittest.main()
