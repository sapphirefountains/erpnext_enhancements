# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What a party-linked record should be called. **No Frappe, no I/O.**

Three doctypes name themselves after the party they belong to, and until now none of them
was checked. :mod:`party_naming` does the reads; every judgement is here so it can be
executed rather than inspected — the split :mod:`inventory_enhancements.item_naming_rules`
and :mod:`chat.governance.drift_rules` already use, and for the reason those give: there is
no Frappe integration-test job in CI, so bench-free code is the only code that runs on every
push.

Nothing here raises and nothing decides what a finding *means*. Advisory by construction:
there is no ``validate`` hook on any of these three doctypes and nothing blocks a save.

--------------------------------------------------------------------------------------
Two shapes, not one — and Address is the one people get wrong
--------------------------------------------------------------------------------------

**Project and Opportunity** carry the whole thing in one free-text field::

    Hess Construction - Colony 256 Cascading Pillar
    ^^^^^^^^^^^^^^^^^   ^^^^^^^^^^^^^^^^^^^^^^^^^^
    party               what we are doing for them

**Address does not, and appending the type by hand is a defect rather than the rule.**
Frappe's ``Address.autoname`` already builds the record name for you (verified against
frappe version-16, ``frappe/contacts/doctype/address/address.py``)::

    self.name = cstr(self.address_title).strip() + "-" + cstr(_(self.address_type)).strip()

So ``address_title`` should be **the party and nothing else** — set it to
``Hess Construction LLC`` and the name comes out ``Hess Construction LLC-Billing`` on its
own. A title of ``Project Site Splashpad - West Jordan Parks`` is wrong twice over: it is not
the party, and the qualifier it carries is one frappe was going to add anyway.

Two consequences of that ``autoname`` worth knowing before anyone proposes a rename:

* The separator is ``-``, not `` - ``. That is frappe core, not ours.
* ``autoname`` runs **once, at insert**. Change ``address_type`` afterwards and the name
  keeps the old suffix — eight live records already disagree with themselves this way. That
  is :data:`ADDRESS_TYPE_STALE`, and it is a NOTE rather than a FIX because fixing it means
  a rename, and renames ripple through every Dynamic Link and linked document.

--------------------------------------------------------------------------------------
What "matches the party" means, and why it is not equality
--------------------------------------------------------------------------------------

Equality was the obvious rule and the data refuses it: of 164 Projects that already use the
separator, only 55 lead with the customer name verbatim, while 42 lead with a **shortening**
of it — ``West Jordan`` for *West Jordan Parks and Recreation*, ``Hess Construction`` for
*Hess Construction LLC*. Those 42 are good practice, not errors; nobody wants
``West Jordan Parks and Recreation - Splash Pad Controller``.

