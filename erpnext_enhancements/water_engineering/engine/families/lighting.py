"""Lighting: fixtures per effect, beam, transformer VA and the NEC 680 GFCI flag.

Industry report section 1.11. A **system module**, not a feature family: it reads the whole
schema-2 model (its features and their flow data) rather than one feature, so its interface
is ``inputs()``, ``calc(model, derived=None, rules=None)`` and
``checks(model, derived=None, rules=None)``. ``derived["flows"]`` maps a feature id to that
feature's ``flow().data``; features without one are computed here.

Fixture counts and beams are sourced (Crystal); watts per foot of jet or weir are not, so
they are Sapphire house rules and apply only until real fixtures are picked in
``model["lighting"]["fixtures"]``. ``controls.calc_lighting`` (60 W per 12 VDC relay,
DOC-0126) still sizes the relays; this module does not replace it.
"""

from __future__ import annotations

import math
from typing import Any

from .. import rules as R
from ..envelope import make_input
from . import jets
from .base import (
    BLOCKER,
    INFO,
    WARNING,
    Check,
    FamilyResult,
    feature_dict,
    num,
    spec,
)

FAMILY = "lighting"
LABEL = "Lighting"

CIT_CRYSTAL = (
    "https://www.slideshare.net/slideshow/lighting-5145934/5145934 (>= 2 lights on streams over 4 ft; narrow spot "
    "~13 deg over 10 ft, wide flood ~44 deg under; transformer VA = watts x 1.25)"
)
CIT_LUX = "https://saferain.com/en/faq (light a 10 m jet to >= 300 lux; a 75 W spot gives ~600 lux at 10 m)"
CIT_NEC = (
    "https://iaeimagazine.org/2013/septemberoctober-2013/fountains-and-reflection-pools/; "
    "https://www.ecmweb.com/code-basics/pools-and-spas-part-3-3 (NEC 680 Part V: GFCI unless listed low-voltage "
    "<= 15 V AC / 30 V DC per 680.23(A)(2); luminaire circuits <= 150 V). Verify against the edition the site's state enforces"
)

TWO_LIGHTS_OVER_FT = 4.0
NARROW_OVER_FT = 10.0
BEAMS = {"narrow": 13.0, "wide": 44.0}
VA_FACTOR = 1.25
TARGET_LUX = 300.0
MAX_LUMINAIRE_V = 150.0
LOW_V_AC = 15.0
LOW_V_DC = 30.0

JET_FAMILIES = ("jets", "foam", "laminar")
SHEET_FAMILIES = ("weir", "cascade", "curtain")


def inputs() -> list[dict[str, Any]]:
    return [
        spec("system_volts", "Lighting voltage", "number", "V", 12.0),
        spec("current", "AC or DC", "select", "", "AC", False, "", ["AC", "DC"]),
        spec("control", "Control", "select", "", "switched", False, "", ["switched", "DMX"]),
        spec("listed_low_voltage", "Fixtures listed for the low-voltage contact limit", "bool", "", False),
        spec("gfci", "GFCI protection provided", "bool", "", None),
        spec("transformer_va", "Transformer as designed", "number", "VA", None),
        spec("fixtures", "Fixtures", "table", "", [], False, "Picked fixtures; blank uses the house rules.",
             columns=[{"name": "item", "label": "Item", "kind": "text"},
                      {"name": "qty", "label": "Qty", "kind": "integer"},
                      {"name": "watts", "label": "Watts", "kind": "number", "unit": "W"},
                      {"name": "beam_deg", "label": "Beam", "kind": "number", "unit": "deg"},
                      {"name": "lumens", "label": "Lumens", "kind": "number", "unit": "lm"}]),
    ]


def beam_for_height(height_ft: float) -> tuple[str, float]:
    key = "narrow" if height_ft > NARROW_OVER_FT else "wide"
    return key, BEAMS[key]


