"""Branch-circuit overcurrent protection: motors per NEC 430.52, other loads per
210.20(A) / 215.3, both landing on the standard sizes of 240.6(A).

The correction this module exists for: the engine used to call 125% of FLA the
motor *breaker* (citing 430.52). 125% is the 430.22 *conductor* rule. The motor
branch short-circuit and ground-fault device is set by Table 430.52(C)(1), up to
250% of FLC for an inverse-time breaker, and 430.52(C)(1) Exception 1 lets a value
that is not a standard size go to the next standard size up. A breaker at 125%
is legal but will often trip on a pump's starting current; the table value is the
ceiling the code allows. :func:`motor_branch_ocpd` returns that ceiling.

Overload protection is separate (430.32: 115% or 125% of nameplate FLA, usually
the starter's overload relay or the VFD) and is not sized here.
"""

from __future__ import annotations

from ..data.nec_tables import (
    CIT_MOTOR_OCPD,
    CIT_STD_OCPD,
    MOTOR_OCPD_EXC2_PERCENT,
    MOTOR_OCPD_PERCENT,
    OCPD_DEVICES,
    STD_AMPS_240_6,
    STD_FUSE_EXTRA_AMPS,
)
from ..envelope import CalcResult, make_input
from ._util import edition_warning, next_standard, num, phase_of, standard_not_exceeding

CONDUCTOR_FACTOR_430_22 = 1.25  # NEC 430.22: motor branch conductors >= 125% of FLC
CONTINUOUS_FACTOR = 1.25  # NEC 210.19(A)(1) / 210.20(A) / 215.2 / 215.3: continuous loads at 125%
MIN_BREAKER_A = 15
CIT_CONTINUOUS = "NEC 210.20(A) / 215.3 (OCPD for continuous loads at 125%)"
CIT_MOTOR_CONDUCTOR = "NEC 430.22 (motor branch conductors at 125% of FLC)"


def standard_sizes(device: str = "inverse_time_breaker") -> list[int]:
    """240.6(A) ratings for the device type: fuses add 1, 3, 6, 10 and 601 A."""
    if "fuse" in (device or ""):
        return sorted(set(STD_AMPS_240_6) | set(STD_FUSE_EXTRA_AMPS))
    return list(STD_AMPS_240_6)


def motor_type_for(phase: int = 1, motor_type: str | None = None) -> str:
    """Table 430.52(C)(1) row: the given type, else single-phase or squirrel cage."""
    if motor_type and motor_type in MOTOR_OCPD_PERCENT:
        return motor_type
    return "squirrel_cage" if phase_of(phase) == 3 else "single_phase"


