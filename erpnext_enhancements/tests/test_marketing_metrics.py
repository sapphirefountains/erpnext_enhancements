"""Bench-free test: engagement pull-back, link tagging and post attribution (TASK-2026-01488).

What these pin:

1. **Links to our own site go out tagged**, identically in Python and in the composer's JS
   (``tracking_vectors.json``), never with ``utm_id`` (the ad-spend report reads that as paid),
   and with ``utm_medium=social`` (which lead-source derivation reads as social, not paid).
   Each publisher sends the tagged link; the pre-approval checks count it.
2. **A metric row is a lifetime total as of its date.** Lifetime networks write today's row;
   YouTube's days are summed from publishing, and only the trailing window is rewritten.
3. **The cursor moves only on a clean pull**, a failure is recorded on the job, and one refused
   credential stops that network for the run instead of failing every post.
4. **The parsers read what the networks send** (recorded shapes), using the metrics that still
   exist in 2026: no ``post_impressions``, no Instagram ``impressions``.
5. **Every read is on the allowlist, and every one is a GET.**
6. **Posts join leads and revenue on their tags**, per network, with ``roas``'s window and revenue.

Pure modules plus AST and file reads; no frappe stub.

Run: python -m unittest erpnext_enhancements.tests.test_marketing_metrics
"""

import ast
import datetime
import json
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.marketing.core import roas
from erpnext_enhancements.marketing.core.client import MarketingAPIError
from erpnext_enhancements.marketing.publish import constants as P
from erpnext_enhancements.marketing.publish import insights as I
from erpnext_enhancements.marketing.publish import metrics as MX
from erpnext_enhancements.marketing.publish import performance as PF
from erpnext_enhancements.marketing.publish import tracking as T
from erpnext_enhancements.marketing.publish import validation as V
from erpnext_enhancements.marketing.publish.client import allowed
from erpnext_enhancements.marketing.publish.publishers import linkedin as L
from erpnext_enhancements.marketing.publish.publishers import meta
from erpnext_enhancements.marketing.publish.publishers import youtube as Y

APP = REPO_ROOT / "erpnext_enhancements"
PUBLISH = APP / "marketing" / "publish"
DOCTYPES = APP / "marketing" / "doctype"
TODAY = datetime.date(2026, 9, 22)
NOW = datetime.datetime(2026, 9, 22, 3, 50)
LINK = "https://www.sapphirefountains.com/plaza"
POST = {"name": "SPOST-00012", "campaign": "Spring Launch", "link": LINK, "body": "The plaza fountain"}


def tag(network):
	return f"{LINK}?utm_source={network}&utm_medium=social&utm_campaign=spring-launch&utm_content=spost-00012"


def attribution_constant(name):
	"""A frozenset from crm_enhancements/attribution.py, read without importing frappe."""
	tree = ast.parse((APP / "crm_enhancements" / "attribution.py").read_text(encoding="utf-8"))
	node = next(n for n in tree.body if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == name)
	return frozenset(ast.literal_eval(node.value.args[0]))


class FakeTransport:
	"""Answers by (method, substring of the URL); records every call."""

	def __init__(self, answers):
		self.answers = answers
		self.calls = []

	def request(self, method, url, params=None, json=None, data=None, headers=None, with_headers=False, **kw):
		self.calls.append({"method": method, "url": url, "params": params, "json": json, "data": data})
		for (verb, needle), value in self.answers.items():
			if verb == method and needle in url:
				body = value() if callable(value) else value
				return (body, {"x-restli-id": "urn:li:share:7"}) if with_headers else body
		raise AssertionError(f"unexpected {method} {url}")


# ---------------------------------------------------------------- 1. tagging


