"""Remediation for the importer's default-group sweep (v1.496.0 / v1.498.0): put each
QBO-linked Supplier / Customer back in the group or territory it held before the sweep, apply
the hand-curated corrections from the 2026-09-22 audit, and otherwise leave the field **blank**.

Background
----------
QuickBooks has no supplier group, customer group or territory, so ``_map_supplier`` /
``_map_customer`` used to default them. Until v1.496.0 the default was
``frappe.db.get_value(doctype, {"is_group": 0}, "name")`` -- "any leaf group" -- and on
Frappe v16 a dict-filtered ``get_value`` with no ``order_by`` sorts by ``creation`` DESC, so
it answered "the leaf somebody created most recently". The in-place update path re-applied
every mapped value on every re-sync (``apply_values``), so each new leaf anyone added became
the value of every QBO-linked party on the next scheduled run. Verified on prod 2026-09-22:

* **Supplier.supplier_group** -- all 911 QBO-linked Suppliers moved en bloc five times:
  Staffing (the 2026-06-18 vendor import) -> Event Decor (07-21) -> Encapsulant (08-19) ->
  Labels (09-09) -> Garbage & Junk Removal (09-16, 906 rows). Fixed by the deploy of v1.496.0.
* **Customer.customer_group** -- 466 Customers in "Government", the newest Customer Group leaf
  (all four leaves were seeded in the same second on 2025-07-08, Government last). Every one
  is QBO-linked; one had a real group before (Commercial).
* **Customer.territory** -- the 2026-06-18 import filed 355 Customers under "Asia" (the newest
  Territory leaf then), overwriting 63 real territories (45 Utah among them). A manual clear on
  06-23 blanked the Asia rows, real values included, and the 08-19 sync re-filed 178 under
  "United States of America" (created 08-05, newest leaf again).

The forward fix (``core/mapping.py``): the importer no longer defaults any of the three --
``DEFAULT_PARTY_GROUP`` is ``None`` -- and the update path drops them whatever the record
holds, so they are ERPNext's from the moment a party exists. That is Nik's call of
2026-09-22: **a wrong group is worse than no group.** This module restores what the sweep
overwrote, under the same rule.

What it does
------------
``restore_party_groups`` -- for each ``(doctype, field)`` in ``SWEEP_LANDING_GROUPS``, the
candidates are the QBO-linked records whose current value is a landing value, **plus** any
record whose current value is blank but whose Version history shows a sweep touch (the
territory case: a person cleared the swept value and the real one went with it). A party
without a mapping row that sits in a landing value was never written by the sync and is left
alone (counted, so the report shows it).

For each candidate it walks the record's Version rows (``ref_doctype`` = the doctype,
``docname`` = the record) in creation order, reading the ``changed`` entries of the ``data``
JSON -- ``[fieldname, old, new]`` per ``frappe.core.doctype.version.get_diff`` -- for the
field. The first change whose NEW value is a landing value is the sweep's first touch, and
its OLD value is the pre-sweep value:

* a real, still-existing leaf that is not itself a landing value -> **restored**;
* empty, a landing value (the record was created by an earlier sweep) or since deleted ->
  **cleared** (the field is set to NULL: no group rather than a guessed one);
* no such change anywhere in its history -> **untouched and listed**, unless
  ``include_sync_created=True`` and the mapping says the import created the record
  (``match_status = Created``), or ``clear_no_history=True`` (every linked no-history row --
  the Customer pass, where every candidate is QBO-linked and the landing value is the sync's
  default) -> **cleared**;
* named in the doctype's curated corrections for that field -> **protected**: the curated
  pass owns it. A person's deliberate filing under a landing value (no mapping) is never
  touched, and a record whose current value is neither blank nor a landing value (a person
  re-set it after the sweep) is left as it is.

``apply_curated_party_values`` -- the audit's hand-curated corrections, one JSON file per
doctype next to this module (``CURATED_FILES``): each entry names a record and the value(s) it
belongs with (``null`` = clear) plus the basis for the call -- a duplicate record of the same
company already carrying the value, the trade or institution in the name, the record's own
notes, a group created for that record minutes before a sweep moved it, or a person's
deliberate choice the history walk would otherwise read as a sweep. An entry whose record or
leaf no longer exists is skipped and reported, never guessed; the curated value wins, so this
runs second (and the sweep pass skips those names).

Writes go through ``frappe.db.set_value`` (no doc hooks: a Supplier save fires contact sync,
Drive filing and the like, 900 times over), but a Supplier's two denormalized fields --
``custom_supplier_groups_search`` and ``custom_additional_supplier_groups_list``, which
``supplier_query.sync_supplier_groups`` maintains on ``validate`` and list-view search reads
-- are recomputed with that same function and written alongside, so a bare column write does
not leave the search index pointing at the old group.

Safety
------
* **Dry-run by default** (``apply=False`` writes nothing; it reports what it would do).
* **Idempotent** -- re-runnable; a restored or cleared record is no longer a candidate, and a
  curated entry already holding its value is skipped.
* **Batched + committed** so a mid-run failure keeps completed work.
* **Per-record guarded** -- one bad row logs an Error and is skipped, never aborting.
* **Field-only** -- it changes the Link (and the Supplier search fields derived from it),
  never the docname or any other field, so nothing cascades.

Each side runs once, automatically, from its patch (``bench migrate`` on ``main`` is the
deploy): ``patches/restore_supplier_groups_after_qbo_sweep`` (v1.496.0) and
``patches/restore_customer_groups_after_qbo_sweep`` (v1.498.0). The dry run stays useful for
a re-check::

    bench --site <site> execute \\
      erpnext_enhancements.quickbooks_online.core.party_group_remediation.restore_party_groups \\
      --kwargs "{'doctype': 'Customer', 'clear_no_history': True}"
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path

import frappe

from erpnext_enhancements.supplier_query import sync_supplier_groups

QBO_COMMIT_EVERY = 100
_VERSION_CHUNK = 200

# The values each sweep landed in, per doctype and field, oldest first. Verified on prod
# 2026-09-22 from tabVersion.
SWEEP_LANDING_GROUPS = {
	"Supplier": {
		"supplier_group": ("Staffing", "Event Decor", "Encapsulant", "Labels", "Garbage & Junk Removal"),
	},
	"Customer": {
		"customer_group": ("Government",),
		"territory": ("Asia", "United States of America"),
	},
}
GROUP_DOCTYPE = {
	"supplier_group": "Supplier Group",
	"customer_group": "Customer Group",
	"territory": "Territory",
}

# The Supplier fields sync_supplier_groups derives from the group; written alongside it.
_SUPPLIER_DERIVED_FIELDS = ("custom_supplier_groups_search", "custom_additional_supplier_groups_list")

# The audit's hand-curated corrections, one file per doctype (see the module docstring), and
# the JSON key -> fieldname each file's entries may carry. A Supplier entry says ``group``; a
# Customer entry says ``customer_group`` and / or ``territory``.
CURATED_FILES = {
	"Supplier": Path(__file__).with_name("supplier_group_corrections.json"),
	"Customer": Path(__file__).with_name("customer_corrections.json"),
}
CURATED_VALUE_KEYS = {
	"Supplier": {"group": "supplier_group"},
	"Customer": {"customer_group": "customer_group", "territory": "territory"},
}
CURATED_SUPPLIER_GROUPS_FILE = CURATED_FILES["Supplier"]
CURATED_BASES = ("twin", "name", "notes", "intent", "audit", "person")


def pre_sweep_group(changes: Iterable, fieldname: str, landing_groups: Iterable[str]) -> tuple[bool, str]:
	"""Find the sweep's first touch in a record's Version history.

	``changes`` is every ``changed`` entry (``[fieldname, old, new]``) of the record's Version
	rows, in creation order. Returns ``(True, old_value)`` for the first entry on ``fieldname``
	whose new value is a landing value -- ``old_value`` is what the record held before the
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

	The pre-sweep value is restored when it is a real leaf that still exists and is not
	itself a landing value; otherwise the field is cleared -- ``""`` (written as NULL), the
	honest answer when the history holds no real value. The outcome is ``"restored"`` or
	``"cleared"`` for the report.
	"""
	if old_value and old_value not in set(landing_groups) and group_exists(old_value):
		return old_value, "restored"
	return "", "cleared"


def load_curated_corrections(doctype: str, path: Path | None = None) -> dict[str, dict]:
	"""The raw, validated ``corrections`` object of ``doctype``'s corrections file.

	Every entry is an object carrying ``basis`` (one of ``CURATED_BASES``) plus at least one
	of the doctype's value keys (``CURATED_VALUE_KEYS``), each a non-empty string or ``null``,
	and nothing else -- so a typo in the JSON fails a test, not a migrate.
	"""
	value_keys = CURATED_VALUE_KEYS[doctype]
	raw = json.loads((path or CURATED_FILES[doctype]).read_text(encoding="utf-8"))
	corrections = raw.get("corrections")
	if not isinstance(corrections, dict):
		raise ValueError(f"{doctype} corrections: 'corrections' must be an object")
	for name, entry in corrections.items():
		if not isinstance(name, str) or not name.strip():
			raise ValueError(f"{doctype} corrections: blank record name {name!r}")
		if not isinstance(entry, dict) or "basis" not in entry:
			raise ValueError(f"{doctype} corrections: {name!r} must be an object with a basis")
		if entry["basis"] not in CURATED_BASES:
			raise ValueError(f"{doctype} corrections: {name!r} basis {entry['basis']!r} unknown")
		keys = set(entry) - {"basis"}
		if not keys or not keys <= set(value_keys):
			raise ValueError(
				f"{doctype} corrections: {name!r} must carry one or more of {sorted(value_keys)}"
			)
		for key in keys:
			value = entry[key]
			if value is not None and (not isinstance(value, str) or not value.strip()):
				raise ValueError(f"{doctype} corrections: {name!r} {key} must be a name or null")
	return corrections


def load_curated_supplier_groups(path: Path | None = None) -> dict[str, dict]:
	"""The Supplier corrections, raw: ``{supplier: {"group": name-or-None, "basis": ...}}``."""
	return load_curated_corrections("Supplier", path)


def normalize_curated(doctype: str, corrections: dict) -> dict[str, dict]:
	"""``{name: {"values": {fieldname: target-or-""}, "basis": ...}}`` from a raw corrections object."""
	value_keys = CURATED_VALUE_KEYS[doctype]
	out = {}
	for name, entry in corrections.items():
		values = {value_keys[key]: (entry[key] or "") for key in entry if key in value_keys}
		out[name] = {"values": values, "basis": entry.get("basis")}
	return out


def curated_names_by_field(doctype: str, corrections: dict | None = None) -> dict[str, set]:
	"""``{fieldname: {names the curated pass owns}}`` -- what the sweep pass must not touch."""
	try:
		raw = corrections if corrections is not None else load_curated_corrections(doctype)
	except (OSError, ValueError):
		return {}
	protected: dict[str, set] = {}
	for name, entry in normalize_curated(doctype, raw).items():
		for fieldname in entry["values"]:
			protected.setdefault(fieldname, set()).add(name)
	return protected


def restore_party_groups(
	apply=False,
	doctype=None,
	limit=None,
	verbose=True,
	include_sync_created=False,
	clear_no_history=False,
	protect_curated=True,
):
	"""Restore or clear the swept group / territory on every affected Supplier / Customer.

	Args:
		apply: When False (default) this is a DRY RUN -- it computes and reports the plan
			for every affected record but writes nothing. Pass True to write.
		doctype: ``"Supplier"`` or ``"Customer"`` to run one side only; both by default.
		limit: Optionally process at most this many records per field (handy for a first
			sandbox run, e.g. ``limit=5``).
		verbose: Print a per-record before/after line in addition to the summary.
		include_sync_created: Also clear a record with NO sweep change in its history when
			its mapping says the import created it (``Created``) -- it was inserted straight
			into the landing value, which no Version row records.
		clear_no_history: Clear EVERY QBO-linked no-history record sitting in a landing
			value. Right when the landing value is the sync's default and every candidate is
			linked (the Customer pass); off by default so such rows are listed for a person.
		protect_curated: Skip the names the doctype's curated corrections own for that field
			(default): the curated pass writes them.

	Returns:
		dict: ``{"mode", "doctypes": {doctype: {fieldname: section}}}`` -- per field the counts
		per outcome, a ``changes`` list of ``{name, before, after, pre_sweep, outcome}`` and a
		``no_history_names`` list -- also printed for ``bench execute`` / the migrate log.
	"""
	if apply and not (frappe.flags.in_patch or frappe.flags.in_migrate):
		# Writing path is privileged; the dry run is safe for anyone to preview. A patch
		# runs as Administrator inside migrate, where only_for is redundant.
		frappe.only_for("System Manager")

	doctypes = [doctype] if doctype else list(SWEEP_LANDING_GROUPS)
	report = {"mode": "apply" if apply else "dry-run", "doctypes": {}}
	for party_doctype in doctypes:
		if party_doctype not in SWEEP_LANDING_GROUPS:
			frappe.throw(f"Unknown doctype {party_doctype!r}; expected one of {list(SWEEP_LANDING_GROUPS)}")
		protected = curated_names_by_field(party_doctype) if protect_curated else {}
		report["doctypes"][party_doctype] = {}
		for fieldname, landing in SWEEP_LANDING_GROUPS[party_doctype].items():
			report["doctypes"][party_doctype][fieldname] = _restore_field(
				party_doctype,
				fieldname,
				landing,
				apply=apply,
				limit=limit,
				verbose=verbose,
				include_sync_created=include_sync_created,
				clear_no_history=clear_no_history,
				protected=protected.get(fieldname, set()),
			)

	if apply:
		frappe.db.commit()

	_print_summary(report)
	return report


def apply_curated_party_values(doctype: str, apply=False, verbose=True, corrections: dict | None = None):
	"""Apply the audit's hand-curated corrections for ``doctype``.

	Args:
		doctype: ``"Supplier"`` or ``"Customer"``.
		apply: When False (default) this is a DRY RUN. Pass True to write.
		verbose: Print a per-record before/after line in addition to the summary.
		corrections: A raw corrections object (the file's ``corrections`` value); loaded from
			``CURATED_FILES[doctype]`` when omitted (tests pass one in).

	Returns:
		dict: ``{entries, applied, unchanged, missing_record, missing_group, errors, changes,
		skipped}`` -- ``applied`` counts records with at least one write, ``skipped`` lists
		every entry not applied and why.
	"""
	if apply and not (frappe.flags.in_patch or frappe.flags.in_migrate):
		frappe.only_for("System Manager")
	if corrections is None:
		corrections = load_curated_corrections(doctype)
	entries = normalize_curated(doctype, corrections)

	report = {
		"mode": "apply" if apply else "dry-run",
		"doctype": doctype,
		"entries": len(entries),
		"applied": 0,
		"unchanged": 0,
		"missing_record": 0,
		"missing_group": 0,
		"errors": 0,
		"changes": [],
		"skipped": [],
	}
	exists_cache = {fieldname: _existence_cache(GROUP_DOCTYPE[fieldname]) for fieldname in GROUP_DOCTYPE}
	for index, (name, entry) in enumerate(entries.items(), start=1):
		try:
			if not frappe.db.exists(doctype, name):
				report["missing_record"] += 1
				report["skipped"].append({"name": name, "reason": f"no such {doctype}"})
				continue
			missing = [
				target
				for fieldname, target in entry["values"].items()
				if target and not exists_cache[fieldname](target)
			]
			if missing:
				report["missing_group"] += 1
				report["skipped"].append({"name": name, "reason": f"no such leaf {missing[0]!r}"})
				continue
			wrote = False
			for fieldname, target in entry["values"].items():
				current = frappe.db.get_value(doctype, name, fieldname) or ""
				if current == target:
					continue
				wrote = True
				report["changes"].append(
					{
						"name": name,
						"field": fieldname,
						"before": current,
						"after": target,
						"basis": entry["basis"],
					}
				)
				if verbose:
					print(
						f"  [{index}/{len(entries)}] {doctype} {name}.{fieldname}: {current!r} -> {target!r} ({entry['basis']})"
					)
				if apply:
					_write_group(doctype, name, fieldname, target)
			if wrote:
				report["applied"] += 1
				if apply and index % QBO_COMMIT_EVERY == 0:
					frappe.db.commit()
			else:
				report["unchanged"] += 1
		except Exception:  # one bad row must never abort the batch
			report["errors"] += 1
			frappe.log_error(
				f"Curated {doctype} correction failed for {name!r}\n{frappe.get_traceback()}",
				"QBO Party Group Remediation Error",
			)
	if apply:
		frappe.db.commit()
	print(
		f"\n=== curated {doctype} corrections ({report['mode']}) === "
		f"entries {report['entries']}, applied {report['applied']}, unchanged {report['unchanged']}, "
		f"missing record {report['missing_record']}, missing leaf {report['missing_group']}, "
		f"errors {report['errors']}"
	)
	for skipped in report["skipped"]:
		print(f"    skipped {skipped['name']!r}: {skipped['reason']}")
	return report


def apply_curated_supplier_groups(apply=False, verbose=True, corrections: dict | None = None):
	"""The Supplier side of ``apply_curated_party_values`` (what v1.496.0's patch calls)."""
	return apply_curated_party_values("Supplier", apply=apply, verbose=verbose, corrections=corrections)


def _restore_field(
	party_doctype,
	fieldname,
	landing,
	*,
	apply,
	limit,
	verbose,
	include_sync_created,
	clear_no_history,
	protected,
):
	group_doctype = GROUP_DOCTYPE[fieldname]

	changes_by_name = _version_changes(party_doctype, fieldname)
	rows = _swept_rows(party_doctype, fieldname, landing)
	seen = {row.name for row in rows}
	# A record a person blanked after the sweep took the real value with it (the territory
	# clear of 2026-06-23): its history still shows the sweep touch, so it is a candidate too.
	lost = [
		name
		for name, changes in changes_by_name.items()
		if name not in seen and pre_sweep_group(changes, fieldname, landing)[0]
	]
	for name, current in _current_values(party_doctype, fieldname, lost):
		if (current or "") == "":
			rows.append(frappe._dict(name=name, current_group=current, sync_created=0))
	rows.sort(key=lambda row: row.name)
	if limit:
		rows = rows[:limit]

	section = {
		"landing_groups": list(landing),
		"candidates": len(rows),
		"unlinked_left_alone": _unlinked_count(party_doctype, fieldname, landing),
		"restored": 0,
		"cleared": 0,
		"cleared_no_history": 0,
		"protected_curated": 0,
		"no_history": 0,
		"unchanged": 0,
		"errors": 0,
		"changes": [],
		"no_history_names": [],
	}
	if not rows:
		return section

	group_exists = _existence_cache(group_doctype)

	for index, row in enumerate(rows, start=1):
		try:
			if row.name in protected:
				section["protected_curated"] += 1
				continue
			found, old_value = pre_sweep_group(changes_by_name.get(row.name, []), fieldname, landing)
			if found:
				target, outcome = restoration_target(old_value, landing, group_exists)
			elif clear_no_history or (include_sync_created and row.sync_created):
				target, outcome = "", "cleared_no_history"
			else:
				section["no_history"] += 1
				section["no_history_names"].append(row.name)
				continue
			if target == (row.current_group or ""):
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
					f"  [{index}/{len(rows)}] {party_doctype} {row.name}.{fieldname}: "
					f"{row.current_group!r} -> {target!r} ({outcome})"
				)
			if apply:
				_write_group(party_doctype, row.name, fieldname, target)
				if index % QBO_COMMIT_EVERY == 0:
					frappe.db.commit()
		except Exception:  # one bad row must never abort the batch
			section["errors"] += 1
			frappe.log_error(
				f"Party group remediation failed for {party_doctype} {row.get('name')}.{fieldname}\n"
				f"{frappe.get_traceback()}",
				"QBO Party Group Remediation Error",
			)
	return section


def _swept_rows(party_doctype, fieldname, landing):
	"""QBO-linked records currently in a landing value, with whether the import created them.

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
		where p.`{fieldname}` in %(landing)s
		  and exists(
			select 1 from `tabQuickBooks Sync Mapping` m
			where m.erpnext_doctype = %(dt)s and m.erpnext_name = p.name
		  )
		order by p.name
		""",
		{"dt": party_doctype, "landing": tuple(landing)},
		as_dict=True,
	)


