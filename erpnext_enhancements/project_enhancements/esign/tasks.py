# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Scheduled housekeeping and chasing for contract signature requests.

* :func:`expire_stale_requests` — flips timed-out requests to ``Expired``. The
  live expiry check in ``lifecycle.is_signable`` is the control; this exists so
  the desk list tells the truth rather than showing month-old links as "Sent".
* :func:`retry_undelivered` — re-runs delivery for a signed request whose
  customer copy never went out. Signing deliberately cannot fail on a PDF or an
  SMTP hiccup, which means something has to come back for the stragglers.
* :func:`send_signature_reminders` — nudges a customer who has not signed, on
  the cadence in Settings, and tells the contract owner once the last nudge goes
  unanswered. An unsigned agreement is a stalled job, and nobody watches a list.
* :func:`digest_awaiting_signature` — a weekly "still out for signature" summary,
  so a link that quietly went nowhere is visible without anyone remembering to
  look.

Every send stamps its counter **before** dispatching, so a scheduler window
evaluated twice cannot double-chase a customer — the same at-most-once discipline
as ``maintenance_nudge_sent`` and the morning dispatch digest.
"""

import frappe
from frappe.utils import add_to_date, date_diff, escape_html, getdate, now_datetime

from erpnext_enhancements import email_style

BATCH = 50

DIGEST_LIMIT = 200


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


# ---------------------------------------------------------------------------
# Chasing an unsigned agreement
# ---------------------------------------------------------------------------


def reminder_offsets():
	"""Days of silence before each nudge, in the order the operator wrote them.

	These are **gaps, not absolute days from the first send**: every nudge issues
	a fresh link and therefore updates ``sent_on``, so "3,7" means "nudge after 3
	quiet days, then after 7 more". That is the escalation shape people actually
	want, and it is what the Settings description promises.

	Order and repeats are preserved: because these are gaps, "7,7,7" legitimately
	means "chase weekly, three times", and "10,3" means a long wait then a quick
	follow-up. Sorting or de-duplicating would silently rewrite an operator's
	schedule into something they did not ask for.

	Blank means never chase — an explicit choice, so it returns an empty list
	rather than reaching for a default. The shipped default arrives on existing
	sites through ``patches/seed_contract_esign_reminder_days``, because a field
	default only applies when the Settings Single is first created and it already
	exists everywhere. (``get_single_value`` returns "" for an unset Data field,
	never None, so there is no runtime fallback to lean on.)
	"""
	raw = frappe.db.get_single_value("ERPNext Enhancements Settings", "contract_esign_reminder_days")
	return [int(t) for t in str(raw or "").split(",") if t.strip().isdigit() and int(t) > 0]


def send_signature_reminders():
	"""Daily: nudge customers who have not signed, then tell staff when we stop.

	Each nudge issues a **fresh link**, because the original token is stored only
	as a hash and cannot be recovered — so a reminder that pointed at the old link
	would be a reminder we could not actually write. The superseded link then
	explains itself and offers a resend, which is the behaviour the signing page
	was built for.

	Reminders deliberately do NOT reset the email-confirmation attempt counter:
	an automated chase is not a staff decision to unlock someone.
	"""
	from erpnext_enhancements.feature_flags import contract_esign_enabled

	if not contract_esign_enabled():
		return

	offsets = reminder_offsets()
	if not offsets:
		return

	today = getdate(now_datetime())
	for row in frappe.get_all(
		"Contract Signature Request",
		filters={"status": ["in", ["Sent", "Viewed"]], "provider": "In-House"},
		fields=["name", "sent_on", "reminders_sent", "expires_on", "project_contract"],
		# Oldest first. Without this the default `modified desc` ordering, combined
		# with the batch cap, would starve exactly the requests that have been quiet
		# longest — the ones the whole feature exists to chase.
		order_by="sent_on asc",
		limit=BATCH,
	):
		try:
			if not row.sent_on:
				continue
			if row.expires_on and now_datetime() > frappe.utils.get_datetime(row.expires_on):
				continue  # the expiry sweep owns this one

			sent = row.reminders_sent or 0
			quiet_days = date_diff(today, getdate(row.sent_on))

			if sent >= len(offsets):
				# Every nudge used. Give the customer the last gap to respond before
				# telling the owner it went unanswered — otherwise the alert fires the
				# morning after the final email.
				if quiet_days >= offsets[-1]:
					_alert_unanswered(row)
				continue

			if quiet_days < offsets[sent]:
				continue

			# Anything that would make the resend throw is checked BEFORE the counter
			# is stamped, so a nudge that could never be delivered does not consume
			# one — which would otherwise let the "we sent every reminder" alert claim
			# reminders that were never sent.
			if not _still_chaseable(row):
				continue

			# Stamp before sending: a window evaluated twice must not double-nudge.
			frappe.db.set_value(
				"Contract Signature Request",
				row.name,
				{"reminders_sent": sent + 1, "last_reminder_on": now_datetime()},
				update_modified=False,
			)
			frappe.db.commit()
			_send_reminder(row.name, sent + 1, len(offsets))
		except Exception:
			# The resend persists a superseding token before it emails, so a failure
			# at the send step must not leave the customer's working link rotated out
			# with nothing sent in its place.
			frappe.db.rollback()
			frappe.log_error(frappe.get_traceback(), f"Contract e-sign: reminder failed ({row.name})")


def _still_chaseable(row):
	"""True when a nudge for this request could actually be sent.

	Mirrors the guards inside ``resend_for_signature`` (a submitted contract still
	awaiting signature, on a template that can print one). A contract signed on
	paper, voided or amended leaves its request sitting at "Sent" — there is
	nothing left to chase there.
	"""
	contract = frappe.db.get_value(
		"Project Contract",
		row.project_contract,
		["docstatus", "status", "contract_template"],
		as_dict=True,
	)
	if not contract or contract.docstatus != 1:
		return False
	if contract.status not in ("Draft", "Out for Signature"):
		return False
	body = frappe.db.get_value("Contract Template", contract.contract_template, "body") or ""
	return "sig(" in body


def _send_reminder(request_name, number, total):
	"""Re-issue the link with reminder wording. Raises nothing into the sweep."""
	from erpnext_enhancements.project_enhancements.esign.api import resend_for_signature

	note = frappe._(
		"This is a friendly reminder — your agreement is still waiting to be signed. "
		"Use the button below; it replaces any earlier link we sent you."
	)
	if number >= total:
		note += " " + frappe._("This is the last reminder we'll send automatically.")
	resend_for_signature(request_name, message=note, reset_attempts=False)


def _alert_unanswered(row):
	"""Tell the contract owner once that every reminder went unanswered."""
	if frappe.db.get_value("Contract Signature Request", row.name, "stale_alerted"):
		return
	try:
		frappe.db.set_value(
			"Contract Signature Request", row.name, "stale_alerted", 1, update_modified=False
		)
		owner = frappe.db.get_value("Project Contract", row.project_contract, "owner")
		if owner:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"subject": frappe._("Still unsigned: {0}").format(row.project_contract),
					"email_content": frappe._(
						"We've sent every automatic reminder for this agreement and it is still "
						"unsigned. Worth a phone call — or void the request if it is no longer needed."
					),
					"document_type": "Project Contract",
					"document_name": row.project_contract,
					"for_user": owner,
					"type": "Alert",
				}
			).insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Contract e-sign: stale alert failed ({row.name})")


def digest_awaiting_signature():
	"""Weekly: one summary of everything still out for signature.

	Sent to the addresses configured for signature notifications; silent when
	there is nothing outstanding, so an empty week costs nobody an email.
	"""
	from erpnext_enhancements.feature_flags import contract_esign_enabled

	if not contract_esign_enabled():
		return

	rows = frappe.get_all(
		"Contract Signature Request",
		filters={"status": ["in", ["Sent", "Viewed"]]},
		fields=[
			"name",
			"project_contract",
			"signer_email",
			"sent_on",
			"first_sent_on",
			"view_count",
			"reminders_sent",
		],
		order_by="sent_on asc",
		limit=DIGEST_LIMIT,
	)
	if not rows:
		return
	# The cap exists so one runaway week cannot produce an unreadable email; say so
	# rather than silently reporting a subset as if it were everything.
	total = frappe.db.count("Contract Signature Request", {"status": ["in", ["Sent", "Viewed"]]})

	recipients = [
		row.email
		for row in (
			frappe.get_single("ERPNext Enhancements Settings").get("contract_esign_notify_emails") or []
		)
		if row.get("email")
	]
	if not recipients:
		return

	today = getdate(now_datetime())

	def days_out(row):
		# first_sent_on, not sent_on: every reminder re-sends and moves sent_on, so
		# a column headed "Days out" driven by it would reset to 0 on each nudge and
		# report the opposite of the truth — the most-chased row as the freshest.
		anchor = row.first_sent_on or row.sent_on
		return date_diff(today, getdate(anchor)) if anchor else ""

	# Four columns, not five: "Opened" and "Reminders" fold into one Activity
	# cell, because five columns do not fit a phone even stacked.
	table_rows = [
		[
			r.project_contract or "",
			r.signer_email or "",
			days_out(r),
			("opened" if r.view_count else "not opened")
			+ (f" · {r.reminders_sent} reminder(s)" if r.reminders_sent else ""),
		]
		for r in rows
	]
	truncated = (
		email_style.note(
			frappe._("Showing the {0} longest-outstanding of {1}.").format(len(rows), total)
		)
		if total > len(rows)
		else ""
	)
	try:
		frappe.sendmail(
			recipients=recipients,
			subject=frappe._("{0} agreement(s) still awaiting signature").format(total),
			message=email_style.wrap(
				email_style.p(
					frappe._("These agreements have been sent for signature and are not signed yet.")
				)
				+ truncated
				+ email_style.table(
					[
						frappe._("Contract"),
						frappe._("Sent to"),
						frappe._("Days out"),
						frappe._("Activity"),
					],
					table_rows,
				),
				title=frappe._("{0} agreement(s) still awaiting signature").format(total),
				eyebrow=frappe._("Contracts"),
			),
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Contract e-sign: awaiting-signature digest failed")
