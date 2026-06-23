"""Pump selection (catalog lookup) and electrical/breaker sizing.

The Sapphire workbooks size a pump by matching the required flow + TDH against a
catalog and transcribing the nameplate — there is NO pump-curve formula, breaker
rule, or VFD-vs-starter logic in the sheets. So:

* :func:`select_pump` is a catalog lookup. Candidates come from the caller
  (ERPNext Items, ``item_group`` "Pumps"); the engine stays pure and never
  queries the DB.
* :func:`electrical_load` applies the 125%-FLA continuous-duty rule
  (NEC 430.52). This is a business rule, not a source-document formula — it is
  flagged in ``warnings`` for the engineer to confirm.
"""

from __future__ import annotations

import math

from .constants import BREAKER_CONTINUOUS_FACTOR
from .envelope import CalcOption, CalcResult, make_input

# Standard inverse-time breaker ampere ratings (NEC 240.6) for rounding up.
_STD_BREAKERS = [15, 20, 25, 30, 35, 40, 45, 50, 60, 70, 80, 90, 100, 110, 125, 150, 175, 200]


def select_pump(flow_gpm: float, tdh_ft: float, candidates: list[dict] | None = None) -> CalcResult:
    """Pick the smallest catalog pump whose rated flow AND head cover the duty
    point. ``candidates`` = ``[{item_code, rated_gpm, rated_tdh_ft, ...}]``."""
    flow_gpm = float(flow_gpm)
    tdh_ft = float(tdh_ft)
    candidates = candidates or []

    if not candidates:
        return CalcResult(
            calc="select_pump",
            unit="catalog match",
            inputs={
                "design_flow": make_input(flow_gpm, "GPM", "prior_calc"),
                "tdh": make_input(tdh_ft, "ft", "prior_calc"),
            },
            formula="select pump where rated_gpm >= design_flow AND rated_tdh_ft >= TDH",
            citations=["DOC-0048 / Pumps", "DOC-0028 / Part Numbers"],
            warnings=[
                "No pump catalog supplied. Provide candidates from ERPNext Items "
                "(item_group 'Pumps') with rated_gpm / rated_tdh_ft to size the pump."
            ],
        )

    def adequate(c: dict) -> bool:
        # Always require enough flow. Require head only when the catalog has a
        # head rating — fountain submersibles are spec'd by GPH (flow); the head
        # ("max lift") is often not on file. An unknown head doesn't exclude a
        # pump; the chosen one is flagged for a pump-curve check instead.
        head = c.get("rated_tdh_ft") or 0
        return (c.get("rated_gpm") or 0) >= flow_gpm and (head >= tdh_ft if head else True)

    ranked = sorted(
        candidates,
        # adequate first; then prefer a known head rating; then the smallest pump.
        key=lambda c: (not adequate(c), not c.get("rated_tdh_ft"), (c.get("rated_gpm") or 0)),
    )
    options: list[CalcOption] = []
    recommended = None
    for c in ranked:
        ok = adequate(c)
        if recommended is None and ok:
            recommended = c.get("item_code") or c.get("part_number")
        options.append(
            CalcOption(
                key=str(c.get("item_code") or c.get("part_number") or c.get("label") or "?"),
                label=str(c.get("description") or c.get("item_code") or c.get("part_number") or "pump"),
                value=c.get("item_code") or c.get("part_number"),
                recommended=(recommended is not None and recommended == (c.get("item_code") or c.get("part_number"))),
                detail={
                    "rated_gpm": c.get("rated_gpm"),
                    "rated_tdh_ft": c.get("rated_tdh_ft"),
                    "meets_duty": ok,
                    "head_known": bool(c.get("rated_tdh_ft")),
                    "hp": c.get("hp"),
                    "phase": c.get("phase"),
                    "voltage": c.get("voltage"),
                },
            )
        )

    warnings = []
    if recommended is None:
        warnings.append(
            f"No supplied pump covers {flow_gpm} GPM @ {tdh_ft:.1f} ft TDH; "
            "consider a larger pump or splitting the load."
        )
    else:
        rec_opt = next((o for o in options if o.recommended), None)
        if rec_opt and not rec_opt.detail.get("rated_tdh_ft"):
            warnings.append(
                f"{recommended}: matched on flow only — no head rating on file. Confirm it "
                f"delivers {tdh_ft:.1f} ft TDH against the manufacturer pump curve."
            )
    return CalcResult(
        calc="select_pump",
        value=recommended,
        unit="catalog match",
        inputs={
            "design_flow": make_input(flow_gpm, "GPM", "prior_calc"),
            "tdh": make_input(tdh_ft, "ft", "prior_calc"),
        },
        formula="select smallest pump where rated_gpm >= design_flow AND rated_tdh_ft >= TDH",
        steps=[f"duty point = {flow_gpm:.2f} GPM @ {tdh_ft:.2f} ft TDH"],
        citations=["DOC-0048 / Pumps", "DOC-0028 / Part Numbers"],
        options=options,
        warnings=warnings,
    )


def electrical_load(fla_amps: float, hp: float = 0.0, phase: int = 1, voltage: int = 0) -> CalcResult:
    """Branch-circuit breaker sizing from full-load amps (125% FLA, rounded up to
    the next standard breaker). Flagged as a business rule, not a source formula."""
    fla_amps = float(fla_amps or 0)
    target = fla_amps * BREAKER_CONTINUOUS_FACTOR
    breaker = next((b for b in _STD_BREAKERS if b >= target), math.ceil(target))
    return CalcResult(
        calc="electrical_load",
        value=breaker,
        unit="A breaker",
        inputs={
            "fla": make_input(fla_amps, "A", "lookup", "pump nameplate"),
            "hp": make_input(hp, "HP", "lookup", "pump nameplate"),
            "phase": make_input(phase, "", "user"),
            "voltage": make_input(voltage, "V", "user"),
        },
        formula="breaker = next standard size >= 1.25 * FLA",
        steps=[
            f"target = 1.25 * {fla_amps} = {target:.2f} A",
            f"breaker = {breaker} A (next standard size, NEC 240.6)",
        ],
        citations=["NEC 430.52 / 240.6 (business rule)"],
        warnings=[
            "Breaker sizing (125% FLA) and VFD-vs-motor-starter choice are not in "
            "the Sapphire source documents; confirm against the pump nameplate and "
            "local code with the engineer."
        ],
    )
