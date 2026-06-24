"""Whitelisted desk endpoints for the Water Engineering wizard + form JS.

Thin adapters over the pure ``water_engineering.engine`` — the SAME functions
the FAC MCP tools call — so the desk and the AI produce byte-identical math. The
write path (``save_inputs``) and the state reader (``get_design_state``) expose
core helpers (``_save_design`` / ``design_state``) the MCP tools reuse, keeping
one implementation for both surfaces.

Permission model: every endpoint gates on the ``Water Feature Design`` doctype
(read for calculators/readers, write for saves) and ``doc.save()`` enforces
document-level permission for the actual mutation.
"""

import json

import frappe
from frappe import _

from erpnext_enhancements.water_engineering.engine import (
    basin_volume,
    calc_lighting,
    calc_solenoid_relays,
    chemical_dose,
    chemistry_targets,
    chlorinator_feed,
    electric_cost,
    evaporation_rate,
    filtration_area,
    hazen_williams_loss,
    heating_load,
    jet_trajectory,
    lazy_river_hp,
    lsi_index,
    make_up_water,
    manning_drain_flow,
    nozzle_array_flow,
    nozzle_flow,
    npsh_available,
    open_channel_flow,
    ozone_sidestream,
    program_rules,
    run_spine,
    select_pump,
    size_drain,
    size_pipe,
    suction_outlet_vgb,
    surge_basin_volume,
    total_dynamic_head,
    turnover_gpm,
    uv_dose,
    vertical_pipe,
    water_hammer,
    weir_flow,
)

DESIGN_DOCTYPE = "Water Feature Design"
CONTROL_DOCTYPE = "Control Panel Design"

# Parent fields a desk/AI caller may set; the read-only rollups are never writable.
EDITABLE_DESIGN_FIELDS = frozenset(
    {
        "project",
        "serial_no",
        "design_title",
        "status",
        "turnover_per_hr",
        "hazen_williams_c",
        "pipe_material",
        "static_lift_ft",
    }
)

# Child tables a caller may replace wholesale.
EDITABLE_CHILD_TABLES = ("basins", "features", "pipe_segments", "pumps", "electrical_loads")

# Parent input fields the live form preview may set (the editable design fields
# plus the chemistry/drainage inputs) — read-only rollups are never accepted.
PREVIEW_PARENT_FIELDS = EDITABLE_DESIGN_FIELDS | {
    "chem_water_type",
    "chem_chlorine_pct",
    "drain_nominal_size",
    "drain_slope_in_per_ft",
    "surge_pool_area_sf",
    "surge_basin_area_sf",
}


# ----------------------------------------------------------------- helpers


def _parse(value):
    """Accept a dict or a JSON string (frappe.call sends objects as JSON)."""
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return {}
    return value


def _require(ptype):
    if not frappe.has_permission(DESIGN_DOCTYPE, ptype):
        frappe.throw(
            _("You do not have {0} permission for {1}.").format(ptype, DESIGN_DOCTYPE),
            frappe.PermissionError,
        )


def nozzle_profile_params(profile_name):
    """Resolve a Nozzle Profile docname to the orifice/rated params the pure
    engine's nozzle_flow needs. Empty dict if the profile is missing."""
    if not profile_name or not frappe.db.exists("Nozzle Profile", profile_name):
        return {}
    p = (
        frappe.db.get_value(
            "Nozzle Profile",
            profile_name,
            ["discharge_coefficient", "orifice_diameter_in", "orifice_area_in2", "rated_gpm", "rated_head_ft"],
            as_dict=True,
        )
        or {}
    )
    return {
        "cd": p.get("discharge_coefficient"),
        "orifice_diameter_in": p.get("orifice_diameter_in"),
        "orifice_area_in2": p.get("orifice_area_in2"),
        "rated_gpm": p.get("rated_gpm"),
        "rated_head_ft": p.get("rated_head_ft"),
    }


