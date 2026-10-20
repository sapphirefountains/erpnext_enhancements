"""Bench-free tests for My Work (WI-081 Phase 1): the rules, who may do what, auto-close, the
customer reminder fix, and the wiring that would otherwise fail in silence.

What each part guards, in the terms it fails in:

* **The rules** (``task_enhancements/assignments.py``). A ToDo dated the day it was made has no due
  date -- read the other way, 1,141 of production's 1,310 open ToDos showed as overdue. "Closed
  Won" and "Pending Review" are not finished, or a won Opportunity's "please create the project"
  closes itself. The triage never closes an unassigned ToDo for being unassigned, or Triton's live
  call follow-ups go with the junk.
* **Who may do what** (``api/my_work._abilities``). Every endpoint is reachable by any signed-in
  user, and v16's ToDo permission query is not applied for us.
* **Auto-close** (``task_enhancements/auto_close.py``) closes on the change INTO a finished state
  and never raises, so a record always saves.
* **The customer reminder** stops writing a closed follow-up again the next morning, and stops
  assigning to Administrator.
* **Wiring**: each finished doctype's ``on_change`` and the ``"*"`` ``on_cancel`` are in hooks.py,
  the email's Jinja global is registered, the page and its stylesheet exist, the global script is
  in the bundle, and the triage's ToDo field is a fixture.

Installs its own ``frappe`` stub in ``setUpModule``, so it has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_my_work -v
"""

import ast
import datetime
import html
import importlib
import json
import re
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
if str(REPO) not in sys.path:
	sys.path.insert(0, str(REPO))

RULES = "erpnext_enhancements.task_enhancements.assignments"
AUTO_CLOSE = "erpnext_enhancements.task_enhancements.auto_close"
TRIAGE = "erpnext_enhancements.task_enhancements.backlog_triage"
API = "erpnext_enhancements.api.my_work"
CUSTOMER = "erpnext_enhancements.script_migrations.customer"
STUBS = ("frappe", "frappe.utils")
_saved = {}

rules = auto_close = triage = api = customer = None
fake = None


class FakeDB:
	def __init__(self):
		self.values = {}
		self.set_calls = []
		self.sql_rows = []
		self.users = {}

	def get_value(self, doctype, name, field=None, *args, **kwargs):
		if doctype == "User":
			return self.users.get(name, {}).get(field)
		return self.values.get((doctype, name, field))

	def set_value(self, *args, **kwargs):
		self.set_calls.append((args, kwargs))

	def get_single_value(self, doctype, field):
		return self.values.get((doctype, None, field))

	def exists(self, doctype, filters=None):
		return False

	def sql(self, query, values=None, *args, **kwargs):
		self.last_sql = (query, values)
		return self.sql_rows

	def commit(self):
		pass

	def has_column(self, doctype, column):
		return True


class FakeToDo:
	def __init__(self, name, fail=False, **fields):
		self.name = name
		self.status = fields.pop("status", "Open")
		self.fields = fields
		self.fail = fail
		self.saved = []

	def get(self, key):
		return self.fields.get(key)

	@property
	def allocated_to(self):
		return self.fields.get("allocated_to")

	def save(self, ignore_permissions=False):
		if self.fail:
			raise RuntimeError("cannot save")
		self.saved.append(self.status)


class FakeFrappe(types.ModuleType):
	def __init__(self):
		super().__init__("frappe")
		self.flags = types.SimpleNamespace()
		self.db = FakeDB()
		self.todos = {}
		self.errors = []
		self.inserted = []
		self.all_rows = {}

	# decorators and translation
	def whitelist(self, *args, **kwargs):
		return lambda fn: fn

	def _(self, text, *args, **kwargs):
		return text

	# reads
	def get_all(self, doctype, filters=None, **kwargs):
		if doctype == "ToDo" and filters and "reference_type" in filters:
			names = [
				n
				for n, t in self.todos.items()
				if t.get("reference_type") == filters["reference_type"]
				and t.get("reference_name") == filters["reference_name"]
				and t.status == "Open"
			]
			return names
		return list(self.all_rows.get(doctype, []))

	def get_doc(self, doctype, name=None):
		if isinstance(doctype, dict):
			doc = types.SimpleNamespace(**doctype)
			doc.insert = lambda **kw: self.inserted.append(doctype)
			return doc
		return self.todos[name]

	def get_meta(self, doctype):
		return types.SimpleNamespace(issingle=False, get_field=lambda f: None)

	def log_error(self, title=None, message=None, **kwargs):
		self.errors.append(title)

	def get_traceback(self):
		return "traceback"


