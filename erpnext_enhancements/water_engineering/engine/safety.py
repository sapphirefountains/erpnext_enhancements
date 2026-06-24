"""Safety-critical checks: suction-outlet anti-entrapment, pump cavitation, surge.

Three gates that protect people and equipment:

* :func:`suction_outlet_vgb` — VGB / ANSI-APSP-16 drain-cover anti-entrapment.
  Verified verbatim against DOC-0049 ``P - Suction Outlets`` (the worked example
  reproduces to the cell). This is an engineering aid, NOT a substitute for a
  listed cover's stamped flow rating — that caveat is always in ``warnings``.
* :func:`npsh_available` — net positive suction head available vs. required, the
  cavitation go/no-go on pump placement (Hydraulic Institute; not in the docs).
* :func:`water_hammer` — Joukowsky surge pressure vs. pipe rating (not in docs).
"""

from __future__ import annotations

import math
from itertools import pairwise

from .constants import (
    ATM_PRESSURE_PSIA_SEA,
    CFS_TO_GPM,
    CIT_NPSH,
    CIT_VGB,
    CIT_WATER_HAMMER,
    FT_PER_PSI,
    GRAVITY_FT_S2,
    NPSH_DEFAULT_MARGIN_FT,
    VAPOR_PRESSURE_PSIA,
    VGB_BODY_BLOCK_LEN_IN,
    VGB_BODY_BLOCK_WID_IN,
    VGB_FLOW_COEFF,
    VGB_LIFT_LOAD_LBF,
    VGB_MAX_COVER_VELOCITY_FPS,
    VGB_WATER_DENSITY_SLUG,
    WAVE_SPEED_DEFAULT_FPS,
    WAVE_SPEED_FPS,
)
from .envelope import CalcResult, make_input


