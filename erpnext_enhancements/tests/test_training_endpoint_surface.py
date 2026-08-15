# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The learner runtime's HTTP surface: what is exposed, and how it may be called.

``api/training.py`` is the whole learner API — thirteen whitelisted functions that
between them mint signed media URLs, record watch coverage, serve checkpoints and
finish an attempt. Until v1.299.4 not one of them declared ``methods=``, so every
one also answered a **GET**, and a GET puts the attempt id, the lesson key and the
checkpoint timestamp in the query string — which means the web server's access log,
the browser's history, and the ``Referer`` header of whatever the learner clicks
next. This is the same class ``chat/endpoints.py`` closed in v1.283.0.

The client never relied on that: ``www/training.html``'s ``call()`` has always used
``method: "POST"`` and its comment has always claimed *"Every call is a POST"*. So
the transport and the server disagreed in the direction where the server was the
looser of the two, and nothing was watching.

Three properties, and the third is the one that keeps this true later:

1. **Every whitelisted endpoint declares POST.** Asserted on the decorator, not on
   a list someone maintains.
2. **Every method name the player can dial resolves to one of them.** A rename in
   ``api/training.py`` with no matching edit to the ``METHOD`` map is a 404 the
   learner sees and CI does not.
3. **Every endpoint is either in that map or declared unreachable-by-the-player
   with a reason.** Set equality, not a subset check — so a new endpoint cannot be
   added and left un-wired and un-explained. That is the same doctrine
   ``test_training_boundary_contract`` states as *"asymmetries are allowed, silence
   about them is not"*.

Bench-free: AST over ``api/training.py`` plus a text read of ``www/training.html``.

