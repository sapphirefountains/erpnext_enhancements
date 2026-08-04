# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Lead attribution: capture, first-touch propagation, and the source gate.

Nothing about marketing spend can be evaluated until we know where a deal came
from. As of 2026-08-04 that number was effectively zero: of 815 Opportunities,
**814 had no ``utm_source`` and 809 had no ``custom_lead_source``**. This module
is the plumbing that fixes it going forward.

Read this before touching anything here — the schema is a minefield.

## Why this does not use erpnext's own UTM fields

erpnext v16 ships ``utm_source`` / ``utm_medium`` / ``utm_campaign`` /
``utm_content`` on Lead and Opportunity. We deliberately do **not** write real
campaign data into them, for two independent reasons:

1. **``utm_source`` is load-bearing somewhere else.** ``Lead.before_insert``
   mints a second, stray Contact for every new Lead *unless* ``utm_source`` is
   exactly ``"Existing Customer"`` and ``customer`` is set. The fountain-move
   conversion engine depends on that suppression
   (``crm_enhancements/fountain_move/conversion.py``). Writing a campaign name
   into ``utm_source`` would silently start duplicating Contacts.

2. **``utm_medium`` and ``utm_campaign`` are Links, not Data.** They point at the
   ``UTM Medium`` / ``UTM Campaign`` taxonomies. Raw capture has to accept
   whatever arbitrary string a marketer puts in a URL; writing that to a Link
   field either fails validation or silently spawns junk taxonomy rows. Raw
   values need Data fields, which is what we ship.

So: **the raw query-string values live in our own ``custom_utm_*`` Data fields,
and ``custom_lead_source`` (Link -> ``Lead Source``) remains the single
human-facing channel.** ``Lead Source`` is the taxonomy this site actually
populates (~696 Customers); ``UTM Source`` is a near-identical parallel list that
is empty apart from the suppression sentinel. Pointing anything new at the empty
one would split attribution across two lists that mean the same thing.

The cost of that choice, accepted knowingly: we own the mapping from a raw
``utm_source``/``utm_medium`` pair to a ``Lead Source`` value. It is
``derive_lead_source`` below, it is deliberately small, and it is only ever
consulted to fill a **blank** — it never overrides a human's choice.

## First touch wins, everywhere

Every write path in this module fills empty fields only. That is the whole
semantic: the campaign that *earned* the lead is the one that gets credit, not
whichever tracking parameter happened to be on the URL at the moment somebody
finally converted. ``_fill_blanks`` is the only function that writes attribution
onto a document, so there is exactly one place to audit that rule.

## The gate is a hook, not ``reqd``

Setting ``reqd = 1`` on the source field would break every API-created record and
retroactively invalidate the historical backlog. Instead ``enforce_source`` runs
on ``validate``, applies **only to new records**, and is gated by
``require_lead_source_on_*`` in ERPNext Enhancements Settings so it can be
switched off from the UI in seconds if it blocks the sales team mid-day.
Historical blanks are backfilled to ``Unknown (pre-Aug 2026)`` by
``patches.backfill_unknown_lead_source`` — a value that stays visibly a gap
rather than being laundered into a real channel.

## Defensive reads are mandatory

These hooks fire during erpnext's own test bootstrap, before this app's Custom
Fields exist. Every custom-field access goes through ``_get`` /
``_has_attribution_fields``, which is why nothing here reads ``doc.custom_*``
directly. Removing those guards turns a fresh-DB install into a crash.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

#: The Lead Source seeded for historical records that predate capture. Deliberately
#: reads as a gap in any report rather than as a real acquisition channel.
UNKNOWN_LEAD_SOURCE = "Unknown (pre-Aug 2026)"

#: The Lead Source assigned to a web-form submission we cannot classify further.
DEFAULT_WEB_LEAD_SOURCE = "Website"

#: Raw attribution fields, in form order. Data unless noted. These are OUR fields
#: (``custom_`` prefixed) and are Data/Small Text on purpose -- see the module
#: docstring for why erpnext's own Link-typed UTM fields cannot hold raw values.
ATTRIBUTION_FIELDS = (
	"custom_utm_source",
	"custom_utm_medium",
	"custom_utm_campaign",
	"custom_utm_content",
	"custom_utm_term",
	"custom_gclid",
	"custom_landing_page",
	"custom_first_referrer",
)

