"""Does the player read the keys the server actually sends?

It did not, and the result was the worst kind of failure: `/training` told every
learner **"Nothing is assigned to you right now"** no matter how much was assigned
to them. Not an error, not a blank page — a confident, wrong, reassuring sentence.

``get_learner_bootstrap`` returns ``assigned`` and ``library``. ``player.js`` read::

    var courses = b.courses || (b.catalog && b.catalog.courses) || [];

Neither key has ever existed. Two more of the same in the course card: the player
read ``progress_percent`` and ``status`` where the server sends ``percent_complete``
and ``assignment_status``, so the progress bar never appeared and the button said
"Start" even half way through a course.

This is the fourth wire mismatch found in this module — after the heartbeat
(``ranges`` vs ``intervals``), the builder's ``change_type`` (truncated Select
value), and the entire stylesheet (a different class vocabulary). They share a
shape: two halves written against different assumptions, each correct alone, with
nothing at runtime that could notice. A missing key in JavaScript is ``undefined``,
and ``undefined || []`` is an empty list, which renders perfectly.

So the check is static. The server's dict literals are parsed out of
``api/training.py`` and compared against what the player reads.

Run: python -m unittest erpnext_enhancements.tests.test_training_boot_wire
"""

import ast
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
RUNTIME = APP / "api/training.py"
PLAYER = APP / "public/js/training/player.js"

# Keys the page's own bootstrap adds on the client side — options passed by
# www/training.html or defaulted by the player, not fields the server sends.
CLIENT_OPTIONS = {"translate", "route_base", "history", "view", "start"}


def _player_code():
    """player.js with comments stripped.

    A comment naming a key is not a read of that key. This module's whole subject
    is the difference between what the code does and what somebody believed it
    did, so counting prose would be especially silly here.
    """
    src = PLAYER.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("//")
    )


def _returned_keys(function_name):
    """The string keys of the dict a function returns, from the source.

    Static because there is no bench in CI. It reads the last ``return {...}`` in
    the function, which is the shape the client is handed.
    """
    tree = ast.parse(RUNTIME.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != function_name:
            continue
        keys = set()
        for statement in ast.walk(node):
            if isinstance(statement, ast.Return) and isinstance(statement.value, ast.Dict):
                for key in statement.value.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        keys.add(key.value)
        return keys
    return set()


class TestTheScanWorks(unittest.TestCase):
    """Guards every assertion below from passing because a parse went stale."""

    def test_the_server_shape_is_found(self):
        keys = _returned_keys("get_learner_bootstrap")
        self.assertIn("assigned", keys)
        self.assertIn("library", keys)

    def test_the_card_shape_is_found(self):
        keys = _returned_keys("_course_card")
        self.assertIn("percent_complete", keys)
        self.assertIn("assignment_status", keys)

    def test_the_player_is_readable(self):
        self.assertGreater(len(_player_code()), 5000)


class TestBootKeysExist(unittest.TestCase):
    def test_every_boot_key_the_player_reads_is_sent(self):
        sent = _returned_keys("get_learner_bootstrap") | CLIENT_OPTIONS
        read = set(re.findall(r"\bb\.([a-z_]+)", _player_code()))
        unknown = sorted(read - sent)
        self.assertEqual(
            unknown,
            [],
            f"player.js reads {unknown} off the bootstrap; the server sends "
            f"{sorted(_returned_keys('get_learner_bootstrap'))}",
        )

    def test_the_assigned_list_is_read(self):
        """Named on its own because this is the bug: everything else could be
        right and the page would still say nothing is assigned."""
        self.assertIn("b.assigned", _player_code())

    def test_the_library_is_read(self):
        """Optional courses the learner may self-enrol in. Sent since Phase 2 and
        never rendered, so the library was invisible."""
        self.assertIn("b.library", _player_code())

    def test_assigned_and_library_are_not_merged(self):
        """The server separates them deliberately. A due course shown among things
        nobody has to do is close to not showing it."""
        code = _player_code()
        self.assertNotIn("b.assigned.concat(b.library)", code)
        self.assertNotIn("[].concat(b.assigned, b.library)", code)


class TestCourseCardFields(unittest.TestCase):
    """The card read two field names the server does not send.

    Neither failed. `pct(undefined)` is 0, so the progress bar simply never drew;
    `undefined === "In Progress"` is false, so the button always read "Start".
    Both are wrong in the direction that looks like a design choice.
    """

    # Fields the card is known to use, mapped to nothing — we assert every
    # `course.<x>` read resolves against the server's card shape.
    ALLOWED_EXTRA = {"course_title"}  # tolerated fallback in the title expression

    def _card_reads(self):
        code = _player_code()
        start = code.index("function courseCard(")
        end = code.index("function ", start + 10)
        return set(re.findall(r"\bcourse\.([a-z_]+)", code[start:end]))

    def test_the_extractor_finds_reads(self):
        self.assertGreater(len(self._card_reads()), 5)

    def test_every_field_the_card_reads_is_sent(self):
        sent = _returned_keys("_course_card") | self.ALLOWED_EXTRA
        unknown = sorted(self._card_reads() - sent)
        self.assertEqual(
            unknown,
            [],
            f"courseCard reads {unknown}, which _course_card does not send",
        )

    def test_progress_uses_the_server_field(self):
        reads = self._card_reads()
        self.assertIn("percent_complete", reads)
        self.assertNotIn("progress_percent", reads)

    def test_status_uses_the_server_field(self):
        reads = self._card_reads()
        self.assertIn("assignment_status", reads)
        self.assertNotIn("status", reads)


class TestTransportNamesExist(unittest.TestCase):
    """The other half of the same contract, kept here so both are checked together.

    ``www/training.html`` maps the player's transport function names onto endpoint
    names. A typo there is the same class of silent break.
    """

    def test_every_mapped_method_is_whitelisted(self):
        template = (APP / "www/training.html").read_text(encoding="utf-8")
        block = template[template.index("var METHOD = {") : template.index("var PREFIX")]
        mapped = set(re.findall(r':\s*"(\w+)"', block))

        source = RUNTIME.read_text(encoding="utf-8")
        whitelisted = {
            match.group(1)
            for match in re.finditer(
                r"@frappe\.whitelist\([^)]*\)\s*\ndef (\w+)", source
            )
        }
        missing = sorted(mapped - whitelisted)
        self.assertEqual(
            missing, [], f"the transport maps {missing}, which api/training.py does not expose"
        )


if __name__ == "__main__":
    unittest.main()
