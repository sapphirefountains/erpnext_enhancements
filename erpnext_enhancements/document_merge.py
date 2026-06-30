"""Generic document merge tool.

Consolidates a duplicate document (the **loser**) into the one you keep (the
**survivor**), for *any* doctype: every reference anywhere on the site is
repointed at the survivor, the survivor's blank fields are backfilled from the
loser, the loser's child rows are appended, and the loser is then deleted.

Merge rules (decided with the operator):

* **Survivor wins, backfill blanks.** The survivor keeps every value it already
  has; only its *empty* fields (``None`` / ``""`` — never a real ``0``) are
  filled from the loser. The loser's child-table rows are appended (exact
  duplicates are skipped).
* **References: everything.** Standard Link fields, Dynamic Links, child-table
  links and Single docs (discovered by the shared
  :func:`erpnext_enhancements.delete_utils.discover_references`) **plus** the
  "soft" reference tables the framework keys by ``reference_doctype`` /
  ``reference_name`` rather than a declared Link field — Files/attachments,
  Comments, ToDos/assignments, Communications, Tags, Versions, etc. (see
  :data:`SOFT_REFERENCE_TABLES`).
* **Free-text is flagged, never rewritten.** Where the loser's *name* appears
  inside note/email bodies we surface it as "manual review"; we never edit prose.
* **The loser is deleted** once its references are clear.

Safety / gating:

* Behind the default-OFF ``document_merge_enabled`` switch (ERPNext Enhancements
  Settings → Document Merge); :func:`feature_flags.throw_if_document_merge_disabled`.
* **System Manager** only, plus ``write`` on the survivor and ``delete`` on the
  loser.
* **Submitted documents are refused** (either side). References *to* the docs may
  be on submitted documents — those are repointed low-level (``db.set_value``),
  bypassing validation so a posted invoice repoints cleanly.
* Irreversible, so every merge writes a **Document Merge Log** row.
* Large merges (> :data:`BACKGROUND_REF_THRESHOLD` references) run as a
  background job; smaller ones run inline.

Whitelisted endpoints, called from ``public/js/merge_tool/merge_tool.js``:
:func:`get_merge_preview` powers the diff/confirm dialog and :func:`perform_merge`
executes (or enqueues) the merge.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model import no_value_fields
from frappe.utils import cint, cstr

from erpnext_enhancements.delete_utils import discover_references
from erpnext_enhancements.feature_flags import throw_if_document_merge_disabled

# Over this many discovered references, run the merge on the background queue
# instead of inline (keeps the request from timing out on big master-data merges).
BACKGROUND_REF_THRESHOLD = 2000

# Commit every N reference repoints so a mid-run failure keeps completed work.
COMMIT_EVERY = 200

# Framework "soft" reference tables — keyed by a (doctype, name) column pair that
# is NOT a declared Link/Dynamic Link field, so discover_references can't see them.
# (doctype, doctype_column, name_column). Each is guarded: a table/column that is
# absent on this site's Frappe is silently skipped.
SOFT_REFERENCE_TABLES = [
	("File", "attached_to_doctype", "attached_to_name"),
	("Comment", "reference_doctype", "reference_name"),
	("ToDo", "reference_type", "reference_name"),
	("Communication", "reference_doctype", "reference_name"),
	("Communication Link", "link_doctype", "link_name"),
	("Notification Log", "document_type", "document_name"),
	("Email Queue Recipient", "reference_doctype", "reference_name"),
	("Tag Link", "document_type", "document_name"),
	("Energy Point Log", "reference_doctype", "reference_name"),
	("Document Follow", "ref_doctype", "ref_docname"),
	("Activity Log", "reference_doctype", "reference_name"),
	("Version", "ref_doctype", "docname"),
]

# Free-text bodies scanned for a bare mention of the loser's name (manual-review
# flags only — never rewritten). (doctype, [text columns]).
FREE_TEXT_SCAN = [
	("Comment", ["content"]),
	("Communication", ["content", "subject"]),
]

# Cap the manual-review scan so a common name can't return thousands of rows.
MANUAL_REVIEW_LIMIT = 50


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _validate_pair(doctype, survivor, loser, *, for_write):
	"""Shared guards for preview (read) and merge (write).

	Returns the survivor and loser as loaded docs. Both preview and merge are a
	System-Manager-only, switch-gated feature (preview reads field values, so it
	must be gated too — otherwise any logged-in user could read arbitrary docs
	through it). ``for_write`` *additionally* enforces per-doc write/delete
	permission. Refuses cross-doctype, self-merge, Single doctypes and Submitted
	documents on either side.
	"""
	throw_if_document_merge_disabled()
	frappe.only_for("System Manager")

	if not doctype or not survivor or not loser:
		frappe.throw(_("Doctype, survivor and loser are all required."))

	if survivor == loser:
		frappe.throw(_("The survivor and the document to merge cannot be the same."))

	meta = frappe.get_meta(doctype)
	if meta.issingle:
		frappe.throw(_("Single doctypes ({0}) cannot be merged.").format(doctype))

	for label, name in ((_("survivor"), survivor), (_("document to merge"), loser)):
		if not frappe.db.exists(doctype, name):
			frappe.throw(_("The {0} {1} {2} does not exist.").format(doctype, label, name))

	survivor_doc = frappe.get_doc(doctype, survivor)
	loser_doc = frappe.get_doc(doctype, loser)

	for label, doc in ((_("survivor"), survivor_doc), (_("document to merge"), loser_doc)):
		if doc.docstatus == 1:
			frappe.throw(
				_("{0} {1} is submitted. Submitted documents cannot be merged.").format(
					_(doctype), doc.name
				)
			)

	if for_write:
		if not frappe.has_permission(doctype, "write", doc=survivor_doc):
			frappe.throw(_("You do not have permission to modify the survivor {0}.").format(survivor))
		if not frappe.has_permission(doctype, "delete", doc=loser_doc):
			frappe.throw(_("You do not have permission to delete {0}.").format(loser))

	return survivor_doc, loser_doc


# ---------------------------------------------------------------------------
# Field & child-table diff (survivor wins, backfill blanks)
# ---------------------------------------------------------------------------
def _is_blank(value):
	"""Blank = nothing to keep. A real 0 / 0.0 is a value and is NOT blank, so a
	survivor's zero is never clobbered by a loser's non-zero."""
	return value is None or value == ""


def _scalar_fields(meta):
	"""Value-bearing, non-table fields worth diffing/backfilling."""
	skip = {"naming_series"}
	for df in meta.fields:
		if df.fieldtype in no_value_fields:
			continue
		if df.fieldtype == "Password":
			continue
		if df.fieldname in skip:
			continue
		yield df


def _field_diff(survivor_doc, loser_doc):
	"""Per-field plan. Returns rows for fields that differ or will be backfilled
	(fields that are equal or blank-on-both are omitted to keep the preview tight).

	``action`` is one of: ``backfill`` (survivor blank, take loser's value),
	``differs`` (both set, survivor kept). Values are stringified for display.
	"""
	rows = []
	for df in _scalar_fields(survivor_doc.meta):
		sv = survivor_doc.get(df.fieldname)
		lv = loser_doc.get(df.fieldname)

		if _is_blank(sv) and not _is_blank(lv):
			action = "backfill"
		elif not _is_blank(sv) and not _is_blank(lv) and sv != lv:
			action = "differs"
		else:
			continue

		rows.append(
			{
				"fieldname": df.fieldname,
				"label": _(df.label) if df.label else df.fieldname,
				"fieldtype": df.fieldtype,
				"survivor": cstr(sv),
				"loser": cstr(lv),
				"action": action,
			}
		)
	return rows


def _child_row_signature(row, child_meta):
	"""Tuple of a child row's value fields, used to skip appending exact dupes."""
	return tuple(
		cstr(row.get(df.fieldname)) for df in _scalar_fields(child_meta)
	)


def _child_append_plan(survivor_doc, loser_doc):
	"""How many of the loser's child rows would be appended per table (exact
	duplicates of an existing survivor row are skipped)."""
	plan = []
	for df in survivor_doc.meta.get_table_fields():
		loser_rows = loser_doc.get(df.fieldname) or []
		if not loser_rows:
			continue
		child_meta = frappe.get_meta(df.options)
		existing = {
			_child_row_signature(r, child_meta) for r in (survivor_doc.get(df.fieldname) or [])
		}
		to_append = sum(
			1 for r in loser_rows if _child_row_signature(r, child_meta) not in existing
		)
		if to_append:
			plan.append(
				{
					"fieldname": df.fieldname,
					"label": _(df.label) if df.label else df.fieldname,
					"child_doctype": df.options,
					"loser_rows": len(loser_rows),
					"appended": to_append,
				}
			)
	return plan


def _apply_backfill(survivor_doc, loser_doc):
	"""Mutate the survivor in place: fill blank fields from the loser and append
	the loser's non-duplicate child rows. Returns (n_fields, n_rows) for the log.
	Caller saves the survivor."""
	n_fields = 0
	for df in _scalar_fields(survivor_doc.meta):
		sv = survivor_doc.get(df.fieldname)
		lv = loser_doc.get(df.fieldname)
		if _is_blank(sv) and not _is_blank(lv):
			survivor_doc.set(df.fieldname, lv)
			n_fields += 1

	n_rows = 0
	for df in survivor_doc.meta.get_table_fields():
		loser_rows = loser_doc.get(df.fieldname) or []
		if not loser_rows:
			continue
		child_meta = frappe.get_meta(df.options)
		existing = {
			_child_row_signature(r, child_meta) for r in (survivor_doc.get(df.fieldname) or [])
		}
		for r in loser_rows:
			sig = _child_row_signature(r, child_meta)
			if sig in existing:
				continue
			existing.add(sig)
			row_copy = {
				cf.fieldname: r.get(cf.fieldname) for cf in _scalar_fields(child_meta)
			}
			survivor_doc.append(df.fieldname, row_copy)
			n_rows += 1

	return n_fields, n_rows


# ---------------------------------------------------------------------------
# Soft references + free-text scan
# ---------------------------------------------------------------------------
def _soft_reference_rows(doctype, name):
	"""Rows in the framework "soft" reference tables that point at (doctype, name).

	Returns a list of dicts: {table, doctype_column, name_column, row, parent...}.
	Guarded so an absent table/column on this Frappe is skipped, not fatal.
	"""
	rows = []
	for table, dt_col, name_col in SOFT_REFERENCE_TABLES:
		if not frappe.db.table_exists(table):
			continue
		try:
			if not (frappe.db.has_column(table, dt_col) and frappe.db.has_column(table, name_col)):
				continue
		except Exception:
			continue

		try:
			matches = frappe.get_all(
				table,
				filters={dt_col: doctype, name_col: name},
				fields=["name"],
			)
		except Exception:
			continue

		for m in matches:
			rows.append({"table": table, "dt_col": dt_col, "name_col": name_col, "row": m.name})
	return rows


def _manual_review_flags(doctype, loser):
	"""Bare mentions of the loser's *name* inside note/email bodies — flagged, not
	rewritten (see module docstring). Bounded by ``MANUAL_REVIEW_LIMIT``. Only the
	loser's id is matched (e.g. ``CUST-0001``), which is far less false-positive
	prone than its title; rows that are themselves being repointed are excluded."""
	flags = []
	needle = f"%{loser}%"
	for table, columns in FREE_TEXT_SCAN:
		if len(flags) >= MANUAL_REVIEW_LIMIT:
			break
		if not frappe.db.table_exists(table):
			continue
		for col in columns:
			if len(flags) >= MANUAL_REVIEW_LIMIT:
				break
			try:
				if not frappe.db.has_column(table, col):
					continue
				hits = frappe.get_all(
					table,
					filters={col: ("like", needle)},
					fields=["name", "reference_doctype", "reference_name"],
					limit=MANUAL_REVIEW_LIMIT,
				)
			except Exception:
				continue
			for h in hits:
				# Skip the rows we'll structurally repoint anyway.
				if h.get("reference_doctype") == doctype and h.get("reference_name") == loser:
					continue
				flags.append(
					{
						"doctype": table,
						"name": h.name,
						"fieldname": col,
						"on_doctype": h.get("reference_doctype"),
						"on_name": h.get("reference_name"),
					}
				)
				if len(flags) >= MANUAL_REVIEW_LIMIT:
					break
	return flags


def _aggregate_hard_references(refs):
	"""Group raw discover_references rows into per-(doctype, field) counts for the
	preview, and a flat total."""
	groups = {}
	for r in refs:
		key = (r["doctype"], r["fieldname"], bool(r.get("is_child")))
		groups.setdefault(key, 0)
		groups[key] += 1
	return [
		{"doctype": dt, "fieldname": field, "is_child": is_child, "count": count}
		for (dt, field, is_child), count in sorted(groups.items())
	]


def _aggregate_soft_references(rows):
	groups = {}
	for r in rows:
		groups.setdefault(r["table"], 0)
		groups[r["table"]] += 1
	return [{"table": t, "count": c} for t, c in sorted(groups.items())]


# ---------------------------------------------------------------------------
# Preview (read-only)
# ---------------------------------------------------------------------------
@frappe.whitelist()
def get_merge_preview(doctype, survivor, loser):
	"""Read-only dry run: exactly what :func:`perform_merge` will do.

	Returns the field diff (kept / backfilled), the child-table append plan, the
	grouped reference counts (hard + soft), the manual-review flags, and whether
	execution will run in the background.
	"""
	survivor_doc, loser_doc = _validate_pair(doctype, survivor, loser, for_write=False)

	hard_refs = discover_references(doctype, loser, include_cancelled=True, respect_ignore_hook=False)
	soft_rows = _soft_reference_rows(doctype, loser)
	reference_total = len(hard_refs) + len(soft_rows)

	title_field = survivor_doc.meta.get_title_field()

	return {
		"doctype": doctype,
		"survivor": survivor,
		"loser": loser,
		"survivor_title": cstr(survivor_doc.get(title_field)) if title_field else "",
		"loser_title": cstr(loser_doc.get(title_field)) if title_field else "",
		"fields": _field_diff(survivor_doc, loser_doc),
		"child_tables": _child_append_plan(survivor_doc, loser_doc),
		"hard_references": _aggregate_hard_references(hard_refs),
		"soft_references": _aggregate_soft_references(soft_rows),
		"reference_total": reference_total,
		"manual_review": _manual_review_flags(doctype, loser),
		"background": reference_total > BACKGROUND_REF_THRESHOLD,
	}


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
@frappe.whitelist()
def perform_merge(doctype, survivor, loser):
	"""Merge ``loser`` into ``survivor``. Runs inline for typical merges; enqueues
	a background job when the reference count exceeds
	:data:`BACKGROUND_REF_THRESHOLD`. Returns a summary dict (or a "queued" marker).
	"""
	_validate_pair(doctype, survivor, loser, for_write=True)

	hard_refs = discover_references(doctype, loser, include_cancelled=True, respect_ignore_hook=False)
	soft_rows = _soft_reference_rows(doctype, loser)

	if len(hard_refs) + len(soft_rows) > BACKGROUND_REF_THRESHOLD:
		frappe.enqueue(
			"erpnext_enhancements.document_merge.execute_merge_job",
			queue="long",
			timeout=3600,
			doctype=doctype,
			survivor=survivor,
			loser=loser,
			user=frappe.session.user,
		)
		return {
			"queued": True,
			"message": _(
				"This merge touches {0} references and is running in the background. "
				"You'll be notified when it finishes."
			).format(len(hard_refs) + len(soft_rows)),
		}

	return _execute_merge(doctype, survivor, loser, hard_refs, soft_rows)


def execute_merge_job(doctype, survivor, loser, user):
	"""Background entry point (enqueued by :func:`perform_merge`). Re-discovers
	references (the world may have changed since enqueue), runs the merge, and
	pushes a realtime notice to the initiating user."""
	# Re-run the write guards in the job's own context.
	_validate_pair(doctype, survivor, loser, for_write=True)
	hard_refs = discover_references(doctype, loser, include_cancelled=True, respect_ignore_hook=False)
	soft_rows = _soft_reference_rows(doctype, loser)
	try:
		summary = _execute_merge(doctype, survivor, loser, hard_refs, soft_rows)
		frappe.publish_realtime(
			"document_merge_done",
			{"success": True, "doctype": doctype, "survivor": survivor, "loser": loser,
			 "references": summary["references_repointed"]},
			user=user,
		)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "Document Merge (background) failed")
		frappe.publish_realtime(
			"document_merge_done",
			{"success": False, "doctype": doctype, "survivor": survivor, "loser": loser},
			user=user,
		)
		raise


