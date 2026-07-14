"""Tests for contract generation (``project_enhancements.doctype.project_contract``).

Covers the decision logic that protects the legal workflow:

* the MSA gate — a SOW cannot exist without a Signed, submitted MSA for the
  same supplier (the Jun 9 follow-up's hard sequencing rule);
* per-type computed totals (owner phases, rental fees, maintenance deposit);
* revision stamping from the amend chain;
* every shipped template parses and renders against both an empty and a
  populated context (so a template edit that breaks Jinja fails in tests,
  not at print time).
"""

import os
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_enhancements.project_enhancements.doctype.project_contract.project_contract import (
	SERIES_BY_KEY,
	ProjectContract,
	_compose_scope,
	_render_context,
)

TEMPLATE_DIR = frappe.get_app_path("erpnext_enhancements", "templates", "contracts")


def _bare(**fields):
	"""A ProjectContract instance with attributes set directly (no meta/init —
	Document.update would consult meta for child-table values)."""
	doc = ProjectContract.__new__(ProjectContract)
	doc.__dict__.update(fields)
	return doc


class TestMsaGate(FrappeTestCase):
	def _sow(self, msa_contract="SF-MSA-0001", party="SUB-001"):
		return _bare(
			doctype="Project Contract",
			contract_template="sow",
			template_key="sow",
			party_type="Supplier",
			party=party,
			msa_contract=msa_contract,
		)

	def _with_msa(self, sow, msa_row):
		def fake_get_value(doctype, name, fieldname=None, **kwargs):
			if doctype == "Contract Template":
				return 1  # requires_msa
			if doctype == "Project Contract":
				return msa_row
			return None

		return patch.object(frappe.db, "get_value", side_effect=fake_get_value)

	def test_signed_msa_passes_and_stamps_effective_date(self):
		sow = self._sow()
		msa = frappe._dict(
			name="SF-MSA-0001", party="SUB-001", status="Signed", docstatus=1,
			signed_on="2026-05-01", contract_date="2026-04-20", template_key="msa",
		)
		with self._with_msa(sow, msa):
			sow.validate_msa_gate()
		self.assertEqual(str(sow.msa_effective_date), "2026-05-01")

	def test_unsigned_msa_blocks(self):
		sow = self._sow()
		msa = frappe._dict(
			name="SF-MSA-0001", party="SUB-001", status="Out for Signature", docstatus=1,
			signed_on=None, contract_date="2026-04-20", template_key="msa",
		)
		with self._with_msa(sow, msa):
			self.assertRaises(frappe.ValidationError, sow.validate_msa_gate)

	def test_unsubmitted_msa_blocks(self):
		sow = self._sow()
		msa = frappe._dict(
			name="SF-MSA-0001", party="SUB-001", status="Signed", docstatus=0,
			signed_on="2026-05-01", contract_date="2026-04-20", template_key="msa",
		)
		with self._with_msa(sow, msa):
			self.assertRaises(frappe.ValidationError, sow.validate_msa_gate)

	def test_other_suppliers_msa_blocks(self):
		sow = self._sow(party="SUB-002")
		msa = frappe._dict(
			name="SF-MSA-0001", party="SUB-001", status="Signed", docstatus=1,
			signed_on="2026-05-01", contract_date="2026-04-20", template_key="msa",
		)
		with self._with_msa(sow, msa):
			self.assertRaises(frappe.ValidationError, sow.validate_msa_gate)

	def test_missing_msa_blocks(self):
		sow = self._sow(msa_contract=None)
		with self._with_msa(sow, None):
			self.assertRaises(frappe.ValidationError, sow.validate_msa_gate)


class TestTotals(FrappeTestCase):
	def test_owner_totals_only_count_included_phases(self):
		doc = _bare(
			template_key="owner",
			milestones=[frappe._dict(amount=100), frappe._dict(amount=400)],
			phases=[
				frappe._dict(included=1, fee=10000, retainer=2000),
				frappe._dict(included=0, fee=50000, retainer=10000),
				frappe._dict(included=1, fee=3000, retainer=500),
			],
			concept_design_fee=1000, design_development_fee=2000, construction_documents_fee=3000,
		)
		doc._compute_totals()
		self.assertEqual(doc.total_contract_value, 13000)
		self.assertEqual(doc.total_due_at_signing, 2500)
		self.assertEqual(doc.total_design_fee, 6000)
		self.assertEqual(doc.milestones_total, 500)

	def test_rental_totals(self):
		doc = _bare(
			template_key="rental", milestones=[],
			base_rental_fee=1000, delivery_setup_fee=200, pickup_removal_fee=150,
			chemicals_fee=50, other_fee=25, security_deposit=500,
		)
		doc._compute_totals()
		self.assertEqual(doc.total_rental_amount, 1425)
		self.assertEqual(doc.total_due_at_signing, 1925)

	def test_maintenance_due_at_signing(self):
		doc = _bare(template_key="maintenance", milestones=[], maintenance_deposit=350)
		doc._compute_totals()
		self.assertEqual(doc.total_due_at_signing, 350)


class TestRevisionStamp(FrappeTestCase):
	def test_amendment_increments_predecessor(self):
		doc = _bare(amended_from="SF-OC-0001", revision=0)
		with patch.object(frappe.db, "get_value", return_value=2):
			doc._stamp_revision()
		self.assertEqual(doc.revision, 3)

	def test_original_stays_zero(self):
		doc = _bare(amended_from=None, revision=0)
		doc._stamp_revision()
		self.assertEqual(doc.revision, 0)


