"""Seed the five Contract Template records from templates/contracts/*.html.

The Jinja bodies are faithful conversions of Brian's revised agreement suite
(Apr 2026, see scripts/contract_templates/README.md for the regeneration
pipeline). **Insert-only**: an existing template (by ``template_key``) is left
untouched, so legal-text edits made on the site survive re-migrations and
fresh installs still get the full set. To push a *repo-side* template change
to the site after first creation, paste the new body into the Contract
Template record (or write a deliberate patch).
"""

import os

import frappe

TEMPLATES = [
	{
		"template_key": "msa",
		"title": "Master Subcontractor Agreement",
		"party_type": "Supplier",
		"requires_msa": 0,
		"file": "master_subcontractor_agreement.html",
		"description": "Two-tier master agreement (Tier 1 Service & Design / Tier 2 Trade). One per subcontractor; must be signed before any SOW.",
	},
	{
		"template_key": "sow",
		"title": "Statement of Work",
		"party_type": "Supplier",
		"requires_msa": 1,
		"file": "statement_of_work.html",
		"description": "Per-engagement SOW issued under a signed MSA. Scope, schedule, milestone or T&M compensation.",
	},
	{
		"template_key": "owner",
		"title": "Owner Contract",
		"party_type": "Customer",
		"requires_msa": 0,
		"file": "owner_contract.html",
		"description": "Water feature & fountain services agreement - phase-selectable (Design & Engineering / Construction & Installation / Ongoing Maintenance).",
	},
	{
		"template_key": "rental",
		"title": "Rental Agreement",
		"party_type": "Customer",
		"requires_msa": 0,
		"file": "rental_agreement.html",
		"description": "Water feature & fountain equipment rental, with equipment schedule and delivery/return condition checklist.",
	},
	{
		"template_key": "maintenance",
		"title": "Maintenance Services Agreement",
		"party_type": "Customer",
		"requires_msa": 0,
		"file": "maintenance_services_agreement.html",
		"description": "Ongoing maintenance plan agreement. Payment authorization prints as a secure-link instruction and/or a blank card form - card data is never stored in ERPNext.",
	},
	# Retained originals — the Contract Comparison Report keeps these three in
	# active use with no replacement in the revised suite.
	{
		"template_key": "nda",
		"title": "Mutual Non-Disclosure Agreement",
		"party_type": "Any Party",
		"requires_msa": 0,
		"file": "nondisclosure_agreement.html",
		"description": "General mutual NDA (DOC-0033, retained). Counterparty can be a Customer, Supplier, or Employee record.",
	},
	{
		"template_key": "architect",
		"title": "Architect Agreement",
		"party_type": "Customer",
		"requires_msa": 0,
		"file": "architect_agreement.html",
		"description": "Architect engages Sapphire as design subconsultant under their prime agreement with an Owner (DOC-0101, retained). Includes its own embedded SOW page.",
	},
	{
		"template_key": "employee_contractor",
		"title": "Employee-Contractor Agreement",
		"party_type": "Any Party",
		"requires_msa": 0,
		"file": "employee_contractor_agreement.html",
		"description": "Expectations / confidentiality / IP assignment / non-compete for employees and individual contractors (DOC-0137, retained; comparison report recommends a legal review pass).",
	},
]


def execute():
	source_dir = frappe.get_app_path("erpnext_enhancements", "templates", "contracts")
	for spec in TEMPLATES:
		if frappe.db.exists("Contract Template", spec["template_key"]):
			continue
		path = os.path.join(source_dir, spec["file"])
		try:
			with open(path, encoding="utf-8") as f:
				body = f.read()
		except OSError:
			frappe.log_error(
				f"Contract template source missing: {path}\n{frappe.get_traceback()}",
				"seed_contract_templates",
			)
			continue
		doc = frappe.new_doc("Contract Template")
		doc.template_key = spec["template_key"]
		doc.title = spec["title"]
		doc.party_type = spec["party_type"]
		doc.requires_msa = spec["requires_msa"]
		doc.description = spec["description"]
		doc.body = body
		doc.enabled = 1
		doc.insert(ignore_permissions=True)
