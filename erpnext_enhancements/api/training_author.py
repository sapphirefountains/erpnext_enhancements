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

The builder surface at the bottom of this file (:func:`get_builder_bootstrap`,
:func:`save_draft_version`, :func:`reorder_lessons`) backs the Training Builder
desk page, and it is autosave — which changes what a mistake costs. Two rules
carry that weight and neither is negotiable:

* **Every writable field is on an explicit allowlist, and everything else is
  reported back.** A field the builder sends that the server does not accept is
  not applied; if it were also not *reported*, autosave would answer "saved" while
  the author's paragraph went nowhere, and they would only find out on reload.
  Silently dropping a write is strictly worse than refusing one.
* **Every write carries the ``modified`` the caller loaded.** Two builder tabs on
  one draft otherwise overwrite each other with no trace. Unlike
  ``api/maintenance_visit.save_visit``, the token here is *mandatory*: a caller
  with no token never loaded the draft, and honouring that write is exactly the
  clobber the lock exists to stop.

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
# NB: `is_enabled` is imported lazily inside get_builder_bootstrap, NOT here.
# Importing a doctype controller at module scope makes this file unloadable to any
# bench-free suite that stubs `frappe` without also stubbing that module — it broke
# tests/test_training_grading.py, which had passed until this import was added.

# Above this many people, fan the assignment out to a background job. Below it,
# the wait is shorter than the round trip of telling the user to come back later.
INLINE_ASSIGN_LIMIT = 25

AUTHOR_ROLES = ("Training Author", "Training Manager", "System Manager")


# --------------------------------------------------------------------- guards


def _ai_enabled():
    """Whether AI drafting is switched on, imported lazily and never fatal.

    Lazy because a module-scope import of a doctype controller makes this whole
    file unloadable to a bench-free suite that stubs ``frappe`` but not that
    module. Swallowing the failure because a settings read is not worth denying an
    author their builder over — the AI panel simply stays hidden.
    """
    try:
        from erpnext_enhancements.training.doctype.training_settings.training_settings import (
            is_enabled,
        )

        return is_enabled("ai_assist_enabled")
    except Exception:
        return False


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

    _queue_video_copies(doc.name)

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


def _queue_video_copies(course_version):
    """Copy every video this version uses into the bucket, in the background.

    `gcs_media.copy_from_drive` was written in Phase 2b, does exactly this, and
    was **called from nowhere**. So `gcs_object` stayed empty on every asset ever
    registered, and `get_media_url` answered `not_available` for every video
    block — the player rendered an empty frame and the coverage gate had nothing
    to measure. The whole video pipeline was one call short of working.

    Enqueued rather than run inline: a 300 MB copy inside the publish request
    would time out the author's browser, and the copy is idempotent, so an asset
    that already has an object is skipped and a republish does not re-upload
    gigabytes.

    Deliberately not fatal. A publish that succeeded except for the video must
    not roll back — the text lessons are live and correct, and the asset carries
    its own `last_error` for anyone looking.
    """
    assets = {
        row.video_asset
        for row in frappe.get_all(
            "Training Content Block",
            filters={"parenttype": "Training Lesson", "video_asset": ["is", "set"]},
            fields=["video_asset", "parent"],
        )
        if frappe.db.get_value("Training Lesson", row.parent, "course_version") == course_version
    }
    if not assets:
        return
    for asset in sorted(assets):
        frappe.enqueue(
            "erpnext_enhancements.training.gcs_media.copy_from_drive",
            queue="long",
            timeout=1800,
            enqueue_after_commit=True,
            video_asset=asset,
        )


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
        # Guarded rather than assumed: a site where the completion doctype has not
        # migrated yet must still be able to publish. (This comment used to say
        # completions "arrive in Phase 2" — they arrived three releases ago.)
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


# --------------------------------------------------------------------- builder
#
# Everything below backs the Training Builder desk page
# (`training/page/training_builder`). See the note in the module docstring on why
# the allowlist and the `modified` lock are the two load-bearing pieces.


