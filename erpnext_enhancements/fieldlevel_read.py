"""Field-level read permissions on the responses Frappe v16 sends without them (v1.542.0).

ADR 0013 keeps pay behind permlevel 1. Frappe v16 honours a permlevel when it *reads* a
document out to a caller: ``getdoc``, ``frappe.client.get``, ``GET /api/resource`` and the
Desk's own save response all call ``Document.apply_fieldlevel_read_permissions()`` before
they serialise. Two kinds of response skip that step, and each hands back exactly the values
the permlevel exists to hide:

* **The form's version history.** ``getdoc`` strips the document and then attaches
  ``docinfo``, whose ``versions`` are ``frappe.get_all("Version", fields=[..., "data"])``:
  the stored diffs, verbatim (``frappe/desk/form/load.py``, ``get_versions``). A diff holds
  the old and new value of every changed field and the whole of every added or removed child
  row, with no permlevel filter (``frappe/core/doctype/version/version.py``, ``get_diff``).
  Employee and Job Interval both track changes, so the first pay-rate row HR adds, and every
  ``labor_cost`` a kiosk clock-out stamps, lands in a Version that every reader of the record
  then receives. The *rendered* timeline hides those fields (the browser filters by field
  display status, and v16's activity timeline by ``readable_permlevels``); the payload does
  not. ``savedocs``, ``cancel`` and ``discard`` send the same docinfo after every action.
* **Write responses.** ``frappe.client.set_value``, ``insert``, ``save``, ``submit`` and
  ``cancel`` return ``doc.as_dict()``; ``POST``/``PUT /api/resource`` and v2's
  ``POST /api/v2/document`` return the saved document; ``frappe.model.workflow.apply_workflow``
  returns its document. None of them strips it. So a caller with write but not level-1 read
  who saves an empty change (the permlevel reset restores the stored value) gets the stored
  level-1 values back: Projects User on Activity Cost's ``costing_rate``, any Timesheet writer
  on its cost fields, Projects Manager on an open Job Interval's pay block, a Maintenance User
  moving a Sapphire Maintenance Record through its workflow on ``total_labor_cost``.

**How each route is reached.** The RPC routes are whitelisted methods, so
``override_whitelisted_methods`` sends their canonical names here on every transport
(``/api/method``, v2's ``/api/v2/method`` and the legacy ``?cmd=``, all of which resolve through
``frappe.override_whitelisted_method``). Each wrapper here calls frappe's own function,
unchanged, and only then scrubs what it produced. Parameter lists mirror frappe's exactly,
because ``frappe.call`` matches the request against *this* signature; annotations are
deliberately not copied (as in ``po_pdf_filename.py``), since the values still land on
frappe's own typed function.

**An override matches a name; the whitelist matches a function.** A module that does
``from frappe.client import set_value`` holds frappe's very function under a second dotted name,
and frappe's whitelist check (``is_whitelisted``) passes it, because it tests the function
object. The override lookup is an exact string match, so it misses it. frappe v16 and ERPNext
v16 hold twelve such aliases between them. Ten are in test modules, which ship with the apps
and can be imported on a production bench. They include ``frappe.email.inbox.set_value``,
``frappe.workflow.doctype.workflow_action.workflow_action.apply_workflow`` (also reached as
``/api/v2/method/Workflow Action/apply_workflow``) and ``getdoc`` in five test modules, among
others. A list of names would go stale on the next frappe upgrade. So ``seal_wrapped_originals``,
a ``before_request`` hook, takes frappe's own copies of the eleven functions off the whitelist
once their canonical names are overridden. Every alias, present or future, then fails the
whitelist check, while the canonical name still reaches the wrapper here, which is whitelisted
itself, and in-process Python calls are unaffected because only HTTP dispatch checks the
whitelist.

``/api/resource`` and ``/api/v2/document`` are werkzeug routes, not whitelisted methods, and no
override can reach them, so an ``after_request`` hook scrubs their JSON body instead.

**The rule is frappe's own** (``apply_fieldlevel_read_permissions``). Administrator sees
everything. A field at permlevel 0 is never touched. Any other field is kept only when one of
the caller's roles has ``read`` at that level on the doctype, and a child table answers to its
parent's rows. Stored Versions are never modified: the audit trail stays whole for the System
Manager, who reads the Version doctype directly.

**It fails closed, and never takes the request down with it.** A version history that cannot be
scrubbed is sent empty (the timeline loses its edit history for that load, never the pay). A
write response that cannot be scrubbed is cut to its ``doctype`` and ``name`` (the write itself
has already happened and is not rolled back). Either failure is logged without frame locals,
through ``defer_insert``: a form load is a ``GET``, whose transaction frappe rolls back, and
the REST hook runs after the commit, so an ordinary insert would be lost on both paths. The
seal is the one part that fails open. If it cannot run, the aliases stay callable, which is
frappe's stock behaviour, and the failure is logged once per process rather than on every
request.

``tests/test_fieldlevel_read.py`` pins the wiring against frappe v16's signatures and HTTP
methods, and the scrub on the Employee and Job Interval shapes.
"""

