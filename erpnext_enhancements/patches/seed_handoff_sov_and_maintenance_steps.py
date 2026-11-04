"""Seed the two hand-off steps the 2026-11-04 process meeting added (v1.795.0).

Clegg Mabey, James Harris, Lisa Symanski and Nikolas Bradshaw aligned on two new
default steps for every new project:

* **8 — Create the Schedule of Values.** The SOV is the contract breakdown by
  phase (100-700) that billing draws against. Until ERPNext holds it (WI-078)
  the company's AIA G702/G703 pay-application workbook is its interim home. It
  runs **in parallel** with the chain from the moment the project exists
  (``process_steps.PARALLEL_STEP_NUMBERS``): the tracker is otherwise strictly
  sequential, and a step numbered after the launch meeting would only open
  once the launch meeting was held, weeks after the meeting wanted it done.
* **9 — Offer a Maintenance Contract.** The closeout question: was a
  maintenance agreement offered to the client? Recorded on the step's own
  ``notes`` (yes/no plus a note) by the Mark Complete dialog in
  ``process_steps.js``. No SLA: it is a closeout, not a clock
  (``process_steps.CLOSEOUT_STEP_NUMBERS``).

**Insert-only**, like ``seed_process_step_templates``: a template whose
``step_number`` already exists is left untouched, so site edits survive. The
Process Step Template has no applicability field, so both steps seed onto every
Opportunity-born project; the SOV description tells a Design-only or service
project to complete it with a note saying no SOV applies.

In-flight projects are **not** back-filled (Jun 9 decision; ``sync_process_steps``
never appends templates). Only projects created after this ships carry 8 and 9.
"""

import frappe

STEPS = [
	{
		"step_number": 8,
		"step_title": "Create the Schedule of Values",
		"responsible_role": "Project Manager",
		"auto_anchor": "",
		"sla_hours": 48,
		"sla_business_days": 2,
		"description": (
			"Build the Schedule of Values: the contract breakdown by phase "
			"(100-700) that billing draws against for every pay application. "
			"Until ERPNext holds it, the company's AIA G702/G703 pay-application "
			"workbook is the interim home - start it from the signed contract and "
			"link or note where it lives. Runs alongside the other steps from the "
			"day the project is created. A Design-only or service project with no "
			"construction contract: mark complete with a note saying no SOV applies."
		),
	},
	{
		"step_number": 9,
		"step_title": "Offer a Maintenance Contract",
		"responsible_role": "Project Manager",
		"auto_anchor": "",
		"sla_hours": 0,
		"sla_business_days": 0,
		"description": (
			"Closeout: was a maintenance agreement offered to the client? Mark "
			"Complete asks Yes or No and takes a note (who was asked, what was "
			"offered, their answer). No due date - this is the last step, recorded "
			"when the project wraps up, not a clock."
		),
	},
]

SAVEPOINT = "seed_handoff_sov_and_maintenance_steps"


def execute():
	try:
		if not frappe.db.exists("DocType", "Process Step Template"):
			return
		frappe.db.savepoint(SAVEPOINT)
		try:
			inserted = seed_steps()
		except Exception:
			frappe.db.rollback(save_point=SAVEPOINT)
			raise
		print(f"Hand-off steps 8 and 9: inserted {inserted}")
	except Exception:
		# A patch that raises aborts bench migrate, which on this repo is the
		# deploy. Two template rows are not worth a half-applied release.
		frappe.log_error(
			f"seed_handoff_sov_and_maintenance_steps failed:\n{frappe.get_traceback()}",
			"Hand-off step seed failed",
		)


def seed_steps():
	"""Insert the templates whose ``step_number`` is not yet on the site."""
	existing = set(frappe.get_all("Process Step Template", pluck="step_number"))
	inserted = []
	for step in STEPS:
		if step["step_number"] in existing:
			continue
		doc = frappe.new_doc("Process Step Template")
		doc.update(step)
		doc.enabled = 1
		doc.insert(ignore_permissions=True)
		inserted.append(step["step_number"])
	return inserted
