"""Tests for the PRO-0204 status alerts (``erpnext_enhancements.status_alerts``).

Focus is the guards that decide *whether* an alert fires:

* ``deliver_closed_won_alerts`` is gated by the process-automation master switch
  and the maintenance context. It is enqueued from project creation now (not the
  won-save) — the Closed-Won *transition* detection moved to
  :mod:`erpnext_enhancements.crm_enhancements.project_prompt` (see
  ``test_project_prompt``).
* ``notify_payment_received`` fires only when the Payment Received box goes
  0 → 1, posts the timeline comment synchronously, and queues delivery.
* ``stamp_payment_received_date`` defaults the date exactly once.

Delivery itself (the SMS gateway + settings single) is exercised against a
bench; these tests fake the documents and assert on ``frappe.enqueue`` /
``add_comment`` / ``_alert_recipients`` calls.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_enhancements.status_alerts import (
	deliver_closed_won_alerts,
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


class TestClosedWonDeliveryGating(FrappeTestCase):
	"""The deferred Closed-Won SMS respects the master switch + maintenance context.

	(The transition that *triggers* it now lives in ``project_prompt``; here we
	only assert the delivery job's own gate, since it's enqueued from the
	project-creation background job rather than the won-save.)
	"""

	def test_silent_when_switch_off(self):
		with (
			patch("erpnext_enhancements.status_alerts.process_automation_enabled", return_value=False),
			patch("erpnext_enhancements.status_alerts._alert_recipients") as recipients,
		):
			deliver_closed_won_alerts("TEST-DOC-0001")
		recipients.assert_not_called()

	def test_silent_during_migrate(self):
		frappe.flags.in_migrate = True
		try:
			with (
				patch("erpnext_enhancements.status_alerts.process_automation_enabled", return_value=True),
				patch("erpnext_enhancements.status_alerts._alert_recipients") as recipients,
			):
				deliver_closed_won_alerts("TEST-DOC-0001")
			recipients.assert_not_called()
		finally:
			frappe.flags.in_migrate = False

	def test_passes_the_gate_when_enabled(self):
		# Switch on and not in maintenance: it looks up recipients (and only bails
		# because the fake list is empty), proving the gate lets it through.
		with (
			patch("erpnext_enhancements.status_alerts.process_automation_enabled", return_value=True),
			patch("erpnext_enhancements.status_alerts._alert_recipients", return_value=[]) as recipients,
		):
			deliver_closed_won_alerts("TEST-DOC-0001")
		recipients.assert_called_once_with("closed_won")


class TestMasterSwitchOff(FrappeTestCase):
	"""With the suite switched off, the payment alert hook is completely silent."""

	def setUp(self):
		super().setUp()
		_force_flag(self, "status_alerts", value=False)

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
