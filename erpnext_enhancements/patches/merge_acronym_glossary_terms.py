"""Fold ``X (ABBR)`` into ``X`` where ABBR is genuinely that term's acronym.

``seed_training_glossary`` wrote entries independently, so a handful of concepts got written twice
— once under their name and once under their name with the abbreviation in brackets — each with its
own definition. Measured on production 2026-09-15, nine names collided after normalising away the
parenthetical, covering nineteen entries:

    Authority having jurisdiction (AHJ)          Safety data sheet (SDS)
    International Swimming Pool and Spa Code     Total dissolved solids (TDS)
    Langelier Saturation Index (LSI)             Ultraviolet (UV)
    Lock-out tag-out / Lock-out/tag-out (LOTO)   Variable frequency drive (VFD)
    Scale | Scale (on a drawing)                 <- NOT a duplicate

**That last one is why this patch does not merge on name similarity.** *Scale* on a drawing and
*scale* in a basin are different concepts that share a word; the bracket there is a disambiguator,
not an abbreviation, and folding them together would produce one entry that is wrong about both. So
the rule is narrow and mechanical: merge only when the bracketed text is an **acronym of the base
term** — its letters are the initials of the words in front of it. That matches the eight and
refuses "on a drawing" without needing to know anything about fountains.

The merge itself is ``glossary_review.merge_into``, the same code the review screen's button calls:
the abbreviation survives as an alias of the entry that is kept, every other entry's ``see_also``
is repointed, and an entry a person has written or checked is never deleted. So no lesson stops
matching a word it used to match, and nothing is left pointing at a name that has gone.

**Which entry wins**: the plain one, because it is the name a reader looks for and a lesson is more
likely to spell out. What it loses is its own definition only when the other entry's is better —
and judging that is not mechanical, so this does not try: the winner keeps its text and the loser's
wording goes with it. Both were drafted by the same machine on the same day; the review queue is
where somebody decides whether what survived is any good.
"""

import re

import frappe

DOCTYPE = "Training Glossary Term"


def execute() -> None:
	# A patch that raises aborts `bench migrate`, which on this repo IS the deploy. A tidier
	# glossary is not worth a half-finished deploy, so every failure here is quiet.
	if not frappe.db.exists("DocType", DOCTYPE):
		return

	merged = []
	skipped = []

	for base, bracketed in _acronym_pairs():
		try:
			from erpnext_enhancements.training.glossary_review import merge_into

			result = merge_into(bracketed, base)
			merged.append(f"{bracketed} -> {base} ({len(result['spellings_moved'])} spellings kept)")
		except Exception as exc:
			# Named, never silent. A pair that could not be merged -- most likely because somebody
			# has already reviewed it -- is a fact worth reading in the deploy log.
			skipped.append(f"{bracketed}: {exc}")
			frappe.db.rollback()
			continue

	if merged:
		frappe.db.commit()

	print(f"merge_acronym_glossary_terms: {len(merged)} merged, {len(skipped)} left alone")
	for line in merged:
		print(f"  merged: {line}")
	for line in skipped:
		print(f"  skipped: {line}")


def _acronym_pairs():
	"""Every ``(base, bracketed)`` pair where the bracket holds the base term's own acronym."""
	names = frappe.get_all(DOCTYPE, pluck="name")
	have = {name.strip().lower(): name for name in names}

	pairs = []
	for name in names:
		found = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", name)
		if not found:
			continue
		base, bracket = found.group(1).strip(), found.group(2).strip()
		if not base or not _is_acronym_of(bracket, base):
			continue
		owner = have.get(base.lower())
		if owner and owner != name:
			pairs.append((owner, name))
	return pairs


def _is_acronym_of(bracket, base):
	"""Whether ``bracket`` reads as an abbreviation of ``base``.

	Three conditions, and the first is doing most of the work:

	* **Upper case.** A lower-case bracket is prose — "(on a drawing)" — and prose in brackets is
	  somebody telling two meanings of a word apart, which is the one case that must never merge.
	* **Two to eight letters.** Longer than that is a phrase.
	* **Its letters appear in order inside the base term**, first letter first.

	The last one is a subsequence test rather than a strict initials test, because a strict one is
	wrong twice in this very glossary: *International Swimming Pool and Spa Code* abbreviates to
	**ISPSC**, skipping "and" the way every acronym skips joining words, and *Ultraviolet* is one
	word abbreviating to **UV**. Both were refused by initials-only and both are plainly
	abbreviations. Checked against all nine real collisions before this shipped.

	Loose on its own, and it is not on its own: nothing merges unless an entry already exists under
	the base name exactly, which is the condition actually identifying a duplicate.
	"""
	letters = re.sub(r"[^A-Za-z]", "", bracket)
	if not (2 <= len(letters) <= 8) or letters != letters.upper():
		return False

	flat = re.sub(r"[^A-Za-z]", "", base).upper()
	if not flat or flat[0] != letters[0]:
		return False

	at = 0
	for char in letters:
		at = flat.find(char, at)
		if at < 0:
			return False
		at += 1
	return True