#: Stamped once, when attribution first lands on a record. Not part of
#: ATTRIBUTION_FIELDS because it is derived rather than captured.
CAPTURED_ON_FIELD = "custom_attribution_captured_on"

#: Every field this module propagates between records.
PROPAGATED_FIELDS = ATTRIBUTION_FIELDS + (CAPTURED_ON_FIELD, "custom_lead_source")

#: Column widths in the fixtures. Enforced here too, because the ingress endpoint
#: accepts arbitrary strings from a public form and a 2 KB referrer would
#: otherwise raise a database error on insert rather than being trimmed.
FIELD_MAX_LENGTHS = {
	"custom_utm_source": 140,
	"custom_utm_medium": 140,
	"custom_utm_campaign": 140,
	"custom_utm_content": 140,
	"custom_utm_term": 140,
	"custom_gclid": 255,
	"custom_landing_page": 500,
	"custom_first_referrer": 500,
}

#: utm_medium values that mean "we paid for this click".
PAID_MEDIUMS = frozenset({"cpc", "ppc", "paid", "paidsearch", "paid_search", "display", "banner", "retargeting"})

#: utm_medium values that mean social.
SOCIAL_MEDIUMS = frozenset({"social", "paid_social", "paidsocial", "social_paid", "sm"})

#: utm_medium values that mean email.
EMAIL_MEDIUMS = frozenset({"email", "e-mail", "newsletter", "mail"})


# --------------------------------------------------------------------- helpers


def _settings():
	return frappe.get_cached_doc("ERPNext Enhancements Settings")


def _flag(fieldname, settings=None):
	"""Read a Check from Settings, defaulting to off.

	``get`` rather than attribute access: the field may not exist yet on a bench
	that has not migrated, and an AttributeError inside ``validate`` would block
	every save of the doctype.
	"""
	settings = settings or _settings()
	return bool(cint(settings.get(fieldname) or 0))


def _has_attribution_fields(doctype):
	"""True if this bench has the attribution Custom Fields for ``doctype``.

	``doc_events`` fire during erpnext's test bootstrap, before fixtures are
	applied. Checking the column rather than the meta because a Custom Field row
	can exist before the column does during a partially-applied migrate.
	"""
	try:
		return frappe.db.has_column(doctype, "custom_utm_source")
	except Exception:
		return False


def _get(doc, fieldname):
	"""Read a custom field that may not exist. Returns "" rather than None so
	callers can treat blank and missing identically."""
	return (getattr(doc, fieldname, None) or "") if doc else ""


def _truncate(value, fieldname):
	value = (value or "").strip()
	limit = FIELD_MAX_LENGTHS.get(fieldname)
	return value[:limit] if limit else value


def has_any_attribution(doc):
	"""True if any raw attribution value is present on ``doc``."""
	return any(_get(doc, field) for field in ATTRIBUTION_FIELDS)


# ------------------------------------------------------------ the one writer


def _fill_blanks(target, values):
	"""Copy ``values`` onto ``target``, **never overwriting a non-empty field**.

	The single chokepoint for every attribution write in the app. First touch
	wins is enforced here and nowhere else, so this is the only function to audit
	when asking "can attribution be clobbered?".

	Returns the number of fields actually filled.
	"""
	filled = 0
	for fieldname in PROPAGATED_FIELDS:
		incoming = values.get(fieldname)
		if not incoming:
			continue
		if _get(target, fieldname):
			continue
		if not hasattr(target, fieldname):
			# Field not on this doctype (or not migrated yet). Skipping is right:
			# frappe would drop the attribute silently anyway, and a hard failure
			# here would block the save.
			continue
		target.set(fieldname, incoming)
		filled += 1
	return filled


def _snapshot(doc):
	"""The propagatable attribution currently on ``doc``, as a plain dict."""
	return {field: _get(doc, field) for field in PROPAGATED_FIELDS}


# --------------------------------------------------------------- normalisation


