"""Bench-free tests for the Water Feature Design typed-issue / readiness module.

``water_engineering/issues.py`` deliberately imports no frappe (it derives
everything from persisted doc state), so it is tested directly with fake docs —
the same pattern as the engine tests.

Run: python -m pytest erpnext_enhancements/tests/test_water_design_issues.py
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.water_engineering import issues as di  # noqa: E402


class FakeRow:
    """Attribute access with a None default (mimics a child-row docfield read)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __getattr__(self, _name):
        return None


class FakeDoc(FakeRow):
    def get(self, key, default=None):
        value = self.__dict__.get(key)
        if value is None:
            return [] if default is None else default
        return value


def _calc_row(calc, status="", warnings="", citations=""):
    return FakeRow(calc=calc, status=status, warnings=warnings, citations=citations)


def _full_doc(**overrides):
    """A design that satisfies every readiness item (both gates)."""
    doc = FakeDoc(
        design_title="Test Fountain",
        selected_pump="PUMP-FIT",
        total_basin_gallons=800,
        design_flow_gpm=40,
        drain_nominal_size='3"',
        basins=[FakeRow(volume_gal=800)],
        features=[FakeRow(feature_label="Weir", feature_type="Weir", weir_length_ft=6, flow_gpm=27)],
        pipe_segments=[
            FakeRow(
                segment_label="Pump discharge", line_type="Discharge", nominal_size='3"',
                material="SCH40 PVC", pipe_length_ft=50, flow_gpm=40,
                velocity_fps=1.8, velocity_status="Okay",
                pressure_status="Okay", pressure_margin_psi=140,
                fittings_json='[{"type":"ELL 90","qty":2}]',
            )
        ],
        pumps=[FakeRow(pump_item="PUMP-FIT")],
        electrical_loads=[FakeRow(pump_item="PUMP-FIT", hp=1.5)],
        calc_results=[],
    )
    doc.__dict__.update(overrides)
    return doc


class SegmentIssueTests(unittest.TestCase):
    def test_velocity_bands_map_to_severities(self):
        doc = FakeDoc(pipe_segments=[
            FakeRow(segment_label="A", velocity_status="Exceeds Legal Limit", velocity_fps=9.1, nominal_size='2"'),
            FakeRow(segment_label="B", velocity_status="Increase Size", velocity_fps=7.0, nominal_size='2"'),
            FakeRow(segment_label="C", velocity_status="Below Self-Cleaning", velocity_fps=0.3, nominal_size='6"'),
        ])
        issues = di.build_issues(doc)
        by_code = {i["code"]: i for i in issues}
        self.assertEqual(by_code["PIPE_VEL_EXCEEDS_LEGAL"]["severity"], "blocker")
        self.assertEqual(by_code["PIPE_VEL_OVER_LIMIT"]["severity"], "warning")
        self.assertEqual(by_code["PIPE_VEL_SETTLING"]["severity"], "info")
        # blockers sort first
        self.assertEqual(issues[0]["code"], "PIPE_VEL_EXCEEDS_LEGAL")
        # row-addressable + label-keyed (stable across row-name churn)
        self.assertEqual(by_code["PIPE_VEL_EXCEEDS_LEGAL"]["ref"]["table"], "pipe_segments")
        self.assertEqual(by_code["PIPE_VEL_EXCEEDS_LEGAL"]["ref"]["row_idx"], 0)
        self.assertEqual(by_code["PIPE_VEL_EXCEEDS_LEGAL"]["key"], "PIPE_VEL_EXCEEDS_LEGAL|A")

    def test_pressure_status_row_is_a_blocker(self):
        doc = FakeDoc(pipe_segments=[
            FakeRow(segment_label="Main", velocity_status="Okay", velocity_fps=3,
                    nominal_size='2"', pressure_status="Exceeds Pressure Rating",
                    pressure_margin_psi=-12),
        ])
        issues = di.build_issues(doc)
        self.assertEqual(issues[0]["code"], "PIPE_PRESSURE_UNDER_RATED")
        self.assertEqual(issues[0]["severity"], "blocker")

    def test_row_pressure_supersedes_doc_level_envelope(self):
        # Same finding via the audit row AND the row field -> only the
        # row-addressable one survives.
        doc = FakeDoc(
            pipe_segments=[FakeRow(segment_label="Main", velocity_status="Okay", velocity_fps=3,
                                   nominal_size='2"', pressure_status="Exceeds Pressure Rating")],
            calc_results=[_calc_row("pipe_pressure_check", status="Exceeds Pressure Rating",
                                    warnings="System 100 psi exceeds the 83 psi rating")],
        )
        issues = [i for i in di.build_issues(doc) if i["code"] == "PIPE_PRESSURE_UNDER_RATED"]
        self.assertEqual(len(issues), 1)
        self.assertIsNotNone(issues[0]["ref"])

    def test_no_flow_segment_warns_only_without_design_flow(self):
        seg = FakeRow(segment_label="X", nominal_size='3"', pipe_length_ft=50, flow_gpm=0)
        with_flow = FakeDoc(design_flow_gpm=40, pipe_segments=[seg])
        without = FakeDoc(design_flow_gpm=0, pipe_segments=[seg])
        self.assertFalse([i for i in di.build_issues(with_flow) if i["code"] == "SEG_NO_FLOW"])
        self.assertTrue([i for i in di.build_issues(without) if i["code"] == "SEG_NO_FLOW"])


