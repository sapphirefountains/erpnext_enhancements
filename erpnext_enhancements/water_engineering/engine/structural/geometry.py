"""Basin shells: the concrete around the water, in plan and section.

A basin in the design model (PLAN.md §3.3) gives its *inside* dimensions: the
water-side length and width (or diameter, or area and perimeter for a
freeform), and ``depth_in`` from the top of the floor slab to the rim. The
shell wraps that with walls of thickness ``t`` standing on a slab that runs
under them to the outside face:

    footprint      = inside plan grown by t on every side
    wall plan area = footprint - inside area = t * (wall centerline length)
    wall volume    = wall plan area * wall height
    slab volume    = footprint * slab thickness

Rectangles grow with square corners; circles exactly; a freeform grows by
Steiner's formula for a convex outline (A + P·δ + π·δ², P + 2π·δ), so a
freeform given a circle's area and perimeter matches the cylinder. Stepped
floors take the walls at the deepest step (conservative) and add a riser of
slab thickness at each step. An equipment vault is a basin with a lid and no
water.

Optional basin keys this package reads (top level or under ``structure``):
``kind`` ("basin" | "vault"), ``placement`` ("in_grade" | "raised" |
"on_structure"), ``rim_above_grade_in``, ``steps`` ([{depth_in,
area_fraction}]), ``riser_length_ft``, ``corner_count``, ``lid`` ({type,
thickness_in, hatch_length_in, hatch_width_in}), ``slab_in``, ``wall_in``,
``groundwater_depth_ft``, ``soil_bearing_psf``, ``floor_capacity_psf``.
"""

from __future__ import annotations

import math
from itertools import pairwise

from ..constants import GAL_PER_CUBIC_FOOT
from ..envelope import CalcResult, make_input
from .defaults import DISCLAIMER, merged_rules

SHAPES = {
    "rect": "rectangular",
    "rectangle": "rectangular",
    "rectangular": "rectangular",
    "cyl": "cylindrical",
    "cylinder": "cylindrical",
    "cylindrical": "cylindrical",
    "circle": "cylindrical",
    "circular": "cylindrical",
    "round": "cylindrical",
    "freeform": "freeform",
    "free-form": "freeform",
    "custom": "freeform",
}


def _num(value, default=0.0) -> float:
    try:
        return float(value) if value is not None and value != "" else float(default)
    except (TypeError, ValueError):
        return float(default)


