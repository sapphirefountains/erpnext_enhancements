# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What the Supplier Finder decides (v1.726.0). **No Frappe, no I/O.**

Nik, 2026-10-22: "say I know we need a pump, but I don't remember all our pump suppliers." The
Supplier Group field cannot answer that. On prod that day there were 174 flat groups, 629 of 1,193
suppliers had none, and pump suppliers were spread over five of them. Most suppliers we had
actually bought pumps from were in a group with no "pump" in its name. So the answer comes from
what we have bought, quoted and listed, and a group only adds suppliers to the bottom of the list.

``api/supplier_search.py`` reads the catalog, the suppliers and the purchase evidence, and makes
the one optional model call. Everything that decides an answer is here, so the bench-free tests
run it on every push:

* :func:`query_terms` turns what was typed into terms. It drops filler words, folds plurals, and
  adds the synonyms in :data:`SYNONYMS`.
* :func:`match_items` scores every Item against the terms. A term matches a word it starts (PENT
  finds PENTAIR). A term with a digit in it also matches inside the item's squashed text, so
  "SQ 3-65" finds SQ365. The whole query squashed and found in an item code or a supplier part
  number is a *model* match, which outranks the rest. Strict means every term matched. When
  nothing matches strictly, items that match some of the terms are returned and marked partial.
* :func:`match_groups` finds Supplier Groups by their words, ignoring words like SUPPLIES that
  every group shares.
* :func:`rank_suppliers` folds duplicate supplier records into one row (:func:`supplier_key`), then
  ranks them. A supplier we bought a matching item from comes first. Next is one that quoted it,
  lists it as an Item Supplier, is its default supplier, or has a buying price for it. A supplier
  that only sits in a matching group, or whose name matches, comes last.
