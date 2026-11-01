"""Motor-control and control-panel reference data for ``engine/controls/``.

The NEMA starter sizes (NEMA ICS 2) are a published standard. Everything else
here is a typical manufacturer figure, labeled as one where it is defined: IEC
contactor frames, overload dial ranges, drive derates, heat losses and component
footprints differ by maker, and the selected maker's catalog replaces them. UL
508A Supplement SB defaults are as published for the 2018 edition and later.
"""

from __future__ import annotations

# --- NEMA ICS 2 starter sizes ---------------------------------------------------
# Maximum motor HP per NEMA size for across-the-line starting, and the continuous
# ampere rating of the size.
NEMA_SIZES = ["00", "0", "1", "1P", "2", "3", "4", "5", "6"]
NEMA_CONTINUOUS_A = {"00": 9, "0": 18, "1": 27, "1P": 36, "2": 45, "3": 90, "4": 135, "5": 270, "6": 540}
# Single-phase: max HP at 115 V and 230 V.
NEMA_MAX_HP_1PH = {
    "00": {115: 1 / 3, 230: 1.0},
    "0": {115: 1.0, 230: 2.0},
    "1": {115: 2.0, 230: 3.0},
    "1P": {115: 3.0, 230: 5.0},
    "2": {115: 3.0, 230: 7.5},
    "3": {115: 7.5, 230: 15.0},
}
# Three-phase: max HP at 200 V, 230 V and 460/575 V.
NEMA_MAX_HP_3PH = {
    "00": {200: 1.5, 230: 1.5, 460: 2.0},
    "0": {200: 3.0, 230: 3.0, 460: 5.0},
    "1": {200: 7.5, 230: 7.5, 460: 10.0},
    "2": {200: 10.0, 230: 15.0, 460: 25.0},
    "3": {200: 25.0, 230: 30.0, 460: 50.0},
    "4": {200: 40.0, 230: 50.0, 460: 100.0},
    "5": {200: 75.0, 230: 100.0, 460: 200.0},
    "6": {200: 150.0, 230: 200.0, 460: 400.0},
}
CIT_NEMA = "NEMA ICS 2 (starter sizes by HP and voltage)"

# --- IEC contactors -------------------------------------------------------------
# AC-3 (squirrel-cage motor, start and run) rated operational current frames (A).
# Typical of ABB AF, Schneider TeSys D/F and Siemens 3RT; check the maker's own
# HP table, which is what a UL listing uses.
IEC_AC3_FRAMES_A = [9, 12, 18, 25, 32, 40, 50, 65, 80, 95, 115, 150, 185, 225, 265, 300, 400, 500]
CIT_IEC = "IEC 60947-4-1 utilization category AC-3 (typical frame ratings)"

# --- overload relays ------------------------------------------------------------
# Adjustable thermal / electronic overload dial ranges (A), typical of the IEC
# makers. The dial is set to the motor nameplate FLA; the relay's own trip curve
# supplies the 115-125% margin NEC 430.32(A)(1) allows.
OVERLOAD_RANGES_A = [
    (0.63, 1.0), (1.0, 1.6), (1.6, 2.5), (2.5, 4.0), (4.0, 6.0), (5.5, 8.0), (7.0, 10.0), (9.0, 13.0),
    (12.0, 18.0), (17.0, 25.0), (23.0, 32.0), (30.0, 40.0), (37.0, 50.0), (48.0, 65.0), (55.0, 70.0),
    (63.0, 80.0), (80.0, 104.0), (95.0, 120.0), (110.0, 150.0),
]
CIT_OVERLOAD = "NEC 430.32(A)(1) (overload at 125% of nameplate FLA, SF >= 1.15; 115% otherwise)"

# --- soft starters ----------------------------------------------------------------
SOFT_STARTER_FRAMES_A = [9, 12, 16, 22, 30, 37, 45, 60, 72, 85, 105, 142, 175, 210, 250, 300]

# --- drives ---------------------------------------------------------------------
# Drive rated output current by HP class: the NEC 430.250 FLC of the low end of
# each voltage class (208 V for the 200-240 V class), which is how drive catalogs
# are rated (a drive "for 5 HP at 230 V" carries ~16.7 A).
VFD_CLASS_COLUMN = {"200-240": 208, "380-480": 460, "525-600": 575}
ALTITUDE_DERATE_START_FT = 3300  # 1000 m
ALTITUDE_DERATE_PCT_PER_330FT = 1.0  # 1% per 100 m above 1000 m (typical: ABB, Yaskawa, Danfoss)
AMBIENT_DERATE_START_F = 104  # 40 C
AMBIENT_DERATE_PCT_PER_C = 2.0  # typical: ~2% per C above 40 C, to 50 C
AMBIENT_MAX_F = 122  # 50 C: most drives are not rated above it
VFD_LOSS_PCT = 3.0  # drive losses, % of output kW (typical 2.5-4%)
CARRIER_DEFAULT_KHZ = 4.0
CIT_VFD_DERATE = "Drive-manufacturer derates (altitude above 1000 m, ambient above 40 C); not NEC"

