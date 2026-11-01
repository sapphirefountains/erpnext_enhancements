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

The ladder implements the same logic with these rungs:

- **K1** is energized while VFD is selected and there's no transfer.
- **DR**, the drive run command, opens on a drive fault.
- **K2** holds through a time-delay-off (the drive's deceleration time) after DR drops, so it opens with the drive stopped. A drive fault opens it at once.
- **K3** closes on BYPASS, or on a fault with transfer enabled, through a normally closed K2 contact.

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
- **A drive's speed is set by preset speeds** over a digital output: the DOC-0123 "VFDs ramp to windy speed" step, which the Splash Wizard family already describes. Wind control alone therefore keeps the Splash Wizard.
- **Industrial (PLC) is recommended when the design needs something SDP001 does not list,** and the reasons say what:
  - an analog (0-10 V) speed reference, for show modulation or a setpoint
  - DMX
  - 4-20 mA inputs
  - automatic bypass transfer

## `controls_report(model)`: what it returns

One JSON-ready dict:

| Key | What it holds |
|---|---|
| `pumps` | For each motor circuit, `topology.pump_control`'s value: tag, key, label, role, hp, submersible, topology, components (each with its amps), contactor and its basis, overload `{range, setting_a, class, max_trip_a}`, `speed_reference` (`preset` / `analog` / `None`), starting current, interlocks, `flc_a`, `fla_a` |
| `vfds` | Per drive: `{tag, frame, voltage_class, phase_in, input_fuse_a, loss_w, line_reactor, lead_treatment, bypass_contactor_a}` |
| `controller` | `{platform, family, part_no, valve_channels, expansions, housing, reasons}` |
| `io_points` | Control IO Point rows, plus `powered` |
| `relays` | `{ssr, interposing, spare, total}` |
| `dc_supply` | `{watts, units, load_w}` |
| `interlocks` | Control Interlock rows |
| `panel` | `control_transformer_va`, enclosure size, dims and part, `nema_type`, `heat_w`, `thermal`, `sccr_ka` and `sccr_status`, `main_disconnect_a`, `spd`, `terminals`, `gfci_poles` |
| `bom` | Lines of `{section: "Controls", key, description, qty, uom, item_hint, part_no}` |
| `control_panel` | `{parent, pumps, lights, fuses, interlocks, io_points}`, in the Control Panel Design fieldnames |
| `theory_of_operation` | Draft text for the submittal |
| `power_svg`, `ladder_svg` | The two drawings |
| `warnings`, `status`, `results` | Warnings; `good`, `warn` or `bad`; and every CalcResult behind the report, as dicts |

`panel.thermal` is `{status, rise_f, fan_cfm, ac_btu_h, hx_w_per_f}`.

The `control` keys it reads:

- `family`, `topology`, `auto_transfer`, `nema_starters`, `analog_monitoring`
- `wind_control`, `show_modulation`
- `location`, `shaded`, `near_chemicals`, `panel_ambient_f`
- `available_fault_ka`, `component_ka` (marked kA ratings by kind), `application_type`

## Open questions

- **The tiers.** What separates Basic, PLUS and MAX? Is MAX FCA003? Can a Splash Wizard put out a 0-10 V speed reference?
- **The screen.** Does the shipping Splash Wizard use an LCD with 4 buttons (DOC-0062), or the Nextion HMI the DocType defaults to?
- **Panel enclosures.** IDP009's drawing labels name Saginaw SCE-24EL2410LP and SCE-30EL2410LP panels. Neither has a part number or a datasheet behind it, so neither is selected.
  - The only enclosure part the engine attaches is the Polycase YH-161407 backplane (500656), and its NEMA rating isn't recorded, so the result asks the designer to confirm it.
  - DC-34P (500109) is a 4.6 x 3.1 x 1.8 in box, too small to be a panel enclosure. Its vendor number reads DC-34B.
- **Available fault current.** It is needed at each site for the SCCR check. Without it the check reports `warn`.
