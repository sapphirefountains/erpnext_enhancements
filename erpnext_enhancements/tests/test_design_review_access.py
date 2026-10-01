"""Bench-free tests for who may do what in a design review (WI-079 slice 5, ADR 0016 §2).

The acceptance criteria these pin:

- **No Desk access, no review.** A Website User is refused every read and write.
- **Not on the list, no vote, verdict or note** — for a System User, and for a System Manager
  who moderates but is not a participant.
- **Only while Open.** A participant is refused on a Draft or Closed review.
- **The session user, always.** A vote, verdict or note is stamped with ``frappe.session.user``;
  the endpoints accept no author or voter argument at all.
- **``triton@`` cannot promote**, nor ``Administrator``, though both hold System Manager. A human
  System Manager's promotion files through ``api.feedback.file_request`` with
  ``approve=True``, ``source = Design Review`` and the decision as ``source_ref``.
- **Content arrives only by import, and as a File.** A review's screens are one private JSON File
  written by ``importer.import_bundle``; the seed below imports a real bundle through it, so the
  service is tested against content the importer actually wrote. A revision may add parts and
  may never renumber one, and the example bundle in ``design_review/examples`` must import.
- **The AI tools refuse what a person would be refused.** ``triton@`` cannot import, an inline
  bundle has a size cap, and a bundle File must be private.

``frappe`` is a local in-memory stub installed in ``setUpModule`` and removed in
``tearDownModule``, so this suite gets its own CI step (CLAUDE.md: stub-installing suites are
kept apart).

Run: python -m unittest erpnext_enhancements.tests.test_design_review_access -v
"""

import importlib
import json
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

STUBBED = (
	"frappe",
	"frappe.utils",
	"frappe.permissions",
	"erpnext_enhancements.api.feedback",
	"erpnext_enhancements.design_review.service",
	"erpnext_enhancements.design_review.content",
	"erpnext_enhancements.design_review.importer",
	"erpnext_enhancements.design_review.ai_tools",
)
_saved = {}
service = None
DB = None


class ValidationError(Exception):
	pass


class PermissionError_(Exception):
	pass


class DoesNotExistError(Exception):
	pass


class Row(dict):
	__getattr__ = dict.get


class Flags(dict):
	"""``frappe._dict``: attribute reads and writes are its keys."""

	__getattr__ = dict.get
	__setattr__ = dict.__setitem__


def _match(row, filters):
	for key, want in (filters or {}).items():
		have = row.get(key)
		if isinstance(want, (list, tuple)) and len(want) == 2 and want[0] == "in":
			if have not in want[1]:
				return False
		elif have != want:
			return False
	return True


class FakeDB:
	def __init__(self):
		self.tables = {}
		self.user = "pat@sapphirefountains.com"
		self.roles = {}
		self.system_users = set()
		self.filed = []
		self.seq = 0

	def rows(self, doctype):
		return self.tables.setdefault(doctype, [])

	def add(self, doctype, **values):
		self.seq += 1
		row = Row(values)
		row.setdefault("name", f"{doctype[:3].upper()}-{self.seq}")
		self.rows(doctype).append(row)
		return row


class FakeDoc:
	def __init__(self, doctype, values=None):
		self.doctype = doctype
		self._row = Row(values or {})
		self._new = "name" not in self._row
		self.flags = Flags()

	def get_content(self):
		return self._row.get("content")

	def update(self, values):
		self._row.update(values)

	def get(self, key, default=None):
		return self._row.get(key, default)

	def __getattr__(self, key):
		if key.startswith("_"):
			raise AttributeError(key)
		return self._row.get(key)

	def __setattr__(self, key, value):
		if key in ("doctype", "_row", "_new", "flags"):
			object.__setattr__(self, key, value)
		else:
			self._row[key] = value

	def is_new(self):
		return self._new

	def insert(self, ignore_permissions=False):
		row = DB.add(self.doctype, **self._row)
		self._row = row
		self._new = False
		return self

	def save(self, ignore_permissions=False):
		return self


