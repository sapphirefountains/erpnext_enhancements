"""Electrical design for a water feature: loads, branch circuits, feeder and
service, low-voltage runs, transformers, the NEC 680 checklist, drives, backup
power, energy, BOM lines and the one-line drawing.

This package replaces ``engine/electrical.py`` (phase L of the Fountain Designer
rebuild, PLAN.md section 4). The four functions that module had keep their names
and import paths -- ``motor_flc``, ``total_connected_load``,
``service_main_breaker``, ``control_transformer_va`` -- and are re-exported here
from the modules they now live in. ``electrical_report(model)`` runs the whole
chain for phase E's ``derive()``.

Every public function returns a :class:`~..envelope.CalcResult` with its NEC
citations and a note to verify the adopted edition; the tables live in
``engine/data/nec_tables.py`` (NEC 2023). ``README.md`` beside this file maps each
module to its articles. Like the rest of ``engine/``, nothing here imports frappe,
and nothing imports ``engine.hydraulics``: pump power comes from the model.
"""

from __future__ import annotations

import math

from ..data.nec_tables import NEC_EDITION, VERIFY_EDITION
from ._util import num, phase_of
from .article680 import article680_checklist, gfci_requirement
from .backup import backup_plan, battery_runtime, generator_size
from .bom import electrical_bom
from .circuits import branch_circuit, branch_circuits
from .conductors import ampacity, egc_size, size_conductor, wire_for_load
from .conduit import conduit_fill
from .drawings import electrical_oneline, oneline_state
from .energy import annual_energy
from .feeders import (
    PANEL_SCHEDULE_COLUMNS,
    feeder_load,
    feeder_ocpd,
    panel_schedule,
    service_check,
    service_main_breaker,
    total_connected_load,
)
from .loads import build_loads, demand_summary, motor_flc
from .ocpd import continuous_ocpd, motor_branch_ocpd
from .transformers import control_transformer_va, lighting_transformer
from .vfd import select_vfd
from .voltage_drop import (
    BRANCH_DROP_PCT,
    LV_LIGHTING_WIRE_SIZES,
    TOTAL_DROP_PCT,
    low_voltage_run,
    total_drop,
    voltage_drop,
)

CONTROL_PUMP_VOLTAGES = (120, 208, 230, 240, 480)  # Control Pump.voltage options
# The rain curtain's coilPlan supply rule: sized for every valve open plus the controller's
# logic, with 25% headroom, split over 1000 W units so each stays on an ordinary circuit.
PSU_SIZES_W = [35, 50, 75, 100, 150, 200, 350, 500, 750, 1000]
LOGIC_W = 3.0


def valve_supply(valves_w: float, logic_w: float = LOGIC_W, io_w: float = 0.0) -> dict:
    """``{n, W}``: how many DC supplies of which standard size carry the valves
    (``coilPlan`` in the Digital Rain Curtain's src/electrical.js): every valve
    open, plus the controller's logic (3 W) and its I/O load (``io_w``: relay
    coils, powered sensors; the rain curtain's ``ioLoadW``), x 1.25. The controls
    package sizes the panel's own 24 V DC supply with the full I/O list."""
    need = 1.25 * (num(valves_w) + num(logic_w) + num(io_w))
    n = max(1, math.ceil(need / PSU_SIZES_W[-1]))
    return {"n": n, "W": next((s for s in PSU_SIZES_W if s * n >= need - 1e-9), PSU_SIZES_W[-1])}


def _basin_perimeter_ft(basins: list[dict] | None) -> float:
    total = 0.0
    for b in basins or []:
        shape = (b.get("shape") or "rectangular").lower()
        if "cyl" in shape or "circ" in shape or "round" in shape:
            total += math.pi * num(b.get("diameter_in") or b.get("length_in")) / 12.0
        else:
            total += 2.0 * (num(b.get("length_in")) + num(b.get("width_in"))) / 12.0
    return total


