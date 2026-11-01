"""The rebar schedule: bar marks per element, laps, hooks, stock sticks,
weight, chairs and tie wire.

Detailing follows DOC-0119's Big & Beefy details:

* slab: a mat each way at the slab spacing (#4 at 18 in), bars running the
  footprint less cover at each end;
* walls: vertical L-bars (DOC-0119's "U-shaped rebar that ties the wall into
  the floor", legs tied into the floor mat 24 in) and horizontal rows at the
  wall spacing from the DOC-0119 table; straight horizontals per side on a
  rectangle with an L corner bar per row at each corner (legs one lap each),
  closed hoops on a circle or freeform;
* laps: ACI 318 Class B (1.3 × ld, ld by the simplified §25.4.2.3) rounded
  up to the inch, or 40 bar diameters when ``rules["lap_rule"] == "40db"``;
* bars longer than the stock length are split with laps; every cut piece is
  then packed into stock sticks first-fit-decreasing, which is the count to
  buy (20 ft by default, 40 ft shown alongside).

Counts are ``ceil(run / spacing) + 1`` on an open run (both ends get a bar)
and ``ceil(loop / spacing)`` around a closed loop.
"""

from __future__ import annotations

import math

from ..data.rebar import CIT_ASTM_A615, STOCK_LENGTHS_FT
from ..data.rebar import bar as rebar
from ..envelope import CalcResult, make_input
from .defaults import (
    CIT_ACI318_DEV,
    CIT_ACI318_HOOK,
    CIT_ACI318_LAP,
    CIT_ACI332_HORIZ,
    CIT_CRSI,
    CIT_DOC0119_REBAR,
    DISCLAIMER,
    merged_rules,
)

EPS = 1e-9


def _ceil(x: float) -> int:
    return int(math.ceil(x - EPS))


def development_length(bar_size, rules: dict | None = None) -> CalcResult:
    """Straight-bar tension development length by ACI 318-19 §25.4.2.3,
    the case with clear spacing and cover of at least one bar diameter
    (true for DOC-0119's 2 in cover and 12–18 in spacing): ld = fy·ψt·ψe·ψg /
    (25 λ √f'c) · db for #6 and smaller, 20 for #7 and larger; ≥ 12 in.
    Uncoated bars (ψe = 1), not top-cast (ψt = 1), Grade 60 (ψg = 1)."""
    rules = merged_rules(rules)
    b = rebar(bar_size)
    fc = min(float(rules["fc_psi"]), 10000.0)  # √f'c ≤ 100 psi
    fy = float(rules["fy_psi"])
    divisor = 25.0 if b["size"] <= 6 else 20.0
    ld_calc = fy / (divisor * math.sqrt(fc)) * b["diameter_in"]
    ld = max(ld_calc, 12.0)
    return CalcResult(
        calc="structural_development_length",
        value=ld,
        unit="in",
        inputs={"bar": make_input(b["label"], "", "standard"), "fc": make_input(fc, "psi", "default"),
                "fy": make_input(fy, "psi", "default")},
        formula=f"ld = fy / ({divisor:g} √f'c) × db ≥ 12 in",
        steps=[f"ld = {fy:g} / ({divisor:g} × √{fc:g}) × {b['diameter_in']:g} = {ld_calc:.3f} in → {ld:.3f} in"],
        citations=[CIT_ACI318_DEV, CIT_ASTM_A615],
        warnings=[DISCLAIMER],
    )


