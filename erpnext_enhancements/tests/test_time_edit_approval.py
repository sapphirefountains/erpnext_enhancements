# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Hand edits to time wait for approval before they count. Bench-free.

The rule (v1.836.5, ``workforce/time_edits.py``): when an employee changes the times of
their own Job Interval, adds one by hand, or starts one backdated, the interval is
marked ``Waiting``. It is left out of the payroll export's totals and listed on its own
sheet, it gets no Timesheet line, and it counts once a correction reviewer who is
neither the employee nor the editor approves it. A reviewer's edit to someone else's
time needs no approval. Intervals from before the field existed read blank and count.

These tests RUN the rule over a stand-in ``frappe``: ``time_edits`` and
``payroll_export`` are imported under the stub, and the three ``api/time_kiosk.py``
functions that write or sync an interval are loaded with ``ast`` and executed against
recorded fakes, the style ``tests/README.md`` describes. Own CI step: it installs the
stub in ``setUpModule``.

Run: python -m unittest erpnext_enhancements.tests.test_time_edit_approval -v
"""

import ast
import datetime as dt
import json
import sys
import types
import unittest
from datetime import timedelta
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
KIOSK_SOURCE = (APP / "api" / "time_kiosk.py").read_text(encoding="utf-8")
JOB_INTERVAL_JSON = APP / "workforce" / "doctype" / "job_interval" / "job_interval.json"

NOW = dt.datetime(2026, 11, 18, 18, 0, 0)

# ----------------------------------------------------------------- the people

#: user -> (employee, roles). Names are invented for the test.
PEOPLE = {
	"tech@example.test": ("EMP-T", []),
	"other.tech@example.test": ("EMP-O", []),
	"pm@example.test": ("EMP-PM", ["Projects Manager"]),
	"hr@example.test": ("EMP-HR", ["HR Manager"]),
	"lead@example.test": ("EMP-L", []),
	"ops@example.test": ("EMP-OPS", ["Operations Manager"]),
}
#: employee -> reports_to employee
REPORTS_TO = {"EMP-T": "EMP-L"}


class _Dict(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class _Throw(Exception):
	pass


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe._ee_time_edit_stub = True
	frappe._dict = _Dict
	frappe.PermissionError = type("PermissionError", (Exception,), {})
	frappe.DoesNotExistError = type("DoesNotExistError", (Exception,), {})

	def _throw(message, exc=None, *args, **kwargs):
		if isinstance(exc, type) and issubclass(exc, Exception):
			raise exc(message)
		raise _Throw(message)

	frappe.throw = _throw
	frappe._ = lambda value, *a, **k: value
	frappe.session = types.SimpleNamespace(user="tech@example.test")
	frappe.get_roles = lambda user=None: list(PEOPLE.get(user or frappe.session.user, (None, []))[1])
	frappe.log_error = lambda *a, **k: None
	frappe.sent = []
	frappe.sendmail = lambda **kw: frappe.sent.append(kw)
	frappe.response = {}
	frappe.docs = {}

	def _get_doc(doctype, name=None):
		if isinstance(doctype, dict):
			raise AssertionError("no document should be built from a dict here")
		return frappe.docs[(doctype, name)]

	frappe.get_doc = _get_doc

	def _whitelist(*dargs, **dkwargs):
		def decorator(fn):
			fn._methods = dkwargs.get("methods")
			return fn

		return decorator

	frappe.whitelist = _whitelist

	def _get_value(doctype, filters=None, fieldname=None, as_dict=False, **kw):
		if doctype == "Employee" and isinstance(filters, dict) and "user_id" in filters:
			return PEOPLE.get(filters["user_id"], (None, []))[0]
		if doctype == "Employee" and fieldname == "reports_to":
			return REPORTS_TO.get(filters)
		if doctype == "Employee" and fieldname == "user_id":
			return next((u for u, (e, _r) in PEOPLE.items() if e == filters), None)
		if doctype == "User":
			return _Dict({"email": filters, "enabled": 1})
		return None

	frappe.db = types.SimpleNamespace(
		get_value=_get_value,
		has_column=lambda *a, **k: True,
		table_exists=lambda *a, **k: True,
		exists=lambda *a, **k: False,
		count=lambda *a, **k: 0,
		sql=lambda *a, **k: [],
		set_value=lambda *a, **k: None,
		escape=lambda v: f"'{v}'",
	)
	frappe.get_all = lambda *a, **k: []

	utils = types.ModuleType("frappe.utils")

	def _get_datetime(value=None):
		if value is None:
			return NOW
		if isinstance(value, dt.datetime):
			return value
		if isinstance(value, dt.date):
			return dt.datetime(value.year, value.month, value.day)
		text = str(value).strip().replace("T", " ")[:19]
		for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
			try:
				return dt.datetime.strptime(text, fmt)
			except ValueError:
				continue
		raise ValueError(value)

	def _getdate(value=None):
		if value is None:
			return NOW.date()
		return _get_datetime(value).date()

	def _flt(value, precision=None):
		try:
			out = float(value or 0)
		except (TypeError, ValueError):
			return 0.0
		return round(out, precision) if precision is not None else out

	def _cint(value):
		try:
			return int(float(value or 0))
		except (TypeError, ValueError):
			return 0

	def _get_last_day(value):
		day = _getdate(value)
		nxt = day.replace(day=28) + timedelta(days=4)
		return nxt - timedelta(days=nxt.day)

	for name, fn in (
		("get_datetime", _get_datetime), ("getdate", _getdate), ("flt", _flt), ("cint", _cint),
		("now_datetime", lambda: NOW), ("nowdate", lambda: str(NOW.date())),
		("add_days", lambda v, n: _getdate(v) + timedelta(days=n)), ("get_last_day", _get_last_day),
		("escape_html", lambda v: str(v)), ("get_url_to_form", lambda *a: "/x"),
	):
		setattr(utils, name, fn)
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = type("Document", (), {})
	model.document = document
	frappe.model = model

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document
	return frappe


frappe = None
time_edits = None
payroll_export = None


def setUpModule():
	global frappe, time_edits, payroll_export
	frappe = _install_frappe_stub()
	for name in list(sys.modules):
		if name.startswith("erpnext_enhancements.workforce"):
			del sys.modules[name]
	from erpnext_enhancements.workforce import payroll_export as _payroll_export
	from erpnext_enhancements.workforce import time_edits as _time_edits

	time_edits = _time_edits
	payroll_export = _payroll_export


def tearDownModule():
	for name in list(sys.modules):
		if name == "frappe" or name.startswith("frappe.") or name.startswith("erpnext_enhancements.workforce"):
			del sys.modules[name]


# ----------------------------------------------------------------- fakes


class FakeInterval(_Dict):
	"""A Job Interval Document: attribute access, ``get``, and recorded ``save``/``db_set``."""

	def __init__(self, **values):
		base = {
			"doctype": "Job Interval", "name": "JOB-INT-00001", "employee": "EMP-T", "project": "PROJ-1",
			"task": None, "time_category": "Labor", "status": "Completed", "total_paused_seconds": 0,
			"start_time": dt.datetime(2026, 11, 18, 8, 0), "end_time": dt.datetime(2026, 11, 18, 12, 0),
			"sync_status": "Synced", "manual_start": 0, "manual_start_reason": None, "corrected": 0,
		}
		base.update(values)
		super().__init__(base)
		self["saves"] = 0
		self["db_sets"] = []

	def save(self, ignore_permissions=False):
		self["saves"] += 1

	def insert(self, ignore_permissions=False):
		self["inserted"] = True

	def db_set(self, field, value):
		self[field] = value
		self["db_sets"].append((field, value))


def _as(user):
	frappe.session.user = user


def _load_kiosk_function(name, namespace):
	"""Execute ``api/time_kiosk.py``'s ``name`` in ``namespace`` and return it."""
	tree = ast.parse(KIOSK_SOURCE)
	node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
	node.decorator_list = []
	module = ast.Module(body=[node], type_ignores=[])
	exec(compile(module, "api/time_kiosk.py", "exec"), namespace)
	return namespace[name]


