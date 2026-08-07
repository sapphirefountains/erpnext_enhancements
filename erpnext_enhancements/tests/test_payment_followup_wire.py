"""Static wire check for the initial-payment follow-up, and its rename.

Bench-free by construction: parses `process_steps.py` with AST and
`process_steps.js` with regex, and imports neither. The suites that exercise the
real behaviour need a bench and so never run in CI — which is exactly why the
seam between the button and the endpoint is worth pinning here. A renamed
whitelisted method or a dropped `STEP_ACTIONS` entry does not raise anywhere; the
button simply stops working, silently, in production.

The rename checks exist for a related reason. `step_title` is copied onto every
project's child rows at seed time, so the wording lives in three places at once —
the seed patch (new sites), the migration (existing rows), and the prose that
refers to the step. Renaming one and forgetting another leaves the site showing
two names for the same step, which is precisely the failure this suite fails on.
"""
import ast
import pathlib
import re
import unittest

APP = pathlib.Path(__file__).resolve().parents[1]
STEPS_PY = APP / "process_steps.py"
STEPS_JS = APP / "public" / "js" / "project_enhancements" / "process_steps.js"
SEED_PATCH = APP / "patches" / "seed_process_step_templates.py"
RENAME_PATCH = APP / "patches" / "rename_payment_step_title.py"
PATCHES_TXT = APP / "patches.txt"

OLD_TITLE = "Receive Customer Payment"
NEW_TITLE = "Initial Payment From Customer"

#: This file necessarily contains the old string, and the migration must keep it
#: to have something to match on. Everything else is fair game.
RENAME_SCAN_EXEMPT = {"test_payment_followup_wire.py", "rename_payment_step_title.py"}


def _whitelisted_functions(path):
	"""Names of module-level functions carrying an @frappe.whitelist() decorator."""
	tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
	found = set()
	for node in tree.body:
		if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
			continue
		for dec in node.decorator_list:
			call = dec.func if isinstance(dec, ast.Call) else dec
			if isinstance(call, ast.Attribute) and call.attr == "whitelist":
				found.add(node.name)
	return found


def _module_constant(path, name):
	"""A module-level literal assignment, read without importing."""
	tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
	for node in tree.body:
		if isinstance(node, ast.Assign):
			for target in node.targets:
				if isinstance(target, ast.Name) and target.id == name:
					return ast.literal_eval(node.value)
	raise AssertionError(f"{name} not found in {path.name}")


class PaymentFollowUpWireTest(unittest.TestCase):
	def test_endpoints_the_button_calls_are_whitelisted(self):
		"""Every process_steps method the JS xcalls must exist and be whitelisted.

		An xcall to a missing or unwhitelisted method fails at click time with a
		PermissionError the user reads as "the button is broken".
		"""
		js = STEPS_JS.read_text(encoding="utf-8")
		called = set(re.findall(r"erpnext_enhancements\.process_steps\.(\w+)", js))
		self.assertTrue(called, "no process_steps xcalls found in the JS — did the path change?")

		whitelisted = _whitelisted_functions(STEPS_PY)
		missing = sorted(called - whitelisted)
		self.assertFalse(
			missing,
			f"JS calls process_steps method(s) that are not @frappe.whitelist(): {missing}",
		)

	def test_both_follow_up_endpoints_exist(self):
		"""Named explicitly, so a regression says which half broke."""
		whitelisted = _whitelisted_functions(STEPS_PY)
		self.assertIn("get_payment_followup", whitelisted)
		self.assertIn("schedule_payment_followup", whitelisted)

	def test_payment_step_has_an_action_button(self):
		"""STEP_ACTIONS must carry an entry for the payment step.

		Without it the step renders with its label and no button — the exact
		state this feature was built to fix, and one that looks intentional.
		"""
		js = STEPS_JS.read_text(encoding="utf-8")
		block = re.search(r"const STEP_ACTIONS = \{(.*?)\n\t\};", js, re.S)
		self.assertIsNotNone(block, "STEP_ACTIONS block not found")
		self.assertIn("[STEP_PAYMENT]", block.group(1))

	def test_follow_up_is_two_business_days(self):
		"""The interval the request specified. Parsed, not imported."""
		self.assertEqual(_module_constant(STEPS_PY, "PAYMENT_FOLLOWUP_BUSINESS_DAYS"), 2)

	def test_seed_patch_uses_the_new_title(self):
		"""New sites must be seeded with the new wording."""
		self.assertIn(NEW_TITLE, SEED_PATCH.read_text(encoding="utf-8"))

	def test_rename_patch_is_registered(self):
		"""An unregistered patch never runs, and existing rows keep the old name."""
		self.assertIn(
			"erpnext_enhancements.patches.rename_payment_step_title",
			PATCHES_TXT.read_text(encoding="utf-8"),
		)

	def test_rename_patch_maps_old_to_new(self):
		"""The migration's endpoints must match what the seed now writes."""
		self.assertEqual(_module_constant(RENAME_PATCH, "OLD_TITLE"), OLD_TITLE)
		self.assertEqual(_module_constant(RENAME_PATCH, "NEW_TITLE"), NEW_TITLE)

	def test_old_title_is_gone_from_shipped_code(self):
		"""No stragglers — two names for one step is the failure being prevented."""
		stragglers = []
		for path in list(APP.rglob("*.py")) + list(APP.rglob("*.js")):
			if path.name in RENAME_SCAN_EXEMPT or "node_modules" in path.parts:
				continue
			if OLD_TITLE in path.read_text(encoding="utf-8", errors="replace"):
				stragglers.append(str(path.relative_to(APP)))
		self.assertFalse(stragglers, f"still referring to {OLD_TITLE!r}: {sorted(stragglers)}")

	def test_scan_actually_reads_files(self):
		"""A guard whose file list silently emptied would pass forever."""
		files = [p for p in APP.rglob("*.py") if "node_modules" not in p.parts]
		self.assertGreater(len(files), 50, f"expected to scan the app, found {len(files)} files")


if __name__ == "__main__":
	unittest.main()
