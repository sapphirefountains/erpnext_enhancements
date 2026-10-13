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

And, v1.597.0: the name a visit is given (``PRJ-00179-2026-V001``, the controller's ``autoname``)
and the check that its fountain photo was taken on it. v1.598.0: a cleaning task's three states
(done, N/A, not done with a reason) in the record's own rules. v1.610.0: the technician signs
before the visit goes for review, the signature is stamped with who and when, and only a drawn
image is taken into either signature field.

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
	"frappe.model.naming",
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

	def get_doc_before_save(self):
		return self.__dict__.get("_before")


TODAY = D(2026, 10, 12)
NOW = DT(2026, 10, 12, 10, 30)
#: A drawn signature as the wizard's pad and the Desk's Signature control send it.
SIGNED = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


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
			"series": {},
			"files": [],
			"user": "austin@example.com",
		}
	)


class _Thrown(Exception):
	pass


class _Session:
	@property
	def user(self):
		return STATE["user"]


def _make_autoname(key, doctype="", doc=None):
	"""frappe.model.naming.make_autoname for a ``PREFIX.###`` key: one counter per prefix."""
	assert doc is not None, "the controller passes itself"
	prefix, hashes = key.rsplit(".", 1)
	assert set(hashes) == {"#"}, key
	assert "." not in prefix, "a dot in the prefix would split it into naming parts"
	STATE["series"][prefix] = STATE["series"].get(prefix, 0) + 1
	return f"{prefix}{STATE['series'][prefix]:0{len(hashes)}d}"


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
		if doctype == "File":
			assert set(filters) == {"file_url"}, filters
			return [
				_Dict({f: row.get(f) for f in fields})
				for row in STATE["files"]
				if row.file_url == filters["file_url"]
			]
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
	frappe.session = _Session()

	def _throw(message, *args, **kwargs):
		raise _Thrown(message)

	frappe.throw = _throw
	frappe.db = types.SimpleNamespace(get_value=_get_value, set_value=_set_value, exists=lambda *a: True)

	utils = types.ModuleType("frappe.utils")
	utils.flt = lambda v, *a: float(v or 0)
	utils.getdate = _getdate
	utils.nowdate = lambda: str(STATE["today"])
	utils.escape_html = lambda text: text
	utils.now_datetime = lambda: STATE["now"]
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = _Document
	model.document = document
	naming = types.ModuleType("frappe.model.naming")
	naming.make_autoname = _make_autoname
	model.naming = naming
	frappe.model = model

	crew = types.ModuleType(CREW_MODULE)
	crew.validate_crew = lambda doc: None

	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.utils": utils,
			"frappe.model": model,
			"frappe.model.document": document,
			"frappe.model.naming": naming,
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
		"doctype": "Sapphire Maintenance Record",
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
		# Signed (v1.610.0): going for review needs it, and the other suites here are not about it.
		"technician_signature": SIGNED,
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


class TestTheReportNumber(unittest.TestCase):
	"""Lisa (2026-10-12): could the report number be the PRJ number? Nik: with the year."""

	def setUp(self):
		_reset()

	def _named(self, **fields):
		doc = _visit(name=None, **fields)
		doc.autoname()
		return doc.name

	def test_project_year_and_the_visits_number_that_year(self):
		self.assertEqual(self._named(project="PRJ-00179", visit_date="2026-10-06"), "PRJ-00179-2026-V001")
		self.assertEqual(self._named(project="PRJ-00179", visit_date="2026-10-20"), "PRJ-00179-2026-V002")
		self.assertEqual(self._named(project="PRJ-00228", visit_date="2026-10-20"), "PRJ-00228-2026-V001")

	def test_the_year_is_the_visits_own(self):
		"""Drafted in December for a January visit: it counts in January's year."""
		_reset(today=D(2026, 12, 28))
		self.assertEqual(
			self._named(project="PRJ-00179", visit_date=None, scheduled_visit_date="2027-01-05"),
			"PRJ-00179-2027-V001",
		)
		self.assertEqual(
			self._named(project="PRJ-00179", visit_date="2026-12-30", scheduled_visit_date="2027-01-05"),
			"PRJ-00179-2026-V001",
			"the day it was done outranks the day it was planned",
		)
		self.assertEqual(
			self._named(project="PRJ-00179", visit_date=None, scheduled_visit_date=None),
			"PRJ-00179-2026-V002",
		)

	def test_a_visit_with_no_project_is_left_to_frappe(self):
		self.assertIsNone(self._named(project=None))
		self.assertEqual(STATE["series"], {})

	def test_a_project_name_that_could_not_lead_a_series_is_left_to_frappe(self):
		"""v16's make_autoname refuses a series outside ``^[\\w\\- /.#{}]+$`` and reads ``.``, ``#``
		and braces as syntax. Every project is PRJ-##### today, but Project allows rename, and
		an insert that raised here would stop the nightly scheduler at that contract."""
		for project in ("PRJ.1", "PRJ-#1", "PRJ-{x}", "Myers (Sanctuary)", "O'Brien", "A&B", "A/B", "P" * 61):
			with self.subTest(project=project):
				self.assertIsNone(self._named(project=project))
		self.assertEqual(STATE["series"], {})
		self.assertEqual(
			self._named(project="Myers Sanctuary", visit_date="2026-10-06"), "Myers Sanctuary-2026-V001"
		)

	def test_the_doctype_does_not_name_it_first(self):
		"""v16 ``set_new_name`` runs a Document Naming Rule, then the controller's ``autoname``,
		then the DocType's own option, then ``hash``. But an ``autoincrement`` or ``UUID`` option
		returns before the controller is asked, and ``prompt`` keeps a typed name, so the JSON
		must stay on the default."""
		import json

		meta = json.loads(
			(
				APP
				/ "sapphire_maintenance/doctype/sapphire_maintenance_record/sapphire_maintenance_record.json"
			).read_text(encoding="utf-8")
		)
		self.assertIn(meta.get("autoname") or "hash", ("hash", ""))


