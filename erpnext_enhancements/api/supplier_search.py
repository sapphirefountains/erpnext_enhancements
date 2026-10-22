"""Supplier Finder: who sells this, or has sold it to us (v1.726.0).

Nik, 2026-10-22: "say I know we need a pump, but I don't remember all our pump suppliers ...
what if we have two pump categories and one pump supplier I'm looking for is in a different
supplier group." His rulings that day, which ``docs/supplier-finder.md`` records in full:

* **One search.** It accepts a word ("pump"), a phrase ("submersible pump") or a model or part
  number ("EQKT750"). It runs on the Supplier Finder page (``/desk/supplier-finder``), from the
  "Find suppliers" button on the Item and Purchase Order forms, and through the
  ``supplier_search`` assistant tool. All three call :func:`run_search`.
* **Evidence ranks suppliers, groups do not.** A supplier we bought a matching item from ranks
  first, then one that quoted, listed or priced it. A supplier whose only link is a matching
  Supplier Group or a matching name ranks last. Duplicate supplier records (one company entered
  twice, with and without "Inc.") show as one row with the others as aliases. Nothing is merged.
* **The AI only expands the query.** When it is asked for and set up, Gemini rewrites a fuzzy
  search into search phrases and Supplier Group names. It sees only what was typed and the list of
  group names, never a supplier, an item or a price. Ranking stays deterministic. When the model
  is not set up, is busy, times out or answers badly, the keyword results come back anyway, with a
  note saying the AI step was skipped.

**It writes nothing** except the shared Gemini client's AI Model Usage row and, when a model call
fails, one Error Log row naming the exception's class (never the query). An expansion is cached
for a day per search, so retyping the same search costs nothing.

Who may use it: anyone who may read both Supplier and Item. Each source of evidence is used only
when the person may also read it (Purchase Order, Purchase Invoice, Purchase Receipt, Supplier
Quotation, Item Price). One they may not read is neither ranked on nor shown, and the answer says
which were hidden. So a price paid, in its own currency, comes only from a purchase they may read.

The decisions are in ``inventory_enhancements/supplier_search_rules.py`` (bench-free tested).
This file does the reads and the one model call. Indentation is tabs (new file).
"""

import hashlib
import json

import frappe
from frappe import _
from frappe.utils import cint, cstr, getdate, nowdate

from erpnext_enhancements.api.item_suggest import ai_configured
from erpnext_enhancements.inventory_enhancements import supplier_search_rules as rules

#: Model calls a person may make in an hour. Counted per user.
RATE_LIMIT = 60
RATE_WINDOW_SECONDS = 3600
#: Someone is waiting at the screen, and expanding a few words needs little thought.
THINKING_LEVEL = "LOW"
TIMEOUT_SECONDS = 20
CACHE_SECONDS = 24 * 3600

MSG_EMPTY = "Type what you are looking for: a kind of item, a model or a part number."
MSG_AI_OFF = "The AI step is not set up, so these are keyword results."
MSG_AI_BUSY = "You have used the AI step a lot this hour, so these are keyword results."
MSG_AI_FAILED = "The AI step did not answer, so these are keyword results."
MSG_AI_SKIPPED_MODEL = "This looks like a model or part number, so the AI step was not needed."


# --- reads -----------------------------------------------------------------------


def _items():
	"""Every enabled, non-template Item with its Item Supplier part numbers. The catalog is about
	1,200 rows, and reading it whole is cheaper than one LIKE query per term."""
	rows = frappe.db.sql(
		"""
		select name as item_code, item_name, item_group, left(description, 600) as description
		from `tabItem`
		where disabled = 0 and ifnull(has_variants, 0) = 0
		""",
		as_dict=True,
	)
	parts = {}
	for p in frappe.db.sql(
		"""
		select parent, supplier_part_no from `tabItem Supplier`
		where parenttype = 'Item' and ifnull(supplier_part_no, '') != ''
		""",
		as_dict=True,
	):
		parts.setdefault(p.parent, []).append(p.supplier_part_no)
	out = []
	for r in rows:
		out.append(
			{
				"item_code": r.item_code,
				"item_name": r.item_name or "",
				"item_group": r.item_group or "",
				"description": rules.plain_text(r.description, 600),
				"part_nos": parts.get(r.item_code, []),
			}
		)
	return out


