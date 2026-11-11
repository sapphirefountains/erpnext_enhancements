"""Set up job-site warehouses: the Warehouse field, the group, the setting (v1.801.0).

The owner's decision of 2026-11-11: material sent to a Build job goes to that job's own site
warehouse, one per project, and stock there must not read as stock the shop has
(``inventory_enhancements/job_site_rules.py`` has the reasons). A site is opened by Stock
Scan the first time something is sent to a job; this patch makes the three things that needs.
In this order, each in its own step:

1. **The field.** ``Warehouse.custom_job_site_project``, a Link to Project, is what makes a
   warehouse a job site. Created with ``create_custom_fields`` (``is_system_generated = 1``, so
   the fixture export skips it), the way the store-run receipt fields are. A new column on a
   normal doctype with no default, so every existing warehouse reads empty, which is right:
   none of them is a job's site. Also named in ``hooks.after_install``, because
   ``bench install-app`` writes all of patches.txt to the Patch Log as already run.
2. **The tree's numbering, only if it is broken.** Production's Warehouse tree has two empty
   groups sharing one position. Frappe adds a node by shifting those numbers, and reports that
   filter by a group warehouse read them, so a new group goes into a counted tree or not at
   all. ``rebuild_tree`` renumbers from each warehouse's parent, which is the part that is
   right, so no warehouse changes group. Skipped when no two warehouses share a number.
3. **The group.** One group Warehouse, ``warehouse_name`` "Job Sites", beside the shop's own
   stock: under the parent of :data:`SHOP_GROUP` (``Inventory - SF``, whose parent is the
   company's root). Beside, not inside, so a reorder rule or a count that covers the shop does
   not cover job sites. Found by ``warehouse_name`` and company, never by docname, and reused.
   Skipped where the shop group does not exist (a fresh or test site).
4. **The setting.** ``Inventory Scanner Settings.job_sites_warehouse`` is filled with the
   group **only where ``tabSingles`` has no row for it**, never over a stored value, an emptied
   one included: a Stock Manager who cleared it switched job sites off on purpose. A Single
   that has never been saved is left alone too (writing its first row would make every other
   field without a row stop reading its declared default).
5. **The old "Job Site" warehouse comes into the tree.** Production has a leaf called
   ``Job Site - SF`` with no parent, made by hand. It holds no stock and has no ledger rows,
   and received purchase-order lines for non-stock items name it as where they were delivered.
   It is moved under the new group and otherwise left exactly as it is: still enabled, still
   usable on an order, and with no Project of its own it is not any one job's site. Only a leaf
   of that name with no parent is touched.

**It cannot raise, so it must say what it did.** A patch that raises aborts ``bench migrate``,
which on this repo is the deploy, so a step that fails is caught, logged with its traceback,
and the next one still runs. But the patch is then recorded in the Patch Log as run, which
looks exactly like success. So every step answers one of four words (:data:`DONE`,
:data:`ALREADY`, :data:`SKIPPED`, :data:`FAILED`) with its reason, the whole list is printed
to the migrate output, and **when any step was skipped or failed, one Error Log row** titled
:data:`REPORT_TITLE` lists all five with what to do by hand for each one that did not finish.
A run where every step is done or already done writes no Error Log row: that log alerts
people, and a clean deploy is not an alert.

Safe twice: the second run finds the field, a counted tree, the group, the setting's row and
nothing to move, and answers "already" five times.
"""

import frappe

from erpnext_enhancements.inventory_enhancements import job_site_rules as rules

SHOP_GROUP = "Inventory - SF"
OLD_NAME = "Job Site"
SETTINGS = "Inventory Scanner Settings"
SETTING = "job_sites_warehouse"
FIELD = rules.SITE_FIELD
SAVEPOINT = "add_job_sites_warehouse"

DONE, ALREADY, SKIPPED, FAILED = "done", "already", "skipped", "failed"

#: The one Error Log row a run with a skipped or failed step leaves.
REPORT_TITLE = "Job sites patch (add_job_sites_warehouse): finish these steps by hand"