# --- control power ------------------------------------------------------------------
# DIN-rail 24 V DC supplies (W): 1.25, 2.5, 3.2, 5, 10, 20, 40 A.
PSU_24VDC_W = [30, 60, 75, 120, 240, 480, 960]
PSU_EFF = 0.88
# Nominal 24 V DC draws for sizing (W), to be replaced by the device datasheets.
DC_DEVICE_W = {
    "ssr_input": 0.3,  # solid-state relay input (~12 mA)
    "relay_coil": 0.6,  # interposing relay coil
    "sensor": 1.0,  # powered level / flow / pressure switch
    "transmitter": 0.6,  # 4-20 mA loop at 24 V
    "anemometer": 0.5,
    "pilot_light": 0.5,  # LED pilot
}

# --- enclosures ---------------------------------------------------------------------
# Standard wall-mount enclosures, inches (H, W, D); the backplane is about 2 in
# smaller each way.
ENCLOSURE_SIZES_IN = [
    (12, 10, 6), (16, 12, 6), (16, 14, 7), (20, 16, 8), (24, 20, 8), (24, 24, 10), (30, 24, 10),
    (36, 30, 12), (42, 36, 12), (48, 36, 12), (60, 36, 16), (72, 36, 16),
]
# Sapphire parts for the sizes they fit. Only the Polycase YH-161407 backplane
# (500656) is in DOC-0028 / the source audit; the enclosure itself has no Sapphire
# part number and its NEMA rating is not recorded, so the result asks the designer
# to confirm it against the type the location needs. (IDP009's drawing labels name
# Saginaw SCE-24EL2410LP / SCE-30EL2410LP panels; with no datasheet or part number
# behind them they are listed as an open question, not selected.)
ENCLOSURE_PARTS = {
    (16, 14, 7): {"enclosure": "Polycase YH-161407", "part_no": None,
                  "backplane": "Backplane, Polycase YH-161407", "backplane_part_no": "500656",
                  "note": "NEMA rating not recorded in DOC-0028: confirm it matches the location's type"},
}
SPLASH_WIZARD_HOUSING = {"box": "500615", "lid": "500616", "label": "WP-40"}  # DOC-0028, EDP001
WIREWAY_FACTOR = 0.30  # wire duct and spacing around the components
SPARE_FACTOR = 0.20  # 20% spare backplane
# Backplane footprint (in^2) of common components, typical.
FOOTPRINT_IN2 = {
    "breaker_pole": 3.0,  # 0.7 in x 3.5 in DIN breaker, per pole, with its share of rail
    "contactor_small": 9.0,  # to ~40 A
    "contactor_large": 24.0,  # to ~95 A
    "overload": 6.0,
    "soft_starter": 30.0,
    "terminal": 0.6,  # 0.25 in x 2.4 in feed-through block
    "relay": 1.5,  # slim interposing relay or SSR
    "psu_per_100w": 8.0,
    "xfmr_per_100va": 12.0,
    "controller": 60.0,  # controller board or PLC rack and its I/O
    "spd": 9.0,
    "disconnect": 30.0,
}
VFD_FOOTPRINT_IN2 = [(2, 45.0), (5, 60.0), (10, 90.0), (20, 140.0), (40, 220.0), (75, 360.0), (150, 600.0)]
# Natural convection: heat an enclosure sheds per unit exposed area per degree.
ENCLOSURE_K_W_PER_M2K = {"steel": 5.5, "stainless": 3.7, "fiberglass": 3.5, "polycarbonate": 3.5}
IN2_PER_M2 = 1550.0031
MAX_INTERNAL_F = 104  # 40 C: the common component rating inside the enclosure
SOLAR_GAIN_F = 15  # outdoor enclosure in sun: add to the ambient (typical allowance)
CFM_PER_W_PER_F = 3.17  # filtered fan CFM = 3.17 x watts / allowed rise (deg F)
CIT_THERMAL = "IEC/TR 60890 method (k = 5.5 W/m2K painted steel); fan CFM = 3.17 x W / dT (enclosure makers)"

# --- UL 508A Supplement SB ------------------------------------------------------------
# Table SB4.1 default short-circuit current ratings (kA) for unmarked components.
SB4_DEFAULT_KA = {
    "motor_controller_0_50hp": 5,
    "motor_controller_51_200hp": 10,
    "overload_relay": 5,
    "terminal_block": 10,
    "switch": 10,
    "power_supply": 5,
    "vfd": 5,
    "miniature_breaker_supplementary": 0,  # supplementary protectors carry no SCCR of their own
    # Branch breakers are marked with an interrupting rating (UL 489); 10 kA is the common
    # minimum, used only when the model gives none (control.component_ka.branch_breaker).
    "branch_breaker": 10,
}
CIT_SCCR = "UL 508A Supplement SB (SCCR: the lowest-rated power component governs); NEC 409.22, 409.110"

# --- NEMA enclosure types ----------------------------------------------------------------
ENCLOSURE_TYPE_BY_LOCATION = {
    "outdoor": "NEMA 3R",
    "outdoor_washdown": "NEMA 4",
    "chemical": "NEMA 4X",
    "pool_equipment_room": "NEMA 4X",
    "indoor": "NEMA 12",
    "indoor_clean": "NEMA 1",
}
CIT_ENCLOSURE_TYPE = "NEMA 250 enclosure types; NEC 680.14 (corrosive environment)"
