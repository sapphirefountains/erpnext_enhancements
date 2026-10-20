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
ALERTS = "erpnext_enhancements.task_enhancements.assignment_alerts"
RECURRENCE = "erpnext_enhancements.task_enhancements.recurrence"
NOTIFICATION_LOG = "frappe.desk.doctype.notification_log.notification_log"
PLANNER_NOTICES = "erpnext_enhancements.project_enhancements.planner_notices"
TELEPHONY = "erpnext_enhancements.api.telephony"
STUBS = (
	"frappe",
	"frappe.utils",
	"frappe.desk",
	"frappe.desk.doctype",
	"frappe.desk.doctype.notification_log",
	NOTIFICATION_LOG,
	PLANNER_NOTICES,
	TELEPHONY,
)
_saved = {}

rules = auto_close = triage = api = customer = alerts = recurrence = None
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


class Flags(dict):
	"""frappe._dict as Document.flags: read as attributes or with .get."""

	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class FakeToDo:
	def __init__(self, name, fail=False, **fields):
		self.name = name
		self.status = fields.pop("status", "Open")
		self.fields = fields
		self.fail = fail
		self.saved = []
		self.flags = types.SimpleNamespace()

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
		self.conf = {}
		self.enqueued = []
		self.bells = []
		self.texts = []
		self.cells = {}

	def parse_json(self, value):
		return json.loads(value) if isinstance(value, str) else value

	def enqueue(self, method, **kwargs):
		self.enqueued.append((method, kwargs))

	def bold(self, text):
		return f"<b>{text}</b>"

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
		rows = list(self.all_rows.get(doctype, []))
		wanted = (filters or {}).get("name") if isinstance(filters, dict) else None
		if isinstance(wanted, list) and wanted[:1] == ["in"]:
			rows = [r for r in rows if getattr(r, "name", None) in wanted[1]]
		return rows

	def get_doc(self, doctype, name=None):
		if isinstance(doctype, dict):
			doc = types.SimpleNamespace(**doctype)
			doc.flags = Flags()
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
	utils.get_url = lambda path="": "https://erp.test" + path
	utils.get_url_to_form = lambda dt, dn: f"https://erp.test/desk/{dt.lower().replace(' ', '-')}/{dn}"
	utils.formatdate = lambda d: str(d)
	frappe.utils = utils
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	for name in ("frappe.desk", "frappe.desk.doctype", "frappe.desk.doctype.notification_log"):
		sys.modules[name] = types.ModuleType(name)
	log = types.ModuleType(NOTIFICATION_LOG)
	log.get_title = lambda doctype, name: f"Title of {name}"
	log.enqueue_create_notification = lambda user, doc: sys.modules["frappe"].bells.append((user, doc))
	sys.modules[NOTIFICATION_LOG] = log
	notices = types.ModuleType(PLANNER_NOTICES)
	notices.cell_number = lambda user: sys.modules["frappe"].cells.get(user)
	sys.modules[PLANNER_NOTICES] = notices
	telephony = types.ModuleType(TELEPHONY)
	telephony.send_system_sms = lambda number, message: sys.modules["frappe"].texts.append((number, message))
	sys.modules[TELEPHONY] = telephony
	return frappe


def rules_as_date(value):
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	return datetime.date.fromisoformat(str(value)[:10])


def setUpModule():
	global rules, auto_close, triage, api, customer, alerts, recurrence, fake
	for name in (*STUBS, RULES, AUTO_CLOSE, TRIAGE, API, CUSTOMER, ALERTS, RECURRENCE):
		_saved[name] = sys.modules.get(name)
		if name not in STUBS:
			sys.modules.pop(name, None)
	fake = _install_frappe()
	rules = importlib.import_module(RULES)
	auto_close = importlib.import_module(AUTO_CLOSE)
	triage = importlib.import_module(TRIAGE)
	api = importlib.import_module(API)
	customer = importlib.import_module(CUSTOMER)
	alerts = importlib.import_module(ALERTS)
	recurrence = importlib.import_module(RECURRENCE)


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


