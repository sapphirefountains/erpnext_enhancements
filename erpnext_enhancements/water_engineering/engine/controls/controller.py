"""The controller: Splash Wizard selection, the I/O list generated from the
design, relay and SSR counts, the 24 V DC supply, and the interlocks.

What the sources say about the Splash Wizard (read 2026-11-01):

* DOC-0028 numbers the controllers by family and valve count. Basic is 500624
  (6 valves) and 500625 (9 valves); PLUS is 500626 / 500627; MAX is 500628 / 500629.
  The valve expansions are 500630 (6 valves) and 500631 (9 valves). The housing is
  the WP-40 box 500615 with lid 500616.
* DOC-0062 gives Basic and PLUS the same platform: EDP001, an Arduino MEGA 2560 in
  a WP-40, plus EDP002 Power Relay. MAX is an empty template, and is mislabeled
  FCA002. SDP001 lists what the software does: 4-button UI and LCD, time of day,
  valve sequencing (patterns, random), water level monitoring and fill, feature
  and circulation pump control, daily run window, and push-button activation.
* The training material says the Splash Wizard puts out 24 V DC to the solenoids.

So the valve count picks the part, and the Basic / PLUS / MAX tier is the
designer's call (``control.family``, Basic by default): no source says what
separates the tiers. A design that needs something outside SDP001's list gets the
Industrial (PLC) platform recommended. That covers a drive speed reference, DMX,
4-20 mA analog inputs and automatic bypass transfer; the reasons say which.
"""

from __future__ import annotations

import math

from ..data.motor_control import DC_DEVICE_W, PSU_24VDC_W, PSU_EFF, SPLASH_WIZARD_HOUSING
from ..electrical._util import EPS, num
from ..envelope import CalcOption, CalcResult, make_input

CIT_DOC_0028 = "DOC-0028 Design Part Numbers (500615-500631)"
CIT_DOC_0062 = "DOC-0062 Design Architectures & Platforms (FCA001/FCA002, EDP001, SDP001)"
CIT_DOC_0126 = "DOC-0126 / Control Panel Submittal (one SSR per valve; 60 W per 12 V DC lighting relay)"
SPLASH_WIZARD_PARTS = {
    "Basic": {6: "500624", 9: "500625"},
    "PLUS": {6: "500626", 9: "500627"},
    "MAX": {6: "500628", 9: "500629"},
}
VALVE_EXPANSION_PARTS = {6: "500630", 9: "500631"}
ERPNEXT_ITEMS = {"Basic": "PDT-0000", "PLUS": "PDT-0001", "MAX": "PDT-0002", "Industrial": "PDT-0003"}
SDP001_FUNCTIONS = (
    "4-button UI and LCD", "time of day", "valve sequencing (patterns, random)", "water level monitoring and fill",
    "feature and circulation pump control", "daily run window", "push-button activation",
)
CONTROLLER_W = {"Splash Wizard": 5.0, "Industrial": 16.0}  # board + display; PLC + HMI (estimates)
DMX_CHANNELS_PER_FIXTURE = 4  # RGBW
DMX_UNIVERSE = 512
SPARE_IO = 0.20


def _io(name, io_type, signal, device, description, qty=1, mechanism="", ref="", powered=False):
    """One Control IO Point row. ``powered`` marks a sensor that draws from the
    24 V DC supply (a float or flow switch, not an E-stop contact); it is not a
    DocType field and ``outputs.control_panel_rows`` drops it."""
    return {
        "point_name": name, "io_type": io_type, "signal": signal, "device": device,
        "description": description, "qty": int(qty), "control_mechanism": mechanism, "drawing_ref": ref,
        "powered": bool(powered),
    }


