"""Bench-free unit tests for the Training media V4 signing.

Stubs a minimal ``frappe`` (no site/bench) so ``training.gcs_media`` runs under
plain unittest. Installed in ``setUpModule`` (execution time), not at import, so
it never fools the bench-only suites' ``import frappe`` skip-guards.

V4 signing is hand-rolled here because ``google-cloud-storage`` cannot be
installed on the host, and hand-rolled signing has exactly one failure mode
worth guarding: a signature that is subtly wrong produces a perfectly
well-formed URL that GCS rejects with an opaque 403. You cannot tell by looking
at it. So these assert the *canonical* parts of the spec rather than the output
of the implementation:

  * the string-to-sign is rebuilt independently, from the spec, and compared;
  * the signed-headers list and the canonical header block agree (they must, or
    the signature never matches);
  * query parameters are sorted and percent-encoded as the spec requires;
  * the expiry is clamped to the 7-day maximum and to the configured floor;
  * an unconfigured or broken install returns ``None`` rather than throwing,
    because a traceback on a learner's first lesson is worse than a lesson that
    reports its video as unavailable.

Run: python -m unittest erpnext_enhancements.tests.test_training_gcs_media
"""

import datetime
import hashlib
import pathlib
import sys
import types
import unittest
import urllib.parse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

gcs_media = None

STATE = {}

FAKE_EMAIL = "sa-training-media@erpnext-465317.iam.gserviceaccount.com"


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError as exc:
			raise AttributeError(key) from exc

	def __setattr__(self, key, value):
		self[key] = value


class _StubSigner:
	"""Deterministic stand-in for an RSA signer.

	Returns the SHA-256 of the string-to-sign, so a test can recompute exactly
	what *should* have been signed and compare. That is the whole point: it
	checks the canonical request, not the crypto.
	"""

	def sign(self, message):
		if isinstance(message, str):
			message = message.encode("utf-8")
		STATE["last_signed"] = message.decode("utf-8")
		return hashlib.sha256(message).digest()


class _StubCredentials:
	service_account_email = FAKE_EMAIL

	def __init__(self):
		self.signer = _StubSigner()

	def with_scopes(self, scopes):
		return self


class _StubSettings(_Dict):
	def get_password(self, fieldname, raise_exception=True):
		return STATE.get("key_json")


def _reset_state():
	STATE.clear()
	STATE.update(
		{
			"bucket": "sf-erpnext-training-media",
			"key_json": '{"type":"service_account"}',
			"ttl_minutes": 15,
			"assets": {},
			"errors": [],
			"credentials_ok": True,
		}
	)


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.session = _Dict(user="tester@example.com")
	frappe.flags = _Dict()
	frappe.log_error = lambda *a, **k: STATE["errors"].append(a[0] if a else "")
	frappe.get_traceback = lambda: "traceback"
	frappe.generate_hash = lambda length=10: "h" * length
	frappe.only_for = lambda *a, **k: None
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)

	def _cached_doc(doctype, name=None):
		return _StubSettings(
			gcs_bucket=STATE["bucket"], signed_url_ttl_minutes=STATE["ttl_minutes"]
		)

	frappe.get_cached_doc = _cached_doc
	frappe.get_doc = lambda doctype, name=None: _Dict(STATE["assets"].get(name, {}))

	def _get_value(doctype, name, fields=None, as_dict=False, **kwargs):
		row = STATE["assets"].get(name)
		if not row:
			return None
		return _Dict(row) if as_dict else row

	frappe.db = types.SimpleNamespace(
		get_value=_get_value, set_value=lambda *a, **k: None, exists=lambda *a, **k: None
	)

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	utils.now_datetime = lambda: "2026-08-01 12:00:00"
	frappe.utils = utils
	frappe.__dict__["_"] = lambda s: s

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils

	# google.oauth2.service_account — stubbed so no real key is ever needed.
	google = types.ModuleType("google")
	oauth2 = types.ModuleType("google.oauth2")
	service_account = types.ModuleType("google.oauth2.service_account")

	class Credentials:
		@staticmethod
		def from_service_account_info(info):
			if not STATE.get("credentials_ok"):
				raise ValueError("bad key")
			return _StubCredentials()

	service_account.Credentials = Credentials
	oauth2.service_account = service_account
	google.oauth2 = oauth2
	sys.modules["google"] = google
	sys.modules["google.oauth2"] = oauth2
	sys.modules["google.oauth2.service_account"] = service_account


def setUpModule():
	global gcs_media
	_install_frappe_stub()
	_reset_state()
	from erpnext_enhancements.training import gcs_media as module

	gcs_media = module


FIXED_NOW = datetime.datetime(2026, 8, 1, 12, 0, 0)