# Fields the builder may write, by scope. Anything sent that is not named here —
# and not in the matching *_ECHOED set below — is refused and reported in the
# response's `rejected` list. Adding a field to the builder UI therefore means
# adding it here too, and forgetting shows up as a visible warning on the first
# autosave rather than as work missing after a reload.
LESSON_ALLOWED_FIELDS = frozenset(
    {
        "lesson_title",
        "chapter_key",
        "idx_in_chapter",
        "estimated_minutes",
        "summary",
        "transcript",
        "allow_questions",
        "has_quiz",
        "quiz_questions_to_ask",
        "quiz_pass_score",
        "quiz_shuffle_questions",
        "quiz_shuffle_options",
    }
)
BLOCK_ALLOWED_FIELDS = frozenset(
    {
        "block_type",
        "heading",
        "content",
        "image",
        "file",
        "video_asset",
        "embed_url",
        "poster_image",
        "caption",
        "required_for_completion",
        "min_coverage_percent",
        "checkpoints_enabled",
    }
)
QUIZ_ROW_ALLOWED_FIELDS = frozenset({"question", "points", "is_required"})
CHAPTER_ALLOWED_FIELDS = frozenset({"chapter_title", "description"})
VERSION_ALLOWED_FIELDS = frozenset({"change_type", "release_notes"})

# Keys the bootstrap emits that are structure or server-owned mirrors. The builder
# is expected to echo them back untouched, so they are skipped in silence — they
# are not author work being dropped. Note what is deliberately NOT here:
# `checkpoints`, and the Training Question body fields. Those look writable in the
# payload and are not, so they are reported.
LESSON_ECHOED_KEYS = frozenset(
    {"name", "temp_id", "doctype", "modified", "blocks", "quiz", "lesson_key", "course", "course_version"}
)
BLOCK_ECHOED_KEYS = frozenset({"name", "idx", "doctype", "block_key", "video_duration_seconds"})
CHAPTER_ECHOED_KEYS = frozenset({"name", "idx", "doctype", "chapter_key"})
QUIZ_ECHOED_KEYS = frozenset({"name", "idx", "doctype", "question_preview"})
VERSION_ECHOED_KEYS = frozenset(
    {"name", "doctype", "modified", "docstatus", "version_number", "submitted_for_review"}
)

# Why a refused field was refused. Generic is fine for a typo; these four are the
# ones an author could reasonably believe the builder owns, and a bare "not
# allowed" would send them looking for a bug that is not there.
REFUSAL_REASON = {
    "checkpoints": "Checkpoints are not saved by autosave — accept an AI suggestion, or use the "
    "Training Checkpoint form.",
    "lesson_key": "Stable key. Regenerating it would strand every learner currently mid-course.",
    "block_key": "Stable key. Regenerating it would reset watch progress on this block.",
    "question_text": "Question wording is edited on the Training Question form, not here — the "
    "builder's autosave only manages which questions are in the pool.",
    "options": "Answer options are edited on the Training Question form, not here.",
    "published_content_json": "Machine-written at publish.",
    "answer_key_json": "Machine-written at publish.",
}
_DEFAULT_REFUSAL = "Not writable through the builder."

# Cap on the video picker. A site with thousands of assets should search rather
# than ship the lot into every bootstrap.
VIDEO_ASSET_LIMIT = 500


def _refusal(scope, target, field):
    return {
        "scope": scope,
        "target": target or "",
        "field": field,
        "reason": _(REFUSAL_REASON.get(field, _DEFAULT_REFUSAL)),
    }


