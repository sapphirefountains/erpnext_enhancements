"""Swap two warehouse names, so the bays sit in the Inventory Room (v1.823.0).

The owner, looking at the Warehouse Map's list on 2026-11-17, where a bay reads "Bay A1 ·
Inventory › Row 1": "Those bays should exist in the Inventory Room, not the Inventory warehouse."

The shop's tree on production, read that day: ``Inventory - SF`` is a group directly under
``All Warehouses - SF``. It holds Row 1 to Row 5 (each holding Bays, then Shelves, then Bins) and
two leaves, ``Inventory Room - SF`` and ``Boneyard - SF``. The leaf called Inventory Room holds
stock, and it is where Stock Scan receives an Overhead purchase whose item has no bin yet
(``Inventory Scanner Settings.overhead_receiving_warehouse``). ERPNext refuses to make a
warehouse with stock into a group, so the room cannot become the group the bays sit in. The
owner chose to swap the names instead, and to call the leaf **Receiving**:

1. The leaf ``Inventory Room - SF`` becomes ``Receiving - SF`` (``warehouse_name`` Receiving).
2. The group ``Inventory - SF`` becomes ``Inventory Room - SF`` (``warehouse_name`` Inventory Room).

Afterwards the tree reads Inventory Room › Row 1–5 › Bay › Shelf › Bin, plus Inventory Room ›
Receiving and Inventory Room › Boneyard. **Nothing moves in the tree and no stock moves**: only
the two names change. That is also why a rename is right and a new group would be wrong. ERPNext's
own reports and 23 of production's reorder rules read the ``Inventory`` group as the shop's
stock, and so does this app wherever it rolls stock up at a group. Every one of them holds the
group by its name in a Link, and a rename carries every Link with it, so they all go on reading
the same warehouse.

**The rename is Frappe's own** (``frappe.model.rename_doc.rename_doc``), the way
``patches/rename_stores_to_project_shelf`` (v1.817.0) did it. Read at frappe v16.56.0 and erpnext
v16.54.0, the versions production runs:

* It rewrites every Link to the warehouse in every doctype, submitted documents included, and
  every Single that holds it (the Single is saved through the ORM). For step 1 that was, on
  2026-11-17: 84 Stock Ledger Entries and 84 Bin rows (56 with stock), 70 Item Defaults, 83
  Putaway Rules, 29 reorder rules (``Item Reorder.warehouse``), 17 Purchase Order lines (14 on
  orders received in full, 3 on a closed order, none still to receive), one Warehouse Map box
  (``Warehouse Floor Map Shape.warehouse``) and ``overhead_receiving_warehouse``. For step 2 it is
  23 reorder rules (``warehouse_group``) and the children's ``parent_warehouse`` and
  ``old_parent``. Nothing else names either warehouse: no List Filter, Report, Dashboard Chart,
  Number Card, Kanban Board, Workspace, Auto Email Report, Property Setter, Custom Field,
  Notification or script did, as text, on that day.
* ``force`` is passed because ERPNext 16.54.0's Warehouse has no ``allow_rename``; that is the
  only check it skips. The step still refuses to merge (see below).
* **The same version has no rename hook on Warehouse**, so the docname changes and
  ``warehouse_name``, the label people read, would not. Each rename step sets it.
* This app's own ``Warehouse`` ``after_rename`` (``warehouse_map_files.follow_warehouse_rename``)
  carries each rename into the map boxes' ``bin_order`` text. No box's order named either
  warehouse on 2026-11-17, so it has nothing to do here, and it cannot raise.

**The order is the point.** After step 1 the name ``Inventory Room - SF`` is free, and step 2
gives it to the group. So anything that still says ``Inventory Room - SF`` when step 2 runs
silently changes meaning, from the room to the whole shop. Links are safe: step 1 has moved them
all to Receiving. People's saved list filters are not, and they are the one known thing a rename
misses:

**Saved list filters.** Each person's last-used filters, sort and columns for a list are one JSON
row in ``__UserSettings``. Frappe's rename means to rewrite a filter that names the renamed
record, but at v16.56.0 it looks for them with ``data LIKE '<old name>'`` and no wildcards, which
matches a row that is nothing but the name, so none. Each rename is therefore followed by a step
that replaces the quoted old name with the quoted new one in those rows and drops their cached
copies (the quotes keep a longer name that merely contains the old one from matching). Step 1's
rewrite must **finish** before step 2 starts, or a list somebody last filtered to the room would
open on the whole shop. So the group is renamed only when step 1 and its rewrite both answered
done or already.

A rewrite never runs while the old name belongs to a warehouse: once the group is called
Inventory Room, a filter that names it means the group, and a second run must not send it to
Receiving. The output says how many rows, how many people and which lists; **never who**: the
migrate output lands in the build log, and a person's sign-in address does not belong there.

Then the Warehouse names are indexed again for the Desk's search, here and not in a background
job, because the deploy clears the job queue.

**Never merge two warehouses.** Where ``Receiving - SF`` already exists beside the room, step 1
is skipped and says so. Where the group's new name is taken by anything but the room it has just
renamed, step 2 is skipped. Step 2 never runs unless step 1 answered done or already.

**What it does not do, and what is left for a person.**

* **The QR label.** A label encodes ``/stock-scan?w=<warehouse name>``, so the label printed for
  the room now opens the group, which Stock Scan refuses (it says a label that opens a group is
  out of date). Print a new label for Receiving from Warehouse Labels and stick it over the old
  one.
* **The map.** The box that was linked to the room is linked to ``Receiving - SF`` after step 1,
  because that is the same warehouse. If the box was meant as the whole room, re-link it in the
  map editor; nothing here guesses.
* It moves no stock, changes no order line beyond the name, and touches no Putaway Rule, Item
  Default or reorder rule beyond the name.

**It cannot raise, so it must say what it did.** A patch that raises aborts ``bench migrate``,
which on this repo is the deploy. A step that fails is caught, rolled back to its own savepoint,
logged with its traceback, and the next one still runs (or says why it may not). Every step
answers one of :data:`DONE`, :data:`ALREADY`, :data:`SKIPPED`, :data:`FAILED` with its reason,
the whole list is printed to the migrate output with counts, and **when any step was skipped or
failed, one Error Log row** titled :data:`REPORT_TITLE` lists them with what to do by hand. A run
where every step is done or already done writes no Error Log row.

**A site without these warehouses is not a failure.** Where there is no ``Inventory Room - SF``
leaf and no ``Receiving - SF`` (a fresh bench, a test site, any other company's site), every step
answers :data:`ABSENT`, the output says so in one line each, and no Error Log row is written.

Safe twice: the second run finds Receiving, the group already called Inventory Room, no saved
filter left to rewrite, and answers "already" (the index step answers "done" again; it is a
rebuild).
"""

