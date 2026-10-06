# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Remove the wrong-company links the directory's Link Existing fanned out (one-off, v1.575.0).

Until v1.575.0 the Contacts/Addresses directory's **Link Existing** linked the picked record to
the open form *and* to its Customer *and* to every Project Stakeholder company
(``sync_contact.link_existing_record``). A county health inspector linked from a Harwood project
was filed as a Harwood and a Layton Construction contact, and ``contacts_ux`` then showed
"Account: Harwood" on him. The 2026-10-06 audit traced every such link to the save that made it;
:data:`WRONG_LINKS` is that list, after independent review, minus anything the office has to
decide (e.g. the duplicate Jaxon Kier contact,
the draft quotation SAL-QTN-2026-01918 that names Nathan Brooks as Layton's contact, and the
job-site links in :data:`FOR_THE_OFFICE`). It also clears Nikolas Bradshaw's own staff contact of
the test and placeholder records it was linked to, at his request.

**This only removes links, and only the ones listed, and only the rows the review saw.** It never
adds a link, never touches a link that is not on the list, and leaves every record's link to its
own company and to the job alone. Each wrong link is matched by value *and* by the Dynamic Link
row name it had at the 2026-10-06 audit:

* a listed value on a row with a different name was linked again after the audit. Someone chose
  it, so it is theirs, and this script must not remove it on the strength of an older review;
* an audited row name that now holds a different value means the company was renamed or merged
  (``rename_doc`` rewrites Dynamic Link rows in place and keeps their names), so the review no
  longer describes that row.

Either case, a record that still carries a listed link but lost its own-company or job link
(or, for a staff member's own contact, is no longer theirs), or a record renamed or deleted, is
**blocked**, and one blocked record stops the whole run before the first save. A record with
nothing listed left on it is reported clean. Edits to anything else on these records do not
matter: the office being asked to fix a primary contact first cannot stall the cleanup.

**How a link is removed.** By a normal ``save()`` of the Contact/Address, not raw SQL and not
``sync_contact.unlink_record``:

* ``save()`` runs ``contacts_ux.sync_contact_account_links``, so a supplier's person whose
  Account was the fanned-out Customer gets a blank Account in the same save, and a Version row
  records exactly which link went;
* every wrong row on a record goes in ONE save, so the Account never passes through a second
  wrong company on the way;
* ``unlink_record`` rebuilds the whole links table (new row names, every survivor recorded as
  re-added) and writes a Directory Link Exclusion, neither of which is wanted here.

``flags.is_syncing`` is set so ``sync_contact.sync_from_contact`` does not push the Contact's
details up onto the Projects it is primary contact of: no detail changes, and a cleanup should
not cascade into Project saves.

Values are compared in Python: MariaDB's collation is case-insensitive and ignores trailing spaces,
Python's comparison does neither. Nathan Brooks' Harwood row has two audited names, because James
Harris's Unlink on 2026-10-06 rebuilt his rows under new names (prod has the new one, a mirror
restored before then the old one).

Run it (dry run first, always; the mirror before production)::

    bench --site erp.local execute erpnext_enhancements.crm_enhancements.party_link_cleanup.run
    bench --site erp.local execute erpnext_enhancements.crm_enhancements.party_link_cleanup.run \\
        --kwargs "{'dry_run': False}"

