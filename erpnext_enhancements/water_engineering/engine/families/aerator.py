"""Floating aerators and floating display fountains on ponds.

Industry report section 1.10. Horsepower per surface acre by purpose, the spray pattern
against the pond and its shoreline, several smaller units for a long narrow pond, and the
cable run to shore power (which the electrical step turns into a wire size and voltage
drop). A floating unit is self-contained: it is not a terminal on a pumped network.

``flow()`` returns the required horsepower (unit ``HP``); its ``data`` carries the gpm when a
unit with a published pattern flow is picked.

``derived`` keys read by :func:`checks`: ``flow``.
"""

from __future__ import annotations

import math
from typing import Any

from .. import rules as R
from ..envelope import CalcOption, make_input
from .base import (
    INFO,
    WARNING,
    Check,
    FamilyResult,
    feature_dict,
    flow_data,
    num,
    product_of,
    spec,
    values,
)

FAMILY = "aerator"
LABEL = "Floating aerator or fountain"
TYPES = ("Floating Aerator", "Floating Fountain", "Pond Aerator")

CIT_KASCO = (
    "https://cdn.shopify.com/s/files/1/2394/4493/files/Kasco_VFX_AF_JF_Fountain_Sizing_Chart_PDF.pdf "
    "(AF 1 HP per surface acre; JF display ~2 HP/acre; CF 3/4 HP/acre)"
)
CIT_OTTERBINE = "https://sweets.construction.com/swts_content_files/49654/P302633.htm (2 HP per surface acre for water quality) [snippet]"
CIT_SHAPE = "https://archive.lib.msu.edu/tic/wetrt/article/1986jul54.pdf (long, narrow ponds: several smaller units)"

HP_PER_ACRE = {"aeration": 1.0, "water_quality": 2.0, "display": 2.0, "circulation": 0.75}
HP_CITATION = {"aeration": CIT_KASCO, "water_quality": CIT_OTTERBINE, "display": CIT_KASCO, "circulation": CIT_KASCO}
SQFT_PER_ACRE = 43560.0


def inputs() -> list[dict[str, Any]]:
    return [
        spec("surface_acres", "Pond surface", "number", "acres", None, False, "Blank: from length and width."),
        spec("length_ft", "Pond length", "number", "ft", None),
        spec("width_ft", "Pond width", "number", "ft", None),
        spec("depth_ft", "Pond depth at the unit", "number", "ft", None),
        spec("purpose", "Purpose", "select", "", "aeration", True, "", list(HP_PER_ACRE)),
        spec("units", "Units", "integer", "", None, False, "Blank: from the pond's shape."),
        spec("unit_hp", "Horsepower per unit", "number", "HP", None),
        spec("pattern_diameter_ft", "Spray pattern diameter", "number", "ft", None, False, "From the unit's spec table."),
        spec("shoreline_setback_ft", "Clear water to keep from the shore", "number", "ft", None, False,
             "Spray must not reach the bank or the people on it."),
        spec("shore_power_ft", "Unit to shore power", "number", "ft", None, False, "Cable run on land, to the panel."),
    ]


def flow(feature: Any, site: dict[str, Any] | None = None, rules: dict[str, Any] | None = None) -> FamilyResult:
    v = values(feature, inputs())
    purpose = str(v.get("purpose") or "aeration").lower()
    if purpose not in HP_PER_ACRE:
        purpose = "aeration"
    L, W = num(v.get("length_ft")), num(v.get("width_ft"))
    acres = num(v.get("surface_acres")) or (L * W / SQFT_PER_ACRE if L > 0 and W > 0 else 0.0)
    if acres <= 0:
        return FamilyResult(calc="aerator_family_flow", unit="HP", warnings=["Enter the pond's surface acres or its size."],
                            data={"complete": False})
    band = HP_PER_ACRE[purpose]
    hp = acres * band
    steps = [f"{acres:.2f} acres x {band:g} HP/acre ({purpose}) = {hp:.2f} HP"]
    aspect = max(L, W) / min(L, W) if L > 0 and W > 0 else 1.0
    max_aspect = R.rule(rules, "aerator_max_aspect")
    units = int(num(v.get("units"))) or max(1, math.ceil(aspect / max_aspect))
    if units > 1:
        steps.append(f"length-to-width {aspect:.1f} over {max_aspect:g} -> {units} units of {hp / units:.2f} HP")
    # Cable: on-land run plus out to the unit, which sits mid-pond across the narrow side.
    cable = num(v.get("shore_power_ft")) + (min(L, W) / 2.0 if L > 0 and W > 0 else 0.0)
    if cable:
        steps.append(f"cable run ~{cable:.0f} ft (shore power + half the narrow side)")
    product = product_of(feature)
    gpm = num(product.get("gpm")) or None
    return FamilyResult(
        calc="aerator_family_flow",
        value=hp,
        unit="HP",
        inputs={"surface_acres": make_input(round(acres, 4), "acres", "user"), "purpose": make_input(purpose, "", "user")},
        formula="HP = acres x HP/acre(purpose); units = ceil(aspect / house max aspect)",
        steps=steps,
        citations=[HP_CITATION[purpose], CIT_SHAPE, R.citation("aerator_max_aspect", rules)],
        options=[CalcOption(key=p, label=p.replace("_", " ").title(), value=round(acres * b, 2), recommended=p == purpose)
                 for p, b in HP_PER_ACRE.items()],
        data={"hp_required": hp, "hp_per_acre": band, "acres": acres, "units": units, "hp_per_unit": hp / units,
              "aspect": aspect, "cable_run_ft": cable, "gpm": gpm, "purpose": purpose, "complete": True},
    )