def _install_frappe():
	frappe = FakeFrappe()
	utils = types.ModuleType("frappe.utils")
	utils.getdate = lambda v=None: rules_as_date(v) if v else datetime.date(2026, 10, 20)
	utils.nowdate = lambda: "2026-10-20"
	utils.today = lambda: "2026-10-20"
	utils.now_datetime = lambda: datetime.datetime(2026, 10, 20, 9, 0)
	utils.add_to_date = lambda d, days=0, **k: d + datetime.timedelta(days=days)
	utils.add_days = lambda d, n: rules_as_date(d) + datetime.timedelta(days=n)
	utils.get_datetime = lambda v: datetime.datetime.combine(rules_as_date(v), datetime.time())
	utils.escape_html = lambda v: html.escape(str(v))
	utils.get_fullname = lambda u: u
	utils.cint = lambda v: int(v or 0)
	frappe.utils = utils
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	return frappe


def rules_as_date(value):
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	return datetime.date.fromisoformat(str(value)[:10])


def setUpModule():
	global rules, auto_close, triage, api, customer, fake
	for name in (*STUBS, RULES, AUTO_CLOSE, TRIAGE, API, CUSTOMER):
		_saved[name] = sys.modules.get(name)
		if name not in STUBS:
			sys.modules.pop(name, None)
	fake = _install_frappe()
	rules = importlib.import_module(RULES)
	auto_close = importlib.import_module(AUTO_CLOSE)
	triage = importlib.import_module(TRIAGE)
	api = importlib.import_module(API)
	customer = importlib.import_module(CUSTOMER)


def tearDownModule():
	for name, module in _saved.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


def _reset():
	fresh = FakeFrappe()
	fake.__dict__.update({k: v for k, v in fresh.__dict__.items() if k != "utils"})


D = datetime.date
DT = datetime.datetime
NOW = DT(2026, 10, 20, 9, 0)


# ---------------------------------------------------------------------------
# The rules


class TestDueDates(unittest.TestCase):
	def test_the_creation_day_stamp_is_no_due_date(self):
		"""assign_to.add writes nowdate() when nobody picked a date."""
		self.assertIsNone(rules.due_date("2026-10-01", DT(2026, 10, 1, 14, 3)))

	def test_a_chosen_date_is_a_due_date(self):
		self.assertEqual(rules.due_date("2026-10-23", DT(2026, 10, 1, 14, 3)), D(2026, 10, 23))
		self.assertEqual(rules.due_date(D(2026, 9, 30), "2026-10-01 08:00:00"), D(2026, 9, 30))

	def test_no_date_is_no_due_date(self):
		self.assertIsNone(rules.due_date(None, NOW))
		self.assertIsNone(rules.due_date("", NOW))

	def test_buckets(self):
		today = D(2026, 10, 20)
		self.assertEqual(rules.bucket(None, today), "undated")
		self.assertEqual(rules.bucket(D(2026, 10, 19), today), "overdue")
		self.assertEqual(rules.bucket(today, today), "today")
		self.assertEqual(rules.bucket(D(2026, 10, 27), today), "week")
		self.assertEqual(rules.bucket(D(2026, 10, 28), today), "later")

	def test_snoozing_takes_an_item_off_the_count(self):
		self.assertEqual(set(rules.ACTIONABLE), {"overdue", "today", "undated"})
		self.assertTrue(set(rules.ACTIONABLE) <= set(rules.BUCKETS))
		self.assertNotIn("week", rules.ACTIONABLE)


