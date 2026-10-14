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

import html
import re

import frappe
from frappe import _
from frappe.utils import escape_html

from erpnext_enhancements.form_kit import field_help, saver
from erpnext_enhancements.form_kit import party as party_rules
from erpnext_enhancements.form_kit import record_info as record_info_rules
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
	open_rule = (config.get("mine") or {}).get("open")
	if open_rule:
		# The statuses that close a record (Closed Won, Lost, Closed, Converted on Opportunity): the side
		# panel offers no Undo for an edit that moved the record into or out of one (review of #1286:
		# a plain save back out of Closed Won skips revert_won_status's refusal once a project exists).
		out["closed"] = {"field": open_rule["field"], "in": list(open_rule["not_in"])}
	won = config.get("won")
	if won:
		# What a move into Closed Won must carry (form kit P6.6): the board's side panel asks for it.
		out["won"] = {
			"status": won["status"],
			"status_field": won.get("status_field", "status"),
			"fields": list(won["fields"]),
		}
	quick = config.get("quick")
	if quick and not name:
		# The board's quick add (P6.7): the fields it asks for, and what it fills itself.
		# A field quick add asks for only while the server says a new record needs it (the Lead
		# Source, while Lead Attribution Settings require one): decided here, never in the Desk.
		extra = [
			fieldname for fieldname, path in (quick.get("when") or {}).items() if _quick_needs(path, doctype)
		]
		out["quick"] = {
			"fields": list(quick["fields"]) + [f for f in extra if f not in quick["fields"]],
			"fixed": dict(quick.get("fixed") or {}),
			"owner_field": quick.get("owner_field"),
			"stage_field": quick.get("stage_field"),
		}
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


#: ``quick.when`` rules that have raised in this process: logged once each, not on every page open.
_QUICK_RULES_LOGGED = set()


def _quick_needs(path, doctype):
	"""Whether quick add must ask for a field, by the config's ``quick.when`` rule at ``path``.

	It runs on every new-record ``get_flow``, so a rule that raises (a Settings field not migrated
	yet, a bad path) must not take the guided page's step 1 down with it. The field is then
	**offered**: read as "not required", a rule like Opportunity's Lead Source would hide the one
	field ``attribution.enforce_source`` may still refuse the insert without, and nothing could ever
	be created (review of #1291). The failure is logged once per process, not on every page open.
	"""
	try:
		return bool(frappe.get_attr(path)(doctype))
	except Exception:
		if path not in _QUICK_RULES_LOGGED:
			_QUICK_RULES_LOGGED.add(path)
			frappe.log_error(
				title=f"Guided entry: quick add rule {path} failed", message=frappe.get_traceback()
			)
		return True


@frappe.whitelist(methods=["GET"])
def party_details(doctype, party_type, party, contacts=None, addresses=None, name=None):
	"""The Party entry component's answer when the account changes (form kit P3.2).

	``{"defaults": {"contact", "billing", "site"}, "linked": {"Contact": [...], "Address": [...]}}``:
	the account's own contact and addresses as ERPNext's form would suggest them, and which of the
	names in ``contacts`` / ``addresses`` (JSON lists, the record's current values) are linked to it
	or to the record ``name``. Read-only, GET only. See ``form_kit/party.py`` for the rules and the
	permission checks.
	"""
	_require_configured(doctype)
	frappe.has_permission(doctype, "read", throw=True)
	return party_rules.details(doctype, party_type, party, contacts, addresses, name)


@frappe.whitelist(methods=["POST"])
def create_record(doctype, values, prefill=None, client_id=None):
	"""Insert a new draft from step 1's values (JSON). Returns the save result.

	``prefill`` (JSON, optional) is what the record was started with elsewhere; see
	``saver.create_record`` for what is taken from it. ``client_id`` (optional) is the page's id
	for this create: sent again, it returns the record it made rather than a second one.
	"""
	_require_configured(doctype)
	return saver.create_record(doctype, values, prefill, client_id)