class TrackingTests(unittest.TestCase):
	def test_the_shared_vectors(self):
		vectors = json.loads((PUBLISH / "tracking_vectors.json").read_text(encoding="utf-8"))
		self.assertGreaterEqual(len(vectors["tagged"]), 10)
		for case in vectors["tagged"]:
			self.assertEqual(
				T.tagged(case["link"], case["network"], case["post"], case["campaign"]),
				case["out"],
				case["why"],
			)
		for case in vectors["with_link"]:
			self.assertEqual(T.with_link(case["text"], case["link"], case["sent"]), case["out"], case["why"])
		self.assertFalse(any("utm_id" in case["out"] for case in vectors["tagged"]), "never utm_id")

	def test_a_tagged_click_is_social_not_paid(self):
		query = dict(part.split("=") for part in tag("facebook").split("?")[1].split("&"))
		lead = {f"custom_{k}": v for k, v in query.items()}
		self.assertFalse(
			roas.is_paid(lead, attribution_constant("PAID_MEDIUMS")), "no spend to set it against"
		)
		self.assertIn(T.UTM_MEDIUM, attribution_constant("SOCIAL_MEDIUMS"))

	def test_the_join_keys(self):
		self.assertEqual(T.post_key("  SPOST-00012 "), "spost-00012")
		self.assertEqual(T.slug("SPOST-00012"), "spost-00012")
		self.assertEqual(T.network_of_source("LinkedIn"), P.NETWORK_LINKEDIN)
		self.assertIsNone(T.network_of_source("newsletter"))
		self.assertEqual(T.for_post(POST, P.NETWORK_YOUTUBE), tag("youtube"))
		self.assertEqual(T.for_post({"link": LINK}, P.NETWORK_YOUTUBE), LINK, "no post name: untouched")

	def test_the_js_twin_names_the_same_hosts_and_sources(self):
		js = (APP / "public" / "js" / "marketing" / "composer.js").read_text(encoding="utf-8")
		self.assertIn(f"const OWN_HOSTS = {json.dumps(list(T.OWN_HOSTS))};", js)
		for network, source in T.SOURCES.items():
			self.assertIn(f'{network}: "{source}"', js)


class PublisherTagTests(unittest.TestCase):
	def context(self, network, media=(), body="The plaza fountain", external_id="101"):
		return {
			"job": {"name": "SPJ-1", "network": network},
			"post": {**POST, "body": body},
			"target": {"variant_text": "", "first_comment": ""},
			"account": {"name": f"SACC-{network}-x", "external_id": external_id},
			"media": list(media),
		}

	def test_facebook_sends_the_card_and_its_own_copy_tagged(self):
		graph = FakeTransport(
			{("POST", "/feed"): {"id": "101_5"}, ("GET", "/101_5"): {"permalink_url": "https://fb/5"}}
		)
		send = meta.prepare(self.context("Facebook", body=f"See {LINK} today"), graph)
		send()
		feed = next(c for c in graph.calls if c["url"].endswith("/feed"))
		self.assertEqual(feed["data"]["link"], tag("facebook"))
		self.assertEqual(feed["data"]["message"], f"See {tag('facebook')} today")

	def test_linkedin_card_and_text_beside_media(self):
		article = L._content(self.context("LinkedIn"), None, "urn:li:organization:9", None, None)
		self.assertEqual(article["article"]["source"], tag("linkedin"))
		api = FakeTransport(
			{
				("POST", "/rest/images"): {
					"value": {
						"image": "urn:li:image:A",
						"uploadUrl": "https://www.linkedin.com/dms-uploads/x",
					}
				},
				("PUT", "dms-uploads"): {},
				("GET", "/rest/images/"): {"status": "AVAILABLE"},
				("POST", "/rest/posts"): {},
			}
		)
		photo = {"name": "MMA-1", "asset_type": "Image", "source": "File", "file": "/private/files/a.jpg"}
		send = L.prepare(
			self.context("LinkedIn", media=[photo]), api, sleep=lambda s: None, read=lambda a: b"x"
		)
		send()
		posted = next(c for c in api.calls if c["url"].endswith("/rest/posts") and c["method"] == "POST")
		self.assertTrue(L.plain(posted["json"]["commentary"]).endswith(tag("linkedin")))

	def test_youtube_description_ends_with_the_tagged_link(self):
		meta_ = Y.metadata(self.context("YouTube"))
		self.assertEqual(meta_["snippet"]["description"], f"The plaza fountain\n\n{tag('youtube')}")

	def test_the_checks_count_the_link_as_sent(self):
		untagged = f"\n\n{LINK}"
		room = P.YOUTUBE_DESCRIPTION_MAX_BYTES - len(untagged.encode())
		video = {
			"name": "V",
			"asset_type": "Video",
			"source": "File",
			"file": "/files/v.mp4",
			"mime_type": "video/mp4",
		}
		post = {"video_title": "Plaza", "link": LINK}
		self.assertFalse(
			any("description" in p for p in V.youtube_problems("x" * room, LINK, [video], post=post)),
			"exactly at the limit with the plain link",
		)
		self.assertTrue(
			any(
				"description" in p
				for p in V.youtube_problems("x" * room, LINK, [video], post={**post, **POST})
			),
			"the tags push it over, and the check says so before approval",
		)
		photo = {"name": "P", "asset_type": "Image", "mime_type": "image/jpeg", "width": 1200, "height": 627}
		room = P.LINKEDIN_COMMENTARY_MAX - len(untagged)
		self.assertTrue(
			any("characters" in p for p in V.linkedin_problems("x" * room, LINK, [photo], post=POST)),
			"LinkedIn counts the tagged link it will carry beside media",
		)


