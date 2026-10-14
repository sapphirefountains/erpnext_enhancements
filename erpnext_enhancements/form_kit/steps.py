"""A DocType's form, walked as a guided flow of steps.

:func:`flow_steps` reads ``frappe.get_meta(doctype)`` the way the Desk lays the form out
(Tab Breaks hold sections, Section Breaks start them) and turns it into steps, using the
DocType's config (``form_kit/config``) to merge, order, rename and introduce sections and to
say which steps apply when. A field added to the DocType appears in the flow with no change
here: a section no configured step claims becomes a step of its own. So the flow can never
quietly lose a field the form shows.

Four things it is careful about:

* **Permissions are the Desk's, per field, computed here.** Frappe's form decides a field's
  display status from the permlevel row the user's roles reach
  (``frappe/public/js/frappe/model/perm.js``, ``get_field_display_status``: write at that level
  is "Write", read is "Read", neither is "None"). Level 0 is the document's own permission,
  sharing and if-owner rules included (``frappe.has_permission(doctype, "write", doc)``); a
  higher level is the role rows only (``Meta.get_permlevel_access``). So a field is offered
  editable only when the user can write the document *and* its permlevel; readable-only
  levels come back read-only; a level the user cannot read is not sent at all, which is what
  ``Document.apply_fieldlevel_read_permissions`` does to a form load. Nothing here trusts the
  client: :mod:`form_kit.saver` recomputes the same steps before it assigns anything.
* **Hidden is the form's hidden.** A field with ``hidden`` set, or inside a hidden Section or
  Tab Break, is not shown, exactly as ``frappe/form/section.js`` hides the section's wrapper;
  ``show_hidden`` in the config is the one, reasoned, exception. HTML fields with no static
  content are drawn by form JavaScript the flow cannot run; each one is listed in the config's
  ``skip_fields`` with its reason (the schema test fails otherwise).
* **Step 1 makes the record.** It carries the ``create`` fields: the config's list, plus any
  required field with no default the meta adds later (so a new required field can never make
  the insert impossible from the flow). They are flagged ``"create": 1``.
* **``applies`` is data, not code.** ``{"field", "row_field", "in"}`` is evaluated here for the
  saved record (``active``) and sent as-is, so the page re-evaluates it as the user picks a
  value stream without a round trip.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint

from erpnext_enhancements.form_kit import field_help
from erpnext_enhancements.form_kit.config import get_config, query_fieldnames

LAYOUT_TYPES = {"Section Break", "Column Break", "Tab Break"}
DISPLAY_TYPES = {"HTML", "Heading", "Image", "Button", "Fold"}
NO_VALUE_TYPES = LAYOUT_TYPES | DISPLAY_TYPES
TABLE_TYPES = {"Table", "Table MultiSelect"}

#: The fields before the first Section Break, when the form does not open with a Tab Break.
TOP = "_top"

#: The docfield keys that shape a control on the page. ``read_only`` is recomputed per user.
#: v1.685.0 added the ones v16's controls read from the docfield itself (Nik, 2026-10-14: "the
#: inputs should be the same"): ``link_filters`` (ControlLink.set_custom_query applies it with no
#: ``frm``; Opportunity From's Property Setter limits it to Lead, Customer and Prospect),
#: ``ignore_user_permissions`` (search_link's own flag, which the form reads from the meta the page
#: does not load; search_link honors it only with a reference DocType, so the editor gives each Link
#: the record's DocType, and each grid cell its child DocType, as the form does), ``length`` (a Data
#: input's maxlength, 140 when absent), ``sort_options``, ``placeholder``, ``max_height`` (Text
#: Editor) and ``hide_days`` / ``hide_seconds`` (Duration).
#: ``fetch_from`` is left out on purpose: v16 fills it on the server in ``_validate_links`` on every
#: save, ``save_step`` returns what the save changed, and a client fetch inside a FieldGroup would
#: queue a change to a field the person cannot edit.
FIELD_KEYS = (
	"fieldname",
	"fieldtype",
	"label",
	"options",
	"reqd",
	"mandatory_depends_on",
	"depends_on",
	"read_only_depends_on",
	"precision",
	"bold",
	"link_filters",
	"ignore_user_permissions",
	"length",
	"sort_options",
	"placeholder",
	"max_height",
	"hide_days",
	"hide_seconds",
)

#: Defaults evaluated at insert time; sending them as a value would be wrong.
DYNAMIC_DEFAULTS = {"__user", "today", "now"}


def is_value_field(df):
	return df.get("fieldtype") not in NO_VALUE_TYPES


def _is_new(doc):
	if doc is None:
		return True
	if hasattr(doc, "is_new"):
		return bool(doc.is_new())
	return not doc.get("name") or bool(doc.get("__islocal"))


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


def access_for(doctype, meta, doc=None, user=None):
	"""What ``user`` may read and write on ``doc`` (None for a record not yet created).

	``read_levels`` / ``write_levels`` are permlevels. Level 0 follows the document permission
	(``frappe.has_permission`` with the doc: user permissions, sharing, if-owner); a higher
	level follows the role rows (``meta.get_permlevel_access``), as on the Desk. Nothing is
	writable unless the document itself is: a save would fail its own ``check_permission``.
	A submitted or cancelled document is read-only throughout (allow-on-submit fields are not
	offered; no configured DocType is submittable yet).
	"""
	user = user or frappe.session.user
	is_new = _is_new(doc)
	if is_new:
		writable = bool(frappe.has_permission(doctype, "create", user=user))
		readable = writable or bool(frappe.has_permission(doctype, "read", user=user))
	else:
		readable = bool(frappe.has_permission(doctype, "read", doc=doc, user=user))
		writable = bool(frappe.has_permission(doctype, "write", doc=doc, user=user))
	if doc is not None and cint(doc.get("docstatus")) != 0:
		writable = False
	write_levels = {cint(level) for level in meta.get_permlevel_access("write", user=user) or []}
	read_levels = {cint(level) for level in meta.get_permlevel_access("read", user=user) or []} | write_levels
	if readable:
		read_levels.add(0)
	else:
		read_levels.discard(0)
	if writable:
		write_levels.add(0)
	else:
		write_levels = set()
	return {
		"user": user,
		"is_new": is_new,
		"readable": readable,
		"writable": writable,
		"read_levels": read_levels,
		"write_levels": write_levels,
	}


def field_def(df, access, *, read_only=False, read_only_names=(), help_entry=None):
	"""The docfield as the page renders it, or None when the user may not read its permlevel.

	``hint`` and ``help`` come from the DocType's help file (``form_kit.field_help``, P4.3); a
	field the file says nothing about falls back to its docfield description, with no (i).
	"""
	level = cint(df.get("permlevel"))
	if level not in access["read_levels"]:
		return None
	out = {key: df.get(key) for key in FIELD_KEYS if df.get(key) not in (None, "", 0)}
	entry = help_entry or {}
	out["hint"] = entry.get("hint") or df.get("description") or ""
	out["help"] = entry.get("help") or None
	default = df.get("default")
	if (
		default not in (None, "")
		and str(default).strip().lower() not in DYNAMIC_DEFAULTS
		and not str(default).startswith((":", "eval:"))
	):
		out["default"] = default
	writable = (
		level in access["write_levels"]
		and not read_only
		and not cint(df.get("read_only"))
		and not cint(df.get("is_virtual"))
		and df.get("fieldtype") != "Read Only"
		and df.get("fieldname") not in read_only_names
		and not (cint(df.get("set_only_once")) and not access["is_new"])
	)
	out["read_only"] = 0 if writable else 1
	if df.get("fieldtype") in TABLE_TYPES:
		out["child_fields"] = child_field_defs(df.get("options"), access, read_only=not writable)
	return out


def child_field_defs(child_doctype, access, *, read_only=False):
	"""A child table's columns: its visible value fields the user may read at their permlevel.

	Child fields answer to the parent's permission rows (``Meta.get_permissions(parenttype)``),
	so the parent's ``access`` decides them.
	"""
	out = []
	for df in frappe.get_meta(child_doctype).fields:
		if not is_value_field(df) or cint(df.get("hidden")):
			continue
		field = field_def(df, access, read_only=read_only)
		if field is None:
			continue
		field.pop("child_fields", None)
		field["in_list_view"] = cint(df.get("in_list_view"))
		out.append(field)
	return out


# ---------------------------------------------------------------------------
# Walking the meta
# ---------------------------------------------------------------------------


def sections_of(meta):
	"""The form's sections in order: ``{key, label, depends_on, hidden, tab, fields}``.

	A Tab Break opens a tab; a Section Break opens a section. Fields that open a tab before any
	Section Break form a section keyed by the tab's fieldname, and fields before the first break
	of all form :data:`TOP`. A section is hidden when it or its tab is.
	"""
	sections = []
	# The Desk calls a form's implicit first tab "Details" (frappe/form/layout.js).
	tab = {"key": None, "label": "Details", "hidden": False}
	current = None
	for df in meta.fields:
		fieldtype = df.get("fieldtype")
		if fieldtype == "Tab Break":
			tab = {
				"key": df.get("fieldname"),
				"label": df.get("label") or "",
				"hidden": bool(cint(df.get("hidden"))),
			}
			current = None
			continue
		if fieldtype == "Section Break":
			current = {
				"key": df.get("fieldname"),
				"label": df.get("label") or "",
				"depends_on": df.get("depends_on") or None,
				"hidden": bool(cint(df.get("hidden"))) or tab["hidden"],
				"tab": tab,
				"fields": [],
			}
			sections.append(current)
			continue
		if current is None:
			current = {
				"key": tab["key"] or TOP,
				"label": "",
				"depends_on": None,
				"hidden": tab["hidden"],
				"tab": tab,
				"fields": [],
			}
			sections.append(current)
		current["fields"].append(df)
	return sections


def _fills_itself(df):
	"""Whether a new record gets a value without the user: frappe.new_doc's static defaults."""
	if df.get("default") not in (None, ""):
		return True
	if df.get("fieldtype") == "Select" and (df.get("options") or "").strip():
		return True  # create_new.get_static_default_value takes the first option
	return df.get("fieldtype") == "Check" or bool(df.get("fetch_from"))