@frappe.whitelist()
def find_created(doctype, client_id):
	"""``{"name"}`` of the record the caller's create with ``client_id`` made, or ``{}``.

	The guided page asks when it puts back a create it kept on the phone (P4.7): the first attempt
	may have arrived with its answer lost, and the record should open rather than be typed again.
	"""
	_require_configured(doctype)
	frappe.has_permission(doctype, "read", throw=True)
	doc = saver.already_created(doctype, client_id)
	return {"name": doc.name} if doc is not None else {}


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


#: The notes block lists at most this many, newest first; the Desk form's Notes tab has the rest.
NOTES_LIMIT = 200


def _note_text(value):
	"""A CRM Note's HTML (a Text Editor) as plain text with its line breaks, for ``.text()``."""
	text = re.sub(r"(?i)<br\s*/?>|</p\s*>|</div\s*>|</li\s*>", "\n", value or "")
	text = html.unescape(frappe.utils.strip_html_tags(text)).replace("\xa0", " ")
	text = re.sub(r"[ \t]+\n", "\n", text)
	return re.sub(r"\n{3,}", "\n\n", text).strip()


@frappe.whitelist(methods=["GET"])
def record_notes(doctype, name):
	"""A record's CRM Notes (ERPNext's Notes tab), for the guided page and the side panel.

	Nik, 2026-10-14: "I can't see comments which are extremely important." The guided page replaced
	the Desk form, and an Opportunity's history is mostly here (1,049 notes on 235 deals on prod).

	``{"has_notes", "notes": [{"name", "text", "added_by", "added_on"}], "total", "can_add"}``,
	newest first. ``has_notes`` is false for a DocType without a ``notes`` table of CRM Note. ``text``
	is plain text (the note's HTML with its tags taken out), so the page writes it with ``.text()``.
	``can_add`` is write permission on the record and the DocType's own ``add_note``, which the page
	calls through ``run_doc_method`` (ERPNext's CRMNote mixin; it appends a row and saves).
	"""
	_require_configured(doctype)
	if not isinstance(name, str) or not name:
		frappe.throw(_("Which record?"), title=_("Not Found"))
	field = frappe.get_meta(doctype).get_field("notes")
	if not field or field.fieldtype != "Table" or field.options != "CRM Note":
		return {"has_notes": False, "notes": [], "total": 0, "can_add": False}
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	rows = sorted(
		doc.get("notes") or [],
		key=lambda row: str(row.get("added_on") or row.get("creation") or ""),
		reverse=True,
	)
	shown = rows[:NOTES_LIMIT]
	people = {}
	for row in shown:
		user = row.get("added_by")
		if user and user not in people:
			people[user] = frappe.utils.get_fullname(user) or user
	return {
		"has_notes": True,
		"notes": [
			{
				"name": row.get("name"),
				"text": _note_text(row.get("note")),
				"added_by": people.get(row.get("added_by"), row.get("added_by") or ""),
				"added_on": str(row.get("added_on") or ""),
			}
			for row in shown
		],
		"total": len(rows),
		"can_add": bool(doc.has_permission("write")) and callable(getattr(doc, "add_note", None)),
	}


@frappe.whitelist(methods=["GET"])
def record_info(doctype, name):
	"""What the Desk form's sidebar and timeline show for a record, for the guided page's record
	tools (attachments, activity, assigned to, tags, follow, shared): v1.686.0.

	Nik, 2026-10-14: "We shouldn't have lost any fidelity or information on any of the UI/UX
	changes." The form reads all of this through ``frappe.desk.form.load.get_docinfo``, which
	answers in ``frappe.response["docinfo"]``; sf_kit.call reads only ``message``. So this runs that
	same function (read permission on the record, as the form's own load checks it) and returns what
	it found with every stored HTML string as plain text. Shapes: ``form_kit/record_info.py``.
	"""
	_require_configured(doctype)
	if not isinstance(name, str) or not name:
		frappe.throw(_("Which record?"), title=_("Not Found"))
	return record_info_rules.build(doctype, name)
