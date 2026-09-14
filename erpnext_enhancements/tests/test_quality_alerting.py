"""Bench-free tests for Critical NCR alerting (WI-075 sub-phase G).

Three failures, all of which look like success from the outside:

* **An alert nobody received.** A prod deploy `FLUSHDB`s the queue redis and destroys every
  pending job. The acknowledgement row still exists, still says a recipient was listed, and
  nobody was ever emailed. "We enqueued it" is not evidence.
* **One person, two emails.** On a site with nineteen enabled users, the same person holding two
  of the three notified roles is the expected case. Two rows means two emails and an
  acknowledgement that can be half-done.
* **A nag that becomes noise.** A sweep that re-sends hourly trains people to filter it, and a
  filtered Critical alert is worse than no alert.

Run: python -m unittest erpnext_enhancements.tests.test_quality_alerting
"""

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.quality import alerting
from erpnext_enhancements.quality.lifecycle import SEVERITY_CRITICAL

NOW = datetime(2026, 9, 14, 12, 0, 0)


def _ack(user="a@x.com", notified=None, acknowledged=None, reminded=None, role="Project Manager"):
    return {
        "user": user,
        "role_label": role,
        "notified_on": notified,
        "acknowledged_on": acknowledged,
        "last_reminded_on": reminded,
    }


class TestAlertIsDue(unittest.TestCase):
    def test_critical_and_never_sent_is_due(self):
        self.assertTrue(alerting.alert_is_due("Critical", SEVERITY_CRITICAL, None))

    def test_minor_and_major_are_never_due(self):
        for severity in ("Minor", "Major", "", None):
            self.assertFalse(alerting.alert_is_due(severity, SEVERITY_CRITICAL, None))

    def test_resaving_a_critical_ncr_does_not_mail_again(self):
        self.assertFalse(alerting.alert_is_due("Critical", SEVERITY_CRITICAL, NOW))

    def test_blank_sent_stamp_counts_as_never_sent(self):
        for blank in ("", "   ", None):
            self.assertTrue(alerting.alert_is_due("Critical", SEVERITY_CRITICAL, blank))


class TestRecipients(unittest.TestCase):
    def test_all_three_roles_are_notified(self):
        out = alerting.dedupe_recipients(
            [("pm@x.com", "Project Manager"), ("ops@x.com", "Production Manager"), ("ceo@x.com", "President")]
        )
        self.assertEqual([u for u, _r in out], ["pm@x.com", "ops@x.com", "ceo@x.com"])

    def test_one_person_holding_two_roles_gets_one_row(self):
        """The expected case on a nineteen-user site, not an edge case."""
        out = alerting.dedupe_recipients(
            [("ceo@x.com", "Production Manager"), ("ceo@x.com", "President")]
        )
        self.assertEqual(out, [("ceo@x.com", "Production Manager")])

    def test_the_label_records_the_first_reason_they_qualified(self):
        out = alerting.dedupe_recipients([("x@x.com", "President"), ("x@x.com", "Project Manager")])
        self.assertEqual(out[0][1], "President")

    def test_administrator_and_guest_are_never_paged(self):
        out = alerting.dedupe_recipients(
            [("Administrator", "President"), ("Guest", "Project Manager"), ("real@x.com", "President")]
        )
        self.assertEqual(out, [("real@x.com", "President")])

    def test_an_unresolved_role_contributes_nothing_rather_than_a_blank_row(self):
        out = alerting.dedupe_recipients([(None, "Production Manager"), ("", "President")])
        self.assertEqual(out, [])

    def test_no_candidates_is_not_an_error(self):
        self.assertEqual(alerting.dedupe_recipients([]), [])
        self.assertEqual(alerting.dedupe_recipients(None), [])


