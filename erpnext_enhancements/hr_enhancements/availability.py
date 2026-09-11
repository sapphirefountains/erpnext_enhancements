# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Is this person actually available on that day?

Two reasons somebody should not be sent, neither of which the app could see until
now: they have **approved time off**, and they are on **restricted duty**. Both
were already recorded; nothing read them at the moment of scheduling, so a visit
could be booked for a technician who had an approved day off, and nobody found out
until the morning.

**Warn, never block**, and the reasoning is copied from `training/compliance.py`
deliberately rather than re-argued: by the time a maintenance record is being
saved there is often already a truck moving, and refusing the form does not stop
the work — it moves the work off the books, where nobody can see it at all. So
this returns sentences for somebody to read, and the caller decides.

**Time off is checked on `Approved` only.** A `Requested` day is not a day off
yet; warning about one would train people to ignore the warning, which costs more
than the case it catches.

Nothing here is cached. Both reads are one indexed query against small tables, and
a cached answer to "is he off on Thursday" is wrong the moment somebody approves a
request.
"""

import frappe
from frappe import _
from frappe.utils import getdate, today

TIME_OFF = "Time Off Request"
RESTRICTION = "Work Restriction"
APPROVED = "Approved"


def reasons_unavailable(users, on_date=None):
	"""``{user: [sentence, ...]}`` for everybody with a reason not to be sent.

	Users with nothing against them are **absent from the result** rather than
	present with an empty list, so a caller can treat the dict as "the problems"
	without filtering. Never raises: every caller is an advisory hanging off a
	document save that matters more than this does.
	"""
	users = [u for u in dict.fromkeys(u for u in (users or []) if u)]
	if not users:
		return {}
	when = getdate(on_date or today())

	out = {}
	for user, sentence in _time_off(users, when).items():
		out.setdefault(user, []).append(sentence)
	for user, sentence in _restrictions(users, when).items():
		out.setdefault(user, []).append(sentence)
	return out


def _time_off(users, when):
	"""Approved time off covering *when*.

	Two half-open comparisons rather than a BETWEEN, because a request is a range
	and the visit is a point inside it.
	"""
	if not frappe.db.exists("DocType", TIME_OFF):
		return {}
	found = {}
	for row in frappe.get_all(
		TIME_OFF,
		filters={
			"user": ["in", users],
			"status": APPROVED,
			"from_date": ["<=", when],
			"to_date": [">=", when],
		},
		fields=["user", "from_date", "to_date", "half_day"],
	):
		if row.half_day:
			found[row.user] = _("has an approved half day off on {0}").format(when)
		elif getdate(row.from_date) == getdate(row.to_date):
			found[row.user] = _("has approved time off on {0}").format(when)
		else:
			found[row.user] = _("has approved time off from {0} to {1}").format(
				row.from_date, row.to_date
			)
	return found


def _restrictions(users, when):
	"""Active restricted duty covering *when*.

	The **summary only** — what they may not do. The record has nowhere to store a
	reason and this must never grow one either: the sentence that reaches a
	dispatcher's screen is the one most likely to be pasted into a message.
	"""
	if not frappe.db.exists("DocType", RESTRICTION):
		return {}
	found = {}
	for row in frappe.get_all(
		RESTRICTION,
		filters={"user": ["in", users], "status": "Active", "from_date": ["<=", when]},
		fields=["name", "user", "to_date"],
	):
		# An absent `to_date` is open-ended, not expired. Filtered here rather than
		# in the query because `["or"]` on a nullable date is where the coalesce
		# trap lives -- a `>=` filter silently matches NULLs in Frappe, which would
		# be right by accident here and wrong the next time somebody copies it.
		if row.to_date and getdate(row.to_date) < when:
			continue
		doc = frappe.get_cached_doc(RESTRICTION, row.name)
		summary = doc.summary()
		found[row.user] = (
			_("is on restricted duty: {0}").format(summary)
			if summary
			else _("is on restricted duty")
		)
	return found


def restriction_blocks(user, on_date=None, kinds=()):
	"""Whether any *kind* of restriction applies — for a caller that knows the work.

	``kinds`` are ``Work Restriction`` fieldnames. A confined-space permit asks for
	``no_confined_space``; the truck check asks for ``no_driving``. Returns the
	matching sentences, so the caller can say which restriction stopped it rather
	than reporting a bare refusal.
	"""
	if not user or not kinds or not frappe.db.exists("DocType", RESTRICTION):
		return []
	when = getdate(on_date or today())
	hits = []
	for row in frappe.get_all(
		RESTRICTION,
		filters={"user": user, "status": "Active", "from_date": ["<=", when]},
		fields=["name", "to_date"],
	):
		if row.to_date and getdate(row.to_date) < when:
			continue
		doc = frappe.get_cached_doc(RESTRICTION, row.name)
		if any(doc.get(kind) for kind in kinds):
			hits.append(doc.summary())
	return hits


# ------------------------------------------------------------------ advisories


def _swallow(fn):
	"""Gate + swallow, the same shape as ``training/compliance.py::_advisory``.

	Copied rather than imported so the two advisories cannot be switched off by
	each other's setting — a scheduling warning and a certification warning are
	different decisions, and somebody muting one should not silently mute the
	other. The swallow is the point: a bug in a *warning* must never be able to
	fail a dispatcher's save.
	"""
	import functools

	@functools.wraps(fn)
	def wrapper(*args, **kwargs):
		try:
			flags = frappe.flags
			if flags.in_migrate or flags.in_install or flags.in_patch or flags.in_import:
				return None
			return fn(*args, **kwargs)
		except Exception:
			frappe.log_error(
				f"availability.{fn.__name__} failed\n{frappe.get_traceback()}", "HR availability"
			)
		return None

	return wrapper


@_swallow
def warn_unavailable_technician(doc, method=None):
	"""``Sapphire Maintenance Record`` validate — is this tech actually free?

	Runs beside `compliance.warn_uncertified_technician`, which answers a different
	question about the same person: that one asks *may* they do the work, this one
	asks *can* they be there. Two hooks rather than one function, because the two
	are switched on and off independently and a site may well want one without the
	other.

	Checked against the **scheduled** date first and the actual visit date second.
	A record being written on the day carries `visit_date`; one being planned
	carries only `scheduled_visit_date`, and that is the case worth catching —
	a warning that only appears once the truck has arrived is a warning nobody can
	act on.
	"""
	from frappe.utils import cint

	if cint(getattr(doc, "docstatus", 0)) == 2:
		return
	technician = getattr(doc, "technician", None)
	if not technician:
		return
	when = getattr(doc, "scheduled_visit_date", None) or getattr(doc, "visit_date", None)
	if not when:
		return

	reasons = reasons_unavailable([technician], when)
	sentences = reasons.get(technician) or []
	if not sentences:
		return

	who = frappe.db.get_value("User", technician, "full_name") or technician
	frappe.msgprint(
		_("{0} {1}.").format(frappe.bold(who), "; ".join(str(s) for s in sentences)),
		title=_("Check who is going"),
		indicator="orange",
	)


@frappe.whitelist()
def unavailable_on(on_date=None, users=None):
	"""Who is unavailable on a day, and why. For a board or a scheduler.

	Staff only, by EMPLOYMENT rather than by role — every customer contact on this
	site holds a login, and who is off is both none of their business and a rough
	map of the company's week. Same gate as `timeoff.who_is_out`.
	"""
	me = frappe.session.user
	if not me or me == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	if not frappe.db.exists("Employee", {"user_id": me, "status": "Active"}):
		frappe.throw(_("Only staff can see who is unavailable."), frappe.PermissionError)

	if isinstance(users, str):
		import json as _json

		try:
			users = _json.loads(users)
		except (TypeError, ValueError):
			users = [users]
	if not users:
		users = [
			u
			for u in frappe.get_all(
				"Employee", filters={"status": "Active"}, pluck="user_id"
			)
			if u
		]
	return reasons_unavailable(users, on_date)


# --------------------------------------------------------------- driving


#: `Credential Type` names that mean "may drive". Matched case-insensitively on
#: the whole name, never as a substring: a substring match on "licence" would
#: also catch a contractor licence, a pesticide applicator licence and anything
#: else somebody names that way, and reporting a technician as unable to drive
#: because their pesticide ticket lapsed is the kind of wrong that gets the whole
#: warning switched off.
DRIVING_CREDENTIALS = ("driver's license", "drivers license", "driving licence", "driver licence")


def may_drive(employee, on_date=None):
	"""``(ok, reason)`` for whether *employee* may drive a company vehicle.

	``ok`` is True when nothing says otherwise, and that default matters: this is
	not a register of who has been authorised, it is a check for things that would
	stop somebody. A missing licence record means **nobody has filed one**, which
	is a gap to chase and not the same statement as "this person cannot drive".
	Treating absence as refusal would flag fifteen of sixteen people on the day it
	shipped and be muted by the end of the week.

	Two things do stop somebody: an expired or revoked driving credential that IS
	on file, and an active `no_driving` restriction.
	"""
	if not employee:
		return True, None

	user = frappe.db.get_value("Employee", employee, "user_id")
	blocked = restriction_blocks(user, on_date, kinds=("no_driving",)) if user else []
	if blocked:
		return False, _("on restricted duty: {0}").format(blocked[0])

	if not frappe.db.exists("DocType", "Employee Credential"):
		return True, None

	held = frappe.get_all(
		"Employee Credential",
		filters={"employee": employee, "credential_type": ["in", _driving_credential_types()]},
		fields=["name", "status", "expires_on"],
		order_by="expires_on desc",
	)
	if not held:
		# Nothing on file. See the docstring: a gap, not a refusal.
		return True, None
	if any(row.status in ("Valid", "Expiring") for row in held):
		return True, None
	worst = held[0]
	return False, _("driving licence is {0}").format((worst.status or "not current").lower())


def _driving_credential_types():
	"""Credential Types whose name means "may drive", or a sentinel that matches
	nothing.

	The sentinel matters: an empty ``in`` list is a filter Frappe drops, which
	would turn the query into "every credential this person holds" and report
	somebody as unable to drive because their first aid card lapsed.
	"""
	if not frappe.db.exists("DocType", "Credential Type"):
		return ["__none__"]
	names = [
		row
		for row in frappe.get_all("Credential Type", pluck="name")
		if (row or "").strip().casefold() in DRIVING_CREDENTIALS
	]
	return names or ["__none__"]


@_swallow
def warn_driver_cannot_drive(doc, method=None):
	"""``Fleet Vehicle`` validate — the licence the credential register already knows."""
	driver = getattr(doc, "assigned_driver", None)
	if not driver:
		return
	ok, reason = may_drive(driver)
	if ok:
		return
	who = frappe.db.get_value("Employee", driver, "employee_name") or driver
	frappe.msgprint(
		_("{0} {1}.").format(frappe.bold(who), reason),
		title=_("Check the driver"),
		indicator="orange",
	)
