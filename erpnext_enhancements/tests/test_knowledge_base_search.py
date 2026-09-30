# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Knowledge base search (WI-080 PR 5): the ranking, and the rules search_service holds to.

**A pytest suite, on its own CI step** ("Knowledge base search (bench-free pytest suite)"). Plain
pytest functions: ``python -m unittest`` collects none of them, which is how the QuickBooks suite
once ran nowhere for weeks (CLAUDE.md). Locally, run it with plugin autoload off, as CI sees it::

    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest erpnext_enhancements/tests/test_knowledge_base_search.py -q

``knowledge_base/search.py`` is pure, so most of this needs no stub at all:

* **every tokenizer rule**: words of 2 or more characters, acronyms and their plurals, the all-caps
  run, IT against it, stopwords (never an acronym's, never a keyword's), NFKC, the spellings of an
  article number (2026-09-29: ``SOP-06-0001`` by kind and department, one term however written, and
  never the Drive register's ``SOP-0601`` or the retired ``KB`` format), running text shaped like
  one (a product's ``Pro 2 1000``) still found by its words, a document number with its
  parts, and a punctuated acronym (W-2, I-9, T&M, A/R, P.O.) as one term that meets its unpunctuated
  and lowercase spellings;
* **the stemmer's table**;
* **ranking**: title and keywords over the body, an acronym only in keywords, pinning by article number,
  ``allowed``, department and kind applied *before* scoring (so they cannot change another
  document's score or take a slot), the kind's aliases through the meta field, a lowercase acronym,
  deterministic ties;
* **snippets** and the AwesomeBar's highlighting;
* **a golden set** of invented articles and questions (``tests/data/kb_search_golden.json``): each
  expected article in the top 3, and every acronym or article-number question's article first;
* **a performance guard**: 500 articles of 800 words build in under 5 seconds, 100 queries in under 1;
* **a fresh interpreter** importing ``search`` with ``frappe`` absent.

``search_service.py`` needs frappe, and ``test_knowledge_base_actions.SearchServiceTest`` runs it over
a whole in-memory site. Here it runs over the golden corpus through a minimal fake (installed with
``monkeypatch`` for one test at a time, never left in ``sys.modules``), to pin what the corpus is,
in what order the caller's permissions are asked, and that the ranking is handed the caller's own
readable set (a reader who may not read some articles, so a hidden article that would rank first
takes no slot); plus **static checks** that neither search module names the Version doctype or
passes a SQL function string as a field.
"""

import ast
import datetime
import importlib
import importlib.util
import json
import random
import re
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
REPO_ROOT = APP.parent
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.knowledge_base import constants as K
from erpnext_enhancements.knowledge_base import search as S

GOLDEN = json.loads((APP / "tests" / "data" / "kb_search_golden.json").read_text(encoding="utf-8"))
SEARCH_SOURCES = (APP / "knowledge_base" / "search.py", APP / "knowledge_base" / "search_service.py")
SERVICE = "erpnext_enhancements.knowledge_base.search_service"


def doc(key, title="", keywords="", summary="", body="", kind=None, department="06 Operations"):
	return S.Document(key, key, title, keywords, summary, body, kind, department)


def terms(text, **kwargs):
	return [t.term for t in S.tokenize(text, **kwargs)]


def keys(hits):
	return [hit.key for hit in hits]


def golden_documents(articles=None):
	return [
		S.Document(a["key"], a["key"], a["title"], a["keywords"], a["summary"], a["body"], a["kind"], a["department"])
		for a in (articles if articles is not None else GOLDEN["articles"])
	]


@pytest.fixture(scope="module")
def golden_index():
	return S.build_index(golden_documents())


# ------------------------------------------------------------------ tokens


def test_words_of_two_or_more_characters_are_kept():
	assert terms("A PO or 2 go") == ["po", "go"]
	assert terms("x y z") == []


@pytest.mark.parametrize("word", ["PO", "QBO", "SOP", "AIA", "SOV", "PTO", "W2", "2FA", "OSHA"])
def test_a_word_in_capitals_is_an_acronym(word):
	assert S.tokenize(f"Check the {word} first") == [
		S.Token("check", False),
		S.Token(word.casefold(), True),
		S.Token("first", False),
	]


def test_an_acronym_plural_is_the_same_acronym():
	assert S.tokenize("Match the POs and QBOs") == [
		S.Token("match", False),
		S.Token("po", True),
		S.Token("qbo", True),
	]


def test_an_acronym_is_never_stemmed():
	"""In mixed text a capitalized word of up to six characters is an acronym and keeps its spelling."""
	assert S.tokenize("Check the SLIPS") == [S.Token("check", False), S.Token("slips", True)]
	assert terms("Check the slips") == ["check", "slip"]


def test_in_an_all_caps_run_only_short_tokens_are_acronyms():
	"""An all-caps heading is not six acronyms: its longer words are ordinary, stemmed words."""
	assert S.tokenize("RECEIVING PACKING SLIPS FOR THE PO") == [
		S.Token("receiv", False),
		S.Token("pack", False),
		S.Token("slip", False),
		S.Token("for", True),
		S.Token("the", True),
		S.Token("po", True),
	]


def test_a_run_is_a_line_or_a_sentence():
	assert terms("RECEIVING SLIPS. Check the SLIPS") == ["receiv", "slip", "check", "slips"]
	assert terms("Check the SLIPS\nRECEIVING SLIPS") == ["check", "slips", "receiv", "slip"]


def test_digits_alone_are_not_an_acronym():
	assert S.tokenize("In 2026 the rate") == [S.Token("2026", False), S.Token("rate", False)]


def test_it_is_an_acronym_and_it_is_a_stopword():
	assert S.tokenize("Put it on the shelf") == [S.Token("put", False), S.Token("shelf", False)]
	assert S.tokenize("Call IT") == [S.Token("call", False), S.Token("it", True)]


def test_stopwords_are_dropped_except_in_keywords():
	assert terms("how to receive the order") == ["receiv", "order"]
	assert terms("how to receive the order", keywords=True) == ["how", "to", "receiv", "the", "order"]
	assert "it" in S.STOPWORDS and "po" not in S.STOPWORDS
	assert 60 <= len(S.STOPWORDS) <= 110


def test_nfkc_reads_a_full_width_or_ligature_spelling_as_the_plain_one():
	assert S.tokenize("ＰＯ") == [S.Token("po", True)]
	assert terms("ﬁnance") == terms("finance")


@pytest.mark.parametrize(
	("spelling", "parts"),
	[
		("SOP-06-0001", ["sop", "06", "0001"]),
		("sop 06 0001", ["sop", "06", "0001"]),
		("SOP-06-1", ["sop", "06"]),
		("sop_6_1", ["sop"]),
		("SOP06-0001", ["sop", "06", "0001"]),
		("SOP\u201306\u20130001", ["sop", "06", "0001"]),
		("SOP-06-00001", ["sop", "06", "00001"]),
		("\uff33\uff2f\uff30-06-0001", ["sop", "06", "0001"]),
	],
)
def test_the_spellings_of_an_article_number(spelling, parts):
	"""2026-09-29: one term, the canonical number casefolded, however it is written. In an article's
	text its prefix and its digit runs of 2 or more characters, as written, follow it (review of
	v1.568.0); a query holds the one term alone."""
	tokens = S.tokenize(f"See {spelling} first")
	assert tokens[:2] == [S.Token("see", False), S.Token("sop-06-0001", True)]
	assert [t.term for t in tokens[2:]] == [*parts, "first"]
	assert S.query_terms(f"See {spelling} first") == {"see", "sop-06-0001", "first"}
	assert S.normalize_article_number(spelling) == "SOP-06-0001"
	assert S.normalize_article_number is K.normalize_article_number


def test_running_text_shaped_like_a_number_keeps_its_words():
	"""Review of v1.568.0: a product called "Pro 2 1000" has an article number's shape (``PRO-02-1000``
	in the loose form a query may use). Before numbers were read, its words were found by ``1000``,
	``pro 1000`` and ``Pro 2``; they still are, because an article's text indexes a number's prefix
	and digit runs beside its term. And fetch's ``related`` never lists it
	(``constants.cited_article_numbers``, pinned in ``test_knowledge_base_rules``)."""
	assert terms("Use the Pro 2 1000 pump kit") == ["use", "pro-02-1000", "pro", "1000", "pump", "kit"]
	assert terms("pro 5 10 times") == ["pro-05-0010", "pro", "10", "time"]
	body = "Before startup, use the Pro 2 1000 pump kit."
	index = S.build_index(
		[
			doc("SOP-06-0001", title="Starting the fountain", body=body, kind="SOP"),
			doc("SOP-06-0002", title="Draining the basin", kind="SOP"),
		]
	)
	for query in ("1000", "pro 1000", "Pro 2", "Pro 2 1000 pump", "pump 1000"):
		hits = S.search(index, query)
		assert keys(hits) == ["SOP-06-0001"], query
		assert hits[0].matched == ("body",) and not hits[0].pinned, query
	assert "Pro 2 1000 pump" in S.snippet(body, "1000")
	# A query naming a number means that article, so its parts are no query terms: an unknown number
	# finds nothing, although both articles here are SOPs in 06 (the meta field's "sop" and "06").
	assert S.query_terms("SOP-06-0099") == {"sop-06-0099"}
	assert S.search(index, "SOP-06-0099") == []


def test_what_is_not_an_article_number():
	for text in (
		"KBV-00001",
		"SOP-06-12345",
		"SOP",
		"SOP-06",
		"0601",
		"",
		None,
		"SOP-06-0001 and more",
		"KB-0601",
		"SOP-0601",
	):
		assert S.normalize_article_number(text) is None, text
	assert "sop-00-0001" not in terms("KBV-00001")
	# The Drive register's own numbers are document numbers (their parts indexed too), never articles;
	# the retired KB format likewise.
	assert S.tokenize("See POL-0600 and SOP-06-0001") == [
		S.Token("see", False),
		S.Token("pol-0600", True),
		S.Token("pol", True),
		S.Token("0600", False),
		S.Token("sop-06-0001", True),
		S.Token("sop", True),
		S.Token("06", False),
		S.Token("0001", False),
	]
	assert terms("KB-0601") == ["kb-0601", "kb", "0601"]
	# Shaped like one but not a number: the words it is made of.
	assert terms("SOP-06-0000") == ["sop", "06", "0000"]
	# A department that is not a block is no article number either; SOP-12 is a document number.
	assert "sop-12-0001" not in terms("SOP-12-0001")


def test_a_document_number_is_one_term_and_its_parts():
	assert S.tokenize("Follow SOP-9001") == [
		S.Token("follow", False),
		S.Token("sop-9001", True),
		S.Token("sop", True),
		S.Token("9001", False),
	]
	assert terms("po-1234") == ["po-1234", "po", "1234"]


@pytest.mark.parametrize(
	("text", "expected"),
	[
		("W-2", ["w2"]),
		("I-9", ["i9"]),
		("W-9 from the vendor", ["w9", "vendor"]),
		("G-702", ["g702", "702"]),
		("T&M billing", ["tm", "bill"]),
		("O&M manual", ["om", "manual"]),
		("P&L review", ["pl", "review"]),
		("A/R aging", ["ar", "aging"]),
		("A/P", ["ap"]),
		("P.O. number", ["po", "number"]),
		("W-2s to collect", ["w2", "collect"]),
		("1099-K", ["1099k", "1099"]),
		# Typed in lowercase, in a query, it is the same term.
		("w-2", ["w2"]),
		("t&m", ["tm"]),
		("a/r", ["ar"]),
	],
)
def test_a_punctuated_acronym_is_one_term(text, expected):
	"""Found in review: single letters and digits joined by ``-``, ``&``, ``/`` or ``.`` were dropped
	piece by piece (one-character tokens), so W-2, I-9, T&M and A/R were never indexed or searched."""
	assert terms(text) == expected
	joined = [t for t in S.tokenize(text) if t.term == expected[0]]
	assert joined and joined[0].acronym, "never stemmed and never a stopword"


def test_what_is_not_a_punctuated_acronym():
	"""No letter, no join (``3-4``, a date, a decimal); and a word longer than one letter is not a
	piece, so ``x-ray`` and ``e-mail`` keep their words and drop the lone letter, as before."""
	assert terms("3-4") == []
	assert terms("9/28/2026") == ["28", "2026"]
	assert terms("1.5") == []
	assert terms("x-ray") == ["ray"]
	assert terms("e-mail") == ["mail"]
	assert terms("Plan B-style") == ["plan", "styl"]
	assert terms("PO/SO") == ["po", "so"]
	# A document number is still a document number.
	assert terms("SOP-9001") == ["sop-9001", "sop", "9001"]


def test_a_long_letterless_chain_is_read_in_linear_time():
	"""A chain with no letter is matched once and read as its words. A regex lookahead for its
	letter, retried at every piece, made ``1-1-1-…`` quadratic (5,000 pieces took two seconds while
	this was built), and the AwesomeBar tokenizes whatever any signed-in user types."""
	started = time.perf_counter()
	assert S.tokenize("1-" * 20000 + " PO") == [S.Token("po", True)]
	assert time.perf_counter() - started < 1.0


def test_every_spelling_of_a_punctuated_acronym_meets():
	index = S.build_index(
		[
			doc("SOP-06-0001", title="Billing a T&M service call"),
			doc("SOP-06-0002", title="Collecting a vendor W-9"),
			doc("SOP-06-0003", title="Reviewing A/R aging"),
			doc("SOP-06-0004", title="Onboarding", keywords="I-9, paperwork"),
			doc("SOP-06-0005", title="Correcting a W2"),
		]
	)
	for queries, key in (
		(("T&M", "t&m", "TM", "tm"), "SOP-06-0001"),
		(("W-9", "W9", "w9", "w-9"), "SOP-06-0002"),
		(("A/R", "AR", "ar"), "SOP-06-0003"),
		(("I-9", "I9", "i9"), "SOP-06-0004"),
		(("W-2", "W2", "w2", "w-2"), "SOP-06-0005"),
	):
		for query in queries:
			assert keys(S.search(index, query)) == [key], query
	assert S.search(index, "3-4") == []
	marked = S.mark("Billing a T&M call for a W-2", "t&m w2")
	assert marked == "Billing a <b>T&amp;M</b> call for a <b>W-2</b>"


# ------------------------------------------------------------------ the stemmer


@pytest.mark.parametrize(
	("words", "stem"),
	[
		(("receive", "receives", "received", "receiving"), "receiv"),
		(("shipping", "shipped"), "ship"),
		(("packing", "packed", "packs"), "pack"),
		(("invoices", "invoiced"), "invoic"),
		(("policies",), "policy"),
		(("procedure", "procedures"), "procedur"),
		(("status",), "status"),
		(("process", "processes", "processing"), "process"),
	],
)
def test_the_stemmer_table(words, stem):
	for word in words:
		assert S.stem(word) == stem, word


def test_the_stemmer_leaves_short_and_non_alphabetic_words_alone():
	for word in ("pos", "use", "w2", "2026", "kb-0601"):
		assert S.stem(word) == word


# ------------------------------------------------------------------ ranking


def test_title_and_keywords_outrank_the_body():
	body_only = doc("SOP-06-0001", title="Monthly checks", body="Count the cartons on the dock before noon.")
	in_title = doc("SOP-06-0002", title="Counting cartons", body="Do it before noon.")
	in_keywords = doc("SOP-06-0003", title="Monthly checks", keywords="cartons", body="Before noon.")
	index = S.build_index([body_only, in_title, in_keywords])
	hits = S.search(index, "cartons")
	assert keys(hits)[-1] == "SOP-06-0001"
	assert set(keys(hits)[:2]) == {"SOP-06-0002", "SOP-06-0003"}
	assert dict((h.key, h.matched) for h in hits) == {
		"SOP-06-0001": ("body",),
		"SOP-06-0002": ("title",),
		"SOP-06-0003": ("keywords",),
	}


def test_an_acronym_found_only_in_keywords():
	index = S.build_index(
		[doc("SOP-06-0001", title="Closing the books", keywords="QBO, month end"), doc("SOP-06-0002", title="Other")]
	)
	(hit,) = S.search(index, "QBO")
	assert (hit.key, hit.matched, hit.pinned) == ("SOP-06-0001", ("keywords",), False)
	assert keys(S.search(index, "qbo")) == ["SOP-06-0001"]


def test_it_versus_it():
	index = S.build_index(
		[
			doc("SOP-06-0001", title="Printer problems", body="Call IT when the printer fails."),
			doc("SOP-06-0002", title="Shelving", body="Put it on the shelf and leave it there."),
		]
	)
	assert keys(S.search(index, "IT")) == ["SOP-06-0001"]
	assert S.search(index, "it") == []


def test_a_lowercase_acronym_meets_the_capitals():
	index = S.build_index([doc("SOP-06-0001", body="Read the MSDS first."), doc("SOP-06-0002", body="Pay at the POS.")])
	assert keys(S.search(index, "msds")) == ["SOP-06-0001"]
	assert keys(S.search(index, "pos")) == ["SOP-06-0002"]


def test_an_article_number_pins_its_article_first_in_query_order():
	index = S.build_index(
		[
			doc("SOP-06-0001", title="Receiving receiving receiving", keywords="receiving"),
			doc("SOP-06-0002", title="Something else"),
			doc("SOP-06-0003", title="A third", body="See SOP-06-0002 for receiving."),
		]
	)
	hits = S.search(index, "receiving SOP-06-0002 sop 6 3")
	assert keys(hits)[:2] == ["SOP-06-0002", "SOP-06-0003"]
	assert hits[0].pinned and hits[1].pinned and not hits[2].pinned
	assert hits[0].matched[0] == "kb_number"
	assert keys(hits)[2] == "SOP-06-0001"
	# Every written form pins the same article.
	for query in ("SOP-06-0002", "sop 06 0002", "sop_6_2", "SOP06-0002", "SOP\u201306\u20130002"):
		assert keys(S.search(index, query))[0] == "SOP-06-0002", query
	# Only a number that is in the index pins; an unknown one is just a term.
	assert S.search(index, "SOP-06-0099") == []
	# A kind and a department alone pin nothing.
	assert not any(hit.pinned for hit in S.search(index, "SOP-06"))


def test_filters_apply_before_scoring():
	"""A document outside ``allowed``, the department or the kind is never ranked, pinned or counted:
	it takes no slot, and every other document scores exactly as it would without the filter (idf
	is the whole corpus's)."""
	docs = [
		doc("SOP-06-0001", title="PO PO PO", kind="SOP"),
		doc("SOP-06-0002", title="Receiving a PO", kind="Policy"),
		doc("SOP-03-0001", title="Paying a PO", kind="SOP", department="03 Finance"),
	]
	index = S.build_index(docs)
	unfiltered = {h.key: h.score for h in S.search(index, "PO")}
	assert keys(S.search(index, "PO", limit=1)) == ["SOP-06-0001"]
	assert keys(S.search(index, "PO", allowed={"SOP-06-0002", "SOP-03-0001"}, limit=1)) in (["SOP-06-0002"], ["SOP-03-0001"])
	filtered = S.search(index, "PO", allowed={"SOP-06-0002", "SOP-03-0001"})
	assert "SOP-06-0001" not in keys(filtered)
	assert {h.key: h.score for h in filtered} == {k: v for k, v in unfiltered.items() if k != "SOP-06-0001"}
	assert keys(S.search(index, "PO SOP-06-0001", allowed={"SOP-06-0002"})) == ["SOP-06-0002"]
	assert keys(S.search(index, "PO", department="03 Finance")) == ["SOP-03-0001"]
	assert keys(S.search(index, "PO", kind="Policy")) == ["SOP-06-0002"]
	assert S.search(index, "PO", kind="Process") == []
	assert S.search(index, "PO", allowed=set()) == []


@pytest.mark.parametrize(
	("query", "kind"), [("procedure", "SOP"), ("how-to", "SOP"), ("workflow", "Process"), ("rules", "Policy")]
)
def test_a_kinds_aliases_reach_its_articles_through_the_meta_field(query, kind):
	index = S.build_index(
		[
			doc("SOP-06-0001", title="Counting a bin", kind="SOP"),
			doc("SOP-06-0002", title="From order to delivery", kind="Process"),
			doc("SOP-06-0003", title="Safety on the floor", kind="Policy"),
			doc("SOP-06-0004", title="Unclassified", kind=None),
		]
	)
	if query == "how-to":
		# "how" and "to" are stopwords in a query; the alias is reached as one word.
		query = "howto"
	hits = S.search(index, query)
	assert [index.kinds[index.keys.index(h.key)] for h in hits] == [kind]
	assert hits[0].matched == ("meta",)


def test_the_meta_field_weighs_less_than_the_text():
	index = S.build_index(
		[doc("SOP-06-0001", title="Counting a bin", kind="SOP"), doc("SOP-06-0002", title="The procedure for payroll", kind=None)]
	)
	assert keys(S.search(index, "procedure")) == ["SOP-06-0002", "SOP-06-0001"]


def test_the_department_is_searchable_too():
	index = S.build_index([doc("SOP-06-0001", title="A", department="04 HR"), doc("SOP-06-0002", title="B")])
	assert keys(S.search(index, "HR")) == ["SOP-06-0001"]


def test_ties_are_deterministic():
	docs = [doc(f"SOP-06-{n:04d}", title="Receiving a PO") for n in (7, 3, 5, 1)]
	first = keys(S.search(S.build_index(docs), "PO"))
	second = keys(S.search(S.build_index(list(reversed(docs))), "PO"))
	assert first == second == ["SOP-06-0001", "SOP-06-0003", "SOP-06-0005", "SOP-06-0007"]


def test_nothing_to_search_for():
	index = S.build_index([doc("SOP-06-0001", title="Receiving")])
	for query in ("", "   ", "the and of", "a", None):
		assert S.search(index, query) == []
	assert S.search(S.build_index([]), "PO") == []


def test_limit():
	index = S.build_index([doc(f"SOP-06-{n:04d}", title="PO") for n in range(1, 13)])
	assert len(S.search(index, "PO")) == 10
	assert len(S.search(index, "PO", limit=3)) == 3
	assert len(S.search(index, "PO", limit=None)) == 12


def test_the_index_keeps_no_text():
	"""Terms and numbers only: a worker holding the index holds no article's text (search_service
	reads ``body_md`` to build it and lets it go)."""
	body = "Count the cartons, then sign the sheet."
	index = S.build_index([doc("SOP-06-0001", title="Receiving goods", summary="A summary line.", body=body)])
	assert not hasattr(index, "__dict__")
	assert set(S.Index.__slots__) == {"by_kb", "departments", "idf", "kb_numbers", "keys", "kinds", "postings"}
	held = repr({slot: getattr(index, slot) for slot in S.Index.__slots__})
	for text in (body, "Count the cartons", "Receiving goods", "A summary line"):
		assert text not in held


# ------------------------------------------------------------------ snippets and highlighting


def test_the_snippet_is_the_window_with_the_most_query_terms():
	text = (
		"The PO number is on the slip. "
		+ "Filler words about unrelated matters go here. " * 12
		+ "Count each carton, check each carton against the PO, and sign the carton list."
	)
	piece = S.snippet(text, "carton PO", width=120)
	assert "check each carton against the PO" in piece
	assert piece.startswith("…")
	assert len(piece) <= 120 + 2


def test_the_snippet_is_cut_at_word_boundaries_with_no_marks():
	text = "alpha " * 30 + "receiving the goods " + "omega " * 60
	piece = S.snippet(text, "receiving", width=80)
	inner = piece.strip("…")
	assert inner.split()[0] in ("alpha", "receiving")
	assert inner.split()[-1] in ("omega", "goods")
	assert all(word in ("alpha", "receiving", "the", "goods", "omega") for word in inner.split())
	assert "<" not in piece and "*" not in piece


def test_the_snippet_reads_markdown_as_text():
	text = "## Step 1\n\n**Open** the [PO](/app/purchase-order/PO-0001) ![slip](/private/files/slip.png)"
	piece = S.snippet(text, "PO")
	assert piece == "Step 1 Open the PO slip"


def test_the_snippet_falls_back_to_the_summary():
	assert S.snippet("Nothing relevant here.", "carton", fallback="How to count cartons.") == "How to count cartons."
	long_summary = "word " * 100
	piece = S.snippet("", "carton", width=40, fallback=long_summary)
	assert piece.endswith("…") and len(piece) <= 41
	assert S.snippet("", "carton") == ""


def test_the_snippet_window_survives_nfkc():
	"""The scan's positions are in the NFKC text; a ligature doubles in length there, so the window
	must be cut from the same normalized text or it misses the match."""
	text = "ﬁ " * 80 + "then check the PO number carefully"
	piece = S.snippet(text, "PO", width=40)
	assert "PO" in piece
	assert "ﬁ" not in piece


def test_a_short_text_is_shown_whole():
	assert S.snippet("Check the PO.", "PO") == "Check the PO."


def test_mark_escapes_everything_and_bolds_only_matches():
	assert S.mark("SOP-06-0001 · Receiving a PO <script>", "po sop 6 1") == (
		"<b>SOP-06-0001</b> · Receiving a <b>PO</b> &lt;script&gt;"
	)
	assert S.mark("a & b", "") == "a &amp; b"


def test_plain_text():
	text = "> quote\n- item one\n1. step\n| a | b |\n|---|---|\n`code` and __bold__ and snake_case http://x.example/y"
	assert S.plain_text(text) == "quote\nitem one\nstep\na b\ncode and bold and snake_case"


# ------------------------------------------------------------------ the golden set


def test_the_golden_set_is_invented_and_complete():
	articles = GOLDEN["articles"]
	assert 25 <= len(articles) <= 40
	assert {a["kind"] for a in articles} == set(K.ARTICLE_KINDS)
	assert all(a["department"] in K.DEPARTMENT_BLOCK_OPTIONS for a in articles)
	# Invented numbers, outside any real register: canonical, numbered by the article's own kind and
	# department, with sequences from 9001; and none is a document number any body cites.
	for a in articles:
		parsed = K.parse_article_number(a["key"])
		assert parsed == (a["kind"], a["department"][:2], parsed[2]) and parsed[2] >= 9001, a["key"]
	bodies = " ".join(a["body"] for a in articles)
	documents = set(re.findall(r"\b[A-Za-z]{2,5}-[0-9]{2,6}\b", bodies))
	assert documents and not documents & {a["key"] for a in articles}
	# An article number a body cites is one of the corpus's (the AIA article cites the SOV one).
	assert set(K.written_article_numbers(bodies)) <= {a["key"] for a in articles}
	assert len({a["key"] for a in articles}) == len(articles)
	assert len(GOLDEN["queries"]) >= 40
	assert {q["expect"] for q in GOLDEN["queries"]} <= {a["key"] for a in articles}


@pytest.mark.parametrize("case", GOLDEN["queries"], ids=[q["query"] for q in GOLDEN["queries"]])
def test_the_golden_set(golden_index, case):
	found = keys(S.search(golden_index, case["query"], limit=10))
	assert case["expect"] in found[:3], found
	if case.get("first"):
		assert found[0] == case["expect"], found


def test_every_acronym_question_ranks_its_article_first():
	"""The rule the WI-080 acceptance keeps for the real questions: a question that names an acronym
	or a KB number must put its article first, not merely in the top 3."""
	for case in GOLDEN["queries"]:
		query = case["query"]
		has_acronym = any(t.acronym for t in S.tokenize(query))
		if has_acronym and len(query.split()) <= 3:
			assert case.get("first"), f"{query!r} names an acronym, so it must be marked first"


# ------------------------------------------------------------------ cost


def test_the_performance_guard():
	"""Loose on purpose (CI runners vary): 500 articles of 800 words build in under 5 seconds, and
	100 queries run in under 1."""
	rng = random.Random(80)
	vocab = ["".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(3, 10))) for _ in range(6000)]
	acronyms = ["PO", "QBO", "SOP", "AIA", "SOV", "PTO", "W2"]

	def words(n):
		out = []
		for i in range(n):
			out.append(rng.choice(acronyms) if rng.random() < 0.03 else rng.choice(vocab))
			if i % 15 == 14:
				out.append(".\n")
		return " ".join(out)

	docs = [
		doc(
			f"SOP-{(n % 10):02d}-{(n // 10) + 1:04d}",
			title=words(8),
			keywords=", ".join(rng.sample(acronyms, 2)),
			summary=words(30),
			body=words(800),
			kind=rng.choice((*K.ARTICLE_KINDS, None)),
		)
		for n in range(500)
	]
	S.stem.cache_clear()
	started = time.perf_counter()
	index = S.build_index(docs)
	assert time.perf_counter() - started < 5.0
	assert len(index) == 500
	queries = [" ".join(rng.sample(vocab, 3)) + " " + rng.choice(acronyms) for _ in range(100)]
	allowed = {d.key for d in docs[:400]}
	started = time.perf_counter()
	for query in queries:
		S.search(index, query, allowed=allowed)
	assert time.perf_counter() - started < 1.0


# ------------------------------------------------------------------ purity and the static rules


def test_search_imports_with_frappe_absent():
	"""A fresh interpreter, with ``frappe`` set to ``None`` so any import of it raises."""
	code = (
		"import sys\n"
		"sys.modules['frappe'] = None\n"
		"import erpnext_enhancements.knowledge_base.search as s\n"
		"assert s.search(s.build_index([s.Document('SOP-06-0001', 'SOP-06-0001', 'PO', '', '', '', 'SOP', '06 Operations')]), 'PO')\n"
		"print('ok')\n"
	)
	result = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True)
	assert result.returncode == 0, result.stderr
	assert result.stdout.strip() == "ok"