import json
import re

import frappe

#: The standard fields ``get_diff`` records beside the doctype's own. Neither has a permlevel.
_DIFF_STANDARD_FIELDS = frozenset(("name", "docstatus"))

#: frappe.model.table_fields, restated so this module imports nothing past ``frappe`` itself.
_TABLE_FIELDTYPES = frozenset(("Table", "Table MultiSelect"))

#: The REST write routes. v1 is mounted at both ``/api`` and ``/api/v1``. v2's ``PUT`` already
#: strips, but a second strip removes nothing, so it is matched too rather than special-cased.
_REST_WRITE_PATH = re.compile(r"^/api/(?:v1/)?resource/[^/]+|^/api/v2/document/[^/]+")
_REST_WRITE_METHODS = frozenset(("POST", "PUT"))

#: frappe's functions that the wrappers below replace, by canonical dotted path: the
#: override_whitelisted_methods keys in hooks.py. tests/test_fieldlevel_read.py holds the two equal.
WRAPPED_ORIGINALS = (
	"frappe.desk.form.load.getdoc",
	"frappe.desk.form.load.get_docinfo",
	"frappe.desk.form.save.savedocs",
	"frappe.desk.form.save.cancel",
	"frappe.desk.form.save.discard",
	"frappe.client.set_value",
	"frappe.client.insert",
	"frappe.client.save",
	"frappe.client.submit",
	"frappe.client.cancel",
	"frappe.model.workflow.apply_workflow",
)

#: The originals this process has taken off frappe's whitelist, so that they go back on for a
#: site that does not override them (a bench can serve more than one site per process).
_SEALED = set()
_seal_failure_logged = False


# ---------------------------------------------------------------------------
# The scrub
# ---------------------------------------------------------------------------


def _level(df):
	return int(getattr(df, "permlevel", 0) or 0)


def _readable_levels(doctype, parenttype=None):
	"""The permlevels the session user reads on ``doctype``, or None when nothing is hidden.

	``Document.get_permlevel_access("read")`` without needing a document. A child table has no
	DocPerms of its own and answers to its parent's; one with no parent named gets none, so only
	permlevel 0 survives on it.
	"""
	if frappe.session.user == "Administrator":
		return None
	meta = frappe.get_meta(doctype)
	if getattr(meta, "istable", False):
		if not parenttype:
			return set()
		meta = frappe.get_meta(parenttype)
	roles = set(frappe.get_roles())
	return {
		int(getattr(perm, "permlevel", 0) or 0)
		for perm in meta.permissions or []
		if getattr(perm, "role", None) in roles and perm.get("read")
	}


def _is_readable(df, levels):
	return df is not None and (not _level(df) or _level(df) in levels)


