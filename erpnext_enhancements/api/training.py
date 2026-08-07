# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The learner runtime — every call the training player makes, and nothing else.

Answers one question: *what may this particular person see, do and be credited
with right now?* Authoring lives in :mod:`training_author`; this file never
writes content, only progress.

Four properties hold across the whole surface and are the reason it is written
the way it is:

* **Nothing here trusts the caller's identity.** Learner, employee and customer
  are always derived from ``frappe.session.user``. A payload field named ``user``
  is ignored wherever one turns up.
* **Every attempt-scoped call asserts ``attempt.user == frappe.session.user`` by
  hard equality** — no role bypass, not even for a Training Manager. A manager who
  needs to look at somebody's attempt uses the desk, where the DocPerms and the
  permission query decide. ``ignore_permissions=True`` appears only *after* that
  assertion, never as the gate itself.
* **Learner roles hold no DocPerm on the content doctypes at all** (see
  ``training/permissions.py``), so a permission-checked read of Training Course
  would return nothing for everyone this file exists to serve. Visibility is
  therefore computed in Python — once, in :func:`_visible_course_names` — and the
  reads that follow it are deliberately unchecked.
* **Correct answers never leave the server.** Quiz and checkpoint grading is
  delegated to ``training/grading.py``, which owns the answer key. The one place
  this module serves checkpoint text itself (:func:`_checkpoint_payload`) names
  its fields explicitly and never reads ``is_correct``.

Two anti-cheat rules are enforced *here* rather than in ``grading``, because they
are about the request rather than the answer:

* a checkpoint is only served, and only accepted, when the stored watch intervals
  actually cover its timestamp (± :data:`CHECKPOINT_TOLERANCE_SECONDS`);
* checkpoint timestamps are handed out **one at a time** and re-armed after each
  answer. A list of them is a map of exactly where to skip to.

Callers include customer Website Users with ``desk_access = 0``, so nothing here
may depend on a desk role, on a workspace, or on ``frappe.has_permission`` over a
content doctype.

