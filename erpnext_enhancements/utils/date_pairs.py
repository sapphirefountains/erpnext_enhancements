# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Warn, never block, when a start/end date pair is out of order (form kit P2-A).

A person who types an end date earlier than the start date has made a typo far more often
than a decision, and a hard stop on every such pair would also stop the person who is
mid-edit (changing the start, about to change the end) from saving at all. So an out-of-order
pair gets one orange pop-up naming both fields and what they hold, and the save goes ahead.

:data:`PAIRS` is the registry: ``{doctype: [{"fields", "labels", "message"}]}`` where
``fields`` must be in **non-decreasing** order. A pair is the two-field case (start <= end);
the Opportunity's event schedule is a four-field sequence (delivery <= setup <= event <= take
down). Fields that are blank are skipped, so a sequence with gaps compares the ones that are
set, in order. Equal is fine: setup and delivery can be the same minute.

:func:`warn_out_of_order` is the ``validate`` hook. ``hooks.py`` wires it to every doctype in
the registry and ``tests/test_date_pairs.py`` fails the build if one is missing.

--------------------------------------------------------------------------------------
Only pairs nothing validates today
--------------------------------------------------------------------------------------

A pair that already throws needs no warning on top. These are validated elsewhere, so they are
**not** registered here:

* ``Project`` ``expected_start_date``/``expected_end_date`` and ``actual_*``: ERPNext's
  ``validate_from_to_dates``. ``Task`` ``exp_*`` and ``act_*``: the same.
* ``Time Off Request`` and ``Work Restriction`` ``from_date``/``to_date``,
  ``Travel Trip`` ``start_date``/``end_date``, ``Employee Credential``,
  ``Asset Booking`` ``from_datetime``/``to_datetime``, ``Asset Out of Service``,
  ``Time Correction Request``: each controller throws.
* ``Rental Booking``'s whole schedule (delivery, setup, event start/end, take down):
  ``rental_rules.schedule_problems``, which throws.

And these were considered and left out on purpose:

* **Imported or generated** pairs, which no person types: Ad Campaign (synced from the ad
  platforms), Quality Review and Quality Meeting periods and Subcontractor Scorecard periods
  (derived by the build), Calendar Time Off Review, Marketing Sync Log.
* **Child-table rows**, which this registry does not reach (it compares fields of the saved
  document itself): Trip Accommodation check-in/check-out, Trip Traveler, Contract Rate Line
  and Planner Resource Work Pattern (that one is validated by its controller).
* **Flights**: departure and arrival are local to different time zones, so an arrival that
  reads earlier than the departure is often correct.
* **Stamps the system sets**, such as ``posted_on``, ``opened_on``, ``reported_on``.

