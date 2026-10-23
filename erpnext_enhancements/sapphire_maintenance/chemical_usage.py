# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What was put into each site's fountains, by month: the rows behind *Maintenance Chemicals Used*.

TASK-2026-02480 (B3 of the 2026-10-12 Maintenance tools plan, Phase B finance). Pricing a contract
or checking a truck restock needs "how much of what did we use at this site this month", and the
answer lives in the visits' **Consumables** tables: since v1.594.0 chemicals are *recorded* on a
visit, never issued from stock (stock moves by the container on Stock Scan), so Stock Entries
cannot say.

**Quantities are never converted and never added across units.** A chemical line's unit is the
free-text *Dose Unit* of its template line (a tablet, a cap full, a tablespoon), the dose is
fractional, and tablets per bucket vary by brand, so there is no honest dose-to-stock factor.
A row is therefore one site x month x item x unit; "3 cap full" and "2 tablespoon" of one product
are two rows. Units are compared with case and spacing ignored ("Cap Full" and "cap  full" are
one unit), and shown in the spelling that occurs most often. A line with no unit (older rows,
or an item used as a part) is counted in the **item's own stock unit**, which is what its
quantity meant before doses.

**Tablet sizes need no code of their own.** Chlorine tablets in 1" and 3" are two Items tied by
``custom_size_family`` / ``custom_size`` (``item_sizes.py``), so grouping by item already keeps the
sizes apart; the *Size* column shows the item's size so that it reads without opening the item.

**Which visits count** is the *Visit state* filter, over the Sapphire Maintenance Workflow
(Draft -> Pending Review -> Final/Submitted):

* ``Approved`` (the default): ``docstatus`` 1. Only a reviewer-approved visit is final: it is the
  one the customer's report, the invoice and the schedule roll-forward act on. A technician's
  draft can be a phone-held half-answer, and a visit in review may be sent back and changed.
* ``Approved or waiting for review``: adds visits in Pending Review (finished and sent, not yet
  approved). For a month that is still open, where reviewers have a few days' backlog.
* ``Including drafts``: also unsent drafts that have a quantity on a line. For checking what is
  on phones, not for pricing.

Canceled visits (``docstatus`` 2) never count. A line counts only when its quantity is above 0: the
wizard prefills nothing, but a 0 line means "none used" and is not a use.

A visit is placed in a month by ``visit_date`` (the day the work was done, stamped when the visit
goes for review), else its Scheduled Visit Date, else the day it was created.

Nothing here prices anything: billing is undecided (see ``chemical_billing.py``), so no money
column exists to be mistaken for an invoice.