def suction_outlet_vgb(
    system_gpm: float,
    cover_length_in: float,
    cover_width_in: float,
    open_area_fraction: float,
    outlets: int = 1,
    vmax_fps: float = VGB_MAX_COVER_VELOCITY_FPS,
) -> CalcResult:
    """Anti-entrapment check for a suction-outlet (drain) cover.

    Computes the max flow a cover can carry without exceeding the body-blockage
    entrapment limit ``Q = AR*(F/(C*rho/2*AB))^0.5`` and the approach-velocity
    limit, then compares against the worst-case per-outlet flow (one outlet
    blocked). ``open_area_fraction`` is the grate's percent-open (e.g. 0.215).
    """
    system_gpm = float(system_gpm)
    cl = float(cover_length_in)
    cw = float(cover_width_in)
    pct = float(open_area_fraction)
    outlets = int(outlets or 1)
    warnings = [
        "Engineering aid only — confirm against the suction cover's listed/stamped "
        "GPM rating (floor vs. wall) and ANSI/APSP-16; covers are model-specific.",
    ]

    if cl <= 0 or cw <= 0 or pct <= 0:
        return CalcResult(
            calc="suction_outlet_vgb",
            unit="GPM",
            citations=[CIT_VGB],
            warnings=["Cover length, width, and open-area fraction must all be > 0."],
        )

    gross_area = cl * cw  # sq in
    # The largest area a body can block: the code 23x18 footprint, clipped to the
    # cover's actual size (a body can't block more than the cover itself).
    block_len = min(VGB_BODY_BLOCK_LEN_IN, cl)
    block_wid = min(VGB_BODY_BLOCK_WID_IN, cw)
    blocked_area = block_len * block_wid  # sq in
    remaining_area = max(gross_area - blocked_area, 0.0)  # sq in

    ab = blocked_area * pct / 144.0  # SF — open area within the blockable footprint
    ar = remaining_area * pct / 144.0  # SF — open area remaining unblocked
    if ab <= 0 or ar <= 0:
        return CalcResult(
            calc="suction_outlet_vgb",
            unit="GPM",
            citations=[CIT_VGB],
            warnings=["Cover too small relative to the body footprint to compute a margin."],
        )

    # Entrapment-limited flow (CFS -> GPM).
    q_cfs = ar * math.sqrt(VGB_LIFT_LOAD_LBF / (VGB_FLOW_COEFF * VGB_WATER_DENSITY_SLUG / 2 * ab))
    q_entrap_gpm = q_cfs * CFS_TO_GPM
    # Approach-velocity-limited flow through the open remaining area.
    q_vel_gpm = vmax_fps * ar * CFS_TO_GPM
    max_safe_gpm = min(q_entrap_gpm, q_vel_gpm)
    binding = "approach velocity" if q_vel_gpm <= q_entrap_gpm else "entrapment"

    # Worst case: with one outlet blocked the rest must carry the whole system.
    worst_per_outlet = system_gpm / max(1, outlets - 1) if outlets > 1 else system_gpm
    vact_fps = worst_per_outlet / (ar * CFS_TO_GPM) if ar else 0.0

    meets = worst_per_outlet <= max_safe_gpm
    status = "Okay" if meets else "Entrapment Risk — Resize"
    if not meets:
        warnings.append(
            f"Worst-case outlet flow {worst_per_outlet:.0f} GPM exceeds the cover's "
            f"safe {max_safe_gpm:.0f} GPM ({binding}-limited) — use a larger/higher-flow "
            "cover, add covers, or reduce flow."
        )
    if outlets < 2:
        status = "Add Second Drain" if meets else status
        warnings.append(
            "Single suction outlet — VGB requires it be field-fabricated unblockable "
            "OR backed by a second anti-entrapment system (SVRS, gravity drain, vent, "
            "or auto pump-off). Provide two outlets >3 ft apart, each rated for full flow."
        )

    return CalcResult(
        calc="suction_outlet_vgb",
        value=round(max_safe_gpm, 1),
        unit="GPM (max safe per outlet)",
        inputs={
            "system_gpm": make_input(system_gpm, "GPM", "prior_calc", "design circulation"),
            "cover_length_in": make_input(cl, "in", "user", "cover gross length"),
            "cover_width_in": make_input(cw, "in", "user", "cover gross width"),
            "open_area_fraction": make_input(pct, "fraction", "user", "grate % open"),
            "outlets": make_input(outlets, "", "user"),
            "F": make_input(VGB_LIFT_LOAD_LBF, "lbf", "standard", "ANSI/APSP-16 2.3.1.2"),
            "C": make_input(VGB_FLOW_COEFF, "", "standard", "ANSI/APSP-16 2.3.1.2"),
            "rho": make_input(VGB_WATER_DENSITY_SLUG, "slug/ft^3", "standard"),
            "vmax": make_input(vmax_fps, "ft/s", "standard", "max approach velocity"),
        },
        formula="Q_cfs = AR*(F/(C*rho/2*AB))^0.5 ; AB,AR = blocked/remaining open area (SF)",
        steps=[
            f"gross = {cl:g}*{cw:g} = {gross_area:.0f} in^2; blocked = {block_len:g}*{block_wid:g} = {blocked_area:.0f} in^2",
            f"AB = {blocked_area:.0f}*{pct:g}/144 = {ab:.4f} SF; AR = {remaining_area:.0f}*{pct:g}/144 = {ar:.4f} SF",
            f"entrapment Q = {ar:.4f}*sqrt({VGB_LIFT_LOAD_LBF:g}/({VGB_FLOW_COEFF:g}*{VGB_WATER_DENSITY_SLUG:g}/2*{ab:.4f})) = {q_cfs:.3f} CFS = {q_entrap_gpm:.0f} GPM",
            f"velocity limit = {vmax_fps:g}*{ar:.4f}*{CFS_TO_GPM:g} = {q_vel_gpm:.0f} GPM",
            f"max safe = min = {max_safe_gpm:.0f} GPM ({binding}-limited)",
            f"worst-case per outlet (1 blocked) = {worst_per_outlet:.0f} GPM; approach V = {vact_fps:.3f} ft/s -> {status}",
        ],
        citations=[CIT_VGB],
        status=status,
        warnings=warnings,
    )


