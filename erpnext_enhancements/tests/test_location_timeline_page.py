"""The Location Timeline desk page — Time Kiosk overhaul (v1.480.0).

A desk page is a JavaScript file nothing in CI executes, wired to a JSON record
nothing in CI installs, dialling endpoints nothing in CI serves. Every failure
mode is therefore silent until a manager opens the page: a role the JSON grants
that the API refuses (the page renders, the data does not), an endpoint name
typed once wrongly (a 404 behind a spinner), a `?v=` token on a `frappe.require`
path (loads as nothing — desk_assets.js), a live poll that keeps running in a
background tab for the rest of the day. Static reads over the source are the only
check that runs before a browser does, so that is what this is.

Bench-free: no ``frappe`` stub, nothing imported from the app. Runs as its own
CI step.

Run: python -m unittest erpnext_enhancements.tests.test_location_timeline_page
"""

import ast
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
PAGE_DIR = APP / "workforce/page/location_timeline"
PAGE_JS = PAGE_DIR / "location_timeline.js"
PAGE_JSON = PAGE_DIR / "location_timeline.json"
PAGE_CSS = APP / "public/css/workforce/location_timeline.css"
EMPLOYEE_JS = APP / "public/js/employee.js"
API = APP / "api/time_kiosk.py"

# The day Projects Manager joined the page's roles. The JSON's `modified` must be
# at or after it, or `bench migrate` skips the file as older than the row on prod
# and the role change never lands (frappe/modules/import_file.py compares them).
ROLES_CHANGED_ON = "2026-09-17"

LIGHT_TILES = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
DARK_TILES = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"


def _text(path):
    return path.read_text(encoding="utf-8")


def _js(path):
    """JS with comments stripped. An assertion about what the code does must not
    be satisfied by the comment describing it; the header of this page names
    every endpoint and both tile hosts in prose."""
    src = re.sub(r"/\*.*?\*/", "", _text(path), flags=re.S)
    return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def _timeline_manager_roles():
    """`TIMELINE_MANAGER_ROLES` as the API defines it, read through the AST so a
    set written as a literal is compared as a set rather than as text."""
    tree = ast.parse(_text(API))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "TIMELINE_MANAGER_ROLES":
                return set(ast.literal_eval(node.value))
    raise AssertionError("TIMELINE_MANAGER_ROLES not found in api/time_kiosk.py")


def _whitelisted():
    tree = ast.parse(_text(API))
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if getattr(target, "attr", None) == "whitelist":
                names.add(node.name)
    return names


def _dialled(js):
    return set(re.findall(r"erpnext_enhancements\.api\.time_kiosk\.(\w+)", js))


class TestTheRolesAgreeWithTheAPI(unittest.TestCase):
    """The page JSON decides who can open the page; the API decides who gets
    data. A role in one list and not the other is a page that renders and then
    says "not permitted" -- or an endpoint nobody can reach from the desk."""

    def test_page_roles_equal_the_api_manager_set(self):
        page = json.loads(_text(PAGE_JSON))
        page_roles = {row["role"] for row in page["roles"]}
        self.assertEqual(
            page_roles,
            _timeline_manager_roles(),
            "page JSON roles and api.time_kiosk.TIMELINE_MANAGER_ROLES have drifted",
        )

    def test_projects_manager_is_in(self):
        page = json.loads(_text(PAGE_JSON))
        self.assertIn("Projects Manager", {row["role"] for row in page["roles"]})

    def test_the_json_modified_stamp_was_bumped_with_the_role_change(self):
        page = json.loads(_text(PAGE_JSON))
        self.assertIn("modified", page)
        self.assertGreaterEqual(page["modified"][:10], ROLES_CHANGED_ON)
        self.assertGreaterEqual(page["modified"], page["creation"])

    def test_the_employee_form_button_is_gated_on_the_same_roles(self):
        """The button is drawn for the roles the page admits, and no others: a
        button that only opens a "not permitted" screen is worse than none."""
        body = _js(EMPLOYEE_JS)
        at = body.index("ee_location_timeline_action: function")
        block = body[at : body.index("ee_tier_review_action: function")]
        self.assertIn("frappe.user.has_role(", block)
        gate = re.search(r"frappe\.user\.has_role\(\[(.*?)\]\)", block, re.S)
        self.assertIsNotNone(gate, "the button must be gated on an explicit role list")
        roles = set(re.findall(r'"([^"]+)"', gate.group(1)))
        page_roles = {row["role"] for row in json.loads(_text(PAGE_JSON))["roles"]}
        self.assertEqual(roles, page_roles)
        # And the gate comes BEFORE the button is added, as an early return.
        self.assertLess(block.index("frappe.user.has_role("), block.index("add_custom_button"))
        self.assertIn('frappe.route_options = { employee: frm.doc.name }', block)
        self.assertIn('frappe.set_route("location-timeline")', block)

    def test_the_button_is_wired_into_refresh(self):
        body = _js(EMPLOYEE_JS)
        self.assertIn('frm.trigger("ee_location_timeline_action")', body)


