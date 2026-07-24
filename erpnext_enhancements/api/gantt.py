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
	- composite mode (``group_by`` roots under synthetic parents and/or
	  ``children`` nesting a second doctype under each root) applies the SAME
	  rules per doctype: the child doctype gets its own ``has_permission``
	  gate and ``get_list`` query, every child fieldname is validated against
	  ITS meta, the ``link_field`` must be a Link back at the root, and child
	  rows are constrained to roots the permission-checked root query
	  returned (children of missing roots are dropped and counted).
	- ``limit`` is clamped to ``MAX_ROWS`` (children to ``MAX_CHILD_ROWS``);
	  child-table filters and 4-element filter entries are rejected (out of
	  scope for v1).

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
MAX_CHILD_ROWS = 5000

# Composite responses (``children`` / ``group_by``) prefix every id so the
# root, child and group namespaces can never collide (e.g. a Project and a
# Task that share a name). Single-source responses keep plain ``name`` ids.
GROUP_ID_PREFIX = "G::"
ROOT_ID_PREFIX = "P::"
CHILD_ID_PREFIX = "C::"

# Raw column values may be passed through per row via ``extra_fields`` (for
# client-side colouring/labels). Cap them, and never let one shadow a key the
# shaper owns — "$has_child" is DHTMLX's branch_loading_property.
MAX_EXTRA_FIELDS = 12
RESERVED_TASK_KEYS = frozenset(
	{
		"id",
		"text",
		"start_date",
		"end_date",
		"progress",
		"parent",
		"open",
		"type",
		"ref_doctype",
		"ref_name",
		"$has_child",
	}
)

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


def _parse_children_config(meta, children):
	"""Validate the composite ``children`` block (nested child rows per root).

	The child doctype gets the same treatment as the root: its own
	``frappe.has_permission`` read gate, and every fieldname (field map,
	filters, order_by, the ``link_field``) validated against ITS meta.
	``link_field`` must be a Link on the child doctype pointing back at the
	root doctype — it anchors each child under its root row.
	"""
	if not isinstance(children, dict):
		frappe.throw(_("Gantt 'children' must be a config object"))

	child_doctype = children.get("doctype")
	if not child_doctype or not isinstance(child_doctype, str):
		frappe.throw(_("Gantt 'children' requires a doctype"))
	try:
		child_meta = frappe.get_meta(child_doctype)
	except frappe.DoesNotExistError:
		frappe.throw(_("Unknown child doctype in Gantt config"))
	if child_meta.istable or child_meta.issingle:
		frappe.throw(_("Gantt child source must be a regular doctype"))
	if not frappe.has_permission(child_doctype, "read"):
		frappe.throw(_("Not permitted to read {0}").format(child_doctype), frappe.PermissionError)

	link_field = cstr(children.get("link_field"))
	df = child_meta.get_field(link_field)
	if not df or df.fieldtype != "Link" or df.options != meta.name:
		frappe.throw(
			_("Gantt children.link_field must be a Link field on {0} pointing at {1}").format(
				child_doctype, meta.name
			)
		)

	return {
		"meta": child_meta,
		"link_field": link_field,
		"field_map": _parse_field_map(child_meta, children.get("fields")),
		"filters": _sanitize_filters(child_meta, children.get("filters")),
		"order_by": _sanitize_order_by(child_meta, children.get("order_by")),
		"limit": min(max(cint(children.get("limit")) or MAX_CHILD_ROWS, 1), MAX_CHILD_ROWS),
		"dependencies": children.get("dependencies") or None,
		"extra_fields": _parse_extra_fields(child_meta, children.get("extra_fields")),
		# lazy: emit no child rows, only a per-root "has children" marker, so
		# the client can render a collapsed caret and fetch that one root's
		# children when the user expands it
		"lazy": bool(children.get("lazy")),
	}


