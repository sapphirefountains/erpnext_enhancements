"""Control panel design: control transformer, enclosure size and NEMA type, heat
load (with the fan or air conditioner it calls for), short-circuit current rating
per UL 508A Supplement SB, main disconnect, surge protection, GFCI / GFPE
devices, terminal strips and internal wire.

Footprints, heat losses and default device ratings are typical figures
(``data/motor_control.py``). They give a first enclosure and a thermal check
that flags trouble; the panel builder's layout and the components' datasheets
replace them.
"""

from __future__ import annotations

import math

from ..data.motor_control import (
    CFM_PER_W_PER_F,
    CIT_ENCLOSURE_TYPE,
    CIT_SCCR,
    CIT_THERMAL,
    ENCLOSURE_K_W_PER_M2K,
    ENCLOSURE_PARTS,
    ENCLOSURE_SIZES_IN,
    ENCLOSURE_TYPE_BY_LOCATION,
    FOOTPRINT_IN2,
    IN2_PER_M2,
    MAX_INTERNAL_F,
    PSU_EFF,
    SB4_DEFAULT_KA,
    SOLAR_GAIN_F,
    SPARE_FACTOR,
    VFD_FOOTPRINT_IN2,
    WIREWAY_FACTOR,
)
from ..data.nec_tables import VERIFY_EDITION
from ..electrical._util import EPS, num
from ..electrical.transformers import control_transformer_va
from ..envelope import CalcResult, make_input

DISCONNECT_SIZES_A = [30, 60, 100, 200, 400, 600]
CONTACTOR_COIL_W = 5.0  # sealed coil dissipation per contactor (typical)
OVERLOAD_W = 2.0
BREAKER_POLE_W = 2.0
SSR_W = 1.0
XFMR_LOSS_FRACTION = 0.12  # small control transformers run ~88% efficient
BTU_PER_W = 3.412
CIT_DISCONNECT = "NEC 430.109 / 430.110 (disconnect >= 115% of the motor FLC) / 409.104"
CIT_SPD = "NEC Article 285 / UL 1449 (SPD); recommended for controllers and drives"
CIT_WIRE = "UL 508A 66 (internal wiring) / NFPA 79 colors: black power, red AC control, blue DC control"


def panel_control_transformer(
    pump_controls: list[dict] | None,
    *,
    relays: int = 0,
    platform: str = "Splash Wizard",
    psu_input_w: float = 0.0,
    fill_valve_24vac: bool = False,
) -> CalcResult:
    """Control transformer from the panel's own parts: a coil per contactor,
    pilot lights, relays, the controller (HMI or PLC) and the 24 V DC supply's
    input, through ``electrical.transformers.control_transformer_va``."""
    pcs = pump_controls or []
    contactors = sum(c["qty"] for pc in pcs for c in pc.get("components", []) if "contactor" in c["device"].lower())
    pilots = sum(c["qty"] for pc in pcs for c in pc.get("components", []) if c["device"].lower().startswith("pilot"))
    rows = []
    if contactors:
        rows.append({"device": "contactor", "qty": contactors})
    if pilots:
        rows.append({"device": "pilot_light", "qty": pilots})
    if relays:
        rows.append({"device": "relay", "qty": relays})
    rows.append({"device": "plc" if platform == "Industrial" else "hmi", "qty": 1})
    if fill_valve_24vac:
        rows.append({"device": "solenoid", "qty": 1})
    if num(psu_input_w) > 0:
        rows.append({"device": "dc_supply", "qty": 1, "va": round(num(psu_input_w) / 0.9, 1)})  # ~0.9 pf
    r = control_transformer_va(rows)
    r.inputs["devices"] = make_input(rows, "", "prior_calc")
    return r