Rows are grouped in Python from a plain bound-parameter query; Frappe 16 refuses
``"sum(x) as y"`` strings in ``get_all`` (CLAUDE.md) and the grouping needs the unit
normalisation SQL cannot do. Only ``frappe`` is imported, and the Item size field is read
through ``item_sizes`` behind ``has_column``.
"""

import calendar
import datetime

import frappe
from frappe import _
from frappe.utils import getdate, nowdate

from erpnext_enhancements.sapphire_maintenance import item_sizes

RECORD = "Sapphire Maintenance Record"
REVIEW_STATE = "Pending Review"

STATE_APPROVED = "Approved"
STATE_REVIEW = "Approved or waiting for review"
STATE_DRAFTS = "Including drafts"
STATES = (STATE_APPROVED, STATE_REVIEW, STATE_DRAFTS)
DEFAULT_STATE = STATE_APPROVED

#: How many full months before the current one the default window starts.
DEFAULT_MONTHS_BACK = 3

NO_UNIT = ""


def first_of_month(day):
	return day.replace(day=1)


def last_of_month(day):
	return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def month_key(day):
	"""``2026-07``: sorts as the calendar does and cannot be mistaken for a day."""
	return f"{day.year:04d}-{day.month:02d}"


def shift_months(day, months):
	"""The first of the month ``months`` away from ``day``'s month."""
	index = day.year * 12 + (day.month - 1) + months
	return datetime.date(index // 12, index % 12 + 1, 1)


def default_months(today=None):
	"""(first day, last day): the last three full months plus the current one.

	On 2026-10-23 that is 2026-07-01 .. 2026-10-31.
	"""
	today = getdate(today or nowdate())
	return shift_months(today, -DEFAULT_MONTHS_BACK), last_of_month(today)


def resolve_months(filters, today=None):
	"""(first day, last day) of the window the filters ask for, whole months at both ends.

	Either end may be any day of its month (the desk's date picker has no month mode): the
	start snaps to the 1st and the end to the month's last day. A missing end takes its default.
	"""
	filters = filters or {}
	default_from, default_to = default_months(today)
	start = filters.get("from_month")
	end = filters.get("to_month")
	start = first_of_month(getdate(start)) if start else default_from
	end = last_of_month(getdate(end)) if end else default_to
	if start > end:
		frappe.throw(_("From Month must not be after To Month."))
	return start, end


def state_counts(state, docstatus, workflow_state):
	"""Whether a visit in this state is counted under the ``state`` filter.

	``docstatus`` 2 (canceled) is never counted; anything but a known state filter counts as the
	default, so a typo cannot widen a finance report.
	"""
	docstatus = int(docstatus or 0)
	if docstatus == 2:
		return False
	if docstatus == 1:
		return True
	if state == STATE_DRAFTS:
		return True
	if state == STATE_REVIEW:
		return (workflow_state or "") == REVIEW_STATE
	return False


def unit_key(unit):
	"""Case and spacing ignored: ``Cap  Full`` and ``cap full`` are one unit."""
	return " ".join((unit or "").split()).casefold()


def fetch_lines(start, end, project=None, item=None):
	"""Every consumable line with a quantity on a non-canceled visit dated inside the window.

	Bound parameters only. Returns dicts with ``visit, project, item, uom, qty, docstatus,
	workflow_state, day``; the state filter and the grouping are applied by :func:`build_rows`.
	"""
	conditions = [
		"r.docstatus < 2",
		"c.qty > 0",
		"COALESCE(r.visit_date, r.scheduled_visit_date, DATE(r.creation)) BETWEEN %(start)s AND %(end)s",
	]
	params = {"start": start, "end": end}
	if project:
		conditions.append("r.project = %(project)s")
		params["project"] = project
	if item:
		conditions.append("c.item = %(item)s")
		params["item"] = item
	return frappe.db.sql(
		"""
		SELECT c.parent AS visit, r.project AS project, c.item AS item, c.uom AS uom,
			c.qty AS qty, r.docstatus AS docstatus, r.workflow_state AS workflow_state,
			COALESCE(r.visit_date, r.scheduled_visit_date, DATE(r.creation)) AS day
		FROM `tabSapphire Maintenance Consumable` c
		INNER JOIN `tabSapphire Maintenance Record` r
			ON r.name = c.parent AND c.parenttype = 'Sapphire Maintenance Record'
		WHERE """
		+ " AND ".join(conditions),
		params,
		as_dict=True,
	)


def _item_info(items):
	"""{item: {"stock_uom", "size"}} for the items on these lines."""
	items = sorted({i for i in items if i})
	if not items:
		return {}
	info = {
		row.name: {"stock_uom": row.stock_uom or "", "size": ""}
		for row in frappe.get_all("Item", filters={"name": ["in", items]}, fields=["name", "stock_uom"])
	}
	for name, found in item_sizes.item_sizes(items).items():
		info.setdefault(name, {"stock_uom": "", "size": ""})["size"] = found["size"]
	return info


def _site_names(projects):
	projects = sorted({p for p in projects if p})
	if not projects:
		return {}
	return {
		row.name: row.project_name or ""
		for row in frappe.get_all(
			"Project", filters={"name": ["in", projects]}, fields=["name", "project_name"]
		)
	}


def build_rows(lines, state=DEFAULT_STATE, info=None, site_names=None):
	"""Group lines into report rows and say how many visits the state filter left out.

	Returns ``(rows, left_out)``: ``rows`` are one per site x month x item x unit, with ``qty``
	(rounded to 4 places, which removes 0.1 + 0.2 noise and keeps a 0.25 dose), ``visits``
	(distinct visits) and ``last_visit`` (the latest day); ``left_out`` is
	``{"waiting": n, "drafts": n}``, distinct visits inside the window that the state filter did
	not count, so the report can say what it is not showing.
	"""
	info = info or {}
	site_names = site_names or {}
	groups = {}
	spellings = {}
	waiting, drafts = set(), set()
	for line in lines:
		if not state_counts(state, line.get("docstatus"), line.get("workflow_state")):
			if int(line.get("docstatus") or 0) == 0:
				if (line.get("workflow_state") or "") == REVIEW_STATE:
					waiting.add(line["visit"])
				else:
					drafts.add(line["visit"])
			continue
		day = getdate(line["day"])
		item = line.get("item")
		unit = " ".join((line.get("uom") or "").split())
		if not unit:
			unit = (info.get(item) or {}).get("stock_uom") or NO_UNIT
		key = (line.get("project") or "", month_key(day), item, unit_key(unit))
		group = groups.setdefault(key, {"qty": 0.0, "visits": set(), "last": day})
		group["qty"] += float(line.get("qty") or 0)
		group["visits"].add(line["visit"])
		if day > group["last"]:
			group["last"] = day
		counts = spellings.setdefault((item, unit_key(unit)), {})
		counts[unit] = counts.get(unit, 0) + 1

	rows = []
	for (project, month, item, ukey), group in groups.items():
		counts = spellings[(item, ukey)]
		unit = sorted(counts, key=lambda spelling: (-counts[spelling], spelling))[0]
		rows.append(
			{
				"project": project,
				"site_name": site_names.get(project, ""),
				"month": month,
				"item": item,
				"size": (info.get(item) or {}).get("size", ""),
				"uom": unit,
				"qty": round(group["qty"], 4),
				"visits": len(group["visits"]),
				"last_visit": group["last"],
			}
		)
	rows.sort(key=lambda r: (r["project"], r["month"], r["item"] or "", unit_key(r["uom"])))
	return rows, {"waiting": len(waiting), "drafts": len(drafts)}


def left_out_message(left_out, state):
	"""One sentence on the visits the state filter hid, or None when it hid nothing."""
	parts = []
	if left_out.get("waiting"):
		parts.append(_("{0} waiting for review").format(left_out["waiting"]))
	if left_out.get("drafts"):
		parts.append(_("{0} unsent drafts").format(left_out["drafts"]))
	if not parts:
		return None
	return _("Visit state: {0}. Not counted in this period: {1}.").format(state, ", ".join(parts))


def get_usage(filters=None, today=None):
	"""(rows, message) for the report. ``filters``: from_month, to_month, project, item, state."""
	filters = filters or {}
	state = filters.get("state") or DEFAULT_STATE
	if state not in STATES:
		state = DEFAULT_STATE
	start, end = resolve_months(filters, today)
	lines = fetch_lines(start, end, project=filters.get("project") or None, item=filters.get("item") or None)
	info = _item_info(line.get("item") for line in lines)
	names = _site_names(line.get("project") for line in lines)
	rows, left_out = build_rows(lines, state, info, names)
	return rows, left_out_message(left_out, state)
