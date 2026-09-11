"""The team feed, and the one rule it exists to obey.

    **The feed never carries a number somebody could be judged by.**

Not scores, not attempt counts, not coverage, not who is behind. An achievement is
"X finished Y" and nothing else. That is not squeamishness: in a sixteen-person
company where everybody knows everybody, a feed that publishes how many goes at it
somebody needed is a feed that makes people wait until they are sure before they
start — the opposite of what a training system is for.

**Why there is a separate record at all.** The obvious design is to let people
react to a `Training Completion`. That is the compliance artefact — its own
controller calls it "the only record in the module that anybody outside it will
ever be asked to produce: to a client, to an insurer" — and it carries
`score_percent`, `video_coverage_percent`, `attempt`, `status` and
`revoked_reason`. Both plausible reuse routes for reactions begin with a read check
on the referenced document (`api/comments.py`, and frappe's own `desk/like.py`), so
"let colleagues react to a completion" reduces exactly to "publish second-attempt
scores and revocations company-wide".

So `Training Achievement` is a separate, deliberately public row with a snapshot
title and no numbers on it at all. There is no field a future change could widen
into performance data, because there is no field that holds any.

Two consequences follow, and both are the point: a **failed** attempt never mints
one, so the feed cannot become a record of who struggled; and a **revoked**
completion has its achievement deleted, so the feed stops congratulating somebody
for a certification that has since been pulled — while the completion, which is
evidence, stays exactly where it is.

Run: python -m unittest erpnext_enhancements.tests.test_training_social
"""

import ast
import json
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SOCIAL = APP / "training/social.py"
ACHIEVEMENT_JSON = APP / "training/doctype/training_achievement/training_achievement.json"
ACHIEVEMENT_PY = APP / "training/doctype/training_achievement/training_achievement.py"
KUDOS_JSON = APP / "training/doctype/training_kudos/training_kudos.json"
KUDOS_PY = APP / "training/doctype/training_kudos/training_kudos.py"
PREF_JSON = APP / "training/doctype/training_profile_preference/training_profile_preference.json"
CERTIFICATES = APP / "training/certificates.py"
PERMISSIONS = APP / "training/permissions.py"
HOOKS = APP / "hooks.py"
RUNTIME = APP / "api/training.py"
PLAYER = APP / "public/js/training/player.js"
PAGE = APP / "www/training.html"
COMPLETION_JSON = APP / "training/doctype/training_completion/training_completion.json"


def _text(path):
    return path.read_text(encoding="utf-8")


def _code(text):
    """JS with `//` comments stripped — an absence assertion must not read the
    comment explaining the absence."""
    return re.sub(r"//.*$", "", text, flags=re.M)


def _fields(path):
    return {f["fieldname"] for f in json.loads(_text(path))["fields"]}


def _body(node, lines):
    """A function's source with its docstring dropped.

    Dropped because these docstrings quote the tokens being asserted absent --
    `get_feed` explains that the customer feed is empty by naming `Customer`, and a
    raw scan reads the explanation as a filter.
    """
    stmts = node.body
    if (
        stmts
        and isinstance(stmts[0], ast.Expr)
        and isinstance(stmts[0].value, ast.Constant)
        and isinstance(stmts[0].value.value, str)
    ):
        stmts = stmts[1:]
    return "\n".join(lines[stmts[0].lineno - 1 : node.end_lineno]) if stmts else ""


def _fn(name, path=SOCIAL):
    """A top-level function, or a method of any class in the file."""
    src = _text(path)
    lines = src.splitlines()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return _body(node, lines)
    raise AssertionError(f"{name} not found in {path.name}")