# ---------------------------------------------------------------------------
# Phase 2: alerts


class Row(dict):
	"""frappe._dict: a ToDo row read both as row.field and row.get("field")."""

	__getattr__ = dict.get


TODAY = D(2026, 10, 20)  # a Tuesday


def todo(name, **kw):
	base = {
		"name": name,
		"allocated_to": "crew@x",
		"assigned_by": "boss@x",
		"owner": "boss@x",
		"date": "2026-10-01",
		"creation": DT(2026, 10, 1, 9),
		"description": "Do it",
		"reference_type": "Task",
		"reference_name": name,
		"priority": "Medium",
		"custom_escalated_on": None,
	}
	base.update(kw)
	return Row(base)


class TestAlertRules(unittest.TestCase):
	def test_escalation_needs_a_real_date_two_days_gone(self):
		self.assertTrue(rules.escalation_due(todo("A", date="2026-10-18"), TODAY))
		self.assertFalse(rules.escalation_due(todo("B", date="2026-10-19"), TODAY))
		# the creation-day stamp is not a promise anyone made
		self.assertFalse(rules.escalation_due(todo("C", date="2026-10-01"), TODAY))

	def test_escalation_is_said_once_and_only_to_someone_else(self):
		self.assertFalse(
			rules.escalation_due(todo("A", date="2026-10-18", custom_escalated_on="2026-10-19"), TODAY)
		)
		self.assertFalse(rules.escalation_due(todo("A", date="2026-10-18", assigned_by="crew@x"), TODAY))
		self.assertFalse(rules.escalation_due(todo("A", date="2026-10-18", assigned_by=None), TODAY))

	def test_due_today(self):
		self.assertTrue(rules.due_today(todo("A", date="2026-10-20"), TODAY))
		self.assertFalse(rules.due_today(todo("B", date="2026-10-20", creation=DT(2026, 10, 20, 8)), TODAY))

	def test_the_standing_pile_alone_sends_nothing(self):
		self.assertFalse(
			rules.digest_worth_sending({"overdue": [], "today": [], "new": [], "waiting": [], "undated": 40})
		)
		self.assertTrue(rules.digest_worth_sending({"new": [1]}))

	def test_the_text_keeps_its_link_and_fits(self):
		url = "https://erp.test/desk/opportunity/CRM-OPP-2026-00187"
		text = rules.sms_text("James Harris", "Smith Residence", "word " * 200, url)
		self.assertLessEqual(len(text), rules.SMS_LIMIT)
		self.assertTrue(text.endswith(url))
		self.assertTrue(text.startswith("High priority from James Harris: Smith Residence - "))
		self.assertEqual(rules.sms_text(None, "T", "T", None), "High priority: T")


