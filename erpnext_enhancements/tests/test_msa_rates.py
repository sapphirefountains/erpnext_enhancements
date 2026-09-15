"""Bench-free tests for MSA expiry, rates and order attribution (WI-075 sub-phase L).

The existing `validate_msa_gate` asks whether an agreement was ever **signed**. It never asks
whether it is still **in force**, so an MSA signed in 2019 — superseded rates, lapsed insurance —
gates a Statement of Work issued today exactly as well as one signed last week.

Three failures shape this file, and the middle one is the interesting one:

* **Unknown treated as expired** blocks every Statement of Work the company can currently issue,
  on deploy day, for a data gap rather than a real lapse. None of the sixteen live contracts
  records an expiry.
* **Unknown treated as valid** is the vacuous pass — clean forever, on every agreement, with
  nothing to investigate.
* **A purchase-order rollup keyed on one project field.** Either side alone loses orders, and
  loses them silently: 40 of 148 live pending lines carry no row project and 32 of those sit
  under a header that names the job.

Run: python -m unittest erpnext_enhancements.tests.test_msa_rates
"""

import sys
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.quality import msa

TODAY = date(2026, 9, 14)


def _line(classification="Journeyman", rate_type="Hourly", rate=95.0, start=None, end=None):
    return {
        "classification": classification,
        "rate_type": rate_type,
        "rate": rate,
        "effective_from": start,
        "effective_to": end,
    }


class TestExpiresOn(unittest.TestCase):
    def test_an_explicit_override_always_wins(self):
        """That is somebody stating the fact, rather than the system deriving one."""
        self.assertEqual(
            msa.expires_on("2026-01-01", 12, override="2027-06-30"), date(2027, 6, 30)
        )

    def test_the_term_is_counted_from_signing(self):
        self.assertEqual(msa.expires_on("2026-01-15", 12), date(2027, 1, 15))
        self.assertEqual(msa.expires_on("2026-01-15", 24), date(2028, 1, 15))

    def test_a_missing_term_uses_the_default(self):
        self.assertEqual(msa.expires_on("2026-01-15", None), date(2027, 1, 15))

    def test_no_signing_date_and_no_override_has_no_answer(self):
        """A guess stored as a date becomes a fact somebody later quotes."""
        self.assertIsNone(msa.expires_on(None, 12))
        self.assertIsNone(msa.expires_on("", 12))

    def test_month_arithmetic_clamps(self):
        """31 January plus one month is 28 February, not 3 March."""
        self.assertEqual(msa.expires_on("2026-01-31", 1), date(2026, 2, 28))

    def test_a_leap_day_does_not_crash(self):
        self.assertEqual(msa.expires_on("2024-02-29", 12), date(2025, 2, 28))

    def test_a_term_of_zero_means_unset_and_uses_the_default(self):
        """A Frappe `Int` field is 0 when nobody has filled it in — there is no null to tell
        'unset' from 'deliberately zero'. So 0 has to mean unset, or every MSA created before
        somebody types a term would have no computable expiry."""
        self.assertEqual(msa.expires_on("2026-01-15", 0), date(2027, 1, 15))

    def test_a_negative_term_has_no_answer(self):
        """Nobody means this. Deriving an expiry in the past from it would report every
        agreement as expired."""
        self.assertIsNone(msa.expires_on("2026-01-15", -6))


