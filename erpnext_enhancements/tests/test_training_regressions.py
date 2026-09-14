"""Regressions for the four defects an adversarial review found in v1.207.0-1.209.0.

Every one of these shipped. None was caught by the original suites, and the
reason is worth recording: those suites stub ``frappe`` wholesale, so they never
exercised the framework's *ordering* — which is precisely where three of the four
bugs lived. These tests therefore assert the ordering contract explicitly,
against the real Frappe source where it is available.

The four:

1. **Naming ran before the version number existed.** ``Training Course Version``
   is named ``format:{course}-V{version_number}``, but the number was assigned in
   ``validate``. ``Document.insert`` calls ``set_new_name`` at line 729 and only
   reaches ``validate`` at line 734, so every version was named ``<course>-V``
   and the second one died on a duplicate key — making a second version of any
   course impossible, which is the entire premise of the doctype.

2. **Link validation ran before the Dynamic Link's options field was set.**
   ``applies_to_doctype`` was stamped in ``Course.validate``, but
   ``_validate_links`` runs at line 727 — before *everything*. Any assignment
   rule other than "All Employees" could not be saved at all.

3. **Role Profile rules matched only the legacy scalar.** Users here hold several
   profiles at once, and this module's own patch adds one to everybody, so
   matching ``User.role_profile_name`` silently assigned nobody for every
   secondary profile.

4. **The True-False normaliser guessed an answer key.** It looked for an option
   literally spelled "false" and defaulted to *True is correct* otherwise,
   silently rewriting the key for Yes/No or T/F phrasing.

Run: python -m unittest erpnext_enhancements.tests.test_training_regressions
"""

import ast
import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
TRAINING = APP / "training"

# The bench checkout, when this is run on a developer machine that has it. The
# ordering assertions that depend on it skip cleanly in CI, where it is absent —
# the source-independent assertions below still run everywhere.
FRAPPE_DOCUMENT = Path("C:/Users/nbbsh/Documents/GitHub/frappe/frappe/model/document.py")
FRAPPE_NAMING = Path("C:/Users/nbbsh/Documents/GitHub/frappe/frappe/model/naming.py")


def _source(path):
	return path.read_text(encoding="utf-8")


def _method_names(py_path, class_name):
	tree = ast.parse(_source(py_path))
	for node in ast.walk(tree):
		if isinstance(node, ast.ClassDef) and node.name == class_name:
			return {n.name for n in node.body if isinstance(n, ast.FunctionDef)}
	return set()


# --------------------------------------------------------------- 1. naming


class TestVersionNumberExistsBeforeNaming(unittest.TestCase):
	CONTROLLER = TRAINING / "doctype/training_course_version/training_course_version.py"
	SCHEMA = TRAINING / "doctype/training_course_version/training_course_version.json"

	def test_autoname_still_interpolates_the_version_number(self):
		"""Guards the premise of the other tests here — if the autoname ever stops
		using version_number, they stop meaning anything."""
		schema = json.loads(_source(self.SCHEMA))
		self.assertEqual(schema["autoname"], "format:{course}-V{version_number}")

	def test_version_number_has_no_schema_default(self):
		"""It is computed, so it cannot be defaulted — which is exactly why it has
		to be assigned before naming rather than relying on a fallback."""
		schema = json.loads(_source(self.SCHEMA))
		field = next(f for f in schema["fields"] if f["fieldname"] == "version_number")
		self.assertNotIn("default", field)

	def test_controller_assigns_it_in_before_naming(self):
		"""The fix. ``validate`` alone is too late; ``before_naming`` is the only
		hook that runs before the name is built."""
		self.assertIn("before_naming", _method_names(self.CONTROLLER, "TrainingCourseVersion"))

	def test_before_naming_actually_calls_the_assignment(self):
		"""A ``before_naming`` that does not assign the number is worse than none —
		it looks like the bug is fixed."""
		tree = ast.parse(_source(self.CONTROLLER))
		fn = next(
			n
			for n in ast.walk(tree)
			if isinstance(n, ast.FunctionDef) and n.name == "before_naming"
		)
		called = {
			node.func.attr
			for node in ast.walk(fn)
			if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
		}
		self.assertIn("_assign_version_number", called)

	@unittest.skipUnless(FRAPPE_DOCUMENT.exists(), "frappe source not available")
	def test_frappe_still_names_before_it_validates(self):
		"""The ordering this whole fix rests on. If a future Frappe reversed it,
		the fix would be unnecessary — and we should find out from a red test
		rather than from a duplicate-key error in production."""
		src = _source(FRAPPE_DOCUMENT)
		set_name = src.index("self.set_new_name(set_name=set_name")
		before_save = src.index("self.run_before_save_methods()", set_name)
		self.assertLess(set_name, before_save)

	@unittest.skipUnless(FRAPPE_NAMING.exists(), "frappe source not available")
	def test_before_naming_is_a_real_hook(self):
		self.assertIn('run_method("before_naming")', _source(FRAPPE_NAMING))


