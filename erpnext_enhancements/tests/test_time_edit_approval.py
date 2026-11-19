# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Hand edits to time wait for approval before they count. Bench-free.

The rule (v1.836.5, ``workforce/time_edits.py``): when an employee changes the times of
their own Job Interval, adds one by hand, or starts one backdated, the interval is
marked ``Waiting``. It is left out of the payroll export's totals and listed on its own
sheet, it gets no Timesheet line, and it counts once an approver who is neither the
employee nor the editor approves it. An approver (Nikolas, 2026-11-19) is the employee's
``reports_to`` supervisor, an HR Manager or a System Manager; a Projects Manager is not.
An approver's edit to someone else's time needs no approval. The approver is told when an
entry starts waiting, once per waiting spell, from ``JobInterval.on_update``, and the
Time Edit Approvals page lists what the caller may decide. Intervals from before the
field existed read blank and count.

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
	"hr2@example.test": ("EMP-HR2", ["HR Manager"]),
	"sys@example.test": ("EMP-SYS", ["System Manager"]),
	"lead@example.test": ("EMP-L", []),
	"ops@example.test": ("EMP-OPS", ["Operations Manager"]),
	"gone.lead@example.test": ("EMP-GL", []),
	"third.tech@example.test": ("EMP-3", []),
}
#: employee -> reports_to employee. EMP-3's supervisor has a disabled login.
REPORTS_TO = {"EMP-T": "EMP-L", "EMP-3": "EMP-GL"}
#: users whose login is disabled
DISABLED = {"gone.lead@example.test"}


def _users_with_role(role):
	"""``frappe.utils.user.get_users_with_role``: enabled users holding ``role``."""
	return [u for u, (_e, roles) in PEOPLE.items() if role in roles and u not in DISABLED]


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
	frappe.errors = []
	frappe.log_error = lambda *a, **k: frappe.errors.append(k.get("title") or (a[0] if a else ""))
	frappe.sent = []
	frappe.sendmail = lambda **kw: frappe.sent.append(kw)
	frappe.response = {}
	frappe.docs = {}
	#: Notification Log rows inserted: the bell entries.
	frappe.bells = []

	class _NewDoc(_Dict):
		def insert(self, ignore_permissions=False):
			if self.get("doctype") != "Notification Log":
				raise AssertionError("only a Notification Log is built from a dict here")
			frappe.bells.append(dict(self))
			return self

	def _get_doc(doctype, name=None):
		if isinstance(doctype, dict):
			return _NewDoc(doctype)
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
		if doctype == "Employee" and fieldname == "employee_name":
			return f"Name {filters}"
		if doctype == "Project" and fieldname == "project_name":
			return f"Title {filters}"
		if doctype == "User":
			enabled = 0 if filters in DISABLED else 1
			if fieldname == "enabled":
				return enabled
			if fieldname == "full_name":
				return f"Full {filters}"
			return _Dict({"email": filters, "enabled": enabled})
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
		("get_url", lambda path="": "https://erp.example.test" + (path or "")),
	):
		setattr(utils, name, fn)
	frappe.utils = utils
	user_utils = types.ModuleType("frappe.utils.user")
	user_utils.get_users_with_role = _users_with_role
	utils.user = user_utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = type("Document", (), {})
	model.document = document
	frappe.model = model

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.utils.user"] = user_utils
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


#: Run after every FakeInterval save / insert, as Frappe runs ``on_update``. Empty unless a
#: test puts the real controller's ``on_update`` here (:func:`_run_on_update`).
AFTER_SAVE = []


class FakeInterval(_Dict):
	"""A Job Interval Document: attribute access, ``get``, ``flags``, and recorded
	``save``/``insert``/``db_set``."""

	def __init__(self, **values):
		base = {
			"doctype": "Job Interval", "name": "JOB-INT-00001", "employee": "EMP-T", "project": "PROJ-1",
			"task": None, "time_category": "Labor", "status": "Completed", "total_paused_seconds": 0,
			"start_time": dt.datetime(2026, 11, 18, 8, 0), "end_time": dt.datetime(2026, 11, 18, 12, 0),
			"sync_status": "Synced", "manual_start": 0, "manual_start_reason": None, "corrected": 0,
		}
		base.update(values)
		super().__init__(base)
		self["flags"] = _Dict()
		self["saves"] = 0
		self["db_sets"] = []

	def save(self, ignore_permissions=False):
		self["saves"] += 1
		for hook in AFTER_SAVE:
			hook(self)

	def insert(self, ignore_permissions=False):
		self["inserted"] = True
		for hook in AFTER_SAVE:
			hook(self)

	def db_set(self, field, value):
		self[field] = value
		self["db_sets"].append((field, value))


def _as(user):
	frappe.session.user = user


def _controller():
	"""The real ``JobInterval`` controller class, imported under the stub."""
	from erpnext_enhancements.workforce.doctype.job_interval.job_interval import JobInterval

	return JobInterval


