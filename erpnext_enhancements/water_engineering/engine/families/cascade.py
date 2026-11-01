"""Cascades, tiered fountains, naturalistic streams and the ponds they fall into.

Industry report section 1.6. Each tier's spillway needs a flow per foot (or per inch) of
width for the chosen look; the same water runs through every tier in series, so **the
tightest tier governs** the pump flow. At shutdown every upper pool drains its sheet back
into the lowest basin, which must hold it.

A **spillway** (a width) uses the trade's waterfall look bands (GPH per foot of spillway,
measured at the spillway, not the frame rocks):

* light: 1,500 (Aquascape's residential minimum, the same as its 125 GPH per inch)
* average: 2,400 (Pond Trade: 200 GPH per inch for a 1 in sheet)
* heavy: 3,000 (Aquascape's commercial figure)

A **round tier** (a rim diameter, the old ``Tiered Fountain``) is a circular weir and keeps
the DOC-0049 B edge rule the engine has always used (``feature.tiered_fountain_flow``): light
0.5 gpm per foot of rim (the operating figure), average 4 and heavy 6 (the band it says to
engineer the plumbing for). The waterfall bands are for naturalistic spillways and would put
500 gpm over a 4 ft bowl.

``derived`` keys read by :func:`checks`: ``flow``, ``basin`` (the lowest basin) and
``pipe_volume_gal`` (water in the pipes that drains back too, when the piping step knows it).
"""

from __future__ import annotations

import math
from typing import Any

from .. import rules as R
from ..envelope import CalcOption, make_input
from .base import (
    BLOCKER,
    GAL_PER_CUBIC_FOOT,
    INFO,
    WARNING,
    Check,
    FamilyResult,
    basin_plan,
    count_of,
    feature_dict,
    flow_data,
    num,
    spec,
    values,
)

FAMILY = "cascade"
LABEL = "Cascade, tiers or stream"
TYPES = ("Tiered Fountain", "Cascade", "Stream", "Waterfall", "Rill", "Pond")

CIT_AQUASCAPE = (
    "https://support.aquascapeinc.com/hc/en-us/articles/48506913796244-Waterfall-What-flow-rate-is-needed-for-a-waterfall "
    "(1,500 GPH/ft residential minimum; 3,000 commercial; ~125 GPH per inch)"
)
CIT_POND_TRADE = "https://www.pondtrademag.com/?p=17369 (50 / 100 / 200 GPH per inch for 1/4 / 1/2 / 1 in sheets) [snippet]"
CIT_TURNOVER = (
    "https://manualslib.fr/manual/724172/Aquascape-Aquaforce-1000-Gph.html?page=4 (pond turnover at least every 2 h) [snippet]; "
    "https://playitkoi.com/pages/pond-pump-sizing-guide (koi: every hour) [snippet]"
)
CIT_DRAINBACK = "https://watershapes.com/water-in-transit-pond-problem/ (drain-back at shutdown overflows the lowest basin)"
CIT_RILL = "https://www.slideshare.net/slideshow/water-feature-types/5145948 (Crystal limits rills to 12 in wide)"

LOOK_GPH_PER_FT = {"light": 1500.0, "average": 2400.0, "heavy": 3000.0}
RIM_GPM_PER_FT = {"light": 0.5, "average": 4.0, "heavy": 6.0}
CIT_RIM = "DOC-0049 / B - Surge Basin (edge sheet: operate ~0.5 GPM/ft, engineer plumbing for 4-6 GPM/ft)"
# Sheet depth over the lip for each look, read off Pond Trade's GPH-per-inch figures
# (100 at 1/2 in, 200 at 1 in): light 125/in interpolates to 5/8 in, heavy 250/in extrapolates
# to 1-1/4 in. Used only for the water standing over each lip that drains back at shutdown.
LOOK_SHEET_IN = {"light": 0.625, "average": 1.0, "heavy": 1.25}
LOOK_CITATION = {"light": CIT_AQUASCAPE, "average": CIT_POND_TRADE, "heavy": CIT_AQUASCAPE}
GPH_PER_INCH = 125.0
POND_TURNOVER_H = 2.0
KOI_TURNOVER_H = 1.0
RILL_MAX_IN = 12.0

