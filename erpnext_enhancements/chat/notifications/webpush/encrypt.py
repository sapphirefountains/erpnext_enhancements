# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""RFC 8291 message encryption, hand-rolled — and the reason that is not recklessness.

Web Push payloads are encrypted end to end: the push service relays bytes it cannot read,
and only the browser holds the key. That is done with the ``aes128gcm`` content coding of RFC
8188, keyed by an ECDH exchange against the subscription's own public key per RFC 8291.

**There is no library for this on the production bench and no way to add one.** A live import
probe returned ``ModuleNotFoundError`` for ``pywebpush``, ``py_vapid``, ``ecdsa`` and
``http_ece``; and the deploy pipeline is exactly ``git fetch/reset → bench migrate → bench
build → FLUSHDB → restart``, with **no ``pip install`` step anywhere**. A dependency added to
``pyproject.toml`` would not be installed by a deploy and would vanish on any VM rebuild. So
this is written by hand against ``cryptography`` and the standard library, which is the same
posture — and the same reasoning — as ``stripe_payments`` hand-rolling its webhook signature
verification and ``quickbooks_online`` hand-rolling its OAuth client.

--------------------------------------------------------------------------------------
Why this file is split the way it is
--------------------------------------------------------------------------------------

**The failure mode here is silence.** A wrong salt, a wrong info string, or the two public
keys concatenated in the wrong order all produce a payload the browser cannot decrypt — and
the browser's response to that is to show *nothing*. No error reaches the server, nothing
appears in the console, and the push service returns 201 Created because as far as it is
concerned the delivery succeeded. It is a bug that looks exactly like "push doesn't work on
my phone" and can survive weeks of investigation.

So the derivation — which is where every one of those mistakes lives — is separated from the
parts that need ``cryptography``, and written with nothing but ``hmac`` and ``hashlib``. That
lets :func:`derive_keys`, :func:`build_header` and :func:`pad_plaintext` be tested on the CI
runner, which installs neither ``cryptography`` nor ``requests``. The full round trip is
pinned separately against RFC 8291's published example, byte for byte, because a self-
consistent implementation that encrypts and decrypts its own output would pass a round-trip
test while being uniformly wrong.

--------------------------------------------------------------------------------------
The record layout, since it is off by one in every prose description
--------------------------------------------------------------------------------------

::

	+-----------+--------+---------+------------------+
	| salt (16) | rs (4) | idlen(1)| keyid (idlen)    |   <- 86 bytes for Web Push
	+-----------+--------+---------+------------------+
	| AES-128-GCM( plaintext || 0x02 || padding )      |
	+--------------------------------------------------+

``keyid`` is the sender's ephemeral public key, uncompressed, so ``idlen`` is always 65 and
the header is always 86 bytes. The ``0x02`` is RFC 8188's *last record* delimiter — ``0x01``
marks a record that is not the last, and using it for a single-record message produces a
payload every browser rejects.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
from typing import Final

#: RFC 8291 §3.4. The literal, the NUL, then **the receiver's key and then the sender's** —
#: in that order. Swapping them is the classic implementation bug and it fails silently: both
#: sides compute a key, they simply compute different ones.
_KEY_INFO_PREFIX: Final[bytes] = b"WebPush: info\x00"

#: RFC 8188 §2.2. The trailing NUL is part of the info string, not a separator this code adds.
_CEK_INFO: Final[bytes] = b"Content-Encoding: aes128gcm\x00"
_NONCE_INFO: Final[bytes] = b"Content-Encoding: nonce\x00"

#: AES-128 key, GCM nonce, GCM tag. Fixed by the content coding, not by preference.
CEK_LENGTH: Final[int] = 16
NONCE_LENGTH: Final[int] = 12
TAG_LENGTH: Final[int] = 16

#: An uncompressed P-256 point: one 0x04 marker plus two 32-byte coordinates.
PUBLIC_KEY_LENGTH: Final[int] = 65

#: RFC 8188's salt is 16 bytes and the length is not negotiable — it is read positionally out
#: of the header by the receiver.
SALT_LENGTH: Final[int] = 16

#: The default record size. Any value works so long as it exceeds the payload plus the
#: delimiter and the tag; 4096 is what the RFC's own example uses and what every browser is
#: exercised against.
DEFAULT_RECORD_SIZE: Final[int] = 4096

#: The overhead one record costs on top of its plaintext: the padding delimiter and the tag.
_RECORD_OVERHEAD: Final[int] = 1 + TAG_LENGTH


def b64u_decode(value: str | bytes) -> bytes:
	"""base64url → bytes, tolerating the missing padding every Web Push value ships without.

	Browsers hand out ``p256dh`` and ``auth`` unpadded, and ``base64.urlsafe_b64decode``
	raises on a string whose length is not a multiple of four. Re-adding the padding here is
	the difference between "push works" and an exception on every subscription.
	"""
	if isinstance(value, str):
		value = value.encode("ascii")
	return base64.urlsafe_b64decode(value + b"=" * (-len(value) % 4))