def pump_curves(item_codes):
    """{item_code: [{flow_gpm, head_ft}, ...]} performance-curve points for the
    given pump Items. Empty if the Pump Curve Point table isn't migrated yet."""
    codes = [c for c in (item_codes or []) if c]
    if not codes:
        return {}
    try:
        rows = frappe.get_all(
            "Pump Curve Point",
            filters={"parent": ["in", codes], "parenttype": "Item", "parentfield": "custom_pump_curve"},
            fields=["parent", "flow_gpm", "head_ft"],
            order_by="parent asc, idx asc",
        )
    except Exception:
        return {}
    out = {}
    for r in rows:
        out.setdefault(r["parent"], []).append({"flow_gpm": r["flow_gpm"], "head_ft": r["head_ft"]})
    return out


# ----------------------------------------------------------- stateless calc


def _run_calc(calc, inputs):
    """Dispatch one engine calculation; return its CalcResult dict."""
    i = inputs or {}
    if calc == "basin_volume":
        r = basin_volume(
            i.get("shape", "rectangular"),
            length_in=i.get("length_in", 0),
            width_in=i.get("width_in", 0),
            height_in=i.get("height_in", 0),
            diameter_in=i.get("diameter_in", 0),
        )
    elif calc == "turnover_gpm":
        r = turnover_gpm(i.get("volume_gal", 0), i.get("turnovers_per_hr", 2))
    elif calc == "weir_flow":
        r = weir_flow(i.get("length_ft", 0), i.get("head_in", 0), i.get("contractions", 2))
    elif calc == "nozzle_array_flow":
        r = nozzle_array_flow(i.get("nozzle_count", 0), i.get("gpm_each", 0))
    elif calc == "nozzle_flow":
        params = nozzle_profile_params(i.get("nozzle_profile"))
        r = nozzle_flow(
            i.get("supply_head_ft", i.get("head_ft", 0)),
            cd=i.get("cd", params.get("cd")),
            orifice_area_in2=i.get("orifice_area_in2", params.get("orifice_area_in2")),
            orifice_diameter_in=i.get("orifice_diameter_in", params.get("orifice_diameter_in")),
            rated_gpm=i.get("rated_gpm", params.get("rated_gpm")),
            rated_head_ft=i.get("rated_head_ft", params.get("rated_head_ft")),
            nozzle_profile=i.get("nozzle_profile", ""),
        )
    elif calc == "size_pipe":
        r = size_pipe(
            i.get("flow_gpm", 0),
            i.get("length_ft", 0),
            i.get("material", "SCH40 PVC"),
            i.get("line", "discharge"),
            c=i.get("c") or 130,
        )
    elif calc == "hazen_williams_loss":
        r = hazen_williams_loss(i.get("flow_gpm", 0), i.get("length_ft", 0), i.get("id_in", 0), i.get("c") or 130)
    elif calc == "total_dynamic_head":
        r = total_dynamic_head(
            i.get("segments", []), static_lift_ft=i.get("static_lift_ft", 0), c=i.get("c") or 130
        )
    elif calc == "select_pump":
        r = select_pump(i.get("flow_gpm", 0), i.get("tdh_ft", 0), i.get("candidates"))
    elif calc == "chlorinator_feed":
        r = chlorinator_feed(i.get("volume_gal", 0), i.get("chlorine_pct", 10))
    elif calc == "chemistry_targets":
        r = chemistry_targets(i.get("water_type", "outdoor"))
    elif calc == "ozone_sidestream":
        r = ozone_sidestream(
            i.get("volume_gal", 0),
            i.get("turnover_min", 0),
            i.get("sidestream_pct", 0.25),
            i.get("contact_tank", "CNT120"),
            i.get("tank_qty", 1),
            i.get("log_reduction", "2-log"),
        )
    elif calc == "manning_drain_flow":
        r = manning_drain_flow(i.get("nominal_size", '3"'), i.get("slope_in_per_ft", 0.25))
    elif calc == "size_drain":
        r = size_drain(i.get("required_gpm", 0), i.get("slope_in_per_ft", 0.25))
    elif calc == "surge_basin_volume":
        r = surge_basin_volume(
            i.get("pool_area_sf", 0),
            i.get("basin_area_sf", 0),
            evap_in_day=i.get("evap_in_day", 0.25),
            precip_in=i.get("precip_in", 1.0),
            vortex_in=i.get("vortex_in", 12),
            freeboard_in=i.get("freeboard_in", 3),
            overflow_in=i.get("overflow_in", 3),
            swimmers=i.get("swimmers", 0),
        )
    elif calc == "calc_lighting":
        r = calc_lighting(i.get("lights", []), i.get("lighting_voltage", 12), i.get("per_relay_watts", 60))
    elif calc == "calc_solenoid_relays":
        r = calc_solenoid_relays(i.get("valve_qty", 0))
    elif calc == "suction_outlet_vgb":
        r = suction_outlet_vgb(
            i.get("system_gpm", 0),
            i.get("cover_length_in", 0),
            i.get("cover_width_in", 0),
            i.get("open_area_fraction", 0),
            outlets=i.get("outlets", 1),
            vmax_fps=i.get("vmax_fps", 1.5),
        )
    elif calc == "npsh_available":
        r = npsh_available(
            i.get("suction_static_ft", 0),
            i.get("suction_friction_ft", 0),
            elevation_ft=i.get("elevation_ft", 0),
            water_temp_f=i.get("water_temp_f", 70),
            npshr_ft=i.get("npshr_ft", 0),
            margin_ft=i.get("margin_ft", 3.0),
        )
    elif calc == "water_hammer":
        r = water_hammer(
            i.get("velocity_fps", 0),
            i.get("length_ft", 0),
            closure_time_s=i.get("closure_time_s", 0),
            material=i.get("material", "SCH40 PVC"),
            wave_speed_fps=i.get("wave_speed_fps", 0),
            static_psi=i.get("static_psi", 0),
            pipe_rating_psi=i.get("pipe_rating_psi", 0),
        )
    elif calc == "electric_cost":
        r = electric_cost(
            i.get("flow_gpm", 0),
            i.get("tdh_ft", 0),
            hours_per_day=i.get("hours_per_day", 6),
            rate_per_kwh=i.get("rate_per_kwh", 0.17),
            sg=i.get("sg", 1.0),
            pump_eff=i.get("pump_eff", 0.70),
            motor_eff=i.get("motor_eff", 0.90),
            pump_qty=i.get("pump_qty", 1),
        )
    elif calc == "vertical_pipe":
        r = vertical_pipe(i.get("head_in", 0), i.get("id_in", 0), i.get("flow_gpm", 0))
    elif calc == "open_channel_flow":
        r = open_channel_flow(i.get("width_in", 0), i.get("depth_in", 0), i.get("slope", 0), i.get("n", 0.015))
    elif calc == "lazy_river_hp":
        r = lazy_river_hp(
            i.get("width_ft", 0),
            i.get("depth_ft", 0),
            i.get("length_ft", 0),
            i.get("velocity_fps", 5.0),
            i.get("n", 0.0155),
            i.get("sg", 1.0),
            i.get("safety_factor", 2.0),
        )
    elif calc == "program_rules":
        r = program_rules(i.get("surface_area_sf", 0), i.get("pool_class", "pool"))
    elif calc == "jet_trajectory":
        r = jet_trajectory(
            target_height_ft=i.get("target_height_ft", 0),
            supply_head_ft=i.get("supply_head_ft", 0),
            supply_psi=i.get("supply_psi", 0),
            nozzle_type=i.get("nozzle_type", "smooth"),
        )
    elif calc == "lsi_index":
        r = lsi_index(
            i.get("ph", 0),
            i.get("temp_f", 0),
            i.get("calcium_hardness_ppm", 0),
            i.get("total_alkalinity_ppm", 0),
            i.get("tds_ppm", 1000),
        )
    elif calc == "evaporation_rate":
        r = evaporation_rate(
            i.get("surface_area_sf", 0),
            i.get("water_temp_f", 0),
            i.get("air_temp_f", 0),
            i.get("rh_pct", 0),
            i.get("activity", "residential"),
        )
    elif calc == "make_up_water":
        r = make_up_water(
            i.get("evaporation_gpd", 0),
            i.get("splash_gpd", 0),
            i.get("backwash_gpd", 0),
            i.get("fill_window_min", 20),
        )
    elif calc == "heating_load":
        r = heating_load(
            i.get("volume_gal", 0),
            i.get("delta_f", 0),
            cover=i.get("cover", "none"),
            wind=i.get("wind", True),
            gas_rate=i.get("gas_rate", 1.40),
            heater_eff=i.get("heater_eff", 0.92),
            warmup_hours=i.get("warmup_hours", 24),
        )
    elif calc == "chemical_dose":
        r = chemical_dose(i.get("volume_gal", 0), i.get("chemical", ""), i.get("current", 0), i.get("target", 0))
    elif calc == "uv_dose":
        r = uv_dose(i.get("flow_gpm", 0), i.get("target_red_mj", 60))
    elif calc == "filtration_area":
        r = filtration_area(i.get("design_gpm", 0), i.get("media", "sand"), i.get("rate_gpm_sf", 0))
    else:
        frappe.throw(_("Unknown calculation: {0}").format(calc), frappe.ValidationError)
    return r.to_dict()