* :func:`build_expansion_prompt` and :func:`parse_expansion` are the AI layer, which only expands
  the query (Nik's ruling). The model sees what was typed and the list of Supplier Group names.
  It never sees a supplier, an item or a price. Its answer is untrusted: terms are cut short and
  bounded in number, and a group counts only if the site has a group of exactly that name.

Indentation is tabs (new file).
"""

from __future__ import annotations

import datetime as _dt
import functools
import re
from collections.abc import Iterable

from erpnext_enhancements.inventory_enhancements import item_suggest_rules as _suggest

#: Plain text, entities decoded and tags gone (the item suggester's own cleaner).
plain_text = _suggest.plain_text

#: The ``feature`` on the AI Model Usage row.
FEATURE = "supplier_search_expand"

QUERY_MAX = 160
TERMS_MAX = 8
AI_TERMS_MAX = 6
AI_GROUPS_MAX = 6
AI_TERM_LEN = 40
NOTE_MAX = 200

ITEMS_MAX = 60
SUPPLIERS_MAX = 40
ITEMS_PER_SUPPLIER = 6

_WORD = re.compile(r"[A-Z0-9]+")
_NON_ALNUM = re.compile(r"[^A-Z0-9]")
_DIGIT = re.compile(r"\d")

#: Words that say nothing about what is wanted ("who sells a 2hp pump").
STOP_WORDS: frozenset[str] = frozenset(
	"""
	A AN THE AND OR OF FOR WITH TO IN ON AT BY FROM ANY SOME SOMETHING LIKE SIMILAR NEED NEEDS
	WE I OUR US MY WHO WHAT WHERE WHICH SELL SELLS SOLD SELLING BUY BOUGHT BUYING SUPPLIER
	SUPPLIERS VENDOR VENDORS FIND SHOW ME PLEASE GET GOT
	""".split()
)

#: Words too common in Supplier Group names to say anything about a group.
GROUP_FILLER: frozenset[str] = frozenset(
	"SUPPLY SUPPLIES SUPPLIER SUPPLIERS AND THE OF GENERAL MISC SERVICE SERVICES".split()
)

#: Words that mean the same thing to a buyer, matched both ways. The item names were not written
#: to one vocabulary before POL-0602, so a search for LIGHT has to find LAMP. Each row is one
#: meaning. A term matches when any word of its row matches. Keep this short: the AI layer covers
#: looser intent.
SYNONYMS: tuple[frozenset[str], ...] = (
	frozenset({"LIGHT", "LIGHTING", "LAMP", "LUMINAIRE"}),
	frozenset({"NOZZLE", "JET"}),
	frozenset({"PIPE", "PIPING"}),
	frozenset({"HOSE", "TUBING"}),
	frozenset({"WIRE", "CABLE"}),
	frozenset({"BREAKER", "CIRCUIT"}),
	frozenset({"CHEMICAL", "CHEM"}),
	frozenset({"CHLORINE", "HYPOCHLORITE", "BLEACH"}),
	frozenset({"SCREW", "BOLT", "FASTENER"}),
	frozenset({"CONTROLLER", "PLC"}),
	frozenset({"CATRIDGE", "CARTRIDGE"}),
	frozenset({"BRUSHING", "BUSHING"}),
	frozenset({"GREY", "GRAY"}),
)

#: Legal and web suffixes dropped before two supplier names are compared. "Acme Pump Co" and
#: "Acme Pump Company, Inc.", or "Pipe Depot" and "PipeDepot.com", are one supplier entered twice. Prod
#: had about fifty such pairs on 2026-10-22, most created by the Zoho and QuickBooks imports.
#: Merging them would rewrite records that QuickBooks still syncs, so the search shows them as one
#: row with the others listed as aliases.
_SUPPLIER_SUFFIXES = re.compile(
	r"\b(?:INC|INCORPORATED|LLC|L L C|CO|CORP|CORPORATION|COMPANY|LTD|LIMITED|THE|COM|WWW)\b"
)


# --- words ---------------------------------------------------------------------


def stem(word: str) -> str:
	"""Uppercase ``word`` with a plain English plural folded: PUMPS, BOXES, BATTERIES. Words of
	three letters or fewer, words ending SS and words with a digit are left alone."""
	w = (word or "").upper()
	if len(w) <= 3 or _DIGIT.search(w) or w.endswith("SS"):
		return w
	if w.endswith("IES") and len(w) > 4:
		return w[:-3] + "Y"
	if w.endswith(("SSES", "XES", "CHES", "SHES", "ZES")):
		return w[:-2]
	if w.endswith("S"):
		return w[:-1]
	return w


def words(text) -> list[str]:
	"""The alphanumeric runs of ``text``, uppercased and stemmed, in order."""
	return [stem(w) for w in _WORD.findall(str(text or "").upper())]


def squash(text) -> str:
	"""Uppercase with everything but A-Z and 0-9 removed: what a model number is compared on."""
	return _NON_ALNUM.sub("", str(text or "").upper())


@functools.lru_cache(maxsize=2048)
def synonyms_of(term: str) -> frozenset[str]:
	"""``term`` and every word :data:`SYNONYMS` says means the same."""
	out = {term}
	for row in SYNONYMS:
		stems = {stem(w) for w in row}
		if term in stems:
			out |= stems
	return frozenset(out)


#: A size written with separators ("1-1/2", "3-65", "1.5"): one term, digits only, the way the
#: item's squashed name spells it.
_COMPOUND_NUMBER = re.compile(r"\d+(?:[-/.]\d+)+")
#: A number and its unit written apart ("2 hp", "24 V"): one term, as item names write it.
_NUMBER_UNIT = re.compile(r"\b(\d+)\s+(HP|THP|GPH|GPM|V|VAC|VDC|W|A|AMP|AMPS|PH|MM|FT)\b")


def _prepare(query) -> str:
	text = _suggest.plain_text(query, QUERY_MAX).upper()
	text = _COMPOUND_NUMBER.sub(lambda m: squash(m.group(0)), text)
	return _NUMBER_UNIT.sub(r"\1\2", text)


def whole_query(query) -> str:
	"""The search squashed, filler words left out and plurals kept: what a model number or a
	vendor part number is compared on ("Grundfos SQ 3-65" is GRUNDFOSSQ365)."""
	raw = _WORD.findall(_prepare(query))
	kept = [w for w in raw if w not in STOP_WORDS] or raw
	return "".join(kept)


def is_single_chunk(query) -> bool:
	"""Is the search one unbroken chunk ("EQKT-750", "SQ3-65"), filler words aside? Only then can it
	be a model number inside a longer code or name. "pvc 1-1/2" and "sch 40" are a category and a
	size, and as substrings they would mark every 1-1/2" PVC fitting an exact model."""
	chunks = [c for c in _prepare(query).split() if squash(c) and squash(c) not in STOP_WORDS]
	return len(chunks) == 1


def query_terms(query) -> list[str]:
	"""The terms of a search, stemmed, without filler words, de-duplicated, at most
	:data:`TERMS_MAX`. A query of nothing but filler keeps its words rather than becoming empty."""
	all_words = words(_prepare(query))
	kept = [w for w in all_words if w not in STOP_WORDS]
	if not kept:
		kept = all_words
	out: list[str] = []
	for w in kept:
		if w and w not in out:
			out.append(w)
	return out[:TERMS_MAX]


def _term_hits(term: str, item_words: Iterable[str], item_squashed: str) -> bool:
	"""Does ``term`` (or a synonym) match? It matches a word it starts. A term with a digit, or a
	pure-letter term of six or more letters, also matches inside the squashed text, so "SQ365"
	finds "SQ 3-65" and "WHISPERFLO" finds "WHISPERFLOXF". Shorter words do not, or LAMP would
	find CLAMP."""
	for alt in synonyms_of(term):
		for w in item_words:
			if w.startswith(alt):
				return True
		if (_DIGIT.search(alt) or len(alt) >= 6) and alt in item_squashed:
			return True
	return False


# --- items ---------------------------------------------------------------------

#: How much a term hit counts by where it hit.
_FIELD_WEIGHTS = (("code", 3.0), ("name", 3.0), ("parts", 3.0), ("group", 1.0), ("description", 1.0))


def _item_fields(item: dict) -> dict:
	parts = " ".join(str(p) for p in item.get("part_nos") or ())
	fields = {
		"code": item.get("item_code") or "",
		"name": item.get("item_name") or "",
		"parts": parts,
		"group": item.get("item_group") or "",
		"description": item.get("description") or "",
	}
	return {k: (words(v), squash(v)) for k, v in fields.items()}


def score_item(item: dict, terms: list[str], whole: str, single: bool = True) -> dict | None:
	"""One Item scored against ``terms``. ``whole`` is the whole query squashed (:func:`squash`) and
	``single`` says whether it was typed as one chunk (:func:`is_single_chunk`). None when nothing
	matches. Otherwise ``{"score", "hits", "model", "strict"}``. ``hits`` is the number of terms that
	matched somewhere, ``model`` is true when ``whole`` is the item code or a part number, or (a
	single chunk with a digit) is inside one or inside the name, and ``strict`` is true when every
	term matched."""
	fields = _item_fields(item)
	model = False
	score = 0.0
	if whole and len(whole) >= 3:
		code_sq = fields["code"][1]
		part_sqs = [squash(p) for p in item.get("part_nos") or ()]
		# Inside a longer code or name only for one chunk with a digit, as a model number is. A plain
		# word is not a model: PUMP is inside the code CHEMPUMP-STENNER-45M1, and as a "model match"
		# that chemical feed pump outranked every real pump and switched the AI step off. Nor is a
		# category and a size: "pvc 1-1/2" is inside the name of every 1-1/2" PVC fitting.
		has_digit = single and bool(_DIGIT.search(whole))
		if whole == code_sq or whole in part_sqs:
			model, score = True, 120.0
		elif has_digit and len(whole) >= 4 and (whole in code_sq or any(whole in p for p in part_sqs if p)):
			model, score = True, 100.0
		elif has_digit and len(whole) >= 5 and whole in fields["name"][1]:
			model, score = True, 80.0
	hits = 0
	for term in terms:
		best = 0.0
		for key, weight in _FIELD_WEIGHTS:
			w_list, sq = fields[key]
			if _term_hits(term, w_list, sq):
				best = max(best, weight)
		if best:
			hits += 1
			score += best
	if not hits and not model:
		return None
	# POL-0602 names lead with the category ("PUMP, PENTAIR ..."), so an item whose name starts
	# with a term is that thing, and one with the term later on ("COVER, PUMP, ...") is for it.
	name_words = fields["name"][0]
	if name_words and any(name_words[0] in synonyms_of(t) or name_words[0].startswith(t) for t in terms):
		score += 2.0
	strict = model or hits == len(terms)
	return {"score": round(score, 3), "hits": hits, "model": model, "strict": strict}


def match_items(
	items: Iterable[dict],
	terms: list[str],
	whole: str = "",
	limit: int | None = None,
	single: bool | None = None,
) -> dict:
	"""Every item in ``items`` scored, the best first. Answers ``{"items", "partial"}``.

	Each item is a dict with ``item_code``, ``item_name``, ``item_group``, ``description`` and
	``part_nos`` (its Item Supplier part numbers). Each answer row is that dict without
	``description``, plus ``score``, ``hits``, ``model`` and ``strict``. Strict matches only, unless
	there are none. Then the items matching the most terms (at least half of them, and at least one)
	come back, with ``partial`` true. ``limit`` None returns every match: the caller reads evidence for
	all of them and caps only what it shows. ``single`` defaults to "one term"."""
	if not terms and not whole:
		return {"items": [], "partial": False}
	scored = []
	for item in items:
		s = score_item(item, terms, whole, len(terms) <= 1 if single is None else single)
		if s:
			row = {k: v for k, v in item.items() if k != "description"}
			row.update(s)
			scored.append(row)
	strict = [r for r in scored if r["strict"]]
	partial = False
	if strict:
		chosen = strict
	else:
		need = max(1, (len(terms) + 1) // 2)
		chosen = [r for r in scored if r["hits"] >= need]
		partial = bool(chosen)
	chosen.sort(key=lambda r: (not r["model"], -r["score"], str(r.get("item_code") or "")))
	return {"items": chosen[:limit] if limit else chosen, "partial": partial}


def category_terms(items: list[dict], limit: int = 2) -> list[str]:
	"""The leading word of the best items' names (POL-0602 puts the category first), most common
	first. A model search ("EQKT750") matches no group, but its item is a PUMP, and the pump groups
	are where to look for another supplier."""
	counts: dict[str, int] = {}
	for row in items[:10]:
		name_words = words(row.get("item_name"))
		if name_words and not _DIGIT.search(name_words[0]) and len(name_words[0]) > 2:
			counts[name_words[0]] = counts.get(name_words[0], 0) + 1
	return [w for w, _n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))][:limit]


