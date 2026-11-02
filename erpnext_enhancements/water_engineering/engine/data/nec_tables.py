"""NEC tables for the electrical package (``engine/electrical/``).

Edition: **NFPA 70, National Electrical Code, 2023**, with the 2020 numbering
noted where it differs. Every value here is a transcription of a published table;
the electrical modules read them and never restate a number. Where the 2020 and
2023 editions disagree on something that changes a result, the 2020 value is
noted beside the table. Which edition a job is reviewed under is the authority
having jurisdiction's call (each state's edition and basis are in
``data/jurisdictions.json``; ``electrical_report`` names the site's), so every
result built on these tables tells the reader to verify the adopted edition.

Table numbering moved in the 2020 edition: Table 310.15(B)(16) became Table
310.16, Table 310.15(B)(2)(a) became Table 310.15(B)(1), Table 310.15(B)(3)(a)
became Table 310.15(C)(1), and Table 430.52 became Table 430.52(C)(1). The
values did not change in those moves.
"""

from __future__ import annotations

NEC_EDITION = "NFPA 70 (NEC) 2023"
VERIFY_EDITION = (
    "Verify against the NEC edition the site's state or city has adopted and any local "
    "amendments; the authority having jurisdiction decides."
)

# --- conductor sizes --------------------------------------------------------
# AWG / kcmil sizes in order, smallest first. Conductor sizes are strings
# because "1/0" and "250" are not numbers you can compare.
SIZES = [
    "18", "16", "14", "12", "10", "8", "6", "4", "3", "2", "1",
    "1/0", "2/0", "3/0", "4/0", "250", "300", "350", "400", "500", "600", "750",
]


def size_index(size: str) -> int:
    """Position of a conductor size in :data:`SIZES` (bigger is later)."""
    return SIZES.index(str(size))


# --- Table 310.16 (was 310.15(B)(16) before 2020) -----------------------------
# Ampacity of insulated conductors, not more than three current-carrying
# conductors in a raceway, cable or earth, 30 C (86 F) ambient. Columns 60 C
# (TW, UF), 75 C (THW, THWN, XHHW, USE) and 90 C (THHN, THWN-2, XHHW-2). The 90 C
# column is for derating only unless every termination is rated 90 C (110.14(C)).
AMPACITY_310_16 = {
    "Cu": {
        "14": (15, 20, 25), "12": (20, 25, 30), "10": (30, 35, 40), "8": (40, 50, 55),
        "6": (55, 65, 75), "4": (70, 85, 95), "3": (85, 100, 115), "2": (95, 115, 130),
        "1": (110, 130, 145), "1/0": (125, 150, 170), "2/0": (145, 175, 195),
        "3/0": (165, 200, 225), "4/0": (195, 230, 260), "250": (215, 255, 290),
        "300": (240, 285, 320), "350": (260, 310, 350), "400": (280, 335, 380),
        "500": (320, 380, 430), "600": (350, 420, 475), "750": (400, 475, 535),
    },
    "Al": {
        "12": (15, 20, 25), "10": (25, 30, 35), "8": (35, 40, 45), "6": (40, 50, 55),
        "4": (55, 65, 75), "3": (65, 75, 85), "2": (75, 90, 100), "1": (85, 100, 115),
        "1/0": (100, 120, 135), "2/0": (115, 135, 150), "3/0": (130, 155, 175),
        "4/0": (150, 180, 205), "250": (170, 205, 230), "300": (195, 230, 260),
        "350": (210, 250, 280), "400": (225, 270, 305), "500": (260, 310, 350),
        "600": (285, 340, 385), "750": (320, 385, 435),
    },
}
TEMP_COLUMNS = (60, 75, 90)
CIT_AMPACITY = "NEC Table 310.16 (Table 310.15(B)(16) before 2020)"

# --- 240.4(D) small-conductor rule --------------------------------------------
# The OCPD for these sizes may not exceed the value below, whatever the table
# ampacity says. Does not apply to motor circuits (240.4(G) sends those to
# Article 430 Parts III and IV) or to Class 1 / 2 / 3 circuits.
SMALL_CONDUCTOR_MAX_OCPD = {
    "Cu": {"18": 7, "16": 10, "14": 15, "12": 20, "10": 30},
    "Al": {"12": 15, "10": 25},
}
CIT_SMALL_CONDUCTOR = "NEC 240.4(D) (small conductors); 240.4(G) exempts motor circuits"

