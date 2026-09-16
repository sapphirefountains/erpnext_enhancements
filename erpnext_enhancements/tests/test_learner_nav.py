"""The learner's routes and rail — and the one asymmetry that silently ate a whole view.

**The bug this suite exists for.** Clicking *View* on a colleague in the People directory took the
learner back to the catalogue. The player set `state.view = "person"` and asked the Desk host to
write the URL; the host's writer is

    if (next.course) { … } else if (next.view && LEARN_VIEWS.indexOf(next.view) !== -1) { … }

and `"person"` was not in `LEARN_VIEWS`. **There is no `else`** — so neither branch ran, the URL
collapsed to `/desk/learn`, and the route event that followed drove the player to the catalogue. The
person view was built and thrown away inside the same task.

Nothing failed. No error, no log line, no console message: an unknown view is written as the
catalogue, which is a legitimate URL. `test_training_desk_page` already asserted that `LEARN_VIEWS`
*exists* — it never asked what was in it.

So the test that matters is the **symmetry**: every view the player can route to must be one the
host can write. That is checked below by reading both files, and it fails for any future view
somebody adds to one side and not the other.

Run: python -m unittest erpnext_enhancements.tests.test_learner_nav
"""

import json
import re
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PLAYER = APP_ROOT / "public" / "js" / "training" / "player.js"
DESK_NAV = APP_ROOT / "public" / "js" / "training" / "desk_nav.js"
LEARN_JS = APP_ROOT / "training" / "page" / "learn" / "learn.js"
API_PY = APP_ROOT / "api" / "training.py"
CERT_HTML = APP_ROOT / "www" / "training_certificate.html"
PRINT_FORMATS = APP_ROOT / "training" / "setup_print_formats.py"
POSITION_PATCH = APP_ROOT / "patches" / "set_position_departments.py"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _raw(path):
    return path.read_text(encoding="utf-8")


def _strip_js_comments(src):
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    return re.sub(r"(?m)^\s*//.*$", "", src)


def _learn_views():
    found = re.search(r"const LEARN_VIEWS = \[(.*?)\];", _raw(LEARN_JS), re.S)
    assert found, "LEARN_VIEWS not found"
    return set(re.findall(r'"([^"]+)"', found.group(1)))


def _course_scoped_views():
    js = _strip_js_comments(_raw(PLAYER))
    block = js.split("var COURSE_SCOPED_VIEWS = {", 1)[1].split("};", 1)[0]
    return set(re.findall(r"(\w+):\s*true", block))


def _dispatched_views():
    """Every view `render()` knows how to draw."""
    js = _strip_js_comments(_raw(PLAYER))
    return set(re.findall(r'view === "([a-z]+)"', js))


class TestEveryViewTheHostCanWrite(unittest.TestCase):
    """The symmetry. This is the whole point of the suite."""

    def test_every_standalone_view_is_known_to_the_desk_host(self):
        """A view the host does not know is written as `/desk/learn` — no error, no log — and the
        route event that follows bounces the learner to the catalogue. That is what happened to
        the person view for three days."""
        standalone = _dispatched_views() - _course_scoped_views()
        # `unavailable` is a state the player enters when the runtime is switched off, never a
        # destination anybody routes to, and it has no URL by design. `catalog` IS the bare
        # /desk/learn route, so it is written by having no view segment at all rather than by
        # being in the list.
        standalone.discard("unavailable")
        standalone.discard("catalog")
        missing = sorted(standalone - _learn_views())
        self.assertEqual(
            missing,
            [],
            f"player.js can route to these and learn.js cannot write them: {missing}",
        )

    def test_the_host_writes_no_view_the_player_cannot_draw(self):
        """The other direction: a URL somebody bookmarks must land somewhere."""
        extra = sorted(_learn_views() - _dispatched_views())
        self.assertEqual(extra, [], f"learn.js writes these and player.js cannot draw them: {extra}")

    def test_person_is_among_them(self):
        self.assertIn("person", _learn_views())