def create_fieldnames(meta, config):
	"""Step 1's fields: the config's list plus any required field nothing else fills."""
	create = config.get("create") or {}
	out = list(create.get("fields") or [])
	filled = create.get("filled_on_create") or {}
	for df in meta.fields:
		name = df.get("fieldname")
		if name in out or name in filled or not is_value_field(df) or not cint(df.get("reqd")):
			continue
		if _fills_itself(df):
			continue
		out.append(name)
	return out


def _shown(df, section, config, forced):
	name = df.get("fieldname")
	if name in (config.get("skip_fields") or {}):
		return False
	fieldtype = df.get("fieldtype")
	if fieldtype in ("Button", "Fold"):
		return False
	if name in forced:
		return True
	if fieldtype == "HTML" and not (df.get("options") or "").strip():
		return False
	return not (cint(df.get("hidden")) or section["hidden"])


def applies(rule, doc):
	"""Whether a step's ``applies`` rule holds for ``doc`` (a Document or a dict)."""
	if not rule:
		return True
	if doc is None:
		return False
	value = doc.get(rule["field"])
	if isinstance(value, list | tuple):
		row_field = rule.get("row_field")
		values = {(row.get(row_field) if row_field else None) for row in value}
	else:
		values = {value}
	return any(choice in values for choice in rule.get("in") or [])


