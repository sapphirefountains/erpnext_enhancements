"""Minor-loss K-factors and component head-loss coefficients.

Verified from DOC-0049 ``H - TDH`` sheet:
  * FITTING_K     (B26:C53) -- velocity-head K for fittings/valves; minor loss =
                   SUMPRODUCT(K, count) * V^2 / (2 * 32.2).
  * COMPONENT_COEFF (B55:C74) -- ft-of-head-per-GPM for filters/skimmers/etc.;
                   component loss = SUMPRODUCT(coeff, count) * Q.

Names are ASCII (the source labels use degree symbols and quotes for pipe
sizes); the originals are noted where they differ so the desk dropdowns and the
AI use one stable key set.
"""

from __future__ import annotations

# Fitting / valve velocity-head K-factors (source label -> K).
FITTING_K: dict[str, float] = {
    "ELL 90": 0.81,  # ELL 90 deg
    "ELL 90 LONG RADIUS": 0.43,
    "ELL 90 MITERED": 1.62,
    "ELL 45": 0.43,
    "ELL 45 MITERED": 0.41,
    "TEE LINE 180": 0.54,
    "TEE BRANCH 90": 1.62,
    "BUTTERFLY VALVE": 0.86,
    "BALL VALVE": 0.08,
    "GLOBE VALVE": 9.2,
    "GATE VALVE": 0.22,
    "SWING CHECK VALVE": 3.0,
    "ENTRANCE REENTRANT": 1.0,
    "ENTRANCE FLUSH": 0.5,
    "ENTRANCE r/D=0.02": 0.28,
    "ENTRANCE r/D=0.06": 0.15,
    "ENTRANCE r/D>=0.15": 0.04,
    "EXIT": 1.0,
    "REDUCER d1/d2=0.75": 0.21875,
    "REDUCER d1/d2=0.50": 0.375,
    "REDUCER d1/d2=0.25": 0.46875,
    "REDUCER d1/d2=0.10": 0.495,
    "INCREASER d1/d2=1.33": 0.18894402291901402,
    "INCREASER d1/d2=2.00": 0.5625,
    "INCREASER d1/d2=4.00": 0.87890625,
    "INCREASER d1/d2=10.0": 0.9801,
}

# Component head-loss coefficients in ft-of-head per GPM (source label -> coeff).
# Single-slope approximation; the real (often nonlinear) curves are in
# COMPONENT_CURVES below and are what ``component_loss`` uses — these coefficients
# remain as a fallback + the desk-picker hint.
COMPONENT_COEFF: dict[str, float] = {
    "SUCTION OUTLET COVER/GRATE": 0.05,
    'SKIMMER W/ 2" PORTS': 0.075,
    'MULTIPORT VALVE, 2"': 0.07083333333333333,
    'SLIDING BACKWASH VALVE, 2"': 0.14666666666666667,
    "CARTRIDGE FILTER 240 SF CCP CLEAN": 0.07666666666666667,
    "CARTRIDGE FILTER 320 SF CCP CLEAN": 0.07666666666666667,
    "CARTRIDGE FILTER 420 SF CCP CLEAN": 0.07666666666666667,
    "CARTRIDGE FILTER 520 SF CCP CLEAN": 0.07666666666666667,
    'SAND FILTER TRITON II 24" DIA CLEAN': 0.125,
    'SAND FILTER TRITON II 30" DIA CLEAN': 0.126,
    'SAND FILTER TRITON II 36" DIA CLEAN': 0.136,
    "D.E. FILTER 60 SF QUAD D.E. CLEAN": 0.125,
    "D.E. FILTER 80 SF QUAD D.E. CLEAN": 0.125,
    "D.E. FILTER 100 SF QUAD D.E. CLEAN": 0.125,
    "HEATER, GENERIC 400,000 BTU/H": 0.08333333333333333,
    'LASCO VENTURI TEE 473-210 1.5"x1.5"x1"': 1.8466666666666667,
}

