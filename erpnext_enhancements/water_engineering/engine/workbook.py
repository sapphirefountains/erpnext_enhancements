"""Additional DOC-0049 workbook sheets: cost, vertical pipe, open channels,
lazy-river current, programmatic planning.

Each formula was extracted from the named sheet and reproduces that sheet's own
worked example (golden tests):

* :func:`electric_cost`     — E - Elec Costs  (pump energy -> $/yr)
* :func:`vertical_pipe`     — K - Vert Pipe   (standpipe discharge, 3 solve modes)
* :func:`open_channel_flow` — J - Channel     (runnel/rill Manning + regime)
* :func:`lazy_river_hp`     — L - Lazy        (current-generation horsepower)
* :func:`program_rules`     — D - Program     (occupancy / skimmer / solar roll-up)
"""

from __future__ import annotations

import math

from .constants import (
    CFS_TO_GPM,
    CIT_CHANNEL,
    CIT_ELEC,
    CIT_LAZY,
    CIT_PROGRAM,
    CIT_VERT_PIPE,
    DAYS_PER_YEAR,
    DEFAULT_CHANNEL_N,
    DEFAULT_KWH_RATE,
    DEFAULT_LAZY_RIVER_N,
    DEFAULT_MOTOR_EFF,
    DEFAULT_PUMP_EFF,
    DEFAULT_PUMP_HOURS_DAY,
    GAL_PER_CUBIC_FOOT_DRAIN,
    GRAVITY_FT_S2,
    HP_TO_KW,
    KINEMATIC_VISCOSITY_FT2_S,
    LAZY_RIVER_SAFETY_FACTOR,
    LIGHTING_WATTS_PER_SF,
    MANNING_CONSTANT,
    OVERFLOW_PIPE_GPM,
    PERIMETER_OVERFLOW_SF_THRESHOLD,
    RAIN_DESIGN_IN_HR,
    SF_PER_POOL_USER,
    SF_PER_SKIMMER,
    SF_PER_SPA_USER,
    SOLAR_PANEL_FRACTION,
    VERT_PIPE_COEFF,
    VERT_PIPE_K_FIXED,
    WHP_DIVISOR,
)
from .envelope import CalcResult, make_input


def lighting_design(surface_area_sf: float, pool_class: str = "residential") -> CalcResult:
    """Recommend total underwater-lighting wattage from the water-surface area and
    pool class, using the DOC-0049 D-Program watts/SF design bands. Returns the
    band midpoint as the headline; the low/high ends bracket the design range."""
    sa = float(surface_area_sf or 0)
    cls = (pool_class or "residential").strip().lower().replace(" ", "_")
    band = LIGHTING_WATTS_PER_SF.get(cls)
    if sa <= 0 or not band:
        return CalcResult(
            calc="lighting_design", unit="W",
            inputs={"pool_class": make_input(pool_class, "", "user")},
            citations=[CIT_PROGRAM],
            warnings=[
                f"Give a surface area > 0 and a pool class in {list(LIGHTING_WATTS_PER_SF)}."
            ],
        )
    lo, hi = band
    w_lo, w_hi, w_mid = sa * lo, sa * hi, sa * (lo + hi) / 2
    return CalcResult(
        calc="lighting_design", value=round(w_mid, 0), unit="W",
        inputs={
            "surface_area_sf": make_input(sa, "SF", "user"),
            "pool_class": make_input(cls, "", "user"),
            "watts_per_sf": make_input(f"{lo}-{hi}", "W/SF", "lookup", "D - Program B34:I39"),
        },
        formula="watts = surface_area_sf * watts_per_sf (band by pool class)",
        steps=[
            f"{cls}: {lo}-{hi} W/SF",
            f"range = {sa:g} * {lo}-{hi} = {w_lo:.0f}-{w_hi:.0f} W (midpoint {w_mid:.0f} W)",
        ],
        citations=[CIT_PROGRAM],
    )


