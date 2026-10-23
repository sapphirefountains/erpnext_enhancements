"""Two things Nik had taken off the Project form (v1.716.2, 2026-10-22).

* "Production tracking section in the project doctype needs to be hidden as well": the
  ``Production Tracking`` Section Break (``custom_production_section``) is ``hidden: 1``. Hiding a
  Section Break hides everything in it (build status, bid cost, production dates, quality sign-off).
  The fields stay, because other code reads and writes them without the form: the Production Builds
  Kanban board drags ``custom_build_status``, quality scheduling reads it, the Project Brief prints
  the lot. A hidden field still saves, so nothing here stops them.
* "Move the Activity Log to only display under Comments tab of the Project view": the form timeline
  shows on Project's ``custom_comments`` tab and on no other. This is the shared
  ``activity_first_tab_only.js`` given a named home tab for Project, not a second script; the
  behaviour runs in ``scripts/test_activity_home_tab.js`` against the real script.

(The "Planner schedule" section hidden in the same release is pinned in
``test_planner_publish_baseline``.)

Bench-free: reads files and runs node, no ``frappe`` stub.

Run: python -m unittest erpnext_enhancements.tests.test_project_form_hides
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
FIXTURE = APP / "fixtures" / "custom_field.json"
SCRIPT = APP / "public/js/global_enhancements/activity_first_tab_only.js"
HARNESS = REPO / "scripts" / "test_activity_home_tab.js"

#: The fields inside the Production Tracking section, in the order the form lays them out.
PRODUCTION_FIELDS = (
	"custom_build_status",
	"custom_bid_cost",
	"custom_column_break_production",
	"custom_production_started",
	"custom_production_completed",
	"custom_quality_sign_off_by",
	"custom_quality_sign_off_date",
)


def _records():
	return {r["name"]: r for r in json.loads(FIXTURE.read_text(encoding="utf-8"))}


class TestProductionTrackingIsHidden(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.records = _records()

	def test_the_section_is_hidden(self):
		section = self.records["Project-custom_production_section"]
		self.assertEqual(section["fieldtype"], "Section Break")
		self.assertEqual(section["label"], "Production Tracking")
		self.assertEqual(section["hidden"], 1)

	def test_the_fields_are_kept_and_not_individually_required(self):
		"""Hidden, not deleted: a required hidden field would make every Project unsaveable."""
		for fieldname in PRODUCTION_FIELDS:
			record = self.records["Project-" + fieldname]
			self.assertEqual(record.get("reqd") or 0, 0, fieldname)

	def test_the_neighbouring_sections_are_not_hidden(self):
		"""Build Deliverables above it and Service Scope below it stay on the Scope tab."""
		for name in ("Project-custom_build_deliverables", "Project-custom_service_scope", "Project-custom_scope"):
			self.assertEqual(self.records[name]["hidden"], 0, name)

	def test_the_readers_still_find_their_fields(self):
		"""Quality scheduling and the Kanban board read the status; a rename would break them."""
		select = self.records["Project-custom_build_status"]
		self.assertEqual(select["fieldtype"], "Select")


class TestTheFeedLivesOnTheCommentsTab(unittest.TestCase):
	def test_project_names_its_comments_tab_as_home(self):
		script = SCRIPT.read_text(encoding="utf-8")
		self.assertRegex(script, r'HOME_TABS = \{ Project: "custom_comments" \}')

	def test_that_tab_exists_on_project(self):
		tab = _records()["Project-custom_comments"]
		self.assertEqual((tab["dt"], tab["fieldtype"], tab["label"]), ("Project", "Tab Break", "Comments"))
		self.assertEqual(tab["hidden"], 0)

	def test_it_stays_one_shared_script(self):
		"""No per-doctype copy, no Project form handler of its own."""
		script = SCRIPT.read_text(encoding="utf-8")
		self.assertNotIn("frappe.ui.form.on", script)
		for path in (APP / "public" / "js").rglob("*.js"):
			if path == SCRIPT:
				continue
			self.assertNotIn("ee-activity-off-tab", path.read_text(encoding="utf-8", errors="ignore"), path.name)

	def test_no_project_script_hides_or_moves_the_footer_itself(self):
		"""The timeline's host is `.form-footer`; only the shared script and its stylesheet own it."""
		for path in (APP / "public" / "js" / "project_enhancements").rglob("*.js"):
			text = re.sub(r"//.*|/\*.*?\*/", "", path.read_text(encoding="utf-8", errors="ignore"), flags=re.S)
			self.assertNotIn(".form-footer", text, path.name)

	def test_the_behaviour_on_every_tab(self):
		"""Runs the real script against a fake DOM: shown on Comments, hidden elsewhere, Details included."""
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not on PATH")
		result = subprocess.run([node, str(HARNESS)], capture_output=True, text=True, timeout=60)
		self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
	unittest.main()
