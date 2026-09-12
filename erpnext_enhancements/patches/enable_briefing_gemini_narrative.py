# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Turn the morning briefing's Gemini narrative on.

Its own patch rather than a line in `restore_drifted_single_defaults`, deliberately, and the
reason is not tidiness: **this one starts spending money.** It is the only drifted `Check` on
that Single, and `tests/test_single_default_drift.py` asserts it is absent from that table so
a later edit cannot quietly fold a spend decision into a data-hygiene backfill. Kept separate
it can also be reverted on its own, without unwinding the other 22.

--------------------------------------------------------------------------------------
What it was doing
--------------------------------------------------------------------------------------

Same Single default drift as the rest: `briefing_use_gemini` declares `default: "1"` and every
live row read `0`. `api/briefing._generate_narrative` reads it **raw** —
`if not cint(settings.get("briefing_use_gemini")): return compose_fallback(...), "Fallback",
None` — so the Vertex call was never attempted.

Production before this patch: **all 200 Daily Briefing rows from 2026-07-13 to 2026-09-11 are
`narrative_source = "Fallback"`**, every one with `generation_error` NULL, and `AI Model Usage`
holds no `morning_briefing` row at all. The Gemini narrative has never once run — including
the "Top 3 Priorities" section `compose_fallback` does not emit, which is the only part of the
briefing that is not already on the reader's task list. Nothing errored and nothing logged;
the single tell was a " · data-only" suffix in the block header that reads as a label rather
than a fault.

--------------------------------------------------------------------------------------
Why the flag alone was not enough, and why this ships in the same release as the rescue
--------------------------------------------------------------------------------------

Turning this on before v1.420.0 would have changed nothing except to start writing a missing-
key error into `generation_error` every weekday morning. `generate_content_with_vertex_ai`
reads `Triton Settings.get_password("maps_api_key")`, and that value was stranded under the
pre-rename doctype name in `__Auth` — see `rescue_renamed_doctype_auth_rows`, which ships
alongside this and is what actually makes the call possible.

The ordering inside a single migrate does not matter: this patch only writes a setting, and
the briefing next runs on its own cron.

--------------------------------------------------------------------------------------
Failure direction
--------------------------------------------------------------------------------------

Safe, and checked rather than assumed. `_generate_narrative` wraps the Vertex call in
`except Exception`, logs, and returns `compose_fallback(...)` with the reason recorded on
`generation_error`; an empty response falls back the same way. So the worst case after this
runs is exactly today's briefing plus a stated reason — never a missing or broken email.

Written with `db.set_single_value`, never `.save()`: a settings controller's `validate` can
throw on a field this never touches, and a patch that raises aborts `bench migrate`, which on
this repo is the deploy.

Deliberately **not** idempotent-by-overwrite: it writes only while the value is still falsy,
so switching the feature back off in the Desk is not undone by the next migrate.
"""

import frappe
from frappe.utils import cint

DOCTYPE = "ERPNext Enhancements Settings"
FIELD = "briefing_use_gemini"


def execute():
	if not frappe.db.exists("DocType", DOCTYPE):
		return

	try:
		current = frappe.db.get_single_value(DOCTYPE, FIELD)
	except Exception:
		# Column not there yet on a site mid-migrate. Next run picks it up.
		return

	if cint(current):
		# Already on, or somebody has since made the choice themselves. Either way this
		# patch has nothing to say — it restores a default that never arrived, it does
		# not enforce one.
		return

	try:
		frappe.db.set_single_value(DOCTYPE, FIELD, 1)
		frappe.clear_document_cache(DOCTYPE, DOCTYPE)
		print("enable_briefing_gemini_narrative: %s.%s <- 1" % (DOCTYPE, FIELD))
	except Exception:
		frappe.log_error(
			title="enable_briefing_gemini_narrative",
			message="Could not set %s.%s" % (DOCTYPE, FIELD),
		)
