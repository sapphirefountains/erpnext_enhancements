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
