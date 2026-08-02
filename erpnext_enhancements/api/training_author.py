# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Authoring endpoints for the Training module — draft, publish, assign.

Answers one question: *how does a course go from something an author is writing to
something learners are doing, without an author ever being able to push
half-finished content at a live audience?*

The mechanism is the version's docstatus. Authors edit ``docstatus 0``; the
runtime only ever reads ``docstatus 1``. :func:`publish_version` is the single
door between them, and it is the only function here restricted to Training
Manager. Everything else an author may do.

Things this module is careful about, several of which look like bugs until you
know why:

* **``create_draft_version`` copies keys, not just content.** Every
  ``lesson_key`` / ``block_key`` / ``checkpoint_key`` is carried across verbatim.
  Regenerating them would be simpler and would silently reset every in-flight
  learner to lesson one on the next typo fix.
* **``publish_version`` materializes two payloads per lesson** — the answer-free
  one the player renders, and the answer key at permlevel 1. It never serves from
  the live rows. An author fixing a typo in a question next month must not
  retroactively change what a completed learner was graded against.
* **The Minor/Material decision is applied here, not at read time.** Deciding it
  lazily would mean a completion's validity depended on when you asked.
* **Assignment fan-out is enqueued above 25 targets.** Below that it is faster to
  do it inline than to explain to the author why nothing appeared to happen.

