"""Panel and service electrical sizing: motor full-load current, connected load,
feeder/service OCPD, and control-transformer VA.

The Sapphire workbooks stop at pump selection; branch, feeder, service, and
control-transformer sizing are NEC business rules, not source-document formulas.
Every function here carries its NEC citation and a "confirm with the engineer"
warning, exactly like :func:`pump.electrical_load` (the branch-circuit rule these
build on).

One thing worth stating loudly: the two OCPD round directions are opposite. A
*branch* breaker rounds UP to the next standard size (240.6, `electrical_load`),
but a *feeder/service* OCPD rounds DOWN to the largest standard size not
exceeding its ceiling (430.62, `service_main_breaker`). Rounding the service up
would oversize it illegally — a golden test pins the down-rounding.
"""

from __future__ import annotations

import math

from .constants import (
    CIT_CONTROL_XFMR,
    CIT_FEEDER,
    CIT_MOTOR_FLC,
    CONTROL_DEVICE_VA,
    FEEDER_MOTOR_FACTOR,
    MOTOR_FLC_1PH,
    MOTOR_FLC_3PH,
    STD_CONTROL_XFMR_VA,
    STD_OCPD_AMPS,
)
from .envelope import CalcResult, make_input
from .pump import electrical_load


def _nearest_voltage(cols: dict, voltage: int) -> int:
    """Pick the closest available voltage-column key to ``voltage``."""
    return min(cols, key=lambda k: abs(k - int(voltage)))


def motor_flc(hp: float, phase: int = 1, voltage: int = 230, tol: float = 0.01) -> float | None:
    """Motor full-load current (A) from NEC Table 430.248 (1ph) / 430.250 (3ph).

    Returns the table value for a standard HP at the nearest voltage column, or
    ``None`` for a fractional / oversize HP not in the table — the caller then
    uses the motor's nameplate FLA, as NEC 430.6(A)(1) allows.
    """
    hp = float(hp or 0)
    table = MOTOR_FLC_3PH if int(phase or 1) == 3 else MOTOR_FLC_1PH
    if hp <= 0:
        return None
    match = next((k for k in table if abs(k - hp) <= tol), None)
    if match is None:
        return None
    cols = table[match]
    return cols[_nearest_voltage(cols, int(voltage or 230))]


def _load_fla(load: dict) -> float:
    """Per-unit full-load amps for one load row: explicit FLA, else the motor
    table via HP, else 0."""
    fla = float(load.get("fla") or load.get("fla_amps") or 0)
    if fla > 0:
        return fla
    if load.get("is_motor") and load.get("hp"):
        return float(motor_flc(load.get("hp"), load.get("phase", 1), load.get("voltage", 230)) or 0)
    return 0.0


def total_connected_load(loads: list[dict] | None) -> CalcResult:
    """Connected load (A) for a feeder serving several motors + other loads, per
    NEC 430.24: 125% of the largest motor's FLC + the FLC of every other motor +
    the other (non-motor) loads, with continuous non-motor loads at 125%.

    Each ``loads`` row: ``{fla|fla_amps, hp, phase, voltage, qty, is_motor,
    continuous, label}`` — a motor's FLA is taken from ``fla`` when given, else
    from the NEC table via ``hp``.
    """
    rows = loads or []
    motor_units: list[float] = []
    non_motor_cont = 0.0
    non_motor_noncont = 0.0
    for ld in rows:
        qty = max(int(ld.get("qty") or 1), 1)
        fla = _load_fla(ld)
        if fla <= 0:
            continue
        if ld.get("is_motor"):
            motor_units.extend([fla] * qty)
        elif ld.get("continuous"):
            non_motor_cont += fla * qty
        else:
            non_motor_noncont += fla * qty

    if not motor_units and non_motor_cont == 0 and non_motor_noncont == 0:
        return CalcResult(
            calc="total_connected_load",
            unit="A",
            formula="amps = 1.25*largest_motor + sum(other_motors) + non_motor_loads",
            citations=[CIT_FEEDER],
            warnings=["No loads with a resolvable FLA were supplied."],
        )

    largest = max(motor_units) if motor_units else 0.0
    sum_other_motors = sum(motor_units) - largest
    amps = FEEDER_MOTOR_FACTOR * largest + sum_other_motors + 1.25 * non_motor_cont + non_motor_noncont
    return CalcResult(
        calc="total_connected_load",
        value=amps,
        unit="A",
        inputs={
            "motor_count": make_input(len(motor_units), "", "prior_calc"),
            "largest_motor_fla": make_input(round(largest, 2), "A", "prior_calc"),
            "non_motor_continuous_a": make_input(round(non_motor_cont, 2), "A", "user"),
            "non_motor_a": make_input(round(non_motor_noncont, 2), "A", "user"),
        },
        formula=(
            "amps = 1.25*largest_motor_fla + sum(other_motor_fla) "
            "+ 1.25*continuous_non_motor + other_non_motor"
        ),
        steps=[
            f"largest motor = {largest:.2f} A -> 1.25 * {largest:.2f} = {FEEDER_MOTOR_FACTOR * largest:.2f} A",
            f"other motors = {sum_other_motors:.2f} A",
            f"non-motor continuous = 1.25 * {non_motor_cont:.2f} = {1.25 * non_motor_cont:.2f} A ; "
            f"non-motor = {non_motor_noncont:.2f} A",
            f"connected load = {amps:.2f} A",
        ],
        citations=[CIT_FEEDER],
        warnings=[
            "Feeder ampacity (NEC 430.24) is a design aid, not a stamped calculation; "
            "confirm load classification and demand factors with the engineer."
        ],
    )


