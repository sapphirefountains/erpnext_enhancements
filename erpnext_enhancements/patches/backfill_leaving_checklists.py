# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Raise the offboarding checklist for people who left before there was one.

Five Employees on this site have `status != Active`. One already has a Leaving checklist;
**four do not**, because they left before `hr_enhancements/onboarding.py` existed:

    Josh Farris       relieved 2025-09-23
    Laura Brimley     relieved 2025-12-01
    Jacob Shefchik    relieved 2025-12-04
    Angie Larsen      relieved 2026-02-09

--------------------------------------------------------------------------------------
Leavers, not joiners — and that was the decision, not an omission
--------------------------------------------------------------------------------------

The obvious reading of "backfill onboarding" is the **joining** side: raise a checklist for
each of the fifteen active employees. That was considered and rejected, and Nik confirmed the
call on 2026-09-12.

A joining checklist raised today for somebody hired in 2004 is a list of eight things that
were done two decades ago and can never be ticked from evidence. `ensure_checklist` already
refuses the obvious half of this — it returns None for a JOINING checklist on a non-Active
employee, because *"somebody being backfilled after they left does not need a first week"* —
but nothing stops it for an active one. Fifteen employees times eight items is roughly **120
permanently-red rows** that nobody can honestly close, on the exact dashboards this module
built to make outstanding work visible. A checklist nobody can complete teaches people to
ignore checklists.

The leaving side is the opposite: it is **generated from what the person is actually holding
right now**.

--------------------------------------------------------------------------------------
What a backfilled leaving checklist actually is
--------------------------------------------------------------------------------------

Not a reconstruction of what the list would have said on their last day. `leaving_items`
derives its rows at creation time — devices, assets with them as custodian, credentials,
open work assigned to their user, direct reports, vehicles — so a checklist raised today
lists **what is still outstanding today**.

That is the more useful artefact, and it is the whole reason this is worth doing. If a laptop
issued to somebody who left in September 2025 is still in their name a year later, this is the
thing that says so. The fixed items will read as stale; the derived ones are the finding.

Anything already returned simply produces no derived row, so a clean exit yields a short
checklist — which is itself the correct answer.

--------------------------------------------------------------------------------------
Mechanics
--------------------------------------------------------------------------------------

Goes through `onboarding.ensure_checklist`, which is idempotent on ``(employee, kind)`` — the
reason the DocField `unique` came off `employee` when leaving was added. Its docstring names
a patch as one of the three callers, so this is the sanctioned seam rather than a second
implementation.

`on_employee_insert` returns early under `frappe.flags.in_patch`; that guard is on the
JOINING hook and does not apply here, because this calls `ensure_checklist` directly.

`starts_on` comes from `relieving_date`, so the dates on these four are their real last days
rather than today. Wrapped per employee — a patch that raises aborts `bench migrate`, which on
this repo is the deploy, and failing to raise one checklist is not worth a failed deploy.
"""

import frappe


def execute():
	if not frappe.db.exists("DocType", "Onboarding Checklist"):
		return
	if not frappe.db.exists("DocType", "Employee"):
		return

	try:
		from erpnext_enhancements.hr_enhancements.onboarding import LEAVING, ensure_checklist
	except Exception:
		return

	# Anybody who has left. Not filtered to a hard-coded list of four: the four are what
	# this site has today, and a site restored from an older backup, or one that loses
	# somebody between this being written and deployed, should get the same treatment.
	leavers = frappe.get_all(
		"Employee",
		filters={"status": ("!=", "Active")},
		pluck="name",
		order_by="relieving_date asc",
	)

	raised = []
	for employee in leavers:
		# Checked HERE rather than from the return value. `ensure_checklist` hands back the
		# EXISTING name when there already is one, so a count taken from its result would
		# report every leaver as newly raised and be indistinguishable from a real run --
		# the same shape as a backfill that matches nothing and logs itself a success.
		if frappe.db.exists("Onboarding Checklist", {"employee": employee, "kind": LEAVING}):
			continue
		try:
			if ensure_checklist(employee, LEAVING):
				raised.append(employee)
		except Exception:
			frappe.log_error(
				title="backfill_leaving_checklists",
				message="Could not raise a leaving checklist for %s" % employee,
			)

	print(
		"backfill_leaving_checklists: raised %d of %d leaver(s)"
		% (len(raised), len(leavers))
	)
