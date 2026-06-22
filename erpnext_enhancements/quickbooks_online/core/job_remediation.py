"""One-off remediation: consolidate QBO sub-customer "jobs" that were imported as
flat colon-named Customers into the parent Customer + an ERPNext Project.

Background
----------
Before the job->Project mapping fix (``core/mapping.py``), the QBO importer mapped
every QBO sub-customer / job (``Job``/``IsProject``/``ParentRef``/``Level`` > 0) to a
flat ERPNext Customer named with QBO's ``FullyQualifiedName`` -- the colon path
``Parent:Job`` (e.g. ``4th West Apartments:PRJ-401 4th West Fountain Control & Pump
Repair``) -- and the Customer ``after_insert`` Drive hook then created a malformed
**top-level** Drive folder for each. This module cleans up the records that left
behind, modelling each job the way the forward fix now does on import: as a Project
under the top-level parent Customer.

Per job it: links the job to the existing ERPNext Project by its ``PRJ-###`` number
(else creates one under the parent); tags the job's Sales Invoices with that Project;
merges the job-Customer into its top-level parent (``frappe.rename_doc(merge=True)``,
which moves invoices/payments/quotations/addresses/etc.); repoints the QBO Sync
Mapping to the Project; and cleans up the orphan Drive folder (trash if empty, else
relocate under the parent customer folder).

Safety
------
* **Dry-run by default** (``apply=False`` writes nothing; it reports what it would do).
* **Idempotent** -- re-runnable; already-consolidated jobs are skipped, every write
  is guarded "skip if already done".
* **Batched + committed** so a mid-run failure keeps completed work.
* **Per-record guarded** -- one bad job logs an Error and is skipped, never aborting.
* **Reversible-leaning** -- Drive folders are *trashed*, never hard-deleted.
* **Not** wired to migrate/scheduler. Run it manually, **sandbox first**.

Run it::

    # 1) preview (no writes):
    bench --site <site> execute \\
      erpnext_enhancements.quickbooks_online.core.job_remediation.consolidate_qbo_jobs
    # 2) apply (after reviewing the dry-run, on sandbox first):
    bench --site <site> execute \\
      erpnext_enhancements.quickbooks_online.core.job_remediation.consolidate_qbo_jobs \\
      --kwargs "{'apply': True}"

Deploy the forward fix and run this (so every QBO Sync Mapping points at a Project)
**before** re-enabling the QBO sync, so the paused sync cannot recreate the flat
Customers. See ``quickbooks_online/MIGRATION_NOTES.md`` for the full runbook.
"""

from __future__ import annotations

import frappe

from erpnext_enhancements.quickbooks_online.core.mapping import (
	_is_qbo_customer_job,
	_match_project,
	_raw_payload_dict,
)
from erpnext_enhancements.quickbooks_online.core.utils import get_settings

QBO_COMMIT_EVERY = 25


def consolidate_qbo_jobs(apply=False, limit=None, clean_drive=True, verbose=True):
	"""Consolidate the legacy colon-named QBO job-Customers into parent + Project.

	Args:
		apply: When False (default) this is a DRY RUN -- it computes and reports the
			plan for every job but writes nothing. Pass True to perform the merges,
			Project links/creates, invoice tags, mapping repoints and Drive cleanup.
		limit: Optionally process at most this many jobs (handy for a first sandbox
			run, e.g. ``limit=5``).
		clean_drive: When True, also trash/relocate each job's orphan Drive folder.
			Set False to do only the ERPNext-side consolidation.
		verbose: Print a per-job line in addition to the summary.

	Returns:
		dict: A summary report (counts per outcome + a ``jobs`` list of per-record
		results). Also printed for ``bench execute`` visibility.
	"""
	if apply:
		# Writing path is privileged; the dry run is safe for anyone to preview.
		frappe.only_for("System Manager")

	settings = get_settings()
	service, shared_drive_id = (None, None)
	if clean_drive:
		service, shared_drive_id = _maybe_drive_service()

	jobs = _enumerate_jobs(limit=limit)
	report = _new_report("apply" if apply else "dry-run", len(jobs))

	for index, (mapping_row, payload) in enumerate(jobs, start=1):
		try:
			result = _process_job(
				mapping_row, payload, settings, apply, clean_drive, service, shared_drive_id
			)
		except Exception:  # one bad job must never abort the batch
			report["errors"] += 1
			frappe.log_error(
				f"QBO job remediation failed for {mapping_row.get('erpnext_name')} "
				f"(qbo_id {mapping_row.get('qbo_id')})\n{frappe.get_traceback()}",
				"QBO Job Remediation Error",
			)
			result = {"job": mapping_row.get("erpnext_name"), "outcome": "error"}

		_tally(report, result)
		report["jobs"].append(result)
		if verbose:
			print(f"  [{index}/{len(jobs)}] {result.get('job')}: {result.get('outcome')}")

		if apply and index % QBO_COMMIT_EVERY == 0:
			frappe.db.commit()

	if apply:
		frappe.db.commit()

	_print_summary(report)
	return report


