"""``structural_takeoff(model, rules)``: ladder step 10b in one call.

For every basin and vault in a schema-2 design model it runs thickness →
shell → checks → rebar → concrete and site quantities, and returns one plain
dict for phase E's ``derive()``: per-basin quantities and CalcResults, the
rebar schedule, typed checks, totals, and BOM lines in the shape phase B's
``bom.py`` merges (``{section: "Structural", key, description, qty, uom,
item_hint}``; a line with ``priced: False`` is a count shown beside a priced
line for the same material and must not be priced twice).

Unlike the calcs it chains, this returns a dict rather than a ``CalcResult``:
it is an aggregator, the structural sibling of ``derive()``. Every number in
it traces to a ``CalcResult`` under ``basins[i]["results"]``.
"""

from __future__ import annotations

import math
import re

from .concrete import (
    anchor_bolts,
    coating_area,
    concrete_volumes,
    excavation,
    formwork,
    gravel_base,
    joints,
    mix_notes,
    penetrations,
)
from .defaults import (
    CIT_ACI350_ST,
    CIT_DOC0119_ANALYSIS,
    CIT_DOC0119_CONCRETE,
    CIT_IBC_SOIL,
    CIT_OSHA_SLOPE,
    CIT_USACE_FLOTATION,
    DISCLAIMER,
    merged_rules,
)
from .general import general_structure
from .geometry import basin_shell, basin_spec
from .rebar import rebar_schedule
from .sizing import (
    bearing_check,
    cover_check,
    floor_load,
    lateral_soil_heights,
    select_thickness,
    shrinkage_temperature_check,
    uplift_check,
    wall_bending_check,
)

SCHEMA = 1
SECTION = "basin"  # issues.py section key the page jumps to
BLOCKER, WARNING, INFO = "blocker", "warning", "info"


def _check(code, severity, spec, title, detail, remedy, citation):
    label = spec.get("label") or spec.get("id") or "basin"
    return {"key": f"{code}|{label}", "code": code, "severity": severity, "title": title, "detail": detail,
            "remedy": remedy, "section": SECTION, "basin": spec.get("id"), "citation": citation}


def _checks(spec, det, shell, res, rules) -> list[dict]:
    out = []
    s = shell.value
    if det.value["beyond_standard_details"]:
        out.append(_check("STRUCT_BEYOND_STANDARD", WARNING, spec, "Beyond Sapphire's standard concrete details",
                          det.warnings[-1], "Get a structural engineer's analysis for this basin.",
                          CIT_DOC0119_ANALYSIS))
    for key, what in (("wall_water", "full of water"), ("wall_soil", "empty with backfill")):
        r = res.get(key)
        if r is not None and r.status == "bad":
            v = r.value
            out.append(_check(
                f"STRUCT_WALL_{'WATER' if key == 'wall_water' else 'SOIL'}", WARNING, spec,
                f"Wall needs more steel than the standard detail ({what})",
                f"{v['height_ft']:.1f} ft wall: needs "
                + ("more than any bar area can give (section too thin)" if v["as_required_in2_per_ft"] is None
                   else f"{v['as_required_in2_per_ft']:.3f} in²/ft")
                + f", detail gives {v['as_provided_in2_per_ft']:.3f}; shear {'ok' if v['shear_ok'] else 'fails'}.",
                "Use a thicker wall from the DOC-0119 table or have the wall designed.", r.citations[1]))
    for key, element in (("cover_wall", "wall"), ("cover_slab", "slab")):
        r = res[key]
        if r.status == "bad":
            out.append(_check(f"STRUCT_COVER_{element.upper()}", WARNING, spec,
                              f"{element.title()} too thin for 2 in cover", r.warnings[-1],
                              f"Make the {element} at least {r.value['required_in']:.1f} in.", CIT_DOC0119_CONCRETE))
    light = [res[k].value for k in ("st_wall", "st_slab") if res[k].status != "ok"]
    if light:
        out.append(_check(
            "STRUCT_ACI350_ST", INFO, spec, "Standard detail is lighter than ACI 350's shrinkage steel",
            "; ".join(f"{v['element']}: {v['provided_in2_per_ft']:.3f} in²/ft at {v['spacing_in']:g} in vs "
                      f"{v['required_in2_per_ft']:.3f} in²/ft at 12 in maximum" for v in light)
            + ". DOC-0119's detail governs.",
            "Accept per DOC-0119, or tighten spacing for a crack-controlled, water-tight shell.", CIT_ACI350_ST))
    up = res["uplift"]
    if up.status in ("bad", "warn"):
        v = up.value
        out.append(_check(
            "STRUCT_UPLIFT", BLOCKER if up.status == "bad" else WARNING, spec, "Empty basin can float",
            f"Groundwater {v['head_ft']:.2f} ft above the slab bottom lifts {v['uplift_lb']:.0f} lb against "
            f"{v['dead_weight_lb']:.0f} lb of concrete: FS {v['factor_of_safety']:.2f} < {v['required_fs']:g}.",
            "Thicken the slab or extend it as a toe under backfill, add a hydrostatic relief valve or "
            "underdrain, or never drain it with the water table high.", CIT_USACE_FLOTATION))
    br = res["bearing"]
    if br.status == "warn":
        out.append(_check("STRUCT_BEARING", WARNING, spec, "Soil bearing exceeded", br.warnings[-1],
                          "Get a geotechnical bearing value, or spread the load with a wider slab.", CIT_IBC_SOIL))
    fl = res["floor_load"]
    if fl.status == "bad":
        out.append(_check("STRUCT_FLOOR_LOAD", BLOCKER, spec, "Floor cannot carry the basin", fl.warnings[-1],
                          "Have the building's engineer verify or strengthen the floor.", fl.citations[0]))
    elif s["placement"] == "on_structure" and fl.value["capacity_psf"] is None and not spec.get("floor_use"):
        out.append(_check("STRUCT_FLOOR_UNKNOWN", WARNING, spec, "Floor capacity unknown",
                          f"The basin puts {fl.value['pressure_psf']:.0f} psf on the floor it stands on.",
                          "Enter the floor's live-load capacity or ask the building's engineer.", fl.citations[0]))
    ex = res["excavation"]
    depth = ex.value.get("depth_ft", 0.0)
    if depth + 1e-9 >= float(rules["excavation_slope_free_ft"]):
        out.append(_check("STRUCT_EXCAVATION_DEPTH", INFO, spec, "Excavation needs a protective system",
                          f"Excavation {depth:.1f} ft deep: OSHA protective system required (sloped or shored).",
                          "Slope, bench or shore the excavation; a competent person on site.", CIT_OSHA_SLOPE))
    return out