class TestTheTechnicianSigns(unittest.TestCase):
	"""v1.610.0. Nik, 2026-10-13: "we need the technicians to sign the maintenance visits once
	they complete it so the printed form has that." Required on the move into review only."""

	def setUp(self):
		_reset()

	def test_going_for_review_unsigned_is_refused(self):
		with self.assertRaises(_Thrown) as caught:
			_send_for_review(_visit(technician_signature=None))
		self.assertEqual(str(caught.exception), "The technician must sign the visit before it goes for review.")

	def test_signed_it_goes(self):
		doc = _send_for_review(_visit())
		self.assertEqual(doc.visit_date, D(2026, 10, 12))

	def test_nothing_else_asks_for_it(self):
		"""Visits already waiting, a reviewer's edit, Send Back, autosaves, and approval."""
		cases = {
			"an autosave of a draft": _visit(technician_signature=None, changed=()),
			"a reviewer's edit while pending": _visit(state="Pending Review", technician_signature=None, changed=()),
			"Send Back": _visit(state="Draft", technician_signature=None),
			"a submitted visit": _visit(state="Pending Review", docstatus=1, technician_signature=None),
		}
		for label, doc in cases.items():
			with self.subTest(label):
				doc.validate()
		approved = _visit(state="Pending Review", technician_signature=None, changed=())
		approved.before_submit()

	def test_a_new_signature_is_stamped_with_who_saved_it_and_when(self):
		"""The session's user, not the visit's technician: a substitute signs as themselves."""
		STATE["user"] = "korben@example.com"
		doc = _visit(changed=("technician_signature",))
		doc.validate()
		self.assertEqual(
			(doc.technician_signed_by, doc.technician_signed_on), ("korben@example.com", NOW)
		)

	def test_a_signature_left_as_it_was_keeps_its_stamp(self):
		before = _Dict(technician_signed_by="austin@example.com", technician_signed_on=DT(2026, 10, 9, 7, 0))
		STATE["user"] = "lisa@example.com"
		doc = _visit(
			state="Pending Review",
			changed=(),
			_before=before,
			technician_signed_by="lisa@example.com",
			technician_signed_on=NOW,
		)
		doc.validate()
		self.assertEqual(
			(doc.technician_signed_by, doc.technician_signed_on), ("austin@example.com", DT(2026, 10, 9, 7, 0))
		)

	def test_clearing_it_clears_the_stamp(self):
		doc = _visit(
			technician_signature="",
			changed=("technician_signature",),
			technician_signed_by="austin@example.com",
			technician_signed_on=NOW,
		)
		doc.validate()
		self.assertEqual((doc.technician_signed_by, doc.technician_signed_on), (None, None))

	def test_only_a_drawn_image_is_taken(self):
		"""Both fields are printed unescaped into an img src: nothing that can leave the attribute."""
		bad = (
			"https://example.com/sig.png",
			'data:image/png;base64,AAAA" onerror="alert(1)',
			"data:image/svg+xml;base64,PHN2Zz4=",
			"data:text/html;base64,PHNjcmlwdD4=",
			"javascript:alert(1)",
		)
		for value in bad:
			with self.subTest(technician=value), self.assertRaises(_Thrown):
				_visit(technician_signature=value, changed=("technician_signature",)).validate()
			with self.subTest(client=value), self.assertRaises(_Thrown):
				_visit(client_sign_off=value, changed=("client_sign_off",)).validate()
		for value in (SIGNED, "data:image/jpeg;base64,/9j/4AAQ"):
			with self.subTest(client=value):
				_visit(client_sign_off=value, changed=("client_sign_off",)).validate()
		with self.assertRaises(_Thrown):
			_visit(technician_signature="data:image/jpeg;base64,/9j/4AAQ", changed=("technician_signature",)).validate()

	def test_a_value_already_held_is_not_checked_again(self):
		"""A visit is never made unsaveable by what it already holds."""
		_visit(client_sign_off="data:legacy", technician_signature="data:legacy", changed=()).validate()