import frappe

#: The group before the swap. Its name is free afterwards.
GROUP = "Inventory - SF"
#: The leaf before the swap; the group's name afterwards.
ROOM = "Inventory Room - SF"
#: The leaf afterwards.
RECEIVING = "Receiving - SF"
RECEIVING_LABEL = "Receiving"
ROOM_LABEL = "Inventory Room"
SETTINGS = "Inventory Scanner Settings"
SETTING = "overhead_receiving_warehouse"
SAVEPOINT = "swap_inventory_room_names"

DONE, ALREADY, SKIPPED, FAILED = "done", "already", "skipped", "failed"

#: A step that had nothing to work on because this site never had these warehouses. Printed,
#: and never the reason for an Error Log row.
ABSENT = "not needed here"

#: The one Error Log row a run with a skipped or failed step leaves.
REPORT_TITLE = "Inventory Room patch (swap_inventory_room_names): finish these steps by hand"

RERUN = "bench --site <site> execute erpnext_enhancements.patches.swap_inventory_room_names.execute"

#: What to do for a step that did not finish. Every one can also be finished by running the
#: patch again (:data:`RERUN`): it is safe twice.
RECOVERY = {
	"room": (
		f"Run the patch again: {RERUN}. Or, as a System Manager in the console, "
		f'frappe.rename_doc("Warehouse", "{ROOM}", "{RECEIVING}", force=True), then set that '
		f'warehouse\'s Warehouse Name to "{RECEIVING_LABEL}", then run the patch again for the rest. '
		"Never rename the group before this is done."
	),
	"room_filters": (
		f"Run the patch again: {RERUN}. The group is not renamed until this is done, so nothing "
		"points at the wrong warehouse meanwhile."
	),
	"group": (
		f"Run the patch again: {RERUN}. Or, only once {ROOM} is no longer a leaf (step 1 done), "
		f'frappe.rename_doc("Warehouse", "{GROUP}", "{ROOM}", force=True) and set its Warehouse '
		f'Name to "{ROOM_LABEL}". Until then the tree reads Inventory › Receiving, which is right, '
		"only not yet renamed."
	),
	"group_filters": (
		"Nothing to do by hand. A list that opens empty because it is still filtered to the old "
		"group name is fixed by clearing that filter once."
	),
	"search": "Nothing to do by hand: a name is indexed the next time its warehouse is saved.",
}

