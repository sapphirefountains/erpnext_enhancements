"""The authoring entry path: can an author actually reach and use the builder?

Two failures here were reported by a real author on the first day they tried to
write a course, and neither is the kind a unit test of any single function would
have caught. Both are about a *route through the product* rather than a
computation:

1. **The Course form's "Open Builder" button was a placeholder that said the
   builder was not built yet.** It shipped in Phase 2, the builder landed in
   Phase 3, and the placeholder outlived it by three releases. Anyone who
   believed the button never found the builder at all.

2. **"This version is published and frozen" was shown when no version was
   loaded.** ``get_builder_bootstrap`` returns the open draft and nothing else,
   so ``version`` is null whenever a course has no draft — which is exactly where
   every course sits the moment it is published, because publishing consumes the
   draft. So the *normal* second edit of any course produced a message describing
   a state that did not exist, sending the author to look for a version switcher
   instead of telling them to raise a draft.

The tests are static reads of the JavaScript. That is the same approach as
``test_training_heartbeat_wire.py``, and it is used for the same reason: these
files never run in CI, there is no bench here, and a placeholder that outlives
its feature is a *text* problem — exactly what a text assertion is good at.

Run: python -m unittest erpnext_enhancements.tests.test_training_builder_entry
"""

import ast
import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
BUILDER = APP / "training/page/training_builder/training_builder.js"
COURSE_FORM = APP / "public/js/training/training_course.js"
TRAINING_JS = APP / "public/js/training"
TRAINING_PY = APP / "training"

# Phases 1-4 are all built and merged. Any promise that something "arrives in a
# later release" is now a statement about the product that is simply false, and
# the one that survived cost an author their first afternoon.
BROKEN_PROMISES = (
    "arrives in a later release",
    "is a later release",
    "coming soon",
    "not yet implemented",
    "will be available in a future",
)


def _builder_raw():
    return BUILDER.read_text(encoding="utf-8")


def _course_form_raw():
    return COURSE_FORM.read_text(encoding="utf-8")


def _builder():
    """The builder **with comments stripped**, and that is the important part.

    Every assertion in this module is a claim about code. Three of them passed
    their mutation on the first run purely because the comment explaining a bug
    quotes the bug: the comment above `load_player` contains the literal
    `?v=1.218.0`, so a test asserting the bust token was still applied stayed
    green after the token was deleted from the code. Prose about code is not code.
    """
    return _code(_builder_raw())


def _course_form():
    return _code(_course_form_raw())


def _code(src):
    """`src` with comments removed.

    Every "this string must NOT appear" assertion has to run on code alone. Both
    of the negative assertions below failed on their first run against the fixed
    tree — not because the bug was back, but because the comment *explaining* the
    bug quotes it verbatim. That is the same trap that let a `update_modified=False`
    assertion pass its mutation in `test_training_runtime_regressions`: prose about
    code is not code, and a plain substring search cannot tell them apart.
    """
    out = []
    in_block = False
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


def _method(src, name):
    """One method body out of the builder class, by brace matching."""
    start = re.search(rf"^\t{re.escape(name)}\(", src, re.M)
    if not start:
        return ""
    i = src.index("{", start.start())
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i : j + 1]
    return src[i:]


class TestOpenBuilderActuallyOpensTheBuilder(unittest.TestCase):
    def test_the_button_routes_to_the_page(self):
        src = _course_form()
        self.assertIn("training-builder", src, "Open Builder does not route to the builder page")
        self.assertIn("frappe.set_route", src)

    def test_it_passes_the_course_through(self):
        """The page reads `course` from the query string, then route_options. A
        route with neither lands on the course picker, which is not what pressing
        the button on a specific course should do."""
        self.assertIn("frappe.route_options", _course_form())

    def test_the_builder_still_reads_route_options(self):
        """The other half of the same contract — asserted here so the two cannot
        drift apart silently."""
        self.assertIn("frappe.route_options.course", _builder())

    def test_the_button_is_not_a_placeholder_dialog(self):
        """The bug itself: a button whose entire body was a msgprint saying the
        thing it names does not exist."""
        src = _course_form_raw()
        self.assertNotIn("Not yet", src)
        for promise in BROKEN_PROMISES:
            self.assertNotIn(promise, src, f"stale promise on the Course form: {promise!r}")


