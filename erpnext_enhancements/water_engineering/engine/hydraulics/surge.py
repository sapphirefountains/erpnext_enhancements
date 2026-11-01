"""Water hammer: the pressure rise when flow stops, and the arrestor that absorbs it.

Port of Digital Rain Curtain ``src/hydro/transient.js`` ``supplyWave``, ``manifoldWave``,
``surgeFor``, ``accumulatorGasL`` and ``presets.js`` ``stdGasL``; golden file
``tests/golden/drc/surge.json`` (1e-12).

* **Wave speed** from the pipe itself (Korteweg, phase A's ``wave_speed_si``: bore, wall and
  modulus), not a table: 2" Sch 40 PVC comes out near 1,400 ft/s, hose near 100 ft/s.
* **The supply line** is lumped into one equivalent pipe: total length ``L``, the
  transit-time-weighted wave speed ``a = L / sum(L_i / a_i)`` and the length-weighted area.
  Only single runs count when there are any (two identical parallel branches split the flow,
  so the trunk carries the hammer). With no supply line the manifold is the trunk.
* **Joukowsky with the Michaud reduction**: ``dp = rho a dU``, scaled by ``(2L/a) / t_close``
  when the closure is slower than the pipe's round trip. The manifold is checked the same way
  and the larger governs.
* **The arrestor**: the gas volume that absorbs the trunk's kinetic energy
  ``E = 1/2 rho L A dU^2`` isothermally within the allowable rise,
  ``V0 = E / (p0 ln((p0 + dp_allow) / p0))``, then rounded up to a stock size.

**Absolute pressure at altitude.** DRC adds sea-level ``PATM`` to the gauge operating pressure.
The kernel takes ``atm_pa`` (DRC's value by default, which the goldens check); the envelope
passes the site's barometric pressure, which gives a slightly larger arrestor in Salt Lake City.

``engine/safety.water_hammer`` (a table wave speed, the same Joukowsky and slow-closure
scaling) stays as the simple cross-check and is shown beside the governing figure.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..envelope import CalcResult, make_input
from ..safety import water_hammer
from . import _js
from .manifold import Manifold
from .segment import Segment
from .units import CIT_DRC, M_PER_FT, PA_PER_PSI, PATM, f_to_c, gpm_to_m3s
from .water import CIT_KORTEWEG, Water, water_si, wave_speed_si

CIT_JOUKOWSKY = "Joukowsky (1898) dp = rho a dV; Michaud (1878) slow-closure reduction (2L/a)/tc (Wylie & Streeter ch. 1)"
CIT_ACCUMULATOR = "Accumulator sized from the line's kinetic energy, isothermal gas (Thorley, Fluid Transients in Pipeline Systems, ch. 9)"

GAS_SIZES_L = (0.1, 0.25, 0.5, 1, 2, 4, 8, 12, 19, 24, 35, 50, 80, 100)  # DRC presets.js stock arrestor gas volumes
L_PER_GAL = 3.785411784


@dataclass(frozen=True)
class Wave:
    """An equivalent pipe for the hammer: wave speed (m/s), length (m), area (m^2)."""

    a: float
    L: float
    A: float


def manifold_wave_si(m: Manifold, w: Water) -> Wave:
    """``manifoldWave``: the manifold pipe's own wave speed over the length the takeoffs span."""
    s = m.spec
    return Wave(wave_speed_si(w, s.id_m, s.wall_mm / 1000, s.e_gpa), m.span_m, s.area)


def supply_wave_si(segments, w: Water, fallback: Wave | None = None) -> Wave | None:
    """``supplyWave``: the supply line lumped into one pipe (``fallback`` when it has no length)."""
    segs = [s for s in segments or () if s.branches == 1] or list(segments or ())
    length, sum_area_len, sum_transit = 0.0, 0.0, 0.0
    for s in segs:
        ln = s.length_m or 0
        if ln <= 0:
            continue
        spec = s.spec
        a_i = wave_speed_si(w, spec.id_m, spec.wall_mm / 1000, spec.e_gpa)
        length += ln
        sum_area_len += ln * spec.area
        sum_transit += ln / a_i
    if length <= 0 or sum_transit <= 0:
        return fallback
    return Wave(length / sum_transit, length, sum_area_len / length)


