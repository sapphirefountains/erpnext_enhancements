import frappe
import unittest
from erpnext_enhancements.project_merge import merge_projects

class TestProjectMerge(unittest.TestCase):
    def setUp(self):
        # Create Company
        self.company = frappe.get_doc({
            "doctype": "Company",
            "company_name": "_Test Company Merge",
            "default_currency": "USD",
            "country": "United States"
        })
        if not frappe.db.exists("Company", "_Test Company Merge"):
            self.company.insert(ignore_permissions=True)
        else:
            self.company = frappe.get_doc("Company", "_Test Company Merge")

        # Create Source Project
        self.source_project = frappe.get_doc({
            "doctype": "Project",
            "project_name": "Source Project Test",
            "status": "Open",
            "company": "_Test Company Merge"
        }).insert(ignore_permissions=True)
        
        # Create Target Project
        self.target_project = frappe.get_doc({
            "doctype": "Project",
            "project_name": "Target Project Test",
            "status": "Open",
            "company": "_Test Company Merge"
        }).insert(ignore_permissions=True)
        
        # Create a Task linked to Source Project
        self.task = frappe.get_doc({
            "doctype": "Task",
            "subject": "Test Task for Merge",
            "project": self.source_project.name,
            "company": "_Test Company Merge"
        }).insert(ignore_permissions=True)

    def tearDown(self):
        frappe.delete_doc("Project", self.source_project.name, force=True)
        frappe.delete_doc("Project", self.target_project.name, force=True)
        frappe.delete_doc("Task", self.task.name, force=True)

    def test_merge_projects(self):
        print(f"DEBUG: Source: {self.source_project.name}, Target: {self.target_project.name}")
        
        # Verify initial state
        self.assertEqual(self.task.project, self.source_project.name)
        
        # Perform Merge
        print("DEBUG: Calling merge_projects")
        merge_projects(self.source_project.name, self.target_project.name)
        print("DEBUG: merge_projects returned")
        
        # Reload documents
        self.task.reload()
        self.source_project.reload()
        
        # Verify Task is moved
        self.assertEqual(self.task.project, self.target_project.name)
        
        # Verify Source Project is Cancelled
        self.assertEqual(self.source_project.status, "Cancelled")
