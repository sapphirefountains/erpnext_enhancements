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

from erpnext_enhancements.water_engineering import catalog_reader
from erpnext_enhancements.water_engineering import issues as design_issues
from erpnext_enhancements.water_engineering.engine import (
    basin_volume,
    bather_load,
    calc_lighting,
    calc_solenoid_relays,
    chemical_dose,
    chemistry_targets,
    chlorinator_feed,
    control_transformer_va,
    electric_cost,
    electrical_load,
    evaporation_rate,
    feature_visual_kind,
    filtration_area,
    hazen_williams_loss,
    heating_load,
    jet_trajectory,
    lazy_river_hp,
    lighting_design,
    lsi_index,
    main_drain_flow,
    make_up_water,
    manning_drain_flow,
    minimum_flow_rate,
    nozzle_array_flow,
    nozzle_flow,
    npsh_available,
    open_channel_flow,
    overflow_check,
    ozone_sidestream,
    pipe_pressure_check,
    pipe_pressure_rating,
    program_rules,
    run_spine,
    select_pump,
    service_main_breaker,
    size_drain,
    size_pipe,
    skimmer_sizing,
    suction_outlet_vgb,
    surge_basin_volume,
    total_connected_load,
    total_dynamic_head,
    turnover_gpm,
    turnover_time,
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
        "customer",
        "serial_no",
        "design_title",
        "venue_type",
        "fountain_type",
        "status",
        "turnover_per_hr",
        "hazen_williams_c",
        "pipe_material",
        "static_lift_ft",
        # regulated aquatic-venue inputs
        "governing_code",
        "max_turnover_min",
        "design_flow_published_gpm",
        "bather_sf_per_person",
        "skimmer_sf_each",
        "skimmer_rated_gpm",
        # vessel / shell identity (regulated venues)
        "vessel_model",
        "vessel_listing",
        "vessel_construction",
    }
)