def _joukowsky(w: Water, wave: Wave, dq: float, t_close_s: float) -> tuple[float, float, float]:
    du = dq / wave.A
    tw = (2 * wave.L) / wave.a
    return w.rho * wave.a * du * (tw / t_close_s if t_close_s > tw else 1), du, tw


def surge_for_si(dq: float, w: Water, t_close_s: float, supply=(), manifold: Manifold | None = None) -> dict:
    """``surgeFor``: the pressure rise (Pa) when ``dq`` (m^3/s) stops within ``t_close_s``."""
    zero = {"pa": 0.0, "supply_pa": 0.0, "manifold_pa": 0.0, "du": 0.0, "a": 0.0, "t_wave": 0.0, "slow": False}
    if not (dq > 0):
        return zero
    mf = manifold_wave_si(manifold, w) if manifold is not None else None
    tr = supply_wave_si(supply, w, mf)
    if tr is None:
        return zero
    pa_tr, du_tr, tw_tr = _joukowsky(w, tr, dq, t_close_s)
    pa_mf = _joukowsky(w, mf, dq, t_close_s)[0] if mf is not None else 0.0
    return {"pa": _js.jmax(pa_tr, pa_mf), "supply_pa": pa_tr, "manifold_pa": pa_mf, "du": du_tr, "a": tr.a,
            "t_wave": tw_tr, "slow": t_close_s > tw_tr, "L": tr.L, "A": tr.A}


def accumulator_gas_l_si(dq: float, p_op_pa: float, allow_pa: float, w: Water, supply=(), manifold: Manifold | None = None,
                         atm_pa: float = PATM) -> float:
    """``accumulatorGasL``: litres of gas that absorb the trunk's kinetic energy within ``allow_pa``."""
    if not (dq > 0) or not (allow_pa > 0):
        return 0.0
    mf = manifold_wave_si(manifold, w) if manifold is not None else None
    t = supply_wave_si(supply, w, mf)
    if t is None:
        return 0.0
    du = dq / t.A
    e = 0.5 * w.rho * t.L * t.A * du * du
    p0 = p_op_pa + atm_pa
    if p0 <= 0:
        return 0.0
    v0 = e / (p0 * math.log((p0 + allow_pa) / p0))
    return v0 * 1000


def ceil_to(x: float, step: float) -> float:
    """``ceilTo``: up to the next multiple of ``step``, as ``+(...).toFixed(3)``."""
    return _js.to_fixed(math.ceil(x / step - 1e-9) * step, 3)


def std_gas_l(need: float) -> float:
    """``stdGasL``: the smallest stock arrestor gas volume (L) at least ``need``; past 100 L, the next 10 L."""
    if not (need > 0):
        return 0
    for s in GAS_SIZES_L:
        if s >= need - 1e-9:
            return s
    return ceil_to(need, 10)


# ---------------------------------------------------------------------------
# Envelopes (US)
# ---------------------------------------------------------------------------


def _segments(rows) -> list:
    return [r if isinstance(r, Segment) else Segment.from_us(r) for r in rows or []]


