"""The Training rail — one sidebar shared by the learner page and the dashboard.

Three things here can break without anything erroring, which is what this module
is for.

**A class with no rule.** The rail is built in JavaScript and styled in a separate
file, so an invented class renders as an unstyled div — not an error, and visible
only to a human looking at the page. Checked in BOTH directions, for the reason
`test_training_player_css_contract` was written: that stylesheet and its scripts
had drifted into two different vocabularies while the page still *looked* styled.

**A link offered to somebody the Page refuses.** `training-insights` is manager-only
and `training-canvas` is author-only. A rail that offers either to a learner lands
them on "Not permitted", which reads as the feature being broken rather than as
not being theirs — so the role lists in the script are asserted against the `roles`
arrays on the Page documents themselves.

**A second answer to "which courses are mine".** The rail is fed the boot payload
the player already fetched. The tempting alternative is a `frappe.db.get_list` for
open assignments, and it would be wrong in a specific way: "open" and "overdue" are
predicates defined in `api/training._open_assignments`, and a nullable-date filter
written in the browser is exactly the shape that once turned "no expiry" into
"expired" across this module.

One grep trap worth naming, because it will catch somebody: an unanchored search
for ``tn-`` also matches every Bootstrap ``btn-`` in the repo. Every pattern below
anchors on ``\\b`` — a word boundary excludes the ``b`` — or on the leading dot.

Run: python -m unittest erpnext_enhancements.tests.test_training_desk_nav
"""

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"

NAV_JS = APP / "public" / "js" / "training" / "desk_nav.js"
NAV_CSS = APP / "public" / "css" / "training" / "desk_nav.css"
LEARN_JS = APP / "training" / "page" / "learn" / "learn.js"
INSIGHTS_JS = APP / "training" / "page" / "training_insights" / "training_insights.js"
INSIGHTS_JSON = APP / "training" / "page" / "training_insights" / "training_insights.json"
CANVAS_JSON = APP / "training" / "page" / "training_canvas" / "training_canvas.json"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def read(path):
    return path.read_text(encoding="utf-8")


def code(path):
    """Source with comments stripped.

    Every assertion about ABSENCE below needs this, and so do the two about
    presence: the header comments in both files spell out `/app/`, `frappe.call`
    and the `tn-`/`btn-` trap while explaining why none of them are used. A comment
    that names the thing it forbids is the fourth instance of that failure in this
    repo, and the reason it keeps happening is that the comment is the most
    natural place to write the explanation.
    """
    src = re.sub(r"/\*.*?\*/", "", read(path), flags=re.S)
    return chr(10).join(line for line in src.splitlines() if not line.strip().startswith("//"))


def page_roles(path):
    return {row["role"] for row in json.loads(read(path))["roles"]}


class TestTheFilesAreThere(unittest.TestCase):
    def test_both_halves_exist(self):
        """Two files, and the script alone is useless. `learn.js` and
        `training_insights.js` load them as one list for that reason."""
        self.assertTrue(NAV_JS.exists(), f"{NAV_JS} is missing")
        self.assertTrue(NAV_CSS.exists(), f"{NAV_CSS} is missing")


class TestTheClassContract(unittest.TestCase):
    def emitted(self):
        return set(re.findall(r"\btn-[a-z0-9-]+", code(NAV_JS)))

    def styled(self):
        css = re.sub(r"/\*.*?\*/", "", read(NAV_CSS), flags=re.S)
        return {name.lstrip(".") for name in re.findall(r"\.tn-[a-z0-9-]+", css)}

    def test_every_class_the_script_emits_has_a_rule(self):
        missing = sorted(self.emitted() - self.styled())
        self.assertEqual(missing, [], f"{missing} render unstyled")

    def test_every_rule_styles_something_the_script_emits(self):
        """The other direction, and the one that rots quietly: a rule for a class
        nobody writes any more is dead weight that reads as coverage."""
        stray = sorted(self.styled() - self.emitted())
        self.assertEqual(stray, [], f"{stray} style nothing")

    def test_it_actually_found_some(self):
        """A regex that matched nothing would pass both tests above."""
        self.assertGreater(len(self.emitted()), 10)


class TestTheSeam(unittest.TestCase):
    """The rail is desk chrome. The player is the Aurora reading surface. The
    boundary is `.tl-desk-surface`, and the palette has exactly one declaration
    site."""

    def test_the_stylesheet_declares_no_palette_token(self):
        self.assertNotRegex(read(NAV_CSS), r"--tr-[\w-]+\s*:")

    def test_the_stylesheet_reads_no_palette_token(self):
        """Not even a read. The rail sits outside the seam, so an Aurora colour
        here would follow the learner's surface instead of the desk theme — and
        would leave the manager dashboard, which never loads player.css, styling
        itself against variables that do not exist."""
        self.assertNotIn("var(--tr-", read(NAV_CSS))

    def test_it_invents_no_player_class(self):
        """A `tr-` class emitted here fails the player's own two-way contract as
        "rendered but never styled", and the obvious fix — a rule in player.css —
        would ship desk-only chrome to the portal and the authoring canvas."""
        stray = sorted(set(re.findall(r"\btr-[a-z0-9-]+", code(NAV_JS))))
        self.assertEqual(stray, [], f"{stray} are player classes invented by the rail")