def lap_length(bar_size, rules: dict | None = None) -> CalcResult:
    """Tension lap splice: ACI 318 Class B (1.3 ld, ≥ 12 in) rounded up to
    the whole inch, or the 40 × db rule of thumb when ``lap_rule`` says so.
    Both figures are in ``steps`` whichever governs."""
    rules = merged_rules(rules)
    b = rebar(bar_size)
    ld = development_length(bar_size, rules).value
    class_b = max(1.3 * ld, 12.0)
    thumb = float(rules["lap_db_fallback"]) * b["diameter_in"]
    use_thumb = str(rules["lap_rule"]).lower() in ("40db", "rule_of_thumb", "db")
    lap = math.ceil((thumb if use_thumb else class_b) - EPS)
    return CalcResult(
        calc="structural_lap_length",
        value=float(lap),
        unit="in",
        inputs={"bar": make_input(b["label"], "", "standard"), "rule": make_input(rules["lap_rule"], "", "default")},
        formula="Class B lap = 1.3 ld ≥ 12 in (or 40 db), rounded up to the inch",
        steps=[f"Class B = 1.3 × {ld:.3f} = {class_b:.3f} in", f"40 db = {thumb:.3f} in",
               f"lap = {lap} in ({'40 db rule of thumb' if use_thumb else 'ACI Class B'})"],
        citations=[CIT_ACI318_LAP, CIT_ACI318_DEV] + (["Rule of thumb 40 bar diameters (CRSI field practice)"]
                                                      if use_thumb else []),
        warnings=[DISCLAIMER],
    )


def hook_geometry(bar_size) -> CalcResult:
    """ACI 318-19 Table 25.3.1 standard hooks for #3–#8: inside bend diameter
    6 db; a 90° hook extends 12 db past the bend, a 180° hook 4 db (≥ 2.5 in)."""
    b = rebar(bar_size)
    db = b["diameter_in"]
    value = {"bend_diameter_in": 6 * db, "ext_90_in": 12 * db, "ext_180_in": max(4 * db, 2.5),
             "bar": b["label"]}
    return CalcResult(
        calc="structural_hook_geometry",
        value=value,
        unit="in",
        inputs={"bar": make_input(b["label"], "", "standard")},
        formula="bend Ø = 6 db; 90° ext = 12 db; 180° ext = max(4 db, 2.5 in)",
        steps=[f"{k} = {v:g}" for k, v in value.items() if k != "bar"],
        citations=[CIT_ACI318_HOOK],
        warnings=[DISCLAIMER],
    )


def _split(length_ft: float, stock_ft: float, lap_ft: float) -> list[float]:
    """Cut pieces for a straight run longer than a stock bar, lapped."""
    if length_ft <= stock_ft + EPS:
        return [length_ft]
    n = _ceil((length_ft - lap_ft) / (stock_ft - lap_ft))
    total = length_ft + (n - 1) * lap_ft
    return [stock_ft] * (n - 1) + [total - (n - 1) * stock_ft]


def _hoop(circ_ft: float, stock_ft: float, lap_ft: float) -> list[float]:
    """Cut pieces for a closed hoop: n equal pieces, n laps."""
    n = max(_ceil(circ_ft / (stock_ft - lap_ft)), 1)
    return [(circ_ft + n * lap_ft) / n] * n


def stock_sticks(cuts_ft: list[float], stock_length_ft: float = 20.0) -> CalcResult:
    """Stock bars to buy for a list of cut lengths of one bar size, packed
    first-fit-decreasing (no saw kerf: rebar is sheared)."""
    stock = float(stock_length_ft)
    bins: list[float] = []
    too_long = 0
    for cut in sorted((float(c) for c in cuts_ft), reverse=True):
        if cut > stock + EPS:
            too_long += 1
            continue
        for i, free in enumerate(bins):
            if cut <= free + EPS:
                bins[i] = free - cut
                break
        else:
            bins.append(stock - cut)
    used = sum(cuts_ft)
    return CalcResult(
        calc="structural_stock_sticks",
        value=len(bins),
        unit="bars",
        inputs={"stock_length": make_input(stock, "ft", "default"), "cuts": make_input(len(cuts_ft), "pieces")},
        formula="first-fit-decreasing packing of cut pieces into stock bars",
        steps=[f"{len(cuts_ft)} pieces, {used:.2f} ft → {len(bins)} × {stock:g} ft bars "
               f"(offcut {len(bins) * stock - used:.2f} ft)"],
        citations=[CIT_CRSI],
        warnings=[DISCLAIMER] + ([f"{too_long} piece(s) longer than {stock:g} ft were not packed."] if too_long else []),
    )