def checks(feature: Any, derived: dict[str, Any] | None = None, site: dict[str, Any] | None = None,
           rules: dict[str, Any] | None = None) -> list[Check]:
    v = values(feature, inputs())
    fid = feature_dict(feature).get("id") or ""
    d = flow_data(flow, feature, derived, site, rules)
    if not d.get("complete"):
        return [Check("AERATOR_INCOMPLETE", INFO, "Enter the pond's size", feature=fid)]
    out: list[Check] = []
    unit_hp = num(v.get("unit_hp"))
    if unit_hp > 0:
        have = unit_hp * d["units"]
        if have < d["hp_required"] - 1e-9:
            out.append(Check(
                "AERATOR_HP_LOW", WARNING, "Horsepower is below the band for this purpose",
                detail=f"{have:g} HP on {d['acres']:.2f} acres = {have / d['acres']:.2f} HP/acre; "
                       f"{d['purpose'].replace('_', ' ')} wants {d['hp_per_acre']:g}.",
                remedy="Use a larger unit or add units.", citation=HP_CITATION[d["purpose"]], feature=fid,
                value=round(have, 2), limit=round(d["hp_required"], 2), unit="HP",
            ))
    pattern = num(v.get("pattern_diameter_ft"))
    W = min(num(v.get("length_ft")), num(v.get("width_ft"))) if num(v.get("width_ft")) else 0.0
    setback = num(v.get("shoreline_setback_ft"))
    if pattern > 0 and W > 0 and pattern + 2 * setback > W:
        out.append(Check(
            "AERATOR_PATTERN_SHORE", WARNING, "Spray pattern reaches the shoreline setback",
            detail=f"{pattern:g} ft pattern + 2 x {setback:g} ft setback in a {W:g} ft wide pond.",
            remedy="Pick a narrower pattern or a smaller unit.", feature=fid,
            value=pattern + 2 * setback, limit=W, unit="ft",
        ))
    if d["units"] == 1 and d["aspect"] > R.rule(rules, "aerator_max_aspect"):
        out.append(Check(
            "AERATOR_LONG_POND", WARNING, "One unit on a long, narrow pond",
            detail=f"Length-to-width {d['aspect']:.1f}.", remedy="Split the horsepower over several smaller units.",
            citation=CIT_SHAPE + "; " + R.citation("aerator_max_aspect", rules), feature=fid,
        ))
    if d["cable_run_ft"] > 0:
        out.append(Check(
            "AERATOR_CABLE", INFO, f"Cable run ~{d['cable_run_ft']:.0f} ft to shore power",
            detail="The electrical step sizes the cable for voltage drop over this run.",
            section="electrical", feature=fid, value=round(d["cable_run_ft"]), unit="ft",
        ))
    return out


def terminal(feature: Any, derived: dict[str, Any] | None = None) -> dict[str, Any]:
    d = (derived or {}).get("flow") or flow(feature).data
    return {"kind": "fixed_flow", "params": {"flow_gpm": d.get("gpm"), "self_contained": True,
                                             "hp": d.get("hp_required"), "cable_run_ft": d.get("cable_run_ft")}}