def _tidy(items):
	"""Drop empty sub-sections and column breaks that would open an empty column."""
	segments, current = [], {"head": None, "body": []}
	for item in items:
		if item["fieldtype"] == "Section Break":
			segments.append(current)
			current = {"head": item, "body": []}
		else:
			current["body"].append(item)
	segments.append(current)
	out = []
	for segment in segments:
		body = []
		for item in segment["body"]:
			if item["fieldtype"] == "Column Break" and (not body or body[-1]["fieldtype"] == "Column Break"):
				continue
			body.append(item)
		while body and body[-1]["fieldtype"] == "Column Break":
			body.pop()
		if not any(item["fieldtype"] not in LAYOUT_TYPES for item in body):
			continue
		head = segment["head"]
		if head is not None and (out or head.get("depends_on")):
			out.append(head)
		out.extend(body)
	return out


def won_conditions(config):
	"""``{fieldname: mandatory_depends_on}`` for the config's ``won`` fields.

	What a move into the won status must carry is required while the record has that status: the
	Desk's own rule (``opportunity_migrated_scripts.js`` ``set_rank_fields_mandatory`` sets the three
	ranks ``reqd`` while the status is Closed Won, and ``validate_ranks_on_won`` refuses the save
	without them). The form says it with ``set_df_property``, which the page cannot run, so the flow
	carries it as the docfield's own condition and the page shows the asterisk the form shows.
	"""
	won = config.get("won") or {}
	status = won.get("status")
	if not status:
		return {}
	field = won.get("status_field") or "status"
	condition = f"eval:doc.{field}==={json.dumps(status)}"
	return {fieldname: condition for fieldname in won.get("fields") or []}


