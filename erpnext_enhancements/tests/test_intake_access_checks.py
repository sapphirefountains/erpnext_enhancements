"""Access checks on the Document Intake endpoints (v1.835.1).

The real ``accounting_intake`` modules run over a small ``frappe`` stub, so each rule is
exercised rather than grepped for:

- ``intake.ingest_upload`` and ``channels.ingest_mobile_photo`` accept a File only when the
  caller has read permission on it (the File doctype's own rule: their own upload, a public
  file, a file shared with them, or one attached to a record they can read). The check runs
  in the whitelisted wrapper, before the shared ``ingest_document`` reads or stores anything,
  and ``ingest_document`` itself stays check-free for the server-side channels (email, Drive
  watch) that call it in-process.
- ``review.reprocess`` resets only a document that is still being worked up, never one that is
  approved, posting, posted, rejected, a duplicate, or that names the record it created; and it
  is rate limited per user.
- ``review.reject_document`` refuses a document that is posting or posted, or that names the
  record it created.
- ``DocumentIntake.validate`` asks the same File read rule of a person who sets or changes
  ``source_file`` on a save made through the DocType's own write permission, and of nobody
  else: not the channels (they save with ``ignore_permissions``), and not a reviewer whose
  save leaves the file as it was.

``frappe`` is a local stub installed in ``setUpModule`` and removed in ``tearDownModule``, so
this suite has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_intake_access_checks -v
"""

import importlib
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO_ROOT = APP.parent
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

BASE = "erpnext_enhancements.accounting_intake"
CONTROLLER = f"{BASE}.doctype.document_intake.document_intake"
MODULES = (
	"frappe",
	"frappe.utils",
	"frappe.model",
	"frappe.model.document",
	CONTROLLER,
	"erpnext_enhancements.utils.user_rate_limit",
	f"{BASE}.audit",
	f"{BASE}.intake",
	f"{BASE}.channels",
	f"{BASE}.review",
)
_saved = {}
frappe = None
intake = None
channels = None
review = None
controller = None


class PermissionError_(Exception):
	pass


class DoesNotExistError(Exception):
	pass


class RateLimitExceededError(Exception):
	pass


class _Cache:
	def __init__(self):
		self.counts = {}
		self.expiries = {}

	def make_key(self, key):
		return f"site1|{key}"

	def incrby(self, key, amount):
		self.counts[key] = self.counts.get(key, 0) + amount
		return self.counts[key]

	def expire(self, key, seconds):
		self.expiries[key] = seconds

	def ttl(self, key):
		return self.expiries.get(key, -1) if key in self.counts else -2


class _Doc:
	"""A Document Intake stand-in: attribute access, saves and db_set recorded."""

	def __init__(self, **fields):
		self.__dict__.update(
			{"status": "Needs Review", "created_docname": None, "line_items": [], **fields}
		)
		self.saved = 0
		self.db_sets = []

	def save(self, **kwargs):
		self.saved += 1

	def db_set(self, field, value, **kwargs):
		self.db_sets.append((field, value))
		setattr(self, field, value)


def setUpModule():
	global frappe, intake, channels, review, controller
	for name in MODULES:
		_saved[name] = sys.modules.pop(name, None)

	frappe = types.ModuleType("frappe")
	frappe._ = lambda text, *a, **k: text
	frappe.PermissionError = PermissionError_
	frappe.DoesNotExistError = DoesNotExistError
	frappe.RateLimitExceededError = RateLimitExceededError

	def throw(message=None, exc=Exception, *a, **k):
		raise exc(message)

	def whitelist(*args, **kwargs):
		if args and callable(args[0]):
			return args[0]
		return lambda fn: fn

	frappe.throw = throw
	frappe.whitelist = whitelist
	frappe.session = types.SimpleNamespace(user="clerk@example.com")
	frappe.local = types.SimpleNamespace(request=object())
	frappe.cache = _Cache()
	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	utils.now_datetime = lambda: "now"
	frappe.utils = utils
	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = type("Document", (), {})
	model.document = document
	frappe.model = model
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document
	for name in (f"{BASE}.intake", f"{BASE}.channels", f"{BASE}.review", CONTROLLER):
		sys.modules.pop(name, None)
	intake = importlib.import_module(f"{BASE}.intake")
	channels = importlib.import_module(f"{BASE}.channels")
	review = importlib.import_module(f"{BASE}.review")
	controller = importlib.import_module(CONTROLLER)


def tearDownModule():
	for name in MODULES:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


def _reset(roles=("Accounts User",)):
	frappe.cache = _Cache()
	frappe.session.user = "clerk@example.com"
	frappe.local.request = object()
	frappe.get_roles = lambda: list(roles)
	frappe.enqueue = lambda *a, **k: enqueued.append((a, k))
	del enqueued[:]
	frappe.get_all = lambda *a, **k: []
	frappe.has_permission = lambda *a, **k: False
	frappe.get_doc = lambda *a, **k: (_ for _ in ()).throw(AssertionError("get_doc must not run"))


