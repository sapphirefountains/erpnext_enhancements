# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""VAPID (RFC 8292) — who this server is, signed, so a push service will accept its traffic.

Every push request carries ``Authorization: vapid t=<JWT>, k=<public key>``. The JWT is
signed ES256 with a P-256 key this site owns; the push service checks the signature against
the key in ``k``, and the browser's subscription is bound to that same key. It is an identity
claim rather than a secret — it lets a push service rate-limit and contact one sender rather
than the whole internet.

--------------------------------------------------------------------------------------
Where the private key lives, and why it is not on a DocType
--------------------------------------------------------------------------------------

``site_config.json``, read through ``frappe.conf``. That is a deviation from Appendix B,
which specifies a ``Password`` field, and the reason is a rule this repo already enforces:
``tests/test_chat_guardrails.py::test_no_chat_doctype_declares_a_password_field`` refuses a
``Password`` field on **any** chat DocType, with the argument that *"once it is in ``__Auth``
it is in every backup"*.

That guardrail was written about Google credentials under the keyless domain-wide-delegation
design, where the whole point is that there is no key to store. A VAPID key is a different
animal — it is ours, self-generated, and Web Push has no keyless mode; the specification
requires an application server key by construction. So the choice was between weakening an
absolute rule and finding the key a different home, and ``site_config.json`` satisfies the
guardrail's actual stated harm rather than merely evading its wording: it is not in
``__Auth``, so it is not in a database backup at all.

It also matches how this repo already treats server-level secrets — ``api/telephony.py`` and
``api/call_recording_export.py`` both fall back to ``frappe.conf`` for Twilio credentials —
and it makes rotation a deliberate bench action, which is right for a key whose rotation
**invalidates every existing subscription on the site**. Nobody should be able to do that
from a settings form by accident.

The cost, stated: it cannot be set from the desk. :func:`generate` exists to make that one
step short, and it prints rather than writes, because a function that edits
``site_config.json`` is a function that can corrupt it.

--------------------------------------------------------------------------------------
The two ways this fails quietly
--------------------------------------------------------------------------------------

**``aud`` is the origin of the endpoint, not the endpoint.** A JWT whose audience is the full
push URL is rejected by every push service, with a 401 that reads like a signing problem.

**The public key the browser subscribed with must be the key in ``k``.** They are the same
key, and if the site's keypair is ever regenerated, every subscription created under the old
one becomes undeliverable — the push service answers 403, not 410, so the pruning rule does
not clean them up either. :func:`public_key_changed_since` is what lets the subscription
store notice.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

import time
from typing import Final
from urllib.parse import urlsplit

import frappe

from erpnext_enhancements.chat.notifications.webpush import encrypt

#: ``site_config.json`` keys. The private one is named ``..._private_key`` on purpose:
#: ``scripts/check_no_committed_secrets.py`` matches a snake_case ``private_key`` field and
#: would NOT match ``vapidPrivateKey`` or ``VAPID_PRIVATE``, so the name is what buys the
#: committed-secret gate's protection if one ever gets pasted into a file.
CONF_PRIVATE_KEY: Final[str] = "chat_vapid_private_key"
CONF_PUBLIC_KEY: Final[str] = "chat_vapid_public_key"

#: Twelve hours. RFC 8292 permits up to 24; half of it means a token is never near expiry when
#: a retry finally goes out, and re-signing costs microseconds.
TOKEN_LIFETIME_SECONDS: Final[int] = 12 * 60 * 60


class VapidNotConfigured(Exception):
	"""No keypair in ``site_config.json``. Raised so the caller can log once and stop.

	A distinct exception rather than ``None``, because "push is not set up on this site" and
	"push failed for this subscription" want completely different operator responses and are
	otherwise indistinguishable in a log.
	"""


def is_configured() -> bool:
	"""Whether this site can sign a push request at all. Checked before any work is done."""
	return bool(_conf(CONF_PRIVATE_KEY) and _conf(CONF_PUBLIC_KEY))


def public_key() -> str:
	"""The application server key, base64url — what the browser subscribes with.

	Handed to the client and passed to ``pushManager.subscribe({applicationServerKey})``. Not
	a secret: it is published to every browser that subscribes, and it appears in the
	``Authorization`` header of every push request.
	"""
	value = _conf(CONF_PUBLIC_KEY)
	if not value:
		raise VapidNotConfigured(
			f"{CONF_PUBLIC_KEY} is not set in site_config.json. Run "
			"`bench --site <site> execute "
			"erpnext_enhancements.chat.notifications.webpush.vapid.generate` and add the two "
			"lines it prints."
		)
	return value


def authorization_header(endpoint: str, *, subject: str | None = None, now: int | None = None) -> str:
	"""``vapid t=<jwt>, k=<public key>`` for one endpoint.

	One header, not the two of the expired draft: RFC 8292 folds the key into the same
	``Authorization`` value, and a ``Crypto-Key`` header alongside it is at best ignored.
	"""
	token = sign(endpoint, subject=subject, now=now)
	return f"vapid t={token}, k={public_key()}"


