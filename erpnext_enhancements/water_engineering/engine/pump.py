"""Pump selection (catalog lookup) and electrical/breaker sizing.

The Sapphire workbooks size a pump by matching the required flow + TDH against a
catalog and transcribing the nameplate — there is NO pump-curve formula, breaker
rule, or VFD-vs-starter logic in the sheets. So:

* :func:`select_pump` is a catalog lookup. Candidates come from the caller
  (ERPNext Items, ``item_group`` "Pumps"); the engine stays pure and never
  queries the DB.
* :func:`electrical_load` sizes the pump's branch breaker. Since v1.759.0 it is
  the NEC 430.52 / Table 430.52(C)(1) maximum (250% of FLC for an inverse-time
  breaker, next standard size up), computed by ``electrical.ocpd``; 125% of FLA,
  which this function used to call the breaker, is the 430.22 *conductor*
  minimum and is reported beside it. A non-motor load (``is_motor=False``) gets
  the 210.20(A) continuous-load breaker at 125%.
"""

from __future__ import annotations

from itertools import pairwise

from .constants import BREAKER_CONTINUOUS_FACTOR
from .electrical.ocpd import continuous_ocpd, motor_branch_ocpd
from .envelope import CalcOption, CalcResult, make_input


def head_at_flow(curve, flow_gpm):
    """Linear-interpolate a pump performance curve's head (ft) at a flow (GPM).

    ``curve`` is a list of {flow_gpm, head_ft} points. Returns None if the curve
    is empty or the flow exceeds the curve's max flow (the pump physically can't
    deliver that much water). Below the lowest point, the lowest point's head is
    used (curves typically start at shutoff = max head, ~0 flow)."""
    pts = sorted(
        ((float(p.get("flow_gpm") or 0), float(p.get("head_ft") or 0)) for p in (curve or [])),
        key=lambda x: x[0],
    )
    if not pts:
        return None
    f = float(flow_gpm)
    if f > pts[-1][0]:
        return None
    if f <= pts[0][0]:
        return pts[0][1]
    for (f0, h0), (f1, h1) in pairwise(pts):
        if f0 <= f <= f1:
            return h0 + (h1 - h0) * ((f - f0) / (f1 - f0)) if f1 > f0 else h0
    return pts[-1][1]


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
        # Best evidence wins: a performance CURVE gives the real duty-point check
        # (interpolate head at the design flow). Otherwise fall back to the
        # rated max-flow / max-head envelope; an unknown head doesn't exclude a
        # pump (fountain submersibles are spec'd by GPH) — it's flagged instead.
        if c.get("curve"):
            h = head_at_flow(c["curve"], flow_gpm)
            return h is not None and h >= tdh_ft
        head = c.get("rated_tdh_ft") or 0
        return (c.get("rated_gpm") or 0) >= flow_gpm and (head >= tdh_ft if head else True)

    def head_basis(c):
        if c.get("curve") and head_at_flow(c["curve"], flow_gpm) is not None:
            return "curve"
        return "rating" if c.get("rated_tdh_ft") else "flow-only"

    ranked = sorted(
        candidates,
        # adequate first; then prefer curve > head-rating > flow-only; then smallest.
        key=lambda c: (
            not adequate(c),
            {"curve": 0, "rating": 1, "flow-only": 2}[head_basis(c)],
            (c.get("rated_gpm") or 0),
        ),
    )
    options: list[CalcOption] = []
    recommended = None
    for c in ranked:
        ok = adequate(c)
        if recommended is None and ok:
            recommended = c.get("item_code") or c.get("part_number")
        duty_head = head_at_flow(c["curve"], flow_gpm) if c.get("curve") else None
        options.append(
            CalcOption(
                key=str(c.get("item_code") or c.get("part_number") or c.get("label") or "?"),
                label=str(c.get("description") or c.get("item_code") or c.get("part_number") or "pump"),
                value=c.get("item_code") or c.get("part_number"),
                recommended=(recommended is not None and recommended == (c.get("item_code") or c.get("part_number"))),
                detail={
                    "rated_gpm": c.get("rated_gpm"),
                    "rated_tdh_ft": c.get("rated_tdh_ft"),
                    "head_at_duty_ft": round(duty_head, 2) if duty_head is not None else None,
                    "head_basis": head_basis(c),
                    "meets_duty": ok,
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
        if rec_opt and rec_opt.detail.get("head_basis") == "flow-only":
            warnings.append(
                f"{recommended}: matched on flow only — no head rating or curve on file. "
                f"Confirm it delivers {tdh_ft:.1f} ft TDH against the manufacturer pump curve."
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


def electrical_load(
    fla_amps: float,
    hp: float = 0.0,
    phase: int = 1,
    voltage: int = 0,
    *,
    is_motor: bool = True,
    device: str = "inverse_time_breaker",
) -> CalcResult:
    """Branch-circuit breaker for a pump (or, with ``is_motor=False``, any other
    load) from its full-load amps.

    Motor: the NEC 430.52(C)(1) maximum for ``device`` (inverse-time breaker by
    default: 250% of FLA, next standard size up per Exception 1), from
    ``electrical.ocpd.motor_branch_ocpd``. ``inputs['conductor_min_a']`` is the
    430.22 conductor figure, 125% of FLA. Non-motor: noncontinuous + 125% of the
    continuous load, next standard size (210.20(A)). Same ``CalcResult`` shape
    as before v1.759.0; only the motor ``value`` changed (it was the 125% figure).
    """
    fla_amps = float(fla_amps or 0)
    conductor_a = fla_amps * BREAKER_CONTINUOUS_FACTOR
    if is_motor:
        oc = motor_branch_ocpd(fla_amps, device=device, phase=phase or 1, hp=hp, voltage=voltage)
        formula = "breaker = Table 430.52(C)(1) % x FLA, next standard size up ; conductors >= 1.25 x FLA (430.22)"
    else:
        oc = continuous_ocpd(fla_amps, continuous=True)
        formula = "breaker = next standard size >= 1.25 x FLA (210.20(A), continuous load)"
    breaker = oc.value if oc.value is not None else 0
    steps = list(oc.steps)
    if is_motor:
        steps.append(f"conductor minimum = 1.25 * {fla_amps} = {conductor_a:.2f} A (NEC 430.22)")
    return CalcResult(
        calc="electrical_load",
        value=breaker,
        unit="A breaker",
        inputs={
            "fla": make_input(fla_amps, "A", "lookup", "pump nameplate"),
            "hp": make_input(hp, "HP", "lookup", "pump nameplate"),
            "phase": make_input(phase, "", "user"),
            "voltage": make_input(voltage, "V", "user"),
            "is_motor": make_input(bool(is_motor), "", "user"),
            "device": make_input(device, "", "user"),
            "conductor_min_a": make_input(round(conductor_a, 2), "A", "prior_calc", "NEC 430.22 / 210.19(A)"),
        },
        formula=formula,
        steps=steps,
        citations=oc.citations,
        warnings=[
            *oc.warnings,
            "VFD-vs-motor-starter choice is not in the Sapphire source documents; confirm against the pump "
            "nameplate and local code with the engineer.",
        ],
    )