def _item(item_code):
	"""One Item by its code, enabled or not, as an :func:`_items` row in a list; [] when there is none."""
	row = frappe.db.sql(
		"select name as item_code, item_name, item_group from `tabItem` where name = %s",
		(item_code,),
		as_dict=True,
	)
	if not row:
		return []
	parts = frappe.db.sql(
		"""
		select supplier_part_no from `tabItem Supplier`
		where parenttype = 'Item' and parent = %s and ifnull(supplier_part_no, '') != ''
		""",
		(item_code,),
	)
	return [
		{
			"item_code": row[0].item_code,
			"item_name": row[0].item_name or "",
			"item_group": row[0].item_group or "",
			"description": "",
			"part_nos": [p[0] for p in parts],
		}
	]


def _suppliers():
	"""Every non-internal Supplier: ``{docname: {"supplier_name", "groups", "disabled"}}``. The
	primary group comes first in ``groups``, followed by the Additional Supplier Groups."""
	info = {}
	for s in frappe.db.sql(
		"""
		select name, supplier_name, supplier_group, disabled
		from `tabSupplier`
		where ifnull(is_internal_supplier, 0) = 0
		""",
		as_dict=True,
	):
		info[s.name] = {
			"supplier_name": s.supplier_name or s.name,
			"groups": [s.supplier_group] if s.supplier_group else [],
			"disabled": bool(cint(s.disabled)),
		}
	if frappe.db.table_exists("Additional Supplier Group"):
		for a in frappe.db.sql(
			"""
			select parent, supplier_group from `tabAdditional Supplier Group`
			where parenttype = 'Supplier' and ifnull(supplier_group, '') != ''
			order by idx
			""",
			as_dict=True,
		):
			row = info.get(a.parent)
			if row is not None and a.supplier_group not in row["groups"]:
				row["groups"].append(a.supplier_group)
	return info


def _group_names():
	return frappe.get_all("Supplier Group", filters={"is_group": 0}, pluck="name", order_by="name asc")


#: Where purchase evidence comes from, and the doctype a person must be able to read for it to be
#: used or shown: (doctype, child table, date field, count key, a purchase with a price?). The
#: last three are purchases. A Purchase Invoice counts only when it is not a return.
PURCHASE_SOURCES = (
	("Purchase Order", "Purchase Order Item", "transaction_date", "po_lines"),
	("Purchase Invoice", "Purchase Invoice Item", "posting_date", "pi_lines"),
	("Purchase Receipt", "Purchase Receipt Item", "posting_date", "pr_lines"),
)
#: Every doctype the evidence reads, so the caller's read permission on each can be checked.
EVIDENCE_DOCTYPES = (*(src[0] for src in PURCHASE_SOURCES), "Supplier Quotation", "Item Price")

MSG_HIDDEN = "Some purchase history is hidden because you cannot read: {0}."


def readable_sources():
	"""``{doctype: bool}``: may this person read each source of evidence? A source they may not read
	is neither used to rank nor shown (``hidden`` in the answer says which)."""
	return {dt: bool(frappe.has_permission(dt, "read")) for dt in EVIDENCE_DOCTYPES}


