"""draft_course_spec — draft a whole training course as a Course Spec (read-only).

The *generate* half of AI-authored trainings. Given a plain-language brief, it asks
ERPNext's own Vertex client for a Course Spec — a course, its lessons, content
blocks and quizzes — validates it against
:mod:`erpnext_enhancements.training.course_spec`, stashes it, and returns a token
plus a readable preview. It **writes no business records**: it drafts a proposal a
person reviews, and the companion write tool ``author_training_course`` (or the
returned ``draft_token``) is what actually builds the course, behind the write gate.

Only imported by frappe_assistant_core's tool loader via the assistant_tools hook;
see the package docstring for the FAC-optional invariant.

READ-ONLY in the sense the gate cares about: it creates no Training records. It does
make one Vertex call (logged to AI Model Usage like every other drafting feature)
and caches the result for an hour, so it is listed in ``_gate.py``'s
``EXPLICIT_READONLY`` set — a confirmation card in front of "draft me a proposal"
would be pure friction, and there is nothing to undo.

The safety of the eventual write does not live here: this only proposes. Every
question the builder later creates from the spec is stamped AI-generated and
unreviewed, and the course lands as a Draft — both enforced by the materializer, not
by this tool.
"""

import json
from typing import Any

import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for
from erpnext_enhancements.training.course_spec import (
    MAX_LESSONS,
    MAX_QUESTIONS_PER_LESSON,
    spec_summary,
    validate_course_spec,
)

# Recorded on the AI Model Usage row so whole-course drafting is accounted for
# separately from the per-lesson quiz/checkpoint drafting in api/training_ai.py.
FEATURE = "training_course_draft"

DEFAULT_LESSONS = 4

_SYSTEM = """You author complete workplace training courses for a fountain design, build, service and rental company, as a single JSON object.

Rules you must follow:
- Answer with ONE JSON object and nothing else. No prose before or after it, and no markdown code fences.
- Shape: {"course": {"course_title": str, "summary": str, "category"?: str, "weight"?: "Optional"|"Required", "audience"?: "Internal Staff"|"Customers"|"Both"}, "chapters"?: [{"title": str, "description": str}], "lessons": [{"lesson_title": str, "chapter"?: int, "summary": str, "estimated_minutes": int, "blocks": [ ... ], "quiz"?: {"pass_score": int, "questions_to_ask": int, "questions": [ ... ]}}]}
- A lesson's "chapter" is a 0-based index into "chapters". Omit it for an ungrouped lesson. Use chapters only when the course is long enough to group.
- Each block is one of these shapes ONLY (no images, video, PDFs or file downloads — you cannot supply media):
  - {"block_type": "Rich Text", "heading"?: str, "content": "<p>real teaching text, simple HTML</p>"}
  - {"block_type": "Callout", "heading"?: str, "content": str, "callout_tone"?: "Tip"|"Warning"|"Danger"}
  - {"block_type": "Divider"}
  - {"block_type": "Checklist", "heading"?: str, "items": [str, ...]}
  - {"block_type": "Flashcards", "heading"?: str, "cards": [{"front": str, "back": str}]}
  - {"block_type": "Accordion", "heading"?: str, "panels": [{"title": str, "body": "<p>...</p>"}]}
- Write real, substantive teaching content in the blocks — not placeholders like "Lesson content here". A learner reads this.
- Each quiz question: {"question": str, "type": "Single Choice"|"Multiple Choice"|"True-False", "explanation": str, "options": [{"text": str, "is_correct": true|false}]}. Two to six options, at least one correct and never all of them. "Single Choice" and "True-False" have exactly one correct option; a "True-False" question's two options must be spelled exactly True and False.
- Questions must be answerable from the lesson you wrote, and every question needs a short "explanation" of why the answer is right.
- Keep to the number of lessons requested."""


