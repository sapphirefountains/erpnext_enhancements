"""Bench-free tests for the Water Feature Design controller (schema 2, v1.768.0).

Imports the controller under the shared frappe stub (so a syntax/import error fails CI without a
site) and exercises what it does on a save with a fake doc: ``write_derive`` (rollups, the BOM
with Manual lines kept, the JSON fields, kit/family/mode, the Draft/Derived status),
``acks_from_rows``, ``compute_completion_percent`` from the readiness, ``recompute`` on a legacy
model, and the conversion patch's ``legacy_inputs`` + ``engine.model.migrate`` (``legacy_inputs`` left
the controller for ``patches/convert_water_feature_designs_to_model.py`` in v1.771.0, when phase H
retired the schema-1 child DocTypes and left that patch its only reader).
The real ``doc.save()`` is covered only on a bench.

Run: python -m unittest erpnext_enhancements.tests.test_water_design_controller -v
"""

import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

wfd = None


def setUpModule():
    global wfd
    from erpnext_enhancements.tests.test_assistant_tools_schema import install_stubs

    install_stubs()
    import frappe

    frappe.utils.cint = lambda v=0: int(float(v or 0))
    frappe.utils.flt = lambda v=0, precision=None: float(v or 0)
    frappe.utils.escape_html = lambda s: str(s)
    frappe.log_error = lambda *a, **k: None
    frappe.get_traceback = lambda *a, **k: ""
    if not hasattr(frappe, "whitelist"):
        frappe.whitelist = lambda *a, **k: (lambda f: f)

    model = sys.modules.setdefault("frappe.model", types.ModuleType("frappe.model"))
    doc_mod = sys.modules.setdefault("frappe.model.document", types.ModuleType("frappe.model.document"))

    class Document:
        pass

    doc_mod.Document = Document
    model.document = doc_mod
    frappe.model = model

    from erpnext_enhancements.water_engineering.doctype.water_feature_design import (
        water_feature_design as module,
    )

    wfd = module


