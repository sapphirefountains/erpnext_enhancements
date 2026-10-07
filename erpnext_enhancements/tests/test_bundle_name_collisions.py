"""No bundle of ours may share an assets.json name with frappe's or erpnext's.

frappe's build writes ONE flat ``sites/assets/assets.json`` for every app on the
bench, keyed by nothing but the bundle's file name (frappe v16.50.0
``esbuild/esbuild.js`` ``write_assets_json``: ``key = path.basename(info.entryPoint)``),
and every lookup goes through that key — ``app_include_js``, ``include_script``,
``frappe.require("x.bundle.js")``. Two apps shipping the same name do not fail the
build or warn (the duplicate check compares ``<app>/dist/...`` output paths, which
never collide across apps); the second one written simply replaces the first.

That bit us in v1.574.2. Our Kanban patch suite was ``kanban.bundle.js``, and
frappe v16.50.0 shipped its own ``kanban.bundle.js`` — the Kanban v2 engine, which
``list_factory.js`` loads with ``frappe.require("kanban.bundle.js")`` for the six
boards that use it. Whichever app built last owned the key: either those boards
could not construct ``frappe.views.KanbanV2View``, or every desk page loaded the v2
engine and our hold-to-drag, Opportunity styling and scroll fixes never loaded at
all. Nothing errored either way. Ours is ``ee_kanban.bundle.js`` now.

CI has no frappe or erpnext checkout, so the upstream names are a pinned list
(``UPSTREAM_BUNDLE_KEYS``). **Refresh it on every frappe/erpnext upgrade**, from a
checkout of each app at the tag being deployed — the list is only as current as
the tag it was read from::

    git -C <frappe checkout> ls-tree -r --name-only <tag> -- frappe/public \\
        | grep -E '\\.bundle\\.(js|ts|jsx|css|scss|sass|less|styl)$' \\
        | grep -v -e /node_modules/ -e /dist/ | xargs -n1 basename \\
        | sed -E 's/\\.(scss|sass|less|styl)$/.css/'

and the same for ``erpnext`` (``erpnext/public``) and any other app on the bench.
The ``sed`` is the key rule: a style entry builds to ``.css`` (``desk.bundle.scss``
is the key ``desk.bundle.css``); a script keeps its own extension.

Run: python -m unittest erpnext_enhancements.tests.test_bundle_name_collisions
"""

import ast
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
PUBLIC = APP / "public"
HOOKS = APP / "hooks.py"

# frappe v16.50.0 esbuild.js get_all_files_to_build(): <app>/public/**/*.bundle.{...},
# minus public/node_modules and public/dist.
BUNDLE_SUFFIXES = (".js", ".ts", ".jsx", ".css", ".scss", ".sass", ".less", ".styl")
STYLE_SUFFIXES = (".css", ".scss", ".sass", ".less", ".styl")

# The assets.json keys each upstream app's bundles build to. Taken with the command
# in the module docstring; update the tag in each comment when you refresh a list.
UPSTREAM_BUNDLE_KEYS = {
    # frappe v16.50.0 (frappe/public)
    "frappe": (
        "arrangement_editor.bundle.js",
        "billing.bundle.js",
        "bootstrap-4-web.bundle.js",
        "calendar.bundle.js",
        "controls.bundle.js",
        "data_import_tools.bundle.js",
        "data_import_wizard.bundle.js",
        "desk.bundle.js",
        "desktop_icons.bundle.js",
        "dialog.bundle.js",
        "doctype_settings.bundle.js",
        "embedded_list.bundle.js",
        "form.bundle.js",
        "form_builder.bundle.js",
        "frappe-web.bundle.js",
        "build_events.bundle.js",
        "file_uploader.bundle.js",
        "kanban_board.bundle.js",
        "kanban.bundle.js",  # views/kanban_v2 -- new in 16.50.0, and the one that collided
        "kanban_settings.bundle.js",
        "leaflet.bundle.js",
        "libs.bundle.js",
        "list.bundle.js",
        "list_filter.bundle.js",
        "list_view_virtualization.bundle.js",
        "logtypes.bundle.js",
        "onboarding_tours.bundle.js",
        "photoswipe.bundle.js",
        "print.bundle.js",
        "print_format_builder.bundle.js",
        "report.bundle.js",
        "sentry.bundle.js",
        "side_panel.bundle.js",
        "syntax_highlighting.bundle.js",
        "telemetry.bundle.js",
        "user_settings_dialog.bundle.js",
        "video_player.bundle.js",
        "web_form.bundle.js",
        "workflow_builder.bundle.js",
        "desk.bundle.css",
        "email.bundle.css",
        "leaflet.bundle.css",
        "login.bundle.css",
        "print.bundle.css",
        "print_format.bundle.css",
        "report.bundle.css",
        "web_form.bundle.css",
        "website.bundle.css",
    ),
    # erpnext v16.50.0 (erpnext/public)
    "erpnext": (
        "bank-reconciliation-tool.bundle.js",
        "bom_configurator.bundle.js",
        "erpnext.bundle.js",
        "item-dashboard.bundle.js",
        "point-of-sale.bundle.js",
        "erpnext-web.bundle.css",
        "erpnext.bundle.css",
        "erpnext_email.bundle.css",
    ),
    # The other apps on the production bench (tabInstalled Application, 2026-10-06), read
    # from the local mirror's checkouts: newsletter 33ebdf9. frappe_assistant_core v3.0.0,
    # payments cca07d9 and telephony 039cf39 ship no bundles. assets.json is bench-wide,
    # so their names are as off-limits as frappe's.
    "newsletter": (
        "newsletter.bundle.js",
        "newsletter-web.bundle.js",
    ),
}

