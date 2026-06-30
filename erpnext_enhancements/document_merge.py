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
# The merge is still a single atomic transaction either way (see _execute_merge).
BACKGROUND_REF_THRESHOLD = 2000

# Framework "soft" reference tables — keyed by a (doctype, name) column pair that
# is NOT a declared Link/Dynamic Link field, so discover_references can't see them.
# (doctype, doctype_column, name_column). Each is guarded: a table/column that is
# absent on this site's Frappe is silently skipped.
SOFT_REFERENCE_TABLES = [
	("File", "attached_to_doctype", "attached_to_name"),
	("Comment", "reference_doctype", "reference_name"),
	("ToDo", "reference_type", "reference_name"),
	("Communication", "reference_doctype", "reference_name"),
	# Communication also carries a separate timeline target (what shows on a
	# record's Activity feed); it can differ from reference_name, so move it too.
	("Communication", "timeline_doctype", "timeline_name"),
	("Communication Link", "link_doctype", "link_name"),
	("Notification Log", "document_type", "document_name"),
	# The document-reference columns live on the Email Queue parent, NOT on the
	# Email Queue Recipient child (which only has recipient/status/error).
	("Email Queue", "reference_doctype", "reference_name"),
	("Tag Link", "document_type", "document_name"),
	("Energy Point Log", "reference_doctype", "reference_name"),
	("Document Follow", "ref_doctype", "ref_docname"),
	("Activity Log", "reference_doctype", "reference_name"),
	("Version", "ref_doctype", "docname"),
]


def _is_duplicate_entry_error(exc):
	"""True if ``exc`` is a DB duplicate/unique-key violation (MariaDB 1062).

	Used to tell a *genuine* collision (the survivor already owns the equivalent
	row — drop the loser's redundant one) apart from any other error (which must
	abort the merge, never silently delete data)."""
	args = getattr(exc, "args", None) or ()
	if args and args[0] == 1062:
		return True
	return "Duplicate entry" in str(exc)

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
		# _execute_merge is atomic and rolls back + logs on failure; here we only
		# turn its outcome into a realtime notice for the initiating user.
		summary = _execute_merge(doctype, survivor, loser, hard_refs, soft_rows)
		frappe.publish_realtime(
			"document_merge_done",
			{"success": True, "doctype": doctype, "survivor": survivor, "loser": loser,
			 "references": summary["references_repointed"]},
			user=user,
		)
	except Exception:
		frappe.publish_realtime(
			"document_merge_done",
			{"success": False, "doctype": doctype, "survivor": survivor, "loser": loser},
			user=user,
		)
		raise


def _execute_merge(doctype, survivor, loser, hard_refs, soft_rows):
	"""The actual work, shared by the inline and background paths. Assumes the
	write guards have already run.

	**Atomic and fail-closed.** The whole merge is one transaction: backfill →
	repoint hard refs → repoint soft refs → reconcile tags → delete the loser →
	commit. If anything raises, the transaction is rolled back and nothing is
	changed or deleted — a partial merge (loser deleted while a reference still
	points at it) is never committed. The loser is deleted **without** ``force``,
	so Frappe's own ``LinkExistsError`` is a final backstop: if any hard reference
	was somehow missed, the delete fails and the whole merge rolls back rather
	than orphaning a link.
	"""
	survivor_doc = frappe.get_doc(doctype, survivor)
	loser_doc = frappe.get_doc(doctype, loser)

	# Capture the manual-review flags BEFORE any mutation, so the logged flags
	# match exactly what the preview showed (after repointing, the loser's own
	# soft refs read as the survivor's and the exclusion would stop matching).
	manual_review = _manual_review_flags(doctype, loser)

	try:
		# 1) Backfill the survivor's blanks + append the loser's child rows.
		n_fields, n_rows = _apply_backfill(survivor_doc, loser_doc)
		if n_fields or n_rows:
			survivor_doc.save(ignore_permissions=True)

		# 2) Repoint hard references (Link / Dynamic Link / child / Single).
		hard_count, touched = _repoint_hard_references(doctype, survivor, loser, hard_refs)

		# 3) Repoint soft references (Files / Comments / ToDos / Communications / …).
		soft_count = _repoint_soft_references(doctype, survivor, loser, soft_rows)

		# 3b) Union the loser's tags into the survivor's denormalized _user_tags
		#     (the Tag Link rows moved above, but the sidebar reads _user_tags).
		_merge_user_tags(doctype, survivor, loser_doc)

		# 4) Delete the loser. No force: if a hard reference was missed, Frappe's
		#    LinkExistsError fires and the whole merge rolls back (fail-closed).
		frappe.delete_doc(doctype, loser, ignore_permissions=True)

		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "Document Merge failed")
		raise

	# Scope the cache flush to the doctypes actually touched (not the whole site).
	for dt in touched | {doctype}:
		frappe.clear_cache(doctype=dt)

	# 5) Record the irreversible operation (after commit; never blocks the merge).
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


