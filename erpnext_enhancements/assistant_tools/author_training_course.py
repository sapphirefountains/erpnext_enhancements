"""author_training_course — build a whole training course from a Course Spec (gated).

The *write* half of AI-authored trainings. Given a Course Spec — either an inline
one, or (preferred) a ``draft_token`` from ``draft_course_spec`` so a whole course
need not be re-sent as tool arguments — it creates a Training Course and an
unpublished draft via the deterministic materializer
(``api/training_course_authoring.author_course_from_spec``). The model never writes
records itself; it fills a spec, and ERPNext builds it the same way the manual
Training Builder does, so every AI-seeded course has the identical shape and flow.

Only imported by frappe_assistant_core's tool loader via the assistant_tools hook;
see the package docstring for the FAC-optional invariant.

WRITES — and therefore goes through the AI write-confirmation gate (``_gate.py``):
``author_training_course`` is in the gate's ``APP_MUTATING`` set, so when AI write
gating is ON this returns an ``awaiting_user_confirmation`` envelope and an
**AI Pending Action** instead of building; the course is created only after a human
confirms in the desk (the gate re-runs ``execute`` as the confirming user). When
gating is OFF it builds immediately (still audited). Risk is **Low** (a create that
produces an unpublished draft).

Two safety properties are inherited from the materializer, not re-implemented here:
every quiz question is stamped AI-generated and **unreviewed**, so the course cannot
be published until a person reviews it; and the course lands as a ``Draft``, never
published. Seeding is not shipping.
"""

from typing import Any

import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for
from erpnext_enhancements.training.course_spec import COURSE_SPEC_SCHEMA


class AuthorTrainingCourse(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "author_training_course"  # must match module filename
        self.description = (
            "Build a whole training course (a Training Course plus an unpublished "
            "draft version with lessons, content blocks and quizzes) from a Course "
            "Spec. Prefer passing a 'draft_token' from draft_course_spec — that "
            "builds the course you just drafted without re-sending it. Otherwise "
            "pass an inline 'spec' shaped like the schema. "
            "This tool WRITES: when AI write gating is on it returns "
            "status='awaiting_user_confirmation' with an action_id and creates "
            "nothing until a human confirms in ERPNext — then call "
            "check_ai_pending_action with that action_id for the result. Never claim "
            "the course was created from the envelope alone. "
            "The course is always created as an unpublished Draft, and every quiz "
            "question is flagged AI-generated and must be reviewed by a person in the "
            "Training Builder before the course can be published — tell the author to "
            "open the draft and review it."
        )
        self.category = "Training"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Training Course"
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "draft_token": {
                    "type": "string",
                    "description": "Token returned by draft_course_spec. The preferred input — the drafted spec is retrieved server-side.",
                },
                "spec": dict(
                    COURSE_SPEC_SCHEMA,
                    description="An inline Course Spec, if you are not using a draft_token. Constrained to the course/chapters/lessons shape.",
                ),
            },
            "required": [],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not frappe.db.exists("DocType", "Training Course"):
            return {"error": "The Training module is not installed on this site."}

        from erpnext_enhancements.api.training_course_authoring import (
            author_course_from_spec,
            pop_spec,
        )

        args = arguments or {}
        token = (args.get("draft_token") or "").strip()
        spec = args.get("spec")

        if token:
            spec = pop_spec(token)
            if spec is None:
                return {
                    "error": (
                        "That draft token has expired or was already used. Draft the "
                        "course again with draft_course_spec, then build it."
                    )
                }
        elif not spec:
            frappe.throw(
                _("Provide either a 'draft_token' from draft_course_spec or an inline 'spec'."),
                frappe.ValidationError,
            )

        # author_course_from_spec re-validates, checks author role + create
        # permission (as the confirming user), and builds atomically.
        result = author_course_from_spec(spec)
        return {"success": True, **result}


__all__ = ["AuthorTrainingCourse"]