class TestTheFeedCarriesNoNumbers(unittest.TestCase):
    def test_the_achievement_has_no_performance_fields(self):
        """The structural guarantee. There is nothing here to widen."""
        fields = _fields(ACHIEVEMENT_JSON)
        for forbidden in (
            "score",
            "score_percent",
            "attempt",
            "video_coverage_percent",
            "attempts",
            "passed",
        ):
            with self.subTest(field=forbidden):
                self.assertNotIn(forbidden, fields)

    def test_the_completion_it_points_at_does_have_them(self):
        """The reason the split exists. If this ever stops being true, the argument
        for a separate record weakens and should be re-examined rather than
        forgotten."""
        fields = _fields(COMPLETION_JSON)
        self.assertIn("score_percent", fields)
        self.assertIn("attempt", fields)

    def test_nothing_from_the_completion_is_served(self):
        """`source_completion` is a back-reference for auditors, not a join the feed
        reads through."""
        body = _fn("_rows")
        self.assertNotIn("source_completion", body)
        self.assertNotIn("Training Completion", body)

    def test_the_title_is_a_snapshot(self):
        """The record must still read correctly after the course it names is
        renamed or retired."""
        self.assertIn("title", _fields(ACHIEVEMENT_JSON))
        self.assertIn("course_title_snapshot", _fn("on_completion"))

    def test_the_feed_item_shows_no_number(self):
        player = _code(_text(PLAYER))
        at = player.index("function feedHeadline(")
        body = player[at : player.index("function kudosControls(")]
        for forbidden in ("score", "pct(", "attempt"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, body)


class TestTheAudienceIsMandatory(unittest.TestCase):
    """Copied verbatim from `gamification._stat_rows`, including its reason: an
    optional privacy filter is a privacy filter somebody eventually leaves out."""

    def test_the_reader_refuses_an_unknown_audience(self):
        body = _fn("_rows")
        self.assertIn("raise ValueError", body)

    def test_the_customer_feed_is_empty_by_construction(self):
        """Not by a filter in the endpoint — a guard there would be a second place
        for the rule to live, and the second place is the one that gets forgotten."""
        body = _fn("_rows")
        self.assertIn("if learner_type == CUSTOMER:", body)
        self.assertIn("return []", body)

    def test_learner_type_is_derived_not_accepted(self):
        """A role can be granted by accident — every customer contact holds
        Training Learner. Whether somebody works here cannot be."""
        controller = _text(ACHIEVEMENT_PY)
        self.assertIn("_resolve_learner", controller)
        self.assertIn('frappe.db.get_value(\n\t\t\t"Employee"', controller)
        field = next(
            f for f in json.loads(_text(ACHIEVEMENT_JSON))["fields"] if f["fieldname"] == "learner_type"
        )
        self.assertEqual(field.get("read_only"), 1)

    def test_the_endpoint_does_not_re_filter(self):
        body = _fn("get_feed", RUNTIME)
        self.assertIn("social.feed(", body)
        self.assertNotIn("Customer", body)


class TestWithdrawalPropagates(unittest.TestCase):
    def test_a_revoked_completion_drops_its_achievement(self):
        self.assertIn("withdraw_for_completion", _text(CERTIFICATES))

    def test_it_is_deleted_not_tombstoned(self):
        """A tombstone would publish the withdrawal, which is the one part of this
        that is genuinely nobody else's business."""
        body = _fn("withdraw_for_completion")
        self.assertIn("frappe.delete_doc", body)

    def test_the_completion_itself_is_untouched(self):
        body = _fn("withdraw_for_completion")
        self.assertNotIn("Training Completion", body)

    def test_minting_joins_the_existing_fan_out_and_is_caught(self):
        """`after_completion` already fires the certificate, the badges and the pass
        email, each inside its own try. A feed row failing must never cost somebody
        their certificate."""
        src = _text(CERTIFICATES)
        at = src.index("social.on_completion")
        window = src[max(0, at - 700) : at + 400]
        self.assertIn("try:", window)
        self.assertIn("log_error", window)

    def test_record_cannot_raise(self):
        body = _fn("record")
        self.assertIn("except Exception:", body)
        self.assertIn("log_error", body)

    def test_minting_is_idempotent(self):
        """A re-run of the backfill, or a completion submitted twice, must not
        double the feed."""
        self.assertIn("frappe.db.exists(ACHIEVEMENT, filters)", _fn("record"))