class TestVisitsFromBeforeSignatures(unittest.TestCase):
	"""v1.614.0. Nik: "exemption is fine": a visit done before signatures existed (v1.610.0,
	2026-10-13) goes for review without one. A blank scheduler draft does not: thirteen of them,
	0% complete and undated, were sent for review in one bulk Request Review on 2026-10-13, and
	the move would have dated them today and, on approval, rolled their contracts forward."""

	def setUp(self):
		_reset(today=D(2026, 10, 14), now=DT(2026, 10, 14, 9, 0))

	def test_the_cutoff_is_named_and_is_the_day_signatures_came_in(self):
		self.assertEqual(record_module.TECHNICIAN_SIGNATURE_FROM, D(2026, 10, 13))

	def test_a_visit_done_the_day_before_goes_unsigned(self):
		doc = _send_for_review(_visit(technician_signature=None, visit_date="2026-10-12"))
		self.assertEqual(doc.visit_date, "2026-10-12", "a date the technician chose is kept")

	def test_a_blank_draft_is_still_refused(self):
		with self.assertRaises(_Thrown) as caught:
			_send_for_review(_visit(technician_signature=None, visit_date=None))
		self.assertEqual(str(caught.exception), "The technician must sign the visit before it goes for review.")

	def test_the_cutoff_day_and_after_are_refused(self):
		for day in ("2026-10-13", "2026-10-14", D(2026, 10, 13)):
			with self.subTest(day=day), self.assertRaises(_Thrown):
				_send_for_review(_visit(technician_signature=None, visit_date=day))


class TestTheClientSignsOnSite(unittest.TestCase):
	"""v1.614.0: a client signature drawn on site is stamped "On site" and the time, so the report
	can tell it from one signed from an emailed link (which never passes through validate)."""

	def setUp(self):
		_reset()

	def test_a_new_one_is_stamped_on_site_and_when(self):
		doc = _visit(client_sign_off=SIGNED, changed=("client_sign_off",))
		doc.validate()
		self.assertEqual(
			(doc.client_sign_off_method, doc.client_signed_on, doc.client_signed_by), ("On site", NOW, None)
		)

	def test_one_left_as_it_was_keeps_its_stamp(self):
		"""An emailed sign-off, then a reviewer's save: still "By email", by whom it was."""
		doc = _visit(
			state="Pending Review",
			changed=(),
			client_sign_off=SIGNED,
			client_sign_off_method="By email",
			client_signed_by="Jane Owner",
			client_signed_on=DT(2026, 10, 12, 18, 0),
		)
		doc.validate()
		self.assertEqual(
			(doc.client_sign_off_method, doc.client_signed_by, doc.client_signed_on),
			("By email", "Jane Owner", DT(2026, 10, 12, 18, 0)),
		)

	def test_clearing_it_clears_the_stamp(self):
		doc = _visit(
			client_sign_off="",
			changed=("client_sign_off",),
			client_sign_off_method="On site",
			client_signed_on=NOW,
		)
		doc.validate()
		self.assertEqual((doc.client_sign_off_method, doc.client_signed_on, doc.client_signed_by), (None, None, None))