STEPS = (
	("room", f"The room: {ROOM_LABEL} becomes {RECEIVING_LABEL}"),
	("room_filters", "People's saved list filters that named the room"),
	("group", f"The group: Inventory becomes {ROOM_LABEL}"),
	("group_filters", "People's saved list filters that named the group"),
	("search", "The search index"),
)

_FIELDS = ["name", "warehouse_name", "is_group", "disabled", "company", "parent_warehouse"]


def _say(text):
	try:
		print(text)
	except Exception:
		pass


def _warehouse(name):
	return frappe.db.get_value("Warehouse", name, _FIELDS, as_dict=True) or None


def _plural(count, one, many):
	return f"{count} {one if count == 1 else many}"


def _rename(old, new, label):
	"""Frappe's rename, then the label ERPNext 16.54.0 has no hook to set. Returns the new
	docname, and raises if the rename did not take."""
	from frappe.model.rename_doc import rename_doc

	# force: Warehouse is not renameable from the Desk on ERPNext 16.54.0; that is all it skips.
	# No alert (there is no one to show it to) and no queued re-index (a later step does it).
	renamed = rename_doc(
		doctype="Warehouse", old=old, new=new, force=True, show_alert=False, rebuild_search=False
	)
	renamed = renamed or new
	frappe.db.set_value("Warehouse", renamed, "warehouse_name", label, update_modified=False)
	try:
		frappe.clear_document_cache("Warehouse", renamed)
	except Exception:
		pass
	if not frappe.db.exists("Warehouse", renamed) or frappe.db.exists("Warehouse", old):
		raise RuntimeError(f"after the rename, {renamed} is missing or {old} is still there")
	return renamed


def rename_room():
	"""Step 1: the leaf ``Inventory Room - SF`` becomes ``Receiving - SF``.
	``(status, detail, Receiving's docname or None)``."""
	room = _warehouse(ROOM)
	receiving = _warehouse(RECEIVING)
	if receiving:
		if receiving.is_group:
			return SKIPPED, f"{RECEIVING} exists and is a group, so it cannot be the room", None
		if room and not room.is_group:
			return (
				SKIPPED,
				f"both {ROOM} and {RECEIVING} exist as places that hold stock; nothing is merged, "
				"so neither was touched",
				None,
			)
		return ALREADY, f"{RECEIVING} is there, and {ROOM} is no longer a place that holds stock", RECEIVING
	if not room:
		return ABSENT, f"there is no warehouse {ROOM} and none called {RECEIVING}", None
	if room.is_group:
		return (
			SKIPPED,
			f"{ROOM} is already a group and there is no {RECEIVING}, so the room was renamed some other "
			"way; nothing was touched",
			None,
		)
	group = _warehouse(GROUP)
	if not group or not group.is_group:
		return (
			SKIPPED,
			f"there is no group {GROUP} to take the room's name afterwards, so the room was left as it is",
			None,
		)
	if room.company != group.company:
		return SKIPPED, f"{ROOM} and {GROUP} belong to different companies; nothing was touched", None
	renamed = _rename(ROOM, RECEIVING, RECEIVING_LABEL)
	return DONE, f"{ROOM} is now {renamed}, called {RECEIVING_LABEL}", renamed


