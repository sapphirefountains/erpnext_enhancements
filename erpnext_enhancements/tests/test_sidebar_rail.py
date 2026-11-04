"""The Desk module sidebar as an icon rail (v1.790.0; Nik, 2026-11-03, Mock C).

Bench-free: reads files only, and runs the endpoint against a fake frappe. The sidebar behaviour is
executed, not grepped, in ``scripts/test_sidebar_rail.js``; this suite holds the seams that harness
cannot see: that the module ships in the global bundle after sidebar_memory.js (whose per-user key it
reads), that the stylesheet has the classes the script sets, that the phone bar is below 768px only,
that the endpoint is a read-only GET whose counts respect read permission, that the frappe-16.50
compat suite names this file as its one allowance, and that CI runs both.

Run: python -m unittest erpnext_enhancements.tests.test_sidebar_rail
"""

import ast
import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
MODULE = APP / "public" / "js" / "global_enhancements" / "sidebar_rail.js"
BUNDLE = APP / "public" / "js" / "erpnext_enhancements.bundle.js"
CSS = APP / "public" / "css" / "desk_enhancements.bundle.css"
API = APP / "api" / "sidebar_rail.py"
API_README = APP / "api" / "README.md"
COMPAT = APP / "tests" / "test_frappe_16_50_desk_compat.py"
HARNESS = REPO_ROOT / "scripts" / "test_sidebar_rail.js"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def read(path):
	return path.read_text(encoding="utf-8")


def code(path):
	"""The script with comments stripped: an assertion must not be satisfied by its own prose."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", read(path))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def media_block(css, query):
	"""The body of the first @media block whose condition is `query`, braces balanced."""
	start = css.index("@media " + query)
	open_at = css.index("{", start)
	depth = 0
	for i in range(open_at, len(css)):
		depth += {"{": 1, "}": -1}.get(css[i], 0)
		if depth == 0:
			return css[open_at + 1 : i]
	raise AssertionError("unbalanced @media block")


class TestShips(unittest.TestCase):
	def test_the_module_is_in_the_global_bundle_after_sidebar_memory(self):
		self.assertTrue(MODULE.is_file())
		imports = re.findall(r'^import "([^"]+)";', read(BUNDLE), flags=re.M)
		self.assertIn("./global_enhancements/sidebar_rail.js", imports)
		self.assertLess(
			imports.index("./global_enhancements/sidebar_memory.js"),
			imports.index("./global_enhancements/sidebar_rail.js"),
			"the rail reads sidebar_memory's per-user copy of the collapse key, so it loads after it",
		)

	def test_it_wraps_the_one_method_that_hides_the_sidebar(self):
		"""frappe 16.53: hides_when_collapsed (sidebar.js 721) answers dock.is_pinned, and
		apply_expanded_state then adds .sidebar-hidden; the rail is frappe's own CSS otherwise."""
		src = code(MODULE)
		self.assertIn("proto.hides_when_collapsed = function", src)
		self.assertIn('typeof proto.hides_when_collapsed !== "function"', src)
		self.assertIn("if (is_phone()) return hides.apply(this, arguments);", src)
		self.assertIn("return false;", src)

	def test_the_keys_it_shares_with_sidebar_memory(self):
		memory = code(APP / "public" / "js" / "global_enhancements" / "sidebar_memory.js")
		src = code(MODULE)
		for name in ("desk-sidebar-collapsed", "ee-nav-collapsed"):
			self.assertIn(f'"{name}"', memory)
			self.assertIn(f'"{name}"', src)

	def test_the_compat_suite_names_this_file_as_its_one_allowance(self):
		compat = read(COMPAT)
		self.assertIn('RAIL = PUBLIC_JS / "global_enhancements" / "sidebar_rail.js"', compat)
		self.assertIn("def test_the_rail_is_the_one_allowance", compat)
		# and the file keeps its side of it
		self.assertNotIn("sidebar-toggle", code(MODULE))


