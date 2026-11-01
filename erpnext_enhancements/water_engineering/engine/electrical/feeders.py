"""Feeder and service: connected load per 430.24 and Article 220, the feeder /
service OCPD per 430.62 and 430.63, the panel schedule rows, and a check against
the service that is actually available.

The two OCPD round directions are opposite. A *branch* device rounds UP to the
next standard size (430.52(C)(1) Exc. 1, 240.6). A motor *feeder* OCPD is a
ceiling (430.62(A): not greater than the largest branch device plus the other
motors' FLC) and rounds DOWN. When the feeder also carries other loads, 430.63
adds them, and the result may not be smaller than the load it carries: if the
rounded-down value is under the 430.24 load, the feeder goes up to the next
standard size at or above that load and the result says so.

The largest branch device is the 430.52 rating from ``ocpd.motor_branch_ocpd``.
Before v1.763.0 it was 125% of FLA (the 430.22 conductor figure), which seeded
430.62 too low.
"""

from __future__ import annotations

from ..constants import CIT_FEEDER, FEEDER_MOTOR_FACTOR
from ..envelope import CalcResult, make_input
from ._util import EPS, edition_warning, next_standard, num, phase_mult, standard_not_exceeding
from .loads import motor_flc
from .ocpd import CONTINUOUS_FACTOR, motor_branch_ocpd

CIT_FEEDER_OCPD = "NEC 430.62(A) (motor feeder OCPD) / 430.63 (motor and other loads)"
CIT_FEEDER_LOAD = "NEC 430.24 (feeder ampacity) / 215.2(A)(1) / Article 220"
# Columns we_panel_schedule (Control Panel Design print) reads, in order.
PANEL_SCHEDULE_COLUMNS = ("tag", "description", "load_a", "voltage", "phase", "control_method", "breaker_a")


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
    from the NEC table via ``hp``. Amps are summed as given (one voltage); use
    :func:`feeder_load` for loads at mixed voltages.
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


def _rows(loads: list[dict] | None, voltage: float | None, phase: int) -> list[dict]:
    """Normalize load rows to per-unit feeder amps. With a feeder ``voltage``,
    each load's current is converted through its VA (so 120 V and 240 V loads
    add correctly); without one, amps add as given (the legacy single-voltage
    behavior of :func:`total_connected_load`)."""
    out = []
    for ld in loads or []:
        qty = max(int(num(ld.get("qty"), 1)), 1)
        amps = num(ld.get("amps")) or _load_fla(ld)
        if amps <= 0:
            continue
        if voltage:
            ratio = (num(ld.get("voltage"), voltage) * phase_mult(ld.get("phase") or 1)) / (
                num(voltage) * phase_mult(phase)
            )
        else:
            ratio = 1.0
        row = {
            "label": ld.get("label") or ld.get("key") or "load",
            "is_motor": bool(ld.get("is_motor")),
            "continuous": bool(ld.get("continuous")),
            "amps": amps,
            "ratio": ratio,
            "phase": ld.get("phase") or 1,
            "voltage": num(ld.get("voltage"), num(voltage) or 0),
            "hp": num(ld.get("hp")),
        }
        if row["is_motor"]:
            out.extend(dict(row) for _ in range(qty))
        else:
            row["amps"] = amps * qty
            out.append(row)
    return out