class TestDigest(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_sections(self):
		since = DT(2026, 10, 19, 9)
		rows = [
			todo("late", allocated_to="me", date="2026-10-15"),
			todo("today", allocated_to="me", date="2026-10-20"),
			todo("new", allocated_to="me", date="2026-10-19", creation=DT(2026, 10, 19, 15)),
			todo("pile", allocated_to="me"),
			todo("theirs", allocated_to="crew@x", assigned_by="me", date="2026-10-10"),
			todo("theirs_fine", allocated_to="crew@x", assigned_by="me", date="2026-10-30"),
		]
		got = alerts.digest_sections("me", rows, TODAY, since)
		self.assertEqual([r.name for r in got["overdue"]], ["late"])
		self.assertEqual([r.name for r in got["today"]], ["today"])
		self.assertEqual([r.name for r in got["new"]], ["new"])
		self.assertEqual([r.name for r in got["waiting"]], ["theirs"])
		self.assertEqual(got["undated"], 1)

	def test_monday_covers_the_weekend(self):
		since = alerts._since(D(2026, 10, 19))
		self.assertEqual(since, DT(2026, 10, 17, 9, 0))

	def test_record_titles_cannot_inject_markdown(self):
		row = todo("X", reference_name="](javascript:alert(1))", date="2026-10-15", allocated_to="me")
		md = alerts.digest_markdown(
			{"overdue": [row]}, {("Task", row.reference_name): "Evil](javascript:x)"}, TODAY
		)
		self.assertIn("Evil\\]\\(javascript:x\\)", md)
		self.assertIn("5 days late", md)

	def test_briefing_readers_do_not_get_it_twice(self):
		fake.db.values[("ERPNext Enhancements Settings", None, "briefing_enabled")] = 1
		fake.all_rows["Briefing Recipient"] = ["me"]
		fake.all_rows["User"] = [Row(name="me", email="me@x", full_name="Me")]
		sent = []
		alerts._send_digest = lambda person, *a: sent.append(person.name)
		try:
			alerts.send_digests([todo("late", allocated_to="me", date="2026-10-15")], TODAY)
		finally:
			importlib.reload(alerts)
		self.assertEqual(sent, [])


class TestEscalateAndNudge(unittest.TestCase):
	def setUp(self):
		_reset()
		fake.all_rows["User"] = [
			Row(name="crew@x", email="crew@x", full_name="Crew Person"),
			Row(name="boss@x", email="boss@x", full_name="Boss Person"),
		]

	def test_escalation_tells_the_assigner_and_stamps_the_row(self):
		sent = alerts.escalate([todo("A", date="2026-10-17"), todo("B", date="2026-10-19")], TODAY)
		self.assertEqual(sent, 1)
		self.assertEqual(fake.bells[0][0], "boss@x")
		self.assertIn("3 days late", fake.bells[0][1]["subject"])
		self.assertEqual(fake.db.set_calls[0][0], ("ToDo", "A", "custom_escalated_on", "2026-10-20"))
		self.assertEqual(fake.db.set_calls[0][1], {"update_modified": False})

	def test_due_today_bells_the_assignee(self):
		sent = alerts.nudge_due_today([todo("A", date="2026-10-20"), todo("B", date="2026-10-21")], TODAY)
		self.assertEqual(sent, 1)
		self.assertEqual(fake.bells[0][0], "crew@x")
		self.assertIn("Due today", fake.bells[0][1]["subject"])

	def test_a_kill_switch_skips_its_step(self):
		fake.conf["my_work_nudge_disabled"] = 1
		fake.conf["my_work_escalation_disabled"] = 1
		fake.conf["my_work_digest_disabled"] = 1
		fake.all_rows["ToDo"] = [todo("A", date="2026-10-20")]
		alerts.daily_run()
		self.assertEqual(fake.bells, [])


class TestHighPriorityText(unittest.TestCase):
	def setUp(self):
		_reset()

	def doc(self, **kw):
		base = {
			"name": "T1",
			"priority": "High",
			"allocated_to": "crew@x",
			"assigned_by": "boss@x",
			"owner": "boss@x",
		}
		base.update(kw)
		return Row(base)

	def test_high_from_someone_else_queues_after_commit(self):
		alerts.on_todo_insert(self.doc())
		self.assertEqual(len(fake.enqueued), 1)
		self.assertTrue(fake.enqueued[0][1]["enqueue_after_commit"])

	def test_nothing_else_is_texted(self):
		for doc in (
			self.doc(priority="Medium"),
			self.doc(assigned_by="crew@x"),
			self.doc(assigned_by=None, owner="crew@x"),
			self.doc(allocated_to="Administrator"),
			self.doc(allocated_to=None),
		):
			alerts.on_todo_insert(doc)
		self.assertEqual(fake.enqueued, [])
		fake.conf["my_work_sms_disabled"] = 1
		alerts.on_todo_insert(self.doc())
		self.assertEqual(fake.enqueued, [])

	def test_the_job_texts_the_cell_number(self):
		fake.db.exists = lambda *a, **k: True
		row = Row(todo("T1", status="Open", priority="High", reference_name="TASK-9"))
		fake.db.get_value = lambda dt, name, field=None, **k: row if dt == "ToDo" else "Boss Person"
		fake.cells["crew@x"] = "+18015550100"
		self.assertTrue(alerts.send_high_priority_text("T1"))
		number, message = fake.texts[0]
		self.assertEqual(number, "+18015550100")
		self.assertIn("High priority from Boss Person: Title of TASK-9 - Do it", message)
		self.assertTrue(message.endswith("https://erp.test/desk/task/TASK-9"))

	def test_no_cell_number_no_text(self):
		fake.db.exists = lambda *a, **k: True
		fake.db.get_value = lambda dt, name, field=None, **k: (
			Row(todo("T1", status="Open")) if dt == "ToDo" else None
		)
		self.assertFalse(alerts.send_high_priority_text("T1"))
		self.assertEqual(fake.texts, [])


class TestPhaseTwoWiring(unittest.TestCase):
	def hooks(self):
		tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
		out = {}
		for node in tree.body:
			if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
				try:
					out[node.targets[0].id] = ast.literal_eval(node.value)
				except ValueError:
					pass
		return out

	def test_frappes_assignment_emails_are_off_and_the_morning_job_runs(self):
		hooks = self.hooks()
		self.assertIn("Assignment", hooks["notification_skip_email_types"])
		self.assertIn(
			"erpnext_enhancements.task_enhancements.assignment_alerts.daily_run",
			hooks["scheduler_events"]["cron"]["40 6 * * 1-5"],
		)
		self.assertEqual(
			hooks["doc_events"]["ToDo"]["after_insert"],
			"erpnext_enhancements.task_enhancements.assignment_alerts.on_todo_insert",
		)

	def test_the_assignment_email_is_for_high_priority_only(self):
		records = json.loads((APP / "fixtures" / "notification.json").read_text(encoding="utf-8"))
		todo_mail = next(r for r in records if r.get("document_type") == "ToDo")
		self.assertEqual(todo_mail.get("condition"), 'doc.priority == "High"')

	def test_the_escalation_field_is_a_fixture_and_on_the_form(self):
		fields = json.loads((APP / "fixtures" / "custom_field.json").read_text(encoding="utf-8"))
		self.assertTrue(any(f["name"] == "ToDo-custom_escalated_on" for f in fields))
		setters = json.loads((APP / "fixtures" / "property_setter.json").read_text(encoding="utf-8"))
		order = next(s for s in setters if s["name"] == "ToDo-main-field_order")
		self.assertIn(alerts.FIELD_ESCALATED, json.loads(order["value"]))

	def test_the_new_reads_are_get(self):
		source = (APP / "api" / "my_work.py").read_text()
		for name in ("get_workload", "get_record_overview"):
			self.assertRegex(source, rf'@frappe\.whitelist\(methods=\["GET"\]\)\ndef {name}\(')


# ---------------------------------------------------------------------------
# Phase 3: teams, repeats, checklists


class TestRepeatRules(unittest.TestCase):
	def test_counted_from_the_due_date_to_the_first_one_after_today(self):
		monday = D(2026, 10, 19)
		self.assertEqual(rules.next_occurrence(monday, D(2026, 10, 21), "Weekly"), D(2026, 10, 26))
		# finished weeks late: comes back on the next Monday, not already overdue
		self.assertEqual(rules.next_occurrence(D(2026, 9, 7), D(2026, 10, 21), "Weekly"), D(2026, 10, 26))
		self.assertEqual(rules.next_occurrence(monday, D(2026, 10, 19), "Every 2 weeks"), D(2026, 11, 2))

	def test_never_due_the_day_it_is_made(self):
		"""A step landing on today would be dated its creation day, which reads as no due date."""
		self.assertEqual(rules.next_occurrence(D(2026, 10, 13), D(2026, 10, 20), "Weekly"), D(2026, 10, 27))
		self.assertEqual(rules.next_occurrence(D(2026, 10, 19), D(2026, 10, 20), "Daily"), D(2026, 10, 21))

	def test_months_keep_their_day(self):
		self.assertEqual(rules.next_occurrence(D(2026, 1, 31), D(2026, 2, 1), "Monthly"), D(2026, 2, 28))
		# counted from the first due date, so March is the 31st again, not the 28th
		self.assertEqual(rules.next_occurrence(D(2026, 1, 31), D(2026, 3, 1), "Monthly"), D(2026, 3, 31))
		self.assertEqual(rules.next_occurrence(D(2026, 10, 1), D(2026, 10, 2), "Quarterly"), D(2027, 1, 1))
		self.assertEqual(rules.add_months(D(2024, 2, 29), 12), D(2025, 2, 28))

	def test_no_due_date_is_one_step_from_today_and_unknown_is_none(self):
		self.assertEqual(rules.next_occurrence(None, D(2026, 10, 20), "Daily"), D(2026, 10, 21))
		self.assertIsNone(rules.next_occurrence(D(2026, 10, 20), D(2026, 10, 20), "Hourly"))
		self.assertIsNone(rules.next_occurrence(D(2026, 10, 20), D(2026, 10, 20), ""))

	def test_teams_and_progress(self):
		self.assertTrue(rules.is_team_role("Sales Team"))
		self.assertFalse(rules.is_team_role("Sales Manager"))
		self.assertFalse(rules.is_team_role(" Team"))
		self.assertEqual(rules.team_label("Support Team"), "Support")
		self.assertEqual(rules.checklist_progress([{"done": 1}, {"done": 0}, {"done": "1"}]), (2, 3))
		self.assertEqual(rules.checklist_progress(None), (0, 0))


class FakeDoc(Row):
	"""A ToDo document: attribute and .get access, flags, and a before-image."""

	def __init__(self, before=None, **kw):
		super().__init__(**kw)
		self["flags"] = Flags()
		self["_before"] = before

	def get_doc_before_save(self):
		return self["_before"]


class TestRecurrence(unittest.TestCase):
	def setUp(self):
		_reset()

	def closed(self, **kw):
		base = dict(
			name="T1",
			status="Closed",
			custom_repeat="Weekly",
			date="2026-10-19",
			creation=DT(2026, 10, 12, 9),
			allocated_to="crew@x",
			assigned_by="boss@x",
			role=None,
			reference_type="Task",
			reference_name="TASK-1",
			description="Check the pump log",
			priority="High",
			custom_checklist=[Row(item="Read the meter", done=1), Row(item="Photo", done=0)],
		)
		base.update(kw)
		before = Row(status="Open")
		return FakeDoc(before=before, **base)

	def test_done_makes_the_next_one(self):
		recurrence.on_update(self.closed())
		self.assertEqual(len(fake.inserted), 1)
		nxt = fake.inserted[0]
		self.assertEqual(nxt["date"], "2026-10-26")
		self.assertEqual(nxt["allocated_to"], "crew@x")
		self.assertEqual(nxt["custom_repeat"], "Weekly")
		self.assertEqual(nxt["custom_checklist"], [{"item": "Read the meter"}, {"item": "Photo"}])

	def test_cancelled_or_auto_closed_or_resaved_makes_nothing(self):
		recurrence.on_update(self.closed(status="Cancelled"))
		auto = self.closed()
		auto.flags.ee_no_repeat = True
		recurrence.on_update(auto)
		resaved = self.closed()
		resaved["_before"] = Row(status="Closed")
		recurrence.on_update(resaved)
		recurrence.on_update(self.closed(custom_repeat=""))
		self.assertEqual(fake.inserted, [])

	def test_the_next_one_is_not_texted(self):
		doc = Row(name="T2", priority="High", allocated_to="crew@x", assigned_by="boss@x", owner="boss@x")
		doc["flags"] = types.SimpleNamespace(
			ee_repeat_of="T1", get=lambda k: "T1" if k == "ee_repeat_of" else None
		)
		alerts.on_todo_insert(doc)
		self.assertEqual(fake.enqueued, [])

	def test_auto_close_marks_its_closes_as_not_repeating(self):
		fake.todos = {"T1": FakeToDo("T1", reference_type="Task", reference_name="TASK-1", allocated_to="a")}
		auto_close.on_change(Doc("Task", "TASK-1", {"status": "Working"}, status="Completed"))
		self.assertTrue(fake.todos["T1"].flags.ee_no_repeat)


class TestTeamsAndSteps(unittest.TestCase):
	def test_a_team_queue_item_is_claimed_by_its_team(self):
		row = types.SimpleNamespace(allocated_to=None, assigned_by="boss", owner="boss", role="Sales Team")
		self.assertTrue(api._abilities(row, "me", False, {"Sales Team"})["claim"])
		self.assertFalse(api._abilities(row, "me", False, {"Support Team"})["claim"])
		self.assertTrue(api._abilities(row, "lead", True, set())["claim"])
		self.assertTrue(api._abilities(row, "me", False, {"Sales Team"})["checklist"])

	def test_steps_from_the_dialog(self):
		self.assertEqual(
			api._steps("- Drain the basin\n\n* Photo of the label\n  "),
			[{"item": "Drain the basin"}, {"item": "Photo of the label"}],
		)
		self.assertEqual(api._steps('["a", "", "b"]'), [{"item": "a"}, {"item": "b"}])
		self.assertEqual(api._steps(None), [])
		self.assertEqual(len(api._steps("\n".join(str(i) for i in range(80)))), 50)


class QueueDoc(FakeDoc):
	def is_new(self):
		return bool(self.get("_new"))


class TestQueueTimelineLine(unittest.TestCase):
	def test_a_new_queue_item_says_which_queue(self):
		doc = QueueDoc(
			_new=True, role="Sales Team", allocated_to=None, assigned_by="nik", description="Call back Mike"
		)
		doc._assignment = {"text": "nik assigned nik: Call back Mike", "comment_type": "Assigned"}
		api.todo_queue_comment(doc)
		self.assertEqual(doc._assignment["text"], "nik put this in the Sales queue: Call back Mike")

	def test_claiming_says_who_took_it(self):
		doc = QueueDoc(before=Row(allocated_to=None), role="Sales Team", allocated_to="ann", description="x")
		doc._assignment = None
		api.todo_queue_comment(doc)
		self.assertEqual(doc._assignment["text"], "ann took this from the Sales queue")

	def test_an_ordinary_assignment_is_left_alone(self):
		doc = QueueDoc(_new=True, role=None, allocated_to="ann", assigned_by="nik", description="x")
		doc._assignment = {"text": "nik assigned ann: x", "comment_type": "Assigned"}
		api.todo_queue_comment(doc)
		self.assertEqual(doc._assignment["text"], "nik assigned ann: x")


class Refused(Exception):
	pass


class FakeTask(Row):
	def __init__(self, **kw):
		super().__init__(**kw)
		self["flags"] = Flags()
		self["saves"] = []

	def __setattr__(self, key, value):
		self[key] = value

	def save(self):
		self["saves"].append(
			(self.status, self.completed_on, self.completed_by, self.flags.ignore_permissions)
		)


class TestDoneCompletesTheTask(unittest.TestCase):
	"""Nik, 2026-10-20: Done on a Task completes it, dated today, by whoever pressed Done; a Task
	ERPNext will not complete yet refuses the Done before anything changes."""

	def setUp(self):
		_reset()
		fake.session = types.SimpleNamespace(user="ann@x")
		fake.throw = lambda message, *a, **k: (_ for _ in ()).throw(Refused(message))
		self.tasks = {"TASK-1": Row(name="TASK-1", status="Working", subject="Install the pump")}
		self.depends = []
		fake.db.get_value = lambda doctype, name, fields=None, as_dict=False, **k: self.tasks.get(name)

		def get_all(doctype, filters=None, pluck=None, **k):
			if doctype == "Task Depends On":
				return list(self.depends)
			names = (filters or {}).get("name", [None, []])[1]
			return [self.tasks[n] for n in names if n in self.tasks]

		fake.get_all = get_all

	def tearDown(self):
		# _reset swaps in fresh state but cannot see what a test pinned on the module object.
		for name in ("get_all", "get_doc", "throw", "session"):
			fake.__dict__.pop(name, None)

	def todo(self, **kw):
		return Row({"name": "T1", "reference_type": "Task", "reference_name": "TASK-1", **kw})

	def test_only_a_task_that_does_not_repeat(self):
		self.assertTrue(rules.done_completes_task("Task", None))
		self.assertFalse(rules.done_completes_task("Task", "Weekly"))
		self.assertFalse(rules.done_completes_task("Opportunity", None))
		self.assertFalse(rules.done_completes_task(None, None))

	def test_erpnext_own_spelling_is_what_lets_a_dependency_through(self):
		self.assertIsNone(rules.blocking_dependency([("A", "Completed"), ("B", "Cancelled")]))
		self.assertEqual(
			rules.blocking_dependency([("A", "Completed"), ("B", "Canceled")]), ("B", "Canceled")
		)
		self.assertEqual(rules.blocking_dependency([("A", None)]), ("A", None))
		self.assertEqual(rules.blocking_dependency([("A", "Working"), ("B", "Open")]), ("A", "Working"))

	def test_an_open_task_is_the_one_to_complete(self):
		self.assertEqual(api._task_to_complete(self.todo()), "TASK-1")

	def test_nothing_to_complete_off_a_task_or_on_a_finished_one(self):
		self.assertIsNone(
			api._task_to_complete(Row(name="T1", reference_type="Opportunity", reference_name="O-1"))
		)
		self.assertIsNone(api._task_to_complete(self.todo(custom_repeat="Weekly")))
		for status in ("Completed", "Canceled", "Invoiced", "Template"):
			self.tasks["TASK-1"]["status"] = status
			self.assertIsNone(api._task_to_complete(self.todo()), status)
		self.assertIsNone(api._task_to_complete(self.todo(reference_name="TASK-GONE")))

	def test_a_dependency_still_open_refuses_and_names_it(self):
		self.depends = ["TASK-0"]
		self.tasks["TASK-0"] = Row(name="TASK-0", status="Working", subject="Order the pump")
		with self.assertRaises(Refused) as caught:
			api._task_to_complete(self.todo())
		self.assertIn("TASK-0 (Order the pump)", str(caught.exception))
		self.assertIn("Working", str(caught.exception))
		self.tasks["TASK-0"]["status"] = "Completed"
		self.assertEqual(api._task_to_complete(self.todo()), "TASK-1")

	def test_the_task_is_completed_today_by_whoever_pressed_done(self):
		task = FakeTask(name="TASK-1", status="Working", completed_on=None, completed_by=None)
		fake.get_doc = lambda doctype, name=None: task
		api._complete_task("TASK-1")
		self.assertEqual(task.saves, [("Completed", "2026-10-20", "ann@x", True)])

	def test_done_closes_the_assignment_first_and_says_which_task(self):
		calls = []
		saved = {name: getattr(api, name) for name in ("_load", "close_todo", "_complete_task", "_notify")}
		doc = self.todo(allocated_to="ann@x", assigned_by="ann@x")
		try:
			api._load = lambda todo, need: doc
			api.close_todo = lambda d, status: calls.append(("close", d.name, status))
			api._complete_task = lambda name: calls.append(("complete", name))
			api._notify = lambda *a, **k: calls.append(("notify",))
			self.assertEqual(api.complete("T1"), {"ok": True, "completed": "TASK-1"})
			self.assertEqual(calls, [("close", "T1", "Closed"), ("complete", "TASK-1")])
			calls.clear()
			self.depends = ["TASK-0"]
			self.tasks["TASK-0"] = Row(name="TASK-0", status="Open", subject="Order the pump")
			with self.assertRaises(Refused):
				api.complete("T1")
			self.assertEqual(calls, [])
		finally:
			for name, fn in saved.items():
				setattr(api, name, fn)


class TestRepeatsStopWhenTheRecordFinishes(unittest.TestCase):
	"""ERPNext closes a Task's assignments while it is being Completed, before its row says so."""

	def setUp(self):
		_reset()

	def closed(self):
		return FakeDoc(
			before=Row(status="Open"),
			name="T1",
			status="Closed",
			custom_repeat="Weekly",
			date="2026-10-19",
			creation=DT(2026, 10, 12, 9),
			allocated_to="crew@x",
			assigned_by="boss@x",
			role=None,
			reference_type="Task",
			reference_name="TASK-1",
			description="Check the pump log",
			priority="Medium",
			custom_checklist=[],
		)

	def test_a_task_marked_finishing_makes_no_next_one(self):
		recurrence.mark_finishing("Task", "TASK-1")
		recurrence.on_update(self.closed())
		self.assertEqual(fake.inserted, [])

	def test_a_task_stored_as_finished_makes_no_next_one(self):
		fake.db.values[("Task", "TASK-1", "status")] = "Completed"
		recurrence.on_update(self.closed())
		self.assertEqual(fake.inserted, [])

	def test_another_task_finishing_does_not_stop_this_one(self):
		recurrence.mark_finishing("Task", "TASK-2")
		recurrence.on_update(self.closed())
		self.assertEqual(len(fake.inserted), 1)


class TestPhaseThreeWiring(unittest.TestCase):
	def test_repeat_hook_and_fields(self):
		events = _doc_events()
		self.assertEqual(
			events["ToDo"]["on_update"], "erpnext_enhancements.task_enhancements.recurrence.on_update"
		)
		self.assertEqual(events["ToDo"]["before_save"], "erpnext_enhancements.api.my_work.todo_queue_comment")
		fields = {
			f["name"]: f
			for f in json.loads((APP / "fixtures" / "custom_field.json").read_text(encoding="utf-8"))
		}
		repeat = fields["ToDo-custom_repeat"]
		self.assertEqual([o for o in repeat["options"].split("\n") if o], list(rules.REPEATS))
		self.assertEqual(fields["ToDo-custom_checklist"]["options"], "Assignment Checklist Item")

	def test_the_checklist_doctype_is_a_child_table_in_its_module(self):
		folder = APP / "task_enhancements" / "doctype" / "assignment_checklist_item"
		meta = json.loads((folder / "assignment_checklist_item.json").read_text())
		self.assertEqual(
			(meta["name"], meta["istable"], meta["module"]),
			("Assignment Checklist Item", 1, "Task Enhancements"),
		)
		self.assertIn(
			"class AssignmentChecklistItem(Document)", (folder / "assignment_checklist_item.py").read_text()
		)

	def test_the_browser_copies_match_the_server(self):
		page = (APP / "task_enhancements" / "page" / "my_work" / "my_work.js").read_text()
		desk = (APP / "public" / "js" / "global_enhancements" / "my_work.js").read_text()
		for source in (page, desk):
			found = re.search(r"const REPEATS = (\[[^\]]*\]);", source)
			self.assertEqual(json.loads(found.group(1)), list(rules.REPEATS))
		leads = re.search(r"const LEAD_ROLES = (\[[^\]]*\]);", page)
		self.assertEqual(tuple(json.loads(leads.group(1))), api.LEAD_ROLES)

	def test_phase_three_endpoints(self):
		source = (APP / "api" / "my_work.py").read_text()
		decorated = dict(
			(n, v)
			for v, n in re.findall(r'@frappe\.whitelist\(methods=\["(GET|POST)"\]\)\ndef (\w+)', source)
		)
		for name in ("get_team_board", "get_calendar", "get_teams"):
			self.assertEqual(decorated.get(name), "GET", name)
		for name in ("assign", "set_repeat", "checklist_toggle", "checklist_add", "checklist_remove"):
			self.assertEqual(decorated.get(name), "POST", name)


if __name__ == "__main__":
	unittest.main()
