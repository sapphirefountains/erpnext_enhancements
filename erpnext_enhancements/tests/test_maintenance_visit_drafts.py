# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Maintenance Planner's Draft mode and Publish, and pencilled visits (v1.601.0, C1).

A Google Calendar sync that pushes only committed work comes next, so the Maintenance Planner
needed the Project Planner's two ideas of "not committed yet": a **draft** (a change only its
planner can see, until Publish) and a **pencil** (a visit everyone can see, booked softly).

What each section pins, and how it would fail otherwise:

* **A draft never touches the visit.** ``move_visit(draft=1)`` and ``add_crew(draft=1)`` write one
  Planner Draft Change row per (planner, visit) and save nothing, so nobody gets a ToDo, a text or a
  customer email for a change that is still being tried. Re-drafting merges into the one row;
  drafting it back to what is stored leaves no draft; a draft of a visit somebody else changed
  since is not built on.
* **Each planner keeps to its own drafts.** Visit rows and Task rows share the doctype; the
  Maintenance Planner never reads, publishes or discards a Task row, and the Project Planner's
  query asks for Task rows only.
* **The overlay shows the drafts** on the calendar (moved, moved out of range, out of date) and
  hands the engine the drafted visits in place of the stored ones.
* **Publishing replays the live save**, all or nothing on the batch's conflicts, skips (and keeps)
  a draft of a visit changed since it was drafted (optimistic locking on ``base_modified``), marks
  the rows Published and tells each person once, never the publisher.
* **A pencilled visit is a pencil everywhere**: soft hours in the engine, never a reason to ask,
  no ToDo until it is firmed up (then everyone's at once), no customer date, no 6 AM text.

Bench-free: it reuses ``test_maintenance_visit_crews``' ``frappe`` stub (which runs the real
modules), adds what drafting needs on top, and restores ``sys.modules`` afterwards.

