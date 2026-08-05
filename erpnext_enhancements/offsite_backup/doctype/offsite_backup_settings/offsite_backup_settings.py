# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Offsite Backup Settings — the module's one configuration surface.

Ships dormant (``enabled`` off) with no credentials. Nothing is scheduled and
nothing is uploaded until somebody creates a service account, shares the backup
folder with it, pastes the key here and turns the switch on.

**There is deliberately no Shared Drive ID field.** The drive id is read off the
folder metadata that :func:`erpnext_enhancements.offsite_backup.drive.check_folder`
already returns, which means it cannot drift out of step with the folder — one
fewer value to go stale the day somebody moves the folder to a different Shared
Drive and forgets this form exists.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, escape_html


class OffsiteBackupSettings(Document):
	def validate(self):
		self.drive_folder_id = (self.drive_folder_id or "").strip()

		if cint(self.enabled):
			# Only enforced when enabled, so the form can be saved half-configured
			# while somebody is still waiting on the Google Cloud Console.
			if not self.drive_folder_id:
				frappe.throw(_("Set a Drive Folder ID before enabling offsite backups."))
			if not self._has_service_account_key():
				frappe.throw(_("Paste the service account key before enabling offsite backups."))

		retention_days = cint(self.retention_days)
		if 1 <= retention_days <= 6:
			# Almost certainly a typo for a longer window. A genuine one-week
			# retention is expressed as 7; anything under that would start deleting
			# the archive before the weekly full backup has even run twice, and 0
			# already means "never prune".
			frappe.throw(
				_(
					"A retention window of {0} day(s) would delete backups faster than the "
					"weekly full backup replaces them. Use 7 or more, or 0 to never prune."
				).format(retention_days)
			)

		if cint(self.min_keep) < 1:
			# The floor exists to stop retention emptying the folder. A floor of
			# zero is not a floor.
			self.min_keep = 1

	def _has_service_account_key(self):
		"""True when a key is set, counting one pasted in this very save.

		``get_password()`` alone is wrong here. Frappe writes Password fields to
		``__Auth`` in ``_save_passwords()``, which runs *after* ``validate()`` — so
		on the first save, when somebody pastes the key and ticks Enable in one go,
		the key is still only on the in-memory field and ``__Auth`` is empty. Asking
		``__Auth`` at this point would reject the exact save the operator is trying
		to make, on their first attempt, with a message telling them to do what they
		have just done. On later saves the field holds Frappe's ``*****`` dummy,
		which is truthy, and the stored value is the fallback.
		"""
		if (self.service_account_json or "").strip():
			return True
		return bool(self.get_password("service_account_json", raise_exception=False))


@frappe.whitelist()
def test_connection():
	"""Probe the configured folder and report what would break at 02:00.

	Answers four questions the operator otherwise only gets answered by a failed
	backup: does the folder exist, is it on a Shared Drive, can this service
	account put files in it, and can it delete the old ones.
	"""
	frappe.only_for("System Manager")

	from erpnext_enhancements.offsite_backup import drive

	settings = frappe.get_cached_doc("Offsite Backup Settings")
	folder_id = (settings.drive_folder_id or "").strip()
	if not folder_id:
		frappe.throw(_("Set a Drive Folder ID first."))

	key = settings.get_password("service_account_json", raise_exception=False)
	if not key:
		frappe.throw(_("Paste the service account key first."))

	service = folder = None
	complaint = None
	try:
		service = drive.get_drive_service(key)
		folder = drive.check_folder(service, folder_id) or {}
	except Exception as exc:
		# Keep only the message. Throwing from *inside* the except block would chain
		# the original exception onto the ValidationError as __context__, which drags
		# the get_drive_service frame — and the parsed key in its locals — along with
		# it for anything that renders a traceback with context.
		complaint = str(exc)[:800]
	if complaint is not None:
		frappe.throw(_("Could not reach that folder: {0}").format(escape_html(complaint)))

	drive_id = folder.get("driveId")
	capabilities = folder.get("capabilities") or {}
	problems = []

	if folder.get("mimeType") != "application/vnd.google-apps.folder":
		problems.append(_("That ID is not a folder."))

	if not drive_id:
		problems.append(
			_(
				"This folder is not on a Shared Drive. A service account has no storage quota "
				"of its own, so a My Drive folder charges every upload to the folder owner's "
				"personal quota — backups then fail unpredictably once that fills up. Move the "
				"folder onto a Shared Drive."
			)
		)

	if not capabilities.get("canAddChildren"):
		problems.append(
			_(
				"This service account cannot add files to the folder. Share the folder with its "
				"client_email as Content manager."
			)
		)

	# canDeleteChildren, NOT canDelete. canDelete is the right to delete the folder
	# itself; pruning needs the right to delete the folder's contents. Checking the
	# wrong one passes this test and then fails every night at prune time.
	if cint(settings.retention_days) > 0 and not capabilities.get("canDeleteChildren"):
		problems.append(
			_(
				"Retention is on but this service account cannot delete the folder's contents, "
				"so pruning will fail every night. Share the folder as Content manager "
				"(Contributor is not enough)."
			)
		)

	try:
		existing = len(drive.list_backup_files(service, folder_id, drive_id))
	except Exception:
		existing = None
		problems.append(_("Could not list the folder's contents."))

	return {
		"ok": not problems,
		"folder_name": folder.get("name"),
		"shared_drive": bool(drive_id),
		"drive_id": drive_id,
		"object_count": existing,
		"problems": problems,
	}
