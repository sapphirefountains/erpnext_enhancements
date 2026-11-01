"""Interactive, cascade, pondless, curtain, mist and aerator families, plus the system modules
(lighting, winterization, heat) and the family registry.

Bench-free pytest. Expected values are the industry report's sourced figures: Utah
R392-302-41 (3-minute tank, 30-minute turnover, 20 ft/s), Aquascape's 125 GPH per inch, the
pondless reservoir multipliers, WaterShapes' strand and screen flows, Kasco's HP per acre,
Crystal's lighting rules, SLC's 30 in frost depth; heat loss against its textbook formulas.

Run: python -m pytest erpnext_enhancements/tests/test_families_others.py -q
"""

import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.water_engineering.engine.families import (
    FAMILIES,
    SYSTEM_MODULES,
    aerator,
    cascade,
    catalog,
    curtain,
    family_for_type,
    heat,
    interactive,
    lighting,
    mist,
    pondless,
    run_feature,
    winterization,
)
from erpnext_enhancements.water_engineering.engine.families.base import (
    BLOCKER,
    INFO,
    PASS,
    SEVERITIES,
    WARNING,
)
from erpnext_enhancements.water_engineering.engine.feature import tiered_fountain_flow


def codes(checks):
    return {c.code: c.severity for c in checks}


# --- interactive ----------------------------------------------------------------------

ROWS = [{"label": "A", "gpm": 10, "count": 4}, {"label": "B", "gpm": 10, "count": 4}, {"label": "C", "gpm": 10, "count": 4}]
SEQ = [{"open": "A"}, {"open": "B"}, {"open": "C"}, {"open": "A, C"}]


def plaza(**inputs):
    return {"id": "ip", "family": "interactive", "inputs": dict({"play_features": ROWS, "sequence": SEQ}, **inputs)}


def test_feature_pump_is_sized_to_the_worst_sequence_step():
    res = interactive.flow(plaza())
    assert res.data["all_on_gpm"] == pytest.approx(120.0)
    assert res.value == pytest.approx(80.0)  # step A + C
    assert res.data["worst_step"] == 4
    no_seq = interactive.flow(plaza(sequence=[]))
    assert no_seq.value == pytest.approx(120.0) and no_seq.warnings


def test_utah_tank_is_three_minutes_of_combined_pump_flow():
    assert interactive.tank_required_gal(80.0, 40.0) == pytest.approx(360.0)
    res = interactive.flow(plaza(filter_gpm=40))
    assert res.data["tank_required_gal"] == pytest.approx(3 * (80 + 40))


def test_utah_turnover_is_thirty_minutes():
    ok = codes(interactive.checks(plaza(system_volume_gal=1200, filter_gpm=40)))
    assert ok["IWF_TURNOVER"] == PASS
    bad = codes(interactive.checks(plaza(system_volume_gal=1300, filter_gpm=40)))
    assert bad["IWF_TURNOVER"] == BLOCKER
    # With no filter flow entered, the filter is sized to the turnover.
    assert interactive.flow(plaza(system_volume_gal=1200)).data["filter_gpm"] == pytest.approx(40.0)


def test_utah_checklist_pass_fail_and_unanswered():
    f = plaza(system_volume_gal=1200, filter_gpm=40, inlet_count=3, tank_gal=400, deck_width_ft=3,
              deck_slope_pct=1.5, orp_ph_controller=True, secondary_disinfection=False, separate_loop=True,
              level_controller=True, night_fc=5)
    c = codes(interactive.checks(f, site={"jurisdiction": "UT", "regulated": True}))
    assert c["IWF_INLETS"] == BLOCKER
    assert c["IWF_TANK"] == PASS
    assert c["IWF_DECK"] == PASS
    assert c["IWF_SLOPE"] == BLOCKER
    assert c["IWF_ORP_PH"] == PASS
    assert c["IWF_SECONDARY"] == BLOCKER
    assert c["IWF_LIGHT"] == PASS
    assert c["IWF_DRAIN_SPHERE"] == INFO  # not answered: never a silent pass
    assert c["IWF_FEATURE_FILTER_RATIO"] == PASS  # 80 : 40 = 2:1


