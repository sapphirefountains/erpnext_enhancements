"""Bench-free unit tests for semi-monthly pay-period commission reporting.

Stubs a minimal ``frappe`` (no site/bench) so
``crm_enhancements.pay_period_reports`` runs under plain unittest, with a real
date implementation behind ``frappe.utils`` — the date arithmetic *is* the
feature, and mocking it away would test nothing. The stub is installed in
``setUpModule`` (execution time), not at import, so it never fools the
bench-only suites' ``import frappe`` skip-guards.

What is guarded here is everything that produces a *plausible but wrong*
commission statement — the failure mode that is invisible until payroll
disagrees with it:

  * both ends of the window inclusive, so a deal closed **on** a 1st or a 16th
    is in exactly one period. The filter this replaced used ``>`` against the
    period's first day, which dropped those deals out of both periods (a real
    $500 opportunity dated 2026-07-16 was missing from the live report);
  * the period that just *closed* is the one emailed, and the period *running*
    is the one the desk report is left showing — they are different windows on
    every send day, and swapping them is silent;
  * month and year rollover: 1 Jan emails 16–31 Dec, and February's second
    period ends on 28 or 29 as the year dictates;
  * the window is restored even when the send raises, because the send borrows
    it — a stranded window means the desk report lies until someone notices;
  * one statement per period, whatever re-runs the job;
  * non-date filters (the owner, the Closed Won status) survive every tick.

Run: python -m unittest erpnext_enhancements.tests.test_pay_period_reports
"""

import datetime
import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

pay_period_reports = None

#: Everything the stubbed frappe serves, reset by each test.
STATE = {}

REPORT = "Brian's Closed Won"
AUTO_EMAIL_REPORT = "Brian Commissions Report"

#: The report's saved filters as they look in production, minus the date row.
BASE_FILTERS = [
	["Opportunity", "opportunity_owner", "=", "brian.morisseau@sapphirefountains.com"],
	["Opportunity", "status", "=", "Closed Won"],
]


def _report_json(filters):
	return json.dumps({"filters": filters, "fields": [["name", "Opportunity"]], "add_totals_row": True})


def _reset_state(report_filters=None, today="2026-08-16"):
	STATE.clear()
	STATE.update(
		{
			"today": today,
			"reports": {REPORT: {"json": _report_json(report_filters or list(BASE_FILTERS))}},
			"auto_email_reports": {
				AUTO_EMAIL_REPORT: {
					"email_to": "billing@sapphirefountains.com\nbrian.morisseau@sapphirefountains.com",
					# HTML the doc would render; falsy stands in for "no rows and
					# send_if_data is set", which frappe signals the same way.
					"content": "<table>rows</table>",
				}
			},
			"defaults": {},
			"sent": [],
			"set_value_calls": [],
			"errors": [],
			# Windows the report was carrying each time content was rendered.
			# The whole point of the borrow/restore dance.
			"rendered_windows": [],
			# Set to an exception to make rendering blow up.
			"render_raises": None,
		}
	)


# ------------------------------------------------------------------ date utils
# Real implementations: these are what the feature is made of.


def _getdate(value=None):
	if value is None:
		return _getdate(STATE["today"])
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	return datetime.datetime.strptime(str(value), "%Y-%m-%d").date()


def _add_days(value, days):
	return _getdate(value) + datetime.timedelta(days=days)