class TestKudosAreOnePerPerson(unittest.TestCase):
    def test_re_sending_edits_rather_than_stacking(self):
        """A second kudos from the same person is them changing their mind or adding
        a sentence, not a second cheer. A feed where one enthusiastic colleague
        appears five times under one item reads as noise."""
        body = _fn("add_kudos")
        self.assertIn('frappe.db.exists(KUDOS, {"achievement": achievement, "from_user": user})', body)

    def test_you_cannot_congratulate_yourself(self):
        self.assertIn("_reject_self_kudos", _text(KUDOS_PY))

    def test_the_sender_is_the_session_user(self):
        """Accepting it would let one person post praise under another's name — a
        small thing that would feel very bad."""
        self.assertIn("frappe.session.user", _fn("_resolve_sender", KUDOS_PY))

    def test_the_reactions_are_words_not_emoji(self):
        """A thumbs-up is encouragement to one person and sarcasm to another;
        "Nice work" cannot be."""
        field = next(
            f for f in json.loads(_text(KUDOS_JSON))["fields"] if f["fieldname"] == "reaction"
        )
        options = [o for o in field["options"].split("\n") if o]
        self.assertIn("Nice work", options)
        for option in options:
            with self.subTest(option=option):
                self.assertTrue(option.isascii(), f"{option!r} is not plain words")

    def test_the_player_offers_exactly_those_reactions(self):
        field = next(
            f for f in json.loads(_text(KUDOS_JSON))["fields"] if f["fieldname"] == "reaction"
        )
        declared = [o for o in field["options"].split("\n") if o]
        player = _text(PLAYER)
        at = player.index("var REACTIONS = [")
        offered = re.findall(r'"([^"]+)"', player[at : player.index("];", at)])
        self.assertEqual(offered, declared)

    def test_a_note_is_truncated_not_refused(self):
        """Somebody typing a long note on a phone should not lose it to a validation
        error at the end."""
        self.assertIn("[:MAX_NOTE]", _text(KUDOS_PY))

    def test_it_is_not_frappes_comment_doctype(self):
        """`public/js/comments.js` is Vue and needs the desk bundle, which
        www/training.py forbids; and the player is contractually innerHTML-free
        while `Comment.content` is HTML-editor output."""
        self.assertNotIn('"Comment"', _text(SOCIAL))


class TestTheOptOutIsReal(unittest.TestCase):
    def test_preferences_are_not_on_the_disposable_stat_row(self):
        """`Training Learner Stat` says of itself that nothing in it is evidence and
        it can be thrown away and rebuilt — `rebuild_learner_stat` does exactly
        that. A preference stored there would be silently reset by a routine
        recalculation, and the person would find out by being back on a leaderboard
        they had left."""
        stat = _fields(APP / "training/doctype/training_learner_stat/training_learner_stat.json")
        self.assertNotIn("show_on_feed", stat)
        self.assertNotIn("show_on_leaderboard", stat)
        self.assertIn("show_on_feed", _fields(PREF_JSON))

    def test_both_settings_default_to_on(self):
        """An opt-in feed in a sixteen-person company is an empty feed, and empty
        reads as broken rather than as private."""
        for name in ("show_on_feed", "show_on_leaderboard"):
            with self.subTest(field=name):
                field = next(
                    f for f in json.loads(_text(PREF_JSON))["fields"] if f["fieldname"] == name
                )
                self.assertEqual(field.get("default"), "1")

    def test_absence_means_the_default(self):
        self.assertIn("True if row is None else", _fn("get_preferences"))

    def test_opting_out_re_sweeps_what_is_already_posted(self):
        """An opt-out that only applied to the future would leave everything already
        up there, which is not what anybody means by it."""
        self.assertIn("resweep_visibility(user)", _fn("set_preferences"))

    def test_the_control_is_on_the_feed_itself(self):
        """Somebody uncomfortable being on the feed is uncomfortable while looking at
        it, and that is when the control needs to be to hand -- not in a settings
        page nobody opens."""
        player = _text(PLAYER)
        at = player.index("function renderFeed(")
        self.assertIn("renderFeedPrefs", player[at : at + 1200])

    def test_the_preference_endpoints_have_callers(self):
        """This module has shipped complete-but-uncalled features twice, and the
        boot-wire suite caught these two mapped-but-never-called before they
        shipped a third time."""
        player = _text(PLAYER)
        self.assertIn('call("feedPrefs"', player)
        self.assertIn('call("setFeedPrefs"', player)


