"""Bench-free tests for the subcontractor scorecard (WI-075 sub-phase N).

A vendor scorecard is the one artifact in this programme that gets **printed and carried into a
negotiation**, so its failure mode is not a crash. It is a page asserting that every subcontractor
is flawless, which is exactly what this system would produce today if nobody stopped it.

Measured on production 2026-09-14: every quality doctype holds zero rows; no `Project Contract` is
a subcontractor master agreement (all sixteen are `maintenance` with `party_type = Customer`); and
rework hours and certificates of insurance are recorded nowhere at all.

So the guards here are:

* **Nothing judgeable must produce no score** — `None`, never 0 and never 100, and a state that
  says so in words.
* **A ratio with an empty denominator is unanswerable**, not zero. First-pass yield over zero
  inspections says nothing about a subcontractor nobody inspected.
* **The score is a count of thresholds met, never a weighted composite.** Weights are judgements
  disguised as arithmetic and nobody has agreed any.
* **A measure from an instrument nobody uses is not evidence**, however good it looks.
* **A manual adjustment cannot be silent** — no reason, no save.
* **Unknown is not failure.** A missing master agreement is a gap in our record, not the
  subcontractor's conduct, and must never be scored against them.

Run: python -m unittest erpnext_enhancements.tests.test_subcontractor_scorecard
"""

import json
import re
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.quality import budgets, scorecard

DOCTYPE_ROOT = APP_ROOT / "quality" / "doctype"
CARD_DIR = DOCTYPE_ROOT / "subcontractor_scorecard"
MEASURE_DIR = DOCTYPE_ROOT / "scorecard_measure"
EVIDENCE_DIR = DOCTYPE_ROOT / "scorecard_evidence"
BUILDER = APP_ROOT / "quality" / "scorecard_build.py"
API = APP_ROOT / "api" / "subcontractor_scorecard.py"
CUSTOM_FIELDS = APP_ROOT / "fixtures" / "custom_field.json"


def _raw(path):
    return path.read_text(encoding="utf-8")


def _no_comments(path):
    """Source with `#` comments stripped but string literals intact.

    An absence assertion needs the comments gone — the comment explaining why a token is absent
    names that token. The builder keeps its SQL in triple-quoted strings, so the full docstring
    stripper would delete the queries other tests here check.
    """
    return re.sub(r"(?m)^\s*#.*$", "", _raw(path))


def _code_only(path):
    src = re.sub(r'"""[\s\S]*?"""', "", _raw(path))
    return re.sub(r"(?m)^\s*#.*$", "", src)


def result(key, value, coverage=scorecard.COVERAGE_TRACKED):
    return {"measure_key": key, "value": value, "coverage": coverage}


def all_untracked():
    """What every scorecard on this site looks like today."""
    return [
        result(key, None, scorecard.COVERAGE_NOT_TRACKED) for key in scorecard.measure_keys()
    ]


# --------------------------------------------------------------------------------------------
# The state this site is actually in
# --------------------------------------------------------------------------------------------


class TestNothingMeasurable(unittest.TestCase):
    """The load-bearing case: what happens when the system knows nothing."""

    def test_no_judgeable_measure_yields_no_score(self):
        percent, met, judgeable, total = scorecard.score(all_untracked())
        self.assertIsNone(percent)
        self.assertEqual(met, 0)
        self.assertEqual(judgeable, 0)
        self.assertEqual(total, len(scorecard.MEASURES))

    def test_the_score_is_not_zero_and_not_a_hundred(self):
        """Zero reads as a terrible subcontractor and a hundred as a flawless one. Both are
        confident, and on this site both are false."""
        percent, _, _, _ = scorecard.score(all_untracked())
        self.assertNotEqual(percent, 0)
        self.assertNotEqual(percent, 100)

    def test_the_state_says_so_in_words(self):
        self.assertEqual(
            scorecard.scorecard_state(all_untracked()), scorecard.STATE_NOT_MEASURABLE
        )

    def test_the_headline_does_not_imply_a_result(self):
        text = scorecard.headline(all_untracked())
        self.assertIn("Not yet measurable", text)
        self.assertNotIn("Met 0", text)

    def test_an_empty_scorecard_is_also_not_measurable(self):
        self.assertEqual(scorecard.scorecard_state([]), scorecard.STATE_NOT_MEASURABLE)
        self.assertEqual(scorecard.score([]), (None, 0, 0, 0))


# --------------------------------------------------------------------------------------------
# Measures
# --------------------------------------------------------------------------------------------


