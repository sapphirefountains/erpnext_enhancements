# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The glossary review queue — 717 drafted definitions and, until now, nowhere to check them.

Every entry ``patches/seed_training_glossary`` created is stamped ``ai_generated`` with no
``reviewed_by``, and the Help panel says so on each one: *"Drafted, not yet checked by a person."*
That disclosure is honest and it is also the whole of the accountability — **nothing is gated on
it**, deliberately, because a glossary entry is not an answer key and blocking course publication on
glossary review would be the wrong coupling entirely. A wrong definition is bad; it does not grade
anybody.

Which is exactly why this screen has to exist. The AI question queue is *forced* into use by the
publish gate: nobody can ship a course without working through it. Nothing forces this one. A
backlog with no gate and no screen is a backlog that stays at 717 for ever, and every learner keeps
reading "not yet checked by a person" on every word.

Two jobs, because the glossary has two different problems
----------------------------------------------------------

**Reviewing** is the obvious one: read the definition, correct it or accept it, and put a name to
it.

**Collisions** are the one nobody would go looking for. A term matches lesson text by its name *and
every alias*, so two entries claiming the same spelling both appear in the panel for the same word.
Measured on production 2026-09-15 across all 717: **277 spellings claimed by more than one entry**
— 247 pairs, 28 triples, 2 quadruples.

The important finding is that **most of those are not duplicates and merging them would be wrong**:

* ``vaults`` is claimed by *Equipment vault*, *Reservoir*, *Surge tank* and *Vault* — four genuinely
  different things. The fix is deleting an alias.
* ``spec`` is claimed by *Engineer of record*, *Specification* and *Submittal*. Same.
* ``gfcis`` is claimed by *Class A GFCI*, *GFCI* and *Ground-fault circuit interrupter (GFCI)* —
  and *those* really are one concept written three times.

So the screen offers both moves and does not guess which one applies. ``merge_terms`` folds one
entry into another; ``drop_alias`` removes one spelling from one entry. Deciding whether *vaults* is
a good alias for *Surge tank* is a judgement about the trade.

What is guarded
---------------

**The reviewer is taken from the session and the payload cannot name one.** Same rule, and the same
reason, as ``training/review.py``: a signature you can address to somebody else is not a signature.

**A merge never deletes work somebody has checked.** The losing entry must be ``ai_generated`` and
unreviewed. Its own name and every alias it held move onto the winner first, so no spelling stops
matching, and every *other* entry whose ``see_also`` named it is repointed — ``see_also`` is a plain
text field rather than a child table of Links, so nothing else would notice the name had gone.

**There is no bulk accept**, for the reason the question queue gives: a button that clears the
backlog in one click makes the reviewed/unreviewed distinction meaningless, and that distinction is
the only thing the panel has to tell a learner with.