def _opt(value):
    """A number, or ``None`` for a missing or blank value (a cleared form
    field arrives as "")."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def rim_above_grade_ft(spec: dict, h_ft: float, lid_in: float) -> float | None:
    """Rim (top of wall) height above finished grade, in feet; ``None`` for a
    basin standing on a structure. One rule for the shell and the sizing."""
    if spec.get("placement") == "on_structure":
        return None
    if spec.get("rim_above_grade_in") is not None:
        return float(spec["rim_above_grade_in"]) / 12.0
    if spec.get("placement") == "raised":
        return float(h_ft)
    if spec.get("kind") == "vault":
        return -float(lid_in or 0) / 12.0
    return 0.0


def basin_spec(basin: dict, site: dict | None = None, rules: dict | None = None) -> dict:
    """Flatten one model basin (plus site-level soil facts) into the keys the
    structural calcs read. Keys under ``basin["structure"]`` win over the same
    key at the basin's top level; site values fill what neither gives."""
    rules = merged_rules(rules)
    basin = basin or {}
    site = site or {}
    struct = basin.get("structure") or {}

    def get(key, default=None):
        if struct.get(key) is not None:
            return struct[key]
        if basin.get(key) is not None:
            return basin[key]
        return default

    kind = str(get("kind", "basin") or "basin").strip().lower()
    kind = "vault" if kind in ("vault", "equipment_vault", "pit") else "basin"
    shape = SHAPES.get(str(get("shape", "rectangular") or "rectangular").strip().lower(), "")
    placement = str(get("placement", "in_grade") or "in_grade").strip().lower().replace("-", "_")
    if placement in ("ingrade", "in_ground", "sunken", "below_grade"):
        placement = "in_grade"
    if placement in ("above_grade", "raised_wall"):
        placement = "raised"
    if placement in ("on_slab", "on_roof", "rooftop", "elevated", "podium"):
        placement = "on_structure"

    problems: list[str] = []

    def positive(key, value, allow_zero=False):
        """A number ≥ 0 (or > 0), or None with a note; a negative never reaches the math."""
        num = _opt(value)
        if num is None:
            return None
        if num < 0 or (num == 0 and not allow_zero):
            problems.append(f"{key} = {value!r} ignored (must be {'≥' if allow_zero else '>'} 0)")
            return None
        return num

    steps = []
    for i, row in enumerate(get("steps", None) or [], start=1):
        depth = _opt((row or {}).get("depth_in"))
        frac = _opt((row or {}).get("area_fraction"))
        if depth is not None and frac is not None and depth > 0 and frac > 0:
            steps.append({"depth_in": depth, "area_fraction": frac})
        else:
            problems.append(f"step {i} ignored: needs a depth_in and an area_fraction, both > 0 (got {row!r})")
    depth_in = _num(get("depth_in"))
    if steps:
        depth_in = max(depth_in, max(s["depth_in"] for s in steps))

    lid = None
    if kind == "vault":
        given = get("lid", None) or {}
        hatch_l = positive("lid.hatch_length_in", given.get("hatch_length_in"), allow_zero=True)
        hatch_w = positive("lid.hatch_width_in", given.get("hatch_width_in"), allow_zero=True)
        lid = {
            "type": str(given.get("type") or "concrete").lower(),
            "thickness_in": positive("lid.thickness_in", given.get("thickness_in")),
            "hatch_length_in": rules["vault_hatch_in"] if hatch_l is None else hatch_l,
            "hatch_width_in": rules["vault_hatch_in"] if hatch_w is None else hatch_w,
        }

    freeboard = positive("freeboard_in", get("freeboard_in", None), allow_zero=True)
    gw = _opt(get("groundwater_depth_ft", None))  # negative = water above grade, allowed
    corners = positive("corner_count", get("corner_count", None), allow_zero=True)
    return {
        "_spec": True,
        "id": basin.get("id") or "",
        "label": basin.get("label") or basin.get("id") or "",
        "kind": kind,
        "shape": shape,
        "placement": placement,
        "length_in": _num(get("length_in")),
        "width_in": _num(get("width_in")),
        "diameter_in": _num(get("diameter_in")),
        "area_sqft": _num(get("area_sqft")),
        "perimeter_ft": _num(get("perimeter_ft")),
        "depth_in": depth_in,
        "freeboard_in": freeboard,
        "steps": steps,
        "riser_length_ft": positive("riser_length_ft", get("riser_length_ft", None)),
        "corner_count": None if corners is None else int(corners),
        "rim_above_grade_in": _opt(get("rim_above_grade_in", None)),
        "lid": lid,
        "slab_in": positive("slab_in", get("slab_in", None)),
        "wall_in": positive("wall_in", get("wall_in", None)),
        "groundwater_depth_ft": gw if gw is not None else _opt(site.get("groundwater_depth_ft")),
        "soil_bearing_psf": positive("soil_bearing_psf", get("soil_bearing_psf", None))
        or positive("site.soil_bearing_psf", site.get("soil_bearing_psf")),
        "floor_capacity_psf": positive("floor_capacity_psf", get("floor_capacity_psf", None)),
        "problems": problems,
    }