Indentation is 4 spaces, matching the majority of ``api/``.
"""

import hashlib
import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, now_datetime, today

from erpnext_enhancements.training.doctype.training_course_version.training_course_version import (
    MATERIAL_CHANGE,
    MINOR_EDIT,
)

# Above this many people, fan the assignment out to a background job. Below it,
# the wait is shorter than the round trip of telling the user to come back later.
INLINE_ASSIGN_LIMIT = 25

AUTHOR_ROLES = ("Training Author", "Training Manager", "System Manager")


# --------------------------------------------------------------------- guards


def _require_author():
    if not set(AUTHOR_ROLES) & set(frappe.get_roles()):
        frappe.throw(_("Not permitted."), frappe.PermissionError)


def _require_manager():
    """Publishing, assigning and waiving are Training Manager actions.

    Checked here as well as in the doctype permissions because a whitelisted
    method is callable directly, whatever the desk UI chooses to show.
    """
    if not {"Training Manager", "System Manager"} & set(frappe.get_roles()):
        frappe.throw(_("Only a Training Manager can do this."), frappe.PermissionError)


def _draft(course_version):
    """Load a version and refuse if it is not an editable draft."""
    doc = frappe.get_doc("Training Course Version", course_version)
    if cint(doc.docstatus) != 0:
        frappe.throw(
            _("{0} is already published and is frozen. Create a new draft version to make changes.").format(
                course_version
            )
        )
    doc.check_permission("write")
    return doc


# ------------------------------------------------------------------ versioning


@frappe.whitelist()
def create_draft_version(course, change_type=None):
    """Clone the live version into a fresh editable draft, and return its name.

    The clone preserves every stable key, which is what lets a minor edit leave
    in-flight learners exactly where they were.
    """
    _require_author()
    course_doc = frappe.get_doc("Training Course", course)
    course_doc.check_permission("write")

    existing = frappe.db.exists("Training Course Version", {"course": course, "docstatus": 0})
    if existing:
        frappe.throw(
            _("{0} already has an open draft ({1}). Finish or discard that one first — two drafts of "
              "the same course cannot both become version {2}.").format(
                course, existing, cint(frappe.db.get_value("Training Course Version", existing, "version_number"))
            )
        )

    source = None
    if course_doc.current_version:
        source = frappe.get_doc("Training Course Version", course_doc.current_version)

    draft = frappe.new_doc("Training Course Version")
    draft.course = course
    draft.change_type = change_type or ""
    if source:
        for row in source.chapters or []:
            draft.append(
                "chapters",
                {
                    "chapter_key": row.chapter_key,
                    "chapter_title": row.chapter_title,
                    "description": row.description,
                },
            )
    draft.insert(ignore_permissions=True)

    if source:
        _clone_lessons(source.name, draft.name)

    frappe.get_doc("Training Course", course).refresh_status()
    return draft.name


def _clone_lessons(from_version, to_version):
    """Deep-copy lessons, blocks and checkpoints, keeping every stable key."""
    for name in frappe.get_all("Training Lesson", filters={"course_version": from_version}, pluck="name"):
        source = frappe.get_doc("Training Lesson", name)
        clone = frappe.copy_doc(source, ignore_no_copy=False)
        clone.course_version = to_version
        # copy_doc keeps field values, but the published payloads belong to the
        # version that produced them — carrying them over would serve last
        # version's content from a draft that has since been edited.
        clone.published_content_json = ""
        clone.answer_key_json = ""
        clone.lesson_key = source.lesson_key
        # strict=True on purpose. `block_key` is no_copy, so copy_doc blanks it and
        # these keys have to be carried across by position. If the two row counts
        # ever disagreed, a silent zip would truncate and leave later blocks with
        # fresh keys — which is precisely the failure ("everyone bumped back to
        # lesson one after a typo fix") this whole mechanism exists to prevent.
        for target, origin in zip(clone.blocks or [], source.blocks or [], strict=True):
            target.block_key = origin.block_key
        clone.insert(ignore_permissions=True)

        for cp_name in frappe.get_all("Training Checkpoint", filters={"lesson": name}, pluck="name"):
            cp_source = frappe.get_doc("Training Checkpoint", cp_name)
            cp = frappe.copy_doc(cp_source, ignore_no_copy=False)
            cp.lesson = clone.name
            cp.checkpoint_key = cp_source.checkpoint_key
            for target, origin in zip(cp.options or [], cp_source.options or [], strict=True):
                target.option_key = origin.option_key
            cp.insert(ignore_permissions=True)


@frappe.whitelist()
def submit_for_review(course_version, notes=None):
    """Hand a draft to a Training Manager.

    Refuses while any AI-drafted question is still unreviewed. That gate lives at
    the workflow boundary rather than in the builder UI on purpose: "never publish
    an unreviewed AI answer key into a compliance quiz" has to be true even when
    somebody calls the endpoint directly.
    """
    _require_author()
    doc = _draft(course_version)

    unreviewed = _unreviewed_ai_questions(course_version)
    if unreviewed:
        frappe.throw(
            _("{0} AI-drafted question(s) have not been reviewed yet: {1}. Accept or reject each one "
              "before sending this for review.").format(len(unreviewed), ", ".join(unreviewed[:5]))
        )

    if notes:
        doc.db_set("release_notes", notes, update_modified=False)
    doc.db_set(
        {
            "submitted_for_review": 1,
            "submitted_for_review_on": now_datetime(),
            "submitted_for_review_by": frappe.session.user,
        },
        update_modified=False,
    )
    frappe.get_doc("Training Course", doc.course).refresh_status()
    return {"course_version": doc.name, "status": "In Review"}


def _unreviewed_ai_questions(course_version):
    """Names of AI-drafted questions in this version that no human has accepted."""
    lessons = frappe.get_all("Training Lesson", filters={"course_version": course_version}, pluck="name")
    if not lessons:
        return []
    rows = frappe.get_all(
        "Training Quiz Question",
        filters={"parent": ["in", lessons], "parenttype": "Training Lesson"},
        pluck="question",
    )
    if not rows:
        return []
    return frappe.get_all(
        "Training Question",
        filters={"name": ["in", rows], "ai_generated": 1, "ai_reviewed_by": ["is", "not set"]},
        pluck="name",
    )


@frappe.whitelist()
def publish_version(course_version, change_type, release_notes=None):
    """Freeze a draft and make it the live version. Training Manager only.

    ``change_type`` is the author's Minor/Material call and it is not optional —
    see the reasoning on ``TrainingCourseVersion._require_change_type``.
    """
    _require_manager()

    if change_type not in (MINOR_EDIT, MATERIAL_CHANGE):
        frappe.throw(_("Choose whether this is a minor edit or a material change."))

    doc = _draft(course_version)
    unreviewed = _unreviewed_ai_questions(course_version)
    if unreviewed:
        frappe.throw(
            _("{0} AI-drafted question(s) are still unreviewed. They cannot go live.").format(len(unreviewed))
        )

    totals = _materialize_lessons(doc.name)

    doc.change_type = change_type
    if release_notes:
        doc.release_notes = release_notes
    doc.total_lessons = totals["lessons"]
    doc.total_questions = totals["questions"]
    doc.estimated_minutes = totals["minutes"]
    doc.toc_json = json.dumps(totals["toc"], separators=(",", ":"))
    doc.content_hash = totals["content_hash"]
    doc.save(ignore_permissions=True)
    doc.submit()

    frappe.db.set_value(
        "Training Course", doc.course, "reviewed_by", frappe.session.user, update_modified=False
    )

    superseded = 0
    if change_type == MATERIAL_CHANGE:
        superseded = _supersede_previous_completions(doc)

    course = frappe.get_doc("Training Course", doc.course)
    course.refresh_status()

    if cint(course.auto_assign) and course.weight == "Required":
        frappe.enqueue(
            "erpnext_enhancements.training.assignment.sync_course",
            queue="long",
            enqueue_after_commit=True,
            course_name=course.name,
        )

    return {
        "course_version": doc.name,
        "version_number": doc.version_number,
        "change_type": change_type,
        "superseded_completions": superseded,
    }


def _materialize_lessons(course_version):
    """Write each lesson's answer-free payload and its separate answer key.

    Written with ``db_set`` rather than ``save`` because the lesson controller
    refuses edits once its version is submitted, and because these two fields are
    machine-owned — re-running the lesson's own validation here would achieve
    nothing and could reject a lesson the author has already had accepted.
    """
    lessons = frappe.get_all(
        "Training Lesson",
        filters={"course_version": course_version},
        fields=["name"],
        order_by="chapter_key asc, idx_in_chapter asc, creation asc",
    )

    toc = []
    question_count = 0
    minutes = 0
    digest = hashlib.sha256()

    for row in lessons:
        lesson = frappe.get_doc("Training Lesson", row.name)
        public, key = _split_lesson(lesson)

        frappe.db.set_value(
            "Training Lesson",
            lesson.name,
            {
                "published_content_json": json.dumps(public, separators=(",", ":")),
                "answer_key_json": json.dumps(key, separators=(",", ":")),
            },
            update_modified=False,
        )

        question_count += len(key.get("quiz", {}))
        minutes += cint(lesson.estimated_minutes)
        toc.append(
            {
                "lesson": lesson.name,
                "lesson_key": lesson.lesson_key,
                "chapter_key": lesson.chapter_key or "",
                "title": lesson.lesson_title,
                "minutes": cint(lesson.estimated_minutes),
                "has_quiz": cint(lesson.has_quiz),
                "blocks": len(lesson.blocks or []),
            }
        )
        digest.update(json.dumps(public, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        digest.update(json.dumps(key, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    return {
        "lessons": len(lessons),
        "questions": question_count,
        "minutes": minutes,
        "toc": toc,
        "content_hash": digest.hexdigest(),
    }


def _split_lesson(lesson):
    """Return ``(public_payload, answer_key)`` for one lesson.

    This is the single place a learner-facing payload is built. Nothing else in
    the module may assemble one, because the guarantee being made — that
    ``is_correct`` and ``correct_text_answers`` never reach a browser — is only as
    strong as the number of functions capable of breaking it.
    """
    public = {
        "lesson_key": lesson.lesson_key,
        "title": lesson.lesson_title,
        "summary": lesson.summary or "",
        "minutes": cint(lesson.estimated_minutes),
        "allow_questions": cint(lesson.allow_questions),
        "blocks": [],
        "quiz": {
            "enabled": cint(lesson.has_quiz),
            "questions_to_ask": cint(lesson.quiz_questions_to_ask),
            "pass_score": cint(lesson.quiz_pass_score),
            "shuffle_questions": cint(lesson.quiz_shuffle_questions),
            "shuffle_options": cint(lesson.quiz_shuffle_options),
            "questions": [],
        },
        "checkpoint_count": {},
    }
    key = {"lesson_key": lesson.lesson_key, "quiz": {}, "checkpoints": {}}

    for block in lesson.blocks or []:
        public["blocks"].append(
            {
                "block_key": block.block_key,
                "type": block.block_type,
                "heading": block.heading or "",
                "html": frappe.utils.sanitize_html(block.content or ""),
                "image": block.image or "",
                "file": block.file or "",
                "video_asset": block.video_asset or "",
                "duration_s": cint(block.video_duration_seconds),
                "embed_url": block.embed_url or "",
                "poster": block.poster_image or "",
                "caption": block.caption or "",
                "required": cint(block.required_for_completion),
                "min_coverage": cint(block.min_coverage_percent),
                "checkpoints_enabled": cint(block.checkpoints_enabled),
            }
        )

    for cp in frappe.get_all(
        "Training Checkpoint",
        filters={"lesson": lesson.name},
        fields=["name", "checkpoint_key", "block_key", "at_seconds", "question_text", "question_type",
                "explanation", "pause_video", "allow_skip", "max_attempts", "rewind_seconds_on_wrong",
                "counts_toward_score"],
        order_by="at_seconds asc",
    ):
        options = frappe.get_all(
            "Training Answer Option",
            filters={"parent": cp.name, "parenttype": "Training Checkpoint"},
            fields=["option_key", "option_text", "is_correct", "explanation"],
            order_by="idx asc",
        )
        # NB: the per-option explanation stays in the key, never in `public`. It
        # is written to say *why* an option is right or wrong, so shipping it up
        # front hands over the answer as surely as `is_correct` would.
        # Only the count reaches the client. A list of timestamps is a map of
        # exactly where to skip to; the runtime hands out the next one at a time.
        public["checkpoint_count"][cp.block_key] = public["checkpoint_count"].get(cp.block_key, 0) + 1
        key["checkpoints"][cp.checkpoint_key] = {
            "checkpoint": cp.name,
            "block_key": cp.block_key,
            "at": cint(cp.at_seconds),
            "type": cp.question_type,
            "question": cp.question_text,
            "explanation": cp.explanation or "",
            "pause": cint(cp.pause_video),
            "allow_skip": cint(cp.allow_skip),
            "max_attempts": cint(cp.max_attempts),
            "rewind": cint(cp.rewind_seconds_on_wrong),
            "scored": cint(cp.counts_toward_score),
            "options": [
                {"option_key": o.option_key, "text": o.option_text, "explanation": o.explanation or ""}
                for o in options
            ],
            "correct": sorted(o.option_key for o in options if cint(o.is_correct)),
        }
        # The checkpoint's own question and options are served from the key at
        # runtime, one checkpoint at a time, after the server has confirmed the
        # learner actually watched that far.

    for row in lesson.quiz_questions or []:
        question = frappe.get_doc("Training Question", row.question)
        public["quiz"]["questions"].append(
            {
                "question": question.name,
                "type": question.question_type,
                "text": frappe.utils.sanitize_html(question.question_text or ""),
                "points": cint(row.points) or cint(question.points) or 1,
                # Option key and text only — see the note above on explanations.
                "options": [
                    {"option_key": o.option_key, "text": o.option_text}
                    for o in question.options or []
                ],
            }
        )
        key["quiz"][question.name] = {
            "type": question.question_type,
            "points": cint(row.points) or cint(question.points) or 1,
            "explanation": question.explanation or "",
            "correct": sorted(o.option_key for o in question.options or [] if cint(o.is_correct)),
            "accepted_text": [
                line.strip().lower()
                for line in (question.correct_text_answers or "").splitlines()
                if line.strip()
            ],
        }

    return public, key


def _supersede_previous_completions(version):
    """Mark earlier completions superseded and raise retake assignments.

    Re-dates an existing open assignment rather than inserting a second one — a
    course that recertifies annually and gets a material change in month eleven
    must not leave somebody with two overlapping rows.
    """
    if not frappe.db.exists("DocType", "Training Completion"):
        # Completions arrive in Phase 2. Until then a material change simply
        # republishes; there is nothing yet to supersede.
        return 0

    previous = frappe.get_all(
        "Training Completion",
        filters={
            "course": version.course,
            "docstatus": 1,
            "status": "Valid",
            "course_version": ["!=", version.name],
        },
        fields=["name", "user"],
    )
    due_days = cint(frappe.db.get_value("Training Course", version.course, "due_days")) or cint(
        frappe.db.get_single_value("Training Settings", "default_due_days")
    ) or 14

    for row in previous:
        frappe.db.set_value(
            "Training Completion",
            row.name,
            {"status": "Superseded", "superseded_by_version": version.name},
            update_modified=False,
        )
        _raise_retake(version.course, row.user, due_days)

    return len(previous)


def _raise_retake(course, user, due_days):
    from erpnext_enhancements.training.doctype.training_assignment.training_assignment import (
        OPEN_STATUSES,
    )

    existing = frappe.db.exists(
        "Training Assignment", {"course": course, "user": user, "status": ["in", OPEN_STATUSES]}
    )
    if existing:
        frappe.db.set_value(
            "Training Assignment",
            existing,
            {"due_date": add_days(today(), due_days), "assignment_source": "Version Retake"},
            update_modified=False,
        )
        return

    frappe.get_doc(
        {
            "doctype": "Training Assignment",
            "course": course,
            "user": user,
            "status": "Not Started",
            "assigned_on": today(),
            "due_date": add_days(today(), due_days),
            "assignment_source": "Version Retake",
        }
    ).insert(ignore_permissions=True)


@frappe.whitelist()
def retire_course(course, reason=None):
    """Withdraw a course and cancel what is still outstanding on it."""
    _require_manager()
    if not (reason or "").strip():
        frappe.throw(_("Say why the course is being retired."))

    from erpnext_enhancements.training.doctype.training_assignment.training_assignment import (
        OPEN_STATUSES,
    )

    frappe.db.set_value("Training Course", course, "status", "Retired", update_modified=False)
    open_rows = frappe.get_all(
        "Training Assignment", filters={"course": course, "status": ["in", OPEN_STATUSES]}, pluck="name"
    )
    for name in open_rows:
        frappe.db.set_value(
            "Training Assignment",
            name,
            {"status": "Cancelled", "waiver_reason": _("Course retired: {0}").format(reason)},
            update_modified=False,
        )
    return {"course": course, "cancelled_assignments": len(open_rows)}


# ------------------------------------------------------------------ assignment


@frappe.whitelist()
def assign_course(course, users=None, employees=None, due_date=None):
    """Assign a course to named people. Training Manager only."""
    _require_manager()

    targets = set(frappe.parse_json(users) if isinstance(users, str) else (users or []))
    for employee in frappe.parse_json(employees) if isinstance(employees, str) else (employees or []):
        user_id = frappe.db.get_value("Employee", employee, "user_id")
        if user_id:
            targets.add(user_id)
    targets = sorted(t for t in targets if t)

    if not targets:
        frappe.throw(_("Pick at least one person with a login to assign this to."))

    if len(targets) > INLINE_ASSIGN_LIMIT:
        frappe.enqueue(
            "erpnext_enhancements.api.training_author.run_bulk_assign",
            queue="long",
            enqueue_after_commit=True,
            course=course,
            targets=targets,
            due_date=due_date,
            assigned_by=frappe.session.user,
        )
        return {"queued": len(targets)}

    return {"created": run_bulk_assign(course, targets, due_date, frappe.session.user)}


def run_bulk_assign(course, targets, due_date=None, assigned_by=None):
    """Create one assignment per target, skipping anyone who already has one open.

    Each insert is independent: one bad target must not lose the other twenty-four.
    """
    from erpnext_enhancements.training import notifications
    from erpnext_enhancements.training.doctype.training_assignment.training_assignment import (
        OPEN_STATUSES,
    )

    created = 0
    for user in targets:
        if frappe.db.exists(
            "Training Assignment", {"course": course, "user": user, "status": ["in", OPEN_STATUSES]}
        ):
            continue
        try:
            doc = frappe.get_doc(
                {
                    "doctype": "Training Assignment",
                    "course": course,
                    "user": user,
                    "status": "Not Started",
                    "assigned_on": today(),
                    "due_date": due_date,
                    "assigned_by": assigned_by or frappe.session.user,
                    "assignment_source": "Manual",
                }
            )
            doc.insert(ignore_permissions=True)
            created += 1
            notifications.notify_assigned(doc)
        except Exception:
            frappe.log_error(
                f"Could not assign {course} to {user}\n{frappe.get_traceback()}", "Training assignment"
            )
    frappe.db.commit()
    return created


@frappe.whitelist()
def waive_assignment(assignment, reason):
    """Excuse somebody from a course they were assigned."""
    _require_manager()
    if not (reason or "").strip():
        frappe.throw(_("Say why this is being waived."))

    doc = frappe.get_doc("Training Assignment", assignment)
    doc.status = "Waived"
    doc.waiver_reason = reason
    doc.save(ignore_permissions=True)
    return {"assignment": doc.name, "status": doc.status}


# ---------------------------------------------------------------------- media


@frappe.whitelist()
def register_video_asset(drive_file_id, title=None):
    """Register a Drive video, probing its real duration.

    The probe is the point. Watch coverage is a fraction of ``duration_seconds``,
    so a hand-typed 600 against a real 900-second video lets an 80% gate pass on
    53% of an actual watch. A probe failure is recorded as ``Manual`` rather than
    hidden, so the builder can say so.
    """
    _require_author()
    if not (drive_file_id or "").strip():
        frappe.throw(_("A Drive file id is required."))

    existing = frappe.db.exists("Training Video Asset", {"drive_file_id": drive_file_id})
    if existing:
        return {"video_asset": existing, "created": False}

    probe = _probe_drive_video(drive_file_id)

    doc = frappe.get_doc(
        {
            "doctype": "Training Video Asset",
            "title": title or probe.get("name") or drive_file_id,
            "drive_file_id": drive_file_id,
            "drive_web_view_link": probe.get("web_view_link") or "",
            "mime_type": probe.get("mime_type") or "",
            "size_bytes": cint(probe.get("size_bytes")),
            "duration_seconds": cint(probe.get("duration_seconds")) or 1,
            "duration_source": "Probed" if probe.get("duration_seconds") else "Manual",
        }
    )
    doc.insert(ignore_permissions=True)

    if not probe.get("duration_seconds"):
        frappe.msgprint(
            _("Could not read the length of this video from Drive, so it has been set to 1 second. "
              "Enter the real duration before using it — watch coverage is measured against it."),
            indicator="orange",
            alert=True,
        )
    return {"video_asset": doc.name, "created": True, "duration_probed": bool(probe.get("duration_seconds"))}


def _probe_drive_video(drive_file_id):
    """Read name, size, mime and duration from Drive.

    Returns an empty dict on any failure — Drive being unreachable must not stop
    an author registering a video they can fill in by hand.
    """
    try:
        from erpnext_enhancements.google_drive.drive_utils import get_drive_service

        service, _shared_drive_id = get_drive_service()
        meta = (
            service.files()
            .get(
                fileId=drive_file_id,
                fields="name,mimeType,size,webViewLink,videoMediaMetadata(durationMillis)",
                supportsAllDrives=True,
            )
            .execute()
        )
    except Exception:
        frappe.log_error(
            f"Could not probe Drive video {drive_file_id}\n{frappe.get_traceback()}", "Training video"
        )
        return {}

    duration_ms = cint((meta.get("videoMediaMetadata") or {}).get("durationMillis"))
    return {
        "name": meta.get("name"),
        "mime_type": meta.get("mimeType"),
        "size_bytes": cint(meta.get("size")),
        "web_view_link": meta.get("webViewLink"),
        "duration_seconds": duration_ms // 1000 if duration_ms else 0,
    }
