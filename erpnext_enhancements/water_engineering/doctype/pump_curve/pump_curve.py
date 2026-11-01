# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One performance curve of a pump Item (one impeller / one speed).

The Fountain Designer solves the operating point by intersecting this curve with the
system curve, so the points have to describe a function the solver can use: flow
strictly rising, head never rising. A curve that breaks either rule is refused on save,
because the solver would otherwise find no duty point, or two. A gap in the points
(no shutoff point, a long jump between two flows) only warns: interpolation still
works, it is just less certain there.

:func:`check_points` holds every rule and needs nothing from Frappe but the translator, so the bench-free
suite (``tests/test_water_catalog.py``) runs it directly.
"""

from itertools import pairwise

import frappe
from frappe import _
from frappe.model.document import Document

#: A jump between two neighboring flows wider than this share of the curve's flow span (and
#: more than twice the table's smallest step) is reported as a gap. A typical datasheet table
#: has 6-12 evenly spaced points.
GAP_SHARE = 0.25


def _num(value):
	try:
		return float(value) if value not in (None, "") else None
	except (TypeError, ValueError):
		return None


def check_points(points):
	"""Sort ``points`` (dicts or rows with ``flow_gpm`` / ``head_ft``) by flow and judge them.

	Returns ``(sorted_points, errors, warnings)``; the lists hold plain sentences. Errors are
	what makes the curve unusable to the solver; warnings are what makes it less certain.
	"""
	errors, warnings = [], []

	def get(p, key):
		return _num(p.get(key) if isinstance(p, dict) else getattr(p, key, None))

	usable = [p for p in points or [] if get(p, "flow_gpm") is not None and get(p, "head_ft") is not None]
	if len(usable) < len(points or []):
		errors.append(_("Every curve point needs both a flow and a head."))
	ordered = sorted(usable, key=lambda p: get(p, "flow_gpm"))
	if len(ordered) < 2:
		errors.append(_("A pump curve needs at least two points."))
		return ordered, errors, warnings

	for p in ordered:
		if get(p, "flow_gpm") < 0 or get(p, "head_ft") < 0:
			errors.append(_("Flow and head cannot be negative."))
			break
	for key, label in (("efficiency_pct", _("Efficiency")),):
		bad = [get(p, key) for p in ordered if get(p, key) is not None and not 0 <= get(p, key) <= 100]
		if bad:
			errors.append(_("{0} must be between 0 and 100 %.").format(label))
	for key, label in (("npshr_ft", _("NPSHr")), ("power_bhp", _("Power"))):
		if any(get(p, key) is not None and get(p, key) < 0 for p in ordered):
			errors.append(_("{0} cannot be negative.").format(label))

	for prev, cur in pairwise(ordered):
		f0, f1 = get(prev, "flow_gpm"), get(cur, "flow_gpm")
		h0, h1 = get(prev, "head_ft"), get(cur, "head_ft")
		if f1 == f0:
			errors.append(
				_("Two points share the flow {0} GPM; each flow may appear once.").format(f"{f1:g}")
			)
		elif h1 > h0:
			errors.append(
				_(
					"Head rises from {0} ft to {1} ft between {2} and {3} GPM. Head must fall as flow rises."
				).format(f"{h0:g}", f"{h1:g}", f"{f0:g}", f"{f1:g}")
			)

	first, last = get(ordered[0], "flow_gpm"), get(ordered[-1], "flow_gpm")
	if first > 0:
		warnings.append(
			_("The curve starts at {0} GPM, not at shutoff (0 GPM); head below that flow is unknown.").format(
				f"{first:g}"
			)
		)
	span = last - first
	steps = [get(cur, "flow_gpm") - get(prev, "flow_gpm") for prev, cur in pairwise(ordered)]
	smallest = min((s for s in steps if s > 0), default=0)
	if span > 0:
		for prev, cur in pairwise(ordered):
			f0, f1 = get(prev, "flow_gpm"), get(cur, "flow_gpm")
			# Wide against the whole curve AND against the table's own spacing: an evenly
			# spaced four-point table is sparse, not gappy.
			if f1 - f0 > GAP_SHARE * span and f1 - f0 > 2 * smallest:
				warnings.append(
					_("Gap between {0} and {1} GPM; add a point there if the datasheet has one.").format(
						f"{f0:g}", f"{f1:g}"
					)
				)
	return ordered, errors, warnings


class PumpCurve(Document):
	def validate(self):
		ordered, errors, warnings = check_points(self.get("points") or [])
		if errors:
			frappe.throw("<br>".join(dict.fromkeys(errors)), title=_("Pump curve points"))
		# Keep the grid in flow order, so the table reads like the datasheet.
		for idx, row in enumerate(ordered, start=1):
			row.idx = idx
		self.points = ordered
		if not _num(self.max_flow_gpm):
			self.max_flow_gpm = _num(ordered[-1].flow_gpm)
		if not _num(self.shutoff_head_ft) and _num(ordered[0].flow_gpm) == 0:
			self.shutoff_head_ft = _num(ordered[0].head_ft)
		if not _num(self.speed_pct):
			self.speed_pct = 100
		if warnings:
			frappe.msgprint("<br>".join(warnings), title=_("Pump curve"), indicator="orange")
