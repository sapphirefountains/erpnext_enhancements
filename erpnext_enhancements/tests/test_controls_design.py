"""Bench-free pytest suite for the controls package (phase L, PR 2): pump control
topology, drive frames, the Splash Wizard controller, panel design, outputs and
``controls_report``. Hand goldens from NEMA ICS 2, NEC 430.32 and the worked
examples in each docstring.

Run: python -m pytest erpnext_enhancements/tests/test_controls_design.py -q
"""

import ast
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.water_engineering.engine import controls as C
from erpnext_enhancements.water_engineering.engine.envelope import CalcResult

WE = REPO_ROOT / "erpnext_enhancements" / "water_engineering"
PKG = WE / "engine" / "controls"


def _model(**over):
    m = {
        "site": {"air_temp_f": 95, "elevation_ft": 4500, "venue_type": "Decorative Fountain"},
        "basins": [{"id": "b1", "shape": "rectangular", "length_in": 120, "width_in": 24}],
        "features": [{"id": "f1", "family": "jets", "wind_control": True}],
        "network": {"pumps": [
            {"id": "p1", "label": "Circulation pump", "hp": 3, "voltage": 230, "phase": 3, "role": "circulation"},
            {"id": "p2", "label": "Jet pump", "hp": 10, "voltage": 230, "phase": 3, "run_ft": 150},
        ]},
        "lighting": {"fixtures": [{"label": "Uplights", "watts": 35, "qty": 8, "voltage": 12}]},
        "electrical": {"service_v": 240, "phase": 3, "available_service_a": 200,
                       "solenoid_valves": {"qty": 12, "watts_each": 4}},
        "control": {"auto_transfer": True, "location": "outdoor", "available_fault_ka": 10},
    }
    m.update(over)
    return m


# --- the package boundary and the old paths ---------------------------------------------


def test_package_never_imports_frappe_or_hydraulics():
    for path in PKG.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for n in names:
                assert not n.startswith("frappe") and "hydraulics" not in n, f"{path.name} imports {n}"


def test_old_controls_names_still_import():
    from erpnext_enhancements.water_engineering.engine import calc_lighting, calc_solenoid_relays
    from erpnext_enhancements.water_engineering.engine.controls import (
        DEFAULT_INTERLOCKS,
        DEFAULT_IO_POINTS,
        SPA_INTERLOCKS,
        SPA_IO_POINTS,
        lighting_sizing,
    )

    assert calc_lighting([{"qty": 2, "watts_each": 50}]).value == 2  # 100 W / 60 W per relay
    assert calc_solenoid_relays(6).value == 6
    assert DEFAULT_INTERLOCKS and DEFAULT_IO_POINTS and SPA_INTERLOCKS and SPA_IO_POINTS
    assert lighting_sizing([{"qty": 1, "watts_each": 60}])["relay_count"] == 1


# --- the 5 HP, 230 V, single-phase pump ----------------------------------------------------


def test_5hp_1ph_nema_size_is_1p_or_2():
    r = C.nema_size(5, 230, 1)
    assert r.value == "1P"  # size 1 stops at 3 HP at 230 V single-phase; 1P carries 5 HP
    assert r.inputs["alternate_size"]["value"] == "2"
    assert C.nema_size(3, 230, 1).value == "1"
    assert C.nema_size(10, 460, 3).value == "1"
    assert C.nema_size(15, 230, 3).value == "2"


def test_5hp_1ph_iec_contactor_and_overload():
    assert C.iec_contactor(28).value == 32  # AC-3 frame at or above FLC 28 A
    ol = C.overload_relay(26.5, service_factor=1.15)
    assert ol.value == "23-32 A"
    assert ol.inputs["setting_a"]["value"] == 26.5
    assert ol.inputs["max_trip_a"]["value"] == pytest.approx(33.12, abs=0.01)  # 125% (430.32(A)(1))
    assert C.overload_relay(26.5, service_factor=1.0).inputs["max_trip_a"]["value"] == pytest.approx(30.48, abs=0.01)
    assert C.overload_relay(26.5, submersible=True, nema=True).inputs["trip_class"]["value"] == 10
    assert C.overload_relay(26.5, nema=True).inputs["trip_class"]["value"] == 20


