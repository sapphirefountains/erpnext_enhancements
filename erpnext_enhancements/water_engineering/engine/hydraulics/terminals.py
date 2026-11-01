"""Terminals: what the water does at the end of the supply line, as a demand curve.

The contract comes from Digital Rain Curtain ``src/hydro/manifold.js`` ``demandModel``:
an object with ``q(p)`` (flow at inlet gauge pressure ``p``), ``p_for(q)`` (its
inverse), ``any_open`` and ``pg`` (the hydrostatic offset: no flow below
``p = -pg``). ``operating_point`` only ever talks to that contract, so any terminal
that honours it can be solved. :class:`OrificeDemand` is DRC's case and matches
``demandModel`` at 1e-12 (``tests/golden/drc/terminals.json``); the others are new
and carry hand goldens.

* :class:`OrificeDemand`: ``Q = G sqrt(p + pg)``. A nozzle (or N identical nozzles)
  from a Nozzle Profile's Cd and orifice area, or from a rated GPM at a rated head.
* :class:`OpenDischargeDemand`: a weir trough, spillway or open pipe fed until it
  overflows. It takes any flow at one pressure (the static lift to the overflow):
  infinite conductance. ``p_fixed`` tells ``operating_point`` to solve for the flow
  the pump delivers at that pressure instead of bisecting on the pressure.
  ``q_design`` is the flow the feature wants (``feature.weir_flow`` or the GPM/ft
  rule), which the operating point is compared against.
* :class:`FixedFlowDemand`: a flow-control valve or a fixed-rate consumer: ``q`` at
  any pressure above ``p_min``. If the pump cannot hold ``p_min`` at that flow the
  operating point reports it starved.
* :class:`ParallelDemand`: several terminals on one node; ``q(p)`` is the sum in
  insertion order. One pump feeding N parallel terminals is the case every current
  Sapphire layout reduces to (audit 3.1); phase B's network solver must reproduce
  it to 1e-9.

Manifold terminals (DRC ``manifoldSolve``, per-takeoff) are phase B.
"""

from __future__ import annotations

import math
from typing import Protocol, runtime_checkable

from ..envelope import CalcResult, make_input
from ..feature import weir_flow
from . import _js
from .units import CIT_DRC, M_PER_FT, MM_PER_IN, PA_PER_PSI, G, f_to_c, gpm_to_m3s, m3s_to_gpm
from .water import water_si

CIT_ORIFICE = "Orifice equation Q = Cd A sqrt(2 dp / rho) (Idelchik; ASME MFC-3M)"
CIT_TERMINAL = "Sapphire decision 2026-11-01: open discharge = constant static lift, infinite conductance"


@runtime_checkable
class Demand(Protocol):
    """The terminal contract (SI): ``q(p)`` m^3/s at inlet gauge pressure ``p`` Pa, and back."""

    any_open: bool
    pg: float
    p_fixed: float | None

    def q(self, p: float) -> float: ...

    def p_for(self, q: float) -> float: ...


class OrificeDemand:
    """``demandModel``: ``Q = G sqrt(p + pg)`` for ``p + pg > 0``; ``p_for(Q) = (Q/G)^2 - pg``."""

    p_fixed = None

    def __init__(self, conductance: float, pg: float = 0.0, any_open: bool = True, label: str = ""):
        self.G = float(conductance)
        self.pg = float(pg)
        self.any_open = bool(any_open)
        self.label = label

    def q(self, p: float) -> float:
        return self.G * math.sqrt(p + self.pg) if self.any_open and p + self.pg > 0 else 0.0

    def p_for(self, q: float) -> float:
        if q <= 0:
            return -self.pg
        return _js.jpow(q / self.G, 2) - self.pg if self.any_open else math.inf

    @classmethod
    def from_cd_area(cls, cd: float, area_m2: float, rho: float, count: int = 1, pg: float = 0.0, label: str = "") -> OrificeDemand:
        """N orifices of discharge coefficient ``cd`` and area ``area_m2``: G = N Cd A sqrt(2/rho)."""
        return cls(count * cd * area_m2 * math.sqrt(2 / rho), pg, count > 0 and cd > 0 and area_m2 > 0, label)

    @classmethod
    def from_rated(cls, q_rated: float, p_rated: float, count: int = 1, pg: float = 0.0, label: str = "") -> OrificeDemand:
        """N nozzles each passing ``q_rated`` at ``p_rated``: G = N q_rated / sqrt(p_rated)."""
        ok = count > 0 and q_rated > 0 and p_rated > 0
        return cls(count * q_rated / math.sqrt(p_rated) if ok else 0.0, pg, ok, label)


