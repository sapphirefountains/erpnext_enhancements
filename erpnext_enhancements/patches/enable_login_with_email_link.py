"""Turn on Login with Email Link, for the customer portal (v1.565.0).

Nik's call, 2026-09-29: customers sign in to ``/rentals`` and ``/pay`` with a one-time link emailed
to them. The site's only sign-in until now was Google, which fails for the many event planners and
venues on Outlook or work mail. Sign-up stays disabled: an account exists only when a customer signs
a Rental Agreement or staff invite a contact (``portal_login.ensure_portal_user``), and frappe mints
no link for an address without one.

**This ships in the same release as the guard, and must never ship without it.** Frappe's setting is
site-wide and its ``login_via_key`` signs in any account with no password, 2FA or Google, staff and
Administrator included. ``portal_login.send_login_link`` (the override) mints no link for anything but
a Website User, and ``portal_login.refuse_staff_email_link_login`` (``on_login``) refuses the sign-in
anyway. v16's login page shows the "Login with Email Link" button even with password login disabled,
so no other setting is needed.

Runs once: someone who later turns the setting off in System Settings stays off. Sets the link's life
to 10 minutes only where it is blank. Returns early on a frappe without the field.
"""

import frappe
from frappe.utils import cint

SETTING = "login_with_email_link"
EXPIRY = "login_with_email_link_expiry"
EXPIRY_MINUTES = 10


def execute():
	meta = frappe.get_meta("System Settings")
	if not meta.has_field(SETTING):
		return
	if not cint(frappe.db.get_single_value("System Settings", SETTING)):
		frappe.db.set_single_value("System Settings", SETTING, 1)
	if meta.has_field(EXPIRY) and not cint(frappe.db.get_single_value("System Settings", EXPIRY)):
		frappe.db.set_single_value("System Settings", EXPIRY, EXPIRY_MINUTES)
	frappe.clear_cache()
