# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Unstick Chat subscription recreates deadlocked on a uid a superseded row still claims.

A Workspace Events subscription id is **deterministic**: base64-decoding the id segment of a
live one reads ``s:-:<google user id>:<app>``, so the same coworker against the same
``spaces/-`` target is handed back the same name every time it is created. ``_recreate_one``
was written against the opposite belief — ``active_subscription_for`` still says a recreated
subscription is "genuinely a different subscription with a different name" — so it marked the
old row ``DELETED`` and left ``subscription_uid`` on it. ``subscription_uid`` is unique
table-wide. The replacement therefore came back carrying a uid the dead row was still holding,
and the write died on a 1062.

It could not recover on its own. ``_rows`` skips ``DELETED``, so the hourly scheduler dropped
the dead row, while ``_row_by_uid`` did **not** filter on state — so every redelivered
``subscriptions.expired`` event found it again and re-ran the identical doomed recreate. Two
coworkers reached 27 consecutive failures. Inbound Chat sync for any space only they cover was
dead throughout, and because the failing write is upstream of ``_sweep_gap`` the recovery sweep
that exists for exactly this never ran once.

The code fix ships alongside (v1.340.1). This clears the wreckage it left in the table:

1. **Release the uid from every superseded row.** This is the deadlock itself. Keyed on
   ``state = DELETED`` — the rule the writer applies — rather than on which rows look wrong.
2. **Delete the abandoned placeholders.** A create commits its row *before* calling Google, so
   every one of the 27 attempts left a uid-less ``STATE_UNSPECIFIED`` row behind. They carry no
   identity and no history: nothing links to them, no lifecycle event can resolve to them, and
   with no ``expire_time`` the scheduler classifies each one ``renew`` forever.
3. **Reset the failure counters** on rows whose ``last_error`` records this collision, so the
   alert stops and the next real failure is visible again. Scoped to that error text, so a
   genuine unrelated failure keeps its count.

Safe twice: after a successful run there are no ``DELETED`` rows holding a uid, no uid-less
placeholders and no matching ``last_error``, so a second run matches nothing. Never raises — an
exception in ``post_model_sync`` aborts the migrate *after* the code has been reset to the new
release, which leaves the site running new code against a half-migrated schema.
"""

import frappe

DOCTYPE = "Chat Event Subscription"
STATE_DELETED = "DELETED"
STATE_UNSPECIFIED = "STATE_UNSPECIFIED"


def execute():
	if not frappe.db.table_exists(DOCTYPE):
		# The Chat module never reached model sync on this site. Nothing to repair.
		return
	if not frappe.db.has_column(DOCTYPE, "subscription_uid"):
		return

	released = _release_superseded_uids()
	deleted = _delete_abandoned_placeholders()
	reset = _reset_collision_failures()

	if released or deleted or reset:
		# stdout is the deploy log; a patch that only whispers into the Error Log is
		# indistinguishable from one that did nothing.
		print(
			f"chat subscriptions: released {released} superseded uid(s), "
			f"deleted {deleted} abandoned placeholder(s), reset {reset} failure counter(s)"
		)


def _release_superseded_uids() -> int:
	"""Clear ``subscription_uid`` on rows already marked ``DELETED``. The deadlock itself."""
	rows = frappe.get_all(
		DOCTYPE,
		filters={"state": STATE_DELETED, "subscription_uid": ["is", "set"]},
		fields=["name"],
		limit=1000,
	)
	for row in rows:
		frappe.db.set_value(DOCTYPE, row["name"], {"subscription_uid": None}, update_modified=False)
	return len(rows)


def _delete_abandoned_placeholders() -> int:
	"""Drop uid-less ``STATE_UNSPECIFIED`` rows — the per-attempt leak of a failing create."""
	rows = frappe.get_all(
		DOCTYPE,
		filters={
			"state": STATE_UNSPECIFIED,
			"subscription_uid": ["is", "not set"],
			"expire_time": ["is", "not set"],
		},
		fields=["name"],
		limit=1000,
	)
	deleted = 0
	for row in rows:
		try:
			frappe.delete_doc(DOCTYPE, row["name"], ignore_permissions=True, delete_permanently=True)
			deleted += 1
		except Exception:
			# One stubborn row is not worth aborting a migrate for; it is inert either way.
			frappe.log_error(
				title="chat subscriptions: could not delete an abandoned placeholder",
				message=frappe.get_traceback(),
			)
	return deleted


def _reset_collision_failures() -> int:
	"""Zero the counters that only ever counted this collision, so the alert stops."""
	rows = frappe.get_all(
		DOCTYPE,
		filters={
			"consecutive_failures": [">", 0],
			"last_error": ["like", "%subscription_uid%"],
		},
		fields=["name"],
		limit=1000,
	)
	for row in rows:
		frappe.db.set_value(
			DOCTYPE,
			row["name"],
			{"consecutive_failures": 0, "last_error": ""},
			update_modified=False,
		)
	return len(rows)
