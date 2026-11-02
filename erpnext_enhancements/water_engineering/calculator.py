"""The stateless calculator: one engine calculation by name, and the list of them with their inputs.

The Fountain Designer's Calculator drawer (Review step), the ``water_calc`` AI tool and
``api/designer.run_calc`` all go through ``run``, so the page and the assistant show the same math.
``CATALOG`` is what the drawer draws its fields from (``boot().calculator``, the page README's
contract): each calc's key, label, group and inputs, every input with the default ``run`` uses when
it is left out. ``CALCS`` is the keys in order, the AI tool's enum.

Until v1.771.0 the dispatch was ``_run_calc`` in ``api/water_design.py``, behind the Water
Engineering Wizard; phase H of the rebuild retired the wizard and moved it here unchanged.

Pure: no frappe. A Nozzle Profile is looked up through the ``nozzle_lookup`` callable the caller
passes (``api/designer.nozzle_profile_params`` on a site), so the tests run it bench-free.
"""

from __future__ import annotations

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
from erpnext_enhancements.water_engineering.engine.constants import EVAP_ACTIVITY_FACTOR, FILTER_MAX_RATE
from erpnext_enhancements.water_engineering.engine.data.chemistry import CONTACT_TANKS
from erpnext_enhancements.water_engineering.engine.data.nec_tables import OCPD_DEVICES

MATERIALS = ["SCH40 PVC", "SCH80 PVC", "Type K Copper"]
VENUES = ["spa", "pool", "wading", "therapy"]


class UnknownCalc(ValueError):
    """``run`` was asked for a calc it does not have."""


# ------------------------------------------------------------------ inputs
# One input: {key, label, kind (number | int | select | check | text | json), unit, default, options}.
# json is a list the engine takes as a whole (pipe segments, lights, loads); the drawer shows it as text.


def _num(key, label, unit="", default=None):
    return {"key": key, "label": label, "kind": "number", "unit": unit, "default": default}


def _int(key, label, default=None):
    return {"key": key, "label": label, "kind": "int", "unit": "", "default": default}


def _sel(key, label, options, default=None):
    return {"key": key, "label": label, "kind": "select", "unit": "", "default": default, "options": list(options)}


def _check(key, label, default=False):
    return {"key": key, "label": label, "kind": "check", "unit": "", "default": default}


def _text(key, label, default=""):
    return {"key": key, "label": label, "kind": "text", "unit": "", "default": default}


def _json(key, label, example):
    return {"key": key, "label": label, "kind": "json", "unit": "", "default": example}


def _calc(key, label, group, inputs, note=""):
    return {"key": key, "label": label, "group": group, "note": note, "inputs": inputs}


FLOW = "Flow and basins"
PIPE = "Piping and pumps"
SAFETY = "Safety checks"
TREAT = "Water treatment"
DRAIN = "Drainage and overflow"
ELEC = "Electrical and controls"
REG = "Regulated pools and spas"
MORE = "Workbook sheets"

