"""Laminar jets: glass-rod arcs from a deck or a basin wall.

Industry report section 1.4. The cut sheet gives flow and pressure for an arc height and
throw; the arc's launch angle and speed follow from that geometry, and the 2-D drag
trajectory in :mod:`.jets` then says where it lands in wind. The built-in table is the CMP
deck jet's two published points, used when no product is picked (and flagged).

``derived`` keys read by :func:`checks`: ``flow``.
"""

from __future__ import annotations

import math
from typing import Any

from .. import rules as R
from ..constants import FT_PER_PSI
from ..envelope import make_input
from . import jets
from .base import (
    BLOCKER,
    G_FT_S2,
    INFO,
    WARNING,
    Check,
    FamilyResult,
    count_of,
    feature_dict,
    flow_data,
    num,
    product_of,
    spec,
    values,
)

FAMILY = "laminar"
LABEL = "Laminar jets"
TYPES = ("Laminar Jet", "Deck Jet")

CIT_CMP = "https://www.poolsupplyunlimited.com/Products/Manuals/169670_20187311590.pdf (CMP deck jet: 2.5 gpm @ 3.8 psi -> 2 ft x 3 ft; 3.5 @ 6.5 -> 4 x 6) [snippet]"
CIT_FIBERSTARS = "https://images.inyopools.com/cloud/documents/fiberstars-light-streams-lsml.pdf (>= 5 gpm; 3 ft high, 5 ft out)"
CIT_FILTER = "Industry report section 1.4: laminar jets clog easily; filtration ahead of the jet"

# CMP deck jet (the generic stand-in until a product is picked).
GENERIC_TABLE = (
    {"flow_gpm": 2.5, "psi": 3.8, "display_height_ft": 2.0, "throw_ft": 3.0},
    {"flow_gpm": 3.5, "psi": 6.5, "display_height_ft": 4.0, "throw_ft": 6.0},
)


def inputs() -> list[dict[str, Any]]:
    return [
        spec("arc_height_ft", "Arc height", "number", "ft", None, True),
        spec("landing_edge_ft", "Jet to the basin edge beyond the landing", "number", "ft", None, False,
             "Measured along the arc; the landing must fall short of it."),
        spec("wind_mph", "Design wind", "number", "mph", None, False, "Blank uses the wind stage 1 setpoint."),
        spec("filtered", "Filtration ahead of the jets", "bool", "", True),
    ]


def arc_from_table(height_ft: float, throw_ft: float) -> tuple[float, float]:
    """Launch speed (ft/s) and angle (deg) of a drag-free arc from water level that peaks at
    ``height_ft`` and lands ``throw_ft`` away."""
    vy = math.sqrt(2.0 * G_FT_S2 * height_ft)
    flight = 2.0 * vy / G_FT_S2
    vx = throw_ft / flight
    return math.hypot(vx, vy), math.degrees(math.atan2(vy, vx))


def flow(feature: Any, site: dict[str, Any] | None = None, rules: dict[str, Any] | None = None) -> FamilyResult:
    v = values(feature, inputs())
    n = count_of(feature)
    site = site or {}
    height = num(v.get("arc_height_ft"))
    pts = jets.table_points(product_of(feature))
    source = "table"
    citations = []
    if not any(p["display_height_ft"] > 0 for p in pts):
        pts = jets.table_points({"points": list(GENERIC_TABLE)})
        source = "generic"
        citations.append(CIT_CMP)
    else:
        citations.append((product_of(feature).get("profile") or {}).get("source_url") or jets.CIT_TABLES)
    row = jets.interpolate(pts, "display_height_ft", height)
    data: dict[str, Any] = {"count": n, "source": source, "complete": False, "in_table": row is not None}
    if height <= 0 or row is None:
        span = [p["display_height_ft"] for p in pts if p["display_height_ft"] > 0]
        data["table_span_ft"] = (min(span), max(span)) if span else (0, 0)
        return FamilyResult(calc="laminar_family_flow", unit="GPM", citations=citations,
                            warnings=["Enter an arc height inside the jet's table."], data=data)
    # A table without throws: the CMP points' own ratio (3 ft out for 2 ft up, 6 for 4).
    throw = row["throw_ft"] or height * 1.5
    v0_free, angle = arc_from_table(height, throw)
    gpm_each = row["flow_gpm"]
    stream_d = jets.jet_diameter_in(gpm_each, v0_free)
    drop_d = jets.DROP_FACTOR * stream_d if stream_d else None
    elev = num(site.get("elevation_ft"))
    v0 = jets.calibrate_v0(height, angle, drop_d, elev)
    still = jets.trajectory(v0, angle, drop_d, 0.0, 0.0, elev)
    wind = num(v.get("wind_mph")) or R.rule(rules, "wind_reduce_mph")
    windy = jets.trajectory(v0, angle, drop_d, wind, 0.0, elev)
    total = gpm_each * n
    head = row["head_ft"]
    steps = [
        f"table at {height:g} ft: {gpm_each:.2f} gpm at {head / FT_PER_PSI:.1f} psi, throw {throw:.1f} ft",
        f"arc: launch {v0_free:.1f} ft/s at {angle:.1f} deg (drag-free fit to height and throw)",
        f"with drag: lands {still['range_ft']:.1f} ft still, {windy['range_ft']:.1f} ft in {wind:g} mph wind",
        f"Q = {gpm_each:.2f} x {n} = {total:.1f} GPM",
    ]
    warnings = []
    if source == "generic":
        warnings.append("No laminar product picked: sized from the CMP deck jet's published points.")
    data.update({
        "gpm": total, "gpm_each": gpm_each, "head_ft": head, "psi": head / FT_PER_PSI,
        "height_ft": height, "throw_ft": throw, "angle_deg": angle, "launch_velocity_fps": v0,
        "drop_diameter_in": drop_d, "trajectory": still, "trajectory_wind": windy, "wind_mph": wind,
        "landing_ft": still["range_ft"], "landing_wind_ft": windy["range_ft"], "complete": True,
    })
    citations += [jets.CIT_DRAG]
    return FamilyResult(
        calc="laminar_family_flow",
        value=total,
        unit="GPM",
        inputs={
            "arc_height_ft": make_input(height, "ft", "user"),
            "count": make_input(n, "", "user"),
            "wind_mph": make_input(wind, "mph", "user" if num(v.get("wind_mph")) else "default"),
        },
        formula="table (height -> gpm, psi, throw); v0, angle from vy = sqrt(2 g H), vx = R / (2 vy / g); drag trajectory for landing",
        steps=steps,
        citations=citations,
        warnings=warnings,
        data=data,
    )


