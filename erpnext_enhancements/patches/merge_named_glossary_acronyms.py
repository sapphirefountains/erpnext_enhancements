"""Fold the five standalone acronym entries into the terms they abbreviate.

``merge_acronym_glossary_terms`` handled the ``X (ABBR)`` shape mechanically, because the bracket
is evidence: it names its own base term. These five are the other shape — a bare acronym standing
as its own entry beside the spelled-out one, with nothing in the name to connect them:

    AHJ    -> Authority having jurisdiction
    ISPSC  -> International Swimming Pool and Spa Code
    LSI    -> Langelier Saturation Index
    TDS    -> Total dissolved solids
    VFD    -> Variable frequency drive

**No rule can find these safely, which is why they are written down here instead.** Deciding that
``LSI`` means the Langelier Saturation Index rather than a large-scale integration circuit is
knowing the trade, and a rule loose enough to pair them automatically would also pair things that
merely look alike. Every pair in this list was named explicitly.

The acronym is the one that goes, and it survives as an alias of the entry that stays — so a lesson
saying "LSI" still matches, it just reaches one entry instead of two. That is the whole point: a
learner hovering the word was getting two panels for one idea.

Uses ``glossary_review.merge_into``, the same code the review screen's button calls, so the
guarantees hold here too: an entry a person has written or checked is never deleted, every spelling
moves across first, and every ``see_also`` elsewhere is repointed.
"""

import frappe

DOCTYPE = "Training Glossary Term"

#: ``(acronym, spelled out)``. The acronym is deleted; the spelled-out entry is kept.
PAIRS = (
	("AHJ", "Authority having jurisdiction"),
	("ISPSC", "International Swimming Pool and Spa Code"),
	("LSI", "Langelier Saturation Index"),
	("TDS", "Total dissolved solids"),
	("VFD", "Variable frequency drive"),
)


def execute() -> None:
	# A patch that raises aborts `bench migrate`, which on this repo IS the deploy.
	if not frappe.db.exists("DocType", DOCTYPE):
		return

	from erpnext_enhancements.training.glossary_review import merge_into

	merged = []
	skipped = []
	for acronym, spelled_out in PAIRS:
		if not frappe.db.exists(DOCTYPE, acronym):
			skipped.append(f"{acronym}: no such entry")
			continue
		if not frappe.db.exists(DOCTYPE, spelled_out):
			# Never create the survivor. If the spelled-out entry is gone, somebody has already
			# reorganised this and guessing which entry should absorb the acronym is not a guess
			# a patch gets to make.
			skipped.append(f"{acronym}: {spelled_out} is not there to merge into")
			continue
		try:
			result = merge_into(acronym, spelled_out)
			merged.append(f"{acronym} -> {spelled_out} ({len(result['spellings_moved'])} spellings kept)")
		except Exception as exc:
			skipped.append(f"{acronym}: {exc}")
			frappe.db.rollback()
			continue

	if merged:
		frappe.db.commit()

	print(f"merge_named_glossary_acronyms: {len(merged)} merged, {len(skipped)} left alone")
	for line in merged:
		print(f"  merged: {line}")
	for line in skipped:
		print(f"  skipped: {line}")
