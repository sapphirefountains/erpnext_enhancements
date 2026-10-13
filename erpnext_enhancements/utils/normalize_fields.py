# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Which fields hold a phone, an email, a website or an address part — the registry.

``utils.normalize`` decides what a value should look like; this module decides which values
it is allowed to touch. The list is curated rather than derived from fieldtypes, because on
this site the fieldtype says almost nothing: most phone and email fields are plain ``Data``
(typing them ``Phone``/``Email`` is the last step of the plan, not the first — see
``docs/data-normalization.md``), and several fields whose *names* say phone or email are not
contact values at all. It was derived from production DocField and Custom Field metadata on
2026-10-13 and every exclusion below carries its reason.

**The rules every consumer follows** (the dry-run report, the save hook
``utils/normalize_hook.py``, and the cleanup in P2-C later):

* **A record with ``docstatus`` 1 or 2 is never touched.** A submitted or cancelled document
  is a record of what was true when it was submitted; rewriting a field on it is editing
  history, and most of these doctypes would refuse the write anyway.
* **The bulk cleanup never writes a Single.** Writing one field of a never-saved Single makes
  ``load_from_db`` read ``tabSingles`` for every field, and each field with no row loads blank
  (CLAUDE.md, "the inverse bites too"). Singles in :data:`SINGLES` are *reported* only; a
  person fixes them through the settings form, which saves every field at once.
* Child rows are reported against their parent, because the parent is what a person opens.