TIER_COLUMNS = [
    {"name": "label", "label": "Tier", "kind": "text"},
    {"name": "spill_width_in", "label": "Spillway width", "kind": "number", "unit": "in"},
    {"name": "diameter_in", "label": "Rim diameter (round tier)", "kind": "number", "unit": "in"},
    {"name": "gpm_per_ft", "label": "Rim flow (pins the look)", "kind": "number", "unit": "gpm/ft"},
    {"name": "look", "label": "Look", "kind": "select", "options": list(LOOK_GPH_PER_FT)},
    {"name": "pool_area_sqft", "label": "Pool surface above the lip", "kind": "number", "unit": "sq ft"},
    {"name": "fall_in", "label": "Fall below", "kind": "number", "unit": "in"},
]


def inputs() -> list[dict[str, Any]]:
    return [
        spec("tiers", "Tiers or spillways", "table", "", [], True,
             "Each spillway (width) or round tier (rim diameter); a round rim spills all the way round.",
             columns=TIER_COLUMNS),
        spec("look", "Default look", "select", "", "average", False, "", list(LOOK_GPH_PER_FT)),
        spec("stream_length_ft", "Stream length", "number", "ft", None),
        spec("stream_width_ft", "Stream width", "number", "ft", None),
        spec("stream_depth_in", "Flowing depth in the stream", "number", "in", None),
        spec("pond_volume_gal", "Pond volume", "number", "gal", None),
        spec("koi", "Koi pond", "bool", "", False, False, "Koi want a turnover every hour."),
        spec("pond_length_ft", "Pond length (for the liner)", "number", "ft", None),
        spec("pond_width_ft", "Pond width (for the liner)", "number", "ft", None),
        spec("pond_depth_ft", "Pond depth (for the liner)", "number", "ft", None),
        spec("top_elevation_ft", "Top spillway above the pump's water", "number", "ft", None),
    ]


def tier_flow(tier: dict[str, Any], default_look: str = "average") -> dict[str, Any]:
    """Flow one spillway or round tier needs for its look."""
    look = str(tier.get("look") or default_look).lower()
    if look not in LOOK_GPH_PER_FT:
        look = "average"
    width_in = num(tier.get("spill_width_in"))
    rim = width_in <= 0 and num(tier.get("diameter_in")) > 0
    if rim:
        width_in = math.pi * num(tier.get("diameter_in"))
        per_ft = num(tier.get("gpm_per_ft")) or RIM_GPM_PER_FT[look]
        gpm = per_ft * width_in / 12.0
        gph = gpm * 60.0
    else:
        gph = LOOK_GPH_PER_FT[look] * width_in / 12.0
        gpm = gph / 60.0
    return {"label": tier.get("label") or "", "look": look, "width_in": width_in, "gph": gph, "gpm": gpm,
            "rim": rim, "gpm_per_ft": gpm / (width_in / 12.0) if width_in else 0.0,
            "pool_area_sqft": num(tier.get("pool_area_sqft"))}


def _francis_head_in(gpm_per_ft: float) -> float:
    """Head (in) over a long crest passing ``gpm_per_ft``: Francis Q/L = 36 h^1.5 inverted."""
    return (max(gpm_per_ft, 0.0) / 36.0) ** (2.0 / 3.0)


def liner_size_ft(length_ft: float, width_ft: float, depth_ft: float, overlap_ft: float) -> tuple[float, float]:
    """Sapphire house rule: liner = L + 2 x depth + overlap, each direction."""
    return length_ft + 2 * depth_ft + overlap_ft, width_ft + 2 * depth_ft + overlap_ft


