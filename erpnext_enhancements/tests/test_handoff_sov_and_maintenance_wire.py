"""Static wire check for the two 2026-11-04 hand-off steps (v1.795.0).

Bench-free by construction, like ``test_payment_followup_wire``: parses the
seed patch and ``process_steps.py`` with AST and ``process_steps.js`` with regex,
and imports none of them. The engine's behaviour is covered by
``test_process_steps`` on a bench; what is pinned here is the seam that raises
nowhere when it breaks — a patch that is not registered never runs, a parallel
set that the JS no longer mirrors highlights the wrong step on the form, and a
closeout dialog that stops passing ``notes`` quietly throws the answer away.
"""
import ast
import pathlib
import re
import unittest

APP = pathlib.Path(__file__).resolve().parents[1]
STEPS_PY = APP / "process_steps.py"
STEPS_JS = APP / "public" / "js" / "project_enhancements" / "process_steps.js"
SEED_PATCH = APP / "patches" / "seed_handoff_sov_and_maintenance_steps.py"
ORIGINAL_SEED = APP / "patches" / "seed_process_step_templates.py"
PATCHES_TXT = APP / "patches.txt"
README = APP / "project_enhancements" / "README.md"

SOV_TITLE = "Create the Schedule of Values"
MAINTENANCE_TITLE = "Offer a Maintenance Contract"


def _module_constant(path, name):
	"""A module-level literal assignment, read without importing."""
	tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
	for node in tree.body:
		if isinstance(node, ast.Assign):
			for target in node.targets:
				if isinstance(target, ast.Name) and target.id == name:
					return ast.literal_eval(node.value)
	raise AssertionError(f"{name} not found in {path.name}")


def _frozenset_constant(path, name):
	"""``NAME = frozenset({...})`` where the members are names or ints, resolved."""
	tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
	ints = {}
	for node in tree.body:
		if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
			for target in node.targets:
				if isinstance(target, ast.Name):
					ints[target.id] = node.value.value
	for node in tree.body:
		if not isinstance(node, ast.Assign):
			continue
		if not any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
			continue
		call = node.value
		assert isinstance(call, ast.Call) and getattr(call.func, "id", None) == "frozenset", name
		members = call.args[0].elts
		out = set()
		for member in members:
			if isinstance(member, ast.Name):
				out.add(ints[member.id])
			else:
				out.add(ast.literal_eval(member))
		return out
	raise AssertionError(f"{name} not found in {path.name}")


def _js_const(js, name):
	match = re.search(rf"const {name} = (\d+);", js)
	assert match, f"{name} not found in process_steps.js"
	return int(match.group(1))


class SeedPatchTest(unittest.TestCase):
	def test_patch_is_registered(self):
		"""An unregistered patch never runs, and no site ever gets the steps."""
		self.assertIn(
			"erpnext_enhancements.patches.seed_handoff_sov_and_maintenance_steps",
			PATCHES_TXT.read_text(encoding="utf-8"),
		)

	def test_steps_are_eight_and_nine(self):
		steps = _module_constant(SEED_PATCH, "STEPS")
		by_no = {s["step_number"]: s for s in steps}
		self.assertEqual(set(by_no), {8, 9})
		self.assertEqual(by_no[8]["step_title"], SOV_TITLE)
		self.assertEqual(by_no[9]["step_title"], MAINTENANCE_TITLE)
		self.assertEqual(by_no[8]["responsible_role"], "Project Manager")
		self.assertEqual(by_no[9]["responsible_role"], "Project Manager")

	def test_numbers_do_not_collide_with_the_original_seven(self):
		"""Insert-only by step_number: a collision would silently skip the new step."""
		original = {s["step_number"] for s in _module_constant(ORIGINAL_SEED, "STEPS")}
		new = {s["step_number"] for s in _module_constant(SEED_PATCH, "STEPS")}
		self.assertEqual(original, set(range(1, 8)))
		self.assertFalse(original & new)

	def test_closeout_has_no_clock_and_sov_has_one(self):
		by_no = {s["step_number"]: s for s in _module_constant(SEED_PATCH, "STEPS")}
		self.assertEqual(by_no[9]["sla_business_days"], 0)
		self.assertEqual(by_no[9]["auto_anchor"], "")
		self.assertGreater(by_no[8]["sla_business_days"], 0)

	def test_sov_description_says_what_it_is_and_where_it_lives(self):
		by_no = {s["step_number"]: s for s in _module_constant(SEED_PATCH, "STEPS")}
		text = by_no[8]["description"]
		self.assertIn("100-700", text)
		self.assertIn("G702/G703", text)

	def test_patch_is_insert_only_by_step_number(self):
		src = SEED_PATCH.read_text(encoding="utf-8")
		self.assertIn('pluck="step_number"', src)
		self.assertIn('if step["step_number"] in existing', src)


