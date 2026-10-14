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
	record's defaults, and step 1 (``"create": 1``) is what ``create_record`` takes.
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
	return {
		"doctype": doctype,
		"name": doc.name if name else None,
		"modified": str(doc.modified) if name else None,
		"steps": steps,
		"values": saver.serialize(doc, flow.value_fields(steps)),
		"completeness": stage_completeness(doctype, doc),
		"can_write": 1 if can_write else 0,
		"help_article": field_help.load(doctype)["article"],
	}


@frappe.whitelist(methods=["POST"])
def create_record(doctype, values):
	"""Insert a new draft from step 1's values (JSON). Returns the save result."""
	_require_configured(doctype)
	return saver.create_record(doctype, values)


@frappe.whitelist(methods=["POST"])
def save_step(doctype, name, values, modified=None, step=None):
	"""Save one step's values (JSON) onto ``name``, if it is still at ``modified``.

	Returns the save result, or the conflict shape without saving when the record has changed.
	"""
	_require_configured(doctype)
	return saver.save_step(doctype, name, values, modified, step=step)


@frappe.whitelist()
def get_field_help(doctype):
	"""The (i) help for the standard Desk form (``public/js/form_kit/field_help.js``).

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