def _parse(url):
	parsed = urllib.parse.urlparse(url)
	return parsed, dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))


class TestSignedUrlShape(unittest.TestCase):
	def setUp(self):
		_reset_state()
		self.url = gcs_media.generate_signed_url("training/TRN-VID-00001/source.mp4", now=FIXED_NOW)

	def test_points_at_the_gcs_host(self):
		"""Must be storage.googleapis.com — the host is part of the signed
		canonical request, so a CNAME would invalidate every signature."""
		parsed, _q = _parse(self.url)
		self.assertEqual(parsed.netloc, "storage.googleapis.com")
		self.assertEqual(parsed.scheme, "https")

	def test_path_is_bucket_then_object(self):
		parsed, _q = _parse(self.url)
		self.assertEqual(parsed.path, "/sf-erpnext-training-media/training/TRN-VID-00001/source.mp4")

	def test_carries_every_required_v4_parameter(self):
		_p, q = _parse(self.url)
		for key in (
			"X-Goog-Algorithm",
			"X-Goog-Credential",
			"X-Goog-Date",
			"X-Goog-Expires",
			"X-Goog-SignedHeaders",
			"X-Goog-Signature",
		):
			self.assertIn(key, q)
		self.assertEqual(q["X-Goog-Algorithm"], "GOOG4-RSA-SHA256")
		self.assertEqual(q["X-Goog-SignedHeaders"], "host")

	def test_credential_scope_is_date_slash_auto_storage(self):
		_p, q = _parse(self.url)
		self.assertEqual(q["X-Goog-Credential"], f"{FAKE_EMAIL}/20260801/auto/storage/goog4_request")

	def test_expiry_defaults_to_the_configured_ttl(self):
		_p, q = _parse(self.url)
		self.assertEqual(q["X-Goog-Expires"], str(15 * 60))

	def test_object_path_separators_survive_encoding(self):
		"""Reserved characters must be escaped, but the slashes we add ourselves
		must not — encoding them would change the object being addressed."""
		url = gcs_media.generate_signed_url("training/a b/c+d.mp4", now=FIXED_NOW)
		parsed, _q = _parse(url)
		self.assertEqual(parsed.path, "/sf-erpnext-training-media/training/a%20b/c%2Bd.mp4")


class TestStringToSign(unittest.TestCase):
	"""Rebuild the string-to-sign independently from the spec and compare.

	This is the test that actually matters. A signature that is subtly wrong
	still produces a well-formed URL; the only symptom is an opaque 403 from
	GCS, which is impossible to diagnose by inspection.
	"""

	def setUp(self):
		_reset_state()
		self.object_name = "training/TRN-VID-00001/source.mp4"
		self.url = gcs_media.generate_signed_url(self.object_name, now=FIXED_NOW)
		self.signed = STATE["last_signed"]

	def _expected(self):
		timestamp = "20260801T120000Z"
		scope = "20260801/auto/storage/goog4_request"
		canonical_query = "&".join(
			[
				"X-Goog-Algorithm=GOOG4-RSA-SHA256",
				"X-Goog-Credential=" + urllib.parse.quote(f"{FAKE_EMAIL}/{scope}", safe=""),
				f"X-Goog-Date={timestamp}",
				"X-Goog-Expires=900",
				"X-Goog-SignedHeaders=host",
			]
		)
		canonical_request = "\n".join(
			[
				"GET",
				f"/sf-erpnext-training-media/{self.object_name}",
				canonical_query,
				"host:storage.googleapis.com\n",
				"host",
				"UNSIGNED-PAYLOAD",
			]
		)
		return "\n".join(
			[
				"GOOG4-RSA-SHA256",
				timestamp,
				scope,
				hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
			]
		)

	def test_signs_exactly_the_spec_string(self):
		self.assertEqual(self.signed, self._expected())

	def test_query_parameters_are_sorted(self):
		"""V4 requires the canonical query sorted by key. Unsorted still looks
		fine in a browser address bar and never validates."""
		line = self.signed.split("\n")
		self.assertTrue(line[0].startswith("GOOG4"))
		# The canonical request is hashed, so verify sorting via the URL itself.
		_p, _q = _parse(self.url)
		keys = [
			p.split("=")[0]
			for p in urllib.parse.urlparse(self.url).query.split("&")
			if p.split("=")[0] != "X-Goog-Signature"
		]
		self.assertEqual(keys, sorted(keys))

	def test_signed_headers_matches_the_canonical_header_block(self):
		_p, q = _parse(self.url)
		self.assertEqual(q["X-Goog-SignedHeaders"], "host")


