"""KPI Snapshot's DocPerms, and the row filter that has to come with them.

``KPI Snapshot`` granted read to System Manager alone, which made the planned
``kpi_dashboard_status`` assistant tool unbuildable: a tool's
``requires_permission`` controls per-user *visibility* as well as execution, so it
would have been invisible to exactly the department managers who own the numbers
(TASK-2026-01187).

Widening is only half of it, and the half that is easy to get wrong. **A DocPerm
is doctype-wide.** An Accounts Manager with ``read`` on KPI Snapshot can read
every snapshot on the site — Sales, HR, Executive — through the desk list view or
``/api/resource/KPI Snapshot``, no matter what ``api/kpi.py::_can_view`` says,
because that function guards two whitelisted endpoints and not the doctype. So the
widening ships with a ``permission_query_conditions`` hook, and this module pins
both halves together: neither is correct without the other.

Bench-free. The doctype JSON and ``hooks.py`` are read as data — ``hooks.py``
through the AST rather than imported, because importing it pulls the whole app in.

Run: python -m unittest erpnext_enhancements.tests.test_kpi_snapshot_permissions
"""

import ast
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
SNAPSHOT_JSON = APP / "kpi_dashboards/doctype/kpi_snapshot/kpi_snapshot.json"
KPI_API = APP / "api/kpi.py"
HOOKS = APP / "hooks.py"
PERMISSIONS = APP / "kpi_dashboards/permissions.py"


def department_roles():
    """``DEPARTMENT_ROLES`` from api/kpi.py, via the AST.

    Not a regex over the source. The first attempt at generating the DocPerm rows
    did regex it and picked up ``"HR User"`` out of the comment that says, in as
    many words, not to use it — because every employee on this site holds that
    role. Comments are not data, and on this doctype the difference is whether the
    whole company can read the HR KPIs.
    """
    tree = ast.parse(KPI_API.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "DEPARTMENT_ROLES" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("DEPARTMENT_ROLES not found in api/kpi.py")


def snapshot_permissions():
    return json.loads(SNAPSHOT_JSON.read_text(encoding="utf-8"))["permissions"]


def hook_dict(name):
    tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    return {}


class TestTheScanWorks(unittest.TestCase):
    def test_the_roles_are_found(self):
        mapping = department_roles()
        self.assertIn("Finance", mapping)
        self.assertIn("Accounts Manager", mapping["Finance"])

    def test_the_permissions_are_found(self):
        self.assertGreater(len(snapshot_permissions()), 1)


class TestTheDocPermsMatchTheRoleMap(unittest.TestCase):
    def test_every_department_role_can_read(self):
        expected = {role for group in department_roles().values() for role in group}
        granted = {row["role"] for row in snapshot_permissions() if row.get("read")}
        missing = sorted(expected - granted)
        self.assertEqual(missing, [], f"DEPARTMENT_ROLES roles with no read DocPerm: {missing}")

    def test_no_role_is_granted_that_the_map_does_not_name(self):
        """The direction that leaks. A role here and not in DEPARTMENT_ROLES can
        read snapshots that `_can_view` would refuse — which is how a widening
        turns into a disclosure."""
        expected = {role for group in department_roles().values() for role in group}
        granted = {row["role"] for row in snapshot_permissions()}
        extra = sorted(granted - expected - {"System Manager"})
        self.assertEqual(extra, [], f"roles granted on KPI Snapshot but not in DEPARTMENT_ROLES: {extra}")

    def test_hr_user_is_never_granted(self):
        """Named on its own because it is one keystroke from correct and every
        employee on this site holds it. api/kpi.py says so in a comment; a comment
        cannot fail a build."""
        granted = {row["role"] for row in snapshot_permissions()}
        self.assertNotIn("HR User", granted)

    def test_the_department_roles_are_read_only(self):
        """`refresh_kpi_dashboard` writes and commits. Nothing outside System
        Manager should be able to write a snapshot from the desk either."""
        for row in snapshot_permissions():
            if row["role"] == "System Manager":
                continue
            for ptype in ("write", "create", "delete", "submit", "cancel", "amend"):
                self.assertFalse(
                    row.get(ptype),
                    f"{row['role']} has {ptype} on KPI Snapshot; department roles are read-only",
                )

    def test_system_manager_keeps_full_access(self):
        rows = [row for row in snapshot_permissions() if row["role"] == "System Manager"]
        self.assertEqual(len(rows), 1)
        for ptype in ("read", "write", "create", "delete"):
            self.assertTrue(rows[0].get(ptype))


class TestTheWideningIsScoped(unittest.TestCase):
    """A DocPerm is doctype-wide. Without a row filter, `read` on KPI Snapshot
    means read on every department's numbers — which is precisely what the task
    said this change was *not* doing."""

    def test_a_query_condition_is_registered(self):
        hook = hook_dict("permission_query_conditions")
        self.assertIn("KPI Snapshot", hook)
        self.assertEqual(
            hook["KPI Snapshot"],
            "erpnext_enhancements.kpi_dashboards.permissions.get_permission_query_conditions",
        )

    def test_a_single_document_check_is_registered(self):
        """A query condition filters lists; it does not refuse a read of one
        snapshot by name."""
        hook = hook_dict("has_permission")
        self.assertIn("KPI Snapshot", hook)
        self.assertEqual(
            hook["KPI Snapshot"],
            "erpnext_enhancements.kpi_dashboards.permissions.has_permission",
        )

    def test_the_module_exists_and_defines_both(self):
        source = PERMISSIONS.read_text(encoding="utf-8")
        self.assertIn("def get_permission_query_conditions(", source)
        self.assertIn("def has_permission(", source)

    def test_the_filter_reuses_the_role_map(self):
        """Two copies of "who may see Finance" is the drift this repo has spent
        five releases on elsewhere."""
        self.assertIn("DEPARTMENT_ROLES", PERMISSIONS.read_text(encoding="utf-8"))

    def test_an_empty_department_set_is_not_an_empty_in_clause(self):
        """`IN ()` is a MariaDB syntax error, so the no-departments case has to be
        written out. It is reachable: a role could hold read through some other
        grant and own no department."""
        self.assertIn("1 = 0", PERMISSIONS.read_text(encoding="utf-8"))

    def test_the_import_is_lazy(self):
        """hooks.py is loaded during `bench migrate` on a fresh site, and
        api.kpi imports the snapshot engine, which reaches for doctypes that may
        not exist yet."""
        source = PERMISSIONS.read_text(encoding="utf-8")
        self.assertNotIn("\nfrom erpnext_enhancements.api.kpi import", source)
        self.assertIn("\tfrom erpnext_enhancements.api.kpi import", source)


if __name__ == "__main__":
    unittest.main()
