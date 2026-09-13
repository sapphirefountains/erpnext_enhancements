# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Who was qualified to do what, on a given day. The version you hand an insurer.

**This report re-derives every state from dates and never reads a ``status``
column.** That single rule is what makes it correct, and it is not a stylistic
preference. Every derived-status write in both modules goes through
``db_set(..., update_modified=False)``, which never reaches ``save_version()`` — so a
stored status is a fact about *today*, ``track_changes = 1`` recorded nothing about
how it got there, and reading one to answer a question about March silently restates
the present over the past.

Four consequences worth stating, because each inverts an instinct:

* **The roster of people comes from ``date_of_joining`` / ``relieving_date``, not
  ``status = "Active"``.** Every other enumeration in this app filters on Active,
  which silently omits leavers — and after an incident, leavers are precisely who an
  insurer asks about. ``Employee.status`` carries no transition date and therefore
  cannot answer a historical question at all.
* **``Expiring`` counts as HELD.** The credential register's 90-day horizon is a
  warning, not a lapse, and the horizon is relative to the *as-of* date rather than
  to today. A card expiring on 15 April was held on 1 March and is expired now; both
  the verdict and the wording have to be recomputed against the as-of date.
* **``Supervised Only`` is not competence.** It shares a doctype, a docstatus and a
  date stamp with ``Competent``, so a query filtering only on ``docstatus = 1``
  reports somebody as cleared to work alone on the strength of a job they did with
  help. It is rendered as its own outcome, never folded in.
* **A fact that cannot be reconstructed says so.** Position and tier have no
  effective-from date anywhere, so this report *refuses* to state a historical rung
  rather than printing today's. Same for a revocation or a restriction transition
  whose date the backfill could not prove. An empty cell would read as "nothing to
  report"; the words "not reconstructible" read as what they are.