def _new_report(mode, total_jobs):
	"""Zeroed summary counters for a remediation run."""
	return {
		"mode": mode,
		"total_jobs": total_jobs,
		"project_linked": 0,
		"project_created": 0,
		"merged": 0,
		"self_skipped": 0,
		"invoices_tagged": 0,
		"folders_trashed": 0,
		"folders_moved": 0,
		"folders_flagged": 0,
		"folders_already_clean": 0,
		"no_parent": 0,
		"ambiguous_project": 0,
		"errors": 0,
		"jobs": [],
	}


def _enumerate_jobs(limit=None):
	"""The canonical QBO job set: QBO Sync Mappings (qbo_entity_type='Customer') whose
	latest raw payload is a job, sorted top-level-first (ascending QBO ``Level``).

	Driven off the mapping + payload -- never a blind ``customer_name LIKE '%:%'`` --
	so it (a) ignores a legitimately colon-named non-job Customer and (b) still catches
	jobs that auto-linked to a flat, non-colon-named Customer. Ascending Level ordering
	consolidates a job's parent before the job (parent resolution itself does not depend
	on it -- see ``_resolve_parent_customer``).
	"""
	mappings = frappe.get_all(
		"QuickBooks Sync Mapping",
		filters={"qbo_entity_type": "Customer"},
		fields=["name", "qbo_id", "erpnext_doctype", "erpnext_name"],
	)
	jobs = []
	for mapping_row in mappings:
		payload = _raw_payload_dict("Customer", mapping_row.qbo_id)
		if payload and _is_qbo_customer_job(payload):
			jobs.append((mapping_row, payload))
	jobs.sort(key=lambda pair: (pair[1].get("Level") or 0, str(pair[0].qbo_id)))
	return jobs[:limit] if limit else jobs