class TestNewDocAttributeSafety(FrappeTestCase):
	"""A NEW document from the desk omits every empty field (the client strips
	nulls before POSTing), so the validate path must never read unset fields
	as bare attributes. Regression for the Jun 10 production crash:
	``AttributeError: 'ProjectContract' object has no attribute 'amended_from'``
	on the very first save of an MSA from the UI."""

	def test_stamp_revision_with_no_attributes_at_all(self):
		doc = _bare()  # neither amended_from nor revision exist
		doc._stamp_revision()  # must not raise
		self.assertIsNone(doc.get("revision"))

	def test_owner_totals_without_fee_attributes(self):
		doc = _bare(template_key="owner", milestones=[], phases=[])
		doc._compute_totals()  # design fee fields absent entirely
		self.assertEqual(doc.total_design_fee, 0)
		self.assertEqual(doc.total_contract_value, 0)

	def test_rental_totals_without_fee_attributes(self):
		doc = _bare(template_key="rental", milestones=[])
		doc._compute_totals()  # no fee fields, no equipment
		self.assertEqual(doc.total_rental_amount, 0)
		self.assertEqual(doc.total_due_at_signing, 0)

	def test_maintenance_totals_without_deposit(self):
		doc = _bare(template_key="maintenance", milestones=[])
		doc._compute_totals()
		self.assertEqual(doc.total_due_at_signing, 0)

	def test_msa_gate_skips_without_template(self):
		doc = _bare()  # no contract_template attribute
		doc.validate_msa_gate()  # must not raise

	def test_party_display_skips_without_party(self):
		doc = _bare()  # no party attribute
		doc._resolve_party_display()  # must not raise
		self.assertIsNone(doc.get("party_display"))


class _FakeSource:
	"""A Project/Opportunity stand-in carrying the scope child tables."""

	def __init__(self, **tables):
		self._tables = tables

	def get(self, key, default=None):
		return self._tables.get(key, default)


class TestScopeComposition(FrappeTestCase):
	def test_streams_with_content_render_in_order(self):
		source = _FakeSource(
			custom_design_customer_requests=[frappe._dict(design_customer_requests="20ft fountain\nwith LEDs")],
			custom_design_deliverables=[frappe._dict(design_deliverables="Concept package")],
			custom_build_deliverables=[frappe._dict(build_deliverables="Install basin & pumps")],
		)
		out = _compose_scope(source)
		self.assertIn("<h4>Design</h4>", out)
		self.assertIn("<h4>Build</h4>", out)
		self.assertLess(out.index("Design"), out.index("Build"))
		self.assertIn("Customer Requests", out)
		self.assertIn("20ft fountain<br>with LEDs", out)
		self.assertIn("Install basin &amp; pumps", out)  # escaped
		# streams without rows are omitted entirely
		self.assertNotIn("Service", out)
		self.assertNotIn("Events", out)
		# Build has no requests -> no empty Customer Requests block under it
		build_section = out[out.index("<h4>Build</h4>") :]
		self.assertNotIn("Customer Requests", build_section)

	def test_empty_and_blank_rows_yield_nothing(self):
		self.assertEqual(_compose_scope(_FakeSource()), "")
		source = _FakeSource(
			custom_service_customer_requests=[frappe._dict(service_customer_requests="   ")],
		)
		self.assertEqual(_compose_scope(source), "")


class TestTemplatesRender(FrappeTestCase):
	"""Every shipped template must parse and render, empty and populated."""

	def _render(self, body, doc):
		return frappe.render_template(body, _render_context(doc))

	def test_all_templates(self):
		populated = _bare(
			name="SF-OC-0001", template_key="owner", party_display="Acme Corp",
			billing_address="1 Main St\nSLC UT", contact_phone="801-555-0100",
			contact_email="a@acme.test", site_address="2 Site Rd",
			project_title="Lobby Fountain", project="PRJ-0001", contract_date="2026-06-10",
			property_type="Commercial", feature_location="Outdoor",
			msa_tier="Tier 2 - Trade", payment_section_choice="Both",
			visit_frequency="Monthly", invoicing_frequency="Monthly",
			milestones=[frappe._dict(milestone="Mobilization", description="start", due_upon="execution", percent=25.0, amount=1000)],
			milestones_total=1000,
			equipment_items=[frappe._dict(description="Pump", serial_id="SN-1")],
			phases=[frappe._dict(phase_key="design", included=1, fee=5000, retainer=1000)],
			service_options=[frappe._dict(option_key="standard", included=1, price=150, unit="4 visits / month")],
		)
		empty = _bare(
			name="SF-OC-0002", template_key="owner",
			milestones=[], equipment_items=[], phases=[], service_options=[],
			payment_section_choice="Payment Link",
		)
		for filename in sorted(os.listdir(TEMPLATE_DIR)):
			if not filename.endswith(".html"):
				continue
			body = open(os.path.join(TEMPLATE_DIR, filename), encoding="utf-8").read()
			for doc in (populated, empty):
				out = self._render(body, doc)
				self.assertNotIn("{{", out, f"unrendered jinja in {filename}")
				self.assertNotIn("{%", out, f"unrendered jinja block in {filename}")