def _execute_merge(doctype, survivor, loser, hard_refs, soft_rows):
	"""The actual work, shared by the inline and background paths. Assumes the
	write guards have already run."""
	survivor_doc = frappe.get_doc(doctype, survivor)
	loser_doc = frappe.get_doc(doctype, loser)

	# 1) Backfill the survivor's blanks + append the loser's child rows.
	n_fields, n_rows = _apply_backfill(survivor_doc, loser_doc)
	if n_fields or n_rows:
		survivor_doc.save()

	# 2) Repoint hard references (Link / Dynamic Link / child / Single), low-level.
	hard_count = _repoint_hard_references(doctype, survivor, loser, hard_refs)

	# 3) Repoint soft references (Files / Comments / ToDos / Communications / …).
	soft_count = _repoint_soft_references(survivor, loser, soft_rows)

	frappe.db.commit()

	# 4) Delete the loser (references are clear, so no LinkExistsError).
	frappe.delete_doc(doctype, loser, force=1, ignore_permissions=True)
	frappe.db.commit()

	# 5) Record the irreversible operation.
	manual_review = _manual_review_flags(doctype, loser)
	_write_merge_log(
		doctype, survivor, loser, n_fields, n_rows, hard_count, soft_count, manual_review
	)

	return {
		"doctype": doctype,
		"survivor": survivor,
		"loser": loser,
		"fields_backfilled": n_fields,
		"child_rows_appended": n_rows,
		"references_repointed": hard_count + soft_count,
		"manual_review": len(manual_review),
		"message": _("Merged {0} into {1}: {2} references repointed, {3} fields backfilled.").format(
			loser, survivor, hard_count + soft_count, n_fields
		),
	}


