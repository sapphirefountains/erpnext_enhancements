# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Simple mode for both planners (``api/planner_simple.py``, ``planner_kit/simple.js``,
``docs/planner-simple-mode.md``; Nik, 2026-10-22).

What this pins, and what would fail quietly otherwise:

* **A job's card is the planner's display rule (D6), and an old job still has one (S13).** A task under a
  Phase 100–700 group or with someone on it is listed; the template admin tasks are not. A job with none of
  that (every job from before the phase templates) lists its own top-level groups, "N of M done", so the 565
  finished jobs do not all show empty cards.
* **Done, late and not scheduled are worked out from dates and status, never from the status value
  "Overdue"** (1,005 open tasks carry it). A finished job has nothing late: what was never done says so.
* **A site's card has its history and its future (S14).** Its Maintenance Records (done once submitted or in
  review, as the Maintenance Planner says), its contract's projected visits, and its project's completed
  tasks; a projection that fails is logged and the rest still answers.
* **The reads are bound SQL**, search text included, with ``%`` and ``_`` literal, and nothing is read for
  no projects.
* **Each endpoint holds its own planner's role gate** and is a plain whitelisted read.
* **The screen and both pages** are run under node (``scripts/test_planner_simple.js``; the Project Planner's
  landing and Back/Forward in ``scripts/test_planner_today.js``), and their wiring is pinned here: one-line
  hooks, nothing that writes, the kit's classes all styled.

Bench-free. Reuses ``test_planner_phase3b``'s ``frappe`` stub.