def _run_on_update(doc):
	"""``JobInterval.on_update`` on a fake, as Frappe runs it after a save or an insert."""
	_controller().on_update(doc)


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

	def test_an_hr_or_system_managers_edit_of_another_persons_time_does_not_wait(self):
		for user in ("hr@example.test", "sys@example.test"):
			with self.subTest(user=user):
				_as(user)
				doc = FakeInterval()
				self.assertFalse(time_edits.record_edit(doc))
				self.assertEqual(time_edits.state(doc), "")

	def test_a_projects_managers_edit_of_another_persons_time_waits(self):
		"""Projects Manager decides Time Correction Requests, not hand edits (2026-11-19)."""
		_as("pm@example.test")
		doc = FakeInterval()
		self.assertTrue(time_edits.record_edit(doc))
		self.assertEqual(doc.edit_approval, "Waiting")
		self.assertEqual(doc.edited_by, "pm@example.test")

	def test_the_supervisor_named_in_reports_to_need_not_wait(self):
		_as("lead@example.test")
		self.assertFalse(time_edits.record_edit(FakeInterval()))

	def test_an_approvers_edit_of_their_own_time_still_waits(self):
		_as("hr@example.test")
		doc = FakeInterval(employee="EMP-HR")
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

	def test_an_approver_cannot_approve_their_own_time(self):
		self.doc.employee = "EMP-HR"
		self.doc.edited_by = "hr@example.test"
		_as("hr@example.test")
		with self.assertRaises(frappe.PermissionError):
			time_edits.approve_time_edit(self.doc.name)
		with self.assertRaises(frappe.PermissionError):
			time_edits.send_back_time_edit(self.doc.name)

	def test_the_person_who_made_the_edit_cannot_approve_it(self):
		self.doc.edited_by = "lead@example.test"
		_as("lead@example.test")
		with self.assertRaises(frappe.PermissionError):
			time_edits.approve_time_edit(self.doc.name)

	def test_someone_who_may_not_approve_this_person_cannot_decide(self):
		"""A Projects Manager included: that role decides Time Correction Requests only."""
		for user in ("other.tech@example.test", "ops@example.test", "pm@example.test"):
			with self.subTest(user=user):
				_as(user)
				with self.assertRaises(frappe.PermissionError):
					time_edits.approve_time_edit(self.doc.name)

	def test_approval_records_who_and_when_and_writes_the_timesheet_line(self):
		_as("sys@example.test")
		out = time_edits.approve_time_edit(self.doc.name, note="  ok  ")
		self.assertEqual(out["status"], "Approved")
		self.assertEqual(self.doc.edit_approval, "Approved")
		self.assertEqual(self.doc.edit_reviewed_by, "sys@example.test")
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


# ----------------------------------------------------------------- a save on the Desk form


class DirectSave(FakeInterval):
	"""An interval as ``JobInterval.validate`` sees it: the values the save sent, over the
	stored row. ``ignore_permissions`` is how this app's own endpoints save."""

	def __init__(self, stored=None, new=False, ignore_permissions=False, **sent):
		stored_row = FakeInterval(**(stored or {}))
		values = dict(stored_row)
		values.update(sent)
		super().__init__(**values)
		self["flags"] = _Dict(ignore_permissions=ignore_permissions)
		self["_stored"] = None if new else stored_row
		self["_new"] = new

	def is_new(self):
		return self["_new"]

	def get_doc_before_save(self):
		return self["_stored"]

	def set(self, field, value):
		self[field] = value