class OpenDischargeDemand:
    """An open (unpressurized) terminal: any flow at the static lift ``lift_m`` above the inlet node.

    ``p_for(Q) = rho g lift`` for every Q, and ``q(p)`` is 0 up to that pressure and unbounded above
    it, so ``operating_point`` solves this terminal on flow (``p_fixed``) rather than pressure."""

    any_open = True

    def __init__(self, lift_m: float, rho: float, q_design: float | None = None, label: str = "", basis: str = ""):
        self.lift_m = float(lift_m)
        self.p_fixed = rho * G * self.lift_m
        self.pg = -self.p_fixed
        self.q_design = q_design
        self.label = label
        self.basis = basis

    def q(self, p: float) -> float:
        return 0.0 if p <= self.p_fixed else math.inf

    def p_for(self, q: float) -> float:
        return self.p_fixed

    @classmethod
    def from_weir(cls, length_ft: float, head_in: float, lift_ft: float, rho: float, contractions: int = 2, label: str = ""):
        """Design flow from the Francis weir formula (``feature.weir_flow``) at the chosen crest head."""
        r = weir_flow(length_ft, head_in, contractions)
        return cls(lift_ft * M_PER_FT, rho, gpm_to_m3s(r.value), label, f"weir_flow {length_ft:g} ft @ {head_in:g} in = {r.value:.1f} GPM")

    @classmethod
    def from_gpm_per_ft(cls, length_ft: float, gpm_per_ft: float, lift_ft: float, rho: float, label: str = ""):
        """Design flow from a sheet rate per foot of edge (DOC-0049 B: operate ~0.5, engineer 4-6 GPM/ft)."""
        q = float(length_ft) * float(gpm_per_ft)
        return cls(lift_ft * M_PER_FT, rho, gpm_to_m3s(q), label, f"{length_ft:g} ft x {gpm_per_ft:g} GPM/ft = {q:.1f} GPM")


class FixedFlowDemand:
    """A fixed flow ``q_fixed`` at any inlet pressure of at least ``p_min``."""

    p_fixed = None

    def __init__(self, q_fixed: float, p_min: float = 0.0, label: str = ""):
        self.q_fixed = float(q_fixed)
        self.p_min = float(p_min)
        self.p_floor = self.p_min  # operating_point re-solves at this pressure when the pump cannot hold it
        self.pg = -self.p_min
        self.any_open = self.q_fixed > 0
        self.label = label

    def q(self, p: float) -> float:
        return self.q_fixed if self.any_open and p >= self.p_min else 0.0

    def p_for(self, q: float) -> float:
        if q <= 0:
            return -self.pg
        return self.p_min if q <= self.q_fixed else math.inf


class ParallelDemand:
    """Terminals in parallel on one node: ``q(p) = Σ q_i(p)`` (insertion order).

    If a member is an open discharge the node pressure cannot rise past its overflow, so
    ``p_fixed`` is the lowest member ``p_fixed``; the open member takes whatever the pump
    delivers beyond what the others draw at that pressure (``split``)."""

    def __init__(self, members, label: str = ""):
        self.members = list(members)
        self.label = label
        opened = [m for m in self.members if m.any_open]
        self.any_open = bool(opened)
        self.pg = max((m.pg for m in opened), default=0.0)
        fixed = [m.p_fixed for m in self.members if getattr(m, "p_fixed", None) is not None]
        self.p_fixed = min(fixed) if fixed else None

    def q(self, p: float) -> float:
        total = 0.0
        for m in self.members:
            total += m.q(p)
        return total

    def p_for(self, q: float) -> float:
        if q <= 0:
            return -self.pg
        if not self.any_open:
            return math.inf
        if self.p_fixed is not None and self.q(self.p_fixed) <= q:
            return self.p_fixed
        lo = -self.pg
        hi = self.p_fixed if self.p_fixed is not None else max(1.0, lo + 1.0)
        for _ in range(200):
            if self.q(hi) >= q:
                break
            hi = lo + 2 * (hi - lo)
        else:
            return math.inf
        for _ in range(200):
            if hi - lo <= 1e-12 * max(1.0, abs(hi)):
                break
            mid = 0.5 * (lo + hi)
            if self.q(mid) < q:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    def split(self, p: float, q_total: float | None = None) -> list[float]:
        """Each member's flow at node pressure ``p``. At an open member's overflow pressure the first
        such member takes the remainder of ``q_total``; any others at the same pressure take none."""
        flows = []
        remainder_to = None
        for i, m in enumerate(self.members):
            if getattr(m, "p_fixed", None) is not None and self.p_fixed is not None and m.p_fixed == self.p_fixed and p >= m.p_fixed:
                flows.append(0.0)
                if remainder_to is None:
                    remainder_to = i
            else:
                flows.append(m.q(p))
        if remainder_to is not None and q_total is not None:
            flows[remainder_to] = max(0.0, q_total - sum(flows))
        return flows


# ---------------------------------------------------------------------------
# Building a demand from a design-model terminal row, and the envelope
# ---------------------------------------------------------------------------


