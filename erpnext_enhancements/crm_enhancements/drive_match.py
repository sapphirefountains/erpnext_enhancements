"""Pure fuzzy-matching helpers for linking ERPNext records to existing Google
Drive folders — **no frappe, no network** (unit-tested bench-free in
``tests/test_drive_match.py``).

The Drive Link Manager dashboard (``drive_link_manager.py``) uses these to rank
candidate Drive folders for each unlinked Project / Customer / Opportunity, so a
System Manager can review and approve auto-suggested matches *before* anything is
linked. Keeping the scoring here, frappe-free, is what lets it be tested fast in
CI and reasoned about in isolation.

Scoring blends three cheap signals on normalized names:

* difflib sequence ratio (character-level closeness),
* token-set overlap (word-level closeness, order-independent), and
* a small containment bonus (one name fully inside the other — e.g. a folder
  that appends a suffix to the record name).
"""

import re
from difflib import SequenceMatcher

# Confidence tiers over a 0..100 score. ``High`` matches are pre-selected for
# approval in the dashboard; ``Medium`` / ``Low`` are shown but need a deliberate
# pick; ``None`` means "no usable suggestion" (the record shows as unmatched, to
# be searched manually or given a freshly created folder).
TIER_HIGH = 88
TIER_MEDIUM = 70
TIER_LOW = 50

# Leading record-id tokens stripped before comparing, so "PRJ-00694 Smith
# Residence" matches a plain "Smith Residence" folder and vice-versa. Covers the
# multi-segment id forms this app mints (e.g. "CRM-OPP-2026-00112"): a keyword,
# then any run of digits/dashes/colons/spaces — requiring at least one digit so a
# real word like "Oppenheimer" or "Custom Homes" is never mistaken for an id.
_ID_PREFIX = re.compile(
	r"^(?:prj|crm-opp|opp|cust|hd-?ticket)[\s\-–—:]*\d[\s\-–—:\d]*\s*", re.I
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(text):
	"""Lowercase, drop a leading record-id token, collapse every run of
	non-alphanumerics to a single space, and trim. Returns ``""`` for falsy
	input."""
	if not text:
		return ""
	text = str(text).strip().lower()
	text = _ID_PREFIX.sub("", text)
	text = _NON_ALNUM.sub(" ", text)
	return text.strip()


def _token_set(normalized):
	return {t for t in normalized.split() if t}


def similarity(a, b):
	"""A 0..100 similarity between two names, robust to word order and to extra
	or missing tokens. Both inputs are :func:`normalize`-d first; identical
	normalized names score 100.0, no overlap scores 0.0."""
	na, nb = normalize(a), normalize(b)
	if not na or not nb:
		return 0.0
	if na == nb:
		return 100.0
	ratio = SequenceMatcher(None, na, nb).ratio()
	ta, tb = _token_set(na), _token_set(nb)
	overlap = len(ta & tb) / len(ta | tb) if (ta or tb) else 0.0
	contained = 1.0 if (na in nb or nb in na) else 0.0
	# Token overlap carries the most weight so reordered words ("Reno Pool
	# Smith" vs "Smith Pool Reno") still rank as a strong match; the char ratio
	# catches typos, and containment softens a folder that only adds a suffix.
	blended = 0.30 * ratio + 0.60 * overlap + 0.10 * contained
	return round(blended * 100, 1)


def tier_for_score(score):
	"""Map a 0..100 score to ``High`` / ``Medium`` / ``Low`` / ``None``."""
	if score >= TIER_HIGH:
		return "High"
	if score >= TIER_MEDIUM:
		return "Medium"
	if score >= TIER_LOW:
		return "Low"
	return "None"


def best_matches(aliases, folders, limit=3):
	"""Rank ``folders`` against a record described by one or more ``aliases``
	(alternative name forms — e.g. ``["PRJ-00694 Smith Residence", "Smith
	Residence", "PRJ-00694"]``). Each folder is scored as its **best** score
	across all aliases, so any naming form that matches wins.

	Args:
		aliases: iterable of candidate name strings for the record (falsy ones
			are ignored).
		folders: iterable of dicts each carrying at least a ``name`` key; the
			whole dict is passed through untouched on the result (so callers keep
			``id`` / ``path`` / ``webViewLink`` alongside the score).
		limit: how many top matches to return.

	Returns:
		list[dict]: up to ``limit`` ``{"folder": <dict>, "score": <float>}``
		entries, best score first.
	"""
	aliases = [a for a in aliases if a]
	ranked = []
	for folder in folders:
		fname = folder.get("name", "") if isinstance(folder, dict) else str(folder)
		best = max((similarity(alias, fname) for alias in aliases), default=0.0)
		ranked.append({"folder": folder, "score": best})
	ranked.sort(key=lambda row: row["score"], reverse=True)
	return ranked[:limit]
