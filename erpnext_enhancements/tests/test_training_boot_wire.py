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



def _python_function_source(source, name):
    """One function's source text, by AST line span.

    `_js_body` brace-matches, which is meaningless against Python — pointing it at
    a `def` returns whatever happens to sit after the first `{` in the file, and
    an assertion against that passes or fails for no reason connected to the code.
    """
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"no function named {name}")


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


class TestOutlineRowFields(unittest.TestCase):
    """The lesson outline read three fields the server does not put on a toc row.

    ``_public_toc`` yields ``{lesson_key, chapter_key, title, minutes, has_quiz,
    blocks}``. ``outlineRow`` read ``row.status``, ``row.locked`` and
    ``row.lock_reason``. All three were always ``undefined``, so every lesson drew
    the "not started" circle and offered "Open" no matter how much of it the
    learner had done — there was no way to see progress from the course page.

    ``row.locked`` being undefined is also why nothing was ever locked, which is
    correct: the server recommends an order via ``next_lesson_key`` and does not
    enforce one. That branch was UI for a feature that does not exist.
    """

    def _toc_keys(self):
        """Read from the *publisher*, not the runtime.

        ``_public_toc`` just reloads `toc_json` and strips the internal docname, so
        the shape is only ever written down in `api/training_author.py`, in the
        `toc.append({...})` that runs at publish. That is the one place to compare
        against — chasing it through the runtime would compare the player to a
        `json.loads`.
        """
        author = (APP / "api/training_author.py").read_text(encoding="utf-8")
        tree = ast.parse(author)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "append"
                and getattr(node.func.value, "id", "") == "toc"
            ):
                continue
            if node.args and isinstance(node.args[0], ast.Dict):
                keys = {
                    k.value
                    for k in node.args[0].keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                }
                # `lesson` is the internal docname and _public_toc strips it before
                # the row ever reaches a browser.
                return keys - {"lesson"}
        return set()

    def _row_reads(self):
        body = _js_body(_player_code(), "function outlineRow(")
        return set(re.findall(r"\brow\.([a-z_]+)", body))

    def test_the_toc_shape_is_found(self):
        self.assertIn("lesson_key", self._toc_keys())
        self.assertIn("has_quiz", self._toc_keys())

    def test_every_field_the_row_reads_is_sent(self):
        unknown = sorted(self._row_reads() - self._toc_keys())
        self.assertEqual(
            unknown, [], f"outlineRow reads {unknown}, which a toc row does not carry"
        )

    def test_status_is_derived_not_expected_on_the_row(self):
        code = _player_code()
        self.assertIn("function lessonStatus(", code)
        self.assertIn("lessonStatus(row.lesson_key)", _js_body(code, "function outlineRow("))

    def test_the_attempt_progress_reaches_the_outline(self):
        """`state.progress` was only ever filled by get_lesson, so on the course
        page — which is where the outline lives — it was empty.

        Asserts the assignment, not the mention: checking for `attempt.lessons`
        anywhere in the function passed a mutation that kept the `if` guard and
        assigned `{}` instead.
        """
        self.assertIn(
            "state.progress.lessons = attempt.lessons",
            _js_body(_player_code(), "function adoptAttempt("),
        )


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


