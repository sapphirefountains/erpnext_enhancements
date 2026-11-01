"""Drive frame selection for the control panel: the HP class and rated current a
drive needs after the altitude and ambient derates, single-phase input, its input
protection, reactors and filters by motor-lead length, carrier frequency, heat
loss, and the bypass contactors.

It builds on ``electrical.vfd.select_vfd`` (drive HP, input current, the 430.122
conductor minimum) and adds what the panel needs. The Wasatch Front sits at 4,300
to 5,000 ft, so the altitude derate (1% per 330 ft above 3,300 ft, typical of the
drive makers) applies to nearly every Sapphire job.
"""

from __future__ import annotations

from ..data.motor_control import (
    ALTITUDE_DERATE_PCT_PER_330FT,
    ALTITUDE_DERATE_START_FT,
    AMBIENT_DERATE_PCT_PER_C,
    AMBIENT_DERATE_START_F,
    AMBIENT_MAX_F,
    CARRIER_DEFAULT_KHZ,
    CIT_VFD_DERATE,
    VFD_CLASS_COLUMN,
    VFD_LOSS_PCT,
)
from ..data.nec_tables import FLC_3PH, VERIFY_EDITION
from ..electrical._util import EPS, next_standard, num, phase_of
from ..electrical.loads import motor_flc
from ..electrical.vfd import STD_VFD_HP, select_vfd
from ..envelope import CalcResult, make_input
from .topology import iec_contactor

CIT_VFD_PANEL = "NEC 430.122 / 430.130 (drive conductors and OCPD per its listing); NEMA MG 1 Part 31 (inverter-duty motors)"
INPUT_FUSE_FACTOR = 1.5  # input fuses ~150% of rated input current, within the drive's listed maximum (typical)


def voltage_class(voltage: float) -> str:
    v = num(voltage, 230)
    if v >= 525:
        return "525-600"
    if v >= 380:
        return "380-480"
    return "200-240"


def drive_rated_current(hp: float, voltage: float) -> float | None:
    """Rated output current of a drive of this HP class (the class's low-voltage
    FLC column, how drive catalogs rate)."""
    col = VFD_CLASS_COLUMN[voltage_class(voltage)]
    row = FLC_3PH.get(float(hp))
    return row.get(col) if row else None


def altitude_factor(elevation_ft: float) -> float:
    over = max(0.0, num(elevation_ft) - ALTITUDE_DERATE_START_FT)
    return max(0.0, 1.0 - (over / 330.0) * ALTITUDE_DERATE_PCT_PER_330FT / 100.0)


def ambient_factor(ambient_f: float) -> float | None:
    """Ambient derate factor; None above the drive's 50 C limit."""
    a = num(ambient_f, 86.0)
    if a > AMBIENT_MAX_F + EPS:
        return None
    over_c = max(0.0, (a - AMBIENT_DERATE_START_F) / 1.8)
    return 1.0 - over_c * AMBIENT_DERATE_PCT_PER_C / 100.0


