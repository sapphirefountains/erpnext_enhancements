"""The bill of materials: pipe, fittings, valves, nozzles, equipment, consumables and labor.

Generalized from Digital Rain Curtain ``src/hydro/checks.js`` ``bomRows`` and ``src/hydro/route.js``
``cutList`` / ``fittingCounts`` (audit 1.19): DRC's grouping logic, run on the design model's
network instead of the rain curtain's route. It also answers the open "aggregated Fitting
Schedule" item in ``SOURCE_DATA_AUDIT.md`` as an engine function rather than Jinja.

* Pipe by material and size, every branch counted, plus 10% waste, in feet and in 20 ft sticks
  (``STICK_FT``); a manifold terminal adds its header (span plus 12 cm overhang each end, as DRC).
* Fittings grouped by type and size; the valve types go to their own section. Straight runs
  longer than a stick get a coupling per extra stick (DRC ``stickCouplings``).
* Nozzles from the terminals, equipment from the pumps (with the VFD or PRV, bypass orifice and
  arrestor their control and surge sections chose), the equipment curves on segments, and the
  model's ``equipment`` list.
* Consumables: primer and cement (PVC) or solder and flux (copper) by joint count, and hangers for
  segments marked ``exposed`` (``structure.pipe_hangers``).
* Labor from the house rules: hours per feature family and per square foot of basin.

Every line has the one shape other phases emit too (Electrical, Controls, Structural, families)::

    {"section", "key", "description", "qty", "uom", "item_hint"}

and :func:`merge_sections` folds any number of such lists into one, summing a line that appears in
more than one. ``key`` is what makes two lines the same thing; ``item_hint`` carries what phase E
needs to match an ERPNext Item (material, size, catalog item).
"""

from __future__ import annotations

import math

from .envelope import CalcResult, make_input
from .hydraulics._checks import INFO, make_check
from .hydraulics.basin_checks import plan_area_sf
from .hydraulics.catalog import pipe_spec_si
from .hydraulics.fittings import FITTINGS, resolve_fitting
from .hydraulics.manifold import OVERHANG_M
from .hydraulics.units import M_PER_FT
from .structure import pipe_hangers

CIT_BOM = "Digital Rain Curtain checks.js bomRows / route.js cutList (grouping), generalized to the design network"
CIT_RULES = "Sapphire standard 2026-11-01 (Water Engineering Settings house rules; confirm with the designers)"

STICK_FT = 20.0  # pipe comes in 20 ft sticks (DRC STICK_M = 6.096 m)
WASTE = 0.10
VALVE_KEYS = {"ball", "gate", "globe", "butterfly", "check", "lift_check", "foot_valve"}
EQUIPMENT_KEYS = {"strainer", "filter"}
RULE_DEFAULTS = {
    "waste_pct": 10.0,
    "joints_per_quart": 80,  # primer / cement coverage, joints of 1-2 in pipe per quart (Sapphire standard)
    "joints_per_lb_solder": 60,
}

SECTION_ORDER = ["Piping", "Valves", "Nozzles", "Equipment", "Electrical", "Controls", "Structural", "Consumables", "Labor"]


def line(section: str, key: str, description: str, qty: float, uom: str, item_hint: dict | None = None) -> dict:
    """One BOM line in the shape every phase emits."""
    return {"section": section, "key": key, "description": description, "qty": qty, "uom": uom, "item_hint": item_hint or {}}


def _size_label(spec) -> str:
    return spec.nominal or f"{spec.id_mm / 25.4:.3f} in bore"


def _spec(row: dict):
    id_in = row.get("id_in")
    wall_in = row.get("wall_in")
    return pipe_spec_si(row.get("material") or "SCH40 PVC", row.get("nominal_size"),
                        float(id_in) * 25.4 if id_in else 0, float(wall_in) * 25.4 if wall_in else 0)


def _fit_label(key: str) -> str:
    k = resolve_fitting(key) or key
    return (FITTINGS.get(k) or {}).get("label") or k


def cut_list(segments: list[dict]) -> list[dict]:
    """Each run cut from 20 ft sticks: pieces, sticks and the couplings between them, per branch.

    By length only: the design model has no route geometry yet, so fitting take-outs (DRC
    ``takeoutM``) are left to the field, as DRC leaves the pump-room run."""
    out = []
    for s in segments or []:
        length = float(s.get("length_ft") or 0)
        if length <= 0:
            continue
        spec = _spec(s)
        sticks = max(1, math.ceil(length / STICK_FT - 1e-9))
        pieces = [STICK_FT] * (sticks - 1) + [length - STICK_FT * (sticks - 1)]
        for b in range(int(s.get("branches") or 1)):
            out.append({"segment": s.get("id") or "", "branch": b + 1, "material": spec.label, "nominal_size": _size_label(spec),
                        "length_ft": length, "sticks": sticks, "couplings": sticks - 1, "pieces_ft": pieces})
    return out


