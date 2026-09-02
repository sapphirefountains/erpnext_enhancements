"""Notifications go to group addresses, except where a person is the point.

TASK-2026-01240. A role recipient resolves to whoever currently holds that role,
so a departmental alert lands in a handful of personal inboxes — and stops landing
anywhere the day somebody leaves or a role is reshuffled.

Two things this pins that are easy to get wrong:

* **`company@` is the whole-company mailing list**, so routing any routine alert
  there mails everyone. It must appear nowhere.
* **Some notifications are personal by design** and must keep their document-field
  recipients: `New ToDo Created` (`allocated_to`), `Remind Me Email` (`user`),
  `Material Request Receipt Notification` (`owner`). "Point everything at groups"
  applied literally would break the one case where a named individual is the whole
  purpose.

Also pins the fixture invariant this repo has been bitten by: a Notification
fixture **without an explicit `enabled: 1` imports disabled**, and re-disables
itself on every migrate — which is how a Days Before alert can be configured,
present, and silent.

As of v1.331.0 the fixture covers **all nineteen** live Notifications, not six:
the email design system adopted the thirteen that had only ever existed in the
site database. So the fixture, not this patch, is what sets these recipients on
every migrate — and the overlap test below pins that the two agree rather than
that they are disjoint.

Bench-free: reads the fixture JSON and the patch source.

Run: python -m unittest erpnext_enhancements.tests.test_notification_recipients
"""

import ast
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
FIXTURE = APP / "fixtures/notification.json"
PATCH = APP / "patches/repoint_notifications_to_group_emails.py"
HOOKS = APP / "hooks.py"

GROUP_DOMAIN = "@sapphirefountains.com"

# Confirmed with Nik 2026-08-07. `company@` exists but is the entire company.
VALID_GROUPS = {
    "operations@sapphirefountains.com",
    "billing@sapphirefountains.com",
    "production@sapphirefountains.com",
    "sales@sapphirefountains.com",
    "service_repair@sapphirefountains.com",
    "executive@sapphirefountains.com",
}
EVERYONE = "company@sapphirefountains.com"

# Notifications that keep a **role** recipient on purpose. Both are platform
# alerts, and the reasoning is the patch's own (confirmed with Nik): a role
# follows whoever is actually administering the system, where a shared inbox
# nobody owns does not. Everything departmental goes to a group address.
ROLE_RECIPIENT_BY_DESIGN = {"Error Log", "Integration Request"}

# Notifications aimed at a named individual by design, addressed by document
# field. "Point everything at groups" applied literally would break the one case
# where a person is the entire purpose.
PERSONAL_BY_DESIGN = {"New ToDo Created - Notify Creator and Assignee", "Remind Me Email"}