class TestItIsScoped(unittest.TestCase):
    """All three doctypes grant `Training Learner` read, and customer Website Users
    hold that role. DocPerms with no scoping hook is the one combination that
    leaks."""

    LEAKY = ("Training Achievement", "Training Kudos", "Training Profile Preference")

    def _hooks_dict(self, name):
        for node in ast.parse(_text(HOOKS)).body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets
            ):
                return ast.literal_eval(node.value)
        raise AssertionError(name)

    def test_all_three_have_a_query_condition(self):
        registered = self._hooks_dict("permission_query_conditions")
        for doctype in self.LEAKY:
            with self.subTest(doctype=doctype):
                self.assertIn(doctype, registered)

    def test_all_three_have_the_single_document_twin(self):
        registered = self._hooks_dict("has_permission")
        for doctype in self.LEAKY:
            with self.subTest(doctype=doctype):
                self.assertIn(doctype, registered)

    def test_the_achievement_filter_checks_audience_and_visibility(self):
        body = _fn("achievement_query_conditions", PERMISSIONS)
        self.assertIn("learner_type", body)
        self.assertIn("visibility", body)

    def test_your_own_rows_survive_your_own_opt_out(self):
        """An opt-out hides you from other people, not from yourself."""
        self.assertIn("`user` = {own}", _fn("achievement_query_conditions", PERMISSIONS))

    def test_kudos_are_scoped_by_their_parent(self):
        """A reaction is a public act on a public row. Scoping it to its sender
        would mean seeing an item and not the congratulations under it."""
        body = _fn("kudos_query_conditions", PERMISSIONS)
        self.assertIn("tabTraining Achievement", body)

    def test_a_preference_is_owner_only(self):
        """Knowing who has opted out of a feed is itself information about them."""
        body = _fn("profile_preference_query_conditions", PERMISSIONS)
        self.assertIn("`user` =", body)


class TestItIsWiredUp(unittest.TestCase):
    def test_the_endpoints_exist(self):
        src = _text(RUNTIME)
        for fn in ("get_feed", "send_kudos", "get_feed_preferences", "set_feed_preferences"):
            with self.subTest(fn=fn):
                self.assertIn(f"def {fn}(", src)

    def test_the_player_can_dial_them(self):
        page = _text(PAGE)
        for name in ("get_feed", "send_kudos", "get_feed_preferences", "set_feed_preferences"):
            with self.subTest(method=name):
                self.assertIn(name, page)

    def test_the_feed_is_routable_and_linked(self):
        player = _text(PLAYER)
        self.assertIn('view === "feed"', player)
        self.assertIn('go("feed")', player)

    def test_the_link_is_staff_only(self):
        """A client should never be offered a link to a staff feed, even one that
        would come back empty."""
        player = _code(_text(PLAYER))
        at = player.index("function recordOpen(")
        body = player[at : player.index("function openRecord(")]
        self.assertIn("b.is_staff", body)
        self.assertIn('go("feed")', body)