# --- 110.14(C) termination temperature ---------------------------------------
# Circuits rated 100 A or less, or marked for 14 AWG through 1 AWG, use the 60 C
# column unless the equipment is listed and marked for 75 C terminations. Over
# 100 A, or larger than 1 AWG, use 75 C.
TERMINATION_SMALL_MAX_A = 100
CIT_TERMINATION = "NEC 110.14(C)(1) (termination temperature limits)"

# --- Table 310.15(B)(1) (was 310.15(B)(2)(a)) ---------------------------------
# Ambient temperature correction factors for a 30 C table. Rows are the upper
# bound of each ambient band (C); values are the 60 / 75 / 90 C column factors.
# None = the insulation cannot be used at that ambient. The 2023 edition splits
# this into 310.15(B)(1)(1) (30 C tables) and (B)(1)(2) (40 C tables); the 30 C
# values are unchanged.
TEMP_CORRECTION = [
    (10, (1.29, 1.20, 1.15)),
    (15, (1.22, 1.15, 1.12)),
    (20, (1.15, 1.11, 1.08)),
    (25, (1.08, 1.05, 1.04)),
    (30, (1.00, 1.00, 1.00)),
    (35, (0.91, 0.94, 0.96)),
    (40, (0.82, 0.88, 0.91)),
    (45, (0.71, 0.82, 0.87)),
    (50, (0.58, 0.75, 0.82)),
    (55, (0.41, 0.67, 0.76)),
    (60, (None, 0.58, 0.71)),
    (65, (None, 0.47, 0.65)),
    (70, (None, 0.33, 0.58)),
    (75, (None, None, 0.50)),
    (80, (None, None, 0.41)),
    (85, (None, None, 0.29)),
]
CIT_TEMP_CORRECTION = "NEC Table 310.15(B)(1) (Table 310.15(B)(2)(a) before 2020)"
# 310.15(B)(2): raceways or cables exposed to sunlight on or above rooftops, less
# than 23 mm (7/8 in) above the roof, add 33 C (60 F) to the outdoor ambient.
ROOFTOP_ADDER_C = 33

# --- Table 310.15(C)(1) (was 310.15(B)(3)(a)) ---------------------------------
# Adjustment for more than three current-carrying conductors in a raceway or
# cable: (max conductors in the band, factor). The neutral of a balanced
# 3-wire circuit and the equipment grounding conductor are not counted.
CONDUCTOR_COUNT_ADJUSTMENT = [
    (3, 1.00),
    (6, 0.80),
    (9, 0.70),
    (20, 0.50),
    (30, 0.45),
    (40, 0.40),
    (10_000, 0.35),
]
CIT_COUNT_ADJUSTMENT = "NEC Table 310.15(C)(1) (Table 310.15(B)(3)(a) before 2020)"

# --- Table 250.122 ------------------------------------------------------------
# Minimum equipment grounding conductor by the rating of the OCPD ahead of it:
# (max OCPD amps, copper size, aluminum size). 250.122(B) makes the EGC grow in
# proportion when the ungrounded conductors are upsized (voltage drop is the
# usual reason). Pool and spa motor circuits need an insulated copper EGC no
# smaller than 12 AWG (680.21(A)(1), 680.23(F)(2)).
EGC_250_122 = [
    (15, "14", "12"),
    (20, "12", "10"),
    (60, "10", "8"),
    (100, "8", "6"),
    (200, "6", "4"),
    (300, "4", "2"),
    (400, "3", "1"),
    (500, "2", "1/0"),
    (600, "1", "2/0"),
    (800, "1/0", "3/0"),
    (1000, "2/0", "4/0"),
    (1200, "3/0", "250"),
    (1600, "4/0", "350"),
    (2000, "250", "400"),
]
CIT_EGC = "NEC Table 250.122 / 250.122(B) (proportional upsizing)"