# Shape check for the pinned list: an assets.json key, never a path or a source suffix.
KEY_SHAPE = re.compile(r"^[A-Za-z0-9_.-]+\.bundle\.(js|ts|jsx|css)$")


def assets_key(path):
    """The assets.json key frappe's build gives the entry point at ``path``.

    A style entry is compiled to ``<stem>.css`` in a temp dir before esbuild sees
    it, and the key is that file's name; a script entry keeps its own name.
    """
    if path.suffix in STYLE_SUFFIXES:
        return path.stem + ".css"
    return path.name


def our_bundles():
    """``{assets.json key: [source paths]}`` for every bundle entry this app ships."""
    found = {}
    for path in sorted(PUBLIC.rglob("*.bundle.*")):
        relative = path.relative_to(PUBLIC)
        if relative.parts[0] in ("node_modules", "dist") or path.suffix not in BUNDLE_SUFFIXES:
            continue
        if not path.is_file():
            continue
        found.setdefault(assets_key(path), []).append(path.relative_to(REPO_ROOT).as_posix())
    return found


def upstream_owner(key):
    return [app for app, keys in UPSTREAM_BUNDLE_KEYS.items() if key in keys]


def hooks_bundle_names():
    """Every string literal in hooks.py that names a ``*.bundle.js/css`` asset."""
    tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.strip()
            if re.fullmatch(r"[\w.-]+\.bundle\.(js|css)", value):
                names.add(value)
    return names


class TestTheInputsAreReal(unittest.TestCase):
    """Anti-vacuity: an empty side of the comparison makes every check below pass."""

    def test_our_bundles_are_found(self):
        ours = our_bundles()
        self.assertGreaterEqual(len(ours), 15, f"only {sorted(ours)} found under {PUBLIC}")
        # One of each kind, including the .scss entry whose key is not its file name.
        for key in ("erpnext_enhancements.bundle.js", "desk_addons.bundle.css", "ee_kanban.bundle.js"):
            self.assertIn(key, ours)

    def test_the_upstream_list_is_plausible(self):
        frappe = UPSTREAM_BUNDLE_KEYS["frappe"]
        self.assertGreaterEqual(len(frappe), 40)
        for key in ("desk.bundle.js", "form.bundle.js", "desk.bundle.css", "kanban.bundle.js"):
            self.assertIn(key, frappe)
        self.assertIn("erpnext.bundle.js", UPSTREAM_BUNDLE_KEYS["erpnext"])

    def test_the_upstream_list_holds_keys_not_paths(self):
        for app, keys in UPSTREAM_BUNDLE_KEYS.items():
            for key in keys:
                self.assertRegex(key, KEY_SHAPE, f"{app}: {key!r} is not an assets.json key")
            self.assertEqual(len(keys), len(set(keys)), f"{app}: duplicate entries")

    def test_the_key_rule_matches_what_hooks_references(self):
        """desk_addons.bundle.scss is referenced as desk_addons.bundle.css."""
        self.assertEqual(assets_key(Path("x/desk_addons.bundle.scss")), "desk_addons.bundle.css")
        self.assertEqual(assets_key(Path("x/ee_kanban.bundle.js")), "ee_kanban.bundle.js")
        self.assertIn("desk_addons.bundle.css", hooks_bundle_names())


class TestNoBundleNameIsShared(unittest.TestCase):
    def test_no_bundle_of_ours_shares_a_name_with_an_upstream_bundle(self):
        clashes = [
            f"{source} builds to {key!r}, which {' and '.join(upstream_owner(key))} "
            f"{'also ships' if len(upstream_owner(key)) == 1 else 'also ship'}"
            for key, sources in our_bundles().items()
            if upstream_owner(key)
            for source in sources
        ]
        self.assertEqual(
            clashes,
            [],
            "assets.json holds ONE entry per bundle file name across every app on the bench, "
            "so one of each pair below is unreachable, silently. Rename ours (an ee_ prefix) "
            "and update hooks.py and every frappe.require of it.",
        )

    def test_no_two_bundles_of_ours_share_a_name(self):
        """``a/x.bundle.js`` and ``b/x.bundle.js``, or ``x.bundle.css`` beside ``x.bundle.scss``."""
        shared = {key: sources for key, sources in our_bundles().items() if len(sources) > 1}
        self.assertEqual(shared, {})


class TestHooksNameOurOwnBundles(unittest.TestCase):
    def test_every_bundle_hooks_includes_is_one_of_ours(self):
        """A stale name here does not 404 — it resolves to SOMEONE ELSE's bundle.

        After the rename, a hooks.py still listing ``kanban.bundle.js`` would load
        frappe's Kanban v2 engine on every desk page and none of our patches.
        """
        ours = our_bundles()
        foreign = sorted(name for name in hooks_bundle_names() if name not in ours)
        self.assertEqual(
            foreign,
            [],
            "hooks.py names a bundle this app does not ship"
            + "".join(f"\n  {name}: {', '.join(upstream_owner(name)) or 'nobody'} ships it" for name in foreign),
        )


if __name__ == "__main__":
    unittest.main()
