# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Cactus & Tropicals fountain-move intake — shared constants and lookups.

A customer buys a fountain at Cactus & Tropicals, C&T recommends us to move it,
and the customer fills in the public form at ``/fountain-move``. The submission
lands as a **Fountain Move Request** and converts, in a background job, into a
linked Customer / Address / Contact / Lead / Opportunity set.

Module layout::

    __init__.py    constants, the guest field allowlist, store-location lookup
    intake.py      the three guest endpoints (begin / upload / submit)
    matching.py    duplicate resolution — which party does this submission belong to?
    conversion.py  the staging-row -> five-CRM-records engine
    photos.py      File fan-out onto Lead/Customer/Opportunity + Drive mirroring
    notify.py      failure / duplicate-review / new-submission alerts
    invites.py     the desk "Send Intake Link" flow
    api.py         desk RPC facade (retry, mark spam, copy link)

Security posture is documented in ``intake.py``. The one rule that belongs here:
**never build a document by splatting guest input.** ``INTAKE_FIELD_MAP`` is the
complete set of keys an anonymous caller may influence; everything else on the
staging doctype is server-set. ``read_only`` in a DocType JSON is a UI hint, not
an authorisation boundary — under ``ignore_permissions=True`` (which the guest
insert requires, since Guest holds no DocPerm) frappe's higher-permlevel check
returns early, so a splatted payload could set ``status``, ``turnstile_verdict``
or the ``created_*`` links directly.
"""

import frappe

#: Built-in Cactus & Tropicals stores.
#:
#: The operator-editable list lives in **ERPNext Enhancements Settings → Fountain
#: Move Intake → Partner Store Locations** and is seeded from this constant by
#: ``patches.seed_fountain_move_defaults``. This copy stays as the fallback for
#: :func:`get_store_locations` so an empty or fully-disabled table degrades to a
#: working form rather than a dropdown with no options — which would be an
#: unsubmittable public form with no server-side symptom.
CT_LOCATIONS = (
	{
		"location_name": "Cactus & Tropicals Midvale",
		"store_address": "7696 S Main Street, Midvale, UT 84047",
	},
	{
		"location_name": "Cactus & Tropicals Draper",
		"store_address": "12252 Draper Gate Drive, Draper, UT 84020",
	},
	{
		"location_name": "Cactus & Tropicals Salt Lake City",
		"store_address": "2735 South 2000 East, Salt Lake City, UT 84109",
	},
)

#: Default Lead Source stamped on every converted record. Seeded by
#: ``patches.seed_cactus_tropicals_lead_source``; overridable in Settings.
DEFAULT_LEAD_SOURCE = "Cactus & Tropicals"

#: Default Value Stream for the created Opportunity (mandatory on Opportunity).
DEFAULT_VALUE_STREAM = "Service"

#: erpnext's ``Lead.before_insert`` creates a duplicate Contact unless
#: ``utm_source`` is exactly this string AND ``customer`` is set. We create the
#: Contact ourselves, so we set both to suppress the stray. Seeded as a UTM
#: Source record by ``patches.seed_cactus_tropicals_lead_source``.
EXISTING_CUSTOMER_UTM_SOURCE = "Existing Customer"

#: The complete set of payload keys an anonymous submitter may set, mapped to
#: their Fountain Move Request fieldnames. Anything not listed is ignored.
#: Values are still length-, charset- and type-validated in ``intake.py``;
#: this map only bounds *which* fields exist.
INTAKE_FIELD_MAP = {
	"first_name": "first_name",
	"last_name": "last_name",
	"email": "email",
	"phone": "phone",
	"address_line1": "address_line1",
	"address_line2": "address_line2",
	"city": "city",
	"state": "state",
	"pincode": "pincode",
	"property_type": "property_type",
	"purchase_location": "purchase_location",
	"fountain_weight_lbs": "fountain_weight_lbs",
	"water_access": "water_access",
	"electricity_access": "electricity_access",
	"contact_consent": "contact_consent",
	"terms_accepted": "terms_accepted",
	"google_place_id": "google_place_id",
	"formatted_address": "formatted_address",
	"latitude": "latitude",
	"longitude": "longitude",
	"address_autocompleted": "address_autocompleted",
}

#: Per-field character ceilings applied at the guest boundary. Frappe's ``Data``
#: columns are 140 chars and raise ``CharacterLengthExceededError`` past that;
#: more importantly, the composed ``customer_name`` becomes a docname, so
#: unbounded input here becomes unbounded input to ``validate_name``.
FIELD_MAX_LENGTHS = {
	"first_name": 60,
	"last_name": 60,
	"email": 140,
	"phone": 30,
	"address_line1": 100,
	"address_line2": 100,
	"city": 60,
	"state": 40,
	"pincode": 12,
	"property_type": 20,
	"purchase_location": 140,
	"google_place_id": 255,
	"formatted_address": 255,
}

#: Checkbox-ish payload keys, coerced to 0/1 rather than length-checked.
CHECKBOX_FIELDS = (
	"water_access",
	"electricity_access",
	"contact_consent",
	"terms_accepted",
	"address_autocompleted",
)

#: Valid ``property_type`` values. These map onto ``Customer.customer_type``,
#: whose options a Property Setter constrains to Commercial/Residential/Partnership.
PROPERTY_TYPES = ("Residential", "Commercial")

#: Turnstile ``action`` asserted on siteverify. A token minted for a different
#: widget/action on some other site must not be replayable here.
TURNSTILE_ACTION = "fountain-move-intake"

#: The honeypot field name. Lives here so the template that renders it and the
#: endpoint that checks for it cannot drift apart — if they ever disagree, the
#: honeypot silently stops catching anything and nothing looks wrong.
#: Named to look worth auto-filling, and positioned off-screen rather than
#: display:none (which some bots specifically skip).
HONEYPOT_FIELD_NAME = "company_website"

#: Folder new intake photos are filed under before conversion attaches them.
INTAKE_FILE_FOLDER = "Home/Fountain Move Intake"

#: Company phone offered to customers when the form cannot help them — the
#: no-JavaScript notice, the rate-limit and generic error messages, the success
#: screen, and the invite email.
#:
#: Defined once because it appears in five customer-facing places; five copies
#: would drift, and the failure mode is a real person dialling a wrong number at
#: the exact moment our form has already let them down.
#: :func:`get_contact_phone` prefers ``Company.phone_no`` when an operator has
#: filled it in, so this is the fallback rather than the authority.
CONTACT_PHONE = "(801) 837-2199"

#: Automatic conversion attempts before a request is left Failed for a human.
#: Lives here rather than in ``conversion`` because ``notify`` needs it too, and
#: ``conversion`` already imports ``notify`` — the reverse import would be circular.
MAX_CONVERSION_ATTEMPTS = 3


def get_contact_phone():
	"""The phone number to show a customer the form has failed.

	Prefers ``Company.phone_no`` so an operator can change it without a deploy;
	falls back to :data:`CONTACT_PHONE`. Never raises and never returns empty —
	this is rendered into copy that already says "call us", so a blank here would
	produce a sentence telling someone to call nobody.
	"""
	try:
		company = frappe.defaults.get_defaults().get("company")
		if company:
			configured = frappe.db.get_value("Company", company, "phone_no")
			if configured and str(configured).strip():
				return str(configured).strip()
	except Exception:
		pass
	return CONTACT_PHONE


def get_store_locations(include_disabled=False):
	"""Return the partner store locations offered on the public form.

	Reads the operator-editable Settings child table and falls back to
	:data:`CT_LOCATIONS` when it is empty or every row is disabled. The fallback
	is the whole point: this runs on the guest render path, and a dropdown with
	no options is an unsubmittable form that looks fine to the operator.

	Returns a list of ``{"location_name", "store_address"}`` dicts.
	"""
	rows = []
	try:
		settings = frappe.get_cached_doc("ERPNext Enhancements Settings")
		for row in settings.get("fountain_move_locations") or []:
			if not include_disabled and row.get("disabled"):
				continue
			name = (row.get("location_name") or "").strip()
			if not name:
				continue
			rows.append(
				{
					"location_name": name,
					"store_address": (row.get("store_address") or "").strip(),
				}
			)
	except Exception:
		# Settings unreadable (fresh install, doctype not yet synced). The
		# built-in list below keeps the form working; never break the render.
		frappe.log_error(
			frappe.get_traceback(), "Fountain Move: store locations unreadable", defer_insert=True
		)
		rows = []

	return rows or [dict(loc) for loc in CT_LOCATIONS]


def get_store_address(location_name):
	"""Street address for a stored ``purchase_location``, or ``""`` if unknown.

	Looks through disabled rows too — a request submitted last month must still
	render its pickup address after the store is retired from the dropdown.
	"""
	target = (location_name or "").strip()
	if not target:
		return ""
	for loc in get_store_locations(include_disabled=True):
		if loc["location_name"] == target:
			return loc["store_address"]
	return ""


def is_valid_store_location(location_name):
	"""True when ``location_name`` is currently offered on the form.

	Enforced server-side at submit: the dropdown is client-side markup and an
	anonymous caller can POST anything.
	"""
	target = (location_name or "").strip()
	return any(loc["location_name"] == target for loc in get_store_locations())