Indentation is 4 spaces, matching the majority of ``api/``.
"""

import statistics

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime, today

from erpnext_enhancements.training import gcs_media, grading, progress
from erpnext_enhancements.training.doctype.training_assignment.training_assignment import (
    OPEN_STATUSES,
)
from erpnext_enhancements.training.doctype.training_assignment.training_assignment import (
    _customer_for_user as customer_for_user,  # one implementation of Contact → Dynamic Link → Customer
)
from erpnext_enhancements.training.doctype.training_settings.training_settings import (
    get_settings,
    is_enabled,
)

# How far a stored watch interval may miss a checkpoint's timestamp and still
# count as "watched". Two seconds absorbs the gap between the last heartbeat and
# the pause; anything larger starts to forgive a genuine skip.
CHECKPOINT_TOLERANCE_SECONDS = 2

# Block types that carry no measurable playback, so no signed URL and no coverage.
EMBED_BLOCK_TYPE = "External Embed"


# --------------------------------------------------------------------- guards


def _learner():
    """The session user, or a refusal.

    Whitelisted methods already reject Guest, but the check is repeated because
    the message a signed-out customer sees on the portal matters more than the
    generic one.
    """
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Please sign in to take training."), frappe.PermissionError)
    return user


def _runtime_ready():
    return is_enabled("training_enabled") and is_enabled("portal_enabled")


def _unavailable():
    """The shape every *read* endpoint returns while the module is dormant.

    Deliberately not an exception: the portal is linked from the navbar before
    the switches are turned on, and a learner arriving early should be told the
    training area is not open yet rather than shown an error page.
    """
    return {"enabled": False, "message": _("Training is not available yet.")}


def _require_runtime():
    """The same gate for endpoints that write. A refusal here *is* the message."""
    if not _runtime_ready():
        frappe.throw(_("Training is not available yet."))


def _attempt(name):
    """Load an attempt after proving it belongs to the caller.

    Hard equality, no role bypass. This is the single ownership gate for every
    attempt-scoped endpoint in the file; if a new endpoint does not start with
    this call, it is wrong.
    """
    if not name:
        frappe.throw(_("No attempt was given."))
    doc = frappe.get_doc("Training Attempt", name)
    if doc.user != frappe.session.user:
        frappe.throw(_("Not permitted."), frappe.PermissionError)
    return doc


def _open_attempt(name):
    """An attempt that is still being worked on.

    Finished attempts stay readable (a learner may re-watch what they passed) but
    accept no further progress — otherwise a completed attempt could be topped up
    after the completion record was already written and submitted.
    """
    doc = _attempt(name)
    if doc.status != "In Progress":
        frappe.throw(_("This attempt has already been finished. Start it again to carry on."))
    return doc


# ------------------------------------------------------------------- identity


def _learner_profile(user):
    """Who this person is, in the two ways the module cares about.

    ``internal`` is what the Internal Staff / Customers audience switch is tested
    against. It is *not* the same as "has an Employee record": a new starter whose
    Employee row has not been created yet is still a System User and must not drop
    into the customer catalogue.
    """
    employee = frappe.db.get_value(
        "Employee", {"user_id": user, "status": "Active"}, ["name", "employee_name"], as_dict=True
    )
    account = frappe.db.get_value("User", user, ["full_name", "user_type"], as_dict=True) or frappe._dict()
    customer = None if employee else customer_for_user(user)
    return frappe._dict(
        {
            "user": user,
            "full_name": account.get("full_name") or user,
            "employee": employee.name if employee else None,
            "customer": customer,
            "learner_type": "Employee" if employee else "Website User",
            "internal": bool(employee) or account.get("user_type") == "System User",
        }
    )


# ----------------------------------------------------------------- visibility


def _open_assignments(user):
    """``{course: assignment row}`` for everything this person still owes."""
    rows = frappe.get_all(
        "Training Assignment",
        filters={"user": user, "status": ["in", OPEN_STATUSES]},
        fields=["name", "course", "status", "due_date", "required_version", "assignment_source"],
    )
    return {row.course: row for row in rows}


def _visible_course_names(user, profile=None):
    """Every published course this user may open, as a set of course names.

    One helper, called by everything, because visibility is five predicates ANDed
    together — published state × audience × role × customer × assignment — and the
    failure mode of duplicating it is a customer-facing portal quietly listing an
    internal safety course.

    An open assignment wins over the audience rules on purpose. Somebody who has
    been assigned a course must be able to take it even if they have since moved
    department and lost the role that made it visible; the alternative is an
    overdue assignment nobody can clear.
    """
    profile = profile or _learner_profile(user)
    roles = set(frappe.get_roles(user))
    assigned = set(_open_assignments(user))

    visible = set()
    for course in frappe.get_all(
        "Training Course",
        filters={"status": "Published"},
        fields=["name", "audience", "customer_scope", "weight", "allow_self_enrollment", "current_version"],
    ):
        if not course.current_version:
            continue
        if course.name in assigned:
            visible.add(course.name)
            continue
        if not _audience_matches(course, profile):
            continue
        if profile.internal and not _role_gate_ok(course.name, roles):
            continue
        if not profile.internal and not _customer_gate_ok(course, profile.customer):
            continue
        visible.add(course.name)
    return visible


def _audience_matches(course, profile):
    audience = course.audience or "Internal Staff"
    if audience == "Both":
        return True
    if audience == "Customers":
        return not profile.internal
    return bool(profile.internal)


def _role_gate_ok(course, roles):
    """Empty role table means "every internal learner", not "nobody"."""
    allowed = frappe.get_all(
        "Training Audience Role",
        filters={"parent": course, "parenttype": "Training Course"},
        pluck="role",
    )
    return not allowed or bool(set(allowed) & roles)


def _customer_gate_ok(course, customer):
    if (course.customer_scope or "All Customers") != "Selected Customers":
        return True
    if not customer:
        return False
    return bool(
        frappe.db.exists(
            "Training Audience Customer",
            {"parent": course.name, "parenttype": "Training Course", "customer": customer},
        )
    )


def _require_visible(course, profile):
    if course not in _visible_course_names(profile.user, profile):
        frappe.throw(_("This course is not available to you."), frappe.PermissionError)


# ------------------------------------------------------- version / lesson maps


def _version_lessons(course_version):
    """Lessons of one version, in reading order."""
    return frappe.get_all(
        "Training Lesson",
        filters={"course_version": course_version},
        fields=["name", "lesson_key", "lesson_title", "chapter_key"],
        order_by="chapter_key asc, idx_in_chapter asc, creation asc",
    )


def _lesson_name(course_version, lesson_key):
    name = frappe.db.get_value(
        "Training Lesson", {"course_version": course_version, "lesson_key": lesson_key}, "name"
    )
    if not name:
        frappe.throw(_("That lesson is not part of this course."))
    return name


def _public_toc(version_row):
    """The published table of contents, with the internal lesson docname removed.

    The player addresses lessons by ``lesson_key`` alone. Leaking the docname
    would hand a learner the argument for ``/api/resource/Training Lesson`` —
    which their role cannot read, but there is no reason to publish the key to a
    door and then rely on the lock.
    """
    toc = frappe.parse_json(version_row.get("toc_json") or "[]") or []
    return [{k: v for k, v in row.items() if k != "lesson"} for row in toc if isinstance(row, dict)]


# ------------------------------------------------------------------- progress


def _lesson_progress(attempt_name, lesson_key):
    return (progress.load(attempt_name).get("lessons") or {}).get(lesson_key) or {}


def _block_intervals(attempt_name, lesson_key, block_key):
    blocks = _lesson_progress(attempt_name, lesson_key).get("blocks") or {}
    return (blocks.get(block_key) or {}).get("iv") or []


def _covered(intervals, second):
    """True when ``second`` falls inside a watched range, give or take the tolerance.

    Anti-cheat rule 3 rests entirely on this: a checkpoint at 04:12 is neither
    served nor accepted until the server's own record says that second was played.
    """
    second = cint(second)
    for pair in intervals or []:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        start, end = cint(pair[0]), cint(pair[1])
        if start - CHECKPOINT_TOLERANCE_SECONDS <= second <= end + CHECKPOINT_TOLERANCE_SECONDS:
            return True
    return False


def _percent_complete(attempt_name, total_lessons):
    total = cint(total_lessons)
    if not total:
        return 0.0
    lessons = progress.load(attempt_name).get("lessons") or {}
    done = sum(1 for row in lessons.values() if (row or {}).get("status") == "done")
    return round(min(done, total) * 100.0 / total, 1)


# ------------------------------------------------- doctypes owned by phase two


def _doc_payload(doctype, values):
    """Drop keys the target doctype does not actually declare, and SAY SO.

    The silent version of this shipped a certificate reading ``score 0`` for a
    learner who scored 100. ``"score"`` was written where the field is
    ``score_percent``; the filter quietly discarded it and everything downstream
    reported a real, wrong number rather than failing. Nobody noticed until the
    module was executed for the first time.

    So a dropped key is now logged. The filter still exists — an insert that dies
    with a traceback in front of a learner is worse than a missing value — but
    "defensive" must not mean "silent", or the defence becomes the bug.

    Training Attempt and Training Completion are defined elsewhere in this phase.
    Filtering through the meta means a field renamed there costs a missing value
    rather than an insert that dies with a traceback in front of a learner — and
    the missing value shows up in the record, where somebody will notice it.
    """
    meta = frappe.get_meta(doctype)
    payload = {"doctype": doctype}
    dropped = []
    for key, value in values.items():
        if meta.has_field(key):
            payload[key] = value
        else:
            dropped.append(key)
    if dropped:
        frappe.log_error(
            f"{doctype} has no field(s) {sorted(dropped)} — those values were discarded. "
            "This is almost always a typo in the caller, not a schema that has moved.",
            "Training payload",
        )
    return payload


def _fields_present(doctype, candidates):
    meta = frappe.get_meta(doctype)
    return [field for field in candidates if meta.has_field(field)]


def _status_option(doctype, fieldname, candidates):
    """First of ``candidates`` the Select actually offers, else the first candidate.

    Same reasoning as :func:`_doc_payload`: the status vocabulary of a doctype
    this file does not own must not be able to make it write an invalid value.
    """
    field = frappe.get_meta(doctype).get_field(fieldname)
    options = [o.strip() for o in (field.options or "").split("\n") if o.strip()] if field else []
    for candidate in candidates:
        if candidate in options:
            return candidate
    return candidates[0]


def _current_attempt(user, course, course_version):
    name = frappe.db.get_value(
        "Training Attempt",
        {"user": user, "course": course, "course_version": course_version, "status": "In Progress"},
        "name",
        order_by="creation desc",
    )
    return name or None


def _attempts_by_course(user, course_names):
    """The newest attempt per course, for the catalogue cards."""
    if not course_names:
        return {}
    rows = frappe.get_all(
        "Training Attempt",
        filters={"user": user, "course": ["in", list(course_names)]},
        fields=["name", "course", "course_version", "status", "modified"],
        order_by="modified asc",
    )
    # Ascending, so the last write per course wins and the newest attempt is kept.
    return {row.course: row for row in rows}


# ------------------------------------------------------------------ bootstrap


@frappe.whitelist()
def get_learner_bootstrap():
    """Everything ``/training`` needs to draw itself, in one call.

    One round trip on purpose: the portal is opened on phones on site, and three
    sequential requests over a bad connection is the difference between a page
    that appears and a page somebody gives up on.
    """
    user = _learner()
    if not _runtime_ready():
        return _unavailable()

    profile = _learner_profile(user)
    assignments = _open_assignments(user)
    visible = _visible_course_names(user, profile)
    names = sorted(visible | set(assignments))

    courses = {}
    if names:
        courses = {
            row.name: row
            for row in frappe.get_all(
                "Training Course",
                filters={"name": ["in", names]},
                fields=[
                    "name",
                    "course_title",
                    "slug",
                    "summary",
                    "cover_image",
                    "weight",
                    "category",
                    "estimated_minutes",
                    "current_version",
                    "allow_self_enrollment",
                ],
            )
        }
    attempts = _attempts_by_course(user, names)

    assigned_cards, library = [], []
    for name in names:
        course = courses.get(name)
        if not course:
            continue
        card = _course_card(course, assignments.get(name), attempts.get(name))
        if name in assignments:
            assigned_cards.append(card)
        elif course.weight == "Optional":
            library.append(card)

    # Undated assignments (optional courses that were still assigned by hand) sort
    # last rather than first — an empty due date is not an urgent one.
    assigned_cards.sort(key=lambda card: (card["due_date"] is None, card["due_date"] or "", card["title"]))
    library.sort(key=lambda card: card["title"])

    settings = get_settings()
    return {
        "enabled": True,
        "learner": profile,
        "assigned": assigned_cards,
        "library": library,
        "resume": _resume(user),
        "today": today(),
        # Every key here is read by the player, and every setting the player reads
        # is here. Both halves of that sentence were false: `max_playback_rate` and
        # `doc_min_dwell_seconds` were read by video.js and blocks.js and sent by
        # nothing, so both silently fell back to client constants and the two
        # Training Settings fields did nothing at all. They did not even exist as
        # fields — the client was reading settings nobody could set.
        "settings": {
            "heartbeat_interval_seconds": cint(settings.heartbeat_interval_seconds) or 15,
            "progress_flush_seconds": cint(settings.progress_flush_seconds) or 60,
            "attempt_idle_timeout_minutes": cint(settings.attempt_idle_timeout_minutes) or 120,
            # The client caps this at 1.25 whatever arrives, because
            # progress.clamp_new_seconds truncates claimed seconds to elapsed
            # x MAX_CLAIM_RATE. Sending a larger number cannot raise the ceiling,
            # only lower a learner's credited time — so the setting can tighten
            # the speed menu and never loosen it past what the server will pay for.
            # getattr, not attribute access: these two fields are newer than the
            # doctype, and a deploy puts this code on the site a moment before
            # `bench migrate` puts the columns in the database. The boot payload
            # is the first call every learner makes, so the window matters.
            "max_playback_rate": flt(getattr(settings, "max_playback_rate", None)) or 1.25,
            "doc_min_dwell_seconds": cint(getattr(settings, "doc_min_dwell_seconds", None)) or 20,
        },
    }


def _course_card(course, assignment, attempt):
    total_lessons = cint(
        frappe.db.get_value("Training Course Version", course.current_version, "total_lessons")
    )
    return {
        "course": course.name,
        "title": course.course_title,
        "slug": course.slug or "",
        "summary": course.summary or "",
        "cover_image": course.cover_image or "",
        "weight": course.weight,
        "category": course.category or "",
        "minutes": cint(course.estimated_minutes),
        "version": course.current_version,
        "lessons": total_lessons,
        "self_enrol": bool(cint(course.allow_self_enrollment)),
        "assignment": assignment.name if assignment else None,
        "assignment_status": assignment.status if assignment else None,
        "due_date": assignment.due_date if assignment else None,
        "attempt": attempt.name if attempt else None,
        "attempt_status": attempt.status if attempt else None,
        "percent_complete": _percent_complete(attempt.name, total_lessons) if attempt else 0.0,
    }


def _resume(user):
    """Where this learner left off, if anywhere."""
    row = frappe.db.get_value(
        "Training Attempt",
        {"user": user, "status": "In Progress"},
        ["name", "course", "course_version"],
        as_dict=True,
        order_by="modified desc",
    )
    if not row:
        return None
    return {
        "attempt": row.name,
        "course": row.course,
        "course_title": frappe.db.get_value("Training Course", row.course, "course_title"),
        "lesson_key": _next_lesson_key(row.name, row.course_version),
    }


def _next_lesson_key(attempt_name, course_version):
    """First lesson still in progress, else the first not finished, else None."""
    lessons = progress.load(attempt_name).get("lessons") or {}
    ordered = _version_lessons(course_version)
    for lesson in ordered:
        if (lessons.get(lesson.lesson_key) or {}).get("status") == "in_progress":
            return lesson.lesson_key
    for lesson in ordered:
        if (lessons.get(lesson.lesson_key) or {}).get("status") != "done":
            return lesson.lesson_key
    return None


# --------------------------------------------------------------------- course


@frappe.whitelist()
def get_course(course):
    """Course meta, the published table of contents, and where the learner is."""
    user = _learner()
    if not _runtime_ready():
        return _unavailable()

    profile = _learner_profile(user)
    _require_visible(course, profile)

    doc = frappe.get_doc("Training Course", course)
    version = frappe.db.get_value(
        "Training Course Version",
        doc.current_version,
        ["name", "version_number", "total_lessons", "estimated_minutes", "toc_json", "release_notes"],
        as_dict=True,
    )
    if not version:
        frappe.throw(_("This course has not been published yet."))

    chapters = frappe.get_all(
        "Training Chapter",
        filters={"parent": version.name, "parenttype": "Training Course Version"},
        fields=["chapter_key", "chapter_title", "description"],
        order_by="idx asc",
    )

    assignment = _open_assignments(user).get(course)
    attempt_name = _current_attempt(user, course, version.name)
    attempt = None
    if attempt_name:
        attempt = {
            "attempt": attempt_name,
            "lessons": (progress.load(attempt_name).get("lessons") or {}),
            "next_lesson_key": _next_lesson_key(attempt_name, version.name),
            "percent_complete": _percent_complete(attempt_name, version.total_lessons),
        }

    return {
        "enabled": True,
        "course": {
            "course": doc.name,
            "title": doc.course_title,
            "slug": doc.slug or "",
            "summary": doc.summary or "",
            "cover_image": doc.cover_image or "",
            "weight": doc.weight,
            "minutes": cint(doc.estimated_minutes),
            "self_enrol": bool(cint(doc.allow_self_enrollment)),
        },
        "gates": {
            "passing_score": flt(doc.passing_score),
            "min_video_coverage": flt(doc.min_video_coverage),
            "require_checkpoints_answered": bool(cint(doc.require_checkpoints_answered)),
            "max_attempts": cint(doc.max_attempts),
        },
        "version": {
            "course_version": version.name,
            "version_number": cint(version.version_number),
            "lessons": cint(version.total_lessons),
            "minutes": cint(version.estimated_minutes),
            "release_notes": version.release_notes or "",
        },
        "chapters": chapters,
        "toc": _public_toc(version),
        "assignment": assignment,
        "attempt": attempt,
    }


@frappe.whitelist()
def start_attempt(course):
    """Find or create an In Progress attempt against the *current* published version.

    Find-or-create rather than always-create: a learner who reopens the portal on
    a second device must land back in the same attempt, not start a parallel one
    whose progress the first device will overwrite.

    An attempt against a superseded version is left alone rather than migrated.
    What somebody was graded against has to stay answerable later, and a material
    change raises its own retake assignment.
    """
    user = _learner()
    _require_runtime()

    profile = _learner_profile(user)
    _require_visible(course, profile)

    doc = frappe.get_doc("Training Course", course)
    version = doc.current_version
    if not version or cint(frappe.db.get_value("Training Course Version", version, "docstatus")) != 1:
        frappe.throw(_("This course has not been published yet."))

    assignment = _open_assignments(user).get(course)
    if not assignment:
        if doc.weight == "Required":
            frappe.throw(
                _("This course has to be assigned to you before you can start it. Ask a Training "
                  "Manager to add it to your list.")
            )
        if not cint(doc.allow_self_enrollment):
            frappe.throw(
                _("This course is not open for self-enrolment. Ask a Training Manager to assign it to you.")
            )

    existing = _current_attempt(user, course, version)
    if existing:
        _mark_assignment_started(assignment)
        return _attempt_state(_attempt(existing))

    attempt = frappe.get_doc(
        _doc_payload(
            "Training Attempt",
            {
                "user": user,
                "employee": profile.employee,
                # No `customer` here: Training Attempt does not declare one, and the
                # write was being discarded. The learner's customer is derivable from
                # the user, and Training Completion carries it where it is actually
                # needed for reporting.
                "course": course,
                "course_title": doc.course_title,
                "course_version": version,
                "assignment": assignment.name if assignment else None,
                "status": "In Progress",
                "started_on": now_datetime(),
                "shuffle_seed": frappe.generate_hash(length=16),
                "progress_json": "{}",
            },
        )
    )
    attempt.insert(ignore_permissions=True)
    _mark_assignment_started(assignment)
    return _attempt_state(attempt)


def _mark_assignment_started(assignment):
    """Move a Not Started assignment to In Progress, and nothing else.

    Written with ``db.set_value`` rather than ``save``: the assignment controller
    recomputes Overdue on every save, so saving an overdue row here would flip it
    back to Not Started the moment a learner opened it — quietly clearing the
    escalation that was chasing them.
    """
    if not assignment or assignment.status != "Not Started":
        return
    frappe.db.set_value(
        "Training Assignment", assignment.name, "status", "In Progress", update_modified=False
    )


def _attempt_state(doc):
    data = progress.load(doc.name)
    total = cint(frappe.db.get_value("Training Course Version", doc.course_version, "total_lessons"))
    return {
        "attempt": doc.name,
        "course": doc.course,
        "course_version": doc.course_version,
        "status": doc.status,
        "lessons": data.get("lessons") or {},
        "next_lesson_key": _next_lesson_key(doc.name, doc.course_version),
        "percent_complete": _percent_complete(doc.name, total),
    }


# --------------------------------------------------------------------- lesson


@frappe.whitelist()
def get_lesson(attempt, lesson_key):
    """One lesson's answer-free payload, plus this learner's progress through it.

    One lesson, never the course. A forty-lesson course with transcripts is
    megabytes; a phone on a site with one bar should not download all of it to
    read lesson one.
    """
    _learner()
    _require_runtime()
    doc = _attempt(attempt)

    lesson_name = _lesson_name(doc.course_version, lesson_key)
    payload = grading.public_lesson(lesson_name)
    if not payload:
        frappe.throw(_("This lesson has not been published yet."))

    # The quiz question pool is drawn per run by `grading.draw_quiz`, so the
    # published pool is not sent with the lesson — it would hand over every
    # question including the ones this run was never going to ask.
    quiz = dict(payload.get("quiz") or {})
    quiz.pop("questions", None)
    payload["quiz"] = quiz

    return {
        "enabled": True,
        "attempt": doc.name,
        "lesson": payload,
        "progress": _lesson_progress(doc.name, lesson_key),
        # At most one timestamp per video block — the next one still unanswered.
        # Not the block's whole checkpoint list: see the note on `open_checkpoint`.
        # It is here as well as on the heartbeat because a checkpoint at 0:05
        # would otherwise fire before the first beat ever arrived.
        "next_checkpoints": _next_checkpoints_by_block(doc, lesson_key),
    }


@frappe.whitelist()
def heartbeat(attempt, payload=None):
    """Credit watched seconds and tell the player what to do next.

    Fires roughly every ``heartbeat_interval_seconds`` for every learner who is
    watching something, so it stays deliberately thin: one delegated merge, one
    small checkpoint lookup, no document saves of its own.

    The clamp against the server clock lives in ``progress.record_heartbeat`` —
    the server's own elapsed time is the one quantity a client cannot forge.
    """
    _learner()
    _require_runtime()
    doc = _open_attempt(attempt)

    data = frappe.parse_json(payload) if isinstance(payload, str) else (payload or {})
    if not isinstance(data, dict):
        frappe.throw(_("Malformed progress payload."))
    # Identity is never taken from the payload, whatever the client sent.
    data.pop("user", None)
    data.pop("employee", None)

    result = progress.record_heartbeat(doc.name, _normalise_beat(data)) or {}

    lesson_key = (data.get("lesson_key") or "").strip()
    block_key = (data.get("block_key") or "").strip()
    result["next_checkpoint_at"] = (
        _next_checkpoint_at(doc, lesson_key, block_key) if lesson_key and block_key else None
    )
    result["server_time"] = str(now_datetime())
    return result


def _normalise_beat(data):
    """Rename the player's ``ranges`` to the ``intervals`` ``progress`` stores.

    **Both sides speak half-open ``[start, end]``.** They always did. This
    function used to read the second element as a *length* and store
    ``[start, start + length]``, on the strength of a docstring that claimed the
    wire was ``[start, length]`` for bandwidth reasons — a rationale that was
    never true either, since the two shapes are the same size on the wire.

    That misreading was invisible for exactly one reason: ``rle()`` emits its
    first run starting at second 0 for anybody who plays from the beginning, and
    when ``start`` is 0 the end and the length are the same number. The very
    first run that did *not* start at zero — which is to say the first beat after
    a learner pauses and resumes — was inflated. ``[[17, 32]]`` means fifteen
    seconds; it was stored as ``[17, 49]``, thirty-two. Three beats later the
    intervals had swallowed the rest of the video, coverage pinned at 100%, every
    later beat gained nothing and the meter froze. Nothing raised, because an
    interval is an interval.

    So there is no translation here any more, only a rename, and
    ``tests/test_training_heartbeat_wire.py`` now derives the expected shape from
    ``rle()``'s own worked example rather than restating the assumption made here.

    Also flattens the two counters the player reports structurally:

    * ``seeks`` arrives as ``{forward, backward}``; only **forward** seeks matter
      to integrity. Rewatching is legitimate and must not be counted against
      anybody.
    * ``hidden`` arrives nested under ``discount`` alongside blur/muted/stalled,
      which the player tracks separately for its own display.
    """
    out = dict(data)

    # `ranges` is video.js's name for the pair list; `played` is blocks.js's.
    # Same half-open encoding, two vocabularies, and this function had only ever
    # implemented one of them — so a document block's dwell arrived, produced no
    # `intervals`, and credited nothing. `kind` rides through untouched: the
    # normaliser's job is names, not policy, and `record_heartbeat` is where a
    # document beat is treated differently from a video one.
    ranges = data.get("ranges")
    if ranges is None:
        ranges = data.get("played")
    if ranges is not None and "intervals" not in out:
        intervals = []
        for pair in ranges or []:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            start, end = cint(pair[0]), cint(pair[1])
            # Half-open, so end == start is empty and end < start is a client
            # bug. Both are dropped rather than repaired: a degenerate interval
            # credits nothing anyway, and inventing seconds to fix one would be
            # the same class of mistake this function just spent a release on.
            if end > start:
                intervals.append([start, end])
        out["intervals"] = intervals
    out.pop("ranges", None)
    out.pop("played", None)

    # `claimed` is blocks.js's `claimed_seconds`. The cross-check in
    # `record_heartbeat` that compares the two against each other — the one that
    # would have caught the v1.235.0 half-open misread three releases earlier —
    # was reading a key the document blocks never sent, so it silently checked
    # nothing for them.
    if "claimed_seconds" not in out and data.get("claimed") is not None:
        out["claimed_seconds"] = cint(data.get("claimed"))
    out.pop("claimed", None)

    # video.js sends {forward, backward}; blocks.js sends a plain 0. Only the
    # dict form needs flattening — cint handles the rest downstream.
    seeks = data.get("seeks")
    if isinstance(seeks, dict):
        out["seeks"] = cint(seeks.get("forward"))

    discount = data.get("discount")
    if isinstance(discount, dict) and "hidden" not in out:
        out["hidden"] = cint(discount.get("hidden"))
    out.pop("discount", None)

    # "I have read it." One-way by design on the client and treated the same way
    # here, so a stale beat replayed out of the offline queue cannot un-tick it.
    if data.get("ack") is not None:
        out["ack"] = cint(data.get("ack"))

    return out


def _pending_checkpoints(doc, lesson_key, block_key=None):
    """Unanswered checkpoints for a lesson, earliest first."""
    filters = {"lesson": _lesson_name(doc.course_version, lesson_key)}
    if block_key:
        filters["block_key"] = block_key
    rows = frappe.get_all(
        "Training Checkpoint",
        filters=filters,
        fields=["name", "checkpoint_key", "block_key", "at_seconds"],
        order_by="at_seconds asc",
    )
    answered = _lesson_progress(doc.name, lesson_key).get("checkpoints") or {}
    return [row for row in rows if not cint((answered.get(row.checkpoint_key) or {}).get("answered"))]


def _next_checkpoint_at(doc, lesson_key, block_key):
    pending = _pending_checkpoints(doc, lesson_key, block_key)
    return cint(pending[0].at_seconds) if pending else None


def _next_checkpoints_by_block(doc, lesson_key):
    """``{block_key: at_seconds}`` holding the *next* unanswered checkpoint only."""
    out = {}
    for row in _pending_checkpoints(doc, lesson_key):
        if row.block_key and row.block_key not in out:
            out[row.block_key] = cint(row.at_seconds)
    return out


@frappe.whitelist()
def open_checkpoint(attempt, at, lesson_key=None, block_key=None):
    """The next unanswered checkpoint at or before ``at`` — one, never a list.

    Handing the player every timestamp up front would be handing it a map of
    exactly where to skip to, so the runtime returns a single checkpoint and
    re-arms after each answer.

    Refuses one whose timestamp the stored intervals do not cover: seeking to
    04:12 must not produce the question that lives at 04:12.
    """
    _learner()
    _require_runtime()
    doc = _open_attempt(attempt)

    lesson_key = (lesson_key or "").strip() or _next_lesson_key(doc.name, doc.course_version)
    if not lesson_key:
        frappe.throw(_("No lesson is open."))

    position = cint(at)
    for row in _pending_checkpoints(doc, lesson_key, block_key):
        if cint(row.at_seconds) > position + CHECKPOINT_TOLERANCE_SECONDS:
            break
        # Checked against the checkpoint's *own* block, never against a block_key
        # the caller supplied — otherwise omitting the argument would skip the check.
        if not _covered(_block_intervals(doc.name, lesson_key, row.block_key), row.at_seconds):
            continue
        return {"enabled": True, "checkpoint": _checkpoint_payload(row.name)}
    return {"enabled": True, "checkpoint": None}


def _checkpoint_payload(checkpoint):
    """A checkpoint as a learner may see it.

    The field list is written out rather than taken from the document because
    this is the one place in the runtime that serves checkpoint content, and the
    guarantee being made — that ``is_correct`` and the per-option explanation stay
    on the server — is only as strong as the narrowest projection. The same
    reasoning is written out at length in ``training_author._split_lesson``.

    **The keys are the Training Checkpoint field names, deliberately.** They used
    to be abbreviated — ``at``, ``type``, ``question``, ``rewind`` — and nothing
    read them under those names: ``video.js`` and the builder's preview harness
    (``training_builder.preview_checkpoint``) both spelled them out in full, which
    is to say both consumers independently agreed with the doctype and disagreed
    with this function. An abbreviation is a translation layer, and a translation
    layer with no reader is just a second name for the same thing waiting to be
    read wrong. ``at`` in particular did not say its unit, in a module whose
    entire defect history is units and names.

    Three fields the old payload carried are gone rather than renamed. Each was
    sent, read by nobody, and would have been flagged by the boundary contract
    test (TASK-2026-01184) the moment it lands:

    * ``counts_toward_score`` — scoring happens here. The player was never told
      what to do with it and there is nothing it could correctly do.
    * ``rewind_seconds_on_wrong`` — superseded by ``answer_checkpoint``'s
      ``resume_at``. Sending the ingredients *and* the answer is how the two
      drifted apart in the first place (TASK-2026-01183).
    * ``pause_video`` — ``video.js`` pauses unconditionally when it opens the
      scrim, and the scrim covers the element, so there is no behaviour behind
      this flag to switch. It is not dropped because the author's setting does
      not matter; it is dropped because it does not currently reach anything,
      and a key on the wire is a promise that it does. Restoring it is a player
      change (what does an un-paused checkpoint look like?), not a payload one.
    """
    row = frappe.db.get_value(
        "Training Checkpoint",
        checkpoint,
        [
            "name",
            "checkpoint_key",
            "block_key",
            "at_seconds",
            "question_type",
            "question_text",
            "allow_skip",
            "max_attempts",
        ],
        as_dict=True,
    )
    options = frappe.get_all(
        "Training Answer Option",
        filters={"parent": row.name, "parenttype": "Training Checkpoint"},
        fields=["option_key", "option_text"],
        order_by="idx asc",
    )
    return {
        "checkpoint_key": row.checkpoint_key,
        "block_key": row.block_key,
        "at_seconds": cint(row.at_seconds),
        "question_type": row.question_type,
        "question_text": row.question_text,
        "allow_skip": bool(cint(row.allow_skip)),
        "max_attempts": cint(row.max_attempts),
        "options": [{"option_key": o.option_key, "text": o.option_text} for o in options],
    }


@frappe.whitelist()
def answer_checkpoint(attempt, checkpoint_key, option_keys, response_ms=None):
    """Grade one in-video answer and record that it was given."""
    _learner()
    _require_runtime()
    doc = _open_attempt(attempt)

    row = _checkpoint_row(doc, checkpoint_key)
    lesson_key = _lesson_key_for(doc.course_version, row.lesson)

    # Unconditional, including when the checkpoint carries no block_key: an
    # unattached checkpoint has no intervals to cover it, and refusing is the
    # safe reading of anti-cheat rule 3.
    if not _covered(_block_intervals(doc.name, lesson_key, row.block_key), row.at_seconds):
        frappe.throw(_("That question comes from a part of the video that has not been watched yet."))

    before = _lesson_progress(doc.name, lesson_key).get("checkpoints") or {}
    prior = cint((before.get(checkpoint_key) or {}).get("attempts"))
    if cint(row.max_attempts) and prior >= cint(row.max_attempts):
        frappe.throw(_("You have used all {0} tries at this question.").format(cint(row.max_attempts)))

    result = grading.grade_checkpoint(doc, checkpoint_key, _as_list(option_keys)) or {}

    _record_checkpoint(doc, lesson_key, checkpoint_key, prior, result)

    recorded = (_lesson_progress(doc.name, lesson_key).get("checkpoints") or {}).get(checkpoint_key) or {}
    attempts = cint(recorded.get("attempts"))
    payload = dict(result)
    payload["attempts"] = attempts
    payload["attempts_left"] = max(cint(row.max_attempts) - attempts, 0) if cint(row.max_attempts) else None
    # `rewind` used to be added here as well, as the raw seconds off the
    # checkpoint. Nothing read it, and it disagreed with the `rewind_applied`
    # that `grading._checkpoint_result` puts in the same dict: this one fired on
    # any wrong answer, that one only on a wrong answer with a try left. Two
    # numbers for one decision, one of them wrong, neither of them read — the
    # client now takes `resume_at`/`rewind_applied` and nothing else.
    payload["response_ms"] = cint(response_ms)
    return payload


def _record_checkpoint(doc, lesson_key, checkpoint_key, prior_attempts, result):
    """Write the answer into progress_json, unless grading already did.

    ``grade_checkpoint`` is free to record its own result; both this module and
    ``grading`` hold the attempt. Comparing the attempt counter across the call
    is what stops a single answer being counted twice and burning a learner's
    last try — cheaper and more obvious than a flag the two modules would have to
    keep agreeing about.
    """
    data = progress.load(doc.name)
    lessons = data.setdefault("lessons", {})
    lesson = lessons.setdefault(lesson_key, {"status": "in_progress"})
    checkpoints = lesson.setdefault("checkpoints", {})
    entry = checkpoints.setdefault(checkpoint_key, {})

    if cint(entry.get("attempts")) != prior_attempts:
        return  # grading recorded it; leave the counter alone

    entry["answered"] = 1
    entry["correct"] = 1 if result.get("correct") else 0
    entry["attempts"] = prior_attempts + 1
    # Stringified: progress_json is written with json.dumps, which has no idea
    # what a datetime is, and the failure would surface as a 500 on an answer.
    entry["at"] = str(now_datetime())
    progress.save(doc.name, data, force=True)


def _checkpoint_row(doc, checkpoint_key):
    """Resolve a checkpoint key *within this attempt's version*.

    Scoped by lesson rather than by the checkpoint's own ``course_version`` field
    so it cannot matter whether that field was populated by the authoring flow —
    and so a key from another course can never be answered against this attempt.
    """
    lessons = [row.name for row in _version_lessons(doc.course_version)]
    row = None
    if lessons:
        row = frappe.db.get_value(
            "Training Checkpoint",
            {"lesson": ["in", lessons], "checkpoint_key": checkpoint_key},
            ["name", "lesson", "block_key", "at_seconds", "max_attempts", "rewind_seconds_on_wrong"],
            as_dict=True,
        )
    if not row:
        frappe.throw(_("That question is not part of this course."))
    return row


def _lesson_key_for(course_version, lesson_name):
    for row in _version_lessons(course_version):
        if row.name == lesson_name:
            return row.lesson_key
    frappe.throw(_("That lesson is not part of this course."))


@frappe.whitelist()
def complete_lesson(attempt, lesson_key):
    """Mark a lesson finished, if every gate on it is actually met."""
    _learner()
    _require_runtime()
    doc = _open_attempt(attempt)
    _lesson_name(doc.course_version, lesson_key)  # rejects a key from another course

    verdict = grading.evaluate_gates(doc, lesson_key) or {}
    if not verdict.get("ok"):
        # The reasons are the point — "not finished" without saying which gate is
        # missing is what turns a lesson into a support call.
        reasons = verdict.get("reasons") or [_("This lesson is not finished yet.")]
        frappe.throw("<br>".join(reasons), title=_("Not finished yet"))

    data = progress.load(doc.name)
    lesson = data.setdefault("lessons", {}).setdefault(lesson_key, {})
    lesson["status"] = "done"
    progress.save(doc.name, data, force=True)

    return {
        "ok": True,
        "lesson_key": lesson_key,
        "coverage": verdict.get("coverage"),
        "next_lesson_key": _next_lesson_key(doc.name, doc.course_version),
    }


# ----------------------------------------------------------------------- quiz


@frappe.whitelist()
def get_quiz(attempt, lesson_key):
    """Draw this run's questions. Refuses once the attempt limit is used up."""
    _learner()
    _require_runtime()
    doc = _open_attempt(attempt)
    lesson_name = _lesson_name(doc.course_version, lesson_key)

    limit = cint(frappe.db.get_value("Training Course", doc.course, "max_attempts"))
    runs = cint((_lesson_progress(doc.name, lesson_key).get("quiz") or {}).get("runs"))
    if limit and runs >= limit:
        frappe.throw(
            _("You have used all {0} attempts at this quiz. A Training Manager can reset it for you.")
            .format(limit)
        )

    questions = grading.draw_quiz(doc, lesson_key, runs + 1)
    quiz = (grading.public_lesson(lesson_name) or {}).get("quiz") or {}
    return {
        "enabled": True,
        "run": runs + 1,
        "questions": questions,
        "pass_score": flt(quiz.get("pass_score")) or flt(
            frappe.db.get_value("Training Course", doc.course, "passing_score")
        ),
        "attempts_left": max(limit - runs, 0) if limit else None,
    }


