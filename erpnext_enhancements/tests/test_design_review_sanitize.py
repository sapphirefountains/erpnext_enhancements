"""Bench-free tests for the concept-screen sanitizer (WI-079 slice 5, ADR 0016 §2).

Concept screens are model-written HTML rendered inside the Desk's origin. The sanitizer is one
of two layers (the sandboxed, policy-locked frame is the other), and these pin what it must
never let through, and what the screens need it to keep: SVG icons with their ``viewBox``,
``data-c`` part names, inline styles without URLs.

Stdlib only. Run: python -m unittest erpnext_enhancements.tests.test_design_review_sanitize -v
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.design_review.sanitize import sanitize_css, sanitize_html

HOSTILE = (
	'<div data-c="Top bar" onclick="parent.x=1">x<script>parent.x=2</script>'
	'<img src="https://evil.example/x.png" onerror="parent.x=3">'
	'<a href="javascript:parent.x=4">l</a>'
	'<div style="background:url(https://evil.example/y)">y</div>'
	'<iframe src="https://evil.example"></iframe>'
	'<svg><foreignObject><body onload="parent.x=5"></body></foreignObject></svg>'
	"<style>@import url(https://evil.example/z.css)</style>"
	"<!--[if IE]><script>parent.x=6</script><![endif]-->"
	'<form action="https://evil.example"><input name="q"></form>'
	'<meta http-equiv="refresh" content="0;url=https://evil.example">'
	"</div>"
)


class TestSanitizeHtml(unittest.TestCase):
	def setUp(self):
		self.clean, self.dropped = sanitize_html(HOSTILE)

	def test_no_script_survives(self):
		low = self.clean.lower()
		for needle in (
			"<script",
			"parent.x",
			"onclick",
			"onerror",
			"onload",
			"javascript:",
			"<iframe",
			"<style",
			"@import",
			"<form",
			"<meta",
			"foreignobject",
			"<!--",
		):
			self.assertNotIn(needle, low, needle)

	def test_no_external_url_survives(self):
		self.assertNotIn("evil.example", self.clean)

	def test_links_point_nowhere(self):
		self.assertIn('<a href="#">l</a>', self.clean)

	def test_drops_are_reported(self):
		for item in ("script", "iframe", "@onclick", "@onerror", "@src", "@style", "style", "form", "meta"):
			self.assertIn(item, self.dropped, item)

	def test_keeps_what_concepts_need(self):
		html = (
			'<div class="ux-app" data-c="Video block" style="width:390px;height:844px">'
			'<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M8 5v14l11-7z"/></svg>'
			'<button type="button" class="ux-btn" aria-label="Play video">PLAY</button>'
			'<input type="checkbox" checked><label>Strainer is clear</label></div>'
		)
		clean, dropped = sanitize_html(html)
		self.assertEqual(dropped, [])
		self.assertIn('viewBox="0 0 24 24"', clean)
		self.assertIn('data-c="Video block"', clean)
		self.assertIn('style="width:390px;height:844px"', clean)
		self.assertIn('aria-label="Play video"', clean)
		self.assertIn("<path", clean)

	def test_data_image_kept_other_src_dropped(self):
		ok = '<img src="data:image/png;base64,iVBORw0KGgo=" alt="x">'
		self.assertIn("data:image/png", sanitize_html(ok)[0])
		self.assertNotIn("src=", sanitize_html('<img src="/files/private/secret.png">')[0])
		self.assertNotIn("src=", sanitize_html('<img src="data:text/html;base64,PHNjcmlwdD4=">')[0])

	def test_attribute_values_are_escaped(self):
		# The value stays inside its quotes: no attribute boundary appears after it.
		clean, _ = sanitize_html('<div title="a&quot; onmouseover=&quot;x">t</div>')
		self.assertEqual(clean, '<div title="a&quot; onmouseover=&quot;x">t</div>')
		self.assertNotIn('" onmouseover=', clean)

	def test_text_is_escaped(self):
		clean, _ = sanitize_html("<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>")
		self.assertNotIn("<script>", clean)


class TestSanitizeCss(unittest.TestCase):
	def test_fetching_rules_are_dropped(self):
		css = (
			".a{color:red}.b{background:url(https://evil.example/x)}@import url(x.css);"
			"@font-face{font-family:x;src:url(https://evil.example/f.woff2)}.c{width:1px}</style><script>"
		)
		clean, dropped = sanitize_css(css)
		self.assertIn(".a{color:red}", clean)
		self.assertIn(".c{width:1px}", clean)
		self.assertNotIn("evil.example", clean)
		self.assertNotIn("url(", clean)
		self.assertNotIn("</", clean)
		self.assertTrue(dropped)


if __name__ == "__main__":
	unittest.main()