class TestMeasures(unittest.TestCase):
    def test_every_measure_key_is_unique(self):
        keys = scorecard.measure_keys()
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_measure_declares_a_known_source(self):
        for key, *_rest in scorecard.MEASURES:
            self.assertIn(scorecard.measure(key)[4], scorecard.SOURCES, key)

    def test_rework_hours_declares_no_source_and_is_still_listed(self):
        """Nothing on this site records rework hours. Listed rather than omitted so the gap
        appears on the artifact — a measure nobody can see missing is one nobody builds. It is
        the open question keeping KPI #11 at Semi."""
        row = scorecard.measure("rework_hours")
        self.assertIsNotNone(row)
        self.assertEqual(row[4], scorecard.SOURCE_NONE)
        self.assertIsNone(row[5])

    def test_insurance_currency_declares_no_source_and_is_still_listed(self):
        """There is no certificate-of-insurance field on Supplier or anywhere else."""
        row = scorecard.measure("insurance_currency")
        self.assertEqual(row[4], scorecard.SOURCE_NONE)

    def test_critical_ncrs_are_counted_separately_from_the_total(self):
        """Folding three Minor findings and one Critical into a single 4 hides the only one that
        mattered."""
        self.assertIsNotNone(scorecard.measure("critical_ncrs"))
        self.assertIsNotNone(scorecard.measure("ncrs_raised"))

    def test_an_unknown_measure_is_not_invented(self):
        self.assertIsNone(scorecard.measure("made_up"))
        self.assertIsNone(scorecard.meets_threshold("made_up", 5))


# --------------------------------------------------------------------------------------------
# Ratios and averages
# --------------------------------------------------------------------------------------------


class TestRatio(unittest.TestCase):
    def test_a_ratio_of_an_empty_denominator_is_unanswerable(self):
        """First-pass yield over zero inspections is not 0% and not 100%."""
        self.assertIsNone(scorecard.ratio(0, 0))
        self.assertIsNone(scorecard.ratio(5, 0))
        self.assertIsNone(scorecard.ratio(None, None))

    def test_an_ordinary_ratio(self):
        self.assertEqual(scorecard.ratio(9, 10), 90.0)
        self.assertEqual(scorecard.ratio(1, 3), 33.3)

    def test_an_average_of_nothing_is_not_zero_days(self):
        self.assertIsNone(scorecard.average([]))
        self.assertIsNone(scorecard.average(None))
        self.assertIsNone(scorecard.average(["", None]))

    def test_an_ordinary_average_skips_unusable_values(self):
        self.assertEqual(scorecard.average([10, 20, None, "abc"]), 15.0)


# --------------------------------------------------------------------------------------------
# Judging
# --------------------------------------------------------------------------------------------


class TestJudging(unittest.TestCase):
    def test_lower_is_better_measures(self):
        self.assertTrue(scorecard.meets_threshold("ncrs_raised", 0))
        self.assertFalse(scorecard.meets_threshold("ncrs_raised", 1))
        self.assertTrue(scorecard.meets_threshold("days_to_close", 14))
        self.assertFalse(scorecard.meets_threshold("days_to_close", 14.1))

    def test_higher_is_better_measures(self):
        self.assertTrue(scorecard.meets_threshold("first_pass_yield", 90))
        self.assertFalse(scorecard.meets_threshold("first_pass_yield", 89.9))

    def test_a_measure_with_no_value_is_not_judged(self):
        self.assertIsNone(scorecard.meets_threshold("first_pass_yield", None))
        self.assertIsNone(scorecard.meets_threshold("first_pass_yield", ""))

    def test_a_measure_with_no_threshold_is_not_judged(self):
        self.assertIsNone(scorecard.meets_threshold("rework_hours", 40))

    def test_an_agreement_in_force_or_near_renewal_counts_as_met(self):
        """An agreement 45 days from renewal is in force. Marking it unmet would score a
        subcontractor down for the calendar."""
        self.assertTrue(scorecard.meets_threshold("agreement_currency", "ok"))
        self.assertTrue(scorecard.meets_threshold("agreement_currency", "warn"))

    def test_an_expired_agreement_is_a_failure(self):
        self.assertFalse(scorecard.meets_threshold("agreement_currency", "expired"))

    def test_an_unknown_agreement_is_never_scored_against_them(self):
        """This is the whole of sub-phase L's finding carried forward: unknown is a gap in OUR
        record, not their conduct. Every one of the sixteen live contracts is in this state."""
        self.assertIsNone(scorecard.meets_threshold("agreement_currency", "unknown"))
        self.assertIsNone(scorecard.meets_threshold("agreement_currency", ""))
        self.assertIsNone(scorecard.meets_threshold("agreement_currency", None))

    def test_a_figure_from_an_unused_instrument_is_not_evidence(self):
        """However good it looks. A zero from a source nobody uses says nothing about them."""
        judged = scorecard.judge([result("ncrs_raised", 0, scorecard.COVERAGE_NOT_TRACKED)])
        self.assertFalse(judged[0]["judgeable"])
        self.assertIsNone(judged[0]["met"])

    def test_judge_does_not_mutate_the_callers_rows(self):
        rows = [result("ncrs_raised", 0)]
        scorecard.judge(rows)
        self.assertNotIn("met", rows[0])


