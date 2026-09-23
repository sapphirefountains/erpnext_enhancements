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


def bytes_problem(asset):
	"""Why this app cannot read ``asset``'s bytes to upload them itself (LinkedIn), or None. Pure.

	LinkedIn never fetches a URL: we register an upload and PUT the file. So, unlike for Meta, a
	**private** file is fine -- it is read from the site's own disk. A GCS object is read through a
	signed URL to our own bucket. An external https URL is refused: fetching arbitrary addresses
	from the server is how a server gets turned against its own network.
	"""
	name = asset.get("name") or asset.get("title") or "An asset"
	source = asset.get("source") or "File"
	if source == "File":
		path = (asset.get("file") or "").strip()
		if not path:
			return f"{name} has no file attached"
		if not path.startswith(("/files/", "/private/files/")):
			return f"{name} is not a file on this site ({path}); upload it here, or store it in Google Cloud Storage"
		return None
	if source == "Google Cloud Storage":
		return (
			None
			if split_gcs(asset.get("gcs_object"))
			else f"{name} has no GCS object in the form bucket/path"
		)
	if source == "Google Drive":
		return f"{name} is on Google Drive, which this app does not read. Upload it here or to Google Cloud Storage"
	return f"{name} has an unknown source {source!r}"


def read_bytes(asset, http=None):
	"""The file's bytes, for a network that takes an upload rather than a URL."""
	problem = bytes_problem(asset)
	if problem:
		raise MediaNotReachable(problem)
	if (asset.get("source") or "File") == "File":
		import frappe

		file_name = frappe.db.get_value("File", {"file_url": asset["file"].strip()}, "name")
		if not file_name:
			raise MediaNotReachable(f"{asset.get('name') or 'An asset'}: no File record for {asset['file']}")
		return frappe.get_doc("File", file_name).get_content()
	url = public_url(asset)  # a short-lived signed URL to our own bucket
	if http is None:
		import requests

		http = requests
	# No bearer token here, deliberately: this reads our own bucket, not a network's API.
	response = http.get(url, timeout=120)
	if response.status_code >= 400:
		raise MediaNotReachable(
			f"{asset.get('name') or 'An asset'}: Google Cloud Storage answered {response.status_code}"
		)
	return response.content


class ByteSource:
	"""A file read in pieces: ``size``, and ``read(start, end)`` for bytes ``start..end`` inclusive.

	For a video too large to hold in memory (YouTube, TASK-2026-01485): a local file is read from
	disk by seeking, a GCS object by ranged GETs on a signed URL -- never the whole thing at once.
	"""

	def __init__(self, size, reader):
		self.size = size
		self._reader = reader

	def read(self, start, end):
		return self._reader(start, end)


def byte_source(asset, http=None):
	"""A ``ByteSource`` for ``asset``. Raises ``MediaNotReachable`` with the reason."""
	problem = bytes_problem(asset)
	if problem:
		raise MediaNotReachable(problem)
	name = asset.get("name") or "An asset"
	if (asset.get("source") or "File") == "File":
		import os

		import frappe

		file_name = frappe.db.get_value("File", {"file_url": asset["file"].strip()}, "name")
		if not file_name:
			raise MediaNotReachable(f"{name}: no File record for {asset['file']}")
		path = frappe.get_doc("File", file_name).get_full_path()

		def read_local(start, end):
			with open(path, "rb") as handle:
				handle.seek(start)
				return handle.read(end - start + 1)

		return ByteSource(os.path.getsize(path), read_local)

	url = public_url(asset)
	if http is None:
		import requests

		http = requests

	def read_range(start, end):
		# No bearer token: this reads our own bucket through a signed URL.
		response = http.get(url, headers={"Range": f"bytes={start}-{end}"}, timeout=300)
		if response.status_code not in (200, 206):
			raise MediaNotReachable(f"{name}: Google Cloud Storage answered {response.status_code}")
		return response.content

	probe = http.get(url, headers={"Range": "bytes=0-0"}, timeout=60)
	total = (probe.headers.get("Content-Range") or "").rpartition("/")[2]
	if probe.status_code not in (200, 206) or not total.isdigit():
		raise MediaNotReachable(f"{name}: could not read its size from Google Cloud Storage")
	return ByteSource(int(total), read_range)


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
