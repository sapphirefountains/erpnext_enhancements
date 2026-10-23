"""Small, idempotent tweaks to core (erpnext-owned) workspaces / sidebars.

Registered in ``after_migrate`` (hooks.py), so these run after Frappe has synced
the standard workspace + Workspace Sidebar records from every app — letting us
re-assert an override even if a core app re-imported its version earlier in the
same migrate.

frappe 16.50 draws a module's sidebar from a ``Sidebar`` document (the areas on the
Dock rail) and no longer reads ``Workspace Sidebar``. The same items are hidden in
both, so this works on either side of that upgrade.
"""

import frappe

# (sidebar name, [(link_type, link_to, label), ...]) sidebar items to hide. The name is
# both the Workspace Sidebar's (before frappe 16.50) and the Sidebar's (16.50 on).
_HIDDEN_SIDEBAR_ITEMS = {
	# Hide the "Project" DocType link in the default Projects module sidebar
	# (user request, "for now"). The Workspace Sidebar Item child has no `hidden`
	# field, so the row is removed. Kept surgical — the Dashboard link (also
	# link_to=Project but link_type=Dashboard) and every other item stay.
	"Projects": [("DocType", "Project", "Project")],
}


def hide_core_sidebar_items():
	"""``after_migrate`` entry point: remove specific links from core sidebars.

	Idempotent: only saves when a targeted item is actually present, so on steady
	state it is a no-op. Saving bumps the sidebar's ``modified`` past the shipping
	app's file stamp, so Frappe's ``modified``-gated sync will not re-import (and
	re-add) the item on later migrates; if a core upgrade ever does bump its file
	and re-add the link, this hook (running after that sync) drops it again.
	"""
	for sidebar_name, targets in _HIDDEN_SIDEBAR_ITEMS.items():
		if not frappe.db.exists("Workspace Sidebar", sidebar_name):
			continue

		doc = frappe.get_doc("Workspace Sidebar", sidebar_name)
		drop = set(targets)
		kept = [
			it for it in doc.items
			if (it.link_type, it.link_to, it.label) not in drop
		]
		if len(kept) == len(doc.items):
			continue  # nothing to remove — no-op

		doc.items = kept
		doc.flags.ignore_permissions = True
		doc.save()

	try:
		_hide_sidebar_items()
	except Exception:
		# Cosmetic: a sidebar link is not worth a failed deploy.
		frappe.log_error(title="Core sidebar item not hidden", message=frappe.get_traceback())


def _hide_sidebar_items():
	"""Set ``hidden`` on the same items in frappe 16.50's ``Sidebar`` documents.

	The loop above stopped working on the 2026-10-06 upgrade: the desk now builds the
	Projects sidebar from ERPNext's ``Sidebar`` "Projects", whose "Project" link came back.

	The row is flagged rather than removed, and ERPNext's own row is the one flagged,
	because of how 16.50 lays a site's changes over an app's sidebar
	(``frappe/desk/layers.py``). A site change goes in a ``Custom Sidebar`` layer, and a
	layer that names any of the app's rows is read as an arrangement: the rows it names
	come first. A one-row layer hiding this link would therefore put the next workspace
	anyone creates in the Projects module (``add_site_sidebar_item`` appends it to that
	layer) above Home, and the module opens on its first item. The app's row may carry
	``hidden`` itself ("so the base can hide", ``sidebar.get_sidebar_items``), which
	changes no order and leaves the layers free. A Workspace Manager can still show the
	link again from the sidebar editor, because a site layer's ``hidden: 0`` wins.

	Written with ``db.set_value``, not by saving the document: outside developer mode
	``Sidebar.validate_app_content`` refuses a save except during a migrate, and in
	developer mode a save exports the JSON into ERPNext's own folder. An ERPNext release
	that ships a newer file re-imports the row unflagged; this runs after that import in
	the same migrate and flags it again.
	"""
	if not frappe.db.exists("DocType", "Sidebar") or not frappe.db.has_column("Sidebar Item", "hidden"):
		return

	changed = False
	for sidebar_name, targets in _HIDDEN_SIDEBAR_ITEMS.items():
		for link_type, link_to, label in targets:
			rows = frappe.get_all(
				"Sidebar Item",
				filters={
					"parenttype": "Sidebar",
					"parent": sidebar_name,
					"link_type": link_type,
					"link_to": link_to,
					"label": label,
					"hidden": 0,
				},
				pluck="name",
			)
			for name in rows:
				frappe.db.set_value("Sidebar Item", name, "hidden", 1, update_modified=False)
				changed = True

	if changed:
		# Every user's boot carries the resolved sidebars; this is the key frappe's own
		# sidebar customizations clear when they change one.
		frappe.cache.delete_key("bootinfo")


