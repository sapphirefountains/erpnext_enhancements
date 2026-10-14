"""The guided flow's endpoints: read a flow, create a record from step 1, save a step, and
measure many records' completeness for the board.

This is an allowlist, not a generic document editor. Only DocTypes with a config in
``form_kit.config.CONFIGS`` are served; anything else is refused before a document is loaded.
Every endpoint checks the caller's permission on the DocType or the record itself (these are
reachable over HTTP by any signed-in user), and writes are POST only. No ``allow_guest``.
There is no rate limit on ``save_step`` or ``create_record``, as on the Contract Builder's
``save_builder`` and the Visit Wizard's ``save_visit``: a page that saves on every step would
trip one, and each save is an ordinary permission-checked document save.

Payload shapes are documented in ``form_kit/README.md``.
"""

import frappe
from frappe import _
from frappe.utils import escape_html

from erpnext_enhancements.form_kit import field_help, saver
from erpnext_enhancements.form_kit import steps as flow
from erpnext_enhancements.form_kit.completeness import completeness_for as _completeness_for
from erpnext_enhancements.form_kit.completeness import stage_completeness
from erpnext_enhancements.form_kit.config import CONFIGS


def _require_configured(doctype):
	if not isinstance(doctype, str) or doctype not in CONFIGS:
		frappe.throw(
			_("The guided flow is not available for {0}.").format(escape_html(str(doctype))),
			frappe.PermissionError,
			title=_("Not Available"),
		)


@frappe.whitelist()
def get_flow(doctype, name=None):
	"""Everything the guided flow page needs for one record, or for a new one.

	Returns ``{"doctype", "name", "modified", "steps", "values", "completeness", "can_write",
	"help_article"}``; ``help_article`` is the Knowledge Article each field's *Learn more* opens at
	its step's anchor (``form_kit.field_help``), or None.
	For a new record (no ``name``) ``name`` and ``modified`` are None, ``values`` are the new
	record's defaults, and step 1 (``"create": 1``) is what ``create_record`` takes; it also
	carries ``prefill_fields``, the fields ``create_record``'s ``prefill`` may set
	(``form_kit.steps.prefill_fields``), so the page can tell what a start elsewhere carries over.
	"""
	_require_configured(doctype)
	if name:
		doc = frappe.get_doc(doctype, name)
		doc.check_permission("read")
	else:
		frappe.has_permission(doctype, "create", throw=True)
		doc = frappe.new_doc(doctype)
	steps = flow.flow_steps(doctype, doc)
	can_write = any(
		not field.get("read_only")
		for step in steps
		for field in step["fields"]
		if field["fieldtype"] not in flow.LAYOUT_TYPES
	)
	out = {
		"doctype": doctype,
		"name": doc.name if name else None,
		"modified": str(doc.modified) if name else None,
		"steps": steps,
		"values": saver.serialize(doc, flow.value_fields(steps)),
		"completeness": stage_completeness(doctype, doc),
		"can_write": 1 if can_write else 0,
		"help_article": field_help.load(doctype)["article"],
	}
	if not name:
		out["prefill_fields"] = sorted(flow.prefill_fields(doctype, steps))
	config = CONFIGS[doctype]
	out["distinct"] = [
		{"fields": rule["fields"], "message": _(rule["message"])} for rule in config.get("distinct") or []
	]
	out["clears"] = config.get("clears") or {}
	lost = config.get("lost")
	if lost:
		# What the page's Set as Lost dialog needs: the value that opens it and the child tables.
		out["lost"] = {
			"status": lost["status"],
			"status_field": lost.get("status_field", "status"),
			"reasons": lost["reasons"]["doctype"],
			"competitors": lost["competitors"]["doctype"],
		}
	return out


@frappe.whitelist(methods=["POST"])
def create_record(doctype, values, prefill=None):
	"""Insert a new draft from step 1's values (JSON). Returns the save result.

	``prefill`` (JSON, optional) is what the record was started with elsewhere; see
	``saver.create_record`` for what is taken from it.
	"""
	_require_configured(doctype)
	return saver.create_record(doctype, values, prefill)