CATALOG = [
    _calc("basin_volume", "Basin volume", FLOW, [
        _sel("shape", "Shape", ["rectangular", "cylindrical"], "rectangular"),
        _num("length_in", "Length", "in", 0), _num("width_in", "Width", "in", 0),
        _num("height_in", "Water depth", "in", 0), _num("diameter_in", "Diameter (round)", "in", 0),
    ]),
    _calc("turnover_gpm", "Turnover flow", FLOW, [
        _num("volume_gal", "Volume", "gal", 0), _num("turnovers_per_hr", "Turnovers per hour", "", 2),
    ]),
    _calc("weir_flow", "Weir flow", FLOW, [
        _num("length_ft", "Weir length", "ft", 0), _num("head_in", "Head over the weir", "in", 0),
        _int("contractions", "End contractions", 2),
    ]),
    _calc("nozzle_array_flow", "Nozzle array flow", FLOW, [
        _int("nozzle_count", "Nozzles", 0), _num("gpm_each", "Flow each", "gpm", 0),
    ]),
    _calc("nozzle_flow", "Orifice nozzle flow", FLOW, [
        _text("nozzle_profile", "Nozzle Profile"), _num("supply_head_ft", "Supply head", "ft", 0),
    ], "Q = Cd x A x sqrt(2gh) from the Nozzle Profile's coefficients, or its rated flow at head."),
    _calc("jet_trajectory", "Jet height and pressure", FLOW, [
        _num("target_height_ft", "Target height", "ft", 0), _num("supply_head_ft", "Supply head", "ft", 0),
        _num("supply_psi", "Supply pressure", "psi", 0), _sel("nozzle_type", "Nozzle", ["smooth", "aerated"], "smooth"),
    ], "Give a target height for the pressure it needs, or a head or pressure for the height it reaches."),
    _calc("size_pipe", "Size a pipe", PIPE, [
        _num("flow_gpm", "Flow", "gpm", 0), _num("length_ft", "Length", "ft", 0),
        _sel("material", "Material", MATERIALS, "SCH40 PVC"), _sel("line", "Line", ["discharge", "suction"], "discharge"),
        _num("c", "Hazen-Williams C", "", 130),
    ], "Every size, with its velocity and status, comes back in the options."),
    _calc("hazen_williams_loss", "Hazen-Williams friction loss", PIPE, [
        _num("flow_gpm", "Flow", "gpm", 0), _num("length_ft", "Length", "ft", 0),
        _num("id_in", "Inside diameter", "in", 0), _num("c", "Hazen-Williams C", "", 130),
    ]),
    _calc("pipe_pressure_rating", "Pipe pressure rating", PIPE, [
        _sel("material", "Material", MATERIALS, "SCH40 PVC"), _text("nominal_size", "Nominal size", '2"'),
        _num("temp_f", "Water temperature", "°F", 73),
    ], "PVC derates from 73 to 110 °F to half its rating."),
    _calc("pipe_pressure_check", "Pipe pressure check", PIPE, [
        _sel("material", "Material", MATERIALS, "SCH40 PVC"), _text("nominal_size", "Nominal size", '2"'),
        _num("system_psi", "System pressure", "psi", 0), _num("temp_f", "Water temperature", "°F", 73),
    ]),
    _calc("total_dynamic_head", "Total dynamic head", PIPE, [
        _json("segments", "Pipe segments", [{"label": "Discharge", "flow_gpm": 100, "nominal_size": '3"',
                                              "material": "SCH40 PVC", "length_ft": 50, "line_type": "Discharge",
                                              "fittings": [{"type": "ELL 90", "qty": 4}]}]),
        _num("static_lift_ft", "Static lift", "ft", 0), _num("c", "Hazen-Williams C", "", 130),
    ]),
    _calc("select_pump", "Select a pump", PIPE, [
        _num("flow_gpm", "Flow", "gpm", 0), _num("tdh_ft", "Total dynamic head", "ft", 0),
        _json("candidates", "Candidates", [{"item_code": "PUMP-1", "rated_gpm": 120, "rated_tdh_ft": 60}]),
    ]),
    _calc("npsh_available", "NPSH available", SAFETY, [
        _num("suction_static_ft", "Suction static (+ flooded, - lift)", "ft", 0),
        _num("suction_friction_ft", "Suction friction", "ft", 0), _num("elevation_ft", "Site elevation", "ft", 0),
        _num("water_temp_f", "Water temperature", "°F", 70), _num("npshr_ft", "Pump NPSHr", "ft", 0),
        _num("margin_ft", "Margin", "ft", 3.0),
    ]),
    _calc("suction_outlet_vgb", "Suction outlet (VGB)", SAFETY, [
        _num("system_gpm", "System flow", "gpm", 0), _num("cover_length_in", "Cover length", "in", 0),
        _num("cover_width_in", "Cover width", "in", 0), _num("open_area_fraction", "Open area fraction", "", 0),
        _int("outlets", "Outlets", 1), _num("vmax_fps", "Maximum velocity", "ft/s", 1.5),
    ], "Anti-entrapment cover flow and the dual-drain flag (ANSI/APSP-16)."),
    _calc("water_hammer", "Water hammer", SAFETY, [
        _num("velocity_fps", "Velocity", "ft/s", 0), _num("length_ft", "Pipe length", "ft", 0),
        _num("closure_time_s", "Valve closure time", "s", 0), _sel("material", "Material", MATERIALS, "SCH40 PVC"),
        _num("wave_speed_fps", "Wave speed (blank: by material)", "ft/s", 0),
        _num("static_psi", "Static pressure", "psi", 0), _num("pipe_rating_psi", "Pipe rating", "psi", 0),
    ]),
    _calc("chlorinator_feed", "Chlorinator feed", TREAT, [
        _num("volume_gal", "Volume", "gal", 0), _num("chlorine_pct", "Chlorine strength", "%", 10),
    ]),
    _calc("chemistry_targets", "Chemistry targets", TREAT, [
        _sel("water_type", "Water", ["outdoor", "indoor", "saltwater"], "outdoor"),
        _num("cya_ppm", "Cyanuric acid", "ppm"), _num("free_cl_ppm", "Free chlorine", "ppm"),
    ], "With CYA and free chlorine, checks the DOC-0119 floor: the larger of 2.0 ppm and 7.5% of CYA."),
    _calc("ozone_sidestream", "Ozone sidestream", TREAT, [
        _num("volume_gal", "Volume", "gal", 0), _num("turnover_min", "Turnover", "min", 0),
        _num("sidestream_pct", "Sidestream share", "", 0.25), _sel("contact_tank", "Contact tank", [{"value": k, "label": v.get("label") or k} for k, v in CONTACT_TANKS.items()],
             "CNT120"),
        _int("tank_qty", "Tanks", 1), _sel("log_reduction", "Log reduction", ["2-log", "3-log"], "2-log"),
    ]),
    _calc("lsi_index", "Langelier saturation index", TREAT, [
        _num("ph", "pH", "", 0), _num("temp_f", "Water temperature", "°F", 0),
        _num("calcium_hardness_ppm", "Calcium hardness", "ppm", 0),
        _num("total_alkalinity_ppm", "Total alkalinity", "ppm", 0), _num("tds_ppm", "TDS", "ppm", 1000),
    ]),
    _calc("chemical_dose", "Chemical dose", TREAT, [
        _num("volume_gal", "Volume", "gal", 0),
        _sel("chemical", "Chemical", ["ph_down", "alkalinity_up", "cya_up", "salt_up"], "ph_down"),
        _num("current", "Current reading", "", 0), _num("target", "Target", "", 0),
    ]),
    _calc("uv_dose", "UV dose", TREAT, [
        _num("flow_gpm", "Flow", "gpm", 0), _num("target_red_mj", "Target dose", "mJ/cm²", 60),
    ]),
    _calc("filtration_area", "Filter area", TREAT, [
        _num("design_gpm", "Design flow", "gpm", 0), _sel("media", "Media", list(FILTER_MAX_RATE), "sand"),
        _num("rate_gpm_sf", "Filtration rate (blank: by media)", "gpm/sf", 0),
    ]),
    _calc("evaporation_rate", "Evaporation", TREAT, [
        _num("surface_area_sf", "Water surface", "sf", 0), _num("water_temp_f", "Water temperature", "°F", 0),
        _num("air_temp_f", "Air temperature", "°F", 0), _num("rh_pct", "Relative humidity", "%", 0),
        _sel("activity", "Activity", list(EVAP_ACTIVITY_FACTOR), "residential"),
    ]),
    _calc("make_up_water", "Make-up water", TREAT, [
        _num("evaporation_gpd", "Evaporation", "gal/day", 0), _num("splash_gpd", "Splash", "gal/day", 0),
        _num("backwash_gpd", "Backwash", "gal/day", 0), _num("fill_window_min", "Fill window", "min", 20),
    ]),
    _calc("heating_load", "Heating load", TREAT, [
        _num("volume_gal", "Volume", "gal", 0), _num("delta_f", "Temperature rise", "°F", 0),
        _sel("cover", "Cover", ["none", "solid", "liquid"], "none"), _check("wind", "Windy site", True),
        _num("gas_rate", "Gas rate", "$/therm", 1.40), _num("heater_eff", "Heater efficiency", "", 0.92),
        _num("warmup_hours", "Warm-up time", "hr", 24),
    ]),
    _calc("manning_drain_flow", "Gravity drain flow", DRAIN, [
        _text("nominal_size", "Drain size", '3"'), _num("slope_in_per_ft", "Slope", "in/ft", 0.25),
    ]),
    _calc("size_drain", "Size a drain", DRAIN, [
        _num("required_gpm", "Required flow", "gpm", 0), _num("slope_in_per_ft", "Slope", "in/ft", 0.25),
    ]),
    _calc("surge_basin_volume", "Surge basin volume", DRAIN, [
        _num("pool_area_sf", "Pool area", "sf", 0), _num("basin_area_sf", "Surge basin area", "sf", 0),
        _num("evap_in_day", "Evaporation", "in/day", 0.25), _num("precip_in", "Rain", "in", 1.0),
        _num("vortex_in", "Vortex allowance", "in", 12), _num("freeboard_in", "Freeboard", "in", 3),
        _num("overflow_in", "Overflow", "in", 3), _int("swimmers", "Swimmers", 0),
    ]),
    _calc("overflow_check", "Rain overflow", DRAIN, [
        _num("surface_area_sf", "Surface area", "sf", 0), _text("pipe_size", "Standpipe size"),
        _num("rain_in_hr", "Design rain", "in/hr", 7.9), _num("runoff_fraction", "Runoff fraction", "", 1.0),
    ]),
    _calc("electrical_load", "Motor branch circuit", ELEC, [
        _num("fla_amps", "Full-load amps", "A", 0), _num("hp", "Horsepower", "hp", 0), _int("phase", "Phase", 1),
        _num("voltage", "Voltage", "V", 0), _check("is_motor", "A motor", True),
        _sel("device", "Protective device", [{"value": k, "label": v} for k, v in OCPD_DEVICES.items()],
             "inverse_time_breaker"),
    ], "The NEC 430.52 breaker maximum, with the 430.22 conductor minimum (125% of FLA)."),
    _calc("total_connected_load", "Feeder connected load", ELEC, [
        _json("loads", "Loads", [{"fla": 12, "is_motor": True, "qty": 1, "voltage": 240, "phase": 1}]),
    ], "NEC 430.24: 125% of the largest motor, plus the other motors, plus the other loads."),
    _calc("service_main_breaker", "Service main breaker", ELEC, [
        _json("loads", "Loads", [{"fla": 12, "is_motor": True, "qty": 1, "voltage": 240, "phase": 1}]),
    ], "NEC 430.62/430.63, rounded down and never below the load."),
    _calc("control_transformer_va", "Control transformer", ELEC, [
        _json("control_loads", "Control loads", [{"device": "contactor", "qty": 2}, {"device": "plc", "qty": 1}]),
    ]),
    _calc("calc_lighting", "Lighting circuit", ELEC, [
        _json("lights", "Lights", [{"qty": 4, "watts_each": 25}]), _num("lighting_voltage", "Voltage", "V", 12),
        _num("per_relay_watts", "Watts per relay", "W", 60),
    ]),
    _calc("calc_solenoid_relays", "Solenoid relays", ELEC, [_int("valve_qty", "Valves", 0)]),
    _calc("electric_cost", "Pump running cost", ELEC, [
        _num("flow_gpm", "Flow", "gpm", 0), _num("tdh_ft", "Total dynamic head", "ft", 0),
        _num("hours_per_day", "Hours a day", "hr", 6), _num("rate_per_kwh", "Rate", "$/kWh", 0.17),
        _num("sg", "Specific gravity", "", 1.0), _num("pump_eff", "Pump efficiency", "", 0.70),
        _num("motor_eff", "Motor efficiency", "", 0.90), _int("pump_qty", "Pumps", 1),
    ]),
    _calc("turnover_time", "Turnover time", REG, [
        _num("volume_gal", "Volume", "gal", 0), _num("flow_gpm", "Flow", "gpm", 0),
    ]),
    _calc("minimum_flow_rate", "Code minimum flow", REG, [
        _num("volume_gal", "Volume", "gal", 0), _num("max_turnover_min", "Maximum turnover", "min", 0),
        _sel("venue", "Venue", VENUES, "spa"), _text("governing_code", "Governing code"),
    ]),
    _calc("bather_load", "Bather load", REG, [
        _num("surface_area_sf", "Water surface", "sf", 0), _num("sf_per_person", "Square feet a bather", "sf", 0),
        _sel("rounding", "Rounding", ["floor", "nearest", "ceil"], "floor"), _sel("venue", "Venue", VENUES, "spa"),
        _text("governing_code", "Governing code"),
    ]),
    _calc("skimmer_sizing", "Skimmers", REG, [
        _num("surface_area_sf", "Water surface", "sf", 0), _num("sf_each", "Square feet a skimmer", "sf", 0),
        _num("rated_gpm", "Rated flow each", "gpm", 0), _num("derate", "Derate", "", 0.80),
        _sel("venue", "Venue", VENUES, "spa"), _text("governing_code", "Governing code"),
    ]),
    _calc("main_drain_flow", "Main drain flow", REG, [
        _num("open_area_in2", "Open area", "in²", 0), _num("velocity_fps", "Velocity", "ft/s", 1.5),
        _int("drains", "Drains", 2), _text("governing_code", "Governing code"),
    ]),
    _calc("program_rules", "Program rules", REG, [
        _num("surface_area_sf", "Water surface", "sf", 0), _sel("pool_class", "Class", ["pool", "spa"], "pool"),
    ]),
    _calc("lighting_design", "Underwater lighting", MORE, [
        _num("surface_area_sf", "Water surface", "sf", 0),
        _sel("pool_class", "Class", ["shallow_pond", "residential", "public", "diving_shallow", "diving_deep",
                                     "competition"], "residential"),
    ]),
    _calc("vertical_pipe", "Standpipe discharge", MORE, [
        _num("head_in", "Head", "in", 0), _num("id_in", "Inside diameter", "in", 0), _num("flow_gpm", "Flow", "gpm", 0),
    ], "Give two of head, diameter and flow for the third."),
    _calc("open_channel_flow", "Open channel flow", MORE, [
        _num("width_in", "Width", "in", 0), _num("depth_in", "Depth", "in", 0), _num("slope", "Slope", "", 0),
        _num("n", "Manning n", "", 0.015),
    ]),
    _calc("lazy_river_hp", "Lazy river power", MORE, [
        _num("width_ft", "Width", "ft", 0), _num("depth_ft", "Depth", "ft", 0), _num("length_ft", "Length", "ft", 0),
        _num("velocity_fps", "Current", "ft/s", 5.0), _num("n", "Manning n", "", 0.0155), _num("sg", "Specific gravity", "", 1.0),
        _num("safety_factor", "Safety factor", "", 2.0),
    ]),
]

CALCS = tuple(c["key"] for c in CATALOG)
_BY_KEY = {c["key"]: c for c in CATALOG}


def catalog():
    """The calc list the page draws, a fresh copy (the caller may change it)."""
    return [
        {**c, "inputs": [{**i, "options": [dict(o) if isinstance(o, dict) else o for o in i["options"]]}
                         if "options" in i else dict(i) for i in c["inputs"]]}
        for c in CATALOG
    ]


def spec(calc):
    """One calc's entry, or None."""
    return _BY_KEY.get(calc)


# ------------------------------------------------------------------ dispatch


def run(calc, inputs, nozzle_lookup=None):
    """Run one engine calculation; return its ``CalcResult`` dict. Raises ``UnknownCalc`` for a name
    not in ``CATALOG``. ``nozzle_lookup(profile_name) -> dict`` resolves a Nozzle Profile for
    ``nozzle_flow`` (``{}`` when there is none)."""
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
        params = (nozzle_lookup(i.get("nozzle_profile")) if nozzle_lookup else None) or {}
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
        raise UnknownCalc(calc)
    return r.to_dict()
