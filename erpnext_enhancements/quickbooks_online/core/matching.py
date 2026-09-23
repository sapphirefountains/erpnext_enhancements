"""Record-matching review for the QuickBooks Online migration.

The dashboard's "Link Existing Records" dialog answers one question -- *which QBO records
have no Sync Mapping row yet?* -- and on a site where Import All has run the answer is
none. Every master record gets a mapping row on import (``Created``, ``Auto Matched`` or
``Pending Review``), so the dialog is empty for exactly the people who now have to decide
whether those rows are right. This module is the review the migration actually needs:
every master mapping in one queue, what the import decided, the records it could have
picked instead, and one action that re-points the link and folds away the duplicate the
import created.

Things this module is careful about:

* **The newest payload per record is fetched in one query per entity type**, not one per
  row. ``QuickBooks Raw Payload`` holds ~433k rows on production and until v1.474.0 had no
  index on ``qbo_id``, so a per-row ``_latest_raw_payload`` was a full scan each; a 50-row
  page would have been fifty of them. The index now exists (``search_index`` on the
  doctype) and the batch query keeps a page cheap even on a site that has not built it yet.
* **A merge is only ever of the record the import itself created.** ``merge_plan`` refuses
  unless the previous link was ``Created`` -- a record a person made, or one the import
  linked *to*, is never folded into another because the accountant re-pointed a QBO id. It
  only merges like into like: a job consolidated onto its parent Customer that is now
  linked to a Project keeps the Customer. And it never merges Projects: the app's Project
  Merge tool cancels rather than deletes, which is the right thing for a record with Tasks
  and Timesheets hanging off it.
* **Account merges are pre-checked the way ERPNext's own ``merge_account`` checks them**
  (is_group, root_type, company, currency), because ``rename_doc(merge=True)`` alone would
  happily fold a Liability into an Asset.
* **The link is committed before the merge is attempted**, so a merge ERPNext refuses (a
  currency mismatch, a stock item with a different UOM) leaves the accountant's decision in
  place with the duplicate still there, never a half-applied decision. The refusal comes
  back as ``merge.status == "failed"`` with its message, and nothing is logged with
  ``log_error`` -- a bare re-raise out of here would publish frame locals to the Error Log.
* **``rename_doc`` re-points every Link and Dynamic Link to the merged record**, which
  includes ``QuickBooks Sync Mapping.erpnext_name`` -- so any *other* QBO id that was linked
  to the duplicate follows it onto the survivor without a second decision. ``rebuild_search``
  is off: one global-search rebuild per click is a queued job per click for nothing.
* **Review state is two new columns, not a new status.** ``match_status`` already carries
  meaning (``Created`` says the import made the record); overloading it to mean "a person
  looked" would erase that. ``reviewed_by``/``reviewed_on`` are stamped by every decision,
  and the queue's "needs decision" filter is simply "not stamped and not already a manual
  match". New columns on a normal doctype reach every existing row as NULL, which is the
  right starting value, so there is no backfill patch to get wrong.
* **The search filter is ``or_filters`` beside ``filters``, and Frappe ANDs the two.** So a
  search never widens the status filter; it narrows within it.
"""

from __future__ import annotations

import difflib
import re

import frappe
from frappe.utils import cint, now_datetime

from erpnext_enhancements.quickbooks_online.core.constants import (
	ENTITY_DOCTYPE_MAP,
	MASTER_ENTITIES,
	TRANSACTION_ENTITIES,
)
from erpnext_enhancements.quickbooks_online.core.mapping import (
	_display_name,
	_is_qbo_customer_job,
	find_existing_match,
	get_mapping,
	link_existing_record,
	preview_existing_matches,
)
from erpnext_enhancements.quickbooks_online.core.utils import json_loads

MAPPING_DOCTYPE = "QuickBooks Sync Mapping"
RAW_DOCTYPE = "QuickBooks Raw Payload"

#: The human-readable title field per ERPNext target. ``name`` is the docname, which has
#: drifted from the title on 125 of 1,662 production Customers, so both are scored.
NAME_FIELDS = {
	"Customer": "customer_name",
	"Supplier": "supplier_name",
	"Item": "item_name",
	"Account": "account_name",
	"Cost Center": "cost_center_name",
	"Payment Terms Template": "template_name",
	"Mode of Payment": "mode_of_payment",
	"Project": "project_name",
}

