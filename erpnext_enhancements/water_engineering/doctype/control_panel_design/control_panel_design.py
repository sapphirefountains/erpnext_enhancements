# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the Control Panel Design doctype (the "controller document").

Captures a fountain control panel's submittal (DOC-0126): user-interface screens,
pump control method, I/O points, interlocks, lighting, and the nameplate. On
validate it seeds the standard interlock checklist (DOC-0126/0127) when empty and
rolls up the lighting load + relay counts via the shared pure engine
(water_engineering.engine.controls) — the same math the fac_water_calc tool uses.
"""

import re

import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt

from erpnext_enhancements.water_engineering.engine import (
	control_transformer_va,
	electrical_load,
	lighting_sizing,
	motor_flc,
	service_main_breaker,
	total_connected_load,
)
from erpnext_enhancements.water_engineering.engine.controls import DEFAULT_INTERLOCKS, DEFAULT_IO_POINTS


class ControlPanelDesign(Document):
	def validate(self):
		self._seed_defaults()
		self._recompute_sizing()

	def _seed_defaults(self):
		"""Seed the standard interlock checklist + standard input list on a fresh
		panel (DOC-0126/0127); delete the rows a given job doesn't include."""
		if not self.get("interlocks"):
			for row in DEFAULT_INTERLOCKS:
				self.append("interlocks", dict(row))
		if not self.get("io_points"):
			for row in DEFAULT_IO_POINTS:
				self.append("io_points", dict(row))

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
		self._recompute_power()

	def _recompute_power(self):
		"""NEC panel/service sizing from the pump loads (430.24 connected load,
		430.62 feeder OCPD) + control-transformer VA (Art. 450). Computed by
		default; turn off ``power_autosize`` to enter the nameplate manually. These
		are code-based design aids for the engineer to confirm, not stamped calcs."""
		if not cint(self.get("power_autosize") if self.get("power_autosize") is not None else 1):
			return
		loads = _panel_loads(self)
		if any(ld.get("fla") or ld.get("hp") for ld in loads):
			self.amperage_to_panel = total_connected_load(loads).value or 0
			self.main_breaker_size_a = service_main_breaker(loads).value or 0
		xfmr = control_transformer_va(_control_loads(self))
		if xfmr.value:
			self.control_transformer_va = xfmr.value


# ------------------------------------------------------------- pure helpers


def _first_int(value, default=230):
	"""First integer in a voltage string ('208/230' -> 208); default if none."""
	m = re.search(r"\d+", str(value or ""))
	return int(m.group()) if m else default


def _panel_loads(doc):
	"""The panel's motor loads (one per Control Pump row) for the NEC calcs."""
	main_v = _first_int(doc.get("main_line_voltage"), 230)
	main_ph = cint(doc.get("phase")) or 1
	loads = []
	for p in doc.get("pumps") or []:
		loads.append({
			"label": p.function or p.part_no or "Pump",
			"is_motor": True,
			"fla": flt(p.get("fla_amps")),
			"hp": flt(p.hp),
			"phase": cint(p.phase) or main_ph,
			"voltage": _first_int(p.voltage, main_v),
			"qty": cint(p.qty) or 1,
			"control_method": p.control_method,
		})
	return loads


def _control_loads(doc):
	"""Control-circuit devices for the control-transformer VA: one contactor coil
	per pump, the lighting/solenoid relays, and the HMI/PLC if present."""
	pump_count = sum(cint(p.qty) or 1 for p in doc.get("pumps") or [])
	relays = cint(doc.get("lighting_relay_count")) + cint(doc.get("solenoid_relay_count"))
	loads = []
	if pump_count:
		loads.append({"device": "contactor", "qty": pump_count})
	if relays:
		loads.append({"device": "relay", "qty": relays})
	hw = doc.get("controller_hardware") or ""
	if any(tag in hw for tag in ("HMI", "Nextion", "LCD", "PLC")):
		loads.append({"device": "hmi", "qty": 1})
	return loads


def _row_fla(load):
	"""Per-unit FLA for a panel load: nameplate FLA if given, else the NEC table."""
	fla = flt(load.get("fla"))
	if fla > 0:
		return fla
	if load.get("is_motor") and load.get("hp"):
		return flt(motor_flc(load["hp"], load.get("phase", 1), load.get("voltage", 230)) or 0)
	return 0.0


def we_panel_schedule(doc):
	"""Panel-schedule rows + service totals for the Control Panel Design print
	format. The NEC math can't run in the Jinja print sandbox, so it is computed
	here (registered as a jinja method in hooks.py, like we_fitting_schedule)."""
	if isinstance(doc, str):
		doc = frappe.get_doc("Control Panel Design", doc)
	loads = _panel_loads(doc)
	circuits = []
	for idx, ld in enumerate(loads, start=1):
		fla = _row_fla(ld)
		circuits.append({
			"tag": f"M{idx}",
			"description": ld["label"],
			"load_a": round(fla, 2),
			"voltage": ld["voltage"],
			"phase": ld["phase"],
			"control_method": ld.get("control_method") or "",
			"breaker_a": electrical_load(fla).value if fla else None,
		})
	tcl = total_connected_load(loads) if loads else None
	smb = service_main_breaker(loads) if loads else None
	ctv = control_transformer_va(_control_loads(doc))
	return {
		"circuits": circuits,
		"connected_load_a": round(tcl.value, 1) if (tcl and tcl.value) else None,
		"main_breaker_a": smb.value if smb else None,
		"control_transformer_va": ctv.value,
		"service": {
			"main_line_voltage": doc.get("main_line_voltage"),
			"phase": doc.get("phase"),
			"frequency_hz": doc.get("frequency_hz"),
			"amperage_to_panel": doc.get("amperage_to_panel"),
			"main_breaker_size_a": doc.get("main_breaker_size_a"),
		},
	}


__all__ = ["ControlPanelDesign"]
