"""Field references in read requests, held to the caller's field-level read (v1.835.5).

``fieldlevel_read.py`` keeps the fields a caller cannot read at their permlevel out of the documents
the app sends. This module applies the same rule to the *field names a request carries*: a list,
count, value, search or chart request that names such a field in ``filters``, ``or_filters``,
``order_by``, ``group_by``, an aggregate or another ``fields`` expression is refused with a plain
permission message. A field of a child table answers to the parent doctype's levels, as in
``fieldlevel_read``. Nothing else changes: a caller who can read every field of the doctype (and
of its child tables) leaves on the first check, Administrator included, and server-side code never
passes through here.

**What frappe 16.56.0 already enforces, and what this adds.** ``frappe.get_list`` builds its query
with ``frappe.model.qb_query.DatabaseQuery`` (``frappe/__init__.py`` 1376-1378), which hands it to
``frappe.database.query.Engine.get_query``. With permissions applied, the Engine checks a field named
in a filter (``_validate_and_prepare_filter_field``, query.py 863-1006), in ``order_by`` and
``group_by`` (``_validate_and_parse_field_for_clause``, 1315-1378) and inside an aggregate
(``SQLFunctionParser._validate_string_argument``, 2481-2557) against the caller's readable levels,
and it refuses a SQL function written as a string in ``fields`` (2257-2282). Its check
(``_check_field_permission``, 1020-1057) reads a child table's levels from the parent where the
parent is passed to it. This module applies the parent's levels in every position, including
`` `tabChild`.`field` `` notation and a child table read through its parent, and applies the same
rule in four routes that do not go through the Engine for the names they are given:
``frappe.client.get`` with ``filters``
(client.py 109-110, then ``Document.load_from_db``, document.py 524-530), ``frappe.client.get_value``
on a Single (client.py 153-154, ``Database.get_values_from_single``, database.py 726-786),
``frappe.client.get_single_value`` (client.py 175-180) and ``frappe.desk.form.utils.get_next``,
whose ``sort_field`` is read and sorted on outside the Engine (utils.py 98-123).

A plain name in ``fields`` is left to frappe, which drops a field the caller cannot read from what
it selects (query.py 1447-1531; ``reportview.validate_fields``, reportview.py 151-181).

**How each route is reached.** The RPC routes are whitelisted methods, wrapped here through
``override_whitelisted_methods`` exactly as in ``fieldlevel_read``: each wrapper restates frappe's
parameter list and HTTP methods (``tests/test_fieldlevel_filters.py`` pins both against 16.56.0),
checks the request, and then calls frappe's own function unchanged. ``seal_filter_originals``, a
``before_request`` hook, takes frappe's own copies off the whitelist once their canonical names are
overridden, so no other dotted name for the same function (``frappe.client.search_widget`` is one)
answers HTTP. The REST list routes (``GET /api/resource/<doctype>``, ``/api/v2/document/<doctype>``
and ``/api/v2/doctype/<doctype>/count``) are werkzeug routes no override reaches, and frappe runs
``before_request`` hooks before it reads an API key or token (app.py 114-116 and 219-220), so they
are checked in ``after_request``, where the session user is the caller: a refused request's body is
replaced by a 403 before anything is sent.

**Unknown shapes are refused.** For a caller with hidden fields on the doctype, a field reference
this module cannot read as one of frappe's forms (``field``, `` `field` ``,
`` `tabDocType`.`field` ``, ``table_or_link_field.field``) is refused rather than passed, except where
frappe itself reads a bare value as a document name (a top-level filter list of plain values, or a
string filter). Those two positions are only refused when the value reads as a hidden field.
"""

import json
import re

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
_SIMPLE = re.compile(r"^\w+$")
_DOTTED = re.compile(r"^(\w+)\.(\w+)$")
_QUOTED = re.compile(r"^(['\"]).*\1$", re.S)
_DIRECTION = re.compile(r"\s+(?:asc|desc)$", re.I)

#: frappe's default: frappe.whitelist() with no methods answers these four.
ANY_METHOD = ["GET", "POST", "PUT", "DELETE"]

REFUSAL = "You do not have permission to filter, sort, group or total by a field you cannot read."

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
)

