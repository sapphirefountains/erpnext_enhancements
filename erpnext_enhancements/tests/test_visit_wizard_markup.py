# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Guards for how the Visit Wizard renders authored text.

Bench-free and frappe-free: these are assertions about the contents of
``visit_wizard.js``, so no stub is needed. unittest, not pytest, so it can ride
an existing ``python -m unittest`` step in ci.yml (a pytest-style suite added to
a unittest module list is silently collected as nothing -- the trap that left the
QuickBooks suite running nowhere for weeks).

Two mistakes are guarded here, both of which shipped and neither of which is
visible in a diff:

1. **Escaping Text Editor HTML.** ``frappe.utils.xss_sanitise`` escapes ``<``,
   ``>``, ``"``, ``'`` and ``/`` by default -- its default strategies are
   ``["html", "js"]``. Passing a Section's ``step_instructions`` or a Template's
   safety guidance through it renders the markup as visible tags, so technicians
   read a literal ``<ul><li><b>`` instead of a formatted list. The panel still
   *looks* populated, which is why it survived review.

2. **Raw control bytes in the source.** Writing a character class as
   ``[\\s<NUL>-<US>]`` with literal control characters rather than ``\\u0000``
   escapes works at runtime, so nothing fails -- but git then treats the file as
   binary and stops producing readable diffs for it.
"""

import io
import os
import re
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIZARD = os.path.join(APP_DIR, "sapphire_maintenance", "page", "visit_wizard", "visit_wizard.js")


class TestVisitWizardMarkup(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		with io.open(WIZARD, "rb") as handle:
			cls.raw = handle.read()
		cls.source = cls.raw.decode("utf-8")
		# Strip // comments so assertions about *code* are not fooled by prose
		# that legitimately names the thing it is warning about.
		cls.code = re.sub(r"^\s*//.*$", "", cls.source, flags=re.MULTILINE)

	def test_rich_text_is_not_escaped(self):
		"""No call to xss_sanitise survives in code -- it would escape the markup."""
		self.assertNotIn(
			"xss_sanitise(",
			self.code,
			"visit_wizard.js calls frappe.utils.xss_sanitise, which escapes HTML by "
			"default and renders Text Editor content as visible tags. Use "
			"vz_rich_html() for Text Editor fields and vz_plain_html() for Small Text.",
		)

	def test_helpers_are_defined(self):
		for helper in ("function vz_rich_html(", "function vz_plain_html("):
			self.assertIn(helper, self.code, f"{helper} is missing from visit_wizard.js")

	def test_rich_html_strips_executable_nodes(self):
		"""The rich-text path must still drop anything that could execute."""
		for needle in ("DOMParser", "script", "removeAttribute"):
			self.assertIn(needle, self.code, f"vz_rich_html no longer references {needle}")

	def test_plain_text_keeps_line_breaks(self):
		"""Small Text notes are one hazard per line; collapsing them hides one."""
		self.assertIn("escape_html", self.code)
		self.assertRegex(
			self.code,
			r"vz_plain_html[\s\S]{0,400}?replace\(/\\n/g,\s*\"<br>\"\)",
			"vz_plain_html should escape and then convert newlines to <br>",
		)

	def test_no_raw_control_bytes(self):
		"""Literal control characters make git treat the file as binary."""
		offenders = [
			i for i, byte in enumerate(self.raw)
			if byte < 9 or byte in (11, 12) or 14 <= byte < 32
		]
		self.assertEqual(
			offenders,
			[],
			f"visit_wizard.js contains raw control bytes at {offenders[:5]}. "
			"Write them as \\u0000-style escapes instead.",
		)


if __name__ == "__main__":
	unittest.main()