def _parse_extra_fields(meta, extra_fields):
	"""Validate ``extra_fields`` — raw column values passed through per row.

	They let a client colour/label/sort rows by source data (project type,
	status, …) without a second round trip. Same validation as everything
	else, plus a cap and a guard against overwriting the DHTMLX keys the
	shaper owns.
	"""
	if not extra_fields:
		return []
	if not isinstance(extra_fields, list | tuple):
		frappe.throw(_("Gantt extra_fields must be a list of fieldnames"))
	if len(extra_fields) > MAX_EXTRA_FIELDS:
		frappe.throw(_("Too many Gantt extra_fields (max {0})").format(MAX_EXTRA_FIELDS))
	out = []
	for fieldname in extra_fields:
		validated = _validate_fieldname(meta, fieldname, must_hold_value=True)
		if validated in RESERVED_TASK_KEYS:
			frappe.throw(_("Gantt extra_fields may not use the reserved name {0}").format(validated))
		if validated not in out:
			out.append(validated)
	return out


def _with_in_filter(filters, fieldname, values):
	"""Return ``filters`` plus an ``["in", values]`` constraint on ``fieldname``.

	The constraint must win: a dict filter the caller supplied on the same
	fieldname is replaced; list filters get one more AND entry.
	"""
	if isinstance(filters, list | tuple):
		return [*filters, [fieldname, "in", values]]
	merged = dict(filters or {})
	merged[fieldname] = ["in", values]
	return merged


def _add_extra_fields(task, row, extra_fields):
	"""Copy validated raw column values onto a shaped task dict."""
	for fieldname in extra_fields or ():
		task[fieldname] = row.get(fieldname)


def _format_dt(value):
	return value.strftime("%Y-%m-%d %H:%M")


def _shape_progress(row, field_map):
	return round(min(max(flt(row.get(field_map["progress"])) / 100.0, 0), 1), 4)


def _shape_row(row, field_map):
	"""Shape ONE permission-checked row into a DHTMLX task dict, or ``None``.

	``None`` means the row has no usable dates (missing, or values that fail
	coercion — garbage in a date column, driver types ``get_datetime`` cannot
	handle) and should be counted rather than crash the request. See the
	module docstring for the date semantics. A mapped ``parent`` is carried
	as the RAW field value — callers resolve/re-root it against what they
	actually emit.
	"""
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
			return None
		start_str, end_str = _format_dt(start), _format_dt(end)
	except Exception:
		return None

	task = {
		"id": row.name,
		"text": cstr(row.get(field_map["text"])) or row.name,
		"start_date": start_str,
		"end_date": end_str,
		"open": True,
	}
	if field_map.get("progress"):
		task["progress"] = _shape_progress(row, field_map)
	if field_map.get("parent"):
		task["parent"] = row.get(field_map["parent"]) or 0
	return task


def _shape_tasks(rows, field_map):
	"""Map rows into DHTMLX task dicts. Returns ``(tasks, unscheduled_count)``."""
	tasks = []
	unscheduled = 0
	for row in rows:
		task = _shape_row(row, field_map)
		if task is None:
			unscheduled += 1
		else:
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


