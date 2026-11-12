"""Rename the ``Stores`` warehouse to ``Project Shelf``, name it in the settings, and take it
off the items that never go into stock (v1.815.0).

The owner's decision of 2026-11-12: everything ordered for a project is received onto one
shared Project Shelf, and stock there is not stock the shop has
(``inventory_enhancements/project_shelf_rules.py`` has the reasons). The shelf is the warehouse
ERPNext installed as ``Stores``: it is where every purchase line already defaulted, so it is
renamed rather than replaced, and everything that pointed at it keeps pointing at it.

In this order, each in its own step:

1. **The rename.** ``Stores - SF`` becomes ``Project Shelf - SF`` with **Frappe's own rename**
   (``frappe.model.rename_doc.rename_doc``), which rewrites every Link to the warehouse in every
   doctype: Item Defaults, Putaway Rules, on-hand rows, Stock Ledger rows, purchase order,
   receipt and material request lines, the Company's default warehouse, and anything a Single
   or a saved filter held. ``force`` is passed because ERPNext 16.54.0's Warehouse does not
   allow a rename from the Desk; that is the only thing ``force`` skips. **The same version has
   no rename hook on Warehouse**, so the docname changes and ``warehouse_name`` would not: this
   step sets it. The warehouse stays where it is in the tree (see below), stays enabled, keeps
   its stock, and nothing is merged: where a ``Project Shelf`` already exists beside a
   ``Stores``, the step is skipped and says so.
2. **The search index.** The warehouse is found by name in the Desk's search; the rename
   leaves the old name there until the Warehouse rows are indexed again. Done here, not left
   to a background job, because the deploy clears the job queue.
3. **People's saved list filters.** Each person's last-used filters, sort and columns for a
   list are one JSON row in ``__UserSettings``. Frappe's rename means to rewrite a filter that
   names the renamed record, but at v16.56.0 it looks for them with ``data LIKE '<old name>'``
   and no wildcards, which matches a row that is nothing but the name, so none. A list
   somebody last filtered to ``Stores - SF`` would open on a warehouse that no longer exists
   and show nothing. This step replaces the quoted old name with the new one in those rows,
   and drops their cached copies. It prints how many rows and how many people, and for which
   lists; **never who**: the migrate output lands in the build log, and a person's sign-in
   address does not belong there. The names are in the table for anyone who needs them.
4. **The setting.** ``Inventory Scanner Settings.project_shelf_warehouse`` is filled with the
   shelf **only where ``tabSingles`` has no row for it**, never over a stored value, an emptied
   one included: a Stock Manager who cleared it switched the Project Shelf off on purpose. On a
   Single that has never been saved, every declared default is written first
   (:func:`materialize_defaults`): the first stored row makes every other field stop reading
   its declared default (CLAUDE.md, "code that writes ONE field of a never-saved Single").
5. **The fallback.** ``overhead_receiving_warehouse`` is filled with ``Inventory Room`` under
   the same rule, where that warehouse exists as an enabled leaf: it is where an Overhead
   purchase lands when its item has no bin yet.
6. **The default warehouse of items that do not maintain stock.** The owner, 2026-11-12:
   "Blank the 705". ERPNext gave every new item an Item Default naming ``Stores``, so after the
   rename hundreds of items that are bought and never stocked say their default warehouse is
   the Project Shelf. This step blanks ``default_warehouse`` on each Item Default row that
   names the shelf **where the item has Maintain Stock off**, and on nothing else. The rule is
   read when the step runs, never from a list or a number: an item switched to Maintain Stock
   before this runs is a stock item by then, and keeps its row. A disabled item is included (it
   would carry the shelf back the day it is enabled again). The rows on items that maintain
   stock are left for the owner to sort by hand, and counted in the output. Only that one field
   is written: the row and its other defaults (supplier, price list, accounts) stay, so
   ERPNext does not add a new default row to the item at its next save
   (``Item.update_defaults_from_item_group`` adds one only where there is none). A blank
   default warehouse is legal on ERPNext 16.54.0 (the field is not required) and is what the
   Bin Manager's *Remove from this bin* already writes. For a new line ERPNext then goes on
   down its own order (``get_item_details.get_item_warehouse_``): the document's own warehouse,
   the item's default, its item group's, its brand's, the row's own, and last the Company's
   default, which is the shelf. The item's ``modified`` is not moved: a migration is not
   somebody's edit.

**Where the shelf sits, and why it is not moved.** It stays directly under the company's root
group, beside ``Inventory - SF`` and ``Job Sites - SF``. Not under ``Job Sites``: that group
refuses bins and everything in it reads as a job site. Not under ``Inventory``: ERPNext's own
reports and 23 of production's reorder rules read that group as the shop's stock, and no code
of ours runs inside them, so a shelf placed there would count as shop stock in exactly the
places this app cannot correct. Outside it, those are right with no code at all.

**What it does not do.** It moves no stock, rewrites no order line, disables no Putaway Rule
and changes no Item Default of an item that maintains stock. After it runs, the output lists
how many of each still point at the shelf, so the owner can decide what to do with them.

**It cannot raise, so it must say what it did.** A patch that raises aborts ``bench migrate``,
which on this repo is the deploy. A step that fails is caught, rolled back to its own
savepoint, logged with its traceback, and the next one still runs. Every step answers one of
four words (:data:`DONE`, :data:`ALREADY`, :data:`SKIPPED`, :data:`FAILED`) with its reason,
the whole list is printed to the migrate output with the counts, and **when any step was
skipped or failed, one Error Log row** titled :data:`REPORT_TITLE` lists them with what to do
by hand. A run where every step is done or already done writes no Error Log row.

**A site with nothing to rename is not a failure.** Where there is no shop group
``Inventory - SF``, or no warehouse called ``Stores`` and no ``Project Shelf`` either (a fresh
bench, a test site, any other company's site), every step answers :data:`ABSENT`, the output
says so in one line each, and **no Error Log row is written**: that log alerts people, and
"this site never had the warehouse" is not something anyone has to finish by hand.

Safe twice: the second run finds the shelf, an indexed name, no saved filter left to rewrite,
both settings' rows and no non-stock item's default left on the shelf, and answers "already"
(the index step answers "done" again; it is a rebuild). **A run by hand some time later is not
a no-op: it applies each rule again.** A non-stock item that somebody has pointed at the shelf
on purpose since the deploy is blanked again by step 6.
"""

