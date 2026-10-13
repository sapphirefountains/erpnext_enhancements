"""A plain-language summary of what the client is agreeing to, printed at the top of the agreement.

Nik, 2026-10-13: "a summary that summarizes what they've agreed to in simple terms and
language would be great."

A template calls ``{{ summary() }}`` right after its title (``project_contract._render_context``
supplies it). The summary is built from this contract's own figures (its plan, prices,
dates, payment method and renewal increase), so it states this client's terms rather than
the template's options, and it sits inside the agreement body. That puts it in front of the
client on the signing page, in the builder's preview, and in the signed copy they are
emailed. It is a reading aid and says so: the box ends by saying the full agreement governs
where the two differ.

**Keep it in step with the templates.** Each sentence restates a clause of the shipped
template it summarizes (the § numbers are in the comments). The templates are editable on
the site, so a clause changed there is not changed here; `tests/test_contract_builder_terms`
checks every fixed figure quoted below against the repo's template text.

Deterministic, and never raises: the drift check at execution compares a fresh unsigned
render with the snapshot the signer was sent, and this runs on the print path.
"""

import frappe
from frappe.utils import cint, escape_html, flt

#: Visit Frequency (§3.1) in words.
FREQUENCY_WORDS = {
	"Weekly": "every week",
	"Bi-Weekly": "every two weeks",
	"Monthly": "once a month",
	"Quarterly": "once every three months",
	"Custom": "on the custom schedule in Section 3",
}

INVOICE_WORDS = {
	"Per Visit": "after each visit",
	"Monthly": "monthly",
	"Quarterly": "quarterly",
	"Annually": "once a year, in advance",
}

TERM_WORDS = {
	"One (1) Year": "one year",
	"Two (2) Years": "two years",
}


def summary_html(doc):
	"""The summary box for ``doc``, or "" for a contract type without one (MSA, SOW, NDA …)."""
	builder = BUILDERS.get(doc.get("template_key"))
	if not builder:
		return ""
	try:
		rows = [row for row in builder(doc) if row]
	except Exception:
		frappe.log_error(title="Contract summary failed", message=frappe.get_traceback())
		return ""
	if not rows:
		return ""
	items = "".join(f"<li><b>{escape_html(label)}</b> {text}</li>" for label, text in rows)
	return (
		'<div class="ct-summary">'
		f"<h2>{escape_html(frappe._('What you are agreeing to, in short'))}</h2>"
		f"<ul>{items}</ul>"
		'<p class="ct-summary-note">'
		+ escape_html(
			frappe._(
				"This summary is here to help you read the agreement. The full agreement below is what "
				"you are signing, and where the two differ, the agreement governs."
			)
		)
		+ "</p></div>"
	)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _money(value):
	currency = frappe.defaults.get_global_default("currency") or "USD"
	return escape_html(frappe.utils.fmt_money(flt(value), currency=currency))


def _date(value):
	return escape_html(frappe.utils.formatdate(value)) if value else ""


def _text(value):
	return escape_html(str(value or "").strip())


def _number(value):
	number = flt(value)
	return str(int(number)) if number == int(number) else f"{number:g}"


def _hours(value):
	number = flt(value)
	return f"{_number(number)} hour" if number == 1 else f"{_number(number)} hours"


def _priced(text, row, suffix=""):
	"""``text`` with "for $X{suffix}" when the option has a price; the agreement prints a blank
	line for one without, so the summary never states a figure the agreement leaves out."""
	return f"{text} for {_money(row.get('price'))}{suffix}" if flt(row.get("price")) else text


def _join(parts):
	parts = [part for part in parts if part]
	if len(parts) <= 1:
		return "".join(parts)
	return ", ".join(parts[:-1]) + " and " + parts[-1]


# ---------------------------------------------------------------------------
# Maintenance Services Agreement
# ---------------------------------------------------------------------------


