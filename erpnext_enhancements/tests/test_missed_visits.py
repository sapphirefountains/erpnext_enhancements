# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for missed and stale visits (``sapphire_maintenance/missed_visits.py``).

A2 of the 2026-10-12 Maintenance tools plan (TASK-2026-02475). The Missed Maintenance Visits
report and the Visit Review page both list these rows, so the rules are pinned once here:

* **Not started** and **Started, not sent**: open drafts dated before today, split on whether
  anything is filled in. Today's visits, visits waiting for review and unscheduled drafts are
  not late.
* **Not drafted**: a contract visit that fell due with nothing drafted for it, by the nightly
  scheduler's own dedupe rule (an open *regular* draft), one row per site on a Per Site Visit
  contract, nothing inside a paused contract's winter window, nothing for a customer on hold
  or a contract not started yet, and seasonal visits whose month went by undrafted.

Runs the real ``missed_visits``, ``maintenance_scheduling`` and contract controller over an
in-memory ``frappe`` stub installed in ``setUpModule``.

Run: python -m unittest erpnext_enhancements.tests.test_missed_visits
"""

import datetime
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

D = datetime.date
TODAY = D(2026, 10, 12)
RECORD = "Sapphire Maintenance Record"
CONTRACT = "Sapphire Maintenance Contract"
CONTRACT_MODULE = "erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract.sapphire_maintenance_contract"
STUBBED = (
	"frappe",
	"frappe.utils",
	"frappe.model",
	"frappe.model.document",
	"erpnext_enhancements.sapphire_maintenance.missed_visits",
	"erpnext_enhancements.api.maintenance_scheduling",
	CONTRACT_MODULE,
)

missed = None
ROWS = {}
HOLDS = set()
_saved = {}


class _Dict(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


def _getdate(value=None):
	if value is None:
		return TODAY
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, D):
		return value
	return D.fromisoformat(str(value)[:10])


def _is_set(value):
	return value not in (None, "")


def _match(row, filters):
	for field, cond in (filters or {}).items():
		value = row.get(field)
		if isinstance(cond, (list, tuple)):
			op, arg = cond
			if op == "is":
				if (arg == "set") != _is_set(value):
					return False
			elif op == "in":
				if value not in arg:
					return False
			elif op == "!=":
				if value == arg:
					return False
			elif op == ">=":
				if str(value or "") < str(arg):
					return False
			else:
				raise AssertionError(f"stub has no operator {op!r}")
		elif value != cond:
			return False
	return True


def _get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
	rows = [row for row in ROWS.get(doctype, []) if _match(row, filters)]
	if pluck:
		return [row.get(pluck) for row in rows]
	return [_Dict({f: row.get(f) for f in fields or ["name"]}) for row in rows]


def _get_doc(doctype, name):
	return next(row for row in ROWS[doctype] if row.name == name)


def _get_value(doctype, name, field=None, **kwargs):
	if doctype == "Customer" and field == "custom_service_hold":
		return 1 if name in HOLDS else 0
	return None


def _forget_package_attributes():
	"""``from package import name`` prefers a stale package attribute to a fresh import, and a
	module imported under another suite's stub would read that suite's ``frappe``."""
	import erpnext_enhancements.api as api
	import erpnext_enhancements.sapphire_maintenance as package

	for owner, attr in ((package, "missed_visits"), (api, "maintenance_scheduling")):
		if attr in vars(owner):
			delattr(owner, attr)


def setUpModule():
	global missed
	for name in STUBBED:
		_saved[name] = sys.modules.pop(name, None)
	_forget_package_attributes()

	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.__dict__["_"] = lambda text, *a, **k: text
	frappe.get_all = _get_all
	frappe.get_doc = _get_doc
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.db = types.SimpleNamespace(get_value=_get_value, set_value=lambda *a, **k: None)

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(float(v or 0))
	utils.flt = lambda v, *a: float(v or 0)
	utils.getdate = _getdate
	utils.nowdate = lambda: str(TODAY)
	utils.add_days = lambda d, n: _getdate(d) + datetime.timedelta(days=n)
	utils.add_months = lambda d, n: _getdate(d)
	utils.add_years = lambda d, n: _getdate(d)
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = type("Document", (), {})
	model.document = document
	frappe.model = model

	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.utils": utils,
			"frappe.model": model,
			"frappe.model.document": document,
		}
	)
	from erpnext_enhancements.sapphire_maintenance import missed_visits

	missed = missed_visits


def tearDownModule():
	_forget_package_attributes()
	for name in STUBBED:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


# ------------------------------------------------------------------ fixtures


def _draft(name, day=None, completion=0, state="Draft", label=None, serial=None, project="PRJ-1", **extra):
	row = _Dict(
		name=name,
		docstatus=0,
		workflow_state=state,
		visit_label=label,
		project=project,
		serial_no=serial,
		technician="austin@example.com",
		visit_date=None,
		scheduled_visit_date=day,
		completion_percent=completion,
		maintenance_contract=extra.pop("contract", None),
		creation="2026-10-01 08:00:00",
	)
	row.update(extra)
	ROWS.setdefault(RECORD, []).append(row)
	return row


def _contract(name="MNT-1", shape="Per Feature", features=(), project="PRJ-1", **extra):
	row = _Dict(
		name=name,
		status="Active",
		project=project,
		customer="CUST-1",
		visit_shape=shape,
		start_date="2026-01-01",
		creation="2026-01-01 08:00:00",
		pause_over_winter=0,
		winterization_month="October",
		startup_month="April",
		seasonal_startup=0,
		winterization=0,
		startup_last_generated_year=0,
		winterization_last_generated_year=0,
		seasonal_visits=[],
		covered_features=[_Dict(serial_no=serial, next_visit_date=due) for serial, due in features],
	)
	row.update(extra)
	ROWS.setdefault(CONTRACT, []).append(row)
	return row


class _Base(unittest.TestCase):
	def setUp(self):
		ROWS.clear()
		HOLDS.clear()
		ROWS["Sapphire Maintenance Profile"] = [
			_Dict(project="PRJ-1", default_technician="austin@example.com")
		]
		ROWS["Project"] = [_Dict(name="PRJ-1", project_name="Highlands Plaza")]
		ROWS["Serial No"] = [_Dict(name="SN-EAST", item_name="East Fountain")]
		ROWS["User"] = [_Dict(name="austin@example.com", full_name="Austin Healey")]

	def rows(self, **kwargs):
		return missed.missed_visits(TODAY, **kwargs)

	def kinds(self, **kwargs):
		return [(row["kind"], row["record"] or row["contract"]) for row in self.rows(**kwargs)]


class TestLateDrafts(_Base):
	def test_a_past_draft_with_nothing_filled_in_is_not_started(self):
		_draft("V-1", "2026-10-07")
		(row,) = self.rows()
		self.assertEqual(
			(row["kind"], row["days_late"], row["due_date"]), (missed.KIND_NOT_STARTED, 5, D(2026, 10, 7))
		)
		self.assertEqual((row["site"], row["technician_name"]), ("Highlands Plaza", "Austin Healey"))

	def test_a_past_draft_with_answers_is_started_not_sent(self):
		_draft("V-1", "2026-10-09", completion=40)
		self.assertEqual(self.kinds(), [(missed.KIND_NOT_SENT, "V-1")])

	def test_the_day_the_work_was_done_wins_over_the_scheduled_day(self):
		_draft("V-1", "2026-10-20", visit_date="2026-10-08", completion=10)
		self.assertEqual(self.rows()[0]["due_date"], D(2026, 10, 8))

	def test_todays_waiting_and_unscheduled_visits_are_not_late(self):
		_draft("TODAY", "2026-10-12")
		_draft("FUTURE", "2026-10-19")
		_draft("WAITING", "2026-10-06", completion=100, state="Pending Review")
		_draft("UNSCHEDULED", None)
		self.assertEqual(self.rows(), [])

	def test_a_late_seasonal_draft_is_listed_with_its_label(self):
		_draft("W-1", "2026-10-07", label="Winterization")
		self.assertEqual(self.rows()[0]["visit_label"], "Winterization")

	def test_most_overdue_first_and_the_filters(self):
		_draft("NEWER", "2026-10-09")
		_draft("OLDER", "2026-10-01", completion=20)
		_draft("BOB", "2026-10-02", technician="bob@example.com")
		self.assertEqual([row["record"] for row in self.rows()], ["OLDER", "BOB", "NEWER"])
		self.assertEqual([row["record"] for row in self.rows(technician="bob@example.com")], ["BOB"])
		self.assertEqual([row["record"] for row in self.rows(kind=missed.KIND_NOT_SENT)], ["OLDER"])


class TestNotDrafted(_Base):
	def test_a_due_fountain_with_no_open_regular_draft_was_never_drafted(self):
		_contract(features=[("SN-EAST", "2026-10-05")])
		(row,) = self.rows()
		self.assertEqual(
			(row["kind"], row["contract"], row["record"]), (missed.KIND_NOT_DRAFTED, "MNT-1", None)
		)
		self.assertEqual((row["fountain"], row["days_late"]), ("East Fountain", 7))

	def test_an_open_regular_draft_stands_for_the_fountain(self):
		"""Even one waiting for review, or dated in the future: the scheduler's rule."""
		_contract(features=[("SN-EAST", "2026-10-05")])
		_draft("V-1", "2026-10-19", serial="SN-EAST", contract="MNT-1")
		self.assertEqual(self.kinds(), [])

	def test_an_open_labelled_draft_does_not(self):
		_contract(features=[("SN-EAST", "2026-10-05")])
		_draft("FOLLOW-UP", "2026-10-19", serial="SN-EAST", label="Chemistry Follow-Up", contract="MNT-1")
		self.assertEqual(self.kinds(), [(missed.KIND_NOT_DRAFTED, "MNT-1")])

	def test_due_today_is_not_missed_yet(self):
		_contract(features=[("SN-EAST", "2026-10-12")])
		self.assertEqual(self.rows(), [])

	def test_a_site_visit_contract_is_one_row_from_its_earliest_due_fountain(self):
		_contract(shape="Per Site Visit", features=[("SN-EAST", "2026-10-08"), ("SN-WEST", "2026-10-03")])
		(row,) = self.rows()
		self.assertEqual((row["due_date"], row["fountain"]), (D(2026, 10, 3), "Whole site"))

	def test_a_site_visit_contract_with_an_open_regular_draft_is_covered(self):
		_contract(shape="Per Site Visit", features=[("SN-EAST", "2026-10-08")])
		_draft("V-1", "2026-10-19", contract="MNT-1")
		self.assertEqual(self.rows(), [])

	def test_a_paused_contract_shows_nothing_in_its_winter_window(self):
		_contract(features=[("SN-EAST", "2026-10-05")], pause_over_winter=1, winterization_month="October")
		self.assertEqual(self.rows(), [])

	def test_a_paused_contract_still_shows_a_visit_missed_before_its_window(self):
		_contract(features=[("SN-EAST", "2026-09-28")], pause_over_winter=1, winterization_month="October")
		self.assertEqual(self.kinds(), [(missed.KIND_NOT_DRAFTED, "MNT-1")])

	def test_a_customer_on_hold_and_a_contract_not_started_are_skipped(self):
		_contract(name="HELD", features=[("SN-EAST", "2026-10-05")], customer="CUST-HOLD")
		HOLDS.add("CUST-HOLD")
		_contract(name="LATER", features=[("SN-EAST", "2026-10-05")], start_date="2026-11-01")
		self.assertEqual(self.rows(), [])


