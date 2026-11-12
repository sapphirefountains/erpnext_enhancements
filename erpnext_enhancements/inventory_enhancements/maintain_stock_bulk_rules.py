# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Which existing items get Maintain Stock turned on, in bulk (v1.803.0). No Frappe, no I/O.

Nik, 2026-11-12: "Go in and check every Maintain Stock for every item to Yes or Checked". The
decided scope is physical items only, and it is not a migrate patch: a person sees the list, can
change any line, and then applies it (the *Switch Items to Maintain Stock* page,
``maintain_stock_bulk.py``). Each item still goes through the single-item switch and every one of
its checks (``maintain_stock.turn_on``).

Every non-stock item gets one of three outcomes, with the reason in words:

* ``switch``  -- a physical item, and every safety check passes.
* ``stay``    -- not something that sits on a shelf: labor, a fee, rent, travel, freight, a
  deposit, an old QuickBooks line, anything in the Service item group, a fixed asset.
* ``blocked`` -- a physical item that one of the safety checks refuses, with that check's words.

**The rule is a guess from the name and the group, and it says so.** Production keeps physical
products (an anemometer, a deck jet) in the same catch-all group as labor lines and "Late fee",
because the QuickBooks import filed everything under one group. So the rule is explicit, in the
order below, each with the reason it gives; it leans toward *stay* when unsure, because a part
left non-stock is exactly where it is today and can be switched any day, while a billing line
turned into a stock item starts expecting stock. And a person's choice on the page beats the rule
either way (:data:`DECISION_SWITCH`, :data:`DECISION_STAY`), except for a fixed asset, which
ERPNext itself refuses to stock.

1. A fixed asset item.
2. A name or code ending in ``(deleted)``: QuickBooks marks a deleted product that way.
3. The Service item group (:data:`SERVICE_GROUPS`).
4. A code starting with ``SRV-``, the company's service codes (:data:`SERVICE_CODE_PREFIXES`).
5. A name that starts with a value stream and a dash, such as ``BUILD - Consulting`` or
   ``Rent - Insurance``, after any leading code (:data:`STREAM_PREFIXES`). Those are QuickBooks
   billing lines.
6. A whole word from :data:`SERVICE_WORDS` in the name or code: labor, fee, rent, travel,
   freight, deposit and their kin. Whole words only: "current" does not match "rent". "Shipping"
   is left out on purpose: on production it only ever described a label and packing peanuts.

