"""Desk bootinfo extensions (wired via ``extend_bootinfo`` in hooks.py).

Runs once per desk session load; keep it cheap — everything added here is
serialized into every desk page's boot payload.
"""

from erpnext_enhancements.api.collab import get_collab_doctypes


def boot_session(bootinfo):
	"""Ship the live-collab doctype allowlist to the desk client.

	``public/js/collab/live_form_sync.js`` reads ``frappe.boot.collab_doctypes``
	to decide which forms to attach to; the server-side authority for actual
	broadcasts remains ``api.collab.get_collab_doctypes()``. Settings changes
	therefore reach clients on their next page load, with no deploy.
	"""
	bootinfo.collab_doctypes = sorted(get_collab_doctypes())
