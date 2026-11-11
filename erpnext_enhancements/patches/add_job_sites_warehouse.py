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
2. **The tree's numbering, only if it is broken.** Production's Warehouse tree has two groups
   sharing one position (``Service Van - SF`` and ``Customer Rental Locations - SF`` both at
   lft 24, measured 2026-11-11). Frappe adds a node by shifting those numbers, and reports that
   filter by a group warehouse read them, so a new group goes into a counted tree or not at
   all. ``rebuild_tree`` renumbers from each warehouse's parent, which is the part that is
   right. Skipped when no two warehouses share a number.
3. **The group.** One group Warehouse, ``warehouse_name`` "Job Sites", beside the shop's own
   stock: under the parent of :data:`SHOP_GROUP` (``Inventory - SF``, whose parent is the
   company's root). Beside, not inside, so a reorder rule or a count that covers the shop does
   not cover job sites. Found by ``warehouse_name`` and company, never by docname, and reused.
   Skipped quietly where the shop group does not exist (a fresh or test site).
4. **The setting.** ``Inventory Scanner Settings.job_sites_warehouse`` is filled with the
   group **only where ``tabSingles`` has no row for it**, never over a stored value, an emptied
   one included: a Stock Manager who cleared it switched job sites off on purpose. A Single
   that has never been saved is left alone too (writing its first row would make every other
   field without a row stop reading its declared default).
5. **The old "Job Site" warehouse comes into the tree.** Production has a leaf called
   ``Job Site - SF`` with no parent, made by hand on 2026-07-29. It holds no stock and has no
   ledger rows, and two received purchase-order lines for non-stock items name it as where
   they were delivered. It is moved under the new group and otherwise left exactly as it is:
   still enabled, still usable on an order, and with no Project of its own it is not any one
   job's site. Only a leaf of that name with no parent is touched.

Cannot raise -- a patch that raises aborts ``bench migrate``, which on this repo is the deploy.
Each step logs to the Error Log if it fails and the next one still runs. Safe twice: the second
run finds the field, a counted tree, the group, the setting's row and nothing to move.
"""

import frappe

from erpnext_enhancements.inventory_enhancements import job_site_rules as rules

SHOP_GROUP = "Inventory - SF"
OLD_NAME = "Job Site"
SETTINGS = "Inventory Scanner Settings"
SETTING = "job_sites_warehouse"
FIELD = rules.SITE_FIELD
SAVEPOINT = "add_job_sites_warehouse"


def _say(text):
	try:
		print(text)
	except Exception:
		pass


def ensure_field():
	"""Create the Warehouse field. True when the column is there afterwards."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

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
	return bool(frappe.db.has_column("Warehouse", FIELD))


def tree_is_broken():
	"""Whether two warehouses share a left number: the sign the nested set is out of step."""
	rows = frappe.db.sql("select count(*), count(distinct lft) from `tabWarehouse`")
	if not rows:
		return False
	total, distinct = (rows[0][0] or 0), (rows[0][1] or 0)
	return total != distinct


def repair_tree():
	"""Renumber the Warehouse tree when it is broken. True when it was renumbered."""
	if not tree_is_broken():
		return False
	from frappe.utils.nestedset import rebuild_tree

	rebuild_tree("Warehouse")
	return True


def ensure_group():
	"""The Job Sites group's name, created if missing; ``None`` when there is no shop group."""
	shop = frappe.db.get_value(
		"Warehouse", SHOP_GROUP, ["name", "company", "is_group", "parent_warehouse"], as_dict=True
	)
	if not shop or not shop.is_group or not shop.company or not shop.parent_warehouse:
		return None
	existing = frappe.db.get_value(
		"Warehouse",
		{"warehouse_name": rules.GROUP_NAME, "company": shop.company},
		["name", "is_group"],
		as_dict=True,
	)
	if existing:
		# A leaf somebody already called "Job Sites" is theirs: sites are not put under a leaf.
		return existing.name if existing.is_group else None
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
	return doc.name


def fill_setting(group):
	"""Write ``group`` into the setting where it has no ``tabSingles`` row. True if written."""
	if not group or not frappe.db.exists("DocType", SETTINGS):
		return False
	stored = {row[0] for row in frappe.db.sql("select field from tabSingles where doctype = %s", (SETTINGS,))}
	if not stored or SETTING in stored:
		return False
	frappe.db.set_single_value(SETTINGS, SETTING, group)
	frappe.clear_document_cache(SETTINGS, SETTINGS)
	return True


def adopt_old_warehouse(group):
	"""Move a parentless leaf called "Job Site" under ``group``. Returns the names moved."""
	if not group:
		return []
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
	return moved


def _step(label, fn, *args):
	try:
		frappe.db.savepoint(SAVEPOINT)
		return fn(*args)
	except Exception:
		try:
			frappe.db.rollback(save_point=SAVEPOINT)
		except Exception:
			pass
		try:
			frappe.log_error(title=f"Job sites patch: {label} failed", message=frappe.get_traceback())
		except Exception:
			pass
		return None


def install_field():
	"""``hooks.after_install``: the field alone, and never an exception. A fresh site has no
	warehouse tree yet for the other steps to work on."""
	_step("creating the Warehouse field", ensure_field)


def execute():
	field = _step("creating the Warehouse field", ensure_field)
	renumbered = _step("renumbering the Warehouse tree", repair_tree)
	group = _step("creating the Job Sites group", ensure_group)
	if not group:
		_say(f"add_job_sites_warehouse: field {'ready' if field else 'missing'}; no Job Sites group; nothing set.")
		return
	written = _step("filling the setting", fill_setting, group)
	moved = _step("moving the old Job Site warehouse", adopt_old_warehouse, group) or []
	_say(
		f"add_job_sites_warehouse: field {'ready' if field else 'missing'}; "
		f"tree {'renumbered' if renumbered else 'left as it was'}; group {group}; "
		f"setting {'filled' if written else 'left as it was'}; moved {', '.join(moved) or 'nothing'}."
	)
