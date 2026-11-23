"""Transformers: the low-voltage lighting transformer (1.25 x fixture watts) and
the control-panel transformer (sealed VA of the control devices), with primary
protection per 450.3(B) and the inrush note.
"""

from __future__ import annotations

import math

from ..constants import CIT_CONTROL_XFMR, CONTROL_DEVICE_VA, STD_CONTROL_XFMR_VA
from ..data.nec_tables import CIT_XFMR_PRIMARY, XFMR_PRIMARY_ONLY_PERCENT
from ..envelope import CalcResult, make_input
from ._util import edition_warning, next_standard, num, standard_not_exceeding

LIGHTING_VA_FACTOR = 1.25  # transformer VA >= 1.25 x fixture watts (industry report 1.11)
CIT_LIGHTING_XFMR = "Crystal Fountains lighting guide (VA = W x 1.25); NEC 680.23(A)(2)"
# Pool/landscape lighting transformer ratings (VA): the sizes Intermatic, Hayward
# and Pentair sell. A design that needs more uses several.
STD_LIGHTING_XFMR_VA = [60, 100, 150, 200, 300, 600, 900, 1200]
LV_SECONDARY_MAX_A = 25.0  # a 12 V landscape/pool transformer's secondary circuits: 25 A each (UL 1838 / 379)
INRUSH_NOTE = (
    "A magnetic (toroidal or EI) transformer draws 10-25 times its rated current for the first "
    "half-cycle; use a breaker with a high magnetic trip or an HID/inrush-rated device if it trips "
    "on energizing."
)


def primary_ocpd_max(va: float, primary_v: float) -> CalcResult:
    """Largest primary-only OCPD (A) for a transformer of 1000 V or less, per Table
    450.3(B): 125% of rated primary current at 9 A or more (next standard size up
    allowed, Note 1), 167% from 2 to 9 A, 300% under 2 A (not exceeding)."""
    i = num(va) / num(primary_v, 120) if num(primary_v) > 0 else 0.0
    pct = next(p for floor, p in XFMR_PRIMARY_ONLY_PERCENT if i >= floor)
    raw = i * pct / 100.0
    rating = next_standard(raw) if i >= 9 else standard_not_exceeding(raw, [1, 3, 6, 10, 15, 20, 25, 30])
    return CalcResult(
        calc="primary_ocpd_max",
        value=rating,
        unit="A",
        inputs={
            "va": make_input(num(va), "VA", "prior_calc"),
            "primary_v": make_input(num(primary_v), "V", "user"),
            "primary_a": make_input(round(i, 3), "A", "prior_calc"),
            "percent": make_input(pct, "%", "standard", "Table 450.3(B)"),
        },
        formula="I = VA / V ; max OCPD = I x 125% (>= 9 A, next size up) / 167% (2-9 A) / 300% (< 2 A)",
        steps=[f"I = {num(va):g} / {num(primary_v):g} = {i:.2f} A", f"{pct}% -> {raw:.2f} A -> {rating} A"],
        citations=[CIT_XFMR_PRIMARY],
        warnings=[
            "Under 9 A the limit is a ceiling with no next-size-up allowance; a 15 A branch breaker can "
            "protect the primary only if the transformer's listing allows it (most listed lighting "
            "transformers carry their own primary protection).",
            edition_warning(),
        ],
    )


def lighting_transformer(
    fixture_watts: float,
    secondary_v: float = 12.0,
    primary_v: float = 120.0,
) -> CalcResult:
    """Low-voltage lighting transformer: VA at 1.25 x the fixture watts, the next
    standard rating (several units past the largest), its primary current, the
    450.3(B) primary limit and the secondary current per circuit."""
    w = num(fixture_watts)
    need = LIGHTING_VA_FACTOR * w
    biggest = STD_LIGHTING_XFMR_VA[-1]
    units = max(1, math.ceil(need / biggest)) if need > 0 else 0
    each = next((s for s in STD_LIGHTING_XFMR_VA if s * units >= need), biggest) if units else 0
    total = each * units
    primary_a = total / num(primary_v, 120) if total else 0.0
    secondary_a = w / num(secondary_v, 12) if w else 0.0
    circuits = max(1, math.ceil(secondary_a / LV_SECONDARY_MAX_A)) if secondary_a else 0
    prim = primary_ocpd_max(total, primary_v) if total else None
    warnings = [INRUSH_NOTE]
    if circuits > units:
        warnings.append(
            f"{secondary_a:.1f} A at {num(secondary_v):g} V needs {circuits} secondary circuits of "
            f"{LV_SECONDARY_MAX_A:g} A; split the fixtures across them."
        )
    warnings.append(
        "Underwater luminaire transformers must be listed for the purpose, with an ungrounded "
        "secondary and a grounded metal barrier between windings (680.23(A)(2))."
    )
    warnings.append(edition_warning())
    return CalcResult(
        calc="lighting_transformer",
        value=total,
        unit="VA",
        inputs={
            "fixture_watts": make_input(w, "W", "prior_calc"),
            "required_va": make_input(round(need, 1), "VA", "prior_calc"),
            "units": make_input(units, "", "prior_calc"),
            "va_each": make_input(each, "VA", "lookup", "standard lighting transformer"),
            "secondary_v": make_input(num(secondary_v), "V", "user"),
            "secondary_a": make_input(round(secondary_a, 2), "A", "prior_calc"),
            "secondary_circuits": make_input(circuits, "", "prior_calc"),
            "primary_v": make_input(num(primary_v), "V", "user"),
            "primary_a": make_input(round(primary_a, 2), "A", "prior_calc"),
            "primary_ocpd_max_a": make_input(prim.value if prim else None, "A", "prior_calc", "450.3(B)"),
        },
        formula="VA >= 1.25 x fixture W ; next standard rating ; I_primary = VA / V_primary",
        steps=[
            f"1.25 x {w:g} W = {need:.0f} VA",
            f"{units} x {each} VA = {total} VA",
            f"primary {total} / {num(primary_v):g} V = {primary_a:.2f} A ; secondary {secondary_a:.2f} A",
        ],
        citations=[CIT_LIGHTING_XFMR, CIT_XFMR_PRIMARY],
        warnings=warnings,
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


__all__ = [
    "LIGHTING_VA_FACTOR",
    "STD_LIGHTING_XFMR_VA",
    "control_transformer_va",
    "lighting_transformer",
    "primary_ocpd_max",
]
