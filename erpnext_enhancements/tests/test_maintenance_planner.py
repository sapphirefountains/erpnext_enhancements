# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Maintenance Planner, and the self-approval rule that kept visits from being submitted.

The planner (``api.maintenance_planner``, page ``maintenance-planner``) is a calendar that people
drag visits around on. Most of what it shows is not stored anywhere yet: the nightly scheduler only
drafts a visit seven days ahead, so everything after that is projected from each contract's feature
rows. Those projections have to agree with the roll-forward (``maintenance_scheduling``), winter
pause included. Otherwise the calendar promises visits that will never be drafted. Moving one also
has to rewrite exactly the rows the scheduler will read.

The second half pins the reason Austin could not submit his visits. The fixture for the
"Sapphire Maintenance Workflow" left ``allow_self_approval`` out. Frappe's field default is 1, but
a fixture import sets ``frappe.flags.in_import``, and ``Document._set_defaults`` returns early under
it, so both transitions landed on prod as 0. Frappe checks self-approval on *every* transition, so
a technician who had started a visit themselves (Log a Visit, Do Visit Today) could not even send it
for review. Request Review now allows it. Approve & Submit still does not: the office (Lisa) reviews
every visit before it is billed. Every workflow transition in the fixtures has to state the flag.

The Maintenance Planner has the Project Planner's features (P1.7): a Resources available panel,
days off on the calendar, a crew timeline (``/crew/<date>``), technicians dragged onto visits, a
reason for a move that overbooks (never a block), Undo, and a route link. The reason flow lives in
``move_visit`` / ``move_projected``; the rest is wiring in the page, so those tests read the page's
source, as ``test_project_planner_page`` does for the Project Planner.

Bench-free: it installs its own ``frappe`` stub and runs the real modules.