def query_context(doctype, doc, steps, user=None):
	"""What the page's queries read that its values do not hold: ``{"fields", "directory"}``.

	``fields`` are the record's values of the fields a ``queries`` entry names (``by`` and
	``{"field": ...}``) that the flow does not show (Opportunity's ``company``, for a Prospect),
	each only at a permlevel the user can read. ``directory`` is the Contact & Address directory's
	link sources (``unified_tab_controller.js`` ``get_all_party_sources``) other than the live
	party, which the page adds itself as it changes: the saved record's own name, its
	``customer`` / ``supplier`` / ``custom_opportunity`` links, and every party a readable child
	table names (``customer``, ``supplier``, ``party_name`` with ``party_type``, ``link_name`` with
	``link_doctype``: Opportunity's stakeholder rows). Names only, as the directory matches them.
	"""
	config = get_config(doctype) or {}
	queries = config.get("queries") or {}
	out = {"fields": {}, "directory": []}
	if not queries or doc is None:
		return out
	meta = frappe.get_meta(doctype)
	access = access_for(doctype, meta, doc, user)
	shown = value_fields(steps)

	def readable(fieldname):
		df = meta.get_field(fieldname)
		return df is not None and cint(df.get("permlevel")) in access["read_levels"]

	for query in queries.values():
		for fieldname in query_fieldnames(query):
			if fieldname in shown or fieldname in out["fields"] or not readable(fieldname):
				continue
			value = doc.get(fieldname)
			out["fields"][fieldname] = None if value is None else str(value)
	if not any(query.get("links") == "directory" for query in queries.values()) or _is_new(doc):
		return out
	names = []

	def add(name):
		name = str(name or "").strip()
		if name and name not in names:
			names.append(name)

	add(doc.get("name"))
	for fieldname in ("customer", "supplier", "custom_opportunity"):
		if readable(fieldname):
			add(doc.get(fieldname))
	for df in meta.fields:
		if df.get("fieldtype") != "Table" or cint(df.get("permlevel")) not in access["read_levels"]:
			continue
		for row in doc.get(df.get("fieldname")) or []:
			add(row.get("customer"))
			add(row.get("supplier"))
			if row.get("party_type"):
				add(row.get("party_name"))
			if row.get("link_doctype"):
				add(row.get("link_name"))
	out["directory"] = names
	return out


def _section_break(section):
	return {
		"fieldtype": "Section Break",
		"fieldname": section["key"],
		"label": _(section["label"]) if section["label"] else "",
		"depends_on": section["depends_on"],
	}


def flow_steps(doctype, doc=None, user=None):
	"""The guided flow for ``doctype``: ``[{key, title, intro, tab, applies, active, read_only,
	final, link, create, extension, more, fields}]``, for ``user`` (default: the session user) on
	``doc`` (None, or an unsaved document, for a record not yet created).

	``fields`` are field definitions (:func:`field_def`) with Section Break and Column Break
	entries for layout; a Section Break carries the section's own ``depends_on``.
	"""
	config = get_config(doctype)
	if not config:
		frappe.throw(_("The guided flow is not set up for {0}.").format(_(doctype)), frappe.PermissionError)
	meta = frappe.get_meta(doctype)
	access = access_for(doctype, meta, doc, user)
	sections = sections_of(meta)
	located = {df.get("fieldname"): section for section in sections for df in section["fields"]}
	create = create_fieldnames(meta, config)
	forced = set(config.get("show_hidden") or {}) | set(create)
	read_only_names = set(config.get("read_only_fields") or {})
	required_when = won_conditions(config)
	# Editable while the record is being created, read-only once it exists (an Item's code is its
	# name: it changes by renaming the Item, never by typing over it).
	if not access["is_new"]:
		read_only_names |= set(config.get("once_fields") or {})
	specs = list(config["steps"])

	# Every create field lives in step 1: moved there from wherever the meta put it.
	moves = {spec["key"]: list(spec.get("fields") or []) for spec in specs}
	first = specs[0]
	first_sections = set(first.get("sections") or [])
	for name in create:
		section = located.get(name)
		if section is None or section["key"] in first_sections or name in moves[first["key"]]:
			continue
		for key in moves:
			if name in moves[key]:
				moves[key].remove(name)
		moves[first["key"]].append(name)
	moved = {name for names in moves.values() for name in names}

	def defs(dfs, read_only, mark_create=False):
		out = []
		for df in dfs:
			if df.get("fieldtype") in LAYOUT_TYPES:
				out.append({"fieldtype": df.get("fieldtype"), "fieldname": df.get("fieldname")})
				continue
			field = field_def(
				df,
				access,
				read_only=read_only,
				read_only_names=read_only_names,
				help_entry=field_help.for_field(doctype, df.get("fieldname")),
			)
			if field is None:
				continue
			if field["fieldname"] in required_when and not field.get("mandatory_depends_on"):
				field["mandatory_depends_on"] = required_when[field["fieldname"]]
			if mark_create and field["fieldname"] in create:
				field["create"] = 1
			out.append(field)
		return out

	def body(section):
		return [
			df
			for df in section["fields"]
			if df.get("fieldname") not in moved and _shown(df, section, config, forced)
		]

	claimed = set()
	built = []
	for spec in specs:
		mark_create = spec is first
		read_only = bool(spec.get("read_only"))
		items = []
		moved_here = []
		for name in moves[spec["key"]]:
			section = located.get(name)
			df = meta.get_field(name) if section else None
			if df is not None and _shown(df, section, config, forced):
				moved_here.append(df)
		items.extend(defs(moved_here, read_only, mark_create))
		tab = None
		for key in spec.get("sections") or []:
			section = next((s for s in sections if s["key"] == key), None)
			if section is None:
				continue
			claimed.add(key)
			tab = tab or section["tab"]
			items.append(_section_break(section))
			items.extend(defs(body(section), read_only, mark_create))
		built.append((spec, tab, _tidy(items)))

	# A section no step claims is walked as a step of its own. When the config has "more" steps
	# (past the core of the flow), such a section is one of those too: it is something the form
	# shows that nobody placed, so it never lands between the core steps.
	any_more = any(spec.get("more") for spec in specs)
	extra = []
	for section in sections:
		if section["key"] in claimed:
			continue
		items = [_section_break(section), *defs(body(section), False)]
		title = section["label"] or section["tab"]["label"] or doctype
		spec = {"key": section["key"], "title": title, "intro": "", "more": any_more}
		extra.append((spec, section["tab"], _tidy(items)))

	if built and built[-1][0].get("final"):
		ordered = built[:-1] + extra + built[-1:]
	else:
		ordered = built + extra

	steps = []
	for spec, tab, fields in ordered:
		if not any(field["fieldtype"] not in LAYOUT_TYPES for field in fields):
			continue
		rule = spec.get("applies")
		steps.append(
			{
				"key": spec["key"],
				"title": _(spec["title"]),
				"intro": _(spec["intro"]) if spec.get("intro") else "",
				"tab": _(tab["label"]) if tab and tab.get("label") else "",
				"applies": rule,
				"active": applies(rule, doc) if rule else True,
				"read_only": 1 if spec.get("read_only") else 0,
				"final": 1 if spec.get("final") else 0,
				"link": spec.get("link"),
				"create": 1 if spec is first else 0,
				# The block the DocType's page extension draws above the fields (config docstring).
				"extension": spec.get("extension") or None,
				"more": 1 if spec.get("more") else 0,
				"fields": fields,
			}
		)
	return steps