# --- groups --------------------------------------------------------------------


def group_word_matches(term: str, word: str) -> bool:
	"""Does a search term name a Supplier Group's word? The stems are equal, or one starts the other
	and the shorter has five letters or more and is at least three quarters of the longer (FASTEN
	and FASTENER, ELECTRIC and ELECTRICAL). Stricter than an item match, which takes any word start:
	a group name is a label, and WATER must not file a pump under Waterproofing, nor TOOL under
	Software Tools."""
	if term == word:
		return True
	short, long_ = sorted((term, word), key=len)
	return long_.startswith(short) and len(short) >= 5 and len(short) * 4 >= len(long_) * 3


def match_groups(group_names: Iterable[str], terms: list[str]) -> list[dict]:
	"""Supplier Groups whose words match ``terms``, ignoring :data:`GROUP_FILLER`. Answers
	``[{"name", "hits", "coverage"}]``, most terms matched first, then the group most of whose own
	words matched ("Pumps" before "Fountain Supplies - Pumps, Filters"), then by name."""
	out = []
	for name in group_names:
		g_words = [w for w in words(name) if w not in GROUP_FILLER]
		if not g_words:
			continue
		hits = 0
		for term in terms:
			if term in GROUP_FILLER or len(term) < 3:
				continue
			if any(group_word_matches(alt, w) for alt in synonyms_of(term) for w in g_words):
				hits += 1
		if hits:
			out.append({"name": name, "hits": hits, "coverage": round(hits / len(g_words), 3)})
	out.sort(key=lambda g: (-g["hits"], -g["coverage"], g["name"]))
	return out