def test_outside_utah_failures_are_warnings():
    c = codes(interactive.checks(plaza(inlet_count=2), site={"jurisdiction": "AZ", "regulated": False}))
    assert c["IWF_INLETS"] == WARNING


def test_nozzle_velocity_openings_and_foggers():
    rows = [{"label": "jet", "gpm": 10, "count": 1, "orifice_in": 0.25, "opening_in": 0.75},
            {"label": "fog", "gpm": 1, "count": 1, "fogger": True}]
    c = codes(interactive.checks({"id": "x", "inputs": {"play_features": rows, "fogger_potable": False}}))
    assert c["IWF_VELOCITY"] == BLOCKER  # 10 gpm through 1/4 in ~ 65 ft/s
    assert c["IWF_OPENINGS"] == BLOCKER
    assert c["IWF_FOGGER"] == BLOCKER


def test_feature_to_filter_ratio_over_three_to_one():
    c = codes(interactive.checks(plaza(sequence=[], filter_gpm=30)))
    assert c["IWF_FEATURE_FILTER_RATIO"] == WARNING  # 120 : 30


def test_interactive_terminal_is_a_manifold_with_the_sequence():
    t = interactive.terminal(plaza())
    assert t["kind"] == "manifold"
    assert len(t["params"]["takeoffs"]) == 12
    assert len(t["params"]["sequence"]) == 4


# --- cascade ---------------------------------------------------------------------------


def casc(tiers, look="average", **inputs):
    return {"id": "c1", "family": "cascade", "inputs": dict({"tiers": tiers}, **inputs), "effect": {"look": look}}


def test_aquascape_gph_per_foot_bands():
    # 1,500 GPH/ft residential (= 125 GPH per inch); 3,000 commercial; 2,400 for a 1 in sheet.
    assert cascade.flow(casc([{"spill_width_in": 12}], "light")).value == pytest.approx(1500 / 60)
    assert cascade.flow(casc([{"spill_width_in": 12}], "heavy")).value == pytest.approx(3000 / 60)
    assert cascade.flow(casc([{"spill_width_in": 12}], "average")).value == pytest.approx(2400 / 60)
    assert cascade.flow(casc([{"spill_width_in": 1}], "light")).data["tiers"][0]["gph"] == pytest.approx(125.0)


def test_the_tightest_tier_governs():
    res = cascade.flow(casc([{"label": "top", "spill_width_in": 12}, {"label": "low", "spill_width_in": 24}]))
    assert res.data["governing_tier"] == "low"
    assert res.value == pytest.approx(2 * 2400 / 60)


def test_a_round_tier_keeps_the_doc0049_rim_rule():
    # The migrated Tiered Fountain: 36 in rim at 0.5 gpm/ft is what tiered_fountain_flow gives.
    old = tiered_fountain_flow([{"diameter_in": 36}], 0.5).value
    assert cascade.flow(casc([{"diameter_in": 36, "gpm_per_ft": 0.5}])).value == pytest.approx(old)
    assert cascade.flow(casc([{"diameter_in": 36}], "average")).value == pytest.approx(math.pi * 3 * 4.0)


def test_drain_back_overflows_the_lowest_basin():
    f = casc([{"spill_width_in": 24, "pool_area_sqft": 40}, {"spill_width_in": 24, "pool_area_sqft": 40}],
             stream_length_ft=20, stream_width_ft=2, stream_depth_in=3)
    d = cascade.flow(f).data
    # Two 1 in sheets over 40 sq ft + 20 x 2 x 3 in of stream.
    assert d["drain_back_gal"] == pytest.approx((2 * 40 * 1 / 12 + 20 * 2 * 3 / 12) * 7.480519)
    small = {"shape": "rectangular", "length_in": 48, "width_in": 48, "depth_in": 18, "freeboard_in": 1}
    assert codes(cascade.checks(f, {"basin": small}))["CASCADE_DRAINBACK"] == BLOCKER
    big = dict(small, freeboard_in=12, length_in=120, width_in=120)
    assert "CASCADE_DRAINBACK" not in codes(cascade.checks(f, {"basin": big}))


