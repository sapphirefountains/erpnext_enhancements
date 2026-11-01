"""Pump curves: head, efficiency and NPSHr against flow, BEP, run-out, shaft power, affinity.

Port of Digital Rain Curtain ``src/hydro/flow.js`` ``clean``, ``interp``, ``pumpHeadM``,
``pumpEff``, ``npshrM``, ``bepFlow``, ``maxFlow``, ``shaftPowerW``, ``pumpCurve``;
golden file ``tests/golden/drc/pump.json`` (1e-12 for interpolation, 1e-9 where a
bisection is involved).

A curve is a list of ``(flow, value)`` points with flow in **L/min**, head and NPSHr in
m and efficiency in %, as DRC stores them (and as metric datasheets print them); the
kernels convert to m^3/s at the call. Speed scales by the affinity laws: at speed
ratio ``s`` the head at flow Q is ``s^2 H(Q/s)``, efficiency is read at ``Q/s``, and
NPSHr is scaled by ``s^2`` (DRC's choice; real NPSHr scales closer to s^1.5-2 and is
not guaranteed at low speed, audit 1.4). Past the last point the curves extrapolate
linearly, so a pump can be asked for its run-out; ``engine/pump.py head_at_flow``
returns None there instead and stays as the workbook's catalog check.

DRC's ``legacyPump`` (a quadratic from shut-off pressure and run-away flow, sized from
the rain-curtain nozzles) is not ported as such; :class:`PumpCurve` keeps the same
quadratic as ``quad = (shutoff_head_m, q_max_m3s)``, which is what a *generic
estimate* pump with only a shut-off head and a maximum flow on file can use.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..envelope import CalcResult, make_input
from . import _js
from .units import CIT_DRC, LPM_PER_GPM, M_PER_FT, W_PER_HP, G, f_to_c, gpm_to_m3s, m3s_to_gpm
from .water import Water, water_si

CIT_AFFINITY = "Hydraulic Institute affinity laws: Q ~ N, H ~ N^2, P ~ N^3 (ANSI/HI 1.3)"
CIT_PUMP_CURVE = "Manufacturer pump curve (head, efficiency, NPSHr vs flow)"
EFF_FLOOR = 0.05  # below 5% efficiency the shaft power is held at its 5% value (DRC shaftPowerW)


def clean(pts) -> list[tuple[float, float]]:
    """``clean``: the finite numeric ``[x, y]`` points, sorted by x (stable, as V8's sort)."""
    if not isinstance(pts, (list, tuple)):
        return []
    valid = [(p[0], p[1]) for p in pts if isinstance(p, (list, tuple)) and len(p) >= 2 and _js.is_number(p[0]) and _js.is_number(p[1])]
    valid.sort(key=lambda p: p[0])
    return [(float(x), float(y)) for x, y in valid]


def _interp_clean(valid: list, x: float) -> float:
    if not _js.is_number(x):
        return _js.NAN
    n = len(valid)
    if n == 0:
        return _js.NAN
    if n == 1 or x <= valid[0][0]:
        return valid[0][1]
    if x >= valid[n - 1][0]:
        (x0, y0), (x1, y1) = valid[n - 2], valid[n - 1]
        return y1 if x1 == x0 else y1 + ((x - x1) * (y1 - y0)) / (x1 - x0)
    lo, hi = 0, n - 1
    while hi - lo > 1:
        mid = (lo + hi) >> 1
        if valid[mid][0] <= x:
            lo = mid
        else:
            hi = mid
    (x0, y0), (x1, y1) = valid[lo], valid[hi]
    return y0 if x1 == x0 else y0 + ((x - x0) * (y1 - y0)) / (x1 - x0)


def interp(pts, x: float) -> float:
    """``interp``: piecewise-linear, clamped below the first point, extrapolated past the last."""
    return _interp_clean(clean(pts), x)


@dataclass(frozen=True, eq=False)
class PumpCurve:
    """A pump: head (m), efficiency (%) and NPSHr (m) against flow (L/min), motor efficiency and speed.

    Compared and hashed by identity (``eq=False``), so it can key a cache though it holds lists.
    The curves are cleaned once, at construction (DRC re-cleans when a curve's content changes):
    editing ``pump.curve`` in place afterwards is ignored, so build a new one (``dataclasses.replace``)."""

    curve: list = field(default_factory=list)
    eff: list = field(default_factory=list)
    npshr: list = field(default_factory=list)
    motor_eff_pct: float = 80.0
    speed_pct: float = 100.0
    quad: tuple | None = None  # (shutoff_head_m, q_max_m3s): the two-parameter fallback when curve < 2 points
    name: str = ""

    def __post_init__(self):
        object.__setattr__(self, "_curve", clean(self.curve))
        object.__setattr__(self, "_eff", clean(self.eff))
        object.__setattr__(self, "_npshr", clean(self.npshr))

    @property
    def head_points(self) -> list:
        """The cleaned head curve [(L/min, m)], sorted by flow."""
        return self._curve

    @property
    def eff_points(self) -> list:
        """The cleaned efficiency curve [(L/min, %)]."""
        return self._eff

    @property
    def npshr_points(self) -> list:
        """The cleaned NPSHr curve [(L/min, m)]."""
        return self._npshr

    @property
    def default_speed(self) -> float:
        return (100 if self.speed_pct is None else self.speed_pct) / 100

    @classmethod
    def from_us(
        cls,
        points: list[dict],
        *,
        motor_eff_pct: float = 80.0,
        speed_pct: float = 100.0,
        shutoff_head_ft: float | None = None,
        max_flow_gpm: float | None = None,
        name: str = "",
    ) -> PumpCurve:
        """From Pump Curve Point rows ``{flow_gpm, head_ft, efficiency_pct, npshr_ft}`` (any may be
        missing). ``shutoff_head_ft`` + ``max_flow_gpm`` give the quadratic used when there are
        fewer than two head points (a generic-estimate pump)."""
        curve, eff, npshr = [], [], []
        for p in points or []:
            q = p.get("flow_gpm")
            if q is None:
                continue
            lpm = float(q) * LPM_PER_GPM
            if p.get("head_ft") is not None:
                curve.append((lpm, float(p["head_ft"]) * M_PER_FT))
            if p.get("efficiency_pct") is not None:
                eff.append((lpm, float(p["efficiency_pct"])))
            if p.get("npshr_ft") is not None:
                npshr.append((lpm, float(p["npshr_ft"]) * M_PER_FT))
        quad = None
        if shutoff_head_ft and max_flow_gpm:
            quad = (float(shutoff_head_ft) * M_PER_FT, gpm_to_m3s(float(max_flow_gpm)))
        return cls(curve=curve, eff=eff, npshr=npshr, motor_eff_pct=motor_eff_pct, speed_pct=speed_pct, quad=quad, name=name)


def _s(pump: PumpCurve, speed) -> float:
    return _js.jmax(pump.default_speed if speed is None else speed, 1e-3)


def pump_head_si(pump: PumpCurve, q: float, speed: float | None = None) -> float:
    """``pumpHeadM``: head (m) at pump flow ``q`` (m^3/s) and speed ratio ``speed``."""
    s = _s(pump, speed)
    if len(pump._curve) < 2:
        if not pump.quad:
            return 0.0
        h0, q_max = pump.quad
        if not q_max > 0:
            return 0.0  # a pump with no run-away flow delivers nothing (and DRC's q / 0 would be NaN at Q = 0)
        ratio = q / (s * q_max)
        return _js.jmax(0.0, h0 * s * s * (1 - ratio * ratio))
    x = (q * 60000) / s
    h = s * s * _interp_clean(pump._curve, x)
    return _js.jmax(0.0, h if _js.is_number(h) else 0.0)


def pump_eff_si(pump: PumpCurve, q: float, speed: float | None = None) -> float:
    """``pumpEff``: hydraulic efficiency (0-0.95); 0.5 when the pump has no efficiency curve."""
    s = _s(pump, speed)
    if not pump._eff:
        return 0.5
    val = _interp_clean(pump._eff, (q * 60000) / s) / 100
    if not _js.is_number(val):
        return 0.5
    return _js.jmin(0.95, _js.jmax(0.0, val))


def npshr_si(pump: PumpCurve, q: float, speed: float | None = None) -> float:
    """``npshrM``: net positive suction head required (m); 0 when the pump has no NPSHr curve."""
    s = _s(pump, speed)
    if not pump._npshr:
        return 0.0
    val = s * s * _interp_clean(pump._npshr, (q * 60000) / s)
    return _js.jmax(0.0, val) if _js.is_number(val) else 0.0


def bep_flow_si(pump: PumpCurve, speed: float | None = None) -> float:
    """``bepFlow``: flow at best efficiency (m^3/s); half the curve's last flow when there is no
    efficiency curve, half the quadratic's run-away flow when there is no curve either."""
    s = _s(pump, speed)
    if pump._eff:
        max_e, flow = -_js.INF, 0.0
        for q, e in pump._eff:
            if e > max_e:
                max_e, flow = e, q
        return (flow / 60000) * s
    if pump._curve:
        max_lpm = max(p[0] for p in pump._curve)
        return 0.5 * (max_lpm / 60000) * s
    return 0.5 * (pump.quad[1] if pump.quad else 0.0) * s


def max_flow_si(pump: PumpCurve, speed: float | None = None) -> float:
    """``maxFlow``: run-out flow (m^3/s) where the head falls to zero (60-step bisection)."""
    s = _s(pump, speed)
    if len(pump._curve) < 2:
        return s * (pump.quad[1] if pump.quad else 0.0)
    last_lpm = pump._curve[-1][0]
    q_range = 10 * (last_lpm / 60000) * s
    if pump_head_si(pump, 0, s) <= 0:
        return 0.0
    lo, hi = 0.0, q_range
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if pump_head_si(pump, mid, s) > 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def shaft_power_si(pump: PumpCurve, q: float, speed: float | None, w: Water) -> float:
    """``shaftPowerW``: hydraulic power / efficiency (W). Near shut-off, where both go to zero, the
    power is held at the value where efficiency crosses 5% (a real pump still draws it there)."""
    rg = w.rho * G
    e = pump_eff_si(pump, q, speed)
    if e >= EFF_FLOOR or not pump._eff:
        return (q * rg * pump_head_si(pump, q, speed)) / _js.jmax(e, EFF_FLOOR)
    lo = _js.jmax(0.0, q)
    hi = _js.jmax(lo, bep_flow_si(pump, speed))
    for _ in range(40):
        m = 0.5 * (lo + hi)
        if pump_eff_si(pump, m, speed) < EFF_FLOOR:
            lo = m
        else:
            hi = m
    return (hi * rg * pump_head_si(pump, hi, speed)) / EFF_FLOOR


def pump_curve_si(pump: PumpCurve, speed: float | None = None, n: int = 40) -> list[dict]:
    """``pumpCurve``: ``n`` points ``{Q, headM}`` from zero to run-out."""
    qm = max_flow_si(pump, speed)
    count = max(2, n)
    return [{"Q": (i / (count - 1)) * qm, "headM": pump_head_si(pump, (i / (count - 1)) * qm, speed)} for i in range(count)]


def affinity_si(q: float, h: float, p: float, ratio: float) -> tuple[float, float, float]:
    """Affinity laws at speed ratio N2/N1: (Q r, H r^2, P r^3)."""
    return q * ratio, h * ratio * ratio, p * ratio**3


# ---------------------------------------------------------------------------
# Envelopes (US: gpm, ft, %, hp)
# ---------------------------------------------------------------------------


def _as_pump(pump) -> PumpCurve:
    if isinstance(pump, PumpCurve):
        return pump
    return PumpCurve.from_us(list(pump or []))


def _speed(speed_pct, pump: PumpCurve):
    return None if speed_pct is None else float(speed_pct) / 100


def _curve_warnings(p: PumpCurve, flow_gpm: float, speed) -> list[str]:
    warnings = []
    if len(p.head_points) < 2:
        warnings.append(
            "No pump curve: the head comes from the two-point quadratic (shut-off head, maximum flow)."
            if p.quad
            else "No pump curve and no shut-off head / maximum flow: head is 0. Enter the curve points."
        )
    elif m3s_to_gpm(p.head_points[-1][0] / 60000 * _s(p, speed)) < flow_gpm:
        warnings.append(f"{flow_gpm:g} GPM is past the last curve point; the head is extrapolated.")
    return warnings


def _inputs(p: PumpCurve, flow_gpm: float, speed) -> dict:
    return {
        "flow": make_input(float(flow_gpm), "GPM", "prior_calc"),
        "speed": make_input(round(_s(p, speed) * 100, 3), "%", "user"),
        "curve_points": make_input(len(p.head_points), "points", "lookup", p.name or "pump curve"),
    }


def pump_head(pump, flow_gpm: float, speed_pct: float | None = None) -> CalcResult:
    """Pump head (ft) at a flow (GPM) and speed (% of rated), from its curve with the affinity laws."""
    p = _as_pump(pump)
    speed = _speed(speed_pct, p)
    h = pump_head_si(p, gpm_to_m3s(float(flow_gpm)), speed) / M_PER_FT
    s = _s(p, speed)
    return CalcResult(
        calc="pump_head",
        value=h,
        unit="ft",
        inputs=_inputs(p, flow_gpm, speed),
        formula="H(Q, s) = s^2 H(Q/s)",
        steps=[f"s = {s:.3f} ; Q/s = {float(flow_gpm) / s:.2f} GPM ; H = {h:.2f} ft"],
        citations=[CIT_PUMP_CURVE, CIT_AFFINITY, CIT_DRC],
        warnings=_curve_warnings(p, float(flow_gpm), speed),
    )


def pump_eff(pump, flow_gpm: float, speed_pct: float | None = None) -> CalcResult:
    """Pump hydraulic efficiency (%) at a flow and speed (efficiency read at Q/s)."""
    p = _as_pump(pump)
    speed = _speed(speed_pct, p)
    e = pump_eff_si(p, gpm_to_m3s(float(flow_gpm)), speed)
    return CalcResult(
        calc="pump_eff",
        value=e * 100,
        unit="%",
        inputs=_inputs(p, flow_gpm, speed),
        formula="eta(Q, s) = eta(Q/s), clamped 0-95%",
        steps=[f"eta = {e * 100:.1f}%"],
        citations=[CIT_PUMP_CURVE, CIT_AFFINITY, CIT_DRC],
        warnings=[] if p.eff_points else ["No efficiency curve: 50% assumed."],
    )


def npshr(pump, flow_gpm: float, speed_pct: float | None = None) -> CalcResult:
    """Net positive suction head required (ft) at a flow and speed (s^2 scaling)."""
    p = _as_pump(pump)
    speed = _speed(speed_pct, p)
    h = npshr_si(p, gpm_to_m3s(float(flow_gpm)), speed) / M_PER_FT
    return CalcResult(
        calc="npshr",
        value=h,
        unit="ft",
        inputs=_inputs(p, flow_gpm, speed),
        formula="NPSHr(Q, s) = s^2 NPSHr(Q/s)",
        steps=[f"NPSHr = {h:.2f} ft"],
        citations=[CIT_PUMP_CURVE, CIT_AFFINITY, CIT_DRC],
        warnings=[] if p.npshr_points else ["No NPSHr curve: NPSHr reads 0 and the cavitation check cannot gate."],
    )


def bep_flow(pump, speed_pct: float | None = None) -> CalcResult:
    """Flow at best efficiency (GPM)."""
    p = _as_pump(pump)
    speed = _speed(speed_pct, p)
    q = m3s_to_gpm(bep_flow_si(p, speed))
    return CalcResult(
        calc="bep_flow",
        value=q,
        unit="GPM",
        inputs=_inputs(p, 0, speed),
        formula="Q_BEP = argmax eta(Q) * s",
        steps=[f"Q_BEP = {q:.1f} GPM"],
        citations=[CIT_PUMP_CURVE, CIT_AFFINITY, CIT_DRC],
        warnings=[] if p.eff_points else ["No efficiency curve: BEP taken as half the curve's last flow."],
    )


def max_flow(pump, speed_pct: float | None = None) -> CalcResult:
    """Run-out flow (GPM) where the pump head falls to zero."""
    p = _as_pump(pump)
    speed = _speed(speed_pct, p)
    q = m3s_to_gpm(max_flow_si(p, speed))
    return CalcResult(
        calc="max_flow",
        value=q,
        unit="GPM",
        inputs=_inputs(p, 0, speed),
        formula="H(Q_max, s) = 0 (bisection)",
        steps=[f"Q_max = {q:.1f} GPM"],
        citations=[CIT_PUMP_CURVE, CIT_DRC],
    )


def shaft_power(pump, flow_gpm: float, speed_pct: float | None = None, water_temp_f: float = 68.0) -> CalcResult:
    """Shaft (brake) power (hp) at a flow and speed: rho g Q H / eta, held at eta = 5% near shut-off."""
    p = _as_pump(pump)
    speed = _speed(speed_pct, p)
    w = water_si(f_to_c(float(water_temp_f)))
    q = gpm_to_m3s(float(flow_gpm))
    watts = shaft_power_si(p, q, speed, w)
    e = pump_eff_si(p, q, speed)
    return CalcResult(
        calc="shaft_power",
        value=watts / W_PER_HP,
        unit="hp",
        inputs={**_inputs(p, flow_gpm, speed), "water_temp": make_input(float(water_temp_f), "degF", "user")},
        formula="P = rho g Q H / max(eta, 0.05)",
        steps=[
            f"H = {pump_head_si(p, q, speed) / M_PER_FT:.2f} ft ; eta = {e * 100:.1f}%",
            f"P = {watts:.0f} W = {watts / W_PER_HP:.3f} hp ; motor input at {p.motor_eff_pct:g}% = "
            f"{watts / max(p.motor_eff_pct / 100, 0.1) / 1000:.2f} kW",
        ],
        citations=[CIT_PUMP_CURVE, CIT_DRC],
        warnings=_curve_warnings(p, float(flow_gpm), speed),
    )


def affinity(flow_gpm: float, head_ft: float, power_hp: float, speed_ratio: float) -> CalcResult:
    """A duty point moved to another speed by the affinity laws (Q ~ N, H ~ N^2, P ~ N^3)."""
    r = float(speed_ratio)
    q, h, pw = affinity_si(float(flow_gpm), float(head_ft), float(power_hp), r)
    warnings = []
    if r < 0.6:
        warnings.append("Below about 60% speed the efficiency is no longer speed-invariant; treat the result as indicative.")
    return CalcResult(
        calc="affinity",
        value={"flow_gpm": q, "head_ft": h, "power_hp": pw},
        unit="",
        inputs={
            "flow": make_input(float(flow_gpm), "GPM", "prior_calc"),
            "head": make_input(float(head_ft), "ft", "prior_calc"),
            "power": make_input(float(power_hp), "hp", "prior_calc"),
            "speed_ratio": make_input(r, "N2/N1", "user"),
        },
        formula="Q2 = Q1 r ; H2 = H1 r^2 ; P2 = P1 r^3",
        steps=[f"r = {r:g}: {q:.1f} GPM, {h:.2f} ft, {pw:.3f} hp"],
        citations=[CIT_AFFINITY],
        warnings=warnings,
    )
