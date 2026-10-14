"""Delete or link the orphaned Address rows two data imports left behind (v1.639.0).

A read-only audit on 2026-10-13 found 445 Address rows with no Dynamic Link -- an address that
belongs to no Customer, Supplier, Contact or anything else, so no party form, picker or report
ever shows it. Two groups came from imports and are settled here by rule; the cleanup was
approved on 2026-10-13. Nothing in this file names a record: every target is found on the
server by the predicates below.

**Rule D -- the CRM import (delete).** The 2025-07-10 CRM import, run as Administrator, wrote
each "<Account> (Shipping)-Shipping" address with another account's street (a systematic row
mis-join), plus a few "-Billing-1" duplicates. Every one is now a copy of an address that IS
linked to the rightful Customer. An Address is deleted when ALL of:

* it has no Dynamic Link row;
* its ``creation`` falls on 2025-07-10 (site-local, which is how Frappe stores it) and its
  ``owner`` is ``Administrator``;
* its normalized (``address_line1``, ``city``) equals the normalized pair of some OTHER
  Address that has a Dynamic Link to a Customer. Normalized means lowercased with every
  non-alphanumeric character dropped -- spaces, commas, periods -- so "Suite 5," matches
  "suite 5" and a trailing space is not a difference. Done in Python on purpose: MariaDB's
  PAD SPACE collation makes SQL whitespace comparisons lie. A blank ``address_line1`` never
  matches anything.

``frappe.delete_doc(..., ignore_permissions=True)`` **without** ``force``: anything that Links
to the Address makes ``delete_doc`` refuse (``LinkExistsError``), and the row is counted
``delete_refused`` and listed, never forced. Each delete runs in its own savepoint, because
v16 runs ``on_trash`` before the link check and a refusal must not leave half a delete behind.
A deleted Address goes to Deleted Document, so this is recoverable. No match -> left alone,
counted ``delete_skipped_no_match`` and listed.

**Rule L -- the supplier import (link).** The 2025-10-03 supplier import created "<X>-Billing"
addresses for existing Suppliers and never linked them. It ran as one batch inside a single
minute -- every row it wrote has a ``creation`` between 09:39:09 and 09:39:24 site time, and
nothing else on the site was created in that minute -- so the batch is identified by that
window rather than by who ran it (other addresses entered by hand the same day are not part of
it and are left alone). An Address gets one Dynamic Link row to its Supplier when ALL
of:

* it has no Dynamic Link row;
* its ``creation`` is at or after 2025-10-03 09:39:00 and before 09:40:00;
* its name is exactly "<X>-Billing" and X is the ``name`` or ``supplier_name`` of exactly one
  Supplier (several -> ``link_skipped_ambiguous``; none -> ``link_skipped_no_supplier``);
* no Customer's ``name`` or ``customer_name`` equals X (compared case-insensitively and
  stripped, so the check errs towards skipping). A name shared by a Customer and a Supplier
  is a decision for a person: ``link_skipped_ambiguous``, listed.

The link is written as a bare child row -- ``frappe.get_doc({"doctype": "Dynamic Link",
"parenttype": "Address", "parentfield": "links", "parent": ..., "name": <hash>, ...})
.db_insert()`` -- never ``Address.save()``. ``db_insert`` is one INSERT: with ``name``
supplied it skips ``set_new_name`` (and so the naming hooks), and it runs no validate, no
``doc_events`` (the app's wildcard before_validate normalizer would otherwise rewrite the
address text) and no Address ``before_save`` hooks. The parent row is not touched, so its
``modified`` stays as it was. ``link_title`` is set to what Address.validate would set: the
Supplier's title (``supplier_name``), else its name. A child table is read from
``tabDynamic Link`` by (parent, parenttype, parentfield), so the form shows the link at once.

**Everything else is left alone** -- in particular the addresses people entered since
go-live, which are a separate review.

Cannot raise -- a patch that raises aborts ``bench migrate``, which on this repo is the
deploy. Every row is guarded on its own (a ``failed`` bucket), the printed tally is checked to
add up (it reports a mismatch rather than asserting), and the skipped and failed docnames go to
the console and one Error Log. A missing table or column (a fresh install, ERPNext's test
bootstrap) makes it a no-op. Safe twice: a deleted row is gone and a linked row has a link, so
the second run finds nothing to do.
"""

import frappe