def _code_strings_and_names(path):
	"""Every string constant that is not a docstring, and every name and attribute, in ``path``: the
	source with its comments and docstrings stripped, as far as a check of it needs."""
	tree = ast.parse(path.read_text(encoding="utf-8"))
	docstrings = set()
	for node in ast.walk(tree):
		if isinstance(node, ast.Module | ast.FunctionDef | ast.ClassDef | ast.AsyncFunctionDef):
			body = node.body
			if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
				docstrings.add(id(body[0].value))
	strings, names = [], set()
	for node in ast.walk(tree):
		if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
			strings.append(node.value)
		elif isinstance(node, ast.Name):
			names.add(node.id)
		elif isinstance(node, ast.Attribute):
			names.add(node.attr)
	return strings, names


@pytest.mark.parametrize("path", SEARCH_SOURCES, ids=lambda p: p.name)
def test_search_never_names_the_version_doctype(path):
	strings, names = _code_strings_and_names(path)
	for text in strings:
		assert "Knowledge Article Version" not in text
		assert "tabKnowledge Article Version" not in text
	assert "VERSION_DOCTYPE" not in names
	assert "VERSION" not in names


def test_no_sql_function_string_is_passed_as_a_field():
	"""v16 refuses ``fields=["count(name) as n"]`` in ``get_all``/``get_list`` (CLAUDE.md), and a
	stub accepts it, so it is checked here, statically."""
	service = importlib.util.find_spec(SERVICE).origin
	tree = ast.parse(Path(service).read_text(encoding="utf-8"))
	constants = {}
	for node in tree.body:
		if isinstance(node, ast.Assign) and isinstance(node.value, ast.Tuple):
			constants[node.targets[0].id] = [e.value for e in node.value.elts]
	for name in ("CORPUS_FIELDS", "DISPLAY_FIELDS"):
		assert constants[name], name
		assert all("(" not in field and " " not in field for field in constants[name]), name
	calls = [
		node
		for node in ast.walk(tree)
		if isinstance(node, ast.Call)
		and isinstance(node.func, ast.Attribute)
		and node.func.attr in ("get_all", "get_list", "get_value", "get_values")
	]
	assert len(calls) == 3
	for call in calls:
		for keyword in call.keywords:
			if keyword.arg == "fields" and isinstance(keyword.value, ast.List):
				for element in keyword.value.elts:
					assert not (isinstance(element, ast.Constant) and "(" in str(element.value))


