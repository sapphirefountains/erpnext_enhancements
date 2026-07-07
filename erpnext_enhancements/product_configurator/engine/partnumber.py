"""Part-number construction and selection validation.

A configurable product's options define a context of named values (Quantity
options -> int, Choice options -> the chosen row's ``choice_code`` string) that
feeds both the part-number template (``PDT-0040-{mounting}-{estop_qty}-...``)
and build-step conditions.

Deliberate divergence from the source pricing workbook: the workbook's
configuration-number formula multiplies the mounting digit by the e-stop
quantity (a spreadsheet bug — Flush with 2 e-stops would read "2" = Surface).
The build-instructions decode table is authoritative: a Choice digit is always
the raw ``choice_code``. Only the mounting *cost* scales with e-stop quantity
(see pricing.py ``qty_multiplier_option``).
"""

from .conditions import ConditionError, render_text


def build_context(options, selections):
	"""Map option keys to their selected values for templates and conditions.

	Quantity -> int, Choice -> ``choice_code`` string plus a ``<key>_label``
	companion. Unselected/unknown keys are simply absent (template rendering
	then raises, which :func:`validate_selections` reports first).
	"""
	ctx = {}
	for row in options:
		key = row.get("option_key")
		kind = row.get("option_type")
		if kind == "Quantity":
			if key in selections:
				ctx[key] = int(selections[key])
		elif kind == "Choice":
			code = selections.get(key)
			if code is not None and str(code) == str(row.get("choice_code")):
				ctx[key] = str(code)
				ctx[key + "_label"] = row.get("choice_label") or str(code)
	return ctx


def build_part_number(template, ctx):
	"""Render the part-number template; raises ConditionError on unknown tokens."""
	rendered = render_text(template, ctx)
	if "{" in rendered or not rendered.strip():
		raise ConditionError(f"Part-number template {template!r} did not fully resolve")
	return rendered.strip()


def validate_selections(options, selections):
	"""Return a list of human-readable problems (empty list = valid).

	Rules: every Choice group has exactly one selected code that matches one of
	its rows; every Quantity option has an integer within [min_qty, max_qty].
	"""
	errors = []
	seen_choice_keys = {}
	seen_qty_keys = set()

	# Defense in depth (the product form validates this too): a key shared by
	# anything other than Choice rows would price multiple modules silently.
	types_by_key = {}
	for row in options:
		types_by_key.setdefault(row.get("option_key"), []).append(row.get("option_type"))
	for key, types in types_by_key.items():
		if len(types) > 1 and any(t != "Choice" for t in types):
			errors.append(
				f"Option key {key!r} is reused by {len(types)} rows — only Choice "
				"rows may share a key"
			)

	for row in options:
		key = row.get("option_key")
		kind = row.get("option_type")
		label = row.get("option_label") or key
		if kind == "Choice":
			seen_choice_keys.setdefault(key, {"label": label, "codes": []})
			seen_choice_keys[key]["codes"].append(str(row.get("choice_code")))
		elif kind == "Quantity":
			if key in seen_qty_keys:
				continue
			seen_qty_keys.add(key)
			raw = selections.get(key)
			try:
				qty = int(raw)
			except (TypeError, ValueError):
				errors.append(f"{label}: quantity {raw!r} is not a whole number")
				continue
			min_qty = int(row.get("min_qty") or 0)
			max_qty = int(row.get("max_qty") or 0)
			if max_qty and not (min_qty <= qty <= max_qty):
				errors.append(f"{label}: quantity {qty} is outside {min_qty}–{max_qty}")
			elif qty < min_qty:
				errors.append(f"{label}: quantity {qty} is below the minimum {min_qty}")

	for key, group in seen_choice_keys.items():
		code = selections.get(key)
		if code is None or str(code) not in group["codes"]:
			errors.append(f"{group['label']}: select one of the available choices")

	return errors
