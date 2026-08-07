"""Both sides of the player↔API boundary, compared.

Twelve-plus defects in this module have shared one shape: a key one side writes
and the other never reads, or reads under a different name. Nothing throws. A
missing key in JavaScript is ``undefined``, ``undefined || []`` is an empty list,
``pct(undefined)`` is 0, and every one of those renders perfectly. The feature is
silently inert and the page looks fine.

The existing suites each pin ONE side of a seam, which is exactly why they stayed
green through eight releases of this. ``test_training_heartbeat_wire`` checks the
beat, ``test_training_boot_wire`` checks the boot payload and the transport
arguments — but nothing compared the *reply bodies*, and that is where the
mismatches were.

So this module enumerates both sides and diffs them:

* **Sent** — every ``@frappe.whitelist()`` function in ``api/training.py``, the
  keys of the dict it returns, and the keys added by the helpers it delegates to.
  The AST is followed through subscript assignment (``payload["x"] = ...``),
  ``dict(other)`` copies, local names, and calls into ``training/grading.py`` and
  ``training/progress.py``.
* **Read** — every ``<binder>.<key>`` in the player, where ``binder`` is a name
  that holds a server reply.

Anything on one side and not the other fails, unless it is in one of the two
allowlists below **with a reason**. That is the whole design: asymmetries are
allowed, silence about them is not.

**Scope: the reply envelope.** Content objects nested inside a reply — course
cards, outline rows, lesson blocks — are deliberately out, because they already
have their own comparisons in ``test_training_boot_wire`` (``TestCourseCardFields``,
``TestOutlineRowFields``). Extending the binder list to ``course``/``lesson``/
``block`` here would duplicate those and drag the whole content tree in with them.

**What it cannot catch:** the right key read off the wrong object. This is a set
comparison, not a type checker. It catches names that exist on one side only,
which is every defect this module has actually shipped.

Static because there is no bench in CI — AST on the Python, regex on the JS.
Deterministic beats clever.

Run: python -m unittest erpnext_enhancements.tests.test_training_boundary_contract
"""

import ast
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
RUNTIME = APP / "api/training.py"
JS_DIR = APP / "public/js/training"

# The runtime plus the two modules it delegates reply-building to. A key added by
# `grading` or `progress` is on the wire just as surely as one written in the
# endpoint itself, and following them is the difference between this test and the
# ones that missed eight releases of the same bug.
SOURCES = {
    "api.training": RUNTIME,
    "grading": APP / "training/grading.py",
    "progress": APP / "training/progress.py",
}

JS_FILES = [JS_DIR / name for name in ("player.js", "video.js", "quiz.js", "blocks.js")] + [
    APP / "www/training.html"
]

# Helpers that build a *reply* rather than content. Followed when they appear as a
# nested value; everything else nested is followed only if it is a literal.
ENVELOPE_BUILDERS = {
    "_checkpoint_payload",
    "_finished_attempt_payload",
    "_unavailable",
}

# Functions that assemble reply rows with `.append({...})` rather than returning a
# literal, so the dict never appears in a `return`. `grade_quiz` builds every
# `per_question` entry this way. Their dict literals are harvested wholesale,
# which is over-inclusive in general and exactly right for a declared reply
# builder.
ROW_BUILDERS = {"grade_quiz"}

# Names that hold a server reply in the player. Curated rather than inferred:
# `.then(function (x)` gives most of them, but `b` is the boot payload passed into
# the Player constructor, `cp` a checkpoint handed around after arming, `entry` a
# per-question row, and no amount of cleverness recovers those from a regex.
# Content binders (`course`, `lesson`, `block`) are deliberately absent — see the
# scope note in the module docstring.
RESPONSE_BINDERS = (
    "res",
    "result",
    "payload",
    "response",
    "attempt",
    "cp",
    "entry",
    "b",
    "settings",
    "src",
    "media",
    "verdict",
    "opt",
)

# ---------------------------------------------------------------------------
# Deliberate asymmetries. Each one needs a reason, and the reasons are load
# bearing: an allowlist without them becomes a place to put things you have
# stopped thinking about.
# ---------------------------------------------------------------------------

