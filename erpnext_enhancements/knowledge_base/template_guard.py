# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Templates, scripts and print formats never read knowledge base drafts (2026-10-01).

**Why.** Every Jinja template the server renders gets Frappe v16's scripting globals (``utils/jinja.py``
``get_jenv``: ``exec_safe_globals`` by default, ``render_safe_globals`` when a caller restricts them),
and those read tables with no permission check (``frappe.get_doc``, ``frappe.get_all``,
``frappe.db.get_value``, a read-only ``frappe.db.sql``, ``frappe.qb``). Server Scripts and Script
Reports get the same through ``safe_exec``. The knowledge base keeps drafts from everyone but KB
Authors and KB Approvers, and not every role that may write a stored template holds one of those, so
the drafts' table is refused to all of them here, in one place, whatever the template is.

**The guard sits where every one of those reads ends: ``Database.sql``.** ``get_doc``, ``get_value``,
``get_all``, the query builder's ``run`` and a raw ``sql`` all reach ``frappe.database.database.
Database.sql`` with the final query string, so a doctype name a template builds from pieces (``"Knowledge
Article " ~ "Version"``) is whole by the time it is checked. The patch (``monkeypatches.py``) refuses a
query naming the drafts' **table** (:data:`DRAFTS_TABLE`, ``tabKnowledge Article Version``: MariaDB has
no way to read a table without naming it, and a ``SELECT`` cannot build an identifier from a string)
while one of :data:`GUARDED_FLAGS` is up:

* ``in_render_safe_exec``: Frappe's own counter around ``frappe.render_template`` (``jinja.py``
  ``safe_render_flags``), so Terms and Conditions, Email Templates, Notifications, Letter Heads, Web
  Pages, Address Templates and every other stored template;
* ``in_safe_exec``: Frappe's own counter around ``safe_exec`` (Server Scripts, Script Reports, the
  System Console);
* :data:`PRINT_FLAG`: ours, around every print format (:func:`pdf_body_html`, the ``pdf_body_html``
  hook). v16 renders a print format with ``template.render`` and no flag (``www/printview.py``
  ``get_rendered_template``), and a System Manager need not hold a KB role.

Only the table is matched, never the doctype's name as a value, so Frappe's own reads about the
doctype keep working inside a template: its meta (``tabDocField where parent = ...``), a File's or a
ToDo's reference to a draft.

