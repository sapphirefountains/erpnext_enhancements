"""Closed Won -> "Create project now?" prompt (Phase 1 of the hand-off UX).

When an Opportunity transitions into status "Closed Won", the user is asked
whether to create the Project now. This is the *only* entry point to project
creation — the old "Create Project" form button has been removed — so the deal
can't sit "won but unconverted" by accident (PRO-0204 Step 1 -> Step 2).

This module is the server half:

* :func:`prompt_create_project_on_won` (Opportunity ``on_update``) — detects the
  *transition* into Closed Won (mirroring the ``get_doc_before_save`` guard the
  now-removed ``status_alerts.notify_closed_won`` used) and publishes a realtime
  event to the acting user. A global desk script
  (``public/js/crm_enhancements/create_project_prompt.js``) shows the popup, so
  it works from the form, the Kanban board, or anywhere a save lands.
* :func:`revert_won_status` (whitelisted) — the popup's "No": rolls the status
  back to its previous value and clears the won-date stamp.
* :func:`default_project_notify_users` (whitelisted) — the popup's "Yes" dialog
  defaults its "Users to Notify" to the Account Executive + Project Manager role
  holders.

The Closed-Won team SMS is deferred to actual project creation (see
:func:`erpnext_enhancements.crm_enhancements.api.create_project_from_opportunity_background`),
so answering "No" leaves no side effects to undo beyond the status + stamp.
"""

import frappe
from frappe import _

from erpnext_enhancements.status_alerts import _in_maintenance_context

EVENT = "ee_prompt_create_project"
WON_STATUS = "Closed Won"
NOTIFY_ROLES = ("Account Executive", "Project Manager")


def prompt_create_project_on_won(doc, method=None):
	"""Opportunity ``on_update`` — prompt the acting user to create the project.

	Fires once, on the transition into "Closed Won", and only when no project has
	been created yet. Skips bulk/import/migrate contexts (no interactive user to
	prompt). Fire-and-forget: a missed event is recovered by the form's
	reopen-on-load check (see the client script).
	"""
	if doc.status != WON_STATUS:
		return
	if doc.get("custom_created_project"):
		return
	if _in_maintenance_context() or frappe.flags.in_bulk_update:
		return

	before = doc.get_doc_before_save()
	if before and before.status == WON_STATUS:
		return  # already won before this save — not a transition

	frappe.publish_realtime(
		EVENT,
		{
			"opportunity_name": doc.name,
			"previous_status": before.status if before else None,
		},
		user=frappe.session.user,
		after_commit=True,
	)


@frappe.whitelist()
def revert_won_status(opportunity_name, previous_status=None):
	"""Roll an Opportunity back out of "Closed Won" (the popup's "No").

	Restores the prior status and clears ``custom_date_closed_won``. Refuses if a
	Project has already been created (nothing to undo, and the link would be
	orphaned). Because the status is no longer "Closed Won", the rank validation
	(:func:`...script_migrations.opportunity.validate_ranks_on_won`) becomes a
	no-op, so a won-without-ranks edge can't block the rollback.
	"""
	doc = frappe.get_doc("Opportunity", opportunity_name)
	if not doc.has_permission("write"):
		frappe.throw(_("Not permitted to modify this Opportunity."), frappe.PermissionError)
	if doc.get("custom_created_project"):
		frappe.throw(_("A project has already been created for this opportunity."))

	doc.status = previous_status or "Open"
	doc.custom_date_closed_won = None
	doc.save()
	return {"status": doc.status}


@frappe.whitelist()
def default_project_notify_users():
	"""Enabled System Users holding the Account Executive or Project Manager role.

	The default for the "Users to Notify" field in the create-project dialog.
	Falls back to the current user when neither role resolves to anyone, so the
	(required) field is never empty. A role that doesn't exist is skipped.
	"""
	names = set()
	for role in NOTIFY_ROLES:
		if not frappe.db.exists("Role", role):
			continue
		holders = frappe.get_all(
			"Has Role",
			filters={"role": role, "parenttype": "User"},
			pluck="parent",
		)
		names.update(holders)

	if names:
		users = frappe.get_all(
			"User",
			filters={
				"name": ("in", list(names)),
				"enabled": 1,
				"user_type": "System User",
			},
			pluck="name",
		)
		users = [u for u in users if u not in ("Administrator", "Guest")]
		if users:
			return sorted(users)

	return [frappe.session.user]


@frappe.whitelist()
def opportunity_handoff_steps(opportunity_name):
	"""First three hand-off steps of the Opportunity's Project (for the Opportunity tab).

	Returns the linked Project's process steps with ``step_number <= 3`` (Mark Won,
	Hold Hand-Off Meeting, Create Project) — the opportunity -> project handover —
	so the Opportunity's "Hand-Off Process" tab can mirror their live status. The
	full 7-step tracker lives on the Project. Returns an empty list when no Project
	exists yet. Read access is gated on the Opportunity; the step rows (low
	sensitivity) are then read with ``frappe.get_all`` (permissions ignored).
	"""
	if not frappe.has_permission("Opportunity", "read", doc=opportunity_name):
		frappe.throw(_("Not permitted to read this Opportunity."), frappe.PermissionError)
	project = frappe.db.get_value("Opportunity", opportunity_name, "custom_created_project")
	if not project:
		return {"project": None, "steps": []}
	steps = frappe.get_all(
		"Project Process Step",
		filters={"parent": project, "parenttype": "Project", "step_number": ("<=", 3)},
		fields=["step_number", "step_title", "responsible_role", "status", "completed_on", "due_by"],
		order_by="step_number asc",
	)
	return {"project": project, "steps": steps}