class TestEveryEndpointThePageDialsExists(unittest.TestCase):
    def test_every_dialled_method_is_whitelisted(self):
        dialled = _dialled(_js(PAGE_JS))
        self.assertTrue(dialled, "the page dials nothing?")
        whitelisted = _whitelisted()
        for name in sorted(dialled):
            with self.subTest(endpoint=name):
                self.assertIn(
                    name,
                    whitelisted,
                    f"{name} is dialled by the page but is not a @frappe.whitelist() def in api/time_kiosk.py",
                )

    def test_the_four_contract_endpoints_are_dialled(self):
        dialled = _dialled(_js(PAGE_JS))
        for name in (
            "get_employees_for_timeline",
            "get_location_history",
            "get_live_positions",
            "export_location_history",
        ):
            with self.subTest(endpoint=name):
                self.assertIn(name, dialled)

    def test_the_export_opens_a_download_url_with_the_current_filters(self):
        body = _js(PAGE_JS)
        at = body.index("exportTrail(format)")
        block = body[at : at + 900]
        self.assertIn("/api/method/", block)
        self.assertIn("export_location_history", block)
        for key in ("employee", "from_datetime", "to_datetime", "format"):
            with self.subTest(param=key):
                self.assertIn(key, block)
        self.assertIn("window.open(", block)


class TestRouteOptions(unittest.TestCase):
    """The Job Interval form and the Employee form both open this page through
    frappe.route_options; a page that ignores them opens blank and the manager
    re-types what they were already looking at."""

    def test_the_page_reads_all_three_keys(self):
        body = _js(PAGE_JS)
        self.assertIn("frappe.route_options", body)
        at = body.index("async applyRouteOptions(opts) {")
        block = body[at : body.index("async setSilently(field, value) {")]
        for key in ("opts.employee", "opts.from_date", "opts.to_date"):
            with self.subTest(key=key):
                self.assertIn(key, block)

    def test_they_are_consumed_on_show_not_only_on_load(self):
        """on_page_load fires once per session; a manager who comes back from a
        second Job Interval arrives through on_page_show."""
        body = _js(PAGE_JS)
        self.assertIn("on_page_show = function", body)
        at = body.index("onShow() {")
        block = body[at : body.index("init() {")]
        self.assertIn("const opts = frappe.route_options", block)
        self.assertIn("frappe.route_options = null", block)


class TestTheMapFollowsTheDeskTheme(unittest.TestCase):
    def test_both_tile_urls_are_present(self):
        body = _js(PAGE_JS)
        self.assertIn(LIGHT_TILES, body)
        self.assertIn(DARK_TILES, body)

    def test_dark_is_chosen_from_the_data_theme_attribute(self):
        """The desk always stamps data-theme (light or dark, never absent), so
        that attribute -- not prefers-color-scheme -- is the truth here."""
        body = _js(PAGE_JS)
        self.assertIn("getAttribute('data-theme') === 'dark'", body)
        self.assertNotIn("prefers-color-scheme", body)

    def test_the_theme_switch_is_live(self):
        body = _js(PAGE_JS)
        at = body.index("new MutationObserver(")
        self.assertIn("attributeFilter: ['data-theme']", body[at : at + 300])

    def test_the_stylesheet_reads_the_desk_variables(self):
        css = _text(PAGE_CSS)
        for var in ("--bg-color", "--card-bg", "--border-color", "--text-color", "--text-muted"):
            with self.subTest(var=var):
                self.assertIn(f"var({var}", css)
        # And Leaflet's light-only chrome is overridden for the dark desk.
        self.assertIn('[data-theme="dark"] .lt-map', css)


class TestAssetsLoadThroughFrappeRequireWithBarePaths(unittest.TestCase):
    """On v16 frappe.require appends ?v=<build> to a bare /assets path itself,
    and a path that already carries a query string is loaded as neither css nor
    js -- frappe.assets.extn() takes the text after the last "?" as the
    extension. desk_assets.js documents the failure; this keeps it out of here."""

    def test_leaflet_comes_from_frappes_vendored_copy(self):
        body = _js(PAGE_JS)
        self.assertIn("'/assets/frappe/js/lib/leaflet/leaflet.js'", body)
        self.assertIn("'/assets/frappe/js/lib/leaflet/leaflet.css'", body)

    def test_the_stylesheet_is_required_and_exists(self):
        body = _js(PAGE_JS)
        self.assertIn("'/assets/erpnext_enhancements/css/workforce/location_timeline.css'", body)
        self.assertTrue(PAGE_CSS.is_file())
        self.assertIn("frappe.require(ASSETS", body)

    def test_no_asset_path_carries_its_own_version_token(self):
        body = _js(PAGE_JS)
        for path in re.findall(r"'(/assets/[^']+)'", body):
            with self.subTest(path=path):
                self.assertNotIn("?", path)