def feeder_load(loads: list[dict] | None, voltage: float, phase: int = 1) -> CalcResult:
    """Feeder load (A at ``voltage``) per 430.24 and 215.2(A)(1), working in VA so
    loads at different voltages add: 125% of the largest motor + the other motors
    + 125% of continuous non-motor loads + noncontinuous loads."""
    rows = _rows(loads, voltage, phase)
    motors = [r["amps"] * r["ratio"] for r in rows if r["is_motor"]]
    cont = sum(r["amps"] * r["ratio"] for r in rows if not r["is_motor"] and r["continuous"])
    noncont = sum(r["amps"] * r["ratio"] for r in rows if not r["is_motor"] and not r["continuous"])
    largest = max(motors) if motors else 0.0
    amps = FEEDER_MOTOR_FACTOR * largest + (sum(motors) - largest) + CONTINUOUS_FACTOR * cont + noncont
    va = amps * num(voltage) * phase_mult(phase)
    return CalcResult(
        calc="feeder_load",
        value=round(amps, 3),
        unit="A",
        inputs={
            "voltage": make_input(num(voltage), "V", "user"),
            "phase": make_input(phase, "", "user"),
            "largest_motor_a": make_input(round(largest, 3), "A", "prior_calc"),
            "other_motors_a": make_input(round(sum(motors) - largest, 3), "A", "prior_calc"),
            "continuous_a": make_input(round(cont, 3), "A", "prior_calc"),
            "noncontinuous_a": make_input(round(noncont, 3), "A", "prior_calc"),
            "va": make_input(round(va, 1), "VA", "prior_calc"),
        },
        formula="I = 1.25 x largest motor + other motors + 1.25 x continuous + noncontinuous (at the feeder voltage)",
        steps=[
            f"largest motor 1.25 x {largest:.2f} = {FEEDER_MOTOR_FACTOR * largest:.2f} A",
            f"other motors {sum(motors) - largest:.2f} A ; continuous 1.25 x {cont:.2f} = "
            f"{CONTINUOUS_FACTOR * cont:.2f} A ; noncontinuous {noncont:.2f} A",
            f"feeder load {amps:.2f} A at {num(voltage):g} V ({va:,.0f} VA)",
        ],
        citations=[CIT_FEEDER_LOAD],
        warnings=["Assumes single-phase loads are balanced across the phases.", edition_warning()],
    )


