# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Build a whole training course from a Course Spec — deterministically.

This is the *materializer* half of AI-authored trainings. A model (via
``assistant_tools.draft_course_spec``) or a human proposes a
:mod:`erpnext_enhancements.training.course_spec` — a plain dict describing a course,
its lessons, blocks and quizzes — and :func:`author_course_from_spec` turns it into
real records. The model never writes; this does, and it does it the *same* way the
manual Training Builder does, so every AI-seeded course comes out in the identical
shape and flow.

The one rule that makes that true: **this module maps a spec onto the existing
authoring engine and nothing else.** It calls
``training_author.create_draft_version`` to mint the draft and
``training_author.save_draft_version`` to write the lessons, blocks and quiz pools —
the same allowlisted, key-minting, optimistic-locked path the builder autosaves
through. It never assembles a lesson document by hand and it never touches
``published_content_json`` / ``answer_key_json`` (those are built only inside
``publish_version`` → ``_split_lesson``, so the answer-key-stays-server-side
guarantee is inherited for free).

Two things it deliberately does and does not do:

* **Every quiz question it creates is stamped ``ai_generated`` with no reviewer.**
  That is the pair ``training_author._unreviewed_ai_questions`` reads, so an AI-seeded
  course *cannot publish* until a human opens the builder and accepts each question.
  A model's guessed answer key never grades anyone unreviewed. This mirrors
  ``training_ai._accept_quiz`` exactly, minus the reviewer stamp.
* **It stops at an unpublished draft.** It never calls ``publish_version`` — the
  course lands as a ``Draft`` for a person to review and publish. That is the whole
  point: seeding is not shipping.

The write is atomic. Every step runs inside the one request transaction and commits
nothing itself, so a spec that fails halfway (a bad category, a stale lock, a
question the controller rejects) rolls back to no course at all rather than a
half-built one.

Indentation is 4 spaces, matching the majority of ``api/``.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint

from erpnext_enhancements.api import training_author
from erpnext_enhancements.training.course_spec import spec_summary, validate_course_spec

AUTHOR_ROLES = ("Training Author", "Training Manager", "System Manager")

# A drafted spec is stashed here between ``draft_course_spec`` (which generates it)
# and ``author_training_course`` (which builds it), so the model does not have to
# re-emit a whole course as tool arguments — it passes back a token instead. Scoped
# to the user and short-lived; a miss just means "generate again".
_SPEC_CACHE_PREFIX = "ee_course_spec"
_SPEC_TTL_SECONDS = 3600


def _require_author():
    if not set(AUTHOR_ROLES) & set(frappe.get_roles()):
        frappe.throw(_("Not permitted."), frappe.PermissionError)


def _ai_model_id():
    """The Vertex model name to stamp on generated questions, or ``""``.

    Copied (not imported) from ``training_ai._model_id`` so this module does not
    depend on that one's import surface; the value is provenance, never gating."""
    try:
        from erpnext_enhancements.api.gemini import MODEL_ID

        return MODEL_ID
    except Exception:
        return ""


# ------------------------------------------------------------- spec stash


def stash_spec(spec):
    """Store a validated spec for this user and return a short token."""
    token = frappe.generate_hash(length=16)
    frappe.cache().set_value(
        f"{_SPEC_CACHE_PREFIX}|{frappe.session.user}|{token}",
        json.dumps(spec),
        expires_in_sec=_SPEC_TTL_SECONDS,
    )
    return token


def pop_spec(token):
    """Return (and forget) a spec stashed for this user, or ``None``."""
    token = (token or "").strip()
    if not token:
        return None
    key = f"{_SPEC_CACHE_PREFIX}|{frappe.session.user}|{token}"
    raw = frappe.cache().get_value(key)
    if not raw:
        return None
    frappe.cache().delete_value(key)
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


# ------------------------------------------------------------- materializer


@frappe.whitelist()
def list_starters():
    """The starter gallery. Read-only; an author sees what shapes exist."""
    from erpnext_enhancements.training import templates_gallery

    _require_author()
    return {"starters": templates_gallery.list_templates()}