@frappe.whitelist()
def submit_quiz(attempt, lesson_key, answers):
    """Grade a quiz run. Any score the client sent with it is ignored."""
    _learner()
    _require_runtime()
    doc = _open_attempt(attempt)
    _lesson_name(doc.course_version, lesson_key)

    parsed = frappe.parse_json(answers) if isinstance(answers, str) else answers
    if not isinstance(parsed, dict):
        frappe.throw(_("Malformed quiz answers."))

    before = _lesson_progress(doc.name, lesson_key).get("quiz") or {}
    prior_runs = cint(before.get("runs"))

    result = grading.grade_quiz(doc, lesson_key, parsed) or {}
    score = flt(result.get("score"))

    data = progress.load(doc.name)
    quiz = data.setdefault("lessons", {}).setdefault(lesson_key, {}).setdefault("quiz", {})
    if cint(quiz.get("runs")) == prior_runs:
        # Same double-record guard as the checkpoints; see _record_checkpoint.
        quiz["runs"] = prior_runs + 1
        quiz["best"] = max(flt(quiz.get("best")), score)
    progress.save(doc.name, data, force=True)

    limit = cint(frappe.db.get_value("Training Course", doc.course, "max_attempts"))
    runs = cint(quiz.get("runs"))
    attempts_left = max(limit - runs, 0) if limit else None

    payload = dict(result)
    payload["run"] = runs
    payload["best"] = flt(quiz.get("best"))
    payload["attempts_left"] = attempts_left
    # `attempts_used` and `max_attempts` let the player say "Attempt 2 of 3",
    # which `attempts_left` alone cannot phrase — and cannot phrase at all on an
    # unlimited course, where it is None.
    payload["attempts_used"] = runs
    payload["max_attempts"] = limit or None
    # Said out loud rather than inferred.
    #
    # `quiz.js` has always asked for explicit permission and treated silence as
    # no, which is the right instinct — offering a retry the server will refuse
    # spends a learner's goodwill on a dead button. But nothing ever said yes,
    # so a learner who failed with two attempts in hand was shown no way to use
    # them. The rule is one line and it belongs here, where `max_attempts` and
    # the run count already are, not reconstructed on the client from parts.
    #
    # Passing does not offer a retry. `best` keeps the highest score, so a resit
    # cannot cost anything, but "Try again" under a pass reads as though the
    # pass did not count.
    payload["can_retry"] = not result.get("passed") and (attempts_left is None or attempts_left > 0)
    return payload


