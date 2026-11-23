"""One-time migration patch (post_model_sync; listed in patches.txt).

Before v1.758.0 a pump's curve lived on the Item itself, as the ``custom_pump_curve`` table
of Pump Curve Point rows. v1.758.0 adds the Pump Curve doctype (one record per impeller or
speed, with its source and who verified it), and the readers now prefer it. This copies each
Item's table into one Pump Curve record so a designer sees the same curve either way.

What it is careful about:

* **The Item's rows stay.** The readers still fall back to them, and deleting a person's data
  in a schema patch is not this patch's call. The Item form hides the legacy table only once
  it is empty.
* **An Item that already has a Pump Curve is skipped**, so the patch is safe twice and never
  duplicates a curve someone entered after the deploy.
* **A curve the new rules refuse is still copied.** Points typed onto the Item were never
  checked; a rising head or a repeated flow would make ``insert`` throw. The record is then
  inserted without the controller's checks and its notes say what to fix, because losing the
  curve is worse than flagging it.
* **It never raises.** A patch that raises aborts ``bench migrate``, which is the deploy. Each
  Item runs in its own savepoint; a failure logs and the patch moves on.

Prod had no Pump Curve Point rows on 2026-11-01, so this is expected to do nothing there.
"""

import frappe

LABEL = "Migrated from the Item's curve table"


def curve_from_rows(item_code, rows):
	"""The Pump Curve dict for one Item's legacy rows (``flow_gpm``/``head_ft`` + optional extras)."""
	return {
		"doctype": "Pump Curve",
		"item": item_code,
		"curve_label": LABEL,
		"source": "Digitized",
		"speed_pct": 100,
		"notes": "Copied from the Item's Pump Curve table by the v1.758.0 migration. "
		"Add the source URL and document, and verify it.",
		"points": [
			{
				"flow_gpm": r.get("flow_gpm"),
				"head_ft": r.get("head_ft"),
				"efficiency_pct": r.get("efficiency_pct"),
				"npshr_ft": r.get("npshr_ft"),
				"power_bhp": r.get("power_bhp"),
			}
			for r in rows
		],
	}


def execute():
	try:
		if not frappe.db.exists("DocType", "Pump Curve"):
			return
		rows = frappe.get_all(
			"Pump Curve Point",
			filters={"parenttype": "Item", "parentfield": "custom_pump_curve"},
			fields=["parent", "flow_gpm", "head_ft", "efficiency_pct", "npshr_ft", "power_bhp"],
			order_by="parent asc, idx asc",
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "migrate_item_pump_curves patch")
		return

	by_item = {}
	for r in rows:
		by_item.setdefault(r["parent"], []).append(r)

	created, flagged = [], []
	for item_code, item_rows in by_item.items():
		try:
			frappe.db.savepoint("migrate_pump_curve")
			if frappe.db.exists("Pump Curve", {"item": item_code}):
				continue
			try:
				frappe.get_doc(curve_from_rows(item_code, item_rows)).insert(ignore_permissions=True)
			except Exception as exc:
				frappe.db.rollback(save_point="migrate_pump_curve")
				doc = frappe.get_doc(curve_from_rows(item_code, item_rows))
				doc.notes = f"{doc.notes} The points failed the curve checks when copied: {exc}"
				doc.flags.ignore_validate = True
				doc.insert(ignore_permissions=True)
				flagged.append(item_code)
			created.append(item_code)
		except Exception:
			try:
				frappe.db.rollback(save_point="migrate_pump_curve")
			except Exception:
				pass
			frappe.log_error(frappe.get_traceback(), f"migrate_item_pump_curves patch: {item_code}")
	frappe.db.commit()
	if created:
		print(f"migrate_item_pump_curves: {len(created)} Pump Curve(s) created; to check: {flagged}")
