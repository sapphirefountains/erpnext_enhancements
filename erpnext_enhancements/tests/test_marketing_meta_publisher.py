"""Bench-free test: the Meta publisher and the pre-approval network checks (TASK-2026-01483).

The first code in this app that can put something in public. What these pin:

1. **Nothing public happens in prepare().** Facebook photos go up unpublished and Instagram builds
   containers there; the one public call -- the feed post, the video, ``media_publish`` -- is made
   only by send(), after the outbox has recorded ``dispatched_at``.
2. **A post that went live is never re-sent.** When ``media_publish`` fails ambiguously, the
   container is asked: PUBLISHED is success (found in recent media), FINISHED is provably not live
   (``NotPublished``, safe to retry), anything else stays ambiguous.
3. **Nothing after the public step raises.** A failed first comment or permalink read is a warning
   on a success, never a failure that would make a live post Unconfirmed.
4. **What Instagram and Facebook would refuse is caught before approval,** with Meta's own figures.
5. Every call goes through the publishing allowlist; the allowlist deletes nothing and names no ad
   endpoint.

The Graph API is a scripted fake that records every call. Media URLs are HTTPS, so resolving them
needs no frappe; the suite imports no frappe at all.

Run: python -m unittest erpnext_enhancements.tests.test_marketing_meta_publisher
"""

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.marketing.core import constants as C
from erpnext_enhancements.marketing.core.client import MarketingAPIError
from erpnext_enhancements.marketing.publish import constants as P
from erpnext_enhancements.marketing.publish import media as M
from erpnext_enhancements.marketing.publish import validation as V
from erpnext_enhancements.marketing.publish.client import NotPublished, PublishTransport
from erpnext_enhancements.marketing.publish.publishers import meta, publisher_for

G = C.META_GRAPH_BASE
PAGE, IG = "101", "17841400000000001"


class Response:
	def __init__(self, status, body):
		self.status_code = status
		self._body = body
		self.text = json.dumps(body) if body is not None else ""
		self.headers = {}
		self.url = ""

	def json(self):
		if self._body is None:
			raise ValueError
		return self._body


class Graph:
	"""Scripted Graph API. ``routes`` maps (METHOD, path suffix) to a body, a Response, an
	exception, or a list of those (served in order, last one repeating)."""

	def __init__(self, routes):
		self.routes = routes
		self.calls = []

	def request(self, method, url, **kw):
		self.calls.append((method, url.replace(G, ""), kw.get("data") or kw.get("params") or {}))
		path = url.replace(G, "")
		for (m, suffix), reply in self.routes.items():
			if m == method and path.endswith(suffix):
				if isinstance(reply, list):
					reply = reply.pop(0) if len(reply) > 1 else reply[0]
				if isinstance(reply, Exception):
					raise reply
				return reply if isinstance(reply, Response) else Response(200, reply)
		raise AssertionError(f"unexpected {method} {path}")

	def public(self):
		"""The calls that can make something public."""
		public = []
		for method, path, data in self.calls:
			if method != "POST":
				continue
			if path.endswith("/photos") and data.get("published") == "false":
				continue
			if path.endswith("/media") and path.startswith(f"/{IG}"):
				continue  # containers
			public.append(path)
		return public


def transport(graph):
	return PublishTransport(P.CONNECTION_META, token="T", http=graph, sleep=lambda s: None, max_retries=0)


def photo(name="MMA-1", **kw):
	return {
		"name": name,
		"asset_type": "Image",
		"source": "File",
		"file": f"https://erp.example/files/{name}.jpg",
		"mime_type": "image/jpeg",
		"width": 1080,
		"height": 1350,
		**kw,
	}


def video(name="MMA-9", **kw):
	return {
		"name": name,
		"asset_type": "Video",
		"source": "File",
		"file": f"https://erp.example/files/{name}.mp4",
		"mime_type": "video/mp4",
		"width": 1080,
		"height": 1920,
		"duration_seconds": 30,
		**kw,
	}


def context(network, media=(), body="A new fountain in Park City", link="", first_comment="", variant=""):
	return {
		"job": {"name": "SPJ-1", "network": network},
		"post": {"body": body, "link": link},
		"target": {"variant_text": variant, "first_comment": first_comment},
		"account": {"name": f"SACC-{network}-x", "external_id": PAGE if network == "Facebook" else IG},
		"media": list(media),
	}


