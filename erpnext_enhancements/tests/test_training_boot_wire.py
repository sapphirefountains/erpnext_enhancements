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
JS_DIR = APP / "public/js/training"
PLAYER = JS_DIR / "player.js"

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


def _js_body(code, declaration):
    """The full body of a JS function, by brace matching.

    Not "everything up to the next `function `" — which is what the first version
    did, and it is wrong the moment a function contains a callback. `finishLesson`
    opens with `.then(function (result) {`, so that slice ended four lines in and
    an assertion about the rest of the body failed against perfectly correct code.
    """
    start = code.index(declaration)
    open_brace = code.index("{", start)
    depth = 0
    for end in range(open_brace, len(code)):
        if code[end] == "{":
            depth += 1
        elif code[end] == "}":
            depth -= 1
            if depth == 0:
                return code[start : end + 1]
    return code[start:]


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
    # Nothing. Every dead fallback found here was removed rather than
    # tolerated: `course_title`, `name` and `estimated_minutes` were all
    # unreachable second terms that made the source claim the server might
    # send a field it never sends.
    ALLOWED_EXTRA = set()

    def _card_reads(self):
        body = _js_body(_player_code(), "function courseCard(")
        return set(re.findall(r"\bcourse\.([a-z_]+)", body))

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


def _whitelisted_signatures():
    """``{endpoint: (all_params, required_params)}`` for every whitelisted method.

    Read from the source because there is no bench in CI. Frappe drops kwargs a
    function does not declare, so an *extra* key is only misleading — but a
    *missing* required one is a 500 before the endpoint body ever runs.
    """
    tree = ast.parse(RUNTIME.read_text(encoding="utf-8"))
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        whitelisted = any(
            getattr(getattr(dec, "func", dec), "attr", "") == "whitelist"
            for dec in node.decorator_list
        )
        if not whitelisted:
            continue
        params = [a.arg for a in node.args.args]
        n_default = len(node.args.defaults)
        required = params[: len(params) - n_default] if n_default else list(params)
        out[node.name] = (set(params), set(required))
    return out


def _transport_map():
    """``{transportName: endpoint}`` from www/training.html."""
    template = (APP / "www/training.html").read_text(encoding="utf-8")
    block = template[template.index("var METHOD = {") : template.index("var PREFIX")]
    return dict(re.findall(r'(\w+):\s*"(\w+)"', block))


def _call_payloads():
    """Every transport call site and the object-literal keys it passes.

    Matches both shapes the runtime uses: ``call("name", { ... })`` inside
    player.js, and ``transport.name({ ... })`` inside video.js and quiz.js.
    """
    sites = []
    for path in sorted(JS_DIR.glob("*.js")):
        src = path.read_text(encoding="utf-8")
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        code = "\n".join(
            line for line in src.splitlines() if not line.strip().startswith("//")
        )
        for name in _transport_map():
            pattern = rf'transport\.{name}\(\s*\{{|call\("{name}",\s*\{{'
            for match in re.finditer(pattern, code):
                start = code.index("{", match.start())
                depth, end = 0, start
                for end in range(start, len(code)):
                    if code[end] == "{":
                        depth += 1
                    elif code[end] == "}":
                        depth -= 1
                        if depth == 0:
                            break
                keys = set(re.findall(r"[{,]\s*([A-Za-z_]\w*)\s*:", code[start : end + 1]))
                sites.append((path.name, name, keys))
    return sites