class DraftCourseSpec(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "draft_course_spec"  # must match module filename
        self.description = (
            "Draft a complete training course from a plain-language brief: it "
            "proposes a course with lessons, teaching content and quizzes as a "
            "structured spec and returns a preview plus a 'draft_token'. It writes "
            "nothing — to actually create the course, pass that draft_token to "
            "author_training_course (which is gated and creates an unpublished "
            "Draft for a person to review). Use 'brief' for what the course should "
            "teach; optionally set 'num_lessons', 'include_quiz', 'weight', "
            "'audience' and 'category'. Show the returned preview to the author and "
            "let them refine the brief before building."
        )
        self.category = "Training"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Training Course"
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "brief": {
                    "type": "string",
                    "description": "What the course should teach, and any specifics (topics, tone, who it is for).",
                },
                "num_lessons": {
                    "type": "integer",
                    "description": f"How many lessons to draft (1-{MAX_LESSONS}). Default {DEFAULT_LESSONS}.",
                },
                "include_quiz": {
                    "type": "boolean",
                    "description": "Whether to draft an end-of-lesson quiz for each lesson. Default true.",
                },
                "weight": {
                    "type": "string",
                    "enum": ["Optional", "Required"],
                    "description": "Optional (self-serve library) or Required (assigned). Default Optional.",
                },
                "audience": {
                    "type": "string",
                    "enum": ["Internal Staff", "Customers", "Both"],
                    "description": "Who the course is for. Default Internal Staff.",
                },
                "category": {
                    "type": "string",
                    "description": "An existing Training Category name to file it under, if any.",
                },
            },
            "required": ["brief"],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from frappe.utils import cint

        if not frappe.db.exists("DocType", "Training Course"):
            return {"error": "The Training module is not installed on this site."}
        if not frappe.has_permission("Training Course", "create"):
            frappe.throw(
                _("You need permission to create Training Courses to draft one."),
                frappe.PermissionError,
            )
        self._require_ai_enabled()

        args = arguments or {}
        brief = (args.get("brief") or "").strip()
        if not brief:
            frappe.throw(_("Say what the course should teach ('brief')."), frappe.ValidationError)

        num_lessons = max(1, min(cint(args.get("num_lessons")) or DEFAULT_LESSONS, MAX_LESSONS))
        include_quiz = args.get("include_quiz")
        include_quiz = True if include_quiz is None else bool(include_quiz)

        prompt = self._build_prompt(args, brief, num_lessons, include_quiz)

        spec, error = self._generate(prompt)
        if spec is None:
            return {"drafted": False, "message": error}

        from erpnext_enhancements.api.training_course_authoring import stash_spec

        token = stash_spec(spec)
        summary = spec_summary(spec)
        return {
            "drafted": True,
            "draft_token": token,
            "summary": summary,
            "preview": self._preview(spec),
            "spec": spec,
            "next_step": (
                "Show the preview to the author. When they are happy, call "
                "author_training_course with this draft_token to build it (it is "
                "gated and lands as an unpublished Draft for review)."
            ),
        }

    # ------------------------------------------------------------ internals

    def _require_ai_enabled(self):
        from erpnext_enhancements.training.doctype.training_settings.training_settings import is_enabled

        if not is_enabled("ai_assist_enabled"):
            frappe.throw(
                _("AI assistance is switched off. A Training Manager can enable it in Training Settings.")
            )

    def _build_prompt(self, args, brief, num_lessons, include_quiz):
        lines = [
            f"Draft a training course of {num_lessons} lesson(s).",
            "Include an end-of-lesson quiz for every lesson."
            if include_quiz
            else "Do not include any quizzes.",
        ]
        if include_quiz:
            lines.append(f"Keep each quiz to at most {min(6, MAX_QUESTIONS_PER_LESSON)} questions.")
        weight = (args.get("weight") or "").strip()
        if weight in ("Optional", "Required"):
            lines.append(f'Set course "weight" to "{weight}".')
        audience = (args.get("audience") or "").strip()
        if audience in ("Internal Staff", "Customers", "Both"):
            lines.append(f'Set course "audience" to "{audience}".')
        category = (args.get("category") or "").strip()
        if category:
            lines.append(f'Set course "category" to "{category}".')
        lines.append("\nBrief:\n" + brief)
        return "\n".join(lines)

    def _generate(self, prompt):
        """(spec, error) — a validated spec or a message. One retry on a spec that
        parses but does not validate, nudging the model with what was wrong."""
        last_error = ""
        for attempt in (1, 2):
            text = self._ask_vertex(
                prompt if attempt == 1 else f"{prompt}\n\nYour previous answer was rejected: {last_error}\nReturn corrected JSON only."
            )
            parsed = self._parse(text)
            if parsed is None:
                last_error = "the reply was not a single JSON object"
                continue
            try:
                return validate_course_spec(parsed), ""
            except frappe.exceptions.ValidationError as exc:
                last_error = frappe.utils.strip_html(str(exc)) or "the spec was not valid"
                continue
        return None, _(
            "The AI drafted a course that could not be turned into a valid spec ({0}). "
            "Nothing was created — try again, or narrow the brief."
        ).format(last_error)

    def _ask_vertex(self, prompt):
        try:
            from erpnext_enhancements.api.gemini import generate_content_with_vertex_ai

            settings = frappe.get_doc("Triton Settings")
            text, _thoughts = generate_content_with_vertex_ai(prompt, _SYSTEM, settings, feature=FEATURE)
            return text
        except Exception:
            frappe.log_error(
                f"Course drafting failed\n{frappe.get_traceback()}", "Training AI"
            )
            frappe.throw(
                _("The AI service did not respond. Nothing has been changed — try again.")
            )

    def _parse(self, text):
        raw = (text or "").strip()
        if not raw:
            return None
        # Defensive fence strip: models occasionally wrap JSON in ``` despite the
        # instruction not to (same slip work_breakdown/deep_research guard against).
        if raw.startswith("```"):
            lines = raw.splitlines()
            if len(lines) > 2:
                raw = "\n".join(lines[1:-1]).strip()
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    def _preview(self, spec):
        course = spec.get("course") or {}
        lines = [f"**{course.get('course_title', '')}**"]
        if course.get("summary"):
            lines.append(course["summary"])
        chapters = spec.get("chapters") or []
        for index, lesson in enumerate(spec.get("lessons") or [], start=1):
            questions = len((lesson.get("quiz") or {}).get("questions") or [])
            bits = [f"{len(lesson.get('blocks') or [])} block(s)"]
            if questions:
                bits.append(f"{questions} quiz question(s)")
            chapter = ""
            if "chapter" in lesson and lesson["chapter"] < len(chapters):
                chapter = f" [{chapters[lesson['chapter']].get('title', '')}]"
            lines.append(f"{index}. {lesson.get('lesson_title', '')}{chapter} — {', '.join(bits)}")
        return "\n".join(lines)


__all__ = ["DraftCourseSpec"]
