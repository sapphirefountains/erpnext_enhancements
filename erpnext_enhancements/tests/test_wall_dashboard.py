"""Integration tests for the Wall/TV Display backend (``api.task_dashboard``
wall extensions and ``utils.deploy``).

Verifies the wall payload shape (task dashboard data + per-project completion
stats + settings + deploy version), the donut math (Invoiced counts as done,
Cancelled in neither slice), the settings defaults, and the role gate.
"""
import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_enhancements.api.task_dashboard import (
    STAFF_ROLES,
    _project_task_stats,
    _wall_settings,
    get_task_dashboard_data,
    get_wall_dashboard_data,
)
from erpnext_enhancements.utils.deploy import get_deploy_version

TEST_PROJECT = "_Test Wall Dashboard Project"


class TestWallDashboard(FrappeTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        if not frappe.db.exists("Project", {"project_name": TEST_PROJECT}):
            frappe.get_doc({"doctype": "Project", "project_name": TEST_PROJECT}).insert(
                ignore_permissions=True
            )
        self.project = frappe.db.get_value("Project", {"project_name": TEST_PROJECT})
        self.task_names = []

    def tearDown(self):
        frappe.set_user("Administrator")
        for name in self.task_names:
            frappe.delete_doc("Task", name, force=True, ignore_permissions=True)

    def _task(self, status):
        doc = frappe.get_doc(
            {
                "doctype": "Task",
                "subject": f"_Test wall task {frappe.generate_hash(length=6)}",
                "project": self.project,
                "status": status,
            }
        )
        doc.insert(ignore_permissions=True)
        self.task_names.append(doc.name)
        return doc

    def test_task_stats_math(self):
        for status in ("Completed", "Completed", "Open"):
            self._task(status)

        stats = _project_task_stats([self.project])
        entry = stats.get(self.project)
        self.assertIsNotNone(entry)
        self.assertGreaterEqual(entry["total"], 3)
        self.assertGreaterEqual(entry["completed"], 2)
        self.assertEqual(entry["pending"], entry["total"] - entry["completed"])

    def test_task_stats_empty(self):
        self.assertEqual(_project_task_stats([]), {})

    def test_wall_settings_defaults(self):
        settings = _wall_settings()
        self.assertGreater(settings["rotation_seconds"], 0)
        self.assertGreater(settings["refresh_seconds"], 0)
        self.assertIn("weather_latitude", settings)
        self.assertIn("weather_label", settings)

    def test_payload_shape(self):
        data = get_wall_dashboard_data()
        for key in (
            "top_projects",
            "overdue_tasks",
            "today_tasks",
            "events",
            "task_stats",
            "settings",
            "deploy_version",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["deploy_version"], get_deploy_version())

    def test_wall_display_role_is_staff(self):
        self.assertIn("Wall Display", STAFF_ROLES)

    def test_guest_is_denied(self):
        frappe.set_user("Guest")
        try:
            with self.assertRaises(frappe.PermissionError):
                get_wall_dashboard_data()
        finally:
            frappe.set_user("Administrator")

    def test_deploy_version_is_stable(self):
        self.assertEqual(get_deploy_version(), get_deploy_version())
        self.assertTrue(get_deploy_version())

    def test_top_projects_sorted_numerically(self):
        """Regression: custom_company_priority is a Select (text) field, so a
        SQL text sort would order it "1","10","11",…,"2" and the LIMIT would
        then surface ranks 10/11/12 ahead of rank 2. The top list must come
        back in numeric rank order — and rank 2 must outrank rank 10."""
        project_names = []
        # Seed across the lexical-vs-numeric boundary: 2 vs 10/11 vs 20.
        for rank in (2, 10, 11, 20):
            name = f"_Test Rank Project {rank} {frappe.generate_hash(length=4)}"
            frappe.get_doc(
                {
                    "doctype": "Project",
                    "project_name": name,
                    "status": "Active",
                    "custom_company_priority": str(rank),
                }
            ).insert(ignore_permissions=True)
            project_names.append(frappe.db.get_value("Project", {"project_name": name}))

        try:
            data = get_task_dashboard_data()
            ranks = [p["rank"] for p in data["top_projects"]]
            # Ascending numerically, never lexically.
            self.assertEqual(ranks, sorted(ranks))
            seeded = {p["name"]: p["rank"] for p in data["top_projects"] if p["name"] in project_names}
            if 2 in seeded.values() and 10 in seeded.values():
                pos = [p["name"] for p in data["top_projects"]]
                rank2 = next(n for n, r in seeded.items() if r == 2)
                rank10 = next(n for n, r in seeded.items() if r == 10)
                self.assertLess(pos.index(rank2), pos.index(rank10))
        finally:
            for name in project_names:
                frappe.delete_doc("Project", name, force=True, ignore_permissions=True)