def io_list(model: dict | None, pump_controls: list[dict] | None = None) -> CalcResult:
    """The controller I/O list, as Control IO Point rows (point_name, io_type,
    signal, device, description, qty, control_mechanism, drawing_ref).

    ``pump_controls`` are ``topology.pump_control`` values; without them every
    pump counts as a starter. Each pump gets a run output, a fault input and,
    on a drive, a speed reference."""
    model = model or {}
    elec = model.get("electrical") or {}
    treatment = model.get("treatment") or {}
    site = model.get("site") or {}
    lighting = model.get("lighting") or {}
    pcs = pump_controls or []
    if not pcs:
        for i, p in enumerate((model.get("network") or {}).get("pumps") or [], start=1):
            pcs.append({"tag": f"M{i}", "label": p.get("label") or f"Pump {i}",
                        "topology": "vfd" if p.get("control") == "vfd" else "across_the_line"})
    rows: list[dict] = []
    for pc in pcs:
        tag, label, topo = pc.get("tag"), pc.get("label"), pc.get("topology")
        drive = topo in ("vfd", "vfd_bypass")
        rows.append(_io(f"{tag} run", "Output", "24VDC", f"{label} {'drive run' if drive else 'contactor'}",
                        f"Run command to {label}", 1, "VFD" if drive else "Motor Starter", tag))
        rows.append(_io(f"{tag} fault", "Input", "Dry Contact NO", f"{label} {'drive fault' if drive else 'overload aux'}",
                        f"{label} fault / overload", 1, "", tag))
        if drive:
            rows.append(_io(f"{tag} speed", "Output", "0-10V", f"{label} drive speed reference",
                            f"Speed reference to {label}", 1, "VFD", tag))
        if topo == "vfd_bypass":
            rows.append(_io(f"{tag} in bypass", "Input", "Dry Contact NO", f"{label} K3 aux",
                            f"{label} running in bypass", 1, "", tag))
    basins = model.get("basins") or []
    n_level = max(1, len(basins)) if (basins or pcs) else 0
    if n_level:
        rows.append(_io("Water level low", "Input", "Dry Contact NO", "Float switch / level controller",
                        "Low-water cutoff per basin", n_level, powered=True))
    autofill = elec.get("autofill", bool(basins))
    if autofill:
        rows.append(_io("Water level fill", "Input", "Dry Contact NO", "Float switch / level controller",
                        "Fill call per basin", max(1, len(basins)), powered=True))
        rows.append(_io("Autofill valve", "Output", "24VDC", "Autofill solenoid", "Opens the fill valve", 1,
                        "Solid State Relay"))
    features = model.get("features") or []
    if any(f.get("wind_control") or (f.get("inputs") or {}).get("wind_control") for f in features) \
            or elec.get("wind_sensor"):
        rows.append(_io("Wind sensor", "Input", "Reed Switch Pulse", "Anemometer (Inspeed WS, DOC-0062)",
                        "Wind speed: medium and high setpoints (two stages, DOC-0123)", 1))
    if pcs:
        rows.append(_io("E-stop", "Input", "Dry Contact NC", "E-stop pushbutton", "All stop", 1))
    circ = [pc for pc in pcs if (pc.get("role") or "") in ("circulation", "filtration")] or pcs[:1]
    if circ:
        rows.append(_io("Circulation flow proof", "Input", "Dry Contact NO", "Flow or pressure switch (IFM SI5004 class)",
                        "Proves circulation before feeders, heater and features", 1, powered=True))
    regulated = bool(site.get("regulated"))
    # Analog readbacks only on request: the heater keeps its own thermostat and a standalone
    # chemical controller (the Fika spa's) reports to the panel by a status contact.
    analog = bool((model.get("control") or {}).get("analog_monitoring"))
    if analog and (treatment.get("heater_kw") or regulated):
        rows.append(_io("Water temperature", "Input", "4-20mA", "Temperature transmitter", "Water temperature", 1,
                        powered=True))
    if treatment.get("chem_feed", regulated):
        rows.append(_io("Chemical controller status", "Input", "Dry Contact NO", "Chemical controller (ORP/pH)",
                        "Controller ok / fault", 1))
        if analog:
            rows.append(_io("ORP / pH", "Input", "4-20mA", "Chemical controller analog out", "ORP and pH readback", 2))
    n_valves = int(num((elec.get("solenoid_valves") or {}).get("qty")))
    if n_valves:
        rows.append(_io("Solenoid valves", "Output", "24VDC", "Manifold solenoid valve", "One output per valve",
                        n_valves, "Solid State Relay"))
    fixtures = lighting.get("fixtures") or []
    lv_w = sum(num(f.get("watts")) * max(1, int(num(f.get("qty"), 1))) for f in fixtures
               if 0 < num(f.get("voltage"), 12) <= 30)
    dmx = (lighting.get("control") or "").lower() == "dmx"
    if dmx:
        channels = sum(max(1, int(num(f.get("qty"), 1))) for f in fixtures) * DMX_CHANNELS_PER_FIXTURE
        universes = max(1, math.ceil(channels / DMX_UNIVERSE))
        rows.append(_io("DMX", "Output", "Digital", "DMX512 port", f"{channels} channels", universes))
    elif lv_w > 0:
        relays = math.ceil(lv_w / 60.0)
        rows.append(_io("Lighting", "Output", "24VDC", "Lighting relay (60 W at 12 V DC each)",
                        "Switched lighting circuits", relays, "Solid State Relay"))
    if pcs:
        rows.append(_io("Push-button activation", "Input", "Dry Contact NO", "Illuminated pushbutton (IFM KT6301 class)",
                        "Starts a show / run window", 1, powered=True))
    ins = sum(r["qty"] for r in rows if r["io_type"] == "Input")
    outs = sum(r["qty"] for r in rows if r["io_type"] == "Output")
    analog = sum(r["qty"] for r in rows if r["signal"] in ("4-20mA", "0-10V"))
    return CalcResult(
        calc="io_list",
        value=rows,
        unit="points",
        inputs={
            "inputs": make_input(ins, "", "prior_calc"),
            "outputs": make_input(outs, "", "prior_calc"),
            "analog": make_input(analog, "", "prior_calc"),
            "with_spare": make_input(math.ceil((ins + outs) * (1 + SPARE_IO)), "", "prior_calc", "20% spare"),
        },
        formula="per pump: run, fault (+ speed, + bypass status) ; level, fill, wind, E-stop, flow, temperature, "
        "chemistry, valves, lighting / DMX, push button",
        steps=[f"{r['point_name']}: {r['qty']} x {r['io_type']} {r['signal']}" for r in rows],
        citations=[CIT_DOC_0126, CIT_DOC_0062, "DOC-0123 (wind control, two setpoints)"],
    )