def test_5hp_vfd_frame_with_single_phase_input_and_altitude():
    # 5 HP 3-phase motor at 230 V: 15.2 A. Single-phase input doubles it to 30.4 A.
    sea = C.vfd_frame(5, 230, 1, elevation_ft=0, ambient_f=104)
    assert sea.value == 10  # 10 HP class carries 30.8 A
    # At 4,500 ft: 1 - (1200 / 330) x 1% = 0.9636 -> 31.55 A, past 30.8 A -> 15 HP
    utah = C.vfd_frame(5, 230, 1, elevation_ft=4500, ambient_f=104)
    assert utah.inputs["altitude_factor"]["value"] == pytest.approx(0.9636, abs=1e-4)
    assert utah.inputs["required_a"]["value"] == pytest.approx(31.55, abs=0.01)
    assert utah.value == 15
    # Three-phase input at sea level and 40 C: the 5 HP class (16.7 A) covers 15.2 A
    assert C.vfd_frame(5, 230, 3, elevation_ft=0, ambient_f=104).value == 5


def test_vfd_ambient_derate_and_limit():
    # 113 F = 45 C: 5 C over 40 C at 2% per C -> 0.90
    r = C.vfd_frame(5, 230, 3, elevation_ft=0, ambient_f=113)
    assert r.inputs["ambient_factor"]["value"] == pytest.approx(0.90, abs=1e-3)
    assert any("50 C" in w for w in C.vfd_frame(5, 230, 3, ambient_f=130).warnings)


def test_vfd_bypass_contactor_size():
    r = C.vfd_frame(10, 230, 3, elevation_ft=0, bypass=True)
    assert r.inputs["bypass_contactor_a"]["value"] == 32  # FLC 28 A -> 32 A AC-3


# --- the bypass interlock truth table ------------------------------------------------------


def test_bypass_truth_table_has_no_violations():
    t = C.bypass_truth_table()
    assert t.status == "good"
    assert len(t.value) == 2 * 3 * 2 ** 5
    assert all(not (row["K2"] and row["K3"]) for row in t.value)


@pytest.mark.parametrize(
    "state,expected",
    [
        (dict(selector="VFD", hoa="AUTO", run_cmd=True), dict(K1=True, K2=True, K3=False, drive_run=True)),
        (dict(selector="VFD", hoa="AUTO", run_cmd=False), dict(K1=True, K2=False, K3=False, drive_run=False)),
        (dict(selector="BYPASS", hoa="HAND"), dict(K1=False, K2=False, K3=True, drive_run=False)),
        (dict(selector="BYPASS", hoa="HAND", bypass_overload=True), dict(K3=False)),
        (dict(selector="VFD", hoa="AUTO", run_cmd=True, drive_fault=True), dict(K2=False, K3=False, mode="VFD")),
        (dict(selector="VFD", hoa="AUTO", run_cmd=True, drive_fault=True, auto_transfer=True),
         dict(K1=False, K2=False, K3=True, mode="BYPASS", transferred=True)),
        (dict(selector="BYPASS", hoa="HAND", estop=True), dict(K3=False, motor_running=False)),
        (dict(selector="VFD", hoa="OFF", run_cmd=True), dict(K2=False, motor_running=False)),
    ],
)
def test_bypass_logic_rows(state, expected):
    out = C.bypass_logic(**state)
    for k, v in expected.items():
        assert out[k] == v, (state, k)


# --- the recommender ----------------------------------------------------------------------