def overflow_check(
    surface_area_sf: float,
    pipe_size: str | None = None,
    rain_in_hr: float = RAIN_DESIGN_IN_HR,
    runoff_fraction: float = 1.0,
) -> CalcResult:
    """Peak rainfall overflow (GPM) a basin must shed, and whether an overflow
    standpipe handles it. ``runoff_fraction`` 1.0 = full design rainfall
    (G - Gravity); 0.2 = the D - Program allowance. (DOC-0049 D/G.)"""
    sa = float(surface_area_sf or 0)
    rain = float(rain_in_hr or RAIN_DESIGN_IN_HR)
    frac = float(runoff_fraction or 1.0)
    if sa <= 0:
        return CalcResult(
            calc="overflow_check", unit="GPM", citations=[CIT_PROGRAM],
            warnings=["Surface area must be > 0."],
        )
    peak_gpm = sa * (rain / 12.0) * GAL_PER_CUBIC_FOOT_DRAIN / 60.0 * frac
    steps = [
        f"peak = {sa:g} SF * ({rain:g}/12 ft/hr) * 7.48 gal/cf / 60"
        + (f" * {frac:g}" if frac != 1.0 else "")
        + f" = {peak_gpm:.2f} GPM",
    ]
    warnings: list[str] = []
    status = None
    if pipe_size:
        cap = OVERFLOW_PIPE_GPM.get(pipe_size)
        if cap is None:
            warnings.append(f"No overflow capacity on file for {pipe_size}; known: {list(OVERFLOW_PIPE_GPM)}.")
        else:
            status = "Okay" if cap >= peak_gpm else "Undersized"
            steps.append(f"{pipe_size} overflow capacity = {cap:g} GPM -> {status}")
            if status != "Okay":
                warnings.append(f"{pipe_size} overflow ({cap:g} GPM) < peak {peak_gpm:.1f} GPM — size up.")
    rec = next((sz for sz, cap in OVERFLOW_PIPE_GPM.items() if cap >= peak_gpm), None)
    if rec:
        steps.append(f"smallest overflow that handles {peak_gpm:.1f} GPM = {rec}")
    return CalcResult(
        calc="overflow_check", value=round(peak_gpm, 2), unit="GPM",
        inputs={
            "surface_area_sf": make_input(sa, "SF", "user"),
            "rain_in_hr": make_input(rain, "in/hr", "default", "DOC-0049 design 7.9"),
            "runoff_fraction": make_input(frac, "", "user", "1.0 full / 0.2 D-Program"),
            "pipe_size": make_input(pipe_size or "", "", "user"),
        },
        formula="peak_GPM = SA * (in_hr/12) * 7.48 / 60 * runoff_fraction",
        steps=steps,
        citations=[CIT_PROGRAM],
        status=status,
        warnings=warnings,
    )


def electric_cost(
    flow_gpm: float,
    tdh_ft: float,
    *,
    hours_per_day: float = DEFAULT_PUMP_HOURS_DAY,
    rate_per_kwh: float = DEFAULT_KWH_RATE,
    sg: float = 1.0,
    pump_eff: float = DEFAULT_PUMP_EFF,
    motor_eff: float = DEFAULT_MOTOR_EFF,
    pump_qty: int = 1,
) -> CalcResult:
    """Annual pump operating cost: WHP -> BHP -> electrical HP -> kW -> $."""
    q = float(flow_gpm)
    tdh = float(tdh_ft)
    pump_qty = max(1, int(pump_qty or 1))
    whp = sg * tdh * q / WHP_DIVISOR
    bhp = whp / pump_eff if pump_eff else 0.0
    ehp = bhp / motor_eff if motor_eff else 0.0
    kw = ehp * HP_TO_KW
    cost_hr = kw * rate_per_kwh
    cost_day = cost_hr * hours_per_day
    cost_yr = cost_day * DAYS_PER_YEAR * pump_qty
    return CalcResult(
        calc="electric_cost",
        value=round(cost_yr, 2),
        unit="$/yr",
        inputs={
            "flow_gpm": make_input(q, "GPM", "prior_calc", "design flow"),
            "tdh_ft": make_input(tdh, "ft", "prior_calc"),
            "hours_per_day": make_input(hours_per_day, "hr", "user"),
            "rate_per_kwh": make_input(rate_per_kwh, "$/kWh", "user"),
            "pump_eff": make_input(pump_eff, "", "default", "hydraulic eff"),
            "motor_eff": make_input(motor_eff, "", "default"),
            "pump_qty": make_input(pump_qty, "", "user"),
        },
        formula="WHP=SG*TDH*Q/3960; BHP=WHP/Nh; HP=BHP/Nm; KW=HP*0.7457; $=KW*rate*hrs",
        steps=[
            f"WHP = {sg:g}*{tdh:g}*{q:g}/3960 = {whp:.4f}",
            f"BHP = {whp:.4f}/{pump_eff:g} = {bhp:.4f}; HP = {bhp:.4f}/{motor_eff:g} = {ehp:.4f}",
            f"kW = {ehp:.4f}*0.7457 = {kw:.4f}",
            f"$/day = {kw:.4f}*{rate_per_kwh:g}*{hours_per_day:g} = {cost_day:.4f}",
            f"$/yr = {cost_day:.4f}*365" + (f"*{pump_qty}" if pump_qty > 1 else "") + f" = {cost_yr:.2f}",
        ],
        citations=[CIT_ELEC],
    )


