# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Knowledge Source: one saved link or uploaded file in the reference library (2026-11-25).

Reference material, **not company knowledge**: nobody approves a source, so it is never an article
and the AI tools label what they read from one as unreviewed reference (Nik, 2026-11-25, "Answer,
labeled"). Every staff user reads the library (``Desk User``, as for articles); KB Authors and KB
Approvers add to it; KB Approvers and System Managers may delete a source, so a file uploaded by
mistake can be removed. Restricted material never goes in (ADR 0017 §5).

**The controller only decides when a source must be read again.** Saving a new source, or changing
its link, its file or its kind, puts it back to Pending, clears what was read before (and a summary
Gemini wrote, but never one a person typed) and, once the save commits, queues
``knowledge_base.sources.extract_source``. The job's own save carries
``flags.kb_source_job`` and changes none of that.

**What was read sits at permlevel 1**, readable by everyone who reads the source and writable by
nobody: v16 resets a permlevel the saver cannot write before ``validate`` runs
(``Document.validate_higher_perm_levels``), so a KB Author cannot type text into
``extracted_text`` through REST and have the AI read it as the source's. Only the job writes there,
with ``ignore_permissions``.

**People's own text is scanned for secrets** (the summary, the keywords and the notes), with the
knowledge base's scan (``content.secret_findings``): the AI reads all three.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_enhancements.knowledge_base import constants, content, source_fetch, sources

#: The fields the job fills, cleared whenever the source must be read again.
READ_FIELDS = (
	"extraction_message",
	"content_type",
	"fetched_url",
	"fetched_on",
	"text_chars",
	"content_hash",
	"extracted_text",
)

#: The fields a person types that the AI also reads, so they are scanned for secrets.
SCANNED_FIELDS = (("summary", "Summary"), ("keywords", "Keywords"), ("notes", "Why it's here"))


class KnowledgeSource(Document):
	def validate(self):
		self.url = (self.url or "").strip()
		if self.source_type == "Link":
			problem = source_fetch.url_problem(self.url)
			if problem:
				frappe.throw(_(problem), title=_("Check the link"))
		elif self.source_type == "File":
			if not (self.source_file or "").strip():
				frappe.throw(_("Attach the file to save."), title=_("Attach a file"))
		else:
			frappe.throw(_("Choose Link or File."))
		self._refuse_secrets()
		if self.flags.get(sources.JOB_FLAG):
			return
		if self._source_changed():
			self.extraction_state = "Pending"
			for fieldname in READ_FIELDS:
				self.set(fieldname, None)
			if self.summary_by_ai:
				self.summary = ""
				self.summary_by_ai = 0
		if not (self.title or "").strip():
			self.title = self._default_title()

	def on_update(self):
		if self.flags.get(sources.JOB_FLAG):
			return
		if self.extraction_state == "Pending":
			sources.enqueue_extraction(self.name)

	def _source_changed(self):
		if self.is_new():
			return True
		before = self.get_doc_before_save()
		if before is None:
			return True
		return any(
			(before.get(field) or "") != (self.get(field) or "")
			for field in ("source_type", "url", "source_file")
		)

	def _default_title(self):
		if self.source_type == "File":
			name = (self.source_file or "").rsplit("/", 1)[-1]
			return name[:140] or _("Uploaded file")
		google = source_fetch.google_file(self.url)
		if google:
			return _("Google file {0}").format(google[1][:12])
		from urllib.parse import urlsplit

		parts = urlsplit(self.url)
		return f"{parts.hostname or ''}{parts.path or ''}".rstrip("/")[:140] or _("Saved link")

	def _refuse_secrets(self):
		places = []
		for fieldname, label in SCANNED_FIELDS:
			value = self.get(fieldname)
			if isinstance(value, str) and value:
				places.extend(
					f"{label} line {f.line} looks like {f.kind}" for f in content.secret_findings(value)
				)
		if places:
			frappe.throw(
				_(
					"Not saved, because it holds something shaped like a secret: {0}. Remove it and save again."
				).format("; ".join(places)),
				title=_("Remove the secret"),
			)