# --- Chapter 9 Table 8 --------------------------------------------------------
# Conductor properties: area in circular mils and DC resistance at 75 C in ohms
# per 1000 ft, stranded, uncoated copper and aluminum. (18 and 16 AWG are the
# 7-strand values; the low-voltage coil runs use them.)
CONDUCTOR_PROPS = {
    # size: (circular mils, Cu ohm/kft, Al ohm/kft)
    "18": (1620, 7.95, 13.1),
    "16": (2580, 4.99, 8.21),
    "14": (4110, 3.14, 5.17),
    "12": (6530, 1.98, 3.25),
    "10": (10380, 1.24, 2.04),
    "8": (16510, 0.778, 1.28),
    "6": (26240, 0.491, 0.808),
    "4": (41740, 0.308, 0.508),
    "3": (52620, 0.245, 0.403),
    "2": (66360, 0.194, 0.319),
    "1": (83690, 0.154, 0.253),
    "1/0": (105600, 0.122, 0.201),
    "2/0": (133100, 0.0967, 0.159),
    "3/0": (167800, 0.0766, 0.126),
    "4/0": (211600, 0.0608, 0.100),
    "250": (250000, 0.0515, 0.0847),
    "300": (300000, 0.0429, 0.0707),
    "350": (350000, 0.0367, 0.0605),
    "400": (400000, 0.0321, 0.0529),
    "500": (500000, 0.0258, 0.0424),
    "600": (600000, 0.0214, 0.0353),
    "750": (750000, 0.0171, 0.0282),
}
CIT_TABLE_8 = "NEC Chapter 9 Table 8 (DC resistance at 75 C, stranded)"
# Table 402.5 fixture-wire ampacity, used only for 18 / 16 AWG low-voltage runs
# (the rain curtain's coil wiring).
FIXTURE_WIRE_AMPACITY = {"18": 6, "16": 8}

# --- Chapter 9 Table 5 --------------------------------------------------------
# Approximate area (in^2) of THHN / THWN / THWN-2 conductors, the insulation
# used for every conductor in a fountain raceway, the EGC included.
THHN_AREA_IN2 = {
    "14": 0.0097, "12": 0.0133, "10": 0.0211, "8": 0.0366, "6": 0.0507,
    "4": 0.0824, "3": 0.0973, "2": 0.1158, "1": 0.1562, "1/0": 0.1855,
    "2/0": 0.2223, "3/0": 0.2679, "4/0": 0.3237, "250": 0.3970, "300": 0.4608,
    "350": 0.5242, "400": 0.5863, "500": 0.7073, "600": 0.8676, "750": 1.0496,
}
CIT_TABLE_5 = "NEC Chapter 9 Table 5 (THHN/THWN-2 conductor area)"

# --- Chapter 9 Table 4 --------------------------------------------------------
# Total internal area (in^2, 100%) by trade size. 40% of it is the fill allowed
# for over two conductors (Chapter 9 Table 1).
CONDUIT_AREA_IN2 = {
    "EMT": {
        '1/2"': 0.304, '3/4"': 0.533, '1"': 0.864, '1-1/4"': 1.496, '1-1/2"': 2.036,
        '2"': 3.356, '2-1/2"': 5.858, '3"': 8.846, '3-1/2"': 11.545, '4"': 14.753,
    },
    "PVC-40": {
        '1/2"': 0.285, '3/4"': 0.508, '1"': 0.832, '1-1/4"': 1.453, '1-1/2"': 1.986,
        '2"': 3.291, '2-1/2"': 4.695, '3"': 7.268, '3-1/2"': 9.737, '4"': 12.554,
    },
    "PVC-80": {
        '1/2"': 0.217, '3/4"': 0.409, '1"': 0.688, '1-1/4"': 1.237, '1-1/2"': 1.711,
        '2"': 2.874, '2-1/2"': 4.119, '3"': 6.442, '3-1/2"': 8.688, '4"': 11.258,
    },
    "RMC": {
        '1/2"': 0.314, '3/4"': 0.549, '1"': 0.887, '1-1/4"': 1.526, '1-1/2"': 2.071,
        '2"': 3.408, '2-1/2"': 4.866, '3"': 7.499, '3-1/2"': 10.010, '4"': 12.882,
    },
}
CONDUIT_LABELS = {
    "EMT": "EMT",
    "PVC-40": "PVC Schedule 40",
    "PVC-80": "PVC Schedule 80",
    "RMC": "Rigid metal conduit",
}
CIT_TABLE_4 = "NEC Chapter 9 Table 4 (conduit internal area)"

# --- Chapter 9 Table 1 --------------------------------------------------------
# Percent of conduit area conductors may fill: 1 conductor 53%, 2 conductors
# 31%, over 2 conductors 40%. Note 4: a nipple of 24 in or less may fill 60%.
FILL_PERCENT = {1: 53, 2: 31, "over_2": 40, "nipple": 60}
CIT_TABLE_1 = "NEC Chapter 9 Table 1 (percent of cross section)"

