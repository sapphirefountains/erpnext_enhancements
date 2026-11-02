"""Bench-free pytest suite for the electrical package's load list, feeder,
checklist, BOM, drawing and the ``electrical_report`` entry point (phase L).

Run: python -m pytest erpnext_enhancements/tests/test_electrical_report.py -q
"""

import ast
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.water_engineering.engine import electrical as E
from erpnext_enhancements.water_engineering.engine.envelope import CalcResult

PKG = REPO_ROOT / "erpnext_enhancements" / "water_engineering" / "engine" / "electrical"

FIKA_LOADS = [
    {"label": "circ", "is_motor": True, "fla": 8},
    {"label": "jet", "is_motor": True, "fla": 8},
    {"label": "heater", "continuous": True, "fla": 11000 / 240},
    {"label": "controller", "fla": 5},
    {"label": "cl feed", "is_motor": True, "fla": 1.7},
    {"label": "ph feed", "is_motor": True, "fla": 1.7},
]


def _model(**over):
    m = {
        "site": {"air_temp_f": 95, "kwh_rate": 0.105, "venue_type": "Decorative Fountain"},
        "basins": [{"id": "b1", "shape": "rectangular", "length_in": 120, "width_in": 24}],
        "network": {"pumps": [
            {"id": "p1", "label": "Feature pump", "hp": 5, "voltage": 230, "phase": 1, "run_ft": 150},
        ]},
        "lighting": {"fixtures": [
            {"label": "Uplights", "watts": 35, "qty": 8, "voltage": 12, "lens_depth_in": 2, "spacing_ft": 3},
        ]},
        "treatment": {},
        "electrical": {"service_v": 240, "phase": 1, "panel_distance_ft": 60, "available_service_a": 100,
                       "feeder_ft": 40, "solenoid_valves": {"qty": 24, "watts_each": 4, "spacing_ft": 0.5}},
    }
    m.update(over)
    return m


# --- the package boundary ---------------------------------------------------------


def test_package_never_imports_frappe_or_hydraulics():
    for path in PKG.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for n in names:
                assert not n.startswith("frappe"), f"{path.name} imports {n}"
                assert "hydraulics" not in n, f"{path.name} imports {n}"


def test_old_names_still_import_from_both_paths():
    from erpnext_enhancements.water_engineering import engine
    from erpnext_enhancements.water_engineering.engine.electrical import (
        control_transformer_va,
        motor_flc,
        service_main_breaker,
        total_connected_load,
    )

    assert engine.motor_flc is motor_flc
    assert engine.service_main_breaker is service_main_breaker
    assert engine.total_connected_load is total_connected_load
    assert engine.control_transformer_va is control_transformer_va


# --- loads ------------------------------------------------------------------------


def test_build_loads_from_model():
    loads = E.build_loads(_model()).value
    by_key = {ld["key"]: ld for ld in loads}
    pump = by_key["pump:p1"]
    assert pump["is_motor"] and pump["amps"] == 28.0 and pump["source"] == "table"
    assert pump["continuous"]  # 6 h/day default is 3 h or more
    light = by_key["lighting:lv"]
    # The panel load is the fixture watts; 125% is applied once, by the branch and feeder rules.
    assert light["va"] == pytest.approx(280) and light["secondary_v"] == 12
    assert by_key["valves"]["continuous"] is False
    assert any(d["device"] == "solenoid" for d in by_key["controls"]["control_devices"])  # autofill


def test_pump_from_kw_rounds_up_to_a_table_motor():
    loads = E.build_loads({}, pumps=[{"id": "p", "kw": 1.6, "voltage": 230, "phase": 1}]).value
    assert loads[0]["hp"] == 3.0  # 1.6 kW = 2.15 HP -> 3 HP motor
    assert loads[0]["amps"] == 17.0
    assert loads[0]["watts"] == pytest.approx(1600 / 0.9)  # energy from the pump's kW, not the 3 HP motor


def test_pump_with_nameplate_only_uses_it():
    loads = E.build_loads({}, pumps=[{"id": "s", "fla": 6.3, "voltage": 120, "phase": 1}]).value
    assert loads[0]["amps"] == 6.3 and loads[0]["source"] == "nameplate"