def enclosure_type(location: str = "outdoor", near_chemicals: bool = False) -> CalcResult:
    """NEMA type by location: 3R outdoors, 4X near pool chemicals or in a pool
    equipment room (corrosive, 680.14), 12 indoors."""
    loc = (location or "outdoor").lower()
    key = "chemical" if near_chemicals else (loc if loc in ENCLOSURE_TYPE_BY_LOCATION else "outdoor")
    t = ENCLOSURE_TYPE_BY_LOCATION[key]
    material = "stainless or fiberglass" if t == "NEMA 4X" else "painted steel"
    return CalcResult(
        calc="enclosure_type",
        value=t,
        unit="",
        inputs={"location": make_input(loc, "", "user"), "near_chemicals": make_input(bool(near_chemicals), "", "user")},
        formula="outdoor -> 3R ; chemicals / pool equipment room -> 4X ; indoor -> 12",
        steps=[f"{loc}{', near chemicals' if near_chemicals else ''} -> {t} ({material})"],
        citations=[CIT_ENCLOSURE_TYPE],
    )


def _vfd_area(hp: float) -> float:
    return next((a for h, a in VFD_FOOTPRINT_IN2 if num(hp) <= h + EPS), VFD_FOOTPRINT_IN2[-1][1])


def enclosure_size(
    pump_controls: list[dict] | None,
    *,
    vfd_hps: list[float] | None = None,
    breaker_poles: int = 0,
    terminals: int = 0,
    relays: int = 0,
    psu_w: float = 0.0,
    xfmr_va: float = 0.0,
    spd: bool = True,
    disconnect: bool = True,
) -> CalcResult:
    """Smallest standard enclosure whose backplane holds the components plus 30%
    for wire duct and spacing plus 20% spare. ``value`` is "H x W x D in"."""
    parts: dict[str, float] = {}

    def add(k, v):
        if v > 0:
            parts[k] = parts.get(k, 0.0) + v

    for pc in pump_controls or []:
        for c in pc.get("components", []):
            d = c["device"].lower()
            if "contactor" in d:
                big = num(c.get("amps")) > 40
                add("contactors", c["qty"] * FOOTPRINT_IN2["contactor_large" if big else "contactor_small"])
            elif d.startswith("overload"):
                add("overloads", c["qty"] * FOOTPRINT_IN2["overload"])
            elif d.startswith("soft starter"):
                add("soft starters", c["qty"] * FOOTPRINT_IN2["soft_starter"])
    for hp in vfd_hps or []:
        add("drives", _vfd_area(hp))
    add("breakers", breaker_poles * FOOTPRINT_IN2["breaker_pole"])
    add("terminals", terminals * FOOTPRINT_IN2["terminal"])
    add("relays", relays * FOOTPRINT_IN2["relay"])
    add("24 V DC supply", math.ceil(num(psu_w) / 100.0) * FOOTPRINT_IN2["psu_per_100w"])
    add("control transformer", max(1, math.ceil(num(xfmr_va) / 100.0)) * FOOTPRINT_IN2["xfmr_per_100va"] if xfmr_va else 0)
    add("controller", FOOTPRINT_IN2["controller"])
    if spd:
        add("SPD", FOOTPRINT_IN2["spd"])
    if disconnect:
        add("main disconnect", FOOTPRINT_IN2["disconnect"])
    comp = sum(parts.values())
    need = comp * (1 + WIREWAY_FACTOR) * (1 + SPARE_FACTOR)
    chosen = next((s for s in ENCLOSURE_SIZES_IN if (s[0] - 2) * (s[1] - 2) >= need - EPS), None)
    warnings = []
    if chosen is None:
        chosen = ENCLOSURE_SIZES_IN[-1]
        warnings.append(f"{need:.0f} in^2 is past a single 72 x 36 enclosure: split into two sections or a floor-standing panel.")
    part = ENCLOSURE_PARTS.get(chosen)
    warnings.append("Footprints are typical; confirm with the panel layout (large drives may need their own enclosure).")
    return CalcResult(
        calc="enclosure_size",
        value=f"{chosen[0]} x {chosen[1]} x {chosen[2]} in",
        unit="H x W x D",
        inputs={
            "component_in2": make_input(round(comp, 1), "in^2", "prior_calc"),
            "required_in2": make_input(round(need, 1), "in^2", "prior_calc", "x1.3 wire duct x1.2 spare"),
            "backplane_in2": make_input((chosen[0] - 2) * (chosen[1] - 2), "in^2", "lookup"),
            "dims_in": make_input(list(chosen), "in", "lookup"),
            "part": make_input(part, "", "lookup", "DOC-0028 / IDP009" if part else ""),
            "breakdown_in2": make_input({k: round(v, 1) for k, v in parts.items()}, "in^2", "prior_calc"),
        },
        formula="backplane >= sum(footprints) x (1 + 0.30 wire duct) x (1 + 0.20 spare)",
        steps=[f"{k}: {v:.0f} in^2" for k, v in parts.items()]
        + [f"{comp:.0f} x 1.3 x 1.2 = {need:.0f} in^2 -> {chosen[0]} x {chosen[1]} x {chosen[2]} in"
           + (f" ({part['enclosure']})" if part else "")],
        citations=["Enclosure-maker layout practice (wire duct, 20% spare)"],
        warnings=warnings,
    )


