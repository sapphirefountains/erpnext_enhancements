"""Migrated Opportunity Server Scripts, wired via ``hooks.py``.

All three functions are registered in ``hooks.py`` under
``doc_events["Opportunity"]["before_save"]`` (alongside
:func:`erpnext_enhancements.crm_enhancements.api.sync_opportunity_tags`):
  * :func:`stamp_won_date`
  * :func:`validate_ranks_on_won`
  * :func:`update_lead_status`

Originally Frappe "Server Script" records stored only in the site DB; now
versioned with the app.
"""

import frappe

# Opportunity Server Scripts migrated to native doc_events.
# Wired in hooks.py under doc_events["Opportunity"]["before_save"].


def stamp_won_date(doc, method=None):
	"""Source Server Script: "Stamp Opportunity Won Date" (Opportunity, Before Save).

	When the opportunity is won and the date hasn't been set yet, stamp today.
	"""
	if doc.status == "Closed Won" and not doc.custom_date_closed_won:
		doc.custom_date_closed_won = frappe.utils.today()


def validate_ranks_on_won(doc, method=None):
	"""Source Server Script: "Opportunity Status for Rankings Mandatory Server Script"
	(Opportunity, Before Save).

	When status is 'Closed Won', Scope/Schedule/Budget rank must each be 1, 2 or 3.
	"""
	if doc.status != "Closed Won":
		return

	validation_errors = []
	allowed_ranks = ["1", "2", "3"]

	if frappe.utils.cstr(doc.custom_scope_rank) not in allowed_ranks:
		validation_errors.append("<b>Scope Rank</b> must be set to 1, 2, or 3.")

	if frappe.utils.cstr(doc.custom_schedule_rank) not in allowed_ranks:
		validation_errors.append("<b>Schedule Rank</b> must be set to 1, 2, or 3.")

	if frappe.utils.cstr(doc.custom_budget_rank) not in allowed_ranks:
		validation_errors.append("<b>Budget Rank</b> must be set to 1, 2, or 3.")

	if validation_errors:
		frappe.throw(
			"<br>".join(validation_errors),
			title="Invalid Ranks for 'Closed Won' Status",
		)


def update_lead_status(doc, method=None):
	"""Source Server Script: "Update Lead Status After Opportunity Creation"
	(Opportunity, Before Save).

	When a new Opportunity is created from a Lead, mark the Lead as Converted.
	The Lead is referenced via ``party_name`` when ``opportunity_from == "Lead"``;
	the Opportunity doctype has no ``lead`` field.

	NOTE (see CHANGELOG 0.2.8): guarding on the non-existent ``doc.lead`` raised
	``AttributeError`` on *every* Opportunity save; the guard now checks
	``opportunity_from == "Lead" and party_name``.

	Side effects:
		Saves the linked Lead (``status="Converted"``, ``opportunity=doc.name``)
		with ``ignore_permissions``. A missing Lead is logged, not raised.
	"""
	if doc.is_new() and doc.opportunity_from == "Lead" and doc.party_name:
		try:
			lead_doc = frappe.get_doc("Lead", doc.party_name)
			lead_doc.status = "Converted"
			lead_doc.opportunity = doc.name
			lead_doc.save(ignore_permissions=True)
		except frappe.DoesNotExistError:
			frappe.log_error(
				f"Lead {doc.party_name} not found for Opportunity {doc.name}",
				"Update Lead Status Script",
			)
