"""Conductor sizing: ampacity (Table 310.16), the 125% rules (430.22 for motors,
210.19(A)(1) / 215.2(A)(1) for continuous loads), ambient and conductor-count
corrections (310.15(B) / (C)), termination temperature (110.14(C)), protection by
the OCPD (240.4 with the small-conductor limits of 240.4(D)), and the equipment
grounding conductor (250.122).

Two things that read alike and are not:

* Motor circuits apply 125% of FLC *and* the corrections together: 430.22 asks
  for an ampacity of 125% FLC, and ampacity is the table value after correction.
  240.4(D) does not apply to them (240.4(G)), so a #14 motor circuit on a 20 A
  breaker is legal when 430.52 allows the breaker.
* Continuous non-motor loads take two separate checks (210.19(A)(1)(a)/(b)):
  125% of the continuous load against the table ampacity *before* correction, and
  the plain load against the ampacity *after* correction. They are never
  multiplied together.

Default insulation is THHN/THWN-2 (90 C), the wire in every Sapphire raceway; the
90 C column is used only to derate. Terminations default to 60 C at 100 A and
below (110.14(C)(1)(a)) unless ``termination_c`` says the equipment is listed for
75 C. That is why a 5 HP 230 V pump (28 A FLC, 35 A at 125%) gets #8 rather than
#10: #10 carries 35 A only at 75 C.
"""

from __future__ import annotations

from ..data.nec_tables import (
    AMPACITY_310_16,
    CIT_AMPACITY,
    CIT_COUNT_ADJUSTMENT,
    CIT_EGC,
    CIT_SMALL_CONDUCTOR,
    CIT_TEMP_CORRECTION,
    CIT_TERMINATION,
    CONDUCTOR_COUNT_ADJUSTMENT,
    CONDUCTOR_PROPS,
    EGC_250_122,
    POOL_EGC_MIN_AWG,
    SIZES,
    SMALL_CONDUCTOR_MAX_OCPD,
    TEMP_COLUMNS,
    TEMP_CORRECTION,
    TERMINATION_SMALL_MAX_A,
    size_index,
)
from ..envelope import CalcResult, make_input
from ._util import EPS, edition_warning, next_standard, num, phase_of
from .ocpd import CONDUCTOR_FACTOR_430_22, CONTINUOUS_FACTOR
from .voltage_drop import BRANCH_DROP_PCT, voltage_drop

CIT_CONTINUOUS_CONDUCTOR = "NEC 210.19(A)(1) / 215.2(A)(1) (continuous loads at 125%)"
CIT_MOTOR_CONDUCTOR = "NEC 430.22 (motor branch conductors at 125% of FLC)"
CIT_OCPD_PROTECTION = "NEC 240.4(B) (next size up to 800 A) / 240.4(G) (motor circuits)"


def f_to_c(temp_f: float) -> float:
    return (num(temp_f) - 32.0) / 1.8


def _col(temp_c) -> int:
    t = int(num(temp_c, 90))
    return t if t in TEMP_COLUMNS else 90


def ampacity(size: str, material: str = "Cu", temp_c: int = 75) -> int | None:
    """Table 310.16 ampacity (A) at a temperature column; None if not listed."""
    row = AMPACITY_310_16.get(material if material == "Al" else "Cu", {}).get(str(size))
    return row[TEMP_COLUMNS.index(_col(temp_c))] if row else None


def temperature_correction(ambient_f: float = 86.0, insulation_c: int = 90) -> float | None:
    """Table 310.15(B)(1) factor for the ambient (deg F in, banded in deg C);
    None when the insulation may not be used at that ambient."""
    amb = round(f_to_c(ambient_f))
    idx = TEMP_COLUMNS.index(_col(insulation_c))
    for upper, factors in TEMP_CORRECTION:
        if amb <= upper:
            return factors[idx]
    return None


def count_adjustment(current_carrying: int = 3) -> float:
    """Table 310.15(C)(1) factor for the number of current-carrying conductors."""
    n = int(num(current_carrying, 3))
    return next(f for upper, f in CONDUCTOR_COUNT_ADJUSTMENT if n <= upper)


