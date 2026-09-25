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

And one about the browser's Back button (``TestVisitWizardHistory``):

3. **Back left the wizard from any step.** No step, tab or visit ever made a
   history entry -- the page rewrote its one entry with ``replaceState`` -- and it
   wrote that entry as ``/app/visit-wizard``, which the v16 router cannot parse,
   so a later Back or Forward onto it showed "Page not found". The behaviour is
   executed, not grepped: ``scripts/test_wizard_back_forward.mjs`` runs the real
   page against a port of the v16 router, and this module runs it, because a new
   node step would need its own line in ci.yml.
"""

import io
import os
import re
import shutil
import subprocess
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIZARD = os.path.join(APP_DIR, "sapphire_maintenance", "page", "visit_wizard", "visit_wizard.js")
HARNESS = os.path.join(os.path.dirname(APP_DIR), "scripts", "test_wizard_back_forward.mjs")


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

	def test_answered_rules_cover_every_gated_table(self):
		"""VZ_ANSWERED mirrors the server's submit gate and must not drift.

		``_validate_mandatory_rows`` blocks submit on results, readings and tasks
		and exempts consumables. If the wizard's copy loses a table, it tells a
		technician the step is finished and submit then refuses it -- after they
		have left the site.
		"""
		for table in ("maintenance_results", "chemistry_readings", "cleaning_tasks", "consumables"):
			self.assertRegex(
				self.code,
				rf"VZ_ANSWERED\s*=\s*\{{[\s\S]*?{table}\s*:",
				f"VZ_ANSWERED has no rule for {table}",
			)

	def test_mandatory_rows_are_marked_before_submit(self):
		"""is_mandatory reached the submit gate without ever being shown."""
		self.assertIn("function vz_required_chip(", self.code)
		for table in ("chemistry_readings", "maintenance_results", "cleaning_tasks"):
			self.assertIn(
				f'vz_required_chip("{table}"',
				self.code,
				f"{table} cards do not show which rows are required",
			)

	def test_autosave_failure_is_visible_and_retried(self):
		"""A silent failed autosave can lose a whole visit on a bad signal."""
		self.assertIn("set_save_state(", self.code)
		self.assertIn("schedule_retry(", self.code)
		self.assertRegex(
			self.code,
			r'set_save_state\("error"\)[\s\S]{0,120}?schedule_retry\(\)',
			"a failed save must both show an error and schedule a retry",
		)
		self.assertIn("beforeunload", self.code, "closing the tab must warn while edits are unsaved")

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


class TestVisitWizardHistory(unittest.TestCase):
	"""Back returns to the previous step, tab or screen; Forward restores it."""

	@classmethod
	def setUpClass(cls):
		with io.open(WIZARD, encoding="utf-8") as handle:
			source = handle.read()
		# Comments out: the ones explaining each rule name the very thing they
		# forbid ("/app/visit-wizard", "replaceState").
		source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
		cls.code = re.sub(r"(^|[^:\"'`])//[^\n]*", r"\1", source)

	def test_no_hand_built_paths(self):
		"""A path written by hand is how /app/visit-wizard got into history."""
		self.assertIsNone(re.search(r"[\"'`]/(app|desk)/", self.code))
		self.assertNotIn("pushState(", self.code)
		self.assertIn("frappe.set_route(", self.code)

	def test_frappe_owns_history(self):
		"""frappe's router already re-renders the route on popstate and fires
		on_page_show; a listener of our own would handle every Back twice."""
		self.assertNotIn("popstate", self.code)
		self.assertEqual(self.code.count(".handle_route()"), 1)

	def test_leaving_the_page_saves(self):
		self.assertRegex(self.code, r"\$\(wrapper\)\.on\(\"hide\"")

	def test_saves_for_a_visit_off_screen_are_silent_and_not_retried_when_refused(self):
		"""A parked retry used to be a normal call: every refusal popped frappe's
		unnamed "Visit Out of Date ... Reload" dialog over whichever visit was
		open, up to six times, and retried a save that could never succeed."""
		body = re.search(r"\n\tsend_parked\(name\) \{(.*?)\n\t\}\n", self.code, re.S)
		self.assertIsNotNone(body, "send_parked() is gone")
		self.assertIn("silent: true", body.group(1))
		self.assertIn("refused: vz_refused(error)", body.group(1))
		self.assertIn("this.flush_save({ silent: true })", self.code)

	def test_a_visit_named_only_in_route_options_gets_an_address(self):
		"""frappe.set_route("visit-wizard", {record}) pushes a bare
		/desk/visit-wizard; left as it was, Back or a reload onto that entry
		showed the picker."""
		body = re.search(r"\n\troute_target\(\) \{(.*?)\n\t\}\n", self.code, re.S)
		self.assertIsNotNone(body, "route_target() is gone")
		self.assertIn("unaddressed:", body.group(1))
		self.assertIn("target.unaddressed", self.code)

	def test_back_and_forward_executed(self):
		"""The behaviour itself: the real page against a port of the v16 router."""
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not installed")
		result = subprocess.run(
			[node, HARNESS, "visit-wizard"],
			capture_output=True,
			text=True,
			encoding="utf-8",
			errors="replace",
			check=False,
			timeout=120,
		)
		self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
		self.assertIn(" 0 failed", result.stdout)


if __name__ == "__main__":
	unittest.main()