def fitting_counts(segments: list[dict], *, stick_couplings: bool = True) -> list[dict]:
    """Fittings to buy, grouped by type, size and material (``fittingCounts``), branches counted."""
    counts: dict = {}
    order = []

    def add(key: str, spec, n: float) -> None:
        k = (key, _size_label(spec), spec.label)
        if k not in counts:
            counts[k] = 0
            order.append(k)
        counts[k] += n

    for s in segments or []:
        spec = _spec(s)
        mult = int(s.get("branches") or 1)
        for f in s.get("fittings") or []:
            key = resolve_fitting(f.get("key") or f.get("type")) or (f.get("key") or f.get("type"))
            n = float(f.get("qty", f.get("count", 1)) or 0)
            if n > 0:
                add(key, spec, n * mult)
        if stick_couplings:
            length = float(s.get("length_ft") or 0)
            extra = max(0, math.ceil(length / STICK_FT - 1e-9) - 1) if length > 0 else 0
            if extra:
                add("coupling", spec, extra * mult)
    return [{"key": k[0], "label": _fit_label(k[0]), "nominal_size": k[1], "material": k[2], "qty": counts[k]} for k in order]


def merge_sections(*lists) -> list[dict]:
    """Fold BOM line lists into one: lines with the same ``(section, key, uom)`` add their quantities;
    the first description and item hint win. Sections come out in :data:`SECTION_ORDER` (others after,
    in the order met), lines in the order first met."""
    merged: dict = {}
    order = []
    for lst in lists:
        for ln in lst or []:
            section = ln.get("section") or "Other"
            key = ln.get("key") or (ln.get("description") or "").strip().lower()
            k = (section, key, ln.get("uom") or "")
            if k in merged:
                merged[k]["qty"] += float(ln.get("qty") or 0)
            else:
                merged[k] = {"section": section, "key": key, "description": ln.get("description") or key,
                             "qty": float(ln.get("qty") or 0), "uom": ln.get("uom") or "", "item_hint": dict(ln.get("item_hint") or {})}
                order.append(k)
    rank = {s: i for i, s in enumerate(SECTION_ORDER)}
    seen_sections = []
    for k in order:
        if k[0] not in seen_sections:
            seen_sections.append(k[0])
    sec_rank = {s: (rank.get(s, len(SECTION_ORDER)), seen_sections.index(s)) for s in seen_sections}
    return [merged[k] for k in sorted(order, key=lambda k: (sec_rank[k[0]], order.index(k)))]


def labor_rows(model: dict, rules: dict | None) -> tuple[list[dict], list[dict]]:
    """Labor assemblies: ``labor_hours_per_family`` (``{family: hours per feature}``) and
    ``labor_hours_per_sf_basin``, from the house rules. Returns (lines, checks)."""
    rules = rules or {}
    per_family = rules.get("labor_hours_per_family") or {}
    per_sf = rules.get("labor_hours_per_sf_basin")
    out, checks = [], []
    for f in model.get("features") or []:
        fam = f.get("family") or ""
        hrs = per_family.get(fam)
        if hrs is None:
            continue
        out.append(line("Labor", f"labor|family|{fam}", f"Install {fam} feature ({f.get('label') or f.get('id')})",
                        float(hrs) * int(f.get("count") or 1), "hr", {"kind": "labor", "family": fam}))
    if per_sf:
        area = sum(plan_area_sf(b) for b in model.get("basins") or [])
        if area:
            out.append(line("Labor", "labor|basin", f"Build basins ({area:.0f} sq ft)", float(per_sf) * area, "hr", {"kind": "labor", "basin_sf": area}))
    if not per_family and not per_sf:
        checks.append(make_check("bom.no_labor_rules", INFO, "No labor rates in the house rules",
                                 "Set hours per family and per square foot of basin in Water Engineering Settings.", "outputs"))
    return out, checks


