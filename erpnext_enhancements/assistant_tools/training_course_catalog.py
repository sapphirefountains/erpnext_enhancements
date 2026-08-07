"""training_course_catalog — what courses exist and how they are performing.

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.

**The question this answers is about the course, never about a learner.**
``training_compliance_status`` and ``training_learner_record`` are the
people-shaped tools and hold the permissions for it; this one is course-shaped
and its counts are aggregates.

``item_analysis`` — per-question difficulty — is the part that needs care. A
question answered by three people, reported as "33% correct", is a statement
about one identifiable person's answer wearing a percentage as a disguise. So
questions below :data:`MIN_ATTEMPTS_FOR_ITEM_ANALYSIS` attempts are counted and
withheld rather than shown, and the reply says how many were withheld so the
absence is visible rather than looking like a clean bill of health.
"""

from typing import Any

import frappe
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._common import clamp_limit
from erpnext_enhancements.assistant_tools._gate import annotations_for

# Below this many recorded answers, a per-question success rate is a small-n
# figure that identifies people. Five is not a statistical threshold — it is the
# point at which "who got it wrong" stops being obvious to a colleague.
MIN_ATTEMPTS_FOR_ITEM_ANALYSIS = 5

COURSE_FIELDS = (
    "name",
    "course_title",
    "status",
    "category",
    "current_version",
    "estimated_minutes",
    "passing_score",
    "max_attempts",
    "min_video_coverage",
    "recertify_months",
    "issues_certificate",
    "auto_assign",
    "due_days",
    "allow_self_enrollment",
    "require_supervisor_signoff",
    "published_on",
)