def flow(feature: Any, site: dict[str, Any] | None = None, rules: dict[str, Any] | None = None) -> FamilyResult:
    v = values(feature, inputs())
    n = count_of(feature)
    look = str(v.get("look") or "average").lower()
    tiers = [tier_flow(t or {}, look) for t in (v.get("tiers") or [])]
    tiers = [t for t in tiers if t["width_in"] > 0]
    stream_w = num(v.get("stream_width_ft"))
    if not tiers and stream_w > 0:
        tiers = [tier_flow({"label": "stream", "spill_width_in": stream_w * 12.0}, look)]
    if not tiers:
        return FamilyResult(calc="cascade_family_flow", unit="GPM", citations=[CIT_AQUASCAPE],
                            warnings=["Enter at least one spillway width or tier diameter."], data={"complete": False})
    steps = [
        (f"{t['label'] or 'tier'}: rim {t['width_in'] / 12.0:.2f} ft x {t['gpm_per_ft']:g} gpm/ft ({t['look']}) = "
         f"{t['gpm']:.1f} gpm") if t["rim"] else
        (f"{t['label'] or 'tier'}: {t['width_in']:.1f} in x {LOOK_GPH_PER_FT[t['look']]:g} GPH/ft ({t['look']}) = "
         f"{t['gph']:.0f} GPH = {t['gpm']:.1f} gpm")
        for t in tiers
    ]
    governing = max(tiers, key=lambda t: t["gpm"])
    total = governing["gpm"] * n
    steps.append(f"the tightest tier governs: {governing['label'] or 'tier'} = {governing['gpm']:.1f} gpm (x {n} = {total:.1f})")
    citations = sorted({CIT_RIM if t["rim"] else LOOK_CITATION[t["look"]] for t in tiers})

    drain = 0.0
    for t in tiers:
        area = t["pool_area_sqft"]
        # A rim's sheet is thin; take its depth from the Francis head that passes its gpm/ft.
        sheet_in = _francis_head_in(t["gpm_per_ft"]) if t["rim"] else LOOK_SHEET_IN[t["look"]]
        drain += area * sheet_in / 12.0 * GAL_PER_CUBIC_FOOT
    stream_l = num(v.get("stream_length_ft"))
    stream_d = num(v.get("stream_depth_in"))
    stream_gal = stream_l * stream_w * stream_d / 12.0 * GAL_PER_CUBIC_FOOT
    drain += stream_gal
    steps.append(f"drain-back at shutdown: sheets over each lip + stream {stream_gal:.0f} gal = {drain:.0f} gal")
    citations.append(CIT_DRAINBACK)

    data: dict[str, Any] = {
        "gpm": total,
        "gpm_each": governing["gpm"],
        "tiers": tiers,
        "governing_tier": governing["label"],
        "drain_back_gal": drain,
        "stream_gal": stream_gal,
        "complete": True,
    }
    pond = num(v.get("pond_volume_gal"))
    if pond > 0 and total > 0:
        data["pond_turnover_h"] = pond / (total * 60.0)
        steps.append(f"pond turnover {pond:g} gal / {total:.1f} gpm = {data['pond_turnover_h']:.2f} h")
        citations.append(CIT_TURNOVER)
    pl, pw, pd = num(v.get("pond_length_ft")), num(v.get("pond_width_ft")), num(v.get("pond_depth_ft"))
    if pl > 0 and pw > 0:
        overlap = R.rule(rules, "liner_overlap_ft")
        data["liner_ft"] = liner_size_ft(pl, pw, pd, overlap)
        steps.append(f"liner {data['liner_ft'][0]:g} x {data['liner_ft'][1]:g} ft (L + 2D + {overlap:g} ft)")
        citations.append(R.citation("liner_overlap_ft", rules))

    def tier_at(t: dict[str, Any], lk: str) -> float:
        row = {"spill_width_in": t["width_in"]} if not t["rim"] else {"diameter_in": t["width_in"] / math.pi}
        return tier_flow(row, lk)["gpm"]

    options = [
        CalcOption(key=lk, label=f"{lk.title()} look", value=round(max(tier_at(t, lk) for t in tiers) * n, 1),
                   recommended=(lk == look))
        for lk in LOOK_GPH_PER_FT
    ]
    return FamilyResult(
        calc="cascade_family_flow",
        value=total,
        unit="GPM",
        inputs={
            "tiers": make_input(len(tiers), "", "user"),
            "look": make_input(look, "", "user"),
            "count": make_input(n, "", "user"),
        },
        formula="per tier: GPH = look GPH/ft x width_in / 12; Q = max over tiers / 60 (series cascade)",
        steps=steps,
        citations=citations,
        options=options,
        data=data,
    )