def test_motor_flc_208v_column():
    # Table 430.248 has its own 208 V column (8.8 A for 1 HP), not the 230 V one.
    assert E.motor_flc(1, 1, 208) == 8.8
    assert E.motor_flc(1, 3, 208) == 4.6
    assert E.motor_flc(10, 3, 480) == 14.0
    assert E.motor_flc(1, 1, 120) == 16.0


def test_demand_summary():
    loads = E.build_loads(_model()).value
    d = E.demand_summary(loads, 240, 1)
    assert d.value == pytest.approx(sum(ld["va"] * ld["qty"] for ld in loads), abs=0.1)


# --- feeder and service -----------------------------------------------------------------


def test_total_connected_load_unchanged():
    # 1.25*8 (largest motor) + (8+1.7+1.7) + 1.25*45.83 + 5 = 83.69 A
    assert E.total_connected_load(FIKA_LOADS).value == pytest.approx(83.69, abs=0.05)


def test_service_main_breaker_seeds_from_430_52():
    # largest branch device: 8 A x 250% = 20 A (was 15 A at 125%) ; + 11.4 A other motors
    # + 1.25 x 45.83 + 5 = 62.29 A other loads -> ceiling 93.69 A -> 90 A (rounds DOWN)
    r = E.service_main_breaker(FIKA_LOADS)
    assert r.value == 90
    assert r.inputs["largest_branch_ocpd"]["value"] == 20
    assert r.inputs["raised_to_load"]["value"] is False


def test_feeder_ocpd_raised_when_rounding_goes_under_the_load():
    # One 8 A motor + a 60 A continuous heater: ceiling 20 + 75 = 95 -> 90 A, but the
    # load is 1.25 x 8 + 75 = 85 A -> 90 A stands. With a 64 A heater: ceiling 100 -> 100.
    loads = [{"is_motor": True, "amps": 8}, {"continuous": True, "amps": 60}]
    assert E.feeder_ocpd(loads).value == 90
    # Two 28 A motors: ceiling 70 + 28 = 98 -> 90 A, above the 63 A load, so 90 A stands.
    two = [{"is_motor": True, "amps": 28, "qty": 2}]
    r = E.feeder_ocpd(two)
    assert r.value == 90 and r.inputs["feeder_load_a"]["value"] == 63.0
    assert r.inputs["raised_to_load"]["value"] is False
    # A 1.7 A feed pump and a 100 A continuous heater: ceiling 15 + 125 = 140 -> 125 A, under the
    # 127.1 A load -> raised to 150 A (430.63 is a minimum).
    raised = E.feeder_ocpd([{"is_motor": True, "amps": 1.7}, {"continuous": True, "amps": 100}])
    assert raised.value == 150
    assert raised.inputs["raised_to_load"]["value"] is True


def test_feeder_load_mixed_voltages_in_va():
    # 1.25 x 28 A at 240 V + a 10 A continuous 120 V load = 35 A + 12.5 x 120/240 = 41.25 A
    loads = [{"is_motor": True, "amps": 28, "voltage": 240, "phase": 1},
             {"continuous": True, "amps": 10, "voltage": 120, "phase": 1}]
    assert E.feeder_load(loads, 240, 1).value == pytest.approx(41.25)


def test_service_check_bands():
    assert E.service_check(70, 90, 100).status == "good"
    assert E.service_check(90, 100, 100).status == "warn"
    assert E.service_check(120, 125, 100).status == "bad"
    assert E.service_check(50, 60, None).status == "warn"


def test_panel_schedule_columns_match_control_panel_design():
    circuits = E.branch_circuits(E.build_loads(_model()).value).value
    rows = E.panel_schedule(circuits).value
    assert tuple(list(rows[0])[:7]) == E.PANEL_SCHEDULE_COLUMNS
    assert E.PANEL_SCHEDULE_COLUMNS == (
        "tag", "description", "load_a", "voltage", "phase", "control_method", "breaker_a",
    )


# --- branch circuits ------------------------------------------------------------------------


