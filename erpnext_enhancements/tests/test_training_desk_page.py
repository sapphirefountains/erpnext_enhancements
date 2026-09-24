"""The learner Desk Page — registration, lifecycle, and the seams it must not cross.

`/desk/learn` is a HOST for the existing `TR.Player`, not a second player. Nothing at
runtime would tell you if it quietly became the latter: a fork renders perfectly and
drifts from the portal one commit at a time. These are the properties that keep it a
host.

Two of them are correctness rather than tidiness, and both fail silently:

* **Teardown.** frappe creates a page div once (`views/container.js` `add_page`) and
  never removes it, and there is no `on_page_hide` hook in v16 — the page loader binds
  only `"show"`. Without a `hide` handler the `<video>`, its timers and the heartbeat
  survive the learner navigating away: the video keeps downloading and the player keeps
  claiming watch time for a lesson nobody is looking at. That is not a leak, it is
  false telemetry on the artefact a compliance conversation rests on.
* **The route name.** `frappe.router` resolves the first path segment against
  `frappe.workspaces` *before* the page loader and discards the rest of the path, and
  `allowed_workspaces` is permission-filtered — so a page named after a workspace is
  one URL with two destinations and no error in either. The general rule is enforced
  in `test_workspaces.py`; this pins the specific choice and the reason.

Run: python -m unittest erpnext_enhancements.tests.test_training_desk_page
"""

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
HISTORY_HARNESS = REPO / "scripts" / "test_training_desk_history.mjs"
PAGE_DIR = APP / "training" / "page" / "learn"
PAGE_JSON = PAGE_DIR / "learn.json"
PAGE_JS = PAGE_DIR / "learn.js"
PAGE_CSS = PAGE_DIR / "learn.css"
PLAYER_DIR = APP / "public" / "js" / "training"


def js():
    return PAGE_JS.read_text(encoding="utf-8")


def code():
    """learn.js with comments stripped — this module asserts several absences."""
    src = re.sub(r"/\*.*?\*/", "", js(), flags=re.S)
    return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def doc():
    return json.loads(PAGE_JSON.read_text(encoding="utf-8"))


def strip_comments(text):
    """Comments out, before any assertion that something is ABSENT.

    A comment naming a token is not a use of it, and the comment explaining why
    something is absent nearly always names the thing -- `training_assignment.js`
    says in prose that an `<a href="/app/...">` would not be intercepted by the
    router, which is exactly the string the test below refuses to find. This repo
    has been caught by that shape four times now.
    """
    text = re.sub(r"/[*].*?[*]/", "", text, flags=re.S)
    keep = [line for line in text.splitlines() if not line.strip().startswith("//")]
    return chr(10).join(keep)


class TestItIsRegistered(unittest.TestCase):
    def test_every_file_is_there(self):
        for path in (PAGE_JSON, PAGE_JS, PAGE_CSS, PAGE_DIR / "__init__.py"):
            with self.subTest(path.name):
                self.assertTrue(path.is_file(), f"missing {path}")

    def test_the_json_is_a_page_in_the_training_module(self):
        d = doc()
        self.assertEqual(d["doctype"], "Page")
        self.assertEqual(d["module"], "Training")
        self.assertEqual(d["standard"], "Yes")

    def test_the_docname_matches_the_folder(self):
        """`Page.load_assets` builds the asset paths from `scrub(self.name)`, so a
        docname that disagrees with its folder loads neither the js nor the css —
        and renders an empty page rather than erroring."""
        self.assertEqual(doc()["name"], PAGE_DIR.name)
        self.assertEqual(doc()["page_name"], PAGE_DIR.name)

    def test_the_js_registers_under_that_name(self):
        self.assertIn('frappe.pages["learn"].on_page_load', code())
        self.assertIn('frappe.pages["learn"].on_page_show', code())

    def test_it_is_not_called_training(self):
        """The Training workspace owns that slug. See the module docstring; the
        general rule lives in test_workspaces.TestNoPageShadowsAWorkspace."""
        self.assertNotEqual(doc()["name"], "training")