def _check_version_not_stale(version, modified):
    """Optimistic lock on the draft version.

    The version is the token for the *whole* builder document, not just its own
    row: :func:`save_draft_version` always saves it, so its ``modified`` advances
    even on a save that only touched lessons (which are separate documents and
    would otherwise leave the token unchanged and the lock useless).
    """
    if not modified:
        frappe.throw(
            _("This save arrived without the version stamp it was loaded with, so it cannot be checked "
              "against what is stored. Reload the builder.")
        )
    if str(version.modified) != str(modified):
        frappe.throw(
            _("{0} was changed elsewhere — another tab, or the desk form — since you loaded it. Reload "
              "the builder to continue; saving now would overwrite that change.").format(version.name),
            title=_("Draft Out of Date"),
        )


# ---------------------------------------------------------------- bootstrap


@frappe.whitelist()
def get_builder_bootstrap(course):
    """Everything the builder needs to render a course in one round trip.

    Loads the **open draft** (``docstatus 0``) only. When a course has none this
    returns ``version: null`` rather than creating one, because a draft is a real
    record with a version number attached to it: opening the builder to look at a
    published course must not silently start a new version in the author's name.
    The client offers "New Draft Version" instead.

    This is the one place in the module that hands ``is_correct`` to a browser. It
    is gated on **write** permission on the course, which is the difference
    between an author editing an answer key and a learner reading one.
    """
    _require_author()
    if not frappe.has_permission("Training Course", "write", doc=course):
        frappe.throw(_("You are not allowed to edit {0}.").format(course), frappe.PermissionError)

    course_doc = frappe.get_doc("Training Course", course)

    version = None
    chapters = []
    lessons = []
    draft_name = frappe.db.exists("Training Course Version", {"course": course, "docstatus": 0})
    if draft_name:
        draft = frappe.get_doc("Training Course Version", draft_name)
        version = {
            "name": draft.name,
            "version_number": cint(draft.version_number),
            "docstatus": cint(draft.docstatus),
            "modified": str(draft.modified),
            "change_type": draft.change_type or "",
            "release_notes": draft.release_notes or "",
            "submitted_for_review": cint(draft.submitted_for_review),
        }
        chapters = [
            {
                "chapter_key": row.chapter_key,
                "chapter_title": row.chapter_title,
                "description": row.description or "",
                "idx": cint(row.idx),
            }
            for row in draft.chapters or []
        ]
        lessons = _builder_lessons(draft.name)

    return {
        "course": {
            "name": course_doc.name,
            "course_title": course_doc.course_title,
            "slug": course_doc.slug or "",
            "status": course_doc.status,
            "weight": course_doc.weight,
            "category": course_doc.category or "",
            "current_version": course_doc.current_version or "",
            "passing_score": cint(course_doc.passing_score),
            "max_attempts": cint(course_doc.max_attempts),
            "min_video_coverage": cint(course_doc.min_video_coverage),
            "require_checkpoints_answered": cint(course_doc.require_checkpoints_answered),
        },
        "version": version,
        "chapters": chapters,
        "lessons": lessons,
        "categories": frappe.get_all(
            "Training Category", fields=["name", "category_name"], order_by="category_name asc"
        ),
        "video_assets": _builder_video_assets(),
        # Whether this user may publish *at all*, not whether this draft is ready.
        # Readiness (a draft exists, it has lessons, its AI questions are reviewed)
        # is decided by publish_version, which is the only thing that can decide it
        # correctly at the moment the button is pressed.
        "can_publish": bool({"Training Manager", "System Manager"} & set(frappe.get_roles())),
        "ai_enabled": _ai_enabled(),
    }


def _builder_video_assets():
    """The video picker. ``has_transcript`` rides along because it is what decides
    whether ``training_ai.suggest_checkpoints`` will refuse — the builder can grey
    the action out instead of letting the author discover the refusal."""
    rows = frappe.get_all(
        "Training Video Asset",
        fields=["name", "title", "duration_seconds", "status", "transcript_source"],
        order_by="title asc",
        limit=VIDEO_ASSET_LIMIT,
    )
    return [
        {
            "name": row.name,
            "title": row.title,
            "duration_seconds": cint(row.duration_seconds),
            "status": row.status,
            "has_transcript": row.transcript_source in ("Author Pasted", "Uploaded VTT"),
        }
        for row in rows
    ]