#: The REST list routes: v1 is mounted at both /api and /api/v1.
_REST_V1_LIST = re.compile(r"^/api/(?:v1/)?resource/([^/]+)/?$")
_REST_V2_LIST = re.compile(r"^/api/v2/document/([^/]+)/?$")
_REST_V2_COUNT = re.compile(r"^/api/v2/doctype/([^/]+)/count/?$")

_SEALED = set()
_seal_failure_logged = False


class _Unknown(Exception):
	"""A field reference in a shape this module does not read: refused for a caller with hidden fields."""


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


def _is_scalar(value):
	return value is None or isinstance(value, str | int | float | bool)


class _Hidden:
	"""The fields of one doctype, and of its child tables, that the session user may not read."""

	def __init__(self, doctype, meta, levels):
		self.doctype = doctype
		self.fields = {df.fieldname: df for df in meta.fields}
		self.hidden = {df.fieldname for df in meta.fields if not _is_readable(df, levels)}
		#: table fieldname -> child doctype; child doctype -> its hidden fieldnames. A table field the
		#: caller cannot read hides every field of its child doctype.
		self.tables = {}
		self.children = {}
		for df in meta.fields:
			if df.fieldtype not in _TABLE_FIELDTYPES or not df.options:
				continue
			child_fields = frappe.get_meta(df.options).fields
			if _is_readable(df, levels):
				hidden = {cdf.fieldname for cdf in child_fields if not _is_readable(cdf, levels)}
			else:
				hidden = {cdf.fieldname for cdf in child_fields}
			self.tables[df.fieldname] = df.options
			self.children.setdefault(df.options, set()).update(hidden)

	# -- one field reference --------------------------------------------

	def names(self, ref, doctype=None, strict=True):
		"""Whether ``ref``, one field reference in one of frappe's forms, names a hidden field.

		``doctype`` is the first element of a ``[doctype, field, operator, value]`` filter. A reference
		in no form frappe reads raises ``_Unknown`` when ``strict``, and is not hidden otherwise.
		"""
		if not isinstance(ref, str):
			if strict:
				raise _Unknown(ref)
			return False
		ref = ref.strip()
		match = _BACKTICK_REF.match(ref)
		if match:
			return self._in_doctype(match.group(1), match.group(3))
		if len(ref) > 2 and ref[0] == ref[-1] == "`" and _SIMPLE.match(ref[1:-1]):
			ref = ref[1:-1]
		if _SIMPLE.match(ref):
			if doctype and doctype != self.doctype:
				if doctype in self.children:
					return ref in self.children[doctype]
				if not strict:
					return False
				return self._in_doctype(doctype, ref)
			return self._own_or_child(ref)
		match = _DOTTED.match(ref)
		if match:
			table, fieldname = match.groups()
			if table in self.tables:
				return fieldname in self.children[self.tables[table]]
			if table in self.fields:
				# A Link: its own level here. The field it points at is frappe's check, made against
				# the linked doctype's own permissions.
				return table in self.hidden
		if strict:
			raise _Unknown(ref)
		return False

	def _own_or_child(self, fieldname):
		if fieldname in self.fields:
			return fieldname in self.hidden
		# frappe looks a name its doctype lacks up in the child tables (query.py 977-1002).
		return any(fieldname in hidden for hidden in self.children.values())

	def _in_doctype(self, doctype, fieldname):
		if doctype == self.doctype:
			return fieldname in self.hidden
		if doctype in self.children:
			return fieldname in self.children[doctype]
		if getattr(frappe.get_meta(doctype), "istable", False):
			# A child table of some other doctype: not one this request can be judged against.
			raise _Unknown(doctype)
		# Any other doctype: frappe checks it against that doctype's own permissions.
		return False

	# -- the positions a request names fields in ---------------------------

	def in_filters(self, filters):
		"""``filters`` / ``or_filters`` in every shape frappe's Engine accepts."""
		if filters is None or filters == "":
			return False
		if isinstance(filters, str):
			try:
				parsed = json.loads(filters)
			except ValueError:
				return False  # a document name: frappe reads a bare value as {"name": value}
			if isinstance(parsed, str):
				return False
			return self.in_filters(parsed)
		if isinstance(filters, int | float | bool):
			return False
		if isinstance(filters, dict):
			return any(self.names(key) for key in filters)
		if isinstance(filters, list | tuple):
			if not filters:
				return False
			if not isinstance(filters[0], list | tuple | dict):
				return self._flat(filters)
			return any(self._condition(item) for item in filters)
		raise _Unknown(type(filters).__name__)

	def _flat(self, items):
		"""A top-level list that starts with a plain value: one condition, or a list of names.

		``[field, operator, value]`` and ``[doctype, field, operator, value(, x)]`` are conditions;
		anything else is a list of document names. A name is never refused for its shape, only when it
		reads as a hidden field.
		"""
		if len(items) == 3:
			return self.names(items[0], strict=False)
		if len(items) in (4, 5):
			doctype = items[0] if isinstance(items[0], str) else None
			return self.names(items[1], doctype=doctype, strict=False)
		return False

	def _condition(self, item):
		"""One element of a list of conditions, or of a nested ``[cond, "and"|"or", cond]`` group."""
		if isinstance(item, str):
			if item.strip().lower() in ("and", "or"):
				return False
			raise _Unknown(item)
		if isinstance(item, dict):
			return self.in_filters(item)
		if isinstance(item, list | tuple):
			if not item:
				raise _Unknown(item)
			if isinstance(item[0], list | tuple | dict):
				return any(self._condition(part) for part in item)
			if len(item) in (2, 3):
				return self.names(item[0])
			if len(item) in (4, 5):
				doctype = item[0] if isinstance(item[0], str) else None
				return self.names(item[1], doctype=doctype)
		raise _Unknown(item)

	def in_clause(self, clause):
		"""``order_by`` / ``group_by``: comma-separated references, each with an optional direction."""
		if clause is None or clause == "":
			return False
		if not isinstance(clause, str):
			raise _Unknown(clause)
		for part in clause.split(","):
			part = _DIRECTION.sub("", part.strip()).strip()
			if not part or part.isdigit():
				continue
			if self.names(part):
				return True
		return False

	def in_fields(self, fields):
		"""Expressions in ``fields``: every argument of a function or operator dict, at any depth."""
		if fields is None or fields == "":
			return False
		if isinstance(fields, str):
			try:
				fields = json.loads(fields)
			except ValueError:
				return False  # plain names, comma-separated: frappe drops what the caller cannot read
		if isinstance(fields, dict):
			fields = [fields]
		if not isinstance(fields, list | tuple):
			return False
		return any(self.in_expression(field) for field in fields if isinstance(field, dict))

	def in_expression(self, value):
		if isinstance(value, dict):
			return any(
				self.in_expression(part)
				for key, part in value.items()
				if not (isinstance(key, str) and key.strip().lower() == "as")
			)
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
	levels = _readable_levels(doctype, parent if isinstance(parent, str) else None)
	if levels is None:
		return None
	meta = frappe.get_meta(doctype)
	if not _hides_anything(meta, levels):
		return None
	return _Hidden(doctype, meta, levels)


