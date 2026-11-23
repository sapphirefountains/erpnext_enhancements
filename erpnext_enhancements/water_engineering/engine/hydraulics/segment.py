"""Head loss along a pipe segment: Darcy-Weisbach friction, fittings, equipment curves.

Port of Digital Rain Curtain ``src/hydro/flow.js`` ``segmentLoss``, ``supplyLoss``,
``suctionLoss``; golden file ``tests/golden/drc/segment.json`` (1e-12).

**Darcy governs.** The loss the solver uses is Darcy-Weisbach with the Churchill
friction factor (laminar through fully rough, with viscosity from the water
temperature), Darby 3-K fittings and the manufacturer equipment curves. The
DOC-0049 ``H - TDH`` figure (Hazen-Williams, C = 130, fixed K) is the workbook's
method: an empirical fit for water near 60 F in turbulent flow and larger pipe,
unreliable in small bores and warm water. :func:`segment_loss` returns it as a
cross-check card beside the Darcy cards; a difference over 10% marks the card
``Diverges`` so ``issues.py`` can raise it (Nikolas, 2026-11-01, decision 8).

What this adds to DRC: equipment curves on a segment (``components``), applied at
the segment's own flow and scaled by the clog factor when they are filters, and
``branches`` generalizing DRC's ``share: 'half'`` to N identical parallel branches.
With no components and one or two branches it is DRC's arithmetic exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ..constants import HW_C_PVC, HW_CONSTANT
from ..envelope import CalcResult, make_input
from ..tdh import segment_loss_results as workbook_segment_cards
from . import _js
from .catalog import STATUS_DEFAULT_BORE, PipeSpec, pipe_spec_si
from .fittings import (
    CIT_COMPONENTS,
    CIT_DARBY,
    FITTING_K_TO_3K,
    _pairs,
    component_loss_ft,
    fit_sum_k_si,
    resolve_fitting,
)
from .units import CIT_DRC, M_PER_FT, MM_PER_IN, G, f_to_c, gpm_to_m3s, m3s_to_gpm
from .water import CIT_CHURCHILL, Water, friction_factor_si, water_si

CIT_DARCY = "Darcy-Weisbach: dp = (f L/D + sum K) rho u^2 / 2"
CIT_CROSSCHECK = "Sapphire decision 2026-11-01: Darcy governs; DOC-0049 Hazen-Williams shown, >10% difference flagged"
DIVERGENCE_LIMIT = 0.10
STATUS_AGREES = "Agrees"
STATUS_DIVERGES = "Diverges"

_WORKBOOK_NAME = {v: k for k, v in FITTING_K_TO_3K.items()}


@dataclass(frozen=True, slots=True)
class Segment:
    """One pipe run in flow order (SI). ``branches`` identical parallel runs share the flow."""

    spec: PipeSpec
    length_m: float = 0.0
    branches: int = 1
    k_extra: float = 0.0
    fittings: tuple = ()  # ordered (key, count): the sum is taken in this order, as DRC's fitSumK
    components: tuple = ()  # (COMPONENT_CURVES name, qty)
    label: str = ""
    line: str = ""  # "suction" | "discharge" | ""
    note: str = ""  # set by from_us when the row named no catalog size and no bore

    def __post_init__(self):
        # N identical parallel branches: fewer than one has no meaning (0 would divide by zero, a
        # negative count would flip the flow's sign), so it reads as one.
        b = int(self.branches) if self.branches == self.branches and self.branches else 1
        object.__setattr__(self, "branches", b if b >= 1 else 1)

    @classmethod
    def from_us(cls, row: dict) -> Segment:
        """A segment from the US-customary row shape the TDH sheet and the design model share:
        ``{label, line, material, nominal_size, id_in, wall_in, length_ft, branches, k_extra,
        fittings: [{key|type, qty}], components: [{key|type, qty}]}``."""
        id_in = row.get("id_in")
        wall_in = row.get("wall_in")
        spec = pipe_spec_si(
            row.get("material") or "SCH40 PVC",
            row.get("nominal_size"),
            float(id_in) * MM_PER_IN if id_in else 0,
            float(wall_in) * MM_PER_IN if wall_in else 0,
        )
        comps = tuple((c.get("key") or c.get("type"), float(c.get("qty", c.get("count", 1)) or 0)) for c in row.get("components") or [])
        note = ""
        if spec.defaulted:
            note = (
                f"{row.get('material') or 'SCH40 PVC'} {row.get('nominal_size') or '(no size)'} is not in the catalog "
                "and the row gives no id_in: solved with a 25 mm (0.98 in) default bore. Pick a catalog size or enter the ID."
            )
        return cls(
            spec=spec,
            length_m=float(row.get("length_ft") or 0) * M_PER_FT,
            branches=int(row.get("branches") or 1),
            k_extra=float(row.get("k_extra") or 0),
            fittings=tuple(_pairs(row.get("fittings"))),
            components=comps,
            label=row.get("label") or row.get("segment_label") or "",
            line=row.get("line") or "",
            note=note,
        )


@dataclass(slots=True)
class SegmentLoss:
    """The parts of one segment's loss at one flow (SI)."""

    q: float  # m^3/s through the segment (all branches)
    q_branch: float  # m^3/s through one branch
    u: float  # m/s
    re: float
    f: float
    k_pipe: float  # f L / D
    k_fittings: float
    k_extra: float
    dp_friction: float  # Pa
    dp_fittings: float  # Pa (fittings + k_extra)
    dp_components: float  # Pa
    dp: float  # Pa, total
    warnings: list = field(default_factory=list)


