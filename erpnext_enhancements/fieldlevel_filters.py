"""Field references in read requests, held to the caller's field-level read (v1.835.0).

``fieldlevel_read.py`` keeps the fields a caller cannot read at their permlevel out of the documents
the app sends. This module applies the same rule to the *field names a request carries*: a list,
count, value, search or chart request that names such a field in ``filters``, ``or_filters``,
``order_by``, ``group_by``, an aggregate or another ``fields`` expression is refused with a plain
permission message. A field of a child table answers to the parent doctype's levels, as in
``fieldlevel_read``. Nothing else changes: a caller who can read every field of the doctype (and
of its child tables) leaves on the first check, Administrator included, and server-side code never
passes through here.

**How this sits beside frappe 16.56.0.** ``frappe.get_list`` builds its query
with ``frappe.model.qb_query.DatabaseQuery`` (``frappe/__init__.py`` 1376-1378), which hands it to
``frappe.database.query.Engine.get_query``. With permissions applied, the Engine holds a field named
in a filter (``_validate_and_prepare_filter_field``, query.py 863-1006), in ``order_by`` and
``group_by`` (``_validate_and_parse_field_for_clause``, 1315-1378) and inside an aggregate
(``SQLFunctionParser._validate_string_argument``, 2481-2557) to the caller's readable levels,
and it refuses a SQL function written as a string in ``fields`` (2257-2282). This module holds
every route listed below to one rule, whatever path the route takes inside frappe: a child table's
fields answer to the parent's levels in every notation, and a document looked up by ``filters``, a
value read from a Single and the next-record sort field follow the same levels
(``frappe.client.get``, ``get_value``, ``get_single_value``, ``frappe.desk.form.utils.get_next``),
as do the two date fields a calendar request names (``frappe.desk.calendar.get_events``).

A plain name in ``fields`` is left to frappe, which drops a field the caller cannot read from what
it selects (query.py 1447-1531; ``reportview.validate_fields``, reportview.py 151-181), except an
entry that names a closed child table (below).

**A child table the caller cannot read is closed.** When the table field holding a child table is
not readable at the caller's levels, or the caller reads no level of the doctype at all, every
reference to that child table is refused, whatever the column, the standard columns included: in
filters (``table_field.col``, `` `tabChild`.col ``, ``[child, col, op, value]``), sort, grouping, an
aggregate, a plain ``fields`` entry and a child query (``{"table_field": [...]}``). A request whose own
doctype is the child table, read through such a parent (``parent``, ``parent_doctype``,
``parent_document_type``), is refused whole on every route that takes a parent. frappe lists a child
doctype through a parent after asking only for ``select`` on the parent, and lets the standard
columns through (``_child_open`` has the lines). A child table whose table field the caller does read
keeps its standard columns open and its own hidden columns refused.

**A name the doctype does not have is read as frappe reads it.** frappe's query engine looks a plain
filter name its doctype lacks up in the doctype's child tables, for a filter that names no doctype
(query.py 966-1002), so in ``filters`` and ``or_filters`` that reach the engine such a name is held to
the child field it resolves to. Every caller of ``check_request`` states whether its filters reach
the engine (``child_names``). Anywhere else (sort, grouping, an aggregate, a search field, a Single's
filters, a filter that names its own doctype) a name the doctype lacks is not one of its fields and
is not refused, and neither is a standard column of the doctype itself (``name``, ``owner``,
``_assign`` and the rest).
**Link search** follows frappe's own choice of path (search.py 125-133): with a custom query, the
``query`` argument or the doctype's ``standard_queries`` hook, the filters are that query's arguments
and are not read as field names; the search field is checked on both paths.

**A name is matched as the database matches it.** MariaDB reads a column name in any case, and
frappe's own lookups do not, so every name a request carries is looked up here without case
(``_fold``): another spelling of a field the caller cannot read is that field.

**``fields`` and ``filters`` are read either way round** where the route reaches
``frappe.get_list``, which takes them in either order (``_swapped``).

**Where frappe applies no permission, another doctype's fields are held here.** On every route
but one, frappe's query engine applies the caller's permissions and holds a field named in a linked
doctype (``link_field.field``) to that doctype's own. ``frappe.client.get`` is the one: it finds its
document with permissions off. There the filters are read for every caller, and a field named in
another doctype is held as frappe holds it everywhere else: to that doctype's read levels, to its
level-0 fields for a caller with ``select`` only, to its standard columns for a caller with no access
(``_Hidden._foreign``).

**One named exception.** ``REFERENCES_NOT_HELD_BACK`` lists the field references a doctype does not
hold back from a caller below the field's level: only ``User.user_type``, for the reason given
there. A caller with ``select`` but not ``read`` on a doctype is not exempt in general, and
``select`` is never counted as ``read``.

**How each route is reached.** The RPC routes are whitelisted methods, wrapped here through
``override_whitelisted_methods`` exactly as in ``fieldlevel_read``: each wrapper restates frappe's
parameter list and HTTP methods (``tests/test_fieldlevel_filters.py`` pins both against 16.56.0,
plus the link-search arguments 16.60 added, which ``_declared`` forwards only where they exist),
checks the request, and then calls frappe's own function unchanged. ``seal_filter_originals``, a
``before_request`` hook, takes frappe's own copies off the whitelist once their canonical names are
overridden, so no other dotted name for the same function (``frappe.client.search_widget`` is one)
answers HTTP. The REST list routes (``GET`` and ``HEAD`` on ``/api/resource/<doctype>``,
``/api/v2/document/<doctype>`` and ``/api/v2/doctype/<doctype>/count``) are werkzeug routes no
override reaches, and frappe runs
``before_request`` hooks before it reads an API key or token (app.py 114-116 and 219-220), so they
are checked in ``after_request``, where the session user is the caller: a refused request's body is
replaced by a 403 before anything is sent.

**Unknown shapes are refused.** For a caller with hidden fields on the doctype, a field reference
this module cannot read as one of frappe's forms (``field``, `` `field` ``,
`` `tabDocType`.`field` ``, ``table_or_link_field.field``) is refused rather than passed, except where
frappe itself reads a bare value as a document name (a top-level filter list of plain values, or a
string filter). Those two positions are only refused when the value reads as a hidden field.
"""

import inspect
import json
import re
import unicodedata

import frappe

from erpnext_enhancements.fieldlevel_read import (
	_TABLE_FIELDTYPES,
	_hides_anything,
	_is_readable,
	_log_failure,
	_readable_levels,
)