class TestRuntimeModulesMeet(unittest.TestCase):
    """The joins between the four player files, and the shim that hid them.

    Two of these were broken from Phase 2 and reached a learner untouched:

    * ``blocks.js`` calls ``TR.Video.mount(el, block, ctx)``. ``video.js``
      exported only the constructor, so the guard in blocks.js fired and every
      Video block rendered *"The video player did not load. Please refresh the
      page."* — on a player that had loaded perfectly well.
    * ``player.js`` called ``TR.Quiz.mount(root, quizPayload, {submit, …})``.
      ``quiz.js``'s signature is ``mount(root, ctx, transport)`` with the
      questions at ``ctx.quiz``, so ``normalise(ctx.quiz)`` got ``undefined`` and
      the quiz rendered with no questions, no attempt and a Submit that went
      nowhere.

    **The builder patched both at runtime.** Its own comment called the shims
    "not a substitute for fixing them" — and then nothing fixed them, for four
    releases, because the preview an author checks their work in was the one
    place the breakage could not be seen. That is the part worth guarding: a
    preview may not repair the runtime on its way past.
    """

    @staticmethod
    def _js(name):
        src = (JS_DIR / name).read_text(encoding="utf-8")
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        return "\n".join(
            line for line in src.splitlines() if not line.strip().startswith("//")
        )

    @staticmethod
    def _builder():
        path = APP / "training/page/training_builder/training_builder.js"
        src = path.read_text(encoding="utf-8")
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        return "\n".join(
            line for line in src.splitlines() if not line.strip().startswith("//")
        )

    def test_video_exports_the_entry_point_blocks_js_calls(self):
        self.assertIn("TR.Video.mount(", self._js("blocks.js"))
        self.assertIn("Video.mount = ", self._js("video.js"))

    def test_video_mount_translates_the_block_shape(self):
        """`duration_s` on the wire, `duration` in the spec — and coverage is a
        fraction of it, so getting this wrong silently disables the gate."""
        code = self._js("video.js")
        body = code[code.index("Video.mount = ") :][:2200]
        self.assertIn("block.duration_s", body)
        self.assertIn("block_key", body)
        self.assertIn("ctx.lessonKey", body)

    def test_video_mount_uses_the_players_heartbeat(self):
        """The player wraps the raw call to stamp the lesson key, merge progress
        and refresh the gate. Going straight to the transport leaves the coverage
        meter and the Finish button frozen while the video plays."""
        code = self._js("video.js")
        body = code[code.index("Video.mount = ") :][:2200]
        self.assertIn("ctx.heartbeat", body)

    def test_the_quiz_is_mounted_with_the_signature_quiz_js_documents(self):
        player = self._js("player.js")
        call = player[player.index("TR.Quiz.mount(") :][:1400]
        self.assertIn("quiz: state.quiz", call)
        self.assertIn("submitQuiz:", call)
        # The old shape put the callbacks under different names in a third arg.
        self.assertIn("onResult:", call)
        self.assertIn("onExit:", call)

    def test_quiz_reads_those_exact_names(self):
        """Both halves asserted together, so renaming one side fails here rather
        than rendering an empty quiz in front of a learner."""
        quiz = self._js("quiz.js")
        for name in ("ctx.quiz", "ctx.onResult", "ctx.onExit", "transport.submitQuiz"):
            self.assertIn(name, quiz)

    def test_the_builder_does_not_patch_the_runtime(self):
        """The load-bearing one.

        Any monkey-patch of a runtime module from the builder makes the preview
        disagree with what a learner gets, and the preview is where an author
        checks their work. Both bugs above survived four releases behind exactly
        this.
        """
        builder = self._builder()
        self.assertNotIn("install_runtime_shims", builder)
        for forbidden in ("TR.Video.mount =", "TR.Quiz.mount =", "TR.Player ="):
            self.assertNotIn(
                forbidden,
                builder,
                f"the builder assigns {forbidden} — the preview must not repair the runtime",
            )