def test_branch_circuit_for_the_5hp_pump():
    circuits = E.branch_circuits(E.build_loads(_model()).value, ambient_f=86).value
    m1 = next(c for c in circuits if c["tag"] == "M1")
    # 430.52 allows 70 A; GFCI breakers stop at 60 A, and 60 A is within 430.52
    assert m1["breaker_max_a"] == 70 and m1["breaker_a"] == 60 and m1["poles"] == 2
    assert m1["conductor_min_a"] == 35.0
    assert m1["conductor"] == "8"  # #8 at 60 C; 150 ft at 28 A is 2.72% on #8, inside 3%
    assert m1["drop_pct"] < 3.0
    assert m1["gfci"] and m1["gfci_basis"] == "680.59"


def test_multi_pump_row_gets_a_circuit_per_unit():
    loads = E.build_loads({}, pumps=[{"id": "p", "hp": 2, "voltage": 230, "qty": 3}]).value
    circuits = E.branch_circuits(loads).value
    assert [c["tag"] for c in circuits if c["is_motor"]] == ["M1", "M2", "M3"]
    assert [c["tag"] for c in circuits if not c["is_motor"]] == ["X1"]  # the control panel


def test_vfd_circuit():
    loads = E.build_loads({"electrical": {"service_v": 208, "phase": 3}},
                          pumps=[{"id": "j", "hp": 10, "control": "vfd", "run_ft": 250}]).value
    c = E.branch_circuits(loads).value[0]
    assert c["control_method"] == "VFD" and c["vfd_hp"] == 10
    assert c["calcs"]["vfd"]["inputs"]["lead_treatment"]["value"] == "load reactor"


# --- transformers, VFD, backup, energy ----------------------------------------------------------


def test_lighting_transformer_1_25x():
    r = E.lighting_transformer(280, 12)
    assert r.inputs["required_va"]["value"] == 350.0 and r.value == 600
    big = E.lighting_transformer(1000, 12)  # 1250 VA -> 2 x 900 VA
    assert big.inputs["units"]["value"] == 2 and big.value == 1800


def test_control_transformer_unchanged():
    assert E.control_transformer_va([{"device": "contactor", "qty": 2}, {"device": "hmi", "qty": 1}]).value == 50
    assert E.control_transformer_va([{"device": "contactor", "qty": 3}]).value == 75


def test_vfd_for_a_nameplate_only_pump_and_a_single_phase_motor():
    loads = E.build_loads({"electrical": {"service_v": 230, "phase": 3}},
                          pumps=[{"id": "n", "fla": 12, "control": "vfd"}]).value
    c = E.branch_circuits(loads).value[0]
    assert c["vfd_hp"] == 5  # smallest Table 430.250 motor covering 12 A at 230 V: 3 HP is 9.6 A, 5 HP 15.2 A
    one = E.build_loads({}, pumps=[{"id": "s", "hp": 2, "voltage": 230, "phase": 1, "control": "vfd"}]).value
    vfd = E.branch_circuits(one).value[0]["calcs"]["vfd"]
    assert any("Single-phase motors" in w for w in vfd["warnings"])


def test_select_vfd_single_phase_input_doubles():
    assert E.select_vfd(10, 208, 3).value == 10
    assert E.select_vfd(10, 230, 1).value == 20
    assert E.select_vfd(5, 460, 3).inputs["output_a"]["value"] == 7.6


def test_backup_with_code_letter():
    loads = E.build_loads({}, pumps=[{"id": "p", "hp": 5, "voltage": 230, "code_letter": "G"}]).value
    plan = E.backup_plan(loads, battery_wh=10000).value
    pump_w = next(ld["watts"] for ld in loads if ld["is_motor"])
    # code G: 6.29 kVA/HP upper bound -> the pump starts at 31,450 W with everything else running
    assert plan["starting_w"] - plan["running_w"] == pytest.approx(5 * 6.29 * 1000 - pump_w, abs=0.2)
    assert plan["generator_w"] == 30000  # 1.25 x 30000 >= 31450 start
    assert plan["battery_hours"] > 0


def test_annual_energy():
    loads = [{"label": "pump", "watts": 1000, "qty": 1, "duty_hours_day": 10}]
    r = E.annual_energy(loads, rate_per_kwh=0.10, days_per_year=365)
    assert r.inputs["total_kwh"]["value"] == 3650.0
    assert r.value == 365.0


