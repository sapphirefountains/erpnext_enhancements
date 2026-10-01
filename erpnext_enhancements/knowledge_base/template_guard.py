# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Templates, scripts and print formats never read knowledge base drafts (2026-10-01).

**Why.** Every Jinja template the server renders gets Frappe v16's scripting globals (``utils/jinja.py``
``get_jenv``: ``exec_safe_globals`` by default, ``render_safe_globals`` when a caller restricts them),
and those read tables with no permission check (``frappe.get_doc``, ``frappe.get_all``,
``frappe.db.get_value``, a read-only ``frappe.db.sql``, ``frappe.qb``, ``frappe.get_cached_doc``).
Server Scripts and Script Reports get the same through ``safe_exec``. The knowledge base keeps drafts
from everyone but KB Authors and KB Approvers, and not every role that may write a stored template
holds one of those, so the drafts are refused to all of them here, in one place.

**Where.** Four patches (``monkeypatches.py``), each idempotent:

1. **``Database.execute_query``**, the one place every MariaDB statement reaches the driver (``sql``
   is its only caller; ``get_doc``, ``get_value``, ``get_all``, the query builder's ``run`` and a raw
   ``sql`` all end there). While a template renders, a script runs or a print format renders
   (:data:`GUARDED_FLAGS`), a statement that reads the drafts' table (:data:`DRAFTS_TABLE`) is
   refused, and so is one reading the server's view of statements in flight
   (:data:`SERVER_VIEWS`, which would show another request's values). It checks **the statement the
   driver will send**: the cursor's own ``mogrify`` of the query and its values, because the driver
   applies Python ``%`` formatting, and a directive such as ``%.0s`` (which prints nothing) would
   otherwise rejoin a table name split to pass a check of the query alone. If ``mogrify`` fails, the
   query is checked with every ``%`` directive removed and only its letters kept, which can only
   over-refuse. A write (``insert``, ``update``, ``delete``, ``replace``) may target the drafts' table,
   since Frappe's own updates of a draft (its ``_assign`` when a ToDo closes) can run inside a script,
   but nothing else in it may name the table: v16's ``set_value`` renders a query-builder value as a
   subquery, which would copy a draft into another table (:func:`readable_part`).
2. **The document cache never holds a draft** (``_set_document_in_cache``, the module function and
   the name ``frappe`` re-exports). ``frappe.get_cached_doc`` reads redis with no SQL, and v16 caches
   a document for an hour from several ordinary paths (an email's link to it, a print attached to an
   email). With nothing cached, every read of a draft is a statement, which (1) sees.
3. **``File.get_content``** refuses a File attached to a draft in a guarded context: a template finds
   a draft's Files by the doctype as a value (``tabFile`` is not the drafts' table), and the default
   globals hand it whole documents.
4. **Print formats get a flag.** v16 renders a print format with ``template.render`` and no flag of
   its own (``www/printview.py`` ``get_rendered_template``), unlike ``frappe.render_template``, so the
   ``pdf_body_html`` hook (:func:`pdf_body_html`, which ``hooks.py`` registers and printview calls,
   last entry wins) raises :data:`PRINT_FLAG` and records the document being printed.

**The one exemption** is the knowledge base's own print global, ``printing.kb_document``, and only
for a genuine print of the very document being printed (:func:`printing_this`): the print flag up,
neither Frappe flag up (a print requested from inside a template or a script is not genuine), and the
same object printview handed the template. A Jinja global is callable from every template, and a
template rendered in a job runs as Administrator, whose permission check passes everything, so
"the session may read it" is no exemption by itself. A template cannot raise any of these flags: its
``frappe.flags`` is a fresh dict, never the request's.

**Every worker.** v16 reads hooks from the redis cache (``frappe.get_hooks``), so a worker that never
misses it never imports ``hooks.py``, where the patches used to be applied alone. ``before_request``
and ``before_job`` call ``monkeypatches.ensure_applied`` too.