def termination_temp(circuit_a: float, size: str, termination_c: int | None = None) -> int:
    """110.14(C)(1): 60 C for circuits of 100 A or less or 14-1 AWG, else 75 C,
    unless the equipment's listed rating is given."""
    if termination_c:
        return 75 if int(termination_c) >= 75 else 60
    small = num(circuit_a) <= TERMINATION_SMALL_MAX_A and size_index(size) <= size_index("1")
    return 60 if small else 75


def size_conductor(
    load_amps: float,
    *,
    continuous: bool = True,
    is_motor: bool = False,
    material: str = "Cu",
    insulation_c: int = 90,
    termination_c: int | None = None,
    ambient_f: float = 86.0,
    current_carrying: int = 3,
    ocpd_a: float | None = None,
    min_size: str | None = None,
) -> CalcResult:
    """Smallest conductor (AWG/kcmil string) for a load, by ampacity alone.

    ``load_amps`` is the motor FLC for a motor, else the load current;
    ``continuous`` marks a load that runs 3 hours or more. ``ocpd_a`` is the
    breaker or fuse ahead of it (protection check, skipped for motors).
    """
    material = "Al" if material == "Al" else "Cu"
    load = num(load_amps)
    ins = _col(insulation_c)
    if is_motor:
        need_table = need_derated = CONDUCTOR_FACTOR_430_22 * load
        rule = f"430.22: 1.25 x {load:g} = {need_table:.2f} A (after correction)"
    else:
        need_table = CONTINUOUS_FACTOR * load if continuous else load
        need_derated = load
        rule = (
            f"210.19(A)(1): {need_table:.2f} A before correction, {need_derated:.2f} A after"
            if continuous
            else f"{load:.2f} A (noncontinuous)"
        )
    tf = temperature_correction(ambient_f, ins)
    cf = count_adjustment(current_carrying)
    floor = min_size or ("12" if material == "Al" else "14")
    warnings = []
    if tf is None:
        return CalcResult(
            calc="size_conductor",
            unit="AWG/kcmil",
            formula="table ampacity x temperature factor x count factor >= required",
            citations=[CIT_TEMP_CORRECTION],
            warnings=[f"{ins} C insulation cannot be used at {num(ambient_f):g} F ambient."],
        )
    derate = tf * cf
    chosen = None
    detail = ""
    circuit_a = num(ocpd_a) or need_table
    for size in SIZES[size_index(floor):]:
        base = ampacity(size, material, ins)
        if base is None:
            continue
        term_c = termination_temp(circuit_a, size, termination_c)
        term_amp = ampacity(size, material, term_c) or 0
        derated = base * derate
        usable = min(derated, term_amp)
        if term_amp + EPS < need_table or derated + EPS < need_derated:
            continue
        if is_motor and usable + EPS < need_derated:
            continue
        if not is_motor and ocpd_a:
            ocpd = num(ocpd_a)
            protected = (next_standard(usable) if usable <= 800 else usable) >= ocpd - EPS
            small_max = SMALL_CONDUCTOR_MAX_OCPD[material].get(size)
            if not protected or (small_max is not None and ocpd > small_max + EPS):
                continue
        chosen = size
        detail = (
            f"{size} {material}: {base} A at {ins} C x {tf:g} x {cf:g} = {derated:.1f} A ; "
            f"{term_amp} A at the {term_c} C terminations"
        )
        break
    if chosen is None:
        chosen = SIZES[-1]
        warnings.append("No single conductor in Table 310.16 carries this load; use parallel sets (310.10(G)).")
    warnings.append(edition_warning())
    citations = [CIT_AMPACITY, CIT_TEMP_CORRECTION, CIT_COUNT_ADJUSTMENT, CIT_TERMINATION]
    citations.append(CIT_MOTOR_CONDUCTOR if is_motor else CIT_CONTINUOUS_CONDUCTOR)
    if not is_motor and ocpd_a:
        citations += [CIT_OCPD_PROTECTION, CIT_SMALL_CONDUCTOR]
    return CalcResult(
        calc="size_conductor",
        value=chosen,
        unit="AWG/kcmil",
        inputs={
            "load_a": make_input(round(load, 3), "A", "prior_calc"),
            "required_table_a": make_input(round(need_table, 2), "A", "prior_calc"),
            "required_corrected_a": make_input(round(need_derated, 2), "A", "prior_calc"),
            "material": make_input(material, "", "user"),
            "insulation_c": make_input(ins, "C", "default", "THHN/THWN-2"),
            "ambient_f": make_input(num(ambient_f), "F", "user"),
            "temperature_factor": make_input(tf, "", "lookup", "Table 310.15(B)(1)"),
            "count_factor": make_input(cf, "", "lookup", "Table 310.15(C)(1)"),
            "current_carrying": make_input(int(num(current_carrying, 3)), "", "user"),
            "ocpd_a": make_input(num(ocpd_a), "A", "prior_calc"),
        },
        formula=(
            "smallest size with table ampacity at the termination column >= required, and "
            "ampacity x temp factor x count factor >= required ; protected by the OCPD (240.4)"
        ),
        steps=[rule, f"corrections: {tf:g} (ambient) x {cf:g} ({int(num(current_carrying, 3))} conductors)", detail],
        citations=citations,
        warnings=warnings,
    )


