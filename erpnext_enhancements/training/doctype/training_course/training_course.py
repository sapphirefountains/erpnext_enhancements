# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Course — the stable identity of a course.

Holds **no content**. Title, slug, audience, policy and completion gates live
here; every lesson, block and question lives on a ``Training Course Version``.
That split is what makes "fix a typo without invalidating fifteen completions"
possible: the course is the thing assignments and completions point at, and the
version is the thing that gets frozen.

``status`` is derived from the versions, never typed — see :meth:`refresh_status`.
An author editing this form cannot accidentally publish anything.

The slug is editable while the course has never been published and frozen after,
because it appears in assignment emails and in any QR sticker somebody put on a
fountain. A course that renames its own URL breaks every one of those silently.
"""

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

# Applies To -> the doctype its value links to.
#
# Employee Grade and Employment Type are hrms-owned and **are not installed
# here** (verified 2026-08-01), even though ``Employee.grade`` and
# ``Employee.employment_type`` exist as columns — ERPNext ships the fields as
# Links to doctypes that arrive with hrms. So the column guard in the rule engine
# is not enough on its own: a rule targeting one of these would produce a Dynamic
# Link pointing at a missing doctype, which fails at read time rather than at the
# point somebody made the mistake. ``_validate_rules`` therefore refuses the row
# up front, with a message that says why. The options stay listed so the module
# works unchanged if hrms is ever installed.
RULE_TARGET_DOCTYPES = {
	"All Employees": "",
	"Department": "Department",
	"Designation": "Designation",
	"Role Profile": "Role Profile",
	"Role": "Role",
	"Employee Grade": "Employee Grade",
	"Employment Type": "Employment Type",
}


class TrainingCourse(Document):
	def validate(self):
		self._derive_slug()
		self._freeze_slug_once_published()
		self._stamp_author()
		self._validate_audience()
		self._validate_rules()
		self._validate_gates()
		self._validate_signoff()

	# ------------------------------------------------------------------ helpers

	def _derive_slug(self):
		if (self.slug or "").strip():
			self.slug = _slugify(self.slug)
		else:
			self.slug = _slugify(self.course_title)
		if not self.slug:
			frappe.throw(_("The course title needs at least one letter or digit so a web address can be made from it."))

	def _freeze_slug_once_published(self):
		"""Once anything has been published under this slug, links to it exist in
		inboxes we cannot reach. Changing it turns them all into 404s."""
		if self.is_new() or not self.published_on:
			return
		before = self.get_doc_before_save()
		if before and before.slug and before.slug != self.slug:
			frappe.throw(
				_("This course has already been published as /training/{0}, so its address is fixed. "
				  "Assignment emails and any printed links point at it.").format(before.slug)
			)

	def _stamp_author(self):
		if not self.author:
			self.author = frappe.session.user

	def _validate_audience(self):
		if self.audience == "Internal Staff":
			# Customer scoping rows left behind from an earlier setting would
			# silently widen the audience if it were ever switched back.
			self.customer_scope = "All Customers"
			self.visible_to_customers = []
		elif self.audience == "Customers":
			self.visible_to_roles = []

		if self.audience != "Internal Staff" and self.customer_scope == "Selected Customers":
			if not self.visible_to_customers:
				frappe.throw(_("Pick at least one customer, or set the scope back to all customers."))

		seen = set()
		for row in self.visible_to_customers or []:
			if row.customer in seen:
				frappe.throw(_("{0} is listed twice.").format(row.customer))
			seen.add(row.customer)

		seen = set()
		for row in self.visible_to_roles or []:
			if row.role in seen:
				frappe.throw(_("{0} is listed twice.").format(row.role))
			seen.add(row.role)

	def _validate_rules(self):
		"""Stamp each rule's target doctype and reject rules that resolve to nobody.

		**This stamp is a backstop, not the primary mechanism, and the difference
		matters.** ``applies_to_value`` is a Dynamic Link resolved through
		``applies_to_doctype``, and Frappe validates links in ``Document.insert``
		at line 727 — before ``before_insert``, before naming, and long before
		``validate`` ever runs. So there is no server hook early enough to derive
		the field on an insert; a row arriving with ``applies_to_value`` set and
		``applies_to_doctype`` empty is rejected by the framework before this code
		is reached, with the unhelpful "Applies To DocType must be set first".

		The desk path is handled client-side in ``public/js/training/training_course.js``,
		which stamps the field the moment the author picks a target. What this
		method adds is (a) correctness on every subsequent save, and (b) a message
		that actually explains the problem when a document is built in Python or
		over the API without the field.
		"""
		if self.weight != "Required":
			# Rules on an optional course would never fire; keeping them would be a
			# quiet trap when someone later flips the weight.
			if self.assign_rules and self.auto_assign:
				frappe.throw(
					_("Only Required courses can be assigned automatically. Change the weight to Required, "
					  "or turn off Assign Automatically.")
				)
			return

		for row in self.assign_rules or []:
			target = RULE_TARGET_DOCTYPES.get(row.applies_to)
			if target is None:
				frappe.throw(_("{0} is not a rule target this app understands.").format(row.applies_to))
			row.applies_to_doctype = target
			if not target:
				row.applies_to_value = None
				continue
			if not frappe.db.exists("DocType", target):
				frappe.throw(
					_("Row {0}: {1} is not available on this site — it comes with the HR module, which "
					  "is not installed. Target the rule by Department, Designation, Role or Role "
					  "Profile instead.").format(row.idx, _(target))
				)
			if not row.applies_to_value:
				frappe.throw(_("Row {0}: pick which {1} the rule applies to.").format(row.idx, _(target)))
			if not frappe.db.exists(target, row.applies_to_value):
				frappe.throw(
					_("Row {0}: there is no {1} called {2}.").format(row.idx, _(target), row.applies_to_value)
				)

	def _validate_gates(self):
		if not 0 < cint(self.passing_score) <= 100:
			frappe.throw(_("Passing score must be between 1 and 100."))
		if not 0 <= cint(self.min_video_coverage) <= 100:
			frappe.throw(_("Minimum watched must be between 0 and 100."))
		if cint(self.max_attempts) < 0:
			frappe.throw(_("Quiz attempts cannot be negative. Use 0 for unlimited."))
		if cint(self.recertify_months) < 0:
			frappe.throw(_("Recertification interval cannot be negative. Use 0 for never."))

	def _validate_signoff(self):
		if not cint(self.require_supervisor_signoff):
			return
		if self.signoff_supervisor_source == "Named Supervisor" and not self.signoff_supervisor:
			frappe.throw(_("Name the supervisor who signs this off."))
		if not (self.signoff_instructions or "").strip():
			frappe.throw(
				_("Say what the supervisor is verifying. A sign-off with no stated criterion is a signature "
				  "on nothing.")
			)

	# ------------------------------------------------------- status, from versions

	def refresh_status(self):
		"""Recompute ``status`` from the versions that exist, and persist it.

		Called by the version controller on submit/cancel and by the publish
		endpoint. Uses ``db_set`` so it never re-runs this doc's validation from
		inside another document's lifecycle.
		"""
		if self.status == "Retired":
			return

		published = frappe.db.exists(
			"Training Course Version", {"course": self.name, "docstatus": 1, "is_current": 1}
		)
		if published:
			status = "Published"
		elif frappe.db.exists("Training Course Version", {"course": self.name, "submitted_for_review": 1, "docstatus": 0}):
			status = "In Review"
		else:
			status = "Draft"

		if status != self.status:
			self.db_set("status", status, update_modified=False)


def _slugify(text):
	"""Lowercase, alphanumerics and single hyphens. Mirrors what a reader would
	guess from the title, which is the point — the slug is a human-facing URL."""
	slug = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower())
	return slug.strip("-")
