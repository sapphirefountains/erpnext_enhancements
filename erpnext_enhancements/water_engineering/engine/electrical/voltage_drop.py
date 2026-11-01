"""Voltage drop: line-voltage runs to the panel and low-voltage runs to fixtures
and valve coils.

The NEC does not make voltage drop mandatory for ordinary circuits. The
informational notes to 210.19(A) and 215.2(A) recommend no more than 3% on a
branch circuit and 5% feeder plus branch, and this module uses those as the
limits. (Sensitive equipment and a few articles, such as 647.4(D), do make it a
rule.) Resistance is the DC value at 75 C from Chapter 9 Table 8; for the sizes a
fountain uses (1/0 and smaller) reactance is negligible and the DC figure is the
conservative one.

:func:`low_voltage_run` is the Digital Rain Curtain's ``coilPlan`` (src/
electrical.js) generalized: any bank of identical devices spaced along a run (24 V
valve coils, 12 V luminaires), split into fused branches loaded to 80%, with the
drop judged at the farthest device of the worst branch.
"""

from __future__ import annotations

import math

from ..data.nec_tables import (
    AMPACITY_310_16,
    CIT_TABLE_8,
    CONDUCTOR_PROPS,
    FIXTURE_WIRE_AMPACITY,
    SIZES,
    size_index,
)
from ..envelope import CalcResult, make_input
from ._util import EPS, edition_warning, num, phase_of

BRANCH_DROP_PCT = 3.0  # 210.19(A) Informational Note No. 4
TOTAL_DROP_PCT = 5.0  # 215.2(A)(1) Informational Note No. 2 (feeder + branch)
CIT_DROP_NOTES = "NEC 210.19(A) Inf. Note 4 / 215.2(A)(1) Inf. Note 2 (3% branch, 5% total; recommendations)"
CIT_CLASS2 = "NEC Article 725 (Class 2) / Chapter 9 Table 11(B) (100 VA, OCPD <= 100/V for 20-30 V DC)"
# Automotive blade-fuse ratings (ATO / ATC), the fuses a low-voltage distribution
# block takes. Not an NEC table; the same list as the rain curtain's FUSES.
BLADE_FUSES_A = [1, 2, 3, 4, 5, 7.5, 10, 15, 20, 25, 30]
CLASS2_MAX_VA = 100.0
LV_WIRE_SIZES = ["18", "16", "14", "12", "10"]  # the rain curtain's coil wire sizes
LV_LIGHTING_WIRE_SIZES = ["16", "14", "12", "10", "8", "6"]  # 12 V lighting runs go heavier


def resistance_ohm_per_kft(size: str, material: str = "Cu") -> float:
    """DC resistance at 75 C, ohms per 1000 ft (Chapter 9 Table 8)."""
    props = CONDUCTOR_PROPS[str(size)]
    return props[2] if (material or "Cu") == "Al" else props[1]


def drop_volts(amps: float, length_ft: float, size: str, phase: int = 1, material: str = "Cu") -> float:
    """Volts lost over a one-way run: 2 I R L for single-phase and DC, sqrt(3) I R L
    for three-phase (R per foot, L one way)."""
    r = resistance_ohm_per_kft(size, material) / 1000.0
    k = math.sqrt(3.0) if phase_of(phase) == 3 else 2.0
    return k * num(amps) * r * num(length_ft)


def voltage_drop(
    amps: float,
    length_ft: float,
    size: str,
    voltage: float,
    phase: int = 1,
    material: str = "Cu",
    limit_pct: float = BRANCH_DROP_PCT,
) -> CalcResult:
    """Voltage drop (% of ``voltage``) over a one-way run of ``length_ft``."""
    v = num(voltage)
    vd = drop_volts(amps, length_ft, size, phase, material)
    pct = 100.0 * vd / v if v > 0 else 0.0
    ok = pct <= num(limit_pct) + EPS
    r = resistance_ohm_per_kft(size, material)
    k = "sqrt(3)" if phase_of(phase) == 3 else "2"
    return CalcResult(
        calc="voltage_drop",
        value=round(pct, 3),
        unit="%",
        inputs={
            "amps": make_input(num(amps), "A", "prior_calc"),
            "length_ft": make_input(num(length_ft), "ft", "user", "one way"),
            "size": make_input(str(size), "AWG/kcmil", "prior_calc"),
            "material": make_input(material, "", "user"),
            "resistance_ohm_kft": make_input(r, "ohm/kft", "lookup", "Chapter 9 Table 8"),
            "voltage": make_input(v, "V", "user"),
            "phase": make_input(phase_of(phase), "", "user"),
            "drop_v": make_input(round(vd, 3), "V", "prior_calc"),
            "limit_pct": make_input(num(limit_pct), "%", "standard"),
        },
        formula=f"Vd = {k} x I x R x L / 1000 ; % = 100 x Vd / V",
        steps=[
            f"Vd = {k} x {num(amps):.2f} A x {r} ohm/kft x {num(length_ft):g} ft / 1000 = {vd:.3f} V",
            f"{vd:.3f} / {v:g} V = {pct:.2f}% ({'within' if ok else 'over'} the {num(limit_pct):g}% limit)",
        ],
        citations=[CIT_TABLE_8, CIT_DROP_NOTES],
        warnings=[] if ok else [f"{pct:.2f}% drop exceeds the {num(limit_pct):g}% recommendation; upsize the wire."],
        status="good" if ok else "warn",
    )