Run: python -m unittest erpnext_enhancements.tests.test_maintenance_visit_drafts
"""

import copy
import datetime
import importlib
import json
import unittest
from unittest import mock

from erpnext_enhancements.tests import test_maintenance_visit_crews as base

D = datetime.date
TODAY = base.TODAY  # a Monday
RECORD = base.RECORD
DRAFT = "Planner Draft Change"
AUSTIN, KORBEN, JESSE, LISA = base.AUSTIN, base.KORBEN, base.JESSE, base.LISA
STORED = "2026-10-12 09:00:00"
NIK = "nik@example.com"

frappe = planner = drafts = engine = visit_crew = confirmations = dispatch = None


def setUpModule():
	global frappe, planner, drafts, engine, visit_crew, confirmations, dispatch
	base.setUpModule()
	frappe = base.frappe
	planner, engine, visit_crew, dispatch = base.planner, base.engine, base.visit_crew, base.dispatch
	drafts = importlib.import_module("erpnext_enhancements.api.maintenance_drafts")
	confirmations = importlib.import_module(
		"erpnext_enhancements.project_enhancements.customer_confirmations"
	)


def tearDownModule():
	base.tearDownModule()


class _Rec(base._Doc):
	"""A visit record that, like the database, keeps what was saved: get_doc hands out copies."""

	def save(self):
		base._Doc.save(self)
		frappe.docs[(RECORD, self["name"])] = copy.deepcopy(self)


class _DraftRow(base._Doc):
	"""A Planner Draft Change: insert() and save() write through to ``frappe.tables``."""

	def insert(self, ignore_permissions=False):
		frappe.counter += 1
		self.setdefault("name", f"PDC-{frappe.counter}")
		self.setdefault("owner", frappe.session.user)
		self["creation"] = self["modified"] = f"2026-10-12 09:{frappe.counter:02d}:00"
		frappe.tables.setdefault(DRAFT, []).append(dict(self))
		return self

	def save(self):
		frappe.counter += 1
		self["modified"] = f"2026-10-12 09:{frappe.counter:02d}:30"
		rows = frappe.tables.setdefault(DRAFT, [])
		for index, row in enumerate(rows):
			if row.get("name") == self.get("name"):
				rows[index] = dict(self)
		return self


def _get_doc(doctype, name=None):
	if isinstance(doctype, dict):
		return _DraftRow(doctype)
	if doctype == DRAFT:
		row = next(r for r in frappe.tables.get(DRAFT, []) if r.get("name") == name)
		return _DraftRow(dict(row, doctype=DRAFT))
	if (doctype, name) not in frappe.docs:
		raise LookupError(f"{doctype} {name} not found")
	return copy.deepcopy(frappe.docs[(doctype, name)])


def _reset():
	base._reset()
	frappe.counter = 0
	frappe.tables[DRAFT] = []
	frappe.tables["Project"] = [{"name": "PRJ-1", "project_name": "Highlands Maintenance Contract"}]
	frappe.get_doc = _get_doc
	frappe.session.user = NIK
	frappe.roles = ["Maintenance Supervisor"]
	frappe.db.savepoint = lambda name: frappe.writes.append(("savepoint", name))
	frappe.db.rollback = lambda save_point=None: frappe.writes.append(("rollback", save_point))


def _visit(name="SMR-1", **values):
	"""A stored draft visit nobody has started, on TODAY + 2 unless said otherwise."""
	values.setdefault("scheduled_visit_date", TODAY + datetime.timedelta(days=2))
	values.setdefault("modified", STORED)
	doc = _Rec(base._record(name, **values))
	frappe.docs[(RECORD, name)] = doc
	return doc


def _rows(status=None):
	return [r for r in frappe.tables[DRAFT] if status is None or r.get("status") == status]


class _NoConflicts:
	def __init__(self, over=None):
		self.over = over or set()
		self.calls = []

	def __call__(self, user, day, ref, label=None, hours=None, full_day=False):
		self.calls.append(user)
		return {user: [f"{day}: Over"]} if user in self.over else {}


class _Base(unittest.TestCase):
	def setUp(self):
		_reset()
		self.conflicts = _NoConflicts()
		patcher = mock.patch.object(planner, "visit_conflicts", self.conflicts)
		patcher.start()
		self.addCleanup(patcher.stop)


# ---------------------------------------------------------------------- pure helpers


class TestPayload(unittest.TestCase):
	def test_merge_keeps_only_visit_keys_and_the_latest_value(self):
		merged = drafts.merge_payload(
			{"date": "2026-10-14", "start": "x"}, {"technician": "", "date": "2026-10-15"}
		)
		self.assertEqual(merged, {"date": "2026-10-15", "technician": ""})

	def test_an_unreadable_payload_is_an_empty_draft(self):
		self.assertEqual(drafts.load_payload("not json"), {})
		self.assertEqual(drafts.load_payload("[1, 2]"), {})
		self.assertEqual(drafts.load_payload('{"tentative": 1, "evil": 2}'), {"tentative": 1})

	def test_nobody_is_a_value_and_none_is_not(self):
		self.assertEqual(
			drafts.payload_kwargs({"technician": "", "date": None, "full_day": 0}),
			{"technician": "", "full_day": 0},
		)


# ---------------------------------------------------------------------- drafting


class TestDrafting(_Base):
	def test_a_drafted_move_saves_nothing_and_keeps_one_row(self):
		_visit(crew=[KORBEN])
		result = planner.move_visit("SMR-1", date="2026-10-15", modified=STORED, draft=1)
		self.assertTrue(result["draft_saved"])
		self.assertEqual(result["date"], "2026-10-15")
		self.assertEqual((frappe.saved, frappe.assigned, frappe.comments), ([], [], []))
		(row,) = _rows()
		self.assertEqual((row["visit"], row["status"], row["owner"]), ("SMR-1", "Draft", NIK))
		self.assertEqual(row["base_modified"], STORED)
		self.assertNotIn("task", row)
		self.assertEqual(json.loads(row["payload"]), {"date": "2026-10-15"})
		self.assertEqual(
			frappe.docs[(RECORD, "SMR-1")]["scheduled_visit_date"], TODAY + datetime.timedelta(days=2)
		)

	def test_re_drafting_merges_and_builds_on_the_draft(self):
		_visit(crew=[KORBEN])
		planner.move_visit("SMR-1", date="2026-10-15", modified=STORED, draft=1)
		result = planner.move_visit("SMR-1", technician=JESSE, modified=STORED, draft=1)
		self.assertEqual((result["date"], result["technician"]), ("2026-10-15", JESSE))
		(row,) = _rows()
		self.assertEqual(
			json.loads(row["payload"]),
			{"date": "2026-10-15", "technician": JESSE, "crew": [{"user": KORBEN, "hours": None}]},
		)
		self.assertEqual(frappe.saved, [])

	def test_drafting_back_to_what_is_stored_leaves_no_draft(self):
		_visit()
		planner.move_visit("SMR-1", date="2026-10-15", modified=STORED, draft=1)
		result = planner.move_visit("SMR-1", date="2026-10-14", modified=STORED, draft=1)
		self.assertFalse(result["in_drafts"])
		self.assertEqual([r["status"] for r in _rows()], ["Discarded"])

	def test_a_drafted_helper_joins_the_drafted_crew_only(self):
		_visit(crew=[KORBEN])
		frappe.tables[RECORD] = [{"name": "SMR-1"}]
		result = planner.add_crew("SMR-1", JESSE, modified=STORED, draft=1)
		self.assertEqual([c["user"] for c in result["crew"]], [KORBEN, JESSE])
		self.assertEqual(
			json.loads(_rows()[0]["payload"])["crew"],
			[{"user": KORBEN, "hours": None}, {"user": JESSE, "hours": None}],
		)
		self.assertEqual((frappe.saved, frappe.assigned), ([], []))

	def test_the_drafting_check_is_information_never_a_question(self):
		_visit(crew=[KORBEN])
		self.conflicts.over = {KORBEN}
		result = planner.move_visit("SMR-1", date="2026-10-15", modified=STORED, draft=1)
		self.assertNotIn("needs_reason", result)
		self.assertEqual(result["conflicts"], {KORBEN: ["2026-10-15: Over"]})
		self.assertIn("Not published yet.", result["warnings"][0])

	def test_a_stale_page_is_refused_as_a_live_move_is(self):
		_visit()
		with self.assertRaises(base._Throw):
			planner.move_visit("SMR-1", date="2026-10-15", modified="2026-10-01 00:00:00", draft=1)
		self.assertEqual(_rows(), [])

	def test_a_draft_of_a_visit_changed_since_is_not_built_on(self):
		_visit()
		planner.move_visit("SMR-1", date="2026-10-15", modified=STORED, draft=1)
		frappe.docs[(RECORD, "SMR-1")]["modified"] = "2026-10-12 11:00:00"  # someone saved it
		result = planner.move_visit("SMR-1", technician=JESSE, modified="2026-10-12 11:00:00", draft=1)
		self.assertIn("replaced", result["warnings"][0])
		(row,) = _rows("Draft")
		self.assertEqual(json.loads(row["payload"]), {"technician": JESSE, "crew": []})
		self.assertEqual(row["base_modified"], "2026-10-12 11:00:00")

	def test_new_visits_and_projected_moves_are_refused_in_draft_mode(self):
		with self.assertRaises(base._Throw) as caught:
			planner.move_projected("C-1", "2026-10-14", "2026-10-15", draft=1)
		self.assertIn("projected from its contract", str(caught.exception))
		actions = importlib.import_module("erpnext_enhancements.api.maintenance_actions")
		with self.assertRaises(base._Throw) as caught:
			actions.create_visit("PRJ-1", "2026-10-15", draft=1)
		self.assertIn("a new visit cannot be a draft", str(caught.exception))

	def test_draft_mode_waits_for_the_migrate(self):
		_visit()
		frappe.db.has_column = lambda doctype, column: column != "visit"
		with self.assertRaises(base._Throw):
			planner.move_visit("SMR-1", date="2026-10-15", modified=STORED, draft=1)
		self.assertFalse(drafts.has_open_draft("SMR-1"))


# ---------------------------------------------------------------------- the two planners' rows


class TestOwnRowsOnly(_Base):
	def test_the_maintenance_planner_never_touches_task_drafts(self):
		_visit()
		frappe.tables[DRAFT] = [
			{"name": "T", "task": "TASK-1", "visit": None, "status": "Draft", "owner": NIK, "modified": "1"},
			{"name": "V", "task": None, "visit": "SMR-1", "status": "Draft", "owner": NIK, "modified": "1"},
		]
		rows, _dups = drafts._draft_rows()
		self.assertEqual([r["name"] for r in rows], ["V"])
		self.assertEqual(drafts.discard_drafts(), {"discarded": 1})
		self.assertEqual(
			{r["name"]: r["status"] for r in frappe.tables[DRAFT]}, {"T": "Draft", "V": "Discarded"}
		)

	def test_the_project_planner_asks_for_task_rows_only(self):
		source = (base.APP / "api/project_planner.py").read_text(encoding="utf-8")
		at = source.index("def _draft_rows(user=None):")
		window = source[at : at + 2200]
		self.assertIn('"status": DRAFT, "task": ["is", "set"]', window)
		# v1.603.0: and its own New task rows, by kind; never a row that names a visit.
		self.assertIn('"status": DRAFT, "kind": NEW_TASK', window)
		self.assertNotIn('"visit"', window.split("def _has_kind_column")[0].split('"""', 2)[2])

	def test_a_row_names_one_task_or_one_visit(self):
		doctype = json.loads(
			(
				base.APP / "project_enhancements/doctype/planner_draft_change/planner_draft_change.json"
			).read_text(encoding="utf-8")
		)
		fields = {f["fieldname"]: f for f in doctype["fields"]}
		self.assertNotIn("reqd", fields["task"])
		self.assertEqual(fields["visit"]["options"], RECORD)
		self.assertGreaterEqual(doctype["modified"], "2026-10-12 20:00:00")
		controller = (
			base.APP / "project_enhancements/doctype/planner_draft_change/planner_draft_change.py"
		).read_text(encoding="utf-8")
		self.assertIn('bool(self.get("task")) == bool(self.get("visit"))', controller)
		hooks = (base.APP / "hooks.py").read_text(encoding="utf-8")
		self.assertIn('"Planner Draft Change"]', hooks[hooks.index("ignore_links_on_delete =") :][:120])

	def test_add_visits_undo_waits_for_a_draft_of_it(self):
		frappe.tables[DRAFT] = [{"name": "V", "visit": "SMR-1", "status": "Draft", "owner": LISA}]
		self.assertTrue(drafts.has_open_draft("SMR-1"))
		self.assertFalse(drafts.has_open_draft("SMR-2"))


