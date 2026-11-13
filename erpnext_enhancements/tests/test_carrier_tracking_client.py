# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for the FedEx and UPS tracking client (v1.819.0, TASK-2026-03204).

``carrier_tracking/carriers.py``, ``normalize.py`` and ``client.py`` import neither Frappe nor (at
load) ``requests``, so this suite needs no stub: a fake HTTP object answers each request from a
queue and records what was sent, and a dict stands in for Redis.

What it holds:

* **Detection**: the formats, "Unknown" as an answer, and an explicit carrier always winning.
* **Tokens**: fetched once and cached for less than their life; UPS's Basic login and string
  ``expires_in``; FedEx's form login; one refresh and one retry on a 401, never two; a refused
  login and FedEx's token-endpoint penalty remembered, so the carrier is not asked again.
* **Every error path**: not found, invalid, 401, 403, 429 (and the back-off it leaves), 5xx, a
  timeout, a dropped connection, a body that is not JSON, a JSON body of the wrong shape, and an
  object that raises when touched. ``track`` never raises and never invents a status.
* **Normalization** from made-up payloads shaped like the documented responses (FedEx Track
  ``trackResults[]``, UPS ``trackResponse.shipment[].package[]``).
* **Secrets**: a carrier error that echoes the client id, the secret or the token comes back masked.
* **The settings DocType**: off by default, secrets as Password, nothing but System Manager, and the
  backfill patch registered and writing only missing rows.

The tracking numbers here are invented. FedEx's sandbox numbers are not used: the repository is
public and the tests must not look like real shipments.

