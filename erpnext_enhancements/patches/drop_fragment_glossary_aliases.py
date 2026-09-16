"""Stop a term claiming one ordinary word out of its own name.

Reported from the live panel: hovering **flooded** — in a sentence about flooding a planter bed —
offered *Flooded suction*, which is a way of mounting a pump. ``Flooded suction`` carried ``flooded``
as an alias, so any use of that everyday word matched it.

That is a shape, not one bad row. An alias which is a single word **taken from the term's own
name** is not a synonym for the term, it is a fragment of it:

    Flooded suction  -> flooded          Mechanical seal -> seal
    Circuit breaker  -> breaker          Sub-panel       -> panel
    Float switch     -> float            Return inlet    -> return
    Water hammer     -> hammer           Impeller eye    -> eye
    Conduit body     -> body             Head loss       -> loss

Every one of those is an English word a lesson uses in its ordinary sense several times a page, and
with the words now marked up in the text each one gets a dotted underline offering the wrong
definition. **Nothing is lost by removing them**: the term still matches its own full name, so a
lesson actually discussing flooded suction still finds it. What goes is the claim on the fragment.

Two conditions, and the second is the one that keeps this honest
---------------------------------------------------------------

An alias is dropped only when it is a single word appearing in its own term's name **and** it is
written in lower case. The case test spares the ones where the fragment is the thing people
actually say:

    ASHRAE Standard 188 -> ASHRAE        NEMA Type 6P -> NEMA
    NFPA 70E            -> NFPA          PTFE tape    -> PTFE
    IP rating           -> IP            UV chamber   -> UV
    Langelier Saturation Index -> Langelier

Nobody writes "NEMA" or "Langelier" meaning anything else, so those keep their claim. Numbers are
spared for the same reason — ``316`` for *316 stainless*, ``680`` for *Article 680* — a lesson
writing 680 in a fountain course means the article.

Measured on production 2026-09-15: 166 aliases matched the fragment test across 158 terms, and the
lower-case condition is what separates the ones worth removing from the ones worth keeping.
"""

import re

import frappe

DOCTYPE = "Training Glossary Term"


def execute() -> None:
	# A patch that raises aborts `bench migrate`, which on this repo IS the deploy.
	if not frappe.db.exists("DocType", DOCTYPE):
		return

	dropped = []
	for row in frappe.get_all(DOCTYPE, fields=["name", "term", "aliases"]):
		keep, gone = _split(row["term"], row["aliases"])
		if not gone:
			continue
		try:
			# `db.set_value` rather than a save: the controller's own validation would re-clean
			# these fields and a term whose `ordinary_meaning` is blank -- there are some -- would
			# throw on the way through and take the rest of the sweep with it.
			frappe.db.set_value(DOCTYPE, row["name"], "aliases", "\n".join(keep))
			dropped.append(f"{row['term']}: {', '.join(gone)}")
		except Exception:
			frappe.log_error(
				title="Glossary fragment alias sweep failed",
				message=f"{row['name']}: {frappe.get_traceback()}",
			)
			continue

	if dropped:
		frappe.db.commit()

	print(f"drop_fragment_glossary_aliases: {len(dropped)} entries tidied")
	for line in dropped:
		print(f"  {line}")


def _split(term, aliases):
	"""``(kept, dropped)`` for one entry's alias list."""
	words = set(re.findall(r"[a-z0-9]+", (term or "").lower()))
	if len(words) < 2:
		# A single-word term has no fragment to give away: its only word IS the term, and
		# `_clean_aliases` already refuses an alias equal to the term.
		return [a.strip() for a in (aliases or "").splitlines() if a.strip()], []

	keep = []
	gone = []
	for raw in (aliases or "").splitlines():
		alias = raw.strip()
		if not alias:
			continue
		if _is_fragment(alias, words):
			gone.append(alias)
		else:
			keep.append(alias)
	return keep, gone


def _is_fragment(alias, term_words):
	"""One lower-case word of the term's own name, standing in for the whole term."""
	if " " in alias or "-" in alias:
		return False
	if alias != alias.lower():
		# Capitals mean an acronym or a proper noun -- NEMA, PTFE, Langelier. Nobody writes those
		# meaning anything else, so the fragment is the thing people actually say.
		return False
	if not re.search(r"[a-z]", alias):
		# Pure digits: `316` for 316 stainless, `680` for Article 680. An identifier, not a word.
		return False
	return alias.lower() in term_words