CRM_IMPORT_DATE = "2025-07-10"
CRM_IMPORT_OWNER = "Administrator"
#: The supplier import's one-minute batch, [start, end) in site-local time.
SUPPLIER_BATCH_START = "2025-10-03 09:39:00"
SUPPLIER_BATCH_END = "2025-10-03 09:40:00"
BILLING_SUFFIX = "-Billing"
SAVEPOINT = "cleanup_orphan_import_addresses"
LOG = "cleanup_orphan_import_addresses"

DELETE_OUTCOMES = ("deleted", "delete_skipped_no_match", "delete_refused", "delete_failed")
LINK_OUTCOMES = ("linked", "link_skipped_ambiguous", "link_skipped_no_supplier", "link_failed")
#: Outcomes whose docnames are listed for a person.
LISTED = (
	"delete_skipped_no_match",
	"delete_refused",
	"delete_failed",
	"link_skipped_ambiguous",
	"link_skipped_no_supplier",
	"link_failed",
)


def normalize(value):
	"""Lowercase, keep letters and digits only: "Suite 5, " and "suite 5" are the same text."""
	return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _key(row):
	return (normalize(row.get("address_line1")), normalize(row.get("city")))


def _day(value):
	"""The site-local date of a stored datetime, as YYYY-MM-DD."""
	return str(value or "")[:10]


def _second(value):
	"""A stored datetime as "YYYY-MM-DD HH:MM:SS", so it compares as text against the window."""
	return str(value or "")[:19]


def _in_supplier_batch(row):
	return SUPPLIER_BATCH_START <= _second(row.get("creation")) < SUPPLIER_BATCH_END


def _has(doctype, column):
	try:
		return bool(frappe.db.has_column(doctype, column))
	except Exception:
		return False


def execute():
	counts = dict.fromkeys(DELETE_OUTCOMES + LINK_OUTCOMES, 0)
	listed = {k: [] for k in LISTED}
	universe = None
	try:
		if not (
			_has("Address", "address_line1")
			and _has("Address", "city")
			and _has("Dynamic Link", "link_doctype")
		):
			print(f"{LOG}: the Address or Dynamic Link table is missing; nothing to do.")
			return _tally(counts)
		universe = _run(counts, listed)
	except Exception:
		try:
			frappe.log_error(title="Orphaned address cleanup failed", message=frappe.get_traceback())
		except Exception:
			pass
		print(f"{LOG}: aborted early; see the Error Log.")
	try:
		_report(counts, listed, universe)
	except Exception:
		pass
	return _tally(counts)


def _run(counts, listed):
	addresses = frappe.get_all("Address", fields=["name", "owner", "creation", "address_line1", "city"])
	links = frappe.get_all(
		"Dynamic Link", filters={"parenttype": "Address"}, fields=["parent", "link_doctype"]
	)
	linked = {row.get("parent") for row in links}
	customer_linked = {row.get("parent") for row in links if row.get("link_doctype") == "Customer"}

	# Normalized (line 1, city) -> the Customer-linked Addresses carrying it.
	customer_keys = {}
	for row in addresses:
		if row.get("name") in customer_linked:
			customer_keys.setdefault(_key(row), set()).add(row.get("name"))

	orphans = sorted((row for row in addresses if row.get("name") not in linked), key=lambda r: r.get("name"))
	delete_rows = [
		row
		for row in orphans
		if _day(row.get("creation")) == CRM_IMPORT_DATE and row.get("owner") == CRM_IMPORT_OWNER
	]
	link_rows = [
		row
		for row in orphans
		if _in_supplier_batch(row) and str(row.get("name") or "").endswith(BILLING_SUFFIX)
	]
	universe = {"delete": len(delete_rows), "link": len(link_rows)}

	# -- Rule D: delete the CRM import's copies ---------------------------------------------
	for row in delete_rows:
		name = row.get("name")
		line1, city = _key(row)
		matches = customer_keys.get((line1, city), set()) - {name} if line1 else set()
		if not matches:
			counts["delete_skipped_no_match"] += 1
			listed["delete_skipped_no_match"].append(name)
			continue
		_delete(name, counts, listed)

	# -- Rule L: link the supplier import's billing addresses ------------------------------
	if link_rows:
		suppliers, supplier_titles, customer_names = _parties()
		for row in link_rows:
			name = row.get("name")
			party = name[: -len(BILLING_SUFFIX)]
			found = suppliers.get(party.strip(), set())
			if not found:
				counts["link_skipped_no_supplier"] += 1
				listed["link_skipped_no_supplier"].append(name)
				continue
			if len(found) > 1 or party.strip().casefold() in customer_names:
				counts["link_skipped_ambiguous"] += 1
				listed["link_skipped_ambiguous"].append(name)
				continue
			supplier = next(iter(found))
			_link(name, supplier, supplier_titles.get(supplier) or supplier, counts, listed)

	return universe


