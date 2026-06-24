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

# --- gravity drainage (DOC-0049 10 - Gravity / G - Gravity) -----------------
# Manning's:  Q_gpm = A * (1.486/n) * R^(2/3) * S^(1/2) * 7.48 * 60
# A = half-full area = 3.14 * D^2 / 8 / 144 (literal 3.14, D in inches); R = (D/4)/12 ft.
# DOC-0049 is the conservative authority — NOT DOC-0119 (1.49 / n .009-.011 /
# full-pipe area, which over-predicts capacity ~3x and is internally inconsistent).
MANNING_CONSTANT = 1.486
DRAIN_GAL_PER_CF = 7.48  # the drain sheet's gal/ft^3 (matches its golden cells)
DRAIN_AREA_PI = 3.14  # literal 3.14 used in the half-full area cell

# --- surge basin (DOC-0049 B - Surge Basin) green-cell defaults -------------
SURGE_EVAP_IN_DAY = 0.25
SURGE_PRECIP_IN = 1.0
SURGE_VORTEX_IN = 12.0  # 6.0 if line velocity <= 1 FPS
SURGE_FREEBOARD_IN = 3.0
SURGE_OVERFLOW_IN = 3.0
SWIMMER_WEIGHT_LB = 189.8  # avg adult male (NCHS)
BODY_SPECIFIC_GRAVITY = 1.06
WATER_LB_PER_CF = 62.4

# --- suction-outlet anti-entrapment (DOC-0049 P - Suction Outlets) ----------
# VGB / ANSI-APSP-16 cover sizing. Q = AR * (F / (C * rho/2 * AB))^0.5  (CFS),
# verified verbatim against the P-sheet's worked example (D29).
VGB_LIFT_LOAD_LBF = 120.0  # F, allowable body lifting load (per 2.3.1.2)
VGB_FLOW_COEFF = 2.1  # C, flow coefficient (per 2.3.1.2)
VGB_WATER_DENSITY_SLUG = 1.940  # rho, slug/ft^3 (per 2.3.1.2)
VGB_BODY_BLOCK_LEN_IN = 23.0  # code body-footprint length (per 2.3.1.1)
VGB_BODY_BLOCK_WID_IN = 18.0  # code body-footprint width (per 2.3.1.1)
VGB_MAX_COVER_VELOCITY_FPS = 1.5  # max approach velocity through a cover
CFS_TO_GPM = 7.48 * 60  # 448.8 — ft^3/s -> gal/min

# --- NPSH available (Hydraulic Institute; NOT in the source docs) ------------
# NPSHa = Ha (atm head) + Hz (static, +flooded/-lift) - Hf (suction loss) - Hvp.
ATM_PRESSURE_PSIA_SEA = 14.696  # standard sea-level atmospheric pressure
NPSH_DEFAULT_MARGIN_FT = 3.0  # require NPSHa >= NPSHr + margin (HI 2-3 ft)
# Saturated water-vapor pressure (psia) by temperature (degF); interpolated.
VAPOR_PRESSURE_PSIA = {
    40: 0.122, 50: 0.178, 60: 0.256, 70: 0.363, 80: 0.507,
    90: 0.698, 100: 0.949, 110: 1.275, 120: 1.692,
}

# --- water hammer / Joukowsky surge (NOT in the source docs) -----------------
# dH = a * dV / g  (head);  dP_psi = dH / 2.31.  Pressure-wave speed `a` (ft/s)
# is material/wall dependent; these are representative values, override-able.
WAVE_SPEED_FPS = {"SCH40 PVC": 1300.0, "SCH80 PVC": 1400.0, "COPPER": 4270.0, "STEEL": 4500.0}
WAVE_SPEED_DEFAULT_FPS = 1300.0  # PVC

# --- electric operating cost (DOC-0049 E - Elec Costs) ----------------------
# WHP = SG*TDH*Q/3960 -> BHP /pump_eff -> HP /motor_eff -> KW *0.7457 -> $.
WHP_DIVISOR = 3960.0
HP_TO_KW = 0.7457
DEFAULT_PUMP_EFF = 0.70  # hydraulic efficiency
DEFAULT_MOTOR_EFF = 0.90
DEFAULT_KWH_RATE = 0.17  # $/kWh
DEFAULT_PUMP_HOURS_DAY = 6.0
DAYS_PER_YEAR = 365.0

# --- vertical-pipe discharge (DOC-0049 K - Vert Pipe) -----------------------
# Q_gpm = 5.68 * H_in^0.5 * K * ID_in^2 ; K = 0.82 + 0.025*ID (0.92 in recommend mode).
VERT_PIPE_COEFF = 5.68
VERT_PIPE_K_FIXED = 0.92

# --- open-channel & lazy-river Manning (DOC-0049 J - Channel / L - Lazy) -----
KINEMATIC_VISCOSITY_FT2_S = 9.26e-6  # water @ 80F, for Reynolds
DEFAULT_CHANNEL_N = 0.015
DEFAULT_LAZY_RIVER_N = 0.0155
LAZY_RIVER_SAFETY_FACTOR = 2.0

# --- programmatic planning (DOC-0049 D - Program) ---------------------------
SF_PER_POOL_USER = 15.0
SF_PER_SPA_USER = 9.0
SF_PER_SKIMMER = 400.0
PERIMETER_OVERFLOW_SF_THRESHOLD = 5000.0
SOLAR_PANEL_FRACTION = 0.8

# --- source citations (doc / sheet) -----------------------------------------
CIT_ELEC = "DOC-0049 / E - Elec Costs"
CIT_VERT_PIPE = "DOC-0049 / K - Vert Pipe"
CIT_CHANNEL = "DOC-0049 / J - Channel"
CIT_LAZY = "DOC-0049 / L - Lazy"
CIT_PROGRAM = "DOC-0049 / D - Program"
CIT_VGB = "DOC-0049 / P - Suction Outlets ; ANSI/APSP-16"
CIT_NPSH = "Hydraulic Institute / NPSH (engineering standard, not in source docs)"
CIT_WATER_HAMMER = "Joukowsky surge equation (engineering standard, not in source docs)"
CIT_CHEM = "DOC-0049 / C - Chemicals"
CIT_CHEM_TARGETS = "DOC-0119"
CIT_BASIN = "DOC-0048 / Basin"
CIT_PIPE = "DOC-0049 / A - Pipe Size"
CIT_TDH = "DOC-0049 / H - TDH"
CIT_WEIR = "DOC-0049 / I - Weir"
CIT_SUPPORT = "DOC-0049 / SUPPORT"
CIT_GRAVITY = "DOC-0049 / 10 - Gravity"