# ---------------------------------------------------------------- 2-3. the engine


class MemoryStore:
	def __init__(self):
		self.rows = {}
		self.done = {}
		self.failed = {}

	def upsert(self, job, row):
		self.rows[(job["name"], row["metric_date"])] = row

	def mark_done(self, name, through, now):
		self.done[name] = through

	def mark_failed(self, name, message, now):
		self.failed[name] = message


def job(name="SPJ-1", network="Facebook", published=TODAY - datetime.timedelta(days=5), **kw):
	return {
		"name": name,
		"network": network,
		"state": "Published",
		"external_post_id": "101_5",
		"published_at": datetime.datetime.combine(published, datetime.time(9, 0)),
		"metrics_through": None,
		**kw,
	}


def lifetime(**figures):
	return lambda *_: {"kind": MX.LIFETIME, "figures": figures}


class EngineTests(unittest.TestCase):
	def test_a_lifetime_network_writes_todays_row(self):
		store = MemoryStore()
		counts = MX.pull(store, [job()], lifetime(impressions=120, reach=90), TODAY, NOW)
		self.assertEqual(store.rows[("SPJ-1", TODAY)]["impressions"], 120)
		self.assertIsNone(store.rows[("SPJ-1", TODAY)]["clicks"], "not reported is None, never 0")
		self.assertEqual((store.done, counts["rows"]), ({"SPJ-1": TODAY}, 1))

	def test_a_daily_network_is_summed_from_publishing_and_restates_the_window(self):
		published = TODAY - datetime.timedelta(days=20)
		days = [
			{"date": str(published + datetime.timedelta(days=i)), "video_views": 10, "engagements": 1}
			for i in range(19)
		]
		store = MemoryStore()
		yt = job("SPJ-Y", "YouTube", published, metrics_through=TODAY - datetime.timedelta(days=2))
		MX.pull(store, [yt], lambda *_: {"kind": MX.DAILY, "days": days}, TODAY, NOW, restate_days=7)
		written = sorted(d for (_, d) in store.rows)
		self.assertEqual(
			written[0], TODAY - datetime.timedelta(days=7), "only the trailing window is rewritten"
		)
		newest = store.rows[("SPJ-Y", written[-1])]
		self.assertEqual(
			(newest["video_views"], newest["engagements"]), (190, 19), "lifetime to date, not a day"
		)
		self.assertEqual(store.done["SPJ-Y"], written[-1], "the cursor is the last day reported")

	def test_window_start(self):
		published = TODAY - datetime.timedelta(days=30)
		self.assertEqual(
			MX.window_start(job(published=published), TODAY, 7), published, "no cursor: from the start"
		)
		old = TODAY - datetime.timedelta(days=12)
		self.assertEqual(
			MX.window_start(job(published=published, metrics_through=old), TODAY, 7),
			old + datetime.timedelta(days=1),
			"a lagging cursor reaches back past the window",
		)
		self.assertEqual(
			MX.window_start(job(published=published, metrics_through=TODAY), TODAY, 7),
			TODAY - datetime.timedelta(days=7),
		)
		recent = TODAY - datetime.timedelta(days=2)
		self.assertEqual(
			MX.window_start(job(published=recent, metrics_through=TODAY), TODAY, 7),
			recent,
			"never before the post existed",
		)

	def test_a_failure_is_recorded_and_the_cursor_stays(self):
		store = MemoryStore()

		def boom(*_):
			raise MarketingAPIError("Meta Publishing", "Graph is down", status=503)

		counts = MX.pull(store, [job()], boom, TODAY, NOW)
		self.assertEqual((store.rows, store.done, counts["failed"]), ({}, {}, 1))
		self.assertIn("Graph is down", store.failed["SPJ-1"])

	def test_a_refused_credential_stops_that_network_only(self):
		store = MemoryStore()
		asked = []

		def fetch(j, *_):
			asked.append(j["name"])
			if j["network"] == "Facebook":
				raise MarketingAPIError("Meta Publishing", "token expired", status=401)
			return {"kind": MX.LIFETIME, "figures": {"impressions": 1}}

		jobs = [job("F1"), job("F2"), job("L1", "LinkedIn")]
		counts = MX.pull(store, jobs, fetch, TODAY, NOW)
		self.assertEqual(asked, ["F1", "L1"], "F2 is not asked after F1's 401")
		self.assertIn("reconnect", store.failed["F2"])
		self.assertEqual((counts["skipped"], list(store.done)), (1, ["L1"]))

	def test_what_is_not_followed(self):
		for not_due in (
			job(published=TODAY - datetime.timedelta(days=MX.TRACK_DAYS + 1)),
			job(state="Failed"),
			job(external_post_id=""),
			job(published=TODAY + datetime.timedelta(days=1)),
		):
			self.assertFalse(MX.is_due(not_due, TODAY), not_due)
		self.assertTrue(MX.is_due(job(published=TODAY - datetime.timedelta(days=MX.TRACK_DAYS)), TODAY))

	def test_latest_and_cumulative(self):
		rows = [
			{"metric_date": "2026-09-20", "reach": 5},
			{"metric_date": "2026-09-22", "reach": 9},
			{"metric_date": "2026-09-21", "reach": 7},
		]
		self.assertEqual(MX.latest(rows)["reach"], 9)
		self.assertEqual(MX.latest([]), dict.fromkeys(MX.FIGURES))
		out = MX.cumulative(
			[{"date": "2026-09-02", "video_views": 3}, {"date": "2026-09-01", "video_views": None}]
		)
		self.assertEqual([(r["metric_date"].day, r["video_views"]) for r in out], [(1, None), (2, 3)])