# ------------------------------------------------------- 2. dynamic link


class TestAssignmentRuleIsSaveable(unittest.TestCase):
	SCHEMA = TRAINING / "doctype/training_assignment_rule/training_assignment_rule.json"
	FORM_SCRIPT = APP / "public/js/training/training_course.js"

	def test_value_field_resolves_through_applies_to_doctype(self):
		schema = json.loads(_source(self.SCHEMA))
		field = next(f for f in schema["fields"] if f["fieldname"] == "applies_to_value")
		self.assertEqual(field["fieldtype"], "Dynamic Link")
		self.assertEqual(field["options"], "applies_to_doctype")

	def test_form_script_stamps_the_doctype_on_the_child_row(self):
		"""The only fix that can work. Frappe validates links before any server
		hook runs, so the field has to arrive already populated."""
		src = _source(self.FORM_SCRIPT)
		self.assertIn('frappe.ui.form.on("Training Assignment Rule"', src)
		self.assertIn("applies_to_doctype", src)

	def test_form_script_covers_both_change_and_add(self):
		"""A row added but never edited would otherwise ship with the field empty."""
		src = _source(self.FORM_SCRIPT)
		self.assertIn("applies_to(frm, cdt, cdn)", src)
		self.assertIn("assign_rules_add(frm, cdt, cdn)", src)

	def test_form_script_maps_every_option_the_schema_offers(self):
		"""A target the schema allows but the script does not map would reintroduce
		the bug for exactly that option, and nothing else would notice."""
		schema = json.loads(_source(self.SCHEMA))
		field = next(f for f in schema["fields"] if f["fieldname"] == "applies_to")
		options = [o for o in field["options"].split("\n") if o.strip()]
		src = _source(self.FORM_SCRIPT)
		mapping = src[src.index("const RULE_TARGET_DOCTYPES") : src.index("frappe.ui.form.on")]
		for option in options:
			bare = option.replace(" ", "")
			self.assertTrue(
				f'"{option}"' in mapping or f"{bare}:" in mapping or f'"{option}":' in mapping,
				f"assignment-rule target {option!r} is not mapped in the form script",
			)

	@unittest.skipUnless(FRAPPE_DOCUMENT.exists(), "frappe source not available")
	def test_frappe_validates_links_before_any_server_hook(self):
		"""Documents why the fix had to be client-side."""
		src = _source(FRAPPE_DOCUMENT)
		insert = src.index("def insert(")
		validate_links = src.index("self._validate_links()", insert)
		before_insert = src.index('self.run_method("before_insert")', insert)
		self.assertLess(validate_links, before_insert)


# ------------------------------------------------------- 3. role profiles


class TestRoleProfileMatching(unittest.TestCase):
	MODULE = TRAINING / "assignment.py"

	def test_matches_against_the_child_table(self):
		"""Users here hold several profiles at once, and this module's own patch
		adds one to everybody — so the scalar is the wrong thing to match."""
		src = _source(self.MODULE)
		self.assertIn("User Role Profile", src)

	def test_helper_exists_and_is_used(self):
		src = _source(self.MODULE)
		self.assertIn("def _has_role_profile(", src)
		self.assertIn("_has_role_profile(user, rule.applies_to_value)", src)

	def test_scalar_is_kept_only_as_a_fallback(self):
		"""Still consulted for a site that only ever set the scalar — but after the
		child table, never instead of it."""
		src = _source(self.MODULE)
		helper = src[src.index("def _has_role_profile(") :]
		helper = helper[: helper.index("\ndef ", 1)]
		self.assertLess(helper.index("User Role Profile"), helper.index("role_profile_name"))


