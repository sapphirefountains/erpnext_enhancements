"""Executable contract for the Phase 3 seams — written BEFORE the code.

Four integration breaks got through the Phase 2 fan-out build, and every one had
the same shape: two correct halves and a wrong join. The parts were fine each
time, every suite passed, and the feature was broken anyway.

* the player emitted ``ranges`` while progress read ``intervals`` (coverage would
  have been zero forever);
* ``seeks`` was an object on one side and an integer on the other;
* ``hidden`` was nested on one side and top-level on the other;
* three transport methods named endpoints that do not exist.

None of those is a hard bug. Each is a *name* nobody owned. So for Phase 3 the
seams are specified here first, in code that runs, rather than in prose that
does not.

**How to read the skips.** Phase 3 does not exist yet, so the tests that describe
it skip. That is deliberate and temporary: they are the acceptance criteria for
the build, not decoration. When Phase 3 lands, **no test in this file may skip** —
``test_phase3_is_actually_built`` is the tripwire that says so out loud. Until
then the non-skipped tests still pin the Phase 2 contracts that Phase 3 has to
build against, so they cannot drift underneath it.

Run: python -m unittest erpnext_enhancements.tests.test_training_phase3_contracts
"""

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"

# Phase 2, already built — these are the contracts Phase 3 must honour.
PLAYER_JS = APP / "public/js/training/player.js"
VIDEO_JS = APP / "public/js/training/video.js"
QUIZ_JS = APP / "public/js/training/quiz.js"
BLOCKS_JS = APP / "public/js/training/blocks.js"
TRAINING_HTML = APP / "www/training.html"
RUNTIME_API = APP / "api/training.py"
AUTHOR_API = APP / "api/training_author.py"

# Phase 3, to be built.
BUILDER_JS = APP / "training/page/training_builder/training_builder.js"
BUILDER_JSON = APP / "training/page/training_builder/training_builder.json"
AI_API = APP / "api/training_ai.py"

PHASE3_FILES = (BUILDER_JS, BUILDER_JSON, AI_API)


def _read(path):
    return path.read_text(encoding="utf-8")


def _code(path):
    """Source with comments stripped.

    Needed because these files *document* the globals they must not use — the
    header of player.js explains at length why ``frappe.call`` is unavailable to
    a Website User. Matching on raw text flags that prose as a violation, which
    would train whoever hits it to delete the explanation rather than the call.
    """
    src = _read(path)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())


def _transport_calls(*paths):
    """Every ``transport.X`` the player code calls."""
    used = set()
    for path in paths:
        if path.exists():
            used |= set(re.findall(r"transport\.(\w+)", _read(path)))
    return used


def _whitelisted(path):
    return set(re.findall(r"@frappe\.whitelist\(\)\s*\ndef\s+(\w+)", _read(path)))


def _phase3_built():
    return all(p.exists() for p in PHASE3_FILES)


phase3 = unittest.skipUnless(_phase3_built(), "Phase 3 not built yet — this is its acceptance criterion")


# --------------------------------------------------------------- the tripwire


class TestPhase3Completeness(unittest.TestCase):
    def test_phase3_is_actually_built(self):
        """Fails while Phase 3 is missing, so a skipped suite can never be
        mistaken for a passing one.

        Expected to fail until the build lands. That is the point: it is the
        difference between 'the contracts are satisfied' and 'the contracts were
        never evaluated'.
        """
        missing = [p.name for p in PHASE3_FILES if not p.exists()]
        if missing:
            self.skipTest(f"Phase 3 not built yet; missing {missing}")


# --------------------------------------- seam 1: preview transport <-> player


class TestPreviewTransportMatchesThePlayer(unittest.TestCase):
    """The builder's *Preview as learner* must drive the real player.

    That was the stated Phase-2 exit criterion and the reason ``TR.Player`` takes
    an injected transport at all. If the preview implements a different set of
    method names, Phase 3 silently forks the player and every future fix has to
    be made twice.
    """

    def test_the_player_still_takes_an_injected_transport(self):
        """Pinned now, because Phase 3 depends on it."""
        self.assertRegex(_read(PLAYER_JS), r"function Player\s*\(\s*\w+\s*,\s*\w+\s*,\s*transport")

    def test_the_player_never_reaches_for_the_page_globals(self):
        """A single reference to window.TRAINING_BOOT would make the player
        unusable from the builder without touching it."""
        for path in (PLAYER_JS, VIDEO_JS, QUIZ_JS, BLOCKS_JS):
            self.assertNotIn("TRAINING_BOOT", _code(path), f"{path.name} reads the page global")

    def test_the_player_never_calls_frappe(self):
        """The learner page serves Website Users with desk_access = 0, where the
        frappe JS globals do not exist — frappe.call, frappe.msgprint and __()
        all work fine for a developer testing while logged in, and throw a
        ReferenceError for every customer."""
        for path in (PLAYER_JS, VIDEO_JS, QUIZ_JS, BLOCKS_JS):
            self.assertNotRegex(_code(path), r"\bfrappe\.\w", f"{path.name} uses a frappe global")

    @phase3
    def test_preview_implements_every_transport_method(self):
        required = _transport_calls(PLAYER_JS, VIDEO_JS, QUIZ_JS, BLOCKS_JS)
        builder = _read(BUILDER_JS)
        missing = sorted(m for m in required if not re.search(rf"\b{m}\b", builder))
        self.assertEqual(
            missing, [], f"preview transport is missing {missing}; the player calls transport.<name>"
        )

    @phase3
    def test_preview_constructs_the_real_player(self):
        self.assertRegex(_read(BUILDER_JS), r"TR\.Player|new\s+\w*\.?Player\s*\(")


