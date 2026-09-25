# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Getting to the canvas, and getting a whole lesson out of it. Bench-free.

Two defects shipped together in v1.489.0 and they are the same defect at two
scales — *an empty page is where authoring stops* — so they are guarded together.

**Getting there.** `training-canvas` opened with no `course` rendered a heading
and one sentence: "Open a Training Course and choose Edit on the canvas, or add
?course=… to the URL". Two doors led into exactly that — the desk rail's "Course
canvas" link, which `desk_nav.js` has always routed with no arguments, and any
bookmark of the bare page — so the one sidebar link named after the authoring
surface opened instructions for editing a URL by hand. The Training workspace had
shortcuts to the learner portal, Insights, sessions and three course lists, and
none to the editor.

**Getting a lesson.** `+ Lesson` minted `{lesson_title: "New lesson", blocks: []}`.
The course starters exist because inventing the content and the shape at once is
what stops people, and they answered it once per course; every lesson after a
starter's own was blank again. This is the button pressed twenty times to the
starters' one.

What this file pins, in the order the traps bite:

* **No lesson shape may set `has_quiz`.** `TrainingLesson._validate_quiz` throws on
  `has_quiz` with an empty pool, and the canvas cannot fill a pool — a quiz row
  allowlists `{question, points, is_required}` and the question body is refused by
  design. A shape that ticked the box would make its own lesson fail on the first
  autosave, four seconds after the author chose it, with a red dialog and no way
  forward. The flag is advice (`suggests_quiz`) and the canvas says it out loud.
* **Both Selects, exactly.** `block_type` and `callout_tone` are Selects;
  `_validate_selects` runs on child rows and `save_draft_version` does a full
  `lesson.save()`, so a value the doctype does not declare does not render oddly,
  it takes the autosave down. Checked against the DocType JSON, not restated.
* **Nothing a shape emits falls outside `BLOCK_ALLOWED_FIELDS`.** A field outside
  it comes back in `rejected`, which the canvas surfaces — so the author would be
  told the editor refused something they never wrote.
* **`data` is a JSON string.** That is what the allowlist carries and what
  `_parse_block_data` reads at publish; a dict would be stringified as a Python
  repr somewhere along the way and come back unparseable.
* **No shape carries a `block_key`.** Keys are minted client-side, once. A shape
  fetched once and used twice must not produce two blocks sharing one — the single
  case `_apply_blocks` rewrites without telling anybody.
* **Two doors, one scaffolder.** The canvas home and the Training Course list view
  both create a course through `create_from_starter`. Neither builds one itself.

Every absence assertion here strips JS comments first. The comment explaining why
the dead end is gone *quotes the dead end*, which is the eleventh time this
project has been caught by an absence test satisfied by the prose describing the
absence.

Run: python -m unittest erpnext_enhancements.tests.test_training_lesson_starters
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

APP = Path(__file__).resolve().parents[1]
STARTERS = APP / "training/lesson_starters.py"
AUTHOR_API = APP / "api/training_author.py"
AI_API = APP / "api/training_ai.py"
CANVAS_JS = APP / "training/page/training_canvas/training_canvas.js"
CANVAS_CSS = APP / "training/page/training_canvas/training_canvas.css"
LIST_JS = APP / "public/js/training/training_course_list.js"
CONTENT_BLOCK = APP / "training/doctype/training_content_block/training_content_block.json"
WORKSPACE = APP / "training/workspace/training/training.json"
PATCH = APP / "patches/reload_training_workspace_for_canvas.py"
PATCHES_TXT = APP / "patches.txt"

lesson_starters = None


def setUpModule():
	"""Stub ``frappe`` at execution time, then import the module under test.

	At execution time rather than import time, matching every other bench-free
	suite here: a stub installed at import would satisfy the bench-only suites'
	``import frappe`` skip-guards and make them run against a fake.
	"""
	global lesson_starters
	frappe = types.ModuleType("frappe")

	def _throw(msg, exc=None):
		raise (exc or Exception)(msg)

	frappe.throw = _throw
	frappe.__dict__["_"] = lambda s: s
	sys.modules.setdefault("frappe", frappe)

	from erpnext_enhancements.training import lesson_starters as module

	lesson_starters = module


def _text(path):
	return path.read_text(encoding="utf-8")


def _strip_js_comments(src):
	"""``src`` with whole-line JS comments removed.

	Defined here rather than imported from a sibling suite, for the reason
	``test_training_authoring_entry`` gives for its own copy: this file has a
	``__main__`` block, and a cross-module import breaks the direct run with
	ModuleNotFoundError while leaving CI green.
	"""
	out, in_block = [], False
	for line in src.splitlines():
		stripped = line.strip()
		if in_block:
			if "*/" in stripped:
				in_block = False
			continue
		if stripped.startswith("/*"):
			in_block = "*/" not in stripped
			continue
		if stripped.startswith("//"):
			continue
		out.append(line)
	return "\n".join(out)