A second run finds nothing to do and writes nothing.
"""

import frappe

#: One entry per record: what it is, the link that must survive on it (``home``: the company it
#: belongs to, or for Home address-Billing the customer whose drafts use it) and the job it is on
#: (both must still be linked while anything listed is left, or nothing runs; a staff member's
#: own contact has neither and is guarded by ``staff_user`` instead), and the wrong links to remove, each with the
#: Dynamic Link row name(s) it had at the audit. Only a row still carrying one of those names, and
#: still holding that value, is removed.
WRONG_LINKS = (
	# PRJ-00703, Harwood - SCLPT Spa Cold Plunge & Water Wall Design (customer Harwoo; stakeholder Layton)
	{
		"doctype": "Contact",
		"name": "Nathan Brooks-Summit County Health Department",
		"home": ("Supplier", "Summit County Health Department"),
		"job": ("Project", "PRJ-00703"),
		"remove": (
			("Customer", "Harwoo", ("odncm9k2ie", "359vd90rli")),
			# Removed by hand on prod 2026-10-06 10:35; still present on a mirror restored earlier.
			# Draft quotation SAL-QTN-2026-01918 names Nathan as Layton's contact: on such a mirror
			# the quotation then fails validate_party_contact until the office repoints it.
			("Customer", "Layton Construction Company", ("359idf5ch7",)),
		),
	},
	{
		"doctype": "Contact",
		"name": "David Peterson-Watershape Consulting",
		"home": ("Supplier", "Watershape Consulting"),
		"job": ("Project", "PRJ-00703"),
		"remove": (
			("Customer", "Harwoo", ("46ru6n8rrs",)),
			("Customer", "Layton Construction Company", ("46ru9bsfqa",)),
		),
	},
	{
		"doctype": "Contact",
		"name": "Krista Flores",
		"home": ("Supplier", "Watershape Consulting"),
		"job": ("Project", "PRJ-00703"),
		"remove": (
			("Customer", "Harwoo", ("36vjns6lq7",)),
			("Customer", "Layton Construction Company", ("36vj1p67h2",)),
		),
	},
	{
		"doctype": "Contact",
		"name": "Zach Fisher-elemental Electrical Engineers",
		"home": ("Supplier", "elemental Electrical Engineers"),
		"job": ("Project", "PRJ-00703"),
		"remove": (
			("Customer", "Harwoo", ("2982rp84jd",)),
			("Customer", "Layton Construction Company", ("29809nbd87",)),
		),
	},
	{
		"doctype": "Contact",
		"name": "Tony Carver-JTB HVAC & Plumbing Engineering",
		"home": ("Supplier", "JTB HVAC & Plumbing Engineering"),
		"job": ("Project", "PRJ-00703"),
		"remove": (
			("Customer", "Harwoo", ("4akc3m1i2u",)),
			("Customer", "Layton Construction Company", ("4ak25l3a1k",)),
		),
	},
	# PRJ-00699 (customer Lowe Property Group)
	{
		"doctype": "Contact",
		"name": "Ana Loza",
		"home": ("Customer", "MVE + Partners"),
		"job": ("Project", "PRJ-00699"),
		"remove": (("Customer", "Lowe Property Group", ("bt5pq4shls",)),),
	},
	{
		"doctype": "Contact",
		"name": "Mitchel Adamson",
		"home": ("Customer", "Kier Construction"),
		"job": ("Project", "PRJ-00699"),
		"remove": (("Customer", "Lowe Property Group", ("dv60j0vsg4",)),),
	},
	{
		"doctype": "Contact",
		"name": "Ryan Blanch",
		"home": ("Customer", "Kier Construction"),
		"job": ("Project", "PRJ-00699"),
		"remove": (("Customer", "Lowe Property Group", ("5v9qi8jvql",)),),
	},
	{
		"doctype": "Contact",
		"name": "Kaden Hill-Wadman Corporation",
		"home": ("Customer", "Wadman Corporation"),
		"job": ("Project", "PRJ-00699"),
		"remove": (("Customer", "Lowe Property Group", ("q0cs5tbvls",)),),
	},
	# PRJ-00604 (customer Wadman Corporation)
	{
		"doctype": "Contact",
		"name": "Kae Schwalber-AJC Architects",
		"home": ("Customer", "AJC Architects"),
		"job": ("Project", "PRJ-00604"),
		"remove": (("Customer", "Wadman Corporation", ("08d0bdqrpf",)),),
	},
	{
		"doctype": "Contact",
		"name": "Chelsy Atonich-AJC Architects",
		"home": ("Customer", "AJC Architects"),
		"job": ("Project", "PRJ-00604"),
		"remove": (("Customer", "Wadman Corporation", ("f8if4t8vgv",)),),
	},
	{
		"doctype": "Contact",
		"name": "Dijana Rambo",
		"home": ("Customer", "AJC Architects"),
		"job": ("Project", "PRJ-00604"),
		"remove": (("Customer", "Wadman Corporation", ("faala7j3ba",)),),
	},
	# PRJ-00706 (customer BJ Carey)
	{
		"doctype": "Contact",
		"name": "Tanner Hamblin",
		"home": ("Supplier", "Hamblin Welding & Custom Fabrication"),
		"job": ("Project", "PRJ-00706"),
		"remove": (("Customer", "BJ Carey", ("7t04n83p6s",)),),
	},
	# CRM-OPP-2026-00153 (party Hess Construction LLC)
	{
		"doctype": "Contact",
		"name": "Brett Catmull-MRG Electric",
		"home": ("Supplier", "MRG Electric"),
		"job": ("Opportunity", "CRM-OPP-2026-00153"),
		"remove": (("Customer", "Hess Construction LLC", ("kbm8qts4eq",)),),
	},
	# PRJ-00700 (customer Kapture Vision)
	{
		"doctype": "Contact",
		"name": "Earnest Wallace-San Diego Convention Center",
		"home": ("Customer", "San Diego Convention Center"),
		"job": ("Project", "PRJ-00700"),
		"remove": (("Customer", "Kapture Vision", ("a7jtuukkm5",)),),
	},
	# PRJ-00739, ERPNext Accounting Migration (customer Sapphire Fountains, the company itself).
	# Removed on the user's decision (2026-10-06): the company's outside CPA is not a person of its
	# own Customer record.
	{
		"doctype": "Contact",
		"name": "John Juntunen-Skyline Pathway CPAs",
		"home": ("Supplier", "Skyline Pathway CPAs"),
		"job": ("Project", "PRJ-00739"),
		"remove": (("Customer", "Sapphire Fountains", ("9fd7lf0nn0",)),),
	},
	# Addresses: PRJ-00219's stakeholder suppliers, PRJ-00695's stakeholders.
	{
		"doctype": "Address",
		"name": "Sanctuary Apartments-Other",
		"home": ("Customer", "Salt Development"),
		"job": ("Project", "PRJ-00219"),
		"remove": (
			("Supplier", "New Cast Stone", ("ebogahqcog",)),
			("Supplier", "Shamrock Plumbing", ("ebogk5minu",)),
			("Supplier", "Valley View Granite", ("ebogh0e5of",)),
		),
	},
	{
		# "home" is the link that must survive. Kodiak America's link was itself added by the same
		# 2026-10-01 fan-out save, but four Kodiak drafts now use this address, so it stays and
		# the office decides (FOR_THE_OFFICE).
		"doctype": "Address",
		"name": "Home address-Billing",
		"home": ("Customer", "Kodiak America"),
		"job": ("Project", "PRJ-00695"),
		"remove": (
			("Supplier", "Adaptive Design Group Inc.", ("nuqfkhe49s",)),
			("Customer", "Martineau Homes", ("nuqsmrqe24",)),
		),
	},
	# A staff member's OWN contact (decided 2026-10-06): Nikolas Bradshaw's Contact carries a
	# renamed telephony placeholder Customer and a Fountain Move test Lead and Opportunity. Frappe
	# files every email a contact sends or receives under all of its links (10,541 Communication
	# Links on these records by 2026-10-06), and Address.link_address used to file his addresses
	# there too. No company or job survives here: the guard is that it is still his own contact.
	{
		"doctype": "Contact",
		"name": "Nikolas Bradshaw",
		"staff_user": "nikolas.bradshaw@sapphirefountains.com",
		"home": None,
		"job": None,
		"remove": (
			("Customer", "Nikolas Bradshaw Residence", ("d28pqqvmn4",)),
			("Lead", "CRM-LEAD-2026-00009", ("qpd7gmgdm3",)),
			("Opportunity", "CRM-OPP-2026-00137", ("qpm99mhed1",)),
		),
	},
)

#: Left for the office on purpose; printed with every run so they are not forgotten.
FOR_THE_OFFICE = (
	"Contact 'Jaxon Kier-Lowe Property Group' (jaxon@kier.com) duplicates 'Jaxon Kier-Kier "
	"Construction' and has no references: DELETE it. Do not merge it: Document Merge copies the "
	"duplicate's wrong Lowe Property Group link onto the Kier contact.",
	"Draft Quotation SAL-QTN-2026-01918 (Layton Construction Company) names 'Nathan Brooks-Summit "
	"County Health Department' as its contact. Pick a Layton contact before it becomes a Sales Order.",
	"Customer 'Harwoo' has no correct flagged primary contact once Nathan Brooks is unlinked: pick one.",
	"Address 'Test Address-Billing-1' is test data still linked to Lowe Property Group: delete it.",
	# Job sites filed under a job's customer: Saltair and the Stenmark lot by the New Address
	# dialog, two older Hess sites by the stock New Address before that dialog existed, and the Rob
	# Wise residence by the same 2026-10-01 Link Existing fan-out this script partly undoes. Not
	# removed here: drafts use some of them, and removing the customer link would make those fail.
	"Address 'Jobsite-Permanent' (Saltair, PRJ-00604) is Wadman Corporation's PRIMARY address and "
	"the billing address on draft ACC-SINV-2026-01750. Put Wadman's real billing address on the "
	"invoice and as its primary, then decide whether the job site stays linked to Wadman.",
	"Address 'Home address-Billing' (Rob Wise residence, PRJ-00695) is Kodiak America's only "
	"linked address and the billing address on drafts SAL-QTN-2026-00038, SAL-QTN-2026-00045, "
	"ACC-SINV-2026-00126, ACC-SINV-2026-01687. Kodiak's real one, 'Kodiac-Billing', has no link "
	"to Kodiak at all: link it, use it on the drafts, then decide about the job site.",
	"Addresses 'Stenmark-Billing' (Wasatch Peaks lot), 'Samani Residence-Billing' and "
	"'Jobsite-Other' (Gruett residence, Hess's PRIMARY address) are job sites filed as Hess "
	"Construction LLC addresses. Decide whether each stays linked to Hess.",
	"After this cleanup Suppliers 'New Cast Stone' and 'Shamrock Plumbing' have no address, and "
	"Customer 'Martineau Homes' has none linked ('Martineau Homes-Billing' has no link to it). "
	"Add or link their real addresses.",
)


def _pairs(doc):
	return [(row.link_doctype, row.link_name) for row in doc.links or []]


def _plan(entry):
	"""What would happen to one record, without writing anything.

	Returns ``(status, detail, doc, rows)``: ``status`` is ``"remove"``, ``"clean"`` (nothing
	listed is still linked) or ``"blocked"`` (the record no longer matches the review; nothing may
	run).
	"""
	doctype, name = entry["doctype"], entry["name"]
	if not frappe.db.exists(doctype, name):
		return "blocked", f"{doctype} {name!r} no longer exists", None, []

	doc = frappe.get_doc(doctype, name)
	if doc.name != name:
		# frappe.db.exists matches case-insensitively and ignores trailing spaces; the list does not.
		return "blocked", f"{doctype} is named {doc.name!r}, not {name!r}", None, []

	audited = {}  # audited row name -> the value it held at the review
	for dt, nm, seen in entry["remove"]:
		for row_name in seen:
			audited[row_name] = (dt, nm)
	wanted = set(audited.values())
	for role in ("home", "job"):
		if entry.get(role) and tuple(entry[role]) in wanted:
			# A mistake in the list itself: block, whatever the record looks like.
			return "blocked", f"its {role} link is also listed for removal", None, []

	rows = []
	for row in doc.links:
		value = (row.link_doctype, row.link_name)
		if row.name in audited and audited[row.name] != value:
			return (
				"blocked",
				f"row {row.name} now links {value[0]} {value[1]!r}, not {audited[row.name][1]!r}: "
				"the company was renamed or merged since the review",
				None,
				[],
			)
		if value in wanted:
			if row.name not in audited:
				return (
					"blocked",
					f"{value[0]} {value[1]!r} was linked again after the review (row {row.name}): "
					"someone chose it, review by hand",
					None,
					[],
				)
			rows.append(row)

	if not rows:
		# Nothing listed is left (already cleaned, by this script or by hand).
		return "clean", "nothing listed is still linked", doc, []

	if entry.get("staff_user") and (doc.get("user") or "") != entry["staff_user"]:
		return "blocked", f"it is no longer {entry['staff_user']}'s own contact: review by hand", None, []

	linked = _pairs(doc)
	for role in ("home", "job"):
		if entry.get(role) and tuple(entry[role]) not in linked:
			return "blocked", f"its {role} link {entry[role]} is gone: review by hand", None, []

	return "remove", ", ".join(f"{r.link_doctype} {r.link_name!r} (row {r.name})" for r in rows), doc, rows


def _account_after(doc, rows):
	"""The Account this Contact will have once ``rows`` are removed, computed by the real rule."""
	from erpnext_enhancements import contacts_ux

	before = frappe.get_doc(doc.doctype, doc.name)
	trial = frappe.get_doc(doc.doctype, doc.name)
	drop = {r.name for r in rows}
	trial.links = [r for r in trial.links if r.name not in drop]
	trial._doc_before_save = before
	contacts_ux.sync_contact_account_links(trial)
	return trial.custom_account


def run(dry_run=True):
	"""Plan every record; if every one is safe and ``dry_run`` is False, remove the listed links.

	Prints one line per record and returns a summary dict. With ``dry_run`` (the default) nothing
	is written. Applied, any blocked record stops the whole run before the first save, and the
	saves share one transaction, so it is all or nothing.
	"""
	dry_run = frappe.parse_json(dry_run) if isinstance(dry_run, str) else dry_run
	print(
		f"party_link_cleanup on {frappe.local.site}: {'DRY RUN, nothing will be written' if dry_run else 'APPLYING'}"
	)

	plans = []
	for entry in WRONG_LINKS:
		status, detail, doc, rows = _plan(entry)
		account = None
		if status == "remove" and entry["doctype"] == "Contact":
			account = (doc.custom_account or None, _account_after(doc, rows))
		plans.append((entry, status, detail, doc, rows, account))

	for entry, status, detail, _doc, _rows, account in plans:
		line = f"  [{status:7}] {entry['doctype']} {entry['name']!r}: {detail}"
		if account and account[0] != account[1]:
			line += f"; Account {account[0]!r} -> {account[1]!r}"
		print(line)

	blocked = [p for p in plans if p[1] == "blocked"]
	todo = [p for p in plans if p[1] == "remove"]
	summary = {
		"site": frappe.local.site,
		"dry_run": bool(dry_run),
		"records": len(plans),
		"to_change": len(todo),
		"links_to_remove": sum(len(p[4]) for p in todo),
		"already_clean": sum(1 for p in plans if p[1] == "clean"),
		"blocked": [f"{p[0]['doctype']} {p[0]['name']}: {p[2]}" for p in blocked],
		"changed": [],
	}

	if blocked:
		print(f"\n{len(blocked)} record(s) changed since the audit. NOTHING was written. Review them first.")
		frappe.db.rollback()
		return summary

	if dry_run:
		print(f"\nWould remove {summary['links_to_remove']} link(s) on {len(todo)} record(s).")
		frappe.db.rollback()
	else:
		for entry, _status, _detail, doc, rows, _account in todo:
			for row in rows:
				doc.remove(row)  # renumbers idx itself
			doc.flags.is_syncing = True  # no upward push onto Projects: no detail changed
			doc.save()
			after = _pairs(frappe.get_doc(entry["doctype"], entry["name"]))
			gone = [(r.link_doctype, r.link_name) for r in rows]
			assert not any(p in after for p in gone), f"{entry['name']}: a link survived the save"
			kept = [tuple(entry[role]) for role in ("home", "job") if entry.get(role)]
			assert all(link in after for link in kept), f"{entry['name']}: lost a kept link"
			summary["changed"].append(entry["name"])
		print(f"\nRemoved {summary['links_to_remove']} link(s) on {len(todo)} record(s).")

	print(
		"\nFor the office, ONLY AFTER this cleanup has been applied on production (not after a dry "
		"run). Picking Harwood's primary contact while Nathan Brooks is still linked to Harwood would "
		"strip his Health Department primary flag. Not changed by this script:"
	)
	for item in FOR_THE_OFFICE:
		print(f"  - {item}")
	return summary
