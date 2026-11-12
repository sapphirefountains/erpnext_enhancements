# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Turn on Maintain Stock for an Item that ERPNext's form refuses to switch (v1.754.0).

The Item form's *Turn on Maintain Stock* action (``public/js/item_maintain_stock.js``) calls
these two. ``check`` answers "would it be safe?" and changes nothing; ``turn_on`` asks the same
question again on the server, under a row lock, and only then switches. The dialog's answer is
never trusted: it is only what was true when the dialog opened.

Who, checked by both endpoints, because a whitelisted function is reachable by any logged-in
session and the form only hides its button:

* **A person at a screen.** A request carrying an ``Authorization`` header (an API key or a
  token), a background job and the console are refused: ``marketing.publish.workflow
  .signed_in_browser``, the rule the knowledge base's and the marketing approvals already use.
  Switching is a decision made after reading the checks.
* **A Stock Manager or an Item Manager who is a person** (``maintain_stock_rules.may_switch``):
  service accounts are refused whatever roles they hold.
* **Who may write this Item.** On production Stock Manager can only read Item (a Custom DocPerm),
  and this action changes the Item, so the role alone is not enough.

``check`` names a failing document only where the caller may read it, and counts the rest.
Reads are GET or POST; the switch is POST only. The work, and every reason behind it, is in
``inventory_enhancements/maintain_stock.py`` and ``maintain_stock_rules.py``.

**The bulk page** (v1.803.0, *Switch Items to Maintain Stock*) has its own endpoints below, all
named ``bulk_...`` and all behind the same three gates, with write on Item asked of the doctype
rather than of one record. They preview, keep a person's choice for an item, queue the switch, and
report on it. The work is ``inventory_enhancements/maintain_stock_bulk.py``.

Tabs, per ``CLAUDE.md`` (new file).
"""

import json

import frappe
from frappe import _
from frappe.utils import cint

from erpnext_enhancements.inventory_enhancements import maintain_stock
from erpnext_enhancements.inventory_enhancements import maintain_stock_rules as rules
from erpnext_enhancements.marketing.publish.workflow import signed_in_browser


def _from_a_person():
	request = getattr(frappe.local, "request", None)
	headers = getattr(request, "headers", None) if request is not None else None
	authorization = headers.get("Authorization") if headers is not None else None
	return request is not None and signed_in_browser(frappe.session, authorization)


def _item_code(item_code):
	code = item_code.strip() if isinstance(item_code, str) else ""
	if not code:
		frappe.throw(_("Which item? No item code was sent."))
	if not frappe.db.exists("Item", code):
		frappe.throw(_("Item {0} doesn't exist.").format(code), frappe.DoesNotExistError)
	return code


def _require_switcher(item_code):
	"""The item code, once the caller passes every gate above. Refuses otherwise."""
	if not _from_a_person():
		frappe.throw(
			_(
				"Turn on Maintain Stock from the Item form, signed in as yourself. API keys and tokens can't use it."
			),
			frappe.PermissionError,
		)
	if not rules.may_switch(frappe.session.user, frappe.get_roles()):
		frappe.throw(
			_("Only a Stock Manager or an Item Manager can turn on Maintain Stock this way."),
			frappe.PermissionError,
		)
	code = _item_code(item_code)
	frappe.get_doc("Item", code).check_permission("write")
	return code


@frappe.whitelist(methods=["GET", "POST"])
def check(item_code=None):
	"""Every safety check for ``item_code``, and whether they all pass. Writes nothing."""
	return maintain_stock.run_checks(_require_switcher(item_code))


@frappe.whitelist(methods=["POST"])
def turn_on(item_code=None):
	"""Re-run every check and, when all pass, switch Maintain Stock on. Refuses in words otherwise."""
	return maintain_stock.turn_on(_require_switcher(item_code))


# ---------------------------------------------------------------------------
# The bulk page
# ---------------------------------------------------------------------------


def _require_bulk_switcher():
	"""The same gates as the single switch, for the whole list: a person at a screen, a Stock
	Manager or an Item Manager who is a person, and write on Item."""
	if not _from_a_person():
		frappe.throw(
			_(
				"Use the Switch Items to Maintain Stock page, signed in as yourself. API keys and tokens can't use it."
			),
			frappe.PermissionError,
		)
	if not rules.may_switch(frappe.session.user, frappe.get_roles()):
		frappe.throw(
			_("Only a Stock Manager or an Item Manager can switch items to Maintain Stock."),
			frappe.PermissionError,
		)
	if not frappe.has_permission("Item", "write"):
		frappe.throw(_("You need permission to edit Items to use this page."), frappe.PermissionError)


def _bulk():
	from erpnext_enhancements.inventory_enhancements import maintain_stock_bulk

	return maintain_stock_bulk


def _picked(items):
	"""The item codes the page showed, or ``None`` when it sent none (apply the server's list)."""
	if items in (None, ""):
		return None
	if isinstance(items, str):
		try:
			items = json.loads(items)
		except ValueError:
			frappe.throw(_("The list of items could not be read. Reload the page and try again."))
	if not isinstance(items, list):
		frappe.throw(_("The list of items could not be read. Reload the page and try again."))
	return [code for code in items if isinstance(code, str) and code.strip()]


@frappe.whitelist(methods=["GET", "POST"])
def bulk_preview(relax_sales_invoices=0):
	"""Every enabled non-stock item with its outcome and reason. Writes nothing."""
	_require_bulk_switcher()
	return _bulk().preview(relax_sales_invoices=cint(relax_sales_invoices))


@frappe.whitelist(methods=["POST"])
def bulk_decide(item_code=None, decision=None):
	"""Keep a person's choice for one item (Switch, Stay non-stock, or blank). The Item is untouched."""
	_require_bulk_switcher()
	return _bulk().set_decision(_item_code(item_code), decision)


@frappe.whitelist(methods=["POST"])
def bulk_apply(items=None, relax_sales_invoices=0):
	"""Queue the items that will switch, and start the background job. The preview is computed
	again on the server; ``items`` (what the page showed) can only narrow it."""
	_require_bulk_switcher()
	return _bulk().queue_switch(picked=_picked(items), relax_sales_invoices=cint(relax_sales_invoices))


@frappe.whitelist(methods=["POST"])
def bulk_stop():
	"""Take every queued item off the list. Items already switched stay switched."""
	_require_bulk_switcher()
	return _bulk().stop()


@frappe.whitelist(methods=["GET", "POST"])
def bulk_status():
	"""How many items are queued, switched and refused."""
	_require_bulk_switcher()
	return _bulk().status()


@frappe.whitelist(methods=["GET", "POST"])
def bulk_results():
	"""The items an apply switched, refused or still holds: the "after" half of the list."""
	_require_bulk_switcher()
	return _bulk().results()


@frappe.whitelist(methods=["GET", "POST"])
def bulk_list_csv(which="preview", relax_sales_invoices=0):
	"""The before-and-after list as CSV text: ``{"filename", "csv"}``.

	``which`` is ``preview`` (every non-stock item, what it will be and why: the list to approve)
	or ``results`` (what a run switched, refused or still holds).
	"""
	_require_bulk_switcher()
	from erpnext_enhancements.inventory_enhancements import maintain_stock_bulk_rules as bulk_rules

	if which == "results":
		rows, name = _bulk().results(), "maintain-stock-results"
	else:
		rows = _bulk().preview(relax_sales_invoices=cint(relax_sales_invoices))["rows"]
		name = "maintain-stock-list"
	return {"filename": f"{name}-{frappe.utils.nowdate()}.csv", "csv": bulk_rules.to_csv(rows)}
