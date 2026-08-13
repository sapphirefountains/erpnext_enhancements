# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the Marketing Settings Single doctype.

Master switch, per-platform flags and the sync dials. All behaviour lives in
``marketing.core``; this stays a plain model with one piece of logic, and that
piece exists because of a production bug in another module.

--------------------------------------------------------------------------------------
Why the dials repair themselves instead of refusing a zero
--------------------------------------------------------------------------------------

**A ``default`` on a new field of a Single never reaches the row that already exists.**
A Single stores one row per field in ``tabSingles``; ``bench migrate`` adds no row for a
newly declared field and ``load_from_db`` applies no defaults — they fire in ``new_doc()``,
i.e. on a fresh install and never again. So every dial below reads ``None``/``0`` on any
site that existed before the field did.

Chat Settings hit exactly this in v1.277.3. Thirty-seven fields shipped with sensible
defaults, the production row read ``None`` for all of them, and ``validate`` *refused* the
resulting zeros — so the settings page could not be saved at all. Fifteen errors about
fields you never touched, on the one page you need in order to switch the feature on. The
shape is worth naming: saving a Single deletes and re-inserts every field row, so a page
people use self-heals on the next save; the ones that bite are the settings for **dormant**
features, where the first save is the one you need and the one that fails.

This module is dormant by design, so it is squarely in that trap. Two defences ship
together, and both are deliberate:

1. ``patches/backfill_marketing_settings_defaults.py`` fills the missing rows on migrate.
   That is the actual fix and it runs once.
2. ``validate`` **coerces** a missing or non-positive dial back to its declared default
   rather than raising. Belt and braces: if the patch has not run — a site restored from an
   older backup, a partial migrate, a field added later without its backfill — the page still
   saves and repairs itself instead of bricking.

Coercion is applied **only to the positive-integer dials**, never to the ``Check`` fields.
A dial of ``0`` is meaningless (zero retries, zero-second timeout, zero-day retention) so
replacing it loses no intent. A checkbox of ``0`` is somebody deliberately switching
something off, and quietly restoring that to ``1`` would turn a feature back on behind their
back — which is the failure mode the whole draft/approve design exists to prevent.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

#: Dials that must be positive to mean anything, mapped to their declared default.
#: Kept in step with the JSON by ``tests/test_marketing_settings.py``, which reads the
#: schema rather than trusting this dict — a default changed in one place and not the other
#: is exactly the drift that makes a self-healing value heal to the wrong thing.
POSITIVE_DIALS = {
	"restate_days": 7,
	"initial_backfill_days": 90,
	"max_retries": 3,
	"request_timeout_seconds": 30,
	"sync_batch_size": 500,
	"raw_payload_retention_days": 90,
}


class MarketingSettings(Document):
	def validate(self):
		self._repair_dials()

	def _repair_dials(self):
		"""Restore any dial that is missing or non-positive to its declared default.

		Reads the default from the *meta* rather than the constant above, so the JSON stays
		the single source of truth; the constant is the fallback for a field whose default
		was removed from the schema entirely.
		"""
		meta = frappe.get_meta(self.doctype)
		repaired = []

		for fieldname, fallback in POSITIVE_DIALS.items():
			if cint(self.get(fieldname)) > 0:
				continue

			field = meta.get_field(fieldname)
			declared = cint(field.default) if field and field.default else 0
			value = declared if declared > 0 else fallback

			self.set(fieldname, value)
			repaired.append(fieldname)

		if repaired:
			# A message rather than silence: the value on screen just changed under the
			# operator, and a settings page that edits itself without saying so is its own
			# small betrayal.
			frappe.msgprint(
				_("Restored default values for: {0}").format(", ".join(sorted(repaired))),
				indicator="orange",
				alert=True,
			)
