"""Bench-free tests for the KPI marketing data-source alert (v1.250.1).

Guards a bug that had been live since the function was written, and whose only
symptom was silence.

``kpi_dashboards.snapshots`` imported ``frappe`` but not ``_``, so every
``_(...)`` inside :func:`_alert_on_source_change` raised ``NameError``. The
function wraps its whole body in ``except Exception: frappe.log_error(...)`` —
correct, because a notification problem must not cost the nightly snapshot — so
the NameError was caught and written to the Error Log under "KPI marketing web —
source alert". That title reads as *the alerting is broken*, not *the data source
is broken*, so a failed GA4 or Search Console pull produced no notification and
nothing that looked like one was missing.

The assertion that would have caught it is the one this suite leads with:
**a failing source must actually call the notifier.** Everything else here —
transition-only firing, recovery, the silence on a healthy first run — was
already correct, and is pinned so a future edit to the alert logic can't
reintroduce silence by a different route.

Static coverage for the same bug class lives in the ``ruff --select F821`` hard
gate in ci.yml; this suite pins the *behaviour* that gate's absence was hiding.

Run: python -m unittest erpnext_enhancements.tests.test_kpi_source_alert
"""

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

#: Mutable state the frappe stub reads at call time.
STATE = {"cache": {}, "notified": [], "errors_logged": [], "role_holders": []}

snapshots = None


class _Cache:
	def get_value(self, key):
		return STATE["cache"].get(key)

	def set_value(self, key, value, **kwargs):
		STATE["cache"][key] = value


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")

	class _Dict(dict):
		def __getattr__(self, key):
			try:
				return self[key]
			except KeyError:
				raise AttributeError(key)

		def __setattr__(self, key, value):
			self[key] = value

	frappe._ = lambda s: s
	frappe._dict = _Dict
	frappe.cache = lambda: _Cache()
	frappe.get_traceback = lambda: "traceback"
	frappe.session = types.SimpleNamespace(user="admin@example.com")
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)

	# The assertion that matters: did the alert reach a person? Recorded rather
	# than mocked away, so a regression that stops notifying is visible here.
	def log_error(message=None, title=None, *args, **kwargs):
		STATE["errors_logged"].append(title or message)

	frappe.log_error = log_error

	def get_doc(payload):
		if isinstance(payload, dict) and payload.get("doctype") == "Notification Log":
			STATE["notified"].append(payload)
		doc = _Dict(payload if isinstance(payload, dict) else {})
		doc["insert"] = lambda **kwargs: doc
		return doc

	frappe.get_doc = get_doc
	frappe.get_all = lambda *a, **k: list(STATE["role_holders"])
	frappe.new_doc = lambda dt: _Dict(doctype=dt)

	utils = types.ModuleType("frappe.utils")
	utils.escape_html = lambda s: str(s)
	utils.cint = lambda v: int(v or 0)
	utils.flt = lambda v, precision=None: float(v or 0)
	utils.add_days = lambda d, n: d
	utils.add_months = lambda d, n: d
	utils.getdate = lambda v=None: v
	utils.nowdate = lambda: "2026-08-06"
	utils.now_datetime = lambda: None
	frappe.utils = utils

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils

	# kpi_dashboards.metrics is deliberately frappe-free ("no frappe import, so it
	# runs in the bench-free CI suite"), so it is imported for real rather than
	# stubbed — a stub of its constants would be one more thing to drift.
	# attribution is not: it pulls frappe, and nothing here exercises it.
	attribution = types.ModuleType("erpnext_enhancements.crm_enhancements.attribution")
	attribution.UNKNOWN_LEAD_SOURCE = "Unknown"
	sys.modules["erpnext_enhancements.crm_enhancements.attribution"] = attribution


def setUpModule():
	global snapshots
	_install_frappe_stub()
	for module in list(sys.modules):
		if module.startswith("erpnext_enhancements"):
			sys.modules.pop(module, None)
	_install_frappe_stub()
	from erpnext_enhancements.kpi_dashboards import snapshots as mod

	snapshots = mod


def _reset(previous=None):
	STATE["cache"] = {}
	STATE["notified"] = []
	STATE["errors_logged"] = []
	STATE["role_holders"] = ["manager@example.com"]
	if previous is not None:
		STATE["cache"][snapshots._SOURCE_STATE_KEY] = previous


