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


if __name__ == "__main__":
    unittest.main()

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
