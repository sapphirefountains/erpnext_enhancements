"""Verified physical constants for the Phase-1 hydraulic spine.

Every value here was read directly from the source workbooks' formula cells
(openpyxl, formulas + cached values) — NOT from a textbook. Where a workbook
uses a value that diverges from the standard form, both are recorded and the
divergence is noted, so the engine reproduces the spreadsheet exactly and the
discrepancy stays documented.

Sources:
  DOC-0048 Fountain Design Data.xlsx       -> Basin sheet
  DOC-0049 Advanced Fluid Engineering.xlsx -> A - Pipe Size, H - TDH, I - Weir,
                                              10 - Gravity, SUPPORT sheets
"""

# --- basin volume / weight (DOC-0048 Basin!J,K) -----------------------------
GAL_PER_CUBIC_INCH = 0.004329  # Basin!J = I*0.004329 (rounded 1/231 = 0.0043290043...)
LB_PER_GAL = 8.34  # Basin!K = J*8.34
DEFAULT_TURNOVERS_PER_HR = 2.0  # Basin!D15 (hard-coded default)

# Note: the O - Heating sheet uses more precise water constants
# (8.337 lb/gal, 7.480519 gal/ft^3). Phase-1 uses the Basin-sheet values above
# to match the design-data template; the heating values belong to that phase.
LB_PER_GAL_PRECISE = 8.337
GAL_PER_CUBIC_FOOT = 7.480519

# --- pipe velocity (DOC-0049 A - Pipe Size!C7 ; H - TDH!E20) ----------------
VELOCITY_COEFF = 0.4085  # V_fps = GPM * 0.4085 / ID_in^2

# --- Hazen-Williams head loss -----------------------------------------------
# A - Pipe Size!G7:  hf = 10.44 * L * Q^1.85 / (C^1.85 * D^4.8655)
# H - TDH!E25:       same form with the constant 10.456 (0.15% higher)
HW_CONSTANT = 10.44  # default (A - Pipe Size)
HW_CONSTANT_TDH = 10.456  # H - TDH variant
HW_EXPONENT_Q = 1.85  # exponent on flow AND on C (NOT the textbook 1.852)
HW_EXPONENT_D = 4.8655  # exponent on inside diameter
HW_C_PVC = 130  # default Hazen-Williams roughness coefficient for PVC

# --- minor (fitting) loss (DOC-0049 H - TDH!E54) ----------------------------
# minor_ft = SUMPRODUCT(K_i, count_i) * V^2 / (2 * 32.2)
GRAVITY_FT_S2 = 32.2

# --- weir / slot flow, Francis formula (DOC-0049 I - Weir!C16) --------------
# Q_gpm = (36 * L_ft * h_in^1.5) - (0.3 * n * h_in^2.5)
WEIR_FRANCIS_COEFF = 36.0
WEIR_FRANCIS_CONTRACTION_COEFF = 0.3
DEFAULT_WEIR_CONTRACTIONS = 2

# --- pressure <-> head ------------------------------------------------------
# NOT present in either workbook. Pumps are specified in ft of TDH; psi is a
# convenience output only. Standard fresh-water value flagged as such.
FT_PER_PSI = 2.31  # engineering standard (1 / 0.4335) -- not in source docs

# --- electrical (business rules; NOT formula-driven in the workbooks) -------
BREAKER_CONTINUOUS_FACTOR = 1.25  # breaker >= 125% FLA (NEC 430.52) -- confirm w/ engineer

# --- water chemistry (DOC-0049 C - Chemicals ; DOC-0119 targets) ------------
# Liquid-chlorinator minimum capacity: 3 lb Cl2 / 24 hr / 10,000 gal (IBC 3133B.1),
# basis "1 gal @ 10% = 1 lb Cl2".
CHLORINE_MIN_LBS_PER_10KGAL_DAY = 3.0
CHLORINE_REF_PCT = 10.0
# Ozone g/hr conversion: GPM * mg/L * (3780 mL/gal * 60 min/hr / 1e6 mg/g).
OZONE_GHR_FACTOR = 3780 * 60 / 1_000_000
# USEPA CT values (mg/L * min) for Cryptosporidium inactivation by ozone.
CT_CRYPTO_2LOG = 4.9
CT_CRYPTO_3LOG = 7.4

# --- source citations (doc / sheet) -----------------------------------------
CIT_CHEM = "DOC-0049 / C - Chemicals"
CIT_CHEM_TARGETS = "DOC-0119"
CIT_BASIN = "DOC-0048 / Basin"
CIT_PIPE = "DOC-0049 / A - Pipe Size"
CIT_TDH = "DOC-0049 / H - TDH"
CIT_WEIR = "DOC-0049 / I - Weir"
CIT_SUPPORT = "DOC-0049 / SUPPORT"
CIT_GRAVITY = "DOC-0049 / 10 - Gravity"
