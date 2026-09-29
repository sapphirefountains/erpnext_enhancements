"""Bench-free tests for the event-rental sales flow (v1.564.0).

The agreement's signature block, what signing is allowed to do inside a Guest's transaction,
what an invoice draft may and may not post, and the settings behind it. No ``frappe`` import
beyond a throwaway module stub for the patch; everything else reads the source.

Run: python -m unittest erpnext_enhancements.tests.test_rental_sales -v

The ones that matter most:

* :meth:`TestSigning.test_confirming_can_never_undo_a_signature` — the hook runs inside the
  signer's transaction on /contract-sign. A raise there would roll back the customer's signature
  because the fountains had been taken meanwhile.
* :meth:`TestInvoices.test_drafting_never_posts` — Nik's call: nothing reaches the books until a
  person presses Submit & Send.
* :meth:`TestInvoices.test_tax_is_the_settings_template_or_nothing` — ERPNext's party defaults
  would otherwise put a tax on rental invoices while the Utah decision (OD-2) is open.
"""

import ast
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

APP = Path(__file__).resolve().parents[1]
SALES = APP / "asset_management" / "rental_sales.py"
HOLDS = APP / "asset_management" / "rental_holds.py"
PATCH = APP / "patches" / "add_rental_esign_signature_block.py"
TEMPLATE = APP / "templates" / "contracts" / "rental_agreement.html"


def load_patch():
	"""Import the patch with a throwaway ``frappe`` if the real one is absent."""
	stubbed = "frappe" not in sys.modules
	if stubbed:
		sys.modules["frappe"] = types.ModuleType("frappe")
	try:
		spec = importlib.util.spec_from_file_location("rental_sig_patch", PATCH)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
		return module
	finally:
		if stubbed:
			del sys.modules["frappe"]


def functions(path):
	return {n.name: n for n in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(n, ast.FunctionDef)}


def source(node):
	return ast.unparse(node)