class TestTheLeaderboardIsNotTheWholeCompany(unittest.TestCase):
    """`LEADERBOARD_LIMIT` is 20 against sixteen active employees, so the board WAS
    the entire staff in rank order — including last place, with no way off it and
    with streaks published to everybody. A ranking that names the person at the
    bottom in a company this size is not a motivator."""

    GAMIFICATION = APP / "training/gamification.py"

    def test_only_the_top_few_are_shown(self):
        src = _text(self.GAMIFICATION)
        self.assertIn("BOARD_VISIBLE = 5", src)
        self.assertIn("visible[:BOARD_VISIBLE]", _fn("get_leaderboard", self.GAMIFICATION))

    def test_you_always_see_your_own_line(self):
        body = _fn("get_leaderboard", self.GAMIFICATION)
        self.assertIn("board = board + [mine]", body)

    def test_rank_is_computed_before_opt_outs_are_removed(self):
        """Otherwise leaving the board silently promotes everyone below you — wrong,
        and a way to work out who opted out."""
        body = _fn("get_leaderboard", self.GAMIFICATION)
        self.assertLess(body.index("ranked.append"), body.index("_shows_on_board"))

    def test_an_opt_out_never_hides_you_from_yourself(self):
        body = _fn("get_leaderboard", self.GAMIFICATION)
        self.assertIn('row["is_me"] or _shows_on_board', body)

    def test_streaks_are_self_only(self):
        """"How many days in a row somebody has studied" published to colleagues is
        a stick, and the person it beats hardest is whoever had a week off."""
        body = _fn("get_leaderboard", self.GAMIFICATION)
        self.assertIn("if user == me else None", body)

    def test_the_player_does_not_print_a_missing_streak_as_zero(self):
        """Zero is a real streak. Absent has to say absent, or the two are the same
        string on screen — the same defect as `pct(undefined)` rendering 0%."""
        player = _code(_text(PLAYER))
        self.assertIn("row.current_streak_days == null", player)

    def test_the_trim_is_said_out_loud(self):
        """A top five that does not say so reads as the whole company."""
        player = _code(_text(PLAYER))
        self.assertIn("total_ranked", player)
        self.assertIn("my_rank", player)

if __name__ == "__main__":
    unittest.main()

class TestTheFeedIsGatedOnTheVIEWER(unittest.TestCase):
    """The leak the first version of this shipped, caught by the branch review.

    `achievement_query_conditions` filtered on the **row's** `learner_type`. A
    customer contact holds `Training Learner`, would fail the "your own rows"
    clause and *pass* `visibility = 'Team' AND learner_type = 'Staff'` — and would
    be served the entire staff feed through `/api/resource`.

    The rule is about who is asking, not about the row. "Is this person staff" is a
    fact about them, and the fix is the same predicate used everywhere else in this
    release: employment, not a role.
    """

    def test_the_query_condition_checks_the_viewer(self):
        body = _fn("achievement_query_conditions", PERMISSIONS)
        self.assertIn("if not _is_staff(resolved):", body)

    def test_a_non_staff_viewer_gets_only_their_own_rows(self):
        """Sliced to the guard's own `return`, not a fixed window — the Team clause
        lives on the very next statement, so a generous slice reads it and the
        assertion fails on correct code."""
        body = _fn("achievement_query_conditions", PERMISSIONS)
        at = body.index("if not _is_staff(resolved):")
        branch = body[at : body.index("\n", body.index("return", at))]
        self.assertIn("`user` = {own}", branch)
        self.assertNotIn("Team", branch)

    def test_the_single_document_read_checks_the_viewer_too(self):
        """A query condition filters lists and says nothing about `frappe.get_doc`,
        so without this a customer could still read any staff achievement by name."""
        body = _fn("achievement_has_permission", PERMISSIONS)
        self.assertIn("if not _is_staff(resolved):", body)

    def test_kudos_are_gated_the_same_way(self):
        """Otherwise a customer could not read the feed but could read every
        reaction posted to it, which names the people and what they finished."""
        body = _fn("kudos_query_conditions", PERMISSIONS)
        self.assertIn("if not _is_staff(resolved):", body)
        self.assertIn("if not _is_staff(resolved):", _fn("kudos_has_permission", PERMISSIONS))

    def test_staff_is_employment_not_a_role(self):
        body = _fn("_is_staff", PERMISSIONS)
        self.assertIn('frappe.db.exists("Employee"', body)
        self.assertNotIn("get_roles", body)


