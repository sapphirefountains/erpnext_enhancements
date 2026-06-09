"""Integration tests for per-user form draft persistence (``api.user_drafts``).

Verifies that ``save_draft`` upserts a single ``User Form Draft`` per
(user, ref_doctype, ref_name) — creating then overwriting ``form_data`` on a
second save rather than duplicating — and that ``delete_draft`` removes it.
"""
import frappe
from frappe.tests.utils import FrappeTestCase
from erpnext_enhancements.api.user_drafts import save_draft, delete_draft
import json

class TestUserDrafts(FrappeTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.ref_doctype = "ToDo"
        self.ref_name = frappe.utils.random_string(10)
        self.form_data = json.dumps({"description": "Draft Description"})

    def test_save_and_update_draft(self):
        """save_draft creates the draft, then a second call updates the same row's form_data."""
        # Save
        save_draft(self.ref_doctype, self.ref_name, self.form_data)

        draft_name = frappe.db.get_value("User Form Draft", {
            "ref_doctype": self.ref_doctype,
            "ref_name": self.ref_name,
            "user": "Administrator"
        })
        self.assertTrue(draft_name)

        doc = frappe.get_doc("User Form Draft", draft_name)
        self.assertEqual(doc.form_data, self.form_data)

        # Update
        new_data = json.dumps({"description": "Updated Description"})
        save_draft(self.ref_doctype, self.ref_name, new_data)

        doc.reload()
        self.assertEqual(doc.form_data, new_data)

    def test_delete_draft(self):
        """delete_draft removes the saved draft for the user/reference."""
        save_draft(self.ref_doctype, self.ref_name, self.form_data)

        delete_draft(self.ref_doctype, self.ref_name)

        exists = frappe.db.get_value("User Form Draft", {
            "ref_doctype": self.ref_doctype,
            "ref_name": self.ref_name,
            "user": "Administrator"
        })
        self.assertFalse(exists)

    def tearDown(self):
        frappe.db.delete("User Form Draft", {"ref_doctype": self.ref_doctype, "ref_name": self.ref_name})
