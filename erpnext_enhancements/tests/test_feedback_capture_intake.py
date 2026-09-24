"""Bench-free tests for the intake half of WI-079 slice 2: ``file_request``, ``submit_capture``
and the per-user filing limit (ADR 0016 §1).

What these pin, and why each one matters:

- **One way in.** ``submit_request`` and ``submit_capture`` both go through ``file_request``. So
  the rate limit, validation and provenance stamps exist once, and a new source cannot forget
  one of them.
- **Provenance is never client input.** ``source``, ``source_doctype``, ``source_ref``,
  ``context_release``, ``requested_by`` and ``status`` sent by a browser are refused and
  reported, and the stored values are the server's.
- **Ten a minute, per person.** The eleventh filing in a window is a 429, whichever endpoint it
  came through. That is acceptance criterion "the eleventh submission in a minute from one user
  is refused".
- **System Users only** for the capture endpoint. The ``system_user`` cookie is a hint, and this
  is the check.
- **The snapshot is a private File, never a field.** Triton's bulk sync reads the request's
  fields.
- **Refuse before writing** an oversized or malformed snapshot. **Never lose a report** because
  the snapshot could not be saved.
- **A resend is not a second report.** A draft of a report whose response was lost carries the
  report's id; the id is remembered only after the filing commits, and only for that user.
- **A pause is recognizable** (``FeedbackPausedError``), so the panel keeps a saved draft.

``frappe`` is a local stub installed in ``setUpModule`` and removed in ``tearDownModule``, so
this suite gets its own CI step (CLAUDE.md: stub-installing suites are kept apart).

Run: python -m unittest erpnext_enhancements.tests.test_feedback_capture_intake -v
"""

import json
import re
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

MODULES = (
	"frappe",
	"frappe.utils",
	"frappe.model",
	"frappe.model.document",
	"frappe.permissions",
)
_saved = {}
feedback = None
STATE = None


class _Throw(Exception):
	pass


class ValidationError(_Throw):
	pass


class PermissionError_(_Throw):
	pass


class RateLimitExceededError(ValidationError):
	pass


class State:
	def __init__(self):
		self.user = "nik@example.com"
		self.system_users = {"nik@example.com", "Administrator"}
		self.counters = {}
		self.docs = []
		self.files = []
		self.file_insert_fails = False
		self.notified = []
		self.paused = False
		self.cache = {}
		self.after_commit = []


class FakeRequest:
	def __init__(self, n):
		self.name = f"ER-2026-{n:05d}"
		self.inserted = False

	def update(self, values):
		for k, v in values.items():
			setattr(self, k, v)

	def get(self, key):
		return getattr(self, key, None)

	def insert(self, ignore_permissions=False):
		self.inserted = True
		STATE.docs.append(self)
		return self


class FakeFile:
	def __init__(self, values):
		self.values = dict(values)

	def insert(self, ignore_permissions=False):
		if STATE.file_insert_fails:
			raise RuntimeError("extension not allowed")
		STATE.files.append(self.values)
		return self


