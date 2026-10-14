# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What the guided Item flow decides (form kit P8.1, TASK-2026-02790). **No Frappe, no I/O.**

``api/item_flow.py`` does the reads and writes; every judgement it makes is here, so the bench-free
tests run it on every push. The naming judgements themselves stay in :mod:`item_naming_rules`:
this module only arranges them for the flow's blocks.

--------------------------------------------------------------------------------------
The next PDT or SRV number comes from a block a person chose, never ``MAX() + 1``
--------------------------------------------------------------------------------------

The two block-allocated families are sparse and *semantic*: PDT runs 00xx, 01xx, 02xx, 04xx, 05xx,
07xx, and which hundred a new product belongs in is a judgement about what the product is
(:func:`item_naming_rules.block_report`). So :func:`code_blocks` lists the blocks in use, each with
its lowest free number, plus the first block nobody uses yet for a new kind of product, and the
person picks one. Occupancy is :func:`item_naming_rules.occupancy`, which already handles both
occupancy traps (a taken number with trailing text, and the five-digit QuickBooks family that must
not collapse onto four-digit slots), and counts a number held only by a ``(deleted)`` tombstone as
taken: reissuing it would make the tombstone's history look like the new item's.

--------------------------------------------------------------------------------------
Pick lists come from the catalog, by position
--------------------------------------------------------------------------------------

POL-0602's seven segments are positional and only CATEGORY has a vocabulary (Appendix A,
:data:`item_naming_rules.APPROVED_CATEGORIES`). The other six pick lists are what the live
catalog already uses in that position, most used first, narrowed to names with the same
CATEGORY once one is chosen. :func:`catalog_segments` turns the names into rows the page filters
itself (``public/js/guided_item/naming.js``), so choosing a CATEGORY needs no round trip. Names
past seven segments, tombstones and names with no comma (a single segment says nothing about
position) are left out.
"""

from __future__ import annotations

import re
from typing import Final
from urllib.parse import urlsplit

from erpnext_enhancements.inventory_enhancements import item_naming_rules as rules

#: Longest purchase link an Item Supplier row can hold: ``purchase_url`` is a plain Data field
#: (``patches/add_item_purchase_links.py``), so 140 characters, and a longer one fails the save.
PURCHASE_URL_MAX: Final[int] = 140


def _pad(prefix: str, number: int) -> str:
	return f"{prefix}-{int(number):0{rules.RESERVED_PREFIXES[prefix]}d}"


def code_blocks(codes, prefix: str) -> dict:
	"""The blocks of ``prefix`` (PDT or SRV) in use, each with its lowest free number.

	``codes`` is every code that occupies a number (``item_naming.read_reserved_codes``: every
	Item, and for PDT every Configurable Product too, read regardless of who is asking). Returns
	``{"prefix", "width", "block_size", "blocks": [...], "new_block": {...} | None}``; a block is
	``{"start", "end", "label", "used", "free", "next_free", "next_code"}`` and ``next_code`` is
	None when the block is full. ``new_block`` is the lowest block nobody uses yet.
	"""
	prefix = (prefix or "").strip().upper()
	if prefix not in rules.RESERVED_PREFIXES:
		raise ValueError(f"{prefix!r} is not a block-allocated family")
	width = rules.RESERVED_PREFIXES[prefix]
	occupied = rules.occupancy(codes, prefix)
	starts = sorted({rules.block_bounds(slot)[0] for slot in occupied})
	blocks = []
	for start in starts:
		report = rules.block_report(occupied, start)
		# Number 0 is never offered: PDT-0000 reads as a placeholder, not a product.
		free = [number for number in report["free"] if number]
		blocks.append(
			{
				"start": start,
				"end": report["block_end"],
				"label": f"{_pad(prefix, start)} to {_pad(prefix, report['block_end'])}",
				"used": len(report["occupied"]),
				"free": len(free),
				"next_free": free[0] if free else None,
				"next_code": _pad(prefix, free[0]) if free else None,
			}
		)
	new_block = None
	limit = 10**width
	for start in range(0, limit, rules.BLOCK_SIZE):
		if start not in starts:
			end = min(start + rules.BLOCK_SIZE - 1, limit - 1)
			first = start or 1
			new_block = {
				"start": start,
				"end": end,
				"label": f"{_pad(prefix, start)} to {_pad(prefix, end)}",
				"used": 0,
				"free": end - first + 1,
				"next_free": first,
				"next_code": _pad(prefix, first),
			}
			break
	return {
		"prefix": prefix,
		"width": width,
		"block_size": rules.BLOCK_SIZE,
		"blocks": blocks,
		"new_block": new_block,
	}


def catalog_segments(rows) -> list[list[str]]:
	"""Each usable live Item Name as its segments, for the name builder's pick lists.

	``rows`` are ``{"item_code", "item_name"}``. A segment is uppercased and its spaces
	collapsed, so ``Pvc`` and ``PVC`` count as one choice.
	"""
	out = []
	for row in rows or ():
		code = str(row.get("item_code") or "")
		if rules.DELETED_MARKER in code.lower():
			continue
		name = str(row.get("item_name") or "")
		if "," not in name:
			continue
		parts = [re.sub(r"\s+", " ", part).strip().upper() for part in rules.segments(name)]
		if not parts or len(parts) > rules.MAX_SEGMENTS or not parts[0]:
			continue
		out.append(parts)
	return out


def approved_categories() -> list[str]:
	"""Appendix A's categories (Tier 1 and Tier 2), alphabetical: the CATEGORY pick list."""
	return sorted(rules.APPROVED_CATEGORIES)


