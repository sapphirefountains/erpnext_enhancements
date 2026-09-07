"""What the kiosk service worker is allowed to answer for.

``kiosk-sw.js`` is registered at **root scope** — `/` — so it intercepts every
GET on the origin, for every page in the app, not just `/kiosk`. That is fine for
what it needs (an offline app shell and a durable geolocation queue) and
dangerous for anything wider, because of its lifecycle: **a service worker is
only replaced when its own script URL changes**, and that only happens when
somebody opens `/kiosk`.

It used to answer *any* request under `/assets/erpnext_enhancements/` cache-first
with ``ignoreSearch: true``. The comment argued that was safe because "the cache
only ever holds this deploy's entries". It is not. A browser that opened the
kiosk once then served **that** deploy's JavaScript to every other page in the
app until somebody went back to the kiosk — and ``ignoreSearch`` reduced the
``?v=`` deploy token to decoration, so a brand-new token matched a months-old
entry.

Found in the wild: a training player four releases stale, in a browser whose
service-worker cache was named after a deploy from weeks earlier. Nothing the
page did could reach it — not a new `?v=`, not `cache: "reload"`, not a random
query string, because the worker matched all of them to the same cached entry.
Every "I deployed the fix and it is still broken" in that stretch was partly this.

These are static reads. The worker never runs in CI, and the property that
matters is a *scope* claim in the source, which is exactly what a text assertion
can hold.

Run: python -m unittest erpnext_enhancements.tests.test_kiosk_service_worker
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
KIOSK_WORKER = APP / "www/kiosk-sw.js"
WALL_WORKER = APP / "www/wall-sw.js"

# Back-compat alias: the kiosk classes below were written against a single WORKER.
# wall-sw.js is a trimmed clone of the kiosk worker, registered at the SAME root
# scope, so the same containment rule binds it (see TestWallWorkerHasTheSameContainment).
WORKER = KIOSK_WORKER


def _code(worker=KIOSK_WORKER):
    """The worker with comments stripped.

    The prose in this file explains the very bug being asserted against and
    quotes the old code, so a substring search over the raw text would match the
    explanation and pass while the code did the wrong thing.
    """
    src = worker.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("//")
    )


def _fetch_handler(worker=KIOSK_WORKER):
    code = _code(worker)
    start = code.index("addEventListener('fetch'")
    depth, opened = 0, False
    for end in range(start, len(code)):
        if code[end] == "{":
            depth += 1
            opened = True
        elif code[end] == "}":
            depth -= 1
            if opened and depth == 0:
                return code[start : end + 1]
    return code[start:]


class TestTheWorkerIsReadable(unittest.TestCase):
    """Guards every assertion below from passing because a parse went stale."""

    def test_the_worker_exists_and_has_a_fetch_handler(self):
        self.assertTrue(WORKER.exists())
        handler = _fetch_handler()
        self.assertIn("respondWith", handler)
        self.assertGreater(len(handler), 500)


class TestItOnlyAnswersForItsOwnShell(unittest.TestCase):
    """The load-bearing one.

    A root-scope worker that answers for the whole app's assets will, sooner or
    later, serve one page's stale JavaScript to another page — and it will do so
    invisibly, because the page has no way to tell that its request was answered
    from a cache it does not control.
    """

    def test_it_does_not_claim_the_apps_asset_root(self):
        handler = _fetch_handler()
        self.assertNotIn(
            "startsWith('/assets/erpnext_enhancements/')",
            handler,
            "the kiosk worker answers for every asset in the app, including other "
            "pages' JavaScript — it may only answer for its own precached shell",
        )

    def test_it_matches_an_explicit_list(self):
        handler = _fetch_handler()
        self.assertIn("PRECACHE_PATHS", handler)

    def test_the_list_is_derived_from_the_precache(self):
        """Two lists that had to be kept in step would drift; this one cannot."""
        code = _code()
        self.assertIn("const PRECACHE_PATHS = new Set(PRECACHE)", code)

    def test_the_precache_is_only_kiosk_assets(self):
        """If a non-kiosk asset is ever added to PRECACHE it becomes a
        cache-first, ignoreSearch entry for the whole origin again."""
        code = _code()
        block = code[code.index("const PRECACHE = [") : code.index("];", code.index("const PRECACHE = ["))]
        entries = re.findall(r"'([^']+)'", block)
        self.assertTrue(entries, "could not read the precache list")
        for entry in entries:
            self.assertTrue(
                "/kiosk" in entry or entry.endswith("kiosk-manifest.json"),
                f"{entry} is not a kiosk asset and must not be precached by a "
                f"root-scope worker",
            )


class TestIgnoreSearchIsContained(unittest.TestCase):
    """`ignoreSearch` is what turned the `?v=` deploy token into decoration.

    It is still right for the shell — page and worker can disagree by one token
    while an update is mid-flight, and the shell must resolve — but only there.
    """

    def test_ignore_search_is_used_at_most_once(self):
        handler = _fetch_handler()
        self.assertLessEqual(
            handler.count("ignoreSearch"),
            1,
            "ignoreSearch outside the shell branch defeats every ?v= cache-bust",
        )

    def test_the_only_ignore_search_is_inside_the_shell_branch(self):
        handler = _fetch_handler()
        if "ignoreSearch" not in handler:
            return
        guard = handler.index("PRECACHE_PATHS")
        self.assertLess(
            guard,
            handler.index("ignoreSearch"),
            "ignoreSearch is reached before the precache guard",
        )


class TestWallWorkerHasTheSameContainment(unittest.TestCase):
    """`wall-sw.js` is a trimmed clone of the kiosk worker and is ALSO registered at
    root scope, so the same rule binds it: answer only its own shell, never the app's
    asset root. It kept the pre-v1.229.0 hole — a `startsWith('/assets/erpnext_
    enhancements/')` branch, cache-first with `ignoreSearch` — long after the kiosk
    worker was cut back, until v1.364.1.
    """

    def test_the_worker_exists_and_has_a_fetch_handler(self):
        self.assertTrue(WALL_WORKER.exists())
        handler = _fetch_handler(WALL_WORKER)
        self.assertIn("respondWith", handler)
        self.assertGreater(len(handler), 500)

    def test_it_does_not_claim_the_apps_asset_root(self):
        handler = _fetch_handler(WALL_WORKER)
        self.assertNotIn(
            "startsWith('/assets/erpnext_enhancements/')",
            handler,
            "the wall worker answers for every asset in the app, including other "
            "pages' JavaScript — it may only answer for its own precached shell",
        )

    def test_it_matches_an_explicit_list(self):
        self.assertIn("PRECACHE_PATHS", _fetch_handler(WALL_WORKER))

    def test_the_list_is_derived_from_the_precache(self):
        self.assertIn("const PRECACHE_PATHS = new Set(PRECACHE)", _code(WALL_WORKER))

    def test_the_precache_is_only_wall_assets(self):
        """If a non-wall asset is ever added to PRECACHE it becomes a cache-first,
        ignoreSearch entry for the whole origin again."""
        code = _code(WALL_WORKER)
        start = code.index("const PRECACHE = [")
        block = code[start : code.index("];", start)]
        entries = re.findall(r"'([^']+)'", block)
        self.assertTrue(entries, "could not read the precache list")
        for entry in entries:
            self.assertIn(
                "/wall",
                entry,
                f"{entry} is not a wall asset and must not be precached by a "
                f"root-scope worker",
            )

    def test_ignore_search_is_used_at_most_once(self):
        self.assertLessEqual(
            _fetch_handler(WALL_WORKER).count("ignoreSearch"),
            1,
            "ignoreSearch outside the shell branch defeats every ?v= cache-bust",
        )

    def test_the_only_ignore_search_is_inside_the_shell_branch(self):
        handler = _fetch_handler(WALL_WORKER)
        if "ignoreSearch" not in handler:
            return
        self.assertLess(
            handler.index("PRECACHE_PATHS"),
            handler.index("ignoreSearch"),
            "ignoreSearch is reached before the precache guard",
        )


class TestBothWorkersCloneBeforeConsuming(unittest.TestCase):
    """`Response.clone()` must be called BEFORE the response is returned to
    `respondWith`. Deferring it inside `caches.open(...).then((c) => c.put(req,
    res.clone()))` clones after the page has already consumed the body — `Failed to
    execute 'clone' on 'Response': Response body is already used`, thrown on every
    page a root-scope worker controls. wall-sw.js spammed it on /training and /desk
    until v1.364.1; kiosk-sw.js was already correct.
    """

    WORKERS = (KIOSK_WORKER, WALL_WORKER)

    def test_no_worker_clones_inside_the_deferred_cache_put(self):
        for worker in self.WORKERS:
            self.assertNotIn(
                "put(req, res.clone())",
                _fetch_handler(worker),
                f"{worker.name} clones the response after returning it; clone "
                f"synchronously into a variable and cache that instead",
            )

    def test_both_shells_clone_synchronously(self):
        for worker in self.WORKERS:
            self.assertIn(
                "const copy = res.clone()",
                _fetch_handler(worker),
                f"{worker.name} should clone synchronously into `copy` before the "
                f"response is returned to respondWith",
            )


if __name__ == "__main__":
    unittest.main()
