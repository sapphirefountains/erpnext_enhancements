# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""YouTube channel uploads, through YouTube's resumable upload protocol (TASK-2026-01485).

Plain ``requests`` via ``publish/client.py``, checked against Google's official references on
2026-09-22. The protocol gives this publisher what Instagram's container gives Meta's -- a way to
*ask* whether a video exists -- and the two phases fall along it:

* **prepare** opens an upload session: ``POST /upload/youtube/v3/videos?uploadType=resumable`` with
  the title, description, tags, category and privacy. The session URI comes back in ``Location``.
  **No video exists yet** -- one is created only when the last byte arrives -- so nothing here is
  public, and a failure here (a refused title, a used-up quota) is retried or refused freely.
* **send** PUTs the file in 32 MiB chunks (a multiple of 256 KiB, as the protocol requires). A 308
  means "keep going" and says, in ``Range``, how much arrived; the final 201 carries the new video.
  **If a chunk fails** -- a timeout, a 5xx -- the session is asked (``Content-Range: bytes */TOTAL``):
  308 means the upload is incomplete, **so no video exists**, and it resumes from where YouTube got
  to; 200/201 means it had finished, and that response *is* the video. Google documents that a
  finished session answers with its original response, so a resumed upload can never make a second
  video. After ``YOUTUBE_RESUME_ATTEMPTS`` incomplete tries, ``NotPublished`` (safe to retry: the next
  attempt opens a new session, and the abandoned one never becomes a video). Only a session nobody
  can ask becomes Unconfirmed.

After the video exists, the **thumbnail** (``thumbnails.set``, which needs a verified channel) and
the **playlist** (``playlistItems.insert``) are best-effort: a failure is a warning on a success.

**Until the YouTube API audit passes, every video uploaded through the API is locked private,**
whatever ``privacyStatus`` asks. The response says so, and the warning repeats it.

Quota: an upload is 1 call from its own bucket of 100 a day (the task's "1,600 units" dates from
before 2025-12-04); the thumbnail and playlist add cost about 50 units each. The rate limiter
(``ratelimit.admit``) accounts for all three. YouTube reports a spent quota as a 403 or 400 with a
*reason*; those are re-raised as 429 so the job waits for the next quota day instead of failing.
"""

import json
import time

from erpnext_enhancements.marketing.core.client import MarketingAPIError
from erpnext_enhancements.marketing.publish import constants as P
from erpnext_enhancements.marketing.publish import media as M
from erpnext_enhancements.marketing.publish import validation as V
from erpnext_enhancements.marketing.publish.client import NotPublished

UPLOAD = f"https://{P.YOUTUBE_HOST}/upload/youtube/v3"
API = P.YOUTUBE_API_BASE


def _wait_if_quota(exc):
	"""Re-raise a YouTube quota refusal as a 429, so the outbox waits instead of failing."""
	if getattr(exc, "reason", None) in P.YOUTUBE_WAIT_REASONS:
		raise MarketingAPIError(
			P.CONNECTION_YOUTUBE, f"YouTube: {exc.reason}; will retry", status=429
		) from None
	raise exc


def metadata(context):
	"""The video resource to create: snippet and status. Pure."""
	post = context["post"]
	target = context.get("target") or {}
	description = ((target.get("variant_text") or "").strip() or (post.get("body") or "")).strip()
	link = (post.get("link") or "").strip()
	if link and link not in description:
		description = f"{description}\n\n{link}" if description else link
	snippet = {
		"title": (post.get("video_title") or "").strip(),
		"description": description,
		"categoryId": P.YOUTUBE_CATEGORY_ID,
	}
	tags = V.split_tags(post.get("video_tags"))
	if tags:
		snippet["tags"] = tags
	return {
		"snippet": snippet,
		"status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
	}


def next_offset(range_header):
	"""The first byte YouTube does not have yet, from a 308's ``Range: bytes=0-N``. Pure."""
	text = (range_header or "").strip()
	if not text.startswith("bytes="):
		return 0
	try:
		return int(text.split("-")[-1]) + 1
	except ValueError:
		return 0


def _status(transport, session, total):
	"""Ask the session how far it got: ``("incomplete", offset)`` or ``("done", video)``."""
	reply = transport.request(
		"PUT", session, data=b"", headers={"Content-Range": f"bytes */{total}"}, full=True
	)
	if reply.status == 308:
		return "incomplete", next_offset(reply.headers.get("range"))
	return "done", reply.body


