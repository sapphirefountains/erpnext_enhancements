"""The code map the Enhancement Request breakdown sends Triton. Bench-free, with a frappe stub.

``product_feedback/codemap.py`` is the only view of this repository the planner gets (ADR 0016
§3), so two things about it matter and neither is visible from a working breakdown:

* **What it leaves out.** It used to send the first 6,000 characters of CLAUDE.md's Gotchas,
  which stopped mid-sentence in the tenth of 21 bullets. Every later gotcha — the trailing-space
  trap, the Frappe 16 ``get_all`` function-string refusal — never reached the model, and nothing
  said so. The tests read the real CLAUDE.md and count the bullets independently, so adding a
  gotcha that the extractor cannot see fails here.
* **Triton's type contracts.** ``conventions`` must be a ``str`` (Triton calls ``.strip()``) and
  every ``packages`` value a list of path strings (Triton basenames each one). Break either and
  every breakdown returns 502 and lands in ``Breakdown Failed``. The listing totals therefore ride
  under their own ``totals`` key, which today's Triton ignores.

Its own CI step: it installs a ``frappe`` stub in ``setUpModule`` (codemap imports frappe at
module level), and a stub must not leak into the stub-free suites.
"""

import os
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

codemap = None

#: The keys today's Triton renders out of ``codebase.erpnext`` (work_breakdown._render_codemap on
#: origin/main 73ce6a4). A new key must not collide with one of these.
TRITON_RENDERED_KEYS = {
	"repo",
	"version",
	"language",
	"layout",
	"modules",
	"packages",
	"backend",
	"frontend",
	"docs",
	"conventions",
}


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe.get_app_path = lambda app, *parts: str(APP_DIR.joinpath(*parts))
	frappe.scrub = lambda text: (text or "").replace(" ", "_").replace("-", "_").lower()
	frappe.get_attr = lambda path: "0.0.0-test" if path.endswith("__version__") else None
	frappe.get_installed_apps = lambda: []

	class _DB:
		def sql(self, *args, **kwargs):
			raise RuntimeError("no database in the bench-free tier")

	frappe.db = _DB()
	sys.modules["frappe"] = frappe


def setUpModule():
	global codemap
	_install_frappe_stub()
	sys.modules.pop("erpnext_enhancements.product_feedback.codemap", None)
	from erpnext_enhancements.product_feedback import codemap as _codemap

	codemap = _codemap


def _claude_md() -> str:
	return (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")


def _independent_gotcha_leads() -> list[str]:
	"""Count CLAUDE.md's gotcha bullets without the code under test: top-level ``- **`` lines."""
	text = _claude_md()
	start = text.index("## Gotchas")
	end = text.index("\n## ", start + 1)
	section = text[start:end]
	return [line for line in section.splitlines() if line.startswith("- **")]


class TestConventions(unittest.TestCase):
	def setUp(self):
		self.totals = {"packages": {}, "doctypes": {}, "gotchas": 0}
		self.text = codemap._conventions(str(REPO_ROOT), self.totals)

	def test_is_a_string_within_the_cap(self):
		self.assertIsInstance(self.text, str)
		self.assertLessEqual(len(self.text), codemap.MAX_CONVENTION_CHARS)

	def test_every_gotcha_is_sent(self):
		leads = _independent_gotcha_leads()
		self.assertGreaterEqual(len(leads), 20, "CLAUDE.md's Gotchas section looks empty")
		self.assertEqual(self.totals["gotchas"], len(leads))
		sent = [line for line in self.text.splitlines() if line.startswith("- ")]
		self.assertEqual(len(sent), len(leads))

	def test_the_gotchas_the_old_prefix_dropped_are_present(self):
		# Control: the old 6,000-character prefix ended inside the tenth bullet, so the last
		# bullet is exactly the one it could not reach.
		last_lead = _independent_gotcha_leads()[-1]
		headline = re.sub(r"\s+", " ", re.match(r"- \*\*(.+?)\*\*", last_lead).group(1)).strip()
		self.assertIn(headline, self.text)
		old = _claude_md()[_claude_md().index("## Gotchas") :][: codemap.MAX_CONVENTION_CHARS]
		self.assertNotIn(headline, old, "the control no longer distinguishes old from new")

	def test_conventions_section_is_present(self):
		self.assertIn("## Conventions", self.text)

	def test_headlines_carry_no_markdown_bold(self):
		for line in self.text.splitlines():
			if line.startswith("- "):
				self.assertNotIn("**", line)

	def test_a_bullet_that_wraps_is_joined_into_one_line(self):
		section = "## Gotchas\n\n- **First line\n  continues here.** Body.\n- **Second.** Body.\n"
		self.assertEqual(
			codemap._gotcha_headlines(section), ["First line continues here.", "Second."]
		)

	def test_falls_back_to_the_prefix_when_nothing_parses(self):
		fallback_root = self._temp_repo("# Title\n\n## Gotchas\n\nplain prose, no bullets\n")
		out = codemap._conventions(fallback_root)
		self.assertTrue(out.startswith("## Gotchas"))

	def test_missing_file_is_empty_not_an_error(self):
		self.assertEqual(codemap._conventions(self._temp_repo(None)), "")

	def _temp_repo(self, claude_md):
		import tempfile

		root = tempfile.mkdtemp()
		if claude_md is not None:
			with open(os.path.join(root, "CLAUDE.md"), "w", encoding="utf-8") as handle:
				handle.write(claude_md)
		return root


class TestListingsAndTotals(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.built = codemap._build()

	def test_packages_are_lists_of_strings(self):
		# Triton runs os.path.basename over every entry.
		for directory, files in self.built["packages"].items():
			self.assertIsInstance(files, list, directory)
			for entry in files:
				self.assertIsInstance(entry, str, directory)

	def test_totals_cover_every_listing(self):
		totals = self.built["totals"]
		for directory, files in self.built["packages"].items():
			self.assertGreaterEqual(totals["packages"][directory], len(files), directory)
		for module, names in self.built["doctypes"].items():
			self.assertGreaterEqual(totals["doctypes"][module], len(names), module)

	def test_a_capped_listing_reports_its_real_size(self):
		# Control: patches/ is far past the cap, which is the case the totals exist for.
		patches = "erpnext_enhancements/patches/"
		self.assertEqual(len(self.built["packages"][patches]), codemap.MAX_FILES_PER_DIR)
		self.assertGreater(self.built["totals"]["packages"][patches], codemap.MAX_FILES_PER_DIR)

	def test_totals_do_not_collide_with_what_triton_renders(self):
		self.assertNotIn("totals", TRITON_RENDERED_KEYS)
		self.assertIsInstance(self.built["conventions"], str)

	def test_totals_hold_only_numbers(self):
		totals = self.built["totals"]
		self.assertIsInstance(totals["gotchas"], int)
		for group in ("packages", "doctypes"):
			for value in totals[group].values():
				self.assertIsInstance(value, int)


if __name__ == "__main__":
	unittest.main()