def _process_job(mapping_row, payload, settings, apply, clean_drive, service, shared_drive_id):
	"""Plan (and, when ``apply``, execute) the consolidation of a single job."""
	job_label = mapping_row.erpnext_name
	result = {"job": job_label, "qbo_id": mapping_row.qbo_id, "outcome": "planned"}

	parent = _resolve_parent_customer(payload)
	if not parent or not frappe.db.exists("Customer", parent):
		result["outcome"] = "skip-no-parent"
		result["note"] = f"top-level parent customer unresolved (parent={parent!r})"
		return result
	result["parent"] = parent

	# The flat job-Customer to merge away (only when the mapping still points at one
	# that exists -- otherwise the merge already happened on a prior run).
	old_customer = (
		mapping_row.erpnext_name
		if mapping_row.erpnext_doctype == "Customer" and frappe.db.exists("Customer", mapping_row.erpnext_name)
		else None
	)
	self_merge = old_customer is not None and old_customer == parent

	# Link an EXISTING Project for this job, or None (never create one).
	project = _resolve_project(mapping_row, payload, settings)
	result["project"] = project

	# Capture the orphan folder id BEFORE the merge deletes the job-Customer. ONLY for a
	# genuine colon job being merged away -- never for the parent itself (a self-merge,
	# or an already-consolidated job re-processed on a later run, where the mapping now
	# points at the parent). The parent's folder is real and must not be touched.
	folder_id = (
		_orphan_folder_id(old_customer) if (clean_drive and old_customer and not self_merge) else None
	)

	# Invoices to tag with the Project (still on the job-Customer pre-merge).
	invoice_names = (
		frappe.get_all("Sales Invoice", filters={"customer": old_customer}, pluck="name")
		if old_customer
		else []
	)
	result["invoices"] = len(invoice_names)
	result["would_merge"] = bool(old_customer) and not self_merge
	result["self_merge"] = self_merge

	if not apply:
		result["outcome"] = "dry-run"
		if clean_drive and folder_id and service:
			result["folder_plan"] = _classify_folder(service, shared_drive_id, folder_id)
		return result

	# --- writes (apply mode) -----------------------------------------------------
	# 1) Tag invoices with the Project (before the merge; tagging is by SI name and
	#    independent of which customer the invoice is on, so a crash before the merge
	#    leaves them correctly tagged).
	tagged = 0
	if project:
		for si_name in invoice_names:
			if _tag_invoice_project(si_name, project):
				tagged += 1
	result["invoices_tagged"] = tagged

	# 2) Merge the job-Customer into the parent (moves all references, deletes the job).
	if old_customer and not self_merge:
		# Frappe's rename_doc(merge=True) runs orjson.loads on each party's _assign /
		# _liked_by; an empty-string value (left by some imports) raises JSONDecodeError
		# mid-merge. Normalise "" -> NULL on both ends first. (rename_doc on this Frappe
		# version takes no ignore_permissions kwarg.)
		_normalize_meta_json(old_customer)
		_normalize_meta_json(parent)
		frappe.rename_doc("Customer", old_customer, parent, merge=True)
		result["merged"] = True

	# 3) Repoint the QBO Sync Mapping (the "done" marker; last DB step): to the matched
	#    Project, or -- when the job has no project -- to the parent Customer, so the
	#    paused sync resumes against a real record instead of recreating the colon name.
	if project:
		frappe.db.set_value(
			"QuickBooks Sync Mapping", mapping_row.name,
			{"erpnext_doctype": "Project", "erpnext_name": project,
			 "match_status": "Manual Matched", "match_rule": "job_remediation"},
		)
	else:
		frappe.db.set_value(
			"QuickBooks Sync Mapping", mapping_row.name,
			{"erpnext_doctype": "Customer", "erpnext_name": parent,
			 "match_status": "Manual Matched", "match_rule": "job_merge_no_project"},
		)

	# 4) Clean the orphan Drive folder (reversible; never aborts the DB work).
	if clean_drive and folder_id and service:
		result["folder_action"] = _cleanup_folder(
			service, shared_drive_id, folder_id, parent, project, payload, job_label
		)

	result["outcome"] = "consolidated"
	return result


def _normalize_meta_json(customer):
	"""Set a Customer's framework meta-JSON fields to NULL when stored as an empty
	string. ``frappe.rename_doc(merge=True)`` runs ``orjson.loads`` on ``_assign`` /
	``_liked_by`` (e.g. in ``update_assignments``); an empty string raises
	``JSONDecodeError`` and aborts the merge. Some imported customers carry ``""``
	there, so normalise both the job and its parent before merging."""
	for field in ("_assign", "_liked_by"):
		if frappe.db.get_value("Customer", customer, field) == "":
			frappe.db.set_value("Customer", customer, field, None, update_modified=False)


def _resolve_parent_customer(payload):
	"""The ERPNext Customer for a job's TOP-LEVEL (Level-0) ancestor, or None.

	Walks ``ParentRef`` up the QBO raw-payload chain to the first ancestor that is not
	itself a job, then resolves that QBO customer to its ERPNext Customer via the sync
	mapping. Walking the payloads (not the live mapping) makes the result independent of
	processing order/state -- correct in dry-run and even if an intermediate parent's
	own merge errored -- so a multi-level (Level 2-4) job still resolves to the real
	top-level customer rather than a still-colon intermediate parent.
	"""
	seen: set[str] = set()
	parent_id = (payload.get("ParentRef") or {}).get("value")
	while parent_id and str(parent_id) not in seen:
		seen.add(str(parent_id))
		parent_payload = _raw_payload_dict("Customer", parent_id)
		if parent_payload is None or not _is_qbo_customer_job(parent_payload):
			# Reached the top-level (non-job) ancestor (or its payload is missing).
			return _customer_name_for_qbo(parent_id)
		parent_id = (parent_payload.get("ParentRef") or {}).get("value")
	return None


def _customer_name_for_qbo(qbo_id):
	"""ERPNext Customer name a top-level QBO customer maps to (and still exists), else None."""
	if not qbo_id:
		return None
	name = frappe.db.get_value(
		"QuickBooks Sync Mapping",
		{"qbo_entity_type": "Customer", "qbo_id": str(qbo_id), "erpnext_doctype": "Customer"},
		"erpnext_name",
	)
	return name if name and frappe.db.exists("Customer", name) else None