def _plan(spec: dict, t_ft: float) -> dict | None:
    """Inside and grown plan figures for wall thickness ``t_ft``."""
    shape = spec["shape"]
    if shape == "rectangular":
        L, W = spec["length_in"] / 12.0, spec["width_in"] / 12.0
        if L <= 0 or W <= 0:
            return None
        return {
            "inside_area": L * W,
            "inside_perimeter": 2 * (L + W),
            "centerline": 2 * (L + W) + 4 * t_ft,
            "outside_perimeter": 2 * (L + W) + 8 * t_ft,
            "footprint": (L + 2 * t_ft) * (W + 2 * t_ft),
            "inside_length": L,
            "inside_width": W,
            "outside_length": L + 2 * t_ft,
            "outside_width": W + 2 * t_ft,
            "corners": 4,
            "riser_length": W,
        }
    if shape == "cylindrical":
        D = spec["diameter_in"] / 12.0
        if D <= 0:
            return None
        return {
            "inside_area": math.pi * D * D / 4.0,
            "inside_perimeter": math.pi * D,
            "centerline": math.pi * (D + t_ft),
            "outside_perimeter": math.pi * (D + 2 * t_ft),
            "footprint": math.pi * (D + 2 * t_ft) ** 2 / 4.0,
            "inside_diameter": D,
            "outside_diameter": D + 2 * t_ft,
            "corners": 0,
            "riser_length": D,
        }
    if shape == "freeform":
        A, P = spec["area_sqft"], spec["perimeter_ft"]
        if A <= 0 or P <= 0:
            return None
        return {
            "inside_area": A,
            "inside_perimeter": P,
            "centerline": P + math.pi * t_ft,
            "outside_perimeter": P + 2 * math.pi * t_ft,
            "footprint": A + P * t_ft + math.pi * t_ft * t_ft,
            "corners": 0,
            "riser_length": math.sqrt(A),
        }
    return None


