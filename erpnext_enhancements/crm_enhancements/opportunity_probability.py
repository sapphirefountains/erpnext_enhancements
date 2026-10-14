"""Default forecast probability per Opportunity stage (form kit P5.5, TASK-2026-02772).

Nik, 2026-10-13: each open stage has a default probability, and Sales can override it on any
deal. The defaults live in Sales Activity Settings (``stage_probabilities``), seeded with
Qualification 10, Needs Analysis 25, Proposal/Price Quote 50 and Negotiation/Review 75. The
stage is ``Opportunity.status``; ERPNext's ``sales_stage`` reads "Prospecting" on nearly every
deal here and is not used. On Hold deals stay out of the forecast (that is the board's rule,
P6), so On Hold has no default and a deal's probability is left alone while it is on hold.

**The rule, run on every save** (``doc_events["Opportunity"]["validate"]``):

* a deal that **enters a stage** with a default takes that default — unless its probability was
  **set by hand**;
* a person (or an import, or the API) that **changes the probability** marks the deal
  ``custom_probability_by_hand``, and a later stage change keeps their number;
* **unticking** *Probability Set by Hand* puts the deal back on its stage's default at once;
* a **new** deal takes its stage's default unless it arrives with a probability other than
  ERPNext's field default (100), which counts as set by hand.

Changing the probability and the stage in the same save counts as setting it by hand: the
number typed beside the new stage is the one the person meant.

**Defensive, like every doc_event here.** ``doc_events`` fire during ERPNext's own test
bootstrap before this app's custom fields exist, so with no ``custom_probability_by_hand`` field
on the meta it does nothing; an empty or unreadable table also does nothing. It never raises:
a forecast number is not worth a failed save. An unexpected error is logged once per request.

The field is created in code (``patches/add_opportunity_probability_by_hand``, from
:func:`field_definition`) rather than in the fixtures, because the backfill patch must write it
on the same migrate, and patches run before fixtures sync.
"""

import frappe
from frappe.utils import cint, flt

SETTINGS = "Sales Activity Settings"
TABLE = "stage_probabilities"
ROW_DOCTYPE = "Opportunity Stage Probability"
FIELD = "custom_probability_by_hand"

#: Nik, 2026-10-13. What the seed patch writes into the settings table; the table is the truth.
DEFAULTS = {
	"Qualification": 10,
	"Needs Analysis": 25,
	"Proposal/Price Quote": 50,
	"Negotiation/Review": 75,
}

#: ERPNext's own ``default`` for Opportunity.probability (opportunity.json, v16).
ERPNEXT_DEFAULT = 100

_LOGGED_FLAG = "sf_opportunity_probability_logged"


def field_definition():
	"""The custom field, shared by the patch that creates it and the tests."""
	return {
		"fieldname": FIELD,
		"label": "Probability Set by Hand",
		"fieldtype": "Check",
		"insert_after": "probability",
		"default": "0",
		"description": (
			"Ticked when someone types the probability: a stage change then keeps it. "
			"Untick to go back to the stage's default (Sales Activity Settings)."
		),
	}


def stage_defaults():
	"""``{stage: probability}`` from Sales Activity Settings; empty when unset or unreadable."""
	try:
		settings = frappe.get_cached_doc(SETTINGS)
		return {row.stage: flt(row.probability) for row in settings.get(TABLE) or [] if row.stage}
	except Exception:
		return {}


def _same(a, b):
	return abs(flt(a) - flt(b)) < 1e-6


def apply_stage_probability(doc, method=None):
	"""Opportunity ``validate``: the stage default, or the probability a person set. Never raises."""
	try:
		_apply(doc)
	except Exception as exc:
		_log_once(exc)


def _apply(doc):
	meta = getattr(doc, "meta", None)
	if meta is None or not meta.has_field(FIELD):
		return
	defaults = stage_defaults()
	if not defaults:
		return
	status = doc.get("status")
	by_hand = cint(doc.get(FIELD))

	if doc.is_new():
		probability = doc.get("probability")
		if not by_hand and probability not in (None, "") and not _same(probability, ERPNEXT_DEFAULT):
			doc.set(FIELD, 1)
			return
		if not by_hand and status in defaults:
			doc.set("probability", defaults[status])
		return

	before = doc.get_doc_before_save()
	if before is None:
		return
	if not _same(before.get("probability"), doc.get("probability")):
		doc.set(FIELD, 1)
		return
	if cint(before.get(FIELD)) and not by_hand:
		if status in defaults:
			doc.set("probability", defaults[status])
		return
	if by_hand:
		return
	if before.get("status") != status and status in defaults:
		doc.set("probability", defaults[status])


def _log_once(exc):
	try:
		if frappe.flags.get(_LOGGED_FLAG):
			return
		frappe.flags[_LOGGED_FLAG] = True
		frappe.log_error(
			title="Opportunity stage probability skipped",
			message=f"{type(exc).__name__}. The Opportunity saved with its probability unchanged.",
		)
	except Exception:
		pass
