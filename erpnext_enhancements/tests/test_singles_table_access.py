# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""``tabSingles`` is never reached through an ORM helper that orders. Bench-free.

``tabSingles`` is not a doctype table. It has exactly three columns::

    doctype | field | value

No ``name``, no ``creation``, no ``modified``. Meanwhile ``frappe.db.get_value`` and its
siblings default to ``order_by="creation"`` — so the perfectly reasonable-looking::

    frappe.db.get_value(
        "Singles", {"doctype": "Training Settings", "field": "warn_on_uncertified_dispatch"}, "value"
    )

compiles to a query ending ``ORDER BY creation`` and raises::

    MySQLdb.OperationalError: (1054, "Unknown column 'creation' in 'ORDER BY'")

**on every site, every time.** It is not a read that can fail; it is a read that cannot
succeed. There is no data shape, no fresh install and no migration state in which that line
returns a value.

Why it is worth a test of its own
---------------------------------------------------------------------------

It shipped in v1.395.0 and **aborted the production deploy**. A patch that raises takes
``bench migrate`` down with it, and `bench migrate` is the deploy — so the site ended up
with its schema synced, three of eight patches applied, no fixtures, no Property Setters,
no ``after_migrate`` hooks and a week-old asset bundle. One unreachable read cost the
whole release.

Three things make it worth fencing rather than just fixing:

* **It reads as the careful option.** Going to ``tabSingles`` directly rather than through
  ``get_single_value`` is what you reach for precisely when you are being careful — when you
  need to tell "the field has never been saved" (no row) apart from "somebody set it to 0".
  That is a real distinction, and this app has a documented history with it: a ``default`` on
  a new field of a Single never reaches the existing row. So the instinct is right and only
  the API is wrong, which is the kind of mistake that gets written again.
* **The surrounding code was already thinking about aborting the migrate.** The comment
  three lines below the offending call explains, at length, why the patch uses
  ``db.set_single_value`` instead of ``get_single().save()`` — *because a controller
  ``validate`` could throw and abort the deploy*. The reasoning was correct and the line
  above it did the thing anyway.
* **No test could have caught it by exercise.** The bench-free suites stub ``frappe``, so a
  stubbed ``get_value`` returns whatever the stub returns and the query is never built. Only
  a real MariaDB connection or a source rule sees it.

The rule
---------------------------------------------------------------------------

No call of the form ``<anything>.get_value("Singles", ...)`` — or ``get_values``,
``get_all``, ``get_list``, ``exists`` — unless it passes an explicit ``order_by``. Use
``frappe.db.get_single_value`` / ``set_single_value``, which read and write ``tabSingles``
by its own shape, or pass ``order_by=None`` if you genuinely need the raw row.

Run: python -m unittest erpnext_enhancements.tests.test_singles_table_access
"""

import ast
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]

#: Helpers that put `ORDER BY creation` on the query when no `order_by` is given.
#: `db.exists` is in the list because it is a thin wrapper over `get_value` and
#: inherits the same default — and additionally selects `name`, a second column
#: `tabSingles` does not have.
ORDERING_HELPERS = frozenset({"get_value", "get_values", "get_all", "get_list", "exists"})

#: Skipped: this suite's own docstring and assertions name the pattern, and a rule
#: that matches the file describing the rule is the absence-assertion trap this repo
#: has hit repeatedly.
SKIP_PARTS = frozenset({"tests", "node_modules", "__pycache__", ".git"})

PATCH = APP / "patches/enable_uncertified_dispatch_warning.py"


def _sources():
	for path in sorted(APP.rglob("*.py")):
		if SKIP_PARTS.intersection(path.parts):
			continue
		yield path


def _offenders(path):
	"""Yield ``(lineno, helper)`` for each ordering helper aimed at ``Singles``."""
	try:
		tree = ast.parse(path.read_text(encoding="utf-8"))
	except (SyntaxError, UnicodeDecodeError):
		return
	for node in ast.walk(tree):
		if not isinstance(node, ast.Call) or not node.args:
			continue
		fn = node.func
		if not isinstance(fn, ast.Attribute) or fn.attr not in ORDERING_HELPERS:
			continue
		first = node.args[0]
		if not (isinstance(first, ast.Constant) and first.value == "Singles"):
			continue
		# An explicit order_by is the author saying they know; `order_by=None`
		# builds a query with no ORDER BY at all and is the legitimate escape.
		if any(kw.arg == "order_by" for kw in node.keywords):
			continue
		yield node.lineno, fn.attr


class TestSinglesIsNotQueriedThroughTheORM(unittest.TestCase):
	def test_no_ordering_helper_is_aimed_at_the_singles_table(self):
		found = []
		for path in _sources():
			for lineno, helper in _offenders(path):
				found.append(f"{path.relative_to(APP)}:{lineno} frappe.db.{helper}(\"Singles\", ...)")
		self.assertEqual(
			found,
			[],
			"`tabSingles` has only (doctype, field, value). These calls default to "
			"ORDER BY creation and raise OperationalError 1054 on every site, every "
			"time -- and a raising patch aborts `bench migrate`, i.e. the deploy. "
			"Use frappe.db.get_single_value / set_single_value, or pass order_by=None:"
			"\n  " + "\n  ".join(found),
		)

	def test_the_scan_actually_reaches_the_patches_directory(self):
		"""A guard that silently scans nothing passes forever.

		The rule above is an absence assertion, and an empty corpus satisfies it just
		as well as a clean one. This pins that the walk reaches the specific file the
		v1.395.0 deploy died in.
		"""
		scanned = {p.resolve() for p in _sources()}
		self.assertIn(PATCH.resolve(), scanned)
		self.assertGreater(len(scanned), 200, "source walk collapsed -- rule proves nothing")


class TestTheDeployRegressionItself(unittest.TestCase):
	"""v1.395.0 -> v1.395.1, pinned by behaviour rather than by spelling."""

	def test_the_dispatch_warning_patch_reads_the_single_properly(self):
		src = PATCH.read_text(encoding="utf-8")
		tree = ast.parse(src)
		calls = {
			node.func.attr
			for node in ast.walk(tree)
			if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
		}
		self.assertIn("get_single_value", calls)

	def test_it_still_writes_without_saving_the_single(self):
		"""The original reasoning stays true: `get_single().save()` runs the controller,
		whose validate rejects the zeros an unsaved Single reads back as."""
		tree = ast.parse(PATCH.read_text(encoding="utf-8"))
		calls = {
			node.func.attr
			for node in ast.walk(tree)
			if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
		}
		self.assertIn("set_single_value", calls)
		self.assertNotIn("get_single", calls)


if __name__ == "__main__":
	unittest.main()
