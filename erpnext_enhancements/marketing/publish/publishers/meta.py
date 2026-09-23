# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Facebook Page and Instagram publishing, through one Meta Page token (TASK-2026-01483).

Graph API, plain REST (no SDK), every call through ``publish/client.py``'s allowlist. Media goes by
**URL** -- Meta fetches it (``publish/media.py`` makes each asset fetchable). The facts below were
checked against Meta's official references on 2026-09-22.

The contract is ``publishers/__init__.py``'s two phases, and each network splits along the line
Meta draws between *not yet public* and *public*:

**Facebook Page**
  * prepare: each photo is uploaded **unpublished** (``POST /{page}/photos``, ``published=false``);
    nothing is visible. A video needs no preparation.
  * send: the one public step -- ``POST /{page}/feed`` (text, link, or the photos as
    ``attached_media``), or ``POST /{page}/videos`` with ``file_url`` for a video. Meta shows either
    a link preview or photos, not both, so with photos the link goes into the text.

**Instagram** (through the Page it is linked to)
  * prepare: read the live publishing limit (``content_publishing_limit``: Meta's docs say 50 a day
    in one place and 100 in another, so the figure is read, and handed to the rate limiter), then
    build the **container** -- one image; a Reel for a single video (Meta retired feed video on
    2023-11-09); or up to ten children plus a CAROUSEL parent -- and wait until it reads FINISHED.
    Containers are never public and expire unpublished after 24 hours.
  * send: ``POST /{ig-user}/media_publish``. If that times out or returns a 5xx, the container is
    asked whether it published: PUBLISHED means it is live (success, found in recent media);
    FINISHED means it is provably not live (``NotPublished``: the outbox retries). Anything else
    stays ambiguous and becomes Unconfirmed. Meta does not document ``media_publish`` as idempotent,
    so this check, not a resend, is what settles it.

After the public step nothing may raise: the permalink read and the **first comment** (which needs
``pages_manage_engagement`` / ``instagram_manage_comments``, requested since v1.511.0) report a
failure as a ``warning`` on a successful result.

