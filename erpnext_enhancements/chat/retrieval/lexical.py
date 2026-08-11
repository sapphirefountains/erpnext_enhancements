# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The BOOLEAN MODE query builder. **Pure — stdlib only.**

InnoDB FULLTEXT in ``IN BOOLEAN MODE`` gives operators meaning: ``+`` requires, ``-``
excludes, ``*`` truncates, ``"`` phrases, ``(`` groups, ``~`` and ``<``/``>`` reweight. A
coworker's question is not written in that language, so passing it through raw produces two
distinct failures and one of them is silent:

* **Syntax errors.** ``Where's the -20% discount?`` contains a leading ``-`` on ``20%``, which
  in boolean mode means *exclude documents containing 20*. The query runs, returns fewer rows,
  and nothing says why. This is the silent one, and it is the common one.
* **Injection into the search expression.** Not SQL injection — the expression is a bound
  parameter — but a user-supplied ``*`` or an unbalanced ``"`` can make MariaDB reject the
  whole statement, which turns a question into a 500.

So every operator character is **stripped rather than escaped**, and each surviving term is
required with an explicit ``+``. Stripping loses the ability to type a deliberate exclusion,
which nobody has asked for; escaping would preserve it and require getting quoting exactly
right on a surface where being wrong is a broken search for the one person who typed an
apostrophe.

--------------------------------------------------------------------------------------
The minimum word length, which is the trap
--------------------------------------------------------------------------------------

InnoDB's default ``innodb_ft_min_token_size`` is **3**, so a two-character term is not indexed
and matches nothing — silently. That is exactly the shape of a lot of real identifiers
(``PO``, ``Q4``), so short terms are dropped from the *required* set and reported by
:func:`dropped_terms`, and a query that reduces to nothing at all returns an empty expression
rather than one that matches everything.

**An empty expression must never become a query that matches every row.** The caller checks
for the empty string; returning ``"*"`` or ``""`` into a ``MATCH ... AGAINST`` would be a
full-corpus read dressed as a search.
"""

from __future__ import annotations

import re

#: InnoDB's default minimum indexed token length. Terms shorter than this match nothing at
#: all, so requiring one makes the whole conjunction empty.
MIN_TOKEN_SIZE: int = 3

#: Everything boolean mode treats as an operator, plus the quoting characters. Stripped, not
#: escaped — see the module docstring.
_OPERATORS = re.compile(r"[+\-><()~*\"@]")

#: Word characters only, which means ``SINV-04412`` becomes **two** required terms rather than
#: one. That is deliberate and it is what makes it work: InnoDB's own tokenizer splits the
#: stored body on non-word characters too, so the index holds ``sinv`` and ``04412``
#: separately. A query preserving the identifier whole would search for a term the index does
#: not contain and match nothing — silently, which is the failure mode this whole module is
#: about. Requiring both parts finds that invoice and not merely chunks about invoices.
_TOKEN = re.compile(r"[\w]+", re.UNICODE)

#: A cap, because a pathological question ("here is our whole meeting transcript, what did we
#: decide") would otherwise build an expression with hundreds of required terms, all of which
#: must match, returning nothing after a full index scan.
MAX_TERMS: int = 24


def tokenize(query: str) -> list[str]:
	"""Lower-cased word tokens, operators removed, order preserved, de-duplicated."""
	cleaned = _OPERATORS.sub(" ", (query or "").lower())
	seen: set[str] = set()
	tokens: list[str] = []
	for match in _TOKEN.finditer(cleaned):
		token = match.group(0)
		if token in seen:
			continue
		seen.add(token)
		tokens.append(token)
	return tokens


def dropped_terms(query: str) -> list[str]:
	"""Terms the index cannot match, so a caller can say so rather than return nothing.

	Worth surfacing: "no results" and "your search terms were all too short for the index"
	are different answers, and only one of them tells the person what to do next.
	"""
	return [token for token in tokenize(query) if len(token) < MIN_TOKEN_SIZE]


def build_boolean_query(query: str) -> str:
	"""The ``AGAINST(... IN BOOLEAN MODE)`` expression, or ``""`` when nothing is searchable.

	Every term is ``+``-required, which makes the search a conjunction. That is the right
	default for this corpus: chat is short and repetitive, so an OR-search over four common
	words returns most of the room. A caller wanting recall should fall back to the semantic
	tier, which is what it is for.

	Returns the empty string when no term survives. **The caller must treat that as "run no
	lexical query"** — never as an expression to pass through, because an empty ``AGAINST``
	is a full scan wearing a search's clothes.
	"""
	terms = [token for token in tokenize(query) if len(token) >= MIN_TOKEN_SIZE][:MAX_TERMS]
	if not terms:
		return ""
	return " ".join(f"+{term}" for term in terms)