def _canvas_code():
	return _strip_js_comments(_text(CANVAS_JS))


def _py_code(path):
	"""Python source with comments and docstrings stripped.

	Needed for the same reason ``_strip_js_comments`` is, and this file was caught
	by it while being written: ``lesson_starters``'s own docstring explains at
	length why a shape never mints a ``block_key``, so a plain substring scan for
	the absence of ``block_key`` is satisfied by the paragraph saying it is absent.
	"""
	import io
	import tokenize

	kept = [
		token
		for token in tokenize.generate_tokens(io.StringIO(_text(path)).readline)
		if token.type != tokenize.COMMENT
	]
	tree = ast.parse(tokenize.untokenize(kept))
	for node in ast.walk(tree):
		if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
			continue
		body = node.body
		if (
			body
			and isinstance(body[0], ast.Expr)
			and isinstance(body[0].value, ast.Constant)
			and isinstance(body[0].value.value, str)
		):
			body[0].value.value = ""
	return ast.unparse(tree)


def _py_const(path, name):
	"""A module-level constant read out of the source, ``frozenset(...)`` included."""
	for node in ast.parse(_text(path)).body:
		if isinstance(node, ast.Assign) and any(
			isinstance(t, ast.Name) and t.id == name for t in node.targets
		):
			value = node.value
			if isinstance(value, ast.Call) and getattr(value.func, "id", "") == "frozenset":
				return set(ast.literal_eval(value.args[0]))
			return ast.literal_eval(value)
	raise AssertionError(f"{name} not found in {path.name}")


def _select_options(fieldname):
	"""The DocType's own Select vocabulary for one field, verbatim."""
	doctype = json.loads(_text(CONTENT_BLOCK))
	for field in doctype["fields"]:
		if field["fieldname"] == fieldname:
			return {opt for opt in (field.get("options") or "").split("\n") if opt}
	raise AssertionError(f"{fieldname} is not a field on Training Content Block")


def _shape_blocks():
	"""``(shape_key, block_dict)`` for every block in every shape."""
	for key, shape in lesson_starters.SHAPES.items():
		for block in shape.get("blocks") or []:
			yield key, block


# =========================================================== the shapes


class TestNoShapeCanMakeItsOwnLessonUnsaveable(unittest.TestCase):
	"""The three ways a starting value can throw on the author's first autosave."""

	def test_no_shape_sets_has_quiz(self):
		"""`_validate_quiz` throws on `has_quiz` with an empty pool, and the canvas
		cannot put a question in a pool — the body is refused by design and there is
		no Training Question to point at yet. Ticking it for the author would make
		the lesson fail four seconds after they chose the shape."""
		for key, shape in lesson_starters.SHAPES.items():
			with self.subTest(shape=key):
				self.assertNotIn("has_quiz", shape)
				self.assertNotIn("quiz_questions_to_ask", shape)

	def test_the_source_never_mentions_the_field_as_a_value(self):
		"""Belt to the braces above: a shape could grow one in a nested dict the
		data test above walks past. Docstrings and comments explain at length why
		the field is absent, so they are stripped before looking."""
		# Dict KEYS specifically, not any occurrence of the string: the module
		# docstring names the field four times explaining why it is never set, and
		# a scan for the bare token is satisfied by the paragraph saying so.
		tree = ast.parse(_text(STARTERS))
		keys = set()
		for node in ast.walk(tree):
			if isinstance(node, ast.Dict):
				keys |= {
					k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)
				}
		self.assertNotIn("has_quiz", keys)

	def test_every_block_type_is_one_the_doctype_declares(self):
		"""A Select value the DocType does not declare does not degrade — it throws
		and takes the whole lesson autosave with it."""
		declared = _select_options("block_type")
		for key, block in _shape_blocks():
			with self.subTest(shape=key, block_type=block.get("block_type")):
				self.assertIn(block.get("block_type"), declared)

	def test_every_block_type_is_also_in_the_module_allowlist(self):
		"""`SHAPE_BLOCK_TYPES` documents the subset a shape may use. A shape drifting
		outside it still saves, so only this notices."""
		for key, block in _shape_blocks():
			with self.subTest(shape=key):
				self.assertIn(block.get("block_type"), lesson_starters.SHAPE_BLOCK_TYPES)

	def test_the_module_allowlist_is_a_subset_of_the_doctype(self):
		self.assertTrue(lesson_starters.SHAPE_BLOCK_TYPES <= _select_options("block_type"))

	def test_every_tone_is_one_the_doctype_declares(self):
		"""The canvas's first Callout was unsaveable until v1.386.0 because a
		lowercase tone with a phantom "info" was stamped on every new one."""
		declared = _select_options("callout_tone")
		for key, block in _shape_blocks():
			if "callout_tone" not in block:
				continue
			with self.subTest(shape=key, tone=block["callout_tone"]):
				self.assertIn(block["callout_tone"], declared)
				self.assertIn(block["callout_tone"], lesson_starters.SHAPE_CALLOUT_TONES)

	def test_a_callout_always_carries_a_tone(self):
		for key, block in _shape_blocks():
			if block.get("block_type") != "Callout":
				continue
			with self.subTest(shape=key):
				self.assertTrue(block.get("callout_tone"))

	def test_the_module_tones_match_the_doctype_exactly(self):
		declared = {opt for opt in _select_options("callout_tone")}
		self.assertEqual(lesson_starters.SHAPE_CALLOUT_TONES, declared)


