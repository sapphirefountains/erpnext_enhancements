"""What a new Project copies from its Opportunity: the job site, the billing address and the budget.

Nik's answers in the Opportunity field review (2026-10-13): *Primary Address* is the job site
and is copied onto the Project, a billing address is kept alongside it and copied too, and the
budget fields are copied when the Project is first created. Before this they stayed on the
Opportunity, so a PM had to look them up there.

Both ways a Project is made from an Opportunity call :func:`copy_site_and_budget`, so they
cannot drift: the background creator behind the Closed Won prompt and the Hand-Off Process tab
(``crm_enhancements.api.create_project_from_opportunity_background``), and the Desk's
"Create > Project" (``opportunity_enhancements.make_project``).

Copied **once, at creation**, like the rest of the hand-off mapping. The Project's copy is the
PM's to revise afterwards, and a later edit on the Opportunity does not reach it. A value the
Project already holds is never overwritten, and a blank or unticked one on the Opportunity is not
copied, so a template's defaults survive. Frappe-free: it only reads and sets fields.
"""

#: Opportunity field -> Project field.
SITE_AND_BUDGET = {
	# The job site (both sides call it primary_address; setup/custom_fields.create_unified_tabs).
	"primary_address": "primary_address",
	# ERPNext's own Opportunity.customer_address, shown on the Opportunity as Billing Address.
	# It is also what ERPNext maps to a Quotation's billing address.
	"customer_address": "custom_billing_address",
	"custom_materials_budget": "custom_materials_budget",
	"custom_time_budget_in_hours": "custom_time_budget_in_hours",
	"custom_time__materials": "custom_time__materials",
	"custom_notes_relating_to_budget": "custom_notes_relating_to_budget",
}


def copy_site_and_budget(opportunity, project):
	"""Set each :data:`SITE_AND_BUDGET` field the Opportunity has and the Project lacks.

	``opportunity`` is anything with ``.get`` (a Document or the dict of a ``get_value``);
	``project`` is the unsaved Project. Returns the Project fieldnames it set.
	"""
	copied = []
	for source, target in SITE_AND_BUDGET.items():
		value = opportunity.get(source)
		if value in (None, "", 0):
			continue
		if project.get(target) not in (None, "", 0):
			continue
		project.set(target, value)
		copied.append(target)
	return copied