class TrainingCourseCatalog(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "training_course_catalog"  # must match module filename
        self.description = (
            "The course catalogue and how each course is performing in aggregate: "
            "status, current version, gates (passing score, attempt limit, minimum "
            "watch coverage), recertification period, and counts of assignments and "
            "attempts. Ask for one course to get its version history and, optionally, "
            "item_analysis — per-question success rates, for finding the question "
            "everybody fails because it is badly worded rather than hard. "
            "All figures are aggregates over the course: this tool never reports an "
            "individual's score, attempt or answer, and questions with fewer than "
            f"{MIN_ATTEMPTS_FOR_ITEM_ANALYSIS} recorded answers are withheld because a "
            "success rate over three people identifies them. For who owes what, use "
            "training_compliance_status; for one person's history, use "
            "training_learner_record."
        )
        self.category = "Training"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Training Course"
        self.annotations = annotations_for(self.name)
        self.default_config = {"max_rows": 200}
        self.inputSchema = {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "One Training Course docname — returns versions and, if asked, item_analysis",
                },
                "status": {
                    "type": "string",
                    "enum": ["Draft", "Published", "Retired"],
                    "description": "Limit the catalogue to one publication status",
                },
                "category": {"type": "string", "description": "Limit to one Training Category"},
                "item_analysis": {
                    "type": "boolean",
                    "default": False,
                    "description": "Per-question aggregate success rates; requires 'course'",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max courses to return (default 50, max 200)",
                },
            },
            "required": [],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not frappe.db.exists("DocType", "Training Course"):
            return {"error": "The Training module is not installed on this site."}

        course = (arguments.get("course") or "").strip()
        if course:
            return self._one_course(course, bool(arguments.get("item_analysis")))

        filters = {}
        if arguments.get("status"):
            filters["status"] = arguments["status"]
        if arguments.get("category"):
            filters["category"] = arguments["category"]

        limit = clamp_limit(arguments.get("limit"), 50, self.default_config["max_rows"])
        courses = frappe.get_all(
            "Training Course",
            filters=filters,
            fields=list(COURSE_FIELDS),
            order_by="status asc, course_title asc",
            limit_page_length=limit,
        )
        counts = self._counts([c["name"] for c in courses])
        for row in courses:
            row.update(counts.get(row["name"], self._empty_counts()))
        return {
            "courses": courses,
            "count": len(courses),
            "note": "Counts are aggregates. Use training_compliance_status for who owes what.",
        }

    def _one_course(self, course, item_analysis):
        if not frappe.db.exists("Training Course", course):
            return {"error": f"No Training Course named {course}."}

        doc = frappe.db.get_value("Training Course", course, list(COURSE_FIELDS), as_dict=True)
        payload = dict(doc)
        payload.update(self._counts([course]).get(course, self._empty_counts()))
        payload["versions"] = frappe.get_all(
            "Training Course Version",
            filters={"course": course},
            fields=[
                "name",
                "version_number",
                "is_current",
                "change_type",
                "total_lessons",
                "total_questions",
                "estimated_minutes",
                "published_on",
                "published_by",
            ],
            order_by="version_number desc",
        )
        if item_analysis:
            payload["item_analysis"] = self._item_analysis(course)
        return payload

    def _empty_counts(self):
        return {
            "assignments_total": 0,
            "assignments_open": 0,
            "assignments_completed": 0,
            "attempts_total": 0,
            "attempts_passed": 0,
        }

    def _counts(self, courses):
        """Assignment and attempt tallies per course.

        Folded in Python from two flat reads rather than five COUNT queries per
        course — the same approach `training_compliance_status._by_course` takes,
        and for the same reason: the catalogue is small and the round trips are
        not.
        """
        if not courses:
            return {}
        out = {name: self._empty_counts() for name in courses}

        if frappe.db.exists("DocType", "Training Assignment"):
            for row in frappe.get_all(
                "Training Assignment",
                filters={"course": ["in", courses]},
                fields=["course", "status"],
                limit_page_length=0,
            ):
                bucket = out.setdefault(row["course"], self._empty_counts())
                bucket["assignments_total"] += 1
                if row["status"] == "Completed":
                    bucket["assignments_completed"] += 1
                else:
                    bucket["assignments_open"] += 1

        if frappe.db.exists("DocType", "Training Attempt"):
            for row in frappe.get_all(
                "Training Attempt",
                filters={"course": ["in", courses]},
                fields=["course", "status"],
                limit_page_length=0,
            ):
                bucket = out.setdefault(row["course"], self._empty_counts())
                bucket["attempts_total"] += 1
                if row["status"] in ("Passed", "Completed"):
                    bucket["attempts_passed"] += 1
        return out

    def _item_analysis(self, course):
        """Per-question success rates, aggregate only.

        Reads Training Attempt Question, which carries `user`, `given_answer` and
        `attempt` — none of which leaves this method. The projection is the
        narrowest that answers the question, and questions with too few answers
        are withheld rather than averaged into deniability.
        """
        if not frappe.db.exists("DocType", "Training Attempt Question"):
            return {"available": False, "reason": "No attempt records on this site."}

        rows = frappe.get_all(
            "Training Attempt Question",
            filters={"course": course},
            # Deliberately not `user`, `given_answer`, `attempt` or
            # `answered_on`: any of them turns an aggregate back into a record of
            # what one person did.
            fields=["question", "question_text_snapshot", "is_correct"],
            limit_page_length=0,
        )
        if not rows:
            return {"available": False, "reason": "No answers recorded for this course yet."}

        tally = {}
        for row in rows:
            key = row.get("question") or row.get("question_text_snapshot") or "(unknown)"
            entry = tally.setdefault(
                key, {"question": key, "text": row.get("question_text_snapshot"), "answers": 0, "correct": 0}
            )
            entry["answers"] += 1
            if row.get("is_correct"):
                entry["correct"] += 1

        shown, withheld = [], 0
        for entry in tally.values():
            if entry["answers"] < MIN_ATTEMPTS_FOR_ITEM_ANALYSIS:
                withheld += 1
                continue
            entry["correct_pct"] = round(100.0 * entry["correct"] / entry["answers"], 1)
            shown.append(entry)
        shown.sort(key=lambda e: e["correct_pct"])

        return {
            "available": True,
            "questions": shown,
            # Named, not silently dropped: an item analysis that quietly omits the
            # thin questions reads as though every question is well-answered.
            "withheld_below_threshold": withheld,
            "min_answers_for_inclusion": MIN_ATTEMPTS_FOR_ITEM_ANALYSIS,
            "note": (
                "Aggregate success rates over all learners. Questions with fewer than "
                f"{MIN_ATTEMPTS_FOR_ITEM_ANALYSIS} recorded answers are withheld, because a "
                "percentage over three people identifies them. Lowest success rate first — "
                "a question everybody fails is usually badly worded rather than hard."
            ),
        }


__all__ = ["TrainingCourseCatalog"]
