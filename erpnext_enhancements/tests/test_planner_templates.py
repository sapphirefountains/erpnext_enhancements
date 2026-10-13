# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Project Planner T3 (v1.608.0): phase templates and task bundles for the Build out grid.

What this pins, because each part can fail quietly:

* **The seed is DOC-0041, corrected.** Phases 100-700 only (800 is the maintenance contracts'), no
  "Merchanical", no second "Exterior Fountains", only the corrected catalog's cost codes (never one of
  DOC-0041's), one Project management task per phase, and materials on each phase's procurement
  checklist, never as tasks. The six starter bundles are Nik's list, in order, hours blank, marked
  *Needs review*.
* **The sequence.** Inside a phase or bundle the tasks run in order (PM aside); a phase's first tasks
  wait on the last tasks of the nearest picked phases before it; 500 and 600 both wait on 400 and 700
  waits on both; an unpicked phase is walked through; 800 is never offered, whatever is asked.
* **The endpoints** read a template the way the patch writes it (a seeded fake site), answer rows in
  ``get_copy_source``'s shape, compose bundles after the phases with their own numbering, and refuse
  what they should. ``build_out_tasks`` writes a template phase's checklist, escaped, into the phase it
  CREATES, and never into one the project already has.
* **The patch** is idempotent, never overwrites a template that exists, rolls a failed template back
  whole, creates its custom fields from the fixture first, and never raises.
* **The page**: the dialog is built from a catalog read BEFORE it (a MultiSelectPills field asks its
  get_data during construction: the v1.602.1 trap), its rows go in through Copy from project's path,
  and Create sends the checklists of the phases it makes. Run under node.

Bench-free: reuses Phase 6B's fake site (its ``frappe`` stub), runs the real modules, puts
``sys.modules`` back. The node parts skip when node is not on PATH.

Run: python -m unittest erpnext_enhancements.tests.test_planner_templates
"""

import ast
import copy
import importlib
import itertools
import json
import re
import shutil
import subprocess
import types
import unittest
from pathlib import Path
from unittest import mock

from erpnext_enhancements.tests import test_planner_phase6b as base
from erpnext_enhancements.tests.test_planner_phase6b import (  # unittest looks both up in this module
	setUpModule,
	tearDownModule,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = Path(__file__).resolve().parents[1]
PP_JS = APP / "project_enhancements/page/project_planner/project_planner.js"
SEED = APP / "project_enhancements/data/planner_templates.json"
PURE = APP / "project_enhancements/planner_templates.py"
API = APP / "api/planner_templates.py"
SEED_PATCH = APP / "patches/seed_planner_templates.py"
GRANT_PATCH = APP / "patches/grant_project_template_editing.py"
FIXTURE = APP / "fixtures/custom_field.json"
DERIVE = REPO_ROOT / "scripts/derive_planner_templates.py"
CI = REPO_ROOT / ".github/workflows/ci.yml"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
PE_README = APP / "project_enhancements/README.md"
API_README = APP / "api/README.md"
PATCHES_README = APP / "patches/README.md"
PATCHES_TXT = APP / "patches.txt"
PROMPT_JS = APP / "public/js/crm_enhancements/create_project_prompt.js"

PHASE_TEMPLATE = "Fountain build phases (DOC-0041)"
BUNDLES = {
	"Bundle: Control panel design + build": [
		"Panel design (schematic + BOM)",
		"Order components",
		"Shop build",
		"Bench test",
		"Program controller",
		"Install + terminate on site",
		"Commission + tune",
	],
	"Bundle: Interior fountain": [
		"Shop drawings",
		"Order or fabricate unit",
		"Rough-in (supply, drain, power)",
		"Set + level",
		"Connect + fill",
		"Commission",
		"Owner training",
	],
	"Bundle: Splash pad": [
		"Layout + stake",
		"Rough-in plumbing + drains",
		"Set nozzles and toys",
		"Controls + activation",
		"Water test + commission",
		"Owner training",
	],
	"Bundle: Lighting package": [
		"Fixture schedule",
		"Order fixtures",
		"Install fixtures + conduit",
		"Terminate + test",
		"Program scenes",
	],
	"Bundle: Equipment room (pump + filter skid)": [
		"Layout drawing",
		"Order equipment",
		"Set equipment",
		"Pipe + valves",
		"Electrical tie-in",
		"Start-up + test",
	],
	"Bundle: Commissioning + hand-off": [
		"Pre-commission checklist",
		"Start-up",
		"Water balance + chemistry",
		"Punch list",
		"Owner training",
		"O&Ms + warranty",
	],
}
#: Every cost code DOC-0041 v1.0's Budget Sheet carries (column C), read off the workbook.
DOC0041_CODES = {
	"00 1000", "00 1100", "00 1200", "00 2000", "00 2200", "01 0100", "01 0120", "01 0150", "01 0200",
	"01 0210", "01 0410", "01 0500", "01 7900", "02 0300", "02 1200", "03 0210", "03 0400", "04 0450",
	"05 0500", "06 0100", "07 0100", "09 0300", "09 0700", "09 0900", "13 0600", "13 0700", "13 0812",
	"13 1213", "13 1223", "22 0150", "22 1100", "22 1119", "22 1120", "22 1150", "22 3100", "22 3200",
	"22 3300", "22 3400", "22 4516", "22 5100", "26 0300", "26 0410", "26 0420", "26 0425", "26 0500",
	"26 0700", "26 0750", "26 0800", "26 5500", "26 5529",
}  # fmt: skip


def tpl():
	return importlib.import_module("erpnext_enhancements.project_enhancements.planner_templates")


def api():
	return importlib.import_module("erpnext_enhancements.api.planner_templates")


def seed_patch():
	return importlib.import_module("erpnext_enhancements.patches.seed_planner_templates")


def grant_patch():
	return importlib.import_module("erpnext_enhancements.patches.grant_project_template_editing")


def catalog():
	return importlib.import_module("erpnext_enhancements.quality.cost_codes")


def load_seed():
	return json.loads(SEED.read_text(encoding="utf-8"))


def phase_template(seed=None):
	return next(t for t in (seed or load_seed())["templates"] if t["kind"] == "Phase template")


def by_subject(rows):
	return {row["subject"]: number for number, row in enumerate(rows, start=1)}


def needs_of(rows, subject, phase=None):
	for row in rows:
		if row["subject"] == subject and (phase is None or row["phase"] == phase):
			return row["needs"]
	raise KeyError(subject)


# ====================================================================== the seed


class TestSeed(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.raw = SEED.read_text(encoding="utf-8")
		cls.seed = json.loads(cls.raw)
		cls.phases = phase_template(cls.seed)["phases"]
		cc = catalog()
		cls.cost_codes = {row["cost_code"]: row for row in cc.COST_CODES}
		cls.cost_codes.update({row["resource_code"]: row for row in cc.RESOURCE_CODES})
		cls.reused = set(cc.REUSED_CODES)

	def test_it_is_seedable_and_reproducible(self):
		self.assertEqual(tpl().seed_problems(self.seed), [])
		self.assertEqual(self.seed["source"]["derived_by"], "scripts/derive_planner_templates.py")
		self.assertTrue(DERIVE.exists())
		self.assertTrue(self.seed["source"]["interpretations"])
		self.assertNotIn("\r", self.raw)
		# The script that derives it writes exactly this path, and never reads the workbook's formulas.
		script = DERIVE.read_text(encoding="utf-8")
		self.assertIn('"project_enhancements" / "data" / "planner_templates.json"', script)
		self.assertIn("data_only=True", script)

	def test_phases_100_to_700_only(self):
		self.assertEqual([p["number"] for p in self.phases], [100, 200, 300, 400, 500, 600, 700])
		self.assertNotIn("800", json.dumps([p["title"] for p in self.phases]))
		self.assertNotIn("Maintenance Labor", self.raw)
		self.assertNotIn("22 0150", self.raw)

	def test_the_v1_0_defects_are_not_copied(self):
		# Only the derivation note may name the misspelling it fixed.
		self.assertNotIn("Merchanical", json.dumps(self.seed["templates"]))
		self.assertTrue(any("Merchanical" in line for line in self.seed["source"]["interpretations"]))
		self.assertEqual(self.phases[4]["title"], "Phase 500")
		finishes = self.phases[6]["checklist"]
		self.assertEqual(sum("Exterior Fountains" in line for line in finishes), 1)
		self.assertIn("Material: Interior Fountains (13 1223)", finishes)
		for phase in self.phases:
			items = phase["checklist"] + [task["subject"] for task in phase["tasks"]]
			self.assertEqual(len(items), len(set(items)), phase["title"])

	def test_only_corrected_cost_codes(self):
		codes = []
		for phase in self.phases:
			codes += [task["cost_code"] for task in phase["tasks"]]
			for line in phase["checklist"]:
				found = re.search(r"\(([^()]+)\)$", line)
				self.assertTrue(found, line)
				codes.append(found.group(1))
		self.assertTrue(codes)
		for code in codes:
			self.assertIn(code, self.cost_codes, code)
			# A string that was ALSO a DOC-0041 code is only allowed where the catalog kept that number
			# (it was already right) or reuses it on purpose.
			if code in DOC0041_CODES:
				kept = self.cost_codes[code].get("old_code") == code
				self.assertTrue(kept or code in self.reused, code)
		# DOC-0041's own codes for its commonest lines are nowhere.
		for old in ("01 0100", "01 0150", "22 3100", "22 5100", "02 0300", "26 0750", "01 0120"):
			self.assertNotIn(f'"{old}"', self.raw, old)
			self.assertNotIn(f"({old})", self.raw, old)

	def test_one_project_management_task_per_phase(self):
		for phase in self.phases:
			pm = [task for task in phase["tasks"] if task["line"] == "Project management"]
			self.assertEqual(len(pm), 1, phase["title"])
			self.assertEqual(pm[0]["cost_code"], "LAB-PM")
			self.assertEqual(pm[0]["waits_on"], [])
			self.assertIn(phase["title"], pm[0]["subject"])
			# Nothing waits on the PM task.
			for task in phase["tasks"]:
				self.assertNotIn(pm[0]["subject"], task["waits_on"])

	def test_materials_are_a_checklist_never_tasks(self):
		lines = {"Labor", "Subcontract", "Project management"}
		subjects = set()
		for phase in self.phases:
			for task in phase["tasks"]:
				self.assertIn(task["line"], lines)
				subjects.add(task["subject"])
				kind = self.cost_codes[task["cost_code"]]["cost_type"]
				if task["line"] == "Labor":
					self.assertEqual(kind, "Labor", task["subject"])
				if task["line"] == "Subcontract":
					self.assertIsNone(task["hours"], task["subject"])
			for line in phase["checklist"]:
				self.assertRegex(line, r"^(Material|Equipment|Expense|Subcontract): ")
		for material in (
			"Pumps",
			"Manifold Materials",
			"Nozzles",
			"Check Valves",
			"Solenoid Valves",
			"Rough Carpentry",
		):
			self.assertNotIn(material, subjects, material)
			self.assertTrue(any(material in line for p in self.phases for line in p["checklist"]), material)
		# Nik's subcontracted scope is tasks.
		for scope in (
			"Site Demolition",
			"Earthwork",
			"Cast-In-Place Concrete",
			"Waterproofing",
			"Tile",
			"Stone",
			"Paints and Coatings",
			"Terminate all Electrical - High/Low voltage",
		):
			self.assertIn(scope, subjects, scope)

	def test_no_task_has_default_hours_since_the_units_are_a_placeholder(self):
		# DOC-0041 v1.0 has 1 in every "# Units" cell: the blank worksheet's placeholder, not an estimate.
		# A 1-hour default would book every templated task as a 1-hour job; blank shows what needs one.
		labor = [t for p in self.phases for t in p["tasks"] if t["line"] in ("Labor", "Project management")]
		self.assertTrue(labor)
		self.assertEqual({t["hours"] for t in labor}, {None})
		self.assertEqual({t["hours"] for p in self.phases for t in p["tasks"]}, {None})
		self.assertTrue(any("placeholder" in line for line in self.seed["source"]["interpretations"]))
		self.assertIn("PLACEHOLDER_UNITS", DERIVE.read_text(encoding="utf-8"))

	def test_the_grid_rows_carry_blank_hours(self):
		t = tpl()
		seed = self.seed
		bundles = [t.structure_from_seed(b) for b in seed["templates"] if b["kind"] == "Bundle"]
		plan = t.template_rows(t.structure_from_seed(phase_template(seed)), None, [(b, "") for b in bundles])
		self.assertEqual({row["hours"] for row in plan["rows"]}, {0.0})

	def test_the_phase_sequence(self):
		self.assertEqual(
			{p["number"]: p["runs_after"] for p in self.phases},
			{100: [], 200: [100], 300: [200], 400: [300], 500: [400], 600: [400], 700: [500, 600]},
		)
		for phase in self.phases:
			work = [t["subject"] for t in phase["tasks"] if t["line"] != "Project management"]
			for before, task in itertools.pairwise(work):
				waits = next(t for t in phase["tasks"] if t["subject"] == task)["waits_on"]
				self.assertEqual(waits, [before], task)
		finishes = [t["subject"] for t in self.phases[6]["tasks"]]
		self.assertEqual(
			finishes[-3:],
			[
				"Commissioning",
				"Training & Owner Hand-off (Training, Warranty, O&Ms)",
				"Project management (Phase 700)",
			],
		)

	def test_the_six_starter_bundles_are_drafts_for_nik(self):
		bundles = {t["name"]: t for t in self.seed["templates"] if t["kind"] == "Bundle"}
		self.assertEqual(list(bundles), list(BUNDLES))
		for name, subjects in BUNDLES.items():
			bundle = bundles[name]
			self.assertEqual(bundle["review_status"], "Needs review")
			self.assertIn("correct", bundle["notes"])
			self.assertEqual([t["subject"] for t in bundle["tasks"]], subjects)
			self.assertEqual({t["hours"] for t in bundle["tasks"]}, {None})
			self.assertEqual([t["waits_on"] for t in bundle["tasks"]], [[]] + [[s] for s in subjects[:-1]])
			self.assertNotIn("/", name)
		self.assertEqual(phase_template(self.seed)["review_status"], "Needs review")

	def test_seed_problems_catches_what_it_should(self):
		bad = copy.deepcopy(self.seed)
		phases = phase_template(bad)["phases"]
		phases[1]["runs_after"] = [150]
		phases[2]["tasks"][0]["waits_on"] = ["Not there"]
		phases[3]["tasks"].append(dict(phases[3]["tasks"][0]))
		phases.append(
			{
				"number": 800,
				"title": "800 Maintenance",
				"runs_after": [700],
				"checklist": [],
				"tasks": [{"subject": "x"}],
			}
		)
		problems = " | ".join(tpl().seed_problems(bad))
		for needle in (
			"runs after phase 150",
			"waits on 'Not there'",
			"is there twice",
			"phase 800 is never seeded",
		):
			self.assertIn(needle, problems)
		self.assertEqual(tpl().load_seed(APP / "no-such-file.json"), {})


# ====================================================================== the sequence


class TestSequence(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.structure = tpl().structure_from_seed(phase_template())

	def rows(self, picked=None, bundles=()):
		return tpl().template_rows(self.structure, picked, bundles)["rows"]

	def test_inside_a_phase_tasks_run_in_order_pm_aside(self):
		rows = self.rows()
		place = by_subject(rows)
		self.assertEqual(needs_of(rows, "Earthwork"), [f"row:{place['Site Demolition']}"])
		self.assertEqual(needs_of(rows, "Preparation Labor (Direct)"), [f"row:{place['Earthwork']}"])
		pm_rows = {place[r["subject"]] for r in rows if r["line"] == "Project management"}
		for row in rows:
			self.assertFalse({int(n[4:]) for n in row["needs"]} & pm_rows, row["subject"])

	def test_each_phase_waits_on_the_last_task_of_the_one_before(self):
		rows = self.rows()
		place = by_subject(rows)
		# 200's first task (and its PM task) wait on 100's last task, never on 100's PM task.
		self.assertEqual(needs_of(rows, "Site Demolition"), [f"row:{place['Design Labor & Assistance']}"])
		self.assertEqual(
			needs_of(rows, "Project management (Phase 200)"),
			[f"row:{place['Design Labor & Assistance']}"],
		)
		self.assertEqual(
			needs_of(rows, "Rough-in Labor (Direct)"), [f"row:{place['Preparation Labor (Direct)']}"]
		)
		self.assertEqual(needs_of(rows, "Design"), [])

	def test_500_and_600_run_side_by_side_and_700_waits_on_both(self):
		rows = self.rows()
		place = by_subject(rows)
		vessel = f"row:{place['Structural Vessel Labor (Direct)']}"
		self.assertEqual(needs_of(rows, "Equipment Room Labor (Direct)"), [vessel])
		self.assertEqual(needs_of(rows, "Control Panel Build"), [vessel])
		both = sorted([place["Equipment Room Labor (Direct)"], place["Controls Labor (Direct)"]])
		self.assertEqual(needs_of(rows, "Earthwork – Finish"), [f"row:{n}" for n in both])
		# Nothing in 500 waits on 600 or the other way round.
		mech = {place[t["subject"]] for t in self.structure["phases"][4]["tasks"]}
		controls = {place[t["subject"]] for t in self.structure["phases"][5]["tasks"]}
		for row in rows:
			waits = {int(n[4:]) for n in row["needs"]}
			if place[row["subject"]] in mech:
				self.assertFalse(waits & controls)
			if place[row["subject"]] in controls:
				self.assertFalse(waits & mech)

	def test_an_unpicked_phase_is_walked_through(self):
		rows = self.rows(["100", "300", "700"])
		place = by_subject(rows)
		self.assertEqual(
			{r["phase"] for r in rows},
			{"Phase 100", "Phase 300", "Phase 700"},
		)
		self.assertEqual(
			needs_of(rows, "Rough-in Labor (Direct)"), [f"row:{place['Design Labor & Assistance']}"]
		)
		self.assertEqual(needs_of(rows, "Earthwork – Finish"), [f"row:{place['Rough-in Labor (Direct)']}"])
		rows = self.rows(["500", "700"])
		place = by_subject(rows)
		self.assertEqual(
			needs_of(rows, "Earthwork – Finish"), [f"row:{place['Equipment Room Labor (Direct)']}"]
		)
		self.assertEqual(needs_of(rows, "Equipment Room Labor (Direct)"), [])

	def test_800_is_never_offered_whatever_is_asked(self):
		structure = copy.deepcopy(self.structure)
		structure["phases"].append(
			{
				"id": "800",
				"number": 800,
				"title": "800 Maintenance",
				"runs_after": ["700"],
				"checklist": ["Material: x"],
				"tasks": [dict(structure["phases"][0]["tasks"][0], key="m", subject="Maintenance Labor")],
			}
		)
		self.assertNotIn("800", [p["id"] for p in tpl().offered_phases(structure)])
		plan = tpl().template_rows(structure, ["700", "800"])
		self.assertNotIn("800 Maintenance", {r["phase"] for r in plan["rows"]})
		self.assertNotIn("800 Maintenance", plan["checklists"])
		self.assertNotIn("800", json.dumps(tpl().template_rows(structure)["phases"]))

	def test_every_row_is_in_order_the_server_accepts(self):
		rows = self.rows()
		self.assertEqual(len(rows), 30)
		waits = {i: {int(n[4:]) - 1 for n in row["needs"]} for i, row in enumerate(rows)}
		order, cycle = base.actions.build_order(len(rows), waits)
		self.assertEqual(cycle, [])
		self.assertEqual(order, list(range(len(rows))))
		for row in rows:
			self.assertEqual(set(row) >= {"subject", "hours", "phase", "crew_size", "needs"}, True)

	def test_the_checklists_are_the_picked_phases(self):
		plan = tpl().template_rows(self.structure, ["300", "500"])
		self.assertEqual(sorted(plan["checklists"]), ["Phase 300", "Phase 500"])
		self.assertIn("Equipment: Pumps (22 5216)", plan["checklists"]["Phase 500"])

	def test_the_data_wins_over_the_default_order(self):
		t = tpl()
		tasks = [
			{"key": "a", "line": "Labor", "waits_on": []},
			{"key": "b", "line": "Labor", "waits_on": []},
			{"key": "c", "line": "Labor", "waits_on": ["a"]},
		]
		self.assertEqual(t.group_waits(tasks), {"a": [], "b": [], "c": ["a"]})
		for task in tasks:
			task["waits_on"] = []
		self.assertEqual(t.group_waits(tasks), {"a": [], "b": ["a"], "c": ["b"]})
		tasks.insert(1, {"key": "pm", "line": "Project management", "waits_on": []})
		self.assertEqual(t.group_waits(tasks), {"a": [], "pm": [], "b": ["a"], "c": ["b"]})

	def test_the_seam_for_estimate_hours(self):
		rows = self.rows(["400"])
		tpl().apply_estimate_hours(rows, {})
		self.assertEqual({r["hours_from"] for r in rows}, {"template"})
		tpl().apply_estimate_hours(rows, {"03 3000": 24})
		concrete = next(r for r in rows if r["subject"] == "Cast-In-Place Concrete")
		self.assertEqual((concrete["hours"], concrete["hours_from"]), (24.0, "estimate"))

	def test_checklist_html_is_a_quill_task_list_and_escaped(self):
		out = tpl().checklist_html(["Pumps <b>x</b> & co", "  ", "Valves"], source="DOC-0041")
		self.assertEqual(
			out,
			'<p><strong>Procurement checklist (from DOC-0041)</strong></p><ol><li data-list="unchecked">'
			'Pumps &lt;b&gt;x&lt;/b&gt; &amp; co</li><li data-list="unchecked">Valves</li></ol>',
		)
		self.assertEqual(tpl().checklist_html([]), "")
		self.assertEqual(tpl().checklist_lines("- one\n[ ] two\n\n• three\n"), ["one", "two", "three"])
		self.assertEqual(tpl().parse_runs_after("500, 600 / 500"), [500, 600])


class TestBundles(unittest.TestCase):
	def test_bundles_come_after_the_phases_numbered_on_from_them(self):
		t = tpl()
		seed = load_seed()
		structure = t.structure_from_seed(phase_template(seed))
		panel = t.structure_from_seed(
			next(b for b in seed["templates"] if b["name"].endswith("Control panel design + build"))
		)
		lights = t.structure_from_seed(
			next(b for b in seed["templates"] if b["name"].endswith("Lighting package"))
		)
		plan = t.template_rows(structure, ["600"], [(panel, "Phase 600"), (lights, "Lighting")])
		rows = plan["rows"]
		self.assertEqual(len(rows), 6 + 7 + 5)
		self.assertEqual([r["phase"] for r in rows[6:13]], ["Phase 600"] * 7)
		self.assertEqual([r["phase"] for r in rows[13:]], ["Lighting"] * 5)
		self.assertEqual(rows[6]["needs"], [])
		self.assertEqual([r["needs"] for r in rows[7:13]], [[f"row:{n}"] for n in range(7, 13)])
		self.assertEqual(rows[13]["needs"], [])
		self.assertEqual(rows[14]["needs"], ["row:14"])
		# Bundles alone, under no phase.
		alone = t.template_rows(None, None, [(lights, "")])["rows"]
		self.assertEqual([r["needs"] for r in alone], [[], ["row:1"], ["row:2"], ["row:3"], ["row:4"]])
		self.assertEqual({r["phase"] for r in alone}, {""})


# ====================================================================== a fake site the patch writes


class _Rec(dict):
	"""A document the seed patch builds: insert() files it into the fake site's tables."""

	site = None

	def __getattr__(self, name):
		try:
			return self[name]
		except KeyError:
			raise AttributeError(name) from None

	def insert(self, ignore_permissions=False, set_name=None):
		site = _Rec.site
		site.inserted.append(self.get("doctype"))
		doctype = self["doctype"]
		if doctype in site.refuse:
			raise base._Throw(f"{doctype} refused")
		if doctype == "Task":
			if self.get("subject") in site.refuse:
				raise base._Throw(f"{self['subject']} refused")
			site.counter += 1
			self["name"] = f"TPL-{site.counter:03d}"
			row = {k: v for k, v in self.items() if k not in ("doctype", "depends_on")}
			site.tables.setdefault("Task", []).append(row)
			for dep in self.get("depends_on") or []:
				site.tables.setdefault("Task Depends On", []).append(
					{"parent": self["name"], "parenttype": "Task", "task": dep["task"]}
				)
			if self.get("parent_task"):
				# ERPNext's populate_depends_on: the phase lists each sub-task among its dependencies.
				site.tables["Task Depends On"] = [
					*site.tables.get("Task Depends On", []),
					{"parent": self["parent_task"], "parenttype": "Task", "task": self["name"]},
				]
		elif doctype == "Project Template":
			self["name"] = set_name
			site.tables.setdefault("Project Template", []).append(
				{
					"name": set_name,
					"custom_template_kind": self.get("custom_template_kind"),
					"custom_review_status": self.get("custom_review_status"),
					"custom_template_notes": self.get("custom_template_notes"),
					"disabled": 0,
				}
			)
			for idx, row in enumerate(self.get("tasks") or [], start=1):
				site.tables.setdefault("Project Template Task", []).append(
					{"parent": set_name, "parenttype": "Project Template", "task": row["task"], "idx": idx}
				)
		elif doctype in ("Custom Field", "Custom DocPerm"):
			site.tables.setdefault(doctype, []).append(dict(self))
		return self


class _SeededSite(unittest.TestCase):
	"""Phase 6B's fake frappe, with a site the seed patch can write into and the endpoints read."""

	def setUp(self):
		base._reset()
		self.frappe = base.frappe
		self.frappe.installed |= {"Project Template"}
		self.frappe.roles = ["Projects Manager"]
		self.frappe.refuse = set()
		self.frappe.get_app_path = lambda app, *parts: str(APP.joinpath(*parts))
		self.frappe.get_doc = self._get_doc
		self.frappe.events = []
		self.savepoints = {}
		self.frappe.db.savepoint = self._savepoint
		self.frappe.db.rollback = self._rollback
		_Rec.site = self.frappe
		self.frappe.tables.update(
			{
				"Project Template": [],
				"Project Template Task": [],
				"Task Depends On": [],
				"Custom Field": [],
				"Task": [],
			}
		)

	def _get_doc(self, doctype, name=None):
		if isinstance(doctype, dict):
			return _Rec(copy.deepcopy(doctype))
		return base._get_doc(doctype, name)

	def _savepoint(self, name):
		self.frappe.events.append(("savepoint", name))
		self.savepoints[name] = copy.deepcopy(self.frappe.tables)

	def _rollback(self, save_point=None):
		self.frappe.events.append(("rollback", save_point))
		self.frappe.tables.clear()
		self.frappe.tables.update(copy.deepcopy(self.savepoints[save_point]))

	def seed(self, data=None):
		return seed_patch().seed(data)

	def templates(self):
		return [row["name"] for row in self.frappe.tables["Project Template"]]


class TestSeedPatch(_SeededSite):
	def test_it_seeds_the_phase_template_and_the_bundles(self):
		report = self.seed()
		self.assertEqual(report["created"], [PHASE_TEMPLATE, *BUNDLES])
		self.assertEqual((report["skipped"], report["failed"]), ([], []))
		tasks = self.frappe.tables["Task"]
		self.assertTrue(all(t["is_template"] == 1 and t["status"] == "Template" for t in tasks))
		groups = [t for t in tasks if t.get("is_group")]
		self.assertEqual([g["custom_phase_number"] for g in groups], [100, 200, 300, 400, 500, 600, 700])
		self.assertEqual(groups[6]["custom_phase_runs_after"], "500, 600")
		self.assertIn("Equipment: Pumps (22 5216)", groups[4]["custom_procurement_checklist"].splitlines())
		design = next(t for t in tasks if t["subject"] == "Design Labor & Assistance")
		first = next(t for t in tasks if t["subject"] == "Design")
		self.assertEqual(design["parent_task"], groups[0]["name"])
		self.assertEqual(
			(design["expected_time"], design["custom_template_line"], design["custom_cost_code"]),
			(0.0, "Labor", "LAB-DESIGN-ASSIST"),
		)
		# No template Task is seeded with hours: they are blank until someone fills them in.
		self.assertEqual({t["expected_time"] for t in tasks if not t.get("is_group")}, {0.0})
		self.assertIn(
			{"parent": design["name"], "parenttype": "Task", "task": first["name"]},
			self.frappe.tables["Task Depends On"],
		)
		pt = {row["name"]: row for row in self.frappe.tables["Project Template"]}
		self.assertEqual(pt[PHASE_TEMPLATE]["custom_template_kind"], "Phase template")
		self.assertEqual({row["custom_review_status"] for row in pt.values()}, {"Needs review"})
		self.assertEqual(pt["Bundle: Splash pad"]["custom_template_kind"], "Bundle")
		listed = [r for r in self.frappe.tables["Project Template Task"] if r["parent"] == PHASE_TEMPLATE]
		self.assertEqual(len(listed), 7 + 30)

	def test_its_custom_fields_are_created_first_from_the_fixture(self):
		self.seed()
		made = [row["name"] for row in self.frappe.tables["Custom Field"]]
		self.assertEqual(made, list(tpl().TEMPLATE_FIELDS))
		first_task = self.frappe.inserted.index("Task")
		self.assertEqual(set(self.frappe.inserted[:first_task]), {"Custom Field"})
		self.assertTrue(all(row["is_system_generated"] == 0 for row in self.frappe.tables["Custom Field"]))
		# A field that already exists is not created again.
		self.setUp()
		self.frappe.tables["Custom Field"] = [{"name": n} for n in tpl().TEMPLATE_FIELDS]
		self.seed()
		self.assertEqual(len(self.frappe.tables["Custom Field"]), len(tpl().TEMPLATE_FIELDS))

	def test_it_is_idempotent(self):
		self.seed()
		before = copy.deepcopy(self.frappe.tables)
		inserted = len(self.frappe.inserted)
		report = self.seed()
		self.assertEqual(report["created"], [])
		self.assertEqual(report["skipped"], [PHASE_TEMPLATE, *BUNDLES])
		self.assertEqual(self.frappe.tables, before)
		self.assertEqual(len(self.frappe.inserted), inserted)

	def test_it_never_overwrites_a_template_that_exists(self):
		self.frappe.tables["Project Template"].append(
			{
				"name": "Bundle: Splash pad",
				"custom_template_kind": "Bundle",
				"custom_review_status": "Reviewed",
				"disabled": 0,
			}
		)
		report = self.seed()
		self.assertIn("Bundle: Splash pad", report["skipped"])
		splash = [r for r in self.frappe.tables["Project Template"] if r["name"] == "Bundle: Splash pad"]
		self.assertEqual(
			splash,
			[
				{
					"name": "Bundle: Splash pad",
					"custom_template_kind": "Bundle",
					"custom_review_status": "Reviewed",
					"disabled": 0,
				}
			],
		)
		self.assertNotIn("Layout + stake", [t["subject"] for t in self.frappe.tables["Task"]])

	def test_a_refused_template_is_rolled_back_whole_and_the_others_go_on(self):
		self.frappe.refuse = {"Set + level"}  # the fourth task of the Interior fountain bundle
		report = self.seed()
		self.assertEqual(report["failed"], ["Bundle: Interior fountain"])
		self.assertEqual(len(report["created"]), 6)
		subjects = [t["subject"] for t in self.frappe.tables["Task"]]
		for orphan in ("Shop drawings", "Order or fabricate unit", "Rough-in (supply, drain, power)"):
			self.assertNotIn(orphan, subjects)
		self.assertIn(("rollback", "seed_planner_template"), self.frappe.events)
		self.assertEqual(len(self.frappe.errors), 1)
		self.assertEqual(self.frappe.errors[0][1]["title"], "Seed planner templates")

	def test_a_seed_with_a_problem_seeds_nothing_of_that_template(self):
		data = load_seed()
		data["templates"][1]["tasks"][2]["waits_on"] = ["Nowhere"]
		report = self.seed(data)
		self.assertEqual(report["failed"], [data["templates"][1]["name"]])
		self.assertNotIn(data["templates"][1]["name"], self.templates())
		self.assertIn("waits on 'Nowhere'", self.frappe.errors[0][1]["message"])

	def test_it_never_raises(self):
		with mock.patch.object(tpl(), "load_seed", side_effect=RuntimeError("boom")):
			self.assertIsNone(seed_patch().execute())
		self.assertEqual(len(self.frappe.errors), 1)
		self.assertEqual(set(self.frappe.errors[0][1]), {"title", "message"})
		self.frappe.errors.clear()
		with mock.patch.object(tpl(), "load_seed", return_value={}):
			seed_patch().execute()
		self.assertIn("no templates", self.frappe.errors[0][1]["message"])
		self.frappe.installed.discard("Project Template")
		self.assertEqual(self.seed(), {"created": [], "skipped": [], "failed": []})

	def test_the_patches_are_registered_and_log_with_keywords(self):
		text = PATCHES_TXT.read_text(encoding="utf-8")
		post = text[text.index("[post_model_sync]") :]
		self.assertIn("erpnext_enhancements.patches.seed_planner_templates\n", post)
		self.assertIn("erpnext_enhancements.patches.grant_project_template_editing\n", post)
		for path in (SEED_PATCH, GRANT_PATCH):
			source = path.read_text(encoding="utf-8")
			self.assertNotIn("\r", source)
			for node in ast.walk(ast.parse(source)):
				if isinstance(node, ast.Call) and ast.unparse(node.func) == "frappe.log_error":
					self.assertEqual(node.args, [], path.name)
					self.assertEqual({k.arg for k in node.keywords}, {"title", "message"}, path.name)
			execute = next(
				n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "execute"
			)
			self.assertIsInstance(execute.body[0], ast.Try, path.name)


class TestGrantPatch(_SeededSite):
	def setUp(self):
		super().setUp()
		self.frappe.tables["Role"] = [{"name": "Projects Manager"}]
		self.frappe.tables["Custom DocPerm"] = []
		self.calls = []
		self.perms = mock.patch.dict(
			"sys.modules",
			{"frappe.permissions": types.SimpleNamespace(setup_custom_perms=self._setup)},
		)
		self.perms.start()
		self.frappe.db.set_value = self._set_value
		self.frappe.clear_cache = lambda **k: self.calls.append(("clear_cache", k))

	def tearDown(self):
		self.perms.stop()

	def _setup(self, doctype):
		self.calls.append(("setup_custom_perms", doctype))
		if not self.frappe.tables["Custom DocPerm"]:
			for role, rights in (
				("System Manager", dict.fromkeys(grant_patch().RIGHTS, 1)),
				("Projects Manager", {"select": 1}),
				("Projects User", {"select": 1}),
			):
				self.frappe.tables["Custom DocPerm"].append(
					dict(
						{
							"name": f"cdp-{role}",
							"parent": doctype,
							"role": role,
							"permlevel": 0,
							"if_owner": 0,
						},
						**rights,
					)
				)

	def _set_value(self, doctype, name, values, *a, **k):
		row = next(r for r in self.frappe.tables[doctype] if r["name"] == name)
		row.update(values)

	def test_the_standard_select_row_is_widened_and_nothing_else_changes(self):
		self.assertEqual(grant_patch().grant_project_template_editing(), "widened")
		self.assertEqual(self.calls[0], ("setup_custom_perms", "Project Template"))
		rows = {r["role"]: r for r in self.frappe.tables["Custom DocPerm"]}
		self.assertTrue(all(rows["Projects Manager"].get(right) == 1 for right in grant_patch().RIGHTS))
		self.assertEqual(
			rows["Projects User"],
			{
				"name": "cdp-Projects User",
				"parent": "Project Template",
				"role": "Projects User",
				"permlevel": 0,
				"if_owner": 0,
				"select": 1,
			},
		)
		self.assertEqual(grant_patch().grant_project_template_editing(), "already granted")

	def test_a_missing_row_is_inserted_and_a_missing_role_skips(self):
		self.frappe.tables["Custom DocPerm"] = [
			{
				"name": "x",
				"parent": "Project Template",
				"role": "System Manager",
				"permlevel": 0,
				"if_owner": 0,
				"read": 1,
			}
		]
		self.assertEqual(grant_patch().grant_project_template_editing(), "granted")
		new = next(r for r in self.frappe.tables["Custom DocPerm"] if r["role"] == "Projects Manager")
		self.assertEqual((new["write"], new["create"], new["permlevel"]), (1, 1, 0))
		self.frappe.tables["Role"] = []
		self.assertEqual(grant_patch().grant_project_template_editing(), "skipped")

	def test_it_never_raises(self):
		with mock.patch.object(
			grant_patch(), "grant_project_template_editing", side_effect=RuntimeError("x")
		):
			self.assertIsNone(grant_patch().execute())
		self.assertEqual(self.frappe.errors[-1][1]["title"], "grant_project_template_editing")


# ====================================================================== the endpoints


class TestEndpoints(_SeededSite):
	def setUp(self):
		super().setUp()
		self.seed()

	def test_the_catalog_offers_phases_100_to_700_and_the_bundles(self):
		out = api().get_planner_templates()
		self.assertTrue(out["ready"])
		(template,) = out["phase_templates"]
		self.assertEqual(template["name"], PHASE_TEMPLATE)
		self.assertEqual(
			[p["id"] for p in template["phases"]], ["100", "200", "300", "400", "500", "600", "700"]
		)
		self.assertTrue(template["needs_review"])
		self.assertEqual(sorted(b["name"] for b in out["bundles"]), sorted(BUNDLES))
		self.assertTrue(all(b["needs_review"] for b in out["bundles"]))
		self.assertEqual(
			{b["name"]: b["tasks"] for b in out["bundles"]}, {n: len(s) for n, s in BUNDLES.items()}
		)

	def test_the_rows_read_back_are_the_seeds_rows(self):
		out = api().get_template_rows(template=PHASE_TEMPLATE)
		expected = tpl().template_rows(tpl().structure_from_seed(phase_template()))

		def strip(rows):
			keys = ("subject", "hours", "phase", "crew_size", "needs", "cost_code", "line")
			return [tuple(r[key] for key in keys) for r in rows]

		self.assertEqual(strip(out["rows"]), strip(expected["rows"]))
		self.assertEqual(out["checklists"], expected["checklists"])
		self.assertEqual(out["count"], 30)
		self.assertTrue(all(r["template_task"].startswith("TPL-") for r in out["rows"]))
		self.assertIn("Still to be reviewed", out["note"])
		self.assertEqual(out["needs_review"], [PHASE_TEMPLATE])

	def test_picked_phases_and_bundles_in_one_answer(self):
		out = api().get_template_rows(
			template=PHASE_TEMPLATE,
			phases=json.dumps(["400", "600"]),
			bundles=json.dumps(
				[
					"Bundle: Lighting package",
					{"template": "Bundle: Control panel design + build", "phase": "Panels"},
				]
			),
			bundle_phase="Phase 600",
		)
		rows = out["rows"]
		self.assertEqual([r["phase"] for r in rows].count("Panels"), 7)
		self.assertEqual(rows[0]["subject"], "Cast-In-Place Concrete")
		self.assertEqual(
			needs_of(rows, "Control Panel Build"),
			[f"row:{by_subject(rows)['Structural Vessel Labor (Direct)']}"],
		)
		lighting = next(r for r in rows if r["subject"] == "Fixture schedule")
		self.assertEqual((lighting["phase"], lighting["needs"]), ("Phase 600", []))
		self.assertEqual(sorted(out["checklists"]), ["Phase 400", "Phase 600"])

	def test_refusals(self):
		a = api()
		cases = [
			({}, "Pick a phase template or at least one bundle"),
			({"template": "Nope"}, "is not a phase template"),
			({"template": "Bundle: Splash pad"}, "is not a phase template"),
			({"bundles": json.dumps([PHASE_TEMPLATE])}, "is not a bundle"),
			({"template": PHASE_TEMPLATE, "phases": json.dumps(["800"])}, "is not one this template offers"),
			({"template": PHASE_TEMPLATE, "phases": "[]"}, "Tick at least one phase"),
			({"bundles": "{not json"}, "could not be read"),
			(
				{"bundles": json.dumps([{"template": "Bundle: Splash pad", "phase": "x" * 141}])},
				"140 characters",
			),
		]
		for kwargs, message in cases:
			with self.assertRaises(base._Throw, msg=kwargs) as caught:
				a.get_template_rows(**kwargs)
			self.assertIn(message, str(caught.exception), kwargs)
		with mock.patch.object(tpl(), "MAX_ROWS", 10), self.assertRaises(base._Throw) as caught:
			a.get_template_rows(template=PHASE_TEMPLATE)
		self.assertIn("the grid holds 10", str(caught.exception))

	def test_planner_roles_only_and_nothing_before_the_migrate(self):
		self.frappe.roles = ["Sales User"]
		for call in (api().get_planner_templates, lambda: api().get_template_rows(template=PHASE_TEMPLATE)):
			with self.assertRaises(base._PermissionError):
				call()
		self.frappe.roles = ["Projects User"]
		self.frappe.db.has_column = lambda doctype, column: False
		self.assertEqual(
			api().get_planner_templates(), {"phase_templates": [], "bundles": [], "ready": False}
		)
		with self.assertRaises(base._Throw):
			api().get_template_rows(template=PHASE_TEMPLATE)

	def test_a_disabled_or_unkinded_template_is_not_offered(self):
		for row in self.frappe.tables["Project Template"]:
			if row["name"] == "Bundle: Splash pad":
				row["disabled"] = 1
			if row["name"] == "Bundle: Lighting package":
				row["custom_template_kind"] = None
		names = [b["name"] for b in api().get_planner_templates()["bundles"]]
		self.assertNotIn("Bundle: Splash pad", names)
		self.assertNotIn("Bundle: Lighting package", names)

	def test_the_reads_are_whitelisted_and_write_nothing(self):
		tree = ast.parse(API.read_text(encoding="utf-8"))
		functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
		for name in ("get_planner_templates", "get_template_rows"):
			self.assertEqual([ast.unparse(d) for d in functions[name].decorator_list], ["frappe.whitelist()"])
		source = API.read_text(encoding="utf-8")
		for forbidden in (
			".insert(",
			".save(",
			"set_value",
			"delete_doc",
			"ignore_permissions",
			"frappe.db.sql",
		):
			self.assertNotIn(forbidden, source, forbidden)
		self.assertNotIn("\n    ", source.split('"""', 2)[2])  # tab-indented
		inserted = len(self.frappe.inserted)
		api().get_template_rows(template=PHASE_TEMPLATE)
		self.assertEqual(len(self.frappe.inserted), inserted)

	def test_structure_from_tasks_reads_what_nik_edits_in_the_desk(self):
		t = tpl()
		tasks = [
			{"name": "G1", "subject": "Prep", "is_group": 1, "custom_phase_number": None},
			{"name": "A", "subject": "Dig", "parent_task": "G1", "expected_time": 4},
			{"name": "G2", "subject": "250 Extra", "is_group": 1},
			{"name": "B", "subject": "Pour", "parent_task": "G2", "custom_crew_size": 3},
			{"name": "C", "subject": "Stray"},
		]
		depends = [{"parent": "G1", "task": "A"}, {"parent": "B", "task": "A"}]
		structure = t.structure_from_tasks("Phase template", tasks, depends)
		self.assertEqual([p["id"] for p in structure["phases"]], ["G1", "250"])
		# No phase says what it runs after, so each runs after the one above it.
		self.assertEqual(structure["phases"][1]["runs_after"], ["G1"])
		self.assertEqual(structure["loose"], 1)
		# A cross-phase dependency and the phase's own list of children are not in-phase order.
		self.assertEqual(structure["phases"][1]["tasks"][0]["waits_on"], [])
		self.assertEqual(structure["phases"][1]["tasks"][0]["crew_size"], 3)
		rows = t.template_rows(structure)["rows"]
		self.assertEqual([r["needs"] for r in rows], [[], ["row:1"]])


class TestBuildOutChecklists(_SeededSite):
	def setUp(self):
		super().setUp()
		self.frappe.roles = ["Projects User"]
		self.frappe.get_doc = base._get_doc
		self.frappe.tables["Task"] = [
			{
				"name": "TASK-P1",
				"project": "PRJ-1",
				"subject": "300 Rough-in",
				"status": "Open",
				"is_group": 1,
				"is_template": 0,
				"exp_end_date": None,
			}
		]
		self.actions = base.actions

	def build(self, rows, checklists, **kwargs):
		return self.actions.build_out_tasks(
			"PRJ-1",
			json.dumps(rows),
			phase_checklists=json.dumps(checklists) if checklists is not None else None,
			**kwargs,
		)

	def test_a_new_phase_gets_its_checklist_and_an_existing_one_never(self):
		self.build(
			[
				{"subject": "Set pumps", "phase": "500 mechanical room"},
				{"subject": "Rough plumbing", "phase": "300 Rough-in"},
			],
			{
				"500 Mechanical Room": ["Equipment: Pumps <x> (22 5216)"],
				"300 Rough-in": ["Material: Pipe"],
				"900 Unused": ["y"],
			},
		)
		phases = [row for row in self.frappe.inserted if row.get("is_group")]
		self.assertEqual(len(phases), 1)
		# A numbered phase the project lacks is made under its canonical name.
		self.assertEqual(phases[0]["subject"], "Phase 500")
		self.assertIn(
			'<li data-list="unchecked">Equipment: Pumps &lt;x&gt; (22 5216)</li>', phases[0]["description"]
		)
		self.assertFalse(any("Pipe" in str(row.get("description")) for row in self.frappe.inserted))
		self.assertEqual(self.frappe.saved, [])

	def test_without_checklists_it_is_what_it_was(self):
		self.build([{"subject": "Set pumps", "phase": "New phase"}], None)
		phase = next(row for row in self.frappe.inserted if row.get("is_group"))
		self.assertNotIn("description", phase)

	def test_a_drafted_phase_keeps_its_checklist_in_the_spec(self):
		with mock.patch.object(base.api, "require_draft_room", lambda count: None):
			out = self.build(
				[{"subject": "Set pumps", "phase": "500 Mechanical Room"}],
				{"500 Mechanical Room": ["Equipment: Pumps"]},
				draft=1,
			)
		self.assertTrue(out["drafted"])
		drafts = [row for row in self.frappe.inserted if row.get("doctype") == "Planner Draft Change"]
		phase = json.loads(drafts[0]["payload"])
		self.assertIn("Equipment: Pumps", phase["values"]["description"])
		self.assertEqual([row for row in self.frappe.inserted if row.get("doctype") == "Task"], [])

	def test_a_bad_checklist_payload_is_refused_before_anything_is_written(self):
		for bad in (
			'["not", "an object"]',
			'{"P": "not a list"}',
			'{"P": [1, 2]}',
			"{nope",
			json.dumps({f"P{i}": ["x"] for i in range(26)}),
		):
			with self.assertRaises(base._Throw, msg=bad) as caught:
				self.actions.build_out_tasks(
					"PRJ-1", json.dumps([{"subject": "A", "phase": "P"}]), phase_checklists=bad
				)
			self.assertIn("checklists could not be read", str(caught.exception))
		self.assertEqual(self.frappe.inserted, [])

	def test_the_seeded_rows_build_out_on_a_project(self):
		self.frappe.get_doc = self._get_doc
		self.seed()
		plan = api().get_template_rows(template=PHASE_TEMPLATE)
		self.frappe.get_doc = base._get_doc
		self.frappe.inserted.clear()
		rows = [
			{"subject": r["subject"], "hours": r["hours"], "phase": r["phase"], "depends_on": r["needs"]}
			for r in plan["rows"]
		]
		out = self.actions.build_out_tasks(
			"PRJ-1", json.dumps(rows), phase_checklists=json.dumps(plan["checklists"])
		)
		self.assertEqual(out["count"], 30)
		# PRJ-1 already has its phase 300, spelled "300 Rough-in": matched by number, never made again.
		self.assertEqual(
			[p["subject"] for p in out["phases"]],
			["Phase 100", "Phase 200", "Phase 400", "Phase 500", "Phase 600", "Phase 700"],
		)
		made = {row["subject"]: row for row in self.frappe.inserted if row.get("doctype") == "Task"}
		self.assertIn('data-list="unchecked"', made["Phase 500"]["description"])
		self.assertNotIn("Phase 300", made)
		self.assertEqual(made["Rough-in Labor (Direct)"]["parent_task"], "TASK-P1")
		groups = [row for row in self.frappe.inserted if row.get("is_group")]
		self.assertEqual(len(groups), len({row["subject"] for row in groups}))
		earthwork = made["Earthwork – Finish"]
		self.assertEqual(
			sorted(d["subject"] for d in earthwork["depends_on"]),
			["Controls Labor (Direct)", "Equipment Room Labor (Direct)"],
		)


class TestPhasesByNumber(_SeededSite):
	"""Decision 2026-10-12: a project's phases are "Phase 100" ... "Phase 700". A template phase, or any
	phase typed in the grid, that starts with a phase number IS the project's open phase with that number,
	whatever its spelling; with none, one new "Phase <number>"."""

	def setUp(self):
		super().setUp()
		self.frappe.roles = ["Projects User"]
		self.frappe.get_doc = base._get_doc
		self.actions = base.actions

	def project(self, *phases):
		self.frappe.tables["Task"] = [
			{
				"name": name,
				"project": "PRJ-1",
				"subject": subject,
				"status": status,
				"is_group": 1,
				"is_template": 0,
				"exp_end_date": None,
			}
			for name, subject, status in phases
		]

	def build(self, rows):
		return self.actions.build_out_tasks("PRJ-1", json.dumps(rows))

	def groups(self):
		return [row["subject"] for row in self.frappe.inserted if row.get("is_group")]

	def parent_of(self, subject):
		return next(r["parent_task"] for r in self.frappe.inserted if r.get("subject") == subject)

	def test_the_one_helper(self):
		t = tpl()
		cases = {
			"Phase 100": 100,
			"phase 300 - design": 300,
			"  PHASE300 x": 300,
			"300 Rough-in": 300,
			"700": 700,
			"Phase 1000": None,
			"Pumps": None,
			"30 Rough-in": None,
			"": None,
			None: None,
		}
		self.assertEqual({text: t.phase_number(text) for text in cases}, cases)
		self.assertIs(self.actions.phase_number, t.phase_number)
		self.assertEqual(t.phase_name(300), "Phase 300")
		self.assertEqual(
			(t.phase_key("300 Rough-in"), t.phase_key("Phase 300"), t.phase_key(" Pumps ")),
			("#300", "#300", "pumps"),
		)
		self.assertEqual(
			t.phase_label("Phase 100", "Full System Overview"), "Phase 100 — Full System Overview"
		)
		self.assertEqual(t.phase_label("Phase 100", " phase 100 "), "Phase 100")
		self.assertEqual(t.plain_text("<p>Full <b>System</b>\n Overview</p>"), "Full System Overview")

	def test_an_existing_phase_300_is_reused(self):
		self.project(("TASK-300", "Phase 300", "Open"))
		self.build(
			[
				{"subject": "A", "phase": "Phase 300"},
				{"subject": "B", "phase": "300 Rough-in"},
				{"subject": "C", "phase": "phase 300 - pipe"},
			]
		)
		self.assertEqual(self.groups(), [])
		self.assertEqual({self.parent_of(s) for s in "ABC"}, {"TASK-300"})

	def test_one_spelled_300_rough_in_is_reused_too(self):
		self.project(("TASK-RI", "300 Rough-in", "Open"))
		self.build([{"subject": "A", "phase": "Phase 300"}])
		self.assertEqual((self.groups(), self.parent_of("A")), ([], "TASK-RI"))

	def test_with_several_the_one_named_phase_300_wins_and_a_finished_one_never(self):
		self.project(
			("TASK-A", "300 Old", "Open"),
			("TASK-Z", "Phase 300", "Open"),
			("TASK-DONE", "Phase 500", "Completed"),
		)
		self.build([{"subject": "A", "phase": "300"}, {"subject": "B", "phase": "Phase 500"}])
		self.assertEqual(self.parent_of("A"), "TASK-Z")
		self.assertEqual(self.groups(), ["Phase 500"])

	def test_no_phase_with_that_number_makes_one_phase_n_never_two(self):
		self.project(("TASK-P", "Pumps", "Open"))
		out = self.build(
			[
				{"subject": "A", "phase": "300 Rough-in"},
				{"subject": "B", "phase": "Phase 300"},
				{"subject": "C", "phase": "pumps"},
			]
		)
		self.assertEqual(self.groups(), ["Phase 300"])
		self.assertEqual([p["subject"] for p in out["phases"]], ["Phase 300"])
		self.assertEqual(self.parent_of("A"), self.parent_of("B"))
		self.assertEqual(self.parent_of("C"), "TASK-P")

	def test_the_seed_phases_are_phase_100_to_700_with_their_names(self):
		phases = phase_template()["phases"]
		self.assertEqual([p["title"] for p in phases], [f"Phase {n}" for n in range(100, 800, 100)])
		self.assertEqual(phases[0]["name"], "Full System Overview")
		self.assertEqual(phases[4]["name"], "Mechanical Room")
		notes = " ".join(load_seed()["source"]["interpretations"])
		for needle in ("'Phase 100' ... 'Phase 700'", "2026-10-12", "Phase 100:", "Phase 400:"):
			self.assertIn(needle, notes)
		bad = load_seed()
		phase_template(bad)["phases"][2]["title"] = "300 Rough-in"
		self.assertIn("a phase is named 'Phase 300'", " ".join(tpl().seed_problems(bad)))

	def test_the_patch_names_them_so_and_the_dialog_labels_them(self):
		self.frappe.get_doc = self._get_doc
		self.frappe.tables["Task"] = []
		self.seed()
		groups = [t for t in self.frappe.tables["Task"] if t.get("is_group")]
		self.assertEqual([g["subject"] for g in groups], [f"Phase {n}" for n in range(100, 800, 100)])
		self.assertEqual(groups[0]["description"], "Full System Overview")
		(template,) = api().get_planner_templates()["phase_templates"]
		self.assertEqual(template["phases"][0]["label"], "Phase 100 — Full System Overview")
		self.assertEqual(template["phases"][4]["title"], "Phase 500")
		# A phase Nik spells "300 Rough-in" in the Desk is still "Phase 300" on a project.
		groups[2]["subject"], groups[2]["description"] = "300 Rough-in", None
		groups[2]["custom_phase_number"] = None
		phase = api().get_planner_templates()["phase_templates"][0]["phases"][2]
		self.assertEqual((phase["title"], phase["label"]), ("Phase 300", "Phase 300 — Rough-in"))
		rows = api().get_template_rows(template=PHASE_TEMPLATE, phases=json.dumps(["300"]))["rows"]
		self.assertEqual({r["phase"] for r in rows}, {"Phase 300"})


# ====================================================================== the page under node


def _source(code, start, end):
	return code[code.index(start) : code.index(end)]


def _t3_block(code):
	"""The T3 block, from its banner to its one Object.assign (T4's block follows it since v1.609.0)."""
	start = code.index("// ====================================================================== T3:")
	end = code.index("Object.assign(ProjectPlanner.prototype, PPT3_METHODS);", start)
	return code[start : end + len("Object.assign(ProjectPlanner.prototype, PPT3_METHODS);")]


PAGE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const [t1_source, t3_source] = JSON.parse(fs.readFileSync(0, "utf8"));
const tr = (text, args) => (args ? String(text).replace(/\{(\d+)\}/g, (m, i) => String(args[i])) : String(text));
const alerts = [];
const calls = [];
const asked = [];
const dialogs = [];
// Frappe builds every field's control while the dialog is constructed, and an autocomplete or
// MultiSelectPills asks its get_data / get_query right then, before `new Dialog` has returned.
class Dialog {
	constructor(opts) {
		this.opts = opts;
		this.fields = opts.fields;
		this.shown = false;
		this.hidden = false;
		this.ask("building");
		dialogs.push(this);
	}
	ask(when) {
		this.fields.forEach((df) => {
			if (df.get_data) asked.push([when, df.fieldname, df.get_data("")]);
			if (df.get_query) asked.push([when, df.fieldname, df.get_query()]);
		});
	}
	show() { this.shown = true; this.ask("shown"); }
	hide() { this.hidden = true; }
}
const catalog = {
	ready: true,
	phase_templates: [{ name: "Phases", needs_review: true, phases: [
		{ id: "100", title: "100 Overview", label: "Phase 100 — Full System Overview" }, { id: "300", title: "300 Rough-in" }, { id: "500", title: "500 Mechanical Room" },
	] }],
	bundles: [{ name: "Bundle: Lighting", tasks: 2, needs_review: true }, { name: "Bundle: Panel", tasks: 3, needs_review: false }],
};
let replies = {};
const ctx = {
	console,
	Promise,
	setTimeout: (fn) => fn(),
	clearTimeout: () => null,
	__: tr,
	pp_esc: (v) => String(v).replace(/</g, "&lt;"),
	PP: {},
	PP6A: { owner: "pp" },
	frappe: {
		ui: { Dialog },
		msgprint: (m) => alerts.push(typeof m === "string" ? m : m.message),
		show_alert: (o) => alerts.push(o.message),
		call: (o) => { calls.push([o.method.split(".").pop(), o.args || null]); return Promise.resolve(replies[o.method.split(".").pop()] || {}); },
	},
	document: { getElementById: () => true },
};
vm.createContext(ctx);
vm.runInContext(t1_source + "\n" + t3_source + "\nthis.T1 = PPT1_METHODS; this.T3 = PPT3_METHODS; this.P1 = PPT1_PURE; this.P3 = PPT3_PURE;", ctx);
const P1 = ctx.P1, P3 = ctx.P3;
const out = {};
const tick = async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); };
const make_page = () => {
	const sent = [];
	const page = Object.assign(Object.create(Object.assign({}, ctx.T1, ctx.T3)), {
		draft_on: false,
		data: { can_edit: true },
		t1: { ids: 0, drafts: {}, needs: null },
		modified: {},
		draft_args() { return this.draft_on ? { draft: 1 } : {}; },
		t1_rebuild_rows: () => null,
		t1_check: () => null,
		t1_load_needs: () => null,
		load: () => null,
		push_undo: () => false,
		t1_send: (method, args) => { sent.push([method, args]); return Promise.resolve({ tasks: [{ name: "T-1", subject: "x", modified: "m" }], phases: [] }); },
	});
	page.sent = sent;
	return page;
};
const grid = (page, n) => ({
	project: "PRJ-1",
	label: "Plaza",
	phases: [{ name: "TASK-P3", subject: "300 Rough-in" }],
	tasks: [],
	rows: Array.from({ length: n }, () => P1.blank_row(page.t1_new_id())),
	table: {},
	handle: { is_open: () => true, close: () => null },
});
const filled = (state) => state.rows.map((row) => [row.subject, row.hours, row.phase, row.needs, row.needs_more || null]);

