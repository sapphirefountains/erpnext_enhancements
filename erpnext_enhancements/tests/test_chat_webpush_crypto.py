"""RFC 8291's published example, reproduced byte for byte. The only test that can catch this.

Subject: ``erpnext_enhancements.chat.notifications.webpush.encrypt``.

**Requires ``cryptography``**, which the standard CI job does not install — so this suite
gets its own step with its own install line. That is a deliberate exception to the repo's
"no new dependency" posture rather than a drift from it: ``cryptography`` is already present
on the production bench (46.0.7, measured), the exception is confined to a test runner, and
nothing shipped gains a dependency.

--------------------------------------------------------------------------------------
Why a known-answer test, and why a round-trip test would be worthless here
--------------------------------------------------------------------------------------

Every mistake available in this code is **symmetric**. Swap the two public keys in
``key_info``; use the message salt where the auth secret belongs; write ``0x01`` as the
padding delimiter instead of ``0x02``. In each case encryption and decryption still agree
with each other perfectly, so a round-trip test passes — and every real browser rejects the
payload, silently, showing nothing, while the push service returns 201 Created and the
server logs a success.

The only thing that catches a uniformly-wrong implementation is an answer computed by
somebody else. That is what the vector below is: the receiver key, the sender key and the
salt are all pinned, so the output is fully determined and one byte of disagreement fails.

The values are RFC 8291 §5's, transcribed as functional constants — the fixed inputs and
expected output of a conformance check, which is what a test vector is for.

Plain pytest functions, so this file needs its **own**
``python -m pytest erpnext_enhancements/tests/test_chat_webpush_crypto.py -q`` step.
"""

from __future__ import annotations

import importlib.util

import pytest

from erpnext_enhancements.chat.notifications.webpush import encrypt as E

_HAS_CRYPTOGRAPHY = bool(importlib.util.find_spec("cryptography"))

pytestmark = pytest.mark.skipif(
	not _HAS_CRYPTOGRAPHY,
	reason=(
		"cryptography is not installed. This suite is the ONLY check on the Web Push key "
		"schedule, and a skipped step reads as a passing one — so if you are seeing this in "
		"CI, the step's install line is missing rather than the suite being optional."
	),
)

# --- RFC 8291 §5, the published example ----------------------------------------
#
# FAKE-DO-NOT-USE — these are the RFC's own published example keys. They are in every
# conformance suite on the internet and encrypt to a well-known constant; they are not this
# site's keys and could not be. The marker is here so the committed-secret scanner reads them
# as the test fixture they are.

PLAINTEXT = b"When I grow up, I want to be a watermelon"

UA_PRIVATE = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
UA_PUBLIC = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
AUTH_SECRET = "BTBZMqHH6r4Tts7J_aSIgg"

AS_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
AS_PUBLIC = "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8"

SALT = "DGv6ra1nlYgDCS1FRnbzlw"
RECORD_SIZE = 4096

EXPECTED_BODY = (
	"DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLoc"
	"InmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLV"
	"WGNWQexSgSxsj_Qulcy4a-fN"
)


def _encrypt_the_example() -> bytes:
	return E.encrypt(
		PLAINTEXT,
		ua_public=E.b64u_decode(UA_PUBLIC),
		auth_secret=E.b64u_decode(AUTH_SECRET),
		record_size=RECORD_SIZE,
		salt=E.b64u_decode(SALT),
		as_private_bytes=E.b64u_decode(AS_PRIVATE),
	)


# --- the known answer -----------------------------------------------------------


def test_the_rfc_8291_example_encrypts_to_the_published_bytes() -> None:
	"""One assertion, and it is the whole reason this file exists.

	If this fails, do not adjust the expectation. Every value feeding it is fixed by the RFC,
	so the output is fully determined and the implementation is what is wrong.
	"""
	assert E.b64u_encode(_encrypt_the_example()) == EXPECTED_BODY


def test_the_example_round_trips_back_to_the_plaintext() -> None:
	"""Weaker than the vector above, and it is here to localise a failure rather than to find
	one: if the vector fails but this passes, the key schedule is self-consistent and wrong,
	which points at an info string rather than at the framing."""
	body = _encrypt_the_example()
	assert E.decrypt(body, ua_private_bytes=E.b64u_decode(UA_PRIVATE), auth_secret=E.b64u_decode(AUTH_SECRET)) == PLAINTEXT