def basin_shell(
    basin: dict, slab_in: float, wall_in: float, rules: dict | None = None, site: dict | None = None
) -> CalcResult:
    """Areas, lengths and volumes of one basin's concrete shell and its water.

    ``basin`` is a model basin or a ``basin_spec`` result. Returns a
    ``CalcResult`` whose ``value`` dict carries every figure in feet, square
    feet, cubic feet and gallons.
    """
    rules = merged_rules(rules)
    spec = basin if (basin or {}).get("_spec") else basin_spec(basin, site, rules)
    calc = "structural_basin_shell"
    citations = ["Plane geometry; Steiner's formula for a convex outline grown by a constant offset"]
    t_in, s_in = float(wall_in or 0), float(slab_in or 0)
    t = t_in / 12.0
    plan = _plan(spec, t) if t_in > 0 and s_in > 0 else None
    if plan is None or spec["depth_in"] <= 0:
        need = {"rectangular": ("length_in", "width_in"), "cylindrical": ("diameter_in",),
                "freeform": ("area_sqft", "perimeter_ft")}.get(spec.get("shape"), ())
        missing = [k for k in need if not (spec.get(k) or 0) > 0]
        if spec["depth_in"] <= 0:
            missing.append("depth_in")
        if t_in <= 0:
            missing.append("wall_in")
        if s_in <= 0:
            missing.append("slab_in")
        what = (f"unknown shape {spec.get('shape')!r} (rectangular, cylindrical or freeform)" if not need
                else "missing or not > 0: " + ", ".join(missing))
        return CalcResult(
            calc=calc,
            inputs={"basin": make_input(spec.get("id"), "", "user")},
            formula="needs shape dimensions, depth_in > 0, slab and wall thickness > 0",
            citations=citations,
            warnings=[
                f"Basin {spec.get('id') or '?'}: {what}.",
                DISCLAIMER,
            ],
            status="incomplete",
        )

    warnings = [DISCLAIMER] + [f"Input: {p}." for p in spec.get("problems") or []]
    steps_out = []
    h = spec["depth_in"] / 12.0
    slab = s_in / 12.0
    corners = plan["corners"] if spec["corner_count"] is None else int(spec["corner_count"])

    # water
    if spec["kind"] == "vault":
        freeboard_in = None
        water_cuft = 0.0
        water_depth_ft = 0.0
    else:
        freeboard_in = spec["freeboard_in"] if spec["freeboard_in"] is not None else rules["default_freeboard_in"]
        zones = spec["steps"] or [{"depth_in": spec["depth_in"], "area_fraction": 1.0}]
        frac_total = sum(z["area_fraction"] for z in zones)
        if spec["steps"] and abs(frac_total - 1.0) > 1e-6:
            warnings.append(f"Step area fractions sum to {frac_total:g}; scaled to 1.")
        water_cuft = sum(
            plan["inside_area"] * (z["area_fraction"] / frac_total) * max(z["depth_in"] - freeboard_in, 0.0) / 12.0
            for z in zones
        )
        water_depth_ft = water_cuft / plan["inside_area"]
        steps_out.append(
            f"water = {plan['inside_area']:.4f} ft² × (depth − {freeboard_in:g} in freeboard) = {water_cuft:.4f} ft³"
        )

    # concrete
    wall_plan = plan["footprint"] - plan["inside_area"]
    vol = {"slab": plan["footprint"] * slab, "walls": wall_plan * h}
    steps_out += [
        f"footprint = {plan['footprint']:.4f} ft²; slab = {plan['footprint']:.4f} × {slab:.4f} = {vol['slab']:.4f} ft³",
        f"wall plan = {plan['footprint']:.4f} − {plan['inside_area']:.4f} = {wall_plan:.4f} ft² "
        f"(= {t:.4f} ft × {plan['centerline']:.4f} ft centerline)",
        f"walls = {wall_plan:.4f} × {h:.4f} = {vol['walls']:.4f} ft³",
    ]
    risers = []
    ledge_fill = ledge_slab_area = 0.0
    if len(spec["steps"]) > 1:
        depths = sorted({z["depth_in"] for z in spec["steps"]})
        riser_len = spec["riser_length_ft"] or plan["riser_length"]
        for lo, hi in pairwise(depths):
            risers.append({"length_ft": riser_len, "height_ft": (hi - lo) / 12.0})
        # the raised (shallower) floor zones stand on the deep slab: a ledge per zone
        frac_sum = sum(z["area_fraction"] for z in spec["steps"])
        deepest = max(depths)
        ledges = [(plan["inside_area"] * z["area_fraction"] / frac_sum, (deepest - z["depth_in"]) / 12.0)
                  for z in spec["steps"] if z["depth_in"] < deepest]
        if str(rules["step_ledge"]).lower() == "gravel":
            ledge_slab_area = sum(a for a, _ in ledges)
            ledge_fill = sum(a * max(hgt - slab, 0.0) for a, hgt in ledges)
            vol["ledge_slab"] = ledge_slab_area * slab
            vol["risers"] = sum(r["length_ft"] * r["height_ft"] * slab for r in risers)
            ledge_note = (f"raised floor = {ledge_slab_area:.2f} ft² slab on {ledge_fill:.2f} ft³ of compacted "
                          f"gravel, plus {len(risers)} riser wall(s) × {riser_len:.2f} ft")
        else:
            vol["ledge"] = sum(a * hgt for a, hgt in ledges)
            ledge_note = f"raised floor = concrete to the step height, {vol['ledge']:.2f} ft³"
        steps_out.append(f"stepped floor: {ledge_note}")
        warnings.append("Stepped floor: walls taken at the deepest step all round (conservative); " + ledge_note
                        + " (house rule step_ledge).")

    lid_t_in = 0.0
    lid_area = hatch_area = 0.0
    if spec["kind"] == "vault" and spec["lid"] and spec["lid"]["type"] == "concrete":
        lid_t_in = spec["lid"]["thickness_in"] or s_in
        hatch_area = spec["lid"]["hatch_length_in"] * spec["lid"]["hatch_width_in"] / 144.0
        lid_area = max(plan["footprint"] - hatch_area, 0.0)
        vol["lid"] = lid_area * lid_t_in / 12.0
        steps_out.append(f"lid = ({plan['footprint']:.4f} − {hatch_area:.4f} hatch) × {lid_t_in / 12:.4f} = {vol['lid']:.4f} ft³")
        warnings.append("Vault lid: quantities only; the lid's span and any traffic load are not checked.")

    # grade
    rim_above = rim_above_grade_ft(spec, h, lid_t_in)
    if rim_above is None:
        slab_bottom_depth = wall_below = 0.0
        wall_above = h
    else:
        slab_bottom_depth = max(h - rim_above + slab, 0.0)
        wall_below = min(max(h - rim_above, 0.0), h)
        wall_above = h - wall_below

    total = sum(vol.values())
    value = {
        "basin_id": spec["id"],
        "label": spec["label"],
        "kind": spec["kind"],
        "shape": spec["shape"],
        "placement": spec["placement"],
        "wall_in": t_in,
        "slab_in": s_in,
        "lid_in": lid_t_in,
        "depth_in": spec["depth_in"],
        "wall_height_ft": h,
        "inside_area_sqft": plan["inside_area"],
        "inside_perimeter_ft": plan["inside_perimeter"],
        "wall_centerline_ft": plan["centerline"],
        "outside_perimeter_ft": plan["outside_perimeter"],
        "footprint_sqft": plan["footprint"],
        "wall_plan_area_sqft": wall_plan,
        "wall_area_inside_sqft": plan["inside_perimeter"] * h,
        "wall_area_outside_sqft": plan["outside_perimeter"] * h,
        "wall_length_ft": plan["centerline"],
        "corner_count": corners,
        "freeboard_in": freeboard_in,
        "water_depth_ft": water_depth_ft,
        "water_volume_cuft": water_cuft,
        "water_volume_gal": water_cuft * GAL_PER_CUBIC_FOOT,
        "water_weight_lb": water_cuft * rules["water_pcf"],
        "full_water_cuft": 0.0 if spec["kind"] == "vault" else plan["inside_area"] * h,
        "rim_above_grade_ft": rim_above,
        "slab_bottom_depth_ft": slab_bottom_depth,
        "wall_below_grade_ft": wall_below,
        "wall_above_grade_ft": wall_above,
        "lid_area_sqft": lid_area,
        "hatch_area_sqft": hatch_area,
        "risers": risers,
        "ledge_fill_cuft": ledge_fill,
        "ledge_slab_sqft": ledge_slab_area,
        "steps": spec["steps"],
        "concrete_cuft": vol,
        "concrete_total_cuft": total,
        "concrete_weight_lb": total * rules["concrete_pcf"],
        "groundwater_depth_ft": spec["groundwater_depth_ft"],
        "soil_bearing_psf": spec["soil_bearing_psf"],
        "floor_capacity_psf": spec["floor_capacity_psf"],
    }
    for key in ("inside_length", "inside_width", "outside_length", "outside_width", "inside_diameter", "outside_diameter"):
        if key in plan:
            value[key + "_ft"] = plan[key]
    steps_out.append(f"concrete total = {total:.4f} ft³ = {total / 27:.4f} yd³ (before waste)")
    return CalcResult(
        calc=calc,
        value=value,
        unit="ft",
        inputs={
            "shape": make_input(spec["shape"], "", "user"),
            "depth": make_input(spec["depth_in"], "in", "user"),
            "wall_thickness": make_input(t_in, "in", "prior_calc"),
            "slab_thickness": make_input(s_in, "in", "prior_calc"),
        },
        formula="footprint = inside plan grown by t; walls = (footprint − inside) × h; slab = footprint × s",
        steps=steps_out,
        citations=citations,
        warnings=warnings,
    )
