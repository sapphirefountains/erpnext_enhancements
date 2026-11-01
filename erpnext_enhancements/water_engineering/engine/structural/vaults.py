"""Vaults, lids, gratings and trench drains.

A vault's walls and slab are a shell (``geometry.basin_shell`` with
``kind: "vault"``): the takeoff already checks them under backfill and
groundwater and checks the empty vault for flotation. This module adds what
covers it:

* a cast concrete lid as a one-way slab spanning the vault's short way,
  simply supported on the walls, under its load class: pedestrian 100 psf,
  plaza 250 psf (ASCE 7 Table 4.3-1), or an H-20 truck wheel (16 kip, 30%
  impact, spread over AASHTO's distribution width E = 4 + 0.06·S ft);
  minimum thickness L/20 (ACI 318 Table 7.3.1.1) and bars chosen from #4 and
  #5 at 18 to 6 in;
* steel bar grating (19-W-4: bearing bars at 1-3/16 in) under the same
  classes, at NAAMM's 18 ksi bending stress, ≤ 1/4 in deflection under the
  pedestrian load and ≤ L/360 under the wheel;
* trench drains: the EN 1433 load class (A15 pedestrian, C250 plaza with
  service vehicles, D400 where a truck crosses), grate count, and the ADA
  limit on grate openings.

DOC-0119 says nothing about lids, gratings or drains.
"""

from __future__ import annotations

import math

from ..data.rebar import bar as rebar
from ..envelope import CalcResult, make_input
from .defaults import DISCLAIMER, merged_rules

CIT_LIVE = "ASCE 7-16 Table 4.3-1 (pedestrian 100 psf; sidewalks and driveways subject to trucking 250 psf)"
CIT_H20 = ("AASHTO Standard Specifications §3.7 (H-20 rear wheel 16 kip), §3.8.2 (impact 50/(S + 125) ≤ 0.30) "
           "and §3.24.3.2 (distribution width E = 4 + 0.06S ≤ 7 ft)")
CIT_ACI_SLAB = "ACI 318-19 Table 7.3.1.1 (one-way slab minimum h = L/20 simply supported) and §7.6.1.1 (As ≥ 0.0018bh)"
CIT_ACI_FLEX = "ACI 318-19 §22.2 (φMn = 0.9·As·fy·(d − a/2)); load factors 1.2D + 1.6L"
CIT_NAAMM = "NAAMM MBG 531 / 532 (bar grating: 18 ksi bending; 1/4 in deflection under 100 psf pedestrian)"
CIT_EN1433 = "EN 1433 load classes A15, B125, C250, D400 (trench drain channels and grates)"
CIT_ADA = "2010 ADA Standards §302.3 (openings in walking surfaces pass no more than a 1/2 in sphere)"

LOAD_CLASSES = {"pedestrian": 100.0, "plaza": 250.0, "vehicle": None}
# bearing bar (depth, thickness) in inches, lightest first
GRATING_BARS = [(0.75, 0.125), (1.0, 0.125), (1.0, 0.1875), (1.25, 0.1875), (1.5, 0.1875), (1.75, 0.1875),
                (2.0, 0.1875), (2.25, 0.1875), (2.5, 0.1875), (2.0, 0.25), (2.5, 0.25), (3.0, 0.25),
                (3.5, 0.25), (4.0, 0.25)]
GRATING_PITCH_IN = 1.1875
WHEEL_LB, TIRE_WIDTH_IN = 16000.0, 20.0


def _load_class(load_class: str) -> str:
    key = str(load_class or "pedestrian").lower()
    return "vehicle" if key in ("vehicle", "h20", "h-20", "hs20", "truck", "drive") else (
        key if key in LOAD_CLASSES else "pedestrian")