# --- Article 680 ------------------------------------------------------------------------------


def test_checklist_flags_high_voltage_luminaire_and_unknowns():
    fixtures = [{"label": "Big light", "voltage": 277, "lens_depth_in": 6}]
    r = E.article680_checklist([], venue="Decorative Fountain", fixtures=fixtures)
    by = {c["key"]: c for c in r.value}
    assert by["luminaire_voltage"]["status"] == "fail"
    assert by["maintenance_disconnect"]["status"] == "verify"
    assert r.status == "bad"
    assert all(c["article"] and c["note"] for c in r.value)


def test_checklist_pool_lens_depth_and_disconnect():
    fixtures = [{"label": "Pool light", "voltage": 12, "lens_depth_in": 12}]
    r = E.article680_checklist([], venue="Spa", fixtures=fixtures, site={"disconnect_ft": 6})
    by = {c["key"]: c for c in r.value}
    assert by["lens_depth"]["status"] == "fail"  # 12 in < 18 in
    assert by["maintenance_disconnect"]["status"] == "pass"
    assert "equipotential_bonding" in by
    # A bonding allowance in the BOM is not a bonding design: still verify.
    bom_only = E.article680_checklist([], venue="Spa", site={"bonding_in_bom": True})
    eq = next(c for c in bom_only.value if c["key"] == "equipotential_bonding")
    assert eq["status"] == "verify" and "allowance" in eq["detail"]


def test_gfci_rules():
    pump = {"is_motor": True, "voltage": 230, "phase": 1, "kind": "pump"}
    assert E.gfci_requirement(pump, "fountain")["required"] is True
    assert E.gfci_requirement(pump, "Spa")["article"] == "680.21(C)"
    lv = {"kind": "lighting_transformer", "voltage": 120}
    assert E.gfci_requirement(lv, "fountain")["required"] is False
    sub480 = {"is_motor": True, "voltage": 480, "phase": 3, "kind": "pump", "submersible": True}
    assert "SPGFCI" in E.gfci_requirement(sub480, "fountain")["note"]
    delta240 = {"is_motor": True, "voltage": 240, "phase": 3, "kind": "pump"}
    assert "SPGFCI" in E.gfci_requirement(delta240, "Spa")["note"]  # 208 V high leg to ground


# --- electrical_report --------------------------------------------------------------------------


REPORT_KEYS = {
    "edition", "verify", "inputs", "loads", "circuits", "low_voltage", "transformers", "feeder", "service",
    "panel_rows", "control_pump_rows", "checklist", "backup", "energy", "bom", "oneline_svg", "warnings",
    "status", "results",
}


def test_report_shape_and_json():
    r = E.electrical_report(_model())
    assert set(r) == REPORT_KEYS
    json.dumps(r)  # JSON-ready for results_json
    assert r["edition"].startswith("NFPA 70")
    assert r["feeder"]["ocpd_a"] and r["feeder"]["conductor"]
    assert r["service"]["available_a"] == 100
    assert r["oneline_svg"].startswith("<svg") and "M1 Feature pump" in r["oneline_svg"]
    assert all(row["section"] == "Electrical" for row in r["bom"])
    assert {"key", "description", "qty", "uom", "item_hint"} <= set(r["bom"][0])
    pump_row = r["control_pump_rows"][0]
    assert pump_row["control_method"] == "Motor Starter" and pump_row["voltage"] == "230"


def test_report_bom_wire_includes_waste():
    r = E.electrical_report(_model())
    m1 = next(c for c in r["circuits"] if c["tag"] == "M1")
    wire = next(b for b in r["bom"] if b["key"] == f"wire:Cu:{m1['conductor']}")
    # M1's two #8 conductors over 150 ft, plus anything else on #8, x 1.1 (its EGC is #10 for 60 A)
    assert wire["qty"] >= round(2 * 150 * 1.1)
    assert m1["egc"] == "10"
    assert any(b["key"].startswith("breaker:60:2:1") for b in r["bom"])
    assert any(b["key"] == "bond:wire" for b in r["bom"])


def test_report_survives_an_ambient_no_insulation_takes():
    m = _model()
    m["electrical"]["ambient_f"] = 200
    r = E.electrical_report(m)
    assert r["feeder"].get("conductor") is None


