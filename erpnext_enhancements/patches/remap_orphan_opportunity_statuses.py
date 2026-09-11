# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Retire the three Opportunity statuses that are not options any more, to `Lost`.

148 opportunities carry a `status` absent from the field's own Select list — `Closed`
(144), `Prospecting` (3) and `Value Proposition` (1). Every one was inserted by the Zoho
CRM import on 2025-07-14 (they carry `custom_zoho_crm_opportunity_id`).

They are not untidy, they are **frozen**. `_validate_selects` (base_document.py:1101-1129 on
``origin/version-16``) hard-throws on a Select value outside its options, and neither
``ignore_validate`` nor ``ignore_mandatory`` nor ``ignore_permissions`` bypasses it — only
``frappe.flags.in_import`` and the no-validation write paths do. So every ordinary save of
these rows has been refused for over a year. It is not theoretical: Error Log
2026-06-19 03:03:55 is a QuickBooks customer import dying on exactly this throw. In the Desk
the Status field renders blank (jQuery sets selectedIndex -1 when no option matches) and any
edit comes back as a modal listing the seven valid options.

What `Closed` actually was
-------------------------------------------------------------------------------
`tabVersion` settles it, and it is not what the option list suggests. **Every** migrated
opportunity landed as `Closed`; customers and their invoices had been loaded three days
earlier. Then two "Update Existing Records" spreadsheet re-imports on 2025-08-16 and
2025-08-20 (596 and 599 rows) promoted rows out of `Closed` into `Closed Won` and `Lost`.
These 144 are the ones those passes did not promote — 31 of a 32-row sample have no Version
row at all.

That also disposes of the one signal that looked like evidence `Closed` meant won: `Closed`
tracks `Closed Won` on whether the party was ever invoiced (62.5% vs 60.0%, against 1.0% for
`Lost`). The correlation is real and circular — everything started as `Closed`, and the
promoted subset was chosen a month *after* the invoice data was already in the system, so
the label is downstream of the revenue knowledge rather than independent of it.

Nik ran those passes and has ruled that what was left behind was lost. `Prospecting` and
`Value Proposition` — Zoho stage names that were never valid in ERPNext either, on four
untouched rows with no amount — go the same way, so there is one rule rather than two.

Why raw SQL, and why `modified` is untouched
-------------------------------------------------------------------------------
Both are load-bearing, not style:

* ``kpi_dashboards/snapshots.py`` keys the lost leg of ``win_rate_90`` on ``modified``.
  These rows' ``modified`` is 13 months stale, so they contribute nothing today. Bumping it
  drags all 144 into the 90-day window and takes the headline win rate from **36.9% to
  16.1%** — a pure artefact nobody would be able to reconstruct later. `frappe.db.set_value`
  bumps `modified` by default and `doc.save()` always does; a bare UPDATE that does not name
  the column leaves it alone, because Frappe's `modified` is a plain datetime managed in
  Python with no ON UPDATE clause.
* ``doc.save()`` additionally fires ~14 Opportunity handlers per row. `validate_close_reason`
  would throw on all 148 (it is a *transition*, and none has a Lost Reason), which aborts
  `bench migrate` — and on this repo `bench migrate` is the deploy.

The audit trail that raw SQL would otherwise destroy is written into `order_lost_reason`,
which is blank on all 148 and whose `depends_on` is ``eval:doc.status==="Lost"`` — so it
becomes visible on the form at exactly the moment these rows become Lost. Without it the
original value would survive nowhere: a raw UPDATE writes no `tabVersion` row.

What this does NOT break
-------------------------------------------------------------------------------
Once the rows are `Lost` in the database they are fully editable again.
`validate_close_reason` is gated on the transition (`previous.status == doc.status` returns
early), which is why 216 of the 313 opportunities already sitting on `Lost` have no Lost
Reason and are still saveable. They also reappear on the Opportunity Kanban board, whose
columns are the Select options and which has been hiding all 148 cards ($877,388).

