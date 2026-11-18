# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Keep ERPNext's automatic status writes from moving an Opportunity's stage (v1.825.0).

**What ERPNext does** (read at v16.54.0). A Quotation linked to an Opportunity moves the deal's
status through ``Opportunity.set_status(update=True)``, a ``db_set`` that no validation sees:

* submitting the Quotation sets ``Quotation``, and its Sales Order sets ``Converted``
  (``Quotation.update_opportunity``, ``SalesOrder.update_prevdoc_status``);
* canceling the Quotation sets ``Open``;
* **declaring the Quotation lost** (``Quotation.declare_enquiry_lost``) sets ``Lost``, unless
  another submitted Quotation on the deal is still open or ordered, in which case ``status_map``
  turns it into ``Quotation`` or ``Converted``. QuickBooks quotes are drafts and do not count.

**Why that breaks this site.** The status field here is a list of stages (a Property Setter:
Qualification, Needs Analysis, Proposal/Price Quote, Negotiation/Review, Lost, Closed Won, On Hold).

* ``Quotation``, ``Converted`` and ``Open`` are not on it. A record holding a status off its own list
  is frozen: ``_validate_selects`` refuses every later ordinary save (148 Zoho imports sat that way
  for a year, ``patches/remap_orphan_opportunity_statuses.py``), and the Kanban, whose columns are
  the list, stops showing the card.
* ``Lost`` is on it, which is worse in its own way: an E-Stop add-on quote declared lost would close
  a whole fountain deal whose real quote lives in QuickBooks, without the Lost Reason a person must
  give (``script_migrations.opportunity.validate_close_reason`` runs on a save, not a ``db_set``).

Nothing hit any of this before because every Quotation on prod was a QuickBooks-mirrored draft. The
E-Stop quote-to-order flow submits Quotations, so from then on it would happen on every sale.

**What this does.** ``on_change`` runs after every ``db_set`` with the before-image loaded (frappe
``document.py``, read at v16.56.0), and a ``db_set`` on a record loaded by ERPNext's code carries no
``_action``, which every real save sets. So:

* one of ERPNext's own statuses that is off the list is replaced by the stage the deal had, or, if
  that is unusable too, by :data:`FALLBACK`'s nearest stage;
* an automatic ``Lost`` (no ``_action``) puts the stage back as well. A person still marks a deal
  Lost by saving it, with a reason.

Moving a deal stays a person's call: forward to Closed Won (which runs the won-date stamp and the
hand-off chain a ``db_set`` would skip), and to Lost. ERPNext also leaves a "Label" timeline comment
naming the status it set; when the status is put back, that comment goes too, so the timeline does
not announce a status the deal never kept. A site that adds one of these statuses to its list keeps
it.

The rule (:func:`restored_status`) is pure; the hook never raises.
"""

#: The statuses ERPNext's own code writes onto an Opportunity (``status_updater.status_map``).
ERPNEXT_STATUSES = ("Open", "Quotation", "Converted", "Closed", "Lost")

#: Where each lands when the stage before it is unusable as well, if the site offers that stage.
FALLBACK = {
	"Quotation": "Proposal/Price Quote",
	"Converted": "Closed Won",
	"Open": "Qualification",
	"Closed": "Lost",
}

#: How recent ERPNext's Label comment must be for the guard to take it back.
LABEL_WINDOW_SECONDS = 120


def restored_status(new_status, previous_status, options, automatic=True):
	"""The status to put back, or ``None`` to leave ``new_status`` alone.

	``options`` is the site's stage list; ``automatic`` is True for a write no person's save made.

	* One of ERPNext's statuses that is off the list: ``previous_status`` when that is on it, else
	  :data:`FALLBACK`, else the list's first stage. Whoever wrote it.
	* ``Lost`` written automatically: ``previous_status``, when that is another stage on the list.
	"""
	stages = [option for option in (options or []) if option]
	if not stages or not new_status or new_status not in ERPNEXT_STATUSES:
		return None
	if new_status not in stages:
		if previous_status in stages:
			return previous_status
		fallback = FALLBACK.get(new_status)
		return fallback if fallback in stages else stages[0]
	if automatic and new_status == "Lost" and previous_status in stages and previous_status != "Lost":
		return previous_status
	return None


def keep_stage_on_list(doc, method=None):
	"""Opportunity ``on_change``: undo a stage move ERPNext made on its own. Never raises."""
	try:
		field = doc.meta.get_field("status")
		options = [o.strip() for o in str(getattr(field, "options", "") or "").split("\n")]
		before = doc.get_doc_before_save()
		previous = before.get("status") if before is not None else None
		written = doc.get("status")
		automatic = getattr(doc, "_action", None) is None
		status = restored_status(written, previous, options, automatic=automatic)
		if status:
			doc.db_set("status", status, update_modified=False)
			_drop_status_label(doc, written)
	except Exception:
		_log()


def _drop_status_label(doc, status):
	"""Delete the "Label" comment ERPNext's ``set_status`` just added for ``status``. Never raises.

	Only the newest one on this record, with exactly that text, from the last couple of minutes:
	``set_status`` adds it immediately before the ``db_set`` this guard has just undone.
	"""
	try:
		import frappe
		from frappe.utils import add_to_date, now_datetime

		rows = frappe.get_all(
			"Comment",
			filters={
				"reference_doctype": doc.doctype,
				"reference_name": doc.name,
				"comment_type": "Label",
				"content": ("in", list({status, frappe._(status)})),
				"creation": (">=", add_to_date(now_datetime(), seconds=-LABEL_WINDOW_SECONDS)),
			},
			pluck="name",
			order_by="creation desc",
			limit=1,
		)
		if rows:
			frappe.db.delete("Comment", {"name": rows[0]})
	except Exception:
		_log()


def _log():
	try:
		import frappe

		frappe.log_error(title="Opportunity stage guard", message=frappe.get_traceback())
	except Exception:
		pass