class TestASaveOnTheFormFollowsTheSameRule(unittest.TestCase):
	"""Projects Manager, HR Manager and System Manager can write a Job Interval on the Desk
	form and through the REST resource. ``guard_direct_save`` holds those saves to the rule
	the kiosk's endpoints hold."""

	LATER = dt.datetime(2026, 11, 18, 14, 0)

	def test_ones_own_time_changed_on_the_form_waits(self):
		_as("pm@example.test")
		doc = DirectSave(stored={"employee": "EMP-PM"}, end_time=self.LATER)
		self.assertTrue(time_edits.guard_direct_save(doc))
		self.assertEqual(doc.edit_approval, "Waiting")
		self.assertEqual(doc.edited_by, "pm@example.test")
		self.assertEqual(doc.edit_previous_start, dt.datetime(2026, 11, 18, 8, 0))
		self.assertEqual(doc.edit_previous_end, dt.datetime(2026, 11, 18, 12, 0))
		self.assertTrue(doc.flags.get("edit_state_changed"))

	def test_times_the_form_sends_back_as_text_are_unchanged(self):
		"""A Desk save sends a Datetime as text. The same minute is no edit."""
		_as("pm@example.test")
		doc = DirectSave(
			stored={"employee": "EMP-PM"}, start_time="2026-11-18 08:00:00", end_time="2026-11-18 12:00:00"
		)
		self.assertFalse(time_edits.guard_direct_save(doc))
		self.assertEqual(time_edits.state(doc), "")
		self.assertFalse(doc.flags.get("edit_state_changed"))

	def test_paused_time_cut_on_ones_own_interval_waits(self):
		"""Paid hours are the span less the paused time, so less paused time is more pay."""
		_as("pm@example.test")
		doc = DirectSave(stored={"employee": "EMP-PM", "total_paused_seconds": 1800}, total_paused_seconds=0)
		self.assertTrue(time_edits.guard_direct_save(doc))
		self.assertEqual(doc.edit_approval, "Waiting")

	def test_an_approver_changing_another_persons_time_does_not_wait(self):
		_as("hr@example.test")
		doc = DirectSave(end_time=self.LATER)
		self.assertFalse(time_edits.guard_direct_save(doc))
		self.assertEqual(time_edits.state(doc), "")

	def test_a_projects_manager_changing_another_persons_time_on_the_form_waits(self):
		_as("pm@example.test")
		doc = DirectSave(end_time=self.LATER)
		self.assertTrue(time_edits.guard_direct_save(doc))
		self.assertEqual(doc.edit_approval, "Waiting")
		self.assertEqual(doc.edited_by, "pm@example.test")
		self.assertTrue(doc.flags.get("edit_state_changed"))

	def test_moving_another_persons_interval_to_oneself_waits(self):
		_as("pm@example.test")
		doc = DirectSave(employee="EMP-PM")
		self.assertTrue(time_edits.guard_direct_save(doc))
		self.assertEqual(doc.edit_approval, "Waiting")

	def test_moving_ones_own_interval_to_another_person_waits(self):
		_as("pm@example.test")
		doc = DirectSave(stored={"employee": "EMP-PM"}, employee="EMP-T")
		self.assertTrue(time_edits.guard_direct_save(doc))

	def test_approval_fields_the_save_sent_are_dropped(self):
		"""Nobody approves their own edit by writing the fields."""
		_as("pm@example.test")
		stored = {
			"employee": "EMP-PM", "edit_approval": "Waiting", "edited_by": "pm@example.test",
			"edit_previous_end": dt.datetime(2026, 11, 18, 11, 0), "sync_status": time_edits.HELD_SYNC_STATUS,
		}
		doc = DirectSave(
			stored=stored, edit_approval="Approved", edit_reviewed_by="hr@example.test",
			edit_reviewed_on=NOW, edit_previous_end=None,
		)
		self.assertFalse(time_edits.guard_direct_save(doc))
		self.assertEqual(doc.edit_approval, "Waiting")
		self.assertIsNone(doc.edit_reviewed_by)
		self.assertIsNone(doc.edit_reviewed_on)
		self.assertEqual(doc.edit_previous_end, dt.datetime(2026, 11, 18, 11, 0))
		self.assertFalse(doc.flags.get("edit_state_changed"))

	def test_a_second_change_on_the_form_keeps_waiting_and_keeps_the_first_times(self):
		_as("pm@example.test")
		stored = {
			"employee": "EMP-PM", "edit_approval": "Waiting", "edited_by": "pm@example.test",
			"edit_previous_start": dt.datetime(2026, 11, 18, 8, 0),
			"edit_previous_end": dt.datetime(2026, 11, 18, 11, 0),
		}
		doc = DirectSave(stored=stored, end_time=self.LATER, edit_approval="Approved")
		self.assertTrue(time_edits.guard_direct_save(doc))
		self.assertEqual(doc.edit_approval, "Waiting")
		self.assertEqual(doc.edit_previous_end, dt.datetime(2026, 11, 18, 11, 0))
		self.assertFalse(doc.flags.get("edit_state_changed"))

	def test_a_reviewer_changing_a_waiting_entry_on_the_form_settles_it(self):
		_as("hr@example.test")
		stored = {
			"edit_approval": "Waiting", "edited_by": "tech@example.test",
			"sync_status": time_edits.HELD_SYNC_STATUS,
		}
		doc = DirectSave(stored=stored, end_time=self.LATER)
		self.assertFalse(time_edits.guard_direct_save(doc))
		self.assertEqual(doc.edit_approval, "Approved")
		self.assertEqual(doc.edit_reviewed_by, "hr@example.test")
		self.assertEqual(doc.sync_status, "Pending")
		self.assertTrue(doc.flags.get("edit_state_changed"))

	def test_the_endpoints_own_saves_are_left_alone(self):
		"""They save with ignore_permissions and have marked the interval already."""
		_as("tech@example.test")
		doc = DirectSave(ignore_permissions=True, end_time=self.LATER, edit_approval="Waiting")
		self.assertFalse(time_edits.guard_direct_save(doc))
		self.assertEqual(doc.edit_approval, "Waiting")
		self.assertIsNone(doc.edited_by)

	def test_an_interval_entered_on_the_form_for_oneself_waits(self):
		_as("pm@example.test")
		doc = DirectSave(new=True, stored={"employee": "EMP-PM"}, edit_approval="Approved")
		self.assertTrue(time_edits.guard_direct_save(doc))
		self.assertEqual(doc.edit_approval, "Waiting")
		self.assertEqual(time_edits.describe_change(doc), "Added by hand")

	def test_the_controller_calls_the_guard_and_brings_the_timesheet_in_step(self):
		source = (JOB_INTERVAL_JSON.parent / "job_interval.py").read_text(encoding="utf-8")
		tree = ast.parse(source)
		cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "JobInterval")
		methods = {n.name: ast.get_source_segment(source, n) for n in cls.body if isinstance(n, ast.FunctionDef)}
		validate = methods["validate"]
		self.assertIn("time_edits.guard_direct_save(self)", validate)
		# After the submitted-day lock refuses, before anything is recomputed.
		self.assertLess(validate.index("assert_not_locked(self)"), validate.index("guard_direct_save(self)"))
		self.assertLess(validate.index("guard_direct_save(self)"), validate.index("assert_no_overlap(self)"))
		self.assertIn('self.flags.get("edit_state_changed")', methods["on_update"])
		self.assertIn("resync_interval_timesheet(self)", methods["on_update"])


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

	def test_an_hr_managers_edit_of_another_persons_interval_does_not_wait(self):
		_as("hr@example.test")
		doc = FakeInterval()
		out = self._update(doc, start_time="2026-11-18 07:30", time_category="Labor")
		self.assertEqual(time_edits.state(doc), "")
		self.assertEqual(out["message"], "Times updated.")
		self.assertEqual(doc.start_time, dt.datetime(2026, 11, 18, 7, 30))

	def test_a_projects_managers_edit_of_another_persons_interval_waits(self):
		_as("pm@example.test")
		doc = FakeInterval()
		out = self._update(doc, start_time="2026-11-18 07:30", time_category="Labor")
		self.assertEqual(time_edits.state(doc), "Waiting")
		self.assertEqual(out["edit_approval"], "Waiting")
		self.assertEqual(doc.edited_by, "pm@example.test")

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


