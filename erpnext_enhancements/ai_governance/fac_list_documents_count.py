"""Make FAC 3.0.0's ``list_documents`` count its results the way Frappe 16 allows.

THE BUG. Frappe Assistant Core 3.0.0 (the AI connector on prod) counts the rows behind every
``list_documents`` call with::

    frappe.get_list(doctype, filters=filters, fields=["count(name) as count"], limit=1, ignore_permissions=False)

Frappe 16 refuses a SQL function written as a string field (``frappe/database/query.py``
``_validate_select_field``: "SQL functions are not allowed as strings in SELECT"; see this repo's
CLAUDE.md). FAC catches the error, writes an Error Log row titled
"list_documents: count query failed for <DocType>", and returns the documents with
``total_count: None``. So every call from every MCP client adds one Error Log row, and
``has_more`` falls back to guessing from the page size.

UPSTREAM. Fixed in FAC v3.1.0 (commit 2b834b0, 2026-09-24), which tries the dict form
``{"COUNT": "name", "as": "count"}`` first. Prod still runs 3.0.0, and FAC is a separate app
this repo cannot edit. Upgrading FAC is the real fix; this module holds the line until then.
**Delete this module (and its entry in ``monkeypatches._PATCHES``) once FAC is upgraded**: from
then on it is dead code that only logs "source-mismatch" once per worker.

WHY A MONKEYPATCH. FAC 3.0.0 has no supported way to replace a core tool. An app tool with the
same name would be *listed* in place of the core one (``ToolRegistry.get_available_tools``
lets hook tools overwrite plugin tools) but never *run*: ``ToolRegistry.get_tool`` checks plugin
tools first. Clients would read our schema and execute FAC's code.

WHAT IT DOES. It replaces the module global ``frappe`` inside FAC's ``list_documents`` module,
and nowhere else, with a stand-in that passes every attribute through to the real ``frappe``
except one call: ``get_list`` with exactly ``fields=["count(name) as count"]``. That call is
answered the way Frappe 16's own Desk list view counts (``frappe.desk.reportview.get_count``):
the same ``get_list`` query, with the same filters and ``ignore_permissions`` value, selecting
only ``name``, wrapped in ``SELECT COUNT(*) FROM (...)``. Permission rules, user permissions
and ``permission_query_conditions`` all apply exactly as they do to the rows the tool returns.
The answer comes back in the shape FAC reads, ``[{"count": n}]``, so callers see the same
output with ``total_count`` filled in.

Two cases are left as they were. A virtual DocType has no table to count, so its count call goes
to its controller exactly as FAC wrote it, once. And, as on the Desk, the count gets one second
(``max_statement_time``) on MariaDB: past that it comes back ``None``, which is what FAC returned
before, minus the Error Log row. Every ``list_documents`` call now runs a real count, which is
nothing at Sapphire's data size; the cap is there so it never becomes the slow part.

WHEN IT DOES NOTHING. It is inert unless every check passes, and it never raises:

* FAC is not importable on this bench;
* the source of FAC's ``list_documents`` module is not byte-for-byte FAC 3.0.0's (sha256 in
  ``KNOWN_SOURCES``) -- so an upgrade, a hotfix or a local edit switches it off. That is the
  point: the stand-in is only known to be correct against code we have read;
* the module's ``frappe`` is not the real ``frappe`` module (someone else got there first).

It tells the app log once per process why it stood down, by reason only: WARNING for
"source-mismatch", the expected state after a FAC upgrade (frappe's file logger drops WARNING on a
production site, so an upgraded bench does not log it on every worker start), and ERROR for the
states nobody expects ("failed", "foreign-frappe"). Nothing here logs arguments, filters or results.

FAC-OPTIONAL. Like ``fac_tool_categories``, nothing here has an import statement for
``frappe_assistant_core``: the target is resolved by dotted path only after
``importlib.util.find_spec`` says FAC is on the bench, so sites without FAC never touch it.

Applied from ``monkeypatches.ensure_applied`` (``before_request`` and ``before_job``), because
v16 serves hooks from the redis cache and the bottom of ``hooks.py`` does not run in every
worker. MCP calls are ordinary web requests, so ``before_request`` precedes FAC's own import of
the module in a fresh worker.
"""

import hashlib
import importlib
import importlib.util
import inspect

import frappe

FAC_PACKAGE = "frappe_assistant_core"
TARGET_MODULE = "frappe_assistant_core.plugins.core.tools.list_documents"

#: sha256 of the target module's source (``\r\n`` folded to ``\n``) -> the FAC release it is.
#: Only add an entry after reading that release's module and confirming its count call is still
#: the one ``_CountSafeFrappe.get_list`` recognises and nothing else in it would be changed.
KNOWN_SOURCES = {
	"2dea9d80bdecee1db816bf905544a60dac3d222979dd40d0ceed1f0b7988ac1c": "3.0.0",
}

#: The exact ``fields`` argument of FAC 3.0.0's count call. Anything else passes through.
LEGACY_COUNT_FIELDS = ["count(name) as count"]

