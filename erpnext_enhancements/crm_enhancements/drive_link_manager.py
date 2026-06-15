"""Drive Link Manager — System-Manager dashboard backend for bulk-linking
existing Google Drive folders to ERPNext records (Project / Customer /
Opportunity) before the two-way sync takes over.

Workflow (all whitelisted, System-Manager only):

1. :func:`scan_drive_links` — list the whole Shared Drive's folders once, then
   fuzzy-rank candidate folders for every *unlinked* record (see
   :mod:`erpnext_enhancements.crm_enhancements.drive_match`). Results land as
   **Drive Link Candidate** rows; ``High``-tier matches are pre-approved with
   their suggested folder, everything else waits as ``Pending``.
2. :func:`get_candidates` / :func:`set_decision` / :func:`search_folders` — the
   dashboard reads the staged rows, the reviewer approves / rejects / overrides
   the folder / asks for a brand-new folder.
3. :func:`apply_links` — writes ``custom_drive_folder_id`` (or provisions a fresh
   folder for "Create New" rows) **one row at a time in its own try/except**, so
   one failure never stops the rest. Every outcome is written to Drive Sync Log.

Matching is hierarchy-aware: customers match Shared-Drive-root folders; projects
and opportunities are scored against the children of their customer's folder
first, widening to the whole drive only when that yields no confident match.
The actual folder writes reuse the existing, retry-hardened provisioning in
:mod:`erpnext_enhancements.crm_enhancements.drive_utils`.
"""

import json
from collections import defaultdict

import frappe
from frappe.utils import cint

from erpnext_enhancements.crm_enhancements import drive_match
from erpnext_enhancements.crm_enhancements.drive_sync import SYNCED_DOCTYPES, log_sync
from erpnext_enhancements.crm_enhancements.drive_utils import (
	get_drive_service,
	provision_customer_folder,
	provision_opportunity_folder,
	provision_project_folders,
)

CANDIDATE = "Drive Link Candidate"
FOLDER_MIME = "application/vnd.google-apps.folder"


def _require_admin():
	frappe.only_for("System Manager")


# --------------------------------------------------------------- Drive listing


def _list_all_folders(service, drive_id):
	"""Every non-trashed folder in the Shared Drive, with parents — one flat,
	paginated listing (far cheaper than walking the tree) that the matcher then
	indexes in memory."""
	folders, page_token = [], None
	kwargs = {
		"q": f"mimeType='{FOLDER_MIME}' and trashed=false",
		"fields": "nextPageToken, files(id, name, parents, webViewLink)",
		"pageSize": 1000,
		"supportsAllDrives": True,
		"includeItemsFromAllDrives": True,
		"corpora": "drive",
		"driveId": drive_id,
	}
	while True:
		if page_token:
			kwargs["pageToken"] = page_token
		result = service.files().list(**kwargs).execute()
		folders.extend(result.get("files", []))
		page_token = result.get("nextPageToken")
		if not page_token:
			return folders


def _path_of(folder_id, by_id, drive_id):
	"""Human-readable "Customer/Project/Sub" path for a folder, built from the
	parents chain. Iterative + cycle-guarded (Drive shortcuts can loop)."""
	names, cur, seen = [], folder_id, set()
	while cur and cur in by_id and cur not in seen and cur != drive_id:
		seen.add(cur)
		folder = by_id[cur]
		names.append(folder.get("name", ""))
		parents = folder.get("parents") or []
		cur = parents[0] if parents else None
	return "/".join(reversed(names))


def _index_folders(folders, drive_id):
	"""Return ``(by_id, root_folders, nested_folders, children_index)`` and stamp
	a ``path`` on every folder dict. Root folders are the Shared Drive's direct
	children (the customer folders); everything else is nested."""
	by_id = {f["id"]: f for f in folders}
	children_index = defaultdict(list)
	for folder in folders:
		for parent in folder.get("parents") or []:
			children_index[parent].append(folder)

	root_folders, nested_folders = [], []
	for folder in folders:
		folder["path"] = _path_of(folder["id"], by_id, drive_id)
		parents = folder.get("parents") or []
		parent = parents[0] if parents else None
		if not parent or parent == drive_id or parent not in by_id:
			root_folders.append(folder)
		else:
			nested_folders.append(folder)
	return by_id, root_folders, nested_folders, children_index


