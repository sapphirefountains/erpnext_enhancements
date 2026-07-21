# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Turn a Fountain Move Request into Customer / Address / Contact / Lead / Opportunity.

Runs in a background job. The staging row records every docname it creates, which
makes the whole thing resumable: a retry skips the steps that already produced a
live record and picks up where it failed.

**Why per-step commits instead of one transaction.** ERPNext names Customers by
``customer_name`` (``cust_master_name``), so a rolled-back-and-retried run would
not re-use "Jane Doe Residence" — it would create "Jane Doe Residence - 2", plus a
duplicate Address and Contact, on every attempt. Atomicity is the wrong trade for
master data whose identity is its name. We commit each step and treat the staging
row as the transaction log.

**Why the job re-authenticates.** ``frappe.enqueue`` captures
``frappe.session.user`` (``background_jobs.py:179``) and the worker restores it
(``:252``). The caller here is **Guest**, so without an explicit ``set_user`` every
record would be owned by Guest and every permission check would be made as an
anonymous user. Each entry point opens with ``frappe.set_user("Administrator")``
and restores in ``finally`` — the same shape as ``crm_enhancements/api.py``.

**Ordering constraints, all load-bearing:**

1. *Customer first.* Everything else links to it.
2. *Address before Contact*, so the Contact can carry ``address``.
3. *Contact's Customer Dynamic Link must be appended BEFORE insert* — frappe runs
   autoname before validate, and this site names Contacts
   ``field:custom_full_name_and_role``, built from ``links[0]``.
4. *Lead after Contact*, with ``utm_source="Existing Customer"`` and ``customer``
   set, or erpnext's ``Lead.before_insert`` mints a second, stray Contact.
5. *Opportunity last*, with ``opportunity_from="Customer"`` — see the note on
   :func:`_create_opportunity`.