#: frappe's backtick notation, `` `tabDocType`.`fieldname` `` or `` `tabDocType`.fieldname ``:
#: BACKTICK_FIELD_PARSE_REGEX, frappe/database/query.py:177 at v16.56.0.
_BACKTICK_REF = re.compile(r"^`tab([\w\s-]+)`\.(`?)(\w+)\2$")
#: frappe's other spellings of a table and a column, with a backtick, a double quote or nothing
#: around either part: FIELD_PARSE_REGEX and CHILD_TABLE_FIELD_PATTERN, query.py 174 and 183.
_TAB_REF = re.compile(r'^[`"]?tab([\w\s-]+)[`"]?\.([`"]?)(\w+)\2$')
_SIMPLE = re.compile(r"^\w+$")
_DOTTED = re.compile(r"^(\w+)\.(\w+)$")
_QUOTED = re.compile(r"^(['\"]).*\1$", re.S)
_DIRECTION = re.compile(r"\s+(?:asc|desc)$", re.I)
#: An alias after a dotted reference: frappe takes it off before it reads the reference
#: (``DynamicTableField.parse``, query.py 2042-2051).
_ALIAS = re.compile(r"\s+as\s+", re.I)
#: The table a plain ``fields`` entry names, whatever follows: ``tabChild.`` with a backtick, a double
#: quote or nothing around it (query.py 183), or ``table_field.``.
_TAB_PREFIX = re.compile(r'^[`"]?tab([\w\s-]+)[`"]?\.')
_TABLE_PREFIX = re.compile(r"^`?(\w+)`?\.")

#: frappe's default: frappe.whitelist() with no methods answers these four.
ANY_METHOD = ["GET", "POST", "PUT", "DELETE"]

#: What a refused caller reads. It says how to get out of it: a saved list or report that uses such
#: a field keeps being refused until the filter, sort or column is taken off.
REFUSAL = (
	"This request filters, sorts, groups, totals or shows a column by a field or table you do not "
	"have permission to read. If this is a saved list or report, remove that filter, sort or "
	"column (in a report: Menu > Pick Columns)."
)

#: frappe's functions that the wrappers below replace, by canonical dotted path: the
#: override_whitelisted_methods keys in hooks.py. tests/test_fieldlevel_filters.py holds the two equal.
FILTER_WRAPPED_ORIGINALS = (
	"frappe.desk.reportview.get",
	"frappe.desk.reportview.get_list",
	"frappe.desk.reportview.get_count",
	"frappe.desk.reportview.export_query",
	"frappe.desk.reportview.get_sidebar_stats",
	"frappe.desk.reportview.get_stats",
	"frappe.desk.reportview.get_filter_dashboard_data",
	"frappe.desk.listview.get_group_by_count",
	"frappe.client.get_list",
	"frappe.client.get_count",
	"frappe.client.get",
	"frappe.client.get_value",
	"frappe.client.get_single_value",
	"frappe.desk.search.search_link",
	"frappe.desk.search.search_widget",
	"frappe.desk.form.utils.get_next",
	"frappe.desk.doctype.number_card.number_card.get_result",
	"frappe.desk.doctype.number_card.number_card.get_percentage_difference",
	"frappe.desk.doctype.dashboard_chart.dashboard_chart.get",
	"frappe.desk.calendar.get_events",
)

#: The REST list routes: v1 is mounted at both /api and /api/v1.
_REST_V1_LIST = re.compile(r"^/api/(?:v1/)?resource/([^/]+)/?$")
_REST_V2_LIST = re.compile(r"^/api/v2/document/([^/]+)/?$")
_REST_V2_COUNT = re.compile(r"^/api/v2/doctype/([^/]+)/count/?$")

#: Columns every doctype has: ``default_fields``, ``optional_fields`` and ``child_table_fields``,
#: frappe/model/__init__.py 84-98 at v16.56.0. frappe's query engine never looks one of these up in a
#: child table (query.py 980), and none is a field a permlevel can hide. On a closed child table they
#: are refused with every other column (``_Hidden.closed_tables``).
_STANDARD_NAMES = frozenset(
	(
		"doctype",
		"name",
		"owner",
		"creation",
		"modified",
		"modified_by",
		"docstatus",
		"idx",
		"parent",
		"parentfield",
		"parenttype",
		"_user_tags",
		"_assign",
		"_liked_by",
		"_seen",
	)
)

#: Field references a doctype does not hold back from a caller below the field's level, by doctype.
#: Only the reference: the value itself still follows the field's level wherever a document is sent.
#: - ``User.user_type`` (level 1 in frappe 16.56.0's User, which gives Desk User only ``select``): it
#:   says only whether an account is a desk account or a website account. The Desk and this app list
#:   staff by it (frappe's Users workspace cards; the Create Project prompt's ``{"enabled": 1,
#:   "user_type": "System User"}``), and every user picker already shows which accounts are desk
#:   accounts. frappe's query engine makes the same exception for filtering, sorting and grouping,
#:   for a caller with ``select`` only and for one with ``read`` (query.py 1104-1107 and 1113-1118);
#:   for everything else a ``select``-only caller may filter, sort and group by level-0 fields and
#:   the standard columns only (1074-1108). Here the exception holds in every position, an aggregate
#:   argument included, on every wrapped route. Every other level-1 field of User is still refused.
REFERENCES_NOT_HELD_BACK = {"User": frozenset({"user_type"})}

_SEALED = set()
_seal_failure_logged = False


class _Unknown(Exception):
	"""A field reference in a shape this module does not read: refused for a caller with hidden fields."""


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


def _is_scalar(value):
	return value is None or isinstance(value, str | int | float | bool)


def _fold(name):
	"""``name`` as MariaDB compares a column name: without case.

	MariaDB matches a column name whatever its case, so ``CTC``, ``Ctc`` and ``ctc`` are one column
	to it, while frappe's own lookups tell them apart. A name with a character outside ASCII is also
	read without its accents and compatibility forms. That is wider than MariaDB's rule on purpose:
	such a name is refused when it folds to a field the caller cannot read.
	"""
	if name.isascii():
		return name.lower()
	plain = unicodedata.normalize("NFKD", name)
	return "".join(ch for ch in plain if not unicodedata.combining(ch)).casefold()


class _Names:
	"""The field names of one doctype and the ones the caller cannot read, matched as MariaDB matches
	a column name. A name spelt exactly as a field is that field; any other spelling is looked up
	without case (``_fold``)."""

	def __init__(self, fieldnames, hidden):
		self.exact = set(fieldnames)
		self.hidden = set(hidden)
		self._folded = {_fold(name) for name in self.exact}
		self._folded_hidden = {_fold(name) for name in self.hidden}

	def has(self, name):
		return name in self.exact or _fold(name) in self._folded

	def hides(self, name):
		if name in self.exact:
			return name in self.hidden
		return _fold(name) in self._folded_hidden


_FOLDED_STANDARD_NAMES = frozenset(_fold(name) for name in _STANDARD_NAMES)