def _merge_ranked(*ranked_lists, limit=3):
	"""Merge several ``best_matches`` results, de-duping by folder id and keeping
	the highest score per folder, best first."""
	best_by_id = {}
	for ranked in ranked_lists:
		for row in ranked:
			fid = row["folder"].get("id")
			if fid and (fid not in best_by_id or row["score"] > best_by_id[fid]["score"]):
				best_by_id[fid] = row
	merged = sorted(best_by_id.values(), key=lambda r: r["score"], reverse=True)
	return merged[:limit]


# --------------------------------------------------------------- candidate rows


def _make_candidate(doctype, name, label, context, ranked):
	"""Insert one Drive Link Candidate from a ranked match list. High-tier
	matches default to Approve (pre-selecting the suggested folder); everything
	else stays Pending for the reviewer."""
	top = ranked[0] if ranked else None
	score = top["score"] if top else 0.0
	tier = drive_match.tier_for_score(score)
	suggested = top["folder"] if (top and tier != "None") else None

	alternatives = [
		{
			"id": row["folder"].get("id"),
			"name": row["folder"].get("name"),
			"label": row["folder"].get("path") or row["folder"].get("name"),
			"score": row["score"],
		}
		for row in ranked
		if row["score"] >= drive_match.TIER_LOW
	]

	approve = tier == "High" and suggested is not None
	doc = frappe.get_doc({
		"doctype": CANDIDATE,
		"reference_doctype": doctype,
		"reference_name": name,
		"record_label": label,
		"context": context,
		"match_tier": tier,
		"score": score,
		"suggested_folder_id": suggested.get("id") if suggested else None,
		"suggested_folder_label": (suggested.get("path") or suggested.get("name")) if suggested else None,
		"alternatives": json.dumps(alternatives),
		"decision": "Approve" if approve else "Pending",
		"chosen_folder_id": suggested.get("id") if approve else None,
		"chosen_folder_label": (suggested.get("path") or suggested.get("name")) if approve else None,
		"status": "Suggested",
	})
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)


def _customer_label_map():
	"""``{customer_name: display_label}`` and ``{customer_name: folder_id}`` for
	every customer, so projects/opportunities can resolve their parent folder."""
	labels, folders = {}, {}
	for cust in frappe.get_all(
		"Customer", fields=["name", "customer_name", "custom_drive_folder_id"]
	):
		labels[cust.name] = cust.customer_name or cust.name
		if cust.custom_drive_folder_id:
			folders[cust.name] = cust.custom_drive_folder_id
	return labels, folders


def _resolve_customer_folder(party, cust_labels, cust_folder_ids, by_id, root_folders):
	"""Best guess at a customer's Drive folder: the already-linked id if present,
	else a confident fuzzy match among the Shared Drive's root folders."""
	if not party:
		return None
	linked = cust_folder_ids.get(party)
	if linked and linked in by_id:
		return by_id[linked]
	label = cust_labels.get(party, party)
	ranked = drive_match.best_matches([label, party], root_folders, limit=1)
	if ranked and ranked[0]["score"] >= drive_match.TIER_MEDIUM:
		return ranked[0]["folder"]
	return None