**What it does not close.** Each needs a trusted role, as the README's "Every leak path" table says of
the System Manager's raw writes:

* a Query Report, whose SQL v16 runs as the viewer with no flag (``report.py``
  ``execute_query_report``). Writing one needs Script Manager;
* a condition evaluated with ``safe_eval`` (a Notification's or Assignment Rule's condition, a
  ``depends_on``), which sets no flag: it can test a draft one yes or no at a time, and only a System
  Manager writes those;
* a draft's File's row (its name and size), which says a draft has a picture, never what is in it;
* the System Console run with full Python, which no guard inside Frappe can stop.

A refusal raises ``PermissionError`` with one sentence that names nothing of the query, and is logged
(deferred, so a GET's rollback cannot drop it) with the user and the kind of context, never the query.
"""

import contextlib
import functools
import re

import frappe
from frappe import _

from erpnext_enhancements.knowledge_base import constants

#: The drafts' table as a statement names it, folded the way :func:`names_drafts` folds one.
DRAFTS_TABLE = ("tab" + constants.VERSION_DOCTYPE).casefold()
#: The same with every character but its letters dropped, for :func:`_letters`.
DRAFTS_LETTERS = re.sub(r"[^a-z]", "", DRAFTS_TABLE)

#: The server's view of statements in flight, which holds other requests' SQL with its values. A
#: template or script has no business reading it.
SERVER_VIEWS = ("processlist", "performance_schema")

#: Ours: up while a print format renders (:func:`pdf_body_html`).
PRINT_FLAG = "kb_rendering_print"
#: Ours: the documents being printed, innermost last (:func:`pdf_body_html`).
PRINTING_DOCS = "kb_printing_docs"
#: Ours: up while the knowledge base's own print reads a draft (:func:`reading_drafts`).
EXEMPT_FLAG = "kb_reads_drafts"
#: Frappe's own counters: ``utils/jinja.py`` ``safe_render_flags`` and ``utils/safe_exec.py``
#: ``safe_exec_flags``.
FRAPPE_FLAGS = ("in_render_safe_exec", "in_safe_exec")

#: The counters under which a read of the drafts' table is refused, and what each means in words.
GUARDED_FLAGS = {
	"in_render_safe_exec": "a template",
	"in_safe_exec": "a script",
	PRINT_FLAG: "a print format",
}

#: A write's head up to and including the one table it targets: ``update `tabX```, ``insert into
#: `tabX```, ``delete from `tabX```, ``replace into `tabX```, after any leading comments.
_WRITE_HEAD = re.compile(
	r"^(?:\s+|/\*.*?\*/|--[^\n]*\n|#[^\n]*\n)*"
	r"(?:update(?:\s+(?:low_priority|ignore))*"
	r"|(?:insert|replace)(?:\s+(?:low_priority|delayed|high_priority|ignore))*\s+into"
	r"|delete(?:\s+(?:low_priority|quick|ignore))*\s+from)"
	r"\s+(?:`[^`]*`|[^\s(]+)",
	re.IGNORECASE | re.DOTALL,
)
#: A Python ``%`` formatting directive, as the driver applies them (``%%`` is a literal percent).
_DIRECTIVE = re.compile(r"%(?:\([^)]*\))?[#0\- +]*(?:\*|\d+)?(?:\.(?:\*|\d+))?[hlL]?[diouxXeEfFgGcrsab]")

#: What :func:`pdf_body_html` is registered as in ``hooks.py``; it never hands a print to itself.
HOOK_PATH = "erpnext_enhancements.knowledge_base.template_guard.pdf_body_html"

#: Marks each wrapped function, so a patch is applied once per process.
PATCH_MARKER = "_ee_kb_drafts_guard"


# ------------------------------------------------------------------ what a statement reads


def names_drafts(text):
	"""Whether ``text`` names the drafts' table. Case and runs of whitespace are folded: on a
	case-sensitive server that can only over-refuse."""
	if text is None:
		return False
	return DRAFTS_TABLE in " ".join(_text(text).casefold().split())


def names_server_view(text):
	"""Whether ``text`` reads the server's view of statements in flight."""
	if text is None:
		return False
	folded = _text(text).casefold()
	return any(view in folded for view in SERVER_VIEWS)


def readable_part(text):
	"""``text`` without the one table a write targets; the whole of anything else. What is left is
	everything the statement reads."""
	text = _text(text)
	head = _WRITE_HEAD.match(text)
	return text[head.end() :] if head else text


def _letters(query):
	"""``query`` with every ``%`` directive removed and only its letters kept, folded: a superset of
	any table name the driver's formatting could make of it."""
	return re.sub(r"[^a-z]", "", _DIRECTIVE.sub("", _text(query)).casefold())


def _text(value):
	if isinstance(value, bytes | bytearray):
		return bytes(value).decode("utf-8", "replace")
	return str(value)


def statement(db, query, values):
	"""The statement the driver will send: the cursor's ``mogrify`` of ``query`` and ``values``
	(mysqlclient 2.2 and PyMySQL both have it), or ``None`` when it cannot be made."""
	if not values:
		return _text(query)
	try:
		return _text(db._cursor.mogrify(query, values))
	except Exception:
		return None


# ------------------------------------------------------------------ the contexts


def guarded_context(flags):
	"""The guarded context ``flags`` (``frappe.local.flags``) is in, in words (``"a template"``), or
	``None``: none is up, or the knowledge base's own print holds the exemption."""
	if not flags or flags.get(EXEMPT_FLAG):
		return None
	for flag, words in GUARDED_FLAGS.items():
		if flags.get(flag):
			return words
	return None


def printing_this(doc, flags=None):
	"""Whether ``doc`` is the very document a genuine print is rendering now: the print flag up, no
	template or script around it, and ``doc`` the object printview handed the template."""
	flags = _flags() if flags is None else flags
	if not flags or not flags.get(PRINT_FLAG) or any(flags.get(flag) for flag in FRAPPE_FLAGS):
		return False
	printing = flags.get(PRINTING_DOCS) or []
	return bool(printing) and printing[-1] is doc


# ------------------------------------------------------------------ the checks


def check_statement(db, query, values, flags):
	"""Refuse a read of the drafts' table, or of the server's view of statements, in a guarded
	context. Outside one this costs one dict lookup."""
	context = guarded_context(flags)
	if context is None:
		return
	final = statement(db, query, values)
	if final is None:
		refused = DRAFTS_LETTERS in _letters(readable_part(query)) or names_server_view(query)
	else:
		refused = (
			names_drafts(readable_part(final))
			or names_drafts(readable_part(query))
			or names_server_view(final)
		)
	if refused:
		_refuse(context, _("Knowledge base drafts cannot be read from {0}."))


def check_file(file, flags):
	"""Refuse reading the bytes of ``file`` if it is attached to a draft, in a guarded context."""
	if (file.get("attached_to_doctype") or "") != constants.VERSION_DOCTYPE:
		return
	context = guarded_context(flags)
	if context is not None:
		_refuse(context, _("A knowledge base draft's files cannot be read from {0}."))


def _refuse(context, message):
	_log_refusal(context)
	frappe.throw(
		message.format(_(context))
		+ " "
		+ _(
			"Published articles are readable as Knowledge Article; drafts open only to KB Authors "
			"and KB Approvers, in the Desk."
		),
		frappe.PermissionError,
		title=_("Not available here"),
	)


# ------------------------------------------------------------------ the patches


def guard_execute_query(original):
	"""``Database.execute_query`` wrapped with :func:`check_statement`."""

	@functools.wraps(original)
	def execute_query(self, query, values=None):
		check_statement(self, query, values, _flags())
		return original(self, query, values)

	setattr(execute_query, PATCH_MARKER, True)
	return execute_query


def guard_file_content(original):
	"""``File.get_content`` wrapped with :func:`check_file`."""

	@functools.wraps(original)
	def get_content(self, *args, **kwargs):
		check_file(self, _flags())
		return original(self, *args, **kwargs)

	setattr(get_content, PATCH_MARKER, True)
	return get_content


def guard_document_cache(original):
	"""``_set_document_in_cache`` that never stores a draft. Every other document is cached as before."""

	@functools.wraps(original)
	def _set_document_in_cache(key, doc):
		if getattr(doc, "doctype", None) == constants.VERSION_DOCTYPE:
			return None
		return original(key, doc)

	setattr(_set_document_in_cache, PATCH_MARKER, True)
	return _set_document_in_cache


def apply_patches():
	"""Install every guard, each once per process. The MariaDB classes inherit ``execute_query``
	(v16 overrides it only for SQLite). The cache function is patched in its module, where
	``get_cached_doc`` looks it up at each call, and under the name ``frappe`` re-exports, which the
	form loader calls."""
	import frappe.model.document as document_module
	from frappe.core.doctype.file.file import File
	from frappe.database.database import Database

	if not getattr(Database.execute_query, PATCH_MARKER, False):
		Database.execute_query = guard_execute_query(Database.execute_query)
	if not getattr(File.get_content, PATCH_MARKER, False):
		File.get_content = guard_file_content(File.get_content)
	if not getattr(document_module._set_document_in_cache, PATCH_MARKER, False):
		document_module._set_document_in_cache = guard_document_cache(document_module._set_document_in_cache)
	if not getattr(frappe._set_document_in_cache, PATCH_MARKER, False):
		frappe._set_document_in_cache = document_module._set_document_in_cache


# ------------------------------------------------------------------ the flags


@contextlib.contextmanager
def reading_drafts():
	"""The knowledge base's own print reads a draft here. ``printing.kb_document`` takes it only when
	:func:`printing_this` says the print is genuine."""
	with _counter(EXEMPT_FLAG):
		yield


@contextlib.contextmanager
def rendering_print(doc=None):
	"""A print format is rendering ``doc``."""
	flags = frappe.local.flags
	stack = flags.get(PRINTING_DOCS)
	if stack is None:
		stack = flags[PRINTING_DOCS] = []
	stack.append(doc)
	try:
		with _counter(PRINT_FLAG):
			yield
	finally:
		stack.pop()


def pdf_body_html(*args, **kwargs):
	"""The ``pdf_body_html`` hook: the print, rendered by the hook registered before this one
	(Frappe's own, ``utils/pdf.py``, unless another app replaced it), with :data:`PRINT_FLAG` up and
	the document being printed recorded."""
	previous = [path for path in frappe.get_hooks("pdf_body_html") if path != HOOK_PATH]
	render = frappe.get_attr(previous[-1]) if previous else _frappe_pdf_body_html()
	doc = (kwargs.get("args") or {}).get("doc") if isinstance(kwargs.get("args"), dict) else None
	with rendering_print(doc):
		return render(*args, **kwargs)


def hook_is_last():
	"""Whether printview will call :func:`pdf_body_html`: it calls only the last registered entry."""
	hooks = frappe.get_hooks("pdf_body_html")
	return bool(hooks) and hooks[-1] == HOOK_PATH


def _frappe_pdf_body_html():
	from frappe.utils.pdf import pdf_body_html as render

	return render


def _flags():
	return getattr(frappe.local, "flags", None)


@contextlib.contextmanager
def _counter(flag):
	flags = frappe.local.flags
	flags[flag] = (flags.get(flag) or 0) + 1
	try:
		yield
	finally:
		flags[flag] = (flags.get(flag) or 1) - 1


def _log_refusal(context):
	"""One deferred Error Log: who, and from what kind of context. Never the query, which is the
	template's own text. A failure to log never stops the refusal."""
	try:
		user = getattr(getattr(frappe, "session", None), "user", None) or "unknown"
		frappe.log_error(
			title="Knowledge base drafts refused",
			message=f"{user} tried to read knowledge base drafts from {context}.",
			defer_insert=True,
		)
	except Exception:
		pass
