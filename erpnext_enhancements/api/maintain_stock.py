# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Turn on Maintain Stock for an Item that ERPNext's form refuses to switch (v1.754.0).

The Item form's *Turn on Maintain Stock* action (``public/js/item_maintain_stock.js``) calls
these two. ``check`` answers "would it be safe?" and changes nothing; ``turn_on`` asks the same
question again on the server, under a row lock, and only then switches. The dialog's answer is
never trusted: it is only what was true when the dialog opened.

Who: ``maintain_stock_rules.may_switch``, a Stock Manager or an Item Manager who is a person
(service accounts are refused whatever they hold). Both endpoints check it, because a
whitelisted function is reachable by any logged-in session; the form only hides its button.
Reads are GET or POST; the switch is POST only. The work, and every reason behind it, is in
``inventory_enhancements/maintain_stock.py`` and ``maintain_stock_rules.py``.

Tabs, per ``CLAUDE.md`` (new file).
"""

import frappe
from frappe import _

from erpnext_enhancements.inventory_enhancements import maintain_stock
from erpnext_enhancements.inventory_enhancements import maintain_stock_rules as rules


def _require_switcher():
	if not rules.may_switch(frappe.session.user, frappe.get_roles()):
		frappe.throw(
			_("Only a Stock Manager or an Item Manager can turn on Maintain Stock this way."),
			frappe.PermissionError,
		)


def _item_code(item_code):
	code = item_code.strip() if isinstance(item_code, str) else ""
	if not code:
		frappe.throw(_("Which item? No item code was sent."))
	if not frappe.db.exists("Item", code):
		frappe.throw(_("Item {0} doesn't exist.").format(code), frappe.DoesNotExistError)
	return code


@frappe.whitelist(methods=["GET", "POST"])
def check(item_code=None):
	"""Every safety check for ``item_code``, and whether they all pass. Writes nothing."""
	_require_switcher()
	return maintain_stock.run_checks(_item_code(item_code))


@frappe.whitelist(methods=["POST"])
def turn_on(item_code=None):
	"""Re-run every check and, when all pass, switch Maintain Stock on. Refuses in words otherwise."""
	_require_switcher()
	return maintain_stock.turn_on(_item_code(item_code))