enqueued = []


class TestFileReadCheck(unittest.TestCase):
	"""The upload and mobile endpoints, run for real up to the shared door."""

	def setUp(self):
		_reset()
		self.files = {"/private/files/mine.pdf": ["FILE-1"], "/private/files/theirs.pdf": ["FILE-2"]}
		self.readable = {"FILE-1"}
		self.checked = []
		self.ingested = []

		def get_all(doctype, filters=None, pluck=None, **kw):
			if doctype == "File":
				return list(self.files.get(filters["file_url"], []))
			return []

		def has_permission(doctype, ptype="read", doc=None, **kw):
			self.checked.append((doctype, ptype, doc))
			return doc in self.readable

		frappe.get_all = get_all
		frappe.has_permission = has_permission
		self._door = intake.ingest_document
		intake.ingest_document = lambda **kw: self.ingested.append(kw) or "DI-0001"

	def tearDown(self):
		intake.ingest_document = self._door

	def test_upload_passes_for_a_file_the_caller_can_read(self):
		self.assertEqual(intake.ingest_upload("/private/files/mine.pdf"), {"name": "DI-0001"})
		self.assertEqual(self.checked, [("File", "read", "FILE-1")])
		self.assertEqual(self.ingested[0]["source_channel"], "Upload")

	def test_upload_is_refused_before_anything_is_read(self):
		with self.assertRaises(PermissionError_):
			intake.ingest_upload("/private/files/theirs.pdf")
		self.assertEqual(self.ingested, [])

	def test_upload_of_an_unknown_file_is_refused(self):
		with self.assertRaises(DoesNotExistError):
			intake.ingest_upload("/private/files/none.pdf")
		self.assertEqual(self.ingested, [])

	def test_any_readable_file_row_for_the_address_is_enough(self):
		self.files["/private/files/shared.pdf"] = ["FILE-2", "FILE-1"]
		self.assertEqual(intake.ingest_upload("/private/files/shared.pdf"), {"name": "DI-0001"})

	def test_mobile_passes_for_a_file_the_caller_can_read(self):
		self.assertEqual(channels.ingest_mobile_photo("/private/files/mine.pdf"), {"name": "DI-0001"})
		self.assertEqual(self.ingested[0]["source_channel"], "Mobile")

	def test_mobile_is_refused_before_anything_is_read(self):
		with self.assertRaises(PermissionError_):
			channels.ingest_mobile_photo("/private/files/theirs.pdf")
		self.assertEqual(self.ingested, [])

	def test_the_role_check_still_comes_first(self):
		frappe.get_roles = lambda: ["Guest"]
		for call in (intake.ingest_upload, channels.ingest_mobile_photo):
			with self.assertRaises(PermissionError_):
				call("/private/files/mine.pdf")
		self.assertEqual(self.checked, [])

	def test_the_shared_door_does_no_permission_check(self):
		"""Email, Drive watch and other server code call ``ingest_document`` directly."""
		intake.ingest_document = self._door
		file_doc = types.SimpleNamespace(get_content=lambda: b"%PDF", file_type="pdf")
		created = []

		class _Row:
			name = "DI-0002"

			def __init__(self, values):
				created.append(values)

			def insert(self, **kw):
				return self

		def get_doc(arg, *a, **k):
			return _Row(arg) if isinstance(arg, dict) and arg.get("doctype") == "Document Intake" else file_doc

		frappe.get_doc = get_doc
		frappe.get_cached_doc = lambda name: {"intake_enabled": 0}
		intake.log_intake = lambda *a, **k: None
		self.assertEqual(
			intake.ingest_document(file_url="/private/files/theirs.pdf", source_channel="Email"), "DI-0002"
		)
		self.assertEqual(self.checked, [])
		self.assertEqual(created[0]["source_file"], "/private/files/theirs.pdf")


class TestReprocess(unittest.TestCase):
	def setUp(self):
		_reset(roles=("Accounts Manager",))
		self.doc = _Doc()
		frappe.get_doc = lambda *a, **k: self.doc

	def test_a_document_still_being_worked_up_is_reset(self):
		for status in ("Received", "Extracting", "Needs Item Review", "Needs Review", "Failed"):
			with self.subTest(status=status):
				self.doc = _Doc(status=status)
				del enqueued[:]
				self.assertEqual(review.reprocess("DI-1"), {"status": "Received"})
				self.assertEqual(self.doc.db_sets, [("status", "Received")])
				self.assertEqual(len(enqueued), 1)

	def test_a_document_past_extraction_is_not_reset(self):
		for status in ("Approved", "Posting", "Posted", "Rejected", "Duplicate"):
			with self.subTest(status=status):
				self.doc = _Doc(status=status)
				with self.assertRaises(Exception):
					review.reprocess("DI-1")
				self.assertEqual(self.doc.db_sets, [])
				self.assertEqual(enqueued, [])

	def test_a_document_that_names_its_record_is_not_reset_whatever_its_status(self):
		self.doc = _Doc(status="Failed", created_docname="PINV-0001")
		with self.assertRaises(Exception):
			review.reprocess("DI-1")
		self.assertEqual(self.doc.db_sets, [])
		self.assertEqual(enqueued, [])

	def test_the_role_check_still_applies(self):
		frappe.get_roles = lambda: ["Accounts User"]
		with self.assertRaises(PermissionError_):
			review.reprocess("DI-1")
		self.assertEqual(self.doc.db_sets, [])

	def test_the_per_user_limit_trips(self):
		for _ in range(30):
			review.reprocess("DI-1")
		with self.assertRaises(RateLimitExceededError):
			review.reprocess("DI-1")
		self.assertEqual(len(enqueued), 30)
		frappe.session.user = "other@example.com"
		self.assertEqual(review.reprocess("DI-1"), {"status": "Received"})