def checks(feature: Any, derived: dict[str, Any] | None = None, site: dict[str, Any] | None = None,
           rules: dict[str, Any] | None = None) -> list[Check]:
    v = values(feature, inputs())
    fid = feature_dict(feature).get("id") or ""
    d = flow_data(flow, feature, derived, site, rules)
    out: list[Check] = []
    if not d.get("complete"):
        return [Check("CASCADE_INCOMPLETE", INFO, "Enter the spillway widths", feature=fid)]
    basin = (derived or {}).get("basin")
    if basin:
        plan = basin_plan(basin)
        freeboard_in = num(basin.get("freeboard_in"))
        drain = d["drain_back_gal"] + num((derived or {}).get("pipe_volume_gal"))
        if freeboard_in > 0 and plan["area_sqft"] > 0:
            room = plan["area_sqft"] * freeboard_in / 12.0 * GAL_PER_CUBIC_FOOT
            if drain > room:
                out.append(Check(
                    "CASCADE_DRAINBACK", BLOCKER, "The lowest basin overflows at shutdown",
                    detail=f"{drain:.0f} gal drains back; {freeboard_in:g} in of freeboard over {plan['area_sqft']:.0f} sq ft holds {room:.0f} gal.",
                    remedy="Add freeboard to the lowest basin, enlarge it, or fit check valves on the risers.",
                    citation=CIT_DRAINBACK, section="basin", feature=fid,
                    value=round(drain), limit=round(room), unit="gal",
                ))
        elif drain > 0:
            mult = R.rule(rules, "freeboard_multiplier")
            rise_in = drain / GAL_PER_CUBIC_FOOT / plan["area_sqft"] * 12.0 if plan["area_sqft"] else 0.0
            out.append(Check(
                "CASCADE_FREEBOARD_UNSET", INFO, "Set the lowest basin's freeboard for drain-back",
                detail=f"Drain-back of {drain:.0f} gal raises it ~{rise_in:.1f} in; the house rule gives {rise_in * mult:.1f} in of freeboard.",
                remedy="The basin step sizes it when the basin is auto.",
                citation=R.citation("freeboard_multiplier", rules), section="basin", feature=fid,
            ))
    turnover = d.get("pond_turnover_h")
    if turnover is not None:
        limit = KOI_TURNOVER_H if v.get("koi") else POND_TURNOVER_H
        if turnover > limit:
            out.append(Check(
                "CASCADE_POND_TURNOVER", WARNING, "The pond turns over too slowly",
                detail=f"{turnover:.1f} h; {'koi want' if v.get('koi') else 'ponds want'} at least once every {limit:g} h.",
                remedy="Raise the flow (a heavier look) or add a separate circulation pump.",
                citation=CIT_TURNOVER, feature=fid, value=round(turnover, 2), limit=limit, unit="h",
            ))
    for t in d["tiers"]:
        if "rill" in str(feature_dict(feature).get("type") or "").lower() and t["width_in"] > RILL_MAX_IN:
            out.append(Check(
                "CASCADE_RILL_WIDTH", INFO, "Rill is wider than 12 in",
                detail=f"{t['width_in']:.0f} in. Crystal limits rills to 12 in.",
                citation=CIT_RILL, feature=fid, value=t["width_in"], limit=RILL_MAX_IN, unit="in",
            ))
    if len(d["tiers"]) > 1:
        out.append(Check(
            "CASCADE_GOVERNING_TIER", INFO, f"Tier {d['governing_tier'] or '(unnamed)'} sets the pump flow",
            detail="The same water runs over every tier; the others run heavier than their look.",
            feature=fid,
        ))
    return out


def terminal(feature: Any, derived: dict[str, Any] | None = None) -> dict[str, Any]:
    d = (derived or {}).get("flow") or flow(feature).data
    v = values(feature, inputs())
    return {"kind": "open_discharge",
            "params": {"lift_ft": num(v.get("top_elevation_ft")), "design_flow_gpm": d.get("gpm"), "residual_head_ft": 0.0}}