def test_pond_turnover_and_liner():
    f = casc([{"spill_width_in": 12}], pond_volume_gal=6000, pond_length_ft=10, pond_width_ft=8, pond_depth_ft=2)
    d = cascade.flow(f).data
    assert d["pond_turnover_h"] == pytest.approx(6000 / 40 / 60)
    assert d["liner_ft"] == (16.0, 14.0)  # L + 2D + 2 ft (house rule)
    assert codes(cascade.checks(f))["CASCADE_POND_TURNOVER"] == WARNING


# --- pondless --------------------------------------------------------------------------


def test_pondless_reservoir_multipliers():
    base = {"kind": "stream", "spill_width_in": 12, "stream_length_ft": 10, "stream_width_ft": 2, "stream_depth_in": 2}
    stream = 10 * 2 * 2 / 12 * 7.480519
    for fill, mult in (("open", 3), ("matrix", 4), ("gravel", 12)):
        d = pondless.flow({"id": "p", "inputs": dict(base, fill=fill)}).data
        assert d["stream_gal"] == pytest.approx(stream)
        assert d["reservoir_required_gal"] == pytest.approx(mult * stream)
    assert pondless.flow({"id": "p", "inputs": base}).value == pytest.approx(12 * 125 / 60)
    c = codes(pondless.checks({"id": "p", "inputs": dict(base, fill="gravel", reservoir_gal=150)}))
    assert c["PONDLESS_RESERVOIR"] == WARNING
    roles = {line["role"] for line in pondless.flow({"id": "p", "inputs": base}).data["kit_lines"]}
    assert {"pump", "reservoir", "check_valve", "spillway", "liner"} <= roles


# --- curtain ---------------------------------------------------------------------------


def test_curtain_strand_lace_and_screen_flows():
    assert curtain.flow({"id": "c", "inputs": {"style": "strand", "width_ft": 6, "strand_count": 30}}).value == pytest.approx(9.9)
    lace = curtain.flow({"id": "c", "inputs": {"style": "lace", "width_ft": 6, "strand_count": 40}})
    assert lace.data["gpm_per_strand"] == pytest.approx(0.5 / 3.785411784)
    assert curtain.flow({"id": "c", "inputs": {"style": "screen", "width_ft": 10}}).value == pytest.approx(5.0)
    assert curtain.flow({"id": "c", "inputs": {"style": "screen_double", "width_ft": 10}}).value == pytest.approx(10.0)


def test_digital_curtain_links_to_the_app_and_adds_no_flow():
    f = {"id": "c", "inputs": {"style": "digital", "width_ft": 8}}
    res = curtain.flow(f)
    assert res.value is None and res.data["link"]["repo"] == "sapphirefountains/digital-rain-curtain"
    assert codes(curtain.checks(f))["CURTAIN_DIGITAL_APP"] == INFO
    assert curtain.terminal(f)["kind"] == "fixed_flow"


def test_curtain_hard_water_and_lace_spacing():
    f = {"id": "c", "inputs": {"style": "lace", "width_ft": 6, "spacing_in": 1.0}}
    c = codes(curtain.checks(f, site={"hardness_ppm": 250}))
    assert c["CURTAIN_SCALING"] == WARNING
    assert c["CURTAIN_LACE_SPACING"] == WARNING


# --- mist ------------------------------------------------------------------------------


def test_mist_count_times_gpm_and_potable_supply():
    f = {"id": "m", "inputs": {"nozzle_count": 20, "orifice_in": "0.012"}}
    assert mist.flow(f).value == pytest.approx(0.56)
    assert mist.flow({"id": "m", "inputs": {"nozzle_count": 20, "orifice_in": "0.008"}}).value == pytest.approx(0.40)
    c = codes(mist.checks({"id": "m", "inputs": {"nozzle_count": 20, "supply": "reservoir", "line_rating_psi": 600}}))
    assert c["MIST_NOT_POTABLE"] == BLOCKER
    assert c["MIST_LINE_RATING"] == BLOCKER