def test_every_list_call_reads_published_articles_only():
	service = Path(importlib.util.find_spec(SERVICE).origin).read_text(encoding="utf-8")
	tree = ast.parse(service)
	for node in ast.walk(tree):
		if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("get_all", "get_list"):
			assert isinstance(node.args[0], ast.Name) and node.args[0].id == "ARTICLE"
			(filters,) = [k.value for k in node.keywords if k.arg == "filters"]
			assert "status" in [k.value for k in filters.keys]


# ------------------------------------------------------------------ the service over the golden corpus

ARTICLE = K.ARTICLE_DOCTYPE
READER = "reader@example.com"
PORTAL = "customer@example.com"


class _Row(dict):
	def __getattr__(self, key):
		return self.get(key)


@pytest.fixture
def service(monkeypatch):
	"""``search_service`` imported over a minimal fake frappe holding the golden articles as Published
	rows, one Retired article, and the golden drafts in a table nothing may read."""
	calls = []
	rows = [
		_Row(
			name=a["key"],
			kb_number=a["key"],
			title=a["title"],
			keywords=a["keywords"],
			summary=a["summary"],
			body_md=a["body"],
			kind=a["kind"],
			department_block=a["department"],
			status="Published",
			version_number=1,
			approved_by="approver@example.com",
			approved_on=datetime.datetime(2026, 9, 1, 10, 0),
			review_by=datetime.date(2027, 3, 1),
			ai_drafted=0,
			modified=datetime.datetime(2026, 9, 1, 10, 0),
		)
		for a in GOLDEN["articles"]
	]
	rows.append(_Row(name="SOP-06-9099", kb_number="SOP-06-9099", title="RETIREDWORD", status="Retired", body_md="RETIREDWORD"))
	session = types.SimpleNamespace(user=READER)
	#: Published names the reader's get_list leaves out, as a User Permission would: a partial
	#: readable set, not only all or nothing.
	hidden = set()

	def readable():
		return session.user != PORTAL

	def match(row, filters):
		for key, want in (filters or {}).items():
			if isinstance(want, list) and want[0] == "in":
				if row.get(key) not in want[1]:
					return False
			elif row.get(key) != want:
				return False
		return True

	def select(filters, fields, pluck, leave_out=()):
		out = [r for r in rows if match(r, filters) and r["name"] not in leave_out]
		return [r.get(pluck) for r in out] if pluck else [_Row({f: r.get(f) for f in fields}) for r in out]

	def get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
		calls.append(("get_all", doctype))
		assert doctype == ARTICLE, f"search read {doctype}"
		return select(filters, fields, pluck)

	def get_list(doctype, filters=None, fields=None, pluck=None, **kwargs):
		calls.append(("get_list", doctype))
		assert doctype == ARTICLE, f"search read {doctype}"
		if not readable():
			frappe.local.message_log.append("Insufficient Permission")
			raise PermissionError("Insufficient Permission")
		return select(filters, fields, pluck, hidden)

	def has_permission(doctype, ptype="read", throw=False, **kwargs):
		calls.append(("has_permission", doctype))
		return readable()

	def sql(query, *args, **kwargs):
		calls.append(("sql", query))
		assert query == "select count(*), max(modified) from `tabKnowledge Article`", query
		return ((len(rows), max(r.get("modified") or datetime.datetime.min for r in rows)),)

	frappe = types.ModuleType("frappe")
	frappe.session = session
	frappe.local = types.SimpleNamespace(site="golden.example.com", message_log=[])
	frappe.get_all = get_all
	frappe.get_list = get_list
	frappe.has_permission = has_permission
	frappe.db = types.SimpleNamespace(sql=sql)
	frappe.clear_messages = lambda: setattr(frappe.local, "message_log", [])
	utils = types.ModuleType("frappe.utils")
	utils.get_fullname = lambda user: "Alex Example"
	utils.get_url = lambda uri="": "https://kb.example.com" + uri
	utils.getdate = lambda value=None: (value.date() if isinstance(value, datetime.datetime) else value) or datetime.date(2026, 9, 28)
	utils.nowdate = lambda: datetime.date(2026, 9, 28)
	frappe.utils = utils
	monkeypatch.setitem(sys.modules, "frappe", frappe)
	monkeypatch.setitem(sys.modules, "frappe.utils", utils)
	monkeypatch.delitem(sys.modules, SERVICE, raising=False)
	module = importlib.import_module(SERVICE)
	yield types.SimpleNamespace(module=module, calls=calls, session=session, frappe=frappe, hidden=hidden)
	sys.modules.pop(SERVICE, None)


