"""Parts explosion — component templates x effective module quantities.

Components are defined once per module on the product; a configuration's
module quantities (from pricing) scale them into the concrete parts list that
feeds the manufacturing BOM. Modules priced out of the configuration
(effective quantity 0) contribute no parts.

Note: rows are NOT aggregated by item_code here — the parts list mirrors the
module structure for the build sheet. BOM generation aggregates duplicates
(the 2-pole terminal appears in the relay, contactor AND timer modules)
because ERPNext rejects duplicate BOM rows.
"""


def explode_parts(components, module_qtys):
	"""Scale component template rows by their module's effective quantity."""
	out = []
	for comp in components or []:
		module_key = comp.get("module_key") or ""
		mult = module_qtys.get(module_key, 0)
		if mult <= 0:
			continue
		qty = float(comp.get("qty_per_module") or 1) * mult
		unit_cost = float(comp.get("unit_cost") or 0)
		out.append(
			{
				"module_key": module_key,
				"component_name": comp.get("component_name") or "",
				"item_code": comp.get("item_code") or "",
				"qty": qty,
				"uom": comp.get("uom") or "Nos",
				"unit_cost": unit_cost,
				"amount": qty * unit_cost,
				"supplier_name": comp.get("supplier_name") or "",
				"manufacturer": comp.get("manufacturer") or "",
				"manufacturer_part_no": comp.get("manufacturer_part_no") or "",
			}
		)
	return out