def egc_size(
    ocpd_a: float,
    material: str = "Cu",
    *,
    ungrounded_min: str | None = None,
    ungrounded_actual: str | None = None,
    pool: bool = False,
) -> CalcResult:
    """Equipment grounding conductor from Table 250.122 by the OCPD rating, grown
    by the circular-mil ratio when the ungrounded conductors were upsized beyond
    their ampacity size (250.122(B)), and at least 12 AWG insulated copper on a
    pool or spa motor or luminaire circuit (680.21(A)(1), 680.23(F)(2))."""
    material = "Al" if material == "Al" else "Cu"
    ocpd = num(ocpd_a)
    row = next((r for r in EGC_250_122 if ocpd <= r[0] + EPS), EGC_250_122[-1])
    size = row[2] if material == "Al" else row[1]
    steps = [f"{ocpd:g} A OCPD -> {size} {material} (Table 250.122)"]
    if ungrounded_min and ungrounded_actual and size_index(ungrounded_actual) > size_index(ungrounded_min):
        ratio = CONDUCTOR_PROPS[ungrounded_actual][0] / CONDUCTOR_PROPS[ungrounded_min][0]
        need_cmil = CONDUCTOR_PROPS[size][0] * ratio
        grown = next((s for s in SIZES if CONDUCTOR_PROPS[s][0] >= need_cmil - EPS), SIZES[-1])
        # Never larger than the ungrounded conductor itself (250.122(A)).
        if size_index(grown) > size_index(ungrounded_actual):
            grown = ungrounded_actual
        if size_index(grown) > size_index(size):
            steps.append(
                f"ungrounded upsized {ungrounded_min} -> {ungrounded_actual} (x{ratio:.2f} cmil) -> EGC {grown} "
                "(250.122(B))"
            )
            size = grown
    if pool and material == "Cu" and size_index(size) < size_index(POOL_EGC_MIN_AWG):
        steps.append(f"pool/spa circuit: insulated copper EGC >= {POOL_EGC_MIN_AWG} AWG -> {POOL_EGC_MIN_AWG}")
        size = POOL_EGC_MIN_AWG
    return CalcResult(
        calc="egc_size",
        value=size,
        unit="AWG/kcmil",
        inputs={
            "ocpd_a": make_input(ocpd, "A", "prior_calc"),
            "material": make_input(material, "", "user"),
            "pool": make_input(bool(pool), "", "user"),
        },
        formula="EGC = Table 250.122 by OCPD ; x (actual / minimum ungrounded cmil) when upsized (250.122(B))",
        steps=steps,
        citations=[CIT_EGC] + (["NEC 680.21(A)(1) / 680.23(F)(2)"] if pool else []),
        warnings=[edition_warning()],
    )