# --- 240.6(A) -----------------------------------------------------------------
# Standard ampere ratings for fuses and inverse-time circuit breakers. Fuses
# also come in 1, 3, 6, 10 and 601 A. (The 2020 edition put these in Table
# 240.6(A); the list is the same.) Breakers in a fountain panel start at 15 A.
STD_AMPS_240_6 = [
    15, 20, 25, 30, 35, 40, 45, 50, 60, 70, 80, 90, 100, 110, 125, 150, 175, 200,
    225, 250, 300, 350, 400, 450, 500, 600, 700, 800, 1000, 1200, 1600, 2000,
    2500, 3000, 4000, 5000, 6000,
]
STD_FUSE_EXTRA_AMPS = [1, 3, 6, 10, 601]
CIT_STD_OCPD = "NEC 240.6(A) (standard ampere ratings)"

# --- Table 430.248 ------------------------------------------------------------
# Full-load current (A) of single-phase AC motors, by HP and rated voltage. The
# table has 115, 200, 208 and 230 V columns; 110-120 V motors use 115 and
# 220-240 V motors use 230. Conductors and OCPD are sized from this table, not
# the nameplate (430.6(A)(1)); overloads use the nameplate.
FLC_1PH = {
    1 / 6: {115: 4.4, 200: 2.5, 208: 2.4, 230: 2.2},
    0.25: {115: 5.8, 200: 3.3, 208: 3.2, 230: 2.9},
    1 / 3: {115: 7.2, 200: 4.1, 208: 4.0, 230: 3.6},
    0.5: {115: 9.8, 200: 5.6, 208: 5.4, 230: 4.9},
    0.75: {115: 13.8, 200: 7.9, 208: 7.6, 230: 6.9},
    1.0: {115: 16.0, 200: 9.2, 208: 8.8, 230: 8.0},
    1.5: {115: 20.0, 200: 11.5, 208: 11.0, 230: 10.0},
    2.0: {115: 24.0, 200: 13.8, 208: 13.2, 230: 12.0},
    3.0: {115: 34.0, 200: 19.6, 208: 18.7, 230: 17.0},
    5.0: {115: 56.0, 200: 32.2, 208: 30.8, 230: 28.0},
    7.5: {115: 80.0, 200: 46.0, 208: 44.0, 230: 40.0},
    10.0: {115: 100.0, 200: 57.5, 208: 55.0, 230: 50.0},
}
CIT_FLC_1PH = "NEC Table 430.248 (single-phase motor full-load current)"

# --- Table 430.250 ------------------------------------------------------------
# Full-load current (A) of three-phase induction (squirrel-cage) motors. The
# 115 V column stops at 2 HP. 440-480 V motors use 460; 550-600 V use 575.
FLC_3PH = {
    0.5: {115: 4.4, 200: 2.5, 208: 2.4, 230: 2.2, 460: 1.1, 575: 0.9},
    0.75: {115: 6.4, 200: 3.7, 208: 3.5, 230: 3.2, 460: 1.6, 575: 1.3},
    1.0: {115: 8.4, 200: 4.8, 208: 4.6, 230: 4.2, 460: 2.1, 575: 1.7},
    1.5: {115: 12.0, 200: 6.9, 208: 6.6, 230: 6.0, 460: 3.0, 575: 2.4},
    2.0: {115: 13.6, 200: 7.8, 208: 7.5, 230: 6.8, 460: 3.4, 575: 2.7},
    3.0: {200: 11.0, 208: 10.6, 230: 9.6, 460: 4.8, 575: 3.9},
    5.0: {200: 17.5, 208: 16.7, 230: 15.2, 460: 7.6, 575: 6.1},
    7.5: {200: 25.3, 208: 24.2, 230: 22.0, 460: 11.0, 575: 9.0},
    10.0: {200: 32.2, 208: 30.8, 230: 28.0, 460: 14.0, 575: 11.0},
    15.0: {200: 48.3, 208: 46.2, 230: 42.0, 460: 21.0, 575: 17.0},
    20.0: {200: 62.1, 208: 59.4, 230: 54.0, 460: 27.0, 575: 22.0},
    25.0: {200: 78.2, 208: 74.8, 230: 68.0, 460: 34.0, 575: 27.0},
    30.0: {200: 92.0, 208: 88.0, 230: 80.0, 460: 40.0, 575: 32.0},
    40.0: {200: 120.0, 208: 114.0, 230: 104.0, 460: 52.0, 575: 41.0},
    50.0: {200: 150.0, 208: 143.0, 230: 130.0, 460: 65.0, 575: 52.0},
    60.0: {200: 177.0, 208: 169.0, 230: 154.0, 460: 77.0, 575: 62.0},
    75.0: {200: 221.0, 208: 211.0, 230: 192.0, 460: 96.0, 575: 77.0},
    100.0: {200: 285.0, 208: 273.0, 230: 248.0, 460: 124.0, 575: 99.0},
}
CIT_FLC_3PH = "NEC Table 430.250 (three-phase motor full-load current)"