@frappe.whitelist(methods=["POST"])
def declare_lost(doctype, name, reasons, competitors=None, detail=None, modified=None):
	"""Declare ``name`` lost, as the Desk form's *Set as Lost* dialog does (the config's ``lost``).

	``reasons`` and ``competitors`` are JSON lists of names; ``detail`` is the detailed reason.
	Returns the save result, or the conflict shape without saving when the record has changed
	since ``modified``. See ``saver.declare_lost``.
	"""
	_require_configured(doctype)
	return saver.declare_lost(doctype, name, reasons, competitors, detail, modified)


@frappe.whitelist(methods=["POST"])
def save_step(doctype, name, values, modified=None, step=None):
	"""Save one step's values (JSON) onto ``name``, if it is still at ``modified``.

	Returns the save result, or the conflict shape without saving when the record has changed.
	"""
	_require_configured(doctype)
	return saver.save_step(doctype, name, values, modified, step=step)


@frappe.whitelist()
def get_field_help(doctype):
	"""The (i) help for the standard Desk form
	(``public/js/global_enhancements/field_description_icons.js``).

	``{"article", "fields": {fieldname: {"help", "anchor"}}}``: only fields with an explanation,
	only fields this user may read (the flow's own permission walk decides), and each field's
	``anchor`` is the key of the step it sits in, the section of the article it links to.
	"""
	_require_configured(doctype)
	frappe.has_permission(doctype, "read", throw=True)
	data = field_help.load(doctype)
	anchors = {
		field["fieldname"]: step["key"]
		for step in flow.flow_steps(doctype, None)
		for field in step["fields"]
		if field["fieldtype"] not in flow.LAYOUT_TYPES
	}
	return {
		"article": data["article"],
		"fields": {
			fieldname: {"help": entry["help"], "anchor": anchors[fieldname]}
			for fieldname, entry in data["fields"].items()
			if entry.get("help") and fieldname in anchors
		},
	}


@frappe.whitelist()
def completeness_for(doctype, names):
	"""``{name: completeness}`` for up to 500 records (``names`` is a JSON list) the caller can read."""
	_require_configured(doctype)
	return _completeness_for(doctype, names)


#: The start screen lists at most this many records; the Desk list has the rest.
MINE_LIMIT = 50


@frappe.whitelist()
def my_records(doctype):
	"""The caller's open records of ``doctype``, for the guided page's start screen.

	``{"label", "rows": [{"name", "title", "details": [...], "modified"}], "limit"}``, newest change
	first, through ``frappe.get_list`` so the caller's own permissions decide. The config's ``mine``
	says who "the caller's" is, which records are open, and what each row shows; a DocType
	without one lists nothing.
	"""
	_require_configured(doctype)
	frappe.has_permission(doctype, "read", throw=True)
	mine = CONFIGS[doctype].get("mine")
	if not mine:
		return {"label": "", "rows": [], "limit": MINE_LIMIT}
	meta = frappe.get_meta(doctype)
	filters = {mine["owner_field"]: frappe.session.user}
	open_rule = mine.get("open")
	if open_rule:
		filters[open_rule["field"]] = ["not in", open_rule["not_in"]]
	hide_flag = mine.get("hide_flag")
	if hide_flag and meta.has_field(hide_flag):
		filters[hide_flag] = 0
	shown = [mine["title_field"], *(mine.get("detail_fields") or [])]
	shown = [fieldname for fieldname in shown if meta.has_field(fieldname)]
	records = frappe.get_list(
		doctype,
		filters=filters,
		fields=["name", "modified", *shown],
		order_by="modified desc",
		limit_page_length=MINE_LIMIT,
	)
	details = [fieldname for fieldname in shown if fieldname != mine["title_field"]]
	return {
		"label": _(mine["label"]),
		"rows": [
			{
				"name": record.get("name"),
				"title": record.get(mine["title_field"]) or record.get("name"),
				"details": [str(record.get(fieldname)) for fieldname in details if record.get(fieldname)],
				"modified": str(record.get("modified")),
			}
			for record in records
		],
		"limit": MINE_LIMIT,
	}
