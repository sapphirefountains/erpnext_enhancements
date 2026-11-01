"""engine/hydraulics/segment.py against DRC flow.js (tests/golden/drc/segment.json, 1e-12), the
equipment-curve extension, and the audit cards with the DOC-0049 Hazen-Williams cross-check.

Bench-free pytest suite: python -m pytest erpnext_enhancements/tests/test_hydraulics_segment.py -q
"""

import math

from erpnext_enhancements.tests.drc_golden import REL, close, golden, seg_from_drc
from erpnext_enhancements.water_engineering.engine.hydraulics.catalog import pipe_spec_si
from erpnext_enhancements.water_engineering.engine.hydraulics.segment import (
    STATUS_AGREES,
    STATUS_DIVERGES,
    Segment,
    crosscheck_status,
    segment_loss,
    segment_loss_si,
    segment_parts_si,
    suction_loss,
    suction_loss_si,
    supply_loss,
    supply_loss_si,
)
from erpnext_enhancements.water_engineering.engine.hydraulics.units import G as GRAV
from erpnext_enhancements.water_engineering.engine.hydraulics.units import gpm_to_m3s
from erpnext_enhancements.water_engineering.engine.hydraulics.water import water_si
from erpnext_enhancements.water_engineering.engine.tdh import segment_loss_results

G = golden("segment")


def _clog(c):
    return (c * 100) / 100  # the generator went through clogPct, as DRC's P.plumb does


def test_segment_loss_matches_drc():
    n = 0
    for row in G["segments"]:
        seg = seg_from_drc(row["seg"])
        for ti, t in enumerate(G["WATER"]):
            w = water_si(t)
            for ci, c in enumerate(G["CLOG"]):
                for qi, q in enumerate(G["QS"]):
                    close(segment_loss_si(seg, q, w, c), row["loss"][ti][ci][qi], REL, f"{row['seg']['name']} T{t} clog{c} Q{q}")
                    n += 1
    assert n > 1000


def test_supply_and_suction_match_drc():
    for row in G["supplies"]:
        segs = [seg_from_drc(s) for s in row["supply"]]
        for ti, t in enumerate(G["WATER"]):
            for ci, c in enumerate(G["CLOG"]):
                for qi, q in enumerate(G["QS"]):
                    close(supply_loss_si(segs, q, water_si(t), _clog(c)), row["loss"][ti][ci][qi], REL, f"supply {row['name']}")
    for row in G["suctions"]:
        seg = seg_from_drc(row["suction"])
        for ti, t in enumerate(G["WATER"]):
            for ci, c in enumerate(G["CLOG"]):
                for qi, q in enumerate(G["QS"]):
                    close(suction_loss_si(seg, q, water_si(t), _clog(c)), row["loss"][ti][ci][qi], REL, f"suction {row['name']}")


def test_hand_darcy():
    """100 ft of 2" Sch 40 PVC at 50 GPM, 68 F: V = 4.78 ft/s = 1.457 m/s, Re = 1.457 * 0.0525 / 1.004e-6
    = 76,200, Blasius f = 0.316 Re^-0.25 = 0.0190, so h_f = 0.0190 * 580.6 * 4.78^2 / 64.35 = 3.9 ft."""
    seg = Segment(spec=pipe_spec_si("SCH40 PVC", '2"'), length_m=100 * 0.3048)
    w = water_si(20)
    p = segment_parts_si(seg, gpm_to_m3s(50), w)
    assert abs(p.u / 0.3048 - 4.78) < 0.01
    assert 7.5e4 < p.re < 7.7e4 and 0.0185 < p.f < 0.0195
    h = p.dp / (w.rho * GRAV) / 0.3048
    assert 3.75 < h < 4.05
    # Hand check of the arithmetic itself.
    assert abs(p.dp - p.f * seg.length_m / seg.spec.id_m * w.rho * p.u**2 / 2) < 1e-9 * p.dp


def test_components_add_their_curve_at_the_branch_flow():
    base = Segment(spec=pipe_spec_si("SCH40 PVC", '2"'), length_m=3, branches=2)
    with_filter = Segment(spec=base.spec, length_m=3, branches=2, components=(("CARTRIDGE FILTER 520 SF CCP CLEAN", 1),))
    w = water_si(20)
    q = gpm_to_m3s(110)  # 55 GPM per branch
    extra = segment_loss_si(with_filter, q, w) - segment_loss_si(base, q, w)
    assert abs(extra - 2.45 * 0.3048 * w.rho * GRAV) < 1e-6
    dirty = segment_loss_si(with_filter, q, w, 0.5) - segment_loss_si(base, q, w, 0.5)
    assert abs(dirty - 4 * extra) < 1e-6


def test_zero_and_nan_flow():
    seg = Segment(spec=pipe_spec_si("SCH40 PVC", '2"'), length_m=10)
    w = water_si(20)
    assert segment_loss_si(seg, 0, w) == 0 and segment_loss_si(seg, math.nan, w) == 0
    assert supply_loss_si([seg], 0, w) == 0 and suction_loss_si(None, 1e-3, w) == 0


