"""The Maintenance Day Board's feed for another day (v1.662.0), run against real SQL.

``api/maintenance_board.get_day_board_data(date)`` reads a past or future day's board. Two things
it must get right, and both fail silently:

* **A visit is on the day it was done, not the day it was last touched.** Billing writes the Sales
  Invoice link onto a submitted visit (``api/maintenance_billing.py``) and the sign-off writes its
  stamps (``sapphire_maintenance/sign_off.py``); both move ``modified``. A board that dated visits
  by ``modified`` showed a visit done on the 1st and invoiced on the 3rd on the 3rd's board, and
  nowhere on the 1st's. The day is ``visit_date``, and for an older visit with none, the day of its
  ``clock_in_time``.
* **No date the page can be handed raises.** A malformed date was a 417 and ``9999-12-31`` a 500
  (``add_days`` overflows). Anything that is not a YYYY-MM-DD calendar day in 1900..2999 is today.

The other-day queries run for real, against an in-memory SQLite copy of the columns they read:
the stubbed ``frappe.db.sql`` turns ``%(name)s`` into ``:name`` and runs them. Today's queries are
pinned by the arguments they are called with, which are the ones the board has always sent.

Bench-free: a ``frappe`` stub installed in ``setUpModule`` and removed in ``tearDownModule``, so it
gets its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_maintenance_board_days
"""

import datetime
import importlib
import re
import sqlite3
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

TODAY = "2026-10-14"
MODULE = "erpnext_enhancements.api.maintenance_board"

board = None
db = None
calls = []
_saved = {}


class _dict(dict):
	def __getattr__(self, key):
		return self.get(key)


class StubPermissionError(Exception):
	pass


def _schema(conn):
	conn.executescript(
		"""
		CREATE TABLE `tabSapphire Maintenance Record` (
			name TEXT PRIMARY KEY, docstatus INTEGER, project TEXT, serial_no TEXT, visit_label TEXT,
			technician TEXT, completion_percent INTEGER, has_out_of_range_readings INTEGER DEFAULT 0,
			warranty_rma_flag INTEGER DEFAULT 0, visit_date TEXT, clock_in_time TEXT,
			scheduled_visit_date TEXT, modified TEXT, creation TEXT
		);
		CREATE TABLE `tabJob Interval` (
			name TEXT, project TEXT, start_time TEXT, employee TEXT, status TEXT
		);
		CREATE TABLE `tabEmployee` (name TEXT, employee_name TEXT);
		CREATE TABLE `tabSapphire Maintenance Contract` (project TEXT, status TEXT);
		"""
	)


def setUpModule():
	global board, db

	for name in ("frappe", "frappe.utils", MODULE):
		_saved[name] = sys.modules.get(name)

	frappe = types.ModuleType("frappe")
	frappe._ = lambda msg: msg
	frappe.PermissionError = StubPermissionError
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.get_roles = lambda *a: ["Maintenance Supervisor"]

	def _throw(msg, exc=None, title=None):
		raise (exc or Exception)(msg)

	frappe.throw = _throw

	def get_all(doctype, **kwargs):
		calls.append(("get_all", doctype, kwargs))
		return []

	frappe.get_all = get_all

	def sql(query, values=None, as_dict=False):
		calls.append(("sql", query, values))
		text = re.sub(r"%\((\w+)\)s", r":\1", query)
		cursor = db.execute(text, values or {})
		cols = [c[0] for c in cursor.description]
		return [_dict(zip(cols, row, strict=True)) for row in cursor.fetchall()]

	frappe.db = types.SimpleNamespace(sql=sql)

	utils = types.ModuleType("frappe.utils")

	def add_days(date, days):
		return datetime.date.fromisoformat(str(date)[:10]) + datetime.timedelta(days=days)

	utils.add_days = add_days
	utils.nowdate = lambda: TODAY
	frappe.utils = utils

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules.pop(MODULE, None)
	board = importlib.import_module(MODULE)

	db = sqlite3.connect(":memory:")
	_schema(db)


def tearDownModule():
	for name, module in _saved.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module
	if db is not None:
		db.close()


def _visit(name, **fields):
	row = {
		"name": name,
		"docstatus": 1,
		"project": None,
		"visit_date": None,
		"clock_in_time": None,
		"modified": "2026-10-03 16:00:00",
		"creation": "2026-09-30 08:00:00",
	}
	row.update(fields)
	cols = ", ".join(row)
	db.execute(
		f"INSERT INTO `tabSapphire Maintenance Record` ({cols}) VALUES ({', '.join('?' * len(row))})",
		list(row.values()),
	)


def _names(rows):
	return sorted(r["name"] for r in rows)