SENT_BUT_NOT_READ = {
    # Read off the course object, which TestCourseCardFields and
    # TestOutlineRowFields in test_training_boot_wire already compare.
    "cover_image": "course metadata; covered by TestCourseCardFields",
    "minutes": "course metadata; covered by TestCourseCardFields",
    "passing_score": "read as state.course.passing_score; covered by the card tests",
    "percent_complete": "course/card metadata; covered by TestCourseCardFields",
    "release_notes": "course metadata; covered by TestCourseCardFields",
    "require_checkpoints_answered": "course policy, shown in the gates panel",
    "self_enrol": "course metadata; covered by TestCourseCardFields",
    "slug": "course metadata; covered by TestCourseCardFields",
    "summary": "course metadata; covered by TestCourseCardFields",
    "title": "course metadata; covered by TestCourseCardFields",
    "version_number": "course metadata; covered by TestCourseCardFields",
    "weight": "outline row metadata; covered by TestOutlineRowFields",
    "course_version": "identity, echoed for the desk and the transcript",
    "min_video_coverage": "course policy, read off the course object",
    "questions": "read as ctx.quiz.questions when TR.Quiz is mounted",
    "type": "read as question.type off the question rows, which are content, not envelope",
    # Sent for consumers that are not the four player files.
    "learner": "the host page renders the learner's own name from it",
    "today": "the host page stamps the date; the player uses server_time-free logic",
    "completions": "the transcript view, not the lesson player",
    "attempt_idle_timeout_minutes": "informational; the server enforces the timeout",
    "progress_flush_seconds": "informational; the server owns the flush cadence",
    # Media metadata the player has no use for. Kept because the reply is also
    # what you look at when a signed URL misbehaves.
    "duration_seconds": "the block's own duration_s is authoritative for coverage",
    "expires_in_minutes": "diagnostic; the player re-requests on failure rather than pre-empting",
    "poster": "the poster comes from the block, not the media reply",
    # Echoes and diagnostics.
    "response_ms": "echoed back for the desk's integrity view",
    "server_time": "diagnostic on every beat; the player never trusts its own clock anyway",
    "run": "the run number is tracked client-side from startQuiz",
    "best": "the best score is shown on the course view from the progress map",
    "block_key": "the checkpoint's own block; the player already knows which block it armed",
    # Superseded by the per-question breakdown, which carries points per row.
    "points_earned": "superseded by per_question[].awarded",
    "points_possible": "superseded by per_question[].points",
}

READ_BUT_NOT_SENT = {
    # Options the host page passes into the Player constructor. Same set the boot
    # test calls CLIENT_OPTIONS.
    "history": "client option from www/training.html, not a server field",
    "route_base": "client option from www/training.html, not a server field",
    "start": "client option from www/training.html, not a server field",
    "translate": "client option from www/training.html, not a server field",
    "view": "client option from www/training.html, not a server field",
    # Built by the client on the way OUT, then read back off its own object.
    "client_id": "the player mints this to dedupe its own beat queue",
    "seq": "the player's own beat sequence number",
    "signed_url": "local alias for the media reply's url",
    "selected": "local UI state: which option the learner has ticked",
    "submitted": "local UI state: whether this run has been sent",
    "answer": "local UI state on the review card",
    "rewound": "computed locally from the server's rewind_applied for the hint text",
    "matches": "a local predicate result, not a wire field",
    # Answer-key fields the server deliberately never sends. The player reads them
    # only in the builder-preview shape, where the author is allowed to see them.
    "correct_options": "answer key; served to authors in preview, never to learners",
    "correct_option_keys": "answer key; served to authors in preview, never to learners",
    "accepted_text": "answer key for Short Answer; never leaves the server for a learner",
    "option_text": "the runtime sends `text`; `option_text` is the doctype field the preview uses",
    # DOM and language properties that collide with a binder name.
    "href": "a DOM property on an anchor, not a reply key",
    "id": "a dead `||` fallback after entry.question, and a DOM property elsewhere",
    "is": "part of a CSS class name, not a property read",
    # A branch that cannot be taken.
    "reasons": (
        "complete_lesson throws on an unmet gate rather than returning ok:false, so "
        "the client's !result.ok branch is unreachable and this read with it. Left "
        "as a defensive fallback rather than deleted, because a future endpoint that "
        "reports instead of throwing is a reasonable change and this is the shape it "
        "would use."
    ),
}


# ---------------------------------------------------------------------------
# Extraction — Python
# ---------------------------------------------------------------------------

_TREES = {name: ast.parse(path.read_text(encoding="utf-8")) for name, path in SOURCES.items()}
_FUNCTIONS = {}
for _module, _tree in _TREES.items():
    for _node in ast.walk(_tree):
        if isinstance(_node, ast.FunctionDef):
            _FUNCTIONS.setdefault(_node.name, _node)


def whitelisted_endpoints():
    """Every ``@frappe.whitelist()`` function in the runtime, by name."""
    found = []
    for node in ast.walk(_TREES["api.training"]):
        if not isinstance(node, ast.FunctionDef):
            continue
        if any("whitelist" in ast.unparse(dec) for dec in node.decorator_list):
            found.append(node.name)
    return sorted(found)