def _install():
	frappe = types.ModuleType("frappe")
	frappe._ = lambda s, *a, **k: s
	frappe.whitelist = lambda *a, **k: (lambda f: f)
	frappe.ValidationError = ValidationError
	frappe.PermissionError = PermissionError_
	frappe.RateLimitExceededError = RateLimitExceededError

	def throw(message, exc=ValidationError, *a, **k):
		raise exc(message)

	frappe.throw = throw
	class Session:
		@property
		def user(self):
			return STATE.user

	frappe.session = Session()
	frappe.get_roles = lambda *a: []
	frappe.log_error = lambda *a, **k: None

	class Cache:
		def make_key(self, key, *a, **k):
			return f"site1|{key}"

		def incrby(self, key, n):
			STATE.counters[key] = STATE.counters.get(key, 0) + n
			return STATE.counters[key]

		def expire(self, key, ttl):
			pass

		# Like RedisWrapper, these add the site prefix themselves.
		def get_value(self, key, *a, **k):
			return STATE.cache.get(f"site1|{key}")

		def set_value(self, key, val, *a, expires_in_sec=None, **k):
			STATE.cache[f"site1|{key}"] = val
			STATE.cache_ttl = expires_in_sec

	frappe.cache = Cache()
	frappe.new_doc = lambda doctype: FakeRequest(len(STATE.docs) + 1)
	frappe.get_doc = lambda values: FakeFile(values)
	def exists(doctype, filters):
		return any(
			d.name == filters.get("name")
			and d.requested_by == filters.get("requested_by")
			and d.source == filters.get("source")
			for d in STATE.docs
		)

	class AfterCommit:
		def add(self, fn):
			STATE.after_commit.append(fn)

	frappe.db = types.SimpleNamespace(
		get_value=lambda *a, **k: None,
		set_value=lambda *a, **k: None,
		exists=exists,
		after_commit=AfterCommit(),
	)

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	utils.flt = lambda v: float(v or 0)
	utils.now_datetime = lambda: datetime(2026, 9, 23, 20, 0, 0)
	utils.strip_html = lambda s: re.sub(r"<[^>]+>", "", s or "")
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = type("Document", (), {})
	model.document = document
	frappe.model = model

	permissions = types.ModuleType("frappe.permissions")
	permissions.is_system_user = lambda user=None: user in STATE.system_users
	frappe.permissions = permissions

	for name in MODULES:
		_saved[name] = sys.modules.get(name)
	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.utils": utils,
			"frappe.model": model,
			"frappe.model.document": document,
			"frappe.permissions": permissions,
		}
	)


def setUpModule():
	global feedback
	_install()
	for name in list(sys.modules):
		if name.startswith("erpnext_enhancements.api.feedback") or name.startswith(
			"erpnext_enhancements.product_feedback.doctype.product_feedback_settings"
		):
			sys.modules.pop(name, None)
	from erpnext_enhancements.api import feedback as module

	feedback = module


def tearDownModule():
	for name in MODULES:
		if _saved.get(name) is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = _saved[name]
	sys.modules.pop("erpnext_enhancements.api.feedback", None)


VALID = {
	"title": "Save button does nothing on the Item form",
	"request_type": "Bug",
	"impact": "Blocking my work",
	"description": "Clicking Save on an Item shows nothing and the change is lost.",
	"context_url": "/desk/item/PUMP-001",
	"context_doctype": "Item",
	"context_docname": "PUMP-001",
}


class Base(unittest.TestCase):
	def setUp(self):
		global STATE
		STATE = State()
		self._orig = (feedback.get_settings, feedback._notify)
		feedback.get_settings = lambda: {"paused": STATE.paused}
		feedback._notify = lambda event, *a: STATE.notified.append((event, a))

	def tearDown(self):
		feedback.get_settings, feedback._notify = self._orig


class TestOneWayIn(Base):
	def test_the_feedback_form_files_through_file_request(self):
		out = feedback.submit_request(payload=dict(VALID))
		(doc,) = STATE.docs
		self.assertEqual(out["name"], doc.name)
		self.assertEqual(doc.source, "Feedback form")
		self.assertEqual(doc.requested_by, "nik@example.com")
		self.assertEqual(doc.status, "Submitted")
		self.assertTrue(doc.context_release)
		self.assertIsNone(doc.source_doctype)
		self.assertIsNone(doc.source_ref)
		self.assertEqual(STATE.notified, [("request_submitted", (doc.name,))])

	def test_capture_files_through_file_request_with_its_own_source(self):
		feedback.submit_capture(payload=dict(VALID), context={"schema": 1})
		(doc,) = STATE.docs
		self.assertEqual(doc.source, "Capture")
		self.assertEqual(doc.status, "Submitted")

	def test_provenance_sent_by_a_browser_is_refused_and_reported(self):
		sneaky = dict(
			VALID,
			source="Design Review",
			source_doctype="Design Decision",
			source_ref="DD-1",
			context_release="9.9.9",
			requested_by="someone@example.com",
			status="Approved",
		)
		out = feedback.submit_request(payload=sneaky)
		(doc,) = STATE.docs
		for key in ("source", "source_doctype", "source_ref", "context_release", "requested_by", "status"):
			self.assertIn(key, out["rejected"])
		self.assertEqual(doc.source, "Feedback form")
		self.assertEqual(doc.requested_by, "nik@example.com")
		self.assertEqual(doc.status, "Submitted")
		self.assertNotEqual(doc.context_release, "9.9.9")

	def test_a_paused_intake_refuses_capture_too(self):
		STATE.paused = True
		with self.assertRaises(ValidationError) as caught:
			feedback.submit_capture(payload=dict(VALID), context={})
		self.assertEqual(STATE.docs, [])
		# The class name reaches the browser as exc_type; the panel keeps a draft on it.
		self.assertEqual(type(caught.exception).__name__, "FeedbackPausedError")

	def test_capture_needs_an_impact_like_the_form(self):
		payload = dict(VALID)
		payload.pop("impact")
		with self.assertRaises(ValidationError):
			feedback.submit_capture(payload=payload, context={})


