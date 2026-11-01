"""The network solver: several pumps on one header, pumps in parallel or series, ring mains.

Nikolas decided on 2026-11-01 that multi-pump headers and ring mains are in version one
(PLAN.md section 1, decision 7), which the audit had deferred (audit 3.1: "a tree or loop
network is deliberately not in the first cut"). This module is that decision. It has no JS
reference; it is built from phase A's kernels and held to them:

* **One pump feeding N parallel terminals** (every current Sapphire layout) reproduces
  :func:`.operating_point.operating_point_si` to 1e-9, with and without a bypass, VFD or PRV,
  and with open-discharge and starved fixed-flow terminals (``test_hydraulics_network.py``).
* **A two-pump header and a ring main** carry hand goldens: two identical pumps equal one pump
  with the flow axis doubled, a symmetric ring equals its symmetric tree, and every solution
  closes continuity at each node and head around each loop.

The method
==========

The global gradient algorithm of Todini and Pilati (1988), the one EPANET uses: unknown flows
on every link and heads at every free node, Newton on both at once, with the node heads from
the Schur complement (a small symmetric system, solved densely here). Each link has a head
drop ``h(Q)`` (m) from its upstream to its downstream node:

* **segment**: ``sign(Q) dp_segment(|Q|) / rho g``, phase A's Darcy + Darby 3-K + equipment
  curves (``segment_loss_si``), so a pipe can carry flow either way (a ring main does).
* **pump**: ``-H(Q)``, the head gain at its speed (affinity laws, ``pump_head_si``). Past the
  run-out the curve is continued as a resistance ``-k (Q^2 - Q_max^2)``, and backwards through a
  pump with no check valve as ``H0 + k Q^2`` (a spinning pump resists reverse flow harder than
  forward flow; ``k = H0 / Q_max^2``). Both are only there so the solve can find, and report,
  those states: a pump past run-out or with backflow is a blocker.
* **terminal**: a link from the terminal node to the atmosphere at the node's elevation, with
  ``h(Q) = demand.p_for(Q) / rho g``: phase A's ``Demand`` contract, so orifices, manifolds,
  open discharges and fixed-flow terminals all fit. A ``ParallelDemand`` is split into its
  members, each its own link.
* **bypass**: an orifice (Cd 0.61) from the pump discharge back to its basin, as DRC.
* **PRV**: a fixed head drop just downstream of the pump, adjusted until the control node holds
  its set pressure.

Links that cannot run backwards (a pump with a check valve, every terminal, the bypass, a PRV)
carry a status, opened and closed between Newton steps as EPANET does. A fixed-flow terminal is
*capped* (a fixed nodal demand) while its node holds the minimum pressure and *starved* (pinned
at the minimum pressure, taking what arrives) when it cannot. Zero-slope links (an open
discharge, a PRV, a zero-length pipe) get a floor on ``dh/dQ``: the residual stays exact, so the
answer is exact, only the Newton direction is approximate.

Controls (VFD, PRV) are outer loops around the solve with DRC's semantics and iteration caps
(:mod:`.operating_point`): a VFD bisects its speed (50 halvings, 15% floor) until the control
node holds the set pressure, a PRV bisects its drop (60 halvings). Several controlled pumps are
adjusted one after another until none moves (Gauss-Seidel).

**One deliberate difference from DRC's single-pump model.** DRC leaves the suction line out of
the head balance (it uses it only for NPSHa). Here the suction is a segment like any other, so
its loss is paid by the pump. A pump with no suction segment draws straight from its ``source``
basin, which is DRC's case exactly and the one the 1e-9 comparison uses.

The ``network`` dict (design model schema 2, PLAN.md 3.3; US units)
==================================================================

::

    {"nodes": [{"id": "src", "kind": "basin", "elevation_ft": 0},       # water surface
               {"id": "p1", "kind": "pump", "elevation_ft": 0.5},       # pump centerline
               {"id": "j1", "kind": "junction", "elevation_ft": 1},
               {"id": "t1", "kind": "terminal", "elevation_ft": 10}],
     "pumps": [{"id": "p1", "curve": [{"flow_gpm", "head_ft", "efficiency_pct", "npshr_ft"}, ...]
                     | {"points": [...], "shutoff_head_ft", "max_flow_gpm", "motor_eff_pct", "name"},
                "speed_pct": 100, "control": "none|vfd|prv", "setpoint_psi": null,
                "control_node": "t1", "check_valve": true, "bypass_in": 0, "running": true,
                "source": "src"}],                                       # source: no suction segment
     "segments": [{"id": "s1", "from": "src", "to": "p1", "line": "suction", "material": "SCH40 PVC",
                   "nominal_size": "3\\"", "length_ft": 8, "fittings": [{"key": "elbow_90", "qty": 2}],
                   "components": [], "branches": 1}],
     "terminals": [{"id": "t1", "node": "t1", "kind": "orifice|open_discharge|fixed_flow|manifold|parallel",
                    "params": {...}}]}                                   # node defaults to id

A segment ending at a pump feeds its inlet, one starting at a pump leaves its outlet, so pumps
in series are two pumps joined by a segment. ``control_node`` defaults to the only terminal node
when there is one (and it is not an open discharge, whose pressure is pinned), else the pump's
discharge (past its PRV, when it has one).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..constants import NPSH_DEFAULT_MARGIN_FT
from ..envelope import CalcResult, make_input
from ..pipe import STATUS_EXCEEDS, STATUS_INCREASE, STATUS_SETTLING, velocity_status
from . import _js
from ._checks import BLOCKER, INFO, WARNING, make_check
from .operating_point import DEAD_HEAD_FRACTION, VFD_FLOOR, site_atmosphere
from .pump import (
    PumpCurve,
    bep_flow_si,
    max_flow_si,
    npshr_si,
    pump_eff_si,
    pump_head_si,
    shaft_power_si,
)
from .segment import Segment, segment_loss_si, segment_parts_si
from .terminals import FixedFlowDemand, OrificeDemand, ParallelDemand, build_demand
from .units import CIT_DRC, M_PER_FT, MM_PER_IN, PA_PER_PSI, W_PER_HP, G, f_to_c, gpm_to_m3s, m3s_to_gpm
from .water import Water, area_of_si, water_si

CIT_GGA = "Todini, E. & Pilati, S. (1988) 'A gradient algorithm for the analysis of pipe networks' (EPANET 2 method)"
CIT_MULTI_PUMP = "Sapphire decision 2026-11-01: multi-pump headers and ring mains are in version one"

D_MIN = 1e-3  # m per m^3/s: the zero-slope floor when no link has a real slope (residuals stay exact)
MAX_ITER = 200
MAX_FLIPS = 8
TOL_Q = 1e-14
Q_FLOOR = 1e-15  # m^3/s: below this a flow change is noise (an all-closed network converges on it)
CONTROL_SWEEPS = 8

OPEN, CLOSED, CAPPED = "open", "closed", "capped"


# ---------------------------------------------------------------------------
# Internal graph
# ---------------------------------------------------------------------------


@dataclass
class _Node:
    id: str
    kind: str  # basin | junction | terminal | pump_in | pump_out | pump_prv | atm
    z: float  # m
    fixed: bool = False
    H: float = 0.0  # m; the head of a fixed node, the last solved head of a free one
    ref: str = ""  # the model node id it belongs to


@dataclass
class _Pump:
    id: str
    curve: PumpCurve | None
    speed: float
    control: str
    set_pa: float | None
    check: bool
    running: bool
    bypass_d_m: float
    control_node: int | None = None
    i_in: int = -1
    i_out: int = -1
    link: int = -1
    bypass_link: int | None = None
    prv_link: int | None = None
    prv_drop: float = 0.0  # m
    discharge: int = -1  # the node its discharge segments leave from (the PRV's outlet when it has one)
    h0: float = 0.0
    q_max: float = 0.0
    k_r: float = 0.0
    item: str | None = None

    def refresh(self) -> None:
        """Shut-off head, run-out and the reverse/past-run-out resistance at the current speed."""
        if self.curve is None:
            self.h0, self.q_max, self.k_r = 0.0, 0.0, 1e9
            return
        full = _js.jmax(self.curve.default_speed, 1e-3)
        h0_full = pump_head_si(self.curve, 0, full)
        qm_full = max_flow_si(self.curve, full)
        self.k_r = h0_full / (qm_full * qm_full) if qm_full > 0 and h0_full > 0 else 1e9
        s = self.speed if self.running else 0.0
        self.h0 = pump_head_si(self.curve, 0, s) if self.running else 0.0
        self.q_max = max_flow_si(self.curve, s) if self.running else 0.0

    def head(self, q: float) -> float:
        """The network characteristic H(Q) (m)."""
        if not self.running or self.curve is None:
            return -self.k_r * q * abs(q)
        if q < 0:
            return self.h0 + self.k_r * q * q
        if q <= self.q_max:
            return pump_head_si(self.curve, q, self.speed)
        return -self.k_r * (q * q - self.q_max * self.q_max)


@dataclass
class _Link:
    kind: str  # segment | pump | terminal | bypass | prv
    a: int
    b: int
    ref: str
    seg: Segment | None = None
    pump: _Pump | None = None
    demand: object = None
    check: bool = False
    status: str = OPEN
    q: float = 0.0
    q_cap: float | None = None
    p_min: float | None = None
    flips: int = 0
    terminal: str = ""


@dataclass
class Network:
    """A built network ready to solve (SI). Build it with :func:`build_network`."""

    nodes: list
    links: list
    pumps: list
    w: Water
    clog: float
    atm_pa: float
    pv_pa: float
    node_index: dict
    model_nodes: dict
    terminals: dict  # terminal id -> {"node", "kind", "links": [link idx], "design_q", "row"}
    checks: list = field(default_factory=list)


def _resolve_curve(raw) -> PumpCurve | None:
    """A PumpCurve from a PumpCurve, Pump Curve Point rows, or ``{points, shutoff_head_ft, max_flow_gpm,
    motor_eff_pct, name}``."""
    if raw is None:
        return None
    if isinstance(raw, PumpCurve):
        return raw
    if isinstance(raw, dict):
        return PumpCurve.from_us(
            list(raw.get("points") or raw.get("curve") or []),
            motor_eff_pct=float(raw.get("motor_eff_pct") or 80.0),
            shutoff_head_ft=raw.get("shutoff_head_ft"),
            max_flow_gpm=raw.get("max_flow_gpm"),
            name=raw.get("name") or raw.get("item") or "",
        )
    pts = list(raw)
    if not pts:
        return None
    return PumpCurve.from_us(pts)


def _members(demand) -> list:
    """A ParallelDemand split into its leaves; anything else as itself."""
    if isinstance(demand, ParallelDemand):
        out = []
        for m in demand.members:
            out.extend(_members(m))
        return out
    return [demand]


def _design_q(row: dict, demand) -> float | None:
    """The flow a terminal is designed to pass (m^3/s), where the row or the demand says."""
    p = row.get("params") or {}
    if p.get("design_gpm"):
        return gpm_to_m3s(float(p["design_gpm"]))
    total, found = 0.0, False
    for m in _members(demand):
        q = getattr(m, "q_design", None) or getattr(m, "q_fixed", None)
        if q:
            total += q
            found = True
            continue
        man = getattr(m, "manifold", None)
        if man is not None:
            qs = [t.q_design for t in man.takeoffs if t.open and t.q_design]
            if qs:
                total += sum(qs)
                found = True
    if found:
        return total
    if row.get("kind") == "orifice" and p.get("rated_gpm"):
        return gpm_to_m3s(float(p["rated_gpm"]) * int(p.get("count") or row.get("count") or 1))
    return None


def build_network(
    network: dict,
    *,
    water_temp_f: float = 68.0,
    elevation_ft: float = 0.0,
    clog_pct: float = 0.0,
    curves: dict | None = None,
    pump_overrides: dict | None = None,
) -> Network:
    """The internal graph from a model-2 ``network`` dict (see the module docstring).

    ``curves`` maps a pump id to a curve that replaces the model's (``rank_pumps`` uses it);
    ``pump_overrides`` maps a pump id to field overrides (``speed_pct``, ``control``,
    ``setpoint_psi``, ``bypass_in``, ``running``)."""
    w = water_si(f_to_c(float(water_temp_f)))
    atm_pa, pv_pa = site_atmosphere(float(elevation_ft or 0), float(water_temp_f))
    rho = w.rho
    curves = curves or {}
    pump_overrides = pump_overrides or {}
    checks: list = []

    model_nodes = {n["id"]: n for n in network.get("nodes") or []}
    pump_rows = {p["id"]: p for p in network.get("pumps") or []}
    for pid in pump_rows:
        model_nodes.setdefault(pid, {"id": pid, "kind": "pump"})
    seg_rows = list(network.get("segments") or [])
    nodes: list = []
    index: dict = {}

    def add(node: _Node) -> int:
        nodes.append(node)
        return len(nodes) - 1

    def z_of(nid: str) -> float:
        return float(model_nodes.get(nid, {}).get("elevation_ft") or 0) * M_PER_FT

    # Basins and junctions first, then each pump's internal nodes.
    for nid, n in model_nodes.items():
        kind = n.get("kind") or "junction"
        if kind == "pump":
            continue
        z = z_of(nid)
        index[nid] = add(_Node(nid, kind, z, fixed=kind == "basin", H=z, ref=nid))

    basins = [nid for nid, n in model_nodes.items() if n.get("kind") == "basin"]

    def upstream_basin(pid: str) -> str | None:
        """The basin a pump draws from: its ``source``, else the first basin up its suction segments."""
        row = pump_rows[pid]
        if row.get("source"):
            return row["source"]
        seen, frontier = {pid}, [pid]
        while frontier:
            cur = frontier.pop()
            for s in seg_rows:
                if s.get("to") == cur and s.get("from") not in seen:
                    up = s.get("from")
                    if model_nodes.get(up, {}).get("kind") == "basin":
                        return up
                    seen.add(up)
                    frontier.append(up)
        return basins[0] if basins else None

    pumps: list = []
    links: list = []
    pump_by_id: dict = {}
    for pid, row in pump_rows.items():
        row = {**row, **(pump_overrides.get(pid) or {})}
        curve = _resolve_curve(curves.get(pid, row.get("curve")))
        src = upstream_basin(pid)
        z_p = float(model_nodes[pid]["elevation_ft"]) * M_PER_FT if model_nodes[pid].get("elevation_ft") is not None else (
            nodes[index[src]].z if src in index else 0.0)
        control = (row.get("control") or "none").lower()
        set_pa = float(row["setpoint_psi"]) * PA_PER_PSI if row.get("setpoint_psi") else None
        speed = float(row["speed_pct"]) / 100 if row.get("speed_pct") is not None else (curve.default_speed if curve else 1.0)
        pp = _Pump(
            id=pid, curve=curve, speed=_js.jmax(speed, 1e-3), control=control, set_pa=set_pa,
            check=row.get("check_valve", True) is not False, running=row.get("running", True) is not False and curve is not None,
            bypass_d_m=float(row.get("bypass_in") or 0) * MM_PER_IN / 1000, item=row.get("item"),
        )
        if curve is None:
            checks.append(make_check("pump.no_curve", BLOCKER, f"Pump {pid} has no curve", "Pick a pump from the catalog or enter its curve points.", "pump", ref=pid))
        has_inlet_segment = any(s.get("to") == pid for s in seg_rows)
        if has_inlet_segment:
            pp.i_in = add(_Node(f"{pid}:in", "pump_in", z_p, ref=pid))
        elif src in index:
            pp.i_in = index[src]  # draws straight from the basin: DRC's model, no suction loss
        else:
            raise ValueError(f"pump {pid!r} has no suction segment and no basin to draw from")
        pp.i_out = add(_Node(f"{pid}:out", "pump_out", z_p, ref=pid))
        pp.refresh()
        pp.link = len(links)
        links.append(_Link("pump", pp.i_in, pp.i_out, pid, pump=pp, check=pp.check))
        if pp.bypass_d_m > 0 and src in index:
            pp.bypass_link = len(links)
            g_b = 0.61 * area_of_si(pp.bypass_d_m) * math.sqrt(2 / rho)
            links.append(_Link("bypass", pp.i_out, index[src], pid, demand=OrificeDemand(g_b), check=True))
        if control == "prv":
            i_prv = add(_Node(f"{pid}:prv", "pump_prv", z_p, ref=pid))
            pp.prv_link = len(links)
            links.append(_Link("prv", pp.i_out, i_prv, pid, pump=pp, check=True))
            pp.discharge = i_prv
        else:
            pp.discharge = pp.i_out
        pumps.append(pp)
        pump_by_id[pid] = pp

    def end(nid: str, side: str) -> int:
        if nid in pump_by_id:
            pp = pump_by_id[nid]
            return pp.i_in if side == "to" else pp.discharge
        if nid not in index:
            raise ValueError(f"segment endpoint {nid!r} is not a node")
        return index[nid]

    for s in seg_rows:
        seg = Segment.from_us(s)
        if seg.note:
            checks.append(make_check("piping.default_bore", WARNING, f"Segment {s.get('id')}: no catalog size", seg.note, "piping", ref=s.get("id") or ""))
        links.append(_Link("segment", end(s["from"], "from"), end(s["to"], "to"), s.get("id") or f"{s['from']}->{s['to']}", seg=seg))

    terminals: dict = {}
    for t in network.get("terminals") or []:
        tid = t.get("id") or t.get("node")
        nid = t.get("node") or tid
        if nid not in index:
            raise ValueError(f"terminal {tid!r} sits on {nid!r}, which is not a node")
        i = index[nid]
        demand = build_demand(t, rho)
        idxs = []
        for m in _members(demand):
            atm = add(_Node(f"{tid}:atm", "atm", nodes[i].z, fixed=True, H=nodes[i].z, ref=tid))
            lk = _Link("terminal", i, atm, tid, demand=m, check=True, terminal=tid)
            if isinstance(m, FixedFlowDemand):
                lk.q_cap = m.q_fixed
                lk.p_min = m.p_min
            idxs.append(len(links))
            links.append(lk)
        terminals[tid] = {"node": nid, "kind": t.get("kind"), "links": idxs, "design_q": _design_q(t, demand), "row": t,
                          "demand": demand}

    # A pressure control cannot hold an open discharge (its pressure is pinned at the overflow whatever
    # the flow), so a network whose only terminal is one controls at the pump outlet instead.
    term_nodes = sorted({v["node"] for v in terminals.values()})
    pinned_nodes = {v["node"] for v in terminals.values() if any(getattr(m, "p_fixed", None) is not None for m in _members(v["demand"]))}
    for pp in pumps:
        row = {**pump_rows[pp.id], **(pump_overrides.get(pp.id) or {})}
        cn = row.get("control_node")
        if pp.control in ("vfd", "prv"):
            if cn:  # a pump id means its discharge: past the PRV when it has one
                pp.control_node = index.get(cn, pump_by_id[cn].discharge if cn in pump_by_id else None)
            elif len(term_nodes) == 1 and term_nodes[0] not in pinned_nodes:
                pp.control_node = index[term_nodes[0]]
            else:
                pp.control_node = pp.discharge
            if pp.set_pa is None:
                checks.append(make_check("control.no_setpoint", WARNING, f"Pump {pp.id}: {pp.control.upper()} has no set pressure",
                                         "Enter the set pressure, or let control derive it; solved uncontrolled.", "control", ref=pp.id))

    net = Network(nodes=nodes, links=links, pumps=pumps, w=w, clog=float(clog_pct or 0) / 100, atm_pa=atm_pa, pv_pa=pv_pa,
                  node_index=index, model_nodes=model_nodes, terminals=terminals, checks=checks)
    _initial_flows(net)
    return net


def _initial_flows(net: Network) -> None:
    for lk in net.links:
        if lk.kind == "pump":
            pp = lk.pump
            lk.q = 0.5 * bep_flow_si(pp.curve, pp.speed) if pp.curve and pp.running else 0.0
            if lk.q <= 0 and pp.check:
                lk.status = CLOSED
        elif lk.kind == "segment":
            lk.q = 0.5 * lk.seg.spec.area * lk.seg.branches
        elif lk.kind == "terminal":
            lk.q = 1e-4
            if lk.q_cap is not None:
                lk.q = 0.5 * lk.q_cap
        else:
            lk.q = 1e-5


# ---------------------------------------------------------------------------
# The gradient solve
# ---------------------------------------------------------------------------


def _h(net: Network, lk: _Link, q: float) -> float:
    """Head drop (m) from ``lk.a`` to ``lk.b`` at flow ``q``."""
    rg = net.w.rho * G
    if lk.kind == "segment":
        loss = segment_loss_si(lk.seg, abs(q), net.w, net.clog) / rg
        return loss if q >= 0 else -loss
    if lk.kind == "pump":
        return -lk.pump.head(q)
    if lk.kind == "prv":
        return lk.pump.prv_drop
    return lk.demand.p_for(_js.jmax(q, 0.0)) / rg  # terminal, bypass


def _dh(net: Network, lk: _Link, q: float, h: float) -> float:
    """dh/dQ by a one-sided difference away from zero (raw: 0 for a zero-slope link)."""
    if lk.kind == "prv":
        return 0.0
    dq = _js.jmax(1e-12, 1e-7 * abs(q))
    if lk.kind in ("terminal", "bypass"):
        h2 = _h(net, lk, q + dq)
        d = (h2 - h) / dq if math.isfinite(h2) else 1e12
    elif q >= 0:
        d = (_h(net, lk, q + dq) - h) / dq
    else:
        d = (h - _h(net, lk, q - dq)) / dq
    return d if d > 0 else 0.0


def _floor(ds: list) -> tuple[float, float]:
    """(zero-slope threshold, floor) for one Newton step.

    A link is *pinned* when its slope is under 1e-8 of the median slope (an open discharge, a PRV, a
    zero-length pipe, an orifice at no flow). It gets a slope of 1e-3 of the smallest real one: stiff
    enough that the pinned pressure converges by three digits a step, soft enough that the head system
    keeps about thirteen good digits (a fixed tiny floor made it ill-conditioned)."""
    pos = sorted(d for d in ds if d > 0)
    if not pos:
        return 0.0, D_MIN
    med = pos[len(pos) // 2]
    zero = 1e-8 * med
    real = [d for d in pos if d >= zero]
    return zero, _js.jmax(1e-3 * real[0], 1e-12) if real else D_MIN


def _gauss(a: list, b: list) -> list:
    """Dense Gaussian elimination with partial pivoting (the Schur matrix is small and SPD)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if m[piv][col] == 0:
            raise ZeroDivisionError("singular network matrix")
        m[col], m[piv] = m[piv], m[col]
        inv = 1 / m[col][col]
        row_c = m[col]
        for r in range(col + 1, n):
            f = m[r][col] * inv
            if f:
                row_r = m[r]
                for k in range(col, n + 1):
                    row_r[k] -= f * row_c[k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = m[r][n]
        row = m[r]
        for k in range(r + 1, n):
            s -= row[k] * x[k]
        x[r] = s / row[r]
    return x


def _reached(net: Network) -> list:
    """Which nodes connect to a fixed head through open links."""
    adj: dict = {}
    for lk in net.links:
        if lk.status == OPEN:
            adj.setdefault(lk.a, []).append(lk.b)
            adj.setdefault(lk.b, []).append(lk.a)
    seen = [n.fixed for n in net.nodes]
    stack = [i for i, n in enumerate(net.nodes) if n.fixed]
    while stack:
        i = stack.pop()
        for j in adj.get(i, ()):
            if not seen[j]:
                seen[j] = True
                stack.append(j)
    return seen


def _status_changes(net: Network, it: int) -> bool:
    """Open and close the one-way links between Newton steps (EPANET's status checks)."""
    rg = net.w.rho * G
    changed = False
    for lk in net.links:
        if lk.flips >= MAX_FLIPS:
            continue
        new = lk.status
        ha, hb = net.nodes[lk.a].H, net.nodes[lk.b].H
        if lk.status == OPEN:
            if lk.q_cap is not None and lk.q > lk.q_cap:
                new = CAPPED
            elif lk.check and lk.q < 0:
                new = CLOSED
        elif lk.status == CLOSED:
            if not (math.isfinite(ha) and math.isfinite(hb)):
                # One side is cut off from every fixed head, so there is nothing to compare: try the link
                # open (a running pump, a terminal); it closes again next step if it cannot flow.
                if lk.kind != "pump" or (lk.pump.running and lk.pump.h0 > 0):
                    new = OPEN
            elif lk.kind == "pump":
                if lk.pump.running and hb - ha < lk.pump.h0:
                    new = OPEN
            elif lk.kind == "prv":
                if ha > hb:
                    new = OPEN
            elif (ha - hb) * rg > lk.demand.p_for(0.0) + 1e-9:
                new = OPEN
        elif lk.status == CAPPED:
            if math.isfinite(ha) and (ha - hb) * rg < lk.p_min - 1e-9:
                new = OPEN
        if new != lk.status:
            lk.status = new
            lk.flips += 1
            changed = True
            if new == CLOSED:
                lk.q = 0.0
            elif new == CAPPED:
                lk.q = lk.q_cap
            elif lk.kind == "pump":
                lk.q = _js.jmax(1e-6, 0.1 * bep_flow_si(lk.pump.curve, lk.pump.speed)) if lk.pump.curve else 1e-6
            elif lk.q_cap is not None:
                lk.q = 0.999 * lk.q_cap
            else:
                lk.q = 1e-6
    return changed


def _gga(net: Network) -> tuple[bool, int]:
    """Newton on flows and heads at the current statuses and settings; returns (converged, iterations)."""
    nodes, links = net.nodes, net.links
    for lk in links:
        lk.flips = 0
    it = 0
    stable = 0
    prev = math.inf
    while it < MAX_ITER:
        it += 1
        reached = _reached(net)
        free = [i for i, n in enumerate(nodes) if not n.fixed and reached[i]]
        pos = {i: k for k, i in enumerate(free)}
        n = len(free)
        a_mat = [[0.0] * n for _ in range(n)]
        rhs = [0.0] * n
        rows = []
        evals = []
        for lk in links:
            if lk.status == CAPPED:
                if lk.a in pos:
                    rhs[pos[lk.a]] -= lk.q_cap
                continue
            if lk.status != OPEN or not (reached[lk.a] and reached[lk.b]):
                continue
            h = _h(net, lk, lk.q)
            evals.append((lk, h, _dh(net, lk, lk.q, h)))
        zero, floor = _floor([d for _, _, d in evals])
        for lk, h, d in evals:
            pin = d < zero or d <= 0
            c = 1 / (floor if pin or d < floor else d)
            q0 = lk.q - c * h
            rows.append((lk, c, q0, pin))
            ia, ib = pos.get(lk.a), pos.get(lk.b)
            # continuity: (sum c) H_n - sum c H_other = sum_in q0 - sum_out q0 (+ c H_fixed)
            if ia is not None:
                a_mat[ia][ia] += c
                rhs[ia] -= q0
                if ib is not None:
                    a_mat[ia][ib] -= c
                else:
                    rhs[ia] += c * nodes[lk.b].H
            if ib is not None:
                a_mat[ib][ib] += c
                rhs[ib] += q0
                if ia is not None:
                    a_mat[ib][ia] -= c
                else:
                    rhs[ib] += c * nodes[lk.a].H
        heads = _gauss(a_mat, rhs) if n else []
        for k, i in enumerate(free):
            nodes[i].H = heads[k]
        for i, nd in enumerate(nodes):
            if not nd.fixed and not reached[i]:
                nd.H = math.nan
        # A pinned link's flow is c (H_a - H_b) with c = 1/D_MIN, so its last digits are round-off in the
        # heads: it is left out of the convergence test and recovered from continuity at the end.
        dq_sum, q_sum = 0.0, 0.0
        pinned = []
        for lk, c, q0, pin in rows:
            q_new = q0 + c * (nodes[lk.a].H - nodes[lk.b].H)
            if pin:
                pinned.append(lk)
            else:
                dq_sum += abs(q_new - lk.q)
                q_sum += abs(q_new)
            lk.q = q_new
        for lk in links:
            if lk.status == OPEN and not (reached[lk.a] and reached[lk.b]):
                lk.q = 0.0
        changed = _status_changes(net, it)
        if changed:
            stable = 0
            prev = math.inf
            continue
        done = dq_sum <= TOL_Q * q_sum + Q_FLOOR
        if not done and dq_sum <= 1e-11 * q_sum + Q_FLOOR and dq_sum >= 0.5 * prev:
            stable += 1  # the finite-difference Jacobian has reached round-off: as close as doubles allow
            done = stable >= 3
        prev = dq_sum
        if done:
            _polish(net, pinned)
            return True, it
    return False, it


def _polish(net: Network, pinned: list) -> None:
    """Each pinned link's flow from continuity at an end whose other open links are all unpinned."""
    pin_ids = {id(lk) for lk in pinned}
    incident: dict = {}
    for lk in net.links:
        if lk.status in (OPEN, CAPPED):
            incident.setdefault(lk.a, []).append(lk)
            incident.setdefault(lk.b, []).append(lk)
    for _ in range(len(pinned)):
        left = []
        for lk in pinned:
            for node, sign in ((lk.a, 1.0), (lk.b, -1.0)):
                if net.nodes[node].fixed:
                    continue
                others = [o for o in incident.get(node, ()) if o is not lk]
                if any(id(o) in pin_ids for o in others):
                    continue
                bal = 0.0  # inflow minus outflow of the other links, at this node
                for o in others:
                    bal += o.q if o.b == node else -o.q
                lk.q = sign * bal  # leaving node a (sign +): carries the balance; entering b: minus it
                pin_ids.discard(id(lk))
                break
            else:
                left.append(lk)
        if not left or len(left) == len(pinned):
            break
        pinned = left


# ---------------------------------------------------------------------------
# Controls (outer loops) and the public solve
# ---------------------------------------------------------------------------


def _p_at(net: Network, i: int) -> float:
    """Gauge pressure (Pa) at node ``i``; -inf when no pump reaches it (a control then speeds up / opens)."""
    nd = net.nodes[i]
    return net.w.rho * G * (nd.H - nd.z) if math.isfinite(nd.H) else -math.inf


def _solve_once(net: Network) -> tuple[bool, int]:
    for pp in net.pumps:
        pp.refresh()
    return _gga(net)


def solve_network_si(net: Network) -> dict:
    """Solve, with every VFD and PRV holding its set pressure. Returns ``{converged, iterations,
    solves}``; the flows and heads are left on ``net``."""
    total_it, solves = 0, 0
    ok, it = _solve_once(net)
    total_it += it
    solves += 1
    controlled = [pp for pp in net.pumps if pp.control in ("vfd", "prv") and pp.set_pa is not None and pp.running]
    user_speed = {pp.id: pp.speed for pp in controlled}
    for _ in range(CONTROL_SWEEPS if len(controlled) > 1 else 1):
        moved = False
        for pp in controlled:
            set_pa, ci = pp.set_pa, pp.control_node
            if pp.control == "vfd":
                old = pp.speed
                pp.speed = user_speed[pp.id]
                ok, it = _solve_once(net)
                total_it, solves = total_it + it, solves + 1
                if _p_at(net, ci) >= set_pa and net.links[pp.link].status == OPEN:
                    pp.speed = VFD_FLOOR
                    ok, it = _solve_once(net)
                    total_it, solves = total_it + it, solves + 1
                    if not _p_at(net, ci) > set_pa:
                        s_lo, s_hi = VFD_FLOOR, user_speed[pp.id]
                        for _k in range(50):
                            pp.speed = 0.5 * (s_lo + s_hi)
                            ok, it = _solve_once(net)
                            total_it, solves = total_it + it, solves + 1
                            if _p_at(net, ci) < set_pa:
                                s_lo = pp.speed
                            else:
                                s_hi = pp.speed
                        pp.speed = 0.5 * (s_lo + s_hi)
                        ok, it = _solve_once(net)
                        total_it, solves = total_it + it, solves + 1
                moved = moved or abs(pp.speed - old) > 1e-12
            else:
                old = pp.prv_drop
                pp.prv_drop = 0.0
                ok, it = _solve_once(net)
                total_it, solves = total_it + it, solves + 1
                if _p_at(net, ci) > set_pa:
                    lo, hi = 0.0, pp.h0 + 1.0
                    for _k in range(60):
                        if hi - lo < 1e-6 / (net.w.rho * G):
                            break
                        pp.prv_drop = 0.5 * (lo + hi)
                        ok, it = _solve_once(net)
                        total_it, solves = total_it + it, solves + 1
                        if _p_at(net, ci) > set_pa:
                            lo = pp.prv_drop
                        else:
                            hi = pp.prv_drop
                    pp.prv_drop = 0.5 * (lo + hi)
                    ok, it = _solve_once(net)
                    total_it, solves = total_it + it, solves + 1
                moved = moved or abs(pp.prv_drop - old) > 1e-12
        if not moved:
            break
    return {"converged": ok, "iterations": total_it, "solves": solves}


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class NetworkResult:
    """A solved network: US-unit tables for the page and the checks, plus the SI figures solvers reuse."""

    converged: bool
    iterations: int
    solves: int
    nodes: dict
    segments: dict
    pumps: dict
    terminals: dict
    totals: dict
    checks: list
    si: dict = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        return {
            "converged": self.converged, "iterations": self.iterations, "solves": self.solves, "nodes": self.nodes,
            "segments": self.segments, "pumps": self.pumps, "terminals": self.terminals, "totals": self.totals,
            "checks": self.checks,
        }


def _ft(m: float) -> float:
    return m / M_PER_FT if math.isfinite(m) else None


def _report(net: Network, status: dict, npsh_margin_ft: float) -> NetworkResult:
    w = net.w
    rg = w.rho * G
    checks = list(net.checks)
    if not status["converged"]:
        checks.append(make_check("network.not_converged", BLOCKER, "The network solve did not converge",
                                 "Check for a pump that cannot reach a terminal, a closed loop with no pump, or a segment with no size.",
                                 "piping"))

    nodes_out = {}
    for nid, i in net.node_index.items():
        nd = net.nodes[i]
        p = rg * (nd.H - nd.z) if math.isfinite(nd.H) else math.nan
        nodes_out[nid] = {"kind": nd.kind, "elevation_ft": nd.z / M_PER_FT, "head_ft": _ft(nd.H),
                          "pressure_psi": p / PA_PER_PSI if math.isfinite(p) else None}
        if math.isfinite(p) and net.atm_pa + p < net.pv_pa:
            checks.append(make_check("network.vapor_pressure", BLOCKER, f"Node {nid} falls below vapor pressure",
                                     "The line siphons or cavitates there: lower that run, raise the basin, or add a pump closer to it.",
                                     "piping", ref=nid))
    for pp in net.pumps:
        for i in (pp.i_in, pp.i_out):
            nd = net.nodes[i]
            if nd.kind.startswith("pump"):
                nodes_out[nd.id] = {"kind": nd.kind, "elevation_ft": nd.z / M_PER_FT, "head_ft": _ft(nd.H),
                                    "pressure_psi": rg * (nd.H - nd.z) / PA_PER_PSI if math.isfinite(nd.H) else None}

    segments_out = {}
    for lk in net.links:
        if lk.kind != "segment":
            continue
        parts = segment_parts_si(lk.seg, abs(lk.q), w, net.clog)
        spec = lk.seg.spec
        v_fps = parts.u / M_PER_FT
        line = lk.seg.line or ("suction" if net.nodes[lk.b].kind == "pump_in" else "discharge")
        vstatus = None
        if spec.max_discharge_fps is not None:
            vstatus = velocity_status(v_fps, line, spec.max_suction_fps, spec.max_discharge_fps, spec.legal_fps)
            if vstatus in (STATUS_INCREASE, STATUS_EXCEEDS):
                limit = spec.max_suction_fps if line == "suction" else spec.max_discharge_fps
                sev = BLOCKER if vstatus == STATUS_EXCEEDS else WARNING
                checks.append(make_check(
                    "piping.velocity", sev, f"Segment {lk.ref}: {v_fps:.1f} ft/s at the operating flow",
                    f"Over the DOC-0049 {line} limit of {limit:g} ft/s ({vstatus}): upsize the run or split the flow.",
                    "piping", ref=lk.ref, value={"velocity_fps": v_fps, "limit_fps": limit}))
            elif vstatus == STATUS_SETTLING and abs(lk.q) > 0:
                checks.append(make_check("piping.settling", INFO, f"Segment {lk.ref}: {v_fps:.2f} ft/s is under the self-cleaning velocity",
                                         "Solids can settle: plan for flushing, or use a smaller run.", "piping", ref=lk.ref))
        segments_out[lk.ref] = {
            "from": net.nodes[lk.a].ref or net.nodes[lk.a].id, "to": net.nodes[lk.b].ref or net.nodes[lk.b].id,
            "line": line, "material": spec.material, "nominal_size": spec.nominal, "id_in": spec.id_mm / MM_PER_IN,
            "flow_gpm": m3s_to_gpm(lk.q), "velocity_fps": v_fps, "reynolds": parts.re, "friction_factor": parts.f,
            "loss_ft": parts.dp / rg / M_PER_FT, "loss_psi": parts.dp / PA_PER_PSI, "velocity_status": vstatus,
            "branches": lk.seg.branches,
        }

    pumps_out = {}
    pump_total = 0.0
    for pp in net.pumps:
        lk = net.links[pp.link]
        q = lk.q if lk.status == OPEN else 0.0
        pump_total += q
        nin, nout = net.nodes[pp.i_in], net.nodes[pp.i_out]
        head = (nout.H - nin.H) if math.isfinite(nout.H) and math.isfinite(nin.H) else math.nan
        if pp.check and lk.status == CLOSED and pp.running:
            head = pp.h0  # held shut by its check valve, the pump churns at its shut-off head
        curve = pp.curve
        qb = net.links[pp.bypass_link].q if pp.bypass_link is not None and net.links[pp.bypass_link].status == OPEN else 0.0
        if curve is None:
            pumps_out[pp.id] = {"flow_gpm": 0.0, "status": "No Curve", "item": pp.item}
            continue
        s = pp.speed
        bep = bep_flow_si(curve, s)
        z_p = nout.z  # the pump centerline (its inlet may be the basin itself)
        p_in_gauge = rg * (nin.H - z_p) if math.isfinite(nin.H) else math.nan
        npsha = (net.atm_pa - net.pv_pa) / rg + (nin.H - z_p) if math.isfinite(nin.H) else math.nan
        q_eval = _js.jmax(q, 0.0)
        npshr = npshr_si(curve, q_eval, s)
        eff = pump_eff_si(curve, q_eval, s)
        shaft = shaft_power_si(curve, q_eval, s, w) if pp.running else 0.0
        motor = 80 if curve.motor_eff_pct is None else curve.motor_eff_pct
        elec = shaft / _js.jmax(motor / 100, 0.1)
        last_pt = curve.head_points[-1][0] / 60000 * s if len(curve.head_points) >= 2 else pp.q_max
        check_closed = pp.check and lk.status == CLOSED and pp.running
        backflow = (not pp.check) and lk.q < -1e-9 * _js.jmax(bep, 1e-6)
        dead_head = pp.running and q < DEAD_HEAD_FRACTION * bep
        runout = pp.running and q > pp.q_max * (1 + 1e-9) and pp.q_max > 0
        past_curve = pp.running and q > last_pt * (1 + 1e-9)
        margin_ft = (npsha - npshr) / M_PER_FT if math.isfinite(npsha) else math.nan
        if backflow:
            st = "Backflow"
            checks.append(make_check("pump.backflow", BLOCKER, f"Pump {pp.id} runs backwards ({m3s_to_gpm(-lk.q):.1f} GPM)",
                                     "A stronger pump on the header overpowers it: add a check valve on its discharge, or match the pumps.",
                                     "pump", ref=pp.id, value={"flow_gpm": m3s_to_gpm(lk.q)}))
        elif check_closed:
            st = "Dead Head"
            checks.append(make_check("pump.dead_head", BLOCKER, f"Pump {pp.id} is dead-headed: its check valve stays shut",
                                     "Its shut-off head cannot beat the header pressure: pick a pump with more head, or match it to the others.",
                                     "pump", ref=pp.id))
        elif runout:
            st = "Runout"
            checks.append(make_check("pump.runout", BLOCKER, f"Pump {pp.id} runs out past its curve ({m3s_to_gpm(q):.1f} GPM)",
                                     "Too little system resistance for this pump: throttle it, add a PRV or VFD, or pick a smaller pump.",
                                     "pump", ref=pp.id, value={"flow_gpm": m3s_to_gpm(q), "runout_gpm": m3s_to_gpm(pp.q_max)}))
        elif dead_head:
            st = "Dead Head"
            checks.append(make_check("pump.dead_head", BLOCKER, f"Pump {pp.id} runs under 10% of its best-efficiency flow",
                                     "It will overheat: add a minimum-flow bypass, or pick a smaller pump.", "pump", ref=pp.id))
        elif past_curve:
            st = "Past Curve"
            checks.append(make_check("pump.past_curve", WARNING, f"Pump {pp.id} runs beyond the last published curve point",
                                     "The head there is extrapolated: confirm with the manufacturer's full curve, or pick another pump.",
                                     "pump", ref=pp.id))
        elif not curve.npshr_points:
            st = "Okay"
        elif margin_ft >= npsh_margin_ft:
            st = "Okay"
        elif margin_ft >= 0:
            st = "Marginal"
            checks.append(make_check("pump.npsh_margin", WARNING, f"Pump {pp.id}: NPSH margin {margin_ft:.1f} ft",
                                     f"Under the {npsh_margin_ft:g} ft margin: lower the pump, shorten or upsize the suction.", "pump", ref=pp.id))
        else:
            st = "Cavitation Risk"
            checks.append(make_check("pump.cavitation", BLOCKER, f"Pump {pp.id} will cavitate (NPSHa {npsha / M_PER_FT:.1f} ft < NPSHr {npshr / M_PER_FT:.1f} ft)",
                                     "Lower the pump below the basin, upsize the suction, or pick a pump with a lower NPSHr.", "pump", ref=pp.id))
        pumps_out[pp.id] = {
            "item": pp.item, "flow_gpm": m3s_to_gpm(q), "head_ft": _ft(head), "speed_pct": s * 100, "control": pp.control,
            "efficiency_pct": eff * 100, "shaft_hp": shaft / W_PER_HP, "electrical_kw": elec / 1000,
            "npsha_ft": _ft(npsha), "npshr_ft": npshr / M_PER_FT, "npsh_margin_ft": margin_ft if math.isfinite(margin_ft) else None,
            "bep_gpm": m3s_to_gpm(bep), "bep_ratio": q / bep if bep > 0 else None,
            "shutoff_head_ft": pp.h0 / M_PER_FT, "runout_gpm": m3s_to_gpm(pp.q_max),
            "inlet_psi": p_in_gauge / PA_PER_PSI if math.isfinite(p_in_gauge) else None,
            "discharge_psi": rg * (nout.H - nout.z) / PA_PER_PSI if math.isfinite(nout.H) else None,
            "bypass_gpm": m3s_to_gpm(qb), "prv_drop_psi": rg * pp.prv_drop / PA_PER_PSI,
            "status": st, "check_closed": check_closed, "backflow": backflow, "dead_head": dead_head or check_closed,
            "runout": runout, "past_curve": past_curve,
        }

    terms_out = {}
    term_total = 0.0
    for tid, t in net.terminals.items():
        q = 0.0
        states = []
        for li in t["links"]:
            lk = net.links[li]
            q += lk.q if lk.status != CLOSED else 0.0
            states.append("starved" if lk.q_cap is not None and lk.status == OPEN else lk.status)
        term_total += q
        i = net.node_index[t["node"]]
        nd = net.nodes[i]
        p = rg * (nd.H - nd.z) if math.isfinite(nd.H) else math.nan
        state = "starved" if "starved" in states else ("closed" if all(s == CLOSED for s in states) else "open")
        dq = t["design_q"]
        terms_out[tid] = {"node": t["node"], "kind": t["kind"], "flow_gpm": m3s_to_gpm(q),
                          "pressure_psi": p / PA_PER_PSI if math.isfinite(p) else None,
                          "design_gpm": m3s_to_gpm(dq) if dq else None, "state": state}
        if state == "starved":
            checks.append(make_check("terminal.starved", BLOCKER, f"Terminal {tid} is starved",
                                     "The pump cannot hold its minimum pressure at its fixed flow: more head, bigger pipe, or less flow.",
                                     "pump", ref=tid))
        elif state == "closed":
            checks.append(make_check("terminal.no_flow", BLOCKER, f"Terminal {tid} gets no flow",
                                     "No pump reaches it with enough head: check the route and the pump.", "pump", ref=tid))
        elif dq and q < 0.95 * dq:
            checks.append(make_check("terminal.short", WARNING, f"Terminal {tid} gets {m3s_to_gpm(q):.1f} of {m3s_to_gpm(dq):.1f} GPM",
                                     "Short of its design flow: more pump head or less loss on the way.", "pump", ref=tid,
                                     value={"flow_gpm": m3s_to_gpm(q), "design_gpm": m3s_to_gpm(dq)}))

    # Continuity at every free node, as a check on the solve itself.
    worst = 0.0
    net_flow = [0.0] * len(net.nodes)
    for lk in net.links:
        q = lk.q if lk.status in (OPEN, CAPPED) else 0.0
        net_flow[lk.a] -= q
        net_flow[lk.b] += q
    for i, nd in enumerate(net.nodes):
        if not nd.fixed:
            worst = max(worst, abs(net_flow[i]))
    totals = {"terminal_flow_gpm": m3s_to_gpm(term_total), "pump_flow_gpm": m3s_to_gpm(pump_total),
              "continuity_gpm": m3s_to_gpm(worst)}
    si = {"links": [(lk.kind, lk.ref, lk.q, lk.status) for lk in net.links], "heads": {n.id: n.H for n in net.nodes},
          "pump_q": {pp.id: (net.links[pp.link].q if net.links[pp.link].status == OPEN else 0.0) for pp in net.pumps},
          "terminal_q": {tid: gpm_to_m3s(v["flow_gpm"]) for tid, v in terms_out.items()},
          "speeds": {pp.id: pp.speed for pp in net.pumps}, "prv_drop": {pp.id: pp.prv_drop for pp in net.pumps}}
    return NetworkResult(converged=status["converged"], iterations=status["iterations"], solves=status["solves"], nodes=nodes_out,
                         segments=segments_out, pumps=pumps_out, terminals=terms_out, totals=totals, checks=checks, si=si)


def solve_network(network: dict, *, water_temp_f: float = 68.0, elevation_ft: float = 0.0, clog_pct: float = 0.0,
                  curves: dict | None = None, pump_overrides: dict | None = None,
                  npsh_margin_ft: float = NPSH_DEFAULT_MARGIN_FT) -> NetworkResult:
    """Build and solve a model-2 network; every pump's operating point, NPSH margin and the checks."""
    net = build_network(network, water_temp_f=water_temp_f, elevation_ft=elevation_ft, clog_pct=clog_pct, curves=curves,
                        pump_overrides=pump_overrides)
    status = solve_network_si(net)
    res = _report(net, status, npsh_margin_ft)
    res.si["net"] = net
    return res


def network_operating_point(network: dict, **kw) -> CalcResult:
    """The envelope: :func:`solve_network` as a ``CalcResult`` (value = the result tables)."""
    res = solve_network(network, **kw)
    steps = [f"{len(res.pumps)} pump(s), {len(res.segments)} segment(s), {len(res.terminals)} terminal(s): "
             f"{'converged' if res.converged else 'NOT converged'} in {res.iterations} Newton steps over {res.solves} solve(s)"]
    for pid, p in res.pumps.items():
        if p.get("head_ft") is not None:
            steps.append(f"{pid}: {p['flow_gpm']:.1f} GPM @ {p['head_ft']:.1f} ft, {p['speed_pct']:.0f}% speed, "
                         f"{p['electrical_kw']:.2f} kW, NPSH margin {p['npsh_margin_ft'] if p['npsh_margin_ft'] is None else round(p['npsh_margin_ft'], 1)} ft ({p['status']})")
    steps.append(f"terminals take {res.totals['terminal_flow_gpm']:.1f} GPM; worst node imbalance {res.totals['continuity_gpm']:.2e} GPM")
    return CalcResult(
        calc="network_operating_point",
        value=res.to_dict(),
        unit="",
        inputs={
            "water_temp": make_input(float(kw.get("water_temp_f", 68.0)), "degF", "user"),
            "elevation": make_input(float(kw.get("elevation_ft", 0.0)), "ft", "user", "site altitude (NPSHa)"),
            "clog": make_input(float(kw.get("clog_pct", 0.0)), "%", "user"),
        },
        formula="h_link(Q) = H_from - H_to on every link ; sum Q = 0 at every free node (Newton, global gradient)",
        steps=steps,
        citations=[CIT_GGA, CIT_MULTI_PUMP, CIT_DRC],
        warnings=[c["title"] for c in res.checks if c["severity"] != INFO],
        status="Okay" if not any(c["severity"] == BLOCKER for c in res.checks) else "Blocked",
    )


__all__ = [
    "Network",
    "NetworkResult",
    "build_network",
    "network_operating_point",
    "solve_network",
    "solve_network_si",
]
