"""Concrete, gravel, earthwork, forms, joints, coatings, penetrations and
anchors for a basin shell.

Quantities, not design: concrete by element with the waste factor, ordered in
quarter yards and 10 yd³ trucks; DOC-0119's 12 in gravel drainage layer;
excavation to the bottom of the gravel with working room for forms and OSHA
side slopes from 5 ft down (or shored vertical); formwork on every formed
face; DOC-0119's waterstop at each cold joint and its joint sealant at the
floor-to-wall cove and movement joints; coating on the water side (the
outside, below grade, for a dry vault); a Link-Seal at every pipe that
crosses the shell; anchor bolts for floor-mounted equipment.
"""

from __future__ import annotations

import math

from ..envelope import CalcResult, make_input
from .defaults import (
    CIT_ACI224_JOINTS,
    CIT_ACI318_EXPOSURE,
    CIT_ACI350_MIX,
    CIT_ASTM_C94,
    CIT_DOC0119_ADMIX,
    CIT_DOC0119_GRAVEL,
    CIT_DOC0119_PENETRATIONS,
    CIT_DOC0119_WATERPROOFING,
    CIT_DOC0119_WATERSTOP,
    CIT_OSHA_SLOPE,
    DISCLAIMER,
    merged_rules,
)

EPS = 1e-9


def _sv(shell):
    return shell.value if isinstance(shell, CalcResult) else shell


def _ceil(x: float) -> int:
    return int(math.ceil(x - EPS))


def concrete_volumes(shell, rules: dict | None = None) -> CalcResult:
    """Cubic yards per element, with the waste factor, the ready-mix order
    rounded up to ``order_increment_yd3`` and the truck count."""
    rules = merged_rules(rules)
    s = _sv(shell)
    waste = float(rules["waste_factor"])
    elements = {}
    steps = []
    for name, cuft in s["concrete_cuft"].items():
        yd3 = cuft / 27.0
        elements[name] = {"cuft": cuft, "yd3": yd3, "yd3_with_waste": yd3 * (1 + waste)}
        steps.append(f"{name}: {cuft:.4f} ft³ = {yd3:.4f} yd³ → ×{1 + waste:g} = {yd3 * (1 + waste):.4f} yd³")
    net = s["concrete_total_cuft"] / 27.0
    gross = net * (1 + waste)
    inc = float(rules["order_increment_yd3"])
    order = math.ceil(gross / inc - EPS) * inc
    trucks = _ceil(order / float(rules["truck_yd3"])) if order > 0 else 0
    steps.append(f"total {net:.4f} yd³ × {1 + waste:g} = {gross:.4f} yd³ → order {order:g} yd³, {trucks} truck(s)")
    return CalcResult(
        calc="structural_concrete_volume",
        value={"elements": elements, "net_yd3": net, "with_waste_yd3": gross, "order_yd3": order, "trucks": trucks},
        unit="yd³",
        inputs={"waste": make_input(waste, "", "default"), "truck": make_input(rules["truck_yd3"], "yd³", "default")},
        formula="yd³ = ft³ / 27; × (1 + waste); order rounded up; trucks = ceil(order / truck size)",
        steps=steps,
        citations=[CIT_ASTM_C94, "Sapphire house rule: 10% concrete waste"],
        warnings=[DISCLAIMER],
    )