def feeder_ocpd(
    loads: list[dict] | None,
    voltage: float | None = None,
    phase: int = 1,
    device: str = "inverse_time_breaker",
    calc: str = "feeder_ocpd",
) -> CalcResult:
    """Feeder or service OCPD (A): 430.62(A) for the motors (largest 430.52 branch
    device + the other motors' FLC) plus the other loads (430.63, continuous at
    125%), rounded DOWN to a standard size, and raised to the next standard size
    at or above the 430.24 load if the rounding went under it."""
    rows = _rows(loads, voltage, phase)
    motors = [r for r in rows if r["is_motor"]]
    others = [r for r in rows if not r["is_motor"]]
    other_a = sum(
        (CONTINUOUS_FACTOR if r["continuous"] else 1.0) * r["amps"] * r["ratio"] for r in others
    )
    # The load the feeder carries (430.24 / 215.2), the floor the OCPD may not go under.
    if motors:
        flcs = [m["amps"] * m["ratio"] for m in motors]
        load_a = FEEDER_MOTOR_FACTOR * max(flcs) + sum(flcs) - max(flcs) + other_a
    else:
        load_a = other_a
    if not rows:
        return CalcResult(
            calc=calc,
            unit="A breaker",
            formula="feeder OCPD = largest motor branch OCPD + sum(other motor FLC) + other loads",
            citations=[CIT_FEEDER_OCPD],
            warnings=["No loads with a resolvable current were supplied."],
        )
    steps = []
    if motors:
        seeds = []
        for m in motors:
            r = motor_branch_ocpd(m["amps"], device=device, phase=m["phase"], hp=m["hp"], voltage=m["voltage"])
            seeds.append((r.value * m["ratio"], m))
        seed_ocpd, seed = max(seeds, key=lambda s: s[0])
        other_motor_a = sum(m["amps"] * m["ratio"] for m in motors) - seed["amps"] * seed["ratio"]
        ceiling = seed_ocpd + other_motor_a + other_a
        steps.append(
            f"largest motor branch device {seed_ocpd:.1f} A (430.52, {seed['label']}) + other motors "
            f"{other_motor_a:.2f} A + other loads {other_a:.2f} A = {ceiling:.2f} A"
        )
        rating = standard_not_exceeding(ceiling)
        steps.append(f"{rating} A: largest standard size not exceeding {ceiling:.2f} A (430.62(A), rounds DOWN)")
    else:
        seed_ocpd = 0.0
        other_motor_a = 0.0
        ceiling = other_a
        rating = max(next_standard(other_a), 15)
        steps.append(f"no motors: {other_a:.2f} A (continuous at 125%) -> {rating} A (215.3, next size up)")
    warnings = []
    raised = False
    if rating + EPS < load_a:
        new = next_standard(load_a)
        msg = (f"Rounding down gave {rating} A, under the {load_a:.2f} A the feeder carries, which would trip in "
               f"service; raised to {new} A, the next standard size at or above the load.")
        if new > ceiling + EPS and motors:
            msg += (f" {new} A is above the {ceiling:.2f} A that 430.62(A) allows, and 430.62 has no next-size-up "
                    "allowance: use an adjustable-trip breaker set between the load and the ceiling, or a smaller "
                    "largest-motor branch device, and confirm with the engineer.")
        warnings.append(msg)
        steps.append(f"raised to {new} A (>= feeder load {load_a:.2f} A)")
        rating, raised = new, True
    elif rating > ceiling + EPS and motors:
        warnings.append(f"{ceiling:.2f} A is below the smallest {rating} A breaker; use fuses sized to the ceiling.")
    warnings.append(
        "Feeder/service OCPD (NEC 430.62) rounds DOWN, unlike a branch breaker; "
        "confirm the largest-motor selection and the load list with the engineer."
    )
    warnings.append(edition_warning())
    return CalcResult(
        calc=calc,
        value=rating,
        unit="A breaker",
        inputs={
            "largest_branch_ocpd": make_input(round(seed_ocpd, 2), "A", "prior_calc", "motor_branch_ocpd"),
            "other_motor_fla": make_input(round(other_motor_a, 2), "A", "prior_calc"),
            "non_motor_a": make_input(round(other_a, 2), "A", "prior_calc", "continuous at 125%"),
            "ceiling_a": make_input(round(ceiling, 2), "A", "prior_calc"),
            "feeder_load_a": make_input(round(load_a, 2), "A", "prior_calc", "430.24"),
            "raised_to_load": make_input(raised, "", "prior_calc"),
        },
        formula=(
            "ceiling = largest 430.52 branch OCPD + sum(other motor FLC) + other loads (125% continuous) ; "
            "OCPD = largest standard <= ceiling, but >= the 430.24 load"
        ),
        steps=steps,
        citations=[CIT_FEEDER_OCPD, CIT_FEEDER_LOAD, "NEC 430.52(C)(1)"],
        warnings=warnings,
    )


def service_main_breaker(loads: list[dict] | None) -> CalcResult:
    """Feeder / service OCPD (A) per NEC 430.62(A) / 430.63 for loads given at one
    voltage (the Control Panel Design's rows): the largest motor's 430.52 branch
    OCPD + the FLC of the other motors + the other loads (continuous at 125%),
    rounded DOWN to a standard size but never below the 430.24 load.

    A motor feeder needs at least one motor; with none, use :func:`feeder_ocpd`.
    """
    if not any(ld.get("is_motor") and _load_fla(ld) > 0 for ld in loads or []):
        return CalcResult(
            calc="service_main_breaker",
            unit="A breaker",
            formula="feeder OCPD = largest motor branch OCPD + sum(other motor FLC) + other loads",
            citations=[CIT_FEEDER],
            warnings=["No motor loads supplied; a motor feeder OCPD needs at least one motor."],
        )
    legacy = [{**ld, "amps": _load_fla(ld)} for ld in loads or []]
    return feeder_ocpd(legacy, voltage=None, calc="service_main_breaker")