def checks(feature: Any, derived: dict[str, Any] | None = None, site: dict[str, Any] | None = None,
           rules: dict[str, Any] | None = None) -> list[Check]:
    v = values(feature, inputs())
    fid = feature_dict(feature).get("id") or ""
    d = flow_data(flow, feature, derived, site, rules)
    out: list[Check] = []
    if not d.get("complete"):
        if d.get("in_table") is False and num(v.get("arc_height_ft")) > 0:
            span = d.get("table_span_ft") or (0, 0)
            out.append(Check(
                "LAMINAR_OUTSIDE_TABLE", BLOCKER, "This laminar jet cannot make that arc",
                detail=f"{num(v.get('arc_height_ft')):g} ft asked; its table covers {span[0]:g}-{span[1]:g} ft.",
                remedy="Pick a larger laminar jet or lower the arc.", feature=fid,
            ))
        else:
            out.append(Check("LAMINAR_INCOMPLETE", INFO, "Enter the arc height", feature=fid))
        return out
    if d["source"] == "generic":
        out.append(Check(
            "LAMINAR_GENERIC", WARNING, "Laminar jet sized from a generic table",
            detail="Using the CMP deck jet's published points.", remedy="Pick the laminar product.",
            citation=CIT_CMP, feature=fid,
        ))
    edge = num(v.get("landing_edge_ft"))
    if edge > 0:
        if edge < d["landing_ft"]:
            out.append(Check(
                "LAMINAR_LANDS_OUTSIDE", BLOCKER, "The arc lands outside the basin",
                detail=f"Lands {d['landing_ft']:.1f} ft out in still air; the edge is {edge:g} ft away.",
                remedy="Lower the flow, re-aim the jet, or move it back.",
                section="basin", feature=fid, value=round(d["landing_ft"], 2), limit=edge, unit="ft",
            ))
        elif edge < d["landing_wind_ft"]:
            out.append(Check(
                "LAMINAR_WIND_MARGIN", WARNING, "Wind carries the arc past the basin edge",
                detail=f"At {d['wind_mph']:g} mph it lands {d['landing_wind_ft']:.1f} ft out; the edge is {edge:g} ft.",
                remedy=f"Leave more margin or fit wind control (reduce at {R.rule(rules, 'wind_reduce_mph'):g} mph).",
                citation=R.citation("wind_reduce_mph", rules), section="basin", feature=fid,
                value=round(d["landing_wind_ft"], 2), limit=edge, unit="ft",
            ))
    if not v.get("filtered"):
        out.append(Check(
            "LAMINAR_NO_FILTER", WARNING, "No filtration ahead of the laminar jets",
            detail="Laminar nozzles clog easily and lose their glass-rod look.",
            remedy="Put a filter (or a fine strainer) on the laminar supply.",
            citation=CIT_FILTER, section="treatment", feature=fid,
        ))
    return out


def terminal(feature: Any, derived: dict[str, Any] | None = None) -> dict[str, Any]:
    d = (derived or {}).get("flow") or flow(feature).data
    return jets.jet_terminal(feature, {**d, "point": {}})
