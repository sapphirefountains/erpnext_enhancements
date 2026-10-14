"""Server side of the Opportunity board (``/desk/opportunity-board``), form kit P6.

The board that is to replace the patched stock Kanban (ERPNext TASK-2026-02774). It answers
one question in one GET: which deals are open, and what does each card, value-stream row and
forecast figure need to know about them. Everything it decides on the client (ages, rows,
totals, the forecast, what a drop means) is in ``public/js/opportunity_board/board.js``; this
module only reads.

Things it is careful about, including the ones that look like bugs:

* **The caller's permissions, not a wall display's.** The Sales Pipeline TV board reads with
  ``frappe.get_all`` because it is a shared screen gated at page level. This board is where a
  person works their deals, so it reads through ``frappe.get_list``: user permissions, sharing
  and if-owner rules apply, and a deal the caller cannot read is simply not on it. The stream
  rows and completeness are then read only for the names that list returned.
* **Stages come from the status options, as the Sales Pipeline page reads them**
  (``sales_pipeline._stage_columns``: the live meta, so a renamed stage reshapes both boards
  with no deploy). Closed Won and Lost are sent as ``close`` stages: drop targets that open the
  guided page's Close step, never columns, because the board shows open deals only.
* **Open** is the guided start screen's own rule (``form_kit/config/opportunity.py``
  ``mine.open``): any status but Closed Won, Lost, Closed and Converted. A deal whose status the
  options no longer list is still sent; the board gives it a column of its own, so the totals
  never silently drop it.
* **Historical deals are left out** (``crm_enhancements/historical.py``), guarded with
  ``meta.has_field`` so a site without the column still loads the board.
* **Every custom field is read only when the meta has it**: a field list naming a missing
  column fails the whole query, and ``doc_events``-era installs have met that before.
* **Days in stage are counted on the client from dates the server sends** (``since``, the date
  of ``custom_stage_changed_on`` or, before that field was stamped, ``modified``; and ``today``,
  the site's date), so the board and the TV board count the same days wherever the phone is.
  The amber / red thresholds are the Sales Pipeline Dashboard's
  (``sales_pipeline._thresholds``: ERPNext Enhancements Settings, falling back to 45 / 90).
* **``modified`` is sent as ``str(modified)``**, exactly the string ``form_kit.saver`` compares
  against when a card is moved with ``save_step``. Any other formatting turns every move into a
  conflict.
* **Completeness is best-effort.** It is the form kit's own batch
  (``form_kit.completeness.completeness_for``, 500 names a call), and a failure there logs and
  leaves the meter off rather than taking the board down.
"""

import frappe
from frappe.utils import flt, getdate, nowdate

from erpnext_enhancements.crm_enhancements.historical import HISTORICAL_FIELD
from erpnext_enhancements.crm_enhancements.page.sales_pipeline.sales_pipeline import (
	WON_STATUS,
	_stage_columns,
	_thresholds,
)
from erpnext_enhancements.form_kit.completeness import MAX_BATCH, completeness_for
from erpnext_enhancements.form_kit.config import CONFIGS
from erpnext_enhancements.form_kit.config.opportunity import STREAM_ROW_FIELD, STREAMS_FIELD

DOCTYPE = "Opportunity"

#: Not open. The guided start screen's own list, so "open" means one thing in both places.
CLOSED_STATUSES = tuple(CONFIGS[DOCTYPE]["mine"]["open"]["not_in"])

#: The closing stages a card can be dropped on (they open the guided Close step).
LOST_STATUS = "Lost"
CLOSING_STATUSES = (WON_STATUS, LOST_STATUS)

#: The most deals the board loads. 80 were open on 2026-10-13; past this the board says so.
MAX_DEALS = 1000

#: Read when the meta has them. ``name``, ``status`` and ``modified`` are always there.
OPTIONAL_FIELDS = (
	"custom_opportunity_name",
	"customer_name",
	"party_name",
	"opportunity_amount",
	"probability",
	"expected_closing",
	"custom_stage_changed_on",
)


def _stages():
	"""``[{"label", "kind"}]``: open stages, then On Hold, then the closing stages."""
	open_stages, parked = _stage_columns()
	field = frappe.get_meta(DOCTYPE).get_field("status")
	options = [opt.strip() for opt in ((field.options if field else "") or "").split("\n") if opt.strip()]
	stages = [{"label": label, "kind": "open"} for label in open_stages]
	stages += [{"label": label, "kind": "parked"} for label in parked]
	stages += [{"label": label, "kind": "close"} for label in options if label in CLOSING_STATUSES]
	return stages


