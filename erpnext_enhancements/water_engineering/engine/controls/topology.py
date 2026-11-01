"""Pump control topology: how each pump is started and run, and the parts that
takes.

Four schemes:

* ``across_the_line``: a contactor (NEMA size or IEC AC-3 frame) and an overload
  relay. Full voltage, about 6 x FLC starting current.
* ``soft_starter``: a reduced-voltage solid-state starter (about 3-4 x FLC) with
  its internal bypass. Gentler on the piping and on a generator.
* ``vfd``: a drive. Speed control for wind stages, show modulation and a
  pressure or flow setpoint; starting current near FLC.
* ``vfd_bypass``: a drive plus a manual bypass. A line contactor (K1) ahead of the
  drive, an output contactor (K2) after it, and a bypass contactor (K3) straight
  across the line to the motor, with an overload in the bypass path. K2 and K3 are
  mechanically and electrically interlocked: closing both would feed line voltage
  into the drive's output. A Hand-Off-Auto switch and a VFD/Bypass selector, and
  optional automatic transfer to bypass on a drive fault.

:func:`recommend_topology` picks one from the pump's size and duty with a list of
reasons. The starter sizes are NEMA ICS 2; the IEC frames, overload ranges and
the rules of thumb in the recommender are typical practice, labeled as such.
"""

from __future__ import annotations

import itertools

from ..data.motor_control import (
    CIT_IEC,
    CIT_NEMA,
    CIT_OVERLOAD,
    IEC_AC3_FRAMES_A,
    NEMA_CONTINUOUS_A,
    NEMA_MAX_HP_1PH,
    NEMA_MAX_HP_3PH,
    NEMA_SIZES,
    OVERLOAD_RANGES_A,
    SOFT_STARTER_FRAMES_A,
)
from ..data.nec_tables import VERIFY_EDITION
from ..electrical._util import EPS, num, phase_of
from ..electrical.loads import motor_flc, standard_motor_hp
from ..envelope import CalcOption, CalcResult, make_input

TOPOLOGIES = {
    "across_the_line": "Across-the-line starter",
    "soft_starter": "Soft starter",
    "vfd": "VFD",
    "vfd_bypass": "VFD with manual bypass",
}
START_MULTIPLE = {"across_the_line": 6.0, "soft_starter": 3.5, "vfd": 1.5, "vfd_bypass": 1.5}
CIT_TOPOLOGY = "Sapphire controls practice (DOC-0123 / DOC-0126) and motor-control rules of thumb; not NEC"
CIT_BYPASS = "UL 508A / NEC 430.86 (bypass isolation); drive-maker bypass wiring guidance"
SOFT_START_MIN_HP = 15.0  # at and above this, a full-voltage start is hard on piping and service (typical)
BYPASS_MIN_HP = 7.5  # at and above this a drive failure is costly enough to justify a bypass (typical)
CRITICAL_ROLES = ("circulation", "filtration", "filter")


def _nema_column(cols: dict, voltage: float) -> int:
    v = num(voltage, 230)
    if v <= 150:
        return 115 if 115 in cols else min(cols)
    if v >= 440:
        return 460 if 460 in cols else max(cols)
    if v < 220 and 200 in cols:
        return 200
    return 230


def nema_size(hp: float, voltage: float = 230, phase: int = 1) -> CalcResult:
    """Smallest NEMA starter size rated for ``hp`` across the line at ``voltage``
    (NEMA ICS 2). Single-phase includes the 1P size between 1 and 2."""
    h = num(hp)
    table = NEMA_MAX_HP_3PH if phase_of(phase) == 3 else NEMA_MAX_HP_1PH
    size = next(
        (s for s in NEMA_SIZES if s in table and table[s][_nema_column(table[s], voltage)] >= h - EPS),
        None,
    )
    col = _nema_column(next(iter(table.values())), voltage)
    alt = None
    if size == "1P":
        alt = "2"  # not every maker builds 1P; size 2 is the next full size
    return CalcResult(
        calc="nema_size",
        value=size,
        unit="NEMA size",
        inputs={
            "hp": make_input(h, "HP", "user"),
            "voltage": make_input(num(voltage), "V", "user", f"{col} V column"),
            "phase": make_input(phase_of(phase), "", "user"),
            "continuous_a": make_input(NEMA_CONTINUOUS_A.get(size), "A", "standard"),
            "alternate_size": make_input(alt, "NEMA size", "standard"),
        },
        formula="smallest NEMA size whose max HP at the voltage >= motor HP",
        steps=[f"{h:g} HP, {phase_of(phase)}ph at the {col} V column -> NEMA size {size}"
               + (" (size 2 where 1P is not made)" if alt else "")],
        citations=[CIT_NEMA],
        warnings=[] if size else [f"{h:g} HP is past the NEMA table; use a soft starter or a drive."],
    )


