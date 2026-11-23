"""Deformed reinforcing bar properties, ASTM A615 / A615M.

Nominal diameter, cross-sectional area and unit weight per inch-pound bar
designation, as tabulated in ASTM A615 Table 1 (the same values CRSI's Manual
of Standard Practice and ACI 318 Appendix A reprint). Unit weight is the
nominal mass of the bar, so it already allows for the deformations.

Stock lengths are what Utah rebar yards carry for #3 to #8: 20 ft and 40 ft
straight bars (60 ft exists for #5 and up, special order).
"""

from __future__ import annotations

CIT_ASTM_A615 = "ASTM A615/A615M, Table 1: nominal dimensions and unit weights of deformed bars"

# bar number -> nominal diameter (in), area (in^2), unit weight (lb/ft)
REBAR = {
    3: {"diameter_in": 0.375, "area_in2": 0.11, "lb_per_ft": 0.376},
    4: {"diameter_in": 0.500, "area_in2": 0.20, "lb_per_ft": 0.668},
    5: {"diameter_in": 0.625, "area_in2": 0.31, "lb_per_ft": 1.043},
    6: {"diameter_in": 0.750, "area_in2": 0.44, "lb_per_ft": 1.502},
    7: {"diameter_in": 0.875, "area_in2": 0.60, "lb_per_ft": 2.044},
    8: {"diameter_in": 1.000, "area_in2": 0.79, "lb_per_ft": 2.670},
}

STOCK_LENGTHS_FT = (20.0, 40.0)


def bar(size) -> dict:
    """Properties of one bar size, accepting ``4``, ``"4"`` or ``"#4"``."""
    key = int(str(size).strip().lstrip("#"))
    if key not in REBAR:
        raise ValueError(f"Unknown rebar size {size!r}; use #3 to #8.")
    return {"size": key, "label": f"#{key}", **REBAR[key]}