class TestTheRolloutSwitch(unittest.TestCase):
    """`roles` on the Page record decides who sees it, and it was used as the
    staged-rollout control: System Manager only in v1.429.0, opened to learners in
    v1.429.1 once the page existed and its tests were green.

    It is show/hide, **never** a permission boundary — a Desk Page has no server
    controller, and every endpoint the player dials re-checks for itself. WI-074
    states the same rule for Desktop Icon. This test exists so that changing who can
    see the learner surface is a deliberate edit with a test to update, rather than
    something that happens on the way past.
    """

    def test_the_role_set_is_what_we_think_it_is(self):
        roles = sorted(row["role"] for row in doc().get("roles") or [])
        self.assertEqual(
            roles,
            ["System Manager", "Training Author", "Training Learner", "Training Manager"],
            "learn.json's roles changed. If this is deliberate, update this test in "
            "the same commit and say so in the CHANGELOG.",
        )

    def test_the_learner_role_is_there(self):
        """Named separately because it is the one that matters: without it the page
        exists, passes every other test here, and is invisible to all fifteen of the
        people it was built for."""
        roles = {row["role"] for row in doc().get("roles") or []}
        self.assertIn("Training Learner", roles)


class TestItIsAHostNotAPlayer(unittest.TestCase):
    def test_it_constructs_the_real_player(self):
        self.assertRegex(code(), r"new TR\.Player\(")

    def test_it_builds_its_transport_from_the_shared_factory(self):
        """Not its own `fetch` wrapper. The endpoint names live in exactly one file."""
        self.assertIn("TR.makeTransport(", code())

    def test_it_passes_the_csrf_token_as_a_function(self):
        """A desk session can outlive the token the page booted with, and this page is
        long-lived by construction — frappe never removes the page div."""
        self.assertRegex(code(), r"csrf:\s*\(\)\s*=>")

    def test_it_names_no_endpoint_of_its_own(self):
        """A METHOD-map entry copied into the host is how the two hosts start
        disagreeing about what an endpoint is called."""
        self.assertNotIn("/api/method/erpnext_enhancements.api.training.", code())

    def test_it_loads_the_runtime_in_composition_order(self):
        """player.js composes blocks, video and quiz. TR.loadAsset sets async=false so
        insertion order is execution order; get this wrong and the failure is
        intermittent, which is worse than broken."""
        text = code()
        order = [text.index(f"js/training/{name}.js") for name in ("blocks", "video", "quiz", "player")]
        self.assertEqual(order, sorted(order), "the player files are not listed in load order")

    def test_it_loads_the_player_stylesheet(self):
        self.assertIn("css/training/player.css", code())

    def test_every_asset_it_loads_actually_exists(self):
        """A path typo is a 404 the page survives — TR.loadAssets rejects, the page
        shows its error, and nothing says which file."""
        for match in re.finditer(r'"/assets/erpnext_enhancements/([^"]+)"', code()):
            rel = match.group(1)
            with self.subTest(rel):
                self.assertTrue((APP / "public" / rel).is_file(), f"no such asset: {rel}")


class TestTheLifecycle(unittest.TestCase):
    def test_it_tears_down_on_hide(self):
        """There is no on_page_hide hook in v16; frappe fires a jQuery "hide" event on
        the outgoing page element and binds only "show" itself."""
        self.assertRegex(code(), r'\$\(wrapper\)\.on\("hide"')

    def test_teardown_destroys_the_player(self):
        self.assertRegex(code(), r"this\.player\.destroy\(\)")

    def test_it_does_not_reboot_on_every_route_change(self):
        """`on_page_show` fires on every route change INTO the page, including
        /desk/learn/A/1 -> /desk/learn/A/2. Re-booting there would refetch the
        catalogue and throw away in-flight watch progress on every click."""
        self.assertIn("openCourse(", code())
        self.assertRegex(code(), r"if \(this\.player\)")

    def test_it_guards_against_a_double_boot(self):
        """Two "show" events before the first bootstrap resolves would mount two
        players onto one element, and the second would win while the first kept its
        timers."""
        self.assertIn("this.booting", code())