# ---------------------------------------------------------------------- showing drafts


class TestOverlay(_Base):
	def _card(self, name, day):
		return {"kind": "visit", "name": name, "date": str(day), "crew": []}

	def test_drafted_cards_move_leave_the_range_or_go_stale(self):
		_visit("SMR-1")
		_visit("SMR-2")
		_visit("SMR-3")
		planner.move_visit("SMR-1", date="2026-10-15", modified=STORED, draft=1, tentative=1)
		planner.move_visit("SMR-2", date="2026-10-30", modified=STORED, draft=1)
		planner.move_visit("SMR-3", date="2026-10-16", modified=STORED, draft=1)
		frappe.docs[(RECORD, "SMR-3")]["modified"] = "2026-10-12 11:00:00"  # saved elsewhere since
		overlay = drafts.draft_overlay()
		self.assertEqual(sorted(overlay["docs"]), ["SMR-1", "SMR-2"])
		self.assertEqual(overlay["stale"][0]["visit"], "SMR-3")
		self.assertIn("Changed by someone else", overlay["stale"][0]["reason"])
		row = overlay["engine"]["SMR-1"]
		self.assertEqual((row["plan_date"], row["tentative"], row["technician"]), ("2026-10-15", 1, AUSTIN))
		day = TODAY + datetime.timedelta(days=2)
		visits, tray = drafts.with_drafts(
			[self._card("SMR-1", day), self._card("SMR-2", day), self._card("SMR-9", day)],
			[],
			overlay,
			TODAY,
			D(2026, 10, 18),
			TODAY,
			True,
		)
		by_name = {card["name"]: card for card in visits}
		self.assertEqual(sorted(by_name), ["SMR-1", "SMR-9"])  # SMR-2 was drafted out of the week
		self.assertEqual((by_name["SMR-1"]["date"], by_name["SMR-1"]["drafted"]), ("2026-10-15", True))
		self.assertTrue(by_name["SMR-1"]["tentative"])
		self.assertNotIn("drafted", by_name["SMR-9"])
		self.assertEqual(tray, [])
		self.assertEqual(
			drafts.summary(overlay)["count"], 3
		)  # two shown, one out of date: the bar offers to discard it

	def test_the_engine_reads_the_drafted_visit_instead_of_the_stored_one(self):
		stored = {
			"name": "SMR-1",
			"project": "PRJ-1",
			"visit_label": None,
			"technician": AUSTIN,
			"clock_in_time": None,
			"clock_out_time": None,
			"planned_hours": 0,
			"full_day": 0,
			"tentative": 0,
			"project_title": "Highlands",
			"plan_date": TODAY,
		}
		frappe.sql_handler = lambda query, values: [stored] if "plan_date BETWEEN" in query else []
		with mock.patch.multiple(planner, _projections=lambda *a: [], _decorate=lambda cards: None):
			plain = engine._read_visits([AUSTIN, KORBEN], TODAY, D(2026, 10, 16), base._settings())
			drafted = engine._read_visits(
				[AUSTIN, KORBEN],
				TODAY,
				D(2026, 10, 16),
				base._settings(),
				overrides={
					"SMR-1": dict(
						stored, plan_date="2026-10-14", tentative=1, crew=[(KORBEN, 3)], project_title="H"
					)
				},
			)
		self.assertEqual([(v["user"], v["date"]) for v in plain], [(AUSTIN, TODAY)])
		self.assertEqual(
			[(v["user"], str(v["date"]), v["hours"], v.get("tentative")) for v in drafted],
			[(AUSTIN, "2026-10-14", 2.0, True), (KORBEN, "2026-10-14", 3.0, True)],
		)