@frappe.whitelist()
def run_calc(calc, inputs=None):
    """Stateless calculator — runs one engine calc and returns the math envelope."""
    _require("read")
    return _run_calc(calc, _parse(inputs))


@frappe.whitelist()
def run_design_spine(inputs=None):
    """Run the whole Phase-1 spine over an ad-hoc input dict (no persistence)."""
    _require("read")
    return run_spine(_parse(inputs))


# ----------------------------------------------------------------- state


def design_state(design=None, project=None, include_results=True):
    """Read a design's saved state, or list designs. Core helper (no whitelist);
    callers must have already gated read access."""
    if design:
        if not frappe.has_permission(DESIGN_DOCTYPE, "read", doc=design):
            frappe.throw(_("No read permission for {0}").format(design), frappe.PermissionError)
        doc = frappe.get_doc(DESIGN_DOCTYPE, design)
        state = {
            "design": doc.name,
            "status": doc.status,
            "project": doc.project,
            "design_title": doc.design_title,
            "completion_percent": doc.completion_percent,
            "rollups": {
                "total_basin_gallons": doc.total_basin_gallons,
                "required_circulation_gpm": doc.required_circulation_gpm,
                "design_flow_gpm": doc.design_flow_gpm,
                "computed_tdh_ft": doc.computed_tdh_ft,
                "selected_pump": doc.selected_pump,
            },
            "has_warnings": bool(doc.has_warnings),
            "next_inputs_needed": [s for s in (doc.next_inputs_needed or "").split("\n") if s],
            "counts": {t: len(doc.get(t) or []) for t in EDITABLE_CHILD_TABLES},
        }
        if include_results:
            state["calc_results"] = [
                {
                    "calc": r.calc,
                    "value": r.value,
                    "unit": r.unit,
                    "formula": r.formula,
                    "steps": r.steps,
                    "citations": r.citations,
                    "warnings": r.warnings,
                }
                for r in (doc.calc_results or [])
            ]
        return state

    filters = {"project": project} if project else {}
    rows = frappe.get_list(
        DESIGN_DOCTYPE,
        filters=filters,
        fields=["name", "design_title", "project", "status", "completion_percent"],
        order_by="modified desc",
        limit=20,
    )
    return {"designs": rows}