# ----------------------------------------------------------------- completion


@frappe.whitelist()
def finish_attempt(attempt):
    """Check every lesson, and on a pass write the completion record.

    Does not throw when a lesson is outstanding — the player shows the list and
    sends the learner back to it. An exception here would tell somebody who has
    watched nine of ten lessons only that something went wrong.
    """
    _learner()
    _require_runtime()
    doc = _attempt(attempt)
    if doc.status != "In Progress":
        # Re-opening something already finished. The score comes off the record
        # rather than being recomputed: it is what the learner was graded on, and
        # recomputing could quietly disagree with the completion certificate.
        return _finished_attempt_payload(
            doc,
            passed=doc.status != "Failed",
            score=_recorded_score(doc),
            outstanding=[],
            completion=_completion_for(doc),
        )

    outstanding, scores, coverages = [], [], []
    for lesson in _version_lessons(doc.course_version):
        verdict = grading.evaluate_gates(doc, lesson.lesson_key) or {}
        # Taken from the gate rather than recomputed, so the number filed on the
        # permanent record is the same one the learner was shown and graded on.
        # Only lessons that actually had a gated video contribute -- see `gated`.
        if verdict.get("gated"):
            coverages.append(flt(verdict.get("coverage")))
        if not verdict.get("ok"):
            outstanding.append(
                {
                    "lesson_key": lesson.lesson_key,
                    "title": lesson.lesson_title,
                    "reasons": verdict.get("reasons") or [],
                }
            )
        best = (_lesson_progress(doc.name, lesson.lesson_key).get("quiz") or {}).get("best")
        if best is not None:
            scores.append(flt(best))

    # A course that demands hands-on verification does not complete on a quiz.
    # This check was missing entirely until the module was first executed: the
    # lesson gates were enforced, the sign-off was not, and a technician could be
    # certified as competent to drain a basin having only answered questions about
    # it. Deliberately reported as `outstanding` rather than thrown, like every
    # other unmet gate, so the player shows the learner what is left.
    if _signoff_outstanding(doc):
        outstanding.append(
            {
                "lesson_key": None,
                "title": _("Supervisor sign-off"),
                "reasons": [
                    _("A supervisor still has to confirm you can do this in practice. "
                      "Ask them to record a sign-off once they have watched you.")
                ],
            }
        )

    if outstanding:
        return _finished_attempt_payload(
            doc,
            passed=False,
            score=_recorded_score(doc),
            outstanding=outstanding,
            completion=None,
        )

    # A course with no quiz anywhere is passed on its gates alone; recording it as
    # 0% would make every transcript of a video-only course look like a failure.
    score = round(statistics.mean(scores), 1) if scores else 100.0
    # None, not 0, when the course had nothing to watch: the field is written only
    # when it means something. Nothing populated it at all until the module was
    # first executed, so every completion recorded 0% coverage -- including for a
    # learner who had watched every second of a safety video. The gate itself was
    # never affected (it computes from the stored intervals), but the completion is
    # the artefact you would produce in a dispute, and it was understating people.
    coverage = round(statistics.mean(coverages), 2) if coverages else None
    completion = _issue_completion(doc, score, coverage)
    _refresh_attempt_summary(doc, coverage)

    # Filtered through the meta for the same reason as `_doc_payload`: db_set
    # writes the columns it is given, so a field this file assumes and the
    # doctype does not have would be a hard SQL error on the last click of a
    # course somebody has just passed.
    doc.db_set(
        {
            key: value
            for key, value in {
                "status": _status_option("Training Attempt", "status", ["Passed", "Completed"]),
                # score_percent, not "score" -- see _doc_payload.
                "score_percent": score,
                "completed_on": now_datetime(),
            }.items()
            if doc.meta.has_field(key)
        }
    )
    _close_assignment(doc, completion)

    return _finished_attempt_payload(
        doc,
        passed=True,
        score=score,
        outstanding=[],
        completion=completion,
    )


