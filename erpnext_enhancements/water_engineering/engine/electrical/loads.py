"""The electrical load list, built from the design model (PLAN.md section 3.3).

Every load is a plain dict with one shape (:data:`LOAD_KEYS`), so the circuit,
feeder, energy and BOM modules read the same rows:

* pumps from ``network.pumps`` (or the ``pumps`` argument, which wins), 1- or
  3-phase. A motor is sized from its HP through the NEC FLC tables (430.6(A)(1));
  an explicit nameplate ``fla`` is kept for the overloads and used for sizing only
  when the HP is not a table size. A pump given only as ``kw`` (shaft power, the
  hydraulics rollup) is rounded up to the next standard motor HP.
* lights from ``lighting.fixtures``: low-voltage fixtures load the panel through
  their transformer (sized in ``transformers.py``); line-voltage fixtures load it
  directly.
* controls (the control transformer), the solenoid-valve supply, autofill,
  heaters, UV, chemical feed and aerators, from ``treatment``, ``features`` and
  ``electrical`` (``electrical.loads`` takes any other load by hand).

Loads that run 3 hours or more are continuous (Article 100); pumps, lights,
heaters, UV and chemical feed are, solenoids and autofill are not. The engine
never reads ``hydraulics``: pump power arrives through the model or the argument.
"""

from __future__ import annotations

import math

from ..constants import (
    CONTROL_DEVICE_VA,
    DEFAULT_MOTOR_EFF,
    DEFAULT_PUMP_HOURS_DAY,
    HP_TO_KW,
)
from ..data.nec_tables import (
    CIT_FLC_1PH,
    CIT_FLC_3PH,
    FLC_1PH,
    FLC_3PH,
    FLC_VOLTAGE_BANDS,
    LOW_VOLTAGE_CONTACT_LIMIT,
)
from ..envelope import CalcResult, make_input
from ._util import amps_of, edition_warning, num, phase_of, va_of

CIT_CONTINUOUS = "NEC Article 100 'Continuous Load' (3 hours or more)"
CIT_FLC_RULE = "NEC 430.6(A)(1) (size from the FLC tables, overloads from the nameplate)"
LOAD_KEYS = (
    "key", "label", "kind", "qty", "is_motor", "continuous", "voltage", "phase", "hp",
    "flc_a", "fla_a", "amps", "va", "watts", "control", "code_letter", "submersible",
    "underwater", "run_ft", "duty_hours_day", "gfci", "source", "secondary_v", "notes",
)
DEFAULT_SERVICE_V = 240
DEFAULT_PANEL_FT = 60.0
DEFAULT_LIGHT_HOURS_DAY = 6.0
DEFAULT_LV = 12.0
# Estimates used only when the model names a load but not its rating. Every one
# is flagged in the load's notes; the nameplate replaces it.
EST_UV_W = 200.0
EST_CHEM_FEED_A_120V = 1.7  # Stenner 45M-class peristaltic feeder (the Fika spa's)
EST_SOLENOID_VA = 10.0  # per 24 V valve coil
EST_PSU_EFF = 0.90


def _band_column(cols: dict, voltage: float) -> int:
    v = num(voltage, 230)
    for low, high, col in FLC_VOLTAGE_BANDS:
        if low <= v <= high and col in cols:
            return col
    return min(cols, key=lambda k: abs(k - v))


def motor_flc(hp: float, phase: int = 1, voltage: int = 230, tol: float = 0.01) -> float | None:
    """Motor full-load current (A) from NEC Table 430.248 (1ph) / 430.250 (3ph).

    The system voltage picks the table column by its rated-voltage band (115, 200,
    208, 230, 460, 575; 240 V uses 230, 120 V uses 115). Returns ``None`` for a HP
    not in the table, and the caller uses the nameplate FLA (430.6(A)(1)).
    """
    hp = num(hp)
    table = FLC_3PH if phase_of(phase) == 3 else FLC_1PH
    if hp <= 0:
        return None
    match = next((k for k in table if abs(k - hp) <= tol), None)
    if match is None:
        return None
    cols = table[match]
    return cols[_band_column(cols, voltage or 230)]


def standard_motor_hp(hp: float, phase: int = 1) -> float | None:
    """Smallest table HP at or above ``hp`` (a 2.2 HP shaft load needs a 3 HP motor)."""
    table = FLC_3PH if phase_of(phase) == 3 else FLC_1PH
    h = num(hp)
    return next((k for k in sorted(table) if k >= h * 0.97), None) if h > 0 else None