#: Targets an import-created duplicate may be folded into with ``rename_doc(merge=True)``.
#: Project is deliberately absent -- see the module docstring.
MERGEABLE_DOCTYPES = frozenset(
	{
		"Customer",
		"Supplier",
		"Item",
		"Account",
		"Cost Center",
		"Payment Terms Template",
		"Mode of Payment",
	}
)

#: What ERPNext's ``merge_account`` insists agree before it merges two Accounts.
ACCOUNT_MERGE_KEYS = ("is_group", "root_type", "company", "account_currency")

#: The status filter values the page offers. Anything else means "All".
STATUS_FILTERS = (
	"Needs decision",
	"Pending Review",
	"Conflict",
	"Auto Matched",
	"Created",
	"Manual Matched",
	"Unmapped",
)

#: The page offers 50/100/200/500 rows (``PAGE_LENGTHS`` in the page script, which a test
#: holds to this ceiling). A larger request is clamped, and the response's ``page_length``
#: says so -- the page pages by that, never by what it asked for, so a clamp can never make
#: it skip rows.
MAX_PAGE_LENGTH = 500
DEFAULT_PAGE_LENGTH = 50
SUGGESTION_LIMIT = 5
#: Below this a name-similarity candidate is noise rather than a suggestion.
SIMILARITY_FLOOR = 55
#: How many recent master payloads the Unmapped view scans (the old dialog scanned 100).
UNMAPPED_SCAN_LIMIT = 500

_LEGAL_SUFFIXES = frozenset(
	{"inc", "incorporated", "llc", "ltd", "limited", "co", "corp", "corporation", "company", "the"}
)
_NON_WORD = re.compile(r"[^a-z0-9]+")
_HTML_TAG = re.compile(r"<[^>]+>")


# ---------------------------------------------------------------------------
# Pure helpers (bench-free; covered by tests/test_quickbooks_matching.py)
# ---------------------------------------------------------------------------


def normalise_name(text) -> str:
	"""Lower-case, strip punctuation and drop legal suffixes, so ``ABC Supply Co.`` and
	``abc supply`` compare equal. Falls back to the punctuation-stripped words when the
	name is nothing *but* suffixes (a supplier called "The Company")."""
	words = _NON_WORD.sub(" ", str(text or "").lower()).split()
	kept = [word for word in words if word not in _LEGAL_SUFFIXES]
	return " ".join(kept or words)


def similarity(a, b) -> int:
	"""0-100 name similarity. 100 only for equality after normalisation; a token-subset
	relationship of at least two words ("ABC Supply" vs "ABC Supply Salt Lake") scores at
	least 85; everything else is ``difflib`` capped at 99."""
	left, right = normalise_name(a), normalise_name(b)
	if not left or not right:
		return 0
	if left == right:
		return 100
	ratio = difflib.SequenceMatcher(None, left, right).ratio()
	left_tokens, right_tokens = set(left.split()), set(right.split())
	if min(len(left_tokens), len(right_tokens)) >= 2 and (
		left_tokens <= right_tokens or right_tokens <= left_tokens
	):
		ratio = max(ratio, 0.85)
	return min(99, int(round(ratio * 100)))


def rank_candidates(
	qbo_name, rows, name_field, *, doctype=None, exclude=(), limit=SUGGESTION_LIMIT, floor=SIMILARITY_FLOOR
):
	"""Score ``rows`` (dicts with ``name`` and ``name_field``) against ``qbo_name``.

	The record currently linked (``exclude``) is never a suggestion for itself; ties break
	on name so the order is stable between refreshes.
	"""
	excluded = {value for value in exclude if value}
	scored = []
	for row in rows:
		name = row.get("name")
		if not name or name in excluded:
			continue
		title = row.get(name_field) or ""
		score = max(similarity(qbo_name, name), similarity(qbo_name, title))
		if score < floor:
			continue
		scored.append(
			{
				"doctype": row.get("doctype") or doctype,
				"name": name,
				"title": title or name,
				"score": score,
				"rule": "name similarity",
				"source": "similar",
			}
		)
	scored.sort(key=lambda candidate: (-candidate["score"], candidate["name"]))
	return scored[:limit]


