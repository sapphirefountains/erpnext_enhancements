"""The learner transport's own contract — ``public/js/training/transport.js``.

Extracted from ``www/training.html`` in v1.428.1, when the portal page stopped
being the only host. Four other suites already assert things *about* the transport
from their own angle (that every mapped name is whitelisted, that every call
satisfies its endpoint's signature, that the beacon posts). They keep their own
independent extractors on purpose — the duplication is what makes them independent
observers. This module is different: it is the home for the properties that belong
to the transport *itself*, and that no other suite is watching.

Each assertion below stands for a bug that has either happened here or is one
plausible edit away, and every one of them fails **silently** at runtime:

* a Promise returned from ``heartbeatBeacon`` is truthy, so ``video.js`` drops
  every queued beat as "delivered" whether or not it left the machine;
* a missing ``Content-Type`` guard on the upload makes the browser stop setting
  the multipart boundary, and the file arrives unparseable;
* a missing deadline lets one socket dropped without a FIN hold the heartbeat's
  ``flushing`` latch forever, and the learner's coverage meter never moves again;
* and the declaration order below is read by three other suites as a text slice.

Run: python -m unittest erpnext_enhancements.tests.test_training_transport
"""

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
TRANSPORT = APP / "public" / "js" / "training" / "transport.js"


def source():
    return TRANSPORT.read_text(encoding="utf-8")


def code():
    """Source with comments stripped.

    A comment naming a token is not a use of it. This module asserts that several
    tokens are ABSENT, and that class of assertion is satisfied by prose unless the
    prose is removed first — the comment explaining why something is absent nearly
    always names the thing.
    """
    src = re.sub(r"/\*.*?\*/", "", source(), flags=re.S)
    return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def body_between(start, end):
    """The slice of code between two markers, both of which must exist."""
    text = code()
    first = text.index(start)
    second = text.index(end, first)
    return text[first:second]


class TestTheFileIsThere(unittest.TestCase):
    """Anti-vacuity: every assertion below reads this file."""

    def test_it_exists_and_is_substantial(self):
        self.assertTrue(TRANSPORT.is_file(), f"no transport at {TRANSPORT}")
        self.assertGreater(len(source()), 4000)

    def test_it_defines_the_factory(self):
        self.assertIn("TR.makeTransport = function", code())

    def test_the_factory_returns_the_transport(self):
        """Without this the host gets ``undefined`` and every call throws on the
        first click, which is loud — but the test costs nothing and pins the shape
        the two hosts depend on."""
        self.assertRegex(code(), r"return transport;")


class TestDeclarationOrder(unittest.TestCase):
    """``METHOD`` must be declared above ``PREFIX``, and this is the canonical home
    for that rule.

    Three other suites extract the map by slicing this file between those two
    declarations. ``str.index`` returns the smaller offset for whichever appears
    first, so swapping them yields an empty slice, an empty map, and a set of
    set-difference assertions that all pass over nothing — green, and checking
    nothing. The idiomatic module ordering (constants first) is precisely the edit
    that breaks it, which is why it is written down rather than left to habit.
    """

    def test_the_map_comes_first(self):
        text = code()
        self.assertLess(
            text.index("var METHOD = {"),
            text.index("var PREFIX"),
            "METHOD must stay declared above PREFIX; three suites slice between them",
        )

    def test_the_map_has_entries(self):
        block = body_between("var METHOD = {", "var PREFIX")
        self.assertGreater(len(re.findall(r':\s*"[a-z_]+"', block)), 20)


class TestTheDeadline(unittest.TestCase):
    """``fetch`` has no default timeout, and a socket dropped by an intermediary
    without a FIN leaves its promise pending forever.

    That is not academic here: the heartbeat holds a ``flushing`` latch released
    only when the promise settles, so one hung request stops every later beat for
    the life of the page. The learner keeps watching and their coverage meter never
    moves again — no error, no retry, nothing to see.
    """

    def test_the_timeout_is_declared(self):
        self.assertRegex(code(), r"CALL_TIMEOUT_MS\s*=\s*20000")

    def test_there_is_a_fallback_for_older_safari(self):
        """``AbortSignal.timeout`` is 2022-era, and the population most likely to
        lose a socket is exactly the one on an older phone."""
        text = code()
        self.assertIn("AbortSignal", text)
        self.assertIn("AbortController", text)

    def test_every_call_carries_the_signal(self):
        self.assertRegex(code(), r"signal:\s*abortSignal\(\)")


