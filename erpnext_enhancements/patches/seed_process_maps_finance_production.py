"""Seed the Finance + Production process maps (Business Process Mapping program, Phase 0).

Authors one ``Process Document`` per mapped process — the human-readable Mermaid
flow plus the structured ``Process Document Step`` RACI grid that drives the
later access-control / workflow phases. People are from the owner's process
interview (Jun 2026): Lisa Symanski (finance), James Harris (approver/principal),
Brian Morisseau (sales), Clegg Mabey (PM/production), Nikolas Bradshaw (admin/QC),
John Juntunen (external accountant — compensating reviewer).

**Insert-only**: a Process Document whose title already exists is left untouched,
so site-side edits (renaming people, retitling steps, filling more names) survive
re-migration and fresh installs. Delete a Process Document on the site to let this
patch re-seed it from scratch.

The ``erpnext_doctype`` Link is only set when that DocType actually exists on the
site (e.g. "Month-End Close" doesn't exist until the Phase 4 build), so the patch
never fails on link validation on a partial/fresh site.
"""

import frappe

# Coverage / enforcement / action values must match the Process Document Step Select options.
PROCESSES = [
	{
		"title": "Finance — Bill Payment (AP)",
		"mermaid_code": (
			"flowchart TD\n"
			'    A["Bill arrives — email / upload / Drive (Lisa)"] --> B["Document Intake captures &amp; de-dupes"]\n'
			'    B --> C["AI extraction: vendor, amount, lines, PO match"]\n'
			'    C --> D["Review &amp; match queue (Lisa)"]\n'
			'    D --> E["Draft Purchase Invoice created"]\n'
			'    E --> F{"Approve the bill? (James / Lisa)"}\n'
			"    F -- Reject --> D\n"
			'    F -- Approve --> G["Purchase Invoice submitted to A/P"]\n'
			'    G --> H["Payment Entry — pick method: check / ACH / card / autopay (Lisa / James)"]\n'
			'    H --> I["Payment issued"]\n'
			'    I --> J["Reconcile vs bank / QBO Balance Comparison (Lisa)"]\n'
		),
		"steps": [
			{
				"step_no": 1, "step_title": "Receive incoming bill",
				"responsible": "Lisa Symanski", "accountable": "Lisa Symanski",
				"erpnext_doctype": "Document Intake", "erpnext_action": "Draft / Create",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "accounting_intake (Upload / Email / Drive / Mobile / Chat channels)",
				"notes": "Bills flow into the Document Intake queue automatically.",
			},
			{
				"step_no": 2, "step_title": "Enter / review bill → draft Purchase Invoice",
				"responsible": "Lisa Symanski", "accountable": "Lisa Symanski",
				"erpnext_doctype": "Document Intake", "erpnext_action": "Review",
				"enforcement": "DocPerm", "coverage": "Built / Existing",
				"target_artifact": "accounting_intake/actions/vendor_bill.py (3-way match if PO)",
				"notes": "AI extracts + matches; reviewer confirms. Role: Accounts User.",
			},
			{
				"step_no": 3, "step_title": "Approve the bill",
				"responsible": "James Harris / Lisa Symanski", "accountable": "James Harris",
				"consulted": "John Juntunen (external accountant, compensating review)",
				"erpnext_doctype": "Purchase Invoice", "erpnext_action": "Approve",
				"enforcement": "Workflow Transition", "coverage": "Gap — To Build",
				"target_artifact": "fixtures/workflow.json — PI approval (allowed: Accounts Manager)",
				"notes": "Phase 2 §4a. Lisa enters; James (or Lisa) approves — workflow records who/when.",
			},
			{
				"step_no": 4, "step_title": "Submit / post the bill to A/P",
				"responsible": "James Harris / Lisa Symanski", "accountable": "James Harris",
				"erpnext_doctype": "Purchase Invoice", "erpnext_action": "Submit",
				"enforcement": "Workflow Transition", "coverage": "Gap — To Build",
				"target_artifact": "fixtures/workflow.json — Approved state (docstatus 1)",
				"notes": "Approval transition auto-submits the PI.",
			},
			{
				"step_no": 5, "step_title": "Issue payment",
				"responsible": "Lisa Symanski / James Harris", "accountable": "James Harris",
				"erpnext_doctype": "Payment Entry", "erpnext_action": "Pay / Receive",
				"enforcement": "Workflow Transition", "coverage": "Config Needed",
				"target_artifact": "fixtures/workflow.json — Payment Entry approval (allowed: Accounts Manager)",
				"notes": "Phase 2 §4b. Single approver (James); no $ threshold set yet.",
			},
			{
				"step_no": 6, "step_title": "Determine payment method (check / ACH / card / autopay)",
				"responsible": "Lisa Symanski / James Harris", "accountable": "James Harris",
				"erpnext_doctype": "Payment Entry", "erpnext_action": "Other",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "Mode of Payment field (QBO methods imported)",
				"notes": "Field exists; the choice itself is a manual judgement.",
			},
			{
				"step_no": 7, "step_title": "Reconcile the payment",
				"responsible": "Lisa Symanski", "accountable": "Lisa Symanski",
				"erpnext_doctype": "", "erpnext_action": "Reconcile",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "quickbooks_online — QuickBooks Balance Comparison report",
				"notes": "Read-only reconciliation report assists; sign-off manual.",
			},
		],
	},
	{
		"title": "Finance — Customer Invoicing (AR)",
		"mermaid_code": (
			"flowchart TD\n"
			'    A["Opportunity marked Closed-Won — sale approved (Brian / Lisa / James)"] --> B["Hand-off engine creates Project"]\n'
			'    B --> C["Create Sales Invoice (Brian / Lisa / James)"]\n'
			'    C --> D["Submit &amp; issue to customer — email / portal / Stripe link (Lisa)"]\n'
			'    D --> E{"Autopay enrolled?"}\n'
			'    E -- Yes --> F["Auto-charge saved method on submit"]\n'
			'    E -- No --> G["Customer pays via Stripe Checkout (card / ACH)"]\n'
			"    F --> H[\"Stripe webhook posts Payment Entry\"]\n"
			"    G --> H\n"
			'    H --> I["Reconcile receipt (Lisa)"]\n'
		),
		"steps": [
			{
				"step_no": 1, "step_title": "Approve the sale (mark Closed-Won)",
				"responsible": "Lisa Symanski / Brian Morisseau / James Harris", "accountable": "Brian Morisseau",
				"erpnext_doctype": "Opportunity", "erpnext_action": "Approve",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "process_steps.py — Closed-Won hand-off engine",
				"notes": "Closed-Won triggers the 7-step hand-off + Project creation.",
			},
			{
				"step_no": 2, "step_title": "Create the Sales Invoice",
				"responsible": "Lisa Symanski / Brian Morisseau / James Harris", "accountable": "Lisa Symanski",
				"erpnext_doctype": "Sales Invoice", "erpnext_action": "Draft / Create",
				"enforcement": "DocPerm", "coverage": "Built / Existing",
				"target_artifact": "Sales Invoice (manual). Optional auto-SI from Opportunity = §4e",
				"notes": "Today manual; auto-SI from Opportunity/Quotation is an optional gap.",
			},
			{
				"step_no": 3, "step_title": "Issue the invoice to the customer",
				"responsible": "Lisa Symanski", "accountable": "Lisa Symanski",
				"erpnext_doctype": "Sales Invoice", "erpnext_action": "Submit",
				"enforcement": "DocPerm", "coverage": "Built / Existing",
				"target_artifact": "stripe_payments — Checkout link / email / portal",
				"notes": "Submit + send; Stripe Checkout link attached.",
			},
			{
				"step_no": 4, "step_title": "Collect payment (card / ACH / autopay)",
				"responsible": "Lisa Symanski", "accountable": "Lisa Symanski",
				"erpnext_doctype": "Payment Entry", "erpnext_action": "Pay / Receive",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "stripe_payments/core/reconcile.py — webhook → Payment Entry",
				"notes": "Autopay auto-charges on SI submit; otherwise customer pays via Checkout.",
			},
			{
				"step_no": 5, "step_title": "Reconcile the receipt",
				"responsible": "Lisa Symanski", "accountable": "Lisa Symanski",
				"erpnext_doctype": "", "erpnext_action": "Reconcile",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "Bank Reconciliation Tool / QBO Balance Comparison",
			},
		],
	},
	{
		"title": "Finance — Month-End Close",
		"mermaid_code": (
			"flowchart TD\n"
			'    A["QBO sync posts the month\'s transactions"] --> B["Record periodic / manual journal entries (Lisa)"]\n'
			'    B --> C["Reconcile balance sheet — QBO Balance Comparison (Lisa)"]\n'
			'    C --> D["Reconcile cash &amp; credit cards — Bank Rec (Lisa)"]\n'
			'    D --> E["Post adjusting entries (Lisa)"]\n'
			'    E --> F["Review with external accountant (John Juntunen)"]\n'
			'    F --> G{"Approve financial statements? (Lisa)"}\n'
			"    G -- Changes --> E\n"
			'    G -- Approve --> H["Close period — set Acc Frozen Upto (Lisa)"]\n'
			'    H --> I["Period locked — no back-posting"]\n'
		),
		"steps": [
			{
				"step_no": 1, "step_title": "Record periodic / manual entries",
				"responsible": "Lisa Symanski", "accountable": "Lisa Symanski",
				"erpnext_doctype": "Journal Entry", "erpnext_action": "Draft / Create",
				"enforcement": "DocPerm", "coverage": "Built / Existing",
				"target_artifact": "quickbooks_online sync (auto) + manual Journal Entry",
			},
			{
				"step_no": 2, "step_title": "Reconcile the balance sheet",
				"responsible": "Lisa Symanski", "accountable": "Lisa Symanski",
				"erpnext_doctype": "", "erpnext_action": "Reconcile",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "QuickBooks Balance Comparison report",
			},
			{
				"step_no": 3, "step_title": "Reconcile cash & credit cards",
				"responsible": "Lisa Symanski", "accountable": "Lisa Symanski",
				"erpnext_doctype": "", "erpnext_action": "Reconcile",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "Bank Reconciliation Tool",
			},
			{
				"step_no": 4, "step_title": "Make adjusting entries",
				"responsible": "Lisa Symanski", "accountable": "Lisa Symanski",
				"erpnext_doctype": "Journal Entry", "erpnext_action": "Draft / Create",
				"enforcement": "DocPerm", "coverage": "Built / Existing",
			},
			{
				"step_no": 5, "step_title": "Approve the financial statements",
				"responsible": "Lisa Symanski", "accountable": "Lisa Symanski",
				"consulted": "John Juntunen (external accountant — second set of eyes)",
				"informed": "James Harris",
				"erpnext_doctype": "", "erpnext_action": "Approve",
				"enforcement": "Workflow Transition", "coverage": "Gap — To Build",
				"target_artifact": "Phase 4 §4d — Month-End Close doctype + checklist",
				"notes": "John Juntunen is the compensating reviewer (Lisa both does and approves close).",
			},
			{
				"step_no": 6, "step_title": "Lock the period",
				"responsible": "Lisa Symanski", "accountable": "Lisa Symanski",
				"erpnext_doctype": "Accounts Settings", "erpnext_action": "Other",
				"enforcement": "Workflow Transition", "coverage": "Gap — To Build",
				"target_artifact": "Accounts Settings → Acc Frozen Upto (set on close, Phase 4)",
				"notes": "The real teeth: no one can post into a closed period.",
			},
		],
	},
	{
		"title": "Finance — Job Costing",
		"mermaid_code": (
			"flowchart TD\n"
			'    A["Bid cost baseline set at hand-off (Clegg / Brian)"] --> B["Actuals accrue to Project: PO + Timesheet + Stock Entry"]\n'
			'    B --> C["Job costing: cost-to-date vs contract value &amp; bid (Lisa)"]\n'
			'    C --> D["Gross-margin-vs-bid KPI"]\n'
			'    D --> E{"Review &amp; approve job financials (Lisa / James)"}\n'
			"    E -- Issue --> B\n"
			'    E -- OK --> F["Job margin signed off"]\n'
		),
		"steps": [
			{
				"step_no": 1, "step_title": "Set the bid-cost baseline at hand-off",
				"responsible": "Clegg Mabey / Brian Morisseau", "accountable": "Clegg Mabey",
				"erpnext_doctype": "Project", "erpnext_action": "Draft / Create",
				"enforcement": "DocPerm", "coverage": "Gap — To Build",
				"target_artifact": "Phase 3 §5 — Project.custom_bid_cost field",
				"notes": "Without a bid baseline the margin-vs-bid KPI can't compute.",
			},
			{
				"step_no": 2, "step_title": "Capture project actual costs",
				"responsible": "(system) / Lisa Symanski", "accountable": "Lisa Symanski",
				"erpnext_doctype": "Project", "erpnext_action": "Report",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "PO + Timesheet + Stock Entry rollup by project",
			},
			{
				"step_no": 3, "step_title": "Perform job costing (cost vs value)",
				"responsible": "Lisa Symanski", "accountable": "Lisa Symanski",
				"erpnext_doctype": "Project", "erpnext_action": "Report",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "KPI: Project Gross Margin vs Bid",
			},
			{
				"step_no": 4, "step_title": "Review & approve financials per job",
				"responsible": "Lisa Symanski / James Harris", "accountable": "James Harris",
				"erpnext_doctype": "Project", "erpnext_action": "Approve",
				"enforcement": "Manual", "coverage": "Manual / Process-Only",
				"notes": "Sign-off is a process step; no doc gate planned for now.",
			},
		],
	},
	{
		"title": "Production — Job Production",
		"mermaid_code": (
			"flowchart TD\n"
			'    A["Job sold — kicked off from Closed-Won (Brian / Clegg)"] --> B["Assign responsible PM (Clegg)"]\n'
			'    B --> C["Design complete &amp; handed to production"]\n'
			'    C --> D["Procurement: Material Request → PO → Receipt"]\n'
			'    D --> E["Assembly / fabrication — quality &amp; efficiency monitored (Clegg / James / Nick / Lisa)"]\n'
			'    E --> F{"QA &amp; quality sign-off (Clegg)"}\n'
			"    F -- Fail --> E\n"
			'    F -- Pass --> G["Ready for install"]\n'
			'    G --> H["Installed on site"]\n'
			'    H --> I["Commissioned &amp; handed to maintenance"]\n'
		),
		"steps": [
			{
				"step_no": 1, "step_title": "Initiate / kick off the job",
				"responsible": "Brian Morisseau / Clegg Mabey", "accountable": "Clegg Mabey",
				"erpnext_doctype": "Project", "erpnext_action": "Draft / Create",
				"enforcement": "Manual", "coverage": "Built / Existing",
				"target_artifact": "process_steps.py — Closed-Won → Project hand-off",
			},
			{
				"step_no": 2, "step_title": "Assign the responsible PM",
				"responsible": "Clegg Mabey", "accountable": "Clegg Mabey",
				"erpnext_doctype": "Project", "erpnext_action": "Other",
				"enforcement": "DocPerm", "coverage": "Config Needed",
				"target_artifact": "Project.custom_project_owner (set on Project)",
			},
			{
				"step_no": 3, "step_title": "Advance the build status (procurement → assembly → ready)",
				"responsible": "Clegg Mabey", "accountable": "Clegg Mabey",
				"erpnext_doctype": "Project", "erpnext_action": "Other",
				"enforcement": "Workflow Transition", "coverage": "Gap — To Build",
				"target_artifact": "Phase 3 §5 — Project.custom_build_status + Production Day Board",
			},
			{
				"step_no": 4, "step_title": "Monitor quality & efficiency",
				"responsible": "Clegg Mabey / James Harris / Nikolas Bradshaw / Lisa Symanski",
				"accountable": "Clegg Mabey",
				"erpnext_doctype": "Project", "erpnext_action": "Review",
				"enforcement": "Row Filter", "coverage": "Gap — To Build",
				"target_artifact": "Phase 3 §5 — Production Day Board (mirrors Maintenance Day Board)",
			},
			{
				"step_no": 5, "step_title": "QA / quality sign-off before install",
				"responsible": "Clegg Mabey", "accountable": "Clegg Mabey",
				"erpnext_doctype": "Project", "erpnext_action": "Approve",
				"enforcement": "Workflow Transition", "coverage": "Gap — To Build",
				"target_artifact": "Phase 3 §5 — custom_quality_sign_off_by / _date; QC gate",
			},
		],
	},
]


def execute():
	if not frappe.db.exists("DocType", "Process Document") or not frappe.db.exists(
		"DocType", "Process Document Step"
	):
		# doctype sync precedes post_model_sync patches — belt and suspenders.
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
			# Only link a DocType that actually exists on this site (future
			# doctypes like "Month-End Close" land in later phases).
			if dt and not frappe.db.exists("DocType", dt):
				row["erpnext_doctype"] = ""
			doc.append("steps", row)
		doc.insert(ignore_permissions=True)
