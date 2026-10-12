# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests: a visit is dated and clocked out when it goes for review (v1.595.1).

Scheduler-drafted visits reached review with no ``visit_date``, and nothing was settled until
a reviewer approved them, often days later. So the contract's next visit was counted from the
approval day (State Capitol: sent for review 2026-10-09, approved 2026-10-12), and the clock-out
autofill took whatever kiosk interval the technician had at approval time, possibly the next
visit's. ``SapphireMaintenanceRecord._finish_on_review`` settles both on the move into Pending
Review. Pinned here, through the real ``validate`` and ``before_submit``:

* the move into review dates a visit with no date, and keeps a date the technician chose;
* it closes the clock from the kiosk interval then, while that interval is this visit's;
* an approval days later leaves the clock alone, even with a later interval on the project;
* no other save stamps anything (autosaves, a reviewer's edit, Send Back);
* the patch dates visits already waiting, from when they went for review.

Stubs a minimal ``frappe`` in ``setUpModule``; runs the real controller and patch.

Run: python -m unittest erpnext_enhancements.tests.test_visit_finished_for_review
"""

import datetime
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
RECORD_MODULE = "erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record.sapphire_maintenance_record"
PATCH_MODULE = "erpnext_enhancements.patches.stamp_visit_date_on_pending_review"
CREW_MODULE = "erpnext_enhancements.sapphire_maintenance.visit_crew"
STUBBED = (
	"frappe",
	"frappe.utils",
	"frappe.model",
	"frappe.model.document",
	RECORD_MODULE,
	PATCH_MODULE,
	CREW_MODULE,
)

record_module = None
patch = None
STATE = {}
_saved = {}

D = datetime.date
DT = datetime.datetime


class _Dict(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class _Document:
	"""Just enough of frappe's Document: attributes, ``get`` and the change test."""

	def __init__(self, **fields):
		self.__dict__.update(fields)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)

	def has_value_changed(self, field):
		return field in self.__dict__.get("_changed", set())


TODAY = D(2026, 10, 12)
NOW = DT(2026, 10, 12, 10, 30)


def _reset(today=TODAY, now=NOW):
	STATE.clear()
	STATE.update(
		{
			"today": today,
			"now": now,
			"intervals": [],
			"records": [],
			"comments": [],
			"set_values": [],
			"errors": [],
		}
	)


def _interval(status, start, end=None, paused=0):
	STATE["intervals"].append(
		_Dict(
			name=f"JI-{len(STATE['intervals'])}",
			employee="EMP-AUSTIN",
			project="PRJ-1",
			status=status,
			start_time=start,
			end_time=end,
			total_paused_seconds=paused,
		)
	)


def _get_value(doctype, filters, fields=None, order_by=None, as_dict=False, **kwargs):
	if doctype == "Employee":
		return "EMP-AUSTIN" if filters == {"user_id": "austin@example.com"} else None
	if doctype == "Job Interval":
		rows = [
			row
			for row in STATE["intervals"]
			if row.employee == filters["employee"]
			and row.project == filters["project"]
			and row.status in filters["status"][1]
		]
		rows.sort(key=lambda row: row.start_time, reverse=True)
		return rows[0] if rows else None
	if doctype == "Comment":
		rows = [
			c
			for c in STATE["comments"]
			if c.reference_name == filters["reference_name"] and c.content == filters["content"]
		]
		assert order_by == "creation desc", "get_value's default order is not the newest (memory)"
		rows.sort(key=lambda c: c.creation, reverse=True)
		return rows[0].creation if rows else None
	raise AssertionError(doctype)


def _getdate(value=None):
	if value is None:
		return STATE["today"]
	if isinstance(value, DT):
		return value.date()
	if isinstance(value, D):
		return value
	return D.fromisoformat(str(value)[:10])


