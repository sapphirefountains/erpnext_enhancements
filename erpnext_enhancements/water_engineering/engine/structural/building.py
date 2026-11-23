"""Beams and decks over collector tanks, and what a basin asks of the
building it stands in or on.

* ``deck_beam``: a simply supported beam carrying a strip of deck over a
  collector tank or a trench, in steel (the lightest W shape or tube from
  ``steel.py``) or reinforced concrete (depth L/16, bars from the factored
  moment), deflection L/360.
* ``building_floor_load``: a rooftop, podium or lobby basin's uniform load
  against the floor's stated capacity, or, failing that, the ASCE 7 live
  load for the floor's use. A design live load is not spare capacity, so any
  basin on a building is flagged to the structural engineer of record.

DOC-0119 is silent on both; its Pedestal-Elevated Floor section covers
paver-on-pedestal decks inside a basin, which stand on the basin slab.
"""

from __future__ import annotations

import math

from ..data.rebar import bar as rebar
from ..envelope import CalcResult, make_input
from .defaults import DISCLAIMER, merged_rules
from .steel import select_member

CIT_ASCE7_USE = "ASCE 7-16 Table 4.3-1 (minimum uniform live loads by occupancy or use)"
CIT_ACI_BEAM = "ACI 318-19 Table 9.3.1.1 (beam minimum depth L/16 simply supported) and §22.2 (flexure)"
CIT_EOR = "Building's structural engineer of record (IBC §1603: the design loads are theirs to confirm)"

FLOOR_USE_PSF = {
    "roof": 20.0, "roof_garden": 100.0, "office": 50.0, "residential": 40.0, "corridor_upper": 80.0,
    "lobby": 100.0, "assembly": 100.0, "retail_first_floor": 100.0, "garage": 40.0, "plaza_trucking": 250.0,
}


def deck_beam(span_ft: float, tributary_ft: float, dead_psf: float, live_psf: float, material: str = "steel",
              shape: str = "W", rules: dict | None = None) -> CalcResult:
    """A beam spanning ``span_ft`` and carrying ``tributary_ft`` of deck."""
    rules = merged_rules(rules)
    w = (float(dead_psf) + float(live_psf)) * float(tributary_ft)
    if str(material).lower() == "steel":
        res = select_member(span_ft, w, 0.0, "simple", "A992" if shape == "W" else "A500C",
                            kind="W" if shape == "W" else "HSS", rules=rules)
        res.steps.insert(0, f"w = ({dead_psf:g} + {live_psf:g}) psf × {tributary_ft:g} ft = {w:.1f} plf")
        res.value["w_plf"] = w
        res.value["material"] = "steel"
        res.value["steel_lb"] = res.value["weight_plf"] * float(span_ft)
        return res
    # reinforced concrete, 12 in wide, depth ≥ L/16 in 2 in steps
    span = float(span_ft)
    fc, fy, cover = float(rules["fc_psi"]), float(rules["fy_psi"]), 1.5
    b = 12.0
    h = max(12.0, 2 * math.ceil(span * 12.0 / 16.0 / 2 - 1e-9))
    wd = float(dead_psf) * float(tributary_ft) + float(rules["concrete_pcf"]) * b * h / 144.0
    mu = (1.2 * wd + 1.6 * float(live_psf) * float(tributary_ft)) * span ** 2 / 8.0 * 12.0  # lb·in
    bar5 = rebar(5)
    d = h - cover - 0.375 - bar5["diameter_in"] / 2.0
    rn = mu / (0.9 * b * d * d)
    inner = 1 - 2 * rn / (0.85 * fc)
    ok = inner >= 0
    as_req = 0.85 * fc / fy * (1 - math.sqrt(inner)) * b * d if ok else None
    as_min = max(3 * math.sqrt(fc) / fy, 200 / fy) * b * d
    n = max(2, math.ceil(max(as_req, as_min) / bar5["area_in2"] - 1e-9)) if ok else None
    return CalcResult(
        calc="structural_deck_beam",
        value={"material": "concrete", "b_in": b, "h_in": h, "w_plf": w, "mu_lb_in": mu, "as_required_in2": as_req,
               "bars": f"{n} #5" if n else None, "concrete_cuft": b * h / 144.0 * span, "ok": ok},
        unit="in",
        inputs={"span": make_input(span, "ft", "user"), "tributary": make_input(tributary_ft, "ft", "user")},
        formula="h ≥ L/16; Mu = (1.2D + 1.6L)·L²/8; As from the stress block",
        steps=[f"{b:g} × {h:g} in beam, d = {d:.2f} in; Mu = {mu:.0f} lb·in; "
               + (f"As = {as_req:.3f} in² → {n} #5" if ok else "too shallow for the moment")],
        citations=[CIT_ACI_BEAM],
        warnings=[DISCLAIMER] + ([] if ok else ["Section too small for the moment: deepen the beam."]),
        status="ok" if ok else "bad",
    )


def building_floor_load(pressure_psf: float, capacity_psf: float | None = None, use: str | None = None,
                        total_lb: float | None = None) -> CalcResult:
    """A basin's uniform floor load against the floor's stated capacity, or
    the ASCE 7 live load for its use when no capacity is stated."""
    q = float(pressure_psf)
    source = "stated"
    cap = capacity_psf
    if cap in (None, "") and use:
        cap = FLOOR_USE_PSF.get(str(use).lower())
        source = f"ASCE 7 live load for {use}"
    if cap in (None, ""):
        status, verdict = "warn", "no floor capacity stated"
    else:
        cap = float(cap)
        status = "ok" if q <= cap else "bad"
        verdict = f"{q:.0f} psf vs {cap:.0f} psf ({source}): " + ("within" if status == "ok" else "over")
    return CalcResult(
        calc="structural_building_floor_load",
        value={"pressure_psf": q, "capacity_psf": cap, "capacity_source": source if cap not in (None, "") else None,
               "use": use, "total_lb": total_lb, "utilization": q / cap if cap else None},
        unit="psf",
        inputs={"capacity": make_input(capacity_psf, "psf", "user"), "use": make_input(use, "", "user")},
        formula="q ≤ stated capacity (or the ASCE 7 live load for the floor's use)",
        steps=[verdict],
        citations=[CIT_ASCE7_USE, CIT_EOR],
        warnings=[DISCLAIMER, "A basin on a building goes to the structural engineer of record: a design live "
                  "load is not spare capacity, and a full basin is a permanent load."],
        status=status,
    )