# (sidebar name, [item, ...]) links this app adds to a core (app-owned) sidebar. Each item is
# placed right after its ``after`` row, matched on (link_type, link_to). Nik, 2026-10-09: "I'd
# also like the Project Planner to exist in the Projects module". Crew Utilization is the
# planner's own report, so it goes beside the module's other reports.
_ADDED_SIDEBAR_ITEMS = {
	"Projects": [
		{
			"label": "Project Planner",
			"link_type": "Page",
			"link_to": "project-planner",
			"icon": "calendar-range",
			"child": 0,
			"after": ("DocType", "Task"),
		},
		{
			"label": "Crew Utilization",
			"link_type": "Report",
			"link_to": "Crew Utilization",
			"icon": None,
			"child": 1,
			"after": ("Report", "Project wise Stock Tracking"),
		},
	],
	# Nik, 2026-10-13: "I don't see an easy way to navigate to the guided opportunity setup." The
	# guided page's start screen (New, and the person's own open deals) sits under Opportunity.
	"CRM": [
		# The dock usage audit (v1.734.0): the stock Opportunity Kanban had 836 visits from six people
		# in the 90 days to 2026-10-23, the CRM dashboard the module opened on 189. frappe 16.50 opens a
		# module on the first sidebar row that leads anywhere (`sidebar.module_landing_route`), so the
		# Kanban goes above everything (`after` None). A URL row, because a Sidebar Item cannot name a
		# list view; the desk writes the CRM shell into it, so the row lights up on the board. frappe
		# never permission-checks a URL row, so `read` names what it opens and
		# `drop_unreadable_sidebar_links` takes the row away from anyone who cannot read that.
		{
			"label": "Opportunity Kanban",
			"link_type": "URL",
			"link_to": None,
			"url": "/desk/opportunity/view/kanban/Opportunity",
			"read": "Opportunity",
			"icon": "kanban",
			"child": 0,
			"after": None,
		},
		{
			"label": "Guided Entry",
			"link_type": "Page",
			"link_to": "guided-entry",
			"icon": "list-checks",
			"child": 0,
			"after": ("DocType", "Opportunity"),
		},
		# Form kit P6 (TASK-2026-02774): the new Opportunity board, beside the guided page. Added
		# after it in this list, so it lands under it: each item is placed after its anchor in turn.
		{
			"label": "Opportunity Board",
			"link_type": "Page",
			"link_to": "opportunity-board",
			"icon": "square-kanban",
			"child": 0,
			"after": ("Page", "guided-entry"),
		},
	],
}


def add_core_sidebar_items():
	"""``after_migrate`` entry point: add this app's links to frappe 16.50's core ``Sidebar`` documents.

	Written into ERPNext's own rows, for the reason ``_hide_sidebar_items`` gives: a ``Custom
	Sidebar`` layer that names nothing in the base is read as a set of appends and lands at the
	very end (after Setup's Settings), and one that names any base row is an arrangement that
	reorders the module. The base is read from the database in ``idx`` order
	(``sidebar.get_sidebar_items``), so a row inserted at the right ``idx`` sits exactly where it
	belongs, and the desk still drops it for anyone who cannot open the page or report.

	Rows go in with ``db_insert`` and the ones below shift with ``db.set_value``, not by saving
	the document: ``Sidebar.validate_app_content`` refuses a save outside a migrate, and in
	developer mode a save would export the JSON into ERPNext's own folder. An ERPNext release that
	ships a newer sidebar file re-imports it without our rows; this runs after that import in the
	same migrate and adds them again.

	Idempotent: an item already present under the same identity (``_key``), whatever its label or
	``hidden``, is left exactly as it is, so a Workspace Manager who hid it keeps it hidden. An
	item whose anchor row is missing is skipped rather than guessed at; ``after`` None means the
	top of the sidebar. Never raises.
	"""
	try:
		_add_sidebar_items()
	except Exception:
		# Cosmetic: a sidebar link is not worth a failed deploy.
		frappe.log_error(title="Core sidebar item not added", message=frappe.get_traceback())