class TestItOwnsTheUrl(unittest.TestCase):
    """Since v1.432.2 the page writes the address bar through the player's injected
    router adapter — and still does not let the player READ it.

    Those are two different jobs. `history: false` keeps the player from running
    `queryParam("course")` against a desk route that has no query string (which would
    land on the catalogue on every browser Back); the adapter is checked
    independently of that flag, so the URL is still written.
    """

    def test_history_is_off(self):
        self.assertRegex(code(), r"boot\.history\s*=\s*false")

    def test_it_injects_a_router_adapter(self):
        self.assertRegex(code(), r"boot\.router\s*=")
        self.assertIn("router_adapter", code())

    def test_the_adapter_routes_rather_than_linking(self):
        self.assertIn("frappe.set_route(", code())

    def test_both_directions_share_one_guard(self):
        """The player writes the URL and the URL drives the player, so they close into
        a loop unless one end stops. `this.showing` is that end, and it has to be read
        by BOTH — an adapter that only checked frappe.get_route() would still fire a
        route event for a no-op, and that event arrives as a fresh handle_route."""
        text = code()
        self.assertGreaterEqual(
            text.count("this.showing"),
            3,
            "the loop guard must be read in apply_route and in the adapter",
        )

    def test_the_deep_link_arrives_through_start(self):
        self.assertRegex(code(), r"boot\.start\s*=")

    def test_it_reads_the_route_from_frappe(self):
        self.assertIn("frappe.get_route()", code())

    def test_the_reserved_segments_are_named(self):
        """Course names come from a TRN-CRS- series, so a collision is not currently
        possible — which is why the list is written down rather than left to the
        naming series to guarantee."""
        self.assertRegex(code(), r"LEARN_VIEWS\s*=\s*\[")


class TestTheChromeStaysOnItsOwnSideOfTheSeam(unittest.TestCase):
    def test_the_stylesheet_declares_no_palette_token(self):
        """player.css is the one declaration site. Re-declaring --tr-surface here to
        "match the desk" is how the authoring canvas stops matching what a learner
        sees. Also asserted globally in test_training_desk_theme."""
        self.assertNotRegex(PAGE_CSS.read_text(encoding="utf-8"), r"--tr-[\w-]+\s*:")

    def test_the_host_invents_no_tr_class(self):
        """A `tr-` class emitted here fails the player's two-way class contract as
        "rendered but never styled"; moving its rule into player.css to satisfy that
        would ship desk-only chrome to the portal and the canvas."""
        invented = set(re.findall(r'class="([^"]*)"', code()))
        classes = {cls for group in invented for cls in group.split()}
        # The mount and its boot line. Both already have rules in player.css and both
        # were rendered by the portal template before it became a redirect — the host
        # took over the same two, it did not invent them. What this refuses is a NEW
        # tr-* class, which would fail the player's two-way contract as "rendered but
        # never styled", and whose obvious fix (add the rule to player.css) would ship
        # desk-only chrome to the portal, the preview harness and the canvas.
        mount = {"tr-shell", "tr-boot"}
        stray = sorted(c for c in classes if c.startswith("tr-") and c not in mount)
        self.assertEqual(stray, [], f"{stray} are player classes invented by the host")

    def test_every_class_it_renders_has_a_rule(self):
        css = PAGE_CSS.read_text(encoding="utf-8")
        groups = set(re.findall(r'class="([^"]*)"', code()))
        classes = {cls for group in groups for cls in group.split()}
        missing = sorted(
            c for c in classes if c.startswith("tl-") and f".{c}" not in css
        )
        self.assertEqual(missing, [], f"{missing} are rendered with no rule in learn.css")


