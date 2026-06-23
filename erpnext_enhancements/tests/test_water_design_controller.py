"""Bench-free tests for the Water Feature Design controller's pure helpers.

Imports the controller under the shared frappe stub (so a syntax/import error in
the controller fails CI without a site) and exercises the frappe-free helpers
``_loads`` / ``_fmt`` / ``_engine_inputs`` / ``compute_completion_percent`` with
a lightweight fake doc. The frappe-bound ``recompute`` path is covered by the
bench DocType tests / dev-site run.

Run: python -m pytest erpnext_enhancements/tests/test_water_design_controller.py
"""

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

    frappe.utils.cint = lambda v=0: int(v or 0)
    frappe.utils.flt = lambda v=0, precision=None: float(v or 0)
    frappe.log_error = lambda *a, **k: None
    frappe.get_traceback = lambda *a, **k: ""

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
    def __init__(self, **kw):
        super().__init__(**kw)

    def get(self, key, default=None):
        value = self.__dict__.get(key)
        if value is None:
            return [] if default is None else default
        return value


class LoadsFmtTests(unittest.TestCase):
    def test_loads(self):
        self.assertEqual(wfd._loads('[{"type":"ELL 90","qty":2}]'), [{"type": "ELL 90", "qty": 2}])
        self.assertEqual(wfd._loads(""), [])
        self.assertEqual(wfd._loads("not json"), [])
        self.assertEqual(wfd._loads('{"a":1}'), [])  # object, not a list

    def test_fmt(self):
        self.assertEqual(wfd._fmt(None), "")
        self.assertEqual(wfd._fmt(5.0), "5")
        self.assertEqual(wfd._fmt(5.7768), "5.7768")
        self.assertEqual(wfd._fmt('3"'), '3"')


class EngineInputsTests(unittest.TestCase):
    def test_builds_engine_input_dict(self):
        doc = FakeDoc(
            pipe_material="SCH40 PVC",
            static_lift_ft=6,
            turnover_per_hr=2,
            basins=[FakeRow(shape="Rectangular", length_in=120, width_in=60, height_in=12)],
            features=[FakeRow(feature_type="Weir", weir_length_ft=6, head_in=0.25, end_contractions=2)],
            pipe_segments=[
                FakeRow(
                    segment_label="A", flow_gpm=27, nominal_size='2"', material=None,
                    pipe_length_ft=60, line_type="Discharge",
                    fittings_json='[{"type":"ELL 90","qty":2}]', components_json="",
                )
            ],
            pumps=[FakeRow(pump_item="PUMP-FIT", rated_gpm=60, rated_tdh_ft=40)],
        )
        out = wfd._engine_inputs(doc)
        self.assertEqual(out["basins"][0]["length_in"], 120)
        self.assertEqual(out["features"][0]["weir_length_ft"], 6)
        self.assertEqual(out["pipe_segments"][0]["material"], "SCH40 PVC")  # defaulted from parent
        self.assertEqual(out["pipe_segments"][0]["fittings"], [{"type": "ELL 90", "qty": 2}])
        self.assertEqual(out["static_lift_ft"], 6)
        self.assertEqual(out["hazen_williams_c"], 130)  # defaulted when unset
        self.assertEqual(out["pump_candidates"][0]["item_code"], "PUMP-FIT")

    def test_custom_hazen_williams_c_threads_through(self):
        doc = FakeDoc(pipe_material="SCH40 PVC", hazen_williams_c=150)
        self.assertEqual(wfd._engine_inputs(doc)["hazen_williams_c"], 150)

    def test_runs_through_engine(self):
        # The controller's input dict must drive run_spine to a real result.
        from erpnext_enhancements.water_engineering.engine import run_spine

        doc = FakeDoc(
            pipe_material="SCH40 PVC", static_lift_ft=6, turnover_per_hr=2,
            basins=[FakeRow(shape="Rectangular", length_in=120, width_in=60, height_in=12)],
            features=[FakeRow(feature_type="Weir", weir_length_ft=6, head_in=0.25, end_contractions=2)],
        )
        out = run_spine(wfd._engine_inputs(doc))
        self.assertAlmostEqual(out["total_basin_gallons"], 374.0256, places=4)
        self.assertAlmostEqual(out["design_flow_gpm"], 26.98125, places=4)


class CompletionTests(unittest.TestCase):
    def test_completion_percent(self):
        self.assertEqual(wfd.compute_completion_percent(FakeDoc()), 0.0)
        self.assertEqual(
            wfd.compute_completion_percent(FakeDoc(basins=[FakeRow()], features=[FakeRow()])), 50.0
        )
        full = FakeDoc(
            basins=[FakeRow()], features=[FakeRow()], pipe_segments=[FakeRow()], selected_pump="PUMP-FIT"
        )
        self.assertEqual(wfd.compute_completion_percent(full), 100.0)


if __name__ == "__main__":
    unittest.main()
