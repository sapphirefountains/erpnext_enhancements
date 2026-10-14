# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Historical Opportunities (v1.629.0, WI-024; Nik, 2026-10-13).

An Opportunity created before 2025-08-01 that no person has worked on in 90 days is
*historical*: no won-without-project warning, no KPI. This suite RUNS the rule against an
in-memory frappe stub (cutoff boundary, every activity source, the non-person owners, the
Data Import and non-status Versions, the 90-day window, write-only-what-changed, never
raising), runs the two patches and the transition-only ``stamp_won_date``, and then checks
statically that every call site the audit named actually adopted the exclusion, that the
fixtures carry the field and the filters, and that the job and the patches are wired.

Bench-free, unittest-style: ``python -m unittest erpnext_enhancements.tests.test_historical_opportunities``.
It installs its own ``frappe`` stub in ``setUpModule``, so it has its own CI step.
"""

import ast
import datetime
import io
import json
import re
import sys
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent

NOW = datetime.datetime(2026, 10, 13, 9, 0, 0)

STATE = {}


def _reset(**overrides):
	STATE.clear()
	STATE.update(
		columns={"custom_is_historical", "custom_date_closed_won"},
		tables={
			"Opportunity": [],
			"Comment": [],
			"Communication": [],
			"Communication Link": [],
			"ToDo": [],
			"Version": [],
		},
		writes=[],
		errors=[],
		calls=[],
		raise_on=None,
		user="sales@example.com",
	)
	STATE.update(overrides)


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError:
			raise AttributeError(key) from None


def _matches(row, filters):
	"""The subset of frappe's filter grammar the code under test uses."""
	if isinstance(filters, dict):
		filters = [[k, *(v if isinstance(v, (list, tuple)) else ["=", v])] for k, v in filters.items()]
	for flt in filters or []:
		if len(flt) == 4:
			flt = flt[1:]
		field, op, value = flt
		have = row.get(field)
		if op == "=":
			ok = have == value
		elif op == "!=":
			ok = have != value
		elif op == "in":
			ok = have in value
		elif op == "not in":
			ok = have not in value
		elif op in (">=", "<", "<=", ">"):
			left, right = str(have), str(value)
			ok = {">=": left >= right, "<": left < right, "<=": left <= right, ">": left > right}[op]
		else:
			raise AssertionError(f"stub does not know operator {op!r}")
		if not ok:
			return False
	return True


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe._ = lambda s: s
	frappe.flags = types.SimpleNamespace(in_import=False, in_patch=False, in_migrate=False)
	frappe.session = types.SimpleNamespace(user="sales@example.com")
	frappe.get_traceback = lambda *a, **k: "traceback"
	frappe.logger = lambda *a, **k: types.SimpleNamespace(info=lambda *x, **y: None)

	def log_error(*args, **kwargs):
		STATE["errors"].append((args, kwargs))

	frappe.log_error = log_error

	def get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
		STATE["calls"].append((doctype, kwargs))
		if STATE.get("raise_on") == doctype:
			raise RuntimeError("boom")
		rows = [r for r in STATE["tables"].get(doctype, []) if _matches(r, filters)]
		if pluck:
			return [r.get(pluck) for r in rows]
		return [_Dict(r) for r in rows]

	frappe.get_all = get_all
	frappe.get_list = get_all

	db = types.SimpleNamespace()

	def has_column(doctype, column):
		return column in STATE["columns"]

	def set_value(doctype, name, field, value=None, update_modified=True, **kwargs):
		if STATE.get("raise_on") == "set_value":
			raise RuntimeError("write refused")
		STATE["writes"].append((doctype, name, field, value, update_modified))
		names = name["name"][1] if isinstance(name, dict) else [name]
		for row in STATE["tables"].get(doctype, []):
			if row["name"] in names:
				row[field] = value

	def get_value(doctype, name, field):
		for row in STATE["tables"].get(doctype, []):
			if row["name"] == name:
				return row.get(field)
		return None

	db.has_column = has_column
	db.set_value = set_value
	db.get_value = get_value
	db.exists = lambda *a, **k: False
	frappe.db = db

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	utils.now_datetime = lambda: NOW
	utils.add_days = lambda d, n: d + datetime.timedelta(days=n)
	utils.today = lambda: NOW.date().isoformat()
	frappe.utils = utils

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils


