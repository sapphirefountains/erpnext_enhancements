"""Bench-free tests for the CRM website scheme fix (TASK-2026-01604).

Two things are fenced here, and they are different sizes.

The small one is the reported bug: ``example.com`` must save. The large one is what
made it worth a patch — our own ``*-website-options`` Property Setters turned ``website``
into a URL field *after* the data was imported, so 384 CRM records rejected **every**
edit, not just an edit to the website. Both come down to one pure function, so that is
what most of this exercises.

The rule is inherited verbatim from ``quickbooks_online.core.mapping._heal_invalid_urls``
(v1.36.0), which now delegates here. ``test_the_rule_is_unchanged_from_the_qbo_healer``
states the parts of it that look like oversights — ``N/A`` becoming ``https://N/A``,
``mailto:`` left alone — so that a later "tidy-up" has to argue with a test rather than
a comment.

Follows the stubbing pattern in ``test_sync_contact_primary.py``: a ``frappe`` stub
installed in ``setUpModule`` (execution time, not import time, so the bench-only suites'
import guards are unaffected).

Run: python -m unittest erpnext_enhancements.tests.test_website_cleanup -v
"""

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))


class _Field:
	"""A DocField as far as the healer is concerned."""

	def __init__(self, fieldname, fieldtype="Data", options="URL"):
		self.fieldname = fieldname
		self.fieldtype = fieldtype
		self.options = options


class _Doc:
	"""A document as far as the healer is concerned: doctype, get, set."""

	def __init__(self, doctype, values):
		self.doctype = doctype
		self._values = dict(values)

	def get(self, fieldname):
		return self._values.get(fieldname)

	def set(self, fieldname, value):
		self._values[fieldname] = value


#: doctype -> fields the stubbed meta serves. Reset per test.
META = {}
#: Set to raise from get_meta, standing in for the fresh-DB bootstrap.
META_RAISES = []


def _install_stub():
	frappe = types.ModuleType("frappe")

	def get_meta(doctype):
		if META_RAISES:
			raise RuntimeError("meta unavailable during bootstrap")
		return types.SimpleNamespace(fields=META.get(doctype, []))

	frappe.get_meta = get_meta
	frappe.whitelist = lambda *a, **kw: (lambda fn: fn)
	frappe._ = lambda s: s

	sys.modules["frappe"] = frappe
	sys.modules.pop("erpnext_enhancements.crm_enhancements.website_cleanup", None)


def setUpModule():
	_install_stub()
	global website_cleanup
	from erpnext_enhancements.crm_enhancements import website_cleanup as module

	website_cleanup = module


class TestNormalizeWebsite(unittest.TestCase):
	"""The whole rule, as a table. None means "leave the stored value alone"."""

	def test_a_bare_domain_gets_https(self):
		"""The reported bug: this is what a business card says."""
		self.assertEqual(website_cleanup.normalize_website("example.com"), "https://example.com")

	def test_the_shapes_people_actually_type(self):
		for typed, expected in [
			("www.saltdev.com", "https://www.saltdev.com"),
			("slcolibrary.org/viridian", "https://slcolibrary.org/viridian"),
			("example.co.uk:8080/x", "https://example.co.uk:8080/x"),
			("  example.com  ", "https://example.com"),
		]:
			with self.subTest(typed=typed):
				self.assertEqual(website_cleanup.normalize_website(typed), expected)

	def test_anything_with_a_scheme_is_left_alone(self):
		for value in ("https://example.com", "http://example.com", "ftp://files.example.com"):
			with self.subTest(value=value):
				self.assertIsNone(website_cleanup.normalize_website(value))

	def test_references_frappe_already_accepts_are_left_alone(self):
		"""validate_url passes a leading slash outright; the rest would be mangled."""
		for value in ("/careers", "#top", "mailto:sales@example.com", "tel:+18015550100"):
			with self.subTest(value=value):
				self.assertIsNone(website_cleanup.normalize_website(value))

	def test_nothing_to_do_with_nothing(self):
		for value in (None, "", "   ", 0, [], object()):
			with self.subTest(value=value):
				self.assertIsNone(website_cleanup.normalize_website(value))

	def test_healing_twice_changes_nothing(self):
		"""What makes the backfill patch safe to re-run."""
		once = website_cleanup.normalize_website("example.com")
		self.assertIsNone(website_cleanup.normalize_website(once))

	def test_the_rule_is_unchanged_from_the_qbo_healer(self):
		"""The two parts that look like bugs and are not.

		``N/A`` becomes a nonsense URL rather than staying invalid, because the
		alternative is a record nobody can save; and a value with a scheme-shaped
		prefix but no ``://`` is still prefixed, because guessing port-versus-scheme
		is how you corrupt data. Both match what the QuickBooks sync has already
		written to this same data since v1.36.0.
		"""
		self.assertEqual(website_cleanup.normalize_website("N/A"), "https://N/A")
		self.assertEqual(
			website_cleanup.normalize_website("djohns929@gmail.com"),
			"https://djohns929@gmail.com",
		)


