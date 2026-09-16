"""`frappe.utils.cint` does not exist in the browser, and calling it took two pages down.

In Frappe's Python, ``frappe.utils`` holds ``cint``, ``flt``, ``strip_html`` and the rest. In
Frappe's **JavaScript** those same helpers are put on ``window`` and nowhere else —
``frappe/public/js/frappe/utils/number_format.js`` ends with ``Object.assign(window, {flt, cint,
…})``, and ``utils.cint`` appears nowhere in version-16's JS at all. So ``frappe.utils.cint(x)`` is
a ``TypeError`` on the first call, every time. It is not a subtle difference between versions or a
missing import: the property has never been there.

Two pages in this app shipped with it and neither ever worked:

* ``/desk/training-insights`` — broken from **v1.431.0**, the release that created the file, until
  v1.471.0. Thirty-nine releases.
* ``/desk/training-review`` — broken from **v1.467.0**, the release that created it.

**What hid it is worth more than the fix.** Both pages wrote
``xcall(...).then(render).catch(show_error)``, and a trailing ``.catch()`` catches whatever the
``then`` handler throws as well as a failed call. So a client-side TypeError was caught, relabelled
*"Could not load training analytics"*, and displayed as a server outage — while the server returned
200 with the full payload. Nothing reached the console, nothing reached the Error Log, and the
message accused the one component that was working. A page can be broken for a year that way.

This suite is a source scan rather than a browser run, because the repo has no JS test runner and
the failure needs none: the name is either written or it is not.

Run: python -m unittest erpnext_enhancements.tests.test_js_frappe_namespace
"""

import re
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent

#: Helpers Frappe's JS puts on ``window`` and never on ``frappe.utils``. Verified against
#: ``git show origin/version-16:frappe/public/js/frappe/utils/number_format.js``.
#:
#: ``frappe.utils`` in JS is a real object with real members — ``escape_html``, ``get_form_link``,
#: ``comma_and`` and plenty more — so this is a named list of the ones that are NOT there rather
#: than a ban on the namespace.
NOT_ON_FRAPPE_UTILS = (
	"cint",
	"flt",
	"strip_html",
	"strip_number_groups",
)

#: Jinja templates are Python. ``frappe.utils.formatdate`` in a ``.html`` is correct and must not
#: be swept up by a rule written about the browser.
JS_GLOBS = ("**/*.js",)

SKIP_DIRS = {"node_modules", ".git", "dist", "__pycache__"}


def _js_files():
	seen = []
	for pattern in JS_GLOBS:
		for path in APP_ROOT.glob(pattern):
			if any(part in SKIP_DIRS for part in path.parts):
				continue
			seen.append(path)
	return seen


def _without_comments(src):
	src = re.sub(r"/\*[\s\S]*?\*/", "", src)
	return re.sub(r"(?m)^\s*//.*$", "", src)


class TestNoJsCallsAPythonOnlyHelper(unittest.TestCase):
	def test_no_js_file_calls_frappe_utils_for_a_window_global(self):
		offenders = []
		for path in _js_files():
			src = _without_comments(path.read_text(encoding="utf-8"))
			for name in NOT_ON_FRAPPE_UTILS:
				for match in re.finditer(rf"frappe\.utils\.{name}\s*\(", src):
					line = src[: match.start()].count("\n") + 1
					offenders.append(f"{path.relative_to(REPO_ROOT)}:{line} frappe.utils.{name}(")
		self.assertEqual(
			sorted(offenders),
			[],
			"These are undefined in the browser and throw on the first call. "
			f"Use the bare global instead: {offenders}",
		)

	def test_the_two_pages_that_were_broken_now_use_the_global(self):
		"""Pinned by name, because both were created broken rather than broken by an edit — a
		general rule alone would not say that these two were the casualties."""
		for relative in (
			"training/page/training_insights/training_insights.js",
			"training/page/training_review/training_review.js",
		):
			with self.subTest(relative):
				src = _without_comments((APP_ROOT / relative).read_text(encoding="utf-8"))
				self.assertNotIn("frappe.utils.cint", src)
				self.assertRegex(src, r"(?<![\w.])cint\s*\(")


class TestARenderBugIsNotReportedAsAnOutage(unittest.TestCase):
	"""The thing that hid it. A trailing `.catch()` after a `.then(render)` swallows whatever the
	render throws and shows the message written for a failed call — so the page accuses the server
	while the server is answering correctly.
	"""

	INSIGHTS = APP_ROOT / "training" / "page" / "training_insights" / "training_insights.js"

	def test_the_analytics_handler_is_not_a_trailing_catch(self):
		src = _without_comments(self.INSIGHTS.read_text(encoding="utf-8"))
		block = src.split("get_training_analytics", 1)[1][:900]
		self.assertNotIn(".catch(", block)
		self.assertIn(".then(null,", block)

	def test_it_rethrows_so_the_error_still_reaches_the_console(self):
		"""Showing the message and stopping is how the next one hides too."""
		src = _without_comments(self.INSIGHTS.read_text(encoding="utf-8"))
		block = src.split("get_training_analytics", 1)[1][:1400]
		self.assertIn("throw err", block)


if __name__ == "__main__":
	unittest.main()