class TestShapesFitTheSaveContract(unittest.TestCase):
	def test_no_emitted_block_field_is_outside_the_allowlist(self):
		"""A field outside `BLOCK_ALLOWED_FIELDS` comes back in `rejected`, which the
		canvas surfaces — so the author would be told the editor refused something
		they never wrote."""
		allowed = _py_const(AUTHOR_API, "BLOCK_ALLOWED_FIELDS")
		for shape in lesson_starters.list_shapes():
			for block in shape["blocks"]:
				for field in block:
					with self.subTest(shape=shape["key"], field=field):
						self.assertIn(field, allowed)

	def test_the_lesson_fields_a_shape_sets_are_writable(self):
		"""`summary` and `estimated_minutes` ride the same allowlist."""
		allowed = _py_const(AUTHOR_API, "LESSON_ALLOWED_FIELDS")
		for field in ("summary", "estimated_minutes"):
			self.assertIn(field, allowed)

	def test_no_shape_mints_a_block_key(self):
		"""`block_key` is where learner watch progress and in-video checkpoints are
		filed. A shape used twice must not produce two blocks sharing one — the
		single case `_apply_blocks` rewrites silently."""
		for shape in lesson_starters.list_shapes():
			for block in shape["blocks"]:
				with self.subTest(shape=shape["key"]):
					self.assertNotIn("block_key", block)
		self.assertNotIn("block_key", _py_code(STARTERS))

	def test_interactive_payloads_are_json_strings(self):
		"""`data` is a JSON string on the wire. A dict would reach `_parse_block_data`
		as a Python repr and come back unparseable."""
		expect = {"Checklist": "items", "Flashcards": "cards", "Accordion": "panels"}
		seen = set()
		for shape in lesson_starters.list_shapes():
			for block in shape["blocks"]:
				key = expect.get(block["block_type"])
				if not key:
					continue
				seen.add(block["block_type"])
				with self.subTest(shape=shape["key"], block_type=block["block_type"]):
					self.assertIsInstance(block["data"], str)
					payload = json.loads(block["data"])
					self.assertIn(key, payload)
					self.assertTrue(payload[key])
		self.assertEqual(seen, set(expect), "a payload shape is not exercised by any starter")

	def test_the_raw_list_keys_do_not_leak_into_the_emitted_block(self):
		"""`items`/`cards`/`panels` are how a shape is *written*; `data` is how it is
		*sent*. Emitting both would put an unallowlisted field on the wire."""
		for shape in lesson_starters.list_shapes():
			for block in shape["blocks"]:
				for raw in ("items", "cards", "panels"):
					with self.subTest(shape=shape["key"], key=raw):
						self.assertNotIn(raw, block)