def service_check(
    required_a: float,
    main_ocpd_a: float,
    available_a: float | None,
    voltage: float = 240,
    phase: int = 1,
) -> CalcResult:
    """Does the available service carry the design? Good at 80% or less of the
    available ampacity, warn up to 100%, bad above it or when the main OCPD the
    design needs exceeds what the service can be protected at."""
    req = num(required_a)
    main = num(main_ocpd_a)
    avail = num(available_a)
    if avail <= 0:
        return CalcResult(
            calc="service_check",
            unit="%",
            inputs={
                "required_a": make_input(round(req, 2), "A", "prior_calc"),
                "main_ocpd_a": make_input(main, "A", "prior_calc"),
            },
            formula="utilization = required / available",
            citations=[CIT_FEEDER_LOAD],
            warnings=["Enter the available service (amps at the panel feeding the fountain) to check it."],
            status="warn",
        )
    util = 100.0 * req / avail
    status = "good" if util <= 80 + EPS and main <= avail + EPS else ("warn" if util <= 100 + EPS else "bad")
    warnings = []
    if main > avail + EPS:
        status = "bad"
        warnings.append(f"The design needs a {main:g} A main; the service is {avail:g} A.")
    elif util > 80 + EPS:
        warnings.append(f"The design uses {util:.0f}% of the {avail:g} A service (over 80% leaves no margin).")
    kva = avail * num(voltage) * phase_mult(phase) / 1000.0
    return CalcResult(
        calc="service_check",
        value=round(util, 1),
        unit="%",
        inputs={
            "required_a": make_input(round(req, 2), "A", "prior_calc", "feeder_load"),
            "main_ocpd_a": make_input(main, "A", "prior_calc", "feeder_ocpd"),
            "available_a": make_input(avail, "A", "user"),
            "available_kva": make_input(round(kva, 2), "kVA", "prior_calc"),
        },
        formula="utilization = feeder load / available service ; main OCPD <= available",
        steps=[
            f"{req:.1f} A / {avail:g} A = {util:.0f}%",
            f"main OCPD {main:g} A vs {avail:g} A available",
        ],
        citations=[CIT_FEEDER_LOAD],
        warnings=[*warnings, edition_warning()],
        status=status,
    )


def panel_schedule(circuits: list[dict] | None) -> CalcResult:
    """Panel-schedule rows from the branch circuits (``circuits.branch_circuits``).
    The first seven keys are exactly the columns of Control Panel Design's
    ``we_panel_schedule`` (:data:`PANEL_SCHEDULE_COLUMNS`); the rest (poles,
    conductor, EGC, conduit, run, drop, GFCI, VA) extend it."""
    rows = []
    for c in circuits or []:
        rows.append({
            "tag": c.get("tag"),
            "description": c.get("description"),
            "load_a": round(num(c.get("load_a")), 2),
            "voltage": c.get("voltage"),
            "phase": c.get("phase"),
            "control_method": c.get("control_method") or "",
            "breaker_a": c.get("breaker_a"),
            "poles": c.get("poles"),
            "conductor": c.get("conductor"),
            "egc": c.get("egc"),
            "conduit": c.get("conduit"),
            "run_ft": c.get("run_ft"),
            "drop_pct": c.get("drop_pct"),
            "gfci": c.get("gfci"),
            "va": round(num(c.get("va")), 1),
        })
    total_va = sum(r["va"] for r in rows)
    return CalcResult(
        calc="panel_schedule",
        value=rows,
        unit="rows",
        inputs={"circuits": make_input(len(rows), "", "prior_calc"), "total_va": make_input(total_va, "VA", "prior_calc")},
        formula="one row per branch circuit ; columns match we_panel_schedule",
        steps=[f"{len(rows)} circuit(s), {total_va:,.0f} VA connected"],
        citations=["Control Panel Design / we_panel_schedule"],
    )


__all__ = [
    "PANEL_SCHEDULE_COLUMNS",
    "feeder_load",
    "feeder_ocpd",
    "panel_schedule",
    "service_check",
    "service_main_breaker",
    "total_connected_load",
]
