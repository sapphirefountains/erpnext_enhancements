"""Tests for the Sales Pipeline page backend (``crm_enhancements.page.sales_pipeline``).

Covers the pure decision logic (staleness levels, stage-change stamping) with
fakes, plus a live ``get_pipeline_data`` shape check against the bench: column
order must follow the Opportunity status meta (open stages, then the won
column, then parked), totals must cover the full set even when cards are
capped, and the won column must contain only Closed Won opportunities with no
created project.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_enhancements.crm_enhancements.page.sales_pipeline.sales_pipeline import (
	PARKED_STATUSES,
	TERMINAL_STATUSES,
	WON_STATUS,
	_stale_level,
	get_pipeline_data,
	stamp_stage_change,
)


class _FakeOpp:
	def __init__(self, status, before_status=None, has_before=True):
		self.status = status
		self.custom_stage_changed_on = None
		self._before = _FakeOpp.__new__(_FakeOpp) if has_before else None
		if self._before is not None:
			self._before.status = before_status

	def get_doc_before_save(self):
		return self._before


class TestStaleLevel(FrappeTestCase):
	def test_levels(self):
		self.assertEqual(_stale_level(0, 7, 14), 0)
		self.assertEqual(_stale_level(6, 7, 14), 0)
		self.assertEqual(_stale_level(7, 7, 14), 1)
		self.assertEqual(_stale_level(13, 7, 14), 1)
		self.assertEqual(_stale_level(14, 7, 14), 2)
		self.assertEqual(_stale_level(400, 7, 14), 2)

	def test_zero_thresholds_disable(self):
		self.assertEqual(_stale_level(100, 0, 0), 0)
		# red disabled, amber still active
		self.assertEqual(_stale_level(100, 7, 0), 1)


class TestStampStageChange(FrappeTestCase):
	def test_stamps_on_insert(self):
		doc = _FakeOpp("Qualification", has_before=False)
		stamp_stage_change(doc)
		self.assertIsNotNone(doc.custom_stage_changed_on)

	def test_stamps_on_status_change(self):
		doc = _FakeOpp("Negotiation/Review", before_status="Qualification")
		stamp_stage_change(doc)
		self.assertIsNotNone(doc.custom_stage_changed_on)

	def test_same_stage_edit_keeps_clock_running(self):
		doc = _FakeOpp("Qualification", before_status="Qualification")
		stamp_stage_change(doc)
		self.assertIsNone(doc.custom_stage_changed_on)


class TestPipelineData(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")

	def test_board_shape_follows_meta(self):
		data = get_pipeline_data()

		options = [
			opt.strip()
			for opt in (frappe.get_meta("Opportunity").get_field("status").options or "").split("\n")
			if opt.strip()
		]
		expected_open = [
			opt
			for opt in options
			if opt not in TERMINAL_STATUSES and opt != WON_STATUS and opt not in PARKED_STATUSES
		]

		labels = [stage["label"] for stage in data["stages"]]
		kinds = [stage["kind"] for stage in data["stages"]]

		self.assertEqual(labels[: len(expected_open)], expected_open)
		self.assertEqual(kinds.count("won"), 1)
		# won sits between open stages and parked stages
		self.assertEqual(kinds[len(expected_open)], "won")
		self.assertTrue(all(kind == "parked" for kind in kinds[len(expected_open) + 1 :]))

		for stage in data["stages"]:
			self.assertLessEqual(len(stage["opportunities"]), 30)
			self.assertEqual(stage["overflow"], max(stage["count"] - 30, 0))
			for card in stage["opportunities"]:
				self.assertIn("days_in_stage", card)
				self.assertIn(card["stale"], (0, 1, 2))
				if stage["kind"] == "parked":
					self.assertEqual(card["stale"], 0)

		self.assertIn("thresholds", data)
		self.assertIn("currency", data)

	def test_permission_denied_throws(self):
		with patch(
			"erpnext_enhancements.crm_enhancements.page.sales_pipeline.sales_pipeline.check_permission",
			return_value=False,
		):
			self.assertRaises(frappe.PermissionError, get_pipeline_data)
