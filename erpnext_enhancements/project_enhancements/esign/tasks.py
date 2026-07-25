# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Daily housekeeping for contract signature requests.

Two sweeps, both cheap and both idempotent:

* :func:`expire_stale_requests` — flips timed-out requests to ``Expired``. The
  live expiry check in ``lifecycle.is_signable`` is the control; this exists so
  the desk list tells the truth rather than showing month-old links as "Sent".
* :func:`retry_undelivered` — re-runs delivery for a signed request whose
  customer copy never went out. Signing deliberately cannot fail on a PDF or an
  SMTP hiccup, which means something has to come back for the stragglers.
"""

import frappe
from frappe.utils import add_to_date, now_datetime

BATCH = 50


def expire_stale_requests():
	"""Mark timed-out signing links Expired (and stop them resolving)."""
	stale = frappe.get_all(
		"Contract Signature Request",
		filters={"status": ["in", ["Sent", "Viewed"]], "expires_on": ["<", now_datetime()]},
		pluck="name",
		limit=BATCH,
	)
	for name in stale:
		try:
			frappe.db.set_value(
				"Contract Signature Request",
				name,
				{"status": "Expired", "token_hash": None},
				update_modified=False,
			)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Contract e-sign: expiry failed ({name})")
	frappe.db.commit()


def retry_undelivered():
	"""Re-attempt the customer's copy for signatures where it never landed.

	Only requests signed more than an hour ago, so this never races the delivery
	job enqueued at signing time.
	"""
	from erpnext_enhancements.project_enhancements.esign.lifecycle import deliver_signed_contract

	pending = frappe.get_all(
		"Contract Signature Request",
		filters={
			"status": "Signed",
			"delivered_on": ["is", "not set"],
			"signed_on": ["<", add_to_date(now_datetime(), hours=-1)],
		},
		pluck="name",
		limit=BATCH,
	)
	for name in pending:
		try:
			deliver_signed_contract(name)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Contract e-sign: delivery retry failed ({name})")