class FakeRow:
    """Attribute access with a None default (mimics a child-row docfield read)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __getattr__(self, _name):
        return None


class FakeDoc(FakeRow):
    def get(self, key, default=None):
        value = self.__dict__.get(key)
        return default if value is None else value

    def set(self, key, value):
        if isinstance(value, list):
            value = [v if isinstance(v, FakeRow) else FakeRow(**v) for v in value]
        self.__dict__[key] = value

    def append(self, key, row):
        self.__dict__.setdefault(key, [])
        if self.__dict__[key] is None:
            self.__dict__[key] = []
        self.__dict__[key].append(FakeRow(**row))


def derive_out(**over):
    out = {
        "model": {"schema": 2, "mode": "quick", "kit": "lobby-sheet-wall-8ft",
                  "site": {"venue_type": "Decorative Fountain", "governing_code": ""},
                  "features": [{"id": "f1", "family": "weir"}], "acks": []},
        "results": {"weir_flow|f1": {"value": 80}},
        "values": {"features": {"f1": {"flow_gpm": 80}}},
        "rollups": {"design_flow_gpm": 80, "operating_flow_gpm": 79.9, "tdh_ft": 24.4, "operating_head_ft": 24.4,
                    "selected_pump": "PUMP-A", "pump_kw": 0.67, "total_basin_gallons": 124.7, "budget_total": 1500,
                    "om_cost_per_year": 1106},
        "issues": [{"key": "LIGHT_GFCI", "severity": "warning", "title": "GFCI"}],
        "readiness": {"sections": [{"key": "site", "status": "missing", "missing": ["Enter the site address"]},
                                   {"key": "features", "status": "ok", "missing": []},
                                   {"key": "lighting", "status": "n_a", "missing": []}],
                      "calc_ready": True, "issue_ready": False},
        "verdict": {"level": "warn"},
        "bom": [{"section": "Equipment", "item": "PUMP-A", "description": "Pump", "qty": 1, "uom": "ea", "rate": 1500,
                 "amount": 1500, "source": "Auto", "catalog_source": "datasheet", "line_key": "pump|p1"}],
        "charts": {"duty_svg": "<svg/>"},
        "outputs": {},
    }
    out.update(over)
    return out


class WriteDeriveTests(unittest.TestCase):
    def test_rollups_json_and_identity(self):
        doc = FakeDoc(status="Draft")
        wfd.write_derive(doc, derive_out())
        self.assertEqual(doc.design_flow_gpm, 80)
        self.assertEqual(doc.computed_tdh_ft, 24.4)  # the API's tdh_ft keeps its v1 column
        self.assertEqual(doc.selected_pump, "PUMP-A")
        self.assertEqual(doc.family, "weir")
        self.assertEqual(doc.mode, "Quick")
        self.assertEqual(json.loads(doc.model_json)["schema"], 2)
        self.assertIn("charts", json.loads(doc.results_json))
        self.assertEqual(json.loads(doc.design_issues_json)[0]["key"], "LIGHT_GFCI")
        self.assertEqual(doc.warning_count, 1)
        self.assertEqual(doc.issue_ready, 0)
        self.assertIn("Enter the site address", doc.next_inputs_needed)

    def test_status_follows_calc_ready_from_draft_only(self):
        doc = FakeDoc(status="Draft")
        wfd.write_derive(doc, derive_out())
        self.assertEqual(doc.status, "Derived")
        out = derive_out(readiness={"sections": [], "calc_ready": False, "issue_ready": False})
        wfd.write_derive(doc, out)
        self.assertEqual(doc.status, "Draft")
        reviewed = FakeDoc(status="Reviewed")
        wfd.write_derive(reviewed, out)
        self.assertEqual(reviewed.status, "Reviewed")  # an earned status is never demoted by a save

    def test_bom_auto_rewritten_manual_kept(self):
        doc = FakeDoc(status="Draft", bom=[
            FakeRow(section="Equipment", item="OLD", qty=1, rate=10, source="Auto"),
            FakeRow(section="Labor", item=None, description="Crane", qty=2, rate=400, source="Manual"),
        ])
        wfd.write_derive(doc, derive_out())
        sources = [(r.source, r.item or r.description) for r in doc.bom]
        self.assertEqual(sources, [("Auto", "PUMP-A"), ("Manual", "Crane")])
        self.assertEqual(doc.bom[1].amount, 800)
        self.assertEqual(doc.budget_total, 2300)  # the Manual line counts in the budget


class AckTests(unittest.TestCase):
    def test_rows_are_the_source_of_truth(self):
        rows = [FakeRow(issue_key="A", note="ok", acknowledged_by="u@x", acknowledged_on="2026-11-01 10:00:00")]
        acks = wfd.acks_from_rows(rows, [{"issue_key": "B", "note": "gone"}])
        self.assertEqual([a["issue_key"] for a in acks], ["A"])
        self.assertEqual(acks[0]["by"], "u@x")


class CompletionTests(unittest.TestCase):
    def test_completion_from_readiness(self):
        self.assertEqual(wfd.compute_completion_percent(FakeDoc()), 0.0)
        readiness = {"sections": [{"status": "ok"}, {"status": "warn"}, {"status": "missing"}, {"status": "n_a"}]}
        self.assertAlmostEqual(wfd.compute_completion_percent(FakeDoc(readiness_json=json.dumps(readiness))), 66.7)


class RecomputeTests(unittest.TestCase):
    def test_a_legacy_model_is_reported_not_derived(self):
        doc = FakeDoc(model_json=json.dumps({"basins": [{"shape": "Rectangular"}], "pipe_segments": []}))
        wfd.WaterFeatureDesign.recompute(doc)
        issues = json.loads(doc.design_issues_json)
        self.assertEqual(issues[0]["code"], "MODEL_NOT_CONVERTED")
        self.assertEqual(doc.blocker_count, 1)

    def test_stored_model_reads_text_or_dict(self):
        self.assertEqual(wfd.stored_model('{"schema": 2}'), {"schema": 2})
        self.assertEqual(wfd.stored_model({"schema": 2}), {"schema": 2})
        self.assertIsNone(wfd.stored_model("not json"))
        self.assertIsNone(wfd.stored_model(None))


class LegacyConversionTests(unittest.TestCase):
    """The conversion patch's path: the stored child rows -> legacy_inputs -> model.migrate."""

    @classmethod
    def setUpClass(cls):
        from erpnext_enhancements.patches import convert_water_feature_designs_to_model as patch

        cls.patch = patch

    def test_rows_become_a_schema_2_model(self):
        from erpnext_enhancements.water_engineering.engine import model as model_engine

        parent = {"pipe_material": "SCH40 PVC", "static_lift_ft": 6, "venue_type": "Decorative Fountain"}
        rows = {
            "basins": [{"basin_label": "Pool", "shape": "Rectangular", "length_in": 120, "width_in": 60, "height_in": 12}],
            "features": [{"feature_type": "Weir", "weir_length_ft": 6, "head_in": 0.25, "end_contractions": 2}],
            "pipe_segments": [
                {"segment_label": "S", "line_type": "Suction", "nominal_size": '3"', "pipe_length_ft": 8,
                 "fittings_json": '[{"type": "ELL 90", "qty": 2}]'},
                {"segment_label": "D", "line_type": "Discharge", "nominal_size": '2"', "pipe_length_ft": 60},
            ],
            "pumps": [{"pump_item": "PUMP-FIT"}],
        }
        model = model_engine.migrate(self.patch.legacy_inputs(parent, rows))
        self.assertEqual(model["schema"], 2)
        self.assertEqual(model["basins"][0]["length_in"], 120)
        self.assertEqual(model["features"][0]["family"], "weir")
        segs = model["network"]["segments"]
        self.assertEqual([s["line"] for s in segs], ["suction", "discharge"])
        self.assertEqual(segs[0]["fittings"], [{"key": "ELL 90", "qty": 2}])
        self.assertEqual(model["network"]["nodes"][-1]["elevation_ft"], 6)  # static lift -> the terminal
        self.assertEqual(model["equipment"][0]["item"], "PUMP-FIT")
        self.assertFalse([p for p in model_engine.validate(model) if p.severity == "error"])

    def test_an_empty_record_still_converts(self):
        from erpnext_enhancements.water_engineering.engine import model as model_engine

        model = model_engine.migrate(self.patch.legacy_inputs({}, {}))
        self.assertEqual(model["schema"], 2)
        self.assertEqual(model["features"], [])

    def test_the_controller_no_longer_reads_schema_1_rows(self):
        self.assertFalse(hasattr(wfd, "legacy_inputs"))


if __name__ == "__main__":
    unittest.main()
