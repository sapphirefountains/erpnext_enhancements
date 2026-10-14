"""How complete a record is for the stage it is in.

Each configured DocType lists, per stage, what a record at that stage should have
(``config["stages"]``; Opportunity's lists were decided from prod fill rates, TASK-2026-02773).
:func:`stage_completeness` answers ``{"stage", "done", "total", "missing"}`` for one record and
is a pure function over anything with ``.get`` (a Document, a ``frappe._dict``, a plain dict),
so it is tested with no bench. :func:`completeness_for` answers it for many records at once,
for the pipeline board: one permission-checked list query for the parent fields and one bound
query per child table, never one per record.

A requirement is one of:

``{"field": f}``
    ``f`` has a value. A table needs at least one row whose ``row_field`` is filled
    (``{"field": t, "row_field": c}``), so a blank row added and left does not count.
    ``"positive": True`` asks a number for more than zero.
``{"any": [f1, f2]}``
    Any one of them has a value (a contact is ``primary_contact`` *or* ``contact_person``).
``{"chosen_rows": {"field", "row_field", "tables": {choice: [table, column]}}}``
    For a choice made in ``field`` (a value stream), its table has a filled row. Choices with
    no table (Products, Delivery) are not counted for or against.

Every requirement carries the plain-English ``label`` the page shows when it is missing.
A record whose stage has no list is measured against ``stages["default"]``.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt

from erpnext_enhancements.form_kit.config import get_config, requirement_fieldnames

#: The most records one batch call will measure.
MAX_BATCH = 500


def _filled(value):
	if value is None:
		return False
	if isinstance(value, str):
		return bool(value.strip())
	if isinstance(value, list | tuple):
		return bool(value)
	return True


def _filled_rows(rows, column):
	return [row for row in rows or [] if _filled(row.get(column))]


def requirement_met(requirement, doc):
	"""Whether ``doc`` meets one requirement."""
	if "any" in requirement:
		return any(_filled(doc.get(fieldname)) for fieldname in requirement["any"])
	if "chosen_rows" in requirement:
		chosen = requirement["chosen_rows"]
		picks = {row.get(chosen["row_field"]) for row in doc.get(chosen["field"]) or []}
		for choice, (table, column) in chosen["tables"].items():
			if choice in picks and _filled_rows(doc.get(table), column):
				return True
		return False
	value = doc.get(requirement["field"])
	if requirement.get("row_field"):
		return bool(_filled_rows(value, requirement["row_field"]))
	if requirement.get("positive"):
		return flt(value) > 0
	return _filled(value)


def _missing_fieldname(requirement, doc):
	"""The field the page should take the user to for a missing requirement."""
	if "chosen_rows" in requirement:
		chosen = requirement["chosen_rows"]
		picks = [row.get(chosen["row_field"]) for row in doc.get(chosen["field"]) or []]
		for pick in picks:
			if pick in chosen["tables"]:
				return chosen["tables"][pick][0]
		return chosen["field"]
	return requirement_fieldnames(requirement)[0]


def stage_completeness(doctype, doc, config=None):
	"""``{"stage", "done", "total", "missing": [{"fieldname", "label"}]}`` for ``doc``'s stage."""
	config = config or get_config(doctype)
	stages = config["stages"]
	stage = doc.get(stages["field"]) or stages.get("default")
	requirements = stages["lists"].get(stage)
	if requirements is None:
		requirements = stages["lists"].get(stages.get("default"), [])
	# A stage whose value is not words (Item's is_stock_item tick) is shown by its label.
	labels = stages.get("labels") or {}
	if stage in labels:
		stage = _(labels[stage])
	missing = []
	for requirement in requirements:
		if not requirement_met(requirement, doc):
			missing.append(
				{"fieldname": _missing_fieldname(requirement, doc), "label": _(requirement["label"])}
			)
	return {
		"stage": stage,
		"done": len(requirements) - len(missing),
		"total": len(requirements),
		"missing": missing,
	}


def needed_fields(config):
	"""``(scalar fieldnames, {table fieldname: row column})`` every stage list reads."""
	scalars, tables = {config["stages"]["field"]}, {}
	for requirements in config["stages"]["lists"].values():
		for requirement in requirements:
			if "chosen_rows" in requirement:
				chosen = requirement["chosen_rows"]
				tables[chosen["field"]] = chosen["row_field"]
				for table, column in chosen["tables"].values():
					tables[table] = column
			elif requirement.get("row_field"):
				tables[requirement["field"]] = requirement["row_field"]
			else:
				scalars.update(requirement_fieldnames(requirement))
	return sorted(scalars - set(tables)), tables


def completeness_for(doctype, names):
	"""``{name: stage_completeness}`` for the records in ``names`` the session user may read.

	The parent fields come from one ``frappe.get_list`` (so user permissions, sharing and
	permission query conditions apply, and an unreadable record is simply absent); each child
	table is one ``frappe.db.sql`` with bound parameters. The table and column names in that SQL
	come from the config and are checked against the meta first, never from the request.
	"""
	config = get_config(doctype)
	if not config:
		frappe.throw(_("The guided flow is not set up for {0}.").format(_(doctype)), frappe.PermissionError)
	frappe.has_permission(doctype, "read", throw=True)
	if isinstance(names, str):
		names = json.loads(names or "[]")
	if not isinstance(names, list):
		frappe.throw(_("names must be a list of record names."))
	names = list(dict.fromkeys(str(name) for name in names if isinstance(name, str | int) and str(name)))
	if len(names) > MAX_BATCH:
		frappe.throw(_("Ask for at most {0} records at a time.").format(MAX_BATCH))
	if not names:
		return {}

	meta = frappe.get_meta(doctype)
	scalars, tables = needed_fields(config)
	present = [fieldname for fieldname in scalars if meta.get_field(fieldname)]
	rows = frappe.get_list(
		doctype,
		filters={"name": ["in", names]},
		fields=["name", *present],
		limit_page_length=0,
	)
	docs = {row["name"]: frappe._dict(row) for row in rows}
	if not docs:
		return {}
	for table, column in tables.items():
		table_df = meta.get_field(table)
		if table_df is None:
			continue
		child = table_df.get("options")
		if not child or not frappe.get_meta(child).get_field(column):
			continue
		for doc in docs.values():
			doc[table] = []
		result = frappe.db.sql(
			f"""select `parent`, `{column}` as value from `tab{child}`
			where `parenttype` = %(doctype)s and `parentfield` = %(table)s and `parent` in %(names)s
			order by `idx`""",
			{"doctype": doctype, "table": table, "names": tuple(docs)},
			as_dict=True,
		)
		for row in result:
			if row["parent"] in docs:
				docs[row["parent"]][table].append({column: row["value"]})
	return {name: stage_completeness(doctype, doc, config) for name, doc in docs.items()}