def test_recommend_topology():
    small = {"label": "small", "hp": 2, "phase": 3}
    assert C.recommend_topology(small).value == "across_the_line"
    assert C.recommend_topology({**small, "hp": 20}).value == "soft_starter"
    assert C.recommend_topology({**small, "hp": 5}, site={"generator": True}).value == "soft_starter"
    assert C.recommend_topology(small, {"wind_control": True}).value == "vfd"
    assert C.recommend_topology({**small, "hp": 10}, {"show_modulation": True}).value == "vfd_bypass"
    circ = {**small, "role": "circulation", "control": "vfd"}
    r = C.recommend_topology(circ)
    assert r.value == "vfd_bypass" and any("circulation" in s for s in r.steps)
    assert sum(o.recommended for o in r.options) == 1
    one = C.recommend_topology({"label": "1ph", "hp": 2, "phase": 1}, {"wind_control": True})
    assert any("three-phase motor" in w for w in one.warnings)


def test_pump_control_components():
    pc = C.pump_control({"tag": "M2", "label": "Jet", "hp": 10, "voltage": 230, "phase": 3}, "vfd_bypass",
                        auto_transfer=True).value
    devices = [c["device"] for c in pc["components"]]
    assert {"Line contactor (drive input)", "Output contactor (drive output)", "Bypass contactor (across the line)",
            "Overload relay (bypass path)", "Selector switch VFD / OFF / BYPASS",
            "Selector switch Hand-Off-Auto"} <= set(devices)
    assert any(i["action"] == "Transfer M2 to bypass" for i in pc["interlocks"])
    atl = C.pump_control({"tag": "M1", "label": "Circ", "hp": 3, "voltage": 230, "phase": 3}, "across_the_line").value
    assert atl["contactor"] == 12 and atl["overload"]["range"] == "7-10 A"
    assert atl["starting_a"] == pytest.approx(6 * 9.6)


# --- controller -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "valves,part,expansions",
    [(6, "500624", []), (9, "500625", []), (12, "500625", ["500630"]), (16, "500625", ["500631"]),
     (20, "500625", ["500631", "500630"])],
)
def test_splash_wizard_part_by_valves(valves, part, expansions):
    io = [{"point_name": "Solenoid valves", "io_type": "Output", "signal": "24VDC", "qty": valves,
           "control_mechanism": "Solid State Relay", "device": "valve"}]
    v = C.select_controller(io).value
    assert v["platform"] == "Splash Wizard" and v["part_no"] == part
    assert [e["part_no"] for e in v["expansions"]] == expansions
    assert C.select_controller(io, family="MAX").value["part_no"] in ("500628", "500629")


def test_industrial_when_outside_sdp001():
    io = [{"point_name": "M1 speed", "io_type": "Output", "signal": "0-10V", "qty": 1, "device": "drive"}]
    r = C.select_controller(io)
    assert r.value["platform"] == "Industrial"
    assert any(o.key == "splash_wizard" for o in r.options)


def test_io_list_and_relays():
    pcs = [C.pump_control({"tag": "M1", "label": "Jet", "hp": 5, "voltage": 230, "phase": 3}, "vfd").value]
    model = _model()
    io = C.io_list(model, pcs).value
    names = {r["point_name"]: r for r in io}
    assert names["M1 speed"]["signal"] == "0-10V"
    assert names["Solenoid valves"]["qty"] == 12 and names["Solenoid valves"]["control_mechanism"] == "Solid State Relay"
    assert names["Wind sensor"]["signal"] == "Reed Switch Pulse"
    assert names["E-stop"]["signal"] == "Dry Contact NC"
    assert names["Lighting"]["qty"] == 5  # 280 W / 60 W per relay (DOC-0126)
    rel = C.relay_counts(io).value
    assert rel["ssr"] == 12 + 5 + 1  # valves + lighting + autofill
    assert rel["interposing"] == 1


