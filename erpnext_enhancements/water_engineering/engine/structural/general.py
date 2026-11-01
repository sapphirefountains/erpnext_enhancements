"""The structure beyond the basin shells, run from the design model.

``general_structure(model, basins, rules)`` is called by
``structural_takeoff``. It reads the site (seismic, wind, frost, soil), the
basins the shell chain already sized, the weir features, and an optional
``model["structure"]`` block that lists the rest of the structure:

    "structure": {
      "walls":   [{id, label, stem_height_ft, stem_in, retained: "water"|"soil", water_depth_ft, length_ft,
                   surcharge: "pedestrian"|"plaza"|"vehicle"|<psf>, soil_class, bearing_psf}],
      "frames":  [{id, label, height_ft, width_ft, header_plf, post_spacing_ft, clearance_ft, solid_ratio,
                   material}],
      "weir_lips": [{id, feature, span_in, thickness_in, flange_in, lip_width_in, water_depth_in, support,
                     tolerance_in}],
      "veneer":  [{id, area_sqft, thickness_in, material, method, anchor_capacity_lb}],
      "pedestals": [{id, length_ft, width_ft, height_ft, top_load_lb, top_cg_ft, wind_lb, wind_height_ft, on}],
      "columns": [{id, height_ft, axial_lb, lateral_plf, top_lb, material}],
      "beams":   [{id, span_ft, tributary_ft, dead_psf, live_psf, material, shape}],
      "gratings": [{id, span_in, area_sqft, load_class}],
      "trench_drains": [{id, length_ft, load_class}],
      "pads":    [{id, length_in, width_in, weight_lb, cg_height_in, n_anchors, anchor_dia_in, embed_in}],
      "thrust_blocks": [{id, size, pressure_psi, fitting, count, soil_bearing_psf}],
      "bracing": [{id, size, run_ft, material}],
      "moorings": [{id, above_water_sqft, submerged_sqft, current_fps, n_anchors, line_angle_deg}],
      "rigging": {hung_lb_per_ft, hung_length_ft, hanger_spans, hanger_span_ft, overhang_ft, curtain_width_ft,
                  mount, truss, header_height_ft, picks, riser_lb, feed}
    }

Automatic, with no ``structure`` block: seismic design values and the
hydrodynamic check of every basin's walls when the site gives Ss/S1; a sag
check of every weir feature's lip at the house-rule section; a lid design for
every vault with a concrete lid; the waterstop ring at every penetration; the
floor check against the floor's use for a basin on a building.
"""

from __future__ import annotations

import math

from ..data.rebar import bar as rebar
from .building import building_floor_load, deck_beam
from .defaults import DISCLAIMER
from .fabrications import pedestal_check, veneer_attachment, weir_lip_sag
from .loads import (
    freeze_thaw_notes,
    hydrodynamic_wall_load,
    seismic_design_values,
    surcharge_load,
    wind_panel_load,
)
from .rigging import rig_plan
from .steel import _section, select_column, water_wall_frame
from .supports import equipment_pad, mooring, nominal_size, penetration_detail, seismic_bracing, thrust_block
from .vaults import grating, lid_slab, trench_drain
from .walls import size_wall_footing

BLOCKER, WARNING, INFO = "blocker", "warning", "info"

# the numbers each structure row must have (> 0) before it is computed
REQUIRED = {
    "walls": ("stem_height_ft",), "frames": ("height_ft", "width_ft"), "columns": ("height_ft",),
    "beams": ("span_ft", "tributary_ft"), "gratings": ("span_in",), "trench_drains": ("length_ft",),
    "veneer": ("area_sqft", "thickness_in"), "pedestals": ("length_ft", "width_ft", "height_ft"),
    "pads": ("length_in", "width_in", "weight_lb"), "thrust_blocks": ("pressure_psi",), "bracing": ("run_ft",),
    "moorings": ("above_water_sqft",), "rigging": ("hung_lb_per_ft", "hung_length_ft", "hanger_span_ft"),
}
GROUP_SECTION = {"walls": "basin", "beams": "basin", "gratings": "basin", "trench_drains": "basin", "pads": "pump",
                 "thrust_blocks": "piping", "bracing": "piping"}


