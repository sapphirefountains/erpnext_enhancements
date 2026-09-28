# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Knowledge base search, as the person asking (WI-080 PR 5, ADR 0017 section 3).

``search.py`` ranks; this module decides **what may be ranked** and keeps the index. It is the one
path from the knowledge base to a search result, and it holds to four rules:

1. **Published articles only, and never the Version doctype.** The corpus is ``Knowledge Article``
   rows with status Published. Nothing here names the drafts' doctype, reads it, or runs SQL on it
   (the AI gate refuses such SQL over MCP anyway), so no draft's text can reach a result: it is not
   in anything this module reads.
2. **Permission first, without a dialog.** ``frappe.has_permission(ARTICLE, "read")`` is asked
   before any list call, with v16's default ``throw=False``, which passes ``print_logs=False`` and
   queues nothing (``frappe/__init__.py:600-646``, :632). v16's ``get_list`` goes through
   ``model/qb_query.py`` to ``frappe.qb.get_query``, whose ``check_select_permission`` refuses with
   ``frappe.throw`` (``database/query.py:278``, ``:1378-1390``), and that queues an "Insufficient
   Permission" message even when the exception is caught. Any signed-in user, a portal user
   included, can call the AwesomeBar's endpoint, so such a caller must get no results and no
   message.
3. **The caller's readable set is taken before anything is ranked.** ``frappe.get_list`` of the
   Published names, **as the caller**, and ``search.search`` removes everything else before it
   scores. The rows shown are then read again with ``frappe.get_list`` as the caller: nothing
   reaches a result except a row the caller's own list call returned. The index itself is built
   from every Published article (``frappe.get_all``), so idf is the whole corpus's, as ADR 0017
   says; that tells a caller nothing, since every staff user reads every published article.
4. **Nothing is stored or queued.** The index lives in each web worker's memory, per site
   (:func:`_index`). No table, no redis, no job: the deploy's ``FLUSHDB`` has nothing to kill, and a
   restarted worker rebuilds on its first search.

**The cache.** Keyed on ``select count(*), max(modified) from `tabKnowledge Article``` (a function in
SQL text, which v16 allows; its refusal is of function *strings* in a ``get_all`` field list).
Publishing, retiring and confirming write the article in their own request, so the stamp moves at
commit and each worker rebuilds inline on its next search. The stamp is read **before** the corpus,
so an article committed in between is in the new index and makes the next stamp differ, which only
costs a rebuild. An index older than :data:`MAX_AGE_SECONDS` is rebuilt anyway, for a write past the
ORM that kept ``modified``. A rebuild happens outside the lock and is swapped in under it; one that
fails keeps the old index and the old stamp, and that search answers with nothing.