@frappe.whitelist()
def scan_drive_links():
	"""Rebuild the Drive Link Candidate set from the current Shared Drive folders
	and unlinked records. Clears prior non-``Linked`` rows first so it is safe to
	re-run. Per-record failures are logged and skipped — the scan always finishes
	what it can."""
	_require_admin()
	service, drive_id = get_drive_service()
	if not drive_id:
		frappe.throw("Shared Drive ID not configured in Project Folder Google Drive Settings")

	folders = _list_all_folders(service, drive_id)
	by_id, root_folders, nested_folders, children_index = _index_folders(folders, drive_id)
	cust_labels, cust_folder_ids = _customer_label_map()

	# Safe to re-run: drop everything not already applied, keep Linked for audit.
	frappe.db.delete(CANDIDATE, {"status": ["!=", "Linked"]})

	counts = {"Customer": 0, "Project": 0, "Opportunity": 0}
	skipped = 0

	def scoped_pool(party):
		folder = _resolve_customer_folder(party, cust_labels, cust_folder_ids, by_id, root_folders)
		return children_index.get(folder["id"], []) if folder else []

	# Customers → root-level folders.
	for cust in _unlinked("Customer", ["name", "customer_name"]):
		try:
			label = cust.customer_name or cust.name
			ranked = drive_match.best_matches([label, cust.name], root_folders)
			_make_candidate("Customer", cust.name, label, None, ranked)
			counts["Customer"] += 1
		except Exception:
			skipped += 1
			frappe.log_error(frappe.get_traceback(), "Drive Link Scan (Customer)")

	# Projects → children of the customer folder, widening to the whole drive.
	for proj in _unlinked("Project", ["name", "project_name", "customer"]):
		try:
			label = f"{proj.name} {proj.project_name}".strip()
			aliases = [label, proj.project_name, proj.name]
			ranked = drive_match.best_matches(aliases, scoped_pool(proj.customer))
			if not ranked or ranked[0]["score"] < drive_match.TIER_MEDIUM:
				ranked = _merge_ranked(ranked, drive_match.best_matches(aliases, nested_folders))
			_make_candidate("Project", proj.name, label, cust_labels.get(proj.customer), ranked)
			counts["Project"] += 1
		except Exception:
			skipped += 1
			frappe.log_error(frappe.get_traceback(), "Drive Link Scan (Project)")

	# Opportunities → same hierarchy via party_name (Customer-party only).
	opp_filters = {"opportunity_from": "Customer", "party_name": ["is", "set"]}
	for opp in _unlinked("Opportunity", ["name", "title", "party_name"], opp_filters):
		try:
			label = f"{opp.name} {opp.title}".strip() if opp.title else opp.name
			aliases = [label, opp.title, opp.name]
			ranked = drive_match.best_matches(aliases, scoped_pool(opp.party_name))
			if not ranked or ranked[0]["score"] < drive_match.TIER_MEDIUM:
				ranked = _merge_ranked(ranked, drive_match.best_matches(aliases, nested_folders))
			_make_candidate("Opportunity", opp.name, label, cust_labels.get(opp.party_name), ranked)
			counts["Opportunity"] += 1
		except Exception:
			skipped += 1
			frappe.log_error(frappe.get_traceback(), "Drive Link Scan (Opportunity)")

	frappe.db.commit()
	return {
		"folders_scanned": len(folders),
		"counts": counts,
		"total": sum(counts.values()),
		"skipped": skipped,
		"generated_at": frappe.utils.now(),
	}


def _unlinked(doctype, fields, extra_filters=None):
	"""Records of ``doctype`` with no ``custom_drive_folder_id`` yet (empty list
	if the column doesn't exist on this site)."""
	field = SYNCED_DOCTYPES.get(doctype)
	if not field or not frappe.db.has_column(doctype, field):
		return []
	filters = {field: ["is", "not set"]}
	if extra_filters:
		filters.update(extra_filters)
	return frappe.get_all(doctype, filters=filters, fields=fields, limit_page_length=0)


# --------------------------------------------------------------- dashboard read