# Real component head-loss curves from DOC-0049 sheet "7 - Component Loss":
# points = [(gpm, ft_head_loss), ...] (the head loss IS in feet, used directly in
# the TDH sum). ``max_gpm`` is the manufacturer's rated flow ceiling (explicit
# note where given, else the last tabulated flow). Filters are convex (a 520
# cartridge jumps 1.8->3.1 ft from 50->60 GPM), so a single coefficient
# under/over-states the loss across the range; ``component_loss`` interpolates
# these and warns when a component runs past its ``max_gpm``. Cartridge and D.E.
# families share a head-loss shape and differ only in max_gpm.
COMPONENT_CURVES: dict[str, dict] = {
    "SUCTION OUTLET COVER/GRATE": {
        "points": [(10, 0.5), (50, 2.5), (100, 5.0), (160, 8.0)], "max_gpm": 160,
        "source": "Sta-Rite Training Manual 2003 p14"},
    'SKIMMER W/ 2" PORTS': {
        "points": [(10, 0.75), (40, 3.0), (75, 5.625)], "max_gpm": 75,
        "source": "Sta-Rite Training Manual 2003 p14"},
    'MULTIPORT VALVE, 2"': {
        "points": [(10, 0.7083), (60, 4.25), (120, 8.5), (150, 10.625)], "max_gpm": 150,
        "source": "Sta-Rite Training Manual 2003 p14"},
    'SLIDING BACKWASH VALVE, 2"': {
        "points": [(10, 1.4667), (60, 8.8), (120, 17.6), (150, 22.0)], "max_gpm": 150,
        "source": "Sta-Rite Training Manual 2003 p14"},
    "CARTRIDGE FILTER 240 SF CCP CLEAN": {
        "points": [(10, 0.2), (20, 0.5), (30, 0.8), (40, 1.2), (50, 1.8),
                   (60, 3.1), (70, 4.2), (80, 5.4), (90, 6.9)], "max_gpm": 90,
        "source": "Pentair C&C+ Owner's Manual"},
    "CARTRIDGE FILTER 320 SF CCP CLEAN": {
        "points": [(10, 0.2), (20, 0.5), (30, 0.8), (40, 1.2), (50, 1.8), (60, 3.1),
                   (70, 4.2), (80, 5.4), (90, 6.9), (100, 8.3), (110, 10.2), (120, 12.1)],
        "max_gpm": 120, "source": "Pentair C&C+ Owner's Manual"},
    "CARTRIDGE FILTER 420 SF CCP CLEAN": {
        "points": [(10, 0.2), (20, 0.5), (30, 0.8), (40, 1.2), (50, 1.8), (60, 3.1),
                   (70, 4.2), (80, 5.4), (90, 6.9), (100, 8.3), (110, 10.2), (120, 12.1),
                   (130, 13.9), (140, 15.7), (150, 16.8)], "max_gpm": 150,
        "source": "Pentair C&C+ Owner's Manual"},
    "CARTRIDGE FILTER 520 SF CCP CLEAN": {
        "points": [(10, 0.2), (20, 0.5), (30, 0.8), (40, 1.2), (50, 1.8), (60, 3.1),
                   (70, 4.2), (80, 5.4), (90, 6.9), (100, 8.3), (110, 10.2), (120, 12.1),
                   (130, 13.9), (140, 15.7), (150, 16.8)], "max_gpm": 150,
        "source": "Pentair C&C+ Owner's Manual"},
    'SAND FILTER TRITON II 24" DIA CLEAN': {
        "points": [(10, 1.0), (20, 1.5), (30, 2.6), (40, 3.8), (50, 5.1), (60, 7.5)],
        "max_gpm": 60, "source": "Pentair Triton II Owner's Manual"},
    'SAND FILTER TRITON II 30" DIA CLEAN': {
        "points": [(10, 1.0), (20, 1.5), (30, 2.6), (40, 3.8), (50, 4.6), (60, 5.7),
                   (70, 6.9), (80, 8.3), (90, 10.2), (100, 12.6)], "max_gpm": 100,
        "source": "Pentair Triton II Owner's Manual"},
    'SAND FILTER TRITON II 36" DIA CLEAN': {
        "points": [(10, 1.0), (20, 1.1), (30, 1.6), (40, 2.3), (50, 3.3), (60, 4.3),
                   (70, 5.9), (80, 7.4), (90, 9.2), (100, 10.9), (110, 12.8), (120, 14.8),
                   (130, 16.9), (140, 19.0), (150, 20.3571)], "max_gpm": 150,
        "source": "Pentair Triton II Owner's Manual"},
    "D.E. FILTER 60 SF QUAD D.E. CLEAN": {
        "points": [(10, 0.4), (20, 0.7), (30, 1.2), (40, 2.1), (50, 3.1), (60, 4.2),
                   (70, 5.4), (80, 6.9), (90, 8.5), (100, 10.4), (110, 12.7), (120, 15.0)],
        "max_gpm": 120, "source": "Pentair Quad D.E. Owner's Manual"},
    "D.E. FILTER 80 SF QUAD D.E. CLEAN": {
        "points": [(10, 0.4), (20, 0.7), (30, 1.2), (40, 2.1), (50, 3.1), (60, 4.2),
                   (70, 5.4), (80, 6.9), (90, 8.5), (100, 10.4), (110, 12.7), (120, 15.0),
                   (130, 17.3), (140, 18.6), (150, 20.0), (160, 21.3)], "max_gpm": 160,
        "source": "Pentair Quad D.E. Owner's Manual"},
    "D.E. FILTER 100 SF QUAD D.E. CLEAN": {
        "points": [(10, 0.4), (20, 0.7), (30, 1.2), (40, 2.1), (50, 3.1), (60, 4.2),
                   (70, 5.4), (80, 6.9), (90, 8.5), (100, 10.4), (110, 12.7), (120, 15.0),
                   (130, 17.3), (140, 18.6), (150, 20.0), (160, 21.3)], "max_gpm": 160,
        "source": "Pentair Quad D.E. Owner's Manual"},
    "HEATER, GENERIC 400,000 BTU/H": {
        "points": [(10, 0.8333), (60, 5.0), (120, 10.0)], "max_gpm": 120,
        "source": "Sta-Rite Training Manual 2003 p14"},
    'LASCO VENTURI TEE 473-210 1.5"x1.5"x1"': {
        "points": [(10, 18.4667), (15, 27.7), (20, 36.9333)], "max_gpm": 15,
        "source": "Lasco & Waterway (design 15 GPM @ 27.7 ft / 12 PSI)"},
}