def size_for_drop(
    amps: float,
    length_ft: float,
    voltage: float,
    phase: int = 1,
    material: str = "Cu",
    limit_pct: float = BRANCH_DROP_PCT,
    min_size: str = "14",
) -> str:
    """Smallest size at or above ``min_size`` that holds the drop to ``limit_pct``
    (the largest size in the table if none does)."""
    start = size_index(min_size)
    v = num(voltage)
    for size in SIZES[start:]:
        if material == "Al" and size in ("18", "16", "14"):
            continue
        if v <= 0 or 100.0 * drop_volts(amps, length_ft, size, phase, material) / v <= num(limit_pct) + EPS:
            return size
    return SIZES[-1]


def total_drop(feeder_pct: float, branch_pct: float) -> CalcResult:
    """Feeder plus branch drop against the 3% branch and 5% total recommendations."""
    f, b = num(feeder_pct), num(branch_pct)
    total = f + b
    warnings = []
    if b > BRANCH_DROP_PCT + EPS:
        warnings.append(f"Branch drop {b:.2f}% exceeds 3% (210.19(A) Inf. Note 4).")
    if total > TOTAL_DROP_PCT + EPS:
        warnings.append(f"Feeder + branch drop {total:.2f}% exceeds 5% (215.2(A)(1) Inf. Note 2).")
    return CalcResult(
        calc="total_drop",
        value=round(total, 3),
        unit="%",
        inputs={
            "feeder_pct": make_input(f, "%", "prior_calc"),
            "branch_pct": make_input(b, "%", "prior_calc"),
        },
        formula="total = feeder % + branch % ; branch <= 3%, total <= 5%",
        steps=[f"{f:.2f}% + {b:.2f}% = {total:.2f}%"],
        citations=[CIT_DROP_NOTES],
        warnings=warnings,
        status="warn" if warnings else "good",
    )


def lv_wire_ampacity(size: str) -> float:
    """Ampacity used for low-voltage wire: Table 402.5 for 18 / 16 AWG fixture
    wire, the 60 C column of Table 310.16 for 14 AWG and larger."""
    if size in FIXTURE_WIRE_AMPACITY:
        return float(FIXTURE_WIRE_AMPACITY[size])
    return float(AMPACITY_310_16["Cu"][size][0])


def _blade_fuse_for_class2(voltage: float) -> float:
    """Largest blade fuse at or below the Class 2 limit 100 VA / V."""
    cap = CLASS2_MAX_VA / num(voltage, 24.0)
    fits = [f for f in BLADE_FUSES_A if f <= cap + EPS]
    return fits[-1] if fits else BLADE_FUSES_A[0]