import frappe

from erpnext_enhancements.inventory_enhancements import project_shelf_rules as rules

SHOP_GROUP = "Inventory - SF"
OLD_NAME = rules.FORMER_NAME
NEW_NAME = rules.SHELF_NAME
FALLBACK_NAME = "Inventory Room"
SETTINGS = "Inventory Scanner Settings"
SETTING = "project_shelf_warehouse"
FALLBACK_SETTING = "overhead_receiving_warehouse"
SAVEPOINT = "rename_stores_to_project_shelf"

DONE, ALREADY, SKIPPED, FAILED = "done", "already", "skipped", "failed"

#: A step that had nothing to work on because this site never had the warehouse. Printed, and
#: never the reason for an Error Log row.
ABSENT = "not needed here"

#: The one Error Log row a run with a skipped or failed step leaves.
REPORT_TITLE = "Project Shelf patch (rename_stores_to_project_shelf): finish these steps by hand"

RERUN = "bench --site <site> execute erpnext_enhancements.patches.rename_stores_to_project_shelf.execute"

#: What to do for a step that did not finish. Every one can also be finished by running the
#: patch again (:data:`RERUN`): it is safe twice.
RECOVERY = {
	"rename": (
		f"Run the patch again: {RERUN}. Or, as a System Manager in the console, "
		'frappe.rename_doc("Warehouse", "Stores - SF", "Project Shelf - SF", force=True) and then set '
		'that warehouse\'s Warehouse Name to "Project Shelf".'
	),
	"search": "Nothing to do by hand: the name is indexed the next time the warehouse is saved.",
	"filters": (
		"Nothing to do by hand. A list that opens empty because it is still filtered to the old "
		"warehouse name is fixed by clearing that filter once."
	),
	"setting": (
		"Open Inventory Scanner Settings, choose the Project Shelf in Project Shelf Warehouse and save. "
		"Until it is set, nothing defaults to the shelf and its stock still counts as shop stock."
	),
	"fallback": (
		"Open Inventory Scanner Settings and choose the shop's general receiving location in "
		"Overhead Receiving Warehouse. Until it is set, an Overhead line whose item has no bin "
		"keeps the location the order gave it."
	),
	"defaults": (
		f"Run the patch again: {RERUN}. Nothing waits on it: no page reads the shelf as an item's "
		"bin. It only blanks Default Warehouse on the Item Defaults that name the Project Shelf, "
		"on items with Maintain Stock off."
	),
}