Run: python -m unittest erpnext_enhancements.tests.test_maintenance_planner
"""

import calendar
import datetime
import importlib
import inspect
import json
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = Path(__file__).resolve().parents[1]
PAGE_DIR = APP / "sapphire_maintenance/page/maintenance_planner"
WORKSPACE = APP / "sapphire_maintenance/workspace/sapphire_maintenance/sapphire_maintenance.json"

D = datetime.date
TODAY = D(2026, 10, 7)
CONTRACT_MODULE = "erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract.sapphire_maintenance_contract"
STUBBED = (
	"frappe",
	"frappe.utils",
	"frappe.desk",
	"frappe.desk.form",
	"frappe.desk.form.assign_to",
	CONTRACT_MODULE,
	"erpnext_enhancements.api.maintenance_dispatch",
	"erpnext_enhancements.api.maintenance_scheduling",
	"erpnext_enhancements.api.maintenance_planner",
	"erpnext_enhancements.sapphire_maintenance.permissions",
	"erpnext_enhancements.tasks",
)
_saved_modules = {}
frappe = None
planner = None
tasks = None


class _Doc(dict):
	"""Enough of a frappe Document: dict access plus attribute access."""

	def __getattr__(self, name):
		try:
			return self[name]
		except KeyError:
			raise AttributeError(name) from None

	def __setattr__(self, name, value):
		self[name] = value


class _Throw(Exception):
	pass


class _PermissionError(Exception):
	pass


def _getdate(value=None):
	if value is None:
		return TODAY
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, D):
		return value
	return D.fromisoformat(str(value)[:10])


def _add_days(value, days):
	return _getdate(value) + datetime.timedelta(days=days)


def _add_months(value, months):
	d = _getdate(value)
	index = d.month - 1 + months
	year, month = d.year + index // 12, index % 12 + 1
	return D(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _throw(message, exc=None, title=None):
	raise (exc or _Throw)(message)


def setUpModule():
	global frappe, planner, tasks
	for name in STUBBED:
		_saved_modules[name] = sys.modules.pop(name, None)

	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")
	utils.getdate = _getdate
	utils.add_days = _add_days
	utils.add_months = _add_months
	utils.add_years = lambda value, years: _add_months(value, 12 * years)
	utils.get_weekday = lambda value=None: _getdate(value).strftime("%A")
	utils.date_diff = lambda a, b: (_getdate(a) - _getdate(b)).days
	utils.formatdate = lambda value=None, *a, **k: _getdate(value).strftime("%m-%d-%Y")
	utils.nowdate = lambda: str(TODAY)
	utils.strip_html_tags = lambda text: re.sub(r"<[^>]+>", "", str(text))
	utils.escape_html = lambda text: str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
	frappe.utils = utils
	frappe._ = lambda text, *a, **k: text
	frappe.whitelist = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda fn: fn))
	frappe.throw = _throw
	frappe.PermissionError = _PermissionError
	frappe.session = types.SimpleNamespace(user="austin@example.com")
	frappe.local = types.SimpleNamespace(message_log=[])
	frappe.parse_json = json.loads
	frappe.log_error = lambda *a, **k: None
	_reset()

	assign_to = types.ModuleType("frappe.desk.form.assign_to")
	assign_to.remove = lambda doctype, name, user: frappe.calls.append(("unassign", name, user))
	desk = types.ModuleType("frappe.desk")
	form = types.ModuleType("frappe.desk.form")
	desk.form, form.assign_to = form, assign_to

	dispatch = types.ModuleType("erpnext_enhancements.api.maintenance_dispatch")
	dispatch.assign_to_technician = lambda name, user: frappe.calls.append(("assign", name, user))
	dispatch.default_technician_for = lambda project: frappe.defaults.get(project)

	contract_module = types.ModuleType(CONTRACT_MODULE)
	contract_module.iter_seasonal_visits = lambda contract: iter(contract.get("_seasonal", []))

	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.utils": utils,
			"frappe.desk": desk,
			"frappe.desk.form": form,
			"frappe.desk.form.assign_to": assign_to,
			"erpnext_enhancements.api.maintenance_dispatch": dispatch,
			CONTRACT_MODULE: contract_module,
		}
	)
	planner = importlib.import_module("erpnext_enhancements.api.maintenance_planner")
	tasks = importlib.import_module("erpnext_enhancements.tasks")


def tearDownModule():
	for name, module in _saved_modules.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


def _reset():
	frappe.roles = ["Maintenance User", "Projects Manager"]
	frappe.get_roles = lambda user=None: list(frappe.roles)
	frappe.docs = {}
	frappe.writes = []
	frappe.comments = []
	frappe.calls = []
	frappe.defaults = {}
	frappe.exists = set()
	frappe.local.message_log = []
	frappe.get_doc = lambda doctype, name: frappe.docs[(doctype, name)]
	frappe.has_permission = lambda *a, **k: True
	frappe.db = types.SimpleNamespace(
		set_value=lambda dt, name, field, value=None: frappe.writes.append((dt, name, field, value)),
		exists=lambda doctype, filters: _exists(doctype, filters),
		get_value=lambda *a, **k: 1,
		has_column=lambda doctype, column: False,
		sql=lambda *a, **k: [],
	)


def _exists(doctype, filters):
	key = (doctype, filters.get("maintenance_contract") or (filters.get("project"), filters.get("serial_no")))
	return key in frappe.exists


def _row(serial, next_date, frequency="Monthly", name=None):
	return _Doc(
		name=name or f"row-{serial}", serial_no=serial, frequency=frequency, next_visit_date=next_date
	)


def _contract(rows, shape="Per Site Visit", pause=0, name="MNT-CON-1", project="PRJ-1", **extra):
	doc = _Doc(
		name=name,
		project=project,
		customer="Highlands HOA",
		status="Active",
		visit_shape=shape,
		pause_over_winter=pause,
		winterization_month="October",
		startup_month="April",
		start_date=None,
		end_date=None,
		auto_renew=1,
		non_renewal_notice=0,
		covered_features=rows,
	)
	doc.update(extra)
	doc.comments = []
	doc.check_permission = lambda ptype: None
	doc.add_comment = lambda kind, text: doc.comments.append(text)
	frappe.docs[("Sapphire Maintenance Contract", name)] = doc
	return doc


def _dates(entries):
	return [entry["date"] for entry in entries]


class TestNames(unittest.TestCase):
	def test_site_names_lose_the_contract_boilerplate(self):
		cases = {
			"Highlands Maintenance Contract": "Highlands",
			"The Charles - Fountain Maintenance Contract": "The Charles",
			"Candi Wadsworth - Maintenance Contract": "Candi Wadsworth",
			"4th West Preventative Maintenance": "4th West",
			"Red Butte Garden Maintenance 2026": "Red Butte Garden",
			"Perry Water Wall Maintenance Contract": "Perry Water Wall",
			"District Heights Water Wall Maintenance": "District Heights Water Wall",
			"The State Capital Building - Fountain Service and Cleaning": (
				"The State Capital Building - Fountain Service and Cleaning"
			),
			"Maintenance": "Maintenance",
		}
		for title, expected in cases.items():
			self.assertEqual(planner.short_site_name(title), expected, title)

	def test_feature_labels_lose_the_site_prefix(self):
		self.assertEqual(planner.feature_label("MAINT-HARDWARE-East-Courtyard"), "East Courtyard")
		self.assertEqual(planner.feature_label("MAINT-CAPITOL-Oval-Fountain"), "Oval Fountain")
		self.assertEqual(planner.feature_label("SN-0042"), "SN 0042")
		self.assertEqual(planner.feature_label(None), "")


class TestSeries(unittest.TestCase):
	def test_series_stays_inside_the_range(self):
		step = lambda day: _add_days(day, 7)  # noqa: E731
		self.assertEqual(
			planner.project_series(D(2026, 9, 30), step, D(2026, 10, 1), D(2026, 10, 31)),
			[D(2026, 10, 7), D(2026, 10, 14), D(2026, 10, 21), D(2026, 10, 28)],
		)

	def test_a_step_that_does_not_advance_ends_the_series(self):
		self.assertEqual(planner.project_series(TODAY, lambda day: None, TODAY, D(2026, 12, 31)), [TODAY])
		self.assertEqual(planner.project_series(TODAY, lambda day: day, TODAY, D(2026, 12, 31)), [TODAY])


class TestProjections(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_the_next_visit_is_movable_and_the_rest_follow_it(self):
		contract = _contract(
			[_row("SN-A", "2026-10-12", "Bi-Weekly"), _row("SN-B", "2026-10-12", "Bi-Weekly")]
		)
		entries = planner.build_projections(contract, {}, [], D(2026, 10, 1), D(2026, 11, 30), TODAY)
		self.assertEqual(_dates(entries), ["2026-10-12", "2026-10-26", "2026-11-09", "2026-11-23"])
		self.assertEqual([e["movable"] for e in entries], [True, False, False, False])
		self.assertEqual(entries[0]["from_date"], "2026-10-12")
		self.assertIsNone(entries[1]["from_date"])

	def test_an_open_draft_is_the_next_visit_and_the_series_rolls_from_it(self):
		contract = _contract([_row("SN-A", "2026-10-12", "Weekly")])
		drafts = {"MNT-CON-1": D(2026, 10, 14)}
		entries = planner.build_projections(contract, drafts, [], D(2026, 10, 1), D(2026, 10, 31), TODAY)
		self.assertEqual(_dates(entries), ["2026-10-21", "2026-10-28"])
		self.assertFalse(any(e["movable"] for e in entries))

	def test_an_overdue_draft_rolls_from_today(self):
		contract = _contract([_row("SN-A", "2026-09-01", "Weekly")])
		entries = planner.build_projections(
			contract, {"MNT-CON-1": D(2026, 9, 1)}, [], D(2026, 10, 1), D(2026, 10, 20), TODAY
		)
		self.assertEqual(_dates(entries), ["2026-10-14"])

	def test_a_site_visit_comes_round_at_its_most_frequent_feature(self):
		contract = _contract([_row("SN-A", "2026-10-12", "Monthly"), _row("SN-B", "2026-10-20", "Weekly")])
		entries = planner.build_projections(contract, {}, [], D(2026, 10, 1), D(2026, 10, 31), TODAY)
		self.assertEqual(_dates(entries), ["2026-10-12", "2026-10-19", "2026-10-26"])

	def test_the_winter_pause_empties_the_off_season_and_resumes_in_april(self):
		# Highlands: weekly, drained over winter. The roll-forward parks it at April 1.
		contract = _contract([_row("SN-A", "2026-09-22", "Weekly")], pause=1)
		entries = planner.build_projections(contract, {}, [], D(2026, 9, 1), D(2027, 4, 20), TODAY)
		self.assertEqual(
			_dates(entries), ["2026-09-22", "2026-09-29", "2027-04-01", "2027-04-08", "2027-04-15"]
		)

	def test_per_feature_series_are_separate_and_a_drafted_feature_is_not_movable(self):
		contract = _contract(
			[_row("SN-A", "2026-10-12"), _row("SN-B", "2026-10-15")], shape="Per Feature", project="PRJ-9"
		)
		drafts = {("PRJ-9", "SN-A"): D(2026, 10, 12)}
		entries = planner.build_projections(contract, drafts, [], D(2026, 10, 1), D(2026, 11, 20), TODAY)
		by_serial = {}
		for entry in entries:
			by_serial.setdefault(entry["serial_no"], []).append(entry)
		self.assertEqual(_dates(by_serial["SN-A"]), ["2026-11-12"])
		self.assertFalse(by_serial["SN-A"][0]["movable"])
		self.assertEqual(_dates(by_serial["SN-B"]), ["2026-10-15", "2026-11-15"])
		self.assertTrue(by_serial["SN-B"][0]["movable"])

	def test_a_contract_that_will_not_renew_stops_at_its_end_date(self):
		contract = _contract([_row("SN-A", "2026-10-12", "Weekly")], end_date="2026-10-25", auto_renew=0)
		entries = planner.build_projections(contract, {}, [], D(2026, 10, 1), D(2026, 11, 30), TODAY)
		self.assertEqual(_dates(entries), ["2026-10-12", "2026-10-19"])
		renewing = _contract([_row("SN-A", "2026-10-12", "Weekly")], end_date="2026-10-25", auto_renew=1)
		self.assertEqual(
			len(planner.build_projections(renewing, {}, [], D(2026, 10, 1), D(2026, 11, 30), TODAY)), 8
		)

	def test_nothing_is_projected_before_the_contract_starts(self):
		contract = _contract([_row("SN-A", "2026-10-01", "Weekly")], start_date="2026-10-15")
		entries = planner.build_projections(contract, {}, [], D(2026, 10, 1), D(2026, 10, 31), TODAY)
		self.assertEqual(_dates(entries), ["2026-10-15", "2026-10-22", "2026-10-29"])
		# The card shows the start date, but the move checks the stored date.
		self.assertEqual(entries[0]["from_date"], "2026-10-01")

	def test_seasonal_visits_land_on_the_first_of_their_month(self):
		contract = _contract([])
		seasonal = [
			{
				"label": "Seasonal Startup",
				"target_month": "April",
				"last_generated_year": 2026,
				"drafted": False,
			},
			{
				"label": "Winterization",
				"target_month": "October",
				"last_generated_year": 2026,
				"drafted": False,
			},
			{
				"label": "Mid-Season Check",
				"target_month": "November",
				"last_generated_year": None,
				"drafted": False,
			},
			{"label": "Drafted", "target_month": "November", "last_generated_year": None, "drafted": True},
		]
		entries = planner.build_projections(contract, {}, seasonal, D(2026, 10, 1), D(2027, 4, 30), TODAY)
		self.assertEqual(
			[(e["label"], e["date"]) for e in entries],
			[("Mid-Season Check", "2026-11-01"), ("Seasonal Startup", "2027-04-01")],
		)
		self.assertFalse(any(e["movable"] for e in entries))

	def test_a_seasonal_visit_due_this_month_shows_today_and_a_missed_one_is_skipped(self):
		contract = _contract([])
		seasonal = [
			{
				"label": "Winterization",
				"target_month": "October",
				"last_generated_year": 2025,
				"drafted": False,
			},
			{"label": "Startup", "target_month": "April", "last_generated_year": 2025, "drafted": False},
		]
		entries = planner.build_projections(contract, {}, seasonal, D(2026, 3, 1), D(2026, 10, 31), TODAY)
		self.assertEqual([(e["label"], e["date"]) for e in entries], [("Winterization", "2026-10-07")])


class TestRowsToMove(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_a_site_visit_carries_every_feature_due_before_the_new_day(self):
		rows = [
			_row("A", "2026-10-12"),
			_row("B", "2026-10-12"),
			_row("C", "2026-10-20"),
			_row("D", "2026-11-30"),
		]
		contract = _contract(rows)
		moved = planner.rows_to_move(contract, "2026-10-12", "2026-10-25")
		self.assertEqual([row.serial_no for row in moved], ["A", "B", "C"])
		earlier = planner.rows_to_move(contract, "2026-10-12", "2026-10-09")
		self.assertEqual([row.serial_no for row in earlier], ["A", "B"])

	def test_a_stale_card_moves_nothing(self):
		contract = _contract([_row("A", "2026-10-14")])
		self.assertEqual(planner.rows_to_move(contract, "2026-10-12", "2026-10-25"), [])

	def test_a_feature_move_touches_only_that_feature(self):
		contract = _contract([_row("A", "2026-10-12"), _row("B", "2026-10-12")], shape="Per Feature")
		self.assertEqual(
			[r.serial_no for r in planner.rows_to_move(contract, "2026-10-12", "2026-10-20", "B")], ["B"]
		)
		self.assertEqual(planner.rows_to_move(contract, "2026-10-13", "2026-10-20", "B"), [])


class TestMoveProjected(unittest.TestCase):
	def setUp(self):
		_reset()
		self.drafted = []
		real = tasks._draft_maintenance_record

		def fake(*args, **kwargs):
			inspect.signature(real).bind(*args, **kwargs)  # the real helper must accept the call
			self.drafted.append(kwargs)
			return _Doc(name="NEW-1")

		self._real = real
		tasks._draft_maintenance_record = fake

	def tearDown(self):
		tasks._draft_maintenance_record = self._real

	def _next_dates(self):
		return {name: value for _, name, field, value in frappe.writes if field == "next_visit_date"}

	def test_moving_inside_the_window_drafts_on_the_exact_day(self):
		_contract([_row("A", "2026-10-20", name="r1")])
		result = planner.move_projected("MNT-CON-1", "2026-10-20", "2026-10-08")
		self.assertEqual(self._next_dates(), {"r1": D(2026, 10, 8)})
		self.assertEqual(result, {"moved": 1, "drafted": "NEW-1"})
		self.assertEqual(
			self.drafted, [{"serial_no": None, "scheduled_date": D(2026, 10, 8), "exact_date": True}]
		)

	def test_moving_further_out_leaves_the_drafting_to_the_scheduler(self):
		contract = _contract([_row("A", "2026-10-20", name="r1")])
		result = planner.move_projected("MNT-CON-1", "2026-10-20", "2026-11-03")
		self.assertEqual(result, {"moved": 1, "drafted": None})
		self.assertEqual(self.drafted, [])
		self.assertEqual(len(contract.comments), 1)

	def test_the_window_is_the_schedulers(self):
		_contract([_row("A", "2026-10-20", name="r1")])
		edge = _add_days(TODAY, tasks.MAINTENANCE_DRAFT_HORIZON_DAYS)
		self.assertEqual(planner.move_projected("MNT-CON-1", "2026-10-20", str(edge))["drafted"], "NEW-1")
		self.assertIn(
			"horizon = add_days(today, MAINTENANCE_DRAFT_HORIZON_DAYS)",
			(APP / "tasks.py").read_text(encoding="utf-8"),
		)

	def test_a_contract_that_has_not_started_is_not_drafted_early(self):
		_contract([_row("A", "2026-10-20", name="r1")], start_date="2026-10-10")
		self.assertIsNone(planner.move_projected("MNT-CON-1", "2026-10-20", "2026-10-08")["drafted"])

	def test_refusals(self):
		_contract([_row("A", "2026-10-20", name="r1")])
		with self.assertRaises(_Throw):
			planner.move_projected("MNT-CON-1", "2026-10-20", "2026-10-06")  # the past
		with self.assertRaises(_Throw):
			planner.move_projected("MNT-CON-1", "2026-10-19", "2026-10-28")  # stale card
		frappe.exists.add(("Sapphire Maintenance Record", "MNT-CON-1"))
		with self.assertRaises(_Throw):
			planner.move_projected("MNT-CON-1", "2026-10-20", "2026-10-28")  # drafted meanwhile
		self.assertEqual(frappe.writes, [])

	def test_outsiders_are_refused(self):
		frappe.roles = ["Customer"]
		_contract([_row("A", "2026-10-20")])
		with self.assertRaises(_PermissionError):
			planner.move_projected("MNT-CON-1", "2026-10-20", "2026-10-28")


class _Record(_Doc):
	def check_permission(self, ptype):
		pass

	def add_comment(self, kind, text):
		frappe.comments.append((self.name, kind, text))

	def save(self):
		frappe.saved.append(dict(self))
		frappe.local.message_log.append(
			{"message": "<b>Austin Healey</b> has approved time off.", "title": "Check who is going"}
		)
		self.modified = "2026-10-07 10:00:01"


class TestMoveVisit(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe.saved = []

	def _record(self, **values):
		doc = _Record(
			name="REC-1",
			doctype="Sapphire Maintenance Record",
			docstatus=0,
			workflow_state="Draft",
			scheduled_visit_date=D(2026, 10, 12),
			visit_date=None,
			technician="austin@example.com",
			modified="2026-10-07 10:00:00",
		)
		doc.update(values)
		frappe.docs[("Sapphire Maintenance Record", "REC-1")] = doc
		return doc

	def test_a_draft_moves_and_its_warnings_come_back_as_text(self):
		self._record()
		result = planner.move_visit("REC-1", date="2026-10-15", modified="2026-10-07 10:00:00")
		self.assertEqual(frappe.saved[0]["scheduled_visit_date"], D(2026, 10, 15))
		self.assertEqual(result["warnings"], ["Austin Healey has approved time off."])
		# Taken off the log, so frappe does not also pop them up as a dialog.
		self.assertEqual(frappe.local.message_log, [])

	def test_handing_a_visit_over_moves_the_assignment(self):
		self._record()
		planner.move_visit("REC-1", technician="lisa@example.com")
		self.assertEqual(
			frappe.calls,
			[("unassign", "REC-1", "austin@example.com"), ("assign", "REC-1", "lisa@example.com")],
		)

	def test_an_unchanged_visit_is_not_saved(self):
		self._record()
		planner.move_visit("REC-1", date="2026-10-12", technician="austin@example.com")
		self.assertEqual(frappe.saved, [])

	def test_refusals(self):
		cases = [
			({"docstatus": 1, "workflow_state": "Final/Submitted"}, {"date": "2026-10-15"}),
			({"workflow_state": "Pending Review"}, {"date": "2026-10-15"}),
			({"visit_date": D(2026, 10, 7)}, {"date": "2026-10-15"}),
			({}, {"date": "2026-10-06"}),
			({}, {"date": "2026-10-15", "modified": "2026-10-01 09:00:00"}),
		]
		for values, args in cases:
			with self.subTest(values=values, args=args):
				self._record(**values)
				with self.assertRaises(_Throw):
					planner.move_visit("REC-1", **args)
		self.assertEqual(frappe.saved, [])


class TestGetPlanner(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe.defaults = {"PRJ-1": "austin@example.com"}

	def test_cards_carry_their_status_and_only_unstarted_drafts_move(self):
		def row(name, docstatus, state, plan_date, visit_date=None):
			return _Doc(
				name=name,
				project="PRJ-1",
				customer="Highlands HOA",
				maintenance_contract="MNT-CON-1",
				serial_no=None,
				visit_label=None,
				technician="austin@example.com",
				scheduled_visit_date=plan_date,
				visit_date=visit_date,
				docstatus=docstatus,
				workflow_state=state,
				completion_percent=0,
				has_out_of_range_readings=0,
				modified="2026-10-07 09:00:00",
				plan_date=plan_date,
			)

		visits = [
			row("R-DRAFT", 0, "Draft", D(2026, 10, 12)),
			row("R-LATE", 0, "Draft", D(2026, 10, 2)),
			row("R-STARTED", 0, "Draft", D(2026, 10, 7), visit_date=D(2026, 10, 7)),
			row("R-REVIEW", 0, "Pending Review", D(2026, 10, 6)),
			row("R-DONE", 1, "Final/Submitted", D(2026, 10, 1)),
		]
		frappe.db.sql = lambda query, *a, **k: list(visits) if "plan_date BETWEEN" in query else []
		_contract([])
		frappe.get_all = lambda doctype, **kwargs: (
			["MNT-CON-1"]
			if doctype == "Sapphire Maintenance Contract"
			else [("PRJ-1", "Highlands Maintenance Contract")]
			if doctype == "Project"
			else []
		)
		data = planner.get_planner("2026-09-27", "2026-11-07")
		cards = {card["name"]: card for card in data["visits"]}
		self.assertEqual(
			{name: (card["status"], card["movable"], card["overdue"]) for name, card in cards.items()},
			{
				"R-DRAFT": ("draft", True, False),
				"R-LATE": ("draft", True, True),
				"R-STARTED": ("draft", False, False),
				"R-REVIEW": ("pending", False, False),
				"R-DONE": ("done", False, False),
			},
		)
		self.assertEqual(cards["R-DRAFT"]["site"], "Highlands")

	def test_only_technicians_are_offered_as_technicians(self):
		# The office holds Maintenance User too (Lisa reviews visits; she does not do them).
		seen = {}

		def get_all(doctype, **kwargs):
			seen[doctype] = kwargs.get("filters")
			if doctype == "Has Role":
				return ["austin@example.com", "lisa@example.com"]
			if doctype == "Employee":
				return ["austin@example.com"]
			return [_Doc(name="austin@example.com", full_name="Austin Healey", enabled=1)]

		frappe.get_all = get_all
		people = planner._technicians([])
		self.assertEqual(seen["Employee"]["designation"], ["like", "%Technician%"])
		self.assertEqual(seen["Employee"]["status"], "Active")
		self.assertEqual(people, [{"user": "austin@example.com", "name": "Austin Healey", "enabled": True}])

	def test_ranges_are_bounded(self):
		with self.assertRaises(_Throw):
			planner.get_planner("2026-10-01", "2027-03-01")
		with self.assertRaises(_Throw):
			planner.get_planner("2026-10-31", "2026-10-01")


ENGINE_MODULE = "erpnext_enhancements.project_enhancements.crew_availability"


class TestProjectBookings(unittest.TestCase):
	"""Project work shown read-only, with free hours from the shared engine (P1.6).

	Maintenance and Projects share technicians, so a technician's free hours must read the same in
	both planners. The planner takes them from ``crew_availability.availability`` and never works
	them out itself; a failure there must leave the maintenance calendar standing.
	"""

	TECHS = [
		{"user": "austin@example.com", "name": "Austin Healey", "enabled": True},
		{"user": "lisa@example.com", "name": "Lisa", "enabled": True},
	]

	def setUp(self):
		_reset()
		frappe.get_traceback = lambda: "traceback"
		self.logged = []
		frappe.log_error = lambda *a, **k: self.logged.append(k)
		self._saved_engine = sys.modules.get(ENGINE_MODULE)
		self.calls = []
		self.engine = types.ModuleType(ENGINE_MODULE)
		self.engine.availability = self._availability
		sys.modules[ENGINE_MODULE] = self.engine

		def booking(kind, ref, label, project, hours, slot=None):
			return {
				"kind": kind,
				"ref": ref,
				"label": label,
				"project": project,
				"hours": hours,
				"slot": slot,
				"estimated": slot is None,
			}

		self.result = {
			"resources": [],
			"user_to_resource": {"austin@example.com": "RES-1"},
			"days": {
				"RES-1": {
					"2026-10-12": {
						"capacity": 8.0,
						"booked": 10.0,
						"free": 0.0,
						"off": None,
						"conflicts": ["Over by 2h"],
						"warnings": ["On restricted duty"],
						"bookings": [
							booking("visit", "SMR-1", "Highlands", "PRJ-1", 2.0),
							booking("task", "TASK-1", "Install pump", "PRJ-2", 6.0, ["09:00", "15:00"]),
							booking("rental", "TASK-2", "Delivery", None, 1.0),
							booking("travel", "TRIP-1", "Travel: Vegas", None, 1.0),
						],
					},
					"2026-10-13": {
						"capacity": 0.0,
						"booked": 0.0,
						"free": 0.0,
						"off": "Not a work day",
						"conflicts": [],
						"warnings": [],
						"bookings": [],
					},
				}
			},
		}

	def tearDown(self):
		if self._saved_engine is None:
			sys.modules.pop(ENGINE_MODULE, None)
		else:
			sys.modules[ENGINE_MODULE] = self._saved_engine

	def _availability(self, start, end, resources=None):
		self.calls.append((start, end, resources))
		if isinstance(self.result, Exception):
			raise self.result
		return self.result

	def test_bookings_are_keyed_by_user_with_non_visit_items_only(self):
		bookings = planner._project_bookings(self.TECHS, D(2026, 10, 12), D(2026, 10, 13))
		# One engine call for the range, whatever the number of technicians.
		self.assertEqual(self.calls, [(D(2026, 10, 12), D(2026, 10, 13), None)])
		# Lisa has no Planner Resource: absent, so the planner shows no free hours for her.
		self.assertEqual(list(bookings), ["austin@example.com"])
		day = bookings["austin@example.com"]["2026-10-12"]
		self.assertEqual(
			{k: day[k] for k in ("capacity", "booked", "free", "off", "conflicts")},
			{"capacity": 8.0, "booked": 10.0, "free": 0.0, "off": None, "conflicts": ["Over by 2h"]},
		)
		self.assertEqual([i["kind"] for i in day["items"]], ["task", "rental", "travel"])
		self.assertEqual(
			day["items"][0],
			{
				"kind": "task",
				"ref": "TASK-1",
				"label": "Install pump",
				"project": "PRJ-2",
				"hours": 6.0,
				"slot": ["09:00", "15:00"],
			},
		)
		self.assertEqual(bookings["austin@example.com"]["2026-10-13"]["off"], "Not a work day")
		self.assertEqual(bookings["austin@example.com"]["2026-10-13"]["items"], [])

	def test_get_planner_returns_the_bookings(self):
		names = ("_records_between", "_unscheduled_drafts", "_projections", "_decorate", "_technicians")
		saved = {name: getattr(planner, name) for name in names}
		try:
			planner._records_between = lambda start, end: []
			planner._unscheduled_drafts = lambda: []
			planner._projections = lambda start, end, today: []
			planner._decorate = lambda cards: None
			planner._technicians = lambda cards: self.TECHS
			data = planner.get_planner("2026-10-12", "2026-10-13")
		finally:
			for name, value in saved.items():
				setattr(planner, name, value)
		self.assertEqual([t["user"] for t in data["technicians"]], [t["user"] for t in self.TECHS])
		self.assertEqual(list(data["bookings"]), ["austin@example.com"])
		self.assertEqual(len(data["bookings"]["austin@example.com"]["2026-10-12"]["items"]), 3)

	def test_an_engine_failure_returns_no_bookings_and_logs(self):
		self.result = RuntimeError("project side broke")
		self.assertEqual(planner._project_bookings(self.TECHS, D(2026, 10, 12), D(2026, 10, 13)), {})
		self.assertEqual(len(self.logged), 1)
		self.assertIn("title", self.logged[0])
		self.assertEqual(self.logged[0]["message"], "traceback")

	def test_get_planner_survives_an_engine_failure(self):
		self.result = RuntimeError("project side broke")
		names = ("_records_between", "_unscheduled_drafts", "_projections", "_decorate", "_technicians")
		saved = {name: getattr(planner, name) for name in names}
		try:
			planner._records_between = lambda start, end: []
			planner._unscheduled_drafts = lambda: []
			planner._projections = lambda start, end, today: []
			planner._decorate = lambda cards: None
			planner._technicians = lambda cards: self.TECHS
			data = planner.get_planner("2026-10-12", "2026-10-13")
		finally:
			for name, value in saved.items():
				setattr(planner, name, value)
		self.assertEqual(data["bookings"], {})
		self.assertEqual(len(self.logged), 1)

	def test_no_technicians_means_no_engine_call(self):
		self.assertEqual(planner._project_bookings([], D(2026, 10, 12), D(2026, 10, 13)), {})
		self.assertEqual(self.calls, [])

	def test_the_engine_is_imported_lazily(self):
		# The engine imports this module back for the visit projections; a top-level import
		# here would make the pair cyclic at import time.
		source = inspect.getsource(planner)
		self.assertNotIn("\nfrom erpnext_enhancements.project_enhancements", source)
		self.assertNotIn("\nimport erpnext_enhancements.project_enhancements", source)
		self.assertIn(
			"\t\tfrom erpnext_enhancements.project_enhancements.crew_availability import availability",
			source,
		)

	def test_the_page_shows_them_read_only(self):
		code = (PAGE_DIR / "maintenance_planner.js").read_text(encoding="utf-8")
		start = code.index("booking_html(card) {")
		card = code[start : code.index("card_html(card) {", start)]
		self.assertNotIn("mp-movable", card)
		cards = code[code.index("booking_cards() {") : code.index("fmt_hours(hours) {")]
		self.assertNotIn("movable", cards)
		# Only a card flagged movable is ever picked up by a drag.
		self.assertIn("if (!card || !card.movable || card.saving) return null;", code)
		# Free hours come from the engine's numbers, never from the visit cards.
		self.assertIn("this.data.bookings", code)
		self.assertIn("{0}h free", code)

	def test_the_project_work_pref_is_guarded_and_remembered(self):
		code = (PAGE_DIR / "maintenance_planner.js").read_text(encoding="utf-8")
		self.assertIn('project_key: "ee_maintenance_planner_project_work"', code)
		self.assertIn('this.load_pref(MP.project_key, "1") !== "0"', code)
		self.assertIn("this.save_pref(MP.project_key,", code)
		self.assertIn('__("Project work")', code)
		# The same try/catch wraps every read and write of a pref.
		for helper in ("load_pref(key, fallback) {", "save_pref(key, value) {"):
			body = code[code.index(helper) :]
			self.assertLess(body.index("try {"), body.index("catch (e)"), helper)
			self.assertLess(body.index("catch (e)"), body.index("\n\t}\n"), helper)

	def test_cards_open_the_task_or_the_trip(self):
		code = (PAGE_DIR / "maintenance_planner.js").read_text(encoding="utf-8")
		self.assertIn('frappe.set_route("Form", "Task", card.ref)', code)
		self.assertIn('frappe.set_route("Form", "Travel Trip", card.ref)', code)
		# Back and Forward stay with frappe.set_route.
		self.assertNotIn("pushState", code)
		self.assertNotIn("replaceState", code)

	def test_the_legend_names_the_new_kinds(self):
		code = (PAGE_DIR / "maintenance_planner.js").read_text(encoding="utf-8")
		for label in ("Project task (read-only)", "Rental crew (read-only)", "Travel (read-only)"):
			self.assertIn(f'__("{label}")', code)


def _engine_day_conflicts(capacity, off, bookings):
	"""``crew_availability.day_conflicts`` as documented: day off, over capacity, double-booked slots."""
	capacity = float(capacity or 0)
	out = []
	if capacity <= 0 and any(float(b.get("hours") or 0) > 0 for b in bookings):
		out.append(f"Booked on a day off ({off})" if off else "Booked on a day off")
	else:
		booked = sum(float(b.get("hours") or 0) for b in bookings)
		if booked - capacity > 0.01:
			out.append(f"Over by {round(booked - capacity, 2):g}h")
	return out


def _cell(capacity=8.0, bookings=(), off=None):
	return {
		"capacity": capacity,
		"booked": sum(b["hours"] for b in bookings),
		"free": 0.0,
		"off": off,
		"bookings": list(bookings),
		"conflicts": [],
		"warnings": [],
	}


def _task(hours, ref="TASK-1"):
	return {
		"kind": "task",
		"ref": ref,
		"label": "Install pump",
		"project": "PRJ-2",
		"hours": hours,
		"slot": None,
	}


class _FakeEngine:
	"""Installs a stand-in ``crew_availability`` that answers from a table of day cells."""

	def install(self, cells=None):
		"""``cells``: ``{planner resource: {"YYYY-MM-DD": day cell}}``."""
		self.cells = cells or {}
		self.availability_calls = []
		self._saved_engine = sys.modules.get(ENGINE_MODULE)
		engine = types.ModuleType(ENGINE_MODULE)
		engine.availability = self._availability
		engine.day_conflicts = _engine_day_conflicts
		engine.get_settings = lambda: {"maintenance_visit_hours": 2.0}
		sys.modules[ENGINE_MODULE] = engine
		self.logged = []
		frappe.get_traceback = lambda: "traceback"
		frappe.log_error = lambda *a, **k: self.logged.append(k)
		# Planner Resources by user: Austin and Lisa have one, nobody else does.
		resources = {
			"austin@example.com": {"name": "RES-1", "resource_name": "Austin Healey"},
			"lisa@example.com": {"name": "RES-2", "resource_name": "Lisa"},
		}

		def get_all(doctype, **kwargs):
			if doctype == "Planner Resource":
				row = resources.get((kwargs.get("filters") or {}).get("user"))
				return [_Doc(row)] if row else []
			return []

		frappe.get_all = get_all

	def restore(self):
		if self._saved_engine is None:
			sys.modules.pop(ENGINE_MODULE, None)
		else:
			sys.modules[ENGINE_MODULE] = self._saved_engine

	def _availability(self, start, end, resources=None):
		self.availability_calls.append((start, end, resources))
		if isinstance(self.cells, Exception):
			raise self.cells
		return {"days": self.cells}


class TestNewConflicts(unittest.TestCase):
	"""``new_visit_conflicts``: only what the visit adds to a day counts."""

	def _new(self, cell, ref="V-1", hours=2.0):
		return planner.new_visit_conflicts(_engine_day_conflicts, cell, ref, hours)

	def test_a_visit_that_fits_adds_nothing(self):
		self.assertEqual(self._new(_cell(8, [_task(5)])), [])

	def test_a_visit_that_overfills_the_day_is_a_conflict(self):
		self.assertEqual(self._new(_cell(8, [_task(7)])), ["Over by 1h"])

	def test_a_day_off_with_nothing_booked_is_a_conflict(self):
		self.assertEqual(self._new(_cell(0, [], "Time off")), ["Booked on a day off (Time off)"])

	def test_a_day_that_was_already_a_conflict_is_not_new(self):
		# Already booked on a day off: one more booking says the same thing, so nobody is asked again.
		self.assertEqual(self._new(_cell(0, [_task(4)], "Time off")), [])

	def test_a_worse_overbooking_is_new(self):
		self.assertEqual(self._new(_cell(8, [_task(9)])), ["Over by 3h"])

	def test_the_visit_already_on_the_day_is_not_counted_twice(self):
		here = {
			"kind": "visit",
			"ref": "V-1",
			"label": "Highlands",
			"project": None,
			"hours": 2.0,
			"slot": None,
		}
		self.assertEqual(self._new(_cell(8, [_task(6), here])), [])

	def test_it_agrees_with_the_engine(self):
		# Guard against the engine renaming a sentence: the real day_conflicts speaks the same.
		saved = sys.modules.pop(ENGINE_MODULE, None)
		try:
			engine = importlib.import_module(ENGINE_MODULE)
		except Exception as error:  # the engine imports more of the app than this suite stubs
			self.skipTest(f"the availability engine does not import bench-free: {error}")
		finally:
			sys.modules.pop(ENGINE_MODULE, None)
			if saved is not None:
				sys.modules[ENGINE_MODULE] = saved
		for cell in (_cell(8, [_task(7)]), _cell(0, [], "Time off"), _cell(0, [_task(4)], "Time off")):
			self.assertEqual(
				self._new(cell),
				planner.new_visit_conflicts(engine.day_conflicts, cell, "V-1", 2.0),
			)


class TestMoveVisitReason(unittest.TestCase):
	"""Overbooking warns and never blocks: ask for a reason, save nothing until it comes (P1.7)."""

	def setUp(self):
		_reset()
		frappe.saved = []
		self.engine = _FakeEngine()
		self.engine.install()
		self.addCleanup(self.engine.restore)

	def _record(self, **values):
		doc = _Record(
			name="REC-1",
			doctype="Sapphire Maintenance Record",
			docstatus=0,
			workflow_state="Draft",
			project="PRJ-1",
			scheduled_visit_date=D(2026, 10, 12),
			visit_date=None,
			technician="austin@example.com",
			modified="2026-10-07 10:00:00",
		)
		doc.update(values)
		frappe.docs[("Sapphire Maintenance Record", "REC-1")] = doc
		return doc

	def test_a_move_that_overbooks_asks_for_a_reason_and_saves_nothing(self):
		self._record()
		self.engine.cells = {"RES-1": {"2026-10-15": _cell(8, [_task(7)])}}
		result = planner.move_visit("REC-1", date="2026-10-15")
		self.assertEqual(
			result,
			{"needs_reason": True, "conflicts": {"Austin Healey": ["2026-10-15: Over by 1h"]}},
		)
		self.assertEqual(frappe.saved, [])
		self.assertEqual(frappe.comments, [])
		self.assertEqual(frappe.calls, [])
		# Only the target technician's resource, for just the target day.
		self.assertEqual(self.engine.availability_calls, [(D(2026, 10, 15), D(2026, 10, 15), ["RES-1"])])

	def test_with_a_reason_it_saves_and_the_reason_goes_on_the_visit(self):
		self._record()
		self.engine.cells = {"RES-1": {"2026-10-15": _cell(8, [_task(7)])}}
		result = planner.move_visit("REC-1", date="2026-10-15", reason="  Pump is failing, <b>urgent</b>  ")
		self.assertEqual(frappe.saved[0]["scheduled_visit_date"], D(2026, 10, 15))
		self.assertNotIn("needs_reason", result)
		self.assertEqual(result["conflicts"], {"Austin Healey": ["2026-10-15: Over by 1h"]})
		name, kind, text = frappe.comments[0]
		self.assertEqual((name, kind), ("REC-1", "Comment"))
		self.assertIn("Scheduled over a conflict on the Maintenance Planner", text)
		self.assertIn("Austin Healey: 2026-10-15: Over by 1h", text)
		self.assertIn("Reason: Pump is failing, &lt;b&gt;urgent&lt;/b&gt;", text)

	def test_a_move_that_fits_saves_without_asking_or_commenting(self):
		self._record()
		self.engine.cells = {"RES-1": {"2026-10-15": _cell(8, [_task(3)])}}
		result = planner.move_visit("REC-1", date="2026-10-15", reason="not needed")
		self.assertEqual(len(frappe.saved), 1)
		self.assertNotIn("conflicts", result)
		self.assertEqual(frappe.comments, [])

	def test_a_day_off_asks(self):
		self._record()
		self.engine.cells = {"RES-1": {"2026-10-16": _cell(0, [], "Not a work day")}}
		result = planner.move_visit("REC-1", date="2026-10-16")
		self.assertEqual(
			result["conflicts"], {"Austin Healey": ["2026-10-16: Booked on a day off (Not a work day)"]}
		)

	def test_a_conflict_the_day_already_had_does_not_ask_again(self):
		self._record()
		self.engine.cells = {"RES-1": {"2026-10-16": _cell(0, [_task(4)], "Time off")}}
		result = planner.move_visit("REC-1", date="2026-10-16")
		self.assertEqual(len(frappe.saved), 1)
		self.assertNotIn("needs_reason", result)
		self.assertEqual(frappe.comments, [])

	def test_handing_a_visit_over_checks_the_new_technicians_day(self):
		self._record()
		self.engine.cells = {
			"RES-1": {"2026-10-12": _cell(8, [])},
			"RES-2": {"2026-10-12": _cell(8, [_task(7)])},
		}
		result = planner.move_visit("REC-1", technician="lisa@example.com")
		self.assertEqual(result["conflicts"], {"Lisa": ["2026-10-12: Over by 1h"]})
		self.assertEqual(frappe.saved, [])
		self.assertEqual(frappe.calls, [])  # no assignment moved either
		self.assertEqual(self.engine.availability_calls, [(D(2026, 10, 12), D(2026, 10, 12), ["RES-2"])])
		self._record()  # the page asks again with the reason; each request loads the record afresh
		planner.move_visit("REC-1", technician="lisa@example.com", reason="Austin is out sick")
		self.assertEqual(len(frappe.saved), 1)
		self.assertEqual(frappe.calls[-1], ("assign", "REC-1", "lisa@example.com"))
		self.assertIn("Reason: Austin is out sick", frappe.comments[0][2])

	def test_a_technician_with_no_planner_resource_has_no_hours_to_overbook(self):
		self._record()
		planner.move_visit("REC-1", technician="lee@example.com")
		self.assertEqual(len(frappe.saved), 1)
		self.assertEqual(self.engine.availability_calls, [])

	def test_unassigning_or_an_unchanged_move_checks_nothing(self):
		self._record()
		planner.move_visit("REC-1", technician="")
		self._record()
		planner.move_visit("REC-1", date="2026-10-12", technician="austin@example.com")
		self.assertEqual(self.engine.availability_calls, [])

	def test_an_engine_failure_never_blocks_a_move(self):
		self._record()
		self.engine.cells = RuntimeError("project side broke")
		result = planner.move_visit("REC-1", date="2026-10-15")
		self.assertEqual(len(frappe.saved), 1)
		self.assertNotIn("needs_reason", result)
		self.assertEqual(len(self.engine.logged), 1)
		self.assertEqual(self.engine.logged[0]["title"], "Maintenance Planner: conflict check failed")


class TestMoveProjectedReason(unittest.TestCase):
	def setUp(self):
		_reset()
		self.engine = _FakeEngine()
		self.engine.install()
		self.addCleanup(self.engine.restore)
		frappe.defaults = {"PRJ-1": "austin@example.com"}
		real = tasks._draft_maintenance_record
		tasks._draft_maintenance_record = lambda *a, **k: _Doc(name="NEW-1")
		self.addCleanup(setattr, tasks, "_draft_maintenance_record", real)

	def _next_dates(self):
		return {name: value for _, name, field, value in frappe.writes if field == "next_visit_date"}

	def test_a_move_that_overbooks_asks_before_writing_anything(self):
		contract = _contract([_row("A", "2026-10-20", name="r1")])
		self.engine.cells = {"RES-1": {"2026-11-03": _cell(8, [_task(7)])}}
		result = planner.move_projected("MNT-CON-1", "2026-10-20", "2026-11-03")
		self.assertEqual(
			result, {"needs_reason": True, "conflicts": {"Austin Healey": ["2026-11-03: Over by 1h"]}}
		)
		self.assertEqual(frappe.writes, [])
		self.assertEqual(contract.comments, [])
		self.assertEqual(self.engine.availability_calls, [(D(2026, 11, 3), D(2026, 11, 3), ["RES-1"])])

	def test_with_a_reason_it_moves_and_the_reason_goes_on_the_contract(self):
		contract = _contract([_row("A", "2026-10-20", name="r1")])
		self.engine.cells = {"RES-1": {"2026-11-03": _cell(8, [_task(7)])}}
		result = planner.move_projected("MNT-CON-1", "2026-10-20", "2026-11-03", reason="Customer asked")
		self.assertEqual(self._next_dates(), {"r1": D(2026, 11, 3)})
		self.assertEqual(result["moved"], 1)
		self.assertEqual(result["conflicts"], {"Austin Healey": ["2026-11-03: Over by 1h"]})
		self.assertEqual(len(contract.comments), 2)  # the move note, then the reason
		self.assertIn("Scheduled over a conflict on the Maintenance Planner", contract.comments[1])
		self.assertIn("Reason: Customer asked", contract.comments[1])

	def test_a_move_that_fits_is_unchanged(self):
		contract = _contract([_row("A", "2026-10-20", name="r1")])
		self.engine.cells = {"RES-1": {"2026-11-03": _cell(8, [_task(2)])}}
		self.assertEqual(
			planner.move_projected("MNT-CON-1", "2026-10-20", "2026-11-03"), {"moved": 1, "drafted": None}
		)
		self.assertEqual(len(contract.comments), 1)

	def test_a_contract_with_no_default_technician_has_nobody_to_overbook(self):
		frappe.defaults = {}
		_contract([_row("A", "2026-10-20", name="r1")])
		planner.move_projected("MNT-CON-1", "2026-10-20", "2026-11-03")
		self.assertEqual(self.engine.availability_calls, [])

	def test_a_refusal_comes_before_any_conflict_check(self):
		_contract([_row("A", "2026-10-20", name="r1")])
		with self.assertRaises(_Throw):
			planner.move_projected("MNT-CON-1", "2026-10-19", "2026-11-03")  # stale card
		self.assertEqual(self.engine.availability_calls, [])


class TestTechnicianView(unittest.TestCase):
	"""``get_planner`` says which Planner Resource each technician is, and adds Field helpers."""

	def setUp(self):
		_reset()
		self.engine = _FakeEngine()
		self.engine.install()
		self.addCleanup(self.engine.restore)
		self.techs = [{"user": "austin@example.com", "name": "Austin Healey", "enabled": True}]
		self.resources = [
			{
				"name": "RES-1",
				"label": "Austin",
				"group": "PM",
				"user": "austin@example.com",
				"color": "#2563eb",
			},
			{
				"name": "RES-3",
				"label": "Korben",
				"group": "Field",
				"user": "korben@example.com",
				"color": None,
			},
			{
				"name": "RES-4",
				"label": "Jesse",
				"group": "Field",
				"user": "jesse@example.com",
				"color": "#0d9488",
			},
			{"name": "RES-5", "label": "Ana", "group": "Design", "user": "ana@example.com", "color": None},
			{"name": "RES-6", "label": "Crew", "group": "Field", "user": None, "color": None},
		]
		self.days = {
			"RES-1": {"2026-10-12": dict(_cell(8, [_task(6)]), drive_minutes=70, long_drive=True)},
			"RES-3": {"2026-10-12": _cell(8, [])},
			"RES-4": {"2026-10-12": _cell(8, [])},
		}
		u2r = {r["user"]: r["name"] for r in self.resources if r["user"]}

		def availability(start, end, resources=None):
			return {"resources": self.resources, "days": self.days, "user_to_resource": u2r}

		sys.modules[ENGINE_MODULE].availability = availability

	def test_technicians_carry_their_resource_and_field_helpers_are_added(self):
		view = planner._project_view(self.techs, D(2026, 10, 12), D(2026, 10, 12))
		people = {p["user"]: p for p in view["technicians"]}
		# The technician first, then the Field helpers by name; a Design resource or one with no user is not offered.
		self.assertEqual(
			[p["user"] for p in view["technicians"]],
			["austin@example.com", "jesse@example.com", "korben@example.com"],
		)
		self.assertEqual(people["austin@example.com"]["resource"], "RES-1")
		self.assertEqual(people["austin@example.com"]["group"], "PM")
		self.assertEqual(people["austin@example.com"]["color"], "#2563eb")
		self.assertNotIn("helper", people["austin@example.com"])
		self.assertEqual(
			people["korben@example.com"],
			{
				"user": "korben@example.com",
				"name": "Korben",
				"enabled": True,
				"resource": "RES-3",
				"group": "Field",
				"color": None,
				"helper": True,
			},
		)
		# Their hours are in the same response.
		self.assertEqual(
			sorted(view["bookings"]), ["austin@example.com", "jesse@example.com", "korben@example.com"]
		)

	def test_a_field_technician_is_not_listed_twice(self):
		self.techs.append({"user": "korben@example.com", "name": "Korben Dallas", "enabled": True})
		view = planner._project_view(self.techs, D(2026, 10, 12), D(2026, 10, 12))
		korben = [p for p in view["technicians"] if p["user"] == "korben@example.com"]
		self.assertEqual(len(korben), 1)
		self.assertEqual(korben[0]["name"], "Korben Dallas")

	def test_day_cells_carry_drive_time_only_when_the_engine_does(self):
		bookings = planner._project_bookings(self.techs, D(2026, 10, 12), D(2026, 10, 12))
		with_drive = bookings["austin@example.com"]["2026-10-12"]
		self.assertEqual((with_drive["drive_minutes"], with_drive["long_drive"]), (70, True))
		self.assertNotIn("drive_source", with_drive)  # not invented
		plain = bookings["korben@example.com"]["2026-10-12"]
		for key in ("drive_minutes", "drive_source", "long_drive", "unlocated"):
			self.assertNotIn(key, plain)
		self.assertEqual(plain["warnings"], [])

	def test_drive_padding_is_not_a_card_but_still_counts_as_booked(self):
		drive = {
			"kind": "drive",
			"ref": None,
			"label": "Driving",
			"project": None,
			"hours": 1.0,
			"slot": None,
		}
		self.days["RES-1"]["2026-10-12"] = _cell(8, [_task(6), drive])
		cell = planner._project_bookings(self.techs, D(2026, 10, 12), D(2026, 10, 12))["austin@example.com"][
			"2026-10-12"
		]
		self.assertEqual([item["kind"] for item in cell["items"]], ["task"])
		self.assertEqual(cell["booked"], 7.0)

	def test_a_technician_without_a_resource_still_lists(self):
		self.techs.append({"user": "lee@example.com", "name": "Lee", "enabled": True})
		view = planner._project_view(self.techs, D(2026, 10, 12), D(2026, 10, 12))
		lee = next(p for p in view["technicians"] if p["user"] == "lee@example.com")
		self.assertIsNone(lee["resource"])
		self.assertNotIn("lee@example.com", view["bookings"])

	def test_an_engine_failure_still_returns_the_technicians(self):
		def broken(start, end, resources=None):
			raise RuntimeError("project side broke")

		sys.modules[ENGINE_MODULE].availability = broken
		view = planner._project_view(self.techs, D(2026, 10, 12), D(2026, 10, 12))
		self.assertEqual(view["bookings"], {})
		self.assertEqual([p["user"] for p in view["technicians"]], ["austin@example.com"])
		self.assertIsNone(view["technicians"][0]["resource"])
		self.assertEqual(len(self.engine.logged), 1)


class TestPlannerParity(unittest.TestCase):
	"""The page has the Project Planner's features (P1.7). Read as source, like test_project_planner_page."""

	@classmethod
	def setUpClass(cls):
		cls.code = (PAGE_DIR / "maintenance_planner.js").read_text(encoding="utf-8")

	def _body(self, header, until):
		start = self.code.index(header)
		return self.code[start : self.code.index(until, start + len(header))]

	def test_the_crew_view_is_a_route_like_month_and_week(self):
		code = self.code
		self.assertIn('views: ["month", "week", "crew"]', code)
		self.assertIn("MP.views.includes(route[1])", code)
		self.assertIn('["crew", __("Crew")]', code)
		self.assertIn("/crew/2026-10-05", code)
		# One week, whichever of week and crew is showing.
		self.assertIn('if (this.view !== "month") {', self._body("range() {", "range_days() {"))
		# Views change only through frappe.set_route, so Back and Forward step through them.
		self.assertIn("frappe.set_route(MP.route, view, anchor)", code)
		for forbidden in ("pushState", "replaceState", "location.hash", "window.location"):
			self.assertNotIn(forbidden, code)

	def test_the_resources_panel_shows_what_the_project_planner_shows(self):
		code = self.code
		panel = self._body(
			"render_panel() {",
			"// ------------------------------------------------------------------ crew view",
		)
		self.assertIn('__("Resources available")', panel)
		self.assertIn('this.view !== "crew"', panel)  # the crew view's rows are the panel
		for text in ("{0}h free", "Full", "Over {0}h", "Holiday", "Time off", "Not a work day"):
			self.assertIn(f'__("{text}"', code)
		# Remembered open or closed, behind the same try/catch as every other pref.
		self.assertIn('panel_key: "ee_maintenance_planner_panel"', code)
		self.assertIn('this.load_pref(MP.panel_key, "1") !== "0"', code)
		self.assertIn("this.save_pref(MP.panel_key,", code)
		self.assertEqual(code.count("localStorage"), 2)  # load_pref and save_pref, nowhere else
		# The numbers are the engine's.
		self.assertIn("this.data.bookings", code)
		self.assertIn("this.avail_state(cell)", panel)
		# The week panel has a capacity bar; the month panel a compact strip.
		self.assertIn('class="mp-bar"', panel)
		self.assertIn("mp-mini", panel)

	def test_days_off_show_on_the_calendar(self):
		code = self.code
		calendar_ = self._body("render_calendar(cards) {", "append_off_badges($day, planned, ymd) {")
		self.assertIn("mp-off-day", calendar_)
		self.assertIn("this.off_label(cell)", calendar_)
		self.assertIn("this.append_off_badges($day, planned, ymd)", calendar_)
		self.assertIn("mp-tech-off", code)
		self.assertIn('__("Everyone off")', code)
		# A visit on a day its technician does not work says so.
		self.assertIn('__("Day off")', self._body("card_html(card) {", "tech_name(user) {"))

	def test_drive_time_is_shown_when_present_and_not_required(self):
		code = self.code
		self.assertIn("drive_line(cell) {", code)
		self.assertIn("cell.drive_minutes", code)
		self.assertIn('__("{0} drive"', code)
		self.assertIn('__("Long drive")', code)
		self.assertIn("cell.long_drive", code)
		# Nothing is drawn for a day without a figure.
		self.assertIn('if (minutes <= 0) return "";', code)

	def test_the_crew_view_has_a_row_per_technician_and_drops_on_cells(self):
		crew = self._body("render_crew(cards) {", "compare(a, b) {")
		self.assertIn('class="mp-cell', crew)
		self.assertIn('data-user="${mp_esc(user)}"', crew)
		self.assertIn("this.person_html(person, true)", crew)
		self.assertIn('__("Unassigned")', crew)
		find = self._body("find_target(x, y) {", "mark_target(target) {")
		self.assertIn(".mp-cell[data-date]", find)
		# A visit dropped on another technician's row changes the technician (and the date).
		plan = self._body("plan_drop(source, target) {", "drop(source, target) {")
		self.assertIn("change.technician = target.user", plan)
		self.assertIn('(target.user || "") !== (card.technician || "")', plan)

	def test_a_projected_visit_cannot_change_technician(self):
		plan = self._body("plan_drop(source, target) {", "drop(source, target) {")
		self.assertEqual(plan.count("follows the technician on the site's Maintenance Profile"), 2)
		# Refused before anything is sent: the guard sits ahead of the technician change.
		self.assertLess(
			plan.index('card.kind === "projected"'), plan.index("change.technician = target.user")
		)
		# And the dialog offers a technician only for a visit record.
		dialog = self._body("open_card(card) {", "\n}\n")
		self.assertRegex(
			dialog, r'if \(card\.kind === "visit"\) \{\s+fields\.push\(\{\s+fieldtype: "Select",'
		)

	def test_a_technician_chip_dragged_onto_a_visit_assigns_it(self):
		code = self.code
		self.assertIn('el.closest(".mp-person[data-user]")', code)
		self.assertIn('kind: "person"', code)
		plan = self._body("plan_drop(source, target) {", "drop(source, target) {")
		self.assertIn("change: { technician: source.user }", plan)
		self.assertIn(
			'el.closest(".mp-card[data-key]")', self._body("find_target(x, y) {", "mark_target(target) {")
		)
		# Started and finished visits keep their technician.
		self.assertIn("card.movable", plan)

	def test_overbooking_asks_for_a_reason_and_never_blocks(self):
		code = self.code
		send = self._body("send(method, args, opts) {", "ask_reason(conflicts, noun) {")
		self.assertIn("result.needs_reason", send)
		self.assertIn("Object.assign({}, args, { reason })", send)
		# One retry only: a server that asks again after a reason is not looped on.
		self.assertIn("if (args.reason) {", send)
		ask = self._body(
			"ask_reason(conflicts, noun) {",
			"// ------------------------------------------------------------------ undo",
		)
		self.assertIn('__("Save anyway")', ask)
		self.assertIn('fieldname: "reason"', ask)
		self.assertIn("reqd: 1", ask)
		self.assertIn("dialog.onhide = () => settle(null)", ask)
		# The conflict text is escaped on its way into the dialog.
		self.assertIn("mp_esc(text)", ask)
		self.assertIn("mp_esc(who)", ask)
		# Visits and contracts both go through it.
		self.assertIn('visit ? "move_visit" : "move_projected"', code)

	def test_undo_keeps_twenty_moves_and_sends_the_old_values_back(self):
		code = self.code
		self.assertIn("undo_max: 20", code)
		self.assertIn('undo_reason: "Undo on the Maintenance Planner"', code)
		self.assertIn("while (this.undo_stack.length > MP.undo_max) this.undo_stack.shift()", code)
		save = self._body("save_move(card, change, before) {", "send(method, args, opts) {")
		# The snapshot is taken from the card as it was, before the optimistic move.
		self.assertIn('const before = { date: card.date || null, technician: card.technician || "" };', code)
		self.assertIn("date: before.date, technician: before.technician", save)
		undo = self._body(
			"undo() {", "// ------------------------------------------------------------------ dialog"
		)
		self.assertIn('"move_visit"', undo)
		self.assertIn('"move_projected"', undo)
		for key in (
			"record: snap.record",
			"modified:",
			"technician: snap.technician",
			"args.date = snap.date",
		):
			self.assertIn(key, undo)
		for key in ("contract: snap.contract", "from_date: snap.from_date", "to_date: snap.to_date"):
			self.assertIn(key, undo)
		# A conflict on the way back is answered for the person who pressed Undo.
		self.assertEqual(undo.count("auto_reason: reason"), 2)
		self.assertIn("const reason = __(MP.undo_reason)", undo)
		# Moving to a day that has passed is refused by the server, so the page does not try.
		self.assertIn("snap.date >= today", undo)

	def test_the_route_icon_opens_the_project_planners_route_view(self):
		code = self.code
		self.assertIn('project_route: "project-planner"', code)
		self.assertIn('frappe.set_route(MP.project_route, "route", resource, ymd)', code)
		self.assertIn("data-route-resource", code)
		self.assertIn("data-route-date", code)
		# In the week panel and every crew cell; the month strip is clickable as a whole.
		panel = self._body(
			"render_panel() {",
			"// ------------------------------------------------------------------ crew view",
		)
		self.assertIn("this.drive_html(person, ymd, cell)", panel)
		self.assertIn(
			"this.drive_html(person, ymd, cell)", self._body("render_crew(cards) {", "compare(a, b) {")
		)
		# A technician with no Planner Resource has no route to open.
		self.assertIn("!person.resource", code)
		# The Project Planner page is open to Maintenance User, so a technician can follow the link.
		page = json.loads(
			(APP / "project_enhancements/page/project_planner/project_planner.json").read_text(
				encoding="utf-8"
			)
		)
		self.assertIn("Maintenance User", {row["role"] for row in page["roles"]})

	def test_dragging_is_pointer_events_not_html5(self):
		code = self.code
		for forbidden in (
			"draggable",
			"dragstart",
			"dragover",
			"ondrop",
			"dataTransfer",
			'addEventListener("drop"',
		):
			self.assertNotIn(forbidden, code)
		for needed in ("pointerdown", "pointermove", "pointerup", "pointercancel"):
			self.assertIn(needed, code)

	def test_prefs_are_guarded_and_calls_match_the_api(self):
		code = self.code
		for helper in ("load_pref(key, fallback) {", "save_pref(key, value) {"):
			body = code[code.index(helper) :]
			self.assertLess(body.index("try {"), body.index("catch (e)"), helper)

		def params(fn):
			return set(inspect.signature(fn).parameters)

		self.assertTrue({"record", "date", "technician", "modified", "reason"} <= params(planner.move_visit))
		self.assertTrue(
			{"contract", "from_date", "to_date", "serial_no", "reason"} <= params(planner.move_projected)
		)
		self.assertEqual(params(planner.get_planner), {"start", "end"})
		# The page calls exactly these three methods of its API.
		self.assertEqual(set(re.findall(r'this\.send\(\s*"(\w+)"', code)), {"move_projected", "move_visit"})
		self.assertIn('visit ? "move_visit" : "move_projected"', code)
		self.assertIn("frappe.call({ method: MP.methods[method], args })", code)
		methods = self._body("MP.methods = {", "};")
		found = set(re.findall(r"(\w+): `\$\{MP\.api\}\.(\w+)`", methods))
		self.assertEqual(found, {(name, name) for name in ("get_planner", "move_visit", "move_projected")})