@frappe.whitelist()
def get_design_state(design=None, project=None, include_results=1):
    _require("read")
    return design_state(design, project, include_results=bool(int(include_results)))


def control_panel_state(panel=None, project=None):
    """Read a Control Panel Design's state, or list panels. Core helper (callers
    must have gated read access)."""
    if panel:
        if not frappe.has_permission(CONTROL_DOCTYPE, "read", doc=panel):
            frappe.throw(_("No read permission for {0}").format(panel), frappe.PermissionError)
        doc = frappe.get_doc(CONTROL_DOCTYPE, panel)
        screens = [
            name
            for name, on in (
                ("Main", doc.screen_main),
                ("Run", doc.screen_run),
                ("Maintenance", doc.screen_maintenance),
                ("Status/Off", doc.screen_status),
            )
            if on
        ]
        return {
            "panel": doc.name,
            "project": doc.project,
            "product_family": doc.product_family,
            "nema_rating": doc.nema_rating,
            "controller_hardware": doc.controller_hardware,
            "power": {
                "main_line_voltage": doc.main_line_voltage,
                "phase": doc.phase,
                "amperage_to_panel": doc.amperage_to_panel,
                "main_breaker_size_a": doc.main_breaker_size_a,
            },
            "screens": screens,
            "sizing": {
                "lighting_total_watts": doc.lighting_total_watts,
                "lighting_current_a": doc.lighting_current_a,
                "lighting_relay_count": doc.lighting_relay_count,
                "solenoid_relay_count": doc.solenoid_relay_count,
                "control_transformer_va": doc.control_transformer_va,
            },
            "counts": {
                "pumps": len(doc.pumps or []),
                "io_points": len(doc.io_points or []),
                "interlocks": len(doc.interlocks or []),
                "lights": len(doc.lights or []),
            },
            "interlocks": [
                {"condition": il.condition, "action": il.action, "enabled": bool(il.enabled)}
                for il in doc.interlocks or []
            ],
            "io_points": [
                {"point_name": io.point_name, "io_type": io.io_type, "signal": io.signal, "device": io.device}
                for io in doc.io_points or []
            ],
        }
    filters = {"project": project} if project else {}
    rows = frappe.get_list(
        CONTROL_DOCTYPE,
        filters=filters,
        fields=["name", "product_family", "project"],
        order_by="modified desc",
        limit=20,
    )
    return {"panels": rows}


