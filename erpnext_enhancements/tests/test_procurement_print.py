"""Bench-free unit tests for printing from the Procurement Tracker.

Stubs a minimal ``frappe`` — including ``frappe.utils.print_format`` — so
``erpnext_enhancements.procurement_print`` runs under plain unittest. The stub is installed
in ``setUpModule`` (execution time), not at import, so it never fools the bench-only suites'
``import frappe`` skip-guards. Own CI step for the same reason as ``test_po_pdf_filename``:
the stub is process-wide and would cross-talk if two suites shared an interpreter.

What is worth pinning, all of it invisible until somebody prints:

* **frappe's own multi-document print is called, once, with everything it was given.**
  This is a decoration of the list view's Actions → Print, not a second implementation of it.
* **The file is named for the job and the choice** — ``PRJ-00706-Open-Purchase-Orders.pdf``
  — which is the entire reason the route exists.
* **Project read is required, and checked before anything renders.**
* **A request that names nothing is refused**, rather than downloading an empty PDF that
  reads as "nothing is open".
* **The browser and the server agree** on the method name, and on the six doctypes.

The open rule itself is in ``test_procurement_quantities.py``, which needs no stub at all.

Run: python -m unittest erpnext_enhancements.tests.test_procurement_print -v
"""

import ast
import importlib
import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
SCRIPT = APP / "public" / "js" / "project_enhancements.js"
PROJECT_PACKAGE = APP / "project_enhancements" / "__init__.py"
DOTTED = "erpnext_enhancements.procurement_print.download_procurement_pdf"

# Mutable state the frappe stub reads at call time.
STATE = {"calls": [], "checks": [], "deny": False}
procurement_print = None


class _Throw(Exception):
	pass


class _PermissionError(Exception):
	pass


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")

	class _Project:
		def __init__(self, name):
			self.name = name

		def check_permission(self, ptype):
			STATE["checks"].append((self.name, ptype))
			if STATE["deny"]:
				raise _PermissionError(f"No {ptype} permission on {self.name}")

	def get_doc(doctype, name):
		assert doctype == "Project", doctype
		return _Project(name)

	def throw(message, exc=None):
		raise _Throw(message)

	frappe.get_doc = get_doc
	frappe.throw = throw
	frappe._ = lambda text: text
	frappe.whitelist = lambda *a, **kw: (lambda fn: fn)
	frappe.PermissionError = _PermissionError
	frappe.local = types.SimpleNamespace(response=types.SimpleNamespace(filename=None))

	utils = types.ModuleType("frappe.utils")
	utils.flt = lambda v, precision=None: float(v or 0)

	print_format = types.ModuleType("frappe.utils.print_format")

	def download_multi_pdf(doctype, name, format=None, no_letterhead=False, letterhead=None, options=None):
		"""Stands in for frappe's: records the call and names the file the way frappe does."""
		STATE["calls"].append(
			{
				"doctype": doctype,
				"name": name,
				"format": format,
				"no_letterhead": no_letterhead,
				"letterhead": letterhead,
				"options": options,
			}
		)
		frappe.local.response.filename = "{}.pdf".format(doctype.replace(" ", "-").replace("/", "-"))
		return None

	print_format.download_multi_pdf = download_multi_pdf
	utils.print_format = print_format
	frappe.utils = utils

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.utils.print_format"] = print_format


def setUpModule():
	global procurement_print
	_install_frappe_stub()
	for module in (
		"erpnext_enhancements.procurement_print",
		"erpnext_enhancements.procurement_project",
	):
		sys.modules.pop(module, None)
	# importlib, not `from erpnext_enhancements import procurement_print`: the from-import
	# form hands back an attribute already bound on the package, which may belong to a
	# module imported against a different stub. See test_po_pdf_filename for the incident.
	procurement_print = importlib.import_module("erpnext_enhancements.procurement_print")


def _reset(deny=False):
	STATE.update(calls=[], checks=[], deny=deny)
	sys.modules["frappe"].local.response.filename = None


def _download(**overrides):
	kwargs = {
		"project": "PRJ-00706",
		"doctype": "Purchase Order",
		"name": json.dumps(["PO-2026-00262", "PO-2026-00263"]),
		"scope": "open",
	}
	kwargs.update(overrides)
	return procurement_print.download_procurement_pdf(**kwargs)


class TestFilename(unittest.TestCase):
	def test_open_is_named_and_all_is_not(self):
		name = procurement_print.combined_pdf_filename
		self.assertEqual(name("PRJ-00706", "Purchase Order", "open"), "PRJ-00706-Open-Purchase-Orders.pdf")
		self.assertEqual(name("PRJ-00706", "Purchase Order", "all"), "PRJ-00706-Purchase-Orders.pdf")

	def test_every_procurement_doctype_has_a_proper_plural(self):
		name = procurement_print.combined_pdf_filename
		self.assertEqual(
			name("PRJ-00706", "Request for Quotation", "all"), "PRJ-00706-Requests-for-Quotation.pdf"
		)
		self.assertEqual(
			name("PRJ-00706", "Material Request", "open"), "PRJ-00706-Open-Material-Requests.pdf"
		)

	def test_a_project_name_cannot_introduce_a_path_separator(self):
		self.assertEqual(
			procurement_print.combined_pdf_filename("A/B C", "Purchase Order", "all"),
			"A-B-C-Purchase-Orders.pdf",
		)