# ----------------------------------------------------------------- who may approve


class TestWhoMayApprove(unittest.TestCase):
	"""Nikolas, 2026-11-19: the employee's reports_to supervisor approves, HR Manager and System
	Manager can approve anything, and Projects Manager is not an approver."""

	def test_hr_and_system_managers_may_approve_anyone(self):
		for user in ("hr@example.test", "sys@example.test"):
			for employee in ("EMP-T", "EMP-O", "EMP-3", "EMP-PM"):
				with self.subTest(user=user, employee=employee):
					self.assertTrue(time_edits.may_approve(employee, user))

	def test_the_supervisor_may_approve_their_direct_report_only(self):
		self.assertTrue(time_edits.may_approve("EMP-T", "lead@example.test"))
		self.assertFalse(time_edits.may_approve("EMP-O", "lead@example.test"))
		self.assertFalse(time_edits.may_approve("EMP-3", "lead@example.test"))

	def test_no_other_role_or_person_may_approve(self):
		for user in ("pm@example.test", "ops@example.test", "other.tech@example.test", "tech@example.test"):
			with self.subTest(user=user):
				self.assertFalse(time_edits.may_approve("EMP-T", user))

	def test_a_supervisor_whose_login_is_disabled_is_not_an_approver(self):
		"""EMP-3 reports to a disabled login: HR Manager and System Manager decide."""
		self.assertIsNone(time_edits.supervisor_user("EMP-3"))
		self.assertFalse(time_edits.may_approve("EMP-3", "gone.lead@example.test"))
		self.assertTrue(time_edits.may_approve("EMP-3", "hr@example.test"))

	def test_an_employee_with_no_supervisor_is_decided_by_hr_or_system_managers(self):
		self.assertIsNone(time_edits.supervisor_user("EMP-O"))
		self.assertTrue(time_edits.may_approve("EMP-O", "sys@example.test"))
		self.assertFalse(time_edits.may_approve("EMP-O", "lead@example.test"))

	def test_may_decide_refuses_the_employee_and_the_editor(self):
		doc = FakeInterval(edit_approval="Waiting", edited_by="hr2@example.test")
		self.assertTrue(time_edits.may_decide(doc, "lead@example.test"))
		self.assertTrue(time_edits.may_decide(doc, "hr@example.test"))
		self.assertFalse(time_edits.may_decide(doc, "hr2@example.test"), "the editor")
		self.assertFalse(time_edits.may_decide(doc, "pm@example.test"))
		own = FakeInterval(employee="EMP-HR", edit_approval="Waiting", edited_by="tech@example.test")
		self.assertFalse(time_edits.may_decide(own, "hr@example.test"), "the employee")

	def test_the_time_correction_review_is_untouched(self):
		"""The hand-edit rule is its own; the Time Correction Request gate keeps Projects Manager."""
		source = (APP / "workforce" / "time_edits.py").read_text(encoding="utf-8")
		tree = ast.parse(source)
		imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
		called = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
		self.assertNotIn("permissions", imported)
		self.assertNotIn("can_review", called)
		perms = (APP / "workforce" / "permissions.py").read_text(encoding="utf-8")
		self.assertIn('TCR_MANAGER_ROLES = {"System Manager", "HR Manager", "Projects Manager"}', perms)
		self.assertEqual(set(time_edits.APPROVER_ROLES), {"HR Manager", "System Manager"})


# ----------------------------------------------------------------- telling the approver


#: Plain-text stand-ins for the email shell's macros, which need frappe's Jinja to render.
#: ``tests/test_email_design.py`` holds the real macros; here the words are what is checked.
_EMAIL_STANDINS = {
	"p": lambda text: f"<p>{text}</p>",
	"kv": lambda rows: "".join(f"<kv>{k}: {v}</kv>" for k, v in rows),
	"button": lambda url, label, tone="primary": f"<a href='{url}'>{label}</a>",
	"wrap": lambda body, **kw: f"<shell title='{kw.get('title')}'>{body}</shell>",
}

