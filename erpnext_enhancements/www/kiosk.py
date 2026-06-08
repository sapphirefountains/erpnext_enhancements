import frappe

from erpnext_enhancements.api.time_kiosk import get_kiosk_bootstrap

# Always render fresh per-user; never cache the authenticated shell.
no_cache = 1


def get_context(context):
    # Auth gate: bounce guests to login and return here afterwards.
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/kiosk"
        raise frappe.Redirect

    boot = get_kiosk_bootstrap()

    context.no_cache = 1
    context.boot_json = frappe.as_json(boot)
    context.csrf_token = boot.get("csrf_token") or ""
    return context
