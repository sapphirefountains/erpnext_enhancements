# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""LinkedIn Company Page publishing (TASK-2026-01484).

Posts as the organization (``urn:li:organization:{id}``) through the versioned REST API, plain
``requests`` via ``publish/client.py``. Checked against LinkedIn's official references on
2026-09-22. Two ways LinkedIn differs from Meta shape this module:

* **LinkedIn does not fetch media; we upload it.** Each image or document is registered
  (``initializeUpload``), its bytes PUT to the returned upload URL, and the asset read back until it
  is AVAILABLE -- all in prepare(), none of it public. So a *private* file is fine here
  (``media.read_bytes`` reads it from the site's disk), unlike for Meta.
* **LinkedIn does not read a link to make its preview.** A link post is an ``article`` whose title
  we send (Social Post's *Link Title*); with photos or a document, the link rides in the text.

send() is the one public call, ``POST /rest/posts``. The new post's URN comes back in the
``x-restli-id`` header, and may be ``urn:li:share`` or ``urn:li:ugcPost``. **Creating a post is not
idempotent**, so after a timeout or a 5xx the Page's latest posts are read (``q=author``) and the post
looked for by its text. Found is success. *Not found is not proof it did not publish* -- there is no
container to ask, as Instagram has -- so the job stays ambiguous and becomes Unconfirmed.

The text is LinkedIn's "little text" format: reserved characters are escaped so they read as
themselves, except a ``#`` that starts a hashtag, which is left so the hashtag works.

Video is not here yet: LinkedIn's video upload is a multi-part flow of its own, and the pre-approval
check refuses a LinkedIn video until it is. The first comment may be refused -- LinkedIn's docs
disagree on whether ``w_organization_social`` covers comments -- and a refusal is only a warning.
"""

import re
import time
from urllib.parse import quote

from erpnext_enhancements.marketing.core import constants as C
from erpnext_enhancements.marketing.core.client import MarketingAPIError
from erpnext_enhancements.marketing.publish import constants as P
from erpnext_enhancements.marketing.publish import media as M

REST = C.LINKEDIN_REST_BASE
HASHTAG_START = re.compile(r"#\w")
HASHTAG_TEMPLATE = re.compile(r"\{hashtag\|\\?#\|([^}]*)\}")


def little_text(text):
	"""``text`` in LinkedIn's little-text format. Pure.

	Every reserved character is backslash-escaped so it reads as itself, except a ``#`` that
	starts a hashtag (at the start, or after a non-word character, and followed by a word character).
	"""
	out = []
	for i, ch in enumerate(text or ""):
		if ch in P.LINKEDIN_RESERVED:
			if (
				ch == "#"
				and HASHTAG_START.match(text, i)
				and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_"))
			):
				out.append(ch)
				continue
			out.append("\\" + ch)
		else:
			out.append(ch)
	return "".join(out)


def plain(text):
	"""Little text back to what a person reads, for comparing a post with what LinkedIn returns. Pure."""
	text = HASHTAG_TEMPLATE.sub(lambda m: "#" + m.group(1), text or "")
	text = re.sub(r"\\(.)", r"\1", text)
	return " ".join(text.split())


def _org(context):
	return f"urn:li:organization:{context['account']['external_id']}"


def _text(context):
	target = context.get("target") or {}
	return ((target.get("variant_text") or "").strip() or (context["post"].get("body") or "")).strip()


def _upload(transport, kind, owner, payload):
	"""Register one image or document and PUT its bytes. Returns its URN. Nothing public."""
	body = transport.request(
		"POST",
		f"{REST}/{kind}s",
		params={"action": "initializeUpload"},
		json={"initializeUploadRequest": {"owner": owner}},
	)
	value = body.get("value") or {}
	urn, upload_url = value.get(kind), value.get("uploadUrl")
	if not urn or not upload_url:
		raise MarketingAPIError(
			P.CONNECTION_LINKEDIN, f"LinkedIn gave no upload URL for the {kind}", status=None
		)
	transport.request("PUT", upload_url, data=payload, headers={"Content-Type": "application/octet-stream"})
	return urn


def wait_available(transport, kind, urns, sleep=time.sleep):
	"""Poll until every uploaded asset is AVAILABLE; PROCESSING_FAILED is a refusal."""
	pending = list(urns)
	for attempt in range(P.LINKEDIN_ASSET_POLL_ATTEMPTS):
		still = []
		for urn in pending:
			status = transport.request("GET", f"{REST}/{kind}s/{quote(urn, safe='')}").get("status")
			if status == "AVAILABLE":
				continue
			if status == "PROCESSING_FAILED":
				raise MarketingAPIError(
					P.CONNECTION_LINKEDIN, f"LinkedIn could not process the {kind}", status=400
				)
			still.append(urn)
		if not still:
			return
		pending = still
		if attempt + 1 < P.LINKEDIN_ASSET_POLL_ATTEMPTS:
			sleep(P.LINKEDIN_ASSET_POLL_SECONDS)
	raise MarketingAPIError(
		P.CONNECTION_LINKEDIN, f"LinkedIn is still processing the {kind}; will retry", status=None
	)


def _is(item, kind):
	return (item.get("asset_type") or "") == kind


def _content(context, transport, owner, read, sleep):
	"""The post's ``content``, uploading media first. None for a text-only post."""
	media = list(context.get("media") or [])
	post = context["post"]
	if any(_is(m, "Video") for m in media):
		raise MarketingAPIError(
			P.CONNECTION_LINKEDIN, "LinkedIn video posts are not supported yet", status=400
		)

	def load(item):
		try:
			return read(item)
		except M.MediaNotReachable as exc:
			raise MarketingAPIError(P.CONNECTION_LINKEDIN, str(exc), status=400) from None

	documents = [m for m in media if _is(m, "Document")]
	if documents:
		doc = documents[0]
		urn = _upload(transport, "document", owner, load(doc))
		wait_available(transport, "document", [urn], sleep)
		return {"media": {"id": urn, "title": doc.get("title") or doc.get("name") or "Document"}}
	if media:
		urns = [_upload(transport, "image", owner, load(item)) for item in media]
		wait_available(transport, "image", urns, sleep)
		entries = []
		for item, urn in zip(media, urns, strict=True):
			entry = {"id": urn}
			if (item.get("alt_text") or "").strip():
				entry["altText"] = item["alt_text"].strip()[:4086]
			entries.append(entry)
		return {"media": entries[0]} if len(entries) == 1 else {"multiImage": {"images": entries}}
	link = (post.get("link") or "").strip()
	if link:
		article = {"source": link, "title": (post.get("link_title") or "").strip()}
		if (post.get("link_description") or "").strip():
			article["description"] = post["link_description"].strip()
		return {"article": article}
	return None


def _first_comment(transport, owner, urn, context):
	message = ((context.get("target") or {}).get("first_comment") or "").strip()
	if not message:
		return None
	try:
		transport.request(
			"POST",
			f"{REST}/socialActions/{quote(urn, safe='')}/comments",
			json={"actor": owner, "object": urn, "message": {"text": little_text(message)}},
		)
	except MarketingAPIError as exc:
		return f"Published, but the first comment was not posted: {exc}"
	return None


def permalink(urn):
	return f"https://www.linkedin.com/feed/update/{urn}/" if urn else None


def _settle_ambiguous(transport, owner, commentary, context, original):
	"""POST /rest/posts failed ambiguously: look for the post among the Page's latest."""
	try:
		recent = (
			transport.request(
				"GET",
				f"{REST}/posts",
				params={"q": "author", "author": owner, "count": 10, "sortBy": "CREATED"},
				headers={"X-RestLi-Method": "FINDER"},
			).get("elements")
			or []
		)
	except MarketingAPIError:
		raise original from None
	wanted = plain(commentary)
	match = next((p for p in recent if plain(p.get("commentary")) == wanted), None)
	if not match:
		# Absent from the list is not proof it did not publish; a person checks.
		raise original from None
	urn = match.get("id")
	return {
		"external_post_id": urn,
		"permalink": permalink(urn),
		"warning": "; ".join(
			w
			for w in (
				"Published: the request timed out, and the post was found on the Page",
				_first_comment(transport, owner, urn, context),
			)
			if w
		),
	}


def prepare(context, transport, sleep=time.sleep, read=M.read_bytes):
	"""``publishers/__init__.py``'s entry point."""
	owner = _org(context)
	text = _text(context)
	link = (context["post"].get("link") or "").strip()
	if link and context.get("media") and link not in text:
		text = f"{text}\n\n{link}" if text else link  # beside media, the link rides in the text
	commentary = little_text(text)
	body = {
		"author": owner,
		"commentary": commentary,
		"visibility": "PUBLIC",
		"distribution": {
			"feedDistribution": "MAIN_FEED",
			"targetEntities": [],
			"thirdPartyDistributionChannels": [],
		},
		"lifecycleState": "PUBLISHED",
		"isReshareDisabledByAuthor": False,
	}
	content = _content(context, transport, owner, read, sleep)
	if content:
		body["content"] = content

	def send():
		try:
			_body, headers = transport.request("POST", f"{REST}/posts", json=body, with_headers=True)
		except MarketingAPIError as exc:
			if exc.status is None or exc.status >= 500:
				return _settle_ambiguous(transport, owner, commentary, context, exc)
			raise
		urn = headers.get("x-restli-id")
		return {
			"external_post_id": urn,
			"permalink": permalink(urn),
			"warning": _first_comment(transport, owner, urn, context)
			if urn
			else "LinkedIn returned no post ID",
		}

	return send