QUOTA = {"data": [{"quota_usage": 3, "config": {"quota_total": 50, "quota_duration": 86400}}]}


# ---------------------------------------------------------------- before approval


class ValidationTests(unittest.TestCase):
	def test_facebook(self):
		self.assertEqual(V.problems("Facebook", "Hello", "", []), [])
		self.assertTrue(V.problems("Facebook", "", "", []))
		self.assertEqual(V.problems("Facebook", "", "https://sapphirefountains.com", []), [])
		self.assertTrue(
			any("one video per post" in p for p in V.problems("Facebook", "x", "", [photo(), video()]))
		)
		self.assertTrue(
			any("one video per post" in p for p in V.problems("Facebook", "x", "", [video(), video("v2")]))
		)
		self.assertTrue(
			any("image/webp" in p for p in V.problems("Facebook", "x", "", [photo(mime_type="image/webp")]))
		)
		self.assertEqual(
			V.problems("Facebook", "x", "", [photo(mime_type="", width=0)]), [], "Facebook is permissive"
		)

	def test_instagram_media_rules(self):
		self.assertEqual(V.problems("Instagram", "Hi", "", [photo()]), [])
		self.assertTrue(any("at least one" in p for p in V.problems("Instagram", "Hi", "", [])))
		self.assertTrue(
			any(
				"at most 10" in p
				for p in V.problems("Instagram", "Hi", "", [photo(str(i)) for i in range(11)])
			)
		)
		self.assertTrue(
			any("JPEG" in p for p in V.problems("Instagram", "Hi", "", [photo(mime_type="image/png")]))
		)
		self.assertTrue(
			any("width and height" in p for p in V.problems("Instagram", "Hi", "", [photo(width=None)]))
		)
		self.assertTrue(
			any("0.56:1" in p for p in V.problems("Instagram", "Hi", "", [photo(width=1080, height=1920)]))
		)
		self.assertEqual(
			V.problems("Instagram", "Hi", "", [photo(width=1080, height=1350)]), [], "exactly 4:5"
		)
		self.assertEqual(
			V.problems("Instagram", "Hi", "", [photo(width=1910, height=1000)]), [], "exactly 1.91:1"
		)
		self.assertTrue(
			any("2 s" in p for p in V.problems("Instagram", "Hi", "", [video(duration_seconds=2)]))
		)
		self.assertTrue(
			any("duration" in p for p in V.problems("Instagram", "Hi", "", [video(duration_seconds=None)]))
		)
		self.assertTrue(
			any("MP4 or MOV" in p for p in V.problems("Instagram", "Hi", "", [video(mime_type="video/webm")]))
		)
		self.assertEqual(V.problems("Instagram", "Hi", "", [photo(), video()]), [], "a carousel may mix")

	def test_instagram_caption_rules(self):
		self.assertTrue(any("2200" in p for p in V.problems("Instagram", "x" * 2201, "", [photo()])))
		tags = " ".join(f"#t{i}" for i in range(31))
		self.assertTrue(any("31 hashtags" in p for p in V.problems("Instagram", tags, "", [photo()])))
		self.assertEqual(V.problems("Instagram", " ".join(f"#t{i}" for i in range(30)), "", [photo()]), [])
		people = " ".join(f"@p{i}" for i in range(21))
		self.assertTrue(any("21 @mentions" in p for p in V.problems("Instagram", people, "", [photo()])))
		self.assertEqual(
			V.problems("Instagram", "email me at hi@example.com &#8212; #one", "", [photo()]), []
		)

	def test_unreachable_media_is_caught_before_approval(self):
		private = photo(file="/private/files/a.jpg")
		drive = photo(source="Google Drive", drive_file_id="abc", file=None)
		for network in ("Facebook", "Instagram"):
			self.assertTrue(any("private" in p for p in V.problems(network, "x", "", [private])), network)
			self.assertTrue(any("Drive" in p for p in V.problems(network, "x", "", [drive])), network)

	def test_each_target_is_checked_with_its_own_text(self):
		post = {"body": "#a " * 40, "link": ""}
		targets = [
			{"social_account": "IG", "variant_text": "short and fine"},
			{"social_account": "FB", "variant_text": ""},
		]
		self.assertEqual(V.post_problems(post, targets, [photo()], {"IG": "Instagram", "FB": "Facebook"}), [])
		targets[0]["variant_text"] = ""
		self.assertTrue(V.post_problems(post, targets, [photo()], {"IG": "Instagram", "FB": "Facebook"}))