class TestThePersonRouteCarriesWhoItIsAbout(unittest.TestCase):
    """Naming the view is not enough — the route has to say whose profile it is.

    Both halves of learn.js compute a key and compare them; the file's own comment records that
    they disagreed once already and caused a navigation loop. If the key omits the user, every
    profile is `person||`, so moving from one colleague to another is swallowed as "already
    showing this" and Back shows the wrong person.
    """

    def test_the_player_puts_the_user_in_the_route_state(self):
        js = _strip_js_comments(_raw(PLAYER))
        block = js.split("function routeState()", 1)[1][:800]
        self.assertIn("user:", block)
        self.assertIn("state.viewingUser", block)

    def test_the_host_writes_it_into_the_url(self):
        js = _strip_js_comments(_raw(LEARN_JS))
        self.assertIn('parts.push("person", next.user)', js)

    def test_the_host_reads_it_back(self):
        js = _strip_js_comments(_raw(LEARN_JS))
        self.assertIn('target.view === "person" && route[2]', js)

    def test_the_loop_guard_tells_two_people_apart(self):
        js = _strip_js_comments(_raw(LEARN_JS))
        block = js.split("position_key(target)", 1)[1][:500]
        self.assertIn("target.user", block)

    def test_the_view_is_opened_through_one_entry_point(self):
        """`openPerson` sets the field and routes together, so the route and the state cannot
        disagree about whose profile is open."""
        js = _strip_js_comments(_raw(PLAYER))
        self.assertIn("function openPerson(user)", js)
        self.assertIn("openPerson: openPerson", js)
        directory = js.split("function directoryRow(", 1)[1][:1400]
        self.assertIn("openPerson(person.user)", directory)
        self.assertNotIn("state.viewingUser = person.user", directory)

    def test_a_deep_link_without_a_user_falls_back_to_the_directory(self):
        """Rendering your own record under a "← People" header reads as a privacy bug even when
        it is not one."""
        js = _strip_js_comments(_raw(PLAYER))
        block = js.split('if (b.view === "person")', 1)[1][:400]
        self.assertIn('go("people")', block)


class TestTheLeaderboardIsReachable(unittest.TestCase):
    """It existed as a collapsed toggle below every course shelf, which is the same as not
    shipping it. gamification.py has computed points, badges and streaks since v1.215.0."""

    def test_it_is_a_destination(self):
        self.assertIn("board", _learn_views())
        self.assertIn('view === "board"', _strip_js_comments(_raw(PLAYER)))

    def test_the_rail_links_to_it(self):
        self.assertIn('route: ["learn", "board"]', _strip_js_comments(_raw(DESK_NAV)))

    def test_it_reuses_the_one_board(self):
        """A second board could disagree with the first about who is winning."""
        js = _strip_js_comments(_raw(PLAYER))
        block = js.split("function renderBoardView()", 1)[1][:700]
        self.assertIn("renderLeaderboard()", block)

    def test_it_opens_expanded(self):
        """Arriving somewhere called Leaderboard and being shown a closed box is a wasted click."""
        js = _strip_js_comments(_raw(PLAYER))
        block = js.split("function renderBoardView()", 1)[1][:700]
        self.assertIn("boardState.open = true", block)