class _Hidden:
	"""The fields of one doctype, and of its child tables, that the session user may not read."""

	def __init__(self, doctype, meta, levels, closed=False, lenient=False):
		self.doctype = doctype
		#: The whole request is refused: ``doctype`` is a child table read through a parent whose table
		#: field holding it the caller cannot read (``_child_open``).
		self.closed = closed
		#: Nothing of this doctype is hidden from the caller. The request is read only for what it
		#: names in other doctypes (``unguarded``), and a shape that cannot be read is left to frappe.
		self.lenient = lenient
		#: The route hands its filters to frappe with no permission applied to them
		#: (``check_request``), so what they name in another doctype is held here (``_foreign``).
		self.unguarded = False

		def readable(df):
			return lenient or _is_readable(df, levels)

		self.fields = {df.fieldname: df for df in meta.fields}
		self.hidden = {df.fieldname for df in meta.fields if not readable(df)}
		self.hidden -= REFERENCES_NOT_HELD_BACK.get(doctype, frozenset())
		#: This doctype's own names, and its fields by folded name (a Link field spelt in any case).
		self.own = _Names(self.fields, self.hidden)
		self._folded_fields = {_fold(name): df for name, df in self.fields.items()}
		#: table fieldname -> child doctype; child doctype -> its names. A table field the caller
		#: cannot read hides every field of its child doctype.
		self.tables = {}
		self.children = {}
		#: The names of the child per table field, in the doctype's field order: the order frappe
		#: searches for a filter name the doctype lacks (query.py 983-1002).
		self.child_order = []
		#: The table fields the caller cannot read, every one when the caller reads no level of the
		#: doctype, and the child doctypes they hold. Every reference to such a child table is refused,
		#: whatever the column, the standard columns included.
		self.closed_tables = set()
		self.closed_children = set()
		child_fieldnames, child_hidden = {}, {}
		for df in meta.fields:
			if df.fieldtype not in _TABLE_FIELDTYPES or not df.options:
				continue
			child_fields = frappe.get_meta(df.options).fields
			if lenient or (levels and _is_readable(df, levels)):
				hidden = {cdf.fieldname for cdf in child_fields if not readable(cdf)}
			else:
				hidden = {cdf.fieldname for cdf in child_fields}
				self.closed_tables.add(df.fieldname)
				self.closed_children.add(df.options)
			names = {cdf.fieldname for cdf in child_fields}
			self.tables[df.fieldname] = df.options
			child_fieldnames[df.options] = names
			child_hidden.setdefault(df.options, set()).update(hidden)
			self.child_order.append(_Names(names, hidden))
		for child, names in child_fieldnames.items():
			self.children[child] = _Names(names, child_hidden[child])
		self._folded_tables = {_fold(name): name for name in self.tables}
		self._folded_children = {_fold(name): name for name in self.children}

	# -- names, in any case --------------------------------------------

	def _table_field(self, name):
		"""The table field of this doctype that ``name`` spells, or None."""
		if name in self.tables:
			return name
		return self._folded_tables.get(_fold(name))

	def _child(self, doctype):
		"""The child doctype of this one that ``doctype`` spells, or None."""
		if doctype in self.children:
			return doctype
		return self._folded_children.get(_fold(doctype))

	def _is_own(self, doctype):
		return doctype == self.doctype or _fold(doctype) == _fold(self.doctype)

	def _unknown(self, what, strict=True):
		"""A reference in no form this module reads. Refused (``_Unknown``) in a position that only
		takes field references; not hidden where a plain value may stand, or when nothing of this
		doctype is hidden from the caller, in which case frappe answers it as it always did."""
		if strict and not self.lenient:
			raise _Unknown(what)
		return False

	# -- one field reference --------------------------------------------

	def names(self, ref, doctype=None, strict=True, child_names=False):
		"""Whether ``ref``, one field reference in one of frappe's forms, names a hidden field.

		``doctype`` is the first element of a ``[doctype, field, operator, value]`` filter. A reference
		in no form frappe reads raises ``_Unknown`` when ``strict``, and is not hidden otherwise.
		``child_names``: see ``_own_or_child``.
		"""
		if not isinstance(ref, str):
			return self._unknown(ref, strict)
		ref = ref.strip()
		if "." in ref:
			ref = _ALIAS.split(ref, maxsplit=1)[0].strip()
		match = _BACKTICK_REF.match(ref)
		if match:
			return self._in_doctype(match.group(1), match.group(3), strict)
		if len(ref) > 2 and ref[0] == ref[-1] == "`" and _SIMPLE.match(ref[1:-1]):
			ref = ref[1:-1]
		if _SIMPLE.match(ref):
			if doctype and not self._is_own(doctype):
				child = self._child(doctype)
				if child:
					return child in self.closed_children or self.children[child].hides(ref)
				if not strict:
					return False
				return self._in_doctype(doctype, ref, strict)
			# A filter that names its own doctype is resolved on that doctype only (query.py 977-979).
			return self._own_or_child(ref, child_names and not doctype)
		match = _DOTTED.match(ref)
		if match:
			table, fieldname = match.groups()
			table_field = self._table_field(table)
			if table_field:
				child = self.tables[table_field]
				return table_field in self.closed_tables or self.children[child].hides(fieldname)
			df = self.fields.get(table) or self._folded_fields.get(_fold(table))
			if df is not None:
				# A Link: its own level here. The field it points at is frappe's check, made against
				# the linked doctype's own permissions, except on a route where frappe makes none.
				if self.own.hides(table):
					return True
				if self.unguarded and df.fieldtype == "Link" and df.options:
					return self._foreign(df.options, fieldname)
				return False
		match = _TAB_REF.match(ref)
		if match:
			return self._in_doctype(match.group(1).strip(), match.group(3), strict)
		return self._unknown(ref, strict)

	def _own_or_child(self, fieldname, child_names):
		"""A plain name: a field of this doctype, else, with ``child_names`` only, a child table's.

		frappe's query engine looks a filter name its doctype lacks up in the doctype's child tables,
		for a filter that names no doctype and a name that is not a standard column, and uses the
		first table, in field order, whose child has a field of that name
		(``_validate_and_prepare_filter_field``, query.py 966-1002). That is the one place such a name
		refers to this doctype's data. A sort, group or aggregate name is resolved on the doctype's own
		table (1315-1378, 2481-2557), a Single's filters are compared with its stored values, and a
		search with a custom query hands its filters to that query: there a name the doctype lacks is
		simply not one of its fields.
		"""
		if self.own.has(fieldname):
			return self.own.hides(fieldname)
		if not child_names or _fold(fieldname) in _FOLDED_STANDARD_NAMES:
			return False
		for names in self.child_order:
			if names.has(fieldname):
				return names.hides(fieldname)
		return False

	def _in_doctype(self, doctype, fieldname, strict=True):
		if self._is_own(doctype):
			return self.own.hides(fieldname)
		child = self._child(doctype)
		if child:
			return child in self.closed_children or self.children[child].hides(fieldname)
		if self.unguarded:
			return self._foreign(doctype, fieldname)
		try:
			is_table = getattr(frappe.get_meta(doctype), "istable", False)
		except frappe.DoesNotExistError:
			# Not a doctype: nothing to judge it against, and frappe refuses it too.
			return self._unknown(doctype, strict)
		if is_table:
			# A child table of some other doctype: not one this request can be judged against.
			raise _Unknown(doctype)
		# Any other doctype: frappe checks it against that doctype's own permissions.
		return False

	def _foreign(self, doctype, fieldname):
		"""A field of a doctype that is neither this one nor a child table of it, on a route where
		frappe applies no permission to the filters (``unguarded``): the doctype a Link field points
		at, or a table named outright.

		It is held as frappe's query engine holds it wherever it does apply permissions
		(``check_filter_field_permission`` and ``_get_filterable_fields``, query.py 888-898 and
		1074-1118): a caller who reads the doctype may name its fields at the levels they read; one
		with ``select`` only, its level-0 fields; one with neither, its standard columns alone. A
		child table of another doctype, and a name that is not a doctype at all, are refused.
		"""
		if _fold(fieldname) in {_fold(name) for name in REFERENCES_NOT_HELD_BACK.get(doctype, ())}:
			return False
		try:
			levels = _readable_levels(doctype)
			if levels is None:
				return False
			meta = frappe.get_meta(doctype)
		except frappe.DoesNotExistError:
			return True
		if getattr(meta, "istable", False):
			return True
		fieldnames = [df.fieldname for df in meta.fields]
		if 0 in levels or _has_select(meta):
			hidden = [df.fieldname for df in meta.fields if not _is_readable(df, levels)]
		else:
			hidden = fieldnames
		return _Names(fieldnames, hidden).hides(fieldname)

	# -- the positions a request names fields in ---------------------------

	def in_filters(self, filters, child_names=False):
		"""``filters`` / ``or_filters`` in every shape frappe's Engine accepts.

		``child_names``: whether these filters reach frappe's query engine, which looks a plain name
		the doctype lacks up in its child tables (``_own_or_child``).
		"""
		if filters is None or filters == "":
			return False
		if isinstance(filters, str):
			try:
				parsed = json.loads(filters)
			except ValueError:
				return False  # a document name: frappe reads a bare value as {"name": value}
			if isinstance(parsed, str):
				return False
			return self.in_filters(parsed, child_names)
		if isinstance(filters, int | float | bool):
			return False
		if isinstance(filters, dict):
			return any(self.names(key, child_names=child_names) for key in filters)
		if isinstance(filters, list | tuple):
			# Read as given, and as ``frappe.db.get_values`` leaves it: that takes every None out of
			# the list before the query engine reads it (database.py 662), and it is how a document
			# looked up by filters is found.
			if self._in_list(filters, child_names):
				return True
			kept = [item for item in filters if item is not None]
			return len(kept) != len(filters) and self._in_list(kept, child_names)
		return self._unknown(type(filters).__name__)

	def _in_list(self, items, child_names):
		"""One reading of a top-level list: conditions when it starts with one, else ``_flat``."""
		if not items:
			return False
		if not isinstance(items[0], list | tuple | dict):
			return self._flat(items, child_names)
		return any(self._condition(item, child_names) for item in items if item is not None)

	def _flat(self, items, child_names):
		"""A top-level list that starts with a plain value: one condition, or a list of names.

		``[field, operator, value]`` and ``[doctype, field, operator, value(, x)]`` are conditions;
		anything else is a list of document names. A name is never refused for its shape, only when it
		reads as a hidden field.
		"""
		if len(items) == 3:
			return self.names(items[0], strict=False, child_names=child_names)
		if len(items) in (4, 5):
			doctype = items[0] if isinstance(items[0], str) else None
			return self.names(items[1], doctype=doctype, strict=False, child_names=child_names)
		return False

	def _condition(self, item, child_names):
		"""One element of a list of conditions, or of a nested ``[cond, "and"|"or", cond]`` group."""
		if isinstance(item, str):
			if item.strip().lower() in ("and", "or"):
				return False
			return self._unknown(item)
		if isinstance(item, dict):
			return self.in_filters(item, child_names)
		if isinstance(item, list | tuple):
			if not item:
				return self._unknown(item)
			if isinstance(item[0], list | tuple | dict):
				return any(self._condition(part, child_names) for part in item)
			if len(item) in (2, 3):
				return self.names(item[0], child_names=child_names)
			if len(item) in (4, 5):
				doctype = item[0] if isinstance(item[0], str) else None
				return self.names(item[1], doctype=doctype, child_names=child_names)
		return self._unknown(item)

	def in_clause(self, clause):
		"""``order_by`` / ``group_by``: comma-separated references, each with an optional direction."""
		if clause is None or clause == "":
			return False
		if not isinstance(clause, str):
			return self._unknown(clause)
		for part in clause.split(","):
			part = _DIRECTION.sub("", part.strip()).strip()
			if not part or part.isdigit():
				continue
			if self.names(part):
				return True
		return False

	def in_fields(self, fields):
		"""Expressions in ``fields``: every argument of a function or operator dict, at any depth.

		A plain entry is left to frappe, which drops a field the caller cannot read, unless it names a
		closed child table (``_closed_entry``).
		"""
		if fields is None or fields == "":
			return False
		if isinstance(fields, str):
			try:
				fields = json.loads(fields)
			except ValueError:
				fields = fields.split(",")  # plain names, comma-separated
		if isinstance(fields, str | dict):
			fields = [fields]
		if not isinstance(fields, list | tuple):
			return False
		return any(
			self.in_expression(field) if isinstance(field, dict) else self._closed_entry(field)
			for field in fields
		)

	def _closed_entry(self, entry):
		"""Whether ``entry``, a plain ``fields`` entry, names a closed child table: ``tabChild.col`` in
		any of frappe's spellings, or ``table_field.col`` (``*`` and an alias included)."""
		if not isinstance(entry, str) or not self.closed_tables:
			return False
		entry = entry.strip()
		match = _TAB_PREFIX.match(entry)
		if match and self._child(match.group(1).strip()) in self.closed_children:
			return True
		match = _TABLE_PREFIX.match(entry)
		return bool(match) and self._table_field(match.group(1)) in self.closed_tables

	def in_expression(self, value):
		if isinstance(value, dict):
			for key, part in value.items():
				if isinstance(key, str) and key.strip().lower() == "as":
					continue
				table_field = self._table_field(key) if isinstance(key, str) else None
				if table_field:
					# A child query, {table_field: [fields of that child]} (query.py 1231-1248); every
					# column of a closed child table reads as hidden (``names``).
					child = self.tables[table_field]
					parts = part if isinstance(part, list | tuple) else [part]
					if any(self.names(p, doctype=child, strict=False) for p in parts):
						return True
					continue
				if self.in_expression(part):
					return True
			return False
		if isinstance(value, list | tuple):
			return any(self.in_expression(part) for part in value)
		if isinstance(value, str):
			value = value.strip()
			if not value or value == "*" or value.isdigit() or _QUOTED.match(value):
				return False
			return self.names(value, strict=False)
		return False


