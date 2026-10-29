"""Custom HTML Blocks take their status colours from the app's ``--sf-*`` tokens (v1.641.0).

The department widgets coloured good / watch / bad with literal hex, about fifty times each
(``#1e7e51``, ``#b7791f``, ``#c0392b``), and the older blocks with near-duplicates of each
(``#28a745``, ``#f0ad4e``, ``#dc2626`` ...). A literal is one value in both themes and forty
places to change. The tokens in ``public/css/sf_tokens.bundle.css`` are defined on ``:root`` and,
being custom properties, inherit into every block's shadow root, so a block reads
``var(--sf-good)`` / ``var(--sf-warn)`` / ``var(--sf-danger)`` (and ``--sf-on-*`` on a fill), and
one file sets the status colours for every block in both themes.

``--sf-warn`` is a fill (yellow, 1.7:1 on white, "never text" in docs/app-design-system.md), so
warn TEXT is ``color-mix(in srgb, var(--sf-warn) 50%, var(--sf-text-strong))``: 5.3:1 on a white
card and 13:1 on a dark one, because ``--sf-text-strong`` flips with the theme.

This fails the build on any of the converted literals coming back, in any block's ``.css`` or
``.js``, compared case-insensitively, with comments stripped first: a comment naming the old hex
(as a changelog might) must not trip it, and nothing here can be satisfied by a comment either.
It also fails on a ``var(--sf-...)`` that ``sf_tokens.bundle.css`` does not define, which
otherwise renders as no colour at all, silently.

Bench-free: the files are read as text.

Run: python -m unittest erpnext_enhancements.tests.test_block_status_tokens
"""

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
BLOCKS_DIR = APP / "custom_html_blocks"
TOKENS = APP / "public" / "css" / "sf_tokens.bundle.css"

# Every literal status colour the blocks used before v1.641.0, and the token that replaced it.
BANNED_HEXES = {
	# good
	"1e7e51": "--sf-good (the department widgets' green; --sf-green is this value)",
	"28a745": "--sf-good (KPI Cockpit, Projects Dashboard badge)",
	"16a34a": "--sf-good (any status use; see CATEGORICAL)",
	"16794c": "--sf-good (My Travel's fallback for --text-on-green)",
	"e4f5e9": "a --sf-good tint (My Travel's fallback for --bg-green)",
	# warn
	"b7791f": "--sf-warn (a fill), or the warn-text color-mix",
	"f0ad4e": "--sf-warn (a fill), or the warn-text color-mix",
	"ffc107": "--sf-warn with --sf-on-warn (Projects Dashboard badge)",
	"8a5a00": "the warn-text color-mix (Bank Balances)",
	"fff1e7": "a --sf-warn tint (My Travel's fallback for --bg-orange)",
	"bd3e0c": "the warn-text color-mix (My Travel's fallback for --text-on-orange)",
	# bad
	"c0392b": "--sf-danger",
	"dc2626": "--sf-danger (Task Dashboard)",
	"e24c4c": "--sf-danger (KPI Cockpit, Projects Dashboard)",
	"dc3545": "--sf-danger with --sf-on-danger (Projects Dashboard badge)",
	"d9534f": "--sf-danger (Projects Dashboard overdue outline)",
	"c9302c": "--sf-danger (Projects Dashboard overdue row)",
	"a32d2a": "--sf-danger (Bank Balances)",
}

# The sites where a banned green is a CATEGORY colour, not a status, and stays literal: exactly
# this many uses of that hex in that file, and nowhere else. A status use of the same green (a
# fourth #16a34a in the Task Dashboard, or #28a745 anywhere else) still fails.
#   - task_dashboard.css, #16a34a x3: the Projects panel's accent, its rank circles and its
#     progress bars. The panels are told apart by colour (overdue red, today blue, events purple,
#     projects green); green here names the panel, not a state.
#   - projects_dashboard.js, #28a745 x1: the "Avg % Complete" summary card, one of a categorical
#     row of six (blue, red, green, orange, purple, grey). It is the fallback of --green-500.
CATEGORICAL = {
	("task_dashboard.css", "16a34a"): 3,
	("projects_dashboard.js", "28a745"): 1,
}

# The same colours written as rgba() tints: now color-mix(in srgb, var(--sf-...) N%, transparent).
BANNED_RGB = {
	"192, 57, 43": "--sf-danger",
	"217, 83, 79": "--sf-danger",
	"183, 121, 31": "--sf-warn",
	"240, 173, 78": "--sf-warn",
	"30, 126, 81": "--sf-good",
}