def test_the_service_answers_the_golden_set_like_the_engine(service, golden_index):
	for case in GOLDEN["queries"]:
		out = service.module.search(case["query"], limit=10)
		found = [r["kb_number"] for r in out["results"]]
		assert found == keys(S.search(golden_index, case["query"], limit=10)), case["query"]


def test_no_draft_or_retired_text_is_ever_found(service):
	draft_words = {"QUOKKADRAFT", *(d["key"] for d in GOLDEN["drafts"])}
	queries = [q["query"] for q in GOLDEN["queries"]] + ["QUOKKADRAFT", "quokkadraft", "RETIREDWORD", "SOP-06-9099"]
	for query in queries:
		dumped = json.dumps(service.module.search(query)) + json.dumps(service.module.awesomebar_hits(query))
		for word in (*draft_words, "RETIREDWORD", "SOP-06-9099"):
			assert word not in dumped, (query, word)
	assert {doctype for kind, doctype in service.calls if kind != "sql"} == {ARTICLE}


def test_permission_is_asked_before_any_list_and_the_readable_set_before_ranking(service):
	service.module.search("PO")
	order = [kind for kind, _doctype in service.calls]
	assert order[0] == "has_permission"
	assert order[1] == "get_list"  # the caller's readable set
	assert order.index("sql") < order.index("get_all")  # the stamp before the corpus
	assert order[-1] == "get_list"  # the rows shown, read as the caller again


