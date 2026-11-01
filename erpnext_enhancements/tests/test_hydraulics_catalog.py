"""engine/hydraulics/catalog.py against DRC common.js with the DOC-0049 rows patched in
(tests/golden/drc/catalog.json, 1e-12), and the reconciliation with engine/data/pipe_specs.py.

Bench-free pytest suite: python -m pytest erpnext_enhancements/tests/test_hydraulics_catalog.py -q
"""

from erpnext_enhancements.tests.drc_golden import REL, close, golden
from erpnext_enhancements.water_engineering.engine.data.pipe_specs import PIPE_PRESSURE_SPECS, PIPE_SPECS
from erpnext_enhancements.water_engineering.engine.hydraulics.catalog import (
    MATERIALS,
    normalize_nominal,
    pipe_spec,
    pipe_spec_si,
    resolve_material,
    sizes_for,
)

G = golden("catalog")
DRC_TO_PY = {m["drc"]: k for k, m in MATERIALS.items()}


def test_golden_doc0049_table_is_the_engines():
    """The table drc.mjs patched into DRC must be engine/data/pipe_specs.py, row for row."""
    assert set(G["doc0049"]) == {"SCH40 PVC", "SCH80 PVC", "Type K Copper"}
    for material, rows in G["doc0049"].items():
        assert len(rows) == len(PIPE_PRESSURE_SPECS[material])
        for nps, od, wall, bore in rows:
            spec = PIPE_PRESSURE_SPECS[material][f'{nps}"']
            assert (spec["od_in"], spec["wall_in"], spec["id_in"]) == (od, wall, bore), (material, nps)


def test_pipe_specs_tables_agree():
    """DOC-0049's two tables (SUPPORT velocity limits, Pipe Specs ratings) carry the same IDs."""
    for material, sizes in PIPE_SPECS.items():
        for size, row in sizes.items():
            assert PIPE_PRESSURE_SPECS[material][size]["id_in"] == row["id_in"], (material, size)
            od, wall = PIPE_PRESSURE_SPECS[material][size]["od_in"], PIPE_PRESSURE_SPECS[material][size]["wall_in"]
            assert abs(od - 2 * wall - row["id_in"]) < 1e-9, (material, size)


def test_materials_match_drc():
    for key, m in G["materials"].items():
        py = MATERIALS[DRC_TO_PY[key]]
        assert (py["eps_mm"], py["e_gpa"], py["density"]) == (m["epsMm"], m["eGPa"], m["density"]), key


def test_pipe_spec_matches_drc():
    assert len(G["pipes"]) > 60
    for row in G["pipes"]:
        spec = pipe_spec_si(row["mat"], row["nps"], row["idFb"], row["wallFb"])
        out = row["out"]
        what = f"pipeSpec({row['mat']}, {row['nps']})"
        assert spec.material == DRC_TO_PY.get(out["mat"], "Custom"), what
        assert (spec.nominal is None) == (out["nps"] is None), what
        for py, js in (("od_mm", "odMm"), ("id_mm", "idMm"), ("wall_mm", "wallMm"), ("eps_mm", "epsMm"),
                       ("e_gpa", "eGPa"), ("density", "density"), ("id_m", "idM"), ("area", "area")):
            close(getattr(spec, py), out[js], REL, f"{what}.{js}")


def test_sizes_for_matches_drc_where_drc_tables_are_kept():
    for key, rows in G["sizes"].items():
        py = sizes_for(key)
        assert [r["nps"] for r in py] == [r["nps"] for r in rows], key
        doc = MATERIALS[DRC_TO_PY[key]]["source"].startswith("DOC-0049")
        for p, j in zip(py, rows, strict=False):
            close(p["od_mm"], j["odMm"], REL, f"{key} {p['nps']} od")
            if not doc:
                assert p["id_mm"] == j["idMm"], f"{key} {p['nps']}: toFixed(2) {p['id_mm']} vs {j['idMm']}"


def test_doc0049_bore_governs():
    s = pipe_spec_si("SCH40 PVC", '2"')
    assert abs(s.id_mm - 2.067 * 25.4) < 1e-12 and s.max_discharge_fps == 6.5 and s.psi_73f == 166
    assert pipe_spec_si("pvc40", "2").id_mm == s.id_mm
    # DRC's own figure would have been 60.3 - 2 * 3.91 = 52.48 mm.
    assert abs(s.id_mm - 52.48) > 0.02
    steel = pipe_spec_si("steel40", "4")
    assert steel.id_mm == 4.026 * 25.4 and steel.psi_73f is None and steel.max_discharge_fps is None
    assert pipe_spec_si("Type K Copper", '1"').max_discharge_fps == 4.9
    assert pipe_spec_si("Type L Copper", '1"').max_discharge_fps == 4.9  # copper's erosion limit
    assert pipe_spec_si("SCH40 PVC", '1/2"').nominal is None  # not in DOC-0049: a custom bore


def test_name_normalization():
    assert normalize_nominal('1-1/2"') == "1-1/2" and normalize_nominal("2 in") == "2" and normalize_nominal("1 1/4") == "1-1/4"
    assert normalize_nominal(None) is None and normalize_nominal("") is None
    assert resolve_material("pvc40") == "SCH40 PVC" and resolve_material("sch80 pvc") == "SCH80 PVC"
    assert resolve_material("nope") is None and resolve_material(None) is None


def test_pipe_spec_envelope():
    r = pipe_spec("SCH40 PVC", '2"')
    assert abs(r.value["id_in"] - 2.067) < 1e-12 and r.value["max_suction_fps"] == 4.5 and not r.warnings
    assert r.formula and r.steps and r.citations
    assert pipe_spec("SCH40 PVC", '7"', id_in=6.9).warnings
    assert abs(pipe_spec("SCH40 PVC", '7"', id_in=6.9).value["id_in"] - 6.9) < 1e-12
