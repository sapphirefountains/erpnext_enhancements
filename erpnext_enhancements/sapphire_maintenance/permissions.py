"""Row-level access for Sapphire Maintenance Record on the customer portal.

Wired in hooks.py via ``permission_query_conditions`` (list views / the
``/maintenance-records`` portal listing) and ``has_permission`` (single-record
open). Internal maintenance/project roles see every record; a portal customer
is scoped to their own **submitted** visit records — matched to the Customers
they are a Contact for. This is what makes the portal safe to switch on: a
customer can read their own service reports but never another customer's, and
drafts (unfinished visits) are never exposed.
"""

import frappe

# Roles that see every maintenance record (staff). Everyone else is a customer.
VIEW_ALL_ROLES = {
	"System Manager",
	"Projects Manager",
	"Maintenance Supervisor",
	"Maintenance User",
}


def _sees_all(user):
	return user == "Administrator" or bool(VIEW_ALL_ROLES & set(frappe.get_roles(user)))


def _customers_for_user(user):
	"""Customers this portal user is a Contact for (via Contact dynamic links)."""
	contacts = frappe.get_all("Contact", filters={"user": user}, pluck="name")
	if not contacts:
		return []
	return list(
		set(
			frappe.get_all(
				"Dynamic Link",
				filters={
					"parenttype": "Contact",
					"parent": ["in", contacts],
					"link_doctype": "Customer",
				},
				pluck="link_name",
			)
		)
	)


def get_permission_query_conditions(user=None):
	user = user or frappe.session.user
	if _sees_all(user):
		return ""
	customers = _customers_for_user(user)
	if not customers:
		return "1=0"  # no linked customer -> no records
	quoted = ", ".join(frappe.db.escape(c) for c in customers)
	return (
		f"(`tabSapphire Maintenance Record`.`customer` in ({quoted}) "
		f"and `tabSapphire Maintenance Record`.`docstatus` = 1)"
	)


def has_permission(doc, ptype=None, user=None):
	user = user or frappe.session.user
	if _sees_all(user):
		return True
	# Customers only ever read their own submitted records; a has_permission
	# hook can only restrict, so denying the rest is safe (they hold no DocPerm
	# for write/submit/delete anyway).
	if ptype not in (None, "read"):
		return False
	if doc.get("docstatus") != 1:
		return False
	return doc.get("customer") in _customers_for_user(user)
