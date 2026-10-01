"""Who can read a design review (ADR 0016 §2).

Three gates, outermost first:

1. **The Desk.** Every doctype in this module grants read only to ``Desk User`` (v16's
   automatic role for System Users) and ``System Manager``. A Website User or a Guest holds
   neither, so ``/api/resource`` refuses them before these hooks run.
2. **These hooks.** A System User who is not a System Manager reads a review, and every
   option, screen, part, note, vote, verdict and decision under it, only if they are on its
   participant list. ``permission_query_conditions`` filters lists and reports;
   ``has_permission`` answers for a single document.
3. **The endpoints.** Votes, verdicts and notes grant no create or write to any role; only
   ``api.design_review`` writes them, after checking participation and the review's status.

A System Manager reads everything and is the moderator: they open and close reviews, set a
note's status and promote decisions. Reading is not voting — a System Manager who is not a
participant cannot vote either.

Registered in ``hooks.py`` under ``permission_query_conditions`` and ``has_permission``.
Tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

import frappe

#: Every doctype hung off a review by its ``review`` field.
CHILD_RECORDS = (
	"Design Option",
	"Design Screen",
	"Design Part",
	"Design Note",
	"Design Vote",
	"Design Verdict",
	"Design Decision",
)


def _is_moderator(user: str) -> bool:
	return "System Manager" in frappe.get_roles(user)


def reviews_for(user: str) -> list[str]:
	"""The reviews ``user`` is a participant of."""
	return frappe.get_all(
		"Design Review Participant",
		filters={"user": user, "parenttype": "Design Review", "parentfield": "participants"},
		pluck="parent",
	)


def is_participant(review: str, user: str | None = None) -> bool:
	user = user or frappe.session.user
	return bool(
		frappe.db.exists(
			"Design Review Participant",
			{"parent": review, "parenttype": "Design Review", "parentfield": "participants", "user": user},
		)
	)


def can_read_review(review: str, user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user in ("Guest", "", None):
		return False
	return _is_moderator(user) or is_participant(review, user)


# ---------------------------------------------------------------- list filters
#
# v16 calls each hook as ``method(user, doctype=doctype)`` (``frappe/database/query.py``,
# ``get_permission_query_conditions``) and wraps a non-empty answer in parentheses.

_READS = (None, "read", "select", "print", "report", "export")


def _condition(user, column):
	user = user or frappe.session.user
	if _is_moderator(user):
		return ""
	return (
		f"{column} in (select `parent` from `tabDesign Review Participant` "
		f"where `parenttype` = 'Design Review' and `parentfield` = 'participants' "
		f"and `user` = {frappe.db.escape(user)})"
	)


def review_query(user=None, doctype=None):
	return _condition(user, "`tabDesign Review`.`name`")


def record_query(user=None, doctype=None):
	"""One filter for every doctype in ``CHILD_RECORDS``: its ``review`` must be one of yours."""
	if doctype not in CHILD_RECORDS:
		# Registered only for those doctypes; anything else here is a wiring mistake, and the
		# failure direction is to show nothing rather than everything.
		return "1 = 0"
	return _condition(user, f"`tab{doctype}`.`review`")


# ---------------------------------------------------------------- single documents
#
# v16 treats any falsy answer — ``None`` included — as a denial
# (``frappe/permissions.py``, ``has_controller_permissions``: ``if not controller_permission``).
# A hook can only take away what the DocPerm grants, so "no objection" is ``True``.


def has_review_permission(doc, ptype=None, user=None, debug=False):
	"""Reading needs participation; writing a review is the System Manager's DocPerm alone."""
	user = user or frappe.session.user
	if _is_moderator(user):
		return True
	if ptype in _READS:
		return is_participant(doc.name, user)
	return False


def has_record_permission(doc, ptype=None, user=None, debug=False):
	"""The same answer for anything hung off a review. Writing is the endpoints' alone, and no
	role holds a write DocPerm on these doctypes, so a moderator's "no objection" grants none."""
	user = user or frappe.session.user
	if _is_moderator(user):
		return True
	if ptype in _READS:
		return is_participant(doc.get("review"), user)
	return False
