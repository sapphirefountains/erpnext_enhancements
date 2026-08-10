"""Pure-Python (no Frappe site) unit tests for the Project print-format data.

Same bench-free pattern as ``test_gantt_api.py``: a minimal fake ``frappe`` /
``frappe.utils`` goes into ``sys.modules`` before importing
``erpnext_enhancements.project_enhancements.print_data``.

**Run me with pytest, not unittest.** These are plain functions;
``python -m unittest`` collects nothing from this file and reports success. See
CLAUDE.md — a bench-free pytest suite needs its own step in ci.yml.

What is worth testing here is the geometry. The Project Schedule Print Format
draws Gantt bars as percentage-positioned divs, and a wrong percentage does not
raise — it silently puts the bar in the wrong place on a document that goes to
a customer. So: bars stay inside 0-100%, a single-day task is still visible,
undated tasks survive as rows without a bar, and the exclusive/inclusive date
convention does not creep.
"""

import sys
import types
from datetime import date

import pytest


def install_frappe_stub():
	frappe = sys.modules.get("frappe") or types.ModuleType("frappe")

	class PermissionError_(Exception):
		pass

	frappe.PermissionError = PermissionError_
	frappe._ = lambda message=None, *a, **k: message
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.has_permission = lambda *a, **k: True

	def _throw(message=None, exc=None, **kwargs):
		raise (exc or Exception)(message if isinstance(message, str) else "frappe.throw")

	frappe.throw = _throw

	frappe_utils = sys.modules.get("frappe.utils") or types.ModuleType("frappe.utils")

	def getdate(value=None):
		if value is None:
			return date(2026, 3, 1)  # fixed "today" so overdue tests are stable
		if isinstance(value, date):
			return value
		return date.fromisoformat(str(value)[:10])

	def _flt(value=0, precision=None):
		try:
			number = float(value or 0)
		except (TypeError, ValueError):
			return 0.0
		return round(number, precision) if precision is not None else number

	frappe_utils.getdate = getdate
	frappe_utils.flt = _flt
	frappe_utils.cint = lambda value=0, *a, **k: int(_flt(value))
	frappe.utils = frappe_utils

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = frappe_utils
	return frappe


def import_print_data(tree):
	"""(Re)import print_data with ``_flatten_task_tree`` stubbed to ``tree``."""
	import importlib

	# print_data imports the flattener from the (heavy) dashboard page module
	# lazily inside _tasks_for; stubbing the module here keeps this suite from
	# dragging in 1400 lines of endpoint code and its own frappe expectations.
	page_mod = types.ModuleType(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard"
	)
	page_mod._flatten_task_tree = lambda project: tree
	sys.modules[page_mod.__name__] = page_mod

	sys.modules.pop("erpnext_enhancements.project_enhancements.print_data", None)
	return importlib.import_module("erpnext_enhancements.project_enhancements.print_data")


def task(name, subject, start, end, level=0, progress=0, milestone=0):
	return (
		level,
		{
			"name": name,
			"subject": subject,
			"exp_start_date": start,
			"exp_end_date": end,
			"progress": progress,
			"expected_time": 0,
			"is_milestone": milestone,
			"status": "Open",
			"priority": "Medium",
		},
	)


@pytest.fixture()
def stub():
	install_frappe_stub()
	return install_frappe_stub


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def test_a_task_spanning_the_whole_project_fills_the_track(stub):
	pd = import_print_data([task("T1", "All of it", "2026-01-01", "2026-01-10")])
	data = pd.project_schedule_rows("PRJ-1")
	row = data["rows"][0]
	assert row["left_pct"] == 0
	assert row["width_pct"] == 100


def test_a_single_day_task_is_still_a_visible_bar(stub):
	"""Width is (end - start) + 1 day. Without the +1 a same-day task is a
	zero-width sliver that renders as nothing at all."""
	pd = import_print_data(
		[
			task("T1", "Long", "2026-01-01", "2026-01-11"),
			task("T2", "One day", "2026-01-05", "2026-01-05"),
		]
	)
	rows = pd.project_schedule_rows("PRJ-1")["rows"]
	assert rows[1]["width_pct"] > 0


def test_no_bar_can_overflow_the_track(stub):
	"""left + width must never exceed 100, or the bar escapes its cell and
	overlaps the next column in the PDF."""
	pd = import_print_data(
		[
			task("T1", "A", "2026-01-01", "2026-01-31"),
			task("T2", "Ends last", "2026-01-20", "2026-01-31"),
			task("T3", "Starts first", "2026-01-01", "2026-01-03"),
		]
	)
	for row in pd.project_schedule_rows("PRJ-1")["rows"]:
		assert row["left_pct"] >= 0
		assert row["left_pct"] + row["width_pct"] <= 100.0001


