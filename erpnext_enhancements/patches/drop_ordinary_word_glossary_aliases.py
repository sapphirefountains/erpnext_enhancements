"""Stop a term claiming an everyday English word that is nowhere in its own name.

``drop_fragment_glossary_aliases`` (v1.471.0) fixed the reported case -- hovering **flooded** in a
sentence about flooding a planter bed offered *Flooded suction* -- by dropping aliases that were one
lower-case word lifted out of the term's own name. It fixed that instance and missed the class.

Opening the first lesson of *Using the Training Module* on production afterwards, exactly two words
in the whole lesson were marked up, and both were wrong::

    "Open My Training in the sidebar."        -> OL       (a meter reading, off the alias `open`)
    "goes overdue if you miss it"             -> Holiday  (a missed spot in a coating, off `miss`)

Neither `open` nor `miss` appears in `OL` or `Holiday`, so the fragment rule never looked at them.
These are the other half of the same problem: an AI-drafted entry listing the ordinary words a
technician *might* say, which on a page of ordinary prose means the term claims ordinary prose.

The seven, and why each alias goes but the term stays
---------------------------------------------------------------------------

===========================  =========================  =================================
Term                         Dropped                    Kept, and still matching
===========================  =========================  =================================
``OL``                       ``open``                   ``open circuit``, ``over limit``,
                                                        ``over range``, ``O.L.``, ``OL``
``Holiday``                  ``miss``, ``misses``,      ``holidays``, ``holidayed``
                             ``skip``
``Drop``                     ``fall``                   ``drops``, ``vertical drop``,
                                                        ``total drop``
``Pitch``                    ``fall``                   ``pitched``, ``slope``, ``slopes``,
                                                        ``cross-fall``, ``falls``
``Ungrounded conductor``     ``hot``                    ``hot line``, ``hot conductor``,
                                                        ``the hot``
===========================  =========================  =================================

Every row keeps a spelling that is unambiguous in prose. ``Ungrounded conductor`` is the one worth
pausing on: **hot** genuinely is what an electrician calls it, so the trade usage is not being
thrown away -- ``the hot``, ``hot line`` and ``hot conductor`` all survive, and those are the forms
that cannot collide with hot water, a hot day or a hot pump. The bare adjective is the only casualty
and it is the only one that was firing on the wrong sentences.

Named rather than ruled, deliberately
---------------------------------------------------------------------------

The obvious generalisation is a stop-word list, and it is the wrong tool. ``fall`` is ordinary
English *and* the trade word for the slope on a deck; ``drop``, ``head``, ``return``, ``run`` and
``bed`` are all both. Deciding which side a word lands on is knowing the trade, so the seven are
named, measured against production on 2026-09-15 (704 enabled terms, and these were the entire set
where an alias was a common English word absent from its own term name).

The durable fix is not a longer list. These aliases were written by a model, and the glossary review
queue (``training/glossary_review.py``, v1.470.0) is where a person accepts or rejects what it
wrote. Terms whose aliases are ordinary words belong in that queue, not in a patch, and a patch is
what this is only because these seven are already live and already wrong on screen.

Fill-safe: an alias somebody has since edited is left alone, because the drop is keyed on the exact
spelling listed below rather than on position.
"""

import frappe

DOCTYPE = "Training Glossary Term"

#: ``term -> aliases to drop``. Exact spellings, compared case-insensitively after stripping.
ORDINARY = {
	"OL": {"open"},
	"Holiday": {"miss", "misses", "skip"},
	"Drop": {"fall"},
	"Pitch": {"fall"},
	"Ungrounded conductor": {"hot"},
}


def execute() -> None:
	# A patch that raises aborts `bench migrate`, which on this repo IS the deploy.
	if not frappe.db.exists("DocType", DOCTYPE):
		return

	dropped = []
	missing = []
	for term, unwanted in sorted(ORDINARY.items()):
		row = frappe.db.get_value(DOCTYPE, {"term": term}, ["name", "aliases"], as_dict=True)
		if not row:
			# Named, not silently skipped: a term that has been renamed or merged away since this
			# was written is a fact worth seeing in the deploy log rather than a no-op.
			missing.append(term)
			continue

		keep, gone = _split(row["aliases"], unwanted)
		if not gone:
			continue
		try:
			# `db.set_value` rather than a save, for the reason `drop_fragment_glossary_aliases`
			# gives: the controller re-validates every field, and an entry with a blank
			# `ordinary_meaning` -- there are some -- would throw and take the sweep with it.
			frappe.db.set_value(DOCTYPE, row["name"], "aliases", "\n".join(keep))
			dropped.append(f"{term}: {', '.join(gone)}")
		except Exception:
			frappe.log_error(
				title="Glossary ordinary-word alias sweep failed",
				message=f"{row['name']}: {frappe.get_traceback()}",
			)
			continue

	if dropped:
		frappe.db.commit()

	print(f"drop_ordinary_word_glossary_aliases: {len(dropped)} entries tidied")
	for line in dropped:
		print(f"  {line}")
	for term in missing:
		print(f"  (not found, nothing done): {term}")


def _split(aliases, unwanted):
	"""``(kept, dropped)`` for one entry's alias list."""
	lowered = {a.lower() for a in unwanted}
	keep = []
	gone = []
	for raw in (aliases or "").splitlines():
		alias = raw.strip()
		if not alias:
			continue
		if alias.lower() in lowered:
			gone.append(alias)
		else:
			keep.append(alias)
	return keep, gone