@frappe.whitelist()
def get_control_panel_state(panel=None, project=None):
    if not frappe.has_permission(CONTROL_DOCTYPE, "read"):
        frappe.throw(
            _("You do not have access to {0}.").format(CONTROL_DOCTYPE), frappe.PermissionError
        )
    return control_panel_state(panel, project)


# ----------------------------------------------------------------- save


def _save_design(payload):
    """Load-or-create a Water Feature Design from a payload, set allowlisted
    parent fields + child rows, save (triggers recompute), and return the doc.
    Core helper reused by the desk save and the MCP write tool."""
    payload = _parse(payload)
    name = payload.get("design")
    if name and name != "new" and frappe.db.exists(DESIGN_DOCTYPE, name):
        doc = frappe.get_doc(DESIGN_DOCTYPE, name)
    else:
        doc = frappe.new_doc(DESIGN_DOCTYPE)

    fields = _parse(payload.get("fields")) or {}
    for key, value in fields.items():
        if key in EDITABLE_DESIGN_FIELDS:
            doc.set(key, value)

    for table in EDITABLE_CHILD_TABLES:
        if table in payload and payload[table] is not None:
            doc.set(table, [])
            for row in payload[table] or []:
                doc.append(table, row)

    doc.save()  # validate() -> recompute(); permissions enforced (not ignored)
    return doc


@frappe.whitelist()
def save_inputs(payload):
    """Persist gathered inputs to a (new or existing) design; returns its state."""
    _require("write")
    doc = _save_design(payload)
    frappe.db.commit()
    return design_state(doc.name)