def _recorded_score(doc):
    """The score already on the attempt, or ``None`` if there is not one yet.

    ``None`` rather than 0.0 on purpose. Zero is a real score a learner can get,
    and the player renders whatever it is handed — so a missing score dressed up
    as a zero is indistinguishable from a genuine nought out of ten. Absent says
    absent, and the caller decides what to show.
    """
    if not doc.meta.has_field("score_percent"):
        return None
    score = getattr(doc, "score_percent", None)
    return flt(score) if score is not None else None


def _finished_attempt_payload(doc, passed, score, outstanding, completion):
    """The one shape :func:`finish_attempt` returns, from all three of its exits.

    It had three shapes, and the differences between them were invisible until
    somebody hit the right one:

    * the pass carried ``score``;
    * **the already-finished early return did not** — so re-opening a course
      passed at full marks rendered "Passed – 0%", because ``pct(undefined)`` is
      0 and the player draws what it is given (TASK-2026-01180);
    * the outstanding-gates return carried neither ``score`` nor ``completion``,
      and the client reads both.

    Same shape as the v1.217.0 certificate bug: present, plausible, wrong. Three
    exits that each assembled their own dict is how one of them ends up missing a
    key nobody notices, so there is one assembler now and the exits differ only in
    what they pass to it.
    """
    return {
        "passed": bool(passed),
        "attempt": doc.name,
        "status": doc.status,
        "score": score,
        "outstanding": outstanding or [],
        "completion": completion,
    }