def _install():
	frappe = types.ModuleType("frappe")
	frappe._ = lambda s, *a, **k: s
	frappe.whitelist = lambda *a, **k: (lambda f: f)
	frappe.ValidationError = ValidationError
	frappe.PermissionError = PermissionError_
	frappe.DoesNotExistError = DoesNotExistError

	def throw(message, exc=ValidationError, *a, **k):
		raise exc(message)

	frappe.throw = throw

	class Session:
		@property
		def user(self):
			return DB.user

	frappe.session = Session()
	frappe.get_roles = lambda user=None: list(DB.roles.get(user or DB.user, []))

	def get_all(
		doctype, filters=None, fields=None, pluck=None, order_by=None, limit_page_length=None, limit=None
	):
		rows = [r for r in DB.rows(doctype) if _match(r, filters)]
		if limit:
			rows = rows[:limit]
		if pluck:
			return [r.get(pluck) for r in rows]
		return [Row(r) for r in rows]

	frappe.get_all = get_all

	db = types.SimpleNamespace()

	def exists(doctype, filters=None):
		if isinstance(filters, str):
			return any(r.get("name") == filters for r in DB.rows(doctype))
		return any(_match(r, filters) for r in DB.rows(doctype))

	def get_value(doctype, filters, fieldname=None, as_dict=False, **k):
		if isinstance(filters, str):
			filters = {"name": filters}
		for r in DB.rows(doctype):
			if _match(r, filters):
				if as_dict:
					return Row({f: r.get(f) for f in fieldname})
				if isinstance(fieldname, (list, tuple)):
					return tuple(r.get(f) for f in fieldname)
				return r.get(fieldname)
		return None

	db.exists = exists
	db.get_value = get_value
	db.set_value = lambda doctype, name, field, value, **k: [
		r.update({field: value}) for r in DB.rows(doctype) if r.get("name") == name
	]
	db.escape = lambda v: "'" + str(v).replace("'", "''") + "'"
	frappe.db = db
	frappe.has_permission = lambda *a, **k: True

	class Cache:
		def __init__(self):
			self.store = {}

		def get_value(self, key):
			return self.store.get(key)

		def set_value(self, key, value, expires_in_sec=None):
			self.store[key] = value

	frappe.cache = Cache()
	frappe.new_doc = lambda doctype: FakeDoc(doctype)

	def get_doc(doctype, name=None):
		if isinstance(doctype, dict):
			return FakeDoc(doctype["doctype"], {k: v for k, v in doctype.items() if k != "doctype"})
		for r in DB.rows(doctype):
			if r.get("name") == name:
				doc = FakeDoc(doctype)
				doc._row = r
				doc._new = False
				return doc
		raise DoesNotExistError(name)

	frappe.get_doc = get_doc
	frappe.delete_doc = lambda doctype, name, **k: DB.tables.__setitem__(
		doctype, [r for r in DB.rows(doctype) if r.get("name") != name]
	)

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0) if str(v or 0).lstrip("-").isdigit() else 0
	utils.now_datetime = lambda: datetime(2026, 10, 1, 12, 0, 0)
	frappe.utils = utils

	perms = types.ModuleType("frappe.permissions")
	perms.is_system_user = lambda user=None: (user or DB.user) in DB.system_users
	frappe.permissions = perms

	feedback = types.ModuleType("erpnext_enhancements.api.feedback")
	feedback.SOURCE_DESIGN_REVIEW = "Design Review"

	def file_request(values, requested_by, source, **kwargs):
		DB.filed.append({"values": values, "requested_by": requested_by, "source": source, **kwargs})
		return types.SimpleNamespace(name="ER-2026-00099")

	feedback.file_request = file_request

	for name in STUBBED:
		_saved[name] = sys.modules.get(name)
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.permissions"] = perms
	sys.modules["erpnext_enhancements.api.feedback"] = feedback
	import erpnext_enhancements.api as api_pkg

	api_pkg.feedback = feedback
	for name in STUBBED[4:]:
		sys.modules.pop(name, None)
	return importlib.import_module("erpnext_enhancements.design_review.service")


def setUpModule():
	global service
	service = _install()


def tearDownModule():
	for name, mod in _saved.items():
		if mod is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = mod
	import erpnext_enhancements.api as api_pkg

	if hasattr(api_pkg, "feedback") and _saved.get("erpnext_enhancements.api.feedback") is None:
		delattr(api_pkg, "feedback")


