"""Static contracts between the kiosk front end, its shell, its worker and its API.

Four things, none of which a browser would report as an error:

1. **No ``window.confirm`` / ``prompt`` / ``alert`` under ``public/js/kiosk/``.**
   Every interruption is an in-app bottom sheet (``ui.js``). A native dialog is
   the thing the overhaul removed — it blocks the clock, cannot be styled, and on
   an installed PWA reads as the app hanging. Comments are stripped before the
   search, because the comment explaining the rule names the very tokens.

2. **The worker's PRECACHE equals the shell's js/css URLs, both ways.** A file
   loaded by ``kiosk.html`` and missing from PRECACHE is not available offline —
   the app then half-loads in a dead zone, which is worse than not loading. A
   PRECACHE entry nothing loads is a cache-first, ``ignoreSearch`` answer for a
   URL that has no reason to exist (``tests/test_kiosk_service_worker.py`` says
   why that matters on a root-scope worker).

3. **Every ``erpnext_enhancements.api.time_kiosk.<name>`` the scripts dial is a
   ``@frappe.whitelist()`` def in ``api/time_kiosk.py``.** Parsed with ``ast`` so a
   name that exists but lost its decorator fails too. A typo here is a 404 the
   user sees as "Something went wrong" — after they tapped Clock Out.

4. **The shell keeps its contract**: ``#kiosk-root``, the three ``window.KIOSK_*``
   boot globals, a ``theme-color`` meta, ``?v={{ deploy_version }}`` on every
   mutable asset and none on the icons.

5. **Browser Back / Forward walk the tabs and close sheets, and change no URL.**
   ``ui.js`` is the only file that touches the History API, every call is
   two-argument (iOS Safari asks for camera and location again when the URL
   changes; the worker serves the offline shell for the exact path ``/kiosk``),
   and nothing traps Back. The behaviour itself is driven, not grepped, by
   ``scripts/test_kiosk_history.js``, which loads the real scripts over a fake
   DOM and history; it runs from here so it runs wherever this suite does.

Run: python -m unittest erpnext_enhancements.tests.test_kiosk_frontend
"""

import ast
import re
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
HISTORY_HARNESS = APP.parent / "scripts" / "test_kiosk_history.js"
JS_DIR = APP / "public" / "js" / "kiosk"
CSS_DIR = APP / "public" / "css" / "kiosk"
HTML = APP / "www" / "kiosk.html"
WORKER = APP / "www" / "kiosk-sw.js"
API = APP / "api" / "time_kiosk.py"

API_PREFIX = "erpnext_enhancements.api.time_kiosk."