# --- suppliers -----------------------------------------------------------------


def supplier_key(name) -> str:
	"""The identity two supplier records share when they are the same company. Uppercased, legal
	and web suffixes dropped (:data:`_SUPPLIER_SUFFIXES`), then everything but A-Z and 0-9 removed.
	A name that is nothing but suffixes keeps its own squashed form."""
	upper = re.sub(r"[^A-Z0-9]+", " ", str(name or "").upper())
	key = squash(_SUPPLIER_SUFFIXES.sub(" ", upper))
	return key or squash(upper)


def _as_date(value):
	if not value:
		return None
	if isinstance(value, _dt.datetime):
		return value.date()
	if isinstance(value, _dt.date):
		return value
	try:
		return _dt.date.fromisoformat(str(value)[:10])
	except ValueError:
		return None


def recency_factor(last, today) -> float:
	"""1.0 for evidence from the last year, 0.8 up to three years, 0.6 older or undated."""
	last, today = _as_date(last), _as_date(today)
	if not last or not today:
		return 0.6
	days = (today - last).days
	if days <= 366:
		return 1.0
	if days <= 3 * 366:
		return 0.8
	return 0.6


#: Points for one supplier–item pair, by kind of evidence. A purchase order is the strongest: we
#: bought it there. So is a purchase invoice billed with no order. A receipt is nearly as strong. A
#: quote, an Item Supplier row, a default supplier or a buying price says they carry it, which is weaker.
EVIDENCE_POINTS = {
	"po": 3.0,
	"po_more": 1.0,
	"pi": 3.0,
	"pi_more": 1.0,
	"pr": 2.0,
	"pr_more": 0.5,
	"sq": 1.5,
	"listed": 2.0,
	"part_no": 0.5,
	"default": 1.5,
	"price": 1.0,
}

