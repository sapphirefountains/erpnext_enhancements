# `engine/controls/` — controller and pump control design

Phase L, PR 2 of the Fountain Designer rebuild (v1.764.0). Nikolas, 2026-11-01: the electrical
phase must also help design the controller and the pump control scheme, including a VFD with
bypass.

## Where the code lives

- **`__init__.py`** holds the old `engine/controls.py`, kept whole.
  - `calc_lighting`, `calc_solenoid_relays` and `lighting_sizing` keep their import paths.
  - So do the seeded `DEFAULT_INTERLOCKS`, `DEFAULT_IO_POINTS`, `SPA_INTERLOCKS` and `SPA_IO_POINTS`.
- **`controls_report(model)`** at the end of the same file runs the whole chain for phase E's `derive()`.
- **Rules:** stdlib only, and nothing imports `frappe` or `engine.hydraulics`. `tests/test_controls_design.py` checks both.
- **Results:** every public function returns a `CalcResult` with its citations.

## Modules

| Module | What it does | Source |
|---|---|---|
| `topology.py` | One control scheme per pump: across-the-line starter, soft starter, VFD, or VFD with manual bypass. Also `recommend_topology` with its reasons, `nema_size`, `iec_contactor`, `overload_relay`, `soft_starter`, `bypass_logic` and `bypass_truth_table` | NEMA ICS 2; NEC 430.32(A)(1); IEC 60947-4-1 AC-3; UL 508A and drive-maker bypass practice |
| `vfd.py` | `vfd_frame`: HP class after the derates, input fuses, heat loss, and the bypass contactors. See the derates below | Drive-maker derates (labeled typical); NEC 430.122 and 430.130; NEMA MG 1 Part 31 |
| `controller.py` | `io_list` (Control IO Point rows), `relay_counts`, `dc_supply` (24 V DC), `select_controller` (Splash Wizard part numbers or Industrial) and `interlocks` | DOC-0028, DOC-0062, DOC-0123, DOC-0126, DOC-0127 |
| `panel.py` | `panel_control_transformer`, `enclosure_size`, `enclosure_type`, `enclosure_thermal`, `panel_heat`, `sccr`, `main_disconnect`, `terminals_and_wire` and `surge_protection` | UL 508A Supplement SB; NEC 409.22, 430.109, 430.110, 680.14; NEMA 250; IEC/TR 60890 |
| `outputs.py` | `panel_bom`, `control_panel_rows` and `theory_of_operation` | Control Panel Design child tables |
| `drawings.py` | `power_state` and `ladder_rungs`, the states that `engine/drawings.power_schematic_svg` and `control_ladder_svg` draw | — |

Reference data is in [`../data/motor_control.py`](../data/motor_control.py). Two kinds of data
are mixed there:

- **Published standards:** the NEMA sizes and the UL 508A SB4.1 default ratings.
- **Typical figures, each labeled:** IEC frames, overload ranges, drive derates, footprints and heat losses.

### The drive derates in `vfd.py`

- **Altitude:** 1% per 330 ft above 3,300 ft. This applies to nearly every Sapphire job, because the Wasatch Front sits at 4,300 to 5,000 ft.
- **Ambient:** 2% per °C above 40 °C.
- **Single-phase input:** twice the current.

## How the recommender chooses

| Need | Scheme |
|---|---|
| The pump needs speed control: it is specified with a VFD, it has wind stages (DOC-0123), the show modulates it, or it holds a setpoint | VFD |
| That, plus a circulation or filtration pump, a regulated venue, or 7.5 HP or more | VFD with manual bypass |
| No speed need, and 15 HP or more | Soft starter |
| No speed need, 5 HP or more, on generator power | Soft starter |
| Anything else | Across-the-line starter |

Wind and show duty apply to feature pumps, not to circulation pumps.

## VFD with bypass

The contactors:

- **K1, line contactor:** ahead of the drive. It isolates the drive in bypass.
- **K2, output contactor:** after the drive.
- **K3, bypass contactor:** straight across the line to the motor, with an overload in its path.

The interlock rules:

- **K2 and K3 never close together.** They are mechanically and electrically interlocked.
- **E-stop or OFF stops the motor.**
- **A drive fault with auto transfer moves a VFD selection to bypass.**

`bypass_truth_table()` walks all 192 states and checks these rules.

Automatic transfer starts the pump across the line at full speed. The result warns about water
hammer and overspray, and keeps the feature to circulation and filtration pumps.

## The Splash Wizard (what the sources say)

- **Part numbers (DOC-0028):**

  | Family | 6 valves | 9 valves |
  |---|---|---|
  | Basic | 500624 | 500625 |
  | PLUS | 500626 | 500627 |
  | MAX | 500628 | 500629 |
  | Valve expansion | 500630 | 500631 |

  The WP-40 housing is box 500615 and lid 500616.
- **Platform (DOC-0062):** Basic and PLUS are listed identically: EDP001 (an Arduino MEGA 2560 in a WP-40) plus EDP002 Power Relay.
- **MAX:** an empty template, labeled FCA002, the same code as PLUS.
- **Functions (SDP001):** valve sequencing, level and fill, feature and circulation pump control, run window, push button, time of day, and an LCD with 4 buttons.

The selector works from those facts:

- **The valve count picks the part.** Past nine valves, expansions are added.
- **The Basic / PLUS / MAX tier is the designer's choice:** `control.family`, Basic by default.
- **Industrial (PLC) is recommended when the design needs something SDP001 does not list,** and the reasons say what:
  - a drive speed reference
  - DMX
  - 4-20 mA inputs
  - automatic bypass transfer

## Open questions

- **The tiers.** What separates Basic, PLUS and MAX? Is MAX FCA003? Can a Splash Wizard put out a 0-10 V speed reference?
- **The screen.** Does the shipping Splash Wizard use an LCD with 4 buttons (DOC-0062), or the Nextion HMI the DocType defaults to?
- **Panel enclosures.** IDP009 labels Saginaw SCE-24EL2410LP and SCE-30EL2410LP as panel enclosures. Their sizes are read from Saginaw's numbering, not from a datasheet.
- **Available fault current.** It is needed at each site for the SCCR check. Without it the check reports `warn`.
