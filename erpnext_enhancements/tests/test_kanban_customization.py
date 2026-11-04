"""The Opportunity Kanban customization on frappe 16.53's classic engine (v1.779.0).

Bench-free: reads files only. The behaviour runs in ``scripts/test_kanban_customization.js``
against the shapes of v16.53.0's ``KanbanView`` and ``KanbanBoardCard``; this suite holds what
that harness cannot see: that the file no longer patches methods the engine does not have
(``render_card`` and ``get_data``, dead since the 2026-10-06 upgrade to 16.50), that it still ships
in the Kanban bundle, that the styles exist, that nothing of the navy high-value fill withdrawn in
v1.794.0 survives in the module or the CSS, and that CI runs the harness.

Run: python -m unittest erpnext_enhancements.tests.test_kanban_customization
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
MODULE = APP / "public" / "js" / "kanban_customization.js"
BUNDLE = APP / "public" / "js" / "ee_kanban.bundle.js"
CSS = APP / "public" / "css" / "desk_enhancements.bundle.css"
HARNESS = REPO_ROOT / "scripts" / "test_kanban_customization.js"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def read(path):
	return path.read_text(encoding="utf-8")


def code(path):
	src = re.sub(r"/\*[\s\S]*?\*/", "", read(path))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


class TestEngineHooks(unittest.TestCase):
	def test_it_hooks_what_16_53_has(self):
		src = code(MODULE)
		self.assertIn("proto.set_fields = function ()", src)
		self.assertIn("proto.render_kanban_board = function ()", src)
		self.assertIn("frappe.views.KanbanBoardCard = wrapped;", src)
		# the card function is defined by kanban_board.bundle.js when it loads, so it is
		# wrapped after load_lib resolves, never at evaluation
		self.assertIn("Promise.resolve(this.load_lib)", src)

	def test_the_dead_patches_are_gone(self):
		src = code(MODULE)
		self.assertNotIn("prototype.render_card", src)
		self.assertNotIn("prototype.get_data", src)

	def test_the_query_carries_the_party_and_the_contact_and_no_tags(self):
		src = code(MODULE)
		self.assertIn('["party_name", "opportunity_from", CONTACT_FIELD, CONTACT_PHONE_FIELD].forEach((f) => this._add_field(f));', src)
		self.assertIn('const CONTACT_FIELD = "primary_contact";', src)
		self.assertIn('const CONTACT_PHONE_FIELD = "custom_contact_mobile_number";', src)
		self.assertIn('const UNFETCHED_FIELDS = ["_user_tags", "_liked_by"];', src)

	def test_no_comment_count_and_no_heart_through_the_engine(self):
		src = code(MODULE)
		self.assertIn("proto.get_args = function ()", src)
		self.assertIn("args.with_comment_count = 0;", src)
		self.assertIn("proto.get_like_html = function ()", src)

	def test_the_navy_fill_is_gone(self):
		# v1.779.0 restored the dark card on deals over 4000; Nikolas found it unreadable and
		# v1.794.0 withdrew it. Neither the class nor the threshold may come back by accident.
		# Comments may still tell the story (the CSS block's does); code and selectors may not,
		# and the harness asserts the same of the module's source.
		for path in (MODULE, CSS):
			self.assertNotIn("opportunity-high-value", code(path), path)
		self.assertNotIn("HIGH_VALUE", code(MODULE))
		self.assertIn('!SOURCE.includes("opportunity-high-value")', read(HARNESS))

	def test_the_link_routes_in_place_and_stops_the_card(self):
		src = code(MODULE)
		handler = src[src.index('$link.on("click"') :]
		handler = handler[: handler.index("});")]
		self.assertIn("event.preventDefault();", handler)
		self.assertIn("event.stopPropagation();", handler)
		self.assertIn('frappe.set_route("Form", doctype, party);', handler)
		self.assertIn('const PARTY_DOCTYPES = ["Customer", "Lead"];', src)

	def test_it_ships_and_is_styled(self):
		self.assertIn('import "./kanban_customization.js";', read(BUNDLE))
		css = read(CSS)
		self.assertIn(".kanban-doc-field a.kanban-card-party-link", css)
		self.assertIn(".kanban-doc-field .kanban-card-contact-phone", css)
		self.assertIn(".kanban-card-value-stream {", css)


class TestHarness(unittest.TestCase):
	def test_ci_runs_the_harness(self):
		self.assertIn("node scripts/test_kanban_customization.js", read(CI))

	@unittest.skipUnless(shutil.which("node"), "node is not installed")
	def test_the_harness_passes(self):
		result = subprocess.run(
			["node", str(HARNESS)], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120
		)
		self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
		self.assertIn("all checks passed", result.stdout)


if __name__ == "__main__":
	unittest.main()