Indentation is tabs, matching the ``training/`` package. The endpoints live here rather than in
``api/`` for the reason ``review.py`` gives: this is a Desk console's own server half, and
``api/training.py`` carries a boundary contract that an author-facing payload would violate.
"""

import frappe
from frappe import _
from frappe.utils import cint

from erpnext_enhancements.training.doctype.training_glossary_term.training_glossary_term import (
	match_patterns_for,
	normalised_spelling,
)

DOCTYPE = "Training Glossary Term"

#: The pair the Help panel reads to decide whether to say "not yet checked by a person". Restated
#: here as data so the queue and the panel cannot drift about what "pending" means.
PENDING_FILTER = {"ai_generated": 1, "reviewed_by": ["is", "not set"]}

REVIEWER_ROLES = {"Training Author", "Training Manager", "System Manager"}

#: One page of the queue. Reviewing is read-a-definition-and-judge-it work, so the page size is
#: about how much somebody can hold at once rather than about bytes.
PAGE = 25


def _reviewer():
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	if not (REVIEWER_ROLES & set(frappe.get_roles(user))):
		frappe.throw(
			_("Only a Training Author or Training Manager can review glossary entries."),
			frappe.PermissionError,
		)
	return user


def _all_terms():
	return frappe.get_all(
		DOCTYPE,
		fields=["name", "term", "aliases", "enabled", "ai_generated", "reviewed_by"],
		order_by="term asc",
	)


# ---------------------------------------------------------------------------- the queue


@frappe.whitelist()
def get_glossary_queue(start=0, only_traps=0):
	"""A page of unreviewed entries, plus what is left to do.

	``only_traps`` narrows to the words that mean something *different* in ordinary English. Those
	are the ones somebody gets wrong while feeling perfectly confident, so they are worth being able
	to review first — a reviewer with an hour should spend it there rather than alphabetically.
	"""
	_reviewer()
	start = cint(start)

	filters = dict(PENDING_FILTER)
	if cint(only_traps):
		filters["trade_trap"] = 1

	rows = frappe.get_all(
		DOCTYPE,
		filters=filters,
		fields=[
			"name",
			"term",
			"aliases",
			"category",
			"short_definition",
			"trade_trap",
			"ordinary_meaning",
			"explanation",
			"example",
			"see_also",
			"enabled",
		],
		order_by="term asc",
		start=start,
		page_length=PAGE,
	)

	return {
		"terms": rows,
		"start": start,
		"pending": frappe.db.count(DOCTYPE, PENDING_FILTER),
		"traps_pending": frappe.db.count(DOCTYPE, dict(PENDING_FILTER, trade_trap=1)),
		"reviewed": frappe.db.count(DOCTYPE, {"reviewed_by": ["is", "set"]}),
		"total": frappe.db.count(DOCTYPE),
	}


@frappe.whitelist()
def accept_term(
	term,
	short_definition=None,
	ordinary_meaning=None,
	explanation=None,
	example=None,
):
	"""Put a name to this definition, correcting it on the way through if it needs it.

	The four editable fields are optional: accepting an entry unchanged is the common case and
	should not require echoing it back. ``reviewed_by`` is written from the session — the payload
	has no way to name a reviewer, which is the difference between an attestation and a field.
	"""
	user = _reviewer()
	doc = frappe.get_doc(DOCTYPE, term)

	if short_definition is not None:
		doc.short_definition = short_definition
	if ordinary_meaning is not None:
		doc.ordinary_meaning = ordinary_meaning
	if explanation is not None:
		doc.explanation = explanation
	if example is not None:
		doc.example = example

	if not (doc.short_definition or "").strip():
		# The one field Help shows mid-quiz. An accepted entry that renders as an empty box at the
		# worst possible moment is worse than one nobody has checked.
		frappe.throw(_("A definition is the one thing an entry cannot be accepted without."))

	doc.reviewed_by = user
	doc.save(ignore_permissions=True)
	return {"term": doc.name, "reviewed_by": user, "pending": frappe.db.count(DOCTYPE, PENDING_FILTER)}


@frappe.whitelist()
def disable_term(term, reason):
	"""Take an entry out of use without destroying what it said.

	Disabling rather than deleting, because ``enabled`` is what ``help.py`` filters on and the row
	keeps its history. An entry somebody decided was wrong is worth being able to read later; a
	deleted one takes the reasoning with it.
	"""
	user = _reviewer()
	reason = (reason or "").strip()
	if not reason:
		frappe.throw(_("Say why. A disabled entry with no reason is one nobody can put back."))

	doc = frappe.get_doc(DOCTYPE, term)
	doc.enabled = 0
	doc.reviewed_by = user
	doc.save(ignore_permissions=True)
	doc.add_comment("Comment", _("Disabled during glossary review: {0}").format(reason))
	return {"term": doc.name, "enabled": 0, "pending": frappe.db.count(DOCTYPE, PENDING_FILTER)}


# ---------------------------------------------------------------------------- collisions


def _spellings(row):
	return match_patterns_for(row.get("term"), row.get("aliases"))


@frappe.whitelist()
def get_collisions():
	"""Every spelling that more than one entry claims.

	A lesson using that word matches all of them, so the panel shows two or three entries for one
	word. This is the only place that fact is visible: nothing about a single entry looks wrong.

	The reply deliberately does **not** say which move to make. ``vaults`` claimed by four different
	pieces of equipment wants an alias deleted; ``GFCI`` claimed by three entries for one concept
	wants a merge. Telling them apart is a judgement about the trade.
	"""
	_reviewer()
	rows = _all_terms()

	claims = {}
	for row in rows:
		for form in _spellings(row):
			key = normalised_spelling(form)
			if key:
				claims.setdefault(key, []).append({"term": row["term"], "spelling": form})

	clusters = []
	for key, holders in claims.items():
		names = {h["term"] for h in holders}
		if len(names) > 1:
			clusters.append({"spelling": key, "holders": sorted(names)})

	# Worst first: a spelling four entries claim wastes more of a reader's attention than one two
	# entries claim, and it is also the more likely to be a real mistake.
	clusters.sort(key=lambda c: (-len(c["holders"]), c["spelling"]))
	return {"clusters": clusters, "total": len(clusters)}


@frappe.whitelist()
def drop_alias(term, alias):
	"""Remove one spelling from one entry.

	The lighter of the two moves and the right one for most collisions: four different pieces of
	equipment that all list ``vaults`` are four correct entries and one bad alias, repeated.
	"""
	_reviewer()
	doc = frappe.get_doc(DOCTYPE, term)
	wanted = normalised_spelling(alias)
	kept = [a for a in (doc.aliases or "").splitlines() if a.strip() and normalised_spelling(a) != wanted]
	if len(kept) == len((doc.aliases or "").splitlines()):
		frappe.throw(_("{0} does not list {1} as an alias.").format(term, alias))
	doc.aliases = "\n".join(kept)
	doc.save(ignore_permissions=True)
	return {"term": doc.name, "aliases": doc.aliases}


@frappe.whitelist()
def merge_terms(loser, winner):
	"""Fold one entry into another, and leave nothing pointing at a name that no longer exists.

	The order matters and each step is protecting something different:

	1. **Refuse to delete checked work.** The loser must be ``ai_generated`` and unreviewed. An
	   entry somebody has read and put their name to is not this button's to remove.
	2. **Move every spelling first.** The loser's own name and all of its aliases become aliases of
	   the winner, so no lesson stops matching a word it used to match. A merge that silently
	   narrows what Help can find is worse than two entries.
	3. **Repoint every ``see_also`` that named the loser**, across the whole glossary.
	   ``see_also`` is a plain text field rather than a child table of Links, so the framework will
	   not stop the delete and nothing else would ever notice the name had gone — the reference just
	   quietly resolves to nothing.
	4. **Then delete.**
	"""
	user = _reviewer()
	result = merge_into(loser, winner)
	result["reviewed_by"] = user
	return result


def merge_into(loser, winner):
	"""The merge itself, without the role gate, so a patch can call the same code a button does.

	Split out rather than copied: ``patches/merge_acronym_glossary_terms`` folds the eight
	``X`` / ``X (ABBR)`` pairs the seed created, and a second implementation of "what a merge does"
	is how the patch and the button end up disagreeing about what happened to somebody's aliases.
	"""
	if not loser or not winner or loser == winner:
		frappe.throw(_("Pick two different entries."))

	losing = frappe.get_doc(DOCTYPE, loser)
	keeping = frappe.get_doc(DOCTYPE, winner)

	if not cint(losing.ai_generated) or losing.reviewed_by:
		frappe.throw(
			_("{0} has been written or checked by a person, so it is not a duplicate to discard.").format(
				loser
			)
		)

	moved = _absorb_spellings(keeping, losing)
	_forget_the_loser(keeping, loser)
	repointed = _repoint_see_also(loser, winner)

	keeping.save(ignore_permissions=True)
	keeping.add_comment(
		"Comment",
		_("Merged {0} into this entry during glossary review ({1} spellings kept).").format(
			loser, len(moved)
		),
	)
	frappe.delete_doc(DOCTYPE, loser, ignore_permissions=True)

	return {
		"winner": winner,
		"loser": loser,
		"spellings_moved": moved,
		"see_also_repointed": repointed,
	}


def _absorb_spellings(keeping, losing):
	"""Every way the losing entry could be found becomes a way to find the winner."""
	have = {normalised_spelling(f) for f in _spellings({"term": keeping.term, "aliases": keeping.aliases})}
	moved = []
	for form in [losing.term] + [a for a in (losing.aliases or "").splitlines() if a.strip()]:
		key = normalised_spelling(form)
		if not key or key in have:
			continue
		have.add(key)
		moved.append(form.strip())
	if moved:
		existing = [a for a in (keeping.aliases or "").splitlines() if a.strip()]
		keeping.aliases = "\n".join(existing + moved)
	return moved


def _forget_the_loser(keeping, loser):
	"""Take the losing name out of the WINNER's own See also.

	``_repoint_see_also`` deliberately skips both entries in the merge, because rewriting the
	winner's reference to the loser as a reference to the winner would make it point at itself and
	``_reject_self_reference`` refuses that save. Skipping it entirely is what the first version did
	and it left the winner referencing a row that had just been deleted — a dead link on the very
	entry the merge was supposed to tidy. It is dropped instead: the two are one entry now, so
	there is nothing left to cross-refer to.
	"""
	wanted = normalised_spelling(loser)
	lines = [line.strip() for line in (keeping.see_also or "").splitlines() if line.strip()]
	keeping.see_also = "\n".join(line for line in lines if normalised_spelling(line) != wanted)


def _repoint_see_also(loser, winner):
	"""Every other entry that referenced the loser now references the winner."""
	wanted = normalised_spelling(loser)
	touched = []
	for row in frappe.get_all(DOCTYPE, fields=["name", "see_also"]):
		if row["name"] in (loser, winner) or not row.get("see_also"):
			continue
		lines = [line for line in row["see_also"].splitlines() if line.strip()]
		if not any(normalised_spelling(line) == wanted for line in lines):
			continue
		rebuilt = []
		for line in lines:
			candidate = winner if normalised_spelling(line) == wanted else line.strip()
			if candidate not in rebuilt and normalised_spelling(candidate) != normalised_spelling(row["name"]):
				# Never leave an entry pointing at itself: `_reject_self_reference` refuses the save
				# and the whole merge would fail on an unrelated row.
				rebuilt.append(candidate)
		frappe.db.set_value(DOCTYPE, row["name"], "see_also", "\n".join(rebuilt))
		touched.append(row["name"])
	return touched