def strip_js_comments(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def js_files():
    return sorted(JS_DIR.glob("*.js"))


def whitelisted_names():
    tree = ast.parse(API.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Attribute) and target.attr == "whitelist":
                names.add(node.name)
            elif isinstance(target, ast.Name) and target.id == "whitelist":
                names.add(node.name)
    return names


def dialled_names():
    """Every endpoint the scripts reference, whether written whole or as
    ``API + 'name'`` / ``ctx.API + 'name'`` (app.js and the view modules do both)."""
    names = {}
    for path in js_files():
        code = strip_js_comments(path.read_text(encoding="utf-8"))
        for m in re.finditer(re.escape(API_PREFIX) + r"(\w+)", code):
            names.setdefault(m.group(1), set()).add(path.name)
        for m in re.finditer(r"\bAPI \+ '(\w+)'", code):
            names.setdefault(m.group(1), set()).add(path.name)
    return names


class TestTheInputsExist(unittest.TestCase):
    """Anti-vacuity for the sweeps below."""

    def test_the_scripts_are_there(self):
        names = {p.name for p in js_files()}
        for required in ("ui.js", "geo.js", "app.js", "myday.js", "map.js", "settings.js"):
            self.assertIn(required, names)

    def test_the_api_is_parseable_and_whitelists_something(self):
        self.assertGreater(len(whitelisted_names()), 10)

    def test_the_scripts_dial_something(self):
        self.assertGreater(len(dialled_names()), 10)


class TestNoNativeDialogs(unittest.TestCase):
    PATTERN = re.compile(r"(?<![\w.$])(?:window\.)?(confirm|prompt|alert)\s*\(")

    def test_no_window_confirm_prompt_or_alert(self):
        offenders = []
        for path in js_files():
            code = strip_js_comments(path.read_text(encoding="utf-8"))
            for m in self.PATTERN.finditer(code):
                line = code.count("\n", 0, m.start()) + 1
                offenders.append(f"{path.name}:{line} {m.group(0).strip()}")
        self.assertEqual(offenders, [], "native dialogs are replaced by KioskUI sheets: " + ", ".join(offenders))

    def test_the_replacements_are_used(self):
        code = strip_js_comments((JS_DIR / "app.js").read_text(encoding="utf-8"))
        self.assertIn("UI.sheet.open(", code)
        self.assertIn("UI.ask(", code)
        self.assertIn("UI.askText(", code)


class TestPrecacheMatchesTheShell(unittest.TestCase):
    def shell_assets(self):
        html = HTML.read_text(encoding="utf-8")
        # Deliberately NOT limited to js/kiosk/ or css/kiosk/. v1.481.0 added a
        # shell <script> from js/global_enhancements/ (the shared Google Maps
        # loader), and the narrower pattern simply did not see it -- so the
        # "every shell asset is precached" guard passed while the file was
        # missing from PRECACHE, which offline is a 504 in a dead zone. Match
        # any of this app's js/css assets the shell loads.
        return set(re.findall(r"(/assets/erpnext_enhancements/(?:js|css)/[\w./-]+)\?v=\{\{ deploy_version \}\}", html))

    def precache(self):
        code = strip_js_comments(WORKER.read_text(encoding="utf-8"))
        start = code.index("const PRECACHE = [")
        block = code[start : code.index("];", start)]
        return set(re.findall(r"'([^']+)'", block))

    # Deliberately network-only, and NOT a hole in the rule above.
    #
    # The service worker is registered at ROOT scope, so it may only ever answer
    # for the kiosk's own shell -- precaching an asset the desk also serves would
    # put this worker in front of desk traffic. The shared Google Maps loader
    # lives in js/global_enhancements/ for exactly that reason (the desk bundle
    # imports it too), so it cannot be precached here.
    #
    # It costs nothing: the loader's only job is to fetch the Google Maps API,
    # which is third-party, versioned and uncacheable. A device offline enough to
    # be missing this file could not render a map anyway, and the map tab says so.
    # This is the same call the vendored Leaflet copy got before v1.481.0.
    NOT_PRECACHED = {
        "/assets/erpnext_enhancements/js/global_enhancements/google_maps_loader.js",
    }

    def test_every_shell_asset_is_precached(self):
        missing = sorted(self.shell_assets() - self.precache() - self.NOT_PRECACHED)
        self.assertEqual(missing, [], f"loaded by kiosk.html but not precached: {missing}")

    def test_the_precache_exemptions_are_actually_loaded_by_the_shell(self):
        """An exemption must name a file the shell really loads, or it is a typo
        quietly widening the rule it was written to narrow."""
        stale = sorted(self.NOT_PRECACHED - self.shell_assets())
        self.assertEqual(stale, [], f"exempted but nothing loads them: {stale}")

    def test_every_precached_script_or_stylesheet_is_loaded(self):
        precached = {p for p in self.precache() if "/js/kiosk/" in p or "/css/kiosk/" in p}
        stale = sorted(precached - self.shell_assets())
        self.assertEqual(stale, [], f"precached but nothing loads them: {stale}")

    def test_every_script_on_disk_is_loaded(self):
        on_disk = {f"/assets/erpnext_enhancements/js/kiosk/{p.name}" for p in js_files()}
        on_disk |= {f"/assets/erpnext_enhancements/css/kiosk/{p.name}" for p in CSS_DIR.glob("*.css")}
        orphaned = sorted(on_disk - self.shell_assets())
        self.assertEqual(orphaned, [], f"files under kiosk/ that the shell never loads: {orphaned}")

    def test_the_shell_versions_every_mutable_asset(self):
        html = HTML.read_text(encoding="utf-8")
        unversioned = [
            m.group(1)
            for m in re.finditer(r'(?:src|href)="(/assets/erpnext_enhancements/(?:js|css)/kiosk/[^"?]+)"', html)
        ]
        self.assertEqual(unversioned, [], f"mutable assets without ?v=: {unversioned}")
        for icon in re.findall(r'href="(/assets/erpnext_enhancements/kiosk/icons/[^"]+)"', html):
            self.assertNotIn("?v=", icon, "icons are unversioned on purpose — their content never changes")

    def test_leaflet_is_not_precached(self):
        for entry in self.precache():
            self.assertNotIn("leaflet", entry, "the vendored library is loaded lazily, never by a root-scope worker")


class TestEveryDialledEndpointIsWhitelisted(unittest.TestCase):
    def test_every_endpoint_exists_and_is_whitelisted(self):
        allowed = whitelisted_names()
        missing = {name: sorted(files) for name, files in dialled_names().items() if name not in allowed}
        self.assertEqual(
            missing,
            {},
            "dialled by the kiosk scripts but not a @frappe.whitelist() def in api/time_kiosk.py: "
            + ", ".join(f"{k} ({', '.join(v)})" for k, v in sorted(missing.items())),
        )

    def test_the_new_views_dial_their_endpoints(self):
        dialled = dialled_names()
        for name in (
            "get_my_day", "get_my_history", "get_my_trail", "get_shift_summary",
            "submit_correction_request", "get_my_correction_requests", "cancel_correction_request",
        ):
            self.assertIn(name, dialled, f"{name} is part of the overhaul contract and nothing dials it")


class TestTheShellKeepsItsContract(unittest.TestCase):
    def html(self):
        return HTML.read_text(encoding="utf-8")

    def test_mount_point_and_boot_globals(self):
        html = self.html()
        self.assertIn('id="kiosk-root"', html)
        for glob in ("window.KIOSK_BOOT", "window.KIOSK_CSRF", "window.KIOSK_BUILD"):
            self.assertIn(glob, html)

    def test_theme_color_meta_exists(self):
        self.assertRegex(self.html(), r'<meta name="theme-color" content="#[0-9a-fA-F]{6}">')

    def test_load_order(self):
        html = self.html()
        order = [html.index(f"/js/kiosk/{n}") for n in ("ui.js", "geo.js", "myday.js", "map.js", "settings.js", "app.js")]
        self.assertEqual(order, sorted(order), "ui.js first, app.js last — app.js mounts the view modules")

    def test_the_worker_answers_stats(self):
        code = strip_js_comments(WORKER.read_text(encoding="utf-8"))
        self.assertIn("type === 'stats'", code)
        self.assertIn("port.postMessage({ queued })", code)

    def test_geo_sends_a_fix_source_and_has_the_anchor(self):
        code = strip_js_comments((JS_DIR / "geo.js").read_text(encoding="utf-8"))
        self.assertIn("fix_source:", code)
        for source in ("'Watch'", "'Heartbeat'", "'Catch-up'"):
            self.assertIn(source, code)
        self.assertIn("anchorFix:", code)
        self.assertIn("getDiagnostics:", code)
        for status in ("'insecure'", "'unavailable'", "'hidden'", "'denied'", "'ready'", "'on'", "'off'"):
            self.assertIn(status, code)


def call_arguments(code, name):
    """The top-level arguments of every ``name(...)`` call in ``code``, as source strings."""
    calls = []
    for m in re.finditer(re.escape(name) + r"\s*\(", code):
        depth, start, args = 1, m.end(), []
        i = start
        while i < len(code) and depth:
            ch = code[i]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == "," and depth == 1:
                args.append(code[start:i].strip())
                start = i + 1
            i += 1
        args.append(code[start : i - 1].strip())
        calls.append([a for a in args if a])
    return calls


class TestBrowserHistory(unittest.TestCase):
    """Back / Forward walk the tabs and close sheets (ui.js, "History"). The rules here
    are the ones a quiet edit breaks without anything looking wrong on a desktop.

    Read raw, not through ``strip_js_comments``: that stripper does not know strings,
    and app.js's ``accept: 'image/*'`` opens a "comment" that swallows half the file.
    So every search below is for a call shape that prose does not produce."""

    HISTORY_CALLS = re.compile(
        r"\.(?:pushState|replaceState)\s*\(|['\"]popstate['\"]|\bhistory\.(?:back|forward|go)\s*\("
    )

    def code(self, name):
        return (JS_DIR / name).read_text(encoding="utf-8")

    def test_only_ui_js_touches_the_history(self):
        """One owner. A second pushState anywhere else lands between the marker and the
        entry its back() is aimed at, and the next Back closes the wrong thing."""
        owners = sorted(p.name for p in js_files() if self.HISTORY_CALLS.search(self.code(p.name)))
        self.assertEqual(owners, ["ui.js"])

    def test_no_history_call_carries_a_url(self):
        """iOS Safari asks for camera and location again when the URL changes, and
        kiosk-sw.js serves the offline shell for the exact path /kiosk."""
        code = self.code("ui.js")
        seen = 0
        for name in ("history.pushState", "history.replaceState"):
            for args in call_arguments(code, name):
                seen += 1
                self.assertEqual(len(args), 2, f"{name}({', '.join(args)}) must be two-argument: no URL")
        self.assertGreaterEqual(seen, 2, "anti-vacuity: the history layer's calls were not found")

    URL_WRITES = (r"location\.hash\s*=", r"location\.href\s*=", r"location\.(?:assign|replace)\s*\(")

    def test_the_url_is_never_written(self):
        for path in js_files():
            code = self.code(path.name)
            for pattern in self.URL_WRITES:
                self.assertIsNone(re.search(pattern, code), f"{path.name}: {pattern}")

    def test_nothing_traps_back(self):
        """Back from the first entry must still leave the page: no leave-page prompt."""
        for path in js_files():
            self.assertNotIn("beforeunload", self.code(path.name), path.name)

    def test_the_report_panel_is_left_its_own_back(self):
        """capture/panel.js owns its entry: popstate is its while ee_capture.isOpen(), and
        an entry carrying its key is never read as one of the kiosk's screens."""
        code = self.code("ui.js")
        self.assertIn("typeof cap.isOpen === 'function' && cap.isOpen()", code)
        self.assertIn("'ee_capture' in s", code)

    def test_only_a_tap_pushes_a_tab_entry(self):
        """Chrome's history intervention: a push with no tap behind it marks every entry of
        the page skippable, and the next Back leaves the app. A tab entry is pushed only for
        a tab tap; any other disagreement between screen and entry re-stamps the entry."""
        code = self.code("ui.js")
        self.assertIn("writeEntry(nav.tapped, false);", code)
        self.assertNotIn("writeEntry(true, false)", code, "a tab entry pushed with no tap behind it")

    def test_app_js_pushes_from_the_tap_and_stamps_the_boot_entry(self):
        code = self.code("app.js")
        self.assertIn("function setTab(name, fromHistory) {", code)
        self.assertIn("if (!fromHistory && UI.nav) UI.nav.go(name);", code)
        self.assertIn("UI.nav.start('clock', TABS.map(", code)
        self.assertIn("function (name) { setTab(name, true); }", code)
        self.assertIn("setTab('clock', true);", code)
        self.assertNotIn("setTab('clock');", code, "boot must not push a Clock entry of its own")

    def test_the_settings_off_switch_turns_off_both_layers(self):
        """Time Kiosk Settings.disable_browser_back is the no-deploy way back if an iPhone
        re-prompts: app.js never starts the history layer (no listener, so ui.js pushes
        nothing for sheets either), and the template tells the report panel the same."""
        self.assertIn("if (UI.nav && !(+SETTINGS.disable_browser_back)) UI.nav.start('clock',", self.code("app.js"))
        self.assertIn("if (!nav.on || nav.backs) return;", self.code("ui.js"), "reconcile must do nothing before start")
        self.assertIn("history: {{ capture_history | tojson }}", HTML.read_text(encoding="utf-8"))
        kiosk_py = (APP / "www" / "kiosk.py").read_text(encoding="utf-8")
        self.assertIn('context.capture_history = not frappe.utils.cint((boot.get("settings") or {}).get("disable_browser_back"))', kiosk_py)
        settings_py = (APP / "workforce" / "doctype" / "time_kiosk_settings" / "time_kiosk_settings.py").read_text(encoding="utf-8")
        self.assertIn('"disable_browser_back": 0,', settings_py, "get_settings only returns keys listed in DEFAULTS")


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestBrowserHistoryBehaviour(unittest.TestCase):
    """Runs scripts/test_kiosk_history.js: the real scripts, a fake DOM and a fake history."""

    def test_the_history_harness_passes(self):
        result = subprocess.run(
            [shutil.which("node"), str(HISTORY_HARNESS)], capture_output=True, text=True, timeout=120
        )
        output = (result.stdout + result.stderr)[-4000:]
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("checks passed", result.stdout, output)


if __name__ == "__main__":
    unittest.main()
