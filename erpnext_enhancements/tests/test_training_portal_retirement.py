"""`/training` is retired as a rendering surface, and kept as a redirect.

The distinction is the whole subject of this module, and getting either half wrong
fails silently:

* **Delete the route** and every training email ever sent 404s. Six code paths have
  emailed `https://…/training` since v1.208.0 — assignment and due/escalation
  digests, answered questions, sign-off requests, graded submissions, evaluation
  invites — and those messages are still in inboxes. A 404 on a link somebody was
  told to follow reads as the feature being gone.
* **Keep the page** and there are two learner surfaces drifting apart, which is the
  reason the transport was extracted in the first place.

So: the route exists, renders nothing, and redirects. Every assertion here comes in
a pair — something is absent *and* its replacement is present — because "no
occurrences of `/training`" is satisfied just as well by a file somebody emptied.

Run: python -m unittest erpnext_enhancements.tests.test_training_portal_retirement
"""

import json
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
TEMPLATE = APP / "www" / "training.html"
CONTROLLER = APP / "www" / "training.py"

# The six senders, by the file that owns each message.
SENDERS = (
    "training/notifications.py",
    "training/qa.py",
    "training/signoff.py",
    "training/submissions.py",
    "training/evaluations.py",
)


def read(rel):
    return (APP / rel).read_text(encoding="utf-8")


def strip_py_comments(text):
    """`#` lines out, triple-quoted strings LEFT IN, and the second half is the point.

    The obvious version of this also stripped triple-quoted strings, on the usual
    reasoning that a comment naming a token is not a use of it. Here that was wrong,
    and quietly so: these senders build their HTML bodies with f-strings that are
    themselves triple-quoted, so stripping "docstrings" removed five of the six links
    this module counts -- the assertion went green having examined almost nothing.

    The absence checks below match an exact call expression, which no prose contains,
    so there is nothing to gain from stripping and a whole assertion to lose.
    """
    keep = [line for line in text.splitlines() if not line.strip().startswith("#")]
    return chr(10).join(keep)


class TestTheRouteStillExists(unittest.TestCase):
    """A controller with no template is not a page: the URL 404s and `get_context`
    never runs, with no exception and no log line. That is the exact shape
    `scripts/check_www_controllers.py` gained a check for in v1.428.0, and it is the
    way this retirement would fail."""

    def test_both_halves_are_present(self):
        self.assertTrue(TEMPLATE.is_file(), "the route needs a template or it does not exist")
        self.assertTrue(CONTROLLER.is_file())

    def test_the_controller_filename_is_importable(self):
        """Frappe imports a controller by hyphen-to-underscore-ing the template
        basename, so a hyphenated one is never imported and `get_context` silently
        never runs -- which here would mean no redirect at all."""
        self.assertNotIn("-", CONTROLLER.stem)


class TestItRendersNoPlayer(unittest.TestCase):
    def test_the_template_is_short(self):
        """It was 291 lines. Anything approaching that again means somebody put the
        player back rather than pointing at the Desk one."""
        self.assertLess(len(TEMPLATE.read_text(encoding="utf-8").splitlines()), 60)

    def test_it_loads_no_player_script(self):
        for name in ("player.js", "video.js", "quiz.js", "blocks.js", "transport.js"):
            with self.subTest(name):
                self.assertNotIn(name, TEMPLATE.read_text(encoding="utf-8"))

    def test_it_builds_no_mount(self):
        self.assertNotIn('id="training-root"', TEMPLATE.read_text(encoding="utf-8"))


class TestItRedirects(unittest.TestCase):
    def test_it_sends_desk_users_to_the_page(self):
        source = CONTROLLER.read_text(encoding="utf-8")
        self.assertIn("redirect_location", source)
        self.assertIn("/app/learn", source)
        self.assertIn("raise frappe.Redirect", source)

    def test_it_does_not_redirect_unconditionally(self):
        """A user with no desk access sent to /desk gets a login page, which is a
        worse answer than a sentence. Training Learner keeps `desk_access = 0`, so a
        customer contact holding only that role is a Website User -- there are none
        today, and the branch exists so that if one is ever made the failure is a
        paragraph rather than a loop."""
        self.assertIn("user_type", CONTROLLER.read_text(encoding="utf-8"))


class TestEveryEmailedLinkMoved(unittest.TestCase):
    """The pair. Absence alone would pass on a sender somebody deleted."""

    def test_no_sender_still_links_the_retired_route(self):
        for rel in SENDERS:
            with self.subTest(rel):
                code = strip_py_comments(read(rel))
                self.assertNotIn("get_url('/training')", code)
                self.assertNotIn('get_url("/training")', code)

    def test_every_sender_still_links_somewhere(self):
        """Six messages, six links. If this count ever drops, a learner stopped being
        told where to go rather than being told somewhere new."""
        total = 0
        for rel in SENDERS:
            code = strip_py_comments(read(rel))
            total += len(re.findall(r"get_url\(['\"]/app/learn['\"]\)", code))
        self.assertEqual(total, 6, f"expected 6 emailed links into the Desk page, found {total}")


class TestNothingElsePointsAtIt(unittest.TestCase):
    def test_the_portal_menu_entry_is_gone(self):
        """`Training Learner` carries `desk_access = 0`, so a customer contact holding
        it cannot open the destination. A menu item leading to a login page teaches
        people to ignore the menu."""
        hooks = strip_py_comments(read("hooks.py"))
        self.assertNotIn('"route": "/training"', hooks)

    def test_no_workspace_tile_points_at_it(self):
        for path in sorted(APP.glob("*/workspace/*/*.json")):
            doc = json.loads(path.read_text(encoding="utf-8"))
            urls = [s.get("url") for s in doc.get("shortcuts") or [] if s.get("type") == "URL"]
            with self.subTest(path.name):
                self.assertNotIn("/training", urls)

    def test_the_scan_saw_some_workspaces(self):
        """Anti-vacuity for the loop above."""
        self.assertGreater(len(list(APP.glob("*/workspace/*/*.json"))), 5)


class TestTheCustomerApparatusRefuses(unittest.TestCase):
    """`grant_portal_access` minted a login for a page that no longer renders.

    Refused rather than deleted: it keeps its caller on the Contact form, which is
    what keeps the no-uncalled-endpoint gate green and, more to the point, keeps the
    reason attached to the button somebody will eventually press. Reinstating
    customer training is a product decision, and the apparatus below the refusal is
    intact for when it is made.
    """

    def test_it_throws_before_doing_anything(self):
        source = read("training/portal.py")
        start = source.index("def grant_portal_access(")
        body = source[start : start + 2500]
        self.assertIn("frappe.throw(", body)
        self.assertLess(
            body.index("frappe.throw("),
            body.index("_require_manager()"),
            "the refusal must come before any work",
        )

    def test_it_still_has_its_caller(self):
        hooks = read("hooks.py")
        self.assertIn("training/training_portal_access.js", hooks)


if __name__ == "__main__":
    unittest.main()
