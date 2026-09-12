# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Move the Sales Pipeline staleness thresholds from 7/14 to 45/90.

v1.419.0 restored these two fields to their declared defaults after finding them stored as
`0`, which had made `_stale_level`'s `if amber_days > 0` / `if red_days > 0` guards
unreachable and left **every** card on the board unlit. That was the right fix to the drift.
It also made plain that the declared numbers were never calibrated: 7 and 14 days are a
plausible guess at what "stale" means and bear no relation to how this pipeline actually
moves.

Measured on 2026-09-12 across the 39 gated open cards — Qualification, Needs Analysis and
Negotiation/Review; `On Hold` is parked and never flags, and Closed Won ages on its own
1/3-day clock — `days_in_stage` ran from 2 to 134 with a natural break around 60:

    7 / 14   ->  4 fresh, 1 amber, 34 red
    30 / 60  ->  9 fresh, 11 amber, 19 red
    45 / 90  -> 15 fresh, 12 amber, 12 red     <- chosen
    60 / 120 -> 20 fresh, 18 amber, 1 red

**A board that is uniformly red carries no more information than one that is uniformly
green.** It reads as urgent, which is worse than reading as nothing, because the colour stops
being a queue and becomes wallpaper. 45/90 is the only setting where all three tiers
distinguish anything, and it falls on the gap in the data rather than on a round number
somebody liked.

Chosen by Nik on 2026-09-12 from those four measured options. Deliberately **not** picked to
make the board look calm: the numbers were computed first and the split shown before the
choice, because quietly widening a threshold until the red goes away reproduces the v1.419.0
failure in a politer form.

Guarded on the values still being exactly the uncalibrated pair, so a later hand-tune in the
Desk is never clobbered. `db.set_single_value`, never `.save()` — a patch that raises aborts
`bench migrate`, which on this repo is the deploy. Safe to run twice.
"""

import frappe
from frappe.utils import cint

DOCTYPE = "ERPNext Enhancements Settings"

# (fieldname, the uncalibrated value this replaces, the calibrated value)
MOVES = (
	("pipeline_stale_amber_days", 7, 45),
	("pipeline_stale_red_days", 14, 90),
)


def execute():
	if not frappe.db.exists("DocType", DOCTYPE):
		return

	for fieldname, was, want in MOVES:
		try:
			current = frappe.db.get_single_value(DOCTYPE, fieldname)
		except Exception:
			continue

		# Only the exact pre-calibration value moves. Not "anything falsy", and not
		# unconditionally: somebody may have tuned these by hand between the v1.419.0
		# backfill and this deploy, and that choice outranks this one.
		if cint(current) != was:
			continue


		try:
			frappe.db.set_single_value(DOCTYPE, fieldname, want)
			print("calibrate_pipeline_staleness: %s %s -> %s" % (fieldname, was, want))
		except Exception:
			frappe.log_error(
				title="calibrate_pipeline_staleness",
				message="Could not set %s.%s" % (DOCTYPE, fieldname),
			)

	frappe.clear_document_cache(DOCTYPE, DOCTYPE)
