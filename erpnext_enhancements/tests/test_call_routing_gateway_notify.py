# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The gateway-refresh ping: when it fires, when it must not, and what it carries.

Saving a Call Routing Rule pings Triton so it drops its 60-second cache and the edit
takes effect now rather than within the minute. That is a `doc_event`-shaped hook reaching
an external service, which puts it in the class this repo is most careful about.

Two properties here would each be a real incident, and neither is visible by reading the
happy path:

* **It must stay silent during migrate, install, patch, import and test.** Those all save
  documents. A `bench migrate` that POSTs to the gateway is slower and noisier for no
  reason, on a fresh site is announcing rules that do not exist yet, and queues jobs that
  the deploy's own `FLUSHDB` then destroys. ERPNext's test bootstrap saves documents too,
  which is exactly how a hook ends up making network calls inside somebody's test run.
* **The shared secret must not travel as a job argument.** `frappe.enqueue` serialises its
  kwargs into redis, so passing the secret would leave `admin_webhook_secret` sitting in
  the queue payload. `push_refresh` reads it inside the worker instead. That is easy to
  "simplify" away later by someone matching `Triton Settings.on_update`, which does pass
  it — hence the assertion.

Stubs `frappe` in `setUpModule` (execution time, not import time, so it never fools the
bench-only suites' `import frappe` skip-guards). Its own CI step for that reason.
"""

from __future__ import annotations

import sys
import types
import unittest

_SAVED: dict[str, object] = {}
call_routing = None


def _fake_frappe() -> types.ModuleType:
	frappe = types.ModuleType("frappe")

	frappe.flags = types.SimpleNamespace()
	frappe.enqueue_calls = []
	frappe.logged_errors = []

	def enqueue(method, **kwargs):
		frappe.enqueue_calls.append({"method": method, "kwargs": kwargs})

	frappe.enqueue = enqueue
	frappe.log_error = lambda *a, **k: frappe.logged_errors.append((a, k))
	frappe.get_traceback = lambda: "traceback"
	frappe.get_doc = lambda *a, **k: None
	frappe.db = types.SimpleNamespace(get_singles_dict=lambda *a, **k: {})

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	utils.get_system_timezone = lambda: "America/Denver"
	utils.getdate = lambda v=None: v
	utils.now_datetime = lambda: None
	frappe.utils = utils

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	return frappe


def setUpModule() -> None:
	global call_routing
	for name in ("frappe", "frappe.utils", "erpnext_enhancements.api.telephony"):
		_SAVED[name] = sys.modules.get(name)

	_fake_frappe()

	# call_routing imports _softphone_identity from api.telephony, which imports the Twilio
	# SDK at module scope. Stub the module rather than the SDK: the identity function is the
	# only thing needed here, and it must stay the same one the desk softphone registers.
	telephony = types.ModuleType("erpnext_enhancements.api.telephony")
	telephony._softphone_identity = lambda email: "erpnext_" + (email or "").replace("@", "_").replace(".", "_")
	telephony.LEGACY_SOFTPHONE_IDENTITY = "nikolas_erpnext"
	telephony._softphone_users = lambda settings: []
	sys.modules["erpnext_enhancements.api.telephony"] = telephony

	from erpnext_enhancements.ai_governance import call_routing as module

	call_routing = module


def tearDownModule() -> None:
	for name, original in _SAVED.items():
		if original is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = original


class TestNotifyGateway(unittest.TestCase):
	def setUp(self) -> None:
		self.frappe = sys.modules["frappe"]
		self.frappe.enqueue_calls.clear()
		self.frappe.logged_errors.clear()
		self.frappe.flags = types.SimpleNamespace()

	def test_an_ordinary_save_enqueues_the_ping(self) -> None:
		call_routing.notify_gateway()

		self.assertEqual(len(self.frappe.enqueue_calls), 1)
		call = self.frappe.enqueue_calls[0]
		self.assertEqual(call["method"], "erpnext_enhancements.ai_governance.call_routing.push_refresh")

	def test_it_is_enqueued_after_commit(self) -> None:
		"""Before commit, the worker could read the row the save has not written yet —
		and a rolled-back save would still have announced itself."""
		call_routing.notify_gateway()

		self.assertTrue(self.frappe.enqueue_calls[0]["kwargs"].get("enqueue_after_commit"))

	def test_no_secret_travels_as_a_job_argument(self) -> None:
		"""enqueue kwargs are serialised into redis. The secret is read in the worker."""
		call_routing.notify_gateway()

		serialised = repr(self.frappe.enqueue_calls[0]["kwargs"]).lower()
		for leak in ("secret", "password", "token", "gateway_url"):
			self.assertNotIn(leak, serialised, f"{leak!r} reached the job payload")

	def test_silent_during_migrate_install_patch_import_and_test(self) -> None:
		"""Every one of these saves documents. None of them should reach the network."""
		for flag in ("in_migrate", "in_install", "in_patch", "in_import", "in_test"):
			with self.subTest(flag=flag):
				self.frappe.enqueue_calls.clear()
				self.frappe.flags = types.SimpleNamespace(**{flag: True})

				call_routing.notify_gateway()

				self.assertEqual(self.frappe.enqueue_calls, [], f"pinged the gateway during {flag}")

	def test_the_suppression_list_is_not_silently_emptied(self) -> None:
		"""A guard whose list got trimmed still passes every test above for the flags left
		in it. Pin the set itself, so removing one is a deliberate edit."""
		self.assertEqual(
			set(call_routing._SUPPRESS_FLAGS),
			{"in_migrate", "in_install", "in_patch", "in_import", "in_test"},
		)

	def test_a_broken_queue_never_breaks_the_save(self) -> None:
		"""A settings page that refuses to save because a gateway is unreachable would be
		a far worse failure than a stale cache."""
		def boom(*a, **k):
			raise RuntimeError("redis is down")

		self.frappe.enqueue = boom
		try:
			call_routing.notify_gateway()  # must not raise
		finally:
			self.frappe.enqueue = lambda method, **kwargs: self.frappe.enqueue_calls.append(
				{"method": method, "kwargs": kwargs}
			)

		self.assertEqual(len(self.frappe.logged_errors), 1, "the failure was swallowed silently")


class TestPushRefresh(unittest.TestCase):
	"""The worker reuses Triton Settings' own webhook rather than a second copy of it."""

	def setUp(self) -> None:
		self.frappe = sys.modules["frappe"]
		self.calls: list[tuple] = []

		stub = types.ModuleType(
			"erpnext_enhancements.ai_governance.doctype.triton_settings.triton_settings"
		)
		stub.trigger_refresh_webhook = lambda url, secret: self.calls.append((url, secret))
		self._saved = sys.modules.get(stub.__name__)
		sys.modules[stub.__name__] = stub

	def tearDown(self) -> None:
		name = "erpnext_enhancements.ai_governance.doctype.triton_settings.triton_settings"
		if self._saved is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = self._saved

	def test_it_reads_the_url_and_secret_itself(self) -> None:
		settings = types.SimpleNamespace(
			gateway_url="https://triton.sapphirefountains.com",
			get_password=lambda field, raise_exception=False: "s3cr3t",
		)
		self.frappe.get_doc = lambda *a, **k: settings

		call_routing.push_refresh()

		self.assertEqual(self.calls, [("https://triton.sapphirefountains.com", "s3cr3t")])


if __name__ == "__main__":
	unittest.main()