RERUN = "bench --site <site> execute erpnext_enhancements.patches.add_job_sites_warehouse.execute"

#: What to do for a step that did not finish, by step. Every one can also be finished by
#: running the patch again (:data:`RERUN`): it is safe twice.
RECOVERY = {
	"field": f"Run the patch again: {RERUN}",
	"tree": (
		"Run the patch again, or as a System Manager rebuild the Warehouse tree "
		"(frappe.utils.nestedset.rebuild_tree_for_doctype with doctype Warehouse)."
	),
	"group": (
		'In the Desk, create a Warehouse named "Job Sites" with Is Group ticked, under the '
		"company's root warehouse (beside the shop's stock, not inside it)."
	),
	"setting": (
		"Open Inventory Scanner Settings, choose that group in Job Sites Warehouse and save. "
		'Until it is set, Stock Scan does not offer "A job site".'
	),
	"old": 'Open the Warehouse "Job Site", set its Parent Warehouse to the Job Sites group and save.',
}

STEPS = (
	("field", "The Warehouse field"),
	("tree", "The Warehouse tree's numbering"),
	("group", "The Job Sites group"),
	("setting", "The setting"),
	("old", 'The old "Job Site" warehouse'),
)


def _say(text):
	try:
		print(text)
	except Exception:
		pass


def _has_field():
	try:
		return bool(frappe.db.has_column("Warehouse", FIELD))
	except Exception:
		return False