class TestTheAsk(unittest.TestCase):
	def test_the_dialogs_empty_comment_is_no_ask(self):
		self.assertEqual(rules.the_ask("Assignment for Task TASK-2026-00001", "Task", "TASK-2026-00001"), "")

	def test_text_editor_html_becomes_one_line(self):
		value = (
			'<div class="ql-editor read-mode"><p>Send revised quote</p><p>with the LED &amp; pump</p></div>'
		)
		self.assertEqual(rules.the_ask(value, "Opportunity", "X"), "Send revised quote with the LED & pump")

	def test_paragraphs_keep_their_breaks(self):
		value = '<div class="ql-editor read-mode"><p>First&nbsp;line</p><p>Second</p><br><p>Third</p></div>'
		self.assertEqual(rules.paragraphs(value), "First line\nSecond\n\nThird")

	def test_subject_leads_with_the_record_then_the_ask(self):
		self.assertEqual(
			rules.email_subject("Smith Residence", "Send quote", "Opportunity"), "Smith Residence: Send quote"
		)
		self.assertEqual(
			rules.email_subject("Smith Residence", "", "Opportunity"), "Smith Residence (Opportunity)"
		)
		self.assertEqual(rules.email_subject(None, "Call back Mike"), "Call back Mike")
		self.assertEqual(rules.email_subject(None, None), "New to-do")

	def test_subject_is_cut_at_a_word(self):
		subject = rules.email_subject("T", "word " * 60)
		self.assertLessEqual(len(subject), 120)
		self.assertTrue(subject.endswith("…"))
		self.assertNotIn("you", rules.email_subject("A", "B").lower())


class TestFinishedStates(unittest.TestCase):
	def test_both_spellings_of_canceled_and_invoiced_finish_a_task(self):
		for status in ("Completed", "Canceled", "Cancelled", "Invoiced"):
			self.assertTrue(rules.finished_value("Task", status), status)

	def test_work_that_is_still_owed_is_not_finished(self):
		self.assertFalse(rules.finished_value("Task", "Pending Review"))
		self.assertFalse(rules.finished_value("Opportunity", "Closed Won"))
		self.assertFalse(rules.finished_value("Material Request", "Ordered"))
		self.assertFalse(rules.finished_value("Unknown Doctype", "Closed"))

	def test_checks_and_docstatus_read_as_numbers(self):
		self.assertTrue(rules.finished_value("Customer", "1"))
		self.assertFalse(rules.finished_value("Customer", 0))
		self.assertTrue(rules.finished_value("Sapphire Maintenance Record", 1))
		self.assertFalse(rules.finished_value("Sapphire Maintenance Record", 0))

	def test_only_the_change_into_finished_counts(self):
		self.assertTrue(rules.just_finished("Task", "Open", "Completed"))
		self.assertTrue(rules.just_finished("Task", None, "Completed"))
		self.assertFalse(rules.just_finished("Task", "Completed", "Completed"))
		self.assertFalse(rules.just_finished("Task", "Completed", "Invoiced"))
		self.assertFalse(rules.just_finished("Task", "Open", "Working"))

	def test_the_task_literal_matches_the_other_two_copies(self):
		pattern = r"FINISHED_STATUSES = (\([^)]*\))"
		for path in (
			APP / "task_enhancements" / "doctype" / "task" / "task.py",
			APP / "project_enhancements" / "crew_availability.py",
		):
			with self.subTest(path=path.name):
				literal = ast.literal_eval(re.search(pattern, path.read_text()).group(1))
				self.assertEqual(literal, rules.TASK_FINISHED)

	def test_the_project_literal_matches_crew_availability(self):
		text = (APP / "project_enhancements" / "crew_availability.py").read_text()
		literal = ast.literal_eval(re.search(r"CLOSED_PROJECT_STATUSES = (\([^)]*\))", text).group(1))
		self.assertEqual(set(literal), set(rules.PROJECT_FINISHED))


