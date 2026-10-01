"""What the two design-review assistant tools do (WI-079 slice 5).

``assistant_tools/check_design_review_bundle.py`` and ``assistant_tools/submit_design_review.py``
are thin FAC wrappers over these functions, so the logic is importable without FAC and testable
bench-free.

A bundle reaches them one of two ways:

* ``bundle_json``, the bundle as JSON text in the call itself. Right for a small bundle an AI
  wrote in the conversation. Capped at ``MAX_INLINE_BYTES``: the AI write gate stores a card's
  arguments on the AI Pending Action and again on every AI Action Log row it writes, and a model
  emitting megabytes of markup in one tool call is not a plan anyway.
* ``file_name``, a private File already in ERPNext (``/review``'s IMPORT uploads one, as does the
  Desk). Right for anything large: the training concepts are about 1 MB.

Checking is a dry run for any System Manager. Importing is a person's: ``triton@``, ``mdm@`` and
``Administrator`` hold System Manager without being anyone who chose to put screens in front of
the team (``design_review.authority``), and through the gate the import runs as whoever confirmed
the card.

Tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

import json

import frappe
from frappe import _

from erpnext_enhancements.design_review import importer, service

MAX_INLINE_BYTES = 3_000_000


def bundle_from(arguments: dict) -> dict:
	"""The bundle the call names. Raises ``importer.BundleError`` with a readable message."""
	args = arguments if isinstance(arguments, dict) else {}
	text = args.get("bundle_json")
	file_name = (args.get("file_name") or "").strip()
	if bool(text) == bool(file_name):
		raise importer.BundleError("Pass the bundle as bundle_json or name an uploaded File as file_name, not both.")
	if file_name:
		doc = frappe.get_doc("File", file_name) if frappe.db.exists("File", file_name) else None
		if doc is None or doc.is_folder:
			raise importer.BundleError(f"There is no File {file_name}.")
		if not doc.is_private:
			raise importer.BundleError("A bundle File must be private: its screens are unreleased designs.")
		if not frappe.has_permission("File", "read", doc=doc):
			raise importer.BundleError(f"You cannot read File {file_name}.")
		return importer.parse_bundle_text(doc.get_content())
	if isinstance(text, dict):
		text = json.dumps(text)
	if not isinstance(text, str):
		raise importer.BundleError("bundle_json is the bundle as JSON text.")
	if len(text.encode("utf-8")) > MAX_INLINE_BYTES:
		raise importer.BundleError(
			f"bundle_json is over {MAX_INLINE_BYTES // 1_000_000} MB. Upload the file at /review instead "
			"(IMPORT), or upload it as a private File and pass its name as file_name."
		)
	return importer.parse_bundle_text(text)


def _review(arguments: dict) -> str | None:
	review = ((arguments or {}).get("review") or "").strip() or None
	if review and not frappe.db.exists(service.REVIEW, review):
		raise importer.BundleError(f"There is no design review {review}.")
	return review


def check(arguments: dict) -> dict:
	"""Every check an import makes, nothing written. A refusal is an answer, not an exception."""
	service.require_moderator()
	try:
		return importer.check_bundle(bundle_from(arguments), _review(arguments))
	except importer.BundleError as exc:
		return {"ok": False, "error": str(exc)}


def precheck(arguments: dict) -> list[str]:
	"""Why the write gate must not queue an import card: short clauses, none quoting the bundle."""
	from erpnext_enhancements.design_review import authority

	user = frappe.session.user
	if not authority.is_human_system_manager(user, frappe.get_roles(user)):
		return ["only a person with the System Manager role can import a design review"]
	try:
		importer.check_bundle(bundle_from(arguments), _review(arguments))
	except importer.BundleError as exc:
		return [str(exc)[:600]]
	return []


def submit(arguments: dict) -> dict:
	"""Import the bundle: a new review, or a new revision of ``review``. Returns the import report."""
	service.require_human_moderator()
	try:
		report = importer.import_bundle(bundle_from(arguments), _review(arguments))
	except importer.BundleError as exc:
		frappe.throw(_(str(exc)), frappe.ValidationError)
	report["link"] = f"/review/{report['review']}"
	report["next"] = (
		"Add participants on the review's Desk form, then set it Open from /review. "
		"Nobody can rank, give verdicts or write notes until both are done."
	)
	return report

