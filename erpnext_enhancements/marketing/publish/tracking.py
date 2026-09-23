# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Tracking tags on a post's link, added as it is sent (TASK-2026-01488, decided 2026-09-22).

A link to our own site goes out with four UTM tags, so a visitor who becomes a Lead carries the
post and the network with them (the website capture writes them onto the Lead's
``custom_utm_*`` fields; ``crm_enhancements/attribution.py``)::

    utm_source=<network>   facebook | instagram | linkedin | youtube
    utm_medium=social      "Social Media Campaign" to derive_lead_source, and not paid
    utm_campaign=<slug>    the post's Campaign, or "organic"
    utm_content=<post>     the Social Post's name, lower case: the join key

The rules, each for a reason:

* **Only our own site** (``OWN_HOSTS``). A tag on somebody else's page tells us nothing and puts
  our tracking into their analytics.
* **A link that already carries any ``utm_`` tag is left exactly as written.** Somebody tagged it
  on purpose, and first touch is theirs to define.
* **Never ``utm_id``.** The ad-spend report reads a ``utm_id`` as a paid click
  (``core/roas.is_paid``), so an organic post that set one would show up there as paid spend it
  cannot join.
* **The author's query string is not re-encoded.** The tags are appended as text, and every tag
  value is a slug of ``[a-z0-9-]``, so there is nothing to encode and the rest of the link is
  byte-for-byte what was approved.
* **Deterministic.** The same post, network and campaign always give the same link, so what the
  composer's preview shows (``public/js/marketing/composer.js::tagged``, the same rule in JS) is
  what goes out. ``tracking_vectors.json`` holds both implementations to the same answers.

Instagram is not tagged: the link is not sent there at all, and a URL in a caption is not a link.

Pure: no frappe.
"""

import re
from urllib.parse import urlsplit

from erpnext_enhancements.marketing.publish import constants as P

#: Our own site. A subdomain counts (www.).
OWN_HOSTS = ("sapphirefountains.com",)
UTM_MEDIUM = "social"
DEFAULT_CAMPAIGN = "organic"
SOURCES = {
	P.NETWORK_FACEBOOK: "facebook",
	P.NETWORK_INSTAGRAM: "instagram",
	P.NETWORK_LINKEDIN: "linkedin",
	P.NETWORK_YOUTUBE: "youtube",
}
#: Networks whose post carries the link (Instagram's does not).
TAGGED_NETWORKS = frozenset({P.NETWORK_FACEBOOK, P.NETWORK_LINKEDIN, P.NETWORK_YOUTUBE})
SLUG_MAX = 60


def slug(text):
	"""``"Spring Launch 2026!"`` -> ``"spring-launch-2026"``. Only ``[a-z0-9-]``."""
	value = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
	return value[:SLUG_MAX].strip("-")


def is_own(link):
	"""Whether ``link`` is an http(s) address on our own site."""
	try:
		parts = urlsplit(str(link or "").strip())
	except ValueError:
		return False
	host = (parts.hostname or "").lower()
	if parts.scheme.lower() not in ("http", "https") or not host:
		return False
	return any(host == own or host.endswith("." + own) for own in OWN_HOSTS)


def has_utm(link):
	query = str(link or "").split("#", 1)[0].partition("?")[2]
	return any(part.split("=", 1)[0].lower().startswith("utm_") for part in query.split("&") if part)


def tagged(link, network, post_name, campaign=None):
	"""``link`` as it goes to ``network``: tagged if it is ours and untagged, else unchanged."""
	text = str(link or "").strip()
	source = SOURCES.get(network)
	content = slug(post_name)
	if not text or network not in TAGGED_NETWORKS or not source or not content:
		return text
	if not is_own(text) or has_utm(text):
		return text
	base, hash_mark, fragment = text.partition("#")
	path, question, query = base.partition("?")
	tags = (
		f"utm_source={source}&utm_medium={UTM_MEDIUM}"
		f"&utm_campaign={slug(campaign) or DEFAULT_CAMPAIGN}&utm_content={content}"
	)
	query = f"{query}&{tags}" if query else tags
	return f"{path}?{query}{hash_mark}{fragment}"


def with_link(text, link, sent_link):
	"""Text carrying the link: the original link swapped for the sent one, or the sent one added.

	If the author wrote the link into the text themselves it is replaced where it stands, so the
	post never carries the link twice.
	"""
	text = text or ""
	original = (link or "").strip()
	sent = (sent_link or "").strip()
	if not sent:
		return text
	if original and original in text:
		return text.replace(original, sent)
	if sent in text:
		return text
	return f"{text}\n\n{sent}" if text else sent


def for_post(post, network):
	"""The link a post sends to ``network``: its own link, tagged by ``tagged``."""
	post = post or {}
	return tagged(post.get("link"), network, post.get("name"), post.get("campaign"))


def post_key(value):
	"""The join key a Lead's ``custom_utm_content`` is compared on: lower case, trimmed."""
	return str(value or "").strip().lower()


def network_of_source(value):
	"""The network a Lead's ``custom_utm_source`` names, or None."""
	wanted = str(value or "").strip().lower()
	return next((network for network, source in SOURCES.items() if source == wanted), None)
