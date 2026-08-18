"""Opportunity -> Project conversion enhancements.

Wraps ERPNext's standard "make Project from Opportunity" mapping so the newly
created Project remembers which Opportunity it came from. Wired in hooks.py via:

    override_whitelisted_methods = {
        "erpnext.crm.doctype.opportunity.opportunity.make_project":
            "erpnext_enhancements.opportunity_enhancements.make_project",
    }

Because the standard method is overridden globally, every "Create > Project"
action launched from an Opportunity (including the desk button) routes here.

The link is stamped on ``Project.custom_opportunity`` — the real, persisted
Link field. (Historical note, v1.3.0: this used to stamp
``custom_sales_opportunity``, a field that does not exist on the site — the
value was silently dropped on insert, which meant the attachment sync in
``sync_attachments_from_opportunity`` only worked within the creation request
and the PRO-0204 seeding/AE-resolution had no forward link to key on.)
The stored link is used by ``sync_attachments_from_opportunity``
(project_enhancements/__init__.py, Project ``after_save``) and by the
hand-off process engine (``process_steps.seed_process_steps``).
"""

import frappe
from erpnext.crm.doctype.opportunity.opportunity import make_project as original_make_project

from erpnext_enhancements.crm_enhancements.handoff import (
	BILLING_EMAIL_FIELD,
	FIRST_INVOICE_PERCENTAGE_FIELD,
	billing_terms,
	throw_if_handoff_missing,
)


@frappe.whitelist()
def make_project(source_name, target_doc=None):
	"""Build a Project from an Opportunity, stamping the source link.

	Overrides ``erpnext...opportunity.make_project`` (see module docstring). Delegates
	to the original mapper, then sets ``custom_opportunity`` on the resulting
	(unsaved) Project document so the origin Opportunity is recorded, and copies
	across the step-4 invoice terms (``custom_first_invoice_percentage`` /
	``custom_billing_email``) that ERPNext's own mapping table has no way to know
	about.

	Args:
	    source_name (str): Name (ID) of the source Opportunity.
	    target_doc: Optional partially-built target passed through by Frappe's
	        get_mapped_doc machinery.

	Returns:
	    Document: The mapped Project document (not yet saved) with
	    ``custom_opportunity`` and the invoice terms populated.

	Raises:
	    frappe.ValidationError: When the source Opportunity is Closed Won and its
	        hand-off meeting has not been recorded (or skipped with a reason).
	        This is the *second* of three enforcement layers — the authoritative
	        one is ``process_steps.enforce_handoff_gate`` on Project
	        ``before_insert``. Refusing here as well means the desk
	        "Create > Project" action fails immediately with a readable message,
	        instead of building the whole mapped document and then dying at insert.
	"""
	throw_if_handoff_missing(source_name)

	target = original_make_project(source_name, target_doc)

	target.custom_opportunity = source_name

	# The step-4 invoice terms. ERPNext's own mapping table cannot know about
	# them, so without this the desk route produces a Project that is blank where
	# the background creator's is filled in. `billing_terms` would fall back to
	# the Opportunity at send time either way; the point of copying is that the
	# PM can *see* and revise the terms on the project.
	terms = billing_terms(opportunity=source_name)
	if terms["first_invoice_percentage"] is not None:
		target.set(FIRST_INVOICE_PERCENTAGE_FIELD, terms["first_invoice_percentage"])
	if terms["billing_email"]:
		target.set(BILLING_EMAIL_FIELD, terms["billing_email"])

	return target
