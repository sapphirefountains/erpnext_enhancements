"""Seed the credential catalogue (v1.386.0).

The tickets a fountain crew is actually asked for at a gate. Every one of these
was previously unrecordable: ``Training Certificate.completion`` is ``reqd: 1``,
so the app could only ever hold a certificate that originated in one of its own
courses, and a forklift ticket issued by somebody else had nowhere to live but a
filing cabinet.

Validity periods are the standard ones and are only ever a **hint** — the
credential controller uses them to fill an empty expiry date and never to
overwrite one somebody typed off the card in their hand. Where a card genuinely
does not expire (OSHA 10 has no federal expiry), the months are 0 and the record
reads Valid until somebody revokes it.

Insert-only and idempotent: a type that already exists is left exactly as it is,
including its validity, because somebody may have adjusted it to match how this
company actually renews things.
"""

import frappe

# (name, category, validity months, needs a photo, needs a number, usually issued by)
TYPES = (
	("OSHA 10", "Safety", 0, 1, 1, "OSHA-authorized trainer"),
	("OSHA 30", "Safety", 0, 1, 1, "OSHA-authorized trainer"),
	("First Aid / CPR", "Medical", 24, 1, 1, "American Red Cross / AHA"),
	("Forklift Operator", "Equipment", 36, 1, 1, "Employer or training provider"),
	("Aerial / Scissor Lift", "Equipment", 36, 1, 1, "Training provider"),
	("Respirator Fit Test", "Medical", 12, 1, 0, "Occupational health provider"),
	("CDL (Commercial Driver's License)", "License", 48, 1, 1, "State DMV"),
	("DOT Medical Card", "Medical", 24, 1, 1, "Certified medical examiner"),
	("Driver's License", "License", 48, 1, 1, "State DMV"),
	("Electrical License", "License", 24, 1, 1, "State licensing board"),
	("Confined Space Entry", "Safety", 36, 1, 0, "Training provider"),
	("Lockout / Tagout", "Safety", 36, 1, 0, "Employer"),
	("Pool / Spa Operator", "License", 60, 1, 1, "PHTA or state health dept"),
	("Water Treatment / Chemical Handling", "Safety", 36, 1, 0, "Training provider"),
	("Trenching & Excavation", "Safety", 36, 1, 0, "Training provider"),
)


def execute():
	if not frappe.db.exists("DocType", "Credential Type"):
		frappe.log_error(
			"Credential Type is not on this site, so the catalogue was not seeded. "
			"Check that 'HR Enhancements' reached frappe.local.app_modules "
			"(setup/module_map.py) before model sync.",
			"HR Enhancements",
		)
		return

	created = 0
	for name, category, months, needs_photo, needs_number, issuer in TYPES:
		if frappe.db.exists("Credential Type", name):
			continue
		frappe.get_doc(
			{
				"doctype": "Credential Type",
				"credential_name": name,
				"category": category,
				"default_validity_months": months,
				"requires_attachment": needs_photo,
				"requires_number": needs_number,
				"issuing_body_hint": issuer,
				"is_active": 1,
			}
		).insert(ignore_permissions=True)
		created += 1

	if created:
		print(f"[erpnext_enhancements] seeded {created} credential type(s)")
