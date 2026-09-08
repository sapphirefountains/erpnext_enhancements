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
  * the **builder** opens the real bubble through ``window.SapphireTriton.ask``,
    the same pattern the Training Course form already uses.

Run: python -m unittest erpnext_enhancements.tests.test_training_triton_authoring
"""

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"


class TestTheCustomTridentIsGone(unittest.TestCase):
    def test_no_endpoint_or_wrapper_remains(self):
        ai = (APP / "api/training_ai.py").read_text(encoding="utf-8")
        api = (APP / "api/training.py").read_text(encoding="utf-8")
        self.assertNotIn("draft_course_with_triton", ai)
        self.assertNotIn("def draft_course(", api)
        # The boot key that only gated the removed trident is gone too, so the
        # boundary contract is not left with a sent-but-never-read key.
        self.assertNotIn("can_author", api)

    def test_the_learner_player_has_no_trident(self):
        player = (APP / "public/js/training/player.js").read_text(encoding="utf-8")
        css = (APP / "public/css/training/player.css").read_text(encoding="utf-8")
        method_map = (APP / "www/training.html").read_text(encoding="utf-8")
        self.assertNotIn("tritonFab", player)
        self.assertNotIn("tr-triton-fab", css)
        self.assertNotIn("draftCourse", method_map)


class TestTheBuilderReusesTheRealBubble(unittest.TestCase):
    def test_the_builder_opens_the_real_triton_via_sapphiretriton(self):
        js = (APP / "training/page/training_builder/training_builder.js").read_text(encoding="utf-8")
        self.assertIn("open_triton_authoring", js)
        self.assertIn("window.SapphireTriton", js)
        self.assertIn("SapphireTriton.ask", js)
        # It never resurrects a private authoring endpoint.
        self.assertNotIn("draft_course_with_triton", js)
        self.assertNotIn("tb-triton-fab", js)

    def test_the_training_course_form_pattern_still_exists(self):
        # The established reuse pattern the builder mirrors; a sanity anchor so this
        # test fails loudly if the shared opener is renamed or removed.
        form = (APP / "public/js/training/training_course.js").read_text(encoding="utf-8")
        self.assertIn("window.SapphireTriton", form)
        self.assertIn("SapphireTriton.ask", form)


if __name__ == "__main__":
    unittest.main()