# ----------------------------------------------------------------- who waits


class TestWhoseEditWaits(unittest.TestCase):
	def test_an_employees_edit_of_their_own_time_waits(self):
		_as("tech@example.test")
		doc = FakeInterval()
		self.assertTrue(time_edits.record_edit(doc))
		self.assertEqual(doc.edit_approval, "Waiting")
		self.assertEqual(doc.edited_by, "tech@example.test")
		self.assertEqual(doc.edited_on, NOW)
		self.assertEqual(doc.edit_previous_start, dt.datetime(2026, 11, 18, 8, 0))
		self.assertEqual(doc.edit_previous_end, dt.datetime(2026, 11, 18, 12, 0))

	def test_a_managers_edit_of_another_persons_time_does_not_wait(self):
		_as("pm@example.test")
		doc = FakeInterval()
		self.assertFalse(time_edits.record_edit(doc))
		self.assertEqual(time_edits.state(doc), "")

	def test_the_supervisor_named_in_reports_to_is_a_reviewer_too(self):
		"""The correction review's own rule, reused: the manager roles OR reports_to."""
		_as("lead@example.test")
		self.assertFalse(time_edits.record_edit(FakeInterval()))

	def test_a_reviewers_edit_of_their_own_time_still_waits(self):
		_as("pm@example.test")
		doc = FakeInterval(employee="EMP-PM")
		self.assertTrue(time_edits.record_edit(doc))
		self.assertEqual(doc.edit_approval, "Waiting")

	def test_someone_who_may_edit_but_does_not_review_this_person_waits(self):
		_as("ops@example.test")
		doc = FakeInterval()
		self.assertTrue(time_edits.record_edit(doc))
		self.assertEqual(doc.edited_by, "ops@example.test")

	def test_an_entry_added_by_hand_has_no_times_before(self):
		_as("tech@example.test")
		doc = FakeInterval()
		self.assertTrue(time_edits.record_edit(doc, added=True))
		self.assertIsNone(doc.edit_previous_start)
		self.assertEqual(time_edits.describe_change(doc), "Added by hand")

	def test_a_second_edit_before_a_decision_keeps_the_times_before_the_first(self):
		_as("tech@example.test")
		doc = FakeInterval()
		time_edits.record_edit(doc)
		doc.start_time = dt.datetime(2026, 11, 18, 7, 0)
		time_edits.record_edit(doc)
		self.assertEqual(doc.edit_previous_start, dt.datetime(2026, 11, 18, 8, 0))
		self.assertIn("Start 2026-11-18 08:00 to 2026-11-18 07:00", time_edits.describe_change(doc))

	def test_a_reviewer_editing_a_waiting_entry_settles_it(self):
		doc = FakeInterval(edit_approval="Waiting", edited_by="tech@example.test")
		_as("hr@example.test")
		self.assertFalse(time_edits.record_edit(doc))
		self.assertEqual(doc.edit_approval, "Approved")
		self.assertEqual(doc.edit_reviewed_by, "hr@example.test")

	def test_an_edit_to_the_same_minute_is_no_change(self):
		self.assertTrue(time_edits.same_minute("2026-11-18 08:03:00", dt.datetime(2026, 11, 18, 8, 3, 27)))
		self.assertFalse(time_edits.same_minute("2026-11-18 08:02:00", dt.datetime(2026, 11, 18, 8, 3, 27)))
		self.assertFalse(time_edits.same_minute("2026-11-18 08:02:00", None))