Tabs, per ``CLAUDE.md`` (new file).
"""

from __future__ import annotations

import re

OUTCOME_SWITCH = "switch"
OUTCOME_STAY = "stay"
OUTCOME_BLOCKED = "blocked"

#: The outcomes as the page's headings and the downloaded list say them.
OUTCOME_LABELS = {
	OUTCOME_SWITCH: "Will switch",
	OUTCOME_STAY: "Stays non-stock",
	OUTCOME_BLOCKED: "Can't switch yet",
}

#: A person's choice for one item, as stored on its Maintain Stock Decision row.
DECISION_SWITCH = "Switch"
DECISION_STAY = "Stay non-stock"
DECISIONS = (DECISION_SWITCH, DECISION_STAY)

#: A row's progress through an apply, as stored.
STATUS_QUEUED = "Queued"
STATUS_SWITCHED = "Switched"
STATUS_REFUSED = "Refused"

#: Item groups whose items stay non-stock.
SERVICE_GROUPS = ("Service", "Services")

#: Item code prefixes that mark a service line.
SERVICE_CODE_PREFIXES = ("SRV-",)

#: The value streams QuickBooks billing lines are named after ("BUILD - Consulting").
STREAM_PREFIXES = ("build", "design", "service", "rent", "internal", "marketing")

#: Whole words that mark a line as not a part.
SERVICE_WORDS = (
	"labor",
	"labour",
	"fee",
	"fees",
	"rent",
	"rental",
	"travel",
	"mileage",
	"freight",
	"delivery",
	"deposit",
	"deposits",
	"booking",
	"consulting",
	"service",
	"services",
	"tax",
	"tariff",
	"tips",
	"expense",
	"software",
	"subscription",
	"insurance",
)

#: Phrases that mark one, where no single word would.
SERVICE_PHRASES = ("custom amount",)

#: How many items one background job switches before it hands over to the next.
BATCH_SIZE = 50

_DELETED = re.compile(r"\(deleted\)\s*$", re.IGNORECASE)
_LEADING_CODE = re.compile(r"^[A-Za-z]{2,5}-[0-9A-Za-z]{2,6}\s+")
_STREAM = re.compile(r"^(" + "|".join(STREAM_PREFIXES) + r")\s*-", re.IGNORECASE)
_WORD = re.compile(r"[a-z]+")


def _text(value):
	return (value or "").strip() if isinstance(value, str) else ""


def _truthy(value):
	try:
		return bool(int(float(value or 0)))
	except (TypeError, ValueError):
		return bool(value)


def name_rule(item_code, item_name, item_group, is_fixed_asset=0):
	"""``(key, reason)`` for the first rule that says this is not a shelf part, or ``None``.

	``key`` names the rule (``asset``, ``deleted``, ``group``, ``code``, ``stream``, ``word``) so a
	test and the downloaded list can say which one fired.
	"""
	code, name, group = _text(item_code), _text(item_name), _text(item_group)
	if _truthy(is_fixed_asset):
		return "asset", "It is a fixed asset item, and ERPNext keeps those non-stock."
	if _DELETED.search(code) or _DELETED.search(name):
		return "deleted", 'An old QuickBooks line: its name ends in "(deleted)".'
	if group in SERVICE_GROUPS:
		return "group", f"It is in the {group} item group."
	for prefix in SERVICE_CODE_PREFIXES:
		if code.upper().startswith(prefix):
			return "code", f"Its code starts with {prefix}, the service codes."
	for text in (name, code):
		stream = _STREAM.match(_LEADING_CODE.sub("", text))
		if stream:
			return "stream", (
				f'Its name starts with "{stream.group(1).upper()} -": a QuickBooks billing line, not a part.'
			)
	lowered = f"{name} {code}".lower()
	for phrase in SERVICE_PHRASES:
		if phrase in lowered:
			return "word", f'Its name has "{phrase}": not a part.'
	words = set(_WORD.findall(lowered))
	for word in SERVICE_WORDS:
		if word in words:
			return "word", f'Its name has the word "{word}": not a part.'
	return None


def outcome(item, checks, decision=""):
	"""One item's outcome: ``{"outcome", "reason", "rule", "rule_reason", "overridden"}``.

	``item`` carries ``item_code``, ``item_name``, ``item_group`` and ``is_fixed_asset``;
	``checks`` is ``maintain_stock_rules.evaluate``'s list for it; ``decision`` is the person's
	choice (:data:`DECISIONS`) or blank. ``rule`` is what the name rule alone says (``stay`` or
	``switch``), kept so the page can show when a person overrode it.
	"""
	found = name_rule(
		item.get("item_code"), item.get("item_name"), item.get("item_group"), item.get("is_fixed_asset")
	)
	rule = OUTCOME_STAY if found else OUTCOME_SWITCH
	rule_reason = found[1] if found else "A physical item by its name and group."
	out = {"rule": rule, "rule_reason": rule_reason, "overridden": False}

	if found and found[0] == "asset":
		# No choice beats this one: ERPNext refuses a fixed asset that keeps stock.
		return dict(out, outcome=OUTCOME_STAY, reason=rule_reason)
	if decision == DECISION_STAY:
		return dict(
			out,
			outcome=OUTCOME_STAY,
			reason="You chose to keep it non-stock.",
			overridden=rule != OUTCOME_STAY,
		)
	if found and decision != DECISION_SWITCH:
		return dict(out, outcome=OUTCOME_STAY, reason=rule_reason)

	failed = [check.get("detail") or check.get("label") for check in checks or () if not check.get("ok")]
	chosen = decision == DECISION_SWITCH and bool(found)
	if failed or not checks:
		return dict(
			out,
			outcome=OUTCOME_BLOCKED,
			reason=" ".join(failed) or "Its safety checks could not be run.",
			overridden=chosen,
		)
	return dict(
		out,
		outcome=OUTCOME_SWITCH,
		reason="You chose to switch it. Every safety check passes."
		if chosen
		else "Every safety check passes.",
		overridden=chosen,
	)


def blocked_only_by_sales_invoices(checks):
	"""Whether the Sales Invoice check is the one thing in the way."""
	failed = [check.get("key") for check in checks or () if not check.get("ok")]
	return failed == ["sales_invoices"]


def summarize(rows):
	"""``{outcome: count}`` over the preview rows, every outcome present."""
	counts = {key: 0 for key in OUTCOME_LABELS}
	for row in rows or ():
		if row.get("outcome") in counts:
			counts[row["outcome"]] += 1
	return counts


def to_apply(rows, picked=None):
	"""The item codes an apply queues: the ``switch`` rows, limited to ``picked`` when given.

	``picked`` is the list the page showed. The server's own answer always decides; ``picked`` can
	only narrow it, so a stale or edited page can never queue an item the rules would not.
	"""
	picked = None if picked is None else set(picked)
	return [
		row["item_code"]
		for row in rows or ()
		if row.get("outcome") == OUTCOME_SWITCH
		and row.get("status") != STATUS_QUEUED
		and (picked is None or row["item_code"] in picked)
	]


#: The downloaded list's columns: (key, heading).
LIST_COLUMNS = (
	("item_code", "Item"),
	("item_name", "Item name"),
	("item_group", "Item group"),
	("before", "Maintain Stock before"),
	("after", "Maintain Stock after"),
	("outcome_label", "Outcome"),
	("reason", "Why"),
	("decision", "Your choice"),
	("status", "Status"),
	("result", "Result"),
)


def list_rows(rows):
	"""The before-and-after list: one dict a row, keyed like :data:`LIST_COLUMNS`.

	``after`` is what the item will be once the list is applied (or is, for a row already
	switched or refused).
	"""
	out = []
	for row in rows or ():
		status = row.get("status") or ""
		if status == STATUS_SWITCHED:
			after = "On"
		elif status == STATUS_REFUSED:
			after = "Off"
		else:
			after = "On" if row.get("outcome") == OUTCOME_SWITCH else "Off"
		out.append(
			{
				"item_code": row.get("item_code") or "",
				"item_name": row.get("item_name") or "",
				"item_group": row.get("item_group") or "",
				"before": "Off",
				"after": after,
				"outcome_label": OUTCOME_LABELS.get(row.get("outcome"), row.get("outcome") or ""),
				"reason": row.get("reason") or "",
				"decision": row.get("decision") or "",
				"status": status,
				"result": row.get("result") or "",
			}
		)
	return out


def csv_cell(value):
	"""One CSV cell, quoted, and safe to open in a spreadsheet: a cell that starts with ``=``,
	``+``, ``-`` or ``@`` gets a leading apostrophe so it is read as text, not a formula."""
	text = "" if value is None else str(value)
	if text[:1] in ("=", "+", "-", "@"):
		text = "'" + text
	return '"' + text.replace('"', '""') + '"'


def to_csv(rows):
	"""The before-and-after list as CSV text (:func:`list_rows`, :data:`LIST_COLUMNS`)."""
	lines = [",".join(csv_cell(heading) for _key, heading in LIST_COLUMNS)]
	for row in list_rows(rows):
		lines.append(",".join(csv_cell(row[key]) for key, _heading in LIST_COLUMNS))
	return "\r\n".join(lines) + "\r\n"


def refused_result(message):
	"""A refusal as the row keeps it: plain words, no markup, short enough for the column."""
	text = re.sub(r"<[^>]+>", " ", str(message or ""))
	text = re.sub(r"\s+", " ", text).strip()
	return text[:1000] or "The switch was refused."