def _parties():
	"""Supplier lookup by name and supplier_name, and every Customer name, casefolded."""
	suppliers, titles, customer_names = {}, {}, set()
	if _has("Supplier", "supplier_name"):
		for row in frappe.get_all("Supplier", fields=["name", "supplier_name"]):
			titles[row.get("name")] = row.get("supplier_name")
			for value in (row.get("name"), row.get("supplier_name")):
				key = str(value or "").strip()
				if key:
					suppliers.setdefault(key, set()).add(row.get("name"))
	if _has("Customer", "customer_name"):
		for row in frappe.get_all("Customer", fields=["name", "customer_name"]):
			for value in (row.get("name"), row.get("customer_name")):
				key = str(value or "").strip().casefold()
				if key:
					customer_names.add(key)
	return suppliers, titles, customer_names


def _delete(name, counts, listed):
	try:
		frappe.db.savepoint(SAVEPOINT)
	except Exception:
		counts["delete_failed"] += 1
		listed["delete_failed"].append(name)
		_log_row_failure(name, "delete (no savepoint)")
		return
	try:
		result = frappe.delete_doc("Address", name, ignore_permissions=True)
		if result is False:  # vanished between the read and the delete
			raise RuntimeError("the Address no longer exists")
	except frappe.LinkExistsError:
		_rollback()
		counts["delete_refused"] += 1
		listed["delete_refused"].append(name)
		return
	except Exception:
		_rollback()
		counts["delete_failed"] += 1
		listed["delete_failed"].append(name)
		_log_row_failure(name, "delete")
		return
	counts["deleted"] += 1


def _rollback():
	try:
		frappe.db.rollback(save_point=SAVEPOINT)
	except Exception:
		pass


def _link(name, supplier, title, counts, listed):
	try:
		frappe.get_doc(
			{
				"doctype": "Dynamic Link",
				"name": frappe.generate_hash(length=10),
				"parenttype": "Address",
				"parentfield": "links",
				"parent": name,
				"idx": 1,
				"link_doctype": "Supplier",
				"link_name": supplier,
				"link_title": title,
			}
		).db_insert()
	except Exception:
		counts["link_failed"] += 1
		listed["link_failed"].append(name)
		_log_row_failure(name, "link to its Supplier")
		return
	counts["linked"] += 1
	try:
		frappe.clear_document_cache("Address", name)
	except Exception:
		pass


def _log_row_failure(name, what):
	try:
		frappe.log_error(
			title="Orphaned address cleanup: a row failed",
			message=f"Address {name}: {what} failed.\n{frappe.get_traceback()}",
			reference_doctype="Address",
			reference_name=name,
		)
	except Exception:
		print(f"{LOG}: {what} failed for Address {name}")


def _tally(counts):
	return {
		"deleted": counts["deleted"],
		"delete_skipped_no_match": counts["delete_skipped_no_match"],
		"delete_refused": counts["delete_refused"],
		"linked": counts["linked"],
		"link_skipped_ambiguous": counts["link_skipped_ambiguous"],
		"link_skipped_no_supplier": counts["link_skipped_no_supplier"],
		"failed": counts["delete_failed"] + counts["link_failed"],
	}


def _check(counts, universe):
	"""Every candidate lands in exactly one outcome of its rule. Reports; never asserts."""
	if universe is None:
		return "not checked (the run stopped early)"
	problems = []
	for rule, keys in (("delete", DELETE_OUTCOMES), ("link", LINK_OUTCOMES)):
		got = sum(counts[k] for k in keys)
		if got != universe[rule]:
			problems.append(f"{rule} outcomes sum to {got}, expected {universe[rule]}")
	if problems:
		return "MISMATCH: " + "; ".join(problems)
	return f"consistent ({universe['delete']} delete candidates, {universe['link']} link candidates)"


def _report(counts, listed, universe):
	print(f"{LOG}: {_tally(counts)}")
	print(f"{LOG}: {_check(counts, universe)}")
	lines = []
	for bucket in LISTED:
		for name in listed[bucket]:
			lines.append(f"{bucket}: {name}")
	for line in lines:
		print(f"  {line}")
	if lines:
		try:
			frappe.log_error(
				title="Orphaned addresses left for a person",
				message="Orphaned address cleanup (v1.639.0) left these alone:\n" + "\n".join(lines),
			)
		except Exception:
			pass