def vertical_pipe(
    head_in: float = 0.0,
    id_in: float = 0.0,
    flow_gpm: float = 0.0,
) -> CalcResult:
    """Vertical-pipe / standpipe discharge (K - Vert Pipe), three solve modes:
    flow from head+ID, head from flow+ID, or recommend an ID from head+flow."""
    head_in = float(head_in or 0)
    id_in = float(id_in or 0)
    flow_gpm = float(flow_gpm or 0)

    if head_in > 0 and id_in > 0:  # mode A: solve flow
        k = 0.82 + 0.025 * id_in
        q = VERT_PIPE_COEFF * math.sqrt(head_in) * k * id_in**2
        warn = ["Head above the standpipe lip is capped at 24 in on the source sheet."] if head_in > 24 else []
        return CalcResult(
            calc="vertical_pipe",
            value=round(q, 2),
            unit="GPM",
            inputs={
                "head_in": make_input(head_in, "in", "user"),
                "id_in": make_input(id_in, "in", "user", "pipe inside dia"),
            },
            formula="Q = 5.68 * H^0.5 * K * ID^2 ; K = 0.82 + 0.025*ID",
            steps=[
                f"K = 0.82 + 0.025*{id_in:g} = {k:.4f}",
                f"Q = 5.68*sqrt({head_in:g})*{k:.4f}*{id_in:g}^2 = {q:.2f} GPM",
            ],
            citations=[CIT_VERT_PIPE],
            warnings=warn,
        )
    if flow_gpm > 0 and id_in > 0:  # mode B: solve head
        k = 0.82 + 0.025 * id_in
        h = (flow_gpm / (VERT_PIPE_COEFF * k * id_in**2)) ** 2
        return CalcResult(
            calc="vertical_pipe",
            value=round(h, 2),
            unit="in head",
            inputs={
                "flow_gpm": make_input(flow_gpm, "GPM", "user"),
                "id_in": make_input(id_in, "in", "user"),
            },
            formula="H = (Q / (5.68 * K * ID^2))^2 ; K = 0.82 + 0.025*ID",
            steps=[
                f"K = 0.82 + 0.025*{id_in:g} = {k:.4f}",
                f"H = ({flow_gpm:g}/(5.68*{k:.4f}*{id_in:g}^2))^2 = {h:.2f} in",
            ],
            citations=[CIT_VERT_PIPE],
        )
    if flow_gpm > 0 and head_in > 0:  # mode C: recommend ID (fixed K=0.92)
        rec_id = math.sqrt(flow_gpm / (VERT_PIPE_COEFF * math.sqrt(head_in) * VERT_PIPE_K_FIXED))
        return CalcResult(
            calc="vertical_pipe",
            value=round(rec_id, 2),
            unit="in ID",
            inputs={
                "flow_gpm": make_input(flow_gpm, "GPM", "user"),
                "head_in": make_input(head_in, "in", "user"),
            },
            formula="ID = (Q / (5.68 * H^0.5 * 0.92))^0.5",
            steps=[f"ID = ({flow_gpm:g}/(5.68*sqrt({head_in:g})*0.92))^0.5 = {rec_id:.2f} in"],
            citations=[CIT_VERT_PIPE],
            warnings=["Recommended inside diameter — round up to the next standard pipe size."],
        )
    return CalcResult(
        calc="vertical_pipe",
        unit="GPM",
        citations=[CIT_VERT_PIPE],
        warnings=["Provide head+ID (for flow), flow+ID (for head), or flow+head (to size the pipe)."],
    )


