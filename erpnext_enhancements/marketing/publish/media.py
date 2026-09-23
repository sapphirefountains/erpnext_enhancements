# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A publicly reachable URL for each Marketing Media Asset (TASK-2026-01483).

Meta fetches a post's media **from a URL, at the moment the post is created**, and it cannot reach
anything behind ERPNext's login. So every asset must resolve to a URL the network can fetch, and one
that cannot is refused when the post is checked -- before approval -- not at publish time:

* **File, public** (``/files/...``): the site URL plus the file path. Public for as long as the file
  exists, which is what whoever uploaded it chose.
* **File, private** (``/private/files/...``): not reachable. Refused, with the fix in the message.
* **Google Cloud Storage** (``bucket/path`` or ``gs://bucket/path``): a V4 **signed** URL that expires
  after ``PUBLISH_URL_TTL_SECONDS``, from the same hand-rolled signer and key the training module uses
  (``training/gcs_media.py``; no SDK can be installed on the host). Long enough for Instagram to fetch
  and process a video, short enough that the link dies soon after. The bucket stays private.
* **Google Drive:** not reachable without sharing the file with the whole internet. Refused.

``url_problem`` is pure; ``public_url`` touches frappe only to build the site URL and to sign.
"""

#: Six hours: Instagram fetches a video during processing, which can take minutes, and the job may
#: wait for a retry. Nothing longer: a signed URL cannot be revoked.
PUBLISH_URL_TTL_SECONDS = 6 * 3600


class MediaNotReachable(Exception):
	"""An asset has no URL a network could fetch."""


def split_gcs(value):
	"""``(bucket, object)`` from ``bucket/path/to/object`` or ``gs://bucket/path``; None if malformed. Pure."""
	text = (value or "").strip()
	if text.startswith("gs://"):
		text = text[len("gs://") :]
	bucket, _, name = text.partition("/")
	if not bucket or not name:
		return None
	return bucket, name


def url_problem(asset):
	"""Why ``asset`` cannot be fetched by a network, or None if it can. Pure.

	``asset`` needs ``source`` plus ``file``, ``gcs_object`` or ``drive_file_id``.
	"""
	name = asset.get("name") or asset.get("title") or "An asset"
	source = asset.get("source") or "File"
	if source == "File":
		path = (asset.get("file") or "").strip()
		if not path:
			return f"{name} has no file attached"
		if path.startswith("/private/"):
			return (
				f"{name} is a private file, which Meta cannot fetch. Re-upload it as a public file, "
				"or store it in Google Cloud Storage"
			)
		if not path.startswith(("/files/", "http://", "https://")):
			return f"{name} has a file path that is not a web address: {path}"
		if path.startswith("http://"):
			return f"{name} is served over plain HTTP; the networks fetch HTTPS only"
		return None
	if source == "Google Cloud Storage":
		return (
			None
			if split_gcs(asset.get("gcs_object"))
			else f"{name} has no GCS object in the form bucket/path"
		)
	if source == "Google Drive":
		return (
			f"{name} is on Google Drive, which the networks cannot fetch without sharing it publicly. "
			"Upload it as a public file or to Google Cloud Storage instead"
		)
	return f"{name} has an unknown source {source!r}"


def public_url(asset):
	"""The URL a network fetches ``asset`` from. Raises ``MediaNotReachable`` with the reason."""
	problem = url_problem(asset)
	if problem:
		raise MediaNotReachable(problem)
	source = asset.get("source") or "File"
	if source == "File":
		path = asset["file"].strip()
		if path.startswith("https://"):
			return path
		from frappe.utils import get_url

		return get_url(path)
	bucket, name = split_gcs(asset.get("gcs_object"))
	from erpnext_enhancements.training.gcs_media import generate_signed_url

	url = generate_signed_url(name, expires_in=PUBLISH_URL_TTL_SECONDS, bucket=bucket)
	if not url:
		raise MediaNotReachable(
			f"{asset.get('name') or 'An asset'}: Google Cloud Storage signing is not configured "
			"(Training Settings holds the key)"
		)
	return url
