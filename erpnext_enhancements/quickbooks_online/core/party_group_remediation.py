"""One-off remediation: put each QBO-linked Supplier / Customer back in the group it held
before the importer's default-group sweep (v1.496.0).

Background
----------
QuickBooks has no supplier group, customer group or territory, so ``_map_supplier`` /
``_map_customer`` default them. Until v1.496.0 the default was ``frappe.db.get_value(doctype,
{"is_group": 0}, "name")`` -- "any leaf group" -- and on Frappe v16 a dict-filtered
``get_value`` with no ``order_by`` sorts by ``creation`` DESC, so it answered "the leaf
somebody created most recently". The in-place update path re-applied every mapped value on
every re-sync (``apply_values``), so each new Supplier Group anyone added became the group of
every QBO-linked Supplier on the next scheduled run. Verified on prod 2026-09-22: all 911
QBO-linked Suppliers moved en bloc five times -- Staffing (the 2026-06-18 vendor import) ->
Event Decor (07-21) -> Encapsulant (08-19) -> Labels (09-09) -> Garbage & Junk Removal
(09-16, 906 rows, the scheduled sync running as Administrator) -- and 466 Customers sit in
"Government", the newest Customer Group leaf, for the same reason. ``customer_group`` is
hidden on the Customer form (Property Setter), which is why nobody saw it.

The forward fix (``core/mapping.py``: ``_default_group`` returns the seeded
``DEFAULT_PARTY_GROUP`` by name; ``_protect_existing_party_groups`` stops the update path
overwriting a group a person set; the fields are excluded from the ``owned_fields`` snapshot)
prevents recurrence. This module restores what the sweep overwrote.

What it does
------------
Scope: Suppliers currently in ``Garbage & Junk Removal`` and Customers currently in
``Government`` (the last landing group of each sweep), restricted to records with a
``QuickBooks Sync Mapping`` row -- a party without one was never written by the sync and is
left alone (counted, so the report shows it).

For each, it walks the record's Version rows (``ref_doctype`` = the doctype, ``docname`` = the
record) in creation order, reading the ``changed`` entries of the ``data`` JSON --
``[fieldname, old, new]`` per ``frappe.core.doctype.version.get_diff`` -- for the group
field. The first change whose NEW value is one of the sweep's landing groups is the sweep's
first touch, and its OLD value is the pre-sweep group:

* a real, still-existing group that is not itself a landing group -> **restored**;
* empty, a landing group (the record was created by an earlier sweep) or since deleted ->
  **``Uncategorized``** (``DEFAULT_PARTY_GROUP``, seeded by ``patches/seed_qbo_uncategorized_groups``);
* no such change anywhere in its history -> **untouched and listed**. The record was either
  created straight into the landing group by the sync (no Version row is written on insert)
  or filed there by a person; the history cannot tell them apart, so the tool does not guess.
  ``include_sync_created=True`` also files the first kind -- those whose mapping says the
  import created them (``match_status = Created``) -- into ``Uncategorized``.

Writes go through ``frappe.db.set_value`` (no doc hooks: a Supplier save fires contact
sync, Drive filing and the like, 900 times over), but a Supplier's two denormalized fields
-- ``custom_supplier_groups_search`` and ``custom_additional_supplier_groups_list``, which
``supplier_query.sync_supplier_groups`` maintains on ``validate`` and list-view search reads
-- are recomputed with that same function and written alongside, so a bare column write does
not leave the search index pointing at the old group.

Out of scope (reported, never touched): a group a person set *deliberately* to what later
became a landing group ("Labels" on 09-08, swept on 09-09) reads as a sweep and is defaulted;
the dry run lists every ``pre_sweep`` value so such rows can be spotted before ``apply``.

Safety
------
* **Dry-run by default** (``apply=False`` writes nothing; it reports what it would do).
* **Idempotent** -- re-runnable; a restored record is no longer in the landing group and
  drops out of scope.
* **Batched + committed** so a mid-run failure keeps completed work.
* **Per-record guarded** -- one bad row logs an Error and is skipped, never aborting.
* **Group-only** -- it changes the group Link (and the Supplier search fields derived from
  it), never the docname or any other field, so nothing cascades.
* **Not** wired to migrate/scheduler. Run it manually, **sandbox first**.

Run it::

    # 1) preview (no writes):
    bench --site <site> execute \\
      erpnext_enhancements.quickbooks_online.core.party_group_remediation.restore_party_groups
    # 2) apply (after reviewing the dry-run, on sandbox first):
    bench --site <site> execute \\
      erpnext_enhancements.quickbooks_online.core.party_group_remediation.restore_party_groups \\
      --kwargs "{'apply': True}"
    # one doctype, a first few rows, or the sync-created no-history rows too:
    #   --kwargs "{'doctype': 'Supplier', 'limit': 5}"
    #   --kwargs "{'apply': True, 'include_sync_created': True}"
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable

import frappe

from erpnext_enhancements.quickbooks_online.core.constants import DEFAULT_PARTY_GROUP, PARTY_GROUP_DOCTYPES
from erpnext_enhancements.supplier_query import sync_supplier_groups

QBO_COMMIT_EVERY = 100
_VERSION_CHUNK = 200

# The groups each sweep landed in, oldest first; the last is where the records sit now.
# Verified on prod 2026-09-22 from tabVersion.
SWEEP_LANDING_GROUPS = {
	"Supplier": ("Staffing", "Event Decor", "Encapsulant", "Labels", "Garbage & Junk Removal"),
	"Customer": ("Government",),
}
GROUP_FIELD = {"Supplier": "supplier_group", "Customer": "customer_group"}

# The Supplier fields sync_supplier_groups derives from the group; written alongside it.
_SUPPLIER_DERIVED_FIELDS = ("custom_supplier_groups_search", "custom_additional_supplier_groups_list")


def pre_sweep_group(changes: Iterable, fieldname: str, landing_groups: Iterable[str]) -> tuple[bool, str]:
	"""Find the sweep's first touch in a record's Version history.

	``changes`` is every ``changed`` entry (``[fieldname, old, new]``) of the record's Version
	rows, in creation order. Returns ``(True, old_value)`` for the first entry on ``fieldname``
	whose new value is a landing group -- ``old_value`` is what the record held before the
	sweep -- or ``(False, "")`` when no such change exists (the history does not show the
	sweep, so the caller must not guess).
	"""
	landing = set(landing_groups)
	for entry in changes:
		if not isinstance(entry, list | tuple) or len(entry) < 3 or entry[0] != fieldname:
			continue
		if (entry[2] or "") in landing:
			return True, (entry[1] or "")
	return False, ""


def restoration_target(
	old_value: str, landing_groups: Iterable[str], group_exists: Callable[[str], bool]
) -> tuple[str, str]:
	"""Decide what a swept record goes back to: ``(target, outcome)``.

	The pre-sweep value is restored when it is a real group that still exists and is not
	itself a landing group; otherwise the record is filed in ``DEFAULT_PARTY_GROUP``. The
	outcome is ``"restored"`` or ``"defaulted"`` for the report.
	"""
	if old_value and old_value not in set(landing_groups) and group_exists(old_value):
		return old_value, "restored"
	return DEFAULT_PARTY_GROUP, "defaulted"


def restore_party_groups(apply=False, doctype=None, limit=None, verbose=True, include_sync_created=False):
	"""Restore the pre-sweep group on every QBO-linked Supplier / Customer the sweep moved.

	Args:
		apply: When False (default) this is a DRY RUN -- it computes and reports the plan
			for every affected record but writes nothing. Pass True to write.
		doctype: ``"Supplier"`` or ``"Customer"`` to run one side only; both by default.
		limit: Optionally process at most this many records per doctype (handy for a first
			sandbox run, e.g. ``limit=5``).
		verbose: Print a per-record before/after line in addition to the summary.
		include_sync_created: Also file a record with NO sweep change in its history into
			``Uncategorized`` when its mapping says the import created it (``Created``) --
			it was inserted straight into the landing group, which no Version row records.
			Off by default: such rows are listed for a person to decide.

	Returns:
		dict: A summary report per doctype (counts per outcome, a ``changes`` list of
		``{name, before, after, pre_sweep, outcome}`` and a ``no_history`` list), also
		printed for ``bench execute`` visibility.
	"""
	if apply:
		# Writing path is privileged; the dry run is safe for anyone to preview.
		frappe.only_for("System Manager")

	doctypes = [doctype] if doctype else list(SWEEP_LANDING_GROUPS)
	report = {"mode": "apply" if apply else "dry-run", "doctypes": {}}
	for party_doctype in doctypes:
		if party_doctype not in SWEEP_LANDING_GROUPS:
			frappe.throw(f"Unknown doctype {party_doctype!r}; expected one of {list(SWEEP_LANDING_GROUPS)}")
		report["doctypes"][party_doctype] = _restore_doctype(
			party_doctype,
			apply=apply,
			limit=limit,
			verbose=verbose,
			include_sync_created=include_sync_created,
		)

	if apply:
		frappe.db.commit()

	_print_summary(report)
	return report


def _restore_doctype(party_doctype, *, apply, limit, verbose, include_sync_created):
	fieldname = GROUP_FIELD[party_doctype]
	group_doctype = PARTY_GROUP_DOCTYPES[fieldname][0]
	landing_groups = SWEEP_LANDING_GROUPS[party_doctype]
	current_landing = landing_groups[-1]

	rows = _swept_rows(party_doctype, fieldname, current_landing)
	if limit:
		rows = rows[:limit]

	section = {
		"landing_group": current_landing,
		"candidates": len(rows),
		"unlinked_left_alone": _unlinked_count(party_doctype, fieldname, current_landing),
		"restored": 0,
		"defaulted": 0,
		"defaulted_sync_created": 0,
		"no_history": 0,
		"default_missing": 0,
		"unchanged": 0,
		"errors": 0,
		"changes": [],
		"no_history_names": [],
	}
	if not rows:
		return section

	default_exists = bool(frappe.db.exists(group_doctype, DEFAULT_PARTY_GROUP))
	changes_by_name = _version_changes(party_doctype, [row.name for row in rows], fieldname)
	group_exists = _existence_cache(group_doctype)

	for index, row in enumerate(rows, start=1):
		try:
			found, old_value = pre_sweep_group(changes_by_name.get(row.name, []), fieldname, landing_groups)
			if found:
				target, outcome = restoration_target(old_value, landing_groups, group_exists)
			elif include_sync_created and row.sync_created:
				target, outcome = DEFAULT_PARTY_GROUP, "defaulted_sync_created"
			else:
				section["no_history"] += 1
				section["no_history_names"].append(row.name)
				continue
			if target == DEFAULT_PARTY_GROUP and not default_exists:
				# The seed patch has not run here; writing a Link to a missing record would
				# leave the row unsaveable. Report and skip rather than guess another group.
				section["default_missing"] += 1
				continue
			if target == row.current_group:
				section["unchanged"] += 1
				continue
			section[outcome] += 1
			section["changes"].append(
				{
					"name": row.name,
					"before": row.current_group,
					"after": target,
					"pre_sweep": old_value,
					"outcome": outcome,
				}
			)
			if verbose:
				print(
					f"  [{index}/{len(rows)}] {party_doctype} {row.name}: {row.current_group!r} -> {target!r} ({outcome})"
				)
			if apply:
				_write_group(party_doctype, row.name, fieldname, target)
				if index % QBO_COMMIT_EVERY == 0:
					frappe.db.commit()
		except Exception:  # one bad row must never abort the batch
			section["errors"] += 1
			frappe.log_error(
				f"Party group remediation failed for {party_doctype} {row.get('name')}\n{frappe.get_traceback()}",
				"QBO Party Group Remediation Error",
			)
	return section


def _swept_rows(party_doctype, fieldname, landing_group):
	"""QBO-linked records currently in the landing group, with whether the import created them.

	``exists`` rather than a join: a Customer can carry several mapping rows (each job the
	remediation consolidated onto its parent points at the parent Customer), and a join
	would list it once per row.
	"""
	return frappe.db.sql(
		f"""
		select
			p.name,
			p.`{fieldname}` as current_group,
			exists(
				select 1 from `tabQuickBooks Sync Mapping` m
				where m.erpnext_doctype = %(dt)s and m.erpnext_name = p.name
				  and m.match_status = 'Created'
			) as sync_created
		from `tab{party_doctype}` p
		where p.`{fieldname}` = %(landing)s
		  and exists(
			select 1 from `tabQuickBooks Sync Mapping` m
			where m.erpnext_doctype = %(dt)s and m.erpnext_name = p.name
		  )
		order by p.name
		""",
		{"dt": party_doctype, "landing": landing_group},
		as_dict=True,
	)


def _unlinked_count(party_doctype, fieldname, landing_group):
	"""Records in the landing group with no mapping row -- never written by the sync, left alone."""
	return frappe.db.sql(
		f"""
		select count(*) from `tab{party_doctype}` p
		where p.`{fieldname}` = %(landing)s
		  and not exists(
			select 1 from `tabQuickBooks Sync Mapping` m
			where m.erpnext_doctype = %(dt)s and m.erpnext_name = p.name
		  )
		""",
		{"dt": party_doctype, "landing": landing_group},
	)[0][0]


def _version_changes(party_doctype, names, fieldname):
	"""``{docname: [[fieldname, old, new], ...]}`` for the group field, in Version creation order."""
	changes_by_name: dict[str, list] = {}
	for start in range(0, len(names), _VERSION_CHUNK):
		chunk = names[start : start + _VERSION_CHUNK]
		rows = frappe.db.sql(
			"""
			select docname, data from `tabVersion`
			where ref_doctype = %(dt)s and docname in %(names)s
			order by creation asc, name asc
			""",
			{"dt": party_doctype, "names": chunk},
			as_dict=True,
		)
		for row in rows:
			try:
				data = json.loads(row.data or "{}")
			except (TypeError, ValueError):
				continue
			for entry in (data.get("changed") or []) if isinstance(data, dict) else []:
				if isinstance(entry, list | tuple) and len(entry) >= 3 and entry[0] == fieldname:
					changes_by_name.setdefault(row.docname, []).append(entry)
	return changes_by_name


def _existence_cache(group_doctype):
	"""``group_exists(name)`` memoized per run -- the same handful of groups recur 900 times."""
	cache: dict[str, bool] = {}

	def group_exists(name):
		if name not in cache:
			cache[name] = bool(frappe.db.exists(group_doctype, name))
		return cache[name]

	return group_exists


def _write_group(party_doctype, name, fieldname, target):
	"""Write the group without doc hooks; keep a Supplier's derived search fields in step."""
	values = {fieldname: target}
	if party_doctype == "Supplier":
		# The two denormalized fields are what list-view search reads; recompute them the
		# way the validate hook does so a column write does not leave them stale. Guarded
		# on the columns: they are Custom Fields created at after_migrate.
		derived = [field for field in _SUPPLIER_DERIVED_FIELDS if frappe.db.has_column("Supplier", field)]
		if derived:
			doc = frappe.get_doc("Supplier", name)
			doc.supplier_group = target
			sync_supplier_groups(doc)
			for field in derived:
				values[field] = doc.get(field) or ""
	frappe.db.set_value(party_doctype, name, values, update_modified=False)


def _print_summary(report):
	"""Print a human-readable summary for ``bench execute``."""
	print(f"\n=== QBO party-group remediation ({report['mode']}) ===")
	for party_doctype, section in report["doctypes"].items():
		print(f"  {party_doctype} (in {section['landing_group']!r})")
		for key in (
			"candidates",
			"restored",
			"defaulted",
			"defaulted_sync_created",
			"no_history",
			"default_missing",
			"unchanged",
			"errors",
			"unlinked_left_alone",
		):
			print(f"    {key:24} {section[key]}")
		if section["no_history_names"]:
			print(
				f"    (no sweep change in history, left alone: {', '.join(section['no_history_names'][:20])}"
				f"{' ...' if len(section['no_history_names']) > 20 else ''})"
			)
	if report["mode"] == "dry-run":
		print("  (dry run -- nothing was written; re-run with apply=True to execute)")