# --------------------------------------------------- 4. true/false answers


class TestTrueFalseNeverGuesses(unittest.TestCase):
	MODULE = TRAINING / "doctype/training_question/training_question.py"

	def test_unrecognised_pair_is_rejected_not_rewritten(self):
		"""The old code defaulted to 'True is correct'. Silently rewriting an
		answer key is the one thing this module must never do."""
		src = _source(self.MODULE)
		fn_start = src.index("def _normalise_true_false(")
		fn = src[fn_start : src.index("\n\tdef ", fn_start + 1)]
		self.assertIn("frappe.throw", fn)

	def test_the_guessing_default_is_gone(self):
		src = _source(self.MODULE)
		self.assertNotIn("correct_is_true = True", src)

	def test_it_no_longer_rebuilds_the_options_table(self):
		"""Clearing self.options was how the rewrite happened; an empty question
		is still seeded, but an existing pair is never reconstructed."""
		src = _source(self.MODULE)
		fn_start = src.index("def _normalise_true_false(")
		fn = src[fn_start : src.index("\n\tdef ", fn_start + 1)]
		self.assertNotIn("self.options = []", fn)


# ------------------------------------------- N. every visible course lands somewhere


class TestTheCatalogueLosesNoVisibleCourse(unittest.TestCase):
	"""`get_learner_bootstrap` sorts every course this person may see into one of
	three lists. Twice now it has sorted one into NONE of them, and each time the
	course vanished from the catalogue for the only people entitled to open it.

	v1.406.0: a COMPLETED Required course matched neither `name in assignments`
	(Completed is not in OPEN_STATUSES) nor `weight == "Optional"`. An arm was added.

	And the arm added was the specific one in front of us, so the final branch stayed
	conditional and the next uncovered combination fell through the same hole: a
	Published, audience-matching, REQUIRED, unassigned, uncompleted course matched
	nothing either. Measured on production 2026-09-13, that was TRN-CRS-00005
	"Accounting in ERPNext" — visible to Internal Staff and invisible to all of them.

	The loop iterates `names`, which IS the set of courses this person may see, so
	the only correct final branch is an unconditional one. A member of that set with
	no list is not filtered out; it is lost.
	"""

	def bucketing(self):
		"""The bucketing loop, WITH COMMENTS STRIPPED.

		Stripped because the assertion below is about absence, and the comment that
		explains why the expression is gone necessarily contains the expression. This
		module's first run failed on exactly that: the test read its own explanation
		and reported the bug still present. It is at least the fifth instance of that
		shape in this repo, which is why it is worth a helper rather than a one-off
		regex at the call site.
		"""
		source = _source(APP / "api" / "training.py")
		start = source.index("    for name in names:")
		end = source.index("assigned_cards.sort(")
		block = source[start:end]
		keep = [line for line in block.splitlines() if not line.strip().startswith("#")]
		return chr(10).join(keep)

	def test_the_final_branch_is_unconditional(self):
		self.assertIn("        else:", self.bucketing())

	def test_no_arm_tests_the_weight(self):
		"""The exact expression that lost the course. `weight` still reaches the
		client on the card — what must not come back is the SERVER deciding
		  visibility from it."""
		self.assertNotIn('course.weight == "Optional"', self.bucketing())

	def test_all_three_lists_are_still_filled(self):
		"""An `else` that swallowed the other arms would also pass the two above."""
		block = self.bucketing()
		for name in ("assigned_cards.append(card)", "finished.append(card)", "library.append(card)"):
			with self.subTest(name):
				self.assertIn(name, block)

	def test_the_player_titles_that_section_neutrally(self):
		"""`library` is the right home for a Required-but-unassigned course only
		because the catalogue calls it "Available to you" rather than "Optional".
		If that heading ever becomes weight-specific, this bucketing lies."""
		player = _source(APP / "public" / "js" / "training" / "player.js")
		self.assertIn('t("Available to you")', player)


if __name__ == "__main__":
	unittest.main()