# ---------------------------------------------------------------- 4. parsers


def graph(**values):
	return {"data": [{"name": k, "period": "lifetime", "values": [{"value": v}]} for k, v in values.items()]}


def summary(n):
	return {"data": [], "summary": {"total_count": n}}


class ParserTests(unittest.TestCase):
	def test_facebook(self):
		figures = I.parse_facebook(
			graph(post_media_view=1200, post_total_media_view_unique=900, post_clicks=31),
			{"shares": {"count": 3}, "comments": summary(4), "reactions": summary(20)},
		)
		self.assertEqual(
			figures, {"impressions": 1200, "reach": 900, "engagements": 27, "clicks": 31, "video_views": None}
		)
		self.assertEqual(
			I.parse_facebook(graph(), {"comments": summary(1), "reactions": summary(0)})["engagements"],
			1,
			"no shares key is 0 shares",
		)
		self.assertEqual(
			I.insight_values(graph(post_reactions_by_type_total={"like": 3, "love": 2})),
			{"post_reactions_by_type_total": 5},
		)

	def test_facebook_video(self):
		figures = I.parse_facebook_video(
			graph(total_video_views=410), {"comments": summary(2), "reactions": summary(9)}
		)
		self.assertEqual(
			(figures["video_views"], figures["engagements"], figures["impressions"]), (410, 11, None)
		)

	def test_instagram(self):
		body = graph(views=800, reach=610, total_interactions=45)
		self.assertEqual(
			I.parse_instagram(body, has_video=False),
			{"impressions": 800, "reach": 610, "engagements": 45, "clicks": None, "video_views": None},
		)
		self.assertEqual(I.parse_instagram(body, has_video=True)["video_views"], 800)
		self.assertEqual(
			I.parse_instagram({"data": []}, False),
			dict.fromkeys(MX.FIGURES),
			"Meta returns an empty set, not zeros",
		)

	def test_linkedin(self):
		urn = "urn:li:share:7100000000000000001"
		body = {
			"elements": [
				{
					"organizationalEntity": "urn:li:organization:9",
					"share": urn,
					"totalShareStatistics": {
						"impressionCount": 500,
						"uniqueImpressionsCount": 410,
						"clickCount": 12,
						"likeCount": 30,
						"commentCount": 4,
						"shareCount": 2,
						"engagement": 0.1,
					},
				}
			]
		}
		self.assertEqual(
			I.parse_linkedin(body, urn),
			{"impressions": 500, "reach": 410, "engagements": 36, "clicks": 12, "video_views": None},
		)
		self.assertEqual(
			I.parse_linkedin({"elements": []}, urn)["impressions"], 0, "left out means no activity"
		)
		ugc = "urn:li:ugcPost:77"
		self.assertEqual(
			I.parse_linkedin(
				{"elements": [{"ugcPost": ugc, "totalShareStatistics": {"impressionCount": 5}}]}, ugc
			)["reach"],
			None,
		)
		self.assertEqual(
			I.linkedin_query("9", urn),
			"q=organizationalEntity&organizationalEntity=urn%3Ali%3Aorganization%3A9&shares=List(urn%3Ali%3Ashare%3A7100000000000000001)",
		)
		self.assertIn("&ugcPosts=List(urn%3Ali%3AugcPost%3A77)", I.linkedin_query("9", ugc))

	def test_youtube(self):
		body = {
			"columnHeaders": [
				{"name": "day"},
				{"name": "views"},
				{"name": "likes"},
				{"name": "comments"},
				{"name": "shares"},
			],
			"rows": [["2026-09-18", 40, 3, 1, 0], ["2026-09-19", 25, 1, 0, 1]],
		}
		self.assertEqual(
			I.parse_youtube(body),
			[
				{"date": "2026-09-18", "video_views": 40, "engagements": 4},
				{"date": "2026-09-19", "video_views": 25, "engagements": 2},
			],
		)
		self.assertEqual(I.parse_youtube({"kind": "youtubeAnalytics#resultTable"}), [], "no rows yet")

	def test_only_metrics_that_still_exist(self):
		gone = {
			"post_impressions",
			"post_impressions_unique",
			"post_engaged_users",
			"post_clicks_unique",
			"post_video_views_unique",
		}
		self.assertFalse(gone & set(I.FACEBOOK_POST_METRICS))
		self.assertFalse({"impressions", "plays"} & set(I.INSTAGRAM_METRICS))
		self.assertFalse(
			{"total_video_impressions", "total_video_views_unique"} & set(I.FACEBOOK_VIDEO_METRICS)
		)


