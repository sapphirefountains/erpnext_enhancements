# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Course Version — a frozen snapshot of a course's content.

**The docstatus is the publishing mechanism, and that is the whole point.**

* ``docstatus 0`` — draft. Authors edit freely.
* ``docstatus 1`` — published. Frozen by the framework.
* ``docstatus 2`` — retired.

Publishing *is* ``submit()``. Because the runtime only ever reads submitted
versions, "an author saved a half-finished edit into a live course" is not a bug
that has to be prevented by discipline — it cannot be expressed. That is worth
more than any amount of validation.

Amend is deliberately disabled. Amending would produce a second document claiming
to be the same version, which is precisely the ambiguity a completion's stored
``version_number`` exists to avoid. To change a published course you create a new
draft version (``api/training_author.create_draft_version``), which deep-clones the
content **preserving every ``lesson_key`` / ``block_key`` / ``checkpoint_key``** —
that key preservation is what lets a minor edit leave in-flight learners exactly
where they were.

The heavy lifting of publishing — materializing each lesson's answer-free payload
and its separate answer key, computing the table of contents and the content hash,
and applying the author's Minor/Material decision to existing completions — lives
in ``api/training_author.publish_version``. It runs *before* ``submit()``. This
controller only guards the transitions.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, now_datetime

MINOR_EDIT = "Minor Edit (keep completions)"
MATERIAL_CHANGE = "Material Change (require retake)"


class TrainingCourseVersion(Document):
	def before_naming(self):
		"""Assign the version number BEFORE the name is built, not in validate.

		The autoname is ``format:{course}-V{version_number}``, and Frappe resolves
		that inside ``set_new_name`` — which ``Document.insert`` calls at line 729,
		*before* ``run_before_save_methods`` (line 734) runs ``validate``. Assigning
		the number in ``validate`` is therefore too late: every version of a course
		would be named ``<course>-V`` with no number, and the second one would die
		on a duplicate primary key, making a second version of any course
		impossible — which is the entire point of this doctype.

		``frappe.model.naming.set_new_name`` calls ``doc.run_method("before_naming")``
		(naming.py line 156), so this is the one hook that runs early enough.
		The same trap is documented, and solved the same way, on
		``kpi_dashboards/doctype/hr_stat_entry``.
		"""
		self._assign_version_number()

	def validate(self):
		self._reject_amend()
		# Belt and braces: before_naming only fires on insert, so a row that
		# somehow reached validate without a number still gets one. No-ops once set.
		self._assign_version_number()
		self._assign_chapter_keys()

	def before_submit(self):
		self._require_change_type()
		self._require_content()

	def on_submit(self):
		self.db_set(
			{
				"published_on": now_datetime(),
				"published_by": frappe.session.user,
				"submitted_for_review": 0,
			},
			update_modified=False,
		)
		self._become_the_live_version()
		self._refresh_course()

	def on_cancel(self):
		"""Cancelling retires a published version. If it was the live one the
		course drops back to whatever is left, rather than silently continuing to
		serve content somebody deliberately withdrew."""
		if cint(self.is_current):
			self.db_set("is_current", 0, update_modified=False)
			self._promote_previous_version()
		self._refresh_course()

	# ------------------------------------------------------------------ helpers

	def _reject_amend(self):
		if self.amended_from:
			frappe.throw(
				_("A published version is never amended — an amendment would be a second document claiming "
				  "to be the same version, and completions record version numbers. Create a new draft "
				  "version from the course instead.")
			)

	def _assign_version_number(self):
		if cint(self.version_number):
			return
		highest = frappe.db.sql(
			"""select max(version_number) from `tabTraining Course Version` where course = %s""",
			(self.course,),
		)
		self.version_number = cint(highest[0][0] if highest else 0) + 1

	def _assign_chapter_keys(self):
		"""Stable keys, generated once. Lessons point at these rather than at a row
		index, so reordering chapters in the builder never orphans a lesson."""
		used = set()
		for row in self.chapters or []:
			key = (row.chapter_key or "").strip()
			if not key or key in used:
				key = frappe.generate_hash(length=10)
			row.chapter_key = key
			used.add(key)

	def _require_change_type(self):
		"""The Minor/Material decision is the author's, and it has to be made
		before the version freezes — afterwards there is no way to know what they
		intended, and guessing either nags fifteen people over a typo or lets a
		rewritten safety course pass on an old completion."""
		if self.change_type not in (MINOR_EDIT, MATERIAL_CHANGE):
			frappe.throw(
				_("Say whether this is a minor edit or a material change before publishing. A minor edit "
				  "keeps everyone's completion; a material change asks them all to retake it.")
			)

	def _require_content(self):
		if not frappe.db.exists("Training Lesson", {"course_version": self.name}):
			frappe.throw(_("This version has no lessons yet, so there is nothing for a learner to do."))

	def _become_the_live_version(self):
		"""Exactly one submitted version per course is live. Demote the incumbent
		with a targeted UPDATE — loading and saving it would re-run its validation
		and, worse, its own on_submit side effects."""
		frappe.db.sql(
			"""
			update `tabTraining Course Version`
			set is_current = 0
			where course = %s and name != %s and is_current = 1
			""",
			(self.course, self.name),
		)
		self.db_set("is_current", 1, update_modified=False)
		frappe.db.set_value(
			"Training Course",
			self.course,
			{
				"current_version": self.name,
				"published_by": frappe.session.user,
				"published_on": now_datetime(),
				"estimated_minutes": cint(self.estimated_minutes),
			},
			update_modified=False,
		)

	def _promote_previous_version(self):
		"""When the live version is cancelled, fall back to the highest-numbered
		version still submitted. Leaving the course with no live version at all
		would 404 every learner mid-course."""
		fallback = frappe.get_all(
			"Training Course Version",
			filters={"course": self.course, "docstatus": 1, "name": ["!=", self.name]},
			fields=["name", "estimated_minutes"],
			order_by="version_number desc",
			limit=1,
		)
		if not fallback:
			frappe.db.set_value(
				"Training Course", self.course, "current_version", None, update_modified=False
			)
			return
		frappe.db.set_value(
			"Training Course Version", fallback[0].name, "is_current", 1, update_modified=False
		)
		frappe.db.set_value(
			"Training Course",
			self.course,
			{
				"current_version": fallback[0].name,
				"estimated_minutes": cint(fallback[0].estimated_minutes),
			},
			update_modified=False,
		)

	def _refresh_course(self):
		frappe.get_doc("Training Course", self.course).refresh_status()
