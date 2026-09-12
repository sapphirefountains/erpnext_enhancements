"""publish_training_course — freeze a draft course version and make it live (gated).

The step that had no tool. ``draft_course_spec`` proposes a course,
``author_training_course`` builds it as a **Draft**, and until now nothing could take
it the last inch: publishing was reachable only from the Publish button on the
Training Course form, because it is not a document submit. It is
``api/training_author.publish_version``, which materializes ``toc_json`` and
``content_hash`` from the lessons, *then* submits, then refreshes the course status,
queues the video copies and (for a Required course with ``auto_assign``) fans the
assignments out.

That shape is why the generic tools cannot stand in for it, and the failure is quiet
in both directions: ``submit_document`` skips the materializer and is refused by
``_require_materialized_content``; ``update_document`` on ``toc_json`` would satisfy
that gate while writing a table of contents nothing derived from the lessons.

Only imported by frappe_assistant_core's tool loader via the assistant_tools hook;
see the package docstring for the FAC-optional invariant.

WRITES, and is a **one-way door** — hence the gate
---------------------------------------------------------------------------
In ``_gate.APP_MUTATING``, so with AI write gating ON this returns an
``awaiting_user_confirmation`` envelope and an **AI Pending Action**, and publishes
only once a human confirms in the desk (the gate re-runs ``execute`` as the
confirming user). Risk is **Medium**, deliberately, not Low: unlike
``author_training_course`` — a create that yields a draft nobody can see — publishing
is not undoable from this tool, and three of its effects reach people:

* ``_materialize_lessons`` **freezes the lesson titles into** ``toc_json``, and a
  submitted version cannot be edited afterwards. A typo published is a typo for good,
  or a new version.
* a Required course with ``auto_assign`` fans assignments out to every matching
  learner, with notifications.
* ``change_type = "Material Change (require retake)"`` **supersedes existing completions**, which is
  the one argument here that can invalidate other people's training records. It is
  never defaulted; the caller must choose.

It is not ``HIGH_RISK`` (no data is destroyed and nothing arbitrary executes), but it
is not a plain create either, so it lands on the fail-safe Medium band.

Authority is not re-implemented here. ``publish_version`` calls ``_require_manager``
and refuses anyone without Training Manager, and it refuses a course with unreviewed
AI-drafted questions — which is what keeps "an AI drafted it" and "an AI shipped it"
two different things.
"""

import json
from pathlib import Path
from typing import Any

import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for

#: The two `change_type` values `TrainingCourseVersion._require_change_type` accepts,
#: READ FROM THE DOCTYPE rather than retyped. Writing them by hand got "Material
#: Change (require retake)" wrong on the first attempt, and a wrong literal here would
#: pass every bench-free test and then be refused by the controller at the moment of
#: publishing -- the worst possible place to find out.
#:
#: The controller's own constants cannot be imported: the bench-free test suites
#: install a `frappe` stub that is a module rather than a package, so importing a
#: doctype controller (which does `from frappe.model.document import Document`) breaks
#: tool collection. The JSON is the authority the Select actually enforces anyway, and
#: `tests/test_training_publish_tool.py` asserts the controller agrees with it.
_VERSION_JSON = (
    Path(__file__).resolve().parents[1]
    / "training/doctype/training_course_version/training_course_version.json"
)


def _change_types():
    doc = json.loads(_VERSION_JSON.read_text(encoding="utf-8"))
    for field in doc.get("fields", []):
        if field.get("fieldname") == "change_type":
            return [o for o in (field.get("options") or "").splitlines() if o.strip()]
    raise RuntimeError("Training Course Version has no change_type field")


MINOR_EDIT, MATERIAL_CHANGE = _change_types()


class PublishTrainingCourse(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "publish_training_course"  # must match module filename
        self.description = (
            "Publish a Training Course's draft version: freeze its table of contents "
            "and make it the live version learners see. Pass the Training Course "
            "docname; the open draft is found for you. "
            "'change_type' is REQUIRED and is a real decision, not a formality: "
            "'Minor Edit (keep completions)' leaves existing completions valid, "
            "'Material Change (require retake)' invalidates them and makes "
            "everyone who passed retake it. Ask the author which it is — never guess. "
            "This tool WRITES and is a one-way door: when AI write gating is on it "
            "returns status='awaiting_user_confirmation' with an action_id and "
            "publishes nothing until a human confirms in ERPNext, then call "
            "check_ai_pending_action with that action_id for the result. Never claim "
            "the course was published from the envelope alone. "
            "Publishing freezes the lesson titles permanently and, for a Required "
            "course with auto-assign on, assigns it to every matching learner with "
            "notifications. It refuses a course whose AI-drafted quiz questions have "
            "not been reviewed by a person, and requires the Training Manager role."
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
                        "Training Course docname (e.g. TRN-CRS-00005). Its open draft "
                        "version is resolved server-side."
                    ),
                },
                "change_type": {
                    "type": "string",
                    "enum": [MINOR_EDIT, MATERIAL_CHANGE],
                    "description": (
                        "Minor Edit keeps existing completions valid. Material Change "
                        "requires a retake, so everyone who has already passed must "
                        "take it again. The author's call, not the model's."
                    ),
                },
                "release_notes": {
                    "type": "string",
                    "description": "Optional note recorded on the version: what changed and why.",
                },
            },
            "required": ["course", "change_type"],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not frappe.db.exists("DocType", "Training Course"):
            return {"error": "The Training module is not installed on this site."}

        from erpnext_enhancements.api.training_author import publish_version

        args = arguments or {}
        course = (args.get("course") or "").strip()
        change_type = (args.get("change_type") or "").strip()
        release_notes = (args.get("release_notes") or "").strip() or None

        if not course:
            frappe.throw(_("Which Training Course? Pass its docname."), frappe.ValidationError)
        if not frappe.db.exists("Training Course", course):
            return {"error": f"No Training Course named {course!r}."}
        if change_type not in (MINOR_EDIT, MATERIAL_CHANGE):
            # Not a throw: the model can fix this by asking the author, and an
            # error with the two options in it is more useful than a traceback.
            return {
                "error": (
                    "change_type must be exactly one of "
                    f"{MINOR_EDIT!r} or {MATERIAL_CHANGE!r}. Ask the author which "
                    "it is — Material Change invalidates everyone's existing "
                    "completion of this course."
                )
            }

        draft = frappe.db.get_value(
            "Training Course Version",
            {"course": course, "docstatus": 0},
            ["name", "version_number"],
            as_dict=True,
            order_by="creation desc",
        )
        if not draft:
            return {
                "error": (
                    f"{course} has no unpublished draft version. Either it is already "
                    "published and unchanged since, or a draft needs creating first."
                )
            }

        # publish_version does the work and owns every rule: Training Manager only,
        # no unreviewed AI questions, and the before_submit gates on finished blocks,
        # finished checkpoints and materialized content.
        result = publish_version(draft.name, change_type, release_notes) or {}
        return {
            "success": True,
            "course": course,
            "course_version": draft.name,
            "version_number": draft.version_number,
            "change_type": change_type,
            **(result if isinstance(result, dict) else {"result": result}),
        }


__all__ = ["PublishTrainingCourse"]