def mix_notes(rules: dict | None = None) -> CalcResult:
    """The mix the drawings should call for: ACI 318 exposure F2 (wet and
    freezing, Utah winters) and ACI 350's environmental limits, plus
    DOC-0119's waterproofing admixture."""
    rules = merged_rules(rules)
    notes = [
        f"f'c {rules['fc_psi']:g} psi at 28 days, maximum water-cement ratio {rules['w_cm_max']:g}.",
        f"Air-entrained, {rules['air_content_pct']:g}% ± 1.5% total air for freeze-thaw "
        f"({rules['max_aggregate_in']:g} in maximum aggregate).",
        "Crystalline waterproofing admixture in every pour that holds water (Xypex Admix C-Series or "
        "Penetron Admix, per DOC-0119).",
        "Cure wet or with a compound for at least 7 days; no deicing salts on new concrete the first winter.",
    ]
    return CalcResult(
        calc="structural_mix_notes",
        value={"fc_psi": rules["fc_psi"], "w_cm_max": rules["w_cm_max"], "air_content_pct": rules["air_content_pct"],
               "air_tolerance_pct": 1.5, "max_aggregate_in": rules["max_aggregate_in"], "exposure_class": "F2",
               "admixture": "crystalline waterproofing (Xypex Admix C-Series / Penetron Admix)", "notes": notes},
        formula="exposure F2: w/cm ≤ 0.45, f'c ≥ 4,500 psi, 6% air for ¾–1 in aggregate",
        steps=notes,
        citations=[CIT_ACI318_EXPOSURE, CIT_ACI350_MIX, CIT_DOC0119_ADMIX],
        warnings=[DISCLAIMER],
    )


def gravel_base(shell, rules: dict | None = None) -> CalcResult:
    """DOC-0119's drainage layer (12 in of 1 in minus gravel, or road base)
    under the slab footprint, in cubic yards and tons with waste."""
    rules = merged_rules(rules)
    s = _sv(shell)
    depth_in = float(rules["gravel_in"])
    fill = float(s.get("ledge_fill_cuft") or 0.0)
    if s.get("rim_above_grade_ft") is None or depth_in <= 0:
        if not fill:
            return CalcResult(calc="structural_gravel_base", value={"cuft": 0.0, "yd3": 0.0, "tons": 0.0},
                              unit="ton", steps=["basin on a structure: no gravel base"],
                              citations=[CIT_DOC0119_GRAVEL], warnings=[DISCLAIMER])
        depth_in = 0.0  # no base under a basin on a structure, but its stepped-floor fill still counts
    cuft = s["footprint_sqft"] * depth_in / 12.0 + fill
    yd3 = cuft / 27.0
    tons = yd3 * float(rules["gravel_tons_per_yd3"]) * (1 + float(rules["gravel_waste"]))
    return CalcResult(
        calc="structural_gravel_base",
        value={"depth_in": depth_in, "area_sqft": s["footprint_sqft"], "cuft": cuft, "yd3": yd3, "tons": tons},
        unit="ton",
        inputs={"depth": make_input(depth_in, "in", "standard", CIT_DOC0119_GRAVEL),
                "density": make_input(rules["gravel_tons_per_yd3"], "ton/yd³", "default")},
        formula="yd³ = (footprint × depth + any stepped-floor fill) / 27; tons = yd³ × density × (1 + waste)",
        steps=[f"{s['footprint_sqft']:.4f} ft² × {depth_in / 12:.4f} ft = {cuft:.4f} ft³ = {yd3:.4f} yd³",
               f"tons = {yd3:.4f} × {rules['gravel_tons_per_yd3']:g} × {1 + rules['gravel_waste']:g} = {tons:.4f}"],
        citations=[CIT_DOC0119_GRAVEL, "Sapphire house rule: 1.4 ton/yd³ compacted crushed gravel, 10% waste"],
        warnings=[DISCLAIMER],
    )


def _grown(s: dict, d: float) -> tuple[float, float, float]:
    """Footprint area and perimeter grown by ``d`` ft, and the corner
    coefficient (4 for a rectangle, π for curves) for further growth."""
    if s["shape"] == "rectangular":
        a = (s["outside_length_ft"] + 2 * d) * (s["outside_width_ft"] + 2 * d)
        return a, s["outside_perimeter_ft"] + 8 * d, 4.0
    return s["footprint_sqft"] + s["outside_perimeter_ft"] * d + math.pi * d * d, \
        s["outside_perimeter_ft"] + 2 * math.pi * d, math.pi