def test_dc_supply_hand_sum():
    io = [
        {"point_name": "Solenoid valves", "io_type": "Output", "signal": "24VDC", "qty": 6,
         "control_mechanism": "Solid State Relay", "device": "valve"},
        {"point_name": "Water level low", "io_type": "Input", "signal": "Dry Contact NO", "qty": 1,
         "device": "Float switch / level controller", "powered": True},
        {"point_name": "E-stop", "io_type": "Input", "signal": "Dry Contact NC", "qty": 1,
         "device": "E-stop pushbutton"},  # an unpowered contact: draws nothing
    ]
    r = C.dc_supply(io, valve_w_each=10)
    # 6 x 10 W + 6 SSR x 0.3 + 1 sensor x 1 + controller 5 = 67.8 W ; x1.25 = 84.75 -> 120 W
    assert r.inputs["load_w"]["value"] == pytest.approx(67.8)
    assert r.value == 120


# --- panel ------------------------------------------------------------------------------------


def test_control_transformer_va_sum():
    pcs = [C.pump_control({"tag": "M1", "label": "P1", "hp": 2, "voltage": 230, "phase": 3}, "across_the_line").value,
           C.pump_control({"tag": "M2", "label": "P2", "hp": 2, "voltage": 230, "phase": 3}, "across_the_line").value]
    r = C.panel_control_transformer(pcs, relays=2)
    # 2 contactors x 20 + 4 pilot lights x 2 + 2 relays x 3 + HMI 10 = 64 VA -> 75 VA
    assert r.inputs["sealed_va_total"]["value"] == 64.0
    assert r.value == 75
    with_psu = C.panel_control_transformer(pcs, relays=2, psu_input_w=90)  # + 90 W / 0.9 pf = 100 VA
    assert with_psu.inputs["sealed_va_total"]["value"] == 164.0 and with_psu.value == 200


def test_enclosure_thermal_fails_and_asks_for_a_fan():
    # 16 x 14 x 7 in wall box: exposed 546 in^2 = 0.352 m^2 ; indoors at 86 F the rise allowed is 18 F
    # -> 5.5 x 0.352 x 10 C = 19.4 W, far under 150 W ; fan = 3.17 x 150 / 18 = 26.4 -> 27 CFM
    r = C.enclosure_thermal([16, 14, 7], 150, ambient_f=86, outdoor_sun=False)
    assert r.status == "bad"
    assert r.inputs["capacity_w"]["value"] == pytest.approx(19.4, abs=0.1)
    assert r.inputs["fan_cfm"]["value"] == 27
    hot = C.enclosure_thermal([16, 14, 7], 150, ambient_f=100, outdoor_sun=True)  # 115 F > 104 F inside
    assert hot.inputs["ac_btu_h"]["value"] == 563 and hot.inputs["fan_cfm"]["value"] is None
    assert C.enclosure_thermal([36, 30, 12], 50, ambient_f=86, outdoor_sun=False).status == "good"


def test_enclosure_type_and_size():
    assert C.enclosure_type("outdoor").value == "NEMA 3R"
    assert C.enclosure_type("outdoor", near_chemicals=True).value == "NEMA 4X"
    assert C.enclosure_type("indoor").value == "NEMA 12"
    small = C.enclosure_size([], breaker_poles=4, terminals=20, relays=6, psu_w=60, xfmr_va=75, spd=False,
                             disconnect=False)
    # 12 + 12 + 9 + 8 + 12 + 60 = 113 in^2 x 1.3 x 1.2 = 176: past 16 x 14 (168) -> 20 x 16
    assert small.inputs["required_in2"]["value"] == pytest.approx(176.3, abs=0.1)
    assert small.value == "20 x 16 x 8 in"
    # 12 + 6 + 6 + 8 + 60 = 92 in^2 x 1.56 = 143.5: past 16 x 12 (140), inside 16 x 14 (168)
    fit = C.enclosure_size([], breaker_poles=4, terminals=10, relays=4, psu_w=60, spd=False, disconnect=False)
    assert fit.value == "16 x 14 x 7 in"
    assert fit.inputs["part"]["value"]["backplane_part_no"] == "500656"