def _vapor_pressure_psia(temp_f: float) -> float:
    """Saturated water-vapor pressure (psia) at a temperature, linearly
    interpolated between the tabulated points (clamped at the ends)."""
    pts = sorted(VAPOR_PRESSURE_PSIA.items())
    t = float(temp_f)
    if t <= pts[0][0]:
        return pts[0][1]
    if t >= pts[-1][0]:
        return pts[-1][1]
    for (t0, p0), (t1, p1) in pairwise(pts):
        if t0 <= t <= t1:
            return p0 + (p1 - p0) * ((t - t0) / (t1 - t0))
    return pts[-1][1]


def _atm_head_ft(elevation_ft: float) -> float:
    """Atmospheric pressure head (ft of water) at an elevation, via the standard
    barometric lapse, then psia -> ft."""
    elevation_ft = float(elevation_ft)
    psia = ATM_PRESSURE_PSIA_SEA * (1.0 - 6.8753e-6 * elevation_ft) ** 5.2559
    return psia * FT_PER_PSI


def npsh_available(
    suction_static_ft: float,
    suction_friction_ft: float,
    *,
    elevation_ft: float = 0.0,
    water_temp_f: float = 70.0,
    npshr_ft: float = 0.0,
    margin_ft: float = NPSH_DEFAULT_MARGIN_FT,
) -> CalcResult:
    """Net positive suction head available (ft) and the cavitation go/no-go.

    ``suction_static_ft`` is signed: positive when the water surface is ABOVE the
    pump centerline (flooded suction), negative for a suction LIFT. If the pump's
    required NPSH (``npshr_ft``) is given, returns a pass/marginal/fail status.
    """
    hz = float(suction_static_ft)
    hf = abs(float(suction_friction_ft))
    ha = _atm_head_ft(elevation_ft)
    pvp_psia = _vapor_pressure_psia(water_temp_f)
    hvp = pvp_psia * FT_PER_PSI
    npsha = ha + hz - hf - hvp

    npshr = float(npshr_ft or 0)
    margin = float(margin_ft)
    status = None
    warnings = []
    if npshr > 0:
        if npsha >= npshr + margin:
            status = "Okay"
        elif npsha >= npshr:
            status = "Marginal"
            warnings.append(
                f"NPSHa {npsha:.2f} ft clears NPSHr {npshr:.2f} ft but not the "
                f"{margin:.0f} ft safety margin — tighten suction piping or lower the pump."
            )
        else:
            status = "Cavitation Risk"
            warnings.append(
                f"NPSHa {npsha:.2f} ft is below the pump's NPSHr {npshr:.2f} ft — the pump "
                "will cavitate. Flood the suction, shorten/enlarge suction pipe, or cool the water."
            )
    else:
        warnings.append("No pump NPSHr supplied — NPSHa computed but not checked. Enter the pump-curve NPSHr to gate it.")

    return CalcResult(
        calc="npsh_available",
        value=round(npsha, 2),
        unit="ft",
        inputs={
            "suction_static_ft": make_input(hz, "ft", "user", "+flooded / -lift"),
            "suction_friction_ft": make_input(hf, "ft", "prior_calc", "suction-side TDH loss"),
            "elevation_ft": make_input(elevation_ft, "ft", "user", "site altitude"),
            "water_temp_f": make_input(water_temp_f, "degF", "user"),
            "npshr_ft": make_input(npshr, "ft", "lookup", "pump curve"),
            "margin_ft": make_input(margin, "ft", "standard", "HI margin 2-3 ft"),
        },
        formula="NPSHa = Ha + Hz - Hf - Hvp",
        steps=[
            f"Ha (atm head @ {elevation_ft:g} ft) = {ha:.2f} ft",
            f"Hvp (vapor head @ {water_temp_f:g} F) = {pvp_psia:.3f} psia * {FT_PER_PSI} = {hvp:.2f} ft",
            f"NPSHa = {ha:.2f} + ({hz:g}) - {hf:g} - {hvp:.2f} = {npsha:.2f} ft",
        ]
        + ([f"vs NPSHr {npshr:.2f} + margin {margin:.0f} = {npshr + margin:.2f} ft -> {status}"] if npshr > 0 else []),
        citations=[CIT_NPSH],
        status=status,
        warnings=warnings,
    )