def setUpModule():
	global record_module, patch
	for name in STUBBED:
		_saved[name] = sys.modules.pop(name, None)
	import erpnext_enhancements.sapphire_maintenance as package

	for attr in ("visit_crew",):
		if attr in vars(package):
			delattr(package, attr)

	frappe = types.ModuleType("frappe")
	frappe.__dict__["_"] = lambda text, *a, **k: text
	frappe._dict = _Dict
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.get_traceback = lambda: "traceback"
	frappe.log_error = lambda *a, **k: STATE["errors"].append(k.get("title"))

	def _get_all(doctype, filters=None, fields=None, **kwargs):
		assert doctype == "Sapphire Maintenance Record"
		assert filters == {
			"docstatus": 0,
			"workflow_state": "Pending Review",
			"visit_date": ["is", "not set"],
		}
		rows = [
			r
			for r in STATE["records"]
			if r.docstatus == 0 and r.workflow_state == "Pending Review" and not r.visit_date
		]
		return [_Dict({f: r.get(f) for f in fields}) for r in rows]

	def _set_value(doctype, name, field, value, update_modified=True):
		STATE["set_values"].append((name, field, value, update_modified))

	frappe.get_all = _get_all
	frappe.db = types.SimpleNamespace(get_value=_get_value, set_value=_set_value, exists=lambda *a: True)

	utils = types.ModuleType("frappe.utils")
	utils.flt = lambda v, *a: float(v or 0)
	utils.getdate = _getdate
	utils.nowdate = lambda: str(STATE["today"])
	utils.now_datetime = lambda: STATE["now"]
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = _Document
	model.document = document
	frappe.model = model

	crew = types.ModuleType(CREW_MODULE)
	crew.validate_crew = lambda doc: None

	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.utils": utils,
			"frappe.model": model,
			"frappe.model.document": document,
			CREW_MODULE: crew,
		}
	)
	from erpnext_enhancements.patches import stamp_visit_date_on_pending_review
	from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record import (
		sapphire_maintenance_record,
	)

	record_module = sapphire_maintenance_record
	patch = stamp_visit_date_on_pending_review


def tearDownModule():
	import erpnext_enhancements.patches as patches
	import erpnext_enhancements.sapphire_maintenance as package
	import erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record as record_package

	for owner, attr in (
		(package, "visit_crew"),
		(patches, "stamp_visit_date_on_pending_review"),
		(record_package, "sapphire_maintenance_record"),
	):
		if attr in vars(owner):
			delattr(owner, attr)
	for name in STUBBED:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


def _visit(state="Draft", changed=("workflow_state",), **fields):
	values = {
		"name": "SMR-1",
		"docstatus": 0,
		"workflow_state": state,
		"project": "PRJ-1",
		"technician": "austin@example.com",
		"serial_no": None,
		"visit_date": None,
		"scheduled_visit_date": "2026-10-12",
		"clock_in_time": None,
		"clock_out_time": None,
		"paused_duration": 0,
		"chemistry_readings": [],
		"maintenance_results": [],
		"cleaning_tasks": [],
		"_changed": set(changed),
	}
	values.update(fields)
	return record_module.SapphireMaintenanceRecord(**values)


def _send_for_review(doc):
	doc.workflow_state = "Pending Review"
	doc._changed = {"workflow_state"}
	doc.validate()
	return doc


