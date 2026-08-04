"""One-time migration patch (post_model_sync; listed in patches.txt).

Makes the attribution gap **visible** instead of silently blank.

As of 2026-08-04 the picture was: 815 Opportunities, of which 809 carried no
``custom_lead_source`` and 814 carried no ``utm_source``; plus ~225 Leads in the
same state. The 45%-missing figure quoted in the August marketing review was
measured against ``tabOpportunity.source`` — a column that still physically
exists but has had **no DocField behind it since erpnext v15 renamed the field to
``utm_source``**. Nothing reads or writes it, and it is invisible in the UI. So
the real coverage was not 55%; it was approximately zero.

This patch seeds one Lead Source, ``Unknown (pre-Aug 2026)``, and stamps it on
every Lead and Opportunity that has no source at all.

**Why a named bucket rather than leaving them blank.** A blank is ambiguous — it
could mean "nobody filled it in", "the record predates capture", or "the field
did not exist". A dated bucket says exactly one thing, and it stays visibly a gap
in any channel report rather than being laundered into a real acquisition channel
like "Website" or "Word of Mouth". It also lets the ``require_lead_source_on_*``
gate be switched on without retroactively invalidating a decade of history.

**Direct SQL, deliberately.** Loading and saving ~1,000 documents would fire
every Lead/Opportunity hook this app installs (contact sync, project hand-off,
value-stream defaults, Drive provisioning), for a metadata-only change, inside a
migration. That is both slow and a genuine risk of side effects. The update
touches one column and does not move ``modified``, so it does not perturb
anything that watches for record changes.

Idempotent by construction: the UPDATE is filtered to blank values, so a second
run matches zero rows. Safe to re-run.
"""

import frappe

from erpnext_enhancements.crm_enhancements.attribution import UNKNOWN_LEAD_SOURCE

#: Doctypes backfilled, in the order a human would read them. The set is closed
#: by construction because ``_backfill`` interpolates the name into a table name
#: — same rule as ``patches.seed_cactus_tropicals_lead_source.SEEDABLE``.
TARGETS = ("Lead", "Opportunity")

#: Only records created before this date are bucketed. The label says
#: "pre-Aug 2026" and it has to stay true: a sourceless record created *after*
#: the cutoff is a live process failure, and stamping it here would hide exactly
#: what the gate and the Attribution Gaps report exist to surface.
CUTOFF = "2026-08-01"


def execute():
	if not _ensure_lead_source():
		return

	for doctype in TARGETS:
		_backfill(doctype)


def _ensure_lead_source():
	"""Create the bucket Lead Source. Returns False if it cannot exist here.

	``Lead Source`` is ``istable: 1`` with no fields and no ``autoname`` on this
	site, so ``naming.set_new_name`` nulls the name and falls through to
	``make_autoname("hash")`` — which is how the junk hash-named rows in
	``Value Streams`` got there. ``insert(set_name=...)`` short-circuits that
	branch entirely (``document.py`` assigns ``validate_name(...)`` and never
	calls ``set_new_name``), and works on an istable doctype too. Same technique
	as ``patches.seed_cactus_tropicals_lead_source``.
	"""
	if not frappe.db.exists("DocType", "Lead Source"):
		# Taxonomy doctype absent (a bench without erpnext's CRM module). Nothing
		# to seed, and throwing here would abort the whole migrate.
		return False

	if frappe.db.exists("Lead Source", UNKNOWN_LEAD_SOURCE):
		return True

	try:
		frappe.get_doc({"doctype": "Lead Source"}).insert(
			set_name=UNKNOWN_LEAD_SOURCE, ignore_permissions=True
		)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Attribution: ORM seed failed for Lead Source '{UNKNOWN_LEAD_SOURCE}', using direct insert",
		)
		frappe.db.sql(
			"""INSERT INTO `tabLead Source` (name, creation, modified, modified_by, owner, docstatus)
				VALUES (%s, NOW(), NOW(), 'Administrator', 'Administrator', 0)""",
			(UNKNOWN_LEAD_SOURCE,),
		)
	return True


def _backfill(doctype):
	"""Stamp the bucket onto every sourceless record of ``doctype``."""
	if doctype not in TARGETS:
		raise ValueError(f"refusing to backfill unexpected doctype {doctype!r}")
	if not frappe.db.has_column(doctype, "custom_lead_source"):
		# Custom Field fixtures have not been applied on this bench yet. The
		# patch runs post_model_sync so this should not happen, but a partially
		# applied migrate is exactly the state where a crash is most expensive.
		return

	# `frappe.qb` would be tidier, but a plain UPDATE keeps the "blank only"
	# filter and the "no modified bump" decision visible in one place.
	frappe.db.sql(
		f"""
		UPDATE `tab{doctype}`
		SET custom_lead_source = %(bucket)s
		WHERE (custom_lead_source IS NULL OR custom_lead_source = '')
		  AND creation < %(cutoff)s
		""",
		{"bucket": UNKNOWN_LEAD_SOURCE, "cutoff": CUTOFF},
	)