Frappe-free, like ``normalize``: plain data and two helpers.
"""

PHONE = "phone"
EMAIL = "email"
WEBSITE = "website"
TEXT = "text"
STATE = "state"
ZIP = "zip"


def _kind(kind, *fieldnames):
	return {fieldname: kind for fieldname in fieldnames}


FIELDS = {
	"Address": {
		**_kind(PHONE, "phone", "fax"),
		**_kind(EMAIL, "email_id"),
		**_kind(TEXT, "address_line1", "address_line2", "city", "county"),
		# state and pincode follow the record's own `country`.
		"state": STATE,
		"pincode": ZIP,
	},
	"Appointment": {**_kind(PHONE, "customer_phone_number"), **_kind(EMAIL, "customer_email")},
	"Bank": _kind(WEBSITE, "website"),
	"Call Routing Settings": _kind(PHONE, "default_forward_number"),
	"Call Routing Target": _kind(PHONE, "phone_number"),
	"Company": {**_kind(PHONE, "phone_no", "fax"), **_kind(EMAIL, "email"), **_kind(WEBSITE, "website")},
	"Competitor": _kind(WEBSITE, "website"),
	"Contact": {
		**_kind(
			PHONE,
			"phone",
			"mobile_no",
			"custom_home_phone",
			"custom_mobile_number",
			"custom_other_phone",
			"custom_phone_number",
			"custom_fax",
		),
		**_kind(EMAIL, "email_id", "custom_email", "custom_secondary_email_address"),
		**_kind(TEXT, "first_name", "middle_name", "last_name"),
	},
	"Contact Email": _kind(EMAIL, "email_id"),
	"Contact Phone": _kind(PHONE, "phone"),
	"Contract Signature Request": _kind(EMAIL, "signer_email"),
	"Customer": {
		**_kind(PHONE, "custom_accounts_phone_number", "custom_primary_phone", "primary_contact_phone"),
		**_kind(
			EMAIL, "custom_accounts_email_address", "custom_primary_email_address", "primary_contact_email"
		),
		**_kind(WEBSITE, "website"),
	},
	"Driver": _kind(PHONE, "cell_number"),
	"Employee": {
		**_kind(PHONE, "cell_number", "emergency_phone_number"),
		**_kind(EMAIL, "company_email", "personal_email", "prefered_email"),
	},
	"ERPNext Enhancements Settings": _kind(EMAIL, "handoff_escalation_fallback"),
	"Fountain Move Invite": {**_kind(PHONE, "recipient_phone"), **_kind(EMAIL, "recipient_email")},
	"Fountain Move Request": {**_kind(PHONE, "phone"), **_kind(EMAIL, "email")},
	"Hand-Off Attendee Role": _kind(EMAIL, "email"),
	"Lead": {
		**_kind(
			PHONE,
			"phone",
			"mobile_no",
			"whatsapp_no",
			"fax",
			"custom_account_phone_number",
			"custom_contact_phone_number",
			"custom_primary_phone",
			"primary_contact_phone",
		),
		**_kind(
			EMAIL,
			"email_id",
			"custom_account_email",
			"custom_contact_email",
			"custom_email_address",
			"primary_contact_email",
		),
		**_kind(WEBSITE, "website", "custom_account_website"),
		**_kind(TEXT, "first_name", "last_name"),
	},
	"Managed Device": _kind(PHONE, "phone_number"),
	"Manufacturer": _kind(WEBSITE, "website"),
	"Opportunity": {
		**_kind(
			PHONE,
			"phone",
			"contact_mobile",
			"whatsapp",
			"custom_contact_mobile_number",
			"primary_contact_phone",
		),
		**_kind(EMAIL, "contact_email", "custom_billing_email", "primary_contact_email"),
		**_kind(WEBSITE, "website"),
	},
	"Package Dispatch": _kind(PHONE, "recipient_phone"),
	"Project": {
		**_kind(
			PHONE,
			"custom_contact_phone",
			"custom_customer_phone",
			"custom_primary_phone",
			"primary_contact_phone",
		),
		**_kind(
			EMAIL,
			"custom_billing_email",
			"custom_customer_email",
			"custom_primary_email_address",
			"primary_contact_email",
		),
	},
	"Project Contract": {
		**_kind(PHONE, "contact_phone", "supervisor_phone"),
		**_kind(EMAIL, "contact_email", "sapphire_contact_email", "supervisor_email"),
	},
	"Project Planner Settings": _kind(PHONE, "customer_confirmation_phone"),
	"Project Reminder Email": _kind(EMAIL, "email"),
	"Prospect": {**_kind(PHONE, "fax"), **_kind(WEBSITE, "website")},
	"Prospect Lead": {**_kind(PHONE, "mobile_no"), **_kind(EMAIL, "email")},
	"Rental Booking": _kind(PHONE, "site_contact_phone"),
	"Sales Partner": _kind(WEBSITE, "partner_website"),
	"Supplier": {
		**_kind(PHONE, "custom_phone", "custom_phone_number", "primary_contact_phone"),
		**_kind(EMAIL, "custom_email", "custom_email_address", "primary_contact_email"),
		**_kind(WEBSITE, "website"),
	},
	"Travel Settings": {**_kind(PHONE, "travel_desk_phone"), **_kind(EMAIL, "travel_desk_email")},
	"Visit Sign-Off Request": _kind(EMAIL, "email"),
	"Warehouse": {**_kind(PHONE, "mobile_no", "phone_no"), **_kind(EMAIL, "email_id")},
	"Water Engineering Settings": _kind(PHONE, "contractor_phone", "engineer_phone"),
}

#: Single doctypes in the registry: reported, never written by the bulk cleanup.
SINGLES = frozenset(
	{
		"Call Routing Settings",
		"ERPNext Enhancements Settings",
		"Project Planner Settings",
		"Travel Settings",
		"Water Engineering Settings",
	}
)

#: Child tables in the registry: reported against their parent document.
CHILD_TABLES = frozenset(
	{
		"Call Routing Target",
		"Contact Email",
		"Contact Phone",
		"Hand-Off Attendee Role",
		"Project Reminder Email",
		"Prospect Lead",
	}
)

#: Which parent doctypes hold each registered child table. The save hook
#: (``utils/normalize_hook.py``) runs on the parent's save — a child row never gets a
#: ``before_validate`` of its own — and reads this to know, without loading any meta, which
#: parents can carry a registered row. ``tests/test_normalize_hook.py`` checks it against the
#: Table fields of this app's doctypes and fixtures; Contact's and Prospect's are frappe's and
#: ERPNext's own.
CHILD_TABLE_PARENTS = {
	"Call Routing Target": ("Call Routing Rule",),
	"Contact Email": ("Contact",),
	"Contact Phone": ("Contact",),
	"Hand-Off Attendee Role": ("ERPNext Enhancements Settings",),
	"Project Reminder Email": ("ERPNext Enhancements Settings",),
	"Prospect Lead": ("Prospect",),
}

#: The doctypes whose ``state``/``zip`` fields read the record's own ``country``.
COUNTRY_FIELD = {"Address": "country"}

_SNAPSHOT = "a copy fetched from the Contact on a transaction; it follows the source"
_SNAPSHOT_DOCTYPES = (
	"Sales Invoice",
	"Sales Order",
	"Quotation",
	"Payment Entry",
	"Delivery Note",
	"POS Invoice",
	"Dunning",
	"Supplier Quotation",
	"Installation Note",
	"Maintenance Schedule",
	"Maintenance Visit",
	"Warranty Claim",
	"Request for Quotation Supplier",
	"Shipment",
	"Delivery Trip",
	"Delivery Stop",
	"Process Statement Of Accounts Customer",
)
_NOT_A_WEBSITE = "a URL that is not a website: a payment link, a permalink, a file or a meeting"

#: Fields that look like contact values and are deliberately left alone, each with why.
#: A key is a whole doctype or ``"Doctype.field"``.
EXCLUDED = {
	**{dt: f"contact_email / contact_mobile / contact_phone: {_SNAPSHOT}" for dt in _SNAPSHOT_DOCTYPES},
	"Purchase Invoice.sender": "a mail header captured from an inbound email, not a contact field",
	"Issue.raised_by": "a mail header captured from an inbound email, not a contact field",
	"Newsletter.sender_email": "the system's outbound sender, configured with the email account",
	"Account": "primary_contact_*: custom fields on the chart-of-accounts doctype, not a party",
	"Fountain Move Request.phone_normalized": "a derived digits-only matching key, not a phone to show",
	"Lead.phone_ext": "an extension number on its own, not a phone",
	"Opportunity.phone_ext": "an extension number on its own, not a phone",
	"Call Routing Target.phone_label": "a label for the number, not a contact value",
	"Triton Settings.email_model_id": "an AI model id, not a contact value",
	"TP OTP Settings.email_otp_subject": "an email subject line, not a contact value",
	"TP Telephony Agent.mobile_no": "another app's doctype; its matching rules are not ours",
	"Payment Request.payment_url": _NOT_A_WEBSITE,
	"Code List.url": _NOT_A_WEBSITE,
	"Social Post.link": _NOT_A_WEBSITE,
	"Social Post Target.permalink": _NOT_A_WEBSITE,
	"Social Publish Job.permalink": _NOT_A_WEBSITE,
	"Call Log.custom_voicemail_url": _NOT_A_WEBSITE,
	"Training Live Class.join_url": _NOT_A_WEBSITE,
	"Rental Settings.review_url": _NOT_A_WEBSITE,
	"Paytm Settings.website": "a payment-gateway setting, not a business website",
	"Payment Request.phone_number": "a payment-gateway payload, sent as the gateway expects it",
	"Payment Request.email_to": "a payment-gateway payload, sent as the gateway expects it",
	"Asset Maintenance Log.task_assignee_email": "fetched from the User record; it follows the source",
}


def fields_for(doctype):
	"""``{fieldname: kind}`` for ``doctype`` — empty for a doctype the registry does not cover."""
	return dict(FIELDS.get(doctype) or {})


def kinds():
	"""Every kind the registry uses, sorted."""
	return sorted({kind for fields in FIELDS.values() for kind in fields.values()})


def is_excluded(doctype, fieldname=None):
	"""True when the whole doctype, or ``doctype.fieldname``, is on :data:`EXCLUDED`."""
	return doctype in EXCLUDED or (fieldname is not None and f"{doctype}.{fieldname}" in EXCLUDED)