def service_main_breaker(loads: list[dict] | None) -> CalcResult:
    """Feeder / service OCPD (A) per NEC 430.62(A): the largest motor's
    branch-circuit OCPD + the FLC of the other motors + the other loads, then the
    largest standard size NOT exceeding that ceiling (rounds DOWN — the opposite
    of a branch OCPD).
    """
    rows = loads or []
    motor_units: list[float] = []
    non_motor = 0.0
    for ld in rows:
        qty = max(int(ld.get("qty") or 1), 1)
        fla = _load_fla(ld)
        if fla <= 0:
            continue
        if ld.get("is_motor"):
            motor_units.extend([fla] * qty)
        else:
            non_motor += fla * qty

    if not motor_units:
        return CalcResult(
            calc="service_main_breaker",
            unit="A breaker",
            formula="feeder OCPD = largest motor branch OCPD + sum(other motor FLC) + other loads",
            citations=[CIT_FEEDER],
            warnings=["No motor loads supplied; a motor feeder OCPD needs at least one motor."],
        )

    # Each motor's branch OCPD (125% -> next standard, 240.6); the LARGEST seeds
    # 430.62, and that motor is excluded from the "other motors" FLC sum.
    branch = [(fla, electrical_load(fla).value) for fla in motor_units]
    seed_fla, seed_ocpd = max(branch, key=lambda x: x[1])
    other_motor_fla = sum(motor_units) - seed_fla
    ceiling = seed_ocpd + other_motor_fla + non_motor
    main = next((b for b in reversed(STD_OCPD_AMPS) if b <= ceiling), STD_OCPD_AMPS[0])
    return CalcResult(
        calc="service_main_breaker",
        value=main,
        unit="A breaker",
        inputs={
            "largest_branch_ocpd": make_input(seed_ocpd, "A", "prior_calc", "electrical_load"),
            "other_motor_fla": make_input(round(other_motor_fla, 2), "A", "prior_calc"),
            "non_motor_a": make_input(round(non_motor, 2), "A", "user"),
        },
        formula=(
            "ceiling = largest_branch_ocpd + sum(other_motor_fla) + non_motor ; "
            "feeder = largest standard OCPD <= ceiling"
        ),
        steps=[
            f"largest motor branch OCPD = {seed_ocpd} A (for the {seed_fla:.2f} A motor)",
            f"+ other motor FLC {other_motor_fla:.2f} A + non-motor {non_motor:.2f} A "
            f"= ceiling {ceiling:.2f} A",
            f"feeder OCPD = {main} A (largest standard size NOT exceeding the ceiling, "
            "NEC 430.62 — rounds DOWN)",
        ],
        citations=[CIT_FEEDER],
        warnings=[
            "Feeder/service OCPD (NEC 430.62) rounds DOWN, unlike a branch breaker; "
            "confirm the largest-motor selection and the load list with the engineer."
        ],
    )


def control_transformer_va(control_loads: list[dict] | None) -> CalcResult:
    """Control-transformer size (VA): sum the sealed VA of the control-circuit
    devices and pick the next standard transformer (NEC Article 450 + mfr rule).

    Each ``control_loads`` row: ``{device, qty, va}`` — ``va`` overrides the
    per-device nominal in ``CONTROL_DEVICE_VA``.
    """
    rows = control_loads or []
    total = 0.0
    device_va: list[float] = []
    for cl in rows:
        qty = max(int(cl.get("qty") or 1), 1)
        each = float(cl.get("va") or CONTROL_DEVICE_VA.get((cl.get("device") or "").strip().lower(), 0.0))
        if each <= 0:
            continue
        total += each * qty
        device_va.extend([each] * qty)
    if total <= 0:
        return CalcResult(
            calc="control_transformer_va",
            unit="VA",
            formula="VA = sum(qty * sealed_va) ; pick the next standard transformer",
            citations=[CIT_CONTROL_XFMR],
            warnings=["No control-circuit devices (or VA values) supplied."],
        )
    xfmr = next((v for v in STD_CONTROL_XFMR_VA if v >= total), math.ceil(total))
    inrush = max(device_va) if device_va else 0.0
    return CalcResult(
        calc="control_transformer_va",
        value=xfmr,
        unit="VA",
        inputs={
            "sealed_va_total": make_input(round(total, 1), "VA", "prior_calc"),
            "largest_device_va": make_input(round(inrush, 1), "VA", "lookup"),
        },
        formula="VA = sum(qty * sealed_va) ; transformer = next standard rating >= total",
        steps=[
            f"sealed VA total = {total:.1f} VA",
            f"control transformer = {xfmr} VA (next standard rating)",
            f"largest single-device sealed VA = {inrush:.1f} VA (confirm the inrush margin)",
        ],
        citations=[CIT_CONTROL_XFMR],
        warnings=[
            "Control-transformer VA is a manufacturer-selection business rule; verify "
            "sealed vs inrush VA of the actual contactor coils / HMI / relays with the engineer."
        ],
    )