def _control_pump_row(c: dict) -> dict:
    """A Control Pump child row (Control Panel Design.pumps) for one motor circuit."""
    v = num(c.get("voltage"))
    volt = min(CONTROL_PUMP_VOLTAGES, key=lambda x: abs(x - v)) if v else ""
    return {
        "function": c.get("description") or c.get("tag"),
        "qty": 1,
        "part_no": "",
        "hp": num(c.get("hp")),
        "fla_amps": round(num(c.get("load_a")), 2),
        "control_method": "VFD" if c.get("control_method") == "VFD" else "Motor Starter",
        "voltage": str(volt),
        "voltage_type": "VAC",
        "phase": str(c.get("phase") or 1),
    }


def electrical_report(model: dict | None, *, pumps: list[dict] | None = None) -> dict:
    """Run the electrical chain on a design model (PLAN.md section 3.3) and return
    one JSON-ready dict for ``derive()``. ``pumps`` overrides ``network.pumps``
    (each ``{id, label, hp, fla, kw, voltage, phase, qty, control, code_letter,
    submersible, run_ft}``). Keys:

    ``edition``, ``verify``, ``inputs``, ``loads``, ``circuits``,
    ``low_voltage``, ``transformers``, ``feeder``, ``service``, ``panel_rows``,
    ``control_pump_rows``, ``checklist``, ``backup``, ``energy``, ``bom``,
    ``oneline_svg``, ``warnings``, ``status``, and ``results`` (every
    CalcResult behind them, as dicts, by name).
    """
    model = model or {}
    elec = model.get("electrical") or {}
    site = model.get("site") or {}
    service_v = num(elec.get("service_v"), 240)
    service_ph = phase_of(elec.get("phase"), 1)
    venue = site.get("venue_type") or "Decorative Fountain"
    conduit_type = elec.get("conduit_type") or "PVC-40"
    ambient_f = num(elec.get("ambient_f"), num(site.get("air_temp_f"), 86.0))
    device = elec.get("ocpd_device") or "inverse_time_breaker"
    material = elec.get("material") or "Cu"
    termination_c = elec.get("termination_c")
    max_drop = num(elec.get("max_branch_drop_pct"), BRANCH_DROP_PCT)
    results: dict = {}

    loads_r = build_loads(model, pumps=pumps)
    loads = loads_r.value
    results["loads"] = loads_r.to_dict()
    results["demand"] = demand_summary(loads, service_v, service_ph).to_dict()

    circ_r = branch_circuits(
        loads, venue=venue, conduit_type=conduit_type, ambient_f=ambient_f, device=device,
        max_drop_pct=max_drop, material=material, termination_c=termination_c,
    )
    circuits = circ_r.value
    results["circuits"] = circ_r.to_dict()

    # Low-voltage side: lighting transformer + runs, valve supply + coil runs, control transformer.
    fixtures = (model.get("lighting") or {}).get("fixtures") or []
    transformers, xfmr_bom, low_voltage = [], [], []
    lv_rows = [f for f in fixtures if 0 < num(f.get("voltage"), 12) <= 30]
    lv_w = sum(num(f.get("watts")) * max(1, int(num(f.get("qty"), 1))) for f in lv_rows)
    if lv_w > 0:
        lt = lighting_transformer(lv_w, num(lv_rows[0].get("voltage"), 12))
        results["lighting_transformer"] = lt.to_dict()
        transformers.append({"label": f"Lighting transformer {lt.value} VA",
                             "sub": f"{lt.inputs['units']['value']} unit(s), {num(lv_rows[0].get('voltage'), 12):g} V"})
        xfmr_bom.append({"va_each": lt.inputs["va_each"]["value"], "units": lt.inputs["units"]["value"]})
        for j, f in enumerate(lv_rows, start=1):
            run = low_voltage_run(
                max(1, int(num(f.get("qty"), 1))), num(f.get("watts")), num(f.get("spacing_ft"), 4.0),
                voltage=num(f.get("voltage"), 12), feed_ft=num(f.get("feed_ft"), 20.0),
                supply_at=f.get("supply_at") or "end", branch_fuse_a=num(f.get("circuit_a"), 25.0),
                limit_pct=num(elec.get("lv_drop_pct"), 5.0), group=1, kind="luminaire",
                wire_sizes=LV_LIGHTING_WIRE_SIZES,
            )
            results[f"lv_lighting_{j}"] = run.to_dict()
            low_voltage.append({**run.value, "tag": f"lights-{j}", "label": f.get("label") or f"Fixtures {j}",
                                "voltage": num(f.get("voltage"), 12)})
    sol = elec.get("solenoid_valves") or {}
    if int(num(sol.get("qty"))) > 0:
        run = low_voltage_run(
            int(num(sol.get("qty"))), num(sol.get("watts_each"), 10.0), num(sol.get("spacing_ft"), 1.0),
            voltage=num(sol.get("voltage"), 24), feed_ft=num(sol.get("feed_ft"), 6.0),
            supply_at=sol.get("supply_at") or "end", branch_fuse_a=num(sol.get("branch_fuse_a")),
            limit_pct=num(sol.get("drop_pct"), 5.0), group=8, kind="valve",
        )
        results["lv_valves"] = run.to_dict()
        psu = valve_supply(run.value["total_w"], logic_w=num(sol.get("logic_w"), LOGIC_W), io_w=sol.get("io_w"))
        low_voltage.append({**run.value, "tag": "valves", "label": "Solenoid valves",
                            "voltage": num(sol.get("voltage"), 24), "supply_w": psu["W"], "supply_n": psu["n"]})
    ctl_devices = next((ld.get("control_devices") for ld in loads if ld.get("kind") == "controls"), None) or []
    ctl = control_transformer_va(ctl_devices)
    results["control_transformer"] = ctl.to_dict()
    if ctl.value:
        transformers.append({"label": f"Control transformer {ctl.value} VA", "sub": "120 V : 24 V"})

    # Feeder and service.
    fl = feeder_load(loads, service_v, service_ph)
    fo = feeder_ocpd(loads, service_v, service_ph, device)
    results["feeder_load"] = fl.to_dict()
    results["feeder_ocpd"] = fo.to_dict()
    feeder_ft = num(elec.get("feeder_ft"))
    worst_branch = max((num(c.get("drop_pct")) for c in circuits), default=0.0)
    feeder = {"load_a": fl.value, "ocpd_a": fo.value, "length_ft": feeder_ft, "conduit_type": conduit_type,
              "current_carrying": 3 if service_ph == 3 else 2}
    if fl.value and fo.value:
        # A motor feeder's conductors carry the 430.24 load; its 430.62 device may be larger
        # (430.62, 240.4(G)), so the protection check applies only to a feeder with no motors.
        # The EGC still follows the installed device (250.122).
        has_motor = any(ld.get("is_motor") for ld in loads)
        fw = wire_for_load(
            fl.value, voltage=service_v, phase=service_ph, length_ft=feeder_ft, continuous=False,
            ocpd_a=None if has_motor else fo.value, egc_ocpd_a=fo.value, material=material,
            ambient_f=ambient_f, termination_c=termination_c,
            max_drop_pct=max(TOTAL_DROP_PCT - worst_branch, 1.0),
        )
        results["feeder_wire"] = fw.to_dict()
        if fw.value:
            egc = (fw.inputs.get("egc") or {}).get("value")
            fc = conduit_fill(
                [{"size": fw.value, "qty": feeder["current_carrying"] + (1 if service_v <= 250 else 0)},
                 {"size": egc, "qty": 1 if egc else 0}],
                conduit_type,
            )
            results["feeder_conduit"] = fc.to_dict()
            feeder.update(conductor=fw.value, egc=egc, conduit=fc.value,
                          drop_pct=(fw.inputs.get("drop_pct") or {}).get("value"))
            td = total_drop(feeder["drop_pct"] or 0.0, worst_branch)
            results["total_drop"] = td.to_dict()
    sc = service_check(fl.value or 0, fo.value or 0, elec.get("available_service_a"), service_v, service_ph)
    results["service_check"] = sc.to_dict()

    ps = panel_schedule(circuits)
    results["panel_schedule"] = ps.to_dict()

    bonding_ft = _basin_perimeter_ft(model.get("basins")) + (20.0 if circuits else 0.0)
    checklist = article680_checklist(
        loads, venue=venue, fixtures=fixtures, circuits=circuits,
        site={"bonding_in_bom": bonding_ft > 0, **(elec.get("site_checks") or {})},
    )
    results["checklist"] = checklist.to_dict()
    bk = backup_plan(loads, battery_wh=num(elec.get("battery_wh")))
    results["backup"] = bk.to_dict()
    en = annual_energy(loads, rate_per_kwh=num(site.get("kwh_rate"), 0.17))
    results["energy"] = en.to_dict()

    jboxes = sum(max(1, int(num(f.get("qty"), 1))) for f in fixtures if f.get("underwater", True) is not False)
    bom = electrical_bom(
        circuits, feeder=feeder, main_breaker_a=fo.value, transformers=xfmr_bom, low_voltage=low_voltage,
        control_transformer_va=ctl.value, junction_boxes=jboxes, bonding_ft=bonding_ft, material=material,
    )
    results["bom"] = bom.to_dict()
    svg = electrical_oneline(
        circuits,
        service={"voltage": service_v, "phase": service_ph, "main_breaker": fo.value,
                 "available_a": elec.get("available_service_a")},
        feeder=feeder, transformers=transformers,
    )

    warnings: list[str] = []
    for ld in loads:
        lv_ = num(ld.get("voltage"))
        if lv_ > service_v * 1.05 and lv_ not in (120, 277):
            warnings.append(
                f"{ld.get('label')} is rated {lv_:g} V on a {service_v:g} V service; confirm a dual-rated "
                "motor (208-230 V) or add a transformer."
            )
    for r in (loads_r, circ_r, fo, sc, checklist):
        warnings += [w for w in r.warnings if w != VERIFY_EDITION]
    statuses = [sc.status, checklist.status] + [c["calcs"]["wire"].get("status") for c in circuits]
    status = "bad" if "bad" in statuses else ("warn" if "warn" in statuses else "good")
    return {
        "edition": NEC_EDITION,
        "verify": VERIFY_EDITION,
        "inputs": {
            "service_v": service_v, "phase": service_ph, "venue": venue, "conduit_type": conduit_type,
            "ambient_f": ambient_f, "ocpd_device": device, "material": material,
            "available_service_a": elec.get("available_service_a"), "feeder_ft": feeder_ft,
        },
        "loads": loads,
        "circuits": [{k: v for k, v in c.items() if k != "calcs"} for c in circuits],
        "low_voltage": low_voltage,
        "transformers": transformers,
        "feeder": feeder,
        "service": {"required_a": fl.value, "main_breaker_a": fo.value,
                    "available_a": elec.get("available_service_a"), "utilization_pct": sc.value,
                    "status": sc.status},
        "panel_rows": ps.value,
        "control_pump_rows": [_control_pump_row(c) for c in circuits if c.get("is_motor")],
        "checklist": checklist.value,
        "backup": bk.value,
        "energy": {"cost_per_year": en.value, "rows": en.inputs["rows"]["value"],
                   "kwh_per_year": en.inputs["total_kwh"]["value"]},
        "bom": bom.value,
        "oneline_svg": svg,
        "warnings": list(dict.fromkeys(warnings)),
        "status": status,
        "results": results,
    }


__all__ = [
    "PANEL_SCHEDULE_COLUMNS",
    "ampacity",
    "annual_energy",
    "article680_checklist",
    "backup_plan",
    "battery_runtime",
    "branch_circuit",
    "branch_circuits",
    "build_loads",
    "conduit_fill",
    "continuous_ocpd",
    "control_transformer_va",
    "demand_summary",
    "egc_size",
    "electrical_bom",
    "electrical_oneline",
    "electrical_report",
    "feeder_load",
    "feeder_ocpd",
    "generator_size",
    "gfci_requirement",
    "lighting_transformer",
    "low_voltage_run",
    "motor_branch_ocpd",
    "motor_flc",
    "oneline_state",
    "panel_schedule",
    "select_vfd",
    "service_check",
    "service_main_breaker",
    "size_conductor",
    "total_connected_load",
    "total_drop",
    "valve_supply",
    "voltage_drop",
    "wire_for_load",
]
