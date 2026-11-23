# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Estimate's decisions that are not arithmetic: cost codes, phases, and what the quotation says.

Imports no frappe, so ``tests/test_estimating_rules.py`` runs it in the bench-free CI tier. The
controller (``doctype/estimate/estimate.py``) and the quotation builder (``api/estimating.py``) call
it; :mod:`estimate_math` does the money.

Things this is careful about:

* **A cost code is checked against the catalog, not a pattern.** ``quality/cost_codes.py`` is the one
  authority (WI-077): ``01 5433`` has a perfectly legal shape and is not a section. Whitespace runs and
  non-breaking spaces are collapsed here, in Python, because under MariaDB's PAD SPACE collation a
  trailing-space check in SQL is always false.
* **Fee and contingency codes are refused on a line.** ``FEE`` and ``01 2116`` are computed from the
  rates; keying them as a line would charge them twice.
* **Phases are the integers Task carries** (``Task.custom_phase_number`` is an Int). A design-build
  job's design stages are 110 to 140 inside 100 - the names match the Design Project template's four
  stages - and an event has no phases at all (phase 0).
* **The quotation shows one price per phase, never cost.** Concept flow step 4: "One price per phase,
  scope text per phase". A line with no phase (an event) is its own quotation line. Allowances are
  named under their phase with their amount, and "by others" lines become the exclusions list, so the
  customer reads what is not included.
"""

from __future__ import annotations

import re

from erpnext_enhancements.estimating import estimate_math as em
from erpnext_enhancements.quality import cost_codes

#: Default labels. The number is the key; an estimate can relabel a phase for its job (card 2).
DEFAULT_PHASE_LABELS = {
	100: "Design and planning",
	110: "Predesign",
	120: "Schematic design",
	130: "Design development",
	140: "Construction documents",
	200: "Groundwork and prep",
	300: "Rough-in",
	400: "Structural vessel",
	500: "Mechanical room",
	600: "Controls and electrical",
	700: "Features and finishes",
	800: "Maintenance",
}

_SPACES = re.compile(r"\s+")


def _catalog():
	rows = {}
	for row in cost_codes.COST_CODES:
		rows[row["cost_code"]] = {
			"code": row["cost_code"],
			"title": row["code_title"],
			"cost_type": row["cost_type"],
			"budget_category": row["budget_category"],
			"computed": bool(row.get("is_overhead")),
		}
	for row in cost_codes.RESOURCE_CODES:
		rows[row["resource_code"]] = {
			"code": row["resource_code"],
			"title": row["title"],
			"cost_type": row["cost_type"],
			"budget_category": row["budget_category"],
			"computed": bool(row.get("is_overhead")),
		}
	return rows


CATALOG = _catalog()


def normalize_code(raw):
	"""Collapse whitespace (including U+00A0) and upper-case a resource code: `` lab-pm `` -> ``LAB-PM``."""
	text = _SPACES.sub(" ", str(raw or "").replace(" ", " ")).strip()
	return text.upper()


def code_problem(code):
	"""Why a cost code cannot go on a line, or None. Blank is allowed until acceptance."""
	if not code:
		return None
	row = CATALOG.get(code)
	if row is None:
		if code in cost_codes.OLD_TO_NEW:
			return f"{code} is an old code; it is {cost_codes.OLD_TO_NEW[code]} now."
		return f"{code} is not in the cost code catalog."
	if row["computed"]:
		return f"{code} ({row['title']}) is computed from the rates, never keyed as a line."
	return None


def code_options():
	"""Every code a line may carry, for the editor's picker."""
	return [
		{"code": r["code"], "title": r["title"], "cost_type": r["cost_type"]}
		for r in sorted(CATALOG.values(), key=lambda r: (r["code"][0].isalpha(), r["code"]))
		if not r["computed"]
	]


def default_cost_type(code):
	row = CATALOG.get(code)
	return row["cost_type"] if row and row["cost_type"] in em.COST_TYPES else None


def phase_label(number, labels=None):
	number = em.phase_number(number)
	if labels and labels.get(number):
		return labels[number]
	return DEFAULT_PHASE_LABELS.get(number) or f"Phase {number}"


def missing_phases(existing, line_numbers):
	"""The phase numbers lines use (and their parents) that the estimate has no row for, sorted."""
	have = {em.phase_number(n) for n in existing}
	need = set()
	for n in line_numbers:
		need.update(em.phase_chain(n))
	return sorted(need - have)


def money(amount):
	return f"{float(amount or 0):,.2f}"


def _qty(value):
	value = float(value or 0)
	return f"{value:g}"


def unpriced_lines(lines, results):
	"""Lines that would go on the quotation at nothing: no cost and no sheet price."""
	return [
		line
		for line, result in zip(lines, results)
		if not line.get("by_others") and not float(result.get("amount") or 0)
	]


def quotation_rows(phases, lines, results):
	"""What the quotation lists, in phase order: ``[{"item_name", "description", "amount"}]``.

	``phases`` are the estimate's phase rows (number, label, scope text), ``lines`` its lines and
	``results`` the matching :func:`estimate_math.price_estimate` line results. A phased line's price
	joins its own phase (110, not 100); an unphased line is a row of its own. Returns
	``(rows, exclusions)`` where exclusions are the "by others" descriptions.
	"""
	by_number = {em.phase_number(p.get("phase_number")): p for p in phases or []}
	labels = {n: (p.get("label") or "").strip() for n, p in by_number.items()}
	grouped = {}
	loose = []
	exclusions = []
	for line, result in zip(lines, results):
		if line.get("by_others"):
			exclusions.append((line.get("description") or "").strip())
			continue
		number = em.phase_number(line.get("phase_number"))
		if not number:
			loose.append((line, result))
			continue
		grouped.setdefault(number, []).append((line, result))

	rows = []
	for number in sorted(grouped):
		members = grouped[number]
		amount = em.round_money(sum(r["amount"] for _, r in members))
		parts = []
		phase = by_number.get(number) or {}
		parent = em.top_phase(number)
		if parent != number:
			parts.append(f"Part of {parent} {phase_label(parent, labels)}.")
		scope = (phase.get("scope_text") or "").strip()
		if scope:
			parts.append(scope)
		allowances = [(l, r) for l, r in members if l.get("basis") == "Allowance"]
		for line, result in allowances:
			parts.append(
				f"Includes an allowance: {(line.get('description') or '').strip()}, {money(result['amount'])}."
			)
		rows.append(
			{
				"item_name": f"{number} {phase_label(number, labels)}"[:140],
				"description": "\n".join(parts) or f"{number} {phase_label(number, labels)}",
				"amount": amount,
			}
		)
	for line, result in loose:
		desc = (line.get("description") or "").strip() or "Line"
		detail = f"{_qty(line.get('qty'))} {(line.get('uom') or '').strip()}".strip()
		if line.get("basis") == "Allowance":
			detail += ". Allowance"
		rows.append(
			{
				"item_name": desc[:140],
				"description": f"{desc}\n{detail}",
				"amount": em.round_money(result["amount"]),
			}
		)
	return rows, [e for e in exclusions if e]


def exclusions_html(exclusions):
	"""The "not included" list appended to the quotation's terms. Escaped: descriptions are typed."""
	if not exclusions:
		return ""
	items = "".join(f"<li>{_escape(e)}</li>" for e in exclusions)
	return f"<p><strong>Not included (by others):</strong></p><ul>{items}</ul>"


def _escape(text):
	return (
		str(text)
		.replace("&", "&amp;")
		.replace("<", "&lt;")
		.replace(">", "&gt;")
		.replace('"', "&quot;")
		.replace("'", "&#39;")
	)