class MediaTests(unittest.TestCase):
	def test_sources(self):
		self.assertIsNone(M.url_problem(photo()))
		self.assertIsNone(M.url_problem({"source": "File", "file": "/files/x.jpg"}))
		self.assertIn("private", M.url_problem({"source": "File", "file": "/private/files/x.jpg"}))
		self.assertIn("HTTPS", M.url_problem({"source": "File", "file": "http://x/y.jpg"}))
		self.assertIn("Drive", M.url_problem({"source": "Google Drive", "drive_file_id": "abc"}))
		self.assertIsNone(
			M.url_problem({"source": "Google Cloud Storage", "gcs_object": "gs://bucket/a/b.jpg"})
		)
		self.assertIn(
			"bucket/path", M.url_problem({"source": "Google Cloud Storage", "gcs_object": "justabucket"})
		)
		self.assertEqual(M.split_gcs("gs://b/a/c.mp4"), ("b", "a/c.mp4"))
		self.assertEqual(M.public_url(photo()), "https://erp.example/files/MMA-1.jpg")


# ---------------------------------------------------------------- Facebook


class FacebookTests(unittest.TestCase):
	def test_text_and_link(self):
		graph = Graph(
			{("POST", "/feed"): {"id": "101_1"}, ("GET", "/101_1"): {"permalink_url": "https://fb/1"}}
		)
		send = meta.prepare(context("Facebook", link="https://sapphirefountains.com"), transport(graph))
		self.assertEqual(graph.public(), [], "prepare publishes nothing")
		result = send()
		self.assertEqual((result["external_post_id"], result["permalink"]), ("101_1", "https://fb/1"))
		self.assertEqual(
			graph.calls[0][2],
			{"message": "A new fountain in Park City", "link": "https://sapphirefountains.com"},
		)

	def test_photos_go_up_unpublished_then_one_public_post(self):
		graph = Graph(
			{
				("POST", "/photos"): [{"id": "p1"}, {"id": "p2"}],
				("POST", "/feed"): {"id": "101_2"},
				("GET", "/101_2"): {"permalink_url": "https://fb/2"},
			}
		)
		send = meta.prepare(
			context("Facebook", [photo("a"), photo("b")], link="https://x.test"), transport(graph)
		)
		self.assertEqual([c[2]["published"] for c in graph.calls], ["false", "false"])
		self.assertEqual(graph.public(), [])
		send()
		feed = next(c for c in graph.calls if c[1].endswith("/feed"))
		self.assertEqual(feed[2]["attached_media[0]"], '{"media_fbid":"p1"}')
		self.assertEqual(feed[2]["attached_media[1]"], '{"media_fbid":"p2"}')
		self.assertIn("https://x.test", feed[2]["message"], "a link rides in the text beside photos")
		self.assertNotIn("link", feed[2])

	def test_video(self):
		graph = Graph({("POST", "/videos"): {"id": "701"}, ("GET", "/701"): {}})
		result = meta.prepare(context("Facebook", [video()]), transport(graph))()
		self.assertEqual(graph.calls[0][2]["file_url"], "https://erp.example/files/MMA-9.mp4")
		self.assertEqual(
			result["permalink"], "https://www.facebook.com/701", "falls back when Meta gives none"
		)

	def test_first_comment_and_its_failure_is_only_a_warning(self):
		graph = Graph(
			{
				("POST", "/feed"): {"id": "101_3"},
				("GET", "/101_3"): {"permalink_url": "https://fb/3"},
				("POST", "/101_3/comments"): {"id": "901"},
			}
		)
		result = meta.prepare(context("Facebook", first_comment="#fountains"), transport(graph))()
		self.assertEqual(graph.calls[-1][2], {"message": "#fountains"})
		self.assertIsNone(result["warning"])
		graph = Graph(
			{
				("POST", "/feed"): {"id": "101_4"},
				("GET", "/101_4"): {"permalink_url": "https://fb/4"},
				("POST", "/101_4/comments"): Response(403, {"error": {"message": "missing permission"}}),
			}
		)
		result = meta.prepare(context("Facebook", first_comment="#x"), transport(graph))()
		self.assertEqual(result["external_post_id"], "101_4", "the post is live, so it is a success")
		self.assertIn("first comment was not posted", result["warning"])


