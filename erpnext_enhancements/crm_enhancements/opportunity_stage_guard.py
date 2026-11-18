# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Keep ERPNext's automatic Opportunity statuses out of this site's stage list (v1.824.0).

**What ERPNext does.** When a Quotation linked to an Opportunity is submitted, ERPNext v16 sets the
Opportunity's status to ``Quotation``; when a Sales Order made from that Quotation is submitted, to
``Converted``; when the Quotation is canceled, to ``Open`` (``Quotation.update_opportunity`` and
``SalesOrder.update_prevdoc_status``, read at v16.54.0). Each goes through
``Opportunity.set_status(update=True)``, a ``db_set`` that no Select validation sees.

**Why that breaks this site.** The status field here is a list of stages (a Property Setter:
Qualification, Needs Analysis, Proposal/Price Quote, Negotiation/Review, Lost, Closed Won, On Hold).
``Quotation``, ``Converted`` and ``Open`` are not on it. A record holding a status off its own list
is frozen: ``_validate_selects`` refuses every later ordinary save (148 Zoho imports sat that way for
a year, ``patches/remap_orphan_opportunity_statuses.py``), and the Kanban board, whose columns are
the list, stops showing the card. Nothing hit this before v1.824.0 because every Quotation on prod
was a QuickBooks-mirrored draft. The E-Stop quote-to-order flow submits Quotations and makes Sales
Orders from them, so from then on it would happen on every product sale.

**What this does.** ``on_change`` runs after every ``db_set`` with the before-image already loaded
(frappe ``document.py`` ``db_set``, read at v16.56.0). When ERPNext has just written one of its own
statuses that this site does not offer, the stage the deal had before is put back. If that stage is
unusable too, :data:`FALLBACK` maps ERPNext's meaning onto the nearest stage. Moving a deal forward
(to Proposal/Price Quote, or to Closed Won when its order is placed) stays a person's call: Closed
Won runs the won-date stamp and the hand-off chain, which a ``db_set`` would skip. A site that adds
one of these statuses to its list keeps it: only a value off the list is touched.

The rule (:func:`restored_status`) is pure; the hook never raises.
"""

#: The statuses ERPNext's own code writes onto an Opportunity (``status_updater.status_map``).
ERPNEXT_STATUSES = ("Open", "Quotation", "Converted", "Closed")

#: Where each lands when the stage before it is unusable as well, if the site offers that stage.
FALLBACK = {
	"Quotation": "Proposal/Price Quote",
	"Converted": "Closed Won",
	"Open": "Qualification",
	"Closed": "Lost",
}


def restored_status(new_status, previous_status, options):
	"""The status to put back, or ``None`` to leave ``new_status`` alone.

	``options`` is the site's stage list. Only one of ERPNext's automatic statuses that is not on it
	is replaced: by ``previous_status`` when that is on the list, else by :data:`FALLBACK`, else by
	the list's first stage.
	"""
	stages = [option for option in (options or []) if option]
	if not stages or not new_status or new_status in stages or new_status not in ERPNEXT_STATUSES:
		return None
	if previous_status in stages:
		return previous_status
	fallback = FALLBACK.get(new_status)
	return fallback if fallback in stages else stages[0]


def keep_stage_on_list(doc, method=None):
	"""Opportunity ``on_change``: undo an ERPNext status the stage list does not offer. Never raises."""
	try:
		field = doc.meta.get_field("status")
		options = [o.strip() for o in str(getattr(field, "options", "") or "").split("\n")]
		before = doc.get_doc_before_save()
		previous = before.get("status") if before is not None else None
		status = restored_status(doc.get("status"), previous, options)
		if status:
			doc.db_set("status", status, update_modified=False)
	except Exception:
		try:
			import frappe

			frappe.log_error(title="Opportunity stage guard", message=frappe.get_traceback())
		except Exception:
			pass