class TestLivePollingStopsWhenNobodyIsLooking(unittest.TestCase):
    """Thirty-second polling from a tab left open all day is a load the server
    pays for nothing. It runs while the tab is visible and this page is the one
    on screen, and not otherwise."""

    def test_visibilitychange_stops_and_resumes(self):
        body = _js(PAGE_JS)
        self.assertIn("visibilitychange", body)
        at = body.index("'visibilitychange'")
        block = body[at : at + 400]
        self.assertIn("document.visibilityState === 'hidden'", block)
        self.assertIn("stopLivePolling()", block)
        self.assertIn("startLivePolling()", block)

    def test_the_tick_itself_refuses_to_poll_a_hidden_or_foreign_page(self):
        """Belt and braces: even if an event is missed, the next tick notices."""
        body = _js(PAGE_JS)
        at = body.index("pollLive()")
        at = body.index("pollLive() {", at)
        block = body[at : at + 600]
        self.assertIn("document.visibilityState === 'hidden'", block)
        self.assertIn("pageIsCurrent()", block)
        self.assertIn("stopLivePolling()", block)

    def test_leaving_the_page_stops_it(self):
        body = _js(PAGE_JS)
        at = body.index(".on('hide'")
        self.assertIn("stopLivePolling()", body[at : at + 200])

    def test_stop_clears_the_interval(self):
        body = _js(PAGE_JS)
        at = body.index("stopLivePolling() {")
        self.assertIn("clearInterval(", body[at : at + 200])

    def test_the_interval_is_thirty_seconds(self):
        self.assertIn("LIVE_POLL_MS = 30000", _js(PAGE_JS))


class TestServerStringsAreEscaped(unittest.TestCase):
    def test_the_escape_helper_is_frappes(self):
        body = _js(PAGE_JS)
        self.assertIn("frappe.utils.escape_html(", body)

    def test_nothing_is_written_through_innerhtml_directly(self):
        body = _js(PAGE_JS)
        self.assertNotIn(".innerHTML", body)

    def test_the_labels_that_come_from_the_server_go_through_esc(self):
        """A spot check on the fields most likely to carry user-typed text."""
        body = _js(PAGE_JS)
        for fragment in (
            "esc(iv._label)",
            "esc(r.employee_name || r.employee)",
            "esc(iv.project_title || iv.project",
            "esc(who)",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, body)


class TestTheStatesHaveWords(unittest.TestCase):
    def test_empty_denied_loading_and_error_states_exist(self):
        body = _js(PAGE_JS)
        for kind in ("'empty'", "'denied'", "'loading'", "'error'"):
            with self.subTest(kind=kind):
                self.assertIn(f"renderState({kind}", body)

    def test_the_denied_state_names_the_roles(self):
        body = _js(PAGE_JS)
        at = body.index("renderState('denied'")
        self.assertIn("HR Manager, Projects Manager or System Manager", body[at : at + 400])


class TestTheTrailDrawsWhatTheContractDescribes(unittest.TestCase):
    """One assertion per visual the contract lists, on the token that draws it."""

    def test_low_accuracy_points_are_hollow(self):
        body = _js(PAGE_JS)
        self.assertIn("LOW_ACCURACY = 'Low Accuracy'", body)
        at = body.index("const low = p.log_status === LOW_ACCURACY")
        self.assertIn("fillOpacity: low ? 0 : 1", body[at : at + 500])

    def test_gaps_are_dashed_red(self):
        body = _js(PAGE_JS)
        at = body.index("drawGaps(iv) {")
        block = body[at : at + 1600]
        self.assertIn("GAP_COLOR", block)
        self.assertIn("dashArray", block)

    def test_stops_carry_duration_and_site(self):
        body = _js(PAGE_JS)
        at = body.index("drawStops(iv) {")
        block = body[at : at + 1200]
        self.assertIn("s.at_site", block)
        self.assertIn("permanent: true", block)

    def test_site_geofence_uses_the_payload_radius(self):
        body = _js(PAGE_JS)
        self.assertIn("iv._site.radius_m", body)

    def test_anchors_are_distinct_start_and_end_markers(self):
        body = _js(PAGE_JS)
        self.assertIn("lt-anchor-start", body)
        self.assertIn("lt-anchor-end", body)

    def test_playback_has_the_three_speeds_and_a_scrubber(self):
        body = _js(PAGE_JS)
        self.assertIn("SPEEDS = [1, 4, 16]", body)
        self.assertIn("seekFraction(", body)
        self.assertIn("togglePlay()", body)

    def test_the_side_panel_shows_the_five_day_totals(self):
        body = _js(PAGE_JS)
        for key in ("worked_seconds", "distance_m", "dwell_minutes", "travel_minutes", "gap_minutes"):
            with self.subTest(total=key):
                self.assertIn(f"totals.{key}", body)

    def test_the_cards_show_the_three_badges_and_the_health_pill(self):
        body = _js(PAGE_JS)
        at = body.index("intervalCard(iv) {")
        block = body[at : body.index("exportTrail(format) {")]
        for field in ("iv.offsite_start", "iv.offsite_end", "iv.auto_closed", "iv.corrected"):
            with self.subTest(field=field):
                self.assertIn(field, block)
        self.assertIn("healthPill(s.health)", block)

    def test_a_live_row_opens_todays_trail_for_that_person(self):
        body = _js(PAGE_JS)
        at = body.index("openTrailFor(idx) {")
        block = body[at : at + 1000]
        self.assertIn("setMode('trail')", block)
        self.assertIn("today()", block)
        self.assertIn("loadTrail(", block)