class TestExpiryClamping(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def test_seven_day_maximum_is_enforced(self):
		"""The spec caps V4 at 7 days; beyond it GCS rejects with an opaque
		error, so clamp rather than emit something that cannot work."""
		url = gcs_media.generate_signed_url("o.mp4", expires_in=30 * 24 * 3600, now=FIXED_NOW)
		_p, q = _parse(url)
		self.assertEqual(q["X-Goog-Expires"], str(7 * 24 * 3600))

	def test_configured_ttl_is_clamped_to_the_hour_ceiling(self):
		STATE["ttl_minutes"] = 999
		url = gcs_media.generate_signed_url("o.mp4", now=FIXED_NOW)
		_p, q = _parse(url)
		self.assertEqual(q["X-Goog-Expires"], str(60 * 60))

	def test_zero_or_missing_ttl_falls_back_to_the_default(self):
		STATE["ttl_minutes"] = 0
		url = gcs_media.generate_signed_url("o.mp4", now=FIXED_NOW)
		_p, q = _parse(url)
		self.assertEqual(q["X-Goog-Expires"], str(15 * 60))


class TestDegradesRatherThanThrows(unittest.TestCase):
	"""An unconfigured or broken install must report 'no video' — never raise.

	A traceback on a learner's first lesson is a support ticket; a lesson saying
	its video is unavailable is a message to an admin.
	"""

	def setUp(self):
		_reset_state()

	def test_no_bucket_returns_none(self):
		STATE["bucket"] = ""
		self.assertIsNone(gcs_media.generate_signed_url("o.mp4", now=FIXED_NOW))
		self.assertFalse(gcs_media.is_configured())

	def test_no_key_returns_none(self):
		STATE["key_json"] = None
		self.assertIsNone(gcs_media.generate_signed_url("o.mp4", now=FIXED_NOW))
		self.assertFalse(gcs_media.is_configured())

	def test_malformed_key_json_is_logged_not_raised(self):
		STATE["key_json"] = "{not json"
		self.assertIsNone(gcs_media.generate_signed_url("o.mp4", now=FIXED_NOW))
		self.assertTrue(STATE["errors"])

	def test_unusable_key_is_logged_not_raised(self):
		STATE["credentials_ok"] = False
		self.assertIsNone(gcs_media.generate_signed_url("o.mp4", now=FIXED_NOW))
		self.assertTrue(STATE["errors"])

	def test_empty_object_name_returns_none(self):
		self.assertIsNone(gcs_media.generate_signed_url("", now=FIXED_NOW))


class TestAssetResolution(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def test_asset_not_yet_copied_returns_none(self):
		"""Published before the copy job ran. Not an error — just not ready."""
		STATE["assets"]["TRN-VID-00001"] = {"gcs_object": "", "status": "Draft"}
		self.assertIsNone(gcs_media.signed_url_for_asset("TRN-VID-00001"))

	def test_asset_in_error_returns_none(self):
		STATE["assets"]["TRN-VID-00001"] = {
			"gcs_object": "training/TRN-VID-00001/source.mp4",
			"status": "Error",
		}
		self.assertIsNone(gcs_media.signed_url_for_asset("TRN-VID-00001"))

	def test_available_asset_signs(self):
		STATE["assets"]["TRN-VID-00001"] = {
			"gcs_object": "training/TRN-VID-00001/source.mp4",
			"status": "Available",
		}
		url = gcs_media.signed_url_for_asset("TRN-VID-00001")
		self.assertIn("X-Goog-Signature=", url)

	def test_unknown_asset_returns_none(self):
		self.assertIsNone(gcs_media.signed_url_for_asset("TRN-VID-99999"))

	def test_object_name_is_keyed_on_the_asset_not_the_course(self):
		"""One video is deliberately reusable across courses; keying on the
		course would store the same bytes several times."""
		self.assertEqual(
			gcs_media.object_name_for("TRN-VID-00001"), "training/TRN-VID-00001/source.mp4"
		)
		self.assertEqual(
			gcs_media.object_name_for("TRN-VID-00002", "webm"), "training/TRN-VID-00002/source.webm"
		)


class TestBucketNameComplaints(unittest.TestCase):
	"""The bucket field rejecting a service account pasted into it.

	This is not hypothetical. The runbook prints the bucket name and the service
	account email as adjacent Terraform outputs, and on the first real setup the
	service account went into the bucket field. The only symptom was a 404 from
	Test GCS Connection several steps later, which reads as "the integration is
	broken" rather than "one field is wrong".
	"""

	def setUp(self):
		_reset_state()

	def test_the_exact_mistake_that_happened(self):
		complaint = gcs_media._bucket_name_complaint("sa-training-media")
		self.assertTrue(complaint)
		self.assertIn("service account", complaint)

	def test_full_service_account_email_is_rejected(self):
		complaint = gcs_media._bucket_name_complaint(
			"sa-training-media@erpnext-465317.iam.gserviceaccount.com"
		)
		self.assertTrue(complaint)

	def test_the_real_bucket_name_is_accepted(self):
		self.assertEqual(gcs_media._bucket_name_complaint("sf-erpnext-training-media"), "")

	def test_untrimmed_value_is_rejected(self):
		"""A trailing space from a copy-paste produces a 404 that looks identical
		to a missing bucket."""
		self.assertTrue(gcs_media._bucket_name_complaint("sf-erpnext-training-media "))

	def test_absurdly_short_name_is_rejected(self):
		self.assertTrue(gcs_media._bucket_name_complaint("ab"))


class TestErrorDescriptionIsNeverEmpty(unittest.TestCase):
	"""Guards the dialog that read "Upload failed:" with nothing after the colon.

	str(exc) alone is not enough — several exceptions here carry no message at
	all, and an empty reason tells an operator less than saying nothing would.
	"""

	def setUp(self):
		_reset_state()

	def test_message_less_exception_still_describes_itself(self):
		self.assertIn("TimeoutError", gcs_media._describe(TimeoutError()))

	def test_never_returns_empty(self):
		for exc in (TimeoutError(), ValueError(), Exception(), OSError()):
			self.assertTrue(gcs_media._describe(exc).strip())

	def test_includes_the_exception_type(self):
		self.assertIn("ValueError", gcs_media._describe(ValueError("boom")))

	def test_includes_the_message_when_there_is_one(self):
		self.assertIn("boom", gcs_media._describe(ValueError("boom")))

	def test_404_is_translated_into_the_likely_cause(self):
		"""The single most diagnostic fact available, and the one that would have
		named this bug on sight."""
		exc = Exception()
		exc.resp = types.SimpleNamespace(status=404)
		described = gcs_media._describe(exc)
		self.assertIn("404", described)
		self.assertIn("bucket", described.lower())

	def test_403_points_at_iam_rather_than_the_key(self):
		exc = Exception()
		exc.resp = types.SimpleNamespace(status=403)
		self.assertIn("objectAdmin", gcs_media._describe(exc))

	def test_401_points_at_the_key(self):
		exc = Exception()
		exc.resp = types.SimpleNamespace(status=401)
		self.assertIn("401", gcs_media._describe(exc))

	def test_traceback_goes_to_the_error_log(self):
		"""The dialog stays short; the detail has to survive somewhere."""
		STATE["errors"].clear()
		gcs_media._describe(TimeoutError())
		self.assertTrue(STATE["errors"])


if __name__ == "__main__":
	unittest.main()


class TestConnectionTestNeedsOnlyWhatTheAppNeeds(unittest.TestCase):
	"""The pre-flight must not demand a permission the module never uses.

	``test_connection`` used to probe with ``buckets().get()``, which requires
	``storage.buckets.get`` — and **``roles/storage.objectAdmin`` does not grant
	it**. So a service account configured exactly as this module documents failed
	the connection test with a 403, and ``_describe`` then advised granting
	objectAdmin: the role it already had. Following our own error message would
	have changed nothing.

	The module only ever creates, reads and deletes objects. A connection test
	that asks for more turns a working configuration into a failing one.
	"""

	@staticmethod
	def _source():
		return (
			pathlib.Path(__file__).resolve().parents[1] / "training/gcs_media.py"
		).read_text(encoding="utf-8")

	@staticmethod
	def _func(name):
		"""One top-level function's source, safe at end-of-file.

		`source.index("
def ", start)` raises when the function is the last in the
		file — which `test_connection` is — so the first version of these tests
		errored rather than asserting anything.
		"""
		source = TestConnectionTestNeedsOnlyWhatTheAppNeeds._source()
		start = source.index(f"def {name}(")
		nxt = source.find("\ndef ", start + 5)
		return source[start : nxt if nxt != -1 else len(source)]

	def test_the_preflight_does_not_call_buckets_get(self):
		self.assertNotIn("buckets().get(", self._func("test_connection"))

	def test_the_preflight_still_distinguishes_the_three_cases(self):
		"""A wrong name, a missing binding and no network must stay tellable
		apart — that is why a pre-flight exists at all."""
		body = self._func("test_connection")
		self.assertIn("objects().list(", body)
		self.assertIn("_describe(exc)", body)

	def test_the_403_names_a_role_that_would_actually_help(self):
		body = self._func("_describe")
		self.assertIn("roles/storage.objectAdmin", body)
		self.assertIn("on this bucket", body)

	def test_nothing_else_reaches_for_bucket_metadata(self):
		"""The whole module should stay inside object permissions."""
		source = self._source()
		code = "\n".join(
			line for line in source.splitlines() if not line.strip().startswith("#")
		)
		self.assertNotIn("buckets().get(", code)