def _flow_regime(velocity_fps: float, hydraulic_depth_ft: float, hydraulic_radius_ft: float) -> tuple[float, str, float, str]:
    """Froude (+tranquil/critical/shooting) and Reynolds (+laminar/turbulent)."""
    fr = velocity_fps / math.sqrt(GRAVITY_FT_S2 * hydraulic_depth_ft) if hydraulic_depth_ft > 0 else 0.0
    froude = "critical" if abs(fr - 1) < 0.05 else ("subcritical (tranquil)" if fr < 1 else "supercritical (shooting)")
    re = velocity_fps * hydraulic_radius_ft / KINEMATIC_VISCOSITY_FT2_S
    reynolds = "laminar" if re < 500 else ("transitional" if re < 2000 else "turbulent")
    return fr, froude, re, reynolds


def open_channel_flow(
    width_in: float,
    depth_in: float,
    slope: float,
    n: float = DEFAULT_CHANNEL_N,
) -> CalcResult:
    """Rectangular open-channel (runnel/rill) flow via Manning, with Froude/
    Reynolds regime. ``slope`` is rise/run (ft/ft); ``n`` is Manning roughness."""
    b = float(width_in)
    d = float(depth_in)
    s = float(slope)
    n = float(n) or DEFAULT_CHANNEL_N
    if b <= 0 or d <= 0 or s <= 0:
        return CalcResult(
            calc="open_channel_flow",
            unit="GPM",
            citations=[CIT_CHANNEL],
            warnings=["Width, depth, and slope must all be > 0."],
        )
    area = b * d / 144.0  # SF
    perimeter = (b + 2 * d) / 12.0  # ft
    r = area / perimeter
    q_cfs = MANNING_CONSTANT * area * r ** (2 / 3) * math.sqrt(s) / n
    q_gpm = q_cfs * CFS_TO_GPM
    v = q_cfs / area if area else 0.0
    d_hyd = area / (b / 12.0)  # hydraulic depth (ft)
    fr, froude, re, reynolds = _flow_regime(v, d_hyd, r)
    return CalcResult(
        calc="open_channel_flow",
        value=round(q_gpm, 2),
        unit="GPM",
        inputs={
            "width_in": make_input(b, "in", "user"),
            "depth_in": make_input(d, "in", "user", "flow depth"),
            "slope": make_input(s, "ft/ft", "user"),
            "n": make_input(n, "", "user", "Manning roughness"),
        },
        formula="Q = 1.486 * A * R^(2/3) * S^0.5 / n  (A=b*d/144, R=A/P, P=(b+2d)/12)",
        steps=[
            f"A = {b:g}*{d:g}/144 = {area:.4f} SF; P = ({b:g}+2*{d:g})/12 = {perimeter:.4f} ft; R = {r:.4f} ft",
            f"Q = 1.486*{area:.4f}*{r:.4f}^(2/3)*sqrt({s:g})/{n:g} = {q_cfs:.4f} CFS = {q_gpm:.2f} GPM",
            f"V = {v:.3f} ft/s; Froude {fr:.3f} ({froude}); Reynolds {re:.0f} ({reynolds})",
        ],
        citations=[CIT_CHANNEL],
        status=froude,
    )