class TestExpiryState(unittest.TestCase):
    def test_well_inside_the_term_is_ok(self):
        state, days = msa.expiry_state(date(2027, 1, 1), TODAY)
        self.assertEqual(state, msa.STATE_OK)
        self.assertGreater(days, 60)

    def test_inside_sixty_days_warns(self):
        state, days = msa.expiry_state(date(2026, 10, 30), TODAY)
        self.assertEqual(state, msa.STATE_WARN)
        self.assertEqual(days, 46)

    def test_inside_fourteen_days_escalates(self):
        state, days = msa.expiry_state(date(2026, 9, 20), TODAY)
        self.assertEqual(state, msa.STATE_ESCALATE)
        self.assertEqual(days, 6)

    def test_the_boundaries_fall_on_the_safer_side(self):
        self.assertEqual(msa.expiry_state(date(2026, 9, 28), TODAY)[0], msa.STATE_ESCALATE)
        self.assertEqual(msa.expiry_state(date(2026, 9, 29), TODAY)[0], msa.STATE_WARN)
        self.assertEqual(msa.expiry_state(date(2026, 11, 13), TODAY)[0], msa.STATE_WARN)
        self.assertEqual(msa.expiry_state(date(2026, 11, 14), TODAY)[0], msa.STATE_OK)

    def test_expiring_today_is_not_yet_expired(self):
        state, days = msa.expiry_state(TODAY, TODAY)
        self.assertEqual(state, msa.STATE_ESCALATE)
        self.assertEqual(days, 0)

    def test_yesterday_is_expired_and_the_count_goes_negative(self):
        state, days = msa.expiry_state(date(2026, 9, 13), TODAY)
        self.assertEqual(state, msa.STATE_EXPIRED)
        self.assertEqual(days, -1)

    def test_no_expiry_is_unknown_not_expired(self):
        """Blocks every Statement of Work the company can issue, on deploy day, if got wrong."""
        state, days = msa.expiry_state(None, TODAY)
        self.assertEqual(state, msa.STATE_UNKNOWN)
        self.assertIsNone(days)

    def test_no_expiry_is_unknown_not_ok(self):
        """And the other way: the vacuous pass this programme exists to stop."""
        self.assertNotEqual(msa.expiry_state(None, TODAY)[0], msa.STATE_OK)

    def test_days_remaining_is_none_when_unknown(self):
        """So nothing can render 'expires in 0 days' for an agreement nobody has dated."""
        self.assertIsNone(msa.expiry_state(None, TODAY)[1])

    def test_an_unparseable_today_decides_nothing(self):
        self.assertEqual(msa.expiry_state(date(2027, 1, 1), "nope")[0], msa.STATE_UNKNOWN)


class TestBlocking(unittest.TestCase):
    def test_warn_mode_never_blocks(self):
        for state in (msa.STATE_EXPIRED, msa.STATE_ESCALATE, msa.STATE_UNKNOWN):
            self.assertFalse(msa.blocks_issue(state, "Warn"), state)

    def test_off_mode_never_blocks(self):
        self.assertFalse(msa.blocks_issue(msa.STATE_EXPIRED, "Off"))

    def test_block_mode_refuses_an_expired_agreement(self):
        self.assertTrue(msa.blocks_issue(msa.STATE_EXPIRED, "Block"))

    def test_block_mode_still_does_not_refuse_an_unknown_expiry(self):
        """A missing field is a gap in the record. Blocking every agreement the company has, on
        the day this deploys, would be the change stopping the work rather than the risk."""
        self.assertFalse(msa.blocks_issue(msa.STATE_UNKNOWN, "Block"))

    def test_block_mode_does_not_refuse_one_that_is_merely_close(self):
        self.assertFalse(msa.blocks_issue(msa.STATE_ESCALATE, "Block"))
        self.assertFalse(msa.blocks_issue(msa.STATE_WARN, "Block"))


