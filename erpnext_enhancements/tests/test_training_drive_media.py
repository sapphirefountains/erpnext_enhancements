"""Bench-free unit tests for the training video health check.

Stubs a minimal ``frappe`` (no site/bench) so ``training.drive_media`` runs under
plain unittest. Installed in ``setUpModule`` (execution time), not at import, so
it never fools the bench-only suites' ``import frappe`` skip-guards.

Three things here are worth more than the rest, because each is a defect that
would be invisible in production:

  * **The query composition.** Frappe builds ``<filters> AND (<or_filters>)``, so
    the staleness test and the never-verified test cannot both be ``filters``.
    Worse, the never-verified case is currently carried *implicitly*: frappe emits
    ``coalesce(`last_verified_on`, '0001-01-01') < '<cutoff>'`` for a ``<``
    comparison, and year 1 beats any cutoff. That undocumented coalesce is the only
    thing that makes a brand-new asset visible to this job, and nothing at the call
    site says so. This suite pins the explicit clause instead, because the failure
    it guards against -- a queue that silently never returns new assets -- looks
    exactly like a run where everything was already healthy.
  * **Absent vs unreachable.** A 404 earns ``Missing``; a 403 or a timeout must
    not, or one bad thirty seconds condemns a healthy library.
  * **The retry property.** An inconclusive check deliberately does NOT stamp
    ``last_verified_on``, so the asset stays at the head of the queue. A test that
    only checked the status would pass while the asset silently never got looked
    at again.

Run: python -m unittest erpnext_enhancements.tests.test_training_drive_media
"""

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

drive_media = None

# Every frappe.db.set_value call the module makes, as (doctype, name, updates).
WRITES = []

# The kwargs of the last frappe.get_all call.
LAST_QUERY = {}

# What the fake get_all hands back.
ROWS = []


class _FakeResp:
	def __init__(self, status):
		self.status = status


class _Httpish(Exception):
	"""Stands in for googleapiclient HttpError, which carries ``resp.status``."""

	def __init__(self, status):
		super().__init__(f"HTTP {status}")
		self.resp = _FakeResp(status)


class _FakeObjects:
	def __init__(self, outcome):
		self._outcome = outcome

	# `object` shadows the builtin because the GCS JSON API's own kwarg is called that.
	def get(self, bucket=None, object=None):
		outcome = self._outcome

		class _Req:
			def execute(self):
				if isinstance(outcome, Exception):
					raise outcome
				return outcome

		return _Req()


class _FakeStorage:
	def __init__(self, outcome):
		self._outcome = outcome

	def objects(self):
		return _FakeObjects(self._outcome)


def setUpModule():
	global drive_media

	frappe = types.ModuleType("frappe")

	def get_all(doctype, **kwargs):
		LAST_QUERY.clear()
		LAST_QUERY.update(kwargs)
		LAST_QUERY["doctype"] = doctype
		return list(ROWS)

	def set_value(doctype, name, updates, update_modified=True):
		WRITES.append((doctype, name, dict(updates)))

	frappe.get_all = get_all
	frappe.db = types.SimpleNamespace(set_value=set_value)

	utils = types.ModuleType("frappe.utils")

	def cint(value):
		try:
			return int(float(value))
		except (TypeError, ValueError):
			return 0

	utils.cint = cint
	utils.now_datetime = lambda: "2026-08-19 09:00:00"
	utils.add_to_date = lambda dt, hours=0: f"{dt}+{hours}h"

	frappe.utils = utils
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils

	# training.gcs_media is imported by the module under test; stub only what it
	# actually reaches for, so this suite cannot be broken by unrelated signing work.
	gcs_media = types.ModuleType("erpnext_enhancements.training.gcs_media")
	gcs_media.is_configured = lambda: True
	gcs_media._bucket = lambda: "sf-erpnext-training-media"
	gcs_media._storage_service = lambda: _FakeStorage(None)
	gcs_media._describe = lambda exc: f"described: {exc}"
	sys.modules["erpnext_enhancements.training.gcs_media"] = gcs_media

	from erpnext_enhancements.training import drive_media as module

	drive_media = module


def _row(**overrides):
	row = {
		"name": "TRN-VID-00001",
		"gcs_object": "training/TRN-VID-00001/source.mp4",
		"status": "Available",
		"size_bytes": 0,
		"mime_type": None,
	}
	row.update(overrides)
	return row


def _stub_gcs():
	return sys.modules["erpnext_enhancements.training.gcs_media"]


class QueryCompositionTest(unittest.TestCase):
	"""The clause that decides whether this job ever does anything."""

	def setUp(self):
		LAST_QUERY.clear()
		ROWS.clear()

	def test_staleness_is_an_or_filter_not_a_filter(self):
		drive_media._due_for_verification()

		# Only the "has an object at all" test may be an AND clause.
		self.assertEqual(list(LAST_QUERY["filters"]), ["gcs_object"])

		or_filters = LAST_QUERY["or_filters"]
		self.assertEqual([clause[0] for clause in or_filters], ["last_verified_on", "last_verified_on"])
		operators = [clause[1] for clause in or_filters]
		self.assertIn("is", operators)
		self.assertIn("<", operators)

	def test_never_verified_sorts_first_and_the_run_is_bounded(self):
		drive_media._due_for_verification()
		self.assertEqual(LAST_QUERY["order_by"], "last_verified_on asc")
		self.assertEqual(LAST_QUERY["limit"], drive_media.MAX_PER_RUN)


