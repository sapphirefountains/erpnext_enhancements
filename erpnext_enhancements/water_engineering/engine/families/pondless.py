"""Pondless waterfalls, urns and bubblers: the kit-shaped features.

Industry report section 1.7. Almost no engineering: the stream's flow comes from its
spillway width (125 GPH per inch, the cascade rule), its standing volume is W x L x D, and
the hidden reservoir must hold a multiple of that volume set by what fills it. The output
that matters is the **kit**: a short list of BOM lines with quantities.

``derived`` keys read by :func:`checks`: ``flow``.
"""

from __future__ import annotations

from typing import Any

from .. import rules as R
from ..envelope import make_input
from .base import (
    GAL_PER_CUBIC_FOOT,
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
from .cascade import CIT_AQUASCAPE, GPH_PER_INCH, liner_size_ft

FAMILY = "pondless"
LABEL = "Pondless waterfall, urn or bubbler"
TYPES = ("Pondless Waterfall", "Urn", "Bubbler", "Disappearing Fountain")

CIT_STREAM = "https://best-planet.com/pages/pump-sizing-for-pondless-systems (stream W x L x D x 7.5 gal; reservoir >= 3x)"
CIT_FILL = (
    "https://www.pondtrademag.com/sizing-gravel-filled-and-water-matrix-reservoirs-for-pondless-features/ "
    "(>= 4x the dynamic volume with water-matrix blocks; >= 12x gravel-filled)"
)

RESERVOIR_MULTIPLIER = {"open": 3.0, "matrix": 4.0, "gravel": 12.0}
KINDS = ("stream", "urn", "bubbler")


def inputs() -> list[dict[str, Any]]:
    return [
        spec("kind", "Kit", "select", "", "stream", True, "", list(KINDS)),
        spec("spill_width_in", "Spillway width", "number", "in", None, False, "Streams: sets the flow at 125 GPH per inch."),
        spec("stream_length_ft", "Stream length", "number", "ft", None),
        spec("stream_width_ft", "Stream width", "number", "ft", None),
        spec("stream_depth_in", "Standing depth in the stream", "number", "in", None),
        spec("dynamic_gal", "Water that drains back at shutdown", "number", "gal", None, False,
             "Urns and bubblers: the water in the urn, its basin and the plumbing."),
        spec("urn_gph", "Urn or bubbler flow", "number", "GPH", None, False, "From the urn's cut sheet."),
        spec("fill", "Reservoir fill", "select", "", "matrix", False,
             "Open tank 3x, water-matrix blocks 4x, gravel 12x.", list(RESERVOIR_MULTIPLIER)),
        spec("reservoir_gal", "Reservoir as designed", "number", "gal", None),
    ]


def flow(feature: Any, site: dict[str, Any] | None = None, rules: dict[str, Any] | None = None) -> FamilyResult:
    v = values(feature, inputs())
    n = count_of(feature)
    kind = str(v.get("kind") or "stream").lower()
    fill = str(v.get("fill") or "matrix").lower()
    mult = RESERVOIR_MULTIPLIER.get(fill, RESERVOIR_MULTIPLIER["matrix"])
    steps, citations = [], [CIT_STREAM, CIT_FILL]
    width_in = num(v.get("spill_width_in")) or num(v.get("stream_width_ft")) * 12.0
    if kind == "stream":
        gph = GPH_PER_INCH * width_in
        steps.append(f"stream: {width_in:g} in x {GPH_PER_INCH:g} GPH/in = {gph:.0f} GPH")
        citations.append(CIT_AQUASCAPE)
    else:
        gph = num(v.get("urn_gph"))
        steps.append(f"{kind}: {gph:g} GPH from the cut sheet")
    if gph <= 0:
        return FamilyResult(calc="pondless_family_flow", unit="GPM", citations=citations,
                            warnings=["Enter the spillway width (stream) or the urn's flow."],
                            data={"complete": False})
    L, W = num(v.get("stream_length_ft")), num(v.get("stream_width_ft"))
    D_in = num(v.get("stream_depth_in"))
    stream_gal = L * W * D_in / 12.0 * GAL_PER_CUBIC_FOOT if kind == "stream" else 0.0
    dynamic = stream_gal if kind == "stream" else num(v.get("dynamic_gal"))
    reservoir = mult * dynamic if dynamic > 0 else None
    warnings = []
    if reservoir is None:
        warnings.append("Enter the stream's length, width and standing depth (or the urn's drain-back) to size the reservoir.")
    else:
        steps.append(f"dynamic volume {dynamic:.0f} gal x {mult:g} ({fill}) = reservoir {reservoir:.0f} gal")
    total_gpm = gph / 60.0 * n
    overlap = R.rule(rules, "liner_overlap_ft")
    liner = liner_size_ft(L, W, D_in / 12.0, overlap) if L > 0 and W > 0 else None
    kit = [
        {"role": "pump", "description": f"Pondless pump, {gph:.0f} GPH at the stream head", "qty": n, "uom": "Nos"},
        {"role": "reservoir", "description": f"Reservoir storage ({fill})",
         "qty": round(reservoir) if reservoir else None, "uom": "gal"},
        {"role": "check_valve", "description": "Check valve on the discharge", "qty": n, "uom": "Nos"},
        {"role": "pipe", "description": "Flexible PVC discharge (length from the piping step)", "qty": None, "uom": "ft"},
    ]
    if kind == "stream":
        kit.append({"role": "spillway", "description": f"Spillway, {width_in:g} in", "qty": n, "uom": "Nos"})
        if liner:
            kit.append({"role": "liner", "description": f"Liner {liner[0]:g} x {liner[1]:g} ft", "qty": 1, "uom": "Nos"})
            citations.append(R.citation("liner_overlap_ft", rules))
    else:
        kit.append({"role": "feature", "description": f"{kind.title()} with basin", "qty": n, "uom": "Nos"})
    return FamilyResult(
        calc="pondless_family_flow",
        value=total_gpm,
        unit="GPM",
        inputs={"kind": make_input(kind, "", "user"), "fill": make_input(fill, "", "user"),
                "width_in": make_input(width_in, "in", "user")},
        formula="stream GPH = 125 x width_in; reservoir = multiplier(fill) x W x L x D x 7.48",
        steps=steps,
        citations=citations,
        warnings=warnings,
        data={
            "gpm": total_gpm, "gph": gph, "kind": kind, "stream_gal": stream_gal, "dynamic_gal": dynamic,
            "multiplier": mult, "fill": fill, "reservoir_required_gal": reservoir, "liner_ft": liner,
            "kit_lines": kit, "complete": True,
        },
    )


def checks(feature: Any, derived: dict[str, Any] | None = None, site: dict[str, Any] | None = None,
           rules: dict[str, Any] | None = None) -> list[Check]:
    v = values(feature, inputs())
    fid = feature_dict(feature).get("id") or ""
    d = flow_data(flow, feature, derived, site, rules)
    if not d.get("complete"):
        return [Check("PONDLESS_INCOMPLETE", INFO, "Enter the spillway width or the urn's flow", feature=fid)]
    have = num(v.get("reservoir_gal"))
    if have > 0 and d["reservoir_required_gal"] and have < d["reservoir_required_gal"]:
        return [Check(
            "PONDLESS_RESERVOIR", WARNING, "Reservoir is too small for its fill",
            detail=f"{have:.0f} gal; {d['fill']} fill needs {d['multiplier']:g} x {d['dynamic_gal']:.0f} = "
                   f"{d['reservoir_required_gal']:.0f} gal.",
            remedy="Enlarge the reservoir, or switch gravel to water-matrix blocks.",
            citation=CIT_FILL, section="basin", feature=fid,
            value=round(have), limit=round(d["reservoir_required_gal"]), unit="gal",
        )]
    return []


def terminal(feature: Any, derived: dict[str, Any] | None = None) -> dict[str, Any]:
    d = (derived or {}).get("flow") or flow(feature).data
    return {"kind": "open_discharge", "params": {"lift_ft": 0.0, "design_flow_gpm": d.get("gpm"), "residual_head_ft": 0.0}}
