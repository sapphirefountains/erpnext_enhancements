"""Give the five starter badges their artwork on sites that already have them.

``training/setup.py``'s ``ensure_training_badges`` is **insert-only** — deliberately, so that a
badge an admin renamed, re-priced or disabled stays that way. That is the right rule and it has the
consequence this patch exists for: adding an ``image`` to ``STARTER_BADGES`` reaches a fresh
install and **no existing site**. Production has held all five since Phase 4, so without this they
would render as empty circles forever while the code says they have pictures.

**Only where the field is empty.** A badge whose image somebody has already set is somebody's
decision, and the whole reason the seeder is insert-only is to not walk over those. ``coalesce(...,
'') = ''`` is the predicate, checked per row rather than in a bulk UPDATE, because the set is five
rows and reading the value first is what makes "never overwrite" true rather than intended.

**The rows hold ``NULL``, not ``''``, and that distinction nearly made this patch a no-op.**
``frappe.db.get_value`` returns ``None`` for *both* "no such row" and "the field is NULL", so the
obvious `if current is None: continue` — meaning "this site does not have that badge" — would have
read all five as absent, skipped every one, committed nothing, and printed a success line. Existence
is therefore asked separately, with ``db.exists``, before the value is read at all. Measured on
production 2026-09-15: five rows present, all five with ``image IS NULL``.

That is a cousin of the trap ``CLAUDE.md`` records rather than the same one. The recorded trap is a
*new* field whose ``default`` the ``ALTER`` writes into every existing row, so a backfill keyed on
emptiness matches nothing. Here ``image`` is an existing field with no default and the rows really
are empty — what bites is not the predicate but the sentinel the read returns for two different
facts.

``db_set`` rather than ``doc.save()`` on purpose: this touches one field on five rows and there is
no controller logic on ``Training Badge`` that needs to run. ``update_modified=False`` so a purely
cosmetic backfill does not make five records look freshly edited in every timeline and list view.

Insert-only in spirit and idempotent: a second run finds the field set and does nothing.
"""

import frappe

from erpnext_enhancements.training.setup import BADGE_IMAGE_BASE, STARTER_BADGES

BADGE_DOCTYPE = "Training Badge"


def execute() -> None:
	# A patch that raises aborts `bench migrate`, which on this repo IS the deploy -- and the
	# failure is not a stopped deploy but a half-finished one. Every guard here returns quietly.
	if not frappe.db.exists("DocType", BADGE_DOCTYPE):
		# Phase 4 may not have migrated on this site yet. Not an error: the badge doctype arriving
		# later is exactly what the next migrate is for, and it will arrive with the image already
		# on it, because `ensure_training_badges` now sets one.
		return

	updated = []
	skipped = []

	for name, _description, _criteria_type, _criteria_value, _points, image in STARTER_BADGES:
		try:
			# Existence is asked SEPARATELY, and that is the whole correctness of this loop.
			# `frappe.db.get_value` returns None for BOTH "no such row" and "the field is NULL",
			# and on production all five of these hold NULL rather than '' -- so a single
			# `if current is None: continue` would have read every badge as absent, skipped all
			# five, committed nothing and printed a cheerful success. Measured 2026-09-15.
			if not frappe.db.exists(BADGE_DOCTYPE, name):
				# `ensure_training_badges` runs on after_migrate and will create it with its
				# image, so there is genuinely nothing to do here.
				continue
			current = frappe.db.get_value(BADGE_DOCTYPE, name, "image")
			if (current or "").strip():
				skipped.append(name)
				continue
			frappe.db.set_value(
				BADGE_DOCTYPE,
				name,
				"image",
				f"{BADGE_IMAGE_BASE}/{image}",
				update_modified=False,
			)
			updated.append(name)
		except Exception:
			# One badge must not take the other four down, and must never abort the migrate.
			# Badges are decoration; a deploy is not -- the same rule `training/setup.py` keeps.
			frappe.log_error(
				title="Starter badge image backfill failed",
				message=f"{name}: {frappe.get_traceback()}",
			)
			continue

	if updated:
		frappe.db.commit()

	print(
		f"backfill_starter_badge_images: {len(updated)} given artwork, "
		f"{len(skipped)} already had an image and were left alone"
	)
