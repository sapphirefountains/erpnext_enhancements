"""Element codes and the append-only rule (ADR 0016 §2).

A part of a concept screen is addressed as ``<option>-<screen>-E<nn>``, for example
``L3-S04-E05``: option L3, screen S04, part 5. The part number belongs to the *screen* within
its track, not to the option, so ``L1-S04-E05`` and ``L3-S04-E05`` are the same part ("Video
block") drawn by two options. That is what makes a note comparable across options.

Numbers are **append-only**. Once part 5 of ``learner:S04`` is "Video block", it is "Video
block" for every later revision of the review; a revision may add part 23, never renumber or
rename part 5. Otherwise a note pinned in one revision silently points at a different part in
the next. :func:`check_append_only` is the rule; the importer refuses a whole revision that
breaks it, before writing anything.

Stdlib only, for ``tests/test_design_review_codes.py``. Tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

import re

_CODE = re.compile(r"^(?P<option>[A-Z][A-Z0-9]{0,11})-(?P<screen>[A-Z][A-Z0-9]{0,11})-E(?P<part>\d{1,4})$")


def format_code(option: str, screen: str, part: int) -> str:
	return f"{option}-{screen}-E{int(part):02d}"


def parse_code(code: str) -> tuple[str, str, int] | None:
	"""``("L3", "S04", 5)`` for ``"L3-S04-E05"``; ``None`` for anything else."""
	match = _CODE.match((code or "").strip())
	if not match:
		return None
	return match.group("option"), match.group("screen"), int(match.group("part"))


class RenumberedPart(ValueError):
	"""A revision tried to change what an existing part number means."""


def check_append_only(existing: dict[str, list[list]], incoming: dict[str, list[list]]) -> list[str]:
	"""Refuse a revision that renumbers or renames a part; return what it adds.

	Both maps are ``{"learner:S04": [[1, "Top bar"], [2, "Resume banner"], ...]}``. Raises
	:class:`RenumberedPart` listing every conflict at once, so an author fixes the generator
	in one pass. Returns ``["learner:S04:E23 Quiz card", ...]`` for the parts that are new.

	The rules, per screen key:

	* a number the review already has must keep its name;
	* a name the review already has must keep its number;
	* a number may not appear twice in the incoming list, nor a name;
	* a part the review has and the revision omits is **kept**, not deleted: a note may point
	  at it, and an option that stops drawing a part does not make the part never have been.
	"""
	problems: list[str] = []
	added: list[str] = []
	for key, rows in (incoming or {}).items():
		seen_numbers: dict[int, str] = {}
		seen_names: dict[str, int] = {}
		for number, name in rows:
			number = int(number)
			name = str(name)
			if number in seen_numbers:
				problems.append(f"{key}: E{number:02d} appears twice ({seen_numbers[number]!r}, {name!r})")
			if name in seen_names:
				problems.append(f"{key}: {name!r} appears twice (E{seen_names[name]:02d}, E{number:02d})")
			seen_numbers[number] = name
			seen_names[name] = number
		have = {int(n): str(name) for n, name in (existing or {}).get(key, [])}
		have_names = {name: n for n, name in have.items()}
		for number, name in seen_numbers.items():
			if number in have and have[number] != name:
				problems.append(f"{key}: E{number:02d} is {have[number]!r}; the revision calls it {name!r}")
			elif name in have_names and have_names[name] != number:
				problems.append(
					f"{key}: {name!r} is E{have_names[name]:02d}; the revision numbers it E{number:02d}"
				)
			elif number not in have:
				added.append(f"{key}:E{number:02d} {name}")
	if problems:
		raise RenumberedPart(
			"This revision would change what existing element codes mean, so notes already pinned "
			"to them would point at different parts. Nothing was imported. Fix the generator so "
			"numbers stay put and new parts take new numbers:\n- " + "\n- ".join(problems)
		)
	return added