class TestTriageRules(unittest.TestCase):
	def row(self, **kw):
		base = {
			"allocated_to": "nik@example.com",
			"assigned_by": "james@example.com",
			"reference_type": "Task",
			"reference_name": "TASK-1",
			"description": "Do the thing",
			"date": "2026-10-10",
			"creation": DT(2026, 10, 10, 8),
			"modified": DT(2026, 10, 10, 8),
			"ref": {"exists": True, "finished": False, "modified": DT(2026, 10, 15), "prospect": None},
		}
		base.update(kw)
		return base

	def test_recent_open_work_stays(self):
		self.assertIsNone(rules.triage_rule(self.row(), NOW))

	def test_a_deleted_record(self):
		self.assertEqual(rules.triage_rule(self.row(ref={"exists": False}), NOW), "record_missing")

	def test_a_record_that_finished_with_the_assignment_open(self):
		ref = {"exists": True, "finished": True, "modified": DT(2026, 10, 12), "prospect": None}
		self.assertEqual(rules.triage_rule(self.row(ref=ref), NOW), "record_finished")
		self.assertEqual(rules.TRIAGE_STATUS["record_finished"], "Closed")

	def test_an_assignment_made_after_the_record_finished_stays(self):
		ref = {"exists": True, "finished": True, "modified": DT(2026, 10, 1), "prospect": None}
		self.assertIsNone(rules.triage_rule(self.row(ref=ref), NOW))

	def test_administrator_and_guest(self):
		for who in ("Administrator", "Guest"):
			self.assertEqual(rules.triage_rule(self.row(allocated_to=who), NOW), "nobody_owns")

	def test_tritons_unassigned_follow_up_is_not_closed_for_being_unassigned(self):
		row = self.row(
			allocated_to=None, assigned_by=None, reference_type=None, reference_name=None, ref=None
		)
		self.assertIsNone(rules.triage_rule(row, NOW))

	def test_but_it_goes_when_nobody_has_touched_it_in_ninety_days(self):
		old = DT(2026, 6, 1)
		row = self.row(
			allocated_to=None,
			assigned_by=None,
			reference_type=None,
			reference_name=None,
			ref=None,
			creation=old,
			modified=old,
			date="2026-06-01",
		)
		self.assertEqual(rules.triage_rule(row, NOW), "stale")

	def test_stale_needs_the_record_untouched_too(self):
		old = DT(2026, 6, 1)
		busy = {"exists": True, "finished": False, "modified": DT(2026, 10, 18), "prospect": None}
		self.assertIsNone(
			rules.triage_rule(self.row(creation=old, modified=old, date="2026-06-01", ref=busy), NOW)
		)
		idle = dict(busy, modified=DT(2026, 5, 1))
		self.assertEqual(
			rules.triage_rule(self.row(creation=old, modified=old, date="2026-06-01", ref=idle), NOW), "stale"
		)

	def test_stale_never_closes_something_due_in_the_future(self):
		old = DT(2026, 6, 1)
		idle = {"exists": True, "finished": False, "modified": DT(2026, 5, 1), "prospect": None}
		self.assertIsNone(
			rules.triage_rule(self.row(creation=old, modified=old, date="2026-11-30", ref=idle), NOW)
		)

	def test_a_generated_follow_up_for_someone_no_longer_a_prospect(self):
		ref = {"exists": True, "finished": False, "modified": DT(2026, 10, 1), "prospect": False}
		row = self.row(
			reference_type="Customer", assigned_by=None, description="Follow up with Vestar (Vestar)", ref=ref
		)
		self.assertEqual(rules.triage_rule(row, NOW), "not_a_prospect")
		self.assertIsNone(rules.triage_rule(dict(row, assigned_by="brian@example.com"), NOW))
		self.assertIsNone(rules.triage_rule(dict(row, ref=dict(ref, prospect=True)), NOW))

	def test_every_rule_has_a_label_and_a_status(self):
		self.assertEqual(set(rules.TRIAGE_STATUS), set(triage.RULE_LABELS))
		self.assertEqual(set(rules.TRIAGE_STATUS.values()), {"Closed", "Cancelled"})