class TestHeartbeatIsShapedForItsEndpoint(unittest.TestCase):
    """The beat has to travel under ``payload``, and nothing else does.

    ``heartbeat(attempt, payload=None)`` is the only runtime endpoint that takes a
    nested body. Every other one takes explicit arguments, so the player's habit
    of spreading a dict across the top level is right everywhere except here — and
    here it was silently catastrophic: Frappe bound ``attempt``, left ``payload``
    at its default of ``None``, and the server recorded an empty beat.

    **Nothing errored.** An empty beat is a perfectly valid beat that credits
    nothing, so watch coverage sat at 0% forever, the gate never opened, and the
    meter never moved. It survived from Phase 2 to a learner watching a video and
    asking why the number was not changing.

    The earlier transport-argument scanner could not see this: it only inspects
    call sites written as object literals, and ``transport.heartbeat(payload)``
    passes a variable. This class checks the *adapter* instead, which is where the
    shaping actually happens.
    """

    @staticmethod
    def _player():
        src = PLAYER.read_text(encoding="utf-8")
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        return "\n".join(
            line for line in src.splitlines() if not line.strip().startswith("//")
        )

    @staticmethod
    def _template():
        return (APP / "www/training.html").read_text(encoding="utf-8")

    def test_the_endpoint_still_takes_a_nested_payload(self):
        """If the server is ever flattened, this whole class is wrong and should
        fail loudly rather than enforce a stale shape."""
        sigs = _whitelisted_signatures()
        self.assertIn("payload", sigs["heartbeat"][0])

    def test_the_adapter_nests_the_beat(self):
        code = self._player()
        start = code.index("heartbeat: function")
        body = code[start : start + 700]
        self.assertIn("payload:", body, "the beat is not sent under `payload`")
        self.assertIn("attempt:", body, "the attempt is not sent alongside it")

    def test_the_adapter_does_not_spread_the_beat(self):
        """The bug itself: `call("heartbeat", body)` with the beat as the body."""
        code = self._player()
        start = code.index("heartbeat: function")
        body = code[start : start + 700]
        self.assertNotIn('call("heartbeat", body)', body)

    def test_the_beacon_is_synchronous_and_returns_a_boolean(self):
        """video.js drops a beat from its retry queue on a truthy return. The
        generic transport wrapper returns a Promise — always truthy — so every
        queued beat was dropped as delivered whether or not it ever left."""
        template = self._template()
        self.assertIn("transport.heartbeatBeacon = function", template)
        start = template.index("transport.heartbeatBeacon = function")
        body = template[start : start + 900]
        # The RETURN path, not a mention. `navigator.sendBeacon` also appears in
        # the capability guard on the first line, so asserting presence passed a
        # mutation that swapped the actual send for a fetch — which returns a
        # Promise and puts the always-truthy bug straight back.
        self.assertIn("return navigator.sendBeacon(", body)
        self.assertIn("return false", body)

    def test_the_beacon_uses_the_same_nested_shape(self):
        template = self._template()
        start = template.index("transport.heartbeatBeacon = function")
        body = template[start : start + 900]
        self.assertIn("payload: beat", body)
        self.assertIn("attempt: beat.attempt", body)

    def test_the_beacon_carries_csrf_in_the_body(self):
        """sendBeacon cannot set headers, so the token has to travel in the body.
        Deliberately `csrf_token` and never a key named `sid` — Frappe's auth pops
        that one and reports a session expiry that did not happen."""
        template = self._template()
        start = template.index("transport.heartbeatBeacon = function")
        body = template[start : start + 900]
        self.assertIn("csrf_token", body)
        self.assertNotIn("sid", body)


# ------------------------------------------------------------------ checkpoints
#
# The eighth, ninth and tenth wire mismatches in this module, all on one seam and
# all in the same direction: the runtime invented short names, and both consumers
# — video.js and the builder's preview harness — used the doctype's. Fixed in
# TASK-2026-01174 / 01179 by moving the runtime, since a name that says its unit
# beats a name that saves four characters.
#
# Read the header of this file for why these checks are static.

VIDEO = JS_DIR / "video.js"
BUILDER = APP / "training/page/training_builder/training_builder.js"