#: Line counts on a supplier–item pair. The first three are purchases (the "history" tier).
BOUGHT_KEYS = ("po_lines", "pi_lines", "pr_lines")
COUNT_KEYS = (*BOUGHT_KEYS, "sq_lines")
#: Yes/no evidence that a supplier carries an item.
FLAG_KEYS = ("listed", "default", "price")

#: Tiers, best first. Every supplier with history ranks above every supplier without it.
TIER_HISTORY, TIER_CARRIES, TIER_GROUP = "history", "carries", "group"
_TIER_ORDER = {TIER_HISTORY: 0, TIER_CARRIES: 1, TIER_GROUP: 2}


def pair_points(ev: dict, today) -> float:
	"""The points one supplier–item evidence row earns (:data:`EVIDENCE_POINTS`). Bought and
	received evidence is scaled by :func:`recency_factor`."""
	p = EVIDENCE_POINTS
	history = 0.0
	for key, base, more in (
		("po_lines", "po", "po_more"),
		("pi_lines", "pi", "pi_more"),
		("pr_lines", "pr", "pr_more"),
	):
		n = int(ev.get(key) or 0)
		if n:
			history += p[base] + p[more] * min(n - 1, 4)
	history *= recency_factor(ev.get("last_date"), today)
	carries = 0.0
	if ev.get("sq_lines"):
		carries += p["sq"]
	if ev.get("listed"):
		carries += p["listed"] + (p["part_no"] if ev.get("part_no") else 0.0)
	if ev.get("default"):
		carries += p["default"]
	if ev.get("price"):
		carries += p["price"]
	return history + carries


