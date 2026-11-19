# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""How an estimate line becomes a price. The one pricing engine for the Estimate.

Imports no frappe, deliberately: the bench-free CI tier is the only one that runs, so the money math
has to live where it can be tested there (``tests/test_estimate_math.py``). The Estimate form runs a
line-for-line twin of this file in the browser (``public/js/estimating/estimate_math.js``) so totals
move as you type; both are held to the same cases in ``tests/data/estimate_math_cases.json``, and the
server's numbers are the ones that get saved.

The model (policy A of the 2026-11-04 concept, with D15 of 2026-09-21)
-----------------------------------------------------------------------

For one line::

    base cost      = qty x unit cost
    use tax        = base cost x tax %                 material and equipment lines only
    cost           = base cost + use tax
    markup         = cost x line markup %              markup by cost type, from the rate card
    cost of work   = cost + markup
    contingency    = cost of work x contingency %
    insurance      = cost of work x insurance %
    fee            = cost of work x fee %              overhead and profit
    price          = cost of work + contingency + insurance + fee
    commission     = price x commission %              internal; never added to the price

Things this is careful about, including the ones that look like bugs:

* **The three add-ons are each a percent of the cost of work, added, not compounded.** That is the
  2026-09-21 sheet fix ("the contingency base moves to Cost of Work"): stacking them would charge
  insurance on contingency and fee on both, which is how the old worksheet drifted.
* **Commission comes out of the margin, not on top of the price.** Concept card 1 (accepted
  2026-11-04): commission is a selling cost the company pays, an internal line Finance and the owners
  see and no customer document shows. It is a percent of the price, so a design line can carry 0
  without a second rule ("no double commission on Design lines").
* **Use tax is a cost, not an invoice line.** In Utah a contractor improving real property consumes
  its materials (concept card 14), so the tax sits under the markup like any other cost. Labor,
  subcontract and expense lines never carry it.
* **Every amount is rounded to the cent where it is made, then summed.** So ``cost of work`` is
  exactly ``cost + markup`` and a price is exactly the sum of its parts, with no stray cent between
  a line and its phase. Rounding is banker's (half to even) after first rounding away float noise,
  the same as frappe's: ``2.10 x 2.45`` is ``5.1450000000000005`` in floating point and must come
  out ``5.14``.
* **Each percent resolves line, then phase, then the parent phase, then the estimate** (D15).
  Phases are integers, as on Task: a design-build job's design stages are 110 to 140 inside 100. A
  blank override means "inherit"; ``0`` is a real rate. That is why overrides are strings: a frappe
  Float column cannot hold "not set", it turns None into 0.
* **A line with a sheet price and no cost yet is priced at its sheet price** and counted as still
  needing a cost (cost is required at acceptance, allowed blank before - Nik, 2026-11-04).
  :func:`backfill_unit_cost` turns a sheet price back into the unit cost that reproduces it.
* **A line done "by others" stays on the estimate and is worth nothing** - it is scope the customer
  should see is excluded, not a free line.
"""

from __future__ import annotations

import math

COST_TYPES = ("Material", "Equipment", "Labor", "Subcontract", "Expense")
TAXED_COST_TYPES = ("Material", "Equipment")

#: The four D15 overheads, in the order they print.
OVERHEADS = ("contingency", "insurance", "fee", "commission")

#: The estimate-level field holding each cost type's default markup.
MARKUP_FIELDS = {
	"Material": "markup_material",
	"Equipment": "markup_equipment",
	"Labor": "markup_labor",
	"Subcontract": "markup_subcontract",
	"Expense": "markup_expense",
}


def round_money(value, precision=2):
	"""Banker's rounding to ``precision`` places, after rounding away float noise (frappe's way)."""
	value = float(value or 0)
	if not value:
		return 0.0
	sign = -1 if value < 0 else 1
	multiplier = 10**precision
	scaled = round(abs(value) * multiplier, 8)
	floor_num = math.floor(scaled)
	if scaled - floor_num == 0.5:
		scaled = floor_num if floor_num % 2 == 0 else floor_num + 1
	else:
		scaled = math.floor(scaled + 0.5)
	return sign * scaled / multiplier


def parse_rate(raw):
	"""An override as typed: ``""``/None -> None (inherit), ``"5"``/``"5%"``/``5`` -> 5.0.

	Raises ``ValueError`` on anything else, so the controller can name the field."""
	if raw is None:
		return None
	if isinstance(raw, (int, float)):
		return float(raw)
	text = str(raw).strip().replace("%", "").replace(",", "").strip()
	if not text:
		return None
	value = float(text)
	if math.isnan(value) or math.isinf(value):
		raise ValueError(raw)
	return value


def phase_number(value):
	"""A phase as the integer Task.custom_phase_number stores; 0 means "no phase" (an event)."""
	try:
		number = int(float(value or 0))
	except (TypeError, ValueError):
		return 0
	return max(number, 0)


def phase_chain(value):
	"""A phase and the phase it nests under: ``120`` -> ``[120, 100]``; ``300`` -> ``[300]``; ``0`` -> ``[]``.

	A number that is not a whole hundred nests under its hundred, which is how the design stages of a
	design-build job (110 to 140) sit inside 100 while staying a plain integer a Task can carry."""
	number = phase_number(value)
	if not number:
		return []
	parent = number // 100 * 100
	return [number, parent] if parent and parent != number else [number]


def top_phase(value):
	chain = phase_chain(value)
	return chain[-1] if chain else 0


def resolve(name, line, phases_by_number, estimate):
	"""One D15 rate for a line: its own override, its phase's, each parent phase's, the estimate's."""
	own = parse_rate(line.get(f"{name}_override"))
	if own is not None:
		return own
	for number in phase_chain(line.get("phase_number")):
		phase = phases_by_number.get(number)
		if phase:
			value = parse_rate(phase.get(f"{name}_override"))
			if value is not None:
				return value
	return float(estimate.get(f"{name}_pct") or 0)