It refuses to render rather than rendering blank — an empty roster reads as "everyone
is clear", which is the most dangerous thing this page could say.
"""

import frappe
from frappe import _
from frappe.utils import add_days, getdate, today

from erpnext_enhancements.training.authority import DELEGATE, OBSERVED, TIER

CREDENTIAL = "Employee Credential"
SIGNOFF = "Training Signoff"
COMPLETION = "Training Completion"
RESTRICTION = "Work Restriction"
POLICY = "Policy Acknowledgement"

#: The credential register's warning window. Relative to the AS-OF date, not today.
EXPIRY_HORIZON_DAYS = 90

NOT_RECONSTRUCTIBLE = _("not reconstructible")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	as_of = getdate(filters.get("as_of") or today())

	people = _people_on(as_of, filters)
	if not people:
		return _columns(), [], _refusal(as_of, filters)

	rows = []
	for person in people:
		rows.extend(_rows_for(person, as_of))

	if not rows:
		return _columns(), [], _nothing_on_file(as_of, len(people))

	return _columns(), rows, _preamble(as_of, len(people))


# --------------------------------------------------------------------- the people


def _people_on(as_of, filters):
	"""Everyone employed on ``as_of`` — including people who have since left.

	``Employee.status`` is deliberately not consulted. It has no transition date, so
	it cannot place anybody relative to a past day; joining and relieving dates can,
	and they are core ERPNext fields this app has never used.
	"""
	conditions = {"date_of_joining": ["<=", as_of]}
	if filters.get("employee"):
		conditions["name"] = filters.get("employee")
	if filters.get("department"):
		conditions["department"] = filters.get("department")

	rows = frappe.get_all(
		"Employee",
		filters=conditions,
		fields=[
			"name",
			"employee_name",
			"user_id",
			"department",
			"designation",
			"date_of_joining",
			"relieving_date",
		],
		order_by="employee_name asc",
	)
	# Filtered in Python: a `>=` filter on a nullable date silently matches NULLs in
	# Frappe (the coalesce trap), and here NULL means "still employed", which is the
	# opposite of what a naive filter would conclude.
	return [r for r in rows if not r.relieving_date or getdate(r.relieving_date) >= as_of]


# ----------------------------------------------------------------------- the rows


def _rows_for(person, as_of):
	rows = []
	rows.extend(_credential_rows(person, as_of))
	rows.extend(_signoff_rows(person, as_of))
	rows.extend(_restriction_rows(person, as_of))
	if not rows:
		rows.append(
			_row(person, as_of, _("Nothing on file"), "", "", "", _("No record of any kind on this date."))
		)
	return rows


def _credential_rows(person, as_of):
	"""External tickets. Fully reconstructible: three dates and no status read."""
	if not frappe.db.exists("DocType", CREDENTIAL):
		return []
	has_revoked = frappe.get_meta(CREDENTIAL).has_field("revoked_on")
	fields = ["name", "credential_type", "issued_on", "expires_on"] + (
		["revoked_on"] if has_revoked else []
	)
	out = []
	for row in frappe.get_all(
		CREDENTIAL, filters={"employee": person.name}, fields=fields, order_by="issued_on asc"
	):
		if not row.issued_on or getdate(row.issued_on) > as_of:
			continue
		if row.get("revoked_on") and getdate(row.revoked_on) <= as_of:
			continue
		if row.expires_on and getdate(row.expires_on) < as_of:
			continue

		# Expiring is HELD. The horizon runs from the as-of date, so a card that was
		# inside its warning window in March reads that way in March.
		verdict = _("Held")
		if row.expires_on and getdate(row.expires_on) <= getdate(add_days(as_of, EXPIRY_HORIZON_DAYS)):
			verdict = _("Held — expiring")

		out.append(
			_row(
				person,
				as_of,
				_("Credential"),
				row.credential_type or row.name,
				verdict,
				frappe.format(row.expires_on, {"fieldtype": "Date"}) if row.expires_on else _("no expiry"),
				"",
			)
		)
	return out


def _signoff_rows(person, as_of):
	"""Attestations. Keyed on ``attested_on``, never on ``signed_on``.

	``signed_on`` is when the learner RAISED the request; using it would report
	somebody as competent from the moment they asked to be. Rows predating v1.396.0
	carry no ``attested_on`` and are reported as not reconstructible.

	**Who attested is read through ``authority_basis``, not off one field.** See
	:func:`_attestation_note`: a submitted sign-off carries two people, and which of
	them did the attesting depends on the basis. Until v1.424.0 this printed
	*Attested by* whoever was in ``supervisor_name_at_time``, which the snapshot had
	frozen from the login that pressed submit — so under a delegate basis the sheet
	named the typist.
	"""
	if not frappe.db.exists("DocType", SIGNOFF) or not person.user_id:
		return []
	meta = frappe.get_meta(SIGNOFF)
	dated = meta.has_field("attested_on")
	fields = ["name", "course", "outcome", "signed_on"]
	for extra in (
		"attested_on",
		"authority_basis",
		"supervisor_name_at_time",
		"supervisor_position_title",
		"recorded_by_at_time",
		"recorded_by_position_title",
	):
		if meta.has_field(extra):
			fields.append(extra)

	out = []
	for row in frappe.get_all(
		SIGNOFF, filters={"user": person.user_id, "docstatus": 1}, fields=fields
	):
		attested = row.get("attested_on") if dated else None
		if attested and getdate(attested) > as_of:
			continue

		if not attested:
			note = _(
				"Attested before v1.396.0, when only the request date was stored. "
				"Whether it was in force on this date is {0}."
			).format(NOT_RECONSTRUCTIBLE)
			out.append(_row(person, as_of, _("Sign-off"), row.course or row.name, _("Unknown"), "", note))
			continue

		attestation = _attestation_note(row)

		# Supervised Only is NOT competence and must never read as cleared to work
		# alone. It shares doctype, docstatus and date stamp with Competent.
		verdict = {
			"Competent": _("Competent — may work alone"),
			"Supervised Only": _("Supervised only — NOT cleared to work alone"),
		}.get(row.outcome, row.outcome or _("Unknown"))

		out.append(
			_row(
				person,
				as_of,
				_("Sign-off"),
				row.course or row.name,
				verdict,
				frappe.format(getdate(attested), {"fieldtype": "Date"}),
				attestation,
			)
		)
	return out


def _who(name, rung):
	"""``Name (Rung)``, or just the name when the rung was not recorded."""
	name = (name or "").strip()
	rung = (rung or "").strip()
	return f"{name} ({rung})" if name and rung else name


def _attestation_note(row):
	"""Who attested — and, when that is not who typed it up, who typed it up.

	A submitted sign-off carries **two** people, and ``authority_basis`` is the field
	that says which of them did the attesting. Reading either identity on its own
	misnames somebody on a document handed to an insurer, in one direction or the
	other:

	* ``Observed Supervisor`` — the same person both times, by definition
	  (``supervisor_user == the session user`` is what selects this basis). Name them
	  once. Both submitted sign-offs on this site are this.
	* ``Manager Delegate`` — a Training Manager typing up a verdict relayed over the
	  radio. **The named supervisor attested**; the manager only recorded it. Until
	  v1.424.0 the snapshot froze the supervisor fields from the session login, so
	  this sheet printed the typist as the attester and nothing on the row
	  disagreed — every supervisor field held the same wrong person.
	* ``Position Tier`` — the exact inverse, which is why swapping the two fields
	  would not have been a fix. Here the recorder signs on **their own** rung and
	  the named supervisor is only the address the request was routed to. On this
	  site that is the live arrangement: the one Senior Technician is the
	  ``reports_to`` of none of the four Junior Technicians, so every request he
	  signs is addressed to the Project Manager.

	Rows with no basis are pre-v1.386.0 and carry whatever was frozen at the time;
	they fall through to the plain form and read exactly as they did before, which
	for an observed sign-off is correct.
	"""
	named = _who(row.get("supervisor_name_at_time"), row.get("supervisor_position_title"))
	recorder = _who(row.get("recorded_by_at_time"), row.get("recorded_by_position_title"))
	basis = row.get("authority_basis") or ""

	if basis == TIER and recorder:
		if named and named != recorder:
			return _("Attested by {0} on a position-tier basis; the request was addressed to {1}.").format(
				recorder, named
			)
		return _("Attested by {0} on a position-tier basis.").format(recorder)

	# Keyed on the basis rather than on whether the two names differ. Under
	# `Observed Supervisor` they are the same human read out of two different tables
	# -- Employee.employee_name and User.full_name -- and a spelling difference
	# between those would otherwise print every ordinary sign-off as though two
	# people had been involved.
	if basis == DELEGATE and named and recorder:
		return _("Attested by {0}, recorded by {1}.").format(named, recorder)
	if basis == OBSERVED and not named:
		return _("Attested by {0}.").format(recorder) if recorder else ""
	if named:
		return _("Attested by {0}.").format(named)
	return _("Recorded by {0}.").format(recorder) if recorder else ""


def _restriction_rows(person, as_of):
	"""Restricted duty in force on the date. What, never why."""
	if not frappe.db.exists("DocType", RESTRICTION) or not person.user_id:
		return []
	from erpnext_enhancements.hr_enhancements.availability import restrictions_covering

	in_force, unknown = restrictions_covering([person.user_id], as_of)
	out = []
	for _user, doc in in_force:
		out.append(
			_row(person, as_of, _("Restriction"), doc.name, _("In force"), "", doc.summary() or "")
		)
	for _user, doc in unknown:
		out.append(
			_row(
				person,
				as_of,
				_("Restriction"),
				doc.name,
				_("Unknown"),
				"",
				_("Ended at an unrecorded date; whether it applied on this date is {0}.").format(
					NOT_RECONSTRUCTIBLE
				),
			)
		)
	return out


def _row(person, as_of, kind, what, verdict, valid_until, note):
	return {
		"employee": person.name,
		"employee_name": person.employee_name,
		"employed": _("left {0}").format(frappe.format(person.relieving_date, {"fieldtype": "Date"}))
		if person.relieving_date
		else _("employed"),
		# Position and tier are NOT stated. `Employee.custom_position` is a bare Link
		# with no effective-from date and no history table, so the rung somebody held
		# on a past date cannot be recovered -- and printing today's on a document
		# handed to an insurer would be a claim nobody can support.
		"position_as_of": NOT_RECONSTRUCTIBLE,
		"kind": kind,
		"what": what,
		"verdict": verdict,
		"valid_until": valid_until,
		"note": note,
	}


# -------------------------------------------------------------------- the framing


def _columns():
	return [
		{"fieldname": "employee", "label": _("Employee"), "fieldtype": "Link", "options": "Employee", "width": 120},
		{"fieldname": "employee_name", "label": _("Name"), "fieldtype": "Data", "width": 170},
		{"fieldname": "employed", "label": _("Employment"), "fieldtype": "Data", "width": 110},
		{"fieldname": "position_as_of", "label": _("Position on date"), "fieldtype": "Data", "width": 140},
		{"fieldname": "kind", "label": _("Record"), "fieldtype": "Data", "width": 110},
		{"fieldname": "what", "label": _("What"), "fieldtype": "Data", "width": 220},
		{"fieldname": "verdict", "label": _("On this date"), "fieldtype": "Data", "width": 240},
		{"fieldname": "valid_until", "label": _("Valid until"), "fieldtype": "Data", "width": 110},
		{"fieldname": "note", "label": _("Note"), "fieldtype": "Data", "width": 380},
	]


def _preamble(as_of, count):
	return _(
		"<b>Qualifications as of {0}</b> for {1} employed on that date, including anyone who has "
		"since left.<br>Every state here is re-derived from stored dates. Position and tier are "
		"shown as <i>{2}</i> because no effective-from date is recorded for them anywhere — this "
		"report will not print today's rung against a past date."
	).format(frappe.format(as_of, {"fieldtype": "Date"}), _("{0} people").format(count), NOT_RECONSTRUCTIBLE)


def _nothing_on_file(as_of, count):
	return _(
		"<b>{0} people were employed on {1}, and not one carries a credential, an attestation or a "
		"restriction on that date.</b><br>That is a finding, not an empty report."
	).format(count, frappe.format(as_of, {"fieldtype": "Date"}))


def _refusal(as_of, filters):
	"""Refuse to render rather than render blank.

	An empty roster reads as "everyone is clear", which is the most dangerous
	sentence this page could produce. Say which question was asked and why it has no
	answer instead.
	"""
	if filters.get("employee"):
		return _(
			"<b>{0} was not employed on {1}</b>, so there is nothing to report for that date."
		).format(filters.get("employee"), frappe.format(as_of, {"fieldtype": "Date"}))
	if filters.get("department"):
		return _("<b>Nobody was in {0} on {1}.</b>").format(
			filters.get("department"), frappe.format(as_of, {"fieldtype": "Date"})
		)
	return _(
		"<b>Nobody was employed on {0}.</b> Check the date — this is derived from joining and "
		"relieving dates, not from who is active now."
	).format(frappe.format(as_of, {"fieldtype": "Date"}))
