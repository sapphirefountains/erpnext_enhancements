# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the Control Panel Design doctype (the "controller document").

Captures a fountain control panel's submittal (DOC-0126): user-interface screens,
pump control method, I/O points, interlocks, lighting, and the nameplate. On
validate it seeds the standard interlock checklist (DOC-0126/0127) when empty and
rolls up the lighting load + relay counts via the shared pure engine
(water_engineering.engine.controls) — the same math the fac_water_calc tool uses.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt

from erpnext_enhancements.water_engineering.engine import lighting_sizing
from erpnext_enhancements.water_engineering.engine.controls import DEFAULT_INTERLOCKS


class ControlPanelDesign(Document):
	def validate(self):
		self._seed_default_interlocks()
		self._recompute_sizing()

	def _seed_default_interlocks(self):
		"""Seed the standard interlock checklist on a fresh panel (DOC-0126/0127)."""
		if not self.get("interlocks"):
			for row in DEFAULT_INTERLOCKS:
				self.append("interlocks", dict(row))

	def _recompute_sizing(self):
		lights = [
			{"qty": cint(li.qty), "watts_each": flt(li.watts_each)}
			for li in self.get("lights") or []
		]
		sizing = lighting_sizing(lights, flt(self.lighting_voltage) or 12, flt(self.per_relay_watts) or 60)
		self.lighting_total_watts = sizing["total_watts"]
		self.lighting_current_a = sizing["current_a"]
		self.lighting_relay_count = sizing["relay_count"]
		# One solid-state relay per solenoid valve (DOC-0126).
		self.solenoid_relay_count = cint(self.solenoid_valve_qty)


__all__ = ["ControlPanelDesign"]
