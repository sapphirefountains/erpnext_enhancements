# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The training dashboard read model (D15).

Two whitelisted reads that answer one question from two sides: **where does this
person stand?**

``get_my_dashboard()`` answers it about yourself, and feeds the *My Training*
workspace widget.

``get_person_dashboard(user)`` answers it about somebody else, for a training
manager, and **carries no scores and no attempt history**. That is a decision, not
a phase two. Showing a person their own quiz history is feedback; showing it to
their manager is assessment, and this module has already taken that position once:
``training/README.md`` records that the team feed was built to carry no number
anyone could be judged by, and reactions hang on a record with no performance
fields at all for the same reason. The exclusion lives in the SHAPE of the
response — the manager payload has no score keys to omit — so no future caller can
pass an argument that puts them back.

WHY THIS EXISTS RATHER THAN REUSING ``get_learner_bootstrap``. That endpoint is the
learner *player's* boot payload: announcements, cohorts, live classes, evaluations,
submissions, runtime settings, sign-off counts. A workspace widget calling it would
do several times the work it draws, and would couple a dashboard to every future
change in what the player needs. This assembles the same underlying facts from the
same helpers.

WHAT IT DELIBERATELY DOES NOT DO: define anything. "Open", "overdue" and "visible"
are predicates that live in ``api/training.py`` and are imported from there. This
module has already shipped the cost of a second definition twice — a nullable-date
filter that turned "no expiry" into "expired", and a dashboard tile that counted
four statuses while its drill-through selected two — so the rule here is that a
number on a dashboard is computed exactly once, in the place that owns it.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, today

from erpnext_enhancements.training.doctype.training_settings.training_settings import is_enabled

# The same set `analytics.py` uses and `training/permissions.py` leaves unscoped.
# Spelled out rather than imported for the same reason analytics spells out its
# status set: this module stays importable without a bench.
MANAGER_ROLES = {"System Manager", "Training Manager", "HR Manager"}

# How far ahead "coming up" looks. Thirty days rather than seven, because
# recertification is the thing this window exists to catch and a week's notice on a
# course that takes a day to sit is not notice.
SOON_DAYS = 30


def _is_manager(user=None):
    return bool(MANAGER_ROLES & set(frappe.get_roles(user or frappe.session.user)))


def _require_manager():
    if not _is_manager():
        frappe.throw(_("Training records for other people are for training managers."), frappe.PermissionError)


def _unavailable():
    """The module is switched off, and says so in its own words.

    A wall of zeros reads as "you have no training", which is a statement about
    this person, is wrong, and is the one sentence guaranteed to stop them asking
    why. The player learned this the hard way; the dashboard starts there.
    """
    return {"enabled": False, "message": _("Training is switched off.")}


def _course_rows(user):
    """Every course this person can see, split by what it asks of them.

    Imported wholesale from the learner runtime: ``_open_assignments`` owns "still
    owes work", ``_completed_courses`` owns "has finished", and
    ``_visible_course_names`` owns "may open at all". None of those rules are
    restated here.
    """
    from erpnext_enhancements.api import training as runtime

    profile = runtime._learner_profile(user)
    assignments = runtime._open_assignments(user)
    completions = runtime._completed_courses(user)
    visible = runtime._visible_course_names(user, profile)
    names = sorted(visible | set(assignments))
    if not names:
        return [], {}

    courses = frappe.get_all(
        "Training Course",
        filters={"name": ["in", names]},
        fields=["name", "course_title", "weight", "category", "estimated_minutes", "current_version"],
    )
    attempts = runtime._attempts_by_course(user, names)
    today_d = getdate(today())
    soon_d = getdate(add_days(today_d, SOON_DAYS))

    rows = []
    for course in courses:
        assignment = assignments.get(course.name)
        completion = completions.get(course.name)
        attempt = attempts.get(course.name)
        total = cint(frappe.db.get_value("Training Course Version", course.current_version, "total_lessons"))
        due = getdate(assignment.due_date) if assignment and assignment.due_date else None
        expires = getdate(completion.expires_on) if completion and completion.expires_on else None
        rows.append(
            {
                "course": course.name,
                "title": course.course_title,
                "weight": course.weight or "Optional",
                "category": course.category or "",
                "minutes": cint(course.estimated_minutes),
                "assignment": assignment.name if assignment else None,
                "status": assignment.status if assignment else ("Completed" if completion else None),
                "due_date": str(due) if due else None,
                # The predicate, not the status column. `refresh_overdue_status`
                # writes that column once a day, so between sweeps it is stale --
                # which is exactly the divergence that made a dashboard tile and the
                # list it opened disagree.
                "overdue": bool(assignment and due and due < today_d),
                "due_soon": bool(assignment and due and today_d <= due <= soon_d),
                "percent_complete": flt(runtime._percent_complete(attempt.name, total)) if attempt else 0.0,
                "completed_on": str(completion.completed_on) if completion and completion.completed_on else None,
                "expires_on": str(expires) if expires else None,
                "expiring_soon": bool(expires and today_d <= expires <= soon_d),
                "expired": bool(expires and expires < today_d),
            }
        )

    rows.sort(key=lambda r: (r["due_date"] is None, r["due_date"] or "", r["title"]))
    return rows, {"assignments": assignments, "completions": completions}


