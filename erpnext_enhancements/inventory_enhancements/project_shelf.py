# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Project Shelf: the one warehouse where parts bought for a job wait (v1.817.0).

The owner's decision of 2026-11-12, and every reason, is in ``project_shelf_rules`` (no
Frappe, tested bench-free). This module is the part that reads the database.

* **What makes a warehouse the Project Shelf is one setting**,
  ``Inventory Scanner Settings.project_shelf_warehouse``. Not its name and not its place in
  the tree. Blank means there is no shelf, and every function here answers "nothing", so
  every caller behaves as it did before this release.
* **Its stock is not shop stock** (:func:`not_shop_warehouses`): the same exclusion a job
  site has, and the one set every total reads, so the two cannot disagree.
* **It is never an item's bin** (:func:`refuse_as_bin`): parts wait on it for a job and
  leave. The boneyard and a job site have the same rule for the same reason.
* **Whose stock is on it is read from the Stock Ledger** (:func:`shelf_stock`): the project
  on each row at the shelf. ``project_shelf_rules`` says how that can drift and how the
  drift is reported instead of hidden.

**Other modules call three functions and must not reach past them:** :func:`warehouse`,
:func:`not_shop_warehouses` and :func:`shelf_stock`. (v1.843.0 adds the count's: the
Stock Reconciliation hook :func:`tag_reconciliation`, and :func:`count_plan`,
:func:`latest_count`, :func:`change_whose` and :func:`settle_plan` for the Bin Manager and the
cleanup patch.) Their signatures and return shapes are
fixed; the reports and the Procurement Tracker read them.

Nothing here raises to answer a question: these run inside Stock Scan's boot, the Reorder
Board and the Purchase Order's save, and one exception would take all three down.

Indentation is tabs (new file).
"""

import frappe
from frappe import _

from erpnext_enhancements.inventory_enhancements import job_sites
from erpnext_enhancements.inventory_enhancements import project_shelf_rules as rules

SETTING = "project_shelf_warehouse"
FALLBACK_SETTING = "overhead_receiving_warehouse"

#: The record that names the job(s) of a shelf movement that carries none (v1.843.0).
TAG = "Project Shelf Tag"


def _settings():
	# Imported when asked: the Reorder Board imports this module, and its bench-free suite
	# runs on a small frappe stub that cannot load the settings controller at import time.
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


def _leaf(setting):
	"""The enabled leaf warehouse a setting names, as a row, or ``None``. Never raises."""
	try:
		name = _name(_settings().get(setting))
		if not name:
			return None
		row = frappe.db.get_value(
			"Warehouse", name, ["name", "warehouse_name", "company", "is_group", "disabled"], as_dict=True
		)
		if not row or _on(row.is_group) or _on(row.disabled):
			return None
		return row
	except Exception:
		return None


def row():
	"""The Project Shelf as a warehouse row (``name``, ``warehouse_name``, ``company``), or
	``None``: the setting is blank, or names something that is not an enabled leaf."""
	return _leaf(SETTING)


def warehouse():
	"""The Project Shelf's warehouse name, or ``None`` when there is no shelf. Never raises."""
	shelf = row()
	return shelf.name if shelf else None


def label():
	"""What the shelf is called on a screen: its ``warehouse_name``, or ``None``."""
	shelf = row()
	return (shelf.warehouse_name or shelf.name) if shelf else None


def is_shelf(name):
	"""Whether ``name`` is the Project Shelf."""
	name = _name(name)
	return bool(name) and name == warehouse()


def fallback_warehouse():
	"""Where an Overhead purchase lands when its item has no bin yet
	(``Inventory Scanner Settings.overhead_receiving_warehouse``), or ``None``."""
	found = _leaf(FALLBACK_SETTING)
	return found.name if found else None


def not_shop_warehouses():
	"""Every warehouse whose stock is not stock the shop has, as a set of names: each job
	site (``job_sites.site_warehouses``) and the Project Shelf. Never raises."""
	try:
		sites = job_sites.site_warehouses()
	except Exception:
		sites = set()
	return rules.not_shop(sites, warehouse())


def former_name():
	"""The docname the shelf had before it was renamed, when nothing else has that name now.
	A label printed for ``Stores`` still opens the shelf through this
	(``api.stock_scan._warehouse``). ``None`` when there is nothing to honour."""
	try:
		shelf = warehouse()
		old = rules.former_docname(shelf) if shelf else None
		if not old or frappe.db.exists("Warehouse", old):
			return None
		return old
	except Exception:
		return None


