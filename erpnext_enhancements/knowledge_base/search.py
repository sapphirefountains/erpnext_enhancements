# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Knowledge base search: a small BM25F over the published articles (WI-080 PR 5).

**Pure.** Standard library only (plus ``constants``, which is too), so the bench-free pytest suite
(``tests/test_knowledge_base_search.py``) runs every rule here with ``frappe`` absent, and a fresh
interpreter pins that nothing imports it. ``search_service.py`` is the frappe half: it reads the
corpus, keeps the index, and applies the caller's permissions before anything here ranks.

Why not v16's own search: prod's ``__global_search`` has ``ft_min_word_len=4``, so "PO", "QBO" and
"SOP" are never indexed; InnoDB FULLTEXT drops them the same way; and SQLiteSearch's first build is
a queued job on the redis every deploy flushes, with no row permissions. The words people search
the knowledge base with are exactly the short ones.

**Tokens** (:func:`tokenize`), in this order:

1. NFKC, so a full-width or ligature spelling reads as the plain one.
2. **KB numbers**: ``kb`` then an optional space, ``_`` or ``-``, then 1 to 4 digits (``KB-0601``,
   ``kb 601``, ``KB0601``, ``kb_601``) is one term, ``kb-0601``, zero-padded to four digits.
3. **Document numbers**: 2 to 5 letters, ``-``, 2 to 6 digits (``SOP-9001``, ``PO-1234``) is kept
   as one compound term, and its two parts are indexed as well.
4. **Punctuated acronyms**: single letters or runs of digits joined by ``-``, ``&``, ``/`` or ``.``,
   with at least one letter, are one acronym term with the punctuation dropped: ``W-2`` is ``w2``,
   ``I-9`` ``i9``, ``G-702`` ``g702``, ``T&M`` ``tm``, ``A/R`` ``ar``, ``P.O.`` ``po``, and a plural
   ``W-2s`` ``w2``. So ``W-2``, ``W2`` and ``w2`` all meet. A digit part of 2 or more characters is
   indexed as well (``702``); ``3-4`` and a date, with no letter, are never joined.
5. **Words** are runs of letters and digits. One-character tokens are dropped; **tokens of 2 or
   more characters are kept**, which is the point.
6. **Acronyms.** A token written in capitals, 2 to 6 letters or digits with at least one letter
   (PO, QBO, SOP, AIA, SOV, PTO, W2), is an acronym, and so is its plural (``POs`` is ``po``).
   In a run of text with no lowercase letter at all (an all-caps heading or title; a run is a line,
   or a sentence), only 2- and 3-character tokens are acronyms and longer ones are ordinary words:
   otherwise every word of "RECEIVING PACKING SLIPS" would escape the stemmer. Acronyms are never
   stemmed and never stopwords, so "IT" is a term and "it" is not.
7. **Stopwords** (:data:`STOPWORDS`, English function words) are dropped, except acronyms and
   everything in the keywords field: an author who typed a word as a keyword meant it.
8. **Stemming** (:func:`stem`) for other alphabetic tokens of 4 or more characters, in order:
   ``ies`` -> ``y`` and ``sses`` -> ``ss``; drop ``s`` (not ``ss``, ``us`` or ``is``), then
   ``ing`` or ``ed``, each only when 3 letters remain; undouble a final ``pp tt nn gg dd mm rr``;
   drop a final ``e`` when 4 letters remain. So receive, receives, received and receiving are all
   ``receiv``, and shipping and shipped are ``ship``.