# --------------------------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------------------------


class TestScore(unittest.TestCase):
    def test_the_score_is_the_proportion_of_judgeable_measures_met(self):
        rows = [
            result("ncrs_raised", 0),
            result("critical_ncrs", 0),
            result("hold_point_failures", 1),
            result("first_pass_yield", 95),
        ]
        percent, met, judgeable, total = scorecard.score(rows)
        self.assertEqual((met, judgeable, total), (3, 4, 4))
        self.assertEqual(percent, 75.0)

    def test_unjudgeable_measures_do_not_dilute_the_score(self):
        """A measure nobody can judge is not a failure, and counting it as one would score every
        subcontractor down for gaps in our own instruments."""
        rows = [
            result("ncrs_raised", 0),
            result("rework_hours", None, scorecard.COVERAGE_NO_SOURCE),
            result("insurance_currency", None, scorecard.COVERAGE_NO_SOURCE),
        ]
        percent, met, judgeable, _total = scorecard.score(rows)
        self.assertEqual(percent, 100.0)
        self.assertEqual((met, judgeable), (1, 1))

    def test_the_state_is_partial_when_some_measures_cannot_be_judged(self):
        rows = [
            result("ncrs_raised", 0),
            result("rework_hours", None, scorecard.COVERAGE_NO_SOURCE),
        ]
        self.assertEqual(scorecard.scorecard_state(rows), scorecard.STATE_PARTIAL)

    def test_the_state_is_measured_only_when_everything_was_judged(self):
        rows = [result("ncrs_raised", 0), result("critical_ncrs", 0)]
        self.assertEqual(scorecard.scorecard_state(rows), scorecard.STATE_MEASURED)

    def test_the_headline_carries_the_footing_with_the_number(self):
        """The two never travel separately. A number quoted without how much of it is real is the
        thing this module exists to prevent."""
        rows = [
            result("ncrs_raised", 0),
            result("rework_hours", None, scorecard.COVERAGE_NO_SOURCE),
        ]
        text = scorecard.headline(rows)
        self.assertIn("Met 1 of 1", text)
        self.assertIn("could not be judged", text)


class TestAdjustment(unittest.TestCase):
    def test_an_adjustment_without_a_reason_is_refused(self):
        errors = scorecard.adjustment_errors(-10, "")
        self.assertTrue(any("reason" in e for e in errors), errors)

    def test_a_zero_adjustment_needs_no_reason(self):
        self.assertEqual(scorecard.adjustment_errors(0, ""), [])
        self.assertEqual(scorecard.adjustment_errors(None, None), [])

    def test_whitespace_is_not_a_reason(self):
        self.assertTrue(scorecard.adjustment_errors(5, "   "))

    def test_an_absurd_adjustment_is_refused(self):
        self.assertTrue(scorecard.adjustment_errors(500, "because"))

    def test_an_adjustment_is_applied_and_clamped(self):
        self.assertEqual(scorecard.final_score(75, 10), 85.0)
        self.assertEqual(scorecard.final_score(95, 20), 100.0)
        self.assertEqual(scorecard.final_score(5, -20), 0.0)

    def test_an_adjustment_cannot_conjure_a_score_that_does_not_exist(self):
        """A scorecard reading -5 for a subcontractor nobody could measure is worse than a
        blank."""
        self.assertIsNone(scorecard.final_score(None, -5))
        self.assertIsNone(scorecard.final_score(None, 20))


