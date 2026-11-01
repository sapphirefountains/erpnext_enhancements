"""Vault lids, gratings, trench drains, deck beams, building floors and
equipment and piping supports, by hand; and the takeoff with a full
``structure`` block.

Run: python -m pytest erpnext_enhancements/tests/test_structural_supports.py -q
"""

import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.water_engineering.engine.data.rebar import bar
from erpnext_enhancements.water_engineering.engine.structural import (
    building_floor_load,
    deck_beam,
    equipment_pad,
    grating,
    lid_slab,
    mooring,
    nominal_size,
    penetration_detail,
    seismic_bracing,
    structural_takeoff,
    thrust_block,
    trench_drain,
)


def approx(x, rel=1e-9):
    return pytest.approx(x, rel=rel)


def test_lid_slab_pedestrian_by_hand():
    # 6 ft span: L/20 = 3.6 -> 4 in, 6 in floor; Mu = 1.2 × 75 × 36/8 + 1.6 × 100 × 36/8 = 1,125 lb·ft/ft
    r = lid_slab(6, None, "pedestrian")
    v = r.value
    assert v["thickness_in"] == 6.0 and v["min_thickness_in"] == 4.0
    assert v["mu_lb_ft"] == approx(1.2 * 75 * 36 / 8 + 1.6 * 100 * 36 / 8)
    # #4 at 18 in: As = 0.1333, d = 6 − 2 − 0.25 = 3.75, a = 0.1333 × 60000 / (0.85 × 4500 × 12)
    as_ = 0.2 * 12 / 18
    a = as_ * 60000 / (0.85 * 4500 * 12)
    phi_mn = 0.9 * as_ * 60000 * (3.75 - a / 2) / 12
    assert phi_mn >= v["mu_lb_ft"]  # 2,224 ≥ 1,125, and 0.1333 ≥ As,min 0.0018 × 12 × 6 = 0.1296
    assert (v["bar"], v["spacing_in"]) == (4, 18.0)


def test_lid_slab_h20_by_hand():
    # 6 ft span, truck: impact 50/131 = 0.382 -> 0.30; E = 4 + 0.36 = 4.36 ft; ML = 16000 × 1.3 × 6/4 / 4.36
    r = lid_slab(6, 8, "vehicle")
    assert r.value["m_live_lb_ft"] == approx(16000 * 1.3 * 6 / 4 / 4.36)
    assert r.status == "ok" and r.value["thickness_in"] >= 8


def test_grating_pedestrian_by_hand():
    # 30 in span, 100 psf: w per bar = 100/144 × 1.1875 lb/in; the lightest 3/4 × 1/8 bar
    r = grating(30, "pedestrian", 20)
    v = r.value
    w = 100 / 144 * 1.1875
    s_bar, i_bar = 0.125 * 0.75 ** 2 / 6, 0.125 * 0.75 ** 3 / 12
    assert v["stress_psi"] == approx(w * 900 / 8 / s_bar)
    assert v["deflection_in"] == approx(5 * w * 30 ** 4 / (384 * 29e6 * i_bar))
    assert v["bearing_bar"] == "0.75 × 0.125 in"
    assert v["weight_psf"] == approx(12 / 1.1875 * 0.75 * 0.125 / 144 * 490 * 1.1)
    assert grating(30, "vehicle").value["depth_in"] > 0.75


def test_trench_drain():
    r = trench_drain(12, "vehicle")
    assert r.value["en1433_class"] == "D400" and r.value["pieces"] == math.ceil(144 / 39.37)
    assert trench_drain(5, "pedestrian").value["heel_proof"] is True


def test_deck_beam_steel_and_concrete():
    steel = deck_beam(10, 4, 20, 100)
    assert steel.status == "ok" and steel.value["w_plf"] == approx(480.0)
    conc = deck_beam(10, 4, 20, 100, "concrete")
    assert conc.value["h_in"] == 12.0  # L/16 = 7.5 in -> 12 in minimum


def test_building_floor_load():
    assert building_floor_load(182, None, "roof").status == "bad"
    assert building_floor_load(80, None, "lobby").status == "ok"
    assert building_floor_load(80).status == "warn"