# --- the supplier, stock and asset blocks ----------------------------------------------


def _number(value):
	if value in (None, ""):
		return None
	try:
		return float(value)
	except (TypeError, ValueError):
		return "bad"


def purchase_url_problem(url) -> str | None:
	"""Why a purchase link cannot be stored, or None. Blank is fine: no link."""
	text = str(url or "").strip()
	if not text:
		return None
	if len(text) > PURCHASE_URL_MAX:
		return (
			f"The purchase link is {len(text)} characters; it can hold {PURCHASE_URL_MAX}. Use the "
			"product page's short address, without the tracking part after a question mark."
		)
	parts = urlsplit(text)
	if parts.scheme not in ("http", "https") or not parts.netloc or " " in text:
		return "The purchase link must be a web address starting with https:// (or http://)."
	return None


def price_problem(rate) -> str | None:
	"""Why a buying price cannot be recorded, or None. Blank is fine: no price."""
	value = _number(rate)
	if value is None:
		return None
	if value == "bad" or value < 0:
		return "The price must be a number of dollars, 0 or more."
	return None


def reorder_problem(level, qty) -> str | None:
	"""Why a reorder rule cannot be saved, or None. Both blank: no rule.

	ERPNext refuses a level without a quantity (``Item.validate_warehouse_for_reorder``), so it is
	said here, in words, before a save is tried.
	"""
	level_value, qty_value = _number(level), _number(qty)
	if level_value is None and qty_value is None:
		return None
	if "bad" in (level_value, qty_value):
		return "The reorder level and quantity must be numbers."
	if level_value is None:
		return "Say the reorder level too: the stock left in the bin when more should be bought."
	if level_value < 0:
		return "The reorder level cannot be below 0."
	if qty_value is None or qty_value <= 0:
		return "Say how many to buy each time the bin falls to its reorder level."
	return None


def asset_problem(location, amount) -> str | None:
	"""Why an Asset cannot be made from the block, or None.

	ERPNext refuses an Asset with no Net Purchase Amount unless it is a Composite Asset
	(``Asset.validate_asset_values``), and ``location`` is mandatory.
	"""
	if not str(location or "").strip():
		return "Choose where the asset is kept."
	value = _number(amount)
	if value in (None, "bad") or value <= 0:
		return "Say what it cost, more than 0: ERPNext needs the purchase amount for every Asset."
	return None


def client_id(value) -> str:
	"""The page's id for one create, or "" when it is absent or not the shape the page makes.

	The same shape the guided page's own creates use (form kit P4.7): 8 to 64 characters of
	letters, digits and hyphens.
	"""
	text = value.strip() if isinstance(value, str) else ""
	if 8 <= len(text) <= 64 and all(c.isascii() and (c.isalnum() or c == "-") for c in text):
		return text
	return ""