# ---------------------------------------------------------------- 5. the reads


class ReadTests(unittest.TestCase):
	def assertAllowed(self, network, api):
		connection = P.CONNECTION_FOR[network]
		for call in api.calls:
			self.assertEqual(call["method"], "GET", call["url"])
			self.assertTrue(allowed(connection, "GET", call["url"]), call["url"])

	def test_facebook_post_and_video(self):
		api = FakeTransport({("GET", "/insights"): graph(post_media_view=1), ("GET", "/101_5"): {}})
		I.fetch(api, {"network": "Facebook", "external_post_id": "101_5"}, TODAY)
		self.assertEqual(api.calls[0]["params"]["metric"], ",".join(I.FACEBOOK_POST_METRICS))
		self.assertAllowed("Facebook", api)
		api = FakeTransport({("GET", "/video_insights"): graph(total_video_views=1), ("GET", "/88"): {}})
		I.fetch(api, {"network": "Facebook", "external_post_id": "88"}, TODAY)
		self.assertAllowed("Facebook", api)

	def test_instagram_linkedin_youtube(self):
		api = FakeTransport({("GET", "/insights"): graph(views=1)})
		I.fetch(api, {"network": "Instagram", "external_post_id": "17900", "has_video": True}, TODAY)
		self.assertAllowed("Instagram", api)
		api = FakeTransport({("GET", "organizationalEntityShareStatistics"): {"elements": []}})
		I.fetch(
			api,
			{"network": "LinkedIn", "external_post_id": "urn:li:share:7", "account_external_id": "9"},
			TODAY,
		)
		self.assertIn("shares=List(urn%3Ali%3Ashare%3A7)", api.calls[0]["url"])
		self.assertAllowed("LinkedIn", api)
		api = FakeTransport({("GET", "/v2/reports"): {"rows": []}})
		published = datetime.datetime(2026, 9, 1, 9, 0)
		I.fetch(
			api, {"network": "YouTube", "external_post_id": "dQw4w9WgXcQ", "published_at": published}, TODAY
		)
		params = api.calls[0]["params"]
		self.assertEqual(
			(params["startDate"], params["endDate"], params["filters"], params["dimensions"], params["ids"]),
			("2026-09-01", "2026-09-22", "video==dQw4w9WgXcQ", "day", "channel==MINE"),
		)
		self.assertAllowed("YouTube", api)

	def test_the_new_allowlist_entries_are_reads(self):
		source = (PUBLISH / "constants.py").read_text(encoding="utf-8")
		block = source[source.index("The engagement pull-back") :]
		block = block[: block.index("\n\t)\n)")]
		entries = re.findall(r'\((CONNECTION_\w+), "(\w+)"', block)
		self.assertEqual(len(entries), 4)
		self.assertEqual({method for _, method in entries}, {"GET"})