def relay_counts(io_rows: list[dict] | None) -> CalcResult:
    """Relays the controller drives: an SSR per solenoid valve, per lighting
    circuit and for the autofill (DOC-0126), and an interposing relay per pump run
    command, plus 20% spare."""
    rows = io_rows or []
    ssr = sum(r["qty"] for r in rows if r.get("control_mechanism") == "Solid State Relay")
    interposing = sum(r["qty"] for r in rows if r["io_type"] == "Output" and r.get("signal") == "24VDC"
                      and r.get("control_mechanism") in ("Motor Starter", "VFD"))
    spare = math.ceil((ssr + interposing) * SPARE_IO)
    return CalcResult(
        calc="relay_counts",
        value={"ssr": ssr, "interposing": interposing, "spare": spare, "total": ssr + interposing + spare},
        unit="relays",
        inputs={"ssr": make_input(ssr, "", "prior_calc"), "interposing": make_input(interposing, "", "prior_calc")},
        formula="SSR = valves + lighting circuits + autofill ; interposing = pump run outputs ; +20% spare",
        steps=[f"{ssr} SSR + {interposing} interposing + {spare} spare = {ssr + interposing + spare}"],
        citations=[CIT_DOC_0126],
    )


def dc_supply(
    io_rows: list[dict] | None,
    *,
    valve_w_each: float = 10.0,
    platform: str = "Splash Wizard",
    pilot_lights: int = 0,
) -> CalcResult:
    """24 V DC supply: every valve coil open at once, SSR inputs, interposing
    relay coils, powered sensors and transmitters, pilot lights and the
    controller, x 1.25 headroom, divided by the supply efficiency for the AC
    input, rounded up to a standard DIN-rail size."""
    rows = io_rows or []
    valves = sum(r["qty"] for r in rows if r["point_name"] in ("Solenoid valves", "Autofill valve"))
    ssr = sum(r["qty"] for r in rows if r.get("control_mechanism") == "Solid State Relay")
    relays = sum(r["qty"] for r in rows if r["io_type"] == "Output" and r.get("control_mechanism") in ("Motor Starter", "VFD")
                 and r.get("signal") == "24VDC")
    loops = sum(r["qty"] for r in rows if r["signal"] == "4-20mA")
    sensors = sum(r["qty"] for r in rows if r.get("powered") and r["signal"] != "4-20mA")
    anemo = sum(r["qty"] for r in rows if r["signal"] == "Reed Switch Pulse")
    parts = {
        "valve coils": valves * num(valve_w_each, 10.0),
        "SSR inputs": ssr * DC_DEVICE_W["ssr_input"],
        "relay coils": relays * DC_DEVICE_W["relay_coil"],
        "sensors": sensors * DC_DEVICE_W["sensor"],
        "4-20 mA loops": loops * DC_DEVICE_W["transmitter"],
        "anemometer": anemo * DC_DEVICE_W["anemometer"],
        "pilot lights": pilot_lights * DC_DEVICE_W["pilot_light"],
        "controller": CONTROLLER_W.get(platform, CONTROLLER_W["Splash Wizard"]),
    }
    load = sum(parts.values())
    need = 1.25 * load
    size = next((s for s in PSU_24VDC_W if s >= need - EPS), None)
    units = 1 if size else math.ceil(need / PSU_24VDC_W[-1])
    size = size or PSU_24VDC_W[-1]
    return CalcResult(
        calc="dc_supply",
        value=size,
        unit="W",
        inputs={
            "load_w": make_input(round(load, 1), "W", "prior_calc"),
            "required_w": make_input(round(need, 1), "W", "prior_calc", "1.25 x load"),
            "units": make_input(units, "", "prior_calc"),
            "amps_24v": make_input(round(size * units / 24.0, 2), "A", "prior_calc"),
            "ac_input_w": make_input(round(size * units / PSU_EFF, 1), "W", "prior_calc", f"{PSU_EFF:g} efficiency"),
            "breakdown_w": make_input({k: round(v, 2) for k, v in parts.items()}, "W", "prior_calc"),
        },
        formula="W = 1.25 x (valves x W + SSR + relay coils + sensors + loops + pilots + controller) ; standard size",
        steps=[f"{k}: {v:.1f} W" for k, v in parts.items() if v] + [f"1.25 x {load:.1f} = {need:.1f} W -> "
                                                                  f"{units} x {size} W"],
        citations=[CIT_DOC_0126, "Splash Wizard 24 V DC solenoid output (technician training, module 6)"],
        warnings=["Per-device draws are nominal; replace them with the datasheets (valve coils above all)."],
    )