@frappe.whitelist()
def preview_design(payload):
    """Recompute a design IN MEMORY from the live form (no save), so the desk form
    can show rollups, per-row velocity/flow/head-loss, warnings, and completion as
    the user edits. Reuses the exact controller ``recompute()`` — the preview is
    byte-identical to a save — so there is no second implementation to drift."""
    _require("read")
    # Lazy import avoids a circular import (the controller imports from this module).
    from erpnext_enhancements.water_engineering.doctype.water_feature_design.water_feature_design import (
        compute_completion_percent,
    )

    payload = _parse(payload)
    doc = frappe.new_doc(DESIGN_DOCTYPE)
    for key, value in (_parse(payload.get("fields")) or {}).items():
        if key in PREVIEW_PARENT_FIELDS:
            doc.set(key, value)
    for table in EDITABLE_CHILD_TABLES:
        if payload.get(table) is not None:
            doc.set(table, [])
            for row in payload[table] or []:
                doc.append(table, row)

    try:
        doc.recompute()
        completion = compute_completion_percent(doc)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Water Feature Design preview")
        return {"error": True}

    warnings, seen = [], set()
    for r in doc.get("calc_results") or []:
        for w in (r.warnings or "").split("\n"):
            if w and w not in seen:
                seen.add(w)
                warnings.append(w)

    def _f(v):
        return float(v) if v not in (None, "") else 0.0

    # Tallest jet across the features that carry a supply head (orifice nozzles),
    # for the canvas — illustrative only. A free jet rises to ~0.9 * supply head
    # (the jet_trajectory de-rating); kept inline so the canvas has no dependency
    # on the optional jet calc.
    jet_height = 0.0
    for f in doc.get("features") or []:
        head = _f(getattr(f, "supply_head_ft", 0))
        if head > 0:
            jet_height = max(jet_height, 0.9 * head)

    # Selected pump's performance curve + this design's duty point, for the chart.
    duty_flow = _f(doc.design_flow_gpm) or _f(doc.required_circulation_gpm)
    curve = pump_curves([doc.selected_pump]).get(doc.selected_pump) if doc.selected_pump else []

    return {
        "rollups": {
            "total_basin_gallons": doc.total_basin_gallons,
            "required_circulation_gpm": doc.required_circulation_gpm,
            "design_flow_gpm": doc.design_flow_gpm,
            "computed_tdh_ft": doc.computed_tdh_ft,
            "selected_pump": doc.selected_pump,
            "chlorinator_feed_gph": doc.chlorinator_feed_gph,
            "drain_capacity_gpm": doc.drain_capacity_gpm,
            "surge_basin_gallons": doc.surge_basin_gallons,
        },
        "static_lift_ft": _f(doc.static_lift_ft),
        "jet_height_ft": jet_height or None,
        "feature_count": len(doc.get("features") or []),
        "pump_curve": curve or [],
        "duty_flow": duty_flow,
        "duty_head": _f(doc.computed_tdh_ft),
        "completion_percent": completion,
        "has_warnings": bool(doc.has_warnings),
        "next_inputs_needed": [s for s in (doc.next_inputs_needed or "").split("\n") if s],
        "warnings": warnings,
        "basins": [{"volume_gal": b.volume_gal, "weight_lb": b.weight_lb} for b in doc.get("basins") or []],
        "features": [{"flow_gpm": f.flow_gpm} for f in doc.get("features") or []],
        "pipe_segments": [
            {
                "velocity_fps": s.velocity_fps,
                "velocity_status": s.velocity_status,
                "head_loss_ft": s.head_loss_ft,
                "segment_label": s.segment_label,
            }
            for s in doc.get("pipe_segments") or []
        ],
    }


# ----------------------------------------------------------- pump catalog


@frappe.whitelist()
def get_pump_candidates(gpm=0, tdh_ft=0):
    """Pumps from ERPNext Items (item_group 'Pumps') with ratings if present,
    fed to the engine's selector. Rating custom fields are optional — absent
    ratings yield candidates the engineer confirms manually."""
    _require("read")
    fields = ["item_code", "item_name"]
    meta = frappe.get_meta("Item")
    for cf in (
        "custom_rated_gpm",
        "custom_rated_tdh_ft",
        "custom_pump_hp",
        "custom_pump_phase",
        "custom_pump_voltage",
    ):
        if meta.has_field(cf):
            fields.append(cf)
    try:
        items = frappe.get_all("Item", filters={"item_group": "Pumps", "disabled": 0}, fields=fields)
    except Exception:
        items = []
    candidates = [
        {
            "item_code": it.get("item_code"),
            "description": it.get("item_name"),
            "rated_gpm": it.get("custom_rated_gpm"),
            "rated_tdh_ft": it.get("custom_rated_tdh_ft"),
            "hp": it.get("custom_pump_hp"),
            "phase": it.get("custom_pump_phase"),
            "voltage": it.get("custom_pump_voltage"),
        }
        for it in items
    ]
    curves = pump_curves([c["item_code"] for c in candidates])
    for c in candidates:
        if curves.get(c["item_code"]):
            c["curve"] = curves[c["item_code"]]
    return select_pump(float(gpm or 0), float(tdh_ft or 0), candidates).to_dict()


@frappe.whitelist()
def check_permission():
    """True if the user may use the wizard (read access to designs)."""
    return frappe.has_permission(DESIGN_DOCTYPE, "read")
