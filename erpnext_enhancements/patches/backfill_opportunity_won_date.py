"""Recover the close dates that are genuinely recoverable, and only those (v1.290.0).

``stamp_won_date`` fills a blank ``custom_date_closed_won`` on any Closed-Won save, so
every deal won since it shipped carries a date. Nothing filled it in for the deals that
were already won: 221 of the 290 Closed-Won opportunities have it blank, and the field
only holds values from 2026-05 onward.

That gap matters because Triton's sales dashboard reports booked revenue from this field.
The only other candidate is ``modified``, which is the timestamp of whatever last touched
the row — bucketing wins by it is what made the old bookings chart show 187 wins in one
month and 81 in another (two bulk edits) with seven empty months between them.

**The source is the audit trail.** A status change writes a ``tabVersion`` row holding
``{"changed": [["status", "<old>", "Closed Won"], ...]}`` with ``creation`` as the moment
it happened. 220 of the 221 blanks have one.

**But most of those transitions are not wins, and this is the part worth reading before
changing anything here.** 197 of the 220 were flipped to Closed Won on a *single day* —
2025-08-20 — by two people, during the post-migration cleanup. Every other day in the set
carries one or two, which is what genuine deal flow looks like. Stamping all 220 would
therefore rebuild the very spike the dashboard was fixed to stop showing, just with a
different date on it and a more convincing provenance.

So a transition is only trusted when its day looks organic: if more than
``BULK_EDIT_THRESHOLD`` opportunities were moved to Closed Won on the same day, that day is
a bulk edit and its opportunities keep a blank date. The real close dates of those 197
deals are not in this system — they were won in the previous CRM, before the migration —
and a blank is the truthful representation. Triton renders undated wins as undated rather
than dating them by inference; the invoiced-revenue track carries the long history instead.
Expect this to stamp ~23 and leave ~198 blank. That is the intended outcome, not a
shortfall.

Keying on emptiness is correct **here**, which is worth stating given how loudly this repo
warns against it. That warning is about fields with a `default`: on a normal doctype the
`ALTER` writes the default into every existing row, so nothing is ever empty and an
emptiness predicate matches nothing (`backfill_relay_auth_identity`, v1.280.3).
``custom_date_closed_won`` is a Date custom field with no default, so its 221 NULLs are
genuine — verified against the live instance, not assumed. The predicate also makes the
patch safe twice and keeps a hand-corrected date from being overwritten on a re-run.
"""

import json
from collections import Counter

import frappe

FIELD = "custom_date_closed_won"
WON_STATUS = "Closed Won"
# More than this many deals moved to Closed Won on one day is a bulk edit, not a
# record day. The live data makes the cut obvious rather than arbitrary: the one
# excluded day carries 197, and the busiest genuine day carries 2.
BULK_EDIT_THRESHOLD = 20


def _won_transitions(docname: str) -> list[str]:
	"""Dates of every status change *into* Closed Won for one opportunity, newest first."""
	versions = frappe.get_all(
		"Version",
		filters={"ref_doctype": "Opportunity", "docname": docname},
		fields=["creation", "data"],
		order_by="creation desc",
		limit_page_length=0,
	)
	dates = []
	for version in versions:
		try:
			changes = (json.loads(version.data or "{}") or {}).get("changed") or []
		except (ValueError, TypeError):
			# A malformed audit row is not a reason to abandon the document; the
			# next version may still carry the transition.
			continue
		for change in changes:
			# ["fieldname", old, new] — guard the shape, these are free-form JSON.
			if not isinstance(change, (list, tuple)) or len(change) < 3:
				continue
			if change[0] == "status" and change[2] == WON_STATUS:
				dates.append(str(version.creation)[:10])
	return dates


def execute():
	if not frappe.db.has_column("Opportunity", FIELD):
		# Loudly, not quietly. A silent return here is indistinguishable from a
		# backfill that had nothing to do.
		frappe.log_error(
			f"Opportunity.{FIELD} does not exist; the won-date backfill did nothing. "
			"Check that the custom field fixture has been applied.",
			"Won-date backfill skipped",
		)
		return

	blanks = frappe.get_all(
		"Opportunity",
		filters={"status": WON_STATUS, FIELD: ["is", "not set"]},
		pluck="name",
		limit_page_length=0,
	)

	# Two passes: the first learns which days were bulk edits, because that can
	# only be known across the whole set, not per document.
	transitions = {name: _won_transitions(name) for name in blanks}
	per_day = Counter(day for dates in transitions.values() for day in dates)
	bulk_days = {day for day, count in per_day.items() if count > BULK_EDIT_THRESHOLD}

	stamped = 0
	skipped_bulk = 0
	no_record = 0
	for name, dates in transitions.items():
		# Newest-first: a deal that was won, reopened and won again is currently
		# won as of the last transition, and that is the date revenue belongs to.
		trusted = next((d for d in dates if d not in bulk_days), None)
		if trusted is None:
			if dates:
				skipped_bulk += 1
			else:
				no_record += 1
			continue
		# update_modified=False: `modified` is itself a signal other reports read,
		# and bumping these rows to today would be one more bulk edit of exactly
		# the kind this patch exists to avoid propagating.
		frappe.db.set_value("Opportunity", name, FIELD, trusted, update_modified=False)
		stamped += 1

	frappe.db.commit()
	frappe.logger().info(
		f"Opportunity won-date backfill: {len(blanks)} undated wins examined, "
		f"{stamped} stamped from the audit trail, {skipped_bulk} left blank "
		f"(only bulk-edit transitions, days={sorted(bulk_days) or 'none'}), "
		f"{no_record} left blank (no recorded status transition)."
	)
