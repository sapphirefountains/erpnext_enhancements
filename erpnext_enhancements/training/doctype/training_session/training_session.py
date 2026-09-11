# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Session — recording a training that already happened.

**The honest answer to "make building a training simple enough that anyone can
do it."** Most training at this company is not a course: it is a ten-minute
tailgate talk before a basin drain, a pump manufacturer's rep walking three people
through a filter, a ride-along. It has already happened, in the yard, and what is
needed is a record of it — not a WYSIWYG.

Everything else in this module assumes somebody sat down and worked through
content. Nothing anywhere could say "these five people were told this, on this
day, by this person, and here is the sheet they signed". `Training Live Class`
(WI-071 Phase B) comes closest and has no attendance or completion path out of
it at all.

So: a free-text title, a date, who led it, who was there, and a photo of the
roster. **No course required.** Making somebody author a course before they can
record a safety talk is exactly why safety talks never get recorded.

Linking a course is optional and it is what turns attendance into
**certification**: on submit, every attendee gets a real `Training Completion`
against the course's published version. That is legitimate — `attempt` is not a
required field on a completion, and somebody who was taught the material in person
by a competent person has met the course's substance. What makes it auditable is
the provenance: the completion names the session, and the session names who led
it, what was covered, and carries the signed sheet.

Submittable, because the roster is evidence. Cancelling withdraws the completions
it minted — a session that did not happen must not leave certifications behind.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate, now_datetime, today


class TrainingSession(Document):
	def validate(self):
		self._resolve_attendee_users()
		self._reject_future_dates()
		self._require_someone_present()
		self._resolve_course_version()

	def before_submit(self):
		self._require_published_course()

	def on_submit(self):
		self._issue_completions()
		self._record_achievements()

	def on_cancel(self):
		self._withdraw_completions()

	# ------------------------------------------------------------------ helpers

	def _resolve_attendee_users(self):
		for row in self.attendees or []:
			row.user = frappe.db.get_value("Employee", row.employee, "user_id") or None

	def _reject_future_dates(self):
		"""This records what happened, not what is planned.

		A future-dated session is somebody using the wrong doctype — `Training Live
		Class` is the one for something scheduled — and letting it through would
		mint completions for a talk nobody has given yet.
		"""
		if self.held_on and getdate(self.held_on) > getdate(today()):
			frappe.throw(
				_("This records a session that has already happened. For something coming up, "
				  "use a Training Live Class.")
			)

	def _require_someone_present(self):
		if not any(cint(row.attended) for row in self.attendees or []):
			frappe.throw(_("Nobody is marked as having been there."))

	def _resolve_course_version(self):
		"""Frozen at the version that was live when the session was recorded.

		A completion has to name the exact content somebody was taught and the live
		version moves; resolving this at read time would eventually claim people were
		taught a version that did not exist on the day.
		"""
		if not self.course:
			self.course_version = None
			return
		if not self.course_version:
			self.course_version = frappe.db.get_value("Training Course", self.course, "current_version")

	def _require_published_course(self):
		if not self.course:
			return
		if not self.course_version:
			frappe.throw(
				_("{0} has no published version, so there is nothing to certify anybody against. "
				  "Publish it first, or leave the course blank and record the session on its own.")
				.format(self.course)
			)

	def _issue_completions(self):
		"""One completion per attendee, for a course-linked session.

		Idempotent per attendee: a re-submit after an amend must not mint a second
		certification for the same person and the same session.
		"""
		if not self.course or not self.course_version:
			return

		version = frappe.db.get_value(
			"Training Course Version", self.course_version, ["version_number", "content_hash"], as_dict=True
		) or frappe._dict()
		title = frappe.db.get_value("Training Course", self.course, "course_title")

		for row in self.attendees or []:
			if not cint(row.attended) or not row.user or row.completion:
				continue
			try:
				completion = frappe.get_doc(
					{
						"doctype": "Training Completion",
						"user": row.user,
						"employee": row.employee,
						"course": self.course,
						"course_title_snapshot": title,
						"course_version": self.course_version,
						"version_number": cint(version.get("version_number")),
						"content_hash": version.get("content_hash") or "",
						"status": "Valid",
						"completed_on": self.held_on or now_datetime(),
					}
				)
				if completion.meta.has_field("source_session"):
					# Provenance, and the reason this is auditable rather than a
					# back door: the completion names the session, and the session
					# names who led it, what was covered and the signed sheet.
					completion.source_session = self.name
				completion.insert(ignore_permissions=True)
				completion.submit()
				row.db_set("completion", completion.name, update_modified=False)
			except Exception:
				# One attendee must not cost the other four their record.
				frappe.log_error(
					f"Could not record a completion for {row.user} from session {self.name}\n"
					f"{frappe.get_traceback()}",
					"Training session",
				)

	def _record_achievements(self):
		"""On the feed as itself, whether or not a course was linked.

		A tailgate talk is worth showing even when it certifies nothing — it is the
		commonest kind of training here and the kind that was previously invisible.
		"""
		try:
			from erpnext_enhancements.training import social

			for row in self.attendees or []:
				if cint(row.attended) and row.user:
					social.record(
						row.user,
						"Course Completed" if self.course else "Signed Off",
						self.session_title,
						occurred_on=self.held_on,
					)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Training session feed ({self.name})")

	def _withdraw_completions(self):
		"""A session that did not happen must not leave certifications behind."""
		for row in self.attendees or []:
			if not row.completion:
				continue
			try:
				doc = frappe.get_doc("Training Completion", row.completion)
				if doc.docstatus == 1:
					doc.cancel()
			except Exception:
				frappe.log_error(
					f"Could not withdraw completion {row.completion} for session {self.name}\n"
					f"{frappe.get_traceback()}",
					"Training session",
				)