def fixture_docs():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def patch_literal(name):
    for node in ast.walk(ast.parse(PATCH.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in the patch")


class TestTheFixturedNotifications(unittest.TestCase):
    def test_every_one_has_a_group_recipient(self):
        for doc in fixture_docs():
            rows = doc.get("recipients") or []
            self.assertTrue(rows, f"{doc['name']} has no recipients at all")
            if doc["name"] in PERSONAL_BY_DESIGN or doc["name"] in ROLE_RECIPIENT_BY_DESIGN:
                continue
            self.assertTrue(
                any(row.get("cc") for row in rows),
                f"{doc['name']} has no cc address",
            )

    def test_no_role_recipients_remain(self):
        """A role resolves to individuals, which is the thing being replaced.

        Two platform alerts are exempt and named in ``ROLE_RECIPIENT_BY_DESIGN``;
        the allowlist is the point, so a *new* role recipient still fails here.
        """
        for doc in fixture_docs():
            if doc["name"] in ROLE_RECIPIENT_BY_DESIGN:
                continue
            for row in doc.get("recipients") or []:
                self.assertIsNone(
                    row.get("receiver_by_role"),
                    f"{doc['name']} still notifies the {row.get('receiver_by_role')} role",
                )

    def test_the_personal_ones_keep_their_document_field(self):
        """The exception the module docstring names, pinned rather than assumed."""
        for doc in fixture_docs():
            if doc["name"] not in PERSONAL_BY_DESIGN:
                continue
            rows = doc.get("recipients") or []
            self.assertTrue(
                any(row.get("receiver_by_document_field") for row in rows),
                f"{doc['name']} lost its document-field recipient — it is aimed at a person",
            )

    def test_every_address_is_a_known_group(self):
        for doc in fixture_docs():
            for row in doc.get("recipients") or []:
                for address in (row.get("cc") or "").split(","):
                    address = address.strip()
                    if not address:
                        continue
                    self.assertIn(
                        address, VALID_GROUPS, f"{doc['name']} -> unknown address {address}"
                    )

    def test_nothing_mails_the_whole_company(self):
        """`company@` is everyone. A routine alert there is a company-wide email."""
        self.assertNotIn(EVERYONE, FIXTURE.read_text(encoding="utf-8"))

    def test_every_fixture_is_explicitly_enabled(self):
        """Without an explicit `enabled: 1` a Notification fixture imports DISABLED
        and re-disables itself on every migrate — configured, present, and silent."""
        for doc in fixture_docs():
            self.assertEqual(doc.get("enabled"), 1, f"{doc['name']} has no explicit enabled: 1")

    def test_a_condition_is_paired_with_the_type_that_evaluates_it(self):
        """v16 `evaluate_alert` reads
        `if alert.condition_type == "Python" and alert.condition:` — a `condition` under
        any other `condition_type` is decoration, and the alert fires unconditionally.

        That these fixtures *declare* `condition_type` at all is pinned in
        `test_fixture_completeness`, alongside the rest of the fields fixture sync
        erases. This test is the narrower, semantic half: the two must agree.
        """
        for doc in fixture_docs():
            if not doc.get("condition"):
                continue
            self.assertEqual(
                doc.get("condition_type"),
                "Python",
                f"{doc['name']} has a `condition` but condition_type is "
                f"{doc.get('condition_type')!r}; frappe only evaluates it under Python",
            )


class TestTheClosedWonAlertIsGatedByTheFramework(unittest.TestCase):
    """Belt as well as braces, after the condition silently stopped being read.

    The alert used to run on `Save` with a Python condition that reimplemented the
    before/after comparison itself:
    `doc.status == "Closed Won" and doc.get_doc_before_save()
     and doc.get_doc_before_save().status != "Closed Won"`.

    Correct, and worth nothing the moment `condition_type` went NULL — the condition
    was the ONLY gate, so losing it meant an email on every save of every Opportunity.

    On `Value Change` the transition test moves into the framework, which does it in
    `evaluate_alert` before any condition is consulted and does not depend on
    `condition_type` at all:

        doc_before_save = doc.get_doc_before_save()
        if cast(fieldtype, doc.get(alert.value_changed)) == cast(fieldtype, before):
            return

    `run_notifications` also only maps `on_change` to "Value Change" when
    `not self.flags.in_insert`, so an Opportunity created directly as Closed Won still
    sends nothing — the same behaviour the old `get_doc_before_save()` guard gave.

    Same emails as before, but the worst case if a field is lost again is one mail per
    *status change* rather than one per *save*.
    """

    def alert(self):
        return next(d for d in fixture_docs() if d["name"] == "Email Team on Opportunity Won")

    def test_it_fires_on_a_value_change_not_on_every_save(self):
        self.assertEqual(self.alert()["event"], "Value Change")

    def test_the_watched_field_is_status(self):
        self.assertEqual(self.alert()["value_changed"], "status")

    def test_the_condition_no_longer_reimplements_the_transition(self):
        """Keeping the old before/after clause under Value Change would be dead code
        that reads like the gate, hiding where the gate actually is."""
        condition = self.alert()["condition"]
        self.assertNotIn("get_doc_before_save", condition)
        self.assertIn("Closed Won", condition)

    def test_the_fixtures_are_still_allowlisted(self):
        """A fixture file is only synced for the names in the hooks filter."""
        hooks = HOOKS.read_text(encoding="utf-8")
        for doc in fixture_docs():
            self.assertIn(f'"{doc["name"]}"', hooks, f"{doc['name']} is not in the hooks allowlist")


class TestTheErrorLogAlertCannotRecurse(unittest.TestCase):
    """TASK-2026-01653. The `Error Log` alert fires on every Error Log insert. When
    its own send fails, frappe's `Notification.send_notification_by_channel` logs
    the failure with `self.log_error(...)` — which inserts an Error Log whose
    `reference_doctype`/`reference_name` is this very Notification — and that
    insert fires the alert again. Nothing in the framework breaks the cycle
    (`flags.notifications_executed` is reset per document), so it runs until
    `RecursionError`; when that lands inside mysqlclient's `_query` the connection
    is left mid-result and every later query fails with `(2014) Commands out of
    sync`. Fired twice on prod (2026-08-11: 213 rows; 2026-08-31: 107 rows, which
    is what wedged the WI-071 attachment backfill).

    The gate is the alert's own `condition`: it declines the one self-referential
    row, so a failed send costs two Error Log rows and stops. `doc.get` never
    raises, which matters — an exception inside the condition would loop through
    `evaluate_alert`'s own `except` instead.
    """

    def alert(self):
        return next(d for d in fixture_docs() if d["name"] == "Error Log")

    @staticmethod
    def evaluate(condition, doc):
        # A dict mirrors `BaseDocument.get(key)` for the two keys the condition reads.
        return eval(condition, {"__builtins__": {}}, {"doc": doc})

    def test_it_is_a_python_condition(self):
        self.assertEqual(self.alert().get("condition_type"), "Python")
        self.assertTrue(self.alert().get("condition"))

    def test_it_declines_its_own_send_failure_row(self):
        cond = self.alert()["condition"]
        self.assertFalse(
            self.evaluate(cond, {"reference_doctype": "Notification", "reference_name": "Error Log"})
        )

    def test_it_still_fires_for_everything_else(self):
        cond = self.alert()["condition"]
        for doc in (
            {"reference_doctype": "Notification", "reference_name": "New Lead Created"},
            {"reference_doctype": None, "reference_name": None},
            {"reference_doctype": "Journal Entry", "reference_name": "ACC-JV-2026-00001"},
            {},
        ):
            self.assertTrue(self.evaluate(cond, doc), doc)

    def test_every_error_log_alert_carries_the_gate(self):
        """A second alert on Error Log would need the same clause, or it re-opens the loop."""
        for doc in fixture_docs():
            if doc.get("document_type") != "Error Log":
                continue
            self.assertIn(
                "reference_doctype",
                doc.get("condition") or "",
                f"{doc['name']} watches Error Log without a self-reference gate",
            )


class TestThePatchedSix(unittest.TestCase):
    def test_every_target_is_a_known_group(self):
        for name, addresses in patch_literal("REPOINT").items():
            for address in addresses:
                self.assertIn(address, VALID_GROUPS, f"{name} -> unknown address {address}")

    def test_nothing_mails_the_whole_company(self):
        for name, addresses in patch_literal("REPOINT").items():
            self.assertNotIn(EVERYONE, addresses, f"{name} would mail the entire company")

    def test_it_agrees_with_the_fixture_where_they_overlap(self):
        """The patch and the fixture must not disagree about one record.

        Until v1.331.0 these sets were disjoint and this test asserted exactly
        that — two sources of truth for one record is how they drift. The email
        design system fixtured all nineteen live Notifications, so the six this
        patch repointed are now fixtured too, and the fixture is what applies on
        every migrate.

        Disjointness is therefore no longer available, so the stronger property
        is pinned instead: where both name a record they must name the same
        addresses. Editing the recipients in one place and not the other is the
        actual failure, and this catches it.
        """
        fixtured = {doc["name"]: doc for doc in fixture_docs()}
        for name, addresses in patch_literal("REPOINT").items():
            if name not in fixtured:
                continue
            rows = fixtured[name].get("recipients") or []
            in_fixture = {
                part.strip()
                for row in rows
                for part in (row.get("cc") or "").split(",")
                if part.strip()
            }
            self.assertEqual(
                in_fixture,
                set(addresses),
                f"{name}: the patch says {sorted(addresses)} but the fixture says "
                f"{sorted(in_fixture)}. The fixture wins on every migrate — make them agree.",
            )

    def test_document_field_recipients_are_preserved(self):
        """Personal by design — `allocated_to`, `user`, `owner`. Applying "point
        everything at groups" literally would break the one case where a named
        individual is the whole purpose."""
        source = PATCH.read_text(encoding="utf-8")
        self.assertIn("row.receiver_by_document_field", source)
        self.assertIn("kept = [", source)

    def test_it_is_idempotent(self):
        source = PATCH.read_text(encoding="utf-8")
        self.assertIn("continue", source)
        self.assertIn('(rows[0].cc or "") == target', source)

    def test_a_missing_notification_is_not_an_error(self):
        """These six were created in the UI; a fresh site legitimately has none."""
        self.assertIn('frappe.db.exists("Notification", name)', PATCH.read_text(encoding="utf-8"))


class TestTheDeliberateExceptions(unittest.TestCase):
    """Named individually so the reasoning survives the next person reading the
    diff and thinking the job was left half done."""

    def test_the_technical_alerts_are_not_repointed(self):
        """A role follows whoever is actually administering the system; a shared
        inbox nobody owns does not. Confirmed with Nik."""
        repoint = patch_literal("REPOINT")
        for name in ("Error Log", "Integration Request"):
            self.assertNotIn(name, repoint)

    def test_the_personal_notifications_are_not_repointed(self):
        repoint = patch_literal("REPOINT")
        for name in ("New ToDo Created - Notify Creator and Assignee", "Remind Me Email"):
            self.assertNotIn(name, repoint)

    def test_the_already_grouped_ones_are_not_touched(self):
        repoint = patch_literal("REPOINT")
        for name in (
            "Email Team on Opportunity Won",
            "Material Request Received",
            "Material Request Submission Notification",
        ):
            self.assertNotIn(name, repoint)


if __name__ == "__main__":
    unittest.main()