**Ranking** is BM25F: title and keywords weigh 3, the summary 2, the body 1, and a *meta* field 0.5
(the article's kind, the words ``constants.KIND_ALIASES`` gives that kind, and its department), so
"procedure for receiving" reaches an SOP and "workflow for returns" a Process even when the text
never says so. ``b`` is 0.3 on the short fields and 0.75 on the body, ``k1`` is 1.2, and idf is
``ln(1 + (N - df + 0.5) / (df + 0.5))`` over the whole published corpus. Each (term, document)
weight is computed once, at build.

**A query** is tokenized the same way. A lowercase word also tries its unstemmed spelling and,
when it ends in ``s``, the same without it, and scores its best: an acronym written in lowercase
("msds", "pos") then still meets the capitals. A KB number in the query **pins** that article first,
in the order the query names them. Filters (``allowed``, ``department``, ``kind``) remove documents
**before** scoring, so nothing outside them is ever ranked. Ties go pinned first, then score, then
KB number.

**Snippets** (:func:`snippet`) are the 240-character window of the article's text holding the
most query terms, cut at word boundaries, with no highlight marks; the summary when the text holds
none. :func:`mark` is the one place that does highlight, for the AwesomeBar's label, and escapes
everything it does not wrap.
"""

import array
import html
import math
import re
import unicodedata
from collections import Counter, namedtuple
from functools import lru_cache

from erpnext_enhancements.knowledge_base import constants

__all__ = [
	"FIELDS",
	"K1",
	"STOPWORDS",
	"WEIGHTS",
	"B",
	"Document",
	"Hit",
	"Index",
	"Token",
	"build_index",
	"mark",
	"normalize_kb_number",
	"plain_text",
	"query_terms",
	"search",
	"snippet",
	"stem",
	"tokenize",
]

#: One article, as the index reads it. ``key`` is its name (the KB number), ``body`` its Markdown
#: (``body_md``), ``kind`` one of ``constants.ARTICLE_KINDS`` or ``None``, ``department`` a
#: ``department_block`` option.
Document = namedtuple("Document", "key kb_number title keywords summary body kind department")

#: A term, and whether it came from an acronym (or a KB or document number), which is never
#: stemmed and never a stopword.
Token = namedtuple("Token", "term acronym")

#: A result: the document's key, its score, the fields that matched (``"kb_number"`` first when it
#: was pinned), and whether a KB number in the query pinned it.
Hit = namedtuple("Hit", "key score matched pinned")

#: The indexed fields, in the order ``Hit.matched`` lists them.
FIELDS = ("title", "keywords", "summary", "body", "meta")
WEIGHTS = {"title": 3.0, "keywords": 3.0, "summary": 2.0, "body": 1.0, "meta": 0.5}
#: Length normalization: gentle on the short fields, the usual 0.75 on the body.
B = {"title": 0.3, "keywords": 0.3, "summary": 0.3, "body": 0.75, "meta": 0.3}
K1 = 1.2
_FIELD_BIT = {field: 1 << i for i, field in enumerate(FIELDS)}

#: English function words. Never an acronym (``IT`` is a term; ``it`` is not) and never a keyword.
STOPWORDS = frozenset(
	(
		"about after all also am an and any are as at be been before being but by can could did do "
		"does doing done for from get got had has have having he her here him his how if in into is "
		"it its just me more most my no nor not of off on once only or other our out over own same "
		"she should so some such than that the their them then there these they this those through "
		"to too under until up us very was we were what when where which while who whom why will "
		"with would you your"
	).split()
)

_SNIPPET_WIDTH = 240
_ELLIPSIS = "\u2026"

#: A KB number, a document number, a punctuated chain, or a word, tried in that order at each
#: position. A punctuated chain is two or more pieces, each a single letter or 1 to 6 digits, each
#: joined to the next by one ``-``, ``&``, ``/`` or ``.``, standing alone (no letter or digit
#: touching either end), with an optional plural ``s``; so ``x-ray``, whose ``ray`` is no single
#: letter, is not one. It is an acronym only when a piece is a letter, which :func:`_scan_run`
#: checks: a chain with none (``3-4``, ``9/28/2026``, ``1.5``) is read as the words it is made of,
#: as before. Checked there rather than by a lookahead here, because a lookahead that walks the
#: chain to its first letter is retried at every piece of a long letterless chain (quadratic).
_TOKEN = re.compile(
	r"(?P<kb>(?i:\bkb[\s_\-]?0*(?P<kbn>[0-9]{1,4})\b))"
	r"|(?P<doc>\b(?P<letters>[A-Za-z]{2,5})-(?P<digits>[0-9]{2,6})\b)"
	r"|(?P<joined>(?<![^\W_])"
	r"(?P<chain>(?:[A-Za-z]|[0-9]{1,6})(?:[-&/.](?:[A-Za-z]|[0-9]{1,6}))+)s?(?![^\W_]))"
	r"|(?P<word>[^\W_]+)"
)
_JOINED_PIECE = re.compile(r"[A-Za-z0-9]+")
_WORD_RUN = re.compile(r"[^\W_]+")
_KB_QUERY = re.compile(r"\bkb[\s_\-]?0*([0-9]{1,4})\b", re.IGNORECASE)
_KB_EXACT = re.compile(r"kb[\s_\-]?0*([0-9]{1,4})", re.IGNORECASE)
#: Where one run of text ends and the next begins: a line break, or a sentence's end.
_RUN_BREAK = re.compile(r"[\r\n]+|[.!?;:](?=\s|$)")
_ACRONYM_PLURAL = re.compile(r"[A-Z]{2,6}s")
_LOWER_PLURAL = re.compile(r"[a-z0-9]{2,6}s")
_UNDOUBLE = ("pp", "tt", "nn", "gg", "dd", "mm", "rr")

# Markdown and HTML, down to the words a reader sees.
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_REFERENCE = re.compile(r"^[ \t]*\[[^\]]+\]:[ \t]*\S.*$", re.MULTILINE)
_MD_AUTOLINK = re.compile(r"<(?:https?|mailto):[^>\s]*>", re.IGNORECASE)
_MD_BARE_URL = re.compile(r"\bhttps?://\S+", re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>\n]*>")
_MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>~])")
_MD_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.MULTILINE)
_MD_QUOTE = re.compile(r"^[ \t]{0,3}(?:>[ \t]?)+", re.MULTILINE)
_MD_LIST = re.compile(r"^[ \t]*(?:[-*+]|[0-9]+[.)])[ \t]+", re.MULTILINE)
_MD_RULE = re.compile(r"^[ \t]*(?:[-*_=|:][ \t]*){3,}$", re.MULTILINE)
_MD_EMPHASIS = re.compile(r"[*`~]+|(?<![^\W_])_+|_+(?![^\W_])")
_SPACES = re.compile(r"[ \t\f\v]+")


# ------------------------------------------------------------------ tokens


def tokenize(text, *, keywords=False):
	"""The terms of ``text``, in order. ``keywords=True`` for the keywords field: nothing in it is a
	stopword."""
	return [token for _start, _end, tokens in _scan(text, keywords) for token in tokens]


def normalize_kb_number(text):
	"""``"kb 601"`` -> ``"KB-0601"``; ``None`` unless the whole of ``text`` is one KB number (so
	``"KBV-00001"``, a version's id, is ``None``)."""
	if not isinstance(text, str):
		return None
	match = _KB_EXACT.fullmatch(unicodedata.normalize("NFKC", text).strip())
	return f"KB-{int(match.group(1)):04d}" if match else None


@lru_cache(maxsize=65536)
def stem(word):
	"""The suffix rules above, for an alphabetic, casefolded word of 4 or more characters; anything
	else comes back as it is."""
	if len(word) < 4 or not word.isalpha():
		return word
	if word.endswith("ies"):
		word = word[:-3] + "y"
	elif word.endswith("sses"):
		word = word[:-2]
	elif word.endswith("s") and not word.endswith(("ss", "us", "is")) and len(word) - 1 >= 3:
		word = word[:-1]
	if word.endswith("ing") and len(word) - 3 >= 3:
		word = word[:-3]
	elif word.endswith("ed") and len(word) - 2 >= 3:
		word = word[:-2]
	if word.endswith(_UNDOUBLE):
		word = word[:-1]
	if word.endswith("e") and len(word) - 1 >= 4:
		word = word[:-1]
	return word


def _scan(text, keywords=False):
	"""``[(start, end, tokens)]`` over ``unicodedata.normalize("NFKC", text)``: every token-bearing
	span, with the positions the snippet and the highlighter need. A span whose word was dropped
	(one character, or a stopword) is not listed."""
	if not text:
		return []
	text = unicodedata.normalize("NFKC", str(text))
	spans = []
	start = 0
	for boundary in _RUN_BREAK.finditer(text):
		_scan_run(text, start, boundary.start(), keywords, spans)
		start = boundary.end()
	_scan_run(text, start, len(text), keywords, spans)
	return spans


def _scan_run(text, start, end, keywords, out):
	run = text[start:end]
	if not run.strip():
		return
	shouting = not any(ch.islower() for ch in run)
	for match in _TOKEN.finditer(run):
		begin, finish = start + match.start(), start + match.end()
		if match.group("kb") is not None:
			out.append((begin, finish, (Token(f"kb-{int(match.group('kbn')):04d}", True),)))
		elif match.group("doc") is not None:
			letters, digits = match.group("letters"), match.group("digits")
			tokens = [Token(f"{letters.casefold()}-{digits}", True)]
			part = _word(letters, shouting, keywords)
			if part is not None:
				tokens.append(part)
			tokens.append(Token(digits, False))
			out.append((begin, finish, tuple(tokens)))
		elif match.group("joined") is not None:
			pieces = _JOINED_PIECE.findall(match.group("chain"))
			if not any(piece.isalpha() for piece in pieces):
				# No letter, so no acronym: the words it is made of, each with its own span.
				for word in _WORD_RUN.finditer(run, match.start(), match.end()):
					token = _word(word.group(), shouting, keywords)
					if token is not None:
						out.append((start + word.start(), start + word.end(), (token,)))
				continue
			tokens = [Token("".join(pieces).casefold(), True)]
			# Its digit parts are indexed as well, as a document number's are ("G-702" is found by
			# "702" too); a lone letter never is, as a one-character word never is.
			tokens.extend(Token(piece, False) for piece in pieces if len(piece) >= 2)
			out.append((begin, finish, tuple(tokens)))
		else:
			token = _word(match.group("word"), shouting, keywords)
			if token is not None:
				out.append((begin, finish, (token,)))


@lru_cache(maxsize=65536)
def _word(raw, shouting, keywords):
	"""One word as a Token, or ``None`` when it is dropped."""
	if len(raw) < 2:
		return None
	if _is_acronym(raw, shouting):
		return Token(raw.casefold(), True)
	if not shouting and _ACRONYM_PLURAL.fullmatch(raw):
		return Token(raw[:-1].casefold(), True)
	folded = raw.casefold()
	if not keywords and folded in STOPWORDS:
		return None
	return Token(stem(folded), False)


def _is_acronym(raw, shouting):
	# isupper(): at least one cased character, and every cased one a capital, so "W2" is one and
	# "2024" is not.
	if not raw.isupper() or not raw.isalnum():
		return False
	return 2 <= len(raw) <= (3 if shouting else 6)


def query_terms(query):
	"""Every term a query can match, as a set: what the snippet and :func:`mark` look for."""
	terms = set()
	for slot in _slots(query):
		terms |= slot
	return terms


def _slots(query):
	"""The query as scoring slots, first-seen order, each once: a set of spellings per token, of
	which a document scores its best (see "A query" above)."""
	if not isinstance(query, str):
		return []
	text = unicodedata.normalize("NFKC", query)
	slots, seen = [], set()
	for start, end, tokens in _scan(text):
		for token in tokens:
			slot = {token.term}
			if not token.acronym and len(tokens) == 1:
				# A word the stemmer changed may be an acronym typed in lowercase: try it as typed,
				# and without a plural s.
				raw = text[start:end].casefold()
				slot.add(raw)
				if _LOWER_PLURAL.fullmatch(raw):
					slot.add(raw[:-1])
			key = frozenset(slot)
			if key not in seen:
				seen.add(key)
				slots.append(key)
	return slots


def _kb_numbers(query):
	"""The KB numbers ``query`` names, normalized, in order, each once."""
	numbers = []
	for match in _KB_QUERY.finditer(unicodedata.normalize("NFKC", query or "")):
		number = f"KB-{int(match.group(1)):04d}"
		if number not in numbers:
			numbers.append(number)
	return numbers


# ------------------------------------------------------------------ Markdown to text


def plain_text(markdown):
	"""What a reader sees of a Markdown (or HTML-bearing) text: link and image text kept, their URLs,
	HTML tags, headings' and lists' markers, table rules and emphasis marks dropped. Lines are kept,
	because a line is a run for the acronym rule."""
	if not markdown:
		return ""
	text = str(markdown)
	text = _MD_REFERENCE.sub("", text)
	text = _MD_IMAGE.sub(r"\1", text)
	text = _MD_LINK.sub(r"\1", text)
	text = _MD_AUTOLINK.sub(" ", text)
	text = _MD_BARE_URL.sub(" ", text)
	text = _HTML_TAG.sub(" ", text)
	text = _MD_RULE.sub("", text)
	text = _MD_HEADING.sub("", text)
	text = _MD_QUOTE.sub("", text)
	text = _MD_LIST.sub("", text)
	text = _MD_ESCAPE.sub(r"\1", text)
	text = _MD_EMPHASIS.sub("", text)
	text = html.unescape(text).replace("|", " ")
	lines = (_SPACES.sub(" ", line).strip() for line in text.splitlines())
	return "\n".join(line for line in lines if line)


# ------------------------------------------------------------------ the index


class Index:
	"""The built index: postings per term, and per document only what filtering and pinning need.
	No document text is kept."""

	__slots__ = ("by_kb", "departments", "idf", "kb_numbers", "keys", "kinds", "postings")

	def __init__(self):
		self.keys = []
		self.kb_numbers = []
		self.kinds = []
		self.departments = []
		self.by_kb = {}
		#: term -> (document numbers, weights, field bits), three parallel compact arrays.
		self.postings = {}
		self.idf = {}

	def __len__(self):
		return len(self.keys)


def build_index(documents):
	"""An :class:`Index` of ``documents`` (any iterable of :class:`Document`). Each document is read
	once and dropped: its text is not kept."""
	index = Index()
	counts = []  # per document: {field: Counter}
	lengths = {field: [] for field in FIELDS}
	for doc in documents:
		number = normalize_kb_number(doc.kb_number or doc.key or "")
		position = len(index.keys)
		index.keys.append(doc.key)
		index.kb_numbers.append(number or str(doc.key or ""))
		index.kinds.append(doc.kind or None)
		index.departments.append(doc.department or None)
		if number and number not in index.by_kb:
			index.by_kb[number] = position
		fields = {
			"title": tokenize(doc.title),
			"keywords": tokenize(doc.keywords, keywords=True),
			"summary": tokenize(doc.summary),
			"body": tokenize(plain_text(doc.body)),
			"meta": _meta(doc.kind, doc.department),
		}
		row = {}
		for field, tokens in fields.items():
			lengths[field].append(len(tokens))
			row[field] = Counter(token.term for token in tokens)
		counts.append(row)

	total = len(index.keys)
	average = {field: (sum(values) / total if total else 0.0) or 1.0 for field, values in lengths.items()}
	gathered = {}
	for position, row in enumerate(counts):
		weights = {}
		for field in FIELDS:
			norm = 1.0 - B[field] + B[field] * lengths[field][position] / average[field]
			scale = WEIGHTS[field] / norm
			bit = _FIELD_BIT[field]
			for term, frequency in row[field].items():
				weight, bits = weights.get(term, (0.0, 0))
				weights[term] = (weight + scale * frequency, bits | bit)
		for term, (weight, bits) in weights.items():
			entry = gathered.get(term)
			if entry is None:
				entry = gathered[term] = (array.array("I"), array.array("d"), array.array("B"))
			entry[0].append(position)
			entry[1].append(weight)
			entry[2].append(bits)
	index.postings = gathered
	for term, (documents_with, _weights, _bits) in gathered.items():
		df = len(documents_with)
		index.idf[term] = math.log(1.0 + (total - df + 0.5) / (df + 0.5))
	return index


def _meta(kind, department):
	"""The meta field's terms, each once: the kind, the words people use for it, and the
	department's code and name."""
	words = []
	if kind in constants.ARTICLE_KINDS:
		words.append(kind)
		words.extend(alias for alias, target in constants.KIND_ALIASES.items() if target == kind)
	if department:
		words.append(str(department))
	seen, tokens = set(), []
	for token in tokenize(" \n".join(words)):
		if token.term not in seen:
			seen.add(token.term)
			tokens.append(token)
	return tokens


# ------------------------------------------------------------------ searching


def search(index, query, *, allowed=None, department=None, kind=None, limit=10):
	"""The documents that match ``query``, best first, as :class:`Hit` rows.

	``allowed`` (the keys the caller may read), ``department`` (an option) and ``kind`` (one of the
	three) remove documents **before** anything is scored, so a document outside them is never
	ranked, pinned or counted. ``None`` means no restriction; an empty ``allowed`` means nothing.
	``limit`` of ``None`` returns every hit.
	"""
	total = len(index.keys)
	if not total or not isinstance(query, str) or not query.strip():
		return []
	wanted = set(allowed) if allowed is not None else None
	candidate = bytearray(total)
	for position in range(total):
		if wanted is not None and index.keys[position] not in wanted:
			continue
		if department is not None and index.departments[position] != department:
			continue
		if kind is not None and index.kinds[position] != kind:
			continue
		candidate[position] = 1
	if not any(candidate):
		return []

	pinned = []
	for number in _kb_numbers(query):
		position = index.by_kb.get(number)
		if position is not None and candidate[position] and position not in pinned:
			pinned.append(position)

	scores, bits = {}, {}
	for slot in _slots(query):
		best = {}
		# Sorted: a frozenset's order follows the hash seed, and ``matched`` must not.
		for term in sorted(slot):
			entry = index.postings.get(term)
			if entry is None:
				continue
			idf = index.idf[term]
			for position, weight, fields in zip(*entry, strict=True):
				if not candidate[position]:
					continue
				score = idf * weight / (K1 + weight)
				previous = best.get(position)
				if previous is None or score > previous[0]:
					best[position] = (score, fields)
		for position, (score, fields) in best.items():
			scores[position] = scores.get(position, 0.0) + score
			bits[position] = bits.get(position, 0) | fields

	hits = [
		Hit(index.keys[p], scores.get(p, 0.0), ("kb_number", *_fields(bits.get(p, 0))), True) for p in pinned
	]
	ranked = sorted(
		(p for p, score in scores.items() if score > 0 and p not in pinned),
		key=lambda p: (-scores[p], index.kb_numbers[p], str(index.keys[p])),
	)
	hits.extend(Hit(index.keys[p], scores[p], _fields(bits[p]), False) for p in ranked)
	if limit is not None:
		hits = hits[: max(int(limit), 0)]
	return hits


def _fields(mask):
	return tuple(field for field in FIELDS if mask & _FIELD_BIT[field])


# ------------------------------------------------------------------ what a result shows


def snippet(text, query, width=_SNIPPET_WIDTH, *, fallback=""):
	"""The ``width``-character window of ``text`` (Markdown) holding the most query terms, cut at word
	boundaries, with ``…`` where it was cut. No highlight marks. When the text holds no query term,
	``fallback`` (the summary) from its start instead, cut the same way."""
	# Scanned with its lines (a line is a run for the acronym rule), then shown as one line: the
	# newline becomes a space, so every position the scan found still points at the same word.
	# NFKC first, because the scan's positions are positions in the normalized text.
	lined = unicodedata.normalize("NFKC", plain_text(text))
	terms = query_terms(query) if isinstance(query, str) else set()
	spans = [(start, end) for start, end, tokens in _scan(lined) if terms & {t.term for t in tokens}]
	if not spans:
		return _cut(unicodedata.normalize("NFKC", plain_text(fallback)).replace("\n", " "), 0, width)
	plain = lined.replace("\n", " ")
	best_count, best = 0, (0, 0)
	last = 0
	for first in range(len(spans)):
		last = max(last, first)
		while last + 1 < len(spans) and spans[last + 1][1] - spans[first][0] <= width:
			last += 1
		count = last - first + 1
		if count > best_count:
			best_count, best = count, (first, last)
	begin, finish = spans[best[0]][0], spans[best[1]][1]
	# A little of what comes before the first match, if there is room; and never a window that
	# stops short of the end when the text would fill it.
	lead = min(max(width - (finish - begin), 0) // 3, 60)
	start = max(0, min(begin - lead, len(plain) - width))
	return _cut(plain, start, width, keep=(begin, finish))


def _cut(text, start, width, keep=None):
	"""``text[start:start + width]``, moved in to word boundaries, with an ellipsis at each end that
	was cut. ``keep`` is a ``(begin, end)`` range the moves never cut into."""
	if not text:
		return ""
	if width <= 0:
		return ""
	end = min(start + width, len(text))
	keep_begin, keep_end = keep if keep else (end, start)
	if start > 0 and not text[start - 1].isspace():
		space = text.find(" ", start, max(min(keep_begin, end), start))
		if space != -1:
			start = space + 1
	if end < len(text) and not text[end].isspace():
		space = text.rfind(" ", max(start, keep_end), end)
		if space != -1 and space > start:
			end = space
	piece = text[start:end].strip()
	if start > 0:
		piece = _ELLIPSIS + piece
	if end < len(text):
		piece += _ELLIPSIS
	return piece


def mark(text, query, open_tag="<b>", close_tag="</b>"):
	"""``text`` as HTML: every character escaped, and each word that matches the query wrapped in
	``open_tag``/``close_tag``. For the AwesomeBar, which renders a hit's label as HTML."""
	text = unicodedata.normalize("NFKC", str(text or ""))
	terms = query_terms(query) if isinstance(query, str) else set()
	out, at = [], 0
	for start, end, tokens in _scan(text):
		if terms & {token.term for token in tokens}:
			out.append(html.escape(text[at:start]))
			out.append(open_tag + html.escape(text[start:end]) + close_tag)
			at = end
	out.append(html.escape(text[at:]))
	return "".join(out)
