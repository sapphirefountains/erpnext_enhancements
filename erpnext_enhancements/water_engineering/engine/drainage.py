"""Gravity drainage (Manning's) + surge-basin sizing (Phase 3).

Verified against DOC-0049 ``10 - Gravity`` / ``G - Gravity`` / ``B - Surge Basin``:

* Manning's gravity-drain capacity:
    Q_gpm = A * (1.486/n) * R^(2/3) * S^(1/2) * 7.48 * 60
  with A = half-full area = 3.14·D²/8/144, R = (D/4)/12 (ft), S = slope ft/ft
  (= inches-per-foot / 12). DOC-0049 is the conservative authority (NOT DOC-0119).
* Surge-basin depth/volume from evaporation, precipitation, vortex, freeboard,
  overflow, and swimmer-displacement components.
"""

from __future__ import annotations

from .constants import (
    BODY_SPECIFIC_GRAVITY,
    CIT_GRAVITY,
    DRAIN_AREA_PI,
    DRAIN_GAL_PER_CF,
    DRAIN_SLOPE_MAX_IN_FT,
    DRAIN_SLOPE_MIN_IN_FT,
    MANNING_CONSTANT,
    SURGE_EVAP_IN_DAY,
    SURGE_FREEBOARD_IN,
    SURGE_OVERFLOW_IN,
    SURGE_PRECIP_IN,
    SURGE_VORTEX_IN,
    SWIMMER_WEIGHT_LB,
    WATER_LB_PER_CF,
)
from .data.drainage import GRAVITY_PIPES
from .envelope import CalcOption, CalcResult, make_input

CIT_SURGE = "DOC-0049 / B - Surge Basin"


def _half_full_area(id_in: float) -> float:
    return DRAIN_AREA_PI * id_in**2 / 8 / 144  # sq ft (half-full, literal 3.14)


def _hydraulic_radius(id_in: float) -> float:
    return (id_in / 4) / 12  # ft


def _drain_flow(id_in: float, n: float, slope_ft_ft: float) -> float:
    area = _half_full_area(id_in)
    r = _hydraulic_radius(id_in)
    return area * (MANNING_CONSTANT / n) * r ** (2 / 3) * slope_ft_ft**0.5 * DRAIN_GAL_PER_CF * 60


def manning_drain_flow(nominal_size: str, slope_in_per_ft: float = 0.25) -> CalcResult:
    """Gravity-drain capacity (GPM) for a Sch40 PVC drain at a given slope."""
    spec = GRAVITY_PIPES.get(nominal_size)
    if not spec:
        return CalcResult(
            calc="manning_drain_flow",
            unit="GPM",
            inputs={"nominal_size": make_input(nominal_size, "", "user")},
            citations=[CIT_GRAVITY],
            warnings=[f"Unknown drain size {nominal_size!r}. Use one of {list(GRAVITY_PIPES)}."],
        )
    id_in, n = spec["id_in"], spec["n"]
    slope = float(slope_in_per_ft) / 12.0
    q = _drain_flow(id_in, n, slope)
    velocity = 0.4085 * 2 * q / id_in**2
    warnings = []
    if float(slope_in_per_ft) and not (
        DRAIN_SLOPE_MIN_IN_FT <= float(slope_in_per_ft) <= DRAIN_SLOPE_MAX_IN_FT
    ):
        warnings.append(
            f"Drain slope {float(slope_in_per_ft):g} in/ft is outside the "
            f"{DRAIN_SLOPE_MIN_IN_FT:g}-{DRAIN_SLOPE_MAX_IN_FT:g} in/ft gravity-drainage band "
            "(DOC-0119 / IPC) — too flat silts, too steep outruns its venting."
        )
    return CalcResult(
        calc="manning_drain_flow",
        value=q,
        unit="GPM",
        inputs={
            "nominal_size": make_input(nominal_size, "", "user"),
            "id": make_input(id_in, "in", "lookup", "GravityPipes"),
            "n": make_input(n, "", "lookup", "GravityPipes"),
            "slope": make_input(slope_in_per_ft, "in/ft", "user"),
        },
        formula="Q = A*(1.486/n)*R^(2/3)*S^(1/2)*7.48*60  [A=3.14*D^2/8/144, R=(D/4)/12, S=in_ft/12]",
        steps=[
            f"A = 3.14*{id_in}^2/8/144 = {_half_full_area(id_in):.6f} ft^2",
            f"R = ({id_in}/4)/12 = {_hydraulic_radius(id_in):.6f} ft ; S = {slope_in_per_ft}/12 = {slope:.6f}",
            f"Q = {q:.4f} GPM ; velocity = {velocity:.3f} FPS",
            # Divergence note (DOC-0119 tables are full-pipe): keep the conservative
            # half-full figure as the authority, but show the full-pipe basis so the
            # guideline tables don't look "wrong" next to the engine.
            f"full-pipe basis (same R, 2x area) = {2 * q:.1f} GPM — DOC-0119's drainage "
            "tables are full-pipe and read higher; this half-full figure is the "
            "conservative authority (DOC-0049)",
        ],
        citations=[CIT_GRAVITY],
        warnings=warnings,
    )


