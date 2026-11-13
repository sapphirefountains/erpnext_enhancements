# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Tracked Package is visible only with its order: the ``has_permission`` and
``permission_query_conditions`` hooks (review round 1, item 8).

The DocType's own permissions say *which roles* may see tracking at all. These hooks add *which
packages*: only those on a Purchase Order the person can read. Without them, a role holder whose
Purchase Orders are restricted (a Frappe User Permission on Supplier or Company, a share, an owner
rule) would still see every package, with its supplier and order name, in the Tracked Package list
and in reports.

* ``has_permission`` (one document): read on the referenced order, through
  ``frappe.has_permission``, so every rule Frappe applies to the order applies here.
* ``query_conditions`` (lists, reports, ``get_list``): the order's own match conditions, built by
  Frappe's ``DatabaseQuery("Purchase Order", user).build_match_conditions()`` (read at v16.56.0,
  ``frappe/model/db_query.py``), used as a sub-select. Calling the framework rather than copying
  its rules keeps this right across upgrades (CLAUDE.md). It fails closed: an error or a person
  with no read on Purchase Order sees no package.

System Manager and Administrator are not scoped, the same as everywhere else in this app.
"""

import frappe

DOCTYPE = "Tracked Package"
ORDER = "Purchase Order"


def _unscoped(user):
	if user == "Administrator":
		return True
	try:
		return "System Manager" in frappe.get_roles(user)
	except Exception:
		return False


def has_permission(doc, ptype=None, user=None):
	"""A package is visible (and editable within the DocType's rights) only with read on its order."""
	user = user or frappe.session.user
	if _unscoped(user):
		return True
	try:
		reference_doctype = (
			doc.get("reference_doctype") if hasattr(doc, "get") else getattr(doc, "reference_doctype", None)
		)
		reference_name = (
			doc.get("reference_name") if hasattr(doc, "get") else getattr(doc, "reference_name", None)
		)
	except Exception:
		return False
	if (reference_doctype or ORDER) != ORDER:
		return False
	if not reference_name:
		# A document being built has no order yet; TrackedPackage.validate refuses one without it.
		return ptype == "create"
	try:
		return bool(frappe.has_permission(ORDER, "read", doc=reference_name, user=user))
	except Exception:
		return False


def query_conditions(user=None):
	"""SQL limiting Tracked Package lists to packages on orders ``user`` can read. Fails closed.

	Two cases, because Frappe builds them differently:

	* **The person reads Purchase Orders through a role.** Frappe's own match conditions for Purchase
	  Order (User Permissions, owner rules, shares) are used as a sub-select.
	* **The person has no role read on Purchase Order** and reaches orders only through shares (Stock
	  Manager here: it reads Tracked Package, but production's Purchase Order permissions do not give
	  it read). ``build_match_conditions`` then puts the share filter into the query it is building
	  and returns ``""`` (``db_query.py`` 1060-1065 at v16.56.0), which would have let such a person
	  list every package (review round 2). So this case is built here: the orders shared with them
	  (``frappe.share.get_shared``), or nothing.
	"""
	user = user or frappe.session.user
	if _unscoped(user):
		return ""
	table = f"`tab{DOCTYPE}`"
	base = f"{table}.`reference_doctype` = {frappe.db.escape(ORDER)}"
	try:
		if not frappe.has_permission(ORDER, "read", user=user):
			return "1=0"
		from frappe.permissions import get_role_permissions
		from frappe.share import get_shared

		role_permissions = get_role_permissions(frappe.get_meta(ORDER), user=user)
		if not (role_permissions.get("read") or role_permissions.get("select")):
			shared = [name for name in (get_shared(ORDER, user) or []) if name]
			if not shared:
				return "1=0"
			names = ", ".join(frappe.db.escape(name) for name in shared)
			return f"({base} and {table}.`reference_name` in ({names}))"
		from frappe.model.db_query import DatabaseQuery

		order_conditions = DatabaseQuery(ORDER, user=user).build_match_conditions(as_condition=True)
	except Exception:
		return "1=0"
	if not order_conditions:
		return base
	return (
		f"({base} and {table}.`reference_name` in "
		f"(select `tab{ORDER}`.`name` from `tab{ORDER}` where {order_conditions}))"
	)