class TestFilingLimit(Base):
	def test_the_eleventh_filing_in_a_window_is_a_429(self):
		for _ in range(10):
			feedback.submit_request(payload=dict(VALID))
		with self.assertRaises(RateLimitExceededError):
			feedback.submit_request(payload=dict(VALID))
		self.assertEqual(len(STATE.docs), 10)

	def test_the_limit_is_shared_across_every_way_in(self):
		for _ in range(6):
			feedback.submit_request(payload=dict(VALID))
		for _ in range(4):
			feedback.submit_capture(payload=dict(VALID), context={})
		with self.assertRaises(RateLimitExceededError):
			feedback.submit_capture(payload=dict(VALID), context={})

	def test_the_limit_is_per_person(self):
		for _ in range(10):
			feedback.submit_request(payload=dict(VALID))
		STATE.user = "jo@example.com"
		STATE.system_users.add("jo@example.com")
		feedback.submit_request(payload=dict(VALID))
		self.assertEqual(STATE.docs[-1].requested_by, "jo@example.com")

	def test_the_counter_is_site_prefixed(self):
		feedback.submit_request(payload=dict(VALID))
		(key,) = STATE.counters
		self.assertTrue(key.startswith("site1|"), key)


class TestCaptureGate(Base):
	def test_a_website_user_cannot_file_a_capture(self):
		STATE.user = "customer@example.com"
		with self.assertRaises(PermissionError_):
			feedback.submit_capture(payload=dict(VALID), context={})
		self.assertEqual(STATE.docs, [])

	def test_a_guest_cannot_file_a_capture(self):
		STATE.user = "Guest"
		with self.assertRaises(PermissionError_):
			feedback.submit_capture(payload=dict(VALID), context={})


class TestCaptureContext(Base):
	SNAPSHOT = {
		"schema": 1,
		"surface": "desk",
		"page": {"path": "/desk/item/PUMP-001", "form": {"doctype": "Item", "unsaved": True}},
		"requests": [{"method": "POST", "path": "/api/method/frappe.desk.form.save.savedocs", "status": 500}],
	}

	def test_the_snapshot_is_a_private_file_on_the_request(self):
		out = feedback.submit_capture(payload=dict(VALID), context=self.SNAPSHOT)
		(f,) = STATE.files
		self.assertEqual(f["doctype"], "File")
		self.assertEqual(f["is_private"], 1)
		self.assertEqual(f["attached_to_doctype"], "Enhancement Request")
		self.assertEqual(f["attached_to_name"], out["name"])
		self.assertTrue(f["file_name"].startswith("capture-context-") and f["file_name"].endswith(".json"))
		self.assertEqual(json.loads(f["content"]), self.SNAPSHOT)
		doc = STATE.docs[0]
		self.assertFalse(hasattr(doc, "context"), "the snapshot must never become a field")

	def test_a_json_string_snapshot_is_accepted(self):
		feedback.submit_capture(payload=dict(VALID), context=json.dumps(self.SNAPSHOT))
		self.assertEqual(json.loads(STATE.files[0]["content"]), self.SNAPSHOT)

	def test_an_oversized_snapshot_is_refused_before_anything_is_written(self):
		big = {"console": ["x" * 1000] * 250}
		with self.assertRaises(ValidationError):
			feedback.submit_capture(payload=dict(VALID), context=big)
		self.assertEqual((STATE.docs, STATE.files), ([], []))

	def test_a_malformed_snapshot_is_refused(self):
		for bad in ("{not json", json.dumps([1, 2]), 42):
			with self.subTest(bad=bad):
				with self.assertRaises(ValidationError):
					feedback.submit_capture(payload=dict(VALID), context=bad)
		self.assertEqual(STATE.docs, [])

	def test_a_snapshot_that_cannot_be_saved_never_loses_the_report(self):
		STATE.file_insert_fails = True
		out = feedback.submit_capture(payload=dict(VALID), context=self.SNAPSHOT)
		self.assertEqual(len(STATE.docs), 1)
		self.assertTrue(any("technical details" in r for r in out["rejected"]))


