"""Desk bootinfo extensions (wired via ``extend_bootinfo`` in hooks.py).

Runs once per desk session load; keep it cheap — everything added here is
serialized into every desk page's boot payload.
"""

from erpnext_enhancements.api.collab import get_collab_doctypes
from erpnext_enhancements.api.desk_shortcuts import get_visible_shortcuts_for_user
from erpnext_enhancements.feature_flags import (
	contacts_ux_enabled,
	document_merge_enabled,
	field_description_icons_enabled,
	package_dispatch_enabled,
	process_automation_enabled,
	product_configurator_enabled,
)


def boot_session(bootinfo):
	"""Ship the live-collab doctype allowlist and feature flags to the desk client.

	``public/js/collab/live_form_sync.js`` reads ``frappe.boot.collab_doctypes``
	to decide which forms to attach to; the server-side authority for actual
	broadcasts remains ``api.collab.get_collab_doctypes()``. Settings changes
	therefore reach clients on their next page load, with no deploy.

	``frappe.boot.ee_process_automation`` gates the Jun 9 suite's desk UI
	(Generate Contract buttons, hand-off progress bar, Sales Pipeline board) —
	the server-side guards in ``feature_flags`` remain the authority.

	``frappe.boot.ee_desk_shortcuts`` is the per-user list of desk shortcut tiles
	the "Desk Shortcuts" Custom HTML Block renders on Home (see
	``api.desk_shortcuts``). It is purely cosmetic — target pages enforce their
	own permissions — and is computed defensively so it can never break boot.

	``frappe.boot.ee_field_description_icons`` gates the global desk script that
	renders field descriptions as hover ⓘ icons (see
	``public/js/global_enhancements/field_description_icons.js``).

	``frappe.boot.ee_merge_tool`` gates the global "Merge into…" form button and
	list-view bulk action (see ``public/js/merge_tool/merge_tool.js``); the
	server-side guards in ``document_merge`` remain the authority.

	``frappe.boot.ee_contacts_ux`` gates the Contact/Address quick-entry dialogs
	and the in-place directory refresh (see
	``public/js/global_enhancements/contact_address_quick_entry.js``). The
	``contacts_ux.sync_contact_account_links`` server invariant is deliberately
	NOT gated — see ``feature_flags.contacts_ux_enabled``.

	``frappe.boot.ee_product_configurator`` gates the Product Configuration /
	Configurable Product generation buttons (Item + BOM + Selling Price,
	Create Component Items); the server-side guards in
	``product_configurator.api`` remain the authority.

	``frappe.boot.ee_package_dispatch`` gates the Package Dispatch form's auto-fill
	triggers (catalog-item value + customer address); the form itself works with
	it off (hand-typed), and the server-side guards in ``package_dispatch.api``
	remain the authority.
	"""
	bootinfo.collab_doctypes = sorted(get_collab_doctypes())
	bootinfo.ee_process_automation = 1 if process_automation_enabled() else 0
	bootinfo.ee_desk_shortcuts = get_visible_shortcuts_for_user()
	bootinfo.ee_field_description_icons = 1 if field_description_icons_enabled() else 0
	bootinfo.ee_merge_tool = 1 if document_merge_enabled() else 0
	bootinfo.ee_contacts_ux = 1 if contacts_ux_enabled() else 0
	bootinfo.ee_product_configurator = 1 if product_configurator_enabled() else 0
	bootinfo.ee_package_dispatch = 1 if package_dispatch_enabled() else 0