def _build_composite(doctype, meta, field_map, rows, cfg, group_field, extra_fields=None):
	"""Assemble the composite response: grouped roots + nested child rows.

	Shape (all ids prefixed — see the prefix constants):
		- ``group_by`` values become synthetic ``type: "project"`` rows (DHTMLX
		  derives their bar from descendants); roots parent onto them.
		- root rows keep their own dates; an UNDATED root that still anchors
		  children is emitted as a dateless ``type: "project"`` container
		  instead of being skipped (its subtree must stay visible).
		- child rows parent onto their own emitted parent row (when the child
		  field map has ``parent``) else onto their root. Children whose root
		  row was not returned (filtered out / beyond the row cap) are dropped
		  and counted in ``meta.dropped_children`` — an anchorless child would
		  vanish inside DHTMLX anyway, silently.
		- every row carries ``ref_doctype``/``ref_name`` so click handlers can
		  route to the real document.
	"""
	children_cfg = None
	child_rows = []
	child_counts = {}
	if cfg.get("children"):
		children_cfg = _parse_children_config(meta, cfg["children"])
		if rows:
			link_field = children_cfg["link_field"]
			child_filters = _with_in_filter(children_cfg["filters"], link_field, [r.name for r in rows])
			if children_cfg["lazy"]:
				# Only "does this root have children?" — one grouped, still
				# permission-checked count query instead of every child row.
				for count_row in frappe.get_list(
					children_cfg["meta"].name,
					filters=child_filters,
					fields=[link_field, "count(name) as ee_child_count"],
					group_by=link_field,
					limit_page_length=0,
				):
					if count_row.get(link_field):
						child_counts[count_row.get(link_field)] = cint(count_row.get("ee_child_count"))
			else:
				wanted = (
					{link_field} | set(children_cfg["field_map"].values()) | set(children_cfg["extra_fields"])
				)
				child_rows = frappe.get_list(
					children_cfg["meta"].name,
					filters=child_filters,
					fields=["name", *sorted(wanted - {"name"})],
					order_by=children_cfg["order_by"],
					limit_page_length=children_cfg["limit"],
				)

	unscheduled = 0

	# Children first, so an undated root knows whether it still anchors a subtree.
	shaped_children = []
	roots_with_children = set()
	if children_cfg:
		for row in child_rows:
			task = _shape_row(row, children_cfg["field_map"])
			if task is None:
				unscheduled += 1
				continue
			task["id"] = CHILD_ID_PREFIX + row.name
			task["ref_doctype"] = children_cfg["meta"].name
			task["ref_name"] = row.name
			_add_extra_fields(task, row, children_cfg["extra_fields"])
			shaped_children.append((row, task))
			roots_with_children.add(row.get(children_cfg["link_field"]))
		# a lazily-loaded root still anchors a subtree, so it must not be
		# dropped for being undated
		roots_with_children |= set(child_counts)

	# group_field may be several fieldnames: the first non-empty one wins, so a
	# caller can group by e.g. Master Project where set and fall back to a type
	group_fields = group_field if isinstance(group_field, list) else ([group_field] if group_field else [])
	group_link_targets = {}
	for fieldname in group_fields:
		gdf = meta.get_field(fieldname)
		group_link_targets[fieldname] = gdf.options if gdf and gdf.fieldtype == "Link" else None

	tasks = []
	group_rows = {}
	root_names = set()
	for row in rows:
		task = _shape_row(row, field_map)
		if task is None:
			if row.name not in roots_with_children:
				unscheduled += 1
				continue
			task = {
				"id": row.name,
				"text": cstr(row.get(field_map["text"])) or row.name,
				"type": "project",
				"open": True,
			}
			if field_map.get("progress"):
				task["progress"] = _shape_progress(row, field_map)
		task["id"] = ROOT_ID_PREFIX + row.name
		task["ref_doctype"] = doctype
		task["ref_name"] = row.name
		_add_extra_fields(task, row, extra_fields)
		if children_cfg and children_cfg["lazy"]:
			count = child_counts.get(row.name) or 0
			if count:
				# DHTMLX branch_loading_property: draws a collapsed caret for a
				# branch whose children are not in the datastore yet
				task["$has_child"] = True
				task["ee_child_count"] = count
				task["open"] = False
		if group_fields:
			value, source_field = "", None
			for fieldname in group_fields:
				value = cstr(row.get(fieldname))
				if value:
					source_field = fieldname
					break
			if value:
				gid = GROUP_ID_PREFIX + value
				if gid not in group_rows:
					group_rows[gid] = {
						"id": gid,
						"text": value,
						"type": "project",
						"open": True,
						"parent": 0,
					}
					if group_link_targets.get(source_field):
						group_rows[gid]["ref_doctype"] = group_link_targets[source_field]
						group_rows[gid]["ref_name"] = value
				task["parent"] = gid
			else:
				task["parent"] = 0
		root_names.add(row.name)
		tasks.append(task)
	# groups alphabetically (stable, matches the legacy portfolio chart) rather
	# than in first-appearance order, which would reshuffle with root order_by
	tasks = [*sorted(group_rows.values(), key=lambda group: group["text"]), *tasks]

	kept_children = []
	if children_cfg:
		link_field = children_cfg["link_field"]
		kept_children = [(row, task) for row, task in shaped_children if row.get(link_field) in root_names]
		kept_ids = {task["id"] for _, task in kept_children}
		child_has_parent = bool(children_cfg["field_map"].get("parent"))
		for row, task in kept_children:
			candidate = CHILD_ID_PREFIX + cstr(task.get("parent") or "") if child_has_parent else None
			task["parent"] = candidate if candidate in kept_ids else ROOT_ID_PREFIX + row.get(link_field)
			tasks.append(task)
	dropped_children = len(shaped_children) - len(kept_children)

	links = []
	if cfg.get("dependencies") and root_names:
		for link in _fetch_links(meta, cfg["dependencies"], sorted(root_names)):
			link["source"] = ROOT_ID_PREFIX + link["source"]
			link["target"] = ROOT_ID_PREFIX + link["target"]
			links.append(link)
	if children_cfg and children_cfg["dependencies"] and kept_children:
		kept_names = [row.name for row, _ in kept_children]
		for link in _fetch_links(children_cfg["meta"], children_cfg["dependencies"], kept_names):
			link["source"] = CHILD_ID_PREFIX + link["source"]
			link["target"] = CHILD_ID_PREFIX + link["target"]
			links.append(link)

	return {
		"tasks": tasks,
		"links": links,
		"meta": {
			"total_rows": len(rows),
			"unscheduled": unscheduled,
			"dropped_children": dropped_children,
		},
	}


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
			``group_by`` (str | list, optional): fieldname(s) whose values
				become synthetic parent rows (composite mode). A list
				coalesces — the first non-empty value wins, e.g.
				``["custom_master_project", "project_type"]``.
			``extra_fields`` (list, optional): validated fieldnames copied
				verbatim onto each row, for client-side colouring/labels.
			``children`` (dict, optional): nest another doctype's rows under
				each root (composite mode) — ``doctype``, ``link_field`` (a
				Link back at the root doctype), plus its own ``fields`` /
				``filters`` / ``dependencies`` / ``order_by`` / ``limit`` /
				``extra_fields``, and ``lazy``: with ``lazy`` no child rows
				are returned at all — each root instead carries ``$has_child``
				and ``ee_child_count`` from one grouped count query, so the
				client renders a collapsed caret and fetches that root's
				children only when the user expands it.

		Composite mode (``group_by``/``children`` present) prefixes every id
		(``G::``/``P::``/``C::``) and adds ``ref_doctype``/``ref_name`` per
		row; a root ``parent`` mapping is not supported there.
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

	group_field = None
	if cfg.get("group_by"):
		requested = cfg["group_by"]
		requested = requested if isinstance(requested, list | tuple) else [requested]
		group_field = [_validate_fieldname(meta, f, must_hold_value=True) for f in requested]
	extra_fields = _parse_extra_fields(meta, cfg.get("extra_fields"))
	composite = bool(group_field or cfg.get("children"))
	if composite and field_map.get("parent"):
		frappe.throw(_("Gantt composite configs do not support a root 'parent' mapping"))

	wanted = set(field_map.values()) | set(extra_fields)
	if group_field:
		wanted |= set(group_field)
	query_fields = ["name", *sorted(wanted - {"name"})]
	rows = frappe.get_list(
		doctype,
		filters=filters,
		fields=query_fields,
		order_by=order_by,
		limit_page_length=limit,
	)

	if composite:
		return _build_composite(doctype, meta, field_map, rows, cfg, group_field, extra_fields)

	tasks, unscheduled = _shape_tasks(rows, field_map)
	if extra_fields:
		by_name = {row.name: row for row in rows}
		for task in tasks:
			_add_extra_fields(task, by_name[task["id"]], extra_fields)

	links = []
	if cfg.get("dependencies"):
		links = _fetch_links(meta, cfg["dependencies"], [t["id"] for t in tasks])

	return {
		"tasks": tasks,
		"links": links,
		"meta": {"total_rows": len(rows), "unscheduled": unscheduled},
	}