def excavation(shell, rules: dict | None = None) -> CalcResult:
    """Dig to the bottom of the gravel with ``work_space_ft`` for forms on
    every side; vertical when under 5 ft or shored, else sloped at
    ``side_slope_h_per_v``. Backfill is what goes back around the shell;
    haul-off is the rest, swollen."""
    rules = merged_rules(rules)
    s = _sv(shell)
    calc = "structural_excavation"
    if s.get("rim_above_grade_ft") is None:
        return CalcResult(calc=calc, value={"cuft": 0.0, "yd3": 0.0, "backfill_yd3": 0.0, "haul_yd3": 0.0},
                          unit="yd³", steps=["basin on a structure: no excavation"], warnings=[DISCLAIMER])
    depth = s["slab_bottom_depth_ft"] + float(rules["gravel_in"]) / 12.0
    ws = float(rules["work_space_ft"])
    a_b, p_b, k_c = _grown(s, ws)
    sloped = depth + EPS >= float(rules["excavation_slope_free_ft"]) and not rules["shoring"]
    k = float(rules["side_slope_h_per_v"]) if sloped else 0.0
    vol = a_b * depth + p_b * k * depth ** 2 / 2.0 + k_c * k * k * depth ** 3 / 3.0
    below = s["footprint_sqft"] * s["slab_bottom_depth_ft"]
    gravel = s["footprint_sqft"] * float(rules["gravel_in"]) / 12.0
    backfill = max(vol - below - gravel, 0.0)
    haul = (vol - backfill) * (1 + float(rules["soil_swell"]))
    steps = [f"depth = {s['slab_bottom_depth_ft']:.4f} + {rules['gravel_in'] / 12:.4f} gravel = {depth:.4f} ft",
             f"bottom = footprint + {ws:g} ft each side = {a_b:.4f} ft²; "
             + (f"sides {k:g}H:1V" if sloped else "vertical sides"),
             f"excavation = {vol:.4f} ft³ = {vol / 27:.4f} yd³",
             f"backfill = {vol:.4f} − {below:.4f} shell − {gravel:.4f} gravel = {backfill:.4f} ft³",
             f"haul = ({vol:.4f} − {backfill:.4f}) × {1 + rules['soil_swell']:g} = {haul:.4f} loose ft³"]
    warnings = [DISCLAIMER]
    if depth + EPS >= float(rules["excavation_slope_free_ft"]):
        warnings.append(f"Excavation {depth:.1f} ft deep: OSHA protective system required (sloped or shored).")
    return CalcResult(
        calc=calc,
        value={"depth_ft": depth, "bottom_area_sqft": a_b, "side_slope_h_per_v": k, "cuft": vol, "yd3": vol / 27.0,
               "backfill_cuft": backfill, "backfill_yd3": backfill / 27.0, "haul_yd3": haul / 27.0},
        unit="yd³",
        inputs={"work_space": make_input(ws, "ft", "default"), "slope": make_input(k, "H:V", "standard", CIT_OSHA_SLOPE)},
        formula="V = A_b·D + P_b·k·D²/2 + c·k²·D³/3 (c = 4 rectangle, π curved)",
        steps=steps,
        citations=[CIT_OSHA_SLOPE, CIT_DOC0119_GRAVEL],
        warnings=warnings,
    )


def formwork(shell, rules: dict | None = None) -> CalcResult:
    """Contact area of forms: both wall faces, the slab edge, stepped-floor
    risers and a vault lid's soffit and edge."""
    s = _sv(shell)
    parts = {
        "wall_inside": s["wall_area_inside_sqft"],
        "wall_outside": s["wall_area_outside_sqft"],
        "slab_edge": s["outside_perimeter_ft"] * s["slab_in"] / 12.0,
    }
    if s.get("risers"):
        parts["risers"] = sum(r["length_ft"] * r["height_ft"] for r in s["risers"])
    if s.get("lid_in"):
        parts["lid_soffit"] = max(s["inside_area_sqft"] - s["hatch_area_sqft"], 0.0)
        parts["lid_edge"] = s["outside_perimeter_ft"] * s["lid_in"] / 12.0
    total = sum(parts.values())
    return CalcResult(
        calc="structural_formwork",
        value={"parts": parts, "sqft": total},
        unit="ft²",
        formula="Σ formed faces (contact area)",
        steps=[f"{k} = {v:.4f} ft²" for k, v in parts.items()] + [f"total = {total:.4f} ft²"],
        citations=["Contact-area takeoff (formed faces only)"],
        warnings=[DISCLAIMER],
    )