class TestWiring(unittest.TestCase):
	def test_planner_roles_are_the_roles_that_read_every_record(self):
		permissions = importlib.import_module("erpnext_enhancements.sapphire_maintenance.permissions")
		self.assertEqual(planner.PLANNER_ROLES, permissions.VIEW_ALL_ROLES)
		page = json.loads((PAGE_DIR / "maintenance_planner.json").read_text(encoding="utf-8"))
		self.assertEqual({row["role"] for row in page["roles"]}, planner.PLANNER_ROLES)
		self.assertEqual(page["module"], "Sapphire Maintenance")

	def test_the_page_moves_by_route_and_never_by_its_own_history(self):
		code = (PAGE_DIR / "maintenance_planner.js").read_text(encoding="utf-8")
		self.assertNotIn("pushState", code)
		self.assertNotIn("replaceState", code)
		self.assertIn("frappe.set_route(MP.route, view, anchor)", code)
		self.assertIn("on_page_show", code)
		for method in ("get_planner", "move_visit", "move_projected"):
			self.assertIn(f"${{MP.api}}.{method}", code)

	def test_the_workspace_links_the_planner(self):
		workspace = json.loads(WORKSPACE.read_text(encoding="utf-8"))
		self.assertIn("maintenance-planner", [s["link_to"] for s in workspace["shortcuts"]])
		self.assertIn("maintenance-planner", [link.get("link_to") for link in workspace["links"]])
		# A workspace JSON whose `modified` is not newer than the site's row is skipped on migrate.
		self.assertGreaterEqual(workspace["modified"], "2026-10-07")