def merge_plan(previous, chosen_doctype, chosen_name) -> dict:
	"""Decide whether re-pointing a link from ``previous`` to the chosen record should fold
	the previous record away. Only an import-created (``Created``) record of the same
	doctype qualifies, and Projects never do. The reason is returned either way so the page
	can say why nothing was merged."""
	previous = previous or {}
	prev_doctype = previous.get("doctype")
	prev_name = previous.get("name")
	prev_status = previous.get("match_status")
	if not prev_name:
		return {"merge": False, "reason": "nothing was linked before"}
	if prev_doctype == chosen_doctype and prev_name == chosen_name:
		return {"merge": False, "reason": "the link did not change"}
	if prev_status != "Created":
		return {
			"merge": False,
			"reason": f"the previous record was {prev_status or 'unlabelled'}, not one the import created",
		}
	if prev_doctype != chosen_doctype:
		return {"merge": False, "reason": f"{prev_doctype} cannot be folded into a {chosen_doctype}"}
	if prev_doctype not in MERGEABLE_DOCTYPES:
		return {
			"merge": False,
			"reason": (
				"Projects are merged with the Project Merge tool, which cancels rather than deletes"
				if prev_doctype == "Project"
				else f"{prev_doctype} records are not merged automatically"
			),
		}
	return {
		"merge": True,
		"doctype": prev_doctype,
		"from": prev_name,
		"into": chosen_name,
		"reason": "the previous record was created by the import",
	}


def account_merge_blockers(old_props, new_props) -> list[str]:
	"""The ``ACCOUNT_MERGE_KEYS`` on which two Accounts disagree (empty means mergeable)."""

	def _norm(key, value):
		return str(cint(value)) if key == "is_group" else str(value or "")

	old_props, new_props = old_props or {}, new_props or {}
	return [
		key for key in ACCOUNT_MERGE_KEYS if _norm(key, old_props.get(key)) != _norm(key, new_props.get(key))
	]


def parse_review_notes(owned_fields) -> dict:
	"""``owned_fields`` doubles as the parking note on a Pending Review row: ``issues`` from a
	preflight failure, ``candidates`` from an ambiguous match. Anything else is a snapshot of
	synced values and reads as neither."""
	data = json_loads(owned_fields, default={}) if owned_fields else {}
	if not isinstance(data, dict):
		data = {}
	issues = data.get("issues") or []
	candidates = data.get("candidates") or []
	return {
		"issues": [str(issue) for issue in issues if issue] if isinstance(issues, list) else [],
		"candidates": [
			candidate for candidate in candidates if isinstance(candidate, dict) and candidate.get("name")
		]
		if isinstance(candidates, list)
		else [],
	}


def expected_doctype(entity_type, payload) -> str | None:
	"""Where a QBO record belongs in ERPNext: the static map, except that a QBO job (a
	sub-customer) is a Project, not a Customer."""
	if entity_type == "Customer" and payload and _is_qbo_customer_job(payload):
		return "Project"
	return ENTITY_DOCTYPE_MAP.get(entity_type)


def describe_qbo(entity_type, payload) -> dict:
	"""The few facts about a QBO master record an accountant needs to recognise it."""
	payload = payload or {}
	name = _display_name(payload) if payload else ""
	detail = []
	leaf = payload.get("DisplayName")
	if leaf and leaf != name:
		detail.append(leaf)
	company = payload.get("CompanyName")
	if company and company != name:
		detail.append(company)
	email = (payload.get("PrimaryEmailAddr") or {}).get("Address")
	if email:
		detail.append(email)
	if payload.get("Sku"):
		detail.append(f"SKU {payload['Sku']}")
	if payload.get("AcctNum"):
		detail.append(f"No. {payload['AcctNum']}")
	if payload.get("AccountType"):
		detail.append(str(payload["AccountType"]))
	active = payload.get("Active")
	inactive = active is False or str(active).lower() == "false"
	return {
		"name": str(name or ""),
		"detail": detail,
		"inactive": inactive,
		"job": bool(entity_type == "Customer" and payload and _is_qbo_customer_job(payload)),
	}


def describe_transaction(payload) -> dict:
	"""Document number, date, total and party of a parked QBO transaction."""
	payload = payload or {}
	party = ""
	for key in ("CustomerRef", "VendorRef", "EntityRef"):
		ref = payload.get(key) or {}
		if isinstance(ref, dict) and ref.get("name"):
			party = str(ref["name"])
			break
	return {
		"doc_number": str(payload.get("DocNumber") or ""),
		"txn_date": str(payload.get("TxnDate") or ""),
		"total": payload.get("TotalAmt"),
		"party": party,
	}


