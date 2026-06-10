"""Desk bootinfo extensions (wired via ``extend_bootinfo`` in hooks.py).

Runs once per desk session load; keep it cheap — everything added here is
serialized into every desk page's boot payload.
"""

from erpnext_enhancements.api.collab import get_collab_doctypes
from erpnext_enhancements.feature_flags import process_automation_enabled


def boot_session(bootinfo):
	"""Ship the live-collab doctype allowlist and feature flags to the desk client.

	``public/js/collab/live_form_sync.js`` reads ``frappe.boot.collab_doctypes``
	to decide which forms to attach to; the server-side authority for actual
	broadcasts remains ``api.collab.get_collab_doctypes()``. Settings changes
	therefore reach clients on their next page load, with no deploy.

	``frappe.boot.ee_process_automation`` gates the Jun 9 suite's desk UI
	(Generate Contract buttons, hand-off progress bar, Sales Pipeline board) —
	the server-side guards in ``feature_flags`` remain the authority.
	"""
	bootinfo.collab_doctypes = sorted(get_collab_doctypes())
	bootinfo.ee_process_automation = 1 if process_automation_enabled() else 0