def _repoint_hard_references(doctype, survivor, loser, refs):
	"""Set every discovered Link/Dynamic Link/child/Single reference from loser to
	survivor, low-level (bypasses validation so submitted referrers repoint too).

	Handles two collision cases:
	* A referrer that *is* the survivor would become a self-link — null it instead
	  (avoids e.g. parent_project pointing at itself).
	* A child/dynamic row whose parent already points at the survivor on the same
	  field would duplicate — drop the loser row instead of repointing.
	"""
	count = 0
	for i, ref in enumerate(refs):
		try:
			if ref.get("is_single"):
				frappe.db.set_single_value(ref["doctype"], ref["fieldname"], survivor)
				if ref.get("is_dynamic") and ref.get("doctype_field"):
					frappe.db.set_single_value(ref["doctype"], ref["doctype_field"], doctype)
				count += 1

			elif ref.get("is_child"):
				parent_dt, parent_name = ref["doctype"], ref["name"]
				child_dt, child_name = ref["child_doctype"], ref["child_name"]
				# Skip the loser's own rows (already filtered in discovery, belt-and-braces).
				if parent_dt == doctype and parent_name == loser:
					continue
				# Collision: parent already has a sibling row pointing at survivor.
				dup_filters = {"parent": parent_name, "parenttype": parent_dt, ref["fieldname"]: survivor}
				if ref.get("is_dynamic") and ref.get("doctype_field"):
					dup_filters[ref["doctype_field"]] = doctype
				if frappe.db.exists(child_dt, {**dup_filters, "name": ["!=", child_name]}):
					frappe.db.delete(child_dt, {"name": child_name})
				else:
					frappe.db.set_value(child_dt, child_name, ref["fieldname"], survivor, update_modified=False)
					if ref.get("is_dynamic") and ref.get("doctype_field"):
						frappe.db.set_value(child_dt, child_name, ref["doctype_field"], doctype, update_modified=False)
				count += 1

			else:
				ref_dt, ref_name = ref["doctype"], ref["name"]
				# A reference on the survivor itself would become a self-link; null it.
				if ref_dt == doctype and ref_name == survivor:
					frappe.db.set_value(ref_dt, ref_name, ref["fieldname"], None, update_modified=False)
					if ref.get("is_dynamic") and ref.get("doctype_field"):
						frappe.db.set_value(ref_dt, ref_name, ref["doctype_field"], None, update_modified=False)
				else:
					frappe.db.set_value(ref_dt, ref_name, ref["fieldname"], survivor, update_modified=False)
					if ref.get("is_dynamic") and ref.get("doctype_field"):
						frappe.db.set_value(ref_dt, ref_name, ref["doctype_field"], doctype, update_modified=False)
				count += 1

		except Exception:
			frappe.log_error(
				frappe.get_traceback(), f"Document Merge: failed to repoint reference {ref}"
			)

		if (i + 1) % COMMIT_EVERY == 0:
			frappe.db.commit()

	# Clear caches for parents touched (their cached child tables changed).
	frappe.clear_cache()
	return count