class TestSourceAlertNotifies(unittest.TestCase):
	"""The regression itself: does a broken source actually reach a person?"""

	def setUp(self):
		_reset()

	def test_failing_source_notifies(self):
		snapshots._alert_on_source_change(ga4_ok=False, gsc_ok=True, errors=["boom"])

		self.assertEqual(len(STATE["notified"]), 1, "a failing source must notify somebody")
		self.assertIn("GA4", STATE["notified"][0]["subject"])

	def test_failing_source_logs_no_error(self):
		"""The tell-tale of the old bug: a swallowed NameError in the Error Log.

		Before the fix this produced an Error Log row and zero notifications --
		exactly inverted. Asserting the absence of the log entry is what
		distinguishes 'working' from 'failing quietly' here.
		"""
		snapshots._alert_on_source_change(ga4_ok=False, gsc_ok=True, errors=["boom"])
		self.assertEqual(STATE["errors_logged"], [])

	def test_message_carries_the_reported_error(self):
		snapshots._alert_on_source_change(ga4_ok=False, gsc_ok=True, errors=["quota exceeded"])
		self.assertIn("quota exceeded", STATE["notified"][0]["email_content"])

	def test_missing_error_detail_still_builds_a_message(self):
		"""The `or _("no detail")` branch -- one of the five NameError sites."""
		snapshots._alert_on_source_change(ga4_ok=False, gsc_ok=True, errors=[])
		self.assertEqual(len(STATE["notified"]), 1)
		self.assertEqual(STATE["errors_logged"], [])

	def test_both_sources_failing_notifies_twice(self):
		snapshots._alert_on_source_change(ga4_ok=False, gsc_ok=False, errors=["boom"])
		self.assertEqual(len(STATE["notified"]), 2)


class TestSourceAlertTransitions(unittest.TestCase):
	"""Transition-only firing. Already correct; pinned so it stays that way."""

	def test_recovery_notifies(self):
		_reset(previous={"ga4": False, "gsc": True})
		snapshots._alert_on_source_change(ga4_ok=True, gsc_ok=True, errors=[])

		self.assertEqual(len(STATE["notified"]), 1)
		self.assertIn("working again", STATE["notified"][0]["subject"])
		self.assertEqual(STATE["errors_logged"], [])

	def test_still_failing_stays_quiet(self):
		"""A nightly 'still broken' mail gets filtered, and then a real outage is invisible."""
		_reset(previous={"ga4": False, "gsc": True})
		snapshots._alert_on_source_change(ga4_ok=False, gsc_ok=True, errors=["boom"])
		self.assertEqual(STATE["notified"], [])

	def test_still_healthy_stays_quiet(self):
		_reset(previous={"ga4": True, "gsc": True})
		snapshots._alert_on_source_change(ga4_ok=True, gsc_ok=True, errors=[])
		self.assertEqual(STATE["notified"], [])

	def test_first_run_reports_a_down_source_but_not_a_healthy_one(self):
		"""Unknown previous state is not a reason to stay quiet about a broken source."""
		_reset()
		snapshots._alert_on_source_change(ga4_ok=True, gsc_ok=True, errors=[])
		self.assertEqual(STATE["notified"], [])

		_reset()
		snapshots._alert_on_source_change(ga4_ok=False, gsc_ok=False, errors=["boom"])
		self.assertEqual(len(STATE["notified"]), 2)

	def test_state_is_persisted_for_the_next_run(self):
		_reset()
		snapshots._alert_on_source_change(ga4_ok=False, gsc_ok=True, errors=["boom"])
		self.assertEqual(
			STATE["cache"][snapshots._SOURCE_STATE_KEY], {"ga4": False, "gsc": True}
		)


class TestSourceAlertNeverRaises(unittest.TestCase):
	"""The guard is correct and must stay -- a notification must not cost the snapshot."""

	def test_a_failing_notifier_is_swallowed_and_logged(self):
		_reset()
		original = snapshots._notify_managers
		snapshots._notify_managers = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("smtp"))
		try:
			snapshots._alert_on_source_change(ga4_ok=False, gsc_ok=True, errors=["boom"])
		finally:
			snapshots._notify_managers = original

		self.assertEqual(STATE["notified"], [])
		self.assertEqual(len(STATE["errors_logged"]), 1)


if __name__ == "__main__":
	unittest.main()
