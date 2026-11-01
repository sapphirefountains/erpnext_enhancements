"""Passive rain curtains, lace curtains and stream screens.

Industry report section 1.8. A passive curtain is strands or streams at a flow each. The
**digital** (valve-driven, graphical) curtain is designed end to end in Sapphire's Digital
Rain Curtain app; this family only links to it and contributes no flow of its own until the
app's design is brought across.

``derived`` keys read by :func:`checks`: ``flow``.
"""

from __future__ import annotations

from typing import Any

from ..envelope import make_input
from .base import (
    INFO,
    WARNING,
    Check,
    FamilyResult,
    count_of,
    feature_dict,
    flow_data,
    num,
    spec,
    values,
)

FAMILY = "curtain"
LABEL = "Rain curtain or water screen"
TYPES = ("Rain Curtain", "Lace Curtain", "Stream Screen", "Digital Rain Curtain")

CIT_STRAND = "https://watershapes.com/?p=27303 (about 0.33 gpm per strand)"
CIT_LACE = "https://www.water-garden.co.uk/prod/mylar-lace (0.5 L/min per strand, strands >= 35 mm apart)"
CIT_SCREEN = "https://watershapes.com/?p=32451 (48 streams/ft at 0.5 gpm/ft; double row 1 gpm/ft)"
CIT_ANGLE = "https://www.slideshare.net/slideshow/water-feature-types/5145948 (lace hangs up to 20 deg from vertical)"
CIT_HARDNESS = "https://www.usgs.gov/special-topics/water-science-school/science/hardness-water (over 180 mg/L is very hard)"

DRC_APP = {
    "label": "Digital Rain Curtain app",
    "repo": "sapphirefountains/digital-rain-curtain",
    "url": "https://github.com/sapphirefountains/digital-rain-curtain",
    "file": "dist/digital-rain-curtain.html",
}

STYLES = ("strand", "lace", "screen", "screen_double", "digital")
STRAND_GPM = 0.33
LACE_GPM = 0.5 / 3.785411784  # 0.5 L/min
LACE_MIN_SPACING_IN = 35.0 / 25.4
SCREEN_GPM_PER_FT = 0.5
SCREEN_DOUBLE_GPM_PER_FT = 1.0
SCREEN_STREAMS_PER_FT = 48
LACE_MAX_ANGLE_DEG = 20.0
VERY_HARD_PPM = 180.0


def inputs() -> list[dict[str, Any]]:
    return [
        spec("style", "Curtain", "select", "", "strand", True,
             "Digital curtains are designed in the Digital Rain Curtain app.", list(STYLES)),
        spec("width_ft", "Curtain width", "number", "ft", None, True),
        spec("strand_count", "Strands", "integer", "", None, False, "Blank: from width and spacing."),
        spec("spacing_in", "Strand spacing", "number", "in", None, False, "Needed when the strand count is blank."),
        spec("gpm_per_strand", "Flow per strand", "number", "gpm", None, False,
             "From the cut sheet; overrides the typical figure."),
        spec("angle_deg", "Hang angle from vertical", "number", "deg", 0.0),
        spec("softened_water", "Softened or RO make-up water", "bool", "", False),
    ]