def size_drain(required_gpm: float, slope_in_per_ft: float = 0.25) -> CalcResult:
    """Smallest gravity drain that carries the required GPM at the given slope.
    Every size's capacity is returned in ``options``."""
    required_gpm = float(required_gpm)
    slope = float(slope_in_per_ft) / 12.0
    options: list[CalcOption] = []
    recommended = None
    for size, spec in GRAVITY_PIPES.items():
        cap = _drain_flow(spec["id_in"], spec["n"], slope)
        ok = cap >= required_gpm
        is_first_ok = recommended is None and ok
        if is_first_ok:
            recommended = size
        options.append(
            CalcOption(
                key=size,
                label=f"{size} Sch40 PVC",
                value=size,
                recommended=is_first_ok,
                detail={"capacity_gpm": round(cap, 2)},
            )
        )
    warnings = []
    if recommended is None:
        warnings.append(
            f"No listed drain carries {required_gpm} GPM at {slope_in_per_ft} in/ft; "
            "steepen the slope or use multiple drains."
        )
    return CalcResult(
        calc="size_drain",
        value=recommended,
        unit="nominal size",
        inputs={
            "required_gpm": make_input(required_gpm, "GPM", "user"),
            "slope": make_input(slope_in_per_ft, "in/ft", "user"),
        },
        formula="smallest size where Manning's capacity >= required_gpm",
        steps=[f"{o.label}: {o.detail['capacity_gpm']} GPM" for o in options],
        citations=[CIT_GRAVITY],
        options=options,
        warnings=warnings,
    )


def surge_basin_volume(
    pool_area_sf: float,
    basin_area_sf: float,
    *,
    evap_in_day: float = SURGE_EVAP_IN_DAY,
    precip_in: float = SURGE_PRECIP_IN,
    vortex_in: float = SURGE_VORTEX_IN,
    freeboard_in: float = SURGE_FREEBOARD_IN,
    overflow_in: float = SURGE_OVERFLOW_IN,
    swimmers: int = 0,
) -> CalcResult:
    """Surge/balancing-basin depth (in) and normal-operating volume (gal).

    Basin depth sums the operating allowances spread over the basin footprint:
    overflow + freeboard + swimmer-displacement + precipitation + evaporation +
    vortex. Precip/evap/displacement are pool-area volumes expressed as a height
    over the (usually smaller) basin area.
    """
    pool_area_sf = float(pool_area_sf)
    basin_area_sf = float(basin_area_sf)
    if basin_area_sf <= 0:
        return CalcResult(
            calc="surge_basin_volume",
            unit="gal",
            citations=[CIT_SURGE],
            warnings=["Basin surface area must be > 0."],
        )
    displacement_cf = swimmers * SWIMMER_WEIGHT_LB / (BODY_SPECIFIC_GRAVITY * WATER_LB_PER_CF)
    displacement_in = displacement_cf / basin_area_sf * 12
    precip_h = pool_area_sf * precip_in / basin_area_sf
    evap_h = pool_area_sf * evap_in_day / basin_area_sf
    total_in = overflow_in + freeboard_in + displacement_in + precip_h + evap_h + vortex_in
    gallons = (total_in / 12) * basin_area_sf * DRAIN_GAL_PER_CF
    return CalcResult(
        calc="surge_basin_volume",
        value=gallons,
        unit="gal",
        inputs={
            "pool_area": make_input(pool_area_sf, "sq ft", "user"),
            "basin_area": make_input(basin_area_sf, "sq ft", "user"),
            "evap": make_input(evap_in_day, "in/day", "user", "default 0.25"),
            "precip": make_input(precip_in, "in", "user", "default 1.0"),
            "vortex": make_input(vortex_in, "in", "user", "default 12 (6 if V<=1 fps)"),
            "freeboard": make_input(freeboard_in, "in", "user", "default 3"),
            "overflow": make_input(overflow_in, "in", "user", "default 3"),
            "swimmers": make_input(swimmers, "", "user"),
        },
        formula="depth_in = overflow + freeboard + displacement + precip + evap + vortex ; gal = depth/12 * basin_sf * 7.48",
        steps=[
            f"displacement = {displacement_in:.3f} in ; precip = {precip_h:.3f} in ; evap = {evap_h:.3f} in",
            f"depth = {overflow_in}+{freeboard_in}+{displacement_in:.3f}+{precip_h:.3f}+{evap_h:.3f}+{vortex_in} = {total_in:.3f} in",
            f"normal-op volume = ({total_in:.3f}/12)*{basin_area_sf:g}*7.48 = {gallons:.2f} gal",
        ],
        citations=[CIT_SURGE],
    )