def _hidden_for(doctype, parent=None):
	"""``_Hidden`` for ``doctype`` (read through ``parent`` when a child table), or None when the
	session user reads every field of it and of its child tables."""
	if not doctype or not isinstance(doctype, str):
		return None
	parent = parent if isinstance(parent, str) and parent else None
	levels = _readable_levels(doctype, parent)
	if levels is None:
		return None
	meta = frappe.get_meta(doctype)
	if parent and getattr(meta, "istable", False) and not _child_open(doctype, parent, levels):
		return _Hidden(doctype, meta, levels, closed=True)
	if not _hides_anything(meta, levels) and (levels or not _has_tables(meta)):
		# A caller who reads no level of the doctype cannot read its child tables either.
		return None
	return _Hidden(doctype, meta, levels)


def _has_select(meta):
	"""Whether the session user holds ``select`` on the doctype by role (``DocPerm.select`` at level
	0), which frappe counts when the user has no ``read`` (``frappe.only_has_select_perm``)."""
	roles = set(frappe.get_roles())
	return any(
		getattr(perm, "role", None) in roles
		and not int(getattr(perm, "permlevel", 0) or 0)
		and perm.get("select")
		for perm in meta.permissions or []
	)


def _has_tables(meta):
	return any(df.fieldtype in _TABLE_FIELDTYPES and df.options for df in meta.fields)