def resolve_markup(line, estimate):
	own = parse_rate(line.get("markup_override"))
	if own is not None:
		return own
	field = MARKUP_FIELDS.get(line.get("cost_type") or "")
	return float(estimate.get(field) or 0) if field else 0.0


def price_line(
	qty,
	unit_cost,
	cost_type,
	*,
	markup_pct=0,
	tax_pct=0,
	contingency_pct=0,
	insurance_pct=0,
	fee_pct=0,
	commission_pct=0,
	sheet_price=0,
	by_others=False,
):
	"""Price one line. Returns a dict of every amount the Estimate stores for it."""
	qty = float(qty or 0)
	unit_cost = float(unit_cost or 0)
	sheet_price = float(sheet_price or 0)
	out = {
		"base_cost": 0.0,
		"tax_amount": 0.0,
		"cost_amount": 0.0,
		"markup_amount": 0.0,
		"cost_of_work": 0.0,
		"contingency_amount": 0.0,
		"insurance_amount": 0.0,
		"fee_amount": 0.0,
		"amount": 0.0,
		"unit_price": 0.0,
		"commission_amount": 0.0,
		"margin_amount": 0.0,
		"margin_pct": 0.0,
		"needs_cost": False,
		"priced": False,
	}
	if by_others:
		return out

	if not unit_cost:
		# No cost yet. A sheet price still prices the line; either way it is flagged.
		out["needs_cost"] = True
		out["amount"] = round_money(sheet_price)
		out["unit_price"] = round_money(out["amount"] / qty) if qty else 0.0
		out["commission_amount"] = round_money(out["amount"] * float(commission_pct or 0) / 100)
		return out

	base = round_money(qty * unit_cost)
	tax = round_money(base * float(tax_pct or 0) / 100) if cost_type in TAXED_COST_TYPES else 0.0
	cost = round_money(base + tax)
	markup = round_money(cost * float(markup_pct or 0) / 100)
	cow = round_money(cost + markup)
	contingency = round_money(cow * float(contingency_pct or 0) / 100)
	insurance = round_money(cow * float(insurance_pct or 0) / 100)
	fee = round_money(cow * float(fee_pct or 0) / 100)
	amount = round_money(cow + contingency + insurance + fee)
	margin = round_money(amount - cost)
	out.update(
		base_cost=base,
		tax_amount=tax,
		cost_amount=cost,
		markup_amount=markup,
		cost_of_work=cow,
		contingency_amount=contingency,
		insurance_amount=insurance,
		fee_amount=fee,
		amount=amount,
		unit_price=round_money(amount / qty) if qty else 0.0,
		commission_amount=round_money(amount * float(commission_pct or 0) / 100),
		margin_amount=margin,
		margin_pct=round_money(margin / amount * 100, 1) if amount else 0.0,
		priced=True,
	)
	return out


def price_factor(cost_type, markup_pct=0, tax_pct=0, contingency_pct=0, insurance_pct=0, fee_pct=0):
	"""Price per unit of base cost, before rounding: what :func:`price_line` multiplies by."""
	tax = float(tax_pct or 0) / 100 if cost_type in TAXED_COST_TYPES else 0.0
	add_ons = (float(contingency_pct or 0) + float(insurance_pct or 0) + float(fee_pct or 0)) / 100
	return (1 + tax) * (1 + float(markup_pct or 0) / 100) * (1 + add_ons)