PAT = "pat@sapphirefountains.com"  # participant
OUTSIDER = "sam@sapphirefountains.com"  # System User, not on the list
NIK = "nik@sapphirefountains.com"  # System Manager, not on the list
TRITON = "triton@sapphirefountains.com"
CUSTOMER = "buyer@example.com"  # Website User


def _content_bundle():
	"""One track, three options, S04 drawn in each, with the parts the tests name."""
	return {
		"format": "sapphire-design-review/1",
		"title": "Training",
		"kit": "sapphire-ux/1",
		"tracks": [{"id": "learner", "label": "Learner", "votable": True}],
		"options": [{"track": "learner", "code": code, "name": code} for code in ("L1", "L2", "L3")],
		"screens": [
			{
				"track": "learner",
				"option": code,
				"screen": "S04",
				"frame": "phone",
				"w": 390,
				"h": 844,
				"html": '<div class="ux-app" data-c="Video block">'
				'<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></div>',
			}
			for code in ("L1", "L2", "L3")
		],
		"parts": {"learner:S04": [[1, "Top bar"], [5, "Video block"]]},
	}


def importer():
	return importlib.import_module("erpnext_enhancements.design_review.importer")


def _seed(status="Open"):
	global DB
	DB = FakeDB()
	DB.system_users = {PAT, OUTSIDER, NIK, TRITON, "Administrator"}
	DB.roles = {
		PAT: ["Desk User"],
		OUTSIDER: ["Desk User"],
		NIK: ["Desk User", "System Manager"],
		TRITON: ["Desk User", "System Manager"],
		"Administrator": ["System Manager"],
		CUSTOMER: [],
	}
	sys.modules["frappe"].cache.store.clear()
	DB.add("Design Review", name="DR-2026-001", title="Training", status=status)
	DB.user = "Administrator"
	importer().import_bundle(_content_bundle(), review="DR-2026-001")
	DB.user = PAT
	DB.add(
		"Design Review Participant",
		parent="DR-2026-001",
		parenttype="Design Review",
		parentfield="participants",
		user=PAT,
	)
	DB.rows("Design Review")[0]["participants"] = [Row(user=PAT)]


def _review_doc(name="DR-2026-001"):
	return sys.modules["frappe"].get_doc("Design Review", name)


class TestImportedContent(unittest.TestCase):
	"""What the import wrote is what the Review Room reads, unchanged by any second sanitizer."""

	def setUp(self):
		_seed()

	def test_content_is_one_private_file_attached_to_the_review(self):
		review = DB.rows("Design Review")[0]
		files = DB.rows("File")
		self.assertEqual(len(files), 1)
		self.assertEqual((files[0]["is_private"], files[0]["attached_to_name"]), (1, "DR-2026-001"))
		self.assertEqual(review["content_file"], files[0]["name"])
		self.assertEqual(review["revision"], 1)

	def test_svg_geometry_survives(self):
		data = service.get_content("DR-2026-001")
		self.assertIn('d="M8 5v14l11-7z"', data["screens"][0]["html"])
		self.assertIn('viewBox="0 0 24 24"', data["screens"][0]["html"])
		self.assertIn(".ux-app", data["kit_css"])

	def test_a_revision_replaces_the_file_and_keeps_activity(self):
		service.add_note("DR-2026-001", "L3-S04-E05", "Make the video bigger")
		bundle = _content_bundle()
		bundle["parts"] = {"learner:S04": [[6, "Caption"]]}
		DB.user = "Administrator"
		report = importer().import_bundle(bundle, review="DR-2026-001")
		self.assertEqual((report["revision"], report["parts_added"]), (2, 1))
		self.assertEqual(len(DB.rows("File")), 1)
		self.assertEqual(len(DB.rows("Design Note")), 1)
		parts = importer().content.load("DR-2026-001")["parts"]["learner:S04"]
		self.assertEqual([n for n, _p in parts], [1, 5, 6])

	def test_a_revision_cannot_rename_a_part(self):
		bundle = _content_bundle()
		bundle["parts"] = {"learner:S04": [[5, "Hero video"]]}
		with self.assertRaises(ValidationError):
			importer().import_bundle(bundle, review="DR-2026-001")
		self.assertEqual(DB.rows("Design Review")[0]["revision"], 1)

	def test_get_review_names_no_note_author(self):
		service.add_note("DR-2026-001", "L3-S04-E05", "Make the video bigger")
		state = service.get_review("DR-2026-001")
		self.assertTrue(state["has_content"])
		self.assertEqual((state["me"]["participant"], state["me"]["moderator"]), (True, False))
		note = state["notes"][0]
		self.assertNotIn("author", note)
		self.assertTrue(note["mine"])

	def test_the_desk_form_cannot_open_an_empty_review(self):
		doc = _review_doc()
		doc._row = Row(dict(doc._row, status="Open", participants=[]))
		with self.assertRaises(ValidationError):
			service.validate_review(doc)
		doc._row = Row(dict(doc._row, status="Open", participants=[Row(user=PAT)], content_file=None))
		with self.assertRaises(ValidationError):
			service.validate_review(doc)

	def test_content_fields_change_only_by_import(self):
		doc = _review_doc()
		before = FakeDoc("Design Review", dict(doc._row))
		doc._row = Row(dict(doc._row, content_hash="forged", participants=[Row(user=PAT)]))
		doc.get_doc_before_save = lambda: before
		with self.assertRaises(ValidationError) as caught:
			service.validate_review(doc)
		self.assertIn("content_hash", str(caught.exception))

	def test_the_example_bundle_imports(self):
		example = REPO_ROOT / "erpnext_enhancements" / "design_review" / "examples" / "minimal-bundle.json"
		bundle = importer().parse_bundle_text(example.read_text(encoding="utf-8"))
		DB.user = "Administrator"
		check = importer().check_bundle(bundle)
		self.assertEqual((check["ok"], check["sanitizer_dropped"]), (True, {}))
		report = importer().import_bundle(bundle)
		self.assertEqual(report["screens"], 4)


