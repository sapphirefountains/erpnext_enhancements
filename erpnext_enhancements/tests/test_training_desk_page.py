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
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
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
    """`roles` on the Page record is what decides who sees it, and it is being used
    deliberately as the staged-rollout control: System Manager first, then
    Training Learner once a person has opened it on a real bench.

    It is show/hide, never a permission boundary — a Desk Page has no server
    controller, and every endpoint the player dials re-checks. This test exists so
    that flipping the switch is a deliberate edit with a test to update, rather than
    something that happens by accident while editing something else.
    """

    def test_the_role_set_is_what_we_think_it_is(self):
        roles = sorted(row["role"] for row in doc().get("roles") or [])
        self.assertEqual(
            roles,
            ["System Manager"],
            "learn.json's roles changed. If this is the rollout switch being thrown, "
            "update this test in the same commit and say so in the CHANGELOG.",
        )


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


class TestItDoesNotOwnTheUrlYet(unittest.TestCase):
    """This release keeps `route()` at a zero-line diff: the Desk router owns the URL
    and the player neither reads nor writes it. Deep links still work, through the
    same `boot.start` the preview harness uses."""

    def test_history_is_off(self):
        self.assertRegex(code(), r"boot\.history\s*=\s*false")

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
        # tr-shell is the mount point itself — the portal template owns the identical
        # class, and player.css styles it. The host is allowed to build the mount.
        stray = sorted(c for c in classes if c.startswith("tr-") and c != "tr-shell")
        self.assertEqual(stray, [], f"{stray} are player classes invented by the host")

    def test_every_class_it_renders_has_a_rule(self):
        css = PAGE_CSS.read_text(encoding="utf-8")
        groups = set(re.findall(r'class="([^"]*)"', code()))
        classes = {cls for group in groups for cls in group.split()}
        missing = sorted(
            c for c in classes if c.startswith("tl-") and f".{c}" not in css
        )
        self.assertEqual(missing, [], f"{missing} are rendered with no rule in learn.css")


if __name__ == "__main__":
    unittest.main()