def select_controller(
    io_rows: list[dict] | None,
    *,
    family: str | None = None,
    pump_controls: list[dict] | None = None,
) -> CalcResult:
    """Controller platform and part numbers. A Splash Wizard (Basic by default,
    or ``family``) by valve count with valve expansions past nine, unless the
    design needs something SDP001 does not list, in which case Industrial (PLC)
    is recommended and the Splash Wizard stays an option. ``value`` is a dict:
    platform, family, part_no, expansions, erpnext_item, reasons."""
    rows = io_rows or []
    pcs = pump_controls or []
    valves = sum(r["qty"] for r in rows if r["point_name"] == "Solenoid valves")
    reasons = []
    if any(r["signal"] == "0-10V" for r in rows):
        reasons.append("a drive speed reference (0-10 V) is not among SDP001's functions")
    if any(r["point_name"] == "DMX" for r in rows):
        reasons.append("DMX lighting is not among SDP001's functions")
    if any(r["signal"] == "4-20mA" for r in rows):
        reasons.append("4-20 mA analog inputs (temperature, ORP / pH) are not among SDP001's functions")
    if any(i.get("action", "").startswith("Transfer") for pc in pcs for i in pc.get("interlocks", [])):
        reasons.append("automatic transfer to bypass needs fault-transfer logic")
    fam = family if family in SPLASH_WIZARD_PARTS else "Basic"
    base = 6 if valves <= 6 else 9
    rem = max(0, valves - base)
    expansions = []
    while rem > 0:
        e = 6 if rem <= 6 else 9
        expansions.append(e)
        rem -= e
    sw = {
        "platform": "Splash Wizard",
        "family": fam,
        "valve_channels": base + sum(expansions),
        "part_no": SPLASH_WIZARD_PARTS[fam][base],
        "expansions": [{"valves": e, "part_no": VALVE_EXPANSION_PARTS[e]} for e in expansions],
        "housing": SPLASH_WIZARD_HOUSING,
        "erpnext_item": ERPNEXT_ITEMS[fam],
    }
    industrial = {"platform": "Industrial", "family": "Industrial", "part_no": None, "expansions": [],
                  "erpnext_item": ERPNEXT_ITEMS["Industrial"], "valve_channels": valves}
    chosen = dict(industrial if reasons else sw)
    chosen["reasons"] = reasons or [f"{valves} valve(s) and the I/O fit SDP001's functions"]
    options = [
        CalcOption(key="splash_wizard", label=f"Splash Wizard {fam} ({base} valves"
                   + (f" + {len(expansions)} expansion(s))" if expansions else ")"), value=sw, recommended=not reasons),
        CalcOption(key="industrial", label="Splash Wizard Industrial (PLC)", value=industrial, recommended=bool(reasons)),
    ]
    warnings = [
        "DOC-0062 does not say what separates Basic, PLUS and MAX; the tier is the designer's pick "
        "(control.family, default Basic). MAX is mislabeled FCA002 in DOC-0062.",
    ]
    if reasons:
        warnings.append("Industrial recommended: " + "; ".join(reasons) + ". Confirm with controls engineering.")
    return CalcResult(
        calc="select_controller",
        value=chosen,
        unit="",
        inputs={
            "valves": make_input(valves, "", "prior_calc"),
            "family": make_input(fam, "", "user" if family else "default"),
        },
        formula="valves <= 6 -> 6-valve, else 9-valve + expansions (6 or 9) ; needs outside SDP001 -> Industrial",
        steps=[f"{valves} valves -> {base}-valve {fam} {SPLASH_WIZARD_PARTS[fam][base]}"
               + (f" + expansions {[e['part_no'] for e in sw['expansions']]}" if expansions else "")]
        + ([f"Industrial: {r}" for r in reasons]),
        citations=[CIT_DOC_0028, CIT_DOC_0062],
        warnings=warnings,
        options=options,
    )


