# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Reads for the Item naming advisor. :mod:`item_naming_rules` holds every judgement.

This module does I/O and nothing else — it decides no rule, and it writes nothing at all.
There is deliberately no ``Item`` doc_event anywhere in this app: the SOP's compliance is
procedural (*"nothing in this schema is enforced by the system"*, §3), and a third of the
live catalogue would fail the comma rule today, so anything that blocked a save would fire
constantly on legitimate edits to records that were already there.

--------------------------------------------------------------------------------------
Why this reads the whole corpus instead of a narrowed query
--------------------------------------------------------------------------------------

The catalogue is a few hundred rows — roughly ten milliseconds — and reading all of them
buys two things a narrowed ``WHERE`` cannot.

**The similarity weighting needs the whole corpus.** Neighbours are scored by inverse
document frequency — a shared ``PVC`` is worth almost nothing because 63 records carry it,
a shared ``VARIONAUT`` is worth a great deal. Computed over a pre-filtered subset, document
frequency is a property of how the filter happened to be written, so the same candidate
would come back with different neighbours depending on the query. A tool that silently
changes its answer like that is worse than one that refuses.

**It puts the block-occupancy rule where CI can execute it.** Both occupancy traps are
character-level facts about strings (see :mod:`item_naming_rules`), and expressed as a
MariaDB regex they are untested — this app has no Frappe integration-test job. In Python
they are asserted on every push against the literal production strings.

Above :data:`CORPUS_CEILING` this refuses and says so rather than degrading into a
narrowed mode nobody has exercised. There is no fallback branch on purpose: it would never
run on this site, so it would never be exercised, and it could not be equivalent anyway.

--------------------------------------------------------------------------------------
The corpus is permission-filtered, and the caller is told by how much
--------------------------------------------------------------------------------------