def test_equipment_pad_by_hand():
    # 30 × 20 in pump, 400 lb, cg 14 in, SDS 1.0, four 1/2 in 316 SS cast-in anchors 4 in deep in a 6 in pad
    r = equipment_pad(30, 20, 400, 14, 1.0)
    v = r.value
    fp = min(max(0.4 * 1.0 * 400 / 2.5, 0.3 * 400), 1.6 * 400)  # 0.3·SDS·W floor governs: 120 lb
    s = 20 - 4.0
    t_total = max((fp * 14 - (0.9 - 0.2) * 400 * s / 2) / s, 0)
    assert v["fp_lb"] == approx(fp)
    assert v["tension_per_anchor_lb"] == approx(t_total / 2)
    assert v["shear_per_anchor_lb"] == approx(fp / 4)
    nb = 24 * math.sqrt(4500) * 4.0 ** 1.5
    assert v["phi_nn_lb"] == approx(min(0.70 * nb * 0.75, 0.75 * 0.142 * 75000))
    vb = 7 * (4.0 / 0.5) ** 0.2 * math.sqrt(0.5) * math.sqrt(4500) * 8.0 ** 1.5  # ca1 = 6 + 2 in, le = hef = 4
    assert v["phi_vcb_lb"] == approx(0.70 * vb) and v["phi_vcp_lb"] == approx(0.70 * 2.0 * nb)
    assert v["phi_vn_lb"] == approx(min(0.65 * 0.6 * 0.142 * 75000, 0.70 * vb, 0.70 * 2.0 * nb))
    assert v["pad_length_in"] == 42 and v["concrete_cuft"] == approx(42 * 32 * 6 / 1728)
    assert r.status == "ok"


def test_thrust_block_by_hand():
    # 4 in at 150 psi, 90° bend: T = 150 × π × 4.5²/4 × 2 sin 45° = 3,374 lb; area = T × 1.5 / 1000
    r = thrust_block("4", 150, "90")
    t = 150 * math.pi * 4.5 ** 2 / 4 * 2 * math.sin(math.radians(45))
    assert r.value["thrust_lb"] == approx(t)
    assert r.value["bearing_area_sqft"] == approx(t * 1.5 / 1000)
    assert thrust_block('2"', 100, "tee").value["thrust_lb"] == approx(100 * math.pi * 2.375 ** 2 / 4)


def test_bracing():
    assert seismic_bracing(2, 100, "D").value["exempt"] is True
    r = seismic_bracing(3, 60, "D", material="pvc").value
    assert (r["lateral_braces"], r["longitudinal_braces"]) == (math.ceil(60 / 20) + 1, math.ceil(60 / 40))
    assert seismic_bracing(4, 60, "B").value["exempt"] is True


def test_mooring_by_hand():
    # 6 ft² above water at 90 mph, 4 ft² below at 0.5 ft/s, line at 45°
    r = mooring(6, 4)
    wind = 0.00256 * 90 ** 2 * 1.2 * 6
    cur = 0.5 * 1.94 * 1.0 * 4 * 0.25
    h = wind + cur
    w_sub = 1.5 * (h / 0.5 + h * 1.0)
    assert r.value["horizontal_lb"] == approx(h)
    assert r.value["anchor_submerged_lb"] == approx(w_sub)
    assert r.value["anchor_dry_lb"] == approx(w_sub * 150 / (150 - 62.4))


def test_sizes_and_penetrations():
    assert nominal_size('1-1/2"') == 1.5 and nominal_size("3/4") == 0.75 and nominal_size(4) == 4.0
    assert penetration_detail('3"', 8).value["waterstop_ring_ft"] == approx(math.pi * 3.5 / 12)


