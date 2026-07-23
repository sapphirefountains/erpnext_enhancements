"""Read-only data endpoint for the embeddable Gantt widget.

Serves ``erpnext_enhancements.gantt.mount(...)`` (``public/js/gantt_widget/``).
The client sends a JSON ``config`` describing what to plot — source doctype, a
field map (gantt attribute -> fieldname), filters, an optional dependency
child table — and receives rows shaped for DHTMLX Gantt: ``tasks`` (bars,
optionally a tree via ``parent``) and ``links`` (finish-to-start arrows).

Security model — the config is client-supplied and therefore hostile input:
	- ``frappe.has_permission(doctype, "read")`` gates the whole call, and the
	  data query goes through ``frappe.get_list`` (NOT ``get_all``), so role
	  permissions, user permissions and ``permission_query_conditions`` apply
	  to every row returned.
	- every fieldname in the field map, the filters and ``order_by`` must exist
	  on ``frappe.get_meta(doctype)`` (or be a standard column such as
	  ``name``/``modified``); field-map targets must be value-bearing fields,
	  and ``start``/``end`` specifically must be Date/Datetime (a Time or Data
	  column would crash date coercion — a free exception primitive).
	  Anything unknown throws — nothing is interpolated unvalidated.
	- the dependency source must be a Table field on the doctype whose child
	  doctype carries exactly one Link back to the same doctype. Child rows are
	  read via ``frappe.get_list(..., parent_doctype=doctype)`` (parent-
	  permission checked) and links are kept only when BOTH ends are rows the
	  permission-checked main query returned, so a link can never disclose the
	  existence of a row the caller cannot read.
	- ``limit`` is clamped to ``MAX_ROWS``; child-table filters and 4-element
	  filter entries are rejected (out of scope for v1).

Date semantics: DHTMLX treats ``end_date`` as exclusive. ERPNext date values
at midnight are human-inclusive ("ends on the 5th" means through the 5th), so
a midnight end is pushed forward one day; rows missing a start fall back to a
one-day bar before their end; rows with neither date — or with values that
fail date coercion — are skipped and counted in ``meta.unscheduled`` (the
widget surfaces the count). Parent references are re-rooted against the tasks
actually emitted, so a skipped/filtered parent can never orphan (and thereby
hide) a rendered subtree.
"""

from datetime import timedelta

import frappe
from frappe import _
from frappe.model import default_fields, no_value_fields
from frappe.utils import cint, cstr, flt, get_datetime

MAX_ROWS = 1000
DEFAULT_ROWS = 500

# Gantt attribute -> is it required in the field map?
FIELD_MAP_ATTRS = {
	"text": True,
	"start": True,
	"end": False,
	"progress": False,
	"parent": False,
}

# Standard columns valid in filters/order_by but absent from meta.fields.
# ("doctype" is in default_fields but is not a real DB column — excluded.)
STANDARD_FIELDNAMES = set(default_fields) - {"doctype"}

# start/end must be genuinely date-valued: other value-bearing fieldtypes
# (Time -> timedelta, Data/Link -> arbitrary strings) crash or misparse in
# get_datetime/strftime, handing hostile callers an exception primitive.
DATE_ATTRS = ("start", "end")
DATE_FIELDTYPES = ("Date", "Datetime")
STANDARD_DATETIME_FIELDNAMES = ("creation", "modified")


def _validate_fieldname(meta, fieldname, must_hold_value=False):
	"""Return ``fieldname`` if it is a real column of ``meta``, else throw.

	``must_hold_value`` additionally rejects display-only fieldtypes (Section
	Break, HTML, Table, ...) — required for field-map targets, irrelevant for
	filter/order columns (standard columns always hold values).
	"""
	if not fieldname or not isinstance(fieldname, str):
		frappe.throw(_("Invalid fieldname in Gantt config"))
	if fieldname in STANDARD_FIELDNAMES:
		return fieldname
	df = meta.get_field(fieldname)
	if not df:
		frappe.throw(_("Unknown field {0} on {1} in Gantt config").format(fieldname, meta.name))
	if must_hold_value and df.fieldtype in no_value_fields:
		frappe.throw(_("Field {0} on {1} does not hold a value").format(fieldname, meta.name))
	return fieldname