def _repoint_one(table, name, name_field, value, doctype_field=None, doctype_value=None):
	"""Repoint one reference row low-level (bypasses validation so submitted
	referrers repoint too). Returns ``True`` if repointed, ``False`` if the row was
	a genuine duplicate of one the survivor already owns and was dropped instead.

	A duplicate-key violation (the survivor already has the equivalent unique row,
	e.g. a Document Follow / Tag Link the survivor already carries) is the ONLY
	error treated as "drop the loser's redundant row". Every other error is
	re-raised — the caller fails the whole merge closed rather than guessing.
	A per-row savepoint keeps a recoverable duplicate error from poisoning the
	surrounding transaction.
	"""
	savepoint = "ee_merge_repoint"
	frappe.db.savepoint(savepoint)
	try:
		frappe.db.set_value(table, name, name_field, value, update_modified=False)
		if doctype_field:
			frappe.db.set_value(table, name, doctype_field, doctype_value, update_modified=False)
		return True
	except Exception as exc:
		frappe.db.rollback(save_point=savepoint)
		if _is_duplicate_entry_error(exc):
			frappe.db.delete(table, {"name": name})
			return False
		raise


def _repoint_hard_references(doctype, survivor, loser, refs):
	"""Repoint every discovered Link/Dynamic Link/child/Single reference from loser
	to survivor. Returns (count, touched_doctypes).

	Defaults to *repointing* every row; a row is only dropped on a genuine
	duplicate-key collision (via :func:`_repoint_one`). A referrer that *is* the
	survivor would become a self-link, so it is nulled instead. Any unexpected
	error propagates (the merge is atomic and fails closed).
	"""
	count = 0
	touched = set()
	for ref in refs:
		if ref.get("is_single"):
			frappe.db.set_single_value(ref["doctype"], ref["fieldname"], survivor)
			if ref.get("is_dynamic") and ref.get("doctype_field"):
				frappe.db.set_single_value(ref["doctype"], ref["doctype_field"], doctype)
			touched.add(ref["doctype"])
			count += 1

		elif ref.get("is_child"):
			parent_dt, parent_name = ref["doctype"], ref["name"]
			child_dt, child_name = ref["child_doctype"], ref["child_name"]
			# Skip the loser's own rows (already filtered in discovery, belt-and-braces).
			if parent_dt == doctype and parent_name == loser:
				continue
			_repoint_one(
				child_dt,
				child_name,
				ref["fieldname"],
				survivor,
				ref["doctype_field"] if ref.get("is_dynamic") else None,
				doctype,
			)
			touched.add(parent_dt)
			count += 1

		else:
			ref_dt, ref_name = ref["doctype"], ref["name"]
			# A reference on the survivor itself would become a self-link; null it.
			if ref_dt == doctype and ref_name == survivor:
				frappe.db.set_value(ref_dt, ref_name, ref["fieldname"], None, update_modified=False)
				if ref.get("is_dynamic") and ref.get("doctype_field"):
					frappe.db.set_value(ref_dt, ref_name, ref["doctype_field"], None, update_modified=False)
			else:
				_repoint_one(
					ref_dt,
					ref_name,
					ref["fieldname"],
					survivor,
					ref["doctype_field"] if ref.get("is_dynamic") else None,
					doctype,
				)
			touched.add(ref_dt)
			count += 1

	return count, touched


def _repoint_soft_references(doctype, survivor, loser, soft_rows):
	"""Repoint the framework soft-reference rows from loser to survivor. A row that
	would duplicate one the survivor already owns (e.g. a Tag Link / Document Follow
	already present) is dropped; any other error propagates (fail-closed). The
	companion ``*_doctype`` column never changes (same-doctype merge)."""
	count = 0
	for ref in soft_rows:
		_repoint_one(ref["table"], ref["row"], ref["name_col"], survivor)
		count += 1
	return count


def _merge_user_tags(doctype, survivor, loser_doc):
	"""Union the loser's tags into the survivor's denormalized ``_user_tags`` column
	(the desk tag sidebar and tag filters read this column, not raw Tag Link rows;
	the Tag Link rows themselves are moved by the soft-reference pass)."""
	loser_tags = [t for t in (loser_doc.get("_user_tags") or "").split(",") if t]
	if not loser_tags:
		return
	survivor_tags = frappe.db.get_value(doctype, survivor, "_user_tags") or ""
	tags = {t for t in survivor_tags.split(",") if t}
	tags.update(loser_tags)
	new_value = "," + ",".join(sorted(tags)) if tags else ""
	frappe.db.set_value(doctype, survivor, "_user_tags", new_value, update_modified=False)


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