def _strip_row(row, meta, levels):
	"""Delete from ``row`` every field of ``meta`` at an unreadable level. True if any went."""
	removed = False
	for df in meta.fields:
		if df.fieldname not in row:
			continue
		if not _is_readable(df, levels):
			del row[df.fieldname]
			removed = True
		elif df.fieldtype in _TABLE_FIELDTYPES and isinstance(row[df.fieldname], list):
			child_meta = frappe.get_meta(df.options)
			for child in row[df.fieldname]:
				if isinstance(child, dict) and _strip_row(child, child_meta, levels):
					removed = True
	return removed


def scrub_doc_dict(doc):
	"""Drop from ``doc``, a serialised document, every field the session user may not read.

	In place, and returned. True to frappe's rule: permlevel 0 is never touched, child rows
	answer to the document's levels, and a dict that names no doctype is left alone.
	"""
	_scrub_doc_dict(doc)
	return doc


def _scrub_doc_dict(doc):
	if not isinstance(doc, dict) or not doc.get("doctype"):
		return False
	levels = _readable_levels(doc["doctype"], doc.get("parenttype"))
	if levels is None:
		return False
	meta = frappe.get_meta(doc["doctype"])
	if not _hides_anything(meta, levels):
		return False
	return _strip_row(doc, meta, levels)


def _hides_anything(meta, levels):
	"""Whether any field of ``meta``, or of one of its child tables, is at an unreadable level."""
	for df in meta.fields:
		if not _is_readable(df, levels):
			return True
		if df.fieldtype in _TABLE_FIELDTYPES and df.options:
			if any(not _is_readable(cdf, levels) for cdf in frappe.get_meta(df.options).fields):
				return True
	return False


def scrub_versions(doctype, versions):
	"""Drop, from each Version's ``data``, the changes to fields the session user may not read.

	``versions`` are the rows ``get_versions`` returns (``data`` is the stored JSON diff); each
	``data`` is replaced, never the stored Version. A diff that is not a JSON object cannot be
	scrubbed and is sent as ``{}``, which the timeline renders as nothing.
	"""
	levels = _readable_levels(doctype)
	if levels is None:
		return versions
	meta = frappe.get_meta(doctype)
	if not _hides_anything(meta, levels):
		return versions
	for version in versions:
		raw = version.get("data")
		if not raw:
			continue
		data = json.loads(raw)
		if isinstance(data, dict):
			_scrub_diff(data, meta, levels)
		else:
			data = {}
		version["data"] = json.dumps(data, separators=(",", ":"))
	return versions


def _scrub_diff(data, meta, levels):
	"""``get_diff``'s shape: changed, added, removed and row_changed, each filtered in place.

	A ``changed`` entry for a field the doctype no longer has is dropped: its level is unknown,
	and the timeline would not render it anyway.
	"""
	fields = {df.fieldname: df for df in meta.fields}

	if data.get("changed"):
		data["changed"] = [
			change
			for change in data["changed"]
			if change and (change[0] in _DIFF_STANDARD_FIELDS or _is_readable(fields.get(change[0]), levels))
		]

	for key in ("added", "removed"):
		if not data.get(key):
			continue
		kept = []
		for entry in data[key]:
			# [table_fieldname, the whole child row as a dict]
			table_df = fields.get(entry[0]) if entry else None
			if not _is_readable(table_df, levels):
				continue
			if len(entry) > 1 and isinstance(entry[1], dict) and table_df.options:
				_strip_row(entry[1], frappe.get_meta(table_df.options), levels)
			kept.append(entry)
		data[key] = kept

	if data.get("row_changed"):
		kept = []
		for entry in data["row_changed"]:
			# (table_fieldname, row_index, row_name, [[fieldname, old, new], ...]): get_diff's
			# order, whatever its docstring says. Only the first and last are read here.
			table_df = fields.get(entry[0]) if entry else None
			if not _is_readable(table_df, levels) or len(entry) < 4 or not table_df.options:
				continue
			child_fields = {df.fieldname: df for df in frappe.get_meta(table_df.options).fields}
			changes = [c for c in entry[3] or [] if c and _is_readable(child_fields.get(c[0]), levels)]
			if changes:
				kept.append([entry[0], entry[1], entry[2], changes])
		data["row_changed"] = kept