def backfill_unit_cost(price, qty, cost_type, **rates):
	"""The unit cost that reproduces a sheet price under this line's rates (Nik, 2026-11-06).

	``cost = price / qty / factor``, then the cent either side of it if that reprices closer: every
	amount rounds to the cent on the way up, so the plain quotient can land a cent under the sheet."""
	qty = float(qty or 0) or 1.0
	factor = price_factor(cost_type, **rates)
	if not factor:
		return 0.0
	price = float(price or 0)
	first = round_money(price / qty / factor)
	candidates = [c for c in (first, round_money(first + 0.01), round_money(first - 0.01)) if c > 0]
	return min(candidates, key=lambda c: abs(price_line(qty, c, cost_type, **rates)["amount"] - price))


def line_rates(line, phases_by_number, estimate):
	"""Every effective rate one line prices at."""
	rates = {f"{name}_pct": resolve(name, line, phases_by_number, estimate) for name in OVERHEADS}
	rates["markup_pct"] = resolve_markup(line, estimate)
	rates["tax_pct"] = float(estimate.get("tax_pct") or 0)
	return rates


def price_estimate(estimate, phases, lines):
	"""Price a whole estimate.

	``estimate`` is a mapping with the ``*_pct`` and ``markup_*`` fields; ``phases`` rows carry
	``phase_number`` and optional ``*_override``; ``lines`` rows carry ``phase_number``, ``qty``,
	``unit_cost``, ``cost_type``, ``sheet_price``, ``by_others``, ``cost_code``, ``basis`` and optional
	``*_override``. Returns ``{"lines": [...], "phases": {"120": {...}}, "totals": {...}}`` with one
	result per input line, in order; phase totals are keyed by the number as a string ("0" for lines
	with no phase) so they survive a JSON round trip unchanged.
	"""
	phases_by_number = {phase_number(p.get("phase_number")): p for p in phases or []}
	results = []
	phase_totals = {}
	totals = {
		"amount": 0.0,
		"cost_amount": 0.0,
		"priced_amount": 0.0,
		"markup_amount": 0.0,
		"cost_of_work": 0.0,
		"tax_amount": 0.0,
		"contingency_amount": 0.0,
		"insurance_amount": 0.0,
		"fee_amount": 0.0,
		"commission_amount": 0.0,
		"line_count": 0,
		"missing_cost": 0,
		"missing_code": 0,
		"allowance_count": 0,
	}

	def bucket(number):
		return phase_totals.setdefault(
			number,
			{"amount": 0.0, "cost_amount": 0.0, "line_count": 0, "missing_cost": 0, "labor_lines": 0},
		)

	for line in lines or []:
		rates = line_rates(line, phases_by_number, estimate)
		by_others = bool(line.get("by_others"))
		result = price_line(
			line.get("qty"),
			line.get("unit_cost"),
			line.get("cost_type"),
			sheet_price=line.get("sheet_price"),
			by_others=by_others,
			**rates,
		)
		result.update(rates)
		results.append(result)
		if by_others:
			continue

		totals["line_count"] += 1
		for key in (
			"amount",
			"cost_amount",
			"markup_amount",
			"cost_of_work",
			"tax_amount",
			"contingency_amount",
			"insurance_amount",
			"fee_amount",
			"commission_amount",
		):
			totals[key] += result[key]
		if result["priced"]:
			totals["priced_amount"] += result["amount"]
		if result["needs_cost"]:
			totals["missing_cost"] += 1
		if not (line.get("cost_code") or "").strip():
			totals["missing_code"] += 1
		if line.get("basis") == "Allowance":
			totals["allowance_count"] += 1

		# A nested phase's lines count toward it and its parent; an unphased line toward "0".
		for number in phase_chain(line.get("phase_number")) or [0]:
			b = bucket(str(number))
			b["amount"] += result["amount"]
			b["cost_amount"] += result["cost_amount"]
			b["line_count"] += 1
			b["missing_cost"] += 1 if result["needs_cost"] else 0
			b["labor_lines"] += 1 if line.get("cost_type") == "Labor" else 0

	for key, value in list(totals.items()):
		if isinstance(value, float):
			totals[key] = round_money(value)
	for b in phase_totals.values():
		b["amount"] = round_money(b["amount"])
		b["cost_amount"] = round_money(b["cost_amount"])

	priced_cost = round_money(sum(r["cost_amount"] for r in results if r["priced"]))
	priced_commission = round_money(sum(r["commission_amount"] for r in results if r["priced"]))
	totals["margin_amount"] = round_money(totals["priced_amount"] - priced_cost)
	totals["margin_pct"] = (
		round_money(totals["margin_amount"] / totals["priced_amount"] * 100, 1)
		if totals["priced_amount"]
		else 0.0
	)
	totals["net_margin_amount"] = round_money(totals["margin_amount"] - priced_commission)
	totals["net_margin_pct"] = (
		round_money(totals["net_margin_amount"] / totals["priced_amount"] * 100, 1)
		if totals["priced_amount"]
		else 0.0
	)
	return {"lines": results, "phases": phase_totals, "totals": totals}