def lazy_river_hp(
    width_ft: float,
    depth_ft: float,
    length_ft: float,
    velocity_fps: float = 5.0,
    n: float = DEFAULT_LAZY_RIVER_N,
    sg: float = 1.0,
    safety_factor: float = LAZY_RIVER_SAFETY_FACTOR,
) -> CalcResult:
    """Lazy-river current-generation design horsepower: Manning slope to sustain
    ``velocity_fps`` -> friction head over the loop -> water HP * safety factor."""
    w = float(width_ft)
    d = float(depth_ft)
    length_ft = float(length_ft)
    v = float(velocity_fps)
    n = float(n) or DEFAULT_LAZY_RIVER_N
    if w <= 0 or d <= 0 or length_ft <= 0 or v <= 0:
        return CalcResult(
            calc="lazy_river_hp",
            unit="HP",
            citations=[CIT_LAZY],
            warnings=["Width, depth, length, and target velocity must all be > 0."],
        )
    area = w * d  # SF
    perimeter = 2 * d + w  # ft
    r = area / perimeter
    slope = (v * n / (MANNING_CONSTANT * r ** (2 / 3))) ** 2
    hf = slope * length_ft  # ft
    q_cfs = v * area
    q_gpm = q_cfs * CFS_TO_GPM
    whp = q_gpm * hf * sg / WHP_DIVISOR
    design_whp = whp * safety_factor
    d_hyd = area / w
    fr, froude, re, reynolds = _flow_regime(v, d_hyd, r)
    return CalcResult(
        calc="lazy_river_hp",
        value=round(design_whp, 3),
        unit="HP (design)",
        inputs={
            "width_ft": make_input(w, "ft", "user"),
            "depth_ft": make_input(d, "ft", "user"),
            "length_ft": make_input(length_ft, "ft", "user", "loop length"),
            "velocity_fps": make_input(v, "ft/s", "user", "target current"),
            "n": make_input(n, "", "user"),
            "safety_factor": make_input(safety_factor, "", "default"),
        },
        formula="S=(V*n/(1.486*R^(2/3)))^2; hf=S*L; WHP=Q*hf*SG/3960; design=WHP*SF",
        steps=[
            f"A = {w:g}*{d:g} = {area:.3f} SF; P = 2*{d:g}+{w:g} = {perimeter:.3f} ft; R = {r:.4f} ft",
            f"S = ({v:g}*{n:g}/(1.486*{r:.4f}^(2/3)))^2 = {slope:.6f}; hf = {slope:.6f}*{length_ft:g} = {hf:.4f} ft",
            f"Q = {v:g}*{area:.3f} = {q_cfs:.2f} CFS = {q_gpm:.0f} GPM",
            f"WHP = {q_gpm:.0f}*{hf:.4f}*{sg:g}/3960 = {whp:.4f}; design = *{safety_factor:g} = {design_whp:.3f} HP",
            f"Froude {fr:.3f} ({froude}); Reynolds {re:.0f} ({reynolds})",
        ],
        citations=[CIT_LAZY],
    )


def program_rules(surface_area_sf: float, pool_class: str = "pool") -> CalcResult:
    """D-sheet programmatic sub-rules from the water surface area: bather load,
    skimmer count, perimeter-overflow trigger, and minimum solar-panel area."""
    sa = float(surface_area_sf)
    cls = (pool_class or "pool").strip().lower()
    if sa <= 0:
        return CalcResult(
            calc="program_rules",
            unit="program",
            citations=[CIT_PROGRAM],
            warnings=["Surface area must be > 0."],
        )
    sf_per_user = SF_PER_SPA_USER if cls in ("spa", "wading") else SF_PER_POOL_USER
    capacity = int(sa // sf_per_user)
    skimmers = math.ceil(sa / SF_PER_SKIMMER)
    solar_sf = sa * SOLAR_PANEL_FRACTION
    needs_perimeter = sa > PERIMETER_OVERFLOW_SF_THRESHOLD
    return CalcResult(
        calc="program_rules",
        value=capacity,
        unit="bathers",
        inputs={
            "surface_area_sf": make_input(sa, "SF", "prior_calc"),
            "pool_class": make_input(cls, "", "user", "pool|spa|wading"),
        },
        formula="capacity=SA/sf_per_user; skimmers=ceil(SA/400); solar=0.8*SA; perimeter overflow if SA>5000",
        steps=[
            f"bather capacity = {sa:g}/{sf_per_user:g} = {capacity}",
            f"skimmers = ceil({sa:g}/400) = {skimmers}",
            f"min solar panel area = 0.8*{sa:g} = {solar_sf:.0f} SF",
            f"perimeter-overflow gutter {'required' if needs_perimeter else 'not required'} (SA {'>' if needs_perimeter else '<='} 5000 SF)",
        ],
        citations=[CIT_PROGRAM],
        warnings=["Perimeter overflow recommended above 5,000 SF."] if needs_perimeter else [],
    )
