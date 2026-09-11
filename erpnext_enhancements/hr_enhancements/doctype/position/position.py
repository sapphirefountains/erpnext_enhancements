# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Position — the company's ladder, and the thing sign-off authority is read from.

A tree, because that is what a company chart is: groups are **job families**
(Technician, Designer, Office) and the leaves under them are the rungs (Junior,
Senior, Master). ``tier`` is what actually decides authority — higher outranks
lower, and only ever **inside the same family**, so a Senior Designer has no
standing over a Junior Technician however the integers compare.

Why this is not ``Designation``
-------------------------------
``Designation`` is ERPNext core, flat, and has exactly two fields. It is already
consumed by this app's auto-assignment rule engine and by payroll reporting, so
overloading it with rank would mean every future reader has to know the label
means two things at once. It stays the HR job title. **Changing somebody's
Designation changes no authority** — say that out loud to anyone who asks.

Why this is not a field on ``Employee`` either
----------------------------------------------
Nik's rule is that a *Senior Technician* may sign off a *Junior Technician* — the
authority belongs to the position, not to the person holding it. A ``tier`` on
Employee would also give the org-chart half of the ask nothing.

Why it is not ``Employee.reports_to``
-------------------------------------
Because on this site the reporting tree says the opposite of the ladder. Jesse
Griffin is the one Senior Technician and has **zero direct reports**: all four
Junior Technicians report to the Project Manager, and so does Jesse. Routing
sign-off through ``reports_to`` therefore cannot reach the one person who has
actually watched them work. Both trees are real and they answer different
questions — ``reports_to`` is who runs your week, ``Position`` is who is
qualified to say you can do the job.