# ---------------------------------------------------------------------------
# Failure handling: closed, logged, never raised
# ---------------------------------------------------------------------------


def _log_failure(what):
	try:
		# get_traceback() without context: frame locals here would be the values being hidden.
		# defer_insert: a GET's transaction is rolled back and after_request runs past the commit,
		# so an ordinary insert would vanish on exactly the paths this logs from.
		frappe.log_error(
			title=f"Field-level read scrub failed: {what}",
			message=frappe.get_traceback(),
			defer_insert=True,
		)
	except Exception:
		pass


# ---------------------------------------------------------------------------
# The seal: frappe's own copies stop answering HTTP under any other name
# ---------------------------------------------------------------------------


def seal_wrapped_originals():
	"""``before_request``: take frappe's copies of the wrapped functions off its whitelist.

	Only an original whose canonical name this site overrides is sealed, so the canonical route
	keeps working (it resolves to the wrapper here, never to the original). One that is not
	overridden, or no longer is, goes back on. Cheap: eleven cached lookups and set operations.
	Never raises, because a before_request hook that raises fails every request on the site.
	"""
	global _seal_failure_logged
	try:
		whitelisted = frappe.whitelisted
		for path in WRAPPED_ORIGINALS:
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
			_log_failure("sealing frappe's originals")


def _scrub_docinfo():
	"""Scrub ``frappe.response.docinfo.versions`` in place. On any failure, send none."""
	docinfo = frappe.response.get("docinfo")
	if not docinfo or not docinfo.get("versions"):
		return
	try:
		scrub_versions(docinfo.get("doctype"), docinfo["versions"])
	except Exception:
		docinfo["versions"] = []
		_log_failure("version history")


def _scrubbed(doc):
	"""``doc`` scrubbed, or cut to its doctype and name when the scrub itself fails."""
	try:
		return scrub_doc_dict(doc)
	except Exception:
		_log_failure("write response")
		if isinstance(doc, dict):
			return {"doctype": doc.get("doctype"), "name": doc.get("name")}
		return None


# ---------------------------------------------------------------------------
# The form's docinfo (frappe.desk.form.load / frappe.desk.form.save)
# ---------------------------------------------------------------------------


@frappe.whitelist()
def getdoc(doctype, name):
	"""frappe's ``getdoc``, with the docinfo's version history scrubbed."""
	from frappe.desk.form import load

	result = load.getdoc(doctype, name)
	_scrub_docinfo()
	return result


@frappe.whitelist()
def get_docinfo(doc=None, doctype=None, name=None):
	"""frappe's ``get_docinfo`` (the form's timeline reload), with its versions scrubbed."""
	from frappe.desk.form import load

	result = load.get_docinfo(doc=doc, doctype=doctype, name=name)
	_scrub_docinfo()
	return result


@frappe.whitelist(methods=["POST", "PUT"])
def savedocs(doc, action):
	"""frappe's ``savedocs``; its ``send_updated_docs`` strips the doc but not the docinfo."""
	from frappe.desk.form import save

	result = save.savedocs(doc, action)
	_scrub_docinfo()
	return result


@frappe.whitelist(methods=["POST", "PUT"])
def desk_cancel(doctype=None, name=None, workflow_state_fieldname=None, workflow_state=None):
	"""frappe's Desk ``cancel`` (``frappe.desk.form.save.cancel``), docinfo scrubbed."""
	from frappe.desk.form import save

	result = save.cancel(
		doctype=doctype,
		name=name,
		workflow_state_fieldname=workflow_state_fieldname,
		workflow_state=workflow_state,
	)
	_scrub_docinfo()
	return result


