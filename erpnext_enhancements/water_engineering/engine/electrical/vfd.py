"""Variable-frequency drive selection: drive HP for the motor, input current and
conductors (430.122), the OCPD (430.130: the drive maker's instructions), and the
reactor / filter notes for the line side and long motor leads.

The reactor and lead-length thresholds are common drive-manufacturer guidance,
not NEC rules; every one is labeled so and the drive's manual governs.
"""

from __future__ import annotations

import math

from ..data.nec_tables import FLC_3PH
from ..envelope import CalcResult, make_input
from ._util import edition_warning, num, phase_of
from .loads import motor_flc, standard_motor_hp

STD_VFD_HP = [0.5, 0.75, 1, 1.5, 2, 3, 5, 7.5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 100]
SINGLE_PHASE_INPUT_DERATE = 2.0  # drive HP >= 2 x motor HP on single-phase input (typical; drive maker governs)
LOAD_REACTOR_FT = 100  # motor leads beyond ~100 ft: load reactor (typical)
DVDT_FILTER_FT = 300  # beyond ~300 ft: dV/dt filter (typical, 460 V)
SINE_FILTER_FT = 1000  # beyond ~1000 ft: sine-wave filter (typical)
CIT_VFD = "NEC 430.122 (conductors at 125% of rated input) / 430.130 (OCPD per the drive's instructions)"
CIT_VFD_GUIDE = "Drive-manufacturer application guidance (reactors, filters, derating); not NEC"


def select_vfd(
    hp: float,
    voltage: float = 230,
    phase_in: int = 3,
    *,
    motor_phase: int = 3,
    cable_ft: float = 0.0,
    submersible: bool = False,
    drives_on_bus: int = 1,
    fla: float | None = None,
) -> CalcResult:
    """Drive for a three-phase motor of ``hp`` at ``voltage``. ``value`` is the
    drive HP rating; inputs carry the output current (motor FLC), an estimated
    input current, the 430.122 conductor minimum, and the reactor / filter calls."""
    h = num(hp)
    pin = phase_of(phase_in)
    warnings = []
    if phase_of(motor_phase) == 1:
        warnings.append(
            "Single-phase motors (PSC, capacitor-start) cannot run on a standard VFD; specify a three-phase "
            "motor or a drive made for the single-phase pump."
        )
    motor_hp = standard_motor_hp(h, 3) or h
    if motor_hp <= 0 and num(fla) > 0:
        # No HP: the smallest Table 430.250 motor whose FLC covers the nameplate current.
        motor_hp = next((k for k in sorted(FLC_3PH) if (motor_flc(k, 3, voltage) or 0) >= num(fla)), 0.0)
        warnings.append(f"No motor HP given; drive sized for a {motor_hp:g} HP motor from {num(fla):g} A.")
    if motor_hp <= 0:
        warnings.append("No motor HP or current given; the drive cannot be sized.")
    flc = motor_flc(motor_hp, 3, voltage) or 0.0
    need_hp = motor_hp * (SINGLE_PHASE_INPUT_DERATE if pin == 1 else 1.0)
    drive_hp = next((s for s in STD_VFD_HP if s >= need_hp - 1e-9), None) if need_hp > 0 else None
    if drive_hp is None and need_hp > 0:
        drive_hp = math.ceil(need_hp)
        warnings.append(f"{need_hp:g} HP is past the standard drive list; size from the maker's catalog.")
    if motor_hp not in FLC_3PH:
        warnings.append(f"{motor_hp:g} HP is not a Table 430.250 size; use the motor nameplate current.")
    # Input current from conservation of kVA (output at the motor's FLC, ~97% drive efficiency);
    # the drive's rated input current replaces this estimate (430.122(A)).
    out_kva = math.sqrt(3) * num(voltage) * flc / 1000.0
    in_a = out_kva * 1000.0 / 0.97 / (num(voltage) * (math.sqrt(3) if pin == 3 else 1.0)) if num(voltage) else 0.0
    cond_a = 1.25 * in_a
    line_reactor = pin == 1 or int(num(drives_on_bus, 1)) > 1
    cable = num(cable_ft)
    lead = "none"
    if cable > SINE_FILTER_FT:
        lead = "sine-wave filter"
    elif cable > DVDT_FILTER_FT:
        lead = "dV/dt filter"
    elif cable > LOAD_REACTOR_FT:
        lead = "load reactor"
    notes = [
        "3% line reactor recommended" + (" (several drives on one bus or single-phase input)" if line_reactor else
                                         " where the supply transformer is large relative to the drive"),
        f"motor leads {cable:g} ft -> {lead}" if cable else "motor-lead length not given",
    ]
    if submersible:
        notes.append(
            "Submersible pump on a drive: follow the pump maker's drive rules (output filter or reactor, minimum "
            "frequency, accel/decel ramps); the drop cable counts in the lead length."
        )
    warnings += [
        "The OCPD for a drive's branch circuit is the one its instructions name (430.130); the 430.52 value "
        "shown on the circuit is a placeholder until the drive is chosen.",
        edition_warning(),
    ]
    return CalcResult(
        calc="select_vfd",
        value=drive_hp,
        unit="HP",
        inputs={
            "motor_hp": make_input(motor_hp, "HP", "user"),
            "voltage": make_input(num(voltage), "V", "user"),
            "phase_in": make_input(pin, "", "user"),
            "output_a": make_input(flc, "A", "lookup", "Table 430.250"),
            "input_a_est": make_input(round(in_a, 2), "A", "prior_calc", "estimate; drive nameplate governs"),
            "conductor_min_a": make_input(round(cond_a, 2), "A", "prior_calc", "430.122(A), 125% of input"),
            "line_reactor": make_input(line_reactor, "", "prior_calc"),
            "lead_treatment": make_input(lead, "", "prior_calc"),
            "cable_ft": make_input(cable, "ft", "user"),
        },
        formula=(
            "drive HP >= motor HP (x2 on single-phase input) ; I_in ~ sqrt(3) V FLC / 0.97 / (V x 1 or sqrt(3)) ; "
            "conductors >= 1.25 x I_in"
        ),
        steps=[f"motor {motor_hp:g} HP 3ph at {num(voltage):g} V: FLC {flc:g} A (Table 430.250)", f"drive {drive_hp:g} HP ({'single-phase input, 2x derate' if pin == 1 else 'three-phase input'})", f"input ~{in_a:.1f} A -> conductors >= {cond_a:.1f} A (430.122)", *notes],
        citations=[CIT_VFD, CIT_VFD_GUIDE],
        warnings=warnings,
    )


__all__ = ["STD_VFD_HP", "select_vfd"]