class TestPopulation(unittest.TestCase):
    def test_a_supplier_we_did_not_engage_gets_no_scorecard(self):
        """Scoring all 1181 suppliers would be noise, and supplier group cannot separate them —
        908 sit in the group `Labels`."""
        self.assertEqual(scorecard.engagement_reasons(False, 0, False), [])

    def test_each_way_of_being_engaged_is_named(self):
        self.assertEqual(len(scorecard.engagement_reasons(True, 0, False)), 1)
        self.assertEqual(len(scorecard.engagement_reasons(True, 3, True)), 3)

    def test_an_agreement_alone_is_enough(self):
        """A subcontractor who did no work this month has not stopped being under contract."""
        self.assertTrue(scorecard.engagement_reasons(False, 0, True))


# --------------------------------------------------------------------------------------------
# Schema and source guards
# --------------------------------------------------------------------------------------------


class TestSchema(unittest.TestCase):
    def test_the_decision_module_imports_no_frappe(self):
        self.assertNotIn("import frappe", _code_only(APP_ROOT / "quality" / "scorecard.py"))

    def test_the_coverage_vocabulary_is_shared_not_redefined(self):
        """Imported from `budgets` rather than restated, so the two surfaces cannot drift into
        saying `Not Tracked` and `Untracked`."""
        self.assertEqual(scorecard.COVERAGE_TRACKED, budgets.COVERAGE_TRACKED)
        self.assertEqual(scorecard.COVERAGE_NOT_TRACKED, budgets.COVERAGE_NOT_TRACKED)
        self.assertEqual(scorecard.COVERAGE_NO_SOURCE, budgets.COVERAGE_NO_SOURCE)
        self.assertEqual(set(scorecard.COVERAGE_STATES), set(budgets.COVERAGE_STATES))

    def test_controller_class_names_are_what_frappe_derives(self):
        """`classname = doctype.replace(" ", "").replace("-", "")` — it strips characters and does
        NOT title-case. A mismatch makes `bench migrate` force-delete the DocType while exiting
        0."""
        for folder, doctype in (
            (CARD_DIR, "Subcontractor Scorecard"),
            (MEASURE_DIR, "Scorecard Measure"),
            (EVIDENCE_DIR, "Scorecard Evidence"),
        ):
            expected = doctype.replace(" ", "").replace("-", "")
            self.assertIn(
                f"class {expected}(Document)", _raw(folder / (folder.name + ".py")), doctype
            )

    def test_every_doctype_sits_in_the_quality_module(self):
        for folder in (CARD_DIR, MEASURE_DIR, EVIDENCE_DIR):
            data = json.loads(_raw(folder / (folder.name + ".json")))
            self.assertEqual(data["module"], "Quality", folder.name)

    def test_the_two_detail_tables_are_child_tables(self):
        for folder in (MEASURE_DIR, EVIDENCE_DIR):
            data = json.loads(_raw(folder / (folder.name + ".json")))
            self.assertEqual(data.get("istable"), 1, folder.name)

    def test_every_derived_field_is_read_only(self):
        """A typed score is an opinion wearing a number's clothes. Only the adjustment and its
        reason may be written."""
        data = json.loads(_raw(CARD_DIR / "subcontractor_scorecard.json"))
        fields = {f["fieldname"]: f for f in data["fields"]}
        for fieldname in (
            "state",
            "score_display",
            "score_percent",
            "final_score_percent",
            "measures_met",
            "measures_judgeable",
            "measures_total",
            "adjusted_by",
            "adjusted_on",
            "supplier_name",
            "period_start",
            "period_end",
            "period_label",
            "engagement_reason",
        ):
            self.assertEqual(fields[fieldname].get("read_only"), 1, fieldname)
        self.assertNotEqual(fields["manual_adjustment"].get("read_only"), 1)

    def test_a_reason_is_mandatory_whenever_an_adjustment_is_set(self):
        data = json.loads(_raw(CARD_DIR / "subcontractor_scorecard.json"))
        fields = {f["fieldname"]: f for f in data["fields"]}
        self.assertEqual(
            fields["adjustment_reason"].get("mandatory_depends_on"),
            "eval:doc.manual_adjustment",
        )

    def test_the_state_options_match_the_code(self):
        data = json.loads(_raw(CARD_DIR / "subcontractor_scorecard.json"))
        fields = {f["fieldname"]: f for f in data["fields"]}
        options = [o for o in fields["state"]["options"].split("\n") if o]
        self.assertEqual(set(options), set(scorecard.STATES))

    def test_the_measure_coverage_options_match_the_code(self):
        data = json.loads(_raw(MEASURE_DIR / "scorecard_measure.json"))
        fields = {f["fieldname"]: f for f in data["fields"]}
        options = [o for o in fields["coverage"]["options"].split("\n") if o]
        self.assertEqual(set(options), set(scorecard.COVERAGE_STATES))

    def test_a_measure_carries_a_raw_value_as_well_as_a_display_one(self):
        """Judging the display string would fail to parse "92.5%" and report an unjudgeable
        measure on data that was perfectly good."""
        data = json.loads(_raw(MEASURE_DIR / "scorecard_measure.json"))
        names = {f["fieldname"] for f in data["fields"]}
        self.assertIn("value_raw", names)
        self.assertIn("value_display", names)

    def test_the_list_view_shows_the_sentence_not_the_bare_number(self):
        """`score_percent` reads 0 when there is no score at all. A list of those would be a page
        saying every subcontractor failed."""
        data = json.loads(_raw(CARD_DIR / "subcontractor_scorecard.json"))
        fields = {f["fieldname"]: f for f in data["fields"]}
        self.assertEqual(fields["score_display"].get("in_list_view"), 1)
        self.assertEqual(fields["state"].get("in_list_view"), 1)
        self.assertNotEqual(fields["score_percent"].get("in_list_view"), 1)

    def test_evidence_rows_carry_the_measure_key(self):
        """Frappe has no grandchild tables, so evidence hangs off the scorecard and carries the
        key instead. Nothing joins on `idx`."""
        data = json.loads(_raw(EVIDENCE_DIR / "scorecard_evidence.json"))
        names = {f["fieldname"] for f in data["fields"]}
        self.assertIn("measure_key", names)


