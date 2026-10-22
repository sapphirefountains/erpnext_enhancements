# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""P9 of the Planner UX redesign: the weekly summary, its printout, its Monday email and the Settings
that switch it on (``project_enhancements/planner_weekly_summary.py``; ``docs/planner-ux-redesign.md``
§4.7, and Nik's D16: "Print + optional email ... behind a Settings switch, OFF until Nik turns it on").

What this pins, and what would fail quietly otherwise:

* **The email is OFF.** The Settings Check ships unticked (default 0, no row on production until the
  backfill writes one), the Monday job returns before its first query while it is off or nobody is named,
  and a switched-on job sends at most once a week through a database row, not redis.
* **The backfill is fill-only and cannot raise.** It writes the new Check's row where ``tabSingles`` has
  none, never over a stored value (an unticked box, a ticked one), makes a never-saved Single whole, does
  nothing the second time, and swallows even a failure of its own.
* **A Draft schedule is never described.** The summary reads the Published jobs and names the Draft ones.
* **Nothing typed is trusted.** The printout escapes every job title, task subject and person; the email
  passes only strings to the component macros, which escape them.
* **"What moved" is the Version log, folded:** the first old value and the last new value, a task moved
  and moved back is not listed, and a logged value that is not a date does not break the page.
* **Recipients are addresses.** A line that is not one refuses the Settings save, by name.

Bench-free. Reuses ``test_planner_phase3b``'s ``frappe`` stub, plus the two small modules the Settings
controller imports.

Run: python -m unittest erpnext_enhancements.tests.test_planner_weekly_summary
"""

import datetime
import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.tests import test_planner_phase3b as h

APP = Path(__file__).resolve().parents[1]
SETTINGS_JSON = APP / "project_enhancements/doctype/project_planner_settings/project_planner_settings.json"
PATCH_PATH = APP / "patches/backfill_weekly_summary_setting.py"
PATCHES_TXT = APP / "patches.txt"
PATCHES_README = APP / "patches/README.md"
HOOKS = APP / "hooks.py"
CI = REPO_ROOT / ".github/workflows/ci.yml"

D = datetime.date
ov = None
ws = None
settings_mod = None
patch_mod = None


class _Document:
	"""frappe.model.document.Document, as far as the settings controller imports it."""


def setUpModule():
	global ov, ws, settings_mod, patch_mod
	h.setUpModule()
	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = _Document
	model.document = document
	model.no_value_fields = ()
	sys.modules.update({"frappe.model": model, "frappe.model.document": document})
	h.frappe.model = model
	# The fake email style has the pieces the digest uses; the summary also uses these three.
	for name in ("h", "kpis", "callout"):
		if not hasattr(h.email_style, name):
			setattr(h.email_style, name, _piece(name))
	ov = importlib.import_module("erpnext_enhancements.api.planner_overview")
	ws = importlib.import_module("erpnext_enhancements.project_enhancements.planner_weekly_summary")
	settings_mod = importlib.import_module(
		"erpnext_enhancements.project_enhancements.doctype.project_planner_settings.project_planner_settings"
	)
	patch_mod = importlib.import_module("erpnext_enhancements.patches.backfill_weekly_summary_setting")


def tearDownModule():
	h.tearDownModule()


def _piece(tag):
	def build(*args, **kwargs):
		h.email_style.calls.append((tag, args, kwargs))
		return f"<{tag}>{json.dumps(args, default=str)}</{tag}>"

	return build


# ====================================================================== pure helpers


class TestWording(unittest.TestCase):
	def test_days_and_ranges(self):
		self.assertEqual(ws.day_text("2026-10-26"), "Mon Oct 26")
		self.assertEqual(ws.day_text(D(2026, 10, 26), with_month=False), "Mon 26")
		self.assertEqual(ws.day_text(None), "")
		self.assertEqual(ws.range_text("2026-10-26", "2026-10-28"), "Mon Oct 26–Wed Oct 28")
		self.assertEqual(ws.range_text("2026-10-26", "2026-10-26"), "Mon Oct 26")
		self.assertEqual(ws.range_text("2026-10-26", None), "Mon Oct 26")

	def test_the_week_across_a_month_and_a_year(self):
		self.assertEqual(ws.week_text("2026-10-19"), "Oct 19 – 25, 2026")
		self.assertEqual(ws.week_text("2026-10-26"), "Oct 26 – Nov 1, 2026")
		self.assertEqual(ws.week_text("2026-12-27"), "Dec 27 – Jan 2, 2027")

	def test_a_span_with_no_start_says_no_dates(self):
		self.assertEqual(ws.span_text(["2026-10-26", "2026-10-28"]), "Mon Oct 26–Wed Oct 28")
		self.assertEqual(ws.span_text(None), "no dates")


class TestGroupDays(unittest.TestCase):
	def test_consecutive_days_with_the_same_places_are_one_segment(self):
		entries = [
			("2026-10-26", ["Riverwalk"]),
			("2026-10-27", ["Riverwalk"]),
			("2026-10-28", ["Riverwalk"]),
			("2026-10-29", ["Tanner Park"]),
		]
		got = ws.group_days(entries)
		self.assertEqual(
			[(str(a), str(b), p) for a, b, p in got],
			[("2026-10-26", "2026-10-28", ("Riverwalk",)), ("2026-10-29", "2026-10-29", ("Tanner Park",))],
		)
		self.assertEqual(ws.segment_text(got[0]), "Mon Oct 26–Wed Oct 28: Riverwalk")
		self.assertEqual(ws.segment_text(got[1]), "Thu Oct 29: Tanner Park")

	def test_a_gap_or_a_different_set_starts_a_new_segment(self):
		got = ws.group_days(
			[
				("2026-10-26", ["A"]),
				("2026-10-28", ["A"]),
				("2026-10-29", ["A", "B"]),
				("2026-10-30", ["B", "A"]),
			]
		)
		self.assertEqual(
			[(str(a), str(b)) for a, b, _p in got],
			[("2026-10-26", "2026-10-26"), ("2026-10-28", "2026-10-28"), ("2026-10-29", "2026-10-30")],
		)

	def test_an_unsorted_input_and_empty_days(self):
		got = ws.group_days([("2026-10-27", ["A"]), ("2026-10-26", ["A"]), ("2026-10-28", [])])
		self.assertEqual([(str(a), str(b)) for a, b, _p in got], [("2026-10-26", "2026-10-27")])
		self.assertEqual(ws.group_days([]), [])


class TestFoldDateChanges(unittest.TestCase):
	def version(self, *changes):
		return json.dumps({"changed": [list(c) for c in changes]})

	def test_the_first_old_and_the_last_new(self):
		rows = [
			("T1", self.version(("exp_start_date", "2026-10-20 00:00:00", "2026-10-22 00:00:00"))),
			(
				"T1",
				self.version(
					("exp_start_date", "2026-10-22 00:00:00", "2026-10-26 00:00:00"),
					("exp_end_date", "2026-10-23", "2026-10-28"),
				),
			),
			("T2", self.version(("subject", "A", "B"))),
		]
		got = ws.fold_date_changes(rows)
		self.assertEqual(
			got, {"T1": {"start": ("2026-10-20", "2026-10-26"), "end": ("2026-10-23", "2026-10-28")}}
		)

	def test_a_task_moved_and_moved_back_is_not_listed(self):
		rows = [
			("T1", self.version(("exp_start_date", "2026-10-20", "2026-10-22"))),
			("T1", self.version(("exp_start_date", "2026-10-22", "2026-10-20"))),
		]
		self.assertEqual(ws.fold_date_changes(rows), {})

	def test_a_row_that_is_not_json_or_has_no_changes_is_skipped(self):
		rows = [
			("T1", "not json"),
			("T2", None),
			("T3", json.dumps({"added": []})),
			("T4", json.dumps({"changed": [["exp_start_date"]]})),
			("T5", json.dumps([1, 2])),
		]
		self.assertEqual(ws.fold_date_changes(rows), {})

	def test_a_line_shows_the_days_it_had_and_has(self):
		task = {"name": "T1", "subject": "Set vault", "start": D(2026, 10, 26), "end": D(2026, 10, 28)}
		line = ws.moved_line(task, {"start": ("2026-10-20", "2026-10-26")})
		self.assertEqual(
			line,
			{
				"task": "T1",
				"subject": "Set vault",
				"from": ["2026-10-20", "2026-10-28"],
				"to": ["2026-10-26", "2026-10-28"],
			},
		)
		# The end alone moved: the start it has is the start it had.
		line = ws.moved_line(task, {"end": ("2026-10-27", "2026-10-28")})
		self.assertEqual(line["from"], ["2026-10-26", "2026-10-27"])
		# Nothing differs after all.
		self.assertIsNone(ws.moved_line(task, {"start": ("2026-10-26", "2026-10-26")}))

	def test_a_logged_value_that_is_not_a_date_does_not_break_it(self):
		task = {"name": "T1", "subject": "Set vault", "start": D(2026, 10, 26), "end": D(2026, 10, 28)}
		line = ws.moved_line(task, {"start": ("None", "2026-10-26")})
		self.assertIsNone(line)  # the old start is unreadable, so it is taken as unchanged


class TestRecipients(unittest.TestCase):
	def test_split_on_commas_semicolons_and_lines_distinct_and_in_order(self):
		good, bad = settings_mod.parse_recipients("james@x.com, Lisa@x.com;nik@x.com\nJAMES@x.com\r\n  \n")
		self.assertEqual(good, ["james@x.com", "Lisa@x.com", "nik@x.com"])
		self.assertEqual(bad, [])

	def test_what_is_not_an_address_is_named(self):
		good, bad = settings_mod.parse_recipients("james@x.com, James, nobody@, a b@c.com, <a@b.co>")
		self.assertEqual(good, ["james@x.com"])
		self.assertEqual(bad, ["James", "nobody@", "a b@c.com", "<a@b.co>"])

	def test_blank_is_nobody(self):
		for blank in (None, "", "  \n ,, ;"):
			self.assertEqual(settings_mod.parse_recipients(blank), ([], []))

	def test_validate_refuses_a_line_that_is_not_an_address_and_allows_blank(self):
		def validate(text):
			doc = settings_mod.ProjectPlannerSettings.__new__(settings_mod.ProjectPlannerSettings)
			doc.meta = types.SimpleNamespace(get_label=lambda field: field)
			doc.default_day_hours = None
			doc.get = lambda key, default=None: {"weekly_summary_recipients": text}.get(key, default)
			doc.validate()

		validate(None)
		validate("james@x.com")
		with self.assertRaises(h._Throw) as caught:
			validate("james@x.com, James")
		self.assertIn("James", str(caught.exception))
		self.assertNotIn("james@x.com", str(caught.exception).replace("James", ""))


# ====================================================================== the read


def found(jobs=None, **kw):
	base = {
		"today": D(2026, 10, 26),
		"listed": [],
		"jobs": jobs if jobs is not None else [],
		"shown": {},
		"over": {},
		"conflicts": [],
		"drafts_named": [],
		"overlay": None,
	}
	base.update(kw)
	return base


def job(name="PRJ-1", title="Riverwalk Plaza", pm="Clegg", published_on=None, **kw):
	base = {
		"name": name,
		"title": title,
		"pm": pm,
		"schedule": {"status": "Published", "published_on": published_on, "baseline_set_on": None},
		"draft": False,
		"behind": [],
		"overdue": {"count": 0, "oldest": None, "tasks": []},
		"hours_tasks": [],
		"counts": {"tasks": 0, "baselined": 0},
	}
	base.update(kw)
	return base


def work(name, subject, start, end, status="Open"):
	return {
		"name": name,
		"subject": subject,
		"status": status,
		"start": D.fromisoformat(start),
		"end": D.fromisoformat(end),
		"parent": None,
		"group": False,
		"base_start": None,
		"base_end": None,
		"hours": 0,
		"milestone": False,
	}


class TestBuildSummary(unittest.TestCase):
	def setUp(self):
		h._reset()
		self.found = found(
			[
				job("PRJ-1", "Riverwalk Plaza", published_on="2026-10-24 09:00:00"),
				job("PRJ-2", "Tanner Park"),
			],
			shown={
				"PRJ-1": [
					work("T1", "Excavate basin", "2026-10-26", "2026-10-28"),
					work("T2", "Pour slab", "2026-10-29", "2026-10-29"),
					work("T3", "Last month", "2026-09-01", "2026-09-04"),
					work("T4", "Already done", "2026-10-26", "2026-10-27", status="Completed"),
				],
				"PRJ-2": [],
			},
			drafts_named=["Myers-Roy"],
		)
		self.patches = [
			mock.patch.object(ov, "collect", lambda **kw: self.found),
			mock.patch.object(ws, "_crew_labels", lambda names: {"T1": ["Kyle Jones", "Ana Hill"]}),
			mock.patch.object(
				ws,
				"_places",
				lambda first, last: (
					[("Austin Healey", [(D(2026, 10, 26), D(2026, 10, 28), ("Riverwalk Plaza",))])],
					["Lisa Park"],
				),
			),
			mock.patch.object(
				ws,
				"_moved",
				lambda found_, since: {
					"PRJ-1": [
						{
							"task": "T1",
							"subject": "Excavate basin",
							"from": ["2026-10-20", "2026-10-22"],
							"to": ["2026-10-26", "2026-10-28"],
						}
					]
				},
			),
			mock.patch.object(h.api, "_site_first_weekday", lambda: 6),
		]
		for p in self.patches:
			p.start()
		self.addCleanup(lambda: [p.stop() for p in self.patches])

	def test_the_default_week_is_the_week_ahead(self):
		# Today in the stub is Thursday Oct 8; Sunday-first, so the week ahead starts Sunday Oct 11.
		self.assertEqual(ws.build_summary()["first"], D(2026, 10, 11))
		self.assertEqual(ws.build_summary(today="2026-10-26")["first"], D(2026, 10, 25))

	def test_a_named_day_snaps_to_its_weeks_first_day(self):
		data = ws.build_summary("2026-10-29", today="2026-10-26")
		self.assertEqual((data["first"], data["last"]), (D(2026, 10, 25), D(2026, 10, 31)))

	def test_a_job_lists_the_open_work_that_overlaps_the_week_in_order(self):
		data = ws.build_summary("2026-10-26", today="2026-10-26")
		self.assertEqual([j["title"] for j in data["jobs"]], ["Riverwalk Plaza"])
		lines = data["jobs"][0]["lines"]
		self.assertEqual([l["subject"] for l in lines], ["Excavate basin", "Pour slab"])
		self.assertEqual(lines[0]["crew"], ["Kyle Jones", "Ana Hill"])
		self.assertEqual(lines[1]["crew"], [])
		self.assertEqual(data["jobs"][0]["more"], 0)

	def test_people_and_those_free_all_week(self):
		data = ws.build_summary("2026-10-26", today="2026-10-26")
		self.assertEqual([p["label"] for p in data["people"]], ["Austin Healey"])
		self.assertEqual(data["free"], ["Lisa Park"])

	def test_what_moved_and_what_was_published(self):
		data = ws.build_summary("2026-10-26", today="2026-10-26")
		self.assertEqual(data["moved_total"], 1)
		self.assertEqual(data["moved"][0]["title"], "Riverwalk Plaza")
		# Published Oct 24, inside the seven days before "now" in the stub (Oct 8): not yet; so set it near now.
		self.found["jobs"][0]["schedule"]["published_on"] = "2026-10-07 09:00:00"
		data = ws.build_summary("2026-10-26", today="2026-10-26")
		self.assertEqual(data["published"], [{"title": "Riverwalk Plaza", "on": "2026-10-07 09:00:00"}])

	def test_drafts_are_named_not_described(self):
		data = ws.build_summary("2026-10-26", today="2026-10-26")
		self.assertEqual(data["drafts_named"], ["Myers-Roy"])
		self.assertNotIn("Myers-Roy", [j["title"] for j in data["jobs"]])

	def test_the_summary_is_asked_for_published_jobs_only_without_drafts(self):
		seen = {}

		def collect(**kw):
			seen.update(kw)
			return self.found

		with mock.patch.object(ov, "collect", collect):
			ws.build_summary("2026-10-26", today="2026-10-26")
		self.assertEqual((seen["draft"], seen["published_only"]), (False, True))

	def test_a_long_job_is_cut_and_says_how_many_more(self):
		self.found["shown"]["PRJ-1"] = [
			work(f"T{i}", f"Task {i:02d}", "2026-10-26", "2026-10-27") for i in range(15)
		]
		data = ws.build_summary("2026-10-26", today="2026-10-26")
		self.assertEqual(
			(len(data["jobs"][0]["lines"]), data["jobs"][0]["more"]), (ws.JOB_LINES, 15 - ws.JOB_LINES)
		)


# ====================================================================== the printout and the email


def summary(**kw):
	base = {
		"first": D(2026, 10, 25),
		"last": D(2026, 10, 31),
		"today": D(2026, 10, 26),
		"exceptions": [],
		"more_exceptions": 0,
		"counts": {"lines": 0},
		"conflicts_ok": True,
		"jobs": [],
		"people": [],
		"free": [],
		"moved": [],
		"moved_total": 0,
		"published": [],
		"drafts_named": [],
		"job_count": 0,
	}
	base.update(kw)
	return base


EVIL = '<script>alert("x")</script>'


class TestThePrintout(unittest.TestCase):
	def test_a_quiet_week_says_so_in_each_section(self):
		html = ws.summary_document(summary(), printed_on="Oct 26, 2026")
		for text in (
			"Weekly summary",
			"Week of Oct 25 – 31, 2026",
			"Nothing needs a manager this week.",
			"No Published job has work scheduled this week.",
			"Nobody is booked this week.",
			"No task dates moved.",
			"Printed Oct 26, 2026",
		):
			self.assertIn(text, html)
		self.assertTrue(html.startswith("<!doctype html>"))
		self.assertIn("@page { size: letter portrait;", html)

	def test_everything_typed_is_escaped(self):
		data = summary(
			counts={"lines": 1},
			exceptions=[
				{"kind": "behind", "text": f"{EVIL}: Phase 300 is 4 working days behind its baseline"}
			],
			jobs=[
				{
					"name": "P",
					"title": EVIL,
					"pm": EVIL,
					"lines": [{"subject": EVIL, "start": "2026-10-26", "end": "2026-10-27", "crew": [EVIL]}],
					"more": 0,
				}
			],
			people=[{"label": EVIL, "segments": [(D(2026, 10, 26), D(2026, 10, 26), (EVIL,))]}],
			free=[EVIL],
			moved=[
				{
					"name": "P",
					"title": EVIL,
					"count": 1,
					"lines": [
						{
							"subject": EVIL,
							"from": ["2026-10-20", "2026-10-21"],
							"to": ["2026-10-26", "2026-10-27"],
						}
					],
				}
			],
			moved_total=1,
			published=[{"title": EVIL, "on": "2026-10-24 09:00:00"}],
			drafts_named=[EVIL],
		)
		html = ws.summary_document(data, printed_on=EVIL)
		self.assertNotIn("<script>", html)
		self.assertIn("&lt;script&gt;", html)

	def test_the_exception_list_is_a_table_with_the_kind_in_words(self):
		data = summary(
			counts={"lines": 2},
			exceptions=[
				{
					"kind": "behind",
					"text": "Riverwalk Plaza: Phase 300 is 4 working days behind its baseline",
				},
				{
					"kind": "overdue",
					"text": "Riverwalk Plaza: 6 tasks overdue (the oldest was due Oct 3: Set vault)",
				},
			],
			more_exceptions=3,
		)
		html = ws.summary_document(data, printed_on="x")
		self.assertIn("Behind baseline", html)
		self.assertIn("Overdue", html)
		self.assertIn("4 working days behind its baseline", html)
		self.assertIn("and 3 more", html)
		self.assertNotIn("Nothing needs a manager", html)

	def test_conflicts_that_could_not_be_read_are_said(self):
		self.assertIn(
			"Conflicts could not be read", ws.summary_document(summary(conflicts_ok=False), printed_on="x")
		)

	def test_draft_schedules_are_named_at_the_foot(self):
		html = ws.summary_document(summary(drafts_named=["Myers-Roy", "Tanner Park"]), printed_on="x")
		self.assertIn("Draft schedules, not described here: Myers-Roy, Tanner Park.", html)

	def test_a_person_with_a_week_reads_as_their_segments(self):
		data = summary(
			people=[
				{
					"label": "Austin Healey",
					"segments": [
						(D(2026, 10, 26), D(2026, 10, 28), ("Riverwalk Plaza",)),
						(D(2026, 10, 29), D(2026, 10, 29), ("Tanner Park", "Visit: Sanctuary")),
					],
				}
			]
		)
		html = ws.summary_document(data, printed_on="x")
		self.assertIn(
			"Mon Oct 26–Wed Oct 28: Riverwalk Plaza<br>Thu Oct 29: Tanner Park, Visit: Sanctuary", html
		)


class TestTheEmail(unittest.TestCase):
	def setUp(self):
		h._reset()

	def pieces(self):
		return [call[0] for call in h.email_style.calls]

	def test_it_is_built_only_from_the_shells_components(self):
		data = summary(
			counts={"lines": 1},
			exceptions=[
				{"kind": "behind", "text": "Riverwalk Plaza: Phase 300 is 4 working days behind its baseline"}
			],
			jobs=[
				{
					"name": "P",
					"title": "Riverwalk Plaza",
					"pm": "Clegg",
					"lines": [
						{"subject": "Excavate", "start": "2026-10-26", "end": "2026-10-28", "crew": ["Kyle"]}
					],
					"more": 0,
				}
			],
			people=[
				{"label": "Austin", "segments": [(D(2026, 10, 26), D(2026, 10, 26), ("Riverwalk Plaza",))]}
			],
			moved=[
				{
					"name": "P",
					"title": "Riverwalk Plaza",
					"count": 1,
					"lines": [
						{
							"subject": "Excavate",
							"from": ["2026-10-20", "2026-10-21"],
							"to": ["2026-10-26", "2026-10-27"],
						}
					],
				}
			],
			moved_total=1,
		)
		subject, html = ws.summary_email(data)
		self.assertEqual(subject, "Weekly schedule summary: week of Sun Oct 25")
		self.assertTrue(html.startswith("<shell>"))
		pieces = self.pieces()
		for tag in ("kpis", "h", "bullets", "table", "button", "note", "wrap"):
			self.assertIn(tag, pieces)
		# No markup of its own: every argument it handed a component is a plain string or a list of them.
		source = (APP / "project_enhancements/planner_weekly_summary.py").read_text(encoding="utf-8")
		body = source[
			source.index("def summary_email(") : source.index(
				"# ----------------------------------------------------------------------",
				source.index("def summary_email("),
			)
		]
		self.assertNotIn("<", body.replace("<shell>", ""))
		# The email tables stay within four columns (five will not fit a phone).
		for tag, args, _kw in h.email_style.calls:
			if tag == "table":
				self.assertLessEqual(len(args[0]), 4)

	def test_a_quiet_week_is_a_success_note_and_a_button_to_the_overview(self):
		subject, html = ws.summary_email(summary())
		self.assertIn("callout", self.pieces())
		button = next(c for c in h.email_style.calls if c[0] == "button")
		self.assertEqual(button[1][0], "https://erp.example.com/app/project-planner/overview/2026-10-26")

	def test_a_preview_says_so(self):
		subject, html = ws.summary_email(summary(), subject_prefix="Preview: ", note="This is a preview.")
		self.assertTrue(subject.startswith("Preview: Weekly schedule summary"))
		self.assertTrue(any("This is a preview." in str(c[1]) for c in h.email_style.calls if c[0] == "note"))


# ====================================================================== the Monday job


class TestTheMondayJob(unittest.TestCase):
	def setUp(self):
		h._reset()
		self.build = mock.MagicMock(
			return_value=summary(
				jobs=[{"name": "P", "title": "Riverwalk Plaza", "pm": None, "lines": [], "more": 0}]
			)
		)
		self.patches = [
			mock.patch.object(ws, "build_summary", self.build),
			mock.patch.object(h.api, "_site_first_weekday", lambda: 6),
			mock.patch.object(ws, "getdate", lambda value=None: D(2026, 10, 26)),
		]
		for p in self.patches:
			p.start()
		self.addCleanup(lambda: [p.stop() for p in self.patches])

	def settings(self, on, recipients):
		h._settings(weekly_summary_email=on, weekly_summary_recipients=recipients)

	def test_off_it_does_nothing_before_any_query(self):
		self.settings(0, "james@x.com")
		h.frappe.queries.clear()
		ws.send_weekly_summary()
		self.assertEqual(self.build.call_count, 0)
		self.assertEqual(h.frappe.sent, [])
		self.assertEqual(h.frappe.queries, [])
		self.assertEqual(h.frappe.tables["Planner Digest Log"], [])

	def test_a_setting_that_cannot_be_read_is_off(self):
		h.frappe.cached.pop("Project Planner Settings", None)
		ws.send_weekly_summary()
		self.assertEqual(h.frappe.sent, [])
		self.assertEqual(self.build.call_count, 0)

	def test_on_with_nobody_named_sends_nothing(self):
		self.settings(1, "")
		ws.send_weekly_summary()
		self.assertEqual((h.frappe.sent, self.build.call_count), ([], 0))
		self.settings(1, "not an address")
		ws.send_weekly_summary()
		self.assertEqual(h.frappe.sent, [])

	def test_on_it_sends_one_email_to_the_named_managers(self):
		self.settings(1, "james@x.com, lisa@x.com")
		ws.send_weekly_summary()
		self.assertEqual(len(h.frappe.sent), 1)
		self.assertEqual(h.frappe.sent[0]["recipients"], ["james@x.com", "lisa@x.com"])
		self.assertTrue(h.frappe.sent[0]["subject"].startswith("Weekly schedule summary: week of"))
		# Monday's week is the week holding Monday (Sunday-first), built for that week and that day.
		self.assertEqual(self.build.call_args[0], (D(2026, 10, 25), D(2026, 10, 26)))

	def test_the_week_is_claimed_before_it_is_sent_and_a_second_run_sends_nothing(self):
		self.settings(1, "james@x.com")
		ws.send_weekly_summary()
		rows = h.frappe.tables["Planner Digest Log"]
		self.assertEqual([r["log_key"] for r in rows], ["weekly-summary|2026-10-25"])
		self.assertEqual(rows[0]["user"], "Administrator")
		# The row is committed before anything is sent, so a crash while sending cannot send twice.
		self.assertLess(h.frappe.sequence.index("commit"), h.frappe.sequence.index("sendmail"))
		ws.send_weekly_summary()
		self.assertEqual(len(h.frappe.sent), 1)

	def test_a_claim_that_fails_sends_nothing(self):
		self.settings(1, "james@x.com")
		h.frappe.fail_insert["Planner Digest Log"] = RuntimeError("duplicate key")
		ws.send_weekly_summary()
		self.assertEqual(h.frappe.sent, [])
		self.assertEqual(self.build.call_count, 0)

	def test_a_failure_while_building_is_logged_and_never_raised(self):
		self.settings(1, "james@x.com")
		self.build.side_effect = RuntimeError("engine fell over")
		ws.send_weekly_summary()
		self.assertEqual(h.frappe.sent, [])
		self.assertEqual(len(h.frappe.errors), 1)
		self.assertIn("Project Planner: weekly summary failed", str(h.frappe.errors[0]))

	def test_a_failed_send_is_not_retried_this_week(self):
		self.settings(1, "james@x.com")
		original = h.frappe.sendmail
		h.frappe.sendmail = mock.MagicMock(side_effect=RuntimeError("smtp down"))
		try:
			ws.send_weekly_summary()
			ws.send_weekly_summary()
		finally:
			h.frappe.sendmail = original
		self.assertEqual(h.frappe.sendmail if False else len(h.frappe.sent), 0)
		self.assertEqual(len(h.frappe.tables["Planner Digest Log"]), 1)

	def test_a_preview_goes_to_the_caller_alone_and_claims_nothing(self):
		self.settings(0, "james@x.com")
		answer = ws.send_preview("nik@example.com")
		self.assertTrue(answer["sent"])
		self.assertEqual(len(h.frappe.sent), 1)
		self.assertEqual(h.frappe.sent[0]["recipients"], ["nik@example.com"])
		self.assertTrue(h.frappe.sent[0]["subject"].startswith("Preview: "))
		self.assertEqual(h.frappe.tables["Planner Digest Log"], [])


# ====================================================================== the setting and its patch


class TestTheSetting(unittest.TestCase):
	def setUp(self):
		self.fields = {
			f["fieldname"]: f
			for f in json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))["fields"]
			if "fieldname" in f
		}

	def test_the_switch_is_a_check_that_is_off(self):
		field = self.fields["weekly_summary_email"]
		self.assertEqual((field["fieldtype"], field["default"]), ("Check", "0"))

	def test_the_recipients_have_no_default_and_show_only_with_the_switch(self):
		field = self.fields["weekly_summary_recipients"]
		self.assertEqual(field["fieldtype"], "Small Text")
		self.assertNotIn("default", field)
		self.assertEqual(field["depends_on"], "weekly_summary_email")

	def test_they_are_in_the_field_order_after_the_customer_fields(self):
		order = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))["field_order"]
		self.assertEqual(
			order[-4:],
			["section_summary", "weekly_summary_email", "column_break_summary", "weekly_summary_recipients"],
		)
		self.assertEqual(len(order), len(set(order)))
		self.assertEqual(set(order), set(self.fields))

	def test_exactly_one_new_check_with_a_default_one_new_field_without(self):
		new = ("weekly_summary_email", "weekly_summary_recipients")
		self.assertEqual([n for n in new if "default" in self.fields[n]], ["weekly_summary_email"])


class TestTheBackfill(unittest.TestCase):
	"""The patch is ``materialize_defaults`` under a cover: fill-only, twice-safe, unable to raise."""

	def run_patch(self, stored=()):
		writes = []
		fields = [
			types.SimpleNamespace(fieldname=f["fieldname"], default=f.get("default"))
			for f in json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))["fields"]
			if "fieldname" in f
		]
		h._reset()
		h.frappe.db = types.SimpleNamespace(
			exists=lambda doctype, name=None: True,
			sql=lambda query, params=None: [(name,) for name in stored],
			set_single_value=lambda doctype, values, **kw: writes.append((doctype, dict(values), kw)),
		)
		h.frappe.get_meta = lambda doctype: types.SimpleNamespace(fields=fields)
		h.frappe.clear_document_cache = lambda *a, **k: None
		patch_mod.execute()
		return writes

	def test_it_writes_the_switch_off_where_there_is_no_row(self):
		every = {
			f["fieldname"]
			for f in json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))["fields"]
			if "fieldname" in f and "default" in f
		}
		writes = self.run_patch(stored=every - {"weekly_summary_email"})
		self.assertEqual(len(writes), 1)
		self.assertEqual(writes[0][1], {"weekly_summary_email": "0"})
		self.assertEqual(writes[0][2], {"update_modified": False})

	def test_it_never_writes_over_a_stored_value_a_ticked_box_included(self):
		every = {
			f["fieldname"]
			for f in json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))["fields"]
			if "fieldname" in f
		}
		self.assertEqual(self.run_patch(stored=every), [])
		self.assertEqual(self.run_patch(stored={"weekly_summary_email"}) and True, True)
		writes = self.run_patch(stored={"weekly_summary_email"})
		self.assertTrue(all("weekly_summary_email" not in w[1] for w in writes))

	def test_a_never_saved_single_is_made_whole_so_no_later_write_blanks_the_rest(self):
		writes = self.run_patch(stored=())
		self.assertEqual(len(writes), 1)
		self.assertEqual(writes[0][1]["weekly_summary_email"], "0")
		self.assertIn("default_day_hours", writes[0][1])

	def test_run_twice_the_second_finds_a_row_for_everything(self):
		first = self.run_patch(stored=())
		stored = set(first[0][1])
		self.assertEqual(self.run_patch(stored=stored), [])

	def test_it_cannot_raise(self):
		def boom(query, params=None):
			raise RuntimeError("tabSingles unreadable")

		h._reset()
		h.frappe.db = types.SimpleNamespace(
			exists=lambda *a, **k: True, sql=boom, set_single_value=lambda *a, **k: None
		)
		patch_mod.execute()
		# And a failure of materialize_defaults itself is swallowed, even if logging fails.
		h.frappe.log_error = mock.MagicMock(side_effect=RuntimeError("log down"))
		with mock.patch.object(patch_mod, "materialize_defaults", side_effect=RuntimeError("x")):
			patch_mod.execute()

	def test_it_is_registered_after_model_sync_with_its_readme_row(self):
		lines = PATCHES_TXT.read_text(encoding="utf-8").splitlines()
		name = "erpnext_enhancements.patches.backfill_weekly_summary_setting"
		self.assertEqual(lines.count(name), 1)
		self.assertIn("[post_model_sync]", lines[: lines.index(name)])
		self.assertNotIn("[pre_model_sync]", lines[lines.index("[post_model_sync]") :])
		self.assertIn(
			"# Planner UX redesign P9 (v1.715.0)", " ".join(lines[lines.index(name) - 5 : lines.index(name)])
		)
		rows = [
			l
			for l in PATCHES_README.read_text(encoding="utf-8").splitlines()
			if l.startswith("| `backfill_weekly_summary_setting` |")
		]
		self.assertEqual(len(rows), 1)
		self.assertIn("| post |", rows[0])

	def test_the_patch_reads_no_singles_row_through_db_get_value(self):
		"""``tabSingles`` has no ``creation`` column, so ``db.get_value("Singles", ...)`` cannot succeed
		(CLAUDE.md); the patch reads it not at all, and the Single's own helper reads it raw."""
		import ast

		tree = ast.parse(PATCH_PATH.read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "get_value":
				self.assertNotEqual(getattr(node.args[0], "value", None), "Singles")


class TestTheScheduler(unittest.TestCase):
	def test_the_hook_is_registered_exactly_once_under_a_key_used_nowhere_else(self):
		source = HOOKS.read_text(encoding="utf-8")
		entry = "planner_weekly_summary.send_weekly_summary"
		self.assertEqual(source.count(entry), 1)
		self.assertEqual(source.count('"15 6 * * 1":'), 1)
		self.assertIn(
			'"15 6 * * 1": ["erpnext_enhancements.project_enhancements.planner_weekly_summary.send_weekly_summary"],',
			source,
		)

	def test_the_suite_has_its_own_ci_step(self):
		ci = CI.read_text(encoding="utf-8")
		self.assertIn("run: python -m unittest erpnext_enhancements.tests.test_planner_weekly_summary -v", ci)


class TestTheModule(unittest.TestCase):
	def test_it_never_describes_a_draft_and_never_reads_the_drafts(self):
		source = (APP / "project_enhancements/planner_weekly_summary.py").read_text(encoding="utf-8")
		self.assertIn("overview.collect(draft=False, published_only=True,", source)

	def test_it_has_no_sql_function_string_in_get_all(self):
		source = (APP / "project_enhancements/planner_weekly_summary.py").read_text(encoding="utf-8")
		self.assertNotRegex(source, r'fields=\[[^\]]*"(?:count|sum|max|min)\(')

	def test_the_version_query_is_bound(self):
		source = (APP / "project_enhancements/planner_weekly_summary.py").read_text(encoding="utf-8")
		query = source[source.index("FROM `tabVersion` v") :].split(chr(34) * 3)[0]
		self.assertIn("v.creation >= %(since)s", query)
		self.assertIn("LIMIT %(limit)s", query)
		self.assertNotIn("{", query)


if __name__ == "__main__":
	unittest.main()