class StatClassificationTest(unittest.TestCase):
	def test_404_is_gone(self):
		meta, verdict = drive_media._stat(_FakeStorage(_Httpish(404)), "b", "o")
		self.assertEqual(verdict, "gone")
		self.assertIsNone(meta)

	def test_403_is_unknown_not_gone(self):
		"""A key that lost its binding cannot see the object; the object is still there."""
		_meta, verdict = drive_media._stat(_FakeStorage(_Httpish(403)), "b", "o")
		self.assertEqual(verdict, "unknown")

	def test_a_bare_exception_is_unknown(self):
		message, verdict = drive_media._stat(_FakeStorage(RuntimeError("connection reset")), "b", "o")
		self.assertEqual(verdict, "unknown")
		self.assertIn("connection reset", message)

	def test_success_returns_metadata(self):
		storage = _FakeStorage({"size": "24298433", "contentType": "video/mp4"})
		meta, verdict = drive_media._stat(storage, "b", "o")
		self.assertEqual(verdict, "ok")
		self.assertEqual(meta["contentType"], "video/mp4")

	def test_no_object_name_is_gone(self):
		self.assertEqual(drive_media._stat(_FakeStorage({}), "b", "")[1], "gone")


class VerifyOneTest(unittest.TestCase):
	def setUp(self):
		WRITES.clear()

	def _updates(self):
		self.assertEqual(len(WRITES), 1, WRITES)
		return WRITES[0][2]

	def test_missing_object_sets_missing_and_names_it(self):
		drive_media._verify_one(_FakeStorage(_Httpish(404)), "bucket", _row())
		updates = self._updates()
		self.assertEqual(updates["status"], "Missing")
		self.assertIn("training/TRN-VID-00001/source.mp4", updates["last_error"])
		self.assertTrue(updates["last_verified_on"])

	def test_unreachable_records_the_error_and_touches_nothing_else(self):
		drive_media._verify_one(_FakeStorage(_Httpish(403)), "bucket", _row())
		self.assertEqual(list(self._updates()), ["last_error"])

	def test_unreachable_does_not_stamp_last_verified_on(self):
		"""So the asset stays at the head of the queue and is retried next run."""
		drive_media._verify_one(_FakeStorage(_Httpish(500)), "bucket", _row())
		self.assertNotIn("last_verified_on", self._updates())

	def test_healthy_object_repairs_size_and_mime(self):
		storage = _FakeStorage({"size": "24298433", "contentType": "video/mp4"})
		drive_media._verify_one(storage, "bucket", _row())
		updates = self._updates()
		self.assertEqual(updates["size_bytes"], 24298433)
		self.assertEqual(updates["mime_type"], "video/mp4")
		self.assertEqual(updates["last_error"], "")
		self.assertTrue(updates["last_verified_on"])

	def test_size_is_compared_as_an_integer_not_a_string(self):
		"""GCS returns size as a string; a string comparison would rewrite for ever."""
		storage = _FakeStorage({"size": "24298433", "contentType": "video/mp4"})
		drive_media._verify_one(storage, "bucket", _row(size_bytes=24298433, mime_type="video/mp4"))
		updates = self._updates()
		self.assertNotIn("size_bytes", updates)
		self.assertNotIn("mime_type", updates)

	def test_a_recovered_object_is_promoted_back_to_available(self):
		storage = _FakeStorage({"size": "10", "contentType": "video/mp4"})
		drive_media._verify_one(storage, "bucket", _row(status="Missing"))
		self.assertEqual(self._updates()["status"], "Available")

	def test_draft_with_an_object_is_promoted_too(self):
		"""Draft means 'no object yet' in the controller's own derivation."""
		storage = _FakeStorage({"size": "10", "contentType": "video/mp4"})
		drive_media._verify_one(storage, "bucket", _row(status="Draft"))
		self.assertEqual(self._updates()["status"], "Available")

	def test_an_already_available_asset_is_not_restated(self):
		storage = _FakeStorage({"size": "10", "contentType": "video/mp4"})
		drive_media._verify_one(storage, "bucket", _row(size_bytes=10, mime_type="video/mp4"))
		self.assertNotIn("status", self._updates())


class SchedulerGuardTest(unittest.TestCase):
	def setUp(self):
		WRITES.clear()
		ROWS.clear()

	def test_unconfigured_install_does_nothing_rather_than_throwing(self):
		gcs_media = _stub_gcs()
		original = gcs_media.is_configured
		gcs_media.is_configured = lambda: False
		try:
			drive_media.verify_video_assets()
		finally:
			gcs_media.is_configured = original
		self.assertEqual(WRITES, [])

	def test_no_storage_client_does_nothing_rather_than_throwing(self):
		gcs_media = _stub_gcs()
		original = gcs_media._storage_service
		gcs_media._storage_service = lambda: None
		try:
			drive_media.verify_video_assets()
		finally:
			gcs_media._storage_service = original
		self.assertEqual(WRITES, [])

	def test_each_due_asset_is_verified(self):
		gcs_media = _stub_gcs()
		original = gcs_media._storage_service
		gcs_media._storage_service = lambda: _FakeStorage({"size": "10", "contentType": "video/mp4"})
		ROWS.extend([_row(name="TRN-VID-00001"), _row(name="TRN-VID-00002")])
		try:
			drive_media.verify_video_assets()
		finally:
			gcs_media._storage_service = original
		self.assertEqual([write[1] for write in WRITES], ["TRN-VID-00001", "TRN-VID-00002"])


if __name__ == "__main__":
	unittest.main()
