"""Bench-free unit tests for the MDM Integration routing + action policy.

``mdm_integration.routing`` is frappe-free (the provider router, the capability
map, and the BYOD wipe guard), so it runs as plain ``unittest`` in CI — the
security-sensitive wipe guard is gated on every push. The frappe-backed pieces
(client adapters, sync, the action executor, the gated assistant tools) need a
live bench and the Mock provider; they run with ``bench run-tests``.

Run: python -m unittest erpnext_enhancements.tests.test_mdm_integration
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.mdm_integration import routing  # noqa: E402


class TestProviderRouting(unittest.TestCase):
	def test_by_device_type(self):
		self.assertEqual(routing.provider_key_for_device("Phone", "Android"), "Miradore")
		self.assertEqual(routing.provider_key_for_device("Tablet", "iPadOS"), "Miradore")
		self.assertEqual(routing.provider_key_for_device("Laptop", "Windows"), "Action1")
		self.assertEqual(routing.provider_key_for_device("Desktop", "Windows"), "Action1")

	def test_falls_back_to_platform(self):
		# device_type unset/Other → decide on platform.
		self.assertEqual(routing.provider_key_for_device("Other", "iOS"), "Miradore")
		self.assertEqual(routing.provider_key_for_device(None, "macOS"), "Action1")
		self.assertEqual(routing.provider_key_for_device("", "Linux"), "Action1")

	def test_unknown_returns_none(self):
		self.assertIsNone(routing.provider_key_for_device("Other", "Other"))
		self.assertIsNone(routing.provider_key_for_device(None, None))


class TestCapabilities(unittest.TestCase):
	def test_miradore_can_wipe_action1_cannot(self):
		self.assertTrue(routing.provider_supports("Miradore", "wipe"))
		self.assertTrue(routing.provider_supports("Miradore", "lock"))
		self.assertFalse(routing.provider_supports("Action1", "wipe"))
		self.assertFalse(routing.provider_supports("Miradore", "run_script"))

	def test_action1_can_reboot_and_run(self):
		self.assertTrue(routing.provider_supports("Action1", "reboot"))
		self.assertTrue(routing.provider_supports("Action1", "run_script"))
		self.assertFalse(routing.provider_supports("Action1", "lock"))

	def test_unknown_provider_supports_nothing(self):
		self.assertFalse(routing.provider_supports("Nope", "lock"))


class TestWipeGuard(unittest.TestCase):
	def test_byod_never_full_wipes(self):
		# Explicit full on BYOD while blocked → refused.
		mode, err = routing.resolve_wipe_mode("BYOD", "full", block_byod_full=True)
		self.assertIsNone(mode)
		self.assertTrue(err)
		# BYOD selective → allowed, stays selective.
		self.assertEqual(routing.resolve_wipe_mode("BYOD", "selective"), ("selective", None))
		# Even with the block off, BYOD is coerced to selective (never full).
		self.assertEqual(routing.resolve_wipe_mode("BYOD", "full", block_byod_full=False), ("selective", None))

	def test_company_full_requires_allow(self):
		self.assertEqual(routing.resolve_wipe_mode("Company", "full", allow_corporate_full=True), ("full", None))
		mode, err = routing.resolve_wipe_mode("Company", "full", allow_corporate_full=False)
		self.assertIsNone(mode)
		self.assertTrue(err)

	def test_company_selective_always_ok(self):
		self.assertEqual(routing.resolve_wipe_mode("Company", "selective"), ("selective", None))

	def test_default_mode_is_selective(self):
		self.assertEqual(routing.resolve_wipe_mode("Company", None), ("selective", None))


if __name__ == "__main__":
	unittest.main()
