"""Water treatment / chemistry sizing (Phase 2).

Verified against DOC-0049 ``C - Chemicals`` (the Chemical Rate Advisor) and
DOC-0119 (target ranges):

* Liquid chlorinator minimum feed (C36):
    gph = volume_gal * 3 / (24 * 10000)   [10% chlorine; 1 gal @ 10% = 1 lb Cl2]
  Scaled by 10/strength for other chlorine concentrations.
* Ozone side-stream sizing (C14..C29): flow → side-stream flow → contact tank
  check → contact time → ozone concentration (from the USEPA CT value) → ozone
  required (g/hr).
* Water-balance target ranges (free chlorine, pH, cyanuric acid) by water type.
"""

from __future__ import annotations

from .constants import (
    CHLORINE_MIN_LBS_PER_10KGAL_DAY,
    CHLORINE_REF_PCT,
    CIT_CHEM,
    CIT_CHEM_TARGETS,
    CT_CRYPTO_2LOG,
    CT_CRYPTO_3LOG,
    OZONE_GHR_FACTOR,
)
from .data.chemistry import CHEM_TARGETS, CONTACT_TANKS
from .envelope import CalcResult, make_input


def chlorinator_feed(volume_gal: float, chlorine_pct: float = CHLORINE_REF_PCT) -> CalcResult:
    """Minimum liquid-chlorinator feed rate (gal/hr) for a system volume.

    IBC 3133B.1 minimum is 3 lb Cl2 per 24 hr per 10,000 gal; at 10% chlorine
    1 gal delivers 1 lb Cl2, so a stronger product needs proportionally less.
    """
    volume_gal = float(volume_gal)
    chlorine_pct = float(chlorine_pct) or CHLORINE_REF_PCT
    base_gph = volume_gal * CHLORINE_MIN_LBS_PER_10KGAL_DAY / (24 * 10000)
    gph = base_gph * (CHLORINE_REF_PCT / chlorine_pct)
    lbs_day = volume_gal * CHLORINE_MIN_LBS_PER_10KGAL_DAY / 10000
    return CalcResult(
        calc="chlorinator_feed",
        value=gph,
        unit="gal/hr",
        inputs={
            "volume": make_input(volume_gal, "gal", "prior_calc", "system volume"),
            "chlorine_pct": make_input(chlorine_pct, "%", "user", "default 10%"),
        },
        formula="gph = volume_gal * 3 / (24 * 10000) * (10 / chlorine_pct)",
        steps=[
            f"min Cl2 = {volume_gal:g} * 3 / 10000 = {lbs_day:.3f} lb/day",
            f"gph(10%) = {volume_gal:g} * 3 / (24*10000) = {base_gph:.4f}",
            f"gph({chlorine_pct:g}%) = {base_gph:.4f} * 10/{chlorine_pct:g} = {gph:.4f} gal/hr",
        ],
        citations=[CIT_CHEM],
    )


def chemistry_targets(
    water_type: str = "outdoor",
    cya_ppm: float | None = None,
    free_cl_ppm: float | None = None,
) -> CalcResult:
    """Water-balance target ranges (free chlorine, pH, cyanuric acid).

    DOC-0119: when cyanuric acid (CYA) is present, free chlorine must hold a
    higher floor — ``>= 7.5% of the CYA level`` and ``>= 2 ppm``. Pass a
    ``cya_ppm`` (planned/measured stabilizer) to compute that floor; pass a
    ``free_cl_ppm`` (the level you intend to run) to be warned when it falls
    below the floor (chlorine goes ineffective / the water is under-sanitized).
    """
    key = (water_type or "outdoor").strip().lower()
    targets = CHEM_TARGETS.get(key)
    if not targets:
        return CalcResult(
            calc="chemistry_targets",
            inputs={"water_type": make_input(water_type, "", "user")},
            citations=[CIT_CHEM_TARGETS],
            warnings=[f"Unknown water type {water_type!r}. Use one of {list(CHEM_TARGETS)}."],
        )
    fc, ph, cya = targets["free_cl_ppm"], targets["ph"], targets["cya_ppm"]
    steps = [
        f"free chlorine: {fc[0]}-{fc[1]} ppm",
        f"pH: {ph[0]}-{ph[1]}",
    ]
    if cya[1]:
        steps.append(f"cyanuric acid (CYA): {cya[0]}-{cya[1]} ppm; keep free Cl >= 7.5% of CYA")
    else:
        steps.append("cyanuric acid (CYA): not used indoors")

    warnings: list[str] = []
    inputs = {"water_type": make_input(key, "", "user")}
    # CYA-coupled free-chlorine floor (DOC-0119): >= 7.5% of CYA and >= 2 ppm.
    effective_cya = float(cya_ppm) if cya_ppm is not None else (cya[1] or 0)
    if effective_cya > 0:
        fc_floor = max(2.0, round(0.075 * effective_cya, 2))
        basis = "your CYA" if cya_ppm is not None else f"max target CYA {cya[1]}"
        steps.append(
            f"free Cl floor at {basis} {effective_cya:g} ppm = max(2.0, 7.5% x {effective_cya:g}) "
            f"= {fc_floor:g} ppm"
        )
        inputs["cya"] = make_input(effective_cya, "ppm", "user" if cya_ppm is not None else "default")
        inputs["free_cl_floor"] = make_input(fc_floor, "ppm", "calc", "DOC-0119 7.5% of CYA, >=2")
        if fc[1] < fc_floor:
            warnings.append(
                f"At CYA {effective_cya:g} ppm the free-chlorine floor is {fc_floor:g} ppm, above the "
                f"standard {key} target max of {fc[1]} ppm — raise free chlorine or lower CYA."
            )
        if free_cl_ppm is not None:
            inputs["free_cl"] = make_input(float(free_cl_ppm), "ppm", "user")
            if float(free_cl_ppm) < fc_floor:
                warnings.append(
                    f"Free chlorine {float(free_cl_ppm):g} ppm is below the {fc_floor:g} ppm floor for "
                    f"CYA {effective_cya:g} ppm (DOC-0119) — chlorine is under-effective; raise it."
                )
    return CalcResult(
        calc="chemistry_targets",
        value=key,
        unit="ranges",
        inputs=inputs,
        formula="DOC-0119 water-balance target ranges; free Cl floor = max(2.0, 0.075*CYA)",
        steps=steps,
        citations=[CIT_CHEM_TARGETS],
        warnings=warnings,
    )


