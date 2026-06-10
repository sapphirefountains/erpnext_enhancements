"""Seed the PRO-0204 "Won Opportunity Hand-Off" Process Step Templates.

Creates the seven steps from PRO-0204 v1.0 (04/28/2026) — with the Jun 9
meeting's amendments baked into the descriptions (Step 3 customer-data
verification, Step 4 attendee list incl. the tech lead, Step 5 auto-completing
from the Payment Received checkbox). **Insert-only**: a template whose
``step_number`` already exists is left untouched, so site-side edits
(rewording, SLA tuning, disabling a step) survive re-migrations and this
patch re-running on fresh installs.

SLA semantics: hours after the step becomes *current* before the daily
escalation starts nagging; 0 disables escalation (used for the auto-anchored
steps and for payment, whose timing belongs to the customer, not the rep).
"""

import frappe

STEPS = [
	{
		"step_number": 1,
		"step_title": "Mark Opportunity as Won",
		"responsible_role": "Account Executive",
		"auto_anchor": "Opportunity Won",
		"sla_hours": 0,
		"description": (
			"Mark the opportunity Closed Won in the CRM. The system texts the team "
			"automatically (Sales, Production, Finance) — Finance can start QuickBooks "
			"customer setup immediately, without waiting for the project number."
		),
	},
	{
		"step_number": 2,
		"step_title": "Create Project in PM System",
		"responsible_role": "Project Manager",
		"auto_anchor": "Project Created",
		"sla_hours": 0,
		"description": (
			"Create the project from the won opportunity (this step completes itself "
			"when the project exists). Assign the tech lead while you're here."
		),
	},
	{
		"step_number": 3,
		"step_title": "Create Accounting Project & Send Invoice",
		"responsible_role": "Accounts Receivable",
		"auto_anchor": "",
		"sla_hours": 24,
		"description": (
			"Verify the customer's address and contact details are current FIRST "
			"(per the Jun 9 meeting — this is the data-accuracy checkpoint). Then "
			"create the accounting project using this project number, move the "
			"estimate from the account to the project, and send the invoice."
		),
	},
	{
		"step_number": 4,
		"step_title": "Hold Hand-Off Meeting",
		"responsible_role": "Account Executive",
		"auto_anchor": "",
		"sla_hours": 48,
		"description": (
			"Sales schedules and leads the internal hand-off with the PM and tech "
			"lead (Lisa joins for build projects with a schedule of values). Right "
			"after the production meeting works best; a quick video chat is fine. "
			"Make sure contracts, notes, and preliminary designs are accessible. "
			"Record attendees in the step notes."
		),
	},
	{
		"step_number": 5,
		"step_title": "Receive Customer Payment",
		"responsible_role": "Accounts Receivable",
		"auto_anchor": "Payment Received",
		"sla_hours": 0,
		"description": (
			"Completes itself when the Payment Received box on the Budget tab is "
			"ticked — that also notifies the PM and AE that the project is "
			"financially cleared to proceed. Exceptions (starting before payment) "
			"need James's approval until the parameter guidelines exist."
		),
	},
	{
		"step_number": 6,
		"step_title": "Outline Tasks & Responsibilities in PM System",
		"responsible_role": "Project Manager",
		"auto_anchor": "",
		"sla_hours": 48,
		"description": (
			"Build out the schedule: tasks, milestones, deadlines, assignments. New "
			"projects get the full structure; the open-task count next to this step "
			"is a signal, not a gate."
		),
	},
	{
		"step_number": 7,
		"step_title": "Hold Project Launch Meeting",
		"responsible_role": "Project Manager",
		"auto_anchor": "",
		"sla_hours": 48,
		"description": (
			"The formal kickoff with the production team: scope, deliverables, "
			"timeline, individual assignments. Steps 1-6 all feed this — it only "
			"lands if they're done properly."
		),
	},
]


def execute():
	if not frappe.db.exists("DocType", "Process Step Template"):
		# fresh install ordering safety; doctype sync precedes post_model_sync
		# patches, so this should never trip — belt and suspenders.
		return

	existing = set(
		frappe.get_all("Process Step Template", pluck="step_number")
	)
	for step in STEPS:
		if step["step_number"] in existing:
			continue
		doc = frappe.new_doc("Process Step Template")
		doc.update(step)
		doc.enabled = 1
		doc.insert(ignore_permissions=True)