def wire_for_load(
    load_amps: float,
    *,
    voltage: float,
    phase: int = 1,
    length_ft: float = 0.0,
    continuous: bool = True,
    is_motor: bool = False,
    ocpd_a: float | None = None,
    material: str = "Cu",
    insulation_c: int = 90,
    termination_c: int | None = None,
    ambient_f: float = 86.0,
    current_carrying: int | None = None,
    max_drop_pct: float = BRANCH_DROP_PCT,
    pool: bool = False,
) -> CalcResult:
    """The conductor a circuit needs: sized for ampacity, then upsized until the
    drop over ``length_ft`` (one way) is within ``max_drop_pct``, with its EGC.

    ``value`` is the ungrounded conductor size; ``inputs`` carries the ampacity
    size, the drop, and the EGC (``egc``), so the panel schedule and the BOM read
    one result.
    """
    ph = phase_of(phase)
    ccc = int(num(current_carrying, 3 if ph == 3 else 2)) if current_carrying else (3 if ph == 3 else 2)
    amp = size_conductor(
        load_amps,
        continuous=continuous,
        is_motor=is_motor,
        material=material,
        insulation_c=insulation_c,
        termination_c=termination_c,
        ambient_f=ambient_f,
        current_carrying=ccc,
        ocpd_a=ocpd_a,
    )
    if amp.value is None:
        return amp
    size = amp.value
    steps = [f"ampacity: {size} {material}"]
    drop = voltage_drop(load_amps, length_ft, size, voltage, ph, material, max_drop_pct)
    if num(length_ft) > 0 and (drop.value or 0) > num(max_drop_pct) + EPS:
        for s in SIZES[size_index(size) + 1:]:
            d = voltage_drop(load_amps, length_ft, s, voltage, ph, material, max_drop_pct)
            size, drop = s, d
            if (d.value or 0) <= num(max_drop_pct) + EPS:
                break
        steps.append(f"voltage drop: upsized to {size} for {drop.value:.2f}% over {num(length_ft):g} ft")
    else:
        steps.append(f"voltage drop: {drop.value:.2f}% over {num(length_ft):g} ft")
    ocpd_for_egc = num(ocpd_a) or next_standard(CONTINUOUS_FACTOR * num(load_amps))
    egc = egc_size(ocpd_for_egc, material, ungrounded_min=amp.value, ungrounded_actual=size, pool=pool)
    steps.append(f"EGC: {egc.value} {material}")
    warnings = [*list(drop.warnings), edition_warning()]
    if is_motor:
        warnings.insert(0, "Conductors sized from the NEC FLC table (430.6(A)(1)); overloads from the nameplate.")
    return CalcResult(
        calc="wire_for_load",
        value=size,
        unit="AWG/kcmil",
        inputs={
            "load_a": make_input(round(num(load_amps), 3), "A", "prior_calc"),
            "voltage": make_input(num(voltage), "V", "user"),
            "phase": make_input(ph, "", "user"),
            "length_ft": make_input(num(length_ft), "ft", "user", "one way to the panel"),
            "ampacity_size": make_input(amp.value, "AWG/kcmil", "prior_calc", "size_conductor"),
            "drop_pct": make_input(drop.value, "%", "prior_calc", "voltage_drop"),
            "egc": make_input(egc.value, "AWG/kcmil", "prior_calc", "egc_size"),
            "current_carrying": make_input(ccc, "", "default"),
            "material": make_input(material, "", "user"),
        },
        formula="size = max(ampacity size, voltage-drop size) ; EGC per 250.122 (+ 250.122(B) if upsized)",
        steps=amp.steps + steps,
        citations=list(dict.fromkeys(amp.citations + drop.citations + egc.citations)),
        warnings=list(dict.fromkeys(warnings)),
        status=drop.status,
    )


__all__ = [
    "ampacity",
    "count_adjustment",
    "egc_size",
    "f_to_c",
    "size_conductor",
    "temperature_correction",
    "termination_temp",
    "wire_for_load",
]