# Child tables a caller may replace wholesale.
EDITABLE_CHILD_TABLES = (
    "basins", "features", "pipe_segments", "pumps", "electrical_loads", "tiers",
    "venue_fixtures", "design_equipment",
)

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
    engine's nozzle_flow needs, plus (v1.758.0) the profile's type, generic flag and
    performance ``points`` ([{head_ft, psi, flow_gpm, display_height_ft,
    display_diameter_ft, throw_ft, depth_over_nozzle_in}], see
    ``catalog_reader.nozzle_profiles``). Empty dict if the profile is missing."""
    if not profile_name:
        return {}
    p = catalog_reader.nozzle_profiles([profile_name]).get(profile_name)
    if not p:
        return {}
    return {
        "cd": p.get("discharge_coefficient"),
        "orifice_diameter_in": p.get("orifice_diameter_in"),
        "orifice_area_in2": p.get("orifice_area_in2"),
        "rated_gpm": p.get("rated_gpm"),
        "rated_head_ft": p.get("rated_head_ft"),
        "nozzle_type": p.get("nozzle_type"),
        "level_sensitive": p.get("level_sensitive"),
        "is_generic_estimate": p.get("is_generic_estimate"),
        "points": p.get("points") or [],
    }


def pump_curves(item_codes):
    """{item_code: [{flow_gpm, head_ft}, ...]} performance-curve points for the
    given pump Items: each Item's best Pump Curve record (full speed, sourced,
    largest impeller), else its legacy ``custom_pump_curve`` table. Items with no
    curve are absent. ``catalog_reader.pump_curve_sets`` has every curve, with
    efficiency, NPSHr and provenance."""
    out = {}
    for item_code, curves in catalog_reader.pump_curve_sets(item_codes).items():
        points = curves[0]["points"] if curves else []
        if points:
            out[item_code] = [{"flow_gpm": p["flow_gpm"], "head_ft": p["head_ft"]} for p in points]
    return out


@frappe.whitelist()
def item_pump_curves(item_code):
    """The Item form's Pump Curves panel: this Item's curves, best first, with points.

    Read-gated on Pump Curve; a user who cannot read Pump Curves gets an empty list and
    ``can_read`` False rather than an error, so the Item form still opens for them.
    """
    if not item_code or not frappe.has_permission("Pump Curve", "read"):
        return {"curves": [], "can_read": False, "can_create": False}
    frappe.has_permission("Item", "read", doc=item_code, throw=True)
    curves = catalog_reader.pump_curve_sets([item_code]).get(item_code, [])
    return {
        "curves": curves,
        "can_read": True,
        "can_create": bool(frappe.has_permission("Pump Curve", "create")),
    }


def _model(doc):
    """The design's schema-2 model (``model_json``), or {}."""
    raw = doc.get("model_json")
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _results(doc):
    raw = doc.get("results_json")
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _canvas_state(doc):
    """The fountain-design "canvas" state — everything the shared SVG renderer
    (window.WaterFountain / the Triton chat) needs to draw the design, read from
    the schema-2 model and the last derive's per-row values (v1.768.0)."""

    def _f(v):
        try:
            return float(v) if v not in (None, "") else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _ft(inch):
        return f"{round(_f(inch) / 12.0, 1):g}"

    model = _model(doc)
    values = _results(doc).get("values") or {}
    seg_values = values.get("segments") or {}
    segs = (model.get("network") or {}).get("segments") or []

    label = ""
    basins = model.get("basins") or []
    if basins:
        b = basins[0]
        if str(b.get("shape") or "").lower().startswith(("round", "cyl", "circ")) and _f(b.get("diameter_in")):
            label = f"{_ft(b.get('diameter_in'))} ft dia x {_ft(b.get('depth_in'))} ft deep"
        elif _f(b.get("length_in")) and _f(b.get("width_in")):
            label = f"{_ft(b.get('length_in'))} x {_ft(b.get('width_in'))} ft x {_ft(b.get('depth_in'))} ft deep"

    features = model.get("features") or []
    jet = max((_f((f.get("inputs") or {}).get("height_ft")) for f in features), default=0.0)
    tiers = []
    for f in features:
        for t in (f.get("inputs") or {}).get("tiers") or []:
            tiers.append({"diameter_in": _f(t.get("diameter_in")), "rim_height_in": _f(t.get("rim_height_in"))})
    curve = pump_curves([doc.selected_pump]).get(doc.selected_pump) if doc.get("selected_pump") else []
    kind = feature_visual_kind(features[0].get("type") or "") if features else None
    return {
        "basin_gallons": doc.get("total_basin_gallons"),
        "flow_gpm": doc.get("design_flow_gpm"),
        "feature_count": len(features),
        "feature_kind": kind,
        "tiers": tiers,
        "jet_height_ft": jet or None,
        "pump": doc.get("selected_pump"),
        "tdh_ft": doc.get("computed_tdh_ft"),
        "static_lift_ft": max((_f(n.get("elevation_ft")) for n in (model.get("network") or {}).get("nodes") or []
                               if n.get("kind") == "terminal"), default=0.0),
        "basin_label": label,
        "worst_status": "",
        "segments": [
            {"label": s.get("label") or s.get("id"), "velocity_fps": (seg_values.get(s.get("id")) or {}).get("velocity_fps"),
             "velocity_status": ""}
            for s in segs
        ],
        "curve": curve or [],
        "duty_flow": _f(doc.get("operating_flow_gpm")) or _f(doc.get("design_flow_gpm")),
        "duty_head": _f(doc.get("computed_tdh_ft")),
    }


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
    elif calc == "pipe_pressure_rating":
        r = pipe_pressure_rating(i.get("material", "SCH40 PVC"), i.get("nominal_size") or i.get("size"), i.get("temp_f", 73))
    elif calc == "pipe_pressure_check":
        r = pipe_pressure_check(
            i.get("material", "SCH40 PVC"), i.get("nominal_size") or i.get("size"),
            i.get("system_psi", 0), i.get("temp_f", 73),
        )
    elif calc == "total_dynamic_head":
        r = total_dynamic_head(
            i.get("segments", []), static_lift_ft=i.get("static_lift_ft", 0), c=i.get("c") or 130
        )
    elif calc == "select_pump":
        r = select_pump(i.get("flow_gpm", 0), i.get("tdh_ft", 0), i.get("candidates"))
    elif calc == "chlorinator_feed":
        r = chlorinator_feed(i.get("volume_gal", 0), i.get("chlorine_pct", 10))
    elif calc == "chemistry_targets":
        r = chemistry_targets(i.get("water_type", "outdoor"), i.get("cya_ppm"), i.get("free_cl_ppm"))
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
    elif calc == "lighting_design":
        r = lighting_design(i.get("surface_area_sf", 0), i.get("pool_class", "residential"))
    elif calc == "overflow_check":
        r = overflow_check(
            i.get("surface_area_sf", 0), i.get("pipe_size"),
            i.get("rain_in_hr", 7.9), i.get("runoff_fraction", 1.0),
        )
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
    elif calc == "turnover_time":
        r = turnover_time(i.get("volume_gal", 0), i.get("flow_gpm", 0))
    elif calc == "minimum_flow_rate":
        r = minimum_flow_rate(
            i.get("volume_gal", 0),
            i.get("max_turnover_min", 0),
            venue=i.get("venue", "spa"),
            governing_code=i.get("governing_code", ""),
        )
    elif calc == "bather_load":
        r = bather_load(
            i.get("surface_area_sf", 0),
            i.get("sf_per_person", 0),
            rounding=i.get("rounding", "floor"),
            venue=i.get("venue", "spa"),
            governing_code=i.get("governing_code", ""),
        )
    elif calc == "skimmer_sizing":
        r = skimmer_sizing(
            i.get("surface_area_sf", 0),
            sf_each=i.get("sf_each", 0),
            rated_gpm=i.get("rated_gpm", 0),
            derate=i.get("derate", 0.80),
            venue=i.get("venue", "spa"),
            governing_code=i.get("governing_code", ""),
        )
    elif calc == "main_drain_flow":
        r = main_drain_flow(
            i.get("open_area_in2", 0),
            velocity_fps=i.get("velocity_fps", 1.5),
            drains=i.get("drains", 2),
            governing_code=i.get("governing_code", ""),
        )
    elif calc == "electrical_load":
        r = electrical_load(
            i.get("fla_amps", 0),
            hp=i.get("hp", 0),
            phase=i.get("phase", 1),
            voltage=i.get("voltage", 0),
            is_motor=str(i.get("is_motor", True)).strip().lower() not in ("0", "false", "no", ""),
            device=i.get("device") or "inverse_time_breaker",
        )
    elif calc == "total_connected_load":
        r = total_connected_load(i.get("loads", []))
    elif calc == "service_main_breaker":
        r = service_main_breaker(i.get("loads", []))
    elif calc == "control_transformer_va":
        r = control_transformer_va(i.get("control_loads", []))
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
            "counts": _model_counts(doc),
            # Typed issues + per-section readiness (additive; the legacy
            # warnings/next_inputs_needed shapes above are unchanged).
            "issues": _parse(doc.design_issues_json) if doc.get("design_issues_json") else [],
            "readiness": _parse(doc.readiness_json) if doc.get("readiness_json") else {},
            "blocker_count": doc.get("blocker_count") or 0,
            "warning_count": doc.get("warning_count") or 0,
            "issue_ready": bool(doc.get("issue_ready")),
            "fitting_schedule": design_issues.fitting_schedule(doc),
            # The fountain-canvas state so the AI walkthrough can draw the same
            # picture the desk form shows (see api ``_canvas_state``).
            "canvas": _canvas_state(doc),
        }
        if include_results:
            state["calc_results"] = [
                {
                    "calc": key,
                    "value": r.get("value"),
                    "unit": r.get("unit"),
                    "formula": r.get("formula"),
                    "steps": "\n".join(str(s) for s in r.get("steps") or []),
                    "citations": ", ".join(str(c) for c in r.get("citations") or []),
                    "warnings": "\n".join(str(w) for w in r.get("warnings") or []),
                }
                for key, r in (_results(doc).get("results") or {}).items()
                if isinstance(r, dict)
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


def _model_counts(doc):
    """Row counts per v1 table name, from the schema-2 model (the AI status tool reads these)."""
    model = _model(doc)
    net = model.get("network") or {}
    return {
        "basins": len(model.get("basins") or []),
        "features": len(model.get("features") or []),
        "pipe_segments": len(net.get("segments") or []),
        "pumps": len(net.get("pumps") or []),
        "electrical_loads": len(net.get("pumps") or []) + len((model.get("lighting") or {}).get("fixtures") or []),
        "tiers": sum(len((f.get("inputs") or {}).get("tiers") or []) for f in model.get("features") or []),
        "venue_fixtures": 0,
        "design_equipment": len(model.get("equipment") or []),
    }


# The record's own fields; everything else in a v1 payload's ``fields`` is a design input that
# ``engine.model.migrate`` reads from ``global_inputs``.
_RECORD_FIELDS = ("project", "customer", "serial_no", "design_title", "status")


# Schema-1 design inputs that have a place in a schema-2 model: (section, key).
_GLOBAL_INPUT_PATHS = {
    "venue_type": ("site", "venue_type"),
    "governing_code": ("site", "governing_code"),
    "pipe_material": ("pump_room", "material"),
}


def merge_legacy_payload(existing, tables, global_inputs):
    """A schema-1 payload laid onto an existing schema-2 model, table by table, as the old tool
    promised ("each list REPLACES that table"): only the sections the payload names change, and
    each one it changes is pinned ``custom`` so the next derive keeps it. Returns (model, ignored),
    ``ignored`` naming every input there is no place for, so the caller can say so instead of
    dropping it in silence."""
    from erpnext_enhancements.water_engineering.engine import model as model_engine

    model = model_engine.from_dict(existing).to_dict()
    setup = model.setdefault("setup", {})
    ignored = []
    legacy = {t: rows for t, rows in tables.items() if t in ("basins", "features", "tiers", "pipe_segments", "pumps")}
    legacy["global_inputs"] = dict(global_inputs or {})
    legacy.setdefault("basins", [])
    if "pipe_segments" in tables and "features" not in tables and model.get("features"):
        # The old shape has no topology: its segments are laid from the feature list, so a segment list
        # alone cannot be placed against the features the Designer drew.
        legacy.pop("pipe_segments")
        ignored.append("pipe_segments (send features with them, or edit the piping in the Fountain Designer)")
    migrated = model_engine.migrate(legacy)
    if "basins" in tables:
        model["basins"] = migrated["basins"]
        setup["basin"] = "custom"
    if "features" in tables:
        model["features"] = migrated["features"]
        setup["features"] = "custom"
    elif "tiers" in tables:
        ignored.append("tiers (send them with the features they belong to)")
    # A feature caught by no basin the model has (new basins, or features sent without their basins,
    # which migrate leaves unattached) lands in the first basin, as the old engine read it.
    ids = [b.get("id") for b in model.get("basins") or []]
    for f in model.get("features") or []:
        if f.get("basin") not in ids:
            f["basin"] = ids[0] if ids else None
    if "pipe_segments" in legacy:
        model["network"] = migrated["network"]
        setup["piping"] = "custom"
    if "pumps" in tables:
        picks = [e for e in migrated.get("equipment") or [] if e.get("slot") == "pump"]
        model["equipment"] = [e for e in model.get("equipment") or [] if e.get("slot") != "pump"] + picks
        pumps = (model.get("network") or {}).get("pumps") or []
        if picks and pumps:
            # The curve goes with the Item: naming it lets the derive load that pump's own curve
            # (derive.resolve_curves), rather than solve the new pump on the old pick's curve.
            pumps[0]["item"] = picks[0]["item"]
            pumps[0]["curve"] = picks[0]["item"]
            for key in ("hp", "kw", "shutoff_head_ft", "max_flow_gpm", "motor_eff_pct", "catalog_source"):
                pumps[0].pop(key, None)
            setup["pump"] = "custom"
    for table in ("electrical_loads", "venue_fixtures", "design_equipment"):
        if table in tables:
            ignored.append(f"{table} (the Fountain Designer derives these)")
    for key, value in (global_inputs or {}).items():
        path = _GLOBAL_INPUT_PATHS.get(key)
        if path:
            model.setdefault(path[0], {})[path[1]] = value
        else:
            ignored.append(key)
    return model, ignored


def _save_design(payload):
    """Load-or-create a Water Feature Design from a schema-1 payload (the old wizard and the
    ``save_water_design`` AI tool) and save it. Since v1.768.0 the design lives in ``model_json``:
    a new design (or one never converted) is built from the payload with ``engine.model.migrate``;
    an existing schema-2 design takes only the tables and inputs the payload names
    (``merge_legacy_payload``), and ``doc.flags.ignored_inputs`` lists what had no place. Phase H
    moves the AI tool onto ``api/designer.py`` and a model-2 payload."""
    from erpnext_enhancements.water_engineering.engine import model as model_engine

    payload = _parse(payload)
    name = payload.get("design")
    if name and name != "new" and frappe.db.exists(DESIGN_DOCTYPE, name):
        doc = frappe.get_doc(DESIGN_DOCTYPE, name)
    else:
        doc = frappe.new_doc(DESIGN_DOCTYPE)

    fields = _parse(payload.get("fields")) or {}
    for key in _RECORD_FIELDS:
        if key in fields and key in EDITABLE_DESIGN_FIELDS:
            value = fields[key]
            if key == "status":  # the v1 statuses (v1.768.0 has Draft / Derived / Reviewed / Issued)
                value = {"Inputs Gathered": "Draft", "Calculated": "Derived"}.get(value, value)
            doc.set(key, value)

    tables = {t: payload[t] for t in EDITABLE_CHILD_TABLES if payload.get(t) is not None}
    inputs = {k: v for k, v in fields.items() if k in EDITABLE_DESIGN_FIELDS and k not in _RECORD_FIELDS}
    existing = _model(doc)
    doc.flags.ignored_inputs = []
    if existing and not model_engine.is_legacy(existing) and not doc.is_new():
        if tables or inputs:
            model, doc.flags.ignored_inputs = merge_legacy_payload(existing, tables, inputs)
            doc.model_json = json.dumps(model)
    elif tables or inputs or doc.is_new():
        legacy = dict(tables)
        legacy["global_inputs"] = inputs
        legacy.setdefault("basins", [])
        model = model_engine.migrate(legacy)
        model["acks"] = existing.get("acks") or []
        doc.model_json = json.dumps(model)

    doc.save()  # validate() derives the model; permissions enforced (not ignored)
    return doc


@frappe.whitelist()
def save_inputs(payload):
    """Persist gathered inputs to a (new or existing) design; returns its state."""
    _require("write")
    doc = _save_design(payload)
    frappe.db.commit()
    return design_state(doc.name)


@frappe.whitelist()
def get_design_issues(design):
    """The persisted typed issues + readiness for one design (thin reader for
    the wizard page and anything that doesn't need the full state)."""
    _require("read")
    if not frappe.has_permission(DESIGN_DOCTYPE, "read", doc=design):
        frappe.throw(_("No read permission for {0}").format(design), frappe.PermissionError)
    doc = frappe.get_doc(DESIGN_DOCTYPE, design)
    return {
        "design": doc.name,
        "issues": _parse(doc.design_issues_json) if doc.get("design_issues_json") else [],
        "readiness": _parse(doc.readiness_json) if doc.get("readiness_json") else {},
        "blocker_count": doc.get("blocker_count") or 0,
        "warning_count": doc.get("warning_count") or 0,
        "issue_ready": bool(doc.get("issue_ready")),
    }


@frappe.whitelist()
def acknowledge_issue(design, issue_key, note=None):
    """Record a sign-off on one warning-severity issue (who/when/note). The ack
    is keyed to the issue's stable key; it survives recomputes and is dropped
    automatically if the issue clears (see the controller's _drop_stale_acks).
    Draft docs only — an issued/submitted design's record is frozen."""
    _require("write")
    doc = frappe.get_doc(DESIGN_DOCTYPE, design)
    if not doc.has_permission("write"):
        frappe.throw(_("No write permission for {0}").format(design), frappe.PermissionError)
    if doc.docstatus != 0:
        frappe.throw(_("Cannot acknowledge issues on a submitted design."))

    live = {i.get("key"): i for i in (_parse(doc.design_issues_json) or [])}
    issue = live.get(issue_key)
    if not issue:
        frappe.throw(_("Issue {0} is not open on this design (it may have cleared — reload).").format(issue_key))
    if issue.get("severity") != "warning":
        frappe.throw(_("Only warning-severity issues take acknowledgements; blockers must be resolved."))

    if issue_key not in {a.issue_key for a in doc.get("issue_acks") or []}:
        doc.append("issue_acks", {
            "issue_key": issue_key,
            "title": (issue.get("title") or "")[:140],
            "acknowledged_by": frappe.session.user,
            "acknowledged_on": frappe.utils.now_datetime(),
            "note": note,
        })
        doc.save()
        frappe.db.commit()
    return design_state(doc.name)


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
def get_equipment_candidates(equipment_class=None):
    """Aquatic-equipment Items of a given class (Filter / Heater / Skimmer /
    Suction Outlet (VGB) / Therapy Jet / ...) with their spec + electrical custom
    fields, for the equipment schedule and the filter/heater/VGB cross-checks.
    Empty if the class is blank or the catalog fields aren't migrated yet."""
    _require("read")
    if not equipment_class:
        return []
    meta = frappe.get_meta("Item")
    if not meta.has_field("custom_equipment_class"):
        return []
    fields = ["item_code", "item_name", "item_group"]
    for cf in (
        "custom_equipment_class", "custom_model_no", "custom_nsf_listing",
        "custom_iapmo_listing", "custom_elec_voltage", "custom_elec_phase",
        "custom_elec_hz", "custom_elec_fla_amps", "custom_elec_watts", "custom_elec_hp",
        "custom_requires_gfci", "custom_nec680_bond", "custom_filter_area_sqft",
        "custom_filter_max_flow_gpm", "custom_filter_media", "custom_heater_fuel",
        "custom_heater_kw", "custom_heater_btu_hr", "custom_vgb_open_area_sqin",
        "custom_vgb_max_flow_gpm", "custom_vgb_rated", "custom_jet_flow_gpm",
        "custom_jet_pressure_psi", "custom_skimmer_max_flow_gpm", "custom_meter_kind",
        "custom_meter_range", "custom_feed_rate_gpd", "custom_controller_channels",
    ):
        if meta.has_field(cf):
            fields.append(cf)
    try:
        return frappe.get_all(
            "Item",
            filters={"custom_equipment_class": equipment_class, "disabled": 0},
            fields=fields,
        )
    except Exception:
        return []


@frappe.whitelist()
def check_permission():
    """True if the user may use the wizard (read access to designs)."""
    return frappe.has_permission(DESIGN_DOCTYPE, "read")


@frappe.whitelist()
def get_loss_catalog():
    """Fitting/valve K-factors and component head-loss coefficients (the engine's
    own tables) for the pipe-segment loss pickers on the form. One source of truth
    for the desk dropdowns and the engine math, so the two can't drift."""
    _require("read")
    # Lazy import keeps the engine-data module out of the API import graph until used.
    from erpnext_enhancements.water_engineering.engine.data.fittings import COMPONENT_COEFF, FITTING_K

    return {
        "fittings": [{"type": name, "k": k} for name, k in FITTING_K.items()],
        "components": [{"type": name, "coeff": coeff} for name, coeff in COMPONENT_COEFF.items()],
    }