def _builder_lessons(course_version):
    """Every lesson of a draft, with its blocks, quiz pool and checkpoints.

    Ordered the same way ``_materialize_lessons`` orders them at publish, so the
    outline an author is looking at is the outline that will ship.
    """
    names = frappe.get_all(
        "Training Lesson",
        filters={"course_version": course_version},
        pluck="name",
        order_by="chapter_key asc, idx_in_chapter asc, creation asc",
    )
    if not names:
        return []

    docs = [frappe.get_doc("Training Lesson", name) for name in names]
    question_names = sorted(
        {row.question for doc in docs for row in doc.quiz_questions or [] if row.question}
    )
    questions = _builder_questions(question_names)
    checkpoints = _builder_checkpoints(names)

    return [_builder_lesson(doc, questions, checkpoints.get(doc.name, [])) for doc in docs]


def _builder_lesson(lesson, questions, checkpoints):
    return {
        "name": lesson.name,
        "lesson_key": lesson.lesson_key,
        "chapter_key": lesson.chapter_key or "",
        "lesson_title": lesson.lesson_title,
        "summary": lesson.summary or "",
        "idx_in_chapter": cint(lesson.idx_in_chapter),
        "estimated_minutes": cint(lesson.estimated_minutes),
        "allow_questions": cint(lesson.allow_questions),
        "has_quiz": cint(lesson.has_quiz),
        "quiz_questions_to_ask": cint(lesson.quiz_questions_to_ask),
        "quiz_pass_score": cint(lesson.quiz_pass_score),
        "quiz_shuffle_questions": cint(lesson.quiz_shuffle_questions),
        "quiz_shuffle_options": cint(lesson.quiz_shuffle_options),
        # Raw, not sanitized. The author is editing the source; sanitization
        # happens on the way out, in _split_lesson at publish.
        "transcript": lesson.transcript or "",
        "blocks": [
            {
                "block_key": block.block_key,
                "block_type": block.block_type,
                "heading": block.heading or "",
                "content": block.content or "",
                "image": block.image or "",
                "file": block.file or "",
                "video_asset": block.video_asset or "",
                "video_duration_seconds": cint(block.video_duration_seconds),
                "embed_url": block.embed_url or "",
                "poster_image": block.poster_image or "",
                "caption": block.caption or "",
                "required_for_completion": cint(block.required_for_completion),
                "min_coverage_percent": cint(block.min_coverage_percent),
                "checkpoints_enabled": cint(block.checkpoints_enabled),
            }
            for block in lesson.blocks or []
        ],
        "quiz": [
            dict(
                questions.get(row.question) or {"name": row.question},
                points=cint(row.points),
                is_required=cint(row.is_required),
            )
            for row in lesson.quiz_questions or []
        ],
        "checkpoints": checkpoints,
    }


def _builder_questions(names):
    """Question bodies, keyed by name, answers included — see the note on
    :func:`get_builder_bootstrap` about why that is safe here and nowhere else."""
    if not names:
        return {}
    options = _options_by_parent("Training Question", names)
    return {
        row.name: {
            "name": row.name,
            "question": row.name,
            "question_text": row.question_text or "",
            "question_type": row.question_type,
            "explanation": row.explanation or "",
            "correct_text_answers": row.correct_text_answers or "",
            "difficulty": row.difficulty or "",
            "ai_generated": cint(row.ai_generated),
            "ai_reviewed_by": row.ai_reviewed_by or "",
            "ai_model": row.ai_model or "",
            "ai_source": row.ai_source or "",
            "options": options.get(row.name, []),
        }
        for row in frappe.get_all(
            "Training Question",
            filters={"name": ["in", names]},
            fields=["name", "question_text", "question_type", "explanation", "correct_text_answers",
                    "difficulty", "ai_generated", "ai_reviewed_by", "ai_model", "ai_source"],
        )
    }