def refuse_as_bin(location):
	"""The Project Shelf is never an item's bin: nothing is assigned to it and it is nobody's
	home. Stock lists there because it is on hand, and drops off when it is gone."""
	if location and is_shelf(location.name):
		frappe.throw(
			_(
				"{0} is the Project Shelf. Parts wait there for their job; nothing is kept there as its bin."
			).format(location.get("warehouse_name") or location.name)
		)


# ---------------------------------------------------------------------------
# What is on the shelf, by job
# ---------------------------------------------------------------------------

_EMPTY = {
	"warehouse": None,
	"rows": [],
	"projects": [],
	"untagged": [],
	"untagged_movements": [],
	"exact": True,
}


def _empty(shelf=None):
	out = {key: (list(value) if isinstance(value, list) else value) for key, value in _EMPTY.items()}
	out["warehouse"] = shelf
	return out


def _lookup(doctype, fields, names):
	"""``{name: row}`` for the given record names, in one query."""
	names = sorted({name for name in names if name})
	if not names:
		return {}
	rows = frappe.get_all(doctype, filters={"name": ["in", names]}, fields=["name", *fields])
	return {found.name: found for found in rows}


def _ledger(shelf, item_code=None):
	"""Every live Stock Ledger row at the shelf, in ledger order (posting time, then creation):
	what ``project_shelf_rules.shelf_summary`` reads. Raises like any query; the callers catch."""
	params = {"warehouse": shelf}
	condition = ""
	if _name(item_code):
		condition = " and item_code = %(item_code)s"
		params["item_code"] = _name(item_code)
	return frappe.db.sql(
		f"""
		select item_code, project, actual_qty, qty_after_transaction,
			voucher_type, voucher_no, posting_date
		from `tabStock Ledger Entry`
		where warehouse = %(warehouse)s and is_cancelled = 0{condition}
		order by posting_datetime asc, creation asc
		""",
		params,
		as_dict=True,
	)


def _tags(shelf, item_code=None):
	"""The Project Shelf Tags at the shelf (v1.843.0), oldest first, as the rules read them.

	``[]`` when there are none, and when the table cannot be read: on a site where the DocType
	has not been migrated yet, the shelf's figures are what they were before tags existed
	rather than nothing at all."""
	filters = {"warehouse": shelf}
	if _name(item_code):
		filters["item_code"] = _name(item_code)
	try:
		return frappe.get_all(
			TAG,
			filters=filters,
			fields=["voucher_type", "voucher_no", "item_code", "project", "qty", "source"],
			order_by="creation asc",
		)
	except Exception:
		return []


