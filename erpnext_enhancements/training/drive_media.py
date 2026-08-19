# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Hourly health check for training video assets — the verifier the doctype was built around.

``TrainingVideoAsset._derive_status`` has always deferred to this module by name:
it promotes Draft to Available and then explicitly *refuses* to touch ``Missing``
or ``Error``, on the stated grounds that "the hourly check in
``training/drive_media.py`` is what sets Missing / Error". That file did not
exist. Neither did anything else that wrote ``last_verified_on``, a field
declared on the doctype since v1.207.0 and read by nobody.

The consequence was not cosmetic. Nothing ever moved an asset out of
``Available``, so a video whose GCS object had been deleted — or whose copy never
really landed — stayed green in the authoring UI and failed in front of a learner
at play time. The one asset on production carried ``size_bytes = 0`` and a null
``mime_type`` for sixteen days while the object itself was a perfectly healthy
24 MB ``video/mp4``; there was no mechanism that could have noticed either way.

**What this does.** Stats the object behind each asset, stamps
``last_verified_on``, and repairs ``size_bytes`` / ``mime_type`` from what GCS
actually holds — so the record converges on the truth however the row was first
created. Assets registered by hand in the Desk, rather than through
``api.training_author.register_video_asset``, arrive with those fields empty and
are healed on the next pass.

**Absent is not the same as unreachable, and the difference is the whole point.**
A 404 is GCS telling us the object is gone: that is a fact, and it earns
``Missing``. Any other failure — a timeout, a 5xx, an expired key, a DNS blip —
means we could not ask. Marking a healthy video ``Missing`` because a scheduled
job had a bad thirty seconds would be worse than saying nothing: an author would
go looking for a file that is exactly where they left it. So an inconclusive
check records ``last_error`` and leaves the status alone.

Work is bounded two ways, because an hourly job that grows with the library is a
future incident: assets are re-checked no more often than
``VERIFY_INTERVAL_HOURS``, and no more than ``MAX_PER_RUN`` are examined in a
single pass. Oldest-verified first, so the backlog drains in order and nothing
starves.
"""

import frappe
from frappe.utils import add_to_date, cint, now_datetime

from erpnext_enhancements.training import gcs_media

# How stale a verification may get before it is redone. Twelve hours means every
# asset is checked twice a day while any single run stays small.
VERIFY_INTERVAL_HOURS = 12

# Ceiling on one run. At one metadata call per asset this is cheap, but the job
# runs unattended forever and the library only grows; an unbounded loop over a
# remote API is the kind of thing that is fine until the afternoon it is not.
MAX_PER_RUN = 200


def verify_video_assets():
	"""Scheduled entry point. Re-verify the assets whose check is oldest."""
	if not gcs_media.is_configured():
		return

	storage = gcs_media._storage_service()
	bucket = gcs_media._bucket()
	if storage is None or not bucket:
		return

	for row in _due_for_verification():
		_verify_one(storage, bucket, row)


def _due_for_verification():
	"""Assets with a GCS object, never verified or verified longest ago.

	Ordering by ``last_verified_on`` ascending puts nulls first in MariaDB, which
	is exactly the wanted order: something never checked is more interesting than
	something checked yesterday.
	"""
	cutoff = add_to_date(now_datetime(), hours=-VERIFY_INTERVAL_HOURS)
	# Stated as or_filters on purpose, and the reason is not the obvious one.
	#
	# Frappe composes these as `<filters> AND (<or_filters joined by OR>)` --
	# verified on prod: a false `filters` clause with a true `or_filters` clause
	# returns nothing. So the two predicates below could NOT both live in `filters`.
	#
	# The subtler point is that `last_verified_on < cutoff` would, today, match
	# never-verified rows all by itself. Frappe does not emit a bare comparison; it
	# emits `coalesce(`last_verified_on`, '0001-01-01') < '<cutoff>'`, and year 1 is
	# less than any cutoff. That is an undocumented internal, it is the only thing
	# standing between this job and never looking at a brand-new asset, and it is
	# invisible at the call site. Naming the NULL case explicitly costs one line and
	# does not care what frappe coalesces to.
	return frappe.get_all(
		"Training Video Asset",
		filters={"gcs_object": ["is", "set"]},
		or_filters=[
			["last_verified_on", "is", "not set"],
			["last_verified_on", "<", cutoff],
		],
		fields=["name", "gcs_object", "status", "size_bytes", "mime_type"],
		order_by="last_verified_on asc",
		limit=MAX_PER_RUN,
	)


def _verify_one(storage, bucket, row):
	meta, verdict = _stat(storage, bucket, row.get("gcs_object"))

	if verdict == "gone":
		# Definitive: GCS says there is no such object. Worth a status change, and
		# worth saying which object, because the object name is derived rather than
		# typed and "which one is missing" is the first question an author asks.
		frappe.db.set_value(
			"Training Video Asset",
			row["name"],
			{
				"status": "Missing",
				"last_error": f"Object not found in bucket: {row.get('gcs_object')}",
				"last_verified_on": now_datetime(),
			},
			update_modified=False,
		)
		return

	if verdict == "unknown":
		# We could not ask. Record why and change nothing else -- see the module
		# docstring: a transient failure must not condemn a healthy video. Note that
		# last_verified_on is deliberately NOT stamped, so this asset stays at the
		# front of the queue and is retried on the next run.
		frappe.db.set_value(
			"Training Video Asset",
			row["name"],
			{"last_error": meta},
			update_modified=False,
		)
		return

	updates = {"last_verified_on": now_datetime(), "last_error": ""}

	# Repair what the record should already have said. cint() on GCS's size is
	# deliberate: the JSON API returns it as a *string*, and a string here would
	# compare unequal to the stored integer for ever and rewrite on every pass.
	size = cint(meta.get("size"))
	if size and size != cint(row.get("size_bytes")):
		updates["size_bytes"] = size

	content_type = (meta.get("contentType") or "").strip()
	if content_type and content_type != (row.get("mime_type") or "").strip():
		updates["mime_type"] = content_type

	# The controller derives status as `Available if gcs_object else Draft`, so an
	# asset reached by this query -- which selects only rows that HAVE an object --
	# is Available by that same rule, whatever it currently says. Draft here is a
	# row that was never re-saved after its copy landed, not an author's decision:
	# nothing in the UI lets anybody choose Draft while an object exists.
	if row.get("status") != "Available":
		updates["status"] = "Available"

	frappe.db.set_value("Training Video Asset", row["name"], updates, update_modified=False)


def _stat(storage, bucket, object_name):
	"""Return ``(metadata, "ok")``, ``(None, "gone")`` or ``(message, "unknown")``."""
	if not object_name:
		return None, "gone"

	try:
		return storage.objects().get(bucket=bucket, object=object_name).execute(), "ok"
	except Exception as exc:
		status = getattr(getattr(exc, "resp", None), "status", None)
		if cint(status) == 404:
			return None, "gone"
		# 403 lands here rather than in "gone" on purpose. A key that lost its
		# objectAdmin binding cannot see the object, but the object is still there,
		# and telling an author their video is missing would send them to rebuild
		# something that does not need rebuilding.
		return gcs_media._describe(exc)[:1000], "unknown"