"""

import frappe
from frappe.utils import cint, escape_html, flt, now_datetime, nowdate

from erpnext_enhancements import contacts_ux
from erpnext_enhancements.crm_enhancements.fountain_move import (
	DEFAULT_LEAD_SOURCE,
	DEFAULT_VALUE_STREAM,
	EXISTING_CUSTOMER_UTM_SOURCE,
	MAX_CONVERSION_ATTEMPTS,
	get_store_address,
	matching,
	notify,
	photos,
)

#: Statuses a conversion may legitimately start from.
CONVERTIBLE = ("New", "Queued", "Failed")


def run_conversion(docname, force=0):
	"""Convert one request. Safe to call twice; safe to call after a partial failure."""
	previous_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		_convert(docname, cint(force))
	except Exception:
		_record_failure(docname)
	finally:
		frappe.set_user(previous_user)


def _convert(docname, force):
	req = frappe.get_doc("Fountain Move Request", docname)

	# Row-lock the status so two workers cannot both claim the same request
	# (a retry pressed while the scheduled job is already running).
	status = frappe.db.get_value(
		"Fountain Move Request", docname, "status", for_update=True
	)
	if status == "Converting" and not force:
		return
	if status not in CONVERTIBLE and not force:
		return
	if req.is_spam:
		req.db_set("status", "Spam", update_modified=False)
		frappe.db.commit()
		return
	if cint(req.conversion_attempts) >= MAX_CONVERSION_ATTEMPTS and not force:
		return

	req.mark_converting()
	frappe.db.commit()

	settings = frappe.get_cached_doc("ERPNext Enhancements Settings")
	lead_source = _resolve_lead_source(settings)
	company = settings.get("fmr_company") or frappe.defaults.get_defaults().get("company")
	owner = _resolve_owner(req, settings)

	# ── 1. Which party is this? ───────────────────────────────────────────
	match = matching.resolve_party(req)
	if match.needs_review:
		req.db_set("status", "Duplicate Review", update_modified=False)
		req.db_set("match_basis", match.basis, update_modified=False)
		req.db_set("match_candidates", frappe.as_json(match.as_dict()), update_modified=False)
		frappe.db.commit()
		notify.notify_duplicate_review(req, match)
		return

	req.db_set("match_basis", match.basis, update_modified=False)
	reused_customer = bool(match.customer)

	# ── 2. Customer ───────────────────────────────────────────────────────
	customer = _step(
		req, "created_customer", "Customer",
		lambda: _create_customer(req, settings, lead_source),
		existing=match.customer,
	)
	req.db_set("reused_customer", 1 if reused_customer else 0, update_modified=False)

	# ── 3. Address — always new; a move has a new destination ─────────────
	address = _step(
		req, "created_address", "Address",
		lambda: _create_address(req, customer, reused_customer),
	)

	# ── 4. Contact ────────────────────────────────────────────────────────
	contact = _step(
		req, "created_contact", "Contact",
		lambda: _create_contact(req, customer, address, reused_customer),
		existing=match.contact,
		on_existing=lambda name: _refresh_contact(name, req, customer),
	)
	req.db_set("reused_contact", 1 if match.contact else 0, update_modified=False)

	# ── 5. Lead ───────────────────────────────────────────────────────────
	lead = _step(
		req, "created_lead", "Lead",
		lambda: _create_lead(req, customer, contact, settings, lead_source, company, owner),
		existing=match.lead,
		on_existing=lambda name: _refresh_lead(name, req, customer, contact, lead_source),
	)
	req.db_set("reused_lead", 1 if match.lead else 0, update_modified=False)

	# ── 6. Opportunity — always new ───────────────────────────────────────
	opportunity = _step(
		req, "created_opportunity", "Opportunity",
		lambda: _create_opportunity(
			req, customer, contact, address, settings, lead_source, company, owner
		),
	)

	# ── 7. Back-links ─────────────────────────────────────────────────────
	_link_everything(req, customer, address, contact, lead, opportunity)

	# ── 8. Photos ─────────────────────────────────────────────────────────
	photos.fan_out(req, [("Lead", lead), ("Customer", customer), ("Opportunity", opportunity)])
	frappe.enqueue(
		"erpnext_enhancements.crm_enhancements.fountain_move.photos.mirror_to_drive",
		queue="long",
		enqueue_after_commit=True,
		job_id=f"fmr-drive-{req.name}",
		deduplicate=True,
		docname=req.name,
	)

	req.db_set("status", "Converted", update_modified=False)
	req.db_set("converted_on", now_datetime(), update_modified=False)
	req.db_set("error", None, update_modified=False)
	frappe.db.commit()

	notify.notify_converted(req, owner)


# ---------------------------------------------------------------------------
# step plumbing
# ---------------------------------------------------------------------------


def _step(req, field, doctype, create, existing=None, on_existing=None):
	"""Run one conversion step idempotently and record its result.

	Skips entirely when the staging row already points at a live record — that is
	what makes a retry resume rather than duplicate. A pointer at something that
	no longer exists (deleted by hand between attempts) is healed rather than
	trusted.
	"""
	recorded = req.get(field)
	if recorded:
		if frappe.db.exists(doctype, recorded):
			return recorded
		req.db_set(field, None, update_modified=False)

	if existing and frappe.db.exists(doctype, existing):
		if on_existing:
			on_existing(existing)
		name = existing
	else:
		name = create()

	req.db_set(field, name, update_modified=False)
	frappe.db.commit()
	return name


def _record_failure(docname):
	"""Park the row as Failed with a readable traceback, then alert."""
	traceback = frappe.get_traceback()
	frappe.db.rollback()
	try:
		failed = frappe.get_doc("Fountain Move Request", docname)
		failed.db_set("status", "Failed", update_modified=False)
		failed.db_set("error", traceback, update_modified=False)
		frappe.db.commit()
		frappe.log_error(traceback, "Fountain Move Conversion", defer_insert=True)
		notify.notify_conversion_failure(failed)
	except Exception:
		# Never let the failure handler mask the original failure.
		frappe.log_error(frappe.get_traceback(), "Fountain Move: failure handler", defer_insert=True)


# ---------------------------------------------------------------------------
# record builders
# ---------------------------------------------------------------------------


def _create_customer(req, settings, lead_source):
	"""Create the account.

	Naming follows the operator's rule: a Residential move is "<First> <Last>
	Residence" (distinguishing the household from the person), a Commercial one is
	just "<First> <Last>".

	``ignore_mandatory`` mirrors ``api/telephony.py``: ``customer_group`` and
	``territory`` are mandatory on Customer but Selling Settings carries no
	default on this site, and the documented alternative — falling back to an
	arbitrary leaf — is how every unknown caller once ended up stamped
	"Government" / "Asia". Blank is the honest value.
	"""
	customer = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": build_customer_name(req),
			"customer_type": req.property_type,
			"customer_group": _customer_group(req, settings),
			"territory": settings.get("fmr_territory") or None,
			"custom_accounts_email_address": req.email,
			"custom_accounts_phone_number": req.phone,
			"custom_lead_source": lead_source,
			"custom_account_status": "Lead",
			"custom_preferred_contact_method": "Email" if cint(req.contact_consent) else None,
		}
	)
	customer.flags.ignore_mandatory = True
	try:
		customer.insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		# Name collision with an existing account we did NOT match on email or
		# phone (a namesake, or the same person under a different address).
		# Disambiguate rather than merging into a record we cannot verify is them.
		customer.customer_name = f"{customer.customer_name} ({req.name})"
		customer.insert(ignore_permissions=True)
	return customer.name


def build_customer_name(req):
	"""Compose the account name per the operator's Residential/Commercial rule.

	Single-spaced deliberately: the QuickBooks import taught us that a stray
	double space becomes part of the docname and then fails to match anything.
	Truncated because ``customer_name`` becomes the docname.
	"""
	full = " ".join(part for part in ((req.first_name or "").strip(), (req.last_name or "").strip()) if part)
	full = " ".join(full.split())
	if req.property_type == "Residential":
		full = f"{full} Residence" if full else "Residence"
	return full[:130].strip() or f"Fountain Move {req.name}"


def _customer_group(req, settings):
	"""Configured group for the property type, if it is a usable leaf.

	v16 rejects group nodes outright ("Cannot select a Group type Customer
	Group"), so a mis-set group-typed value must degrade to blank rather than
	failing the whole conversion.
	"""
	field = (
		"fmr_customer_group_residential"
		if req.property_type == "Residential"
		else "fmr_customer_group_commercial"
	)
	group = settings.get(field)
	if not group or not frappe.db.exists("Customer Group", group):
		return None
	if frappe.db.get_value("Customer Group", group, "is_group"):
		return None
	return group


def _create_address(req, customer, reused_customer):
	"""Create the move destination.

	``custom_full_address`` is deliberately never set here —
	``script_migrations.address.set_full_address`` recomputes it on every save and
	it is the doctype's ``title_field``. ``country`` is mandatory with no default.

	Not marked primary when the account already existed: overwriting a known
	customer's primary address with a one-off move destination would be wrong.
	"""
	address = frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": customer,
			"address_type": "Shipping",
			"address_line1": req.address_line1,
			"address_line2": req.address_line2,
			"city": req.city,
			"state": req.state,
			"pincode": req.pincode,
			"country": req.country or "United States",
			"email_id": req.email,
			"phone": req.phone,
			"is_shipping_address": 1,
			"is_primary_address": 0 if reused_customer else 1,
		}
	)
	address.append("links", {"link_doctype": "Customer", "link_name": customer})
	address.insert(ignore_permissions=True)
	return address.name


def _create_contact(req, customer, address, reused_customer):
	"""Create the person.

	Two non-obvious requirements:

	* The Customer Dynamic Link is appended **before** ``insert()``. Naming runs
	  before validate, and ``script_migrations.contact.set_full_name_and_role``
	  builds the unique ``custom_full_name_and_role`` from ``links[0]``.
	* Contact details go in ``custom_email`` / ``custom_phone_number`` /
	  ``custom_mobile_number``, never the stock ``email_id`` / ``phone`` /
	  ``mobile_no`` — those are hidden on this site (one is relabelled
	  "DONTUSE-Mobile No") and the sync hooks read the custom ones.
	"""
	contact = frappe.get_doc(
		{
			"doctype": "Contact",
			"first_name": req.first_name,
			"last_name": req.last_name,
			"custom_email": req.email,
			"custom_phone_number": req.phone,
			"custom_mobile_number": req.phone,
			"custom_preferred_contact_method": "Email" if cint(req.contact_consent) else None,
			"custom_account": customer,
			"address": address,
			"is_primary_contact": 0 if reused_customer else 1,
		}
	)
	contacts_ux._insert_customer_link_first(contact, customer)
	contact.insert(ignore_permissions=True)
	return contact.name


def _refresh_contact(name, req, customer):
	"""Fill blanks on a reused Contact and make sure it links to the account.

	Only ever fills empty fields. A returning customer may have corrected their
	phone number since; a web form must not overwrite that.
	"""
	contact = frappe.get_doc("Contact", name)
	changed = False
	for field, value in (
		("custom_email", req.email),
		("custom_phone_number", req.phone),
		("custom_mobile_number", req.phone),
	):
		if value and not contact.get(field):
			contact.set(field, value)
			changed = True

	if not any(
		link.link_doctype == "Customer" and link.link_name == customer
		for link in (contact.links or [])
	):
		contact.append("links", {"link_doctype": "Customer", "link_name": customer})
		changed = True

	if changed:
		contact.save(ignore_permissions=True)


def _create_lead(req, customer, contact, settings, lead_source, company, owner):
	"""Create the Lead.

	``utm_source = "Existing Customer"`` together with ``customer`` is what stops
	erpnext's ``Lead.before_insert`` creating its own Contact — we already made
	one, and it had to be ours because of the autoname/link ordering. It is also
	simply true by this point: the Customer exists.

	Attribution goes in ``custom_lead_source``, NOT the stock ``source`` field —
	erpnext renamed that to ``utm_source`` in v15 and it points at a different
	taxonomy. See ``patches.drop_orphan_source_property_setters``.
	"""
	lead = frappe.get_doc(
		{
			"doctype": "Lead",
			"first_name": req.first_name,
			"last_name": req.last_name,
			"lead_name": build_customer_name(req),
			"email_id": req.email,
			"mobile_no": req.phone,
			"phone": req.phone,
			"status": "Lead",
			"lead_owner": owner,
			"company": company,
			"territory": settings.get("fmr_territory") or None,
			"city": req.city,
			"state": req.state,
			"country": req.country or "United States",
			"customer": customer,
			"utm_source": _existing_customer_utm(),
			"custom_lead_source": lead_source,
			"custom_lead_source_details": _referral_note(req),
			"custom_account_type": req.property_type,
			"custom_account_name": customer,
			"custom_account_link": customer,
			"custom_contact_link": contact,
			"custom_contact_first_name": req.first_name,
			"custom_contact_last_name": req.last_name,
			"custom_contact_email": req.email,
			"custom_contact_phone_number": req.phone,
			"custom_address_line_1": req.address_line1,
			"custom_address_line_2": req.address_line2,
			"custom_postal_code": req.pincode,
			"custom_service_interest": "Service",
			"custom_contact_method": "Email" if cint(req.contact_consent) else None,
			"custom_lead_details": build_lead_details_html(req),
		}
	)
	lead.insert(ignore_permissions=True)
	_sweep_stray_contact(lead, contact)
	return lead.name


def _existing_customer_utm():
	"""The UTM Source that suppresses erpnext's automatic Contact, if it exists.

	Seeded by patch. Returning None when absent is safe — we lose the suppression
	(and :func:`_sweep_stray_contact` cleans up after erpnext) but the conversion
	still completes, which beats failing outright over a taxonomy row.
	"""
	if frappe.db.exists("UTM Source", EXISTING_CUSTOMER_UTM_SOURCE):
		return EXISTING_CUSTOMER_UTM_SOURCE
	return None


def _sweep_stray_contact(lead, ours):
	"""Delete the duplicate Contact erpnext creates when suppression didn't take.

	``Lead.before_insert`` skips its automatic Contact only when
	``utm_source == "Existing Customer"`` AND ``customer`` resolves. We satisfy
	both, so this should never fire — but the condition lives in erpnext and can
	change under us, and a silent duplicate Contact per submission is exactly the
	kind of slow mess nobody notices for months.
	"""
	stray = getattr(lead, "contact_doc", None)
	stray_name = getattr(stray, "name", None)
	if not stray_name or stray_name == ours:
		return
	frappe.log_error(
		f"Lead {lead.name} produced an unexpected auto-Contact {stray_name}; "
		f"ours is {ours}. Suppression via utm_source may have stopped working.",
		"Fountain Move: stray Contact",
		defer_insert=True,
	)
	try:
		frappe.delete_doc(
			"Contact", stray_name, force=1, ignore_permissions=True, delete_permanently=True
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Fountain Move: stray Contact cleanup", defer_insert=True)


def _refresh_lead(name, req, customer, contact, lead_source):
	"""Append this submission to a reused Lead without trampling it."""
	lead = frappe.get_doc("Lead", name)
	if not lead.get("custom_lead_source"):
		lead.custom_lead_source = lead_source
	if not lead.get("custom_account_link"):
		lead.custom_account_link = customer
	if not lead.get("custom_contact_link"):
		lead.custom_contact_link = contact
	if not lead.get("customer"):
		lead.customer = customer
	lead.custom_lead_details = append_details_block(
		lead.get("custom_lead_details"), build_lead_details_html(req)
	)
	lead.save(ignore_permissions=True)


def _create_opportunity(req, customer, contact, address, settings, lead_source, company, owner):
	"""Create the Opportunity.

	``opportunity_from = "Customer"``, never ``"Lead"``, and that is not cosmetic.
	``crm_enhancements/api.py`` maps ``party_name`` straight into
	``Project.customer`` when a won Opportunity is handed off, and inserts with
	``ignore_validate`` — which skips ``validate()`` but NOT ``_validate_links()``.
	A Lead id there raises inside a ``try/except log_error``, silently killing the
	Closed-Won → Project hand-off. Google Drive provisioning likewise only fires
	for Customer-party opportunities. Since a Customer exists by this point,
	pointing at the Lead instead would be a self-inflicted regression.

	Consequence: ``script_migrations.opportunity.update_lead_status`` (which flips
	a Lead to Converted for Lead-party opportunities) does not fire, so
	:func:`_link_everything` sets the Lead status explicitly.

	The four mandatory fields are ``opportunity_owner`` (Property Setter),
	``custom_opportunity_name`` and ``custom_value_stream`` (Custom Fields), plus
	the stock ``party_name``.
	"""
	opportunity = frappe.get_doc(
		{
			"doctype": "Opportunity",
			"opportunity_from": "Customer",
			"party_name": customer,
			"company": company,
			"transaction_date": nowdate(),
			"status": "Qualification",
			"opportunity_owner": owner,
			"custom_opportunity_name": _opportunity_title(req),
			"custom_value_stream": [{"value_stream": _value_stream(settings)}],
			"custom_lead_source": lead_source,
			"contact_person": contact,
			"customer_address": address,
			"primary_address": address,
			"primary_contact": contact,
			"territory": settings.get("fmr_territory") or None,
			"custom_general_scope_description": build_lead_details_html(req),
			"custom_notes_for_scheduling": _scheduling_note(req),
		}
	)
	_attach_primary_contact_details(opportunity, contact)
	opportunity.insert(ignore_permissions=True)
	return opportunity.name


def _attach_primary_contact_details(opportunity, contact):
	"""Mirror the Contact's details onto the Opportunity's primary_contact_* fields.

	``sync_contact.sync_from_main_doc`` pushes these DOWN onto the Contact on
	update, and its job-title branch has no truthiness guard — so leaving them
	blank here would blank the Contact's ``custom_title`` on the first save.
	Setting them from the Contact we just resolved keeps the sync a no-op.

	Guarded with ``has_field`` because these three fields exist only in the live
	database (they are in neither the fixtures nor ``setup/custom_fields.py``), so
	a fresh install genuinely does not have them.
	"""
	details = frappe.db.get_value(
		"Contact", contact, ["custom_email", "custom_phone_number", "custom_mobile_number", "custom_title"],
		as_dict=True,
	) or {}
	meta = frappe.get_meta("Opportunity")
	mapping = {
		"primary_contact_email": details.get("custom_email"),
		"primary_contact_phone": details.get("custom_phone_number") or details.get("custom_mobile_number"),
		"primary_contact_job_title": details.get("custom_title"),
	}
	for field, value in mapping.items():
		if value and meta.has_field(field):
			opportunity.set(field, value)


def _opportunity_title(req):
	store = (req.purchase_location or "").replace("Cactus & Tropicals", "C&T").strip()
	where = req.city or "Unknown"
	title = f"Fountain Move — {where}"
	if store:
		title = f"{title} ({store})"
	return title[:140]


def _value_stream(settings):
	configured = settings.get("fmr_value_stream")
	if configured and frappe.db.exists("Value Streams", configured):
		return configured
	return DEFAULT_VALUE_STREAM


def _resolve_lead_source(settings):
	configured = settings.get("fmr_lead_source")
	if configured and frappe.db.exists("Lead Source", configured):
		return configured
	if frappe.db.exists("Lead Source", DEFAULT_LEAD_SOURCE):
		return DEFAULT_LEAD_SOURCE
	return None


def _resolve_owner(req, settings):
	"""Whoever sent the invite owns the deal; the configured default otherwise.

	The public URL has no sender, and neither does an invite from someone who has
	since been deactivated — hence the fallback. ``opportunity_owner`` is
	mandatory, so a blank result fails the conversion with a readable error rather
	than assigning the lead to nobody.
	"""
	if req.invite:
		sender = frappe.db.get_value("Fountain Move Invite", req.invite, "sent_by")
		if sender and frappe.db.get_value("User", sender, "enabled"):
			return sender

	default_owner = settings.get("fmr_default_owner")
	if default_owner and frappe.db.get_value("User", default_owner, "enabled"):
		return default_owner

	frappe.throw(
		"No Opportunity owner could be resolved. Set 'Default Opportunity Owner' in "
		"ERPNext Enhancements Settings → Fountain Move Defaults, then retry this request."
	)


# ---------------------------------------------------------------------------
# linking
# ---------------------------------------------------------------------------


def _link_everything(req, customer, address, contact, lead, opportunity):
	"""Wire the five records together and mark the Lead converted."""
	if lead:
		lead_updates = {"status": "Converted"}
		if frappe.get_meta("Lead").has_field("custom_opportunity"):
			lead_updates["custom_opportunity"] = opportunity
		frappe.db.set_value("Lead", lead, lead_updates, update_modified=False)

		# erpnext recomputes Lead status from whether it has a Customer; setting
		# lead_name keeps its own view consistent with ours.
		if not frappe.db.get_value("Customer", customer, "lead_name"):
			frappe.db.set_value("Customer", customer, "lead_name", lead, update_modified=False)

	_ensure_link("Address", address, "Opportunity", opportunity)
	_ensure_link("Contact", contact, "Opportunity", opportunity)
	if lead:
		_ensure_link("Contact", contact, "Lead", lead)

	frappe.db.commit()


def _ensure_link(doctype, name, link_doctype, link_name):
	"""Append a Dynamic Link if absent. Always re-fetches first.

	The re-fetch matters: erpnext's ``Lead.after_insert`` calls
	``link_to_contact()``, which re-saves the Contact. Holding a stale in-memory
	copy across that boundary raises ``TimestampMismatchError`` on the next save.
	"""
	if not name or not link_name:
		return False
	doc = frappe.get_doc(doctype, name)
	if any(
		link.link_doctype == link_doctype and link.link_name == link_name
		for link in (doc.links or [])
	):
		return False
	doc.append("links", {"link_doctype": link_doctype, "link_name": link_name})
	doc.save(ignore_permissions=True)
	return True


# ---------------------------------------------------------------------------
# the details blob
# ---------------------------------------------------------------------------


def build_lead_details_html(req):
	"""Everything the submitter told us that has no native Lead field.

	Values are escaped with ``frappe.utils.escape_html``, NOT ``strip_html``:
	strip_html is a tag remover, so it happily passes ``<img src=x onerror=...>``
	through as text-with-attributes while also destroying legitimate customer
	prose like "gate is < 3 ft wide". Escaping is both safer and lossless.
	"""

	def cell(value):
		return escape_html(str(value)) if value not in (None, "") else "—"

	def yesno(value):
		return "Yes" if cint(value) else "No"

	store_address = get_store_address(req.purchase_location) or req.purchase_location_address
	destination = ", ".join(
		part
		for part in (req.address_line1, req.address_line2, req.city, req.state, req.pincode)
		if part
	)
	rows = [
		("Purchased at", req.purchase_location),
		("Store address", store_address),
		("Fountain weight", f"{flt(req.fountain_weight_lbs):g} lbs" if req.fountain_weight_lbs else None),
		("Property type", req.property_type),
		("Destination", destination),
		("Address source", "Google Places" if cint(req.address_autocompleted) else "Typed by hand"),
		("Water at destination", yesno(req.water_access)),
		("Electricity at destination", yesno(req.electricity_access)),
		("May contact by email/text", yesno(req.contact_consent)),
		("Terms accepted", f"{yesno(req.terms_accepted)} ({req.terms_accepted_on or '—'})"),
		("Submitted", req.submitted_on),
		("Request", req.name),
	]

	body = "".join(
		f"<tr><td style='padding:2px 12px 2px 0'><b>{escape_html(label)}</b></td>"
		f"<td style='padding:2px 0'>{cell(value)}</td></tr>"
		for label, value in rows
	)
	return (
		"<p><b>Cactus &amp; Tropicals fountain move request</b></p>"
		f"<table>{body}</table>"
	)


def append_details_block(existing, block):
	"""Prepend a new details block to a reused Lead's notes, bounded.

	Newest first, older blocks kept below a rule. Capped because an anonymous
	submitter must not be able to grow a column on a pre-existing record without
	limit — five submissions of history is plenty to reconstruct what happened.
	"""
	existing = (existing or "").strip()
	if not existing:
		return block
	combined = f"{block}<hr>{existing}"
	parts = combined.split("<hr>")
	combined = "<hr>".join(parts[:5])
	return combined[:40000]


def _scheduling_note(req):
	bits = []
	if req.purchase_location:
		bits.append(f"Pick up from {req.purchase_location}.")
	store_address = get_store_address(req.purchase_location)
	if store_address:
		bits.append(f"Store address: {store_address}.")
	if req.fountain_weight_lbs:
		bits.append(f"Fountain weight: {flt(req.fountain_weight_lbs):g} lbs.")
	bits.append(f"Water at destination: {'yes' if cint(req.water_access) else 'no'}.")
	bits.append(f"Electricity at destination: {'yes' if cint(req.electricity_access) else 'no'}.")
	if not cint(req.address_autocompleted):
		bits.append("Destination address was typed by hand — worth confirming before dispatch.")
	return " ".join(bits)


def _referral_note(req):
	note = f"Cactus & Tropicals referral — purchased at {req.purchase_location or 'an unspecified store'}"
	return note[:140]
