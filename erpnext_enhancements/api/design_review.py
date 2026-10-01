"""Design Review endpoints for the Review Room (WI-079 slice 5, ADR 0016 §2).

Every method is POST-only and a thin wrapper: the participant, status and role checks live in
``erpnext_enhancements.design_review.service`` so there is one copy of each. Votes, verdicts
and notes are written **only** here — those doctypes grant no create or write permission to
any role — and every one is stamped with the session user, never a value from the client.

Called from ``design_review/page/review_room/review_room.js`` by full dotted path.

Tabs: a new file in ``api/`` takes the ``.editorconfig`` default (see ``api/README.md``).
"""

from __future__ import annotations

import frappe

from erpnext_enhancements.design_review import service


@frappe.whitelist(methods=["POST"])
def list_reviews():
	"""The reviews you can open: every one for a System Manager, your own for anyone else."""
	return service.list_reviews()


@frappe.whitelist(methods=["POST"])
def get_review(review):
	"""A review's tracks, options, screen list, parts, tallies, notes and decisions."""
	return service.get_review(review)


@frappe.whitelist(methods=["POST"])
def get_screens(review, names):
	"""Sanitized markup for some of a review's screens, fetched as the viewer needs them."""
	return service.get_screens(review, frappe.parse_json(names) if isinstance(names, str) else names)


@frappe.whitelist(methods=["POST"])
def cast_vote(review, track, ranking):
	"""Rank one track's options. Participants only, while the review is Open."""
	return service.cast_vote(
		review, track, frappe.parse_json(ranking) if isinstance(ranking, str) else ranking
	)


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
def set_note_status(note, status):
	"""Accept, reject or mark a note done. System Managers only."""
	return service.set_note_status(note, status)


@frappe.whitelist(methods=["POST"])
def record_decision(review, title, decision, track=None, option_code=None, notes=None):
	"""Record what was decided and which notes it carries. System Managers only."""
	return service.record_decision(review, title, decision, track or None, option_code or None, notes)


@frappe.whitelist(methods=["POST"])
def promote_decision(
	decision, target_erpnext=0, target_triton=0, request_type="Feature", impact="Nice to have"
):
	"""File a decision as an Enhancement Request, already Approved. A person with System Manager only."""
	return service.promote_decision(decision, target_erpnext, target_triton, request_type, impact)


@frappe.whitelist(methods=["POST"])
def import_review(file_name, review=None):
	"""Import a bundle uploaded as a File: a new review, or a new revision of ``review``."""
	service.require_moderator()
	from erpnext_enhancements.design_review import importer

	return importer.import_file(file_name, review or None)