@frappe.whitelist(methods=["POST", "PUT"])
def desk_discard(doctype, name):
	"""frappe's Desk ``discard`` (``frappe.desk.form.save.discard``), docinfo scrubbed."""
	from frappe.desk.form import save

	result = save.discard(doctype, name)
	_scrub_docinfo()
	return result


# ---------------------------------------------------------------------------
# Write responses over RPC (frappe.client, frappe.model.workflow)
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST", "PUT"])
def client_set_value(doctype, name, fieldname, value=None):
	"""``frappe.client.set_value``, its returned document scrubbed."""
	from frappe import client

	return _scrubbed(client.set_value(doctype, name, fieldname, value))


@frappe.whitelist(methods=["POST", "PUT"])
def client_insert(doc=None):
	"""``frappe.client.insert``, its returned document scrubbed."""
	from frappe import client

	return _scrubbed(client.insert(doc))


@frappe.whitelist(methods=["POST", "PUT"])
def client_save(doc):
	"""``frappe.client.save``, its returned document scrubbed."""
	from frappe import client

	return _scrubbed(client.save(doc))


@frappe.whitelist(methods=["POST", "PUT"])
def client_submit(doc):
	"""``frappe.client.submit``, its returned document scrubbed."""
	from frappe import client

	return _scrubbed(client.submit(doc))


@frappe.whitelist(methods=["POST", "PUT"])
def client_cancel(doctype, name):
	"""``frappe.client.cancel``, its returned document scrubbed."""
	from frappe import client

	return _scrubbed(client.cancel(doctype, name))


@frappe.whitelist()
def apply_workflow(doc, action):
	"""``frappe.model.workflow.apply_workflow``, its returned document scrubbed.

	frappe returns the Document and lets the response serialise it through ``__json__``, which
	is ``as_dict(no_nulls=True)``; this returns that same dict, scrubbed, so the JSON is
	identical but for the hidden fields. The Document itself is never mutated: a workflow's
	asynchronous tasks are enqueued with it after commit. None (a queued submission) passes
	through.
	"""
	from frappe.model import workflow

	result = workflow.apply_workflow(doc, action)
	if result is None or not hasattr(result, "as_dict"):
		return result
	return _scrubbed(result.as_dict(no_nulls=True))


# ---------------------------------------------------------------------------
# Write responses over REST (after_request)
# ---------------------------------------------------------------------------


def scrub_rest_write_response(response=None, request=None):
	"""``after_request``: scrub the document a REST write echoes back.

	``POST``/``PUT /api/resource/<doctype>[/<name>]`` and ``/api/v2/document/<doctype>...``
	return ``{"data": <document>}``, serialised before this runs, so the body is parsed, scrubbed
	and written back, and only when a field actually went. Every other request leaves on the
	first two checks. Frappe logs and swallows an exception from an ``after_request`` hook, which
	would send the body unscrubbed, so this one never raises: a body it cannot parse is not a
	document echo, and a document it cannot scrub is cut to its doctype and name.
	"""
	try:
		if request is None or response is None:
			return
		if request.method not in _REST_WRITE_METHODS or not _REST_WRITE_PATH.match(request.path or ""):
			return
		if response.status_code >= 300 or response.mimetype != "application/json" or response.is_streamed:
			return
		body = json.loads(response.get_data())
	except Exception:
		return

	data = body.get("data") if isinstance(body, dict) else None
	if not isinstance(data, dict) or not data.get("doctype"):
		return
	try:
		changed = _scrub_doc_dict(data)
	except Exception:
		_log_failure("REST write response")
		body["data"] = {"doctype": data.get("doctype"), "name": data.get("name")}
		changed = True
	if not changed:
		return
	try:
		response.set_data(json.dumps(body, separators=(",", ":"), ensure_ascii=False, default=str))
	except Exception:
		_log_failure("REST write response")
		response.set_data(json.dumps({"data": {"doctype": data.get("doctype"), "name": data.get("name")}}))