class TestItNamesNoEndpoint(unittest.TestCase):
    def test_it_makes_no_server_call(self):
        src = code(NAV_JS)
        for token in ("frappe.xcall", "frappe.call(", "frappe.db.get_list", "/api/method"):
            with self.subTest(token):
                self.assertNotIn(token, src)

    def test_the_learner_data_arrives_from_the_host(self):
        """One entry point, fed by the payload the player already booted from."""
        self.assertIn("DeskNav.prototype.setLearner", code(NAV_JS))

    def test_it_reads_the_keys_the_bootstrap_already_sends(self):
        """Every one of these is returned by `api.training.get_learner_bootstrap`.
        A key invented here is silently undefined, which renders as a rail with a
        section missing rather than as an error."""
        src = code(NAV_JS)
        api = read(APP / "api" / "training.py")
        for key in ("assigned", "is_staff", "signoffs_to_record"):
            with self.subTest(key):
                self.assertIn(key, src)
                self.assertIn(f'"{key}"', api)


class TestItRoutesRatherThanLinking(unittest.TestCase):
    def test_it_builds_no_desk_path_by_hand(self):
        """The desk prefix is `/desk`, not `/app`, and an in-desk `<a href>` to
        either is not intercepted by the router: it costs a full page load, plus a
        redirect hop if it was `/app`."""
        src = code(NAV_JS)
        self.assertNotIn('href="/app/', src)
        self.assertNotIn('href="/desk/', src)
        self.assertNotIn("/desk/learn", src)

    def test_every_destination_goes_through_set_route(self):
        self.assertIn("frappe.set_route", code(NAV_JS))

    def test_a_course_opens_at_its_own_route(self):
        """The same route the player's own cards write, so arriving from the rail
        and arriving from a card are the same event — on the learner page it comes
        back through on_page_show and moves the mounted player rather than
        rebooting it."""
        self.assertIn('frappe.set_route("learn", card.course)', code(NAV_JS))


class TestTheRoleGate(unittest.TestCase):
    def js_role_list(self, name):
        match = re.search(rf"var {name} = \[(.*?)\];", code(NAV_JS), flags=re.S)
        self.assertIsNotNone(match, f"{name} not found in desk_nav.js")
        return set(re.findall(r'"([^"]+)"', match.group(1)))

    def test_the_manager_list_matches_the_dashboard_page(self):
        self.assertEqual(self.js_role_list("MANAGER_ROLES"), page_roles(INSIGHTS_JSON))

    def test_the_author_list_matches_the_canvas_page(self):
        self.assertEqual(self.js_role_list("AUTHOR_ROLES"), page_roles(CANVAS_JSON))

    def test_a_plain_learner_gets_no_manage_section(self):
        """Not an empty heading: the section is not drawn at all. A `Manage`
        heading with nothing under it tells fourteen of seventeen people that
        something is missing."""
        self.assertIn("if (!manager && !author) return [];", code(NAV_JS))

    def test_a_document_link_is_checked_against_the_permission_not_the_role(self):
        """Two gates, because they answer two different questions. The section is
        opened by role, mirroring the Page documents. The links inside it are lists,
        and a role is a poor proxy for a DocPerm in BOTH directions: `HR Manager`
        opens the dashboard and need not hold read on Training Course, while a
        Training Learner holds read on fourteen Training doctypes and must still see
        no Manage section at all."""
        src = code(NAV_JS)
        self.assertIn("frappe.model.can_read", src)
        self.assertIn('if (spec.route[0] !== "List" && spec.route[0] !== "Form") return true;', src)

    def test_the_learner_can_still_reach_the_learner_surfaces(self):
        """Everything in the Learn section is a route into `learn`, which is the
        one Page in this module a Training Learner holds."""
        self.assertIn("Training Learner", page_roles(APP / "training" / "page" / "learn" / "learn.json"))