# ---------------------------------------------------------------- Instagram


def ig_routes(extra=None):
	routes = {
		("GET", "/content_publishing_limit"): QUOTA,
		("POST", f"{IG}/media"): [{"id": "901"}, {"id": "902"}, {"id": "903"}],
		("GET", "/901"): {"status_code": "FINISHED"},
		("GET", "/902"): {"status_code": "FINISHED"},
		("GET", "/903"): {"status_code": "FINISHED"},
		("POST", "/media_publish"): {"id": "801"},
		("GET", "/801"): {"permalink": "https://instagram.com/p/801"},
	}
	routes.update(extra or {})
	return routes


class InstagramTests(unittest.TestCase):
	def test_single_image(self):
		graph = Graph(ig_routes())
		send = meta.prepare(context("Instagram", [photo()]), transport(graph))
		self.assertEqual(graph.public(), [], "a container is not a post")
		container = next(c for c in graph.calls if c[1].endswith(f"{IG}/media"))
		self.assertEqual(
			container[2],
			{"image_url": "https://erp.example/files/MMA-1.jpg", "caption": "A new fountain in Park City"},
		)
		result = send()
		self.assertEqual(
			(result["external_post_id"], result["permalink"]), ("801", "https://instagram.com/p/801")
		)
		self.assertEqual(graph.public(), [f"/{IG}/media_publish"])

	def test_a_single_video_is_a_reel(self):
		graph = Graph(ig_routes())
		meta.prepare(context("Instagram", [video()]), transport(graph))
		container = next(c for c in graph.calls if c[1].endswith(f"{IG}/media"))
		self.assertEqual(container[2]["media_type"], "REELS")
		self.assertEqual(container[2]["share_to_feed"], "true")

	def test_carousel(self):
		graph = Graph(ig_routes())
		send = meta.prepare(context("Instagram", [photo("a"), video("b")]), transport(graph))
		containers = [c[2] for c in graph.calls if c[1].endswith(f"{IG}/media")]
		self.assertEqual(
			containers[0], {"image_url": "https://erp.example/files/a.jpg", "is_carousel_item": "true"}
		)
		self.assertEqual(containers[1]["media_type"], "VIDEO")
		self.assertEqual(containers[2]["media_type"], "CAROUSEL")
		self.assertEqual(containers[2]["children"], "901,902")
		self.assertNotIn("caption", containers[0], "captions go on the parent only")
		send()
		self.assertEqual(graph.calls[-2][2], {"creation_id": "903"})

	def test_the_live_limit_is_read_and_a_used_up_limit_waits(self):
		graph = Graph(
			ig_routes(
				{
					("GET", "/content_publishing_limit"): {
						"data": [{"quota_usage": 50, "config": {"quota_total": 50}}]
					}
				}
			)
		)
		with self.assertRaises(MarketingAPIError) as ctx:
			meta.prepare(context("Instagram", [photo()]), transport(graph))
		self.assertEqual(ctx.exception.status, 429, "the outbox retries a 429 later")
		self.assertFalse(any(c[1].endswith(f"{IG}/media") for c in graph.calls), "no container is built")

	def test_a_container_meta_cannot_process_is_a_refusal(self):
		graph = Graph(ig_routes({("GET", "/901"): {"status_code": "ERROR", "status": "2207026"}}))
		with self.assertRaises(MarketingAPIError) as ctx:
			meta.prepare(context("Instagram", [video()]), transport(graph))
		self.assertEqual(ctx.exception.status, 400)

	def test_still_processing_is_retried_later(self):
		graph = Graph(ig_routes({("GET", "/901"): {"status_code": "IN_PROGRESS"}}))
		with self.assertRaises(MarketingAPIError) as ctx:
			meta.wait_for_containers(transport(graph), ["901"], sleep=lambda s: None)
		self.assertIsNone(ctx.exception.status)
		self.assertEqual(len(graph.calls), P.CONTAINER_POLL_ATTEMPTS)

	def test_ambiguous_publish_that_went_live_is_success(self):
		graph = Graph(
			ig_routes(
				{
					("POST", "/media_publish"): Response(502, {"error": {"message": "Bad Gateway"}}),
					("GET", "/901"): [{"status_code": "FINISHED"}, {"status_code": "PUBLISHED"}],
					("GET", f"{IG}/media"): {
						"data": [
							{"id": "800", "caption": "older", "permalink": "https://ig/m0"},
							{
								"id": "807",
								"caption": "A new fountain in Park City",
								"permalink": "https://ig/m7",
							},
						]
					},
				}
			)
		)
		result = meta.prepare(context("Instagram", [photo()]), transport(graph))()
		self.assertEqual((result["external_post_id"], result["permalink"]), ("807", "https://ig/m7"))
		self.assertIn("confirmed", result["warning"])
		self.assertEqual(graph.public().count(f"/{IG}/media_publish"), 1, "never re-sent")

	def test_ambiguous_publish_that_did_not_go_live_is_not_published(self):
		graph = Graph(
			ig_routes(
				{
					("POST", "/media_publish"): Response(503, {"error": {"message": "unavailable"}}),
					("GET", "/901"): {"status_code": "FINISHED"},
				}
			)
		)
		with self.assertRaises(NotPublished):
			meta.prepare(context("Instagram", [photo()]), transport(graph))()

	def test_ambiguous_publish_nobody_can_settle_stays_ambiguous(self):
		graph = Graph(
			ig_routes(
				{
					("POST", "/media_publish"): Response(502, {"error": {"message": "Bad Gateway"}}),
					("GET", "/901"): [
						{"status_code": "FINISHED"},
						Response(500, {"error": {"message": "down"}}),
					],
				}
			)
		)
		with self.assertRaises(MarketingAPIError) as ctx:
			meta.prepare(context("Instagram", [photo()]), transport(graph))()
		self.assertNotIsInstance(ctx.exception, NotPublished)
		self.assertEqual(ctx.exception.status, 502, "the original ambiguity, for the outbox to hold")

	def test_the_daily_limit_on_publish_is_a_429(self):
		graph = Graph(
			ig_routes(
				{
					("POST", "/media_publish"): Response(
						400,
						{
							"error": {
								"message": "limit",
								"code": 9,
								"error_subcode": P.INSTAGRAM_LIMIT_SUBCODE,
							}
						},
					)
				}
			)
		)
		with self.assertRaises(MarketingAPIError) as ctx:
			meta.prepare(context("Instagram", [photo()]), transport(graph))()
		self.assertEqual(ctx.exception.status, 429)