class TestGateMessage(unittest.TestCase):
    def test_ok_says_nothing(self):
        self.assertIsNone(msa.gate_message(msa.STATE_OK, 200, "SF-MSA-2026-0001"))

    def test_unknown_reads_as_a_gap_not_an_accusation(self):
        text = msa.gate_message(msa.STATE_UNKNOWN, None, "SF-MSA-2026-0001")
        self.assertIn("no expiry date recorded", text)
        self.assertNotIn("expired", text)

    def test_expired_says_how_long_ago(self):
        text = msa.gate_message(msa.STATE_EXPIRED, -30, "SF-MSA-2026-0001", date(2026, 8, 15))
        self.assertIn("30 days ago", text)

    def test_escalate_asks_for_action(self):
        text = msa.gate_message(msa.STATE_ESCALATE, 6, "SF-MSA-2026-0001", date(2026, 9, 20))
        self.assertIn("Renew", text)

    def test_every_message_names_the_agreement(self):
        for state, days in (
            (msa.STATE_UNKNOWN, None), (msa.STATE_EXPIRED, -1),
            (msa.STATE_ESCALATE, 3), (msa.STATE_WARN, 40),
        ):
            self.assertIn("SF-MSA-2026-0001", msa.gate_message(state, days, "SF-MSA-2026-0001", TODAY))


class TestEffectiveRate(unittest.TestCase):
    def test_a_line_with_no_dates_is_always_in_force(self):
        self.assertEqual(msa.effective_rate([_line(rate=95)], "Journeyman", "Hourly", TODAY), 95.0)

    def test_a_line_that_has_not_started_is_not_used(self):
        lines = [_line(rate=110, start="2027-01-01")]
        self.assertIsNone(msa.effective_rate(lines, "Journeyman", "Hourly", TODAY))

    def test_a_line_that_has_ended_is_not_used(self):
        lines = [_line(rate=85, end="2026-01-01")]
        self.assertIsNone(msa.effective_rate(lines, "Journeyman", "Hourly", TODAY))

    def test_the_most_recently_started_line_wins(self):
        """A newer schedule supersedes an older one."""
        lines = [_line(rate=85, start="2024-01-01"), _line(rate=95, start="2026-01-01")]
        self.assertEqual(msa.effective_rate(lines, "Journeyman", "Hourly", TODAY), 95.0)

    def test_classification_and_type_both_have_to_match(self):
        lines = [_line(classification="Apprentice", rate=60), _line(rate_type="Daily", rate=700)]
        self.assertIsNone(msa.effective_rate(lines, "Journeyman", "Hourly", TODAY))
        self.assertEqual(msa.effective_rate(lines, "Apprentice", "Hourly", TODAY), 60.0)

    def test_whitespace_does_not_hide_a_match(self):
        lines = [{"classification": " Journeyman ", "rate_type": " Hourly ", "rate": 95}]
        self.assertEqual(msa.effective_rate(lines, "Journeyman", "Hourly", TODAY), 95.0)

    def test_nothing_matching_is_none_not_zero(self):
        """Zero would read as a free subcontractor."""
        self.assertIsNone(msa.effective_rate([], "Journeyman", "Hourly", TODAY))


class TestRateDrift(unittest.TestCase):
    def test_a_matching_snapshot_has_no_drift(self):
        snapshot = {"rate_journeyman": 95.0}
        self.assertEqual(msa.rate_drift(snapshot, [_line(rate=95.0)], TODAY), [])

    def test_a_changed_rate_is_reported_with_both_figures(self):
        snapshot = {"rate_journeyman": 85.0}
        out = msa.rate_drift(snapshot, [_line(rate=95.0)], TODAY)
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["snapshot"], out[0]["current"]), (85.0, 95.0))
        self.assertEqual(out[0]["field"], "rate_journeyman")

    def test_a_rate_the_msa_does_not_publish_is_not_drift(self):
        """Plenty of Statements of Work carry a negotiated figure the schedule never listed;
        calling that a discrepancy makes the check noise on day one."""
        self.assertEqual(msa.rate_drift({"rate_journeyman": 85.0}, [], TODAY), [])

    def test_a_blank_snapshot_field_is_not_drift(self):
        self.assertEqual(msa.rate_drift({"rate_journeyman": None}, [_line(rate=95)], TODAY), [])
        self.assertEqual(msa.rate_drift({"rate_journeyman": ""}, [_line(rate=95)], TODAY), [])

    def test_float_noise_is_not_a_rate_change(self):
        snapshot = {"rate_journeyman": 95.0}
        self.assertEqual(msa.rate_drift(snapshot, [_line(rate=95.001)], TODAY), [])

    def test_the_markup_percent_maps_onto_its_own_line(self):
        snapshot = {"materials_markup_percent": 15.0}
        lines = [_line(classification="Materials", rate_type="Markup %", rate=20.0)]
        out = msa.rate_drift(snapshot, lines, TODAY)
        self.assertEqual(out[0]["field"], "materials_markup_percent")

    def test_every_snapshot_field_maps_to_a_known_rate_type(self):
        """A typo in the mapping silently means that field is never checked for drift."""
        for _field, _classification, rate_type, _label in msa.SNAPSHOT_RATES:
            self.assertIn(rate_type, msa.RATE_TYPES)

    def test_drift_is_reported_in_a_stable_order(self):
        snapshot = {"rate_journeyman": 1, "rate_apprentice": 1, "materials_markup_percent": 1}
        lines = [
            _line(rate=2),
            _line(classification="Apprentice", rate=2),
            _line(classification="Materials", rate_type="Markup %", rate=2),
        ]
        out = msa.rate_drift(snapshot, lines, TODAY)
        self.assertEqual([d["field"] for d in out],
                         ["rate_journeyman", "rate_apprentice", "materials_markup_percent"])


