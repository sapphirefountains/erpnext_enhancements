"""Who a Critical non-conformance reaches, and when it reaches them again — WI-075 sub-phase G.

The build spec asks for one thing here and it is unusually specific: *Critical severity alerts
the PM, Production Manager and President, with acknowledgment timestamped per recipient.* Not
"notify the team" — a named list, and a record of who actually saw it.

The companion paper explains why the President is on that list at all, and it is the same
argument that put them on the Scope of Work: *the President should see every project at the one
moment ambiguity is highest, and be alerted immediately the one time a failure is serious enough
to matter. Everything in between stays at the PM level by design, not by oversight.*

Two properties this module exists to protect

**An alert must survive a deploy.** Merging to `main` `FLUSHDB`s the queue redis and destroys
every pending background job, silently. So "we enqueued it" is never evidence anybody was told.
:func:`renag_due` treats a recipient row that was never actually notified as due *immediately*,
which is what lets an hourly sweep re-drive alerts a deploy threw away.

**A nag must not become noise.** A sweep that re-sends every hour trains people to filter it,
and a filtered Critical alert is worse than none — so a recipient is re-nagged at most once per
calendar day, the same by-date dedupe the hand-off escalation uses.

Imports only ``datetime``. Deciding who gets paged is not something to leave unasserted until
somebody next runs a bench, and there is no Frappe integration-test job in CI.
"""

from datetime import datetime, timedelta

#: Never page these, whatever a role table says.
NEVER_NOTIFY = ("Administrator", "Guest")

#: How the three recipients are labelled on the acknowledgement row. Stored as text rather than
#: as a role link, because the label records *why this person was told* at the time — and the
#: role holders will change.
ROLE_PM = "Project Manager"
ROLE_PRODUCTION = "Production Manager"
ROLE_PRESIDENT = "President"


def _as_datetime(value):
	"""Accept a datetime or a Frappe-style string; return None for anything empty."""
	if not value:
		return None
	if isinstance(value, datetime):
		return value
	text = str(value).strip()
	if not text:
		return None
	for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
		try:
			return datetime.strptime(text, fmt)
		except ValueError:
			continue
	return None


def alert_is_due(severity, critical_severity, already_sent_on):
	"""Whether this NCR should dispatch a Critical alert now.

	Idempotent on ``already_sent_on``: re-saving a Critical NCR must not mail anybody twice.
	Re-nagging is :func:`renag_due`'s job, and it works per recipient rather than per document.
	"""
	if severity != critical_severity:
		return False
	return _as_datetime(already_sent_on) is None


def dedupe_recipients(candidates):
	"""``[(user, role_label), ...]`` in order, one row per person, Administrator dropped.

	One person can hold two of the three roles — on a site with nineteen enabled users that is
	the expected case, not the edge case. They get **one** row, labelled with the first reason
	they qualified, because two rows would mean two emails and an acknowledgement that could be
	half-done.
	"""
	seen, out = set(), []
	for user, label in candidates or []:
		if not user or user in NEVER_NOTIFY or user in seen:
			continue
		seen.add(user)
		out.append((user, label))
	return out


def renag_due(notified_on, acknowledged_on, now, sla_hours, last_reminded_on=None):
	"""Whether an unacknowledged recipient should be chased again.

	* Acknowledged — never.
	* **Never actually notified — immediately.** This is the deploy case: the enqueue was
	  destroyed by a `FLUSHDB` and the row is sitting there claiming somebody was told.
	* Inside the SLA — no.
	* Already chased today — no. At most one nag per calendar day; hourly re-sends train people
	  to filter exactly the message that must not be filtered.
	"""
	if _as_datetime(acknowledged_on):
		return False

	now = _as_datetime(now)
	if now is None:
		return False

	notified = _as_datetime(notified_on)
	if notified is None:
		return True

	if now - notified < timedelta(hours=max(int(sla_hours or 0), 1)):
		return False

	reminded = _as_datetime(last_reminded_on)
	if reminded and reminded.date() == now.date():
		return False
	return True


def outstanding(rows, now, sla_hours):
	"""The acknowledgement rows that owe somebody a chase, in row order."""
	return [
		row
		for row in rows or []
		if renag_due(
			_field(row, "notified_on"),
			_field(row, "acknowledged_on"),
			now,
			sla_hours,
			_field(row, "last_reminded_on"),
		)
	]


def acknowledgement_state(rows):
	"""``(acknowledged, total)`` — what a list view or a sweep wants to know at a glance."""
	rows = rows or []
	return sum(1 for r in rows if _as_datetime(_field(r, "acknowledged_on"))), len(rows)


def may_acknowledge(rows, user):
	"""Whether ``user`` is on the notified list.

	An acknowledgement from somebody who was never told is not evidence of anything, so the
	endpoint refuses it rather than recording a reassuring row.
	"""
	return any(_field(row, "user") == user for row in rows or [])


def _field(row, name):
	if isinstance(row, dict):
		return row.get(name)
	return getattr(row, name, None)
