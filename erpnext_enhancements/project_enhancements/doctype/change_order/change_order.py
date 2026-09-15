# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A change order — WI-075 sub-phase K.

A first-class record rather than a `Project Contract` revision, and the reason is one field:
**cause**. A revision counter can tell you a contract changed; it can never tell you whether the
customer asked for something, the site turned out different, or we got it wrong. That attribution
is what `docs/KPI_DASHBOARD_DESIGN.md` calls impossible today, and it is the only version of this
number worth having — "how much did the job change" and "how much change did we cause" are
different questions and only the second is a quality signal.

Submit is the lock, exactly as it is on `Project Scope of Work`. After submit the added criteria
are contracted, and the inspection generator starts putting them on inspections for the milestones
they name.

Three refusals
---------------

**An amendment is refused outright.** Frappe's amend path appends ``-1`` to the name, so amending
``PRJ-00580-CO-003`` produces ``PRJ-00580-CO-003-1`` — a second commercial instrument with almost
the same name as the first, in a place where the two would be quoted at each other. A wrong change
order is cancelled and a new one raised.

**Submit is refused without both approvals.** They are the gates; making them advisory would make
submit a formality, and submit is what turns this into contracted scope.

**An added criterion naming no milestone is called out.** Not refused — a criterion may genuinely
be verified by something other than an inspection — but said out loud on every save, because a
change order is the scope most likely to be agreed in a hurry and inspected by nobody.

The judgement lives in :mod:`erpnext_enhancements.quality.change_orders`, which imports no
``frappe``. It is in ``quality/`` rather than beside this file because
``project_enhancements/__init__`` imports ``frappe`` at module scope, which would put it out of
reach of the bench-free test tier — the same reason ``quality/scope_criteria.py`` sits there while
`Project Scope of Work` sits here.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_enhancements.quality import change_orders, stable_keys

#: How many times to retry a colliding name before giving up. One is enough: two people creating
#: a change order on the same project in the same instant is already unusual, three is a bug
#: somewhere else and a loop would hide it.
NAME_RETRIES = 1


class ChangeOrder(Document):
	def autoname(self):
		self.co_number = change_orders.next_number(self._used_numbers())
		self.name = change_orders.co_name(self.project, self.co_number)

	def _used_numbers(self):
		"""Numbers already used **on this project** — including cancelled ones.

		Cancelled change orders keep their number. Reusing it would put two different documents
		behind one number in somebody's email, and a voided change order is exactly the one
		people go back and look for.
		"""
		if not self.project:
			frappe.throw(_("A change order needs a project before it can be numbered."))
		return frappe.get_all(
			"Change Order",
			filters={"project": self.project},
			pluck="co_number",
			limit_page_length=0,
		)

	def before_insert(self):
		"""Retry once on a colliding name.

		Two people creating a change order on the same project at the same moment both compute
		the same next number. The collision surfaces on insert, and it can arrive as either
		exception: a primary-key clash raises ``DuplicateEntryError`` while a unique-index clash
		raises ``UniqueValidationError``. Catching only one of them makes this retry fail open,
		which is how a dedupe quietly stops deduping.
		"""
		self._mint_criterion_keys()

	def validate(self):
		self._refuse_amendment()
		self._mint_criterion_keys()
		self._derive()
		self._warn_unassigned_criteria()

	def before_submit(self):
		reasons = change_orders.blocking_reasons(self)
		if reasons:
			frappe.throw(
				"<ul>" + "".join(f"<li>{frappe.utils.escape_html(r)}</li>" for r in reasons) + "</ul>",
				title=_("Not ready to lock"),
			)

	def on_submit(self):
		self._derive()

	def on_cancel(self):
		self._derive()

	# ------------------------------------------------------------------ helpers

	def _refuse_amendment(self):
		if not self.get("amended_from"):
			return
		frappe.throw(
			_(
				"A change order cannot be amended. Amending would name it {0}-1, which is a second "
				"commercial instrument with almost the same name as the first. Cancel {0} and raise "
				"a new change order instead."
			).format(self.amended_from),
			title=_("Amendment refused"),
		)

	def _mint_criterion_keys(self):
		"""Every added criterion gets its stable key, minted once and never regenerated.

		The same identity the Scope of Work uses, so a criterion added by change order joins the
		inspection, the non-conformance and the corrective action on the same value — and
		inserting a row above it never repoints a closed record at a different standard.
		"""
		rows = self.get("added_criteria") or []
		stable_keys.mint_missing_keys(rows, frappe.generate_hash, "criterion_key")
		for row in rows:
			row.source = "Change Order"
			row.source_document = self.name

		duplicates = stable_keys.duplicate_keys(rows, "criterion_key")
		if duplicates:
			frappe.throw(
				_("Two added criteria share the key {0}. A result recorded against one of them "
				  "would be ambiguous.").format(", ".join(duplicates)),
				title=_("Duplicate criterion key"),
			)

	def _derive(self):
		"""Status and impact type, computed from what the document is.

		Neither is ever typed. A stored status beside ``docstatus``, or an impact type beside the
		sign of the money, is a second copy of a fact that is free to disagree with the first —
		and on a commercial instrument that disagreement is the argument.
		"""
		self.cost_impact_type = change_orders.impact_type(self.cost_impact)
		self.status = change_orders.derive_status(
			self.docstatus,
			pm_approved=self.pm_approved,
			customer_approved=self.customer_approved,
			executed_on=self.executed_on,
		)

	def _warn_unassigned_criteria(self):
		unassigned = change_orders.unassigned_added_criteria(self.get("added_criteria"))
		if not unassigned:
			return
		frappe.msgprint(
			_("These added criteria name no milestone, so no inspection will ever check them:")
			+ "<ul>"
			+ "".join(f"<li>{frappe.utils.escape_html(text)}</li>" for _key, text in unassigned)
			+ "</ul>"
			+ _("That may be right — some things are verified another way — but it is worth "
				"saying out loud on the scope most likely to be agreed in a hurry."),
			title=_("Sold, inspected by nothing"),
			indicator="orange",
		)


def insert_with_retry(doc):
	"""Insert a change order, recomputing its number once if the name is taken.

	Exposed as a function rather than buried in ``before_insert`` because the retry has to
	re-run ``autoname``, and a controller hook cannot rename the document it is already
	inserting.
	"""
	for attempt in range(NAME_RETRIES + 1):
		try:
			doc.insert()
			return doc
		except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
			if attempt >= NAME_RETRIES:
				raise
			frappe.db.rollback()
			doc.name = None
			doc.co_number = None
	return doc