def lid_slab(span_ft: float, thickness_in: float | None = None, load_class: str = "pedestrian",
             rules: dict | None = None) -> CalcResult:
    """A cast-in-place vault lid spanning ``span_ft`` one way: the thinnest
    whole-inch slab ≥ L/20 and ≥ the given thickness, with the lightest #4/#5
    bottom bars that carry the factored moment."""
    rules = merged_rules(rules)
    cls = _load_class(load_class)
    s = float(span_ft)
    fc, fy = float(rules["fc_psi"]), float(rules["fy_psi"])
    cover = float(rules["cover_in"])
    h_min = math.ceil(s * 12.0 / 20.0 - 1e-9)
    h = max(float(thickness_in or 0), float(h_min), 6.0)
    if cls == "vehicle":
        impact = min(50.0 / (s + 125.0), 0.30)
        e_width = min(4.0 + 0.06 * s, 7.0)
        m_live = WHEEL_LB * (1 + impact) * s / 4.0 / e_width
        live_note = f"wheel 16,000 lb × {1 + impact:.3f} impact × {s:g}/4 / E {e_width:.2f} ft"
    else:
        q = LOAD_CLASSES[cls]
        m_live = q * s * s / 8.0
        live_note = f"{q:g} psf × {s:g}² / 8"
    chosen = None
    tried = []
    while chosen is None and h <= 24:
        m_dead = float(rules["concrete_pcf"]) * h / 12.0 * s * s / 8.0
        mu = 1.2 * m_dead + 1.6 * m_live
        for size in (4, 5):
            b = rebar(size)
            d = h - cover - b["diameter_in"] / 2.0
            for spacing in (18.0, 15.0, 12.0, 9.0, 6.0):
                as_ = b["area_in2"] * 12.0 / spacing
                a = as_ * fy / (0.85 * fc * 12.0)
                phi_mn = 0.9 * as_ * fy * (d - a / 2.0) / 12.0
                as_min = 0.0018 * 12.0 * h
                if phi_mn >= mu and as_ >= as_min:
                    chosen = {"thickness_in": h, "bar": size, "spacing_in": spacing, "d_in": d, "mu_lb_ft": mu,
                              "phi_mn_lb_ft": phi_mn, "m_dead_lb_ft": m_dead, "m_live_lb_ft": m_live}
                    break
            if chosen:
                break
        tried.append(f"h = {h:g} in: Mu = {mu:.0f} lb·ft/ft " + ("passes" if chosen else "no #4/#5 spacing works"))
        if chosen is None:
            h += 1.0
    ok = chosen is not None
    value = chosen or {"thickness_in": h, "mu_lb_ft": None}
    value.update({"span_ft": s, "load_class": cls, "min_thickness_in": float(h_min),
                  "concrete_cuft_per_ft_width": value["thickness_in"] / 12.0 * s, "ok": ok})
    return CalcResult(
        calc="structural_lid_slab",
        value=value,
        unit="in",
        inputs={"span": make_input(s, "ft", "prior_calc"), "load_class": make_input(cls, "", "user")},
        formula="h ≥ L/20; Mu = 1.2·wD·L²/8 + 1.6·ML ≤ φMn",
        steps=[f"live moment: {live_note} = {m_live:.0f} lb·ft/ft"] + tried
        + ([f"#{chosen['bar']} at {chosen['spacing_in']:g} in, d = {chosen['d_in']:.2f} in: φMn "
            f"{chosen['phi_mn_lb_ft']:.0f} ≥ Mu {chosen['mu_lb_ft']:.0f}"] if ok else []),
        citations=[CIT_ACI_SLAB, CIT_ACI_FLEX] + ([CIT_H20] if cls == "vehicle" else [CIT_LIVE]),
        warnings=[DISCLAIMER, "Lid edges bear on the vault walls; detail the hatch opening with trim bars."]
        + ([] if ok else ["No slab up to 24 in works with #4/#5 bars: engineer the lid."]),
        status="ok" if ok else "bad",
    )