def test_crosscheck_status():
    assert crosscheck_status(10.5, 10) == (STATUS_AGREES, 0.05)
    status, rel = crosscheck_status(12, 10)
    assert status == STATUS_DIVERGES and abs(rel - 0.2) < 1e-12
    assert crosscheck_status(0, 0)[0] == STATUS_AGREES


ROW = {
    "label": "Discharge", "material": "SCH40 PVC", "nominal_size": '2"', "length_ft": 100,
    "fittings": [{"type": "ELL 90", "qty": 4}, {"type": "BALL VALVE", "qty": 1}, {"key": "coupling", "qty": 3}],
    "components": [{"type": "CARTRIDGE FILTER 520 SF CCP CLEAN", "qty": 1}],
}


def test_segment_loss_cards_have_the_tdh_shape():
    cards = segment_loss(ROW, 50)
    names = [c.calc for c in cards]
    assert names == [
        "Pipe friction (Darcy) — Discharge",
        "Fitting loss (3-K) — Discharge",
        "Component loss — Discharge",
        "Workbook cross-check (Hazen-Williams) — Discharge",
    ]
    for c in cards:
        assert c.unit == "ft" and c.formula and c.steps and c.citations
    # The workbook card is the workbook's own numbers (friction + fixed-K fittings), not a re-derivation.
    wb = segment_loss_results({"flow_gpm": 50, "id_in": 2.067, "length_ft": 100,
                               "fittings": [{"type": "ELL 90", "qty": 4}, {"type": "BALL VALVE", "qty": 1}]})
    assert abs(cards[-1].value - sum(c.value for c in wb)) < 1e-12
    assert any("coupling" in w for w in cards[-1].warnings)  # no workbook K for it
    assert cards[-1].status in (STATUS_AGREES, STATUS_DIVERGES)
    # Component card is the DOC-0049 curve at 50 GPM.
    assert abs(cards[2].value - 1.8) < 1e-9


def test_crosscheck_flags_warm_small_bore():
    """3/4" at 104 F (a spa loop): Darcy sees the lower viscosity, Hazen-Williams cannot."""
    row = {"label": "Jet loop", "material": "SCH40 PVC", "nominal_size": '3/4"', "length_ft": 40, "fittings": []}
    warm = segment_loss(row, 6, water_temp_f=104)[-1]
    cold = segment_loss(row, 6, water_temp_f=50)[-1]
    assert warm.value == cold.value  # the workbook figure has no temperature in it
    darcy_warm = segment_loss(row, 6, water_temp_f=104)[0].value
    darcy_cold = segment_loss(row, 6, water_temp_f=50)[0].value
    assert darcy_warm < darcy_cold


def test_supply_and_suction_envelopes():
    s = supply_loss([ROW, {**ROW, "label": "Riser", "length_ft": 10, "components": []}], 50)
    assert s.unit == "ft" and len(s.steps) == 3 and s.value > 0
    suc = suction_loss({**ROW, "branches": 2}, 50)
    one = suction_loss({**ROW, "branches": 1}, 50)
    assert abs(suc.value - one.value) < 1e-12  # suction is one run


def test_lean_kernel_equals_the_parts():
    seg = Segment(spec=pipe_spec_si("SCH40 PVC", '2"'), length_m=30, branches=2, k_extra=1.2,
                  fittings=(("elbow_90", 3), ("strainer", 1)), components=(("CARTRIDGE FILTER 520 SF CCP CLEAN", 1),))
    w = water_si(30)
    for q in (0.0, 1e-4, 3e-3, 2e-2):
        for clog in (0, 0.4):
            assert segment_loss_si(seg, q, w, clog) == segment_parts_si(seg, q, w, clog).dp


def test_unknown_size_warns_instead_of_hiding_the_default_bore():
    row = {"label": "Typo", "material": "SCH40 PVC", "nominal_size": '1.5"', "length_ft": 50}
    seg = Segment.from_us(row)
    assert seg.spec.id_mm == 25.0 and seg.note
    cards = segment_loss(row, 30)
    assert any("default bore" in w for w in cards[0].warnings)
    assert any("default bore" in w for w in supply_loss([row], 30).warnings)
    assert not Segment.from_us({**row, "id_in": 1.61}).note and not Segment.from_us({**row, "nominal_size": '1-1/2"'}).note


def test_crosscheck_leaves_k_extra_out_of_both_sides():
    base = {"label": "Run", "material": "SCH40 PVC", "nominal_size": '2"', "length_ft": 100, "fittings": [{"type": "ELL 90", "qty": 2}]}
    plain = segment_loss(base, 50)[-1]
    extra = segment_loss({**base, "k_extra": 8}, 50)[-1]
    assert extra.status == plain.status and extra.steps[-1] == plain.steps[-1]
    assert any("K_extra" in w for w in extra.warnings)