@frappe.whitelist()
def get_candidates():
	"""All staged candidates for the dashboard, plus a summary and conflict flags
	(the same folder chosen by more than one still-pending Approve row)."""
	_require_admin()
	rows = frappe.get_all(
		CANDIDATE,
		fields=[
			"name", "reference_doctype", "reference_name", "record_label", "context",
			"match_tier", "score", "suggested_folder_id", "suggested_folder_label",
			"alternatives", "decision", "chosen_folder_id", "chosen_folder_label",
			"status", "error",
		],
		order_by="field(reference_doctype, 'Customer', 'Project', 'Opportunity'), score desc",
		limit_page_length=0,
	)

	# Conflict = a folder targeted by >1 approved row that isn't linked yet.
	folder_users = defaultdict(list)
	for row in rows:
		if row.decision == "Approve" and row.chosen_folder_id and row.status != "Linked":
			folder_users[row.chosen_folder_id].append(row.reference_name)
	conflicts = {fid for fid, users in folder_users.items() if len(users) > 1}

	summary = {"total": len(rows), "by_status": defaultdict(int), "by_tier": defaultdict(int), "approved": 0}
	for row in rows:
		row["alternatives"] = _safe_json(row.get("alternatives"))
		row["conflict"] = bool(row.get("chosen_folder_id") in conflicts)
		summary["by_status"][row.status or "Suggested"] += 1
		summary["by_tier"][row.match_tier or "None"] += 1
		if row.decision in ("Approve", "Create New") and row.status != "Linked":
			summary["approved"] += 1

	return {"candidates": rows, "summary": summary, "conflicts": len(conflicts)}


def _safe_json(text):
	try:
		return json.loads(text) if text else []
	except (ValueError, TypeError):
		return []


@frappe.whitelist()
def set_decision(name, decision, chosen_folder_id=None, chosen_folder_label=None):
	"""Persist one row's review decision (and chosen folder) from the dashboard."""
	_require_admin()
	if decision not in ("Pending", "Approve", "Reject", "Create New"):
		frappe.throw("Invalid decision")
	doc = frappe.get_doc(CANDIDATE, name)
	doc.decision = decision
	if decision == "Approve":
		# Fall back to the suggestion when the UI didn't send an explicit folder.
		doc.chosen_folder_id = chosen_folder_id or doc.suggested_folder_id
		doc.chosen_folder_label = chosen_folder_label or doc.suggested_folder_label
	elif decision == "Create New":
		doc.chosen_folder_id = None
		doc.chosen_folder_label = "↳ create new folder"
	else:
		doc.chosen_folder_id = None
		doc.chosen_folder_label = None
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)
	return {"ok": True}


@frappe.whitelist()
def bulk_decision(names, decision):
	"""Apply one decision to many candidates at once (the dashboard's
	"Approve all suggested" / "Reject all" actions). ``names`` is a JSON list.
	Approve only takes rows that actually have a suggested folder; rows are
	processed independently so one bad id can't fail the batch."""
	_require_admin()
	if decision not in ("Pending", "Approve", "Reject", "Create New"):
		frappe.throw("Invalid decision")
	names = json.loads(names) if isinstance(names, str) else (names or [])
	changed = 0
	for name in names:
		try:
			row = frappe.db.get_value(
				CANDIDATE, name, ["suggested_folder_id", "suggested_folder_label"], as_dict=True
			)
			if not row:
				continue
			if decision == "Approve" and not row.suggested_folder_id:
				continue  # nothing to approve — leave it for manual review
			values = {"decision": decision}
			if decision == "Approve":
				values["chosen_folder_id"] = row.suggested_folder_id
				values["chosen_folder_label"] = row.suggested_folder_label
			elif decision == "Create New":
				values["chosen_folder_id"] = None
				values["chosen_folder_label"] = "↳ create new folder"
			else:
				values["chosen_folder_id"] = None
				values["chosen_folder_label"] = None
			frappe.db.set_value(CANDIDATE, name, values, update_modified=False)
			changed += 1
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Drive Link Bulk Decision")
	frappe.db.commit()
	return {"changed": changed}


@frappe.whitelist()
def search_folders(query, limit=20):
	"""Live Drive folder name-search for manual override in the dashboard."""
	_require_admin()
	query = (query or "").strip()
	if not query:
		return []
	service, drive_id = get_drive_service()
	escaped = query.replace("\\", "\\\\").replace("'", "\\'")
	result = service.files().list(
		q=f"mimeType='{FOLDER_MIME}' and trashed=false and name contains '{escaped}'",
		fields="files(id, name, webViewLink)",
		pageSize=cint(limit) or 20,
		supportsAllDrives=True,
		includeItemsFromAllDrives=True,
		corpora="drive",
		driveId=drive_id,
	).execute()
	return [
		{"id": f["id"], "name": f.get("name"), "label": f.get("name"), "link": f.get("webViewLink")}
		for f in result.get("files", [])
	]