def interlocks(application_type: str = "Fountain", pump_controls: list[dict] | None = None) -> CalcResult:
    """The seeded interlock checklist the DocType holds (DOC-0126/0127; the
    pool/spa set for Pool-Spa) plus each pump's bypass interlocks, as Control
    Interlock rows (condition, action, interlock_method, threshold, enabled,
    notes)."""
    from . import DEFAULT_INTERLOCKS, SPA_INTERLOCKS

    spa = (application_type or "").strip() == "Pool-Spa"
    rows = [dict(r) for r in (SPA_INTERLOCKS if spa else DEFAULT_INTERLOCKS)]
    for pc in pump_controls or []:
        rows += [dict(i) for i in pc.get("interlocks", [])]
    for r in rows:
        r.setdefault("interlock_method", "")
        r.setdefault("threshold", "")
        r.setdefault("notes", "")
        r.setdefault("enabled", 1)
    return CalcResult(
        calc="interlocks",
        value=rows,
        unit="interlocks",
        inputs={"application_type": make_input("Pool-Spa" if spa else "Fountain", "", "user")},
        formula="seeded set (fountain or pool/spa) + per-pump bypass interlocks",
        steps=[f"{r['condition']} -> {r['action']}" for r in rows],
        citations=["DOC-0126 / DOC-0127 interlocks; NEC 680 (pool/spa set)"],
    )


__all__ = ["dc_supply", "interlocks", "io_list", "relay_counts", "select_controller"]