Run: python -m unittest erpnext_enhancements.tests.test_planner_simple
"""

import datetime
import importlib
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.tests import test_planner_phase3b as h

APP = Path(__file__).resolve().parents[1]
SIMPLE_PATH = APP / "api" / "planner_simple.py"
KIT_JS = APP / "public" / "js" / "planner_kit" / "simple.js"
BUNDLE_JS = APP / "public" / "js" / "planner_kit.bundle.js"
KIT_CSS = APP / "public" / "css" / "planner_kit.bundle.css"
PP_JS = APP / "project_enhancements" / "page" / "project_planner" / "project_planner.js"
PP_CSS = PP_JS.with_suffix(".css")
MP_JS = APP / "sapphire_maintenance" / "page" / "maintenance_planner" / "maintenance_planner.js"
MP_CSS = MP_JS.with_suffix(".css")
HARNESS = REPO_ROOT / "scripts" / "test_planner_simple.js"
DOC = REPO_ROOT / "docs" / "planner-simple-mode.md"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

ps = None
D = datetime.date
#: The stub's "today": Thursday 2026-10-08.
TODAY = h.TODAY


def setUpModule():
	global ps
	h.setUpModule()
	ps = importlib.import_module("erpnext_enhancements.api.planner_simple")


def tearDownModule():
	h.tearDownModule()


def task(name, subject=None, status="Open", parent=None, group=0, start=None, end=None, done_on=None):
	return {
		"name": name,
		"subject": subject or name,
		"status": status,
		"parent_task": parent,
		"is_group": group,
		"exp_start_date": start,
		"exp_end_date": end,
		"completed_on": done_on,
	}


def project(name="PRJ-1", status="Active", **kw):
	return dict(
		{"name": name, "title": f"Job {name}", "status": status, "customer": "Cust", "pm": "Clegg"}, **kw
	)


def names(items):
	return [i["name"] for i in items]


# ---------------------------------------------------------------------- the small rules


class TestTheSmallRules(unittest.TestCase):
	def test_a_page_start_is_a_whole_number_never_below_zero(self):
		for given, want in ((None, 0), ("", 0), ("40", 40), (-3, 0), ("x", 0), (7.9, 7)):
			self.assertEqual(ps.page_start(given), want, repr(given))

	def test_a_page_length_is_the_default_unless_one_to_the_most(self):
		for given, want in ((None, 20), ("", 20), ("5", 5), (0, 1), (500, 100), ("all", 20)):
			self.assertEqual(ps.page_length(given), want, repr(given))

	def test_the_search_is_trimmed_squeezed_and_capped(self):
		self.assertEqual(ps.search_text("  Sonnen   burg "), "Sonnen burg")
		self.assertEqual(ps.search_text(None), "")
		self.assertEqual(len(ps.search_text("x" * 500)), ps.SEARCH_MAX)

	def test_percent_and_underscore_in_a_search_are_literal(self):
		self.assertEqual(ps.like_pattern("50%_off\\"), "%50\\%\\_off\\\\%")


class TestATaskItem(unittest.TestCase):
	def norm(self, **kw):
		return ps.normalize_task(task("T1", **kw))

	def test_done_is_dated_by_its_completion_else_its_end(self):
		item = ps.task_item(
			self.norm(status="Completed", start="2026-09-01", end="2026-09-03", done_on="2026-09-05"),
			TODAY,
			False,
		)
		self.assertEqual((item["state"], item["date"]), ("done", "2026-09-05"))
		item = ps.task_item(self.norm(status="Completed", start="2026-09-01", end="2026-09-03"), TODAY, False)
		self.assertEqual(item["date"], "2026-09-03")

	def test_late_is_by_end_date_never_by_the_overdue_status(self):
		late = ps.task_item(self.norm(status="Open", start="2026-10-01", end="2026-10-07"), TODAY, False)
		self.assertEqual((late["state"], late["date"], late["end"]), ("late", "2026-10-01", "2026-10-07"))
		not_late = ps.task_item(
			self.norm(status="Overdue", start="2026-10-08", end="2026-10-09"), TODAY, False
		)
		self.assertEqual(not_late["state"], "open")

	def test_open_work_with_no_date_is_not_scheduled(self):
		self.assertEqual(ps.task_item(self.norm(), TODAY, False)["state"], "unscheduled")

	def test_a_finished_jobs_open_work_is_history_not_late(self):
		item = ps.task_item(self.norm(start="2021-01-04", end="2021-01-05"), TODAY, True)
		self.assertEqual((item["state"], item["date"]), ("closed", "2021-01-05"))

	def test_one_day_has_no_end(self):
		self.assertIsNone(ps.task_item(self.norm(start="2026-10-20", end="2026-10-20"), TODAY, False)["end"])

	def test_who_is_capped_and_unique(self):
		item = ps.task_item(self.norm(), TODAY, False, who=["A", "B", "A", "", "C", "D", "E"])
		self.assertEqual(item["who"], ["A", "B", "C", "D"])


# ---------------------------------------------------------------------- a job's card


class TestAJobCard(unittest.TestCase):
	def test_the_display_rule_phase_and_crew_work_and_the_admin_tasks_left_out(self):
		rows = [
			task("PH3", "Phase 300 Rough-in", group=1),
			task(
				"T-done",
				"Excavate",
				"Completed",
				"PH3",
				start="2026-10-01",
				end="2026-10-02",
				done_on="2026-10-02",
			),
			task("T-late", "Survey re-check", "Overdue", "PH3", start="2026-10-05", end="2026-10-06"),
			task("T-next", "Set forms", "Open", "PH3", start="2026-10-12", end="2026-10-13"),
			task("T-wait", "Pull wire", "Open", "PH3"),
			task("ADMIN", "Add to Accounting", "Open", start="2026-10-01", end="2026-10-01"),
			task("CREW", "Deliver pumps", "Open", start="2026-10-20", end="2026-10-20"),
		]
		who = {"T-done": ["Korben Jessop"], "CREW": ["Jesse Griffin"]}
		card = ps.build_job_card(project(), rows, TODAY, who, crewed={"CREW"})
		self.assertEqual(card["mode"], "work")
		self.assertEqual(names(card["done"]), ["T-done"])
		self.assertEqual(names(card["coming"]), ["T-late", "T-next", "CREW"])
		self.assertEqual(names(card["unscheduled"]), ["T-wait"])
		self.assertNotIn("ADMIN", names(card["done"] + card["coming"] + card["unscheduled"]))
		self.assertEqual(card["coming"][0]["state"], "late")
		self.assertEqual(card["coming"][1]["phase"], "Phase 300 Rough-in")
		self.assertIsNone(card["coming"][2]["phase"])
		self.assertEqual(card["done"][0]["who"], ["Korben Jessop"])
		self.assertEqual(card["counts"], {"done": 1, "coming": 4, "late": 1})
		self.assertEqual((card["first"], card["last"]), ("2026-10-02", "2026-10-20"))
		self.assertFalse(card["finished"])

	def test_done_is_newest_first_and_undated_history_last(self):
		rows = [
			task("PH1", "Phase 100 Design", group=1),
			task("A", status="Completed", parent="PH1", done_on="2026-01-05"),
			task("B", status="Completed", parent="PH1", done_on="2026-03-05"),
			task("C", status="Completed", parent="PH1"),
		]
		self.assertEqual(names(ps.build_job_card(project(), rows, TODAY)["done"]), ["B", "A", "C"])

	def test_an_old_job_lists_its_own_top_level_groups(self):
		rows = [
			task("G1", "Project Launch", group=1),
			task("L1", status="Completed", parent="G1", done_on="2024-05-01"),
			task("L2", status="Completed", parent="G1", done_on="2024-05-09"),
			task("G2", "Electrical", group=1),
			task("SUB", "Panels", parent="G2", group=1),
			task("E1", status="Completed", parent="SUB", done_on="2024-06-01"),
			task("E2", parent="SUB", start="2024-06-02", end="2024-06-04"),
			task("E3", parent="G2", start="2024-06-05", end="2024-06-06"),
			task("LONE", "Walkthrough", start="2024-07-01", end="2024-07-01"),
		]
		who = {"L1": ["Ann"], "E2": ["Bob"], "E3": ["Bob", "Cy"]}
		card = ps.build_job_card(project(), rows, TODAY, who)
		self.assertEqual(card["mode"], "groups")
		launch = next(i for i in card["done"] if i["name"] == "G1")
		self.assertEqual(
			(launch["state"], launch["date"], launch["of"], launch["who"]),
			("done", "2024-05-09", [2, 2], ["Ann"]),
		)
		electrical = next(i for i in card["coming"] if i["name"] == "G2")
		self.assertEqual(
			(
				electrical["kind"],
				electrical["state"],
				electrical["of"],
				electrical["date"],
				electrical["end"],
			),
			("group", "late", [1, 3], "2024-06-02", "2024-06-06"),
		)
		self.assertEqual(electrical["who"], ["Bob", "Cy"])
		self.assertIn("LONE", names(card["coming"]))
		self.assertNotIn("SUB", names(card["done"] + card["coming"] + card["unscheduled"]))

	def test_a_finished_old_job_has_nothing_late(self):
		rows = [
			task("G", "Mechanical", group=1),
			task("M1", parent="G", start="2021-02-01", end="2021-02-02"),
		]
		card = ps.build_job_card(project(status="Completed"), rows, TODAY)
		self.assertTrue(card["finished"])
		self.assertEqual(card["coming"], [])
		self.assertEqual((card["done"][0]["state"], card["done"][0]["date"]), ("closed", "2021-02-01"))
		self.assertEqual(card["counts"]["late"], 0)

	def test_a_job_with_no_dated_work_spans_its_own_dates(self):
		card = ps.build_job_card(
			project(expected_start=D(2026, 11, 1), expected_end=D(2026, 12, 1)), [], TODAY
		)
		self.assertEqual((card["first"], card["last"]), ("2026-11-01", "2026-12-01"))
		self.assertEqual((card["done"], card["coming"], card["unscheduled"]), ([], [], []))


# ---------------------------------------------------------------------- a site's card


class TestASiteCard(unittest.TestCase):
	def visit(self, name, **kw):
		return dict(
			{
				"name": name,
				"docstatus": 0,
				"workflow_state": "Draft",
				"visit_label": None,
				"visit_date": None,
			},
			**kw,
		)

	def test_visits_projections_and_old_service_tasks(self):
		visits = [
			self.visit("V-done", docstatus=1, plan_date="2026-10-01"),
			self.visit("V-review", workflow_state="Pending Review", plan_date="2026-10-06"),
			self.visit("V-late", plan_date="2026-10-05"),
			self.visit("V-started", plan_date="2026-10-05", visit_date="2026-10-05"),
			self.visit("V-next", plan_date="2026-10-15", visit_label="Startup"),
			self.visit("V-none"),
		]
		projected = [
			{"key": "MNT-1||2026-11-01", "contract": "MNT-1", "date": "2026-11-01", "feature": "Main pool"}
		]
		history = [
			task("OLD", "Winterize", "Completed", done_on="2019-11-02"),
			task("OPEN", "Admin", "Open", start="2019-11-01", end="2019-11-01"),
			task("GRP", "Service Visit(s)", "Completed", group=1),
		]
		who = {"V-done": ["Austin Healey"], "MNT-1||2026-11-01": ["Austin Healey"]}
		card = ps.build_site_card(project(), visits, projected, history, TODAY, who)
		self.assertEqual(card["mode"], "site")
		self.assertEqual(names(card["done"]), ["V-review", "V-done", "OLD"])
		self.assertTrue(card["done"][0]["review"])
		self.assertEqual(names(card["coming"]), ["V-late", "V-started", "V-next", "MNT-1"])
		self.assertEqual([i["state"] for i in card["coming"]], ["late", "open", "open", "open"])
		projected_item = card["coming"][-1]
		self.assertEqual(
			(
				projected_item["kind"],
				projected_item["doctype"],
				projected_item["label"],
				projected_item["who"],
			),
			("projected", "Sapphire Maintenance Contract", "Visit: Main pool", ["Austin Healey"]),
		)
		self.assertEqual(card["coming"][2]["label"], "Startup")
		self.assertEqual(names(card["unscheduled"]), ["V-none"])
		self.assertEqual(card["done"][1]["doctype"], "Sapphire Maintenance Record")


# ---------------------------------------------------------------------- the reads


class _FakeSql:
	"""Records each query with its values; a COUNT answers ``count``, anything else ``rows``."""

	def __init__(self, rows=(), count=0):
		self.calls, self.rows, self.count = [], list(rows), count

	def __call__(self, query, values=None, as_dict=False, **kw):
		self.calls.append((query, dict(values or {})))
		if "COUNT(*)" in query:
			return [[self.count]]
		return list(self.rows)


class TestTheReads(unittest.TestCase):
	def setUp(self):
		h._reset()

	def run_page(self, fn, text, start=0, limit=20, count=3, rows=()):
		fake = _FakeSql(rows, count)
		with (
			mock.patch.object(h.frappe.db, "sql", fake, create=True),
			mock.patch.object(h.frappe.db, "has_column", lambda *a: True, create=True),
		):
			got = fn(text, start, TODAY, limit)
		return got, fake.calls

	def test_jobs_one_page_in_order_every_value_bound(self):
		search = "50%' OR 1=1 --"
		(rows, total), calls = self.run_page(ps._page_of_jobs, search, start=40, limit=20, count=640)
		self.assertEqual(total, 640)
		self.assertEqual(len(calls), 2)
		for query, values in calls:
			self.assertNotIn("1=1", query)
			self.assertIn("LIKE %(q)s", query)
			self.assertEqual(values["q"], ps.like_pattern(search))
			self.assertIn("%(planner_types)s", query)
		page, values = calls[1]
		self.assertIn("LIMIT %(limit)s OFFSET %(start)s", page)
		self.assertEqual((values["limit"], values["start"], values["today"]), (20, 40, TODAY))
		self.assertIn("ORDER BY", page)
		self.assertLess(page.index("agg.next_day IS NOT NULL THEN 0 ELSE 1"), page.index("DESC"))
		self.assertIn("e.employee_name", page)

	def test_no_search_no_like(self):
		_got, calls = self.run_page(ps._page_of_jobs, "")
		self.assertTrue(all("LIKE" not in q and "q" not in v for q, v in calls))

	def test_sites_are_contract_and_record_projects_every_value_bound(self):
		(_rows, total), calls = self.run_page(ps._page_of_sites, "Valley_Fair", count=18)
		self.assertEqual(total, 18)
		page, values = calls[1]
		self.assertIn("`tabSapphire Maintenance Contract` c WHERE IFNULL(c.project, '') <> ''", page)
		self.assertIn("`tabSapphire Maintenance Record` r", page)
		self.assertIn("`tabSapphire Contract Feature` cf", page)
		self.assertEqual(values["q"], "%Valley\\_Fair%")
		self.assertEqual(values["pending"], "Pending Review")

	def test_no_projects_reads_nothing(self):
		fake = _FakeSql()
		with mock.patch.object(h.frappe.db, "sql", fake, create=True):
			self.assertEqual(ps._read_tasks([]), [])
			self.assertEqual(ps._read_visits([]), [])
			self.assertEqual(ps._read_history([]), [])
		self.assertEqual(fake.calls, [])
		self.assertEqual(ps._projected(set(), TODAY), {})

	def test_the_task_reads_are_bound(self):
		fake = _FakeSql()
		with (
			mock.patch.object(h.frappe.db, "sql", fake, create=True),
			mock.patch.object(h.api, "_task_has_column", lambda c: False),
		):
			ps._read_tasks(["PRJ-1"])
			ps._read_history(["PRJ-1"])
			ps._read_visits(["PRJ-1"])
		for query, values in fake.calls:
			self.assertIn("IN %(projects)s", query)
			self.assertNotIn("PRJ-1", query)
			self.assertEqual(values["projects"], ("PRJ-1",))

	def test_the_source_never_formats_a_value_into_sql(self):
		source = SIMPLE_PATH.read_text(encoding="utf-8")
		allowed = {"owner", "join", "where", "search", "slot", "engine.planner_job_condition('p')"}
		for sql in re.findall(r'f"""(.*?)"""', source, flags=re.S):
			for name in re.findall(r"\{([^}]*)\}", sql):
				self.assertIn(name, allowed, sql)
		self.assertNotRegex(source, r'fields=\[[^\]]*"(?:count|sum|max|min)\(')

	def test_who_crew_and_assignees_and_the_planners_crew_rule(self):
		h.frappe.tables.update(
			{
				"Planner Resource": [{"name": "RES-K", "user": "korben@x.com", "is_active": 1}],
				"Task Crew Member": [
					{
						"parent": "T1",
						"parenttype": "Task",
						"resource": "RES-J",
						"resource_name": "Jesse Griffin",
					}
				],
				"ToDo": [
					{
						"reference_type": "Task",
						"reference_name": "T2",
						"allocated_to": "korben@x.com",
						"status": "Open",
					},
					{
						"reference_type": "Task",
						"reference_name": "T3",
						"allocated_to": "korben@x.com",
						"status": "Closed",
					},
					{
						"reference_type": "Task",
						"reference_name": "T4",
						"allocated_to": "office@x.com",
						"status": "Open",
					},
					{
						"reference_type": "Task",
						"reference_name": "T5",
						"allocated_to": "korben@x.com",
						"status": "Cancelled",
					},
				],
				"User": [
					{"name": "korben@x.com", "full_name": "Korben Jessop"},
					{"name": "office@x.com", "full_name": "Office"},
				],
			}
		)
		who, crewed = ps._read_who(["T1", "T2", "T3", "T4", "T5"])
		self.assertEqual(
			who, {"T1": ["Jesse Griffin"], "T2": ["Korben Jessop"], "T3": ["Korben Jessop"], "T4": ["Office"]}
		)
		# A crew row, or an OPEN assignee who is a Planner Resource: a closed ToDo is who did it, not a booking.
		self.assertEqual(crewed, {"T1", "T2"})


# ---------------------------------------------------------------------- the endpoints


class TestTheEndpoints(unittest.TestCase):
	def setUp(self):
		h._reset()

	def test_each_endpoint_holds_its_planners_role_gate(self):
		h.frappe.roles = ["Customer"]
		with self.assertRaises(h._PermissionError):
			ps.get_simple_jobs()
		with self.assertRaises(h._PermissionError):
			ps.get_simple_sites()
		# A Projects User plans projects, not maintenance.
		h.frappe.roles = ["Projects User"]
		with self.assertRaises(h._PermissionError):
			ps.get_simple_sites()

	def test_jobs_answer(self):
		rows = [
			task("PH", "Phase 100 Design", group=1),
			task("T", parent="PH", start="2026-10-12", end="2026-10-12"),
		]
		for r in rows:
			r["project"] = "PRJ-1"
		with (
			mock.patch.object(ps, "_page_of_jobs", lambda text, start, today, limit: ([project()], 25)) as _p,
			mock.patch.object(ps, "_read_tasks", lambda names: rows),
			mock.patch.object(ps, "_read_who", lambda names: ({}, set())),
		):
			answer = ps.get_simple_jobs(" riverwalk ", "0", "")
		self.assertEqual(
			(answer["today"], answer["search"], answer["start"], answer["page_length"]),
			("2026-10-08", "riverwalk", 0, 20),
		)
		self.assertEqual((answer["total"], answer["more"]), (25, True))
		self.assertEqual(names(answer["cards"][0]["coming"]), ["T"])

	def test_more_is_false_on_the_last_page_and_a_limit_passes_through(self):
		seen = {}

		def page(text, start, today, limit):
			seen["limit"] = limit
			return [project()], 21

		with (
			mock.patch.object(ps, "_page_of_jobs", page),
			mock.patch.object(ps, "_read_tasks", lambda names: []),
			mock.patch.object(ps, "_read_who", lambda names: ({}, set())),
		):
			answer = ps.get_simple_jobs(None, 20, 60)
		self.assertEqual((seen["limit"], answer["more"]), (60, False))

	def test_sites_answer_with_the_technicians_names(self):
		h.frappe.roles = ["Maintenance User"]
		visits = [
			{
				"name": "V1",
				"project": "PRJ-9",
				"technician": "austin@x.com",
				"docstatus": 1,
				"plan_date": "2026-10-01",
			}
		]
		entries = {
			"PRJ-9": [
				{
					"key": "K1",
					"contract": "C1",
					"project": "PRJ-9",
					"date": "2026-10-20",
					"technician": "austin@x.com",
				}
			]
		}
		with (
			mock.patch.object(
				ps, "_page_of_sites", lambda text, start, today, limit: ([project("PRJ-9")], 1)
			),
			mock.patch.object(ps, "_read_visits", lambda names: visits),
			mock.patch.object(ps, "_read_history", lambda names: []),
			mock.patch.object(ps, "_projected", lambda names, today: entries),
			mock.patch.object(ps, "_read_who", lambda names: ({}, set())),
			mock.patch.object(ps.mp, "_full_names", lambda users: {"austin@x.com": "Austin Healey"}),
		):
			answer = ps.get_simple_sites()
		card = answer["cards"][0]
		self.assertEqual(
			(card["done"][0]["who"], card["coming"][0]["who"]), (["Austin Healey"], ["Austin Healey"])
		)
		self.assertFalse(answer["more"])

	def test_a_projection_that_fails_is_logged_and_the_site_still_answers(self):
		with mock.patch.object(ps.mp, "_projections", side_effect=RuntimeError("contract")):
			self.assertEqual(ps._projected({"PRJ-9"}, TODAY), {})
		self.assertEqual(len(h.frappe.errors), 1)

	def test_the_projection_window_and_its_sites_only(self):
		got = {}

		def projections(start, end, today):
			got["window"] = (start, end)
			return [{"project": "PRJ-9", "key": "a"}, {"project": "PRJ-OTHER", "key": "b"}]

		with (
			mock.patch.object(ps.mp, "_projections", projections),
			mock.patch.object(ps.mp, "_decorate", lambda entries: None),
		):
			out = ps._projected({"PRJ-9"}, TODAY)
		self.assertEqual(list(out), ["PRJ-9"])
		self.assertEqual(got["window"], (TODAY, TODAY + datetime.timedelta(days=ps.PROJECTION_DAYS)))

	def test_both_are_plain_whitelisted_reads(self):
		source = SIMPLE_PATH.read_text(encoding="utf-8")
		self.assertIn("@frappe.whitelist()\ndef get_simple_jobs(search=None, start=0, limit=None):", source)
		self.assertIn("@frappe.whitelist()\ndef get_simple_sites(search=None, start=0, limit=None):", source)
		for forbidden in ("frappe.db.set_value", ".save(", ".insert(", "frappe.db.commit", "delete_doc"):
			self.assertNotIn(forbidden, source)


# ---------------------------------------------------------------------- the wiring


def _code(path):
	src = re.sub(r"/\*[\s\S]*?\*/", "", path.read_text(encoding="utf-8"))
	return "\n".join(re.sub(r"(^|\s)//.*$", r"\1", line) for line in src.splitlines())


def _between(text, start, end):
	i = text.index(start)
	return text[i : text.index(end, i)]


class TestTheWiring(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.pp = PP_JS.read_text(encoding="utf-8")
		cls.mp = MP_JS.read_text(encoding="utf-8")
		cls.pps = _between(cls.pp, "const PPS = {", "Object.assign(ProjectPlanner.prototype, PPS_METHODS);")
		cls.mps = _between(
			cls.mp, "const MPS = {", "Object.assign(MaintenancePlanner.prototype, MPS_METHODS);"
		)

	def test_the_project_planners_hooks_are_one_line_each(self):
		for hook in (
			"this.init_simple(); // Simple mode",
			"const simple = this.pps_route_target(route); // Simple mode: /simple",
			"this.pps_hide(); // Simple mode's screen goes with any other",
			"if (this.view === PPS.view) return this.pps_refresh(); // Simple mode: get_simple_jobs",
			"this.pps_kit_ready(); // Simple mode draws with the kit",
			"this.pps_today_button($head); // Simple mode, one tap from Today",
			"if (link && tab === PPS.view) tab = PP6C.default_view; // nor Simple mode, which has no filters",
			"if (view === PPS.view) return [PPS.view]; // Simple mode: no date",
			'"lookahead", "gantt", "overview", "simple"],',
		):
			self.assertEqual(self.pp.count(hook), 1, hook)

	def test_the_maintenance_planners_hooks_are_one_line_each(self):
		for hook in (
			"this.init_simple(); // Simple mode",
			"if (route[1] === MPS.view) return { view: MPS.view, anchor: frappe.datetime.get_today() }; // Simple mode",
			"if (this.mps_take(target.view)) return; // Simple mode",
			"if (!route[1] && this.mps_landing()) return true; // Simple mode",
			"if (this.view === MPS.view) return this.mps_refresh(); // Simple mode: get_simple_sites",
			"this.mps_kit_ready(); // Simple mode draws with the kit",
			"if (this.view === MPS.view) return false; // Simple mode has no calendar",
		):
			self.assertEqual(self.mp.count(hook), 1, hook)

	def test_the_tour_blocks_stay_last(self):
		self.assertTrue(self.pp.rstrip().endswith("Object.assign(ProjectPlanner.prototype, PPTOUR_METHODS);"))
		self.assertTrue(
			self.mp.rstrip().endswith("Object.assign(MaintenancePlanner.prototype, MPTOUR_METHODS);")
		)

	def test_the_blocks_write_nothing_and_read_with_a_quiet_get(self):
		for block, method in ((self.pps, "get_simple_jobs"), (self.mps, "get_simple_sites")):
			for forbidden in (
				"this.send(",
				"save_task",
				"frappe.xcall",
				"pushState",
				"replaceState",
				"frappe.call(",
			):
				self.assertNotIn(forbidden, block)
			self.assertIn(f'method: "erpnext_enhancements.api.planner_simple.{method}"', block)
			self.assertIn('http: "GET",', block)
			self.assertIn("kit.quiet_call(", block)

	def test_the_kit_installs_it(self):
		bundle = BUNDLE_JS.read_text(encoding="utf-8")
		self.assertIn('from "./planner_kit/simple.js";', bundle)
		self.assertIn("simple: Object.assign(create_simple(env),", bundle)

	def test_every_class_the_kit_draws_is_styled_and_every_rule_is_drawn(self):
		js = KIT_JS.read_text(encoding="utf-8")
		css = KIT_CSS.read_text(encoding="utf-8")
		drawn = set(re.findall(r"\bpk-simple(?:-[a-z0-9]+)*", js))
		# The states are drawn as pk-simple-${state}.
		drawn |= {f"pk-simple-{s}" for s in ("done", "closed", "late", "open", "unscheduled")}
		drawn -= {"pk-simple-"}
		styled = set(re.findall(r"\.(pk-simple(?:-[a-z0-9]+)*)", css))
		hooks = {"pk-simple-half"}  # styled; drawn inside a template string with its key
		self.assertEqual(sorted(drawn - styled - {"pk-simple-retry", "pk-simple-card"} - hooks), [])
		self.assertEqual(sorted(styled - drawn), [])

	def test_the_pages_hide_their_chrome_with_one_rule(self):
		self.assertIn(".pp-wrap.pp-simple-on > :not(.pp-simple) {", PP_CSS.read_text(encoding="utf-8"))
		self.assertIn(".mp-wrap.mp-simple-on > :not(.mp-simple) {", MP_CSS.read_text(encoding="utf-8"))

	def test_the_design_note_has_nik_s_decisions(self):
		doc = DOC.read_text(encoding="utf-8")
		for decision in (
			"S1",
			"S7",
			"S9",
			"S10",
			"S13",
			"S14",
			'"Simple mode switch"',
			'"B: Done \\| Coming up card"',
		):
			self.assertIn(decision, doc)

	def test_the_suite_has_its_own_ci_step_and_the_module_its_readme_row(self):
		self.assertIn(
			"run: python -m unittest erpnext_enhancements.tests.test_planner_simple -v",
			CI.read_text(encoding="utf-8"),
		)
		self.assertIn("`planner_simple.py`", (APP / "api" / "README.md").read_text(encoding="utf-8"))


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestTheBehaviourHarness(unittest.TestCase):
	def test_the_harness_passes(self):
		result = subprocess.run(
			[shutil.which("node"), str(HARNESS)],
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=300,
		)
		output = (result.stdout + result.stderr)[-6000:]
		self.assertEqual(result.returncode, 0, output)
		self.assertIn("checks passed", result.stdout, output)


if __name__ == "__main__":
	unittest.main()