# --------------------------------------------------------------- apply (robust)


@frappe.whitelist()
def apply_links():
	"""Link every Approve / Create New candidate that isn't already linked —
	**one row at a time, each in its own try/except**, so a single failure is
	recorded and the rest still proceed. Returns a per-outcome summary."""
	_require_admin()
	rows = frappe.get_all(
		CANDIDATE,
		filters={"decision": ["in", ["Approve", "Create New"]], "status": ["!=", "Linked"]},
		fields=["name", "reference_doctype", "reference_name", "record_label",
				"decision", "chosen_folder_id", "suggested_folder_id"],
		limit_page_length=0,
	)
	results = {"linked": 0, "created": 0, "failed": 0, "skipped": 0}
	claimed = {}  # folder_id -> reference_name, so we never link one folder twice

	for row in rows:
		try:
			if row.decision == "Create New":
				folder_id = _provision_for(row.reference_doctype, row.reference_name)
				if not folder_id:
					_mark(row.name, "Failed", "Provisioning returned no folder id")
					results["failed"] += 1
					continue
				_mark(row.name, "Linked")
				results["created"] += 1
				continue

			folder_id = row.chosen_folder_id or row.suggested_folder_id
			if not folder_id:
				_mark(row.name, "Skipped", "No folder chosen")
				results["skipped"] += 1
				continue
			if folder_id in claimed:
				_mark(row.name, "Skipped", f"Folder already linked to {claimed[folder_id]} in this run")
				results["skipped"] += 1
				continue

			_set_record_folder(row.reference_doctype, row.reference_name, folder_id)
			claimed[folder_id] = row.reference_name
			log_sync(
				"Backfill", "Success",
				reference_doctype=row.reference_doctype, reference_name=row.reference_name,
				file_name=row.record_label, drive_file_id=folder_id,
			)
			_mark(row.name, "Linked")
			results["linked"] += 1
		except Exception:
			tb = frappe.get_traceback()
			frappe.log_error(tb, "Drive Link Apply")
			_mark(row.name, "Failed", tb)
			log_sync(
				"Backfill", "Failed",
				reference_doctype=row.reference_doctype, reference_name=row.reference_name,
				file_name=row.record_label, error=tb,
			)
			results["failed"] += 1

	frappe.db.commit()
	return results


def _mark(name, status, error=None):
	frappe.db.set_value(
		CANDIDATE, name, {"status": status, "error": (error or "")[:1000] or None},
		update_modified=False,
	)


def _set_record_folder(doctype, name, folder_id):
	field = SYNCED_DOCTYPES.get(doctype)
	if not field or not frappe.db.has_column(doctype, field):
		frappe.throw(f"{doctype} has no Drive folder field")
	if not frappe.db.exists(doctype, name):
		frappe.throw(f"{doctype} {name} no longer exists")
	frappe.db.set_value(doctype, name, field, folder_id, update_modified=False)


def _provision_for(doctype, name):
	"""Create a fresh Drive folder for an unmatched record, reusing the existing
	retry-hardened provisioning, and return the new folder id."""
	field = SYNCED_DOCTYPES.get(doctype)
	if doctype == "Project":
		proj = frappe.db.get_value(
			"Project", name, ["project_name", "customer", "project_type"], as_dict=True
		)
		folder_name = f"{name} {proj.project_name}".strip()
		party = proj.customer or "Unknown Customer"
		folder_id, _link = provision_project_folders(
			folder_name, party, project_type=proj.get("project_type")
		)
		if folder_id:
			frappe.db.set_value("Project", name, field, folder_id, update_modified=False)
			log_sync(
				"Provision Folder", "Success",
				reference_doctype="Project", reference_name=name,
				file_name=folder_name, drive_file_id=folder_id,
			)
		return folder_id

	if doctype == "Customer":
		provision_customer_folder(name)
	elif doctype == "Opportunity":
		provision_opportunity_folder(name)
	else:
		frappe.throw(f"Cannot provision a folder for {doctype}")
	# These provisioners set the field + log themselves; read the id back.
	return frappe.db.get_value(doctype, name, field)
