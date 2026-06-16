"""Fix the "Integrations" module-name collision with Frappe core (post_model_sync).

erpnext_enhancements declared a module literally named **"Integrations"** (v1.39.0),
which collides with Frappe's built-in **"Integrations"** module (OAuth, Webhook,
Google Calendar/Contacts/Settings, LDAP, Social Login Key, Connected App, Token
Cache, Integration Request, ... doctypes). On migrate, Frappe then resolved its
OWN integration doctypes to ``erpnext_enhancements/integrations/`` -- not found --
and deleted them as "orphaned", breaking the site (500 errors on login/website).

The module is renamed to **"Integration Hub"** (folder ``integration_hub/``). This
patch:
- reassigns this app's three surfaces (GA4 Settings doctype, GA4 Dashboard +
  Integrations Health pages) to "Integration Hub";
- restores the "Integrations" Module Def to the ``frappe`` app so Frappe re-owns
  it.

Frappe re-creates its deleted integration doctypes from ``frappe/integrations/``
during the same migrate's model sync -- the *structure* is restored (their stored
rows, e.g. OAuth tokens / Social Login Keys, are lost and must be reconfigured).

Idempotent.
"""
import frappe

NEW = "Integration Hub"
MY_DOCTYPES = ("GA4 Settings",)
MY_PAGES = ("ga4-dashboard", "integrations-health")


def execute():
    if frappe.db.exists("Module Def", NEW):
        for dt in MY_DOCTYPES:
            if frappe.db.exists("DocType", dt):
                frappe.db.set_value("DocType", dt, "module", NEW)
        for pg in MY_PAGES:
            if frappe.db.exists("Page", pg):
                frappe.db.set_value("Page", pg, "module", NEW)

    # Restore Frappe's built-in Integrations module ownership (the name collision
    # may have flipped its Module Def's app to this app).
    if frappe.db.exists("Module Def", "Integrations"):
        frappe.db.set_value("Module Def", "Integrations", "app_name", "frappe")

    frappe.clear_cache()