class TestRenagDue(unittest.TestCase):
    def test_acknowledged_is_never_chased(self):
        self.assertFalse(alerting.renag_due(NOW - timedelta(days=9), NOW, NOW, 24))

    def test_a_row_that_was_never_notified_is_due_immediately(self):
        """The deploy case. The FLUSHDB destroyed the enqueue; the row claims a recipient and
        nobody was ever told. This is what makes the sweep a re-drive rather than a nag."""
        self.assertTrue(alerting.renag_due(None, None, NOW, 24))

    def test_inside_the_sla_is_not_chased(self):
        self.assertFalse(alerting.renag_due(NOW - timedelta(hours=5), None, NOW, 24))

    def test_past_the_sla_is_chased(self):
        self.assertTrue(alerting.renag_due(NOW - timedelta(hours=25), None, NOW, 24))

    def test_already_chased_today_is_not_chased_again(self):
        """Hourly re-sends train people to filter the one message that must not be filtered."""
        self.assertFalse(
            alerting.renag_due(NOW - timedelta(days=3), None, NOW, 24, last_reminded_on=NOW - timedelta(hours=2))
        )

    def test_chased_yesterday_is_chased_again_today(self):
        self.assertTrue(
            alerting.renag_due(NOW - timedelta(days=3), None, NOW, 24, last_reminded_on=NOW - timedelta(days=1))
        )

    def test_a_zero_sla_does_not_mean_chase_every_sweep(self):
        """A misconfigured 0 would otherwise re-nag on every hourly run."""
        self.assertFalse(alerting.renag_due(NOW - timedelta(minutes=5), None, NOW, 0))

    def test_string_timestamps_are_accepted(self):
        """Frappe hands these back as strings from a raw query and as datetimes from a doc."""
        self.assertTrue(alerting.renag_due("2026-09-10 08:00:00", None, "2026-09-14 12:00:00", 24))
        self.assertFalse(alerting.renag_due("2026-09-14 11:00:00", None, "2026-09-14 12:00:00", 24))

    def test_an_unparseable_now_decides_nothing_rather_than_chasing_everyone(self):
        self.assertFalse(alerting.renag_due(None, None, "not a date", 24))


class TestOutstanding(unittest.TestCase):
    def test_it_returns_only_the_rows_that_owe_a_chase(self):
        rows = [
            _ack("ack@x.com", notified=NOW - timedelta(days=2), acknowledged=NOW - timedelta(days=1)),
            _ack("late@x.com", notified=NOW - timedelta(days=2)),
            _ack("fresh@x.com", notified=NOW - timedelta(hours=1)),
            _ack("never@x.com"),
        ]
        self.assertEqual(
            [r["user"] for r in alerting.outstanding(rows, NOW, 24)], ["late@x.com", "never@x.com"]
        )

    def test_a_fully_acknowledged_alert_owes_nothing(self):
        rows = [_ack("a@x.com", notified=NOW - timedelta(days=5), acknowledged=NOW)]
        self.assertEqual(alerting.outstanding(rows, NOW, 24), [])

    def test_empty_is_tolerated(self):
        self.assertEqual(alerting.outstanding([], NOW, 24), [])
        self.assertEqual(alerting.outstanding(None, NOW, 24), [])


class TestAcknowledgementState(unittest.TestCase):
    def test_counts_acknowledged_over_total(self):
        rows = [_ack("a@x.com", acknowledged=NOW), _ack("b@x.com"), _ack("c@x.com", acknowledged=NOW)]
        self.assertEqual(alerting.acknowledgement_state(rows), (2, 3))

    def test_no_rows_is_zero_of_zero_not_a_crash(self):
        self.assertEqual(alerting.acknowledgement_state([]), (0, 0))


class TestMayAcknowledge(unittest.TestCase):
    def test_a_notified_recipient_may(self):
        self.assertTrue(alerting.may_acknowledge([_ack("pm@x.com")], "pm@x.com"))

    def test_somebody_who_was_never_told_may_not(self):
        """An acknowledgement from a person who was never notified is not evidence of anything,
        so it is refused rather than recorded as a reassuring row."""
        self.assertFalse(alerting.may_acknowledge([_ack("pm@x.com")], "passerby@x.com"))

    def test_an_empty_list_refuses_everybody(self):
        self.assertFalse(alerting.may_acknowledge([], "anyone@x.com"))


if __name__ == "__main__":
    unittest.main()