class TestTheDoorIsFindable(unittest.TestCase):
    """A page nobody can find is the problem this programme exists to fix, restated.

    The player was finished and reachable only from a link inside an email. Shipping
    a second surface with the same property would be the same bug with a nicer
    implementation, so the routes IN are asserted rather than assumed.
    """

    WORKSPACES = APP / "training" / "workspace"

    def workspace(self, folder):
        return json.loads((self.WORKSPACES / folder / f"{folder}.json").read_text(encoding="utf-8"))

    def test_both_workspaces_shortcut_the_page(self):
        for folder in ("training", "my_training"):
            with self.subTest(folder):
                shortcuts = self.workspace(folder).get("shortcuts") or []
                pages = [s for s in shortcuts if s.get("type") == "Page" and s.get("link_to") == "learn"]
                self.assertTrue(pages, f"{folder} has no Page shortcut to learn")

    def test_the_learner_workspace_is_in_the_training_module(self):
        """The module gate is what decides whether it appears at all: a workspace
        vanishes silently unless the viewer holds a DocPerm on some non-child doctype
        in its module, and `roles: []` does not mean everyone."""
        self.assertEqual(self.workspace("my_training")["module"], "Training")

    def test_the_learner_workspace_links_only_at_learner_records(self):
        """It must not become a second authoring console. Every doctype it links is
        one `permission_query_conditions` scopes to the learner's own rows."""
        owned = {
            "Training Assignment",
            "Training Completion",
            "Training Certificate",
            "Training Submission",
        }
        links = {
            row["link_to"]
            for row in self.workspace("my_training").get("links") or []
            if row.get("type") == "Link"
        }
        self.assertTrue(links)
        self.assertEqual(links - owned, set(), f"{links - owned} are not learner-scoped")

    def test_both_workspaces_bump_modified(self):
        """Workspaces are TIMESTAMP-gated by the importer, unlike DocTypes, which are
        hash-gated. A file that does not read newer than the stored row is skipped in
        silence -- the failure that stranded this workspace's cards for five weeks."""
        for folder in ("training", "my_training"):
            with self.subTest(folder):
                self.assertGreaterEqual(self.workspace(folder)["modified"], "2026-09-13")

    def test_the_resync_patch_is_registered(self):
        """Bumping `modified` is enough on a site whose rows are older; the patch is
        what makes it land on a row somebody has rearranged in the Desk since."""
        patches = (APP / "patches.txt").read_text(encoding="utf-8")
        self.assertIn("resync_training_workspace_split", patches)

    def test_the_assignment_form_and_list_are_registered(self):
        """A form script hooks.py does not list never loads, which makes the button
        true in the repo and absent in the desk."""
        hooks = (APP / "hooks.py").read_text(encoding="utf-8")
        self.assertIn("training/training_assignment.js", hooks)
        self.assertIn("training/training_assignment_list.js", hooks)

    def test_the_entry_points_route_rather_than_link(self):
        """`/app` is a website_redirect to `/desk` in v16, so a hand-built href is not
        intercepted by the router: it costs a full reload plus a redirect hop. Every
        in-desk navigation goes through frappe.set_route."""
        for name in ("training_assignment.js", "training_assignment_list.js"):
            with self.subTest(name):
                text = strip_comments((PLAYER_DIR / name).read_text(encoding="utf-8"))
                self.assertIn('frappe.set_route("learn"', text)
                self.assertNotIn('href="/app/', text)
                self.assertNotIn('href="/desk/', text)