def sign(endpoint: str, *, subject: str | None = None, now: int | None = None) -> str:
	"""The ES256 JWT for one endpoint's origin.

	``now`` is injectable so the claim set can be asserted without freezing the clock.
	"""
	private = _conf(CONF_PRIVATE_KEY)
	if not private:
		raise VapidNotConfigured(
			f"{CONF_PRIVATE_KEY} is not set in site_config.json. Web Push cannot be signed, so "
			"nothing is sent; the bell and the in-app counters are unaffected."
		)

	issued = int(time.time()) if now is None else int(now)
	claims = {
		"aud": audience(endpoint),
		"exp": issued + TOKEN_LIFETIME_SECONDS,
		"sub": subject or contact_subject(),
	}

	import jwt
	from cryptography.hazmat.primitives.asymmetric import ec

	key = ec.derive_private_key(int.from_bytes(encrypt.b64u_decode(private), "big"), ec.SECP256R1())
	return jwt.encode(claims, key, algorithm="ES256")


def audience(endpoint: str) -> str:
	"""``scheme://host`` of the endpoint. **Not the endpoint.**

	The single most common VAPID mistake, and its symptom is a 401 that reads like a signing
	failure — which sends everybody to look at the key rather than at the claim.
	"""
	parts = urlsplit((endpoint or "").strip())
	if not parts.scheme or not parts.netloc:
		raise ValueError(f"a push endpoint must be an absolute URL; got {endpoint!r}")
	return f"{parts.scheme}://{parts.netloc}"


def contact_subject() -> str:
	"""The ``mailto:`` a push service operator would use to reach us.

	Not a secret and not an authenticator — it exists so that a push service with a question
	about this site's traffic has somebody to ask before it starts rejecting it. Falls back to
	the site's own support email rather than raising: a missing contact address is not a
	reason to stop notifying people.
	"""
	try:
		configured = (frappe.db.get_single_value("Chat Settings", "push_contact_mailto") or "").strip()
	except Exception:
		configured = ""
	if not configured:
		try:
			configured = (frappe.db.get_single_value("Contact Us Settings", "email_id") or "").strip()
		except Exception:
			configured = ""
	if not configured:
		configured = "admin@example.com"
	return configured if configured.startswith("mailto:") else f"mailto:{configured}"


def public_key_changed_since(recorded: str | None) -> bool:
	"""Whether this site's key differs from the one a subscription was created under.

	Regenerating the keypair invalidates every existing subscription, and the push service
	answers **403** for one signed by a different key — not 410 — so the prune-on-gone rule
	never fires and the dead rows stay forever, each costing a request per message. Recording
	the key per subscription is what makes that detectable instead of mysterious.
	"""
	if not recorded:
		return False
	try:
		return recorded.strip() != public_key()
	except VapidNotConfigured:
		return False


def generate() -> dict[str, str]:
	"""Mint a keypair and **print** the two lines for ``site_config.json``. Never writes.

	Run once per site::

		bench --site <site> execute \\
			erpnext_enhancements.chat.notifications.webpush.vapid.generate

	It does not edit ``site_config.json`` itself, and that is deliberate rather than lazy: a
	function that rewrites the file every bench process reads at startup is one bad
	serialisation away from taking the site down, and this runs once in the lifetime of a
	site.

	**Running it again on a site that already has a key is a destructive act** — every browser
	subscription on the site was bound to the old public key and becomes permanently
	undeliverable. So it refuses when a key is already present and says why.
	"""
	if is_configured():
		raise frappe.ValidationError(
			"This site already has a VAPID keypair. Generating a new one would invalidate "
			"EVERY existing push subscription — browsers bind their subscription to the key "
			"they subscribed with, and the push service answers 403 rather than 410 for the "
			"old ones, so they are never pruned either. If you genuinely mean to rotate, "
			f"remove {CONF_PRIVATE_KEY} and {CONF_PUBLIC_KEY} from site_config.json first and "
			"expect every user to be re-prompted."
		)

	from cryptography.hazmat.primitives.asymmetric import ec

	private = ec.generate_private_key(ec.SECP256R1())
	private_bytes = private.private_numbers().private_value.to_bytes(32, "big")
	public_bytes = encrypt._raw_public_bytes(private.public_key())

	pair = {
		CONF_PRIVATE_KEY: encrypt.b64u_encode(private_bytes),
		CONF_PUBLIC_KEY: encrypt.b64u_encode(public_bytes),
	}

	print("\nAdd these two lines to sites/<site>/site_config.json, then restart the bench:\n")
	for key, value in pair.items():
		print(f'  "{key}": "{value}",')
	print(
		"\nThe private one is a secret and belongs nowhere else — not in the repo, not in a "
		"DocType, not in a ticket. The public one is published to every browser that "
		"subscribes and is not sensitive.\n"
	)
	return pair


def _conf(key: str) -> str:
	"""One ``site_config.json`` value as a stripped string, or ``""``."""
	try:
		return str(frappe.conf.get(key) or "").strip()
	except Exception:
		return ""
