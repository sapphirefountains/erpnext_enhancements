"""Controller for the ERPNext Enhancements Settings Single doctype.

The app-wide configuration hub (``issingle``). Holds:
  * ``project_reminder_emails`` — recipients (child table Project Reminder Email)
    for the daily project-start reminder job.
  * ``maintenance_fee_item`` / ``maintenance_services_group`` — defaults read by
    ``api.maintenance_workflow.create_sales_invoice`` when billing a maintenance
    visit.
  * ``collab_enabled`` + ``collab_doctypes`` (child table Collab Doctype) — the
    live collaborative editing master switch and doctype allowlist, read by
    ``api.collab.get_collab_doctypes()`` and shipped to the desk client via
    ``boot.boot_session``. Seeded with the launch doctypes by the
    ``seed_collab_doctypes`` patch.
  * ``fountain_move_*`` / ``fmr_*`` — the Cactus & Tropicals fountain-move intake
    feature (see ``crm_enhancements/fountain_move/``). Read by
    ``feature_flags.fountain_move_*`` and by the conversion engine.

Values are read via ``frappe.get_single`` / ``frappe.get_cached_doc`` elsewhere.
The only controller logic is the fail-closed guard below.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class ERPNextEnhancementsSettings(Document):
	def validate(self):
		self.validate_fountain_move_public_form()

	def validate_fountain_move_public_form(self):
		"""Refuse to publish the guest intake form without a Turnstile secret.

		``/fountain-move`` is the app's only unauthenticated write path. Every
		other ``allow_guest`` endpoint is gated by a shared secret or an HMAC;
		this one has nothing but Turnstile, a honeypot and rate limits standing
		between an anonymous POST and a Customer/Lead/Opportunity being created.
		Publishing it with a blank secret would make the captcha layer a no-op
		with no visible symptom, so the switch fails closed here rather than
		degrading quietly at request time.
		"""
		if not cint(self.fountain_move_public_form_enabled):
			return
		if self.get_password("fountain_move_turnstile_secret_key", raise_exception=False):
			return
		frappe.throw(
			_(
				"Set the Turnstile secret key before publishing the public fountain-move "
				"form. Without it the captcha check cannot run, leaving the only "
				"unauthenticated write path in the app unprotected."
			),
			title=_("Turnstile Not Configured"),
		)