NOTICE_FAILED = "Time edit notice failed: JOB-INT-00001"


class _NoticeCase(unittest.TestCase):
	def setUp(self):
		from erpnext_enhancements import email_style

		frappe.sent.clear()
		frappe.bells.clear()
		frappe.errors.clear()
		self._sendmail = frappe.sendmail
		self._get_doc = frappe.get_doc
		self._email_style = {name: getattr(email_style, name) for name in _EMAIL_STANDINS}
		for name, standin in _EMAIL_STANDINS.items():
			setattr(email_style, name, standin)
		AFTER_SAVE.clear()

	def tearDown(self):
		from erpnext_enhancements import email_style

		frappe.sendmail = self._sendmail
		frappe.get_doc = self._get_doc
		for name, original in self._email_style.items():
			setattr(email_style, name, original)
		AFTER_SAVE.clear()

	def told(self):
		return [b["for_user"] for b in frappe.bells]

	def mailed(self):
		return [m["recipients"] for m in frappe.sent]


class TestTellingTheApprover(_NoticeCase):
	def test_a_wait_starting_marks_the_interval_once(self):
		_as("tech@example.test")
		for before in (None, "Approved", "Sent Back"):
			with self.subTest(before=before):
				doc = FakeInterval(edit_approval=before)
				time_edits.record_edit(doc)
				self.assertTrue(doc.flags.get("edit_wait_started"))
		doc = FakeInterval(edit_approval="Waiting", edited_by="tech@example.test")
		time_edits.record_edit(doc)
		self.assertFalse(doc.flags.get("edit_wait_started"), "a second edit in the same spell")

	def test_an_edit_that_does_not_wait_marks_nothing(self):
		_as("hr@example.test")
		doc = FakeInterval()
		time_edits.record_edit(doc)
		self.assertFalse(doc.flags.get("edit_wait_started"))
		self.assertEqual(time_edits.notify_wait_started(doc), [])
		self.assertEqual(frappe.bells, [])

	def test_the_supervisor_is_told_with_a_bell_and_an_email(self):
		_as("tech@example.test")
		doc = FakeInterval(time_edit_reason="forgot to clock out")
		time_edits.record_edit(doc)
		doc.end_time = dt.datetime(2026, 11, 18, 13, 0)
		self.assertEqual(time_edits.notify_wait_started(doc), ["lead@example.test"])
		self.assertEqual(self.told(), ["lead@example.test"])
		bell = frappe.bells[0]
		self.assertEqual(bell["type"], "Alert", "Frappe never emails an Alert, so one email only")
		self.assertEqual(bell["link"], "/desk/time-edit-approvals")
		self.assertEqual(bell["document_name"], doc.name)
		self.assertIn("Name EMP-T", bell["subject"])
		self.assertIn("2026-11-18", bell["subject"])
		self.assertEqual(self.mailed(), [["lead@example.test"]])
		mail = frappe.sent[0]
		for text in (
			"Name EMP-T", "2026-11-18", "Title PROJ-1", "End 2026-11-18 12:00 to 2026-11-18 13:00",
			"forgot to clock out", "Full tech@example.test", "/desk/time-edit-approvals",
		):
			with self.subTest(text=text):
				self.assertIn(text, mail["message"])
		self.assertNotIn("rate", mail["message"].lower())
		self.assertNotIn("cost", mail["message"].lower())

	def test_with_no_supervisor_the_hr_managers_are_told(self):
		_as("other.tech@example.test")
		doc = FakeInterval(employee="EMP-O")
		time_edits.record_edit(doc, added=True)
		self.assertEqual(time_edits.notify_wait_started(doc), ["hr2@example.test", "hr@example.test"])
		self.assertEqual(sorted(self.told()), ["hr2@example.test", "hr@example.test"])
		self.assertNotIn("sys@example.test", self.told(), "the fallback is HR Manager")
		self.assertIn("Added by hand", frappe.sent[0]["message"])

	def test_a_supervisor_with_a_disabled_login_hands_the_notice_to_hr(self):
		_as("third.tech@example.test")
		doc = FakeInterval(employee="EMP-3")
		time_edits.record_edit(doc)
		time_edits.notify_wait_started(doc)
		self.assertEqual(sorted(self.told()), ["hr2@example.test", "hr@example.test"])

	def test_never_the_employee_or_the_editor(self):
		"""An HR Manager's own time waits; the other HR Manager is told, never the one who edited."""
		_as("hr@example.test")
		doc = FakeInterval(employee="EMP-HR")
		time_edits.record_edit(doc)
		self.assertEqual(time_edits.notify_wait_started(doc), ["hr2@example.test"])
		doc = FakeInterval(employee="EMP-O", edit_approval=None)
		_as("pm@example.test")
		time_edits.record_edit(doc)
		frappe.bells.clear()
		time_edits.notify_wait_started(doc)
		self.assertNotIn("pm@example.test", self.told())
		self.assertNotIn("other.tech@example.test", self.told())

	def test_once_per_waiting_spell(self):
		_as("tech@example.test")
		doc = FakeInterval()
		time_edits.record_edit(doc)
		self.assertEqual(time_edits.notify_wait_started(doc), ["lead@example.test"])
		self.assertEqual(time_edits.notify_wait_started(doc), [], "the flag is spent")
		time_edits.record_edit(doc)  # a second edit before anyone decides
		self.assertEqual(time_edits.notify_wait_started(doc), [])
		self.assertEqual(len(frappe.bells), 1)
		doc.edit_approval = "Sent Back"  # sent back, then edited again: a new spell
		time_edits.record_edit(doc)
		self.assertEqual(time_edits.notify_wait_started(doc), ["lead@example.test"])
		self.assertEqual(len(frappe.bells), 2)

	def test_a_failed_notice_never_raises(self):
		_as("tech@example.test")

		def _boom(**kw):
			raise RuntimeError("mail server down")

		frappe.sendmail = _boom
		doc = FakeInterval()
		time_edits.record_edit(doc)
		self.assertEqual(time_edits.notify_wait_started(doc), [])
		self.assertEqual(frappe.errors, [NOTICE_FAILED])
		self.assertEqual(doc.edit_approval, "Waiting", "the edit stands")

	def test_a_failed_bell_or_lookup_never_raises(self):
		_as("tech@example.test")
		frappe.get_doc = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no table"))
		doc = FakeInterval()
		time_edits.record_edit(doc)
		self.assertEqual(time_edits.notify_wait_started(doc), [])
		original = time_edits.approvers_to_notify
		time_edits.approvers_to_notify = lambda d: (_ for _ in ()).throw(RuntimeError("db gone"))
		try:
			doc = FakeInterval()
			time_edits.record_edit(doc)
			self.assertEqual(time_edits.notify_wait_started(doc), [])
		finally:
			time_edits.approvers_to_notify = original
		self.assertEqual(frappe.errors, [NOTICE_FAILED, NOTICE_FAILED])