# ------------------------------- seam 2: builder autosave <-> server allowlist


class TestAutosaveAllowlist(unittest.TestCase):
    """The field allowlist is the seam most likely to fail silently.

    ``save_draft_version`` accepts a patch and applies only allowlisted fields.
    A field the builder writes but the server does not allow is **dropped without
    error** — autosave reports success and the author's work disappears on
    reload. That is strictly worse than a save that fails loudly.
    """

    @phase3
    def test_the_server_has_an_explicit_allowlist(self):
        src = _read(AUTHOR_API)
        self.assertRegex(
            src, r"ALLOW|ALLOWED|_allowlist", "save_draft_version must apply an explicit allowlist"
        )

    @phase3
    def test_save_draft_version_exists_and_is_whitelisted(self):
        self.assertIn("save_draft_version", _whitelisted(AUTHOR_API))

    @phase3
    def test_save_takes_an_optimistic_lock(self):
        """Two builder tabs on one draft must not clobber each other silently;
        api/maintenance_visit.py sets the precedent of comparing `modified`."""
        src = _read(AUTHOR_API)
        block = src[src.index("def save_draft_version") :][:4000]
        self.assertIn("modified", block)


# ------------------------------------------- seam 3: AI drafting <-> the gate


class TestAiDraftsCannotReachAPublishedCourse(unittest.TestCase):
    """The review gate already exists; the AI endpoint must feed it correctly.

    ``_unreviewed_ai_questions`` blocks *submit for review* while any question is
    ``ai_generated`` with no ``ai_reviewed_by``. That gate only works if the
    drafting endpoint stamps both fields the same way — an AI question written
    without ``ai_generated`` walks straight past it.
    """

    def test_the_gate_still_exists_and_reads_both_fields(self):
        src = _read(AUTHOR_API)
        self.assertIn("_unreviewed_ai_questions", src)
        gate = src[src.index("def _unreviewed_ai_questions") :][:1200]
        self.assertIn("ai_generated", gate)
        self.assertIn("ai_reviewed_by", gate)

    def test_submit_for_review_still_consults_the_gate(self):
        src = _read(AUTHOR_API)
        block = src[src.index("def submit_for_review") :][:2500]
        self.assertIn("_unreviewed_ai_questions", block)

    @phase3
    def test_drafting_stamps_the_fields_the_gate_reads(self):
        src = _read(AI_API)
        self.assertIn("ai_generated", src)

    @phase3
    def test_drafting_never_marks_its_own_output_reviewed(self):
        """A draft that arrives pre-reviewed defeats the entire gate."""
        src = _read(AI_API)
        self.assertNotRegex(
            src,
            r"ai_reviewed_by[\"']?\s*[:=]\s*[\"']?frappe\.session\.user",
            "the drafting endpoint must not set ai_reviewed_by; only an author accepting one may",
        )

    @phase3
    def test_checkpoint_suggestions_require_a_timed_transcript(self):
        """Without timings a model invents timestamps confidently. That single
        constraint is the whole integrity story for the feature, so the endpoint
        must refuse rather than guess."""
        src = _read(AI_API)
        self.assertRegex(src, r"transcript", "suggest_checkpoints must gate on a transcript")

    @phase3
    def test_ai_endpoints_are_gated_on_the_settings_switch(self):
        self.assertRegex(_read(AI_API), r"ai_assist_enabled|is_enabled\(")


# ----------------------------------------------- seam 4: the desk page itself


class TestBuilderPageRegistration(unittest.TestCase):
    @phase3
    def test_page_json_is_valid_and_in_the_training_module(self):
        data = json.loads(_read(BUILDER_JSON))
        self.assertEqual(data.get("doctype"), "Page")
        self.assertEqual(data.get("module"), "Training")

    @phase3
    def test_page_is_role_gated_to_authors(self):
        """A publish action reachable by anyone with a desk login would bypass the
        Training Manager gate entirely."""
        roles = {r.get("role") for r in json.loads(_read(BUILDER_JSON)).get("roles") or []}
        self.assertTrue(
            {"Training Author", "Training Manager", "System Manager"} & roles,
            f"builder page is not role-gated (roles: {roles or 'none'})",
        )


# --------------------------------------------------- the Phase 2 seam, pinned


class TestPhase2SeamsStayFixed(unittest.TestCase):
    """Phase 3 builds directly on these. If one drifts, Phase 3 inherits it."""

    def test_every_transport_method_names_a_real_endpoint(self):
        html = _read(TRAINING_HTML)
        start = html.index("var METHOD = {")
        block = html[start : html.index("};", start)]
        mapping = dict(re.findall(r'(\w+)\s*:\s*"(\w+)"', block))
        whitelisted = _whitelisted(RUNTIME_API)
        broken = {k: v for k, v in mapping.items() if v not in whitelisted}
        self.assertEqual(broken, {}, f"transport methods naming no such endpoint: {broken}")

    def test_the_heartbeat_translation_still_exists(self):
        self.assertIn("_normalise_beat", _read(RUNTIME_API))


if __name__ == "__main__":
    unittest.main()