HEX = re.compile(r"#(" + "|".join(BANNED_HEXES) + r")(?![0-9a-z_-])", re.I)
RGB = re.compile(
	r"rgba?\(\s*(" + "|".join(re.escape(t).replace(r",\ ", r"\s*,\s*") for t in BANNED_RGB) + r")\b"
)
TOKEN_USE = re.compile(r"var\(\s*(--sf-[\w-]+)")
TOKEN_DEF = re.compile(r"(--sf-[\w-]+)\s*:")
STATUS_USE = re.compile(r"var\(\s*--sf-(?:good|warn|danger)\s*\)")


def strip_comments(text):
	"""Block comments, then // line comments not preceded by ':' or a quote (so a URL stays)."""
	text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
	return re.sub(r"(^|[^:\"'\\])//[^\n]*", r"\1", text)


def block_files():
	return sorted([*BLOCKS_DIR.glob("*.css"), *BLOCKS_DIR.glob("*.js")])


def code(path):
	return strip_comments(path.read_text(encoding="utf-8"))


class TestTheMatcher(unittest.TestCase):
	"""A scanner that finds nothing proves nothing: check it on known text first."""

	def test_it_finds_a_literal_in_code_in_either_case(self):
		self.assertTrue(HEX.search(strip_comments(".x { color: #C0392B; }")))
		self.assertTrue(HEX.search(strip_comments('el.style.color = "#1e7e51";')))
		self.assertTrue(RGB.search(strip_comments(".x { background: rgba(192, 57, 43, 0.12); }")))
		self.assertTrue(RGB.search(strip_comments(".x { background: rgba(192,57,43,.12); }")))

	def test_a_comment_naming_the_old_hex_does_not_trip_it(self):
		self.assertFalse(HEX.search(strip_comments("/* was #c0392b */ .x { color: var(--sf-danger); }")))
		self.assertFalse(HEX.search(strip_comments("const a = 1; // was #b7791f")))

	def test_an_id_selector_or_a_longer_hex_is_not_a_status_colour(self):
		self.assertFalse(HEX.search("#c0392b-body"))
		self.assertFalse(HEX.search("#1e7e51aa"))

	def test_a_url_is_not_mistaken_for_a_comment(self):
		self.assertIn("#dc2626", strip_comments('const u = "https://x.example/#dc2626";'))


class TestBlockStatusTokens(unittest.TestCase):
	def test_the_scan_sees_the_blocks(self):
		files = block_files()
		# 41 blocks (80+ files) until v1.756.0 moved the four Marketing widgets onto Marketing Performance.
		self.assertGreaterEqual(len(files), 72, "fewer block files than the 37 blocks ship")

	def test_no_block_writes_a_literal_status_colour(self):
		for path in block_files():
			text = code(path)
			found = {}
			for match in HEX.finditer(text):
				key = match.group(1).lower()
				found[key] = found.get(key, 0) + 1
			for key, count in sorted(found.items()):
				allowed = CATEGORICAL.get((path.name, key), 0)
				with self.subTest(file=path.name, hex=key):
					self.assertLessEqual(
						count, allowed, f"{path.name}: #{key} x{count} -> use {BANNED_HEXES[key]}"
					)
			for match in RGB.finditer(text):
				with self.subTest(file=path.name, rgb=match.group(0)):
					key = re.sub(r"\s*,\s*", ", ", match.group(1))
					self.fail(f"{path.name}: {match.group(0)} -> color-mix with {BANNED_RGB[key]}")

	def test_the_categorical_sites_are_exactly_these(self):
		# Not one more (a status use slipped in) and not one fewer (a stale allowance that would
		# wave the next status use through).
		for (name, key), allowed in CATEGORICAL.items():
			with self.subTest(file=name, hex=key):
				hits = [m for m in HEX.finditer(code(BLOCKS_DIR / name)) if m.group(1).lower() == key]
				self.assertEqual(len(hits), allowed)

	def test_the_blocks_read_the_status_tokens(self):
		# Read from code, not comments: a header saying "uses --sf-good" satisfies nothing.
		using = [path.name for path in block_files() if STATUS_USE.search(code(path))]
		self.assertGreaterEqual(len(using), 30, using)

	def test_every_sf_token_a_block_reads_is_defined(self):
		defined = set(TOKEN_DEF.findall(strip_comments(TOKENS.read_text(encoding="utf-8"))))
		self.assertIn("--sf-good", defined)
		for path in block_files():
			for token in sorted(set(TOKEN_USE.findall(code(path)))):
				with self.subTest(file=path.name, token=token):
					self.assertIn(
						token, defined, f"{path.name} reads {token}, which sf_tokens.bundle.css lacks"
					)


if __name__ == "__main__":
	unittest.main()