def _evidence(item_codes=None, suppliers=None, allowed=None):
	"""Supplier–item evidence rows (:func:`rules.rank_suppliers` documents the shape), filtered by
	``item_codes`` or by ``suppliers``, from the sources ``allowed`` (:func:`readable_sources`) says
	this person may read. Submitted orders, invoices (not returns) and receipts count as bought,
	draft and submitted quotations as quoted, and Item Supplier rows, default suppliers and buying
	prices as carries it. ``last_rate`` is the newest nonzero rate from a readable purchase, in that
	document's own currency."""
	if not item_codes and not suppliers:
		return []
	allowed = allowed or {}
	values = {"keys": tuple(item_codes or suppliers)}

	def where(item_col, supplier_col):
		return f"{item_col if item_codes else supplier_col} in %(keys)s"

	pairs = {}

	def _pair(supplier, item_code):
		return pairs.setdefault((supplier, item_code), dict(rules.blank_pair(item_code), supplier=supplier))

	for doctype, child, date_field, key in PURCHASE_SOURCES:
		if not allowed.get(doctype):
			continue
		not_return = " and ifnull(p.is_return, 0) = 0" if doctype != "Purchase Order" else ""
		for r in frappe.db.sql(
			f"""
			select p.supplier, c.item_code, count(*) as n, max(p.{date_field}) as last_date
			from `tab{child}` c join `tab{doctype}` p on p.name = c.parent
			where p.docstatus = 1{not_return} and {where("c.item_code", "p.supplier")}
			group by p.supplier, c.item_code
			""",
			values,
			as_dict=True,
		):
			row = _pair(r.supplier, r.item_code)
			row[key] += cint(r.n)
			if r.last_date and (not row["last_date"] or getdate(r.last_date) > getdate(row["last_date"])):
				row["last_date"] = str(getdate(r.last_date))
		# The newest nonzero rate per pair, ranked in the database so no pair is cut off however long
		# the history. 377 of 592 PO lines on prod were $0 on 2026-10-22, so a zero is no price at all.
		for r in frappe.db.sql(
			f"""
			select supplier, item_code, rate, uom, currency, dt from (
				select p.supplier, c.item_code, c.rate, c.uom, p.currency, p.{date_field} as dt,
					row_number() over (
						partition by p.supplier, c.item_code
						order by p.{date_field} desc, p.modified desc
					) as rn
				from `tab{child}` c join `tab{doctype}` p on p.name = c.parent
				where p.docstatus = 1{not_return} and c.rate > 0 and {where("c.item_code", "p.supplier")}
			) latest
			where rn = 1
			""",
			values,
			as_dict=True,
		):
			row = _pair(r.supplier, r.item_code)
			if row.get("_rate_date") is None or getdate(r.dt) > getdate(row["_rate_date"]):
				row["last_rate"], row["uom"], row["currency"] = float(r.rate), r.uom, r.currency
				row["_rate_date"] = r.dt

	if allowed.get("Supplier Quotation"):
		for r in frappe.db.sql(
			f"""
			select p.supplier, c.item_code, count(*) as n
			from `tabSupplier Quotation Item` c join `tabSupplier Quotation` p on p.name = c.parent
			where p.docstatus < 2 and {where("c.item_code", "p.supplier")}
			group by p.supplier, c.item_code
			""",
			values,
			as_dict=True,
		):
			_pair(r.supplier, r.item_code)["sq_lines"] += cint(r.n)

	# Item Supplier and Item Default are rows of the Item, which the caller may read.
	for r in frappe.db.sql(
		f"""
		select c.supplier, c.parent as item_code, c.supplier_part_no
		from `tabItem Supplier` c
		where c.parenttype = 'Item' and ifnull(c.supplier, '') != ''
			and {where("c.parent", "c.supplier")}
		""",
		values,
		as_dict=True,
	):
		row = _pair(r.supplier, r.item_code)
		row["listed"] = True
		row["part_no"] = row["part_no"] or cstr(r.supplier_part_no)

	for r in frappe.db.sql(
		f"""
		select c.default_supplier as supplier, c.parent as item_code
		from `tabItem Default` c
		where c.parenttype = 'Item' and ifnull(c.default_supplier, '') != ''
			and {where("c.parent", "c.default_supplier")}
		""",
		values,
		as_dict=True,
	):
		_pair(r.supplier, r.item_code)["default"] = True

	if allowed.get("Item Price"):
		for r in frappe.db.sql(
			f"""
			select c.supplier, c.item_code
			from `tabItem Price` c
			where c.buying = 1 and ifnull(c.supplier, '') != ''
				and {where("c.item_code", "c.supplier")}
			""",
			values,
			as_dict=True,
		):
			_pair(r.supplier, r.item_code)["price"] = True

	for row in pairs.values():
		row.pop("_rate_date", None)
	return list(pairs.values())