# ----------------------------------------------------------------- approve / send back


class TestDecidingAnEdit(unittest.TestCase):
	def setUp(self):
		self.resynced = []
		self._orig_resync = time_edits._resync
		time_edits._resync = lambda doc: self.resynced.append(doc.name)
		frappe.sent.clear()
		self.doc = FakeInterval(
			edit_approval="Waiting", edited_by="tech@example.test", sync_status=time_edits.HELD_SYNC_STATUS,
			edit_previous_start=dt.datetime(2026, 11, 18, 8, 30), edit_previous_end=dt.datetime(2026, 11, 18, 12, 0),
		)
		frappe.docs[("Job Interval", self.doc.name)] = self.doc

	def tearDown(self):
		time_edits._resync = self._orig_resync

	def test_both_endpoints_are_post_only(self):
		self.assertEqual(time_edits.approve_time_edit._methods, ["POST"])
		self.assertEqual(time_edits.send_back_time_edit._methods, ["POST"])

	def test_the_employee_cannot_approve_their_own_entry(self):
		_as("tech@example.test")
		with self.assertRaises(frappe.PermissionError):
			time_edits.approve_time_edit(self.doc.name)
		self.assertEqual(self.doc.edit_approval, "Waiting")

	def test_a_reviewer_cannot_approve_their_own_time(self):
		self.doc.employee = "EMP-PM"
		self.doc.edited_by = "pm@example.test"
		_as("pm@example.test")
		with self.assertRaises(frappe.PermissionError):
			time_edits.approve_time_edit(self.doc.name)
		with self.assertRaises(frappe.PermissionError):
			time_edits.send_back_time_edit(self.doc.name)

	def test_the_person_who_made_the_edit_cannot_approve_it(self):
		self.doc.edited_by = "lead@example.test"
		_as("lead@example.test")
		with self.assertRaises(frappe.PermissionError):
			time_edits.approve_time_edit(self.doc.name)

	def test_someone_who_does_not_review_this_person_cannot_decide(self):
		for user in ("other.tech@example.test", "ops@example.test"):
			with self.subTest(user=user):
				_as(user)
				with self.assertRaises(frappe.PermissionError):
					time_edits.approve_time_edit(self.doc.name)

	def test_approval_records_who_and_when_and_writes_the_timesheet_line(self):
		_as("pm@example.test")
		out = time_edits.approve_time_edit(self.doc.name, note="  ok  ")
		self.assertEqual(out["status"], "Approved")
		self.assertEqual(self.doc.edit_approval, "Approved")
		self.assertEqual(self.doc.edit_reviewed_by, "pm@example.test")
		self.assertEqual(self.doc.edit_reviewed_on, NOW)
		self.assertEqual(self.doc.edit_review_note, "ok")
		self.assertEqual(self.doc.sync_status, "Pending")
		self.assertEqual(self.doc.saves, 1)
		self.assertEqual(self.resynced, [self.doc.name])
		self.assertFalse(time_edits.is_held(self.doc))

	def test_the_supervisor_in_reports_to_may_approve(self):
		_as("lead@example.test")
		time_edits.approve_time_edit(self.doc.name)
		self.assertEqual(self.doc.edit_reviewed_by, "lead@example.test")

	def test_send_back_records_who_and_when_restores_nothing_and_tells_the_person(self):
		_as("hr@example.test")
		start_before = self.doc.start_time
		out = time_edits.send_back_time_edit(self.doc.name, note="Wrong day")
		self.assertEqual(out["status"], "Sent Back")
		self.assertEqual(self.doc.edit_reviewed_by, "hr@example.test")
		self.assertEqual(self.doc.edit_reviewed_on, NOW)
		self.assertEqual(self.doc.start_time, start_before, "sending back restores nothing")
		self.assertTrue(time_edits.is_held(self.doc), "a sent-back entry stays out of payroll")
		self.assertEqual(self.resynced, [self.doc.name])
		self.assertEqual(len(frappe.sent), 1)
		self.assertEqual(frappe.sent[0]["recipients"], ["tech@example.test"])

	def test_only_a_waiting_entry_can_be_sent_back_and_only_a_held_one_approved(self):
		_as("hr@example.test")
		self.doc.edit_approval = "Approved"
		with self.assertRaises(_Throw):
			time_edits.send_back_time_edit(self.doc.name)
		with self.assertRaises(_Throw):
			time_edits.approve_time_edit(self.doc.name)
		self.doc.edit_approval = "Sent Back"
		time_edits.approve_time_edit(self.doc.name)
		self.assertEqual(self.doc.edit_approval, "Approved")