# ---------------------------------------------------------------------- publishing


class TestPublish(_Base):
	def setUp(self):
		super().setUp()
		self.notices = []
		notices = importlib.import_module("erpnext_enhancements.project_enhancements.planner_notices")
		patcher = mock.patch.object(
			notices,
			"send_publish_notices",
			lambda out, publisher=None, planner=None: self.notices.append((out, publisher, planner))
			or len(out),
		)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_publish_saves_through_the_live_path_and_tells_each_person_once(self):
		_visit(crew=[KORBEN])
		_visit("SMR-2", technician=JESSE)
		planner.move_visit("SMR-1", date="2026-10-15", modified=STORED, draft=1)
		planner.move_visit("SMR-2", technician=KORBEN, modified=STORED, draft=1)
		with mock.patch.object(drafts, "_batch_check", lambda plans: {}):
			result = drafts.publish_drafts()
		self.assertEqual([p["visit"] for p in result["published"]], ["SMR-1", "SMR-2"])
		self.assertEqual((result["skipped"], result["failed"]), ([], []))
		saved = {doc["name"]: doc for doc in frappe.saved}
		self.assertEqual(saved["SMR-1"]["scheduled_visit_date"], "2026-10-15")
		self.assertEqual(saved["SMR-2"]["technician"], KORBEN)
		# The live path's dispatch ToDo moved with the technician (maintenance_planner._move_assignment).
		self.assertIn(("remove", "SMR-2", JESSE), frappe.assigned)
		self.assertIn(("add", "SMR-2", KORBEN), frappe.assigned)
		self.assertEqual({r["status"] for r in _rows()}, {"Published"})
		self.assertFalse(frappe.flags.planner_publishing)
		((out, publisher, planner_name),) = self.notices
		self.assertEqual((publisher, planner_name), (NIK, "Maintenance Planner"))
		self.assertEqual(sorted(out), [AUSTIN, JESSE, KORBEN])
		self.assertEqual(out[AUSTIN]["doctype"], RECORD)
		self.assertIn("moved from", out[AUSTIN]["lines"][0]["text"])
		self.assertIn("You're off", out[JESSE]["lines"][0]["text"])

	def test_a_visit_changed_since_it_was_drafted_is_skipped_and_kept(self):
		_visit()
		planner.move_visit("SMR-1", date="2026-10-15", modified=STORED, draft=1)
		frappe.docs[(RECORD, "SMR-1")]["modified"] = "2026-10-12 11:00:00"
		with mock.patch.object(drafts, "_batch_check", lambda plans: {}):
			result = drafts.publish_drafts()
		self.assertEqual(result["published"], [])
		self.assertIn("Changed by someone else", result["skipped"][0]["reason"])
		self.assertEqual(frappe.saved, [])
		self.assertEqual([r["status"] for r in _rows()], ["Draft"])

	def test_batch_conflicts_are_all_or_nothing(self):
		_visit()
		_visit("SMR-2")
		planner.move_visit("SMR-1", date="2026-10-15", modified=STORED, draft=1)
		planner.move_visit("SMR-2", date="2026-10-15", modified=STORED, draft=1)
		over = {"Austin": ["2026-10-15: Over by 2h"]}
		with mock.patch.object(drafts, "_batch_check", lambda plans: over):
			asked = drafts.publish_drafts()
			self.assertEqual((asked["needs_reason"], asked["conflicts"], asked["count"]), (True, over, 2))
			self.assertEqual(frappe.saved, [])
			result = drafts.publish_drafts(reason="Busy week, Lisa approved")
		self.assertEqual(len(result["published"]), 2)
		self.assertEqual({r["reason"] for r in _rows()}, {"Busy week, Lisa approved"})

	def test_the_batch_check_judges_every_draft_together(self):
		_visit()
		planner.move_visit("SMR-1", date="2026-10-15", modified=STORED, draft=1)
		frappe.tables["Planner Resource"] = [{"name": "RES-A", "user": AUSTIN, "is_active": 1}]
		calls = []

		def availability(start, end, resources=None, google=True, visit_overrides=None):
			calls.append((str(start), str(end), resources, sorted(visit_overrides or {})))
			booking = {"kind": "visit", "ref": "SMR-1", "hours": 2}
			if visit_overrides:
				days = {"RES-A": {"2026-10-15": {"bookings": [booking], "conflicts": ["Over by 2h"]}}}
			else:
				days = {"RES-A": {"2026-10-14": {"bookings": [booking], "conflicts": []}}}
			return {"resources": [{"name": "RES-A", "label": "Austin"}], "days": days}

		with mock.patch.object(engine, "availability", availability):
			plans = [drafts._publish_plan(row)[0] for row in drafts._draft_rows()[0]]
			found = drafts._batch_check(plans)
		self.assertEqual(found, {"Austin": ["2026-10-15: Over by 2h"]})
		self.assertEqual(calls[0][:3], ("2026-10-14", "2026-10-15", ["RES-A"]))
		self.assertEqual(calls[1][3], ["SMR-1"])

	def test_a_visit_the_record_refuses_fails_alone(self):
		_visit()
		_visit("SMR-2")
		planner.move_visit("SMR-1", date="2026-10-15", modified=STORED, draft=1)
		planner.move_visit("SMR-2", date="2026-10-15", modified=STORED, draft=1)
		original = _Rec.save

		def refuse_two(doc):
			if doc["name"] == "SMR-2":
				raise base._Throw("The workflow says no")
			original(doc)

		with (
			mock.patch.object(_Rec, "save", refuse_two),
			mock.patch.object(drafts, "_batch_check", lambda p: {}),
		):
			result = drafts.publish_drafts()
		self.assertEqual([p["visit"] for p in result["published"]], ["SMR-1"])
		self.assertIn("The workflow says no", result["failed"][0]["reason"])
		self.assertIn(("rollback", "planner_publish_visit"), frappe.writes)
		self.assertEqual({r["visit"]: r["status"] for r in _rows()}, {"SMR-1": "Published", "SMR-2": "Draft"})