def _signoff_outstanding(doc):
    """True when the course wants a supervisor sign-off and does not have one.

    "Has one" means a **submitted** Training Signoff recording *Competent* for
    this learner on this course. A draft is a request, not a verification, and
    "Needs More Practice" is the supervisor explicitly declining.

    Matched on the course rather than the course version on purpose: somebody who
    was watched draining a basin last month has been watched draining a basin. A
    material change to the *content* supersedes their completion and sends them
    back through the material, which is the right lever — re-observing them
    physically because a paragraph was rewritten is not.

    Returns False when the doctype is absent, so a site that has not migrated
    Phase 4 can still finish a course rather than being unable to complete
    anything at all.
    """
    if not cint(frappe.db.get_value("Training Course", doc.course, "require_supervisor_signoff")):
        return False
    if not frappe.db.exists("DocType", "Training Signoff"):
        frappe.log_error(
            f"{doc.course} requires a supervisor sign-off but Training Signoff is not migrated "
            "on this site; the requirement cannot be enforced.",
            "Training sign-off",
        )
        return False
    return not frappe.db.exists(
        "Training Signoff",
        {"course": doc.course, "user": doc.user, "outcome": "Competent", "docstatus": 1},
    )


def _competent_signoff(doc):
    """The submitted Competent sign-off backing this completion, if any."""
    if not frappe.db.exists("DocType", "Training Signoff"):
        return None
    return frappe.db.get_value(
        "Training Signoff",
        {"course": doc.course, "user": doc.user, "outcome": "Competent", "docstatus": 1},
        "name",
    )

