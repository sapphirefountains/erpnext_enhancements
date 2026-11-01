# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A nozzle in the catalog: its type, placement limits, provenance and performance table.

Manufacturers publish nozzle tables by pressure (PSI) or by head (ft); the designer works
in head. :func:`check_performance_points` fills whichever of the two a row is missing,
sorts the rows by depth over the nozzle and then by head, refuses a row the designer
cannot read, and warns when flow falls as head rises (a typo far more often than a real
nozzle). It imports nothing from Frappe beyond the translator, so the bench-free suite
(``tests/test_water_catalog.py``) runs it directly.
"""

from itertools import pairwise

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_enhancements.water_engineering.engine.constants import FT_PER_PSI


def _num(value):
	try:
		return float(value) if value not in (None, "") else None
	except (TypeError, ValueError):
		return None


def check_performance_points(points):
	"""Fill head/PSI, sort, and judge ``points`` (dicts or rows). Returns ``(sorted, errors, warnings)``.

	Rows are mutated in place: a row with PSI and no head gets ``head_ft = psi * FT_PER_PSI``
	and the reverse, so every row the engine reads carries both.
	"""
	errors, warnings = [], []

	def get(p, key):
		return _num(p.get(key) if isinstance(p, dict) else getattr(p, key, None))

	def put(p, key, value):
		if isinstance(p, dict):
			p[key] = value
		else:
			setattr(p, key, value)

	rows = list(points or [])
	for p in rows:
		head, psi = get(p, "head_ft"), get(p, "psi")
		if head is None and psi is not None:
			put(p, "head_ft", round(psi * FT_PER_PSI, 3))
		elif psi is None and head is not None:
			put(p, "psi", round(head / FT_PER_PSI, 3))
		if get(p, "head_ft") is None or get(p, "flow_gpm") is None:
			errors.append(_("Every performance row needs a flow and either a head or a pressure."))
			return rows, errors, warnings
		for key in ("head_ft", "psi", "flow_gpm", "display_height_ft", "display_diameter_ft", "throw_ft"):
			value = get(p, key)
			if value is not None and value < 0:
				errors.append(_("Performance values cannot be negative."))
				return rows, errors, warnings

	rows.sort(key=lambda p: (get(p, "depth_over_nozzle_in") or 0, get(p, "head_ft")))
	for prev, cur in pairwise(rows):
		if (get(prev, "depth_over_nozzle_in") or 0) != (get(cur, "depth_over_nozzle_in") or 0):
			continue
		if get(cur, "head_ft") > get(prev, "head_ft") and get(cur, "flow_gpm") < get(prev, "flow_gpm"):
			warnings.append(
				_("Flow falls from {0} to {1} GPM as head rises from {2} to {3} ft; check the row.").format(
					f"{get(prev, 'flow_gpm'):g}",
					f"{get(cur, 'flow_gpm'):g}",
					f"{get(prev, 'head_ft'):g}",
					f"{get(cur, 'head_ft'):g}",
				)
			)
	return rows, errors, warnings


class NozzleProfile(Document):
	def validate(self):
		lo, hi = _num(self.get("min_depth_in")), _num(self.get("max_depth_in"))
		if lo is not None and hi is not None and lo > hi:
			frappe.throw(_("Min water over nozzle cannot be more than the max."), title=_("Nozzle placement"))
		rows = self.get("performance") or []
		if not rows:
			return
		ordered, errors, warnings = check_performance_points(rows)
		if errors:
			frappe.throw("<br>".join(dict.fromkeys(errors)), title=_("Nozzle performance"))
		for idx, row in enumerate(ordered, start=1):
			row.idx = idx
		self.performance = ordered
		if warnings:
			frappe.msgprint("<br>".join(warnings), title=_("Nozzle performance"), indicator="orange")
