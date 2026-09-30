# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The article as a document, printed, previewed and on its form (2026-09-30): the values
``document.render`` draws, read from the saved record.

Three doors, one renderer:

* **Print and PDF.** The "Article Document" print format (Knowledge Article's default) and the
  "Article Version Preview" format (Knowledge Article Version's default) are one line each,
  ``{{ kb_document(doc) }}`` (``setup_print_formats.py``), and :func:`kb_document` is that Jinja
  global (``hooks.py``, ``jinja.methods``).
* **The draft preview** is the second format: a KB Author's Preview button on the draft opens it
  in the print view, with the PDF button there.
* **The article's form** shows the same document at the top: ``publish.article_onload`` puts
  :func:`article_html` in ``__onload.kb.document``, and ``knowledge_article.js`` draws it into the
  ``document_view`` field.

**It prints the record as saved, never the document it is handed.** v16's whitelisted
``printview.get_html_and_style`` renders a document posted as JSON (the Desk's own print preview),
so the ``doc`` a template receives can be anything the caller typed, with any ``live_version``, body
or owner. :func:`kb_document` loads the article or version again by name and checks the caller may
read it, exactly as the Trip Sheet's global does and for the same reason (``api/travel.py``,
``ee_trip_sheet``); an unsaved draft is refused ("Save the draft to preview it"). A Jinja global is
reachable from every template on the site, not only these two formats, which is one more reason it
checks for itself.

**It reads no draft it was not asked for.** An article's page reads the Knowledge Article, its
``revisions`` rows and the Employee designation of its owner; it never names the Version doctype,
so the reader path stays what "Every leak path" in the README says it is. Only a version's own
preview reads that version, as a KB role that can open it anyway.

**Pictures are carried into the PDF.** Frappe v16's chrome PDF engine loads the page into a
headless browser pointed at the site with no session (``utils/pdf_generator/browser.py``,
``setup_body_page``), so a private image, which every KB picture is (``files.force_private``),
would print as a broken box. Only the wkhtmltopdf path inlines private images
(``utils/pdf.py``, ``prepare_options`` -> ``inline_private_images``). So :func:`kb_document` writes
each picture the body uses into the page as a ``data:`` URI, and only a File attached to the record
being printed (or, for a revision, to its article), up to :data:`INLINE_IMAGE_LIMIT` bytes each.
The form does not: the browser loads them with the reader's own session.
"""

import base64
import html
import mimetypes
import re
from urllib.parse import parse_qs, unquote, urlsplit

import frappe
from frappe import _
from frappe.utils import cint, get_fullname, getdate, nowdate

from erpnext_enhancements.knowledge_base import constants, content, document, workflow

ARTICLE = constants.ARTICLE_DOCTYPE
VERSION = constants.VERSION_DOCTYPE

#: The largest picture written into a printed page, in bytes. A bigger one is left as a link, which
#: prints as a broken box: a PDF of many such pictures would be too big to send anyone.
INLINE_IMAGE_LIMIT = 5 * 1024 * 1024

#: An ``<img>``'s ``src``, quoted either way.
_IMAGE_SOURCE = re.compile(r"(<img\b[^>]*?\ssrc\s*=\s*)([\"'])(.*?)\2", re.IGNORECASE | re.DOTALL)


def kb_document(doc):
	"""The Jinja global both print formats call: the article or version ``doc`` names, as saved,
	drawn in its kind's template. Anything that is not one of the two doctypes draws nothing."""
	doctype = getattr(doc, "doctype", None)
	if doctype not in (ARTICLE, VERSION):
		return ""
	saved = _saved(doctype, doc)
	if doctype == ARTICLE:
		sheet = article_sheet(saved)
		sheet.footnote = _(
			"{0} version {1}, printed {2}. A printed copy is not controlled: the current version is in "
			"the ERPNext Knowledge Base."
		).format(saved.name, cint(saved.get("version_number")) or "", _today())
	else:
		sheet = version_sheet(saved)
		sheet.footnote = _(
			"A preview of {0}, printed {1}. It is not company guidance until it is published."
		).format(saved.name, _today())
		if workflow.state_of(saved) == workflow.PUBLISHED:
			sheet.footnote = _("{0}, published as {1} version {2}, printed {3}.").format(
				saved.name, saved.get("article") or "", cint(saved.get("version_number")) or "", _today()
			)
	sheet.body = inline_images(sheet.body, _attachments(saved))
	return document.render(sheet)


def article_html(article):
	"""The article's form copy (``publish.article_onload``): the same document, framed as a page,
	with no footnote and the pictures left to the browser."""
	sheet = article_sheet(article)
	sheet.screen = True
	return document.render(sheet)


# ------------------------------------------------------------------ the values


def article_sheet(article):
	"""A published or retired article, as ``document.Sheet``. Reads the article and its
	``revisions`` rows only."""
	kind = article.get("kind")
	notice = None
	if article.get("status") == constants.ARTICLE_STATUSES[1]:
		notice = document.Notice(
			tone="warn",
			heading=_("Retired on {0}").format(_date_text(article.get("retired_on"))),
			text=article.get("retired_reason") or "",
		)
	return document.Sheet(
		title=article.get("title") or article.name,
		kind=kind,
		number=article.name,
		version=cint(article.get("version_number")) or None,
		date=_date(article.get("approved_on")),
		owner=_name(article.get("process_owner")),
		owner_role=_role(article.get("process_owner")),
		group=_group(article.get("department_block")),
		body=article.get("body") or "",
		revisions=article_revisions(article),
		notice=notice,
	)


def version_sheet(version):
	"""A version, as ``document.Sheet``: a draft's preview, or an approved version's record. Its
	history is its article's approved lines before it, then its own line (pending until it is
	approved)."""
	state = workflow.state_of(version)
	number = version.get("article") or None
	article = frappe.get_doc(ARTICLE, number) if number and frappe.db.exists(ARTICLE, number) else None
	approved = cint(version.docstatus) == 1
	this_version = cint(version.get("version_number")) or (
		cint(article.get("version_number")) + 1 if article else 1
	)
	scope = None
	if not number:
		try:
			scope = constants.number_scope(version.get("kind"), version.get("department_block"))
		except ValueError:
			scope = None
	revisions = (
		[line for line in article_revisions(article) if (line.version or 0) < this_version] if article else []
	)
	revisions.append(
		document.Revision(
			version=this_version,
			date=_date(version.get("approved_on")) if approved else None,
			author=_name(version.owner),
			approved_by=_name(version.get("approved_by")) if approved else "",
			note=version.get("change_note") or "",
			pending=not approved,
		)
	)
	return document.Sheet(
		title=version.get("title") or version.name,
		kind=version.get("kind"),
		number=number,
		scope=scope,
		version=this_version,
		date=_date(version.get("approved_on")) if approved else None,
		owner=_name(version.get("process_owner")),
		owner_role=_role(version.get("process_owner")),
		group=_group(version.get("department_block")),
		body=version.get("body") or "",
		revisions=revisions,
		notice=_version_notice(version, state),
		watermark=state in constants.OPEN_REVIEW_STATES,
	)


def article_revisions(article):
	"""The article's Revision History: its ``revisions`` rows, oldest first. An article published
	before the rows existed (v1.569.0) has none until its next publish, so its live version's line is
	made from the article itself; ``publish.publish`` writes that same line first when it adds the
	next one."""
	rows = list(article.get("revisions") or [])
	if not rows and cint(article.get("version_number")):
		rows = [revision_values(article)]
	return [
		document.Revision(
			version=cint(row.get("version_number")) or None,
			date=_date(row.get("approved_on")),
			author=_name(row.get("author")),
			approved_by=_name(row.get("approved_by")),
			note=row.get("change_note") or "",
		)
		for row in sorted(rows, key=lambda row: cint(row.get("version_number")))
	]


def revision_values(source, **overrides):
	"""A ``revisions`` row's values, from an article (its live version) or, with ``overrides``, from
	what publishing is about to write."""
	values = {
		"version_number": cint(source.get("version_number")),
		"approved_on": source.get("approved_on"),
		"author": source.get("author"),
		"approved_by": source.get("approved_by"),
		"change_note": source.get("change_note"),
	}
	values.update(overrides)
	return values


def _version_notice(version, state):
	if state in constants.OPEN_REVIEW_STATES:
		return document.Notice(
			tone="warn",
			heading=_("In review - not approved")
			if state == workflow.IN_REVIEW
			else _("Draft - not approved"),
			text=_(
				"A preview of {0}. It is not company guidance until a KB Approver who did not write it "
				"approves and publishes it."
			).format(version.name),
		)
	if state == workflow.SUPERSEDED:
		return document.Notice(
			tone="history",
			heading=_("Superseded"),
			text=_("A newer version of {0} is live. This one is kept as history.").format(
				version.get("article") or ""
			),
		)
	if state == workflow.DISCARDED:
		return document.Notice(
			tone="history", heading=_("Discarded"), text=_("Never published. Kept as history.")
		)
	return None


# ------------------------------------------------------------------ the saved record


def _saved(doctype, doc):
	"""The record ``doc`` names, loaded again, after checking the caller may read it."""
	name = doc.get("name") if hasattr(doc, "get") else getattr(doc, "name", None)
	if not isinstance(name, str) or not name or not frappe.db.exists(doctype, name):
		if doctype == VERSION:
			frappe.throw(_("Save the draft to preview it."), title=_("Not saved yet"))
		frappe.throw(_("There is no article {0}.").format(name or ""), title=_("Not found"))
	saved = frappe.get_doc(doctype, name)
	if not frappe.has_permission(doctype, "read", doc=saved):
		frappe.throw(_("You cannot open {0}.").format(name), frappe.PermissionError)
	return saved


def _attachments(saved):
	"""The records whose Files a printed page may carry: the record itself, and a version's article
	(a revision keeps its article's pictures, which stay attached to the article)."""
	pairs = [(saved.doctype, saved.name)]
	if saved.doctype == VERSION and saved.get("article"):
		pairs.append((ARTICLE, saved.get("article")))
	return pairs


def inline_images(body, attachments):
	"""``body`` with each picture it shows that is a private File attached to one of ``attachments``
	written in as a ``data:`` URI (see the module docstring). Anything else is left as it is."""
	fids, paths = content.referenced_files(body)
	if not fids and not paths:
		return body
	files_by_name, files_by_path = {}, {}
	for doctype, name in attachments:
		for row in frappe.get_all(
			"File",
			filters={"attached_to_doctype": doctype, "attached_to_name": name, "is_private": 1},
			fields=["name", "file_url", "file_size"],
		):
			files_by_name[row.name] = row
			files_by_path.setdefault(row.file_url or "", row)
	if not files_by_name:
		return body
	cache = {}

	def source(match):
		prefix, quote, url = match.group(1), match.group(2), match.group(3)
		row = _file_for(url, files_by_name, files_by_path)
		if row is None:
			return match.group(0)
		if row.name not in cache:
			cache[row.name] = _data_uri(row)
		data = cache[row.name]
		return f"{prefix}{quote}{data}{quote}" if data else match.group(0)

	return _IMAGE_SOURCE.sub(source, body)


def _file_for(url, files_by_name, files_by_path):
	parts = urlsplit(html.unescape(url).strip())
	for fid in parse_qs(parts.query).get("fid", ()):
		if fid.strip() in files_by_name:
			return files_by_name[fid.strip()]
	return files_by_path.get(unquote(parts.path))


def _data_uri(row):
	mime = mimetypes.guess_type(row.file_url or "")[0] or ""
	if not mime.startswith("image/") or cint(row.file_size) > INLINE_IMAGE_LIMIT:
		return ""
	try:
		data = frappe.get_doc("File", row.name).get_content()
	except Exception:
		return ""
	if isinstance(data, str):
		data = data.encode("utf-8")
	if not data or len(data) > INLINE_IMAGE_LIMIT:
		return ""
	return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


# ------------------------------------------------------------------ names and dates


def _name(user):
	"""A person's full name, never their email address."""
	if not user:
		return ""
	return get_fullname(user) or user


def _role(user):
	"""The owner's job title, from their active Employee record (``designation``); ``""`` for
	none."""
	if not user:
		return ""
	return frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "designation") or ""


def _group(block):
	"""``"06 Operations"`` -> ``"Operations"``."""
	code = constants.block_code(block)
	if code is None:
		return ""
	return dict(constants.DEPARTMENT_BLOCKS)[code]


def _date(value):
	return getdate(value) if value else None


def _date_text(value):
	day = _date(value)
	return day.strftime(document.DATE_FORMAT) if day else ""


def _today():
	return getdate(nowdate()).strftime(document.DATE_FORMAT)