@frappe.whitelist()
def create_from_starter(starter, course_title=None):
    """Build a draft course from a starter shape.

    **The whole point is that this is not a second scaffolder.** A starter is a
    Course Spec, so it goes through ``validate_course_spec`` and
    ``author_course_from_spec`` exactly as an AI-drafted course does — same
    validator, same builder, same block vocabulary, same review gate. If a starter
    could express something the AI path cannot, one of the two would be wrong, and
    the drift would only surface when a template produced a course the builder
    could not render.

    Answers the half of "anyone can build a training" that is not about the
    editor: most of what stops people is opening a blank course and having to
    invent the content and the shape at once. The starter gives away the shape and
    leaves prose that says *replace this with…* — never filler, because filler is
    worse than an empty page. Filler gets published.
    """
    from erpnext_enhancements.training import templates_gallery

    _require_author()
    return author_course_from_spec(templates_gallery.spec_for(starter, course_title))


@frappe.whitelist()
def author_course_from_spec(spec):
    """Create a Training Course and an unpublished draft from a Course Spec.

    ``spec`` is a :mod:`~erpnext_enhancements.training.course_spec` dict (or its JSON
    string). Returns ``{course, course_title, draft_version, url, ...counts}``. The
    course is left as a ``Draft`` with every AI question awaiting human review.
    """
    _require_author()
    if not frappe.has_permission("Training Course", "create"):
        frappe.throw(_("You are not allowed to create Training Courses."), frappe.PermissionError)

    spec = validate_course_spec(spec)
    course_name = _create_course(spec["course"])

    # An empty draft: a brand-new course has no live version to clone, so
    # create_draft_version mints a bare docstatus-0 version to fill.
    draft_version = training_author.create_draft_version(course_name)

    modified = str(frappe.db.get_value("Training Course Version", draft_version, "modified"))
    chapter_keys = _apply_chapters(draft_version, spec["chapters"], modified)
    if chapter_keys is not None:
        modified = chapter_keys.pop("_modified")

    modified, lesson_names = _apply_lessons(draft_version, spec["lessons"], chapter_keys, modified)
    _apply_quizzes(draft_version, spec["lessons"], lesson_names, modified)

    summary = spec_summary(spec)
    return {
        "course": course_name,
        "course_title": summary["course_title"],
        "draft_version": draft_version,
        "status": "Draft",
        "chapters": summary["chapters"],
        "lessons": summary["lessons"],
        "blocks": summary["blocks"],
        "questions": summary["questions"],
        "url": f"/app/training-course/{course_name}",
        "note": (
            "Created as an unpublished Draft. Every quiz question is flagged as "
            "AI-generated and must be reviewed by a person in the Training Builder "
            "before the course can be published."
        ),
    }


def _create_course(course):
    data = {
        "doctype": "Training Course",
        "course_title": course["course_title"],
        "weight": course["weight"],
        "audience": course["audience"],
        "author": frappe.session.user,
    }
    if course.get("summary"):
        data["summary"] = course["summary"]
    # A category the model named that does not exist is dropped rather than fatal —
    # the author can set it on the draft. Naming a real one is the common case.
    category = course.get("category")
    if category and frappe.db.exists("Training Category", category):
        data["category"] = category

    doc = frappe.get_doc(data)
    doc.insert()  # as the user: create permission was checked above
    return doc.name


def _apply_chapters(draft_version, chapters, modified):
    """Create the chapters, returning ``{index: chapter_key, "_modified": token}``.

    ``None`` when the spec has no chapters. The keys are minted by
    ``TrainingCourseVersion`` on save and read back out of the response, so a
    lesson can reference the chapter it belongs to by the real key.
    """
    if not chapters:
        return None
    payload = {"chapters": [{"chapter_title": ch["title"], "description": ch["description"]} for ch in chapters]}
    result = training_author.save_draft_version(draft_version, payload, modified)
    # save_draft_version returns chapters ordered by idx — the same order we sent.
    keys = {index: row["chapter_key"] for index, row in enumerate(result.get("chapters") or [])}
    keys["_modified"] = result["modified"]
    return keys


