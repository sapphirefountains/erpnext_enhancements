# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A dated note of a conversation, a warning or a grievance.

**The most tightly held doctype in this app.** `HR Manager` and `System Manager`,
and deliberately not `HR User` — every one of the sixteen staff holds `HR User`,
so granting it here would be the same as granting it to everybody. No export, no
share, no web view, no copy.

Three things shape it.

**"Conversation" is a real entry, not a lesser one.** Most of what belongs in a
personnel file is a conversation somebody wanted a dated note of, and a form that
offers only "written warning" and "final warning" is a form people avoid until
things are already bad — at which point there is no earlier record to show that
anybody tried.

**The summary is written for the person it is about to read back.** That is the
standard, and it is on the field's own description: anything you would not say to
their face does not belong in a record they can be shown in a tribunal. It is also
simply the useful standard — notes written to be defensible are notes that say
nothing.

**Their response is recorded verbatim if they give one.** A file with only one
side of it is worth less, not more, and the absence of a reply is itself
informative.

Deliberately not a workflow. No approval, no acknowledgement gate, no escalation
chain. The record is the point.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, today


class HRCaseRecord(Document):
	def before_insert(self):
		# Stamped once. A dated record whose author can be changed afterwards is not
		# a record -- and this is the doctype most likely to be read out in a room
		# where somebody disputes it.
		self.raised_by = frappe.session.user

	def validate(self):
		self._reject_self_filing()
		self._stamp_acknowledgement()

	def _reject_self_filing(self):
		"""Nobody writes their own warning.

		The two people who hold HR Manager are also employees, so this is reachable
		rather than theoretical -- and a personnel file entry somebody wrote about
		themselves is the one entry an outside reader would discount entirely.
		"""
		if not self.employee:
			return
		subject = frappe.db.get_value("Employee", self.employee, "user_id")
		if subject and subject == (self.raised_by or frappe.session.user):
			frappe.throw(
				_("Somebody else has to file this. A file entry you wrote about yourself is not one."),
				frappe.PermissionError,
			)

	def _stamp_acknowledgement(self):
		if cint(self.employee_acknowledged) and not self.acknowledged_on:
			self.acknowledged_on = today()
		if not cint(self.employee_acknowledged):
			self.acknowledged_on = None