STEPS = (
	("rename", "The warehouse"),
	("search", "The search index"),
	("filters", "People's saved list filters"),
	("setting", "The Project Shelf setting"),
	("fallback", "The Overhead receiving setting"),
	("defaults", "The default warehouse of items that do not maintain stock"),
)


def _say(text):
	try:
		print(text)
	except Exception:
		pass


def _company():
	"""The company whose ``Stores`` is renamed: the shop group's. ``None`` where the shop group
	does not exist, which is every site this was not written for."""
	return frappe.db.get_value("Warehouse", SHOP_GROUP, "company") or None


def _named(warehouse_name, company):
	return frappe.db.get_value(
		"Warehouse",
		{"warehouse_name": warehouse_name, "company": company},
		["name", "is_group", "disabled"],
		as_dict=True,
	)


def rename_warehouse():
	"""Rename ``Stores`` to ``Project Shelf``. ``(status, detail, shelf docname or None)``."""
	company = _company()
	if not company:
		return ABSENT, f"there is no group warehouse {SHOP_GROUP}, so this is not the site with a Stores to rename", None
	old = _named(OLD_NAME, company)
	new = _named(NEW_NAME, company)
	if new and old:
		return (
			SKIPPED,
			f"both {old.name} and {new.name} exist; nothing is merged, so neither was touched",
			None,
		)
	if new:
		if new.is_group:
			return SKIPPED, f"{new.name} exists and is a group, so it cannot be the shelf", None
		return ALREADY, new.name, new.name
	if not old:
		return ABSENT, f'there is no warehouse called "{OLD_NAME}" and none called "{NEW_NAME}"', None
	if old.is_group:
		return SKIPPED, f"{old.name} is a group of warehouses, not one place, so it was left alone", None

	abbr = frappe.db.get_value("Company", company, "abbr") or ""
	target = f"{NEW_NAME} - {abbr}" if abbr else NEW_NAME
	if frappe.db.exists("Warehouse", target):
		return SKIPPED, f"a warehouse with the docname {target} already exists; nothing is merged", None

	from frappe.model.rename_doc import rename_doc

	# force: Warehouse is not renameable from the Desk on ERPNext 16.54.0; that is all it skips.
	# No alert (there is no one to show it to) and no queued re-index (the next step does it).
	renamed = rename_doc(
		doctype="Warehouse", old=old.name, new=target, force=True, show_alert=False, rebuild_search=False
	)
	renamed = renamed or target
	# ERPNext 16.54.0 has no rename hook on Warehouse and the doctype has no autoname field, so
	# the label people read is still "Stores" until this line.
	frappe.db.set_value("Warehouse", renamed, "warehouse_name", NEW_NAME, update_modified=False)
	if not frappe.db.exists("Warehouse", renamed) or frappe.db.exists("Warehouse", old.name):
		raise RuntimeError(f"after the rename, {renamed} is missing or {old.name} is still there")
	return DONE, f"{old.name} is now {renamed}", renamed


def rebuild_search(shelf):
	"""Index the Warehouse names again so the Desk's search finds the new one. ``(status, detail)``."""
	if not shelf:
		return SKIPPED, "there is no Project Shelf to index"
	from frappe.utils.global_search import rebuild_for_doctype

	rebuild_for_doctype("Warehouse")
	return DONE, "Warehouse names indexed again"


def rewrite_saved_filters(shelf):
	"""Point people's saved list filters at the renamed warehouse. ``(status, detail)``.

	The quoted old docname is replaced with the quoted new one in each ``__UserSettings`` row
	that holds it: a filter value is a JSON string, so the quotes keep a longer name that
	merely contains the old one from matching. Then the cached copy of each such row is
	dropped, or the next save from the browser's cache would write the old name back.
	"""
	old = rules.former_docname(shelf) if shelf else None
	if not shelf or not old:
		return SKIPPED, "there is no Project Shelf whose old name is known"
	like = f'%"{old}"%'
	rows = frappe.db.sql("select `user`, `doctype` from `__UserSettings` where `data` like %s", (like,))
	if not rows:
		return ALREADY, f"no saved list filter names {old}"
	frappe.db.sql(
		"update `__UserSettings` set `data` = replace(`data`, %s, %s) where `data` like %s",
		(f'"{old}"', f'"{shelf}"', like),
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
		f"{len(rows)} saved list {'setting' if len(rows) == 1 else 'settings'} of {people} "
		f"{'person' if people == 1 else 'people'} now name {shelf}: lists {', '.join(lists)}"
	)


def _stored_fields():
	return {row[0] for row in frappe.db.sql("select field from tabSingles where doctype = %s", (SETTINGS,))}