def _key(row):
	"""A row's identity, as frappe's ``sidebar.item_key`` reads it: what it opens, never its label.

	A URL row opens its ``url`` and has no ``link_to``, so keying it on ``link_to`` would make every
	URL row in a sidebar the same row.
	"""
	if row.get("link_type") == "URL":
		return ("URL", row.get("url"))
	return (row.get("link_type"), row.get("link_to"))


def _add_sidebar_items():
	if not frappe.db.exists("DocType", "Sidebar"):
		return

	changed = False
	for sidebar_name, additions in _ADDED_SIDEBAR_ITEMS.items():
		if not frappe.db.exists("Sidebar", sidebar_name):
			continue
		for item in additions:
			rows = frappe.get_all(
				"Sidebar Item",
				filters={"parenttype": "Sidebar", "parent": sidebar_name},
				fields=["name", "idx", "link_type", "link_to", "url"],
				order_by="idx asc",
			)
			if any(_key(r) == _key(item) for r in rows):
				continue
			if item["after"] is None:
				idx = int(rows[0].get("idx") or 0) if rows else 1
			else:
				anchor = next((r for r in rows if _key(r) == item["after"]), None)
				if anchor is None:
					continue
				idx = int(anchor.get("idx") or 0) + 1
			for row in rows:
				if int(row.get("idx") or 0) >= idx:
					frappe.db.set_value(
						"Sidebar Item", row["name"], "idx", int(row["idx"]) + 1, update_modified=False
					)
			frappe.get_doc(
				{
					"doctype": "Sidebar Item",
					"parent": sidebar_name,
					"parenttype": "Sidebar",
					"parentfield": "items",
					"idx": idx,
					"type": "Link",
					"label": item["label"],
					"link_type": item["link_type"],
					"link_to": item["link_to"],
					"url": item.get("url"),
					"icon": item["icon"],
					"child": item["child"],
					"indent": 0,
					"collapsible": 1,
					"keep_closed": 0,
					"show_arrow": 0,
					"open_in_new_tab": 0,
					"is_default_module": 0,
				}
			).db_insert()
			changed = True

	if changed:
		frappe.cache.delete_key("bootinfo")


# (sidebar name, (anchor, [row, ...])) rows of a core sidebar moved up to sit right under the anchor,
# in this order, every other row keeping its own. The dock usage audit (v1.734.0), over the 90 days
# to 2026-10-23: the Purchase Order list had 916 visits (Parker 878) and the Material Request list 133
# from four people, and ERPNext lists both below Dashboard and Supplier, Purchase Order below the two
# quotation lists as well. Home stays first, so Buying still opens on its workspace (203 visits from
# six people).
_PROMOTED_SIDEBAR_ITEMS = {
	"Buying": (("Workspace", "Buying"), [("DocType", "Purchase Order"), ("DocType", "Material Request")]),
}

# (sidebar name, [(row, ERPNext's label, ours), ...]). CRM's "Home" is its dashboard, and once the
# module opens on the Opportunity Kanban a second row called Home is wrong. Buying already calls its
# own dashboard row "Dashboard".
_RELABELED_SIDEBAR_ITEMS = {
	"CRM": [(("Dashboard", "CRM"), "Home", "Dashboard")],
}