class TestStatus(unittest.TestCase):
	def setUp(self):
		_seed("Draft")

	def test_only_a_moderator_sets_status(self):
		with self.assertRaises(PermissionError_):
			service.set_status("DR-2026-001", "Open")

	def test_open_needs_content_and_participants(self):
		DB.user = NIK
		DB.add("Design Review", name="DR-2026-002", title="Empty", status="Draft", participants=[Row(user=PAT)])
		with self.assertRaises(ValidationError):
			service.set_status("DR-2026-002", "Open")
		_review_doc()._row["participants"] = []
		with self.assertRaises(ValidationError):
			service.set_status("DR-2026-001", "Open")
		_review_doc()._row["participants"] = [Row(user=PAT)]
		self.assertEqual(service.set_status("DR-2026-001", "Open"), {"status": "Open"})


class TestAiTools(unittest.TestCase):
	def setUp(self):
		_seed("Draft")
		self.ai = importlib.import_module("erpnext_enhancements.design_review.ai_tools")

	def test_service_accounts_and_non_managers_cannot_import(self):
		for user in (TRITON, "Administrator", PAT):
			DB.user = user
			self.assertTrue(self.ai.precheck({"bundle_json": json.dumps(_content_bundle())}))
			with self.assertRaises(PermissionError_):
				self.ai.submit({"bundle_json": json.dumps(_content_bundle())})

	def test_a_person_imports_and_gets_a_link(self):
		DB.user = NIK
		self.assertEqual(self.ai.precheck({"bundle_json": json.dumps(_content_bundle())}), [])
		report = self.ai.submit({"bundle_json": json.dumps(_content_bundle()), "review": "DR-2026-001"})
		self.assertEqual((report["revision"], report["link"]), (2, "/review/DR-2026-001"))

	def test_the_bundle_comes_from_exactly_one_place(self):
		DB.user = NIK
		self.assertFalse(self.ai.check({})["ok"])
		self.assertFalse(self.ai.check({"bundle_json": "{}", "file_name": "x"})["ok"])
		self.assertIn("MB", self.ai.check({"bundle_json": " " * (self.ai.MAX_INLINE_BYTES + 1)})["error"])

	def test_a_bundle_file_must_be_private(self):
		DB.user = NIK
		DB.add("File", name="pub-1", is_private=0, is_folder=0, content=json.dumps(_content_bundle()))
		DB.add("File", name="priv-1", is_private=1, is_folder=0, content=json.dumps(_content_bundle()))
		self.assertIn("private", self.ai.check({"file_name": "pub-1"})["error"])
		self.assertTrue(self.ai.check({"file_name": "priv-1"})["ok"])