def iec_contactor(flc_amps: float) -> CalcResult:
    """Smallest IEC AC-3 frame at or above the motor FLC."""
    a = num(flc_amps)
    frame = next((f for f in IEC_AC3_FRAMES_A if f >= a - EPS), None)
    return CalcResult(
        calc="iec_contactor",
        value=frame,
        unit="A (AC-3)",
        inputs={"flc_a": make_input(a, "A", "prior_calc")},
        formula="smallest AC-3 rated current >= motor FLC",
        steps=[f"FLC {a:g} A -> {frame} A AC-3 frame"],
        citations=[CIT_IEC],
        warnings=["Confirm against the maker's UL HP rating at this voltage and phase."],
    )


def overload_relay(
    fla_amps: float,
    *,
    service_factor: float = 1.15,
    submersible: bool = False,
    nema: bool = False,
) -> CalcResult:
    """Overload relay: trip class, dial range and setting. The dial is set to the
    nameplate FLA; the trip may not exceed 125% of it with a service factor of
    1.15 or more (115% otherwise), 430.32(A)(1). Submersible pump makers require
    Class 10 (quick trip); a NEMA general-purpose starter is Class 20."""
    fla = num(fla_amps)
    trip_pct = 125 if num(service_factor, 1.0) >= 1.15 - EPS else 115
    trip_class = 10 if (submersible or not nema) else 20
    ranges = [r for r in OVERLOAD_RANGES_A if r[0] - EPS <= fla <= r[1] + EPS]
    # Prefer the range that puts the setting nearest its middle.
    rng = min(ranges, key=lambda r: abs((r[0] + r[1]) / 2 - fla)) if ranges else None
    warnings = []
    if not rng:
        warnings.append(f"No typical dial range covers {fla:g} A; use a current-transformer electronic overload.")
    if submersible:
        warnings.append("Submersible pump: Class 10 quick-trip, ambient-compensated, per the pump maker's warranty terms.")
    return CalcResult(
        calc="overload_relay",
        value=f"{rng[0]:g}-{rng[1]:g} A" if rng else None,
        unit="dial range",
        inputs={
            "fla_a": make_input(fla, "A", "lookup", "motor nameplate"),
            "setting_a": make_input(round(fla, 2), "A", "prior_calc", "dial set to nameplate FLA"),
            "max_trip_a": make_input(round(fla * trip_pct / 100, 2), "A", "standard", f"430.32(A)(1) {trip_pct}%"),
            "trip_class": make_input(trip_class, "", "default"),
            "service_factor": make_input(num(service_factor, 1.0), "", "user"),
        },
        formula="dial range containing FLA ; setting = FLA ; trip <= 125% FLA (SF >= 1.15) or 115%",
        steps=[f"FLA {fla:g} A -> {rng[0]:g}-{rng[1]:g} A range, set {fla:g} A, Class {trip_class}" if rng
               else f"FLA {fla:g} A: no standard range"],
        citations=[CIT_OVERLOAD],
        warnings=[*warnings, "Overload ranges are typical; match the selected maker's catalog.", VERIFY_EDITION],
    )


def soft_starter(flc_amps: float) -> CalcResult:
    a = num(flc_amps)
    frame = next((f for f in SOFT_STARTER_FRAMES_A if f >= a - EPS), None)
    return CalcResult(
        calc="soft_starter",
        value=frame,
        unit="A",
        inputs={"flc_a": make_input(a, "A", "prior_calc")},
        formula="smallest soft-starter rating >= motor FLC (standard duty, 10 starts/h)",
        steps=[f"FLC {a:g} A -> {frame} A soft starter"],
        citations=["Soft-starter maker ratings (typical frames)"],
        warnings=["Heavy duty (frequent starts, long ramps) takes the next frame up."],
    )


def _pump_fla(pump: dict) -> tuple[float, float, str]:
    """(table FLC for sizing, nameplate FLA for the overload, source note)."""
    hp = num(pump.get("hp"))
    ph = phase_of(pump.get("phase"))
    v = num(pump.get("voltage"), 230)
    flc = num(pump.get("flc_a")) or (motor_flc(standard_motor_hp(hp, ph) or hp, ph, v) or 0.0)
    fla = num(pump.get("fla_a") or pump.get("fla"))
    if flc <= 0:
        flc = fla or num(pump.get("amps"))
    note = "nameplate FLA" if fla else "no nameplate FLA: overload set from the FLC table, reset it to the nameplate"
    return flc, fla or flc, note


