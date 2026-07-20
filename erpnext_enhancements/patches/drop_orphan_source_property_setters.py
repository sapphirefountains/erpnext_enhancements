"""One-time migration patch (post_model_sync; listed in patches.txt).

Deletes three Property Setters that target a field ERPNext no longer has.

ERPNext v15 renamed ``Lead.source`` and ``Opportunity.source`` to ``utm_source``
(the field JSON still carries ``"oldfieldname": "source"``). These three
customizations were never repointed and have been silently inert ever since:

===============================  ==============  ====================
Property Setter                  Property        Intended effect
===============================  ==============  ====================
``Lead-source-reqd``             ``reqd = 1``    make source mandatory
``Opportunity-source-reqd``      ``reqd = 1``    make source mandatory
``Lead-source-label``            ``label``       "Lead Source"
===============================  ==============  ====================

Verified against the live site: ``frappe.get_meta("Lead").get_field("source")``
and the Opportunity equivalent both return ``None``. A Property Setter for a
non-existent field is not an error — frappe simply never finds a docfield to
apply it to — so this failed open with no symptom, and lead-source attribution
on Lead and Opportunity has in practice been unrecorded.

Removing them from ``fixtures/property_setter.json`` alone is NOT enough: fixture
sync is create/update-only, so an unmanaged record lingers in the database
forever (see ``fixtures/README.md``). Hence this patch.

The replacement is not a repointed Property Setter but three real Custom Fields
(``Lead.custom_lead_source``, ``Lead.custom_opportunity``,
``Opportunity.custom_lead_source``) shipped in ``fixtures/custom_field.json``,
matching the already-populated ``Customer.custom_lead_source``. ``utm_source`` is
deliberately left to erpnext's own semantics — the fountain-move conversion uses
it only for its documented side effect of suppressing the automatic Contact.

Idempotent: deletes only what is present.
"""

import frappe

ORPHANS = (
	"Lead-source-reqd",
	"Opportunity-source-reqd",
	"Lead-source-label",
)


def execute():
	for name in ORPHANS:
		if not frappe.db.exists("Property Setter", name):
			continue
		frappe.delete_doc("Property Setter", name, ignore_permissions=True, force=True)

	# Property Setters are baked into the cached Meta; without this the stale
	# entries survive in cache until the next unrelated doctype change.
	for doctype in ("Lead", "Opportunity"):
		frappe.clear_cache(doctype=doctype)
