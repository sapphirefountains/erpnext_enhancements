# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Help glossary — one entry per word a learner might not know.

Written from the ten Technician Program courses: **every term is a word that actually appears in a
lesson**, collected lesson by lesson rather than from a general trade wordlist, so the panel has
something to say about the material people are about to be assigned.

Seeded by ``patches/seed_training_glossary`` into ``Training Glossary Term``, insert-only and keyed
on the term, so a definition somebody corrects stays corrected. Matching happens at read time in
``training/help.py``, which means a term added here surfaces in every lesson that was already using
the word — nothing is stored per lesson, and nothing needs re-running when a course changes.

**The entries live in ``data/glossary.json`` rather than in this file**, and that is the one
structural decision worth explaining. The course specs next door are Python because they are read
and argued with as prose — somebody opens ``module_03_water_chemistry.py`` to disagree with a
lesson. This is a 700-row lookup table with no logic in it at all, and as Python it was a
three-quarter-megabyte module that no editor opens comfortably and ``ruff format`` has to walk on
every commit. Data that nothing computes belongs in a data file.

Four things about the shape of the entries are deliberate.

**``short_definition`` is the only field Help shows during a quiz**, so it is written to define the
*word* without answering a question about the *thing*. Where a fact is the literal answer to a quiz
question — the pH step being tenfold, the ORP band, the breakpoint dose, the bedding depth — it sits
in ``explanation`` instead. Getting that split wrong is how a Help panel becomes an answer key.

**``aliases`` is what decides whether a term is ever found.** Matching is whole-word and
case-insensitive, so ``GFCI`` already matches ``gfci`` but **not** ``GFCIs`` — a plural needs its own
entry in the list, and so does a verb form, a hyphen variant and the expansion of an acronym.

**``trade_trap`` marks the words that mean something else in ordinary English** — bonding,
aggressive, shock, weir, invert, hardness, schedule, shading, spoil, prime, media. They are the most
valuable entries in the set: what somebody gets wrong while feeling completely confident. Help
renders them *above* the definition, because a reader who believes they already know the word will
not read the definition underneath it.

**Examples invent the scenario, never the figure.** An example may put a specific basin on a
specific Tuesday, but any number in it is either one the course itself states or a genuine constant.
Cure times, torques, dose rates and anchor tables are pointed at, never printed — the same rule the
course specs hold to, and for the same reason.
"""

import json
from pathlib import Path

#: Where the entries live. Read once at import; the patch is the only importer, and a glossary
#: that changed under a long-lived worker would be worse than one read a moment too early.
GLOSSARY_PATH = Path(__file__).parent / "data" / "glossary.json"


def _load():
	try:
		return tuple(json.loads(GLOSSARY_PATH.read_text(encoding="utf-8")))
	except (OSError, ValueError):
		# A missing or malformed data file must not stop the module importing -- the seeding patch
		# imports this at migrate time, and a patch that raises aborts `bench migrate`, which on
		# this repo IS the deploy. An empty glossary seeds nothing and says so.
		return ()


GLOSSARY_TERMS = _load()