def test_offsets_are_relative_to_the_project_span_not_the_calendar(stub):
	pd = import_print_data(
		[
			task("T1", "First half", "2026-01-01", "2026-01-10"),
			task("T2", "Second half", "2026-01-11", "2026-01-20"),
		]
	)
	rows = pd.project_schedule_rows("PRJ-1")["rows"]
	assert rows[0]["left_pct"] == 0
	assert rows[1]["left_pct"] == pytest.approx(50, abs=0.5)


# ---------------------------------------------------------------------------
# Rows that would otherwise be dropped
# ---------------------------------------------------------------------------


def test_an_undated_task_is_listed_without_a_bar(stub):
	"""A printed schedule must show work that still needs dates rather than
	quietly omitting it."""
	pd = import_print_data(
		[
			task("T1", "Dated", "2026-01-01", "2026-01-10"),
			task("T2", "No dates", None, None),
		]
	)
	data = pd.project_schedule_rows("PRJ-1")
	assert len(data["rows"]) == 2
	undated = data["rows"][1]
	assert undated["undated"] is True
	assert undated["width_pct"] is None
	assert data["undated_count"] == 1


def test_a_task_with_only_one_date_gets_a_bar(stub):
	"""Half-dated tasks are common; they collapse to a one-day bar rather than
	falling into the undated bucket."""
	pd = import_print_data(
		[
			task("T1", "Anchor", "2026-01-01", "2026-01-10"),
			task("T2", "Start only", "2026-01-05", None),
			task("T3", "End only", None, "2026-01-08"),
		]
	)
	rows = pd.project_schedule_rows("PRJ-1")["rows"]
	assert rows[1]["width_pct"] is not None
	assert rows[2]["width_pct"] is not None


def test_a_project_with_no_dated_tasks_at_all_does_not_divide_by_zero(stub):
	pd = import_print_data([task("T1", "No dates", None, None)])
	data = pd.project_schedule_rows("PRJ-1")
	assert data["has_dates"] is False
	assert data["months"] == []
	assert len(data["rows"]) == 1


def test_an_empty_project_returns_an_empty_shape_not_none(stub):
	pd = import_print_data([])
	data = pd.project_schedule_rows("PRJ-1")
	assert data["rows"] == []
	assert data["has_dates"] is False


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_a_past_due_incomplete_task_is_flagged_overdue(stub):
	"""Stubbed today is 2026-03-01."""
	pd = import_print_data(
		[
			task("T1", "Late", "2026-01-01", "2026-02-01", progress=40),
			task("T2", "Done", "2026-01-01", "2026-02-01", progress=100),
			task("T3", "Future", "2026-04-01", "2026-04-10", progress=0),
		]
	)
	rows = pd.project_schedule_rows("PRJ-1")["rows"]
	assert rows[0]["overdue"] is True
	assert rows[1]["overdue"] is False
	assert rows[2]["overdue"] is False


def test_levels_are_carried_through_for_indentation(stub):
	pd = import_print_data(
		[
			task("P1", "Parent", "2026-01-01", "2026-01-10", level=0),
			task("C1", "Child", "2026-01-02", "2026-01-05", level=1),
		]
	)
	assert [r["level"] for r in pd.project_schedule_rows("PRJ-1")["rows"]] == [0, 1]


# ---------------------------------------------------------------------------
# Timeline header
# ---------------------------------------------------------------------------


def test_month_columns_cover_the_span_without_overflowing(stub):
	pd = import_print_data([task("T1", "Q1", "2026-01-01", "2026-03-31")])
	months = pd.project_schedule_rows("PRJ-1")["months"]
	assert len(months) >= 3
	assert months[0]["left_pct"] == 0
	last = months[-1]
	assert last["left_pct"] + last["width_pct"] <= 100.0001


def test_a_multi_year_span_falls_back_to_quarters(stub):
	"""A month per column over three years is unreadable at print width."""
	pd = import_print_data([task("T1", "Long haul", "2026-01-01", "2029-01-01")])
	months = pd.project_schedule_rows("PRJ-1")["months"]
	assert len(months) <= pd.MAX_MONTH_COLUMNS
	assert months[0]["label"].startswith("Q")