**The AwesomeBar** (:func:`awesomebar_hits`) is registered in ``hooks.py`` as ``awesomebar_search``.
v16.35 calls every such hook from ``frappe.desk.search.awesomebar_search`` for any input of two or
more characters (``desk/search.py:510-538``; ``awesome_bar.js`` ``txt.length > 1``, :176, and
``has_awesomebar_search`` from ``boot.py:109``), keeps 20 results per hook and needs a label and a
route (``_normalize_awesomebar_result``, :541-574). It logs a hook's exception to the
``awesomebar`` logger only (:528-532), but this one never raises: every keystroke is a request, and
an error per keystroke would be noise nobody reads.
"""

import html
import threading
import time

import frappe
from frappe.utils import get_fullname, get_url, getdate, nowdate

from erpnext_enhancements.knowledge_base import constants, markdown
from erpnext_enhancements.knowledge_base import search as engine

ARTICLE = constants.ARTICLE_DOCTYPE
PUBLISHED = constants.ARTICLE_STATUSES[0]

#: An index this old is rebuilt even when the stamp has not moved.
MAX_AGE_SECONDS = 600

#: What the index is built from: the published article, never its versions. ``body_md`` is the
#: Markdown publishing wrote from the approved body; it is read to build the index and not kept.
CORPUS_FIELDS = (
	"name",
	"kb_number",
	"title",
	"keywords",
	"summary",
	"body_md",
	"kind",
	"department_block",
)

#: What a result shows, read as the caller for the hits only.
DISPLAY_FIELDS = (
	"name",
	"version_number",
	"title",
	"kind",
	"department_block",
	"summary",
	"approved_by",
	"approved_on",
	"review_by",
	"ai_drafted",
)

#: The AwesomeBar: at most this many knowledge base hits, ranked above v16's built-in entries
#: (``index`` 100, per the hook's docstring) and below this app's own "Search for" row.
AWESOMEBAR_LIMIT = 5
AWESOMEBAR_INDEX = 160

#: ``{site: {"stamp", "built_at", "index"}}``: one index per site, per worker process.
_STATE = {}
_LOCK = threading.Lock()


def search(query, *, department=None, kind=None, limit=10, snippets=True):
	"""Published articles matching ``query`` that the session user may read, best first.

	``department`` and ``kind`` are filters, read the way a person types them
	(``constants.department_option``, ``constants.kind_option``); one that names no department or
	kind returns no results and says so in ``problems``, rather than silently ignoring the filter.
	Returns ``{"results": [...], "problems": [...]}``; see the README for a result's fields.
	"""
	department_filter, kind_filter, problems = read_filters(department, kind)
	empty = {"results": [], "problems": problems}
	if problems or not isinstance(query, str) or not query.strip():
		return empty

	# Rule 2: no read, no results, and no message queued (throw is False by default).
	if not frappe.has_permission(ARTICLE, "read"):
		return empty
	# Rule 3: the caller's readable set, before anything is ranked.
	allowed = set(
		frappe.get_list(ARTICLE, filters={"status": PUBLISHED}, pluck="name", limit_page_length=0)
	)
	if not allowed:
		return empty
	index = _index()
	if index is None:
		return empty
	hits = engine.search(
		index, query, allowed=allowed, department=department_filter, kind=kind_filter, limit=limit
	)
	if not hits:
		return empty

	fields = list(DISPLAY_FIELDS) + (["body_md"] if snippets else [])
	rows = frappe.get_list(
		ARTICLE,
		filters={"name": ["in", [hit.key for hit in hits]], "status": PUBLISHED},
		fields=fields,
		limit_page_length=0,
	)
	by_name = {row.get("name"): row for row in rows}
	today = getdate(nowdate())
	results = []
	for hit in hits:
		row = by_name.get(hit.key)
		if row is None:
			# Retired, or no longer readable, since the readable set was read: never shown.
			continue
		results.append(_result(row, hit, query, today, snippets))
	return {"results": results, "problems": []}


def read_filters(department=None, kind=None):
	"""``(department, kind, problems)``: a department and a kind filter as a person or a tool typed
	them, read with ``constants.department_option`` and ``constants.kind_option``. Blank is no filter.
	One that names no department or kind is a problem, in words that list the valid values, and never a
	filter silently ignored. Shared by search and the AI tools' table of contents (PR 6a)."""
	problems = []
	department_filter = kind_filter = None
	if department not in (None, ""):
		department_filter = constants.department_option(department)
		if department_filter is None:
			problems.append(
				f"unknown department {_quoted(department)}; use one of "
				+ ", ".join(constants.DEPARTMENT_BLOCK_OPTIONS)
			)
	if kind not in (None, ""):
		kind_filter = constants.kind_option(kind)
		if kind_filter is None:
			problems.append(f"unknown kind {_quoted(kind)}; use one of Policy, Process or SOP")
	return department_filter, kind_filter, problems


def _result(row, hit, query, today, snippets):
	name = row.get("name")
	review_by = getdate(row.get("review_by")) if row.get("review_by") else None
	approved_on = getdate(row.get("approved_on")) if row.get("approved_on") else None
	approver = row.get("approved_by")
	result = {
		"name": name,
		"kb_number": name,
		"version": row.get("version_number"),
		"title": row.get("title"),
		"kind": row.get("kind") or None,
		"department": row.get("department_block"),
		"summary": row.get("summary"),
		"snippet": None,
		"approved_by": approver_name(approver),
		"approved_on": approved_on.isoformat() if approved_on else None,
		"review_by": review_by.isoformat() if review_by else None,
		"review_overdue": bool(review_by and review_by < today),
		"ai_drafted": bool(row.get("ai_drafted")),
		"url": get_url(f"/desk/knowledge-article/{name}"),
		"matched": list(hit.matched),
		"score": round(hit.score, 3),
	}
	if snippets:
		result["snippet"] = engine.snippet(row.get("body_md"), query, fallback=row.get("summary") or "")
	return result


