# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bare domains in URL fields, and the 384 records they froze.

Five Property Setter fixtures — ``{Company,Customer,Lead,Opportunity,Supplier}-website-options``
— set ``options = "URL"`` on the stock ``website`` Data field, which switches on frappe's
``validate_url`` for it. Stock ERPNext ships those fields with no options at all, so this
validation is entirely ours, and it arrived after the data did.

That has two consequences, and the second is the expensive one.

**Typing a bare domain is rejected.** ``urlparse("example.com")`` yields no scheme and no
netloc, so ``validate_url`` returns False and the save throws *"'example.com' is not a valid
URL"*. Anyone who writes a website down the way it is printed on a business card hits it.
That is the reported bug (ER-2026-256819 → TASK-2026-01604).

**And 384 existing CRM records could not be saved at all.** When this was written production
held 281 of 739 Customers, 80 of 506 Opportunities, 14 of 35 Leads and 9 of 16 Lead
``custom_account_website`` values with a scheme-less domain — plus 24 of 277 Suppliers, out of
scope here, for 408 across the whole site. frappe re-validates *every*
URL field on *every* save, so those records rejected edits that had nothing to do with the
website — a phone number, a customer group, a background job touching the doc. Not a
form-entry annoyance: 38% of the customer book frozen, and nothing in the error message says
why an unrelated edit will not stick.

The QuickBooks sync met the second half of this first and has healed it since v1.36.0, because
a parked Customer/Supplier master cascaded into its Bills failing to resolve a party.
:func:`heal_url_fields` is that function (``mapping._heal_invalid_urls``) moved here, and
``quickbooks_online.core.mapping`` now calls this one. Two implementations of "what counts as a
fixable URL" would eventually disagree about a record one of them had already rewritten.

**The rule is deliberately unchanged from the QBO version, including the part that looks
careless.** Anything already carrying ``://``, or starting with ``/``, ``#``, ``mailto:`` or
``tel:``, is left alone; every other non-empty value gets ``https://`` prefixed. There is no
"does this look like a hostname" test, so a website field holding ``N/A`` becomes
``https://N/A``. That is the intended trade: the alternative is a record nobody can save, and
visibly-wrong-but-editable beats frozen. It is also the behaviour already applied to this data
by the sync — re-deciding it here would make the two paths differ on exactly the values a
human needs to look at.

**``before_validate``, not ``validate``.** Both run inside ``run_before_save_methods`` and
both are early enough — ``_validate_data_fields`` (which calls ``validate_url``) fires
afterwards, in ``_validate``. ``before_validate`` is the right one because it runs *ahead of
the ``flags.ignore_validate`` early return* and ahead of every other ``validate`` handler, so
no other hook on these doctypes ever reads the half-fixed value.

Wired for Lead / Customer / Opportunity in ``hooks.py``. Supplier and Company carry the same
Property Setter and are **not** wired here — they are outside the CRM request that asked for
this, and the sync already heals the Supplier path that had the cascade.
"""

import frappe

#: Prefixes that mean "this is already a usable reference, leave it alone".
#: ``/`` and ``#`` are relative links, which ``validate_url`` accepts outright; the two
#: schemes are the ones people actually paste into a website field by mistake, and
#: prefixing them would produce ``https://mailto:...`` — worse than what was typed.
_EXEMPT_PREFIXES = ("/", "#", "mailto:", "tel:")

#: The scheme added to a bare domain. https rather than http because every site this
#: would be pointed at redirects to it anyway, and a stored http:// link is one more
#: thing to fix later.
_DEFAULT_SCHEME = "https://"


def normalize_website(value):
	"""Return ``value`` with a scheme prefixed, or ``None`` if it needs no change.

	The whole rule, in one pure function with no ``frappe`` in it, so both callers and
	the bench-free tests exercise the same decision. ``None`` means "leave the stored
	value exactly as it is" — which is different from returning the value unchanged,
	because the callers use it to decide whether anything was healed at all.
	"""
	if not isinstance(value, str):
		return None

	stripped = value.strip()
	if not stripped:
		return None
	if "://" in stripped or stripped.startswith(_EXEMPT_PREFIXES):
		return None

	return _DEFAULT_SCHEME + stripped


def heal_url_fields(doc):
	"""Prefix a scheme onto every scheme-less URL field on ``doc``. Returns the fieldnames.

	Meta-driven rather than a hardcoded ``website``: the fields that need this are
	exactly the ones some Property Setter or Custom Field marked ``options = "URL"``,
	and that list is a fixture that moves. It is also how ``Lead.custom_account_website``
	— a second URL field on a doctype nobody would have thought to list — is covered
	without a second list to keep in step.

	The meta read is wrapped because ``doc_events`` fire during ERPNext's own test
	bootstrap, before this app's customizations exist; a handler that cannot read meta
	must do nothing rather than turn a fresh-DB install into a crash.
	"""
	try:
		fields = frappe.get_meta(doc.doctype).fields or []
	except Exception:
		return []

	healed = []
	for df in fields:
		if getattr(df, "fieldtype", None) != "Data" or (getattr(df, "options", "") or "") != "URL":
			continue
		fixed = normalize_website(doc.get(df.fieldname))
		if fixed is not None:
			doc.set(df.fieldname, fixed)
			healed.append(df.fieldname)

	return healed


def add_missing_scheme(doc, method=None):
	"""``before_validate`` doc_event: accept a bare domain in any URL field.

	Thin wrapper over :func:`heal_url_fields` — the doc_event signature, and a name
	that says what a reader of ``hooks.py`` needs to know.
	"""
	heal_url_fields(doc)