class TestResendIsNotASecondReport(Base):
	ID = "0f8e2c1a-7b3d-4e5f-9a6b-1c2d3e4f5a6b"

	def _commit(self):
		for fn in STATE.after_commit:
			fn()
		STATE.after_commit = []

	def test_a_resend_after_commit_returns_the_first_request(self):
		first = feedback.submit_capture(payload=dict(VALID), context={}, client_id=self.ID)
		self._commit()
		again = feedback.submit_capture(payload=dict(VALID), context={}, client_id=self.ID)
		self.assertEqual(len(STATE.docs), 1)
		self.assertEqual(again["name"], first["name"])
		self.assertTrue(again["duplicate"])
		self.assertEqual(STATE.cache_ttl, feedback.CLIENT_ID_TTL_SECONDS)
		self.assertGreaterEqual(feedback.CLIENT_ID_TTL_SECONDS, 7 * 24 * 3600)

	def test_nothing_is_remembered_before_the_filing_commits(self):
		feedback.submit_capture(payload=dict(VALID), context={}, client_id=self.ID)
		self.assertEqual(STATE.cache, {})

	def test_a_remembered_name_that_is_not_in_the_table_files_again(self):
		# Committed and remembered, then gone (say, deleted): not proof that this report arrived.
		feedback.submit_capture(payload=dict(VALID), context={}, client_id=self.ID)
		self._commit()
		STATE.docs.clear()
		feedback.submit_capture(payload=dict(VALID), context={}, client_id=self.ID)
		self.assertEqual(len(STATE.docs), 1)

	def test_the_id_is_per_person(self):
		feedback.submit_capture(payload=dict(VALID), context={}, client_id=self.ID)
		self._commit()
		STATE.user = "jo@example.com"
		STATE.system_users.add("jo@example.com")
		out = feedback.submit_capture(payload=dict(VALID), context={}, client_id=self.ID)
		self.assertEqual(len(STATE.docs), 2)
		self.assertNotIn("duplicate", out)

	def test_a_malformed_id_is_ignored(self):
		for bad in ("short", "x" * 65, "<script>alert(1)</script>", "id with spaces here", 42):
			with self.subTest(bad=bad):
				feedback.submit_capture(payload=dict(VALID), context={}, client_id=bad)
				self._commit()
		self.assertEqual((len(STATE.docs), STATE.cache), (5, {}))

	def test_a_resend_does_not_count_toward_the_limit(self):
		feedback.submit_capture(payload=dict(VALID), context={}, client_id=self.ID)
		self._commit()
		for _ in range(15):
			feedback.submit_capture(payload=dict(VALID), context={}, client_id=self.ID)
		self.assertEqual(sum(STATE.counters.values()), 1)

	def test_a_paused_intake_still_answers_a_resend_of_a_filed_report(self):
		first = feedback.submit_capture(payload=dict(VALID), context={}, client_id=self.ID)
		self._commit()
		STATE.paused = True
		self.assertEqual(feedback.submit_capture(payload=dict(VALID), context={}, client_id=self.ID)["name"], first["name"])


if __name__ == "__main__":
	unittest.main()