def clean_error(exc) -> str:
	"""A ``frappe.throw`` message with its markup removed, for a table cell."""
	return _HTML_TAG.sub("", str(exc or "")).strip() or exc.__class__.__name__


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def latest_payloads(pairs) -> dict:
	"""``{(entity_type, qbo_id): payload}`` for the newest stored payload of each pair, one
	query per entity type. See the module docstring for why this is not a per-row read."""
	out: dict = {}
	by_type: dict = {}
	for entity_type, qbo_id in pairs:
		if entity_type and qbo_id:
			by_type.setdefault(entity_type, set()).add(str(qbo_id))
	for entity_type, ids in by_type.items():
		rows = frappe.db.sql(
			"""select raw.qbo_id, raw.payload
			   from `tabQuickBooks Raw Payload` raw
			   join (select qbo_id, max(creation) as newest
			           from `tabQuickBooks Raw Payload`
			          where qbo_entity_type = %(entity_type)s and qbo_id in %(ids)s
			          group by qbo_id) latest
			     on latest.qbo_id = raw.qbo_id and latest.newest = raw.creation
			  where raw.qbo_entity_type = %(entity_type)s""",
			{"entity_type": entity_type, "ids": tuple(sorted(ids))},
			as_dict=True,
		)
		for row in rows:
			key = (entity_type, str(row.qbo_id))
			if key not in out:
				out[key] = json_loads(row.payload, default={}) or {}
	return out


def _record_titles(refs) -> dict:
	"""``{(doctype, name): title}`` for the linked records on a page; ``None`` marks a link
	whose record no longer exists (a merge elsewhere, a hand delete)."""
	by_doctype: dict = {}
	for doctype, name in refs:
		if doctype and name:
			by_doctype.setdefault(doctype, set()).add(name)
	out: dict = {}
	for doctype, names in by_doctype.items():
		field = NAME_FIELDS.get(doctype)
		fields = ["name", field] if field else ["name"]
		try:
			rows = frappe.get_all(doctype, filters={"name": ["in", sorted(names)]}, fields=fields)
		except Exception:
			rows = []
		found = {row.name: ((row.get(field) if field else None) or row.name) for row in rows}
		for name in names:
			out[(doctype, name)] = found.get(name)
	return out


def similar_records(doctype, qbo_name, *, exclude=(), limit=SUGGESTION_LIMIT):
	"""Name-similar candidates of ``doctype``: a SQL ``like`` on the two longest words of
	the normalised name narrows the table, then ``rank_candidates`` scores what came back."""
	field = NAME_FIELDS.get(doctype)
	if not field or not qbo_name:
		return []
	tokens = sorted(
		(token for token in normalise_name(qbo_name).split() if len(token) >= 3), key=len, reverse=True
	)
	if not tokens:
		return []
	or_filters = []
	for token in tokens[:2]:
		or_filters.append([field, "like", f"%{token}%"])
		or_filters.append(["name", "like", f"%{token}%"])
	try:
		rows = frappe.get_all(doctype, fields=["name", field], or_filters=or_filters, limit_page_length=60)
	except Exception:
		return []
	return rank_candidates(
		qbo_name,
		[dict(row, doctype=doctype) for row in rows],
		field,
		doctype=doctype,
		exclude=exclude,
		limit=limit,
	)


def _suggestions(entity_type, payload, target, settings, *, current=None, stored=()):
	"""Candidate records for one row, best first, and the rule (if any) under which the
	sync's own matcher agrees with the *current* link."""
	found = []
	agrees = None
	match = None
	if payload:
		try:
			match = find_existing_match(entity_type, payload, settings)
		except Exception:
			match = None
	if match and match.get("status") == "matched":
		if match.get("name") == current:
			agrees = match.get("rule")
		else:
			found.append(
				{
					"doctype": target,
					"name": match.get("name"),
					"score": cint(match.get("confidence")) or 90,
					"rule": match.get("rule"),
					"source": "matcher",
				}
			)
	elif match and match.get("status") == "ambiguous":
		for candidate in match.get("candidates") or []:
			if candidate.get("name") and candidate["name"] != current:
				found.append(
					{
						"doctype": candidate.get("doctype") or target,
						"name": candidate["name"],
						"score": 50,
						"rule": match.get("reason"),
						"source": "matcher",
					}
				)
	seen = {candidate["name"] for candidate in found} | {current}
	for candidate in stored:
		if candidate.get("name") not in seen:
			found.append(
				{
					"doctype": candidate.get("doctype") or target,
					"name": candidate["name"],
					"score": 50,
					"rule": "stored candidate",
					"source": "stored",
				}
			)
			seen.add(candidate["name"])
	qbo_name = _display_name(payload) if payload else ""
	if target and qbo_name:
		found.extend(similar_records(target, qbo_name, exclude=seen))
	titles = _record_titles([(candidate["doctype"], candidate["name"]) for candidate in found])
	kept = []
	for candidate in found:
		title = titles.get((candidate["doctype"], candidate["name"]))
		if title is None:
			continue  # the matcher named a record that has since gone
		candidate["title"] = title
		kept.append(candidate)
	return kept[:SUGGESTION_LIMIT], agrees