class TestOverlappingRateLines(unittest.TestCase):
    def test_two_open_ended_lines_for_the_same_thing_clash(self):
        """Two rates in force at once means the answer depends on which row a query read first,
        and an invoice checked against it could be right or wrong depending on nothing."""
        out = msa.overlapping_rate_lines([_line(rate=95), _line(rate=110)])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["rows"], (1, 2))

    def test_consecutive_periods_do_not_clash(self):
        lines = [
            _line(rate=85, start="2024-01-01", end="2025-12-31"),
            _line(rate=95, start="2026-01-01"),
        ]
        self.assertEqual(msa.overlapping_rate_lines(lines), [])

    def test_touching_periods_do_clash(self):
        """Both are in force on the shared day, and an invoice for that day is ambiguous."""
        lines = [
            _line(rate=85, start="2024-01-01", end="2026-01-01"),
            _line(rate=95, start="2026-01-01"),
        ]
        self.assertEqual(len(msa.overlapping_rate_lines(lines)), 1)

    def test_different_classifications_never_clash(self):
        lines = [_line(rate=95), _line(classification="Apprentice", rate=60)]
        self.assertEqual(msa.overlapping_rate_lines(lines), [])

    def test_empty_is_tolerated(self):
        self.assertEqual(msa.overlapping_rate_lines([]), [])
        self.assertEqual(msa.overlapping_rate_lines(None), [])


class TestOrderProject(unittest.TestCase):
    def test_the_row_wins_when_it_names_a_job(self):
        self.assertEqual(msa.order_project("PRJ-00566", "PRJ-00001"), "PRJ-00566")

    def test_it_falls_back_to_the_header(self):
        """The case that loses money: 32 of 148 live pending lines are exactly this."""
        self.assertEqual(msa.order_project(None, "PRJ-00566"), "PRJ-00566")
        self.assertEqual(msa.order_project("", "PRJ-00566"), "PRJ-00566")

    def test_whitespace_counts_as_empty(self):
        self.assertEqual(msa.order_project("   ", "PRJ-00566"), "PRJ-00566")

    def test_neither_is_none_rather_than_a_blank_string(self):
        self.assertIsNone(msa.order_project(None, None))
        self.assertIsNone(msa.order_project("", "  "))

    def test_a_row_project_is_not_overridden_by_a_different_header(self):
        """Both fields are populated and disagree on real data; the row is the more specific."""
        self.assertEqual(msa.order_project("PRJ-00566", "PRJ-00999"), "PRJ-00566")