def _hidden(allowed):
	"""The evidence doctypes this person may not read, and the plain note that says so."""
	hidden = [dt for dt in EVIDENCE_DOCTYPES if not allowed.get(dt)]
	return hidden, (_(MSG_HIDDEN).format(", ".join(_(dt) for dt in hidden)) if hidden else "")


# --- the AI step -------------------------------------------------------------------


def _enforce_rate():
	"""False when this user has used the AI step up for the window. A cache that cannot count lets
	the call through."""
	try:
		key = frappe.cache.make_key(f"ee_supplier_search_ai:{frappe.session.user}")
		count = frappe.cache.incrby(key, 1)
		if count == 1:
			frappe.cache.expire(key, RATE_WINDOW_SECONDS)
		return count <= RATE_LIMIT
	except Exception:
		return True


def _log(exc):
	"""One Error Log row with the exception's class and a short message, never the query. Keyword
	arguments: v16 reads a positional first argument as the title."""
	try:
		frappe.log_error(
			title="Supplier search AI step failed",
			message=f"{type(exc).__name__}: {rules.plain_text(str(exc), 400)}",
		)
	except Exception:
		pass


def _ask_model(prompt):
	from erpnext_enhancements.api.gemini import generate_content_with_vertex_ai

	text, _thoughts = generate_content_with_vertex_ai(
		prompt,
		rules.SYSTEM_PROMPT,
		None,
		feature=rules.FEATURE,
		response_mime_type="application/json",
		thinking_level=THINKING_LEVEL,
		timeout=TIMEOUT_SECONDS,
	)
	return text


def _cache_key(whole, terms):
	digest = hashlib.sha1(f"{whole}|{' '.join(terms)}".encode()).hexdigest()
	return f"ee_supplier_search_expand:{digest}"


def expand(query, terms, whole, group_names):
	"""``(expansion, message)``. ``expansion`` is :func:`rules.parse_expansion`'s dict, or None when
	the AI step could not help, and then ``message`` says why in plain words. Nothing here raises."""
	key = _cache_key(whole, terms)
	try:
		cached = frappe.cache.get_value(key)
		if cached:
			return json.loads(cached), ""
	except Exception:
		pass
	if not ai_configured():
		return None, _(MSG_AI_OFF)
	if not _enforce_rate():
		return None, _(MSG_AI_BUSY)
	try:
		reply = _ask_model(rules.build_expansion_prompt(query, group_names))
		parsed = rules.parse_expansion(reply, group_names, terms)
	except Exception as exc:
		_log(exc)
		return None, _(MSG_AI_FAILED)
	if parsed is None:
		_log(ValueError("The model's reply was not the JSON asked for"))
		return None, _(MSG_AI_FAILED)
	try:
		frappe.cache.set_value(key, json.dumps(parsed), expires_in_sec=CACHE_SECONDS)
	except Exception:
		pass
	return parsed, ""


# --- the search --------------------------------------------------------------------


def _check_access():
	frappe.has_permission("Supplier", "read", throw=True)
	frappe.has_permission("Item", "read", throw=True)


