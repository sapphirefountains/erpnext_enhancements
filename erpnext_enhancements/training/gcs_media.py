# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Streaming training video from a private GCS bucket via short-lived signed URLs.

**Why the video is copied out of Drive at all.** A Drive preview frame is
cross-origin: the player cannot read ``currentTime``, cannot ``pause()``, and
cannot observe seeking. That kills in-video checkpoints *and* watch-coverage
measurement — two of the three ways this module checks whether somebody actually
engaged with a lesson. A signed URL on a real ``<video>`` element restores both,
serves correct HTTP byte ranges so seeking works, costs no bench disk or
bandwidth, and behaves identically for a customer who has no Google account.

**Why the signing is hand-rolled.** ``google-cloud-storage`` is not a dependency
and the host is a managed server where packages cannot be pip-installed — the
same constraint that made ``stripe_payments`` talk to Stripe over plain REST.
But ``google-auth`` *is* already a dependency, and a service-account credential
built from it exposes an RSA signer. That is the only primitive a V4 signature
needs; the rest is a documented string-to-sign. So this module mirrors the
QuickBooks and Stripe clients: canonical request assembled by hand, signed with
a library we already have, no new package.

Everything degrades rather than throws. With no bucket configured, or no key, or
a Drive copy that has not happened yet, :func:`signed_url_for_asset` returns
``None`` and the caller falls back to reporting the video as unavailable — a
lesson that cannot play its video is a bad afternoon, but an exception on the
learner's first page load is a support ticket.
"""

import binascii
import datetime
import hashlib
import json
import urllib.parse

import frappe
from frappe import _
from frappe.utils import cint

# GCS's own host. Signed URLs must be built against this exact host because it
# is part of the signed canonical request — pointing at a CNAME would invalidate
# every signature.
GCS_HOST = "storage.googleapis.com"
GCS_ENDPOINT = f"https://{GCS_HOST}"

# V4 is the current signing scheme and the only one that supports the 7-day
# maximum; V2 is deprecated. The literals below are fixed by the spec.
SIGNING_ALGORITHM = "GOOG4-RSA-SHA256"
REQUEST_TYPE = "goog4_request"

# Signed URLs cannot be revoked once minted — they work until they expire even
# if the learner's access is pulled a minute later. The TTL is the mitigation,
# which is why it is a setting rather than a constant, and why it is clamped.
MIN_TTL_MINUTES = 1
MAX_TTL_MINUTES = 60


# --------------------------------------------------------------------- config


def _settings():
	return frappe.get_cached_doc("Training Settings")


def _bucket():
	return (_settings().get("gcs_bucket") or "").strip()


def _credentials():
	"""Service-account credentials from the key stored in Training Settings.

	Returns ``None`` rather than throwing when unconfigured — "video delivery is
	not set up yet" is a normal state during rollout, not an error.
	"""
	raw = _settings().get_password("gcs_service_account_json", raise_exception=False)
	if not raw:
		return None
	try:
		info = json.loads(raw)
	except ValueError:
		frappe.log_error(
			"Training Settings > GCS Service Account JSON is not valid JSON. Paste the whole key file.",
			"Training media",
		)
		return None

	try:
		from google.oauth2 import service_account

		return service_account.Credentials.from_service_account_info(info)
	except Exception:
		frappe.log_error(
			f"Could not build GCS credentials from the stored key\n{frappe.get_traceback()}",
			"Training media",
		)
		return None


def is_configured():
	"""True when a bucket and a usable key are both present."""
	return bool(_bucket()) and _credentials() is not None


def _ttl_seconds():
	minutes = cint(_settings().get("signed_url_ttl_minutes")) or 15
	minutes = max(MIN_TTL_MINUTES, min(MAX_TTL_MINUTES, minutes))
	return minutes * 60


# -------------------------------------------------------------------- signing


def _encode_path(object_name):
	"""Percent-encode an object name for the canonical resource path.

	``safe=""`` on purpose: every reserved character must be escaped, but the
	path separators we add ourselves must not be. Object names here are machine
	generated (see :func:`object_name_for`), so this is belt-and-braces rather
	than the main defence.
	"""
	return "/".join(urllib.parse.quote(part, safe="") for part in object_name.split("/"))


def generate_signed_url(object_name, expires_in=None, method="GET", now=None):
	"""A V4 signed URL for one object, or ``None`` if signing is not configured.

	``now`` is injectable purely so the tests can pin a timestamp; nothing in
	the app passes it.
	"""
	bucket = _bucket()
	credentials = _credentials()
	if not bucket or credentials is None or not object_name:
		return None

	expires_in = cint(expires_in) or _ttl_seconds()
	# The spec caps a V4 signature at seven days. We never go near it, but a
	# caller passing something absurd should be clamped rather than produce a
	# URL that GCS rejects with an opaque error.
	expires_in = max(1, min(expires_in, 7 * 24 * 3600))

	# UTC, always — the timestamp and the date-scoped credential are both part of
	# the signature, so signing against site-local time would produce URLs that
	# validate only when the site happens to be on UTC. (now_datetime() is
	# site-local, which is exactly the trap; do not substitute it here.)
	now = now or datetime.datetime.now(datetime.timezone.utc)
	timestamp = now.strftime("%Y%m%dT%H%M%SZ")
	datestamp = now.strftime("%Y%m%d")

	client_email = getattr(credentials, "service_account_email", None)
	if not client_email:
		return None

	credential_scope = f"{datestamp}/auto/storage/{REQUEST_TYPE}"
	canonical_resource = f"/{bucket}/{_encode_path(object_name)}"

	# Header names in the canonical request must be lowercase and sorted; here
	# there is only one, but the signed-headers string must agree with it
	# exactly or the signature silently fails to match.
	canonical_headers = f"host:{GCS_HOST}\n"
	signed_headers = "host"

	query = {
		"X-Goog-Algorithm": SIGNING_ALGORITHM,
		"X-Goog-Credential": f"{client_email}/{credential_scope}",
		"X-Goog-Date": timestamp,
		"X-Goog-Expires": str(expires_in),
		"X-Goog-SignedHeaders": signed_headers,
	}
	canonical_query = "&".join(
		f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
		for k, v in sorted(query.items())
	)

	canonical_request = "\n".join(
		[
			method,
			canonical_resource,
			canonical_query,
			canonical_headers,
			signed_headers,
			"UNSIGNED-PAYLOAD",
		]
	)

	string_to_sign = "\n".join(
		[
			SIGNING_ALGORITHM,
			timestamp,
			credential_scope,
			hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
		]
	)

	try:
		signature = binascii.hexlify(credentials.signer.sign(string_to_sign)).decode("utf-8")
	except Exception:
		frappe.log_error(
			f"Could not sign a training media URL\n{frappe.get_traceback()}", "Training media"
		)
		return None

	return f"{GCS_ENDPOINT}{canonical_resource}?{canonical_query}&X-Goog-Signature={signature}"


# ------------------------------------------------------------------- objects


def object_name_for(video_asset, extension="mp4"):
	"""Stable object key for a Training Video Asset.

	Keyed on the asset name rather than the course, because one video is
	deliberately reusable across courses — keying on the course would store the
	same bytes several times and make "which file is this really" ambiguous.
	"""
	safe = urllib.parse.quote(str(video_asset), safe="")
	return f"training/{safe}/source.{extension.lstrip('.')}"


def signed_url_for_asset(video_asset):
	"""Playback URL for an asset, or ``None`` when it cannot be served yet.

	Never throws: the learner runtime treats ``None`` as "this video is not
	available" and says so, which is a far better outcome than a traceback on
	somebody's first lesson.
	"""
	if not video_asset or not is_configured():
		return None
	row = frappe.db.get_value(
		"Training Video Asset", video_asset, ["gcs_object", "status"], as_dict=True
	)
	if not row or not row.gcs_object or row.status == "Error":
		return None
	return generate_signed_url(row.gcs_object)


# --------------------------------------------------------------- drive -> gcs


def copy_from_drive(video_asset):
	"""Copy an asset's Drive file into the bucket and stamp the asset.

	Streamed in chunks through a resumable upload rather than read whole: a
	300 MB video buffered into a worker's memory is how a background job takes
	the site down at the least convenient moment.

	Idempotent — an asset that already has a ``gcs_object`` is left alone, so a
	republish does not re-upload gigabytes.
	"""
	if not is_configured():
		return {"ok": False, "reason": "not_configured"}

	asset = frappe.get_doc("Training Video Asset", video_asset)
	if (asset.gcs_object or "").strip():
		return {"ok": True, "reason": "already_copied", "gcs_object": asset.gcs_object}
	if not (asset.drive_file_id or "").strip():
		return {"ok": False, "reason": "no_drive_file"}

	try:
		import io

		from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

		from erpnext_enhancements.google_drive.drive_utils import get_drive_service

		drive, _shared_drive_id = get_drive_service()
		media = drive.files().get_media(fileId=asset.drive_file_id, supportsAllDrives=True)

		buffer = io.BytesIO()
		downloader = MediaIoBaseDownload(buffer, media, chunksize=8 * 1024 * 1024)
		done = False
		while not done:
			_status, done = downloader.next_chunk()
		buffer.seek(0)

		object_name = object_name_for(asset.name, _extension_for(asset.mime_type))
		storage = _storage_service()
		if storage is None:
			return {"ok": False, "reason": "no_storage_client"}

		storage.objects().insert(
			bucket=_bucket(),
			name=object_name,
			media_body=MediaIoBaseUpload(
				buffer, mimetype=asset.mime_type or "video/mp4", chunksize=8 * 1024 * 1024, resumable=True
			),
		).execute()

		frappe.db.set_value(
			"Training Video Asset",
			asset.name,
			{
				"gcs_object": object_name,
				"gcs_synced_on": frappe.utils.now_datetime(),
				"status": "Available",
				"last_error": "",
			},
			update_modified=False,
		)
		return {"ok": True, "gcs_object": object_name}

	except Exception:
		message = frappe.get_traceback()
		frappe.log_error(f"Drive -> GCS copy failed for {video_asset}\n{message}", "Training media")
		frappe.db.set_value(
			"Training Video Asset",
			video_asset,
			{"status": "Error", "last_error": message[:1000]},
			update_modified=False,
		)
		return {"ok": False, "reason": "copy_failed"}


def _extension_for(mime_type):
	return {
		"video/mp4": "mp4",
		"video/webm": "webm",
		"video/quicktime": "mov",
	}.get((mime_type or "").lower(), "mp4")


def _storage_service():
	"""A GCS JSON API client built from the stored key.

	``googleapiclient`` rather than ``google-cloud-storage`` — the former is
	already a dependency and the latter is not installable on this host.
	"""
	credentials = _credentials()
	if credentials is None:
		return None
	try:
		from googleapiclient.discovery import build

		scoped = credentials.with_scopes(["https://www.googleapis.com/auth/devstorage.read_write"])
		return build("storage", "v1", credentials=scoped, cache_discovery=False)
	except Exception:
		frappe.log_error(
			f"Could not build a GCS client\n{frappe.get_traceback()}", "Training media"
		)
		return None


# ------------------------------------------------------------------- key entry


# The keys a Google service-account file must carry. Checked before storing so a
# truncated or mangled paste is rejected at the point of entry.
REQUIRED_KEY_FIELDS = ("type", "project_id", "private_key", "client_email")

EXPECTED_ACCOUNT_PREFIX = "sa-training-media@"


@frappe.whitelist()
def set_service_account_key(key_json):
	"""Store the signing key, after checking it is actually a usable one.

	This exists because the field it writes to is a ``Password``, which Frappe
	renders as a **single-line** masked input — a control that cannot take a
	2 KB multi-line JSON key by paste without mangling or truncating it, and
	which then hides the damage. Storing has to happen through a proper
	multi-line box.

	The validation is the other half of the point. A key that is wrong in a way
	nobody notices does not fail here; it fails much later, as a signed URL that
	returns an opaque 403 on somebody's first lesson. Every check below turns one
	of those into a sentence at the moment of paste.
	"""
	frappe.only_for(("System Manager", "Training Manager"))

	raw = (key_json or "").strip()
	if not raw:
		frappe.throw(_("Paste the service account key first."))

	try:
		info = json.loads(raw)
	except ValueError:
		frappe.throw(
			_("That is not valid JSON. Paste the whole key file, including the opening and closing "
			  "braces — a partial paste is the usual cause.")
		)

	if not isinstance(info, dict):
		frappe.throw(_("That JSON is not a service account key."))

	missing = [f for f in REQUIRED_KEY_FIELDS if not info.get(f)]
	if missing:
		frappe.throw(
			_("The key is missing: {0}. That normally means the paste was cut short.").format(
				", ".join(missing)
			)
		)

	if info.get("type") != "service_account":
		frappe.throw(
			_("That is a {0} key, not a service account key. Create one with "
			  "'gcloud iam service-accounts keys create'.").format(info.get("type"))
		)

	# A private key that lost its newlines is the classic single-line-paste
	# casualty: it looks complete, parses fine, and fails only at signing time.
	private_key = info.get("private_key") or ""
	if "BEGIN PRIVATE KEY" not in private_key or "END PRIVATE KEY" not in private_key:
		frappe.throw(_("The private_key in that file looks incomplete."))

	# Prove it can actually sign, rather than trusting that it parsed.
	try:
		from google.oauth2 import service_account

		credentials = service_account.Credentials.from_service_account_info(info)
		credentials.signer.sign("probe")
	except Exception as exc:
		frappe.throw(_("The key parsed but cannot sign: {0}").format(str(exc)[:200]))

	warning = ""
	email = info.get("client_email") or ""
	if not email.startswith(EXPECTED_ACCOUNT_PREFIX):
		# Not fatal — a differently-named account is a legitimate choice — but
		# pasting the wrong key from a downloads folder is a very easy mistake.
		warning = _("Note: this key belongs to {0}, not the expected sa-training-media account.").format(email)

	settings = frappe.get_doc("Training Settings")
	settings.gcs_service_account_json = raw
	settings.gcs_key_status = _("{0} · stored {1}").format(email, frappe.utils.nowdate())
	settings.save(ignore_permissions=True)
	frappe.db.commit()

	# The cached Single still holds the old value; the next signing call would
	# otherwise use a stale key.
	frappe.clear_document_cache("Training Settings", "Training Settings")

	return {"ok": True, "client_email": email, "project_id": info.get("project_id"), "warning": warning}


# ---------------------------------------------------------------- diagnostics


def _describe(exc):
	"""A human-readable account of an exception, and never an empty string.

	``str(exc)`` alone is not enough. Several of the exceptions this module can
	raise carry no message at all — a bare ``TimeoutError()`` is the obvious one —
	and the result was a dialog reading "Upload failed:" with nothing after the
	colon, which tells an operator strictly less than saying nothing would have.
	So: the class name always, the message when there is one, and the HTTP status
	dug out of a googleapiclient error, since that is the single most diagnostic
	fact available (404 wrong bucket, 403 missing IAM, 401 bad key).

	The full traceback goes to the Error Log, because the dialog should stay
	short and somebody will want the detail afterwards.
	"""
	parts = [type(exc).__name__]

	status = getattr(getattr(exc, "resp", None), "status", None)
	if status:
		parts.append(
			{
				400: _("400 Bad Request"),
				401: _("401 Unauthorized — the stored key was rejected."),
				403: _("403 Forbidden — the service account lacks objectAdmin on this bucket, "
					   "or the IAM binding has not propagated yet (it can take a minute)."),
				404: _("404 Not Found — no bucket of that name exists in this project. "
					   "Check GCS Video Bucket is the *bucket* name, not the service account."),
			}.get(status, _("HTTP {0}").format(status))
		)

	message = (str(exc) or "").strip()
	if message:
		parts.append(message[:300])
	elif not status:
		parts.append(_("The error carried no message; see the Error Log for the traceback."))

	try:
		frappe.log_error(
			f"{type(exc).__name__}: {message or '(no message)'}\n\n{frappe.get_traceback()}",
			"Training media",
		)
	except Exception:
		pass

	return " ".join(parts)


def _bucket_name_complaint(bucket):
	"""Reject a value that is obviously not a bucket name, and say why.

	The runbook prints the bucket and the service account as adjacent Terraform
	outputs, so copying the wrong one is a genuinely easy mistake — and it did
	happen. Without this the only symptom is a 404 several steps later.
	"""
	if "@" in bucket or bucket.endswith(".iam.gserviceaccount.com"):
		return _("{0} is a service account, not a bucket. Put the bucket name in GCS Video Bucket "
				 "and the key itself in Set Service Account Key.").format(bucket)
	if bucket.startswith("sa-"):
		return _("{0} looks like a service account name. The bucket from the Terraform output is "
				 "the one starting 'sf-'.").format(bucket)
	if not 3 <= len(bucket) <= 222 or bucket.strip() != bucket:
		return _("{0} is not a valid bucket name.").format(bucket)
	return ""


@frappe.whitelist()
def test_connection():
	"""Write a probe object, sign it, fetch it, delete it.

	Deliberately exercises the whole chain rather than just parsing the key: a
	pass here means credentials, IAM, signing, CORS-independent egress and
	object lifecycle all genuinely work. Step 5 of the runbook.
	"""
	frappe.only_for(("System Manager", "Training Manager"))

	bucket = _bucket()
	if not bucket:
		return {"ok": False, "message": _("No bucket is set in Training Settings.")}

	misnamed = _bucket_name_complaint(bucket)
	if misnamed:
		return {"ok": False, "message": misnamed}

	if _credentials() is None:
		return {"ok": False, "message": _("No usable service account key is stored in Training Settings.")}

	storage = _storage_service()
	if storage is None:
		return {"ok": False, "message": _("Could not build a storage client from the stored key.")}

	# Check the bucket resolves before trying to write to it. Without this, a
	# wrong name surfaces as a generic upload failure and the operator is left
	# guessing between "bucket missing", "no permission" and "no network".
	try:
		storage.buckets().get(bucket=bucket).execute()
	except Exception as exc:
		return {"ok": False, "message": _("Cannot reach the bucket {0}. {1}").format(bucket, _describe(exc))}

	import io

	from googleapiclient.http import MediaIoBaseUpload

	probe = f"training/_probe/{frappe.generate_hash(length=12)}.txt"
	# Long enough to ask for a byte range out of the middle. The Range check below
	# is the whole reason this is not a five-byte payload.
	payload = b"training media probe " * 8

	try:
		storage.objects().insert(
			bucket=bucket,
			name=probe,
			media_body=MediaIoBaseUpload(io.BytesIO(payload), mimetype="text/plain"),
		).execute()
	except Exception as exc:
		return {"ok": False, "message": _("Upload failed. {0}").format(_describe(exc))}

	url = generate_signed_url(probe, expires_in=120)
	fetched = False
	ranged = None
	try:
		import requests

		response = requests.get(url, timeout=20)
		fetched = response.status_code == 200 and response.content == payload

		# THE RANGE CHECK. Seeking in a <video> is a byte-range request, and the
		# entire watch-coverage model assumes the server honours it: if a seek
		# returns 200 with the whole file instead of 206 with a slice, the player
		# still *works* but the numbers it reports are wrong rather than absent —
		# which is the worst failure mode this feature has. Verified here so it is
		# answered without anybody having to author a course first.
		if fetched:
			probe_range = requests.get(url, headers={"Range": "bytes=10-19"}, timeout=20)
			ranged = {
				"status": probe_range.status_code,
				"content_range": probe_range.headers.get("Content-Range"),
				"accept_ranges": probe_range.headers.get("Accept-Ranges"),
				"ok": probe_range.status_code == 206 and probe_range.content == payload[10:20],
			}
	except Exception:
		fetched = False
	finally:
		try:
			storage.objects().delete(bucket=_bucket(), object=probe).execute()
		except Exception:
			# A stray probe object is harmless; failing to clean it up must not
			# turn a successful test into a reported failure.
			pass

	if not url:
		return {"ok": False, "message": _("Upload worked, but the URL could not be signed.")}
	if not fetched:
		return {
			"ok": False,
			"message": _("Upload and signing worked, but fetching the signed URL did not. "
						 "Check that the service account has objectAdmin on the bucket."),
		}

	# A failed Range check is reported but does NOT fail the test: everything
	# except video seeking still works, and telling an operator "connection
	# failed" when the connection is fine would send them looking in the wrong
	# place. It is surfaced loudly instead, because it silently corrupts coverage
	# rather than breaking anything visibly.
	if ranged and not ranged.get("ok"):
		return {
			"ok": True,
			"range_ok": False,
			"message": _(
				"Bucket, key and signing all work — but the byte-range check did NOT pass "
				"(got HTTP {0}, Content-Range {1}). Seeking in a video is a range request, and "
				"watch coverage assumes it is honoured. Until this passes, treat any coverage "
				"figure as unreliable rather than merely missing."
			).format(ranged.get("status"), ranged.get("content_range") or _("absent")),
		}

	return {
		"ok": True,
		"range_ok": bool(ranged and ranged.get("ok")),
		"message": _("Bucket, key, signing, playback and byte-range seeking all check out."),
	}
