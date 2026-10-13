"""What the site's maintenance agreement covers, for the technician on site (v1.619.0).

The Visit Wizard's Safety step showed the gate code and key location from the signed
Maintenance Services Agreement and nothing else from it. A technician could not see the
schedule the client was promised, the custom plan (Exhibit A) the visit is meant to carry
out, or the work the agreement leaves out. Out-of-scope work done for free, and in-scope
work skipped, both start there. ``agreement_scope`` gathers that for the wizard's collapsed
"What the agreement covers" card.

Only for a maintenance agreement (``template_key == "maintenance"``) the user may read. No
rate field is read: the card is about the work. Exhibit A is shown as written, so a custom
plan whose text names its price shows it (an Employee can read the agreement anyway). Never
raises; the visit opens without the card rather than not at all.
"""

import frappe

#: Section 2.4 "Services NOT Included" of ``templates/contracts/maintenance_services_agreement.html``,
#: the current standard wording; an agreement signed on an older wording is not re-read.
#: ``tests/test_visit_agreement_match.py`` fails the build when a line here stops matching it.
NOT_INCLUDED = (
	"Structural repairs, major equipment replacement, or resurfacing",
	"Electrical repairs beyond minor bulb replacement",
	"Plumbing repairs requiring excavation or re-piping",
	"Removal of invasive vegetation or tree root intrusion",
	"Water features that are inaccessible without specialized equipment",
)

#: Section 2.1's chemicals line and section 5: billed on top of the base rate.
BILLED_ON_TOP = (
	"Chemicals are billed in addition to the base rate, so record every one you add on Chemicals Used."
)

_FIELDS = (
	"template_key",
	"visit_frequency",
	"custom_visit_schedule",
	"preferred_days",
	"preferred_time",
	"startup_month",
	"winterization_month",
	"custom_plan_details",
)


def agreement_scope(project_contract):
	"""The agreement's schedule, chosen services and custom plan, as plain text.

	Returns:
		dict: {} when there is no maintenance agreement the user may read, else {name,
		schedule, preferred_days, time_window, startup, winterization, services: [labels of
		the options chosen], custom_plan: Exhibit A, not_included: NOT_INCLUDED, billed_on_top}.
	"""
	try:
		return _scope(project_contract)
	except Exception:
		frappe.log_error(title="Visit wizard: agreement scope failed", message=frappe.get_traceback())
		return {}


def _scope(project_contract):
	if not project_contract or not frappe.db.exists("Project Contract", project_contract):
		return {}
	if not frappe.has_permission("Project Contract", "read", doc=project_contract):
		return {}
	row = frappe.db.get_value("Project Contract", project_contract, list(_FIELDS), as_dict=True)
	if not row or row.template_key != "maintenance":
		return {}
	schedule = row.visit_frequency or ""
	if schedule == "Custom":
		schedule = (row.custom_visit_schedule or "").strip() or schedule
	services = frappe.get_all(
		"Contract Service Option",
		filters={"parent": project_contract, "parenttype": "Project Contract", "included": 1},
		pluck="option_label",
		order_by="idx asc",
	)
	return {
		"name": project_contract,
		"schedule": schedule,
		"preferred_days": (row.preferred_days or "").strip(),
		"time_window": (row.preferred_time or "").strip(),
		"startup": (row.startup_month or "").strip(),
		"winterization": (row.winterization_month or "").strip(),
		"services": [label for label in services if label],
		"custom_plan": (row.custom_plan_details or "").strip(),
		"not_included": list(NOT_INCLUDED),
		"billed_on_top": BILLED_ON_TOP,
	}
