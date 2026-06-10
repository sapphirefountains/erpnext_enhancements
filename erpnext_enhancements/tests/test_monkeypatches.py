"""Tests for runtime framework monkeypatches (``erpnext_enhancements.monkeypatches``).

Covers the None-safety guard on ``frappe.utils.modules.get_modules_from_app``: a
``@redis_cache``-cached ``None`` (the decorator's documented "Edge Case") would
otherwise make ``get_modules_from_all_apps`` do ``list += None`` and raise
``TypeError: 'NoneType' object is not iterable`` — which took down CRM's
``check_app_permission`` / the app switcher, the dashboards, and the User /
Module Profile forms.
"""

import frappe.utils.modules as fmod
from frappe.tests.utils import FrappeTestCase

from erpnext_enhancements import monkeypatches


class TestNoneSafeModules(FrappeTestCase):
	def test_get_modules_from_app_coerces_none_to_empty_list(self):
		"""A function returning ``None`` is wrapped to return ``[]`` instead."""
		original = fmod.get_modules_from_app
		try:
			fmod.get_modules_from_app = lambda app: None  # simulate a cached None
			monkeypatches.apply()
			self.assertEqual(fmod.get_modules_from_app("telephony"), [])
		finally:
			fmod.get_modules_from_app = original

	def test_get_modules_from_all_apps_survives_cached_none(self):
		"""The real crash path no longer raises when an app yields ``None``."""
		original = fmod.get_modules_from_app
		try:
			# Worst case: every app's lookup returns None.
			fmod.get_modules_from_app = lambda app: None
			monkeypatches.apply()
			# Without the patch this is the original
			# `TypeError: 'NoneType' object is not iterable`.
			self.assertEqual(fmod.get_modules_from_all_apps(), [])
		finally:
			fmod.get_modules_from_app = original

	def test_apply_is_idempotent(self):
		"""Re-applying does not double-wrap or rebind the function."""
		monkeypatches.apply()
		patched = fmod.get_modules_from_app
		monkeypatches.apply()
		self.assertIs(fmod.get_modules_from_app, patched)
		self.assertTrue(getattr(fmod.get_modules_from_app, "_ee_none_safe", False))

	def test_real_module_list_is_unaffected(self):
		"""A normal call still returns the real, non-empty module list."""
		modules = fmod.get_modules_from_all_apps()
		self.assertIsInstance(modules, list)
		self.assertTrue(modules)  # frappe always registers modules
		self.assertTrue(all(m.get("module_name") for m in modules))
