"""Reuse the real Triton assistant for training authoring (no second widget).

The training module opens the **real** global Triton chat bubble — via its
sanctioned public opener ``window.SapphireTriton.ask(prompt, context)`` — rather
than shipping a parallel course-authoring widget. Triton proposes a Course Spec
and calls the ``author_training_course`` FAC tool, which materialises an
**unpublished, review-gated** draft; ERPNext never runs a second authoring path.

An earlier iteration (v1.373.0) added a self-contained "Create a course with
Triton" trident on both the learner ``/training`` player and the builder, with its
own ``draft_course_with_triton`` endpoint. That duplicated the real bubble, so it
was removed. These guards keep it removed and keep the reuse wired:

  * the learner ``/training`` player carries no Triton trident (no ``tritonFab``,
    no ``tr-triton-fab``, no ``draftCourse`` transport, no ``can_author`` boot key,
    no ``draft_course`` re-export, no ``draft_course_with_triton`` endpoint);
  * the **Training Course form** opens the real bubble through
    ``window.SapphireTriton.ask``. It is the only surface that does so: the classic
    builder carried the same wiring and was deleted in v1.422.0 (R3), and the canvas
    has no Triton button at all.

Run: python -m unittest erpnext_enhancements.tests.test_training_triton_authoring
"""

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"


# Every assertion below is an ABSENCE assertion, and this project has now been bitten
# thirteen times by the same thing: the comment explaining why a token is gone names the
# token, so a raw substring search matches the explanation and the test passes over a
# genuine regression. These read three source files that v1.416.0 (R1) wrote new comments
# into. They were honest before that change and they are honest after it; they are
# hardened here so they stay honest without anyone having to remember.


def _py(path):
    """Python source with comments and docstrings removed.

    `ast` never retains comments, so unparsing drops them for free; docstrings are
    stripped explicitly because prose about code is not code."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


def _web(path):
    """JS/CSS/HTML source with whole-line and block comments removed.

    Line-START only, matching the `_code` helpers in the sibling suites: a trailing-
    comment stripper that is not quote-aware mangles `https://` inside string literals."""
    out, in_block = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if in_block:
            if "*/" in stripped:
                in_block = False
            continue
        if stripped.startswith("/*"):
            in_block = "*/" not in stripped
            continue
        if stripped.startswith("<!--"):
            in_block = "-->" not in stripped
            continue
        if stripped.startswith("//"):
            continue
        out.append(line)
    return "\n".join(out)


class TestTheCustomTridentIsGone(unittest.TestCase):
    def test_no_endpoint_or_wrapper_remains(self):
        ai = _py(APP / "api/training_ai.py")
        api = _py(APP / "api/training.py")
        self.assertNotIn("draft_course_with_triton", ai)
        self.assertNotIn("def draft_course(", api)
        # The boot key that only gated the removed trident is gone too, so the
        # boundary contract is not left with a sent-but-never-read key.
        self.assertNotIn("can_author", api)

    def test_the_learner_player_has_no_trident(self):
        player = _web(APP / "public/js/training/player.js")
        css = _web(APP / "public/css/training/player.css")
        method_map = _web(APP / "www/training.html")
        self.assertNotIn("tritonFab", player)
        self.assertNotIn("tr-triton-fab", css)
        self.assertNotIn("draftCourse", method_map)


class TestTheCourseFormReusesTheRealBubble(unittest.TestCase):
    """Was `TestTheBuilderReusesTheRealBubble`. Its builder half was deleted with the
    classic builder in v1.422.0 (R3) -- it read training_builder.js, which no longer exists.

    The Training Course form is now the only surface that opens Triton for authoring; the
    canvas has no Triton button at all (grep SapphireTriton in training_canvas.js returns
    nothing), which is stated in training/README.md rather than left to be discovered."""

    def test_the_training_course_form_pattern_still_exists(self):
        # The established reuse pattern the builder mirrors; a sanity anchor so this
        # test fails loudly if the shared opener is renamed or removed.
        form = _web(APP / "public/js/training/training_course.js")
        self.assertIn("window.SapphireTriton", form)
        self.assertIn("SapphireTriton.ask", form)


if __name__ == "__main__":
    unittest.main()