def flow(feature: Any, site: dict[str, Any] | None = None, rules: dict[str, Any] | None = None) -> FamilyResult:
    v = values(feature, inputs())
    n = count_of(feature)
    style = str(v.get("style") or "strand").lower()
    width = num(v.get("width_ft"))
    if style == "digital":
        return FamilyResult(
            calc="curtain_family_flow", unit="GPM",
            steps=[f"digital curtain: design it in the {DRC_APP['label']} ({DRC_APP['url']})"],
            warnings=["A digital curtain's flow, manifold and valves come from the Digital Rain Curtain app."],
            data={"gpm": None, "style": style, "link": DRC_APP, "complete": True},
        )
    if width <= 0 and not (style in ("strand", "lace") and num(v.get("strand_count")) > 0):
        return FamilyResult(calc="curtain_family_flow", unit="GPM", warnings=["Enter the curtain width."],
                            data={"complete": False, "style": style})
    if style in ("screen", "screen_double"):
        per_ft = SCREEN_DOUBLE_GPM_PER_FT if style == "screen_double" else SCREEN_GPM_PER_FT
        gpm = per_ft * width
        steps = [f"stream screen: {per_ft:g} gpm/ft x {width:g} ft = {gpm:.2f} gpm ({SCREEN_STREAMS_PER_FT} streams/ft per row)"]
        cit, strands, each = CIT_SCREEN, None, None
    else:
        spacing = num(v.get("spacing_in"))
        strands = int(num(v.get("strand_count"))) or (int(width * 12.0 / spacing) + 1 if spacing > 0 else 0)
        if strands <= 0:
            return FamilyResult(calc="curtain_family_flow", unit="GPM",
                                warnings=["Enter the strand count or the strand spacing."],
                                data={"complete": False, "style": style})
        each = num(v.get("gpm_per_strand")) or (LACE_GPM if style == "lace" else STRAND_GPM)
        gpm = strands * each
        steps = [f"{strands} strands x {each:.3f} gpm = {gpm:.2f} gpm"]
        cit = CIT_LACE if style == "lace" else CIT_STRAND
    total = gpm * n
    return FamilyResult(
        calc="curtain_family_flow",
        value=total,
        unit="GPM",
        inputs={"style": make_input(style, "", "user"), "width_ft": make_input(width, "ft", "user")},
        formula="strands x gpm each, or gpm/ft x width (stream screens)",
        steps=steps,
        citations=[cit],
        data={"gpm": total, "gpm_each": gpm, "style": style, "strands": strands, "gpm_per_strand": each,
              "complete": True},
    )


def checks(feature: Any, derived: dict[str, Any] | None = None, site: dict[str, Any] | None = None,
           rules: dict[str, Any] | None = None) -> list[Check]:
    v = values(feature, inputs())
    fid = feature_dict(feature).get("id") or ""
    d = flow_data(flow, feature, derived, site, rules)
    out: list[Check] = []
    if d.get("style") == "digital":
        return [Check("CURTAIN_DIGITAL_APP", INFO, "Design this curtain in the Digital Rain Curtain app",
                      detail=f"{DRC_APP['repo']} ({DRC_APP['file']}).", citation=DRC_APP["url"], feature=fid)]
    if not d.get("complete"):
        return [Check("CURTAIN_INCOMPLETE", INFO, "Enter the curtain width", feature=fid)]
    if d["style"] == "lace" and 0 < num(v.get("spacing_in")) < LACE_MIN_SPACING_IN:
        out.append(Check(
            "CURTAIN_LACE_SPACING", WARNING, "Lace strands are closer than 35 mm",
            detail=f"{num(v.get('spacing_in')):g} in spacing; strands tangle below {LACE_MIN_SPACING_IN:.2f} in.",
            remedy="Space the strands at least 35 mm apart.", citation=CIT_LACE, feature=fid,
        ))
    if num(v.get("angle_deg")) > LACE_MAX_ANGLE_DEG:
        out.append(Check(
            "CURTAIN_ANGLE", WARNING, "Curtain hangs more than 20 deg off vertical",
            detail=f"{num(v.get('angle_deg')):g} deg.", remedy="Keep the hang within 20 deg of vertical.",
            citation=CIT_ANGLE, feature=fid,
        ))
    hardness = num((site or {}).get("hardness_ppm"))
    if hardness > VERY_HARD_PPM and not v.get("softened_water"):
        out.append(Check(
            "CURTAIN_SCALING", WARNING, "Hard water will scale the strands",
            detail=f"Site hardness {hardness:g} ppm; Wasatch Front supplies scale curtains quickly.",
            remedy="Use softened or RO make-up water and schedule descaling.",
            citation=CIT_HARDNESS, section="treatment", feature=fid, value=hardness, limit=VERY_HARD_PPM, unit="ppm",
        ))
    return out


def terminal(feature: Any, derived: dict[str, Any] | None = None) -> dict[str, Any]:
    d = (derived or {}).get("flow") or flow(feature).data
    if d.get("style") == "digital":
        return {"kind": "fixed_flow", "params": {"flow_gpm": None, "link": DRC_APP}}
    return {"kind": "open_discharge", "params": {"lift_ft": 0.0, "design_flow_gpm": d.get("gpm"), "residual_head_ft": 0.0}}