**The link goes out tagged** (TASK-2026-01488): a link to our own site carries UTM tags naming the
post and the network (``publish/tracking.py``). Facebook only: Instagram is not sent the link.
"""

import time

from erpnext_enhancements.marketing.core import constants as C
from erpnext_enhancements.marketing.core.client import MarketingAPIError
from erpnext_enhancements.marketing.publish import constants as P
from erpnext_enhancements.marketing.publish import media as M
from erpnext_enhancements.marketing.publish import tracking
from erpnext_enhancements.marketing.publish.client import NotPublished

GRAPH = C.META_GRAPH_BASE


def _text(context):
	target = context.get("target") or {}
	return ((target.get("variant_text") or "").strip() or (context["post"].get("body") or "")).strip()


def _is_video(item):
	return (item.get("asset_type") or "") == "Video"


def _media_urls(context):
	try:
		return [(item, M.public_url(item)) for item in context.get("media") or []]
	except M.MediaNotReachable as exc:
		# Validation should have stopped this before approval; if it did not, it is a refusal.
		raise MarketingAPIError(P.CONNECTION_META, str(exc), status=400) from None


def _first_comment(transport, object_id, context):
	message = ((context.get("target") or {}).get("first_comment") or "").strip()
	if not message:
		return None
	try:
		transport.request("POST", f"{GRAPH}/{object_id}/comments", data={"message": message})
	except MarketingAPIError as exc:
		return f"Published, but the first comment was not posted: {exc}"
	return None


def _permalink(transport, object_id, field, fallback=None):
	try:
		return (
			transport.request("GET", f"{GRAPH}/{object_id}", params={"fields": field}).get(field) or fallback
		)
	except MarketingAPIError:
		return fallback


def _result(external_id, permalink, *warnings):
	warning = "; ".join(w for w in warnings if w)
	return {"external_post_id": external_id, "permalink": permalink, "warning": warning or None}


# ---------------------------------------------------------------- Facebook


def prepare_facebook(context, transport):
	page = context["account"]["external_id"]
	text = _text(context)
	link = (context["post"].get("link") or "").strip()
	sent = tracking.for_post(context["post"], P.NETWORK_FACEBOOK)
	media = _media_urls(context)

	if media and _is_video(media[0][0]):
		_item, url = media[0]

		def send_video():
			body = transport.request(
				"POST",
				f"{GRAPH}/{page}/videos",
				data={"file_url": url, "description": tracking.with_link(text, link, sent)},
			)
			video_id = str(body.get("id") or body.get("video_id") or "")
			permalink = _permalink(
				transport, video_id, "permalink_url", f"https://www.facebook.com/{video_id}"
			)
			return _result(video_id, permalink, _first_comment(transport, video_id, context))

		return send_video

	photo_ids = []
	for _item, url in media:
		body = transport.request("POST", f"{GRAPH}/{page}/photos", data={"url": url, "published": "false"})
		photo_ids.append(str(body["id"]))

	def send_post():
		data = {}
		if photo_ids:
			data["message"] = tracking.with_link(text, link, sent)
			for index, photo_id in enumerate(photo_ids):
				data[f"attached_media[{index}]"] = f'{{"media_fbid":"{photo_id}"}}'
		else:
			if text:
				# The author's own copy of the link, if any, carries the same tags as the card.
				data["message"] = text.replace(link, sent) if link and link in text else text
			if sent:
				data["link"] = sent
		body = transport.request("POST", f"{GRAPH}/{page}/feed", data=data)
		post_id = str(body.get("id") or "")
		permalink = _permalink(transport, post_id, "permalink_url", f"https://www.facebook.com/{post_id}")
		return _result(post_id, permalink, _first_comment(transport, post_id, context))

	return send_post


# ---------------------------------------------------------------- Instagram


def _instagram_quota(transport, ig_user, account_name):
	"""Read the live limit, hand it to the rate limiter, and refuse (as a 429) if it is used up."""
	from erpnext_enhancements.marketing.publish import ratelimit

	body = transport.request(
		"GET", f"{GRAPH}/{ig_user}/content_publishing_limit", params={"fields": "quota_usage,config"}
	)
	parsed = ratelimit.parse_instagram_limit(body)
	if not parsed:
		return
	used, total = parsed
	try:
		ratelimit.set_instagram_limit(account_name, total)
	except Exception:
		pass  # the limiter is an optimisation; Meta's own answer below is what counts
	if used >= total:
		raise MarketingAPIError(
			P.CONNECTION_META, f"Instagram's {total}-post daily limit is used up ({used} posted)", status=429
		)


def wait_for_containers(transport, container_ids, sleep=time.sleep):
	"""Poll until every container reads FINISHED. Raises on ERROR/EXPIRED or if time runs out."""
	pending = list(container_ids)
	for attempt in range(P.CONTAINER_POLL_ATTEMPTS):
		still = []
		for container in pending:
			body = transport.request("GET", f"{GRAPH}/{container}", params={"fields": "status_code,status"})
			code = body.get("status_code")
			if code in ("FINISHED", "PUBLISHED"):
				continue
			if code in ("ERROR", "EXPIRED"):
				raise MarketingAPIError(
					P.CONNECTION_META,
					f"Instagram could not process the media ({code}: {body.get('status') or 'no detail'})",
					status=400,
				)
			still.append(container)
		if not still:
			return
		pending = still
		if attempt + 1 < P.CONTAINER_POLL_ATTEMPTS:
			sleep(P.CONTAINER_POLL_SECONDS)
	# Still processing: nothing is public, so the outbox may simply try again later.
	raise MarketingAPIError(
		P.CONNECTION_META, "Instagram is still processing the media; will retry", status=None
	)


def _container(transport, ig_user, data):
	return str(transport.request("POST", f"{GRAPH}/{ig_user}/media", data=data)["id"])


def prepare_instagram(context, transport, sleep=time.sleep):
	ig_user = context["account"]["external_id"]
	caption = _text(context)
	media = _media_urls(context)
	if not media:
		raise MarketingAPIError(P.CONNECTION_META, "Instagram needs at least one photo or video", status=400)
	_instagram_quota(transport, ig_user, context["account"].get("name"))

	if len(media) == 1:
		item, url = media[0]
		if _is_video(item):
			data = {"media_type": "REELS", "video_url": url, "caption": caption, "share_to_feed": "true"}
		else:
			data = {"image_url": url, "caption": caption}
		container = _container(transport, ig_user, data)
	else:
		children = []
		for item, url in media:
			if _is_video(item):
				data = {"media_type": "VIDEO", "video_url": url, "is_carousel_item": "true"}
			else:
				data = {"image_url": url, "is_carousel_item": "true"}
			children.append(_container(transport, ig_user, data))
		wait_for_containers(transport, children, sleep)
		container = _container(
			transport, ig_user, {"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption}
		)
	wait_for_containers(transport, [container], sleep)

	def send():
		try:
			body = transport.request(
				"POST", f"{GRAPH}/{ig_user}/media_publish", data={"creation_id": container}
			)
		except MarketingAPIError as exc:
			if getattr(exc, "subcode", None) == P.INSTAGRAM_LIMIT_SUBCODE:
				raise MarketingAPIError(P.CONNECTION_META, str(exc), status=429) from None
			if exc.status is None or exc.status >= 500:
				return _settle_ambiguous_publish(transport, ig_user, container, caption, context, exc)
			raise
		media_id = str(body.get("id") or "")
		permalink = _permalink(transport, media_id, "permalink")
		return _result(media_id, permalink, _first_comment(transport, media_id, context))

	return send


def _settle_ambiguous_publish(transport, ig_user, container, caption, context, original):
	"""media_publish failed ambiguously: ask the container whether it published."""
	try:
		status = transport.request("GET", f"{GRAPH}/{container}", params={"fields": "status_code"}).get(
			"status_code"
		)
	except MarketingAPIError:
		raise original from None
	if status == "FINISHED":
		raise NotPublished(
			P.CONNECTION_META, f"media_publish failed ({original}); the container is not published"
		)
	if status != "PUBLISHED":
		raise original from None
	# It is live. Find it among the account's latest media by its caption.
	try:
		recent = (
			transport.request(
				"GET",
				f"{GRAPH}/{ig_user}/media",
				params={"fields": "id,permalink,caption,timestamp", "limit": 5},
			).get("data")
			or []
		)
	except MarketingAPIError:
		recent = []
	match = next((m for m in recent if (m.get("caption") or "").strip() == caption.strip()), None)
	note = "Published: media_publish timed out, and the container confirmed it went live"
	if not match:
		return _result(None, None, f"{note}, but the post could not be matched to record its link")
	return _result(
		str(match["id"]), match.get("permalink"), note, _first_comment(transport, match["id"], context)
	)


# ---------------------------------------------------------------- the contract


def prepare(context, transport):
	"""``publishers/__init__.py``'s entry point, by the job's network."""
	network = context["job"]["network"]
	if network == P.NETWORK_FACEBOOK:
		return prepare_facebook(context, transport)
	if network == P.NETWORK_INSTAGRAM:
		return prepare_instagram(context, transport)
	raise MarketingAPIError(P.CONNECTION_META, f"not a Meta network: {network}", status=400)
