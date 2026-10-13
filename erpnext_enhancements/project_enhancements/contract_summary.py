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
	"""The summary box for ``doc``, or "" for a contract type with no entry in ``BUILDERS``."""
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
	plan = _plan(doc, chosen, rate_words="a base rate of ")
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


def _plan(doc, chosen, rate_words="", custom_words=None):
	"""The ticked plan options in words: the maintenance agreement's Service Plan Selection and
	the owner contract's §4.2 print the same menu from the same rows."""
	plan = []
	standard = chosen.get("standard")
	if standard:
		how_often = FREQUENCY_WORDS.get(doc.get("visit_frequency"), "on the agreed schedule")
		if doc.get("visit_frequency") == "Custom":
			if doc.get("custom_visit_schedule"):
				how_often = f"on a custom schedule ({_text(doc.get('custom_visit_schedule'))})"
			elif custom_words:
				how_often = custom_words
		price = (
			f" at {rate_words}{_money(standard.get('price'))} per visit" if flt(standard.get("price")) else ""
		)
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
	return plan


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
	if term == "Other" and doc.get("initial_term_other"):
		length = f" for {_text(doc.get('initial_term_other'))}"
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
	# §3.2 prints the days as a blank line when none are set, so the summary names them only when set.
	days = cint(doc.get("balance_due_days"))
	rest = "the rest before the rental starts"
	if days > 0:
		rest = f"the rest {days} day{'' if days == 1 else 's'} before the rental starts"
	rows.append(
		(
			"Cost:",
			(_join(money) + ". " if money else "")
			+ f"Half the rental and the whole deposit are due when you sign{due}; {rest}. Card payments carry a "
			"3% fee, and late payments accrue 1.5% a month.",
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
	# §4.1, §4.2, §4.4
	if "maintenance" in keys:
		options = {row.get("option_key"): row for row in (doc.get("service_options") or [])}
		plan = _plan(
			doc,
			{key: row for key, row in options.items() if cint(row.get("included"))},
			custom_words="on the agreed schedule",
		)
		renews = (
			"renews every year unless either of us gives 30 days' written notice, and follows our "
			"Maintenance Services Agreement."
		)
		rows.append(("Maintenance:", f"{_join(plan)}. It {renews}" if plan else renews))
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


# ---------------------------------------------------------------------------
# Mutual Non-Disclosure Agreement
# ---------------------------------------------------------------------------


def _nda(doc):
	# Mutual: "each of us" is Sapphire and the Company alike.
	purpose = _text(doc.get("nda_purpose")) or "a proposed business relationship"
	effective = f" ({_date(doc.get('contract_date'))})" if doc.get("contract_date") else ""
	return [
		# Recitals, §1, §2, §3
		(
			"What it covers:",
			f"the information each of us shares, or lets the other see, in connection with {purpose}: drawings, "
			"plans, know-how, processes, and business, financial, customer and product information. It does not "
			"cover what is already public, what came from someone free to share it, or what either of us "
			"developed independently.",
		),
		# §4.1
		(
			"What each of us must do:",
			"keep the other's information confidential and protect it as we protect our own; share it only with "
			"people who need it to evaluate the relationship and are bound to keep it confidential; use it for "
			"nothing else; and not copy, reverse engineer or export it.",
		),
		# §5.1
		(
			"If the law asks for it:",
			"whoever is asked tells the other within 48 hours, before disclosing anything where possible, and "
			"discloses no more than the request requires.",
		),
		# §6.1
		(
			"Giving it back:",
			"within 3 days of a request. Anything that cannot be returned stays confidential for 10 years, and "
			"longer while it is a trade secret.",
		),
		# §7.1, §7.2, §8.4, §8.5
		(
			"How long, and if it is broken:",
			f"these obligations last until ten years after the Effective Date{effective}. A breach can be stopped "
			"by a court order without posting a bond, the side that wins in court has its costs and attorney fees "
			"paid, and Utah law and the Utah courts apply.",
		),
	]


# ---------------------------------------------------------------------------
# Architect Agreement
# ---------------------------------------------------------------------------


def _architect(doc):
	rows = []
	# Recitals, §1.1, §1.3
	what = "the services in the Statement of Work"
	if doc.get("project_title"):
		what += f" for {_text(doc.get('project_title'))}"
	if doc.get("architect_owner"):
		what += f", as part of your Prime Agreement with {_text(doc.get('architect_owner'))}"
	rows.append(
		(
			"What we do:",
			f"{what}. We control how the work is built and coordinate it, and we obtain the permits and "
			"inspections it needs.",
		)
	)
	# §1.1, §1.2
	rows.append(
		(
			"Your part:",
			"provide the design requirements, surveys and site descriptions we ask for. We rely on their accuracy, "
			"and our work waits for anything you do not provide in time.",
		)
	)
	# §2.1
	rows.append(
		(
			"Changes:",
			"ask in writing; a change to the Statement of Work takes effect only when both of us agree it in writing.",
		)
	)
	# §3.1. The Statement of Work's own payment terms when they are filled in; §3.1's fallback otherwise.
	terms = "; ".join(
		line.strip().rstrip(".;")
		for line in _text(doc.get("architect_payment_terms")).splitlines()
		if line.strip()
	)
	pay = (
		f"as the Statement of Work sets out: {terms}."
		if terms
		else "as the Statement of Work sets out, or our customary rates monthly where it does not say."
	)
	rows.append(
		(
			"Paying:",
			f"{pay} Invoices are due 30 days after you receive them, and you pay the taxes (other than on our net "
			"income) and the pre-approved travel. You can hold back an item you dispute in writing before it is due; "
			"we can pause the work on 10 days' written notice if an undisputed invoice goes unpaid.",
		)
	)
	# §6.1
	rows.append(
		(
			"Warranty:",
			"we redo work that is not good and workmanlike at no cost if you tell us within 30 days of delivery. "
			"Beyond that, the work is provided as is.",
		)
	)
	# §4.1, §7.1, §10.7
	rows.append(
		(
			"Ending it, and if something goes wrong:",
			"either of us can end the agreement with 30 days' written notice while no Statement of Work is in "
			"effect, or after a breach left uncured for 30 days. Our liability is capped at what you paid us under "
			"the Statement of Work in the last 12 months. Utah law applies, and suits go to a federal court in Utah "
			"or the state court in Salt Lake County.",
		)
	)
	return rows


# ---------------------------------------------------------------------------
# Employee-Contractor Agreement
# ---------------------------------------------------------------------------


def _employee_contractor(doc):
	return [
		# §2
		(
			"What we expect:",
			"clear communication, professionalism, help with the Company's marketing, every hour worked billable to "
			"a client job or project, respect for co-workers, clients, equipment and company time, company "
			"resources used for company work, and work finished on time or earlier.",
		),
		# §3.1, §3.2
		(
			"Confidentiality:",
			"the Company's plans and strategies, its customers' and suppliers' names and addresses, its trade "
			"secrets and its communications with customers and clients stay confidential, during and after your "
			"time with the Company. Information made public through no fault of yours is not covered.",
		),
		# §4.1
		(
			"What you create:",
			"work product that relates to your work for the Company belongs to the Company, and you sign whatever "
			"is needed to assign it.",
		),
		# §5.1, §5.2
		(
			"Afterwards:",
			"for 36 months after you leave, you will not work with the Company's clients and contacts on jobs done "
			"for the Company or solicit its employees; and while you work here and for 36 months after, you will "
			"not compete with the Company for business with its clients or customers without the written consent "
			"of its CEO/Owner.",
		),
	]


# ---------------------------------------------------------------------------
# Master Subcontractor Agreement ("you" is the subcontractor, "we" is Sapphire)
# ---------------------------------------------------------------------------


#: The tier selection on page 1, in words.
MSA_TIER_WORDS = {
	"Tier 1": "You are Tier 1 (service and design work): Sections 1–20 apply.",
	"Tier 2": (
		"You are Tier 2 (trade work): Sections 1–20 and the Trade Subcontractor Addendum (Sections 21–24) "
		"apply."
	),
}


def _msa(doc):
	tier = doc.get("msa_tier") or ""
	which = next(
		(words for key, words in MSA_TIER_WORDS.items() if key in tier),
		"The tier ticked on page 1 decides whether the Trade Subcontractor Addendum (Sections 21–24) applies.",
	)
	rows = [
		# §1.1, §1.2, §2.2, tier selection
		(
			"What this covers:",
			"the terms for all the work you do for us. Each job also gets its own Statement of Work, signed by "
			"both of us, with the scope, dates and price; where the two differ, the Statement of Work controls "
			f"for that job. {which}",
		),
	]
	# §4.1-§4.4; retainage (§4.4) is Tier 2's alone.
	retainage = (
		" We may hold back up to 10% of each progress payment until 30 days after your work is finally accepted."
		if "Tier 2" in tier
		else ""
	)
	rows.append(
		(
			"Getting paid:",
			"we pay what each Statement of Work sets, within 10 business days of being paid for your work by the "
			"project owner, and only once we have been: you carry the risk of the owner not paying (pay if paid). "
			f"Dispute an invoice in writing within 10 days.{retainage}",
		)
	)
	# §5.1, §6.1, §20.8
	rows.append(
		(
			"Before you start:",
			"no work begins until we have your current certificate of insurance, and we can stop the work and hold "
			"payment whenever one is not on file. We may also ask for performance and payment bonds, and for a "
			"personal guarantee from an owner or principal.",
		)
	)
	# §8.2, §14.1, §14.2
	rows.append(
		(
			"Your work:",
			"warranted for one year from final acceptance, and defects are fixed at no cost within 48 hours of "
			"written notice. If you fall behind or are in default, we can take the work over after 48 hours' "
			"written notice and charge you the cost to finish it.",
		)
	)
	# §11.1, §12.1, §12.2
	rows.append(
		(
			"Delays and liability:",
			"a delay earns you more time, not money. You indemnify us against claims arising from your work, and "
			"our liability to you is capped at what we paid you under that Statement of Work in the three months "
			"before the claim.",
		)
	)
	# §17.1, §18
	rows.append(
		(
			"Ending it, and disputes:",
			"either of us can end this agreement, or any Statement of Work, with 30 days' written notice. Disputes "
			"go to negotiation, then mediation, then binding arbitration in Salt Lake City, where the winner's "
			"attorney fees are paid by the other side.",
		)
	)
	return rows


# ---------------------------------------------------------------------------
# Statement of Work ("you" is the subcontractor, "we" is Sapphire)
# ---------------------------------------------------------------------------


#: §2.1's Included Services, in words.
SOW_SERVICES = (
	("sow_svc_site_prep", "site preparation and layout"),
	("sow_svc_excavation", "excavation and grading"),
	("sow_svc_concrete", "concrete and masonry"),
	("sow_svc_plumbing", "plumbing and hydraulics"),
	("sow_svc_electrical", "electrical rough-in and conduit"),
	("sow_svc_equipment", "equipment installation"),
	("sow_svc_assembly", "fountain assembly"),
	("sow_svc_finish", "tile, stone and finish work"),
	("sow_svc_landscaping", "landscaping"),
	("sow_svc_startup", "startup, testing and commissioning"),
	("sow_svc_cleanup", "cleanup and debris removal"),
)

#: §5.1's Compensation Type, in words.
SOW_COMPENSATION_WORDS = {
	"Fixed Price (Lump Sum)": "a fixed price (lump sum)",
	"Time & Materials": "time and materials",
	"Unit Price": "unit prices",
	"Cost Plus Fee": "cost plus a fee",
}


def _sow(doc):
	rows = []
	title = _text(doc.get("project_title"))
	site = _text(doc.get("site_address")).replace("\n", ", ")
	if title or site:
		rows.append(("The job:", (title or "the project") + (f" at {site}" if site else "") + "."))
	# Section 2
	services = [words for field, words in SOW_SERVICES if cint(doc.get(field))]
	if doc.get("sow_svc_other"):
		services.append(_text(doc.get("sow_svc_other")))
	rows.append(
		(
			"What you do:",
			"furnish the labor, materials, tools, equipment and supervision for "
			+ (_join(services) + ", as Section 2 describes" if services else "the work Section 2 describes")
			+ (
				", except the items Section 6 lists, which we furnish."
				if doc.get("sow_company_furnished") == "Company-Furnished Items"
				else "."
			),
		)
	)
	# The opening paragraph
	dated = f" effective {_date(doc.get('msa_effective_date'))}" if doc.get("msa_effective_date") else ""
	rows.append(
		(
			"Under your master agreement:",
			f"this Statement of Work sits under our Master Services Agreement with you{dated}, whose terms apply; "
			"where the two differ, this Statement of Work controls for this job.",
		)
	)
	# Section 4: the dates, §4.1, §4.2, §4.3
	when = []
	if doc.get("mobilization_date"):
		when.append(f"starts {_date(doc.get('mobilization_date'))}")
	if doc.get("substantial_completion_date"):
		when.append(f"is substantially complete by {_date(doc.get('substantial_completion_date'))}")
	if doc.get("final_completion_date"):
		when.append(f"is finally complete by {_date(doc.get('final_completion_date'))}")
	days = cint(doc.get("sow_schedule_days"))
	schedule = (
		f"You send us a project schedule within {days} business day{'' if days == 1 else 's'} of signing"
		if days > 0
		else "You send us a project schedule after signing"
	)
	late = ""
	rate = flt(doc.get("sow_liquidated_damages_rate"))
	if doc.get("sow_liquidated_damages") == "Per Calendar Day" and rate:
		late = f" Finishing late costs {_money(rate)} for each calendar day past the Substantial Completion Date."
	when_text = (
		(f"the work {_join(when)}. " if when else "")
		+ f"{schedule} and update it weekly. Time is of the essence for substantial completion.{late}"
	)
	# Every row reads on from its label in lower case.
	rows.append(("When:", when_text[:1].lower() + when_text[1:]))
	# Section 5
	pay = []
	compensation = SOW_COMPENSATION_WORDS.get(doc.get("sow_compensation_type"))
	if compensation:
		pay.append(f"priced as {compensation}")
	if flt(doc.get("milestones_total")):
		pay.append(f"the payment schedule in Section 5.2 totals {_money(doc.get('milestones_total'))}")
	if flt(doc.get("not_to_exceed")):
		pay.append(f"time and materials work is not to exceed {_money(doc.get('not_to_exceed'))}")
	retention = flt(doc.get("sow_retention_percent"))
	held = (
		f"{_number(retention)}% of each progress payment"
		if retention > 0
		else "retention from each progress payment"
	)
	day = _text(doc.get("sow_invoice_day"))
	invoices = (
		f"Send invoices by the {day} of each month or when a milestone is done"
		if day
		else "Send invoices each month or when a milestone is done"
	)
	paid_text = (
		("; ".join(pay) + ". " if pay else "")
		+ f"We hold back {held} until final completion and your final lien waiver. {invoices}; the master "
		"agreement's payment terms apply."
	)
	rows.append(("Getting paid:", paid_text[:1].lower() + paid_text[1:]))
	# §7.2, §8.2, §8.4, §9.3
	fix = cint(doc.get("sow_correction_days"))
	within = f" within {fix} business day{'' if fix == 1 else 's'} of written notice" if fix > 0 else ""
	rows.append(
		(
			"Your part:",
			"keep your key personnel on the job unless we agree in writing to a change, hold a tailgate safety meeting at the "
			"start of each new phase, send us incident and near-miss reports within 24 hours, and correct work that "
			f"misses the quality standards at no extra cost{within}.",
		)
	)
	# Section 10, only when it says something
	if _text(doc.get("sow_special_terms")):
		rows.append(
			(
				"Special terms:",
				"Section 10 adds terms for this job that supplement or modify the master agreement; you initial them.",
			)
		)
	return rows


BUILDERS = {
	"maintenance": _maintenance,
	"rental": _rental,
	"owner": _owner,
	"nda": _nda,
	"architect": _architect,
	"employee_contractor": _employee_contractor,
	"msa": _msa,
	"sow": _sow,
}