class FeatureIssueTests(unittest.TestCase):
    def test_orifice_without_profile_warns(self):
        doc = FakeDoc(features=[FakeRow(feature_label="Jet", feature_type="Orifice Nozzle", flow_gpm=0)])
        issues = di.build_issues(doc)
        self.assertEqual(issues[0]["code"], "FEATURE_NEEDS_PROFILE")
        self.assertEqual(issues[0]["ref"]["field"], "nozzle_profile")

    def test_under_sheeted_weir_warns_with_row_ref(self):
        doc = FakeDoc(features=[FakeRow(feature_label="Edge", feature_type="Weir",
                                        weir_length_ft=20, flow_gpm=4)])  # 0.2 GPM/ft
        issues = di.build_issues(doc)
        self.assertEqual(issues[0]["code"], "WEIR_UNDER_SHEETED")
        self.assertEqual(issues[0]["ref"]["row_idx"], 0)

    def test_healthy_weir_is_quiet(self):
        doc = FakeDoc(features=[FakeRow(feature_label="Edge", feature_type="Weir",
                                        weir_length_ft=6, flow_gpm=27)])
        self.assertFalse(di.build_issues(doc))


class CalcResultMappingTests(unittest.TestCase):
    def test_safety_status_rules(self):
        cases = [
            ("suction_outlet_vgb", "Entrapment Risk — Resize", "VGB_ENTRAPMENT", "blocker"),
            ("suction_outlet_vgb", "Add Second Drain", "VGB_SINGLE_OUTLET", "warning"),
            ("npsh_available", "Cavitation Risk", "NPSH_CAVITATION", "blocker"),
            ("npsh_available", "Marginal", "NPSH_MARGINAL", "warning"),
            ("water_hammer", "Exceeds Pipe Rating", "WATER_HAMMER_OVER_RATING", "blocker"),
            ("ozone_sidestream", "Need Larger or More Contact Tanks",
             "CHEM_CONTACT_TANK_UNDERSIZED", "warning"),
        ]
        for calc, status, code, severity in cases:
            doc = FakeDoc(calc_results=[_calc_row(calc, status=status)])
            issues = di.build_issues(doc)
            self.assertTrue(issues, f"{calc}/{status} produced no issue")
            self.assertEqual(issues[0]["code"], code)
            self.assertEqual(issues[0]["severity"], severity)

    def test_okay_statuses_are_quiet(self):
        doc = FakeDoc(calc_results=[
            _calc_row("pipe_pressure_check", status="Okay"),
            _calc_row("npsh_available", status="Okay"),
        ])
        self.assertFalse(di.build_issues(doc))

    def test_component_over_max_flow_maps_from_segment_envelope(self):
        doc = FakeDoc(calc_results=[_calc_row(
            "Component loss — Pump discharge",
            warnings="Over rated flow: SKIMMER is rated to 75 GPM but carries 90 GPM — split flow or size up.",
        )])
        issues = di.build_issues(doc)
        self.assertEqual(issues[0]["code"], "COMPONENT_OVER_MAX_FLOW")
        self.assertEqual(issues[0]["severity"], "warning")

    def test_cya_floor_severity_tracks_input_basis(self):
        floor_warning = (
            "At CYA 50 ppm the free-chlorine floor is 3.75 ppm, above the standard "
            "outdoor target max of 3 ppm — raise free chlorine or lower CYA."
        )
        default_basis = FakeDoc(calc_results=[_calc_row("chemistry_targets", warnings=floor_warning)])
        user_basis = FakeDoc(chem_cya_ppm=80,
                             calc_results=[_calc_row("chemistry_targets", warnings=floor_warning)])
        self.assertEqual(di.build_issues(default_basis)[0]["severity"], "info")
        self.assertEqual(di.build_issues(user_basis)[0]["severity"], "warning")
        self.assertEqual(di.build_issues(user_basis)[0]["code"], "CHEM_FC_BELOW_CYA_FLOOR")

    def test_unmatched_warning_surfaces_as_advisory_and_quiet_list_is_quiet(self):
        doc = FakeDoc(calc_results=[
            _calc_row("turnover_gpm", warnings="Some brand-new engine warning text."),
            _calc_row("suction_outlet_vgb",
                      warnings="Engineering aid only — confirm against the suction cover's listed rating"),
        ])
        issues = di.build_issues(doc)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["code"], "ENGINE_NOTE")
        self.assertEqual(issues[0]["severity"], "info")

    def test_drain_divergence_info(self):
        doc = FakeDoc(drain_capacity_gpm=30)
        issues = di.build_issues(doc)
        self.assertEqual(issues[0]["code"], "DRAIN_BASIS_DIVERGENCE")
        self.assertEqual(issues[0]["severity"], "info")