class TestEveryCallPosts(unittest.TestCase):
    """The POST-only rule exists so attempt ids, lesson keys and checkpoint keys
    never reach an access log. The server declares ``methods=["POST"]``, which is
    only safe because the client already obeyed it — a GET here would 405 every
    learner request."""

    def test_call_posts(self):
        self.assertRegex(code(), r"fetch\(PREFIX \+ method, \{\s*\n\s*method: \"POST\"")

    def test_it_sends_the_csrf_header(self):
        self.assertIn("X-Frappe-CSRF-Token", code())


class TestTheBeaconStaysSynchronous(unittest.TestCase):
    """``heartbeatBeacon`` must be synchronous and return a boolean.

    ``video.js`` drops a beat from its retry queue on a truthy return. The generic
    wrapper returns a Promise, which is **always** truthy — so when this path was
    once built by the same loop as everything else, every queued beat was dropped
    as "delivered" whether or not it ever left the machine. Nothing errored; the
    telemetry simply stopped being true.
    """

    def test_it_is_defined_outside_the_generic_loop(self):
        """The loop assigns ``transport[name] = function (payload) {...}`` for every
        METHOD entry. This one is assigned by hand, after it."""
        self.assertIn("transport.heartbeatBeacon = function", code())

    def test_it_returns_the_beacon_result_directly(self):
        body = body_between("transport.heartbeatBeacon = function", "return transport;")
        self.assertIn("return navigator.sendBeacon(", body)

    def test_it_never_returns_a_promise(self):
        body = body_between("transport.heartbeatBeacon = function", "return transport;")
        for token in (".then(", "Promise", "async "):
            self.assertNotIn(token, body, f"the beacon path must stay synchronous ({token})")

    def test_the_token_rides_in_the_body(self):
        """``sendBeacon`` cannot set headers, so the CSRF token cannot travel in
        one. Same endpoint, different delivery."""
        body = body_between("transport.heartbeatBeacon = function", "return transport;")
        self.assertIn("csrf_token", body)


class TestTheUpload(unittest.TestCase):
    """A learner's work submission is the one thing the player sends that is not
    JSON, and it goes to a different prefix."""

    def test_it_uses_frappes_own_upload_endpoint(self):
        self.assertIn("/api/method/upload_file", code())

    def test_it_uploads_private(self):
        body = body_between("transport.uploadFile = function", "transport.heartbeatBeacon")
        self.assertIn("is_private", body)

    def test_it_does_not_set_a_content_type(self):
        """The browser sets the multipart boundary itself, and only if we leave
        Content-Type alone. Setting it by hand produces a body the server cannot
        parse — with a perfectly well-formed request as the symptom."""
        body = body_between("transport.uploadFile = function", "transport.heartbeatBeacon")
        self.assertNotIn("Content-Type", body)


class TestItStaysHostAgnostic(unittest.TestCase):
    """The whole reason this file exists is that there are now two hosts."""

    def test_it_reads_no_page_global(self):
        self.assertNotIn("TRAINING_CSRF", code())
        self.assertNotIn("TRAINING_BOOT", code())

    def test_the_csrf_is_an_injected_option(self):
        """A value on the portal, which renders its token once; a function in the
        Desk, where a session can outlive the token it booted with."""
        self.assertRegex(code(), r"settings\.csrf")

    def test_it_calls_no_frappe_global(self):
        """Also asserted in test_training_phase3_contracts across all five files.
        Repeated here because this is the file where the temptation lives: the Desk
        host has frappe.call available and using it would look like a simplification.
        """
        self.assertNotRegex(code(), r"\bfrappe\.\w")

    def test_it_renders_no_html(self):
        self.assertNotIn("innerHTML", code())


if __name__ == "__main__":
    unittest.main()
