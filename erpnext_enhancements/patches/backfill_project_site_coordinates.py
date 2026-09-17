"""Enqueue a geocode for every active Project that has no site coordinates.

v1.480.0 teaches the Time Kiosk where a project's site is (``workforce/sites.py``) so it can
flag an off-site clock-in and draw the site on the map. On 2026-09-17 almost nothing could
answer that question: 16 Sapphire Maintenance Profiles, **0** with coordinates; 0 active
Projects linking an Address; 11 of 1,024 Addresses geocoded. So this patch enqueues
``sites.geocode_project`` for every ``is_active = Yes`` Project without coordinates that has
some address text, bounded to 200 per run, on the ``long`` queue.

**Enqueued, not run inline.** Each job is a Google Geocoding call; ~76 of them inside
``bench migrate`` would hold the deploy for the API's round-trips and any failure would abort
it. The jobs run after the migrate commits.

**A deploy ``FLUSHDB``s the queue redis and destroys every queued job**, silently — the
confirmed cause of a batch of Drive folders that were never created (CLAUDE.md). This patch
is therefore not the only driver: ``workforce.sites.backfill_missing_site_coordinates`` is
also a daily scheduler job that enqueues whatever is still missing, and ``geocode_project``
is idempotent (returns at once when coordinates exist). If this deploy's batch is destroyed
by the next one, tomorrow's sweep re-drives it. Nothing here needs to have run for the
kiosk to work; a project without coordinates simply gets no off-site check.

Never raises. Safe twice.
"""

import frappe


def execute() -> None:
	try:
		from erpnext_enhancements.workforce import sites

		enqueued = sites.backfill_missing_site_coordinates()
		print(f"Project site coordinates: {enqueued} geocode job(s) enqueued")
	except Exception:
		frappe.log_error(title="backfill_project_site_coordinates")
