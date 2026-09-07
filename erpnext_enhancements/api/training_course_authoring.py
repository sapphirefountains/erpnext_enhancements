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