class TestTheStylesheetCoversWhatTheScriptEmits(unittest.TestCase):
    """An unstyled element is not an error; it just renders wrongly, and only a
    person looking at the page can tell. Every lt- class the script writes must
    have a rule."""

    def test_every_emitted_class_has_a_rule(self):
        js = _js(PAGE_JS)
        css = _text(PAGE_CSS)
        emitted = set(re.findall(r"\blt-[a-z0-9-]+", js))
        # Dynamic pill kinds are composed at runtime from these fixed prefixes.
        emitted -= {"lt-pill-", "lt-anchor-", "lt-state-"}
        emitted |= {
            "lt-pill-good", "lt-pill-gaps", "lt-pill-none", "lt-pill-off", "lt-pill-pending",
            "lt-pill-warn", "lt-pill-info", "lt-pill-muted", "lt-pill-stale", "lt-pill-live",
            "lt-state-empty", "lt-state-denied", "lt-state-loading", "lt-state-error",
        }
        styled = set(re.findall(r"\.(lt-[a-z0-9-]+)", css))
        missing = sorted(emitted - styled)
        self.assertEqual(missing, [], f"classes the script emits with no rule in the stylesheet: {missing}")

    def test_the_swatch_variable_is_set_by_the_script_and_read_by_the_css(self):
        self.assertIn("--lt-swatch:", _js(PAGE_JS))
        self.assertIn("var(--lt-swatch", _text(PAGE_CSS))


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestTheScriptsParse(unittest.TestCase):
    def test_node_check(self):
        for path in (PAGE_JS, EMPLOYEE_JS):
            with self.subTest(file=path.name):
                result = subprocess.run(
                    [shutil.which("node"), "--check", str(path)], capture_output=True, text=True
                )
                self.assertEqual(result.returncode, 0, result.stderr)


class TestThePageActuallyReachesTheSite(unittest.TestCase):
    """A Page JSON is age-gated on import exactly like a Workspace: `import_file` skips the
    file when the database row is not older than the file's `modified`. v1.480.0 shipped
    Projects Manager in this JSON stamped 09:00:00 on a day the prod row already read
    14:12:58, and the deploy installed everything except this file -- nothing errored, the
    page simply kept its old roles. Two halves stop that recurring: a stamp newer than any
    row the site held, and a forced reload patch for the edit that forgets the stamp."""

    PATCH = APP / "patches" / "reload_location_timeline_page.py"
    PATCHES_TXT = APP / "patches.txt"
    GATED_ROW = "2026-09-17 14:12:58"

    def test_the_stamp_is_newer_than_the_row_that_gated_it(self):
        record = json.loads(PAGE_JSON.read_text(encoding="utf-8"))
        self.assertIn("modified", record, "a Page JSON with no `modified` is skipped as 'not older'")
        self.assertGreater(record["modified"], self.GATED_ROW)

    def test_the_forced_reload_patch_exists_and_targets_this_page(self):
        source = self.PATCH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "reload_doc"
        ]
        self.assertEqual(len(calls), 1, "exactly one reload_doc call")
        call = calls[0]
        self.assertEqual([a.value for a in call.args], ["workforce", "page", "location_timeline"])
        self.assertTrue(
            any(k.arg == "force" and k.value.value is True for k in call.keywords),
            "the reload must be force=True or the age gate applies to it too",
        )

    def test_the_patch_is_registered_after_the_model_sync(self):
        text = self.PATCHES_TXT.read_text(encoding="utf-8")
        post = text.split("[post_model_sync]", 1)[1]
        self.assertIn("erpnext_enhancements.patches.reload_location_timeline_page", post)


if __name__ == "__main__":
    unittest.main()