def _maintenance(doc):
	options = {row.get("option_key"): row for row in (doc.get("service_options") or [])}
	chosen = {key: row for key, row in options.items() if cint(row.get("included"))}
	rows = []

	site = _text(doc.get("site_address")).replace("\n", ", ")
	if site:
		rows.append(("Where:", f"the water features at {site}."))

	# Service Plan Selection, §3.1
	plan = []
	standard = chosen.get("standard")
	if standard:
		how_often = FREQUENCY_WORDS.get(doc.get("visit_frequency"), "on the agreed schedule")
		price = f" at a base rate of {_money(standard.get('price'))} per visit" if flt(standard.get("price")) else ""
		plan.append(f"standard maintenance visits {how_often}{price}")
	if "package" in chosen:
		plan.append(_priced("spring startup and fall winterization", chosen["package"], " a year"))
	else:
		if "startup" in chosen:
			plan.append(_priced("a spring startup", chosen["startup"]))
		if "winterization" in chosen:
			plan.append(_priced("a fall winterization", chosen["winterization"]))
	if "as_needed" in chosen:
		as_needed = chosen["as_needed"]
		rate = f" at {_money(as_needed.get('price'))} an hour" if flt(as_needed.get("price")) else ""
		plan.append(f"extra service when you ask for it{rate} (1-hour minimum)")
	if doc.get("custom_plan_details"):
		plan.append("the custom plan in Exhibit A")
	if plan:
		rows.append(("What we do:", _join(plan) + "."))

	# §2.1, §5.1
	rows.append(
		(
			"Chemicals are extra:",
			"the water treatment chemicals used on each visit are billed on top of the base rate, at our "
			"current prices, and listed on each invoice.",
		)
	)
	# No yearly estimate here (Nik, 2026-10-13: "remove the About a year from the summary").
	# §5.2, §8.1
	rows.append(
		(
			"Repairs:",
			"we quote repairs and replacement parts first and only go ahead with your OK; parts are billed at "
			"cost plus 15%.",
		)
	)
	# §8.2
	# Only the rates and minimum the agreement actually prints: a blank in §8.2 stays out of here.
	emergency = []
	if flt(doc.get("emergency_rate")):
		hours = f" ({_text(doc.get('business_hours'))})" if doc.get("business_hours") else ""
		emergency.append(f"{_money(doc.get('emergency_rate'))} an hour during business hours{hours}")
	if flt(doc.get("after_hours_emergency_rate")):
		emergency.append(
			f"{_money(doc.get('after_hours_emergency_rate'))} an hour at any other time, including weekends and holidays"
		)
	if emergency:
		minimum = (
			f", with a {_hours(doc.get('emergency_minimum_hours'))} minimum per call"
			if flt(doc.get("emergency_minimum_hours"))
			else ""
		)
		rows.append(("Emergencies:", _join(emergency) + minimum + "."))

	rows.append(_maintenance_term(doc))
	rows.append(_maintenance_payment(doc))
	# §3.3, §6
	rows.append(
		(
			"Your part:",
			"keep the water topped up, clear large debris, don't add chemicals yourself, tell us about leaks or "
			"odd noises, and give us access. If we can't get to the fountain at a scheduled visit, the visit may "
			"still be charged.",
		)
	)
	# §10.1, §10.4
	rows.append(
		(
			"Ending it:",
			"either of us can end the agreement with 30 days' written notice. A prepaid annual plan ended early has "
			"a fee of 25% of the unused balance.",
		)
	)
	# §11.3, §14.2, §14.3
	rows.append(
		(
			"If something goes wrong:",
			"our liability is capped at what you paid us in the six months before, and a dispute that "
			"negotiation does not settle goes to binding arbitration in Salt Lake City, where the winner's "
			"attorney fees are paid by the other side.",
		)
	)
	return rows