def status_counts(entity_types) -> dict:
	"""Chip counts for the queue header, from one GROUP BY."""
	rows = frappe.db.sql(
		"""select match_status, conflict_status, (reviewed_on is null) as unreviewed, count(*) as n
		     from `tabQuickBooks Sync Mapping`
		    where qbo_entity_type in %(types)s and coalesce(deleted, 0) = 0
		    group by match_status, conflict_status, unreviewed""",
		{"types": tuple(entity_types)},
		as_dict=True,
	)
	counts = {
		"total": 0,
		"Needs decision": 0,
		"Pending Review": 0,
		"Conflict": 0,
		"Auto Matched": 0,
		"Created": 0,
		"Manual Matched": 0,
	}
	for row in rows:
		n = cint(row.n)
		status = row.match_status or ""
		counts["total"] += n
		if status in counts:
			counts[status] += n
		if row.conflict_status == "Conflict":
			counts["Conflict"] += n
		if cint(row.unreviewed) and status != "Manual Matched":
			counts["Needs decision"] += n
	return counts


def _page_args(start, page_length):
	return max(0, cint(start)), max(1, min(cint(page_length) or DEFAULT_PAGE_LENGTH, MAX_PAGE_LENGTH))


def _master_row(row, payload, titles, settings) -> dict:
	entity_type = row.qbo_entity_type
	qbo = describe_qbo(entity_type, payload)
	target = expected_doctype(entity_type, payload) or row.erpnext_doctype
	link = None
	if row.erpnext_name:
		title = titles.get((row.erpnext_doctype, row.erpnext_name))
		link = {
			"doctype": row.erpnext_doctype,
			"name": row.erpnext_name,
			"title": title or row.erpnext_name,
			"exists": title is not None,
		}
	notes = parse_review_notes(row.owned_fields)
	suggestions, agrees = _suggestions(
		entity_type, payload, target, settings, current=row.erpnext_name, stored=notes["candidates"]
	)
	return {
		"mapping": row.name,
		"entity_type": entity_type,
		"qbo_id": row.qbo_id,
		"qbo": qbo,
		"has_payload": bool(payload),
		"expected_doctype": target,
		"link": link,
		"match_status": row.match_status,
		"conflict_status": row.conflict_status,
		"match_rule": row.match_rule,
		"match_confidence": row.match_confidence,
		"agrees": agrees,
		"issues": notes["issues"],
		"suggestions": suggestions,
		"reviewed_by": row.reviewed_by,
		"reviewed_on": str(row.reviewed_on) if row.reviewed_on else None,
		"unmapped": False,
	}


def _master_where(entity_types, status, search):
	"""WHERE clause and bound parameters for the master queue, shared by the page query and
	its count so the two can never disagree. The clause text is assembled only from the
	literals in this function; every value the caller supplies is a bound parameter.

	Raw SQL rather than ``frappe.get_all`` because Frappe 16's query engine refuses a SQL
	function written as a string field (``"count(name) as total"`` raises *SQL functions
	are not allowed as strings in SELECT*), and the bench-free test stub's ``get_all``
	accepts anything -- so v1.474.0 shipped green and the page could not load. Found live,
	minutes after the deploy. ``status_counts`` and ``latest_payloads`` in this module were
	already written this way.
	"""
	clauses = ["qbo_entity_type in %(types)s", "coalesce(deleted, 0) = 0"]
	params = {"types": tuple(entity_types)}
	if status in ("Pending Review", "Auto Matched", "Created", "Manual Matched"):
		clauses.append("match_status = %(status)s")
		params["status"] = status
	elif status == "Conflict":
		clauses.append("conflict_status = 'Conflict'")
	elif status == "Needs decision":
		clauses.append("reviewed_on is null")
		# coalesce: a NULL match_status is "not a manual match" too (frappe's != would agree).
		clauses.append("coalesce(match_status, '') != 'Manual Matched'")
	term = str(search or "").strip()
	if term:
		# The search narrows within the status filter (ANDed), never widens it.
		clauses.append("(erpnext_name like %(term)s or qbo_id like %(term)s)")
		params["term"] = "%" + term + "%"
	return " and ".join(clauses), params