def _refuse():
	frappe.throw(frappe._(REFUSAL), frappe.PermissionError, title=frappe._("Not Permitted"))


def check_request(
	doctype,
	parent=None,
	*,
	filters=None,
	or_filters=None,
	order_by=None,
	group_by=None,
	fields=None,
	references=(),
	expressions=(),
):
	"""Refuse a request on ``doctype`` that names a field the session user cannot read.

	``references`` are single field references in filter, sort or group position (strict);
	``expressions`` are aggregate arguments, read as ``fields`` expressions are.
	"""
	hidden = _hidden_for(doctype, parent)
	if hidden is None:
		return
	try:
		named = (
			hidden.in_filters(filters)
			or hidden.in_filters(or_filters)
			or hidden.in_clause(order_by)
			or hidden.in_clause(group_by)
			or hidden.in_fields(fields)
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

	check_request(doctype, filters=filters, references=_stats_references(stats))
	return reportview.get_sidebar_stats(stats, doctype, filters)


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def reportview_get_stats(stats, doctype, filters=None):
	"""``frappe.desk.reportview.get_stats``: its filters and grouped columns checked."""
	from frappe.desk import reportview

	check_request(doctype, filters=filters, references=_stats_references(stats))
	return reportview.get_stats(stats, doctype, filters)


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def reportview_get_filter_dashboard_data(stats, doctype, filters=None):
	"""``frappe.desk.reportview.get_filter_dashboard_data``: its filters and grouped columns checked."""
	from frappe.desk import reportview

	check_request(doctype, filters=filters, references=_stats_references(stats, key="name"))
	return reportview.get_filter_dashboard_data(stats, doctype, filters)


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def listview_get_group_by_count(doctype, current_filters, field):
	"""``frappe.desk.listview.get_group_by_count``: its filters and grouped field checked."""
	from frappe.desk import listview

	check_request(doctype, filters=current_filters, references=[field])
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

	check_request(doctype, filters=filters)
	return client.get_count(doctype, filters, debug, cache)


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def client_get(doctype, name=None, filters=None, parent=None):
	"""``frappe.client.get``; a document looked up by ``filters`` has them checked first."""
	from frappe import client

	if not name and filters:
		check_request(doctype, parent, filters=filters)
	return client.get(doctype, name, filters, parent)


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def client_get_value(doctype, fieldname, filters=None, as_dict=True, debug=False, parent=None):
	"""``frappe.client.get_value``, its fields and filters checked.

	A Single is read straight from its stored values, past frappe's field-level read, so for a
	Single that hides fields from the caller every requested name and filter is checked, and the
	whole-record dict a lookup by filters returns (``get_values_from_single``) leaves without the
	fields the caller cannot read.
	"""
	from frappe import client

	single = _check_get_value(doctype, fieldname, filters, parent)
	result = client.get_value(doctype, fieldname, filters, as_dict, debug, parent)
	if single is not None and isinstance(result, dict):
		return {key: value for key, value in result.items() if key not in single.hidden}
	return result


def _check_get_value(doctype, fieldname, filters, parent):
	"""Refuse a hidden field named in the request. Returns the ``_Hidden`` of a Single that hides
	fields from the caller, whose answer is then scrubbed, else None."""
	hidden = _hidden_for(doctype, parent)
	if hidden is None:
		return None
	fields = _parsed(fieldname)
	if not getattr(frappe.get_meta(doctype), "issingle", False):
		check_request(doctype, parent, filters=filters, fields=fields)
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
		named = named or hidden.in_filters(lookup if isinstance(lookup, dict | list | tuple) else None)
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
):
	"""``frappe.desk.search.search_link``: its filters and search field checked."""
	from frappe.desk import search

	check_request(doctype, filters=filters, references=[searchfield] if searchfield else ())
	return search.search_link(
		doctype,
		txt,
		query,
		filters,
		page_length,
		searchfield,
		reference_doctype,
		ignore_user_permissions,
		link_fieldname=link_fieldname,
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
):
	"""``frappe.desk.search.search_widget``: its filters and search field checked."""
	from frappe.desk import search

	check_request(doctype, filters=filters, references=[searchfield] if searchfield else ())
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
	)


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def form_get_next(doctype, value, prev, filters=None, sort_order="desc", sort_field="creation"):
	"""``frappe.desk.form.utils.get_next``: its filters and sort field checked."""
	from frappe.desk.form import utils

	check_request(doctype, filters=filters, references=[sort_field])
	return utils.get_next(doctype, value, prev, filters, sort_order, sort_field)


