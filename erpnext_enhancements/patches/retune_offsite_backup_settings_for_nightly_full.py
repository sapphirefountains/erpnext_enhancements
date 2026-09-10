"""Move the two Offsite Backup Settings dials that the nightly-full schedule invalidated.

The schedule changed from "database-only at 02:00, full at 03:00 on Sundays" to a single
**full** backup every night. Two stored numbers were calibrated against the old shape and are
wrong against the new one:

``alert_if_full_older_than_hours`` was **192** -- eight days, one Sunday plus a day of grace.
Against a nightly full backup it is a threshold that can only fire after the database tier has
already been shouting for six days, so the tier that actually watches the files stops being an
independent check and becomes an echo. **36** matches the database tier: one missed night,
one cycle of grace.

``min_keep`` was **14**. It is a floor counted in *objects*, and a full run uploads three of
them (database, public files, private files) where a database-only run uploaded one. The old
mix was 7 + 3 = 10 objects a week, so 14 objects was a fortnight of nights. The same 14 is now
four and a half nights. **21** puts it back to a week.

Neither number is load-bearing on its own -- ``retention_days`` (90) is the real policy and
``backup.py`` falls back to the same 36 when the field reads empty. They matter because they
are the numbers an operator opens the form to read. A stale threshold that silently disagrees
with the code's fallback is how a dial stops being trusted.

**Why a patch at all.** These are fields on a *Single*, and they already have rows in
``tabSingles``. Changing the ``default`` in the doctype JSON reaches a brand-new site and
nothing else: ``load_from_db`` reads ``tabSingles`` and applies no defaults, and the fallback
to ``new_doc`` (the one path that does) only fires when the Single has no stored rows at all.
So on this site the form would keep showing 192 and 14 while the JSON claimed otherwise --
the same trap as v1.277.3, in the direction where nothing throws and nobody notices.

**Only over the old default, never over a choice.** The predicate is "still holds the value
this app shipped", not "is empty". An operator who deliberately set 336 because they are
comfortable with a fortnight has made a decision, and a migration is not the place to
overrule it. A field with no row at all is written too -- no row is not a decision, and the
new default is what a fresh site would get.

Safe to run twice: the second pass finds the new values and matches neither branch.
"""

import frappe

SETTINGS_DOCTYPE = "Offsite Backup Settings"

# (fieldname, the value this app used to ship, the value it ships now). Stored as strings
# because tabSingles.value is a text column and is compared as one.
RETUNED = (
	("alert_if_full_older_than_hours", "192", "36"),
	("min_keep", "14", "21"),
)


def execute() -> None:
	retune_offsite_backup_settings_for_nightly_full()


def retune_offsite_backup_settings_for_nightly_full() -> int:
	"""Rewrite each dial that still holds its old default. Returns how many it wrote."""
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return 0

	# Read tabSingles directly rather than through get_single_value: that casts by fieldtype,
	# which turns a missing row into 0 for an Int and makes "never set" indistinguishable from
	# "set to zero". The two need different answers here.
	stored = dict(
		frappe.db.sql(
			"select field, value from tabSingles where doctype = %s and field in %s",
			(SETTINGS_DOCTYPE, tuple(field for field, _old, _new in RETUNED)),
		)
	)

	written = 0
	for field, old, new in RETUNED:
		current = stored.get(field)
		if current is not None and str(current).strip() != old:
			continue
		# set_single_value writes tabSingles directly and does not run the controller --
		# which matters, because OffsiteBackupSettings.validate() throws on a half-configured
		# form and a migrate must not depend on the credentials being in place.
		frappe.db.set_single_value(SETTINGS_DOCTYPE, field, new)
		written += 1

	if written:
		frappe.clear_document_cache(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)

	return written
