"""Bench-free unit tests for the MDM Integration routing + action policy.

The provider clients themselves (the exact requests sent to Miradore and Action1)
are pinned in ``test_mdm_provider_clients.py``, which needs a frappe stub.

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

from erpnext_enhancements.mdm_integration import routing


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


class TestRetryClassification(unittest.TestCase):
	"""Auth/permission/not-found/bad-request are permanent (a scheduled retry can't
	fix a bad key); 5xx / 429 / network errors (no status) stay retryable. This is
	what stops a standing Miradore 401 from being retried — and re-logged — every
	cycle."""

	def test_auth_and_client_config_errors_are_permanent(self):
		for code in (400, 401, 403, 404):
			self.assertFalse(routing.is_retryable_status(code), f"{code} should not be retryable")

	def test_transient_errors_stay_retryable(self):
		for code in (429, 500, 502, 503, 504):
			self.assertTrue(routing.is_retryable_status(code), f"{code} should be retryable")

	def test_no_status_is_retryable(self):
		# A network/transport error carries no HTTP status — treat as transient.
		self.assertTrue(routing.is_retryable_status(None))


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



class TestMiradoreSelectiveWipeGuard(unittest.TestCase):
	"""Miradore has no selective Wipe, so selective is a Retire -- which itself
	factory-resets fully managed Android devices and Shared iPads. The guard
	refuses those, and anything it does not recognise."""

	def test_fully_managed_android_is_refused(self):
		# Retire and Wipe both factory-reset a Device Owner; there is no selective path.
		refusal = routing.miradore_selective_wipe_refusal("AndroidDeviceOwner", "Pixel 8")
		self.assertIn("fully managed", refusal)

	def test_enrollments_where_retire_keeps_personal_data(self):
		self.assertIsNone(routing.miradore_selective_wipe_refusal("AndroidProfileOwner", "Galaxy S23"))
		self.assertIsNone(routing.miradore_selective_wipe_refusal("iOSUnsupervised", "iPhone 14"))
		self.assertIsNone(routing.miradore_selective_wipe_refusal("iOSSupervised", "iPhone15,2"))

	def test_supervised_ipad_might_be_shared_and_is_refused(self):
		self.assertIn("Shared iPad", routing.miradore_selective_wipe_refusal("iOSSupervised", "iPad13,1"))
		# An unsupervised iPad cannot be a Shared iPad (that requires supervision).
		self.assertIsNone(routing.miradore_selective_wipe_refusal("iOSUnsupervised", "iPad13,1"))

	def test_unknown_or_unlisted_management_types_fail_closed(self):
		unknown = (None, "", "Unknown", "None", "AndroidDeviceAdministrator", "BuiltInMDM", "Something new")
		for kind in unknown:
			self.assertTrue(routing.miradore_selective_wipe_refusal(kind, "Phone"), kind)


class TestAction1ScriptTarget(unittest.TestCase):
	def test_windows_gets_powershell(self):
		self.assertEqual(routing.action1_script_target("Windows"), ("Windows", "PowerShell"))
		self.assertEqual(routing.action1_script_target("Windows 11 (25H2)"), ("Windows", "PowerShell"))

	def test_mac_and_linux_get_bash(self):
		self.assertEqual(routing.action1_script_target("Mac"), ("Mac", "Bash"))
		self.assertEqual(routing.action1_script_target("macOS"), ("Mac", "Bash"))
		self.assertEqual(routing.action1_script_target("Linux"), ("Linux", "Bash"))

	def test_unscriptable_platform(self):
		self.assertEqual(routing.action1_script_target("Android"), (None, None))
		self.assertEqual(routing.action1_script_target(None), (None, None))


if __name__ == "__main__":
	unittest.main()