def ensure_field():
	"""Create the Warehouse field. ``(status, detail)``; raises when the column is not there
	afterwards, which :func:`_step` turns into ``failed``."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	had = _has_field()
	create_custom_fields(
		{
			"Warehouse": [
				{
					"fieldname": FIELD,
					"fieldtype": "Link",
					"options": "Project",
					"label": "Job Site For Project",
					"insert_after": "parent_warehouse",
					"depends_on": "eval:!doc.is_group",
					"no_copy": 1,
					"search_index": 1,
					"in_standard_filter": 1,
					# A site is found by its job whoever is asking: a person limited to some
					# Projects must still see that the stock is not the shop's.
					"ignore_user_permissions": 1,
					"description": (
						"Set by Stock Scan when material is first sent to a job. A warehouse with a "
						"project here is that job's site: its stock is not counted as shop stock, and "
						"parts taken from it are charged to the job."
					),
				}
			]
		},
		update=True,
	)
	if not _has_field():
		raise RuntimeError(f"Warehouse.{FIELD} is not there after create_custom_fields")
	return (ALREADY if had else DONE), f"Warehouse.{FIELD}"


def tree_is_broken():
	"""Whether two warehouses share a left number: the sign the nested set is out of step."""
	rows = frappe.db.sql("select count(*), count(distinct lft) from `tabWarehouse`")
	if not rows:
		return False
	total, distinct = (rows[0][0] or 0), (rows[0][1] or 0)
	return total != distinct


def repair_tree():
	"""Renumber the Warehouse tree when it is broken. ``(status, detail)``."""
	if not tree_is_broken():
		return ALREADY, "no two warehouses share a position"
	from frappe.utils.nestedset import rebuild_tree

	rebuild_tree("Warehouse")
	return DONE, "renumbered from each warehouse's parent"


def ensure_group():
	"""The Job Sites group, created if missing. ``(status, detail, name or None)``."""
	shop = frappe.db.get_value(
		"Warehouse", SHOP_GROUP, ["name", "company", "is_group", "parent_warehouse"], as_dict=True
	)
	if not shop or not shop.is_group or not shop.company or not shop.parent_warehouse:
		return SKIPPED, f"there is no group warehouse {SHOP_GROUP} with a parent to put it beside", None
	existing = frappe.db.get_value(
		"Warehouse",
		{"warehouse_name": rules.GROUP_NAME, "company": shop.company},
		["name", "is_group"],
		as_dict=True,
	)
	if existing and not existing.is_group:
		# A leaf somebody already called "Job Sites" is theirs: sites are not put under a leaf.
		return SKIPPED, f"{existing.name} exists and is not a group, so it was left alone", None
	if existing:
		return ALREADY, existing.name, existing.name
	doc = frappe.get_doc(
		{
			"doctype": "Warehouse",
			"warehouse_name": rules.GROUP_NAME,
			"parent_warehouse": shop.parent_warehouse,
			"company": shop.company,
			"is_group": 1,
		}
	)
	doc.insert(ignore_permissions=True)
	return DONE, f"{doc.name}, under {shop.parent_warehouse}", doc.name


def fill_setting(group):
	"""Write ``group`` into the setting where it has no ``tabSingles`` row. ``(status, detail)``."""
	if not group:
		return SKIPPED, "there is no Job Sites group to point it at"
	if not frappe.db.exists("DocType", SETTINGS):
		return SKIPPED, f"{SETTINGS} is not installed"
	stored = {row[0] for row in frappe.db.sql("select field from tabSingles where doctype = %s", (SETTINGS,))}
	if not stored:
		return SKIPPED, f"{SETTINGS} has never been saved, and its first row is not written by a patch"
	if SETTING in stored:
		value = frappe.db.get_single_value(SETTINGS, SETTING)
		return ALREADY, f"a value is stored ({value or 'blank, which switches job sites off'}); not written over"
	frappe.db.set_single_value(SETTINGS, SETTING, group)
	frappe.clear_document_cache(SETTINGS, SETTINGS)
	return DONE, group


def adopt_old_warehouse(group):
	"""Move a parentless leaf called "Job Site" under ``group``. ``(status, detail)``."""
	if not group:
		return SKIPPED, "there is no Job Sites group to move it under"
	company = frappe.db.get_value("Warehouse", group, "company")
	rows = frappe.get_all(
		"Warehouse",
		filters={"warehouse_name": OLD_NAME, "company": company, "is_group": 0},
		fields=["name", "parent_warehouse"],
	)
	moved = []
	for row in rows:
		if row.parent_warehouse:
			continue
		doc = frappe.get_doc("Warehouse", row.name)
		doc.parent_warehouse = group
		doc.save(ignore_permissions=True)
		moved.append(doc.name)
	if not moved:
		return ALREADY, f'no leaf called "{OLD_NAME}" is without a parent'
	return DONE, f"moved {', '.join(moved)} under {group}; still enabled"


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
		title = f"Job sites patch: {label} failed"
		try:
			frappe.log_error(title=title, message=frappe.get_traceback())
		except Exception:
			pass
		return FAILED, f'{type(error).__name__}: {error} (traceback in the Error Log row "{title}")'


def report(outcomes):
	"""The five steps as text, one line each, with what to do for each one not finished."""
	lines = []
	for number, (key, label) in enumerate(STEPS, start=1):
		status, detail = outcomes.get(key, (SKIPPED, "not reached"))
		lines.append(f"{number}. {label}: {status}. {detail}")
		if status in (SKIPPED, FAILED):
			lines.append(f"   By hand: {RECOVERY[key]}")
	return "\n".join(lines)


def install_field():
	"""``hooks.after_install``: the field alone, and never an exception. A fresh site has no
	warehouse tree yet for the other steps to work on."""
	_step("creating the Warehouse field", ensure_field)


def execute():
	outcomes = {}
	outcomes["field"] = _step("creating the Warehouse field", ensure_field)
	outcomes["tree"] = _step("renumbering the Warehouse tree", repair_tree)
	made = _step("creating the Job Sites group", ensure_group)
	group = made[2] if len(made) > 2 else None
	outcomes["group"] = made[:2]
	outcomes["setting"] = _step("filling the setting", fill_setting, group)
	outcomes["old"] = _step("moving the old Job Site warehouse", adopt_old_warehouse, group)

	text = report(outcomes)
	_say("add_job_sites_warehouse:\n" + text)
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