def vfd_frame(
    hp: float,
    voltage: float = 230,
    phase_in: int = 3,
    *,
    fla: float | None = None,
    elevation_ft: float = 4500.0,
    ambient_f: float = 104.0,
    cable_ft: float = 0.0,
    carrier_khz: float = CARRIER_DEFAULT_KHZ,
    submersible: bool = False,
    bypass: bool = False,
) -> CalcResult:
    """Drive frame for a three-phase motor of ``hp`` (``fla`` overrides the
    table current). Required drive current = motor current / (altitude factor x
    ambient factor), doubled on single-phase input; the frame is the smallest HP
    class whose rated current covers it. ``value`` is the frame HP."""
    base = select_vfd(hp, voltage, phase_in, cable_ft=cable_ft, submersible=submersible, fla=fla)
    motor_hp = num(base.inputs["motor_hp"]["value"])
    motor_a = num(fla) or (motor_flc(motor_hp, 3, voltage) or 0.0)
    af = altitude_factor(elevation_ft)
    tf = ambient_factor(ambient_f)
    warnings = list(base.warnings)
    if tf is None:
        warnings.append(f"{num(ambient_f):g} F is over the drive's 50 C (122 F) rating: cool the enclosure.")
        tf = ambient_factor(AMBIENT_MAX_F)
    pin = phase_of(phase_in)
    single = 2.0 if pin == 1 else 1.0
    need_a = motor_a * single / (af * tf) if af * tf > 0 else motor_a
    frame = next(
        (h for h in STD_VFD_HP if h in FLC_3PH and (drive_rated_current(h, voltage) or 0) >= need_a - EPS),
        None,
    )
    if frame is None:
        warnings.append(f"{need_a:.1f} A is past the drive table; size from the maker's catalog.")
    in_a = num(base.inputs["input_a_est"]["value"])  # input current follows the load, not the derate
    fuse = next_standard(INPUT_FUSE_FACTOR * in_a) if in_a else None
    kw = motor_hp * 0.7457
    loss_w = kw * 1000.0 * VFD_LOSS_PCT / 100.0
    notes = list(base.steps[3:])
    if num(carrier_khz) > CARRIER_DEFAULT_KHZ + EPS:
        notes.append(f"carrier {num(carrier_khz):g} kHz: quieter, but more drive derate and more reflected-wave "
                     "stress; keep 4 kHz or less on long leads")
    if voltage_class(voltage) != "200-240" or num(cable_ft) > 50:
        notes.append("specify an inverter-duty motor (NEMA MG 1 Part 31) at 460 V or on leads over ~50 ft")
    bypass_contactor = iec_contactor(motor_a) if bypass else None
    if bypass_contactor:
        notes.append(f"bypass, output and line contactors: {bypass_contactor.value} A AC-3 (motor FLC {motor_a:g} A)")
    return CalcResult(
        calc="vfd_frame",
        value=frame,
        unit="HP frame",
        inputs={
            "motor_hp": make_input(motor_hp, "HP", "user"),
            "motor_a": make_input(round(motor_a, 2), "A", "lookup", "nameplate" if fla else "Table 430.250"),
            "voltage_class": make_input(voltage_class(voltage), "V", "prior_calc"),
            "phase_in": make_input(pin, "", "user"),
            "elevation_ft": make_input(num(elevation_ft), "ft", "user"),
            "altitude_factor": make_input(round(af, 4), "", "standard", "1% / 330 ft over 3,300 ft"),
            "ambient_f": make_input(num(ambient_f), "F", "user", "inside the enclosure"),
            "ambient_factor": make_input(round(tf, 4), "", "standard", "2% / C over 40 C"),
            "required_a": make_input(round(need_a, 2), "A", "prior_calc"),
            "frame_rated_a": make_input(drive_rated_current(frame, voltage) if frame else None, "A", "lookup"),
            "input_a_est": make_input(round(in_a, 2), "A", "prior_calc"),
            "input_fuse_a": make_input(fuse, "A", "prior_calc", "~150% of input; the listing's max governs"),
            "loss_w": make_input(round(loss_w, 1), "W", "prior_calc", f"{VFD_LOSS_PCT:g}% of output"),
            "lead_treatment": base.inputs["lead_treatment"],
            "line_reactor": base.inputs["line_reactor"],
            "bypass_contactor_a": make_input(bypass_contactor.value if bypass_contactor else None, "A", "prior_calc"),
            "carrier_khz": make_input(num(carrier_khz), "kHz", "default"),
        },
        formula=(
            "required A = motor A x (2 if 1-phase input) / (altitude factor x ambient factor) ; "
            "frame = smallest HP class with rated A >= required"
        ),
        steps=[f"motor {motor_hp:g} HP, {motor_a:g} A ; altitude {num(elevation_ft):g} ft -> x{af:.3f} ; " f"ambient {num(ambient_f):g} F -> x{tf:.3f}", f"required {need_a:.1f} A -> {frame} HP frame ({drive_rated_current(frame, voltage) if frame else '?'} A)", f"input ~{in_a:.1f} A -> input fuses {fuse} A (per the drive's listing, 430.130)", *notes],
        citations=[CIT_VFD_PANEL, CIT_VFD_DERATE, *base.citations],
        warnings=list(dict.fromkeys([*warnings, VERIFY_EDITION])),
    )


__all__ = ["altitude_factor", "ambient_factor", "drive_rated_current", "vfd_frame", "voltage_class"]