# Rated-voltage bands that map a system voltage onto a table column (430.248 /
# 430.250 notes): (low, high, column).
FLC_VOLTAGE_BANDS = [
    (100, 127, 115),
    (190, 202, 200),
    (203, 219, 208),
    (220, 250, 230),
    (440, 480, 460),
    (550, 600, 575),
]

# --- Table 430.52(C)(1) (Table 430.52 before 2020) ---------------------------
# Maximum rating of the motor branch-circuit short-circuit and ground-fault
# protective device, percent of full-load current. Keys: the device types.
OCPD_DEVICES = {
    "nontime_delay_fuse": "Nontime-delay fuse",
    "dual_element_fuse": "Dual-element (time-delay) fuse",
    "instantaneous_trip": "Instantaneous-trip breaker",
    "inverse_time_breaker": "Inverse-time breaker",
}
MOTOR_OCPD_PERCENT = {
    "single_phase": {
        "nontime_delay_fuse": 300, "dual_element_fuse": 175,
        "instantaneous_trip": 800, "inverse_time_breaker": 250,
    },
    "squirrel_cage": {  # AC polyphase other than wound-rotor, other than Design B energy-efficient
        "nontime_delay_fuse": 300, "dual_element_fuse": 175,
        "instantaneous_trip": 800, "inverse_time_breaker": 250,
    },
    "design_b_energy_efficient": {
        "nontime_delay_fuse": 300, "dual_element_fuse": 175,
        "instantaneous_trip": 1100, "inverse_time_breaker": 250,
    },
    "synchronous": {
        "nontime_delay_fuse": 300, "dual_element_fuse": 175,
        "instantaneous_trip": 800, "inverse_time_breaker": 250,
    },
    "wound_rotor": {
        "nontime_delay_fuse": 150, "dual_element_fuse": 150,
        "instantaneous_trip": 800, "inverse_time_breaker": 150,
    },
    "dc_constant_voltage": {
        "nontime_delay_fuse": 150, "dual_element_fuse": 150,
        "instantaneous_trip": 250, "inverse_time_breaker": 150,
    },
}
CIT_MOTOR_OCPD = "NEC 430.52(C)(1) and Table 430.52(C)(1) (Table 430.52 before 2020)"
# 430.52(C)(1) Exception 2: where the table value will not carry the starting
# current, the device may be raised to these absolute limits (percent of FLC).
# Inverse-time breakers: 400% for FLC of 100 A or less, 300% above 100 A.
MOTOR_OCPD_EXC2_PERCENT = {
    "nontime_delay_fuse": 400,  # fuses 600 A or less
    "dual_element_fuse": 225,
    "inverse_time_breaker": 400,
    "inverse_time_breaker_over_100a": 300,
    "instantaneous_trip": 1300,  # 430.52(C)(3): adjustable, part of a listed combination
    "instantaneous_trip_design_b": 1700,
}

# --- Table 430.7(B) -----------------------------------------------------------
# Locked-rotor indicating code letters: kVA per HP with the rotor locked, as
# (letter, low, high). Starting kVA = HP x kVA/HP; the high bound is used for
# generator sizing because it is the conservative end.
LOCKED_ROTOR_KVA_PER_HP = [
    ("A", 0.0, 3.14), ("B", 3.15, 3.54), ("C", 3.55, 3.99), ("D", 4.0, 4.49),
    ("E", 4.5, 4.99), ("F", 5.0, 5.59), ("G", 5.6, 6.29), ("H", 6.3, 7.09),
    ("J", 7.1, 7.99), ("K", 8.0, 8.99), ("L", 9.0, 9.99), ("M", 10.0, 11.19),
    ("N", 11.2, 12.49), ("P", 12.5, 13.99), ("R", 14.0, 15.99), ("S", 16.0, 17.99),
    ("T", 18.0, 19.99), ("U", 20.0, 22.39), ("V", 22.4, 22.4),
]
CIT_CODE_LETTER = "NEC Table 430.7(B) (locked-rotor code letters)"

