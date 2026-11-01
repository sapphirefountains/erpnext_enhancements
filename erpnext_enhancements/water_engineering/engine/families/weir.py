"""Weirs, sheet falls, waterwalls, spillways, vanishing edges and slot overflows.

Industry report section 1.1. Francis stays the governing formula through
:func:`..feature.weir_flow` (DOC-0049 ``I - Weir``); the trade's planning flows sit on top of
it as a floor that Francis can only raise. ``derived`` keys read by :func:`checks`:
``flow``, ``basin`` (the catch basin; its width is the catch width when none is entered) and
``trough_volume_gal`` (the trough or surge volume available, when the basin step knows it).
"""

from __future__ import annotations

import math
from typing import Any

from .. import rules as R
from ..envelope import CalcOption, make_input
from ..feature import weir_flow
from .base import (
    BLOCKER,
    G_FT_S2,
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

FAMILY = "weir"
LABEL = "Weir, sheet fall or vanishing edge"
TYPES = (
    "Weir", "Spilling Weir", "Waterwall", "Sheet Fall", "Vanishing Edge", "Slot Overflow", "Spillway",
)

CIT_FRANCIS = "https://watershapes.com/over-the-edge/ (Francis: Q = 36 L h^1.5 - 0.3 n h^2.5; 1/4 in ~ 4.5 gpm/ft)"
CIT_PLANNING = "https://www.poolspamarketing.com/trade/water-feature-hydraulics/2/ (square edge 7-10 gpm/ft at 1/4 in; 36 at 1 in)"
CIT_VANISH = "https://watershapes.com/over-the-edge/ (vanishing edge ~5 gpm/ft; slot overflow ~2 gpm/ft)"
CIT_TOLERANCE = "https://www.poolspamarketing.com/trade/water-feature-hydraulics/2/ (1/16 in tolerance needs 1/8 in head; 1/4 needs 1/2; rounded crest >= 1 in)"
CIT_BUILD_TOL = "https://watershapes.com/water-in-transit/ (edge built to +/-1/16 in; 20 ft minimum vanishing edge)"
CIT_PRODUCTS = (
    "https://www.epoolsupply.com/blogs/default-blog/jandy-sheer-descent-sheer-rain-water-features-comprehensive-guide "
    "(Jandy Sheer Descent ~12 gpm/ft, 5-15; Pentair MagicFalls 8-10) [snippet]"
)
CIT_BREAKUP = (
    "https://pondprodirect.com/products/blue-thumb-stainless-medium-formal-falls-28 (sheet breaks apart past 24 in); "
    "https://manualmachine.com/starite/magicfalls/5275295-installation--manual (MagicFalls sheet <= 1 m) [snippet]"
)
CIT_LANDING = "https://www.poolspamarketing.com/trade/water-feature-hydraulics/2/ (1 m fall: 6 mm sheet lands ~0.3 m out, 25 mm ~0.7 m)"
CIT_CATCH = "https://watershapes.com/?p=49059 (catch-basin width about equal to the fall, 1:1)"
CIT_SURGE = (
    "https://watershapes.com/water-in-transit/ (2 in of surge over the upper surface; 3 in for small busy pools); "
    "https://www.poolspamarketing.com/trade/water-feature-hydraulics/2/ (add raised-spa volume and >= 1 ft vortex reserve)"
)
CIT_WIND = "https://watershapes.com/water-in-transit/ (sheets above ~3 ft need wind control)"

STYLES = ("waterwall", "spillway", "vanishing_edge", "slot_overflow", "sheet_product")
LOOKS = ("light", "average", "heavy")

# Square / sharp edge planning flows by look: (head over the lip in, planning gpm per ft).
# Light and average are the two ends of the 7-10 gpm/ft band at 1/4 in; heavy is 36 at 1 in.
LOOK_BANDS = {"light": (0.25, 7.0), "average": (0.25, 10.0), "heavy": (1.0, 36.0)}
VANISHING_GPM_PER_FT = 5.0
SLOT_GPM_PER_FT = 2.0
PRODUCT_GPM_PER_FT = {"Jandy Sheer Descent": 12.0, "Pentair MagicFalls": 10.0}
PRODUCT_DEFAULT_GPM_PER_FT = 12.0

TOLERANCE_HEAD_RATIO = 2.0  # head over the lip = 2 x edge tolerance
ROUNDED_MIN_HEAD_IN = 1.0
BUILD_TOLERANCE_IN = 1.0 / 16.0
VANISHING_MIN_FT = 20.0
SHEET_BREAKUP_FT = 2.0  # 24 in
WIND_CONTROL_FALL_FT = 3.0
CATCH_RATIO = 1.0
SURGE_IN = 2.0
SURGE_BUSY_IN = 3.0
VORTEX_RESERVE_FT = 1.0

# Landing distance fitted through the two P&S points at a 1 m fall (x = a t^b, t in mm),
# scaled with sqrt(fall) because a level sheet's carry grows with its fall time.
_LAND_B = math.log(0.7 / 0.3) / math.log(25.0 / 6.0)
_LAND_A = 0.3 / 6.0**_LAND_B
MM_PER_IN = 25.4


def inputs() -> list[dict[str, Any]]:
    return [
        spec("style", "Edge style", "select", "", "waterwall", True,
             "Waterwall or spillway sheet, vanishing edge, slot overflow, or a sheet-fall product.",
             list(STYLES)),
        spec("length_ft", "Edge length", "number", "ft", None, True, "Wetted length of the lip."),
        spec("lip", "Lip shape", "select", "", "square", False, "A rounded crest needs at least 1 in of head.",
             ["sharp", "square", "rounded"]),
        spec("look", "Sheet look", "select", "", "average", False,
             "Light and average are the two ends of the 7-10 gpm/ft band at 1/4 in; heavy is 1 in.", list(LOOKS)),
        spec("head_in", "Head over the lip", "number", "in", None, False,
             "Leave blank to take it from the look and the edge tolerance."),
        spec("tolerance_in", "Edge tolerance", "number", "in", BUILD_TOLERANCE_IN, False,
             "How far the lip is out of level end to end."),
        spec("contractions", "End contractions", "integer", "", 2, False, "Francis n (0, 1 or 2)."),
        spec("fall_ft", "Fall height", "number", "ft", None, False, "Lip to catch-basin water."),
        spec("catch_width_in", "Catch-basin width", "number", "in", None, False,
             "Blank uses the catch basin's width."),
        spec("lip_texture", "Lip texture", "select", "", "smooth", False,
             "A rain or textured lip lets a tall fall break up on purpose.", ["smooth", "rain", "textured"]),
        spec("free_fall", "Free-falling sheet", "bool", "", None, False,
             "Blank: yes, except a waterwall, whose water runs down the wall face."),
        spec("indoor", "Indoors", "bool", "", False, False, "No wind checks indoors."),
        spec("wind_control", "Wind control fitted", "bool", "", False),
        spec("product", "Sheet-fall product", "select", "", None, False, "For the sheet-fall product style.",
             list(PRODUCT_GPM_PER_FT)),
        spec("product_gpm_per_ft", "Product flow per foot", "number", "gpm/ft", None, False,
             "From the product's cut sheet; overrides the typical figure."),
        spec("upper_area_sqft", "Upper pool surface", "number", "sq ft", None, False,
             "Vanishing edges: the surface whose surge the trough holds."),
        spec("spa_volume_gal", "Raised spa volume", "number", "gal", 0.0, False,
             "A raised spa that drains into the trough adds its volume."),
        spec("busy_pool", "Small, busy pool", "bool", "", False, False, "Uses 3 in of surge instead of 2."),
        spec("trough_area_sqft", "Trough plan area", "number", "sq ft", None, False,
             "For the 1 ft vortex reserve over the suction. Blank uses the catch basin."),
    ]


def francis_gpm_per_ft(head_in: float, length_ft: float = 1.0, contractions: int = 2) -> float:
    """Francis flow per foot of crest at ``head_in`` (via the DOC-0049 ``weir_flow``)."""
    if length_ft <= 0 or head_in <= 0:
        return 0.0
    return weir_flow(length_ft, head_in, contractions).value / length_ft


def tolerance_head_in(tolerance_in: float, lip: str = "square") -> float:
    """Head over the lip an edge built to ``tolerance_in`` needs to sheet end to end."""
    head = TOLERANCE_HEAD_RATIO * max(tolerance_in, 0.0)
    if (lip or "").lower() == "rounded":
        head = max(head, ROUNDED_MIN_HEAD_IN)
    return head


def landing_distance_ft(head_in: float, fall_ft: float) -> float:
    """How far out a sheet of thickness ~``head_in`` lands after falling ``fall_ft``."""
    if head_in <= 0 or fall_ft <= 0:
        return 0.0
    t_mm = head_in * MM_PER_IN
    x_m = _LAND_A * t_mm**_LAND_B * math.sqrt(fall_ft * 0.3048 / 1.0)
    return x_m / 0.3048


def surge_gal(upper_area_sqft: float, busy: bool = False, spa_gal: float = 0.0,
              trough_area_sqft: float = 0.0) -> dict[str, float]:
    """Trough volume a vanishing edge needs: 2 in (3 in busy) over the upper surface, plus a
    raised spa, plus 1 ft of vortex reserve over the trough."""
    depth_in = SURGE_BUSY_IN if busy else SURGE_IN
    surge = max(upper_area_sqft, 0.0) * depth_in / 12.0 * GAL_PER_CUBIC_FOOT
    vortex = max(trough_area_sqft, 0.0) * VORTEX_RESERVE_FT * GAL_PER_CUBIC_FOOT
    return {
        "surge_in": depth_in,
        "surge_gal": surge,
        "spa_gal": max(spa_gal, 0.0),
        "vortex_gal": vortex,
        "total_gal": surge + max(spa_gal, 0.0) + vortex,
    }


def flow(feature: Any, site: dict[str, Any] | None = None, rules: dict[str, Any] | None = None) -> FamilyResult:
    v = values(feature, inputs())
    n = count_of(feature)
    style = str(v.get("style") or "waterwall").lower()
    lip = str(v.get("lip") or "square").lower()
    look = str(v.get("look") or "average").lower()
    if look not in LOOK_BANDS:
        look = "average"
    length = num(v.get("length_ft"))
    contractions = int(num(v.get("contractions"), 2))
    tolerance = num(v.get("tolerance_in"), BUILD_TOLERANCE_IN)
    pinned_head = num(v.get("head_in"))
    citations = [CIT_FRANCIS, CIT_TOLERANCE]
    warnings: list[str] = []
    steps: list[str] = []

    if length <= 0:
        return FamilyResult(
            calc="weir_family_flow", unit="GPM", citations=citations,
            warnings=["Enter the edge length to size the weir."],
            data={"gpm": None, "complete": False},
        )

    req_head = tolerance_head_in(tolerance, lip)
    steps.append(
        f"edge tolerance {tolerance:g} in -> head >= {TOLERANCE_HEAD_RATIO:g} x {tolerance:g} = "
        f"{TOLERANCE_HEAD_RATIO * tolerance:g} in" + (f"; rounded crest -> >= {ROUNDED_MIN_HEAD_IN:g} in" if lip == "rounded" else "")
    )

    if style == "vanishing_edge":
        planning, look_head, plan_src = VANISHING_GPM_PER_FT, 0.0, CIT_VANISH
    elif style == "slot_overflow":
        planning, look_head, plan_src = SLOT_GPM_PER_FT, 0.0, CIT_VANISH
    elif style == "sheet_product":
        product = v.get("product") or ""
        planning = num(v.get("product_gpm_per_ft")) or PRODUCT_GPM_PER_FT.get(product, PRODUCT_DEFAULT_GPM_PER_FT)
        look_head, plan_src = 0.0, CIT_PRODUCTS
        if not num(v.get("product_gpm_per_ft")) and product not in PRODUCT_GPM_PER_FT:
            warnings.append(
                f"No sheet-fall product picked: using the typical {PRODUCT_DEFAULT_GPM_PER_FT:g} gpm/ft "
                "(Jandy Sheer Descent). Pick the product and enter its cut-sheet flow."
            )
    else:
        look_head, planning = LOOK_BANDS[look]
        plan_src = CIT_PLANNING
    citations.append(plan_src)

    if pinned_head > 0:
        head = pinned_head
        steps.append(f"head pinned at {head:g} in")
        francis = francis_gpm_per_ft(head, length, contractions)
        gpm_ft = francis
        steps.append(f"Francis at {head:g} in = {francis:.2f} gpm/ft (pinned head; look band not applied)")
    else:
        head = max(look_head, req_head)
        francis = francis_gpm_per_ft(head, length, contractions)
        gpm_ft = max(francis, planning)
        steps.append(f"head = max(look {look_head:g}, tolerance {req_head:g}) = {head:g} in")
        steps.append(
            f"gpm/ft = max(Francis {francis:.2f}, planning {planning:g}) = {gpm_ft:.2f}  "
            "(Francis is the floor; the trade's planning flow governs above it)"
        )

    total = gpm_ft * length * n
    steps.append(f"Q = {gpm_ft:.2f} gpm/ft x {length:g} ft x {n} = {total:.1f} GPM")

    fall = num(v.get("fall_ft"))
    landing = landing_distance_ft(head, fall)
    fall_s = math.sqrt(2.0 * fall / G_FT_S2) if fall > 0 else 0.0
    in_flight = total / 60.0 * fall_s
    if fall > 0:
        steps.append(
            f"sheet ~{head:g} in thick falling {fall:g} ft lands ~{landing:.2f} ft out; "
            f"catch width >= max(1:1 fall {fall * CATCH_RATIO:g} ft, landing) ; in flight {in_flight:.1f} gal"
        )
        citations += [CIT_LANDING, CIT_CATCH]

    data = {
        "gpm": total,
        "gpm_each": gpm_ft * length,
        "gpm_per_ft": gpm_ft,
        "francis_gpm_per_ft": francis,
        "planning_gpm_per_ft": planning,
        "head_in": head,
        "required_head_in": req_head,
        "head_pinned": pinned_head > 0,
        "length_ft": length,
        "style": style,
        "look": look,
        "fall_ft": fall,
        "landing_ft": landing,
        "catch_width_required_ft": max(fall * CATCH_RATIO, landing),
        "fall_time_s": fall_s,
        "in_flight_gal": in_flight,
        "complete": True,
    }

    upper = num(v.get("upper_area_sqft"))
    if style == "vanishing_edge" or upper > 0:
        s = surge_gal(upper, bool(v.get("busy_pool")), num(v.get("spa_volume_gal")), num(v.get("trough_area_sqft")))
        data["surge"] = s
        steps.append(
            f"trough: {s['surge_in']:g} in over {upper:g} sq ft = {s['surge_gal']:.0f} gal + spa "
            f"{s['spa_gal']:.0f} + vortex reserve {s['vortex_gal']:.0f} = {s['total_gal']:.0f} gal"
        )
        citations.append(CIT_SURGE)

    options = []
    if style in ("waterwall", "spillway") and pinned_head <= 0:
        for lk in LOOKS:
            lk_head, lk_plan = LOOK_BANDS[lk]
            lk_ft = max(francis_gpm_per_ft(max(lk_head, req_head), length, contractions), lk_plan)
            options.append(CalcOption(
                key=lk, label=f"{lk.title()} sheet", value=round(lk_ft * length * n, 1),
                recommended=(lk == look), detail={"head_in": lk_head, "gpm_per_ft": round(lk_ft, 2)},
            ))

    return FamilyResult(
        calc="weir_family_flow",
        value=total,
        unit="GPM",
        inputs={
            "length_ft": make_input(length, "ft", "user"),
            "style": make_input(style, "", "user"),
            "look": make_input(look, "", "user"),
            "tolerance_in": make_input(tolerance, "in", "user"),
            "head_in": make_input(head, "in", "user" if pinned_head > 0 else "prior_calc"),
            "count": make_input(n, "", "user"),
        },
        formula="gpm/ft = max(Francis(h), planning band); h = max(look head, 2 x tolerance); Q = gpm/ft x L x count",
        steps=steps,
        citations=citations,
        warnings=warnings,
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
        return [Check("WEIR_INCOMPLETE", INFO, "Enter the edge length to size the weir", feature=fid)]

    tolerance = num(v.get("tolerance_in"), BUILD_TOLERANCE_IN)
    if d["head_pinned"] and d["head_in"] < d["required_head_in"]:
        out.append(Check(
            "WEIR_HEAD_BELOW_TOLERANCE", WARNING,
            "Head over the lip is below what the edge tolerance needs",
            detail=f"{d['head_in']:g} in of head on an edge out by {tolerance:g} in; it needs {d['required_head_in']:g} in "
                   f"({francis_gpm_per_ft(d['required_head_in']):.1f} gpm/ft) or the sheet breaks at the high end.",
            remedy="Raise the head (more flow) or build the lip to a tighter tolerance.",
            citation=CIT_TOLERANCE, feature=fid, value=d["head_in"], limit=d["required_head_in"], unit="in",
        ))
    if d["head_pinned"] and d["style"] in ("waterwall", "spillway") and d["gpm_per_ft"] < d["planning_gpm_per_ft"]:
        out.append(Check(
            "WEIR_BELOW_LOOK_BAND", INFO, f"Flow is below the {d['look']} sheet planning band",
            detail=f"{d['gpm_per_ft']:.1f} gpm/ft against the {d['planning_gpm_per_ft']:g} gpm/ft the trade plans for.",
            remedy="Expect a thinner sheet than the look, or clear the pinned head.",
            citation=CIT_PLANNING, feature=fid, value=d["gpm_per_ft"], limit=d["planning_gpm_per_ft"], unit="gpm/ft",
        ))
    if tolerance > BUILD_TOLERANCE_IN + 1e-9:
        out.append(Check(
            "WEIR_TOLERANCE", WARNING, "Edge tolerance is looser than +/-1/16 in",
            detail=f"{tolerance:g} in end to end. It costs head ({d['required_head_in']:g} in) and flow to cover it.",
            remedy="Specify the lip to +/-1/16 in (a stainless or set-stone edge).",
            citation=CIT_BUILD_TOL, feature=fid, value=tolerance, limit=BUILD_TOLERANCE_IN, unit="in",
        ))
    if d["style"] == "vanishing_edge" and d["length_ft"] < VANISHING_MIN_FT:
        out.append(Check(
            "WEIR_VANISHING_SHORT", WARNING, "Vanishing edge is shorter than 20 ft",
            detail=f"{d['length_ft']:g} ft. The trade treats 20 ft as the minimum vanishing edge.",
            remedy="Lengthen the edge or treat it as a spillway.",
            citation=CIT_BUILD_TOL, feature=fid, value=d["length_ft"], limit=VANISHING_MIN_FT, unit="ft",
        ))
    fall = d["fall_ft"]
    free = v.get("free_fall")
    free = d["style"] != "waterwall" if free is None else bool(free)
    texture = str(v.get("lip_texture") or "smooth").lower()
    if free and fall > SHEET_BREAKUP_FT and texture == "smooth":
        out.append(Check(
            "WEIR_SHEET_BREAKUP", WARNING, "A smooth sheet this tall will break apart",
            detail=f"{fall:g} ft of drop; smooth sheets break up past about 24 in (1 m for MagicFalls).",
            remedy="Use a rain or textured lip, or shorten the fall.",
            citation=CIT_BREAKUP, feature=fid, value=fall, limit=SHEET_BREAKUP_FT, unit="ft",
        ))
    if fall > WIND_CONTROL_FALL_FT and not v.get("wind_control") and not v.get("indoor"):
        out.append(Check(
            "WEIR_WIND_CONTROL", WARNING, "A sheet over 3 ft needs wind control",
            detail=f"{fall:g} ft fall with no wind control.",
            remedy=f"Fit a two-stage anemometer control (reduce at {R.rule(rules, 'wind_reduce_mph'):g} mph, "
                   f"stop at {R.rule(rules, 'wind_stop_mph'):g} mph).",
            citation=CIT_WIND + "; setpoints: " + R.citation("wind_reduce_mph", rules),
            section="control", feature=fid, value=fall, limit=WIND_CONTROL_FALL_FT, unit="ft",
        ))
    if free and fall > 0:
        catch_in = num(v.get("catch_width_in"))
        if catch_in <= 0 and derived and derived.get("basin"):
            catch_in = basin_plan(derived["basin"])["width_ft"] * 12.0
        if catch_in > 0:
            catch_ft = catch_in / 12.0
            if catch_ft < d["landing_ft"]:
                out.append(Check(
                    "WEIR_LANDS_OUTSIDE", BLOCKER, "The sheet lands outside the catch basin",
                    detail=f"Catch {catch_ft:.2f} ft; the sheet lands ~{d['landing_ft']:.2f} ft out.",
                    remedy="Widen the catch basin or cut the flow per foot.",
                    citation=CIT_LANDING, section="basin", feature=fid,
                    value=round(catch_ft, 2), limit=round(d["landing_ft"], 2), unit="ft",
                ))
            elif catch_ft < fall * CATCH_RATIO:
                out.append(Check(
                    "WEIR_CATCH_NARROW", WARNING, "Catch basin is narrower than the fall",
                    detail=f"Catch {catch_ft:.2f} ft for a {fall:g} ft fall; the trade sizes it 1:1 to hold splash.",
                    remedy="Widen the catch basin toward the fall height where the site allows.",
                    citation=CIT_CATCH, section="basin", feature=fid,
                    value=round(catch_ft, 2), limit=fall * CATCH_RATIO, unit="ft",
                ))
    surge = d.get("surge")
    have = num((derived or {}).get("trough_volume_gal"))
    if surge and have > 0 and have < surge["total_gal"]:
        out.append(Check(
            "WEIR_TROUGH_SMALL", WARNING, "Trough is smaller than the surge rule",
            detail=f"{have:.0f} gal available; {surge['total_gal']:.0f} gal needed "
                   f"({surge['surge_in']:g} in surge + spa + 1 ft vortex reserve).",
            remedy="Deepen or lengthen the trough.",
            citation=CIT_SURGE, section="basin", feature=fid,
            value=round(have), limit=round(surge["total_gal"]), unit="gal",
        ))
    return out


def terminal(feature: Any, derived: dict[str, Any] | None = None) -> dict[str, Any]:
    """An open discharge: the pump lifts water to the crest and it leaves at atmosphere.
    ``lift_ft`` defaults to the fall height; the network node's elevation governs when set."""
    d = (derived or {}).get("flow") or flow(feature).data
    v = values(feature, inputs())
    return {
        "kind": "open_discharge",
        "params": {
            "lift_ft": num(v.get("fall_ft")),
            "design_flow_gpm": d.get("gpm"),
            "residual_head_ft": 0.0,
        },
    }