class TestTheCatalogueIsGroupedByTheBusiness(unittest.TestCase):
    def test_the_card_carries_its_groups(self):
        src = _raw(API_PY)
        card = src.split("def _course_card(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('"groups": _course_groups()', card)

    def test_the_group_is_derived_not_stored(self):
        """A `functional_group` field on the course would be a second place for the same fact to
        live and a second place for it to go stale. The assignment rules already say who it is
        for."""
        src = _raw(API_PY)
        fn = src.split("def _course_groups()", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("Training Assignment Rule", fn)
        self.assertIn("Position", fn)
        self.assertIn('rule.applies_to == "Department"', fn)

    def test_a_role_rule_contributes_nothing(self):
        """`Production Team` is a permission, not a part of the business."""
        src = _raw(API_PY)
        fn = src.split("def _course_groups()", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn('== "Role"', fn)

    def test_a_course_may_belong_to_more_than_one(self):
        """Written for a project manager and a sales rep, it belongs under both headings."""
        src = _raw(API_PY)
        fn = src.split("def _course_groups()", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("setdefault", fn)

    def test_the_rail_draws_the_submenu_from_the_boot(self):
        js = _strip_js_comments(_raw(DESK_NAV))
        self.assertIn("catalogueSubmenu", js)
        self.assertIn("card.groups", js)


class TestThePositionMapping(unittest.TestCase):
    """Which department a Position belongs to is a judgement about the org chart, not a
    derivation — so it is a table somebody can read and correct."""

    @classmethod
    def setUpClass(cls):
        cls.src = _raw(POSITION_PATCH)

    def test_the_technician_grades_all_go_to_production(self):
        """It is what makes the ten technician modules appear under one heading rather than four."""
        table = self.src.split("POSITION_DEPARTMENT = {", 1)[1].split("}", 1)[0]
        for position in ("Technician", "Junior Technician", "Senior Technician", "Master Technician"):
            with self.subTest(position):
                self.assertRegex(table, rf'"{re.escape(position)}":\s*"Production"')

    def test_it_never_overwrites_a_department_somebody_set(self):
        """Operations Manager already read Operations. A correction made in the Desk has to
        outlive the next migrate."""
        self.assertIn("if current:", self.src)
        self.assertIn("kept.append", self.src)

    def test_it_never_invents_a_department(self):
        """Creating org structure from a patch is how a tidy-up invents a department nobody
        agreed to."""
        self.assertIn('frappe.db.exists("Department", department)', self.src)
        self.assertIn("missing.append", self.src)

    def test_all_positions_is_left_alone(self):
        """It is the root of the tree, not a job anybody holds."""
        self.assertIn("UNASSIGNED", self.src)
        table = self.src.split("POSITION_DEPARTMENT = {", 1)[1].split("}", 1)[0]
        self.assertNotIn('"All Positions"', table)

    def test_it_is_registered(self):
        self.assertIn(
            "erpnext_enhancements.patches.set_position_departments", _raw(APP_ROOT / "patches.txt")
        )


class TestTheCertificate(unittest.TestCase):
    def test_there_is_one_print_button(self):
        """Two Print buttons, and the one that looked official did nothing."""
        html = _raw(CERT_HTML)
        self.assertNotIn('onclick="window.print()">Print</button>', html)

    def test_the_saved_banner_print_link_is_wired_up(self):
        """`certificate_html` is the whole rendered print view, and frappe's Print link in it has
        neither an href nor a handler — the script that binds it lives in frappe's print page and
        is not part of the markup. So the link was inert in the snapshot."""
        html = _raw(CERT_HTML)
        self.assertIn(".tc-doc .action-banner", html)
        self.assertIn("window.print()", html)

    def test_it_binds_the_link_with_no_href_rather_than_the_word_print(self):
        """Matching on the missing destination keeps it working in a translated certificate."""
        html = _raw(CERT_HTML)
        self.assertIn('link.getAttribute("href")', html)

    def test_the_snapshot_itself_is_not_rewritten(self):
        """What the holder was given must not change because a template was edited."""
        html = _raw(CERT_HTML)
        self.assertIn("certificate.certificate_html", html)

    def test_the_badge_is_keyed_on_the_course_not_the_award(self):
        """`after_completion` issues and renders the certificate BEFORE `_award_badges` runs, so a
        `Training Badge Award` for this completion does not exist yet. Querying one would render
        an empty space and raise nothing."""
        # Comments stripped first. The comment explaining WHY the award is not queried names the
        # award, so an absence assertion over the raw file fails on its own documentation and
        # teaches whoever hits it to delete the explanation. This repo has been caught by that
        # shape before.
        src = _raw(PRINT_FORMATS)
        self.assertIn('"criteria_type": "Course Completed"', src)
        jinja = re.sub(r"\{#-?[\s\S]*?-?#\}", "", src)
        jinja = re.sub(r'"""[\s\S]*?"""', lambda m: m.group(0) if "_CERTIFICATE_HTML" in src[: m.start()] else "", jinja, count=1)
        self.assertNotIn("Training Badge Award", re.sub(r"(?m)^\s*#.*$", "", jinja))

    def test_the_badge_image_is_sized(self):
        """The badge SVGs carry width="256" height="256" and would otherwise take over the page."""
        src = _raw(PRINT_FORMATS)
        block = src.split("badge_image", 1)[1][:900]
        self.assertIn('width="96"', block)
        self.assertIn('height="96"', block)

    def test_a_course_with_no_badge_renders_as_before(self):
        src = _raw(PRINT_FORMATS)
        self.assertIn("{%- if badge_image %}", src)


class TestThisSuiteRunsInCi(unittest.TestCase):
    def test_it_is_wired(self):
        self.assertIn("erpnext_enhancements.tests.test_learner_nav", _raw(CI))


if __name__ == "__main__":
    unittest.main()