def _block_to_payload(block):
    """One spec block as a builder block-payload dict (allowlisted fields only)."""
    payload = {"block_type": block["block_type"], "heading": block.get("heading", "")}
    if block["block_type"] in ("Rich Text", "Callout"):
        payload["content"] = block.get("content", "")
    if block.get("callout_tone"):
        payload["callout_tone"] = block["callout_tone"]

    data = None
    if block["block_type"] == "Checklist":
        data = {"items": block.get("items") or []}
    elif block["block_type"] == "Flashcards":
        data = {"cards": block.get("cards") or []}
    elif block["block_type"] == "Accordion":
        data = {"panels": block.get("panels") or []}
    if data is not None:
        payload["data"] = json.dumps(data)
    return payload


def _apply_lessons(draft_version, lessons, chapter_keys, modified):
    """Create every lesson with its blocks (no quiz yet). Returns the new lock
    token and a list of the created lesson names, in spec order."""
    patches = []
    for index, lesson in enumerate(lessons):
        patch = {
            "temp_id": f"L{index}",
            "lesson_title": lesson["lesson_title"],
            "summary": lesson.get("summary", ""),
            "estimated_minutes": cint(lesson.get("estimated_minutes")),
            "blocks": [_block_to_payload(b) for b in lesson["blocks"]],
        }
        if chapter_keys and "chapter" in lesson:
            patch["chapter_key"] = chapter_keys.get(lesson["chapter"], "")
        patches.append(patch)

    result = training_author.save_draft_version(draft_version, {"lessons": patches}, modified)
    by_temp = {row["temp_id"]: row["name"] for row in result.get("created_lessons") or []}
    # Ordered back into spec order so the caller can line questions up by index.
    lesson_names = [by_temp.get(f"L{index}") for index in range(len(lessons))]
    return result["modified"], lesson_names


def _apply_quizzes(draft_version, lessons, lesson_names, modified):
    """Create the AI questions (flagged, unreviewed) and attach each lesson's pool.

    A second pass over the lessons: questions are created first so the quiz rows can
    reference them by name, then one ``save_draft_version`` marks the pools. Lessons
    without a quiz are left untouched (no ``blocks`` key, so their content stands).
    """
    patches = []
    for index, lesson in enumerate(lessons):
        quiz = lesson.get("quiz")
        if not quiz or not quiz.get("questions"):
            continue
        lesson_name = lesson_names[index]
        if not lesson_name:
            # A lesson we failed to place cannot own a quiz; skip rather than orphan
            # the questions. In practice every lesson is created, so this is defensive.
            continue

        question_names = []
        for question in quiz["questions"]:
            doc = frappe.get_doc(
                {
                    "doctype": "Training Question",
                    "question_text": question["question"],
                    "question_type": question["type"],
                    "explanation": question.get("explanation", ""),
                    "source_lesson": lesson_name,
                    "is_bank_question": 0,
                    "options": [
                        {"option_text": option["text"], "is_correct": option["is_correct"]}
                        for option in question["options"]
                    ],
                    "ai_model": _ai_model_id(),
                    # ai_generated WITHOUT ai_reviewed_by: the pair the publish gate
                    # reads. This is what forces a human to review before it goes live.
                    "ai_generated": 1,
                }
            )
            doc.insert(ignore_permissions=True)
            question_names.append(doc.name)

        patches.append(
            {
                "name": lesson_name,
                "has_quiz": 1,
                "quiz_pass_score": cint(quiz.get("pass_score")),
                "quiz_questions_to_ask": cint(quiz.get("questions_to_ask")),
                "quiz": [{"question": name} for name in question_names],
            }
        )

    if patches:
        training_author.save_draft_version(draft_version, {"lessons": patches}, modified)