class TestTheGalleryItself(unittest.TestCase):
	def test_the_order_covers_every_shape_exactly_once(self):
		self.assertEqual(sorted(lesson_starters.SHAPE_ORDER), sorted(lesson_starters.SHAPES))
		self.assertEqual(len(set(lesson_starters.SHAPE_ORDER)), len(lesson_starters.SHAPE_ORDER))

	def test_blank_is_offered_last(self):
		"""On a chooser whose whole purpose is to talk you out of a blank page, the
		blank option does not go first."""
		self.assertEqual(lesson_starters.SHAPE_ORDER[-1], "blank")

	def test_the_gallery_is_drawn_in_that_order(self):
		self.assertEqual(
			[shape["key"] for shape in lesson_starters.list_shapes()],
			list(lesson_starters.SHAPE_ORDER),
		)

	def test_every_shape_but_blank_lands_real_blocks(self):
		for shape in lesson_starters.list_shapes():
			if shape["key"] == "blank":
				self.assertEqual(shape["blocks"], [])
				continue
			with self.subTest(shape=shape["key"]):
				self.assertGreaterEqual(len(shape["blocks"]), 3)
				self.assertEqual(shape["block_count"], len(shape["blocks"]))

	def test_every_shape_describes_itself(self):
		for shape in lesson_starters.list_shapes():
			with self.subTest(shape=shape["key"]):
				self.assertTrue(shape["label"].strip())
				self.assertTrue(shape["blurb"].strip())

	def test_the_gallery_hands_out_a_copy(self):
		"""`SHAPES` is module-level state shared by every request in the worker.
		Handing out the live dict would let one author's edit leak into the next
		author's lesson — cheap to do, very confusing to find."""
		first = lesson_starters.shape_for("briefing")
		first["blocks"][0]["heading"] = "MUTATED"
		second = lesson_starters.shape_for("briefing")
		self.assertNotEqual(second["blocks"][0]["heading"], "MUTATED")
		self.assertNotEqual(
			lesson_starters.SHAPES["briefing"]["blocks"][0]["heading"], "MUTATED"
		)

	def test_an_unknown_shape_is_refused(self):
		with self.assertRaises(Exception):
			lesson_starters.shape_for("no-such-shape")

	def test_the_prose_is_instructions_not_filler(self):
		"""Filler is worse than an empty page, because filler gets published."""
		blob = _text(STARTERS)
		self.assertIn("Replace with", blob)
		self.assertIn("Replace this with", blob)

	def test_a_shape_places_media_a_course_spec_could_not(self):
		"""The one capability a lesson shape has that a course starter does not:
		`validate_course_spec` excludes uploaded media, so `templates_gallery`
		writes "add a photo here" in a callout. A lesson shape goes through
		`BLOCK_ALLOWED_FIELDS` instead and can place the empty slot itself."""
		media = {"Image", "Video", "PDF", "Downloadable File"}
		found = {block.get("block_type") for _, block in _shape_blocks()} & media
		self.assertTrue(found, "no shape places a media slot; the whole point of the level")

	def test_a_shape_that_wants_a_quiz_says_so_without_setting_it(self):
		wanting = [s for s in lesson_starters.list_shapes() if s["suggests_quiz"]]
		self.assertTrue(wanting, "no shape suggests a quiz, so the nudge is dead code")


# ================================================== reaching the canvas


class TestTheDeadEndIsGone(unittest.TestCase):
	def test_the_canvas_no_longer_tells_anybody_to_edit_a_url(self):
		"""The sentence the desk rail's only authoring link used to land on."""
		code = _canvas_code()
		self.assertNotIn("add ?course=", code)
		self.assertNotIn("to the URL", code)

	def test_the_picker_was_replaced_rather_than_left_beside_the_home_screen(self):
		"""Two landing surfaces would mean one of them is the stale one."""
		code = _canvas_code()
		self.assertNotIn("render_course_picker", code)
		self.assertIn("render_home(", code)

	def test_every_caller_reaches_the_home_screen(self):
		"""`render()`, `handle_route()` and `reload()` all land here with no course."""
		code = _canvas_code()
		self.assertGreaterEqual(code.count("this.render_home()"), 3)

	def test_the_home_screen_lists_courses_and_offers_a_new_one(self):
		code = _canvas_code()
		self.assertIn("list_authorable_courses", code)
		self.assertIn("create_from_starter", code)

	def test_a_course_can_be_left_without_the_browser_back_button(self):
		"""The course name in the top bar is the only persistent chrome on a
		full-bleed page; before this, switching courses meant retyping the URL."""
		code = _canvas_code()
		self.assertIn("	go_home() {", code)
		self.assertIn("paint_course_name()", code)

	def test_leaving_a_course_flushes_first(self):
		"""Navigating away with an edit still in the autosave buffer loses it."""
		code = _canvas_code()
		# Anchored on the DEFINITION, not on any `go_home()`. Written the loose way
		# this test failed the moment a call site was added earlier in the file,
		# which is the sliding-window vacuity a source-window assertion invites:
		# had the window landed somewhere that happened to contain the token, it
		# would have passed while asserting nothing.
		at = code.index("	go_home() {")
		self.assertIn("save_then", code[at : at + 700])

	def test_leaving_a_course_clears_the_query_string(self):
		"""Leaving routes to the bare page. Since v1.536.1 the course is a route
		segment, so that route differs from the course's and is a real history step:
		Back returns to the lesson just left. `_home` is set first so the route change
		coming back through `handle_route` does not draw the home screen twice.
		(Name kept: the route used to carry `?course=`, and leaving it set reopened
		the course on the next `on_page_show`.)"""
		code = _canvas_code()
		at = code.index("	go_home() {")
		window = code[at : at + 700]
		self.assertIn('frappe.set_route("training-canvas")', window)
		self.assertIn("this._home = true", window)


