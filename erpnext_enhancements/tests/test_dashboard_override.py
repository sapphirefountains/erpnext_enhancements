"""Unit tests for the Project dashboard override (``get_dashboard_data``).

Verifies that the override registers the custom non-standard fieldnames
(Material Request / Request for Quotation both link to Project via
``custom_project``) without clobbering any pre-existing dashboard config.
"""
import unittest
from erpnext_enhancements.project_enhancements import get_dashboard_data


class TestDashboardOverride(unittest.TestCase):
	def test_get_dashboard_data_empty(self):
		"""Test getting dashboard data with empty input."""
		data = get_dashboard_data(None)
		expected = {
			"non_standard_fieldnames": {
				"Material Request": "custom_project",
				"Request for Quotation": "custom_project",
			}
		}
		self.assertEqual(data, expected)

	def test_get_dashboard_data_existing(self):
		"""Test getting dashboard data with existing data."""
		existing_data = {
			"fieldname": "project",
			"non_standard_fieldnames": {"Some Other Doc": "custom_field"},
		}
		data = get_dashboard_data(existing_data)

		self.assertEqual(data["fieldname"], "project")
		self.assertEqual(data["non_standard_fieldnames"]["Material Request"], "custom_project")
		self.assertEqual(data["non_standard_fieldnames"]["Request for Quotation"], "custom_project")
		self.assertEqual(data["non_standard_fieldnames"]["Some Other Doc"], "custom_field")


if __name__ == "__main__":
	unittest.main()
