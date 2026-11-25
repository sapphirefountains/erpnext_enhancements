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
	"snapshot_file",
)

#: Where an uploaded file's URL may point: this site's own files. Frappe does not check an Attach
#: field's value, and the job reads only a File attached to the source itself in any case.
FILE_URL_PREFIXES = ("/private/files/", "/files/")

#: The fields a person types that the AI also reads, so they are scanned for secrets.
SCANNED_FIELDS = (("summary", "Summary"), ("keywords", "Keywords"), ("notes", "Why it's here"))


class KnowledgeSource(Document):
	def validate(self):
		self.url = (self.url or "").strip()
		self.source_file = (self.source_file or "").strip()
		if self.source_type == "Link":
			self.source_file = ""
			problem = source_fetch.url_problem(self.url)
			if problem:
				frappe.throw(_(problem), title=_("Check the link"))
		elif self.source_type == "File":
			self.url = ""
			if not self.source_file:
				frappe.throw(_("Attach the file to save."), title=_("Attach a file"))
			if not self.source_file.startswith(FILE_URL_PREFIXES) or ".." in self.source_file:
				frappe.throw(_("Attach the file with the Attach button."), title=_("Attach a file"))
		else:
			frappe.throw(_("Choose Link or File."))
		self._refuse_secrets()
		if self.flags.get(sources.JOB_FLAG):
			return
		changed = self._source_changed()
		if changed:
			self._refuse_google_link_unless_approver()
		self._keep_a_persons_summary()
		self._keep_a_persons_title()
		if changed and self.title_from_source:
			self.title = ""
		if changed:
			self.extraction_state = "Pending"
			for fieldname in READ_FIELDS:
				self.set(fieldname, None)
			if self.summary_by_ai:
				self.summary = ""
				self.summary_by_ai = 0
		if not (self.title or "").strip():
			self.title = self._default_title()
			self.title_from_source = 1

	def on_update(self):
		if self.flags.get(sources.JOB_FLAG):
			return
		if self.extraction_state == "Pending":
			sources.enqueue_extraction(self.name, self.modified)

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

	def _refuse_google_link_unless_approver(self):
		"""A Google link is read with ERPNext's Drive account, which reaches far more than the person
		saving it may: the shared drives' project, customer and opportunity folders. So only a KB
		Approver saves one (chosen in the 2026-11-25 review); anyone else uploads the file."""
		if self.source_type != "Link" or not source_fetch.google_file(self.url):
			return
		if constants.APPROVER_ROLE in frappe.get_roles():
			return
		frappe.throw(
			_(
				"Only a KB Approver can save a Google Docs or Drive link, because ERPNext reads it with "
				"the company's Drive account. Download the file and upload it here instead."
			),
			title=_("Upload the file instead"),
		)

	def _keep_a_persons_summary(self):
		"""A summary a person edits is theirs: it stops being Gemini's, so no later read replaces it."""
		if not self.summary_by_ai or self.is_new():
			return
		before = self.get_doc_before_save()
		if before is not None and (before.get("summary") or "") != (self.summary or ""):
			self.summary_by_ai = 0

	def _keep_a_persons_title(self):
		"""A title a person types is theirs: the next read no longer replaces it with the page's."""
		if not self.title_from_source or self.is_new():
			return
		before = self.get_doc_before_save()
		if before is not None and (before.get("title") or "") != (self.title or ""):
			self.title_from_source = 0

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