def joints(shell, rules: dict | None = None) -> CalcResult:
    """Cold, movement and sealant joints.

    The wall-to-slab cold joint always gets a waterstop (DOC-0119), on the
    water side of the bars. Movement joints are added where the slab or the
    wall run exceeds ``max_joint_spacing_ft`` (DOC-0119 is silent; ACI
    224.3R and ACI 350's 30 ft band); each also gets a waterstop. Sealant
    (DOC-0119 names Dymonic 100) runs the inside floor-to-wall cove and every
    movement joint, and a vault lid's bearing joint."""
    rules = merged_rules(rules)
    s = _sv(shell)
    mx = float(rules["max_joint_spacing_ft"])
    if s["shape"] == "rectangular":
        lo, wo = s["outside_length_ft"], s["outside_width_ft"]
    else:
        lo = wo = math.sqrt(s["footprint_sqft"])
    n_across_long = max(_ceil(lo / mx) - 1, 0)
    n_across_short = max(_ceil(wo / mx) - 1, 0)
    slab_joint_ft = n_across_long * wo + n_across_short * lo
    pc = s["wall_centerline_ft"]
    if s["shape"] == "rectangular":
        # each straight side is its own run: joints inside a side only
        n_long, n_short = max(_ceil(lo / mx) - 1, 0), max(_ceil(wo / mx) - 1, 0)
        n_wall = 2 * (n_long + n_short)
        wall_panel = max(lo / (n_long + 1), wo / (n_short + 1))
    else:
        # a closed curved wall longer than the limit gets joints all round
        n_wall = _ceil(pc / mx) if pc > mx + EPS else 0
        wall_panel = pc / n_wall if n_wall else pc
    wall_joint_ft = n_wall * s["wall_height_ft"]
    slab_panel = max(lo / (n_across_long + 1), wo / (n_across_short + 1))
    cold = pc
    waterstop = cold + slab_joint_ft + wall_joint_ft
    sealant = s["inside_perimeter_ft"] + slab_joint_ft + wall_joint_ft
    if s.get("lid_in"):
        sealant += pc
    return CalcResult(
        calc="structural_joints",
        value={"cold_joint_ft": cold, "slab_movement_joints": n_across_long + n_across_short,
               "slab_movement_joint_ft": slab_joint_ft, "wall_movement_joints": n_wall,
               "wall_movement_joint_ft": wall_joint_ft, "waterstop_ft": waterstop, "sealant_ft": sealant,
               "slab_joint_spacing_ft": slab_panel, "wall_joint_spacing_ft": wall_panel},
        unit="ft",
        inputs={"max_joint_spacing": make_input(mx, "ft", "default", CIT_ACI224_JOINTS)},
        formula="waterstop = wall-to-slab joint + movement joints; sealant = inside cove + movement joints",
        steps=[f"cold joint (wall base) = {cold:.4f} ft", f"slab movement joints {slab_joint_ft:.2f} ft, "
               f"wall {n_wall} × {s['wall_height_ft']:.2f} ft", f"waterstop = {waterstop:.4f} ft",
               f"sealant = {sealant:.4f} ft"],
        citations=[CIT_DOC0119_WATERSTOP, CIT_DOC0119_WATERPROOFING, CIT_ACI224_JOINTS],
        warnings=[DISCLAIMER, "Waterstop on the water side of the bars: Mirastop or Henry Synkoflex SF302 "
                  "per DOC-0119's drawing note."],
    )