def grating(span_in: float, load_class: str = "pedestrian", area_sqft: float = 0.0,
            rules: dict | None = None) -> CalcResult:
    """The lightest welded steel bar grating (bearing bars at 1-3/16 in)
    spanning ``span_in`` for the load class, its weight and the area to buy."""
    cls = _load_class(load_class)
    span = float(span_in)
    e, fb = 29.0e6, 18000.0
    per_ft = 12.0 / GRATING_PITCH_IN
    chosen = None
    for depth, t in GRATING_BARS:
        s_bar, i_bar = t * depth ** 2 / 6.0, t * depth ** 3 / 12.0
        if cls == "vehicle":
            p = WHEEL_LB * 1.3
            n = math.floor(TIRE_WIDTH_IN / GRATING_PITCH_IN) + 1
            stress = p * span / 4.0 / (n * s_bar)
            defl = p * span ** 3 / (48 * e * n * i_bar)
            limit = span / 360.0
        else:
            q = LOAD_CLASSES[cls]
            w = q / 144.0 * GRATING_PITCH_IN  # lb/in on one bar
            stress = w * span ** 2 / 8.0 / s_bar
            defl = 5 * w * span ** 4 / (384 * e * i_bar)
            limit = min(0.25, span / 360.0) if cls == "pedestrian" else span / 360.0
        if stress <= fb and defl <= limit:
            psf = per_ft * depth * t / 144.0 * 490.0 * 1.10  # + 10% for cross bars and banding
            chosen = {"bearing_bar": f"{depth:g} × {t:g} in", "depth_in": depth, "thickness_in": t,
                      "stress_psi": stress, "deflection_in": defl, "deflection_limit_in": limit, "weight_psf": psf}
            break
    ok = chosen is not None
    value = {**(chosen or {}), "span_in": span, "load_class": cls, "area_sqft": float(area_sqft),
             "weight_lb": (chosen["weight_psf"] * float(area_sqft)) if chosen else None, "ok": ok}
    return CalcResult(
        calc="structural_grating",
        value=value,
        unit="psf",
        inputs={"span": make_input(span, "in", "user"), "load_class": make_input(cls, "", "user")},
        formula="f = M/S ≤ 18 ksi; Δ ≤ 1/4 in (pedestrian) or L/360; wheel over the bars under a 20 in tire",
        steps=[f"{cls}: {chosen['bearing_bar']} bars, f = {chosen['stress_psi']:.0f} psi, Δ = "
               f"{chosen['deflection_in']:.4f} in, {chosen['weight_psf']:.1f} psf"] if ok else
        [f"{cls}: no bar in the table spans {span:g} in"],
        citations=[CIT_NAAMM] + ([CIT_H20] if cls == "vehicle" else [CIT_LIVE]),
        warnings=[DISCLAIMER, "Confirm the grating against the manufacturer's load table (heavy-duty for trucks)."]
        + ([] if ok else ["Shorten the span with a support beam."]),
        status="ok" if ok else "bad",
    )


def trench_drain(length_ft: float, load_class: str = "pedestrian", grate_length_in: float | None = None,
                 rules: dict | None = None) -> CalcResult:
    """The EN 1433 class for a trench drain's channel and grate, and how
    many channel and grate pieces a run takes."""
    rules = merged_rules(rules)
    cls = _load_class(load_class)
    en = {"pedestrian": "A15 (B125 if cars can reach it)", "plaza": "C250", "vehicle": "D400"}[cls]
    piece = float(grate_length_in or rules["trench_grate_length_in"])
    count = math.ceil(float(length_ft) * 12.0 / piece - 1e-9) if length_ft else 0
    return CalcResult(
        calc="structural_trench_drain",
        value={"load_class": cls, "en1433_class": en, "length_ft": float(length_ft), "pieces": count,
               "piece_length_in": piece, "heel_proof": cls != "vehicle"},
        unit="each",
        inputs={"length": make_input(length_ft, "ft", "user"), "load_class": make_input(cls, "", "user")},
        formula="pieces = ceil(length / piece length)",
        steps=[f"{cls} → EN 1433 {en}; {count} pieces of {piece:g} in"],
        citations=[CIT_EN1433, CIT_ADA],
        warnings=[DISCLAIMER, "Walkable grates: heel-proof, openings that pass no more than a 1/2 in sphere."],
    )