def _mark(mark, element, desc, bar_size, shape, spacing_in, qty, each_ft, pieces_per_bar, cuts=None):
    """One bar mark. ``pieces_per_bar`` is the cut list of one bar; bars of
    varying length (chords) pass every cut of the mark as ``cuts``."""
    b = rebar(bar_size)
    cuts = list(cuts) if cuts is not None else list(pieces_per_bar) * int(qty)
    total = sum(cuts)
    return {
        "mark": mark, "element": element, "description": desc, "bar": b["label"], "size": b["size"],
        "shape": shape, "spacing_in": spacing_in, "qty": int(qty), "length_ft": each_ft,
        "pieces": list(pieces_per_bar), "cuts": cuts, "total_ft": total, "weight_lb": total * b["lb_per_ft"],
    }


def _chord_lengths(radius_ft: float, spacing_ft: float) -> list[float]:
    """Bar lengths across a circle of ``radius_ft`` at ``spacing_ft``, bars
    centered in their strip."""
    if radius_ft <= 0:
        return []
    n = max(_ceil(2 * radius_ft / spacing_ft), 1)
    pitch = 2 * radius_ft / n
    out = []
    for k in range(n):
        y = -radius_ft + pitch * (k + 0.5)
        out.append(2 * math.sqrt(max(radius_ft * radius_ft - y * y, 0.0)))
    return out


def _grid(element, prefix, s, bar_size, spacing_in, mats, cover_ft, stock, lap_ft, warnings):
    """Two-way mat(s) over the footprint of shell ``s``."""
    sp = spacing_in / 12.0
    marks, ties = [], 0
    if s["shape"] == "rectangular":
        lx = s["outside_length_ft"] - 2 * cover_ft
        ly = s["outside_width_ft"] - 2 * cover_ft
        nx = _ceil(ly / sp) + 1  # bars running along x, spaced across y
        ny = _ceil(lx / sp) + 1
        marks.append(_mark(f"{prefix}1", element, f"{element} bars, long way", bar_size, "straight", spacing_in,
                           nx * mats, lx, _split(lx, stock, lap_ft)))
        marks.append(_mark(f"{prefix}2", element, f"{element} bars, short way", bar_size, "straight", spacing_in,
                           ny * mats, ly, _split(ly, stock, lap_ft)))
        ties = nx * ny * mats
    elif s["shape"] == "cylindrical":
        r = s["outside_diameter_ft"] / 2 - cover_ft
        chords = _chord_lengths(r, sp)
        cuts = [p for lg in chords for p in _split(lg, stock, lap_ft)] * mats
        for idx, way in enumerate(("one way", "other way"), start=1):
            marks.append(_mark(f"{prefix}{idx}", element, f"{element} bars, {way} (chords, varying length)",
                               bar_size, "straight", spacing_in, len(chords) * mats,
                               sum(chords) / len(chords) if chords else 0.0, [], cuts=cuts))
        n = len(chords)
        ties = int(round(math.pi * n * n / 4)) * mats
    else:
        area = max(s["footprint_sqft"] - s["outside_perimeter_ft"] * cover_ft, 0.0)
        side = math.sqrt(area)
        n = _ceil(side / sp) + 1
        each = area / sp / n if n else 0.0
        for idx, way in enumerate(("one way", "other way"), start=1):
            marks.append(_mark(f"{prefix}{idx}", element, f"{element} bars, {way} (freeform, averaged)", bar_size,
                               "straight", spacing_in, n * mats, each, _split(each, stock, lap_ft)))
        ties = n * n * mats
        warnings.append(f"Freeform {element}: bar count from area ÷ spacing (approximate).")
    return marks, ties