def _parse_field_map(meta, fields):
	"""Validate the ``fields`` map (gantt attribute -> source fieldname)."""
	if not isinstance(fields, dict) or not fields:
		frappe.throw(_("Gantt config requires a 'fields' map"))

	unknown = set(fields) - set(FIELD_MAP_ATTRS)
	if unknown:
		frappe.throw(_("Unknown Gantt field attribute(s): {0}").format(", ".join(sorted(unknown))))

	field_map = {}
	for attr, required in FIELD_MAP_ATTRS.items():
		fieldname = fields.get(attr)
		if not fieldname:
			if required:
				frappe.throw(_("Gantt config 'fields' map requires {0}").format(attr))
			continue
		field_map[attr] = _validate_fieldname(meta, fieldname, must_hold_value=True)
		if attr in DATE_ATTRS:
			df = meta.get_field(fieldname)
			fieldtype_ok = (
				df.fieldtype in DATE_FIELDTYPES if df else fieldname in STANDARD_DATETIME_FIELDNAMES
			)
			if not fieldtype_ok:
				frappe.throw(
					_("Gantt {0} must map to a Date or Datetime field, not {1}").format(attr, fieldname)
				)
	return field_map


def _sanitize_filters(meta, filters):
	"""Accept dict filters or a list of 3-element entries; validate fieldnames.

	Operators and values are left to ``frappe.get_list`` (parameterized, with
	its own operator whitelist). 4-element entries (child-table / cross-doctype
	filters) are rejected — their fieldnames would belong to a doctype this
	function has not validated.
	"""
	if not filters:
		return {}

	if isinstance(filters, dict):
		for fieldname in filters:
			_validate_fieldname(meta, fieldname)
		return filters

	if isinstance(filters, list | tuple):
		for entry in filters:
			if not isinstance(entry, list | tuple) or len(entry) != 3:
				frappe.throw(_("Gantt filters must be a dict or a list of [field, operator, value]"))
			_validate_fieldname(meta, entry[0])
		return filters

	frappe.throw(_("Invalid Gantt filters"))


def _sanitize_order_by(meta, order_by):
	"""Reduce ``order_by`` to one validated fieldname plus asc/desc."""
	if not order_by:
		return "modified desc"
	parts = cstr(order_by).strip().split()
	if len(parts) > 2:
		frappe.throw(_("Invalid Gantt order_by"))
	fieldname = _validate_fieldname(meta, parts[0])
	direction = parts[1].lower() if len(parts) == 2 else "asc"
	if direction not in ("asc", "desc"):
		frappe.throw(_("Invalid Gantt order_by direction"))
	return f"{fieldname} {direction}"


def _resolve_dependency_source(meta, dep_fieldname):
	"""Resolve the dependency Table field -> (table field, child meta, link fieldname).

	The child doctype must carry exactly one Link field pointing back at
	``meta.name`` — that Link names the predecessor row (e.g. Task ->
	``depends_on`` -> Task Depends On.``task``).
	"""
	df = meta.get_field(cstr(dep_fieldname))
	if not df or df.fieldtype != "Table":
		frappe.throw(_("Gantt dependencies must name a Table field on {0}").format(meta.name))
	child_meta = frappe.get_meta(df.options)
	link_fields = [f.fieldname for f in child_meta.fields if f.fieldtype == "Link" and f.options == meta.name]
	if len(link_fields) != 1:
		frappe.throw(
			_("Cannot resolve a single {0} link on {1} for Gantt dependencies").format(
				meta.name, child_meta.name
			)
		)
	return df, child_meta, link_fields[0]


def _format_dt(value):
	return value.strftime("%Y-%m-%d %H:%M")


