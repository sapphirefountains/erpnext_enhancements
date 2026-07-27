"""Seed the "Overhead" Project Type and its five standing overhead projects.

``Purchase Order Item.project`` is mandatory (WI-014 Branch A) so material and
subcontract cost lands on the job. That requirement assumed every PO line has a
legitimate project to point at — but **non-job spend had nowhere to go**. The 13
``Internal`` projects on prod are all specific R&D development jobs
(IDP000-IDP016 plus the ERPNext migration), not overhead buckets, so a purchaser
buying printer paper or shop consumables was simply blocked at save. The WI-014
migration note flagged exactly this as an unmet precondition.

These five projects are that home:

* Overhead - Office & Admin
* Overhead - Shop & Warehouse
* Overhead - Fleet & Vehicles
* Overhead - IT & Software
* Overhead - Marketing & Trade Shows

They sit under their own **Overhead** Project Type rather than ``Internal``,
which keeps overhead spend separable from R&D in reporting and keeps them out of
the Projects Dashboard (that lists only Build/Design/Events/Service/Delivery, so
the exclusion is automatic — see
``project_enhancements/page/project_dashboard/project_dashboard.py``).

Insert-only and idempotent: matched on ``project_name``, existing records are
never modified and nothing is deleted, so a later rename or edit survives
re-migration. Projects are named ``PRJ-#####`` by the Document Naming Rule
(``patches.seed_project_naming_rule``); the picker shows ``project_name``
because Project sets ``show_title_field_in_link``, so "Overhead" typed into the
Project field surfaces all five.
"""

import frappe

from erpnext_enhancements.procurement_project import OVERHEAD_PROJECT_TYPE

PROJECT_TYPE_DESCRIPTION = (
	"Non-job overhead buckets. Purchases that support the business rather than a "
	"specific customer job or R&D project book here, so the mandatory Project on "
	"purchasing lines always has a legitimate target."
)

#: (project_name, notes) — the notes land in Project.notes so a purchaser hovering
#: the bucket can tell what belongs in it.
OVERHEAD_PROJECTS = [
	(
		"Overhead - Office & Admin",
		"General and administrative: office supplies, paper, kitchen/breakroom, "
		"postage, printing, professional services.",
	),
	(
		"Overhead - Shop & Warehouse",
		"Shop and warehouse running costs: consumables, hand tools, safety gear, "
		"cleaning supplies, packaging — anything not billed to a job.",
	),
	(
		"Overhead - Fleet & Vehicles",
		"Company vehicles and trailers: fuel, maintenance, parts, registration, "
		"tires. Job-specific rentals belong on the job, not here.",
	),
	(
		"Overhead - IT & Software",
		"Hardware, software licences and SaaS subscriptions, phones, network gear, IT services.",
	),
	(
		"Overhead - Marketing & Trade Shows",
		"Marketing and business development: trade-show booths and materials, "
		"samples, print/digital advertising, branded merchandise.",
	),
]


def execute():
	if not frappe.db.exists("Project Type", OVERHEAD_PROJECT_TYPE):
		# Project Type autoname is field:project_type, so name == project_type.
		frappe.get_doc(
			{
				"doctype": "Project Type",
				"project_type": OVERHEAD_PROJECT_TYPE,
				"description": PROJECT_TYPE_DESCRIPTION,
			}
		).insert(ignore_permissions=True)

	company = frappe.defaults.get_global_default("company") or frappe.db.get_value("Company", {}, "name")
	if not company:
		# Fresh site with no Company yet — Project.company is mandatory, so there
		# is nothing to attach the buckets to. Nothing is purchasable yet either.
		return

	for project_name, notes in OVERHEAD_PROJECTS:
		if frappe.db.exists("Project", {"project_name": project_name}):
			continue

		doc = frappe.new_doc("Project")
		doc.project_name = project_name
		doc.project_type = OVERHEAD_PROJECT_TYPE
		doc.company = company
		doc.status = "Active"
		doc.is_active = "Yes"
		doc.notes = notes
		# No customer and no custom_opportunity: process_steps.seed_process_steps
		# only seeds a tracker for opportunity-born projects, so these stay clean.
		doc.insert(ignore_permissions=True)
