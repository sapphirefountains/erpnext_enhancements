"""Controls outputs: the panel BOM (with Sapphire part numbers where DOC-0028
has them), the Control Panel Design rows phase E pushes into the DocType, and a
draft Theory of Operation.

The BOM line shape is the electrical package's plus ``part_no``:
``{section: "Controls", key, description, qty, uom, item_hint, part_no}``.
The Control Panel Design child rows use exactly the child tables' fieldnames
(Control Pump, Control Light, Control Fuse, Control Interlock, Control IO Point).
"""

from __future__ import annotations

from ..electrical._util import num
from ..envelope import CalcResult, make_input

SECTION = "Controls"
PUMP_VOLTAGES = (120, 208, 230, 240, 480, 24, 12)  # Control Pump.voltage options
LIGHT_VOLTAGES = (12, 24, 120)  # Control Light.voltage options
CIT_OUTPUTS = "Control Panel Design child tables (Control Pump, Light, Fuse, Interlock, IO Point)"


def _line(key, description, qty, uom="ea", item_hint="", part_no=None):
    return {"section": SECTION, "key": key, "description": description, "qty": qty, "uom": uom,
            "item_hint": item_hint or description, "part_no": part_no}


def panel_bom(
    pump_controls: list[dict] | None,
    *,
    controller: dict | None = None,
    vfd_frames: list[dict] | None = None,
    relays: dict | None = None,
    psu_w: float = 0.0,
    psu_units: int = 1,
    control_xfmr_va: float = 0.0,
    enclosure: dict | None = None,
    nema_type: str = "",
    disconnect_a: float = 0.0,
    terminals: int = 0,
    spd: bool = True,
    fan_cfm: float | None = None,
    ac_btu: float | None = None,
) -> CalcResult:
    """Panel BOM lines. Like parts are grouped by description, so two pumps on
    the same contactor make one line of qty 2."""
    grouped: dict[str, dict] = {}

    def add(key, description, qty, uom="ea", hint="", part_no=None):
        if not qty:
            return
        if key in grouped:
            grouped[key]["qty"] += qty
        else:
            grouped[key] = _line(key, description, qty, uom, hint, part_no)

    c = controller or {}
    if c.get("platform") == "Splash Wizard":
        add(f"ctl:{c.get('part_no')}", f"Splash Wizard {c.get('family')} controller, "
            f"{c.get('valve_channels', 0) - sum(e['valves'] for e in c.get('expansions', []))} valves",
            1, part_no=c.get("part_no"), hint=f"Splash Wizard {c.get('family')}")
        for e in c.get("expansions", []):
            add(f"ctl:exp:{e['part_no']}", f"Splash Wizard valve expansion, {e['valves']} valves", 1, part_no=e["part_no"])
        h = c.get("housing") or {}
        if h:
            add("ctl:housing:box", "Enclosure box, WP-40, Splash Wizard", 1, part_no=h.get("box"))
            add("ctl:housing:lid", "Enclosure lid, WP-40, Splash Wizard", 1, part_no=h.get("lid"))
    elif c:
        add("ctl:industrial", "Splash Wizard Industrial controller (PLC + HMI)", 1, hint="Splash Wizard Industrial")
    for pc in pump_controls or []:
        for comp in pc.get("components", []):
            d = comp["device"]
            if d == "Variable frequency drive":
                continue  # drives come from vfd_frames with their derated frame
            add(f"{d}:{comp.get('rating')}", f"{d}, {comp.get('rating')}".rstrip(", "), comp.get("qty", 1))
    for vf in vfd_frames or []:
        add(f"vfd:{vf.get('frame')}:{vf.get('voltage_class')}",
            f"Variable frequency drive, {vf.get('frame')} HP frame, {vf.get('voltage_class')} V", 1,
            hint=f"VFD {vf.get('frame')} HP")
        if vf.get("input_fuse_a"):
            add(f"vfdfuse:{vf['input_fuse_a']}", f"Class J fuse {vf['input_fuse_a']} A (drive input)",
                3 if vf.get("phase_in") == 3 else 2)
            add(f"vfdfuseblock:{vf['input_fuse_a']}", f"Class J fuse holder, {3 if vf.get('phase_in') == 3 else 2}-pole",
                1)
        if vf.get("line_reactor"):
            add(f"reactor:line:{vf.get('frame')}", f"Line reactor 3%, {vf.get('frame')} HP", 1)
        if vf.get("lead_treatment") not in (None, "none"):
            add(f"reactor:load:{vf.get('frame')}:{vf['lead_treatment']}",
                f"{vf['lead_treatment'].capitalize()}, {vf.get('frame')} HP", 1)
    r = relays or {}
    add("relay:ssr", "Solid-state relay, 24 V DC input, DIN mount", r.get("ssr", 0) + (r.get("spare", 0) // 2))
    add("relay:interposing", "Interposing relay, 24 V DC coil, DIN mount", r.get("interposing", 0)
        + (r.get("spare", 0) - r.get("spare", 0) // 2))
    if psu_w:
        add(f"psu:{int(psu_w)}", f"24 V DC power supply {int(psu_w)} W, DIN rail", psu_units)
    if control_xfmr_va:
        add(f"xfmr:{int(control_xfmr_va)}", f"Control transformer {int(control_xfmr_va)} VA", 1)
    e = enclosure or {}
    if e:
        part = e.get("part") or {}
        dims = e.get("dims_in") or []
        label = " x ".join(f"{x:g}" for x in dims) + " in" if dims else ""
        add("enclosure", f"Enclosure {nema_type} {label}".strip(), 1,
            hint=part.get("enclosure") or f"{nema_type} enclosure {label}", part_no=part.get("part_no"))
        if part.get("backplane"):
            add("backplane", part["backplane"], 1, part_no=part.get("backplane_part_no"))
        else:
            add("backplane", f"Backplane for {label} enclosure", 1)
    if disconnect_a:
        add(f"disconnect:{int(disconnect_a)}", f"Main disconnect {int(disconnect_a)} A, lockable", 1)
    if spd:
        add("spd", "Surge protective device, Type 2 (UL 1449)", 1)
    if terminals:
        add("terminals", "Terminal block, feed-through, DIN mount", terminals)
        add("dinrail", "DIN rail 35 mm", 1, "lot")
        add("wireduct", "Wire duct and cover", 1, "lot")
    if fan_cfm:
        add(f"fan:{int(fan_cfm)}", f"Filtered enclosure fan, {int(fan_cfm)} CFM, with exhaust grille", 1)
    if ac_btu:
        add(f"ac:{int(ac_btu)}", f"Enclosure air conditioner, {int(ac_btu)} BTU/h", 1)
    add("estop", "E-stop pushbutton, 40 mm mushroom, maintained", 1 if pump_controls else 0)
    lines = list(grouped.values())
    return CalcResult(
        calc="panel_bom",
        value=lines,
        unit="lines",
        inputs={"lines": make_input(len(lines), "", "prior_calc")},
        formula="one line per distinct part ; Sapphire part numbers from DOC-0028 where they exist",
        steps=[f"{ln['qty']} {ln['uom']} {ln['description']}" + (f" [{ln['part_no']}]" if ln["part_no"] else "")
               for ln in lines],
        citations=["DOC-0028 Design Part Numbers", CIT_OUTPUTS],
        warnings=["Ratings are the engine's selections; the panel builder picks the maker and catalog numbers."],
    )


def _nearest(options, value):
    v = num(value)
    return str(min(options, key=lambda x: abs(x - v))) if v else ""


def control_panel_rows(
    pump_controls: list[dict] | None,
    pumps: list[dict] | None,
    *,
    fixtures: list[dict] | None = None,
    fuses: list[dict] | None = None,
    interlock_rows: list[dict] | None = None,
    io_rows: list[dict] | None = None,
    parent: dict | None = None,
) -> CalcResult:
    """Control Panel Design rows: ``{parent: {...fields}, pumps, lights, fuses,
    interlocks, io_points}``, each list in its child table's fieldnames."""
    pump_rows = []
    by_tag = {pc.get("tag"): pc for pc in pump_controls or []}
    for p in pumps or []:
        pc = by_tag.get(p.get("tag"), {})
        drive = pc.get("topology") in ("vfd", "vfd_bypass")
        pump_rows.append({
            "function": p.get("label") or p.get("description"),
            "qty": 1,
            "part_no": p.get("part_no") or "",
            "hp": num(p.get("hp")),
            "fla_amps": round(num(p.get("fla_a") or p.get("load_a") or p.get("amps")), 2),
            "control_method": "VFD" if drive else "Motor Starter",
            "voltage": _nearest(PUMP_VOLTAGES, p.get("voltage")),
            "voltage_type": "VAC",
            "phase": str(int(num(p.get("phase"), 1))),
            "drawing_ref": pc.get("tag") or "",
        })
    light_rows = [{
        "qty": max(1, int(num(f.get("qty"), 1))),
        "part_no_description": f.get("item") or f.get("label") or "",
        "watts_each": num(f.get("watts")),
        "voltage": _nearest(LIGHT_VOLTAGES, f.get("voltage") or 12),
    } for f in fixtures or []]
    fuse_rows = [{"fuse_qty": int(num(f.get("fuse_qty"), 1)), "rating": str(f.get("rating") or ""),
                  "replacement_part_no": f.get("replacement_part_no") or "", "protects": f.get("protects") or ""}
                 for f in fuses or []]
    il_rows = [{"condition": r.get("condition"), "action": r.get("action"),
                "interlock_method": r.get("interlock_method") or "", "threshold": r.get("threshold") or "",
                "enabled": int(r.get("enabled", 1)), "notes": r.get("notes") or ""} for r in interlock_rows or []]
    io = [{k: r.get(k) for k in ("point_name", "io_type", "signal", "device", "description", "qty",
                                 "control_mechanism", "drawing_ref")} for r in io_rows or []]
    rows = {"parent": dict(parent or {}), "pumps": pump_rows, "lights": light_rows, "fuses": fuse_rows,
            "interlocks": il_rows, "io_points": io}
    return CalcResult(
        calc="control_panel_rows",
        value=rows,
        unit="",
        inputs={k: make_input(len(v), "rows", "prior_calc") for k, v in rows.items() if isinstance(v, list)},
        formula="child rows in the Control Panel Design child tables' fieldnames",
        steps=[f"{k}: {len(v)} row(s)" for k, v in rows.items() if isinstance(v, list)],
        citations=[CIT_OUTPUTS],
    )


def theory_of_operation(
    pump_controls: list[dict] | None,
    *,
    controller: dict | None = None,
    io_rows: list[dict] | None = None,
    has_wind: bool = False,
    application_type: str = "Fountain",
) -> CalcResult:
    """A draft Theory of Operation paragraph for the submittal, from the topology,
    the controller and the interlocks. A draft: the designer edits it."""
    c = controller or {}
    names = [r["point_name"] for r in io_rows or []]
    plat = (f"a Splash Wizard {c.get('family')} controller" if c.get("platform") == "Splash Wizard"
            else "an industrial PLC controller with an operator touch screen")
    parts = [f"The fountain is run by {plat} in the control panel."]
    pcs = pump_controls or []
    for pc in pcs:
        topo = pc.get("topology")
        if topo == "across_the_line":
            parts.append(f"{pc.get('label')} starts across the line through a contactor and overload relay.")
        elif topo == "soft_starter":
            parts.append(f"{pc.get('label')} starts through a soft starter that ramps it up to limit inrush and water hammer.")
        elif topo == "vfd":
            parts.append(f"{pc.get('label')} runs on a variable frequency drive; the controller sets its speed.")
        elif topo == "vfd_bypass":
            transfer = any(i.get("action", "").startswith("Transfer") for i in pc.get("interlocks", []))
            parts.append(
                f"{pc.get('label')} runs on a variable frequency drive with a manual bypass. The VFD / Bypass selector "
                "chooses the drive or a full-voltage start through the bypass contactor; the drive output and bypass "
                "contactors are mechanically and electrically interlocked so both can never close"
                + (", and a drive fault transfers the pump to bypass automatically." if transfer else ".")
            )
    if pcs:
        parts.append("Each pump has a Hand-Off-Auto switch: Hand runs it from the panel, Auto hands it to the controller's "
                     "schedule and daily run window, and Off stops it. Run and fault pilot lights are on the door.")
    if "Water level low" in names:
        parts.append("A low-water float stops the pumps before they run dry"
                     + ("; the fill float opens the autofill valve until the level recovers." if "Autofill valve" in names
                        else "."))
    if has_wind or "Wind sensor" in names:
        parts.append("The anemometer feeds two wind setpoints: at the medium setpoint the drives ramp to their windy "
                     "speed, and at the high setpoint the feature pumps stop (DOC-0123).")
    if "Circulation flow proof" in names:
        parts.append("Feature pumps, chemical feed and the heater are interlocked to proven circulation flow.")
    if "Solenoid valves" in names:
        parts.append("The controller sequences the manifold solenoid valves at 24 V DC through one solid-state relay "
                     "each, in patterns or at random.")
    if "E-stop" in names:
        parts.append("The E-stop drops every output, and on power-up all components return to a safe off state.")
    if (application_type or "") == "Pool-Spa":
        parts.append("The therapy-jet timer limits the jet pump to 15 minutes, and the emergency shut-off stops everything.")
    text = " ".join(parts)
    return CalcResult(
        calc="theory_of_operation",
        value=text,
        unit="text",
        inputs={"sentences": make_input(len(parts), "", "prior_calc")},
        formula="sentences from the topology, the controller, the I/O list and the interlocks",
        steps=parts,
        citations=["DOC-0123 / DOC-0126 / DOC-0127"],
        warnings=["A draft for the submittal; the designer reviews and edits it."],
    )


__all__ = ["SECTION", "control_panel_rows", "panel_bom", "theory_of_operation"]