def blank_load(**kw) -> dict:
    row = {k: None for k in LOAD_KEYS}
    row.update(qty=1, is_motor=False, continuous=True, phase=1, notes=[], submersible=False,
               underwater=False, control="none", gfci=None)
    row.update(kw)
    return row


def motor_load(
    key: str,
    label: str,
    *,
    hp: float = 0.0,
    fla: float = 0.0,
    kw: float = 0.0,
    voltage: float = DEFAULT_SERVICE_V,
    phase: int = 1,
    qty: int = 1,
    control: str = "none",
    code_letter: str | None = None,
    submersible: bool = False,
    run_ft: float = DEFAULT_PANEL_FT,
    duty_hours_day: float = DEFAULT_PUMP_HOURS_DAY,
    kind: str = "pump",
) -> dict:
    """One motor row: FLC from the table when the HP is a table size, else the
    nameplate FLA, else an estimate from kW."""
    ph = phase_of(phase)
    notes = []
    h = num(hp)
    if h <= 0 and num(kw) > 0:
        h = standard_motor_hp(num(kw) / HP_TO_KW, ph) or num(kw) / HP_TO_KW
        notes.append(f"{num(kw):g} kW shaft power -> {h:g} HP motor (next standard size).")
    flc = motor_flc(h, ph, voltage) if h > 0 else None
    fla_np = num(fla)
    if flc:
        amps, source = flc, "table"
    elif fla_np > 0:
        amps, source = fla_np, "nameplate"
    elif h > 0:
        # Not a table HP and no nameplate: input kVA from HP at the default motor efficiency and 0.85 pf.
        amps = (h * HP_TO_KW * 1000.0 / DEFAULT_MOTOR_EFF / 0.85) / (num(voltage) * (math.sqrt(3) if ph == 3 else 1))
        source = "estimate"
        notes.append(f"{h:g} HP is not a Table 430.248/430.250 size; current estimated. Enter the nameplate FLA.")
    else:
        amps, source = 0.0, "unknown"
        notes.append("No HP, FLA or kW given; this motor carries no load until one is entered.")
    watts = h * HP_TO_KW * 1000.0 / DEFAULT_MOTOR_EFF if h > 0 else amps * num(voltage) * 0.85
    return blank_load(
        key=key, label=label, kind=kind, qty=max(1, int(num(qty, 1))), is_motor=True,
        continuous=num(duty_hours_day, DEFAULT_PUMP_HOURS_DAY) >= 3, voltage=num(voltage), phase=ph,
        hp=h or None, flc_a=flc, fla_a=fla_np or None, amps=amps, va=va_of(amps, voltage, ph),
        watts=watts, control=(control or "none").lower(), code_letter=code_letter,
        submersible=bool(submersible), underwater=bool(submersible), run_ft=num(run_ft, DEFAULT_PANEL_FT),
        duty_hours_day=num(duty_hours_day, DEFAULT_PUMP_HOURS_DAY), source=source, notes=notes,
    )


def simple_load(
    key: str,
    label: str,
    kind: str,
    *,
    watts: float = 0.0,
    va: float = 0.0,
    amps: float = 0.0,
    voltage: float = 120,
    phase: int = 1,
    qty: int = 1,
    continuous: bool = True,
    run_ft: float = DEFAULT_PANEL_FT,
    duty_hours_day: float = 24.0,
    source: str = "user",
    notes: list | None = None,
    **extra,
) -> dict:
    """A non-motor row from whichever of watts / VA / amps is given (unity power
    factor when only watts are known)."""
    ph = phase_of(phase)
    v = num(voltage, 120)
    if num(amps) > 0:
        a = num(amps)
        load_va = va_of(a, v, ph)
    else:
        load_va = num(va) or num(watts)
        a = amps_of(load_va, v, ph)
    return blank_load(
        key=key, label=label, kind=kind, qty=max(1, int(num(qty, 1))), continuous=bool(continuous),
        voltage=v, phase=ph, amps=a, va=load_va, watts=num(watts) or load_va,
        run_ft=num(run_ft, DEFAULT_PANEL_FT), duty_hours_day=num(duty_hours_day, 24.0),
        source=source, notes=list(notes or []), **extra,
    )