class TestStylesheetMatchesScript(unittest.TestCase):
	def test_the_classes_the_script_sets_are_the_ones_the_css_keys_on(self):
		src = code(MODULE)
		css = read(CSS)
		for const in ("PIN_CLASS", "RECENT_CLASS", "BADGE_CLASS", "BAR_CLASS"):
			match = re.search(rf'const {const} = "([^"]+)"', src)
			self.assertIsNotNone(match, const)
			self.assertIn("." + match.group(1), css)
		for cls in ("ee-rail-top-first", "ee-rail-top-last", "ee-rail-popover", "ee-rail-popover-order", "ee-quick-bar-item", "ee-quick-bar-on", "ee-quick-bar-hidden"):
			self.assertIn(cls, src)
			self.assertIn("." + cls, css)

	def test_the_phone_bar_is_below_768px_only(self):
		css = read(CSS)
		block = media_block(css, "(max-width: 767.98px)")
		self.assertIn(".ee-quick-bar {", block)
		self.assertIn("position: fixed;", block)
		# and nothing at desktop widths
		before = css[: css.index("@media (max-width: 767.98px)")]
		rail = before[before.index("Sidebar rail (v1.790.0") :]
		self.assertIn(".ee-quick-bar {\n    display: none;\n}", rail)

	def test_the_badge_rides_the_icon_not_the_suffix(self):
		"""frappe folds .sidebar-item-suffix to width 0 in the rail (sidebar.scss 321), so a count
		drawn there vanishes exactly where the rail needs it."""
		self.assertIn(".body-sidebar .sidebar-item-icon {\n    position: relative;\n}", read(CSS))
		self.assertIn('$icon.find("." + BADGE_CLASS)', code(MODULE))
		self.assertNotIn("sidebar-item-suffix", code(MODULE))


# ---------------------------------------------------------------- the endpoint


class _Db:
	def __init__(self, rows=None, counts=None, tables=("Route History",), doctypes=("AI Pending Action",), fail=False):
		self.rows, self.counts, self.tables, self.doctypes, self.fail = rows or [], counts or {}, set(tables), set(doctypes), fail
		self.queries, self.count_calls = [], []

	def table_exists(self, name):
		return name in self.tables

	def exists(self, doctype, name):
		return doctype == "DocType" and name in self.doctypes

	def sql(self, query, values=None, as_dict=False):
		if self.fail:
			raise RuntimeError("boom")
		self.queries.append((query, values))
		return [type("Row", (), {"route": r, "n": n, "get": lambda self, k, d=None: getattr(self, k, d)})() for r, n in self.rows]

	def count(self, doctype, filters=None):
		self.count_calls.append((doctype, filters))
		key = (doctype, "overdue" if filters and "exp_end_date" in filters else "all")
		return self.counts.get(key, 0)


class _Frappe:
	PermissionError = PermissionError

	def __init__(self, db, user="clegg@x", allowed=("Task", "AI Pending Action")):
		self.db, self.allowed = db, set(allowed)
		self.session = type("S", (), {"user": user})()
		self.errors = []

	def has_permission(self, doctype, ptype="read"):
		return doctype in self.allowed

	def log_error(self, title=None, message=None):
		self.errors.append(title)

	@staticmethod
	def get_traceback():
		return "Traceback"

	@staticmethod
	def _(s):
		return s

	def throw(self, msg, exc=Exception):
		raise exc(msg)

	@staticmethod
	def whitelist(methods=None):
		def deco(fn):
			fn.whitelisted, fn.methods = True, methods
			return fn

		return deco


def _module(fake):
	tree = ast.parse(read(API))
	body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
	ns = {"frappe": fake, "add_days": lambda d, n: f"{d}{n:+d}", "nowdate": lambda: "2026-11-03", "today": lambda: "2026-11-03"}
	exec(compile(ast.Module(body=body, type_ignores=[]), str(API), "exec"), ns)
	return ns