def normalize_payload(raw):
	"""Turn an arbitrary inbound dict into a clean attribution dict.

	Accepts both bare names (``utm_source``) and our prefixed ones
	(``custom_utm_source``) so the ingress endpoint can pass a web form's field
	names straight through. Unknown keys are dropped -- this is the
	mass-assignment boundary for the public endpoint, so the allowlist is the
	tuple above and nothing else.
	"""
	raw = raw or {}
	values = {}
	for fieldname in ATTRIBUTION_FIELDS:
		bare = fieldname[len("custom_") :]
		value = raw.get(fieldname) or raw.get(bare) or ""
		value = _truncate(value, fieldname)
		if value:
			values[fieldname] = value
	return values


def derive_lead_source(values):
	"""Best-effort ``Lead Source`` for a set of raw UTM values, or "".

	Deliberately coarse. It maps to the handful of ``Lead Source`` records that
	already exist and are already used in reporting; it does not invent taxonomy.
	Consulted only to fill a blank -- see ``resolve_lead_source``.

	The ordering matters: ``gclid`` is checked before medium because a Google Ads
	click is paid regardless of how the medium was tagged (or whether it was
	tagged at all -- auto-tagging sets gclid and nothing else).
	"""
	medium = (values.get("custom_utm_medium") or "").strip().lower()

	if values.get("custom_gclid"):
		return "Advertisement"
	if medium in PAID_MEDIUMS:
		return "Advertisement"
	if medium in EMAIL_MEDIUMS:
		return "Email Campaign"
	if medium in SOCIAL_MEDIUMS:
		return "Social Media Campaign"
	if values.get("custom_utm_source") or values.get("custom_landing_page"):
		# Tagged, but not in a way we classify (organic, referral, print QR code,
		# a medium nobody standardised). "Website" is honest: it arrived through
		# the site. The raw values stay on the record for anyone who wants more.
		return DEFAULT_WEB_LEAD_SOURCE
	return ""


def resolve_lead_source(values, explicit=None):
	"""The ``Lead Source`` to stamp, preferring an explicit choice.

	Returns "" when nothing can be resolved or when the resolved record does not
	exist on this site -- a Link to a missing record would fail validation and
	reject the whole submission, which is a bad trade for a nice-to-have.
	"""
	candidate = (explicit or "").strip() or derive_lead_source(values)
	if not candidate:
		return ""
	if not frappe.db.exists("Lead Source", candidate):
		return ""
	return candidate


# ------------------------------------------------------------------- capture


def stamp_capture_time(doc, method=None):
	"""``Lead.validate`` / ``Opportunity.validate``: stamp when attribution landed.

	Set once. A record that already carries a capture timestamp keeps it, so an
	edit years later does not restate the record as freshly attributed.
	"""
	if not _has_attribution_fields(doc.doctype):
		return
	if not has_any_attribution(doc):
		return
	if _get(doc, CAPTURED_ON_FIELD):
		return
	if hasattr(doc, CAPTURED_ON_FIELD):
		doc.set(CAPTURED_ON_FIELD, now_datetime())


# --------------------------------------------------------------- propagation


def propagate_to_opportunity(doc, method=None):
	"""``Opportunity.validate``: inherit attribution from the originating Lead.

	Only when ``opportunity_from == "Lead"``. Note the fountain-move flow creates
	Opportunities with ``opportunity_from = "Customer"`` on purpose (see
	``fountain_move/conversion.py`` -- a Lead id in ``party_name`` silently kills
	the closed-won hand-off), so this deliberately also falls back to the
	Customer path below.
	"""
	if not _has_attribution_fields("Opportunity"):
		return

	party = getattr(doc, "party_name", None)
	if not party:
		return

	origin = (getattr(doc, "opportunity_from", None) or "").strip()
	source_doc = None

	if origin == "Lead" and frappe.db.exists("Lead", party):
		source_doc = _fetch_attribution("Lead", party)
	elif origin == "Customer" and frappe.db.exists("Customer", party):
		source_doc = _fetch_attribution("Customer", party)

	if source_doc:
		_fill_blanks(doc, source_doc)

	stamp_capture_time(doc)


def propagate_to_customer(doc, method=None):
	"""``Customer.validate``: inherit attribution from the Lead it came from.

	erpnext's ``make_customer`` stamps ``lead_name`` on the Customer it builds, so
	that is the reliable back-link. When it is absent we try the Opportunity
	back-link ``custom_opportunities`` is *not* consulted -- it is a child table
	populated after insert, so it is empty at the moment this runs.
	"""
	if not _has_attribution_fields("Customer"):
		return

	lead = getattr(doc, "lead_name", None)
	if lead and frappe.db.exists("Lead", lead):
		_fill_blanks(doc, _fetch_attribution("Lead", lead))

	stamp_capture_time(doc)


