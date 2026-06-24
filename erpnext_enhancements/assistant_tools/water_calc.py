"""water_calc — stateless water-feature hydraulic calculator (read-only).

Runs one Phase-1 calculation through the pure ``water_engineering.engine`` and
returns the result WITH its math — value, formula, ordered steps, source
citations, warnings, and any A/B/C options the user must still choose — so the
assistant can show its work and the user can decide. No database writes; the
exact same dispatch backs the desk wizard's run_calc endpoint.

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.
"""

from typing import Any

import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for

_CALCS = (
    "basin_volume",
    "turnover_gpm",
    "weir_flow",
    "nozzle_array_flow",
    "nozzle_flow",
    "size_pipe",
    "hazen_williams_loss",
    "pipe_pressure_rating",
    "pipe_pressure_check",
    "total_dynamic_head",
    "select_pump",
    "chlorinator_feed",
    "chemistry_targets",
    "ozone_sidestream",
    "manning_drain_flow",
    "size_drain",
    "surge_basin_volume",
    "calc_lighting",
    "calc_solenoid_relays",
    "suction_outlet_vgb",
    "npsh_available",
    "water_hammer",
    "electric_cost",
    "vertical_pipe",
    "open_channel_flow",
    "lazy_river_hp",
    "program_rules",
    "jet_trajectory",
    "lsi_index",
    "evaporation_rate",
    "make_up_water",
    "heating_load",
    "chemical_dose",
    "uv_dose",
    "filtration_area",
)


