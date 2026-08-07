"""Tests for the PRO-0204 hand-off process engine (``erpnext_enhancements.process_steps``).

Focus is the decision logic that runs on every Project save — seeding,
anchor auto-completion, completion stamping, due-date computation, and
transition detection — exercised on fake documents so a logic regression
can't spam notifications or silently stall the process. Delivery and
escalation hit the gateway/settings and are exercised against a bench.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime, getdate

from erpnext_enhancements.process_steps import (
	_actionable_steps,
	_first_pending,
	notify_step_transitions,
	seed_process_steps,
	sync_process_steps,
)
from erpnext_enhancements.utils.working_days import add_working_days

TEMPLATES = [
	frappe._dict(step_number=1, step_title="Mark Opportunity as Won", responsible_role="Account Executive", auto_anchor="Opportunity Won", sla_hours=0, sla_business_days=0, description=""),
	frappe._dict(step_number=2, step_title="Create Project in PM System", responsible_role="Project Manager", auto_anchor="Project Created", sla_hours=0, sla_business_days=0, description=""),
	frappe._dict(step_number=3, step_title="Create Accounting Project & Send Invoice", responsible_role="Finance & Accounting Manager", auto_anchor="", sla_hours=24, sla_business_days=1, description=""),
	frappe._dict(step_number=5, step_title="Initial Payment From Customer", responsible_role="Finance & Accounting Manager", auto_anchor="Payment Received", sla_hours=0, sla_business_days=0, description=""),
]

#: TEMPLATES plus Outline Tasks (6) and Hold Launch Meeting (7), for the tests
#: that need those two steps present to exercise the "5 doesn't block 6/7" rule.
TEMPLATES_WITH_TASKS_AND_LAUNCH = TEMPLATES + [
	frappe._dict(step_number=6, step_title="Outline Tasks & Responsibilities in PM System", responsible_role="Project Manager", auto_anchor="", sla_hours=48, sla_business_days=2, description=""),
	frappe._dict(step_number=7, step_title="Hold Project Launch Meeting", responsible_role="Project Manager", auto_anchor="", sla_hours=48, sla_business_days=2, description=""),
]


class _FakeRow(frappe._dict):
	pass


class _FakeMeta:
	def __init__(self, has_field=True):
		self._has = has_field

	def get_field(self, fieldname):
		return object() if self._has else None


class _FakeProject:
	def __init__(self, before=None, has_field=True, **fields):
		self.name = fields.pop("name", "PRJ-TEST-0001")
		self.meta = _FakeMeta(has_field)
		self._fields = fields
		self._before = before
		self.comments = []
		self._fields.setdefault("custom_process_steps", [])
		self._row_counter = 0

	def get(self, key, default=None):
		return self._fields.get(key, default)

	def __getattr__(self, key):
		try:
			return self.__dict__["_fields"][key]
		except KeyError:
			raise AttributeError(key)

	def append(self, table, row):
		self._row_counter += 1
		row = _FakeRow(row)
		row.name = f"row-{self._row_counter}"
		row.setdefault("due_by", None)
		row.setdefault("completed_on", None)
		row.setdefault("completed_by", None)
		self._fields[table].append(row)
		return row

	def get_doc_before_save(self):
		return self._before

	def add_comment(self, comment_type, text=None, **kwargs):
		self.comments.append((comment_type, text))


def _seeded_project(**overrides):
	project = _FakeProject(custom_opportunity="OPP-0001", **overrides)
	with (
		patch("erpnext_enhancements.process_steps._templates", return_value=TEMPLATES),
		patch.object(frappe.db, "get_value", return_value="2026-06-01"),
		patch.object(frappe.db, "get_single_value", return_value=None),
	):
		seed_process_steps(project)
	return project


def _seeded_project_full(**overrides):
	"""Like ``_seeded_project`` but seeded from ``TEMPLATES_WITH_TASKS_AND_LAUNCH``."""
	project = _FakeProject(custom_opportunity="OPP-0001", **overrides)
	with (
		patch("erpnext_enhancements.process_steps._templates", return_value=TEMPLATES_WITH_TASKS_AND_LAUNCH),
		patch.object(frappe.db, "get_value", return_value="2026-06-01"),
		patch.object(frappe.db, "get_single_value", return_value=None),
	):
		seed_process_steps(project)
	return project


def _snapshot(project):
	"""A ``before``-shaped ``_FakeProject`` copy of ``project``'s current steps,
	for feeding ``notify_step_transitions`` a before/after pair."""
	snapshot = _FakeProject(custom_opportunity="OPP-0001")
	snapshot._fields["custom_process_steps"] = [
		_FakeRow(dict(row)) for row in project.get("custom_process_steps")
	]
	return snapshot


def _force_flag(testcase, value=True):
	patcher = patch(
		"erpnext_enhancements.process_steps.process_automation_enabled", return_value=value
	)
	patcher.start()
	testcase.addCleanup(patcher.stop)


class TestMasterSwitchOff(FrappeTestCase):
	"""With the suite switched off, the engine never seeds or notifies."""

	def setUp(self):
		super().setUp()
		_force_flag(self, value=False)

	def test_no_seeding(self):
		project = _FakeProject(custom_opportunity="OPP-0001")
		with patch("erpnext_enhancements.process_steps._templates", return_value=TEMPLATES):
			seed_process_steps(project)
		self.assertEqual(project.get("custom_process_steps"), [])


class TestSeeding(FrappeTestCase):
	def setUp(self):
		super().setUp()
		_force_flag(self)
	def test_seeds_with_retro_anchors(self):
		project = _seeded_project()
		steps = project.get("custom_process_steps")
		self.assertEqual(len(steps), 4)
		by_no = {s.step_number: s for s in steps}
		# anchored steps 1-2 retro-complete; manual + payment stay pending
		self.assertEqual(by_no[1].status, "Completed")
		self.assertIsNotNone(by_no[1].completed_on)
		self.assertEqual(by_no[2].status, "Completed")
		self.assertEqual(by_no[3].status, "Pending")
		self.assertEqual(by_no[5].status, "Pending")
		# the current step (3) got a due date from its 24h SLA
		self.assertIsNotNone(by_no[3].due_by)
		self.assertIsNone(by_no[5].due_by)

	def test_no_opportunity_no_seed(self):
		project = _FakeProject()
		with patch("erpnext_enhancements.process_steps._templates", return_value=TEMPLATES):
			seed_process_steps(project)
		self.assertEqual(project.get("custom_process_steps"), [])

	def test_missing_field_is_inert(self):
		project = _FakeProject(has_field=False, custom_opportunity="OPP-0001")
		with patch("erpnext_enhancements.process_steps._templates", return_value=TEMPLATES):
			seed_process_steps(project)
		self.assertEqual(project.get("custom_process_steps"), [])

	def test_first_pending_orders_by_step_number(self):
		rows = [
			frappe._dict(step_number=5, status="Pending"),
			frappe._dict(step_number=3, status="Pending"),
			frappe._dict(step_number=1, status="Completed"),
		]
		self.assertEqual(_first_pending(rows).step_number, 3)
		self.assertIsNone(_first_pending([frappe._dict(step_number=1, status="Completed")]))


class TestActionableSteps(FrappeTestCase):
	"""2026-08-07 decision: step 5 (payment) must not block 6/7, since payment
	can take weeks and neither step is money-dependent. Ordering among 1-4,
	and between 6 and 7 themselves, is otherwise unchanged."""

	def test_payment_does_not_block_tasks_or_launch(self):
		rows = [
			frappe._dict(step_number=1, status="Completed"),
			frappe._dict(step_number=2, status="Completed"),
			frappe._dict(step_number=3, status="Completed"),
			frappe._dict(step_number=4, status="Completed"),
			frappe._dict(step_number=5, status="Pending"),  # payment still outstanding
			frappe._dict(step_number=6, status="Pending"),
			frappe._dict(step_number=7, status="Pending"),
		]
		actionable = {row.step_number for row in _actionable_steps(rows)}
		self.assertEqual(actionable, {5, 6})  # 7 still waits on 6

	def test_launch_still_waits_on_tasks(self):
		rows = [
			frappe._dict(step_number=5, status="Pending"),
			frappe._dict(step_number=6, status="Completed"),
			frappe._dict(step_number=7, status="Pending"),
		]
		actionable = {row.step_number for row in _actionable_steps(rows)}
		self.assertEqual(actionable, {5, 7})

	def test_unpaid_invoice_still_blocks_everything(self):
		# Step 4 (send invoice) is not the payment step -- it still gates
		# 5/6/7 normally, same as any other manual step.
		rows = [
			frappe._dict(step_number=1, status="Completed"),
			frappe._dict(step_number=2, status="Completed"),
			frappe._dict(step_number=3, status="Completed"),
			frappe._dict(step_number=4, status="Pending"),
			frappe._dict(step_number=5, status="Pending"),
			frappe._dict(step_number=6, status="Pending"),
			frappe._dict(step_number=7, status="Pending"),
		]
		actionable = {row.step_number for row in _actionable_steps(rows)}
		self.assertEqual(actionable, {4})

	def test_skipped_step_does_not_block(self):
		# Project Process Step's status options include "Skipped"; confirm it
		# is treated the same as Completed for blocking purposes, not assumed
		# away just because nothing writes it today.
		rows = [
			frappe._dict(step_number=1, status="Skipped"),
			frappe._dict(step_number=2, status="Pending"),
		]
		actionable = {row.step_number for row in _actionable_steps(rows)}
		self.assertEqual(actionable, {2})

	def test_empty_when_nothing_pending(self):
		self.assertEqual(_actionable_steps([frappe._dict(step_number=1, status="Completed")]), [])

	def test_agrees_with_first_pending_when_single_actionable(self):
		rows = [
			frappe._dict(step_number=1, status="Completed"),
			frappe._dict(step_number=3, status="Pending"),
			frappe._dict(step_number=4, status="Pending"),
		]
		self.assertEqual([row.step_number for row in _actionable_steps(rows)], [3])
		self.assertEqual(_first_pending(rows).step_number, 3)


class TestSync(FrappeTestCase):
	def setUp(self):
		super().setUp()
		_force_flag(self)

	def test_payment_anchor_autocompletes(self):
		project = _seeded_project()
		project._fields["custom_payment_received"] = 1
		project._fields["custom_payment_received_on"] = "2026-06-09"
		sync_process_steps(project)
		by_no = {s.step_number: s for s in project.get("custom_process_steps")}
		self.assertEqual(by_no[5].status, "Completed")
		self.assertIsNotNone(by_no[5].completed_on)
		# manual step 3 untouched
		self.assertEqual(by_no[3].status, "Pending")

	def test_manual_completion_gets_stamped(self):
		project = _seeded_project()
		by_no = {s.step_number: s for s in project.get("custom_process_steps")}
		by_no[3].status = "Completed"
		sync_process_steps(project)
		self.assertIsNotNone(by_no[3].completed_on)
		self.assertEqual(by_no[3].completed_by, frappe.session.user)

	def test_next_step_gets_due_date_when_current_advances(self):
		project = _seeded_project()
		by_no = {s.step_number: s for s in project.get("custom_process_steps")}
		by_no[3].status = "Completed"
		sync_process_steps(project)
		# next pending is the payment step (sla 0) -> no due date
		self.assertIsNone(by_no[5].due_by)


class TestTransitions(FrappeTestCase):
	def setUp(self):
		super().setUp()
		_force_flag(self)

	def _pair(self):
		"""(before, after) sharing row names, ready for diffing."""
		after = _seeded_project()
		before = _FakeProject(custom_opportunity="OPP-0001")
		for row in after.get("custom_process_steps"):
			copied = _FakeRow(row)
			before._fields["custom_process_steps"].append(copied)
		after._before = before
		return before, after

	def test_completion_notifies_new_current(self):
		before, after = self._pair()
		# freeze before-state, then complete step 3 on after
		before._fields["custom_process_steps"] = [
			_FakeRow(dict(r, status=r.status)) for r in before.get("custom_process_steps")
		]
		step3 = next(s for s in after.get("custom_process_steps") if s.step_number == 3)
		step3.status = "Completed"
		with patch.object(frappe, "enqueue") as enqueue:
			notify_step_transitions(after)
		enqueue.assert_called_once()
		self.assertEqual(enqueue.call_args.kwargs.get("project"), after.name)

	def test_no_change_is_silent(self):
		_, after = self._pair()
		with patch.object(frappe, "enqueue") as enqueue:
			notify_step_transitions(after)
		enqueue.assert_not_called()
		self.assertEqual(after.comments, [])

	def test_last_completion_comments_instead(self):
		before, after = self._pair()
		before._fields["custom_process_steps"] = [
			_FakeRow(dict(r, status=r.status)) for r in before.get("custom_process_steps")
		]
		for step in after.get("custom_process_steps"):
			step.status = "Completed"
		with patch.object(frappe, "enqueue") as enqueue:
			notify_step_transitions(after)
		enqueue.assert_not_called()
		self.assertEqual(len(after.comments), 1)
		self.assertIn("complete", after.comments[0][1].lower())

	def test_insert_is_silent(self):
		project = _seeded_project()  # no before-doc
		with patch.object(frappe, "enqueue") as enqueue:
			notify_step_transitions(project)
		enqueue.assert_not_called()


class TestPaymentDoesNotBlockLater(FrappeTestCase):
	"""End-to-end (through seed/sync/notify) coverage of the same 2026-08-07
	decision as ``TestActionableSteps``, on ``TEMPLATES_WITH_TASKS_AND_LAUNCH``."""

	def setUp(self):
		super().setUp()
		_force_flag(self)

	def test_seeding_leaves_5_6_7_pending_with_no_due_dates(self):
		project = _seeded_project_full()
		by_no = {s.step_number: s for s in project.get("custom_process_steps")}
		for no in (3, 5, 6, 7):
			self.assertEqual(by_no[no].status, "Pending")
		self.assertIsNotNone(by_no[3].due_by)  # the only actionable step at seed time
		self.assertIsNone(by_no[5].due_by)
		self.assertIsNone(by_no[6].due_by)
		self.assertIsNone(by_no[7].due_by)

	def test_completing_3_opens_5_and_6_together(self):
		project = _seeded_project_full()
		by_no = {s.step_number: s for s in project.get("custom_process_steps")}
		by_no[3].status = "Completed"
		sync_process_steps(project)
		# 5 has no SLA (auto-anchored to payment) so no due date; 6 has a real
		# SLA and must start its own clock the moment it becomes actionable --
		# not sit waiting behind 5, which is still Pending.
		self.assertIsNone(by_no[5].due_by)
		self.assertIsNotNone(by_no[6].due_by)
		self.assertIsNone(by_no[7].due_by)  # still blocked by 6

	def test_notifies_both_newly_actionable_owners(self):
		project = _seeded_project_full()
		before = _snapshot(project)
		by_no = {s.step_number: s for s in project.get("custom_process_steps")}
		by_no[3].status = "Completed"
		project._before = before
		with patch.object(frappe, "enqueue") as enqueue:
			notify_step_transitions(project)
		notified = {call.kwargs.get("step_name") for call in enqueue.call_args_list}
		self.assertEqual(notified, {by_no[5].name, by_no[6].name})

	def test_completing_6_opens_7_while_5_still_pending(self):
		project = _seeded_project_full()
		by_no = {s.step_number: s for s in project.get("custom_process_steps")}
		by_no[3].status = "Completed"
		sync_process_steps(project)  # 5 & 6 become actionable

		before = _snapshot(project)
		by_no[6].status = "Completed"  # 5 (payment) is untouched -- still Pending
		sync_process_steps(project)
		project._before = before

		with patch.object(frappe, "enqueue") as enqueue:
			notify_step_transitions(project)

		self.assertEqual(by_no[5].status, "Pending")
		self.assertEqual(by_no[7].status, "Pending")
		self.assertIsNotNone(by_no[7].due_by)
		notified = {call.kwargs.get("step_name") for call in enqueue.call_args_list}
		self.assertEqual(notified, {by_no[7].name})  # not 5 -- already notified earlier


class TestWorkingDays(FrappeTestCase):
	"""Business-day arithmetic for hand-off due dates (2026-06-19 is a Friday)."""

	def test_friday_plus_two_lands_tuesday(self):
		self.assertEqual(getdate(add_working_days("2026-06-19 09:00:00", 2)), getdate("2026-06-23"))

	def test_friday_plus_one_lands_monday(self):
		self.assertEqual(getdate(add_working_days("2026-06-19 09:00:00", 1)), getdate("2026-06-22"))

	def test_monday_plus_one_lands_tuesday(self):
		self.assertEqual(getdate(add_working_days("2026-06-22 09:00:00", 1)), getdate("2026-06-23"))

	def test_zero_or_negative_returns_unchanged(self):
		self.assertEqual(
			get_datetime(add_working_days("2026-06-19 09:00:00", 0)), get_datetime("2026-06-19 09:00:00")
		)
		self.assertEqual(
			get_datetime(add_working_days("2026-06-19 09:00:00", -2)), get_datetime("2026-06-19 09:00:00")
		)

	def test_preserves_time_of_day(self):
		self.assertEqual(
			get_datetime(add_working_days("2026-06-19 14:30:00", 1)), get_datetime("2026-06-22 14:30:00")
		)

	def test_skips_configured_holiday(self):
		# Holiday on Monday 2026-06-22 -> Friday + 1 business day skips to Tuesday.
		with patch.object(frappe, "get_all", return_value=[frappe._dict(holiday_date="2026-06-22")]):
			result = add_working_days("2026-06-19 09:00:00", 1, holiday_list="Test Holidays")
		self.assertEqual(getdate(result), getdate("2026-06-23"))