class TestNoStalePlaceholdersInTraining(unittest.TestCase):
    """Sweeps the module rather than the one file, because the placeholder that
    caused this was found by an author, not by a reader — and there is no reason
    to think it was the only one."""

    def _sources(self):
        files = list(TRAINING_JS.glob("*.js"))
        files += list(TRAINING_PY.rglob("*.js"))
        files += list(TRAINING_PY.rglob("*.py"))
        return files

    def test_the_sweep_sees_files(self):
        """Guards every assertion below from passing because a glob went stale."""
        self.assertGreater(len(self._sources()), 10)

    def test_nothing_promises_a_feature_that_already_shipped(self):
        offenders = []
        for path in self._sources():
            text = path.read_text(encoding="utf-8", errors="replace")
            for promise in BROKEN_PROMISES:
                if promise in text:
                    offenders.append(f"{path.relative_to(APP)}: {promise!r}")
        self.assertEqual(offenders, [], "stale 'not built yet' text: " + "; ".join(offenders))


class TestTheGuardTellsTheTruth(unittest.TestCase):
    """A refusal has to name the state the author is actually in."""

    def test_the_guard_distinguishes_no_draft_from_frozen(self):
        body = _method(_builder(), "guard_editable")
        self.assertTrue(body, "guard_editable not found")
        self.assertIn("this.version", body, "the guard does not branch on whether a version is loaded")
        self.assertIn("No open draft", body)

    def test_the_frozen_message_is_only_used_when_a_version_is_loaded(self):
        """The bug: `editable()` is false both for a published version and for no
        version at all, and one sentence was used for both."""
        body = _method(_builder(), "guard_editable")
        frozen = body.index("Published version")
        no_draft = body.index("No open draft")
        # Whichever order they appear in, they must be in different branches.
        self.assertNotEqual(frozen, no_draft)
        self.assertIn("if (this.version) {", body, "the two messages are not branched")

    def test_the_guard_offers_the_way_out(self):
        """Describing the fix is not the same as offering it. `New Draft Version`
        was only reachable from the page's overflow menu and from an empty state
        in a pane the author was not necessarily looking at."""
        body = _method(_builder(), "guard_editable")
        self.assertIn("new_draft_version", body)
        # Both branches, not just one. The first version of this assertion only
        # required the string to appear somewhere in the method, so a mutation
        # that stripped the action from the *no-draft* branch alone — which is
        # exactly the reported bug — left the test green.
        self.assertEqual(
            body.count("primary_action"),
            2,
            "both the frozen-version and no-draft branches must offer the way out",
        )

    def test_new_draft_version_works_without_a_loaded_version(self):
        """It must gate on the course, not the version — it is precisely the
        action for when there is no version."""
        body = _method(_builder(), "new_draft_version")
        self.assertIn("if (!this.course) return;", body)
        self.assertNotIn("if (!this.version) return;", body)


class TestDeadControlsAreDisabled(unittest.TestCase):
    """Offering a control and then refusing it is worse than not offering it.

    `render_no_draft` empties the pane *body*; the add-lesson button lives in the
    pane *head*, so it stayed available on a course with no draft. Clicking it is
    how the author met the guard in the first place.
    """

    def test_affordances_are_repainted_on_render(self):
        self.assertIn("this.paint_edit_affordances()", _method(_builder(), "render"))

    def test_add_lesson_is_disabled_when_not_editable(self):
        body = _method(_builder(), "paint_edit_affordances")
        self.assertTrue(body, "paint_edit_affordances not found")
        self.assertIn("add-lesson", body)
        self.assertIn("this.editable()", body)
        self.assertIn('prop("disabled"', body)

    def test_the_disabled_button_says_why(self):
        """A greyed-out button with no explanation is its own dead end."""
        body = _method(_builder(), "paint_edit_affordances")
        self.assertIn('attr(', body)
        self.assertIn("title", body)