def rebar_schedule(shell, detail, rules: dict | None = None) -> CalcResult:
    """Bar marks, lengths, laps and weights for one basin shell.

    ``shell`` is a ``basin_shell`` result (or its value); ``detail`` a
    ``select_thickness`` result (or its value)."""
    rules = merged_rules(rules)
    s = shell.value if isinstance(shell, CalcResult) else shell
    d = detail.value if isinstance(detail, CalcResult) else detail
    stock = float(rules["stock_length_ft"])
    cover_ft = float(rules["cover_in"]) / 12.0
    warnings = [DISCLAIMER]
    steps = []
    marks: list[dict] = []
    ties = 0

    slab_lap = lap_length(d["slab_bar"], rules).value / 12.0
    wall_lap = lap_length(d["wall_bar"], rules).value / 12.0
    steps.append(f"laps: slab #{d['slab_bar']} {slab_lap * 12:g} in, wall #{d['wall_bar']} {wall_lap * 12:g} in")

    m, t = _grid("slab", "S", s, d["slab_bar"], d["slab_spacing_in"], d["slab_mats"], cover_ft, stock, slab_lap,
                 warnings)
    marks += m
    ties += t

    # walls
    h = s["wall_height_ft"]
    wsp = d["wall_spacing_in"] / 12.0
    mats = int(d["wall_mats"])
    vert = s["slab_in"] / 12.0 + h - 2 * cover_ft
    leg = float(rules["dowel_leg_in"]) / 12.0
    n_vert = _ceil(s["wall_centerline_ft"] / wsp)
    marks.append(_mark("W1", "walls", "wall verticals, L-bar tied into the floor mat", d["wall_bar"], "L",
                       d["wall_spacing_in"], n_vert * mats, vert + leg, _split(vert + leg, stock, wall_lap)))
    steps.append(f"verticals: ceil({s['wall_centerline_ft']:.4f} / {wsp:.4f}) = {n_vert} per mat, "
                 f"{vert:.4f} + {leg:.4f} leg = {vert + leg:.4f} ft each")
    rows = max(_ceil((h - cover_ft) / wsp) + 1, 1)  # ≥ 2 whenever h > cover: ACI 332's top and bottom bar
    steps.append(f"horizontal rows: ceil(({h:.4f} − {cover_ft:.4f}) / {wsp:.4f}) + 1 = {rows} per mat")
    if s["shape"] == "rectangular":
        lx = s["outside_length_ft"] - 2 * cover_ft
        ly = s["outside_width_ft"] - 2 * cover_ft
        marks.append(_mark("W2", "walls", "wall horizontals, long sides", d["wall_bar"], "straight",
                           d["wall_spacing_in"], 2 * rows * mats, lx, _split(lx, stock, wall_lap)))
        marks.append(_mark("W3", "walls", "wall horizontals, short sides", d["wall_bar"], "straight",
                           d["wall_spacing_in"], 2 * rows * mats, ly, _split(ly, stock, wall_lap)))
    else:
        ring = s["wall_centerline_ft"]
        marks.append(_mark("W2", "walls", "wall horizontal hoops, lapped closed", d["wall_bar"], "hoop",
                           d["wall_spacing_in"], rows * mats, ring, _hoop(ring, stock, wall_lap)))
    corners = int(s.get("corner_count") or 0)
    if corners:
        marks.append(_mark("W4", "walls", "corner bars, one lap each leg", d["wall_bar"], "L", d["wall_spacing_in"],
                           corners * rows * mats, 2 * wall_lap, [2 * wall_lap]))
    ties += n_vert * rows * mats + corners * rows * mats * 2

    # stepped-floor risers: hairpins over the riser plus two running bars
    for i, r in enumerate(s.get("risers") or [], start=1):
        n_pin = _ceil(r["length_ft"] / (d["slab_spacing_in"] / 12.0)) + 1
        pin = 2 * (r["height_ft"] + s["slab_in"] / 12.0 - 2 * cover_ft)
        marks.append(_mark(f"R{i}", "risers", "riser hairpins", d["slab_bar"], "U", d["slab_spacing_in"], n_pin,
                           pin, _split(pin, stock, slab_lap)))
        marks.append(_mark(f"R{i}L", "risers", "riser running bars", d["slab_bar"], "straight", None, 2,
                           r["length_ft"], _split(r["length_ft"], stock, slab_lap)))
        ties += 2 * n_pin

    if s.get("kind") == "vault" and s.get("lid_in"):
        m, t = _grid("lid", "L", s, d["slab_bar"], d["slab_spacing_in"], 1, cover_ft, stock, slab_lap, warnings)
        marks += m
        ties += t
        warnings.append("Vault lid bars are a quantity allowance only; size the lid with an engineer.")

    # totals per size and per element
    by_size: dict[str, dict] = {}
    elements: dict[str, dict] = {}
    for mk in marks:
        size = by_size.setdefault(mk["bar"], {"bar": mk["bar"], "size": mk["size"], "total_ft": 0.0,
                                              "weight_lb": 0.0, "cuts": []})
        size["total_ft"] += mk["total_ft"]
        size["weight_lb"] += mk["weight_lb"]
        size["cuts"] += mk["cuts"]
        el = elements.setdefault(mk["element"], {"element": mk["element"], "total_ft": 0.0, "weight_lb": 0.0,
                                                 "marks": []})
        el["total_ft"] += mk["total_ft"]
        el["weight_lb"] += mk["weight_lb"]
        el["marks"].append(mk["mark"])
        del mk["cuts"]
    for size in by_size.values():
        lb_ft = rebar(size["size"])["lb_per_ft"]
        size["sticks"] = {}
        for length in sorted(set(STOCK_LENGTHS_FT) | {stock}):
            n = stock_sticks(size["cuts"], length).value
            size["sticks"][f"{length:g}"] = {"count": n, "purchase_lb": n * length * lb_ft}
        size["pieces"] = len(size["cuts"])
        del size["cuts"]

    chair_sp = float(rules["chair_spacing_ft"])
    chairs = _ceil(s["footprint_sqft"] / (chair_sp * chair_sp)) * int(d["slab_mats"])
    if s.get("kind") == "vault" and s.get("lid_in"):
        chairs += _ceil(s["footprint_sqft"] / (chair_sp * chair_sp))
    spacer_sp = float(rules["spacer_spacing_ft"])
    spacers = _ceil((s["wall_area_inside_sqft"] + s["wall_area_outside_sqft"]) / (spacer_sp * spacer_sp))
    ties = _ceil(ties / max(int(rules["tie_every"]), 1))
    wire_lb = ties * float(rules["tie_wire_in_per_tie"]) / 12.0 * float(rules["tie_wire_lb_per_ft"])
    total_ft = sum(v["total_ft"] for v in by_size.values())
    total_lb = sum(v["weight_lb"] for v in by_size.values())
    steps.append(f"total {total_ft:.3f} ft, {total_lb:.2f} lb; {ties} ties ≈ {wire_lb:.2f} lb wire; "
                 f"{chairs} chairs, {spacers} wall spacers")
    return CalcResult(
        calc="structural_rebar_schedule",
        value={"marks": marks, "by_size": by_size, "elements": elements, "total_ft": total_ft,
               "weight_lb": total_lb, "ties": ties, "tie_wire_lb": wire_lb, "chairs": chairs,
               "wall_spacers": spacers, "lap_in": {"slab": slab_lap * 12, "walls": wall_lap * 12},
               "hooks": hook_geometry(d["wall_bar"]).value, "stock_length_ft": stock},
        unit="lb",
        inputs={"stock_length": make_input(stock, "ft", "default"),
                "cover": make_input(rules["cover_in"], "in", "standard", CIT_DOC0119_REBAR),
                "dowel_leg": make_input(rules["dowel_leg_in"], "in", "standard", CIT_DOC0119_REBAR)},
        formula="count = ceil(run/s)+1 (open) or ceil(loop/s) (closed); length × count + laps; lb = ft × lb/ft",
        steps=steps,
        citations=[CIT_DOC0119_REBAR, CIT_ACI318_LAP, CIT_ACI318_HOOK, CIT_ACI332_HORIZ, CIT_ASTM_A615, CIT_CRSI],
        warnings=warnings,
    )
