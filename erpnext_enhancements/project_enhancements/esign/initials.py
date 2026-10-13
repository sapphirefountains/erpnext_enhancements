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
   starts empty every time.
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
