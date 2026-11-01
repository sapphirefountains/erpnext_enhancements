"""Manifolds: how a header pipe shares its flow among the takeoffs along it.

Port of Digital Rain Curtain ``src/hydro/manifold.js`` ``manifoldSolve``, ``manifoldRuns``,
``demandModel``, ``sizeSweep`` and ``manifoldVolumeL`` (with ``common.js`` ``nozzle``,
``branchK``, ``designPressures``, ``manifoldSpec``, ``manifoldBeam``, ``elevationProfile``
for the rain-curtain case); golden file ``tests/golden/drc/manifold.json``.

**The solve.** Each run (feed point to dead end) is solved by shooting on the flow that
enters it: march from the feed, take off each branch's flow ``q_i = sqrt((p + rho g dz_i +
rho g h_below) / K_i)``, add the velocity-head regain ``gamma rho/2 (u_before^2 - u_after^2)``
(gamma = 0.9) as the flow slows past the takeoff, subtract the Darcy friction to the next
takeoff, and root-find the inlet flow with Illinois regula falsi so nothing is left over past
the dead end. Same caps and tolerances as DRC, so the uniform case matches to 1e-12.
Friction uses DRC's interpolated friction-factor table (801 log-spaced Reynolds numbers per
relative roughness), not a fresh Churchill call, for the same reason.

**The generalization** (audit section 2). DRC assumes one branch resistance for every valve,
a uniform pitch and a straight pipe. Here a manifold is a list of :class:`Takeoff`
``{x, k, dz, open}``: any spacing (friction between neighbours uses ``|x_j+1 - x_j|``), any
mix of nozzles and valves, any elevation profile. :func:`curtain_manifold` builds DRC's
uniform rain-curtain manifold (pitch, sag between hangers, tilt) as such a list, which is how
the goldens are checked; a hand-built uniform list must reproduce it to 1e-12
(``test_hydraulics_manifold.py``).

**Velocities are float32**, as DRC's ``v`` is a ``Float32Array``: :attr:`ManifoldSolution.v`
is rounded through float32 so that ``size_sweep``'s spread (computed from those values in
DRC) matches; ``v64`` keeps the unrounded figures.

**As a terminal.** :class:`ManifoldDemand` fits phase A's ``Demand`` contract. By default it
is DRC's ``demandModel``: one equivalent orifice ``Q = G sqrt(p + pg)`` with ``G`` from an
exact solve at a reference pressure (the design pressure) and ``pg`` the mean open-takeoff
elevation. ``exact=True`` solves the manifold on every ``q(p)`` instead (and inverts it for
``p_for``), for headers where friction is large enough that the flow no longer scales as
``sqrt(p)``. Either way :meth:`ManifoldDemand.solution` gives the per-takeoff split at the
pressure the operating point or the network settles on.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, replace

from ..envelope import CalcOption, CalcResult, make_input
from . import _js
from .catalog import PipeSpec, pipe_spec_si, sizes_for
from .units import CIT_DRC, M_PER_FT, MM_PER_IN, PA_PER_PSI, G, f_to_c, gpm_to_m3s, m3s_to_gpm
from .water import Water, area_of_si, friction_factor_si, water_si

CIT_MANIFOLD = "Manifold flow distribution by shooting on the inlet flow (Bajura & Jones 1976; Idelchik, ch. 7)"
CIT_REGAIN = "Static-pressure regain past a dividing-flow takeoff, gamma = 0.9 (Bajura 1971; DRC manifold.js)"
CIT_SPREAD = "Sapphire standard 2026-11-01: a manifold is sized for at most 2% flow spread end to end"

REGAIN = 0.9
OVERHANG_M = 0.12  # DRC: 12 cm of pipe past the end takeoffs at each end
SPREAD_TARGET_PCT = 2.0

# ---------------------------------------------------------------------------
# Friction-factor table (DRC fTable / fAt)
# ---------------------------------------------------------------------------

_FT: dict[float, list] = {}


def _f_table(rr: float) -> list:
    """Churchill friction factors at Re = 10^(i/100), i = 0..800, for one relative roughness."""
    t = _FT.get(rr)
    if t is None:
        t = [friction_factor_si(10 ** (i / 100), rr) for i in range(801)]
        if len(_FT) > 32:
            _FT.clear()
        _FT[rr] = t
    return t


def _f_at(t: list, re: float) -> float:
    """``fAt``: the table interpolated linearly in log10(Re); 64/Re below Re = 1."""
    if re <= 1:
        return 64 / re if re > 0 else 0.0
    x = _js.jlog10(re) * 100
    if x >= 800:
        return t[800]
    i = int(x)
    return t[i] + (t[i + 1] - t[i]) * (x - i)


def _f32(x: float) -> float:
    """``Float32Array`` storage: round a double to the nearest float32."""
    return struct.unpack("<f", struct.pack("<f", x))[0]


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Takeoff:
    """One branch off the manifold (SI).

    ``k`` is the whole branch as a quadratic resistance, ``dp = k q^2`` (Pa s^2/m^6): valve,
    drop tube and nozzle together. ``dz`` is how far the takeoff sits **below** the manifold's
    nominal centerline (m, DRC's sign: tilt and sag). ``aj`` is the jet area (m^2) the
    velocity is reported at; 0 reports no velocity. ``q_design`` (m^3/s) is the branch's flow
    at the design pressure, which sets the lumped demand's reference when the manifold has
    no ``p_design``."""

    x: float
    k: float
    dz: float = 0.0
    open: bool = True
    aj: float = 0.0
    q_design: float | None = None
    label: str = ""


@dataclass(frozen=True)
class Manifold:
    """A header pipe and its takeoffs, ordered by ``x`` (SI).

    ``feed``: ``end`` (in past the last takeoff), ``center`` or ``both`` (each end feeds half),
    as DRC. ``nozzle_below_m``: every nozzle outlet below the centerline (adds ``rho g h``).
    ``length_m``: pipe length the takeoffs span (default ``x_first + x_last``, i.e. equal end
    margins); the pipe volume adds ``overhang_m`` at each end. ``p_design`` / ``p_branch``: the
    design inlet pressure and the whole-branch drop at the design flow (Pa), which the lumped
    demand uses as its reference. ``profile``: the rain-curtain parameters
    :func:`curtain_manifold` used, so :meth:`with_spec` can redo the sag for another pipe."""

    spec: PipeSpec
    takeoffs: tuple
    feed: str = "end"
    regain: bool = True
    nozzle_below_m: float = 0.0
    length_m: float | None = None
    overhang_m: float = OVERHANG_M
    p_design: float | None = None
    p_branch: float | None = None
    profile: dict | None = None
    label: str = ""

    @property
    def n(self) -> int:
        return len(self.takeoffs)

    @property
    def span_m(self) -> float:
        if self.length_m is not None:
            return self.length_m
        if not self.takeoffs:
            return 0.0
        return self.takeoffs[0].x + self.takeoffs[-1].x

    def with_spec(self, spec: PipeSpec) -> Manifold:
        """The same takeoffs on another pipe; a rain-curtain manifold re-derives its sag."""
        if self.profile:
            return curtain_manifold(spec=spec, **self.profile)
        return replace(self, spec=spec)


@dataclass
class ManifoldSolution:
    """``manifoldSolve``'s result, snake_case: per-takeoff flow (m^3/s), pressure at the
    takeoff (Pa, including its own ``rho g dz``), jet velocity (float32, m/s), the total, the
    worst run's regula-falsi iteration count and whether every run converged."""

    q: list
    p: list
    v: list
    v64: list
    Q: float
    iters: int
    converged: bool

    def spread_pct(self, by: str = "v") -> float:
        """(max - min) / max over the open takeoffs' velocities (or flows), in percent."""
        vals = self.v if by == "v" else self.q
        hi = max(vals, default=0.0)
        lo = min(vals, default=0.0)
        return (hi - lo) / hi * 100 if hi > 0 else 100.0


def manifold_runs(n: int, feed: str = "end") -> list[list[int]]:
    """``manifoldRuns``: the runs (feed to dead end) as takeoff index lists, feed side first.

    End feed comes in past the last takeoff; center feed at the middle; both ends feed half
    each. One takeoff is a single run whatever the feed (DRC builds an index -1 there)."""
    if n <= 0:
        return [[]]
    if n == 1:
        return [[0]]

    def seq(a: int, b: int) -> list[int]:
        step = 1 if a <= b else -1
        return list(range(a, b + step, step))

    m = n // 2
    if feed == "center":
        return [seq(m, n - 1), seq(m - 1, 0)]
    if feed == "both":
        return [seq(n - 1, m), seq(0, m - 1)]
    return [seq(n - 1, 0)]


def manifold_solve_si(m: Manifold, p_in: float, w: Water, open_=None) -> ManifoldSolution:
    """``manifoldSolve``: the steady split at feed gauge pressure ``p_in`` (Pa).

    ``open_`` overrides each takeoff's ``open`` flag (a valve pattern)."""
    tks = m.takeoffs
    n = len(tks)
    spec = m.spec
    d, am = spec.id_m, spec.area
    ft = _f_table(spec.eps_mm / spec.id_mm)
    rho_g = w.rho * G
    head = rho_g * (m.nozzle_below_m or 0)
    gam = REGAIN if m.regain else 0
    half = w.rho / 2
    nu = w.nu
    opened = [bool(t.open) for t in tks] if open_ is None else [bool(o) for o in open_]
    inv_k = [1 / t.k if t.k > 0 else 0.0 for t in tks]
    dz = [t.dz for t in tks]
    xs = [t.x for t in tks]
    q = [0.0] * n
    pp = [0.0] * n

    def q_of(i: int, p: float) -> float:
        if not opened[i]:
            return 0.0
        dp = p + rho_g * dz[i] + head
        return math.sqrt(dp * inv_k[i]) if dp > 0 else 0.0

    def fric(qq: float, length: float) -> float:
        if qq <= 0:
            return 0.0
        u = qq / am
        return _f_at(ft, (u * d) / nu) * (length / d) * half * u * u

    def uu(qq: float) -> float:
        return _js.jpow(qq / am, 2)

    iters, converged = 0, True
    for run in manifold_runs(n, m.feed):
        nr = len(run)
        if not nr:
            continue
        gaps = [abs(xs[run[j + 1]] - xs[run[j]]) for j in range(nr - 1)]

        def left(q_in: float) -> float:
            """Flow left in the pipe past the dead end when ``q_in`` enters the run (fills q, pp)."""
            p, down = p_in, q_in
            for j in range(nr):
                i = run[j]
                qi = q_of(i, p)
                after = down - qi
                pp[i] = p
                q[i] = qi
                if gam:
                    p += gam * half * (uu(down) - uu(after))
                down = after
                if j < nr - 1:
                    p -= fric(down, gaps[j]) if down >= 0 else -fric(-down, gaps[j])
            return down

        a = 0.0
        fa = left(0.0)
        if fa >= 0:
            continue  # nothing on this run can flow: q = 0, pressures stay at p_in
        b = 0.0
        for i in run:
            b += q_of(i, p_in)
        b = 1.2 * b + 1e-12
        fb = left(b)
        e = 0
        while fb < 0 and e < 60:
            a, fa = b, fb
            b *= 2
            fb = left(b)
            e += 1
        # Illinois regula falsi on the inlet flow
        tol = 1e-10 * b
        x, fx, it = b, fb, 0
        while it < 200 and abs(fx) > tol and b - a != 0 and abs(b - a) > 1e-15 * b:
            den = fb - fa
            x = b - (fb * (b - a)) / den if den != 0 else _js.NAN
            if not math.isfinite(x) or x <= _js.jmin(a, b) or x >= _js.jmax(a, b):
                x = 0.5 * (a + b)
            fx = left(x)
            if fx * fb < 0:
                a, fa = b, fb
            else:
                fa /= 2  # Illinois: halve the stale end so it cannot stall
            b, fb = x, fx
            it += 1
        # a collapsed bracket at a kink (far takeoffs just reaching zero pressure) is converged too
        if abs(fx) > 1e-6 * x + 1e-12 and not (abs(b - a) <= 1e-12 * b and abs(fx) < 1e-3 * x):
            converged = False
        left(x)
        iters = max(iters, it)

    p_out, v, v64 = [0.0] * n, [0.0] * n, [0.0] * n
    total = 0.0
    for i in range(n):
        p_out[i] = pp[i] + rho_g * dz[i]
        aj = tks[i].aj
        v64[i] = q[i] / aj if aj > 0 else 0.0
        v[i] = _f32(v64[i])
        total += q[i]
    return ManifoldSolution(q=q, p=p_out, v=v, v64=v64, Q=total, iters=iters, converged=converged)


def manifold_volume_l(m: Manifold) -> float:
    """``manifoldVolumeL``: liters of water in the manifold pipe, overhang included."""
    return area_of_si(m.spec.id_m) * (m.span_m + 2 * m.overhang_m) * 1000


def size_sweep_si(m: Manifold, material, p_in: float | None, w: Water, target_pct: float = 5.0) -> list[dict]:
    """``sizeSweep``: every catalog size of ``material`` with the takeoffs all open at ``p_in``
    (default: the manifold's design pressure), and the jet-velocity spread each gives."""
    rows = []
    for s in sizes_for(material):
        spec = pipe_spec_si(material, s["nominal"])
        mm = m.with_spec(spec)
        p = p_in if p_in is not None else mm.p_design
        if p is None:
            raise ValueError("size_sweep needs an inlet pressure or a manifold with p_design")
        sol = manifold_solve_si(mm, p, w, [True] * mm.n)
        by_v = any(t.aj > 0 for t in mm.takeoffs)
        vals = sol.v if by_v else sol.q
        lo, hi = _js.INF, -_js.INF
        for val in vals:
            if val < lo:
                lo = val
            if val > hi:
                hi = val
        spread = ((hi - lo) / hi) * 100 if hi > 0 else 100
        rows.append({
            "nps": s["nps"], "nominal": s["nominal"], "id_mm": spec.id_mm, "od_mm": spec.od_mm,
            "min_v": lo, "max_v": hi, "spread_pct": spread, "ok": spread <= target_pct, "Q": sol.Q,
            "by": "velocity" if by_v else "flow",
        })
    return rows


# ---------------------------------------------------------------------------
# DRC's rain-curtain manifold (nozzle, branchK, designPressures, manifoldSpec,
# manifoldBeam, elevationProfile)
# ---------------------------------------------------------------------------


def curtain_branch_si(orifice_mm: float, w: Water, *, cc: float = 1.0, cv: float = 1.0, v0: float = 0.0,
                      valve_kv: float = 0.0, tube_id_mm: float = 0.0, tube_len_m: float = 0.0) -> dict:
    """``nozzle`` + ``branchK``: one valve branch as ``dp = k q^2`` and its jet area and nominal flow."""
    d_m = orifice_mm / 1000
    a = area_of_si(d_m)
    cc = _js.jmin(1.0, _js.jmax(0.5, _js.or_(_js.num(cc), 1)))
    cv = _js.or_(cv, 1)
    aj = a * cc
    q_nom = aj * _js.jmax(0.0, v0)
    k_n = w.rho / (2 * _js.jpow(aj * cv, 2))
    k_v = 1e5 * (w.rho / 1000) * _js.jpow(3600 / valve_kv, 2) if valve_kv > 0 else 0.0
    k_t = 0.0
    if tube_id_mm > 0:
        dt = tube_id_mm / 1000
        at = area_of_si(dt)
        u = _js.jmax(q_nom, 1e-9) / at
        f = friction_factor_si((u * dt) / w.nu, 0.0015 / tube_id_mm)
        k_t = ((f * _js.jmax(0.0, _js.or_(tube_len_m, 0))) / dt + 1.0) * w.rho / (2 * at * at)
    k = k_n + k_v + k_t
    return {"aj": aj, "q_nom": q_nom, "k_n": k_n, "k_v": k_v, "k_t": k_t, "k": k,
            "p_nozzle": k_n * q_nom * q_nom, "p_branch": k * q_nom * q_nom}


def curtain_spec(material, nominal, bore_mm: float, wall_mm: float) -> PipeSpec:
    """``manifoldSpec``: the catalog pipe, or a hand-typed bore on that material's wall."""
    s = pipe_spec_si(material, nominal, bore_mm, wall_mm)
    if abs(s.id_mm - bore_mm) <= 0.05:
        return s
    return replace(s, nominal=None, id_mm=bore_mm, od_mm=bore_mm + 2 * s.wall_mm, id_m=bore_mm / 1000,
                   area=area_of_si(bore_mm / 1000))


def manifold_beam_si(spec: PipeSpec, valves: int, pitch_mm: float, hanger_m: float, w: Water) -> dict:
    """``manifoldBeam``: hanger spans, wet load per meter, EI and the mid-span sag ``5wL^4/384EI``."""
    width = (valves * pitch_mm) / 1000
    n_spans = _js.jmax(1, math.ceil(width / _js.jmax(0.1, _js.or_(_js.num(hanger_m), 1)) - 1e-9))
    span = width / n_spans
    di, do = spec.id_mm / 1000, spec.od_mm / 1000
    w_npm = G * (w.rho * area_of_si(di) + spec.density * (area_of_si(do) - area_of_si(di)))
    ei = spec.e_gpa * 1e9 * (math.pi / 64) * (_js.jpow(do, 4) - _js.jpow(di, 4))
    return {"W": width, "n_spans": n_spans, "span_m": span, "hangers": n_spans + 1, "w_npm": w_npm, "EI": ei,
            "max_sag_m": (5 * w_npm * _js.jpow(span, 4)) / (384 * ei)}


def curtain_profile_si(spec: PipeSpec, valves: int, pitch_mm: float, w: Water, *, tilt_mm: float = 0.0,
                       sag: bool = True, hanger_m: float = 1.0) -> list[float]:
    """``elevationProfile``: each takeoff below the centerline (m): tilt plus sag between hangers."""
    pitch = pitch_mm / 1000
    width = valves * pitch
    tilt = _js.or_(_js.num(tilt_mm), 0) / 1000
    beam = manifold_beam_si(spec, valves, pitch_mm, hanger_m, w) if sag else None
    dz = []
    for i in range(valves):
        x = (i + 0.5) * pitch
        z = -tilt * (x / width - 0.5)
        if beam:
            span = beam["span_m"]
            xi = x - math.floor(x / span) * span
            z += (beam["w_npm"] * xi * (_js.jpow(span, 3) - 2 * span * xi * xi + _js.jpow(xi, 3))) / (24 * beam["EI"])
        dz.append(z)
    return dz


def curtain_manifold(*, spec: PipeSpec, valves: int, pitch_mm: float, orifice_mm: float, w_c: float = 20.0,
                     cc: float = 1.0, cv: float = 1.0, v0: float = 0.0, valve_kv: float = 0.0, tube_id_mm: float = 0.0,
                     tube_len_m: float = 0.0, tilt_mm: float = 0.0, sag: bool = True, hanger_m: float = 1.0,
                     nozzle_below_m: float = 0.0, regain: bool = True, feed: str = "end") -> Manifold:
    """DRC's rain-curtain manifold as a takeoff list: ``valves`` identical branches at a uniform
    pitch, takeoff ``i`` at ``(i + 1/2) pitch``, with the tilt-and-sag elevation profile."""
    w = water_si(w_c)
    br = curtain_branch_si(orifice_mm, w, cc=cc, cv=cv, v0=v0, valve_kv=valve_kv, tube_id_mm=tube_id_mm, tube_len_m=tube_len_m)
    dz = curtain_profile_si(spec, valves, pitch_mm, w, tilt_mm=tilt_mm, sag=sag, hanger_m=hanger_m)
    pitch = pitch_mm / 1000
    takeoffs = tuple(
        Takeoff(x=(i + 0.5) * pitch, k=br["k"], dz=dz[i], aj=br["aj"], q_design=br["q_nom"], label=str(i + 1))
        for i in range(valves)
    )
    profile = dict(valves=valves, pitch_mm=pitch_mm, orifice_mm=orifice_mm, w_c=w_c, cc=cc, cv=cv, v0=v0, valve_kv=valve_kv,
                   tube_id_mm=tube_id_mm, tube_len_m=tube_len_m, tilt_mm=tilt_mm, sag=sag, hanger_m=hanger_m,
                   nozzle_below_m=nozzle_below_m, regain=regain, feed=feed)
    return Manifold(
        spec=spec, takeoffs=takeoffs, feed=feed, regain=regain, nozzle_below_m=nozzle_below_m,
        length_m=(valves * pitch_mm) / 1000,
        p_design=br["p_branch"] - w.rho * G * (nozzle_below_m or 0), p_branch=br["p_branch"], profile=profile,
    )


# ---------------------------------------------------------------------------
# The manifold as a terminal (DRC demandModel)
# ---------------------------------------------------------------------------


def _design_pressures(m: Manifold, opened: list) -> tuple[float | None, float]:
    """(design inlet pressure, branch drop) from the manifold, else from its takeoffs' design flows."""
    if m.p_design is not None:
        return m.p_design, m.p_branch or 0.0
    rows = [t for t, o in zip(m.takeoffs, opened, strict=False) if o and t.q_design]
    if not rows:
        return None, 0.0
    p_branch = sum(t.k * t.q_design * t.q_design for t in rows) / len(rows)
    return p_branch, p_branch


class ManifoldDemand:
    """A manifold terminal on the ``Demand`` contract.

    Default (DRC ``demandModel``): ``Q = G sqrt(p + pg)``, ``pg = rho g (h_below + mean dz of the
    open takeoffs)``, ``G`` from an exact solve at ``p_ref`` (default the design pressure; if
    that leaves ``p_ref + pg <= 1 Pa``, ``1 - pg + p_branch`` as DRC). ``exact=True`` solves the
    manifold on every call instead. With no design pressure and no ``p_ref`` the reference is
    1 bar, and :attr:`note` says so."""

    p_fixed = None

    def __init__(self, manifold: Manifold, w: Water, open_=None, p_ref: float | None = None, exact: bool = False, label: str = ""):
        self.manifold = manifold
        self.w = w
        self.exact = bool(exact)
        self.label = label or manifold.label
        tks = manifold.takeoffs
        self.open = [bool(t.open) for t in tks] if open_ is None else [bool(o) for o in open_]
        self.n_open = sum(1 for o in self.open if o)
        self.any_open = self.n_open > 0
        self.note = ""
        pg = 0.0
        if self.any_open:
            sum_dz = 0.0
            for t, o in zip(tks, self.open, strict=False):
                if o:
                    sum_dz += t.dz
            pg = w.rho * G * ((manifold.nozzle_below_m or 0) + sum_dz / self.n_open)
        self.pg = pg
        p_design, p_branch = _design_pressures(manifold, self.open)
        if p_ref is None:
            p_ref = p_design
        if p_ref is None:
            p_ref = 1e5
            self.note = "No design pressure on the manifold or its takeoffs: the lumped demand is referenced to 1 bar."
        if p_ref + pg <= 1:
            p_ref = 1 - pg + p_branch
        self.p_ref = p_ref
        self.ref = manifold_solve_si(manifold, p_ref, w, self.open)
        self.q_ref = self.ref.Q
        self.G = self.q_ref / math.sqrt(p_ref + pg) if self.any_open else 0.0

    def solution(self, p: float) -> ManifoldSolution:
        """The exact per-takeoff split at inlet pressure ``p``."""
        return manifold_solve_si(self.manifold, p, self.w, self.open)

    def scale(self, p: float) -> float:
        return math.sqrt(_js.jmax(0.0, p + self.pg) / (self.p_ref + self.pg)) if self.any_open else 0.0

    def v_at(self, p: float) -> list:
        """``vAt``: the reference jet velocities scaled by ``sqrt((p + pg) / (p_ref + pg))`` (float32)."""
        s = self.scale(p)
        return [_f32(v * s) for v in self.ref.v]

    def q(self, p: float) -> float:
        if self.exact:
            return self.solution(p).Q if self.any_open else 0.0
        return self.G * math.sqrt(p + self.pg) if self.any_open and p + self.pg > 0 else 0.0

    def p_for(self, q: float) -> float:
        if q <= 0:
            return -self.pg
        if not self.any_open:
            return math.inf
        lumped = _js.jpow(q / self.G, 2) - self.pg
        if not self.exact:
            return lumped
        # Exact: invert q(p) (monotone) by bracketing then Illinois regula falsi.
        drop = max((t.dz for t, o in zip(self.manifold.takeoffs, self.open, strict=False) if o), default=0.0)
        lo = -self.w.rho * G * ((self.manifold.nozzle_below_m or 0) + drop) - 1.0
        hi = max(lumped, lo + 1.0)
        f_lo, f_hi = self.q(lo) - q, self.q(hi) - q
        for _ in range(200):
            if f_hi >= 0:
                break
            width = hi - lo
            lo, f_lo = hi, f_hi
            hi = hi + 2 * width + 1.0
            f_hi = self.q(hi) - q
        else:
            return math.inf
        for _ in range(200):
            if hi - lo <= 1e-12 * max(1.0, abs(hi)):
                break
            x = hi - f_hi * (hi - lo) / (f_hi - f_lo) if f_hi != f_lo else 0.5 * (lo + hi)
            if not (lo < x < hi):
                x = 0.5 * (lo + hi)
            fx = self.q(x) - q
            if fx == 0:
                return x
            if fx > 0:
                hi, f_hi = x, fx
                f_lo /= 2
            else:
                lo, f_lo = x, fx
                f_hi /= 2
        return 0.5 * (lo + hi)


# ---------------------------------------------------------------------------
# From the design model (US) and the envelopes
# ---------------------------------------------------------------------------


def _takeoff_k(t: dict, rho: float) -> tuple[float, float, float | None]:
    """(k, jet area m^2, design flow m^3/s) of one US takeoff row."""
    area_in2 = t.get("orifice_area_in2")
    if not area_in2 and t.get("orifice_diameter_in"):
        area_in2 = math.pi / 4 * float(t["orifice_diameter_in"]) ** 2
    area = float(area_in2) * (MM_PER_IN / 1000) ** 2 if area_in2 else 0.0
    if t.get("k"):
        return float(t["k"]), area, None
    if t.get("cd") and area:
        cd = float(t["cd"])
        return rho / (2 * (cd * area) ** 2), area, None
    if t.get("rated_gpm") and t.get("rated_head_ft"):
        q = gpm_to_m3s(float(t["rated_gpm"]))
        p = rho * G * float(t["rated_head_ft"]) * M_PER_FT
        return p / (q * q), area, q
    raise ValueError("a manifold takeoff needs k, or cd + orifice size, or rated_gpm + rated_head_ft")


def manifold_from_us(params: dict, rho: float) -> Manifold:
    """A manifold from a model-2 terminal's ``params`` (US units).

    ``{material, nominal_size | id_in, wall_in, feed, regain, nozzle_below_ft, design_psi,
    takeoffs: [{x_ft, cd, orifice_diameter_in | orifice_area_in2 | rated_gpm + rated_head_ft | k,
    dz_in, open, label}]}``, or a uniform row ``{count, pitch_in, cd, orifice_diameter_in, ...,
    tilt_in}`` instead of ``takeoffs`` (takeoff ``i`` at ``(i + 1/2) pitch``; ``tilt_in`` raises
    the far end, DRC's sign)."""
    id_in = params.get("id_in")
    wall_in = params.get("wall_in")
    spec = pipe_spec_si(params.get("material") or "SCH40 PVC", params.get("nominal_size"),
                        float(id_in) * MM_PER_IN if id_in else 0, float(wall_in) * MM_PER_IN if wall_in else 0)
    rows = params.get("takeoffs")
    if not rows:
        count = int(params.get("count") or 0)
        pitch_ft = float(params.get("pitch_in") or 0) / 12
        tilt_ft = float(params.get("tilt_in") or 0) / 12
        width = count * pitch_ft
        rows = []
        for i in range(count):
            x = (i + 0.5) * pitch_ft
            row = {k: params[k] for k in ("cd", "orifice_diameter_in", "orifice_area_in2", "rated_gpm", "rated_head_ft", "k") if params.get(k)}
            row.update(x_ft=x, dz_in=-tilt_ft * (x / width - 0.5) * 12 if width else 0.0, label=str(i + 1))
            rows.append(row)
    takeoffs = []
    for i, t in enumerate(rows):
        k, area, q_design = _takeoff_k(t, rho)
        takeoffs.append(Takeoff(
            x=float(t.get("x_ft") or 0) * M_PER_FT, k=k, dz=float(t.get("dz_in") or 0) * MM_PER_IN / 1000,
            open=bool(t.get("open", True)), aj=area, q_design=q_design, label=str(t.get("label") or i + 1),
        ))
    takeoffs.sort(key=lambda t: t.x)
    nb = float(params.get("nozzle_below_ft") or 0) * M_PER_FT
    p_design = float(params["design_psi"]) * PA_PER_PSI if params.get("design_psi") else None
    length_ft = params.get("length_ft")
    return Manifold(
        spec=spec, takeoffs=tuple(takeoffs), feed=params.get("feed") or "end", regain=params.get("regain", True) is not False,
        nozzle_below_m=nb, length_m=float(length_ft) * M_PER_FT if length_ft else None, p_design=p_design,
        p_branch=p_design + rho * G * nb if p_design is not None else None, label=params.get("label") or "",
    )


def manifold_demand_from_us(params: dict, rho: float, label: str = "") -> ManifoldDemand:
    """``build_demand`` for ``kind: manifold``: the lumped demand unless ``params.exact``."""
    w = _water_for_rho(rho)
    m = manifold_from_us(params, w.rho)
    p_ref = float(params["reference_psi"]) * PA_PER_PSI if params.get("reference_psi") else None
    return ManifoldDemand(m, w, p_ref=p_ref, exact=bool(params.get("exact")), label=label)


def _water_for_rho(rho: float) -> Water:
    """The water whose density is ``rho`` (bisection on temperature, 4-100 C): demands are built from
    a density, the manifold also needs the viscosity."""
    if abs(water_si(20).rho - rho) < 1e-9:
        return water_si(20)
    lo, hi = 4.0, 100.0
    if rho >= water_si(lo).rho:
        return water_si(lo)
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if water_si(mid).rho > rho:
            lo = mid
        else:
            hi = mid
    return water_si(0.5 * (lo + hi))


def manifold_distribution(params: dict, inlet_psi: float, water_temp_f: float = 68.0) -> CalcResult:
    """The flow and pressure at every takeoff of a manifold fed at ``inlet_psi``, and the spread."""
    w = water_si(f_to_c(float(water_temp_f)))
    m = manifold_from_us(params, w.rho)
    p_in = float(inlet_psi) * PA_PER_PSI
    sol = manifold_solve_si(m, p_in, w)
    by_v = any(t.aj > 0 for t in m.takeoffs)
    open_idx = [i for i, t in enumerate(m.takeoffs) if t.open]
    vals = [(sol.v64 if by_v else sol.q)[i] for i in open_idx]
    hi, lo = max(vals, default=0.0), min(vals, default=0.0)
    spread = (hi - lo) / hi * 100 if hi > 0 else 100.0
    warnings = []
    if not sol.converged:
        warnings.append("The manifold solve did not converge on every run; treat the split as approximate.")
    if spread > SPREAD_TARGET_PCT:
        warnings.append(f"Flow spread {spread:.1f}% is over the {SPREAD_TARGET_PCT:g}% target: upsize the manifold or feed it from both ends.")
    rows = [
        {
            "label": t.label, "x_ft": t.x / M_PER_FT, "open": t.open, "flow_gpm": m3s_to_gpm(sol.q[i]),
            "pressure_psi": sol.p[i] / PA_PER_PSI, "velocity_fps": sol.v64[i] / M_PER_FT if by_v else None,
        }
        for i, t in enumerate(m.takeoffs)
    ]
    return CalcResult(
        calc="manifold_distribution",
        value={"flow_gpm": m3s_to_gpm(sol.Q), "spread_pct": spread, "takeoffs": rows, "converged": sol.converged,
               "volume_gal": manifold_volume_l(m) / 3.785411784},
        unit="",
        inputs={
            "inlet": make_input(float(inlet_psi), "psi", "user"),
            "manifold": make_input(f"{m.spec.label} {m.spec.nominal or f'{m.spec.id_mm / MM_PER_IN:.3f} in bore'}", "", "user"),
            "takeoffs": make_input(m.n, "", "user"),
            "feed": make_input(m.feed, "", "user"),
            "water_temp": make_input(float(water_temp_f), "degF", "user"),
        },
        formula="q_i = sqrt((p_i + rho g dz_i) / K_i) ; p_i+1 = p_i + 0.9 rho/2 (u_i^2 - u_i'^2) - f L/D rho u'^2/2 ; shoot on Q_in",
        steps=[f"{m.n} takeoffs, {m.feed} feed: {m3s_to_gpm(sol.Q):.2f} GPM at {inlet_psi:g} psi ; spread {spread:.2f}%",
               f"regula falsi {sol.iters} iterations on the worst run"],
        citations=[CIT_MANIFOLD, CIT_REGAIN, CIT_SPREAD, CIT_DRC],
        warnings=warnings,
    )


def manifold_size_sweep(params: dict, material: str | None = None, inlet_psi: float | None = None,
                        target_pct: float = SPREAD_TARGET_PCT, water_temp_f: float = 68.0) -> CalcResult:
    """The smallest catalog size whose spread is within ``target_pct`` (``sizeSweep``)."""
    w = water_si(f_to_c(float(water_temp_f)))
    m = manifold_from_us(params, w.rho)
    material = material or params.get("material") or "SCH40 PVC"
    p_in = float(inlet_psi) * PA_PER_PSI if inlet_psi is not None else (m.p_design if m.p_design is not None else _design_pressures(m, [t.open for t in m.takeoffs])[0])
    warnings = []
    if p_in is None:
        p_in = 1e5
        warnings.append("No inlet pressure and no design pressure: swept at 1 bar (14.5 psi).")
    rows = size_sweep_si(m, material, p_in, w, target_pct)
    pick = next((r for r in rows if r["ok"]), None)
    if pick is None and rows:
        warnings.append(f"No {material} size holds the spread within {target_pct:g}%; the largest is listed.")
    options = [
        CalcOption(key=r["nominal"], label=f"{r['nominal']} {material}", value=r["nominal"], recommended=r is pick,
                   detail={"spread_pct": r["spread_pct"], "flow_gpm": m3s_to_gpm(r["Q"]), "id_in": r["id_mm"] / MM_PER_IN})
        for r in rows
    ]
    return CalcResult(
        calc="manifold_size_sweep",
        value=pick["nominal"] if pick else (rows[-1]["nominal"] if rows else None),
        unit="nominal size",
        inputs={"material": make_input(material, "", "user"), "target": make_input(target_pct, "%", "standard", CIT_SPREAD),
                "inlet": make_input(p_in / PA_PER_PSI, "psi", "prior_calc")},
        formula="smallest size with (v_max - v_min) / v_max <= target, every takeoff open",
        steps=[f"{r['nominal']}: spread {r['spread_pct']:.2f}%" for r in rows],
        citations=[CIT_MANIFOLD, CIT_SPREAD, CIT_DRC],
        options=options,
        warnings=warnings,
    )