# ---------------------------------------------------------------- 6. attribution


class PerformanceTests(unittest.TestCase):
	def build(self, leads=(), opportunities=(), window=365):
		posts = [{"name": "SPOST-00012", "title": "Plaza"}, {"name": "SPOST-00013", "title": "Other"}]
		jobs = [
			{
				"name": "SPJ-F",
				"social_post": "SPOST-00012",
				"network": "Facebook",
				"account": "Page",
				"published_at": NOW,
				"permalink": "",
			},
			{
				"name": "SPJ-L",
				"social_post": "SPOST-00012",
				"network": "LinkedIn",
				"account": "Co",
				"published_at": NOW,
				"permalink": "",
			},
		]
		metric_rows = {
			"SPJ-F": [
				{"metric_date": "2026-09-20", "impressions": 50},
				{"metric_date": "2026-09-22", "impressions": 80},
			]
		}
		rows = PF.build(posts, jobs, metric_rows, list(leads), list(opportunities), window)
		return {(r["social_post"], r["network"]): r for r in rows}

	def test_engagement_is_the_newest_row(self):
		rows = self.build()
		self.assertEqual(rows[("SPOST-00012", "Facebook")]["impressions"], 80)
		self.assertIsNone(rows[("SPOST-00012", "LinkedIn")]["impressions"])

	def test_leads_join_on_the_post_and_the_network(self):
		rows = self.build(
			leads=[
				{"name": "L1", "custom_utm_content": "spost-00012", "custom_utm_source": "facebook"},
				{"name": "L2", "custom_utm_content": " SPOST-00012 ", "custom_utm_source": "LinkedIn"},
				{"name": "L3", "custom_utm_content": "spost-00012", "custom_utm_source": "newsletter"},
				{"name": "L4", "custom_utm_content": "spost-00012", "custom_utm_source": "instagram"},
				{"name": "L5", "custom_utm_content": "someone-else", "custom_utm_source": "facebook"},
			]
		)
		self.assertEqual(rows[("SPOST-00012", "Facebook")]["leads"], 1)
		self.assertEqual(
			rows[("SPOST-00012", "LinkedIn")]["leads"], 1, "compared as tagged: trimmed, lower case"
		)
		self.assertEqual(rows[("SPOST-00012", PF.NETWORK_NOT_RECORDED)]["leads"], 1)
		self.assertEqual(
			rows[("SPOST-00012", "Instagram")]["leads"], 1, "a network with no job still gets its row"
		)
		self.assertEqual(sum(r["leads"] for r in rows.values()), 4, "an unknown post's lead is not ours")

	def test_revenue_follows_roas(self):
		touch = datetime.datetime(2026, 9, 1)
		won = {
			"custom_utm_content": "spost-00012",
			"custom_utm_source": "facebook",
			"status": "Closed Won",
			"opportunity_amount": 48000,
			"project": "PRJ-1",
			"total_billed_amount": 12000,
			"custom_attribution_captured_on": touch,
			"creation": touch,
		}
		late = {**won, "creation": touch + datetime.timedelta(days=400), "project": None}
		lost = {**won, "status": "Lost", "project": None}
		row = self.build(opportunities=[won, late, lost])[("SPOST-00012", "Facebook")]
		self.assertEqual(
			(row["opportunities"], row["won"], row["contract_value"], row["invoiced"]),
			(2, 1, 48000.0, 12000.0),
		)