def _child_open(child, parent, levels):
	"""Whether the session user reads a table field of ``parent`` that holds ``child``.

	frappe 16.56.0 lists a child doctype through a parent after asking only for ``select`` on the
	parent: the query engine's ``check_select_permission`` (database/query.py 290, 1433-1438) calls
	``frappe.has_permission(child, "select", parent_doctype=parent)``, which for a child table goes to
	``has_child_permission`` (permissions.py 120-129). With no stored row in hand that checks only that
	``parent`` has a table of ``child`` (845-860) and then asks for the permission on the parent
	(893-905); the level of the table field is checked for one stored row only (864-884). The standard
	columns then pass ``apply_field_permissions``, since ``get_permitted_fields`` always includes them
	(model/__init__.py 217-263). So the level of the table field is held here.
	"""
	if not levels:
		return False
	return any(
		df.fieldtype in _TABLE_FIELDTYPES and df.options == child and _is_readable(df, levels)
		for df in frappe.get_meta(parent).fields
	)


def _nothing_hidden(doctype):
	"""``_Hidden`` for a doctype the session user reads every field of: the request is then read only
	for what it names in other doctypes (``check_request``, ``unguarded``)."""
	return _Hidden(doctype, frappe.get_meta(doctype), None, lenient=True)


def _swapped(fields, filters):
	"""Whether frappe's list query reads ``fields`` as the filters and ``filters`` as the fields.

	``frappe.get_list`` takes the two either way round (``DatabaseQuery.execute``,
	frappe/model/qb_query.py 107-115 at v16.56.0): ``fields`` given as a dict or as a list of lists is
	used as the filters, and so is anything in ``fields`` when ``filters`` is a list of two or more
	that starts with a string.
	"""
	if isinstance(fields, dict):
		return True
	if isinstance(fields, list | tuple) and fields and isinstance(fields[0], list | tuple):
		return True
	return (
		bool(fields)
		and isinstance(filters, list | tuple)
		and len(filters) > 1
		and isinstance(filters[0], str)
	)


def _refuse():
	frappe.throw(frappe._(REFUSAL), frappe.PermissionError, title=frappe._("Not Permitted"))


def check_request(
	doctype,
	parent=None,
	*,
	child_names,
	filters=None,
	or_filters=None,
	order_by=None,
	group_by=None,
	fields=None,
	references=(),
	expressions=(),
	swap=True,
	unguarded=False,
):
	"""Refuse a request on ``doctype`` that names a field the session user cannot read.

	``child_names`` is stated by every caller: True when ``filters`` and ``or_filters`` reach frappe's
	query engine, which looks a plain filter name the doctype lacks up in its child tables
	(``_Hidden._own_or_child``). It applies to those two only. ``references`` are single field
	references in sort, group or search-field position (strict); ``expressions`` are aggregate
	arguments, read as ``fields`` expressions are.

	``swap``: the route reaches ``frappe.get_list``, which reads ``fields`` and ``filters`` either way
	round (``_swapped``), so each is also read as the other. False only for the route that hands them
	to the query engine itself.

	``unguarded``: the route hands ``filters`` to frappe with no permission applied to them
	(``frappe.client.get`` finds its document through ``frappe.db.get_value``). There the request is
	read for every caller but Administrator, and a field it names in another doctype, through a Link
	field or by table, is held as frappe's query engine holds it elsewhere (``_Hidden._foreign``).
	"""
	hidden = _hidden_for(doctype, parent)
	if hidden is None:
		if not unguarded or not doctype or not isinstance(doctype, str):
			return
		if frappe.session.user == "Administrator":
			return
		hidden = _nothing_hidden(doctype)
	if hidden.closed:
		_refuse()
	hidden.unguarded = unguarded
	filters, fields = _parsed(filters), _parsed(fields)
	try:
		named = (
			hidden.in_filters(filters, child_names)
			or hidden.in_filters(or_filters, child_names)
			or hidden.in_clause(order_by)
			or hidden.in_clause(group_by)
			or hidden.in_fields(fields)
			or (
				swap
				and _swapped(fields, filters)
				and (hidden.in_filters(fields, child_names) or hidden.in_fields(filters))
			)
			or any(hidden.names(ref) for ref in references)
			or any(hidden.in_expression(value) for value in expressions)
		)
	except _Unknown:
		named = True
	if named:
		_refuse()


def _parsed(value):
	"""``value`` decoded from JSON when it is a JSON string, else as given."""
	if isinstance(value, str):
		try:
			return json.loads(value)
		except ValueError:
			return value
	return value


def _check_report_form(form, doctype=None):
	"""The report view's request (``frappe.desk.reportview``), read from ``form`` as frappe reads it."""
	doctype = doctype or form.get("doctype")
	references = []
	if form.get("aggregate_on_field"):
		on_doctype = form.get("aggregate_on_doctype") or doctype
		references.append(f"`tab{on_doctype}`.`{form.get('aggregate_on_field')}`")
	check_request(
		doctype,
		form.get("parent_doctype"),
		child_names=True,
		filters=form.get("filters"),
		or_filters=form.get("or_filters"),
		order_by=form.get("order_by"),
		group_by=form.get("group_by"),
		fields=form.get("fields"),
		references=references,
	)


def _stats_references(stats, key=None):
	"""The columns a sidebar-stats request groups by. Unreadable input yields ``[None]``, which the
	check refuses (only for a caller with hidden fields), as frappe would refuse it too."""
	stats = _parsed(stats)
	if isinstance(stats, str):
		stats = [stats]
	if not isinstance(stats, list | tuple):
		return [None]
	if key:
		return [s.get(key) if isinstance(s, dict) else None for s in stats]
	return list(stats)


# ---------------------------------------------------------------------------
# The seal: frappe's own copies stop answering HTTP under any other name
# ---------------------------------------------------------------------------


def seal_filter_originals():
	"""``before_request``: take frappe's copies of the wrapped functions off its whitelist.

	The same rule as ``fieldlevel_read.seal_wrapped_originals``: only an original whose canonical
	name this site overrides is sealed, and one no longer overridden goes back on. Never raises.
	"""
	global _seal_failure_logged
	try:
		whitelisted = frappe.whitelisted
		for path in FILTER_WRAPPED_ORIGINALS:
			fn = frappe.get_attr(path)
			if frappe.override_whitelisted_method(path) != path:
				if fn in whitelisted:
					whitelisted.discard(fn)
					_SEALED.add(fn)
			elif fn in _SEALED:
				whitelisted.add(fn)
				_SEALED.discard(fn)
	except Exception:
		if not _seal_failure_logged:
			_seal_failure_logged = True
			_log_failure("sealing frappe's list and value routes")