# --- VFD with bypass: the interlock logic -------------------------------------------------


def bypass_logic(
    selector: str = "VFD",
    hoa: str = "AUTO",
    run_cmd: bool = False,
    drive_fault: bool = False,
    auto_transfer: bool = False,
    bypass_overload: bool = False,
    estop: bool = False,
) -> dict:
    """Contactor and drive outputs for one state of a VFD-with-bypass panel.

    ``selector`` VFD or BYPASS; ``hoa`` HAND, OFF or AUTO; ``run_cmd`` is the
    controller's run in AUTO. A drive fault with ``auto_transfer`` moves a VFD
    selection to bypass. K1 (drive input) is closed whenever the drive path is
    selected and the E-stop is clear, so the drive stays powered and can report;
    K2 (drive output) and K3 (bypass) never close together.
    """
    demand = (not estop) and (hoa == "HAND" or (hoa == "AUTO" and run_cmd))
    transferred = selector == "VFD" and drive_fault and auto_transfer
    mode = "BYPASS" if (selector == "BYPASS" or transferred) else "VFD"
    if mode == "VFD":
        k1 = not estop
        k2 = demand and not drive_fault
        drive_run = k2
        k3 = False
    else:
        k1 = False  # the drive is isolated in bypass (and can be serviced)
        k2 = False
        drive_run = False
        k3 = demand and not bypass_overload
    return {
        "mode": mode, "transferred": transferred, "K1": k1, "K2": k2, "K3": k3, "drive_run": drive_run,
        "motor_running": bool(k3 or drive_run),
    }


def bypass_truth_table() -> CalcResult:
    """Every combination of selector, HOA, run command, drive fault, auto
    transfer, bypass overload and E-stop through :func:`bypass_logic`, checking
    that K2 and K3 are never closed together and that nothing runs on E-stop or
    OFF. ``value`` is the list of rows."""
    rows = []
    violations = []
    for sel, hoa, run, fault, auto, ol, es in itertools.product(
        ("VFD", "BYPASS"), ("HAND", "OFF", "AUTO"), (False, True), (False, True), (False, True), (False, True),
        (False, True),
    ):
        out = bypass_logic(sel, hoa, run, fault, auto, ol, es)
        row = {"selector": sel, "hoa": hoa, "run_cmd": run, "drive_fault": fault, "auto_transfer": auto,
               "bypass_overload": ol, "estop": es, **out}
        rows.append(row)
        if out["K2"] and out["K3"]:
            violations.append(row)
        if (es or hoa == "OFF") and out["motor_running"]:
            violations.append(row)
    return CalcResult(
        calc="bypass_truth_table",
        value=rows,
        unit="states",
        inputs={"states": make_input(len(rows), "", "prior_calc"), "violations": make_input(len(violations), "", "prior_calc")},
        formula="K2 and K3 never both closed ; E-stop or OFF -> motor stopped ; fault + auto transfer -> bypass",
        steps=[f"{len(rows)} states checked, {len(violations)} violation(s)"],
        citations=[CIT_BYPASS],
        warnings=[] if not violations else ["Interlock violation in the bypass logic."],
        status="good" if not violations else "bad",
    )


# --- components per topology ----------------------------------------------------------


def _component(tag, device, rating="", qty=1, note="", amps=None):
    return {"tag": tag, "device": device, "rating": rating, "qty": qty, "note": note, "amps": amps}