class ReadinessTests(unittest.TestCase):
    def test_empty_doc_is_not_ready_and_itemizes_missing(self):
        doc = FakeDoc()
        readiness = di.build_readiness(doc, [])
        self.assertFalse(readiness["calc_ready"])
        self.assertFalse(readiness["issue_ready"])
        incomplete = {s["key"] for s in readiness["sections"] if s["state"] == "incomplete"}
        self.assertIn("basin", incomplete)
        self.assertIn("features", incomplete)
        self.assertIn("piping", incomplete)
        self.assertIn("pump", incomplete)
        # every missing item explains itself
        for s in readiness["sections"]:
            for m in s["missing"]:
                self.assertTrue(m["label"] and m["why"])

    def test_full_doc_is_ready_on_both_gates(self):
        doc = _full_doc()
        issues = di.build_issues(doc)
        self.assertFalse([i for i in issues if i["severity"] != "info"])
        readiness = di.build_readiness(doc, issues)
        self.assertTrue(readiness["calc_ready"])
        self.assertTrue(readiness["issue_ready"])

    def test_tiers_na_unless_a_tiered_feature_exists(self):
        plain = di.build_readiness(FakeDoc(), [])
        tiered = di.build_readiness(
            FakeDoc(features=[FakeRow(feature_type="Tiered Fountain", flow_gpm=0)]), []
        )
        self.assertEqual({s["key"]: s["state"] for s in plain["sections"]}["tiers"], "n/a")
        self.assertEqual({s["key"]: s["state"] for s in tiered["sections"]}["tiers"], "incomplete")

    def test_blockers_block_the_section_and_the_issue_gate(self):
        doc = _full_doc(pipe_segments=[FakeRow(
            segment_label="Main", line_type="Discharge", nominal_size='2"',
            velocity_status="Exceeds Legal Limit", velocity_fps=9.0,
            fittings_json='[{"type":"ELL 90","qty":2}]',
        )])
        issues = di.build_issues(doc)
        readiness = di.build_readiness(doc, issues)
        states = {s["key"]: s["state"] for s in readiness["sections"]}
        self.assertEqual(states["piping"], "blocked")
        self.assertFalse(readiness["issue_ready"])
        self.assertTrue(readiness["calc_ready"])  # the model computes; issuing is gated

    def test_unacked_warning_blocks_issue_gate_until_acknowledged(self):
        doc = _full_doc(features=[FakeRow(feature_label="Edge", feature_type="Weir",
                                          weir_length_ft=20, flow_gpm=4)])
        issues = di.build_issues(doc)
        warning = [i for i in issues if i["severity"] == "warning"][0]
        self.assertFalse(di.build_readiness(doc, issues)["issue_ready"])
        doc.issue_acks = [FakeRow(issue_key=warning["key"])]
        self.assertTrue(di.build_readiness(doc, issues)["issue_ready"])
        self.assertFalse(di.unacknowledged_warnings(doc, issues))


class ScheduleAndSummaryTests(unittest.TestCase):
    def test_fitting_schedule_aggregates_across_segments(self):
        doc = FakeDoc(
            pipe_material="SCH40 PVC",
            pipe_segments=[
                FakeRow(nominal_size='2"', fittings_json='[{"type":"ELL 90","qty":2}]',
                        components_json='[{"type":"SKIMMER","qty":1}]'),
                FakeRow(nominal_size='2"', fittings_json='[{"type":"ELL 90","qty":3}]'),
                FakeRow(nominal_size='3"', material="SCH80 PVC",
                        fittings_json='[{"type":"ELL 90","qty":1}]'),
            ],
        )
        schedule = di.fitting_schedule(doc)
        ell_2in = [r for r in schedule if r["type"] == "ELL 90" and r["size"] == '2"']
        self.assertEqual(ell_2in[0]["qty"], 5)  # 2 + 3, same material+size
        self.assertEqual(len([r for r in schedule if r["type"] == "ELL 90"]), 2)  # split by size
        self.assertEqual([r for r in schedule if r["kind"] == "Component"][0]["qty"], 1)

    def test_summarize_counts(self):
        issues = [
            {"severity": "blocker"}, {"severity": "warning"},
            {"severity": "warning"}, {"severity": "info"},
        ]
        counts = di.summarize(issues)
        self.assertEqual(counts["blocker_count"], 1)
        self.assertEqual(counts["warning_count"], 2)
        self.assertEqual(counts["info_count"], 1)
        self.assertIn("1 blocker", counts["summary"])
        self.assertEqual(di.summarize([])["summary"], "No issues")


if __name__ == "__main__":
    unittest.main()
