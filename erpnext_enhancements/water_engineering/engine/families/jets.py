"""Nozzle jets: single orifices, arrays, rings and geysers. Also the shared jet physics the
foam, laminar and interactive families call.

Industry report section 1.2 and engine audit section 1.8. The headline change from
``feature.jet_trajectory``: **height comes from the nozzle's performance table** (display
height -> flow and head, by linear interpolation between the cut sheet's points). The old
``k x head`` rule survives only as a flagged fallback, with sourced k values that are far
lower than the engine's 0.90: a smooth bore needs about 1.22 x the height in head, and a real
decorative column nozzle 2.5-3 x (Atlantic: 12 ft at 31 ft of head).

The trajectory is a 2-D point mass with quadratic air drag, integrated by RK4, with a
horizontal wind. It is used for range, landing point and wind drift, and is calibrated so its
still-air apex equals the table (or estimated) height: the table says how high, the physics
says where it lands. The drag diameter is the Rayleigh drop size, 1.89 x the jet diameter,
the same breakup model the rain-curtain app uses. This module is stdlib-only and does not
import ``engine.hydraulics``.

``derived`` keys read by :func:`checks`: ``flow``, ``basin`` (a jet centered in it gets half
the shorter side as its edge distance when none is entered) and ``manifold_spread_pct`` (the
network solver's pressure spread across an array, when it has run).
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

from .. import rules as R
from ..constants import FT_PER_PSI
from ..envelope import make_input
from ..feature import nozzle_flow
from .base import (
    BLOCKER,
    G_FT_S2,
    INFO,
    WARNING,
    Check,
    FamilyResult,
    basin_plan,
    count_of,
    feature_dict,
    flow_data,
    num,
    product_of,
    site_regulated,
    spec,
    values,
)

FAMILY = "jets"
LABEL = "Nozzle jets and arrays"
TYPES = ("Nozzle Array", "Orifice Nozzle", "Jet Ring", "Geyser", "Single Jet", "Arching Jet")

CIT_ATLANTIC = (
    "https://practicalgardenponds.com/products/atlantic-water-gardens-vertical-nozzles "
    "(column nozzle: 2 ft at 6.5 ft of head, 12 ft at 31 ft)"
)
CIT_SMOOTH = "https://www.scribd.com/document/462312854/elementry-pdf (smooth bore: head ~1.22 x height) [weak]"
CIT_TABLES = (
    "https://cdn.shopify.com/s/files/1/2394/4493/files/Oase_Geyser_Nozzles_Specifications_Sheet_PDF.pdf "
    "(manufacturer tables: height -> GPM, TDH)"
)
CIT_DRAG = (
    "Quadratic drag on a sphere, Cd 0.47 (textbook); drop size 1.89 x jet diameter (Rayleigh, "
    "digital-rain-curtain src/hydro/checks.js jetChecks); standard atmosphere for air density"
)
CIT_SPLASH = (
    "https://saferain.com/en/blog/tips-of-fountain-design-part-2/ (splash radius ~ jet height); "
    "https://www.deltafountains.com/?p=772 (height <= distance to the nearest edge); "
    "https://www.slideshare.net/slideshow/water-feature-types/5145948 (Crystal: 1:1 ground pools, 1:1.5 raised tables)"
)
CIT_SPREAD = (
    "https://saferain.com/en/blog/tips-of-fountain-design-part-2/ (equal head loss to every nozzle); "
    "digital-rain-curtain manifold sizeSweep (+/-2% spread)"
)
CIT_IWF = "https://www.law.cornell.edu/regulations/utah/Utah-Admin-Code-R392-302-41 (nozzle velocity <= 20 ft/s)"
CIT_WIND = (
    "https://landscapearchitect.com/ladetails/landscape-product/fountains-equipment/roman-fountains-corp./rwc-sm-dul-wind-control "
    "(two-stage anemometer control, 0-25 mph)"
)
CIT_ORIFICE_CD = "Smooth-bore nozzle Cd 0.96 (textbook); a placeholder until a Nozzle Profile is picked"

# k = display height / supply head, used ONLY when no performance table exists.
FALLBACK_K = {"smooth": 0.82, "aerating": 0.39, "other": 0.39}
FALLBACK_K_CITATION = {
    "smooth": CIT_SMOOTH,
    "aerating": CIT_ATLANTIC,
    "other": CIT_ATLANTIC + "; a column nozzle: no public figure exists for cluster or spray nozzles, so it stands in",
}
FALLBACK_CD = 0.96
NOZZLE_TYPES = ("smooth", "aerating", "column", "cluster", "spray")

SPLASH_RATIO = {"ground": 1.0, "raised": 1.5}
MANIFOLD_SPREAD_PCT = 2.0
IWF_MAX_VELOCITY_FPS = 20.0

DROP_FACTOR = 1.89
SPHERE_CD = 0.47
RHO_WATER = 998.2  # kg/m^3 at 20 C
RHO_AIR_SEA = 1.225  # kg/m^3, ISA sea level
M_PER_FT = 0.3048
MPS_PER_MPH = 0.44704
GPM_TO_M3S = 6.30902e-5
CALIBRATION_STEPS = 600


def inputs() -> list[dict[str, Any]]:
    return [
        spec("height_ft", "Display height", "number", "ft", None, True,
             "The effect you want; the nozzle table gives the flow and head for it."),
        spec("nozzle_type", "Nozzle type", "select", "", "smooth", False,
             "Used only for the flagged estimate when no table exists.", list(NOZZLE_TYPES)),
        spec("angle_deg", "Launch angle", "number", "deg", 90.0, False, "90 is vertical."),
        spec("supply_head_ft", "Head at the nozzle", "number", "ft", None, False,
             "Pin a head instead of a height to read the height off the table."),
        spec("gpm_each", "Flow per nozzle", "number", "gpm", None, False,
             "When the flow is known but no table is: sizes the flow; the head still needs a height."),
        spec("orifice_diameter_in", "Orifice diameter", "number", "in", None, False,
             "From the cut sheet; sets the drag drop size and the exit velocity."),
        spec("edge_distance_ft", "Distance to the nearest basin edge", "number", "ft", None, False,
             "Blank: half the shorter side of the basin (a centered jet)."),
        spec("basin_kind", "Basin", "select", "", "ground", False,
             "Raised tables need 1.5 x the height of setback.", list(SPLASH_RATIO)),
        spec("wind_mph", "Design wind", "number", "mph", None, False,
             "Blank uses the wind-control stage 1 setpoint."),
        spec("wind_control", "Wind control fitted", "bool", "", False),
        spec("indoor", "Indoors", "bool", "", False, False, "No wind drift indoors."),
        spec("body_contact", "People touch the water", "bool", "", False,
             "Interactive venues: exit velocity is capped at 20 ft/s in Utah."),
        spec("ring_diameter_ft", "Ring diameter", "number", "ft", None, False),
        spec("spacing_ft", "Nozzle spacing", "number", "ft", None, False),
    ]


# --------------------------------------------------------------------------------------
# Performance tables
# --------------------------------------------------------------------------------------


def table_points(feature_or_product: Any) -> list[dict[str, float]]:
    """The nozzle's performance points, normalized: ``head_ft`` (from ``psi`` when only that
    is given), ``flow_gpm``, ``display_height_ft``, ``display_diameter_ft``, ``throw_ft``,
    ``depth_over_nozzle_in``. Read from ``product["points"]`` or
    ``product["profile"]["points"]`` (the shape phase D's Nozzle Performance Point rows take)."""
    product = feature_or_product
    if isinstance(product, dict) and ("inputs" in product or "family" in product):
        product = product_of(product)
    product = product or {}
    raw = product.get("points") or (product.get("profile") or {}).get("points") or []
    pts = []
    for p in raw:
        head = num(p.get("head_ft"))
        if head <= 0 and num(p.get("psi")) > 0:
            head = num(p.get("psi")) * FT_PER_PSI
        pts.append({
            "head_ft": head,
            "flow_gpm": num(p.get("flow_gpm")),
            "display_height_ft": num(p.get("display_height_ft")),
            "display_diameter_ft": num(p.get("display_diameter_ft")),
            "throw_ft": num(p.get("throw_ft")),
            "depth_over_nozzle_in": num(p.get("depth_over_nozzle_in")),
        })
    return pts


def interpolate(points: list[dict[str, float]], by: str, x: float) -> dict[str, Any] | None:
    """Linear interpolation of every column of ``points`` at ``points[by] == x``.

    Returns ``None`` when fewer than one usable point exists or ``x`` lies outside the
    table: a nozzle asked for more than its cut sheet shows is a different nozzle, never an
    extrapolation. Columns a point leaves at 0 interpolate as 0 (unknown).
    """
    pts = sorted((p for p in points if p.get(by, 0) > 0), key=lambda p: p[by])
    if not pts or x <= 0:
        return None
    lo, hi = pts[0][by], pts[-1][by]
    if x < lo - 1e-9 or x > hi + 1e-9:
        return None
    for a, b in zip(pts, pts[1:] + [pts[-1]], strict=False):
        if a[by] - 1e-9 <= x <= b[by] + 1e-9:
            span = b[by] - a[by]
            t = 0.0 if span <= 0 else (x - a[by]) / span
            return {k: a[k] + t * (b[k] - a[k]) for k in a}
    return None


# --------------------------------------------------------------------------------------
# Trajectory: 2-D point mass with quadratic drag and wind
# --------------------------------------------------------------------------------------


def air_density(elevation_ft: float = 0.0) -> float:
    """ISA troposphere air density (kg/m^3) at ``elevation_ft``."""
    h_m = max(elevation_ft, 0.0) * M_PER_FT
    return RHO_AIR_SEA * (1.0 - 2.25577e-5 * h_m) ** 4.25588


def drag_k(drop_diameter_in: float | None, rho_air: float = RHO_AIR_SEA, cd: float = SPHERE_CD) -> float:
    """Drag constant k (1/m) in a = -k |v_rel| v_rel for a water sphere of the given size."""
    if not drop_diameter_in or drop_diameter_in <= 0:
        return 0.0
    d_m = drop_diameter_in * 0.0254
    return 3.0 * rho_air * cd / (4.0 * RHO_WATER * d_m)


def trajectory(
    v0_fps: float,
    angle_deg: float = 90.0,
    drop_diameter_in: float | None = None,
    wind_mph: float = 0.0,
    launch_height_ft: float = 0.0,
    elevation_ft: float = 0.0,
    samples: int = 40,
    steps: int = 2000,
) -> dict[str, Any]:
    """Fly a jet from the nozzle to the water level (y = 0) and report where it went.

    ``x`` is horizontal in the launch direction and the wind blows along +x (the worst case
    for a basin edge on that side). Returns ``apex_ft``, ``apex_x_ft``, ``range_ft`` (x at
    landing), ``flight_s`` and a ``path`` of ``samples`` (x, y) points in feet.
    """
    g = 9.80665
    k = drag_k(drop_diameter_in, air_density(elevation_ft))
    w = wind_mph * MPS_PER_MPH
    th = math.radians(angle_deg)
    v0 = max(v0_fps, 0.0) * M_PER_FT
    state = [0.0, max(launch_height_ft, 0.0) * M_PER_FT, v0 * math.cos(th), v0 * math.sin(th)]
    if v0 <= 0:
        return {"apex_ft": launch_height_ft, "apex_x_ft": 0.0, "range_ft": 0.0, "flight_s": 0.0, "path": []}

    def deriv(s: list[float]) -> list[float]:
        rx, ry = s[2] - w, s[3]
        sp = math.hypot(rx, ry)
        return [s[2], s[3], -k * sp * rx, -g - k * sp * ry]

    t_est = 2.0 * v0 / g + 2.0 * math.sqrt(2.0 * state[1] / g) + 0.1
    dt = t_est / max(steps, 50)
    t = 0.0
    apex_y, apex_x = state[1], state[0]
    trace = [(0.0, state[0], state[1])]
    for _ in range(400000):
        k1 = deriv(state)
        s2 = [state[i] + dt / 2 * k1[i] for i in range(4)]
        k2 = deriv(s2)
        s3 = [state[i] + dt / 2 * k2[i] for i in range(4)]
        k3 = deriv(s3)
        s4 = [state[i] + dt * k3[i] for i in range(4)]
        k4 = deriv(s4)
        new = [state[i] + dt / 6 * (k1[i] + 2 * k2[i] + 2 * k3[i] + k4[i]) for i in range(4)]
        t += dt
        if new[1] > apex_y:
            apex_y, apex_x = new[1], new[0]
        if new[1] < 0.0 and new[3] < 0.0:
            frac = state[1] / (state[1] - new[1]) if state[1] != new[1] else 1.0
            x_land = state[0] + frac * (new[0] - state[0])
            t_land = t - dt + frac * dt
            trace.append((t_land, x_land, 0.0))
            break
        state = new
        trace.append((t, state[0], state[1]))
    else:  # pragma: no cover - a 400k-step flight is a bug, not a jet
        x_land, t_land = state[0], t
    step = max(len(trace) // max(samples, 2), 1)
    path = [(round(x / M_PER_FT, 3), round(y / M_PER_FT, 3)) for _, x, y in trace[::step]]
    if path[-1] != (round(x_land / M_PER_FT, 3), 0.0):
        path.append((round(x_land / M_PER_FT, 3), 0.0))
    return {
        "apex_ft": apex_y / M_PER_FT,
        "apex_x_ft": apex_x / M_PER_FT,
        "range_ft": x_land / M_PER_FT,
        "flight_s": t_land,
        "path": path,
    }


def calibrate_v0(height_ft: float, angle_deg: float = 90.0, drop_diameter_in: float | None = None,
                 elevation_ft: float = 0.0) -> float:
    """Launch speed (ft/s) whose still-air apex is ``height_ft``, by bisection. Memoized: it
    depends only on its four arguments and costs ~45 RK4 flights, and flow() and checks()
    both ask for it on every derive."""
    return _calibrate_v0(float(height_ft), float(angle_deg),
                         float(drop_diameter_in) if drop_diameter_in else None, float(elevation_ft))


@lru_cache(maxsize=1024)
def _calibrate_v0(height_ft: float, angle_deg: float, drop_diameter_in: float | None, elevation_ft: float) -> float:
    if height_ft <= 0:
        return 0.0
    s = max(math.sin(math.radians(angle_deg)), 1e-6)
    lo, hi = 0.0, math.sqrt(2 * G_FT_S2 * height_ft) / s
    def apex(v0: float) -> float:
        return trajectory(v0, angle_deg, drop_diameter_in, 0.0, 0.0, elevation_ft, 2, CALIBRATION_STEPS)["apex_ft"]

    while apex(hi) < height_ft:
        hi *= 1.5
        if hi > 2000:
            return hi
    for _ in range(40):
        mid = (lo + hi) / 2
        if apex(mid) < height_ft:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def jet_diameter_in(flow_gpm: float, velocity_fps: float) -> float:
    """Diameter of a round jet carrying ``flow_gpm`` at ``velocity_fps``."""
    if flow_gpm <= 0 or velocity_fps <= 0:
        return 0.0
    area_ft2 = flow_gpm / 448.831 / velocity_fps
    return math.sqrt(4.0 * area_ft2 / math.pi) * 12.0


def splash_setback_ft(height_ft: float, basin_kind: str = "ground") -> float:
    return SPLASH_RATIO.get((basin_kind or "ground").lower(), 1.0) * max(height_ft, 0.0)


def fallback_k(nozzle_type: str) -> tuple[float, str]:
    t = (nozzle_type or "smooth").lower()
    key = "smooth" if t == "smooth" else ("aerating" if t in ("aerating", "column", "foam") else "other")
    return FALLBACK_K[key], FALLBACK_K_CITATION[key]


# --------------------------------------------------------------------------------------
# The family interface
# --------------------------------------------------------------------------------------


def _with_rated_flow(out: dict[str, Any], v: dict[str, Any]) -> dict[str, Any]:
    """A point with no head yet still carries an entered flow per nozzle, so a design that
    knows only "5 nozzles at 8 gpm" (the old engine's ``nozzle_array_flow``) keeps its flow."""
    if num(v.get("gpm_each")):
        out["gpm_each"] = num(v.get("gpm_each"))
        out["source"] = "rated_flow"
    out["exit_velocity_fps"] = 0.0
    out["psi"] = 0.0
    return out


def nozzle_point(feature: Any, site: dict[str, Any] | None = None, *, default_type: str = "smooth") -> dict[str, Any]:
    """Per-nozzle operating point for a jet feature: height, head, flow, exit velocity, and
    where the numbers came from (``source``: ``table`` | ``estimate`` | ``none``). Shared
    with the foam and interactive families."""
    v = values(feature, inputs())
    product = product_of(feature)
    profile = product.get("profile") or {}
    pts = table_points(product)
    height = num(v.get("height_ft"))
    pinned_head = num(v.get("supply_head_ft"))
    ntype = str(v.get("nozzle_type") or profile.get("nozzle_type") or default_type).lower()
    orifice = num(v.get("orifice_diameter_in")) or num(profile.get("orifice_diameter_in"))
    out: dict[str, Any] = {
        "height_ft": height, "head_ft": 0.0, "gpm_each": None, "source": "none", "in_table": None,
        "nozzle_type": ntype, "orifice_diameter_in": orifice, "notes": [], "citations": [],
    }

    usable = [p for p in pts if p["display_height_ft"] > 0 and p["head_ft"] > 0]
    if usable and height <= 0 and pinned_head <= 0:
        out["notes"].append("no height or head given")
        return _with_rated_flow(out, v)
    if usable:
        if height <= 0 and pinned_head > 0:
            row = interpolate(usable, "head_ft", pinned_head)
            if row:
                height = row["display_height_ft"]
        else:
            row = interpolate(usable, "display_height_ft", height)
        out["in_table"] = row is not None
        if row:
            out.update(height_ft=height, head_ft=row["head_ft"], source="table",
                       gpm_each=row["flow_gpm"] or None, display_diameter_ft=row["display_diameter_ft"])
            out["citations"].append(profile.get("source_url") or product.get("source_url") or CIT_TABLES)
            out["notes"].append(f"table: {height:g} ft -> {row['head_ft']:.2f} ft of head"
                                + (f", {row['flow_gpm']:.2f} gpm" if row["flow_gpm"] else ""))
        else:
            span = (min(p["display_height_ft"] for p in usable), max(p["display_height_ft"] for p in usable))
            out["notes"].append(f"{height:g} ft is outside this nozzle's table ({span[0]:g}-{span[1]:g} ft)")
            out["table_span_ft"] = span
            return out

    if out["source"] != "table":
        k, k_cit = fallback_k(ntype)
        if pinned_head > 0 and height <= 0:
            height = k * pinned_head
        head = pinned_head if pinned_head > 0 else (height / k if height > 0 else 0.0)
        if head <= 0:
            out["notes"].append("no height or head given")
            return _with_rated_flow(out, v)
        out.update(height_ft=height, head_ft=head, source="estimate", k=k, k_citation=k_cit)
        out["citations"].append(k_cit)
        out["notes"].append(f"estimate: height = {k:g} x head -> {head:.2f} ft of head for {height:g} ft")
        rated_gpm, rated_head = num(profile.get("rated_gpm")), num(profile.get("rated_head_ft"))
        cd = num(profile.get("cd"))
        if rated_gpm and rated_head:
            out["gpm_each"] = nozzle_flow(head, rated_gpm=rated_gpm, rated_head_ft=rated_head).value
        elif cd and orifice:
            out["gpm_each"] = nozzle_flow(head, cd=cd, orifice_diameter_in=orifice).value
        elif orifice:
            out["gpm_each"] = nozzle_flow(head, cd=FALLBACK_CD, orifice_diameter_in=orifice).value
            out["citations"].append(CIT_ORIFICE_CD)
            out["notes"].append(f"flow from the orifice equation with Cd {FALLBACK_CD:g} (placeholder)")
    if not out["gpm_each"] and num(v.get("gpm_each")):
        out["gpm_each"] = num(v.get("gpm_each"))
        out["notes"].append(f"flow {out['gpm_each']:g} gpm per nozzle as entered")

    head = out["head_ft"]
    gpm = out["gpm_each"] or 0.0
    if orifice and gpm:
        vel = gpm / 448.831 / (math.pi / 4 * (orifice / 12.0) ** 2)
    else:
        vel = math.sqrt(2 * G_FT_S2 * head) if head > 0 else 0.0
    out["exit_velocity_fps"] = vel
    out["psi"] = head / FT_PER_PSI if head else 0.0
    return out


def flow(feature: Any, site: dict[str, Any] | None = None, rules: dict[str, Any] | None = None) -> FamilyResult:
    v = values(feature, inputs())
    n = count_of(feature)
    pt = nozzle_point(feature, site)
    site = site or {}
    steps = list(pt["notes"])
    citations = list(pt["citations"])
    warnings: list[str] = []
    data: dict[str, Any] = {"point": pt, "count": n, "complete": False}

    if pt["source"] == "rated_flow":
        total = pt["gpm_each"] * n
        warnings.append("Flow per nozzle as entered; enter the display height (or a head) for the head and the checks.")
        steps.append(f"Q = {pt['gpm_each']:g} gpm x {n} = {total:.1f} GPM (head unknown)")
        data.update(gpm=total, gpm_each=pt["gpm_each"], head_ft=None, source="rated_flow")
        return FamilyResult(calc="jets_family_flow", value=total, unit="GPM", steps=steps,
                            formula="Q = gpm_each x count", warnings=warnings, data=data)
    if pt["source"] == "none" or not pt["head_ft"]:
        warnings.append("Enter a display height and pick a nozzle (or a head) to size this jet.")
        return FamilyResult(calc="jets_family_flow", unit="GPM", steps=steps, citations=citations,
                            warnings=warnings, data=data)
    if pt["source"] == "estimate":
        warnings.append(
            f"Height assumed from a constant (k = {pt['k']:g}), not a nozzle table. Pick a Nozzle Profile "
            "with performance points before this design is issued."
        )
    gpm_each = pt["gpm_each"]
    total = gpm_each * n if gpm_each else None
    if total is None:
        warnings.append("The head is known but not the flow: pick a nozzle with a rated flow or enter the orifice size.")
    else:
        steps.append(f"Q = {gpm_each:.2f} gpm x {n} nozzles = {total:.1f} GPM at {pt['head_ft']:.1f} ft ({pt['psi']:.1f} psi)")

    angle = num(v.get("angle_deg"), 90.0)
    jet_d = pt["orifice_diameter_in"] or (jet_diameter_in(gpm_each, pt["exit_velocity_fps"]) if gpm_each else 0.0)
    drop_d = DROP_FACTOR * jet_d if jet_d else None
    elev = num(site.get("elevation_ft"))
    v0 = calibrate_v0(pt["height_ft"], angle, drop_d, elev)
    still = trajectory(v0, angle, drop_d, 0.0, 0.0, elev)
    wind = 0.0 if v.get("indoor") else (num(v.get("wind_mph")) or R.rule(rules, "wind_reduce_mph"))
    windy = trajectory(v0, angle, drop_d, wind, 0.0, elev)
    setback = splash_setback_ft(pt["height_ft"], v.get("basin_kind") or "ground")
    steps.append(
        f"trajectory: launch {v0:.1f} ft/s at {angle:g} deg (drop {drop_d or 0:.2f} in) -> apex {still['apex_ft']:.1f} ft, "
        f"lands {still['range_ft']:.1f} ft out still, {windy['range_ft']:.1f} ft in {wind:g} mph wind"
    )
    steps.append(f"splash setback {SPLASH_RATIO.get(str(v.get('basin_kind') or 'ground'), 1.0):g} x {pt['height_ft']:g} ft = {setback:.1f} ft")
    citations += [CIT_DRAG, CIT_SPLASH]

    data.update({
        "gpm": total,
        "gpm_each": gpm_each,
        "head_ft": pt["head_ft"],
        "psi": pt["psi"],
        "height_ft": pt["height_ft"],
        "source": pt["source"],
        "exit_velocity_fps": pt["exit_velocity_fps"],
        "launch_velocity_fps": v0,
        "drop_diameter_in": drop_d,
        "trajectory": still,
        "trajectory_wind": windy,
        "wind_mph": wind,
        "range_ft": still["range_ft"],
        "landing_wind_ft": windy["range_ft"],
        "setback_required_ft": setback,
        "complete": True,
    })
    if n > 1:
        data["manifold"] = {"takeoffs": n, "gpm_each": gpm_each, "head_ft": pt["head_ft"],
                            "spread_target_pct": MANIFOLD_SPREAD_PCT}
        steps.append(f"manifold: size for <= {MANIFOLD_SPREAD_PCT:g}% pressure spread across {n} nozzles")
        citations.append(CIT_SPREAD)

    return FamilyResult(
        calc="jets_family_flow",
        value=total,
        unit="GPM",
        inputs={
            "height_ft": make_input(pt["height_ft"], "ft", "user"),
            "head_ft": make_input(round(pt["head_ft"], 3), "ft", "lookup" if pt["source"] == "table" else "default",
                                  "nozzle table" if pt["source"] == "table" else "k x head estimate"),
            "count": make_input(n, "", "user"),
            "angle_deg": make_input(angle, "deg", "user"),
            "wind_mph": make_input(wind, "mph", "user" if num(v.get("wind_mph")) else "default",
                                   "" if num(v.get("wind_mph")) else R.citation("wind_reduce_mph", rules)),
        },
        formula="table: (height -> head, gpm) by interpolation; Q = gpm_each x count; trajectory: "
                "dv/dt = -g y - k|v - w|(v - w), k = 3 rho_a Cd / (4 rho_w d_drop)",
        steps=steps,
        citations=citations,
        warnings=warnings,
        data=data,
    )


def edge_distance(v: dict[str, Any], derived: dict[str, Any] | None) -> float:
    dist = num(v.get("edge_distance_ft"))
    if dist <= 0 and derived and derived.get("basin"):
        plan = basin_plan(derived["basin"])
        dist = min(plan["length_ft"], plan["width_ft"]) / 2.0
    return dist


def jet_checks(fid: str, v: dict[str, Any], d: dict[str, Any], derived: dict[str, Any] | None,
               site: dict[str, Any] | None, rules: dict[str, Any] | None, prefix: str = "JET") -> list[Check]:
    """The checks every jet-like family shares (jets, foam, interactive spray features)."""
    out: list[Check] = []
    pt = d.get("point") or {}
    if pt.get("in_table") is False:
        span = pt.get("table_span_ft") or (0, 0)
        out.append(Check(
            f"{prefix}_OUTSIDE_TABLE", BLOCKER, "This nozzle cannot make that height",
            detail=f"{pt.get('height_ft'):g} ft asked; its table covers {span[0]:g}-{span[1]:g} ft.",
            remedy="Pick a larger nozzle or lower the display height.",
            citation=CIT_TABLES, feature=fid, value=pt.get("height_ft"), limit=span[1], unit="ft",
        ))
        return out
    if not d.get("complete"):
        out.append(Check(f"{prefix}_INCOMPLETE", INFO, "Enter a display height and pick a nozzle", feature=fid))
        return out
    if d["source"] == "estimate":
        out.append(Check(
            f"{prefix}_HEIGHT_ESTIMATED", WARNING, "Jet height assumed from a constant, not a nozzle table",
            detail=f"k = {pt.get('k'):g}: {d['height_ft']:g} ft needs ~{d['head_ft']:.1f} ft of head. Real nozzles vary 2-3x.",
            remedy="Pick a Nozzle Profile with performance points.",
            citation=pt.get("k_citation") or CIT_SMOOTH, feature=fid,
        ))
    if d.get("gpm") is None:
        out.append(Check(
            f"{prefix}_NO_FLOW", WARNING, "Jet flow is unknown",
            detail="The head is known but not the flow per nozzle.",
            remedy="Pick a nozzle with a rated flow, or enter its orifice size.", feature=fid,
        ))
    dist = edge_distance(v, derived)
    if dist > 0:
        if dist < d["setback_required_ft"]:
            out.append(Check(
                f"{prefix}_SETBACK", WARNING, "Basin edge is inside the splash radius",
                detail=f"Edge {dist:.1f} ft from a {d['height_ft']:g} ft jet; still-air splash needs {d['setback_required_ft']:.1f} ft.",
                remedy="Widen the basin, lower the jet, or fit wind control and accept a tighter setback.",
                citation=CIT_SPLASH, section="basin", feature=fid,
                value=round(dist, 2), limit=round(d["setback_required_ft"], 2), unit="ft",
            ))
        if dist < d["landing_wind_ft"]:
            fitted = bool(v.get("wind_control"))
            out.append(Check(
                f"{prefix}_WIND_DRIFT", INFO if fitted else WARNING,
                "Wind carries the jet past the basin edge",
                detail=f"At {d['wind_mph']:g} mph the jet lands {d['landing_wind_ft']:.1f} ft out; the edge is at {dist:.1f} ft.",
                remedy=(f"Wind control is fitted: confirm stage 1 reduces at {R.rule(rules, 'wind_reduce_mph'):g} mph."
                        if fitted else
                        f"Fit two-stage wind control (reduce at {R.rule(rules, 'wind_reduce_mph'):g} mph, "
                        f"stop at {R.rule(rules, 'wind_stop_mph'):g} mph)."),
                citation=CIT_WIND + "; setpoints: " + R.citation("wind_reduce_mph", rules),
                section="control", feature=fid,
                value=round(d["landing_wind_ft"], 2), limit=round(dist, 2), unit="ft",
            ))
    spread = (derived or {}).get("manifold_spread_pct")
    if d.get("manifold"):
        if spread is None:
            out.append(Check(
                f"{prefix}_MANIFOLD_TARGET", INFO, f"Size the manifold for <= {MANIFOLD_SPREAD_PCT:g}% pressure spread",
                detail=f"{d['count']} nozzles share a header; equal heights need equal head at every takeoff.",
                remedy="The piping step sizes the header for the spread target.",
                citation=CIT_SPREAD, section="piping", feature=fid, limit=MANIFOLD_SPREAD_PCT, unit="%",
            ))
        elif spread > MANIFOLD_SPREAD_PCT:
            out.append(Check(
                f"{prefix}_MANIFOLD_SPREAD", WARNING, "Jets will not stand at equal heights",
                detail=f"Pressure spread {spread:.1f}% across the header (target {MANIFOLD_SPREAD_PCT:g}%).",
                remedy="Upsize the header or feed it from both ends.",
                citation=CIT_SPREAD, section="piping", feature=fid,
                value=round(spread, 2), limit=MANIFOLD_SPREAD_PCT, unit="%",
            ))
    if (site_regulated(site) or v.get("body_contact")) and d["exit_velocity_fps"] > IWF_MAX_VELOCITY_FPS:
        out.append(Check(
            f"{prefix}_IWF_VELOCITY", BLOCKER, "Nozzle velocity is over 20 ft/s at a body-contact venue",
            detail=f"Exit velocity {d['exit_velocity_fps']:.1f} ft/s.",
            remedy="Lower the head or use a larger orifice for the same flow.",
            citation=CIT_IWF, feature=fid,
            value=round(d["exit_velocity_fps"], 2), limit=IWF_MAX_VELOCITY_FPS, unit="ft/s",
        ))
    return out


def checks(feature: Any, derived: dict[str, Any] | None = None, site: dict[str, Any] | None = None,
           rules: dict[str, Any] | None = None) -> list[Check]:
    v = values(feature, inputs())
    d = flow_data(flow, feature, derived, site, rules)
    return jet_checks(feature_dict(feature).get("id") or "", v, d, derived, site, rules)


def jet_terminal(feature: Any, d: dict[str, Any]) -> dict[str, Any]:
    """Orifice (one nozzle) or manifold (several) terminal for a jet-like feature."""
    pt = d.get("point") or {}
    product = product_of(feature)
    profile = product.get("profile") or {}
    curve = [(p["head_ft"], p["flow_gpm"]) for p in table_points(product) if p["head_ft"] > 0 and p["flow_gpm"] > 0]
    nozzle = {"flow_gpm": d.get("gpm_each"), "head_ft": d.get("head_ft"), "curve": sorted(curve)}
    if num(profile.get("cd")) and pt.get("orifice_diameter_in"):
        nozzle["cd"] = num(profile.get("cd"))
        nozzle["area_in2"] = math.pi / 4 * pt["orifice_diameter_in"] ** 2
    n = d.get("count") or count_of(feature)
    if n > 1:
        fid = feature_dict(feature).get("id") or "f"
        return {
            "kind": "manifold",
            "params": {
                "takeoffs": [dict(nozzle, id=f"{fid}-{i + 1}") for i in range(n)],
                "spread_target_pct": MANIFOLD_SPREAD_PCT,
            },
        }
    return {"kind": "orifice", "params": nozzle}


def terminal(feature: Any, derived: dict[str, Any] | None = None) -> dict[str, Any]:
    d = (derived or {}).get("flow") or flow(feature).data
    return jet_terminal(feature, d)
