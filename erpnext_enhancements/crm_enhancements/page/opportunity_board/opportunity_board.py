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
from frappe.utils import date_diff, escape_html, flt, formatdate, get_datetime, getdate, nowdate

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
	"opportunity_owner",
	"custom_snoozed_until",
	"custom_snooze_reason",
)

#: P6.8's snooze: a date the deal stays hidden until, and an optional reason. Hidden on the form,
#: no_copy, defined in fixtures/custom_field.json; the guided flow never shows them (hidden fields
#: are not in it), so snoozing has its own endpoint, :func:`snooze`, rather than ``save_step``.
SNOOZE_UNTIL = "custom_snoozed_until"
SNOOZE_REASON = "custom_snooze_reason"
SNOOZE_FIELDS = (SNOOZE_UNTIL, SNOOZE_REASON)

#: The furthest ahead a snooze may run. A deal parked for longer than a year belongs On Hold.
MAX_SNOOZE_DAYS = 366

#: ``custom_snooze_reason`` is Small Text; a reason is a sentence, not a document.
MAX_REASON = 500


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


def _owner_names(records):
	"""``{user: full name}`` for the owners on the board (the owner filter's choices and the table)."""
	names = {}
	for record in records:
		user = record.get("opportunity_owner")
		if user and user not in names:
			names[user] = frappe.utils.get_fullname(user) or user
	return names


@frappe.whitelist(methods=["GET"])
def get_board():
	"""The open deals, with exactly what the board's views, filters and forecast strip read.

	Returns ``{"stages": [{"label", "kind"}], "deals": [...], "thresholds": {"amber", "red"},
	"today", "currency", "truncated", "can_snooze"}``; each deal is ``{"name", "title", "account",
	"status", "amount", "probability", "expected_closing", "since", "modified", "streams",
	"completeness", "owner", "owner_name", "snoozed_until", "snooze_reason"}``. ``amount`` is None
	when the deal has none above zero. Snoozed deals are sent too: the page hides them (and says how
	many), so "Show snoozed" needs no second request. See the module docstring for what is read and
	why.
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
	owner_names = _owner_names(records)
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
				"owner": record.get("opportunity_owner") or "",
				"owner_name": owner_names.get(record.get("opportunity_owner"), ""),
				"snoozed_until": _date(record.get(SNOOZE_UNTIL)),
				"snooze_reason": record.get(SNOOZE_REASON) or "",
			}
		)

	return {
		"stages": _stages(),
		"deals": deals,
		"thresholds": {"amber": amber, "red": red},
		"today": nowdate(),
		"currency": frappe.defaults.get_global_default("currency") or "USD",
		"truncated": truncated,
		"can_snooze": all(meta.has_field(fieldname) for fieldname in SNOOZE_FIELDS),
	}


def _snooze_values(doc):
	return {
		"snoozed_until": _date(doc.get(SNOOZE_UNTIL)),
		"snooze_reason": doc.get(SNOOZE_REASON) or "",
	}


@frappe.whitelist(methods=["POST"])
def snooze(name, until=None, reason=None, modified=None):
	"""Snooze ``name`` until ``until`` (a date after today), with an optional ``reason``; a blank
	``until`` wakes it (clears both fields).

	P6.8: a snoozed deal is hidden from the board until its date, then comes back marked as
	returning. Returns ``{"name", "modified", "snoozed_until", "snooze_reason"}``, or, when the deal
	changed since ``modified``, the form kit's conflict shape without saving
	(``{"conflict": True, "name", "modified", "modified_by", "server": {...}}``).

	Careful about:

	* **Its own permission check**: write on this deal (``doc.check_permission("write")``, which
	  includes user permissions, sharing and if-owner rules), and only on an open deal.
	* **The ``modified`` lock**, as ``form_kit.saver.save_step`` does it (``str`` against ``str``),
	  and a snooze without one is refused, because it could overwrite somebody silently.
	* **Only these two fields are written, with ``db_set``**, not ``save()``. A snooze is a focus
	  tool, and an unrelated validation (an old deal missing a field that became required since) must
	  not stop it; nor may it run the save hooks that stamp the stage clock or reset a probability.
	  ``db_set`` still stamps ``modified``, so the next save of the deal is checked against it. The
	  board is told through the same realtime event an Opportunity save sends, and the deal's
	  timeline gets an Info comment saying who snoozed it and until when.
	"""
	if not isinstance(name, str) or not name:
		frappe.throw(frappe._("Say which deal to snooze."))
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("write")
	meta = frappe.get_meta(DOCTYPE)
	if not all(meta.has_field(fieldname) for fieldname in SNOOZE_FIELDS):
		frappe.throw(frappe._("Snoozing is not set up on this site yet."))
	if doc.get("status") in CLOSED_STATUSES:
		frappe.throw(frappe._("{0} is closed, so there is nothing to snooze.").format(name))
	if not modified:
		frappe.throw(
			frappe._(
				"The snooze did not say which version of {0} it was made on. Reload and try again."
			).format(name)
		)
	if str(doc.modified) != str(modified):
		return {
			"conflict": True,
			"name": doc.name,
			"modified": str(doc.modified),
			"modified_by": doc.get("modified_by"),
			"server": _snooze_values(doc),
		}

	until_date = getdate(until) if until else None
	if until_date:
		days = date_diff(until_date, getdate(nowdate()))
		if days < 1:
			frappe.throw(frappe._("Pick a date after today."))
		if days > MAX_SNOOZE_DAYS:
			frappe.throw(frappe._("A snooze can run at most a year. Put a deal parked longer On Hold."))
	text = " ".join(str(reason or "").split())[:MAX_REASON] if until_date else ""

	doc.db_set({SNOOZE_UNTIL: until_date, SNOOZE_REASON: text or None})
	if until_date:
		note = frappe._("Snoozed on the Opportunity Board until {0}").format(formatdate(until_date))
		if text:
			note += ": " + escape_html(text)
	else:
		note = frappe._("Woken on the Opportunity Board")
	doc.add_comment("Info", note)
	frappe.publish_realtime("sales_pipeline_updated", {"opportunity": doc.name})
	# db_set stamps `modified` as now()'s text, which always carries microseconds; read back from the
	# table, a datetime with none prints without them. str(get_datetime(...)) is the form the next
	# lock check (save_step, or this one) compares against.
	return {"name": doc.name, "modified": str(get_datetime(doc.modified)), **_snooze_values(doc)}