def _refresh_attempt_summary(doc, coverage=None):
    """Write the attempt's denormalised counters from the progress blob.

    ``quiz_runs``, ``checkpoints_answered``, ``checkpoints_correct`` and
    ``video_coverage_percent`` are declared on Training Attempt and, until the
    module was first run end to end, nothing ever wrote any of them. Every attempt
    in the system read zero on all four -- not missing, which somebody would have
    queried, but a confident zero.

    They are a cache of ``progress_json``, so they are derived here rather than
    incremented as things happen: a counter that is added to on every heartbeat
    drifts the first time a write is lost, and this one is cheap enough to just
    recompute. Deliberately *not* called from the heartbeat path -- that is a write
    in a loop, which is the whole reason progress lives in a single blob.

    Never allowed to break a completion. The attempt is already passed and the
    record already written by the time this runs; failing here would take a course
    away from somebody who finished it, to fix a reporting column.
    """
    try:
        data = progress.load(doc.name) or {}
        runs = answered = correct = 0
        for lesson in (data.get("lessons") or {}).values():
            if not isinstance(lesson, dict):
                continue
            runs += cint((lesson.get("quiz") or {}).get("runs"))
            for state in (lesson.get("checkpoints") or {}).values():
                if not isinstance(state, dict):
                    continue
                if cint(state.get("attempts")):
                    answered += 1
                if cint(state.get("correct")):
                    correct += 1

        values = {
            "quiz_runs": runs,
            "checkpoints_answered": answered,
            "checkpoints_correct": correct,
        }
        if coverage is not None:
            values["video_coverage_percent"] = flt(coverage)
        # update_modified=False for the same reason as every other write on this
        # path: these are counters, not edits, and churning `modified` on the
        # attempt would show up as somebody having touched the record.
        payload = {k: v for k, v in values.items() if doc.meta.has_field(k)}
        for field, value in payload.items():
            frappe.db.set_value("Training Attempt", doc.name, field, value, update_modified=False)
    except Exception:
        frappe.log_error(
            f"Could not refresh the summary counters on Training Attempt {doc.name}. "
            "The completion itself was written; only the denormalised columns are stale.",
            "Training attempt summary",
        )