# ----------------------------------------------------------------- the endpoints that edit


class TestTheKioskEndpointsMarkTheEdit(unittest.TestCase):
	def setUp(self):
		self.resynced = []
		self.fake_corrections = types.ModuleType("erpnext_enhancements.workforce.corrections")

		def _record_originals(interval):
			if not interval.get("corrected"):
				interval.original_start_time = interval.start_time
				interval.original_end_time = interval.end_time

		self.fake_corrections._record_originals = _record_originals
		self._saved = sys.modules.get("erpnext_enhancements.workforce.corrections")
		sys.modules["erpnext_enhancements.workforce.corrections"] = self.fake_corrections

	def tearDown(self):
		if self._saved is None:
			sys.modules.pop("erpnext_enhancements.workforce.corrections", None)
		else:
			sys.modules["erpnext_enhancements.workforce.corrections"] = self._saved

	def _update(self, doc, **kwargs):
		frappe.docs[("Job Interval", doc.name)] = doc
		ns = {
			"frappe": frappe, "_": frappe._, "get_datetime": frappe.utils.get_datetime,
			"now_datetime": lambda: NOW,
			"_assert_may_edit_time_for": lambda employee: PEOPLE[frappe.session.user][0],
			"resync_interval_timesheet": lambda d: self.resynced.append(d.name),
		}
		fn = _load_kiosk_function("update_interval_times", ns)
		return fn(interval=doc.name, reason="forgot", **kwargs)

	def test_moving_your_own_end_waits_and_keeps_the_end_before(self):
		_as("tech@example.test")
		doc = FakeInterval()
		out = self._update(doc, start_time="2026-11-18 08:00", end_time="2026-11-18 13:00", time_category="Labor")
		self.assertEqual(doc.edit_approval, "Waiting")
		self.assertEqual(out["edit_approval"], "Waiting")
		self.assertIn("approves", out["message"])
		self.assertEqual(doc.edit_previous_end, dt.datetime(2026, 11, 18, 12, 0))
		self.assertEqual(doc.end_time, dt.datetime(2026, 11, 18, 13, 0))
		self.assertEqual(doc.start_time, dt.datetime(2026, 11, 18, 8, 0))

	def test_a_change_of_activity_alone_does_not_wait(self):
		_as("tech@example.test")
		doc = FakeInterval(start_time=dt.datetime(2026, 11, 18, 8, 0, 41))
		self._update(doc, start_time="2026-11-18 08:00", end_time="2026-11-18 12:00", time_category="Travel")
		self.assertEqual(time_edits.state(doc), "")
		self.assertEqual(doc.time_category, "Travel")
		self.assertEqual(doc.start_time, dt.datetime(2026, 11, 18, 8, 0, 41), "same minute leaves the stored value")
		self.assertFalse(doc.manual_start)

	def test_a_managers_edit_of_another_persons_interval_does_not_wait(self):
		_as("pm@example.test")
		doc = FakeInterval()
		out = self._update(doc, start_time="2026-11-18 07:30", time_category="Labor")
		self.assertEqual(time_edits.state(doc), "")
		self.assertEqual(out["message"], "Times updated.")
		self.assertEqual(doc.start_time, dt.datetime(2026, 11, 18, 7, 30))

	def test_adding_time_by_hand_for_yourself_waits(self):
		_as("tech@example.test")
		built = {}

		def _new_interval(employee, project, task, time_category, description, now_dt, **kw):
			built["doc"] = FakeInterval(employee=employee, project=project, start_time=now_dt, end_time=None,
			                            status="Open", sync_status="Pending")
			return built["doc"]

		ns = {
			"frappe": frappe, "_": frappe._, "get_datetime": frappe.utils.get_datetime,
			"now_datetime": lambda: NOW, "_new_interval": _new_interval,
			"_require_session_employee": lambda: "EMP-T",
			"_assert_may_edit_time_for": lambda employee: PEOPLE[frappe.session.user][0],
			"photo_gate": types.SimpleNamespace(stamp=lambda *a, **k: None, resolve=lambda d: "Skipped"),
			"resync_interval_timesheet": lambda d: self.resynced.append(d.name),
		}
		fn = _load_kiosk_function("add_manual_interval", ns)
		out = fn(project="PROJ-1", start_time="2026-11-18 08:00", end_time="2026-11-18 10:00",
		         reason="forgot to clock in", time_category="Labor")
		doc = built["doc"]
		self.assertTrue(doc.inserted)
		self.assertEqual(doc.edit_approval, "Waiting")
		self.assertIsNone(doc.edit_previous_start)
		self.assertEqual(out["edit_approval"], "Waiting")

	def test_start_backdated_goes_through_the_same_path(self):
		tree = ast.parse(KIOSK_SOURCE)
		fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "start_backdated")
		calls = [c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)]
		self.assertIn("add_manual_interval", calls)


