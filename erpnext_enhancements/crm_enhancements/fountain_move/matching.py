# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Which existing party, if any, does this fountain-move submission belong to?

Repeat customers are common (someone buys a second fountain, or moves house), so
blindly creating a Customer per submission would rebuild the duplicate sprawl the
QuickBooks import already cost us once. Equally, a wrong *merge* is worse than a
duplicate: it writes a stranger's address and opportunity onto someone else's
account, and it is far harder to unpick after the fact.

So the rules are deliberately conservative:

* **Email first, phone second.** Email is close to an identity; a phone number is
  frequently shared (a household, an office switchboard, a spouse).
* **Exact comparison on normalised values.** Not the fuzzy regex
  ``api.telephony._get_caller_info`` uses — see ``utils.phone`` for why that trade
  is right for "who is ringing?" and wrong for "whose record do we write to?".
* **Ambiguity is not resolved, it is escalated.** More than one distinct Customer,
  or an existing Lead already pointing at a different account, stops the
  conversion and files the row for Duplicate Review with the candidates recorded.
  A human decides; nothing is created in the meantime.

The Address and the Opportunity are **always** new. A move has a destination that
may be nothing like the address we already hold, and a new job is a new job.
"""

import frappe

from erpnext_enhancements.utils.phone import is_nanp, normalize_phone

#: Contact fields searched for an email match, in priority order.
CONTACT_EMAIL_FIELDS = ("custom_email", "custom_secondary_email_address", "email_id")

#: Contact fields searched for a phone match.
CONTACT_PHONE_FIELDS = ("custom_phone_number", "custom_mobile_number")


class PartyMatch:
	"""Outcome of resolving a submission to an existing party.

	``needs_review`` is the important one: when True the caller must NOT create
	anything. ``candidates`` then carries what was found, for a human to judge.
	"""

	def __init__(self, customer=None, contact=None, lead=None, basis=None, candidates=None, reason=None):
		self.customer = customer
		self.contact = contact
		self.lead = lead
		self.basis = basis or "None"
		self.candidates = candidates or []
		self.reason = reason

	@property
	def needs_review(self):
		return bool(self.candidates)

	def as_dict(self):
		return {
			"customer": self.customer,
			"contact": self.contact,
			"lead": self.lead,
			"basis": self.basis,
			"reason": self.reason,
			"candidates": self.candidates,
		}


def resolve_party(req):
	"""Resolve a Fountain Move Request to an existing Customer/Contact/Lead.

	Returns a :class:`PartyMatch`. Never writes anything.
	"""
	email = (req.email or "").strip().lower()
	phone10 = normalize_phone(req.phone)

	contacts, customers, basis = _search(email, phone10)

	# Customers reachable either directly or through a matched Contact's links.
	linked = set()
	for contact in contacts:
		linked.update(_customers_for_contact(contact))
	distinct_customers = sorted(set(customers) | linked)

	if len(distinct_customers) > 1:
		return PartyMatch(
			basis=basis,
			candidates=[{"type": "Customer", "name": name} for name in distinct_customers],
			reason=(
				f"{len(distinct_customers)} different Customers match this submission "
				f"(matched on {basis.lower()}). Pick one and convert manually."
			),
		)

	customer = distinct_customers[0] if distinct_customers else None
	contact = _best_contact(contacts, customer)
	lead, lead_conflict, adopted_customer = _resolve_lead(email, customer)

	if adopted_customer:
		# The Lead knew an account that email/phone did not surface (e.g. the
		# Customer carries a different billing address). Adopting it is the whole
		# point of the exercise — creating a second Customer here is exactly the
		# duplicate sprawl this matcher exists to prevent.
		customer = adopted_customer
		basis = "Lead Email"
		contact = _best_contact(contacts, customer) or _primary_contact_of(customer)

	if lead_conflict:
		return PartyMatch(
			customer=customer,
			contact=contact,
			basis=basis,
			candidates=[
				{"type": "Lead", "name": lead_conflict["lead"]},
				{"type": "Customer", "name": lead_conflict["points_at"]},
			],
			reason=(
				f"Lead {lead_conflict['lead']} already belongs to Customer "
				f"{lead_conflict['points_at']}, which is not the Customer this submission "
				f"resolves to ({customer or 'a new one'}). Reconcile them before converting."
			),
		)

	if customer or contact or lead:
		return PartyMatch(customer=customer, contact=contact, lead=lead, basis=basis)
	return PartyMatch(basis="None")


def _search(email, phone10):
	"""Return (contact names, customer names, basis label)."""
	if email:
		contacts = _contacts_by_email(email)
		customers = _customers_by_field("custom_accounts_email_address", email)
		if contacts or customers:
			return contacts, customers, "Email"

	# Phone is only trusted at full NANP length — a 4-digit extension or a
	# half-typed number would match far too much.
	if phone10 and is_nanp(phone10):
		contacts = _contacts_by_phone(phone10)
		customers = _customers_by_phone(phone10)
		if contacts or customers:
			return contacts, customers, "Phone"

	return [], [], "None"


def _contacts_by_email(email):
	names = []
	for field in CONTACT_EMAIL_FIELDS:
		# email_id is stock and always present, but the custom fields only exist
		# once fixtures have synced — on a fresh DB they legitimately do not.
		if not frappe.db.has_column("Contact", field):
			continue
		names.extend(
			frappe.get_all("Contact", filters={field: email}, pluck="name", limit=20)
		)
	return _dedupe(names)


def _contacts_by_phone(phone10):
	"""Match a Contact on the last 10 digits of either phone field.

	Compared in SQL after stripping the common separators, because the stored
	values are free text: "(801) 555-1212", "801.555.1212" and "+1 801 555 1212"
	must all match. Anchored with LIKE '%<digits>' rather than a scattered regex.
	"""
	names = []
	for field in CONTACT_PHONE_FIELDS:
		if not frappe.db.has_column("Contact", field):
			continue
		names.extend(_phone_query("Contact", field, phone10))
	return _dedupe(names)


def _customers_by_phone(phone10):
	if not frappe.db.has_column("Customer", "custom_accounts_phone_number"):
		return []
	return _dedupe(_phone_query("Customer", "custom_accounts_phone_number", phone10))


def _phone_query(doctype, field, phone10):
	"""Names where ``field``, stripped of separators, ends with ``phone10``."""
	stripped = (
		f"REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(`{field}`,"
		" ' ', ''), '-', ''), '(', ''), ')', ''), '.', ''), '+', '')"
	)
	rows = frappe.db.sql(
		f"""SELECT name FROM `tab{doctype}`
			WHERE `{field}` IS NOT NULL AND `{field}` != ''
			  AND {stripped} LIKE %s
			LIMIT 20""",
		(f"%{phone10}",),
	)
	return [row[0] for row in rows]


def _customers_by_field(field, value):
	if not frappe.db.has_column("Customer", field):
		return []
	return frappe.get_all("Customer", filters={field: value}, pluck="name", limit=20)


def _customers_for_contact(contact):
	return frappe.get_all(
		"Dynamic Link",
		filters={"parent": contact, "parenttype": "Contact", "link_doctype": "Customer"},
		pluck="link_name",
	)


def _best_contact(contacts, customer):
	"""Prefer a matched Contact that is actually linked to the resolved Customer."""
	if not contacts:
		return None
	if customer:
		for contact in contacts:
			if customer in _customers_for_contact(contact):
				return contact
	return contacts[0]


def _resolve_lead(email, customer):
	"""Find a reusable Lead by email.

	Returns ``(lead_name, conflict, adopted_customer)``.

	``Lead.email_id`` is uniqueness-enforced by erpnext
	(``check_email_id_is_unique``, with ``CRM Settings.allow_lead_duplication_
	based_on_emails`` off), so a repeat customer *must* be handled — inserting a
	second Lead with the same address would simply throw.

	A Lead already pointing at a *different* account is a conflict, not something
	to silently re-point: that pointer is somebody's deliberate CRM work. But a
	Lead pointing at an account we simply hadn't found is a gift — adopt it.
	"""
	if not email:
		return None, None, None

	lead = frappe.db.get_value(
		"Lead", {"email_id": email}, ["name", "customer", "custom_account_link"], as_dict=True
	)
	if not lead:
		return None, None, None

	pointer = lead.get("customer") or lead.get("custom_account_link")
	if pointer and not frappe.db.exists("Customer", pointer):
		pointer = None  # stale pointer at a deleted Customer — ignore, don't conflict on it

	if pointer and customer and pointer != customer:
		return None, {"lead": lead["name"], "points_at": pointer}, None
	if pointer and not customer:
		return lead["name"], None, pointer
	return lead["name"], None, None


def _primary_contact_of(customer):
	"""The Customer's primary Contact, when it has one.

	Used only when a Lead handed us a Customer that the email/phone search did not
	find — in which case we have no matched Contact either, and reusing the
	account's existing primary beats minting a second Contact for the same person.
	"""
	primary = frappe.db.get_value("Customer", customer, "customer_primary_contact")
	if primary and frappe.db.exists("Contact", primary):
		return primary
	linked = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Contact", "link_doctype": "Customer", "link_name": customer},
		pluck="parent",
		limit=2,
	)
	# Only reuse when it is unambiguous; several contacts on an account means we
	# cannot tell which one is this submitter.
	return linked[0] if len(linked) == 1 else None


def _dedupe(names):
	"""Order-preserving dedupe."""
	seen = set()
	out = []
	for name in names:
		if name and name not in seen:
			seen.add(name)
			out.append(name)
	return out