def master_queue(
	entity_types=None, status=None, search=None, start=0, page_length=DEFAULT_PAGE_LENGTH
) -> dict:
	"""One page of the master-record review queue, with header counts."""
	entity_types = [entity for entity in (entity_types or []) if entity in MASTER_ENTITIES] or list(
		MASTER_ENTITIES
	)
	start, page_length = _page_args(start, page_length)
	if status == "Unmapped":
		return _unmapped_queue(entity_types, start, page_length)

	where, params = _master_where(entity_types, status, search)
	# The clause text is assembled from literals in _master_where; every value is bound.
	rows = frappe.db.sql(
		"select name, qbo_entity_type, qbo_id, erpnext_doctype, erpnext_name, match_status,"
		" conflict_status, match_rule, match_confidence, owned_fields, reviewed_by, reviewed_on"
		" from `tabQuickBooks Sync Mapping` where "
		+ where
		+ " order by qbo_entity_type asc, erpnext_name asc, qbo_id asc"
		+ " limit %(start)s, %(page_length)s",
		dict(params, start=start, page_length=page_length),
		as_dict=True,
	)
	total = cint(
		frappe.db.sql("select count(*) from `tabQuickBooks Sync Mapping` where " + where, params)[0][0]
	)

	settings = frappe.get_single("QuickBooks Online Settings")
	payloads = latest_payloads([(row.qbo_entity_type, row.qbo_id) for row in rows])
	titles = _record_titles([(row.erpnext_doctype, row.erpnext_name) for row in rows])
	items = [
		_master_row(row, payloads.get((row.qbo_entity_type, str(row.qbo_id))) or {}, titles, settings)
		for row in rows
	]
	return {
		"rows": items,
		"total": total,
		"start": start,
		"page_length": page_length,
		"counts": status_counts(entity_types),
	}


def _unmapped_queue(entity_types, start, page_length) -> dict:
	"""QBO master records with a stored payload and no mapping row at all -- what the old
	dialog showed. Empty after a full import; populated by a Preview Resync of new records."""
	settings = frappe.get_single("QuickBooks Online Settings")
	found = preview_existing_matches(entity_types=entity_types, limit=UNMAPPED_SCAN_LIMIT)
	items = []
	for match in found[start : start + page_length]:
		target = match.get("erpnext_doctype")
		suggestions, _agrees = _suggestions(
			match["entity_type"],
			None,
			target,
			settings,
			current=None,
			stored=(match.get("match") or {}).get("candidates") or [],
		)
		hit = match.get("match") or {}
		if hit.get("status") == "matched" and hit.get("name"):
			titles = _record_titles([(target, hit["name"])])
			if titles.get((target, hit["name"])) is not None:
				suggestions.insert(
					0,
					{
						"doctype": target,
						"name": hit["name"],
						"title": titles[(target, hit["name"])],
						"score": cint(hit.get("confidence")) or 90,
						"rule": hit.get("rule"),
						"source": "matcher",
					},
				)
		if target and match.get("qbo_name"):
			seen = {candidate["name"] for candidate in suggestions}
			suggestions.extend(similar_records(target, match["qbo_name"], exclude=seen))
		items.append(
			{
				"mapping": None,
				"entity_type": match["entity_type"],
				"qbo_id": match["qbo_id"],
				"qbo": {
					"name": str(match.get("qbo_name") or ""),
					"detail": [],
					"inactive": False,
					"job": False,
				},
				"has_payload": True,
				"expected_doctype": target,
				"link": None,
				"match_status": "Not Matched",
				"conflict_status": None,
				"match_rule": None,
				"match_confidence": None,
				"agrees": None,
				"issues": [],
				"suggestions": suggestions[:SUGGESTION_LIMIT],
				"reviewed_by": None,
				"reviewed_on": None,
				"unmapped": True,
			}
		)
	return {
		"rows": items,
		"total": len(found),
		"start": start,
		"page_length": page_length,
		"counts": status_counts(entity_types),
	}