class TestTheWorkspaceHasADoor(unittest.TestCase):
	def _workspace(self):
		return json.loads(_text(WORKSPACE))

	def test_there_is_a_canvas_shortcut(self):
		shortcuts = self._workspace()["shortcuts"]
		matching = [s for s in shortcuts if s.get("link_to") == "training-canvas"]
		self.assertEqual(len(matching), 1, "expected exactly one Course canvas shortcut")
		self.assertEqual(matching[0]["type"], "Page")

	def test_the_shortcut_is_actually_drawn(self):
		"""A shortcut absent from `content` exists on the document and renders
		nowhere — the workspace layout is that blob, not the child table."""
		workspace = self._workspace()
		label = next(s["label"] for s in workspace["shortcuts"] if s["link_to"] == "training-canvas")
		drawn = [
			item["data"]["shortcut_name"]
			for item in json.loads(workspace["content"])
			if item.get("type") == "shortcut"
		]
		self.assertIn(label, drawn)

	def test_every_shortcut_in_content_exists_on_the_document(self):
		"""The other half: a `content` entry naming a shortcut that is not there
		renders an empty tile."""
		workspace = self._workspace()
		labels = {s["label"] for s in workspace["shortcuts"]}
		for item in json.loads(workspace["content"]):
			if item.get("type") != "shortcut":
				continue
			with self.subTest(shortcut=item["data"]["shortcut_name"]):
				self.assertIn(item["data"]["shortcut_name"], labels)

	def test_a_patch_forces_the_resync(self):
		"""A Workspace JSON is age-gated on the way in: `import_file` silently skips
		a file whose `modified` is not newer than the row. Bumping the stamp reaches
		a fresh install and no existing site."""
		self.assertTrue(PATCH.exists())
		src = _text(PATCH)
		self.assertIn("reload_doc", src)
		self.assertIn("force=True", src)
		self.assertIn("reload_training_workspace_for_canvas", _text(PATCHES_TXT))

	def test_the_patch_cannot_abort_the_deploy(self):
		"""A patch that raises aborts `bench migrate`, which on this repo IS the
		deploy — v1.395.0 left prod schema-synced with no fixtures that way. A
		workspace shortcut is not worth it."""
		tree = ast.parse(_text(PATCH))
		execute = next(
			node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "execute"
		)
		self.assertTrue(
			any(isinstance(node, ast.Try) for node in ast.walk(execute)),
			"execute() does not guard its body",
		)


class TestTheEndpointsBehindIt(unittest.TestCase):
	def _functions(self, path):
		out = {}
		for node in ast.parse(_text(path)).body:
			if isinstance(node, ast.FunctionDef):
				out[node.name] = node
		return out

	def _is_whitelisted(self, node):
		for dec in node.decorator_list:
			target = dec.func if isinstance(dec, ast.Call) else dec
			if getattr(target, "attr", "") == "whitelist":
				return True
		return False

	def test_the_home_endpoints_are_whitelisted(self):
		functions = self._functions(AUTHOR_API)
		for name in ("list_authorable_courses", "list_lesson_starters"):
			with self.subTest(endpoint=name):
				self.assertIn(name, functions)
				self.assertTrue(self._is_whitelisted(functions[name]))

	def test_the_home_endpoints_are_author_gated(self):
		"""The home screen enumerates unpublished drafts. A learner holds no role
		that should be able to list them from anywhere."""
		functions = self._functions(AUTHOR_API)
		for name in ("list_authorable_courses", "list_lesson_starters"):
			with self.subTest(endpoint=name):
				body = ast.unparse(functions[name])
				self.assertIn("_require_author()", body)

	def test_lesson_drafting_is_gated_twice(self):
		functions = self._functions(AI_API)
		self.assertIn("draft_lesson_content", functions)
		node = functions["draft_lesson_content"]
		self.assertTrue(self._is_whitelisted(node))
		body = ast.unparse(node)
		self.assertIn("_require_author()", body)
		self.assertIn("_require_ai_enabled()", body)

	def test_the_course_list_never_asks_for_a_sql_function_as_a_string(self):
		"""Frappe 16 raises "SQL functions are not allowed as strings in SELECT",
		and nothing bench-free sees it: the stub's `get_all` takes any string and
		ruff sees a plain one. v1.474.0 shipped two on a green build and the Record
		Matching page could not load."""
		node = self._functions(AUTHOR_API)["list_authorable_courses"]
		for call in ast.walk(node):
			if not isinstance(call, ast.Call):
				continue
			name = getattr(call.func, "attr", "")
			if name not in ("get_all", "get_list"):
				continue
			for keyword in call.keywords:
				if keyword.arg != "fields":
					continue
				for element in getattr(keyword.value, "elts", []):
					if not isinstance(element, ast.Constant):
						continue
					with self.subTest(field=element.value):
						self.assertNotIn("(", str(element.value))

	def test_the_lesson_count_is_tallied_rather_than_aggregated(self):
		node = self._functions(AUTHOR_API)["list_authorable_courses"]
		body = ast.unparse(node)
		self.assertNotIn("count(", body.lower().replace("lesson_counts", ""))