Safe twice: the UPDATE matches nothing on a second run, and each chart rewrite is guarded on
finding the exact filter it expects.
"""

import json

import frappe

#: Off-vocabulary status -> what it becomes. All three go to `Lost`; see the module
#: docstring for the evidence and for who made the call.
RETIRED = {
	"Closed": "Lost",
	"Prospecting": "Lost",
	"Value Proposition": "Lost",
}

#: Written into `order_lost_reason` only where it is blank. This is the ONLY record that
#: survives — a raw UPDATE leaves no `tabVersion` row, and that is deliberate (see above).
NOTE = (
	"Marked Lost by the v1.402.0 status cleanup. This opportunity carried status "
	'"{old}", which stopped being an option on this field after the July 2025 Zoho '
	"import; the August 2025 re-import passes promoted the rest of that batch to Closed "
	"Won or Lost and did not promote this one. Until now the record could not be saved "
	"at all."
)

#: Dashboard Charts that filter Opportunity on a status no row carries, or on the wrong
#: one. `name -> (expected current filters_json, replacement)`. Guarded on the exact
#: current value so a hand-edited chart is skipped rather than clobbered.
#:
#: "Opportunities Won" is the reason these ship WITH the data change rather than after it:
#: it is labelled Won and plots `status = "Closed"`, i.e. exactly the 144 rows being
#: retired. Remapping without repointing it turns a wrong chart into an empty one.
CHART_FIXES = {
	"Opportunities Won": (
		[["Opportunity", "status", "=", "Closed", False]],
		[["Opportunity", "status", "=", "Closed Won", False]],
	),
	"Won Opportunities": (
		[["Opportunity", "status", "=", "Converted"]],
		[["Opportunity", "status", "=", "Closed Won"]],
	),
	"Territory Wise Sales": (
		[["Opportunity", "status", "=", "Converted"]],
		[["Opportunity", "status", "=", "Closed Won"]],
	),
	# A literal null inside the IN list. Harmless in SQL, but it is junk that survives
	# every re-save, and this chart starts counting the retired rows once they are Lost.
	"Opportunities Won vs. Lost": (
		[["Opportunity", "status", "in", ["Closed Won", "Lost", None], False]],
		[["Opportunity", "status", "in", ["Closed Won", "Lost"], False]],
	),
}


def execute():
	_retire_statuses()
	_repoint_charts()
	frappe.clear_cache()


def _retire_statuses():
	for old, new in RETIRED.items():
		before = frappe.db.count("Opportunity", {"status": old})
		if not before:
			continue  # already run, or this site never had the value
		frappe.db.sql(
			"""
			update `tabOpportunity`
			set status = %(new)s,
				order_lost_reason = case
					when ifnull(order_lost_reason, '') = '' then %(note)s
					else order_lost_reason
				end
			where status = %(old)s
			""",
			{"new": new, "old": old, "note": NOTE.format(old=old)},
		)
		# `modified` is deliberately absent from the SET list. See the module docstring:
		# naming it here would halve the reported 90-day win rate.
		left = frappe.db.count("Opportunity", {"status": old})
		print(f"  Opportunity status {old!r} -> {new!r}: {before - left} rows")
		if left:
			frappe.log_error(
				f"{left} Opportunity rows still carry status {old!r} after the remap",
				"Opportunity status cleanup",
			)


def _repoint_charts():
	for name, (expected, replacement) in CHART_FIXES.items():
		if not frappe.db.exists("Dashboard Chart", name):
			continue
		current = frappe.db.get_value("Dashboard Chart", name, "filters_json")
		try:
			parsed = json.loads(current or "[]")
		except ValueError:
			parsed = None
		if parsed != expected:
			# Already fixed, or somebody has edited it since. Either way leave it be --
			# a dashboard somebody tuned by hand outranks this tidy-up.
			continue
		frappe.db.set_value(
			"Dashboard Chart",
			name,
			"filters_json",
			json.dumps(replacement),
			update_modified=False,
		)