class TestBuilder(unittest.TestCase):
    """Source-level guards on the frappe glue, asserted against the raw source: the SQL lives in
    triple-quoted strings a docstring stripper would delete."""

    def test_purchase_order_queries_use_the_project_union(self):
        """Either project field alone drops orders, silently."""
        source = _raw(BUILDER)
        self.assertIn("ifnull(nullif(poi.project, ''), po.project)", source)

    def test_only_submitted_documents_count(self):
        self.assertIn("po.docstatus = 1", _raw(BUILDER))

    def test_agreements_are_found_by_party_not_by_a_supplier_column(self):
        """`Project Contract` has no `supplier` column — it uses a dynamic `party_type`/`party`
        link, and a query on `supplier` raises `Unknown column`."""
        source = _raw(BUILDER)
        self.assertIn("party_type = 'Supplier'", source)
        self.assertIn("template_key = 'msa'", source)

    def test_a_scorecard_is_never_built_for_an_unfinished_month(self):
        source = _no_comments(BUILDER)
        self.assertIn("def closed_periods(", source)
        entry = source.split("def closed_periods(", 1)[1]
        self.assertIn("range(1,", entry.replace(" ", ""))

    def test_an_existing_scorecard_is_never_overwritten(self):
        """One that has been read, adjusted and signed is a record of what was known then."""
        source = _no_comments(BUILDER)
        entry = source.split("def build(", 1)[1].split("def build_period(", 1)[0]
        self.assertIn("if existing:", entry)
        self.assertIn("return existing", entry)

    def test_one_bad_supplier_does_not_stop_the_month(self):
        source = _no_comments(BUILDER)
        entry = source.split("def build_period(", 1)[1].split("def sweep(", 1)[0]
        self.assertIn("except Exception", entry)
        self.assertIn("continue", entry)

    def test_the_sweep_is_gated_on_the_module_switch(self):
        source = _no_comments(BUILDER)
        entry = source.split("def sweep(", 1)[1]
        self.assertIn("is_enabled()", entry)

    def test_supplier_summaries_avoid_the_orm(self):
        """Supplier carries its own doc_events; saving each one to stamp a read-only summary
        would fire the full hook chain across every scored supplier."""
        source = _no_comments(BUILDER)
        entry = source.split("def refresh_supplier_fields(", 1)[1]
        self.assertIn("frappe.db.set_value", entry)
        self.assertNotIn(".save()", entry)

    def test_the_supplier_stamp_is_the_sentence_not_the_figure(self):
        """A Supplier row showing a bare 0 with nothing saying whether anything was measurable is
        the misreading this sub-phase exists to prevent."""
        source = _no_comments(BUILDER)
        entry = source.split("def refresh_supplier_fields(", 1)[1]
        self.assertIn("custom_scorecard_summary", entry)
        self.assertIn("custom_scorecard_state", entry)