def _strip_comments(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def _video_code():
    return _strip_comments(VIDEO.read_text(encoding="utf-8"))


def _builder_code():
    return _strip_comments(BUILDER.read_text(encoding="utf-8"))


def _js_object_keys(code, marker):
    """The TOP-LEVEL keys of the object literal ``marker``'s function returns.

    Depth-aware on purpose. A flat regex over the body also collects
    ``option_key`` and ``text`` out of the nested map that builds ``options``,
    which makes the comparison against the server's payload fail for a reason
    that has nothing to do with the two shapes agreeing.
    """
    body = _js_body(code, marker)
    start = body.index("return {")
    depth = 0
    keys = set()
    for line in body[start:].splitlines():
        if depth == 1:
            found = re.match(r"\s*([a-z_]+):", line)
            if found:
                keys.add(found.group(1))
        depth += line.count("{") - line.count("}")
        if depth <= 0 and keys:
            break
    return keys


class TestCheckpointPayloadIsReadable(unittest.TestCase):
    """Guards the assertions below from passing because a parse went stale."""

    def test_the_payload_shape_is_found(self):
        keys = _returned_keys("_checkpoint_payload")
        self.assertIn("checkpoint_key", keys)
        self.assertGreaterEqual(len(keys), 6)

    def test_the_video_is_readable(self):
        self.assertGreater(len(_video_code()), 5000)


class TestCheckpointFieldNames(unittest.TestCase):
    def test_the_player_reads_the_names_the_server_sends(self):
        """Five of seven disagreed. `at` vs `at_seconds` was the load-bearing one:
        `maybeFireCheckpoint` compared a NaN against the playhead, so even an
        armed checkpoint could not fire."""
        sent = _returned_keys("_checkpoint_payload")
        code = _video_code()
        for field in ("checkpoint_key", "at_seconds", "question_text", "question_type"):
            self.assertIn(field, sent, f"_checkpoint_payload no longer sends {field}")
            self.assertIn(field, code, f"video.js no longer reads {field}")

    def test_the_short_names_are_gone(self):
        """`at`, `question`, `type`, `rewind`, `pause`, `scored` — the abbreviations
        nothing ever read. Named individually so a reviewer sees the whole set."""
        sent = _returned_keys("_checkpoint_payload")
        for gone in ("at", "question", "type", "rewind", "pause", "scored"):
            self.assertNotIn(gone, sent, f"_checkpoint_payload is sending `{gone}` again")

    def test_the_builder_preview_matches_the_runtime_key_for_key(self):
        """`preview_checkpoint` exists so an author can test a checkpoint before
        a learner meets one. It is worth nothing if it serves a different shape —
        and it drifted precisely because nothing compared the two."""
        preview = _js_object_keys(_builder_code(), "preview_checkpoint(cp) {")
        self.assertEqual(
            preview,
            _returned_keys("_checkpoint_payload"),
            "training_builder.preview_checkpoint and api.training._checkpoint_payload "
            "have drifted apart",
        )


class TestCheckpointEnvelope(unittest.TestCase):
    def test_the_player_unwraps_the_envelope(self):
        """`open_checkpoint` replies {enabled, checkpoint}. armNext read
        `cp.checkpoint_key` off the envelope, got undefined, and armed nothing —
        for every learner, on every attempt, since the endpoint was written."""
        body = _js_body(_video_code(), "function armNext()")
        self.assertIn("res.checkpoint", body)

    def test_the_runtime_still_sends_the_envelope(self):
        keys = _returned_keys("open_checkpoint")
        self.assertEqual(keys, {"enabled", "checkpoint"})

    def test_the_builder_preview_sends_the_envelope_too(self):
        code = _builder_code()
        self.assertIn("enabled: true, checkpoint:", code)


class TestRewindHasOneAuthority(unittest.TestCase):
    """TASK-2026-01183. Two computations of where playback resumes, and they did
    not agree: grading rewinds only when an attempt is left, the client rewound on
    any wrong answer. The client's has been deleted."""

    def test_the_client_honours_the_server_position(self):
        body = _js_body(_video_code(), "function answerCheckpoint(cp, optionKeys, submitBtn)")
        self.assertIn("res.rewind_applied", body)
        self.assertIn("res.resume_at", body)

    def test_the_client_no_longer_derives_it(self):
        self.assertNotIn("rewind_seconds_on_wrong", _video_code())

    def test_the_server_still_computes_it(self):
        grading = (APP / "training/grading.py").read_text(encoding="utf-8")
        self.assertIn('"resume_at"', grading)
        self.assertIn('"rewind_applied"', grading)

    def test_the_dead_rewind_key_is_gone(self):
        """`answer_checkpoint` also bolted a raw `rewind` onto the reply. Unread,
        and it disagreed with the `rewind_applied` sitting beside it."""
        body = _js_body(RUNTIME.read_text(encoding="utf-8"), "def answer_checkpoint(")
        self.assertNotIn('payload["rewind"]', body)


class TestCheckpointsCanRearm(unittest.TestCase):
    """The break neither task named. `armNext` runs once at mount and can only
    fetch a checkpoint the playhead has already reached — so with the envelope and
    the field names both fixed, a checkpoint at 0:30 of a 90-second video was still
    unreachable. `next_checkpoint_at` is how the server says one is coming; it was
    sent on every beat and read by nobody."""

    def test_the_server_sends_it_on_every_beat(self):
        # Not via _returned_keys: the endpoint bolts this onto the reply with a
        # subscript assignment rather than putting it in the dict literal, so
        # there is nothing for the AST walk to see.
        self.assertIn('result["next_checkpoint_at"]', RUNTIME.read_text(encoding="utf-8"))

    def test_the_player_reads_it_off_the_beat(self):
        body = _js_body(_video_code(), "function applyBeatResult(res)")
        self.assertIn("next_checkpoint_at", body)

    def test_reaching_it_arms_the_checkpoint(self):
        body = _js_body(_video_code(), "function maybeFireCheckpoint(from, to)")
        self.assertIn("nextCheckpointAt", body)
        self.assertIn("armNext()", body)

    def test_the_builder_preview_reports_it(self):
        """Otherwise the author previews a video in which no pin ever fires."""
        self.assertIn("next_checkpoint_at: next_at", _builder_code())


# ------------------------------------------------------------------------ quiz
#
# TASK-2026-01175. Four names disagreed across `submit_quiz`, so a learner who
# failed with attempts in hand was shown no way to use them, and the per-question
# score breakdown rendered blank.
#
# The task guessed the server should be renamed. It should not: both server names
# have other readers — `attempts_left` is read by `player.js` on the lesson result
# panel, `awarded` by grading on its way to the Training Attempt Question row's
# `points_awarded`. The client names had no readers at all. So the client moved,
# except for `can_retry`, which nothing sent and which is now derived once on the
# server rather than reconstructed from parts on the client.

QUIZ = JS_DIR / "quiz.js"


def _quiz_code():
    return _strip_comments(QUIZ.read_text(encoding="utf-8"))


class TestQuizReplyKeys(unittest.TestCase):
    def test_the_scan_works(self):
        self.assertGreater(len(_quiz_code()), 5000)
        self.assertIn("attempts_left", _returned_keys("get_quiz"))

    def test_retry_reads_the_name_the_server_sends(self):
        body = _js_body(_quiz_code(), "function canRetry(result)")
        self.assertIn("result.attempts_left", body)
        self.assertNotIn("attempts_remaining", body)

    def test_the_attempts_line_reads_it_too(self):
        body = _js_body(_quiz_code(), "function attemptsText(result)")
        self.assertIn("result.attempts_left", body)
        self.assertNotIn("attempts_remaining", body)

    def test_attempts_remaining_is_gone_entirely(self):
        """It was never a key, only a belief about one."""
        self.assertNotIn("attempts_remaining", _quiz_code())
        self.assertNotIn("attempts_remaining", _player_code())

    def test_the_per_question_score_reads_awarded(self):
        body = _js_body(_quiz_code(), "function renderReview(entry, i, byId, numberOf, result)")
        self.assertIn("entry.awarded", body)
        self.assertNotIn("entry.earned", body)

    def test_the_server_still_calls_it_awarded(self):
        grading = (APP / "training/grading.py").read_text(encoding="utf-8")
        self.assertIn('"awarded"', grading)

    def test_attempts_left_still_has_its_other_reader(self):
        """Renaming it server-side would have moved the break to the lesson result
        panel rather than closing it. Pinned so nobody tries."""
        self.assertIn("result.attempts_left", _player_code())


class TestCanRetryIsSaidOutLoud(unittest.TestCase):
    """Silence used to mean no, and the server was always silent."""

    def test_the_server_sends_it(self):
        self.assertIn('payload["can_retry"]', RUNTIME.read_text(encoding="utf-8"))

    def test_the_client_prefers_it_over_inferring(self):
        body = _js_body(_quiz_code(), "function canRetry(result)")
        self.assertIn("result.can_retry === true", body)
        self.assertIn("result.can_retry === false", body)

    def test_the_attempt_counters_are_sent(self):
        runtime = RUNTIME.read_text(encoding="utf-8")
        for key in ("attempts_used", "max_attempts"):
            self.assertIn(f'payload["{key}"]', runtime)

    def test_the_builder_preview_says_it_too(self):
        """Otherwise an author previewing a failed quiz sees no Try again button
        and reasonably concludes the learner will not get one either."""
        builder = _builder_code()
        self.assertIn("can_retry:", builder)
        self.assertIn("awarded:", builder)


# ----------------------------------------------------------------- get_lesson
#
# TASK-2026-01177. `get_lesson` sends the progress of the ONE lesson it was asked
# for; the player assigned it over the slot holding the whole {lessons: {...}} map.
# So opening any lesson erased every other lesson's state — and its own, because
# `lessonProgress()` reads `state.progress.lessons`, which the assignment left
# undefined. A half-finished course rendered as entirely not started.


class TestGetLessonProgressIsMerged(unittest.TestCase):
    # The reply handler is an anonymous `.then`, so there is no declaration for
    # `_js_body` to brace-match from. Slice forward off the call itself, the same
    # way the heartbeat-beacon tests above do.
    def _handler(self):
        code = _player_code()
        start = code.index('call("getLesson"')
        return code[start : start + 1400]

    def test_the_scan_works(self):
        keys = _returned_keys("get_lesson")
        self.assertIn("progress", keys)
        self.assertIn("next_checkpoints", keys)
        self.assertIn("payload.progress", self._handler())

    def test_the_single_lesson_dict_is_not_assigned_over_the_map(self):
        self.assertNotIn("state.progress = payload.progress", self._handler())

    def test_it_is_merged_under_its_own_key(self):
        body = self._handler()
        self.assertIn("state.progress.lessons = state.progress.lessons ||", body)
        self.assertIn("lessons[key] = payload.progress", body)

    def test_the_server_still_sends_one_lesson_not_a_map(self):
        """If this ever starts returning {lessons: {...}} the merge above becomes
        a nesting bug, so the two have to be pinned together."""
        runtime = RUNTIME.read_text(encoding="utf-8")
        self.assertIn("def _lesson_progress(attempt_name, lesson_key):", runtime)
        self.assertIn('.get("lessons") or {}).get(lesson_key)', runtime)

    def test_nothing_else_replaces_the_progress_object(self):
        """The sweep the task asked for. Every other write folds into the map —
        `mergeHeartbeat` and `recordQuizRun` both use the defaulting idiom, and
        `adoptAttempt` replaces `lessons` with the server's own full map, which is
        the authoritative one. A bare `state.progress = <anything else>` is the bug
        class returning."""
        found = re.findall(r"state\.progress\s*=\s*([^;\n]+)", _player_code())
        unexpected = [rhs.strip() for rhs in found if rhs.strip() != "state.progress || {}"]
        self.assertEqual(unexpected, [], f"player.js replaces state.progress with {unexpected}")


class TestNextCheckpointsIsReadAtLast(unittest.TestCase):
    """Sent by `get_lesson` since Phase 2 and read by nothing.

    It closes a real hole rather than being tidy-up. Beats do not start until
    roughly ten seconds of credited playback, and the mount-time `armNext()` can
    only reach a checkpoint within tolerance of the resume position — so a
    checkpoint in the opening seconds was too late for the arm and too early for
    the first beat, and by the time one arrived the playhead was past it.
    """

    def test_the_player_hands_it_to_the_block_context(self):
        body = _js_body(_player_code(), "function blockContext()")
        self.assertIn("nextCheckpoints", body)

    def test_the_player_still_stores_it(self):
        self.assertIn("state.nextCheckpoints = payload.next_checkpoints", _player_code())

    def test_the_video_mount_reads_it_off_the_context(self):
        body = _js_body(_video_code(), "Video.mount = function (wrapper, block, ctx)")
        self.assertIn("ctx.nextCheckpoints", body)
        self.assertIn("next_checkpoint_at", body)

    def test_the_video_seeds_its_state_from_the_spec(self):
        self.assertIn("spec.next_checkpoint_at", _video_code())


# -------------------------------------------------------------- finish_attempt
#
# TASK-2026-01180. Three exits, three hand-assembled dicts, and the differences
# between them were invisible until you hit the right one. Re-opening a course
# passed at full marks rendered "Passed - 0%", because the already-finished early
# return carried no `score` and `pct(undefined)` is 0.


class TestFinishAttemptHasOneSerializer(unittest.TestCase):
    def _runtime(self):
        return RUNTIME.read_text(encoding="utf-8")

    def test_the_serializer_exists(self):
        self.assertIn("def _finished_attempt_payload(", self._runtime())

    def test_every_exit_goes_through_it(self):
        """Counted rather than spot-checked: a fourth exit added later that
        assembles its own dict is the same bug again."""
        body = _python_function_source(self._runtime(), "finish_attempt")
        self.assertEqual(body.count("_finished_attempt_payload("), 3)
        self.assertNotIn('"passed":', body)

    def test_the_shape_carries_score_on_every_path(self):
        keys = _returned_keys("_finished_attempt_payload")
        self.assertEqual(
            keys, {"passed", "attempt", "status", "score", "outstanding", "completion"}
        )

    def test_the_reopened_path_reads_the_recorded_score(self):
        """Off the record, not recomputed — recomputing could quietly disagree
        with the completion certificate already issued."""
        body = _python_function_source(self._runtime(), "finish_attempt")
        self.assertIn("_recorded_score(doc)", body)

    def test_a_missing_score_is_none_not_zero(self):
        """Zero is a real score. Absent has to say absent, or the two are the
        same string on screen."""
        body = _python_function_source(self._runtime(), "_recorded_score")
        self.assertIn("return None", body)
        self.assertIn("if score is not None else None", body)

    def test_the_player_does_not_render_an_absent_score_as_zero(self):
        body = _js_body(_player_code(), "function renderResults()")
        self.assertIn("result.score != null", body)


class TestFinishAttemptKeysAreRead(unittest.TestCase):
    def test_the_client_reads_every_key_the_payload_sends(self):
        code = _player_code()
        start = code.index('call("finishAttempt"')
        handler = code[start : start + 1400]
        for key in ("status", "outstanding", "passed", "completion", "score"):
            self.assertIn(f"result.{key}", handler, f"player.js stopped reading {key}")


# --------------------------------------------------------------- boot settings
#
# TASK-2026-01182. The client read `boot.settings.max_playback_rate` and
# `boot.settings.doc_min_dwell_seconds`; the server sent neither, so both fell
# back to hard-coded client constants and the two Training Settings fields did
# nothing. They did not exist as fields either — the player was reading settings
# nobody could set.

SETTINGS_DOCTYPE = APP / "training/doctype/training_settings/training_settings.json"
BLOCKS = JS_DIR / "blocks.js"


def _blocks_code():
    return _strip_comments(BLOCKS.read_text(encoding="utf-8"))


def _settings_fieldnames():
    import json

    doc = json.loads(SETTINGS_DOCTYPE.read_text(encoding="utf-8"))
    return {f["fieldname"] for f in doc["fields"]}


def _boot_settings_keys():
    """The keys of the `settings` dict inside get_learner_bootstrap's return."""
    tree = ast.parse(RUNTIME.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "get_learner_bootstrap":
            continue
        for statement in ast.walk(node):
            if not (isinstance(statement, ast.Return) and isinstance(statement.value, ast.Dict)):
                continue
            for key, value in zip(statement.value.keys, statement.value.values):
                if isinstance(key, ast.Constant) and key.value == "settings":
                    return {
                        k.value
                        for k in value.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                    }
    return set()


class TestBootSettingsReachThePlayer(unittest.TestCase):
    def test_the_scan_works(self):
        self.assertIn("heartbeat_interval_seconds", _boot_settings_keys())

    def test_the_fields_exist_on_the_doctype(self):
        """They did not. The client was reading settings nobody could set."""
        fields = _settings_fieldnames()
        self.assertIn("max_playback_rate", fields)
        self.assertIn("doc_min_dwell_seconds", fields)

    def test_both_are_on_the_boot_payload(self):
        keys = _boot_settings_keys()
        self.assertIn("max_playback_rate", keys)
        self.assertIn("doc_min_dwell_seconds", keys)

    def test_every_settings_key_the_player_reads_is_sent(self):
        """Both directions, so a new client-side read cannot go unserved either."""
        sent = _boot_settings_keys()
        read = set(re.findall(r"settings\.([a-z_]+)", _video_code()))
        read |= set(re.findall(r'settingsValue\(ctx, "([a-z_]+)"', _blocks_code()))
        unknown = sorted(read - sent)
        self.assertEqual(unknown, [], f"the player reads {unknown}; boot.settings sends {sorted(sent)}")

    def test_the_video_still_caps_the_rate_itself(self):
        """The setting can tighten the speed menu and must never loosen it past
        MAX_PLAYBACK_RATE — progress.clamp_new_seconds truncates claimed seconds
        at elapsed x 1.25, so a larger rate silently costs a learner watch time
        and earns them an integrity flag for it."""
        self.assertIn("Math.min(flt(settings.max_playback_rate) || MAX_PLAYBACK_RATE, MAX_PLAYBACK_RATE)", _video_code())

    def test_the_document_dwell_is_read_through_the_settings_helper(self):
        self.assertIn('settingsValue(ctx, "doc_min_dwell_seconds"', _blocks_code())

    def test_the_server_owns_the_dwell_target_too(self):
        """A document has no asset row to check a duration against, so the divisor
        cannot be the payload's — see progress.record_heartbeat."""
        prog = (APP / "training/progress.py").read_text(encoding="utf-8")
        self.assertIn('_setting("doc_min_dwell_seconds"', prog)


class TestTheAckRoundTrips(unittest.TestCase):
    """`player.js` has read `response.ack` since the document blocks were written,
    off a reply that never carried it."""

    def test_the_server_returns_it(self):
        prog = (APP / "training/progress.py").read_text(encoding="utf-8")
        self.assertIn('"ack": block.get("ack")', prog)

    def test_the_player_merges_it(self):
        body = _js_body(_player_code(), "function mergeHeartbeat(blockKey, response)")
        self.assertIn("response.ack", body)

    def test_the_gate_can_still_tell_untracked_from_unacknowledged(self):
        """An absent `ack` means "not tracked" and must never hold a learner."""
        self.assertIn("stored.ack === 0", _player_code())
