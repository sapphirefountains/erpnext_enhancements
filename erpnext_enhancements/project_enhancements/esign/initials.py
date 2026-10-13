# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Client initials beside the clauses that matter.

Nik, 2026-10-13: "Little initials over any part that is something important for the
client to know (each initial needs to be done as its own, not copy paste the initial)."

A template marks a clause with ``{{ initial('renewal', 'Automatic renewal and the annual
rate increase') }}`` (the maintenance agreement has eight). From there, one box travels
through the whole signing life:

1. **Unsigned render** (paper, desk preview, the snapshot a signer is sent):
   :func:`initial_markup` prints a blank box carrying ``data-initial`` and ``data-label``.
   It is deterministic, because execution's drift check compares a fresh unsigned render
   with the snapshot.
2. **Signing page**: ``contract_sign.js`` turns each box into its own drawing pad, which
   starts empty every time. **Or the Contract Builder**, with the client beside you: each
   box becomes a pad in the live preview as soon as its clause is filled in, and
   :func:`stale_initials` refuses any drawn beside wording that changed afterwards.
3. **Sign**: :func:`validate_initials` asks for exactly the boxes in the request's stored
   snapshot (:func:`initial_slots`), never the caller's list, and refuses two
   byte-identical drawings, which only a copied image can produce.
4. **Executed render**: the request's ``initials_json`` (:func:`signed_initials`) puts each
   drawing in its box, exactly as ``sig()`` puts the signature on its line.

Pure apart from ``frappe._`` and ``escape_html``, so the logic tests without a bench.
"""

import base64
import hashlib
import json
import re
from html.parser import HTMLParser

import frappe

#: An initial's key names the clause it sits beside ("renewal", "chemicals"). Written by
#: whoever edits the template, so it is held to a shape the signing page can carry in a
#: data attribute and the server can match without escaping.
INITIAL_KEY_RE = re.compile(r"^[a-z0-9_]{1,40}$")

#: Initials boxes one agreement may ask for. The maintenance agreement has eight; the cap
#: bounds the request body (each box is one PNG, capped like a drawn signature).
MAX_INITIALS = 20

PNG_PREFIX = "data:image/png;base64,"


def initial_markup(initials, key, label=""):
	"""One initials box for clause ``key``: the client's drawn initials once signed, else a blank box.

	A key that is not ``[a-z0-9_]`` renders nothing at all rather than an unmatchable box:
	the template author sees the gap in the preview, and the signing page never asks for an
	initial the server could not accept.
	"""
	key = str(key or "")
	if not INITIAL_KEY_RE.match(key):
		return ""
	escape = frappe.utils.escape_html
	image = (initials or {}).get(key)
	if image:
		return (
			f'<span class="ct-initial is-signed" data-initial="{key}">'
			f'<img class="ct-initial-img" src="{escape(image)}" alt="{escape(frappe._("Client initials"))}">'
			"</span>"
		)
	return (
		f'<span class="ct-initial" data-initial="{key}" data-label="{escape(str(label or ""))}">'
		'<span class="ct-initial-box">&nbsp;</span>'
		f'<span class="ct-initial-label">{escape(frappe._("Client initials"))}</span>'
		"</span>"
	)


def signed_initials(signature):
	"""``{key: image}`` from a signed request's ``initials_json``; empty when there is none.

	Never raises: this is the print path, and a malformed evidence row must print blank
	boxes, not a traceback.
	"""
	if not signature:
		return {}
	try:
		rows = json.loads(signature.get("initials_json") or "[]")
	except (TypeError, ValueError):
		return {}
	out = {}
	for row in rows if isinstance(rows, list) else []:
		if isinstance(row, dict) and row.get("key") and str(row.get("image") or "").startswith(PNG_PREFIX):
			out[str(row["key"])] = row["image"]
	return out


def initial_slots(html):
	"""``[(key, label), ...]`` for every blank initials box in a rendered agreement, in order.

	Read from the snapshot the signer was actually shown, so the server asks for exactly the
	boxes on their screen, never the live template's, which may have changed since. Parsed
	with HTMLParser rather than a regex over markup: attribute order, quoting and entities
	are the parser's problem. A key that appears twice is asked for once.
	"""
	slots, seen = [], set()

	class _Collector(HTMLParser):
		def handle_starttag(self, tag, attrs):
			if tag != "span":
				return
			values = dict(attrs)
			key = values.get("data-initial")
			classes = (values.get("class") or "").split()
			if not key or "is-signed" in classes or key in seen or not INITIAL_KEY_RE.match(key):
				return
			seen.add(key)
			slots.append((key, values.get("data-label") or ""))

	# No try/except: this decides which initials a signature requires, and a parse that
	# failed quietly would require none. Failing loudly costs the signer a retry; failing
	# open would execute a contract with its initials missing.
	_Collector(convert_charrefs=True).feed(html or "")
	return slots


#: The elements a clause lives in, for :func:`clause_texts`. Kept in step with
#: ``CB_BLOCKS`` in the Contract Builder page, which reads the same text off its preview.
CLAUSE_BLOCKS = frozenset({"p", "li", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "dd", "dt"})


def normalize_clause(text):
	"""Whitespace-collapsed text, so a clause compares the same however it was laid out."""
	return " ".join(str(text or "").split())


def clause_texts(html):
	"""``{key: clause text}`` for every blank initials box in a rendered agreement.

	The clause is the text of the paragraph, list item or cell the box sits in, leaving out
	the box itself and anything inside a nested block; a box alone on its own line (the
	maintenance agreement's payment initials) takes the nearest non-empty block before it.

	The Contract Builder lets a client initial clauses while the agreement is still being
	filled in, then signs against the snapshot frozen at issue. It sends the text each
	set of initials was drawn beside, and :func:`stale_initials` compares it with this, so
	initials drawn beside wording that changed afterwards are never carried onto the
	signed copy.
	"""
	blocks = []  # owned text per block, in document (open) order
	stack = []  # indexes into blocks of the open block elements
	where = {}  # key -> (index of its block or None, blocks seen before it)
	state = {"initial_depth": 0}

	class _Clauses(HTMLParser):
		def handle_starttag(self, tag, attrs):
			if state["initial_depth"]:
				if tag == "span":
					state["initial_depth"] += 1
				return
			if tag == "span":
				values = dict(attrs)
				key = values.get("data-initial")
				classes = (values.get("class") or "").split()
				if key and "ct-initial" in classes:
					state["initial_depth"] = 1
					if "is-signed" not in classes and key not in where and INITIAL_KEY_RE.match(key):
						where[key] = (stack[-1] if stack else None, len(blocks))
					return
			if tag in CLAUSE_BLOCKS:
				blocks.append([])
				stack.append(len(blocks) - 1)

		def handle_startendtag(self, tag, attrs):
			# A self-closed tag opens nothing (``<br/>``); never a block or an initials box.
			return

		def handle_endtag(self, tag):
			if state["initial_depth"]:
				if tag == "span":
					state["initial_depth"] -= 1
				return
			if tag in CLAUSE_BLOCKS and stack:
				stack.pop()

		def handle_data(self, data):
			if state["initial_depth"] or not stack:
				return
			blocks[stack[-1]].append(data)

	parser = _Clauses(convert_charrefs=True)
	parser.feed(html or "")
	parser.close()

	texts = [normalize_clause("".join(parts)) for parts in blocks]
	out = {}
	for key, (index, seen) in where.items():
		text = texts[index] if index is not None else ""
		cursor = (index if index is not None else seen) - 1
		while not text and cursor >= 0:
			text = texts[cursor]
			cursor -= 1
		out[key] = text
	return out


def stale_initials(drawn, snapshot):
	"""Keys of the initials whose clause in ``snapshot`` is not the text they were drawn beside.

	``drawn`` is ``{key: {"image": …, "clause": text}}``. A key the snapshot does not ask
	for is ignored here (``validate_initials`` only takes the snapshot's own keys).
	"""
	clauses = clause_texts(snapshot)
	stale = []
	for key, expected in clauses.items():
		entry = drawn.get(key) if isinstance(drawn, dict) else None
		if not isinstance(entry, dict) or not entry.get("image"):
			continue
		if normalize_clause(entry.get("clause")) != expected:
			stale.append(key)
	return stale


def validate_initials(raw, snapshot, validate_image, clean_text=None):
	"""``(initials, error)`` — every box in ``snapshot``, each drawn on its own.

	``raw`` is the signing page's JSON object of ``{key: PNG data URI}``. ``validate_image``
	is the portal's drawn-signature check (shape, size, PNG magic, dimensions; returns the
	re-encoded data URI or ""), applied to every set of initials. Returns a list of
	``{key, label, image, sha256}`` in document order and None, or an empty list and the
	message to show the signer.

	The signing page clears its pad for every box, so no two hand-drawn sets of initials
	are the same PNG; two identical images can only be one drawing submitted twice, and
	that is refused.
	"""
	slots = initial_slots(snapshot)
	if not slots:
		return [], None
	if len(slots) > MAX_INITIALS:
		return [], frappe._("This agreement could not be signed online. Please call us and we'll sort it out.")

	try:
		supplied = json.loads(raw) if isinstance(raw, str) else (raw or {})
	except (TypeError, ValueError):
		supplied = {}
	if not isinstance(supplied, dict):
		supplied = {}

	captured, missing, seen = [], 0, set()
	for key, label in slots:
		image = validate_image(supplied.get(key))
		if not image:
			missing += 1
			continue
		digest = hashlib.sha256(base64.b64decode(image[len(PNG_PREFIX):])).hexdigest()
		if digest in seen:
			return [], frappe._(
				"Each set of initials has to be drawn separately. Please redo the initials that were repeated."
			)
		seen.add(digest)
		label = clean_text(label, 200) if clean_text else label
		captured.append({"key": key, "label": label, "image": image, "sha256": digest})

	if missing:
		return [], frappe._("Please initial every marked clause: {0} of {1} still need your initials.").format(
			missing, len(slots)
		)
	return captured, None