class TestRejectDocument(unittest.TestCase):
	def setUp(self):
		_reset(roles=("Accounts Manager",))
		self.doc = _Doc()
		frappe.get_doc = lambda *a, **k: self.doc
		review.log_intake = lambda *a, **k: None

	def test_a_document_in_review_is_rejected(self):
		for status in ("Needs Review", "Approved", "Failed"):
			with self.subTest(status=status):
				self.doc = _Doc(status=status)
				self.assertEqual(review.reject_document("DI-1", reason="wrong file"), {"status": "Rejected"})
				self.assertEqual(self.doc.saved, 1)

	def test_a_posting_or_posted_document_is_refused(self):
		for fields in ({"status": "Posting"}, {"status": "Posted"}, {"status": "Failed", "created_docname": "PINV-1"}):
			with self.subTest(fields=fields):
				self.doc = _Doc(**fields)
				with self.assertRaises(Exception):
					review.reject_document("DI-1")
				self.assertEqual(self.doc.saved, 0)
				self.assertNotEqual(self.doc.status, "Rejected")


class TestSourceFileIsSetByAReader(unittest.TestCase):
	"""``DocumentIntake._guard_source_file``, run on the real controller."""

	def setUp(self):
		_reset()
		self.files = {"/private/files/mine.pdf": ["FILE-1"], "/private/files/theirs.pdf": ["FILE-2"]}
		self.readable = {"FILE-1"}
		self.checked = []

		def get_all(doctype, filters=None, pluck=None, **kw):
			return list(self.files.get(filters["file_url"], [])) if doctype == "File" else []

		def has_permission(doctype, ptype="read", doc=None, **kw):
			self.checked.append(doc)
			return doc in self.readable

		frappe.get_all = get_all
		frappe.has_permission = has_permission

	def _doc(self, source_file, stored=None, new=False, ignore_permissions=False):
		doc = controller.DocumentIntake()
		doc.source_file = source_file
		doc.flags = types.SimpleNamespace(ignore_permissions=ignore_permissions)
		doc.is_new = lambda: new
		before = None if stored is None else types.SimpleNamespace(source_file=stored)
		doc.get_doc_before_save = lambda: before
		return doc

	def test_a_new_document_takes_a_file_its_creator_can_read(self):
		self._doc("/private/files/mine.pdf", new=True)._guard_source_file()
		self.assertEqual(self.checked, ["FILE-1"])

	def test_a_new_document_is_refused_a_file_its_creator_cannot_read(self):
		with self.assertRaises(PermissionError_):
			self._doc("/private/files/theirs.pdf", new=True)._guard_source_file()

	def test_changing_the_file_asks_the_same_rule(self):
		with self.assertRaises(PermissionError_):
			self._doc("/private/files/theirs.pdf", stored="/private/files/mine.pdf")._guard_source_file()
		self._doc("/private/files/mine.pdf", stored="/private/files/theirs.pdf")._guard_source_file()

	def test_an_unknown_file_is_refused(self):
		with self.assertRaises(DoesNotExistError):
			self._doc("/private/files/nowhere.pdf", new=True)._guard_source_file()

	def test_a_save_that_leaves_the_file_alone_is_not_asked(self):
		"""A reviewer works a document whoever sent it in."""
		self._doc("/private/files/theirs.pdf", stored="/private/files/theirs.pdf")._guard_source_file()
		self.assertEqual(self.checked, [])

	def test_the_channels_are_not_asked(self):
		"""Email, the Drive watch and the scheduler save with ignore_permissions."""
		self._doc("/private/files/theirs.pdf", new=True, ignore_permissions=True)._guard_source_file()
		self._doc(
			"/private/files/theirs.pdf", stored="/private/files/mine.pdf", ignore_permissions=True
		)._guard_source_file()
		self.assertEqual(self.checked, [])

	def test_validate_runs_the_rule(self):
		doc = self._doc("/private/files/theirs.pdf", new=True)
		doc.document_type, doc.party_name_text, doc.document_number = "Bill", None, None
		with self.assertRaises(PermissionError_):
			doc.validate()


if __name__ == "__main__":
	unittest.main()