def low_voltage_run(
    count: int,
    watts_each: float,
    spacing_ft: float,
    *,
    voltage: float = 24.0,
    feed_ft: float = 6.0,
    supply_at: str = "end",
    branch_fuse_a: float = 0.0,
    limit_pct: float = 5.0,
    group: int = 8,
    kind: str = "valve",
    wire_sizes: list[str] | None = None,
) -> CalcResult:
    """Low-voltage bank: ``count`` identical devices ``spacing_ft`` apart, fed from
    one supply. Splits them into fused branches loaded to 80% of the fuse (Class 2
    branches of at most 100 VA when ``branch_fuse_a`` is 0), in whole multiples of
    ``group`` where possible (8 = one TPIC6B595 driver chip per eight valves; use 1
    for luminaires), and picks the smallest wire in ``wire_sizes`` (18-10 AWG by
    default, the rain curtain's coil wire) that holds the drop at the
    farthest device of the worst branch to ``limit_pct``.

    Each branch gets a home run from the supply to its near end (``feed_ft`` plus
    any distance along the bank) and a bus along its devices; the drop is the home
    run at full branch current plus half the bus (evenly spread load), the rain
    curtain's ``coilPlan`` rule. ``value`` is a dict: wire, fuse_a, per_branch,
    branches, drop_pct, wire_ft, class2, drop_ok, over_fuse.
    """
    n = max(1, int(num(count, 1)))
    v = num(voltage, 24.0) or 24.0
    w_each = max(0.0, num(watts_each))
    amps_each = w_each / v
    class2 = not (num(branch_fuse_a) > 0)
    fuse = _blade_fuse_for_class2(v) if class2 else num(branch_fuse_a)
    fit = math.floor((0.8 * fuse * v) / w_each + EPS) if w_each > 0 else n
    grp = max(1, int(num(group, 1)))
    per = max(1, min(n, (fit // grp) * grp if fit >= grp else fit))
    n_branches = math.ceil(n / per)
    pitch = num(spacing_ft)
    bank_len = n * pitch
    xs = bank_len / 2.0 if supply_at == "center" else 0.0
    branches = []
    for b in range(n_branches):
        nb = min(per, n - b * per)
        x0 = b * per * pitch
        x1 = x0 + nb * pitch
        inside = x0 < xs < x1
        home = num(feed_ft) + (0.0 if inside else (x0 - xs if xs <= x0 else xs - x1))
        bus = max(xs - x0, x1 - xs) if inside else x1 - x0
        branches.append({"devices": nb, "amps": nb * amps_each, "home_ft": home, "bus_ft": bus})

    def worst(size: str) -> float:
        r = resistance_ohm_per_kft(size) / 1000.0
        return max(100.0 * br["amps"] * 2.0 * r * (br["home_ft"] + br["bus_ft"] / 2.0) / v for br in branches)

    sizes = list(wire_sizes or LV_WIRE_SIZES)
    fuse_ok = [s for s in sizes if lv_wire_ampacity(s) >= fuse - EPS]
    wire = next((s for s in fuse_ok if worst(s) <= num(limit_pct) + EPS), None)
    if wire is None:
        wire = fuse_ok[-1] if fuse_ok else sizes[-1]
    drop = worst(wire)
    drop_ok = drop <= num(limit_pct) + EPS
    wire_ft = sum(br["home_ft"] + br["bus_ft"] for br in branches)
    over_fuse = amps_each > 0.8 * fuse + EPS
    warnings = []
    if over_fuse:
        warnings.append(f"One {kind} draws {amps_each:.2f} A, more than 80% of a {fuse:g} A branch fuse.")
    if not drop_ok:
        warnings.append(
            f"Drop at the farthest {kind} is {drop:.1f}% even in {wire} AWG; move the supply to the center "
            "or shorten the run to it."
        )
    if kind == "luminaire":
        warnings.append(
            "Underwater luminaires need a listed transformer or power supply per 680.23(A)(2); "
            "check the fixture's own minimum voltage."
        )
    warnings.append(edition_warning())
    return CalcResult(
        calc="low_voltage_run",
        value={
            "wire": wire,
            "fuse_a": fuse,
            "class2": class2,
            "per_branch": per,
            "branch_count": n_branches,
            "branches": [{k: round(x, 4) if isinstance(x, float) else x for k, x in br.items()} for br in branches],
            "drop_pct": round(drop, 3),
            "drop_ok": drop_ok,
            "wire_ft": round(wire_ft, 2),
            "amps_each": round(amps_each, 4),
            "total_w": round(n * w_each, 2),
            "over_fuse": over_fuse,
        },
        unit="",
        inputs={
            "count": make_input(n, "", "user"),
            "watts_each": make_input(w_each, "W", "user"),
            "spacing_ft": make_input(pitch, "ft", "user"),
            "voltage": make_input(v, "V", "user"),
            "feed_ft": make_input(num(feed_ft), "ft", "user", "supply to the near end, one way"),
            "supply_at": make_input(supply_at, "", "user"),
            "limit_pct": make_input(num(limit_pct), "%", "default"),
        },
        formula=(
            "per branch = devices fitting 0.8 x fuse x V (whole groups) ; "
            "drop% = 100 x I_branch x 2R x (home + bus/2) / V at the worst branch"
        ),
        steps=[
            f"{n} x {w_each:g} W at {v:g} V = {amps_each:.3f} A each",
            f"branch fuse {fuse:g} A ({'Class 2, 100 VA' if class2 else 'set'}) -> {per} per branch, "
            f"{n_branches} branch(es)",
            f"wire {wire} AWG: {drop:.2f}% at the farthest {kind} (limit {num(limit_pct):g}%)",
            f"wire length {wire_ft:.1f} ft (2-conductor, before waste)",
        ],
        citations=[CIT_TABLE_8, CIT_CLASS2, "Digital Rain Curtain src/electrical.js coilPlan"],
        warnings=warnings,
        status="good" if drop_ok and not over_fuse else "warn",
    )


__all__ = [
    "BRANCH_DROP_PCT",
    "LV_LIGHTING_WIRE_SIZES",
    "LV_WIRE_SIZES",
    "TOTAL_DROP_PCT",
    "drop_volts",
    "low_voltage_run",
    "lv_wire_ampacity",
    "resistance_ohm_per_kft",
    "size_for_drop",
    "total_drop",
    "voltage_drop",
]
