"""Migrated Customer Server Scripts, wired via ``hooks.py``.

Hook wiring (see ``hooks.py``):
  * doc_events["Customer"]["before_save"] -> :func:`set_last_activity`
  * scheduler_events["daily"] -> :func:`customer_inactivity_reminder`

Originally Frappe "Server Script" records stored only in the site DB; now
versioned with the app.
"""

import frappe

# Customer Server Scripts migrated to native doc_events / scheduler_events.


def set_last_activity(doc, method=None):
	"""Source Server Script: "Update Last Activity on Customer Save"
	(Customer, Before Save).

	Stamp the last activity date when a Customer is created or when the save
	actually changes something. The stamp used to be unconditional, which
	manufactured a one-field Version diff out of every no-op re-save (bulk
	edits touching other records' fields, sync/webhook replays writing
	identical values) — turning invisible background writes into visible
	"updated" events. Guarded, a truly no-op ``doc.save()`` has zero field
	changes, so Frappe mints no Version at all. A save whose only change is
	the stamp field itself also skips (a manual edit of the date survives
	instead of being clobbered to today). Wired as a Customer ``before_save``
	doc_event. Mutates ``doc`` in place (the enclosing save persists it); no
	DB write of its own.
	"""
	if not doc.is_new() and not _changed_besides_stamp(doc):
		return
	doc.custom_last_activity_date = frappe.utils.today()


def _changed_besides_stamp(doc):
	"""True when this save changes anything other than the stamp itself.

	Uses the same diff engine Frappe's Version feature uses, so "changed"
	matches exactly what would land in a Version record (scalar edits,
	child-row adds/removes/edits; framework bookkeeping fields like
	``modified`` are already excluded by it).
	"""
	before = doc.get_doc_before_save()
	if before is None:
		# no baseline mid-save (defensive) — keep the legacy always-stamp
		return True

	from frappe.core.doctype.version.version import get_diff

	diff = get_diff(before, doc)
	if not diff:
		return False
	changed = {row[0] for row in (diff.get("changed") or [])}
	changed.discard("custom_last_activity_date")
	return bool(
		changed or diff.get("added") or diff.get("removed") or diff.get("row_changed")
	)


def customer_inactivity_reminder():
	"""Source Server Script: "Customer Inactivity Reminder" (Scheduler Event, Daily).

	Create follow-up ToDos for customers whose reminder period has elapsed. Wired
	as a daily ``scheduler_event``. Only customers flagged as a **Prospect**
	(``custom_prospect`` checked) participate — the checkbox is the master gate so
	follow-up reminders go out for prospect accounts only. The reminder window is
	the Customer's own ``custom_reminder_days`` when positive, else the global
	fallback from the "Sales Activity Settings" Single (``inactivity_threshold``;
	the Single ships with the app since v0.8.0). Set ``custom_reminder_days = -1``
	to opt a customer out entirely (any negative value skips; 0 means "unset"
	because it is the Int default, and falls through to the global fallback).
	Setting the global ``inactivity_threshold`` to 0 disables the fallback
	site-wide. For each enabled Prospect Customer with a set
	``custom_last_activity_date`` whose (last activity + reminder window) is now
	due, inserts an Open ToDo allocated to the account's Reminder Assignee
	(``custom_reminder_assignee`` — typically the account executive), falling back
	to the document owner when unset — unless an Open ToDo for that Customer
	already exists. The ToDo allocation sends Frappe's assignment-notification
	email to that user.

	Two rules since WI-081 (v1.696.0): the window runs from the later of the last
	activity and the last time a follow-up on the Customer was closed or cancelled
	(closing one *is* following up, and used to be undone the next morning); and a
	reminder whose assignee would be Administrator, Guest or a disabled user is not
	written at all (:func:`_can_hold_reminder`).

	Side effects:
		Inserts ToDo documents and commits the DB.
	"""
	default_reminder_days = frappe.utils.cint(
		frappe.db.get_single_value("Sales Activity Settings", "inactivity_threshold")
	)

	customers = frappe.get_all(
		"Customer",
		filters={
			"disabled": 0,
			"custom_prospect": 1,
			"custom_last_activity_date": ["is", "set"],
		},
		fields=[
			"name",
			"customer_name",
			"custom_last_activity_date",
			"custom_reminder_days",
			"custom_reminder_assignee",
			"owner",
		],
	)

	current_date = frappe.utils.get_datetime(frappe.utils.today())
	last_closed = _last_closed_follow_up([c.name for c in customers])

	for customer in customers:
		reminder_days = frappe.utils.cint(customer.custom_reminder_days) or default_reminder_days
		if reminder_days <= 0:
			continue

		# Closing (or cancelling) the last follow-up counts as following up. Without this, a
		# closed reminder was written again the next morning for as long as nobody re-saved the
		# Customer, since closing a ToDo never touches custom_last_activity_date (WI-081).
		last_activity = frappe.utils.getdate(customer.custom_last_activity_date)
		closed_on = last_closed.get(customer.name)
		if closed_on and frappe.utils.getdate(closed_on) > last_activity:
			last_activity = frappe.utils.getdate(closed_on)

		follow_up_date = frappe.utils.get_datetime(frappe.utils.add_days(last_activity, reminder_days))

		if follow_up_date <= current_date:
			existing_todo = frappe.db.exists(
				"ToDo",
				{
					"reference_type": "Customer",
					"reference_name": customer.name,
					"status": "Open",
				},
			)

			assignee = customer.custom_reminder_assignee or customer.owner
			if not existing_todo and _can_hold_reminder(assignee):
				todo_description = f"Follow up with {customer.customer_name} ({customer.name})"
				frappe.get_doc(
					{
						"doctype": "ToDo",
						"description": todo_description,
						"status": "Open",
						# ToDo's assignee field is allocated_to (the source server
						# script's assigned_to never existed on v16 ToDo and was
						# silently dropped). Route to the account's Reminder Assignee
						# (custom_reminder_assignee) so prospect follow-ups reach the
						# chosen account executive; fall back to the document owner
						# when it is left blank.
						"allocated_to": assignee,
						"reference_type": "Customer",
						"reference_name": customer.name,
					}
				).insert()

	frappe.db.commit()


def _last_closed_follow_up(customer_names):
	"""{customer: the latest ``modified`` of a Closed or Cancelled ToDo on it}, for the reminder.

	Raw SQL with bound parameters: v16 refuses ``max(modified)`` as a ``get_all`` field string.
	"""
	if not customer_names:
		return {}
	rows = frappe.db.sql(
		"""
		select reference_name, max(modified)
		from `tabToDo`
		where reference_type = 'Customer'
			and status in ('Closed', 'Cancelled')
			and reference_name in %(names)s
		group by reference_name
		""",
		{"names": tuple(customer_names)},
	)
	return {name: modified for name, modified in rows}


def _can_hold_reminder(user):
	"""Whether ``user`` is a real, enabled person a reminder can go to.

	The fallback to the Customer's owner made Administrator the assignee of every imported
	prospect's reminder -- 286 open ToDos on 2026-10-20 that nobody would ever read. Such a
	prospect gets no reminder until someone sets its Reminder Assignee (WI-081).
	"""
	if not user or user in ("Administrator", "Guest"):
		return False
	return bool(frappe.db.get_value("User", user, "enabled"))