def test_report_on_an_empty_model_does_not_raise():
    r = E.electrical_report({})
    assert r["loads"] == [] and r["circuits"] == []
    assert r["service"]["status"] == "warn"


def test_report_warns_on_motor_voltage_above_the_service():
    m = _model()
    m["electrical"]["service_v"] = 208
    r = E.electrical_report(m)
    assert any("230 V on a 208 V service" in w for w in r["warnings"])


def test_every_public_function_returns_a_calcresult_with_citations():
    loads = E.build_loads(_model()).value
    circuits = E.branch_circuits(loads).value
    results = [
        E.build_loads(_model()), E.demand_summary(loads), E.motor_branch_ocpd(28), E.continuous_ocpd(10),
        E.size_conductor(20), E.wire_for_load(20, voltage=240), E.egc_size(20), E.voltage_drop(10, 50, "12", 120),
        E.total_drop(1, 2), E.low_voltage_run(8, 4, 1), E.conduit_fill([{"size": "12", "qty": 3}]),
        E.feeder_load(loads, 240), E.feeder_ocpd(loads, 240), E.service_check(10, 20, 100),
        E.panel_schedule(circuits), E.branch_circuits(loads), E.lighting_transformer(100), E.select_vfd(5),
        E.backup_plan(loads), E.generator_size(1000, 2000), E.battery_runtime(1000, 100),
        E.annual_energy(loads), E.article680_checklist(loads), E.electrical_bom(circuits),
        E.total_connected_load(FIKA_LOADS), E.service_main_breaker(FIKA_LOADS),
        E.control_transformer_va([{"device": "relay", "qty": 2}]),
    ]
    for r in results:
        assert isinstance(r, CalcResult), r
        assert r.citations, r.calc
        assert r.formula, r.calc


# --- independent review of #1395 -------------------------------------------------------------


def test_gfci_breaker_capped_at_60a_for_a_5hp_pump():
    # 430.52 allows 70 A, but GFCI breakers stop at 60 A: 60 A is within 430.52 and is the chosen device.
    r = E.electrical_report({"network": {"pumps": [{"id": "p", "hp": 5, "voltage": 230, "phase": 1}]}})
    m1 = next(c for c in r["circuits"] if c["tag"] == "M1")
    assert m1["breaker_max_a"] == 70 and m1["breaker_a"] == 60 and m1["gfci"]
    assert m1["gfci_device"] == "GFCI breaker"
    assert any(b["description"] == "GFCI circuit breaker 2P 60 A" for b in r["bom"])
    assert not any("GFCI circuit breaker 2P 70 A" == b["description"] for b in r["bom"])


def test_pump_circuit_over_60a_is_outside_680_59():
    # 10 HP 230 V single-phase: FLC 50 A, 125% = 62.5 A > 60 A: no GFCI breaker can serve it.
    loads = E.build_loads({}, pumps=[{"id": "p", "hp": 10, "voltage": 230, "phase": 1}]).value
    c = E.branch_circuits(loads).value[0]
    assert c["breaker_a"] == 125 and c["gfci"] is False and "outside 680.59" in c["gfci_note"]
    sub = E.build_loads({}, pumps=[{"id": "s", "hp": 10, "voltage": 230, "phase": 1, "submersible": True}]).value
    s = E.branch_circuits(sub).value[0]
    assert s["gfci"] and s["gfci_device"] == "SPGFCI / GFPE"
    chk = E.article680_checklist(loads, circuits=[c, s])
    gf = next(x for x in chk.value if x["key"] == "gfci_equipment")
    assert gf["status"] == "verify" and "over 60 A" in gf["detail"]


def test_zero_voltage_means_not_entered():
    r = E.electrical_report({"electrical": {"service_v": 0},
                             "network": {"pumps": [{"id": "p", "hp": 4, "voltage": 0, "phase": 1}]}})
    assert r["loads"][0]["voltage"] == 240 and r["loads"][0]["amps"] > 0