def materialize_defaults(stored):
	"""On a Single with no stored row, write every declared default before anything else is
	written to it. Returns how many were written. With any row stored, nothing: that Single
	has been saved, and a missing row there is no longer a default waiting to be read."""
	if stored:
		return 0
	todo = {}
	for df in frappe.get_meta(SETTINGS).fields:
		name, default = getattr(df, "fieldname", None), getattr(df, "default", None)
		if name and default not in (None, ""):
			todo[name] = default
	for name, default in todo.items():
		frappe.db.set_single_value(SETTINGS, name, default)
	return len(todo)


def fill_setting(field, value, missing):
	"""Write ``value`` into one setting where it has no ``tabSingles`` row. ``(status, detail)``."""
	if not value:
		return SKIPPED, missing
	if not frappe.db.exists("DocType", SETTINGS):
		return SKIPPED, f"{SETTINGS} is not installed"
	stored = _stored_fields()
	if field in stored:
		current = frappe.db.get_single_value(SETTINGS, field)
		return ALREADY, f"a value is stored ({current or 'blank, left blank on purpose'}); not written over"
	written = materialize_defaults(stored)
	frappe.db.set_single_value(SETTINGS, field, value)
	frappe.clear_document_cache(SETTINGS, SETTINGS)
	note = f" (first save of {SETTINGS}: {written} declared defaults written with it)" if written else ""
	return DONE, f"{value}{note}"


def fallback_warehouse(shelf):
	"""The shop's general receiving location: an enabled leaf called ``Inventory Room`` in the
	shelf's company, or ``None``."""
	if not shelf:
		return None
	company = frappe.db.get_value("Warehouse", shelf, "company")
	row = _named(FALLBACK_NAME, company) if company else None
	if not row or row.is_group or row.disabled:
		return None
	return row.name


def _maintains_stock(value):
	try:
		return bool(int(value or 0))
	except (TypeError, ValueError):
		return bool(value)


def _plural(count, one, many):
	return f"{count} {one if count == 1 else many}"


def defaults_at(shelf):
	"""The Item Default rows of Items that name ``shelf``, split by the one rule this step has:
	``(blank, keep)``, each a list of ``(row name, item, item is disabled)``. ``blank`` is the
	rows of items with Maintain Stock off; ``keep`` is the rest. Read fresh each time."""
	blank, keep = [], []
	rows = frappe.db.sql(
		"""select d.name, d.parent, i.is_stock_item, i.disabled
		from `tabItem Default` d
		inner join `tabItem` i on i.name = d.parent
		where d.parenttype = 'Item' and d.default_warehouse = %(shelf)s""",
		{"shelf": shelf},
	)
	for name, item, is_stock_item, disabled in rows:
		(keep if _maintains_stock(is_stock_item) else blank).append((name, item, _maintains_stock(disabled)))
	return blank, keep


def blank_item_defaults(shelf):
	"""Blank ``default_warehouse`` on the Item Defaults that name the shelf, on items that do not
	maintain stock. ``(status, detail)``. The detail gives counts and never an item."""
	if not shelf:
		return SKIPPED, "there is no Project Shelf, so no Item Default was looked at"
	blank, keep = defaults_at(shelf)
	kept = (
		f"{_plural(len(keep), 'row', 'rows')} on items that maintain stock "
		f"{'was' if len(keep) == 1 else 'were'} left as {'it is' if len(keep) == 1 else 'they are'}"
	)
	if not blank:
		return ALREADY, f"no item that does not maintain stock names {shelf} as its default warehouse; {kept}"
	for name, _item, _disabled in blank:
		frappe.db.set_value("Item Default", name, "default_warehouse", None, update_modified=False)
	# ERPNext reads an item's defaults from the cached Item (``get_item_defaults``).
	for item in sorted({item for _name, item, _disabled in blank}):
		try:
			frappe.clear_document_cache("Item", item)
		except Exception:
			pass
	left, _keep = defaults_at(shelf)
	if left:
		raise RuntimeError(
			f"{_plural(len(left), 'Item Default row', 'Item Default rows')} of items that do not maintain "
			f"stock still name {shelf}"
		)
	disabled = sum(1 for _name, _item, off in blank if off)
	return DONE, (
		f"{_plural(len(blank), 'Item Default row', 'Item Default rows')} on items that do not maintain "
		f"stock no longer {'names' if len(blank) == 1 else 'name'} {shelf} ({disabled} on disabled items); {kept}"
	)