def ozone_sidestream(
    volume_gal: float,
    turnover_min: float,
    sidestream_pct: float = 0.25,
    contact_tank: str = "CNT120",
    tank_qty: int = 1,
    log_reduction: str = "2-log",
) -> CalcResult:
    """Ozone side-stream sizing: ozone required (g/hr) plus the contact-tank
    adequacy check and contact time (DOC-0049 C - Chemicals)."""
    volume_gal = float(volume_gal)
    turnover_min = float(turnover_min)
    sidestream_pct = float(sidestream_pct)
    tank_qty = int(tank_qty or 1)
    warnings = []

    if turnover_min <= 0:
        return CalcResult(
            calc="ozone_sidestream",
            unit="g/hr",
            citations=[CIT_CHEM],
            warnings=["Turnover (minutes) must be > 0."],
        )

    tank = CONTACT_TANKS.get((contact_tank or "").strip().upper())
    if not tank:
        return CalcResult(
            calc="ozone_sidestream",
            unit="g/hr",
            citations=[CIT_CHEM],
            warnings=[f"Unknown contact tank {contact_tank!r}. Use one of {list(CONTACT_TANKS)}."],
        )

    ct_value = CT_CRYPTO_3LOG if str(log_reduction).startswith("3") else CT_CRYPTO_2LOG
    full_flow = volume_gal / turnover_min  # GPM
    side_flow = full_flow * sidestream_pct
    contact_vol = tank_qty * tank["volume_gal"]
    contact_flow = tank_qty * tank["max_gpm"]
    status = "Okay" if contact_flow > side_flow else "Need Larger or More Contact Tanks"
    contact_time = contact_vol / side_flow if side_flow else 0.0
    concentration = ct_value / contact_time if contact_time else 0.0
    ozone_ghr = side_flow * concentration * OZONE_GHR_FACTOR

    if status != "Okay":
        warnings.append(
            f"Contact-tank flow {contact_flow:g} GPM <= side-stream {side_flow:.2f} GPM; "
            "select a larger or additional contact tank."
        )
    return CalcResult(
        calc="ozone_sidestream",
        value=ozone_ghr,
        unit="g/hr",
        inputs={
            "volume": make_input(volume_gal, "gal", "prior_calc"),
            "turnover": make_input(turnover_min, "min", "user"),
            "sidestream_pct": make_input(sidestream_pct, "fraction", "user", "0.15-0.30, 0.25 ideal"),
            "contact_tank": make_input(contact_tank, "", "user", "ContactTanks"),
            "tank_qty": make_input(tank_qty, "", "user"),
            "ct_value": make_input(ct_value, "mg/L*min", "lookup", f"USEPA Crypto {log_reduction}"),
        },
        formula="ozone_g/hr = side_flow * (CT / contact_time) * (3780*60/1e6)",
        steps=[
            f"full flow = {volume_gal:g}/{turnover_min:g} = {full_flow:.3f} GPM",
            f"side-stream flow = {full_flow:.3f} * {sidestream_pct:g} = {side_flow:.3f} GPM",
            f"contact tank: {contact_vol:g} gal, {contact_flow:g} GPM max -> {status}",
            f"contact time = {contact_vol:g}/{side_flow:.3f} = {contact_time:.3f} min",
            f"concentration = {ct_value}/{contact_time:.3f} = {concentration:.4f} mg/L",
            f"ozone = {side_flow:.3f} * {concentration:.4f} * {OZONE_GHR_FACTOR:.5f} = {ozone_ghr:.4f} g/hr",
        ],
        citations=[CIT_CHEM],
        status=status,
        warnings=warnings,
    )