def enclosure_thermal(
    dims_in: list[float] | tuple,
    heat_w: float,
    *,
    ambient_f: float = 95.0,
    outdoor_sun: bool = True,
    material: str = "steel",
    max_internal_f: float = MAX_INTERNAL_F,
    wall_mounted: bool = True,
) -> CalcResult:
    """Can the enclosure shed its heat by natural convection? Exposed area (front,
    top and two sides of a wall-mounted box) x k x the allowed rise must cover the
    internal heat. If not: a filtered fan (CFM = 3.17 x W / allowed rise) when the
    ambient is under the internal limit, otherwise an air conditioner sized in
    BTU/h. ``status`` is good / bad, ``value`` the internal rise (deg F)."""
    h, w, d = (num(x) for x in dims_in)
    area_in2 = w * h + w * d + 2 * h * d + (0 if wall_mounted else w * h)
    area_m2 = area_in2 / IN2_PER_M2
    k = ENCLOSURE_K_W_PER_M2K.get(material, ENCLOSURE_K_W_PER_M2K["steel"])
    amb = num(ambient_f, 95.0) + (SOLAR_GAIN_F if outdoor_sun else 0.0)
    allowed_f = num(max_internal_f) - amb
    capacity_w = k * area_m2 * max(allowed_f, 0.0) / 1.8
    q = num(heat_w)
    rise_f = (q / (k * area_m2)) * 1.8 if area_m2 > 0 else 0.0
    ok = q <= capacity_w + EPS
    remedy = None
    cfm = btu = None
    if not ok:
        if allowed_f > 5:
            cfm = math.ceil(CFM_PER_W_PER_F * q / allowed_f)
            remedy = f"a filtered fan of at least {cfm} CFM (rated for the enclosure type)"
        else:
            btu = math.ceil(q * BTU_PER_W * 1.1)
            remedy = f"an enclosure air conditioner of at least {btu} BTU/h"
    return CalcResult(
        calc="enclosure_thermal",
        value=round(rise_f, 1),
        unit="F rise",
        inputs={
            "heat_w": make_input(round(q, 1), "W", "prior_calc"),
            "exposed_area_ft2": make_input(round(area_in2 / 144.0, 2), "ft^2", "prior_calc"),
            "k_w_m2k": make_input(k, "W/m^2K", "standard", material),
            "design_ambient_f": make_input(round(amb, 1), "F", "user", "+15 F sun" if outdoor_sun else ""),
            "allowed_rise_f": make_input(round(allowed_f, 1), "F", "prior_calc", f"to {max_internal_f:g} F inside"),
            "capacity_w": make_input(round(capacity_w, 1), "W", "prior_calc"),
            "fan_cfm": make_input(cfm, "CFM", "prior_calc"),
            "ac_btu_h": make_input(btu, "BTU/h", "prior_calc"),
        },
        formula="capacity = k x A x (T_max - T_ambient) ; fan CFM = 3.17 x W / dT(F) ; AC BTU/h = 1.1 x 3.412 x W",
        steps=[
            f"A = {area_in2 / 144.0:.1f} ft^2 ({area_m2:.2f} m^2), k = {k} W/m^2K",
            f"allowed rise {allowed_f:.1f} F -> sheds {capacity_w:.0f} W naturally ; internal heat {q:.0f} W",
            "natural convection is enough" if ok else f"over by {q - capacity_w:.0f} W -> {remedy}",
        ],
        citations=[CIT_THERMAL],
        warnings=([] if ok else [f"Enclosure overheats: add {remedy}. A fan lowers a 4X rating; use an air "
                                 "conditioner or heat exchanger there."]),
        status="good" if ok else "bad",
    )


