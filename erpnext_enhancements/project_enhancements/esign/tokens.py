# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Signing-token minting, hashing and shape-checking.

The token is a **bearer credential for executing a contract** — the inverse of
``Fountain Move Invite``'s token, which is attribution only and is therefore
stored in plaintext and allowed to fail open. Three consequences:

1. **256 bits**, from :func:`secrets.token_urlsafe`, not ``frappe.generate_hash``
   (22 hex chars ≈ 88 bits, chosen for something that gates nothing).
2. **Only the SHA-256 is stored.** A database copy or a report export must not
   hand someone the ability to sign every open contract.
3. **The shape guard allows ``-`` and ``_``.** ``token_urlsafe`` emits base64url,
   so the ``^[A-Za-z0-9]{8,64}$`` guard used for invite tokens would reject the
   overwhelming majority of real tokens — intermittently, with no error anyone
   could debug. This is the single easiest bug to introduce by copying the
   sibling module, so it has its own test.
"""

import hashlib
import re
import secrets

# base64url alphabet. token_urlsafe(32) yields 43 chars; the range leaves room
# without accepting anything long enough to be worth a database round trip.
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
TOKEN_BYTES = 32


def mint_token():
	"""Return ``(plaintext, sha256_hex)``. The plaintext is never persisted."""
	token = secrets.token_urlsafe(TOKEN_BYTES)
	return token, hash_token(token)


def hash_token(token):
	"""SHA-256 hex of a token, or None when the token is not even the right shape.

	Shape-checking here means a malformed token never reaches the database — the
	same "guard before any DB hit" discipline as ``invites.resolve_invite``.
	"""
	if not token or not TOKEN_RE.match(str(token)):
		return None
	return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def text_fingerprint(text):
	"""Full SHA-256 hex of a block of text.

	Full length, deliberately: ``_record_consent`` truncates to 12 hex chars
	because it is labelling which version of a short blurb was shown, where a
	collision means nothing. Truncating the hash of a contract body to 48 bits
	would make it forgeable, and this one is evidence.
	"""
	return hashlib.sha256((text or "").encode("utf-8")).hexdigest()
