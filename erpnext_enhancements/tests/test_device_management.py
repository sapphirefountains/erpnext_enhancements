"""Bench-free unit tests for the Device Management lifecycle/compliance rules.

``device_management.compliance`` carries no frappe dependency (the same design
as the Integrations Health tone helpers), so the status-transition guard and the
compliance derivation run as plain ``unittest`` in CI — no live bench. The
frappe-backed pieces (the Managed Device controller, the device API, the
dashboard) need a real site and are exercised with ``bench run-tests``.

Run: python -m unittest erpnext_enhancements.tests.test_device_management
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.device_management import compliance  # noqa: E402


class TestStatusTransitions(unittest.TestCase):
	def test_noop_always_allowed(self):
		for status in compliance.STATUSES:
			self.assertTrue(compliance.is_valid_transition(status, status))

	def test_representative_allowed(self):
		self.assertTrue(compliance.is_valid_transition("In Stock", "Assigned"))
		self.assertTrue(compliance.is_valid_transition("Assigned", "In Stock"))
		self.assertTrue(compliance.is_valid_transition("Assigned", "Lost/Stolen"))
		self.assertTrue(compliance.is_valid_transition("In Repair", "Assigned"))
		self.assertTrue(compliance.is_valid_transition("Lost/Stolen", "In Stock"))  # recovered

	def test_retired_is_terminal(self):
		for status in compliance.STATUSES:
			if status == "Retired":
				continue
			self.assertFalse(compliance.is_valid_transition("Retired", status))

	def test_cannot_go_straight_from_stock_to_lost_is_allowed_but_assigned_to_repair(self):
		# In Stock -> Lost/Stolen is allowed (a device can vanish off the shelf)…
		self.assertTrue(compliance.is_valid_transition("In Stock", "Lost/Stolen"))
		# …but Lost/Stolen cannot jump straight to Assigned (must re-stock first).
		self.assertFalse(compliance.is_valid_transition("Lost/Stolen", "Assigned"))

	def test_unknown_origin_is_permissive(self):
		# A legacy/hand-corrected status is never wedged.
		self.assertTrue(compliance.is_valid_transition("Something Else", "Retired"))


class TestDeriveCompliance(unittest.TestCase):
	def test_truth_table(self):
		self.assertEqual(compliance.derive_compliance(True, True), "Compliant")
		self.assertEqual(compliance.derive_compliance(True, False), "Non-Compliant")
		self.assertEqual(compliance.derive_compliance(False, True), "Non-Compliant")
		self.assertEqual(compliance.derive_compliance(False, False), "Non-Compliant")

	def test_accepts_int_flags(self):
		# The API passes cint() 0/1; both must behave like booleans.
		self.assertEqual(compliance.derive_compliance(1, 1), "Compliant")
		self.assertEqual(compliance.derive_compliance(1, 0), "Non-Compliant")
		self.assertEqual(compliance.derive_compliance(0, 0), "Non-Compliant")


if __name__ == "__main__":
	unittest.main()
