"""Backup power: generator size (80% continuous, plus the largest motor's
starting surge) and battery runtime. Ported from the Digital Rain Curtain's
``backupPlan`` (src/electrical.js) and extended with the locked-rotor code letters
of Table 430.7(B) for the starting surge.
"""

from __future__ import annotations

from ..data.nec_tables import CIT_CODE_LETTER, LOCKED_ROTOR_KVA_PER_HP
from ..envelope import CalcResult, make_input
from ._util import EPS, edition_warning, num

# Generator ratings (running W). The first twelve are the rain curtain's
# GEN_SIZES; larger standby sets follow. A generator carries 80% continuously and
# surges to about 1.25 x its running rating.
GEN_SIZES_W = [
    1000, 2000, 3000, 4000, 5000, 6500, 8000, 10000, 12000, 15000, 20000, 30000,
    40000, 50000, 60000, 80000, 100000, 125000, 150000, 200000,
]
GEN_CONTINUOUS = 0.8
GEN_SURGE = 1.25
BATTERY_USABLE = 0.85  # share of rated Wh that reaches the outlets (inverter losses, reserve)
DEFAULT_START_FACTOR = 3.0  # starting W / running W across the line (the rain curtain's startX)
CIT_BACKUP = "Digital Rain Curtain src/electrical.js backupPlan (80% continuous, 1.25 x surge)"


def code_letter_kva_per_hp(letter: str | None) -> float | None:
    """Upper bound of the Table 430.7(B) kVA/HP band for a code letter."""
    letter = (letter or "").strip().upper()
    return next((hi for code, _lo, hi in LOCKED_ROTOR_KVA_PER_HP if code == letter), None)


def starting_w(load: dict, start_factor: float = DEFAULT_START_FACTOR) -> tuple[float, str]:
    """Per-unit starting draw (W, treating VA as W) and its basis: running W on a
    VFD (a soft start), HP x the code letter's kVA/HP when given, else running W x
    ``start_factor``."""
    run = num(load.get("watts"))
    if not load.get("is_motor"):
        return run, "not a motor"
    if (load.get("control") or "") == "vfd":
        return run, "VFD soft start"
    kva_hp = code_letter_kva_per_hp(load.get("code_letter"))
    if kva_hp and num(load.get("hp")) > 0:
        return num(load.get("hp")) * kva_hp * 1000.0, f"code letter {load.get('code_letter')} ({kva_hp} kVA/HP)"
    return run * max(1.0, num(start_factor, DEFAULT_START_FACTOR)), f"{start_factor:g} x running"


def generator_size(running_w: float, start_w: float, sizes: list[int] | None = None) -> CalcResult:
    """Smallest generator with 80% of its rating >= the running load and 1.25 x
    its rating >= the starting load (the rain curtain's rule)."""
    sizes = sizes or GEN_SIZES_W
    run, start = num(running_w), num(start_w)
    gen = next((s for s in sizes if GEN_CONTINUOUS * s >= run - EPS and GEN_SURGE * s >= start - EPS), None)
    return CalcResult(
        calc="generator_size",
        value=gen,
        unit="W",
        inputs={
            "running_w": make_input(round(run, 1), "W", "prior_calc"),
            "starting_w": make_input(round(start, 1), "W", "prior_calc"),
        },
        formula="smallest rating with 0.8 x rating >= running and 1.25 x rating >= starting",
        steps=[f"running {run:,.0f} W, starting {start:,.0f} W -> {gen:,} W" if gen else
               f"running {run:,.0f} W, starting {start:,.0f} W -> past the {sizes[-1]:,} W list"],
        citations=[CIT_BACKUP],
        warnings=([] if gen else ["Larger than the listed generators; size with the maker's software."]) + [
            "Standby sets are chosen for an allowed voltage dip on motor start (often 15-35%); confirm with the "
            "generator maker's sizing tool.",
        ],
    )


def battery_runtime(battery_wh: float, avg_w: float, usable: float = BATTERY_USABLE) -> CalcResult:
    """Hours a battery runs the average load: Wh x usable share / average W."""
    wh, avg = num(battery_wh), num(avg_w)
    hours = (wh * usable / avg) if avg > 0 else float("inf")
    return CalcResult(
        calc="battery_runtime",
        value=round(hours, 3) if avg > 0 else None,
        unit="h",
        inputs={
            "battery_wh": make_input(wh, "Wh", "user"),
            "avg_w": make_input(round(avg, 1), "W", "prior_calc"),
            "usable": make_input(usable, "", "default", "inverter losses and reserve"),
        },
        formula="hours = Wh x usable / average W",
        steps=[f"{wh:g} Wh x {usable:g} / {avg:.0f} W = {hours:.2f} h" if avg > 0 else "no load"],
        citations=[CIT_BACKUP],
    )


def backup_plan(
    loads: list[dict] | None,
    *,
    battery_wh: float = 0.0,
    start_factor: float = DEFAULT_START_FACTOR,
) -> CalcResult:
    """Generator and battery for every load running at once, with the motor of
    the largest starting surge starting last. ``value`` is a dict: running_w,
    starting_w, generator_w, avg_w, battery_hours, surge_basis."""
    rows = loads or []
    run = sum(num(r.get("watts")) * int(num(r.get("qty"), 1)) for r in rows)
    extra, basis, who = 0.0, "", ""
    for r in rows:
        s, b = starting_w(r, start_factor)
        if s - num(r.get("watts")) > extra:
            extra, basis, who = s - num(r.get("watts")), b, r.get("label") or ""
    start = run + extra
    gen = generator_size(run, start)
    avg = sum(
        num(r.get("watts")) * int(num(r.get("qty"), 1)) * min(24.0, num(r.get("duty_hours_day"), 24.0)) / 24.0
        for r in rows
    )
    bat = battery_runtime(battery_wh, avg) if num(battery_wh) > 0 else None
    return CalcResult(
        calc="backup_plan",
        value={
            "running_w": round(run, 1),
            "starting_w": round(start, 1),
            "generator_w": gen.value,
            "avg_w": round(avg, 1),
            "battery_hours": bat.value if bat else None,
            "surge_basis": basis,
            "surge_motor": who,
        },
        unit="",
        inputs={
            "loads": make_input(len(rows), "", "prior_calc"),
            "battery_wh": make_input(num(battery_wh), "Wh", "user"),
            "start_factor": make_input(start_factor, "", "default"),
        },
        formula="running = sum(W) ; starting = running + largest (start - run) ; generator per generator_size",
        steps=[
            f"running {run:,.0f} W (everything on)",
            f"+ {extra:,.0f} W starting surge ({who}: {basis}) = {start:,.0f} W" if extra else "no motor surge",
        ] + gen.steps + (bat.steps if bat else []),
        citations=[CIT_BACKUP, CIT_CODE_LETTER],
        warnings=[*gen.warnings, edition_warning()],
    )


__all__ = [
    "BATTERY_USABLE",
    "GEN_SIZES_W",
    "backup_plan",
    "battery_runtime",
    "code_letter_kva_per_hp",
    "generator_size",
    "starting_w",
]