def _repoint_soft_references(survivor, loser, soft_rows):
	"""Repoint the framework soft-reference rows from loser to survivor, low-level.
	A repoint that would duplicate (e.g. a Tag Link the survivor already has) is
	dropped instead."""
	count = 0
	for i, ref in enumerate(soft_rows):
		table, name_col, row = ref["table"], ref["name_col"], ref["row"]
		try:
			frappe.db.set_value(table, row, name_col, survivor, update_modified=False)
			count += 1
		except Exception:
			# Likely a unique-key collision (survivor already has the equivalent row).
			try:
				frappe.db.delete(table, {"name": row})
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					f"Document Merge: failed to repoint soft reference {table} {row}",
				)
		if (i + 1) % COMMIT_EVERY == 0:
			frappe.db.commit()
	return count


def _write_merge_log(doctype, survivor, loser, n_fields, n_rows, hard_count, soft_count, manual_review):
	"""Append-only audit record of the (irreversible) merge."""
	try:
		log = frappe.get_doc(
			{
				"doctype": "Document Merge Log",
				"merged_doctype": doctype,
				"survivor": survivor,
				"loser": loser,
				"fields_backfilled": cint(n_fields),
				"child_rows_appended": cint(n_rows),
				"hard_references_repointed": cint(hard_count),
				"soft_references_repointed": cint(soft_count),
				"manual_review_count": len(manual_review),
				"manual_review": frappe.as_json(manual_review) if manual_review else None,
				"merged_by": frappe.session.user,
				"merged_on": frappe.utils.now(),
			}
		)
		log.insert(ignore_permissions=True)
	except Exception:
		# The merge already happened; never let logging failure surface as a merge error.
		frappe.log_error(frappe.get_traceback(), "Document Merge: failed to write merge log")
