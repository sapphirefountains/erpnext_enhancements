"""Saving a guided flow's steps onto the real record.

Two writes, both onto the document itself, never a parallel draft store:

* :func:`create_record` inserts a new draft from step 1's fields. From then on the record
  exists, so every later step is an ordinary save and nothing typed is lost if the user stops.
* :func:`save_step` assigns one step's values and saves.

What both are careful about:

* **Only what the flow offers this user is assigned.** The steps are recomputed here from the
  meta, the config and the user's permissions (:func:`form_kit.steps.flow_steps`); the
  client's idea of what is editable is never asked. Each allowed field is set one by one, never
  ``doc.update(values)``, so a crafted key (``owner``, ``docstatus``, a read-only stamp, a
  field at a permlevel the user cannot write) is ignored, not refused, exactly like a field the
  page did not send.
* **Child rows are matched by ``name``.** A row whose ``name`` is on the document is updated in
  place (only the child fields the user may write); a row without one, or with a name that is
  not on this document, is appended as new (the name is dropped, so a row can never be pulled
  in from another record); a stored row the client did not send is removed. That last rule
  applies only to tables present in ``values``: a table the client did not send is untouched.
* **Optimistic locking on ``modified``.** A save carries the ``modified`` the page loaded. If
  the record has changed since, nothing is written and the call *returns* (does not raise)::

      {"conflict": True, "name": ..., "modified": <the record's current modified>,
       "modified_by": ..., "server": {fieldname: current value, ...}}

  ``server`` holds the step's fields as they now stand (the fields sent, when no step is
  named), serialised like ``values`` below, so the page can show both versions side by side
  and let the user choose. The offline queue (P4.7) replays a kept save against this shape:
  on a conflict it keeps the local copy and asks, rather than overwriting. A conflict is an
  expected outcome, not an error, which is why it is a return value.
* **The normal save runs.** ``doc.insert()`` / ``doc.save()``: validation, the ``"*"`` value
  normalization (phones, emails, websites), and the DocType's own hooks (on Opportunity: ranks
  on Closed Won, the lost reason, attribution, the hand-off stamps). Their errors are raised as
  they are everywhere else in the Desk (``frappe.ValidationError`` and friends: HTTP 417 with
  the message in ``_server_messages``, which ``frappe.call`` shows), the same as the Contract
  Builder's ``save_builder`` and the Visit Wizard's ``save_visit``; nothing is half-saved. A
  successful write returns::

      {"name", "modified", "values": {fieldname: value as saved}, "completeness": {...}}

  ``values`` are read back from the document *after* the save, so they are the normalized
  values the hooks stored, and the page should replace what it shows with them.
"""

import datetime
import decimal
import json

import frappe
from frappe import _

from erpnext_enhancements.form_kit import steps as flow
from erpnext_enhancements.form_kit.completeness import stage_completeness


def _plain(value):
	if isinstance(value, datetime.datetime | datetime.date | datetime.time | datetime.timedelta):
		return str(value)
	if isinstance(value, decimal.Decimal):
		return float(value)
	return value


def serialize(doc, fields):
	"""``{fieldname: value}`` for ``fields`` (``{fieldname: field def}``), JSON-safe.

	A table becomes its rows as ``{"name", "idx", <each readable child field>}``; child fields
	the user may not read were never in the field def, so they are never sent.
	"""
	out = {}
	for fieldname, field in fields.items():
		value = doc.get(fieldname)
		if field["fieldtype"] in flow.TABLE_TYPES:
			columns = [child["fieldname"] for child in field.get("child_fields") or []]
			out[fieldname] = [
				{"name": row.get("name"), "idx": row.get("idx"), **{c: _plain(row.get(c)) for c in columns}}
				for row in value or []
			]
		else:
			out[fieldname] = _plain(value)
	return out


def _parse(values):
	if isinstance(values, str):
		try:
			values = json.loads(values or "{}")
		except ValueError:
			values = None
	if not isinstance(values, dict):
		frappe.throw(_("Could not read the values to save."))
	return values