def _builder_checkpoints(lesson_names):
    rows = frappe.get_all(
        "Training Checkpoint",
        filters={"lesson": ["in", lesson_names]},
        fields=["name", "lesson", "checkpoint_key", "block_key", "at_seconds", "question_type",
                "question_text", "explanation", "pause_video", "allow_skip", "max_attempts",
                "rewind_seconds_on_wrong", "counts_toward_score", "ai_generated", "ai_reviewed_by"],
        order_by="at_seconds asc",
    )
    if not rows:
        return {}

    options = _options_by_parent("Training Checkpoint", [row.name for row in rows])
    by_lesson = {}
    for row in rows:
        by_lesson.setdefault(row.lesson, []).append(
            {
                "name": row.name,
                "checkpoint_key": row.checkpoint_key,
                "block_key": row.block_key,
                "at_seconds": cint(row.at_seconds),
                "question_type": row.question_type,
                "question_text": row.question_text or "",
                "explanation": row.explanation or "",
                "pause_video": cint(row.pause_video),
                "allow_skip": cint(row.allow_skip),
                "max_attempts": cint(row.max_attempts),
                "rewind_seconds_on_wrong": cint(row.rewind_seconds_on_wrong),
                "counts_toward_score": cint(row.counts_toward_score),
                "ai_generated": cint(row.ai_generated),
                "ai_reviewed_by": row.ai_reviewed_by or "",
                "options": options.get(row.name, []),
            }
        )
    return by_lesson


def _options_by_parent(parenttype, parents):
    if not parents:
        return {}
    out = {}
    for row in frappe.get_all(
        "Training Answer Option",
        filters={"parent": ["in", parents], "parenttype": parenttype},
        fields=["parent", "option_key", "option_text", "is_correct", "explanation"],
        order_by="parent asc, idx asc",
    ):
        out.setdefault(row.parent, []).append(
            {
                "option_key": row.option_key or "",
                "option_text": row.option_text or "",
                "is_correct": cint(row.is_correct),
                "explanation": row.explanation or "",
            }
        )
    return out


# ------------------------------------------------------------------- autosave


@frappe.whitelist()
def save_draft_version(course_version, payload, modified=None):
    """Apply a builder patch to an open draft and return a fresh lock token.

    Args:
        course_version: the draft. Refused unless ``docstatus == 0``.
        payload: ``{"version": {...}, "chapters": [...], "deleted_lessons": [...],
            "lessons": [{"name": ..., <allowlisted fields>, "blocks": [...],
            "quiz": [...]}]}``. A lesson with no ``name`` is **created**; pass a
            ``temp_id`` and read the real name back out of ``created_lessons``.
            ``blocks`` and ``quiz`` replace their tables wholesale in the given
            order — which is how reordering is expressed — so a block's
            ``block_key`` must be sent back with it. Never regenerate one: it is
            what watch progress is stored against.
        modified: the version ``modified`` the client loaded. Mandatory.

    Returns:
        dict: ``modified`` (the new token — the client must adopt it or its next
        save is rejected), ``saved`` lesson names, ``created_lessons``,
        ``deleted``, the post-save ``chapters`` (server-generated keys land here),
        and ``rejected`` — every field that was **not** applied, with a reason.
        ``rejected`` is not an error; it is the difference between autosave losing
        work quietly and the builder being able to say so.

    Lesson validation runs in full (``lesson.save()``), so a half-built block —
    an Image block with nothing attached — fails the save loudly. That is on
    purpose: the alternative is letting an unrenderable lesson reach publish. The
    builder's accumulator re-merges and retries on failure, same as the visit
    wizard's.
    """
    _require_author()
    doc = _draft(course_version)
    _check_version_not_stale(doc, modified)

    payload = frappe.parse_json(payload) or {}
    rejected = []

    for field, value in (payload.get("version") or {}).items():
        if field in VERSION_ALLOWED_FIELDS:
            doc.set(field, value)
        elif field not in VERSION_ECHOED_KEYS:
            rejected.append(_refusal("version", doc.name, field))

    # Deletions first: a chapter can only be checked for orphans once the lessons
    # that were going to be removed are actually gone.
    deleted = _delete_lessons(doc, payload.get("deleted_lessons") or [])

    if "chapters" in payload:
        _apply_chapters(doc, payload.get("chapters") or [], rejected)

    # Saved before the lessons, not after. Training Lesson._validate_chapter reads
    # the version's chapter keys *from the database*, so a lesson moved into a
    # chapter created in this same payload would be rejected if the version were
    # still unsaved. This save is also unconditional — it is what advances the
    # lock token on a payload that only touched lessons.
    doc.save(ignore_permissions=True)

    saved = []
    created = []
    for patch in payload.get("lessons") or []:
        lesson, is_new = _load_or_new_lesson(doc, patch)
        _apply_lesson(lesson, patch, rejected)
        lesson.save(ignore_permissions=True)
        saved.append(lesson.name)
        if is_new:
            created.append({"temp_id": patch.get("temp_id") or "", "name": lesson.name})

    return {
        "modified": str(doc.modified),
        "saved": saved,
        "created_lessons": created,
        "deleted": deleted,
        "chapters": [
            {"chapter_key": row.chapter_key, "chapter_title": row.chapter_title, "idx": cint(row.idx)}
            for row in doc.chapters or []
        ],
        "rejected": rejected,
    }


