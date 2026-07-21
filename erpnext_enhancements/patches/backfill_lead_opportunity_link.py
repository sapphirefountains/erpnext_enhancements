"""One-time migration patch (post_model_sync; listed in patches.txt).

Reconstructs the Lead → Opportunity back-link that was never persisted.

``script_migrations.opportunity.update_lead_status`` has, since it was written,
assigned ``lead_doc.opportunity = doc.name``. ERPNext's Lead has no ``opportunity``
field, and frappe silently discards unknown attributes on save rather than
raising — so every Lead converted to an Opportunity was marked ``Converted`` but
never pointed at the Opportunity it became. The forward link
(``Opportunity.party_name`` when ``opportunity_from == "Lead"``) was always
correct, so the reverse can be derived exactly rather than guessed.

Same shape as ``patches.backfill_project_opportunity_link``, which fixed the same
class of bug on Project.

**Safety.** Verified against the live data before writing this: every Lead that
has an Opportunity has exactly ONE, so collapsing the relationship into a single
Link field loses nothing. Should that ever stop being true, the ``ORDER BY
creation`` below makes the choice deterministic (the earliest Opportunity wins,
i.e. the one the conversion actually produced) rather than arbitrary.

Deliberately conservative:

* **Insert-only.** A Lead whose ``custom_opportunity`` is already set is skipped,
  so a hand-corrected link is never overwritten.
* **Link is verified.** A ``party_name`` pointing at a deleted Lead, or an
  Opportunity that no longer exists, is skipped rather than written — a Link
  field holding a dangling name makes the record un-saveable (cf. the
  ``clear_invalid_primary_address_links`` patch).
* **Status is not touched.** Some of these Leads sit at ``Opportunity`` or
  ``Lost Quotation`` rather than ``Converted``; that is somebody's deliberate
  pipeline state and none of this patch's business.
* **db_set-level writes**, ``update_modified=False``: this is a data repair, not
  user activity, and re-saving ~200 Leads through the ORM would fire every
  Lead hook (including the global Triton sync) for no reason.

**Ordering.** Patches run *before* fixture sync (see ``fixtures/README.md``), so
on the deploy that introduces ``Lead.custom_opportunity`` the column does not
exist yet when this executes — and because the Patch Log records the patch as
done, it would never get a second chance. Caught on a real bench: the first run
backfilled 0 of 11. The patch therefore creates the Custom Field itself when
missing, using the exact fixture definition and the same record name, so fixture
sync adopts it later in the same migrate and the fixtures stay the source of
truth. Same approach as ``patches.backfill_stage_changed_on``.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field


def execute():
	_ensure_field_exists()

	if not frappe.db.has_column("Lead", "custom_opportunity"):
		# Should be unreachable after the call above. Log rather than fail the
		# migrate — but do not fail *silently*, which is the bug being fixed.
		frappe.log_error(
			"Lead.custom_opportunity column still absent after create_custom_field — "
			"backfill_lead_opportunity_link did nothing. Re-run it manually:\n"
			"  bench --site <site> execute "
			"erpnext_enhancements.patches.backfill_lead_opportunity_link.execute",
			"Lead back-link not backfilled",
		)
		return

	pairs = frappe.db.sql(
		"""
		SELECT o.name AS opportunity, o.party_name AS lead
		FROM `tabOpportunity` o
		WHERE o.opportunity_from = 'Lead'
		  AND IFNULL(o.party_name, '') != ''
		ORDER BY o.creation ASC
		""",
		as_dict=True,
	)

	linked = 0
	skipped_missing_lead = 0
	skipped_already_set = 0

	for pair in pairs:
		if not frappe.db.exists("Lead", pair.lead):
			# party_name points at a deleted Lead. Writing the reverse link is
			# impossible and the forward one is already dangling — leave both alone.
			skipped_missing_lead += 1
			continue

		if frappe.db.get_value("Lead", pair.lead, "custom_opportunity"):
			# Already linked — by an earlier run of this patch, by the fixed hook,
			# or by hand. Never overwrite.
			skipped_already_set += 1
			continue

		frappe.db.set_value(
			"Lead", pair.lead, "custom_opportunity", pair.opportunity, update_modified=False
		)
		linked += 1

	frappe.db.commit()
	print(
		f"backfill_lead_opportunity_link: {linked} linked, "
		f"{skipped_already_set} already set, {skipped_missing_lead} lead missing "
		f"(of {len(pairs)} Lead-party opportunities)"
	)


def _ensure_field_exists():
	"""Create Lead.custom_opportunity if fixture sync has not run yet.

	Mirrors the fixture record exactly — same name, same definition, and
	``is_system_generated=False`` — so the later fixture sync updates this record
	rather than creating a second one.
	"""
	if frappe.db.exists("Custom Field", "Lead-custom_opportunity"):
		return
	create_custom_field(
		"Lead",
		{
			"fieldname": "custom_opportunity",
			"fieldtype": "Link",
			"label": "Opportunity",
			"options": "Opportunity",
			"insert_after": "custom_contact_link",
			"read_only": 1,
			"description": (
				"The Opportunity this Lead converted into. erpnext's Lead has no "
				"'opportunity' field, so script_migrations.opportunity.update_lead_status "
				"was assigning to a non-existent attribute and the back-link never persisted."
			),
		},
		is_system_generated=False,
	)