def panel_heat(
    vfd_losses_w: list[float] | None = None,
    *,
    xfmr_va: float = 0.0,
    psu_w: float = 0.0,
    contactors: int = 0,
    overloads: int = 0,
    breaker_poles: int = 0,
    ssr: int = 0,
    soft_starter_w: float = 0.0,
) -> CalcResult:
    """Heat dissipated inside the enclosure (W)."""
    parts = {
        "drives": sum(num(x) for x in vfd_losses_w or []),
        "control transformer": num(xfmr_va) * XFMR_LOSS_FRACTION,
        "24 V DC supply": num(psu_w) * (1 / PSU_EFF - 1),
        "contactor coils": contactors * CONTACTOR_COIL_W,
        "overloads": overloads * OVERLOAD_W,
        "breakers": breaker_poles * BREAKER_POLE_W,
        "SSRs": ssr * SSR_W,
        "soft starters": num(soft_starter_w),
    }
    total = sum(parts.values())
    return CalcResult(
        calc="panel_heat",
        value=round(total, 1),
        unit="W",
        inputs={"breakdown_w": make_input({k: round(v, 1) for k, v in parts.items() if v}, "W", "prior_calc")},
        formula="drives (3% of kW) + transformer (12% of VA) + supply losses + coils + overloads + breakers + SSRs",
        steps=[f"{k}: {v:.1f} W" for k, v in parts.items() if v] + [f"total {total:.1f} W"],
        citations=["Typical component dissipation (maker datasheets replace these)"],
    )


def sccr(components: list[dict] | None, available_fault_ka: float | None = None) -> CalcResult:
    """Panel short-circuit current rating: the lowest-rated power-circuit
    component governs (UL 508A SB4.2). Each component is ``{name, kind, ka}``;
    ``ka`` overrides the SB4.1 default for its ``kind``. Fails when the available
    fault current at the panel exceeds it (NEC 409.22)."""
    rows = []
    for c in components or []:
        ka = num(c.get("ka")) or SB4_DEFAULT_KA.get(c.get("kind"), 0)
        if ka > 0:
            rows.append({"name": c.get("name"), "kind": c.get("kind"), "ka": ka})
    if not rows:
        return CalcResult(calc="sccr", unit="kA", formula="SCCR = min(component SCCR)", citations=[CIT_SCCR],
                          warnings=["No power-circuit components to rate."])
    weakest = min(rows, key=lambda r: r["ka"])
    avail = num(available_fault_ka)
    status = "warn"
    warnings = []
    if avail > 0:
        status = "good" if weakest["ka"] >= avail - EPS else "bad"
        if status == "bad":
            warnings.append(
                f"Available fault current {avail:g} kA exceeds the panel SCCR {weakest['ka']:g} kA ({weakest['name']}). "
                "Use combination-rated starters with Class J / CC fuses and drives listed with their fuses "
                "(typically 65-100 kA)."
            )
    else:
        warnings.append("Enter the available fault current at the panel (from the utility or a fault study) to "
                        "check the SCCR (NEC 409.22).")
    return CalcResult(
        calc="sccr",
        value=weakest["ka"],
        unit="kA",
        inputs={
            "components": make_input(rows, "", "prior_calc"),
            "governing": make_input(weakest["name"], "", "prior_calc"),
            "available_fault_ka": make_input(avail or None, "kA", "user"),
        },
        formula="SCCR = lowest SCCR of the power-circuit components (SB4.1 defaults when unmarked)",
        steps=[f"{r['name']}: {r['ka']:g} kA" for r in rows] + [f"panel SCCR {weakest['ka']:g} kA ({weakest['name']})"],
        citations=[CIT_SCCR],
        warnings=[*warnings, VERIFY_EDITION],
        status=status,
    )