class EngineAgreementTest(unittest.TestCase):
	def test_constants_name_the_seeded_steps(self):
		self.assertEqual(_module_constant(STEPS_PY, "SOV_STEP_NUMBER"), 8)
		self.assertEqual(_module_constant(STEPS_PY, "MAINTENANCE_OFFER_STEP_NUMBER"), 9)

	def test_sov_is_parallel_and_maintenance_is_closeout(self):
		self.assertEqual(_frozenset_constant(STEPS_PY, "PARALLEL_STEP_NUMBERS"), {8})
		self.assertEqual(_frozenset_constant(STEPS_PY, "CLOSEOUT_STEP_NUMBERS"), {9})

	def test_js_mirrors_the_python_constants(self):
		"""The form highlights what the JS computes; the server acts on what Python
		computes. A step actionable on one side and not the other shows a button
		that fails, or hides a button for a step the escalation is nagging about."""
		js = STEPS_JS.read_text(encoding="utf-8")
		self.assertEqual(_js_const(js, "STEP_SOV"), _module_constant(STEPS_PY, "SOV_STEP_NUMBER"))
		self.assertEqual(
			_js_const(js, "STEP_MAINTENANCE_OFFER"),
			_module_constant(STEPS_PY, "MAINTENANCE_OFFER_STEP_NUMBER"),
		)
		match = re.search(r"const PARALLEL_STEPS = new Set\(\[(.*?)\]\);", js)
		self.assertIsNotNone(match, "PARALLEL_STEPS not found in process_steps.js")
		names = [n.strip() for n in match.group(1).split(",") if n.strip()]
		js_parallel = {_js_const(js, n) for n in names}
		self.assertEqual(js_parallel, _frozenset_constant(STEPS_PY, "PARALLEL_STEP_NUMBERS"))

	def test_js_actionability_honours_the_parallel_set(self):
		js = STEPS_JS.read_text(encoding="utf-8")
		block = re.search(r"function actionable_steps\(steps\) \{(.*?)\n\t\}", js, re.S)
		self.assertIsNotNone(block)
		self.assertIn("PARALLEL_STEPS.has(Number(s.step_number))", block.group(1))


class CloseoutDialogTest(unittest.TestCase):
	def test_maintenance_step_completes_through_a_dialog_with_notes(self):
		"""The meeting wanted the question asked. The dialog must reach
		complete_step with ``notes``, and the bare Mark Complete must defer to it
		for step 9."""
		js = STEPS_JS.read_text(encoding="utf-8")
		block = re.search(r"function complete_maintenance_offer\(frm, step_name\) \{(.*?)\n\t\}", js, re.S)
		self.assertIsNotNone(block, "complete_maintenance_offer not found")
		body = block.group(1)
		self.assertIn("erpnext_enhancements.process_steps.complete_step", body)
		self.assertIn("notes: notes", body)
		self.assertIn("Maintenance agreement offered:", body)
		self.assertRegex(
			js,
			r"STEP_MAINTENANCE_OFFER\) \{\s*complete_maintenance_offer\(frm, step_name\);",
		)

	def test_complete_step_accepts_notes(self):
		"""The server half of the same seam, read from the signature."""
		tree = ast.parse(STEPS_PY.read_text(encoding="utf-8"), filename=str(STEPS_PY))
		for node in tree.body:
			if isinstance(node, ast.FunctionDef) and node.name == "complete_step":
				self.assertIn("notes", [a.arg for a in node.args.args])
				return
		self.fail("complete_step not found")


class ReadmeTest(unittest.TestCase):
	def test_readme_lists_both_steps(self):
		text = README.read_text(encoding="utf-8")
		self.assertIn(SOV_TITLE, text)
		self.assertIn(MAINTENANCE_TITLE, text)
		self.assertIn("seed_handoff_sov_and_maintenance_steps", text)


if __name__ == "__main__":
	unittest.main()