class TestApiSurface(unittest.TestCase):
    def test_whitelist_sits_directly_on_every_endpoint(self):
        for match in re.finditer(r"@frappe\.whitelist\(\)\n(.*)", _raw(API)):
            self.assertTrue(match.group(1).startswith("def "), match.group(1))

    def test_the_api_is_four_space_indented(self):
        self.assertNotIn("\n\t", _raw(API))

    def test_no_endpoint_returns_a_bare_score(self):
        """Every read hands back state and score_display beside the numeric fields."""
        source = _code_only(API)
        shape = source.split("def _shape(", 1)[1].split("@frappe.whitelist", 1)[0]
        self.assertIn('"state"', shape)
        self.assertIn('"score_display"', shape)

    def test_the_history_endpoint_carries_state_on_every_row(self):
        """A list where some scores are Not Measurable zeros and some are real would show a trend
        that never happened."""
        source = _code_only(API)
        entry = source.split("def get_supplier_scorecards(", 1)[1]
        self.assertIn('"state"', entry)

    def test_the_period_summary_surfaces_not_measurable_as_a_figure(self):
        source = _code_only(API)
        entry = source.split("def get_period_summary(", 1)[1]
        self.assertIn("not_measurable", entry)

    def test_the_adjustment_endpoint_validates_as_well_as_the_controller(self):
        """A rule enforced on one path only is a rule with a door left open."""
        source = _code_only(API)
        entry = source.split("def record_adjustment(", 1)[1]
        self.assertIn("adjustment_errors", entry)


class TestFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fields = {(f["dt"], f["fieldname"]): f for f in json.loads(_raw(CUSTOM_FIELDS))}

    def test_the_supplier_summary_fields_exist_and_are_read_only(self):
        for fieldname in (
            "custom_scorecard_summary",
            "custom_scorecard_state",
            "custom_latest_scorecard",
        ):
            field = self.fields[("Supplier", fieldname)]
            self.assertEqual(field.get("read_only"), 1, fieldname)

    def test_the_builder_and_the_fixture_agree_on_the_field_names(self):
        """A fieldname typo would stamp nothing, silently, on every supplier."""
        source = _raw(BUILDER)
        for fieldname in (
            "custom_scorecard_summary",
            "custom_scorecard_state",
            "custom_latest_scorecard",
        ):
            self.assertIn(fieldname, source)
            self.assertIn(("Supplier", fieldname), self.fields)

    def test_recovery_fields_live_on_the_non_conformance(self):
        """What went wrong and what we recovered stay on one document."""
        for fieldname in (
            "custom_recovery_status",
            "custom_recovery_claimed",
            "custom_recovery_recovered",
            "custom_recovery_closed_on",
            "custom_recovery_note",
        ):
            self.assertIn(("Non Conformance", fieldname), self.fields)

    def test_recovery_only_shows_when_a_subcontractor_is_named(self):
        """Recovering a cost from ourselves is not a thing."""
        field = self.fields[("Non Conformance", "custom_recovery_section")]
        self.assertEqual(field.get("depends_on"), "eval:doc.custom_supplier")

    def test_the_builder_reads_the_recovery_fields_it_shipped(self):
        source = _raw(BUILDER)
        self.assertIn("custom_recovery_claimed", source)
        self.assertIn("custom_recovery_recovered", source)


class TestHooks(unittest.TestCase):
    def test_the_sweep_is_wired_daily_not_monthly(self):
        """A prod deploy FLUSHDBs the queue redis and silently destroys pending jobs. A monthly
        job caught by a deploy is a month with no scorecards and nothing to notice."""
        import ast

        source = _raw(APP_ROOT / "hooks.py")
        self.assertIn("erpnext_enhancements.quality.scorecard_build.sweep", source)

        tree = ast.parse(source)
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and getattr(node.targets[0], "id", "") == "scheduler_events"
            ):
                keys = [k.value for k in node.value.keys]
                self.assertEqual(len(keys), len(set(keys)))
                self.assertNotIn("monthly", keys)
                for k, v in zip(node.value.keys, node.value.values, strict=False):
                    if k.value == "daily":
                        entries = [e.value for e in v.elts]
                        self.assertEqual(len(entries), len(set(entries)))
                        self.assertIn(
                            "erpnext_enhancements.quality.scorecard_build.sweep", entries
                        )
                        return
        self.fail("scheduler_events daily not found")


if __name__ == "__main__":
    unittest.main()
