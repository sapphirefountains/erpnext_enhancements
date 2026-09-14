"""create_training_draft_version — open an editable draft of a published course (gated).

The missing rung in the authoring ladder, and its absence had a concrete cost.
``draft_course_spec`` proposes a course, ``author_training_course`` builds a **new**
one, and ``publish_training_course`` makes a draft live. Nothing could open a draft
of a course that *already exists* — so an assistant asked to correct a published
course had exactly two options, and both were wrong:

* edit the published version's lessons in place with ``update_document``, which the
  module forbids for a reason that is not stylistic — learners are reading that
  version, and a completion record says precisely what somebody passed. Frappe would
  have allowed it (a Training Lesson is a plain document), so this is a rule the
  data model does not enforce and a tool therefore must not invite.
* give up and ask a human to press **New Draft Version** on the form.

The second is honest but it stops an agent mid-task on a one-click step, which is
how a correction gets abandoned rather than made. This closes that gap.

It is ``api/training_author.create_draft_version``, which clones the live version's
chapters and lessons **preserving every stable key** (``lesson_key``, ``block_key``,
checkpoint and chapter keys). That preservation is the whole point and is why a
generic create cannot stand in: ``create_document`` on Training Course Version makes
an empty shell with no lessons in it, and hand-copying the rows afterwards would mint
fresh keys — which silently strands every in-flight learner's resume position, every
in-video checkpoint and every video chapter, because all of them join on those keys
rather than on an index.

Only imported by frappe_assistant_core's tool loader via the assistant_tools hook;
see the package docstring for the FAC-optional invariant.

WRITES, and is gated — but it is the gentlest write in this group
---------------------------------------------------------------------------
In ``_gate.APP_MUTATING``, so with AI write gating ON it returns an
``awaiting_user_confirmation`` envelope and creates nothing until a human confirms.

Its consequences are deliberately small, and worth stating because they argue
against the reflex of treating every training write like ``publish_training_course``:

* **No learner sees anything change.** The live version stays live and stays the
  version assignments and completions point at. A draft is invisible until published.
* **It is reversible.** An unwanted draft is deleted; nothing else moved.
* **It cannot supersede a completion**, cannot assign anything to anybody, and
  cannot freeze a table of contents. Those all belong to publishing.

So the risk band it lands on (Medium, by being in ``APP_MUTATING`` and not
``HIGH_RISK``) is if anything generous. The reason it is gated at all is that it is
a write on a live training record, and the one failure worth a confirmation card is
the boring one: opening a draft on the *wrong course* and then editing it for an
hour before anybody notices.

Authority is not re-implemented here. ``create_draft_version`` calls
``_require_author`` and then ``check_permission("write")`` on the course itself, so a
Training Author who may not touch this particular course is refused by the same rule
that refuses them in the Desk.

One refusal is surfaced rather than worked around: a course may hold **only one open
draft**. Two drafts of the same course would both claim the same next version number,
and the second to publish would quietly overwrite the first author's work. When a
draft already exists this returns it rather than erroring, because "the thing you
asked for already exists and here it is" is the useful answer — but it never opens a
second one.
"""

from typing import Any

import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for


class CreateTrainingDraftVersion(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "create_training_draft_version"  # must match module filename
        self.description = (
            "Open an editable draft version of an existing Training Course, so its "
            "lessons and content can be corrected. Pass the Training Course docname. "
            "The draft is cloned from the live version and PRESERVES the stable keys "
            "(lesson_key, block_key, checkpoint keys), which is what keeps in-flight "
            "learners where they were — so always use this rather than building a "
            "Training Course Version by hand. "
            "A published version must never be edited in place: learners are reading "
            "it and their completion records state exactly what they passed. "
            "This tool WRITES: when AI write gating is on it returns "
            "status='awaiting_user_confirmation' with an action_id and creates "
            "nothing until a human confirms in ERPNext, then call "
            "check_ai_pending_action with that action_id for the result. Never claim "
            "the draft was created from the envelope alone. "
            "It changes nothing a learner can see — the live version stays live — and "
            "an unwanted draft can simply be deleted. A course may hold only one open "
            "draft; if one already exists this returns it instead of opening a second. "
            "Requires an authoring role and write permission on that course."
        )
        self.category = "Training"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Training Course"
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": (
                        "Training Course docname (e.g. TRN-CRS-00005). The live version "
                        "is resolved and cloned server-side."
                    ),
                },
                "change_type": {
                    "type": "string",
                    "description": (
                        "Optional, and only a note on the draft at this stage — the "
                        "decision that matters is made at publish time by "
                        "publish_training_course, which is where Material Change "
                        "actually supersedes completions. Leave it out unless the "
                        "author has already said which it will be."
                    ),
                },
            },
            "required": ["course"],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not frappe.db.exists("DocType", "Training Course"):
            return {"error": "The Training module is not installed on this site."}

        from erpnext_enhancements.api.training_author import create_draft_version

        args = arguments or {}
        course = (args.get("course") or "").strip()
        change_type = (args.get("change_type") or "").strip() or None

        if not course:
            frappe.throw(_("Which Training Course? Pass its docname."), frappe.ValidationError)
        if not frappe.db.exists("Training Course", course):
            return {"error": f"No Training Course named {course!r}."}

        # Answered before calling, because `create_draft_version` throws on an
        # existing draft and a thrown error reads as a failure. It is not one: the
        # caller wanted an editable draft of this course and there is one.
        existing = frappe.db.get_value(
            "Training Course Version",
            {"course": course, "docstatus": 0},
            ["name", "version_number"],
            as_dict=True,
            order_by="creation desc",
        )
        if existing:
            return {
                "success": True,
                "created": False,
                "course": course,
                "draft_version": existing.name,
                "version_number": existing.version_number,
                "note": (
                    f"{course} already had an open draft. Returning it rather than "
                    "opening a second — two drafts of one course would both claim the "
                    "same next version number, and the second to publish would "
                    "overwrite the first author's work. Edit this one."
                ),
            }

        draft = create_draft_version(course, change_type)
        # `create_draft_version` returns the new docname; read the rest back so the
        # caller is told what it actually got rather than what was requested.
        name = draft if isinstance(draft, str) else (draft or {}).get("name")
        version_number = frappe.db.get_value("Training Course Version", name, "version_number")
        lessons = frappe.db.count("Training Lesson", {"course_version": name})

        return {
            "success": True,
            "created": True,
            "course": course,
            "draft_version": name,
            "version_number": version_number,
            "lessons_cloned": lessons,
            "live_version": frappe.db.get_value("Training Course", course, "current_version"),
            "note": (
                "Unpublished draft, cloned from the live version with lesson and block "
                "keys preserved. Nothing a learner sees has changed. Edit the lessons "
                "on this draft, then publish with publish_training_course — which is "
                "where the Minor Edit / Material Change decision is made."
            ),
        }


__all__ = ["CreateTrainingDraftVersion"]