def test_sccr_lowest_governs():
    parts = [{"name": "M1 contactor", "kind": "motor_controller_0_50hp"}, {"name": "terminals", "kind": "terminal_block"},
             {"name": "drive", "kind": "vfd", "ka": 65}]
    r = C.sccr(parts, available_fault_ka=10)
    assert r.value == 5 and r.status == "bad"
    assert C.sccr(parts, available_fault_ka=5).status == "good"
    assert C.sccr(parts).status == "warn"


def test_main_disconnect():
    # 1.15 x (9.6 + 28) + 5 = 48.2 A ; feeder OCPD 60 A governs -> 60 A switch
    assert C.main_disconnect([9.6, 28], other_a=5, feeder_ocpd_a=60).value == 60
    assert C.main_disconnect([9.6, 28], other_a=5, feeder_ocpd_a=40).value == 60
    assert C.main_disconnect([80], feeder_ocpd_a=90).value == 100


# --- outputs ------------------------------------------------------------------------------------


def _child_fields(doctype):
    data = json.loads((WE / "doctype" / doctype / f"{doctype}.json").read_text(encoding="utf-8"))
    return {f["fieldname"] for f in data["fields"] if f["fieldtype"] not in ("Section Break", "Column Break")}


def test_control_panel_rows_match_child_tables():
    r = C.controls_report(_model())
    cp = r["control_panel"]
    for key, doctype in [("pumps", "control_pump"), ("lights", "control_light"), ("fuses", "control_fuse"),
                         ("interlocks", "control_interlock"), ("io_points", "control_io_point")]:
        fields = _child_fields(doctype)
        assert cp[key], key
        for row in cp[key]:
            assert set(row) <= fields, (key, set(row) - fields)
    parent = _child_fields("control_panel_design")
    assert set(cp["parent"]) <= parent, set(cp["parent"]) - parent


def test_theory_of_operation_mentions_the_design():
    r = C.controls_report(_model())
    t = r["theory_of_operation"]
    assert "manual bypass" in t and "interlocked" in t and "anemometer" in t and "E-stop" in t


def test_panel_bom_shape_and_part_numbers():
    m = _model()
    m["network"]["pumps"] = [{"id": "p1", "label": "Feature pump", "hp": 2, "voltage": 230, "phase": 3}]
    m["features"] = []
    m["control"] = {"location": "indoor"}
    r = C.controls_report(m)
    assert r["controller"]["platform"] == "Splash Wizard"
    lines = r["bom"]
    assert all(ln["section"] == "Controls" for ln in lines)
    assert {"key", "description", "qty", "uom", "item_hint", "part_no"} <= set(lines[0])
    parts = {ln["part_no"] for ln in lines if ln["part_no"]}
    assert {"500625", "500630", "500615", "500616"} <= parts  # 12 valves: 9-valve Basic + 6-valve expansion + WP-40


# --- the report and the drawings -------------------------------------------------------------------


REPORT_KEYS = {
    "pumps", "vfds", "controller", "io_points", "relays", "dc_supply", "interlocks", "panel", "bom",
    "control_panel", "theory_of_operation", "power_svg", "ladder_svg", "warnings", "status", "results",
}


def test_controls_report_shape():
    r = C.controls_report(_model())
    assert set(r) == REPORT_KEYS
    json.dumps(r)
    tops = {p["tag"]: p["topology"] for p in r["pumps"]}
    assert tops == {"M1": "across_the_line", "M2": "vfd_bypass"}  # wind control drives the jet pump only
    assert r["vfds"][0]["tag"] == "M2"
    assert r["panel"]["sccr_status"] == "bad"  # 10 kA available against a 5 kA default panel
    assert r["power_svg"].startswith("<svg") and "M2-K3 bypass" in r["power_svg"]
    assert r["ladder_svg"].startswith("<svg") and "Wind stage 2" in r["ladder_svg"]


def test_controls_report_on_an_empty_model():
    r = C.controls_report({})
    assert r["pumps"] == [] and r["controller"]["platform"] == "Splash Wizard"
    assert r["power_svg"].startswith("<svg") and "M1" not in r["power_svg"]


