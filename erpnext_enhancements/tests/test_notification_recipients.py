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


def fixture_docs():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def patch_literal(name):
    for node in ast.walk(ast.parse(PATCH.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in the patch")


class TestTheFixturedSix(unittest.TestCase):
    def test_every_one_has_a_group_recipient(self):
        for doc in fixture_docs():
            rows = doc.get("recipients") or []
            self.assertTrue(rows, f"{doc['name']} has no recipients at all")
            self.assertTrue(
                any(row.get("cc") for row in rows),
                f"{doc['name']} has no cc address",
            )

    def test_no_role_recipients_remain(self):
        """A role resolves to individuals, which is the thing being replaced."""
        for doc in fixture_docs():
            for row in doc.get("recipients") or []:
                self.assertIsNone(
                    row.get("receiver_by_role"),
                    f"{doc['name']} still notifies the {row.get('receiver_by_role')} role",
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

    def test_the_fixtures_are_still_allowlisted(self):
        """A fixture file is only synced for the names in the hooks filter."""
        hooks = HOOKS.read_text(encoding="utf-8")
        for doc in fixture_docs():
            self.assertIn(f'"{doc["name"]}"', hooks, f"{doc['name']} is not in the hooks allowlist")


class TestThePatchedSix(unittest.TestCase):
    def test_every_target_is_a_known_group(self):
        for name, addresses in patch_literal("REPOINT").items():
            for address in addresses:
                self.assertIn(address, VALID_GROUPS, f"{name} -> unknown address {address}")

    def test_nothing_mails_the_whole_company(self):
        for name, addresses in patch_literal("REPOINT").items():
            self.assertNotIn(EVERYONE, addresses, f"{name} would mail the entire company")

    def test_it_does_not_touch_the_fixtured_six(self):
        """Two sources of truth for one record is how they drift."""
        fixtured = {doc["name"] for doc in fixture_docs()}
        overlap = sorted(fixtured & set(patch_literal("REPOINT")))
        self.assertEqual(overlap, [], f"handled by both the fixture and the patch: {overlap}")

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