def _maintenance_term(doc):
	"""§9.1-§9.3: start, length, automatic renewal and the increase."""
	increase = flt(doc.get("renewal_increase_percent"))
	raise_text = (
		f"each renewal raises every rate by {_number(increase)}%"
		if increase > 0
		else "rates stay the same at renewal"
	)
	start = f"Starts {_date(doc.get('agreement_start_date'))}" if doc.get("agreement_start_date") else "Starts on the start date"
	term = doc.get("initial_term")
	if term == "Month-to-Month":
		yearly = (
			f"; rates rise {_number(increase)}% on each anniversary of the start date" if increase > 0 else ""
		)
		return (
			"How long:",
			f"{start} and runs month to month. Either of us can end it with 30 days' written notice{yearly}.",
		)
	length = f" for {TERM_WORDS[term]}" if term in TERM_WORDS else ""
	return (
		"How long:",
		f"{start}{length}, then renews by itself every year unless either of us gives 30 days' written notice "
		f"before the end of a term, and {raise_text}.",
	)


def _maintenance_payment(doc):
	"""§4.2-§4.4: the payment method on file and how it is used."""
	method = doc.get("payment_method") or ""
	cadence = INVOICE_WORDS.get(doc.get("invoicing_frequency"))
	due_days = cint(doc.get("invoice_due_days")) or 30
	when = f"Invoices go out {cadence}. " if cadence else ""
	if method == "Bank Account (ACH)":
		how = (
			"Your bank account is debited automatically after each visit and you get a \"Paid\" invoice by "
			"email. A returned payment costs $35."
		)
	elif method == "Invoice (Check or Bank Transfer)":
		how = f"You pay each invoice by check or bank transfer within {due_days} days. A returned payment costs $35."
	elif method == "Other":
		detail = _text(doc.get("payment_method_notes"))
		how = f"You pay by {detail or 'the method agreed in Section 4.3'}, within {due_days} days of each invoice."
	elif method == "Credit / Debit Card":
		how = (
			"Your card on file is charged automatically after each visit and you get a \"Paid\" invoice by "
			"email. A declined card costs $35."
		)
	else:
		# Nothing chosen yet: the paper form, where the client ticks a box in §4.3.
		how = "You choose in Section 4.3 how you pay: a card, a bank account, or by invoice."
	return (
		"Paying:",
		f"{when}{how} A credit card is not required, but some payment method has to stay on file, and service "
		"can be paused if an account is more than 30 days past due.",
	)


# ---------------------------------------------------------------------------
# Rental Agreement
# ---------------------------------------------------------------------------


def _rental(doc):
	rows = []
	items = [_text(row.get("description")) for row in (doc.get("equipment_items") or []) if row.get("description")]
	if items:
		shown = items[:4] + ([f"{len(items) - 4} more items"] if len(items) > 4 else [])
		rows.append(("What you're renting:", _join(shown) + "."))
	# §2.1, §2.2
	if doc.get("rental_start_date") or doc.get("rental_end_date"):
		rows.append(
			(
				"When:",
				f"{_date(doc.get('rental_start_date')) or 'the start date'} to "
				f"{_date(doc.get('rental_end_date')) or 'the end date'}. The equipment must be back, or ready for "
				"pickup, by 5:00 PM on the last day; extra days are charged at the daily rate.",
			)
		)
	# §3.1, §3.2, §3.3, §3.4
	money = []
	if flt(doc.get("total_rental_amount")):
		money.append(f"the rental comes to {_money(doc.get('total_rental_amount'))}")
	if flt(doc.get("security_deposit")):
		money.append(f"plus a {_money(doc.get('security_deposit'))} security deposit")
	due = f" ({_money(doc.get('total_due_at_signing'))})" if flt(doc.get("total_due_at_signing")) else ""
	rows.append(
		(
			"Cost:",
			(_join(money) + ". " if money else "")
			+ f"Half the rental and the whole deposit are due when you sign{due}; the rest before the rental "
			"starts. Card payments carry a 3% fee, and late payments accrue 1.5% a month.",
		)
	)
	# §4.2
	rows.append(
		(
			"Your deposit:",
			"comes back within 14 business days of pickup, less any damage beyond normal wear, missing parts, "
			"cleaning or unpaid charges, which we list for you.",
		)
	)
	# §5.1, §6, §8
	rows.append(
		(
			"Your part:",
			"give us access, a level surface, power and water for setup; keep the equipment on site and people "
			"at a safe distance; check the water level and tell us about problems. You are responsible for the "
			"equipment from delivery to pickup, including damage beyond normal wear.",
		)
	)
	# §12
	rows.append(
		(
			"Cancelling:",
			"more than 30 days before the start, a full refund; 15 to 30 days before, we keep half the rental "
			"fees; under 15 days, we keep all of them. The deposit is refunded either way.",
		)
	)
	# §9.1, §10.2, §11.1, §14
	rows.append(
		(
			"If something goes wrong:",
			"insurance is strongly recommended, and you take on the risks of a water feature on site. Our "
			"liability is capped at the rental fees you paid. Disputes go to negotiation, then mediation, then "
			"binding arbitration in Salt Lake City, and both of us give up a jury trial.",
		)
	)
	return rows