def b64u_encode(value: bytes) -> str:
	"""bytes → unpadded base64url, which is what every Web Push field expects."""
	return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
	"""HKDF-SHA256, extract and one expand block. **Standard library only, deliberately.**

	Written out rather than imported from ``cryptography.hazmat.primitives.kdf.hkdf`` so that
	the derivation this module gets wrong most easily can be tested on a CI runner with no
	crypto library installed. It is eight lines and they are the eight lines that matter.

	Only one expand block is generated, which caps ``length`` at 32 bytes. Everything Web Push
	derives is 32, 16 or 12, and refusing longer is better than silently truncating a caller
	who wanted more than one block's worth.
	"""
	if length > hashlib.sha256().digest_size:
		raise ValueError(
			f"hkdf here emits a single block, so length must be <= 32; asked for {length}. "
			"Web Push needs 32 (IKM), 16 (CEK) and 12 (nonce) and nothing else — a longer "
			"request means the caller is doing something this function was not written for."
		)
	prk = hmac.new(salt, ikm, hashlib.sha256).digest()
	return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def build_key_info(ua_public: bytes, as_public: bytes) -> bytes:
	"""``"WebPush: info" || 0x00 || ua_public || as_public``. Receiver first. RFC 8291 §3.4.

	A function rather than an inline concatenation because the order is the single most
	commonly transposed detail in every implementation of this spec, and a transposition is
	invisible: both ends derive a perfectly good key, they just derive different ones, and the
	only symptom is a notification that never appears.
	"""
	if len(ua_public) != PUBLIC_KEY_LENGTH:
		raise ValueError(
			f"the subscription's p256dh must be a {PUBLIC_KEY_LENGTH}-byte uncompressed P-256 "
			f"point, got {len(ua_public)} bytes. A shorter value usually means it was stored "
			"base64url-decoded twice, or truncated by a column narrower than the key."
		)
	if len(as_public) != PUBLIC_KEY_LENGTH:
		raise ValueError(f"the ephemeral public key must be {PUBLIC_KEY_LENGTH} bytes")
	return _KEY_INFO_PREFIX + ua_public + as_public


def derive_keys(
	*, ecdh_secret: bytes, auth_secret: bytes, ua_public: bytes, as_public: bytes, salt: bytes
) -> tuple[bytes, bytes]:
	"""The whole RFC 8291 key schedule. Returns ``(content_encryption_key, nonce)``.

	Two HKDF extractions with **different salts**, and that is the part worth staring at:

	1. the *authentication secret* salts the first, mixing in the shared secret the
	   subscription proved it holds — this is what stops anybody who merely knows the public
	   key from encrypting to it;
	2. the *message salt* salts the second, which is what makes two messages to the same
	   subscription produce unrelated keys.

	Using the message salt for both, or the auth secret for both, produces a working-looking
	implementation that no browser can read.

	Standard library only. This is the function the CI runner can actually execute.
	"""
	if len(salt) != SALT_LENGTH:
		raise ValueError(f"the record salt is {SALT_LENGTH} bytes; got {len(salt)}")

	key_info = build_key_info(ua_public, as_public)
	ikm = hkdf(salt=auth_secret, ikm=ecdh_secret, info=key_info, length=32)
	cek = hkdf(salt=salt, ikm=ikm, info=_CEK_INFO, length=CEK_LENGTH)
	nonce = hkdf(salt=salt, ikm=ikm, info=_NONCE_INFO, length=NONCE_LENGTH)
	return cek, nonce


def build_header(salt: bytes, record_size: int, keyid: bytes) -> bytes:
	"""The 86-byte RFC 8188 header: ``salt || rs || idlen || keyid``.

	``rs`` is a **big-endian uint32** and ``idlen`` a single byte. Both are read positionally
	by the receiver, so a length mistake here does not produce a decryption failure — it
	produces a *parse* failure, which some browsers report and others simply ignore.
	"""
	if len(salt) != SALT_LENGTH:
		raise ValueError(f"the record salt is {SALT_LENGTH} bytes; got {len(salt)}")
	if len(keyid) > 255:
		raise ValueError("idlen is one byte, so keyid cannot exceed 255 bytes")
	return salt + struct.pack("!I", record_size) + struct.pack("!B", len(keyid)) + keyid