So the test is a **leading word-sequence match** in either direction: normalise both sides
to a list of alphanumeric words and pass when the shorter is a prefix of the longer. See
:func:`party_matches`, which explains why it is words rather than characters.
"""

from __future__ import annotations

import re
from typing import Final

# --- severities and verdict ----------------------------------------------------

STOP: Final[str] = "STOP"
FIX: Final[str] = "FIX"
NOTE: Final[str] = "NOTE"
SEVERITIES: Final[tuple[str, ...]] = (STOP, FIX, NOTE)

VERDICT_PASS: Final[str] = "PASS"
VERDICT_FIX: Final[str] = "FIX"
VERDICT_STOP: Final[str] = "STOP"

#: Worst first, so a report can order by consequence rather than alphabetically.
SEVERITY_ORDER: Final[dict[str, int]] = {STOP: 0, FIX: 1, NOTE: 2, VERDICT_PASS: 3}

#: Comma-and-space is the Item schema's separator; this one is a spaced hyphen, because
#: these names are prose rather than a segment list. `` - `` with the spaces: a bare hyphen
#: is ambiguous against hyphenated party names (*Ana Mendez-Law* is one word, not two).
SEPARATOR: Final[str] = " - "

# --- the two shapes ------------------------------------------------------------

#: ``<Party> - <what we are doing>`` in one field. Project, Opportunity.
SHAPE_PARTY_QUALIFIER: Final[str] = "party_qualifier"

#: ``<Party>`` alone; the qualifier is appended by the framework. Address.
SHAPE_PARTY_ONLY: Final[str] = "party_only"

#: Project types that are customer-facing. Anything else is an internal project with no
#: customer — Overhead, Internal, Other, Group Projects, and the 72 with no type at all —
#: and is skipped entirely rather than flagged.
#:
#: This is a **field that already exists** rather than a list this module maintains, which
#: is the whole reason it is trustworthy: leftover stage templates (`Stage 1 - Predesign`)
#: and the software backlog that lives in Project (`Better filtering - 1.5`) both carry no
#: project_type and fall out on their own, with nobody having to enumerate them.
#:
#: ``Rent`` is listed although WI-065 has already renamed it to ``Events`` and no live row
#: uses it. Accepting a value that cannot appear costs nothing; a half-applied rename that
#: made the check start flagging real work would cost a morning.
CUSTOMER_FACING_PROJECT_TYPES: Final[frozenset[str]] = frozenset(
	{"Build", "Design", "Events", "Rent", "Service"}
)

#: The doctypes this module knows, and what to read on each.
#:
#: ``party_fields`` is ordered — the first one with a value wins. Opportunity carries both
#: ``party_name`` (the link) and ``customer_name`` (the label), and they disagree on records
#: where the party was renamed, so the link is asked first.
DOCTYPES: Final[dict[str, dict]] = {
	"Project": {
		"field": "project_name",
		"label": "Project Name",
		"shape": SHAPE_PARTY_QUALIFIER,
		"party_fields": ("customer",),
		"party_label": "Customer",
	},
	"Opportunity": {
		"field": "title",
		"label": "Title",
		"shape": SHAPE_PARTY_QUALIFIER,
		"party_fields": ("party_name", "customer_name"),
		"party_label": "Party",
	},
	"Address": {
		"field": "address_title",
		"label": "Address Title",
		"shape": SHAPE_PARTY_ONLY,
		# Supplied by the caller from Dynamic Link rather than a column on the row.
		"party_fields": ("party",),
		"party_label": "Linked party",
	},
}

#: Link doctypes an Address may name itself after, best first. An Address can carry several
#: links at once — 42 live records do — and frappe's own autoname just takes ``links[0]``,
#: whatever that happens to be. This orders them by who the address actually belongs to.
ADDRESS_PARTY_DOCTYPES: Final[tuple[str, ...]] = ("Customer", "Supplier", "Project", "Opportunity")

#: Qualifiers that say nothing. Deliberately short and uncontroversial — a long list would
#: start rejecting legitimate shorthand, and the cost of a false positive here is that
#: somebody stops reading the findings.
VAGUE_QUALIFIERS: Final[frozenset[str]] = frozenset(
	{"TBD", "TBA", "NA", "NONE", "X", "XX", "XXX", "TEST", "NEW", "UNKNOWN", "???", "-"}
)

# --- finding codes -------------------------------------------------------------

VALUE_MISSING: Final[str] = "value_missing"
PARTY_MISSING: Final[str] = "party_missing"
SEPARATOR_MISSING: Final[str] = "separator_missing"
PARTY_PREFIX_MISMATCH: Final[str] = "party_prefix_mismatch"
QUALIFIER_MISSING: Final[str] = "qualifier_missing"
QUALIFIER_VAGUE: Final[str] = "qualifier_vague"
EDGE_WHITESPACE: Final[str] = "edge_whitespace"
DOUBLE_SPACE: Final[str] = "double_space"
TITLE_CARRIES_QUALIFIER: Final[str] = "title_carries_qualifier"
ADDRESS_TYPE_STALE: Final[str] = "address_type_stale"
DUPLICATE_NORMALISED: Final[str] = "duplicate_normalised"

SEVERITY: Final[dict[str, str]] = {
	VALUE_MISSING: FIX,
	PARTY_MISSING: FIX,
	SEPARATOR_MISSING: FIX,
	PARTY_PREFIX_MISMATCH: FIX,
	QUALIFIER_MISSING: FIX,
	QUALIFIER_VAGUE: FIX,
	EDGE_WHITESPACE: FIX,
	DOUBLE_SPACE: FIX,
	TITLE_CARRIES_QUALIFIER: FIX,
	ADDRESS_TYPE_STALE: NOTE,
	DUPLICATE_NORMALISED: STOP,
}

CODES: Final[tuple[str, ...]] = tuple(SEVERITY)

#: What each finding is grounded in, and for the heuristics the false-positive class they
#: are known to have. Not documentation: :func:`missing_evidence` reads it and a code added
#: without an entry cannot be emitted.
EVIDENCE: Final[dict[str, str]] = {
	VALUE_MISSING: "the field that carries the name is empty, so there is nothing to check.",
	PARTY_MISSING: (
		"the record is one of the customer-facing kinds and no party is linked. 130 of the 543 "
		"customer-facing Projects are in this state, and it is usually the more useful finding "
		"of the two: the name is often already right and it is the link that was never set, "
		"which no amount of renaming fixes."
	),
	SEPARATOR_MISSING: (
		"the value carries no ' - ', so it cannot express party-and-qualifier. Spaced on "
		"purpose: a bare hyphen is ambiguous against hyphenated party names like "
		"'Ana Mendez-Law'."
	),
	PARTY_PREFIX_MISMATCH: (
		"what comes before the separator is not the linked party, even allowing a shortening "
		"of it. The live case is 'Landmark - Millcreek Commons Phase 2 Controller' against a "
		"customer of 'CEM Aquatics' — named for the site or the general contractor rather "
		"than for who pays. Known false-positive class: a party whose legal name shares no "
		"leading word with the name everybody uses for them."
	),
	QUALIFIER_MISSING: "there is a separator but nothing after it.",
	QUALIFIER_VAGUE: (
		"the qualifier is a placeholder rather than a description. The list is deliberately "
		"short — a long one starts rejecting legitimate shorthand, and a check that fires on "
		"correct input is one people stop reading."
	),
	EDGE_WHITESPACE: (
		"leading or trailing whitespace. 25 live Opportunity titles have it. Compared by "
		"length, in Python: MariaDB's PAD SPACE collation makes the SQL form of this test "
		"(`title <> TRIM(title)`) always false, so it reports clean on data that is broken."
	),
	DOUBLE_SPACE: "a double space inside the value.",
	TITLE_CARRIES_QUALIFIER: (
		"an Address title that already contains ' - ' plus what looks like an address type. "
		"Frappe's autoname appends the type itself, so this produces a doubled qualifier — "
		"'Site - Office' typed Billing becomes 'Site - Office-Billing'. The title should be "
		"the party alone."
	),
	ADDRESS_TYPE_STALE: (
		"the record name ends in an address type that is not this record's current one. "
		"`autoname` runs once at insert, so changing the type afterwards leaves the name "
		"behind — 'DELIVERY-Billing' is typed Shipping. NOTE rather than FIX because the "
		"remedy is a rename, and renames ripple through every Dynamic Link and every linked "
		"Sales Order, Purchase Order and Invoice."
	),
	DUPLICATE_NORMALISED: (
		"another record of the same doctype has an identical name after normalisation. On a "
		"Project or an Opportunity that usually means the same job was entered twice."
	),
}

# --- normalisation -------------------------------------------------------------

_WORD = re.compile(r"[A-Z0-9]+")
_NON_ALNUM = re.compile(r"[^A-Z0-9]+")

#: The one normalisation used for every identity question here. Named because a duplicate
#: count is a property of the rule, not of the data, and quoting one without the other is
#: unfalsifiable.
NORMALISATION: Final[str] = "uppercase, then remove every character that is not A-Z or 0-9"


def normalise(value: str | None) -> str:
	"""Fold a value to its identity string. See :data:`NORMALISATION`."""
	return _NON_ALNUM.sub("", (value or "").upper())


def words(value: str | None) -> tuple[str, ...]:
	"""Alphanumeric words, uppercased. ``Ore Designs, Inc.`` -> ``('ORE','DESIGNS','INC')``."""
	return tuple(_WORD.findall((value or "").upper()))


def party_matches(candidate: str | None, party: str | None) -> bool:
	"""Is ``candidate`` the party, allowing either to be a shortening of the other?

	**Words, not characters, and that is the whole design.** A character-prefix test passes
	``O`` against ``Ore Designs, Inc.`` and would wave through any name that happens to start
	with the same letter. Comparing word sequences means ``West Jordan`` matches
	*West Jordan Parks and Recreation* and ``Ore`` matches *Ore Designs, Inc.*, while
	``Landmark`` against *CEM Aquatics* fails as it should.

	Symmetric, because both over- and under-specification are recognisable: somebody writing
	``Hess Construction LLC`` where the customer is recorded as ``Hess Construction`` has not
	made a mistake worth a finding.
	"""
	left, right = words(candidate), words(party)
	if not left or not right:
		return False
	shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
	return longer[: len(shorter)] == shorter


def split(value: str | None) -> tuple[str, str]:
	"""``(before, after)`` the first separator. ``after`` is '' when there is none."""
	text = (value or "").strip()
	if SEPARATOR not in text:
		return text, ""
	head, _sep, tail = text.partition(SEPARATOR)
	return head.strip(), tail.strip()


# --- findings ------------------------------------------------------------------


def missing_evidence(code: str) -> bool:
	"""True when a finding code has no :data:`EVIDENCE` entry, so it may not be emitted."""
	return code not in EVIDENCE


def finding(code: str, message: str, **extra) -> dict:
	"""One finding. Raises on an unregistered code rather than emitting a mystery."""
	if code not in SEVERITY:
		raise ValueError(f"unknown finding code {code!r}; add it to party_naming_rules.SEVERITY")
	if missing_evidence(code):
		raise ValueError(
			f"finding code {code!r} has no EVIDENCE entry, so nothing states what it is "
			"grounded in. Write one; a check without it is folklore."
		)
	out = {"code": code, "severity": SEVERITY[code], "message": message}
	out.update({k: v for k, v in extra.items() if v is not None})
	return out


def verdict(findings) -> str:
	"""STOP if anything is a STOP, FIX if anything is a FIX, else PASS.

	NOTE never moves the verdict — which is what makes the stale-address-name finding safe
	to emit on eight records whose remedy is a rename nobody has authorised.
	"""
	severities = {f.get("severity") for f in findings or ()}
	if STOP in severities:
		return VERDICT_STOP
	if FIX in severities:
		return VERDICT_FIX
	return VERDICT_PASS


# --- scope ---------------------------------------------------------------------


def in_scope(doctype: str, facts: dict) -> bool:
	"""Should this record be checked at all?

	Out-of-scope is silence, not a pass — an internal Project is not badly named, it is a
	different kind of thing. Flagging all 101 of them would be the fastest way to get the
	whole report dismissed.
	"""
	config = DOCTYPES.get(doctype)
	if not config:
		return False
	if doctype == "Project":
		return (facts.get("project_type") or "").strip() in CUSTOMER_FACING_PROJECT_TYPES
	if doctype == "Address":
		# An address with no link belongs to nobody, so there is no party to name it after.
		# Reported separately by the caller rather than checked here.
		return bool((facts.get("party") or "").strip())
	# Every Opportunity has a party by construction; there is no internal-opportunity class.
	return True


def party_of(doctype: str, facts: dict) -> str:
	"""The party this record should be named after, or ''."""
	config = DOCTYPES.get(doctype) or {}
	for field in config.get("party_fields", ()):
		value = (facts.get(field) or "").strip()
		if value:
			return value
	return ""


# --- the checks ----------------------------------------------------------------

_ADDRESS_TYPE_WORDS = ("BILLING", "SHIPPING", "OFFICE", "PERSONAL", "PLANT", "OTHER")


def check(doctype: str, facts: dict) -> list[dict]:
	"""Every decidable defect in one record's name. Pure; no corpus, no I/O.

	``facts`` is a plain dict — the caller's query shape is the caller's business. It needs
	the doctype's own field (see :data:`DOCTYPES`), whichever party field applies, and for
	Address also ``address_type`` and ``name``.

	Returns ``[]`` for a record that is out of scope, because silence and a pass are
	different answers and only :func:`in_scope` can tell them apart.
	"""
	config = DOCTYPES.get(doctype)
	if not config or not in_scope(doctype, facts):
		return []

	value = facts.get(config["field"])
	party = party_of(doctype, facts)
	out: list[dict] = []

	if not (value or "").strip():
		return [finding(VALUE_MISSING, f"{config['label']} is empty.")]

	# Whitespace, compared by length rather than by equality: PAD SPACE collation makes the
	# SQL form of this test always false. See EVIDENCE[EDGE_WHITESPACE].
	if len(value) != len(value.strip()):
		out.append(
			finding(
				EDGE_WHITESPACE,
				f"{config['label']} has leading or trailing whitespace.",
				suggestion=value.strip(),
			)
		)
	body = value.strip()
	if "  " in body:
		out.append(finding(DOUBLE_SPACE, f"{config['label']} contains a double space."))

	if not party:
		out.append(
			finding(
				PARTY_MISSING,
				f"No {config['party_label'].lower()} is linked, so there is nothing to name "
				"this after. The name may well be right — it is the link that is missing.",
			)
		)

	if config["shape"] == SHAPE_PARTY_ONLY:
		out.extend(_check_party_only(doctype, facts, body, party, config))
	else:
		out.extend(_check_party_qualifier(body, party, config))
	return out


def _check_party_qualifier(body: str, party: str, config: dict) -> list[dict]:
	"""``<Party> - <what we are doing>``. Project and Opportunity."""
	out: list[dict] = []
	head, tail = split(body)

	if SEPARATOR not in body:
		out.append(
			finding(
				SEPARATOR_MISSING,
				f"{config['label']} needs '{config['party_label'].lower()} - what we are doing "
				f"for them', separated by ' - '.",
				suggestion=f"{party} - " if party else None,
			)
		)
		# Without a separator there is no prefix to judge and no qualifier to describe, so
		# the checks below would each report the same defect a second time.
		return out

	if not tail:
		out.append(finding(QUALIFIER_MISSING, "There is a separator but nothing after it."))
	elif tail.strip().upper() in VAGUE_QUALIFIERS:
		out.append(
			finding(QUALIFIER_VAGUE, f"{tail!r} does not say what we are doing.", segment=tail)
		)

	if party and not party_matches(head, party):
		out.append(
			finding(
				PARTY_PREFIX_MISMATCH,
				f"{head!r} is not {party!r}, even allowing a shortening of it. If this is the "
				"site or the general contractor rather than who pays, put the party first and "
				"the site in the description.",
				found=head,
				expected=party,
				suggestion=f"{party} - {tail}" if tail else None,
			)
		)
	return out


def _check_party_only(doctype: str, facts: dict, body: str, party: str, config: dict) -> list[dict]:
	"""``<Party>`` alone — the framework appends the qualifier. Address."""
	out: list[dict] = []

	head, tail = split(body)
	if tail and tail.strip().upper() in _ADDRESS_TYPE_WORDS:
		out.append(
			finding(
				TITLE_CARRIES_QUALIFIER,
				f"{config['label']} ends in {tail!r}, which frappe appends for you from "
				"Address Type. Leave the title as the party alone.",
				suggestion=head,
			)
		)
		# Judge the party against what is left once the doubled qualifier is removed,
		# otherwise every such record collects a second, redundant finding.
		body = head

	if party and not party_matches(body, party):
		out.append(
			finding(
				PARTY_PREFIX_MISMATCH,
				f"{body!r} is not {party!r}. An Address should be titled with the party it "
				"belongs to; the site or the room goes in the address lines.",
				found=body,
				expected=party,
				suggestion=party,
			)
		)

	# The record name carries the address type it had when it was inserted, and `autoname`
	# never runs again. Compared against the CURRENT type.
	name = (facts.get("name") or "").strip()
	address_type = (facts.get("address_type") or "").strip()
	if name and address_type and not _name_matches_type(name, address_type):
		out.append(
			finding(
				ADDRESS_TYPE_STALE,
				f"The record is named {name!r} but its Address Type is now {address_type!r}. "
				"Renaming it would ripple through every linked document, so this is worth "
				"knowing rather than worth fixing.",
				address_type=address_type,
			)
		)
	return out


def _name_matches_type(name: str, address_type: str) -> bool:
	"""Does an Address record name end in its own type, allowing frappe's ``-1`` suffix?

	``Title-Billing`` and ``Title-Billing-1`` both match ``Billing``; ``DELIVERY-Billing``
	does not match ``Shipping``.
	"""
	upper = name.upper()
	wanted = address_type.upper()
	if upper.endswith("-" + wanted):
		return True
	return bool(re.search(r"-" + re.escape(wanted) + r"-\d+$", upper))


# --- the corpus audit ----------------------------------------------------------


def collision_groups(rows) -> list[dict]:
	"""Records of one doctype whose names are identical under :data:`NORMALISATION`.

	One pass, largest group first — the same shape and the same reason as the Item audit:
	asking "does this one collide" per record is quadratic, and the audit needs the whole
	picture anyway.
	"""
	buckets: dict[str, list[dict]] = {}
	for row in rows or ():
		key = normalise(row.get("value"))
		if not key:
			continue
		buckets.setdefault(key, []).append(row)

	out = []
	for key, members in buckets.items():
		if len(members) < 2:
			continue
		names = sorted(str(m.get("name") or "") for m in members)
		out.append({"key": key, "names": names, "count": len(members), "value": members[0].get("value")})
	out.sort(key=lambda group: (-group["count"], group["key"]))
	return out


def audit(doctype: str, rows) -> list[dict]:
	"""Every in-scope record with its findings and verdict. Linear.

	``rows`` are plain dicts carrying ``name`` plus whatever :func:`check` needs. Out-of-scope
	records are dropped rather than passed, so a caller cannot mistake "not checked" for
	"checked and fine".
	"""
	config = DOCTYPES.get(doctype)
	if not config:
		return []
	rows = list(rows or ())

	scoped = []
	for row in rows:
		if not in_scope(doctype, row):
			continue
		scoped.append({**row, "value": row.get(config["field"]) or ""})

	collides: dict[str, list[str]] = {}
	for group in collision_groups(scoped):
		for name in group["names"]:
			collides[name] = [other for other in group["names"] if other != name]

	out = []
	for row in scoped:
		findings = check(doctype, row)
		others = collides.get(str(row.get("name") or ""))
		if others:
			findings.append(
				finding(
					DUPLICATE_NORMALISED,
					f"{len(others) + 1} records share this name after normalisation: "
					f"{', '.join(others[:5])}.",
					matches=others,
				)
			)
		out.append({
			"doctype": doctype,
			"name": str(row.get("name") or ""),
			"value": row.get("value") or "",
			"party": party_of(doctype, row),
			"findings": findings,
			"verdict": verdict(findings),
		})
	return out


def audit_sort_key(row: dict) -> tuple:
	"""Worst first, then by name. Deterministic."""
	return (SEVERITY_ORDER.get(row.get("verdict"), 9), row.get("name") or "")


def summarise(rows) -> dict:
	"""Counts a surface may be quoted from, so no caller invents its own definition."""
	rows = list(rows or ())
	by_code: dict[str, int] = {}
	for row in rows:
		for item in row.get("findings") or ():
			key = item.get("code")
			by_code[key] = by_code.get(key, 0) + 1
	passing = sum(1 for r in rows if r.get("verdict") == VERDICT_PASS)
	return {
		"in_scope_rows": len(rows),
		"passing_rows": passing,
		"compliance_pct": (passing / len(rows) * 100.0) if rows else None,
		"findings_by_code": dict(sorted(by_code.items(), key=lambda kv: (-kv[1], kv[0]))),
		"normalisation": NORMALISATION,
	}