def arrange_core_sidebar_items():
	"""``after_migrate`` entry point: reorder and relabel rows of frappe 16.50's core ``Sidebar`` documents.

	Written into ERPNext's own rows with ``db.set_value``, for the reasons ``add_core_sidebar_items``
	gives. That leaves a site's own arrangement alone: a ``Custom Sidebar`` layer that names base rows
	puts them in its own order whatever their ``idx``, and its label wins over the base's.

	Idempotent: a promoted row's place and a label are only written while they differ, so a second
	migrate writes nothing. After an ERPNext re-import, which brings back ERPNext's order and labels,
	this runs in the same migrate and puts ours back. A sidebar whose anchor row is gone is left as
	it is. Never raises.
	"""
	try:
		_arrange_sidebar_items()
	except Exception:
		# Cosmetic: a sidebar's order is not worth a failed deploy.
		frappe.log_error(title="Core sidebar items not arranged", message=frappe.get_traceback())


def _arrange_sidebar_items():
	if not frappe.db.exists("DocType", "Sidebar"):
		return

	changed = False
	for sidebar_name, (anchor, promoted) in _PROMOTED_SIDEBAR_ITEMS.items():
		if not frappe.db.exists("Sidebar", sidebar_name):
			continue
		rows = frappe.get_all(
			"Sidebar Item",
			filters={"parenttype": "Sidebar", "parent": sidebar_name},
			fields=["name", "idx", "link_type", "link_to", "url"],
			order_by="idx asc",
		)
		moved = [row for key in promoted for row in rows if _key(row) == key]
		rest = [row for row in rows if row not in moved]
		at = next((i for i, row in enumerate(rest) if _key(row) == anchor), None)
		if at is None or not moved:
			continue
		# Renumbered 1..n rather than shuffled, so no two rows tie on idx.
		for idx, row in enumerate(rest[: at + 1] + moved + rest[at + 1 :], start=1):
			if int(row.get("idx") or 0) != idx:
				frappe.db.set_value("Sidebar Item", row["name"], "idx", idx, update_modified=False)
				changed = True

	for sidebar_name, relabels in _RELABELED_SIDEBAR_ITEMS.items():
		for (link_type, link_to), theirs, ours in relabels:
			names = frappe.get_all(
				"Sidebar Item",
				filters={
					"parenttype": "Sidebar",
					"parent": sidebar_name,
					"link_type": link_type,
					"link_to": link_to,
					"label": theirs,
				},
				pluck="name",
			)
			for name in names:
				frappe.db.set_value("Sidebar Item", name, "label", ours, update_modified=False)
				changed = True

	if changed:
		frappe.cache.delete_key("bootinfo")


def drop_unreadable_sidebar_links(bootinfo):
	"""``extend_bootinfo`` (via ``boot.boot_session``): take a URL row this app adds away from anyone
	who cannot read what it opens.

	frappe filters every sidebar row by permission except a URL row: ``desk.utils.is_item_allowed``
	answers True for ``url``. Without this, someone who cannot read Opportunity would see the
	Opportunity Kanban row and CRM would open on a page that refuses them. On 2026-10-23 that was
	three enabled System Users (two External Contractors and HR), who see CRM at all only for its
	Contact and Communication rows.

	frappe leaves out a sidebar with no row that leads anywhere (``sidebar.resolve_sidebar``), and
	one left that way here goes too; the dock drops an entry whose sidebar is missing. It runs on
	every boot, cached or not, so a change of roles shows on the next page load. Never raises.
	"""
	try:
		sidebars = bootinfo.get("module_sidebars") or {}
		for sidebar_name, additions in _ADDED_SIDEBAR_ITEMS.items():
			sidebar = sidebars.get(sidebar_name)
			if not sidebar:
				continue
			urls = {
				item["url"]
				for item in additions
				if item.get("read") and not frappe.has_permission(item["read"], "read")
			}
			if not urls:
				continue
			items = [
				item
				for item in sidebar.get("items") or []
				if not (item.get("link_type") == "URL" and item.get("url") in urls)
			]
			if any(item.get("type") not in ("Section Break", "Spacer") for item in items):
				sidebar["items"] = items
			else:
				del sidebars[sidebar_name]
	except Exception:
		frappe.log_error(title="Sidebar URL rows not filtered at boot", message=frappe.get_traceback())