def pump_control(
    pump: dict,
    topology: str | None = None,
    *,
    feature: dict | None = None,
    site: dict | None = None,
    nema: bool = False,
    auto_transfer: bool | None = None,
) -> CalcResult:
    """The control scheme for one pump (a load row from ``electrical.build_loads``
    or the same keys) and the parts it takes. With no ``topology`` the recommender
    picks one. ``value`` is a dict: topology, label, components, contactor,
    overload, start_multiple, interlocks."""
    rec = recommend_topology(pump, feature, site)
    topo = topology if topology in TOPOLOGIES else rec.value
    flc, fla, fla_note = _pump_fla(pump)
    hp = num(pump.get("hp"))
    ph = phase_of(pump.get("phase"))
    v = num(pump.get("voltage"), 230)
    tag = pump.get("tag") or "M1"
    submersible = bool(pump.get("submersible"))
    comps: list[dict] = []
    interlocks: list[dict] = []
    contactor = nema_size(hp, v, ph) if (nema and hp) else iec_contactor(flc)
    ol = overload_relay(fla, submersible=submersible, nema=nema, service_factor=num(pump.get("service_factor"), 1.15))
    c_rating = (f"NEMA size {contactor.value}" if nema and hp else f"{contactor.value} A AC-3")
    # Contactor current, for footprints and SCCR: the NEMA size's continuous rating or the AC-3 frame.
    c_amps = NEMA_CONTINUOUS_A.get(contactor.value) if nema and hp else contactor.value
    if topo == "across_the_line":
        comps += [
            _component(f"{tag}-M", "Contactor", c_rating, 1, "across-the-line", c_amps),
            _component(f"{tag}-OL", "Overload relay", f"{ol.value}, set {fla:g} A, Class {ol.inputs['trip_class']['value']}"),
        ]
    elif topo == "soft_starter":
        ss = soft_starter(flc)
        comps += [
            _component(f"{tag}-SS", "Soft starter", f"{ss.value} A", 1, "internal run bypass; electronic overload set to FLA"),
        ]
    else:
        comps.append(_component(f"{tag}-VFD", "Variable frequency drive", f"{hp:g} HP class", 1,
                                "frame and derates in controls.vfd"))
        if topo == "vfd_bypass":
            k = c_rating
            comps += [
                _component(f"{tag}-K1", "Line contactor (drive input)", k, 1, "isolates the drive in bypass", c_amps),
                _component(f"{tag}-K2", "Output contactor (drive output)", k, 1,
                           "opens only with the drive stopped; interlocked with K3", c_amps),
                _component(f"{tag}-K3", "Bypass contactor (across the line)", k, 1,
                           "full-voltage duty; mechanically and electrically interlocked with K2", c_amps),
                _component(f"{tag}-OLB", "Overload relay (bypass path)",
                           f"{ol.value}, set {fla:g} A, Class {ol.inputs['trip_class']['value']}"),
                _component(f"{tag}-SS1", "Selector switch VFD / OFF / BYPASS", "22 mm, 3-position"),
            ]
            interlocks += [
                {"condition": f"{tag} K2 closed", "action": f"Block {tag} K3 (mechanical + electrical)",
                 "interlock_method": "Dry Contact NC", "enabled": 1},
                {"condition": f"{tag} drive fault", "action": f"Transfer {tag} to bypass" if auto_transfer
                 else f"Stop {tag}; alarm", "interlock_method": "Dry Contact NO", "enabled": 1},
                {"condition": f"{tag} bypass overload", "action": f"Stop {tag} in bypass",
                 "interlock_method": "Dry Contact NC", "enabled": 1},
            ]
    comps.append(_component(f"{tag}-HOA", "Selector switch Hand-Off-Auto", "22 mm, 3-position"))
    comps += [_component(f"{tag}-PL1", "Pilot light, run (green)", "22 mm LED"),
              _component(f"{tag}-PL2", "Pilot light, fault (red)", "22 mm LED")]
    warnings = []
    if topo in ("vfd", "vfd_bypass") and ph == 1:
        warnings.append(f"{pump.get('label')}: a drive needs a three-phase motor (fed from single-phase) or a "
                        "drive listed for the single-phase pump.")
    if topo == "vfd_bypass" and auto_transfer:
        warnings.append(
            "Automatic transfer starts the pump across the line at full speed: check water hammer and that a "
            "feature pump at full speed does not overspray. Keep it to circulation and filtration pumps."
        )
    if fla_note.startswith("no"):
        warnings.append(f"{pump.get('label')}: {fla_note}.")
    return CalcResult(
        calc="pump_control",
        value={
            "tag": tag,
            "key": pump.get("key"),
            "label": pump.get("label"),
            "role": (pump.get("role") or "").lower() or None,
            "hp": hp or None,
            "submersible": submersible,
            "topology": topo,
            "topology_label": TOPOLOGIES[topo],
            "recommended": rec.value,
            "components": comps,
            "contactor": contactor.value,
            "contactor_basis": "NEMA" if nema and hp else "IEC AC-3",
            "overload": {"range": ol.value, "setting_a": ol.inputs["setting_a"]["value"],
                         "class": ol.inputs["trip_class"]["value"], "max_trip_a": ol.inputs["max_trip_a"]["value"]},
            "start_multiple": START_MULTIPLE[topo],
            "starting_a": round(START_MULTIPLE[topo] * flc, 1),
            "interlocks": interlocks,
            "flc_a": flc,
            "fla_a": fla,
        },
        unit="",
        inputs={
            "hp": make_input(hp, "HP", "user"),
            "voltage": make_input(v, "V", "user"),
            "phase": make_input(ph, "", "user"),
            "flc_a": make_input(flc, "A", "lookup"),
            "fla_a": make_input(fla, "A", "lookup", fla_note),
        },
        formula="topology -> contactor (NEMA ICS 2 / IEC AC-3) + overload (430.32) + drive / soft starter + pilots",
        steps=[*rec.steps, f"{TOPOLOGIES[topo]}: " + ", ".join(c["device"] for c in comps)],
        citations=[CIT_TOPOLOGY, CIT_NEMA, CIT_IEC, CIT_OVERLOAD] + ([CIT_BYPASS] if topo == "vfd_bypass" else []),
        warnings=warnings + rec.warnings,
        options=rec.options,
    )