def parked_transactions(entity_types=None, start=0, page_length=DEFAULT_PAGE_LENGTH) -> dict:
	"""Transactions parked in Pending Review (a preflight or save failure), with the stored
	reason and whatever draft exists, so the accountant can fix the cause and retry."""
	entity_types = [entity for entity in (entity_types or []) if entity in TRANSACTION_ENTITIES] or list(
		TRANSACTION_ENTITIES
	)
	start, page_length = _page_args(start, page_length)
	filters = {"qbo_entity_type": ["in", entity_types], "match_status": "Pending Review", "deleted": 0}
	rows = frappe.get_all(
		MAPPING_DOCTYPE,
		filters=filters,
		fields=[
			"name",
			"qbo_entity_type",
			"qbo_id",
			"erpnext_doctype",
			"erpnext_name",
			"match_rule",
			"owned_fields",
			"last_synced_at",
		],
		order_by="qbo_entity_type asc, last_synced_at desc, qbo_id asc",
		limit_start=start,
		limit_page_length=page_length,
	)
	total = frappe.db.count(MAPPING_DOCTYPE, filters)
	# Raw SQL for the same reason as _master_where: a string aggregate in get_all's fields
	# is refused by Frappe 16's query engine.
	per_type = frappe.db.sql(
		"select qbo_entity_type, count(*) as n from `tabQuickBooks Sync Mapping`"
		" where qbo_entity_type in %(types)s and match_status = 'Pending Review'"
		" and coalesce(deleted, 0) = 0 group by qbo_entity_type",
		{"types": tuple(TRANSACTION_ENTITIES)},
		as_dict=True,
	)
	payloads = latest_payloads([(row.qbo_entity_type, row.qbo_id) for row in rows])
	items = []
	for row in rows:
		exists = bool(
			row.erpnext_doctype
			and row.erpnext_name
			and frappe.db.exists(row.erpnext_doctype, row.erpnext_name)
		)
		items.append(
			{
				"mapping": row.name,
				"entity_type": row.qbo_entity_type,
				"qbo_id": row.qbo_id,
				"doctype": row.erpnext_doctype,
				"name": row.erpnext_name,
				"exists": exists,
				"qbo": describe_transaction(payloads.get((row.qbo_entity_type, str(row.qbo_id)))),
				"issues": parse_review_notes(row.owned_fields)["issues"],
				"match_rule": row.match_rule,
				"last_synced_at": str(row.last_synced_at) if row.last_synced_at else None,
			}
		)
	return {
		"rows": items,
		"total": total,
		"start": start,
		"page_length": page_length,
		"counts": {row.qbo_entity_type: cint(row.n) for row in per_type},
	}


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _stamp_reviewed(mapping_name, user):
	frappe.db.set_value(
		MAPPING_DOCTYPE,
		mapping_name,
		{"reviewed_by": user, "reviewed_on": now_datetime()},
		update_modified=False,
	)


def _merge_records(doctype, old, new) -> dict:
	"""Fold ``old`` into ``new``. Returns a status dict rather than raising, so a refusal
	never undoes the link decision committed just before it."""
	frappe.db.savepoint("qbo_match_merge")
	try:
		if doctype == "Account":
			old_props = frappe.db.get_value("Account", old, list(ACCOUNT_MERGE_KEYS), as_dict=True) or {}
			new_props = frappe.db.get_value("Account", new, list(ACCOUNT_MERGE_KEYS), as_dict=True) or {}
			blockers = account_merge_blockers(old_props, new_props)
			if blockers:
				frappe.throw(
					f"Accounts differ on {', '.join(blockers)}; ERPNext will not merge them. Merge by hand or leave both."
				)
		# The model-level function, not the ``frappe.rename_doc`` alias: on Frappe 16.30 the
		# alias does not take ``ignore_permissions`` (a keyword that wedged every production
		# migration on 2026-08-07 -- see tests/test_uom_cleanup.py), and the operator gate is
		# the permission boundary here, not the accountant's write access to Supplier or Item.
		from frappe.model.rename_doc import rename_doc as model_rename_doc

		model_rename_doc(
			doctype,
			old,
			new,
			merge=True,
			force=True,
			ignore_permissions=True,
			show_alert=False,
			rebuild_search=False,
		)
		frappe.db.commit()
		return {"status": "merged", "doctype": doctype, "from": old, "into": new}
	except Exception as exc:
		try:
			frappe.db.rollback(save_point="qbo_match_merge")
		except Exception:
			frappe.db.rollback()
		return {"status": "failed", "doctype": doctype, "from": old, "into": new, "error": clean_error(exc)}