class TestSeasonalNotDrafted(_Base):
	def test_a_month_gone_by_with_no_stamp_and_no_record(self):
		_contract(seasonal_startup=1, startup_month="April")
		(row,) = self.rows()
		self.assertEqual(
			(row["kind"], row["visit_label"], row["due_date"], row["fountain"]),
			(missed.KIND_NOT_DRAFTED, "Seasonal Startup", D(2026, 4, 1), None),
		)

	def test_stamped_for_this_year_is_drafted(self):
		_contract(seasonal_startup=1, startup_month="April", startup_last_generated_year=2026)
		self.assertEqual(self.rows(), [])

	def test_a_record_made_by_hand_counts(self):
		_contract(seasonal_startup=1, startup_month="April")
		ROWS[RECORD] = [
			_Dict(
				name="HAND",
				docstatus=1,
				visit_label="Seasonal Startup",
				maintenance_contract="MNT-1",
				creation="2026-04-03 08:00:00",
			)
		]
		self.assertEqual(self.rows(), [])

	def test_this_month_is_tonights_scheduler_run_not_missed(self):
		_contract(winterization=1, winterization_month="October")
		self.assertEqual(self.rows(), [])

	def test_a_contract_started_after_that_month_never_owed_it(self):
		"""Contracts entered in September 2026 never had an April startup in ERPNext."""
		_contract(seasonal_startup=1, startup_month="April", start_date="2026-09-09")
		self.assertEqual(self.rows(), [])

	def test_a_contract_started_part_way_through_the_month_still_owed_it(self):
		_contract(seasonal_startup=1, startup_month="April", start_date="2026-04-15")
		self.assertEqual(self.kinds(), [(missed.KIND_NOT_DRAFTED, "MNT-1")])

	def test_custom_seasonal_rows_count_too(self):
		_contract(
			seasonal_visits=[
				_Dict(name="ROW-1", visit_label="Fall Clean", target_month="September", last_generated_year=0)
			]
		)
		self.assertEqual(self.rows()[0]["visit_label"], "Fall Clean")


if __name__ == "__main__":
	unittest.main()