class TestEachPathTellsTheApproverAfterTheSave(_NoticeCase):
	"""Every path that can start a wait reaches ``JobInterval.on_update``, which tells the
	approver; ``validate`` (``guard_direct_save``) sends nothing."""

	def setUp(self):
		super().setUp()
		AFTER_SAVE.append(_run_on_update)
		self.fake_corrections = types.ModuleType("erpnext_enhancements.workforce.corrections")
		self.fake_corrections._record_originals = lambda interval: None
		self._saved = sys.modules.get("erpnext_enhancements.workforce.corrections")
		sys.modules["erpnext_enhancements.workforce.corrections"] = self.fake_corrections

	def tearDown(self):
		super().tearDown()
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
			"resync_interval_timesheet": lambda d: None,
		}
		return _load_kiosk_function("update_interval_times", ns)(interval=doc.name, reason="forgot", **kwargs)

	def _add(self, end_time="2026-11-18 10:00"):
		built = {}

		def _new_interval(employee, project, task, time_category, description, now_dt, **kw):
			built["doc"] = FakeInterval(employee=employee, project=project, start_time=now_dt, end_time=None,
			                            status="Open", sync_status="Pending")
			return built["doc"]

		ns = {
			"frappe": frappe, "_": frappe._, "get_datetime": frappe.utils.get_datetime,
			"now_datetime": lambda: NOW, "_new_interval": _new_interval,
			"_require_session_employee": lambda: PEOPLE[frappe.session.user][0],
			"_assert_may_edit_time_for": lambda employee: PEOPLE[frappe.session.user][0],
			"photo_gate": types.SimpleNamespace(stamp=lambda *a, **k: None, resolve=lambda d: "Skipped"),
			"resync_interval_timesheet": lambda d: None,
		}
		frappe.db.get_value("Employee", "EMP-T", "name")  # the stub answers None; no lock needed
		out = _load_kiosk_function("add_manual_interval", ns)(
			project="PROJ-1", start_time="2026-11-18 08:00", end_time=end_time, reason="forgot", time_category="Labor"
		)
		return out, built["doc"]

	def test_the_kiosk_edit_tells_the_supervisor_once(self):
		_as("tech@example.test")
		doc = FakeInterval()
		self._update(doc, end_time="2026-11-18 13:00")
		self.assertEqual(self.told(), ["lead@example.test"])
		self.assertEqual(self.mailed(), [["lead@example.test"]])
		self._update(doc, end_time="2026-11-18 13:30")  # a second edit before a decision
		self.assertEqual(len(frappe.bells), 1)

	def test_time_added_by_hand_tells_the_supervisor(self):
		_as("tech@example.test")
		out, doc = self._add()
		self.assertEqual(out["edit_approval"], "Waiting")
		self.assertTrue(doc.inserted)
		self.assertEqual(self.told(), ["lead@example.test"])
		self.assertIn("Added by hand", frappe.bells[0]["email_content"])

	def test_an_approvers_kiosk_edit_tells_nobody(self):
		_as("hr@example.test")
		self._update(FakeInterval(), end_time="2026-11-18 13:00")
		self.assertEqual(frappe.bells, [])
		self.assertEqual(frappe.sent, [])

	def test_a_failed_notice_does_not_fail_the_kiosk_edit(self):
		_as("tech@example.test")
		frappe.sendmail = lambda **kw: (_ for _ in ()).throw(RuntimeError("mail server down"))
		doc = FakeInterval()
		out = self._update(doc, end_time="2026-11-18 13:00")
		self.assertEqual(out["status"], "success")
		self.assertEqual(out["edit_approval"], "Waiting")
		self.assertEqual(frappe.errors, [NOTICE_FAILED])

	def test_the_desk_form_tells_after_the_save_and_not_in_validate(self):
		_as("pm@example.test")
		doc = DirectSave(end_time=dt.datetime(2026, 11, 18, 14, 0))
		self.assertTrue(time_edits.guard_direct_save(doc))  # validate
		self.assertEqual(frappe.bells, [], "nothing is sent before the save is known to succeed")
		resynced = []
		fake_kiosk = types.ModuleType("erpnext_enhancements.api.time_kiosk")
		fake_kiosk.resync_interval_timesheet = lambda d: resynced.append(d.name)
		saved = sys.modules.get("erpnext_enhancements.api.time_kiosk")
		sys.modules["erpnext_enhancements.api.time_kiosk"] = fake_kiosk
		try:
			_run_on_update(doc)  # the save went through
			_run_on_update(doc)  # a later save of the same document
		finally:
			if saved is None:
				sys.modules.pop("erpnext_enhancements.api.time_kiosk", None)
			else:
				sys.modules["erpnext_enhancements.api.time_kiosk"] = saved
		self.assertEqual(self.told(), ["lead@example.test"])
		self.assertEqual(resynced, [doc.name, doc.name])

	def test_a_desk_save_that_does_not_wait_tells_nobody(self):
		_as("hr@example.test")
		doc = DirectSave(end_time=dt.datetime(2026, 11, 18, 14, 0))
		self.assertFalse(time_edits.guard_direct_save(doc))
		_run_on_update(doc)
		self.assertEqual(frappe.bells, [])

	def test_the_controller_notifies_from_on_update_only(self):
		source = (JOB_INTERVAL_JSON.parent / "job_interval.py").read_text(encoding="utf-8")
		tree = ast.parse(source)
		cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "JobInterval")
		methods = {n.name: ast.get_source_segment(source, n) for n in cls.body if isinstance(n, ast.FunctionDef)}
		self.assertIn("time_edits.notify_wait_started(self)", methods["on_update"])
		self.assertNotIn("notify_wait_started", methods["validate"])
		edits = (APP / "workforce" / "time_edits.py").read_text(encoding="utf-8")
		guard = ast.get_source_segment(
			edits, next(n for n in ast.parse(edits).body if isinstance(n, ast.FunctionDef) and n.name == "guard_direct_save")
		)
		self.assertNotIn("notify_wait_started(", guard)
		self.assertNotIn("sendmail", guard)


