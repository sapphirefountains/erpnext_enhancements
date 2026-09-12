# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Say what each Position requires, so the qualification reports can mean something.

`tabPosition Requirement` held **zero rows across all twenty Positions** against a seeded
taxonomy of fifteen Credential Types, and `tabEmployee Credential` held zero too. So the
Skills Matrix and Qualification Coverage were correctly reporting that nobody holds anything
— because nothing had ever said what a position needs. The reports were not broken; they had
nothing to report on.

Seeded 2026-09-12 at Nik's direction, who chose the **full twenty-position mapping** over a
field-roles-only subset after being shown that every employee goes red on every line until
credentials are actually entered.

--------------------------------------------------------------------------------------
Requirements do NOT inherit down the Position tree — this is the load-bearing fact
--------------------------------------------------------------------------------------

`progression.requirements_for` reads ``frappe.get_doc("Position", position).get("requirements")``
and `skills_matrix` reads `frappe.get_cached_doc(...).get("requirements")`. Neither walks
`parent_position`. So a baseline placed on the **All Positions** root would apply to nobody,
and every leaf has to spell out its whole set including the shared parts.

That is why `Driver's License` appears nineteen times below rather than once. It reads as
duplication and is not: it is the only shape the consumers can actually see.

The two **group** positions — `All Positions` and `Technician` — are deliberately given
nothing. A group is a job family, not a job; nobody holds one (confirmed against
`Employee.custom_position` on prod), and a row there would be invisible to every reader.

--------------------------------------------------------------------------------------
`is_mandatory` is used to mean something
--------------------------------------------------------------------------------------

Flagging everything mandatory would make the distinction worthless and the roster uniformly
red — the same mistake the pipeline thresholds made in v1.419.0. So:

* **mandatory** = the job genuinely cannot be done without it. A technician who has not done
  confined-space entry may not enter a drained basin, and that is not a matter of degree.
* **not mandatory** = expected, tracked, and not a stop-work item. Mostly the driving and
  general-awareness lines for office roles that visit sites occasionally.

**CDL and DOT Medical Card are deliberately assigned to nobody.** They are seeded in the
taxonomy for completeness, but they apply to commercial vehicles over 26,000 lb and nothing
in this fleet qualifies. Assigning them would manufacture a permanent gap against a rule that
does not apply to this company. Add them if that ever changes.

--------------------------------------------------------------------------------------
Mechanics
--------------------------------------------------------------------------------------

**Seeds only a Position whose `requirements` table is empty.** If anybody has added a single
row by hand, that Position is left entirely alone — this puts in a starting point that was
never there, it does not enforce a policy. That predicate also makes it safe to run twice.

Saved through the doc API rather than by writing child rows directly, because `Position` is a
`NestedSet` and its `on_update` maintains `lft`/`rgt` and repairs descendants; going around
that to save a few milliseconds is how a tree gets corrupted. Each Position is wrapped
separately, so one failure cannot take the deploy with it — a patch that raises aborts
`bench migrate`, which on this repo is the deploy.
"""

import frappe

CREDENTIAL = "Credential"

# Position -> ((Credential Type, is_mandatory), ...)
#
# Read `progression.requirements_for`: no inheritance, so each row is the complete set for
# that rung, shared lines included.
REQUIREMENTS = {
	# ---- the field ladder. Everything here enters basins, drives, and handles chemicals.
	"Junior Technician": (
		("Driver's License", 1),
		("First Aid / CPR", 1),
		("OSHA 10", 1),
		("Confined Space Entry", 1),
		("Lockout / Tagout", 1),
		("Water Treatment / Chemical Handling", 1),
	),
	"Senior Technician": (
		("Driver's License", 1),
		("First Aid / CPR", 1),
		("OSHA 10", 1),
		("Confined Space Entry", 1),
		("Lockout / Tagout", 1),
		("Water Treatment / Chemical Handling", 1),
		("Aerial / Scissor Lift", 1),
		("Trenching & Excavation", 0),
		("Pool / Spa Operator", 0),
	),
	"Master Technician": (
		("Driver's License", 1),
		("First Aid / CPR", 1),
		("OSHA 30", 1),
		("Confined Space Entry", 1),
		("Lockout / Tagout", 1),
		("Water Treatment / Chemical Handling", 1),
		("Aerial / Scissor Lift", 1),
		("Trenching & Excavation", 1),
		("Pool / Spa Operator", 1),
		("Respirator Fit Test", 1),
		("Forklift Operator", 0),
	),
	# ---- runs the field. Supervises entry, so holds the entry tickets themselves.
	"Operations Manager": (
		("Driver's License", 1),
		("First Aid / CPR", 1),
		("OSHA 30", 1),
		("Confined Space Entry", 1),
		("Lockout / Tagout", 1),
	),
	"Project Manager": (
		("Driver's License", 1),
		("First Aid / CPR", 1),
		("OSHA 10", 1),
	),
	# ---- design and engineering. On site regularly, not entering confined spaces.
	"Designer": (("Driver's License", 1), ("OSHA 10", 0)),
	"Junior Designer": (("Driver's License", 1), ("OSHA 10", 0)),
	"Engineer": (("Driver's License", 1), ("OSHA 10", 0)),
	"Electrical Designer": (
		("Driver's License", 1),
		("OSHA 10", 0),
		("Electrical License", 1),
	),
	"Software Engineer": (("Driver's License", 0),),
	# ---- office and commercial. Site visits happen; basin entry does not.
	"Chief Executive Officer": (("Driver's License", 0),),
	"Finance & Accounting Manager": (("Driver's License", 0),),
	"AP/AR, Purchasing Manager": (("Driver's License", 0),),
	"HR Manager": (("Driver's License", 0), ("First Aid / CPR", 0)),
	"Internal Systems Manager": (("Driver's License", 0),),
	"Marketing Specialist": (("Driver's License", 0),),
	"Sales Representative": (("Driver's License", 1), ("OSHA 10", 0)),
	# ---- the yard. Moves stock, so the lift ticket is the real one here.
	"Purchasing Agent/Inventory Clerk": (
		("Driver's License", 1),
		("Forklift Operator", 1),
	),
}


def execute():
	if not frappe.db.exists("DocType", "Position"):
		return
	if not frappe.db.exists("DocType", "Credential Type"):
		return

	seeded = []
	for position, rows in REQUIREMENTS.items():
		try:
			if _seed(position, rows):
				seeded.append(position)
		except Exception:
			frappe.log_error(
				title="seed_position_credential_requirements",
				message="Could not seed %s" % position,
			)

	if seeded:
		print(
			"seed_position_credential_requirements: %d position(s) -- %s"
			% (len(seeded), ", ".join(sorted(seeded)))
		)


def _seed(position, rows):
	if not frappe.db.exists("Position", position):
		# A site that renamed or has not created this rung. Not an error.
		return False

	doc = frappe.get_doc("Position", position)
	if doc.get("requirements"):
		# Somebody has already said what this rung needs. Leave it entirely alone —
		# this seeds a starting point that was never there, it does not enforce one.
		return False

	added = 0
	for credential, mandatory in rows:
		if not frappe.db.exists("Credential Type", credential):
			# The taxonomy is seeded separately; a missing type means a narrower set,
			# never a failed migrate.
			continue
		doc.append(
			"requirements",
			{
				"requirement_type": CREDENTIAL,
				"credential_type": credential,
				"is_mandatory": 1 if mandatory else 0,
			},
		)
		added += 1

	if not added:
		return False

	doc.save(ignore_permissions=True)
	return True