class WaterCalc(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "water_calc"  # must match module filename
        self.description = (
            "Run one fountain hydraulic calculation and return the result WITH the "
            "math behind it (formula, step-by-step working, source citations, "
            "warnings, and any A/B/C options to choose). Read-only. Pick 'calc' and "
            "pass its 'inputs': "
            "basin_volume {shape:rectangular|cylindrical, length_in, width_in, "
            "height_in | diameter_in}; turnover_gpm {volume_gal, turnovers_per_hr}; "
            "weir_flow {length_ft, head_in, contractions}; nozzle_array_flow "
            "{nozzle_count, gpm_each}; size_pipe {flow_gpm, length_ft, material, "
            "line:discharge|suction} (returns every size with velocity/status in "
            "options); hazen_williams_loss {flow_gpm, length_ft, id_in}; "
            "pipe_pressure_rating {material, nominal_size, temp_f} (max psi a pipe is rated "
            "for; PVC derates 73->110F to half); pipe_pressure_check {material, nominal_size, "
            "system_psi, temp_f} (is the pipe rated for the pressure? value = psi margin); "
            "total_dynamic_head {segments:[...], static_lift_ft}; select_pump "
            "{flow_gpm, tdh_ft, candidates:[...]}. Orifice nozzle_flow {nozzle_profile, "
            "supply_head_ft} computes Q=Cd*A*sqrt(2gh) from a Nozzle Profile's "
            "coefficients (or rated GPM @ head); without a profile use nozzle_array_flow "
            "with a rated GPM, or a weir. "
            "Water treatment: chlorinator_feed {volume_gal, chlorine_pct} (min "
            "liquid-chlorine feed gal/hr); chemistry_targets {water_type: outdoor|"
            "indoor|saltwater, cya_ppm?, free_cl_ppm?} (free Cl / pH / CYA ranges; "
            "pass cya_ppm/free_cl_ppm to check the DOC-0119 free-Cl floor = "
            "max(2.0, 7.5% of CYA)); ozone_sidestream "
            "{volume_gal, turnover_min, sidestream_pct, contact_tank, tank_qty, "
            "log_reduction:2-log|3-log} (ozone g/hr + contact-tank check). "
            "Drainage: manning_drain_flow {nominal_size, slope_in_per_ft} (gravity-"
            "drain GPM); size_drain {required_gpm, slope_in_per_ft} (smallest drain); "
            "surge_basin_volume {pool_area_sf, basin_area_sf, evap_in_day, precip_in, "
            "vortex_in, freeboard_in, overflow_in, swimmers}. Controls: calc_lighting "
            "{lights:[{qty,watts_each}], lighting_voltage, per_relay_watts} (watts/"
            "amps/relays); calc_solenoid_relays {valve_qty}. "
            "Safety gates: suction_outlet_vgb {system_gpm, cover_length_in, "
            "cover_width_in, open_area_fraction, outlets} (anti-entrapment drain-cover "
            "max safe GPM + dual-drain flag, ANSI/APSP-16); npsh_available "
            "{suction_static_ft (+flooded/-lift), suction_friction_ft, elevation_ft, "
            "water_temp_f, npshr_ft} (pump cavitation go/no-go); water_hammer "
            "{velocity_fps, length_ft, closure_time_s, material, static_psi, "
            "pipe_rating_psi} (Joukowsky surge pressure vs pipe rating). "
            "Workbook sheets: electric_cost {flow_gpm, tdh_ft, hours_per_day, "
            "rate_per_kwh, pump_qty} (annual pump operating $); vertical_pipe "
            "{head_in, id_in | flow_gpm} (standpipe discharge — give head+ID for "
            "flow, flow+ID for head, or flow+head to size the pipe); "
            "open_channel_flow {width_in, depth_in, slope, n} (runnel/rill GPM + "
            "Froude/Reynolds regime); lazy_river_hp {width_ft, depth_ft, length_ft, "
            "velocity_fps, n} (current-generation design HP); program_rules "
            "{surface_area_sf, pool_class:pool|spa} (bather load / skimmers / solar). "
            "Aesthetics: jet_trajectory {supply_head_ft|supply_psi or target_height_ft, "
            "nozzle_type:smooth|aerated} (realistic spray height <-> required pressure + "
            "basin setback). Treatment/thermal: lsi_index {ph, temp_f, "
            "calcium_hardness_ppm, total_alkalinity_ppm, tds_ppm} (water balance: "
            "scale vs corrode); evaporation_rate {surface_area_sf, water_temp_f, "
            "air_temp_f, rh_pct, activity} (ASHRAE gal/day); make_up_water "
            "{evaporation_gpd, splash_gpd, backwash_gpd, fill_window_min} (daily make-up "
            "+ auto-fill valve); heating_load {volume_gal, delta_f, cover:none|solid|"
            "liquid, wind, gas_rate} (heat-loss $/mo + heater BTU/hr); chemical_dose "
            "{volume_gal, chemical:ph_down|alkalinity_up|cya_up|salt_up, current, target} "
            "(dose to target); uv_dose {flow_gpm, target_red_mj} (UV design dose); "
            "filtration_area {design_gpm, media:sand|cartridge|de} (filter SF + backwash)."
        )
        self.category = "Water Engineering"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Water Feature Design"
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "calc": {
                    "type": "string",
                    "enum": list(_CALCS),
                    "description": "Which Phase-1 hydraulic calculation to run.",
                },
                "inputs": {
                    "type": "object",
                    "description": "Calc-specific inputs (see the tool description for each calc's keys).",
                },
            },
            "required": ["calc"],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        args = arguments or {}
        calc = (args.get("calc") or "").strip()
        if not calc:
            frappe.throw(_("A 'calc' name is required."), frappe.ValidationError)
        if not frappe.has_permission("Water Feature Design", "read"):
            frappe.throw(_("You do not have access to Water Feature Designs."), frappe.PermissionError)

        from erpnext_enhancements.water_engineering.api.water_design import _run_calc

        result = _run_calc(calc, args.get("inputs") or {})
        return {
            "success": True,
            "calc": calc,
            "result": result,
            "options": result.get("options") or [],
            "warnings": result.get("warnings") or [],
        }


__all__ = ["WaterCalc"]
