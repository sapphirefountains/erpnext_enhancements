"""engine/hydraulics/fittings.py against DRC common.js (tests/golden/drc/fittings.json, 1e-12), the
mapping from the workbook's FITTING_K names, and the equipment curves with the clog factor.

Bench-free pytest suite: python -m pytest erpnext_enhancements/tests/test_hydraulics_fittings.py -q
"""

from erpnext_enhancements.tests.drc_golden import REL, close, golden, num
from erpnext_enhancements.water_engineering.engine.data.fittings import COMPONENT_CURVES, FITTING_K
from erpnext_enhancements.water_engineering.engine.hydraulics.fittings import (
    DRC_KEYS,
    FITTING_K_TO_3K,
    FITTINGS,
    component_loss_ft,
    fit_sum_k,
    fit_sum_k_si,
    fitting_k,
    fitting_k_si,
    resolve_fitting,
)

G = golden("fittings")


def test_drc_keys_in_drc_order():
    assert [DRC_KEYS[k] for k in G["keys"]] == list(FITTINGS)[: len(G["keys"])]


def test_fitting_k_matches_drc():
    assert len(G["fittings"]) > 500
    for row in G["fittings"]:
        key = DRC_KEYS.get(row["key"], row["key"])
        close(fitting_k_si(key, num(row["Re"]), row["idMm"]), row["k"], REL, f"K({row['key']}, {row['Re']}, {row['idMm']})")


def test_fit_sum_k_matches_drc_in_insertion_order():
    assert len(G["fitSums"]) > 100
    for row in G["fitSums"]:
        # DRC keys straight through (resolve_fitting maps them), in the JSON's order.
        close(fit_sum_k_si(row["fit"], row["Re"], row["idMm"], row["clog"]), row["k"], REL, f"sumK({row['fit']}, clog {row['clog']})")


def test_every_workbook_fitting_has_a_home():
    assert set(FITTING_K_TO_3K) == set(FITTING_K)
    for name, key in FITTING_K_TO_3K.items():
        assert key in FITTINGS and resolve_fitting(name) == key
        # Fixed-K entries carry the workbook K unchanged.
        if "k" in FITTINGS[key] and FITTINGS[key].get("source") == "DOC-0049":
            assert FITTINGS[key]["k"] == FITTING_K[name]


def test_three_k_falls_with_size():
    """Darby: a 90 deg elbow is about 0.8 in 3/4" pipe and about 0.5 in 4" at turbulent Re -- the
    workbook's fixed 0.81 overstates large pipe."""
    small = fitting_k_si("elbow_90", 1e5, 0.824 * 25.4)
    large = fitting_k_si("elbow_90", 4e5, 4.026 * 25.4)
    assert 0.7 < small < 0.85 and 0.45 < large < 0.55
    assert FITTING_K["ELL 90"] > large


def test_clog_applies_to_strainers_and_filters_only():
    clean = fit_sum_k_si({"strainer": 1, "elbow_90": 1}, 2e4, 26.6, 0)
    dirty = fit_sum_k_si({"strainer": 1, "elbow_90": 1}, 2e4, 26.6, 0.5)
    assert abs((dirty - clean) - 2.0 * (1 / 0.25 - 1)) < 1e-12
    assert fit_sum_k_si({"elbow_90": 1}, 2e4, 26.6, 0.9) == fit_sum_k_si({"elbow_90": 1}, 2e4, 26.6, 0)


def test_equipment_curves_and_clog():
    h, warn = component_loss_ft("CARTRIDGE FILTER 520 SF CCP CLEAN", 55)
    assert abs(h - (1.8 + 3.1) / 2) < 1e-12 and warn is None
    dirty, _ = component_loss_ft("CARTRIDGE FILTER 520 SF CCP CLEAN", 55, 0.5)
    assert abs(dirty - h * 4) < 1e-12
    grate, _ = component_loss_ft("SUCTION OUTLET COVER/GRATE", 50, 0.5)
    assert grate == 2.5  # not a filter: no clog factor
    _, over = component_loss_ft("CARTRIDGE FILTER 240 SF CCP CLEAN", 120)
    assert over and "rated" in over
    assert component_loss_ft("NOPE", 10)[1]
    assert set(COMPONENT_CURVES)  # the curves stay where they were


def test_envelopes():
    r = fitting_k("ELL 90", 1e5, 2.067)
    assert r.formula.startswith("K = K1/Re") and r.citations and abs(r.value - fitting_k_si("elbow_90", 1e5, 2.067 * 25.4)) < 1e-15
    s = fit_sum_k([{"type": "ELL 90", "qty": 2}, {"key": "ball", "qty": 1}, {"key": "nope", "qty": 1}], 1e5, 2.067)
    assert s.warnings and s.steps and abs(s.value - fit_sum_k_si({"elbow_90": 2, "ball": 1}, 1e5, 2.067 * 25.4)) < 1e-15