def _components_pa(seg: Segment, q_branch: float, w: Water, clog: float) -> tuple[float, list]:
    """Equipment-curve loss (Pa) at the branch flow, and the curves' warnings."""
    dp, warnings = 0.0, []
    gpm = m3s_to_gpm(abs(q_branch))
    for name, qty in seg.components:
        h_ft, warn = component_loss_ft(name, gpm, clog)
        dp += qty * h_ft * M_PER_FT * w.rho * G
        if warn:
            warnings.append(warn)
    return dp, warnings


def segment_parts_si(seg: Segment, q: float, w: Water, clog: float = 0.0) -> SegmentLoss:
    """``segmentLoss`` with its parts kept, plus equipment curves. ``dp`` is DRC's total
    (computed in DRC's order) plus the components."""
    zero = SegmentLoss(q, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    if not q or q != q:
        return zero
    qs = q / seg.branches if seg.branches != 1 else q
    if not qs:
        return zero
    spec = seg.spec
    d, area = spec.id_m, spec.area
    u = abs(qs) / area
    if not u or u != u:
        return zero
    rho, nu = w.rho, w.nu
    re = (u * d) / nu
    f = friction_factor_si(re, spec.eps_mm / spec.id_mm)
    k_pipe = (f * _js.jmax(0.0, _js.or_(_js.num(seg.length_m), 0.0))) / d
    k_fit = fit_sum_k_si(seg.fittings, re, spec.id_mm, clog)
    k_extra = _js.or_(seg.k_extra, 0.0)
    k = k_pipe + k_fit + k_extra
    dp = (k * rho * u * u) / 2
    dp_comp, warnings = _components_pa(seg, qs, w, clog) if seg.components else (0.0, [])
    if seg.note:
        warnings.append(seg.note)
    return SegmentLoss(
        q=q, q_branch=qs, u=u, re=re, f=f, k_pipe=k_pipe, k_fittings=k_fit, k_extra=k_extra,
        dp_friction=k_pipe * rho * u * u / 2, dp_fittings=(k_fit + k_extra) * rho * u * u / 2,
        dp_components=dp_comp, dp=dp + dp_comp, warnings=warnings,
    )


def segment_loss_si(seg: Segment, q: float, w: Water, clog: float = 0.0) -> float:
    """``segmentLoss``: pressure loss (Pa) across one segment at flow ``q`` (m^3/s).

    The solvers call this on every residual evaluation, so it keeps no parts and allocates
    nothing; ``segment_parts_si`` is the same arithmetic with the breakdown."""
    if not q or q != q:
        return 0.0
    qs = q / seg.branches if seg.branches != 1 else q
    if not qs:
        return 0.0
    spec = seg.spec
    d = spec.id_m
    u = abs(qs) / spec.area
    if not u or u != u:
        return 0.0
    re = (u * d) / w.nu
    f = friction_factor_si(re, spec.eps_mm / spec.id_mm)
    k = (f * _js.jmax(0.0, _js.or_(_js.num(seg.length_m), 0.0))) / d + fit_sum_k_si(seg.fittings, re, spec.id_mm, clog) + _js.or_(seg.k_extra, 0.0)
    dp = (k * w.rho * u * u) / 2
    if seg.components:
        dp += _components_pa(seg, qs, w, clog)[0]
    return dp


def supply_loss_si(segments, q: float, w: Water, clog: float = 0.0) -> float:
    """``supplyLoss``: pump discharge to the terminal, summed in flow order (Pa)."""
    if not q or q != q:
        return 0.0
    total = 0.0
    for seg in segments or ():
        total += segment_loss_si(seg, q, w, clog)
    return total


def suction_loss_si(seg: Segment | None, q: float, w: Water, clog: float = 0.0) -> float:
    """``suctionLoss``: basin to pump inlet (Pa). The suction is always one run (no branches)."""
    if seg is None:
        return 0.0
    return segment_loss_si(replace(seg, branches=1), q, w, clog)


# ---------------------------------------------------------------------------
# Envelopes
# ---------------------------------------------------------------------------


def _ft(dp: float, w: Water) -> float:
    return dp / (w.rho * G) / M_PER_FT


def crosscheck_status(governing: float, workbook: float, limit: float = DIVERGENCE_LIMIT) -> tuple[str, float]:
    """(status, relative difference) of the governing figure against the workbook's."""
    if not workbook:
        return (STATUS_AGREES if not governing else STATUS_DIVERGES), (0.0 if not governing else float("inf"))
    rel = (governing - workbook) / workbook
    return (STATUS_DIVERGES if abs(rel) > limit else STATUS_AGREES), rel


def _workbook_row(row: dict, seg: Segment, flow_gpm: float) -> tuple[dict, list]:
    """The same run in the shape ``tdh.segment_loss_results`` reads, and the fittings it has no K for."""
    fittings, missing = [], []
    for raw, n in seg.fittings:
        key = resolve_fitting(raw)
        name = raw if raw in FITTING_K_TO_3K else _WORKBOOK_NAME.get(key or "")
        if name:
            fittings.append({"type": name, "qty": _js.num(n)})
        else:
            missing.append(str(raw))
    return {
        "label": seg.label,
        "flow_gpm": flow_gpm,
        "id_in": seg.spec.id_mm / MM_PER_IN,
        "length_ft": float(row.get("length_ft") or 0),
        "fittings": fittings,
        "components": [{"type": n, "qty": q} for n, q in seg.components],
    }, missing


def segment_loss(
    segment: dict,
    flow_gpm: float,
    water_temp_f: float = 68.0,
    clog_pct: float = 0.0,
    hw_c: float = HW_C_PVC,
) -> list[CalcResult]:
    """One segment's head loss as audit cards, the shape ``tdh.segment_loss_results`` gives:
    Darcy friction, 3-K fittings, equipment, and the DOC-0049 Hazen-Williams cross-check.

    ``segment`` is the US row ``Segment.from_us`` reads; ``flow_gpm`` is the flow through the
    whole segment (split evenly over ``branches``)."""
    seg = Segment.from_us(segment)
    w = water_si(f_to_c(float(water_temp_f)))
    clog = float(clog_pct or 0) / 100
    q = gpm_to_m3s(float(flow_gpm))
    parts = segment_parts_si(seg, q, w, clog)
    label = seg.label or "segment"
    id_in = seg.spec.id_mm / MM_PER_IN
    v_fps = parts.u / M_PER_FT
    branch_gpm = float(flow_gpm) / seg.branches if seg.branches > 1 else float(flow_gpm)
    common_inputs = {
        "flow": make_input(float(flow_gpm), "GPM", "prior_calc"),
        "id": make_input(round(id_in, 4), "in", "lookup", seg.spec.source),
        "water_temp": make_input(float(water_temp_f), "degF", "user"),
    }
    branch_note = [f"{seg.branches} parallel branches: {branch_gpm:.2f} GPM each"] if seg.branches > 1 else []

    friction_ft = _ft(parts.dp_friction, w)
    cards = [
        CalcResult(
            calc=f"Pipe friction (Darcy) — {label}",
            value=friction_ft,
            unit="ft",
            inputs={
                **common_inputs,
                "length": make_input(float(segment.get("length_ft") or 0), "ft", "user"),
                "roughness": make_input(seg.spec.eps_mm, "mm", "lookup", seg.spec.label),
            },
            formula="h_f = f (L/D) V^2 / 2g ; f = Churchill(Re, eps/D)",
            steps=[
                *branch_note,
                f"V = {v_fps:.3f} ft/s ; Re = {parts.re:,.0f} ; eps/D = {seg.spec.eps_mm / seg.spec.id_mm:.3g}",
                f"f = {parts.f:.5f} ; f L/D = {parts.k_pipe:.3f}",
                f"h_f = {friction_ft:.4f} ft",
            ],
            citations=[CIT_DARCY, CIT_CHURCHILL, CIT_DRC],
            warnings=[seg.note] if seg.note else [],
            status=STATUS_DEFAULT_BORE if seg.spec.defaulted else None,
        )
    ]
    if seg.fittings or seg.k_extra:
        fit_ft = _ft(parts.dp_fittings, w)
        cards.append(
            CalcResult(
                calc=f"Fitting loss (3-K) — {label}",
                value=fit_ft,
                unit="ft",
                inputs={**common_inputs, "fittings": make_input(len(seg.fittings), "rows", "user")},
                formula="h_m = (Σ n K(Re, D) + K_extra) V^2 / 2g",
                steps=[
                    f"Σ K = {parts.k_fittings:.4f} (+ K_extra {parts.k_extra:g}) at Re {parts.re:,.0f}",
                    f"h_m = {fit_ft:.4f} ft",
                ],
                citations=[CIT_DARBY, CIT_DRC],
            )
        )
    if seg.components:
        comp_ft = _ft(parts.dp_components, w)
        cards.append(
            CalcResult(
                calc=f"Component loss — {label}",
                value=comp_ft,
                unit="ft",
                inputs={**common_inputs, "components": make_input(len(seg.components), "rows", "user")},
                formula="h_c = Σ qty * curve(type, Q_branch) ; filters / (1 - clog)^2",
                steps=[f"at {branch_gpm:.2f} GPM: {comp_ft:.4f} ft" + (f" (clog {clog_pct:g}%)" if clog else "")],
                citations=[CIT_COMPONENTS],
                warnings=[x for x in parts.warnings if x != seg.note],
            )
        )

    # Workbook cross-check: the same run through DOC-0049 H - TDH (Hazen-Williams + fixed K).
    wb_row, missing = _workbook_row(segment, seg, branch_gpm)
    wb_cards = workbook_segment_cards(wb_row, c=hw_c, constant=HW_CONSTANT)
    wb_pipe = sum(c.value for c in wb_cards if not c.calc.startswith("Component"))
    # Like for like: the workbook has no K_extra, so it is left out of the Darcy side too.
    darcy_pipe = _ft(parts.dp_friction + parts.k_fittings * w.rho * parts.u * parts.u / 2, w)
    status, rel = crosscheck_status(darcy_pipe, wb_pipe)
    warnings = []
    if status == STATUS_DIVERGES:
        warnings.append(
            f"Darcy friction + fittings {darcy_pipe:.2f} ft vs DOC-0049 Hazen-Williams {wb_pipe:.2f} ft "
            f"({rel:+.0%}): more than {DIVERGENCE_LIMIT:.0%} apart. Darcy governs; check the bore, "
            "the water temperature and the fitting list."
        )
    if missing:
        warnings.append(f"DOC-0049 has no fixed K for {missing}; the workbook figure leaves them out.")
    if parts.k_extra:
        warnings.append(f"K_extra {parts.k_extra:g} has no workbook counterpart; both sides of the comparison leave it out.")
    cards.append(
        CalcResult(
            calc=f"Workbook cross-check (Hazen-Williams) — {label}",
            value=wb_pipe,
            unit="ft",
            inputs={**common_inputs, "hw_c": make_input(hw_c, "", "standard", "DOC-0049 A - Pipe Size")},
            formula="h = 10.44 L Q^1.85 / (C^1.85 D^4.8655) + Σ K_fixed V^2 / 2g (per branch)",
            steps=[f"{c.calc}: {c.value:.4f} ft" for c in wb_cards if not c.calc.startswith("Component")]
            + [f"workbook {wb_pipe:.4f} ft vs Darcy {darcy_pipe:.4f} ft -> {rel:+.1%} ({status})"],
            citations=[CIT_CROSSCHECK, "DOC-0049 / H - TDH"],
            warnings=warnings + ([seg.note] if seg.note else []),
            status=STATUS_DEFAULT_BORE if seg.spec.defaulted else status,
        )
    )
    return cards


def _loss_card(calc: str, segs: list[Segment], rows: list[dict], flow_gpm: float, water_temp_f: float, clog_pct: float) -> CalcResult:
    w = water_si(f_to_c(float(water_temp_f)))
    clog = float(clog_pct or 0) / 100
    q = gpm_to_m3s(float(flow_gpm))
    total, steps, warnings = 0.0, [], []
    for seg in segs:
        p = segment_parts_si(seg, q, w, clog)
        total += p.dp
        warnings += p.warnings
        steps.append(
            f"{seg.label or seg.spec.label} ({seg.spec.nominal or 'custom'}): V {p.u / M_PER_FT:.2f} ft/s, "
            f"Re {p.re:,.0f}, f {p.f:.4f} -> {_ft(p.dp, w):.3f} ft"
        )
    steps.append(f"total = {_ft(total, w):.4f} ft")
    return CalcResult(
        calc=calc,
        value=_ft(total, w),
        unit="ft",
        inputs={
            "flow": make_input(float(flow_gpm), "GPM", "prior_calc"),
            "segments": make_input(len(rows), "rows", "user"),
            "water_temp": make_input(float(water_temp_f), "degF", "user"),
            "clog": make_input(float(clog_pct or 0), "%", "user"),
        },
        formula="h = Σ segments [(f L/D + Σ K) V^2/2g + equipment]",
        steps=steps,
        citations=[CIT_DARCY, CIT_CHURCHILL, CIT_DARBY, CIT_DRC],
        warnings=warnings,
        status=STATUS_DEFAULT_BORE if any(s.spec.defaulted for s in segs) else None,
    )


def supply_loss(segments: list[dict], flow_gpm: float, water_temp_f: float = 68.0, clog_pct: float = 0.0) -> CalcResult:
    """Head loss (ft) from the pump discharge to the terminal, segments in flow order."""
    rows = list(segments or [])
    return _loss_card("supply_loss", [Segment.from_us(r) for r in rows], rows, flow_gpm, water_temp_f, clog_pct)


def suction_loss(segment: dict, flow_gpm: float, water_temp_f: float = 68.0, clog_pct: float = 0.0) -> CalcResult:
    """Head loss (ft) from the basin to the pump inlet (always a single run)."""
    seg = replace(Segment.from_us(segment), branches=1)
    return _loss_card("suction_loss", [seg], [segment], flow_gpm, water_temp_f, clog_pct)