def lumens_for(height_ft: float, beam_deg: float, lux: float = TARGET_LUX) -> float:
    """Lumens a spot of ``beam_deg`` needs to put ``lux`` on a target ``height_ft`` away
    (inverse square over the beam's solid angle; no losses, so a floor)."""
    d_m = max(height_ft, 0.0) * 0.3048
    omega = 2 * math.pi * (1 - math.cos(math.radians(beam_deg) / 2))
    return lux * d_m * d_m * omega


def effects(model: dict[str, Any], derived: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """One lighting effect per feature that takes light: jets by height, sheets by length."""
    from . import FAMILIES  # deferred: the registry imports this module

    flows = (derived or {}).get("flows") or {}
    site = model.get("site") or {}
    out = []
    for f in model.get("features") or []:
        f = feature_dict(f)
        fam = f.get("family")
        if fam not in JET_FAMILIES + SHEET_FAMILIES or fam not in FAMILIES:
            continue
        d = flows.get(f.get("id"))
        if fam in JET_FAMILIES:
            # Only the height is needed: read it without the flow's trajectory work.
            if d:
                height = num(d.get("height_ft"))
            elif fam == "laminar":
                height = num((f.get("inputs") or {}).get("arc_height_ft"))
            else:
                height = num(jets.nozzle_point(f, site).get("height_ft"))
            if height > 0:
                out.append({"feature": f.get("id"), "kind": "jet", "height_ft": height,
                            "count": int((d or {}).get("count") or f.get("count") or 1)})
            continue
        d = d or FAMILIES[fam].flow(f, site).data
        if not d.get("complete"):
            continue
        count = int(d.get("count") or f.get("count") or 1)
        if fam == "cascade":
            length = max((t["width_in"] for t in d.get("tiers") or []), default=0.0) / 12.0
        elif fam == "curtain":
            length = num((f.get("inputs") or {}).get("width_ft"))
        else:
            length = num(d.get("length_ft")) * count
        out.append({"feature": f.get("id"), "kind": "sheet", "length_ft": length, "count": 1})
    return out


def calc(model: dict[str, Any], derived: dict[str, Any] | None = None, rules: dict[str, Any] | None = None) -> FamilyResult:
    light = model.get("lighting") or {}
    fixture_w = R.rule(rules, "light_fixture_watts")
    w_jet = R.rule(rules, "light_watts_per_ft_jet")
    w_weir = R.rule(rules, "light_watts_per_ft_weir")
    rows, steps = [], []
    for e in effects(model, derived):
        if e["kind"] == "jet":
            h = e["height_ft"]
            per = 2 if h > TWO_LIGHTS_OVER_FT else 1
            beam_key, beam = beam_for_height(h)
            watts_each = max(per * fixture_w, h * w_jet)
            n_fix = max(per, math.ceil(watts_each / fixture_w)) * e["count"]
            rows.append({"feature": e["feature"], "kind": "jet", "fixtures": n_fix, "watts": n_fix * fixture_w,
                         "beam": beam_key, "beam_deg": beam, "lumens_each_jet": lumens_for(h, beam)})
            steps.append(f"{e['feature']}: {e['count']} jet(s) at {h:g} ft -> {n_fix} x {fixture_w:g} W, {beam_key} "
                         f"{beam:g} deg, ~{lumens_for(h, beam):.0f} lm per jet for {TARGET_LUX:g} lux")
        else:
            watts = e["length_ft"] * w_weir
            n_fix = max(1, math.ceil(watts / fixture_w)) if e["length_ft"] > 0 else 0
            rows.append({"feature": e["feature"], "kind": "sheet", "fixtures": n_fix, "watts": n_fix * fixture_w,
                         "beam": "wide", "beam_deg": BEAMS["wide"]})
            steps.append(f"{e['feature']}: {e['length_ft']:g} ft of sheet x {w_weir:g} W/ft -> {n_fix} x {fixture_w:g} W")
    picked = [fx for fx in (light.get("fixtures") or []) if num(fx.get("watts")) > 0]
    if picked:
        total_w = sum(num(fx.get("watts")) * max(int(num(fx.get("qty"), 1)), 1) for fx in picked)
        n_total = sum(max(int(num(fx.get("qty"), 1)), 1) for fx in picked)
        steps.append(f"picked fixtures: {n_total} totalling {total_w:g} W")
        source = "picked"
    else:
        total_w = sum(r["watts"] for r in rows)
        n_total = sum(r["fixtures"] for r in rows)
        source = "house_rule"
    va = total_w * VA_FACTOR
    steps.append(f"transformer >= {total_w:g} W x {VA_FACTOR:g} = {va:.0f} VA")
    citations = [CIT_CRYSTAL, CIT_LUX, CIT_NEC]
    if source == "house_rule":
        citations.append(R.citation("light_watts_per_ft_jet", rules))
    return FamilyResult(
        calc="lighting_family",
        value=total_w,
        unit="W",
        inputs={"fixture_watts": make_input(fixture_w, "W", "default", R.citation("light_fixture_watts", rules)),
                "source": make_input(source, "", "prior_calc")},
        formula="jets: >= 2 fixtures over 4 ft, max(fixtures x W, height x W/ft); sheets: length x W/ft; VA = W x 1.25",
        steps=steps,
        citations=citations,
        data={"effects": rows, "fixtures": n_total, "watts": total_w, "va_required": va, "source": source},
    )


def checks(model: dict[str, Any], derived: dict[str, Any] | None = None, rules: dict[str, Any] | None = None) -> list[Check]:
    light = model.get("lighting") or {}
    d = (derived or {}).get("lighting") or calc(model, derived, rules).data
    out: list[Check] = []
    if not d["watts"]:
        return out
    volts = num(light.get("system_volts"), 12.0)
    dc = str(light.get("current") or "AC").upper() == "DC"
    if volts > MAX_LUMINAIRE_V:
        out.append(Check(
            "LIGHT_OVER_150V", BLOCKER, "Underwater luminaire circuit over 150 V",
            detail=f"{volts:g} V.", remedy="Use 120 V or low-voltage fixtures.", section="lighting",
            citation=CIT_NEC, value=volts, limit=MAX_LUMINAIRE_V, unit="V",
        ))
    low = volts <= (LOW_V_DC if dc else LOW_V_AC) and bool(light.get("listed_low_voltage"))
    if not low and light.get("gfci") is not True:
        out.append(Check(
            "LIGHT_GFCI", WARNING, "Fountain lights need GFCI protection",
            detail=f"{volts:g} V {'DC' if dc else 'AC'} fixtures"
                   + ("" if light.get("listed_low_voltage") else ", not listed at the low-voltage contact limit") + ".",
            remedy="Put the lighting on a GFCI, or use listed fixtures at or below 15 V AC / 30 V DC.",
            section="electrical", citation=CIT_NEC,
        ))
    if low and dc:
        out.append(Check(
            "LIGHT_GFCI_DC", INFO, "Confirm GFCI on 24 V DC fixtures with the inspector",
            detail="Inspectors differ on whether listed DC fixtures still need GFCI.", section="electrical",
            citation=CIT_NEC,
        ))
    have = num(light.get("transformer_va"))
    if have > 0 and have < d["va_required"] - 1e-9:
        out.append(Check(
            "LIGHT_TRANSFORMER", WARNING, "Transformer is under 1.25 x the lighting load",
            detail=f"{have:g} VA for {d['watts']:g} W; needs {d['va_required']:.0f} VA.",
            remedy="Use the next larger transformer, or split the load.", section="lighting",
            citation=CIT_CRYSTAL, value=have, limit=round(d["va_required"]), unit="VA",
        ))
    if d["source"] == "house_rule":
        out.append(Check(
            "LIGHT_HOUSE_RULE", INFO, "Lighting sized from the house rules, not picked fixtures",
            detail=f"{d['fixtures']} fixtures, {d['watts']:g} W.",
            remedy="Pick the fixtures to replace the estimate.", section="lighting",
            citation=R.citation("light_watts_per_ft_jet", rules),
        ))
    return out