class TestDownload(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_frappes_multi_pdf_is_called_once_with_everything_it_was_given(self):
		_download(
			format="Purchase Order - Sapphire", no_letterhead="0", letterhead="Sapphire Fountains Default"
		)
		self.assertEqual(len(STATE["calls"]), 1)
		call = STATE["calls"][0]
		self.assertEqual(call["doctype"], "Purchase Order")
		self.assertEqual(json.loads(call["name"]), ["PO-2026-00262", "PO-2026-00263"])
		self.assertEqual(call["format"], "Purchase Order - Sapphire")
		self.assertEqual(call["no_letterhead"], "0")
		self.assertEqual(call["letterhead"], "Sapphire Fountains Default")

	def test_the_file_is_renamed_after_frappe_has_named_it(self):
		"""frappe names it `Purchase-Order.pdf` — the same name for every job's orders."""
		_download()
		self.assertEqual(sys.modules["frappe"].local.response.filename, "PRJ-00706-Open-Purchase-Orders.pdf")

	def test_project_read_is_checked_before_anything_renders(self):
		_reset(deny=True)
		with self.assertRaises(_PermissionError):
			_download()
		self.assertEqual(STATE["checks"], [("PRJ-00706", "read")])
		self.assertEqual(STATE["calls"], [], "nothing may render for a project the caller cannot read")

	def test_a_request_that_names_nothing_is_refused(self):
		for name in ("[]", "not json", json.dumps("PO-1"), json.dumps([""]), json.dumps([1])):
			with self.subTest(name=name), self.assertRaises(_Throw):
				_download(name=name)
		self.assertEqual(STATE["calls"], [])

	def test_only_procurement_doctypes_and_known_choices(self):
		with self.assertRaises(_Throw):
			_download(doctype="Sales Invoice")
		with self.assertRaises(_Throw):
			_download(scope="everything")
		self.assertEqual(STATE["calls"], [])

	def test_the_signature_carries_every_parameter_frappes_does(self):
		"""`frappe.call` matches the request's form_dict against THIS signature, so a
		parameter missing here is a format or letter head the user picked and silently did
		not get."""
		import inspect

		ours = set(inspect.signature(procurement_print.download_procurement_pdf).parameters)
		theirs = set(
			inspect.signature(sys.modules["frappe.utils.print_format"].download_multi_pdf).parameters
		)
		self.assertLessEqual(theirs - {"doctype", "name"}, ours)


class TestAgreement(unittest.TestCase):
	def test_the_six_doctypes_match_the_trackers_groups(self):
		"""The allow-list must be exactly the groups the tracker can show a Print button on."""
		tree = ast.parse(PROJECT_PACKAGE.read_text(encoding="utf-8"))
		order = None
		for node in tree.body:
			if isinstance(node, ast.Assign) and any(
				isinstance(t, ast.Name) and t.id == "PROCUREMENT_DOCTYPE_ORDER" for t in node.targets
			):
				order = ast.literal_eval(node.value)
		self.assertIsNotNone(order, "PROCUREMENT_DOCTYPE_ORDER not found")
		self.assertEqual(list(procurement_print.PLURALS), order)

	def test_procurement_project_re_exports_the_one_settled_rule(self):
		"""One definition of "nothing left to receive": the tracker's Open, the Receive picker
		and the Supplier Pickup List all read this object."""
		quantities = importlib.import_module("erpnext_enhancements.procurement_quantities")
		project = importlib.import_module("erpnext_enhancements.procurement_project")
		self.assertIs(project.SETTLED_PO_STATUSES, quantities.SETTLED_PO_STATUSES)


class TestTrackerScript(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.script = SCRIPT.read_text(encoding="utf-8")

	def test_the_tracker_dials_a_method_that_exists(self):
		self.assertIn(f"/api/method/{DOTTED}?", self.script)
		module, _, attr = DOTTED.rpartition(".")
		self.assertTrue(callable(getattr(importlib.import_module(module), attr)))

	def test_the_tracker_sends_what_the_endpoint_reads(self):
		for key in ("project", "doctype", "name", "scope", "format", "no_letterhead", "letterhead"):
			self.assertRegex(self.script, rf"\b{key}\b\s*[:,]|params\.set\(\"{key}\"", key)
		# Not a parameter of ours, and still load-bearing: frappe's get_print reads it off
		# form_dict, and without it "Standard" renders on wkhtmltopdf.
		self.assertIn("pdf_generator: procurementPdfGenerator(format)", self.script)

	def test_documents_frappe_would_refuse_are_filtered_before_the_request(self):
		"""download_multi_pdf drops a refused document from the PDF without a word, so the
		browser has to know both Print Settings rules."""
		self.assertIn("allow_print_for_draft", self.script)
		self.assertIn("allow_print_for_cancelled", self.script)

	def test_background_threshold_matches_frappes_list_view(self):
		self.assertIn("const PROCUREMENT_BACKGROUND_PRINT_THRESHOLD = 25;", self.script)
		self.assertIn('"frappe.utils.print_format.download_multi_pdf_async"', self.script)


if __name__ == "__main__":
	unittest.main()