class TestSelfApproval(unittest.TestCase):
	def test_every_fixture_transition_states_self_approval(self):
		# A fixture import applies no field defaults, so an omitted flag lands as 0, not
		# Frappe's default of 1. That is how the maintenance workflow shipped.
		workflows = json.loads((APP / "fixtures/workflow.json").read_text(encoding="utf-8"))
		for workflow in workflows:
			for transition in workflow["transitions"]:
				with self.subTest(workflow=workflow["name"], action=transition["action"]):
					self.assertIn("allow_self_approval", transition)

	def test_a_technician_sends_their_own_visit_for_review_but_does_not_approve_it(self):
		workflows = json.loads((APP / "fixtures/workflow.json").read_text(encoding="utf-8"))
		maintenance = next(w for w in workflows if w["name"] == "Sapphire Maintenance Workflow")
		self.assertEqual(
			{t["action"]: t["allow_self_approval"] for t in maintenance["transitions"]},
			{"Request Review": 1, "Approve & Submit": 0, "Send Back": 1},
		)

	def test_finish_skips_a_transition_the_user_cannot_apply(self):
		source = (APP / "api/maintenance_visit.py").read_text(encoding="utf-8")
		self.assertIn("has_approval_access(user, doc, t)", source)
		self.assertIn('doc = apply_workflow(doc, usable[0]["action"])', source)
		self.assertNotIn("transitions[0]", source)