class TestReadGate(unittest.TestCase):
	def setUp(self):
		_seed()

	def test_website_user_cannot_read(self):
		DB.user = CUSTOMER
		with self.assertRaises(PermissionError_):
			service.require_reader("DR-2026-001")

	def test_outsider_cannot_read_and_cannot_probe(self):
		DB.user = OUTSIDER
		with self.assertRaises(DoesNotExistError) as a:
			service.require_reader("DR-2026-001")
		with self.assertRaises(DoesNotExistError) as b:
			service.require_reader("DR-2026-999")
		self.assertEqual(str(a.exception).replace("001", "x"), str(b.exception).replace("999", "x"))

	def test_participant_and_moderator_can_read(self):
		DB.user = PAT
		self.assertEqual(service.require_reader("DR-2026-001"), PAT)
		DB.user = NIK
		self.assertEqual(service.require_reader("DR-2026-001"), NIK)


class TestParticipantWrites(unittest.TestCase):
	def setUp(self):
		_seed()

	def test_outsider_and_non_participant_moderator_cannot_vote_or_note(self):
		for user in (OUTSIDER, NIK, CUSTOMER):
			DB.user = user
			with self.assertRaises((PermissionError_, DoesNotExistError)):
				service.cast_vote("DR-2026-001", "learner", ["L1", "L2", "L3"])
			with self.assertRaises((PermissionError_, DoesNotExistError)):
				service.add_note("DR-2026-001", "L3-S04-E05", "Make the video bigger")
			with self.assertRaises((PermissionError_, DoesNotExistError)):
				service.cast_verdict("DR-2026-001", "L3", "S04", "Yes")
		self.assertEqual(DB.rows("Design Vote") + DB.rows("Design Note") + DB.rows("Design Verdict"), [])

	def test_participant_writes_are_stamped_with_the_session_user(self):
		DB.user = PAT
		service.cast_vote("DR-2026-001", "learner", ["L3", "L1", "L2"])
		service.add_note("DR-2026-001", "L3-S04-E05", "Make the video bigger")
		service.cast_verdict("DR-2026-001", "L3", "S04", "Maybe")
		self.assertEqual(DB.rows("Design Vote")[0]["voter"], PAT)
		self.assertEqual(json.loads(DB.rows("Design Vote")[0]["ranking"]), ["L3", "L1", "L2"])
		note = DB.rows("Design Note")[0]
		self.assertEqual(
			(note["author"], note["code"], note["part_name"], note["is_imported"]),
			(PAT, "L3-S04-E05", "Video block", 0),
		)
		self.assertEqual(DB.rows("Design Verdict")[0]["voter"], PAT)

	def test_voting_again_replaces(self):
		DB.user = PAT
		service.cast_vote("DR-2026-001", "learner", ["L3", "L1", "L2"])
		service.cast_vote("DR-2026-001", "learner", ["L1", "L2", "L3"])
		self.assertEqual(len(DB.rows("Design Vote")), 1)
		self.assertEqual(json.loads(DB.rows("Design Vote")[0]["ranking"]), ["L1", "L2", "L3"])

	def test_bad_ranking_and_unknown_code_are_refused(self):
		DB.user = PAT
		with self.assertRaises(ValidationError):
			service.cast_vote("DR-2026-001", "learner", ["L1", "L2"])
		with self.assertRaises(ValidationError):
			service.add_note("DR-2026-001", "L3-S04-E77", "No such part")

	def test_closed_and_draft_reviews_refuse_writes(self):
		for status in ("Draft", "Closed", "Decided"):
			_seed(status)
			DB.user = PAT
			with self.assertRaises(ValidationError):
				service.cast_vote("DR-2026-001", "learner", ["L1", "L2", "L3"])
			with self.assertRaises(ValidationError):
				service.add_note("DR-2026-001", "L3-S04-E05", "Too late")

	def test_only_the_author_deletes_a_note(self):
		DB.user = PAT
		name = service.add_note("DR-2026-001", "L3-S04-E05", "Make the video bigger")["name"]
		DB.add(
			"Design Review Participant",
			parent="DR-2026-001",
			parenttype="Design Review",
			parentfield="participants",
			user=OUTSIDER,
		)
		DB.user = OUTSIDER
		with self.assertRaises(PermissionError_):
			service.delete_note(name)
		DB.user = PAT
		service.delete_note(name)
		self.assertEqual(DB.rows("Design Note"), [])


