# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The published knowledge base as Markdown files, for the company's private mirror (WI-080 Slice 6).

One read-only endpoint, :func:`snapshot`. A scheduled job in the company's private knowledge repo calls
it and writes each article to its ``path``, ``kb/<NN-department>/<KB number>.md``. **Pull, not push**:
ERPNext holds no GitHub credential and queues nothing, so an ERPNext compromise cannot rewrite that
repo, and there is no job for the deploy's FLUSHDB to kill; a failed run is recovered by running it
again. The account that calls it, its key and the workflow are described in that repo's runbook, not
here.

What it holds to:

* **Only the mirror.** ``@frappe.whitelist(methods=["GET"])``, so never a guest and never a POST; then
  the caller must hold :data:`MIRROR_ROLE` ("KB Mirror", seeded by
  ``patches/seed_knowledge_base_mirror_role.py`` with ``desk_access = 0`` and no DocPerm anywhere) or
  be Administrator. A staff user's session is refused, System Manager included, and the refusal comes
  before anything is read. The other way round holds too: the account that holds the role can call
  nothing but this. It is a signed-in Website User, and v16's whitelist refuses only a Guest, so
  ``knowledge_base/mirror_guard.py``, an ``auth_hooks`` entry, refuses its every other request before
  Frappe dispatches it (the PR 8 review).
* **Published articles only, and never the Version doctype.** One ``frappe.get_all`` of Knowledge
  Article with status Published. ``get_all`` because the role holds no DocPerm: the role check above is
  the whole gate. What it returns is what every staff user already reads in the Desk.
* **The same bytes as ``fetch_knowledge_article``, untruncated.** Each file is
  ``ai_tools.article_text``, which fetch cuts at 40,000 characters and the mirror does not. The links
  in it start with ``frappe.utils.get_url()``: the site's configured ``host_name``, or, with none, the
  host the request came to. So a mirror file and a fetched article match byte for byte when both reach
  the site at the same address.
* **Skipped, not guessed.** An article whose department is not one of the ten blocks, or whose name is
  not a KB number, has no folder (``markdown.mirror_path``). It is listed in ``skipped`` and not
  rendered.
* **A stamp that changes exactly when a file would.** :func:`stamp_of`: sha256 over the rendered files'
  ``(path, sha256)`` pairs, sorted by path. Retiring an article, publishing a version, renaming its
  approver or moving the site's URL changes it; a save that changes no rendered byte does not. ``since``
  equal to the current stamp answers ``{"schema": 1, "unchanged": true, "stamp": ...}`` and nothing
  more.
* **Reads only.** It writes nothing and logs nothing, and its own code reads no request header, so the
  credential never passes through it (Frappe reads the ``Authorization`` header, and drops it, before
  this runs; ``get_url`` looks only at the request's host and scheme, and only when the site has no
  ``host_name``). Frappe rolls a GET's transaction back in any case (``app.sync_database``). An
  unexpected failure is Frappe's own 500.
* **Rate limited**: 60 calls an hour from one address (``frappe.rate_limiter``, keyed by the method and
  the client's IP). The limit is counted before the role check, so a refused call counts too. The
  schedule is four runs a day, plus a run on demand.

The answer, ``schema`` 1, is ``{"schema", "stamp", "app_version", "count", "skipped", "articles"}``.
Each of ``articles``, in KB-number order, is ``{"kb_number", "version", "path", "sha256", "markdown"}``
(``path`` e.g. ``kb/06-operations/KB-0601.md``; ``sha256`` the hex digest of ``markdown``'s UTF-8
bytes), and each of ``skipped`` is ``{"kb_number", "department"}``. ``knowledge_base/README.md`` ("The
private mirror (PR 8)") shows one. Adding a field is allowed; renaming or removing one, or changing
what a file holds, is a new ``schema``.

Indentation is tabs, the ``.editorconfig`` default for a new file.
"""

import hashlib

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import get_url

from erpnext_enhancements import __version__
from erpnext_enhancements.knowledge_base import ai_tools, constants, markdown

#: What the answer's shape is. The private repo's script refuses any other.
SCHEMA = 1
MIRROR_ROLE = constants.MIRROR_ROLE
ARTICLE = constants.ARTICLE_DOCTYPE
PUBLISHED = constants.ARTICLE_STATUSES[0]


@frappe.whitelist(methods=["GET"])
@rate_limit(limit=60, seconds=3600)
def snapshot(since=None):
	"""Every published article as Markdown, with its mirror path and sha256, and the stamp of the set.
	``since``: the stamp the caller already holds; when it is still current, the answer says
	``unchanged`` and carries no articles."""
	_require_mirror()
	articles, skipped = _rendered()
	stamp = stamp_of(articles)
	if isinstance(since, str) and since.strip() == stamp:
		return {"schema": SCHEMA, "unchanged": True, "stamp": stamp}
	return {
		"schema": SCHEMA,
		"stamp": stamp,
		"app_version": __version__,
		"count": len(articles),
		"skipped": skipped,
		"articles": articles,
	}


def stamp_of(articles):
	"""sha256, hex, of ``"<path>\\t<sha256>\\n"`` for each article, sorted by path, as UTF-8. It covers
	exactly the files the mirror writes, so it changes when, and only when, one of them would."""
	digest = hashlib.sha256()
	for path, sha in sorted((article["path"], article["sha256"]) for article in articles):
		digest.update(f"{path}\t{sha}\n".encode())
	return digest.hexdigest()


def _require_mirror():
	"""Refused, as ``frappe.PermissionError`` (403), unless the session user holds :data:`MIRROR_ROLE`
	or is Administrator. Asked before anything is read."""
	if frappe.session.user == "Administrator" or MIRROR_ROLE in frappe.get_roles():
		return
	frappe.throw(_("Only the knowledge base mirror may read this snapshot."), frappe.PermissionError)


def _rendered():
	"""``(articles, skipped)``: each published article with a folder, rendered whole, and each without
	one, in KB-number order."""
	base = get_url()
	rows = frappe.get_all(
		ARTICLE,
		filters={"status": PUBLISHED},
		fields=list(ai_tools.FETCH_FIELDS),
		order_by="name asc",
		limit_page_length=0,
	)
	articles, skipped = [], []
	for row in sorted(rows, key=lambda row: str(row.get("name") or "")):
		number = row.get("name")
		path = markdown.mirror_path(row)
		if path is None:
			skipped.append({"kb_number": number, "department": row.get("department_block") or None})
			continue
		text = ai_tools.article_text(row, base)
		articles.append(
			{
				"kb_number": number,
				"version": row.get("version_number"),
				"path": path,
				"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
				"markdown": text,
			}
		)
	return articles, skipped
