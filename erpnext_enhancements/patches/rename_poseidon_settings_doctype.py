import frappe

OLD = "Poseidon Settings"
NEW = "Triton Settings"


def execute():
    """Rename the single DocType 'Poseidon Settings' -> 'Triton Settings'.

    Runs in pre_model_sync so the rename happens before the new `triton_settings`
    JSON is synced. frappe.rename_doc carries the stored single values (in
    tabSingles) and the DocType definition across, so the configured Gateway
    URL, secrets, prompts and Twilio credentials are preserved. The subsequent
    model sync then reconciles the field definitions against the new JSON.

    Idempotent: a no-op once the rename has happened (or on a fresh install
    where the old doctype never existed).
    """
    if not frappe.db.exists("DocType", OLD):
        return
    if frappe.db.exists("DocType", NEW):
        # Both somehow present — leave them; the service-user patch and the
        # synced JSON win. Nothing safe to rename onto an existing name.
        return

    frappe.rename_doc("DocType", OLD, NEW, force=True)
    frappe.clear_cache(doctype=NEW)