def _resolve_project(mapping_row, payload, settings):
	"""The EXISTING ERPNext Project this job maps to, or ``None`` -- NEVER creates one.

	The site already holds every QBO project, so a job is only ever *linked* to an
	existing Project (matched by its ``PRJ-###`` number, else an exact ``project_name``
	+ customer -- see ``_match_project``). A job with no match (the internal / deleted /
	differently-named ones) is consolidated into its parent Customer with **no project**;
	its transactions roll up to the parent untagged. We never invent a Project, and an
	ambiguous match is treated as no-match (left untagged) rather than a guess.
	"""
	if mapping_row.erpnext_doctype == "Project" and frappe.db.exists("Project", mapping_row.erpnext_name):
		return mapping_row.erpnext_name
	match = _match_project(payload, settings)
	return match["name"] if (match and match.get("status") == "matched") else None


def _tag_invoice_project(si_name, project):
	"""Set ``project`` on a Sales Invoice (header + item rows; + GL rows if submitted).

	Idempotent (skips when already set). All of production's affected invoices are
	drafts, so this is a plain field denormalisation; the submitted-doc / GL path is
	kept correct for the general case but no-ops on draft, GL-less data.
	"""
	current = frappe.db.get_value("Sales Invoice", si_name, ["docstatus", "project"], as_dict=True)
	if not current or current.project == project:
		return False
	frappe.db.set_value("Sales Invoice", si_name, "project", project, update_modified=False)
	for item in frappe.get_all("Sales Invoice Item", filters={"parent": si_name}, pluck="name"):
		frappe.db.set_value("Sales Invoice Item", item, "project", project, update_modified=False)
	if current.docstatus == 1:
		# Submitted: GL rows carry the project denormalised and feed costing reports.
		for gl in frappe.get_all(
			"GL Entry",
			filters={"voucher_type": "Sales Invoice", "voucher_no": si_name, "is_cancelled": 0},
			pluck="name",
		):
			frappe.db.set_value("GL Entry", gl, "project", project, update_modified=False)
	return True


def _orphan_folder_id(customer):
	"""The job-Customer's orphan Drive folder id, or None.

	Prefers ``Customer.custom_drive_folder_id``; falls back to the most recent
	"Provision Folder" Drive Sync Log row for that customer (covers the handful whose
	field was never stamped). The log row also survives the customer's deletion.
	"""
	folder_id = frappe.db.get_value("Customer", customer, "custom_drive_folder_id")
	if folder_id:
		return folder_id
	rows = frappe.get_all(
		"Drive Sync Log",
		filters={"reference_doctype": "Customer", "reference_name": customer, "action": "Provision Folder"},
		fields=["drive_file_id"],
		order_by="creation desc",
		limit=1,
	)
	return rows[0].drive_file_id if rows and rows[0].drive_file_id else None


def _maybe_drive_service():
	"""Build the Drive service if configured, else ``(None, None)`` (Drive cleanup is
	then skipped with a logged note rather than failing the DB remediation)."""
	try:
		from erpnext_enhancements.google_drive.drive_utils import get_drive_service

		return get_drive_service()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "QBO Job Remediation: Drive unavailable")
		return None, None


def _classify_folder(service, shared_drive_id, folder_id):
	"""Read-only probe: ``'gone' | 'empty' | 'nonempty'`` for the orphan folder."""
	from erpnext_enhancements.google_drive.drive_sync import _drive_id_of, _list_folder_children
	from erpnext_enhancements.google_drive.drive_utils import get_folder_meta

	try:
		meta = get_folder_meta(service, folder_id, shared_drive_id)
		if meta is None or meta.get("trashed"):
			return "gone"
		drive_id = shared_drive_id or _drive_id_of(service, folder_id)
		return "empty" if not _list_folder_children(service, folder_id, drive_id) else "nonempty"
	except Exception:
		return "probe-failed"