class TestPromotion(unittest.TestCase):
	def setUp(self):
		_seed("Closed")
		DB.add(
			"Design Decision",
			name="DD-2026-0001",
			review="DR-2026-001",
			track="learner",
			option_code="L3",
			title="Go with the outline layout",
			decision="Build L3 for the learner app, with a bigger video.",
			status="Recorded",
			notes_json="[]",
		)

	def test_service_accounts_cannot_promote(self):
		for user in (TRITON, "Administrator"):
			DB.user = user
			with self.assertRaises(PermissionError_):
				service.promote_decision("DD-2026-0001", 1, 0)
		self.assertEqual(DB.filed, [])

	def test_a_person_promotes_through_file_request_already_approved(self):
		DB.user = NIK
		out = service.promote_decision("DD-2026-0001", 1, 0)
		self.assertEqual(out["request"], "ER-2026-00099")
		filed = DB.filed[0]
		self.assertEqual(filed["source"], "Design Review")
		self.assertEqual(filed["requested_by"], NIK)
		self.assertTrue(filed["approve"])
		self.assertEqual((filed["source_doctype"], filed["source_ref"]), ("Design Decision", "DD-2026-0001"))
		self.assertEqual(filed["target_erpnext"], 1)
		self.assertIn("Build L3", filed["values"]["description"])
		decision = DB.rows("Design Decision")[0]
		self.assertEqual(
			(decision["status"], decision["promoted_request"], decision["promoted_by"]),
			("Promoted", "ER-2026-00099", NIK),
		)

	def test_a_decision_is_promoted_once(self):
		DB.user = NIK
		service.promote_decision("DD-2026-0001", 1, 0)
		with self.assertRaises(ValidationError):
			service.promote_decision("DD-2026-0001", 1, 0)
		self.assertEqual(len(DB.filed), 1)


def _bundle():
	return {
		"format": "sapphire-design-review/1",
		"title": "Training",
		"tracks": [{"id": "learner", "label": "Learner"}],
		"options": [{"track": "learner", "code": "L3", "name": "Outline"}],
		"screens": [
			{
				"track": "learner",
				"option": "L3",
				"screen": "S04",
				"frame": "phone",
				"w": 390,
				"h": 844,
				"html": "<div></div>",
			}
		],
		"parts": {"learner:S04": [[1, "Top bar"]]},
	}


class TestBundleShape(unittest.TestCase):
	"""Codes are written into the Review Room's markup and routes, so their format is the import's job."""

	def setUp(self):
		sys.modules.pop("erpnext_enhancements.design_review.importer", None)
		self.importer = importlib.import_module("erpnext_enhancements.design_review.importer")

	def test_a_plain_bundle_validates(self):
		self.importer.validate_bundle(_bundle())

	def test_hostile_or_malformed_codes_are_refused(self):
		cases = [
			("options", 0, "code", 'L3"><img src=x onerror=alert(1)>'),
			("options", 0, "code", "l3"),
			("screens", 0, "screen", "S04<b>"),
			("tracks", 0, "id", "Learner Track"),
		]
		for key, index, field, value in cases:
			bundle = _bundle()
			bundle[key][index][field] = value
			if key == "options":
				bundle["screens"][0]["option"] = value
			if key == "tracks":
				bundle["options"][0]["track"] = value
				bundle["screens"][0]["track"] = value
			with self.assertRaises(self.importer.BundleError, msg=value):
				self.importer.validate_bundle(bundle)

	def test_frames_sizes_and_unknown_options_are_refused(self):
		for mutate in (
			lambda b: b["screens"][0].update(frame="tablet"),
			lambda b: b["screens"][0].update(w=10),
			lambda b: b["screens"][0].update(option="L9"),
			lambda b: b.update(format="something-else"),
			lambda b: b["parts"].update({"learner:S04": [[0, "Zero"]]}),
		):
			bundle = _bundle()
			mutate(bundle)
			with self.assertRaises(self.importer.BundleError):
				self.importer.validate_bundle(bundle)


if __name__ == "__main__":
	unittest.main()