def upload(transport, session, source, content_type, sleep=time.sleep):
	"""PUT the file to ``session`` and return the video resource. See the module docstring."""
	total = source.size
	offset = 0
	resumes = 0
	while True:
		end = min(offset + P.YOUTUBE_CHUNK_BYTES, total) - 1
		try:
			reply = transport.request(
				"PUT",
				session,
				data=source.read(offset, end),
				headers={"Content-Type": content_type, "Content-Range": f"bytes {offset}-{end}/{total}"},
				full=True,
				timeout=P.YOUTUBE_CHUNK_TIMEOUT_SECONDS,
			)
		except MarketingAPIError as exc:
			if exc.status is not None and exc.status < 500:
				_wait_if_quota(exc)  # a 4xx is a refusal (or a spent quota); nothing to resume
			# A timeout or 5xx: the chunk may or may not have landed. Ask; never guess.
			try:
				state, value = _status(transport, session, total)
			except MarketingAPIError:
				raise exc from None  # nobody can say: the outbox holds it Unconfirmed
			if state == "done":
				return value
			resumes += 1
			if resumes > P.YOUTUBE_RESUME_ATTEMPTS:
				raise NotPublished(
					P.CONNECTION_YOUTUBE,
					f"upload incomplete after {resumes - 1} resumes ({exc}); no video exists",
				) from None
			offset = value
			sleep(min(2**resumes, 60))
			continue
		if reply.status == 308:
			offset = next_offset(reply.headers.get("range"))
			continue
		return reply.body


def _thumbnail(transport, video_id, context, read):
	asset = (context.get("post") or {}).get("video_thumbnail_asset")
	if not asset:
		return None
	try:
		transport.request(
			"POST",
			f"{UPLOAD}/thumbnails/set",
			params={"videoId": video_id, "uploadType": "media"},
			data=read(asset),
			headers={"Content-Type": (asset.get("mime_type") or "image/jpeg")},
		)
	except (MarketingAPIError, M.MediaNotReachable) as exc:
		return f"Published, but the thumbnail was not set (custom thumbnails need a verified channel): {exc}"
	return None


def _playlist(transport, video_id, context):
	playlist = ((context.get("post") or {}).get("youtube_playlist_id") or "").strip()
	if not playlist:
		return None
	try:
		transport.request(
			"POST",
			f"{API}/playlistItems",
			params={"part": "snippet"},
			json={
				"snippet": {
					"playlistId": playlist,
					"resourceId": {"kind": "youtube#video", "videoId": video_id},
				}
			},
		)
	except MarketingAPIError as exc:
		return f"Published, but not added to playlist {playlist}: {exc}"
	return None


def prepare(context, transport, sleep=time.sleep, source_for=M.byte_source, read=M.read_bytes):
	"""``publishers/__init__.py``'s entry point. Opens the session; send() uploads."""
	videos = [m for m in context.get("media") or [] if (m.get("asset_type") or "") == "Video"]
	if len(videos) != 1:
		raise MarketingAPIError(P.CONNECTION_YOUTUBE, "YouTube needs exactly one video", status=400)
	asset = videos[0]
	try:
		source = source_for(asset)
	except M.MediaNotReachable as exc:
		raise MarketingAPIError(P.CONNECTION_YOUTUBE, str(exc), status=400) from None
	if source.size <= 0:
		raise MarketingAPIError(P.CONNECTION_YOUTUBE, f"{asset.get('name')} is empty", status=400)
	content_type = asset.get("mime_type") or "application/octet-stream"
	try:
		_body, headers = transport.request(
			"POST",
			f"{UPLOAD}/videos",
			params={"uploadType": "resumable", "part": "snippet,status"},
			data=json.dumps(metadata(context)).encode("utf-8"),
			headers={
				"Content-Type": "application/json; charset=UTF-8",
				"X-Upload-Content-Length": str(source.size),
				"X-Upload-Content-Type": content_type,
			},
			with_headers=True,
		)
	except MarketingAPIError as exc:
		_wait_if_quota(exc)
	session = headers.get("location")
	if not session:
		raise MarketingAPIError(P.CONNECTION_YOUTUBE, "YouTube opened no upload session", status=None)

	def send():
		video = upload(transport, session, source, content_type, sleep)
		video_id = video.get("id")
		warnings = []
		if (video.get("status") or {}).get("privacyStatus") == "private":
			warnings.append(
				"YouTube kept the video private: API uploads stay private until the YouTube audit passes"
			)
		if video_id:
			warnings += [
				_thumbnail(transport, video_id, context, read),
				_playlist(transport, video_id, context),
			]
		return {
			"external_post_id": video_id,
			"permalink": f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
			"warning": "; ".join(w for w in warnings if w) or None,
		}

	return send
