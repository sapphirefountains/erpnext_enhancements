# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Row-level scoping for the learner-owned Training doctypes.

**Scope note, and it is the important part of this file:** course *content* is not
protected here. Learner roles hold no DocPerm at all on ``Training Course``,
``Training Course Version``, ``Training Lesson``, ``Training Content Block``,
``Training Checkpoint``, ``Training Question`` or ``Training Answer Option``, so
``/api/resource/Training Question`` refuses them outright — a defence that holds
even if a future endpoint is careless with ``fields=["*"]``. The learner runtime
(Phase 2) computes visibility itself and reads with ``ignore_permissions=True``
after its own gate, because visibility is a predicate — audience × roles ×
customer × assignment × published state — that reads as fifteen lines of Python
and as a stack of correlated subqueries in SQL.

What *is* here is the scoping for the records a learner owns: their assignments,
and later their attempts, completions and certificates. Those need DocPerms
because they show up in desk list views and in the learner's own transcript.

Frappe semantics worth remembering (documented the same way in
``travel_management/permissions.py``): a ``has_permission`` hook can only
**restrict** what role DocPerms already grant. It can never widen access, so the
role rows in the doctype JSON remain the outer bound.
"""

import frappe

# Roles that see everything. Training Author is deliberately absent: an author
# needs to see completion *statistics*, which the reports give them, not the
# individual records of who failed what.
UNSCOPED_ROLES = {"System Manager", "Training Manager", "HR Manager"}


def _resolve(user):
	return user or frappe.session.user


def _is_unscoped(user):
	return bool(UNSCOPED_ROLES & set(frappe.get_roles(_resolve(user))))


def _direct_report_users(user):
	"""Logins of the people who report to ``user``, via ``Employee.reports_to``.

	Empty for almost everybody, which is the common case and costs one indexed
	lookup.
	"""
	manager = frappe.db.get_value("Employee", {"user_id": user}, "name")
	if not manager:
		return []
	return [
		u
		for u in frappe.get_all("Employee", filters={"reports_to": manager}, pluck="user_id")
		if u
	]


def _own_rows_condition(doctype, user):
	"""SQL restricting ``doctype`` to rows this user owns.

	Supervisors additionally see their direct reports — that is what makes the
	sign-off queue a plain filtered list view rather than a bespoke endpoint.
	"""
	table = f"`tab{doctype}`"
	allowed = [user] + _direct_report_users(user)
	joined = ", ".join(frappe.db.escape(u) for u in allowed)
	return f"{table}.`user` in ({joined})"


def _own_row(doc, user):
	if doc.get("user") == user:
		return True
	return doc.get("user") in _direct_report_users(user)


# ------------------------------------------------------- permission_query_conditions


def assignment_query_conditions(user=None):
	if _is_unscoped(user):
		return ""
	return _own_rows_condition("Training Assignment", _resolve(user))


def attempt_query_conditions(user=None):
	if _is_unscoped(user):
		return ""
	return _own_rows_condition("Training Attempt", _resolve(user))


def attempt_question_query_conditions(user=None):
	if _is_unscoped(user):
		return ""
	return _own_rows_condition("Training Attempt Question", _resolve(user))


def completion_query_conditions(user=None):
	if _is_unscoped(user):
		return ""
	return _own_rows_condition("Training Completion", _resolve(user))


def certificate_query_conditions(user=None):
	if _is_unscoped(user):
		return ""
	return _own_rows_condition("Training Certificate", _resolve(user))


def signoff_query_conditions(user=None):
	"""A sign-off is scoped to the learner it is about, plus the people you may sign.

	Three arms, and the third is new in v1.386.0. Your own rows and your direct
	reports come from ``_own_rows_condition`` — that is what makes the sign-off
	queue a plain filtered list view rather than a bespoke endpoint. The third arm
	is **position tier**: the learners you outrank on your own ladder.

	Without it the tier rule is half-built in the way that reads as working. A
	Senior Technician would be *allowed* to sign the four Junior Technicians and
	would see none of their requests, because on this site none of them reports to
	him — they all report to the Project Manager, and so does he. Authority you
	cannot see the queue for is authority nobody exercises.

	The observed-supervisor arm is already covered: ``signoff_has_permission``
	handles the single-document read, and the queue filters on ``supervisor_user``
	directly. Folding it in here too would double-count.
	"""
	if _is_unscoped(user):
		return ""

	resolved = _resolve(user)
	base = _own_rows_condition("Training Signoff", resolved)

	from erpnext_enhancements.training import authority

	signable = authority.signable_learner_users(resolved)
	if not signable:
		return base
	joined = ", ".join(frappe.db.escape(u) for u in signable)
	return f"({base} or `tabTraining Signoff`.`user` in ({joined}))"


def badge_award_query_conditions(user=None):
	"""A badge award is scoped to the learner who earned it.

	``Training Badge Award`` grants ``read`` to ``Training Learner`` in its
	doctype JSON, and ``Training Learner`` is held by **customer** Website Users
	as well as staff — so without this, ``/api/resource/Training Badge Award``
	enumerated every staff member's badges to any client contact. The leaderboard
	is unaffected: ``gamification._stat_rows`` and ``_award_missing_badges`` read
	through ``frappe.get_all``, which does not check permissions at all.

	Note the sibling that is deliberately *not* scoped: ``Training Badge`` itself
	is a catalogue of badge definitions with no ``user`` column, and the player
	shows learners what there is to earn. Leaving it open is the intent, not an
	oversight.
	"""
	if _is_unscoped(user):
		return ""
	return _own_rows_condition("Training Badge Award", _resolve(user))


def learner_stat_query_conditions(user=None):
	"""A learner stat row is scoped to the learner it describes.

	Same leak, same shape as ``badge_award_query_conditions`` — this table holds
	points, streaks and completion counts, and ``current_streak_days`` /
	``longest_streak_days`` are nobody else's business. The leaderboard's own
	two-population separation (``gamification._stat_rows``, which takes
	``learner_type`` as a mandatory positional) is the surface that *is* meant to
	publish a ranking; a raw REST read is not.
	"""
	if _is_unscoped(user):
		return ""
	return _own_rows_condition("Training Learner Stat", _resolve(user))


def question_thread_query_conditions(user=None):
	"""Your own questions, plus the ones an author chose to publish.

	This mirrors what ``qa.list_lesson_threads`` already returns — own rows, and
	rows that are ``is_public`` **and** ``Answered`` — and it exists because the
	endpoint was the only thing enforcing that. ``Training Question Thread``
	grants ``read`` to ``Training Learner`` with no scoping hook, so
	``/api/resource/Training Question Thread`` returned every thread on the site:
	other people's unanswered questions, on courses the reader was never given,
	to customer Website Users included. "I don't understand how to drain the
	basin" is exactly the kind of thing somebody asks precisely because it is not
	going on a noticeboard.

	Both halves matter. ``is_public`` alone is not enough: an author sets it while
	the thread is still ``Open``, and an unanswered question published to the
	whole company is the thing the flag exists to avoid.
	"""
	if _is_unscoped(user):
		return ""
	table = "`tabTraining Question Thread`"
	return (
		f"({_own_rows_condition('Training Question Thread', _resolve(user))}"
		f" or ({table}.`is_public` = 1 and {table}.`status` = 'Answered'))"
	)


def submission_query_conditions(user=None):
	"""A work submission is scoped to the learner who made it.

	Graders are Training Managers, who are already unscoped and see the whole
	queue — so unlike the sign-off there is no second, grader-shaped arm here. A
	supervisor who is *not* a manager reaches a report's submissions through the
	direct-reports arm, same as everywhere else.
	"""
	if _is_unscoped(user):
		return ""
	return _own_rows_condition("Training Submission", _resolve(user))


# -------------------------------------------------------------------- has_permission


def assignment_has_permission(doc, ptype=None, user=None):
	if _is_unscoped(user):
		return True
	return _own_row(doc, _resolve(user))


def attempt_has_permission(doc, ptype=None, user=None):
	if _is_unscoped(user):
		return True
	return _own_row(doc, _resolve(user))


def attempt_question_has_permission(doc, ptype=None, user=None):
	if _is_unscoped(user):
		return True
	return _own_row(doc, _resolve(user))


def completion_has_permission(doc, ptype=None, user=None):
	if _is_unscoped(user):
		return True
	return _own_row(doc, _resolve(user))


def certificate_has_permission(doc, ptype=None, user=None):
	if _is_unscoped(user):
		return True
	return _own_row(doc, _resolve(user))


def signoff_has_permission(doc, ptype=None, user=None):
	if _is_unscoped(user):
		return True
	resolved = _resolve(user)
	# The supervisor named on the sign-off can always see it, even when the
	# learner is not one of their Employee.reports_to (a Named Supervisor or a
	# stand-in Training Manager) — otherwise they cannot action their own queue.
	if doc.get("supervisor_user") == resolved:
		return True
	if _own_row(doc, resolved):
		return True

	# The twin of the tier arm on the query condition. A query condition filters
	# lists and says nothing about frappe.get_doc(), so a supervisor who could see
	# a request in the list would hit a permission error opening it.
	from erpnext_enhancements.training import authority

	return authority.authority_basis(doc, resolved) == authority.TIER


def question_thread_has_permission(doc, ptype=None, user=None):
	if _is_unscoped(user):
		return True
	if _own_row(doc, _resolve(user)):
		return True
	return bool(doc.get("is_public")) and doc.get("status") == "Answered"


def badge_award_has_permission(doc, ptype=None, user=None):
	if _is_unscoped(user):
		return True
	return _own_row(doc, _resolve(user))


def learner_stat_has_permission(doc, ptype=None, user=None):
	if _is_unscoped(user):
		return True
	return _own_row(doc, _resolve(user))


def submission_has_permission(doc, ptype=None, user=None):
	if _is_unscoped(user):
		return True
	return _own_row(doc, _resolve(user))