def _num(value, default=None):
    try:
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _wall_phi_mn(wall_in, bar_size, spacing_in, mats, rules) -> float:
    """φMn per foot of a wall's tension mat (lb·ft/ft)."""
    b = rebar(bar_size)
    d = float(wall_in) / 2.0 if int(mats) < 2 else float(wall_in) - float(rules["cover_in"]) - b["diameter_in"] / 2
    as_ = b["area_in2"] * 12.0 / float(spacing_in)
    a = as_ * float(rules["fy_psi"]) / (0.85 * float(rules["fc_psi"]) * 12.0)
    return 0.9 * as_ * float(rules["fy_psi"]) * (d - a / 2.0) / 12.0


def general_structure(model: dict, basins: list[dict], rules: dict) -> dict:
    """Results, checks, BOM rows and totals for the structure beyond the shells.

    ``basins`` is ``structural_takeoff``'s per-basin list. Returns
    ``{"results": {group: [{id, label, result}]}, "seismic", "notes", "checks",
    "bom": [(key, description, qty, uom, item_hint, extra)], "totals"}``."""
    model = model or {}
    site = model.get("site") or {}
    struct = model.get("structure") or {}
    results: dict[str, list] = {}
    checks: list[dict] = []
    bom: list[tuple] = []
    totals = {"steel_lb": 0.0, "site_concrete_cuft": 0.0, "site_rebar_lb": 0.0, "grating_sqft": 0.0,
              "veneer_anchors": 0, "equipment_anchors": 0, "pads": 0, "braces": 0, "mooring_blocks": 0,
              "trench_drain_pieces": 0, "waterstop_ring_ft": 0.0}

    def record(group, row, res):
        results.setdefault(group, []).append({"id": row.get("id"), "label": row.get("label") or row.get("id"),
                                              "result": res.to_dict()})

    def check(code, severity, section, label, title, detail, remedy, citation):
        checks.append({"key": f"{code}|{label}", "code": code, "severity": severity, "title": title,
                       "detail": detail, "remedy": remedy, "section": section, "basin": None,
                       "citation": citation})

    def steel(material, lb):
        key = "structural.steel" if "SS" not in str(material) else f"structural.steel.{str(material).lower()}"
        desc = "Structural steel, tube and shapes (A500C / A992)" if key == "structural.steel" else \
            f"Stainless steel members ({material})"
        bom.append((key, desc, lb, "Pound", key.replace(".", "_"), {}))
        totals["steel_lb"] += lb

    wall_rebar: dict[str, float] = {}
    def complete(group, rows=None):
        """Rows of ``group`` (or the given ``rows``) with every required number > 0; the rest become
        an INFO check (a half-filled form row must never take the whole takeoff down)."""
        for i, row in enumerate(struct.get(group) or [] if rows is None else rows, start=1):
            row = row or {}
            missing = [k for k in REQUIRED[group] if not (_num(row.get(k), 0.0) > 0)]
            if missing:
                check("STRUCT_ROW_INCOMPLETE", INFO, GROUP_SECTION.get(group, "features"),
                      f"{group} {row.get('label') or row.get('id') or i}", f"{group.replace('_', ' ').title()} row not computed",
                      "missing or not > 0: " + ", ".join(missing), "Fill in the row or remove it.", "")
                continue
            yield row

    frost = _num(site.get("frost_depth_in"), float(rules["frost_depth_in"]))
    soil_class = site.get("soil_class")
    bearing = _num(site.get("soil_bearing_psf"))

    # seismic
    seismic = seismic_design_values(site.get("ss"), site.get("s1"), site.get("site_class") or "D",
                                    site.get("sds"), site.get("sd1"))
    sds = sd1 = sdc = None
    if seismic.status == "incomplete":
        if basins or struct:
            check("STRUCT_SEISMIC_INPUTS", INFO, "project", "site", "Seismic values not entered",
                  "The Wasatch Front is normally Seismic Design Category D; the hydrodynamic, anchor and pedestal "
                  "checks need Ss and S1.", "Look up Ss and S1 for the address in the USGS / ASCE Hazard Tool.",
                  seismic.citations[0])
    else:
        sds, sd1, sdc = seismic.value["sds"], seismic.value["sd1"], seismic.value["sdc"]

    # basins: hydrodynamic walls, vault lids, penetration rings, building floors
    for b in basins:
        if not b.get("complete"):
            continue
        g, d = b["geometry"], b["detail"]
        label = b["label"] or b["basin_id"]
        if b["kind"] == "basin" and sds is not None:
            if g["shape"] == "rectangular":
                lengths, hshape = sorted({g["inside_length_ft"], g["inside_width_ft"]}), "rectangular"
            else:  # a freeform as the circle of the same area
                lengths, hshape = [math.sqrt(4 * g["inside_area_sqft"] / math.pi)], "circular"
            buried = (g.get("wall_below_grade_ft") or 0) > 0
            phi_mn = _wall_phi_mn(d["wall_in"], d["wall_bar"], d["wall_spacing_in"], d["wall_mats"], rules)
            for length in lengths:
                hd = hydrodynamic_wall_load(g["wall_height_ft"], length, sds, sd1, d["wall_in"], g["wall_height_ft"],
                                            buried, rules=rules, shape=hshape)
                if g["shape"] == "freeform":
                    hd.warnings.append("Freeform basin taken as the circle of the same area.")
                record("hydrodynamic", {"id": f"{b['basin_id']}:{length:g}", "label": label}, hd)
                v = hd.value
                mu = float(rules["durability_factor"]) * (1.2 * v["static_moment_lb_ft_per_ft"] +
                                                          1.0 * v["base_moment_lb_ft_per_ft"])
                if mu > phi_mn:
                    check("STRUCT_SEISMIC_WALL", WARNING, "basin", f"{label} ({length:g} ft)",
                          "Wall short of steel under earthquake sloshing",
                          f"Mu {mu:.0f} lb·ft/ft (1.2 water + 1.0 earthquake, × 1.3) vs φMn {phi_mn:.0f}.",
                          "Thicker wall or tighter bars, or have the walls designed for SDC "
                          f"{sdc}.", hd.citations[0])
                slosh_in = length / 2.0 * v["cc"] * 12.0
                freeboard = g.get("freeboard_in") or 0.0
                if slosh_in > freeboard + 1e-9:
                    check("STRUCT_SLOSH", INFO, "basin", f"{label} ({length:g} ft)", "Earthquake slosh tops the rim",
                          f"Slosh height about {slosh_in:.1f} in vs {freeboard:g} in freeboard: water leaves the "
                          "basin in a design earthquake.", "Accept the spill, or add freeboard.", hd.citations[0])
        if b["kind"] == "vault" and g.get("lid_in"):
            span = min(g.get("inside_length_ft") or math.inf, g.get("inside_width_ft") or math.inf,
                       g.get("inside_diameter_ft") or math.inf)
            if span == math.inf:
                span = math.sqrt(g["inside_area_sqft"])
            lid_cls = (b.get("lid") or {}).get("load_class") or "pedestrian"
            lid = lid_slab(span, g["lid_in"], lid_cls, rules)
            record("lids", {"id": b["basin_id"], "label": label}, lid)
            if lid.status != "ok" or lid.value["thickness_in"] > g["lid_in"] + 1e-9:
                check("STRUCT_LID", WARNING, "basin", label, "Vault lid thicker than modeled",
                      f"A {lid.value['load_class']} lid spanning {span:.1f} ft needs {lid.value['thickness_in']:g} in; "
                      f"the vault was taken with {g['lid_in']:g} in.", "Set the lid thickness on the vault.",
                      lid.citations[0])
            if g.get("hatch_area_sqft"):
                bom.append(("structural.hatch", f"Access hatch, {lid.value['load_class']} rated", 1, "Nos",
                            f"access_hatch_{lid.value['load_class']}", {}))
        for pen in (b.get("quantities") or {}).get("penetrations", {}).get("rows", []):
            if pen["nominal_size"] == "unsized":
                continue
            try:
                ring = penetration_detail(pen["nominal_size"], d["wall_in"]).value["waterstop_ring_ft"]
            except ValueError:
                continue
            totals["waterstop_ring_ft"] += ring
        if g.get("placement") == "on_structure":
            fl = (b.get("results") or {}).get("floor_load", {}).get("value") or {}
            use = (b.get("floor_use") or site.get("floor_use"))
            bf = building_floor_load(fl.get("pressure_psf", 0.0), fl.get("capacity_psf"), use, fl.get("total_lb"))
            record("floors", {"id": b["basin_id"], "label": label}, bf)
            if bf.status == "bad" and fl.get("capacity_psf") is None:
                check("STRUCT_FLOOR_USE", WARNING, "basin", label, "Basin heavier than the floor's use rating",
                      bf.steps[0], "Send it to the building's structural engineer of record.", bf.citations[0])
    if totals["waterstop_ring_ft"]:
        bom.append(("structural.waterstop", "Waterstop, hydrophilic (Mirastop or Synkoflex SF302)",
                    totals["waterstop_ring_ft"], "Foot", "waterstop_hydrophilic", {}))

    # weir lips: listed rows, plus every weir feature not listed
    listed = {r.get("feature") for r in struct.get("weir_lips") or []}
    lips = list(struct.get("weir_lips") or [])
    for f in model.get("features") or []:
        if str(f.get("family") or "").lower() == "weir" and f.get("id") not in listed:
            lips.append({"id": f"{f.get('id')}:lip", "label": f.get("label"), "feature": f.get("id"), "auto": True})
    feats = {f.get("id"): f for f in model.get("features") or []}
    for row in lips:
        inputs = (feats.get(row.get("feature")) or {}).get("inputs") or {}
        res = weir_lip_sag(
            _num(row.get("span_in"), float(rules["weir_lip_support_spacing_in"])),
            _num(row.get("thickness_in"), float(rules["weir_lip_thickness_in"])),
            _num(row.get("flange_in"), float(rules["weir_lip_flange_in"])),
            _num(row.get("lip_width_in"), float(rules["weir_lip_width_in"])),
            _num(row.get("water_depth_in"), float(rules["weir_lip_water_in"])), row.get("material") or "304SS", row.get("support") or "continuous",
            _num(row.get("tolerance_in"), _num(inputs.get("tolerance_in"), 0.0625)), rules)
        if row.get("auto"):
            res.warnings.append("Lip section assumed from the house rules (3/16 in 304 stainless, 2 in flange, "
                                "1 in of water on it, supports at 48 in): enter the real lip to refine.")
        record("weir_lips", row, res)
        if res.status != "ok":
            check("STRUCT_WEIR_SAG", WARNING, "features", row.get("label") or row.get("id"),
                  "Weir lip sags past the edge tolerance",
                  next((w for w in res.warnings if " sags " in w), res.steps[1]),
                  f"Support the lip at {res.value['max_support_spacing_in']:.0f} in or stiffen the flange.",
                  res.citations[0])

    for row in complete("walls"):
        sur = row.get("surcharge")
        sur_psf = _num(sur)
        if sur_psf is None and sur:
            sur_psf = surcharge_load(sur, _num(row.get("stem_height_ft"), 0.0), float(rules["soil_pcf"])).value
        res = size_wall_footing(_num(row.get("stem_height_ft"), 0.0), _num(row.get("stem_in"), float(rules["wall_in"])),
                                row.get("retained") or "water", _num(row.get("water_depth_ft")), sur_psf or 0.0,
                                row.get("soil_class") or soil_class, _num(row.get("bearing_psf"), bearing), frost, sds,
                                rules=rules)
        record("walls", row, res)
        length = _num(row.get("length_ft"), 0.0)
        totals["site_concrete_cuft"] += res.value["concrete_cuft_per_ft"] * length
        totals["site_rebar_lb"] += res.value["rebar_lb_per_ft"] * length
        for label_, lb_ft in res.value["rebar_lb_per_ft_by_bar"].items():
            wall_rebar[label_] = wall_rebar.get(label_, 0.0) + lb_ft * length
        if res.status != "ok":
            check("STRUCT_WALL_STABILITY", WARNING, "basin", row.get("label") or row.get("id"),
                  "Freestanding wall fails a stability check", "; ".join(res.warnings[1:]) or res.steps[-1],
                  "Have the wall designed; widen the footing or add a key.", res.citations[0])

    wind_v = _num(site.get("wind_speed_mph"), float(rules["wind_speed_mph"]))
    exposure = site.get("wind_exposure") or rules["wind_exposure"]
    for row in complete("frames"):
        h, wd = _num(row.get("height_ft"), 0.0), _num(row.get("width_ft"), 0.0)
        wind = wind_panel_load(h, wd, _num(row.get("clearance_ft"), 0.0), wind_v, exposure,
                               _num(row.get("solid_ratio"), 1.0), _num(site.get("elevation_ft"), 0.0), rules)
        if wind.status == "incomplete":
            record("frames", row, wind)
            check("STRUCT_FRAME_INPUT", INFO, "features", row.get("label") or row.get("id"), "Frame inputs incomplete",
                  wind.warnings[-1], "Enter the frame's height, width and clearance.", wind.citations[0])
            continue
        frame = water_wall_frame(h, wd, wind.value["pressure_psf"], _num(row.get("header_plf"), 0.0),
                                 _num(row.get("post_spacing_ft")), row.get("material") or "A500C", rules)
        frame.steps.insert(0, wind.steps[-1])
        frame.citations += wind.citations
        record("frames", row, frame)
        steel(row.get("material") or "A500C", frame.value["steel_lb"])
        if frame.status != "ok":
            check("STRUCT_FRAME", WARNING, "features", row.get("label") or row.get("id"), "Feature frame fails",
                  frame.warnings[-1], "Shorten bays or have the frame engineered.", frame.citations[0])

    for row in complete("columns"):
        res = select_column(_num(row.get("height_ft"), 0.0), _num(row.get("axial_lb"), 0.0),
                            _num(row.get("lateral_plf"), 0.0), _num(row.get("top_lb"), 0.0),
                            row.get("material") or "A500C", rules)
        record("columns", row, res)
        steel(row.get("material") or "A500C", _section(res.value["section"])["wt"] * _num(row.get("height_ft"), 0.0))
        if res.status != "ok":
            check("STRUCT_COLUMN", WARNING, "features", row.get("label") or row.get("id"), "Tower or column fails",
                  res.warnings[-1], "Engineer the tower.", res.citations[0])

    for row in complete("beams"):
        res = deck_beam(_num(row.get("span_ft"), 0.0), _num(row.get("tributary_ft"), 0.0),
                        _num(row.get("dead_psf"), 15.0), _num(row.get("live_psf"), 100.0),
                        row.get("material") or "steel", row.get("shape") or "W", rules)
        record("beams", row, res)
        if res.value.get("material") == "steel":
            steel("A992", res.value["steel_lb"])
        else:
            totals["site_concrete_cuft"] += res.value.get("concrete_cuft") or 0.0
        if res.status != "ok":
            check("STRUCT_BEAM", WARNING, "basin", row.get("label") or row.get("id"), "Deck beam fails",
                  res.warnings[-1], "Deeper section or a shorter span.", res.citations[0])

    for row in complete("gratings"):
        res = grating(_num(row.get("span_in"), 0.0), row.get("load_class") or "pedestrian",
                      _num(row.get("area_sqft"), 0.0), rules)
        record("gratings", row, res)
        totals["grating_sqft"] += _num(row.get("area_sqft"), 0.0)
        if res.status == "ok":
            bom.append((f"structural.grating.{res.value['load_class']}",
                        f"Steel bar grating, {res.value['bearing_bar']} bearing bars, {res.value['load_class']}",
                        _num(row.get("area_sqft"), 0.0), "Square Foot", "bar_grating", {}))
        else:
            check("STRUCT_GRATING", WARNING, "basin", row.get("label") or row.get("id"), "No grating spans this",
                  res.warnings[-1], "Add a support beam.", res.citations[0])

    for row in complete("trench_drains"):
        res = trench_drain(_num(row.get("length_ft"), 0.0), row.get("load_class") or "pedestrian", rules=rules)
        record("trench_drains", row, res)
        totals["trench_drain_pieces"] += res.value["pieces"]
        bom.append((f"structural.trench_drain.{res.value['load_class']}",
                    f"Trench drain channel + grate, EN 1433 {res.value['en1433_class']}", res.value["pieces"], "Nos",
                    "trench_drain", {}))

    for row in complete("veneer"):
        res = veneer_attachment(_num(row.get("area_sqft"), 0.0), _num(row.get("thickness_in"), 1.25),
                                row.get("material") or "granite", row.get("method") or "anchored", sdc or "D",
                                _num(row.get("anchor_capacity_lb")), rules)
        record("veneer", row, res)
        totals["veneer_anchors"] += res.value["anchors"]
        bom.append(("structural.veneer_anchors", "Veneer anchors, stainless", res.value["anchors"], "Nos",
                    "veneer_anchor_ss", {}))
        if res.status != "ok":
            check("STRUCT_VENEER", WARNING, "features", row.get("label") or row.get("id"), "Veneer too heavy to adhere",
                  res.warnings[1], "Anchor it mechanically.", res.citations[0])

    for row in complete("pedestals"):
        res = pedestal_check(_num(row.get("length_ft"), 0.0), _num(row.get("width_ft"), 0.0),
                             _num(row.get("height_ft"), 0.0), _num(row.get("unit_weight_pcf"), 150.0),
                             _num(row.get("top_load_lb"), 0.0), _num(row.get("top_cg_ft")), _num(row.get("wind_lb"), 0.0),
                             _num(row.get("wind_height_ft")), sds, row.get("on") or "slab", soil_class, bearing,
                             row.get("material") or "concrete", rules)
        record("pedestals", row, res)
        totals["site_concrete_cuft"] += res.value["concrete_cuft"]
        if res.status != "ok":
            check("STRUCT_PEDESTAL", WARNING, "features", row.get("label") or row.get("id"), "Pedestal unstable",
                  res.warnings[-1], "Widen the base or anchor it.", res.citations[0])

    for row in complete("pads"):
        res = equipment_pad(_num(row.get("length_in"), 24.0), _num(row.get("width_in"), 24.0),
                            _num(row.get("weight_lb"), 0.0), _num(row.get("cg_height_in"), 12.0), sds,
                            int(_num(row.get("n_anchors"), 4)), _num(row.get("anchor_dia_in"), 0.5),
                            _num(row.get("embed_in"), 4.0), row.get("anchor_type") or "cast-in",
                            row.get("anchor_grade") or "316SS", rules=rules)
        record("pads", row, res)
        totals["site_concrete_cuft"] += res.value["concrete_cuft"]
        totals["equipment_anchors"] += res.value["anchors"]
        totals["pads"] += 1
        if res.status == "bad":  # "warn" only means no SDS was given; the project check already says so
            check("STRUCT_ANCHORS", WARNING, "pump", row.get("label") or row.get("id"), "Equipment anchors overstressed",
                  next((w for w in res.warnings if "overstressed" in w), res.steps[-1]), "Larger or deeper anchors.",
                  res.citations[1])

    def bad_size(group, row, err):
        check("STRUCT_PIPE_SIZE", INFO, "piping", row.get("label") or row.get("id"), "Pipe size not set",
              f"{group}: {err}", "Enter a nominal pipe size such as 3 or 1-1/2.", "")

    for row in complete("thrust_blocks"):
        try:
            nominal_size(row.get("size") or 2)
        except ValueError as err:
            bad_size("thrust block", row, err)
            continue
        res = thrust_block(row.get("size") or 2, _num(row.get("pressure_psi"), 0.0), str(row.get("fitting") or "90"),
                           _num(row.get("soil_bearing_psf")), rules)
        record("thrust_blocks", row, res)
        totals["site_concrete_cuft"] += (res.value or {}).get("concrete_cuft", 0.0) * int(_num(row.get("count"), 1))
        if res.status == "incomplete":
            check("STRUCT_THRUST_INPUT", INFO, "piping", row.get("label") or row.get("id"), "Thrust block not computed",
                  res.warnings[-1], "Use a bend angle, tee or dead end, or size it by hand.", "")

    for row in complete("bracing"):
        try:
            nominal_size(row.get("size") or 2)
        except ValueError as err:
            bad_size("bracing", row, err)
            continue
        res = seismic_bracing(row.get("size") or 2, _num(row.get("run_ft"), 0.0), sdc or "D", 1.0,
                              row.get("material") or "pvc", rules)
        record("bracing", row, res)
        n = res.value["lateral_braces"] + res.value["longitudinal_braces"]
        totals["braces"] += n
        bom.append(("structural.seismic_braces", "Seismic pipe brace kits", n, "Nos", "seismic_brace", {}))

    for row in complete("moorings"):
        res = mooring(_num(row.get("above_water_sqft"), 0.0), _num(row.get("submerged_sqft"), 0.0),
                      _num(site.get("mooring_wind_mph")), _num(row.get("current_fps"), 0.5),
                      int(_num(row.get("n_anchors"), 3)), _num(row.get("line_angle_deg"), 45.0), rules)
        record("moorings", row, res)
        n = res.value["anchors"]
        totals["mooring_blocks"] += n
        totals["site_concrete_cuft"] += res.value["anchor_cuft"] * n
        dry = math.ceil(res.value["anchor_dry_lb"] / 50.0 - 1e-9) * 50  # one line per block size (50 lb steps)
        bom.append((f"structural.mooring_blocks.{dry}", f"Concrete mooring block, {dry} lb dry",
                    n, "Nos", "mooring_block", {}))

    rig = struct.get("rigging")
    if rig:
        rig = next(complete("rigging", [rig]), None)
    if rig:
        res = rig_plan(_num(rig.get("hung_lb_per_ft"), 0.0), _num(rig.get("hung_length_ft"), 0.0),
                       int(_num(rig.get("hanger_spans"), 1)), _num(rig.get("hanger_span_ft"), 1.0),
                       _num(rig.get("overhang_ft"), 0.0), _num(rig.get("curtain_width_ft")), rig.get("mount") or "towers",
                       rig.get("truss") or "box12", _num(rig.get("header_height_ft"), 10.0),
                       int(_num(rig.get("picks"), 2)), _num(rig.get("riser_lb"), 0.0), rig.get("feed") or "end",
                       _num(rig.get("dyn"), 1.25))
        record("rigging", rig, res)
        if res.status != "ok":
            check("STRUCT_RIGGING", BLOCKER, "features", rig.get("label") or "rigging", "Pick over a 2-ton hoist",
                  res.warnings[1], "Add picks.", res.citations[0])

    if totals["site_concrete_cuft"]:
        waste = 1 + float(rules["waste_factor"])
        yd3 = math.ceil(totals["site_concrete_cuft"] / 27.0 * waste / float(rules["order_increment_yd3"]) - 1e-9) * \
            float(rules["order_increment_yd3"])
        bom.append(("structural.concrete_site", "Concrete for walls, pads, pedestals, thrust and mooring blocks "
                    "(order, incl. waste)", yd3, "Cubic Yard", "ready_mix_concrete_4500_air", {}))
    for bar_label, lb in wall_rebar.items():
        bom.append((f"structural.rebar.{bar_label}.lb", f"Rebar {bar_label} Grade 60 (ASTM A615), purchase weight",
                    lb * (1 + float(rules["waste_factor"])), "Pound", f"rebar_{bar_label.lstrip('#')}_grade60", {}))
    if totals["equipment_anchors"]:
        bom.append(("structural.anchors", "Anchor bolts, stainless, equipment pads", totals["equipment_anchors"], "Nos",
                    "anchor_bolt_ss_wedge", {}))

    return {
        "results": results,
        "seismic": seismic.to_dict(),
        "notes": freeze_thaw_notes(frost, rules).to_dict(),
        "checks": checks,
        "bom": bom,
        "totals": totals,
        "disclaimer": DISCLAIMER,
    }