@frappe.whitelist(methods=["GET", "POST", "PUT", "DELETE"])
def number_card_get_result(doc, filters, to_date=None):
	"""Number Card ``get_result``: the card's aggregate field and the filters checked."""
	from frappe.desk.doctype.number_card import number_card

	card = _parsed(doc)
	if isinstance(card, dict):
		check_request(
			card.get("document_type"),
			card.get("parent_document_type"),
			filters=filters,
			expressions=[card.get("aggregate_function_based_on")],
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
			["document_type", "parent_document_type", "aggregate_function_based_on"],
			as_dict=True,
		)
		if name
		else None
	)
	if stored:
		check_request(
			stored.get("document_type"),
			stored.get("parent_document_type"),
			filters=filters,
			expressions=[stored.get("aggregate_function_based_on")],
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
	``GET /api/v2/doctype/<doctype>/count`` is the report view's count. Every other request leaves
	on the method and path checks. Never raises: frappe logs and swallows an ``after_request``
	exception and would send the body, so a check that fails replaces the body too.
	"""
	try:
		if request is None or response is None or request.method != "GET":
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
				filters=form.get("filters"),
				or_filters=form.get("or_filters"),
				order_by=form.get("order_by"),
				group_by=form.get("group_by"),
				fields=form.get("fields"),
			)
		elif v2_list:
			check_request(
				doctype,
				filters=form.get("filters"),
				order_by=form.get("order_by"),
				group_by=form.get("group_by"),
				fields=form.get("fields"),
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
