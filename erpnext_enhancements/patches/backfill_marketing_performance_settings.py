"""Give the existing Marketing Settings row the three fields v1.756.0 added (Marketing Performance).

``google_business_profile_enabled`` and ``social_page_stats_enabled`` (both ``0``) and
``branded_terms`` (``sapphire``) are new fields on a Single, so ``bench migrate`` gives the row
that already exists none of their defaults -- CLAUDE.md's Single-defaults trap. The branded terms
would read blank, and the page would call no query branded until somebody opened the settings and
saved them.

This is ``backfill_marketing_settings_defaults`` again, under a new ``patches.txt`` line so it runs
on a site whose Patch Log already holds the old one. It fills a field **only where tabSingles has
no row for it**, never over a stored value: a switch somebody turned off stays off. The same
function also runs on every ``after_migrate``. Safe twice.
"""

from erpnext_enhancements.patches.backfill_marketing_settings_defaults import (
	backfill_marketing_settings_defaults,
)


def execute() -> None:
	backfill_marketing_settings_defaults()
