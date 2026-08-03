# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Settings — the module's master switches and defaults.

Its own Single rather than twenty more fields on ERPNext Enhancements Settings,
following Travel Settings: the knobs here are only meaningful to this module, and
a settings form nobody can read is a settings form nobody adjusts.

Ships dormant (``training_enabled`` off). Authoring works with every switch off;
nothing auto-assigns and nothing is emailed until they are turned on.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class TrainingSettings(Document):
	def validate(self):
		self._reject_absurd_runtime_values()
		self._reject_misnamed_bucket()

	# ------------------------------------------------------------------ helpers

	def _reject_misnamed_bucket(self):
		"""Catch a service account pasted into the bucket field, at save time.

		The runbook prints the bucket name and the service account email as
		adjacent Terraform outputs, so grabbing the wrong one is easy — and it
		happened on the first real setup. Left uncaught, the only symptom is a 404
		from a Test GCS Connection several steps later, which reads as "the whole
		integration is broken" rather than "one field is wrong".
		"""
		bucket = (self.gcs_bucket or "").strip()
		if not bucket:
			return
		self.gcs_bucket = bucket

		from erpnext_enhancements.training.gcs_media import _bucket_name_complaint

		complaint = _bucket_name_complaint(bucket)
		if complaint:
			frappe.throw(complaint)

	def _reject_absurd_runtime_values(self):
		"""These four feed arithmetic in the player and the progress merge. A zero
		or negative heartbeat interval is a busy loop against the site; a zero
		interval cap collapses every watched range into nothing and silently makes
		coverage read 0% for everyone."""
		if cint(self.heartbeat_interval_seconds) < 5:
			frappe.throw(_("Heartbeat interval must be at least 5 seconds."))
		if cint(self.progress_flush_seconds) < cint(self.heartbeat_interval_seconds):
			frappe.throw(
				_("Progress flush interval cannot be shorter than the heartbeat interval — "
				  "it would write to the database on every beat.")
			)
		if cint(self.max_intervals_per_video) < 10:
			frappe.throw(_("Max watched ranges per video must be at least 10."))
		if cint(self.signed_url_ttl_minutes) < 1:
			frappe.throw(_("Signed URL lifetime must be at least 1 minute."))


def get_settings():
	"""The cached Single. Every caller in the module goes through here so the
	dormant-by-default contract is enforced in one place."""
	return frappe.get_cached_doc("Training Settings")


def is_enabled(switch="training_enabled"):
	"""True when the master switch *and* ``switch`` are both on.

	Nothing in this module acts on the site without the master switch, so callers
	never have to remember to check both.
	"""
	try:
		settings = get_settings()
	except Exception:
		# The Single does not exist yet (fresh site mid-migrate). Dormant is the
		# safe reading — see the defensive-hooks note in CLAUDE.md.
		return False
	if not cint(getattr(settings, "training_enabled", 0)):
		return False
	if switch == "training_enabled":
		return True
	return bool(cint(getattr(settings, switch, 0)))