def shelf_stock(project=None, item_code=None):
	"""What is on the Project Shelf, by job. **The one function that answers both "what is on
	the shelf for project X" and "which projects have stock on the shelf".**

	Read from the Stock Ledger: the live rows at the shelf warehouse, grouped by the project
	on each row (``project_shelf_rules.shelf_summary`` has the arithmetic and its limits).
	``project`` and ``item_code`` are optional filters. Returns::

		{
			"warehouse": the shelf's warehouse name, or None when there is no shelf,
			"rows": [{project, project_name, item_code, item_name, stock_uom,
			          qty, tagged, over_tagged, qty_in, qty_out, last_movement,
			          last_in, counted}],
			"projects": [{project, project_name, status, project_type, items, qty}],
			"untagged": [{item_code, item_name, stock_uom, on_hand, tagged, qty, over_tagged}],
			"untagged_movements": [{posting_date, voucher_type, voucher_no, item_code, qty, kind}],
			"exact": bool,
		}

	**Limits.** ERPNext keeps on-hand by item and warehouse, with no project. These numbers
	are derived from the project on each ledger row, and from the Project Shelf Tags that name
	the jobs of a movement with none (v1.843.0: every count posted since names its jobs). A
	movement with no project and no tag (a transfer made by hand in the Desk) makes them drift. The drift is in ``untagged`` and
	``untagged_movements`` and is never hidden. ``rows[].qty`` is a job's tag balance **capped
	by what is physically on the shelf**, so it is never negative and an item's rows never add
	up to more than its on-hand; the uncapped balance is ``rows[].tagged`` (it can be negative)
	and what the cap took off is ``over_tagged``. ``exact`` is true only when the tags account
	for every unit. Show it, or the ``untagged`` list, beside any total.

	Never raises: a failure is logged and the answer is the empty shape with ``exact`` false.
	Reads the ledger without permission checks, like a report; the caller decides who may see
	the answer.
	"""
	shelf = warehouse()
	if not shelf:
		return _empty()
	try:
		ledger = _ledger(shelf, item_code)
		on_hand = {
			found.item_code: found.actual_qty
			for found in frappe.get_all(
				"Bin",
				filters={"warehouse": shelf, **({"item_code": _name(item_code)} if _name(item_code) else {})},
				fields=["item_code", "actual_qty"],
			)
		}
		summary = rules.shelf_summary(
			ledger, on_hand, project=project, item_code=item_code, tags=_tags(shelf, item_code)
		)
		items = _lookup(
			"Item",
			["item_name", "stock_uom"],
			[r["item_code"] for r in summary["rows"]] + [r["item_code"] for r in summary["untagged"]],
		)
		jobs = _lookup(
			"Project", ["project_name", "status", "project_type"], [r["project"] for r in summary["rows"]]
		)
		for found in [*summary["rows"], *summary["untagged"]]:
			item = items.get(found["item_code"]) or {}
			found["item_name"] = item.get("item_name") or found["item_code"]
			found["stock_uom"] = item.get("stock_uom")
		for found in summary["rows"]:
			found["project_name"] = (jobs.get(found["project"]) or {}).get("project_name") or found["project"]
		projects = rules.projects_summary(summary["rows"])
		for found in projects:
			job = jobs.get(found["project"]) or {}
			found["project_name"] = job.get("project_name") or found["project"]
			found["status"] = job.get("status")
			found["project_type"] = job.get("project_type")
		return {
			"warehouse": shelf,
			"rows": summary["rows"],
			"projects": projects,
			"untagged": summary["untagged"],
			"untagged_movements": summary["untagged_movements"],
			"exact": summary["exact"],
		}
	except Exception:
		try:
			frappe.log_error(title="Project Shelf: could not read what is on the shelf")
		except Exception:
			pass
		out = _empty(shelf)
		out["exact"] = False
		return out


def holding(item_code, stock=None):
	"""``{"jobs": [{"project", "project_name", "qty"}], "exact": bool}`` for one item on the
	shelf: the jobs that hold it (:func:`holders`), and whether the tags account for every
	unit of it (``project_shelf_rules.item_exact``). ``exact`` false means some of what is
	there has no job on record, or more is tagged than is there: the jobs are shown, and none
	is picked for the person."""
	item_code = _name(item_code)
	if not item_code:
		return {"jobs": [], "exact": True}
	stock = stock if stock is not None else shelf_stock(item_code=item_code)
	return {"jobs": holders(item_code, stock), "exact": rules.item_exact(stock, item_code)}


def holders(item_code, stock=None):
	"""The jobs that hold one item on the shelf, most first:
	``[{"project", "project_name", "qty"}]``. ``stock`` is a :func:`shelf_stock` answer the
	caller already has; without it the item's own rows are read."""
	item_code = _name(item_code)
	if not item_code:
		return []
	stock = stock if stock is not None else shelf_stock(item_code=item_code)
	names = {found["project"]: found.get("project_name") for found in stock.get("rows") or ()}
	return [
		{"project": name, "project_name": names.get(name) or name, "qty": qty}
		for name, qty in rules.holders(stock.get("rows") or (), item_code)
	]


# ---------------------------------------------------------------------------
# Whose a count's change is (v1.843.0)
# ---------------------------------------------------------------------------
#
# A count is a Stock Reconciliation, which carries no project, so on its own it changes what is
# on the shelf without changing any job's share. A Project Shelf Tag beside it names the jobs.
# The tag posts nothing; the count's accounting is ERPNext's, unchanged. The arithmetic is
# ``project_shelf_rules.count_split``.


def _names(projects):
	"""``{project: project_name}`` for the given jobs, in one query."""
	return {
		name: (row.get("project_name") or name)
		for name, row in _lookup("Project", ["project_name"], projects).items()
	}