# ---------------------------------------------------------------------------
# Reading the steps back
# ---------------------------------------------------------------------------


def find_step(steps, step_key):
	for step in steps:
		if step["key"] == step_key:
			return step
	frappe.throw(_("There is no step {0} in this flow.").format(step_key))


def value_fields(steps, step_key=None):
	"""``{fieldname: field def}`` of every value field shown (in one step, when given)."""
	chosen = [find_step(steps, step_key)] if step_key else steps
	return {
		field["fieldname"]: field
		for step in chosen
		for field in step["fields"]
		if field["fieldtype"] not in LAYOUT_TYPES
	}


def editable_fields(steps, step_key=None, create_only=False):
	"""``{fieldname: None | [child fieldnames]}``: exactly what a save may assign.

	Read-only fields (by permission, by docfield, by step) are left out; a table maps to the
	child fields the user may write.
	"""
	out = {}
	for fieldname, field in value_fields(steps, step_key).items():
		if field.get("read_only") or (create_only and not field.get("create")):
			continue
		if field["fieldtype"] in TABLE_TYPES:
			out[fieldname] = [
				child["fieldname"] for child in field.get("child_fields") or [] if not child.get("read_only")
			]
		else:
			out[fieldname] = None
	return out


def prefill_fields(doctype, steps, user=None):
	"""``{fieldname: None | [child fieldnames]}``: what a new record may take from where it was
	started (``frappe.new_doc``'s options, or a mapped document), beyond step 1.

	Every field the flow lets this user write, in any step, plus the config's
	``create.prefill_fields``: fields the flow does not show that such a start fills. Those are
	held to the same rule as a flow field: a value field the user may write at its permlevel,
	not read-only, never a table.
	"""
	out = editable_fields(steps)
	listed = (get_config(doctype).get("create") or {}).get("prefill_fields") or {}
	if not listed:
		return out
	meta = frappe.get_meta(doctype)
	access = access_for(doctype, meta, None, user)
	for fieldname in listed:
		df = meta.get_field(fieldname)
		if fieldname in out or df is None or not is_value_field(df) or df.get("fieldtype") in TABLE_TYPES:
			continue
		if cint(df.get("permlevel")) not in access["write_levels"] or cint(df.get("read_only")):
			continue
		out[fieldname] = None
	return out