# ---------------------------------------------------------------- wiring


class WiringTests(unittest.TestCase):
	def test_registered_for_facebook_and_instagram(self):
		self.assertIs(publisher_for("Facebook"), meta)
		self.assertIs(publisher_for("Instagram"), meta)
		self.assertIsNot(publisher_for("LinkedIn"), meta, "LinkedIn has its own publisher (v1.512.0)")

	def test_the_allowlist_deletes_nothing_and_touches_no_ads(self):
		for connection, method, host, pattern in P.PUBLISH_ALLOWLIST:
			self.assertIn(method, ("GET", "POST", "PUT"), pattern.pattern)
			self.assertNotIn("act_", pattern.pattern)
			self.assertEqual(host if connection == P.CONNECTION_META else P.GRAPH_HOST, P.GRAPH_HOST)
			if method == "PUT":
				# Only LinkedIn's upload URLs take a PUT (v1.512.0): bytes, never an edit.
				self.assertEqual(connection, P.CONNECTION_LINKEDIN, pattern.pattern)
				self.assertTrue(
					pattern.pattern.startswith(("^/dms-uploads/", "^/mediaUpload/")), pattern.pattern
				)

	def test_first_comment_permissions_are_requested(self):
		scopes = P.PUBLISH_OAUTH[P.CONNECTION_META]["scopes"]
		self.assertIn("pages_manage_engagement", scopes)
		self.assertIn("instagram_manage_comments", scopes)
		self.assertFalse(set(scopes) & C.SPEND_CAPABLE_SCOPES)


if __name__ == "__main__":
	unittest.main()