# ----------------------------------------------------------------- the Timesheet side


class TestTheTimesheetDoesNotCountAWaitingEntry(unittest.TestCase):
	def _namespace(self, timesheet_docs):
		self.inserted = []

		def _get_doc(arg, name=None):
			if isinstance(arg, dict):
				ts = _Dict(arg)
				ts.insert = lambda ignore_permissions=False: self.inserted.append(arg)
				return ts
			return timesheet_docs[name]

		frappe.get_doc = _get_doc
		frappe.db.get_value = lambda *a, **k: None
		return {
			"frappe": frappe, "get_datetime": frappe.utils.get_datetime, "flt": frappe.utils.flt,
			"timedelta": timedelta, "_timesheet_detail_has_interval_link": lambda: True,
			"update_timesheet_note": lambda *a, **k: None,
		}

	def setUp(self):
		self._get_doc = frappe.get_doc
		self._get_value = frappe.db.get_value

	def tearDown(self):
		frappe.get_doc = self._get_doc
		frappe.db.get_value = self._get_value

	def test_a_waiting_interval_gets_no_timesheet_line(self):
		sync = _load_kiosk_function("sync_interval_to_timesheet", self._namespace({}))
		doc = FakeInterval(edit_approval="Waiting", sync_status="Pending")
		sync(doc)
		self.assertEqual(self.inserted, [])
		self.assertEqual(doc.sync_status, time_edits.HELD_SYNC_STATUS)

	def test_a_sent_back_interval_gets_none_either(self):
		sync = _load_kiosk_function("sync_interval_to_timesheet", self._namespace({}))
		doc = FakeInterval(edit_approval="Sent Back")
		sync(doc)
		self.assertEqual(self.inserted, [])

	def test_an_approved_or_never_edited_interval_is_synced_as_before(self):
		for state in ("Approved", None, ""):
			with self.subTest(state=state):
				sync = _load_kiosk_function("sync_interval_to_timesheet", self._namespace({}))
				doc = FakeInterval(edit_approval=state)
				sync(doc)
				self.assertEqual(len(self.inserted), 1)
				self.assertEqual(self.inserted[0]["time_logs"][0]["hours"], 4.0)
				self.assertEqual(doc.sync_status, "Synced")

	def test_a_resync_of_a_waiting_interval_takes_its_old_line_off_and_adds_none(self):
		class _Row(_Dict):
			def as_dict(self):
				return dict(self)

		removed = []
		ts = _Dict(name="TS-1", time_logs=[_Row(name="L1"), _Row(name="L2")])
		ts.save = lambda ignore_permissions=False: removed.append([r.name for r in ts.time_logs])
		ts.append = lambda field, row: ts.time_logs.append(_Row(row))
		ns = self._namespace({"TS-1": ts})
		ns["cint"] = frappe.utils.cint
		ns["find_timesheet_line"] = lambda d: _Dict(name="L1", parent="TS-1", docstatus=0)
		ns["sync_interval_to_timesheet"] = _load_kiosk_function("sync_interval_to_timesheet", ns)
		resync = _load_kiosk_function("resync_interval_timesheet", ns)
		doc = FakeInterval(edit_approval="Waiting")
		self.assertTrue(resync(doc))
		self.assertEqual(removed, [["L2"]])
		self.assertEqual(self.inserted, [])
		self.assertEqual(doc.sync_status, time_edits.HELD_SYNC_STATUS)