class TestMyTrainings(unittest.TestCase):
    def test_absent_and_empty_are_different_states(self):
        """`null` is "this surface has no learner payload" and renders a door; `[]`
        is "nothing is assigned to you" and renders the sentence. Collapsing the
        two would tell a manager on the dashboard that they owe no training, which
        is a statement about them and is very likely false."""
        src = code(NAV_JS)
        self.assertIn("Nothing is assigned to you right now.", src)
        self.assertIn("Open my training", src)

    def test_the_count_is_the_length_of_what_it_shows(self):
        """A badge derived from anything else is a number that can disagree with
        the list directly under it."""
        self.assertIn("String(assigned.length)", code(NAV_JS))

    def test_overdue_is_measured_against_the_site_date(self):
        """`frappe.datetime.get_today()` is the site's date. `new Date()` is the
        device's, and this app has already shipped one bug from comparing a
        site-local stamp against a browser clock."""
        src = code(NAV_JS)
        self.assertIn("frappe.datetime.get_today()", src)
        self.assertNotIn("new Date(", src)

    def test_a_finished_course_is_never_overdue(self):
        """Its due date is in the past by definition once it is done, so a naive
        date comparison paints every completed course red."""
        start = code(NAV_JS).index("function isOverdue(")
        body = code(NAV_JS)[start : start + 600]
        self.assertIn("Completed", body)

    def test_the_progress_bar_is_drawn_only_when_there_is_progress(self):
        self.assertIn("if (percent > 0)", code(NAV_JS))


class TestTheNarrowScreen(unittest.TestCase):
    """frappe lays `.layout-main` out as `display: flex; flex-direction: row` at
    EVERY width — it does not stack on its own. Without the rules below the rail
    sits beside the player on a phone, which is the device player.css says the
    learner surface was built for."""

    def test_the_layout_is_stacked_under_the_desktop_breakpoint(self):
        css = read(NAV_CSS)
        self.assertIn("@media (max-width: 991px)", css)
        block = css[css.index("@media (max-width: 991px)") :]
        self.assertIn("flex-direction: column", block)

    def test_the_rail_does_not_inherit_the_form_sidebar_width(self):
        """frappe's own `.layout-side-section` carries `min-width:
        var(--form-sidebar-width)` and, under xl, `min-width: calc(38vw -
        var(--sidebar-width))`. Inherited, that hands 38% of a tablet to a nav."""
        self.assertIn("min-width: 0", read(NAV_CSS))

    def test_the_disclosure_follows_the_viewport(self):
        src = code(NAV_JS)
        self.assertIn("matchMedia", src)
        self.assertIn("details.open", src)


class TestTheHostsMountIt(unittest.TestCase):
    def test_both_pages_are_two_column(self):
        """`page.sidebar` exists only on a two-column page. A rail mounted into a
        single-column page appends to an empty jQuery set and vanishes with no
        error at all."""
        for path in (LEARN_JS, INSIGHTS_JS):
            with self.subTest(path.name):
                self.assertIn("single_column: false", code(path))

    def test_both_pages_load_both_halves(self):
        for path in (LEARN_JS, INSIGHTS_JS):
            with self.subTest(path.name):
                src = code(path)
                self.assertIn("css/training/desk_nav.css", src)
                self.assertIn("js/training/desk_nav.js", src)

    def test_both_pages_construct_it(self):
        for path in (LEARN_JS, INSIGHTS_JS):
            with self.subTest(path.name):
                self.assertIn("TR.deskNav(", code(path))

    def test_the_assets_are_loaded_with_a_cache_bust_token(self):
        """Raw `/assets` are served immutable for a year with no content hash, so
        an edit never reaches a device that already cached the file. TR.loadAssets
        refuses a path with no `?v=`; these two must go through it."""
        for path in (LEARN_JS, INSIGHTS_JS):
            with self.subTest(path.name):
                self.assertIn("TR.loadAssets(", code(path))

    def test_the_rail_fails_independently_of_the_page(self):
        """Chrome is not the page. A rail that cannot load must not put an error
        where a lesson or six numbers should be — so its chain is its own, and it
        ends in a catch."""
        for path in (LEARN_JS, INSIGHTS_JS):
            with self.subTest(path.name):
                src = code(path)
                start = src.index("desk_nav")
                self.assertIn(".catch(", src[start:])

    def test_the_learner_page_feeds_it_the_boot_payload(self):
        self.assertIn("setLearner(", code(LEARN_JS))

    def test_the_dashboard_does_not(self):
        """Deliberate. It holds no learner boot, and filling the list there would
        mean a second read of this manager's own assignments on a page whose whole
        job is other people's."""
        self.assertNotIn("setLearner", code(INSIGHTS_JS))

    def test_the_learner_page_keeps_the_rail_and_the_player_in_step(self):
        """One place where position changes, so the rail cannot highlight a course
        the player is not showing."""
        src = code(LEARN_JS)
        self.assertIn("mark(where)", src)
        # Both directions of the router loop, which is where position is decided.
        self.assertEqual(src.count("this.mark("), 3)


class TestItIsWiredIntoCI(unittest.TestCase):
    def test_ci_runs_this_module(self):
        self.assertIn("erpnext_enhancements.tests.test_training_desk_nav", read(CI))


if __name__ == "__main__":
    unittest.main()
