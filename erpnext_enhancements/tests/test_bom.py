"""engine/bom.py: hand goldens for the grouped BOM, the 20 ft cut list, fitting counts, the merge of
other phases' lines, and labor from the house rules.

Bench-free pytest suite: python -m pytest erpnext_enhancements/tests/test_bom.py -q
"""

import math

from erpnext_enhancements.water_engineering.engine.bom import (
    bom_rows,
    cut_list,
    fitting_counts,
    labor_rows,
    line,
    merge_sections,
)

SEGS = [
    {"id": "s1", "material": "SCH40 PVC", "nominal_size": '3"', "length_ft": 8, "fittings": [{"key": "elbow_90", "qty": 2}, {"key": "foot_valve", "qty": 1}]},
    {"id": "s2", "material": "SCH40 PVC", "nominal_size": '2"', "length_ft": 45, "fittings": [{"key": "check", "qty": 1}, {"key": "elbow_90", "qty": 3}],
     "components": [{"key": "filter_ccp520"}]},
    {"id": "s3", "material": "SCH40 PVC", "nominal_size": '2"', "length_ft": 12, "branches": 2, "fittings": [{"key": "ELL 90", "qty": 1}]},
]


def model():
    return {
        "features": [{"id": "f1", "family": "weir", "count": 2}, {"id": "f2", "family": "jets"}],
        "basins": [{"id": "b1", "shape": "rectangular", "length_in": 120, "width_in": 24}],
        "network": {
            "pumps": [{"id": "p1", "item": "PUMP-XF-15", "control": "vfd", "bypass_in": 0}],
            "segments": SEGS,
            "terminals": [{"id": "t1", "kind": "orifice", "params": {"rated_gpm": 6, "rated_head_ft": 20, "count": 8, "item": "NZ-CASCADE-1"}},
                          {"id": "t2", "kind": "manifold", "params": {"material": "SCH40 PVC", "nominal_size": '1-1/2"', "count": 12, "pitch_in": 6,
                                                                       "rated_gpm": 2, "rated_head_ft": 15}}],
        },
        "surge": {"arrestors": [{"pump": "p1", "gas_l": 2, "precharge_psi": 30}]},
        "equipment": [{"slot": "autofill", "item": "AF-100", "qty": 1}],
    }


def by_key(lines):
    return {ln["key"]: ln for ln in lines}


def test_cut_list_in_20_ft_sticks():
    cuts = cut_list(SEGS)
    s2 = next(c for c in cuts if c["segment"] == "s2")
    assert s2["sticks"] == 3 and s2["couplings"] == 2 and s2["pieces_ft"] == [20, 20, 5]
    assert sum(1 for c in cuts if c["segment"] == "s3") == 2  # two branches, each cut on its own
    assert next(c for c in cuts if c["segment"] == "s1")["couplings"] == 0


def test_fitting_counts_group_by_type_size_material():
    f = {(x["key"], x["nominal_size"]): x["qty"] for x in fitting_counts(SEGS)}
    assert f[("elbow_90", '2"')] == 3 + 1 * 2  # s2's three, plus one on each of s3's two branches (workbook name resolved)
    assert f[("elbow_90", '3"')] == 2
    assert f[("coupling", '2"')] == 2  # 45 ft of 2" is three sticks
    assert f[("check", '2"')] == 1


def test_bom_rows():
    v = bom_rows(model(), rules={"labor_hours_weir": 16, "labor_hours_jets": 10, "labor_hours_per_sqft_basin": 0.5}).value
    k = by_key(v["lines"])
    # 2": 45 + 2 x 12 = 69 ft, +10% = 75.9 -> 76 ft
    assert k['pipe|SCH40 PVC|2"']["qty"] == math.ceil(69 * 1.1) == 76
    assert k['pipe|SCH40 PVC|3"']["qty"] == math.ceil(8 * 1.1)
    # The manifold header: 12 x 6 in = 6 ft plus 2 x 12 cm overhang, +10%
    assert k['pipe|SCH40 PVC|1-1/2"']["qty"] == math.ceil((6 + 0.24 / 0.3048) * 1.1)
    assert k['fitting|check|2"|PVC Sch 40']["section"] == "Valves"
    assert k['fitting|foot_valve|3"|PVC Sch 40']["section"] == "Valves"
    assert k["nozzle|NZ-CASCADE-1"]["qty"] == 8
    assert k["control|vfd"]["qty"] == 1 and k["arrestor|2"]["section"] == "Equipment"
    assert k["component|filter_ccp520"]["qty"] == 1 and k["equipment|autofill|AF-100"]["qty"] == 1
    assert k["labor|family|weir"]["qty"] == 32 and k["labor|family|jets"]["qty"] == 10
    assert k["labor|basin"]["qty"] == 0.5 * 20
    assert k["consumable|pvc_cement"]["uom"] == "qt"
    sections = [ln["section"] for ln in v["lines"]]
    assert sections == sorted(sections, key=["Piping", "Valves", "Nozzles", "Equipment", "Consumables", "Labor"].index)
    for ln in v["lines"]:
        assert set(ln) == {"section", "key", "description", "qty", "uom", "item_hint"}


def test_merge_sections_folds_other_phases_lines():
    ours = [line("Piping", "pipe|SCH40 PVC|2\"", "PVC 2\"", 76, "ft"), line("Equipment", "control|vfd", "VFD", 1, "ea")]
    electrical = [line("Electrical", "wire|12awg", "12 AWG THWN", 300, "ft"), line("Equipment", "control|vfd", "VFD 2 hp", 1, "ea")]
    structural = [{"section": "Structural", "key": "concrete", "description": "Concrete 4000 psi", "qty": 3.5, "uom": "cy", "item_hint": {}}]
    merged = merge_sections(ours, electrical, structural)
    k = {(ln["section"], ln["key"]): ln for ln in merged}
    assert k[("Equipment", "control|vfd")]["qty"] == 2 and k[("Equipment", "control|vfd")]["description"] == "VFD"
    assert [ln["section"] for ln in merged] == ["Piping", "Equipment", "Electrical", "Structural"]


def test_no_labor_rules_is_flagged():
    lines, checks = labor_rows(model(), None)
    assert lines == [] and checks[0]["code"] == "bom.no_labor_rules"