def pad_plaintext(plaintext: bytes, record_size: int = DEFAULT_RECORD_SIZE) -> bytes:
	"""Append RFC 8188's **last-record** delimiter, ``0x02``.

	No zero padding is added. Padding exists to hide the payload length, and this payload's
	length is already uninformative — the interesting fact about a chat push is that it
	happened, not how many bytes it carries — while every padded byte is one more the push
	service may count against a 4 KB limit.

	``0x01`` is the delimiter for a record that is *not* the last one. Using it here produces a
	message the browser treats as truncated and drops.
	"""
	body = plaintext + b"\x02"
	if len(body) + TAG_LENGTH > record_size:
		raise ValueError(
			f"payload of {len(plaintext)} bytes does not fit in a record of {record_size} "
			f"(needs {len(body) + TAG_LENGTH}). Send a shorter preview rather than raising the "
			"record size: push services impose their own limit, commonly 4096 bytes total, and "
			"a message over it is rejected with 413 rather than truncated."
		)
	return body


def max_payload_bytes(record_size: int = DEFAULT_RECORD_SIZE) -> int:
	"""The largest plaintext that fits one record. What the payload builder trims against."""
	return record_size - _RECORD_OVERHEAD


def encrypt(
	plaintext: bytes,
	*,
	ua_public: bytes,
	auth_secret: bytes,
	record_size: int = DEFAULT_RECORD_SIZE,
	salt: bytes | None = None,
	as_private_bytes: bytes | None = None,
) -> bytes:
	"""One encrypted push body: header ‖ AES-128-GCM(plaintext ‖ 0x02).

	``salt`` and ``as_private_bytes`` exist **only so RFC 8291's published example can be
	reproduced byte for byte**. Every production call omits both and gets a fresh random salt
	and a fresh ephemeral key, which is required: reusing an ephemeral key across messages
	would make two payloads to the same subscription share a key stream.

	``cryptography`` is imported inside the function, not at module scope, so that the pure
	half of this file stays importable on the CI runner — which installs neither it nor
	``requests`` — and so that a bench missing the library fails at the one call that needed
	it rather than at import time on every path that mentions chat.
	"""
	from cryptography.hazmat.primitives.asymmetric import ec
	from cryptography.hazmat.primitives.ciphers.aead import AESGCM

	if as_private_bytes is None:
		as_private = ec.generate_private_key(ec.SECP256R1())
	else:
		as_private = ec.derive_private_key(
			int.from_bytes(as_private_bytes, "big"), ec.SECP256R1()
		)

	as_public = _raw_public_bytes(as_private.public_key())
	peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
	ecdh_secret = as_private.exchange(ec.ECDH(), peer)

	message_salt = os.urandom(SALT_LENGTH) if salt is None else salt
	cek, nonce = derive_keys(
		ecdh_secret=ecdh_secret,
		auth_secret=auth_secret,
		ua_public=ua_public,
		as_public=as_public,
		salt=message_salt,
	)

	body = pad_plaintext(plaintext, record_size)
	# aad is empty for aes128gcm: the header is not authenticated data, it is transmitted in
	# the clear and the receiver parses it before it can derive anything to authenticate with.
	ciphertext = AESGCM(cek).encrypt(nonce, body, None)
	return build_header(message_salt, record_size, as_public) + ciphertext


def _raw_public_bytes(public_key: object) -> bytes:
	"""A P-256 public key as the 65-byte uncompressed point every Web Push field carries."""
	from cryptography.hazmat.primitives import serialization

	return public_key.public_bytes(  # type: ignore[attr-defined]
		encoding=serialization.Encoding.X962,
		format=serialization.PublicFormat.UncompressedPoint,
	)


def decrypt(
	body: bytes, *, ua_private_bytes: bytes, auth_secret: bytes
) -> bytes:
	"""Reverse the whole thing. **Test and diagnostic use only — nothing in production calls it.**

	It exists for two reasons that justify carrying code no request path touches. First, it
	makes the RFC's example checkable from both directions, so a failure says *where* rather
	than only *that*. Second, when somebody eventually reports "the notification is blank on
	my phone", this is what answers whether the server produced a decryptable payload at all —
	which is otherwise unanswerable, because the browser reports nothing and the push service
	reports success.
	"""
	from cryptography.hazmat.primitives.asymmetric import ec
	from cryptography.hazmat.primitives.ciphers.aead import AESGCM

	salt = body[:SALT_LENGTH]
	idlen = body[SALT_LENGTH + 4]
	keyid_at = SALT_LENGTH + 5
	as_public = body[keyid_at : keyid_at + idlen]
	ciphertext = body[keyid_at + idlen :]

	ua_private = ec.derive_private_key(int.from_bytes(ua_private_bytes, "big"), ec.SECP256R1())
	ua_public = _raw_public_bytes(ua_private.public_key())
	peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public)
	ecdh_secret = ua_private.exchange(ec.ECDH(), peer)

	cek, nonce = derive_keys(
		ecdh_secret=ecdh_secret,
		auth_secret=auth_secret,
		ua_public=ua_public,
		as_public=as_public,
		salt=salt,
	)
	padded = AESGCM(cek).decrypt(nonce, ciphertext, None)
	return padded.rstrip(b"\x00")[:-1]
