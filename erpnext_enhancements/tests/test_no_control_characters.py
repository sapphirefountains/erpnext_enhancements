# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""No source file carries a stray ASCII control character. Bench-free.

Every text file in this repo should contain printable characters, tabs and newlines and
nothing else. A byte below 0x20 that is not a tab, newline or carriage return got there by
accident, and the accident is always the same one.

What actually happens
---------------------------------------------------------------------------

A backslash escape written into a file *through a shell* can be eaten before the file ever
sees it. ``\\25B8`` — the CSS escape for the small right-pointing triangle — went into
``public/css/training/desk_nav.css`` and arrived as a literal 0x15 byte followed by the
letters ``B8``, because something between the editor and the file read ``\\2`` ``5`` as an
octal escape. The rail drew a tofu box and the text "B8" beside every group heading, on
production, for an hour after v1.472.0 deployed.

That one was visible. The others were not:

* ``tests/test_training_help.py`` asked for ``\\b(?:within|inside|no later than)`` and got a
  literal **backspace** where the word boundary should be. The regex then required an actual
  0x08 character in the glossary text, which no glossary entry has ever contained, so
  ``assertIsNone(hit)`` passed for all 704 entries — forever, for any data. The test was
  written to catch a glossary entry that invents a company deadline ("backwash every 6
  weeks"); from the day it was written it could not have caught one.
* ``training/README.md`` documents anchoring a grep on ``\\btn-``. It rendered the advice
  with the backslash-b replaced by a backspace, so the prose said ``tn-`` — the exact
  unanchored pattern the sentence warns against.
* ``CHANGELOG.md`` lost the ``\\1`` out of a regex backreference the same way.

Note the direction of every one of those failures: **the file still parses, the test still
passes, the page still renders.** Nothing raises. A backspace is a perfectly legal character
in a Python string, a CSS ``content`` value and a Markdown paragraph. There is no stage that
rejects it, which is why it survived three days and five files before anybody looked at the
bytes.

Why a test and not a lint rule
---------------------------------------------------------------------------

``ruff`` is advisory in CI here, and none of these are syntax errors in any case. The only
thing that distinguishes a corrupted file from a correct one is the byte, so the byte is what
gets checked.

The suite carries its own positive control (:class:`TestTheScannerActuallyFires`). A guard
that cannot fire is the failure being fenced against, and this file is not exempt from it.
"""

import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent

#: Tab, newline and carriage return are the three control characters a text file may hold.
#: Everything else below 0x20, plus DEL, is damage.
ALLOWED = frozenset({0x09, 0x0A, 0x0D})
FORBIDDEN = frozenset(set(range(0x00, 0x20)) - ALLOWED | {0x7F})

#: Checked by extension rather than by sniffing: a JPEG that happens to start with printable
#: bytes must never be read as text, and the repo ships ~150 of them under
#: `public/images/maintenance/`.
TEXT_SUFFIXES = frozenset(
	{
		".py",
		".js",
		".mjs",
		".css",
		".scss",
		".html",
		".json",
		".md",
		".txt",
		".yml",
		".yaml",
		".toml",
		".cfg",
		".ini",
		".sh",
		".sql",
		".csv",
	}
)

#: Matched against the path **relative to the repo root**, never the absolute path. A git
#: worktree of this repo lives at `.claude/worktrees/<name>/`, so an absolute-path check that
#: skipped `.claude` skipped the entire checkout when the suite ran from a worktree — every
#: file, silently, leaving `test_every_text_file_is_clean` green over nothing.
#: `worktrees` is on the list for the reverse reason: from the main checkout, descending into
#: it re-scans every worktree's copy of the repo.
SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "env", "dist", "worktrees"})


def _text_files():
	for path in sorted(REPO.rglob("*")):
		if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
			continue
		if SKIP_DIRS.intersection(path.relative_to(REPO).parts):
			continue
		yield path


def _offenders(data):
	"""Yield ``(line, column, byte)`` for every forbidden byte in ``data``."""
	line = 1
	column = 1
	for byte in data:
		if byte in FORBIDDEN:
			yield line, column, byte
		if byte == 0x0A:
			line += 1
			column = 1
		else:
			column += 1


class TestNoSourceFileCarriesAControlCharacter(unittest.TestCase):
	def test_every_text_file_is_clean(self):
		damaged = []
		for path in _text_files():
			try:
				data = path.read_bytes()
			except OSError:
				continue
			for line, column, byte in _offenders(data):
				damaged.append(f"{path.relative_to(REPO).as_posix()}:{line}:{column} holds 0x{byte:02x}")
				break
		self.assertEqual(
			damaged,
			[],
			"Stray control characters, almost certainly a backslash escape eaten by a shell "
			"on the way into the file. Rewrite the escape without going through a shell, or "
			"use the literal character:\n  " + "\n  ".join(damaged),
		)

	def test_the_sweep_reaches_a_useful_number_of_files(self):
		"""A path bug that made `_text_files` yield nothing would pass the test above."""
		self.assertGreater(len(list(_text_files())), 200)


class TestTheScannerActuallyFires(unittest.TestCase):
	"""The positive control.

	The bug this suite exists for is a check that silently cannot match. Asserting only that
	nothing is wrong would reproduce it exactly: rename a constant, invert a condition, and
	the suite goes quietly green on a repo full of damage.
	"""

	def test_a_backspace_is_caught(self):
		found = list(_offenders(b"r" + bytes([0x22, 0x08]) + b"within"))
		self.assertEqual([(1, 3, 0x08)], found)

	def test_the_octal_escape_wreckage_is_caught(self):
		found = list(_offenders(bytes([0x15]) + b"B8"))
		self.assertEqual([(1, 1, 0x15)], found)

	def test_tabs_newlines_and_carriage_returns_are_not_caught(self):
		self.assertEqual([], list(_offenders(b"\tone\r\n\ttwo\r\n")))

	def test_the_line_and_column_are_the_damaged_ones(self):
		data = b"clean\nalso clean\nbad " + bytes([0x01]) + b"here\n"
		self.assertEqual([(3, 5, 0x01)], list(_offenders(data)))


if __name__ == "__main__":
	unittest.main()