def test_every_public_function_returns_a_calcresult():
    pcs = [C.pump_control({"tag": "M1", "label": "P", "hp": 2, "voltage": 230, "phase": 3}).value]
    io = C.io_list(_model(), pcs).value
    for r in [
        C.nema_size(2), C.iec_contactor(10), C.overload_relay(8), C.soft_starter(20), C.recommend_topology({"hp": 1}),
        C.bypass_truth_table(), C.pump_control({"hp": 1}), C.vfd_frame(5), C.io_list({}), C.relay_counts(io),
        C.dc_supply(io), C.select_controller(io), C.interlocks(), C.panel_control_transformer(pcs),
        C.enclosure_type(), C.enclosure_size(pcs), C.enclosure_thermal([24, 20, 8], 100), C.panel_heat([50]),
        C.sccr([{"name": "x", "kind": "vfd"}]), C.main_disconnect([10]), C.terminals_and_wire([], io),
        C.surge_protection(240, 1), C.panel_bom(pcs), C.control_panel_rows(pcs, []), C.theory_of_operation(pcs),
    ]:
        assert isinstance(r, CalcResult) and r.citations and r.formula, r.calc


# --- review fixes ------------------------------------------------------------------------------------


def test_drive_frame_uses_the_enclosure_interior_not_the_sun():
    # 10 HP at 4,500 ft: 28 / 0.9636 = 29.06 A at a 104 F interior -> the 10 HP class (30.8 A), not 15 HP
    r = C.controls_report(_model())
    assert r["vfds"][0]["frame"] == 10


def test_auto_transfer_only_on_circulation_pumps():
    m = _model()
    m["network"]["pumps"][0]["control"] = "vfd"  # the circulation pump on a drive -> VFD with bypass
    r = C.controls_report(m)
    il = {i["condition"]: i["action"] for p in r["pumps"] for i in p["interlocks"]}
    assert il["M1 drive fault"] == "Transfer M1 to bypass"  # circulation
    assert il["M2 drive fault"] == "Stop M2; alarm"  # feature pump: no automatic full-speed start


def test_regulated_venue_keeps_the_splash_wizard_without_analog_monitoring():
    m = _model(site={"regulated": True, "elevation_ft": 4500})
    m["network"]["pumps"] = [{"id": "p1", "label": "Circ", "hp": 2, "voltage": 230, "phase": 3}]
    m["features"] = []
    m["control"] = {}
    r = C.controls_report(m)
    assert not any(p["signal"] == "4-20mA" for p in r["io_points"])
    assert any(p["point_name"] == "Chemical controller status" for p in r["io_points"])
    m["control"] = {"analog_monitoring": True}
    assert C.controls_report(m)["controller"]["platform"] == "Industrial"


def test_control_pump_row_carries_the_nameplate_fla():
    m = _model()
    m["network"]["pumps"][1]["fla"] = 26.5
    r = C.controls_report(m)
    row = next(p for p in r["control_panel"]["pumps"] if p["function"] == "Jet pump")
    assert row["fla_amps"] == 26.5


def test_nema_contactor_amps_drive_the_footprint():
    pc = C.pump_control({"tag": "M1", "label": "Big", "hp": 30, "voltage": 230, "phase": 3}, "across_the_line",
                        nema=True).value
    m = next(c for c in pc["components"] if c["device"] == "Contactor")
    assert m["rating"] == "NEMA size 3" and m["amps"] == 90
    assert C.enclosure_size([pc]).inputs["breakdown_in2"]["value"]["contactors"] == 24.0


def test_role_survives_a_multi_unit_pump_row():
    m = _model()
    m["network"]["pumps"] = [{"id": "c", "label": "Circ", "hp": 2, "voltage": 230, "phase": 3, "qty": 2,
                              "role": "circulation", "control": "vfd"}]
    r = C.controls_report(m)
    assert [p["role"] for p in r["pumps"]] == ["circulation", "circulation"]
    assert all(p["topology"] == "vfd_bypass" for p in r["pumps"])