def blank_pair(code) -> dict:
	"""A supplier–item row before any evidence is added: every key present, so each row the
	search returns has the same shape whether or not a price was ever paid."""
	row = {
		"item_code": code,
		"part_no": "",
		"last_date": None,
		"last_rate": None,
		"uom": None,
		"currency": None,
	}
	row.update(dict.fromkeys(COUNT_KEYS, 0))
	row.update(dict.fromkeys(FLAG_KEYS, False))
	return row


def bought(row: dict) -> bool:
	"""Did we buy this pair's item from this supplier (an order, an invoice or a receipt)?"""
	return any(row.get(k) for k in BOUGHT_KEYS)


def _merge_pair(into: dict, row: dict) -> None:
	"""Add one evidence row to another for the same item: counts summed, flags OR-ed, the first part
	number kept, the newer date kept, and the price (with its unit and currency) of the newer
	purchase, or the only price there is."""
	for k in COUNT_KEYS:
		into[k] = int(into.get(k) or 0) + int(row.get(k) or 0)
	for k in FLAG_KEYS:
		into[k] = bool(into.get(k)) or bool(row.get(k))
	into["part_no"] = into.get("part_no") or row.get("part_no") or ""
	newer = _later(into.get("last_date"), row.get("last_date"))
	if row.get("last_rate") and (newer == row.get("last_date") or not into.get("last_rate")):
		into["last_rate"], into["uom"], into["currency"] = (
			row.get("last_rate"),
			row.get("uom"),
			row.get("currency"),
		)
	into["last_date"] = newer


def _item_weight(item: dict | None) -> float:
	if not item:
		return 0.5
	if item.get("model"):
		return 1.0
	return 0.9 if item.get("strict") else 0.5


def _later(a, b):
	da, db = _as_date(a), _as_date(b)
	if not da:
		return b
	if not db:
		return a
	return a if da >= db else b