Run: python -m unittest erpnext_enhancements.tests.test_carrier_tracking_client -v
"""

import base64
import json
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.carrier_tracking import carriers as C
from erpnext_enhancements.carrier_tracking import client
from erpnext_enhancements.carrier_tracking import normalize as N

APP = REPO_ROOT / "erpnext_enhancements"
SETTINGS_JSON = (
	APP / "carrier_tracking" / "doctype" / "carrier_tracking_settings" / "carrier_tracking_settings.json"
)
PATCH = APP / "patches" / "backfill_carrier_tracking_settings_defaults.py"

NOW = datetime(2026, 11, 13, 10, 0, 0)

# Invented values. Long enough for mask() to replace them, shaped like nothing real.
CLIENT_ID = "FAKE-DO-NOT-USE-client-id"
CLIENT_SECRET = "FAKE-DO-NOT-USE-client-secret"
TOKEN = "FAKE-DO-NOT-USE-token-one"
TOKEN_2 = "FAKE-DO-NOT-USE-token-two"

FEDEX_NUMBER = "100000000001"  # 12 digits
UPS_NUMBER = "1Z0000000000000001"


class FakeResponse:
	def __init__(self, status, body=None, headers=None):
		self.status_code = status
		self._body = body
		self.headers = headers or {}
		self.text = body if isinstance(body, str) else json.dumps(body)

	def json(self):
		if isinstance(self._body, str):
			raise ValueError("not json")
		return self._body


class FakeHTTP:
	def __init__(self, *answers):
		self.answers = list(answers)
		self.calls = []

	def request(self, method, url, **kwargs):
		self.calls.append({"method": method, "url": url, **kwargs})
		if not self.answers:
			raise AssertionError(f"unexpected request {method} {url}")
		answer = self.answers.pop(0)
		if isinstance(answer, Exception):
			raise answer
		return answer


class FakeCache:
	def __init__(self):
		self.data = {}
		self.ttl = {}

	def get(self, key):
		return self.data.get(key)

	def set(self, key, value, seconds):
		self.data[key] = value
		self.ttl[key] = seconds

	def delete(self, key):
		self.data.pop(key, None)


def config(carrier=C.FEDEX, environment=client.SANDBOX):
	return client.Config(carrier, environment, CLIENT_ID, CLIENT_SECRET)


def token_ok(token=TOKEN, expires_in=3600):
	return FakeResponse(
		200, {"access_token": token, "token_type": "bearer", "expires_in": expires_in, "scope": "CXS"}
	)


def fedex_body(track_result):
	return {
		"transactionId": "made-up",
		"output": {
			"completeTrackResults": [{"trackingNumber": FEDEX_NUMBER, "trackResults": [track_result]}]
		},
	}


FEDEX_IN_TRANSIT = {
	"trackingNumberInfo": {"trackingNumber": FEDEX_NUMBER},
	"latestStatusDetail": {
		"code": "IT",
		"derivedCode": "IT",
		"statusByLocale": "In transit",
		"description": "In transit",
		"scanLocation": {"city": "SPRINGFIELD", "stateOrProvinceCode": "UT", "countryCode": "US"},
	},
	"dateAndTimes": [
		{"type": "SHIP", "dateTime": "2026-11-10T00:00:00-06:00"},
		{"type": "ESTIMATED_DELIVERY", "dateTime": "2026-11-15T20:00:00-07:00"},
	],
	"estimatedDeliveryTimeWindow": {"window": {}},
}

FEDEX_DELIVERED = {
	"latestStatusDetail": {
		"code": "DL",
		"derivedCode": "DL",
		"description": "Delivered",
		"scanLocation": {"city": "OREM", "stateOrProvinceCode": "UT", "countryCode": "US"},
	},
	"dateAndTimes": [
		{"type": "ACTUAL_DELIVERY", "dateTime": "2026-11-12T14:14:00-07:00"},
		{"type": "ESTIMATED_DELIVERY", "dateTime": "2026-11-12T20:00:00-07:00"},
	],
}

UPS_IN_TRANSIT = {
	"trackResponse": {
		"shipment": [
			{
				"inquiryNumber": UPS_NUMBER,
				"package": [
					{
						"trackingNumber": UPS_NUMBER,
						"currentStatus": {
							"type": "I",
							"statusCode": "005",
							"description": "On the Way",
							"simplifiedTextDescription": "On the Way",
							"code": "IT",
						},
						"deliveryDate": [{"type": "SDD", "date": "20261116"}],
						"activity": [
							{
								"date": "20261113",
								"time": "081500",
								"location": {
									"address": {
										"city": "Salt Lake City",
										"stateProvince": "UT",
										"countryCode": "US",
									}
								},
								"status": {
									"type": "I",
									"statusCode": "005",
									"description": "Departed from Facility",
								},
							},
						],
					}
				],
			}
		]
	}
}


def ups_package(**current):
	body = json.loads(json.dumps(UPS_IN_TRANSIT))
	package = body["trackResponse"]["shipment"][0]["package"][0]
	package["currentStatus"].update(current)
	return body, package


class TestDetection(unittest.TestCase):
	def test_ups_1z(self):
		self.assertEqual(C.detect_carrier(UPS_NUMBER), C.UPS)
		self.assertEqual(C.detect_carrier("1z 000 000 00 0000 0001"), C.UPS)

	def test_fedex_lengths(self):
		self.assertEqual(C.detect_carrier("1" * 12), C.FEDEX)
		self.assertEqual(C.detect_carrier("1" * 15), C.FEDEX)
		self.assertEqual(C.detect_carrier("96" + "1" * 20), C.FEDEX)

	def test_unknown_is_an_answer(self):
		# 22 digits starting 92-95 is USPS's format, which FedEx Ground Economy also prints.
		self.assertEqual(C.detect_carrier("94" + "0" * 20), C.UNKNOWN)
		self.assertEqual(C.detect_carrier("TBA000000000000"), C.UNKNOWN)  # a marketplace's own service
		self.assertEqual(C.detect_carrier("12345"), C.UNKNOWN)
		self.assertEqual(C.detect_carrier(""), C.UNKNOWN)

	def test_explicit_carrier_wins(self):
		self.assertEqual(C.resolve_carrier("FedEx", UPS_NUMBER), C.FEDEX)
		self.assertEqual(C.resolve_carrier("UPS", "1" * 12), C.UPS)
		self.assertEqual(C.resolve_carrier("Other", UPS_NUMBER), C.OTHER)
		self.assertEqual(C.resolve_carrier("Auto", UPS_NUMBER), C.UPS)
		self.assertEqual(C.resolve_carrier("", "1" * 15), C.FEDEX)

	def test_clean_number(self):
		self.assertEqual(C.clean_number(" 1z-000 000 "), "1Z000000")

	def test_public_pages(self):
		self.assertEqual(
			C.tracking_url(C.FEDEX, FEDEX_NUMBER), f"https://www.fedex.com/wtrk/track/?trknbr={FEDEX_NUMBER}"
		)
		self.assertTrue(C.tracking_url(C.UPS, UPS_NUMBER).startswith("https://www.ups.com/track?"))
		self.assertIn(UPS_NUMBER, C.tracking_url(C.UPS, UPS_NUMBER))
		self.assertEqual(C.tracking_url(C.OTHER, "X1", "https://example.com/t/1"), "https://example.com/t/1")
		self.assertEqual(C.tracking_url(C.OTHER, "X1", "javascript:alert(1)"), "")
		self.assertEqual(C.tracking_url(C.OTHER, "X1", ""), "")


class TestMask(unittest.TestCase):
	def test_known_values_and_patterns(self):
		text = (
			f"bad id {CLIENT_ID} secret {CLIENT_SECRET} header Bearer {TOKEN} "
			'{"access_token": "abc123xyz", "client_secret": "s3cr3t"} Authorization: Basic dXNlcjpwYXNz'
		)
		out = C.mask(text, (CLIENT_ID, CLIENT_SECRET, TOKEN))
		for leaked in (CLIENT_ID, CLIENT_SECRET, TOKEN, "abc123xyz", "s3cr3t", "dXNlcjpwYXNz"):
			self.assertNotIn(leaked, out)

	def test_caps_and_never_raises(self):
		self.assertEqual(len(C.mask("x" * 5000)), 500)
		self.assertEqual(C.mask(None), "")

		class Hostile:
			def __str__(self):
				raise RuntimeError("no")

		self.assertEqual(C.mask(Hostile()), "(message withheld)")


class TestTokens(unittest.TestCase):
	def test_fedex_token_is_fetched_once_and_cached_short(self):
		cache = FakeCache()
		http = FakeHTTP(
			token_ok(),
			FakeResponse(200, fedex_body(FEDEX_IN_TRANSIT)),
			FakeResponse(200, fedex_body(FEDEX_IN_TRANSIT)),
		)
		self.assertEqual(client.track(config(), FEDEX_NUMBER, cache, http, NOW)["outcome"], "ok")
		self.assertEqual(client.track(config(), FEDEX_NUMBER, cache, http, NOW)["outcome"], "ok")
		token_calls = [c for c in http.calls if c["url"].endswith("/oauth/token")]
		self.assertEqual(len(token_calls), 1)
		self.assertEqual(token_calls[0]["url"], "https://apis-sandbox.fedex.com/oauth/token")
		self.assertEqual(token_calls[0]["data"]["grant_type"], "client_credentials")
		self.assertEqual(token_calls[0]["data"]["client_id"], CLIENT_ID)
		token_key = next(k for k in cache.data if ":token:" in k)
		self.assertEqual(cache.ttl[token_key], 3600 - client.TOKEN_SAFETY_SECONDS)
		self.assertNotIn(CLIENT_SECRET, token_key)
		self.assertNotIn(CLIENT_ID, token_key)

	def test_production_hosts(self):
		cache = FakeCache()
		http = FakeHTTP(token_ok(), FakeResponse(200, fedex_body(FEDEX_IN_TRANSIT)))
		client.track(config(environment=client.PRODUCTION), FEDEX_NUMBER, cache, http, NOW)
		self.assertTrue(all(c["url"].startswith("https://apis.fedex.com/") for c in http.calls))
		http = FakeHTTP(token_ok(), FakeResponse(200, UPS_IN_TRANSIT))
		client.track(config(C.UPS, client.PRODUCTION), UPS_NUMBER, FakeCache(), http, NOW)
		self.assertEqual(http.calls[0]["url"], "https://onlinetools.ups.com/security/v1/oauth/token")
		self.assertEqual(
			http.calls[1]["url"], f"https://onlinetools.ups.com/api/track/v1/details/{UPS_NUMBER}"
		)

	def test_ups_basic_login_and_string_expiry(self):
		cache = FakeCache()
		http = FakeHTTP(token_ok(expires_in="14399"), FakeResponse(200, UPS_IN_TRANSIT))
		result = client.track(config(C.UPS), UPS_NUMBER, cache, http, NOW)
		self.assertEqual(result["outcome"], "ok")
		login = http.calls[0]
		self.assertEqual(login["url"], "https://wwwcie.ups.com/security/v1/oauth/token")
		self.assertEqual(login["data"], {"grant_type": "client_credentials"})
		expected = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
		self.assertEqual(login["headers"]["Authorization"], expected)
		track = http.calls[1]
		self.assertEqual(track["method"], "GET")
		self.assertEqual(track["headers"]["Authorization"], f"Bearer {TOKEN}")
		self.assertTrue(track["headers"]["transId"])
		self.assertEqual(track["headers"]["transactionSrc"], client.UPS_TRANSACTION_SRC)
		token_key = next(k for k in cache.data if ":token:" in k)
		self.assertEqual(cache.ttl[token_key], 14399 - client.TOKEN_SAFETY_SECONDS)

	def test_fedex_track_request_shape(self):
		http = FakeHTTP(token_ok(), FakeResponse(200, fedex_body(FEDEX_IN_TRANSIT)))
		client.track(config(), FEDEX_NUMBER, FakeCache(), http, NOW)
		track = http.calls[1]
		self.assertEqual(track["url"], "https://apis-sandbox.fedex.com/track/v1/trackingnumbers")
		self.assertEqual(track["method"], "POST")
		self.assertEqual(track["headers"]["Authorization"], f"Bearer {TOKEN}")
		self.assertEqual(
			track["json"]["trackingInfo"][0]["trackingNumberInfo"]["trackingNumber"], FEDEX_NUMBER
		)
		self.assertIn("includeDetailedScans", track["json"])

	def test_401_refreshes_once_and_retries_once(self):
		cache = FakeCache()
		http = FakeHTTP(
			token_ok(TOKEN),
			FakeResponse(401, {"errors": [{"code": "NOT.AUTHORIZED.ERROR"}]}),
			token_ok(TOKEN_2),
			FakeResponse(200, fedex_body(FEDEX_IN_TRANSIT)),
		)
		result = client.track(config(), FEDEX_NUMBER, cache, http, NOW)
		self.assertEqual(result["outcome"], "ok")
		self.assertEqual(http.calls[3]["headers"]["Authorization"], f"Bearer {TOKEN_2}")
		self.assertIn(TOKEN_2, cache.data.values())

	def test_second_401_is_auth_failed_not_a_loop(self):
		http = FakeHTTP(token_ok(TOKEN), FakeResponse(401, {}), token_ok(TOKEN_2), FakeResponse(401, {}))
		result = client.track(config(), FEDEX_NUMBER, FakeCache(), http, NOW)
		self.assertEqual(result["outcome"], "auth_failed")
		self.assertEqual(len(http.calls), 4)
		self.assertIsNone(result["status"])

	def test_refused_login_is_remembered(self):
		cache = FakeCache()
		http = FakeHTTP(FakeResponse(401, {"errors": [{"code": "NOT.AUTHORIZED.ERROR", "message": "bad"}]}))
		self.assertEqual(client.track(config(), FEDEX_NUMBER, cache, http, NOW)["outcome"], "auth_failed")
		# No answers left in the queue: a second request would fail the test.
		self.assertEqual(client.track(config(), FEDEX_NUMBER, cache, http, NOW)["outcome"], "auth_failed")
		refused_key = next(k for k in cache.data if ":refused:" in k)
		self.assertEqual(cache.ttl[refused_key], client.AUTH_REFUSED_SECONDS)
		client.forget_token(config(), cache)
		self.assertFalse([k for k in cache.data if ":refused:" in k])

	def test_fedex_token_403_is_the_ten_minute_penalty(self):
		cache = FakeCache()
		result = client.track(config(), FEDEX_NUMBER, cache, FakeHTTP(FakeResponse(403, {})), NOW)
		self.assertEqual(result["outcome"], "rate_limited")
		self.assertEqual(result["retry_after"], client.FEDEX_TOKEN_PENALTY_SECONDS)
		self.assertEqual(
			client.track(config(), FEDEX_NUMBER, cache, FakeHTTP(), NOW)["outcome"], "rate_limited"
		)

	def test_ups_token_403_is_auth_failed(self):
		result = client.track(config(C.UPS), UPS_NUMBER, FakeCache(), FakeHTTP(FakeResponse(403, {})), NOW)
		self.assertEqual(result["outcome"], "auth_failed")

	def test_token_endpoint_down(self):
		result = client.track(config(), FEDEX_NUMBER, FakeCache(), FakeHTTP(FakeResponse(503, "busy")), NOW)
		self.assertEqual(result["outcome"], "unavailable")
		result = client.track(config(), FEDEX_NUMBER, FakeCache(), FakeHTTP(TimeoutError("slow")), NOW)
		self.assertEqual(result["outcome"], "unavailable")
		result = client.track(config(), FEDEX_NUMBER, FakeCache(), FakeHTTP(FakeResponse(200, "<html>")), NOW)
		self.assertEqual(result["outcome"], "bad_response")


class TestErrorPaths(unittest.TestCase):
	def run_track(self, answer, carrier=C.FEDEX, number=FEDEX_NUMBER, cache=None):
		cache = cache or FakeCache()
		http = FakeHTTP(token_ok(), *(answer if isinstance(answer, list) else [answer]))
		return client.track(config(carrier), number, cache, http, NOW), http, cache

	def assert_no_status(self, result):
		self.assertIsNone(result["status"])
		self.assertIsNone(result["estimated_delivery"])
		self.assertIsNone(result["delivered_at"])

	def test_fedex_not_found_in_result(self):
		body = fedex_body(
			{
				"error": {
					"code": "TRACKING.TRACKINGNUMBER.NOTFOUND",
					"message": "Tracking number cannot be found.",
				}
			}
		)
		result, _, _ = self.run_track(FakeResponse(200, body))
		self.assertEqual(result["outcome"], "not_found")
		self.assertIn("TRACKING.TRACKINGNUMBER.NOTFOUND", result["error"])
		self.assert_no_status(result)

	def test_fedex_invalid(self):
		body = fedex_body(
			{"error": {"code": "TRACKING.TRACKINGNUMBER.INVALID", "message": "Invalid tracking number."}}
		)
		self.assertEqual(self.run_track(FakeResponse(200, body))[0]["outcome"], "invalid")
		http400 = FakeResponse(400, {"errors": [{"code": "TRACKING.TRACKINGNUMBER.INVALID"}]})
		self.assertEqual(self.run_track(http400)[0]["outcome"], "invalid")

	def test_fedex_404_not_found(self):
		result, _, _ = self.run_track(FakeResponse(404, {"errors": [{"code": "NOT.FOUND.ERROR"}]}))
		self.assertEqual(result["outcome"], "not_found")

	def test_ups_404_and_400(self):
		body = {
			"response": {"errors": [{"code": "151044", "message": "Tracking number information not found"}]}
		}
		result, _, _ = self.run_track(FakeResponse(404, body), C.UPS, UPS_NUMBER)
		self.assertEqual(result["outcome"], "not_found")
		self.assertIn("151044", result["error"])
		result, _, _ = self.run_track(
			FakeResponse(400, {"response": {"errors": [{"code": "x"}]}}), C.UPS, UPS_NUMBER
		)
		self.assertEqual(result["outcome"], "invalid")

	def test_ups_warning_without_package(self):
		body = {
			"trackResponse": {
				"shipment": [
					{
						"inquiryNumber": UPS_NUMBER,
						"warnings": [{"code": "TW0001", "message": "Tracking Information Not Found"}],
					}
				]
			}
		}
		result, _, _ = self.run_track(FakeResponse(200, body), C.UPS, UPS_NUMBER)
		self.assertEqual(result["outcome"], "not_found")

	def test_429_backs_off_for_retry_after(self):
		result, _, cache = self.run_track(
			FakeResponse(429, {"errors": [{"code": "RATE.LIMIT"}]}, {"Retry-After": "10"})
		)
		self.assertEqual(result["outcome"], "rate_limited")
		self.assertEqual(result["retry_after"], 10)
		self.assert_no_status(result)
		# The next call asks nobody.
		later = client.track(config(), FEDEX_NUMBER, cache, FakeHTTP(), NOW)
		self.assertEqual(later["outcome"], "rate_limited")

	def test_429_without_header_uses_default_and_caps(self):
		result, _, _ = self.run_track(FakeResponse(429, {}))
		self.assertEqual(result["retry_after"], client.DEFAULT_RETRY_AFTER)
		result, _, _ = self.run_track(FakeResponse(429, {}, {"Retry-After": "999999"}))
		self.assertEqual(result["retry_after"], client.MAX_RETRY_AFTER)

	def test_5xx_timeout_and_dropped_connection(self):
		for answer in (
			FakeResponse(500, {}),
			FakeResponse(503, "Service Unavailable"),
			TimeoutError(),
			ConnectionError(),
		):
			result, _, _ = self.run_track(answer)
			self.assertEqual(result["outcome"], "unavailable", answer)
			self.assert_no_status(result)

	def test_malformed_json_and_wrong_shapes(self):
		for body in (
			"<html>oops</html>",
			{"output": "x"},
			{"output": {"completeTrackResults": [{}]}},
			{"output": {}},
		):
			result, _, _ = self.run_track(FakeResponse(200, body))
			self.assertEqual(result["outcome"], "bad_response", body)
		for body in ("nope", {"trackResponse": []}, {}):
			result, _, _ = self.run_track(FakeResponse(200, body), C.UPS, UPS_NUMBER)
			self.assertEqual(result["outcome"], "bad_response", body)

	def test_hostile_response_never_raises(self):
		class Hostile:
			@property
			def status_code(self):
				raise RuntimeError("boom")

		result, _, _ = self.run_track(Hostile())
		self.assertIn(result["outcome"], ("bad_response", "unavailable"))

	def test_not_configured_and_unsupported(self):
		blank = client.Config(C.FEDEX, client.SANDBOX, "", "")
		self.assertEqual(
			client.track(blank, FEDEX_NUMBER, FakeCache(), FakeHTTP(), NOW)["outcome"], "not_configured"
		)
		other = client.Config(C.OTHER, client.SANDBOX, CLIENT_ID, CLIENT_SECRET)
		self.assertEqual(
			client.track(other, "ABC12345", FakeCache(), FakeHTTP(), NOW)["outcome"], "unsupported"
		)
		self.assertEqual(
			client.track(None, "ABC12345", FakeCache(), FakeHTTP(), NOW)["outcome"], "unsupported"
		)
		self.assertEqual(client.track(config(), "<x>", FakeCache(), FakeHTTP(), NOW)["outcome"], "invalid")

	def test_broken_cache_is_survivable(self):
		class Broken:
			def get(self, key):
				raise RuntimeError("redis down")

			set = delete = get

		http = FakeHTTP(token_ok(), FakeResponse(200, fedex_body(FEDEX_IN_TRANSIT)))
		self.assertEqual(client.track(config(), FEDEX_NUMBER, Broken(), http, NOW)["outcome"], "ok")


class TestSecretsNeverLeak(unittest.TestCase):
	def test_echoed_secrets_are_masked(self):
		echo = f"client_id={CLIENT_ID} client_secret={CLIENT_SECRET} Bearer {TOKEN}"
		answers = [
			[FakeResponse(401, {"errors": [{"code": "NOT.AUTHORIZED.ERROR", "message": echo}]})],
			[token_ok(), FakeResponse(500, {"errors": [{"code": "INTERNAL.SERVER.ERROR", "message": echo}]})],
			[
				token_ok(),
				FakeResponse(
					200, fedex_body({"error": {"code": "TRACKING.TRACKINGNUMBER.NOTFOUND", "message": echo}})
				),
			],
			[
				token_ok(),
				FakeResponse(200, fedex_body({"latestStatusDetail": {"code": "IT", "description": echo}})),
			],
		]
		for queue in answers:
			result = client.track(config(), FEDEX_NUMBER, FakeCache(), FakeHTTP(*queue), NOW)
			blob = json.dumps(result)
			for secret in (CLIENT_ID, CLIENT_SECRET, TOKEN):
				self.assertNotIn(secret, blob, result)

	def test_exception_names_only(self):
		class Leaky(Exception):
			def __str__(self):
				return CLIENT_SECRET

		result = client.track(config(), FEDEX_NUMBER, FakeCache(), FakeHTTP(Leaky()), NOW)
		self.assertNotIn(CLIENT_SECRET, json.dumps(result))


class TestNormalizeFedEx(unittest.TestCase):
	def test_in_transit(self):
		out = N.fedex_package(FEDEX_IN_TRANSIT)
		self.assertEqual(out["status"], C.IN_TRANSIT)
		self.assertEqual(out["estimated_delivery"], "2026-11-15")
		self.assertEqual(out["last_location"], "SPRINGFIELD, UT, US")
		self.assertIsNone(out["delivered_at"])

	def test_delivered_keeps_local_time(self):
		out = N.fedex_package(FEDEX_DELIVERED)
		self.assertEqual(out["status"], C.DELIVERED)
		self.assertEqual(out["delivered_at"], "2026-11-12 14:14:00")
		self.assertIsNone(out["estimated_delivery"])

	def test_window_and_fallbacks(self):
		out = N.fedex_package(
			{
				"latestStatusDetail": {"code": "OC", "description": "Label created"},
				"estimatedDeliveryTimeWindow": {
					"window": {"begins": "2026-11-17T09:00:00", "ends": "2026-11-18T17:00:00"}
				},
			}
		)
		self.assertEqual(out["status"], C.LABEL_CREATED)
		self.assertEqual(out["estimated_delivery"], "2026-11-18")
		out = N.fedex_package(
			{
				"latestStatusDetail": {"code": "PU"},
				"standardTransitTimeWindow": {"window": {"ends": "2026-11-19T00:00:00"}},
				"scanEvents": [{"scanLocation": {"city": "OGDEN", "stateOrProvinceCode": "UT"}}],
			}
		)
		self.assertEqual(out["estimated_delivery"], "2026-11-19")
		self.assertEqual(out["last_location"], "OGDEN, UT")

	def test_status_table(self):
		self.assertEqual(N.fedex_status("OD"), C.OUT_FOR_DELIVERY)
		self.assertEqual(N.fedex_status("DE"), C.EXCEPTION)
		self.assertEqual(N.fedex_status("SE"), C.EXCEPTION)
		self.assertEqual(N.fedex_status("CA"), C.EXCEPTION)
		self.assertEqual(N.fedex_status("ZZ"), C.UNKNOWN_STATUS)
		# derivedCode is read first; an unknown one falls back to code.
		out = N.fedex_package({"latestStatusDetail": {"code": "OD", "derivedCode": "QQ"}})
		self.assertEqual(out["status"], C.OUT_FOR_DELIVERY)

	def test_garbage_never_raises(self):
		for value in (
			None,
			[],
			"x",
			{"latestStatusDetail": "x", "dateAndTimes": "y", "scanEvents": [1, None]},
		):
			self.assertEqual(N.fedex_package(value)["status"], C.UNKNOWN_STATUS)

	def test_end_to_end_ok_shape(self):
		http = FakeHTTP(token_ok(), FakeResponse(200, fedex_body(FEDEX_DELIVERED)))
		result = client.track(config(), FEDEX_NUMBER, FakeCache(), http, NOW)
		self.assertEqual(result["outcome"], "ok")
		self.assertEqual(result["carrier"], C.FEDEX)
		self.assertEqual(result["tracking_number"], FEDEX_NUMBER)
		self.assertEqual(result["status"], C.DELIVERED)
		self.assertEqual(result["checked_at"], "2026-11-13 10:00:00")
		self.assertEqual(
			set(result),
			{
				"carrier",
				"tracking_number",
				"outcome",
				"status",
				"description",
				"estimated_delivery",
				"delivered_at",
				"last_location",
				"checked_at",
				"error",
				"retry_after",
			},
		)


class TestNormalizeUPS(unittest.TestCase):
	def test_in_transit(self):
		out = N.ups_package(UPS_IN_TRANSIT["trackResponse"]["shipment"][0]["package"][0])
		self.assertEqual(out["status"], C.IN_TRANSIT)
		self.assertEqual(out["estimated_delivery"], "2026-11-16")
		self.assertEqual(out["last_location"], "Salt Lake City, UT, US")
		self.assertEqual(out["description"], "On the Way")

	def test_out_for_delivery_is_an_I_with_its_status_code(self):
		_, package = ups_package(type="I", statusCode="021", description="Out For Delivery Today")
		self.assertEqual(N.ups_package(package)["status"], C.OUT_FOR_DELIVERY)

	def test_rescheduled_beats_scheduled(self):
		_, package = ups_package()
		package["deliveryDate"] = [{"type": "SDD", "date": "20261116"}, {"type": "RDD", "date": "20261118"}]
		self.assertEqual(N.ups_package(package)["estimated_delivery"], "2026-11-18")

	def test_delivered(self):
		_, package = ups_package(type="D", statusCode="011", description="Delivered")
		package["deliveryDate"] = [{"type": "DEL", "date": "20261112"}]
		package["deliveryTime"] = {"type": "DEL", "endTime": "141400"}
		out = N.ups_package(package)
		self.assertEqual(out["status"], C.DELIVERED)
		self.assertEqual(out["delivered_at"], "2026-11-12 14:14:00")
		self.assertIsNone(out["estimated_delivery"])

	def test_delivered_time_from_activity(self):
		_, package = ups_package(type="D", statusCode="011")
		package["deliveryDate"] = []
		package["activity"][0].update(
			{"date": "20261112", "time": "093000", "status": {"type": "D", "statusCode": "011"}}
		)
		self.assertEqual(N.ups_package(package)["delivered_at"], "2026-11-12 09:30:00")

	def test_letters(self):
		self.assertEqual(N.ups_status("M"), C.LABEL_CREATED)
		self.assertEqual(N.ups_status("X"), C.EXCEPTION)
		self.assertEqual(N.ups_status("MV"), C.EXCEPTION)
		self.assertEqual(N.ups_status("I", "034"), C.EXCEPTION)
		self.assertEqual(N.ups_status("??"), C.UNKNOWN_STATUS)

	def test_garbage_never_raises(self):
		for value in (None, [], {"currentStatus": "x", "activity": [None, 3], "deliveryDate": "z"}):
			self.assertEqual(N.ups_package(value)["status"], C.UNKNOWN_STATUS)


class TestSettingsDocType(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.meta = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
		cls.fields = {f["fieldname"]: f for f in cls.meta["fields"]}

	def test_single_in_its_module(self):
		self.assertEqual(self.meta["issingle"], 1)
		self.assertEqual(self.meta["module"], "Carrier Tracking")
		self.assertEqual({p["role"] for p in self.meta["permissions"]}, {"System Manager"})

	def test_everything_off_by_default(self):
		for name in ("enabled", "fedex_enabled", "ups_enabled"):
			self.assertEqual(self.fields[name]["fieldtype"], "Check")
			self.assertEqual(self.fields[name].get("default"), "0", name)
		for name in ("fedex_environment", "ups_environment"):
			self.assertEqual(self.fields[name]["options"].split("\n"), [client.SANDBOX, client.PRODUCTION])
			self.assertEqual(self.fields[name].get("default"), client.SANDBOX)

	def test_secrets_are_passwords(self):
		for name in ("fedex_client_secret", "ups_client_secret"):
			self.assertEqual(self.fields[name]["fieldtype"], "Password")
			self.assertNotIn("default", self.fields[name])

	def test_backfill_patch_registered(self):
		dotted = "erpnext_enhancements.patches.backfill_carrier_tracking_settings_defaults"
		self.assertIn(dotted, (APP / "patches.txt").read_text(encoding="utf-8").split())
		self.assertIn(
			f'"{dotted}.backfill_carrier_tracking_settings_defaults"',
			(APP / "hooks.py").read_text(encoding="utf-8"),
		)

	def test_backfill_writes_only_missing_rows(self):
		written = {}
		stored = {"enabled"}  # somebody saved the page with the switch off
		meta_fields = [
			types.SimpleNamespace(
				fieldname=f["fieldname"], fieldtype=f["fieldtype"], default=f.get("default")
			)
			for f in self.meta["fields"]
		]
		fake = types.ModuleType("frappe")
		fake.db = types.SimpleNamespace(
			exists=lambda doctype, name: True,
			sql=lambda query, values: [(name,) for name in stored],
			set_single_value=lambda doctype, field, value: written.__setitem__(field, value),
		)
		fake.get_meta = lambda doctype: types.SimpleNamespace(fields=meta_fields)
		fake.clear_document_cache = lambda *a: None
		fake.log_error = lambda *a, **k: None
		model = types.ModuleType("frappe.model")
		model.no_value_fields = ("Section Break", "Column Break", "HTML", "Table", "Button")
		saved = {name: sys.modules.get(name) for name in ("frappe", "frappe.model")}
		sys.modules["frappe"], sys.modules["frappe.model"] = fake, model
		try:
			import importlib

			module = importlib.import_module(
				"erpnext_enhancements.patches.backfill_carrier_tracking_settings_defaults"
			)
			module = importlib.reload(module)
			count = module.backfill_carrier_tracking_settings_defaults()
		finally:
			for name, value in saved.items():
				if value is None:
					sys.modules.pop(name, None)
				else:
					sys.modules[name] = value
			sys.modules.pop("erpnext_enhancements.patches.backfill_carrier_tracking_settings_defaults", None)
		self.assertNotIn("enabled", written)  # a stored 0 is never overwritten
		self.assertEqual(
			written,
			{
				"fedex_enabled": "0",
				"fedex_environment": "Sandbox",
				"ups_enabled": "0",
				"ups_environment": "Sandbox",
			},
		)
		self.assertEqual(count, 4)


if __name__ == "__main__":
	unittest.main()
