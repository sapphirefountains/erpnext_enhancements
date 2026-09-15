"""A doc handed to ``make_quick_entry`` must carry its own ``doctype`` (v1.467.0).

Frappe's ``make_quick_entry(doctype, after_insert, init_callback, doc, ...)`` looks
like it takes the doctype once. It does not. ``check_quick_entry_doc`` builds a doc
via ``frappe.model.get_new_doc`` **only when the 4th argument is falsy**::

    check_quick_entry_doc() {
        if (!this.doc) {
            this.doc = frappe.model.get_new_doc(this.doctype, null, null, true);
        }
    }

Pass an object and it is used verbatim — no ``doctype``, no ``__islocal``, no name.
``QuickEntryForm.insert()`` then posts that object straight to ``frappe.client.save``,
which raises ``ValueError: "doctype" is a required key`` in ``get_doc_from_dict``,
before the target DocType's controller is ever consulted.

The Task form's "Create Child Task" button shipped that way and was broken outright:
every press 500'd and created nothing. **It is the failure mode that matters here,
not the count.** The dialog opens, renders the right fields, and pre-fills project
and parent — it looks completely normal right up to the moment Save returns an
error with no field to attach it to. Nothing distinguishes it from a validation
problem on the form, so the Error Log rows read as user error.

Source-only: no bench, no stub set, no import of the app. Per CLAUDE.md that means
its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_quick_entry_doctype_key
"""

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]

CALL = "frappe.ui.form.make_quick_entry("


def strip_js_comments(source: str) -> str:
	"""Drop ``//`` and ``/* */`` comments, leaving string literals intact.

	Needed because the comment explaining this very trap names ``doctype``, so a
	naive scan would find the word in prose and pass over a call that lacks it —
	the same vacuous-assertion shape CLAUDE.md warns about for absence tests.
	Here the risk runs the other way (a *presence* check satisfied by a comment),
	but the cure is the same and ``TestTheStripperWorks`` below holds it.
	"""
	out = []
	i, n = 0, len(source)
	quote = None
	while i < n:
		ch = source[i]
		if quote:
			out.append(ch)
			if ch == "\\" and i + 1 < n:
				out.append(source[i + 1])
				i += 2
				continue
			if ch == quote:
				quote = None
			i += 1
			continue
		if ch in "\"'`":
			quote = ch
			out.append(ch)
			i += 1
			continue
		if source.startswith("//", i):
			while i < n and source[i] != "\n":
				i += 1
			continue
		if source.startswith("/*", i):
			end = source.find("*/", i + 2)
			i = n if end == -1 else end + 2
			continue
		out.append(ch)
		i += 1
	return "".join(out)


def call_arguments(source: str, start: int) -> str:
	"""Return the balanced-paren argument text of the call opening at ``start``."""
	depth = 0
	for index in range(start, len(source)):
		if source[index] == "(":
			depth += 1
		elif source[index] == ")":
			depth -= 1
			if depth == 0:
				return source[start + 1 : index]
	raise AssertionError(f"unbalanced parentheses in a {CALL} call")


def quick_entry_calls():
	"""Yield ``(path, argument_text)`` for every make_quick_entry call in the app."""
	for path in sorted(APP.rglob("*.js")):
		if "node_modules" in path.parts:
			continue
		source = strip_js_comments(path.read_text(encoding="utf-8"))
		index = source.find(CALL)
		while index != -1:
			opener = index + len(CALL) - 1
			yield path, call_arguments(source, opener)
			index = source.find(CALL, index + 1)


class TestQuickEntryDocsCarryTheirDoctype(unittest.TestCase):
	def test_every_object_literal_names_its_doctype(self):
		"""A ``{...}`` passed as the doc must declare ``doctype``.

		Checked per call rather than per file: a second call added later to a file
		that already has a correct one must not inherit its pass.
		"""
		checked = 0
		for path, arguments in quick_entry_calls():
			if "{" not in arguments:
				# No doc object: frappe builds one itself, which is the safe shape.
				continue
			checked += 1
			literal = arguments[arguments.index("{") : arguments.rindex("}") + 1]
			self.assertRegex(
				literal,
				r"\bdoctype\s*:",
				f"{path.relative_to(APP)} passes a doc to make_quick_entry with no "
				"doctype key; frappe.client.save will reject it before the controller runs",
			)
		self.assertGreater(checked, 0, "no make_quick_entry doc literals found — has the call moved?")

	def test_the_task_button_is_the_known_case(self):
		"""Pin the call the production failure came from, so a delete is not a pass.

		``test_every_object_literal_names_its_doctype`` iterates whatever it finds;
		removing the file would leave it scanning nothing. This names the one call
		that is known to have 500'd in production.
		"""
		path = APP / "public/js/task_enhancements.js"
		source = strip_js_comments(path.read_text(encoding="utf-8"))
		self.assertIn(CALL, source, "the Create Child Task quick entry has moved or gone")
		arguments = call_arguments(source, source.index(CALL) + len(CALL) - 1)
		self.assertRegex(arguments, r"doctype\s*:\s*'Task'")
		self.assertRegex(arguments, r"parent_task\s*:")


class TestTheStripperWorks(unittest.TestCase):
	"""The assertions above are only worth as much as the comment stripper."""

	def test_it_drops_prose_that_names_the_key(self):
		source = "// doctype: 'Task'\n/* doctype: 'Task' */\nvar x = 1;"
		self.assertNotIn("doctype", strip_js_comments(source))

	def test_it_keeps_code_and_string_literals(self):
		source = "f({ doctype: 'Task', note: 'a // b', url: 'http://x/*y*/' });"
		stripped = strip_js_comments(source)
		self.assertIn("doctype: 'Task'", stripped)
		self.assertIn("a // b", stripped)
		self.assertIn("http://x/*y*/", stripped)

	def test_a_doc_literal_without_doctype_would_fail(self):
		"""Reintroduce the bug in miniature; the regex must not match it."""
		self.assertNotRegex("{ project: frm.doc.project }", r"\bdoctype\s*:")


if __name__ == "__main__":
	unittest.main()
