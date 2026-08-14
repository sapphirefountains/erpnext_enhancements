"""Markdown → Google Chat formatting. **Bench-free, `unittest`, and it imports no frappe.**

`chat/gchat/markdown.py` is `re` and nothing else, deliberately, so this suite needs no stub
set and can never cross-talk with another suite's.

THE BUG, RESTATED AS A TEST
===========================

Google Chat's bold is a *single* asterisk. Triton emits CommonMark. So `**Incompressibility:**`
arrived as `*` + `*Incompressibility:*` + `*` — bold, with a stray asterisk glued to it. Read as
"the formatting works sometimes and adds a spare character other times", which is how it was
reported and is not what was happening: it is one deterministic mistranslation.

The first test is that sentence as an assertion.
"""

from __future__ import annotations

import pathlib
import unittest

from erpnext_enhancements.chat.gchat.markdown import to_google_chat as md


class TheReportedBug(unittest.TestCase):
	def test_double_asterisk_bold_becomes_single_asterisk_bold(self) -> None:
		self.assertEqual(md("**Incompressibility:** water"), "*Incompressibility:* water")

	def test_no_stray_asterisk_survives_anywhere(self) -> None:
		"""The visible symptom, asserted directly.

		Counting is the right assertion here rather than an equality: a stray delimiter is
		exactly what a reader sees, and it is invisible in a diff of two similar strings.
		"""
		converted = md("**one** and **two** and **three**")
		self.assertEqual(converted.count("*"), 6, f"expected three bold pairs, got {converted!r}")
		self.assertNotIn("**", converted)

	def test_a_bulleted_answer_reads_as_a_list(self) -> None:
		source = "- **Incompressibility:** density is constant\n- **Newtonian:** viscosity is constant"
		self.assertEqual(
			md(source),
			"• *Incompressibility:* density is constant\n• *Newtonian:* viscosity is constant",
		)


class TheSubstitutionTrap(unittest.TestCase):
	"""The reason this is a tokeniser and not a chain of `re.sub` calls."""

	def test_bold_does_not_degrade_into_italic(self) -> None:
		"""`**b**` → `*b*` followed by `*i*` → `_i_` converts its own output. Every bold word
		would silently become italic, and any ordering has a case like it because the source
		and target alphabets overlap."""
		self.assertEqual(md("**bold** and *italic*"), "*bold* and _italic_")

	def test_asterisk_italic_becomes_underscore_italic(self) -> None:
		self.assertEqual(md("*just italic*"), "_just italic_")

	def test_underscore_italic_is_left_alone(self) -> None:
		self.assertEqual(md("_already italic_"), "_already italic_")

	def test_nested_emphasis_converts_both_levels(self) -> None:
		self.assertEqual(md("**bold with *inner* italic**"), "*bold with _inner_ italic*")


class CodeIsLiteral(unittest.TestCase):
	def test_inline_code_keeps_its_asterisks(self) -> None:
		self.assertEqual(md("use `**not bold**` here"), "use `**not bold**` here")

	def test_a_fenced_block_is_untouched(self) -> None:
		source = "before\n\n```python\nx = **1**\n# not a heading\n- not a bullet\n```\n\nafter"
		self.assertEqual(md(source), source)

	def test_code_and_conversion_coexist_on_one_line(self) -> None:
		self.assertEqual(md("**set** `retention_mode` **now**"), "*set* `retention_mode` *now*")


class TheRestOfTheTable(unittest.TestCase):
	def test_a_heading_becomes_bold_because_chat_has_no_headings(self) -> None:
		self.assertEqual(md("# Fluid dynamics"), "*Fluid dynamics*")
		self.assertEqual(md("### Deeper"), "*Deeper*")

	def test_strikethrough_loses_one_tilde(self) -> None:
		self.assertEqual(md("~~gone~~"), "~gone~")

	def test_a_link_emits_the_label_and_the_url(self) -> None:
		"""Chat autolinks bare URLs. `<url|label>` is Slack's syntax and arrives in Chat as
		those literal characters, which is worse than no link at all."""
		self.assertEqual(md("see [the docs](https://example.com/x)"), "see the docs: https://example.com/x")
		self.assertEqual(md("[](https://example.com)"), "https://example.com")

	def test_a_numbered_list_is_already_readable(self) -> None:
		self.assertEqual(md("1. first\n2. second"), "1. first\n2. second")

	def test_an_identifier_is_not_italicised(self) -> None:
		"""`retrieve_for_oversight` must survive. Triton emits identifiers constantly, and
		CommonMark's intraword rule exists for exactly this."""
		self.assertEqual(md("call retrieve_for_oversight() first"), "call retrieve_for_oversight() first")
		self.assertEqual(md("a_b and _real_ italic"), "a_b and _real_ italic")

	def test_unmatched_delimiters_pass_through(self) -> None:
		for text in ("2 * 3 = 6", "a ** b", "50% _ 60%", "no markdown at all"):
			with self.subTest(text=text):
				self.assertEqual(md(text), text)

	def test_empty_and_none_are_empty(self) -> None:
		self.assertEqual(md(""), "")
		self.assertEqual(md(None), "")

	def test_conversion_is_NOT_idempotent_and_that_is_a_property_to_know(self) -> None:
		"""**Never convert converted text.** Asserted as a fact, not aspired to as a guarantee.

		`*x*` is Chat's *bold* and CommonMark's *italic*. The two languages disagree about what
		that string means, so no translator between them can be idempotent — a second pass reads
		its own bold as italic and emits `_x_`. This test pins the collision so nobody "fixes"
		it and quietly makes bold unreachable.

		What keeps it safe in production is the call site, not the function: `outbox.relay_text`
		converts from `Chat Message.text`, the stored CommonMark, on **every** relay including
		the re-relay after an edit. The source of truth is never the converted form, so the
		second pass never happens. `test_relay_text_converts_from_the_stored_markdown` is the
		assertion that this stays true.
		"""
		once = md("**bold**")
		self.assertEqual(once, "*bold*")
		self.assertEqual(md(once), "_bold_", "the collision changed shape — re-read the docstring")


class TheCallSiteIsWhatKeepsItSafe(unittest.TestCase):
	"""The conversion is not idempotent, so where it runs matters as much as what it does."""

	def test_relay_text_converts_from_the_stored_markdown_and_only_for_triton(self) -> None:
		"""Source-level, because the alternative needs a bench and this repo has no bench job.

		Two properties, both load-bearing:

		* the converter is reached from `relay_text`, which reads `Chat Message.text` — the
		  stored CommonMark — on **every** relay including the re-relay after an edit. So the
		  second pass that would degrade bold into italic never happens.
		* it is gated on `sender_kind == "Triton"`. A coworker typing `2 * 3` typed arithmetic,
		  and a relay that rewrites what somebody wrote is a relay that lies about what they
		  said.
		"""
		source = (pathlib.Path(__file__).resolve().parents[1] / "chat" / "sync" / "outbox.py").read_text(
			encoding="utf-8"
		)
		self.assertIn("to_google_chat", source, "relay_text no longer converts markdown at all")
		self.assertIn(
			'!= "Triton"',
			source,
			"the Triton gate is gone — a coworker's literal asterisks would now be rewritten",
		)
		self.assertIn(
			"_formatted_for_chat(body, message)",
			source,
			"the conversion moved out of relay_text; check it still runs before the byte budget",
		)


if __name__ == "__main__":
	unittest.main()