class TestTheFountainPhoto(unittest.TestCase):
	"""frappe's on_update hook attaches any unattached File an Attach field names, with no
	permission check, and the customer's report inlines what is attached to the visit."""

	def setUp(self):
		_reset()

	def _file(self, url, owner="lisa@example.com", attached=(None, None)):
		STATE["files"].append(
			_Dict(file_url=url, owner=owner, attached_to_doctype=attached[0], attached_to_name=attached[1])
		)

	def _save(self, url):
		doc = _visit(changed=("fountain_photo",), fountain_photo=url)
		doc._check_fountain_photo()

	def test_a_photo_attached_to_this_visit_or_uploaded_by_the_saver_is_kept(self):
		self._file("/private/files/a.jpg", attached=("Sapphire Maintenance Record", "SMR-1"))
		self._file("/private/files/b.jpg", owner="austin@example.com")
		self._file(
			"/private/files/c.jpg",
			owner="austin@example.com",
			attached=("Sapphire Maintenance Record", "new-1"),
		)
		for url in ("/private/files/a.jpg", "/private/files/b.jpg", "/private/files/c.jpg", None, ""):
			with self.subTest(url=url):
				self._save(url)

	def test_someone_elses_file_is_refused(self):
		self._file("/private/files/payroll.png")
		self._file("/private/files/theirs.jpg", attached=("Sapphire Maintenance Record", "SMR-2"))
		for url in (
			"/private/files/payroll.png",
			"/private/files/theirs.jpg",
			"/private/files/gone.jpg",
			"https://x.test/a.jpg",
		):
			with self.subTest(url=url), self.assertRaises(_Thrown):
				self._save(url)

	def test_an_unchanged_photo_is_not_checked_again(self):
		"""A reviewer saving the visit later is not refused over the technician's upload."""
		doc = _visit(changed=("workflow_state",), fountain_photo="/private/files/gone.jpg")
		doc._check_fountain_photo()

	def test_validate_runs_it(self):
		self._file("/private/files/payroll.png")
		doc = _visit(changed=("fountain_photo",), fountain_photo="/private/files/payroll.png")
		with self.assertRaises(_Thrown):
			doc.validate()


class TestCleaningTaskStates(unittest.TestCase):
	"""v1.598.0 (D2): done, N/A, or not done with a note saying why. Lisa, on the Myers report:
	"It only states that one cartridge wasn't cleaned out; why wasn't it?"
	"""

	#: The same table as the wizard harness's "the cleaning-task rule matches" test.
	CASES = [
		({"is_done": 1, "not_applicable": 0, "notes": ""}, "done", True),
		({"is_done": 0, "not_applicable": 1, "notes": ""}, "na", True),
		({"is_done": 1, "not_applicable": 1, "notes": ""}, "done", True),
		({"is_done": 0, "not_applicable": 0, "notes": "Pump off"}, "open", True),
		({"is_done": 0, "not_applicable": 0, "notes": "   "}, "open", False),
		({"is_done": 0, "not_applicable": 0, "notes": None}, "open", False),
	]

	def setUp(self):
		_reset()

	def test_the_rule(self):
		from erpnext_enhancements.sapphire_maintenance import task_rules

		for row, state, answered in self.CASES:
			with self.subTest(row=row):
				self.assertEqual(task_rules.task_state(row), state)
				self.assertEqual(task_rules.task_answered(row), answered)
				self.assertEqual(task_rules.task_unexplained(row), not answered)

	def test_the_desk_form_clears_one_box_when_the_other_is_ticked(self):
		"""Else validate's "done wins" undoes a reviewer's N/A on a task wrongly marked done."""
		form = (
			APP / "sapphire_maintenance/doctype/sapphire_maintenance_record/sapphire_maintenance_record.js"
		).read_text(encoding="utf-8")
		handler = form[form.index('frappe.ui.form.on("Sapphire Cleaning Task"') :]
		self.assertIn('frappe.model.set_value(cdt, cdn, "not_applicable", 0)', handler)
		self.assertIn('frappe.model.set_value(cdt, cdn, "is_done", 0)', handler)

	def test_done_wins_when_a_task_is_ticked_both_ways(self):
		"""The Desk grid shows both boxes; the wizard clears one when the other is set."""
		row = _Dict(task="Heater check", is_done=1, not_applicable=1, notes="")
		doc = _visit(changed=(), cleaning_tasks=[row])
		doc.validate()
		self.assertEqual((row.is_done, row.not_applicable), (1, 0))

	def test_na_and_a_reason_count_towards_completion(self):
		doc = _visit(
			cleaning_tasks=[_Dict(is_done=0, not_applicable=1), _Dict(notes="Pump off"), _Dict(notes="  ")]
		)
		self.assertEqual(record_module.compute_completion_percent(doc), round(2 / 3 * 100, 1))

	def test_a_required_task_is_answered_by_na_or_a_reason_but_not_by_nothing(self):
		for row in (_Dict(not_applicable=1), _Dict(notes="Pump off")):
			with self.subTest(row=row):
				row.update(task="Heater check", is_mandatory=1)
				_visit(cleaning_tasks=[row])._validate_mandatory_rows()
		blank = _Dict(task="Heater check", is_mandatory=1, notes=" ")
		with self.assertRaises(_Thrown):
			_visit(cleaning_tasks=[blank])._validate_mandatory_rows()


if __name__ == "__main__":
	unittest.main()