def test_takeoff_runs_the_whole_structure():
    model = {
        "site": {"ss": 1.5, "s1": 0.55, "site_class": "D", "soil_class": 4},
        "features": [{"id": "f1", "family": "weir", "label": "Sheet", "inputs": {"tolerance_in": 0.0625}}],
        "basins": [{"id": "b1", "label": "Pool", "shape": "rectangular", "length_in": 120, "width_in": 72,
                    "depth_in": 18, "slab_in": 6, "wall_in": 8},
                   {"id": "v1", "label": "Vault", "kind": "vault", "shape": "rectangular", "length_in": 96,
                    "width_in": 72, "depth_in": 84, "lid": {"load_class": "vehicle"}}],
        "network": {"nodes": [{"id": "src", "kind": "basin", "basin": "b1"}, {"id": "p1", "kind": "pump"}],
                    "pumps": [{"id": "p1"}], "segments": [{"id": "s1", "from": "src", "to": "p1",
                                                           "nominal_size": '3"'}]},
        "structure": {
            "walls": [{"id": "w1", "stem_height_ft": 4, "retained": "soil", "surcharge": "plaza", "length_ft": 30,
                       "soil_class": 3}],
            "frames": [{"id": "fr", "height_ft": 10, "width_ft": 8, "header_plf": 60}],
            "veneer": [{"id": "vn", "area_sqft": 80, "thickness_in": 1.25}],
            "gratings": [{"id": "g", "span_in": 30, "area_sqft": 20}],
            "pads": [{"id": "pd", "length_in": 30, "width_in": 20, "weight_lb": 400, "cg_height_in": 14}],
            "rigging": {"hung_lb_per_ft": 4, "hung_length_ft": 8, "hanger_spans": 3, "hanger_span_ft": 2.6,
                        "mount": "flown"},
        },
    }
    t = structural_takeoff(model)
    assert t["seismic"]["value"]["sdc"] == "D"
    assert {"hydrodynamic", "lids", "weir_lips", "walls", "frames", "veneer", "gratings", "pads", "rigging"} <= \
        set(t["structure"])
    bom = {line["key"]: line for line in t["bom"]}
    assert bom["structural.steel"]["uom"] == "Pound" and bom["structural.steel"]["qty"] > 0
    assert bom["structural.grating.pedestrian"]["qty"] == 20.0
    assert bom["structural.veneer_anchors"]["qty"] == 40
    assert bom["structural.anchors"]["qty"] == 4  # the pad's anchors, not the generic count as well
    assert bom["structural.hatch"]["qty"] == 1
    # the pipe through the pool wall adds a waterstop ring to the strip waterstop
    shells_only = structural_takeoff({k: v for k, v in model.items() if k != "structure"})
    ws = {line["key"]: line for line in shells_only["bom"]}["structural.waterstop"]["qty"]
    assert ws == pytest.approx(34.67 + 0.0 + 31.33 + math.pi * 3.5 / 12, abs=0.02)
    # wall concrete and rebar flow into the site concrete and the #4 line
    assert bom["structural.concrete_site"]["qty"] > 0
    assert bom["structural.rebar.#4.lb"]["qty"] > {line["key"]: line for line in shells_only["bom"]}[
        "structural.rebar.#4.lb"]["qty"]
    for group in t["structure"].values():
        for row in group:
            assert row["result"]["citations"], row
            assert "not a stamped structural design" in " ".join(row["result"]["warnings"])
    assert bar(4)["lb_per_ft"] == 0.668


def test_odd_inputs_are_reported_not_guessed():
    pad = equipment_pad(30, 20, 400, 14, 1.0, anchor_dia_in=0.4375)
    assert any("checked as 0.375 in" in w for w in pad.warnings)
    assert thrust_block(4, 150, "reducer").status == "incomplete"
    t = structural_takeoff({"structure": {"thrust_blocks": [{"id": "x", "size": "unsized", "pressure_psi": 100}],
                                          "bracing": [{"id": "y", "size": "tbd", "run_ft": 40}]}})
    assert [c["code"] for c in t["checks"]].count("STRUCT_PIPE_SIZE") == 2


def test_pad_without_seismic_says_so():
    r = equipment_pad(30, 20, 400, 14, None)
    assert r.status == "warn" and any("not checked for uplift" in w for w in r.warnings)


def test_pads_add_to_the_anchors_they_do_not_cover():
    # two anchored units (a pump and a filter) and one listed pad: 1 × 4 generic + the pad's own 4
    model = {"network": {"pumps": [{"id": "p1"}]}, "equipment": [{"slot": "filter", "qty": 1}],
             "structure": {"pads": [{"id": "pd", "length_in": 30, "width_in": 20, "weight_lb": 400}]}}
    t = structural_takeoff(model)
    assert {line["key"]: line for line in t["bom"]}["structural.anchors"]["qty"] == 8
    assert t["totals"]["anchors"] == 8


def test_moorings_of_different_weights_stay_on_their_own_lines():
    t = structural_takeoff({"structure": {"moorings": [{"id": "a", "above_water_sqft": 6, "submerged_sqft": 4},
                                                       {"id": "b", "above_water_sqft": 30, "submerged_sqft": 20}]}})
    keys = [line["key"] for line in t["bom"] if line["key"].startswith("structural.mooring_blocks")]
    assert len(keys) == 2