def _issue_completion(doc, score, coverage=None):
    """Create and submit the Training Completion for a passed attempt.

    Submitted immediately: the completion *is* the compliance artefact, and a
    draft one proves nothing. Idempotent on the attempt so a double-click cannot
    mint two records for one pass.
    """
    if not frappe.db.exists("DocType", "Training Completion"):
        # Guarded rather than assumed: the runtime must still let somebody finish a
        # course on a site where the completion doctype has not migrated yet.
        frappe.log_error(
            f"Attempt {doc.name} passed but Training Completion does not exist on this site.",
            "Training completion",
        )
        return None

    existing = _completion_for(doc)
    if existing:
        return existing

    version = frappe.db.get_value(
        "Training Course Version", doc.course_version, ["version_number", "content_hash"], as_dict=True
    ) or frappe._dict()

    completion = frappe.get_doc(
        _doc_payload(
            "Training Completion",
            {
                "user": doc.user,
                "employee": getattr(doc, "employee", None) or "",
                "customer": getattr(doc, "customer", None) or "",
                "course": doc.course,
                # course_title_SNAPSHOT: the point of the field is that it survives the
                # course being renamed, so it is written here rather than fetched live.
                "course_title_snapshot": frappe.db.get_value(
                    "Training Course", doc.course, "course_title"
                ),
                "course_version": doc.course_version,
                "version_number": cint(version.get("version_number")),
                "content_hash": version.get("content_hash") or "",
                "attempt": doc.name,
                # The sign-off that unlocked this, so the completion carries its own
                # evidence rather than requiring a second lookup to prove it was met.
                "signoff": _competent_signoff(doc) or "",
                "status": _status_option("Training Completion", "status", ["Valid"]),
                # score_percent, not "score" -- the field is score_percent and the
                # payload filter discarded the typo silently, which is how a learner
                # who scored 100 got a certificate reading 0.
                "score_percent": score,
                "completed_on": now_datetime(),
            },
        )
    )
    if coverage is not None and completion.meta.has_field("video_coverage_percent"):
        completion.video_coverage_percent = coverage
    completion.insert(ignore_permissions=True)
    completion.submit()
    return completion.name


def _completion_for(doc):
    if not frappe.db.exists("DocType", "Training Completion"):
        return None
    if not frappe.get_meta("Training Completion").has_field("attempt"):
        return None
    return frappe.db.get_value("Training Completion", {"attempt": doc.name, "docstatus": 1}, "name")


def _close_assignment(doc, completion):
    """Mark the assignment Completed and point it at the record that proves it."""
    assignment = getattr(doc, "assignment", None) or frappe.db.get_value(
        "Training Assignment",
        {"user": doc.user, "course": doc.course, "status": ["in", OPEN_STATUSES]},
        "name",
    )
    if not assignment:
        return
    row = frappe.get_doc("Training Assignment", assignment)
    row.status = "Completed"
    if completion and row.meta.has_field("completion"):
        row.completion = completion
    row.save(ignore_permissions=True)


@frappe.whitelist()
def get_my_transcript():
    """This learner's own completions. Never anybody else's."""
    user = _learner()
    if not _runtime_ready():
        return _unavailable()
    if not frappe.db.exists("DocType", "Training Completion"):
        return {"enabled": True, "completions": []}

    fields = _fields_present(
        "Training Completion",
        [
            "name",
            "course",
            "course_title",
            "course_version",
            "version_number",
            "completed_on",
            "score",
            "status",
            "expires_on",
            "certificate",
        ],
    )
    rows = frappe.get_all(
        "Training Completion",
        filters={"user": user, "docstatus": 1},
        fields=fields or ["name", "course"],
        order_by="creation desc",
    )
    for row in rows:
        if not row.get("course_title"):
            row["course_title"] = frappe.db.get_value("Training Course", row.get("course"), "course_title")
    return {"enabled": True, "completions": rows}


# ---------------------------------------------------------------------- media


@frappe.whitelist()
def get_media_url(attempt, block_key):
    """A short-lived playback URL for one video block.

    Returns ``url: None`` with a reason rather than throwing when video delivery
    is not configured. The player then says the video is unavailable, which is a
    far better first lesson than a traceback — and an unconfigured bucket is a
    setup state, not a bug.
    """
    _learner()
    _require_runtime()
    doc = _attempt(attempt)

    lessons = [row.name for row in _version_lessons(doc.course_version)]
    block = None
    if lessons:
        block = frappe.db.get_value(
            "Training Content Block",
            {"parent": ["in", lessons], "parenttype": "Training Lesson", "block_key": block_key},
            ["block_type", "video_asset", "embed_url", "poster_image", "video_duration_seconds"],
            as_dict=True,
        )
    if not block:
        frappe.throw(_("That video is not part of this course."))

    if block.block_type == EMBED_BLOCK_TYPE:
        # Embeds are cross-origin and carry no coverage gate; the player renders
        # the provider's own iframe rather than a <video> element.
        return {
            "url": None,
            "embed_url": block.embed_url or "",
            "reason": "external_embed",
            "duration_seconds": cint(block.video_duration_seconds),
        }

    url = gcs_media.signed_url_for_asset(block.video_asset)
    return {
        "url": url,
        "embed_url": "",
        "reason": None if url else "not_available",
        "poster": block.poster_image or "",
        "duration_seconds": cint(block.video_duration_seconds)
        or cint(frappe.db.get_value("Training Video Asset", block.video_asset, "duration_seconds")),
        "expires_in_minutes": cint(get_settings().signed_url_ttl_minutes) or 15,
    }


# -------------------------------------------------------------------- helpers


def _as_list(value):
    """Option keys arrive as a JSON array, a CSV string, or a bare key."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            value = frappe.parse_json(text)
        else:
            return [part.strip() for part in text.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]