# ---------------------------------------------------------------------------
# The report view and list sidebar (frappe.desk.reportview, frappe.desk.listview)
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def reportview_get():
	"""``frappe.desk.reportview.get``, after the request's field references are checked."""
	from frappe.desk import reportview

	_check_report_form(frappe.form_dict)
	return reportview.get()


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def reportview_get_list():
	"""``frappe.desk.reportview.get_list``, after the request's field references are checked."""
	from frappe.desk import reportview

	_check_report_form(frappe.form_dict)
	return reportview.get_list()


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def reportview_get_count():
	"""``frappe.desk.reportview.get_count``, after the request's field references are checked."""
	from frappe.desk import reportview

	_check_report_form(frappe.form_dict)
	return reportview.get_count()


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def reportview_export_query():
	"""``frappe.desk.reportview.export_query``, checked before anything is exported or queued."""
	from frappe.desk import reportview

	_check_report_form(frappe.form_dict)
	return reportview.export_query()


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def reportview_get_sidebar_stats(stats, doctype, filters=None):
	"""``frappe.desk.reportview.get_sidebar_stats``: its filters and grouped columns checked."""
	from frappe.desk import reportview

	check_request(doctype, child_names=True, filters=filters, references=_stats_references(stats))
	# By keyword, here and in the two below: frappe's function sits behind a decorator of its own
	# (``frappe.read_only``, ``http_cache``), and its argument check reads keywords only through one.
	return reportview.get_sidebar_stats(stats=stats, doctype=doctype, filters=filters)


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def reportview_get_stats(stats, doctype, filters=None):
	"""``frappe.desk.reportview.get_stats``: its filters and grouped columns checked."""
	from frappe.desk import reportview

	check_request(doctype, child_names=True, filters=filters, references=_stats_references(stats))
	return reportview.get_stats(stats=stats, doctype=doctype, filters=filters)


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def reportview_get_filter_dashboard_data(stats, doctype, filters=None):
	"""``frappe.desk.reportview.get_filter_dashboard_data``: its filters and grouped columns checked."""
	from frappe.desk import reportview

	check_request(doctype, child_names=True, filters=filters, references=_stats_references(stats, key="name"))
	return reportview.get_filter_dashboard_data(stats, doctype, filters)


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def listview_get_group_by_count(doctype, current_filters, field):
	"""``frappe.desk.listview.get_group_by_count``: its filters and grouped field checked."""
	from frappe.desk import listview

	check_request(doctype, child_names=True, filters=current_filters, references=[field])
	return listview.get_group_by_count(doctype, current_filters, field)


# ---------------------------------------------------------------------------
# frappe.client reads
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def client_get_list(
	doctype,
	fields=None,
	filters=None,
	group_by=None,
	order_by=None,
	limit_start=None,
	limit_page_length=20,
	parent=None,
	debug=False,
	as_dict=True,
	or_filters=None,
	expand=None,
):
	"""``frappe.client.get_list``, after the request's field references are checked."""
	from frappe import client

	check_request(
		doctype,
		parent,
		child_names=True,
		filters=filters,
		or_filters=or_filters,
		order_by=order_by,
		group_by=group_by,
		fields=fields,
	)
	return client.get_list(
		doctype,
		fields,
		filters,
		group_by,
		order_by,
		limit_start,
		limit_page_length,
		parent,
		debug,
		as_dict,
		or_filters,
		expand,
	)


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def client_get_count(doctype, filters=None, debug=False, cache=False):
	"""``frappe.client.get_count``, its filters checked."""
	from frappe import client

	check_request(doctype, child_names=True, filters=filters)
	return client.get_count(doctype, filters, debug, cache)


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def client_get(doctype, name=None, filters=None, parent=None):
	"""``frappe.client.get``; a document looked up by ``filters`` has them checked first, and a child
	table named through ``parent`` is held to that parent's table field.

	frappe finds the document through ``frappe.db.get_value`` (client.py get, document.py
	``load_from_db``), whose query engine reads the filters with no permission applied to them. So
	this is the one ``unguarded`` route: the filters are read for every caller, and a field they name
	in another doctype is held to that doctype's levels here, as nothing in frappe holds it.
	"""
	from frappe import client

	if parent or (not name and filters):
		check_request(
			doctype, parent, child_names=True, filters=None if name else filters, unguarded=True
		)
	return client.get(doctype, name, filters, parent)


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def client_get_value(doctype, fieldname, filters=None, as_dict=True, debug=False, parent=None):
	"""``frappe.client.get_value``, its fields and filters checked.

	For a Single that hides fields from the caller, every requested name and filter is checked,
	and the whole-record dict a lookup by filters returns (``get_values_from_single``) leaves
	without the fields the caller cannot read.
	"""
	from frappe import client

	single = _check_get_value(doctype, fieldname, filters, parent)
	result = client.get_value(doctype, fieldname, filters, as_dict, debug, parent)
	if single is not None and isinstance(result, dict):
		return {key: value for key, value in result.items() if not single.own.hides(key)}
	return result


def _check_get_value(doctype, fieldname, filters, parent):
	"""Refuse a hidden field named in the request. Returns the ``_Hidden`` of a Single that hides
	fields from the caller, whose answer is then scrubbed, else None."""
	hidden = _hidden_for(doctype, parent)
	if hidden is None:
		return None
	fields = _parsed(fieldname)
	if not getattr(frappe.get_meta(doctype), "issingle", False):
		# Answered by frappe.client.get_list, whose query engine reads the filters (client.py 155-164).
		check_request(doctype, parent, child_names=True, filters=filters, fields=fields)
		return None
	# frappe.client.get_value's own normalisation (client.py 138-151, get_safe_filters).
	lookup = _parsed(filters)
	if isinstance(lookup, int | float) and not isinstance(lookup, bool):
		lookup = filters
	if isinstance(lookup, str):
		lookup = {"name": lookup}
	try:
		names = fields if isinstance(fields, list | tuple) else [fields]
		named = any(
			not isinstance(n, str) or n.strip() == "*" or hidden.names(n, strict=False) for n in names
		)
		# A Single's filters are compared with its stored values (database.py 760-765), never looked
		# up in a child table.
		named = named or hidden.in_filters(
			lookup if isinstance(lookup, dict | list | tuple) else None, child_names=False
		)
	except _Unknown:
		named = True
	if named:
		_refuse()
	return hidden


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def client_get_single_value(doctype, field):
	"""``frappe.client.get_single_value``, refused for a field the caller cannot read."""
	from frappe import client

	hidden = _hidden_for(doctype)
	if hidden is not None and (not isinstance(field, str) or hidden.names(field, strict=False)):
		_refuse()
	return client.get_single_value(doctype, field)


# ---------------------------------------------------------------------------
# Search, next-record, number cards and dashboard charts
# ---------------------------------------------------------------------------


