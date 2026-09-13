# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Google Chat mirror and the coworker chat product are gone (ADR 0011, v1.426.0).

Nikolas's call on 2026-09-13: the integration was buggy and troublesome, and the
ERPNext-side chat product it existed to feed had no audience without the mirror. Staff keep
using Google Chat directly, on their phones, outside ERPNext. What ERPNext keeps is
**chatting with Triton** — the floating Desk widget, which was always a separate surface.

~58,000 lines of Python, ~12,500 of browser code, 23 DocTypes, 56 test suites and 58 CI
steps went in one release. That is exactly the shape of change where a sweep is worth more
than any individual assertion, so this file is mostly two sweeps and a survivor list.

-------------------------------------------------------------------------------------
The two halves, and why the survivor half is the longer one
-------------------------------------------------------------------------------------

Deleting a module is easy to verify and easy to over-do. The dangerous direction here was
never "did something get left behind" — a dangling import fails loudly at the next migrate.
It was **"did the deletion take a neighbour with it"**, and it very nearly did in three
places, every one of them silent:

* `public/js/chat/{citations,markdown,dom}.js` were imported by `triton_widget.js`, which
  ships in the GLOBAL Desk bundle. Deleting `public/js/chat/` wholesale makes esbuild fail
  to resolve, `bench build` fails, and the deploy has already run `bench migrate` by then.
  They were moved to `public/js/triton/` instead — `keys.js` being the one surviving
  function of `dom.js`.
* `triton_widget.css`'s "PHASE 3" block ran to end-of-file and looked deletable in one cut,
  but ~60 lines in the middle of it are decision #7's `.ee-citation` / `.triton-source`
  rules — Triton's own, still live on every answer it streams. Cutting the block wholesale
  strips citation styling off the surviving widget and nothing fails.
* `scripts/fuzz_url_safety.mjs` imports `isSafeUrl` from `citations.js` in a CI step that
  has nothing to do with chat.

So `TestTheTritonWidgetSurvives` is not decoration. It is the half that would have failed.

-------------------------------------------------------------------------------------
Absence is asserted over COMMENT-STRIPPED source
-------------------------------------------------------------------------------------

`hooks.py` now carries a dozen comments that name what was removed and why — the scheduler
minutes chat used to own, the second `website_route_rules` entry that was its twin, the
parity arithmetic of the two permission registers. Those comments are the point of the
change, not residue. A sweep that read raw text would flag every one of them, which is the
trap this project has hit repeatedly; `_py` and `_js` below strip comments and docstrings
first, exactly as `test_classic_builder_retirement` does.

Bench-free: filesystem and `ast` only.