def test_valve_supply_follows_coil_plan():
    from erpnext_enhancements.water_engineering.engine.electrical import valve_supply

    # rain-curtain demo: 128 valves x 1.62 W + 3 W logic -> 1.25 x 210.36 = 263 W -> one 350 W supply
    assert valve_supply(128 * 1.62) == {"n": 1, "W": 350}
    # 400 valves x 4 W: 1.25 x 1603 = 2004 W -> three 750 W supplies (two 1000 W units would carry 2000 W)
    assert valve_supply(400 * 4) == {"n": 3, "W": 750}
    # 300 valves x 4 W: 1.25 x 1203 = 1504 W -> two 1000 W units
    assert valve_supply(300 * 4) == {"n": 2, "W": 1000}


def test_motor_feeder_conductor_carries_the_load_not_the_device():
    r = E.electrical_report({"network": {"pumps": [{"id": "p", "hp": 5, "voltage": 240, "phase": 1}]},
                             "electrical": {"service_v": 240, "feeder_ft": 20}})
    f = r["feeder"]
    assert f["load_a"] == pytest.approx(35.0, abs=0.25)  # 1.25 x 28 A, plus the control panel (30 VA)
    assert f["ocpd_a"] == 70
    assert f["conductor"] == "8"  # 40 A at 60 C carries 35 A (was #4 when sized to the 70 A device)
    assert f["egc"] == "8"  # the EGC still follows the 70 A device (Table 250.122)


def test_feeder_raise_past_the_ceiling_is_flagged():
    raised = E.feeder_ocpd([{"is_motor": True, "amps": 1.7}, {"continuous": True, "amps": 100}])
    assert raised.value == 150
    assert any("adjustable-trip" in w for w in raised.warnings)


def test_pool_cord_window():
    r = E.article680_checklist([], venue="Spa", fixtures=[{"label": "x", "voltage": 12, "cord_ft": 2}])
    assert next(c for c in r.value if c["key"] == "cord_length")["status"] == "fail"
    ok = E.article680_checklist([], venue="Spa", fixtures=[{"label": "x", "voltage": 12, "cord_ft": 6}])
    assert next(c for c in ok.value if c["key"] == "cord_length")["status"] == "pass"


def test_60a_gfci_close_to_flc_warns_about_starting():
    # 7.5 HP 230 V single-phase: FLC 40 A, 60 A is 150% of it: an across-the-line start will trip it
    loads = E.build_loads({}, pumps=[{"id": "p", "hp": 7.5, "voltage": 230, "phase": 1}]).value
    c = E.branch_circuits(loads).value[0]
    assert c["breaker_a"] == 60 and c["gfci"] and "likely trip" in c["gfci_note"]
    # 5 HP (FLC 28 A): 60 A is 214% of FLC, no start warning
    five = E.branch_circuits(E.build_loads({}, pumps=[{"id": "p", "hp": 5, "voltage": 230}]).value).value[0]
    assert "likely trip" not in five["gfci_note"]


def test_vfd_gfci_circuit_warns_about_drive_leakage():
    loads = E.build_loads({}, pumps=[{"id": "p", "hp": 5, "voltage": 230, "phase": 1, "control": "vfd"}]).value
    c = E.branch_circuits(loads).value[0]
    assert c["gfci"] and "input filter" in c["gfci_note"]
    assert c["breaker_basis"].startswith("430.122")


def test_valve_supply_adds_the_io_load():
    from erpnext_enhancements.water_engineering.engine.electrical import valve_supply

    # 1.25 x (200 + 3 + 80) = 353.75 W -> past 350 W -> 500 W
    assert valve_supply(200, io_w=80) == {"n": 1, "W": 500}


def test_printed_breaker_is_labeled_a_maximum():
    we = REPO_ROOT / "erpnext_enhancements" / "water_engineering"
    pf = (we / "setup_print_formats.py").read_text(encoding="utf-8")
    assert "Max breaker (A)" in pf and ">Breaker (A)<" not in pf
    assert "Branch breakers round UP" not in pf
    # Since v1.770.0 (phase G) the packet's one-line is this report's own drawing, stored by the derive,
    # which prints the breaker it designed (breaker_a, beside breaker_max_a): no 430.52 maximum to label.
    packet = (we / "packet.py").read_text(encoding="utf-8")
    assert "we_panel_schedule" not in packet and '.get("oneline_svg")' in packet