def _merge_rows(doc, table, columns, rows):
	"""Rebuild ``table`` from ``rows``: update by name, append new, remove the rest."""
	existing = {row.get("name"): row for row in doc.get(table) or [] if row.get("name")}
	ordered = []
	for incoming in rows if isinstance(rows, list) else []:
		if not isinstance(incoming, dict):
			continue
		changes = {column: incoming[column] for column in columns if column in incoming}
		row = existing.pop(incoming.get("name"), None) if incoming.get("name") else None
		if row is not None:
			for column, value in changes.items():
				row.set(column, value)
			ordered.append(row)
		else:
			ordered.append(changes)
	doc.set(table, ordered)
	for index, row in enumerate(doc.get(table) or [], 1):
		row.idx = index


def assign(doc, allowed, values):
	"""Set each allowed field present in ``values`` on ``doc``; returns the fieldnames set."""
	assigned = []
	for fieldname, columns in allowed.items():
		if fieldname not in values:
			continue
		if columns is None:
			doc.set(fieldname, values[fieldname])
		else:
			_merge_rows(doc, fieldname, columns, values[fieldname])
		assigned.append(fieldname)
	return assigned


def _result(doctype, doc, fields):
	return {
		"name": doc.name,
		"modified": str(doc.modified),
		"values": serialize(doc, fields),
		"completeness": stage_completeness(doctype, doc),
	}


def create_record(doctype, values, prefill=None):
	"""Insert a new draft of ``doctype`` from step 1's fields. Returns the save result shape.

	``frappe.new_doc`` first, so the record gets the same defaults the Desk's New form does
	(status, dates, the default company, the first naming series); then only the step-1 fields
	this user may write are assigned, and ``insert()`` runs every validation and hook.

	``prefill`` is what the record was started with somewhere else: ``frappe.new_doc``'s options
	(the Customer form's *Create > Opportunity*), or a mapped document. Only
	:func:`form_kit.steps.prefill_fields` are taken from it, and never over a step-1 value, so
	it can carry a hidden contact or a billing address but cannot set what the flow would not.
	"""
	values = _parse(values)
	prefill = _parse(prefill) if prefill not in (None, "", "null") else {}
	frappe.has_permission(doctype, "create", throw=True)
	doc = frappe.new_doc(doctype)
	steps = flow.flow_steps(doctype, doc)
	allowed = flow.editable_fields(steps, create_only=True)
	assign(doc, allowed, values)
	if prefill:
		# Step 1 is what the person saw and sent, so a step-1 field is never taken from prefill.
		extra = {
			fieldname: columns
			for fieldname, columns in flow.prefill_fields(doctype, steps).items()
			if fieldname not in allowed
		}
		assign(doc, extra, prefill)
	doc.insert()
	create_step = next(step for step in steps if step["create"])
	return _result(doctype, doc, flow.value_fields(steps, create_step["key"]))


def save_step(doctype, name, values, modified, step=None):
	"""Assign one step's values to the record and save it. See the module docstring.

	``step`` (a step key) limits the save to that step's fields and is what the conflict's
	``server`` reports; without it, any field the flow offers may be saved and ``server`` reports
	the fields sent.
	"""
	values = _parse(values)
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(_("{0} {1} is no longer a draft, so it cannot be edited here.").format(_(doctype), name))
	if not modified:
		frappe.throw(
			_("The save did not say which version of {0} it was made on. Reload and try again.").format(name)
		)

	steps = flow.flow_steps(doctype, doc)
	shown = flow.value_fields(steps, step) if step else flow.value_fields(steps)
	if str(doc.modified) != str(modified):
		reported = shown if step else {key: field for key, field in shown.items() if key in values}
		return {
			"conflict": True,
			"name": doc.name,
			"modified": str(doc.modified),
			"modified_by": doc.get("modified_by"),
			"server": serialize(doc, reported),
		}

	allowed = flow.editable_fields(steps, step)
	assigned = assign(doc, allowed, values)
	if assigned:
		# Only when something was assigned: an empty save would still stamp a new ``modified``
		# and turn every other open copy of the record into a conflict.
		doc.save()
	saved = shown if step else {key: field for key, field in shown.items() if key in assigned}
	return _result(doctype, doc, saved)