def run_search(query=None, item_code=None, use_ai=False, limit=None):
	"""The search. The caller has checked access (:func:`search`, the assistant tool).

	``item_code`` pins the search to that one Item: the Item form's button, and a click on an item
	in the page. Answers ``{"ok", "message", "query", "terms", "partial", "items", "items_total",
	"suppliers", "suppliers_total", "groups", "ai", "rates", "hidden", "hidden_note"}``:

	* ``items`` are :func:`rules.match_items` rows, each with ``suppliers``, the names of suppliers
	  with evidence for that item. Evidence is read for **every** matching item, and only the list
	  shown is cut to :data:`rules.ITEMS_MAX`: ``items_total`` says how many matched. Cutting before
	  the evidence read dropped most real suppliers of a broad search ("pvc" matched 270 items on
	  prod, 94 of them bought, and only the first 60 were looked up).
	* ``suppliers`` are :func:`rules.rank_suppliers` rows, each with a plain ``reason``, cut to
	  ``limit``; ``suppliers_total`` says how many there were.
	* ``hidden`` names the evidence doctypes this person may not read, which were neither used nor
	  shown, and ``hidden_note`` says so in words. ``rates`` is whether any price could be shown.
	* ``groups`` are the Supplier Groups that matched, ``{"name", "hits", "suppliers", "source"}``.
	  ``source`` is "query", "category" (from the matched items' category word) or "ai".
	* ``ai`` is ``{"asked", "used", "message", "note", "terms", "groups"}``.
	"""
	query = rules.plain_text(query, rules.QUERY_MAX)
	item_code = cstr(item_code).strip()
	if not query and not item_code:
		return {"ok": False, "message": _(MSG_EMPTY)}
	limit = max(1, min(cint(limit) or rules.SUPPLIERS_MAX, rules.SUPPLIERS_MAX))

	catalog = _items()
	if item_code:
		# Straight from the table: the catalog leaves out disabled Items and templates, and the Item
		# form's button is just as likely to be pressed on one of those.
		pinned = [i for i in catalog if i["item_code"] == item_code] or _item(item_code)
		query = query or item_code
		terms = rules.query_terms(query)
		items = [
			dict(
				{k: v for k, v in i.items() if k != "description"},
				score=120.0,
				hits=len(terms),
				model=True,
				strict=True,
			)
			for i in pinned
		]
		partial = False
	else:
		terms = rules.query_terms(query)
		found = rules.match_items(
			catalog, terms, rules.whole_query(query), single=rules.is_single_chunk(query)
		)
		items, partial = found["items"], found["partial"]
	whole = rules.whole_query(query)

	group_names = _group_names()
	group_hits = {g["name"]: dict(g, source="query") for g in rules.match_groups(group_names, terms)}
	if not group_hits and items:
		# A model search names no group, but its item is a PUMP: the pump groups are where to
		# look for another supplier.
		for g in rules.match_groups(group_names, rules.category_terms(items)):
			group_hits[g["name"]] = dict(g, source="category", hits=len(terms) or 1)

	ai = {"asked": bool(cint(use_ai)), "used": False, "message": "", "note": "", "terms": [], "groups": []}
	if ai["asked"] and not item_code:
		if any(i.get("model") for i in items):
			ai["message"] = _(MSG_AI_SKIPPED_MODEL)
		else:
			expansion, message = expand(query, terms, whole, group_names)
			ai["message"] = message
			if expansion:
				ai.update(
					used=True,
					note=expansion["note"],
					terms=[" ".join(p) for p in expansion["terms"]],
					groups=expansion["groups"],
				)
				seen = {i["item_code"] for i in items}
				for phrase in expansion["terms"]:
					for row in rules.match_items(catalog, phrase)["items"]:
						if row["strict"] and row["item_code"] not in seen:
							seen.add(row["item_code"])
							items.append(dict(row, strict=False, model=False, via_ai=True))
				for g in expansion["groups"]:
					if g not in group_hits:
						group_hits[g] = {"name": g, "hits": len(terms) or 1, "source": "ai"}

	supplier_info = _suppliers()
	allowed = readable_sources()
	hidden, hidden_note = _hidden(allowed)
	evidence = _evidence(item_codes=[i["item_code"] for i in items], allowed=allowed)

	per_supplier_group = {}
	for sup, info in supplier_info.items():
		best = max((group_hits[g]["hits"] for g in info["groups"] if g in group_hits), default=0)
		if best:
			per_supplier_group[sup] = best
	name_hits = {}
	if not item_code:
		for sup, info in supplier_info.items():
			hit = rules.match_groups([info["supplier_name"]], terms)
			if hit:
				name_hits[sup] = hit[0]["hits"]

	ranked_all = rules.rank_suppliers(
		evidence,
		items,
		supplier_info,
		group_hits=per_supplier_group,
		name_hits=name_hits,
		term_count=len(terms),
		today=getdate(nowdate()),
		limit=len(supplier_info) or 1,
	)
	ranked = ranked_all[:limit]
	for row in ranked:
		row["reason"] = rules.reason(row)

	by_item = {}
	for ev in evidence:
		if ev["supplier"] in supplier_info:
			by_item.setdefault(ev["item_code"], set()).add(ev["supplier"])
	shown = items[: rules.ITEMS_MAX]
	for i in shown:
		i["suppliers"] = sorted(by_item.get(i["item_code"], ()))

	counts = {}
	for info in supplier_info.values():
		for g in info["groups"]:
			counts[g] = counts.get(g, 0) + 1
	groups = [dict(g, suppliers=counts.get(g["name"], 0)) for g in group_hits.values()]
	groups.sort(key=lambda g: ({"query": 0, "category": 1, "ai": 2}[g["source"]], -g["hits"], g["name"]))

	return {
		"ok": True,
		"message": "",
		"query": query,
		"item_code": item_code,
		"terms": terms,
		"partial": partial,
		"items": shown,
		"items_total": len(items),
		"suppliers": ranked,
		"suppliers_total": len(ranked_all),
		"groups": groups,
		"ai": ai,
		"rates": any(allowed.get(src[0]) for src in PURCHASE_SOURCES),
		"hidden": hidden,
		"hidden_note": hidden_note,
	}