def rebuild_draft_from_spec(course, spec):
    """Replace an **untouched** draft's content with the spec's, in place.

    ``author_course_from_spec`` can only ever create: it mints a course and then a draft, and
    ``create_draft_version`` refuses outright when an open draft already exists. So a course whose
    spec has been rewritten in the repo cannot be brought up to date by re-running the seeder --
    the seeder is insert-only and keyed on title, and it skips. Without this, editing a spec file
    changes what a *fresh install* gets and nothing at all on a site that already has the course.

    This is the missing half, and it is deliberately **not** whitelisted: it deletes every lesson
    on the draft and builds new ones, which is a migration's job and nobody's to invoke from a
    browser. The caller is responsible for deciding the draft is safe to rebuild --
    ``patches/rebuild_technician_course_drafts.py`` holds those guards and states them.

    **Keys are re-minted, and that is why the caller must check.** A rebuilt lesson is a new
    ``Training Lesson`` with a new ``lesson_key`` and new ``block_key`` values. Everything in this
    module joins on those keys, so a learner part-way through a course would be stranded -- which
    is exactly why the patch refuses to touch a course anybody has started. On an unpublished draft
    that nobody can have taken, there is nothing to strand.

    Returns ``{course, draft_version, lessons, questions, removed_questions}``, or ``None`` when
    there is no open draft to rebuild.
    """
    spec = validate_course_spec(spec)

    draft_version = frappe.db.get_value("Training Course Version", {"course": course, "docstatus": 0}, "name")
    if not draft_version:
        return None

    old_lessons = frappe.get_all("Training Lesson", filters={"course_version": draft_version}, pluck="name")
    # The questions those lessons drew, captured BEFORE the pools are deleted -- afterwards there
    # is nothing left pointing at them and they would sit in the table for ever, unreferenced and
    # invisible, still counting toward the review queue.
    old_questions = set()
    if old_lessons:
        old_questions = set(
            frappe.get_all(
                "Training Quiz Question",
                filters={"parent": ["in", old_lessons], "parenttype": "Training Lesson"},
                pluck="question",
            )
        )

    # Let go of those questions BEFORE the lessons are deleted, not after. `Training Question`
    # carries a Link to the lesson it came from, so frappe's `check_if_doc_is_linked` REFUSES to
    # delete a lesson a question still points at -- and the refusal is the whole delete, not one
    # row. The first run of this on production raised `LinkExistsError` on all ten courses for
    # exactly that reason, and because the failure was caught per course it left ten Error Logs
    # while `Patch Log` recorded the patch as applied and the drafts sat unchanged.
    removed = _release_questions(old_lessons, old_questions)

    modified = str(frappe.db.get_value("Training Course Version", draft_version, "modified"))
    if old_lessons:
        result = training_author.save_draft_version(draft_version, {"deleted_lessons": old_lessons}, modified)
        modified = result["modified"]

    # From here it is the create path exactly, against a version that already exists. Sharing these
    # three helpers is the point: a rebuilt course comes out in the identical shape to a seeded one,
    # and there is no second mapping of a spec onto the model to drift.
    # The one place a rebuild needs MORE than the create path: a spec with no chapters must CLEAR
    # any the draft already has. `_apply_chapters` returns early on an empty list, which is right
    # for a brand-new version -- there is nothing to clear -- and wrong here: the old chapters
    # would survive while every lesson that referenced them has just been deleted, leaving empty
    # groups in the outline that nothing can fill and nobody can explain.
    #
    # It has to happen AFTER the lessons are gone, and it does. `save_draft_version._apply_chapters`
    # refuses to drop a chapter that lessons still point at, so clearing first would be rejected.
    if spec["chapters"]:
        chapter_keys = _apply_chapters(draft_version, spec["chapters"], modified)
        if chapter_keys is not None:
            modified = chapter_keys.pop("_modified")
    else:
        chapter_keys = None
        modified = training_author.save_draft_version(
            draft_version, {"chapters": []}, modified
        )["modified"]
    modified, lesson_names = _apply_lessons(draft_version, spec["lessons"], chapter_keys, modified)
    _apply_quizzes(draft_version, spec["lessons"], lesson_names, modified)

    _refresh_course_fields(course, spec["course"])

    summary = spec_summary(spec)
    return {
        "course": course,
        "draft_version": draft_version,
        "lessons": summary["lessons"],
        "questions": summary["questions"],
        "removed_questions": removed,
    }


