"""Bench-free test: the YouTube uploader and its pre-approval checks (TASK-2026-01485).

What these pin:

1. **Nothing public in prepare().** It opens the resumable session (no video exists until the last
   byte) and uploads nothing.
2. **A failed chunk is settled by asking the session, never by guessing.** 308 resumes from where
   YouTube got to (bytes already received are not resent); 200/201 means it had finished and that
   response is the video; a session nobody can ask stays ambiguous; too many incomplete tries are
   ``NotPublished`` -- no video exists, so a fresh attempt is safe.
3. **A spent quota waits, never fails.** quotaExceeded (403) and uploadLimitExceeded (400) are
   re-raised as 429.
4. **Thumbnail and playlist are best-effort** after the video exists: failures are warnings.
5. **YouTube's limits are checked before approval** (title, description bytes, tags counted
   YouTube's way, one video, thumbnail type, no first comment).

Run: python -m unittest erpnext_enhancements.tests.test_marketing_youtube_publisher
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.marketing.core.client import MarketingAPIError
from erpnext_enhancements.marketing.publish import constants as P
from erpnext_enhancements.marketing.publish import validation as V
from erpnext_enhancements.marketing.publish.client import NotPublished, PublishTransport, PublishViolation
from erpnext_enhancements.marketing.publish.media import ByteSource
from erpnext_enhancements.marketing.publish.publishers import publisher_for
from erpnext_enhancements.marketing.publish.publishers import youtube as Y

SESSION = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=xa298sd"
VIDEO = {"id": "dQw4w9WgXcQ", "status": {"privacyStatus": "public", "uploadStatus": "uploaded"}}


class Response:
	def __init__(self, status, body=None, headers=None):
		self.status_code = status
		self._body = body
		self.text = json.dumps(body) if body is not None else ""
		self.headers = headers or {}
		self.url = ""

	def json(self):
		if self._body is None:
			raise ValueError
		return self._body


class Google:
	"""Scripted YouTube: ``script`` is a list of callables (method, url, kw) -> Response|Exception,
	consumed in order; anything unscripted fails the test."""

	def __init__(self, *script):
		self.script = list(script)
		self.calls = []

	def request(self, method, url, **kw):
		self.calls.append({"method": method, "url": url, **kw})
		if not self.script:
			raise AssertionError(f"unexpected {method} {url}")
		reply = self.script.pop(0)(method, url, kw)
		if isinstance(reply, Exception):
			raise reply
		return reply

	def puts(self):
		return [c for c in self.calls if c["method"] == "PUT"]


def opened(method, url, kw):
	assert method == "POST" and url.endswith("/upload/youtube/v3/videos"), (method, url)
	return Response(200, None, {"Location": SESSION})


def ok(body=None, status=200):
	return lambda m, u, kw: Response(status, body or {})


def chunk(received_to=None, final=None):
	"""A PUT answered 308 (with the range received so far) or, with ``final``, 201."""

	def reply(method, url, kw):
		assert method == "PUT" and url == SESSION, (method, url)
		if final is not None:
			return Response(201, final)
		headers = {"Range": f"bytes=0-{received_to}"} if received_to is not None else {}
		return Response(308, None, headers)

	return reply


def fail(status, reason=None):
	def reply(method, url, kw):
		if status is None:
			return TimeoutError("timed out")
		body = {
			"error": {"code": status, "message": "boom", "errors": [{"reason": reason}] if reason else []}
		}
		return Response(status, body)

	return reply


def transport(api):
	return PublishTransport(P.CONNECTION_YOUTUBE, token="ya29", http=api, sleep=lambda s: None, max_retries=0)


def source(size):
	data = bytes(range(256)) * (size // 256 + 1)
	return ByteSource(size, lambda start, end: data[start : end + 1])


def video_asset(**kw):
	return {
		"name": "MMA-V",
		"asset_type": "Video",
		"source": "File",
		"file": "/private/files/plaza.mp4",
		"mime_type": "video/mp4",
		**kw,
	}


def context(**post):
	base = {"body": "The plaza fountain, start to finish", "link": "", "video_title": "Plaza Fountain Build"}
	base.update(post)
	return {
		"job": {"name": "SPJ-1", "network": "YouTube"},
		"post": base,
		"target": {"variant_text": "", "first_comment": ""},
		"account": {"name": "SACC-YouTube-UC1", "external_id": "UC1"},
		"media": [video_asset()],
	}


def prepare(api, ctx=None, size=10, read=lambda a: b"thumb"):
	return Y.prepare(
		ctx or context(), transport(api), sleep=lambda s: None, source_for=lambda a: source(size), read=read
	)


# ---------------------------------------------------------------- pure


class PureTests(unittest.TestCase):
	def test_metadata(self):
		meta = Y.metadata(
			context(link="https://sapphirefountains.com", video_tags="fountains, water feature")
		)
		self.assertEqual(meta["snippet"]["title"], "Plaza Fountain Build")
		self.assertEqual(
			meta["snippet"]["description"],
			"The plaza fountain, start to finish\n\nhttps://sapphirefountains.com",
		)
		self.assertEqual(meta["snippet"]["tags"], ["fountains", "water feature"])
		self.assertEqual(meta["snippet"]["categoryId"], P.YOUTUBE_CATEGORY_ID)
		self.assertEqual(meta["status"], {"privacyStatus": "public", "selfDeclaredMadeForKids": False})

	def test_next_offset(self):
		self.assertEqual(Y.next_offset("bytes=0-999999"), 1000000)
		self.assertEqual(Y.next_offset(None), 0, "no Range: nothing arrived")
		self.assertEqual(Y.next_offset("garbage"), 0)

	def test_tags_are_counted_youtubes_way(self):
		self.assertEqual(V.tags_length(["a", "b"]), 3, "the comma counts")
		self.assertEqual(V.tags_length(["water feature"]), 15, "a spaced tag counts two quotes")
		self.assertEqual(V.tags_length([]), 0)


# ---------------------------------------------------------------- prepare and send


class UploadTests(unittest.TestCase):
	def test_prepare_opens_a_session_and_uploads_nothing(self):
		api = Google(opened)
		prepare(api, size=10)
		call = api.calls[0]
		self.assertEqual(call["params"], {"uploadType": "resumable", "part": "snippet,status"})
		self.assertEqual(call["headers"]["X-Upload-Content-Length"], "10")
		self.assertEqual(call["headers"]["X-Upload-Content-Type"], "video/mp4")
		self.assertEqual(json.loads(call["data"])["snippet"]["title"], "Plaza Fountain Build")
		self.assertEqual(api.puts(), [], "no byte is sent before the outbox records dispatched_at")

	def test_chunked_upload_to_the_end(self):
		with mock.patch.object(P, "YOUTUBE_CHUNK_BYTES", 4):
			api = Google(opened, chunk(3), chunk(7), chunk(final=VIDEO))
			result = prepare(api, size=10)()
		ranges = [c["headers"]["Content-Range"] for c in api.puts()]
		self.assertEqual(ranges, ["bytes 0-3/10", "bytes 4-7/10", "bytes 8-9/10"])
		self.assertEqual(result["external_post_id"], "dQw4w9WgXcQ")
		self.assertEqual(result["permalink"], "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
		self.assertIsNone(result["warning"])

	def test_a_failed_chunk_resumes_from_where_youtube_got_to(self):
		with mock.patch.object(P, "YOUTUBE_CHUNK_BYTES", 4):
			api = Google(opened, chunk(3), fail(503), chunk(5), chunk(final=VIDEO))
			result = prepare(api, size=10)()
		puts = api.puts()
		self.assertEqual(puts[2]["headers"]["Content-Range"], "bytes */10", "asked, not guessed")
		self.assertEqual(
			puts[3]["headers"]["Content-Range"], "bytes 6-9/10", "resumed at byte 6, nothing resent"
		)
		self.assertEqual(result["external_post_id"], "dQw4w9WgXcQ")

	def test_a_failed_chunk_that_had_in_fact_finished_is_the_video(self):
		api = Google(opened, fail(None), chunk(final=VIDEO))
		result = prepare(api, size=10)()
		self.assertEqual(result["external_post_id"], "dQw4w9WgXcQ")
		self.assertEqual(len(api.puts()), 2, "the status answer was the video: no second upload")

	def test_nobody_can_say_stays_ambiguous(self):
		api = Google(opened, fail(502), fail(None))
		with self.assertRaises(MarketingAPIError) as ctx:
			prepare(api, size=10)()
		self.assertNotIsInstance(ctx.exception, NotPublished)
		self.assertEqual(ctx.exception.status, 502, "the outbox holds it Unconfirmed")

	def test_endless_incomplete_is_not_published(self):
		script = [opened]
		for _ in range(P.YOUTUBE_RESUME_ATTEMPTS + 1):
			script += [fail(503), chunk()]
		api = Google(*script)
		with self.assertRaises(NotPublished):
			prepare(api, size=10)()

	def test_a_refused_chunk_is_a_refusal_and_a_spent_quota_waits(self):
		api = Google(opened, fail(400, "invalidVideoMetadata"))
		with self.assertRaises(MarketingAPIError) as ctx:
			prepare(api, size=10)()
		self.assertEqual(ctx.exception.status, 400)
		api = Google(opened, fail(400, "uploadLimitExceeded"))
		with self.assertRaises(MarketingAPIError) as ctx:
			prepare(api, size=10)()
		self.assertEqual(ctx.exception.status, 429)

	def test_quota_exceeded_when_opening_waits(self):
		api = Google(fail(403, "quotaExceeded"))
		with self.assertRaises(MarketingAPIError) as ctx:
			prepare(api, size=10)
		self.assertEqual(ctx.exception.status, 429)

	def test_thumbnail_and_playlist_and_their_failures(self):
		ctx = context(
			youtube_playlist_id="PLabc_123",
			video_thumbnail_asset={"name": "MMA-T", "asset_type": "Image", "mime_type": "image/png"},
		)
		api = Google(opened, chunk(final=VIDEO), ok(), ok())
		result = prepare(api, ctx, size=10)()
		thumb, playlist = api.calls[2], api.calls[3]
		self.assertTrue(thumb["url"].endswith("/upload/youtube/v3/thumbnails/set"))
		self.assertEqual(thumb["params"], {"videoId": "dQw4w9WgXcQ", "uploadType": "media"})
		self.assertEqual((thumb["data"], thumb["headers"]["Content-Type"]), (b"thumb", "image/png"))
		self.assertEqual(playlist["json"]["snippet"]["playlistId"], "PLabc_123")
		self.assertEqual(
			playlist["json"]["snippet"]["resourceId"], {"kind": "youtube#video", "videoId": "dQw4w9WgXcQ"}
		)
		self.assertIsNone(result["warning"])
		api = Google(opened, chunk(final=VIDEO), fail(403, "forbidden"), fail(404, "playlistNotFound"))
		result = prepare(api, ctx, size=10)()
		self.assertEqual(result["external_post_id"], "dQw4w9WgXcQ", "the video exists: a success")
		self.assertIn("thumbnail was not set", result["warning"])
		self.assertIn("not added to playlist", result["warning"])

	def test_the_audit_lock_is_reported(self):
		locked = {"id": "abc", "status": {"privacyStatus": "private"}}
		api = Google(opened, chunk(final=locked))
		result = prepare(api, size=10)()
		self.assertIn("audit", result["warning"])


# ---------------------------------------------------------------- before approval


class ValidationTests(unittest.TestCase):
	def post(self, **kw):
		return {"video_title": "Plaza Fountain Build", **kw}

	def test_rules(self):
		self.assertEqual(V.problems("YouTube", "A description", "", [video_asset()], post=self.post()), [])
		self.assertTrue(
			any("exactly one video" in p for p in V.problems("YouTube", "x", "", [], post=self.post()))
		)
		self.assertTrue(
			any(
				"exactly one video" in p
				for p in V.problems("YouTube", "x", "", [video_asset(), video_asset()], post=self.post())
			)
		)
		self.assertTrue(
			any("Video Title" in p for p in V.problems("YouTube", "x", "", [video_asset()], post={}))
		)
		self.assertTrue(
			any(
				"101 characters" in p
				for p in V.problems(
					"YouTube", "x", "", [video_asset()], post=self.post(video_title="x" * 101)
				)
			)
		)
		self.assertTrue(
			any("< or >" in p for p in V.problems("YouTube", "a <b>", "", [video_asset()], post=self.post()))
		)
		self.assertTrue(
			any(
				"5000 bytes" in p
				for p in V.problems("YouTube", "é" * 2501, "", [video_asset()], post=self.post())
			)
		)
		self.assertTrue(
			any(
				"tags" in p
				for p in V.problems(
					"YouTube", "x", "", [video_asset()], post=self.post(video_tags=",".join(["tag"] * 200))
				)
			)
		)
		self.assertTrue(
			any(
				"first comment" in p
				for p in V.problems("YouTube", "x", "", [video_asset()], "hi", post=self.post())
			)
		)

	def test_thumbnail_and_playlist(self):
		bad_thumb = {
			"name": "T",
			"asset_type": "Image",
			"mime_type": "image/gif",
			"source": "File",
			"file": "/files/t.gif",
		}
		self.assertTrue(
			any(
				"JPEG or PNG" in p
				for p in V.problems(
					"YouTube", "x", "", [video_asset()], post=self.post(video_thumbnail_asset=bad_thumb)
				)
			)
		)
		self.assertTrue(
			any(
				"Playlist ID" in p
				for p in V.problems(
					"YouTube",
					"x",
					"",
					[video_asset()],
					post=self.post(youtube_playlist_id="https://youtube.com/..."),
				)
			)
		)

	def test_where_the_video_comes_from(self):
		self.assertEqual(
			V.problems("YouTube", "x", "", [video_asset(file="/private/files/v.mp4")], post=self.post()),
			[],
			"private is fine: we upload",
		)
		drive = video_asset(source="Google Drive", drive_file_id="abc", file=None)
		self.assertTrue(any("Drive" in p for p in V.problems("YouTube", "x", "", [drive], post=self.post())))


# ---------------------------------------------------------------- wiring


class WiringTests(unittest.TestCase):
	def test_registered(self):
		self.assertIs(publisher_for("YouTube"), Y)

	def test_put_reaches_only_the_upload_path(self):
		api = Google()
		t = transport(api)
		for url in (
			"https://www.googleapis.com/youtube/v3/videos",
			"https://evil.example/upload/youtube/v3/videos",
			"https://www.googleapis.com/upload/drive/v3/files",
		):
			with self.assertRaises(PublishViolation, msg=url):
				t.request("PUT", url, data=b"x")
		self.assertEqual(api.calls, [])

	def test_redirects_are_never_followed(self):
		api = Google(lambda m, u, kw: Response(200, {}))
		transport(api).request("GET", "https://www.googleapis.com/youtube/v3/channels")
		self.assertIs(api.calls[0]["allow_redirects"], False)


if __name__ == "__main__":
	unittest.main()
