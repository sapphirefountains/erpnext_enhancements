# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What a compliant Item Code and Item Name look like. **No Frappe, no I/O.**

Implements the *ERPNext Item Naming Schema* SOP v1.0 (19 Aug 2026, Process Owner:
Purchasing Agent) — see `docs/item-naming-schema.md`. :mod:`item_naming` does the
reads; every judgement is here, so it can be executed rather than inspected — the
split :mod:`chat.governance.drift_rules` and
:mod:`chat.doctype.chat_settings.chat_settings_rules` already use, and for the reason
those modules give: there is no Frappe integration-test job in CI, so bench-free code
is the only code that runs on every push.

Nothing here raises, and nothing here decides what a finding *means*. Callers decide.
This module is **advisory** by design: there is no ``Item`` doc_event anywhere in this
app and nothing blocks a save. It reports; a human acts.

--------------------------------------------------------------------------------------
The two occupancy traps, which is why block arithmetic lives in Python and not in SQL
--------------------------------------------------------------------------------------

Both are live on production today and both were found in a hand-written query that
shipped in the paste-in validator prompt this module replaces.

**Trap 1 — the anchored regex hides a taken number.** ``item_code REGEXP '^PDT-[0-9]{4}$'``
looks right and is wrong: the only record of the 5HP VFD bypass is

    ``PDT-0008 VFD BYPASS W/MOTOR PROTECTION, 5HP - copy``

whose trailing text defeats the ``$``. The query reports **0008 as free** and the next
person reissues a number that is in use.

**Trap 2 — unanchoring it invents taken numbers.** Loosen the anchor and
``CAST(REGEXP_SUBSTR(item_code,'[0-9]+') AS UNSIGNED)`` swallows the five-digit
QuickBooks-era family — ``PDT-00000 (deleted)`` … ``PDT-00013 (deleted)``,
``PDT-00040 (deleted)``, and the live placeholder ``PDT-00051`` — collapsing them onto
four-digit slots 0-13, 40 and 51. ``PDT-0051`` and ``PDT-00051`` are **different items**.

:func:`block_slot` is the one place that boundary is decided: exactly ``width`` digits,
**not followed by another digit**, trailing text allowed. Trailing text is allowed
because a taken number is taken however badly the record is named; a further digit is
refused because that is a different code family. Unit-tested against the literal
production strings, which is the point of it being here rather than in a regex nobody
can run.

--------------------------------------------------------------------------------------
What this module deliberately does not check
--------------------------------------------------------------------------------------

**The seven segments are validated positionally, never semantically.** CATEGORY is
checkable against a controlled list, SIZE and RATING are recognisable from token shape.
SUB-CATEGORY, KEY FEATURE, MATERIAL and PACKAGING have neither a shape nor a vocabulary
— there is no way to tell that ``BRASS`` in position 4 is a MATERIAL and ``BRASS`` in
position 3 is a KEY FEATURE. A classifier for those four would be a guess wearing a
check's costume, and the failure mode is the bad one: findings people learn to ignore.
Callers receive the segments as positional data and are told so.