class TestHealUrlFields(unittest.TestCase):
	def setUp(self):
		META.clear()
		META_RAISES.clear()
		META["Lead"] = [
			_Field("website"),
			_Field("custom_account_website"),
			_Field("lead_name", options=None),
			_Field("custom_website_image_links", fieldtype="Text Editor", options=None),
			_Field("email_id", options="Email"),
		]

	def test_it_heals_every_url_field_not_just_website(self):
		"""Meta-driven on purpose: Lead has a second URL field nobody would list."""
		doc = _Doc("Lead", {"website": "example.com", "custom_account_website": "acme.test"})

		healed = website_cleanup.heal_url_fields(doc)

		self.assertEqual(sorted(healed), ["custom_account_website", "website"])
		self.assertEqual(doc.get("website"), "https://example.com")
		self.assertEqual(doc.get("custom_account_website"), "https://acme.test")

	def test_it_leaves_fields_that_are_not_urls(self):
		doc = _Doc("Lead", {"lead_name": "Acme Corp", "email_id": "a@b.test", "website": ""})

		self.assertEqual(website_cleanup.heal_url_fields(doc), [])
		self.assertEqual(doc.get("lead_name"), "Acme Corp")
		self.assertEqual(doc.get("email_id"), "a@b.test")

	def test_it_reports_only_what_it_changed(self):
		doc = _Doc("Lead", {"website": "https://done.test", "custom_account_website": "acme.test"})

		self.assertEqual(website_cleanup.heal_url_fields(doc), ["custom_account_website"])
		self.assertEqual(doc.get("website"), "https://done.test")

	def test_an_unknown_doctype_has_no_url_fields(self):
		self.assertEqual(website_cleanup.heal_url_fields(_Doc("Widget", {"website": "x.test"})), [])

	def test_meta_that_cannot_be_read_is_a_no_op(self):
		"""doc_events fire during ERPNext's own test bootstrap, before our fields exist.
		A handler that cannot read meta must do nothing, not crash the install."""
		META_RAISES.append(True)
		doc = _Doc("Lead", {"website": "example.com"})

		self.assertEqual(website_cleanup.heal_url_fields(doc), [])
		self.assertEqual(doc.get("website"), "example.com")


class TestDocEvent(unittest.TestCase):
	def setUp(self):
		META.clear()
		META_RAISES.clear()
		META["Customer"] = [_Field("website")]

	def test_it_takes_the_doc_event_signature(self):
		doc = _Doc("Customer", {"website": "2xlimaging.com"})

		website_cleanup.add_missing_scheme(doc, method="before_validate")

		self.assertEqual(doc.get("website"), "https://2xlimaging.com")

	def test_method_is_optional(self):
		doc = _Doc("Customer", {"website": "2xlimaging.com"})
		website_cleanup.add_missing_scheme(doc)
		self.assertEqual(doc.get("website"), "https://2xlimaging.com")

	def test_the_name_the_quickbooks_mapper_imports_exists(self):
		"""mapping._heal_invalid_urls delegates here by a function-local import, so a
		rename would only surface at sync time."""
		self.assertTrue(callable(getattr(website_cleanup, "heal_url_fields", None)))


class TestHookAndPatchAgree(unittest.TestCase):
	"""The backfill and the hook must cover the same doctypes.

	They are two halves of one fix and they fail in opposite directions. A doctype in
	the patch but not in hooks.py is repaired once and re-breaks the next time somebody
	types a domain. A doctype in hooks.py but not the patch accepts new input and leaves
	its existing records frozen — the state this release exists to end.

	``hooks.py`` is read with ``ast`` rather than imported, because importing it pulls in
	frappe (the same reason ``test_hooks_integrity`` does).
	"""

	HANDLER = "erpnext_enhancements.crm_enhancements.website_cleanup.add_missing_scheme"

	def _wired_doctypes(self):
		import ast

		source = (REPO_ROOT / "erpnext_enhancements" / "hooks.py").read_text(encoding="utf-8")
		for node in ast.parse(source).body:
			if isinstance(node, ast.Assign) and any(
				isinstance(t, ast.Name) and t.id == "doc_events" for t in node.targets
			):
				doc_events = ast.literal_eval(node.value)
				break
		else:
			raise AssertionError("no doc_events assignment in hooks.py")

		wired = set()
		for doctype, events in doc_events.items():
			handlers = events.get("before_validate") or []
			if isinstance(handlers, str):
				handlers = [handlers]
			if self.HANDLER in handlers:
				wired.add(doctype)
		return wired

	def test_the_patch_covers_exactly_what_the_hook_is_wired_to(self):
		from erpnext_enhancements.patches import backfill_website_scheme

		self.assertEqual(self._wired_doctypes(), set(backfill_website_scheme.DOCTYPES))

	def test_it_covers_every_doctype_with_a_url_property_setter(self):
		"""One entry per `*-website-options` fixture. Leaving one out is how this
		defect survived on a doctype nobody thought about for two years."""
		self.assertEqual(
			self._wired_doctypes(),
			{"Lead", "Customer", "Opportunity", "Supplier", "Company"},
		)


if __name__ == "__main__":
	unittest.main()