Run: python -m unittest erpnext_enhancements.tests.test_chat_module_retirement
"""

import ast
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent

CHAT_DIR = APP / "chat"
CHAT_JS_DIR = APP / "public/js/chat"
MODULES_TXT = APP / "modules.txt"
HOOKS = APP / "hooks.py"
BOOT = APP / "boot.py"
GATE = APP / "assistant_tools/_gate.py"
PATCH = APP / "patches/delete_chat_module.py"
PATCHES_TXT = APP / "patches.txt"
WIDGET_JS = APP / "public/js/global_enhancements/triton_widget.js"
WIDGET_CSS = APP / "public/css/global_enhancements/triton_widget.css"
TRITON_JS_DIR = APP / "public/js/triton"

#: The dotted prefix nothing may import any more. Checked rather than the bare word "chat",
#: which legitimately appears all over the surviving Triton widget ("Triton Chat Attachment",
#: `triton_chat.py`, "chat history", every `LS_SESSION` comment).
DEAD_IMPORT = "erpnext_enhancements.chat"

#: Paths the deletion removed outright. Each was a whole feature, not a file.
DELETED_PATHS = (
    "chat",
    "public/js/chat",
    "public/js/chat.bundle.js",
    "public/css/chat.bundle.css",
    "public/js/global_enhancements/chat_surface.js",
    "www/chat.html",
    "www/chat.py",
    "www/chat_admin.html",
    "www/chat_admin.py",
    "www/chat-sw.js",
    "api/chat.py",
)

#: Every DocType the module owned, as the teardown patch must still name them. If this list
#: and the patch's ever disagree, a table survives on production with nothing describing it.
CHAT_DOCTYPES = (
    "Chat Allowed User",
    "Chat Attachment",
    "Chat Audit Log",
    "Chat Context Chunk",
    "Chat Drift Report",
    "Chat Event Subscription",
    "Chat Export Request",
    "Chat Inbound Event",
    "Chat Mention",
    "Chat Message",
    "Chat Message Revision",
    "Chat Ops Alert",
    "Chat Provisioning Run",
    "Chat Push Subscription",
    "Chat Relay Job",
    "Chat Retrieval Audit",
    "Chat Retrieval Audit Room",
    "Chat Room",
    "Chat Room Digest",
    "Chat Room Member",
    "Chat Thread Digest",
    "Triton Invocation Log",
    "Chat Settings",
)

#: The browser modules `triton_widget.js` still imports. Their absence is a failed
#: `bench build`, which lands AFTER `bench migrate` in the deploy.
SURVIVING_JS = ("citations.js", "markdown.js", "keys.js")

#: Decision #7's rules, which sat inside the CSS block that was otherwise deleted.
SURVIVING_CSS_RULES = (".ee-citation", ".triton-source-k", ".triton-source.is-cited")

SWEEP_SKIP_DIRS = {"node_modules", "tests", "__pycache__"}
SWEEP_SKIP_FILES = {"delete_chat_module.py"}


def _js(path):
    """JS/CSS/HTML with whole-line and block comments removed."""
    out, in_block = [], False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if in_block:
            if "*/" in stripped or "-->" in stripped:
                in_block = False
            continue
        if stripped.startswith("/*"):
            in_block = "*/" not in stripped
            continue
        if stripped.startswith("<!--"):
            in_block = "-->" not in stripped
            continue
        if stripped.startswith("//"):
            continue
        out.append(line)
    return "\n".join(out)


def _py(path):
    """Python source with every docstring removed, via `ast`.

    Comments vanish for free — `ast.parse` never sees them — so this handles both halves of
    the trap in one pass.
    """
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree)).replace("'", '"')


class TestTheModuleIsGone(unittest.TestCase):
    def test_every_deleted_path_is_actually_absent(self):
        present = [p for p in DELETED_PATHS if (APP / p).exists()]
        self.assertEqual(present, [], f"still on disk: {present}")

    def test_chat_is_not_a_registered_module(self):
        """`modules.txt` and the package must go in the SAME commit.

        `sync_for()` calls `frappe.get_module(app + "." + module)` for every entry, so a
        `Chat` line with no `chat/` package raises `ModuleNotFoundError` inside `sync_all()`
        and aborts `bench migrate` — which on this repo is the deploy, at exactly the
        half-applied point CLAUDE.md describes.
        """
        modules = [
            line.strip()
            for line in MODULES_TXT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertNotIn("Chat", modules)

    def test_nothing_shipped_imports_the_module(self):
        """Swept over comment-stripped source. See the module docstring."""
        offenders = []
        for path in sorted(APP.rglob("*.py")):
            if SWEEP_SKIP_DIRS & set(path.parts) or path.name in SWEEP_SKIP_FILES:
                continue
            try:
                code = _py(path)
            except SyntaxError:  # pragma: no cover - a broken file is another test's problem
                continue
            if DEAD_IMPORT in code:
                offenders.append(str(path.relative_to(APP)))
        self.assertEqual(offenders, [], f"still import the deleted module: {offenders}")

    def test_no_browser_code_imports_the_deleted_js(self):
        """Matches IMPORT PATHS, not the substring `/chat/`.

        The looser check flags legitimate test data: `test_triton_citations.mjs` feeds
        `isSafeUrl` same-origin relative URLs, and any route spelled in one is a fixture
        about URL *shape*, not a dependency. A sweep that cannot tell those apart gets
        weakened or deleted by the first person it wrongly accuses.
        """
        offenders = []
        for path in sorted(list(APP.rglob("*.js")) + list(REPO.joinpath("scripts").rglob("*.mjs"))):
            if SWEEP_SKIP_DIRS & set(path.parts):
                continue
            code = _js(path).replace("\\", "/")
            if 'from "../chat/' in code or "public/js/chat/" in code:
                offenders.append(str(path.relative_to(REPO)))
        self.assertEqual(offenders, [], f"still import from public/js/chat: {offenders}")

    def test_the_boot_flag_is_gone(self):
        code = _py(BOOT)
        self.assertNotIn("ee_chat", code)
        self.assertNotIn("_chat_visible", code)

    def test_the_ai_gate_no_longer_carries_a_chat_denylist(self):
        """The denylist named 23 DocTypes that no longer exist.

        It went in the same commit deliberately: its own test asserted SET EQUALITY against
        `chat/doctype/*/*.json`, so leaving it would have failed the build against an empty
        directory. Safe to remove only because the teardown patch DROPS the tables — a
        denylist protecting nothing is dead weight, but a dropped table needs no protection.
        """
        code = _py(GATE)
        self.assertNotIn("CHAT_DENYLIST_DOCTYPES", code)
        self.assertNotIn("chat_denylist_hit", code)


class TestTheTeardownPatchIsRegisteredAndSafe(unittest.TestCase):
    """The half the framework does not do for us.

    `remove_orphan_doctypes()` clears the DocType rows on the same migrate but never drops a
    table, and deleting the `Chat Settings` DocType does not touch its `tabSingles` rows.
    """

    def test_it_is_in_post_model_sync(self):
        text = PATCHES_TXT.read_text(encoding="utf-8")
        _, _, post = text.partition("[post_model_sync]")
        self.assertIn("erpnext_enhancements.patches.delete_chat_module", post)

    def test_it_names_every_doctype(self):
        code = PATCH.read_text(encoding="utf-8")
        missing = [d for d in CHAT_DOCTYPES if f'"{d}"' not in code]
        self.assertEqual(missing, [], f"the teardown patch does not name: {missing}")

    def test_it_cannot_raise(self):
        """A patch that raises aborts `bench migrate`, which on this repo is the deploy.

        v1.395.0 is the precedent: one raising patch left production schema-synced with 29
        new doctypes, 3 of 8 patches applied, no fixtures and no `after_migrate` hooks, while
        `__version__` reported the new release.
        """
        tree = ast.parse(PATCH.read_text(encoding="utf-8"))
        raises = [n for n in ast.walk(tree) if isinstance(n, ast.Raise)]
        self.assertEqual(raises, [], "the teardown patch contains a raise")

    def test_every_step_is_individually_guarded(self):
        """One unreachable table must not stop the other twenty-one being dropped."""
        tree = ast.parse(PATCH.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or node.name in ("execute", "_log"):
                continue
            with self.subTest(function=node.name):
                self.assertTrue(
                    any(isinstance(n, ast.Try) for n in ast.walk(node)),
                    f"{node.name}() has no try/except",
                )

    def test_it_does_not_reach_into_singles_through_the_orm(self):
        """`tabSingles` has three columns and no `creation`.

        Every `frappe.db` read helper defaults to `order_by="creation"`, so
        `frappe.db.exists("Singles", ...)` raises `OperationalError (1054)` on every site,
        every time — a read that CANNOT succeed. `tests/test_singles_table_access.py` gates
        the whole repo on this; asserted again here because this patch is the one file with
        a reason to touch that table.
        """
        code = _py(PATCH)
        for helper in ("get_value", "get_all", "exists", "get_list"):
            with self.subTest(helper=helper):
                self.assertNotIn(f'{helper}("Singles"', code)

    def test_it_does_not_disable_chat_first(self):
        """The obvious safety step is the one genuinely dangerous thing to do here.

        Frappe synthesises a Single's defaults only while `tabSingles` holds no row for it,
        so `set_single_value("Chat Settings", ...)` on a site that never materialised it
        CREATES the row and writes None into every other field — `dry_run_mode` to 0 and
        `restrict_to_whitelist` to 0, the unsafe direction for both. A patch written to make
        the integration safer would have made it live on the deploy that removed it.
        """
        self.assertNotIn("set_single_value", _py(PATCH))


class TestTheTritonWidgetSurvives(unittest.TestCase):
    """The half that would have failed. See the module docstring."""

    def test_the_server_side_is_untouched(self):
        for name in ("triton_chat.py", "triton_attachments.py"):
            with self.subTest(module=name):
                self.assertTrue((APP / name).exists(), f"{name} is missing")

    def test_the_shared_browser_modules_were_rehomed_not_deleted(self):
        missing = [n for n in SURVIVING_JS if not (TRITON_JS_DIR / n).exists()]
        self.assertEqual(missing, [], f"missing from public/js/triton: {missing}")

    def test_the_widget_imports_them_from_their_new_home(self):
        """A stale `../chat/` import is not a test failure, it is a FAILED BUILD.

        `triton_widget.js` ships inside `erpnext_enhancements.bundle.js`, the global Desk
        bundle, and the deploy runs `bench migrate && bench build`. An unresolvable import
        fails the build after the migrate has already committed.
        """
        code = _js(WIDGET_JS)
        self.assertIn('from "../triton/citations.js"', code)
        self.assertIn('from "../triton/keys.js"', code)
        self.assertIn('from "../triton/markdown.js"', code)
        self.assertNotIn("../chat/", code)

    def test_the_widget_dropped_the_coworker_surface(self):
        code = _js(WIDGET_JS)
        for symbol in (
            "BubbleChatSurface",
            "chat_surface.js",
            "setSurface",
            "renderBadge",
            "writeBubbleHandoff",
            "restoreFromHandoff",
            "triton-surface-tab",
        ):
            with self.subTest(symbol=symbol):
                self.assertNotIn(symbol, code)

    def test_the_citation_styling_survived_the_css_cut(self):
        """The trap that makes this class worth writing.

        These rules sat INSIDE the `PHASE 3` block that ran to end-of-file. Deleting that
        block in one cut — the obvious move, and the one a reviewer would wave through —
        strips inline-citation and sources-chip styling off every Triton answer, and nothing
        anywhere fails.
        """
        css = WIDGET_CSS.read_text(encoding="utf-8")
        missing = [r for r in SURVIVING_CSS_RULES if r not in css]
        self.assertEqual(missing, [], f"citation CSS lost in the chat cut: {missing}")

    def test_the_coworker_css_is_gone(self):
        css = _js(WIDGET_CSS)
        for rule in (".triton-chat-room", ".triton-fab-badge", ".triton-surface-tabs"):
            with self.subTest(rule=rule):
                self.assertNotIn(rule, css)


if __name__ == "__main__":
    unittest.main()
