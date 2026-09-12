"""One-time migration patch (PRE_model_sync; listed in patches.txt).

Renames the single DocType "Poseidon Settings" -> "Triton Settings" before the
new ``triton_settings`` JSON is synced, so the stored configuration (gateway
URL, prompts, plain Twilio identifiers) carries across instead of being orphaned.
Idempotent: see :func:`execute`.

.. warning::

   **This did NOT carry the secrets across, and an earlier version of this docstring
   said it did.** A Password field keeps a masked placeholder in ``tabSingles`` and the
   real encrypted value in ``__Auth``, keyed by ``(doctype, name, fieldname)``.
   ``frappe.rename_doc`` rewrites ``tabSingles`` and leaves ``__Auth`` alone, so four
   secrets stayed filed under the old name and read back as ``None`` — invisibly, because
   a Password field renders blank whether or not a value is stored.

   ``maps_api_key`` and ``twilio_auth_token`` were still stranded on production in
   September 2026, which is why no Vertex feature in the app had ever worked and every
   inbound Twilio webhook was being rejected. Repaired by
   ``patches/rescue_renamed_doctype_auth_rows`` (v1.420.0).

   If you ever rename a DocType that owns Password fields, move its ``__Auth`` rows too.
"""
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