def _bin_qty(item_code, shelf):
	return float(frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": shelf}, "actual_qty") or 0)


def _named_split(split, names=None):
	"""A ``count_split`` answer with each job's name beside its id, for a screen."""
	names = names if names is not None else _names([row["project"] for row in split.get("rows") or ()])
	for row in split.get("rows") or ():
		row["project_name"] = names.get(row["project"]) or row["project"]
	return split


def count_plan(item_code, counted, chosen=None, before=None):
	"""Who a count of one item at the shelf, made now, would belong to: the
	``project_shelf_rules.count_split`` answer for the shelf's on-hand (``before``, read from
	``tabBin`` when not given) against ``counted``, with ``chosen`` (``{project: qty}``) or the
	default, and each job's name. ``None`` when there is no shelf."""
	shelf = warehouse()
	if not shelf:
		return None
	item_code = _name(item_code)
	stock = shelf_stock(item_code=item_code)
	have = _bin_qty(item_code, shelf) if before is None else float(before or 0)
	split = rules.count_split(have, counted, rules.claims_of(stock.get("rows"), item_code), chosen)
	split["before"] = have
	return _named_split(split)


def write_tags(voucher_type, voucher_no, item_code, tags, source, stock_scan_log=None, note=None, shelf=None):
	"""Insert one Project Shelf Tag per ``(project, qty)`` in ``tags``. Past permissions: every
	caller has already been through the gate of the count it names (a submitted Stock
	Reconciliation, the Bin Manager's count permission, a patch). Returns the names written."""
	shelf = shelf or warehouse()
	made = []
	for project, qty in tags or ():
		doc = frappe.get_doc(
			{
				"doctype": TAG,
				"voucher_type": voucher_type,
				"voucher_no": voucher_no,
				"item_code": item_code,
				"warehouse": shelf,
				"project": project,
				"qty": qty,
				"source": source,
				"stock_scan_log": stock_scan_log,
				"note": note,
			}
		)
		doc.insert(ignore_permissions=True)
		made.append(doc.name)
	return made


def _choice_message(report):
	"""One sentence naming the items whose count the default split between several places, or
	``""``. Said where the count was posted, so the person can look."""
	guessed = [entry for entry in report or () if entry.get("choice")]
	if not guessed:
		return ""
	items = ", ".join(entry["item_code"] for entry in guessed[:5])
	more = len(guessed) - 5
	if more > 0:
		items += _(" and {0} more").format(more)
	return _(
		"On the Project Shelf, the count of {0} was shared between jobs by the default rule (oldest delivery first). "
		"If that is not whose they were, use Change whose on the item's row in the Bin Manager."
	).format(items)


def _is_lock_error(exc):
	"""Whether ``exc`` (or what caused it) is a deadlock or a lock wait timeout: the transaction is
	already gone, so swallowing it inside ``on_submit`` would let the request carry on over a
	rolled-back transaction. Those are re-raised; anything else in the default branch is logged."""
	seen = set()
	while exc is not None and id(exc) not in seen:
		seen.add(id(exc))
		for name in ("is_deadlocked", "is_timedout"):
			check = getattr(frappe.db, name, None)
			try:
				if callable(check) and check(exc):
					return True
			except Exception:
				pass
		exc = exc.__cause__ or exc.__context__
	return False


def _split_at(doc, code, shelf, chosen=None):
	"""``count_split`` for one row of a submitted count, **as of the count's own place in the
	ledger** (``project_shelf_rules.count_context``): what the shelf held and which jobs claimed it
	just before the count's posting time, not today. A Count Desk draft submitted days later, or a
	reconciliation backdated in the Desk, is judged against the shelf as it was when it was counted;
	a delivery or a take posted in between belongs after it. ``None`` when the count changed nothing
	there."""
	context = rules.count_context(_ledger(shelf, code), _tags(shelf, code), code, doc.doctype, doc.name)
	if context is None:
		return None
	before, change, claims = context
	split = rules.count_split(before, before + change, claims, chosen)
	split["before"] = before
	return _named_split(split)


def tag_reconciliation(doc, method=None):
	"""``doc_events``: Stock Reconciliation ``on_submit`` (v1.843.0). Name the jobs of every row
	the count changed at the Project Shelf.

	One hook for every way a count reaches the shelf: the Bin Manager's count, the Count Desk
	(posted, or drafted and submitted later by a Stock Manager), the phone Inventory Scanner
	Audit's draft, and a reconciliation made by hand in the Desk. Runs after ERPNext has written
	the count's ledger rows. **Each row is judged at the count's posting time** (:func:`_split_at`),
	never against today's claims: a draft counted on Monday and submitted on Wednesday takes its
	loss from the jobs that held the item on Monday.

	* **Only a count**: ``purpose`` Stock Reconciliation. ERPNext 16.60.0's Opening Stock and
	  Adjustment Entry reconciliations can read as a count up from nothing and would hand stock to
	  a job; they are left alone.
	* **A split the person named** comes in ``doc.flags.shelf_jobs`` (``{item_code: {project:
	  qty}}``, the Bin Manager's count): it was checked before the count was posted, and is
	  checked again here; a split that no longer adds up refuses the submit, and the count with
	  it, rather than post a count whose jobs are wrong. Tags carry ``source`` Count.
	* **Otherwise the default** (``project_shelf_rules.count_split``), ``source`` Default, each row
	  under its own savepoint. A failure is rolled back to that savepoint, logged, and the count
	  still posts: the drift it leaves is reported as untagged, and a count must never be lost to
	  it. A deadlock or a lock wait timeout is re-raised: the transaction is already gone.

	``doc.flags.shelf_tagged`` gets ``[{item_code, tags, choice}]`` for the caller. When the
	default had to choose between jobs, a short message says so (an alert in the Desk), unless
	the caller says it will tell the person itself (``doc.flags.shelf_quiet``, the Count Desk).

	Posts nothing: the count's own GL entries (the difference to the stock adjustment account,
	no project) are ERPNext's and are not touched.
	"""
	if (doc.get("purpose") or rules.RECONCILIATION) != rules.RECONCILIATION:
		return
	try:
		shelf = warehouse()
		rows = [row for row in (doc.get("items") or []) if shelf and _name(row.get("warehouse")) == shelf]
	except Exception:
		return
	if not rows:
		return
	# Not ``or``: a Document's flags is a frappe._dict, and an empty one is falsy.
	flags = getattr(doc, "flags", None)
	if flags is None:
		flags = frappe._dict()
	named = flags.get("shelf_jobs") or {}
	log = flags.get("shelf_log")
	report = []
	for index, row in enumerate(rows):
		code = _name(row.get("item_code"))
		chosen = named.get(code)
		if chosen is not None:
			split = _split_at(doc, code, shelf, chosen=chosen)
			if split is None:
				continue
			if split["problem"]:
				frappe.throw(_("{0} on the Project Shelf: {1}").format(code, _(split["problem"])))
			write_tags(
				doc.doctype, doc.name, code, split["tags"], rules.TAG_COUNT, stock_scan_log=log, shelf=shelf
			)
			report.append({"item_code": code, "tags": split["tags"], "choice": False})
			continue
		point = f"shelf_tag_{index}"
		frappe.db.savepoint(point)
		try:
			split = _split_at(doc, code, shelf)
			if split is None:
				continue
			write_tags(
				doc.doctype, doc.name, code, split["tags"], rules.TAG_DEFAULT, stock_scan_log=log, shelf=shelf
			)
			report.append(
				{"item_code": code, "tags": split["tags"], "choice": bool(split["choice"] and split["tags"])}
			)
		except Exception as exc:
			if _is_lock_error(exc):
				raise
			frappe.db.rollback(save_point=point)
			try:
				frappe.log_error(title=f"Project Shelf: could not name the jobs of {doc.name}")
			except Exception:
				pass
	flags.shelf_tagged = report
	message = _choice_message(report)
	if message and not flags.get("shelf_quiet"):
		try:
			frappe.msgprint(message, alert=True, indicator="orange")
		except Exception:
			pass


def drop_tags(doc, method=None):
	"""``doc_events``: Stock Reconciliation and Stock Entry ``on_cancel`` (v1.843.0). Delete the
	document's Project Shelf Tags.

	They would count for nothing anyway (the shelf reads only live ledger rows), but a tag is a
	Dynamic Link to its document, and frappe refuses to delete a canceled document something still
	links to (``LinkExistsError``). The deleted tags stay in Deleted Document. An amended count is
	a new document, and its submit names its jobs afresh. Never raises: a cancel must not fail on
	this."""
	try:
		for name in frappe.get_all(
			TAG, filters={"voucher_type": doc.doctype, "voucher_no": doc.name}, pluck="name"
		):
			frappe.delete_doc(TAG, name, ignore_permissions=True, force=True)
	except Exception:
		try:
			frappe.log_error(title=f"Project Shelf: could not remove the job tags of {doc.name}")
		except Exception:
			pass


def latest_count(item_code):
	"""The item's latest live count at the shelf, for "Change whose": ``{voucher_no,
	posting_date, before, change, split, current}`` or ``None``. The only count that may be
	re-split (:func:`change_whose`).

	``split`` is ``count_split`` for that count against what the shelf held just before it
	(``project_shelf_rules.count_context``, with every tag but the count's own), filled with the
	count's current split as the person would have entered it (``current``,
	``project_shelf_rules.count_shares``: a count up's refill of a short claim included, though it
	wrote no tag)."""
	shelf = warehouse()
	item_code = _name(item_code)
	if not shelf or not item_code:
		return None
	ledger = _ledger(shelf, item_code)
	counts = [row for row in ledger if rules.is_count(row)]
	if not counts:
		return None
	last = counts[-1]
	return count_on(item_code, last.get("voucher_no"), ledger=ledger)


def count_on(item_code, voucher_no, ledger=None, chosen=None):
	"""One count's split at the shelf: its change, what the shelf held just before it, and
	``chosen`` checked against that (``None``: the count's current split). ``None`` when the
	count has no live row for the item there."""
	shelf = warehouse()
	item_code = _name(item_code)
	voucher_no = _name(voucher_no)
	if not shelf or not item_code or not voucher_no:
		return None
	ledger = ledger if ledger is not None else _ledger(shelf, item_code)
	tags = _tags(shelf, item_code)
	context = rules.count_context(ledger, tags, item_code, rules.RECONCILIATION, voucher_no)
	if context is None:
		return None
	before, change, claims = context
	own = [
		(tag.get("project"), float(tag.get("qty") or 0))
		for tag in tags
		if tag.get("voucher_type") == rules.RECONCILIATION and tag.get("voucher_no") == voucher_no
	]
	current = rules.count_shares(before, change, claims, own)
	split = rules.count_split(before, before + change, claims, dict(current) if chosen is None else chosen)
	day = next(
		(str(row.get("posting_date") or "")[:10] for row in ledger if row.get("voucher_no") == voucher_no), ""
	)
	return {
		"voucher_type": rules.RECONCILIATION,
		"voucher_no": voucher_no,
		"posting_date": day,
		"before": before,
		"change": change,
		"current": current,
		"own": own,
		"split": _named_split(split),
	}


def _split_words(pairs):
	words = ", ".join(f"{project} {qty:g}" for project, qty in pairs if project)
	return words or "no job named"


def change_whose(item_code, voucher_no, chosen):
	"""Replace one count's tags for one item with ``chosen`` (``{project: qty}``), checked
	against what the shelf held just before that count. Raises the sentence when it does not
	add up. Returns the new split. The caller holds the permission (the Bin Manager's count).

	**Only the item's latest count at the shelf** (:func:`latest_count`): re-splitting an older one
	would change what every later count started from, and a later count's tags could then take
	from a job more than it held. Each new tag's ``note`` says who changed it, when, and what the
	split was before; the old tags themselves stay in Deleted Document."""
	latest = latest_count(item_code)
	if latest is None or latest["voucher_no"] != _name(voucher_no):
		frappe.throw(
			_("Only the latest count of {0} on the Project Shelf can be changed{1}.").format(
				item_code, _(", which is {0}").format(latest["voucher_no"]) if latest else ""
			)
		)
	answer = count_on(item_code, voucher_no, chosen=chosen or {})
	split = answer["split"]
	if split["problem"]:
		frappe.throw(_(split["problem"]))
	from frappe.utils import now_datetime

	note = _("Changed by {0} on {1}. Before: {2}.").format(
		frappe.session.user, now_datetime(), _split_words(answer["own"])
	)
	for name in frappe.get_all(
		TAG,
		filters={
			"voucher_type": rules.RECONCILIATION,
			"voucher_no": answer["voucher_no"],
			"item_code": _name(item_code),
		},
		pluck="name",
	):
		frappe.delete_doc(TAG, name, ignore_permissions=True, force=True)
	write_tags(
		rules.RECONCILIATION,
		answer["voucher_no"],
		_name(item_code),
		split["tags"],
		rules.TAG_CHANGED,
		note=note,
	)
	return split


def settle_plan():
	"""The tags the cleanup would write now (``project_shelf_rules.settle_untagged``): every
	movement off the shelf that names no job and has no tag, with the default split. ``[]``
	when there is no shelf. Reads only."""
	shelf = warehouse()
	if not shelf:
		return []
	return rules.settle_untagged(_ledger(shelf), _tags(shelf))
