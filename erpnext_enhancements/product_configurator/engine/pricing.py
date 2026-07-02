"""Configuration pricing — the Pricing Calculator workbook, made generic.

Per module (one option row): ``unit_labor = flat_labor_cost or labor_hours *
labor_rate``; ``unit_cost = parts_cost + unit_labor``; ``unit_price =
unit_cost * (1 + markup_percent/100)``. The effective quantity is the row's
own quantity (1 for Base and the selected Choice) times the quantity of the
option named by ``qty_multiplier_option`` (the mounting-scales-with-e-stops
rule). "Additional cost" is a passthrough line added as-is — the workbook's
COST TOTAL adds it without markup (C7 = B7).

No rounding anywhere: the workbook's golden totals (1685.008 / 1512.979) are
full floats. Money rounding happens at the Currency-field/Item-Price layer.
"""

from .conditions import ConditionError, safe_eval_expr
from .partnumber import build_context, build_part_number, validate_selections


def price_configuration(product, selections, additional=None):
	"""Price one configuration of a product definition.

	``product``: dict with ``labor_rate``, ``markup_percent``,
	``part_number_template`` and ``options`` (list of option-row dicts).
	``selections``: {option_key: choice_code str | qty int}.
	``additional``: optional {"description": str, "cost": number} passthrough.

	Returns {part_number, context, lines, module_qtys, total_parts_cost,
	total_labor_hours, total_labor_cost, total_cost, total_price, warnings}.
	Raises ValueError when the selections are invalid for the product.
	"""
	options = product.get("options") or []
	labor_rate = float(product.get("labor_rate") or 0)
	markup = float(product.get("markup_percent") or 0)

	errors = validate_selections(options, selections)
	if errors:
		raise ValueError("; ".join(errors))

	ctx = build_context(options, selections)
	part_number = build_part_number(product.get("part_number_template") or "", ctx)

	warnings = []
	lines = []
	module_qtys = {}

	for row in options:
		kind = row.get("option_type")
		key = row.get("option_key")
		if kind == "Base":
			own_qty = 1
		elif kind == "Choice":
			if str(selections.get(key)) != str(row.get("choice_code")):
				continue
			own_qty = 1
		elif kind == "Quantity":
			own_qty = int(selections[key])
		else:
			warnings.append(f"Unknown option type {kind!r} on {key!r} — row skipped")
			continue

		multiplier = 1
		mult_key = (row.get("qty_multiplier_option") or "").strip()
		if mult_key:
			mult_value = ctx.get(mult_key)
			if isinstance(mult_value, bool) or not isinstance(mult_value, int):
				warnings.append(
					f"{row.get('option_label') or key}: multiplier option {mult_key!r} "
					"is not a quantity — ignored"
				)
			else:
				multiplier = mult_value

		eff_qty = own_qty * multiplier
		module_key = row.get("module_key") or key
		module_qtys[module_key] = eff_qty

		if row.get("warning_condition"):
			try:
				if safe_eval_expr(row["warning_condition"], ctx):
					warnings.append(row.get("warning_text") or row["warning_condition"])
			except ConditionError as e:
				warnings.append(str(e))

		if eff_qty <= 0:
			continue

		parts_cost = float(row.get("parts_cost") or 0)
		labor_hours = float(row.get("labor_hours") or 0)
		flat_labor = float(row.get("flat_labor_cost") or 0)
		unit_labor = flat_labor if flat_labor > 0 else labor_hours * labor_rate
		unit_cost = parts_cost + unit_labor
		unit_price = unit_cost * (1 + markup / 100.0)

		lines.append(
			{
				"option_key": key,
				"module_key": module_key,
				"module_label": _module_label(row),
				"qty": eff_qty,
				"unit_parts_cost": parts_cost,
				"unit_labor_hours": 0.0 if flat_labor > 0 else labor_hours,
				"unit_labor_cost": unit_labor,
				"unit_cost": unit_cost,
				"unit_price": unit_price,
				"line_cost": unit_cost * eff_qty,
				"line_price": unit_price * eff_qty,
			}
		)

	additional_cost = float((additional or {}).get("cost") or 0)
	if additional_cost:
		lines.append(
			{
				"option_key": "",
				"module_key": "additional",
				"module_label": (additional or {}).get("description") or "Additional Cost",
				"qty": 1,
				"unit_parts_cost": 0.0,
				"unit_labor_hours": 0.0,
				"unit_labor_cost": 0.0,
				"unit_cost": additional_cost,
				"unit_price": additional_cost,
				"line_cost": additional_cost,
				"line_price": additional_cost,
			}
		)

	return {
		"part_number": part_number,
		"context": ctx,
		"lines": lines,
		"module_qtys": module_qtys,
		"total_parts_cost": sum(ln["unit_parts_cost"] * ln["qty"] for ln in lines),
		"total_labor_hours": sum(ln["unit_labor_hours"] * ln["qty"] for ln in lines),
		"total_labor_cost": sum(ln["unit_labor_cost"] * ln["qty"] for ln in lines),
		"total_cost": sum(ln["line_cost"] for ln in lines),
		"total_price": sum(ln["line_price"] for ln in lines),
		"warnings": warnings,
	}


def _module_label(row):
	label = row.get("option_label") or row.get("module_key") or ""
	choice = row.get("choice_label")
	return f"{label} — {choice}" if choice else label