# ----------------------------------------------------------------- the approvals page


class TestTheApprovalsPage(unittest.TestCase):
	"""``get_waiting_edits`` decides on the server what the caller may decide."""

	WAITING_ROWS = [
		# EMP-T reports to lead@: edited by the technician
		_Dict(name="JI-A", employee="EMP-T", project="PROJ-1", start_time=dt.datetime(2026, 11, 17, 8, 0),
		      end_time=dt.datetime(2026, 11, 17, 13, 0), edit_previous_start=dt.datetime(2026, 11, 17, 8, 0),
		      edit_previous_end=dt.datetime(2026, 11, 17, 12, 0), edited_by="tech@example.test",
		      edited_on=dt.datetime(2026, 11, 17, 18, 0), time_edit_reason="forgot to clock out",
		      manual_start_reason=None, pay_rate=99, labor_cost=999),
		# EMP-T again, edited by the lead themself: not the lead's to decide
		_Dict(name="JI-B", employee="EMP-T", project="PROJ-2", start_time=dt.datetime(2026, 11, 16, 8, 0),
		      end_time=dt.datetime(2026, 11, 16, 9, 0), edit_previous_start=None, edit_previous_end=None,
		      edited_by="lead@example.test", edited_on=dt.datetime(2026, 11, 16, 18, 0),
		      time_edit_reason=None, manual_start_reason="by hand"),
		# EMP-O has no supervisor
		_Dict(name="JI-C", employee="EMP-O", project="PROJ-1", start_time=dt.datetime(2026, 11, 15, 8, 0),
		      end_time=None, edit_previous_start=None, edit_previous_end=None,
		      edited_by="other.tech@example.test", edited_on=dt.datetime(2026, 11, 15, 9, 0),
		      time_edit_reason=None, manual_start_reason="forgot to clock in"),
		# HR's own time
		_Dict(name="JI-D", employee="EMP-HR", project="PROJ-1", start_time=dt.datetime(2026, 11, 14, 8, 0),
		      end_time=dt.datetime(2026, 11, 14, 9, 0), edit_previous_start=None, edit_previous_end=None,
		      edited_by="hr@example.test", edited_on=dt.datetime(2026, 11, 14, 9, 0),
		      time_edit_reason=None, manual_start_reason="x"),
	]

	def setUp(self):
		self.queries = []
		self._get_all = frappe.get_all
		frappe.get_all = self._fake_get_all

	def tearDown(self):
		frappe.get_all = self._get_all

	def _fake_get_all(self, doctype, filters=None, fields=None, pluck=None, **kw):
		self.queries.append((doctype, dict(filters or {}), fields))
		filters = filters or {}
		if doctype == "Employee" and "reports_to" in filters:
			return [e for e, sup in REPORTS_TO.items() if sup == filters["reports_to"]]
		if doctype == "Job Interval":
			self.assertEqual(filters.get("edit_approval"), "Waiting")
			rows = list(self.WAITING_ROWS)
			if "employee" in filters:
				op, names = filters["employee"]
				self.assertEqual(op, "in")
				rows = [r for r in rows if r.employee in names]
			return [_Dict({f: r.get(f) for f in fields}) for r in rows]
		names = filters.get("name", ["in", []])[1]
		field = fields[1]
		return [_Dict(name=n, **{field: f"{doctype} label {n}"}) for n in names]

	def _intervals(self, user):
		_as(user)
		return [e["interval"] for e in time_edits.get_waiting_edits()["entries"]]

	def test_it_is_a_get_read(self):
		self.assertEqual(time_edits.get_waiting_edits._methods, ["GET"])

	def test_a_supervisor_sees_their_reports_entries_but_not_their_own_edits(self):
		self.assertEqual(self._intervals("lead@example.test"), ["JI-A"])
		job_queries = [f for d, f, _x in self.queries if d == "Job Interval"]
		self.assertEqual(job_queries[-1]["employee"], ["in", ["EMP-T"]], "the server limits the query")

	def test_hr_and_system_managers_see_everyone_less_their_own(self):
		self.assertEqual(self._intervals("hr@example.test"), ["JI-A", "JI-B", "JI-C"])
		self.assertEqual(self._intervals("sys@example.test"), ["JI-A", "JI-B", "JI-C", "JI-D"])

	def test_others_see_nothing(self):
		for user in ("pm@example.test", "tech@example.test", "ops@example.test", "gone.lead@example.test"):
			with self.subTest(user=user):
				self.assertEqual(self._intervals(user), [])

	def test_it_takes_no_arguments_from_the_caller(self):
		import inspect

		self.assertEqual(list(inspect.signature(time_edits.get_waiting_edits).parameters), [])

	def test_each_entry_carries_only_what_the_page_shows(self):
		_as("hr@example.test")
		out = time_edits.get_waiting_edits()
		self.assertEqual(out["scope"], "all")
		first = out["entries"][0]
		self.assertEqual(
			set(first),
			{"interval", "employee_name", "day", "project", "project_name", "added", "before_start", "before_end",
			 "start", "end", "change", "reason", "edited_by", "edited_on"},
		)
		self.assertEqual(first["employee_name"], "Employee label EMP-T")
		self.assertEqual(first["day"], "2026-11-17")
		self.assertFalse(first["added"])
		self.assertEqual(first["before_end"], "2026-11-17 12:00:00")
		self.assertEqual(first["end"], "2026-11-17 13:00:00")
		self.assertEqual(first["reason"], "forgot to clock out")
		self.assertEqual(first["edited_by"], "User label tech@example.test")
		self.assertTrue(out["entries"][1]["added"])
		self.assertEqual(out["entries"][1]["change"], "Added by hand")
		self.assertIsNone(out["entries"][2]["end"], "still open")
		fields = next(f for d, _f, f in self.queries if d == "Job Interval")
		self.assertFalse({"pay_rate", "labor_cost", "burdened_rate", "pay_type"} & set(fields))

	def test_a_supervisor_with_no_reports_gets_an_empty_list_without_a_query(self):
		_as("other.tech@example.test")
		self.assertEqual(time_edits.get_waiting_edits(), {"entries": [], "scope": "team"})
		self.assertNotIn("Job Interval", [d for d, _f, _x in self.queries])