def test_the_ranking_is_handed_the_readable_set_and_a_hidden_article_takes_no_slot(
	service, golden_index, monkeypatch
):
	"""A reader whose get_list leaves out the five best answers: the ranking is handed exactly the
	published articles the reader's own list returned, and the AwesomeBar's five slots and a
	one-result search go to what the reader may read. Were the readable set applied only when the
	rows are read for display, the hidden five would fill every slot and then be dropped, and the
	reader would get nothing."""
	query = "policy"
	everyone = keys(S.search(golden_index, query, limit=None))
	limit = service.module.AWESOMEBAR_LIMIT
	assert len(everyone) > limit + 1, everyone
	service.hidden.update(everyone[:limit])
	published = {a["key"] for a in GOLDEN["articles"]}
	readable = published - service.hidden
	expected = keys(S.search(golden_index, query, allowed=readable, limit=limit))
	assert expected == everyone[limit : 2 * limit]

	handed = []
	real = service.module.engine.search

	def spying(index, text, **kwargs):
		handed.append(kwargs.get("allowed"))
		return real(index, text, **kwargs)

	monkeypatch.setattr(service.module.engine, "search", spying)
	assert [hit["route"][2] for hit in service.module.awesomebar_hits(query)] == expected
	assert [r["kb_number"] for r in service.module.search(query, limit=1)["results"]] == expected[:1]
	assert handed == [readable, readable]