class TestSignatureBlockPatch(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.patch = load_patch()
		cls.shipped = TEMPLATE.read_text(encoding="utf-8")

	def test_shipped_template_is_already_signature_aware(self):
		self.assertIn("{{ sig('client') }}", self.shipped)
		self.assertIn("{{ sig('provider') }}", self.shipped)
		self.assertEqual(self.shipped.count(self.patch.NEW_BLOCK), 1)

	def test_patch_rewrites_the_unpatched_block_into_the_shipped_file(self):
		unpatched = self.shipped.replace(self.patch.NEW_BLOCK, self.patch.OLD_BLOCK)
		self.assertNotIn("sig(", unpatched)
		new, changed = self.patch.rewrite_signature_block(unpatched)
		self.assertTrue(changed)
		self.assertEqual(new, self.shipped)

	def test_patch_is_idempotent(self):
		self.assertEqual(self.patch.rewrite_signature_block(self.shipped), (self.shipped, False))

	def test_patch_refuses_a_diverged_body(self):
		unpatched = self.shipped.replace(self.patch.NEW_BLOCK, self.patch.OLD_BLOCK)
		diverged = unpatched.replace("Print Name: {{ blank(30) }}", "Printed Name: {{ blank(30) }}")
		self.assertEqual(self.patch.rewrite_signature_block(diverged), (diverged, False))

	def test_patch_handles_an_empty_body(self):
		self.assertEqual(self.patch.rewrite_signature_block(""), ("", False))
		self.assertEqual(self.patch.rewrite_signature_block(None), (None, False))

	def test_a_diverged_body_is_logged_under_its_title(self):
		"""Frappe v16's ``log_error`` makes its first argument the title unless that holds a newline.

		This call shipped in v1.564.0 as ``log_error(sentence, title)``: prod would have filed the
		one-line sentence as the row's title and the title as its body (fixed in v1.566.1). The stub
		stores the row the way v16 does, so that order fails here.
		"""
		unpatched = self.shipped.replace(self.patch.NEW_BLOCK, self.patch.OLD_BLOCK)
		diverged = unpatched.replace("Print Name: {{ blank(30) }}", "Printed Name: {{ blank(30) }}")
		rows, writes = [], []

		def log_error(
			title=None, message=None, reference_doctype=None, reference_name=None, *, defer_insert=False
		):
			if message and "\n" in title:
				title, message = message, title
			rows.append((title, message))

		fake = types.SimpleNamespace(
			db=types.SimpleNamespace(
				get_value=lambda *a, **k: diverged,
				set_value=lambda *a, **k: writes.append(a),
				commit=lambda: None,
			),
			log_error=log_error,
		)
		with mock.patch.object(self.patch, "frappe", fake):
			self.patch.execute()
		self.assertEqual(writes, [], "a diverged template is left alone")
		self.assertEqual(len(rows), 1, rows)
		title, body = rows[0]
		self.assertEqual(title, "Contract e-sign: rental signature block not patched")
		self.assertIn("{{ sig('client') }}", body)

	def test_exhibit_a_condition_lines_stay_paper(self):
		"""Filled in by hand at delivery and return, not at signing."""
		self.assertIn("Renter (or Authorized Agent): {{ blank(30) }}", self.shipped)

	def test_patch_is_registered(self):
		self.assertIn(
			"erpnext_enhancements.patches.add_rental_esign_signature_block",
			(APP / "patches.txt").read_text(encoding="utf-8"),
		)


class TestSigning(unittest.TestCase):
	def test_hooks_wire_both_signing_paths(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		start = hooks.index('\t"Project Contract": {')
		block = hooks[start : hooks.index("\n\t},", start)]
		self.assertEqual(block.count("rental_sales.on_rental_agreement_signed"), 2)
		self.assertEqual(block.count("autocreate_maintenance_contract_on_signed"), 2)

	def test_confirming_can_never_undo_a_signature(self):
		fn = functions(SALES)["on_rental_agreement_signed"]
		tries = [n for n in fn.body if isinstance(n, ast.Try)]
		# Everything that can fail runs inside a try that catches Exception, rolls back to its
		# savepoint and never re-raises: confirming the booking, and (v1.565.0) the portal account.
		guarded = "\n".join(source(t) for t in tries)
		for call in ("confirm_booking(", "open_portal_for_signer("):
			self.assertIn(call, guarded)
		outside = "\n".join(source(n) for n in fn.body if not isinstance(n, ast.Try))
		self.assertNotIn("confirm_booking(", outside)
		self.assertNotIn("open_portal_for_signer(", outside)
		for block in tries:
			self.assertTrue(any(source(h.type) == "Exception" for h in block.handlers))
			self.assertIn("frappe.db.rollback(save_point=savepoint)", source(block))
			self.assertNotIn("raise", "\n".join(source(h) for h in block.handlers))

	def test_only_a_rental_agreement_becoming_signed_counts(self):
		fn = source(functions(SALES)["on_rental_agreement_signed"])
		self.assertIn("doc.get('template_key') != RENTAL_TEMPLATE", fn)
		self.assertIn("before.get('status') == 'Signed'", fn)

	def test_confirm_runs_as_the_system_for_a_guest_signer(self):
		self.assertIn("booking.flags.ignore_permissions = True", source(functions(SALES)["confirm_booking"]))


class TestInvoices(unittest.TestCase):
	def test_drafting_never_posts(self):
		for name in ("draft_invoice", "after_confirmed", "draft_due_balance_invoices", "draft_rental_invoice"):
			with self.subTest(fn=name):
				self.assertNotIn(".submit()", source(functions(SALES)[name]))

	def test_submit_and_send_is_the_one_click_and_uses_the_callers_rights(self):
		fn = functions(SALES)["submit_and_send"]
		self.assertIn("frappe.whitelist(methods=['POST'])", [source(d) for d in fn.decorator_list])
		# Statements only: the docstring explains the absence and names the token.
		body = "\n".join(source(stmt) for stmt in fn.body[1:])
		self.assertIn("si.check_permission('submit')", body)
		self.assertNotIn("ignore_permissions", body)

	def test_autopay_customers_get_no_link(self):
		"""An open link makes the autopay charge refuse itself; one invoice must never be paid twice."""
		body = source(functions(SALES)["submit_and_send"])
		self.assertLess(body.index("custom_stripe_autopay_enabled"), body.index("create_payment("))

	def test_tax_is_the_settings_template_or_nothing(self):
		body = source(functions(SALES)["draft_invoice"])
		self.assertIn("si.taxes_and_charges = conf.taxes_and_charges or None", body)
		self.assertIn("si.set('taxes', [])", body)
		self.assertLess(body.index("si.set_missing_values()"), body.index("si.set('taxes', [])"))

	def test_security_deposit_posts_to_the_liability_account(self):
		body = source(functions(SALES)["draft_invoice"])
		self.assertIn("row.income_account = conf.security_deposit_account", body)
		self.assertLess(body.index("si.set_missing_values()"), body.index("row.income_account = conf.security_deposit_account"))

	def test_one_invoice_of_each_kind(self):
		body = source(functions(SALES)["draft_invoice"])
		self.assertLess(body.index("_existing_invoice(booking.name, kind)"), body.index("frappe.new_doc('Sales Invoice')"))

	def test_sales_invoice_link_fields_ship(self):
		fixtures = {f["name"]: f for f in json.loads((APP / "fixtures" / "custom_field.json").read_text(encoding="utf-8"))}
		self.assertEqual(fixtures["Sales Invoice-custom_rental_booking"]["options"], "Rental Booking")
		self.assertEqual(
			# v1.566.0 appends Damage and Deposit Return; the first three are what drafting keys on.
			fixtures["Sales Invoice-custom_rental_invoice_kind"]["options"].split("\n")[:3], ["", "Deposit", "Balance"]
		)


class TestHoldsAndSettings(unittest.TestCase):
	def test_date_filters_exclude_holds_with_no_date(self):
		"""Frappe coalesces a comparison on a nullable date; a dateless hold must never be expired."""
		body = source(functions(HOLDS)["run_daily"])
		self.assertEqual(body.count("['hold_expires_on', 'is', 'set']"), 2)

	def test_settings_defaults(self):
		doc = json.loads(
			(APP / "asset_management" / "doctype" / "rental_settings" / "rental_settings.json").read_text(encoding="utf-8")
		)
		self.assertEqual(doc["issingle"], 1)
		defaults = {f["fieldname"]: f.get("default") for f in doc["fields"]}
		self.assertEqual(defaults["hold_days"], "7")
		self.assertEqual(defaults["deposit_percent"], "50")
		self.assertEqual(defaults["balance_days_before_delivery"], "14")
		self.assertEqual(defaults["expire_holds"], "1")
		# Emailing real customers is opt-in.
		self.assertEqual(defaults["email_customer_on_hold"], "0")

	def test_hold_notice_never_raises(self):
		fn = functions(HOLDS)["send_hold_notice"]
		self.assertIsInstance(fn.body[1], ast.Try)
		self.assertTrue(any(source(h.type) == "Exception" for h in fn.body[1].handlers))


if __name__ == "__main__":
	unittest.main()