def approver_name(user):
	"""The approver's name as a result and the AI tools show it, **never an email address**
	(WI-080 PR 6a). ``None`` when there is no approver.

	v16's ``get_fullname`` answers the user id, which is the user's email address, when the User has
	no first or last name (``utils/__init__.py:59-76``), and before PR 6a this returned it as it came.
	``markdown.approver_display_name`` makes that ``"Unnamed approver"``, so the fetch tool's header
	and a search result say the same thing. ``get_fullname`` caches per request, so a page of results
	costs one read per approver."""
	if not user:
		return None
	return markdown.approver_display_name(get_fullname(user), user)


def awesomebar_hits(txt):
	"""The ``awesomebar_search`` hook: up to five published articles for what is typed in the
	AwesomeBar, as the person typing. Never raises and never leaves a message: on any failure it
	clears what was queued and returns ``[]``.

	Each hit's ``label`` and ``description`` are HTML, because v16 renders both as HTML
	(``awesome_bar.js:143-148``): the label is ``KB-0601 · <title>`` with the matched words in
	``<b>``, everything else escaped; the description is the kind and the department, escaped. The
	route is the article's form, so ``frappe.set_route`` opens ``/desk/knowledge-article/<KB number>``
	and Back returns to where the search was typed. ``value`` is the label as plain text, unique per
	article, because the AwesomeBar finds the chosen item by it.
	"""
	try:
		text = str(txt or "").strip()
		if len(text) < 2:
			return []
		out = search(text, limit=AWESOMEBAR_LIMIT, snippets=False)
		hits = []
		for result in out["results"]:
			plain = f"{result['kb_number']} · {result['title'] or ''}".strip()
			description = " · ".join(part for part in (result["kind"], result["department"]) if part)
			hits.append(
				{
					"label": engine.mark(plain, text),
					"value": plain,
					"route": ["Form", ARTICLE, result["name"]],
					"index": AWESOMEBAR_INDEX,
					"description": html.escape(description),
				}
			)
		return hits
	except Exception:
		frappe.clear_messages()
		return []


# ------------------------------------------------------------------ the index, per site and worker


def _index():
	"""This site's index, rebuilt first if the knowledge base changed or it is too old. ``None``
	when a rebuild fails."""
	site = getattr(frappe.local, "site", None) or ""
	stamp = _stamp()
	now = time.monotonic()
	state = _STATE.get(site)
	if state is not None and state["stamp"] == stamp and now - state["built_at"] <= MAX_AGE_SECONDS:
		return state["index"]
	try:
		index = _build()
	except Exception:
		# Keep the old index and stamp: the next search tries again. Nothing is logged per
		# keystroke; the search simply answers with nothing this time.
		return None
	with _LOCK:
		_STATE[site] = {"stamp": stamp, "built_at": now, "index": index}
	return index


def _stamp():
	"""``(count, max modified)`` of the articles. Read before the corpus; see the module docstring."""
	rows = frappe.db.sql("select count(*), max(modified) from `tabKnowledge Article`")
	count, modified = (rows[0] if rows else (0, None))[:2]
	return (int(count or 0), str(modified or ""))


def _build():
	rows = frappe.get_all(
		ARTICLE, filters={"status": PUBLISHED}, fields=list(CORPUS_FIELDS), limit_page_length=0
	)
	return engine.build_index(_document(row) for row in rows)


def _document(row):
	return engine.Document(
		key=row.get("name"),
		kb_number=row.get("kb_number") or row.get("name"),
		title=row.get("title") or "",
		keywords=row.get("keywords") or "",
		summary=row.get("summary") or "",
		body=row.get("body_md") or "",
		kind=row.get("kind") or None,
		department=row.get("department_block") or None,
	)


def _quoted(value):
	"""A filter value as the problem names it: the first 40 characters, quoted."""
	return repr(str(value)[:40])