class TestTheDayIsStamped(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_going_for_review_dates_an_undated_visit_today(self):
		doc = _send_for_review(_visit())
		self.assertEqual(doc.visit_date, D(2026, 10, 12))

	def test_a_date_the_technician_chose_is_kept(self):
		doc = _send_for_review(_visit(visit_date="2026-10-08"))
		self.assertEqual(doc.visit_date, "2026-10-08")

	def test_no_other_save_stamps_anything(self):
		cases = {
			"an autosave of a draft": _visit(changed=()),
			"a reviewer's edit while pending": _visit(state="Pending Review", changed=()),
			"Send Back": _visit(state="Draft"),
			"a submitted visit": _visit(state="Pending Review", docstatus=1),
		}
		for label, doc in cases.items():
			with self.subTest(label):
				doc.validate()
				self.assertIsNone(doc.visit_date)


class TestTheClockClosesAtReview(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_a_running_interval_closes_now(self):
		_interval("Open", DT(2026, 10, 12, 8, 0), paused=600)
		doc = _send_for_review(_visit())
		self.assertEqual(
			(doc.clock_in_time, doc.clock_out_time, doc.paused_duration),
			(DT(2026, 10, 12, 8, 0), DT(2026, 10, 12, 10, 30), 600),
		)

	def test_an_interval_already_completed_gives_its_end(self):
		_interval("Completed", DT(2026, 10, 12, 8, 0), end=DT(2026, 10, 12, 9, 45), paused=120)
		doc = _send_for_review(_visit(clock_in_time=DT(2026, 10, 12, 8, 0)))
		self.assertEqual((doc.clock_out_time, doc.paused_duration), (DT(2026, 10, 12, 9, 45), 120))

	def test_a_backfilled_visit_takes_nothing_from_todays_interval(self):
		_interval("Open", DT(2026, 10, 12, 8, 0))
		doc = _send_for_review(_visit(visit_date="2026-10-08"))
		self.assertEqual((doc.clock_in_time, doc.clock_out_time), (None, None))


class TestApprovalDaysLater(unittest.TestCase):
	"""The reviewer approves on the 12th a visit sent for review on the 9th."""

	def setUp(self):
		_reset(today=D(2026, 10, 9), now=DT(2026, 10, 9, 7, 5))

	def _approve_on_the_12th(self, doc):
		STATE["today"], STATE["now"] = D(2026, 10, 12), DT(2026, 10, 12, 11, 16)
		doc.before_submit()
		return doc

	def test_the_clock_set_at_review_stands(self):
		_interval("Open", DT(2026, 10, 9, 6, 30))
		doc = _send_for_review(_visit())
		STATE["intervals"][0].status = "Completed"
		STATE["intervals"][0].end_time = DT(2026, 10, 9, 7, 10)
		_interval("Open", DT(2026, 10, 12, 9, 0))  # the next visit, under way at approval
		self._approve_on_the_12th(doc)
		self.assertEqual(doc.visit_date, D(2026, 10, 9))
		self.assertEqual(doc.clock_out_time, DT(2026, 10, 9, 7, 5))

	def test_a_later_interval_never_becomes_this_visits_clock_out(self):
		"""Before v1.595.1: clock-out on the 12th for a visit done on the 9th."""
		doc = _send_for_review(_visit(clock_in_time=DT(2026, 10, 9, 6, 30)))
		self.assertIsNone(doc.clock_out_time, "no interval at review")
		_interval("Completed", DT(2026, 10, 11, 8, 0), end=DT(2026, 10, 11, 10, 0))
		self._approve_on_the_12th(doc)
		self.assertIsNone(doc.clock_out_time)

	def test_without_the_workflow_the_technicians_submit_still_closes_the_clock(self):
		STATE["today"], STATE["now"] = D(2026, 10, 12), DT(2026, 10, 12, 10, 30)
		_interval("Open", DT(2026, 10, 12, 8, 0))
		doc = _visit(clock_in_time=DT(2026, 10, 12, 8, 0), workflow_state=None, changed=())
		doc.before_submit()
		self.assertEqual(doc.clock_out_time, DT(2026, 10, 12, 10, 30))


class TestThePatch(unittest.TestCase):
	def setUp(self):
		_reset()

	def _record(self, name, **fields):
		values = {"name": name, "docstatus": 0, "workflow_state": "Pending Review", "visit_date": None}
		values.update(fields)
		STATE["records"].append(_Dict(values))

	def test_waiting_visits_are_dated_from_when_they_went_for_review(self):
		self._record("SEQUOIA", modified=DT(2026, 10, 12, 10, 59))
		STATE["comments"] += [
			_Dict(reference_name="SEQUOIA", content="Pending Review", creation=DT(2026, 10, 9, 8, 0)),
			_Dict(reference_name="SEQUOIA", content="Pending Review", creation=DT(2026, 10, 12, 8, 19)),
		]
		self._record("NO-COMMENT", modified=DT(2026, 10, 10, 15, 0))
		patch.execute()
		self.assertEqual(
			STATE["set_values"],
			[
				("SEQUOIA", "visit_date", D(2026, 10, 12), False),
				("NO-COMMENT", "visit_date", D(2026, 10, 10), False),
			],
		)

	def test_dated_drafts_and_submitted_visits_are_left_alone(self):
		self._record("DATED", visit_date="2026-10-08", modified=DT(2026, 10, 9))
		self._record("DRAFT", workflow_state="Draft", modified=DT(2026, 10, 9))
		self._record("SUBMITTED", docstatus=1, workflow_state="Final/Submitted", modified=DT(2026, 10, 9))
		patch.execute()
		self.assertEqual(STATE["set_values"], [])

	def test_it_never_raises(self):
		self._record("BROKEN", modified=None)
		STATE["comments"].append(
			_Dict(reference_name="BROKEN", content="Pending Review", creation="not a date")
		)
		patch.execute()
		self.assertEqual(STATE["errors"], ["stamp_visit_date_on_pending_review"])

	def test_it_is_registered_after_model_sync(self):
		lines = (APP / "patches.txt").read_text(encoding="utf-8").splitlines()
		entry = "erpnext_enhancements.patches.stamp_visit_date_on_pending_review"
		self.assertIn(entry, lines)
		self.assertGreater(lines.index(entry), lines.index("[post_model_sync]"))


if __name__ == "__main__":
	unittest.main()
