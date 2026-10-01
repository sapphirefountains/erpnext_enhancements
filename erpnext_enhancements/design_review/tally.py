"""Counting rankings and verdicts. Pure functions, stdlib only.

The scoring is the one the claude.ai ballots used, so a review imported from one of them
reads the same here: a ranking of N options gives the first choice N points and the last
choice 1 (a Borda count). Imported ballots are counted **apart** from live ones and never
summed into them (WI-079 slice 5): they were cast by proxy, by name, before anyone needed an
ERPNext login to vote, and ADR 0016 §2 allows no proxy voting on a live review.

Tabs, per ``CLAUDE.md``. ``tests/test_design_review_codes.py`` covers this module too.
"""

from __future__ import annotations


def clean_ranking(ranking: list[str], options: list[str]) -> list[str]:
	"""A ranking the review can store: every option exactly once, nothing else.

	Raises ``ValueError`` naming what is wrong, so the endpoint can say it in a sentence.
	"""
	ranking = [str(code).strip() for code in (ranking or [])]
	options = [str(code) for code in options]
	missing = [code for code in options if code not in ranking]
	unknown = [code for code in ranking if code not in options]
	repeated = sorted({code for code in ranking if ranking.count(code) > 1})
	if missing or unknown or repeated:
		parts = []
		if missing:
			parts.append("missing " + ", ".join(missing))
		if unknown:
			parts.append("not in this track: " + ", ".join(unknown))
		if repeated:
			parts.append("listed twice: " + ", ".join(repeated))
		raise ValueError("Rank every option once (" + "; ".join(parts) + ").")
	return ranking


def borda(rankings: list[list[str]], options: list[str]) -> dict:
	"""``{"points": {code: n}, "first": {code: count}, "voters": n}`` for a list of rankings.

	A stored ranking that no longer covers the track (an option added by a later revision) is
	scored on the options it does name; the new option simply earns nothing from it.
	"""
	n = len(options)
	points = {code: 0 for code in options}
	first = {code: 0 for code in options}
	voters = 0
	for ranking in rankings:
		named = [code for code in (ranking or []) if code in points]
		if not named:
			continue
		voters += 1
		for position, code in enumerate(named):
			points[code] += max(0, n - position)
		first[named[0]] += 1
	return {"points": points, "first": first, "voters": voters}


VERDICTS = ("Yes", "Maybe", "No")


def verdict_counts(rows: list[dict]) -> dict:
	"""``{"L3:S04": {"Yes": 2, "Maybe": 0, "No": 1}}`` from rows with option_code, screen_code, verdict."""
	out: dict[str, dict[str, int]] = {}
	for row in rows or []:
		key = f"{row.get('option_code')}:{row.get('screen_code')}"
		bucket = out.setdefault(key, {v: 0 for v in VERDICTS})
		if row.get("verdict") in bucket:
			bucket[row["verdict"]] += 1
	return out
