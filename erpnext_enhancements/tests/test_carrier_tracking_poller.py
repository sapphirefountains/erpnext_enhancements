# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for tracked packages on Purchase Orders (v1.819.0, TASK-2026-03205).

* **Adding a number** (``package_rules``): cleaning, carrier choice, Other with a link, an unknown
  carrier recorded but never polled.
* **The permission rule**: Tracked Package's create and delete go to exactly
  ``package_rules.MANAGE_ROLES``, nobody but System Manager may write one, and the endpoints refuse
  without read on the order or the Tracked Package right, on a canceled order, and on anything but
  POST for a write.
* **The poller's rules** (``poll_rules``): which rows are due and in what order, the batch limit,
  what each carrier answer does to a row, and every reason polling stops: delivered, invalid, a
  week of "not found", too many unusable answers, three weeks without news, 90 days. A failure
  about the carrier never counts against a package and never changes its status.
* **The poller itself** (``tracking.run_poll``) on a ``frappe`` stub: silent and inert while the
  feature or every carrier is off; bounded by the batch and the time budget; a carrier that refused
  us skipped for the rest of the run; each answer committed on its own; ``poll`` never raises and
  logs a masked message when something unexpected breaks.

Invented tracking numbers and order names only: the repository is public.

Run: python -m unittest erpnext_enhancements.tests.test_carrier_tracking_poller -v
"""

import ast
import json
import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.carrier_tracking import carriers as C
from erpnext_enhancements.carrier_tracking import package_rules
from erpnext_enhancements.carrier_tracking import poll_rules as R

APP = REPO_ROOT / "erpnext_enhancements"
TP_JSON = APP / "carrier_tracking" / "doctype" / "tracked_package" / "tracked_package.json"
HOOKS = APP / "hooks.py"

NOW = datetime(2026, 11, 13, 10, 0, 0)
UPS_NUMBER = "1Z0000000000000001"
FEDEX_NUMBER = "100000000001"

FRAPPE = None
tracking = None
api = None


class Throw(Exception):
	pass


class PermissionError_(Throw):
	pass


class DoesNotExist(Throw):
	pass


def _make_frappe():
	"""A frappe stub with just what tracking.py and api/package_tracking.py touch."""
	frappe = types.ModuleType("frappe")
	frappe.PermissionError = PermissionError_
	frappe.DoesNotExistError = DoesNotExist
	frappe.ValidationError = Throw
	frappe.state = {}

	def throw(message, exc=Throw):
		raise exc(message)

	frappe.throw = throw
	frappe._ = lambda text: text
	frappe.whitelisted = {}

	def whitelist(methods=None, **kwargs):
		def wrap(fn):
			frappe.whitelisted[fn.__name__] = methods
			return fn

		return wrap

	frappe.whitelist = whitelist
	frappe.logged = []
	frappe.log_error = lambda title=None, message=None, **k: frappe.logged.append((title, message))
	frappe.get_traceback = lambda: frappe.state.get("traceback", "Traceback: boom")

	writes, commits = [], []
	frappe.writes, frappe.commits = writes, commits
	frappe.perms = {}
	frappe.orders = {}
	frappe.packages = {}

	def exists(doctype, name=None):
		if doctype == "DocType":
			return True
		if doctype == "Purchase Order":
			return name in frappe.orders
		if doctype == "Tracked Package":
			return name in frappe.packages
		return False

	def get_value(doctype, name, fields, as_dict=False):
		source = frappe.orders if doctype == "Purchase Order" else frappe.packages
		row = source.get(name)
		if row is None:
			return None  # what frappe.db.get_value returns for a missing row
		if as_dict:
			return types.SimpleNamespace(**{f: row.get(f) for f in fields})
		return tuple(row.get(f) for f in fields)

	frappe.db = types.SimpleNamespace(
		exists=exists,
		get_value=get_value,
		set_value=lambda doctype, name, updates, update_modified=True: writes.append((name, dict(updates))),
		commit=lambda: commits.append(1),
		rollback=lambda: None,
		sql=lambda *a, **k: [],
	)
	frappe.utils = types.SimpleNamespace(
		now_datetime=lambda: NOW,
		add_to_date=lambda dt, hours=0, as_datetime=True: dt + timedelta(hours=hours),
		cint=lambda v: int(v or 0),
	)
	cache_store = {}
	frappe.cache = types.SimpleNamespace(
		get_value=cache_store.get,
		set_value=lambda key, value, expires_in_sec=None: cache_store.__setitem__(key, value),
		delete_value=lambda key: cache_store.pop(key, None),
		delete_keys=lambda prefix: None,
	)
	frappe.cache_store = cache_store

	def has_permission(doctype, ptype="read", doc=None, **k):
		return frappe.perms.get((doctype, ptype, doc), frappe.perms.get((doctype, ptype), False))

	frappe.has_permission = has_permission
	frappe.get_all = lambda doctype, filters=None, fields=None, **k: [
		{f: row.get(f) for f in fields}
		for row in frappe.packages.values()
		if row.get("reference_name") == (filters or {}).get("reference_name")
	]

	inserted, deleted = [], []
	frappe.inserted, frappe.deleted = inserted, deleted

	class Doc(dict):
		def get(self, key, default=None):
			return dict.get(self, key, default)

		def __getattr__(self, key):
			return self.get(key)

		def insert(self):
			fields, problem = package_rules.new_package_fields(
				self["tracking_number"], self["carrier"], self["link_url"]
			)
			self.update(fields)
			self["name"] = f"tp-{len(inserted) + 1}"
			inserted.append(dict(self))
			return self

	frappe.get_doc = lambda data: Doc(data)
	frappe.delete_doc = lambda doctype, name, **k: deleted.append(name)
	return frappe


def setUpModule():
	global FRAPPE, tracking, api
	FRAPPE = _make_frappe()
	sys.modules["frappe"] = FRAPPE
	for name in (
		"erpnext_enhancements.carrier_tracking.config",
		"erpnext_enhancements.carrier_tracking.tracking",
		"erpnext_enhancements.api.package_tracking",
	):
		sys.modules.pop(name, None)
	import importlib

	tracking = importlib.import_module("erpnext_enhancements.carrier_tracking.tracking")
	api = importlib.import_module("erpnext_enhancements.api.package_tracking")


def tearDownModule():
	sys.modules.pop("frappe", None)
	for name in (
		"erpnext_enhancements.carrier_tracking.config",
		"erpnext_enhancements.carrier_tracking.tracking",
		"erpnext_enhancements.api.package_tracking",
	):
		sys.modules.pop(name, None)


def row(**fields):
	base = {
		"name": "tp-1",
		"creation": NOW - timedelta(days=1),
		"reference_name": "PO-TEST-1",
		"tracking_number": UPS_NUMBER,
		"resolved_carrier": C.UPS,
		"status": C.NOT_CHECKED,
		"status_description": "",
		"estimated_delivery": None,
		"delivered_on": None,
		"last_location": "",
		"last_checked": None,
		"last_success": None,
		"last_change": None,
		"failure_count": 0,
		"polling_stopped": 0,
		"stop_reason": "",
	}
	base.update(fields)
	return base


def ok(status=C.IN_TRANSIT, **fields):
	result = {
		"outcome": "ok",
		"status": status,
		"description": "On the way",
		"estimated_delivery": "2026-11-16",
		"delivered_at": None,
		"last_location": "Somewhere, UT, US",
		"error": "",
	}
	result.update(fields)
	return result


def failed(outcome, error=""):
	return {"outcome": outcome, "status": None, "error": error}


class TestAddingANumber(unittest.TestCase):
	def test_new_package_fields(self):
		fields, problem = package_rules.new_package_fields(UPS_NUMBER.lower(), "Auto")
		self.assertIsNone(problem)
		self.assertEqual(fields["resolved_carrier"], C.UPS)
		self.assertEqual(fields["status"], C.NOT_CHECKED)
		self.assertEqual(fields["polling_stopped"], 0)
		self.assertTrue(fields["tracking_url"].startswith("https://www.ups.com/"))

	def test_other_is_recorded_with_its_link_and_never_polled(self):
		fields, problem = package_rules.new_package_fields(
			"TBA000000000000", "Other", "https://example.com/p"
		)
		self.assertIsNone(problem)
		self.assertEqual(fields["status"], C.NOT_TRACKED)
		self.assertEqual(fields["polling_stopped"], 1)
		self.assertEqual(fields["tracking_url"], "https://example.com/p")
		self.assertEqual(fields["stop_reason"], C.NOT_TRACKABLE_REASON)

	def test_unknown_carrier_says_what_to_do(self):
		fields, _ = package_rules.new_package_fields("TBA000000000000", "Auto")
		self.assertEqual(fields["resolved_carrier"], C.UNKNOWN)
		self.assertEqual(fields["stop_reason"], package_rules.UNKNOWN_CARRIER_REASON)
		self.assertFalse(R.is_due(row(**fields), NOW))

	def test_explicit_carrier_wins(self):
		fields, _ = package_rules.new_package_fields("ABCD123456", "UPS")
		self.assertEqual(fields["resolved_carrier"], C.UPS)
		self.assertEqual(fields["polling_stopped"], 0)

	def test_refusals(self):
		for args in (
			("", "Auto"),
			("<script>", "Auto"),
			(UPS_NUMBER, "DHL"),
			("ABC12345", "Other", "ftp://x"),
			("ABC12345", "Other", "javascript:alert(1)"),
		):
			self.assertIsNotNone(package_rules.new_package_fields(*args)[1], args)


class TestDocTypeAndPermissionRule(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.meta = json.loads(TP_JSON.read_text(encoding="utf-8"))
		cls.fields = {f["fieldname"]: f for f in cls.meta["fields"]}

	def test_create_and_delete_are_exactly_the_manage_roles(self):
		perms = self.meta["permissions"]
		self.assertEqual({p["role"] for p in perms if p.get("create")}, set(package_rules.MANAGE_ROLES))
		self.assertEqual({p["role"] for p in perms if p.get("delete")}, set(package_rules.MANAGE_ROLES))
		self.assertEqual({p["role"] for p in perms if p.get("read")}, set(package_rules.MANAGE_ROLES))

	def test_only_system_manager_writes(self):
		self.assertEqual({p["role"] for p in self.meta["permissions"] if p.get("write")}, {"System Manager"})

	def test_external_contractor_is_not_given_tracking(self):
		self.assertNotIn("External Contractor", package_rules.MANAGE_ROLES)

	def test_options_match_the_rules(self):
		self.assertEqual(tuple(self.fields["status"]["options"].split("\n")), C.STATUS_OPTIONS)
		self.assertEqual(tuple(self.fields["carrier"]["options"].split("\n")), C.CARRIER_CHOICES)
		self.assertEqual(self.fields["status"]["default"], C.NOT_CHECKED)

	def test_carrier_fields_are_read_only(self):
		for name in (
			"status",
			"status_description",
			"estimated_delivery",
			"delivered_on",
			"last_location",
			"last_checked",
			"last_success",
			"last_change",
			"failure_count",
			"last_error",
			"polling_stopped",
			"resolved_carrier",
			"tracking_url",
			"supplier",
		):
			self.assertEqual(self.fields[name].get("read_only"), 1, name)

	def test_a_record_of_its_own_in_its_module(self):
		self.assertEqual(self.meta["module"], "Carrier Tracking")
		self.assertFalse(self.meta.get("istable"))
		self.assertEqual(self.fields["reference_name"]["fieldtype"], "Dynamic Link")
		# The purchaser's own date lives on the order and is never a field here.
		self.assertNotIn("custom_expected_delivery_date", self.fields)

	def test_rows_written_by_the_poller_cannot_overwrite_the_purchasers_date(self):
		source = (APP / "carrier_tracking" / "tracking.py").read_text(encoding="utf-8")
		self.assertNotIn("custom_expected_delivery_date", source)
		self.assertNotIn('set_value("Purchase Order"', source)


class TestEndpointPermissions(unittest.TestCase):
	def setUp(self):
		FRAPPE.perms.clear()
		FRAPPE.orders.clear()
		FRAPPE.packages.clear()
		FRAPPE.inserted.clear()
		FRAPPE.deleted.clear()
		FRAPPE.orders["PO-TEST-1"] = {"name": "PO-TEST-1", "docstatus": 1, "status": "To Receive and Bill"}
		FRAPPE.orders["PO-TEST-X"] = {"name": "PO-TEST-X", "docstatus": 2, "status": "Cancelled"}
		self._live = tracking.live_carriers
		tracking.live_carriers = lambda settings=None: []

	def tearDown(self):
		tracking.live_carriers = self._live

	def allow(self, *pairs):
		for doctype, ptype in pairs:
			FRAPPE.perms[(doctype, ptype)] = True

	def test_writes_are_post_only(self):
		self.assertEqual(FRAPPE.whitelisted["add_tracking_number"], ["POST"])
		self.assertEqual(FRAPPE.whitelisted["remove_tracking_number"], ["POST"])
		self.assertEqual(FRAPPE.whitelisted["refresh_order"], ["POST"])
		self.assertIsNone(FRAPPE.whitelisted["get_packages"])

	def test_add_needs_read_on_the_order(self):
		self.allow(("Tracked Package", "create"))
		with self.assertRaises(PermissionError_):
			api.add_tracking_number("PO-TEST-1", UPS_NUMBER)
		self.assertEqual(FRAPPE.inserted, [])

	def test_add_needs_create_on_tracked_package(self):
		self.allow(("Purchase Order", "read"))
		with self.assertRaises(PermissionError_):
			api.add_tracking_number("PO-TEST-1", UPS_NUMBER)
		self.assertEqual(FRAPPE.inserted, [])

	def test_add_never_needs_write_or_submit_on_the_order(self):
		self.allow(("Purchase Order", "read"), ("Tracked Package", "create"), ("Tracked Package", "read"))
		api.add_tracking_number("PO-TEST-1", " 1z000 0000000000001 ")
		self.assertEqual(len(FRAPPE.inserted), 1)
		self.assertEqual(FRAPPE.inserted[0]["tracking_number"], UPS_NUMBER)
		self.assertEqual(FRAPPE.inserted[0]["reference_name"], "PO-TEST-1")

	def test_add_refuses_a_canceled_order(self):
		self.allow(("Purchase Order", "read"), ("Tracked Package", "create"))
		with self.assertRaises(Throw):
			api.add_tracking_number("PO-TEST-X", UPS_NUMBER)

	def test_add_refuses_an_unknown_order(self):
		self.allow(("Purchase Order", "read"), ("Tracked Package", "create"))
		with self.assertRaises(DoesNotExist):
			api.add_tracking_number("PO-NOPE", UPS_NUMBER)

	def test_add_does_not_ask_a_carrier_while_off(self):
		self.allow(("Purchase Order", "read"), ("Tracked Package", "create"))
		called = []
		original = tracking.check_rows
		tracking.check_rows = lambda rows, manual=True: called.append(rows)
		try:
			api.add_tracking_number("PO-TEST-1", UPS_NUMBER)
		finally:
			tracking.check_rows = original
		self.assertEqual(called, [])

	def test_remove_needs_delete_and_read_on_the_order(self):
		FRAPPE.packages["tp-9"] = {
			"name": "tp-9",
			"reference_doctype": "Purchase Order",
			"reference_name": "PO-TEST-1",
		}
		self.allow(("Purchase Order", "read"))
		with self.assertRaises(PermissionError_):
			api.remove_tracking_number("tp-9")
		FRAPPE.perms.clear()
		self.allow(("Tracked Package", "delete"))
		with self.assertRaises(PermissionError_):
			api.remove_tracking_number("tp-9")
		self.assertEqual(FRAPPE.deleted, [])
		self.allow(("Purchase Order", "read"))
		api.remove_tracking_number("tp-9")
		self.assertEqual(FRAPPE.deleted, ["tp-9"])

	def test_remove_a_number_already_gone_is_a_sentence_not_a_crash(self):
		self.allow(("Tracked Package", "delete"), ("Purchase Order", "read"))
		with self.assertRaises(DoesNotExist):
			api.remove_tracking_number("tp-gone")
		with self.assertRaises(DoesNotExist):
			api.remove_tracking_number("")
		self.assertEqual(FRAPPE.deleted, [])

	def test_get_packages_is_empty_for_someone_without_tracking(self):
		self.allow(("Purchase Order", "read"))
		out = api.get_packages("PO-TEST-1")
		self.assertEqual(out["packages"], [])
		self.assertFalse(out["can_add"])

	def test_get_packages_needs_read_on_the_order(self):
		self.allow(("Tracked Package", "read"))
		with self.assertRaises(PermissionError_):
			api.get_packages("PO-TEST-1")

	def test_refresh_needs_read(self):
		self.allow(("Purchase Order", "read"))
		with self.assertRaises(PermissionError_):
			api.refresh_order("PO-TEST-1")


class TestDueAndPick(unittest.TestCase):
	def test_due(self):
		self.assertTrue(R.is_due(row(), NOW))
		self.assertTrue(R.is_due(row(last_checked=NOW - timedelta(hours=R.RECHECK_AFTER_HOURS)), NOW))
		self.assertFalse(R.is_due(row(last_checked=NOW - timedelta(minutes=30)), NOW))
		self.assertFalse(R.is_due(row(polling_stopped=1), NOW))
		self.assertFalse(R.is_due(row(resolved_carrier=C.OTHER), NOW))
		self.assertFalse(R.is_due(row(resolved_carrier=C.UNKNOWN), NOW))
		self.assertFalse(R.is_due(row(status=C.STATUS_LABELS[C.DELIVERED]), NOW))

	def test_never_checked_first_then_oldest(self):
		rows = [
			row(name="b", last_checked=NOW - timedelta(hours=3)),
			row(name="a", last_checked=NOW - timedelta(hours=9)),
			row(name="new"),
			row(name="recent", last_checked=NOW - timedelta(minutes=5)),
		]
		self.assertEqual([r["name"] for r in R.pick(rows, NOW)], ["new", "a", "b"])

	def test_batch_limit(self):
		rows = [row(name=f"r{i:03d}") for i in range(100)]
		self.assertEqual(len(R.pick(rows, NOW)), R.BATCH_SIZE)
		self.assertEqual(R.BATCH_SIZE, 25)


class TestApplyResult(unittest.TestCase):
	def test_ok_writes_status_and_resets_failures(self):
		updates = R.apply_result(row(failure_count=3, last_error="x"), ok(), NOW)
		self.assertEqual(updates["status"], "In Transit")
		self.assertEqual(updates["estimated_delivery"], "2026-11-16")
		self.assertEqual(updates["failure_count"], 0)
		self.assertEqual(updates["last_error"], "")
		self.assertEqual(updates["last_success"], NOW)
		self.assertEqual(updates["last_change"], NOW)
		self.assertNotIn("polling_stopped", updates)

	def test_unchanged_answer_keeps_last_change(self):
		before = row(
			status="In Transit",
			status_description="On the way",
			estimated_delivery="2026-11-16",
			last_location="Somewhere, UT, US",
			last_change=NOW - timedelta(days=2),
		)
		updates = R.apply_result(before, ok(), NOW)
		self.assertNotIn("last_change", updates)

	def test_delivered_stops(self):
		updates = R.apply_result(
			row(), ok(C.DELIVERED, delivered_at="2026-11-12 14:14:00", estimated_delivery=None), NOW
		)
		self.assertEqual(updates["status"], "Delivered")
		self.assertEqual(updates["delivered_on"], "2026-11-12 14:14:00")
		self.assertEqual(updates["polling_stopped"], 1)
		self.assertEqual(updates["stop_reason"], R.STOP_DELIVERED)

	def test_could_not_check_never_changes_the_status(self):
		before = row(status="Out for Delivery", failure_count=2)
		for outcome in ("rate_limited", "unavailable", "auth_failed", "not_configured"):
			updates = R.apply_result(before, failed(outcome, "x"), NOW)
			for key in ("status", "estimated_delivery", "delivered_on", "failure_count", "polling_stopped"):
				self.assertNotIn(key, updates, (outcome, key))
			self.assertEqual(updates["last_checked"], NOW)
			self.assertEqual(updates["last_error"], "x")

	def test_bad_response_counts_but_keeps_status(self):
		updates = R.apply_result(row(status="In Transit", failure_count=4), failed("bad_response"), NOW)
		self.assertEqual(updates["failure_count"], 5)
		self.assertNotIn("status", updates)
		self.assertEqual(updates["last_error"], R.OUTCOME_WORDS["bad_response"])

	def test_too_many_unusable_answers_stop(self):
		updates = R.apply_result(
			row(failure_count=R.MAX_ROW_FAILURES - 1, last_success=NOW - timedelta(days=1)),
			failed("not_found"),
			NOW,
		)
		self.assertEqual(updates["polling_stopped"], 1)
		self.assertEqual(updates["stop_reason"], R.STOP_FAILING)

	def test_not_found_is_patient_for_a_week(self):
		young = R.apply_result(row(creation=NOW - timedelta(days=2)), failed("not_found"), NOW)
		self.assertNotIn("polling_stopped", young)
		old = R.apply_result(
			row(creation=NOW - timedelta(days=R.NOT_FOUND_GIVE_UP_DAYS + 1)), failed("not_found"), NOW
		)
		self.assertEqual(old["stop_reason"], R.STOP_NEVER_FOUND)
		# Found once, then not found: not "never found".
		seen = R.apply_result(
			row(creation=NOW - timedelta(days=10), last_success=NOW - timedelta(days=3)),
			failed("not_found"),
			NOW,
		)
		self.assertNotIn("polling_stopped", seen)

	def test_invalid_stops_at_once(self):
		updates = R.apply_result(row(), failed("invalid"), NOW)
		self.assertEqual(updates["stop_reason"], R.STOP_INVALID)

	def test_stale_after_three_weeks_without_change(self):
		before = row(
			status="In Transit",
			status_description="On the way",
			estimated_delivery="2026-11-16",
			last_location="Somewhere, UT, US",
			last_change=NOW - timedelta(days=R.STALE_AFTER_DAYS + 1),
			creation=NOW - timedelta(days=30),
		)
		updates = R.apply_result(before, ok(), NOW)
		self.assertEqual(updates["stop_reason"], R.STOP_STALE)

	def test_too_old(self):
		updates = R.apply_result(row(creation=NOW - timedelta(days=R.MAX_TRACKING_DAYS + 1)), ok(), NOW)
		self.assertEqual(updates["stop_reason"], R.STOP_TOO_OLD)

	def test_a_persons_refresh_resumes_a_stopped_row_that_moved(self):
		before = row(
			polling_stopped=1,
			stop_reason=R.STOP_STALE,
			status="In Transit",
			last_change=NOW - timedelta(days=30),
		)
		updates = R.apply_result(before, ok(C.OUT_FOR_DELIVERY), NOW, manual=True)
		self.assertEqual(updates["polling_stopped"], 0)
		self.assertEqual(updates["stop_reason"], "")
		# The poller's own answer never resumes it.
		self.assertNotIn("polling_stopped", R.apply_result(before, ok(C.OUT_FOR_DELIVERY), NOW))

	def test_unsupported_marks_not_tracked(self):
		updates = R.apply_result(row(), failed("unsupported"), NOW)
		self.assertEqual(updates["status"], C.NOT_TRACKED)
		self.assertEqual(updates["polling_stopped"], 1)

	def test_garbage_result_is_unreadable_not_a_crash(self):
		for result in (None, {}, {"outcome": "something new"}):
			updates = R.apply_result(row(), result, NOW)
			self.assertEqual(updates["failure_count"], 1)


class FakeConfig:
	def __init__(self, enabled=True, carriers=(C.FEDEX, C.UPS)):
		self.enabled = enabled
		self.carriers = carriers


class TestRunPoll(unittest.TestCase):
	def setUp(self):
		FRAPPE.writes.clear()
		FRAPPE.commits.clear()
		FRAPPE.logged.clear()
		self.saved = {
			"settings": tracking.config._settings,
			"enabled": tracking.config.feature_enabled,
			"carrier_config": tracking.config.carrier_config,
			"due_rows": tracking.due_rows,
			"track": tracking.client.track,
		}
		self.calls = []
		self.answers = {}
		self.rows = []
		self.setup(enabled=True, carriers=(C.FEDEX, C.UPS))
		tracking.due_rows = lambda carriers, now, limit: [
			r for r in self.rows if r["resolved_carrier"] in carriers
		][:limit]

		def fake_track(cfg, number, cache, http=None, now=None):
			self.calls.append((cfg.carrier, number))
			return self.answers.get(cfg.carrier, ok())

		tracking.client.track = fake_track

	def setup(self, enabled, carriers):
		tracking.config._settings = lambda: object()
		tracking.config.feature_enabled = lambda settings=None: enabled
		tracking.config.carrier_config = lambda carrier, settings=None: (
			tracking.client.Config(carrier, "Sandbox", "id", "secret") if carrier in carriers else None
		)

	def tearDown(self):
		tracking.config._settings = self.saved["settings"]
		tracking.config.feature_enabled = self.saved["enabled"]
		tracking.config.carrier_config = self.saved["carrier_config"]
		tracking.due_rows = self.saved["due_rows"]
		tracking.client.track = self.saved["track"]

	def test_off_does_nothing_and_logs_nothing(self):
		self.rows = [row(name=f"r{i}") for i in range(3)]
		self.setup(enabled=False, carriers=(C.FEDEX, C.UPS))
		self.assertIsNone(tracking.poll())
		self.setup(enabled=True, carriers=())
		self.assertIsNone(tracking.poll())
		self.assertEqual((self.calls, FRAPPE.writes, FRAPPE.logged), ([], [], []))

	def test_bounded_by_the_batch_and_committed_one_by_one(self):
		self.rows = [row(name=f"r{i:03d}", tracking_number=f"1Z00000000000{i:05d}") for i in range(60)]
		out = tracking.run_poll()
		self.assertEqual(out["checked"], R.BATCH_SIZE)
		self.assertEqual(len(self.calls), R.BATCH_SIZE)
		self.assertEqual(len(FRAPPE.commits), R.BATCH_SIZE)
		self.assertEqual(len(FRAPPE.writes), R.BATCH_SIZE)

	def test_bounded_by_the_time_budget(self):
		self.rows = [row(name=f"r{i}") for i in range(10)]
		ticks = iter([0, 0, 50, 100, 130, 200, 300, 400, 500, 600, 700, 800])
		out = tracking.run_poll(clock=lambda: next(ticks))
		self.assertEqual(out["checked"], 3)

	def test_a_carrier_that_refused_us_is_skipped_for_the_rest_of_the_run(self):
		self.rows = [
			row(name="u1"),
			row(name="f1", resolved_carrier=C.FEDEX, tracking_number=FEDEX_NUMBER),
			row(name="u2"),
			row(name="u3"),
		]
		self.answers[C.UPS] = failed("rate_limited", "slow down")
		out = tracking.run_poll()
		# Rows never checked are taken in name order: f1, u1, then u2 and u3 are skipped.
		self.assertEqual(self.calls, [(C.FEDEX, FEDEX_NUMBER), (C.UPS, UPS_NUMBER)])
		self.assertEqual(out["skipped_carriers"], [C.UPS])

	def test_only_live_carriers_are_selected(self):
		self.setup(enabled=True, carriers=(C.FEDEX,))
		self.rows = [row(name="u1"), row(name="f1", resolved_carrier=C.FEDEX, tracking_number=FEDEX_NUMBER)]
		tracking.run_poll()
		self.assertEqual(self.calls, [(C.FEDEX, FEDEX_NUMBER)])

	def test_writes_without_touching_modified(self):
		recorded = []
		original = FRAPPE.db.set_value
		FRAPPE.db.set_value = lambda doctype, name, updates, update_modified=True: recorded.append(
			update_modified
		)
		try:
			self.rows = [row()]
			tracking.run_poll()
		finally:
			FRAPPE.db.set_value = original
		self.assertEqual(recorded, [False])

	def test_poll_never_raises_and_logs_masked(self):
		def boom():
			raise RuntimeError("Authorization: Bearer abcdef0123456789 client_secret=hunter22")

		original = tracking.run_poll
		tracking.run_poll = boom
		FRAPPE.state["traceback"] = (
			"Traceback ... RuntimeError: Authorization: Bearer abcdef0123456789 client_secret=hunter22"
		)
		try:
			self.assertIsNone(tracking.poll())
		finally:
			tracking.run_poll = original
			FRAPPE.state.pop("traceback", None)
		self.assertEqual(len(FRAPPE.logged), 1)
		title, message = FRAPPE.logged[0]
		self.assertNotIn("abcdef0123456789", message)
		self.assertNotIn("hunter22", message)


class TestRefreshCooldown(unittest.TestCase):
	def test_second_refresh_within_a_minute_asks_nobody(self):
		checked = []
		saved = (tracking.check_rows, tracking.rows_for_order)
		tracking.check_rows = lambda rows, manual=True: checked.append(rows) or len(rows)
		tracking.rows_for_order = lambda po, fields=None: [row(), row(name="o", resolved_carrier=C.OTHER)]
		FRAPPE.cache_store.clear()
		try:
			self.assertEqual(tracking.refresh_order("PO-TEST-1"), 1)
			self.assertIsNone(tracking.refresh_order("PO-TEST-1"))
		finally:
			tracking.check_rows, tracking.rows_for_order = saved
		self.assertEqual(len(checked), 1)
		self.assertEqual([r["name"] for r in checked[0]], ["tp-1"])


class TestWiring(unittest.TestCase):
	def test_poll_is_hourly(self):
		tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
		events = next(
			node.value
			for node in tree.body
			if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "scheduler_events"
		)
		hourly = ast.literal_eval(events.values[[k.value for k in events.keys].index("hourly")])
		self.assertIn("erpnext_enhancements.carrier_tracking.tracking.poll", hourly)

	def test_limits_are_the_documented_ones(self):
		self.assertEqual(
			(
				R.RECHECK_AFTER_HOURS,
				R.NOT_FOUND_GIVE_UP_DAYS,
				R.MAX_ROW_FAILURES,
				R.STALE_AFTER_DAYS,
				R.MAX_TRACKING_DAYS,
				R.RUN_BUDGET_SECONDS,
			),
			(2, 7, 12, 21, 90, 120),
		)


if __name__ == "__main__":
	unittest.main()