# --- aerator ---------------------------------------------------------------------------


def test_aerator_hp_per_acre_by_purpose():
    # Kasco: AF 1 HP/acre, JF display ~2, CF 3/4; Otterbine 2 for water quality.
    for purpose, band in (("aeration", 1.0), ("display", 2.0), ("circulation", 0.75), ("water_quality", 2.0)):
        assert aerator.flow({"id": "a", "inputs": {"surface_acres": 3, "purpose": purpose}}).value == pytest.approx(3 * band)
    c = codes(aerator.checks({"id": "a", "inputs": {"surface_acres": 2, "purpose": "display", "unit_hp": 2}}))
    assert c["AERATOR_HP_LOW"] == WARNING


def test_long_ponds_get_several_units_and_a_cable_run():
    d = aerator.flow({"id": "a", "inputs": {"length_ft": 1000, "width_ft": 100, "shore_power_ft": 50}}).data
    assert d["acres"] == pytest.approx(100000 / 43560)
    assert d["units"] == 4  # aspect 10 over the house rule of 3
    assert d["cable_run_ft"] == pytest.approx(100.0)
    one = codes(aerator.checks({"id": "a", "inputs": {"length_ft": 1000, "width_ft": 100, "units": 1}}))
    assert one["AERATOR_LONG_POND"] == WARNING


# --- system modules --------------------------------------------------------------------


def model_with(features, **extra):
    return dict({"site": {"jurisdiction": "UT"}, "features": features, "basins": [], "network": {}, "lighting": {}}, **extra)


def test_lighting_fixtures_beam_and_transformer():
    jet12 = {"id": "j", "family": "jets", "inputs": {"height_ft": 12, "orifice_diameter_in": 0.5}, "count": 1}
    d = lighting.calc(model_with([jet12])).data
    row = d["effects"][0]
    assert row["fixtures"] >= 2  # >= 2 lights on a stream over 4 ft
    assert row["beam"] == "narrow" and row["beam_deg"] == 13.0  # over 10 ft
    assert d["va_required"] == pytest.approx(d["watts"] * 1.25)
    jet3 = dict(jet12, inputs={"height_ft": 3, "orifice_diameter_in": 0.5})
    assert lighting.calc(model_with([jet3])).data["effects"][0]["beam"] == "wide"


def test_lighting_gfci_and_voltage_checks():
    weir8 = {"id": "w", "family": "weir", "inputs": {"length_ft": 8}}
    line = codes(lighting.checks(model_with([weir8], lighting={"system_volts": 120})))
    assert line["LIGHT_GFCI"] == WARNING
    low = codes(lighting.checks(model_with([weir8], lighting={"system_volts": 12, "listed_low_voltage": True})))
    assert "LIGHT_GFCI" not in low
    hi = codes(lighting.checks(model_with([weir8], lighting={"system_volts": 208, "gfci": True})))
    assert hi["LIGHT_OVER_150V"] == BLOCKER
    xf = codes(lighting.checks(model_with([weir8], lighting={"transformer_va": 20, "gfci": True})))
    assert xf["LIGHT_TRANSFORMER"] == WARNING


def test_winterization_checklist_and_frost_depth():
    m = model_with(
        [{"id": "s", "family": "interactive", "inputs": {}}],
        network={"pumps": [{"id": "p1", "location": "room"}],
                 "segments": [{"id": "s1", "drain": True}, {"id": "s2", "buried_depth_in": 18},
                              {"id": "s3", "buried_depth_in": 36}, {"id": "s4"}]},
    )
    d = winterization.calc(m).data
    assert d["frost_depth_in"] == 30  # SLC default
    keys = {i["key"] for i in d["winterize"]}
    assert {"autofill", "seg_s1", "seg_s2", "seg_s3", "nozzles_s"} <= keys
    c = codes(winterization.checks(m))
    assert c["WINTER_LINE_ABOVE_FROST"] == WARNING  # s2 at 18 in
    assert c["WINTER_DRAIN_UNKNOWN"] == INFO  # s4
    assert c["WINTER_ROOM_UNHEATED"] == INFO


