# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Link each Employee to their reimbursement Supplier — the confident ones only.

Prod carries seven reimbursement Suppliers against sixteen active staff, and they
were created by hand over time, so they do not share a shape:

    Jesse Griffin Reimbursement            <name> Reimbursement
    Danny Rosser Reimbursement             <name> Reimbursement    (nickname!)
    Employee Clegg Mabey Reimbursement     Employee <name> Reimbursement
    Nathan Cox Reimbursement               <name> Reimbursement
    Lisa Symanski Reimbursement            <name> Reimbursement
    Lian Silva Reimbursement               <name> Reimbursement    (partial name!)
    Logan Penrod Employee Reimbursement    <name> Employee Reimbursement

Three shapes, and two that do not contain their Employee's name as stored:
`Danny Rosser` is Employee **Daniel Rosser**, and `Lian Silva` is Employee **Lian
Jentz Da Silva**.

**So this patch deliberately does not try to be clever.** It strips the
``Employee`` and ``Reimbursement`` words, compares what is left against
``employee_name`` exactly, and links only where exactly one Employee matches. The
two nicknames are left for a human, and so is anybody whose Supplier does not
exist yet.

The temptation is a fuzzy match, and the reason to refuse is not tidiness. There
are **1,180 Suppliers** on this site. A near-match that lands on a real vendor
does not fail — it silently makes that vendor the destination for somebody's
out-of-pocket receipts, and the resulting Purchase Invoice looks entirely
ordinary right up until it is paid to the wrong company. A gap a human fills in is
cheap; a wrong link is a payment.

Idempotent: writes only where the field is empty, so a human's correction is never
overwritten on a later deploy.
"""

import re

import frappe

FIELD = "custom_reimbursement_supplier"

#: Words that decorate the Supplier name rather than identify the person.
_NOISE = re.compile(r"\b(employee|reimbursements?)\b", re.I)


def execute():
	if not frappe.db.exists("DocType", "Supplier"):
		return
	try:
		if not frappe.db.has_column("Employee", FIELD):
			# The fixture Custom Field has not synced yet. `sync_fixtures()` runs in
			# post_schema_updates(), AFTER the post-model-sync patches, so on the
			# migrate that introduces the field this is the normal path rather than
			# an error -- the after_migrate hook below re-runs and does the work.
			return
	except Exception:
		# has_column takes a DOCTYPE and RAISES on an unknown table rather than
		# returning False, so this except is load-bearing on a fresh site.
		return
	link_reimbursement_suppliers()


def link_reimbursement_suppliers():
	"""The idempotent body, also registered as an ``after_migrate`` hook.

	Registered there as well as here for the ordering reason above: on the deploy
	that introduces the Custom Field, the patch runs before the column exists and
	returns having done nothing — and a patch that returns having done nothing
	still records itself in ``tabPatch Log`` and never runs again. That exact trap
	has bitten this app three times. The hook runs after fixtures and is safe to
	repeat, so the work lands on this deploy and self-heals on every later one.
	"""
	try:
		if not frappe.db.has_column("Employee", FIELD):
			return 0
	except Exception:
		return 0

	suppliers = frappe.get_all(
		"Supplier",
		filters={"supplier_name": ["like", "%eimbursement%"], "disabled": 0},
		fields=["name", "supplier_name"],
	)
	if not suppliers:
		return 0

	employees = frappe.get_all(
		"Employee",
		filters={"status": "Active"},
		fields=["name", "employee_name", FIELD],
	)
	by_name = {}
	for row in employees:
		key = _normalise(row.employee_name)
		if key:
			by_name.setdefault(key, []).append(row)

	# Grouped by key FIRST, so ambiguity is refused from both directions. Two
	# Suppliers that normalise to the same key -- "Clegg Mabey Reimbursement" and
	# "Employee Clegg Mabey Reimbursement", say, which is exactly the kind of
	# duplicate a hand-maintained vendor list accumulates -- would otherwise both
	# pass the per-Supplier check and write in turn. The second write does not even
	# hit the never-overwrite guard, because `by_name` holds the row as it was READ
	# and its FIELD is still empty in memory. Last one wins, silently, and which one
	# is last depends on row order. Found by the adversarial review of this change.
	by_key = {}
	for supplier in suppliers:
		key = _normalise(_NOISE.sub(" ", supplier.supplier_name or ""))
		if key:
			by_key.setdefault(key, []).append(supplier)

	linked = 0
	for key, matched_suppliers in by_key.items():
		if len(matched_suppliers) != 1:
			# Two vendor rows for one person is the same ambiguity as two people for
			# one vendor row, seen from the other end.
			continue
		candidates = by_name.get(key) or []
		if len(candidates) != 1:
			# Zero means a nickname or a partial name -- a human's call. More than one
			# means two employees share a name, which is precisely when guessing is
			# most expensive.
			continue
		employee = candidates[0]
		if employee.get(FIELD):
			continue
		frappe.db.set_value(
			"Employee", employee.name, FIELD, matched_suppliers[0].name, update_modified=False
		)
		# Stamped in memory too, so a later key that resolves to the same Employee
		# sees it -- `by_name` rows are the ones read at the top of this function.
		employee[FIELD] = matched_suppliers[0].name
		linked += 1

	if linked:
		print(f"[erpnext_enhancements] linked {linked} employee(s) to a reimbursement Supplier")
	return linked


def _normalise(value):
	"""Casefolded, punctuation-stripped, single-spaced. Nothing fuzzier."""
	if not value:
		return ""
	return " ".join(re.sub(r"[^\w\s]", " ", str(value)).split()).casefold()
