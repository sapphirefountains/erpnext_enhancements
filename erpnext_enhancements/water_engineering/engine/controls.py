"""Control-panel sizing (Phase 4 — controls / control-panel submittal).

Grounded in DOC-0126 (Control Panel Submittal Template):
* Lighting: a single fused solid-state relay powers up to 60 W at 12 VDC, so
    total_watts = Sum(qty * watts_each)
    current_a   = total_watts / lighting_voltage
    relay_count = ceil(total_watts / per_relay_watts)   [per_relay default 60 W]
* Solenoid valves: each valve is individually controlled by one solid-state
  relay, so the relay count equals the valve quantity.

The interlock checklist + I/O list live on the Control Panel Design DocType; the
control-transformer VA rule is a business rule (not in the source docs) and is a
manual field, not derived here.
"""

from __future__ import annotations

import math

from .envelope import CalcResult, make_input

CIT_PANEL = "DOC-0126 / Control Panel Submittal"

# Default standard interlock checklist (condition -> action), DOC-0126/0127/0123.
# Wind has TWO thresholds (DOC-0123 Wind Control screen): at the medium setpoint
# the VFDs ramp to the windy speed; at the high setpoint the feature pumps stop.
DEFAULT_INTERLOCKS = [
    {"condition": "Circulation pump off", "action": "Inhibit feature pumps", "enabled": 1},
    {"condition": "Water level low", "action": "Stop / inhibit pumps", "enabled": 1},
    {"condition": "Wind above medium setpoint", "action": "VFDs ramp to windy speed", "enabled": 1},
    {"condition": "Wind above high setpoint", "action": "Stop feature pumps", "enabled": 1},
    {"condition": "E-stop pressed", "action": "All stop", "enabled": 1},
    {"condition": "Thermal overload", "action": "Stop affected pump", "enabled": 1},
    {"condition": "Power-up", "action": "All components to safe state (off)", "enabled": 1},
]

# Standard panel inputs (DOC-0126 Inputs / DOC-0123). Seeded as a checklist when a
# panel's I/O list is empty -- delete the ones a given job doesn't include.
DEFAULT_IO_POINTS = [
    {"point_name": "E-stop", "io_type": "Input", "signal": "Dry Contact NC",
     "device": "E-stop button", "description": "Panel emergency stop", "qty": 1},
    {"point_name": "Water level", "io_type": "Input", "signal": "Dry Contact NO",
     "device": "Water level controller", "description": "Low-water cutoff / fill", "qty": 1},
    {"point_name": "Wind sensor", "io_type": "Input", "signal": "Reed Switch Pulse",
     "device": "Anemometer", "description": "Wind speed (medium/high setpoints)", "qty": 1},
]

# Pool/spa interlock + I/O set (NEC 680 + swimming-pool code, per the Fika spa):
# circ<->chem-feeder + circ<->heater (no flow switch) interlocks, the 15-minute
# therapy-timer, the emergency shut-off, and the chemical-controller status. The
# fountain-oriented wind interlocks do not apply. Picked in _seed_defaults when
# application_type is Pool-Spa.
SPA_INTERLOCKS = [
    {"condition": "Circulation pump off / no flow", "action": "Inhibit chlorine + pH feed pumps", "enabled": 1},
    {"condition": "No circulation flow (no flow switch on heater)", "action": "Disable heater", "enabled": 1},
    {"condition": "Therapy-jet timer expired", "action": "Stop therapy (jet) pump", "threshold": "15 min", "enabled": 1},
    {"condition": "Emergency shut-off pressed", "action": "All stop", "enabled": 1},
    {"condition": "Chemical controller fault / out of range", "action": "Inhibit chemical feed", "enabled": 1},
    {"condition": "Thermal overload", "action": "Stop affected pump", "enabled": 1},
    {"condition": "Power-up", "action": "All components to safe state (off)", "enabled": 1},
]

SPA_IO_POINTS = [
    {"point_name": "Emergency shut-off", "io_type": "Input", "signal": "Dry Contact NC",
     "device": "Emergency shut-off switch", "description": "Spa emergency shut-off (all stop)", "qty": 1},
    {"point_name": "Circulation flow proof", "io_type": "Input", "signal": "Dry Contact NO",
     "device": "Flow switch / circ pump aux contact", "description": "Interlocks the feeders + heater", "qty": 1},
    {"point_name": "Therapy-jet timer", "io_type": "Input", "signal": "Dry Contact NO",
     "device": "15-minute spring-wound timer", "description": "Jet-pump run timer (bather exits to reset)", "qty": 1},
    {"point_name": "Chemical controller status", "io_type": "Input", "signal": "Dry Contact NO",
     "device": "Chemical controller (ORP/pH)", "description": "Controller ok / fault", "qty": 1},
    {"point_name": "Therapy pump", "io_type": "Output", "signal": "24VAC",
     "device": "Therapy (jet) pump contactor", "description": "Timed jet-pump output", "qty": 1},
]


def lighting_sizing(lights, lighting_voltage: float = 12.0, per_relay_watts: float = 60.0) -> dict:
    """Plain-dict lighting rollup reused by the controller and calc_lighting."""
    lighting_voltage = float(lighting_voltage) or 12.0
    per_relay_watts = float(per_relay_watts) or 60.0
    total = sum(float(li.get("qty", 0) or 0) * float(li.get("watts_each", 0) or 0) for li in lights or [])
    current = total / lighting_voltage
    relays = math.ceil(total / per_relay_watts) if total > 0 else 0
    return {"total_watts": total, "current_a": current, "relay_count": relays}


def calc_lighting(lights, lighting_voltage: float = 12.0, per_relay_watts: float = 60.0) -> CalcResult:
    """Lighting load + fused-solid-state-relay count (DOC-0126: 60 W @ 12 VDC each)."""
    s = lighting_sizing(lights, lighting_voltage, per_relay_watts)
    warnings = []
    for li in lights or []:
        if float(li.get("watts_each", 0) or 0) > float(per_relay_watts):
            warnings.append(
                f"A single light at {li.get('watts_each')}W exceeds the {per_relay_watts}W "
                "per-relay capacity; it needs its own (or a larger) relay."
            )
    return CalcResult(
        calc="calc_lighting",
        value=s["relay_count"],
        unit="relays",
        inputs={
            "lights": make_input(len(lights or []), "rows", "user"),
            "lighting_voltage": make_input(lighting_voltage, "V", "user", "default 12 VDC"),
            "per_relay_watts": make_input(per_relay_watts, "W", "lookup", "DOC-0126 60W"),
        },
        formula="total_W = Sum(qty*watts) ; current = total_W/V ; relays = ceil(total_W/60)",
        steps=[
            f"total watts = {s['total_watts']:.1f} W",
            f"current = {s['total_watts']:.1f}/{lighting_voltage:g} = {s['current_a']:.2f} A",
            f"relays = ceil({s['total_watts']:.1f}/{per_relay_watts:g}) = {s['relay_count']}",
        ],
        citations=[CIT_PANEL],
        warnings=warnings,
    )


def calc_solenoid_relays(valve_qty: int) -> CalcResult:
    """One solid-state relay per solenoid valve (DOC-0126)."""
    qty = int(valve_qty or 0)
    return CalcResult(
        calc="calc_solenoid_relays",
        value=qty,
        unit="relays",
        inputs={"valve_qty": make_input(qty, "", "user")},
        formula="relay_count = solenoid_valve_qty (one SSR per valve)",
        steps=[f"{qty} valve(s) -> {qty} solid-state relay(s)"],
        citations=[CIT_PANEL],
    )