def _unlinked_count(party_doctype, fieldname, landing):
	"""Records in a landing value with no mapping row -- never written by the sync, left alone."""
	return frappe.db.sql(
		f"""
		select count(*) from `tab{party_doctype}` p
		where p.`{fieldname}` in %(landing)s
		  and not exists(
			select 1 from `tabQuickBooks Sync Mapping` m
			where m.erpnext_doctype = %(dt)s and m.erpnext_name = p.name
		  )
		""",
		{"dt": party_doctype, "landing": tuple(landing)},
	)[0][0]


def _current_values(party_doctype, fieldname, names):
	"""``[(name, current value)]`` for ``names`` that still exist, in chunks."""
	out = []
	for start in range(0, len(names), _VERSION_CHUNK):
		chunk = names[start : start + _VERSION_CHUNK]
		out.extend(
			frappe.db.sql(
				f"select name, `{fieldname}` from `tab{party_doctype}` where name in %(names)s",
				{"names": chunk},
				as_list=True,
			)
		)
	return out


def _version_changes(party_doctype, fieldname):
	"""``{docname: [[fieldname, old, new], ...]}`` for every record with a change on ``fieldname``,
	in Version creation order."""
	changes_by_name: dict[str, list] = {}
	rows = frappe.db.sql(
		"""
		select docname, data from `tabVersion`
		where ref_doctype = %(dt)s and data like %(needle)s
		order by creation asc, name asc
		""",
		{"dt": party_doctype, "needle": f'%"{fieldname}"%'},
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
	"""``group_exists(name)`` memoized per run -- the same handful of leaves recur 900 times."""
	cache: dict[str, bool] = {}

	def group_exists(name):
		if name not in cache:
			cache[name] = bool(frappe.db.exists(group_doctype, name))
		return cache[name]

	return group_exists


def _write_group(party_doctype, name, fieldname, target):
	"""Write the field (``""`` -> NULL) without doc hooks; keep a Supplier's derived search fields in step."""
	values = {fieldname: target or None}
	if party_doctype == "Supplier" and fieldname == "supplier_group":
		# The two denormalized fields are what list-view search reads; recompute them the
		# way the validate hook does so a column write does not leave them stale. Guarded
		# on the columns: they are Custom Fields created at after_migrate.
		derived = [field for field in _SUPPLIER_DERIVED_FIELDS if frappe.db.has_column("Supplier", field)]
		if derived:
			doc = frappe.get_doc("Supplier", name)
			doc.supplier_group = target or None
			sync_supplier_groups(doc)
			for field in derived:
				values[field] = doc.get(field) or ""
	frappe.db.set_value(party_doctype, name, values, update_modified=False)


def _print_summary(report):
	"""Print a human-readable summary for ``bench execute`` / the deploy log."""
	print(f"\n=== QBO party-group remediation ({report['mode']}) ===")
	for party_doctype, fields in report["doctypes"].items():
		for fieldname, section in fields.items():
			print(f"  {party_doctype}.{fieldname} (landing: {', '.join(section['landing_groups'])})")
			for key in (
				"candidates",
				"restored",
				"cleared",
				"cleared_no_history",
				"protected_curated",
				"no_history",
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
