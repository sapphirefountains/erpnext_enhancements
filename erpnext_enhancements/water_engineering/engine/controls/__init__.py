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

Since v1.764.0 (phase L, PR 2) this is a package: the module above is kept here
whole, so ``calc_lighting``, ``calc_solenoid_relays``, ``lighting_sizing`` and the
seeded interlock / I/O lists import from the same path. Beside it:
``topology.py`` (starter, soft starter, VFD, VFD with bypass, and the
recommender), ``vfd.py`` (drive frame with derates), ``controller.py`` (Splash
Wizard selection, I/O list, relays, 24 V DC supply, interlocks), ``panel.py``
(control transformer, enclosure, heat, SCCR, disconnect, terminals),
``outputs.py`` (panel BOM, Control Panel Design rows, Theory of Operation) and
``drawings.py`` (power schematic and control ladder states).
``controls_report(model)`` runs the chain; see ``README.md``.
"""

from __future__ import annotations

import math

from ..envelope import CalcResult, make_input

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


# --- phase L, PR 2: the controls package ------------------------------------------------
# Imported after the definitions above: controller.interlocks reads the seeded lists from here.
from ..data.motor_control import MAX_INTERNAL_F
from ..electrical._util import num
from .controller import dc_supply, interlocks, io_list, relay_counts, select_controller, wind_control
from .drawings import control_ladder, ladder_rungs, power_schematic, power_state
from .outputs import control_panel_rows, panel_bom, theory_of_operation
from .panel import (
    enclosure_size,
    enclosure_thermal,
    enclosure_type,
    main_disconnect,
    panel_control_transformer,
    panel_heat,
    sccr,
    surge_protection,
    terminals_and_wire,
)
from .topology import (
    CRITICAL_ROLES,
    bypass_logic,
    bypass_truth_table,
    iec_contactor,
    nema_size,
    overload_relay,
    pump_control,
    recommend_topology,
    soft_starter,
)
from .vfd import vfd_frame


def _feature_for_pumps(model: dict) -> dict:
    """The duty a feature puts on the pumps: wind control or show modulation on
    any feature (the model does not yet tie a pump to one feature)."""
    feats = model.get("features") or []
    ctl = model.get("control") or {}

    def any_flag(key):
        return bool(ctl.get(key)) or any(f.get(key) or (f.get("inputs") or {}).get(key) for f in feats)

    # Wind uses the same predicate as the I/O list, so a pump on wind duty always has its sensor.
    return {"wind_control": wind_control(model), "show_modulation": any_flag("show_modulation"),
            "setpoint": any_flag("setpoint")}


def controls_report(model: dict | None, *, electrical: dict | None = None, pumps: list[dict] | None = None) -> dict:
    """Run the controls chain on a design model (PLAN.md section 3.3) and return
    one JSON-ready dict for ``derive()``. ``electrical`` is an
    ``electrical_report`` result (computed when not given); ``pumps`` overrides
    ``network.pumps`` as there.

    Model keys read, beyond the electrical ones: ``control`` (``family``
    Basic / PLUS / MAX, ``topology`` per pump id, ``auto_transfer``,
    ``wind_control``, ``show_modulation``, ``location`` outdoor / indoor /
    pool_equipment_room, ``near_chemicals``, ``available_fault_ka``,
    ``application_type``, ``nema_starters``, ``analog_monitoring``, ``shaded``,
    ``panel_ambient_f``, ``component_ka`` {kind: marked kA}), pump ``role`` and
    ``topology``, ``site.elevation_ft`` and ``site.air_temp_f``.

    Returns (also in ``README.md``):

    * ``pumps``: per motor circuit, ``topology.pump_control``'s value (tag, key,
      label, role, hp, topology, components, contactor, overload, speed_reference,
      interlocks, flc_a, fla_a, ...)
    * ``vfds``: per drive, ``{tag, frame, voltage_class, phase_in, input_fuse_a,
      loss_w, line_reactor, lead_treatment, bypass_contactor_a}``
    * ``controller``: ``{platform, family, part_no, valve_channels, expansions,
      housing?, reasons}``
    * ``io_points`` (Control IO Point rows plus ``powered``), ``relays``,
      ``dc_supply`` ``{watts, units, load_w}``, ``interlocks`` (Control Interlock rows)
    * ``panel``: control transformer VA, enclosure size / dims / part, NEMA type,
      heat, thermal ``{status, rise_f, fan_cfm, ac_btu_h, hx_w_per_f}``, SCCR and
      its status, main disconnect, SPD, terminals, GFCI poles
    * ``bom`` (``{section: "Controls", key, description, qty, uom, item_hint,
      part_no}``), ``control_panel`` (``{parent, pumps, lights, fuses, interlocks,
      io_points}`` in the DocType's fieldnames), ``theory_of_operation``
    * ``power_svg``, ``ladder_svg``, ``warnings``, ``status`` (good / warn /
      bad), ``results`` (every CalcResult behind them, as dicts)
    """
    from ..electrical import electrical_report

    model = model or {}
    elec_r = electrical if electrical is not None else electrical_report(model, pumps=pumps)
    ctl = model.get("control") or {}
    site = model.get("site") or {}
    elec_in = model.get("electrical") or {}
    results: dict = {}
    loads = elec_r.get("loads") or []
    circuits = elec_r.get("circuits") or []
    feature = _feature_for_pumps(model)
    site_ctx = {"regulated": bool(site.get("regulated")), "generator": bool(elec_in.get("generator"))}
    pump_rows = {(p.get("id") or str(i)): p for i, p in enumerate(
        pumps if pumps is not None else ((model.get("network") or {}).get("pumps") or []), start=1)}
    topo_override = ctl.get("topology") or {}
    application = ctl.get("application_type") or ("Pool-Spa" if site_ctx["regulated"] else "Fountain")

    # 1. topology per motor circuit
    pump_controls = []
    motor_circuits = [c for c in circuits if c.get("is_motor")]
    for c in motor_circuits:
        load = next((ld for ld in loads if ld.get("key") == c.get("key")), {})
        pid = (load.get("key") or "").split(":", 1)[-1]
        src = pump_rows.get(pid, {})
        pump = {**load, "tag": c["tag"], "label": c.get("description"), "role": src.get("role"),
                "service_factor": src.get("service_factor")}
        # Wind stages and show modulation drive the feature pumps, not circulation or filtration;
        # automatic transfer to bypass is for circulation and filtration only (a feature pump at
        # full speed across the line oversprays).
        critical = (src.get("role") or "").lower() in CRITICAL_ROLES
        duty = {} if critical else feature
        r = pump_control(pump, src.get("topology") or topo_override.get(pid), feature=duty, site=site_ctx,
                         nema=bool(ctl.get("nema_starters")),
                         auto_transfer=bool(ctl.get("auto_transfer")) and critical)
        results[f"pump_control_{c['tag']}"] = r.to_dict()
        pump_controls.append(r.value)

    # 2. drive frames
    elevation = num(site.get("elevation_ft"), 4500.0)
    panel_ambient = num(ctl.get("panel_ambient_f"), num(site.get("air_temp_f"), 95.0))
    location = ctl.get("location") or "outdoor"
    outdoor = location.startswith("outdoor")
    sun = outdoor and not ctl.get("shaded")
    vfd_frames, frames_by_tag = [], {}
    for pc in pump_controls:
        if pc["topology"] not in ("vfd", "vfd_bypass"):
            continue
        circ = next((c for c in motor_circuits if c["tag"] == pc["tag"]), {})
        # The drive sees the enclosure's interior, which the thermal check holds at or below
        # 104 F (with a fan or air conditioner when needed), not the sun-loaded outdoor air.
        drive_ambient = min(panel_ambient + (15 if sun else 0), MAX_INTERNAL_F)
        vf = vfd_frame(circ.get("hp") or 0, circ.get("voltage") or 230, circ.get("phase") or 3,
                       fla=pc.get("fla_a") if not circ.get("hp") else None, elevation_ft=elevation,
                       ambient_f=drive_ambient, cable_ft=circ.get("run_ft") or 0,
                       submersible=bool(pc.get("submersible")), bypass=pc["topology"] == "vfd_bypass")
        results[f"vfd_frame_{pc['tag']}"] = vf.to_dict()
        frames_by_tag[pc["tag"]] = vf.value
        vfd_frames.append({"tag": pc["tag"], "frame": vf.value, "voltage_class": vf.inputs["voltage_class"]["value"],
                           "phase_in": vf.inputs["phase_in"]["value"], "input_fuse_a": vf.inputs["input_fuse_a"]["value"],
                           "loss_w": vf.inputs["loss_w"]["value"], "line_reactor": vf.inputs["line_reactor"]["value"],
                           "lead_treatment": vf.inputs["lead_treatment"]["value"],
                           "bypass_contactor_a": vf.inputs["bypass_contactor_a"]["value"]})

    # 3. controller, I/O, relays, 24 V DC
    io_r = io_list(model, pump_controls)
    io_rows = io_r.value
    results["io_list"] = io_r.to_dict()
    sel = select_controller(io_rows, family=ctl.get("family"), pump_controls=pump_controls)
    results["controller"] = sel.to_dict()
    rel = relay_counts(io_rows)
    results["relays"] = rel.to_dict()
    sol = elec_in.get("solenoid_valves") or {}
    # The door pilot lights are 120 V AC (on the control transformer), not on the 24 V DC supply.
    psu = dc_supply(io_rows, valve_w_each=num(sol.get("watts_each"), 10.0), platform=sel.value["platform"])
    results["dc_supply"] = psu.to_dict()
    il = interlocks(application, pump_controls)
    results["interlocks"] = il.to_dict()

    # 4. panel
    xf = panel_control_transformer(pump_controls, relays=rel.value["interposing"], platform=sel.value["platform"],
                                   psu_input_w=psu.inputs["ac_input_w"]["value"])
    results["control_transformer"] = xf.to_dict()
    gfci_poles = sum(int(c.get("poles") or 1) for c in circuits if c.get("gfci"))
    poles = sum(int(c.get("poles") or 1) for c in circuits)
    tw = terminals_and_wire(circuits, io_rows)
    results["terminals"] = tw.to_dict()
    enc = enclosure_size(pump_controls, vfd_hps=[v["frame"] or 0 for v in vfd_frames], breaker_poles=poles,
                         terminals=tw.value, relays=rel.value["total"], psu_w=psu.value, xfmr_va=xf.value or 0)
    results["enclosure"] = enc.to_dict()
    etype = enclosure_type(location, bool(ctl.get("near_chemicals")))
    results["enclosure_type"] = etype.to_dict()
    contactors = sum(c["qty"] for pc in pump_controls for c in pc["components"] if "contactor" in c["device"].lower())
    overloads = sum(c["qty"] for pc in pump_controls for c in pc["components"] if c["device"].startswith("Overload"))
    heat = panel_heat([v["loss_w"] for v in vfd_frames], xfmr_va=xf.value or 0, psu_w=psu.value,
                      contactors=contactors, overloads=overloads, breaker_poles=poles, ssr=rel.value["ssr"])
    results["panel_heat"] = heat.to_dict()
    material = "stainless" if etype.value == "NEMA 4X" else "steel"
    thermal = enclosure_thermal(enc.inputs["dims_in"]["value"], heat.value, ambient_f=panel_ambient,
                                outdoor_sun=sun, material=material, sealed=etype.value in ("NEMA 4", "NEMA 4X"))
    results["thermal"] = thermal.to_dict()
    sccr_parts = [{"name": f"{pc['tag']} {d['device']}",
                   "kind": "motor_controller_0_50hp" if num(pc.get("hp")) <= 50 else "motor_controller_51_200hp"}
                  for pc in pump_controls for d in pc["components"]
                  if "contactor" in d["device"].lower() or d["device"].startswith("Soft starter")]
    sccr_parts += [{"name": f"{pc['tag']} overload", "kind": "overload_relay"} for pc in pump_controls
                   if pc["topology"] in ("across_the_line", "vfd_bypass")]
    sccr_parts += [{"name": f"{v['tag']} drive", "kind": "vfd"} for v in vfd_frames]
    sccr_parts.append({"name": "terminal blocks", "kind": "terminal_block"})
    feeder = elec_r.get("feeder") or {}
    disc = main_disconnect([pc.get("flc_a") for pc in pump_controls],
                           other_a=sum(num(c.get("load_a"), 0) for c in circuits if not c.get("is_motor")),
                           feeder_ocpd_a=feeder.get("ocpd_a"))
    results["main_disconnect"] = disc.to_dict()
    sccr_parts.append({"name": "main disconnect", "kind": "switch"})
    if circuits:
        sccr_parts.append({"name": "branch breakers", "kind": "branch_breaker"})
    marked = ctl.get("component_ka") or {}
    if marked.get("spd"):
        sccr_parts.append({"name": "SPD", "kind": "spd"})
    for part in sccr_parts:  # a marked rating from the model replaces the SB4.1 default
        if num(marked.get(part["kind"])) > 0:
            part["ka"] = num(marked[part["kind"]])
    sc = sccr(sccr_parts, ctl.get("available_fault_ka"))
    results["sccr"] = sc.to_dict()
    spd = surge_protection(elec_r.get("inputs", {}).get("service_v") or 240, elec_r.get("inputs", {}).get("phase") or 1)
    results["surge_protection"] = spd.to_dict()

    # 5. outputs
    bom = panel_bom(
        pump_controls, controller=sel.value, vfd_frames=vfd_frames, relays=rel.value, psu_w=psu.value,
        psu_units=psu.inputs["units"]["value"], control_xfmr_va=xf.value or 0,
        enclosure={"dims_in": enc.inputs["dims_in"]["value"], "part": enc.inputs["part"]["value"]},
        nema_type=etype.value, disconnect_a=disc.value, terminals=tw.value,
        fan_cfm=thermal.inputs["fan_cfm"]["value"], ac_btu=thermal.inputs["ac_btu_h"]["value"],
        hx_w_per_f=thermal.inputs["hx_w_per_f"]["value"],
    )
    results["panel_bom"] = bom.to_dict()
    fuses = [{"fuse_qty": 3 if v["phase_in"] == 3 else 2, "rating": f"{v['input_fuse_a']} A Class J",
              "protects": f"{v['tag']} drive input"} for v in vfd_frames if v.get("input_fuse_a")]
    for lv in elec_r.get("low_voltage") or []:
        if lv.get("branch_count"):
            fuses.append({"fuse_qty": lv["branch_count"], "rating": f"{lv.get('fuse_a')} A blade",
                          "protects": f"{lv.get('label')} branches"})
    has_wind = feature["wind_control"]
    toop = theory_of_operation(pump_controls, controller=sel.value, io_rows=io_rows, has_wind=has_wind,
                               application_type=application)
    results["theory_of_operation"] = toop.to_dict()
    lv_light_w = sum(num(f.get("watts"), 0) * max(1, int(num(f.get("qty"), 1)))
                     for f in (model.get("lighting") or {}).get("fixtures") or [] if 0 < num(f.get("voltage"), 12) <= 30)
    parent = {
        "product_family": f"Splash Wizard {sel.value['family']}" if sel.value["platform"] == "Splash Wizard" else "",
        "nema_rating": etype.value,
        "application_type": application,
        "main_line_voltage": str(int(elec_r.get("inputs", {}).get("service_v") or 240)),
        "phase": str(elec_r.get("inputs", {}).get("phase") or 1),
        "power_autosize": 1,
        "amperage_to_panel": feeder.get("load_a"),
        "main_breaker_size_a": feeder.get("ocpd_a"),
        "control_voltage_1": "24",
        "control_voltage_1_type": "VDC",
        "control_voltage_2": "120",
        "control_voltage_2_type": "VAC",
        "solenoid_valve_qty": int(num(sol.get("qty"), 0)),
        "lighting_voltage": 12 if lv_light_w else None,
        "control_transformer_va": xf.value,
        "panel_includes_contactors_overload": 1 if any(pc["topology"] in ("across_the_line", "vfd_bypass")
                                                        for pc in pump_controls) else 0,
        "safe_state_on_power_up": 1,
        "enclosure_part_no": (enc.inputs["part"]["value"] or {}).get("enclosure") or "",
        "theory_of_operation": toop.value,
    }
    rows = control_panel_rows(pump_controls, motor_circuits, fixtures=(model.get("lighting") or {}).get("fixtures"),
                              fuses=fuses, interlock_rows=il.value, io_rows=io_rows, parent=parent)
    results["control_panel_rows"] = rows.to_dict()
    power_svg = power_schematic(pump_controls, circuits=circuits,
                                service={"voltage": elec_r.get("inputs", {}).get("service_v"),
                                         "phase": elec_r.get("inputs", {}).get("phase")},
                                disconnect_a=disc.value, vfd_frames=frames_by_tag,
                                transformers=[{"label": f"Control transformer {xf.value} VA"}] if xf.value else [])
    ladder_svg = control_ladder(pump_controls, io_rows, control_xfmr_va=xf.value)
    warnings: list[str] = []
    for r in (sel, thermal, sc):
        warnings += r.warnings
    for pc_key in [k for k in results if k.startswith("pump_control_")]:
        warnings += results[pc_key]["warnings"]
    statuses = [thermal.status, sc.status]
    status = "bad" if "bad" in statuses else ("warn" if "warn" in statuses or warnings else "good")
    return {
        "pumps": pump_controls,
        "vfds": vfd_frames,
        "controller": sel.value,
        "io_points": io_rows,
        "relays": rel.value,
        "dc_supply": {"watts": psu.value, "units": psu.inputs["units"]["value"],
                      "load_w": psu.inputs["load_w"]["value"]},
        "interlocks": il.value,
        "panel": {
            "control_transformer_va": xf.value,
            "enclosure": enc.value,
            "enclosure_dims_in": enc.inputs["dims_in"]["value"],
            "enclosure_part": enc.inputs["part"]["value"],
            "nema_type": etype.value,
            "heat_w": heat.value,
            "thermal": {"status": thermal.status, "rise_f": thermal.value,
                        "fan_cfm": thermal.inputs["fan_cfm"]["value"], "ac_btu_h": thermal.inputs["ac_btu_h"]["value"],
                        "hx_w_per_f": thermal.inputs["hx_w_per_f"]["value"]},
            "sccr_ka": sc.value,
            "sccr_status": sc.status,
            "main_disconnect_a": disc.value,
            "spd": spd.value,
            "terminals": tw.value,
            "gfci_poles": gfci_poles,
        },
        "bom": bom.value,
        "control_panel": rows.value,
        "theory_of_operation": toop.value,
        "power_svg": power_svg,
        "ladder_svg": ladder_svg,
        "warnings": list(dict.fromkeys(warnings)),
        "status": status,
        "results": results,
    }
