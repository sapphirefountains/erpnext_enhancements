"""Bench-free test: the LinkedIn publisher and its pre-approval checks (TASK-2026-01484).

What these pin:

1. **Nothing public happens in prepare().** Images and documents are registered, their bytes PUT
   to LinkedIn's upload URL and read back until AVAILABLE; the only public calls are the post and
   the first comment, made by send().
2. **The bearer token never leaves LinkedIn.** Every upload PUT goes through the allowlist, which
   admits only LinkedIn's own upload hosts.
3. **A timed-out post is looked for, not re-sent.** Found among the Page's latest posts (matched by
   its text, with LinkedIn's hashtag template undone) is success; not found stays ambiguous,
   because absence is not proof -- there is no container to ask, as on Instagram.
4. **The text is escaped for LinkedIn's little-text format** without breaking hashtags.
5. **What LinkedIn would refuse is caught before approval**: length, image count and type,
   documents alone, a link post's title, and (for now) video.

Run: python -m unittest erpnext_enhancements.tests.test_marketing_linkedin_publisher
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
from erpnext_enhancements.marketing.publish.client import PublishTransport, PublishViolation
from erpnext_enhancements.marketing.publish.publishers import linkedin as L
from erpnext_enhancements.marketing.publish.publishers import publisher_for

R = C.LINKEDIN_REST_BASE
ORG = "urn:li:organization:2414183"
UPLOAD = "https://www.linkedin.com/dms-uploads/sp/v2/D4E10AQ/upload"


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


class LinkedIn:
	"""Scripted API: routes map (METHOD, url-or-suffix) to a reply or a list of replies."""

	def __init__(self, routes):
		self.routes = routes
		self.calls = []

	def request(self, method, url, **kw):
		self.calls.append({"method": method, "url": url, **kw})
		for (m, key), reply in self.routes.items():
			if m == method and (url == key or url.split("?")[0].endswith(key)):
				if isinstance(reply, list):
					reply = reply.pop(0) if len(reply) > 1 else reply[0]
				if isinstance(reply, Exception):
					raise reply
				return reply if isinstance(reply, Response) else Response(200, reply)
		raise AssertionError(f"unexpected {method} {url}")

	def public(self):
		return [
			c["url"]
			for c in self.calls
			if c["method"] == "POST" and ("/posts" in c["url"] or "/comments" in c["url"])
		]


def transport(api):
	return PublishTransport(
		P.CONNECTION_LINKEDIN,
		token="AQV",
		http=api,
		sleep=lambda s: None,
		max_retries=0,
		headers={"LinkedIn-Version": C.LINKEDIN_API_VERSION, "X-Restli-Protocol-Version": "2.0.0"},
	)


def image(name="MMA-1", **kw):
	return {
		"name": name,
		"title": name,
		"asset_type": "Image",
		"source": "File",
		"file": f"/private/files/{name}.jpg",
		"mime_type": "image/jpeg",
		"width": 1200,
		"height": 627,
		**kw,
	}


def document(name="MMA-D", **kw):
	return {
		"name": name,
		"title": "Fountain spec sheet",
		"asset_type": "Document",
		"source": "File",
		"file": f"/files/{name}.pdf",
		"mime_type": "application/pdf",
		**kw,
	}


def context(media=(), body="New fountain at the plaza #fountains", link="", link_title="", first_comment=""):
	return {
		"job": {"name": "SPJ-1", "network": "LinkedIn"},
		"post": {"body": body, "link": link, "link_title": link_title, "link_description": ""},
		"target": {"variant_text": "", "first_comment": first_comment},
		"account": {"name": "SACC-LinkedIn-2414183", "external_id": "2414183"},
		"media": list(media),
	}


def read(item):
	return f"bytes-of-{item['name']}".encode()


def routes(**extra):
	base = {
		("POST", f"{R}/images"): [
			{"value": {"image": f"urn:li:image:IMG{i}", "uploadUrl": UPLOAD}} for i in range(1, 4)
		],
		("POST", f"{R}/documents"): {"value": {"document": "urn:li:document:DOC1", "uploadUrl": UPLOAD}},
		("PUT", UPLOAD): Response(201),
		("GET", "urn%3Ali%3Aimage%3AIMG1"): {"status": "AVAILABLE"},
		("GET", "urn%3Ali%3Aimage%3AIMG2"): {"status": "AVAILABLE"},
		("GET", "urn%3Ali%3Adocument%3ADOC1"): {"status": "AVAILABLE"},
		("POST", f"{R}/posts"): Response(201, None, {"x-restli-id": "urn:li:share:7100000000000000001"}),
		("POST", "/comments"): Response(201, None, {"x-restli-id": "c1"}),
	}
	base.update(extra.get("override") or {})
	return base


def posted(api):
	return next(c for c in api.calls if c["method"] == "POST" and c["url"] == f"{R}/posts")["json"]


# ---------------------------------------------------------------- little text


class LittleTextTests(unittest.TestCase):
	def test_reserved_characters_are_escaped(self):
		self.assertEqual(L.little_text("Call (801) 555-0100 [today]"), "Call \\(801\\) 555-0100 \\[today\\]")
		self.assertEqual(
			L.little_text("a_b *c* ~d~ <e> {f} |g| @h \\i"),
			"a\\_b \\*c\\* \\~d\\~ \\<e\\> \\{f\\} \\|g\\| \\@h \\\\i",
		)

	def test_hashtags_survive(self):
		self.assertEqual(L.little_text("#fountains are #great"), "#fountains are #great")
		self.assertEqual(
			L.little_text("Learn C# now"), "Learn C\\# now", "a # inside a word is not a hashtag"
		)
		self.assertEqual(L.little_text("# alone"), "\\# alone")
		self.assertEqual(L.little_text("(#tag)"), "\\(#tag\\)")

	def test_plain_undoes_it_and_linkedins_hashtag_template(self):
		original = "Call (801) today #fountains"
		self.assertEqual(L.plain(L.little_text(original)), original)
		self.assertEqual(L.plain("Call \\(801\\) today {hashtag|\\#|fountains}"), original)


# ---------------------------------------------------------------- the publisher


class PublishTests(unittest.TestCase):
	def test_text_post(self):
		api = LinkedIn(routes())
		send = L.prepare(context(), transport(api), read=read, sleep=lambda s: None)
		self.assertEqual(api.public(), [])
		result = send()
		body = posted(api)
		self.assertEqual(body["author"], ORG)
		self.assertEqual(body["visibility"], "PUBLIC")
		self.assertEqual(body["lifecycleState"], "PUBLISHED")
		self.assertEqual(body["distribution"]["feedDistribution"], "MAIN_FEED")
		self.assertNotIn("content", body)
		self.assertEqual(result["external_post_id"], "urn:li:share:7100000000000000001")
		self.assertEqual(
			result["permalink"], "https://www.linkedin.com/feed/update/urn:li:share:7100000000000000001/"
		)

	def test_link_post_is_an_article_with_our_title(self):
		api = LinkedIn(routes())
		L.prepare(
			context(link="https://sapphirefountains.com/work", link_title="Plaza fountain"),
			transport(api),
			read=read,
			sleep=lambda s: None,
		)()
		self.assertEqual(
			posted(api)["content"],
			{"article": {"source": "https://sapphirefountains.com/work", "title": "Plaza fountain"}},
		)

	def test_single_image_is_uploaded_before_anything_public(self):
		api = LinkedIn(routes())
		send = L.prepare(
			context([image(alt_text="The plaza fountain at dusk")], link="https://x.test"),
			transport(api),
			read=read,
			sleep=lambda s: None,
		)
		init, put = api.calls[0], api.calls[1]
		self.assertEqual(init["params"], {"action": "initializeUpload"})
		self.assertEqual(init["json"], {"initializeUploadRequest": {"owner": ORG}})
		self.assertEqual((put["method"], put["url"], put["data"]), ("PUT", UPLOAD, b"bytes-of-MMA-1"))
		self.assertEqual(
			put["headers"]["Authorization"], "Bearer AQV", "LinkedIn wants the token on image uploads"
		)
		self.assertEqual(api.public(), [])
		send()
		body = posted(api)
		self.assertEqual(
			body["content"], {"media": {"id": "urn:li:image:IMG1", "altText": "The plaza fountain at dusk"}}
		)
		self.assertIn(
			"https://x.test", L.plain(body["commentary"]), "beside media, the link rides in the text"
		)

	def test_multi_image(self):
		api = LinkedIn(routes())
		L.prepare(context([image("a"), image("b")]), transport(api), read=read, sleep=lambda s: None)()
		images = posted(api)["content"]["multiImage"]["images"]
		self.assertEqual([i["id"] for i in images], ["urn:li:image:IMG1", "urn:li:image:IMG2"])

	def test_document(self):
		api = LinkedIn(routes())
		L.prepare(context([document()]), transport(api), read=read, sleep=lambda s: None)()
		self.assertEqual(
			posted(api)["content"], {"media": {"id": "urn:li:document:DOC1", "title": "Fountain spec sheet"}}
		)

	def test_video_is_refused_for_now(self):
		api = LinkedIn(routes())
		with self.assertRaises(MarketingAPIError) as ctx:
			L.prepare(
				context([{"name": "v", "asset_type": "Video", "source": "File", "file": "/files/v.mp4"}]),
				transport(api),
				read=read,
				sleep=lambda s: None,
			)
		self.assertEqual(ctx.exception.status, 400)
		self.assertEqual(api.calls, [])

	def test_processing_failed_is_a_refusal_and_slow_processing_retries(self):
		api = LinkedIn(routes(override={("GET", "urn%3Ali%3Aimage%3AIMG1"): {"status": "PROCESSING_FAILED"}}))
		with self.assertRaises(MarketingAPIError) as ctx:
			L.prepare(context([image()]), transport(api), read=read, sleep=lambda s: None)
		self.assertEqual(ctx.exception.status, 400)
		api = LinkedIn(routes(override={("GET", "urn%3Ali%3Aimage%3AIMG1"): {"status": "PROCESSING"}}))
		with self.assertRaises(MarketingAPIError) as ctx:
			L.prepare(context([image()]), transport(api), read=read, sleep=lambda s: None)
		self.assertIsNone(ctx.exception.status)

	def test_first_comment_and_its_refusal_is_a_warning(self):
		api = LinkedIn(routes())
		result = L.prepare(
			context(first_comment="More at (link)"), transport(api), read=read, sleep=lambda s: None
		)()
		comment = api.calls[-1]
		self.assertIn("urn%3Ali%3Ashare%3A7100000000000000001/comments", comment["url"])
		self.assertEqual(comment["json"]["actor"], ORG)
		self.assertEqual(comment["json"]["message"]["text"], "More at \\(link\\)")
		self.assertIsNone(result["warning"])
		api = LinkedIn(
			routes(override={("POST", "/comments"): Response(403, {"message": "Not enough permissions"})})
		)
		result = L.prepare(context(first_comment="x"), transport(api), read=read, sleep=lambda s: None)()
		self.assertTrue(result["external_post_id"])
		self.assertIn("first comment was not posted", result["warning"])


class AmbiguityTests(unittest.TestCase):
	def test_found_among_recent_posts_is_success(self):
		api = LinkedIn(
			routes(
				override={
					("POST", f"{R}/posts"): Response(504, {"message": "Gateway Timeout"}),
					("GET", f"{R}/posts"): {
						"elements": [
							{"id": "urn:li:share:1", "commentary": "something else"},
							{
								"id": "urn:li:ugcPost:2",
								"commentary": "New fountain at the plaza {hashtag|\\#|fountains}",
							},
						]
					},
				}
			)
		)
		result = L.prepare(context(), transport(api), read=read, sleep=lambda s: None)()
		self.assertEqual(result["external_post_id"], "urn:li:ugcPost:2")
		self.assertIn("found on the Page", result["warning"])
		finder = next(c for c in api.calls if c["method"] == "GET" and c["url"] == f"{R}/posts")
		self.assertEqual(finder["headers"]["X-RestLi-Method"], "FINDER")
		self.assertEqual(finder["params"]["author"], ORG)
		self.assertEqual(api.public().count(f"{R}/posts"), 1, "never re-sent")

	def test_not_found_stays_ambiguous(self):
		api = LinkedIn(
			routes(
				override={
					("POST", f"{R}/posts"): Response(502, {"message": "Bad Gateway"}),
					("GET", f"{R}/posts"): {"elements": []},
				}
			)
		)
		with self.assertRaises(MarketingAPIError) as ctx:
			L.prepare(context(), transport(api), read=read, sleep=lambda s: None)()
		self.assertEqual(ctx.exception.status, 502, "absence is not proof: the outbox holds it Unconfirmed")

	def test_a_refusal_is_not_looked_for(self):
		api = LinkedIn(
			routes(override={("POST", f"{R}/posts"): Response(422, {"message": "FIELD_LENGTH_TOO_LONG"})})
		)
		with self.assertRaises(MarketingAPIError) as ctx:
			L.prepare(context(), transport(api), read=read, sleep=lambda s: None)()
		self.assertEqual(ctx.exception.status, 422)
		self.assertFalse(any(c["method"] == "GET" and c["url"] == f"{R}/posts" for c in api.calls))


# ---------------------------------------------------------------- before approval


class ValidationTests(unittest.TestCase):
	def test_rules(self):
		ok = V.problems("LinkedIn", "Hello", "", [image()])
		self.assertEqual(ok, [], "a private file is fine: we upload the bytes")
		self.assertTrue(any("3000" in p for p in V.problems("LinkedIn", "x" * 3001, "", [])))
		self.assertTrue(
			any(
				"not supported yet" in p
				for p in V.problems(
					"LinkedIn",
					"x",
					"",
					[{"name": "v", "asset_type": "Video", "source": "File", "file": "/files/v.mp4"}],
				)
			)
		)
		self.assertTrue(
			any("on its own" in p for p in V.problems("LinkedIn", "x", "", [document(), image()]))
		)
		self.assertTrue(
			any(
				"PDF, PowerPoint or Word" in p
				for p in V.problems("LinkedIn", "x", "", [document(mime_type="text/plain")])
			)
		)
		self.assertTrue(
			any(
				"JPG, PNG or GIF" in p
				for p in V.problems("LinkedIn", "x", "", [image(mime_type="image/webp")])
			)
		)
		self.assertTrue(
			any(
				"at most 20" in p for p in V.problems("LinkedIn", "x", "", [image(str(i)) for i in range(21)])
			)
		)
		self.assertTrue(
			any("pixels" in p for p in V.problems("LinkedIn", "x", "", [image(width=10000, height=4000)]))
		)

	def test_a_link_post_needs_our_title(self):
		self.assertTrue(any("Link Title" in p for p in V.problems("LinkedIn", "x", "https://x.test", [])))
		self.assertEqual(V.problems("LinkedIn", "x", "https://x.test", [], link_title="A fountain"), [])
		self.assertEqual(
			V.problems("LinkedIn", "x", "https://x.test", [image()]), [], "with media, no title needed"
		)

	def test_where_the_bytes_come_from(self):
		self.assertIsNone(M.bytes_problem(image()))
		self.assertIsNone(M.bytes_problem({"source": "File", "file": "/files/a.jpg"}))
		self.assertIn(
			"not a file on this site",
			M.bytes_problem({"source": "File", "file": "https://elsewhere.test/a.jpg"}),
		)
		self.assertIn("Drive", M.bytes_problem({"source": "Google Drive", "drive_file_id": "x"}))
		self.assertIsNone(M.bytes_problem({"source": "Google Cloud Storage", "gcs_object": "b/a.jpg"}))

	def test_documents_are_linkedin_only(self):
		for network in ("Facebook", "Instagram"):
			self.assertTrue(
				any("only LinkedIn" in p for p in V.problems(network, "x", "", [document()])), network
			)

	def test_post_problems_pass_the_link_title(self):
		post = {"body": "x", "link": "https://x.test", "link_title": "A fountain"}
		self.assertEqual(V.post_problems(post, [{"social_account": "LI"}], [], {"LI": "LinkedIn"}), [])


# ---------------------------------------------------------------- wiring and safety


class WiringTests(unittest.TestCase):
	def test_registered(self):
		self.assertIs(publisher_for("LinkedIn"), L)
		self.assertIsNone(publisher_for("YouTube"))

	def test_the_token_never_leaves_linkedin(self):
		api = LinkedIn({})
		t = transport(api)
		for url in (
			"https://evil.example/dms-uploads/x",
			"http://www.linkedin.com/dms-uploads/x",
			"https://www.linkedin.com/feed/",
			"https://api.linkedin.com/rest/posts",
		):
			with self.assertRaises(PublishViolation, msg=url):
				t.request("PUT", url, data=b"x")
		self.assertEqual(api.calls, [], "refused before sending")

	def test_linkedin_paths_name_no_ads(self):
		for connection, _method, _host, pattern in P.PUBLISH_ALLOWLIST:
			if connection == P.CONNECTION_LINKEDIN:
				for needle in ("adAccounts", "adCampaigns", "adAnalytics", "sponsored"):
					self.assertNotIn(needle, pattern.pattern)


if __name__ == "__main__":
	unittest.main()
