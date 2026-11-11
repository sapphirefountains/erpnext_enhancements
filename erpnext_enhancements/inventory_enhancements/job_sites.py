# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Job-site warehouses: one leaf Warehouse per Build job, read and created here (v1.801.0).

The owner's decision of 2026-11-11, and the reasons, are in ``job_site_rules`` (no Frappe,
tested bench-free). This module is the part that touches the database:

* **What makes a warehouse a job site is one field**, ``Warehouse.custom_job_site_project``
  (``patches/add_job_sites_warehouse`` creates it). Not its place in the tree and not its
  name: a Stock Manager may move or rename a site in the Desk and it stays that job's site.
* **A site is created the first time material is sent to the job** (:func:`ensure_site`),
  under the group named in ``Inventory Scanner Settings.job_sites_warehouse``. It is written
  with ``ignore_permissions`` after the caller's own gate, because the people who load a
  truck hold Stock User, and ERPNext gives Warehouse create to Stock Manager and Item Manager.
  It is the only thing here written that way, it leaves a comment on the Project naming who
  did it, and the stock movement that follows still posts as the user.
* **Every read is safe before the field exists** (:func:`ready`): a fresh bench, or a deploy
  between the code landing and its patch. Then nothing is a job site and every caller behaves
  exactly as it did before this release. ``frappe.db.has_column`` takes a doctype and raises on
  a missing table rather than answering False, so the guard catches that too.