def surge(
    flow_gpm: float,
    supply: list,
    *,
    closure_ms: float = 5.0,
    manifold: Manifold | None = None,
    operating_psi: float = 0.0,
    rating_psi: float | None = None,
    allow_psi: float | None = None,
    water_temp_f: float = 68.0,
    elevation_ft: float = 0.0,
) -> CalcResult:
    """The surge when ``flow_gpm`` stops in ``closure_ms``, the peak against the pipe rating, and the
    arrestor that keeps the rise within ``allow_psi`` (default: the rating less the operating pressure)."""
    from .operating_point import site_atmosphere

    w = water_si(f_to_c(float(water_temp_f)))
    segs = _segments(supply)
    dq = gpm_to_m3s(float(flow_gpm))
    tc = float(closure_ms) / 1000
    r = surge_for_si(dq, w, tc, segs, manifold)
    surge_psi = r["pa"] / PA_PER_PSI
    peak = float(operating_psi or 0) + surge_psi
    warnings: list[str] = []
    status = None
    if allow_psi is None and rating_psi:
        allow_psi = max(0.0, float(rating_psi) - float(operating_psi or 0))
    gas_l = need_l = 0.0
    atm_pa = site_atmosphere(float(elevation_ft or 0), float(water_temp_f))[0]
    if allow_psi is not None and surge_psi > allow_psi:
        need_l = accumulator_gas_l_si(dq, float(operating_psi or 0) * PA_PER_PSI, float(allow_psi) * PA_PER_PSI, w, segs, manifold, atm_pa)
        gas_l = std_gas_l(need_l)
        warnings.append(f"Surge {surge_psi:.1f} psi is over the {allow_psi:.1f} psi allowed: fit a {gas_l:g} L arrestor "
                        f"(needs {need_l:.2f} L of gas), or slow the closure past {r['t_wave'] * 1000:.0f} ms.")
    if rating_psi:
        status = "Okay" if peak <= float(rating_psi) else "Exceeds Pipe Rating"
        if peak > float(rating_psi) and not gas_l:
            warnings.append(f"Peak {peak:.0f} psi exceeds the {float(rating_psi):g} psi pipe rating.")
    # Cross-check: the workbook-style figure with a table wave speed.
    material = segs[0].spec.material if segs else "SCH40 PVC"
    cross = water_hammer(r["du"] / M_PER_FT, r.get("L", 0.0) / M_PER_FT, closure_time_s=tc, material=material,
                         static_psi=float(operating_psi or 0), pipe_rating_psi=float(rating_psi or 0))
    value = {
        "surge_psi": surge_psi, "supply_psi": r["supply_pa"] / PA_PER_PSI, "manifold_psi": r["manifold_pa"] / PA_PER_PSI,
        "wave_speed_fps": r["a"] / M_PER_FT, "velocity_change_fps": r["du"] / M_PER_FT, "round_trip_ms": r["t_wave"] * 1000,
        "slow_closure": r["slow"], "peak_psi": peak, "rating_psi": float(rating_psi) if rating_psi else None,
        "arrestor_gas_l": gas_l, "arrestor_need_l": need_l, "arrestor_gas_gal": gas_l / L_PER_GAL,
        "precharge_psi": 0.8 * float(operating_psi or 0) if gas_l else 0.0,
        "crosscheck_psi": cross.value,
    }
    return CalcResult(
        calc="surge",
        value=value,
        unit="",
        inputs={
            "flow": make_input(float(flow_gpm), "GPM", "prior_calc", "flow that stops"),
            "closure": make_input(float(closure_ms), "ms", "user"),
            "operating": make_input(float(operating_psi or 0), "psi", "prior_calc"),
            "rating": make_input(rating_psi, "psi", "lookup"),
            "water_temp": make_input(float(water_temp_f), "degF", "user"),
        },
        formula="dp = rho a dU x min(1, (2L/a)/tc) ; a = L / sum(L_i/a_i) ; V_gas = E / (p0 ln((p0 + dp_allow)/p0))",
        steps=[
            f"trunk: L = {r.get('L', 0) / M_PER_FT:.1f} ft, a = {value['wave_speed_fps']:.0f} ft/s, dU = {value['velocity_change_fps']:.2f} ft/s",
            f"2L/a = {value['round_trip_ms']:.1f} ms vs closure {closure_ms:g} ms -> {'slow (Michaud)' if r['slow'] else 'sudden'}",
            f"surge {surge_psi:.1f} psi (supply {value['supply_psi']:.1f}, manifold {value['manifold_psi']:.1f}); peak {peak:.1f} psi",
            f"cross-check (safety.water_hammer, table wave speed): {cross.value} psi",
        ],
        citations=[CIT_JOUKOWSKY, CIT_KORTEWEG, CIT_ACCUMULATOR, CIT_DRC],
        warnings=warnings,
        status=status,
    )