# ----------------------------------------------------------------- the payroll export


def _row(name, employee, start, hours, state=None, paused=0, project="PROJ-1", **extra):
	start = dt.datetime.strptime(start, "%Y-%m-%d %H:%M")
	values = {
		"name": name, "employee": employee, "project": project, "start_time": start,
		"end_time": start + timedelta(hours=hours), "total_paused_seconds": paused,
		"employee_name": f"Name {employee}", "employee_number": employee[-1],
		"edit_approval": state, "edit_previous_start": None, "edit_previous_end": None,
		"edited_by": None, "time_edit_reason": None, "manual_start_reason": None,
	}
	values.update(extra)
	return _Dict(values)


ROWS = [
	_row("JI-1", "EMP-T", "2026-11-03 08:00", 8),
	_row("JI-2", "EMP-T", "2026-11-04 08:00", 8, state="Approved"),
	_row("JI-3", "EMP-T", "2026-11-05 08:00", 6, state="Waiting", edited_by="tech@example.test",
	     edit_previous_start=dt.datetime(2026, 11, 5, 9, 0), edit_previous_end=dt.datetime(2026, 11, 5, 14, 0),
	     time_edit_reason="forgot"),
	_row("JI-4", "EMP-O", "2026-11-05 08:00", 3, state="Sent Back", manual_start_reason="by hand"),
	_row("JI-5", "EMP-O", "2026-11-06 08:00", 5, paused=1800),
]