def rewrite_saved_filters(old, new):
	"""Point people's saved list filters that name ``old`` at ``new``. ``(status, detail)``.

	The quoted old docname is replaced with the quoted new one in each ``__UserSettings`` row that
	holds it: a filter value is a JSON string, so the quotes keep a longer name that merely
	contains the old one from matching. Then the cached copy of each such row is dropped, or the
	next save from the browser's cache would write the old name back.

	Nothing is rewritten while ``old`` is the name of a warehouse that exists: a filter naming it
	then means that warehouse. That is what keeps a second run from sending filters on the renamed
	group to Receiving.
	"""
	if not new or not frappe.db.exists("Warehouse", new):
		return SKIPPED, f"there is no {new or 'warehouse'} to point them at"
	if frappe.db.exists("Warehouse", old):
		return (
			ALREADY,
			f"{old} is the name of a warehouse that exists now, so a filter naming it means that one",
		)
	like = f'%"{old}"%'
	rows = frappe.db.sql("select `user`, `doctype` from `__UserSettings` where `data` like %s", (like,))
	if not rows:
		return ALREADY, f"no saved list filter names {old}"
	frappe.db.sql(
		"update `__UserSettings` set `data` = replace(`data`, %s, %s) where `data` like %s",
		(f'"{old}"', f'"{new}"', like),
	)
	for user, doctype in rows:
		try:
			frappe.cache.hdel("_user_settings", f"{doctype}::{user}")
		except Exception:
			pass
	# Counts only. This text is printed to the migrate output and may be written to the Error
	# Log; a user id here is a sign-in address, so who the rows belong to is never said.
	lists = sorted({doctype for _user, doctype in rows})
	people = len({user for user, _doctype in rows})
	return DONE, (
		f"{_plural(len(rows), 'saved list setting', 'saved list settings')} of "
		f"{_plural(people, 'person', 'people')} now name {new}: lists {', '.join(lists)}"
	)


def rename_group(room_step, room_filters_step):
	"""Step 2: the group ``Inventory - SF`` becomes ``Inventory Room - SF``. ``(status, detail)``.
	Only after the room has given the name up and its saved filters have moved."""
	if room_step not in (DONE, ALREADY):
		return SKIPPED, "the room was not renamed, and the group never takes a name that may still be in use"
	if room_filters_step not in (DONE, ALREADY):
		return (
			SKIPPED,
			"the saved list filters that named the room were not moved to Receiving; renaming the group "
			"now would point them at the whole shop",
		)
	group = _warehouse(GROUP)
	room = _warehouse(ROOM)
	if room:
		if not room.is_group:
			return SKIPPED, f"{ROOM} is still a place that holds stock; nothing is merged"
		if group:
			return (
				SKIPPED,
				f"both {GROUP} and {ROOM} exist as groups; nothing is merged, so neither was touched",
			)
		return ALREADY, f"{ROOM} is the group, holding {_plural(_children(ROOM), 'warehouse', 'warehouses')}"
	if not group:
		return SKIPPED, f"there is no {GROUP} to rename and no group called {ROOM}"
	if not group.is_group:
		return SKIPPED, f"{GROUP} is not a group, so it was left alone"
	renamed = _rename(GROUP, ROOM, ROOM_LABEL)
	return DONE, (
		f"{GROUP} is now {renamed}, called {ROOM_LABEL}, holding "
		f"{_plural(_children(renamed), 'warehouse', 'warehouses')}"
	)


def _children(parent):
	try:
		return int(frappe.db.count("Warehouse", {"parent_warehouse": parent}) or 0)
	except Exception:
		return 0


def rebuild_search(any_renamed):
	"""Index the Warehouse names again so the Desk's search finds the new ones. ``(status, detail)``."""
	if not any_renamed:
		return SKIPPED, "neither warehouse has its new name, so there is nothing new to index"
	from frappe.utils.global_search import rebuild_for_doctype

	rebuild_for_doctype("Warehouse")
	return DONE, "Warehouse names indexed again"


def _count(query, values):
	try:
		rows = frappe.db.sql(query, values)
		return rows[0] if rows else None
	except Exception:
		return None