def _split(rows):
    """Required / Optional / Completed, in the vocabulary the author set.

    `Training Course.weight` is a two-value Select — Required or Optional — so this
    is the author's own word for the course rather than a category invented by the
    dashboard. A finished course leaves both lists: what it asks of you is nothing.
    """
    required, optional, completed = [], [], []
    for row in rows:
        if row["completed_on"]:
            completed.append(row)
        elif row["weight"] == "Required":
            required.append(row)
        else:
            optional.append(row)
    # Most recently finished first: a review is nearly always of the last thing done.
    completed.sort(key=lambda r: (r["completed_on"] or ""), reverse=True)
    return required, optional, completed


def _compliance(rows):
    """The numbers that answer "am I in trouble?".

    Every one is counted off the same row list the dashboard then draws, so a tile
    and the list beneath it cannot disagree — the failure this module has now
    shipped twice on the manager console.
    """
    required, _optional, completed = _split(rows)
    done = len([r for r in completed if r["weight"] == "Required"])
    outstanding = len(required)
    return {
        "overdue": len([r for r in rows if r["overdue"]]),
        "due_soon": len([r for r in rows if r["due_soon"]]),
        "required_outstanding": outstanding,
        "required_done": done,
        # None, not 0, when there are no required courses at all: 0% reads as
        # failure and "nothing is required of you" is not a failure.
        "required_percent": round(done * 100.0 / (done + outstanding), 1) if (done + outstanding) else None,
        "certificates_expiring": len([r for r in rows if r["expiring_soon"]]),
        "certificates_expired": len([r for r in rows if r["expired"]]),
        "minutes_outstanding": sum(cint(r["minutes"]) for r in required),
    }


@frappe.whitelist(methods=["POST"])
def get_my_dashboard():
    """Where the signed-in learner stands. Feeds the My Training workspace widget."""
    if not is_enabled():
        return _unavailable()

    user = frappe.session.user
    rows, _lookup = _course_rows(user)
    required, optional, completed = _split(rows)

    from erpnext_enhancements.api import training as runtime

    return {
        "enabled": True,
        "user": user,
        "required": required,
        "optional": optional,
        "completed": completed,
        "compliance": _compliance(rows),
        # Learner-only, both of them. See the module docstring.
        "stats": runtime._learner_stats(user),
        "scores": _scores(user),
    }


def _scores(user):
    """This learner's own quiz record. Never sent to a manager.

    `Training Attempt` carries score_percent on every attempt; the learner has
    always been able to see their own on the results screen, so surfacing the
    rollup to them adds no exposure. Sending the same rollup to somebody else does.
    """
    if not frappe.db.exists("DocType", "Training Attempt"):
        return None
    rows = frappe.get_all(
        "Training Attempt",
        filters={"user": user},
        fields=["score_percent", "status"],
        limit_page_length=0,
    )
    graded = [flt(r.score_percent) for r in rows if r.score_percent is not None]
    return {
        "attempts": len(rows),
        # None rather than 0 when nothing has been graded: an average of no scores
        # is not zero, and a dashboard reading 0% to somebody who has sat nothing is
        # simply a lie.
        "average_score": round(sum(graded) / len(graded), 1) if graded else None,
        "best_score": round(max(graded), 1) if graded else None,
    }


@frappe.whitelist(methods=["POST"])
def get_person_dashboard(user):
    """Where somebody else stands — compliance and progress only.

    NO SCORES AND NO ATTEMPT HISTORY, by construction rather than by filter: this
    function never builds them, so there is no argument that can include them.
    """
    _require_manager()
    if not is_enabled():
        return _unavailable()
    if not frappe.db.exists("User", user):
        frappe.throw(_("No such user."))

    rows, _lookup = _course_rows(user)
    required, _optional, completed = _split(rows)
    return {
        "enabled": True,
        "user": user,
        "full_name": frappe.db.get_value("User", user, "full_name") or user,
        "required": required,
        # The optional shelf is not a manager's business: what somebody chose to
        # study for themselves is not a compliance fact, and putting it on a
        # manager's screen changes what optional means.
        "completed": completed,
        "compliance": _compliance(rows),
    }
