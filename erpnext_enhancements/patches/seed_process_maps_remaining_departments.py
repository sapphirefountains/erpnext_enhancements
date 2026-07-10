"""Seed the remaining-department process maps (Business Process Mapping, Phase 5).

Adds one ``Process Document`` per department for the six departments not covered
by Phase 0 (Finance/Production): **Sales, Design, Operations, Marketing, Product
Management, Executive** — each with its Mermaid flow plus the structured
``Process Document Step`` RACI grid. People are from the owner's Jun 2026 process
interview. Most steps are already automated in the app (coverage = Built /
Existing); the maps document who owns each and how it's enforced.

**Insert-only**: a Process Document whose title already exists is left untouched,
so site-side edits survive re-migration. ``erpnext_doctype`` links are only set
when that DocType exists on the site, so the patch never fails on link validation.
"""

import frappe

PROCESSES = [
	{
		"title": "Sales — Lead to Closed-Won",
		"mermaid_code": (
			"flowchart TD\n"
			'    A["Capture lead + set source (Brian)"] --> B["Qualify and advance in pipeline (Brian)"]\n'
			'    B --> C["Build quote / proposal (Brian)"]\n'
			'    C --> D{"Won or Lost? (Brian)"}\n'
			'    D -- Lost --> E["Record lost reason"]\n'
			'    D -- Won --> F["Mark Closed-Won + reason (Brian)"]\n'
			'    F --> G["Convert Opportunity to Project (Brian)"]\n'
			'    G --> H["Run 7-step hand-off to PM (Brian to Clegg)"]\n'
		),
		"steps": [
			{
				"step_no": 1, "step_title": "Capture lead & set lead source",
				"responsible": "Brian Morisseau", "accountable": "Brian Morisseau",
				"erpnext_doctype": "Lead", "erpnext_action": "Draft / Create",
				"enforcement": "DocPerm", "coverage": "Built / Existing",
				"target_artifact": "Lead.source — feeds Marketing attribution",
				"notes": "Brian is the sole salesperson.",
			},
			{
				"step_no": 2, "step_title": "Qualify & advance the opportunity",
				"responsible": "Brian Morisseau", "accountable": "Brian Morisseau",
				"erpnext_doctype": "Opportunity", "erpnext_action": "Other",
				"enforcement": "DocPerm", "coverage": "Built / Existing",
				"target_artifact": "crm_enhancements Sales Pipeline stages + days-in-stage aging",
			},
			{
				"step_no": 3, "step_title": "Build quote / proposal",
				"responsible": "Brian Morisseau", "accountable": "Brian Morisseau",
				"erpnext_doctype": "Quotation", "erpnext_action": "Draft / Create",
				"enforcement": "DocPerm", "coverage": "Built / Existing",
			},
			{
				"step_no": 4, "step_title": "Won/Lost decision + record reason",
				"responsible": "Brian Morisseau", "accountable": "Brian Morisseau",
				"erpnext_doctype": "Opportunity", "erpnext_action": "Approve",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "custom_lost_reason (required when marking Lost)",
			},
			{
				"step_no": 5, "step_title": "Convert won Opportunity to Project",
				"responsible": "Brian Morisseau", "accountable": "Brian Morisseau",
				"erpnext_doctype": "Opportunity", "erpnext_action": "Other",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "crm_enhancements/api.py enqueue_project_creation",
			},
			{
				"step_no": 6, "step_title": "Run the 7-step hand-off to the PM",
				"responsible": "Brian Morisseau", "accountable": "Clegg Mabey",
				"informed": "Lisa Symanski (AR setup step)",
				"erpnext_doctype": "Project", "erpnext_action": "Other",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "process_steps.py PRO-0204 hand-off engine (SLA + escalation)",
			},
		],
	},
	{
		"title": "Design — Water Feature Design",
		"mermaid_code": (
			"flowchart TD\n"
			'    A["Create design + gather inputs (Daniel / Nathan)"] --> B["Run calc + select pump (system-assisted)"]\n'
			'    B --> C{"Review and validate (James Harris)"}\n'
			"    C -- Changes --> A\n"
			'    C -- OK --> D["Issue design (Daniel / Nathan)"]\n'
			'    D --> E["Control panel / electrical design (Daniel Blass)"]\n'
			'    E --> F["Hand to production"]\n'
		),
		"steps": [
			{
				"step_no": 1, "step_title": "Create design & gather inputs",
				"responsible": "Daniel Blass / Nathan Cox", "accountable": "Daniel Blass",
				"erpnext_doctype": "Water Feature Design", "erpnext_action": "Draft / Create",
				"enforcement": "DocPerm", "coverage": "Built / Existing",
				"target_artifact": "water_engineering WFD (Draft → Inputs Gathered)",
			},
			{
				"step_no": 2, "step_title": "Run calc & select pump",
				"responsible": "Daniel Blass / Nathan Cox", "accountable": "Daniel Blass",
				"erpnext_doctype": "Water Feature Design", "erpnext_action": "Other",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "engine.run_spine — status Calculated; pump auto-selected",
			},
			{
				"step_no": 3, "step_title": "Review & validate the design",
				"responsible": "James Harris", "accountable": "James Harris",
				"erpnext_doctype": "Water Feature Design", "erpnext_action": "Review",
				"enforcement": "Manual", "coverage": "Manual / Process-Only",
				"target_artifact": "status Reviewed (manual gate; a workflow could enforce later)",
			},
			{
				"step_no": 4, "step_title": "Issue design & hand to production",
				"responsible": "Daniel Blass / Nathan Cox", "accountable": "Daniel Blass",
				"erpnext_doctype": "Water Feature Design", "erpnext_action": "Submit",
				"enforcement": "DocPerm", "coverage": "Built / Existing",
				"target_artifact": "status Issued (before_submit completeness gate)",
			},
			{
				"step_no": 5, "step_title": "Control panel / electrical design",
				"responsible": "Daniel Blass", "accountable": "Daniel Blass",
				"erpnext_doctype": "Control Panel Design", "erpnext_action": "Draft / Create",
				"enforcement": "DocPerm", "coverage": "Built / Existing",
				"target_artifact": "control_panel_design (interlocks/IO auto-seeded)",
			},
		],
	},
	{
		"title": "Operations — Maintenance Visit Lifecycle",
		"mermaid_code": (
			"flowchart TD\n"
			'    A["Set up maintenance contract (Clegg)"] --> B["Bill the contract (Lisa)"]\n'
			'    B --> C["Scheduler auto-drafts visits"]\n'
			'    C --> D["Technician performs the visit (crew)"]\n'
			'    D --> E{"Supervisor review (Austin / Clegg)"}\n'
			"    E -- Revise --> D\n"
			'    E -- Approve --> F["Submit: Stock Entry + Timesheet + Invoice + Warranty (auto)"]\n'
			'    F --> G["Next-visit dates roll forward (auto)"]\n'
		),
		"steps": [
			{
				"step_no": 1, "step_title": "Set up the maintenance contract",
				"responsible": "Clegg Mabey", "accountable": "Clegg Mabey",
				"erpnext_doctype": "Sapphire Maintenance Contract", "erpnext_action": "Draft / Create",
				"enforcement": "DocPerm", "coverage": "Built / Existing",
			},
			{
				"step_no": 2, "step_title": "Bill the contract",
				"responsible": "Lisa Symanski", "accountable": "Lisa Symanski",
				"erpnext_doctype": "Sales Invoice", "erpnext_action": "Pay / Receive",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "auto-drafted per-visit SI when contract bills Per Visit",
			},
			{
				"step_no": 3, "step_title": "Scheduler auto-drafts the visit",
				"responsible": "(system)", "accountable": "Clegg Mabey",
				"erpnext_doctype": "Sapphire Maintenance Record", "erpnext_action": "Draft / Create",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "daily predictive_maintenance scheduler",
			},
			{
				"step_no": 4, "step_title": "Technician performs the visit",
				"responsible": "Daniel Blass / Nathan Cox / Austin Healey / Lian Silva / Korben Jessop / Danny Rosser / Jesse Griffin",
				"accountable": "Clegg Mabey",
				"erpnext_doctype": "Sapphire Maintenance Record", "erpnext_action": "Submit",
				"enforcement": "Workflow Transition", "coverage": "Built / Existing",
				"target_artifact": "Visit Wizard; Maintenance Workflow Draft → Pending Review (Maintenance User)",
				"notes": "Crew need the Maintenance User role.",
			},
			{
				"step_no": 5, "step_title": "Supervisor reviews & approves the visit",
				"responsible": "Austin Healey / Clegg Mabey", "accountable": "Clegg Mabey",
				"erpnext_doctype": "Sapphire Maintenance Record", "erpnext_action": "Approve",
				"enforcement": "Workflow Transition", "coverage": "Built / Existing",
				"target_artifact": "Maintenance Workflow Pending Review → Final/Submitted (Projects Manager)",
				"notes": "Approvers need Projects Manager (or Maintenance Manager) role.",
			},
			{
				"step_no": 6, "step_title": "On-submit automation + next-visit rollover",
				"responsible": "(system)", "accountable": "Clegg Mabey",
				"erpnext_doctype": "Sapphire Maintenance Record", "erpnext_action": "Other",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "Stock Entry + Timesheet + Sales Invoice + Warranty Claim + next_visit_date",
			},
		],
	},
	{
		"title": "Marketing — Lead Source & Spend",
		"mermaid_code": (
			"flowchart TD\n"
			'    A["Tag lead source on every lead (Richard)"] --> B["Enter monthly marketing spend (Richard)"]\n'
			'    B --> C["Nightly KPI snapshot computes CPL / leads / web metrics"]\n'
			'    C --> D["Review CPL, lead conversion, GA4/GSC web metrics (Richard)"]\n'
		),
		"steps": [
			{
				"step_no": 1, "step_title": "Own lead-source tagging",
				"responsible": "Richard Hansen", "accountable": "Richard Hansen",
				"informed": "Brian Morisseau",
				"erpnext_doctype": "Lead", "erpnext_action": "Other",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "Lead.source — drives marketing attribution + unsourced-lead KPI",
			},
			{
				"step_no": 2, "step_title": "Enter monthly marketing spend",
				"responsible": "Richard Hansen", "accountable": "Richard Hansen",
				"erpnext_doctype": "Marketing Spend", "erpnext_action": "Draft / Create",
				"enforcement": "DocPerm", "coverage": "Built / Existing",
				"target_artifact": "Marketing Spend doctype — CPL numerator",
			},
			{
				"step_no": 3, "step_title": "Review CPL / lead / web metrics",
				"responsible": "Richard Hansen", "accountable": "Richard Hansen",
				"erpnext_doctype": "", "erpnext_action": "Report",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "KPI Marketing dashboard (_marketing_metrics: CPL, conversion, GA4/GSC)",
			},
		],
	},
	{
		"title": "Product Management — Catalog, Inventory & Rentals",
		"mermaid_code": (
			"flowchart TD\n"
			'    A["Maintain item catalog / SKUs / pump specs (Parker)"] --> B["Monitor inventory and reorder levels (Parker)"]\n'
			'    B --> C["Manage rental projects (Clegg / Brian)"]\n'
			'    C --> D["Review catalog revenue and data-quality KPIs (Parker)"]\n'
		),
		"steps": [
			{
				"step_no": 1, "step_title": "Maintain item catalog / SKUs / pump specs",
				"responsible": "Parker Bailey", "accountable": "Parker Bailey",
				"erpnext_doctype": "Item", "erpnext_action": "Draft / Create",
				"enforcement": "DocPerm", "coverage": "Built / Existing",
				"target_artifact": "Item master (custom_sku, pump specs) — SKU completeness KPIs",
			},
			{
				"step_no": 2, "step_title": "Monitor inventory & reorder levels",
				"responsible": "Parker Bailey", "accountable": "Parker Bailey",
				"erpnext_doctype": "Item Reorder", "erpnext_action": "Review",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "Bin.stock_value + Item Reorder — items-below-reorder KPI",
			},
			{
				"step_no": 3, "step_title": "Manage rental projects",
				"responsible": "Clegg Mabey / Brian Morisseau", "accountable": "Clegg Mabey",
				"erpnext_doctype": "Project", "erpnext_action": "Draft / Create",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "Project project_type='Rent' — rental KPIs",
			},
			{
				"step_no": 4, "step_title": "Review catalog revenue & data quality",
				"responsible": "Parker Bailey", "accountable": "Parker Bailey",
				"erpnext_doctype": "", "erpnext_action": "Report",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "KPI Product dashboard (_product_metrics)",
			},
		],
	},
	{
		"title": "Executive — KPI Oversight",
		"mermaid_code": (
			"flowchart TD\n"
			'    A["Nightly batch builds per-dept KPI snapshots (auto)"] --> B["Executive rollup aggregates Finance/Sales/Ops/Production/Marketing (auto)"]\n'
			'    B --> C["Review company KPI dashboards (James / Nikolas)"]\n'
			'    C --> D["Approve financials and major decisions (James)"]\n'
		),
		"steps": [
			{
				"step_no": 1, "step_title": "Nightly KPI snapshot (all departments)",
				"responsible": "(system)", "accountable": "Nikolas Bradshaw",
				"erpnext_doctype": "KPI Snapshot", "erpnext_action": "Other",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "kpi_dashboards/snapshots.py nightly batch",
			},
			{
				"step_no": 2, "step_title": "Executive rollup of curated KPIs",
				"responsible": "(system)", "accountable": "Nikolas Bradshaw",
				"erpnext_doctype": "KPI Snapshot", "erpnext_action": "Other",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "_executive_metrics + source-freshness watch",
			},
			{
				"step_no": 3, "step_title": "Review company KPI dashboards",
				"responsible": "James Harris / Nikolas Bradshaw", "accountable": "James Harris",
				"erpnext_doctype": "", "erpnext_action": "Report",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "KPI Cockpit / Executive dashboard",
			},
			{
				"step_no": 4, "step_title": "Approve financials & major decisions",
				"responsible": "James Harris", "accountable": "James Harris",
				"erpnext_doctype": "", "erpnext_action": "Approve",
				"enforcement": "Manual", "coverage": "Manual / Process-Only",
				"target_artifact": "executive sign-off (process step)",
			},
		],
	},
]


def execute():
	if not frappe.db.exists("DocType", "Process Document") or not frappe.db.exists(
		"DocType", "Process Document Step"
	):
		return

	for proc in PROCESSES:
		if frappe.db.exists("Process Document", proc["title"]):
			continue
		doc = frappe.new_doc("Process Document")
		doc.title = proc["title"]
		doc.mermaid_code = proc["mermaid_code"]
		for step in proc["steps"]:
			row = dict(step)
			dt = row.get("erpnext_doctype")
			if dt and not frappe.db.exists("DocType", dt):
				row["erpnext_doctype"] = ""
			doc.append("steps", row)
		doc.insert(ignore_permissions=True)