Callers: ``api/stock_scan.py`` (the page's *A job site* destination, its totals and its
lists), ``inventory_enhancements/bin_assignments.py`` (a site is never an item's bin) and
``inventory_enhancements/reorder_board.py`` (site stock is not shop stock).

Indentation is tabs (new file).
"""

import frappe
from frappe import _

from erpnext_enhancements.inventory_enhancements import job_site_rules as rules
from erpnext_enhancements.inventory_enhancements import stock_scan_rules as scan_rules

FIELD = rules.SITE_FIELD


def _settings():
	# Imported when asked, and nothing from frappe.utils above: the Reorder Board imports this
	# module, and its bench-free suite runs on a small frappe stub that has neither.
	from erpnext_enhancements.inventory_enhancements.doctype.inventory_scanner_settings.inventory_scanner_settings import (
		get_settings,
	)

	return get_settings()


def _name(value):
	return str(value or "").strip()


def _on(value):
	try:
		return bool(int(value or 0))
	except (TypeError, ValueError):
		return False


def ready():
	"""Whether the Warehouse field behind a job site exists yet. Never raises."""
	try:
		return bool(frappe.db.has_column("Warehouse", FIELD))
	except Exception:
		return False


def group():
	"""The group warehouse sites are created under, as a row, or ``None``: the setting is
	blank, or names something that is not an enabled group."""
	name = _name(_settings().get("job_sites_warehouse"))
	if not name:
		return None
	row = frappe.db.get_value("Warehouse", name, ["name", "company", "is_group", "disabled"], as_dict=True)
	if not row or not _on(row.is_group) or _on(row.disabled) or not row.company:
		return None
	return row


def enabled():
	"""Whether the page may offer *A job site*: the field exists and the group is set."""
	return bool(ready() and group())


def allowed_types():
	"""The Project Types that get a site (the setting; blank is Build)."""
	return rules.project_types(_settings().get("job_site_project_types"))


def site_map():
	"""``{warehouse: project}`` for every job site, a disabled one included: stock that was
	left at a closed site is still not the shop's."""
	if not ready():
		return {}
	rows = frappe.get_all("Warehouse", filters=[[FIELD, "is", "set"]], fields=["name", FIELD])
	return {row.name: row.get(FIELD) for row in rows if row.get(FIELD)}


def site_warehouses():
	"""Every warehouse whose stock is on a job site rather than in the shop, as a set of names:
	each job's site, and any other location directly under the Job Sites group (production's
	hand-made ``Job Site - SF`` is one: no job of its own, but nothing in it is on a shelf)."""
	if not ready():
		return set()
	names = set(site_map())
	parent = group()
	if parent:
		names.update(frappe.get_all("Warehouse", filters={"parent_warehouse": parent.name}, pluck="name"))
	return names


def site_project(warehouse):
	"""The Project ``warehouse`` is the site of, or ``None`` when it is an ordinary location."""
	warehouse = _name(warehouse)
	if not warehouse or not ready():
		return None
	return frappe.db.get_value("Warehouse", warehouse, FIELD) or None


def site_info(warehouse):
	"""``{"project", "project_name"}`` for a job site, else ``None``. What the page shows on a
	site's screen and on an item opened there."""
	project = site_project(warehouse)
	if not project:
		return None
	name = frappe.db.get_value("Project", project, "project_name")
	return {"project": project, "project_name": name or project}


def refuse_as_bin(location):
	"""A job site is never an item's bin: nothing is assigned to it and it is never anybody's
	home. Stock lists there because it is on hand, and drops off when it is gone. The same
	rule the boneyard has, for the same reason: neither is a place the shop keeps an item."""
	if location and site_project(location.name):
		frappe.throw(
			_("{0} is a job site. Items are sent there with Move to; nothing is kept there as its bin.").format(
				location.get("warehouse_name") or location.name
			)
		)


def _existing(project):
	# The first one made: get_value's default order hands back the newest, and a second
	# warehouse somebody pointed at the same job in the Desk must not take over from the one
	# that already holds the stock.
	return frappe.db.get_value(
		"Warehouse",
		{FIELD: project},
		["name", "warehouse_name", "disabled", "is_group", "company"],
		as_dict=True,
		order_by="creation asc",
	)


def ensure_site(project):
	"""The site warehouse of ``project`` as ``(row, created)``, created when it has none.

	Refuses, in a sentence, a job that is finished, a job whose type does not keep material on
	site (only when there is no site yet: one made on purpose in the Desk is used), a site that
	was disabled, and a site that is not set up. The caller has already checked who is asking.
	"""
	if not ready():
		frappe.throw(_("Job sites are being set up. Try again after the update."))
	name = _name(project)
	row = (
		frappe.db.get_value(
			"Project", name, ["name", "project_name", "status", "project_type", "company"], as_dict=True
		)
		if name
		else None
	)
	if not row:
		frappe.throw(_("There is no job {0}.").format(name or _("with that name")))

	site = _existing(row.name)
	problem = rules.site_refusal(
		row.name,
		row.project_name,
		row.status,
		row.project_type,
		allowed_types(),
		scan_rules.CLOSED_JOB_STATUSES,
		has_site=bool(site),
	)
	if problem:
		frappe.throw(problem)
	if site:
		if _on(site.disabled) or _on(site.is_group):
			frappe.throw(
				_("The job site for {0} was closed. Ask a Stock Manager to open {1} again.").format(
					row.project_name or row.name, site.warehouse_name or site.name
				)
			)
		return site, False

	parent = group()
	if not parent:
		frappe.throw(
			_(
				"Job sites are not set up yet. A Stock Manager needs to choose the Job Sites warehouse in Inventory Scanner Settings."
			)
		)
	if row.company and row.company != parent.company:
		frappe.throw(
			_("{0} belongs to {1}, and job sites are kept for {2}.").format(
				row.project_name or row.name, row.company, parent.company
			)
		)
	doc = frappe.get_doc(
		{
			"doctype": "Warehouse",
			"warehouse_name": rules.site_name(row.name, row.project_name),
			"parent_warehouse": parent.name,
			"company": parent.company,
			"is_group": 0,
			FIELD: row.name,
		}
	)
	try:
		doc.insert(ignore_permissions=True)
	except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
		# Two phones sent to the same job at once: the other one made it.
		site = _existing(row.name)
		if not site:
			raise
		return site, False
	_note_created(row.name, doc.name)
	return _existing(row.name) or frappe._dict(
		name=doc.name, warehouse_name=doc.warehouse_name, disabled=0, is_group=0, company=doc.company
	), True


def _note_created(project, warehouse):
	"""Say on the Project who opened its job site: the one write here made past permissions."""
	who = frappe.db.get_value("User", frappe.session.user, "full_name") or frappe.session.user
	try:
		frappe.get_doc("Project", project).add_comment(
			"Info",
			_("{0} opened the job site {1} from Stock Scan, to send material to this job.").format(
				frappe.utils.escape_html(who), frappe.utils.escape_html(warehouse)
			),
		)
	except Exception:
		# The site exists and the stock is about to move; a comment that could not be written
		# must not undo either. It goes to the Error Log instead.
		frappe.log_error(title="Job sites: could not note the new site on its Project")