def setUpModule():
	global historical, opportunity_scripts, restore_patch, set_patch
	_install_frappe_stub()
	for module in list(sys.modules):
		if module.startswith("erpnext_enhancements"):
			sys.modules.pop(module, None)
	from erpnext_enhancements.crm_enhancements import historical as h
	from erpnext_enhancements.patches import restore_opportunity_won_dates as r
	from erpnext_enhancements.patches import set_historical_opportunities as s
	from erpnext_enhancements.script_migrations import opportunity as o

	historical, opportunity_scripts, restore_patch, set_patch = h, o, r, s


def _days_ago(n):
	return str(NOW - datetime.timedelta(days=n))


def _opp(name, created, flag=0):
	row = {"name": name, "creation": created, "custom_is_historical": flag}
	STATE["tables"]["Opportunity"].append(row)
	return row


def _flag(name):
	return next(r for r in STATE["tables"]["Opportunity"] if r["name"] == name)["custom_is_historical"]


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


class TestTheCutoff(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_the_day_before_the_cutoff_is_historical_and_the_cutoff_day_is_not(self):
		_opp("OLD", "2025-07-31 23:59:59")
		_opp("NEW", "2025-08-01 00:00:00")
		counts = historical.refresh_historical_flags()
		self.assertEqual(_flag("OLD"), 1)
		self.assertEqual(_flag("NEW"), 0)
		self.assertEqual(counts["flagged"], 1)

	def test_a_new_deal_flagged_by_hand_is_cleared(self):
		_opp("NEW", "2026-01-05 10:00:00", flag=1)
		counts = historical.refresh_historical_flags()
		self.assertEqual(_flag("NEW"), 0)
		self.assertEqual(counts["unflagged"], 1)

	def test_the_zoho_import_day_is_inside_the_rule(self):
		"""Every Zoho deal was imported 2025-07-14, whenever it was won."""
		_opp("ZOHO", "2025-07-14 12:00:00")
		historical.refresh_historical_flags()
		self.assertEqual(_flag("ZOHO"), 1)

	def test_constants_are_the_decided_ones(self):
		self.assertEqual(historical.HISTORICAL_FIELD, "custom_is_historical")
		self.assertEqual(historical.CUTOFF, "2025-08-01")
		self.assertEqual(historical.ACTIVITY_DAYS, 90)


class TestWhatCountsAsActivity(unittest.TestCase):
	def setUp(self):
		_reset()
		_opp("OPP-1", "2025-07-14 12:00:00")

	def _active(self):
		since = NOW - datetime.timedelta(days=90)
		return historical.active_recently(["OPP-1"], since)

	def _comment(self, comment_type="Comment", owner="sales@example.com", age=5):
		STATE["tables"]["Comment"].append(
			{
				"reference_doctype": "Opportunity",
				"reference_name": "OPP-1",
				"comment_type": comment_type,
				"owner": owner,
				"creation": _days_ago(age),
			}
		)

	def _version(self, data, owner="sales@example.com", age=5):
		STATE["tables"]["Version"].append(
			{
				"ref_doctype": "Opportunity",
				"docname": "OPP-1",
				"owner": owner,
				"creation": _days_ago(age),
				"data": json.dumps(data),
			}
		)

	def test_nothing_at_all_is_not_activity(self):
		self.assertEqual(self._active(), set())

	def test_a_comment_counts(self):
		self._comment("Comment")
		self.assertEqual(self._active(), {"OPP-1"})

	def test_an_attachment_counts(self):
		self._comment("Attachment")
		self.assertEqual(self._active(), {"OPP-1"})

	def test_info_like_and_edit_comments_do_not(self):
		"""Written by code as a side effect, or a reaction -- not somebody working the deal."""
		for kind in ("Info", "Like", "Edit", "Assigned"):
			self._comment(kind)
		self.assertEqual(self._active(), set())

	def test_a_communication_counts(self):
		STATE["tables"]["Communication"].append(
			{
				"reference_doctype": "Opportunity",
				"reference_name": "OPP-1",
				"owner": "sales@example.com",
				"creation": _days_ago(10),
			}
		)
		self.assertEqual(self._active(), {"OPP-1"})

	def test_a_communication_logged_as_administrator_counts(self):
		"""Lead, 2026-10-13: an email about a deal is somebody working it, whoever the
		system logged it as. 4 of prod's 392 Opportunity Communications are Administrator's."""
		STATE["tables"]["Communication"].append(
			{
				"reference_doctype": "Opportunity",
				"reference_name": "OPP-1",
				"owner": "Administrator",
				"creation": _days_ago(10),
			}
		)
		self.assertEqual(self._active(), {"OPP-1"})

	def _linked_email(self, age, owner="Administrator"):
		STATE["tables"]["Communication"].append(
			{
				"name": "COMM-9",
				"reference_doctype": "Contact",
				"reference_name": "C-1",
				"owner": owner,
				"creation": _days_ago(age),
			}
		)
		STATE["tables"]["Communication Link"].append(
			{
				"parent": "COMM-9",
				"parenttype": "Communication",
				"link_doctype": "Opportunity",
				"link_name": "OPP-1",
			}
		)

	def test_a_communication_attached_through_a_timeline_link_counts(self):
		self._linked_email(age=5)
		self.assertEqual(self._active(), {"OPP-1"})

	def test_a_timeline_link_to_an_old_email_does_not(self):
		"""The window is the email's own date, not the link row's."""
		self._linked_email(age=91)
		self.assertEqual(self._active(), set())

	def test_a_timeline_link_to_another_doctype_does_not(self):
		self._linked_email(age=5)
		STATE["tables"]["Communication Link"][0]["link_doctype"] = "Customer"
		self.assertEqual(self._active(), set())

	def test_an_assignment_counts(self):
		STATE["tables"]["ToDo"].append(
			{
				"reference_type": "Opportunity",
				"reference_name": "OPP-1",
				"owner": "sales@example.com",
				"creation": _days_ago(10),
			}
		)
		self.assertEqual(self._active(), {"OPP-1"})

	def test_a_status_change_by_a_person_counts(self):
		self._version({"changed": [["status", "Open", "Closed Won"]], "data_import": None})
		self.assertEqual(self._active(), {"OPP-1"})

	def test_a_version_from_a_data_import_does_not(self):
		self._version({"changed": [["status", "Open", "Closed Won"]], "data_import": "DI-0001"})
		self.assertEqual(self._active(), set())

	def test_a_version_that_changed_something_else_does_not(self):
		self._version({"changed": [["opportunity_amount", 0, 5000]], "data_import": None})
		self.assertEqual(self._active(), set())

	def test_a_malformed_version_is_not_activity_and_does_not_raise(self):
		STATE["tables"]["Version"].append(
			{
				"ref_doctype": "Opportunity",
				"docname": "OPP-1",
				"owner": "a@b.c",
				"creation": _days_ago(1),
				"data": "{nope",
			}
		)
		self.assertEqual(self._active(), set())

	def test_administrator_and_guest_are_not_people(self):
		"""For Comment, ToDo and Version. (Communications count whoever owns them.)"""
		self._comment("Comment", owner="Administrator")
		self._comment("Comment", owner="Guest")
		self._version({"changed": [["status", "Open", "Lost"]], "data_import": None}, owner="Administrator")
		STATE["tables"]["ToDo"].append(
			{
				"reference_type": "Opportunity",
				"reference_name": "OPP-1",
				"owner": "Administrator",
				"creation": _days_ago(1),
			}
		)
		self.assertEqual(self._active(), set())

	def test_a_91_day_old_comment_is_outside_the_window(self):
		self._comment("Comment", age=91)
		self.assertEqual(self._active(), set())

	def test_an_89_day_old_comment_is_inside_it(self):
		self._comment("Comment", age=89)
		self.assertEqual(self._active(), {"OPP-1"})

	def test_activity_on_another_deal_does_not_leak(self):
		STATE["tables"]["Comment"].append(
			{
				"reference_doctype": "Opportunity",
				"reference_name": "OPP-2",
				"comment_type": "Comment",
				"owner": "sales@example.com",
				"creation": _days_ago(1),
			}
		)
		self.assertEqual(self._active(), set())

	def test_queries_are_batched_not_per_deal(self):
		"""~850 deals: one query per source per chunk, never one per opportunity."""
		STATE["tables"]["Opportunity"].clear()
		for i in range(1200):
			_opp(f"OPP-{i}", "2025-07-14 12:00:00")
		historical.refresh_historical_flags()
		per_doctype = {}
		for doctype, _ in STATE["calls"]:
			per_doctype[doctype] = per_doctype.get(doctype, 0) + 1
		# 1200 names in chunks of 500 -> 3 chunks.
		for source in ("Comment", "Communication Link", "ToDo", "Version"):
			self.assertLessEqual(per_doctype.get(source, 0), 3, source)
		# Two per chunk at most: the reference query, and the dates of linked emails.
		self.assertLessEqual(per_doctype.get("Communication", 0), 6)

	def test_every_query_asks_for_every_row(self):
		_opp("OPP-2", "2025-07-14 12:00:00")
		historical.refresh_historical_flags()
		for doctype, kwargs in STATE["calls"]:
			self.assertEqual(kwargs.get("limit_page_length"), 0, doctype)


class TestTheRefresh(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_recent_activity_unflags_and_quiet_flags(self):
		_opp("BUSY", "2025-07-14 12:00:00", flag=1)
		_opp("QUIET", "2025-07-14 12:00:00", flag=0)
		STATE["tables"]["Comment"].append(
			{
				"reference_doctype": "Opportunity",
				"reference_name": "BUSY",
				"comment_type": "Comment",
				"owner": "sales@example.com",
				"creation": _days_ago(3),
			}
		)
		counts = historical.refresh_historical_flags()
		self.assertEqual((_flag("BUSY"), _flag("QUIET")), (0, 1))
		self.assertEqual((counts["flagged"], counts["unflagged"], counts["unchanged"]), (1, 1, 0))

	def test_only_changed_rows_are_written_and_never_with_modified(self):
		_opp("A", "2025-07-14 12:00:00", flag=1)  # stays flagged
		_opp("B", "2025-07-14 12:00:00", flag=0)  # becomes flagged
		counts = historical.refresh_historical_flags()
		self.assertEqual(counts["unchanged"], 1)
		written = [name for _, flt, _, _, _ in STATE["writes"] for name in flt["name"][1]]
		self.assertEqual(written, ["B"])
		self.assertTrue(all(update_modified is False for *_, update_modified in STATE["writes"]))

	def test_a_second_run_writes_nothing(self):
		_opp("A", "2025-07-14 12:00:00")
		historical.refresh_historical_flags()
		STATE["writes"].clear()
		counts = historical.refresh_historical_flags()
		self.assertEqual(STATE["writes"], [])
		self.assertEqual(counts["unchanged"], 1)

	def test_it_never_raises_and_logs_with_keyword_arguments(self):
		_opp("A", "2025-07-14 12:00:00")
		STATE["raise_on"] = "Comment"
		counts = historical.refresh_historical_flags()
		self.assertTrue(counts.get("error"))
		self.assertEqual(len(STATE["errors"]), 1)
		args, kwargs = STATE["errors"][0]
		self.assertEqual(args, ())
		self.assertIn("title", kwargs)
		self.assertIn("message", kwargs)

	def test_without_the_column_it_does_nothing(self):
		STATE["columns"] = set()
		_opp("A", "2025-07-14 12:00:00")
		counts = historical.refresh_historical_flags()
		self.assertIn("skipped", counts)
		self.assertEqual(STATE["writes"], [])
		self.assertEqual(STATE["calls"], [])


# ---------------------------------------------------------------------------
# The helpers the call sites use
# ---------------------------------------------------------------------------


class TestHelpers(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_exclude_sql_with_the_column(self):
		self.assertEqual(historical.exclude_sql("o"), "coalesce(o.custom_is_historical, 0) = 0")
		self.assertEqual(historical.exclude_sql(None), "coalesce(custom_is_historical, 0) = 0")

	def test_exclude_sql_is_a_safe_predicate_without_the_column(self):
		"""A fresh install or ERPNext's own test bootstrap has no column; the query must run."""
		STATE["columns"] = set()
		self.assertEqual(historical.exclude_sql("o"), "1=1")
		self.assertEqual(historical.exclude_filters(), [])
		self.assertEqual(historical.exclude_filter_dict(), {})

	def test_has_column_raising_reads_as_no_column(self):
		sys.modules["frappe"].db.has_column = lambda *a: (_ for _ in ()).throw(RuntimeError("no table"))
		try:
			self.assertEqual(historical.exclude_sql("o"), "1=1")
		finally:
			sys.modules["frappe"].db.has_column = lambda d, c: c in STATE["columns"]

	def test_exclude_sql_carries_no_percent_sign(self):
		"""It is interpolated into queries that also carry bound %(x)s parameters."""
		self.assertNotIn("%", historical.exclude_sql("o"))

	def test_filter_shapes(self):
		self.assertEqual(historical.exclude_filters(), [["Opportunity", "custom_is_historical", "!=", 1]])
		self.assertEqual(historical.exclude_filter_dict(), {"custom_is_historical": ["!=", 1]})
		rows = [{"name": "A", "custom_is_historical": 1}, {"name": "B", "custom_is_historical": 0}]
		kept = [r["name"] for r in rows if _matches(r, historical.exclude_filters())]
		self.assertEqual(kept, ["B"])

	def test_is_historical(self):
		_opp("A", "2025-07-14", flag=1)
		_opp("B", "2025-07-14", flag=0)
		self.assertTrue(historical.is_historical("A"))
		self.assertFalse(historical.is_historical("B"))
		self.assertTrue(historical.is_historical({"custom_is_historical": 1}))
		self.assertFalse(historical.is_historical(None))
		STATE["columns"] = set()
		self.assertFalse(historical.is_historical("A"))


class _Doc(dict):
	"""Enough of a Document for the before_save hooks."""

	def __init__(self, new=False, before_status=None, **fields):
		super().__init__(**fields)
		self._new = new
		self._before_status = before_status

	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		if key.startswith("_"):
			super().__setattr__(key, value)
		else:
			self[key] = value

	def set(self, key, value):
		self[key] = value

	def is_new(self):
		return self._new

	def has_value_changed(self, field):
		# Mirrors v16: True for a new document (no doc_before_save).
		if self._new:
			return True
		return self._before_status != self.get(field)


class TestClearOnPersonStatusChange(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe = sys.modules["frappe"]
		frappe.session.user = "sales@example.com"
		frappe.flags.in_import = frappe.flags.in_patch = frappe.flags.in_migrate = False

	def tearDown(self):
		frappe = sys.modules["frappe"]
		frappe.session.user = "sales@example.com"
		frappe.flags.in_import = frappe.flags.in_patch = frappe.flags.in_migrate = False

	def test_a_person_changing_status_revives_the_deal(self):
		doc = _Doc(status="Closed Won", before_status="Closed", custom_is_historical=1)
		historical.clear_on_person_status_change(doc)
		self.assertEqual(doc["custom_is_historical"], 0)

	def test_any_other_edit_leaves_it(self):
		doc = _Doc(status="Closed Won", before_status="Closed Won", custom_is_historical=1)
		historical.clear_on_person_status_change(doc)
		self.assertEqual(doc["custom_is_historical"], 1)

	def test_administrator_import_and_patch_do_not_count(self):
		frappe = sys.modules["frappe"]
		frappe.session.user = "Administrator"
		doc = _Doc(status="Lost", before_status="Open", custom_is_historical=1)
		historical.clear_on_person_status_change(doc)
		self.assertEqual(doc["custom_is_historical"], 1)
		frappe.session.user = "sales@example.com"
		for flag in ("in_import", "in_patch", "in_migrate"):
			setattr(frappe.flags, flag, True)
			doc = _Doc(status="Lost", before_status="Open", custom_is_historical=1)
			historical.clear_on_person_status_change(doc)
			self.assertEqual(doc["custom_is_historical"], 1, flag)
			setattr(frappe.flags, flag, False)

	def test_a_doc_without_the_field_is_left_alone(self):
		doc = _Doc(status="Lost", before_status="Open")
		historical.clear_on_person_status_change(doc)
		self.assertNotIn("custom_is_historical", doc)


# ---------------------------------------------------------------------------
# stamp_won_date: transition-only
# ---------------------------------------------------------------------------


class TestStampWonDate(unittest.TestCase):
	TODAY = NOW.date().isoformat()

	def test_a_new_deal_saved_as_won_is_stamped(self):
		doc = _Doc(new=True, status="Closed Won", custom_date_closed_won=None)
		opportunity_scripts.stamp_won_date(doc)
		self.assertEqual(doc["custom_date_closed_won"], self.TODAY)

	def test_a_status_change_into_won_is_stamped(self):
		doc = _Doc(status="Closed Won", before_status="Negotiation/Review", custom_date_closed_won=None)
		opportunity_scripts.stamp_won_date(doc)
		self.assertEqual(doc["custom_date_closed_won"], self.TODAY)

	def test_resaving_an_already_won_deal_with_no_date_does_not_stamp(self):
		"""The 2026-06-30 re-import: twelve long-won deals re-saved and dated that day."""
		doc = _Doc(status="Closed Won", before_status="Closed Won", custom_date_closed_won=None)
		opportunity_scripts.stamp_won_date(doc)
		self.assertIsNone(doc["custom_date_closed_won"])

	def test_an_entered_date_is_never_overwritten(self):
		doc = _Doc(status="Closed Won", before_status="Open", custom_date_closed_won="2026-01-02")
		opportunity_scripts.stamp_won_date(doc)
		self.assertEqual(doc["custom_date_closed_won"], "2026-01-02")
		doc = _Doc(new=True, status="Closed Won", custom_date_closed_won="2025-03-19")
		opportunity_scripts.stamp_won_date(doc)
		self.assertEqual(doc["custom_date_closed_won"], "2025-03-19")

	def test_other_statuses_are_untouched(self):
		doc = _Doc(status="Lost", before_status="Open", custom_date_closed_won=None)
		opportunity_scripts.stamp_won_date(doc)
		self.assertIsNone(doc["custom_date_closed_won"])


# ---------------------------------------------------------------------------
# The patches
# ---------------------------------------------------------------------------

EXPECTED_RESTORE = {
	"CRM-OPP-2025-00183": "2025-09-26",
	"CRM-OPP-2025-00403": "2025-10-10",
	"CRM-OPP-2025-00505": "2025-03-19",
	"CRM-OPP-2025-00559": "2025-10-10",
	"CRM-OPP-2025-00593": "2025-09-24",
	"CRM-OPP-2025-00604": "2025-08-27",
	"CRM-OPP-2025-00625": "2025-09-24",
	"CRM-OPP-2025-00654": "2025-11-21",
	"CRM-OPP-2025-00662": "2025-12-02",
	"CRM-OPP-2026-00001": "2026-02-27",
	"CRM-OPP-2026-00083": "2026-04-27",
	"CRM-OPP-2026-00123": None,
}


class TestRestoreWonDates(unittest.TestCase):
	def setUp(self):
		_reset()
		for name in EXPECTED_RESTORE:
			STATE["tables"]["Opportunity"].append(
				{"name": name, "custom_date_closed_won": datetime.date(2026, 6, 30)}
			)

	def _run(self):
		with redirect_stdout(io.StringIO()):
			return restore_patch.execute()

	def _date(self, name):
		return next(r for r in STATE["tables"]["Opportunity"] if r["name"] == name)["custom_date_closed_won"]

	def test_the_mapping_is_exactly_the_decided_one(self):
		self.assertEqual(restore_patch.RESTORE, EXPECTED_RESTORE)

	def test_it_restores_eleven_and_leaves_the_real_one(self):
		tally = self._run()
		self.assertEqual(tally, {"done": 11, "skipped": 1, "failed": 0})
		for name, won_on in EXPECTED_RESTORE.items():
			expected = won_on or datetime.date(2026, 6, 30)
			self.assertEqual(str(self._date(name)), str(expected), name)
		self.assertTrue(all(update_modified is False for *_, update_modified in STATE["writes"]))

	def test_a_hand_corrected_date_is_never_overwritten(self):
		row = next(r for r in STATE["tables"]["Opportunity"] if r["name"] == "CRM-OPP-2025-00183")
		row["custom_date_closed_won"] = datetime.date(2025, 9, 1)
		tally = self._run()
		self.assertEqual(self._date("CRM-OPP-2025-00183"), datetime.date(2025, 9, 1))
		self.assertEqual(tally["skipped"], 2)

	def test_a_missing_deal_is_skipped(self):
		STATE["tables"]["Opportunity"] = [
			r for r in STATE["tables"]["Opportunity"] if r["name"] != "CRM-OPP-2026-00001"
		]
		self.assertEqual(self._run()["skipped"], 2)

	def test_failures_are_counted_not_raised_and_the_tally_sums(self):
		STATE["raise_on"] = "set_value"
		tally = self._run()
		self.assertEqual(tally["failed"], 11)
		self.assertEqual(sum(tally.values()), len(EXPECTED_RESTORE))
		self.assertTrue(all("title" in kwargs for _, kwargs in STATE["errors"]))

	def test_a_second_run_changes_nothing(self):
		self._run()
		STATE["writes"].clear()
		tally = self._run()
		self.assertEqual(STATE["writes"], [])
		self.assertEqual(tally["skipped"], 12)

	def test_without_the_column_it_skips_everything(self):
		STATE["columns"] = set()
		tally = self._run()
		self.assertEqual(tally, {"done": 0, "skipped": 12, "failed": 0})


class TestSetHistoricalPatch(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_it_runs_the_refresh_once_the_column_exists(self):
		_opp("OLD", "2025-07-14 12:00:00")
		with redirect_stdout(io.StringIO()) as out:
			set_patch.execute()
		self.assertEqual(_flag("OLD"), 1)
		self.assertIn("1 flagged", out.getvalue())

	def test_it_cannot_raise_when_the_field_cannot_be_made(self):
		STATE["columns"] = set()
		frappe = sys.modules["frappe"]
		frappe.get_app_path = lambda *a: (_ for _ in ()).throw(RuntimeError("no app path"))
		try:
			with redirect_stdout(io.StringIO()):
				set_patch.execute()
		finally:
			del frappe.get_app_path
		self.assertEqual(len(STATE["errors"]), 1)
		self.assertIn("title", STATE["errors"][0][1])


# ---------------------------------------------------------------------------
# Static: wiring, fixtures, call sites
# ---------------------------------------------------------------------------


def _read(rel):
	return (APP / rel).read_text(encoding="utf-8")


def _js_code(rel):
	"""Comments stripped (as test_desk_page_history._code): an explanation must not satisfy it."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", _read(rel))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def _py_tokens(rel):
	"""Every name, attribute and string constant in a Python file, docstrings excluded."""
	tree = ast.parse(_read(rel))
	docstrings = set()
	for node in ast.walk(tree):
		if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
			body = getattr(node, "body", [])
			if (
				body
				and isinstance(body[0], ast.Expr)
				and isinstance(getattr(body[0], "value", None), ast.Constant)
			):
				docstrings.add(id(body[0].value))
	tokens = set()
	for node in ast.walk(tree):
		if isinstance(node, ast.Name):
			tokens.add(node.id)
		elif isinstance(node, ast.Attribute):
			tokens.add(node.attr)
		elif isinstance(node, ast.alias):
			tokens.add(node.name)
		elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
			tokens.add(node.value)
	return tokens


#: Every Python call site the 2026-10-13 audit named, and the helper (or field) it must use.
PY_CALL_SITES = {
	"crm_enhancements/reconciliation.py": "exclude_sql",
	"status_alerts.py": "exclude_filters",
	"api/sales_dashboard.py": "exclude_filter_dict",
	"kpi_dashboards/snapshots.py": "exclude_sql",
	"assistant_tools/closed_won_handoff_status.py": "exclude_filter_dict",
	"crm_enhancements/page/sales_pipeline/sales_pipeline.py": "HISTORICAL_FIELD",
	"crm_enhancements/handoff.py": "is_historical",
	"crm_enhancements/report/hand_off_process_coverage/hand_off_process_coverage.py": "exclude_filter_dict",
	"api/marketing_dashboard.py": "exclude_filter_dict",
	"kpi_dashboards/report/value_stream_performance/value_stream_performance.py": "exclude_sql",
	"api/briefing.py": "exclude_filter_dict",
}

JS_CALL_SITES = {
	"public/js/crm_enhancements/opportunity.js": "custom_is_historical",
	"public/js/crm_enhancements/create_project_prompt.js": "is_historical",
	"public/js/crm_enhancements/opportunity_handoff.js": "is_historical",
	"crm_enhancements/report/closed_won_reconciliation/closed_won_reconciliation.js": "include_historical",
	"crm_enhancements/report/hand_off_process_coverage/hand_off_process_coverage.js": "include_historical",
}


class TestCallSitesAdopted(unittest.TestCase):
	def test_every_python_call_site_uses_the_helper(self):
		for rel, helper in PY_CALL_SITES.items():
			with self.subTest(file=rel):
				self.assertIn(helper, _py_tokens(rel))

	def test_every_js_call_site_reads_the_flag(self):
		for rel, token in JS_CALL_SITES.items():
			with self.subTest(file=rel):
				self.assertIn(token, _js_code(rel))

	def test_every_closed_won_kpi_query_carries_the_exclusion(self):
		"""Every snapshot query counting Closed Won must end in the exclusion predicate."""
		src = _read("kpi_dashboards/snapshots.py")
		start = src.index("def _sales_metrics")
		body = src[start : src.index("\ndef ", start + 10)]
		won_queries = re.findall(r"where status='Closed Won'[^\n]*(?:\n[^\n]*){0,1}", body)
		# won_90, won_30, won_value_30 (avg_deal_size divides those two) and handoff_backlog.
		self.assertGreaterEqual(len(won_queries), 4)
		for query in won_queries:
			self.assertRegex(query, r"not_historical|exclude_sql", query)
		lost = re.search(r"where status = 'Lost'[^\n]*", body).group(0)
		self.assertIn("not_historical", lost)
		marketing = src[src.index('"marketing_won_30"') :][:600]
		self.assertIn("exclude_sql", marketing)

	def test_the_open_pipeline_kpis_carry_the_exclusion(self):
		"""Lead, 2026-10-13: open_pipeline_value, open_opportunities, stalled_opportunities."""
		src = _read("kpi_dashboards/snapshots.py")
		start = src.index("def _sales_metrics")
		body = src[start : src.index("\ndef ", start + 10)]
		for key in ("open_pipeline_value", "open_opportunities", "stalled_opportunities"):
			with self.subTest(kpi=key):
				block = body[body.index(f'"{key}"') :][:400]
				self.assertIn("{open_filter}", block)
				self.assertIn("{not_historical}", block)

	def test_the_reconciliation_reports_default_the_checkbox_off(self):
		for rel in JS_CALL_SITES:
			if "report/" not in rel:
				continue
			code = _js_code(rel)
			block = code[code.index('fieldname: "include_historical"') :][:200]
			self.assertIn("default: 0", block, rel)

	def test_the_ai_tool_reports_how_many_it_left_out(self):
		self.assertIn("historical_excluded", _py_tokens("assistant_tools/closed_won_handoff_status.py"))

	def test_the_handoff_tab_never_marks_a_historical_deal_ready(self):
		code = _js_code("public/js/crm_enhancements/opportunity_handoff.js")
		self.assertIn("!quiet", code)
		self.assertIn("Historical (pre-ERPNext)", code)


class TestWiring(unittest.TestCase):
	def test_the_daily_job_and_the_before_save_hook_are_registered(self):
		hooks = _read("hooks.py")
		daily = hooks[hooks.index('"daily": [') :]
		daily = daily[: daily.index("],")]
		self.assertIn('"erpnext_enhancements.crm_enhancements.historical.refresh_historical_flags"', daily)
		opp = hooks[hooks.index('\t"Opportunity": {') :]
		before_save = opp[opp.index('"before_save": [') :]
		before_save = before_save[: before_save.index("],")]
		self.assertIn(
			"erpnext_enhancements.crm_enhancements.historical.clear_on_person_status_change", before_save
		)
		self.assertIn("erpnext_enhancements.script_migrations.opportunity.stamp_won_date", before_save)

	def test_both_patches_are_registered_post_model_sync(self):
		lines = [line.strip() for line in _read("patches.txt").splitlines()]
		post = lines[lines.index("[post_model_sync]") :]
		for patch in ("restore_opportunity_won_dates", "set_historical_opportunities"):
			self.assertIn(f"erpnext_enhancements.patches.{patch}", post)

	def test_ci_runs_this_suite(self):
		ci = (REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
		self.assertIn("erpnext_enhancements.tests.test_historical_opportunities", ci)


class TestFixtures(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.fields = {r["name"]: r for r in json.loads(_read("fixtures/custom_field.json"))}
		cls.setters = {r["name"]: r for r in json.loads(_read("fixtures/property_setter.json"))}
		cls.cards = {r["name"]: r for r in json.loads(_read("fixtures/number_card.json"))}
		cls.charts = {r["name"]: r for r in json.loads(_read("fixtures/dashboard_chart.json"))}

	def test_the_field_is_a_stored_read_only_filterable_check(self):
		field = self.fields["Opportunity-custom_is_historical"]
		self.assertEqual(field["fieldtype"], "Check")
		self.assertEqual(field["fieldname"], "custom_is_historical")
		self.assertEqual(field["label"], "Historical (pre-ERPNext)")
		self.assertEqual(field["read_only"], 1)
		self.assertEqual(field["in_standard_filter"], 1)
		self.assertEqual(field["hidden"], 0)
		self.assertEqual(field["is_virtual"], 0)
		self.assertEqual(field["is_system_generated"], 0)
		self.assertIn(
			field["insert_after"], json.loads(self.setters["Opportunity-main-field_order"]["value"])
		)

	def test_the_field_order_names_it_right_after_its_anchor(self):
		order = json.loads(self.setters["Opportunity-main-field_order"]["value"])
		at = order.index("custom_is_historical")
		self.assertEqual(order[at - 1], self.fields["Opportunity-custom_is_historical"]["insert_after"])
		self.assertEqual(order.count("custom_is_historical"), 1)

	def test_the_number_card_and_charts_filter_it_out(self):
		records = [
			self.cards["Closed-Won Opportunities"],
			self.charts["Opportunities by Status"],
			self.charts["Opportunity Value by Status"],
		]
		for record in records:
			with self.subTest(record=record["name"]):
				filters = json.loads(record["filters_json"])
				self.assertIn(["Opportunity", "custom_is_historical", "=", 0, False], filters)
		card = json.loads(self.cards["Closed-Won Opportunities"]["filters_json"])
		self.assertIn(["Opportunity", "status", "=", "Closed Won", False], card)


if __name__ == "__main__":
	unittest.main()