Everything here is a warning that must never become a failure: it swallows its own errors, runs
only in a web request by a signed-in person, and stays out of imports, installs, migrations and
patches, where nobody is there to read it.
"""

from itertools import pairwise

import frappe
from frappe import _

from erpnext_enhancements.utils.popup_once import first_time

#: The event schedule on an Opportunity and on the Project it becomes.
_EVENT_FIELDS = [
	"custom_delivery_date_time",
	"custom_setup_date_time",
	"custom_event_date_time",
	"custom_take_down_date_time",
]
_EVENT_LABELS = ["Delivery Date Time", "Setup Date Time", "Event Date Time", "Take Down Date Time"]
_EVENT_MESSAGE = "The event schedule is out of order: delivery, then setup, then the event, then take down."

_REQUESTED = {
	"fields": ["custom_project_start_date", "custom_project_end_date"],
	"labels": ["Requested Start Date", "Requested End Date"],
	"message": "The requested end date is before the requested start date.",
}
_EVENTS = {"fields": _EVENT_FIELDS, "labels": _EVENT_LABELS, "message": _EVENT_MESSAGE}

PAIRS = {
	"Opportunity": [_REQUESTED, _EVENTS],
	"Project": [_REQUESTED, _EVENTS],
	"Lead": [
		{
			"fields": ["custom_rental_start_date", "custom_rental_end_date"],
			"labels": ["Rental Start Date", "Rental End Date"],
			"message": "The rental end date is before the rental start date.",
		},
	],
	# Task has ERPNext's own exp_*/act_* dates, validated by core. These two are the planner's.
	"Task": [
		{
			"fields": ["custom_start_datetime", "custom_end_datetime"],
			"labels": ["Start Datetime", "End Datetime"],
			"message": "The end is before the start.",
		},
	],
	"Sapphire Maintenance Contract": [
		{
			"fields": ["start_date", "end_date"],
			"labels": ["Start Date", "End Date"],
			"message": "The contract ends before it starts.",
		},
	],
	"Training Batch": [
		{
			"fields": ["start_date", "end_date"],
			"labels": ["Start Date", "End Date"],
			"message": "The batch ends before it starts.",
		},
	],
	"Project Contract": [
		{
			"fields": ["rental_start_date", "rental_end_date"],
			"labels": ["Rental Start Date", "Rental End Date"],
			"message": "The rental ends before it starts.",
		},
		{
			"fields": ["mobilization_date", "substantial_completion_date", "final_completion_date"],
			"labels": ["Mobilization / Start Date", "Substantial Completion Date", "Final Completion Date"],
			"message": "The project dates are out of order: mobilization, then substantial completion, then final completion.",
		},
	],
	"Managed Device": [
		{
			"fields": ["purchase_date", "warranty_expiry_date"],
			"labels": ["Purchase Date", "Warranty Expiry"],
			"message": "The warranty expires before the device was purchased.",
		},
	],
}

#: The dotted path ``hooks.py`` registers on ``validate`` for every doctype in :data:`PAIRS`.
HOOK = "erpnext_enhancements.utils.date_pairs.warn_out_of_order"


def warn_out_of_order(doc, method=None):
	"""``validate`` hook: one orange pop-up per person per record per problem. Never raises."""
	try:
		pairs = PAIRS.get(getattr(doc, "doctype", None))
		if not pairs or not _should_run(doc):
			return
		found = []
		for pair in pairs:
			for problem in find_problems(doc, pair):
				if first_time(
					"date-pair", doc.doctype, getattr(doc, "name", None) or "new", problem["fingerprint"]
				):
					found.append(problem)
		if found:
			frappe.msgprint(_message(found), title=_("Check the dates"), indicator="orange")
	except Exception:
		_log_quietly(getattr(doc, "doctype", None), getattr(doc, "name", None))


def find_problems(doc, pair):
	"""The consecutive set values of ``pair`` that run backwards.

	Each is ``{"fingerprint", "message", "earlier", "later"}`` where ``earlier``/``later`` are
	``(label, value)`` and the field that comes **first** in the sequence is ``earlier``. Blank
	and unreadable values are skipped, never reported.
	"""
	present = []
	for fieldname, label in zip(pair["fields"], pair["labels"], strict=False):
		raw = getattr(doc, fieldname, None)
		moment = _to_datetime(raw)
		if moment is not None:
			present.append((fieldname, label, raw, moment))

	problems = []
	for earlier, later in pairwise(present):
		if later[3] < earlier[3]:
			problems.append(
				{
					"fingerprint": f"{earlier[0]}={earlier[3].isoformat()}>{later[0]}={later[3].isoformat()}",
					"message": pair["message"],
					"earlier": (earlier[1], earlier[2]),
					"later": (later[1], later[2]),
				}
			)
	return problems


def _to_datetime(value):
	"""A naive ``datetime`` for a set value, else ``None``. ``get_datetime(None)`` is *now*, so blanks never reach it."""
	import datetime

	if value is None or value == "":
		return None
	try:
		moment = frappe.utils.get_datetime(value)
	except Exception:
		return None
	if not isinstance(moment, datetime.datetime):
		return None
	return moment.replace(tzinfo=None)


def _should_run(doc):
	"""A web request by a signed-in person; never an import, install, migration, patch or job.

	``utils/popup_once`` remembers what a person was *shown*. A job that "showed" the warning
	to its service user would use up the one pop-up the next real save is owed, and nobody is
	there to read it anyway. The warning is also meaningless on a canceled document.
	"""
	flags = frappe.flags
	if flags.in_import or flags.in_install or flags.in_migrate or flags.in_patch:
		return False
	if getattr(frappe.local, "request", None) is None:
		return False
	if frappe.session.user in (None, "Guest"):
		return False
	return int(getattr(doc, "docstatus", 0) or 0) != 2


def _display(value):
	"""A value in the print/email style: ``10/14/2026``, or ``10/14/2026 2:00 PM`` with a time."""
	import datetime

	from erpnext_enhancements.utils.date_display import sf_date, sf_datetime

	if isinstance(value, datetime.datetime):
		return sf_datetime(value)
	if isinstance(value, datetime.date) or (isinstance(value, str) and len(value.strip()) <= 10):
		return sf_date(value)
	return sf_datetime(value)


def _message(problems):
	lines = []
	for problem in problems:
		(first_label, first_value), (second_label, second_value) = problem["earlier"], problem["later"]
		what = _("{0} is {1}, but {2} is {3}.").format(
			f"<b>{frappe.utils.escape_html(_(first_label))}</b>",
			_display(first_value),
			f"<b>{frappe.utils.escape_html(_(second_label))}</b>",
			_display(second_value),
		)
		lines.append(f"<p>{frappe.utils.escape_html(_(problem['message']))}<br>{what}</p>")
	lines.append("<p>" + _("Saved as entered. Change the dates if that was a mistake.") + "</p>")
	return "".join(lines)


def _log_quietly(doctype, name):
	try:
		frappe.log_error(
			title="Date pair warning failed",
			message=f"{doctype} {name}\n{frappe.get_traceback()}",
		)
	except Exception:
		pass