def test_a_portal_user_gets_nothing_and_no_message(service):
	service.session.user = PORTAL
	service.calls.clear()
	assert service.module.search("PO") == {"results": [], "problems": []}
	assert service.module.awesomebar_hits("PO") == []
	assert service.frappe.local.message_log == []
	assert [kind for kind, _d in service.calls] == ["has_permission", "has_permission"]


def test_the_awesomebar_hits_over_the_golden_corpus(service):
	hits = service.module.awesomebar_hits("PO")
	assert 1 <= len(hits) <= 5
	assert hits[0]["route"] == ["Form", ARTICLE, "SOP-06-9001"]
	assert hits[0]["label"] == "SOP-06-9001 · Receiving a <b>PO</b> against a packing slip"
	assert hits[0]["description"] == "SOP · 06 Operations"
	assert all(hit["index"] == 160 for hit in hits)
	assert service.module.awesomebar_hits("P") == []


def test_a_result_carries_its_fields(service):
	(result,) = service.module.search("RMA")["results"]
	assert result == {
		"name": "SOP-06-9002",
		"kb_number": "SOP-06-9002",
		"version": 1,
		"title": "Returning damaged goods to a supplier",
		"kind": "SOP",
		"department": "06 Operations",
		"summary": "Getting a return authorization and sending damaged goods back.",
		"snippet": result["snippet"],
		"approved_by": "Alex Example",
		"approved_on": "2026-09-01",
		"review_by": "2027-03-01",
		"review_overdue": False,
		"ai_drafted": False,
		"url": "https://kb.example.com/desk/knowledge-article/SOP-06-9002",
		"matched": ["keywords", "body"],
		"score": result["score"],
	}
	assert "RMA" in result["snippet"]
	assert isinstance(result["score"], float) and result["score"] == round(result["score"], 3)
