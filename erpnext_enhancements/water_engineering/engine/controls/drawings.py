"""States for the power schematic and the control ladder, built from the
controls results and drawn by ``engine.drawings``."""

from __future__ import annotations

from ..drawings import control_ladder_svg, power_schematic_svg
from ..electrical._util import num


def power_state(
    pump_controls: list[dict] | None,
    *,
    circuits: list[dict] | None = None,
    service: dict | None = None,
    disconnect_a: float | None = None,
    vfd_frames: dict | None = None,
    transformers: list[dict] | None = None,
    spd: bool = True,
) -> dict:
    """``vfd_frames`` maps a pump tag to its ``vfd_frame`` value (HP frame)."""
    by_tag = {c.get("tag"): c for c in circuits or []}
    frames = vfd_frames or {}
    svc = service or {}
    pumps = []
    for pc in pump_controls or []:
        tag = pc.get("tag")
        circ = by_tag.get(tag, {})
        ol = pc.get("overload") or {}
        bk = f"{circ.get('breaker_a')} A {circ.get('poles')}P" if circ.get("breaker_a") else "Breaker"
        if circ.get("gfci"):
            bk += " GFCI"
        topo = pc.get("topology")
        contactor = (f"{tag}-SS soft starter" if topo == "soft_starter"
                     else f"{tag}-M {pc.get('contactor')}" + (" A" if pc.get("contactor_basis") == "IEC AC-3" else ""))
        pumps.append({
            "tag": tag,
            "label": pc.get("label"),
            "topology": topo,
            "breaker": bk,
            "contactor": contactor,
            "overload": f"OL set {ol.get('setting_a')} A" if ol.get("setting_a") else "OL",
            "vfd": f"VFD {num(frames.get(tag)):g} HP" if frames.get(tag) else "VFD",
            "sub": f"{num(pc.get('flc_a')):g} A FLC",
        })
    return {
        "title": "Power Schematic",
        "service": {"label": f"{num(svc.get('voltage'), 240):g} V {int(num(svc.get('phase'), 1))}φ service"},
        "disconnect": {"label": f"Disconnect {num(disconnect_a):g} A" if disconnect_a else "Main disconnect"},
        "spd": spd,
        "pumps": pumps,
        "transformers": list(transformers or []),
    }


def ladder_rungs(
    pump_controls: list[dict] | None,
    io_rows: list[dict] | None,
    *,
    control_xfmr_va: float | None = None,
) -> dict:
    """Rungs: master control relay behind the E-stop, then per pump the HOA,
    level and overload permissives and the run coil (or K2 / K3 on a bypass),
    run and fault pilots; wind stages, autofill and lighting."""
    names = {r["point_name"] for r in io_rows or []}
    low_level = "Water level low" in names
    rungs = [{"label": "MCR master control relay",
              "elements": [{"type": "nc", "label": "E-stop"}, {"type": "no", "label": "Start"},
                           {"type": "coil", "label": "MCR"}]}]
    for pc in pump_controls or []:
        tag = pc.get("tag")
        topo = pc.get("topology")
        base = [{"type": "no", "label": "MCR"}]
        if low_level:
            base.append({"type": "no", "label": "LSL ok"})
        hoa = {"type": "box", "label": "HOA: H / A+run"}
        if topo == "vfd_bypass":
            mcr = [{"type": "no", "label": "MCR"}]
            # K1 powers the drive whenever the VFD path is selected (drive healthy, able to report).
            rungs.append({"label": f"{tag} drive input (K1)", "elements": [
                *mcr, {"type": "box", "label": "SEL=VFD"}, {"type": "nc", "label": f"{tag} xfer"},
                {"type": "coil", "label": f"{tag}-K1"}]})
            # DR is the drive's run command; K2 holds through a time-delay-off (TDO, the decel time)
            # after DR drops, so K2 opens with the drive stopped. A drive fault opens K2 at once.
            rungs.append({"label": f"{tag} drive run command (DR)", "elements": [
                *base, hoa, {"type": "box", "label": "SEL=VFD"}, {"type": "nc", "label": f"{tag} VFD flt"},
                {"type": "coil", "label": f"{tag}-DR"}]})
            rungs.append({"label": f"{tag} drive output (K2), TDO hold", "elements": [
                *mcr, {"type": "box", "label": f"{tag}-DR or TDO"}, {"type": "nc", "label": f"{tag} VFD flt"},
                {"type": "nc", "label": f"{tag}-K3"}, {"type": "coil", "label": f"{tag}-K2"}]})
            # Fault transfer: the drive-fault contact with transfer enabled stands in for SEL=BYP;
            # K2 has already dropped on that fault, so the NC K2 contact lets K3 pull in.
            rungs.append({"label": f"{tag} bypass (K3)", "elements": [
                *base, hoa, {"type": "box", "label": "SEL=BYP or (flt + xfer)"}, {"type": "nc", "label": f"{tag}-K2"},
                {"type": "nc", "label": f"{tag}-OLB"}, {"type": "coil", "label": f"{tag}-K3"}]})
            run_contact = f"{tag}-K2/K3"
            fault_contact = f"{tag} VFD flt"
        elif topo == "vfd":
            rungs.append({"label": f"{tag} drive run", "elements": [*base, hoa, {"type": "coil", "label": f"{tag}-RUN"}]})
            run_contact, fault_contact = f"{tag}-RUN", f"{tag} VFD flt"
        else:
            coil = f"{tag}-SS" if topo == "soft_starter" else f"{tag}-M"
            rungs.append({"label": f"{tag} {pc.get('label') or ''}".strip(), "elements": [*base, hoa, {"type": "nc", "label": f"{tag}-OL"}, {"type": "coil", "label": coil}]})
            run_contact, fault_contact = coil, f"{tag}-OL"
        rungs.append({"label": f"{tag} run light", "elements": [
            {"type": "no", "label": run_contact}, {"type": "light", "label": "RUN", "color": "green"}]})
        rungs.append({"label": f"{tag} fault light", "elements": [
            {"type": "no", "label": fault_contact}, {"type": "light", "label": "FLT", "color": "red"}]})
    if "Wind sensor" in names:
        rungs.append({"label": "Wind stage 1: drives to windy speed", "elements": [
            {"type": "box", "label": "Ctrl: wind > medium"}, {"type": "coil", "label": "WS1"}]})
        rungs.append({"label": "Wind stage 2: feature pumps stop", "elements": [
            {"type": "box", "label": "Ctrl: wind > high"}, {"type": "coil", "label": "WS2"}]})
    if "Autofill valve" in names:
        rungs.append({"label": "Autofill", "elements": [
            {"type": "box", "label": "Ctrl: fill call"}, {"type": "coil", "label": "FV"}]})
    if "Lighting" in names or "DMX" in names:
        rungs.append({"label": "Lighting", "elements": [
            {"type": "box", "label": "Ctrl: lights on"}, {"type": "coil", "label": "LR"}]})
    supply = f"Control transformer {num(control_xfmr_va):g} VA, 120 V control" if control_xfmr_va else "120 V control"
    return {"title": "Control Ladder", "supply": supply, "rungs": rungs}


def power_schematic(pump_controls, **kw) -> str:
    return power_schematic_svg(power_state(pump_controls, **kw))


def control_ladder(pump_controls, io_rows, **kw) -> str:
    return control_ladder_svg(ladder_rungs(pump_controls, io_rows, **kw))


__all__ = ["control_ladder", "ladder_rungs", "power_schematic", "power_state"]
