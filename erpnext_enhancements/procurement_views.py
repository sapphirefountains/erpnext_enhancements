"""The endpoints behind the Procurement Tracker's saved views (v1.815.0).

Three questions, one door each, and the owner is checked on every one:

* ``get_views`` -- the person's own views, plus the catalog (columns, operators, limits and the
  views that ship with the app) the widget draws its picker from.
* ``save_view`` / ``rename_view`` -- create a view, or change one the person owns.
* ``delete_view`` -- delete one the person owns.

**Why not Frappe's user settings, and why not DocPerms.** User settings are served from Redis on
this site and every deploy flushes it, so a view kept there would be gone after the next release.
The views are a DocType of this app instead (``Procurement Tracker View``). That DocType gives no
role but System Manager any permission on it, so nothing reaches another person's view through
``/api/resource`` or a report; these functions are the way in, and they read and write with
``ignore_permissions`` only *after* ``procurement_view_rules.check_owner`` has said the record is the
person's own. A record that is not theirs is reported as "not found": the endpoint does not confirm
that it exists.

Views are not per project: they hold a layout and no project, and work on every project's tracker.
Anyone who may open a Project may keep views (the same Project read the tracker's own endpoints ask
for). What a view can show is decided elsewhere, on the server, by what the feed sends: see
``procurement_view_rules`` for why a layout cannot reveal a column.

Indentation is tabs (new file).
"""

import frappe

from erpnext_enhancements import procurement_view_rules as rules

DOCTYPE = "Procurement Tracker View"


def _gate():
	"""Anyone who may open a Project, and nobody signed out."""
	if frappe.session.user == "Guest" or not frappe.has_permission("Project", "read"):
		frappe.throw("You need access to Projects to keep tracker views.", frappe.PermissionError)
	return frappe.session.user


def _fail(error):
	"""A rules error as the person's message, with the right status."""
	if isinstance(error, rules.NotYours):
		frappe.throw(str(error), frappe.DoesNotExistError)
	frappe.throw(str(error))


def _view(row):
	"""One stored view as the widget reads it. A broken or outdated layout is repaired on the way
	out (``procurement_view_rules.repair_layout``): the tracker gets something it can draw, and
	``repaired`` tells it to say the view was reset."""
	layout, repaired = rules.repair_layout(row.get("layout"), row.get("mode"))
	return {
		"id": row.get("name"),
		"name": row.get("view_name"),
		"mode": row.get("mode"),
		"layout": layout,
		"repaired": repaired,
		"preset": False,
	}


def _own_rows(user):
	return frappe.get_all(
		DOCTYPE,
		filters={"owner": user},
		fields=["name", "view_name", "mode", "layout"],
		order_by="view_name asc",
		limit_page_length=rules.LIMITS["max_views_per_user"] + 50,
	)


def _owned(name, user):
	"""The stored row of one of the person's own views. Anything else is "not found"."""
	row = (
		frappe.db.get_value(DOCTYPE, name, ["name", "owner", "view_name", "mode"], as_dict=True)
		if name
		else None
	)
	try:
		rules.check_owner(row and row.get("owner"), user)
	except rules.ViewError as e:
		_fail(e)
	return row


def _layout_arg(layout):
	if isinstance(layout, str):
		try:
			return frappe.parse_json(layout)
		except Exception:
			raise rules.ViewError("The view's layout could not be read.") from None
	return layout


@frappe.whitelist()
def get_views():
	"""``{"catalog", "mine", "user"}``: the views that ship with the app (inside ``catalog``), the
	person's own, and who they are. Only the caller's own records are ever read."""
	user = _gate()
	return {
		"catalog": rules.catalog_for_client(),
		"mine": [_view(row) for row in _own_rows(user)],
		"user": user,
	}


@frappe.whitelist(methods=["POST"])
def save_view(view_name, mode, layout, name=None):
	"""Create a view, or (with ``name``) change one the person owns. Returns the saved view.

	The layout is validated strictly before anything is written, the name must be new among the
	person's own views, and a person keeps a limited number. A view keeps its grouping: to use a
	layout on the other grouping, save a copy."""
	user = _gate()
	try:
		clean = rules.clean_name(view_name)
		stored = rules.validate_layout(_layout_arg(layout), mode)
		mine = _own_rows(user)
		if name:
			current = _owned(name, user)
			if current.get("mode") != mode:
				raise rules.ViewError("A view keeps its grouping. Save a copy to use it on the other one.")
			rules.check_unique_name(clean, [r["view_name"] for r in mine if r["name"] != name])
			doc = frappe.get_doc(DOCTYPE, name)
		else:
			rules.check_quota(len(mine))
			rules.check_unique_name(clean, [r["view_name"] for r in mine])
			doc = frappe.new_doc(DOCTYPE)
			doc.mode = mode
		doc.view_name = clean
		doc.layout = rules.layout_json(stored)
		doc.save(ignore_permissions=True)
	except rules.ViewError as e:
		_fail(e)
	return _view(doc.as_dict() if hasattr(doc, "as_dict") else doc)


@frappe.whitelist(methods=["POST"])
def rename_view(name, view_name):
	"""Rename one of the person's own views, leaving its layout as stored."""
	user = _gate()
	try:
		clean = rules.clean_name(view_name)
		_owned(name, user)
		rules.check_unique_name(clean, [r["view_name"] for r in _own_rows(user) if r["name"] != name])
		frappe.db.set_value(DOCTYPE, name, "view_name", clean)
		row = frappe.db.get_value(DOCTYPE, name, ["name", "view_name", "mode", "layout"], as_dict=True)
	except rules.ViewError as e:
		_fail(e)
	return _view(row)


@frappe.whitelist(methods=["POST"])
def delete_view(name):
	"""Delete one of the person's own views."""
	user = _gate()
	_owned(name, user)
	frappe.delete_doc(DOCTYPE, name, ignore_permissions=True)
	return {"deleted": name}