def counts(receiving, group):
	"""What points at the two warehouses now, as lines of text, and what is left for a person.
	Reads only; a count that cannot be read is left out rather than guessed."""
	lines = []
	if receiving:
		values = {"w": receiving}
		row = _count(
			"select count(*), coalesce(sum(actual_qty), 0) from `tabBin` where warehouse = %(w)s and actual_qty > 0",
			values,
		)
		if row:
			lines.append(
				f"- In {receiving} now: {int(row[0])} items, {float(row[1]):g} units. No stock was moved."
			)
		row = _count(
			"""select
				(select count(*) from `tabItem Default` where default_warehouse = %(w)s),
				(select count(*) from `tabPutaway Rule` where warehouse = %(w)s and disable = 0),
				(select count(*) from `tabItem Reorder` where warehouse = %(w)s)""",
			values,
		)
		if row:
			lines.append(
				f"- Naming {receiving}: {int(row[0])} Item Defaults, {int(row[1])} enabled bin assignments "
				f"(Putaway Rules), {int(row[2])} reorder rules. Each is the same warehouse under its new name."
			)
		try:
			setting = frappe.db.get_single_value(SETTINGS, SETTING)
		except Exception:
			setting = None
		if setting:
			lines.append(f"- Where an Overhead line with no bin is received ({SETTINGS}): {setting}.")
		row = _count("select count(*) from `tabWarehouse Floor Map Shape` where warehouse = %(w)s", values)
		if row and int(row[0]):
			lines.append(
				f"- Warehouse Map boxes linked to {receiving}: {int(row[0])}. A box drawn as the whole room "
				"should be re-linked to the room in the map editor."
			)
		lines.append(
			f"- The QR label printed for {ROOM_LABEL} now opens the group, which Stock Scan refuses. "
			f"Print a new label for {RECEIVING_LABEL} from Warehouse Labels."
		)
	if group:
		row = _count("select count(*) from `tabItem Reorder` where warehouse_group = %(g)s", {"g": group})
		if row:
			lines.append(
				f"- {group} holds {_plural(_children(group), 'warehouse', 'warehouses')} directly; "
				f"{int(row[0])} reorder rules count stock across it."
			)
	return lines


def _step(label, fn, *args):
	"""Run one step in its own savepoint. Its answer, or ``(FAILED, …)``: never an exception."""
	try:
		frappe.db.savepoint(SAVEPOINT)
		return fn(*args)
	except Exception as error:
		try:
			frappe.db.rollback(save_point=SAVEPOINT)
		except Exception:
			pass
		title = f"Inventory Room patch: {label} failed"
		try:
			frappe.log_error(title=title, message=frappe.get_traceback())
		except Exception:
			pass
		return FAILED, f'{type(error).__name__}: {error} (traceback in the Error Log row "{title}")'


def report(outcomes, extra=()):
	"""The steps as text, one line each, with what to do for each one not finished, then the
	counts."""
	lines = []
	for number, (key, label) in enumerate(STEPS, start=1):
		status, detail = outcomes.get(key, (SKIPPED, "not reached"))
		lines.append(f"{number}. {label}: {status}. {detail}")
		if status in (SKIPPED, FAILED):
			lines.append(f"   By hand: {RECOVERY[key]}")
	if extra:
		lines.append("Where things stand:")
		lines.extend(extra)
	return "\n".join(lines)


def execute():
	outcomes = {}
	room = _step("renaming the room", rename_room)
	receiving = room[2] if len(room) > 2 else None
	outcomes["room"] = room[:2]
	group = None
	if room[0] == ABSENT:
		# This site never had these warehouses: nothing follows from a rename that was not needed.
		for key, _label in STEPS[1:]:
			outcomes[key] = (ABSENT, "nothing was renamed on this site")
	else:
		if receiving:
			outcomes["room_filters"] = _step(
				"moving saved list filters to Receiving", rewrite_saved_filters, ROOM, receiving
			)
		else:
			outcomes["room_filters"] = (SKIPPED, "the room was not renamed, so no filter was moved")
		outcomes["group"] = _step(
			"renaming the group", rename_group, outcomes["room"][0], outcomes["room_filters"][0]
		)
		if outcomes["group"][0] in (DONE, ALREADY):
			group = ROOM
			outcomes["group_filters"] = _step(
				"moving saved list filters to the Inventory Room", rewrite_saved_filters, GROUP, ROOM
			)
		else:
			outcomes["group_filters"] = (SKIPPED, "the group was not renamed, so no filter was moved")
		outcomes["search"] = _step(
			"indexing the warehouse names",
			rebuild_search,
			bool(receiving) or outcomes["group"][0] in (DONE, ALREADY),
		)

	try:
		extra = counts(receiving, group)
	except Exception:
		extra = []
	text = report(outcomes, extra)
	_say("swap_inventory_room_names:\n" + text)
	if any(status in (SKIPPED, FAILED) for status, _detail in outcomes.values()):
		try:
			frappe.log_error(
				title=REPORT_TITLE,
				message=(
					"The patch is recorded as run, and it did not finish every step. Nothing below "
					f"is retried by itself.\n\n{text}\n\nEvery step can be finished by running the "
					f"patch again; it is safe twice:\n{RERUN}"
				),
			)
		except Exception:
			pass