def test_a_fresh_encryption_still_decrypts_for_the_same_subscription() -> None:
	"""The production path: no injected salt, no injected ephemeral key."""
	body = E.encrypt(
		b"hello",
		ua_public=E.b64u_decode(UA_PUBLIC),
		auth_secret=E.b64u_decode(AUTH_SECRET),
	)
	assert E.decrypt(body, ua_private_bytes=E.b64u_decode(UA_PRIVATE), auth_secret=E.b64u_decode(AUTH_SECRET)) == b"hello"


def test_two_encryptions_of_one_message_never_repeat() -> None:
	"""A reused salt or a reused ephemeral key would make two payloads to the same
	subscription share a key stream, which is the one mistake here with a consequence worse
	than "it does not work"."""
	args = {"ua_public": E.b64u_decode(UA_PUBLIC), "auth_secret": E.b64u_decode(AUTH_SECRET)}
	bodies = {E.encrypt(b"same message", **args) for _ in range(8)}
	assert len(bodies) == 8

	salts = {body[: E.SALT_LENGTH] for body in bodies}
	keys = {body[21 : 21 + E.PUBLIC_KEY_LENGTH] for body in bodies}
	assert len(salts) == 8, "the record salt must be fresh per message"
	assert len(keys) == 8, "the ephemeral key must be fresh per message"


# --- the framing, asserted positionally -----------------------------------------


def test_the_header_is_exactly_eighty_six_bytes_and_laid_out_in_order() -> None:
	"""Read positionally by the receiver, so a length error is a parse failure rather than a
	decryption failure — and some browsers report that and some just drop the message."""
	body = _encrypt_the_example()
	salt = E.b64u_decode(SALT)
	as_public = E.b64u_decode(AS_PUBLIC)

	assert body[:16] == salt
	assert int.from_bytes(body[16:20], "big") == RECORD_SIZE
	assert body[20] == E.PUBLIC_KEY_LENGTH == 65
	assert body[21:86] == as_public
	assert as_public[0] == 0x04, "an uncompressed P-256 point begins with 0x04"


def test_the_ciphertext_is_the_plaintext_plus_delimiter_plus_tag() -> None:
	body = _encrypt_the_example()
	assert len(body) == 86 + len(PLAINTEXT) + 1 + E.TAG_LENGTH


# --- the pieces the CI runner can check without cryptography --------------------
#
# Duplicated deliberately in test_chat_webpush_encoding.py, which has no crypto dependency at
# all. If this whole file is ever skipped for a missing library, those still run.


def test_key_info_puts_the_receiver_first() -> None:
	"""The transposition that produces two perfectly good, different keys."""
	ua = E.b64u_decode(UA_PUBLIC)
	as_ = E.b64u_decode(AS_PUBLIC)
	info = E.build_key_info(ua, as_)
	assert info == b"WebPush: info\x00" + ua + as_
	assert info != b"WebPush: info\x00" + as_ + ua


def test_the_two_extractions_use_different_salts() -> None:
	"""Using one salt for both derives a key that is stable, plausible and unreadable."""
	ua, as_ = E.b64u_decode(UA_PUBLIC), E.b64u_decode(AS_PUBLIC)
	salt, auth = E.b64u_decode(SALT), E.b64u_decode(AUTH_SECRET)
	secret = b"\x11" * 32

	correct = E.derive_keys(
		ecdh_secret=secret, auth_secret=auth, ua_public=ua, as_public=as_, salt=salt
	)
	auth_for_both = E.derive_keys(
		ecdh_secret=secret, auth_secret=salt, ua_public=ua, as_public=as_, salt=salt
	)
	assert correct != auth_for_both


def test_the_padding_delimiter_marks_the_last_record() -> None:
	assert E.pad_plaintext(b"abc") == b"abc\x02"


def test_a_payload_that_cannot_fit_is_refused_rather_than_truncated() -> None:
	"""Silently truncating produces a decryptable message with the end missing, which reads as
	a rendering bug and gets investigated in the wrong place."""
	with pytest.raises(ValueError):
		E.pad_plaintext(b"x" * 100, record_size=64)


def test_unpadded_base64url_decodes(  ) -> None:
	"""Browsers hand out ``p256dh`` and ``auth`` without padding, and the stdlib decoder
	raises on those. Getting this wrong breaks every subscription, not some of them."""
	assert len(E.b64u_decode(AUTH_SECRET)) == 16
	assert len(E.b64u_decode(UA_PUBLIC)) == 65
	assert "=" not in E.b64u_encode(b"\x00\x01\x02")
