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
    chemistry_targets,
    chlorinator_feed,
    hazen_williams_loss,
    manning_drain_flow,
    nozzle_array_flow,
    nozzle_flow,
    npsh_available,
    ozone_sidestream,
    run_spine,
    select_pump,
    size_drain,
    size_pipe,
    suction_outlet_vgb,
    surge_basin_volume,
    total_dynamic_head,
    turnover_gpm,
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