(async () => {
	// ---- pure
	out.fields = P3.fields(catalog, ["300 Rough-in", "Lighting"]).map((f) => [f.fieldtype, f.fieldname, f.depends_on || null, Array.isArray(f.options) ? f.options : (typeof f.options === "string" && f.fieldtype === "Select" ? f.options.split("\n") : null), f.default || null]);
	out.fields_empty = P3.fields({}, []).map((f) => f.fieldtype);
	out.requests = [
		P3.request({ t3_template: "Phases", t3_phases_0: ["100", "500"], t3_bundles: ["Bundle: Lighting", " ", "Bundle: Lighting"], t3_bundle_phase: "  Lighting " }, catalog, "PRJ-1"),
		P3.request({ t3_template: "Phases", t3_phases_0: [] }, catalog, "PRJ-1"),
		P3.request({ t3_template: "", t3_bundles: "Bundle: Panel, Bundle: Lighting" }, catalog, ""),
		P3.request({ t3_template: "Phases", t3_phases_0: [], t3_bundles: ["Bundle: Panel"] }, catalog, null),
		P3.request({}, catalog, "PRJ-1"),
	];
	out.options = [P3.bundle_options(catalog, ""), P3.bundle_options(catalog, "pan")];
	out.choices = P3.phase_choices([{ name: "TASK-P3", subject: "300 Rough-in" }], [{ phase: "Lighting" }, { phase: "lighting" }, { phase: "" }], catalog);
	out.review = [P3.review_note(catalog), P3.review_note({ bundles: [{ name: "x", needs_review: false }] })];
	out.checklists_for = [
		P1.checklists_for({ "500 Mechanical Room": ["Pumps"], "300 Rough-in": ["Pipe"], "700 X": [] }, ["500 mechanical room", "700 X", "New"]),
		P1.checklists_for(undefined, ["A"]),
		P1.checklists_for({ A: ["x"] }, []),
		P1.checklists_for({ "Phase 500": ["x"] }, ["500 Mech"]),
	];
	// Phases by number, as the server matches them (decision 2026-10-12).
	const project_phases = [{ name: "T-A", subject: "300 Old" }, { name: "T-Z", subject: "Phase 300" }, { name: "T-P", subject: "Pumps" }];
	out.match = ["300 Rough-in", "phase 300 - x", "Phase 500", "pumps", "Phase 1000"].map((text) => P1.phase_match(text, project_phases));
	out.numbers = ["Phase 100", "300 Rough-in", "  PHASE300 x", "30 x", "Phase 1000", null].map((text) => P1.phase_number(text));
	const sevens = ["Phase 700", "700 Finishes", "phase 700", "Lights"].map((phase, i) => Object.assign(P1.blank_row(`s${i}`), { subject: `T${i}`, phase }));
	out.fresh = P1.rows_payload(sevens, project_phases).new_phases;

	// ---- the dialog opens from a catalog read first, and its fields ask for their options while it is built
	let page = make_page();
	let state = grid(page, 3);
	replies = { get_planner_templates: { message: catalog } };
	let opened = null;
	try {
		opened = await page.t3_open_templates(state);
		out.open_error = null;
	} catch (e) {
		out.open_error = String(e && e.message ? e.message : e);
	}
	out.open = [calls.map((c) => c[0]), dialogs.length, opened ? opened.shown : null, opened ? opened.opts.title : null];
	out.asked = asked.map(([when, field, answer]) => [when, field, (answer || []).map((o) => o.value)]);
	// Nothing picked: said, and the dialog stays.
	opened.opts.primary_action({ t3_template: "Phases", t3_phases_0: [] });
	out.nothing = [opened.hidden, alerts[alerts.length - 1], calls.length];
	// Picked: the rows land in the empty grid (Copy from project's path), with the checklists.
	replies.get_template_rows = { message: {
		rows: [
			{ subject: "Design", hours: 1, phase: "100 Overview", crew_size: 0, needs: [] },
			{ subject: "Rough", hours: 0, phase: "300 Rough-in", crew_size: 2, needs: ["row:1"] },
			{ subject: "Pumps in", hours: 1, phase: "500 Mechanical Room", crew_size: 0, needs: ["row:2"] },
		],
		checklists: { "300 Rough-in": ["Material: Pipe"], "500 Mechanical Room": ["Equipment: Pumps"] },
		count: 3,
		note: "3 tasks from the template.",
	} };
	opened.opts.primary_action({ t3_template: "Phases", t3_phases_0: ["100", "300", "500"] });
	await tick();
	out.loaded = [opened.hidden, calls[calls.length - 1], filled(state), Object.keys(state.checklists).sort(), state.note];
	// Create sends the checklist of the phase it MAKES, never of the project's existing 300 Rough-in.
	await page.t1_create(state);
	const [method, args] = page.sent[0];
	out.create = [method, Object.keys(args).sort(), JSON.parse(args.phase_checklists), JSON.parse(args.rows).length];

	// ---- a bundle onto a grid that has rows: Append keeps them and numbers the bundle on from them
	page = make_page();
	state = grid(page, 2);
	state.rows[0].subject = "Typed by hand";
	state.checklists = { "Old": ["kept"] };
	replies.get_template_rows = { message: { rows: [
		{ subject: "Fixture schedule", hours: 0, phase: "Lighting", crew_size: 0, needs: [] },
		{ subject: "Order fixtures", hours: 0, phase: "Lighting", crew_size: 0, needs: ["row:1"] },
	], checklists: {}, count: 2, note: "2 tasks." } };
	const before = dialogs.length;
	page.t3_load(state, { bundles: '["Bundle: Lighting"]', bundle_phase: "Lighting" });
	await tick();
	const how = dialogs[dialogs.length - 1];
	out.how = [dialogs.length - before, how.opts.title];
	how.opts.primary_action({ t1_how: "Append" });
	out.appended = [filled(state).map((r) => r.slice(0, 3)), state.rows[2].needs === `row:${state.rows[1].id}` ? "first bundle row" : state.rows[2].needs, Object.keys(state.checklists)];

	// ---- no templates yet: said, and no dialog
	page = make_page();
	state = grid(page, 1);
	replies.get_planner_templates = { message: { phase_templates: [], bundles: [], ready: true } };
	const quiet = dialogs.length;
	await page.t3_open_templates(state);
	out.empty = [dialogs.length - quiet, alerts[alerts.length - 1]];
	process.stdout.write(JSON.stringify(out));
})().catch((e) => {
	process.stderr.write(String(e && e.stack ? e.stack : e));
	process.exit(1);
});
"""


class TestPageUnderNode(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		node = shutil.which("node")
		if not node:
			raise unittest.SkipTest("node is not on PATH")
		code = PP_JS.read_text(encoding="utf-8")
		t1 = _source(code, "const PPT1 = {", "Object.assign(ProjectPlanner.prototype, PPT1_METHODS);")
		t3 = _source(code, "const PPT3 = {", "Object.assign(ProjectPlanner.prototype, PPT3_METHODS);")
		result = subprocess.run(
			[node, "-e", PAGE_HARNESS],
			input=json.dumps([t1, t3]),
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=60,
		)
		if result.returncode != 0:
			raise AssertionError(result.stderr)
		cls.out = json.loads(result.stdout)

	def test_the_dialogs_fields(self):
		fields = self.out["fields"]
		self.assertEqual(
			[(f[0], f[1]) for f in fields],
			[
				("HTML", "t3_help"),
				("Select", "t3_template"),
				("MultiCheck", "t3_phases_0"),
				("MultiSelectPills", "t3_bundles"),
				("Autocomplete", "t3_bundle_phase"),
			],
		)
		self.assertEqual(fields[1][3], ["", "Phases"])
		self.assertEqual(fields[1][4], "Phases")
		self.assertEqual(fields[2][2], 'eval:doc.t3_template=="Phases"')
		self.assertEqual(
			fields[2][3],
			[
				{"label": "Phase 100 — Full System Overview", "value": "100", "checked": 1},
				{"label": "300 Rough-in", "value": "300", "checked": 1},
				{"label": "500 Mechanical Room", "value": "500", "checked": 1},
			],
		)
		self.assertEqual(fields[4][3], ["300 Rough-in", "Lighting"])
		self.assertEqual(self.out["fields_empty"], ["HTML"])

	def test_the_request_is_what_the_endpoint_takes(self):
		full, nothing, bundles_only, unticked_with_bundle, empty = self.out["requests"]
		self.assertEqual(
			full,
			{
				"template": "Phases",
				"phases": '["100","500"]',
				"bundles": '["Bundle: Lighting"]',
				"bundle_phase": "Lighting",
				"project": "PRJ-1",
			},
		)
		self.assertIsNone(nothing)
		self.assertEqual(bundles_only, {"bundles": '["Bundle: Panel","Bundle: Lighting"]'})
		self.assertEqual(unticked_with_bundle, {"bundles": '["Bundle: Panel"]'})
		self.assertIsNone(empty)
		params = {
			a.arg
			for a in next(
				n
				for n in ast.parse(API.read_text(encoding="utf-8")).body
				if isinstance(n, ast.FunctionDef) and n.name == "get_template_rows"
			).args.args
		}
		self.assertTrue({"template", "phases", "bundles", "bundle_phase", "project"} <= params)

	def test_options_choices_and_review(self):
		everything, panel = self.out["options"]
		self.assertEqual(
			everything,
			[
				{"value": "Bundle: Lighting", "description": "2 tasks · needs review"},
				{"value": "Bundle: Panel", "description": "3 tasks"},
			],
		)
		self.assertEqual(panel, [{"value": "Bundle: Panel", "description": "3 tasks"}])
		self.assertEqual(
			self.out["choices"], ["300 Rough-in", "Lighting", "100 Overview", "500 Mechanical Room"]
		)
		self.assertEqual(
			self.out["review"],
			["Not reviewed yet: Phases, Bundle: Lighting. Edit them in Project Template.", ""],
		)

	def test_phases_match_by_number_like_the_server(self):
		self.assertEqual(
			self.out["match"],
			[
				{"parent_task": "T-Z"},
				{"parent_task": "T-Z"},
				{"phase": "Phase 500"},
				{"parent_task": "T-P"},
				{"phase": "Phase 1000"},
			],
		)
		self.assertEqual(self.out["numbers"], [100, 300, 300, None, None, None])
		# Three spellings of 700 are ONE new phase (the button counts it once), Lights another.
		self.assertEqual(self.out["fresh"], ["Phase 700", "Lights"])
		self.assertEqual(self.out["checklists_for"][3], {"500 Mech": ["x"]})
		source = PP_JS.read_text(encoding="utf-8")
		self.assertIn(r"/^\s*(?:phase\s*)?(\d{3})\b/i", source)
		self.assertEqual(tpl().PHASE_NUMBER.pattern, r"^\s*(?:phase\s*)?(\d{3})\b")

	def test_checklists_go_only_to_the_phases_create_makes(self):
		some, none_given, no_phases, _by_number = self.out["checklists_for"]
		self.assertEqual(some, {"500 mechanical room": ["Pumps"]})
		self.assertIsNone(none_given)
		self.assertIsNone(no_phases)

	def test_the_dialog_opens_while_its_fields_ask_for_their_options(self):
		# The v1.602.1 trap: a field's get_data runs during `new frappe.ui.Dialog(...)`.
		self.assertIsNone(self.out["open_error"])
		order, built, shown, title = self.out["open"]
		self.assertEqual(order, ["get_planner_templates"])
		self.assertEqual((built, shown, title), (1, True, "Start from template"))
		self.assertEqual(
			self.out["asked"][0], ["building", "t3_bundles", ["Bundle: Lighting", "Bundle: Panel"]]
		)
		self.assertEqual(self.out["asked"][1][:2], ["shown", "t3_bundles"])

	def test_nothing_picked_is_said_and_asks_nothing(self):
		hidden, said, calls = self.out["nothing"]
		self.assertFalse(hidden)
		self.assertEqual(said, "Tick at least one phase, or pick a bundle.")
		self.assertEqual(calls, 1)

	def test_the_rows_land_like_a_copy_with_their_checklists(self):
		hidden, call, rows, checklists, note = self.out["loaded"]
		self.assertTrue(hidden)
		self.assertEqual(
			call,
			[
				"get_template_rows",
				{"template": "Phases", "phases": '["100","300","500"]', "project": "PRJ-1"},
			],
		)
		self.assertEqual(
			[r[:3] for r in rows],
			[
				["Design", "1", "100 Overview"],
				["Rough", "", "300 Rough-in"],
				["Pumps in", "1", "500 Mechanical Room"],
			],
		)
		self.assertTrue(rows[1][3].startswith("row:") and rows[2][3].startswith("row:"))
		self.assertEqual(checklists, ["300 Rough-in", "500 Mechanical Room"])
		self.assertEqual(note, "3 tasks from the template.")

	def test_create_sends_the_checklists_of_the_new_phases_only(self):
		method, keys, checklists, count = self.out["create"]
		self.assertEqual(method, "build_out_tasks")
		self.assertEqual(keys, ["phase_checklists", "project", "rows"])
		self.assertEqual(checklists, {"500 Mechanical Room": ["Equipment: Pumps"]})
		self.assertEqual(count, 3)

	def test_a_bundle_appends_to_a_grid_with_rows(self):
		built, title = self.out["how"]
		self.assertEqual((built, title), (1, "The grid already has tasks"))
		rows, needs, checklists = self.out["appended"]
		self.assertEqual(
			rows,
			[
				["Typed by hand", "", ""],
				["Fixture schedule", "", "Lighting"],
				["Order fixtures", "", "Lighting"],
			],
		)
		self.assertEqual(needs, "first bundle row")
		self.assertEqual(checklists, ["Old"])

	def test_no_templates_is_said(self):
		built, said = self.out["empty"]
		self.assertEqual(built, 0)
		self.assertIn("no planner templates yet", said)


class TestPageSource(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.code = PP_JS.read_text(encoding="utf-8")
		cls.block = _t3_block(cls.code)
		cls.bare = base._strip(cls.block)

	def test_the_page_parses_and_is_lf(self):
		self.assertNotIn("\r", self.code)
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not on PATH")
		result = subprocess.run([node, "--check", str(PP_JS)], capture_output=True, text=True, timeout=60)
		self.assertEqual(result.returncode, 0, result.stderr)

	def test_it_hooks_in_through_the_build_out_footer_and_one_assign(self):
		actions = self.code[self.code.index("\tt1_actions(state, count, phases) {") :]
		actions = actions[: actions.index("\n\t},")]
		self.assertIn('__("Start from template…")', actions)
		self.assertIn("this.t3_open_templates(state)", actions)
		self.assertEqual(self.code.count("Object.assign(ProjectPlanner.prototype, PPT3_METHODS);"), 1)
		# The block ends at its one assign; a later block (T4) may follow it.
		self.assertTrue(self.block.rstrip().endswith("Object.assign(ProjectPlanner.prototype, PPT3_METHODS);"))
		self.assertIn("PPT1_PURE.checklists_for(state.checklists, built.new_phases)", self.code)

	def test_reads_only_and_no_const_dialog(self):
		self.assertEqual(
			set(re.findall(r"frappe\.call\(\{ method: `\$\{PPT3\.templates\}\.(\w+)`", self.bare)),
			{"get_planner_templates", "get_template_rows"},
		)
		self.assertIn('templates: "erpnext_enhancements.api.planner_templates"', self.block)
		for forbidden in (
			"t1_send(",
			"this.send(",
			"innerHTML",
			"localStorage",
			"pushState",
			"history.",
			'type: "GET"',
			"frappe.xcall",
		):
			self.assertNotIn(forbidden, self.bare, forbidden)
		self.assertNotRegex(self.bare, r"const \w+ = new frappe\.ui\.Dialog")
		self.assertRegex(self.bare, r"let dialog = null;\s*dialog = new frappe\.ui\.Dialog")
		# get_data reads the catalog, never the dialog.
		self.assertIn("get_data: (txt) => PPT3_PURE.bundle_options(catalog, txt)", self.bare)

	def test_methods_are_t3_prefixed_and_house_rules(self):
		table = self.block[self.block.index("const PPT3_METHODS = {") :]
		names = re.findall(r"^\t([A-Za-z_]\w*)\s*\([^)]*\)\s*\{\s*$", table, re.M)
		self.assertEqual(names, ["t3_open_templates", "t3_dialog", "t3_load"])
		for british in ("colour", "cancelled", "labour", "centre", "behaviour"):
			self.assertNotIn(british, self.block.lower(), british)
		raw = [
			line
			for line in self.block.splitlines()
			if "<" in line and re.search(r"\$\{(?!pp_esc)[a-z_.]+\}", line)
		]
		self.assertEqual(raw, [])


# ====================================================================== fixtures, docs, release, CI


class TestFixtures(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.fields = {r["name"]: r for r in json.loads(FIXTURE.read_text(encoding="utf-8"))}

	def test_every_template_field_is_a_fixture_in_the_files_own_shape(self):
		keys = set(self.fields["Task-custom_automation_settings"])
		for name in tpl().TEMPLATE_FIELDS:
			record = self.fields[name]
			self.assertEqual(set(record), keys, name)
			self.assertEqual(record["is_system_generated"], 0)
			self.assertEqual(name, f"{record['dt']}-{record['fieldname']}")
		kind = self.fields["Project Template-custom_template_kind"]
		self.assertEqual(kind["options"].split("\n")[1:], list(tpl().KINDS))
		review = self.fields["Project Template-custom_review_status"]
		self.assertEqual(review["options"].split("\n")[1:], [tpl().NEEDS_REVIEW, tpl().REVIEWED])
		line = self.fields["Task-custom_template_line"]
		self.assertEqual(line["options"].split("\n")[1:], list(tpl().LINES))
		# The Task fields show on template Tasks only; the phase ones on template phases only.
		for name in (
			"Task-custom_phase_number",
			"Task-custom_phase_runs_after",
			"Task-custom_procurement_checklist",
		):
			self.assertEqual(self.fields[name]["depends_on"], "eval:doc.is_template && doc.is_group")
		self.assertEqual(
			self.fields["Task-custom_planner_template_section"]["insert_after"], "custom_end_datetime"
		)
		self.assertIn("Task-custom_end_datetime", self.fields)

	def test_the_create_project_dialog_never_offers_a_planner_template(self):
		source = PROMPT_JS.read_text(encoding="utf-8")
		self.assertIn('get_query: () => ({ filters: { custom_template_kind: ["is", "not set"] } })', source)


class TestDocsAndCi(unittest.TestCase):
	def test_the_version_is_bumped_in_both_places_and_written_up(self):
		init = (APP / "__init__.py").read_text(encoding="utf-8")
		package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
		version = re.search(r'__version__ = "([\d.]+)"', init).group(1)
		self.assertEqual(package["version"], version)
		self.assertGreaterEqual(tuple(int(p) for p in version.split(".")), (1, 608, 0))
		changelog = CHANGELOG.read_text(encoding="utf-8")
		self.assertIn("## [1.608.0] - 2026-10-13", changelog)
		section = changelog[changelog.index("## [1.608.0]") :].split("\n## [", 1)[0]
		for needle in (
			"Start from template",
			"get_template_rows",
			"seed_planner_templates",
			"Merchanical",
			"phase_checklists",
			"Needs review",
			"800",
		):
			self.assertIn(needle, section, needle)

	def test_the_readmes_describe_it(self):
		pe = PE_README.read_text(encoding="utf-8")
		for needle in (
			"T3",
			"How to edit",
			"Start from template",
			"planner_templates.json",
			"Needs review",
			"Runs After Phases",
		):
			self.assertIn(needle, pe, needle)
		api_readme = API_README.read_text(encoding="utf-8")
		for needle in (
			"planner_templates.py",
			"get_planner_templates",
			"get_template_rows",
			"phase_checklists",
		):
			self.assertIn(needle, api_readme, needle)
		patches = PATCHES_README.read_text(encoding="utf-8")
		for needle in ("seed_planner_templates", "grant_project_template_editing"):
			self.assertIn(needle, patches, needle)

	def test_the_suite_runs_in_ci_as_unittest(self):
		ci = CI.read_text(encoding="utf-8")
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_planner_templates -v", ci)
		self.assertNotIn("pytest erpnext_enhancements/tests/test_planner_templates", ci)

	def test_the_new_python_is_tab_indented_and_lf(self):
		for path in (PURE, API, SEED_PATCH, GRANT_PATCH, DERIVE):
			source = path.read_text(encoding="utf-8")
			self.assertNotIn("\r", source, path.name)
			self.assertNotRegex(source, r"\n    (def|return|if|for) ", path.name)
		self.assertNotIn("import frappe", PURE.read_text(encoding="utf-8"))


if __name__ == "__main__":
	unittest.main()
