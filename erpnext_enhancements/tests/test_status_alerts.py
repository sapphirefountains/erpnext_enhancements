"""Tests for the PRO-0204 status alerts (``erpnext_enhancements.status_alerts``).

Focus is the transition guards — the rules that decide *whether* an alert
fires, which is where a bug would mean either spam (re-alerting on every
save of a won opportunity) or silence (never alerting at all):

* ``notify_closed_won`` fires only on the transition into "Closed Won"
  (including documents created directly in that status) and never during
  migrate/install/patch/import.
* ``notify_payment_received`` fires only when the Payment Received box goes
  0 → 1, posts the timeline comment synchronously, and queues delivery.
* ``stamp_payment_received_date`` defaults the date exactly once.

Delivery itself (``deliver_*``) talks to the Triton gateway and the settings
single, so it is exercised against a bench, not here; these tests fake the
documents and assert on ``frappe.enqueue`` / ``add_comment`` calls.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_enhancements.status_alerts import (
	notify_closed_won,
	notify_payment_received,
	stamp_payment_received_date,
)


class _FakeDoc:
	"""Just enough of a Document for the guard functions."""

	def __init__(self, before=None, **fields):
		self.name = fields.pop("name", "TEST-DOC-0001")
		self.__dict__.update(fields)
		self._before = before
		self.comments = []

	def get_doc_before_save(self):
		return self._before

	def add_comment(self, comment_type, text=None, **kwargs):
		self.comments.append((comment_type, text))


def _force_flag(testcase, module, value=True):
	"""Pin the process-automation master switch for a test class (the gated
	modules import the flag function by value, so patch their local ref)."""
	patcher = patch(f"erpnext_enhancements.{module}.process_automation_enabled", return_value=value)
	patcher.start()
	testcase.addCleanup(patcher.stop)


class TestClosedWonGuard(FrappeTestCase):
	def setUp(self):
		super().setUp()
		_force_flag(self, "status_alerts")

	def _opp(self, status, before_status=None):
		before = _FakeDoc(status=before_status) if before_status else None
		return _FakeDoc(before=before, status=status)

	def test_transition_into_closed_won_enqueues(self):
		with patch.object(frappe, "enqueue") as enqueue:
			notify_closed_won(self._opp("Closed Won", before_status="Negotiation/Review"))
		enqueue.assert_called_once()
		self.assertEqual(enqueue.call_args.kwargs.get("opportunity"), "TEST-DOC-0001")

	def test_created_directly_as_closed_won_enqueues(self):
		with patch.object(frappe, "enqueue") as enqueue:
			notify_closed_won(self._opp("Closed Won"))
		enqueue.assert_called_once()

	def test_resave_of_won_opportunity_is_silent(self):
		with patch.object(frappe, "enqueue") as enqueue:
			notify_closed_won(self._opp("Closed Won", before_status="Closed Won"))
		enqueue.assert_not_called()

	def test_other_statuses_are_silent(self):
		with patch.object(frappe, "enqueue") as enqueue:
			notify_closed_won(self._opp("Qualification"))
			notify_closed_won(self._opp("Lost", before_status="Closed Won"))
		enqueue.assert_not_called()

	def test_silent_during_migrate(self):
		frappe.flags.in_migrate = True
		try:
			with patch.object(frappe, "enqueue") as enqueue:
				notify_closed_won(self._opp("Closed Won", before_status="Qualification"))
			enqueue.assert_not_called()
		finally:
			frappe.flags.in_migrate = False


class TestMasterSwitchOff(FrappeTestCase):
	"""With the suite switched off, the alert hooks are completely silent."""

	def setUp(self):
		super().setUp()
		_force_flag(self, "status_alerts", value=False)

	def test_closed_won_is_silent(self):
		doc = _FakeDoc(before=_FakeDoc(status="Qualification"), status="Closed Won")
		with patch.object(frappe, "enqueue") as enqueue:
			notify_closed_won(doc)
		enqueue.assert_not_called()

	def test_payment_received_is_silent(self):
		doc = _FakeDoc(
			before=_FakeDoc(custom_payment_received=0),
			custom_payment_received=1,
			custom_payment_received_on=None,
			custom_payment_method="Check",
		)
		with patch.object(frappe, "enqueue") as enqueue:
			notify_payment_received(doc)
		enqueue.assert_not_called()
		self.assertEqual(doc.comments, [])


class TestPaymentReceivedGuard(FrappeTestCase):
	def setUp(self):
		super().setUp()
		_force_flag(self, "status_alerts")

	def _project(self, received, before_received=None, **fields):
		before = (
			_FakeDoc(custom_payment_received=before_received)
			if before_received is not None
			else None
		)
		return _FakeDoc(
			before=before,
			custom_payment_received=received,
			custom_payment_received_on=fields.pop("received_on", None),
			custom_payment_method=fields.pop("method", None),
			**fields,
		)

	def test_ticking_the_box_comments_and_enqueues(self):
		doc = self._project(1, before_received=0, method="Check")
		with patch.object(frappe, "enqueue") as enqueue:
			notify_payment_received(doc)
		enqueue.assert_called_once()
		self.assertEqual(enqueue.call_args.kwargs.get("project"), "TEST-DOC-0001")
		self.assertEqual(len(doc.comments), 1)
		self.assertIn("via Check", doc.comments[0][1])

	def test_resave_with_box_still_ticked_is_silent(self):
		doc = self._project(1, before_received=1)
		with patch.object(frappe, "enqueue") as enqueue:
			notify_payment_received(doc)
		enqueue.assert_not_called()
		self.assertEqual(doc.comments, [])

	def test_unticked_box_is_silent(self):
		doc = self._project(0, before_received=0)
		with patch.object(frappe, "enqueue") as enqueue:
			notify_payment_received(doc)
		enqueue.assert_not_called()

	def test_retick_after_correction_realerts(self):
		doc = self._project(1, before_received=0)
		with patch.object(frappe, "enqueue") as enqueue:
			notify_payment_received(doc)
		enqueue.assert_called_once()


class TestPaymentDateStamp(FrappeTestCase):
	def test_stamps_today_when_ticked_and_empty(self):
		doc = _FakeDoc(custom_payment_received=1, custom_payment_received_on=None)
		stamp_payment_received_date(doc)
		self.assertEqual(doc.custom_payment_received_on, frappe.utils.today())

	def test_keeps_an_explicit_date(self):
		doc = _FakeDoc(custom_payment_received=1, custom_payment_received_on="2026-01-15")
		stamp_payment_received_date(doc)
		self.assertEqual(doc.custom_payment_received_on, "2026-01-15")

	def test_untouched_when_not_ticked(self):
		doc = _FakeDoc(custom_payment_received=0, custom_payment_received_on=None)
		stamp_payment_received_date(doc)
		self.assertIsNone(doc.custom_payment_received_on)