class TestThePageFiles(unittest.TestCase):
	PAGE = APP / "workforce" / "page" / "time_edit_approvals"

	def test_the_page_opens_to_every_staff_member_and_the_approver_roles(self):
		doc = json.loads((self.PAGE / "time_edit_approvals.json").read_text(encoding="utf-8"))
		self.assertEqual(doc["name"], "time-edit-approvals")
		self.assertEqual(doc["module"], "Workforce")
		self.assertEqual({r["role"] for r in doc["roles"]}, {"Employee", "HR Manager", "System Manager"})
		self.assertEqual(time_edits.APPROVALS_PAGE, doc["name"])

	def test_the_script_calls_only_the_three_endpoints(self):
		js = (self.PAGE / "time_edit_approvals.js").read_text(encoding="utf-8")
		self.assertIn('frappe.pages["time-edit-approvals"]', js)
		self.assertIn('TEA_API + "get_waiting_edits"', js)
		self.assertIn('type: "GET"', js)
		for method in ("approve_time_edit", "send_back_time_edit"):
			with self.subTest(method=method):
				self.assertIn(f'"{method}"', js)
				self.assertEqual(getattr(time_edits, method)._methods, ["POST"])
		self.assertNotIn("employee:", js, "the page sends no employee list")
		self.assertIn("tea_esc(", js)


if __name__ == "__main__":
	unittest.main()
