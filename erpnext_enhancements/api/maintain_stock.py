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

Tabs, per ``CLAUDE.md`` (new file).
"""

import frappe
from frappe import _

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
