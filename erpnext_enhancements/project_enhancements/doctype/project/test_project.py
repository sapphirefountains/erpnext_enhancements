# Copyright (c) 2024, Sapphire Fountains and Contributors
# See license.txt
"""Unit tests for the Project doctype's custom server-side scripts."""

import unittest

from erpnext_enhancements.project_enhancements.doctype.project.project import get_project_grouping_option


class TestProject(unittest.TestCase):
	"""Test case for custom functions related to the Project doctype."""

	def test_get_project_grouping_option(self):
		"""Test that the project grouping option is returned correctly."""
		# Call the function
		result = get_project_grouping_option()

		# Define the expected output
		expected = {"group_by": "project_type"}

		# Assert that the result matches the expected output
		self.assertEqual(result, expected)