class TestReviewerWorkflow(unittest.TestCase):
	"""Only the office reviewers approve a visit (v1.576.2).

	Every technician holds Projects Manager, so approval keyed on it let a technician approve a
	colleague's visit, or their own whenever the scheduler had drafted it. The reviewers named on
	2026-10-07 (Lisa, James, Nik, Clegg) hold "Maintenance Reviewer" instead.
	"""

	def _maintenance(self):
		workflows = json.loads((APP / "fixtures/workflow.json").read_text(encoding="utf-8"))
		return next(w for w in workflows if w["name"] == "Sapphire Maintenance Workflow")

	def test_only_reviewers_approve_edit_a_pending_visit_or_send_it_back(self):
		workflow = self._maintenance()
		self.assertEqual(
			{s["state"]: s["allow_edit"] for s in workflow["states"]}["Pending Review"],
			"Maintenance Reviewer",
		)
		by_action = {t["action"]: t for t in workflow["transitions"]}
		self.assertEqual(by_action["Request Review"]["allowed"], "Maintenance User")
		self.assertEqual(by_action["Approve & Submit"]["allowed"], "Maintenance Reviewer")
		self.assertEqual(by_action["Approve & Submit"]["allow_self_approval"], 0)
		back = by_action["Send Back"]
		self.assertEqual(
			(back["state"], back["next_state"], back["allowed"]),
			("Pending Review", "Draft", "Maintenance Reviewer"),
		)
		# finish_visit takes the first usable forward transition, so approval must come first.
		from_pending = [t["action"] for t in workflow["transitions"] if t["state"] == "Pending Review"]
		self.assertEqual(from_pending[0], "Approve & Submit")

	def test_every_workflow_action_has_a_master_fixture_and_is_exported(self):
		actions = {
			t["action"]
			for w in json.loads((APP / "fixtures/workflow.json").read_text(encoding="utf-8"))
			for t in w["transitions"]
		}
		masters = {
			m["name"]
			for m in json.loads((APP / "fixtures/workflow_action_master.json").read_text(encoding="utf-8"))
		}
		self.assertLessEqual(actions, masters)
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		start = hooks.index('"dt": "Workflow Action Master"')
		exported = hooks[start : hooks.index("}", start)]
		for action in actions:
			self.assertIn(f'"{action}"', exported, action)

	def test_finish_never_sends_a_visit_back(self):
		source = (APP / "api/maintenance_visit.py").read_text(encoding="utf-8")
		self.assertIn('if t.get("next_state") != start', source)

	def test_the_seed_runs_before_fixture_sync(self):
		lines = (APP / "patches.txt").read_text(encoding="utf-8").splitlines()
		self.assertGreater(
			lines.index("erpnext_enhancements.patches.seed_maintenance_reviewer_role"),
			lines.index("[post_model_sync]"),
		)

	def test_the_seed_creates_the_role_and_the_action_once_and_never_raises(self):
		inserted = []

		class _New(_Doc):
			def insert(self, ignore_permissions=False):
				if frappe.fail_insert:
					raise RuntimeError("database went away")
				inserted.append((self.doctype, dict(self)))
				frappe.existing.add((self.doctype, self.get("role_name") or self.get("workflow_action_name")))

		frappe.existing = set()
		frappe.fail_insert = False
		frappe.new_doc = lambda doctype: _New(doctype=doctype)
		frappe.get_traceback = lambda: "traceback"
		logged = []
		frappe.log_error = lambda *a, **k: logged.append(k)
		frappe.db = types.SimpleNamespace(
			exists=lambda doctype, name: (doctype, name) in frappe.existing,
			commit=lambda: None,
			rollback=lambda: None,
		)
		seed = importlib.import_module("erpnext_enhancements.patches.seed_maintenance_reviewer_role")
		seed.execute()
		self.assertEqual(
			[(doctype, row.get("role_name") or row.get("workflow_action_name")) for doctype, row in inserted],
			[("Role", "Maintenance Reviewer"), ("Workflow Action Master", "Send Back")],
		)
		self.assertEqual(inserted[0][1]["desk_access"], 1)
		seed.execute()
		self.assertEqual(len(inserted), 2)
		frappe.existing.clear()
		frappe.fail_insert = True
		seed.execute()  # a failing insert is logged, never raised into the migrate
		self.assertEqual(logged[0]["title"], "Maintenance reviewer role seed")
		_reset()
