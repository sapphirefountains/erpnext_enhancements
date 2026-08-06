"""Create the Opportunity hand-off gate fields and exempt the legacy backlog (v1.250.0).

Two jobs, and the second is the load-bearing one.

**1. Create the fields.** The nine Custom Fields ship in ``fixtures/custom_field.json``,
but patches run *before* fixture sync, and the backfill patch that follows this one
needs the columns to exist. Same backstop shape as
``ensure_opportunity_handoff_fields``: idempotent ``create_custom_fields``, then
normalise ``is_system_generated`` back to 0 so the fixtures stay the source of truth.

**2. Stamp the gate exemption.** ``custom_handoff_gate_applies`` is what decides
whether an Opportunity is subject to the "no project without a hand-off" gate, and it
is set only on a *genuine transition* into Closed Won (see
``script_migrations.opportunity.stamp_handoff_gate``). The 227 Opportunities already
sitting in Closed Won never transition again, so they would simply never acquire it —
but relying on that silence is fragile. A blank Check reads as 0 whether it was
decided or merely never written, and the two are not the same thing. This writes the
0 explicitly, so "exempt" is a stored fact somebody can query and the report can
count, rather than an absence.

The exemption is deliberate: the meeting's decision was to gate deals won *from now
on*, not to freeze a 227-deal backlog behind meetings nobody is going to hold
retroactively. That backlog is WI-024's problem. If the team later wants them gated,
that is a patch flipping this flag — not a code change.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

FIELDS = [
	{
		"fieldname": "custom_handoff_meeting_held",
		"label": "Hand-Off Meeting Held",
		"fieldtype": "Check",
		"default": "0",
		"insert_after": "custom_process_progress",
		"read_only": 1,
		"no_copy": 1,
	},
	{
		"fieldname": "custom_handoff_meeting_on",
		"label": "Hand-Off Meeting Recorded On",
		"fieldtype": "Datetime",
		"insert_after": "custom_handoff_meeting_held",
		"read_only": 1,
		"no_copy": 1,
	},
	{
		"fieldname": "custom_handoff_meeting_by",
		"label": "Hand-Off Recorded By",
		"fieldtype": "Link",
		"options": "User",
		"insert_after": "custom_handoff_meeting_on",
		"read_only": 1,
		"no_copy": 1,
	},
	{
		"fieldname": "custom_handoff_event",
		"label": "Hand-Off Meeting Event",
		"fieldtype": "Link",
		"options": "Event",
		"insert_after": "custom_handoff_meeting_by",
		"read_only": 1,
		"no_copy": 1,
	},
	{
		"fieldname": "custom_handoff_skip_reason",
		"label": "Hand-Off Skip Reason",
		"fieldtype": "Small Text",
		"insert_after": "custom_handoff_event",
		"read_only": 1,
		"no_copy": 1,
	},
	{
		"fieldname": "custom_handoff_due_by",
		"label": "Hand-Off Due By",
		"fieldtype": "Datetime",
		"insert_after": "custom_handoff_skip_reason",
		"read_only": 1,
		"no_copy": 1,
	},
	{
		"fieldname": "custom_launch_deadline",
		"label": "Project Launch Deadline",
		"fieldtype": "Datetime",
		"insert_after": "custom_handoff_due_by",
		"read_only": 1,
		"no_copy": 1,
	},
	{
		"fieldname": "custom_handoff_gate_applies",
		"label": "Hand-Off Gate Applies",
		"fieldtype": "Check",
		"default": "0",
		"insert_after": "custom_launch_deadline",
		"read_only": 1,
		"no_copy": 1,
	},
	{
		"fieldname": "custom_handoff_last_reminded_on",
		"label": "Hand-Off Last Reminded On",
		"fieldtype": "Datetime",
		"insert_after": "custom_handoff_gate_applies",
		"read_only": 1,
		"no_copy": 1,
		"hidden": 1,
	},
]


def execute():
	if not frappe.db.exists("DocType", "Opportunity"):
		return

	create_custom_fields({"Opportunity": FIELDS}, ignore_validate=True)

	# create_custom_fields stamps is_system_generated=1; the fixtures own these
	# (is_system_generated=0). Normalize so a future export-fixtures keeps them.
	for field in FIELDS:
		name = frappe.db.get_value(
			"Custom Field", {"dt": "Opportunity", "fieldname": field["fieldname"]}
		)
		if name:
			frappe.db.set_value("Custom Field", name, "is_system_generated", 0, update_modified=False)

	if not frappe.db.has_column("Opportunity", "custom_handoff_gate_applies"):
		return

	# Explicit 0 on everything already won — see the module docstring. Scoped to
	# NULL so a re-run cannot clobber a record somebody has since decided about.
	frappe.db.sql(
		"""
		UPDATE `tabOpportunity`
		SET custom_handoff_gate_applies = 0
		WHERE status = 'Closed Won'
		  AND custom_handoff_gate_applies IS NULL
		"""
	)