class TestTheMigrateSafetyAuditFindings(unittest.TestCase):
    """Three defects the migrate-safety audit confirmed, each pinned so it cannot
    come back. All three shared a shape: the code read as correct, ran without
    error, and did nothing.
    """

    BACKFILL = APP / "patches/backfill_training_achievements.py"

    def test_record_can_be_forced_past_the_dormancy_check(self):
        """`social.record` is dormant during migrate/install/patch -- the app-wide
        convention that stops a schema change firing side effects. The backfill
        patch runs inside a migrate BY DEFINITION, so without a waiver it was a
        guaranteed no-op that still wrote a Patch Log row saying it had worked."""
        body = _fn("_enabled")
        self.assertIn("force", body)
        self.assertIn("in_migrate", body)
        sig = _text(SOCIAL)
        self.assertIn("def record(user, kind, title, occurred_on=None, force=False", sig)

    def test_force_never_waives_the_table_check(self):
        """Only the dormancy half is waivable. Minting into a table that does not
        exist is not a thing anyone should be able to ask for."""
        body = _fn("_enabled")
        early = body[: body.index("if force")]
        self.assertIn('frappe.db.exists("DocType", ACHIEVEMENT)', early)
        self.assertIn("return False", early)

    def test_every_backfill_call_passes_force(self):
        """One missed call is one silently empty section of the feed.

        Counted through the AST, not with `src.count("force=True")`. The comment
        in that patch explaining why the waiver is needed contains the string, so
        a substring count reads the prose as a call site and passes while a real
        call is missing it -- the same absence-assertion trap this release hit
        five times already.
        """
        import ast

        tree = ast.parse(_text(self.BACKFILL))
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "record"
        ]
        self.assertEqual(len(calls), 4, "expected four minting calls")
        for call in calls:
            forced = [
                k
                for k in call.keywords
                if k.arg == "force" and getattr(k.value, "value", None) is True
            ]
            self.assertTrue(forced, f"social.record at line {call.lineno} does not pass force=True")

    def test_the_visibility_opt_out_is_reachable(self):
        """The field carries `default: "Team"` and v16 applies defaults twelve
        lines BEFORE validate (`document.py:474` vs `:486`), so a guard of
        `if self.visibility: return` was already true on every insert -- the
        opt-out branch never ran and every achievement went to the team feed.

        Asserted on the guard being `is_new`-shaped rather than emptiness-shaped,
        because that is the actual defect.
        """
        body = _fn("_apply_visibility", ACHIEVEMENT_PY)
        self.assertIn("if not self.is_new():", body)
        self.assertNotIn("if self.visibility:", _code(body))
        self.assertIn("wants_feed", body)

    def test_dropping_the_json_default_would_not_have_been_enough(self):
        """Kept deliberately: `create_new.py` falls back to the first option of a
        Select with options, which is also "Team". The JSON default staying put is
        the evidence that the controller is the fix."""
        fields = {f["fieldname"]: f for f in json.loads(_text(ACHIEVEMENT_JSON))["fields"]}
        self.assertEqual(fields["visibility"]["options"].splitlines()[0], "Team")


class TestTheFeedIsGatedOnTheVIEWER(unittest.TestCase):
    """A customer contact holds `Training Learner`. Gating the Team clause on the
    ROW's learner_type let them fail the "own rows" arm and pass the Team arm --
    the entire staff feed, through /api/resource.
    """

    def test_a_non_staff_viewer_gets_only_their_own_rows(self):
        """Sliced to the guard's own `return`, not a fixed window -- the Team clause
        lives on the very next statement, so a generous slice reads it and the
        assertion fails on correct code."""
        body = _fn("achievement_query_conditions", PERMISSIONS)
        at = body.index("if not _is_staff(resolved):")
        branch = body[at : body.index(chr(10), body.index("return", at))]
        self.assertIn("`user` = {own}", branch)
        self.assertNotIn("Team", branch)

    def test_staff_is_employment_not_a_role(self):
        """A role can be granted by accident, and on this site every one of the 16
        staff logins holds `Customer`. Employment cannot be granted by accident."""
        body = _fn("_is_staff", PERMISSIONS)
        self.assertIn('frappe.db.exists("Employee"', body)
        self.assertIn('"status": "Active"', body)
        self.assertNotIn("has_role", body)
        self.assertNotIn("get_roles", body)

    def test_all_four_entry_points_gate_on_the_viewer(self):
        """A query condition filters lists and says nothing about get_doc(), so the
        has_permission twins need the same gate or the leak stays open by name."""
        for fn in (
            "achievement_query_conditions",
            "achievement_has_permission",
            "kudos_query_conditions",
            "kudos_has_permission",
        ):
            with self.subTest(fn=fn):
                self.assertIn("_is_staff", _fn(fn, PERMISSIONS))



if __name__ == "__main__":
    unittest.main()