def _r(x, nd=2):
    return round(float(x), nd)


def structural_takeoff(model: dict, rules: dict | None = None) -> dict:
    """Run the structural chain over every basin and vault in ``model``.

    Basins come from ``model["basins"]`` (a row with ``kind: "vault"`` is a
    vault) and ``model["vaults"]`` (each forced to a vault). Site keys read:
    ``groundwater_depth_ft``, ``soil_bearing_psf``."""
    rules = merged_rules(rules)
    model = model or {}
    site = model.get("site") or {}
    rows = list(model.get("basins") or []) + [dict(v, kind="vault") for v in (model.get("vaults") or [])]

    basins_out, schedule, checks = [], [], []
    bom: dict[str, dict] = {}
    totals = {k: 0.0 for k in (
        "concrete_net_yd3", "concrete_with_waste_yd3", "concrete_order_yd3", "rebar_lb", "rebar_purchase_lb",
        "rebar_ft", "gravel_tons", "gravel_yd3", "excavation_yd3", "backfill_yd3", "haul_yd3", "formwork_sqft",
        "waterstop_ft", "sealant_ft", "coating_sqft", "curing_sqft", "penetrations", "water_gal", "chairs",
        "tie_wire_lb")}
    stock = f"{float(rules['stock_length_ft']):g}"
    mix = mix_notes(rules)

    def add(key, description, qty, uom, item_hint, **extra):
        if qty <= 0:
            return
        line = bom.setdefault(key, {"section": "Structural", "key": key, "description": description, "qty": 0.0,
                                    "uom": uom, "item_hint": item_hint, **extra})
        line["qty"] += qty

    for raw in rows:
        spec = basin_spec(raw, site, rules)
        det = select_thickness(spec, rules)
        d = det.value
        shell = basin_shell(spec, d["slab_in"], d["wall_in"], rules)
        if shell.status == "incomplete":
            checks.append(_check("STRUCT_INCOMPLETE", INFO, spec, "Basin dimensions incomplete",
                                 shell.warnings[0], "Enter the basin's shape dimensions and depth.",
                                 CIT_DOC0119_CONCRETE))
            basins_out.append({"basin_id": spec["id"], "label": spec["label"], "kind": spec["kind"],
                               "complete": False, "results": {"shell": shell.to_dict()}})
            continue
        s = shell.value
        h = s["wall_height_ft"]
        res = {"thickness": det, "shell": shell}
        res["wall_water"] = (None if spec["kind"] == "vault" else
                             wall_bending_check(h, d["wall_in"], d["wall_bar"], d["wall_spacing_in"],
                                                d["wall_mats"], rules, case="water"))
        soil_h, gw_h = lateral_soil_heights(h, s["rim_above_grade_ft"], s["groundwater_depth_ft"])
        res["wall_soil"] = (wall_bending_check(soil_h, d["wall_in"], d["wall_bar"], d["wall_spacing_in"],
                                               d["wall_mats"], rules, case="soil",
                                               top="pinned" if s["lid_in"] else "free", groundwater_ft=gw_h)
                            if soil_h > 0 else None)
        res["cover_wall"] = cover_check(d["wall_in"], d["wall_bar"], d["wall_mats"], rules)
        res["cover_slab"] = cover_check(d["slab_in"], d["slab_bar"], d["slab_mats"], rules)
        res["joints"] = joints(shell, rules)
        jv = res["joints"].value
        res["st_wall"] = shrinkage_temperature_check(d["wall_in"], d["wall_bar"], d["wall_spacing_in"],
                                                     d["wall_mats"], jv["wall_joint_spacing_ft"], rules, "wall")
        res["st_slab"] = shrinkage_temperature_check(d["slab_in"], d["slab_bar"], d["slab_spacing_in"],
                                                     d["slab_mats"], jv["slab_joint_spacing_ft"], rules, "slab")
        res["uplift"] = uplift_check(shell, rules)
        res["bearing"] = bearing_check(shell, rules)
        res["floor_load"] = floor_load(shell, rules)
        res["concrete"] = concrete_volumes(shell, rules)
        res["gravel"] = gravel_base(shell, rules)
        res["excavation"] = excavation(shell, rules)
        res["formwork"] = formwork(shell, rules)
        res["coating"] = coating_area(shell, rules)
        res["penetrations"] = penetrations(model, spec["id"])
        res["rebar"] = rebar_schedule(shell, det, rules)

        basin_checks = _checks(spec, det, shell, res, rules)
        checks += basin_checks
        rb = res["rebar"].value
        for mk in rb["marks"]:
            schedule.append({"basin_id": spec["id"], **mk})
        c, g, ex = res["concrete"].value, res["gravel"].value, res["excavation"].value
        purchase_lb = sum(v["sticks"][stock]["purchase_lb"] for v in rb["by_size"].values())
        q = {
            "concrete": c, "rebar_lb": rb["weight_lb"], "rebar_purchase_lb": purchase_lb, "rebar_ft": rb["total_ft"],
            "rebar_by_size": rb["by_size"], "rebar_by_element": rb["elements"], "gravel": g, "excavation": ex,
            "formwork_sqft": res["formwork"].value["sqft"], "waterstop_ft": jv["waterstop_ft"],
            "sealant_ft": jv["sealant_ft"], "coating_sqft": res["coating"].value["coating_sqft"],
            "curing_sqft": res["coating"].value["curing_sqft"], "penetrations": res["penetrations"].value,
            "chairs": rb["chairs"], "wall_spacers": rb["wall_spacers"], "ties": rb["ties"],
            "tie_wire_lb": rb["tie_wire_lb"],
        }
        for key, val in (("concrete_net_yd3", c["net_yd3"]), ("concrete_with_waste_yd3", c["with_waste_yd3"]),
                         ("concrete_order_yd3", c["order_yd3"]), ("rebar_lb", rb["weight_lb"]),
                         ("rebar_purchase_lb", purchase_lb), ("rebar_ft", rb["total_ft"]),
                         ("gravel_tons", g["tons"]), ("gravel_yd3", g["yd3"]), ("excavation_yd3", ex["yd3"]),
                         ("backfill_yd3", ex["backfill_yd3"]), ("haul_yd3", ex["haul_yd3"]),
                         ("formwork_sqft", q["formwork_sqft"]), ("waterstop_ft", q["waterstop_ft"]),
                         ("sealant_ft", q["sealant_ft"]), ("coating_sqft", q["coating_sqft"]),
                         ("curing_sqft", q["curing_sqft"]), ("penetrations", q["penetrations"]["count"]),
                         ("water_gal", s["water_volume_gal"]), ("chairs", rb["chairs"]),
                         ("tie_wire_lb", rb["tie_wire_lb"])):
            totals[key] += val

        add("structural.concrete", "Ready-mix concrete, 4,500 psi, air-entrained (order, incl. waste)",
            c["order_yd3"], "Cubic Yard", "ready_mix_concrete_4500_air")
        if spec["kind"] == "basin":
            add("structural.admixture", "Crystalline waterproofing admixture (Xypex Admix C / Penetron), dosed "
                "per yd³", c["order_yd3"], "Cubic Yard", "crystalline_waterproofing_admixture")
        for size in rb["by_size"].values():
            sticks = size["sticks"][stock]
            add(f"structural.rebar.{size['bar']}.lb", f"Rebar {size['bar']} Grade 60 (ASTM A615), purchase weight",
                sticks["purchase_lb"], "Pound", f"rebar_{size['bar'].lstrip('#')}_grade60")
            add(f"structural.rebar.{size['bar']}.bars{stock}", f"Rebar {size['bar']}, {stock} ft bars (count)",
                sticks["count"], "Nos", f"rebar_{size['bar'].lstrip('#')}_{stock}ft", priced=False)
        # ERPNext has no "Ton" UOM: gravel goes out in pounds (2,000 lb per short ton)
        add("structural.gravel", f"Gravel base, 1 in minus, {rules['gravel_in']:g} in deep (short tons × 2,000)",
            g["tons"] * 2000.0,
            "Pound", "gravel_1in_minus")
        add("structural.excavation", "Excavation (bank)", ex["yd3"], "Cubic Yard", "excavation")
        add("structural.backfill", "Backfill, compacted", ex["backfill_yd3"], "Cubic Yard", "backfill")
        add("structural.haul", "Haul-off (loose)", ex["haul_yd3"], "Cubic Yard", "haul_off")
        add("structural.formwork", "Formwork (contact area)", q["formwork_sqft"], "Square Foot", "formwork")
        add("structural.waterstop", "Waterstop, hydrophilic (Mirastop or Synkoflex SF302)", q["waterstop_ft"],
            "Foot", "waterstop_hydrophilic")
        add("structural.sealant", "Joint sealant, polyurethane (Dymonic 100)", q["sealant_ft"], "Foot",
            "joint_sealant_dymonic_100")
        coat_key = "structural.coating.exterior" if spec["kind"] == "vault" else "structural.coating"
        add(coat_key, "Waterproofing membrane, " + res["coating"].value["coating_where"],
            q["coating_sqft"], "Square Foot", "waterproofing_membrane")
        add("structural.curing", "Curing compound (exposed faces)", q["curing_sqft"], "Square Foot",
            "curing_compound")
        add("structural.chairs", "Rebar chairs / bolsters", rb["chairs"], "Nos", "rebar_chair")
        add("structural.spacers", "Wall bar spacers (side-form wheels)", rb["wall_spacers"], "Nos", "rebar_spacer")
        add("structural.tie_wire", "Tie wire, 16 ga black annealed", rb["tie_wire_lb"], "Pound", "tie_wire_16ga")
        for size, n in q["penetrations"]["by_size"].items():
            slug = re.sub(r"[^0-9a-z.]+", "_", str(size).lower().replace('"', "in")).strip("_")
            add(f"structural.link_seal.{slug}", f"Link-Seal, stainless, for {size} pipe (core + non-shrink grout)",
                n, "Nos", f"link_seal_ss_{slug}")

        basins_out.append({
            "basin_id": spec["id"], "label": spec["label"], "kind": spec["kind"], "complete": True,
            "lid": spec["lid"], "floor_use": spec["floor_use"],
            "detail": d, "geometry": s, "quantities": q, "checks": basin_checks,
            "results": {k: v.to_dict() for k, v in res.items() if v is not None},
        })

    general = general_structure(model, basins_out, rules)
    checks += general["checks"]
    for key, description, qty, uom, hint, extra in general["bom"]:
        add(key, description, qty, uom, hint, **extra)
    totals.update({k: (totals.get(k, 0) + v) for k, v in general["totals"].items()})
    anchors = anchor_bolts(model, rules)
    if not (model.get("structure") or {}).get("pads"):  # pads count their own anchors
        add("structural.anchors", "Anchor bolts, stainless wedge, equipment", anchors.value["count"], "Nos",
            "anchor_bolt_ss_wedge")
        totals["anchors"] = anchors.value["count"]
    else:
        totals["anchors"] = general["totals"]["equipment_anchors"]
    for key in ("penetrations", "chairs"):
        totals[key] = int(totals[key])
    totals["trucks"] = math.ceil(totals["concrete_order_yd3"] / float(rules["truck_yd3"]) - 1e-9) \
        if totals["concrete_order_yd3"] > 0 else 0

    lines = []
    for line in bom.values():
        line["qty"] = _r(line["qty"], 2) if line["uom"] not in ("Nos",) else int(math.ceil(line["qty"] - 1e-9))
        lines.append(line)
    counts: dict[str, int] = {}
    for c in checks:
        counts[c["key"]] = counts.get(c["key"], 0) + 1
    for c in checks:  # two basins with the same label: tell them apart by id, stable when reordered
        if counts[c["key"]] > 1 and c.get("basin"):
            c["key"] = f"{c['key']} [{c['basin']}]"
    sev = {BLOCKER: 0, WARNING: 1, INFO: 2}
    checks.sort(key=lambda c: sev[c["severity"]])
    return {
        "schema": SCHEMA,
        "disclaimer": DISCLAIMER,
        "basins": basins_out,
        "rebar_schedule": schedule,
        "checks": checks,
        "totals": totals,
        "bom": lines,
        "mix": mix.value,
        "anchors": anchors.to_dict(),
        "structure": general["results"],
        "seismic": general["seismic"],
        "notes": general["notes"],
        "rules": rules,
        "warnings": [DISCLAIMER],
    }