def _get_last_day(value):
	day = _getdate(value)
	first_of_next = (day.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
	return first_of_next - datetime.timedelta(days=1)


def _formatdate(value, _format=None):
	return _getdate(value).strftime("%m-%d-%Y")


def _today():
	return STATE["today"]


# ----------------------------------------------------------------- frappe stub


class _StubAutoEmailReport:
	def __init__(self, name):
		self.name = name
		self.email_to = STATE["auto_email_reports"][name]["email_to"]

	def get_report_content(self):
		# Record the window the report is carrying right now — this is what the
		# recipients would actually see.
		spec = json.loads(STATE["reports"][REPORT]["json"])
		STATE["rendered_windows"].append(
			next((row for row in spec["filters"] if row[1] == "custom_date_closed_won"), None)
		)
		if STATE["render_raises"]:
			raise STATE["render_raises"]
		return STATE["auto_email_reports"][self.name]["content"]


class _StubDB:
	def exists(self, doctype, name):
		table = {"Report": "reports", "Auto Email Report": "auto_email_reports"}.get(doctype)
		return bool(table and name in STATE[table])

	def get_value(self, doctype, name, field):
		if doctype == "Report":
			return STATE["reports"].get(name, {}).get(field)
		return None

	def set_value(self, doctype, name, field, value, update_modified=True):
		STATE["set_value_calls"].append(
			{"doctype": doctype, "name": name, "field": field, "update_modified": update_modified}
		)
		if doctype == "Report":
			STATE["reports"].setdefault(name, {})[field] = value

	def get_default(self, key):
		return STATE["defaults"].get(key)

	def set_default(self, key, value):
		STATE["defaults"][key] = value


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe.db = _StubDB()

	def get_doc(doctype, name):
		if doctype == "Auto Email Report":
			return _StubAutoEmailReport(name)
		raise AssertionError(f"unexpected get_doc({doctype!r})")

	def sendmail(**kwargs):
		STATE["sent"].append(kwargs)

	frappe.get_doc = get_doc
	frappe.sendmail = sendmail
	frappe.log_error = lambda message, title=None: STATE["errors"].append((title, str(message)[:200]))
	frappe.get_traceback = lambda: "traceback"
	frappe.clear_document_cache = lambda doctype, name: None

	utils = types.ModuleType("frappe.utils")
	utils.getdate = _getdate
	utils.add_days = _add_days
	utils.get_last_day = _get_last_day
	utils.formatdate = _formatdate
	utils.today = _today
	frappe.utils = utils

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils


def setUpModule():
	global pay_period_reports

	_install_frappe_stub()
	_reset_state()

	from erpnext_enhancements.crm_enhancements import pay_period_reports as module

	pay_period_reports = module


# ----------------------------------------------------------------------- tests


class TestPayPeriodBounds(unittest.TestCase):
	"""1st–15th and 16th–end of month, both ends inclusive."""

	def setUp(self):
		_reset_state()

	def _bounds(self, day):
		start, end = pay_period_reports.pay_period_bounds(day)
		return str(start), str(end)

	def test_first_half_from_every_edge(self):
		for day in ("2026-08-01", "2026-08-07", "2026-08-15"):
			with self.subTest(day=day):
				self.assertEqual(self._bounds(day), ("2026-08-01", "2026-08-15"))

	def test_second_half_from_every_edge(self):
		for day in ("2026-08-16", "2026-08-24", "2026-08-31"):
			with self.subTest(day=day):
				self.assertEqual(self._bounds(day), ("2026-08-16", "2026-08-31"))

	def test_second_half_ends_on_the_real_last_day(self):
		# 30-day month, 31-day month, short February, leap February.
		self.assertEqual(self._bounds("2026-09-20"), ("2026-09-16", "2026-09-30"))
		self.assertEqual(self._bounds("2026-12-20"), ("2026-12-16", "2026-12-31"))
		self.assertEqual(self._bounds("2026-02-20"), ("2026-02-16", "2026-02-28"))
		self.assertEqual(self._bounds("2028-02-20"), ("2028-02-16", "2028-02-29"))

	def test_every_day_of_a_year_lands_in_exactly_one_period(self):
		"""No gaps and no overlaps — the property that makes commission add up."""
		day = datetime.date(2026, 1, 1)
		seen = []
		while day.year == 2026:
			start, end = pay_period_reports.pay_period_bounds(day)
			self.assertTrue(start <= day <= end, f"{day} outside its own period")
			if not seen or seen[-1] != (start, end):
				seen.append((start, end))
			day += datetime.timedelta(days=1)

		self.assertEqual(len(seen), 24, "a year is 24 semi-monthly periods")
		for earlier, later in zip(seen, seen[1:]):
			self.assertEqual(
				later[0] - earlier[1],
				datetime.timedelta(days=1),
				f"gap or overlap between {earlier} and {later}",
			)


class TestPreviousPayPeriod(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def _previous(self, day):
		start, end = pay_period_reports.previous_pay_period(day)
		return str(start), str(end)

	def test_the_16th_emails_the_first_half_of_the_same_month(self):
		self.assertEqual(self._previous("2026-08-16"), ("2026-08-01", "2026-08-15"))

	def test_the_1st_emails_the_second_half_of_the_month_before(self):
		self.assertEqual(self._previous("2026-09-01"), ("2026-08-16", "2026-08-31"))

	def test_new_years_day_reaches_back_into_december(self):
		self.assertEqual(self._previous("2027-01-01"), ("2026-12-16", "2026-12-31"))

	def test_march_1st_reaches_back_to_the_right_end_of_february(self):
		self.assertEqual(self._previous("2026-03-01"), ("2026-02-16", "2026-02-28"))
		self.assertEqual(self._previous("2028-03-01"), ("2028-02-16", "2028-02-29"))


class TestApplyWindow(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def _saved_filters(self):
		return json.loads(STATE["reports"][REPORT]["json"])["filters"]

	def _date_row(self):
		return next(row for row in self._saved_filters() if row[1] == "custom_date_closed_won")

	def test_window_is_inclusive_at_both_ends(self):
		"""The regression: `>` against the first day dropped deals closed on a 1st or 16th."""
		pay_period_reports._apply_window("2026-07-16", "2026-07-31")
		self.assertEqual(
			self._date_row(),
			["Opportunity", "custom_date_closed_won", "between", ["2026-07-16", "2026-07-31"]],
		)

	def test_other_filters_are_left_alone(self):
		pay_period_reports._apply_window("2026-07-16", "2026-07-31")
		kept = [row for row in self._saved_filters() if row[1] != "custom_date_closed_won"]
		self.assertEqual(kept, BASE_FILTERS)

	def test_a_stale_date_filter_is_replaced_not_appended(self):
		_reset_state(
			report_filters=[["Opportunity", "custom_date_closed_won", ">", "2026-07-16"], *BASE_FILTERS]
		)
		pay_period_reports._apply_window("2026-08-01", "2026-08-15")
		date_rows = [row for row in self._saved_filters() if row[1] == "custom_date_closed_won"]
		self.assertEqual(len(date_rows), 1, "ANDing two date filters would return nothing")
		self.assertEqual(date_rows[0][2], "between")

	def test_unchanged_window_does_not_write(self):
		self.assertTrue(pay_period_reports._apply_window("2026-08-01", "2026-08-15"))
		STATE["set_value_calls"].clear()
		self.assertFalse(pay_period_reports._apply_window("2026-08-01", "2026-08-15"))
		self.assertEqual(STATE["set_value_calls"], [], "daily no-op tick must not touch the doc")

	def test_write_does_not_bump_modified(self):
		"""Otherwise anyone with the report open gets a TimestampMismatchError."""
		pay_period_reports._apply_window("2026-08-01", "2026-08-15")
		self.assertEqual([call["update_modified"] for call in STATE["set_value_calls"]], [False])

	def test_missing_report_is_a_no_op(self):
		STATE["reports"].clear()
		self.assertFalse(pay_period_reports._apply_window("2026-08-01", "2026-08-15"))

	def test_unparseable_json_is_logged_not_raised(self):
		STATE["reports"][REPORT]["json"] = "{not json"
		self.assertFalse(pay_period_reports._apply_window("2026-08-01", "2026-08-15"))
		self.assertTrue(STATE["errors"])


class TestRunPayPeriodCycle(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def _date_row(self):
		spec = json.loads(STATE["reports"][REPORT]["json"])
		return next(row for row in spec["filters"] if row[1] == "custom_date_closed_won")

	def _window(self):
		return self._date_row()[3]

	def test_the_16th_emails_the_closed_period_and_leaves_the_running_one(self):
		STATE["today"] = "2026-08-16"
		pay_period_reports.run_pay_period_cycle()

		self.assertEqual(len(STATE["sent"]), 1)
		self.assertEqual(
			STATE["rendered_windows"][0][3],
			["2026-08-01", "2026-08-15"],
			"recipients must see the period that just closed",
		)
		self.assertEqual(
			self._window(),
			["2026-08-16", "2026-08-31"],
			"the desk report must be left on the period in progress",
		)

	def test_the_1st_emails_the_previous_months_second_half(self):
		STATE["today"] = "2026-09-01"
		pay_period_reports.run_pay_period_cycle()

		self.assertEqual(STATE["rendered_windows"][0][3], ["2026-08-16", "2026-08-31"])
		self.assertEqual(self._window(), ["2026-09-01", "2026-09-15"])

	def test_ordinary_days_move_nothing_and_send_nothing(self):
		STATE["today"] = "2026-08-20"
		pay_period_reports.run_pay_period_cycle()

		self.assertEqual(STATE["sent"], [])
		self.assertEqual(self._window(), ["2026-08-16", "2026-08-31"])

	def test_an_ordinary_day_repairs_a_window_left_behind_by_a_dead_send(self):
		_reset_state(
			report_filters=[
				["Opportunity", "custom_date_closed_won", "between", ["2026-08-01", "2026-08-15"]],
				*BASE_FILTERS,
			]
		)
		STATE["today"] = "2026-08-20"
		pay_period_reports.run_pay_period_cycle()
		self.assertEqual(self._window(), ["2026-08-16", "2026-08-31"])

	def test_a_failed_send_still_restores_the_running_window(self):
		STATE["today"] = "2026-08-16"
		STATE["render_raises"] = RuntimeError("smtp down")
		pay_period_reports.run_pay_period_cycle()

		self.assertEqual(STATE["sent"], [])
		self.assertTrue(STATE["errors"])
		self.assertEqual(self._window(), ["2026-08-16", "2026-08-31"])

	def test_a_failed_send_is_retried_on_the_next_tick(self):
		STATE["today"] = "2026-08-16"
		STATE["render_raises"] = RuntimeError("smtp down")
		pay_period_reports.run_pay_period_cycle()
		self.assertEqual(STATE["sent"], [])

		STATE["render_raises"] = None
		pay_period_reports.run_pay_period_cycle()
		self.assertEqual(len(STATE["sent"]), 1, "a period that never went out must not be skipped")

	def test_one_statement_per_period_however_often_the_job_runs(self):
		STATE["today"] = "2026-08-16"
		for _ in range(3):
			pay_period_reports.run_pay_period_cycle()
		self.assertEqual(len(STATE["sent"]), 1)

	def test_consecutive_periods_each_get_their_own_statement(self):
		STATE["today"] = "2026-08-16"
		pay_period_reports.run_pay_period_cycle()
		STATE["today"] = "2026-09-01"
		pay_period_reports.run_pay_period_cycle()

		self.assertEqual(len(STATE["sent"]), 2)
		self.assertEqual(
			[window[3] for window in STATE["rendered_windows"]],
			[["2026-08-01", "2026-08-15"], ["2026-08-16", "2026-08-31"]],
		)

	def test_recipients_and_subject(self):
		STATE["today"] = "2026-08-16"
		pay_period_reports.run_pay_period_cycle()

		sent = STATE["sent"][0]
		self.assertEqual(
			sent["recipients"],
			["billing@sapphirefountains.com", "brian.morisseau@sapphirefountains.com"],
		)
		# The period is in the subject so two statements are distinguishable in
		# an inbox — the doc name alone is identical every time.
		self.assertIn("08-01-2026", sent["subject"])
		self.assertIn("08-15-2026", sent["subject"])

	def test_an_empty_period_is_not_marked_as_sent(self):
		"""`send_if_data` suppressing the mail must not burn the period."""
		STATE["today"] = "2026-08-16"
		STATE["auto_email_reports"][AUTO_EMAIL_REPORT]["content"] = ""
		pay_period_reports.run_pay_period_cycle()

		self.assertEqual(STATE["sent"], [])
		self.assertEqual(STATE["defaults"], {})

	def test_no_recipients_is_logged_not_raised(self):
		STATE["today"] = "2026-08-16"
		STATE["auto_email_reports"][AUTO_EMAIL_REPORT]["email_to"] = ""
		pay_period_reports.run_pay_period_cycle()

		self.assertEqual(STATE["sent"], [])
		self.assertTrue(STATE["errors"])
		self.assertEqual(self._window(), ["2026-08-16", "2026-08-31"])

	def test_missing_records_are_a_silent_no_op(self):
		"""A bench without these site records must not error every morning."""
		STATE["today"] = "2026-08-16"
		STATE["auto_email_reports"].clear()
		pay_period_reports.run_pay_period_cycle()
		self.assertEqual(STATE["sent"], [])
		self.assertEqual(STATE["errors"], [])

		STATE["reports"].clear()
		pay_period_reports.run_pay_period_cycle()
		self.assertEqual(STATE["errors"], [])


if __name__ == "__main__":
	unittest.main()