def water_hammer(
    velocity_fps: float,
    length_ft: float,
    *,
    closure_time_s: float = 0.0,
    material: str = "SCH40 PVC",
    wave_speed_fps: float = 0.0,
    static_psi: float = 0.0,
    pipe_rating_psi: float = 0.0,
) -> CalcResult:
    """Water-hammer (Joukowsky) surge pressure from a valve closure, and the
    peak (static + surge) vs. the pipe pressure rating.

    A sudden stop of ``velocity_fps`` over a pipe of ``length_ft`` produces
    ``dH = a*dV/g``. If ``closure_time_s`` exceeds the pipe reflection period
    ``2L/a`` the surge is scaled down linearly (slow closure)."""
    dv = abs(float(velocity_fps))
    length_ft = float(length_ft)
    a = float(wave_speed_fps) or WAVE_SPEED_FPS.get((material or "").strip().upper(), WAVE_SPEED_DEFAULT_FPS)
    tc = float(closure_time_s or 0)
    static_psi = float(static_psi or 0)
    rating = float(pipe_rating_psi or 0)
    warnings = []

    surge_head_full = a * dv / GRAVITY_FT_S2
    surge_psi_full = surge_head_full / FT_PER_PSI
    t_crit = (2 * length_ft / a) if a else 0.0  # pipe reflection period
    critical = tc <= t_crit
    if critical or tc <= 0:
        surge_psi = surge_psi_full
        regime = "instantaneous (full Joukowsky)"
    else:
        surge_psi = surge_psi_full * (t_crit / tc)
        regime = f"slow closure (scaled by 2L/a / tc = {t_crit:.3f}/{tc:g})"

    peak_psi = static_psi + surge_psi
    status = None
    if rating > 0:
        status = "Okay" if peak_psi <= rating else "Exceeds Pipe Rating"
        if peak_psi > rating:
            warnings.append(
                f"Peak {peak_psi:.0f} psi exceeds the pipe's {rating:.0f} psi rating — "
                "slow the valve closure, add a surge arrestor/accumulator, or up-rate the pipe."
            )
    if tc <= 0:
        warnings.append("No closure time supplied — assumed instantaneous (worst case).")

    return CalcResult(
        calc="water_hammer",
        value=round(surge_psi, 1),
        unit="psi (surge)",
        inputs={
            "velocity_fps": make_input(dv, "ft/s", "prior_calc", "line velocity / dV"),
            "length_ft": make_input(length_ft, "ft", "user", "pipe run to the valve"),
            "closure_time_s": make_input(tc, "s", "user"),
            "wave_speed_fps": make_input(a, "ft/s", "standard", material),
            "static_psi": make_input(static_psi, "psi", "user", "operating pressure"),
            "pipe_rating_psi": make_input(rating, "psi", "lookup", "pipe schedule rating"),
        },
        formula="dP = rho*a*dV  ->  surge_psi = (a*dV/g)/2.31 ; scaled by 2L/a if slow",
        steps=[
            f"wave speed a = {a:g} ft/s ({material})",
            f"full surge = ({a:g}*{dv:g}/{GRAVITY_FT_S2:g})/{FT_PER_PSI} = {surge_psi_full:.1f} psi",
            f"reflection period 2L/a = {t_crit:.3f} s -> {regime}",
            f"surge = {surge_psi:.1f} psi; peak = {static_psi:g} + {surge_psi:.1f} = {peak_psi:.1f} psi"
            + (f" vs rating {rating:g} -> {status}" if rating > 0 else ""),
        ],
        citations=[CIT_WATER_HAMMER],
        status=status,
        warnings=warnings,
    )