# --- Table 450.3(B) -----------------------------------------------------------
# Transformers 1000 V and less, primary-only protection: maximum primary OCPD as
# a percent of rated primary current, by that current: (current at or above, %).
# Note 1 lets 125% go to the next standard size up when the current is 9 A or more.
XFMR_PRIMARY_ONLY_PERCENT = [(9.0, 125), (2.0, 167), (0.0, 300)]
CIT_XFMR_PRIMARY = "NEC 450.3(B) and Table 450.3(B) (primary-only protection)"

# --- Article 680 numbers ------------------------------------------------------
# 680.2 low-voltage contact limit: the voltage below which no GFCI is needed on
# listed underwater equipment supplied per 680.23(A)(2).
LOW_VOLTAGE_CONTACT_LIMIT = {
    "ac_sinusoidal_rms": 15.0,
    "ac_nonsinusoidal_peak": 21.2,
    "dc_continuous": 30.0,
    "dc_interrupted_peak": 12.4,
}
FOUNTAIN_LUMINAIRE_MAX_V = 150  # 680.51(B): luminaires, pumps, submersibles <= 150 V between conductors
FOUNTAIN_CORD_MAX_FT = 10  # 680.51(E): exposed flexible cord in a fountain <= 3.0 m (10 ft)
FOUNTAIN_RECEPTACLE_GFCI_FT = 20  # 680.58: receptacles within 6.0 m (20 ft) of the fountain edge are GFCI
POOL_LENS_MIN_DEPTH_IN = 18  # 680.23(A)(5): top of lens >= 450 mm (18 in) below normal water level
POOL_LENS_LISTED_MIN_DEPTH_IN = 4  # 680.23(A)(5): luminaires listed for it, >= 100 mm (4 in)
DISCONNECT_MIN_FT = 5  # 680.13 (680.12 before 2020): >= 1.5 m (5 ft) from the inside wall, within sight
BONDING_MIN_AWG = "8"  # 680.26(B): solid copper, 8 AWG or larger
BONDING_PERIMETER_FT = 3  # 680.26(B)(2): perimeter surfaces 1 m (3 ft) out from the inside wall
BONDING_FIXED_METAL_H_FT = 5  # 680.26(B)(7): fixed metal within 1.5 m (5 ft) horizontally ...
BONDING_FIXED_METAL_V_FT = 12  # ... and 3.7 m (12 ft) vertically above the water
POOL_WATER_BOND_IN2 = 9  # 680.26(C): >= 5800 mm2 (9 in2) of conductive surface in contact with the water
JBOX_ABOVE_GROUND_IN = 4  # 680.24(A)(2)(a): >= 100 mm (4 in) above ground or deck ...
JBOX_ABOVE_WATER_IN = 8  # ... and >= 200 mm (8 in) above the maximum water level, whichever is greater
JBOX_FROM_WALL_FT = 4  # 680.24(A)(2)(b): >= 1.2 m (4 ft) from the inside wall unless behind a barrier
POOL_RECEPTACLE_MIN_FT = 6  # 680.22(A)(3): receptacles >= 1.83 m (6 ft) from the inside wall
POOL_RECEPTACLE_GFCI_FT = 20  # 680.22(A)(4): 125-250 V receptacles within 6.0 m (20 ft) are GFCI
POOL_LUMINAIRE_MIN_FT = 5  # 680.22(B)(1): no new luminaire within 1.5 m (5 ft) horizontally ...
POOL_LUMINAIRE_MIN_HEIGHT_FT = 12  # ... unless 3.7 m (12 ft) or more above the maximum water level
POOL_EGC_MIN_AWG = "12"  # 680.21(A)(1), 680.23(F)(2): insulated copper EGC, 12 AWG or larger
# Table 680.9(A) (680.8 before 2020): overhead clearance in any direction to the
# water, the edge of the water surface or a diving structure, 0-750 V.
OVERHEAD_CLEARANCE_FT = {
    "insulated_cable_on_messenger": 22.5,  # 6.9 m, includes a grounded neutral
    "other_conductors": 25.0,  # 7.5 m, all other conductors 0-15 kV
    "communications": 10.0,  # 3.0 m (680.9(C))
}