def main_disconnect(motor_flcs: list[float] | None, other_a: float = 0.0, feeder_ocpd_a: float | None = None) -> CalcResult:
    """Main disconnect: at least 115% of the motors' FLC plus the other loads
    (430.110(C)), and at least the feeder OCPD it carries; next standard size."""
    flc = sum(num(x) for x in motor_flcs or [])
    need = max(1.15 * flc + num(other_a), num(feeder_ocpd_a))
    size = next((s for s in DISCONNECT_SIZES_A if s >= need - EPS), math.ceil(need))
    return CalcResult(
        calc="main_disconnect",
        value=size,
        unit="A",
        inputs={
            "motor_flc_a": make_input(round(flc, 2), "A", "prior_calc"),
            "other_a": make_input(round(num(other_a), 2), "A", "prior_calc"),
            "feeder_ocpd_a": make_input(num(feeder_ocpd_a) or None, "A", "prior_calc"),
        },
        formula="rating >= max(1.15 x sum(motor FLC) + other loads, feeder OCPD) ; next standard switch size",
        steps=[f"1.15 x {flc:.1f} + {num(other_a):.1f} = {1.15 * flc + num(other_a):.1f} A ; feeder OCPD "
               f"{num(feeder_ocpd_a):g} A -> {size} A"],
        citations=[CIT_DISCONNECT],
        warnings=["A fused disconnect (Class J) also raises the panel SCCR; a lockable handle is required (430.102)."],
    )


def terminals_and_wire(circuits: list[dict] | None, io_rows: list[dict] | None) -> CalcResult:
    """Terminal blocks (power: each circuit's conductors and EGC; control: two per
    I/O point; +20% spare) and the internal wire sizes and colors."""
    power = sum(int(num(c.get("current_carrying"), 2)) + 1 for c in circuits or [])
    control = 2 * sum(int(num(r.get("qty"), 1)) for r in io_rows or [])
    total = math.ceil((power + control) * (1 + SPARE_FACTOR))
    return CalcResult(
        calc="terminals_and_wire",
        value=total,
        unit="terminals",
        inputs={
            "power_terminals": make_input(power, "", "prior_calc"),
            "control_terminals": make_input(control, "", "prior_calc"),
            "wire": make_input({
                "control_ac": "16 AWG MTW, red", "control_dc": "16 AWG MTW, blue", "neutral": "white",
                "ground": "green", "power": "per branch conductor, 14 AWG MTW minimum, black",
            }, "", "standard"),
        },
        formula="terminals = (sum(conductors + EGC per circuit) + 2 x I/O points) x 1.2",
        steps=[f"power {power} + control {control} = {power + control} -> {total} with 20% spare"],
        citations=[CIT_WIRE],
    )


def surge_protection(service_v: float, phase: int) -> CalcResult:
    """A Type 1 or 2 SPD on the panel's line side, for the controller and drives."""
    return CalcResult(
        calc="surge_protection",
        value="Type 2 SPD",
        unit="",
        inputs={"service_v": make_input(num(service_v), "V", "user"), "phase": make_input(phase, "", "user")},
        formula="Type 2 SPD (UL 1449) on the line side of the control panel",
        steps=[f"{num(service_v):g} V {phase}ph -> Type 2 SPD rated for the system, 20 kA nominal discharge or more"],
        citations=[CIT_SPD],
        warnings=["Recommended, not required by the NEC outside dwellings; electronics and drives on fountain sites "
                  "see lightning and utility surges."],
    )


__all__ = [
    "enclosure_size",
    "enclosure_thermal",
    "enclosure_type",
    "main_disconnect",
    "panel_control_transformer",
    "panel_heat",
    "sccr",
    "surge_protection",
    "terminals_and_wire",
]
