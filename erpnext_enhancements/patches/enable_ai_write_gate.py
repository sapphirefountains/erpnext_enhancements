"""Switch the AI write gate on, with the exemptions Nik chose (v1.525.0, ADR 0016 §6).

Held since v1.524.0 while two blockers stood. ``run_python_code`` proved not read-only, so it
stays gated. ``confirm_action`` executed the redacted card, which v1.524.1 fixed. On
2026-09-23 Nik chose to carry the remaining load with exemptions:

- **Permanent exemptions** for the low-risk doctypes assistants actually write (``PERMANENT``).
  Their create/update executes without a card and is still logged in AI Action Log as Auto
  Approved. Money, stock, contracts, permissions, Items and Item Prices stay gated.
- **Time-boxed windows** for bulk jobs. That is the new ``exempt_until`` column, and a human
  opens a window on the settings page. This patch opens none.

What the patch does:

- Rows are appended to the Single's child table and inserted **row by row with db_insert**. The
  Single itself is never saved, because saving it runs its whole validate().
- Existing rows are left alone, so a list someone has already edited is not reset.
- A doctype missing from the site is skipped.
- The flag goes on only after the rows are in. On its own it would turn every Comment into a card.

A patch that raises aborts ``bench migrate``, which on this repo is the deploy, so every step is
guarded and a failure is logged rather than raised. If seeding fails the flag is **not** set.
The gate then stays off, which is today's behaviour, and the Error Log says why. Safe to run
twice.
"""

import frappe

SETTINGS = "ERPNext Enhancements Settings"
TABLE_FIELD = "ai_exempt_doctypes"

#: Chosen by Nik on 2026-09-23 from the 30-day audit log (the "broad low-risk list"), less
#: Sapphire Maintenance Profile. It was offered as maintenance catalog data and is not: it holds
#: a site's access codes, the Time Kiosk geofence coordinates and the default technician. So it
#: stays gated unless Nik adds it on the settings page.
PERMANENT = (
	"Comment",
	"ToDo",
	"Sapphire Maintenance Template",
	"Sapphire Maintenance Section",
	"Serial No",
	"Training Lesson",
)


def execute():
	if not _seed_exemptions():
		return
	try:
		frappe.db.set_single_value(SETTINGS, "ai_write_gating_enabled", 1)
		frappe.clear_document_cache(SETTINGS, SETTINGS)
	except Exception:
		frappe.log_error(title="enable_ai_write_gate: could not switch the gate on")
		print("enable_ai_write_gate: exemptions seeded, but the flag could not be set.")
		return
	print("enable_ai_write_gate: AI write gate is ON.")


def _seed_exemptions() -> bool:
	# No import of assistant_tools._gate for NEVER_EXEMPT: modules outside assistant_tools must
	# import cleanly without FAC (the FAC-optional tripwire). tests/test_ai_gate_switch_on pins
	# PERMANENT disjoint from NEVER_EXEMPT instead, and the gate ignores such a row regardless.
	try:
		settings = frappe.get_single(SETTINGS)
		present = {row.document_type for row in settings.get(TABLE_FIELD) or []}
		added = []
		for doctype in PERMANENT:
			if doctype in present:
				continue
			if not frappe.db.exists("DocType", doctype):
				continue
			row = settings.append(TABLE_FIELD, {"document_type": doctype})
			row.db_insert()
			added.append(doctype)
		print(f"enable_ai_write_gate: exempted {', '.join(added) or 'nothing new'}.")
		return True
	except Exception:
		try:
			frappe.db.rollback()
		except Exception:
			pass
		frappe.log_error(title="enable_ai_write_gate: could not seed the exemptions; gate left off")
		print("enable_ai_write_gate: could not seed the exemptions; the gate was left off.")
		return False