def test_surface_heat_loss_against_the_formulas():
    a, tw, ta, rh = 100.0, 80.0, 50.0, 50.0

    def psat_inhg(f):
        t = (f - 32) * 5 / 9
        return 0.61121 * math.exp((18.678 - t / 234.5) * (t / (257.14 + t))) * 0.2953

    evap = a * (psat_inhg(tw) - rh / 100 * psat_inhg(ta)) * 95.0  # still air
    conv = 5.7 * 0.17611 * a * (tw - ta)
    tsky_r = 0.0552 * ((ta - 32) * 5 / 9 + 273.15) ** 1.5 * 1.8
    rad = 0.95 * 0.1714e-8 * a * ((tw + 459.67) ** 4 - tsky_r**4)
    got = heat.surface_heat_loss(a, tw, ta, rh, 0.0)
    assert got["evaporation"] == pytest.approx(evap, rel=1e-3)
    assert got["convection"] == pytest.approx(conv, rel=1e-3)
    assert got["radiation"] == pytest.approx(rad, rel=1e-3)
    # Order of magnitude: ~0.1 lb/h per sq ft evaporating from a warm pool.
    assert got["evaporation"] / 1050 / a == pytest.approx(0.077, abs=0.02)


def test_heat_reports_doc0049_beside_the_surface_balance():
    m = {"site": {"water_temp_f": 80, "air_temp_f": 50},
         "basins": [{"shape": "rectangular", "length_in": 120, "width_in": 120, "depth_in": 18}],
         "treatment": {"heat": {"rh_pct": 50, "wind_mph": 5}}}
    d = heat.calc(m).data
    assert d["surface_btu_hr"] > 0 and d["doc0049_btu_hr"] > 0
    assert d["doc0049"]["calc"] == "heating_load"
    assert heat.checks(m)


# --- registry --------------------------------------------------------------------------


def test_every_family_implements_the_interface():
    for key, m in FAMILIES.items():
        assert m.FAMILY == key
        for fn in ("inputs", "flow", "checks", "terminal"):
            assert callable(getattr(m, fn)), (key, fn)
        empty = {"id": "x", "family": key, "inputs": {}}
        res = m.flow(empty)  # an empty feature never raises
        assert res.to_dict()["data"] is not None
        for c in m.checks(empty):
            assert c.severity in SEVERITIES
    for key, m in SYSTEM_MODULES.items():
        for fn in ("inputs", "calc", "checks"):
            assert callable(getattr(m, fn)), (key, fn)


def test_every_citation_is_a_url_or_a_named_source():
    for key, m in FAMILIES.items():
        for name in dir(m):
            if name.startswith("CIT_"):
                text = getattr(m, name)
                assert "http" in text or "Sapphire standard" in text or "DOC-" in text or "Industry report" in text \
                    or "textbook" in text or "digital-rain-curtain" in text, (key, name)


def test_family_for_type_maps_the_old_types():
    assert family_for_type("Weir") == "weir"
    assert family_for_type("Spilling Weir") == "weir"
    assert family_for_type("Waterwall") == "weir"
    assert family_for_type("Nozzle Array") == "jets"
    assert family_for_type("Orifice Nozzle") == "jets"
    assert family_for_type("Splash Pad") == "interactive"
    assert family_for_type("Rain Curtain") == "curtain"
    assert family_for_type("Tiered Fountain") == "cascade"
    assert family_for_type("Koi pond waterfall") == "cascade"


def test_run_feature_and_catalog_are_json_ready():
    import json

    out = run_feature({"id": "w", "family": "weir", "inputs": {"length_ft": 4, "fall_ft": 2}})
    json.dumps(out)
    assert out["terminal"]["kind"] == "open_discharge"
    assert run_feature({"id": "z", "family": "nope"})["checks"][0]["code"] == "UNKNOWN_FAMILY"
    json.dumps(catalog())