def rank_suppliers(
	evidence: Iterable[dict],
	items: list[dict],
	suppliers: dict[str, dict],
	group_hits: dict[str, int] | None = None,
	name_hits: dict[str, int] | None = None,
	term_count: int = 1,
	today=None,
	limit: int = SUPPLIERS_MAX,
	items_per_supplier: int | None = ITEMS_PER_SUPPLIER,
) -> list[dict]:
	"""Suppliers for a search, best first, duplicates folded.

	* ``evidence`` holds one row per supplier–item pair: ``supplier``, ``item_code``, ``po_lines``,
	  ``pi_lines``, ``pr_lines``, ``sq_lines``, ``listed``, ``part_no``, ``default``, ``price``,
	  ``last_date``, ``last_rate``, ``uom``, ``currency``.
	* ``items`` are :func:`match_items` rows, used to weight each pair.
	* ``suppliers`` maps a supplier's docname to ``{"supplier_name", "groups", "disabled"}``, where
	  ``groups`` is its primary group first and then the others.
	* ``group_hits`` and ``name_hits`` map a supplier's docname to the number of terms its groups,
	  or its name, matched.
	* ``items_per_supplier`` caps each row's ``items`` (None: all of them, for one supplier's history).

	Each answer row is ``{"supplier", "supplier_name", "aliases", "groups", "tier", "score",
	"last_date", "items", "item_count", "bought_count", "group_match", "name_match", "disabled"}``. ``items`` are this supplier's
	matched items, best first, each the evidence row's keys plus ``item_name``."""
	group_hits = group_hits or {}
	name_hits = name_hits or {}
	item_by_code = {i.get("item_code"): i for i in items}
	term_count = max(1, int(term_count or 1))

	# Pairs gathered per supplier record, merging the same item seen twice.
	per_supplier: dict[str, dict[str, dict]] = {}
	for ev in evidence:
		sup = ev.get("supplier")
		code = ev.get("item_code")
		if not sup or not code or sup not in suppliers:
			continue
		_merge_pair(per_supplier.setdefault(sup, {}).setdefault(code, blank_pair(code)), ev)

	candidates = (
		set(per_supplier) | {s for s, n in group_hits.items() if n} | {s for s, n in name_hits.items() if n}
	)
	clusters: dict[str, list[str]] = {}
	for sup in candidates:
		if sup not in suppliers:
			continue
		info = suppliers[sup]
		clusters.setdefault(supplier_key(info.get("supplier_name") or sup), []).append(sup)

	out = []
	for members in clusters.values():
		pairs: dict[str, dict] = {}
		for sup in members:
			for code, row in per_supplier.get(sup, {}).items():
				_merge_pair(pairs.setdefault(code, blank_pair(code)), row)
		score = 0.0
		history = carries = False
		for code, row in pairs.items():
			score += pair_points(row, today) * _item_weight(item_by_code.get(code))
			history = history or bought(row)
			carries = carries or bool(row.get("sq_lines") or any(row.get(k) for k in FLAG_KEYS))
		g_hit = max(int(group_hits.get(s) or 0) for s in members)
		n_hit = max(int(name_hits.get(s) or 0) for s in members)
		score += 0.5 * min(g_hit, term_count) / term_count + 0.5 * min(n_hit, term_count) / term_count
		tier = TIER_HISTORY if history else TIER_CARRIES if carries else TIER_GROUP

		def _weight(sup):
			return (
				sum(pair_points(r, today) for r in per_supplier.get(sup, {}).values()),
				not suppliers[sup].get("disabled"),
				bool(suppliers[sup].get("groups")),
				-len(sup),
			)

		lead = sorted(members, key=lambda s: (_weight(s), s), reverse=True)[0]
		groups: list[str] = []
		for sup in [lead] + [m for m in members if m != lead]:
			for g in suppliers[sup].get("groups") or ():
				if g and g not in groups:
					groups.append(g)
		item_rows = []
		for code, row in pairs.items():
			item = item_by_code.get(code) or {}
			r = dict(row)
			r["item_name"] = item.get("item_name") or ""
			r["_rank"] = pair_points(row, today) * _item_weight(item)
			item_rows.append(r)
		item_rows.sort(key=lambda r: (-r["_rank"], str(r["item_code"])))
		for r in item_rows:
			r.pop("_rank", None)
		last = None
		for r in item_rows:
			last = _later(last, r.get("last_date"))
		out.append(
			{
				"supplier": lead,
				"supplier_name": suppliers[lead].get("supplier_name") or lead,
				"aliases": sorted(m for m in members if m != lead),
				"groups": groups,
				"tier": tier,
				"score": round(score, 3),
				"last_date": str(last) if last else None,
				"items": item_rows[:items_per_supplier] if items_per_supplier else item_rows,
				"item_count": len(item_rows),
				"bought_count": sum(1 for r in item_rows if bought(r)),
				"group_match": bool(g_hit),
				"name_match": bool(n_hit),
				"disabled": all(suppliers[m].get("disabled") for m in members),
			}
		)

	def _sort_key(r):
		last = _as_date(r["last_date"])
		return (
			r["disabled"],
			_TIER_ORDER[r["tier"]],
			-r["score"],
			-(last.toordinal() if last else 0),
			r["supplier_name"].upper(),
		)

	out.sort(key=_sort_key)
	return out[:limit]