def run_supplier_history(supplier):
	"""What we have bought from ``supplier`` and from every record that is the same company.
	Answers ``{"ok", "supplier", "supplier_name", "aliases", "groups", "items", "rates", "hidden",
	"hidden_note"}``. Each ``items`` row is an evidence row with ``item_name``, newest first."""
	supplier_info = _suppliers()
	if supplier not in supplier_info:
		return {"ok": False, "message": _("No supplier {0}.").format(supplier)}
	key = rules.supplier_key(supplier_info[supplier]["supplier_name"])
	members = sorted(
		s for s, info in supplier_info.items() if rules.supplier_key(info["supplier_name"]) == key
	)
	allowed = readable_sources()
	hidden, hidden_note = _hidden(allowed)
	rows = _evidence(suppliers=members, allowed=allowed)
	names = dict(
		frappe.db.sql(
			"select name, item_name from `tabItem` where name in %(codes)s",
			{"codes": tuple({r["item_code"] for r in rows}) or ("",)},
		)
	)
	# The ranker folds the aliases' evidence the same way the search does, so a card and this
	# screen never disagree about a last price.
	ranked = rules.rank_suppliers(
		rows,
		[{"item_code": c, "item_name": n} for c, n in names.items()],
		{m: supplier_info[m] for m in members},
		today=getdate(nowdate()),
		limit=1,
		items_per_supplier=None,
	)
	items = ranked[0]["items"] if ranked else []
	items.sort(key=lambda r: (r["last_date"] or "", sum(r[k] for k in rules.BOUGHT_KEYS)), reverse=True)
	groups = []
	for s in [supplier] + [m for m in members if m != supplier]:
		for g in supplier_info[s]["groups"]:
			if g not in groups:
				groups.append(g)
	return {
		"ok": True,
		"supplier": supplier,
		"supplier_name": supplier_info[supplier]["supplier_name"],
		"aliases": [m for m in members if m != supplier],
		"groups": groups,
		"items": items,
		"rates": any(allowed.get(src[0]) for src in PURCHASE_SOURCES),
		"hidden": hidden,
		"hidden_note": hidden_note,
	}


@frappe.whitelist(methods=["POST"])
def search(query=None, item_code=None, use_ai=0):
	"""The Supplier Finder's search (see :func:`run_search`). POST, because asking the AI costs a
	model call. For whoever may read Supplier and Item. **Writes nothing.**"""
	_check_access()
	return run_search(query, item_code=item_code, use_ai=use_ai)


@frappe.whitelist()
def supplier_history(supplier):
	"""One supplier's matched purchase history (see :func:`run_supplier_history`)."""
	_check_access()
	supplier = cstr(supplier).strip()
	if supplier and frappe.db.exists("Supplier", supplier):
		frappe.has_permission("Supplier", "read", doc=supplier, throw=True)
	return run_supplier_history(supplier)