def _dict_literal_keys(node):
    keys = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Dict):
            for key in inner.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.add(key.value)
    return keys


def returned_keys(name, seen=None):
    """Every string key a function can put on the dict it returns."""
    seen = seen or frozenset()
    if name in seen or name not in _FUNCTIONS:
        return set()
    seen = seen | {name}
    node = _FUNCTIONS[name]

    if name in ROW_BUILDERS:
        return _dict_literal_keys(node)

    keys = set()

    # `payload["x"] = ...`, but only onto a name that is actually returned.
    # Collecting every subscript write sweeps in storage-internal keys like
    # `block["iv"]`, which are not on the wire at all.
    returned_names = {
        stmt.value.id
        for stmt in ast.walk(node)
        if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Name)
    }
    for stmt in ast.walk(node):
        if not isinstance(stmt, ast.Assign):
            continue
        for target in stmt.targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.slice, ast.Constant)
                and isinstance(target.slice.value, str)
                and isinstance(target.value, ast.Name)
                and target.value.id in returned_names
            ):
                keys.add(target.slice.value)

    def from_expr(expr, depth=0):
        got = set()
        if depth > 6:
            return got
        if isinstance(expr, ast.Dict):
            for key, value in zip(expr.keys, expr.values):
                if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                    continue
                got.add(key.value)
                if isinstance(value, ast.Call):
                    func = value.func
                    called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                    if called in ENVELOPE_BUILDERS:
                        got |= from_expr(value, depth + 1)
                else:
                    # Nested literals belong to this reply: `settings` is a dict
                    # written inline, `options` a comprehension of dict literals.
                    got |= from_expr(value, depth + 1)
        elif isinstance(expr, ast.Call):
            func = expr.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if called == "dict" and expr.args:
                got |= from_expr(expr.args[0], depth + 1)
            elif called:
                got |= returned_keys(called, seen)
        elif isinstance(expr, ast.Name):
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == expr.id for t in stmt.targets
                ):
                    got |= from_expr(stmt.value, depth + 1)
        elif isinstance(expr, ast.BoolOp):
            for value in expr.values:
                got |= from_expr(value, depth + 1)
        elif isinstance(expr, ast.IfExp):
            got |= from_expr(expr.body, depth + 1) | from_expr(expr.orelse, depth + 1)
        elif isinstance(expr, (ast.ListComp, ast.GeneratorExp)):
            got |= from_expr(expr.elt, depth + 1)
        elif isinstance(expr, (ast.List, ast.Tuple)):
            for value in expr.elts:
                got |= from_expr(value, depth + 1)
        return got

    for stmt in ast.walk(node):
        if isinstance(stmt, ast.Return) and stmt.value is not None:
            keys |= from_expr(stmt.value)
    return keys


def keys_sent():
    sent = set()
    for endpoint in whitelisted_endpoints():
        sent |= returned_keys(endpoint)
    for builder in ROW_BUILDERS:
        sent |= returned_keys(builder)
    return sent


# ---------------------------------------------------------------------------
# Extraction — JavaScript
# ---------------------------------------------------------------------------


def player_source():
    """The player files with comments stripped.

    A comment naming a key is not a read of it, and this module's whole subject is
    the gap between what code does and what somebody believed it did.
    """
    chunks = []
    for path in JS_FILES:
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        chunks.append(
            "\n".join(line for line in text.splitlines() if not line.strip().startswith("//"))
        )
    return "\n".join(chunks)


def keys_read():
    text = player_source()
    read = set()
    for binder in RESPONSE_BINDERS:
        # No `.` in the lookbehind: a binder is often reached through the player's
        # own state (`state.settings.x`, `ctx.settings.x`) and excluding those
        # drops real reads.
        read |= set(re.findall(rf"(?<![\w]){re.escape(binder)}\.([a-z][a-z0-9_]*)\b", text))
    # blocks.js reaches settings through a helper rather than a property chain.
    read |= set(re.findall(r'settingsValue\(ctx, "([a-z_]+)"', text))
    return read


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTheScanWorks(unittest.TestCase):
    """Every assertion below is a set difference, and a set difference against an
    empty set passes beautifully. These guard that."""

    def test_the_endpoints_are_found(self):
        endpoints = whitelisted_endpoints()
        self.assertGreaterEqual(len(endpoints), 10)
        for expected in ("get_learner_bootstrap", "open_checkpoint", "submit_quiz", "heartbeat"):
            self.assertIn(expected, endpoints)

    def test_the_delegated_keys_are_followed(self):
        """The whole point. `submit_quiz` returns `dict(grade_quiz(...))` plus its
        own additions, and a scan that stopped at the endpoint would see neither
        the grading keys nor the subscript ones."""
        keys = returned_keys("submit_quiz")
        self.assertIn("score", keys)  # from grading.grade_quiz
        self.assertIn("can_retry", keys)  # added by subscript in the endpoint
        self.assertIn("awarded", keys)  # from the per_question rows

    def test_the_envelope_builders_are_followed(self):
        self.assertIn("at_seconds", returned_keys("open_checkpoint"))
        self.assertIn("score", returned_keys("finish_attempt"))
        self.assertIn("message", returned_keys("get_learner_bootstrap"))

    def test_the_player_is_readable(self):
        self.assertGreater(len(player_source()), 20000)

    def test_both_sides_are_substantial(self):
        self.assertGreater(len(keys_sent()), 50)
        self.assertGreater(len(keys_read()), 50)


