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
  deposit, an old QuickBooks line, anything in the Service item group, a fixed asset. And
  anything the rule cannot tell: a placeholder, a copy, an item with no real name, office
  supplies, a line in the QuickBooks catch-all group that carries no product code.
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
3. An item this app itself keeps non-stock on purpose (:data:`RESERVED_NON_STOCK`).
4. **Nothing says what it is** (:func:`unclear`): a placeholder (``XX`` where digits belong,
   "test item", "TBD"), a copy (a name ending in ``- copy``), or no name at all (only a number,
   or a bare code repeated as the name). It may well be a part, but nobody can tell from here,
   and a switch can't be undone from the form once the item is used.
5. The Service item group (:data:`SERVICE_GROUPS`).
6. A group of things bought to be used up, not kept as stock (:data:`CONSUMABLE_GROUPS`:
   Office). **Pending Nik's answer, 2026-11-12**: the manager's recommended default. To let
   office items switch, empty that tuple; nothing else changes.
7. A code starting with ``SRV-``, the company's service codes (:data:`SERVICE_CODE_PREFIXES`).
8. A name that starts with a value stream and a dash, such as ``BUILD - Consulting`` or
   ``Rent - Insurance``, after any leading code (:data:`STREAM_PREFIXES`). Those are QuickBooks
   billing lines.
9. A whole word from :data:`SERVICE_WORDS` in the name or code: labor, fee, rent, travel,
   freight, deposit, installation, permit, warranty and their kin. Whole words only: "current"
   does not match "rent".
10. The QuickBooks catch-all group (:data:`CATCH_ALL_GROUPS`) without a product code
    (:data:`PRODUCT_CODE_PREFIXES`). The import filed products and services alike there, and a
    vendor portal's name reads no differently from a part's. A ``PDT-`` code is the one sign
    that a line there is a product.

**Words left out on purpose, each because production has a part it would catch:** "shipping"
(a shipping label, packing peanuts), "tips" (nozzle tips), "repair" (a repair coupling),
"winterizing" (a winterizing plug), "charge" (a charge controller), "cleaner" (a bottle of it).
"Repairs", "winterization", "surcharge" and "cleaning" are in.

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

#: Item groups of things bought to be used, not kept as stock: paper, ink, soap, snacks.
#: PENDING NIK (2026-11-12): the manager's recommended default. Make this ``()`` to let them switch.
CONSUMABLE_GROUPS = ("Office",)

#: The group the QuickBooks import filed everything under, products and services alike.
CATCH_ALL_GROUPS = ("All Item Groups",)

#: Code prefixes that mark a line in a catch-all group as a product.
PRODUCT_CODE_PREFIXES = ("PDT-",)

#: Items this app keeps non-stock on purpose. "Customer Water Feature" is the one item every
#: customer feature's Serial No hangs from (``patches/migrate_assets_to_serial_no``); it is a
#: record, not a part.
RESERVED_NON_STOCK = ("Customer Water Feature",)

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
	"expense",
	"software",
	"subscription",
	"insurance",
	# Added after reading every name on production (v1.803.0 review). None of these is in a
	# part's name there today; each is here so a new service line is caught.
	"installation",
	"cleaning",
	"repairs",
	"maintenance",
	"permit",
	"permits",
	"discount",
	"surcharge",
	"retainer",
	"warranty",
	"winterization",
	"subcontractor",
	"subcontracted",
	"misc",
	"miscellaneous",
	"design",
	"programming",
	"training",
	"inspection",
	"license",
	"membership",
	"commission",
	"refund",
	"credit",
	"handling",
	"overtime",
	"hourly",
	"lodging",
	"hotel",
	"airfare",
	"postage",
	"donation",
	"gratuity",
	"markup",
	"allowance",
	"contingency",
	"calibration",
	"disposal",
)

#: Phrases that mark one, where no single word would.
SERVICE_PHRASES = ("custom amount", "per diem")

#: Words and phrases that say an item is a stand-in, not a real one.
PLACEHOLDER_WORDS = ("placeholder", "tbd", "dummy")
PLACEHOLDER_PHRASES = ("test item", "do not use")

#: How many items one background job switches before it hands over to the next.
BATCH_SIZE = 50