class TestTwoDoorsOneScaffolder(unittest.TestCase):
	"""A starter is a Course Spec and nothing else; the two galleries that offer
	them must not become two ways of building a course."""

	def test_both_surfaces_call_the_same_endpoint(self):
		for path in (CANVAS_JS, LIST_JS):
			with self.subTest(surface=path.name):
				code = _strip_js_comments(_text(path))
				self.assertIn("training_course_authoring.list_starters", code)
				self.assertIn("training_course_authoring.create_from_starter", code)

	def test_neither_surface_creates_a_course_any_other_way(self):
		for path in (CANVAS_JS, LIST_JS):
			with self.subTest(surface=path.name):
				code = _strip_js_comments(_text(path))
				self.assertNotIn("author_course_from_spec", code)
				self.assertNotIn('frappe.new_doc("Training Course"', code)

	def test_both_land_the_author_in_the_editor(self):
		"""The point of a starter is that the next thing you do is replace the
		words. Landing on the course form instead means finding the editor first."""
		for path in (CANVAS_JS, LIST_JS):
			with self.subTest(surface=path.name):
				code = _strip_js_comments(_text(path))
				at = code.index("create_from_starter")
				window = code[at : at + 1400]
				if path == CANVAS_JS:
					# The canvas opens it the way every canvas door does now: through
					# open_course, which puts the course IN the route
					# (/desk/training-canvas/<course>) rather than in route_options, which
					# v16 never writes to the address bar.
					self.assertIn("this.open_course(out.course", window)
					body = code[code.index("\topen_course(name, opts) {") :][:300]
					self.assertIn("this.route_for(name)", body)
					self.assertIn('const parts = ["training-canvas"];', code)
				else:
					self.assertIn("training-canvas", window)


# ============================================== a whole lesson, first time


class TestCreatingAWholeLesson(unittest.TestCase):
	def test_the_button_offers_shapes(self):
		code = _canvas_code()
		self.assertIn("list_lesson_starters", code)
		self.assertIn("open_new_lesson_dialog(", code)

	def test_the_blank_lesson_is_still_reachable_if_the_shapes_are_not(self):
		"""The shapes are a convenience, not the feature. An author must still be
		able to add a lesson when the endpoint is unreachable."""
		code = _canvas_code()
		at = code.index("\tadd_lesson()")
		window = code[at : at + 700]
		self.assertIn(".catch(", window)
		self.assertIn("commit_new_lesson(", window)

	def test_the_canvas_never_ticks_the_quiz_box_for_the_author(self):
		"""`_validate_quiz` throws on `has_quiz` with an empty pool. The nudge is a
		sentence, never a field."""
		code = _canvas_code()
		at = code.index("commit_new_lesson(spec)")
		window = code[at : at + 2200]
		self.assertNotIn("has_quiz", window)
		self.assertIn("suggests_quiz", window)

	def test_every_created_block_gets_a_fresh_key(self):
		code = _canvas_code()
		at = code.index("commit_new_lesson(spec)")
		self.assertIn("this.new_block_key()", code[at : at + 2200])

	def test_created_blocks_are_copied_through_the_allowlist(self):
		"""Trusting the payload wholesale would let a shape — or a model — set
		`name`, `idx` or a key the server then reconciles against a lesson that does
		not exist yet."""
		code = _canvas_code()
		at = code.index("commit_new_lesson(spec)")
		self.assertIn("TC_BLOCK_FIELDS.forEach", code[at : at + 2200])

	def test_an_ai_draft_keeps_the_shapes_media_slots(self):
		"""A model cannot attach a file, so `draft_lesson_content` emits no media at
		all. If a draft simply replaced the shape, choosing "Video lesson" and then
		drafting would silently throw away the one block that made it one."""
		code = _canvas_code()
		at = code.index("draft_lesson_content")
		window = code[at : at + 1600]
		self.assertIn("TC_MEDIA_TYPES", window)

	def test_changing_the_shape_drops_a_draft_written_for_the_old_one(self):
		"""Carrying it across would mix a briefing's prose into an equipment
		walkthrough's slots, and the author would not find out until save time."""
		code = _canvas_code()
		self.assertIn("drafted = null", code)

	def test_the_drafting_offer_is_hidden_where_it_cannot_work(self):
		"""A button that opens nothing is worse than no button — see the "Open
		Builder" placeholder that outlived its feature by three releases."""
		code = _canvas_code()
		at = code.index("paintAi = ()")
		self.assertIn("this.ai_enabled", code[at : at + 400])