class TestThePayrollExportLeavesWaitingEntriesOut(unittest.TestCase):
	def setUp(self):
		self._orig = payroll_export.completed_intervals
		self._orig_ot = payroll_export.overtime_settings
		self.calls = []

		def _fake(from_dt, to_dt, employee=None):
			self.calls.append((from_dt, to_dt))
			lo, hi = frappe.utils.get_datetime(from_dt), frappe.utils.get_datetime(to_dt)
			return [r for r in ROWS if lo <= r.start_time < hi and (not employee or r.employee == employee)]

		payroll_export.completed_intervals = _fake
		payroll_export.overtime_settings = lambda: ("Sunday", 40.0)

	def tearDown(self):
		payroll_export.completed_intervals = self._orig
		payroll_export.overtime_settings = self._orig_ot

	def test_worked_hours_leave_out_waiting_and_sent_back_entries(self):
		hours = {(r.employee, r.project): round(r.hours, 2) for r in payroll_export.worked_hours("2026-11-01", "2026-11-15")}
		self.assertEqual(hours, {("EMP-T", "PROJ-1"): 16.0, ("EMP-O", "PROJ-1"): 4.5})

	def test_the_overtime_split_leaves_them_out_too(self):
		split = payroll_export.overtime_split("2026-11-01", "2026-11-15")
		self.assertEqual(round(split["EMP-T"]["regular_hours"] + split["EMP-T"]["overtime_hours"], 2), 16.0)
		self.assertEqual(round(split["EMP-O"]["regular_hours"] + split["EMP-O"]["overtime_hours"], 2), 4.5)

	def test_held_entries_list_each_waiting_entry_with_what_changed(self):
		held = payroll_export.held_entries("2026-11-01", "2026-11-15")
		self.assertEqual([e["interval"] for e in held], ["JI-3", "JI-4"])
		first = held[0]
		self.assertEqual(first["employee_name"], "Name EMP-T")
		self.assertEqual(first["day"], "2026-11-05")
		self.assertEqual(first["hours"], 6.0)
		self.assertEqual(first["status"], "Waiting")
		self.assertIn("Start 2026-11-05 09:00 to 2026-11-05 08:00", first["change"])
		self.assertNotIn("End", first["change"], "an end that did not move is not listed")
		self.assertEqual(held[1]["change"], "Added by hand")
		self.assertEqual(held[1]["status"], "Sent Back")

	def test_the_workbook_totals_leave_them_out_and_its_own_sheet_lists_them(self):
		frappe.get_all = lambda *a, **k: [
			_Dict(name="EMP-T", employee_name="T", employee_number="1", last_name="A", first_name="T"),
			_Dict(name="EMP-O", employee_name="O", employee_number="2", last_name="B", first_name="O"),
		]
		try:
			data = payroll_export.build_rows("2026-11-01", "2026-11-15")
			sheet = payroll_export.workbook_rows("2026-11-01", "2026-11-15", data=data)
			waiting = payroll_export.waiting_rows("2026-11-01", "2026-11-15")
		finally:
			frappe.get_all = lambda *a, **k: []
		totals = sheet[-1]
		self.assertEqual(totals[1], "Totals")
		self.assertEqual(totals[3], 20.5, "regular hours exclude the 9 held hours")
		self.assertEqual(waiting[0][0], payroll_export.WAITING_NOTICE)
		self.assertEqual(waiting[3], list(payroll_export.WAITING_HEADER))
		listed = [line[-1] for line in waiting[4:-1]]
		self.assertEqual(listed, ["JI-3", "JI-4"])
		self.assertEqual(waiting[-1][1], "Total left out")
		self.assertEqual(waiting[-1][3], 9.0)

	def test_the_desk_report_lists_what_is_waiting(self):
		from erpnext_enhancements.workforce.report.payroll_hours_export import payroll_hours_export as report

		message = report._message([], "2026-11-01", "2026-11-15")
		self.assertIn("2 time entries are waiting for approval", message)
		self.assertIn("(9.0 h)", message)
		self.assertIn("Name EMP-T, 2026-11-05: 6.0 h, Waiting", message)

	def test_an_empty_waiting_sheet_says_so(self):
		rows = payroll_export.waiting_rows("2026-11-01", "2026-11-15", held=[])
		self.assertIn("Nothing", rows[-1][1])

	def test_the_period_bounds_match_the_old_start_date_rule(self):
		payroll_export.worked_hours("2026-11-01", "2026-11-15")
		self.assertEqual(self.calls[-1], ("2026-11-01 00:00:00", "2026-11-16 00:00:00"))

	def test_the_query_reads_the_approval_column_and_survives_its_absence(self):
		captured = []
		frappe.db.sql = lambda query, values=None, as_dict=False: captured.append(query) or []
		payroll_export.completed_intervals = self._orig
		try:
			self._orig("2026-11-01 00:00:00", "2026-11-16 00:00:00")
			self.assertIn("ji.edit_approval", captured[-1])
			frappe.db.has_column = lambda *a, **k: False
			self._orig("2026-11-01 00:00:00", "2026-11-16 00:00:00")
			self.assertIn("NULL AS edit_approval", captured[-1])
		finally:
			frappe.db.has_column = lambda *a, **k: True
			frappe.db.sql = lambda *a, **k: []


