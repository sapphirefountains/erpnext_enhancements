"""Custom pages S1 (v1.623.0, TASK-2026-02803): findability and the retired page.

* Every Desk Page carries a ``title``. The awesomebar indexes pages by title and the
  breadcrumb prints it, so a Page without one cannot be found by name — Sales Pipeline was
  invisible to search for that reason until this release.
* Every workspace or page file that ``patches/custom_pages_s1_navigation.py`` force-imports
  exists and has its ``modified`` stamp bumped. Workspaces and Pages are timestamp-gated by
  ``import_file``, so an edited file left at its old stamp is skipped in silence on every
  migrate (the Training workspace sat five weeks like that).
* Hierarchical Task View is gone from the repo, and nothing outside the retirement patch, this
  test and the docs still points at it — a link to a deleted Page renders a dead tile.

Bench-free: no frappe import. Run: python -m unittest erpnext_enhancements.tests.test_custom_pages_s1
"""

import ast
import json
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
PATCH = APP / "patches" / "custom_pages_s1_navigation.py"
RETIRED = "hierarchical-task-view"
BUMPED_ON_OR_AFTER = "2026-10-13 18:00:00"


def _load(path):
	return json.loads(path.read_text(encoding="utf-8"))


def _patch_constant(name):
	tree = ast.parse(PATCH.read_text(encoding="utf-8"))
	for node in tree.body:
		if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
			return ast.literal_eval(node.value)
	raise AssertionError(f"{name} not found in {PATCH.name}")


class TestEveryPageHasATitle(unittest.TestCase):
	def test_titles(self):
		pages = sorted(APP.glob("*/page/*/*.json"))
		self.assertGreater(len(pages), 20)
		for path in pages:
			with self.subTest(page=path.parent.name):
				self.assertTrue((_load(path).get("title") or "").strip(), f"{path.name} has no title")


class TestThePatchCoversWhatItImports(unittest.TestCase):
	def test_registered(self):
		entries = (APP / "patches.txt").read_text(encoding="utf-8").splitlines()
		self.assertIn("erpnext_enhancements.patches.custom_pages_s1_navigation", entries)

	def test_every_listed_file_exists_and_is_bumped(self):
		listed = [("workspace", f, s) for f, s in _patch_constant("WORKSPACES")]
		listed += [("page", f, s) for f, s in _patch_constant("PAGES")]
		self.assertTrue(listed)
		for kind, folder, slug in listed:
			path = APP / folder / kind / slug / f"{slug}.json"
			with self.subTest(file=f"{folder}/{kind}/{slug}"):
				self.assertTrue(path.exists(), f"{path} is missing")
				self.assertGreaterEqual(_load(path)["modified"][:19], BUMPED_ON_OR_AFTER)

	def test_the_new_entries_are_there(self):
		finance = _load(APP / "accounting_intake/workspace/finance_hub/finance_hub.json")
		self.assertIn("stripe-payments-dashboard", [s.get("link_to") for s in finance["shortcuts"]])
		for hub in ("operations_hub", "sales_hub"):
			links = _load(APP / f"enhancements_core/workspace/{hub}/{hub}.json")["links"]
			with self.subTest(hub=hub):
				self.assertIn("contract-builder", [l.get("link_to") for l in links])
		training = _load(APP / "training/workspace/training/training.json")["links"]
		for page in ("training-review", "training-glossary-review"):
			with self.subTest(page=page):
				self.assertIn(page, [l.get("link_to") for l in training])

	def test_the_patch_never_forces_the_page_delete(self):
		"""A forced delete skips the link checks and leaves whatever still points at the
		Page dangling; unforced, delete_doc refuses and the patch logs it."""
		src = PATCH.read_text(encoding="utf-8")
		call = re.search(r'frappe\.delete_doc\("Page"[^\n]*\)', src)
		self.assertIsNotNone(call)
		self.assertNotIn("force", call.group(0))


class TestTheRetiredPageIsGone(unittest.TestCase):
	def test_no_files(self):
		self.assertFalse((APP / "task_enhancements/page/hierarchical_task_view").exists())
		self.assertFalse((APP / "public/js/task_enhancements/task_enhancements.js").exists())

	def test_nothing_links_to_it(self):
		allowed = {PATCH, Path(__file__).resolve()}
		for path in APP.rglob("*"):
			if path.suffix not in {".json", ".js", ".py", ".html"} or path in allowed:
				continue
			with self.subTest(file=str(path.relative_to(APP))):
				self.assertNotIn(RETIRED, path.read_text(encoding="utf-8", errors="ignore"))


if __name__ == "__main__":
	unittest.main()