def _delete_lessons(version, names):
    deleted = []
    for name in names:
        if not name:
            continue
        owner_version = frappe.db.get_value("Training Lesson", name, "course_version")
        if owner_version is None:
            # Already gone. A retried autosave must not fail on its own success.
            continue
        if owner_version != version.name:
            frappe.throw(_("{0} does not belong to {1}.").format(name, version.name))
        # on_trash cascades the lesson's checkpoints — they are top-level docs and
        # nothing else would.
        frappe.delete_doc("Training Lesson", name, ignore_permissions=True)
        deleted.append(name)
    return deleted


def _apply_chapters(version, rows, rejected):
    """Replace the chapter table in the given order, keeping every key.

    Refuses to drop a chapter that lessons still point at. Frappe would let it
    through and the lessons would simply stop appearing in the outline, with the
    author given no clue why.
    """
    incoming = {(row.get("chapter_key") or "").strip() for row in rows}
    in_use = {
        key
        for key in frappe.get_all(
            "Training Lesson", filters={"course_version": version.name}, pluck="chapter_key"
        )
        if key
    }
    orphaned = sorted(in_use - incoming)
    if orphaned:
        titles = {row.chapter_key: row.chapter_title for row in version.chapters or []}
        frappe.throw(
            _("Move or delete the lessons in {0} before removing it — a lesson whose chapter is gone "
              "disappears from the outline.").format(
                ", ".join(titles.get(key) or key for key in orphaned)
            )
        )

    version.set("chapters", [])
    for row in rows:
        values = {}
        for field, value in row.items():
            if field in CHAPTER_ALLOWED_FIELDS:
                values[field] = value
            elif field not in CHAPTER_ECHOED_KEYS:
                rejected.append(_refusal("chapter", row.get("chapter_key"), field))
        # Blank on a new chapter; TrainingCourseVersion._assign_chapter_keys fills
        # it in on save and the response hands the generated key back.
        values["chapter_key"] = (row.get("chapter_key") or "").strip()
        version.append("chapters", values)


def _load_or_new_lesson(version, patch):
    name = (patch.get("name") or "").strip()
    if not name:
        lesson = frappe.new_doc("Training Lesson")
        lesson.course_version = version.name
        return lesson, True

    lesson = frappe.get_doc("Training Lesson", name)
    if lesson.course_version != version.name:
        frappe.throw(_("{0} does not belong to {1}.").format(name, version.name))
    return lesson, False