def _is_low_voltage(volts: float) -> bool:
    return 0 < num(volts) <= LOW_VOLTAGE_CONTACT_LIMIT["dc_continuous"]


def build_loads(model: dict | None, pumps: list[dict] | None = None) -> CalcResult:
    """Load list from the design model. ``value`` is a list of load dicts
    (:data:`LOAD_KEYS`); ``pumps`` overrides ``network.pumps`` (each
    ``{id, label, hp, fla, kw, voltage, phase, qty, control, code_letter,
    submersible, run_ft, duty_hours_day}``)."""
    model = model or {}
    elec = model.get("electrical") or {}
    site = model.get("site") or {}
    treatment = model.get("treatment") or {}
    service_v = num(elec.get("service_v"), DEFAULT_SERVICE_V)
    service_ph = phase_of(elec.get("phase"), 1)
    panel_ft = num(elec.get("panel_distance_ft"), DEFAULT_PANEL_FT)
    pump_hours = num(elec.get("pump_hours_day"), DEFAULT_PUMP_HOURS_DAY)
    light_hours = num(elec.get("light_hours_day"), DEFAULT_LIGHT_HOURS_DAY)
    regulated = bool(site.get("regulated"))
    loads: list[dict] = []
    steps: list[str] = []

    pump_rows = pumps if pumps is not None else ((model.get("network") or {}).get("pumps") or [])
    for i, p in enumerate(pump_rows, start=1):
        pe = {**p, **(p.get("electrical") or {})}
        control = pe.get("control") or "none"
        row = motor_load(
            f"pump:{pe.get('id') or i}",
            pe.get("label") or f"Pump {pe.get('id') or i}",
            hp=pe.get("hp"), fla=pe.get("fla") or pe.get("fla_amps"), kw=pe.get("kw"),
            voltage=num(pe.get("voltage"), service_v), phase=pe.get("phase") or service_ph,
            qty=pe.get("qty") or 1, control=control, code_letter=pe.get("code_letter"),
            submersible=bool(pe.get("submersible")), run_ft=num(pe.get("run_ft"), panel_ft),
            duty_hours_day=num(pe.get("duty_hours_day"), pump_hours),
        )
        loads.append(row)

    for f in model.get("features") or []:
        if (f.get("family") or "").lower() != "aerator":
            continue
        fi = f.get("inputs") or {}
        loads.append(motor_load(
            f"aerator:{f.get('id')}", f.get("label") or "Aerator", kind="aerator",
            hp=fi.get("hp"), fla=fi.get("fla"), voltage=num(fi.get("voltage"), service_v),
            phase=fi.get("phase") or service_ph, qty=f.get("count") or 1, submersible=True,
            run_ft=panel_ft + num(fi.get("cable_ft")), duty_hours_day=num(fi.get("duty_hours_day"), 24.0),
        ))

    fixtures = (model.get("lighting") or {}).get("fixtures") or []
    lv_w = 0.0
    lv_count = 0
    for j, fx in enumerate(fixtures, start=1):
        volts = num(fx.get("voltage"), DEFAULT_LV)
        qty = max(1, int(num(fx.get("qty"), 1)))
        w = num(fx.get("watts"))
        if _is_low_voltage(volts):
            lv_w += w * qty
            lv_count += qty
            continue
        loads.append(simple_load(
            f"light:{fx.get('id') or j}", fx.get("label") or f"Luminaires {j}", "light",
            watts=w, voltage=volts, qty=qty, run_ft=num(fx.get("run_ft"), panel_ft),
            duty_hours_day=light_hours, underwater=fx.get("underwater", True) is not False,
        ))
    if lv_w > 0:
        lv_v = next(num(f.get("voltage"), DEFAULT_LV) for f in fixtures if _is_low_voltage(num(f.get("voltage"), DEFAULT_LV)))
        # The panel sees the transformer: sized at 1.25 x fixture watts (transformers.py).
        loads.append(simple_load(
            "lighting:lv", f"Low-voltage lighting ({lv_count} fixtures, {lv_w:g} W)", "lighting_transformer",
            watts=lv_w, va=1.25 * lv_w, voltage=120, run_ft=panel_ft, duty_hours_day=light_hours,
            secondary_v=lv_v, underwater=True,
            notes=["Primary load = 1.25 x fixture watts (the transformer rule)."],
        ))
        steps.append(f"low-voltage lighting {lv_w:g} W at {lv_v:g} V -> {1.25 * lv_w:g} VA primary")

    sol = elec.get("solenoid_valves") or {}
    n_valves = int(num(sol.get("qty")))
    if n_valves > 0:
        each = num(sol.get("watts_each"), EST_SOLENOID_VA)
        psu_va = 1.25 * n_valves * each / EST_PSU_EFF
        loads.append(simple_load(
            "valves", f"Solenoid valve supply ({n_valves} valves)", "valve_supply",
            va=psu_va, watts=n_valves * each / EST_PSU_EFF, voltage=120, continuous=False,
            run_ft=panel_ft, duty_hours_day=num(sol.get("duty_hours_day"), light_hours),
            secondary_v=num(sol.get("voltage"), 24), source="user" if sol.get("watts_each") else "estimate",
            notes=[] if sol.get("watts_each") else [f"{EST_SOLENOID_VA:g} W per coil assumed."],
        ))

    heater_kw = num(treatment.get("heater_kw"))
    if heater_kw > 0:
        hv = num(treatment.get("heater_voltage"), service_v)
        loads.append(simple_load(
            "heater", "Heater", "heater", watts=heater_kw * 1000.0, voltage=hv,
            phase=treatment.get("heater_phase") or 1, run_ft=panel_ft,
            duty_hours_day=num(treatment.get("heater_hours_day"), 8.0),
        ))
    if treatment.get("uv"):
        uv_w = num(treatment.get("uv_watts"))
        loads.append(simple_load(
            "uv", "UV sanitizer", "uv", watts=uv_w or EST_UV_W, voltage=120, run_ft=panel_ft,
            duty_hours_day=pump_hours, source="user" if uv_w else "estimate",
            notes=[] if uv_w else [f"{EST_UV_W:g} W assumed; enter the UV system's nameplate."],
        ))
    chem = treatment.get("chem_feed", regulated)
    if chem:
        n_feed = 2 if chem is True else max(1, int(num(chem, 1)))
        loads.append(simple_load(
            "chem_feed", "Chemical feed pumps", "chem_feed", amps=EST_CHEM_FEED_A_120V, voltage=120,
            qty=n_feed, run_ft=panel_ft, duty_hours_day=pump_hours, source="estimate",
            notes=[f"{EST_CHEM_FEED_A_120V:g} A each at 120 V assumed (Stenner 45M class)."],
        ))

    # Control transformer: one contactor per motor on a starter, a relay per
    # lighting circuit and valve, the autofill solenoid, and the HMI.
    starters = sum(r["qty"] for r in loads if r["is_motor"] and r["control"] != "vfd")
    devices = []
    if starters:
        devices.append({"device": "contactor", "qty": starters})
    if n_valves:
        devices.append({"device": "relay", "qty": n_valves})
    autofill = elec.get("autofill", bool(model.get("basins")))
    if autofill:
        devices.append({"device": "solenoid", "qty": 1})
    if loads:
        devices.append({"device": "hmi", "qty": 1})
    ctl_va = sum(CONTROL_DEVICE_VA.get(d["device"], 0.0) * d["qty"] for d in devices)
    if ctl_va > 0:
        loads.append(simple_load(
            "controls", "Control panel (control transformer)", "controls", va=ctl_va, voltage=120,
            run_ft=0.0, duty_hours_day=24.0, control_devices=devices,
        ))

    for k, extra in enumerate(elec.get("loads") or [], start=1):
        key = f"extra:{extra.get('id') or k}"
        if extra.get("is_motor") or num(extra.get("hp")) > 0:
            loads.append(motor_load(
                key, extra.get("label") or f"Motor {k}", kind=extra.get("kind") or "motor",
                hp=extra.get("hp"), fla=extra.get("fla"), voltage=num(extra.get("voltage"), service_v),
                phase=extra.get("phase") or service_ph, qty=extra.get("qty") or 1,
                control=extra.get("control") or "none", submersible=bool(extra.get("submersible")),
                run_ft=num(extra.get("run_ft"), panel_ft), duty_hours_day=num(extra.get("duty_hours_day"), 24.0),
            ))
        else:
            loads.append(simple_load(
                key, extra.get("label") or f"Load {k}", extra.get("kind") or "other",
                watts=extra.get("watts"), va=extra.get("va"), amps=extra.get("amps"),
                voltage=num(extra.get("voltage"), 120), phase=extra.get("phase") or 1, qty=extra.get("qty") or 1,
                continuous=extra.get("continuous", True), run_ft=num(extra.get("run_ft"), panel_ft),
                duty_hours_day=num(extra.get("duty_hours_day"), 24.0),
                submersible=bool(extra.get("submersible")), underwater=bool(extra.get("submersible")),
            ))

    for r in loads:
        steps.append(
            f"{r['label']}: {r['qty']} x {r['amps']:.2f} A at {r['voltage']:g} V {r['phase']}ph "
            f"({'motor' if r['is_motor'] else r['kind']}, {'continuous' if r['continuous'] else 'noncontinuous'}"
            f", {r['source']})"
        )
    warnings = [n for r in loads for n in r["notes"]]
    if not loads:
        warnings.append("The model has no pumps, lights or other electrical loads yet.")
    warnings.append(edition_warning())
    return CalcResult(
        calc="build_loads",
        value=loads,
        unit="loads",
        inputs={
            "service_v": make_input(service_v, "V", "user"),
            "service_phase": make_input(service_ph, "", "user"),
            "panel_distance_ft": make_input(panel_ft, "ft", "user"),
            "pump_hours_day": make_input(pump_hours, "h", "default"),
            "load_count": make_input(len(loads), "", "prior_calc"),
        },
        formula="one row per load ; motors from Table 430.248/430.250 by HP ; continuous = runs 3 h or more",
        steps=steps,
        citations=[CIT_FLC_1PH, CIT_FLC_3PH, CIT_FLC_RULE, CIT_CONTINUOUS],
        warnings=warnings,
    )