class TestTheLessonPreview(unittest.TestCase):
    """One button that opens the one preview, and deliberately not a fourth host.

    The obvious version of this feature mounts the player in a tab on the Lesson
    form. That would need the draft's learner payload as JSON, which no endpoint
    returns -- `/training_preview` builds it server-side with `_split_lesson` and
    renders it into the template. Getting it client-side means rebuilding it in
    JavaScript, which is exactly the ~640 lines the classic builder carried and the
    canvas port deliberately did not.
    """

    SCRIPT = PLAYER_DIR / "training_lesson.js"

    def test_it_is_registered(self):
        self.assertTrue(self.SCRIPT.is_file())
        self.assertIn("training/training_lesson.js", (APP / "hooks.py").read_text(encoding="utf-8"))

    def test_it_opens_the_existing_preview(self):
        text = strip_comments(self.SCRIPT.read_text(encoding="utf-8"))
        self.assertIn("/training_preview?course=", text)

    def test_it_mounts_no_player_of_its_own(self):
        """A fourth host of TR.Player is a fourth place for the two to drift."""
        text = strip_comments(self.SCRIPT.read_text(encoding="utf-8"))
        for token in ("TR.Player", "TR.makeTransport", "TR.loadAssets"):
            with self.subTest(token):
                self.assertNotIn(token, text)

    def test_it_resolves_the_course_rather_than_assuming_one(self):
        """The preview is addressed by COURSE because that is what resolves an open
        draft; the lesson knows only its course_version. Reading it beats storing a
        denormalised course on the lesson, which would be a second place for it to
        be wrong."""
        text = strip_comments(self.SCRIPT.read_text(encoding="utf-8"))
        self.assertIn("course_version", text)


class TestBackAndForwardWalkTheSteps(unittest.TestCase):
    """Browser Back returns to the previous screen and Forward restores it (v1.535.0).

    The textual asserts above could not see the bug this replaces, and it passed them
    all: the player reports the lesson it last had even on the course outline, and
    `position_key` fell back to it, so the outline keyed as `lesson|C|L` while its URL
    keyed as `course|C|`. "← Course" and Resume pushed nothing, Forward from the
    outline did nothing, an in-progress course loaded twice, and the quiz shared its
    lesson's entry so Back skipped the lesson. The checks that matter are executed, in
    `scripts/test_training_desk_history.mjs`, against the real learn.js and the real
    routing half of player.js under a fake of the v16 router.
    """

    def test_one_shape_is_keyed(self):
        """The fallback that made the two sides disagree must not come back."""
        body = code().split("position_key(target) {", 1)[1][:600]
        self.assertNotIn("target.lesson ||", body)
        self.assertIn("this.player_target(next)", code())

    def test_the_quiz_has_a_route_of_its_own(self):
        self.assertIn('route[3] === "quiz"', code())
        self.assertIn('parts.push("quiz")', code())

    def test_the_outline_resumes_only_its_own_course(self):
        """`state.resume` is the boot payload's: `_resume` names the learner's most
        recent attempt, ONE course. Read on every outline, course B's footer offered a
        blank "Resume: " that opened course A's lesson under B, minted an attempt on B
        and then failed with "That lesson is not part of this course"."""
        player = strip_comments((PLAYER_DIR / "player.js").read_text(encoding="utf-8"))
        body = player.split("function renderCourse() {", 1)[1].split("function rowFor(", 1)[0]
        self.assertIn("state.resume.course === state.courseName", body)
        self.assertNotIn("(state.resume && state.resume.lesson_key) ||", body)

    # Skipped where node is genuinely absent -- a laptop without it -- but never in CI.
    # This wrapper is the only thing that runs the executed checks there, and a skip
    # reports OK: a runner image without node would pass them all without running one.
    @unittest.skipUnless(shutil.which("node") or os.environ.get("CI"), "node is not on PATH")
    def test_the_executed_harness_passes(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "no node on PATH in CI: the Back/Forward harness did not run")
        result = subprocess.run(
            [node, str(HISTORY_HARNESS), "learn"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(REPO),
            timeout=120,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"\b([1-9]\d*)/\1 passed")


if __name__ == "__main__":
    unittest.main()