# ---------------------------------------------------------------------------
# Who may do what


class TestAbilities(unittest.TestCase):
	def row(self, **kw):
		base = {"allocated_to": "me", "assigned_by": "boss", "owner": "boss"}
		base.update(kw)
		return types.SimpleNamespace(**base)

	def test_the_assignee(self):
		can = api._abilities(self.row(), "me", False)
		self.assertTrue(can["complete"] and can["reschedule"] and can["reassign"] and can["reply"])
		self.assertFalse(can["nudge"] or can["withdraw"] or can["claim"])

	def test_whoever_assigned_it(self):
		can = api._abilities(self.row(allocated_to="crew"), "boss", False)
		self.assertTrue(can["reassign"] and can["nudge"] and can["withdraw"])
		self.assertFalse(can["complete"] or can["reschedule"] or can["reply"] or can["claim"])

	def test_a_stranger_can_do_nothing(self):
		can = api._abilities(self.row(allocated_to="crew"), "stranger", False)
		self.assertFalse(any(can.values()))

	def test_a_system_manager_may_reschedule_reassign_and_withdraw_but_not_complete(self):
		can = api._abilities(self.row(allocated_to="crew"), "admin", True)
		self.assertTrue(can["reschedule"] and can["reassign"] and can["withdraw"])
		self.assertFalse(can["complete"] or can["nudge"])

	def test_unclaimed_can_only_be_claimed(self):
		can = api._abilities(self.row(allocated_to=None, assigned_by=None, owner="triton"), "me", False)
		self.assertTrue(can["claim"])
		self.assertFalse(can["withdraw"] or can["nudge"] or can["complete"] or can["reply"])

	def test_no_reply_to_yourself(self):
		can = api._abilities(self.row(assigned_by="me", owner="me"), "me", False)
		self.assertFalse(can["reply"])

	def test_the_owner_stands_in_when_nobody_is_named_as_assigner(self):
		can = api._abilities(self.row(allocated_to="crew", assigned_by=None, owner="boss"), "boss", False)
		self.assertTrue(can["nudge"] and can["withdraw"])


# ---------------------------------------------------------------------------
# Auto-close


class Doc:
	def __init__(self, doctype, name, before, **fields):
		self.doctype = doctype
		self.name = name
		self.fields = fields
		self._before = before

	def get(self, key):
		return self.fields.get(key)

	def is_new(self):
		return False

	def get_doc_before_save(self):
		return Doc(self.doctype, self.name, None, **self._before) if self._before is not None else None