_DELETED = re.compile(r"\(deleted\)\s*$", re.IGNORECASE)
_LEADING_CODE = re.compile(r"^[A-Za-z]{2,5}-[0-9A-Za-z]{2,6}\s+")
_STREAM = re.compile(r"^(" + "|".join(STREAM_PREFIXES) + r")\s*-", re.IGNORECASE)
_WORD = re.compile(r"[a-z]+")
#: "XX" standing where digits belong, as in ``PDT-00XX``: upper case, straight after a digit, and
#: ending the code. Not a size ("XX-LARGE") and not a model number ("952XL").
_XX = re.compile(r"\dX{2,}(?![A-Za-z0-9])")
#: A name ending in "- copy", "(copy)" or "- copy 2".
_COPY = re.compile(r"[-(]\s*copy(?:\s*\d+)?\)?\s*$", re.IGNORECASE)


def _text(value):
	return (value or "").strip() if isinstance(value, str) else ""


def _truthy(value):
	try:
		return bool(int(float(value or 0)))
	except (TypeError, ValueError):
		return bool(value)


def unclear(item_code, item_name):
	"""Why nothing says what this item is, or ``None`` when its name is a real one.

	A placeholder, a copy, or an item with no name of its own. Such an item is never switched by
	the rule: it stays non-stock until a person names it or chooses to switch it.
	"""
	code, name = _text(item_code), _text(item_name)
	text = name or code
	lowered = f"{name} {code}".lower()
	# Only when the name is the code and nothing more: a catalog part may carry a wildcard model
	# number in its name ("... (LR375XXX)"), and that is a real part.
	if name == code and _XX.search(code):
		return 'Its code has "XX" where a number belongs, and it has no other name: a placeholder.'
	for phrase in PLACEHOLDER_PHRASES:
		if phrase in lowered:
			return f'Its name has "{phrase}": a placeholder.'
	words = set(_WORD.findall(lowered))
	for word in PLACEHOLDER_WORDS:
		if word in words:
			return f'Its name has the word "{word}": a placeholder.'
	if _COPY.search(code) or _COPY.search(name):
		return 'Its name ends in "copy": a duplicate of another item.'
	if not re.search(r"[A-Za-z]", text):
		return "It has no name, only a number, so nothing says what it is."
	if name == code and " " not in code and re.search(r"\d", code):
		return "Its name is only its code, so nothing says what it is."
	return None


def name_rule(item_code, item_name, item_group, is_fixed_asset=0):
	"""``(key, reason)`` for the first rule that says this is not a shelf part, or ``None``.

	``key`` names the rule (``asset``, ``deleted``, ``reserved``, ``unclear``, ``group``,
	``consumable``, ``code``, ``stream``, ``word``, ``catch_all``) so a test and the downloaded
	list can say which one fired.
	"""
	code, name, group = _text(item_code), _text(item_name), _text(item_group)
	if _truthy(is_fixed_asset):
		return "asset", "It is a fixed asset item, and ERPNext keeps those non-stock."
	if _DELETED.search(code) or _DELETED.search(name):
		return "deleted", 'An old QuickBooks line: its name ends in "(deleted)".'
	if code in RESERVED_NON_STOCK:
		return "reserved", "This app uses it as a record, not a part, and needs it non-stock."
	vague = unclear(code, name)
	if vague:
		return "unclear", f"{vague} Choose Switch if it is a part."
	if group in SERVICE_GROUPS:
		return "group", f"It is in the {group} item group."
	if group in CONSUMABLE_GROUPS:
		return "consumable", (
			f"It is in the {group} item group: supplies bought to be used, not kept as stock. "
			"Choose Switch if we do count it."
		)
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
	if group in CATCH_ALL_GROUPS and not code.upper().startswith(PRODUCT_CODE_PREFIXES):
		return "catch_all", (
			f"It is in {group}, where the QuickBooks import filed products and services alike, and "
			f"its code is not a product code ({', '.join(PRODUCT_CODE_PREFIXES)}). "
			"Choose Switch if it is a part."
		)
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


def best_row(rows):
	"""The one Maintain Stock Decision row that speaks for an item when it has several.

	Merging two Items re-points both rows at the survivor. A row that says Switched wins (it is
	what the Delivery Note guard reads), then one still Queued or Refused, then the newest.
	``None`` for no rows.
	"""

	def rank(row):
		status = row.get("status") or ""
		return (status == STATUS_SWITCHED, bool(status), str(row.get("modified") or ""))

	rows = list(rows or ())
	return max(rows, key=rank) if rows else None


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