class TestEndpoint(unittest.TestCase):
	def test_it_is_a_read_only_get(self):
		src = read(API)
		self.assertIn('@frappe.whitelist(methods=["GET"])\ndef get_rail_data():', src)
		for forbidden in ("set_value(", ".save(", ".insert(", "db.delete", "delete_doc", "frappe.db.commit"):
			self.assertNotIn(forbidden, src)
		# tab-indented code (the docstring's wrapped bullets may start with two spaces)
		self.assertFalse([line for line in src.splitlines() if line.startswith("    ")], "tab-indented")
		self.assertIn("sidebar_rail.py", read(API_README))

	def test_the_answer(self):
		db = _Db(rows=[("List/Task/List", 16), ("Workspaces/Projects", 291)], counts={("Task", "all"): 3, ("Task", "overdue"): 1, ("AI Pending Action", "all"): 12})
		fake = _Frappe(db)
		ns = _module(fake)
		answer = ns["get_rail_data"]()
		self.assertEqual(answer["days"], 90)
		self.assertEqual(answer["usage"], {"List/Task/List": 16, "Workspaces/Projects": 291})
		self.assertEqual(answer["badges"], {"tasks": {"count": 3, "overdue": 1}, "pending_actions": 12})
		self.assertEqual(fake.errors, [])
		# scoped to the session user, bound, over the window
		(query, values), = db.queries
		self.assertIn("where user = %(user)s and creation >= %(since)s", query)
		self.assertEqual(values, {"user": "clegg@x", "since": "2026-11-03-90", "limit": 500})
		# the overdue count is only asked when there is something assigned
		self.assertEqual([c[0] for c in db.count_calls], ["Task", "Task", "AI Pending Action"])
		self.assertEqual(db.count_calls[0][1]["_assign"], ["like", "%clegg@x%"])

	def test_permission_decides_each_badge(self):
		db = _Db(counts={("Task", "all"): 3, ("AI Pending Action", "all"): 12})
		ns = _module(_Frappe(db, allowed=("Task",)))
		self.assertEqual(ns["badges"]("clegg@x"), {"tasks": {"count": 3, "overdue": 0}})
		ns = _module(_Frappe(db, allowed=()))
		self.assertEqual(ns["badges"]("clegg@x"), {})

	def test_no_pending_action_doctype_no_badge(self):
		db = _Db(counts={("AI Pending Action", "all"): 12}, doctypes=())
		ns = _module(_Frappe(db, allowed=("AI Pending Action",)))
		self.assertEqual(ns["badges"]("clegg@x"), {})

	def test_a_failed_read_is_logged_and_the_rest_still_answers(self):
		db = _Db(fail=True, counts={("Task", "all"): 2})
		fake = _Frappe(db)
		ns = _module(fake)
		answer = ns["get_rail_data"]()
		self.assertEqual(answer["usage"], {})
		self.assertEqual(answer["badges"]["tasks"], {"count": 2, "overdue": 0})
		self.assertEqual(fake.errors, ["Sidebar rail: route usage not read"])

	def test_no_route_history_table_no_usage(self):
		ns = _module(_Frappe(_Db(tables=())))
		self.assertEqual(ns["route_usage"]("clegg@x"), {})

	def test_guest_is_refused(self):
		ns = _module(_Frappe(_Db(), user="Guest"))
		with self.assertRaises(PermissionError):
			ns["get_rail_data"]()


class TestHarness(unittest.TestCase):
	def test_ci_runs_both(self):
		ci = read(CI)
		self.assertIn("node scripts/test_sidebar_rail.js", ci)
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_sidebar_rail -v", ci)

	@unittest.skipUnless(shutil.which("node"), "node is not installed")
	def test_the_harness_passes(self):
		result = subprocess.run(["node", str(HARNESS)], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120)
		self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
		self.assertIn("all checks passed", result.stdout)


if __name__ == "__main__":
	unittest.main()
