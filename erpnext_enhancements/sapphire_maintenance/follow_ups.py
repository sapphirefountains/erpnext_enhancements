# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Follow-ups and parts needed: from the technician's visit to installed and invoiced (v1.600.0).

D3 of Phase D (TASK-2026-02488). Lisa, 2026-10-12: "say Austin talked to someone and told them
you need a new manifold, and then he adds it to the form with a follow up and then I can get
it over to Brian and follow it through until the new part is installed and invoiced." Nik's
call: a follow-up list that can make an Opportunity.

* **On the visit.** ``follow_ups`` (Sapphire Visit Follow-Up rows): what is needed, an
  optional part and qty, who on site was told, a photo, and the water feature on a site
  visit. The Visit Wizard adds them at wrap-up or from a Fail / Replace answer, kept on the
  phone like any answer. They print on the customer's report and go in the review email.
* **On approval** (:func:`on_visit_submit`, a ``doc_events`` ``on_submit`` hook) each row
  becomes a **Maintenance Follow-Up**, tracked Open → Quoted → Ordered → Installed →
  Invoiced (or Canceled), and the row links it. It raises rather than logs: an approved
  visit whose follow-ups were silently not tracked is the thing this exists to prevent, and
  the approval rolls back with it.
* **One click to an Opportunity** (:func:`make_opportunity`, from the Visit Review page's
  follow-up list) for the customer, owned by the person set in ERPNext Enhancements Settings
  (Brian), in the Service value stream, with the part as its item.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

DOCTYPE = "Maintenance Follow-Up"
VISIT = "Sapphire Maintenance Record"
ROW = "Sapphire Visit Follow-Up"
#: Still being worked: the follow-up list shows these.
OPEN_STATUSES = ("Open", "Quoted", "Ordered", "Installed")
STATUSES = (*OPEN_STATUSES, "Invoiced", "Canceled")
SETTINGS = "ERPNext Enhancements Settings"
OWNER_SETTING = "follow_up_opportunity_owner"
VALUE_STREAM = "Service"
TABLES = ("chemistry_readings", "cleaning_tasks", "maintenance_results", "consumables")


def check_follow_ups(doc):
	"""The record's ``validate``: tidy each follow-up, and keep it on a water feature this visit
	covers. A site visit's rows are tagged with their feature; a follow-up added in the wizard
	takes the feature tab it was added on."""
	rows = doc.get("follow_ups") or []
	if not rows:
		return
	covered = {doc.get("serial_no")} if doc.get("serial_no") else set()
	for table in TABLES:
		covered.update(row.get("serial_no") for row in doc.get(table) or [] if row.get("serial_no"))
	for row in rows:
		row.description = (row.get("description") or "").strip()
		row.told = (row.get("told") or "").strip() or None
		if flt(row.get("qty")) < 0:
			frappe.throw(_("A follow-up's qty cannot be negative."), title=_("Follow-ups"))
		serial = row.get("serial_no")
		if serial and covered and serial not in covered:
			frappe.throw(
				_("Follow-up {0} is for {1}, which this visit does not cover.").format(
					row.get("idx") or "", serial
				),
				title=_("Follow-ups"),
			)


def on_visit_submit(doc, method=None):
	"""Approved: one Maintenance Follow-Up per row not already tracked, linked back from the row."""
	rows = [row for row in doc.get("follow_ups") or [] if not row.get("follow_up")]
	if not rows:
		return
	raised_on = getdate(doc.get("visit_date") or doc.get("scheduled_visit_date") or nowdate())
	for row in rows:
		tracked = frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"status": "Open",
				"description": row.get("description"),
				"item": row.get("item"),
				"qty": row.get("qty"),
				"told": row.get("told"),
				"photo": row.get("photo"),
				"customer": doc.get("customer"),
				"project": doc.get("project"),
				"serial_no": row.get("serial_no") or doc.get("serial_no"),
				"visit": doc.name,
				"raised_by": doc.get("technician") or doc.get("owner"),
				"raised_on": raised_on,
			}
		)
		tracked.insert(ignore_permissions=True)
		frappe.db.set_value(ROW, row.name, "follow_up", tracked.name, update_modified=False)
		row.follow_up = tracked.name


def make_opportunity(name):
	"""An Opportunity for a follow-up's customer, or the one already made. Returns its name.

	Owned by ERPNext Enhancements Settings → Follow-up Opportunity Owner (Brian), else by the
	person clicking; Service value stream; the part as its item. What was found, who was told
	and the visit go on its timeline. The caller must be allowed to create Opportunities.
	"""
	follow_up = frappe.get_doc(DOCTYPE, name)
	if follow_up.get("opportunity") and frappe.db.exists("Opportunity", follow_up.opportunity):
		return follow_up.opportunity
	if not frappe.has_permission("Opportunity", "create"):
		frappe.throw(_("You cannot create Opportunities."), frappe.PermissionError)
	if not follow_up.get("customer"):
		frappe.throw(_("This follow-up has no customer to make an Opportunity for."), title=_("Follow-ups"))

	owner = frappe.db.get_single_value(SETTINGS, OWNER_SETTING) or frappe.session.user
	site = (
		frappe.db.get_value("Project", follow_up.project, "project_name")
		if follow_up.get("project")
		else None
	) or follow_up.get("customer")
	opportunity = frappe.get_doc(
		{
			"doctype": "Opportunity",
			"opportunity_from": "Customer",
			"party_name": follow_up.customer,
			"company": _default_company(),
			"transaction_date": nowdate(),
			"status": "Open",
			"opportunity_owner": owner,
			"custom_opportunity_name": _("{0}: {1}").format(site, follow_up.get("title") or ""),
			"custom_value_stream": [{"value_stream": VALUE_STREAM}],
			"items": [{"item_code": follow_up.item, "qty": flt(follow_up.get("qty")) or 1}]
			if follow_up.get("item")
			else [],
		}
	)
	opportunity.insert()
	opportunity.add_comment("Comment", _timeline_note(follow_up))
	follow_up.db_set("opportunity", opportunity.name)
	return opportunity.name


def _timeline_note(follow_up):
	"""What the Opportunity's timeline says about where it came from. Escaped: a technician
	typed most of it on a phone."""
	escape = frappe.utils.escape_html
	lines = [
		_("From maintenance follow-up {0}: {1}").format(
			escape(follow_up.name), escape(follow_up.description or "")
		)
	]
	if follow_up.get("told"):
		lines.append(_("Told on site: {0}").format(escape(follow_up.told)))
	if follow_up.get("visit"):
		lines.append(_("Found on visit {0}.").format(escape(follow_up.visit)))
	return "<br>".join(lines)


def _default_company():
	return frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)