def _chunks(names, size):
	for start in range(0, len(names), size):
		yield names[start : start + size]


def _streams(meta, names):
	"""``{name: [value stream, ...]}`` in each deal's own row order, each stream once.

	One bound query per 500 names on the ``Value Stream`` child table (the singular is the
	child; see "Value Stream vs Value Streams" in the module README). The table and column come
	from the meta, never the request.
	"""
	out = {name: [] for name in names}
	field = meta.get_field(STREAMS_FIELD)
	child = field.options if field else None
	if not names or not child or not frappe.get_meta(child).has_field(STREAM_ROW_FIELD):
		return out
	for chunk in _chunks(names, MAX_BATCH):
		rows = frappe.db.sql(
			f"""select `parent`, `{STREAM_ROW_FIELD}` as value from `tab{child}`
			where `parenttype` = %(doctype)s and `parentfield` = %(field)s and `parent` in %(names)s
			order by `parent`, `idx`""",
			{"doctype": DOCTYPE, "field": STREAMS_FIELD, "names": tuple(chunk)},
			as_dict=True,
		)
		for row in rows:
			streams = out.get(row["parent"])
			value = (row["value"] or "").strip()
			if streams is not None and value and value not in streams:
				streams.append(value)
	return out


def _completeness(names):
	"""``{name: completeness}``, or ``{}`` when the batch fails (the meters stay off)."""
	result = {}
	try:
		for chunk in _chunks(names, MAX_BATCH):
			result.update(completeness_for(DOCTYPE, chunk))
	except Exception:
		frappe.log_error(title="Opportunity board: completeness failed", message=frappe.get_traceback())
		return {}
	return result


def _date(value):
	return str(getdate(value)) if value else None


@frappe.whitelist(methods=["GET"])
def get_board():
	"""The open deals, with exactly what the board's cards, rows and forecast strip read.

	Returns ``{"stages": [{"label", "kind"}], "deals": [...], "thresholds": {"amber", "red"},
	"today", "currency", "truncated"}``; each deal is ``{"name", "title", "account", "status",
	"amount", "probability", "expected_closing", "since", "modified", "streams",
	"completeness"}``. ``amount`` is None when the deal has none above zero. See the module
	docstring for what is read and why.
	"""
	frappe.has_permission(DOCTYPE, "read", throw=True)
	meta = frappe.get_meta(DOCTYPE)
	present = [fieldname for fieldname in OPTIONAL_FIELDS if meta.has_field(fieldname)]
	filters = [["status", "not in", list(CLOSED_STATUSES)]]
	if meta.has_field(HISTORICAL_FIELD):
		filters.append([HISTORICAL_FIELD, "!=", 1])
	records = frappe.get_list(
		DOCTYPE,
		filters=filters,
		fields=["name", "status", "modified", *present],
		order_by="modified desc",
		limit_page_length=MAX_DEALS + 1,
	)
	truncated = len(records) > MAX_DEALS
	records = records[:MAX_DEALS]
	names = [record.get("name") for record in records]
	streams = _streams(meta, names)
	completeness = _completeness(names)
	amber, red = _thresholds()

	deals = []
	for record in records:
		name = record.get("name")
		account = record.get("customer_name") or record.get("party_name") or ""
		amount = flt(record.get("opportunity_amount"))
		probability = record.get("probability")
		deals.append(
			{
				"name": name,
				"title": record.get("custom_opportunity_name") or account or name,
				"account": account,
				"status": record.get("status") or "",
				"amount": amount if amount > 0 else None,
				"probability": flt(probability) if probability not in (None, "") else None,
				"expected_closing": _date(record.get("expected_closing")),
				"since": _date(record.get("custom_stage_changed_on") or record.get("modified")),
				"modified": str(record.get("modified")),
				"streams": streams.get(name, []),
				"completeness": completeness.get(name),
			}
		)

	return {
		"stages": _stages(),
		"deals": deals,
		"thresholds": {"amber": amber, "red": red},
		"today": nowdate(),
		"currency": frappe.defaults.get_global_default("currency") or "USD",
		"truncated": truncated,
	}