**Categories are rules; counts are data.** :data:`APPROVED_CATEGORIES` is a constant
because the SOP's Appendix A is policy this business set, not a measurement — deriving
it from ``SELECT DISTINCT item_name`` would be self-ratifying, promoting the corpus's
176 undeclared leading words to the standard they were supposed to be measured against.
Everything countable — how many items exist, which slots are occupied, what collides
with what — is passed in by the caller from a live read and is never stated here.
"""

from __future__ import annotations

import re
from typing import Final

# --- severities and verdict ----------------------------------------------------

#: Do not create this record. It exists, its code collides, or no approved category fits.
STOP: Final[str] = "STOP"

#: Safe to create once the listed corrections are applied. The normal outcome.
FIX: Final[str] = "FIX"

#: Worth a human's eye, but not a defect on its own.
NOTE: Final[str] = "NOTE"

SEVERITIES: Final[tuple[str, ...]] = (STOP, FIX, NOTE)

VERDICT_PASS: Final[str] = "PASS"
VERDICT_FIX: Final[str] = "FIX"
VERDICT_STOP: Final[str] = "STOP"

# --- the seven segments --------------------------------------------------------

#: SOP §5 Phase 3. Order is the schema; the names are labels for humans reading the
#: payload, NOT a classification this module claims to have performed. See the module
#: docstring — only 1, 5 and 6 are decidable, and only 1 has a vocabulary.
SEGMENTS: Final[tuple[str, ...]] = (
	"CATEGORY",
	"SUB-CATEGORY",
	"KEY FEATURE",
	"MATERIAL",
	"SIZE",
	"RATING",
	"PACKAGING",
)

MAX_SEGMENTS: Final[int] = len(SEGMENTS)

# --- code families -------------------------------------------------------------

FAMILY_VENDOR: Final[str] = "vendor"
FAMILY_CONSUMABLE: Final[str] = "consumable"
FAMILY_PRODUCT: Final[str] = "product"
FAMILY_SERVICE: Final[str] = "service"
FAMILY_UNKNOWN: Final[str] = "unknown"

FAMILIES: Final[tuple[str, ...]] = (
	FAMILY_VENDOR,
	FAMILY_CONSUMABLE,
	FAMILY_PRODUCT,
	FAMILY_SERVICE,
	FAMILY_UNKNOWN,
)

#: SOP §5 Phase 2. GROUP is one of these three and no others.
CONSUMABLE_GROUPS: Final[tuple[str, ...]] = ("ELEC", "OFFC", "SRV")

#: prefix -> digit width for the two block-allocated families.
RESERVED_PREFIXES: Final[dict[str, int]] = {"PDT": 4, "SRV": 3}

#: Which family each reserved prefix names.
PREFIX_FAMILY: Final[dict[str, str]] = {
	"PDT": FAMILY_PRODUCT,
	"SRV": FAMILY_SERVICE,
}

#: Numbers per block. **A proposal, not a lookup** — no block width is written down
#: anywhere in this repo or in the SOP. It is read off the shape of the live allocation,
#: where PDT runs 00xx/01xx/02xx/04xx/05xx/07xx and SRV runs 0xx/1xx/2xx/4xx, i.e. both
#: are allocated in hundreds. If a family is ever allocated at a different width this
#: becomes wrong quietly, so :func:`block_report` returns the block bounds it used.
BLOCK_SIZE: Final[int] = 100

#: Working suffixes that must never survive on a saved record (SOP §5 Step 2.4).
WORKING_SUFFIXES: Final[tuple[str, ...]] = ("- copy", "- new")

#: The QuickBooks-era tombstone marker. `disabled` is NOT the marker: every row carries
#: `disabled = 0`, so a filter on it returns everything or nothing depending which way it
#: was written, and both look plausible. This suffix is the only signal there is.
DELETED_MARKER: Final[str] = "(deleted)"

# --- approved category vocabulary (SOP Appendix A) ------------------------------
#
# Transcribed from the SOP, which supersedes the Naming Convention Sheet spreadsheet.
# The abbreviation column from that sheet is retired: not one of CB, XFMR, TB, SWGR,
# FSTNR, GSKT or CTRAY appears as a leading word on any active record, and full words
# are now the rule (SOP Appendix A preamble, and C-9).

#: Tier 1, from the Naming Convention Sheet and confirmed in use.
_TIER1_FROM_SHEET: Final[tuple[str, ...]] = (
	"PUMP", "ELBOW", "PIPE", "TEE", "VALVE", "PIPE CLAMP", "BUSHING", "COUPLING",
	"FILTER", "SENSOR", "LIGHT", "ADAPTER", "GLOVE", "UNION", "RELAY", "SCREW", "PAD",
	"TERMINAL BLOCK", "FLANGE", "CEMENT", "CONNECTOR", "CONTACTOR", "PEDESTAL", "STONE",
	"TRANSFORMER", "MOTOR", "TRAY", "STRUT CHANNEL", "GASKET", "SPACER", "CONTROLLER",
	"TILE", "GRATING",
)

#: Tier 1, promoted from ERPNext usage. These exist because the original sheet is
#: entirely engineering-focused and declares no categories at all for office and
#: janitorial consumables — which is why the Office group scored 1 compliant record
#: out of 61 (SOP Appendix A, Tier 1 note).
_TIER1_PROMOTED: Final[tuple[str, ...]] = (
	"SLEEVE", "BLOCK", "COVER", "PLATE", "WATERPROOFING", "RAM BIT", "CLEANER",
	"FERRULE", "STRAINER", "TAPE", "ANEMOMETER", "CABLE", "CARTRIDGE", "ENCLOSURE",
	"FITTING", "HOSE", "LINK SEAL", "NIPPLE", "POWER SUPPLY", "RECEPTACLE",
	"WATERSTOP FITTING", "BATTERY", "BAG", "PAPER", "PEN", "MARKER", "MOUSE",
	"FILAMENT", "SCOURING PAD",
)

#: Tier 2 — carried forward from the sheet, valid, no records yet. Use rather than
#: inventing a synonym.
_TIER2: Final[tuple[str, ...]] = (
	"BOLT", "CABLE / WIRE", "CABLE TRAY / SUPPORT", "CAP", "CAPACITOR",
	"CAPACITOR BANK", "CHEM FEEDER", "CIRCUIT BREAKER", "CONDUIT / RACEWAY",
	"DIFFUSER", "DIODE", "DRAIN", "DRIVE / VFD", "FASTENER", "FUSE / FUSEHOLDER",
	"GLAND / FITTING", "HANGER", "HEATER", "HMI", "HOSE / TUBE", "IC / CHIP",
	"INDICATOR / LAMP", "MANIFOLD", "METER", "NOZZLE", "NUT", "PLC", "PLUG",
	"RESISTOR", "SEAL", "SKIMMER", "STRUT BOLT", "STRUT COVER", "STRUT FITTING",
	"STRUT NUT", "STRUT WASHER", "SWITCHGEAR", "TRANSISTOR", "WET NICHE",
)

TIER1: Final[tuple[str, ...]] = _TIER1_FROM_SHEET + _TIER1_PROMOTED
TIER2: Final[tuple[str, ...]] = _TIER2


def _expand_alternatives(categories: tuple[str, ...]) -> set[str]:
	"""Split the eight slashed Tier 2 entries into the words a name can actually lead with.

	`CABLE / WIRE` is one row of Appendix A but two admissible category words, and an
	Item Name cannot lead with the literal string `CABLE / WIRE` and stay comma-delimited.
	Reading them as alternatives is an **interpretation of the SOP, not a quotation of
	it** — recorded here because it also resolves a real inconsistency: Tier 3 instructs
	`FUSES -> FUSE (singular)`, but `FUSE` appears in Appendix A only inside
	`FUSE / FUSEHOLDER`, so without this the SOP's own prescribed replacement would be
	rejected as an unapproved category.
	"""
	out: set[str] = set()
	for entry in categories:
		out.add(_squash(entry))
		if "/" in entry:
			for part in entry.split("/"):
				part = _squash(part)
				if part:
					out.add(part)
	return out


def _squash(value: str | None) -> str:
	"""Uppercase, collapse internal runs of whitespace, strip the ends."""
	return " ".join((value or "").upper().split())


#: Every word an Item Name may lead with. A category outside this set is a STOP, never
#: a new category — adding one needs the Process Owner's written approval (SOP §3).
APPROVED_CATEGORIES: Final[frozenset[str]] = frozenset(
	_expand_alternatives(TIER1) | _expand_alternatives(TIER2)
)

#: Tier 3 — leading words that are in the data today and must not be used again, with
#: the form to use instead. Keys are matched against the whole first segment.
#:
#: Two of these are worth knowing about before you trust the replacement column:
#: `SUBPANELT -> PANEL, SUB` points at `PANEL`, which is on neither Tier 1 nor Tier 2,
#: so the SOP's own replacement would itself be rejected; and `WIRE CONNECCTORS ->
#: CONNECTOR, WIRE` is fine only because `CONNECTOR` is Tier 1. Both are reported to
#: the caller as-written rather than silently repaired — this module quotes the SOP, it
#: does not amend it.
TIER3_REPLACEMENTS: Final[dict[str, str]] = {
	"PROTE": "CIRCUIT BREAKER, SUPPLEMENTARY, ...",
	"BREAKER": "CIRCUIT BREAKER",
	"FUSES": "FUSE",
	"TERMINALS": "TERMINAL BLOCK",
	"CHECK VALVE": "VALVE, CHECK",
	"BACKWASH VALVE": "VALVE, BACKWASH",
	"SAND FILTER": "FILTER, SAND",
	"WATER LEVEL SENSOR": "SENSOR, LEVEL",
	"OUTLET COVER": "COVER, OUTLET",
	"MINI RELAY": "RELAY, MINI",
	"WIRE TRAY": "CABLE TRAY / SUPPORT",
	"RED BUSH": "BUSHING, REDUCER",
	"BRUSHING": "BUSHING",
	"CATRIDGE": "CARTRIDGE",
	"EDISION FUSE": "FUSE, EDISON",
	"WIRE CONNECCTORS": "CONNECTOR, WIRE",
	"SUBPANELT": "PANEL, SUB",
}

#: Categories whose SOP-prescribed replacement is not itself on Tier 1 or Tier 2. The
#: caller is told, so a validator never hands somebody a correction that fails its own
#: check. Computed rather than listed so it stays true if Appendix A changes.
TIER3_REPLACEMENT_UNAPPROVED: Final[frozenset[str]] = frozenset(
	bad
	for bad, good in TIER3_REPLACEMENTS.items()
	if _squash(good.split(",")[0]) not in APPROVED_CATEGORIES
)

# --- finding codes -------------------------------------------------------------
#
# Slugs, grouped by what they are about. Every one needs an EVIDENCE entry or it
# cannot be emitted -- see `missing_evidence`, the property that keeps this module
# honest in a year.

# duplicates and collisions
DUPLICATE_CODE_EXACT: Final[str] = "duplicate_code_exact"
DUPLICATE_CODE_NORMALISED: Final[str] = "duplicate_code_normalised"
DUPLICATE_NAME_NORMALISED: Final[str] = "duplicate_name_normalised"

# the Item Code
CODE_MISSING: Final[str] = "code_missing"
CODE_HAS_COMMA: Final[str] = "code_has_comma"
CODE_WORKING_SUFFIX: Final[str] = "code_working_suffix"
CODE_DELETED_SUFFIX: Final[str] = "code_deleted_suffix"
CODE_CONSUMABLE_GROUP_UNKNOWN: Final[str] = "code_consumable_group_unknown"
CODE_CONSUMABLE_MALFORMED: Final[str] = "code_consumable_malformed"
CODE_RESERVED_PREFIX_MALFORMED: Final[str] = "code_reserved_prefix_malformed"
CODE_SLOT_OCCUPIED: Final[str] = "code_slot_occupied"

# the Item Name
NAME_MISSING: Final[str] = "name_missing"
NAME_NOT_UPPERCASE: Final[str] = "name_not_uppercase"
NAME_EDGE_WHITESPACE: Final[str] = "name_edge_whitespace"
NAME_DOUBLE_SPACE: Final[str] = "name_double_space"
NAME_TRAILING_COMMA: Final[str] = "name_trailing_comma"
NAME_EMPTY_SEGMENT: Final[str] = "name_empty_segment"
NAME_SEPARATOR_SPACING: Final[str] = "name_separator_spacing"
NAME_NO_COMMA: Final[str] = "name_no_comma"
NAME_TOO_MANY_SEGMENTS: Final[str] = "name_too_many_segments"
NAME_CATEGORY_TIER3: Final[str] = "name_category_tier3"
NAME_CATEGORY_PLURAL: Final[str] = "name_category_plural"
NAME_CATEGORY_UNAPPROVED: Final[str] = "name_category_unapproved"
NAME_CATEGORY_IS_SIZE: Final[str] = "name_category_is_size"
NAME_CATEGORY_IS_BRAND: Final[str] = "name_category_is_brand"
NAME_SIZE_WORD_INCH: Final[str] = "name_size_word_inch"
NAME_COLOUR_GREY: Final[str] = "name_colour_grey"
NAME_SCHEDULE_OWN_SEGMENT: Final[str] = "name_schedule_own_segment"
NAME_SCHEDULE_BEFORE_SIZE: Final[str] = "name_schedule_before_size"
NAME_PARENTHETICAL_PROSE: Final[str] = "name_parenthetical_prose"
NAME_EQUALS_CODE: Final[str] = "name_equals_code"

# supporting fields
GROUP_MISSING: Final[str] = "group_missing"
GROUP_IS_ROOT: Final[str] = "group_is_root"
STOCK_UOM_MISSING: Final[str] = "stock_uom_missing"

# cross-field
CODE_NAME_DISAGREES: Final[str] = "code_name_disagrees"

#: Severity per finding code. STOP means do not create the record at all.
SEVERITY: Final[dict[str, str]] = {
	DUPLICATE_CODE_EXACT: STOP,
	DUPLICATE_CODE_NORMALISED: STOP,
	DUPLICATE_NAME_NORMALISED: STOP,
	CODE_SLOT_OCCUPIED: STOP,
	NAME_CATEGORY_UNAPPROVED: STOP,
	CODE_MISSING: STOP,
	CODE_HAS_COMMA: FIX,
	CODE_WORKING_SUFFIX: FIX,
	CODE_DELETED_SUFFIX: FIX,
	CODE_CONSUMABLE_GROUP_UNKNOWN: FIX,
	CODE_CONSUMABLE_MALFORMED: FIX,
	CODE_RESERVED_PREFIX_MALFORMED: FIX,
	NAME_MISSING: FIX,
	NAME_NOT_UPPERCASE: FIX,
	NAME_EDGE_WHITESPACE: FIX,
	NAME_DOUBLE_SPACE: FIX,
	NAME_TRAILING_COMMA: FIX,
	NAME_EMPTY_SEGMENT: FIX,
	NAME_SEPARATOR_SPACING: FIX,
	NAME_NO_COMMA: FIX,
	NAME_TOO_MANY_SEGMENTS: FIX,
	NAME_CATEGORY_TIER3: FIX,
	NAME_CATEGORY_PLURAL: FIX,
	NAME_CATEGORY_IS_SIZE: FIX,
	NAME_CATEGORY_IS_BRAND: FIX,
	NAME_SIZE_WORD_INCH: FIX,
	NAME_COLOUR_GREY: FIX,
	NAME_SCHEDULE_OWN_SEGMENT: FIX,
	NAME_SCHEDULE_BEFORE_SIZE: FIX,
	NAME_PARENTHETICAL_PROSE: FIX,
	NAME_EQUALS_CODE: FIX,
	GROUP_MISSING: FIX,
	GROUP_IS_ROOT: FIX,
	STOCK_UOM_MISSING: FIX,
	CODE_NAME_DISAGREES: NOTE,
}

CODES: Final[tuple[str, ...]] = tuple(SEVERITY)

#: What each finding is grounded in — the SOP clause, and for the heuristics the
#: false-positive class they are known to have. Not documentation:
#: :func:`missing_evidence` reads it and a code added without an entry cannot be
#: emitted, which is what stops this list drifting into folklore.
EVIDENCE: Final[dict[str, str]] = {
	DUPLICATE_CODE_EXACT: "an Item with this exact item_code already exists. SOP §5 Step 1.3.",
	DUPLICATE_CODE_NORMALISED: (
		"an existing item_code is identical after `normalise` — same characters, different "
		"punctuation or case. SOP §5 Step 1.1."
	),
	DUPLICATE_NAME_NORMALISED: (
		"an existing item_name is identical after `normalise`. Note what this does NOT catch: "
		"the same physical part named two different ways is invisible to any normalisation "
		"(SOP D-3's 4010052576503 / 57650 pair differ in word order alone), so a clean result "
		"here is not evidence the part is new. That judgement is the reader's."
	),
	CODE_MISSING: (
		"there is nothing to check. Stock Settings has `Item Naming By = Item Code`, so a code "
		"is typed by hand and is mandatory."
	),
	CODE_HAS_COMMA: "SOP §5 Step 2.4 — never put a comma in an Item Code. Four live records break this.",
	CODE_WORKING_SUFFIX: (
		"SOP §5 Step 2.4 — a working suffix such as '- copy' must not survive on a saved record."
	),
	CODE_DELETED_SUFFIX: (
		"the '(deleted)' marker belongs to the 135 QuickBooks migration artefacts and to nothing "
		"else. SOP §6 says do not transact against them and do not rename them."
	),
	CODE_CONSUMABLE_GROUP_UNKNOWN: (
		"SOP §5 Phase 2 — GROUP is ELEC, OFFC or SRV, those three and no others."
	),
	CODE_CONSUMABLE_MALFORMED: "SOP §5 Phase 2 — the shape is CON-<GROUP>-<DESCRIPTOR>[-<SIZE>].",
	CODE_RESERVED_PREFIX_MALFORMED: (
		"the code claims a block-allocated prefix but its digits do not parse at the declared "
		"width. PDT-00051 and PDT-0051 are different items; PDT-00XX is a literal placeholder "
		"sitting in production."
	),
	CODE_SLOT_OCCUPIED: (
		"a record already begins with this prefix and number at the exact digit width. Trailing "
		"text does not free a number: the sole record of the 5HP VFD bypass is "
		"'PDT-0008 VFD BYPASS W/MOTOR PROTECTION, 5HP - copy', and 0008 is taken."
	),
	NAME_MISSING: "SOP §2 — item_name carries the schema and is authoritative. Description does not.",
	NAME_NOT_UPPERCASE: (
		"SOP §5 Phase 3 — the whole name is capitals. Detected with a case-sensitive comparison: "
		"MariaDB's default collation makes the naive `item_name <> UPPER(item_name)` always false."
	),
	NAME_EDGE_WHITESPACE: (
		"SOP §5 Step 4.2. The naive SQL form of this check (`item_name <> TRIM(item_name)`) "
		"returns zero on a corpus with three offenders, because PAD SPACE collation ignores "
		"trailing spaces in comparison. Compared here by length, in Python, where it cannot."
	),
	NAME_DOUBLE_SPACE: "SOP §5 Step 4.2 — no double spaces.",
	NAME_TRAILING_COMMA: (
		"SOP §5 Step 4.2. The live offender carries a trailing comma AND a trailing space, so "
		"`LIKE '%,'` misses it; the name must be stripped before the test."
	),
	NAME_EMPTY_SEGMENT: (
		"SOP §5 Phase 3 — omit a segment that carries no information rather than leaving an "
		"empty pair of commas."
	),
	NAME_SEPARATOR_SPACING: "SOP §5 Phase 3 — a comma and exactly one space.",
	NAME_NO_COMMA: (
		"SOP §5 Phase 3 — the name is comma-delimited. A name with no comma at all has one "
		"segment and cannot express the schema."
	),
	NAME_TOO_MANY_SEGMENTS: (
		"SOP §5 Phase 3 declares seven segments. More than seven means either a segment was "
		"split that should not have been, or prose crept in. Reported, never auto-joined — "
		"which of the seven a surplus segment belongs to is not decidable."
	),
	NAME_CATEGORY_TIER3: "SOP Appendix A Tier 3 names this leading word and gives its replacement.",
	NAME_CATEGORY_PLURAL: (
		"SOP §5 Phase 3 and §6 — the CATEGORY is singular, always. Fired only when removing a "
		"trailing S yields a category that IS approved, so it cannot misfire on a word that "
		"merely ends in S."
	),
	NAME_CATEGORY_UNAPPROVED: (
		"SOP §5 Step 4.1 — stop and request the addition from the Process Owner. 176 undeclared "
		"leading words are already in the data and they are the single largest cause of failed "
		"searches, so a new one is a STOP rather than a FIX."
	),
	NAME_CATEGORY_IS_SIZE: (
		"SOP Appendix A Tier 3 — a size is never a category; it belongs in segment 5."
	),
	NAME_CATEGORY_IS_BRAND: (
		"SOP §5 Phase 3 and Appendix A Tier 3 — a brand is never a category; it belongs in KEY "
		"FEATURE or RATING. Fired only against brands the caller supplied from live data, so a "
		"brand nobody has recorded yet is caught by name_category_unapproved instead, or not "
		"at all."
	),
	NAME_SIZE_WORD_INCH: "SOP §5 Phase 3 — sizes are digits with an inch mark, never the word INCH.",
	NAME_COLOUR_GREY: "SOP §5 Phase 3 — GRAY, not GREY.",
	NAME_SCHEDULE_OWN_SEGMENT: (
		"SOP §5 Phase 3 — the size and its schedule are one segment, size first. A schedule "
		"alone in its own segment is the live wrong form."
	),
	NAME_SCHEDULE_BEFORE_SIZE: (
		"SOP §5 Phase 3 — within the segment the size comes first, then the schedule."
	),
	NAME_PARENTHETICAL_PROSE: (
		"SOP §5 Phase 3 — specifications are segments, not prose in brackets."
	),
	NAME_EQUALS_CODE: (
		"the name repeats the code and therefore carries no description at all. 190 live rows "
		"do this; 135 are '(deleted)' tombstones and the rest are unfinished records."
	),
	GROUP_MISSING: "SOP §5 Step 4.4 — set the Item Group from the tree.",
	GROUP_IS_ROOT: (
		"SOP §5 Step 4.4 — do not leave the item in All Item Groups. 122 active records sit "
		"there and one of them is compliant."
	),
	STOCK_UOM_MISSING: "SOP §5 Step 4.4 — set stock_uom.",
	CODE_NAME_DISAGREES: (
		"the code is a CON- consumable, whose descriptor is chosen by us and therefore SHOULD "
		"echo the name, and no descriptor token appears in the name. The live case is "
		"CON-OFFC-INK-HP-952-COLOR named 'WATER LEVEL SENSOR, REED SWITCH, M10 THREAD' "
		"(SOP D-1). Deliberately not run on vendor codes: a vendor part number is arbitrary by "
		"design and has no relationship to the name to compare. NOTE severity because a "
		"legitimate abbreviation (OFFC for OFFICE) trips it."
	),
}


# --- normalisation -------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")
_TOKEN = re.compile(r"[A-Z0-9]+")
_SCHEDULE_ONLY = re.compile(r"^SCH\s*\d+$")
_SCHEDULE_LEADING = re.compile(r"^SCH\s*\d+\s")
_WORD_INCH = re.compile(r"\bINCHE?S?\b")
_WORD_GREY = re.compile(r"\bGREY\b")
_COMMA_NO_SPACE = re.compile(r",(?=\S)")
_COMMA_WIDE_SPACE = re.compile(r",\s{2,}")

#: The one normalisation this module uses, everywhere, for every identity question.
#:
#: Naming it matters more than choosing it. A duplicate *count* is a property of the
#: normalisation, not of the data: on the live corpus, uppercase-plus-whitespace-collapse
#: finds three colliding name groups and this rule finds four, and neither number means
#: anything without the function beside it. The extra group this rule catches is a real
#: duplicate — `RGB_DMX` against `RGB-DMX` — which is the reason for stripping punctuation
#: rather than collapsing it.
NORMALISATION: Final[str] = "uppercase, then remove every character that is not A-Z or 0-9"


def normalise(value: str | None) -> str:
	"""Fold a code or a name to its identity string. See :data:`NORMALISATION`."""
	return _NON_ALNUM.sub("", (value or "").upper())


def tokens(value: str | None) -> tuple[str, ...]:
	"""Alphanumeric runs, uppercased, in order and with duplicates removed."""
	seen: dict[str, None] = {}
	for tok in _TOKEN.findall((value or "").upper()):
		seen.setdefault(tok, None)
	return tuple(seen)


def segments(name: str | None) -> tuple[str, ...]:
	"""The comma-delimited segments, stripped. Empty segments are preserved as ''."""
	raw = (name or "").strip()
	if not raw:
		return ()
	return tuple(part.strip() for part in raw.split(","))


# --- findings ------------------------------------------------------------------


def missing_evidence(code: str) -> bool:
	"""True when a finding code has no :data:`EVIDENCE` entry, so it may not be emitted."""
	return code not in EVIDENCE


def finding(code: str, message: str, **extra) -> dict:
	"""One finding. Raises on an unregistered code rather than emitting a mystery.

	The `ValueError` is the guard rail, not an error path a caller handles: a code
	without a severity has no place in the verdict, and a code without evidence states
	nothing about why it fired.
	"""
	if code not in SEVERITY:
		raise ValueError(f"unknown finding code {code!r}; add it to item_naming_rules.SEVERITY")
	if missing_evidence(code):
		raise ValueError(
			f"finding code {code!r} has no EVIDENCE entry, so nothing states what it is "
			"grounded in. Write one; a check without it is folklore."
		)
	out = {"code": code, "severity": SEVERITY[code], "message": message}
	out.update({k: v for k, v in extra.items() if v is not None})
	return out


def verdict(findings: list[dict]) -> str:
	"""STOP if anything is a STOP, FIX if anything is a FIX, else PASS.

	NOTE findings never move the verdict — that is what makes them safe to emit from the
	heuristics, and what stops a known false-positive class blocking a correct record.
	"""
	severities = {f.get("severity") for f in findings or ()}
	if STOP in severities:
		return VERDICT_STOP
	if FIX in severities:
		return VERDICT_FIX
	return VERDICT_PASS


# --- code family ---------------------------------------------------------------


def classify_code_family(code: str | None) -> str:
	"""Which of the four families a code belongs to (SOP §5 Phase 2).

	The reserved prefixes are claimed on the *prefix alone*, before the digits are
	checked, so a malformed `PDT-00XX` is still classified `product` and reported as a
	malformed member of that family rather than silently passing as a vendor part number.
	"""
	text = (code or "").strip().upper()
	if not text:
		return FAMILY_UNKNOWN
	for prefix, family in PREFIX_FAMILY.items():
		if text.startswith(prefix + "-"):
			return family
	if text.startswith("CON-"):
		return FAMILY_CONSUMABLE
	return FAMILY_VENDOR


def block_slot(code: str | None, prefix: str) -> int | None:
	"""The number a code occupies in a block-allocated family, or ``None``.

	**Exactly ``width`` digits, not followed by another digit, trailing text allowed.**
	Both halves of that rule are load-bearing and each was a live bug (module docstring):

	>>> block_slot("PDT-0008 VFD BYPASS W/MOTOR PROTECTION, 5HP - copy", "PDT")
	8
	>>> block_slot("PDT-00051", "PDT") is None
	True
	>>> block_slot("PDT-0051", "PDT")
	51
	>>> block_slot("PDT-00XX", "PDT") is None
	True
	"""
	width = RESERVED_PREFIXES.get((prefix or "").upper())
	if not width:
		return None
	text = (code or "").strip().upper()
	match = re.match(rf"{re.escape(prefix.upper())}-(\d{{{width}}})(?!\d)", text)
	return int(match.group(1)) if match else None


def block_bounds(slot: int) -> tuple[int, int]:
	"""The inclusive first and last number of the block a slot falls in."""
	start = (int(slot) // BLOCK_SIZE) * BLOCK_SIZE
	return start, start + BLOCK_SIZE - 1


def occupancy(codes, prefix: str) -> dict[int, list[str]]:
	"""``{slot: [every code occupying it]}`` for one reserved prefix.

	A list rather than a bool because *which* record holds a number is the thing a reader
	needs: a slot held only by a `(deleted)` tombstone is a different conversation from
	one held by a live product, and this module does not make that call for them.
	"""
	out: dict[int, list[str]] = {}
	for code in codes or ():
		slot = block_slot(code, prefix)
		if slot is None:
			continue
		out.setdefault(slot, []).append(str(code))
	for holders in out.values():
		holders.sort()
	return out


def block_report(occupied: dict[int, list[str]], slot: int) -> dict:
	"""Occupied and free numbers within the one block a slot belongs to.

	Reports; never allocates. The blocks are sparse and *semantic* — PDT spans 0 to 701
	with hundreds of gaps, and which block a new product belongs in is a judgement about
	what the product is, not an arithmetic fact. Handing back `max + 1` would be a
	confident wrong answer, so the free list is returned and the choice stays with the
	human.
	"""
	start, end = block_bounds(slot)
	in_block = {n: holders for n, holders in (occupied or {}).items() if start <= n <= end}
	return {
		"block_start": start,
		"block_end": end,
		"block_size": BLOCK_SIZE,
		"occupied": {n: in_block[n] for n in sorted(in_block)},
		"free": [n for n in range(start, end + 1) if n not in in_block],
	}


# --- the checks ----------------------------------------------------------------

#: The Item Group tree's root. An item left here is unclassified, not classified as
#: "everything" (SOP §5 Step 4.4).
ROOT_ITEM_GROUP: Final[str] = "All Item Groups"

#: How many neighbours :func:`similar_records` returns, and how close a record has to be
#: to count as one. **Proposals, not lookups** — no similarity threshold exists anywhere
#: in this repo or in the SOP. Both are returned in the payload so a reader can see what
#: was applied rather than infer it from the size of the list.
DEFAULT_SIMILAR_LIMIT: Final[int] = 8
DEFAULT_SIMILARITY_MIN_SCORE: Final[float] = 0.15


def check_code(code: str | None) -> list[dict]:
	"""Structural defects in an Item Code. No corpus, no I/O."""
	out: list[dict] = []
	text = (code or "").strip()
	if not text:
		return [finding(CODE_MISSING, "No Item Code given.")]

	if "," in text:
		out.append(
			finding(
				CODE_HAS_COMMA,
				"The Item Code contains a comma. Strip the comma only, keep every other "
				"character, and record the full original number in the Description (SOP §6).",
				suggestion=text.replace(",", ""),
			)
		)
	lowered = text.lower()
	for suffix in WORKING_SUFFIXES:
		if suffix in lowered:
			out.append(
				finding(
					CODE_WORKING_SUFFIX,
					f"The Item Code carries the working suffix {suffix!r}.",
				)
			)
			break
	if DELETED_MARKER in lowered:
		out.append(
			finding(
				CODE_DELETED_SUFFIX,
				"The Item Code carries the '(deleted)' migration marker. Do not create new "
				"records with it and do not transact against the ones that have it.",
			)
		)

	family = classify_code_family(text)
	upper = text.upper()

	if family == FAMILY_CONSUMABLE:
		parts = upper.split("-")
		if len(parts) < 3 or not parts[2]:
			out.append(
				finding(
					CODE_CONSUMABLE_MALFORMED,
					"A CON- code needs at least CON-<GROUP>-<DESCRIPTOR>.",
				)
			)
		elif parts[1] not in CONSUMABLE_GROUPS:
			out.append(
				finding(
					CODE_CONSUMABLE_GROUP_UNKNOWN,
					f"{parts[1]!r} is not a consumable GROUP. Use one of "
					f"{', '.join(CONSUMABLE_GROUPS)}.",
				)
			)

	if family in (FAMILY_PRODUCT, FAMILY_SERVICE):
		prefix = upper.split("-", 1)[0]
		width = RESERVED_PREFIXES[prefix]
		if block_slot(upper, prefix) is None:
			out.append(
				finding(
					CODE_RESERVED_PREFIX_MALFORMED,
					f"{text!r} claims the {prefix}- family but its digits do not read as "
					f"exactly {width}. {prefix}-0051 and {prefix}-00051 are different codes.",
					prefix=prefix,
					expected_width=width,
				)
			)
	return out


def check_block(code: str | None, occupied: dict[int, list[str]] | None) -> tuple[list[dict], dict | None]:
	"""``(findings, block_report)`` for a code in a block-allocated family.

	Returns ``(…, None)`` for any code that is not in one, which is most of them — 71% of
	active records are vendor part numbers and have no block at all.
	"""
	text = (code or "").strip().upper()
	family = classify_code_family(text)
	if family not in (FAMILY_PRODUCT, FAMILY_SERVICE):
		return [], None
	prefix = text.split("-", 1)[0]
	slot = block_slot(text, prefix)
	if slot is None:
		return [], None

	occupied = occupied or {}
	report = block_report(occupied, slot)
	report["prefix"] = prefix
	report["slot"] = slot

	holders = occupied.get(slot) or []
	# A record validating itself is not a collision with itself.
	others = [h for h in holders if h.strip().upper() != text]
	out: list[dict] = []
	if others:
		out.append(
			finding(
				CODE_SLOT_OCCUPIED,
				f"{prefix}-{slot:0{RESERVED_PREFIXES[prefix]}d} is already held by "
				f"{', '.join(repr(h) for h in others)}.",
				occupied_by=others,
			)
		)
	return out, report


def check_name(name: str | None, code: str | None = None, brands=()) -> list[dict]:
	"""Every decidable defect in an Item Name (SOP §5 Phase 3 and Step 4.2)."""
	out: list[dict] = []
	raw = name or ""
	if not raw.strip():
		return [finding(NAME_MISSING, "No Item Name given.")]

	# Whitespace, compared by length: MariaDB's PAD SPACE collation makes the SQL form of
	# this test always false, so it is done here where a trailing space is still a
	# character. See EVIDENCE[NAME_EDGE_WHITESPACE].
	if len(raw) != len(raw.strip()):
		out.append(
			finding(
				NAME_EDGE_WHITESPACE,
				"The Item Name has leading or trailing whitespace.",
				suggestion=raw.strip(),
			)
		)
	body = raw.strip()

	if "  " in body:
		out.append(finding(NAME_DOUBLE_SPACE, "The Item Name contains a double space."))
	if body.endswith(","):
		out.append(
			finding(
				NAME_TRAILING_COMMA,
				"The Item Name ends in a comma.",
				suggestion=body.rstrip(",").rstrip(),
			)
		)
	if body != body.upper():
		out.append(
			finding(
				NAME_NOT_UPPERCASE,
				"The Item Name is not entirely uppercase.",
				suggestion=body.upper(),
			)
		)
	if "," not in body:
		out.append(
			finding(
				NAME_NO_COMMA,
				"The Item Name has no comma, so it carries a single segment and cannot "
				"express the schema.",
			)
		)
	else:
		if _COMMA_NO_SPACE.search(body) or _COMMA_WIDE_SPACE.search(body):
			out.append(
				finding(
					NAME_SEPARATOR_SPACING,
					"Segments are separated by a comma and exactly one space.",
				)
			)

	parts = segments(body)
	# A trailing comma produces one empty tail segment, already reported above.
	interior = parts[:-1] if parts and parts[-1] == "" else parts
	if any(part == "" for part in interior):
		out.append(finding(NAME_EMPTY_SEGMENT, "The Item Name contains an empty segment."))
	populated = [part for part in parts if part]
	if len(populated) > MAX_SEGMENTS:
		out.append(
			finding(
				NAME_TOO_MANY_SEGMENTS,
				f"{len(populated)} segments; the schema declares {MAX_SEGMENTS}.",
				segment_count=len(populated),
			)
		)

	if "(" in body or ")" in body:
		out.append(
			finding(
				NAME_PARENTHETICAL_PROSE,
				"Specifications belong in their own segments, not in brackets.",
			)
		)
	if _WORD_INCH.search(body.upper()):
		out.append(
			finding(
				NAME_SIZE_WORD_INCH,
				"Write sizes as digits with an inch mark, not the word INCH.",
			)
		)
	if _WORD_GREY.search(body.upper()):
		out.append(
			finding(NAME_COLOUR_GREY, "Use GRAY, not GREY.", suggestion=re.sub(_WORD_GREY, "GRAY", body.upper()))
		)

	for part in populated:
		upper_part = part.upper()
		if _SCHEDULE_ONLY.match(upper_part):
			out.append(
				finding(
					NAME_SCHEDULE_OWN_SEGMENT,
					f"{part!r} is a schedule on its own. Append it to the size segment: "
					"'2-1/2\" SCH80'.",
					segment=part,
				)
			)
			break
	for part in populated:
		if _SCHEDULE_LEADING.match(part.upper()):
			out.append(
				finding(
					NAME_SCHEDULE_BEFORE_SIZE,
					f"{part!r} puts the schedule before the size. Size first.",
					segment=part,
				)
			)
			break

	if code and normalise(body) and normalise(body) == normalise(code):
		out.append(
			finding(
				NAME_EQUALS_CODE,
				"The Item Name repeats the Item Code, so the record carries no description.",
			)
		)

	out.extend(_check_category(populated[0] if populated else "", brands))
	return out


def _check_category(first_segment: str, brands=()) -> list[dict]:
	"""At most one finding about the CATEGORY, highest-precedence first.

	One finding on purpose: `SAND FILTER` is simultaneously unapproved, a sub-type led
	name and on Tier 3, and saying so three times tells the reader nothing extra while
	burying the sentence that names the fix.
	"""
	category = _squash(first_segment)
	if not category:
		return []
	if category in TIER3_REPLACEMENTS:
		replacement = TIER3_REPLACEMENTS[category]
		extra = {}
		if category in TIER3_REPLACEMENT_UNAPPROVED:
			extra["replacement_is_unapproved"] = True
		return [
			finding(
				NAME_CATEGORY_TIER3,
				f"{category!r} is on Appendix A Tier 3. Use {replacement!r}."
				+ (
					"  Note that the SOP's own replacement is not itself on Tier 1 or Tier 2 — "
					"raise it with the Process Owner rather than creating the category."
					if category in TIER3_REPLACEMENT_UNAPPROVED
					else ""
				),
				suggestion=replacement,
				**extra,
			)
		]
	if category in APPROVED_CATEGORIES:
		return []
	if category[:1].isdigit():
		return [
			finding(
				NAME_CATEGORY_IS_SIZE,
				f"{category!r} leads with a size. A size is never a category; it belongs in "
				"segment 5.",
			)
		]
	brand_set = {_squash(b) for b in (brands or ()) if _squash(b)}
	if category in brand_set:
		return [
			finding(
				NAME_CATEGORY_IS_BRAND,
				f"{category!r} is a brand. A brand is never a category; move it to KEY "
				"FEATURE or RATING.",
			)
		]
	singular = _singular(category)
	if singular:
		return [
			finding(
				NAME_CATEGORY_PLURAL,
				f"The CATEGORY is singular, always. Use {singular!r}.",
				suggestion=singular,
			)
		]
	return [
		finding(
			NAME_CATEGORY_UNAPPROVED,
			f"{category!r} is not on Appendix A. Do not invent a category — request the "
			"addition from the Process Owner (SOP §5 Step 4.1).",
		)
	]


def _singular(category: str) -> str | None:
	"""The approved singular of a plural category, or ``None``.

	Three forms, tried longest-suffix first, and every one of them has to land on a word
	that IS approved before it fires — which is what stops the check misfiring on a
	category that merely ends in S. `BATTERIES` is why the IES form is here rather than
	just stripping the S: it is live on five records, the SOP says singular always, and
	`BATTERIE` is not a word.
	"""
	for suffix, replacement in (("IES", "Y"), ("ES", ""), ("S", "")):
		if not category.endswith(suffix) or len(category) <= len(suffix):
			continue
		candidate = _squash(category[: -len(suffix)] + replacement)
		if candidate in APPROVED_CATEGORIES:
			return candidate
	return None


def check_supporting(item_group: str | None, stock_uom: str | None) -> list[dict]:
	"""Item Group and stock UOM (SOP §5 Step 4.4)."""
	out: list[dict] = []
	group = (item_group or "").strip()
	if not group:
		out.append(finding(GROUP_MISSING, "No Item Group set."))
	elif group == ROOT_ITEM_GROUP:
		out.append(
			finding(
				GROUP_IS_ROOT,
				f"{ROOT_ITEM_GROUP!r} is the root of the tree, not a classification. Choose a "
				"real group.",
			)
		)
	if not (stock_uom or "").strip():
		out.append(finding(STOCK_UOM_MISSING, "No stock UOM set."))
	return out


def check_code_name_agreement(code: str | None, name: str | None) -> list[dict]:
	"""Does a CON- code's descriptor appear anywhere in the name? Consumables only.

	Not run on vendor part numbers, and that restriction is the whole reason this check
	is safe: a vendor number is arbitrary by design, so "the code does not resemble the
	name" is its normal state and firing there would bury the one real case in 410 false
	ones.
	"""
	text = (code or "").strip().upper()
	if classify_code_family(text) != FAMILY_CONSUMABLE:
		return []
	body = (name or "").strip()
	if not body:
		return []
	parts = text.split("-")
	descriptor = [p for p in parts[2:] if len(p) >= 3]
	if not descriptor:
		return []
	haystack = normalise(body)
	if any(p in haystack for p in descriptor):
		return []
	return [
		finding(
			CODE_NAME_DISAGREES,
			f"None of the code's descriptor tokens ({', '.join(descriptor)}) appear in the "
			"Item Name. Check the two describe the same physical thing.",
			descriptor=descriptor,
		)
	]


# --- duplicates and neighbours -------------------------------------------------


def find_duplicates(code: str | None, name: str | None, corpus, exclude_code: str | None = None) -> dict:
	"""Exact and normalised collisions against the corpus.

	``corpus`` is any iterable of dicts carrying ``item_code`` and ``item_name``; the
	caller's query shape is the caller's business.

	What this cannot see is the more common failure. Two records for one physical part
	under two vendor numbers and two differently-worded names collide on nothing — the
	live pair is `PUMP, VARIONAUT, 150, 24 V, /DMX/02` against
	`PUMP, VARIONAUT 150, DMX/02, 24 V`, which normalise differently because the word
	order differs. :func:`similar_records` is the surface for that, and the judgement
	stays with the reader.
	"""
	skip = normalise(exclude_code) if exclude_code else None
	want_code = normalise(code)
	want_name = normalise(name)
	exact: list[dict] = []
	by_code: list[dict] = []
	by_name: list[dict] = []
	for row in corpus or ():
		row_code = row.get("item_code") or ""
		if skip and normalise(row_code) == skip:
			continue
		if code and row_code.strip() == (code or "").strip():
			exact.append(_row(row))
			continue
		if want_code and normalise(row_code) == want_code:
			by_code.append(_row(row))
		if want_name and normalise(row.get("item_name")) == want_name:
			by_name.append(_row(row))
	return {
		"exact": exact,
		"normalised_code": by_code,
		"normalised_name": by_name,
		"normalisation": NORMALISATION,
	}


def duplicate_findings(duplicates: dict) -> list[dict]:
	"""Turn a :func:`find_duplicates` result into findings."""
	out: list[dict] = []
	if duplicates.get("exact"):
		codes = [r["item_code"] for r in duplicates["exact"]]
		out.append(
			finding(
				DUPLICATE_CODE_EXACT,
				f"An Item with this exact code already exists: {', '.join(codes)}. Use it.",
				matches=codes,
			)
		)
	if duplicates.get("normalised_code"):
		codes = [r["item_code"] for r in duplicates["normalised_code"]]
		out.append(
			finding(
				DUPLICATE_CODE_NORMALISED,
				f"An existing Item Code differs only in punctuation or case: "
				f"{', '.join(codes)}.",
				matches=codes,
			)
		)
	if duplicates.get("normalised_name"):
		codes = [r["item_code"] for r in duplicates["normalised_name"]]
		out.append(
			finding(
				DUPLICATE_NAME_NORMALISED,
				f"An existing Item Name is identical after normalisation: {', '.join(codes)}. "
				"Either this is the same item, or both names need whatever tells them apart.",
				matches=codes,
			)
		)
	return out


def _row(row: dict) -> dict:
	return {
		"item_code": row.get("item_code") or "",
		"item_name": row.get("item_name") or "",
		"item_group": row.get("item_group") or "",
	}


def similar_records(
	name: str | None,
	corpus,
	limit: int = DEFAULT_SIMILAR_LIMIT,
	min_score: float = DEFAULT_SIMILARITY_MIN_SCORE,
	exclude_code: str | None = None,
) -> list[dict]:
	"""The nearest existing names, rarest-token-first, as ``{record…, "score": float}``.

	Scored by inverse document frequency over the **whole** corpus: a shared `PVC` is
	worth almost nothing because 63 records have it, a shared `VARIONAUT` is worth a lot.
	That weighting is why this module reads the whole corpus rather than a narrowed
	query — document frequency computed over a pre-filtered subset would give the same
	candidate different neighbours depending on how the filter happened to be written.

	Deterministic: ties break on `item_code`, so the same corpus always yields the same
	list in the same order.
	"""
	candidate = tokens(name)
	if not candidate:
		return []
	skip = normalise(exclude_code) if exclude_code else None

	rows: list[tuple[dict, tuple[str, ...]]] = []
	frequency: dict[str, int] = {}
	for row in corpus or ():
		if skip and normalise(row.get("item_code")) == skip:
			continue
		row_tokens = tokens(row.get("item_name"))
		rows.append((row, row_tokens))
		for tok in row_tokens:
			frequency[tok] = frequency.get(tok, 0) + 1

	total = max(1, len(rows))

	def weight(tok: str) -> float:
		# +1 so a token no existing record uses still carries weight rather than
		# dividing by zero, and so the rarest possible token cannot dominate outright.
		return total / (1.0 + frequency.get(tok, 0))

	denominator = sum(weight(tok) for tok in candidate) or 1.0
	scored: list[tuple[float, str, dict]] = []
	for row, row_tokens in rows:
		shared = [tok for tok in candidate if tok in row_tokens]
		if not shared:
			continue
		score = sum(weight(tok) for tok in shared) / denominator
		if score >= min_score:
			scored.append((score, str(row.get("item_code") or ""), row))
	scored.sort(key=lambda item: (-item[0], item[1]))

	out = []
	for score, _code, row in scored[: max(0, int(limit))]:
		record = _row(row)
		record["score"] = round(score, 4)
		out.append(record)
	return out


# --- the composer --------------------------------------------------------------


def evaluate(
	candidate: dict,
	corpus,
	brands=(),
	reserved_codes: dict | None = None,
	similar_limit: int = DEFAULT_SIMILAR_LIMIT,
	min_score: float = DEFAULT_SIMILARITY_MIN_SCORE,
) -> dict:
	"""Everything this module can say about one proposed Item, in one dict.

	``candidate``       ``item_code``, ``item_name``, ``item_group``, ``stock_uom``
	``corpus``          every existing Item as ``{item_code, item_name, item_group}``
	``brands``          brand names from live data, for the brand-as-category check
	``reserved_codes``  ``{prefix: [codes]}`` for block occupancy. Defaults to the corpus,
	                    but the caller should widen it — a `Configurable Product` holds a
	                    `PDT-` number from the moment it is created, and its Item is
	                    generated from that number afterwards, so for as long as that gap
	                    is open the number is allocated and `tabItem` cannot see it.

	Nothing here raises on bad input and nothing here writes. An unparseable candidate
	comes back as findings, which is the whole contract: the caller decides what a
	finding means, and on this doctype the answer is always "tell a human".
	"""
	item_code = (candidate or {}).get("item_code")
	item_name = (candidate or {}).get("item_name")
	item_group = (candidate or {}).get("item_group")
	stock_uom = (candidate or {}).get("stock_uom")

	corpus = list(corpus or ())
	findings: list[dict] = []
	findings.extend(check_code(item_code))

	family = classify_code_family(item_code)
	prefix = (item_code or "").strip().upper().split("-", 1)[0]
	if reserved_codes is None:
		reserved_codes = {p: [r.get("item_code") for r in corpus] for p in RESERVED_PREFIXES}
	occupied = occupancy(reserved_codes.get(prefix) or [], prefix) if prefix in RESERVED_PREFIXES else {}
	block_findings, block = check_block(item_code, occupied)
	findings.extend(block_findings)

	duplicates = find_duplicates(item_code, item_name, corpus, exclude_code=None)
	findings.extend(duplicate_findings(duplicates))

	findings.extend(check_name(item_name, item_code, brands))
	findings.extend(check_supporting(item_group, stock_uom))
	findings.extend(check_code_name_agreement(item_code, item_name))

	populated = [part for part in segments(item_name) if part]
	return {
		"verdict": verdict(findings),
		"family": family,
		"findings": findings,
		"duplicates": duplicates,
		"similar": similar_records(item_name, corpus, similar_limit, min_score, exclude_code=item_code),
		"block": block,
		"segments": {
			"values": populated,
			"labels": list(SEGMENTS[: len(populated)]),
			# Load-bearing disclaimer, not a formality. The labels above are the schema's
			# positions, NOT a classification of these strings -- four of the seven
			# segments have no decidable shape and no vocabulary. See the module docstring.
			"slots_are_positional": True,
		},
		"normalisation": NORMALISATION,
		"similarity": {"limit": similar_limit, "min_score": min_score},
	}