# ----------------------------------------------------------------- history is not marked


class TestEntriesFromBeforeAreNotWaiting(unittest.TestCase):
	def _field(self, name):
		doctype = json.loads(JOB_INTERVAL_JSON.read_text(encoding="utf-8"))
		return next(f for f in doctype["fields"] if f["fieldname"] == name), doctype

	def test_the_new_column_has_no_default_so_migrate_writes_nothing_into_old_rows(self):
		"""On a normal doctype a new column's default is written into every existing
		row by the ALTER. With none, old rows read NULL, which is blank, which counts."""
		field, _doctype = self._field("edit_approval")
		self.assertIn(field.get("default"), (None, ""))
		self.assertEqual(field["options"].split("\n")[0], "")

	def test_a_blank_or_missing_state_is_not_held(self):
		self.assertFalse(time_edits.is_held(_Dict(edit_approval=None)))
		self.assertFalse(time_edits.is_held(_Dict(edit_approval="")))
		self.assertFalse(time_edits.is_held(types.SimpleNamespace()))

	def test_the_held_sync_status_is_an_option_so_a_held_interval_can_still_be_saved(self):
		field, doctype = self._field("sync_status")
		self.assertIn(time_edits.HELD_SYNC_STATUS, field["options"].split("\n"))
		self.assertNotEqual(time_edits.HELD_SYNC_STATUS, "Pending")
		self.assertGreater(doctype["modified"], "2026-11-19")

	def test_every_new_field_is_in_the_field_order_and_read_only(self):
		_f, doctype = self._field("edit_approval")
		for name in ("edit_approval", "edited_by", "edited_on", "edit_previous_start", "edit_previous_end",
		             "edit_reviewed_by", "edit_reviewed_on", "edit_review_note"):
			with self.subTest(field=name):
				self.assertIn(name, doctype["field_order"])
				field, _d = self._field(name)
				self.assertEqual(field.get("read_only"), 1)


if __name__ == "__main__":
	unittest.main()