def demand_summary(loads: list[dict] | None, service_v: float = DEFAULT_SERVICE_V, service_phase: int = 1) -> CalcResult:
    """Connected load totals: VA by kind, motors, continuous share, and the
    connected current at the service voltage (all loads at 100%; the 125% rules
    live in ``feeders.feeder_load``)."""
    rows = loads or []
    total_va = sum(num(r.get("va")) * int(num(r.get("qty"), 1)) for r in rows)
    cont_va = sum(num(r.get("va")) * int(num(r.get("qty"), 1)) for r in rows if r.get("continuous"))
    motor_va = sum(num(r.get("va")) * int(num(r.get("qty"), 1)) for r in rows if r.get("is_motor"))
    by_kind: dict[str, float] = {}
    for r in rows:
        by_kind[r.get("kind") or "other"] = by_kind.get(r.get("kind") or "other", 0.0) + num(r.get("va")) * int(
            num(r.get("qty"), 1)
        )
    motors = [r for r in rows if r.get("is_motor")]
    largest = max(motors, key=lambda r: num(r.get("va")), default=None)
    amps = amps_of(total_va, service_v, service_phase)
    return CalcResult(
        calc="demand_summary",
        value=round(total_va, 1),
        unit="VA",
        inputs={
            "service_v": make_input(num(service_v), "V", "user"),
            "service_phase": make_input(phase_of(service_phase), "", "user"),
            "connected_a": make_input(round(amps, 2), "A", "prior_calc"),
            "continuous_va": make_input(round(cont_va, 1), "VA", "prior_calc"),
            "motor_va": make_input(round(motor_va, 1), "VA", "prior_calc"),
            "motor_count": make_input(sum(int(num(r.get("qty"), 1)) for r in motors), "", "prior_calc"),
            "largest_motor": make_input(largest.get("label") if largest else None, "", "prior_calc"),
            "va_by_kind": make_input({k: round(v, 1) for k, v in by_kind.items()}, "VA", "prior_calc"),
        },
        formula="connected VA = sum(qty x VA) ; amps = VA / (V x 1 or sqrt(3))",
        steps=[
            f"connected load {total_va:,.0f} VA ({cont_va:,.0f} VA continuous, {motor_va:,.0f} VA motors)",
            f"{total_va:,.0f} VA at {num(service_v):g} V {phase_of(service_phase)}ph = {amps:.1f} A",
        ],
        citations=["NEC Article 220 (load calculations)", "NEC 430.24"],
        warnings=["Assumes single-phase loads are balanced across the service phases.", edition_warning()],
    )


__all__ = [
    "LOAD_KEYS",
    "blank_load",
    "build_loads",
    "demand_summary",
    "motor_flc",
    "motor_load",
    "simple_load",
    "standard_motor_hp",
]