class TestNoKeyIsSentAndNeverRead(unittest.TestCase):
    def test_every_key_on_the_wire_has_a_reader(self):
        orphans = sorted(keys_sent() - keys_read() - set(SENT_BUT_NOT_READ))
        self.assertEqual(
            orphans,
            [],
            "These keys are sent by api/training.py and read by nothing in the "
            "player. Either wire them up or add them to SENT_BUT_NOT_READ with a "
            f"reason: {orphans}",
        )


class TestNoKeyIsReadAndNeverSent(unittest.TestCase):
    def test_every_key_the_player_reads_is_sent(self):
        orphans = sorted(keys_read() - keys_sent() - set(READ_BUT_NOT_SENT))
        self.assertEqual(
            orphans,
            [],
            "The player reads these off a server reply and the server never sends "
            "them, so each one is silently undefined. Either send them or add them "
            f"to READ_BUT_NOT_SENT with a reason: {orphans}",
        )


class TestTheAllowlistStaysHonest(unittest.TestCase):
    """An allowlist nobody prunes stops being a list of exceptions and becomes a
    list of things that used to be true."""

    def test_no_stale_sent_entries(self):
        stale = sorted(set(SENT_BUT_NOT_READ) - (keys_sent() - keys_read()))
        self.assertEqual(
            stale, [], f"SENT_BUT_NOT_READ excuses keys that are no longer asymmetric: {stale}"
        )

    def test_no_stale_read_entries(self):
        stale = sorted(set(READ_BUT_NOT_SENT) - (keys_read() - keys_sent()))
        self.assertEqual(
            stale, [], f"READ_BUT_NOT_SENT excuses keys that are no longer asymmetric: {stale}"
        )

    def test_every_exception_carries_a_reason(self):
        for name, allowlist in (("SENT_BUT_NOT_READ", SENT_BUT_NOT_READ), ("READ_BUT_NOT_SENT", READ_BUT_NOT_SENT)):
            for key, reason in allowlist.items():
                self.assertTrue(
                    reason and len(reason) > 15,
                    f"{name}[{key!r}] needs a real reason, not {reason!r}",
                )

    def test_the_two_lists_do_not_overlap(self):
        both = sorted(set(SENT_BUT_NOT_READ) & set(READ_BUT_NOT_SENT))
        self.assertEqual(both, [], f"a key cannot be both sent-only and read-only: {both}")


class TestTheSeamsThatBrokeStayPinned(unittest.TestCase):
    """Named individually. Each of these was a shipped defect, and a set
    comparison that happens to pass is worth less than one that says why."""

    def test_the_checkpoint_payload_speaks_the_doctype_names(self):
        keys = returned_keys("open_checkpoint")
        for field in ("checkpoint_key", "at_seconds", "question_text", "question_type"):
            self.assertIn(field, keys)

    def test_the_quiz_reply_carries_the_retry_decision(self):
        keys = returned_keys("submit_quiz")
        for field in ("can_retry", "attempts_left", "awarded"):
            self.assertIn(field, keys)

    def test_the_finished_attempt_always_carries_a_score(self):
        self.assertIn("score", returned_keys("finish_attempt"))

    def test_the_beat_answers_with_the_acknowledgement(self):
        self.assertIn("ack", returned_keys("heartbeat"))

    def test_the_beat_says_when_the_next_checkpoint_is_due(self):
        self.assertIn("next_checkpoint_at", returned_keys("heartbeat"))

    def test_the_boot_payload_carries_both_player_settings(self):
        keys = returned_keys("get_learner_bootstrap")
        self.assertIn("max_playback_rate", keys)
        self.assertIn("doc_min_dwell_seconds", keys)


if __name__ == "__main__":
    unittest.main()