def _count(query, values):
	try:
		rows = frappe.db.sql(query, values)
		return rows[0] if rows else None
	except Exception:
		return None


def counts(shelf):
	"""What now points at the shelf, as lines of text. Reads only; a count that cannot be read
	is left out rather than guessed."""
	if not shelf:
		return []
	lines = []
	values = {"shelf": shelf}
	row = _count(
		"select count(*), coalesce(sum(actual_qty), 0) from `tabBin` where warehouse = %(shelf)s and actual_qty > 0",
		values,
	)
	if row:
		lines.append(f"- On the shelf now: {int(row[0])} items, {float(row[1]):g} units. No stock was moved.")
	row = _count(
		"""select count(*), coalesce(sum(i.is_stock_item = 1 and i.disabled = 0), 0)
		from `tabItem Default` d inner join `tabItem` i on i.name = d.parent
		where d.parenttype = 'Item' and d.default_warehouse = %(shelf)s""",
		values,
	)
	if row:
		lines.append(
			f"- Item Defaults that still name it: {int(row[0])} ({int(row[1])} on enabled stock items). "
			"Those on items that maintain stock were left for the owner to sort by hand; the pages "
			"do not read them as the item's bin."
		)
	row = _count("select count(*) from `tabPutaway Rule` where warehouse = %(shelf)s and disable = 0", values)
	if row:
		lines.append(
			f"- Enabled bin assignments (Putaway Rules) on it: {int(row[0])}. None was changed; "
			"the pages no longer read them as the item's bin."
		)
	row = _count(
		"""select
			coalesce(sum(po.docstatus = 1 and p.project_type = %(overhead)s), 0),
			coalesce(sum(po.docstatus = 1 and coalesce(p.project_type, '') <> %(overhead)s), 0),
			coalesce(sum(po.docstatus = 0), 0)
		from `tabPurchase Order Item` poi
		inner join `tabPurchase Order` po on po.name = poi.parent
		left join `tabProject` p on p.name = poi.project
		where poi.warehouse = %(shelf)s and po.docstatus < 2
			and po.status not in ('Closed', 'Delivered', 'Completed', 'To Bill')
			and poi.qty > poi.received_qty and coalesce(poi.delivered_by_supplier, 0) = 0""",
		{**values, "overhead": rules.OVERHEAD_PROJECT_TYPE},
	)
	if row:
		lines.append(
			f"- Open order lines that name it: {int(row[0])} submitted lines on Overhead projects, "
			f"{int(row[1])} submitted lines on other projects, {int(row[2])} draft lines. None was changed; "
			"an Overhead line is offered the item's own bin when it is received."
		)
	row = _count("select count(*) from `tabCompany` where default_warehouse = %(shelf)s", values)
	if row and int(row[0]):
		lines.append(
			"- It is the Company's default warehouse, so ERPNext's own default for a new line is the shelf."
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
		title = f"Project Shelf patch: {label} failed"
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
		lines.append("What points at the Project Shelf now:")
		lines.extend(extra)
	return "\n".join(lines)


def execute():
	outcomes = {}
	renamed = _step("renaming the warehouse", rename_warehouse)
	shelf = renamed[2] if len(renamed) > 2 else None
	outcomes["rename"] = renamed[:2]
	if renamed[0] == ABSENT:
		# This site never had the warehouse: nothing follows from a rename that was not needed.
		for key, _label in STEPS[1:]:
			outcomes[key] = (ABSENT, "nothing was renamed on this site")
	else:
		outcomes["search"] = _step("indexing the warehouse names", rebuild_search, shelf)
		outcomes["filters"] = _step("rewriting saved list filters", rewrite_saved_filters, shelf)
		outcomes["setting"] = _step(
			"filling the Project Shelf setting",
			fill_setting,
			SETTING,
			shelf,
			"there is no Project Shelf to point it at",
		)
		fallback = None
		try:
			fallback = fallback_warehouse(shelf)
		except Exception:
			fallback = None
		outcomes["fallback"] = _step(
			"filling the Overhead receiving setting",
			fill_setting,
			FALLBACK_SETTING,
			fallback,
			f'there is no enabled warehouse called "{FALLBACK_NAME}" to point it at',
		)
		outcomes["defaults"] = _step(
			"blanking the default warehouse of items that do not maintain stock", blank_item_defaults, shelf
		)

	try:
		extra = counts(shelf)
	except Exception:
		extra = []
	text = report(outcomes, extra)
	_say("rename_stores_to_project_shelf:\n" + text)
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