# ---------------------------------------------------------------- wiring


class WiringTests(unittest.TestCase):
	def test_the_nightly_cron(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		self.assertIn(
			'"50 3 * * *": ["erpnext_enhancements.marketing.publish.metrics_sync.nightly_social_metrics"]',
			hooks,
		)

	def test_the_sync_gates_and_the_manual_pull(self):
		tree = ast.parse((PUBLISH / "metrics_sync.py").read_text(encoding="utf-8"))
		fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
		nightly = ast.unparse(fns["nightly_social_metrics"])
		self.assertLess(
			nightly.index("'enabled'"), nightly.index("connected_networks"), "the master switch first"
		)
		self.assertLess(nightly.index("connected_networks"), nightly.index("due_jobs"))
		enqueue = ast.unparse(fns["enqueue"])
		for part in ("queue=QUEUE", "job_id=JOB_ID", "deduplicate=True"):
			self.assertIn(part, enqueue)
		self.assertNotIn("job_name", enqueue, "frappe.enqueue's own parameter")
		manual = fns["pull_metrics_now"]
		self.assertEqual(
			[ast.unparse(d) for d in manual.decorator_list], ["frappe.whitelist(methods=['POST'])"]
		)
		self.assertIn("OPERATOR_ROLE not in frappe.get_roles()", ast.unparse(manual))
		self.assertIn("restate_days=", ast.unparse(fns["run_metrics"]))
		whitelisted = [
			n for n, f in fns.items() if any("whitelist" in ast.unparse(d) for d in f.decorator_list)
		]
		self.assertEqual(whitelisted, ["pull_metrics_now"])

	def test_the_report_is_not_for_marketing_team(self):
		folder = APP / "marketing" / "report" / "social_post_performance"
		report = json.loads((folder / "social_post_performance.json").read_text(encoding="utf-8"))
		self.assertEqual(
			{r["role"] for r in report["roles"]},
			{"System Manager", "Sales Manager", "Marketing Manager"},
			"revenue is financial",
		)
		self.assertEqual(
			(report["ref_doctype"], report["module"], report["report_type"]),
			("Social Post", "Marketing", "Script Report"),
		)
		self.assertIn(
			"performance.build(", (folder / "social_post_performance.py").read_text(encoding="utf-8")
		)
		self.assertFalse(
			(folder / "social_post_performance.html").exists(), "a report print template is compiled whole"
		)

	def test_the_schema(self):
		def fields(name):
			return {
				f["fieldname"]: f
				for f in json.loads((DOCTYPES / name / f"{name}.json").read_text(encoding="utf-8"))["fields"]
			}

		job_fields = fields("social_publish_job")
		self.assertEqual(job_fields["metrics_through"]["fieldtype"], "Date")
		for name in ("metrics_through", "metrics_checked_at", "metrics_error"):
			self.assertEqual(job_fields[name].get("read_only"), 1, name)
		metric = fields("social_post_metric")
		for name in MX.FIGURES:
			self.assertEqual(metric[name].get("read_only"), 1, name)
			self.assertIn("lifetime", metric[name]["description"].lower(), name)


if __name__ == "__main__":
	unittest.main()