def motor_branch_ocpd(
    flc_amps: float,
    *,
    device: str = "inverse_time_breaker",
    phase: int = 1,
    motor_type: str | None = None,
    hp: float = 0.0,
    voltage: float = 0.0,
) -> CalcResult:
    """Maximum motor branch-circuit short-circuit and ground-fault protective
    device (A) per NEC 430.52(C)(1) and Table 430.52(C)(1).

    ``flc_amps`` is the table FLC (430.248 / 430.250, per 430.6(A)(1)), or the
    nameplate FLA for a motor below the table. The result is the table percentage
    of FLC, raised to the next standard size when it falls between sizes
    (Exception 1). ``inputs['exception_2_max_a']`` carries the absolute limit of
    Exception 2 for a motor that will not start on the table value.
    """
    flc = num(flc_amps)
    device = device if device in OCPD_DEVICES else "inverse_time_breaker"
    row = motor_type_for(phase, motor_type)
    pct = MOTOR_OCPD_PERCENT[row][device]
    sizes = standard_sizes(device)
    if flc <= 0:
        return CalcResult(
            calc="motor_branch_ocpd",
            unit="A",
            formula="OCPD max = FLC x Table 430.52(C)(1) % ; next standard size up (Exception 1)",
            citations=[CIT_MOTOR_OCPD, CIT_STD_OCPD],
            warnings=["No motor full-load current supplied."],
        )
    raw = flc * pct / 100.0
    # Breakers start at 15 A; fuses come as small as 1 A (240.6(A)).
    rating = max(next_standard(raw, sizes), 1 if "fuse" in device else MIN_BREAKER_A)
    if device == "instantaneous_trip":
        exc2_pct = MOTOR_OCPD_EXC2_PERCENT[
            "instantaneous_trip_design_b" if row == "design_b_energy_efficient" else "instantaneous_trip"
        ]
    elif device == "inverse_time_breaker" and flc > 100:
        exc2_pct = MOTOR_OCPD_EXC2_PERCENT["inverse_time_breaker_over_100a"]
    else:
        exc2_pct = MOTOR_OCPD_EXC2_PERCENT[device]
    # Exception 2 is a "shall not exceed" with no next-size-up allowance: round down.
    exc2 = standard_not_exceeding(flc * exc2_pct / 100.0, sizes)
    conductor_a = CONDUCTOR_FACTOR_430_22 * flc
    warnings = [
        f"{rating} A is the MAXIMUM 430.52 allows for this {OCPD_DEVICES[device].lower()}; a smaller "
        "device is permitted if it carries the starting current. It is not overload protection "
        "(430.32: size the overload relay from the nameplate FLA).",
        edition_warning(),
    ]
    if device == "instantaneous_trip":
        warnings.insert(
            1,
            "An instantaneous-trip breaker is allowed only as part of a listed combination motor "
            "controller and must be adjustable (430.52(C)(3)).",
        )
    return CalcResult(
        calc="motor_branch_ocpd",
        value=rating,
        unit="A",
        inputs={
            "flc_a": make_input(round(flc, 3), "A", "lookup", "NEC 430.248 / 430.250 or nameplate"),
            "hp": make_input(num(hp), "HP", "user"),
            "voltage": make_input(num(voltage), "V", "user"),
            "device": make_input(device, "", "user", OCPD_DEVICES[device]),
            "motor_type": make_input(row, "", "default"),
            "percent_of_flc": make_input(pct, "%", "standard", "Table 430.52(C)(1)"),
            "raw_max_a": make_input(round(raw, 2), "A", "prior_calc"),
            "exception_2_max_a": make_input(exc2, "A", "standard", f"430.52(C)(1) Exc. 2 ({exc2_pct}%)"),
            "conductor_min_a": make_input(round(conductor_a, 2), "A", "prior_calc", "430.22 (125% FLC)"),
        },
        formula=(
            "OCPD max = FLC x Table 430.52(C)(1) % ; not a standard size -> next standard size up "
            "(430.52(C)(1) Exc. 1, 240.6(A))"
        ),
        steps=[
            f"{OCPD_DEVICES[device]}, {row.replace('_', ' ')} motor: {pct}% of FLC",
            f"{pct}% x {flc:g} A = {raw:.2f} A",
            f"max OCPD = {rating} A (next standard size at or above {raw:.2f} A)",
            f"if it will not start: up to {exc2} A ({exc2_pct}% of FLC, Exception 2, rounded down)",
            f"branch conductors are sized separately at 125% x {flc:g} = {conductor_a:.2f} A (430.22)",
        ],
        citations=[CIT_MOTOR_OCPD, CIT_STD_OCPD, CIT_MOTOR_CONDUCTOR],
        warnings=warnings,
    )


def continuous_ocpd(
    load_amps: float,
    *,
    continuous: bool = True,
    continuous_amps: float | None = None,
) -> CalcResult:
    """Branch or feeder OCPD (A) for a non-motor load: at least the noncontinuous
    load plus 125% of the continuous load (210.20(A), 215.3), at the next standard
    size up, and never below 15 A.

    ``continuous_amps`` splits a mixed load; otherwise ``continuous`` says whether
    the whole ``load_amps`` runs 3 hours or more (Article 100 "continuous load").
    """
    total = num(load_amps)
    cont = num(continuous_amps) if continuous_amps is not None else (total if continuous else 0.0)
    cont = min(cont, total)
    noncont = total - cont
    need = noncont + CONTINUOUS_FACTOR * cont
    rating = max(next_standard(need), MIN_BREAKER_A)
    return CalcResult(
        calc="continuous_ocpd",
        value=rating,
        unit="A",
        inputs={
            "load_a": make_input(round(total, 3), "A", "user"),
            "continuous_a": make_input(round(cont, 3), "A", "user"),
            "noncontinuous_a": make_input(round(noncont, 3), "A", "user"),
        },
        formula="OCPD >= noncontinuous + 1.25 x continuous ; next standard size up (240.6(A)) ; >= 15 A",
        steps=[
            f"{noncont:.2f} A + 1.25 x {cont:.2f} A = {need:.2f} A",
            f"OCPD = {rating} A (next standard size, 15 A minimum)",
        ],
        citations=[CIT_CONTINUOUS, CIT_STD_OCPD],
        warnings=[edition_warning()],
    )


__all__ = [
    "CONDUCTOR_FACTOR_430_22",
    "CONTINUOUS_FACTOR",
    "continuous_ocpd",
    "motor_branch_ocpd",
    "motor_type_for",
    "standard_sizes",
]