def decide(entity_type, qbo_id, erpnext_name, *, fill_blanks=False, merge_duplicate=True, user=None) -> dict:
	"""Link a QBO record to the chosen ERPNext record, stamp the review, and -- when the
	import had created its own copy -- fold that copy into the chosen record."""
	user = user or frappe.session.user
	erpnext_name = (erpnext_name or "").strip()
	if not erpnext_name:
		frappe.throw("Choose the ERPNext record to link to.")
	existing = get_mapping(entity_type, qbo_id)
	previous = (
		{
			"doctype": existing.erpnext_doctype,
			"name": existing.erpnext_name,
			"match_status": existing.match_status,
		}
		if existing
		else {}
	)
	payload = latest_payloads([(entity_type, qbo_id)]).get((entity_type, str(qbo_id))) or {}
	if not payload:
		frappe.throw(
			"No QuickBooks payload is stored for this record. Sync it (or run Preview Resync) first."
		)
	target = expected_doctype(entity_type, payload)
	if not target:
		frappe.throw(f"{entity_type} has no ERPNext destination.")

	mapping_name = link_existing_record(
		entity_type, qbo_id, target, erpnext_name, apply_qbo_data=bool(fill_blanks)
	)
	_stamp_reviewed(mapping_name, user)
	frappe.db.commit()

	plan = merge_plan(previous, target, erpnext_name)
	merge = {"status": "skipped", "reason": plan["reason"]}
	if merge_duplicate and plan["merge"]:
		if frappe.db.exists(plan["doctype"], plan["from"]):
			merge = _merge_records(plan["doctype"], plan["from"], plan["into"])
		else:
			merge = {"status": "skipped", "reason": f"{plan['doctype']} {plan['from']} no longer exists"}
	elif plan["merge"]:
		merge = {"status": "skipped", "reason": "merge not requested", "duplicate": plan["from"]}
	return {
		"mapping": mapping_name,
		"linked": {"doctype": target, "name": erpnext_name},
		"merge": merge,
		"reviewed_by": user,
	}


def decide_many(decisions, *, fill_blanks=False, merge_duplicate=True, user=None) -> list:
	"""``decide`` for each of ``decisions`` (dicts of entity_type/qbo_id/erpnext_name), one
	failure never stopping the rest and never leaving its own half-applied work behind.

	A decision may carry its own ``fill_blanks``, which wins over the call-level flag: the
	page's *Link selected* sends each ticked row's own "Fill blank fields" box, so one row
	asking for the QBO data does not impose it on the rest."""
	results = []
	for decision in decisions or []:
		decision = decision or {}
		entity_type = decision.get("entity_type")
		qbo_id = decision.get("qbo_id")
		erpnext_name = decision.get("erpnext_name")
		own_fill = decision.get("fill_blanks")
		try:
			frappe.db.savepoint("qbo_match_decide")
			result = decide(
				entity_type,
				qbo_id,
				erpnext_name,
				fill_blanks=fill_blanks if own_fill is None else bool(cint(own_fill)),
				merge_duplicate=merge_duplicate,
				user=user,
			)
			results.append(dict(result, entity_type=entity_type, qbo_id=qbo_id, ok=True))
		except Exception as exc:
			try:
				frappe.db.rollback(save_point="qbo_match_decide")
			except Exception:
				frappe.db.rollback()
			results.append(
				{"entity_type": entity_type, "qbo_id": qbo_id, "ok": False, "error": clean_error(exc)}
			)
	return results


def confirm(entity_type, qbo_id, *, user=None) -> dict:
	"""Record that a person looked at the row and left the link as it is. Changes no
	status: a Pending Review row stays pending until its cause is fixed and it is retried."""
	user = user or frappe.session.user
	mapping = get_mapping(entity_type, qbo_id)
	if not mapping:
		frappe.throw(f"No mapping exists for {entity_type} {qbo_id}.")
	_stamp_reviewed(mapping.name, user)
	frappe.db.commit()
	return {"mapping": mapping.name, "reviewed_by": user}


def confirm_many(pairs, *, user=None) -> list:
	"""``confirm`` for each of ``pairs`` (dicts of entity_type/qbo_id) -- the page's *Keep
	selected* -- one result per pair in order. A row with no mapping (an *Unmapped payloads*
	row has nothing to keep) is reported and never stops the rest."""
	results = []
	for pair in pairs or []:
		pair = pair or {}
		entity_type = pair.get("entity_type")
		qbo_id = pair.get("qbo_id")
		try:
			if not entity_type or not qbo_id:
				frappe.throw("Each row needs an entity_type and a qbo_id.")
			result = confirm(entity_type, qbo_id, user=user)
			results.append(dict(result, entity_type=entity_type, qbo_id=qbo_id, ok=True))
		except Exception as exc:
			results.append(
				{"entity_type": entity_type, "qbo_id": qbo_id, "ok": False, "error": clean_error(exc)}
			)
	return results