class TestTheGlue(unittest.TestCase):
    """Source-level guards on the frappe half and the two schema edits."""

    @classmethod
    def setUpClass(cls):
        import json
        import re

        app = REPO_ROOT / "erpnext_enhancements"
        cls.source = (app / "quality" / "msa_enforcement.py").read_text(encoding="utf-8")
        cls.code = re.sub(r'"""[\s\S]*?"""', "", cls.source)
        cls.code = re.sub(r"^\s*#.*$", "", cls.code, flags=re.MULTILINE)
        cls.template = (
            app / "templates" / "contracts" / "statement_of_work.html"
        ).read_text(encoding="utf-8")
        with open(
            app / "project_enhancements" / "doctype" / "project_contract" / "project_contract.json",
            encoding="utf-8",
        ) as handle:
            cls.contract = {f["fieldname"]: f for f in json.load(handle)["fields"]}
        with open(app / "fixtures" / "custom_field.json", encoding="utf-8") as handle:
            cls.fields = {r["name"]: r for r in json.load(handle)}

    def test_the_snapshot_is_never_rewritten_from_the_schedule(self):
        """A signed agreement prints its own copy. A live re-read would change a document
        somebody has already signed, which is the one thing this must not do."""
        for field, _c, _t, _l in msa.SNAPSHOT_RATES:
            self.assertNotRegex(
                self.code,
                rf"doc\.{field}\s*=",
                f"{field} is assigned in the glue; the frozen snapshot must only be reported on",
            )

    def test_only_a_known_past_expiry_can_block(self):
        self.assertIn("msa.blocks_issue(", self.code)
        self.assertRegex(self.code, r"if msa\.blocks_issue\([\s\S]{0,80}?frappe\.throw")

    def test_the_expiry_in_force_is_read_only_because_it_is_derived(self):
        self.assertEqual(self.contract["msa_expiry_effective"].get("read_only"), 1)

    def test_the_override_is_not_read_only(self):
        """It is the one a person is meant to set when the agreement states its own date."""
        self.assertNotEqual(self.contract["msa_expires_on"].get("read_only"), 1)

    def test_the_rate_schedule_only_shows_on_an_msa(self):
        self.assertIn("msa", self.contract["rate_schedule"].get("depends_on", ""))

    def test_the_purchase_order_rollup_uses_the_union(self):
        """Either project field alone drops orders, silently: 40 of 148 live pending lines carry
        no row project and 32 of those sit under a header that names the job."""
        # Against the UNSTRIPPED source: the query is a triple-quoted string, which the
        # docstring stripper removes along with the real docstrings.
        self.assertIn("ifnull(nullif(poi.project, ''), po.project)", self.source)

    def test_the_purchase_order_check_cannot_fail_a_save(self):
        self.assertRegex(
            self.code, r"def on_purchase_order_validate\([\s\S]{0,1400}?except Exception:"
        )

    def test_the_sweep_cannot_take_down_the_daily_queue(self):
        self.assertRegex(
            self.code, r"def sweep_expiring_agreements\([\s\S]{0,600}?except Exception:"
        )

    def test_the_purchase_order_links_exist(self):
        for name in ("custom_msa_contract", "custom_sow_contract", "custom_scope_of_work",
                     "custom_change_order"):
            self.assertIn(f"Purchase Order-{name}", self.fields, name)

    def test_the_contract_print_falls_back_when_there_is_no_locked_scope(self):
        """Most Statements of Work have no Scope of Work today. Printing an empty hold-point
        table instead of the manual checkboxes would make those contracts worse."""
        self.assertIn("{% if hold_points %}", self.template)
        self.assertIn("{% else %}", self.template)
        self.assertIn("Pre-pour inspection (before concrete placement)", self.template)

    def test_the_hold_point_table_prints_the_standard_not_just_the_name(self):
        """A hold point with no acceptance standard is the checkbox again, with extra steps."""
        self.assertIn("hp.pass_standard", self.template)
        self.assertIn("hp.criterion", self.template)


if __name__ == "__main__":
    unittest.main()
