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

Run: python -m unittest erpnext_enhancements.tests.test_kiosk_frontend
"""

import ast
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
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
        return set(re.findall(r"(/assets/erpnext_enhancements/(?:js|css)/kiosk/[\w./-]+)\?v=\{\{ deploy_version \}\}", html))

    def precache(self):
        code = strip_js_comments(WORKER.read_text(encoding="utf-8"))
        start = code.index("const PRECACHE = [")
        block = code[start : code.index("];", start)]
        return set(re.findall(r"'([^']+)'", block))

    def test_every_shell_asset_is_precached(self):
        missing = sorted(self.shell_assets() - self.precache())
        self.assertEqual(missing, [], f"loaded by kiosk.html but not precached: {missing}")

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


if __name__ == "__main__":
    unittest.main()
