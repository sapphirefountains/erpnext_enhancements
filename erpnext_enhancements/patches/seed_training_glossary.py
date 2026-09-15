"""Seed the Help glossary — one ``Training Glossary Term`` per entry in ``glossary_seed``.

The entries were written from the ten Technician Program courses: every term is a word that
actually appears in a lesson, so the panel has something to say on the lessons people are about to
be assigned. Matching happens at read time in ``training/help.py``, so a term seeded here surfaces
in every lesson that already used the word.

**Insert-only and keyed on the term**, the same rule ``training/setup.py`` keeps for categories and
badges: a definition somebody has corrected, re-worded or disabled stays corrected. Running this
twice creates nothing and overwrites nothing.

**Every entry is stamped ``ai_generated`` with no reviewer, and nothing is gated on that.** A
glossary entry is not an answer key — a wrong definition is bad but it does not grade anybody — so
blocking course publication on glossary review would be the wrong coupling entirely. What it does
instead is show, on the entry itself in the Help panel, that nobody has checked it yet. A definition
somebody has stood behind and one a machine wrote last week are different things to rely on when you
are about to go and do the work, and the panel says which it is holding.

A term whose ``short_definition`` is missing is skipped rather than inserted: that field is what the
panel shows **during a quiz**, and an entry that renders as an empty box mid-question is worse than
a term the panel does not know.
"""

import frappe

from erpnext_enhancements.training.glossary_seed import GLOSSARY_TERMS

DOCTYPE = "Training Glossary Term"


def execute() -> None:
	# A patch that raises aborts `bench migrate`, which on this repo IS the deploy -- and the
	# failure is not a stopped deploy but a half-finished one. Every guard here returns quietly.
	if not frappe.db.exists("DocType", DOCTYPE):
		# The doctype arrives in the same release as this patch, but a site part-way through a
		# migrate may not have synced it yet. Not an error: the next migrate is exactly for that.
		return

	created = 0
	skipped = 0
	incomplete = 0

	for entry in GLOSSARY_TERMS:
		term = (entry.get("term") or "").strip()
		if not term:
			continue
		try:
			if frappe.db.exists(DOCTYPE, term):
				skipped += 1
				continue
			if not (entry.get("short_definition") or "").strip():
				# The one field Help shows mid-quiz. An entry without it renders as an empty box
				# at the worst possible moment.
				incomplete += 1
				continue

			frappe.get_doc(
				{
					"doctype": DOCTYPE,
					"term": term,
					"aliases": "\n".join(entry.get("aliases") or []),
					"short_definition": entry["short_definition"],
					"trade_trap": 1 if entry.get("trade_trap") else 0,
					"ordinary_meaning": entry.get("ordinary_meaning") or "",
					"explanation": entry.get("explanation") or "",
					"example": entry.get("example") or "",
					"see_also": "\n".join(entry.get("see_also") or []),
					"category": _category(entry.get("category")),
					"enabled": 1,
					# Said on the record rather than inferred. `help.py` reads the pair and the
					# panel shows "Drafted, not yet checked by a person" until somebody sets a
					# reviewer -- the same provenance discipline the AI-drafted questions carry.
					"ai_generated": 1,
				}
			).insert(ignore_permissions=True)
			created += 1
		except Exception:
			# One bad entry must not take the rest down, and must never abort the migrate.
			# A glossary is a convenience; a deploy is not.
			frappe.log_error(
				title="Training glossary seed failed",
				message=f"{term}: {frappe.get_traceback()}",
			)
			continue

	if created:
		frappe.db.commit()

	print(
		f"seed_training_glossary: {created} terms created, {skipped} already present, "
		f"{incomplete} skipped for having no plain-English definition"
	)


def _category(name):
	"""The Training Category, if it exists on this site.

	Dropped rather than fatal when it does not, the same call ``author_course_from_spec`` makes
	about a course's category: a term filed under nothing is still a term, and refusing to seed
	the glossary because somebody renamed a category would be a poor trade.
	"""
	if name and frappe.db.exists("Training Category", name):
		return name
	return None