Naming
------
Checked before claiming it: no ``Position`` DocType exists in frappe
``version-16``, in erpnext ``version-16``, or on this site. ``hrms`` is not
installed here (that is why this module exists at all) and its nearest names are
``Employee Grade`` and ``Job Opening``. The module is called ``HR Enhancements``
rather than ``HR`` precisely because hrms ships a module by the latter name.
"""

import frappe
from frappe import _
from frappe.utils import cint
from frappe.utils.nestedset import NestedSet


class Position(NestedSet):
	nsm_parent_field = "parent_position"

	def validate(self):
		self._reject_self_parent()
		self._derive_job_family()
		self._validate_tier()

	def on_update(self):
		# NestedSet keeps lft/rgt; this additionally repairs descendants whose
		# family changed because *this* node moved. Without it, moving a rung
		# between ladders leaves every position under it claiming the old family,
		# and the authority predicate silently keeps using it.
		super().on_update()
		self._recompute_descendant_families()

	# ------------------------------------------------------------------ helpers

	def _reject_self_parent(self):
		if self.parent_position and self.parent_position == self.name:
			frappe.throw(_("A position cannot be its own parent."))

	def _derive_job_family(self):
		"""The nearest ancestor-or-self marked ``is_group``.

		Read-only and always recomputed, never accepted from the form: it is the
		left-hand side of "same ladder" in the sign-off predicate, and a stale or
		hand-edited value there would hand somebody authority over a family they
		are not in.

		A group is its own family, which is what makes ``Technician`` and every
		rung beneath it compare equal.
		"""
		if cint(self.is_group):
			self.job_family = self.name
			return

		seen = set()
		parent = self.parent_position
		while parent and parent not in seen:
			seen.add(parent)
			row = frappe.db.get_value(
				"Position", parent, ["name", "is_group", "parent_position"], as_dict=True
			)
			if not row:
				break
			if cint(row.is_group):
				self.job_family = row.name
				return
			parent = row.parent_position

		# A rung hung directly off nothing is its own family. Not an error — it is
		# what a one-rung role like "Chief Executive Officer" looks like.
		self.job_family = self.name

	def _validate_tier(self):
		if cint(self.is_group):
			# A family is not a rung. Leaving a tier on it would make the group
			# itself comparable, and "Technician outranks Junior Technician" is not
			# a sentence anybody means.
			self.tier = 0
			return
		if cint(self.tier) < 1:
			frappe.throw(
				_("Give {0} a tier of 1 or more. Tier is what decides who may sign off whom — "
				  "Junior 1, Senior 2, Master 3 — and a position without one can neither sign "
				  "anybody off nor be signed off by rank.").format(self.position_name or self.name)
			)

	def _recompute_descendant_families(self):
		descendants = frappe.get_all(
			"Position",
			filters={"lft": [">", cint(self.lft)], "rgt": ["<", cint(self.rgt)]},
			pluck="name",
			order_by="lft asc",
		)
		for name in descendants:
			doc = frappe.get_doc("Position", name)
			before = doc.job_family
			doc._derive_job_family()
			if doc.job_family != before:
				# db_set rather than save: nothing else about the descendant changed,
				# and a full save would re-enter NestedSet's own update for every row
				# under a moved subtree.
				doc.db_set("job_family", doc.job_family, update_modified=False)


@frappe.whitelist()
def get_position_children(doctype=None, parent=None, is_root=False, **kwargs):
	"""Tree nodes carrying the tier, which core's ``get_children`` does not.

	``frappe.desk.treeview.get_children`` selects exactly three columns —
	``name as value``, the title field, and ``is_group as expandable``
	(frappe ``origin/version-16:frappe/desk/treeview.py:58-70``). So a tree page
	pointed at it receives no ``tier``, and an ``onrender`` that reads
	``node.data.tier`` draws nothing at all. The tier is the entire reason this
	tree exists, and it was invisible on it until the branch review caught it.

	Same shape as core's reply plus the two extra columns, so the standard
	treeview handles it unchanged.
	"""
	parent = parent or ""
	if parent == doctype:
		# The tree's root node is labelled with the doctype name; core resolves that
		# to "no parent" and so must this, or the first level comes back empty.
		parent = ""
	return frappe.get_all(
		"Position",
		filters={"parent_position": parent or ["in", ("", None)]},
		fields=[
			"name as value",
			"position_name as title",
			"is_group as expandable",
			"tier",
			"tier_label",
			"is_active",
		],
		order_by="tier asc, position_name asc",
	)


def outranks(position, other):
	"""Whether *position* is strictly senior to *other* on the same ladder.

	**The one expression of the rule**, so the endpoint, the controller's
	``before_submit``, the visibility filters and the sign-off queue cannot come
	to disagree about it. Three clauses, and each is load-bearing:

	* **same ``job_family``** — a Senior Designer has no standing over a Junior
	  Technician, whatever the integers say;
	* **strictly greater ``tier``** — never a same-tier peer, or two Junior
	  Technicians sign each other's basin course and the gate means nothing;
	* **both positions active** — a retired rung outranks nobody.

	Missing either position returns ``False``: an unknown ladder is not authority,
	and this predicate fails closed everywhere it is read.
	"""
	if not position or not other or position == other:
		return False
	fields = ["job_family", "tier", "is_active", "is_group"]
	mine = frappe.db.get_value("Position", position, fields, as_dict=True)
	theirs = frappe.db.get_value("Position", other, fields, as_dict=True)
	if not mine or not theirs:
		return False
	if not (cint(mine.is_active) and cint(theirs.is_active)):
		return False
	if cint(mine.is_group) or cint(theirs.is_group):
		return False
	if not mine.job_family or mine.job_family != theirs.job_family:
		return False
	return cint(mine.tier) > cint(theirs.tier)


def positions_outranked_by(position):
	"""Every position *position* is senior to. The IN-list form of :func:`outranks`.

	Used by the row-level filters, which need a list rather than a predicate.
	Computed in Python off one indexed read rather than as SQL over ``lft``/``rgt``
	— the ladder is a dozen rows on this site and a correlated tree query here
	would be harder to read than the rule it implements.
	"""
	if not position:
		return []
	mine = frappe.db.get_value(
		"Position", position, ["job_family", "tier", "is_active", "is_group"], as_dict=True
	)
	if not mine or not mine.job_family or not cint(mine.is_active) or cint(mine.is_group):
		return []
	return frappe.get_all(
		"Position",
		filters={
			"job_family": mine.job_family,
			"tier": ["<", cint(mine.tier)],
			"is_active": 1,
			"is_group": 0,
		},
		pluck="name",
	)
