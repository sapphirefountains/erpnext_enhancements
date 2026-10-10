# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Planner Phase 6E (TASK-2026-02471): four follow-ups Nik decided on 2026-10-09.

1. **Move by calendar days or working days** (both planners, the selection bar). Calendar days stay
   the default; working days count Monday to Friday from each task's own start, send the weekend to the
   next weekday and keep each task's length in working days. The switch is remembered per device
   (``save_pref``), and the offsets are explicit starts and ends for ``move_many``, so the server
   contract is unchanged. A visit moves by that many weekdays. The pure maths runs under node, in both
   pages' copies, and agrees with ``api/project_planner.add_working_days``.
2. **A day note never sends a morning message by itself.** It is added to a message the person gets
   anyway (the combined digest, the maintenance digest, the rental digest) and never causes one; the
   ``Planner Digest Log`` claim is untouched, so it is still once a day.
3. **A split moves what waited on the original onto the second part.** Same project, open tasks, no
   duplicate row, other projects untouched, a timeline note on each; written straight to the child
   table (never ``Task.save``, so ERPNext's ``reschedule_dependent_tasks`` has nothing to push);
   inside the split's savepoint. The Undo puts exactly those back before it deletes, and a dependent
   added since still stops it. Both parts keep the same name.
4. **Add visit here** on the Maintenance Planner: ``create_visit`` drafts the visit the way the nightly
   scheduler would (its own builder, ``tasks._new_maintenance_record``), linked to the site's active
   contract when there is one, as the caller, with a reason on overbooking and an Undo that deletes the
   draft only while nothing has been attached. The page offers it on an empty spot, by double-click and
   in the toolbar.

Bench-free. It borrows the stubs of the two suites it builds on, one class at a time (each class sets
its stub up and puts ``sys.modules`` back): Phase 6B's for the split, Phase 6D's for the digests and the
visit endpoints. The node parts skip when node is not on PATH.

Run: python -m unittest erpnext_enhancements.tests.test_planner_phase6e
"""

import datetime
import importlib
import json
import re
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.tests import test_planner_phase6b as t6b
from erpnext_enhancements.tests import test_planner_phase6d as t6d

APP = Path(__file__).resolve().parents[1]
PP_JS = APP / "project_enhancements/page/project_planner/project_planner.js"
MP_JS = APP / "sapphire_maintenance/page/maintenance_planner/maintenance_planner.js"
CI = REPO_ROOT / ".github/workflows/ci.yml"
API_README = APP / "api/README.md"
PE_README = APP / "project_enhancements/README.md"
SM_README = APP / "sapphire_maintenance/README.md"
ACTIONS_PY = APP / "api/maintenance_actions.py"
PLANNER_ACTIONS_PY = APP / "api/planner_actions.py"
TASKS_PY = APP / "tasks.py"

D = datetime.date
# 2026-10-09 is a Friday; the 10th and 11th are the weekend; the 12th is a Monday.
FRI0, SAT0, SUN0, MON0, TUE0, WED0, THU0, FRI1 = (
	(D(2026, 10, 9) + datetime.timedelta(days=i)).isoformat() for i in range(8)
)

#: The create_visit arguments and the other two endpoints' (maintenance_actions), and what the page sends.
VISIT_CONTRACT = {
	"create_visit": {
		"project",
		"date",
		"technician",
		"crew",
		"hours",
		"serial_no",
		"template",
		"reason",
		"full_day",
	},
	"remove_created_visit": {"record", "modified"},
	"get_visit_defaults": {"project"},
}


# ====================================================================== helpers for the page tests


def _strip(code):
	return t6b._strip(code)


def _tail_block(code, marker):
	"""A phase block at the end of a page: from its banner to the end of the file."""
	return code[code.index(marker) :]


def _methods_of(block, table):
	return re.findall(r"^\t([A-Za-z_]\w*)\s*\([^)]*\)\s*\{\s*$", block[block.index(table) :], re.M)


# ====================================================================== 1. pure maths under node (no env)

PURE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(0, "utf8");
const ctx = {};
vm.createContext(ctx);
vm.runInContext(source + "\nthis.PP = PP6E_PURE; this.MP = MP6E_PURE;", ctx);
const out = {};
const shared = (P) => ({
	weekend: ["2026-10-09", "2026-10-10", "2026-10-11", "2026-10-12"].map((d) => P.is_weekend(d)),
	next: ["2026-10-09", "2026-10-10", "2026-10-11", "2026-10-12"].map((d) => P.next_weekday(d)),
	add: [
		[ "2026-10-09", 1 ], [ "2026-10-09", 5 ], [ "2026-10-10", 1 ], [ "2026-10-11", 1 ],
		[ "2026-10-12", -1 ], [ "2026-10-11", -1 ], [ "2026-10-10", -1 ], [ "2026-10-14", 10 ],
		[ "2026-10-14", 0 ], [ "2026-10-30", 1 ], [ "2026-10-12", -6 ],
	].map(([d, n]) => P.add_working_days(d, n)),
	between: [
		[ "2026-10-09", "2026-10-12" ], [ "2026-10-12", "2026-10-16" ], [ "2026-10-09", "2026-10-16" ],
		[ "2026-10-14", "2026-10-12" ], [ "2026-10-12", "2026-10-12" ], [ "2026-10-10", "2026-10-11" ],
		[ "2026-10-16", "2026-10-09" ], [ "2026-10-09", "2026-10-10" ],
	].map(([a, b]) => P.working_between(a, b)),
	drop: [
		[ "2026-10-09", "2026-10-10", "working" ], [ "2026-10-09", "2026-10-12", "working" ],
		[ "2026-10-14", "2026-10-11", "working" ], [ "2026-10-09", "2026-10-10", "calendar" ],
		[ "2026-10-14", "2026-10-11", "calendar" ], [ "2026-10-12", "2026-10-12", "working" ],
		[ "2026-10-09", "2026-10-11", "working" ],
	].map(([a, b, unit]) => P.drop_days(a, b, unit)),
	date_for: [
		[ "2026-10-09", 1, "working" ], [ "2026-10-09", 1, "calendar" ], [ "2026-10-12", -1, "working" ],
		[ "2026-10-12", -1, "calendar" ],
	].map(([d, n, unit]) => P.date_for(d, n, unit)),
});
out.pp = shared(ctx.PP);
out.mp = shared(ctx.MP);
const moves = (cards, days, unit) => ctx.PP.moves_for(cards, days, unit, (name) => `fresh-${name}`);
out.moves = {
	fri_plus_one: moves([{ name: "T1", start: "2026-10-09", end: "2026-10-09" }], 1, "working"),
	kept_length: moves([{ name: "T1", start: "2026-10-14", end: "2026-10-16" }], 2, "working"),
	thursday_plus_two: moves([{ name: "T1", start: "2026-10-15", end: null }], 2, "working"),
	over_a_weekend: moves([{ name: "T1", start: "2026-10-15", end: "2026-10-20" }], 1, "working"),
	back: moves([{ name: "T1", start: "2026-10-12", end: "2026-10-13", modified: "m1" }], -1, "working"),
	weekend_only: moves([{ name: "T1", start: "2026-10-10", end: "2026-10-11" }], 1, "working"),
	starts_on_saturday: moves([{ name: "T1", start: "2026-10-10", end: "2026-10-12" }], 1, "working"),
	skips_undated: moves([{ name: "T1", start: null }, { name: "T2", start: "2026-10-12", end: "2026-10-12" }], 1, "working"),
	calendar: moves([{ name: "T1", start: "2026-10-09", end: "2026-10-12" }], 3, "calendar"),
	default_unit: moves([{ name: "T1", start: "2026-10-09", end: "2026-10-09" }], 1, undefined),
	reversed_end: moves([{ name: "T1", start: "2026-10-14", end: "2026-10-13" }], 1, "working"),
};
out.args = ctx.MP.create_args(
	{ project: "PRJ-2", date: "2026-10-14", technician: "", hours: "3.5", full_day: 0, serial_no: "S-1", template: "TPL-1", extra: "x" },
	[{ user: "jesse@x.com", hours: null }]
);
out.args_minimal = ctx.MP.create_args({ project: "PRJ-2", date: "2026-10-14", technician: "a@x.com", hours: "", full_day: 1 }, null);
process.stdout.write(JSON.stringify(out));
"""

_NODE_CACHE = {}


def _node_pure():
	if "pure" in _NODE_CACHE:
		return _NODE_CACHE["pure"]
	node = shutil.which("node")
	if not node:
		raise unittest.SkipTest("node is not on PATH")
	pp, mp = PP_JS.read_text(encoding="utf-8"), MP_JS.read_text(encoding="utf-8")
	source = "\n".join(
		(
			t6b._pure(pp, "PP6B_PURE"),
			t6b._pure(pp, "PP6E_PURE"),
			t6b._pure(mp, "MP6B_PURE"),
			t6b._pure(mp, "MP6E_PURE"),
		)
	)
	result = subprocess.run(
		[node, "-e", PURE_HARNESS],
		input=source,
		capture_output=True,
		text=True,
		encoding="utf-8",
		timeout=60,
	)
	if result.returncode != 0:
		raise AssertionError(result.stderr)
	_NODE_CACHE["pure"] = json.loads(result.stdout)
	return _NODE_CACHE["pure"]


class TestWorkingDaysMaths(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.out = _node_pure()

	def test_the_pages_parse(self):
		node = shutil.which("node")
		for path in (PP_JS, MP_JS):
			result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=60)
			self.assertEqual(result.returncode, 0, f"{path.name}: {result.stderr}")

	def test_both_pages_share_the_weekday_helpers(self):
		self.assertEqual(self.out["pp"], self.out["mp"])

	def test_a_friday_plus_one_working_day_is_the_monday(self):
		out = self.out["pp"]
		self.assertEqual(out["add"][0], MON0)  # Friday + 1
		self.assertEqual(out["add"][1], FRI1)  # Friday + 5
		self.assertEqual(out["add"][2], MON0)  # Saturday + 1
		self.assertEqual(out["add"][3], MON0)  # Sunday + 1
		self.assertEqual(out["add"][4], FRI0)  # Monday - 1
		self.assertEqual(out["add"][5], FRI0)  # Sunday - 1
		self.assertEqual(out["add"][6], FRI0)  # Saturday - 1
		self.assertEqual(out["add"][7], "2026-10-28")  # Wednesday + 10 = two weeks on
		self.assertEqual(out["add"][8], "2026-10-14")  # nothing to add
		self.assertEqual(out["add"][9], "2026-11-02")  # across a month end
		self.assertEqual(out["add"][10], "2026-10-02")  # Monday - 6 working days

	def test_a_weekend_landing_goes_to_the_next_weekday(self):
		out = self.out["pp"]
		self.assertEqual(out["weekend"], [False, True, True, False])
		self.assertEqual(out["next"], [FRI0, MON0, MON0, MON0])
		# A drop on Saturday or Sunday is a drop on the Monday after it.
		self.assertEqual(out["drop"][0], 1)  # Friday -> Saturday = one working day (the Monday)
		self.assertEqual(out["drop"][1], 1)  # Friday -> Monday
		self.assertEqual(out["drop"][2], -2)  # Wednesday -> the Sunday before = the Monday: two back
		self.assertEqual(out["drop"][3], 1)  # the same drop by the calendar
		self.assertEqual(out["drop"][4], -3)
		self.assertEqual(out["drop"][5], 0)
		self.assertEqual(out["drop"][6], 1)  # Friday -> Sunday = the Monday
		self.assertEqual(out["date_for"], [MON0, SAT0, FRI0, "2026-10-11"])

	def test_working_days_between_counts_the_weekdays_after_the_first(self):
		self.assertEqual(self.out["pp"]["between"], [1, 4, 5, -2, 0, 0, -5, 0])

	def test_a_task_keeps_its_length_in_working_days(self):
		moves = self.out["moves"]
		one = {"task": "T1", "modified": "fresh-T1"}
		self.assertEqual(moves["fri_plus_one"], [dict(one, start=MON0, end=MON0)])
		# Wed-Fri is three working days: starting on the Friday it is Friday, Monday, Tuesday.
		self.assertEqual(moves["kept_length"], [dict(one, start="2026-10-16", end="2026-10-20")])
		# Thursday + 2 working days: Friday, then Monday.
		self.assertEqual(moves["thursday_plus_two"], [dict(one, start="2026-10-19", end="2026-10-19")])
		# Thursday to Tuesday over a weekend is four working days; one later, Friday to Wednesday.
		self.assertEqual(moves["over_a_weekend"], [dict(one, start="2026-10-16", end="2026-10-21")])
		self.assertEqual(moves["back"], [dict(one, modified="fresh-T1", start=FRI0, end=MON0)])
		# A task that is only a weekend has no working days: it keeps its calendar length.
		self.assertEqual(moves["weekend_only"], [dict(one, start=MON0, end="2026-10-13")])
		# Sat-Mon is one working day (the Monday): it starts on the next weekday and stays one day.
		self.assertEqual(moves["starts_on_saturday"], [dict(one, start=MON0, end=MON0)])
		self.assertEqual([m["task"] for m in moves["skips_undated"]], ["T2"])
		self.assertEqual(moves["reversed_end"], [dict(one, start="2026-10-15", end="2026-10-15")])

	def test_calendar_days_are_phase_6bs_offsets_and_the_default(self):
		moves = self.out["moves"]
		# Friday-Monday (a weekend between) three calendar days on: the length in calendar days is kept.
		self.assertEqual(
			moves["calendar"],
			[{"task": "T1", "modified": "fresh-T1", "start": "2026-10-12", "end": "2026-10-15"}],
		)
		self.assertEqual(
			moves["default_unit"], [{"task": "T1", "modified": "fresh-T1", "start": SAT0, "end": SAT0}]
		)

	def test_the_create_visit_arguments_from_the_form(self):
		args = dict(self.out["args"])
		self.assertEqual(json.loads(args.pop("crew")), [{"user": "jesse@x.com", "hours": None}])
		self.assertEqual(
			args,
			{
				"project": "PRJ-2",
				"date": "2026-10-14",
				"technician": "",
				"hours": 3.5,
				"full_day": 0,
				"serial_no": "S-1",
				"template": "TPL-1",
			},
		)
		minimal = self.out["args_minimal"]
		self.assertEqual((minimal["technician"], minimal["hours"], minimal["full_day"]), ("a@x.com", 0, 1))
		self.assertEqual(minimal["crew"], "[]")
		self.assertNotIn("serial_no", minimal)
		self.assertNotIn("template", minimal)


class TestWorkingDaysAgreeWithTheServer(t6b._Writes):
	"""The JavaScript weekday maths is ``api/project_planner``'s, kept in step."""

	@classmethod
	def setUpClass(cls):
		t6b.setUpModule()

	@classmethod
	def tearDownClass(cls):
		t6b.tearDownModule()

	def test_add_working_days_and_working_days_between(self):
		out = _node_pure()["pp"]
		adds = [
			("2026-10-09", 1),
			("2026-10-09", 5),
			("2026-10-10", 1),
			("2026-10-11", 1),
			("2026-10-12", -1),
			("2026-10-11", -1),
			("2026-10-10", -1),
			("2026-10-14", 10),
			("2026-10-14", 0),
			("2026-10-30", 1),
			("2026-10-12", -6),
		]
		self.assertEqual(out["add"], [str(t6b.api.add_working_days(day, n)) for day, n in adds])
		betweens = [
			("2026-10-09", "2026-10-12"),
			("2026-10-12", "2026-10-16"),
			("2026-10-09", "2026-10-16"),
			("2026-10-14", "2026-10-12"),
			("2026-10-12", "2026-10-12"),
			("2026-10-10", "2026-10-11"),
			("2026-10-16", "2026-10-09"),
			("2026-10-09", "2026-10-10"),
		]
		self.assertEqual(out["between"], [t6b.api.working_days_between(a, b) for a, b in betweens])


# ====================================================================== 1. the page code under node

PAGE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const [pp_source, mp_source] = JSON.parse(fs.readFileSync(0, "utf8"));
const tr = (text, args) => (args ? String(text).replace(/\{(\d+)\}/g, (m, i) => String(args[i])) : String(text));
const esc = (v) => String(v == null ? "" : v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const make = (tag) => {
	const node = { tag, classes: new Set(), attrs: {}, kids: [], txt: "", html: "", handlers: {}, hidden: false };
	const api = {
		node,
		toggleClass(c, on) { if (on === undefined || on) node.classes.add(c); else node.classes.delete(c); return api; },
		addClass(c) { node.classes.add(c); return api; },
		attr(a, b) { if (typeof a === "object") Object.assign(node.attrs, a); else node.attrs[a] = b; return api; },
		text(t) { node.txt = t; return api; },
		on(ev, fn) { node.handlers[ev] = fn; return api; },
		appendTo(parent) { parent.node.kids.push(api); return api; },
		insertBefore() { return api; },
		hide() { node.hidden = true; return api; },
		show() { node.hidden = false; return api; },
		empty() { node.kids = []; return api; },
		toggle(on) { node.hidden = !on; return api; },
	};
	return api;
};
const head = make("head");
const alerts = [];
const calls = [];
const store = {};
const ctx = {
	console, Promise, setTimeout: (fn) => fn(), __: tr, pp_esc: esc, mp_esc: esc,
	pp_when: (ymd) => `when(${ymd})`, mp_when: (ymd) => `when(${ymd})`,
	PP: { route: "project-planner", api: "erpnext_enhancements.api.project_planner", views: ["week", "month", "crew"], drag_px: 6, undo_reason: "Undo on the Project Planner" },
	MP: { route: "maintenance-planner", views: ["month", "week", "crew"], methods: { move_visit: "m.move_visit" }, undo_reason: "Undo on the Maintenance Planner" },
	frappe: {
		call: (opts) => { calls.push(opts); return Promise.resolve({ message: opts.method.endsWith("get_visit_defaults") ? { project: "PRJ-2", title: "Hotel", technician: "austin@x.com", technician_name: "Austin", crew: [{ user: "korben@x.com", name: "Korben", hours: 2 }], hours: 3, full_day: false, contract: "MNT-CON-1", features: [{ serial_no: "S-1", template: "TPL-1" }, { serial_no: "S-2", template: "TPL-2" }], serial_no: "S-1", template: "TPL-1", open_visit: null } : null }); },
		show_alert: (opts) => alerts.push(typeof opts === "string" ? opts : opts.message),
		msgprint: (m) => alerts.push(typeof m === "string" ? m : m.message),
		confirm: (m, fn) => fn(),
		datetime: { get_today: () => "2026-10-09" },
	},
	$: (html) => make(html),
	document: { getElementById: () => null, head },
	window: {},
	MaintenancePlanner: function MaintenancePlanner() {},
};
ctx.MaintenancePlanner.prototype.ask_reason = () => Promise.resolve("A reason");
vm.createContext(ctx);
vm.runInContext(
	pp_source + "\n" + mp_source +
	"\nthis.PPM = Object.assign({}, PP6B_METHODS, PP6E_METHODS); this.MPM = Object.assign({}, MP6B_METHODS, MP6E_METHODS); this.MP6E = MP6E;",
	ctx
);
const out = {};
const flush = () => new Promise((resolve) => setImmediate(resolve));
const buttons = (bar) => bar.node.kids.flatMap((k) => k.node.kids).filter((k) => k.node.tag.startsWith("<button")).map((k) => [k.node.txt, k.node.attrs["aria-pressed"]]);

(async () => {
	// ------------------------------------------------------------ the Project Planner
	const sent = [];
	const pushed = [];
	const rendered = [];
	const prefs = {};
	const pp = Object.assign(Object.create(ctx.PPM), {
		view: "week", anchor: "2026-10-12", draft_on: false,
		data: { can_edit: true },
		modified: { "TASK-1": "m1" },
		p6_selection: new Set(["TASK-1", "TASK-2", "TASK-3"]),
		by_task: {
			"TASK-1": { name: "TASK-1", subject: "Dig", start: "2026-10-14", end: "2026-10-16", movable: true, modified: "m1" },
			"TASK-2": { name: "TASK-2", subject: "Set", start: "2026-10-15", end: null, movable: true, modified: "m2" },
			"TASK-3": { name: "TASK-3", subject: "Pour", start: "2026-10-09", end: "2026-10-12", movable: true, modified: "m3" },
		},
		load_pref: (key, fallback) => (key in prefs ? prefs[key] : fallback),
		save_pref: (key, value) => { prefs[key] = value; },
		p6b_render_bar() { rendered.push(this.p6e_unit()); },
		send: (method, args, opts) => { sent.push([method, args, opts && opts.message, opts && opts.snapshot]); return Promise.resolve({ moved: [] }); },
		push_undo: (snap, message) => { pushed.push([snap, message]); return true; },
		today: () => "2026-10-01",
		load: () => null,
		render: () => null,
	});
	const bar = make("<div>");
	pp.p6e_bar_switch(bar);
	out.pp_default = { unit: pp.p6e_unit(), buttons: buttons(bar), note: pp.p6e_bar_note(), label: pp.p6e_move_label(), default: pp.p6e_move_default(), pref: Object.assign({}, prefs), style: head.node.kids.length };
	out.pp_calendar_drop = pp.p6e_drop_days("2026-10-09", "2026-10-10");
	out.pp_calendar_moves = pp.p6e_moves([pp.by_task["TASK-1"]], 2, (name) => `fresh-${name}`);
	pp.p6b_move_selection(2);
	await flush();
	out.pp_calendar_sent = JSON.parse(sent[0][1].moves);
	out.pp_calendar_message = sent[0][2];
	// Choose working days from the bar: remembered, and the bar redrawn.
	const click = make("<div>");
	pp.p6e_bar_switch(click);
	click.node.kids[0].node.kids[2].node.handlers.click();
	out.pp_chosen = { unit: pp.p6e_unit(), pref: Object.assign({}, prefs), rendered: rendered.slice() };
	pp.p6e_set_unit("nonsense");
	out.pp_ignored = pp.p6e_unit();
	const bar2 = make("<div>");
	pp.p6e_bar_switch(bar2);
	out.pp_working = { buttons: buttons(bar2), note: pp.p6e_bar_note(), label: pp.p6e_move_label(), default: pp.p6e_move_default(), field: pp.p6e_move_note() };
	out.pp_working_drop = [pp.p6e_drop_days("2026-10-09", "2026-10-10"), pp.p6e_drop_days("2026-10-14", "2026-10-11")];
	sent.length = 0;
	out.pp_drop_handled = pp.p6b_drop({ kind: "card", card: pp.by_task["TASK-1"], from_date: "2026-10-09", from_resource: null }, { date: "2026-10-12" });
	await flush();
	out.pp_working_sent = sent.map((s) => JSON.parse(s[1].moves));
	out.pp_working_message = sent.map((s) => s[2]);
	out.pp_snapshot = sent.map((s) => s[3]);
	// The choice comes back on a fresh page for the same device.
	const again = Object.assign(Object.create(ctx.PPM), { load_pref: (key, fallback) => (key in prefs ? prefs[key] : fallback) });
	out.pp_remembered = again.p6e_unit();
	// A split's answer.
	out.pp_dependents = [pp.p6e_dependents({ repointed: ["TASK-9", "TASK-10"] }), pp.p6e_dependents({ repointed: [] }), pp.p6e_dependents({}), pp.p6e_dependents(null)];

	// ------------------------------------------------------------ the Maintenance Planner
	const msent = [];
	const mprefs = {};
	const mrendered = [];
	const panels = [];
	const technicians = [{ user: "austin@x.com", name: "Austin", enabled: 1 }, { user: "jesse@x.com", name: "Jesse", enabled: 1 }, { user: "old@x.com", name: "Old", enabled: 0 }];
	const visit = (key, date) => ({ kind: "visit", key, name: key, site: key, date, technician: "austin@x.com", movable: true, status: "draft", crew: [] });
	const mp = Object.assign(Object.create(ctx.MPM), {
		view: "week", anchor: "2026-10-12", technician: "",
		data: { can_move_visits: true, can_create_visits: true, technicians },
		modified: {},
		by_key: { "SMR-1": visit("SMR-1", "2026-10-09"), "SMR-2": visit("SMR-2", "2026-10-13") },
		p6_selection: new Set(["SMR-1", "SMR-2"]),
		p6_menu_providers: [], p6_legend_providers: [],
		p6b: { flash: null },
		load_pref: (key, fallback) => (key in mprefs ? mprefs[key] : fallback),
		save_pref: (key, value) => { mprefs[key] = value; },
		p6b_render_bar() { mrendered.push(this.p6e_unit()); },
		site_of: (c) => c.site,
		tech_name: (user) => ({ "austin@x.com": "Austin", "jesse@x.com": "Jesse" })[user] || user,
		today: () => "2026-10-05",
		render: () => null,
		load() { this.loaded = (this.loaded || 0) + 1; return Promise.resolve(); },
		go(view, date) { this.went = [view, date]; },
		range_days: () => ["2026-10-11", "2026-10-12", "2026-10-13", "2026-10-14"],
		push_undo: (snap, message) => { pushed.push([snap, message]); return true; },
		send: (method, args, opts) => { msent.push([method, args, opts]); return Promise.resolve(method === "create_visit" ? { name: "SMR-NEW", modified: "mod-1", date: args.date } : { removed: "SMR-NEW" }); },
		build_crew_editor: () => null,
		read_crew_editor: (panel, lead) => [{ user: "korben@x.com", hours: 2 }].filter((row) => row.user !== lead),
		p6b_panel: (opts) => {
			const panel = {
				opts, values: { project: "PRJ-2", date: "2026-10-14", technician: "austin@x.com" }, set: [], html: [], shown: false, hidden: false,
				fields_dict: { technician: { df: {} } },
				$wrapper: { find: () => ({ empty() {}, html(h) { panel.html.push(h); } }) },
				get_value(name) { return this.values[name]; },
				set_value(name, value) { this.values[name] = value; this.set.push([name, value]); },
				set_primary_action(label, fn) { this.primary = [label, fn]; },
				show() { this.shown = true; }, hide() { this.hidden = true; },
			};
			panels.push(panel);
			return panel;
		},
	});
	const mbar = make("<div>");
	mp.p6e_bar_switch(mbar);
	out.mp_default = { unit: mp.p6e_unit(), buttons: buttons(mbar), date: mp.p6e_date("2026-10-09", 1), question: mp.p6e_question(2, 1), drop: mp.p6e_drop_days("2026-10-09", "2026-10-10"), label: mp.p6e_move_label(), default: mp.p6e_move_default() };
	mp.p6e_set_unit("working");
	const mbar2 = make("<div>");
	mp.p6e_bar_switch(mbar2);
	out.mp_working = { unit: mp.p6e_unit(), pref: Object.assign({}, mprefs), rendered: mrendered.slice(), buttons: buttons(mbar2), date: mp.p6e_date("2026-10-09", 1), question: mp.p6e_question(2, 1), earlier: mp.p6e_question(2, -3), drop: mp.p6e_drop_days("2026-10-09", "2026-10-10"), label: mp.p6e_move_label(), default: mp.p6e_move_default(), note: mp.p6e_bar_note() };
	mp.p6e_unit_value = "working";
	// Two visits move by the same number of weekdays; the past is still refused.
	mp.p6b_move_selection(1);
	await flush(); await flush();
	out.mp_working_sent = msent.filter((s) => s[0] === "move_visit").map((s) => [s[1].record, s[1].date]);
	msent.length = 0;
	alerts.length = 0;
	mp.p6b_move_selection(-5);
	out.mp_past = alerts.slice();
	out.mp_past_sent = msent.length;

	// ---- Add visit here
	const cell = { kind: "cell", ymd: "2026-10-14", user: "jesse@x.com" };
	const items = mp.p6e_menu_items(cell);
	out.mp_menu = items.map((i) => [i.label, i.hint, !!i.disabled]);
	out.mp_menu_past = mp.p6e_menu_items({ kind: "cell", ymd: "2026-09-30" }).map((i) => [i.label, i.hint, !!i.disabled]);
	out.mp_menu_cannot = (() => { mp.data.can_create_visits = false; const r = mp.p6e_menu_items(cell).map((i) => [i.hint, !!i.disabled]); mp.data.can_create_visits = true; return r; })();
	out.mp_menu_other = [mp.p6e_menu_items({ kind: "card", ymd: "2026-10-14" }), mp.p6e_menu_items({ kind: "person" }), mp.p6e_menu_items(null), mp.p6e_menu_items({ kind: "cell" })];
	// A double-click: only on an empty cell.
	const opened = [];
	mp.p6e_add_visit = (target) => opened.push(target);
	const ev = (skip, target) => ({ target: { closest: (sel) => (skip ? {} : null), __t: target }, preventDefault() { ev.prevented = true; } });
	mp.p6b_target = (el) => el.__t;
	mp.p6e_dblclick(ev(false, cell));
	mp.p6e_dblclick(ev(true, cell));
	mp.p6e_dblclick(ev(false, { kind: "card", card: {} }));
	mp.p6e_dblclick(ev(false, null));
	mp.p6e_dblclick(ev(false, { kind: "cell" }));
	mp.data.can_create_visits = false;
	mp.p6e_dblclick(ev(false, cell));
	mp.data.can_create_visits = true;
	out.mp_dblclick = opened;
	delete mp.p6e_add_visit;

	// The toolbar's button (no spot): today or the week on screen; and the form.
	out.mp_start_user = [mp.p6e_start_user(cell), mp.p6e_start_user({ kind: "cell", user: "old@x.com" }), mp.p6e_start_user(null)];
	mp.technician = "jesse@x.com";
	out.mp_start_user_filter = mp.p6e_start_user(null);
	mp.technician = "";
	const panel = mp.p6e_add_visit(cell);
	const fields = panel.opts.fields;
	const field = (name) => fields.find((f) => f.fieldname === name);
	out.mp_form = {
		title: panel.opts.title,
		subtitle: panel.opts.subtitle,
		names: fields.filter((f) => f.fieldname).map((f) => f.fieldname),
		project: [field("project").fieldtype, field("project").options, !!field("project").reqd, field("project").get_query()],
		date: field("date").default,
		technician: field("technician").default,
		technician_options: field("technician").options.map((o) => o.value),
		serial: [field("serial_no").options, field("serial_no").get_query()],
		template: field("template").options,
		primary: panel.primary[0],
		shown: panel.shown,
	};
	// The site is picked: its defaults fill the form; the spot's technician stays.
	await mp.p6e_load_defaults(panel, { token: 0, defaults: null, cell_user: "jesse@x.com", serials: [] });
	out.mp_defaults_call = calls.filter((c) => /get_visit_defaults/.test(c.method)).map((c) => [c.method, c.args]);
	out.mp_defaults_set = panel.set.slice();
	out.mp_note = panel.html.slice();
	// Without a spot the site's own technician fills in.
	const bare = mp.p6e_add_visit(null);
	const state2 = { token: 0, defaults: null, cell_user: "", serials: [] };
	await mp.p6e_load_defaults(bare, state2);
	out.mp_defaults_set_bare = bare.set.filter(([name]) => name === "technician");
	out.mp_serials = state2.serials;
	state2.defaults = { features: [{ serial_no: "S-1", template: "TPL-1" }, { serial_no: "S-2", template: "TPL-2" }] };
	bare.values.serial_no = "S-2";
	bare.set.length = 0;
	mp.p6e_fountain_changed(bare, state2);
	out.mp_fountain = bare.set.slice();
	// Save: nothing without a site and a day; the past is refused; otherwise one create_visit through send().
	out.mp_save_blank = mp.p6e_add_visit_save(panel, { defaults: null }, { project: "", date: "2026-10-14" });
	out.mp_save_past = (() => { alerts.length = 0; const r = mp.p6e_add_visit_save(panel, { defaults: null }, { project: "PRJ-2", date: "2026-09-01" }); return [r, alerts.slice()]; })();
	msent.length = 0; pushed.length = 0; mp.loaded = 0;
	await mp.p6e_add_visit_save(panel, { defaults: { title: "Hotel" } }, { project: "PRJ-2", date: "2026-10-14", technician: "austin@x.com", hours: "2", full_day: 0, serial_no: "S-1", template: "TPL-1" });
	await flush();
	out.mp_created = { sent: msent.map((s) => [s[0], s[1], Object.keys(s[2]).sort(), s[2].noun, s[2].message]), snapshot: msent[0][2].snapshot, flash: mp.p6b.flash, hidden: panel.hidden, loaded: mp.loaded, went: mp.went || null };
	// A visit created on a day outside the weeks on screen goes there instead.
	mp.went = null; mp.loaded = 0;
	await mp.p6e_create({ project: "PRJ-2", date: "2026-12-01" }, "Hotel");
	out.mp_far = { went: mp.went, loaded: mp.loaded };
	// Undo of an added visit.
	msent.length = 0;
	out.mp_undo_other = [mp.p6e_undo({ p6b: "many" }), mp.p6e_undo(null), mp.p6e_undo({ kind: "visit", record: "SMR-1" })];
	out.mp_undo_unnamed = (() => { alerts.length = 0; const r = mp.p6e_undo({ p6e: "created", site: "Hotel", record: null }); return [r, alerts.slice(), msent.length]; })();
	mp.modified["SMR-NEW"] = "mod-2";
	out.mp_undo = [mp.p6e_undo({ p6e: "created", site: "Hotel", record: "SMR-NEW", modified: "mod-1" }), msent.map((s) => [s[0], s[1], s[2].message])];
	out.mp_legend = mp.p6e_legend_sections().map((s) => s.title);
	out.mp_methods = Object.keys(ctx.MP6E.methods).sort();
	// The page's own state: a button that is only there for people who may create visits.
	mp.$p6e_add = make("<button>");
	mp.render_phase6e();
	const shown_for = mp.$p6e_add.node.hidden;
	mp.data.can_create_visits = false;
	mp.render_phase6e();
	out.mp_button_hidden = [shown_for, mp.$p6e_add.node.hidden];
	process.stdout.write(JSON.stringify(out));
})().catch((e) => {
	process.stderr.write(String(e && e.stack ? e.stack : e));
	process.exit(1);
});
"""


class TestPagesUnderNode(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		node = shutil.which("node")
		if not node:
			raise unittest.SkipTest("node is not on PATH")
		pp, mp = PP_JS.read_text(encoding="utf-8"), MP_JS.read_text(encoding="utf-8")
		sources = [
			t6b._phase_source(pp, "const PP6B = {", "Object.assign(ProjectPlanner.prototype, PP6B_METHODS);")
			+ t6b._phase_source(
				pp, "const PP6E = {", "Object.assign(ProjectPlanner.prototype, PP6E_METHODS);"
			),
			t6b._phase_source(
				mp, "const MP6B = {", "Object.assign(MaintenancePlanner.prototype, MP6B_METHODS);"
			)
			+ t6b._phase_source(
				mp, "const MP6E = {", "Object.assign(MaintenancePlanner.prototype, MP6E_METHODS);"
			),
		]
		result = subprocess.run(
			[node, "-e", PAGE_HARNESS],
			input=json.dumps(sources),
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=60,
		)
		if result.returncode != 0:
			raise AssertionError(result.stderr)
		cls.out = json.loads(result.stdout)

	# ---------------------------------------------------------------- the Project Planner

	def test_the_bar_shows_the_switch_and_calendar_days_stay_the_default(self):
		out = self.out["pp_default"]
		self.assertEqual(out["unit"], "calendar")
		self.assertEqual(out["buttons"], [["Calendar days", "true"], ["Working days", "false"]])
		self.assertIn("calendar days", out["note"])
		self.assertEqual((out["label"], out["default"]), ("Move by (days)", 7))
		self.assertEqual(out["pref"], {})  # reading the default writes nothing
		self.assertEqual(out["style"], 1)  # the first use adds the switch's style, once
		self.assertEqual(self.out["pp_calendar_drop"], 1)

	def test_calendar_days_move_every_task_by_the_same_calendar_days(self):
		self.assertEqual(
			self.out["pp_calendar_moves"],
			[{"task": "TASK-1", "modified": "fresh-TASK-1", "start": "2026-10-16", "end": "2026-10-18"}],
		)
		# The selection (three tasks) by two calendar days, each keeping its length; modified is the freshest.
		self.assertEqual(
			self.out["pp_calendar_sent"],
			[
				{"task": "TASK-1", "modified": "m1", "start": "2026-10-16", "end": "2026-10-18"},
				{"task": "TASK-2", "modified": "m2", "start": "2026-10-17", "end": "2026-10-17"},
				{"task": "TASK-3", "modified": "m3", "start": "2026-10-11", "end": "2026-10-14"},
			],
		)
		self.assertEqual(self.out["pp_calendar_message"], "3 tasks moved 2 day(s) later")

	def test_choosing_working_days_is_remembered_per_device(self):
		chosen = self.out["pp_chosen"]
		self.assertEqual(chosen["unit"], "working")
		self.assertEqual(chosen["pref"], {"ee_project_planner_move_by": "working"})
		self.assertEqual(chosen["rendered"], ["working"])  # the bar is drawn again
		self.assertEqual(self.out["pp_ignored"], "working")  # an unknown unit changes nothing
		self.assertEqual(self.out["pp_remembered"], "working")  # a fresh page on the same device
		working = self.out["pp_working"]
		self.assertEqual(working["buttons"], [["Calendar days", "false"], ["Working days", "true"]])
		self.assertEqual((working["label"], working["default"]), ("Move by (working days)", 5))
		self.assertIn("working days", working["note"])
		self.assertIn("working days", working["field"])

	def test_working_days_move_each_task_from_its_own_start_and_keep_its_length(self):
		# Dragging from Friday to Saturday lands on the Monday: one working day. Wed-Fri (3 working days)
		# starting a working day later is Thu-Mon; a one-day task moves a day; Fri-Mon (2 working days)
		# starts on the Monday and runs to the Tuesday.
		self.assertEqual(self.out["pp_working_drop"], [1, -2])
		self.assertTrue(self.out["pp_drop_handled"])
		(sent,) = self.out["pp_working_sent"]
		self.assertEqual(
			sent,
			[
				{"task": "TASK-1", "modified": "m1", "start": "2026-10-15", "end": "2026-10-19"},
				{"task": "TASK-2", "modified": "m2", "start": "2026-10-16", "end": "2026-10-16"},
				{"task": "TASK-3", "modified": "m3", "start": "2026-10-12", "end": "2026-10-13"},
			],
		)
		self.assertEqual(self.out["pp_working_message"], ["3 tasks moved 1 working day(s) later"])
		# One Undo entry, and it holds the old dates, so Undo moves them back exactly.
		(snapshot,) = self.out["pp_snapshot"]
		self.assertEqual(snapshot["p6b"], "many")
		self.assertEqual(
			snapshot["items"],
			[
				{"task": "TASK-1", "start": "2026-10-14", "end": "2026-10-16"},
				{"task": "TASK-2", "start": "2026-10-15", "end": "2026-10-15"},
				{"task": "TASK-3", "start": "2026-10-09", "end": "2026-10-12"},
			],
		)

	def test_the_split_hands_back_the_tasks_it_moved(self):
		self.assertEqual(self.out["pp_dependents"], [{"dependents": ["TASK-9", "TASK-10"]}, {}, {}, {}])

	# ---------------------------------------------------------------- the Maintenance Planner

	def test_visits_move_by_calendar_days_unless_the_bar_says_working_days(self):
		default = self.out["mp_default"]
		self.assertEqual(default["unit"], "calendar")
		self.assertEqual(default["buttons"], [["Calendar days", "true"], ["Working days", "false"]])
		self.assertEqual((default["date"], default["drop"]), (SAT0, 1))
		self.assertEqual(default["question"], "Move 2 visits 1 day(s) later?")
		self.assertEqual((default["label"], default["default"]), ("Move by (days)", 7))
		working = self.out["mp_working"]
		self.assertEqual(working["unit"], "working")
		self.assertEqual(working["pref"], {"ee_maintenance_planner_move_by": "working"})
		self.assertEqual(working["rendered"], ["working"])
		self.assertEqual(working["buttons"], [["Calendar days", "false"], ["Working days", "true"]])
		self.assertEqual((working["date"], working["drop"]), (MON0, 1))
		self.assertEqual(working["question"], "Move 2 visits 1 working day(s) later?")
		self.assertEqual(working["earlier"], "Move 2 visits 3 working day(s) earlier?")
		self.assertEqual((working["label"], working["default"]), ("Move by (working days)", 5))
		self.assertIn("working days", working["note"])

	def test_two_visits_move_by_the_same_number_of_weekdays(self):
		# Friday + 1 weekday is the Monday; Tuesday + 1 is the Wednesday.
		self.assertEqual(self.out["mp_working_sent"], [["SMR-1", MON0], ["SMR-2", "2026-10-14"]])
		# A move into the past is still refused before anything is sent.
		self.assertEqual(self.out["mp_past"], ["Visits can only be moved to today or a later day."])
		self.assertEqual(self.out["mp_past_sent"], 0)

	def test_the_menu_offers_add_visit_here_on_an_empty_spot(self):
		self.assertEqual(self.out["mp_menu"], [["Add visit here…", "Double-click", False]])
		self.assertEqual(self.out["mp_menu_past"], [["Add visit here…", "Not on a past day", True]])
		self.assertEqual(self.out["mp_menu_cannot"], [["You cannot add visits", True]])
		self.assertEqual(self.out["mp_menu_other"], [[], [], [], []])

	def test_a_double_click_on_an_empty_cell_opens_the_form_and_nothing_else_does(self):
		self.assertEqual(
			self.out["mp_dblclick"], [{"kind": "cell", "ymd": "2026-10-14", "user": "jesse@x.com"}]
		)

	def test_the_form_is_prefilled_from_the_spot(self):
		form = self.out["mp_form"]
		self.assertEqual(form["title"], "Add a visit")
		self.assertEqual(form["subtitle"], "Jesse · when(2026-10-14)")
		self.assertEqual(
			form["names"],
			[
				"project",
				"date",
				"technician",
				"crew_editor",
				"hours",
				"full_day",
				"contract_note",
				"serial_no",
				"template",
			],
		)
		kind, options, required, query = form["project"]
		self.assertEqual((kind, options, required), ("Link", "Project", True))
		self.assertEqual(query, {"query": "erpnext_enhancements.api.maintenance_actions.sites_query"})
		self.assertEqual((form["date"], form["technician"]), ("2026-10-14", "jesse@x.com"))
		self.assertEqual(
			form["technician_options"], ["", "austin@x.com", "jesse@x.com"]
		)  # enabled people only
		self.assertEqual(form["serial"], ["Serial No", {}])
		self.assertEqual(form["template"], "Sapphire Maintenance Template")
		self.assertEqual((form["primary"], form["shown"]), ("Add visit", True))
		self.assertEqual(self.out["mp_start_user"], ["jesse@x.com", "", ""])
		self.assertEqual(self.out["mp_start_user_filter"], "jesse@x.com")

	def test_picking_a_site_fills_in_its_defaults(self):
		self.assertEqual(
			self.out["mp_defaults_call"][0],
			["erpnext_enhancements.api.maintenance_actions.get_visit_defaults", {"project": "PRJ-2"}],
		)
		by_name = dict(self.out["mp_defaults_set"])
		# The spot named Jesse, so the site's default technician does not take his place.
		self.assertEqual(by_name["technician"], "jesse@x.com")
		self.assertEqual((by_name["hours"], by_name["full_day"]), (3, 0))
		self.assertEqual((by_name["serial_no"], by_name["template"]), ("S-1", "TPL-1"))
		self.assertIn("Linked to the active contract MNT-CON-1", self.out["mp_note"][0])
		# With no spot the site's technician is the default; the fountains are what the field offers.
		self.assertEqual(self.out["mp_defaults_set_bare"], [["technician", "austin@x.com"]])
		self.assertEqual(self.out["mp_serials"], ["S-1", "S-2"])
		self.assertEqual(
			self.out["mp_fountain"], [["template", "TPL-2"]]
		)  # the checklist follows the fountain

	def test_saving_sends_one_create_visit_through_send_and_offers_an_undo(self):
		self.assertIsNone(self.out["mp_save_blank"])
		result, alerts = self.out["mp_save_past"]
		self.assertIsNone(result)
		self.assertEqual(alerts, ["Pick today or a later day."])
		created = self.out["mp_created"]
		((method, args, opts, noun, message),) = created["sent"]
		self.assertEqual((method, noun), ("create_visit", "visit"))
		self.assertEqual(opts, ["message", "noun", "snapshot"])
		self.assertEqual(message, "Hotel visit added on when(2026-10-14)")
		args = dict(args)
		self.assertEqual(json.loads(args.pop("crew")), [{"user": "korben@x.com", "hours": 2}])
		self.assertEqual(
			args,
			{
				"project": "PRJ-2",
				"date": "2026-10-14",
				"technician": "austin@x.com",
				"hours": 2,
				"full_day": 0,
				"serial_no": "S-1",
				"template": "TPL-1",
			},
		)
		# The snapshot is filled in from the answer; the new card flashes; the page reloads.
		self.assertEqual(
			created["snapshot"], {"p6e": "created", "site": "Hotel", "record": "SMR-NEW", "modified": "mod-1"}
		)
		self.assertEqual(created["flash"], {"key": "SMR-NEW", "date": "2026-10-14", "wait": True})
		self.assertEqual((created["hidden"], created["loaded"], created["went"]), (True, 1, None))
		self.assertEqual(self.out["mp_far"], {"went": ["week", "2026-12-01"], "loaded": 0})

	def test_undo_deletes_the_added_draft_and_nothing_else(self):
		self.assertEqual(self.out["mp_undo_other"], [False, False, False])
		unnamed, alerts, sent = self.out["mp_undo_unnamed"]
		self.assertTrue(unnamed)
		self.assertEqual(sent, 0)
		self.assertEqual(len(alerts), 1)
		handled, sent = self.out["mp_undo"]
		self.assertTrue(handled)
		# The freshest modified the page has seen for the record, as the project planner's Undo does.
		self.assertEqual(
			sent, [["remove_created_visit", {"record": "SMR-NEW", "modified": "mod-2"}, "Undone: Hotel"]]
		)

	def test_the_toolbar_button_is_only_there_for_people_who_may_create_visits(self):
		self.assertEqual(self.out["mp_button_hidden"], [False, True])
		self.assertEqual(self.out["mp_methods"], ["create_visit", "remove_created_visit"])
		self.assertEqual(self.out["mp_legend"], ["Adding a visit"])


class TestPageSource(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.pp = PP_JS.read_text(encoding="utf-8")
		cls.mp = MP_JS.read_text(encoding="utf-8")
		cls.pp_block = _tail_block(
			cls.pp, "// ====================================================================== Phase 6E"
		)
		cls.mp_block = _tail_block(
			cls.mp, "// ====================================================================== Phase 6E"
		)

	def test_the_hooks_are_single_lines(self):
		pp6b, mp6b = t6b._block(self.pp), t6b._block(self.mp)
		for block in (pp6b, mp6b):
			self.assertIn("this.p6e_bar_switch($bar);", t6b._method(block, "p6b_render_bar"))
			self.assertIn(".text(this.p6e_bar_note())", t6b._method(block, "p6b_render_bar"))
			self.assertIn("this.p6e_drop_days(", t6b._method(block, "p6b_drop"))
			panel = t6b._method(block, "p6b_move_panel")
			for hook in ("this.p6e_move_label()", "this.p6e_move_default()", "this.p6e_move_note()"):
				self.assertIn(hook, panel)
		self.assertIn("this.p6e_moves(cards, days,", t6b._method(pp6b, "p6b_move_selection"))
		self.assertIn("this.p6e_message(cards.length, days)", t6b._method(pp6b, "p6b_move_selection"))
		self.assertIn("this.p6e_date(card.date, days)", t6b._method(mp6b, "p6b_move_selection"))
		self.assertIn("this.p6e_question(plans.length, days)", t6b._method(mp6b, "p6b_move_selection"))
		self.assertIn("this.p6e_dependents(result)", t6b._method(pp6b, "p6b_split_save"))
		# The Maintenance Planner's three class hooks; the existing p6b_undo line is untouched.
		self.assertIn("this.init_phase6d();\n\t\tthis.init_phase6e();", self.mp)
		self.assertIn("this.render_phase6d();\n\t\tthis.render_phase6e();", self.mp)
		undo = t6b._method(self.mp, "undo")
		self.assertIn("if (this.p6b_undo(snap)) return;\n\t\tif (this.p6e_undo(snap)) return;", undo)
		self.assertIn("Object.assign(ProjectPlanner.prototype, PP6E_METHODS);", self.pp_block)
		self.assertIn("Object.assign(MaintenancePlanner.prototype, MP6E_METHODS);", self.mp_block)
		# The Project Planner needs no class hook at all: its style is added on first use.
		self.assertNotIn("init_phase6e", self.pp)

	def test_the_choice_is_remembered_with_the_pages_own_save_pref(self):
		for block, key in (
			(self.pp_block, "ee_project_planner_move_by"),
			(self.mp_block, "ee_maintenance_planner_move_by"),
		):
			bare = _strip(block)
			self.assertIn(f'"{key}"', block)
			self.assertIn("this.save_pref(", bare)
			self.assertIn("this.load_pref(", bare)
			for forbidden in ("localStorage", "sessionStorage", "pushState", "replaceState", "innerHTML"):
				self.assertNotIn(forbidden, bare, forbidden)

	def test_the_pure_maths_reuses_the_pages_existing_helpers(self):
		# No third copy of the date arithmetic: the 6B helpers do the stepping, counting and offsets.
		pp_pure = _strip(
			self.pp_block[self.pp_block.index("const PP6E_PURE") : self.pp_block.index("const PP6E_STYLE")]
		)
		self.assertIn("PP6B_PURE.ymd_add(", pp_pure)
		self.assertIn("PP6B_PURE.working_days(card.start, end)", pp_pure)
		self.assertIn("PP6B_PURE.offset_moves(cards, days, modified_of)", pp_pure)
		self.assertNotIn("new Date(`${ymd}T00:00:00Z`).setUTCDate", pp_pure)
		mp_pure = _strip(
			self.mp_block[self.mp_block.index("const MP6E_PURE") : self.mp_block.index("const MP6E_STYLE")]
		)
		self.assertIn("MP6B_PURE.ymd_add(", mp_pure)
		self.assertIn("MP6B_PURE.ymd_diff(", mp_pure)

	def test_every_new_method_has_the_phases_prefix_and_is_defined_once(self):
		for code, block, table, extra in (
			(self.pp, self.pp_block, "const PP6E_METHODS = {", set()),
			(self.mp, self.mp_block, "const MP6E_METHODS = {", {"init_phase6e", "render_phase6e"}),
		):
			own = [m for m in _methods_of(block, table) if m not in extra]
			self.assertTrue(own and all(name.startswith("p6e_") for name in own), own)
			names = re.findall(r"^\t(?:async\s+)?([A-Za-z_]\w*)\s*\([^)]*\)\s*\{\s*$", code, re.M)
			names = [n for n in names if n not in {"if", "for", "while", "switch", "catch", "function"}]
			self.assertEqual(sorted({n for n in names if names.count(n) > 1}), [])

	def test_the_maintenance_planner_writes_only_through_send(self):
		bare = _strip(self.mp_block)
		self.assertNotRegex(bare, r"frappe\.call\(\{\s*method:\s*MP6E\.methods")
		self.assertIn('this.send("create_visit", args,', bare)
		self.assertIn('this.send("remove_created_visit",', bare)
		# send() finds a method in MP.methods, so the two are added to it once, by the phase itself.
		self.assertIn("Object.assign(MP.methods, MP6E.methods);", bare)
		self.assertIn("create_visit: `${MP6E.api}.create_visit`", bare)
		self.assertIn("remove_created_visit: `${MP6E.api}.remove_created_visit`", bare)
		self.assertIn('api: "erpnext_enhancements.api.maintenance_actions"', bare)
		# The two reads are the form's, called with frappe.call (nothing is written by them).
		self.assertIn("method: MP6E.defaults", bare)
		# The menu item is a provider (6B's own cell items are untouched), and the form is a kit panel.
		self.assertIn("this.p6_menu_providers.push((target) => this.p6e_menu_items(target));", bare)
		self.assertIn("this.p6b_panel({", bare)
		self.assertIn('addEventListener("dblclick"', bare)
		# Drag stays pointer events: nothing here uses the HTML5 API.
		for forbidden in ("dataTransfer", 'addEventListener("drop"', "draggable"):
			self.assertNotIn(forbidden, bare)

	def test_the_house_rules_hold_for_the_new_blocks(self):
		for block, prefix in ((self.pp_block, "p6e-"), (self.mp_block, "mp-p6e-")):
			style = block[block.index("_STYLE = `") : block.index("`;", block.index("_STYLE = `"))]
			classes = set(re.findall(r"\.([a-zA-Z][\w-]*)", style))
			self.assertTrue(classes and all(c.startswith(prefix) for c in classes), classes)
			for british in ("colour", "cancelled", "labour", "centre", "behaviour"):
				self.assertNotIn(british, block.lower(), british)
			raw = [
				line.strip()
				for line in block.splitlines()
				if "<" in line and re.search(r"\$\{(?:found|card|item|data|visit|result|answer)\.\w+", line)
			]
			self.assertEqual(raw, [])
		# What a site's data puts on screen is escaped.
		self.assertIn("mp_esc(found.contract)", self.mp_block)
		self.assertIn("mp_esc(found.open_visit)", self.mp_block)
		self.assertNotIn("\r", self.pp)
		self.assertNotIn("\r", self.mp)


# ====================================================================== 2. day notes send nothing alone


class _DigestCase(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		t6d.setUpModule()

	@classmethod
	def tearDownClass(cls):
		t6d.tearDownModule()

	def setUp(self):
		t6d._reset()
		t6d._day_note("PDN-1", t6d.MON, "Shop meeting 7 am")
		t6d._block("PBLK-1", "RES-1", t6d.MON, note=t6d.NOTE, all_day=0, start="14:00", end="16:00")


class TestDigestsNeverSendOnANoteAlone(_DigestCase):
	def run_combined(self, entries, runs=2):
		"""``send_daily_digests`` for Lisa (RES-3) over ``entries`` (her day entry), run ``runs`` times;
		what it sent."""
		t6d.frappe.session.user = "Administrator"
		person = {"name": "RES-3", "resource_name": "Lisa Park", "user": t6d.LISA}
		sends = []

		def people_days(start, end, resources=None):
			data = {"resources": [{"name": "RES-3", "label": "Lisa Park"}]}
			return data, {"RES-3": [dict(entries, date=str(t6d.MON), off=None)]}

		with (
			mock.patch.object(t6d.digest.notices, "setting", lambda field: 1),
			mock.patch.object(t6d.digest, "_people", lambda: [person]),
			mock.patch.object(t6d.pp, "_people_days", people_days),
			mock.patch.object(
				t6d.digest,
				"_send",
				lambda user, resource, label, day, lines: sends.append(
					(user, [line["what"] for line in lines])
				)
				or ["email"],
			),
			mock.patch.object(t6d.digest, "nowdate", lambda: str(t6d.MON)),
		):
			for _i in range(runs):
				t6d.digest.send_daily_digests()
		return sends

	def test_a_day_with_only_a_note_sends_nothing_and_claims_nothing(self):
		t6d.frappe.tables["Planner Block"] = []
		sends = self.run_combined({"items": []})
		self.assertEqual(sends, [])
		# No claim either: switching the digest on mid-morning, or a booking arriving, still reaches her.
		self.assertFalse(t6d.frappe.docs.get(("Planner Digest Log", f"{t6d.LISA}|{t6d.MON}")))

	def test_the_note_is_added_to_a_day_with_a_booking_and_it_is_still_once_a_day(self):
		t6d.frappe.tables["Planner Block"] = []
		sends = self.run_combined({"items": [{"label": "Pump set", "time": "09:00", "hours": 3}]})
		self.assertEqual(sends, [(t6d.LISA, ["Note: Shop meeting 7 am", "Pump set"])])
		self.assertTrue(t6d.frappe.docs.get(("Planner Digest Log", f"{t6d.LISA}|{t6d.MON}")))

	def test_a_travel_day_counts_as_a_booking(self):
		t6d.frappe.tables["Planner Block"] = []
		sends = self.run_combined({"items": [], "travel": "Travel: Vegas"})
		self.assertEqual(sends, [(t6d.LISA, ["Note: Shop meeting 7 am", "Travel: Vegas"])])

	def test_her_own_block_rides_along_but_never_sends_by_itself(self):
		t6d.frappe.tables["Planner Day Note"] = []
		t6d.frappe.tables["Planner Block"] = []
		t6d._block("PBLK-2", "RES-3", t6d.MON, note="Dentist", all_day=0, start="14:00", end="16:00")
		self.assertEqual(self.run_combined({"items": []}), [])
		sends = self.run_combined({"items": [{"label": "Pump set", "time": "09:00", "hours": 3}]})
		self.assertEqual(len(sends), 1)
		self.assertIn("Unavailable 2–4 pm: Dentist", sends[0][1])

	def test_the_lines_are_empty_without_work(self):
		digest = t6d.digest
		note = {"text": "Note: Shop meeting 7 am"}
		block = {"text": "Unavailable 2–4 pm: Dentist"}
		self.assertEqual(digest.digest_lines({"day_notes": [note], "blocks": [block]}), [])
		self.assertEqual(digest.digest_lines({}), [])
		work = digest.digest_lines({"day_notes": [note], "blocks": [block], "items": [{"label": "Pump set"}]})
		self.assertEqual([line["what"] for line in work], [note["text"], block["text"], "Pump set"])

	def test_the_preview_says_so_for_a_day_with_only_a_note(self):
		t6d.frappe.session.user = "Administrator"

		def people_days(start, end, resources=None):
			data = {"resources": [{"name": "RES-3", "label": "Lisa Park"}]}
			return data, {"RES-3": [{"date": str(t6d.MON), "items": [], "off": None}]}

		with (
			mock.patch.object(t6d.digest.notices, "resource_of", lambda user: "RES-3"),
			mock.patch.object(t6d.pp, "_people_days", people_days),
		):
			answer = t6d.digest.send_preview(t6d.LISA, t6d.MON)
		self.assertFalse(answer["sent"])
		self.assertEqual(t6d.frappe.sent, [])

	def test_the_claim_code_is_untouched(self):
		source = (APP / "project_enhancements/planner_digest.py").read_text(encoding="utf-8")
		claim = source[source.index("def _claim(") : source.index("def send_daily_digests(")]
		self.assertIn('key = f"{user}|{day}"', claim)
		self.assertIn("frappe.db.exists(LOG_DOCTYPE, key)", claim)
		self.assertIn("frappe.db.commit()", claim)
		send = source[source.index("def send_daily_digests(") : source.index("def _send(")]
		self.assertLess(send.index("if not lines:"), send.index("_claim(user, day, len(lines))"))


class TestTheOlderDigestsNeverSendOnANoteEither(_DigestCase):
	def test_a_technician_with_only_a_note_is_not_messaged(self):
		dispatch, frappe = t6d.dispatch, t6d.frappe
		frappe.db.get_single_value = lambda *a, **k: 1
		frappe.tables["Employee"] = [{"name": "EMP-1", "user_id": t6d.AUSTIN, "cell_number": "555-0100"}]
		with mock.patch.object(dispatch, "nowdate", lambda: str(t6d.MON)):
			dispatch.send_morning_digests()
		self.assertEqual(t6d.telephony.sent, [])
		self.assertEqual(frappe.sent, [])
		self.assertEqual(frappe.errors, [])

	def test_a_technician_with_a_visit_gets_the_note_in_the_one_message(self):
		dispatch, frappe = t6d.dispatch, t6d.frappe
		frappe.db.get_single_value = lambda *a, **k: 1
		frappe.tables["Project"] = [{"name": "PRJ-2", "project_name": "Highlands"}]
		frappe.tables["Employee"] = [{"name": "EMP-1", "user_id": t6d.AUSTIN, "cell_number": "555-0100"}]
		frappe.tables["Sapphire Maintenance Record"] = [
			{
				"name": "SMR-1",
				"technician": t6d.AUSTIN,
				"project": "PRJ-2",
				"customer": "HOA",
				"serial_no": None,
				"visit_label": None,
				"dispatch_digest_sent_on": None,
				"scheduled_visit_date": t6d.MON,
				"docstatus": 0,
			}
		]
		with mock.patch.object(dispatch, "nowdate", lambda: str(t6d.MON)):
			dispatch.send_morning_digests()
		((number, text),) = t6d.telephony.sent
		self.assertEqual(number, "555-0100")
		self.assertIn("1 maintenance visit(s) today", text)
		self.assertIn("Note: Shop meeting 7 am", text)
		self.assertIn("Unavailable 2–4 pm: " + t6d.NOTE, text)
		self.assertEqual(len(frappe.sent), 1)
		self.assertIn("<li>Note: Shop meeting 7 am</li>", frappe.sent[0]["message"])

	def _rental(self, tasks):
		rental, frappe = t6d.rental, t6d.frappe
		real = frappe.get_all
		frappe.get_all = lambda doctype, *a, **k: list(tasks) if doctype == "Task" else real(doctype, *a, **k)
		frappe.tables["Employee"] = [{"name": "EMP-8", "user_id": t6d.AUSTIN, "cell_number": "555-0199"}]
		frappe.tables["ToDo"] = [
			{
				"reference_type": "Task",
				"reference_name": "TASK-R1",
				"status": "Open",
				"allocated_to": t6d.AUSTIN,
			}
		]
		with (
			mock.patch.object(rental, "settings", lambda: {"crew_digest": 1}),
			mock.patch.object(rental, "nowdate", lambda: str(t6d.MON)),
		):
			rental.send_crew_digests()

	def test_a_rental_crew_member_with_only_a_note_is_not_messaged(self):
		self._rental([])
		self.assertEqual(t6d.telephony.sent, [])
		self.assertEqual(t6d.frappe.sent, [])

	def test_a_rental_crew_member_with_a_job_gets_the_note_in_the_one_message(self):
		task = t6d._Doc(
			{
				"name": "TASK-R1",
				"subject": "Setup: Smith",
				"custom_rental_booking": "RB-1",
				"custom_rental_task_kind": "Setup",
			}
		)
		self._rental([task])
		((number, text),) = t6d.telephony.sent
		self.assertIn("1 rental job(s) today", text)
		self.assertIn("Note: Shop meeting 7 am", text)
		self.assertEqual(len(t6d.frappe.sent), 1)

	def test_the_older_digests_send_only_from_the_work_they_find(self):
		# The structural reason: the note lines are fetched inside the per-person send, after the
		# visits (or jobs) that make it worth sending.
		dispatch_source = (APP / "api/maintenance_dispatch.py").read_text(encoding="utf-8")
		send = dispatch_source[dispatch_source.index("def _send_tech_digest(") :]
		self.assertIn("notes = _planner_notes(technician, today)", send)
		loop = dispatch_source[
			dispatch_source.index("def send_morning_digests(") : dispatch_source.index(
				"def _combined_digest_covers("
			)
		]
		self.assertIn("for person, entries in by_person.items():", loop)
		self.assertNotIn("_planner_notes", loop)
		rental_source = (APP / "asset_management/rental_logistics.py").read_text(encoding="utf-8")
		crew = rental_source[
			rental_source.index("def send_crew_digests(") : rental_source.index(
				"def _combined_digest_covers("
			)
		]
		self.assertIn("for user, items in by_user.items():", crew)
		self.assertNotIn("_planner_notes", crew)


# ====================================================================== 3. a split moves its dependents


class TestSplitDependents(t6b._Writes):
	@classmethod
	def setUpClass(cls):
		t6b.setUpModule()

	@classmethod
	def tearDownClass(cls):
		t6b.tearDownModule()

	def setUp(self):
		super().setUp()
		frappe = t6b.frappe
		frappe.tables["Task Depends On"] = []
		frappe.tables["Task"] = []
		self.row_counter = 0
		self.db_deleted = []
		self.db_values = []

		def set_value(doctype, name, field, value=None, **kwargs):
			updates = field if isinstance(field, dict) else {field: value}
			self.db_values.append((doctype, name, dict(updates), kwargs))
			frappe.events.append(("set_value", doctype, name))
			for row in frappe.tables.get(doctype, []):
				if row.get("name") == name:
					row.update(updates)

		def delete(doctype, filters):
			self.db_deleted.append((doctype, filters))
			frappe.events.append(("db_delete", doctype, filters.get("name")))
			frappe.tables[doctype] = [
				r for r in frappe.tables.get(doctype, []) if r.get("name") != filters.get("name")
			]

		frappe.db.set_value = set_value
		frappe.db.delete = delete

	def dependent(self, name, *waits_on, project="PRJ-1", status="Open", **values):
		"""A Task (table row and document) that waits on ``waits_on``."""
		frappe = t6b.frappe
		frappe.tables["Task"].append(
			{
				"name": name,
				"project": project,
				"status": status,
				"is_template": 0,
				"subject": f"Subject {name}",
			}
		)
		rows = []
		for task in waits_on:
			self.row_counter += 1
			row = {
				"name": f"TDO-{self.row_counter}",
				"parenttype": "Task",
				"parent": name,
				"task": task,
				"idx": len(rows) + 1,
			}
			frappe.tables["Task Depends On"].append(row)
			rows.append(row)
		return t6b._task(name, project=project, status=status, **values), rows

	def waits_on(self, name):
		return [r["task"] for r in t6b.frappe.tables["Task Depends On"] if r["parent"] == name]

	def split(self, **kwargs):
		t6b._task()  # TASK-1: Mon-Thu, 16h
		return t6b.actions.split_task("TASK-1", str(t6b.WED), t6b.MODIFIED, **kwargs)

	def test_dependents_wait_on_the_second_part(self):
		self.dependent("TASK-9", "TASK-1")
		self.dependent("TASK-10", "TASK-1", status="Working")
		result = self.split()
		self.assertEqual(result["repointed"], ["TASK-10", "TASK-9"])
		self.assertEqual(self.waits_on("TASK-9"), ["TASK-NEW-1"])
		self.assertEqual(self.waits_on("TASK-10"), ["TASK-NEW-1"])
		# The second part still waits on the first; nothing else changed.
		(second,) = self._inserted_tasks()
		self.assertEqual(second["depends_on"], [{"task": "TASK-1"}])
		# The parent's text column ERPNext keeps beside the table follows the table.
		texts = {n: v["depends_on_tasks"] for _dt, n, v, _k in self.db_values if "depends_on_tasks" in v}
		self.assertEqual(texts, {"TASK-9": "TASK-NEW-1,", "TASK-10": "TASK-NEW-1,"})

	def test_both_parts_keep_the_same_name(self):
		result = self.split()
		(second,) = self._inserted_tasks()
		self.assertEqual(second["subject"], "Subject TASK-1")
		self.assertEqual(t6b.frappe.saved[0]["subject"], "Subject TASK-1")
		self.assertNotIn("part 2", second["subject"].lower())
		self.assertEqual(result["repointed"], [])
		self.assertEqual(self.db_values, [])

	def test_a_task_with_other_dependencies_keeps_them_and_never_gets_a_duplicate_row(self):
		self.dependent("TASK-9", "TASK-7", "TASK-1", "TASK-8")
		self.dependent("TASK-10", "TASK-1", "TASK-1")  # listed twice by someone
		self.split()
		self.assertEqual(self.waits_on("TASK-9"), ["TASK-7", "TASK-NEW-1", "TASK-8"])
		self.assertEqual(self.waits_on("TASK-10"), ["TASK-NEW-1"])
		for name in ("TASK-9", "TASK-10"):
			waiting = self.waits_on(name)
			self.assertEqual(len(waiting), len(set(waiting)), name)
		texts = {n: v["depends_on_tasks"] for _dt, n, v, _k in self.db_values if "depends_on_tasks" in v}
		self.assertEqual(texts["TASK-9"], "TASK-7,TASK-NEW-1,TASK-8,")

	def test_swapping_onto_a_task_that_is_already_waited_on_drops_the_old_row(self):
		self.dependent("TASK-9", "TASK-1", "TASK-NEW-1")
		changed = t6b.actions._swap_dependency(["TASK-9"], "TASK-1", "TASK-NEW-1", "S", "note")
		self.assertEqual(changed, ["TASK-9"])
		self.assertEqual(self.waits_on("TASK-9"), ["TASK-NEW-1"])

	def test_another_projects_finished_and_template_tasks_are_untouched(self):
		self.dependent("TASK-OTHER", "TASK-1", project="PRJ-2")
		self.dependent("TASK-DONE", "TASK-1", status="Completed")
		self.dependent("TASK-OFF", "TASK-1", status="Canceled")
		self.dependent("TASK-TPL", "TASK-1", status="Template")
		self.dependent("TASK-OPEN", "TASK-1")
		result = self.split()
		self.assertEqual(result["repointed"], ["TASK-OPEN"])
		for name in ("TASK-OTHER", "TASK-DONE", "TASK-OFF", "TASK-TPL"):
			self.assertEqual(self.waits_on(name), ["TASK-1"], name)
		noted = {n for n, _kind, text in t6b.frappe.comments if "Now waits on" in text}
		self.assertEqual(noted, {"TASK-OPEN"})

	def test_each_moved_task_gets_a_timeline_note(self):
		self.dependent("TASK-9", "TASK-1")
		self.split()
		notes = [(n, text) for n, _kind, text in t6b.frappe.comments if n == "TASK-9"]
		self.assertEqual(notes, [("TASK-9", "Now waits on TASK-NEW-1 (second part of a split)")])
		# The second part's timeline names who moved to it.
		self.assertTrue(
			any(
				n == "TASK-NEW-1" and "TASK-9" in text and "now wait on this one" in text
				for n, _k, text in t6b.frappe.comments
			)
		)

	def test_no_dates_move_because_nothing_is_saved_through_task_save(self):
		# ERPNext's Task.on_update -> reschedule_dependent_tasks runs on a save; the dependents are
		# written straight to the child table, so only the split's own two tasks are ever saved.
		self.dependent(
			"TASK-9", "TASK-1", exp_start_date="2026-10-16 08:00:00", exp_end_date="2026-10-16 17:00:00"
		)
		self.split()
		self.assertEqual([saved["name"] for saved in t6b.frappe.saved], ["TASK-1"])
		self.assertNotIn(("save", "TASK-9"), t6b.frappe.events)
		self.assertEqual(t6b.frappe.docs[("Task", "TASK-9")]["exp_start_date"], "2026-10-16 08:00:00")

	def test_it_is_all_one_savepoint_and_a_failure_rolls_the_whole_split_back(self):
		self.dependent("TASK-9", "TASK-1")
		denied = {"TASK-9"}
		t6b.frappe.has_permission = lambda doctype=None, ptype="read", doc=None, *a, **k: not (
			ptype == "write" and doc in denied
		)
		with self.assertRaises(t6b._PermissionError) as caught:
			self.split()
		self.assertIn("TASK-9", str(caught.exception))
		self.assertIn(("rollback", "planner_split"), t6b.frappe.events)
		self.assertEqual(self.waits_on("TASK-9"), ["TASK-1"])
		self.assertEqual(self.db_values, [])
		# The savepoint opened before the first save, and the repoint happens inside it.
		events = t6b.frappe.events
		self.assertLess(events.index(("savepoint", "planner_split")), events.index(("save", "TASK-1")))

	def test_the_repoint_runs_after_the_second_part_exists_and_before_the_reply(self):
		self.dependent("TASK-9", "TASK-1")
		self.split()
		events = t6b.frappe.events
		self.assertLess(
			events.index(("insert", "Task", "TASK-NEW-1")),
			events.index(("set_value", "Task Depends On", "TDO-1")),
		)

	# ---------------------------------------------------------------- the Undo

	def undo_setup(self, dependents=("TASK-9",), extra=()):
		"""The state after a split: the second half (TASK-NEW-1) waits on TASK-1 and the moved tasks wait on it."""
		t6b._task(
			"TASK-NEW-1", owner="nik@example.com", modified=t6b.SAVED, depends_on=[t6b._Doc(task="TASK-1")]
		)
		t6b._task(
			"TASK-1",
			start="2026-10-12 08:00:00",
			end="2026-10-13 17:00:00",
			expected_time=8,
			modified=t6b.SAVED,
		)
		for name in dependents:
			self.dependent(name, "TASK-NEW-1")
		for name in extra:
			self.dependent(name, "TASK-NEW-1")

	def restore(self, **values):
		plan = {
			"task": "TASK-1",
			"modified": t6b.SAVED,
			"start": "2026-10-12",
			"end": "2026-10-15",
			"expected_time": 16,
			"crew": [{"resource": "RES-1", "hours": None, "is_lead": 1}],
		}
		plan.update(values)
		return json.dumps(plan)

	def test_the_undo_puts_the_dependents_back_before_it_deletes(self):
		self.undo_setup(dependents=("TASK-9", "TASK-10"))
		result = t6b.actions.remove_created_task(
			"TASK-NEW-1", t6b.SAVED, restore=self.restore(dependents=["TASK-9", "TASK-10"])
		)
		self.assertEqual(self.waits_on("TASK-9"), ["TASK-1"])
		self.assertEqual(self.waits_on("TASK-10"), ["TASK-1"])
		self.assertEqual(result["restored_dependents"], ["TASK-10", "TASK-9"])
		events = t6b.frappe.events
		self.assertLess(
			events.index(("set_value", "Task Depends On", "TDO-1")), events.index(("delete", "TASK-NEW-1"))
		)
		self.assertLess(events.index(("delete", "TASK-NEW-1")), events.index(("save", "TASK-1")))
		((doctype, name, kwargs),) = t6b.frappe.deleted
		self.assertEqual((doctype, name), ("Task", "TASK-NEW-1"))
		self.assertFalse(kwargs.get("force"))
		self.assertTrue(
			any("Waits on TASK-1 again" in text for n, _k, text in t6b.frappe.comments if n == "TASK-9")
		)

	def test_the_dependents_may_come_as_a_json_string(self):
		self.undo_setup()
		t6b.actions.remove_created_task(
			"TASK-NEW-1", t6b.SAVED, restore=self.restore(dependents='["TASK-9"]')
		)
		self.assertEqual(self.waits_on("TASK-9"), ["TASK-1"])

	def test_a_dependent_added_since_still_stops_the_undo(self):
		self.undo_setup(dependents=("TASK-9",), extra=("TASK-NEWER",))
		with self.assertRaises(t6b._Throw) as caught:
			t6b.actions.remove_created_task(
				"TASK-NEW-1", t6b.SAVED, restore=self.restore(dependents=["TASK-9"])
			)
		self.assertIn("another task depends on it", str(caught.exception))
		# Nothing at all happened: the moved dependent is still on the second half, nothing deleted or saved.
		self.assertEqual(self.waits_on("TASK-9"), ["TASK-NEW-1"])
		self.assertEqual(t6b.frappe.deleted, [])
		self.assertEqual(t6b.frappe.saved, [])
		self.assertEqual(self.db_values, [])

	def test_without_a_restore_plan_any_dependent_stops_the_undo(self):
		# A duplicate or a quick add has no moved dependents: Phase 6B's rule stands.
		self.undo_setup(dependents=("TASK-9",))
		with self.assertRaises(t6b._Throw):
			t6b.actions.remove_created_task("TASK-NEW-1", t6b.SAVED)
		with self.assertRaises(t6b._Throw):
			t6b.actions.remove_created_task("TASK-NEW-1", t6b.SAVED, restore=self.restore())
		self.assertEqual(t6b.frappe.deleted, [])

	def test_a_listed_task_that_no_longer_waits_is_left_alone(self):
		self.undo_setup(dependents=())
		self.dependent("TASK-9", "TASK-7")  # was re-pointed, then edited to wait on something else
		result = t6b.actions.remove_created_task(
			"TASK-NEW-1", t6b.SAVED, restore=self.restore(dependents=["TASK-9"])
		)
		self.assertEqual(result["restored_dependents"], [])
		self.assertEqual(self.waits_on("TASK-9"), ["TASK-7"])

	def test_a_denied_dependent_fails_the_undo_before_anything_is_deleted(self):
		self.undo_setup()
		t6b.frappe.has_permission = lambda doctype=None, ptype="read", doc=None, *a, **k: not (
			ptype == "write" and doc == "TASK-9"
		)
		with self.assertRaises(t6b._PermissionError):
			t6b.actions.remove_created_task(
				"TASK-NEW-1", t6b.SAVED, restore=self.restore(dependents=["TASK-9"])
			)
		self.assertEqual(t6b.frappe.deleted, [])

	def test_split_then_undo_is_a_round_trip(self):
		self.dependent("TASK-9", "TASK-1")
		result = self.split()
		self.assertEqual(self.waits_on("TASK-9"), ["TASK-NEW-1"])
		second = t6b.frappe.docs[("Task", "TASK-NEW-1")]
		restore = {
			"task": "TASK-1",
			"modified": result["modified"],
			"start": "2026-10-12",
			"end": "2026-10-15",
			"expected_time": 16,
			"crew": [],
			"dependents": result["repointed"],
		}
		t6b.actions.remove_created_task(
			"TASK-NEW-1", result["second"]["modified"], restore=json.dumps(restore)
		)
		self.assertEqual(self.waits_on("TASK-9"), ["TASK-1"])
		self.assertEqual(second["name"], "TASK-NEW-1")
		self.assertEqual(len(t6b.frappe.deleted), 1)

	def test_the_module_uses_no_force_and_the_split_still_documents_its_answer(self):
		source = PLANNER_ACTIONS_PY.read_text(encoding="utf-8")
		self.assertNotIn("force=True", source)
		self.assertIn('"repointed": repointed', source)
		self.assertIn("reschedule_dependent_tasks", source)
		swap = source[source.index("def _swap_dependency(") : source.index("def _repoint_dependents(")]
		code = swap.split('"""')[2]  # the docstring explains why, and names Task.save()
		self.assertNotIn(".save(", code)
		self.assertNotIn("pp._save", code)


# ====================================================================== 4. Add visit here (the server)


class _Visit(t6d._Doc):
	"""A Sapphire Maintenance Record as far as the endpoints use one: child rows, insert, comments."""

	def append(self, field, row):
		self.setdefault(field, []).append(t6d._Doc(row))

	def set(self, field, value):
		self[field] = [t6d._Doc(row) for row in value] if isinstance(value, list) else value


class _VisitCase(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		t6d.setUpModule()
		utils = t6d.frappe.utils
		utils.add_months = lambda value, months: value
		utils.add_years = lambda value, years: value
		utils.get_weekday = lambda value=None: "Monday"
		t6d.frappe.logger = lambda *a, **k: types.SimpleNamespace(info=lambda *args, **kw: None)
		cls.actions = importlib.import_module("erpnext_enhancements.api.maintenance_actions")
		cls.tasks = importlib.import_module("erpnext_enhancements.tasks")

	@classmethod
	def tearDownClass(cls):
		t6d.tearDownModule()

	def setUp(self):
		frappe = t6d.frappe
		t6d._reset()
		frappe.session.user = t6d.PM
		frappe.tables["Project"] = [
			{"name": "PRJ-2", "project_name": "Highlands Maintenance Contract", "customer": "HOA"},
			{"name": "PRJ-3", "project_name": "Lodge", "customer": "LODGE"},
			{"name": "PRJ-4", "project_name": "Unprofiled", "customer": "NOPE"},
		]
		frappe.tables["User"] = [
			*frappe.tables["User"],
			{"name": "off@example.com", "full_name": "Off Duty", "enabled": 0},
		]
		frappe.tables["Sapphire Maintenance Profile"] = [
			{
				"name": "PROF-2",
				"project": "PRJ-2",
				"customer": "HOA",
				"default_technician": t6d.AUSTIN,
				"visit_hours": 4,
				"visit_full_day": 0,
			},
			{
				"name": "PROF-3",
				"project": "PRJ-3",
				"customer": "LODGE",
				"default_technician": None,
				"visit_hours": 0,
				"visit_full_day": 1,
			},
		]
		frappe.tables["Sapphire Visit Crew Member"] = [
			{
				"parenttype": "Sapphire Maintenance Profile",
				"parent": "PROF-2",
				"user": t6d.KORBEN,
				"hours": 2,
				"idx": 1,
			}
		]
		frappe.tables["Sapphire Maintenance Template"] = [{"name": "TPL-1"}, {"name": "TPL-2"}]
		self.contract = t6d._Doc(
			{
				"name": "MNT-CON-1",
				"project": "PRJ-2",
				"customer": "HOA",
				"status": "Active",
				"visit_shape": "Per Feature",
				"project_contract": None,
				"covered_features": [
					t6d._Doc({"serial_no": "S-1", "next_visit_date": D(2026, 11, 2), "template": "TPL-1"}),
					t6d._Doc({"serial_no": "S-2", "next_visit_date": D(2026, 10, 20), "template": "TPL-2"}),
				],
			}
		)
		self.active = {"PRJ-2": self.contract}
		self.templates = {None: "TPL-D", "S-1": "TPL-1", "S-2": "TPL-2"}
		self.assigned = []
		self.conflict_calls = []
		self.conflicting = {}
		self.deleted = []
		self.created_docs = []

		def new_doc(doctype):
			doc = _Visit({"doctype": doctype, "__islocal": 1, "name": None, "crew": []})
			self.created_docs.append(doc)
			return doc

		def visit_conflicts(user, day, ref, label=None, hours=None, full_day=False):
			self.conflict_calls.append((user, str(day), ref, hours, full_day))
			return (
				{t6d.frappe.person_label(user): list(self.conflicting[user])}
				if user in self.conflicting
				else {}
			)

		frappe.person_label = lambda user: {t6d.AUSTIN: "Austin", t6d.KORBEN: "Korben"}.get(user, user)
		frappe.new_doc = new_doc
		frappe.delete_doc = lambda doctype, name, **kwargs: (
			self.deleted.append((doctype, name, kwargs)),
			frappe.docs.pop((doctype, name), None),
		)
		self.patches = [
			mock.patch.object(self.actions, "_active_contract", lambda project: self.active.get(project)),
			mock.patch.object(
				self.actions,
				"_template_for",
				lambda project, customer, contract, row=None: self.templates.get(
					row.get("serial_no") if row else None
				),
			),
			mock.patch.object(t6d.engine, "planner_projects", lambda names: set(names) - {"PRJ-INTERNAL"}),
			mock.patch.object(t6d.mp, "visit_conflicts", visit_conflicts),
			mock.patch.object(
				t6d.dispatch,
				"assign_to_technician",
				lambda record, user, description=None: self.assigned.append((record, user)),
			),
		]
		for patch in self.patches:
			patch.start()

	def tearDown(self):
		for patch in self.patches:
			patch.stop()

	def create(self, **kwargs):
		args = {"project": "PRJ-2", "date": str(t6d.FRI)}
		args.update(kwargs)
		return self.actions.create_visit(**args)

	def inserted(self):
		(doc,) = [d for d in self.created_docs if d.get("name")]
		return doc


D = datetime.date


class TestCreateVisit(_VisitCase):
	def test_with_an_active_contract_the_visit_is_linked_to_it_like_a_scheduled_one(self):
		result = self.create(serial_no="S-1")
		doc = self.inserted()
		self.assertEqual(doc["maintenance_contract"], "MNT-CON-1")
		self.assertEqual((doc["project"], doc["customer"], doc["serial_no"]), ("PRJ-2", "HOA", "S-1"))
		self.assertEqual(str(doc["scheduled_visit_date"]), str(t6d.FRI))
		self.assertTrue(doc.get("visit_label") in (None, ""))  # a regular visit, not a labelled one
		self.assertTrue(result["created"])
		self.assertEqual((result["contract"], result["serial_no"]), ("MNT-CON-1", "S-1"))
		self.assertEqual(result["warnings"], [])

	def test_with_no_contract_the_visit_is_plain(self):
		self.active = {}
		result = self.create(serial_no="S-9")
		doc = self.inserted()
		self.assertFalse(doc.get("maintenance_contract"))
		self.assertEqual((doc["project"], doc["customer"], doc["serial_no"]), ("PRJ-2", "HOA", "S-9"))
		self.assertIsNone(result["contract"])

	def test_a_fountain_the_contract_does_not_cover_is_not_linked_and_the_answer_says_so(self):
		result = self.create(serial_no="S-9")
		doc = self.inserted()
		self.assertFalse(doc.get("maintenance_contract"))
		self.assertEqual(doc["serial_no"], "S-9")
		self.assertEqual(len(result["warnings"]), 1)
		self.assertIn("S-9", result["warnings"][0])
		self.assertIn("MNT-CON-1", result["warnings"][0])

	def test_a_whole_site_visit_has_no_fountain_and_is_linked(self):
		self.contract["visit_shape"] = "Per Site Visit"
		self.create()
		doc = self.inserted()
		self.assertEqual(doc["maintenance_contract"], "MNT-CON-1")
		self.assertFalse(doc.get("serial_no"))

	def test_the_defaults_are_the_schedulers_own(self):
		# The site's default technician, crew and length from its Maintenance Profile, copied by the
		# scheduler's builder; nothing the page sent.
		self.create()
		doc = self.inserted()
		self.assertEqual(doc["technician"], t6d.AUSTIN)
		self.assertEqual([(r["user"], r["hours"]) for r in doc["crew"]], [(t6d.KORBEN, 2)])
		self.assertEqual(doc["planned_hours"], 4)
		self.assertFalse(doc.get("full_day"))
		self.assertEqual(self.assigned, [("DOC-00001", t6d.AUSTIN), ("DOC-00001", t6d.KORBEN)])

	def test_a_full_day_site_default_carries_over(self):
		self.create(project="PRJ-3")
		doc = self.inserted()
		self.assertEqual(doc["full_day"], 1)
		self.assertFalse(doc.get("technician"))

	def test_the_planner_can_override_who_and_for_how_long(self):
		self.create(
			technician=t6d.KORBEN,
			crew=json.dumps([{"user": t6d.AUSTIN, "hours": 1.5}]),
			hours=3,
			full_day=None,
			template="TPL-2",
		)
		doc = self.inserted()
		self.assertEqual(doc["technician"], t6d.KORBEN)
		self.assertEqual([(r["user"], r["hours"]) for r in doc["crew"]], [(t6d.AUSTIN, 1.5)])
		self.assertEqual((doc["planned_hours"], doc.get("full_day")), (3, 0))
		self.assertEqual(doc["template"], "TPL-2")

	def test_a_blank_technician_means_nobody_and_none_means_the_site_default(self):
		self.create(technician="")
		self.assertFalse(self.inserted().get("technician"))

	def test_the_technician_is_never_also_crew(self):
		# Handing the visit to someone on the site's default crew moves them out of the crew.
		self.create(technician=t6d.KORBEN)
		doc = self.inserted()
		self.assertEqual(doc["technician"], t6d.KORBEN)
		self.assertEqual([r["user"] for r in doc["crew"]], [])

	def test_zero_hours_is_the_settings_length_and_full_day_can_be_set(self):
		self.create(hours=0, full_day=1)
		doc = self.inserted()
		self.assertEqual((doc["planned_hours"], doc["full_day"]), (0, 1))

	def test_it_uses_the_schedulers_helpers_and_does_not_copy_them(self):
		source = ACTIONS_PY.read_text(encoding="utf-8")
		body = source[
			source.index("def create_visit(") : source.index(
				"# ---------------------------------------------------------------------- undo"
			)
		]
		self.assertIn("tasks._new_maintenance_record(", body)
		self.assertIn("tasks._assign_visit_people(record)", body)
		for copied in (
			"_apply_default_crew(",
			"default_technician_for(",
			"default_crew_for(",
			"frappe.new_doc(",
		):
			self.assertNotIn(copied, body, copied)
		tasks_source = TASKS_PY.read_text(encoding="utf-8")
		builder = tasks_source[
			tasks_source.index("def _new_maintenance_record(") : tasks_source.index(
				"def _draft_maintenance_record("
			)
		]
		self.assertIn("_apply_default_crew(record, project)", builder)
		self.assertIn("default_technician_for(project)", builder)
		drafted = tasks_source[
			tasks_source.index("def _draft_maintenance_record(") : tasks_source.index(
				"def predictive_maintenance_scheduling("
			)
		]
		self.assertIn("record = _new_maintenance_record(", drafted)
		self.assertIn("record.insert(ignore_permissions=True)", drafted)
		self.assertIn("_assign_visit_people(record)", drafted)

	def test_the_scheduler_still_drafts_exactly_what_it_did(self):
		contract = t6d._Doc(
			{"name": "MNT-CON-1", "project": "PRJ-2", "customer": "HOA", "project_contract": None}
		)
		inserts = []
		original = _Visit.insert
		_Visit.insert = lambda self, **kwargs: (inserts.append(kwargs), original(self, **kwargs))[1]
		try:
			record = self.tasks._draft_maintenance_record(
				contract, serial_no="S-1", scheduled_date=D(2026, 10, 12), exact_date=True
			)
		finally:
			_Visit.insert = original
		self.assertEqual(inserts, [{"ignore_permissions": True}])
		self.assertEqual(record["maintenance_contract"], "MNT-CON-1")
		self.assertEqual(
			(record["customer"], record["project"], record["serial_no"]), ("HOA", "PRJ-2", "S-1")
		)
		self.assertEqual(str(record["scheduled_visit_date"]), "2026-10-12")
		self.assertEqual(record["technician"], t6d.AUSTIN)
		self.assertEqual([r["user"] for r in record["crew"]], [t6d.KORBEN])
		self.assertEqual(self.assigned, [(record["name"], t6d.AUSTIN), (record["name"], t6d.KORBEN)])

	def test_overbooking_asks_for_a_reason_and_creates_nothing(self):
		self.conflicting = {t6d.AUSTIN: [f"{t6d.FRI}: Over by 2h"]}
		answer = self.create()
		self.assertEqual(answer, {"needs_reason": True, "conflicts": {"Austin": [f"{t6d.FRI}: Over by 2h"]}})
		self.assertEqual([d for d in self.created_docs if d.get("name")], [])
		self.assertEqual(t6d.frappe.inserted, [])
		# Everyone on the visit is checked, on its day, with the visit's own hours.
		users = [call[0] for call in self.conflict_calls]
		self.assertEqual(users, [t6d.AUSTIN, t6d.KORBEN])
		self.assertEqual({call[1] for call in self.conflict_calls}, {str(t6d.FRI)})
		self.assertEqual({call[2] for call in self.conflict_calls}, {self.actions.NEW_REF})

	def test_with_a_reason_it_saves_and_the_reason_is_on_the_timeline(self):
		self.conflicting = {t6d.KORBEN: [f"{t6d.FRI}: Booked on a day off (Time off)"]}
		result = self.create(reason="  Customer insisted  ")
		doc = self.inserted()
		self.assertEqual(result["conflicts"], {"Korben": [f"{t6d.FRI}: Booked on a day off (Time off)"]})
		texts = [text for _dt, name, _kind, text in t6d.frappe.comments if name == doc["name"]]
		self.assertTrue(
			any(
				"Scheduled over a conflict on the Maintenance Planner" in t and "Customer insisted" in t
				for t in texts
			)
		)
		self.assertTrue(any(t.startswith("Added on the Maintenance Planner") for t in texts))

	def test_the_conflict_check_is_move_visits_own(self):
		self.assertIn("mp._people_conflicts(", ACTIONS_PY.read_text(encoding="utf-8"))
		seen = []
		real = t6d.mp._people_conflicts
		with mock.patch.object(
			t6d.mp,
			"_people_conflicts",
			lambda doc, users: (seen.append((doc.name, list(users))), real(doc, users))[1],
		):
			self.create()
		self.assertEqual(seen, [(self.actions.NEW_REF, [t6d.AUSTIN, t6d.KORBEN])])

	def test_a_visit_nobody_works_on_has_no_conflict_to_check(self):
		self.create(project="PRJ-3")
		self.assertEqual(self.conflict_calls, [])

	def test_the_role_gate_comes_first(self):
		t6d.frappe.session.user = "stranger@example.com"
		with self.assertRaises(t6d._PermissionError):
			self.create()
		self.assertEqual(self.created_docs, [])
		# Technicians hold Maintenance User, which the planner admits; Administrator always.
		t6d.frappe.session.user = "tech-only@example.com"
		self.create()
		t6d.frappe.session.user = "Administrator"
		self.create(date=str(t6d.FRI))

	def test_creating_needs_create_permission_as_the_user(self):
		t6d.frappe.has_permission = lambda doctype=None, ptype="read", *a, **k: ptype != "create"
		with self.assertRaises(t6d._PermissionError):
			self.create()
		self.assertEqual(self.created_docs, [])
		# And the document itself is checked as the caller before it is inserted, never inserted as the system.
		t6d.frappe.has_permission = lambda *a, **k: True
		t6d.frappe.denied = {None}
		with self.assertRaises(t6d._PermissionError):
			self.create()
		self.assertEqual(t6d.frappe.inserted, [])
		body = ACTIONS_PY.read_text(encoding="utf-8")
		self.assertIn('record.check_permission("create")', body)
		self.assertNotIn("ignore_permissions=True", body)

	def test_refusals(self):
		cases = [
			({"project": ""}, "Pick the site"),
			({"project": "PRJ-404"}, "Pick the site"),
			({"project": "PRJ-INTERNAL"}, "not a customer job"),
			({"project": "PRJ-4"}, "no Maintenance Profile"),
			({"date": ""}, "Pick the day"),
			({"date": "2026-10-01"}, "Pick today or a later day"),
			({"technician": "off@example.com"}, "not an active user"),
			({"crew": json.dumps([t6d.LISA, "off@example.com"])}, "not an active user"),
			({"template": "TPL-404"}, "was not found"),
		]
		t6d.frappe.tables["Project"].append(
			{"name": "PRJ-INTERNAL", "project_name": "Shop", "customer": None}
		)
		for kwargs, text in cases:
			with self.assertRaises(t6d._Throw, msg=str(kwargs)) as caught:
				self.create(**kwargs)
			self.assertIn(text, str(caught.exception), kwargs)
		self.assertEqual([d for d in self.created_docs if d.get("name")], [])

	def test_today_is_allowed(self):
		self.create(date=str(t6d.TODAY))

	def test_the_answer_is_move_visits_shape_plus_created(self):
		result = self.create(serial_no="S-1")
		for key in (
			"name",
			"date",
			"technician",
			"crew",
			"planned_hours",
			"full_day",
			"modified",
			"warnings",
		):
			self.assertIn(key, result)
		self.assertEqual(result["date"], str(t6d.FRI))
		self.assertEqual(result["technician"], t6d.AUSTIN)
		self.assertEqual(result["crew"], [{"user": t6d.KORBEN, "name": t6d.KORBEN, "hours": 2.0}])

	def test_validate_warnings_come_back_as_text_rather_than_popping_up(self):
		original = _Visit.insert

		def insert(self, **kwargs):
			t6d.frappe.local.message_log.append(json.dumps({"message": "<b>Austin</b> is on time off"}))
			return original(self, **kwargs)

		_Visit.insert = insert
		try:
			result = self.create()
		finally:
			_Visit.insert = original
		self.assertEqual(result["warnings"], ["Austin is on time off"])
		self.assertEqual(t6d.frappe.local.message_log, [])

	def test_the_endpoints_are_posts_where_they_write(self):
		source = ACTIONS_PY.read_text(encoding="utf-8")
		for name in ("create_visit", "remove_created_visit"):
			self.assertRegex(source, rf'@frappe\.whitelist\(methods=\["POST"\]\)\ndef {name}\(')
		for name, args in VISIT_CONTRACT.items():
			function = getattr(self.actions, name)
			params = set(function.__code__.co_varnames[: function.__code__.co_argcount])
			self.assertTrue(args <= params, f"{name} lacks {sorted(args - params)}")


class TestVisitDefaultsAndSites(_VisitCase):
	def test_the_forms_defaults(self):
		answer = self.actions.get_visit_defaults("PRJ-2")
		self.assertEqual(answer["project"], "PRJ-2")
		self.assertEqual(answer["title"], "Highlands")
		self.assertEqual((answer["technician"], answer["hours"], answer["full_day"]), (t6d.AUSTIN, 4, False))
		self.assertEqual(answer["crew"], [{"user": t6d.KORBEN, "name": "Korben Fox", "hours": 2}])
		self.assertEqual((answer["contract"], answer["visit_shape"]), ("MNT-CON-1", "Per Feature"))
		# The fountain due soonest is the starting point; every covered fountain is on offer.
		self.assertEqual(answer["serial_no"], "S-2")
		self.assertEqual(answer["template"], "TPL-2")
		self.assertEqual([f["serial_no"] for f in answer["features"]], ["S-1", "S-2"])
		self.assertEqual(answer["features"][0]["template"], "TPL-1")
		self.assertIsNone(answer["open_visit"])

	def test_an_open_regular_visit_for_the_fountain_is_named(self):
		t6d.frappe.tables["Sapphire Maintenance Record"] = [
			{"name": "SMR-7", "project": "PRJ-2", "serial_no": "S-2", "visit_label": None, "docstatus": 0}
		]
		with mock.patch.object(
			t6d.frappe.db, "get_value", self._open_visit(t6d.frappe.db.get_value), create=True
		):
			answer = self.actions.get_visit_defaults("PRJ-2")
		self.assertEqual(answer["open_visit"], "SMR-7")

	@staticmethod
	def _open_visit(real):
		def get_value(doctype, name, field=None, *args, **kwargs):
			if doctype == "Sapphire Maintenance Record":
				return "SMR-7"
			return real(doctype, name, field, *args, **kwargs)

		return get_value

	def test_a_whole_site_contract_names_no_fountain(self):
		self.contract["visit_shape"] = "Per Site Visit"
		answer = self.actions.get_visit_defaults("PRJ-2")
		self.assertIsNone(answer["serial_no"])
		self.assertEqual(answer["template"], "TPL-D")

	def test_a_site_with_no_contract_is_plain(self):
		self.active = {}
		answer = self.actions.get_visit_defaults("PRJ-3")
		self.assertIsNone(answer["contract"])
		self.assertEqual(answer["features"], [])
		self.assertEqual((answer["full_day"], answer["technician"]), (True, None))

	def test_the_role_gate_and_the_profile_requirement(self):
		t6d.frappe.session.user = "stranger@example.com"
		with self.assertRaises(t6d._PermissionError):
			self.actions.get_visit_defaults("PRJ-2")
		t6d.frappe.session.user = t6d.PM
		with self.assertRaises(t6d._Throw):
			self.actions.get_visit_defaults("PRJ-4")

	def test_the_site_search_is_customer_jobs_with_a_profile_and_gated(self):
		captured = []
		with (
			mock.patch.object(
				t6d.engine, "planner_job_sql", lambda alias="p": (f"IFNULL({alias}.project_type, '')", "0")
			),
			mock.patch.object(
				t6d.frappe.db,
				"sql",
				lambda query, values=None, **k: (captured.append((query, values)), [("PRJ-2", "Highlands")])[
					1
				],
			),
		):
			rows = self.actions.sites_query("Project", "high_%", "name", 0, 500)
			t6d.frappe.session.user = "stranger@example.com"
			with self.assertRaises(t6d._PermissionError):
				self.actions.sites_query("Project", "x", "name", 0, 20)
		self.assertEqual(rows, [("PRJ-2", "Highlands")])
		((query, values),) = captured
		self.assertIn("tabSapphire Maintenance Profile", query)
		self.assertIn("proj.project_type", query)
		# The typed text is a bound parameter with its wildcards escaped, and the page is capped.
		self.assertEqual(values["like"], "%high\\_\\%%")
		self.assertEqual(values["page_len"], 50)
		self.assertEqual(values["planner_types"], t6d.engine.PLANNER_PROJECT_TYPES)


class TestRemoveCreatedVisit(_VisitCase):
	def setUp(self):
		super().setUp()
		self.created = self.create(serial_no="S-1")
		self.name = self.created["name"]
		self.doc = t6d.frappe.docs[("Sapphire Maintenance Record", self.name)]

	def remove(self, modified=None):
		return self.actions.remove_created_visit(self.name, modified or self.created["modified"])

	def test_an_untouched_draft_is_deleted_without_force(self):
		self.assertEqual(self.remove(), {"removed": self.name})
		((doctype, name, kwargs),) = self.deleted
		self.assertEqual((doctype, name), ("Sapphire Maintenance Record", self.name))
		self.assertFalse(kwargs.get("force"))
		self.assertFalse(kwargs.get("ignore_permissions"))

	def test_my_own_comments_do_not_stop_it(self):
		t6d.frappe.tables["Comment"] = [
			{
				"reference_doctype": "Sapphire Maintenance Record",
				"reference_name": self.name,
				"comment_type": "Comment",
				"owner": t6d.PM,
				"name": "C1",
			}
		]
		self.remove()
		self.assertEqual(len(self.deleted), 1)

	def test_anything_attached_since_stops_it(self):
		table = "Sapphire Maintenance Record"

		def child(child_table):
			return lambda doc, name: t6d.frappe.tables.update(
				{child_table: [{"parent": name, "parenttype": table}]}
			)

		def comment(doc, name):
			t6d.frappe.tables["Comment"] = [
				{
					"reference_doctype": table,
					"reference_name": name,
					"comment_type": "Comment",
					"owner": t6d.LISA,
					"name": "C2",
				}
			]

		setups = {
			"someone else's": lambda doc, name: doc.update(owner=t6d.LISA),
			"changed": lambda doc, name: doc.update(modified="2026-10-09 11:11:11"),
			"submitted": lambda doc, name: doc.update(docstatus=1),
			"pending review": lambda doc, name: doc.update(workflow_state="Pending Review"),
			"started": lambda doc, name: doc.update(visit_date=t6d.FRI),
			"clocked in": lambda doc, name: doc.update(clock_in_time="2026-10-09 08:00:00"),
			"clocked out": lambda doc, name: doc.update(clock_out_time="2026-10-09 09:00:00"),
			"invoiced": lambda doc, name: doc.update(sales_invoice="SINV-1"),
			"digest to the technician": lambda doc, name: doc.update(dispatch_digest_sent_on=t6d.FRI),
			"digest to the crew": lambda doc, name: doc["crew"][0].update(digest_sent_on=t6d.FRI),
			"a reading": child("Sapphire Chemistry Reading"),
			"a checklist row": child("Sapphire Maintenance Result"),
			"a cleaning task": child("Sapphire Cleaning Task"),
			"a consumable": child("Sapphire Maintenance Consumable"),
			"commented": comment,
			"a file": lambda doc, name: t6d.frappe.tables.update(
				{"File": [{"attached_to_doctype": table, "attached_to_name": name}]}
			),
		}
		for what, setup in setups.items():
			with self.subTest(what):
				# A fresh visit for every case, so no case leans on another's leftovers.
				for leftover in ("Comment", "File", *self.actions.FORM_TABLES):
					t6d.frappe.tables.pop(leftover, None)
				created = self.create(serial_no="S-1")
				name = created["name"]
				doc = t6d.frappe.docs[(table, name)]
				# Untouched, the same visit would go: the case's one change is the only reason.
				setup(doc, name)
				with self.assertRaises(t6d._Throw) as caught:
					self.actions.remove_created_visit(name, created["modified"])
				self.assertIn("was not removed", str(caught.exception))
				self.assertEqual(self.deleted, [])

	def test_no_delete_permission_no_undo(self):
		t6d.frappe.has_permission = lambda doctype=None, ptype="read", *a, **k: ptype != "delete"
		with self.assertRaises(t6d._PermissionError):
			self.remove()
		self.assertEqual(self.deleted, [])

	def test_the_role_gate(self):
		t6d.frappe.session.user = "stranger@example.com"
		with self.assertRaises(t6d._PermissionError):
			self.remove()
		self.assertEqual(self.deleted, [])

	def test_a_missing_visit_is_said_so(self):
		with self.assertRaises(t6d._Throw):
			self.actions.remove_created_visit("SMR-404", "2026-10-09 10:00:00")

	def test_there_is_no_force_anywhere(self):
		source = ACTIONS_PY.read_text(encoding="utf-8")
		self.assertNotIn("force=", source)
		self.assertIn("frappe.delete_doc(DOCTYPE, doc.name)", source)


# ====================================================================== docs, CI and the contract


class TestDocsAndWiring(unittest.TestCase):
	def assertHas(self, needle, text, where=""):
		# Not assertIn: a README is long, and a failure should name the missing sentence, not print it.
		self.assertTrue(needle in text, f"{where}: missing {needle!r}")

	def assertLacks(self, needle, text, where=""):
		self.assertTrue(needle not in text, f"{where}: still says {needle!r}")

	def test_the_readmes_say_what_changed(self):
		pe = PE_README.read_text(encoding="utf-8")
		self.assertHas("### Follow-ups (Phase 6E", pe, "project_enhancements/README.md")
		section = pe[pe.index("### Follow-ups (Phase 6E") :]
		for needle in (
			"Working days",
			"save_pref",
			"A day note never sends a message on its own",
			"digest_note_lines",
			"Planner Digest Log",
			"depends_on",
			"remove_created_task",
			"reschedule_dependent_tasks",
			"create_visit",
			"_new_maintenance_record",
			"maintenance_actions.py",
		):
			self.assertHas(needle, section, "Follow-ups (Phase 6E)")
		# The Phase 6D and 6B sections no longer say what Phase 6E changed.
		self.assertLacks("A day note is worth a message on its own", pe, "Phase 6D digests bullet")
		self.assertLacks(
			"Tasks that depended on the original still depend on the first half after a split", pe, "Phase 6B"
		)
		sm = SM_README.read_text(encoding="utf-8")
		self.assertHas("Add visit here", sm, "sapphire_maintenance/README.md")
		self.assertHas("Phase 6E", sm, "sapphire_maintenance/README.md")
		self.assertHas("maintenance_actions.py", sm, "sapphire_maintenance/README.md")
		self.assertLacks(
			"a planner shortcut would be a fourth, unreviewed way to create one", sm, "Phase 6B left-out list"
		)
		api = API_README.read_text(encoding="utf-8")
		self.assertHas("`maintenance_actions.py`", api, "api/README.md")
		for name in VISIT_CONTRACT:
			self.assertHas(name, api, "api/README.md")
		self.assertHas(
			"maintenance_actions", api.split("**Tab-indented**")[1].split("Every other file")[0], "tab list"
		)

	def test_the_suite_has_its_own_ci_step_after_phase_6b(self):
		ci = CI.read_text(encoding="utf-8")
		line = "python -m unittest erpnext_enhancements.tests.test_planner_phase6e -v"
		self.assertEqual(ci.count(line), 1)
		self.assertLess(ci.index("erpnext_enhancements.tests.test_planner_phase6b -v"), ci.index(line))
		self.assertLess(ci.index(line), ci.index("erpnext_enhancements.tests.test_planner_phase6c -v"))

	def test_every_file_is_lf_and_the_new_module_is_tab_indented(self):
		for path in (ACTIONS_PY, PLANNER_ACTIONS_PY, TASKS_PY, Path(__file__), PP_JS, MP_JS):
			self.assertNotIn(b"\r", path.read_bytes(), path.name)
		# api/maintenance_actions.py is a new file, so it takes tabs (api/README.md): no code line
		# starts with spaces (the docstring's own wrapped lines do not count).
		code = ACTIONS_PY.read_text(encoding="utf-8").split('"""', 2)[2]
		spaced = [line for line in code.splitlines() if re.match(r" +\S", line)]
		self.assertEqual(spaced, [])


if __name__ == "__main__":
	unittest.main()