class TestAutoClose(unittest.TestCase):
	def setUp(self):
		_reset()
		fake.todos = {
			"T1": FakeToDo("T1", reference_type="Task", reference_name="TASK-1", allocated_to="a"),
			"T2": FakeToDo("T2", reference_type="Task", reference_name="TASK-1", allocated_to="b"),
			"T3": FakeToDo("T3", reference_type="Task", reference_name="TASK-2", allocated_to="a"),
		}

	def test_a_task_moving_to_completed_closes_its_assignments(self):
		auto_close.on_change(Doc("Task", "TASK-1", {"status": "Working"}, status="Completed"))
		self.assertEqual(fake.todos["T1"].saved, ["Closed"])
		self.assertEqual(fake.todos["T2"].saved, ["Closed"])
		self.assertEqual(fake.todos["T3"].saved, [])

	def test_resaving_a_finished_task_closes_nothing(self):
		auto_close.on_change(Doc("Task", "TASK-1", {"status": "Completed"}, status="Completed"))
		self.assertEqual(fake.todos["T1"].saved, [])

	def test_a_won_opportunity_keeps_its_assignments(self):
		fake.todos["T1"].fields["reference_type"] = "Opportunity"
		auto_close.on_change(
			Doc("Opportunity", "TASK-1", {"status": "Negotiation/Review"}, status="Closed Won")
		)
		self.assertEqual(fake.todos["T1"].saved, [])

	def test_imports_are_left_alone(self):
		fake.flags.in_import = True
		auto_close.on_change(Doc("Task", "TASK-1", {"status": "Working"}, status="Completed"))
		self.assertEqual(fake.todos["T1"].saved, [])

	def test_one_failure_is_logged_and_the_rest_still_close(self):
		fake.todos["T1"].fail = True
		auto_close.on_change(Doc("Task", "TASK-1", {"status": "Working"}, status="Completed"))
		self.assertEqual(fake.todos["T2"].saved, ["Closed"])
		self.assertTrue(fake.errors)

	def test_a_cancelled_document_cancels_its_assignments(self):
		auto_close.on_cancel(Doc("Task", "TASK-1", None))
		self.assertEqual(fake.todos["T1"].saved, ["Cancelled"])

	def test_cancelling_a_todo_is_not_recursive(self):
		auto_close.on_cancel(Doc("ToDo", "T1", None))
		self.assertEqual(fake.todos["T1"].saved, [])


# ---------------------------------------------------------------------------
# The customer reminder


class TestCustomerReminder(unittest.TestCase):
	def setUp(self):
		_reset()
		fake.db.values[("Sales Activity Settings", None, "inactivity_threshold")] = 90
		fake.db.users = {"brian@example.com": {"enabled": 1}, "gone@example.com": {"enabled": 0}}

	def prospect(self, **kw):
		base = {
			"name": "CUST-1",
			"customer_name": "Vestar",
			"custom_last_activity_date": D(2026, 6, 1),
			"custom_reminder_days": 0,
			"custom_reminder_assignee": "brian@example.com",
			"owner": "Administrator",
		}
		base.update(kw)
		return types.SimpleNamespace(**base)

	def run_reminder(self, *customers):
		fake.all_rows["Customer"] = list(customers)
		customer.customer_inactivity_reminder()
		return fake.inserted

	def test_a_due_prospect_gets_a_follow_up(self):
		inserted = self.run_reminder(self.prospect())
		self.assertEqual([d["allocated_to"] for d in inserted], ["brian@example.com"])

	def test_administrator_gets_nothing(self):
		self.assertEqual(self.run_reminder(self.prospect(custom_reminder_assignee=None)), [])

	def test_a_disabled_user_gets_nothing(self):
		self.assertEqual(self.run_reminder(self.prospect(custom_reminder_assignee="gone@example.com")), [])

	def test_closing_the_last_follow_up_restarts_the_window(self):
		fake.db.sql_rows = [("CUST-1", DT(2026, 10, 19, 16, 0))]
		self.assertEqual(self.run_reminder(self.prospect()), [])

	def test_an_old_closed_follow_up_does_not_hold_it_back(self):
		fake.db.sql_rows = [("CUST-1", DT(2026, 5, 1))]
		self.assertEqual(len(self.run_reminder(self.prospect())), 1)


# ---------------------------------------------------------------------------
# Wiring


def _doc_events():
	tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
	for node in tree.body:
		if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "doc_events":
			return ast.literal_eval(node.value)
	raise AssertionError("no doc_events in hooks.py")