APPLIED = "applied"
NO_FAC = "no-fac"
SOURCE_MISMATCH = "source-mismatch"
FOREIGN_FRAPPE = "foreign-frappe"
FAILED = "failed"

# One attempt per process: the outcome cannot change without a restart, because a FAC upgrade
# arrives with one, and a failed import would fail the same way on every request.
_state = None


#: Seconds the count may run on MariaDB before it gives up, as ``reportview.get_count`` does.
COUNT_TIMEOUT_SECONDS = 1


def count_field(doctype):
	"""The one field the count query selects: the table-qualified ``name``, as the Desk writes it."""
	return f"`tab{doctype}`.name"


def permitted_count(real_frappe, doctype, kwargs):
	"""How many rows ``real_frappe.get_list(doctype, **kwargs)`` could return, with no limit.

	Frappe 16's Desk list view does it this way (``frappe.desk.reportview.get_count``): build the
	permission-checked query selecting only ``name``, then count its rows. Only ``fields``,
	``limit`` and ``order_by`` are set here; ``filters``, ``ignore_permissions`` and anything else
	the caller passed go through unchanged, so the count obeys the same rules as the listing.

	Like the Desk when it is given a limit, the count runs under a one-second
	``max_statement_time`` on MariaDB and returns ``None`` when it times out. Not for virtual
	DocTypes: their controller ignores ``run=0``.
	"""
	from frappe.query_builder.functions import Count

	query_kwargs = {key: value for key, value in kwargs.items() if key not in ("fields", "limit", "order_by")}
	rows = real_frappe.get_list(
		doctype,
		fields=[count_field(doctype)],
		order_by=None,
		run=0,
		**query_kwargs,
	)
	count_sql, count_params = real_frappe.qb.from_(rows).select(Count("*")).walk()
	timeout_clause = (
		f"SET STATEMENT max_statement_time={COUNT_TIMEOUT_SECONDS} FOR "
		if real_frappe.db.db_type == "mariadb"
		else ""
	)
	try:
		return real_frappe.db.sql(timeout_clause + count_sql, count_params)[0][0]
	except Exception as e:
		if real_frappe.db.is_statement_timeout(e):
			return None
		raise


def is_virtual(doctype):
	from frappe.model.utils import is_virtual_doctype

	return bool(is_virtual_doctype(doctype))


class _CountSafeFrappe:
	"""Stands in for ``frappe`` inside FAC's ``list_documents`` module, and nowhere else.

	Every attribute read and write goes to the real module, so ``frappe.session``,
	``frappe.log_error`` and the listing's own ``get_list`` behave exactly as before. Only the
	3.0.0 count call is answered differently.
	"""

	__slots__ = ("_ee_frappe",)

	def __init__(self, real_frappe):
		object.__setattr__(self, "_ee_frappe", real_frappe)

	def __getattr__(self, name):
		return getattr(self._ee_frappe, name)

	def __setattr__(self, name, value):
		setattr(self._ee_frappe, name, value)

	def get_list(self, *args, **kwargs):
		if args and kwargs.get("fields") == LEGACY_COUNT_FIELDS and not is_virtual(args[0]):
			return [{"count": permitted_count(self._ee_frappe, args[0], kwargs)}]
		# Everything else, a virtual DocType's count included, goes to frappe exactly as FAC wrote it.
		return self._ee_frappe.get_list(*args, **kwargs)


def source_sha256(module):
	"""sha256 of ``module``'s source with line endings folded, so a CRLF checkout matches."""
	source = inspect.getsource(module).replace("\r\n", "\n")
	return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _install():
	if importlib.util.find_spec(FAC_PACKAGE) is None:
		return NO_FAC
	module = importlib.import_module(TARGET_MODULE)
	current = getattr(module, "frappe", None)
	if isinstance(current, _CountSafeFrappe):
		return APPLIED
	if source_sha256(module) not in KNOWN_SOURCES:
		return SOURCE_MISMATCH
	if current is not frappe:
		return FOREIGN_FRAPPE
	module.frappe = _CountSafeFrappe(frappe)
	return APPLIED


def apply():
	"""Install the stand-in in this process if every guard passes. Idempotent; never raises.

	Returns the outcome (``APPLIED``, ``NO_FAC``, ``SOURCE_MISMATCH``, ``FOREIGN_FRAPPE`` or
	``FAILED``). Only the first call in a process does any work.
	"""
	global _state
	if _state is not None:
		return _state
	try:
		_state = _install()
	except Exception:
		_state = FAILED
	if _state not in (APPLIED, NO_FAC):
		try:
			# File log only, never the Error Log doctype. SOURCE_MISMATCH is what every worker of an
			# upgraded bench reports, so it stays at WARNING, which frappe's file logger drops on a
			# production site (frappe/utils/logger.py default_log_level is ERROR). The other two mean
			# something unexpected happened to code we read, so they are written.
			logger = frappe.logger("erpnext_enhancements")
			log = logger.warning if _state == SOURCE_MISMATCH else logger.error
			log(f"FAC list_documents count fix not applied: {_state}")
		except Exception:
			pass
	return _state
