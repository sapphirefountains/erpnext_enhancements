"""One-time migration patch (post_model_sync; listed in patches.txt).

Renames the telephony service User poseidon@ -> triton@ (or creates it fresh on a
new install) and repoints residual free-text references that ``rename_doc`` does
not rewrite — see :func:`execute` and the ``REFERENCE_COLUMNS`` table below.
Companion to the pre-sync ``rename_poseidon_settings_doctype`` patch. Idempotent.
"""
import frappe

OLD = "poseidon@sapphirefountains.com"
NEW = "triton@sapphirefountains.com"

# (doctype, column) pairs the telephony integration writes the service-user
# address into. frappe.rename_doc on a User does not rewrite free-text Data
# columns like Communication.sender, so we repoint them explicitly.
REFERENCE_COLUMNS = [
    ("Communication", "sender"),
    ("Communication", "owner"),
    ("Communication", "modified_by"),
    ("ToDo", "allocated_to"),
    ("ToDo", "owner"),
    ("Comment", "owner"),
    ("File", "owner"),
    ("Notification Log", "for_user"),
    ("Notification Log", "owner"),
]


def execute():
    """Migrate the telephony service user poseidon@ -> triton@.

    * If the old user exists and the new one doesn't, rename it (preserving
      roles, permissions and password) via frappe.rename_doc.
    * If neither exists (fresh install), create a minimal enabled System User
      so the integration's frappe.set_user(NEW) calls succeed. It needs no
      special roles — the telephony code inserts with ignore_permissions.
    * Repoint residual free-text references (Communication.sender, owners,
      assignees) and the "Poseidon" sender_full_name branding.

    Idempotent: re-running after a successful migration only re-applies the
    (already-satisfied) reference updates, which match no rows.
    """
    if frappe.db.exists("User", OLD) and not frappe.db.exists("User", NEW):
        frappe.rename_doc("User", OLD, NEW, force=True)
    elif not frappe.db.exists("User", OLD) and not frappe.db.exists("User", NEW):
        user = frappe.new_doc("User")
        user.email = NEW
        user.first_name = "Triton"
        user.user_type = "System User"
        user.enabled = 1
        # No interactive login for a service account.
        user.send_welcome_email = 0
        user.insert(ignore_permissions=True)

    # Repoint any rows still pointing at the old address (covers historical
    # Communications/ToDos and the rename's free-text blind spots).
    for doctype, column in REFERENCE_COLUMNS:
        if not frappe.db.has_column(doctype, column):
            continue
        frappe.db.sql(
            f"UPDATE `tab{doctype}` SET `{column}` = %s WHERE `{column}` = %s",
            (NEW, OLD),
        )

    # Rebrand the historical sender display name.
    if frappe.db.has_column("Communication", "sender_full_name"):
        frappe.db.sql(
            "UPDATE `tabCommunication` SET `sender_full_name` = %s "
            "WHERE `sender_full_name` = %s",
            ("Triton", "Poseidon"),
        )

    frappe.db.commit()