def coating_area(shell, rules: dict | None = None) -> CalcResult:
    """Waterproofing on the water side (inside walls and floor, plus wall tops
    when ``coat_wall_tops``); a dry vault takes it outside, below grade.
    Curing covers every exposed face."""
    rules = merged_rules(rules)
    s = _sv(shell)
    tops = s["wall_centerline_ft"] * s["wall_in"] / 12.0
    riser_faces = sum(r["length_ft"] * r["height_ft"] for r in s.get("risers") or [])
    if s["kind"] == "vault":
        below = min(s["slab_bottom_depth_ft"], s["wall_height_ft"] + (s["slab_in"] + s["lid_in"]) / 12.0)
        coat = s["outside_perimeter_ft"] * below
        where = "outside faces below grade (dry vault)"
    else:
        coat = (s["wall_area_inside_sqft"] + s["inside_area_sqft"] + riser_faces
                + (tops if rules["coat_wall_tops"] else 0.0))
        where = "inside walls + floor" + (" + step risers" if riser_faces else "") + \
            (" + wall tops" if rules["coat_wall_tops"] else "")
    curing = (s["wall_area_inside_sqft"] + s["inside_area_sqft"] + tops + riser_faces
              + s["outside_perimeter_ft"] * s["wall_above_grade_ft"] + s.get("lid_area_sqft", 0.0))
    return CalcResult(
        calc="structural_coating_area",
        value={"coating_sqft": coat, "coating_where": where, "curing_sqft": curing},
        unit="ft²",
        formula="coating = water-side faces; curing = exposed faces",
        steps=[f"coating ({where}) = {coat:.4f} ft²", f"curing = {curing:.4f} ft²"],
        citations=[CIT_DOC0119_WATERPROOFING],
        warnings=[DISCLAIMER, "DOC-0119: broadcast sand into the last coat where tile goes over the membrane."],
    )


def penetrations(model: dict | None, basin_id: str) -> CalcResult:
    """Pipes crossing a basin's (or vault's) shell, from the design network:
    every segment with exactly one end at a node in this basin. Each gets a
    stainless Link-Seal in a cored hole (or a cast-in flange) and non-shrink
    grout on the outside face (DOC-0119 Concrete Penetrations)."""
    net = (model or {}).get("network") or {}
    inside = {n.get("id") for n in net.get("nodes") or []
              if (n.get("kind") == "basin" and n.get("basin") == basin_id) or n.get("vault") == basin_id}
    for p in net.get("pumps") or []:
        if p.get("vault") == basin_id:
            inside.add(p.get("id"))
    rows = []
    for seg in net.get("segments") or []:
        ends = (seg.get("from") in inside) + (seg.get("to") in inside)
        if ends == 1:
            rows.append({"segment": seg.get("id"), "nominal_size": seg.get("nominal_size") or "unsized",
                         "line": seg.get("line") or ""})
    by_size: dict[str, int] = {}
    for r in rows:
        by_size[r["nominal_size"]] = by_size.get(r["nominal_size"], 0) + 1
    return CalcResult(
        calc="structural_penetrations",
        value={"count": len(rows), "rows": rows, "by_size": by_size},
        unit="each",
        formula="segments with one end inside the shell",
        steps=[f"{len(rows)} penetration(s): " + ", ".join(f"{n} × {k}" for k, n in by_size.items())],
        citations=[CIT_DOC0119_PENETRATIONS],
        warnings=[DISCLAIMER],
    )


def anchor_bolts(model: dict | None, rules: dict | None = None) -> CalcResult:
    """Anchor bolts for floor-mounted equipment: every non-submersible pump
    on the network plus every equipment row in ``anchored_slots`` (pumps are
    counted from the network, not twice)."""
    rules = merged_rules(rules)
    model = model or {}
    pumps = [p for p in ((model.get("network") or {}).get("pumps") or [])
             if "submersible" not in str(p.get("generic_class") or "").lower()]
    slots = {str(x).lower() for x in rules["anchored_slots"]} - {"pump"}
    other = 0
    for row in model.get("equipment") or []:
        if str(row.get("slot") or "").lower() in slots:
            qty = row.get("qty")
            other += 1 if qty is None or qty == "" else max(int(float(qty)), 0)
    per = int(rules["anchors_per_equipment"])
    count = (len(pumps) + other) * per
    return CalcResult(
        calc="structural_anchor_bolts",
        value={"count": count, "pieces_of_equipment": len(pumps) + other, "per_equipment": per},
        unit="each",
        formula="anchors = (floor-mounted pumps + anchored equipment) × anchors each",
        steps=[f"({len(pumps)} pumps + {other} other) × {per} = {count}"],
        citations=["Sapphire house rule: 4 anchors per floor-mounted unit"],
        warnings=[DISCLAIMER],
    )