class TestWhatTheReviewCaught(unittest.TestCase):
	"""Regressions for the defects an adversarial review found in this change.

	Each one shipped in the first draft of v1.489.0 and was fixed before merge.
	They are grouped because they share a shape: every one is invisible until a
	particular sequence of clicks, and none of them throws.
	"""

	def test_the_home_screen_tears_down_the_lesson_settings_panel(self):
		"""`.tc-lessonsettings` is a SIBLING of the block list, so emptying
		`.tc-blocks` leaves it on screen — and hiding its toggle button removes the
		only way to close it. Worse, its controls close over a lesson `reset()` has
		already dropped, and the AI bar's buttons are not gated on `editable()`:
		`draft_questions` calls `render_sheet()`, which would empty `$blocks` and
		wipe the course list the author is reading."""
		code = _canvas_code()
		at = code.index("clear_course_chrome() {")
		window = code[at : at + 900]
		self.assertIn("this.$lessonset", window)
		self.assertIn(".empty()", window)
		# Both no-course surfaces must use it, not just the home screen.
		for caller in ("render_home() {", "render_no_draft() {"):
			with self.subTest(caller=caller):
				start = code.index(caller)
				self.assertIn("this.clear_course_chrome()", code[start : start + 400])

	def test_the_course_name_loses_every_handler_and_affordance(self):
		"""Unbinding only `click.tchome` leaves a live keydown handler plus
		`role="button"` and `tabindex` on an emptied span — a focus stop that
		announces itself as a button and navigates on Enter."""
		code = _canvas_code()
		at = code.index("clear_course_chrome() {")
		window = code[at : at + 900]
		self.assertIn("click.tchome keydown.tchome", window)
		self.assertIn('removeAttr("role")', window)
		self.assertIn('removeAttr("tabindex")', window)

	def test_a_filtered_search_cannot_redefine_the_whole_list(self):
		"""Keyed on the last response, the flag narrows permanently: above the cap,
		the first search that matches under it comes back `truncated: false`, which
		cleared the flag and replaced the cache — so clearing the box showed three
		courses out of two hundred, hid the "type to search the rest" note, and never
		asked the server again."""
		code = _canvas_code()
		self.assertNotIn("_home_truncated", code, "the response-keyed flag is back")
		at = code.index("load_home_courses(search) {")
		window = code[at : at + 1600]
		# Only an UNFILTERED load may set it.
		self.assertIn('if (!(search || "")) this._home_capped', window)

	def test_clearing_the_search_box_refetches_the_full_list(self):
		"""The other half. Without it the cache stays narrowed even once the flag
		is right, because nothing asks for the unfiltered page again."""
		code = _canvas_code()
		at = code.index("$search.on(\"input\"")
		window = code[at : at + 900]
		self.assertIn('query.length >= 2 ? query : ""', window)

	def test_the_drafting_topic_survives_a_repaint(self):
		"""`paintAi()` rebuilds the textarea, so the button it relabels to "Draft
		again" read an empty box and refused with "Say what it should cover first" —
		a button that could never work on its first press, with the sentence it
		wanted no longer on screen."""
		code = _canvas_code()
		at = code.index("const paintAi = ()")
		window = code[at : at + 2600]
		self.assertIn(".val(topicText)", window)
		self.assertIn("topicText = String($topic.val()", window)

	def test_the_quiz_pool_is_not_hidden_behind_the_tick_box(self):
		"""The deadlock the `suggests_quiz` nudge pointed straight into. The pool
		lived inside `$quizbox`, which `paintQuiz` toggles on `has_quiz` — so
		reaching "Add a question" required ticking the box, and
		`_validate_quiz` throws on a ticked box with an empty pool. The only order
		the UI allowed was the one the validator refuses, 1200ms later, on every
		save until the author guessed that unticking would stop it."""
		code = _canvas_code()
		at = code.index("this.$quizpool = $(")
		line = code[at : code.index("\n", at)]
		self.assertIn("this.$lessonset", line)
		self.assertNotIn("$quizbox", line)

	def test_the_nudge_describes_an_order_the_ui_permits(self):
		"""It tells the author to add a question and then tick the box. That is only
		true while the test above holds."""
		code = _canvas_code()
		at = code.index("suggests_quiz) {")
		self.assertIn("then tick", code[at : at + 500])

	def test_no_shape_puts_author_instructions_in_a_learner_facing_field(self):
		"""`caption` is rendered to learners as `<p class="tr-block-caption">` and
		used as the `<img alt>`. "A photo of the actual machine, labelled" would have
		shipped to every learner and every screen reader on any lesson where the
		author attached the photo and left the caption — the normal case, because in
		the editor a caption reads as help text."""
		for shape in lesson_starters.list_shapes():
			for block in shape["blocks"]:
				with self.subTest(shape=shape["key"], block_type=block["block_type"]):
					self.assertFalse(
						(block.get("caption") or "").strip(),
						"caption is learner-facing; the empty slot is its own instruction",
					)

	def test_the_blank_shape_does_not_name_the_lesson_after_itself(self):
		"""The gallery label is the title fallback, and "Blank" is the name of a
		choice rather than of a lesson."""
		# Anchored on the DIALOG's primary action. `code.index("lesson_title:")`
		# finds `add_lesson`'s endpoint-unreachable fallback first, several hundred
		# lines earlier -- the same sliding-window vacuity this suite already fixed
		# twice, and the reason a source-window assertion needs a unique anchor.
		code = _canvas_code()
		at = code.index("primary_action: () => {")
		self.assertIn('chosen.key === "blank"', code[at : at + 700])

	def test_the_course_list_is_read_through_the_permitted_path(self):
		"""`frappe.get_all` is documented "will **not** check for permissions" — it
		is `get_list` with `ignore_permissions=True`. This endpoint enumerates
		documents for a person, which is the one query shape where that difference
		IS the security model."""
		tree = ast.parse(_text(AUTHOR_API))
		node = next(
			n
			for n in tree.body
			if isinstance(n, ast.FunctionDef) and n.name == "list_authorable_courses"
		)
		for call in ast.walk(node):
			if not isinstance(call, ast.Call):
				continue
			args = [a.value for a in call.args if isinstance(a, ast.Constant)]
			if "Training Course" in args:
				self.assertEqual(
					getattr(call.func, "attr", ""),
					"get_list",
					"the course enumeration must not bypass permissions",
				)
				break
		else:
			self.fail("no query against Training Course found")

	def test_the_search_term_cannot_act_as_a_wildcard(self):
		"""Unescaped, a search for "50%" matches everything after "50" and the
		author reads the result as the courses about 50%."""
		node = _py_code(AUTHOR_API)
		at = node.index("def list_authorable_courses")
		window = node[at : at + 1500]
		self.assertIn("replace('%', '\\\\%')", window.replace('"', "'"))

	def test_the_new_ai_tests_are_collected_by_a_direct_run(self):
		"""They were appended AFTER `if __name__ == "__main__": unittest.main()`, so
		`python tests/test_training_ai.py` ran the guard before the classes existed
		and collected 63 of 90. `python -m unittest` imported the module and saw all
		of them, so nothing in CI noticed."""
		lines = _text(APP / "tests/test_training_ai.py").rstrip().split("\n")
		guard = [i for i, line in enumerate(lines) if line.startswith('if __name__ ==')]
		self.assertEqual(len(guard), 1)
		after = [line for line in lines[guard[0] + 1 :] if line.strip() and not line.startswith(("\t", " "))]
		self.assertEqual(after, [], f"module-level code after the __main__ guard: {after}")