Reads go through ``frappe.get_list``, which applies DocPerms and User Permissions. That is
the right default and it has a consequence worth stating out loud rather than discovering:
a user who cannot see every Item gets a duplicate check against the subset they can see,
and "no duplicate found" then means "none that you can see". Every payload therefore
carries ``visible`` beside ``total``, so a gap is visible in the answer instead of being
an invisible property of the reader.
"""

import frappe
from frappe import _

from erpnext_enhancements.inventory_enhancements import item_naming_rules as rules

#: Above this many Item rows, refuse rather than degrade. Set an order of magnitude clear
#: of the live corpus (716 on 2026-08-19, and growing during the working day) — it is a
#: runaway guard, not a tuning knob. Raising it is a deliberate act; the refusal names the
#: number it saw so nobody has to guess.
CORPUS_CEILING = 5000

#: Fields the corpus read needs. `disabled` is deliberately absent: every row carries
#: `disabled = 0`, so filtering on it returns everything or nothing depending which way the
#: predicate was written and both look plausible. The only marker that separates a live
#: record from a QuickBooks tombstone is the `(deleted)` suffix in the code.
CORPUS_FIELDS = ("item_code", "item_name", "item_group", "stock_uom")


def read_corpus(include_deleted=True):
	"""Every Item the current user can read, as plain dicts.

	Returns ``(rows, meta)``. ``meta`` carries ``total`` (unfiltered, via ``frappe.db.count``)
	and ``visible`` (what this user got), because the difference is the honest measure of
	how much a "no duplicate found" result is worth to them.
	"""
	total = frappe.db.count("Item")
	rows = frappe.get_list(
		"Item",
		fields=list(CORPUS_FIELDS),
		limit_page_length=0,
	)
	rows = [dict(row) for row in rows]
	if not include_deleted:
		rows = [r for r in rows if rules.DELETED_MARKER not in (r.get("item_code") or "").lower()]
	meta = {
		"total": total,
		"visible": len(rows),
		"permission_filtered": len(rows) < total,
		"includes_deleted": include_deleted,
	}
	return rows, meta


def read_brands():
	"""Brand names, for the brand-as-category check.

	Guarded because `Brand` is an ERPNext doctype and this app's hooks fire during
	ERPNext's own test bootstrap, before every doctype exists — the same defensive shape
	the rest of this app uses for custom-field reads.
	"""
	if not frappe.db.exists("DocType", "Brand"):
		return []
	try:
		return [row.get("name") for row in frappe.get_all("Brand", fields=["name"])]
	except Exception:
		return []


def read_reserved_codes():
	"""``{prefix: [codes]}`` for the block-allocated families.

	Unions ``tabItem`` with ``tabConfigurable Product``, and the reason is a lifecycle
	window rather than a present-day gap. ``Configurable Product`` is
	``autoname: field:product_code`` and ``product_configurator.erp_integration``
	generates the Item *from* that part number afterwards — so between the two a `PDT-`
	number is allocated and ``tabItem`` cannot see it. Today the one configurable product
	(`PDT-0040`) also exists as an Item and the union changes no answer; it is here so
	that the day it matters is not the day somebody notices.
	"""
	# `get_all`, not `get_list`, and the asymmetry with read_corpus is deliberate: a
	# number is taken regardless of who can see the record holding it. Occupancy that
	# varied by reader would hand two people the same "free" slot. Only the code strings
	# are read, so nothing about the hidden records is disclosed.
	item_codes = frappe.get_all("Item", pluck="item_code")
	reserved = {prefix: list(item_codes) for prefix in rules.RESERVED_PREFIXES}
	if frappe.db.exists("DocType", "Configurable Product"):
		try:
			reserved["PDT"] = list(item_codes) + frappe.get_all("Configurable Product", pluck="name")
		except Exception:
			pass
	return reserved


def inspect_item_naming(
	item_code=None,
	item_name=None,
	item_group=None,
	stock_uom=None,
	similar_limit=rules.DEFAULT_SIMILAR_LIMIT,
	existing=False,
):
	"""Check one proposed Item against the schema. Read-only; returns findings.

	The whole payload, including the refusal, is a plain dict — no exception is raised for
	a bad candidate, because "this candidate is wrong" is the answer, not an error.

	``existing`` says whether this record is already saved. It changes what an exact code
	match *means* — a collision for a proposal, the record itself for a saved row — and
	getting it wrong made every saved Item report as a STOP in v1.337.0. See
	:func:`item_naming_rules.evaluate`.
	"""
	total = frappe.db.count("Item")
	if total > CORPUS_CEILING:
		return {
			"success": False,
			"error": "corpus_too_large",
			"message": (
				f"{total} Item rows exceeds the {CORPUS_CEILING}-row ceiling this check reads in "
				"full. There is deliberately no narrowed-query fallback — it could not weight "
				"similarity the same way, so it would answer differently. Raise "
				"inventory_enhancements.item_naming.CORPUS_CEILING deliberately, or build the "
				"narrowed path."
			),
			"total_rows": total,
			"ceiling": CORPUS_CEILING,
		}

	corpus, meta = read_corpus()
	result = rules.evaluate(
		{
			"item_code": item_code,
			"item_name": item_name,
			"item_group": item_group,
			"stock_uom": stock_uom,
		},
		corpus,
		brands=read_brands(),
		reserved_codes=read_reserved_codes(),
		similar_limit=similar_limit,
		existing=bool(existing),
	)
	result["success"] = True
	result["corpus"] = meta
	result["reference"] = reference_vocabulary()
	result["context"] = corpus_context(corpus)
	return result


def reference_vocabulary():
	"""The approved categories, served rather than embedded in the prompt that uses them.

	Categories are **rules**, not data — Appendix A is policy this business set, so unlike
	every count in this payload it does not go stale and is a constant in
	:mod:`item_naming_rules`. It is returned here so there is exactly one copy of it in
	the app, and the skill that reads it does not carry a second that can drift.
	"""
	return {
		"tier1": sorted(rules.TIER1),
		"tier2": sorted(rules.TIER2),
		"tier3_replacements": dict(sorted(rules.TIER3_REPLACEMENTS.items())),
		"tier3_replacements_not_themselves_approved": sorted(rules.TIER3_REPLACEMENT_UNAPPROVED),
		"consumable_groups": list(rules.CONSUMABLE_GROUPS),
		"segments": list(rules.SEGMENTS),
		"source": "ERPNext Item Naming Schema SOP v1.0, Appendix A — see docs/item-naming-schema.md",
	}


def corpus_context(corpus):
	"""The handful of live numbers a reader needs, measured now rather than remembered.

	Every figure here is read at call time and none is written down anywhere. The stock
	UOM split is the reason this exists: SOP C-10 records `Unit` and `Nos` as two labels
	for one concept and asks for a standard, and until somebody sets one, a validator that
	*picked* would be arbitrating a governance question. It reports the split and lets the
	reader follow the siblings.
	"""
	uoms = {}
	root_group = 0
	tombstones = 0
	for row in corpus:
		uom = (row.get("stock_uom") or "").strip() or "(unset)"
		uoms[uom] = uoms.get(uom, 0) + 1
		if (row.get("item_group") or "") == rules.ROOT_ITEM_GROUP:
			root_group += 1
		if rules.DELETED_MARKER in (row.get("item_code") or "").lower():
			tombstones += 1
	return {
		"stock_uom_distribution": dict(sorted(uoms.items(), key=lambda kv: -kv[1])),
		"rows_on_root_item_group": root_group,
		"deleted_suffix_rows": tombstones,
		"note": (
			"Measured at call time. Nothing here is a constant — quote it from this response "
			"or not at all."
		),
	}


# --- the corpus audit ----------------------------------------------------------


def audit_corpus(include_deleted=False, severity=None, family=None, code=None):
	"""Every Item, checked, filtered, and summarised. Backs the Item Naming Audit report.

	Filters are applied **after** the audit rather than inside the query, and that is the
	point: the summary counts describe the whole corpus while the rows describe what was
	asked for. A report whose headline figure moved every time somebody ticked a filter
	would be a report nobody could quote.

	``include_deleted`` defaults to False. The 135 QuickBooks tombstones are 100%
	``item_code = item_name`` and would otherwise be most of the list, drowning the live
	defects that somebody can actually act on.
	"""
	corpus, meta = read_corpus()
	rows = rules.audit(corpus, brands=read_brands())
	summary = rules.summarise(rows)

	wanted_severity = (severity or "").strip().upper()
	wanted_family = (family or "").strip().lower()
	wanted_code = (code or "").strip()

	out = []
	for row in rows:
		if not include_deleted and row["is_tombstone"]:
			continue
		if not row["findings"]:
			continue
		if wanted_severity and wanted_severity not in {"", "ANY"} and row["verdict"] != wanted_severity:
			continue
		if wanted_family and wanted_family not in {"", "any"} and row["family"] != wanted_family:
			continue
		if wanted_code and not any(f["code"] == wanted_code for f in row["findings"]):
			continue
		out.append(row)

	out.sort(key=rules.audit_sort_key)
	return {"rows": out, "summary": summary, "corpus": meta}


def _as_bool(value):
	"""Coerce an HTTP argument to a bool. Whitelisted args arrive as strings.

	Handles the three spellings a caller might reasonably send — ``1``, ``"1"``, ``"true"``
	— rather than `cint`, which reads ``"true"`` as 0 and would silently turn a saved record
	back into a proposal.
	"""
	if isinstance(value, str):
		return value.strip().lower() in ("1", "true", "yes")
	return bool(value)


@frappe.whitelist()
def check_item(
	item_code=None, item_name=None, item_group=None, stock_uom=None, mode="record", existing=False
):
	"""Check one proposed or existing Item. Read-only, advisory, writes nothing.

	Two modes, and the split is not a micro-optimisation:

	``record``  the checks that read only the record in hand — case, separators, whitespace,
	            category, code family shape. **No corpus read at all.** This is what the Item
	            form calls on every refresh, and a corpus read there would mean reading the
	            whole catalogue every time anybody opens an Item.
	``full``    adds duplicates, block occupancy and near neighbours. What the button calls,
	            on demand, when somebody has actually asked.

	``existing`` matters only in ``full`` mode, where it stops a saved record matching itself.
	``record`` mode reads no corpus, so it has nothing to match against and is unaffected —
	which is why the form's headline was right while its button was wrong.

	The brand list is read in both modes: it is a handful of rows, and without it a
	brand-led name falls through to `name_category_unapproved`, which is a STOP where a
	`name_category_is_brand` FIX is the truthful answer.
	"""
	if not frappe.has_permission("Item", "read"):
		frappe.throw(_("You do not have read access to Items."), frappe.PermissionError)

	mode = (mode or "record").strip().lower()
	if mode not in ("record", "full"):
		frappe.throw(_("mode must be 'record' or 'full'."), frappe.ValidationError)

	if mode == "full":
		result = inspect_item_naming(
			item_code=item_code,
			item_name=item_name,
			item_group=item_group,
			stock_uom=stock_uom,
			existing=_as_bool(existing),
		)
		result["mode"] = "full"
		return result

	brands = read_brands()
	findings = []
	findings.extend(rules.check_code(item_code))
	findings.extend(rules.check_name(item_name, item_code, brands))
	findings.extend(rules.check_supporting(item_group, stock_uom))
	findings.extend(rules.check_code_name_agreement(item_code, item_name))
	return {
		"success": True,
		"mode": "record",
		"item_code": item_code,
		"family": rules.classify_code_family(item_code),
		"verdict": rules.verdict(findings),
		"findings": findings,
		# Stated so a caller can never mistake a record-mode PASS for "no duplicate exists".
		"checked": "this record only — no duplicate, block-occupancy or neighbour check was run",
	}