# ---------------------------------------------------------------------------
# Owner Contract
# ---------------------------------------------------------------------------


OWNER_PHASE_WORDS = {
	"design": "design and engineering",
	"construction": "construction and installation",
	"maintenance": "ongoing maintenance",
}


def _owner(doc):
	rows = []
	phases = [row for row in (doc.get("phases") or []) if cint(row.get("included"))]
	if phases:
		parts = []
		for row in phases:
			words = OWNER_PHASE_WORDS.get(row.get("phase_key"), _text(row.get("phase_label")))
			parts.append(f"{words} ({_money(row.get('fee'))})" if flt(row.get("fee")) else words)
		rows.append(("What we do:", _join(parts) + "."))
	money = []
	if flt(doc.get("total_contract_value")):
		money.append(f"The total for the work above is {_money(doc.get('total_contract_value'))}.")
	if flt(doc.get("total_due_at_signing")):
		money.append(f"{_money(doc.get('total_due_at_signing'))} is due when you sign.")
	if money:
		rows.append(("Cost:", " ".join(money)))
	keys = {row.get("phase_key") for row in phases}
	# §2.4, §2.7
	if "design" in keys:
		rows.append(
			(
				"Design:",
				"you have 14 days to review each design stage; after that it counts as approved. Design "
				"invoices are due within 10 days.",
			)
		)
	# §3.2, §3.4, §3.5, §7.1
	if "construction" in keys:
		rows.append(
			(
				"Construction:",
				"paid by milestones, each due within 5 business days. Any change to the work needs a signed "
				"Change Order. Permit fees are yours. Our workmanship is warranted for one year from substantial "
				"completion.",
			)
		)
	# §4.1, §4.4
	if "maintenance" in keys:
		rows.append(
			(
				"Maintenance:",
				"renews every year unless either of us gives 30 days' written notice, and follows our "
				"Maintenance Services Agreement.",
			)
		)
	# §5; the late-payment terms differ by phase, so only the chosen phases' are stated:
	# interest in §3.2 (construction) and §4.4 (maintenance), a pause after 5 days' notice in
	# §3.2, and a pause on design work for an overdue balance in §2.4.
	late = []
	if keys & {"construction", "maintenance"}:
		late.append("Overdue amounts accrue 1.5% a month.")
	if "construction" in keys:
		late.append("We can pause construction for non-payment after 5 days' written notice.")
	if "design" in keys:
		late.append("We can pause design work while a design balance is overdue.")
	rows.append(
		(
			"Your part:",
			"site access on weekdays 7:00 AM to 5:00 PM, underground utilities marked, and power and water on "
			"site." + (" " + " ".join(late) if late else ""),
		)
	)
	# §12.1, §11.1, §14
	rows.append(
		(
			"Ending it, and if something goes wrong:",
			"you can end any phase with 14 days' written notice and pay for the work done and materials ordered. "
			"Our liability is capped at what you paid for the phase in question. Disputes go to negotiation, "
			"then mediation, then binding arbitration in Salt Lake City.",
		)
	)
	return rows


BUILDERS = {
	"maintenance": _maintenance,
	"rental": _rental,
	"owner": _owner,
}