# ---------------------------------------------------------------------- pencil


class TestPencil(_Base):
	def test_a_pencil_never_asks_and_firming_up_checks_everyone(self):
		_visit(crew=[KORBEN])
		self.conflicts.over = {AUSTIN, KORBEN}
		result = planner.move_visit("SMR-1", tentative=1, modified=STORED)
		self.assertTrue(result["tentative"])
		self.assertEqual(self.conflicts.calls, [])
		result = planner.move_visit(
			"SMR-1", date="2026-10-16", modified=frappe.docs[(RECORD, "SMR-1")]["modified"]
		)
		self.assertNotIn("needs_reason", result)  # a pencil moves freely
		result = planner.move_visit("SMR-1", tentative=0, modified=frappe.docs[(RECORD, "SMR-1")]["modified"])
		self.assertTrue(result["needs_reason"])
		self.assertEqual(sorted(self.conflicts.calls), [AUSTIN, KORBEN])

	def test_a_pencil_handed_over_assigns_nobody_new(self):
		_visit(tentative=1)
		planner.move_visit("SMR-1", technician=JESSE, modified=STORED)
		self.assertEqual(frappe.assigned, [("remove", "SMR-1", AUSTIN)])

	def test_the_mirror_assigns_nobody_on_a_pencil_and_everyone_when_firmed_up(self):
		visit_crew.on_record_update(base._record(crew=[KORBEN, JESSE], tentative=1))
		self.assertEqual(frappe.assigned, [])
		before = base._record(crew=[KORBEN, JESSE], tentative=1)
		visit_crew.on_record_update(base._record(crew=[KORBEN, JESSE], tentative=0, before=before))
		self.assertEqual(
			frappe.assigned, [("add", "SMR-1", AUSTIN), ("add", "SMR-1", JESSE), ("add", "SMR-1", KORBEN)]
		)

	def test_pencilling_a_firm_visit_removes_nothing_but_leaving_a_pencil_does(self):
		base._todo("SMR-1", KORBEN)
		before = base._record(crew=[KORBEN], tentative=0)
		visit_crew.on_record_update(base._record(crew=[KORBEN], tentative=1, before=before))
		self.assertEqual(frappe.assigned, [])
		before = base._record(crew=[KORBEN], tentative=1)
		visit_crew.on_record_update(base._record(crew=[], tentative=1, before=before))
		self.assertEqual(frappe.assigned, [("remove", "SMR-1", KORBEN)])

	def test_a_pencil_has_no_customer_date_until_firmed_up(self):
		firm = base._record(scheduled_visit_date=TODAY)
		self.assertEqual(confirmations.visit_customer_date(firm), TODAY)
		self.assertIsNone(
			confirmations.visit_customer_date(base._record(scheduled_visit_date=TODAY, tentative=1))
		)
		# Firmed up: "changed" from no date to one, which is what queues the email.
		self.assertTrue(confirmations.needs_sending(None, TODAY, None, TODAY))
		# A record read before the field existed has no key at all: firm, as it always was.
		legacy = base._record(scheduled_visit_date=TODAY)
		legacy.pop("tentative", None)
		self.assertEqual(confirmations.visit_customer_date(legacy), TODAY)

	def test_the_engine_books_a_pencil_softly_for_everyone_on_it(self):
		row = {
			"name": "SMR-1",
			"project": "PRJ-1",
			"visit_label": None,
			"technician": AUSTIN,
			"clock_in_time": None,
			"clock_out_time": None,
			"planned_hours": 3,
			"full_day": 0,
			"tentative": 1,
			"project_title": "Highlands",
			"plan_date": TODAY,
		}
		queries = []

		def sql(query, values):
			queries.append(query)
			if "plan_date BETWEEN" in query:
				return [row]
			if "FROM `tabSapphire Visit Crew Member`" in query:
				return [{"parent": "SMR-1", "user": KORBEN, "hours": 0}]
			return []

		frappe.sql_handler = sql
		with mock.patch.multiple(planner, _projections=lambda *a: [], _decorate=lambda cards: None):
			got = engine._read_visits([AUSTIN, KORBEN], TODAY, TODAY, base._settings())
		self.assertEqual([(v["user"], v.get("tentative")) for v in got], [(AUSTIN, True), (KORBEN, True)])
		self.assertIn("r.tentative AS tentative", queries[0])
		# Before the migrate adds the column, every visit reads firm instead of the query failing.
		frappe.db.has_column = lambda doctype, column: False
		queries.clear()
		with mock.patch.multiple(planner, _projections=lambda *a: [], _decorate=lambda cards: None):
			engine._read_visits([AUSTIN], TODAY, TODAY, base._settings())
		self.assertIn("0 AS tentative", queries[0])

		people = [
			base._Doc(name="RES-A", user=AUSTIN, employee=None, resource_name="Austin"),
			base._Doc(name="RES-K", user=KORBEN, employee=None, resource_name="Korben"),
		]
		visits = [
			{
				"user": u,
				"date": TODAY,
				"ref": "SMR-1",
				"label": "Highlands",
				"project": "PRJ-1",
				"hours": 9,
				"slot": None,
				"estimated": False,
				"full_day": False,
				"tentative": True,
			}
			for u in (AUSTIN, KORBEN)
		]
		routed = []
		with (
			mock.patch.multiple(
				engine,
				get_settings=lambda: base._settings(default_day_hours=8),
				_read_resources=lambda names=None: (people, {"RES-A": [], "RES-K": []}),
				read_tasks=lambda start, end: [],
				read_crews=lambda names: ({}, {}),
				_read_holidays=lambda p, s, e: {},
				_read_time_off=lambda p, s, e: {},
				_read_restrictions=lambda p, s, e: {},
				_read_visits=lambda users, start, end, settings: [dict(v) for v in visits],
				_read_travel=lambda employees, start, end: [],
				_read_blocks=lambda p, s, e, x=(): {},
				pattern_hours=lambda patterns, day, default=8.0: 8.0,
			),
			mock.patch.object(
				engine.routing,
				"plan_routes",
				lambda day_bookings, settings, **k: routed.append(day_bookings) or {},
			),
		):
			data = engine.availability(TODAY, TODAY)
		cell = data["days"]["RES-A"][str(TODAY)]
		self.assertEqual((cell["booked"], cell["soft_booked"], cell["free"]), (0.0, 9.0, 8.0))
		self.assertEqual(cell["conflicts"], [])  # 9h of pencil on an 8h day is a warning, not a conflict
		self.assertTrue(any("Pencilled work" in w for w in cell["warnings"]))
		self.assertEqual(routed, [])  # a pencil is not driven to

	def test_the_six_am_text_leaves_a_pencil_out(self):
		frappe.singles[("ERPNext Enhancements Settings", "maintenance_dispatch_digests")] = 1
		frappe.tables[RECORD] = [
			{
				"name": "SMR-1",
				"technician": AUSTIN,
				"project": "PRJ-1",
				"scheduled_visit_date": TODAY,
				"docstatus": 0,
				"tentative": 1,
			},
			{
				"name": "SMR-2",
				"technician": KORBEN,
				"project": "PRJ-1",
				"scheduled_visit_date": TODAY,
				"docstatus": 0,
				"tentative": 0,
			},
		]
		sent = []
		with mock.patch.multiple(
			dispatch,
			_combined_digest_covers=lambda: set(),
			_stamp_digest=lambda stamp, today: None,
			_send_tech_digest=lambda person, visits, today: sent.append((person, [v.name for v in visits])),
		):
			dispatch.send_morning_digests()
		self.assertEqual(sent, [(KORBEN, ["SMR-2"])])

	def test_the_record_carries_the_pencil_field(self):
		path = base.DOCTYPES / "sapphire_maintenance_record" / "sapphire_maintenance_record.json"
		text = path.read_text(encoding="utf-8")
		self.assertFalse(text.endswith("\n"))  # the record JSON has no final newline (.editorconfig)
		doc = json.loads(text)
		order = doc["field_order"]
		self.assertEqual(order[order.index("full_day") + 1], "tentative")
		field = {f["fieldname"]: f for f in doc["fields"]}["tentative"]
		self.assertEqual(
			(field["fieldtype"], field["label"], field["default"]), ("Check", "Tentative (pencil)", "0")
		)
		self.assertGreaterEqual(doc["modified"], "2026-10-12 20:00:00")