def reason(row: dict) -> str:
	"""One plain line saying why a supplier is on the list."""
	n = row.get("item_count") or 0
	if row.get("tier") == TIER_HISTORY:
		bought = row.get("bought_count") or 0
		text = f"Bought {bought} matching item{'s' if bought != 1 else ''} from them"
		if n > bought:
			text += f" ({n} matching in all)"
		return text + (f", last on {row['last_date']}" if row.get("last_date") else "")
	if row.get("tier") == TIER_CARRIES:
		return f"Quoted, listed or priced for {n} matching item{'s' if n != 1 else ''}, never bought"
	bits = []
	if row.get("group_match"):
		bits.append("in a matching group")
	if row.get("name_match"):
		bits.append("name matches")
	return ("Only " + " and ".join(bits) if bits else "No evidence") + ", no purchase history"


# --- the AI layer: query expansion only -------------------------------------------

SYSTEM_PROMPT = """You help the purchasing staff of a commercial fountain company (design, build,
service and rental of fountains and water features) search their own records for suppliers.

You are given what a person typed into the search box and the list of the company's Supplier Group
names. Rewrite the search so that a plain keyword search over item names finds what they mean.

Answer with one JSON object and nothing else:
{"terms": [...], "groups": [...], "note": "..."}

- "terms": up to 6 short search phrases (1 to 3 words each) for the kind of item they mean: the
  product's usual names, the words an item name would use (item names lead with the category noun,
  e.g. "PUMP, SUBMERSIBLE"), and well-known product lines or makers of it. No sentences, no prices,
  no quantities. Do not repeat the search itself.
- "groups": names copied EXACTLY from the Supplier Group list that a supplier of this item would
  plausibly be filed under. Up to 6. Never invent a group.
- "note": one short sentence saying how you read the search.
"""


def build_expansion_prompt(query, group_names: Iterable[str]) -> str:
	"""The user message for one expansion: the search, fenced, and the group names."""
	text = _suggest.plain_text(query, QUERY_MAX)
	names = "\n".join(f"- {_suggest.plain_text(g, 80)}" for g in sorted(set(group_names)) if g)
	return f"<search>\n{text}\n</search>\n\nSupplier Groups:\n{names}\n"


def parse_expansion(
	reply, group_names: Iterable[str], original_terms: list[str] | None = None
) -> dict | None:
	"""The model's reply held to its shape: ``{"terms": [[word, ...], ...], "groups": [...],
	"note"}``, or None when the reply is not the JSON asked for.

	Each term phrase becomes its list of :func:`query_terms`. Phrases that add nothing (empty, or
	the same words as the search) are dropped. Groups are kept only when the site has a group of
	that name, compared case-insensitively and returned in the site's spelling."""
	data = _suggest.parse_answer(reply)
	if data is None:
		return None
	original = sorted(set(original_terms or []))
	phrases: list[list[str]] = []
	raw_terms = data.get("terms")
	if isinstance(raw_terms, list):
		for t in raw_terms:
			if not isinstance(t, str):
				continue
			terms = query_terms(_suggest.plain_text(t, AI_TERM_LEN))[:3]
			if not terms or sorted(set(terms)) == original or terms in phrases:
				continue
			phrases.append(terms)
			if len(phrases) >= AI_TERMS_MAX:
				break
	by_upper = {str(g).strip().upper(): g for g in group_names if g}
	groups: list[str] = []
	raw_groups = data.get("groups")
	if isinstance(raw_groups, list):
		for g in raw_groups:
			if not isinstance(g, str):
				continue
			hit = by_upper.get(g.strip().upper())
			if hit and hit not in groups:
				groups.append(hit)
			if len(groups) >= AI_GROUPS_MAX:
				break
	note = _suggest.plain_text(data.get("note"), NOTE_MAX) if isinstance(data.get("note"), str) else ""
	return {"terms": phrases, "groups": groups, "note": note}
