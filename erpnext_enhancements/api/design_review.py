"""Design Review endpoints for the Review Room at ``/review`` (WI-079 slice 5, ADR 0016 §2).

Every method is POST-only and a thin wrapper: the participant, status and role checks live in
``erpnext_enhancements.design_review.service`` so there is one copy of each. Every Design
doctype grants read to System Manager only and create or write to nobody, so these are the only
way a participant reads a review or writes a vote, verdict or note, and every write is stamped
with the session user, never a value from the client.

Called from ``public/js/design_review/`` (the ``design_review`` bundle) by full dotted path.

Tabs: a new file in ``api/`` takes the ``.editorconfig`` default (see ``api/README.md``).
"""

from __future__ import annotations

import frappe

from erpnext_enhancements.design_review import service


def _parsed(value):
	return frappe.parse_json(value) if isinstance(value, str) else value


@frappe.whitelist(methods=["POST"])
def list_reviews():
	"""The reviews you can open: every one for a System Manager, your own for anyone else."""
	return service.list_reviews()


@frappe.whitelist(methods=["POST"])
def get_review(review):
	"""A review's state: status, people, tallies, notes, decisions."""
	return service.get_review(review)


@frappe.whitelist(methods=["POST"])
def get_content(review):
	"""The review's sanitized screens, codes, click-through rules and stylesheet."""
	return service.get_content(review)


@frappe.whitelist(methods=["POST"])
def find_people(review, text):
	"""Employees a participant can name as the person who raised a note."""
	return service.find_people(review, text)


@frappe.whitelist(methods=["POST"])
def cast_vote(review, track, ranking):
	"""Rank one track's options. Participants only, while the review is Open."""
	return service.cast_vote(review, track, _parsed(ranking))


@frappe.whitelist(methods=["POST"])
def cast_verdict(review, option_code, screen_code, verdict=None):
	"""Yes, maybe or no on one screen; an empty verdict clears yours."""
	return service.cast_verdict(review, option_code, screen_code, verdict or None)


@frappe.whitelist(methods=["POST"])
def add_note(review, code, text, raised_by=None):
	"""Pin a note to one part, by element code. ``raised_by`` is an Employee, and optional."""
	return service.add_note(review, code, text, raised_by or None)


@frappe.whitelist(methods=["POST"])
def delete_note(note):
	"""Delete a note you wrote, while it is still Open and the review is Open."""
	return service.delete_note(note)


@frappe.whitelist(methods=["POST"])
def set_status(review, status):
	"""Move a review to Draft, Open, Closed or Decided. System Managers only."""
	return service.set_status(review, status)


@frappe.whitelist(methods=["POST"])
def set_note_status(note, status):
	"""Accept, reject or mark a note done. System Managers only."""
	return service.set_note_status(note, status)


@frappe.whitelist(methods=["POST"])
def record_decision(review, title, decision, track=None, option_code=None, notes=None):
	"""Record what was decided and which notes it carries. System Managers only."""
	return service.record_decision(review, title, decision, track or None, option_code or None, _parsed(notes))


@frappe.whitelist(methods=["POST"])
def promote_decision(decision, target_erpnext=0, target_triton=0, request_type="Feature", impact="Nice to have"):
	"""File a decision as an Enhancement Request, already Approved. A person with System Manager only."""
	return service.promote_decision(decision, target_erpnext, target_triton, request_type, impact)


@frappe.whitelist(methods=["POST"])
def check_bundle(file_name, review=None):
	"""Dry-run an uploaded bundle: every check an import makes, nothing written. System Managers only."""
	service.require_moderator()
	from erpnext_enhancements.design_review import importer

	try:
		bundle = importer.parse_bundle_text(frappe.get_doc("File", file_name).get_content())
		return importer.check_bundle(bundle, review or None)
	except (importer.BundleError, ValueError) as exc:
		return {"ok": False, "error": str(exc)}


@frappe.whitelist(methods=["POST"])
def import_review(file_name, review=None):
	"""Import a bundle uploaded as a File: a new review, or a new revision of ``review``."""
	service.require_moderator()
	from erpnext_enhancements.design_review import importer

	report = importer.import_file(file_name, review or None)
	# The upload held the bundle before sanitizing. The import wrote its own sanitized File, so the
	# original is only a second, unsanitized copy of unreleased designs.
	if frappe.db.get_value("File", file_name, "attached_to_doctype") in (None, ""):
		frappe.delete_doc("File", file_name, ignore_permissions=True)
	return report