**A draft's pictures too.** A draft's Files are found by that value (``tabFile`` is not the drafts'
table), and where a template is handed whole documents (the default globals' ``frappe.get_doc``) it
could read one's bytes. So the patch also wraps ``File.get_content``: in a guarded context, a File
attached to a draft is refused the same way (:func:`check_file`).

**Whoever is signed in.** No user is exempt, not even a KB role: a template rendered in a job runs as
Administrator, who holds every role, so "the person can read drafts" says nothing about who wrote the
template or who receives the output. The one exemption is the knowledge base's own code
(:func:`reading_drafts`), which ``printing.kb_document``, the print formats' Jinja global, takes after
nothing and gives up on return; it checks read permission itself. A template cannot raise the
exemption: its ``frappe.flags`` is a fresh dict, never the request's.

**What it does not close.** Each is a trusted role's own choice, or needs one, as the README's "Every
leak path" table says of the System Manager's raw writes:

* a Query Report, whose SQL v16 runs as the viewer with no flag (``report.py``
  ``execute_query_report``). Writing one needs Script Manager, a trusted role;
* a condition evaluated with ``safe_eval`` (a Notification's or Assignment Rule's condition, a
  ``depends_on``), which sets no flag: it can test a draft one yes or no at a time, and only a System
  Manager writes those;
* a draft's attached File's name and metadata (its ``tabFile`` row), which say a draft has a picture,
  never what is in it or the draft's text;
* the System Console run with full Python, which no guard inside Frappe can stop.

A refusal raises ``PermissionError`` with one sentence that names nothing of the query, and is logged
(deferred, so a GET's rollback cannot drop it) with the user and the kind of context, never the query.
"""

import contextlib
import functools

import frappe
from frappe import _

from erpnext_enhancements.knowledge_base import constants

#: The drafts' table as a query names it, folded the way :func:`names_drafts` folds a query.
DRAFTS_TABLE = ("tab" + constants.VERSION_DOCTYPE).casefold()

#: Ours: up while a print format renders (:func:`pdf_body_html`).
PRINT_FLAG = "kb_rendering_print"
#: Ours: up while the knowledge base's own code reads drafts inside a guarded context.
EXEMPT_FLAG = "kb_reads_drafts"

#: The counters under which a query naming the drafts' table is refused, and what each means in words.
GUARDED_FLAGS = {
	"in_render_safe_exec": "a template",
	"in_safe_exec": "a script",
	PRINT_FLAG: "a print format",
}

#: What :func:`pdf_body_html` is registered as in ``hooks.py``; it never hands a print to itself.
HOOK_PATH = "erpnext_enhancements.knowledge_base.template_guard.pdf_body_html"

#: Marks the wrapped ``Database.sql``, so the patch is applied once per process.
PATCH_MARKER = "_ee_kb_drafts_guard"


def names_drafts(query):
	"""Whether ``query`` names the drafts' table. Case and runs of whitespace are folded; the
	identifier itself cannot be split, quoted apart or built from a string inside a ``SELECT``."""
	if query is None:
		return False
	return DRAFTS_TABLE in " ".join(str(query).casefold().split())


def guarded_context(flags):
	"""The guarded context ``flags`` (``frappe.local.flags``) is in, in words (``"a template"``), or
	``None``: none is up, or the knowledge base's own code has the exemption."""
	if not flags or flags.get(EXEMPT_FLAG):
		return None
	for flag, words in GUARDED_FLAGS.items():
		if flags.get(flag):
			return words
	return None


def check_query(query, flags):
	"""Refuse ``query`` if it names the drafts' table inside a guarded context. The cheap test
	(a flag) comes first: outside templates and scripts this costs one dict lookup per query."""
	context = guarded_context(flags)
	if context is None or not names_drafts(query):
		return
	_log_refusal(context)
	frappe.throw(
		_(
			"Knowledge base drafts cannot be read from {0}. Published articles are readable as "
			"Knowledge Article; drafts open only to KB Authors and KB Approvers, in the Desk."
		).format(_(context)),
		frappe.PermissionError,
		title=_("Not available here"),
	)


def guard_sql(original):
	"""``Database.sql`` wrapped with :func:`check_query`. A query built and not run (``run=False``)
	reads nothing, and passes."""

	@functools.wraps(original)
	def sql(self, query, *args, **kwargs):
		if kwargs.get("run", True):
			check_query(query, getattr(frappe.local, "flags", None))
		return original(self, query, *args, **kwargs)

	setattr(sql, PATCH_MARKER, True)
	return sql


def check_file(file, flags):
	"""Refuse reading the bytes of ``file`` if it is attached to a draft, inside a guarded context."""
	if (file.get("attached_to_doctype") or "") != constants.VERSION_DOCTYPE:
		return
	context = guarded_context(flags)
	if context is None:
		return
	_log_refusal(context)
	frappe.throw(
		_("A knowledge base draft's files cannot be read from {0}.").format(_(context)),
		frappe.PermissionError,
		title=_("Not available here"),
	)


def guard_file_content(original):
	"""``File.get_content`` wrapped with :func:`check_file`."""

	@functools.wraps(original)
	def get_content(self, *args, **kwargs):
		check_file(self, getattr(frappe.local, "flags", None))
		return original(self, *args, **kwargs)

	setattr(get_content, PATCH_MARKER, True)
	return get_content


def patch_database_sql():
	"""Install :func:`guard_sql` on ``frappe.database.database.Database.sql`` and
	:func:`guard_file_content` on ``File.get_content``, each once per process. The MariaDB class
	inherits ``sql`` (v16 overrides it only for Postgres, SQLite and DuckDB)."""
	from frappe.core.doctype.file.file import File
	from frappe.database.database import Database

	if not getattr(Database.sql, PATCH_MARKER, False):
		Database.sql = guard_sql(Database.sql)
	if not getattr(File.get_content, PATCH_MARKER, False):
		File.get_content = guard_file_content(File.get_content)


@contextlib.contextmanager
def reading_drafts():
	"""The knowledge base's own code reads drafts here, whatever context it runs in. Only Python can
	raise it; a template's ``frappe.flags`` is not the request's."""
	with _counter(EXEMPT_FLAG):
		yield


@contextlib.contextmanager
def rendering_print():
	"""A print format is rendering."""
	with _counter(PRINT_FLAG):
		yield


def pdf_body_html(*args, **kwargs):
	"""The ``pdf_body_html`` hook: the print, rendered by the hook registered before this one
	(Frappe's own, ``utils/pdf.py``, unless another app replaced it), with :data:`PRINT_FLAG` up."""
	previous = [path for path in frappe.get_hooks("pdf_body_html") if path != HOOK_PATH]
	render = frappe.get_attr(previous[-1]) if previous else _frappe_pdf_body_html()
	with rendering_print():
		return render(*args, **kwargs)


def _frappe_pdf_body_html():
	from frappe.utils.pdf import pdf_body_html as render

	return render


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