class TestWiring(unittest.TestCase):
	HANDLER = "erpnext_enhancements.task_enhancements.auto_close.on_change"

	def test_every_finished_doctype_runs_auto_close_on_change(self):
		events = _doc_events()
		for doctype in rules.FINISHED_STATES:
			with self.subTest(doctype=doctype):
				handlers = events.get(doctype, {}).get("on_change")
				handlers = [handlers] if isinstance(handlers, str) else (handlers or [])
				self.assertIn(self.HANDLER, handlers)

	def test_cancel_is_wired_for_every_doctype(self):
		handlers = _doc_events()["*"].get("on_cancel")
		handlers = [handlers] if isinstance(handlers, str) else handlers
		self.assertIn("erpnext_enhancements.task_enhancements.auto_close.on_cancel", handlers)

	def test_the_email_global_is_registered_and_used(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		self.assertIn('"erpnext_enhancements.email_style.ee_assignment"', hooks)
		records = json.loads((APP / "fixtures" / "notification.json").read_text(encoding="utf-8"))
		todo = [r for r in records if r.get("document_type") == "ToDo"]
		self.assertEqual(len(todo), 1)
		self.assertIn("ee_assignment(doc)", todo[0]["subject"])
		self.assertIn("ee_assignment(doc)", todo[0]["message"])
		self.assertNotIn("Open the to-do", todo[0]["message"])
		# ee_prose escapes what it is given: the raw Text Editor HTML printed its tags.
		self.assertNotIn("ee_prose(doc.description)", todo[0]["message"])

	def test_the_page_exists_with_its_stylesheet(self):
		folder = APP / "task_enhancements" / "page" / "my_work"
		page = json.loads((folder / "my_work.json").read_text())
		self.assertEqual(
			(page["name"], page["title"], page["module"]), ("my-work", "My Work", "Task Enhancements")
		)
		self.assertTrue((folder / "my_work.js").exists())
		self.assertTrue((folder / "my_work.css").exists())
		self.assertTrue((folder / "__init__.py").exists())
		self.assertTrue((APP / "task_enhancements" / "page" / "__init__.py").exists())

	def test_the_global_script_and_its_styles_are_bundled(self):
		bundle = (APP / "public" / "js" / "erpnext_enhancements.bundle.js").read_text()
		self.assertIn('import "./global_enhancements/my_work.js";', bundle)
		scss = (APP / "public" / "css" / "desk_addons.bundle.scss").read_text()
		self.assertIn('@import "./global_enhancements/my_work";', scss)

	def test_the_sidebar_count_never_takes_the_bells_class(self):
		"""Notifications.update_count_badge writes into EVERY .notification-count."""
		js = (APP / "public" / "js" / "global_enhancements" / "my_work.js").read_text()
		code = re.sub(r"(?s)/\*.*?\*/", "", js)
		code = re.sub(r"(?m)^\s*//.*$", "", code)
		self.assertNotIn("notification-count", code)

	def test_the_triage_field_is_a_fixture_and_on_the_form(self):
		fields = json.loads((APP / "fixtures" / "custom_field.json").read_text(encoding="utf-8"))
		field = [f for f in fields if f["name"] == "ToDo-custom_triage_batch"]
		self.assertEqual(len(field), 1)
		self.assertEqual(field[0]["fieldname"], triage.FIELD)
		setters = json.loads((APP / "fixtures" / "property_setter.json").read_text(encoding="utf-8"))
		order = next(s for s in setters if s["name"] == "ToDo-main-field_order")
		self.assertIn(triage.FIELD, json.loads(order["value"]))

	def test_reads_are_get_and_writes_are_post(self):
		source = (APP / "api" / "my_work.py").read_text()
		decorated = re.findall(r'@frappe\.whitelist\(methods=\["(GET|POST)"\]\)\ndef (\w+)', source)
		methods = dict((name, verb) for verb, name in decorated)
		for name in ("get_items", "get_count", "get_record_assignments", "triage_preview", "triage_batches"):
			self.assertEqual(methods.get(name), "GET", name)
		for name in (
			"complete",
			"reschedule",
			"reassign",
			"reply",
			"claim",
			"nudge",
			"withdraw",
			"triage_apply",
			"triage_undo",
		):
			self.assertEqual(methods.get(name), "POST", name)
		self.assertEqual(len(re.findall(r"@frappe\.whitelist\(", source)), len(decorated))


if __name__ == "__main__":
	unittest.main()