def _shape_tasks(rows, field_map):
	"""Map permission-checked rows into DHTMLX task dicts.

	Returns ``(tasks, unscheduled_count)``. See the module docstring for the
	date semantics (inclusive midnight ends, one-day fallbacks, undated rows
	skipped but counted). Rows whose date values fail coercion (garbage in a
	date column, driver types ``get_datetime`` cannot handle) are counted as
	unscheduled rather than crashing the request.
	"""
	tasks = []
	unscheduled = 0

	for row in rows:
		try:
			start = row.get(field_map["start"])
			end = row.get(field_map["end"]) if field_map.get("end") else None
			start = get_datetime(start) if start else None
			end = get_datetime(end) if end else None

			if end is not None and end.hour == end.minute == end.second == 0:
				# midnight = date-only value = inclusive end day
				end = end + timedelta(days=1)
			if start and end:
				if end <= start:
					end = start + timedelta(days=1)
			elif start:
				end = start + timedelta(days=1)
			elif end:
				start = end - timedelta(days=1)
			else:
				unscheduled += 1
				continue
			start_str, end_str = _format_dt(start), _format_dt(end)
		except Exception:
			unscheduled += 1
			continue

		task = {
			"id": row.name,
			"text": cstr(row.get(field_map["text"])) or row.name,
			"start_date": start_str,
			"end_date": end_str,
			"open": True,
		}
		if field_map.get("progress"):
			task["progress"] = round(min(max(flt(row.get(field_map["progress"])) / 100.0, 0), 1), 4)
		if field_map.get("parent"):
			task["parent"] = row.get(field_map["parent"]) or 0
		tasks.append(task)

	# Re-root parents against the tasks actually EMITTED — not the fetched
	# rows. A parent that was filtered out, beyond the row cap, or skipped
	# above as unscheduled would make DHTMLX silently drop the whole child
	# subtree (undated ERPNext group Tasks hit this constantly).
	if field_map.get("parent"):
		emitted = {t["id"] for t in tasks}
		for task in tasks:
			if task["parent"] not in emitted:
				task["parent"] = 0

	return tasks, unscheduled


def _fetch_links(meta, dep_fieldname, task_ids):
	"""Dependency arrows among ``task_ids`` (both ends must be in the set)."""
	if not task_ids:
		return []
	df, child_meta, link_field = _resolve_dependency_source(meta, dep_fieldname)
	rows = frappe.get_list(
		child_meta.name,
		filters={
			"parenttype": meta.name,
			"parentfield": df.fieldname,
			"parent": ["in", task_ids],
		},
		fields=["name", "parent", link_field],
		parent_doctype=meta.name,
		limit_page_length=0,
	)
	id_set = set(task_ids)
	links = []
	for row in rows:
		source = row.get(link_field)
		if source and source != row.parent and source in id_set:
			# "0" = finish-to-start: predecessor (child-row link) -> successor (parent doc)
			links.append({"id": row.name, "source": source, "target": row.parent, "type": "0"})
	return links


@frappe.whitelist()
def get_gantt_data(config):
	"""Return ``{tasks, links, meta}`` for one widget embed's ``config``.

	Args:
		config: JSON string or dict —
			``doctype`` (str, required): source doctype.
			``fields`` (dict, required): gantt attribute -> fieldname; see
				``FIELD_MAP_ATTRS`` (``text``/``start`` required).
			``filters`` (dict | list, optional): passed to ``frappe.get_list``
				after fieldname validation.
			``dependencies`` (str, optional): Table fieldname holding the
				dependency child rows.
			``order_by`` (str, optional): "fieldname asc|desc".
			``limit`` (int, optional): row cap, clamped to ``MAX_ROWS``.
	"""
	cfg = frappe.parse_json(config) or {}
	if not isinstance(cfg, dict):
		frappe.throw(_("Invalid Gantt config"))

	doctype = cfg.get("doctype")
	if not doctype or not isinstance(doctype, str):
		frappe.throw(_("Gantt config requires a doctype"))

	try:
		meta = frappe.get_meta(doctype)
	except frappe.DoesNotExistError:
		frappe.throw(_("Unknown doctype in Gantt config"))
	if meta.istable or meta.issingle:
		frappe.throw(_("Gantt source must be a regular doctype"))

	if not frappe.has_permission(doctype, "read"):
		frappe.throw(_("Not permitted to read {0}").format(doctype), frappe.PermissionError)

	field_map = _parse_field_map(meta, cfg.get("fields"))
	filters = _sanitize_filters(meta, cfg.get("filters"))
	order_by = _sanitize_order_by(meta, cfg.get("order_by"))
	limit = min(max(cint(cfg.get("limit")) or DEFAULT_ROWS, 1), MAX_ROWS)

	query_fields = ["name", *sorted({f for f in field_map.values() if f != "name"})]
	rows = frappe.get_list(
		doctype,
		filters=filters,
		fields=query_fields,
		order_by=order_by,
		limit_page_length=limit,
	)

	tasks, unscheduled = _shape_tasks(rows, field_map)

	links = []
	if cfg.get("dependencies"):
		links = _fetch_links(meta, cfg["dependencies"], [t["id"] for t in tasks])

	return {
		"tasks": tasks,
		"links": links,
		"meta": {"total_rows": len(rows), "unscheduled": unscheduled},
	}