def _apply_lesson(lesson, patch, rejected):
    # The patch's own name, not lesson.name — a new_doc already carries a throwaway
    # "new-training-lesson-…" the client has never seen and could not match up.
    target = (patch.get("name") or "").strip() or patch.get("temp_id") or ""
    for field, value in patch.items():
        if field in LESSON_ALLOWED_FIELDS:
            lesson.set(field, value)
        elif field not in LESSON_ECHOED_KEYS:
            rejected.append(_refusal("lesson", target, field))

    if "blocks" in patch:
        _apply_blocks(lesson, patch.get("blocks") or [], rejected)
    if "quiz" in patch:
        _apply_quiz(lesson, patch.get("quiz") or [], rejected)


def _apply_blocks(lesson, rows, rejected):
    """Replace the block table in the given order.

    ``block_key`` is carried across from the payload, never regenerated. The
    lesson controller only mints one when it is blank or duplicated, so a new
    block gets a key and an existing one keeps the key its watch intervals and
    checkpoints are filed under.
    """
    lesson.set("blocks", [])
    for row in rows:
        values = {}
        for field, value in row.items():
            if field in BLOCK_ALLOWED_FIELDS:
                values[field] = value
            elif field not in BLOCK_ECHOED_KEYS:
                rejected.append(_refusal("block", row.get("block_key"), field))
        values["block_key"] = (row.get("block_key") or "").strip()
        lesson.append("blocks", values)


def _apply_quiz(lesson, rows, rejected):
    """Replace the quiz pool in the given order.

    Pool membership only. A Training Question is a shared, reusable document —
    it can sit in another course's pool — so editing its wording from here would
    silently rewrite a question somewhere else. Body fields arriving in a row are
    therefore refused and reported rather than dropped.
    """
    lesson.set("quiz_questions", [])
    for row in rows:
        values = {}
        for field, value in row.items():
            if field in QUIZ_ROW_ALLOWED_FIELDS:
                values[field] = value
            elif field not in QUIZ_ECHOED_KEYS:
                rejected.append(_refusal("quiz", row.get("question") or row.get("name"), field))
        if not values.get("question"):
            frappe.throw(
                _("A quiz row on {0} names no question.").format(lesson.lesson_title or lesson.name)
            )
        lesson.append("quiz_questions", values)


@frappe.whitelist()
def reorder_lessons(course_version, order):
    """Re-number lessons from a drag-and-drop, in the order given.

    ``idx_in_chapter`` is renumbered per chapter, reading each lesson's *current*
    ``chapter_key`` — so a lesson dragged into another chapter by a save that ran
    first lands in the right sequence there.

    Written with ``db.set_value`` and ``update_modified=False`` on purpose. This is
    position, not content: it must not churn the lessons' timestamps, and it must
    not be able to fail on an unrelated half-finished block elsewhere in the
    course. ``_draft`` has already refused a published version, which is the guard
    ``db.set_value`` would otherwise bypass.

    Lessons missing from ``order`` keep the position they had; send the whole list.
    """
    _require_author()
    doc = _draft(course_version)

    order = frappe.parse_json(order) if isinstance(order, str) else (order or [])
    owned = {
        row.name: row.chapter_key or ""
        for row in frappe.get_all(
            "Training Lesson", filters={"course_version": doc.name}, fields=["name", "chapter_key"]
        )
    }
    unknown = [name for name in order if name not in owned]
    if unknown:
        frappe.throw(
            _("These are not lessons of {0}: {1}.").format(doc.name, ", ".join(unknown[:5]))
        )

    counters = {}
    for name in order:
        chapter_key = owned[name]
        counters[chapter_key] = counters.get(chapter_key, 0) + 1
        frappe.db.set_value(
            "Training Lesson", name, "idx_in_chapter", counters[chapter_key], update_modified=False
        )
    return {"ok": True}