Run: python -m unittest erpnext_enhancements.tests.test_training_endpoint_surface
"""

import ast
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
API = APP / "api" / "training.py"
PAGE = APP / "www" / "training.html"

#: Whitelisted endpoints the player's ``METHOD`` map deliberately does not carry.
#: Each needs a reason, and the reason has to survive somebody reading it.
NOT_DIALLED_BY_THE_PLAYER = {
    "get_learner_bootstrap": (
        "Called server-side, not over HTTP: www/training.py imports it and runs it "
        "inside get_context, so the shell renders with the learner's assigned "
        "courses already in it. One round trip on purpose — the portal is opened on "
        "phones on site. Declaring POST here changes nothing for that path (the "
        "decorator gates HTTP dispatch, not a Python call) and keeps the rule "
        "uniform if it is ever dialled directly."
    ),
}


def _tree():
    return ast.parse(API.read_text(encoding="utf-8"))


def _whitelisted():
    """Every whitelisted function, mapped to its decorator node."""
    found = {}
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if getattr(target, "attr", "") == "whitelist":
                found[node.name] = dec
    return found


def _body(name):
    """A function's source minus its docstring.

    A character-window slice was the first version of this and it failed: the docstrings in
    ``api/training.py`` are long enough that 900 characters after ``def`` never reached the
    code. Slicing by offset assumes something about prose; the AST does not.
    """
    text = API.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            src = ast.get_source_segment(text, node) or ""
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                doc = ast.get_source_segment(text, body[0].value)
                if doc:
                    src = src.replace(doc, "", 1)
            return src
    raise AssertionError(f"{name}() not found in api/training.py")


def _method_map():
    """The endpoint names ``www/training.html`` can dial, from its ``METHOD`` map.

    Read from the map's own braces rather than by grepping the file for identifiers:
    the page mentions endpoint names in prose too, and a scan that cannot tell a
    comment from a dispatch table is satisfied by deleting the comment.
    """
    src = PAGE.read_text(encoding="utf-8")
    match = re.search(r"var METHOD = \{(.*?)\n\t\};", src, re.S)
    assert match, "the METHOD map has moved or changed shape; re-derive this scan"
    body = re.sub(r"//.*$", "", match.group(1), flags=re.M)
    return set(re.findall(r':\s*"([a-z_]+)"', body))


class TheScanWorksTest(unittest.TestCase):
    """Both halves must actually find something, or every test below is vacuous."""

    def test_it_finds_the_endpoints(self):
        self.assertGreaterEqual(len(_whitelisted()), 13)

    def test_it_finds_the_method_map(self):
        self.assertGreaterEqual(len(_method_map()), 12)


class PostOnlyTest(unittest.TestCase):
    def test_every_endpoint_declares_post(self):
        """A GET here leaks the attempt id and lesson key into three logs.

        Asserted on the decorator so it holds for an endpoint added next year, not
        on a list that would have to be remembered.
        """
        offenders = []
        for name, dec in sorted(_whitelisted().items()):
            declared = None
            if isinstance(dec, ast.Call):
                for kw in dec.keywords:
                    if kw.arg == "methods":
                        declared = ast.literal_eval(kw.value)
            if declared != ["POST"]:
                offenders.append(f"{name} -> {declared!r}")
        self.assertEqual(
            offenders,
            [],
            "these api/training.py endpoints do not declare methods=[\"POST\"], so "
            "they also answer a GET and put attempt/lesson/checkpoint keys in the "
            f"query string: {offenders}",
        )


class TheTwoSidesAgreeTest(unittest.TestCase):
    """The player dials by string. Nothing else checks these names line up."""

    def test_every_dialled_name_exists(self):
        missing = sorted(_method_map() - set(_whitelisted()))
        self.assertEqual(
            missing,
            [],
            f"www/training.html dials {missing}, which api/training.py does not "
            "whitelist — a 404 the learner sees and CI does not",
        )

    def test_every_endpoint_is_dialled_or_explained(self):
        """Set equality. A subset check would let a new endpoint sit unreachable."""
        stray = sorted(set(_whitelisted()) - _method_map() - set(NOT_DIALLED_BY_THE_PLAYER))
        self.assertEqual(
            stray,
            [],
            f"{stray} are whitelisted in api/training.py but the player cannot dial "
            "them. Either wire them into the METHOD map or add them to "
            "NOT_DIALLED_BY_THE_PLAYER with a reason.",
        )

    def test_the_reasons_are_still_about_real_endpoints(self):
        """An allowlist that outlives its subject stops being a record and becomes
        a place things were filed."""
        gone = sorted(set(NOT_DIALLED_BY_THE_PLAYER) - set(_whitelisted()))
        self.assertEqual(gone, [], f"NOT_DIALLED_BY_THE_PLAYER names {gone}, which no longer exist")

    def test_every_reason_says_something(self):
        for name, reason in NOT_DIALLED_BY_THE_PLAYER.items():
            self.assertGreater(len(reason.strip()), 40, f"{name}'s reason is a placeholder")


class AskTheAuthorIsReachableTest(unittest.TestCase):
    """``training/qa.py`` shipped complete in v1.215.0 with **no caller anywhere**.

    The changelog said the feature landed, and the backend really was there — visibility gate,
    author resolution, notification, all of it. No learner could reach a line of it. That is
    the failure this class keeps closed, and it is why every check here is about
    *reachability* rather than about the function existing.
    """

    PLAYER = APP / "public/js/training/player.js"

    def test_both_endpoints_are_dialled_by_the_player(self):
        """Not merely present in the METHOD map — actually called."""
        src = self.PLAYER.read_text(encoding="utf-8")
        for method in ("askQuestion", "lessonQuestions"):
            self.assertIn(f'call("{method}"', src, f"player.js never calls transport.{method}")

    def test_the_map_carries_both(self):
        for name in ("ask_lesson_question", "lesson_questions"):
            self.assertIn(name, _method_map(), f"{name} is not in the player's METHOD map")

    def test_the_wrappers_delegate_rather_than_reimplement(self):
        """A second copy of the visibility gate would drift from the one in ``training/qa.py``,
        and a drift in a permission rule is a leak. These wrappers exist for the transport's
        single PREFIX, not to make decisions."""
        for fn, delegate in (
            ("ask_lesson_question", "qa.ask_question"),
            ("lesson_questions", "qa.get_lesson_questions"),
        ):
            body = _body(fn)
            self.assertIn(delegate, body, f"{fn} does not delegate to {delegate}")
            self.assertNotIn("_visible_or_throw", body, f"{fn} re-implements the visibility gate")

    def test_the_panel_renders_untrusted_text_without_innerhtml(self):
        """Questions are learner-typed and answers are author-typed, and both are rendered into
        a page a third learner opens. ``el()`` sets textContent; an innerHTML anywhere in this
        file would turn a question into script."""
        self.assertNotIn("innerHTML", self.PLAYER.read_text(encoding="utf-8"))

    def test_the_disclosure_is_announced(self):
        """The panel is collapsed by default, so a screen reader has to be told it is a
        disclosure and what it controls — otherwise the button is an unlabelled dead end."""
        src = self.PLAYER.read_text(encoding="utf-8")
        self.assertIn("aria-expanded", src)
        self.assertIn("aria-controls", src)


class TheClientStillPostsTest(unittest.TestCase):
    """The server rule is only safe because the client already obeyed it."""

    def test_the_transport_posts(self):
        src = PAGE.read_text(encoding="utf-8")
        self.assertRegex(
            src,
            r"fetch\(PREFIX \+ method, \{\s*\n\s*method: \"POST\"",
            "www/training.html's call() no longer posts — declaring the server "
            "POST-only would 405 every learner request",
        )

    def test_the_beacon_path_is_a_post_too(self):
        """``navigator.sendBeacon`` is always a POST, and the last heartbeat on
        pagehide goes through it. Named here because it is the one call that does
        not use ``call()``, so the assertion above does not cover it."""
        src = (APP / "www" / "training.html").read_text(encoding="utf-8")
        self.assertIn("sendBeacon", src)


if __name__ == "__main__":
    unittest.main()
