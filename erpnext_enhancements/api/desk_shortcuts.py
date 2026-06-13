"""Per-user desk shortcut visibility for the Home "Desk Shortcuts" block.

``boot.py`` ships the result as ``frappe.boot.ee_desk_shortcuts`` and the Custom
HTML Block (``Custom HTML Block/desk_shortcuts.js``) renders it as icon tiles.

A user sees a shortcut when it is **enabled** AND any of:
- they are **System Manager** / **Administrator** (always see every enabled one),
- it is **visible to all**,
- they hold one of its **roles**,
- they are listed as one of its **users**.

This is a desk-tidiness filter only — every target page enforces its own role
permissions, so this never grants access. It runs in every desk boot, so it is
kept cheap (three small queries) and is wrapped so it can never break the desk:
on any error (e.g. code deployed before ``bench migrate`` created the table) it
returns an empty list.
"""

import frappe


def get_visible_shortcuts_for_user():
	"""Return the ordered shortcuts the session user may see (``list[dict]``).

	Each entry: ``{label, icon, color, type, link_to, url, doc_view}``.
	Never raises — returns ``[]`` on any failure so desk boot is unaffected.
	"""
	user = frappe.session.user
	if not user or user == "Guest":
		return []
	try:
		return _compute_visible_shortcuts(user)
	except Exception:
		frappe.log_error(title="ee_desk_shortcuts boot failed")
		return []


def _compute_visible_shortcuts(user):
	# Pre-migrate safety: the doctype/table may not exist yet on a site that has
	# this code but hasn't migrated. Bail quietly rather than erroring in boot.
	if not frappe.db.table_exists("Enhancement Desk Shortcut"):
		return []

	rows = frappe.get_all(
		"Enhancement Desk Shortcut",
		filters={"enabled": 1},
		fields=[
			"name",
			"shortcut_label",
			"icon",
			"color",
			"link_type",
			"link_to",
			"url",
			"doc_view",
			"visible_to_all",
		],
		order_by="sequence asc, shortcut_label asc",
	)
	if not rows:
		return []

	user_roles = set(frappe.get_roles(user))
	is_admin = user == "Administrator" or "System Manager" in user_roles

	# Bulk-load role/user child rows only for the gated shortcuts (and only when
	# the user isn't an admin, who sees everything anyway).
	allowed_roles = {}
	allowed_users = {}
	gated = [r.name for r in rows if not r.visible_to_all]
	if gated and not is_admin:
		for child in frappe.get_all(
			"Enhancement Desk Shortcut Role",
			filters={"parenttype": "Enhancement Desk Shortcut", "parent": ("in", gated)},
			fields=["parent", "role"],
		):
			allowed_roles.setdefault(child.parent, set()).add(child.role)
		for child in frappe.get_all(
			"Enhancement Desk Shortcut User",
			filters={"parenttype": "Enhancement Desk Shortcut", "parent": ("in", gated)},
			fields=["parent", "user"],
		):
			allowed_users.setdefault(child.parent, set()).add(child.user)

	visible = []
	for r in rows:
		if is_admin or r.visible_to_all:
			show = True
		else:
			show = bool(user_roles & allowed_roles.get(r.name, set())) or (
				user in allowed_users.get(r.name, set())
			)
		if not show:
			continue
		visible.append(
			{
				"label": r.shortcut_label,
				"icon": r.icon or "",
				"color": r.color or "Gray",
				"type": r.link_type,
				"link_to": r.link_to or "",
				"url": r.url or "",
				"doc_view": r.doc_view or "",
			}
		)
	return visible