class TestChangeTypeMatchesTheDocType(unittest.TestCase):
    """The builder could never publish, and this is why.

    ``change_type`` is a Select whose stored values carry a parenthetical:
    ``Minor Edit (keep completions)``. The builder's two dialogs offered the bare
    ``Minor Edit`` / ``Material Change``, which ``publish_version`` rejects
    outright. The Course form had the full strings all along, so the same flow
    worked from the other entry point — which is exactly why nobody noticed.

    Cross-checked against the DocType JSON rather than a hardcoded pair, so this
    keeps working if the wording is ever changed.
    """

    def _declared(self):
        path = (
            APP
            / "training/doctype/training_course_version/training_course_version.json"
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        for field in data["fields"]:
            if field["fieldname"] == "change_type":
                return [o for o in (field.get("options") or "").split("\n") if o.strip()]
        return []

    def test_the_doctype_still_declares_two_options(self):
        """Guards the assertions below from passing vacuously."""
        self.assertEqual(len(self._declared()), 2)

    def test_the_python_constants_match_the_doctype(self):
        src = (
            APP / "training/doctype/training_course_version/training_course_version.py"
        ).read_text(encoding="utf-8")
        for option in self._declared():
            self.assertIn(f'"{option}"', src)

    def test_the_builder_offers_exactly_the_declared_options(self):
        src = _builder()
        for option in self._declared():
            self.assertIn(f'"{option}"', src, f"the builder never names {option!r}")

    def test_the_builder_does_not_send_a_truncated_value(self):
        """The bug itself. A truncated value is not merely unsaved — publish_version
        throws, so the button appears simply not to work."""
        code = _builder()
        for bad in ('"Minor Edit"', '"Material Change"'):
            self.assertNotIn(bad, code, f"builder still sends the truncated {bad}")

    def test_the_course_form_agrees(self):
        src = _course_form()
        for option in self._declared():
            self.assertIn(f'"{option}"', src)


class TestDeletedLessonsLeaveTheClient(unittest.TestCase):
    """The worst of the set: one delete could poison a whole session.

    Deleting the lesson you are looking at left it selected, painted and editable.
    The next keystroke queued a patch against a name the autosave was about to
    delete; the server 404s and rolls back the *entire* payload, the client
    re-merges the doomed patch, and it retries every four seconds forever — taking
    every other lesson's edits with it. Reloading was the only escape and it threw
    away everything since the last good save.
    """

    def test_deleting_the_selected_lesson_moves_the_selection(self):
        body = _method(_builder(), "remove_lesson")
        self.assertIn("this.lesson_name === lesson.name", body)

    def test_it_repaints_more_than_the_rail(self):
        """`render_outline()` alone greys the rail row and leaves the canvas and
        inspector showing the doomed lesson, still enabled."""
        body = _method(_builder(), "remove_lesson")
        self.assertIn("this.render()", body)
        self.assertNotIn("this.render_outline();", body)

    def test_a_confirmed_delete_is_forgotten(self):
        body = _method(_builder(), "forget_deleted")
        self.assertTrue(body, "forget_deleted not found")
        for expected in ("this.lessons", "removed_lessons", "dirty.lessons"):
            self.assertIn(expected, body)

    def test_the_save_response_is_actually_read(self):
        """`state.deleted` was returned by the server from the first day and read
        by nobody.

        Both the guard and the call are asserted. Checking only that the string
        appears somewhere passed a mutation that turned the condition into
        `if (false)` — the call survived inside the dead branch.
        """
        body = _method(_builder(), "flush_save")
        self.assertIn("state.deleted && state.deleted.length", body)
        self.assertIn("this.forget_deleted(state.deleted)", body)


class TestSaveChainingIsHonest(unittest.TestCase):
    """Two bugs in one promise.

    ``flush_save()`` returned ``Promise.resolve()`` when a save was already in
    flight — indistinguishable, to a caller, from "everything is stored". Publish
    chains off it, so publishing within a few seconds of typing froze a version
    missing the author's last words, and a published version cannot be edited.

    Separately, when the flush *failed*, the chained action was correctly
    abandoned but nothing said so: the dialog had closed and the only thing on
    screen was an error about the autosave.
    """

    def test_a_queued_flush_returns_the_real_chain(self):
        body = _method(_builder(), "flush_save")
        self.assertIn("return this._inflight", body)
        self.assertNotIn("this._pending_flush = true;\n\t\t\treturn Promise.resolve();", body)

    def test_the_followup_is_chained_not_fired_and_forgotten(self):
        self.assertIn("return this.flush_save().then(() => state);", _method(_builder(), "flush_save"))

    def test_abandoned_actions_are_named(self):
        body = _method(_builder(), "save_then")
        self.assertTrue(body, "save_then not found")
        self.assertIn("msgprint", body)

    def test_every_chained_action_goes_through_it(self):
        """Publish is the one that matters — its failure mode is unrecoverable."""
        for name in ("publish", "submit_for_review", "add_lesson"):
            self.assertIn("this.save_then(", _method(_builder(), name), f"{name} bypasses save_then")


class TestPreviewCanActuallyLoad(unittest.TestCase):
    """Preview failed 100% of the time.

    ``frappe.assets.extn()`` derives an asset's type by splitting the URL on "?"
    and taking the last segment, so ``player.js?v=1.218.0`` reports its extension
    as ``0`` and matches neither the js branch nor the css branch. The file was
    silently never loaded. Dropping the token is not the fix: raw ``/assets`` are
    served immutable for a year, and this repo has shipped that cache bug twice.
    """

    def test_it_does_not_hand_a_busted_url_to_frappe_require(self):
        self.assertNotIn("frappe.require", _method(_builder(), "load_player"))

    def test_the_bust_token_is_still_applied(self):
        self.assertIn("?v=", _method(_builder(), "load_player"))

    def test_scripts_keep_their_execution_order(self):
        """player.js expects blocks, video and quiz to have registered on
        window.TR first, so insertion order has to be execution order."""
        src = _builder()
        self.assertIn("async = false", src)

    def test_a_failed_load_is_reported(self):
        body = _method(_builder(), "load_player")
        self.assertIn("catch", body)


class TestNoDraftControlsExplainThemselves(unittest.TestCase):
    def test_publish_and_review_do_not_silently_no_op(self):
        for name in ("publish", "submit_for_review"):
            body = _method(_builder(), name)
            self.assertIn(
                "return this.guard_editable();",
                body,
                f"{name} still returns silently with no draft loaded",
            )

    def test_preview_is_disabled_with_nothing_to_preview(self):
        body = _method(_builder(), "paint_edit_affordances")
        self.assertIn('data-act="preview"', body)


class TestChaptersCanBeManaged(unittest.TestCase):
    """The builder could never create, rename or delete a chapter.

    Every other half of this feature was already built: ``save_draft_version``
    accepts a ``chapters`` array, ``_apply_chapters`` replaces the table in order
    and refuses to orphan a lesson, ``TrainingCourseVersion._assign_chapter_keys``
    mints the keys, and the outline groups lessons by chapter. The client simply
    never assigned ``this.dirty.chapters`` — it was read in two places and written
    in none. On a new course, whose first draft clones nothing, the chapter list
    was permanently empty and every lesson sat under "Unfiled" with no way out.
    """

    def _author(self):
        return (APP / "api/training_author.py").read_text(encoding="utf-8")

    def test_it_is_reachable(self):
        self.assertIn("this.manage_chapters()", _builder())

    def test_it_writes_the_dirty_slot_the_save_reads(self):
        """The whole bug: `dirty.chapters` was read by flush_save and remerge, and
        assigned by nothing."""
        body = _method(_builder(), "manage_chapters")
        self.assertTrue(body, "manage_chapters not found")
        self.assertIn("this.dirty.chapters =", body)
        self.assertIn("this.schedule_save()", body)

    def test_it_refuses_on_a_published_version(self):
        self.assertIn("this.guard_editable()", _method(_builder(), "manage_chapters"))

    def test_existing_keys_are_carried_through_untouched(self):
        """`chapter_key` is what every lesson points at. Regenerating one silently
        unfiles every lesson in that chapter."""
        body = _method(_builder(), "manage_chapters")
        self.assertIn("chapter_key: row.chapter_key", body)

    def test_a_new_chapter_sends_no_key(self):
        """The server mints it and returns it. A client-invented key is how a key
        ends up disagreeing with the row it names."""
        body = _method(_builder(), "manage_chapters")
        push = body[body.index("rows.push(") :][:120]
        self.assertNotIn("chapter_key", push)

    def test_it_checks_for_orphaned_lessons_before_saving(self):
        """The server refuses this too — but on the *next autosave*, seconds later,
        with the dialog closed and the message attached to a save the author did
        not knowingly trigger."""
        body = _method(_builder(), "manage_chapters")
        self.assertIn("this.chapters_in_use(", body)
        helper = _method(_builder(), "chapters_in_use")
        self.assertTrue(helper, "chapters_in_use not found")
        self.assertIn("chapter_key", helper)

    def test_reordering_is_not_drag_only(self):
        """A drag handle cannot be operated from a keyboard, and reordering is the
        main reason this dialog exists."""
        body = _method(_builder(), "manage_chapters")
        for act in ('data-act="up"', 'data-act="down"'):
            self.assertIn(act, body)

    def test_the_client_sends_only_fields_the_server_accepts(self):
        """Cross-checked against CHAPTER_ALLOWED_FIELDS rather than assumed. A
        field outside it is not saved — it comes back in `rejected`, which the
        builder reports, but silently doing nothing is the failure mode this whole
        module keeps finding."""
        allowed = re.search(
            r"CHAPTER_ALLOWED_FIELDS = frozenset\(\{([^}]*)\}\)", self._author()
        )
        self.assertIsNotNone(allowed, "CHAPTER_ALLOWED_FIELDS not found")
        declared = set(re.findall(r'"(\w+)"', allowed.group(1)))

        body = _method(_builder(), "manage_chapters")
        rows = body[body.index("const rows =") : body.index("const dialog")]
        # Not line-anchored. The anchored version missed a field appended to an
        # existing line, which is exactly how a stray key gets added in practice.
        sent = set(re.findall(r"(\w+)\s*:", rows))
        # chapter_key is echoed back, not written — it is in CHAPTER_ECHOED_KEYS.
        unknown = sorted(sent - declared - {"chapter_key"})
        self.assertEqual(
            unknown, [], f"the chapter dialog sends {unknown}, which the server will reject"
        )

    def test_every_allowed_field_that_matters_is_editable(self):
        """`chapter_title` is required by the DocType — a dialog that cannot set it
        can only create rows that fail to save."""
        self.assertIn("chapter_title", _method(_builder(), "manage_chapters"))


if __name__ == "__main__":
    unittest.main()


class TestVideoCanBeAuthored(unittest.TestCase):
    """The two gaps that made a video impossible to add and impossible to debug.

    The builder's only video control was a Link to an **already registered**
    Training Video Asset, so there was no way to register one from the builder at
    all — the author was told to go and do it, with no door. The Desk form is the
    wrong door anyway: it lets a duration be typed while `duration_source` still
    reads `Probed`, and that is the one combination that makes `evaluate_gates`
    score the coverage gate against a number nobody checked. Registering through
    `register_video_asset` probes the real length and says so.

    And a failed copy was invisible. The asset carried `status = Error` and a
    traceback; the builder said only "This video asset is Error", so a broken
    video and one that had simply not finished copying looked identical.
    """

    def _author(self):
        return (APP / "api/training_author.py").read_text(encoding="utf-8")

    def test_the_builder_can_register_a_video(self):
        """Asserts the button is WIRED, not merely that the method exists.

        Checking for the name alone passed a mutation that unhooked the click
        handler and left `register_drive_video` sitting there complete and
        unreachable — the same shape as the `finishCourse` miss.
        """
        code = _builder()
        self.assertIn("training_author.register_video_asset", code)
        self.assertIn("this.register_drive_video(", _method(code, "render_video_controls"))

    def test_it_reports_whether_the_length_was_really_probed(self):
        """`duration_probed: false` means the service account could not read the
        file — the length is a placeholder and the gate will be waived. Saying
        "registered" and nothing else would hide exactly that."""
        body = _method(_builder(), "register_drive_video")
        self.assertIn("duration_probed", body)

    def test_a_failed_copy_is_shown_with_a_way_out(self):
        body = _method(_builder(), "render_asset_state")
        self.assertTrue(body, "render_asset_state not found")
        self.assertIn("last_error", body)
        self.assertIn("retry_video_copy", body)

    def test_the_404_is_translated(self):
        """Drive answers 404 rather than 403 for a file the service account cannot
        see, so the raw error points away from the actual cause.

        Asserts the *explanation*, not the digits. Checking for "404" alone passed
        a mutation that disabled the branch, because the regex that detects it
        still contained the number.
        """
        body = _method(_builder(), "render_asset_state")
        self.assertIn("not shared", body)
        self.assertIn("service account", body)
        # The detector has to be computed AND used as the condition. Asserting the
        # explanation alone passed a mutation that replaced the condition with
        # `false`: the sentence was still in the file and could never be shown,
        # which is the same "complete and unreachable" shape as the finishCourse
        # miss and the register-from-Drive one.
        self.assertGreaterEqual(
            body.count("notFound"), 2, "the 404 detector is computed but never used"
        )

    def test_an_unverified_duration_is_called_out(self):
        """The quiet one: a `Manual` duration means the coverage gate is waived,
        and an author who thinks they set an 80% gate should know it is not being
        applied."""
        body = _method(_builder(), "render_asset_state")
        self.assertIn("duration_source", body)

    def test_the_state_panel_is_rendered(self):
        self.assertIn("this.render_asset_state(", _method(_builder(), "render_video_controls"))

    def test_the_retry_endpoint_exists_and_is_gated(self):
        source = self._author()
        self.assertIn("def retry_video_copy(", source)
        start = source.index("def retry_video_copy(")
        body = source[start : source.index("\ndef ", start + 5)]
        self.assertIn("_require_author()", body)
        self.assertIn("copy_from_drive", body)

    def test_the_picker_carries_what_the_panel_needs(self):
        """Cross-checked against what the server actually RETURNS.

        The first version searched the function body, so a field still named in
        the ``fields=[...]`` it selects from the database satisfied the check even
        after it had been dropped from the returned dict. Two mutations survived
        exactly that way. The keys of the returned literal are the contract; the
        SQL column list is not.
        """
        tree = ast.parse(self._author())
        sent = set()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.FunctionDef) and node.name == "_builder_video_assets"):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Dict):
                    sent |= {
                        key.value
                        for key in inner.keys
                        if isinstance(key, ast.Constant) and isinstance(key.value, str)
                    }
        self.assertTrue(sent, "could not parse what _builder_video_assets returns")
        for field in ("duration_source", "last_error", "copied"):
            self.assertIn(field, sent, f"_builder_video_assets does not return {field}")

    def test_a_pasted_folder_or_drive_link_is_refused(self):
        """Three ways to copy the wrong id, and all three fail identically an hour
        later with 'file not found'. Two are detectable up front."""
        source = self._author()
        self.assertIn("def drive_file_id_from(", source)
        start = source.index("def drive_file_id_from(")
        body = source[start : source.index("\ndef ", start + 5)]
        self.assertIn("/folders/", body)
        self.assertIn('startswith("0A")', body)

    def test_register_accepts_a_pasted_url(self):
        source = self._author()
        start = source.index("def register_video_asset(")
        body = source[start : source.index("\ndef ", start + 5)]
        self.assertIn("drive_file_id_from(", body)
