"""Seed the Position ladder and map every Employee onto it (v1.386.0).

Two jobs, and the second is the one that makes the first mean anything: create
the tree, then set ``Employee.custom_position`` for people whose Designation
already names their rung.

**Only one ladder is asserted, and it is the one Nik specified**: Technician,
Junior 1 / Senior 2 / Master 3. Every other current Designation becomes a
single-rung position that is its own job family, so it outranks nobody and
nobody outranks it. That is deliberate rather than lazy — "Electrical Designer"
is a *specialty*, not a rank, and putting it at tier 2 would silently assert it
outranks a Junior Designer, which is a claim about somebody's authority over
somebody else that no one has made. Nik can build the rest of the ladders in the
tree view; the point of a tree is that it does not need a deploy.

``Master Technician`` is seeded even though nobody holds it today. It is the top
of the ladder Nik described, and a ladder whose top rung does not exist yet is
exactly what a promotion target looks like.

Insert-only and idempotent. It never overwrites a ``custom_position`` somebody
has already set, and it never touches a Designation.

**Not a fixture.** ``Position`` is a nested set: fixtures are imported by
delete-and-reinsert, and re-inserting the rows of a tree in file order rebuilds
``lft``/``rgt`` from whatever ``parent_position`` happens to resolve at the time.
Seeding it as data once, and letting the tree be edited in the tree view
thereafter, is the shape that survives.
"""

import frappe
from frappe.utils import now_datetime

ROOT = "All Positions"

#: Marks that the one-shot Employee backfill has run. Stored as a global default
#: rather than in Patch Log, because the mapping is an ``after_migrate`` hook and
#: has no Patch Log row of its own to key on.
MAPPED_STAMP = "ee_positions_mapped_on"

# (name, parent, is_group, tier, tier_label)
LADDER = (
	(ROOT, None, 1, 0, None),
	("Technician", ROOT, 1, 0, None),
	("Junior Technician", "Technician", 0, 1, "Junior"),
	("Senior Technician", "Technician", 0, 2, "Senior"),
	("Master Technician", "Technician", 0, 3, "Master"),
)

#: Designations that are their own single-rung family. Tier 1 across the board:
#: within a family of one, the number never compares against anything.
STANDALONE_TIER = 1


def execute():
	if not frappe.db.exists("DocType", "Position"):
		# The module did not install on this migrate. Say so rather than failing the
		# deploy: setup/module_map.py exists to prevent exactly this, and if it has
		# not worked the useful signal is a line in the log, not an abort.
		frappe.log_error(
			"Position DocType is not on this site, so the ladder was not seeded. "
			"Check that 'HR Enhancements' reached frappe.local.app_modules "
			"(setup/module_map.py) before model sync.",
			"HR Enhancements",
		)
		return

	for name, parent, is_group, tier, tier_label in LADDER:
		_ensure(name, parent, is_group, tier, tier_label)

	# Every Designation actually in use that the ladder does not already name.
	seeded = {row[0] for row in LADDER}
	for designation in frappe.get_all("Designation", pluck="name"):
		if designation in seeded:
			continue
		_ensure(designation, ROOT, 0, STANDALONE_TIER, None)

	# NOT mapped here. `Employee.custom_position` is a FIXTURE Custom Field, and
	# `sync_fixtures()` runs in `post_schema_updates()` -- *after* the post-model-sync
	# patches (frappe v16 `migrate.py:143` then `:171`). So on the very migrate that
	# introduces the field the column does not exist yet, this would map nobody, and
	# the patch would record itself in `tabPatch Log` and never run again: a
	# permanently empty ladder that looks like it was seeded successfully.
	#
	# The mapping is an `after_migrate` hook instead, which runs after fixtures and
	# is idempotent, so it lands on this deploy and self-heals on every later one.
	# Found by the adversarial review of this branch.


def _ensure(name, parent, is_group, tier, tier_label):
	if frappe.db.exists("Position", name):
		return
	frappe.get_doc(
		{
			"doctype": "Position",
			"position_name": name,
			"parent_position": parent,
			"is_group": is_group,
			"tier": tier,
			"tier_label": tier_label,
			"is_active": 1,
		}
	).insert(ignore_permissions=True)


def map_employees_to_positions():
	"""Point each Employee at the Position that shares its Designation's name.

	An ``after_migrate`` hook rather than part of the patch above, because the
	column it writes is created by ``sync_fixtures()``, which runs after patches.
	Cheap and idempotent: one indexed read per employee, and it writes only where
	``custom_position`` is empty, so every later migrate is a no-op.

	Matched on the name because the seed above *is* the Designation list, so the
	correspondence is exact by construction rather than by guesswork. An Employee
	with no Designation is left alone — there is nothing to infer, and inventing a
	position for somebody would put them on a ladder they were never placed on.

	Note which predicate this keys on: **an empty ``custom_position``**, not "the
	field was just added". Those are the same thing here only because Employee is a
	normal doctype and the new column arrived with no default — had the field
	carried one, MariaDB would have written it into every existing row as part of
	the ALTER and an emptiness-keyed backfill would have matched nothing while
	still recording itself as a successful run.
	"""
	if not frappe.db.exists("DocType", "Position"):
		return
	try:
		if not frappe.db.has_column("Employee", "custom_position"):
			return
	except Exception:
		# has_column raises rather than returning False when the table is missing.
		return

	# The backfill is ONE-SHOT, and the stamp is what makes it so. This runs on
	# every migrate forever, and "custom_position is empty" is not a durable
	# predicate for it: clearing the field is how somebody says "this person is on
	# no ladder, and holds no tier authority over anyone". Re-deriving it from
	# their Designation on the next deploy would silently overrule that decision
	# and hand back sign-off authority a human deliberately took away -- the exact
	# inverse of the safe direction. Found by the migrate-safety audit.
	#
	# After the stamp, only employees created SINCE it are placed, so new hires
	# still land on the ladder automatically and nobody's manual clearing is ever
	# second-guessed.
	stamp = frappe.db.get_global(MAPPED_STAMP)
	filters = {"designation": ["is", "set"]}
	if stamp:
		filters["creation"] = [">", stamp]

	rows = frappe.get_all(
		"Employee",
		filters=filters,
		fields=["name", "designation", "custom_position"],
	)
	mapped = 0
	for row in rows:
		if row.custom_position:
			continue
		if not frappe.db.exists("Position", row.designation):
			continue
		# Both columns in one write. `custom_position_tier` is a `fetch_from` field
		# (`custom_position.tier`), and fetch_from is resolved by
		# `Document.set_fetch_from_value()` during a save -- `db.set_value` does not
		# go through the document, so writing only the Link would leave the Tier
		# column reading 0 on every backfilled Employee while the ladder said
		# otherwise. The tier shown on a person is display only (authority is always
		# read from the Position itself), but a display that contradicts the record
		# is worse than no display. Found by the migrate-safety audit.
		update = {"custom_position": row.designation}
		try:
			if frappe.db.has_column("Employee", "custom_position_tier"):
				update["custom_position_tier"] = (
					frappe.db.get_value("Position", row.designation, "tier") or 0
				)
		except Exception:
			pass
		frappe.db.set_value("Employee", row.name, update, update_modified=False)
		mapped += 1

	if not stamp:
		frappe.db.set_global(MAPPED_STAMP, now_datetime())

	if mapped:
		print(f"[erpnext_enhancements] placed {mapped} employee(s) on the Position ladder")