def _takes_custom_query(doctype, query):
	"""Whether frappe's link search hands this search to a query function, as frappe decides it.

	frappe 16.56.0, ``frappe/desk/search.py``: ``search_link`` passes ``query`` and ``filters`` to
	``search_widget`` unchanged (52-62). ``search_widget`` reads the ``standard_queries`` hook
	(125), takes the doctype's registered query when the request names none (127-128: ``if not query
	and doctype in standard_queries``), and takes the custom path ``if query:`` (133), where
	``filters`` is passed to that function as its argument (152-164). frappe itself registers one for
	User (frappe/hooks.py 180). ``get_hooks(name)`` gives the same mapping as
	``get_hooks().standard_queries``, or an empty list when no app declares it.
	"""
	if query:
		return True
	# A doctype that is not a string is frappe's to refuse, with its own type error.
	return isinstance(doctype, str) and doctype in (frappe.get_hooks("standard_queries") or {})


def _search_filters(doctype, filters):
	"""``filters`` of a search with no custom query, as frappe hands them to its query engine.

	A JSON string is decoded (search.py 116-117); a dict becomes ``[doctype, key, operator, value]``
	rows, ``include_disabled`` taken out first (190-196, ``make_filter_tuple`` in frappe/utils/data.py
	2446-2451). Only the doctype and the key of each row are read here. A list goes as given.
	"""
	filters = _parsed(filters)
	if isinstance(filters, dict):
		return [[doctype, key, "=", value] for key, value in filters.items() if key != "include_disabled"]
	return filters


def _check_search(doctype, query, filters, searchfield):
	"""A link search: ``searchfield`` is a field reference on both of frappe's paths. With a custom
	query, ``filters`` are that query's arguments and are not read as field names; without one they
	reach frappe's query engine and are checked as it reads them."""
	check_request(
		doctype,
		child_names=True,
		filters=None if _takes_custom_query(doctype, query) else _search_filters(doctype, filters),
		references=[searchfield] if searchfield else (),
	)


def _as_int(value):
	"""A request value as frappe's ``int`` annotation would read it: ``"20"`` is 20, junk is 0."""
	try:
		return int(value or 0)
	except (TypeError, ValueError):
		return 0


def _as_flag(value):
	"""A request value as frappe's ``bool`` annotation would read it. The Desk sends ``1``/``0``,
	and a request carries them as text, where ``"0"`` is truthy."""
	if isinstance(value, str):
		return value.strip().lower() in ("1", "true", "yes", "on")
	return bool(value)


def _declared(fn, **optional):
	"""The part of ``optional`` that frappe's ``fn`` names in its signature; none when the signature
	cannot be read.

	frappe 16.60 added ``start``, ``include_image`` and ``keep_order`` to ``search_link``, and
	``keep_order`` to ``search_widget``; 16.56 has none of them. ``frappe.call`` passes a whitelisted
	function only the names it declares, so a wrapper that does not declare them silently drops the
	Link dropdown's paging, images and order; one that passes them to a frappe that does not know them
	raises ``TypeError`` on every Link search. Declaring them here and forwarding only what the
	installed frappe names works on both.

	``inspect.signature`` follows ``__wrapped__`` through both of frappe's layers, ``http_cache`` and
	the whitelist's argument check, which both use ``functools.wraps``. A catch-all ``**kwargs`` is
	deliberately not taken as acceptance: frappe's own functions never declare one, so seeing it means
	a decorator without ``wraps`` is hiding the real parameters, and guessing there would bring the
	``TypeError`` back. Dropping an argument costs paging; passing one costs the search.
	"""
	try:
		params = inspect.signature(fn).parameters
	except (TypeError, ValueError):
		return {}
	return {
		name: value
		for name, value in optional.items()
		if name in params and params[name].kind is not inspect.Parameter.VAR_KEYWORD
	}


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def search_link(
	doctype,
	txt,
	query=None,
	filters=None,
	page_length=10,
	searchfield=None,
	reference_doctype=None,
	ignore_user_permissions=False,
	*,
	link_fieldname=None,
	start=0,
	include_image=False,
	keep_order=False,
):
	"""``frappe.desk.search.search_link``: its search field, and its filters unless a custom query
	takes them (``_check_search``). ``start``, ``include_image`` and ``keep_order`` are frappe
	16.60's, forwarded only to a frappe that declares them (``_declared``)."""
	from frappe.desk import search

	_check_search(doctype, query, filters, searchfield)
	return search.search_link(
		doctype=doctype,
		txt=txt,
		query=query,
		filters=filters,
		page_length=page_length,
		searchfield=searchfield,
		reference_doctype=reference_doctype,
		ignore_user_permissions=ignore_user_permissions,
		link_fieldname=link_fieldname,
		**_declared(
			search.search_link,
			start=_as_int(start),
			include_image=_as_flag(include_image),
			keep_order=_as_flag(keep_order),
		),
	)


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def search_widget(
	doctype,
	txt,
	query=None,
	searchfield=None,
	start=0,
	page_length=10,
	filters=None,
	filter_fields=None,
	as_dict=False,
	reference_doctype=None,
	ignore_user_permissions=False,
	*,
	link_fieldname=None,
	for_link_validation=False,
	query_filters_as_dict=False,
	keep_order=False,
):
	"""``frappe.desk.search.search_widget``: its search field, and its filters unless a custom query
	takes them (``_check_search``). ``keep_order`` is frappe 16.60's, forwarded only to a frappe that
	declares it (``_declared``)."""
	from frappe.desk import search

	_check_search(doctype, query, filters, searchfield)
	return search.search_widget(
		doctype,
		txt,
		query,
		searchfield,
		start,
		page_length,
		filters,
		filter_fields,
		as_dict,
		reference_doctype,
		ignore_user_permissions,
		link_fieldname=link_fieldname,
		for_link_validation=for_link_validation,
		query_filters_as_dict=query_filters_as_dict,
		**_declared(search.search_widget, keep_order=_as_flag(keep_order)),
	)


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def form_get_next(doctype, value, prev, filters=None, sort_order="desc", sort_field="creation"):
	"""``frappe.desk.form.utils.get_next``: its filters and sort field checked."""
	from frappe.desk.form import utils

	# The filters go to frappe.qb.get_query with permissions applied (utils.py 119-120); the sort
	# field is a column of the doctype's own table, used as written (101-105), so it is matched
	# here as MariaDB matches it: in any case.
	check_request(doctype, child_names=True, filters=filters, references=[sort_field])
	return utils.get_next(doctype, value, prev, filters, sort_order, sort_field)