def bom_rows(model: dict, *, rules: dict | None = None) -> CalcResult:
    """Every hydraulic line of the design's BOM, plus labor; value ``{lines, cut_list, fittings, checks}``."""
    rr = {**RULE_DEFAULTS, **(rules or {})}
    waste = float(rr["waste_pct"]) / 100
    net = model.get("network") or {}
    segs = [s for s in net.get("segments") or []]
    lines: list = []
    checks: list = []

    # Pipe (a manifold terminal's header counts as pipe too)
    pipe: dict = {}
    pipe_order = []

    def add_pipe(spec, ft: float) -> None:
        k = (spec.material, _size_label(spec))
        if k not in pipe:
            pipe[k] = [spec, 0.0]
            pipe_order.append(k)
        pipe[k][1] += ft

    for s in segs:
        if not (s.get("nominal_size") or s.get("id_in")):
            checks.append(make_check("bom.unsized_segment", INFO, f"Segment {s.get('id')} has no size yet", "Its pipe is left off the BOM until it is sized.",
                                     "outputs", ref=s.get("id") or ""))
            continue
        add_pipe(_spec(s), float(s.get("length_ft") or 0) * int(s.get("branches") or 1))
    nozzles: list = []
    for t in net.get("terminals") or []:
        p = t.get("params") or {}
        if t.get("kind") == "manifold":
            spec = _spec(p)
            takeoffs = p.get("takeoffs")
            n = len(takeoffs) if takeoffs else int(p.get("count") or 0)
            span_ft = float(p.get("length_ft") or (n * float(p.get("pitch_in") or 0) / 12))
            add_pipe(spec, span_ft + 2 * OVERHANG_M / M_PER_FT)
            lines.append(line("Piping", f"fitting|end_cap|{_size_label(spec)}|{spec.label}", f"End cap {_size_label(spec)} {spec.label}", 2, "ea",
                              {"kind": "fitting", "fitting": "end_cap", "nominal_size": spec.nominal, "material": spec.material}))
            lines.append(line("Piping", f"fitting|takeoff|{_size_label(spec)}", f"Takeoff on {_size_label(spec)} manifold", n, "ea",
                              {"kind": "fitting", "fitting": "takeoff", "nominal_size": spec.nominal}))
            nozzles.append((t, n, p))
        elif t.get("kind") == "orifice":
            nozzles.append((t, int(p.get("count") or t.get("count") or 1), p))
        elif t.get("kind") == "parallel":
            for m in p.get("members") or []:
                if m.get("kind") == "orifice":
                    mp = m.get("params") or {}
                    nozzles.append((m, int(mp.get("count") or 1), mp))
    for k in pipe_order:
        spec, ft = pipe[k]
        if ft <= 0:
            continue
        qty = math.ceil(ft * (1 + waste) - 1e-9)
        lines.append(line("Piping", f"pipe|{spec.material}|{k[1]}", f"{spec.label} pipe {k[1]} ({math.ceil(qty / STICK_FT)} x 20 ft sticks, incl. {waste:.0%} waste)",
                          qty, "ft", {"kind": "pipe", "material": spec.material, "nominal_size": spec.nominal, "id_mm": spec.id_mm, "length_ft": ft}))

    # Fittings and valves
    fits = fitting_counts(segs)
    joints = 0.0
    for f in fits:
        section = "Valves" if f["key"] in VALVE_KEYS else ("Equipment" if f["key"] in EQUIPMENT_KEYS else "Piping")
        lines.append(line(section, f"fitting|{f['key']}|{f['nominal_size']}|{f['material']}", f"{f['label']} {f['nominal_size']} {f['material']}",
                          f["qty"], "ea", {"kind": "valve" if section == "Valves" else "fitting", "fitting": f["key"], "nominal_size": f["nominal_size"],
                                           "material": f["material"]}))
        joints += 2 * f["qty"]

    # Nozzles
    for t, n, p in nozzles:
        tid = t.get("id") or ""
        product = p.get("item") or p.get("nozzle")
        desc = product or ("Nozzle " + (f"{p['orifice_diameter_in']:g} in orifice" if p.get("orifice_diameter_in") else
                                        f"{p.get('rated_gpm', '?')} GPM @ {p.get('rated_head_ft', '?')} ft"))
        lines.append(line("Nozzles", f"nozzle|{product or desc}", desc, n, "ea", {"kind": "nozzle", "item": product, "terminal": tid}))

    # Equipment: pumps and what their sections chose, components on segments, the model's equipment list
    arrestors = {a.get("pump"): a for a in (model.get("surge") or {}).get("arrestors") or []}
    for pmp in net.get("pumps") or []:
        pid = pmp.get("id")
        item = pmp.get("item")
        lines.append(line("Equipment", f"pump|{item or pmp.get('generic_class') or 'generic'}", item or f"Pump ({pmp.get('generic_class') or 'generic, pick a model'})",
                          1, "ea", {"kind": "pump", "item": item, "pump": pid}))
        ctl = (pmp.get("control") or "none").lower()
        if ctl == "vfd":
            lines.append(line("Equipment", "control|vfd", "Variable-frequency drive", 1, "ea", {"kind": "vfd", "pump": pid}))
        elif ctl == "prv":
            lines.append(line("Valves", "control|prv", "Pressure-reducing valve", 1, "ea", {"kind": "prv", "pump": pid}))
        if pmp.get("bypass_in"):
            lines.append(line("Equipment", f"bypass|{float(pmp['bypass_in']):.3f}", f"Bypass orifice {float(pmp['bypass_in']):.3f} in", 1, "ea",
                              {"kind": "orifice", "diameter_in": float(pmp["bypass_in"]), "pump": pid}))
        if pid in arrestors:
            a = arrestors[pid]
            lines.append(line("Equipment", f"arrestor|{a['gas_l']:g}", f"Surge arrestor {a['gas_l']:g} L gas, {a.get('precharge_psi', 0):.0f} psi precharge",
                              1, "ea", {"kind": "arrestor", "gas_l": a["gas_l"], "pump": pid}))
    for s in segs:
        for c in s.get("components") or []:
            name = c.get("key") or c.get("type")
            if name:
                lines.append(line("Equipment", f"component|{name}", name, float(c.get("qty", c.get("count", 1)) or 1) * int(s.get("branches") or 1), "ea",
                                  {"kind": "component", "component": name, "segment": s.get("id")}))
    for e in model.get("equipment") or []:
        slot = e.get("slot") or "equipment"
        lines.append(line("Equipment", f"equipment|{slot}|{e.get('item') or ''}", e.get("description") or e.get("item") or slot.replace("_", " ").title(),
                          float(e.get("qty") or 1), e.get("uom") or "ea", {"kind": slot, "item": e.get("item")}))

    # Consumables: joints and hangers
    cuts = cut_list([s for s in segs if s.get("nominal_size") or s.get("id_in")])
    joints += 2 * sum(c["couplings"] for c in cuts) + 2 * len(cuts)
    plastic = any((pipe_spec_si(s.get("material") or "SCH40 PVC").material or "").endswith("PVC") for s in segs)
    copper = any("Copper" in (pipe_spec_si(s.get("material") or "").material or "") for s in segs)
    if joints and plastic:
        q = math.ceil(joints / float(rr["joints_per_quart"]))
        lines.append(line("Consumables", "consumable|pvc_primer", "PVC primer (quart)", q, "qt", {"kind": "consumable"}))
        lines.append(line("Consumables", "consumable|pvc_cement", "PVC cement (quart)", q, "qt", {"kind": "consumable"}))
    if joints and copper:
        lines.append(line("Consumables", "consumable|solder", "Lead-free solder and flux (lb)", math.ceil(joints / float(rr["joints_per_lb_solder"])), "lb",
                          {"kind": "consumable"}))
    for s in segs:
        if s.get("exposed") and (s.get("nominal_size") or s.get("id_in")):
            h = pipe_hangers(s, spacing_ft=s.get("hanger_spacing_ft"))
            if h.value:
                lines.append(line("Consumables", f"hanger|{_size_label(_spec(s))}", f"Pipe hanger {_size_label(_spec(s))} (to {h.value['max_load_lb']:.0f} lb)",
                                  h.value["hangers"], "ea", {"kind": "hanger", "nominal_size": _spec(s).nominal}))
                checks.extend(h.value["checks"])
    labor, lchecks = labor_rows(model, rules)
    checks.extend(lchecks)
    merged = merge_sections(lines, labor)
    return CalcResult(
        calc="bom_rows",
        value={"lines": merged, "cut_list": cuts, "fittings": fits, "checks": checks},
        unit="",
        inputs={"waste": make_input(waste * 100, "%", "standard", "DRC bomRows"), "stick": make_input(STICK_FT, "ft", "standard")},
        formula="pipe = sum(length x branches) x (1 + waste), in 20 ft sticks ; fittings grouped by type, size, material",
        steps=[f"{len(merged)} lines: " + ", ".join(f"{sum(1 for ln in merged if ln['section'] == s)} {s}" for s in dict.fromkeys(ln["section"] for ln in merged))],
        citations=[CIT_BOM, CIT_RULES],
    )
