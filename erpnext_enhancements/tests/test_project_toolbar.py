"""Bench-free tests for the Project form's toolbar grouping.

The Project toolbar is assembled by five independent form scripts, none of which
can see the others. Left ungrouped they each rendered a top-level button, so the
toolbar grew a button per feature until it ran to seven. Each script now passes a
group name to ``add_custom_button`` so the buttons fold into the dropdowns ERPNext
already provides on Project ("Actions", "View", "Create").

Nothing here executes JavaScript -- there is no JS test runner in this app. These
are source-level assertions, in the same spirit as ``test_custom_html_blocks``:
they pin the one detail that is invisible until someone opens the form and easy to
drop in a later edit (the third argument), and they pin the blast radius of the two
scripts that are shared with other doctypes.
"""

import re
import unittest
from pathlib import Path

JS_ROOT = Path(__file__).resolve().parent.parent / "public" / "js"

# (file, button label, expected group) -- the group ERPNext itself uses, so the
# button lands in the existing dropdown rather than minting a near-duplicate one.
GROUPED_BUTTONS = [
	("project_merge.js", "Merge Project", "Actions"),
	("project_enhancements/project_brief.js", "Project Brief", "View"),
]


def _read(relative_path):
	return (JS_ROOT / relative_path).read_text(encoding="utf-8")


def _without_comments(source):
	"""Drop block comments and whole-line ``//`` comments.

	So a rule about what the code may do is not tripped by prose explaining it --
	the comment above the fix names the selector it replaced. Whole-line only:
	stripping from a mid-line ``//`` would eat the rest of any line holding a URL.
	"""
	source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
	return "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("//"))


def _button_calls(source, label):
	"""Return each ``add_custom_button`` call for ``label``, argument list included.

	Walks parenthesis depth from the call to its close so an assertion reads that
	call's own arguments, rather than grepping the file and matching a group that
	belongs to some other button.
	"""
	calls = []
	pattern = r"add_custom_button\(\s*__\(\"" + re.escape(label) + r"\"\)"
	for match in re.finditer(pattern, source):
		depth = 0
		for index in range(match.start(), len(source)):
			char = source[index]
			if char == "(":
				depth += 1
			elif char == ")":
				depth -= 1
				if depth == 0:
					calls.append(source[match.start() : index + 1])
					break
		else:
			raise AssertionError(f"unbalanced add_custom_button call for {label}")
	return calls


class TestProjectToolbarGrouping(unittest.TestCase):
	def test_project_buttons_declare_their_dropdown(self):
		"""Every Project button passes a group, so none renders top-level."""
		for filename, label, group in GROUPED_BUTTONS:
			with self.subTest(button=label):
				calls = _button_calls(_read(filename), label)
				self.assertEqual(len(calls), 1, f"{filename}: expected one {label} button")
				self.assertIn(
					f'__("{group}")',
					calls[0],
					f'{filename}: "{label}" must be grouped under {group}',
				)

	def test_both_maintenance_contract_branches_are_grouped(self):
		"""View the existing contract under View; map a new one under Create.

		Two calls on mutually exclusive branches of the same `if`, so only one ever
		renders -- but neither may be top-level.
		"""
		calls = _button_calls(_read("project.js"), "Maintenance Contract")
		self.assertEqual(len(calls), 2, "expected the view and create branches")
		view_call, create_call = calls
		self.assertIn("frappe.set_route", view_call)
		self.assertIn('__("View")', view_call)
		self.assertIn("open_mapped_doc", create_call)
		self.assertIn('__("Create")', create_call)

	def test_drive_folder_button_groups_only_on_project(self):
		"""Customer and Opportunity keep the plain button -- no one-item dropdown."""
		source = _read("global_enhancements/drive_folder_button.js")
		groups = re.search(r"const DOCTYPE_GROUPS = \{(.*?)\};", source, re.S)
		self.assertIsNotNone(groups, "DOCTYPE_GROUPS mapping not found")
		body = groups.group(1)
		self.assertRegex(body, r"Project:\s*\"View\"")
		self.assertRegex(body, r"Customer:\s*null")
		self.assertRegex(body, r"Opportunity:\s*null")

	def test_no_script_hides_a_toolbar_group_wholesale(self):
		"""Unwanted buttons come off one label at a time, never by hiding the group.

		``project_form_script.js`` used to inject ``display: none`` on
		``.inner-group-button[data-label="View"]`` to drop ERPNext's Gantt Chart and
		Kanban Board. When the buttons above were folded into that same native group
		they went invisible with it -- present in the DOM, wired up, and unreachable.
		``frm.remove_custom_button(label, group)`` is the tool: Frappe drops the group
		itself once its last item goes, so nothing is left hanging empty either.

		The style element also went into ``document.head`` and stayed, so one visit to
		a Project hid that group on every other doctype for the rest of the session --
		which is why this scans all of ``public/js`` rather than the Project scripts.
		"""
		for path in sorted(JS_ROOT.rglob("*.js")):
			with self.subTest(script=str(path.relative_to(JS_ROOT))):
				self.assertNotRegex(
					_without_comments(path.read_text(encoding="utf-8")),
					r"(?:inner-group-button|custom-btn-group)\[data-label=",
					"hide the button (frm.remove_custom_button), not the whole group",
				)

	def test_merge_tool_groups_only_on_project(self):
		"""The global merge button stays top-level on every other doctype.

		It is registered from a `form-refresh` handler that fires on ALL forms, so an
		unconditional group would put a single-item "Actions" dropdown on every
		non-submittable form in the desk.
		"""
		source = _read("merge_tool/merge_tool.js")
		groups = re.search(r"const TOOLBAR_GROUPS = \{(.*?)\};", source, re.S)
		self.assertIsNotNone(groups, "TOOLBAR_GROUPS mapping not found")
		self.assertRegex(groups.group(1), r"Project:\s*\"Actions\"")
		self.assertEqual(groups.group(1).count(":"), 1, "only Project should be grouped")
		# The call must consult the map rather than hardcoding a group.
		self.assertIn("TOOLBAR_GROUPS[frm.doctype]", source)


if __name__ == "__main__":
	unittest.main()