class TestTransportArguments(unittest.TestCase):
    """Every transport call must satisfy its endpoint's signature.

    The bug this exists for: the player called
    ``getLesson({course, lesson_key})`` while the endpoint is
    ``get_lesson(attempt, lesson_key)``. Frappe filtered out the unknown
    ``course``, found no ``attempt``, and raised ``TypeError: get_lesson() missing
    1 required positional argument`` — a 500 on the first click of any course.

    The player's own header comment documented the API it was written against,
    and that API was never built: it assumed the server tracked the current
    attempt in session, so the client only ever sent ``lesson_key``. Four calls
    were wrong the same way, plus ``open_checkpoint`` missing its required ``at``.
    """

    def test_the_scan_finds_call_sites(self):
        """Both call shapes, in every file that uses one.

        The first version of this only required "more than five sites and a
        getLesson", and passed while the scanner was silently blind to half the
        runtime: a stray control character had eaten the `transport.` branch of
        the pattern, so only player.js's `call("name", {...})` sites were seen and
        video.js was never scanned at all. Mutation testing caught it; this guard
        did not. Naming the files is what makes the guard actually guard.
        """
        sites = _call_payloads()
        self.assertGreater(len(sites), 8, "the call-site scanner found almost nothing")
        seen = {filename for filename, _, _ in sites}
        for expected in ("player.js", "video.js"):
            self.assertIn(expected, seen, f"no transport calls found in {expected}")
        names = {name for _, name, _ in sites}
        self.assertIn("getLesson", names)  # call("name", {...}) shape
        self.assertIn("openCheckpoint", names)  # transport.name({...}) shape

    def test_the_signature_scan_works(self):
        sigs = _whitelisted_signatures()
        self.assertIn("get_lesson", sigs)
        self.assertIn("attempt", sigs["get_lesson"][1])

    def test_no_call_omits_a_required_argument(self):
        sigs = _whitelisted_signatures()
        methods = _transport_map()
        problems = []
        for filename, name, keys in _call_payloads():
            endpoint = methods.get(name)
            if endpoint not in sigs:
                continue
            missing = sorted(sigs[endpoint][1] - keys)
            if missing:
                problems.append(f"{filename}: {name} -> {endpoint}() missing {missing}")
        self.assertEqual(
            problems,
            [],
            "transport calls that will 500 before the endpoint runs:\n  "
            + "\n  ".join(problems),
        )

    def test_no_call_passes_an_argument_the_endpoint_does_not_declare(self):
        """Not fatal — Frappe drops unknown kwargs — but every one is a sentence
        of the source claiming something the server does not do. Three were
        found this way, including a ``lesson_key`` on ``get_media_url``."""
        sigs = _whitelisted_signatures()
        methods = _transport_map()
        problems = []
        for filename, name, keys in _call_payloads():
            endpoint = methods.get(name)
            if endpoint not in sigs:
                continue
            extra = sorted(keys - sigs[endpoint][0])
            if extra:
                problems.append(f"{filename}: {name} -> {endpoint}() has no {extra}")
        self.assertEqual(problems, [], "\n  ".join(problems))

    def test_every_mapped_method_is_actually_called(self):
        """A mapped method nobody calls is a feature that silently does not exist.

        ``finishAttempt`` was mapped from the first day and called from nowhere —
        so a learner could complete every lesson in a course and it would simply
        never finish. No Training Completion, no certificate, and the assignment
        left open. Nothing errored; the player just returned them to the course
        page looking done.
        """
        called = set()
        for path in sorted(JS_DIR.glob("*.js")):
            src = path.read_text(encoding="utf-8")
            src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
            code = "\n".join(
                line for line in src.splitlines() if not line.strip().startswith("//")
            )
            for name in _transport_map():
                if re.search(rf'\btransport\.{name}\b|call\("{name}"', code):
                    called.add(name)
        # get_my_transcript is a built endpoint with no view in front of it yet.
        # Listed rather than tolerated silently, so it stays visible as missing.
        unbuilt = {"getTranscript"}
        never = sorted(set(_transport_map()) - called - unbuilt)
        self.assertEqual(
            never, [], f"mapped but never called from anywhere: {never}"
        )

    def test_the_course_is_actually_finished(self):
        """Named separately because of what it costs: without this there is no
        completion record and no certificate, however much the learner did.

        Asserts the **wiring**, not just that a `finishCourse` function exists.
        Checking only for the call left a mutation green that unhooked it from
        `finishLesson` — the function sat there, complete and unreachable, which
        is exactly the original bug in a slightly different costume.
        """
        code = _player_code()
        self.assertIn('call("finishAttempt"', code)
        self.assertIn("function finishCourse()", code, "finishCourse is not defined")
        self.assertIn(
            "finishCourse()",
            _js_body(code, "function finishLesson()"),
            "finishLesson never reaches finishCourse, so the last lesson ends nowhere",
        )

    def test_the_attempt_is_threaded_through(self):
        """Every runtime endpoint except the two entry points is scoped to an
        attempt. That is the session model, and the player has to carry it."""
        sigs = _whitelisted_signatures()
        methods = _transport_map()
        for filename, name, keys in _call_payloads():
            endpoint = methods.get(name)
            if endpoint in sigs and "attempt" in sigs[endpoint][1]:
                self.assertIn(
                    "attempt", keys, f"{filename}: {name} does not send an attempt"
                )


if __name__ == "__main__":
    unittest.main()