def _fetch_attribution(doctype, docname):
	"""Read the propagatable fields off a stored record, tolerating a bench where
	some of them do not exist yet."""
	fields = [f for f in PROPAGATED_FIELDS if frappe.db.has_column(doctype, f)]
	if not fields:
		return {}
	row = frappe.db.get_value(doctype, docname, fields, as_dict=True) or {}
	return {k: v for k, v in row.items() if v}


def backfill_opportunity_to_customer(doc, method=None):
	"""``Opportunity.on_update``: push attribution forward onto the Customer.

	The Lead -> Customer link only exists when erpnext built the Customer from the
	Lead. Deals that arrive as Customer-first (the fountain-move path, and every
	manually-created account) need the other direction, or the Customer -- which
	is what the value-stream dashboards group by -- stays unattributed.

	Fills blanks only, and never touches a Customer that already has a source.
	"""
	if not _has_attribution_fields("Customer"):
		return
	if (getattr(doc, "opportunity_from", None) or "") != "Customer":
		return

	customer = getattr(doc, "party_name", None)
	if not customer or not frappe.db.exists("Customer", customer):
		return

	values = {k: v for k, v in _snapshot(doc).items() if v}
	if not values:
		return

	existing = _fetch_attribution("Customer", customer)
	updates = {k: v for k, v in values.items() if not existing.get(k)}
	if not updates:
		return

	if not existing.get(CAPTURED_ON_FIELD) and not updates.get(CAPTURED_ON_FIELD):
		updates[CAPTURED_ON_FIELD] = now_datetime()

	# db_set rather than a full save: this runs inside the Opportunity's own
	# on_update, and loading + saving the Customer here would re-enter every
	# Customer hook (Drive provisioning, contact sync) for a metadata-only change.
	frappe.db.set_value("Customer", customer, updates, update_modified=False)


# ------------------------------------------------------------------ the gate


def enforce_source(doc, method=None):
	"""``validate``: require a Lead Source on NEW Leads/Opportunities.

	Gated three ways, each of which matters:

	* ``lead_attribution_enabled`` -- the module master switch.
	* ``require_lead_source_on_lead`` / ``require_lead_source_on_opportunity`` --
	  per-doctype, so Opportunity can be enforced while Lead is still loose.
	* ``doc.is_new()`` -- an existing record is never blocked. Enforcing on every
	  save would mean nobody could edit any of the 815 historical Opportunities
	  until somebody backfilled them by hand.

	Bulk contexts (import, migrate, patch, install, test) are exempt: a hard throw
	during ``bench migrate`` aborts the whole migration, and the backfill patch
	itself writes records that would otherwise trip this.
	"""
	if not _has_attribution_fields(doc.doctype):
		return
	if not doc.is_new():
		return
	if _bulk_context():
		return

	settings = _settings()
	if not _flag("lead_attribution_enabled", settings):
		return

	toggle = {
		"Lead": "require_lead_source_on_lead",
		"Opportunity": "require_lead_source_on_opportunity",
	}.get(doc.doctype)
	if not toggle or not _flag(toggle, settings):
		return

	if _get(doc, "custom_lead_source"):
		return

	# Last chance before throwing: if the record carries raw UTM values we can
	# classify, stamp the derived source rather than stopping the user. A web
	# lead should never be blocked for a source we could work out ourselves.
	derived = resolve_lead_source(_snapshot(doc))
	if derived and hasattr(doc, "custom_lead_source"):
		doc.custom_lead_source = derived
		return

	frappe.throw(
		_("Lead Source is required. Pick where this {0} came from, or use '{1}' if it genuinely is not known.").format(
			_(doc.doctype), UNKNOWN_LEAD_SOURCE
		),
		title=_("Attribution required"),
	)


def _bulk_context():
	"""True during import/migrate/patch/install/test.

	``frappe.flags`` is a plain dict-alike; missing keys read as falsy.
	"""
	flags = frappe.flags
	return bool(
		flags.in_import
		or flags.in_migrate
		or flags.in_patch
		or flags.in_install
		or flags.in_test
		or flags.in_setup_wizard
	)
