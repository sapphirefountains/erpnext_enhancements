"""Every literal percent inside a parameterised query is doubled. Bench-free.

The defect this fences (shipped in v1.342.4, fixed in v1.344.3) is one sentence of prose and
it took the project procurement panel down completely::

    drafts counted as ordered, received orders shown 0%. Routing them here gives

That line is a ``/* ... */`` comment inside the query string
``project_enhancements.get_procurement_status`` hands to ``frappe.db.sql`` **with values**.
Values mean MySQLdb mogrifies, and mogrifying is literally ``query % args`` — so ``%.`` in
"0%. Routing" is not prose to Python, it is a conversion specifier. The real query raised::

    TypeError: not enough arguments for format string

re-raised by MySQLdb as ``ProgrammingError`` before the database was ever contacted. What
makes it expensive out of proportion to its size:

* **It is invisible to SQL review.** The query is valid SQL and the comment is a valid SQL
  comment; paste it into a MariaDB client and it runs. The failure lives one layer below
  where anyone reading a query is looking.
* **There is no partial failure.** The endpoint raises on every call, for every project,
  from the moment the sentence is written.
* **The trigger is writing a comment** — the one edit everybody makes freely and nobody
  tests. The offending line changed no behaviour by intent.
* **And raising is the good case.** Which failure you get depends on the characters that
  happen to follow the percent. ``"/* 0% received */ ... %(project)s"`` does *not* raise:
  ``% r`` is a valid ``repr`` conversion, so Python substitutes the repr of the whole
  argument dict into the middle of the SQL comment and the query runs. A stray percent is
  therefore a coin flip between a loud crash and a silently rewritten query.

The rule this file enforces
---------------------------------------------------------------------------

In any string literal carrying a **named** placeholder (``%(name)s``) — the binding style
every raw query in this app uses — each ``%`` must begin ``%(name)s``, ``%s``/``%d``, or an
escaped ``%%``. The named placeholder is the signal: a string containing one is bound by the
driver, so its percents belong to Python, not to the reader.

Scope, stated so a green run is not read as more than it is
---------------------------------------------------------------------------

* Only **named**-placeholder strings are checked. Positional ``%s`` strings are skipped
  deliberately: ``"%s declares module %r"`` in an assertion message is ordinary Python
  formatting and is much more common in this repo than positional SQL, so enforcing there
  would put noise in front of the signal.
* It is a source-level check over string *literals*. A query assembled by concatenation or
  ``.format()`` at runtime is invisible to it, as is one built in JavaScript or a Server
  Script.
* It proves a string is bindable, not that the query is correct.

Run: python -m unittest erpnext_enhancements.tests.test_sql_percent_escaping -v
"""

import ast
import re
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]

# This file's own docstring and fixtures argue about stray percents by quoting them, so it
# is the one module that cannot be its own subject.
SELF = Path(__file__).resolve()

# A named placeholder: what makes a string "parameterised" for our purposes.
NAMED = re.compile(r"%\([A-Za-z_][A-Za-z0-9_]*\)[sdifr]")

# Everything Python's % operator binds without an argument position we cannot see.
LEGAL_AT_PERCENT = re.compile(r"%(?:\([A-Za-z_][A-Za-z0-9_]*\)[sdifr]|[sdifr]|%)")


def _stray_percents(text):
	"""Yield ``(offset, excerpt)`` for each ``%`` that begins nothing Python can bind."""
	i = 0
	while True:
		j = text.find("%", i)
		if j < 0:
			return
		match = LEGAL_AT_PERCENT.match(text, j)
		if match:
			i = match.end()
			continue
		excerpt = text[max(0, j - 60) : j + 60].replace("\n", " ")
		yield j, " ".join(excerpt.split())
		i = j + 1


def _parameterised_strings():
	"""Every string literal in the app that carries a named placeholder."""
	for path in sorted(APP_ROOT.rglob("*.py")):
		if path.resolve() == SELF:
			continue
		try:
			tree = ast.parse(path.read_text(encoding="utf-8"))
		except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - not this file's job
			continue
		for node in ast.walk(tree):
			if isinstance(node, ast.Constant) and isinstance(node.value, str):
				if NAMED.search(node.value):
					yield path, node.lineno, node.value


def _procurement_query():
	"""The literal that actually broke, read from source without importing frappe."""
	source = (APP_ROOT / "project_enhancements" / "__init__.py").read_text(encoding="utf-8")
	for node in ast.walk(ast.parse(source)):
		if isinstance(node, ast.Constant) and isinstance(node.value, str):
			if "combined_results" in node.value and NAMED.search(node.value):
				return node.value
	raise AssertionError(
		"get_procurement_status's union query no longer contains 'combined_results'. "
		"Re-point this fence at whatever replaced it rather than deleting it."
	)


class TestSQLPercentEscaping(unittest.TestCase):
	def test_no_unescaped_percent_in_parameterised_strings(self):
		offenders = [
			f"{path.relative_to(APP_ROOT.parent)}:{lineno}: ...{excerpt}..."
			for path, lineno, text in _parameterised_strings()
			for _offset, excerpt in _stray_percents(text)
		]

		self.assertEqual(
			offenders,
			[],
			"Unescaped '%' in a string that also binds a named parameter. MySQLdb mogrifies "
			"such a string with Python's % operator, so a lone '%' is read as a conversion "
			"specifier: the query either raises ProgrammingError('not enough arguments for "
			"format string') before reaching the database, or silently interpolates the "
			"argument dict into your SQL. Double it to '%%'.\n  " + "\n  ".join(offenders),
		)

	def test_the_procurement_query_still_binds(self):
		"""The end the user sees: the query that broke can be mogrified again."""
		bound = _procurement_query() % {"project": "PRJ-00756"}
		self.assertIn("po.project) = PRJ-00756", bound)
		# And the comment survives as the sentence a reader meant to write.
		self.assertIn("received orders shown 0%.", bound)

	def test_undoing_one_escape_reproduces_the_outage(self):
		"""A guard whose detector is broken passes forever; prove this one fires."""
		query = _procurement_query()
		self.assertEqual(list(_stray_percents(query)), [])

		broken = query.replace("%%", "%", 1)
		self.assertTrue(list(_stray_percents(broken)), "detector missed a stray percent")
		with self.assertRaises(TypeError):
			broken % {"project": "PRJ-00756"}

	def test_a_stray_percent_can_corrupt_instead_of_raising(self):
		"""Why the rule is 'always double', not 'double when it crashes'."""
		silent = "SELECT 1 /* 0% received */ WHERE project = %(project)s"
		self.assertTrue(list(_stray_percents(silent)))
		self.assertIn("'project': 'PRJ-00756'", silent % {"project": "PRJ-00756"})


if __name__ == "__main__":
	unittest.main()