class TestTheHomeScreenIsStyled(unittest.TestCase):
	def test_every_class_the_home_screen_renders_has_a_rule(self):
		"""Class names come from the stylesheet and are not invented in the page
		script — the canvas says so at the top of the file, and an unstyled home
		screen is an unreadable one.

		Bare containers (`tc-home-list`) are deliberately absent: they exist to be
		selected from JavaScript and need no rule, and adding CSS to satisfy a test
		is how a stylesheet fills up with rules nobody can delete."""
		css = _text(CANVAS_CSS)
		for name in (
			"tc-home",
			"tc-home-head",
			"tc-home-actions",
			"tc-home-row",
			"tc-home-title",
			"tc-home-meta",
			"tc-home-pill",
			"tc-home-group",
			"tc-starter",
			"tc-shape",
			"tc-newai",
		):
			with self.subTest(css_class=name):
				self.assertIn("." + name, css)

	def test_the_status_pill_covers_every_status_the_doctype_has(self):
		"""Read off the Training Course DocType, not from a list written here.

		The first version of this hard-coded three of the four statuses, so
		`Retired` -- the one that most needs to look different from `Published` --
		was unguarded, and its second assertion looked for the bare word in the
		whole file, where "Draft" and "Published" appear in a dozen unrelated
		places. Both halves are keyed on the doctype now, and the group-heading
		check looks inside `home_group_label` rather than anywhere at all."""
		course = json.loads(_text(APP / "training/doctype/training_course/training_course.json"))
		statuses = [
			opt
			for field in course["fields"]
			if field["fieldname"] == "status"
			for opt in (field.get("options") or "").split("\n")
			if opt
		]
		self.assertGreaterEqual(len(statuses), 4, "the status Select was not read")

		css = _text(CANVAS_CSS)
		code = _canvas_code()
		at = code.index("home_group_label(status) {")
		labels = code[at : at + 500]
		for status in statuses:
			slug = status.lower().replace(" ", "-")
			with self.subTest(status=status):
				self.assertIn(f".tc-home-pill.is-{slug}", css)
				self.assertIn(status, labels, "home_group_label has no heading for this status")


if __name__ == "__main__":
	unittest.main()