def _release_questions(old_lessons, old_questions):
    """Let go of everything the lessons about to be deleted are still holding.

    A `Training Lesson` cannot be deleted while another document Links to it. `check_if_doc_is_linked`
    runs on every `frappe.delete_doc` and throws `LinkExistsError`, and six doctypes carry a Link to
    Training Lesson: `Training Question.source_lesson`, `Training Attempt Question.lesson`,
    `Training Checkpoint.lesson`, `Training Question Thread.lesson`, `Training Submission.lesson`
    and `Training Video Chapter.lesson`.

    Only the first of those is the rebuild's own doing — `_apply_quizzes` sets `source_lesson` on
    every question it mints — so only the first is released here. The other five are a learner or an
    author having done something with this lesson, and the honest answer to those is to let the
    delete throw and report the course as failed. **Forcing the delete past them would leave the
    links dangling**, which is precisely what the framework check exists to prevent; a rebuild that
    quietly breaks a video chapter's anchor is worse than one that refuses.

    Returns the number of questions deleted.
    """
    removed = _drop_orphaned_questions(old_questions, ignoring_lessons=old_lessons)
    _unlink_source_lessons(old_lessons)
    return removed


def _unlink_source_lessons(lessons):
    """Clear `source_lesson` on whatever survived, so the lesson delete is no longer blocked.

    This runs after the drop, so what is left is a question the drop deliberately spared: reviewed,
    hand-written, or still drawn by a lesson on some other version. Those keep their text and their
    review; what they lose is a pointer to a lesson that is about to stop existing. There is no
    third option — the framework will not let the lesson go while the Link is set, and a Link left
    set would point at nothing.
    """
    if not lessons:
        return

    holders = frappe.get_all(
        "Training Question", filters={"source_lesson": ["in", list(lessons)]}, pluck="name"
    )
    for name in holders:
        frappe.db.set_value("Training Question", name, "source_lesson", None)


def _drop_orphaned_questions(names, ignoring_lessons=()):
    """Delete AI-drafted questions the rebuild is about to leave pointing at nothing.

    Guarded three ways, because deleting somebody's content is the one thing here that cannot be
    undone by running the patch again: only questions that were **AI-drafted**, only ones **nobody
    has reviewed**, and only ones **no remaining pool draws**. A hand-written question, a reviewed
    one, or one a second lesson still uses is left alone -- it becomes an unreferenced bank entry,
    which is untidy and recoverable, rather than gone.

    ``ignoring_lessons`` is what makes "no remaining pool draws" answerable **before** the lessons
    are deleted, which is when it has to be answered. Asked without it at this point every question
    is still drawn — by the very pools that are about to go — so the whole set reads as in use and
    nothing is ever dropped. The lessons being deleted are therefore excluded from the question,
    and a pool on any *other* version still counts.
    """
    if not names:
        return 0

    used_filters = {"question": ["in", list(names)], "parenttype": "Training Lesson"}
    if ignoring_lessons:
        used_filters["parent"] = ["not in", list(ignoring_lessons)]
    still_used = set(frappe.get_all("Training Quiz Question", filters=used_filters, pluck="question"))
    candidates = [n for n in names if n not in still_used]
    if not candidates:
        return 0

    droppable = frappe.get_all(
        "Training Question",
        filters={
            "name": ["in", candidates],
            "ai_generated": 1,
            "ai_reviewed_by": ["is", "not set"],
            "is_bank_question": 0,
        },
        pluck="name",
    )
    for name in droppable:
        frappe.delete_doc("Training Question", name, ignore_permissions=True, force=True)
    return len(droppable)


def _refresh_course_fields(course, spec_course):
    """Bring the course record itself back in line with the spec.

    A rewritten spec can change the summary or the category, and neither lives on the version --
    they are on the course, which the seeder set once and nothing has touched since. `weight` and
    `audience` are deliberately NOT re-applied: those are the adopter's to change and a rebuild of
    the *content* has no business reaching into policy.
    """
    updates = {}
    if spec_course.get("summary"):
        updates["summary"] = spec_course["summary"]
    category = spec_course.get("category")
    if category and frappe.db.exists("Training Category", category):
        updates["category"] = category
    if updates:
        frappe.db.set_value("Training Course", course, updates, update_modified=False)