def build_demand(terminal: dict, rho: float):
    """A demand from a model-2 terminal ``{kind, params, count}`` (US units in ``params``).

    * ``orifice``: ``{cd, orifice_area_in2 | orifice_diameter_in}`` or ``{rated_gpm, rated_head_ft}``,
      plus ``count`` and ``below_ft`` (nozzle below the inlet node).
    * ``open_discharge``: ``lift_ft`` plus one of ``design_gpm``, ``{weir_length_ft, weir_head_in,
      contractions}`` or ``{length_ft, gpm_per_ft}``.
    * ``fixed_flow``: ``{flow_gpm, min_psi}``.
    * ``parallel``: ``{members: [terminal, ...]}``.
    """
    kind = terminal.get("kind")
    p = terminal.get("params") or {}
    label = terminal.get("id") or terminal.get("label") or ""
    count = int(p.get("count") or terminal.get("count") or 1)
    if kind == "orifice":
        pg = rho * G * float(p.get("below_ft") or 0) * M_PER_FT
        area_in2 = p.get("orifice_area_in2")
        if not area_in2 and p.get("orifice_diameter_in"):
            area_in2 = math.pi / 4 * float(p["orifice_diameter_in"]) ** 2
        if p.get("cd") and area_in2:
            return OrificeDemand.from_cd_area(float(p["cd"]), float(area_in2) * (MM_PER_IN / 1000) ** 2, rho, count, pg, label)
        if p.get("rated_gpm") and p.get("rated_head_ft"):
            p_rated = rho * G * float(p["rated_head_ft"]) * M_PER_FT
            return OrificeDemand.from_rated(gpm_to_m3s(float(p["rated_gpm"])), p_rated, count, pg, label)
        raise ValueError(f"orifice terminal {label!r} needs cd + orifice size, or rated_gpm + rated_head_ft")
    if kind == "open_discharge":
        lift = float(p.get("lift_ft") or 0)
        if p.get("weir_length_ft") and p.get("weir_head_in"):
            return OpenDischargeDemand.from_weir(float(p["weir_length_ft"]), float(p["weir_head_in"]), lift, rho, int(p.get("contractions", 2)), label)
        if p.get("length_ft") and p.get("gpm_per_ft"):
            return OpenDischargeDemand.from_gpm_per_ft(float(p["length_ft"]), float(p["gpm_per_ft"]), lift, rho, label)
        q = gpm_to_m3s(float(p["design_gpm"])) if p.get("design_gpm") else None
        return OpenDischargeDemand(lift * M_PER_FT, rho, q, label, "design_gpm" if q else "")
    if kind == "fixed_flow":
        return FixedFlowDemand(gpm_to_m3s(float(p.get("flow_gpm") or 0)) * count, float(p.get("min_psi") or 0) * PA_PER_PSI, label)
    if kind == "parallel":
        return ParallelDemand([build_demand(m, rho) for m in p.get("members") or terminal.get("members") or []], label)
    if kind == "manifold":
        raise NotImplementedError("manifold terminals arrive with the phase B manifold solver")
    raise ValueError(f"unknown terminal kind {kind!r}")


def terminal_demand(terminal: dict, flow_gpm: float | None = None, water_temp_f: float = 68.0) -> CalcResult:
    """The pressure (psi and ft) a terminal needs at its inlet to pass ``flow_gpm`` (default: its design flow)."""
    w = water_si(f_to_c(float(water_temp_f)))
    d = build_demand(terminal, w.rho)
    q_design = getattr(d, "q_design", None) or getattr(d, "q_fixed", None)
    q = gpm_to_m3s(float(flow_gpm)) if flow_gpm is not None else q_design
    warnings = []
    if q is None:
        warnings.append("No flow given and the terminal has no design flow; reporting its zero-flow pressure.")
        q = 0.0
    p = d.p_for(q)
    finite = math.isfinite(p)
    if not finite:
        warnings.append(f"The terminal cannot pass {m3s_to_gpm(q):.1f} GPM at any pressure.")
    kind = terminal.get("kind")
    formula = {
        "orifice": "p = (Q/G)^2 - pg ; G = N Cd A sqrt(2/rho)",
        "open_discharge": "p = rho g h_lift (any flow; infinite conductance)",
        "fixed_flow": "p = p_min for Q <= Q_fixed",
        "parallel": "Σ q_i(p) = Q (bisection)",
    }.get(kind, "")
    basis = getattr(d, "basis", "")
    return CalcResult(
        calc="terminal_demand",
        value={
            "kind": kind,
            "flow_gpm": m3s_to_gpm(q),
            "design_gpm": m3s_to_gpm(q_design) if q_design else None,
            "pressure_psi": p / PA_PER_PSI if finite else None,
            "head_ft": p / (w.rho * G) / M_PER_FT if finite else None,
        },
        unit="",
        inputs={
            "terminal": make_input(terminal.get("id") or kind, "", "user"),
            "flow": make_input(m3s_to_gpm(q), "GPM", "prior_calc" if flow_gpm is None else "user"),
            "water_temp": make_input(float(water_temp_f), "degF", "user"),
        },
        formula=formula,
        steps=([basis] if basis else []) + [
            f"p({m3s_to_gpm(q):.1f} GPM) = " + (f"{p / PA_PER_PSI:.3f} psi" if finite else "unbounded")
        ],
        citations=[CIT_ORIFICE if kind == "orifice" else CIT_TERMINAL, CIT_DRC],
        warnings=warnings,
    )
