"""Seed the inspection milestone catalog (v1.447.0, WI-075 sub-phase C).

The points in a project's life where an inspection is due, taken from the build spec's
"category-specific configuration" table. This is the catalog only — sub-phase D is what reads a
milestone and generates an inspection, so until then these are a declaration of intent that a
person acts on.

Keyed to ``Project.project_type`` as it exists on production, which is **not** the vocabulary
the spec uses:

* **Service** (354 projects) is what the spec calls Maintenance. Deliberately not renamed — the
  rename buys vocabulary rather than capability and would be a data migration across the largest
  category on the site.
* **Controls Fab** has no project type at all, so it rides on **Products**, added by
  ``seed_products_project_type`` in v1.444.0.
* **Design** milestones are review gates rather than physical checklists. A deficiency still
  runs through the same NCR and Quality Action machinery; only the shape of the checks differs.
* **Service**'s recurring check is **calendar-driven rather than project-stage driven**, which
  the spec flags as needing its own scheduling. It is seeded here with an interval so the
  intent is recorded; nothing acts on it yet.

Build and Products triggers name values of the existing ``Project.custom_build_status`` Select
(``Design Complete / Procurement / Assembly / QA / Ready for Install / Installed /
Commissioned``) rather than inventing a parallel state machine. **If one of those options is
ever renamed, these triggers stop matching — silently, because a trigger that matches nothing
looks exactly like a milestone that has not come round yet.** ``tests/test_inspection_milestones.py``
pins them against the live options.

Insert-only, keyed on ``milestone_key``: a site that has edited a title, a sequence or a trigger
keeps its edit. Logs the count, because a patch that matched nothing commits and writes its
``tabPatch Log`` row indistinguishably from one that did the work.
"""

import frappe

from erpnext_enhancements.quality.catalog import MILESTONES


def execute() -> None:
	if not frappe.db.exists("DocType", "Inspection Milestone"):
		return

	created, skipped_missing_type = 0, []
	existing = set(frappe.get_all("Inspection Milestone", pluck="milestone_key"))

	for (
		project_type, key, title, sequence, gate_kind,
		trigger_basis, trigger_value, interval_days, multi_day_only, description,
	) in MILESTONES:
		if key in existing:
			continue
		# A project type the site has not got is skipped rather than created: inventing one
		# here would put a value into `Project.project_type`'s catalog that nobody chose.
		if not frappe.db.exists("Project Type", project_type):
			skipped_missing_type.append(f"{key} (needs Project Type {project_type!r})")
			continue

		doc = frappe.new_doc("Inspection Milestone")
		doc.update(
			{
				"milestone_name": f"{project_type} — {title}",
				"project_type": project_type,
				"milestone_key": key,
				"milestone_title": title,
				"sequence": sequence,
				"gate_kind": gate_kind,
				"trigger_basis": trigger_basis,
				"trigger_value": trigger_value,
				"interval_days": interval_days,
				"multi_day_only": multi_day_only,
				"description": description,
			}
		)
		doc.insert(ignore_permissions=True)
		created += 1

	frappe.logger().info(
		f"seed_inspection_milestones: created {created} of {len(MILESTONES)}; "
		f"skipped for missing project type: {skipped_missing_type or 'none'}"
	)