def _cleanup_folder(service, shared_drive_id, folder_id, parent, project, payload, job_label):
	"""Trash an empty orphan folder; relocate a non-empty one under the parent.

	Idempotent and non-fatal: a missing/trashed folder is ``already-clean``; any Drive
	error is logged and reported (never rolls back the committed DB consolidation).
	A non-empty folder is moved under the parent customer's folder and renamed to the
	project folder name so its files stay (now correctly nested). Returns a short
	status string. All actions are logged to Drive Sync Log.
	"""
	from erpnext_enhancements.google_drive.drive_sync import _drive_id_of, _list_folder_children, log_sync
	from erpnext_enhancements.google_drive.drive_utils import (
		create_folder,
		find_folder,
		get_folder_meta,
		move_folder,
		rename_folder,
		trash_folder,
	)

	try:
		meta = get_folder_meta(service, folder_id, shared_drive_id)
		if meta is None or meta.get("trashed"):
			return "already-clean"

		drive_id = shared_drive_id or _drive_id_of(service, folder_id)
		if not _list_folder_children(service, folder_id, drive_id):
			trash_folder(service, folder_id, shared_drive_id)
			log_sync(
				"Provision Folder", "Success",
				reference_doctype="Customer", reference_name=job_label,
				file_name="Orphan job folder trashed (empty)", drive_file_id=folder_id,
			)
			return "trashed"

		# Non-empty: relocate under the parent customer's folder, renamed to the project.
		parent_label = frappe.db.get_value("Customer", parent, "customer_name") or parent
		parent_folder = find_folder(service, parent_label, shared_drive_id, shared_drive_id)
		if not parent_folder:
			parent_folder, _link = create_folder(service, parent_label, shared_drive_id, shared_drive_id)
		new_name = _project_folder_name(project, payload)
		move_folder(service, folder_id, parent_folder, shared_drive_id)
		rename_folder(service, folder_id, new_name, shared_drive_id)
		log_sync(
			"Provision Folder", "Success",
			reference_doctype="Customer", reference_name=job_label,
			file_name=f"Orphan job folder relocated under {parent_label} as {new_name}",
			drive_file_id=folder_id,
		)
		return "moved"
	except Exception:
		frappe.log_error(
			f"Orphan Drive folder cleanup failed for {job_label} (folder {folder_id})\n{frappe.get_traceback()}",
			"QBO Job Remediation: Drive cleanup",
		)
		return "drive-error"


def _project_folder_name(project, payload):
	"""Folder name for a relocated non-empty orphan: the leaf job name, prefixed with
	the ERPNext project id when known (mirrors the project-folder convention)."""
	leaf = payload.get("DisplayName") or (payload.get("FullyQualifiedName") or "").split(":")[-1]
	if project and not project.startswith("(new)") and leaf and not leaf.startswith(project):
		return f"{project} - {leaf}".strip(" -")
	return leaf or project


_FOLDER_OUTCOME_KEY = {
	# apply-mode folder_action ...
	"trashed": "folders_trashed",
	"moved": "folders_moved",
	"already-clean": "folders_already_clean",
	"drive-error": "folders_flagged",
	# ... and dry-run folder_plan (what it WOULD do)
	"empty": "folders_trashed",
	"nonempty": "folders_moved",
	"gone": "folders_already_clean",
	"probe-failed": "folders_flagged",
}


def _tally(report, result):
	"""Fold a per-job result into the running summary counters (mode-aware).

	Errors are counted by the caller's except handler, not here. In dry-run the
	"merged"/"invoices_tagged" counters report what WOULD happen (would_merge / the
	invoice count), so the preview totals match a subsequent apply run.
	"""
	outcome = result.get("outcome")
	if outcome == "skip-no-parent":
		report["no_parent"] += 1
		return
	if outcome == "skip-ambiguous-project":
		report["ambiguous_project"] += 1
		return
	if outcome == "error":
		return

	if result.get("project_created"):
		report["project_created"] += 1
	elif result.get("project"):
		report["project_linked"] += 1
	if result.get("self_merge"):
		report["self_skipped"] += 1

	if report["mode"] == "apply":
		if result.get("merged"):
			report["merged"] += 1
		report["invoices_tagged"] += result.get("invoices_tagged", 0)
	else:
		if result.get("would_merge"):
			report["merged"] += 1
		report["invoices_tagged"] += result.get("invoices", 0)

	key = _FOLDER_OUTCOME_KEY.get(result.get("folder_action") or result.get("folder_plan"))
	if key:
		report[key] += 1


def _print_summary(report):
	"""Print a human-readable summary for ``bench execute``."""
	print(f"\n=== QBO job remediation ({report['mode']}) ===")
	for key in (
		"total_jobs", "project_linked", "project_created", "merged", "self_skipped",
		"invoices_tagged", "folders_trashed", "folders_moved", "folders_already_clean",
		"folders_flagged", "no_parent", "ambiguous_project", "errors",
	):
		print(f"  {key:24} {report[key]}")
	if report["mode"] == "dry-run":
		print("  (dry run -- nothing was written; re-run with apply=True to execute)")