def _card_aggregate(card):
	"""The field a Number Card totals: none for a Count card. frappe counts ``*`` for one and never
	reads ``aggregate_function_based_on`` (number_card.py 140-145), which the form only hides, so a
	card switched to Count keeps the name of the field it used to total."""
	if card.get("function") == "Count":
		return []
	return [card.get("aggregate_function_based_on")]


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def number_card_get_result(doc, filters, to_date=None):
	"""Number Card ``get_result``: the card's aggregate field and the filters checked."""
	from frappe.desk.doctype.number_card import number_card

	card = _parsed(doc)
	if isinstance(card, dict):
		check_request(
			card.get("document_type"),
			card.get("parent_document_type"),
			child_names=True,
			filters=filters,
			expressions=_card_aggregate(card),
		)
	return number_card.get_result(doc, filters, to_date)


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def number_card_get_percentage_difference(doc, filters, result):
	"""Number Card ``get_percentage_difference``: the stored card's aggregate field and the filters
	checked (frappe reads the card by name and runs ``get_result`` with these filters)."""
	from frappe.desk.doctype.number_card import number_card

	card = _parsed(doc)
	name = card.get("name") if isinstance(card, dict) else None
	stored = (
		frappe.db.get_value(
			"Number Card",
			name,
			["document_type", "parent_document_type", "function", "aggregate_function_based_on"],
			as_dict=True,
		)
		if name
		else None
	)
	if stored:
		check_request(
			stored.get("document_type"),
			stored.get("parent_document_type"),
			child_names=True,
			filters=filters,
			expressions=_card_aggregate(stored),
		)
	return number_card.get_percentage_difference(doc, filters, result)


_CHART_FIELDS = (
	"name",
	"document_type",
	"parent_document_type",
	"chart_type",
	"based_on",
	"value_based_on",
	"group_by_based_on",
	"aggregate_function_based_on",
	"filters_json",
)


def _check_chart(config, filters):
	"""One chart's own fields, its stored filters and the request's filters."""
	if not isinstance(config, dict):
		return
	if config.get("chart_type") == "Group By":
		references = [config.get("group_by_based_on")]
		expressions = [config.get("aggregate_function_based_on")]
	else:
		references = [config.get("based_on")]
		expressions = [config.get("value_based_on")]
	# frappe draws the chart with the request's filters, or with the chart's own when there are none
	# (dashboard_chart.py 125), so both are checked; or_filters is only the second slot here.
	check_request(
		config.get("document_type"),
		config.get("parent_document_type"),
		child_names=True,
		filters=filters,
		or_filters=config.get("filters_json"),
		references=[ref for ref in references if ref],
		expressions=expressions,
	)


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def dashboard_chart_get(
	chart_name=None,
	chart=None,
	no_cache=None,
	filters=None,
	from_date=None,
	to_date=None,
	timespan=None,
	time_interval=None,
	heatmap_year=None,
	refresh=None,
):
	"""Dashboard Chart ``get``: the chart's fields and filters checked, for the chart frappe draws.

	frappe's ``cache_source`` answers from a cache keyed on the chart's name, so the stored chart of
	that name is checked as well as a chart passed in.
	"""
	from frappe.desk.doctype.dashboard_chart import dashboard_chart

	stored_name = chart_name
	if not stored_name and not no_cache:
		passed = _parsed(chart)
		stored_name = passed.get("name") if isinstance(passed, dict) else None
	if not chart_name:
		_check_chart(_parsed(chart), filters)
	if stored_name:
		stored = frappe.db.get_value("Dashboard Chart", stored_name, list(_CHART_FIELDS), as_dict=True)
		if stored:
			_check_chart(stored, filters)
	return dashboard_chart.get(
		chart_name=chart_name,
		chart=chart,
		no_cache=no_cache,
		filters=filters,
		from_date=from_date,
		to_date=to_date,
		timespan=timespan,
		time_interval=time_interval,
		heatmap_year=heatmap_year,
		refresh=refresh,
	)


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def calendar_get_events(doctype, start, end, field_map, filters=None, fields=None, order_by=None):
	"""``frappe.desk.calendar.get_events``: its filters, sort and fields, and the two date fields its
	``field_map`` names.

	frappe lists the events with ``frappe.get_list`` and compares those two fields with the dates
	asked for as columns of the doctype's own table (calendar.py 62-77), a form its query engine
	takes as written, so they are field references here like a sort field.
	"""
	from frappe.desk import calendar

	mapped = _parsed(field_map)
	references = [mapped.get("start"), mapped.get("end")] if isinstance(mapped, dict) else [None]
	check_request(
		doctype,
		child_names=True,
		filters=filters,
		order_by=order_by,
		fields=fields,
		references=references,
	)
	return calendar.get_events(
		doctype=doctype,
		start=start,
		end=end,
		field_map=field_map,
		filters=filters,
		fields=fields,
		order_by=order_by,
	)


# ---------------------------------------------------------------------------
# REST list routes (after_request)
# ---------------------------------------------------------------------------


def _refusal_body(v2):
	message = frappe._(REFUSAL)
	if v2:
		return json.dumps({"errors": [{"type": "PermissionError", "message": message}]})
	return json.dumps({"exc_type": "PermissionError", "message": message})


def refuse_rest_list_references(response=None, request=None):
	"""``after_request``: a REST list or count request that names a field the caller cannot read
	gets a 403 in place of its body.

	``GET /api/resource/<doctype>`` (and ``/api/v1/...``) reads ``filters``, ``or_filters``,
	``order_by``, ``group_by``, ``fields`` and ``parent`` through ``frappe.client.get_list``;
	``GET /api/v2/document/<doctype>`` reads ``filters``, ``order_by``, ``group_by`` and ``fields``;
	``GET /api/v2/doctype/<doctype>/count`` is the report view's count. A ``HEAD`` request runs the
	same handlers (a werkzeug rule for GET answers HEAD too) and its status and length say as much as
	a body, so it is checked the same way. Every other request leaves on the method and path checks.
	Never raises: frappe logs and swallows an ``after_request`` exception and would send the body, so
	a check that fails replaces the body too.
	"""
	try:
		if request is None or response is None or request.method not in ("GET", "HEAD"):
			return
		path = request.path or ""
		v1 = _REST_V1_LIST.match(path)
		v2_list = None if v1 else _REST_V2_LIST.match(path)
		v2_count = None if (v1 or v2_list) else _REST_V2_COUNT.match(path)
		match = v1 or v2_list or v2_count
		if not match or response.status_code >= 300:
			return
	except Exception:
		return

	is_v2 = not v1
	try:
		doctype = match.group(1)
		form = frappe.form_dict or {}
		if v1:
			check_request(
				doctype,
				form.get("parent"),
				child_names=True,
				filters=form.get("filters"),
				or_filters=form.get("or_filters"),
				order_by=form.get("order_by"),
				group_by=form.get("group_by"),
				fields=form.get("fields"),
			)
		elif v2_list:
			# Handed to the query engine itself (api/v2.py document_list), not to frappe.get_list.
			check_request(
				doctype,
				child_names=True,
				filters=form.get("filters"),
				order_by=form.get("order_by"),
				group_by=form.get("group_by"),
				fields=form.get("fields"),
				swap=False,
			)
		else:
			_check_report_form(form, doctype)
		return
	except frappe.PermissionError:
		pass
	except Exception:
		_log_failure("REST list field check")
	try:
		response.status_code = 403
		response.set_data(_refusal_body(is_v2))
	except Exception:
		_log_failure("REST list refusal")