class TestAVisitIsOnTheDayItWasDone(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		db.execute("DELETE FROM `tabSapphire Maintenance Record`")
		# Done on the 1st, invoiced (so modified) on the 3rd.
		_visit("SMR-DONE-1ST", visit_date="2026-10-01", modified="2026-10-03 16:00:00")
		# The same, flagged for chemistry.
		_visit(
			"SMR-FLAGGED-1ST",
			visit_date="2026-10-01",
			modified="2026-10-03 16:00:00",
			has_out_of_range_readings=1,
		)
		# An older visit with no visit_date: its clock-in dates it.
		_visit("SMR-UNDATED-2ND", clock_in_time="2026-10-02 09:30:00", modified="2026-10-05 10:00:00")
		# Neither date: on no past day's board.
		_visit("SMR-NO-DATE", modified="2026-10-01 10:00:00")
		# A draft is never "submitted".
		_visit("SMR-DRAFT", docstatus=0, visit_date="2026-10-01")
		db.commit()

	def test_the_day_it_was_done(self):
		data = board.get_day_board_data("2026-10-01")
		self.assertEqual(_names(data["submitted_today"]), ["SMR-DONE-1ST", "SMR-FLAGGED-1ST"])

	def test_not_the_day_it_was_last_modified(self):
		data = board.get_day_board_data("2026-10-03")
		self.assertEqual(_names(data["submitted_today"]), [])

	def test_an_undated_visit_falls_back_to_its_clock_in(self):
		self.assertEqual(
			_names(board.get_day_board_data("2026-10-02")["submitted_today"]), ["SMR-UNDATED-2ND"]
		)
		self.assertEqual(_names(board.get_day_board_data("2026-10-05")["submitted_today"]), [])

	def test_flagged_is_the_seven_days_up_to_the_day_by_the_visits_date(self):
		self.assertEqual(_names(board.get_day_board_data("2026-10-07")["flagged"]), ["SMR-FLAGGED-1ST"])
		self.assertEqual(_names(board.get_day_board_data("2026-10-01")["flagged"]), ["SMR-FLAGGED-1ST"])
		# Eight days on, it has left the window, invoiced since or not.
		self.assertEqual(_names(board.get_day_board_data("2026-10-09")["flagged"]), [])
		# And before it was done, it is not there yet.
		self.assertEqual(_names(board.get_day_board_data("2026-09-30")["flagged"]), [])


class TestTodayIsUnchanged(unittest.TestCase):
	def _today_calls(self, date):
		del calls[:]
		board.get_day_board_data(date)
		return [c for c in calls if c[0] == "get_all" and c[1] == "Sapphire Maintenance Record"]

	def test_today_named_or_not_sends_what_it_always_sent(self):
		for date in (None, "", TODAY):
			with self.subTest(date=date):
				got = self._today_calls(date)
				self.assertEqual(got[0][2]["filters"], {"docstatus": 0})
				self.assertEqual(got[1][2]["filters"], {"docstatus": 1, "modified": [">=", TODAY]})
				self.assertEqual(got[1][2]["order_by"], "modified desc")
				self.assertEqual(
					got[2][2]["filters"],
					{"docstatus": 1, "modified": [">=", datetime.date(2026, 10, 7)]},
				)
				live = [c for c in calls if c[0] == "sql"]
				self.assertEqual(len(live), 1)
				self.assertIn("ji.status IN ('Open', 'Paused')", live[0][1])
				self.assertIsNone(live[0][2])


class TestNoDateRaises(unittest.TestCase):
	def test_malformed_or_out_of_range_is_today(self):
		for date in (
			"garbage",
			"2026-02-30",
			"2026-9-1",
			"2026-10-01T00:00:00",
			"0001-01-01",
			"1899-12-31",
			"3000-01-01",
			"9999-12-31",
			"<script>",
			12,
		):
			with self.subTest(date=date):
				self.assertIsNone(board._board_day(date))
				del calls[:]
				board.get_day_board_data(date)  # must not raise
				sent = [c[2].get("filters") for c in calls if c[0] == "get_all"]
				self.assertIn({"docstatus": 1, "modified": [">=", TODAY]}, sent)

	def test_the_range_edges_are_days(self):
		self.assertEqual(board._board_day("1900-01-01"), datetime.date(1900, 1, 1))
		self.assertEqual(board._board_day(" 2999-12-31 "), datetime.date(2999, 12, 31))
		board.get_day_board_data("2999-12-31")  # the day after is still a date

	def test_the_permission_check_still_comes_first(self):
		sys.modules["frappe"].get_roles = lambda *a: ["Employee"]
		try:
			with self.assertRaises(StubPermissionError):
				board.get_day_board_data("2026-10-01")
		finally:
			sys.modules["frappe"].get_roles = lambda *a: ["Maintenance Supervisor"]


if __name__ == "__main__":
	unittest.main()
