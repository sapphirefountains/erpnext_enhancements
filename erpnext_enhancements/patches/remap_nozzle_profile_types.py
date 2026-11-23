"""One-time migration patch (post_model_sync; listed in patches.txt).

v1.758.0 replaced Nozzle Profile's ``nozzle_type`` options with the Fountain Designer's
families (Orifice Jet, Foam / Aerating, Laminar, Cascade / Fan, Geyser, Bubbler, Spray
Ring, Water Screen, Sheet Fall, Other). The option list moves with the doctype JSON; this
carries the stored rows over, because Frappe validates a Select's value on every save and a
profile left on an old name would refuse to save the next time anyone touched it. Prod had
five rows on 2026-11-01, one each of Smooth Bore, Aerating, Geyser, Spray and Cascade.

Written with ``db.set_value`` (no validation, no ``modified`` bump: a renamed option is not
an edit to the nozzle). It logs and never raises, because a patch that raises aborts
``bench migrate``, which is the deploy. Safe twice.
"""

import frappe

OLD_TO_NEW = {
	"Smooth Bore": "Orifice Jet",
	"Jet": "Orifice Jet",
	"Aerating": "Foam / Aerating",
	"Foam": "Foam / Aerating",
	"Spray": "Spray Ring",
	"Cascade": "Cascade / Fan",
}


def execute():
	try:
		if not frappe.db.exists("DocType", "Nozzle Profile"):
			return
		rows = frappe.get_all(
			"Nozzle Profile",
			filters={"nozzle_type": ["in", list(OLD_TO_NEW)]},
			fields=["name", "nozzle_type"],
		)
		for row in rows:
			frappe.db.set_value(
				"Nozzle Profile", row.name, "nozzle_type", OLD_TO_NEW[row.nozzle_type], update_modified=False
			)
		frappe.db.commit()
		if rows:
			print(f"remap_nozzle_profile_types: moved {len(rows)} Nozzle Profile(s) to the new type names")
	except Exception:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "remap_nozzle_profile_types patch")