def recommend_topology(pump: dict, feature: dict | None = None, site: dict | None = None) -> CalcResult:
    """Pick a control scheme for a pump, with reasons.

    Speed control is needed when the pump already says ``control: vfd``, when its
    feature has wind stages (``wind_control``, the DOC-0123 two-setpoint wind
    screen), show modulation (``show_modulation``) or a pressure / flow setpoint
    (``setpoint``). A speed-controlled pump gets a bypass when it is a circulation
    or filtration pump, the venue is regulated, or it is 7.5 HP or more. Without a
    speed need, a pump of 15 HP or more, or 5 HP or more on generator power, gets a
    soft starter; anything else starts across the line. ``value`` is the topology
    key; ``options`` lists all four with the recommended one marked.
    """
    feature = feature or {}
    site = site or {}
    hp = num(pump.get("hp"))
    role = (pump.get("role") or "").lower()
    reasons: list[str] = []
    needs_speed = False
    if (pump.get("control") or "").lower() == "vfd":
        needs_speed = True
        reasons.append("the pump is specified with a VFD")
    if feature.get("wind_control"):
        needs_speed = True
        reasons.append("wind control ramps the pump to a windy speed at the medium setpoint (DOC-0123)")
    if feature.get("show_modulation"):
        needs_speed = True
        reasons.append("the show modulates jet height, which needs speed control")
    if feature.get("setpoint"):
        needs_speed = True
        reasons.append("a pressure or flow setpoint needs speed control")
    critical = role in CRITICAL_ROLES or bool(site.get("regulated"))
    if needs_speed:
        if critical:
            topo = "vfd_bypass"
            reasons.append("circulation / regulated venue: a bypass keeps water moving if the drive fails")
        elif hp >= BYPASS_MIN_HP - EPS:
            topo = "vfd_bypass"
            reasons.append(f"{hp:g} HP: a bypass keeps the feature running through a drive failure")
        else:
            topo = "vfd"
    else:
        on_gen = bool(site.get("generator"))
        if hp >= SOFT_START_MIN_HP - EPS:
            topo = "soft_starter"
            reasons.append(f"{hp:g} HP: a soft start limits inrush and water hammer")
        elif on_gen and hp >= 5 - EPS:
            topo = "soft_starter"
            reasons.append("on generator power: a soft start halves the starting surge")
        else:
            topo = "across_the_line"
            reasons.append("constant speed and small enough to start across the line")
    warnings = []
    if needs_speed and phase_of(pump.get("phase")) == 1:
        warnings.append("Speed control on a single-phase pump needs a three-phase motor or a single-phase pump drive.")
    options = [
        CalcOption(key=k, label=lbl, value=k, recommended=(k == topo),
                   detail={"start_multiple": START_MULTIPLE[k]})
        for k, lbl in TOPOLOGIES.items()
    ]
    return CalcResult(
        calc="recommend_topology",
        value=topo,
        unit="",
        inputs={
            "hp": make_input(hp, "HP", "user"),
            "role": make_input(role or None, "", "user"),
            "needs_speed": make_input(needs_speed, "", "prior_calc"),
            "critical": make_input(critical, "", "prior_calc"),
        },
        formula="speed need -> VFD (+ bypass if critical or >= 7.5 HP) ; else >= 15 HP (or >= 5 HP on generator) -> "
        "soft starter ; else across the line",
        steps=[f"{pump.get('label') or 'pump'}: {TOPOLOGIES[topo]} because " + "; ".join(reasons)],
        citations=[CIT_TOPOLOGY],
        warnings=warnings,
        options=options,
    )


__all__ = [
    "TOPOLOGIES",
    "bypass_logic",
    "bypass_truth_table",
    "iec_contactor",
    "nema_size",
    "overload_relay",
    "pump_control",
    "recommend_topology",
    "soft_starter",
]