# ---------------------------------------------------------------------- the page


class TestPage(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.code = (
			base.APP / "sapphire_maintenance/page/maintenance_planner/maintenance_planner.js"
		).read_text(encoding="utf-8")
		cls.block = cls.code[
			cls.code.index("// ====================================================================== C1:") :
		]

	def test_the_hooks_are_one_line_each(self):
		for line in (
			"this.init_phase_c1();",
			"Object.assign({ start: mp_ymd(start), end: mp_ymd(end) }, this.c1_draft_args())",
			"if (this.c1_refuse(method)) return Promise.resolve(null);",
			"args = this.c1_with_draft(method, args);",
			"this.render_phase_c1();",
			"Object.assign(MaintenancePlanner.prototype, MPC1_METHODS);",
		):
			self.assertIn(line, self.code)

	def test_draft_mode_sends_visit_writes_only_and_refuses_the_rest(self):
		self.assertIn('draft_methods: ["move_visit", "add_crew"]', self.block)
		self.assertIn("move_projected:", self.block)
		self.assertIn("create_visit:", self.block)
		# The page's refusal and the server's are the same sentences.
		self.assertIn(drafts.PROJECTED_REFUSAL, self.block)
		self.assertIn(drafts.DRAFT_REFUSAL, self.block)
		self.assertIn("method: `${MPC1.api}.publish_drafts`", self.block)
		self.assertIn("method: `${MPC1.api}.discard_drafts`", self.block)
		self.assertIn('pref: "ee_maintenance_planner_draft"', self.block)

	def test_pencil_is_a_menu_item_a_dialog_field_and_an_undoable_write(self):
		self.assertIn("this.apply(card, { tentative: 1 })", self.block)
		self.assertIn("this.apply(card, { tentative: 0 })", self.block)
		self.assertIn('fieldname: "tentative"', self.code)
		self.assertIn("before.tentative = card.tentative ? 1 : 0;", self.code)
		self.assertIn("args.tentative = snap.tentative;", self.code)
		self.assertIn('classes.push("mp-pencil")', self.code)
