"""The profile, and the line between "my record" and "a colleague's entry".

    Your own profile answers "what do I need to do?".
    A colleague's answers "who around here knows how to do this?".

Everything here checks that sentence holds. A colleague sees badges, completed
course *titles*, and which qualifications somebody currently holds. A colleague
never sees a score, an attempt, a failure, a due date, a certificate number, or
anything about a course somebody is part-way through — those are performance data
or personal documents, and neither is a directory question.

**The construction matters more than the field list.** `colleague_profile` builds
its payload by *selecting* `PUBLIC_FIELDS` from the shared dict, never by deleting
private keys from the full one. That is deliberate and it is what these tests
pin: with a subtractive filter, every field added to the profile in future is
public until somebody remembers to exclude it — and the person adding a field is
never thinking about the directory. The gamification module made the same choice
for the same reason and wrote it down: *"an optional privacy filter is a privacy
filter somebody eventually leaves out."*

The second boundary is customers. `Training Learner` is held by customer Website
Users as well as staff, so every entry point refuses anybody without an
`Employee` record — **by requiring employment, not by checking a role**. A role
can be granted by accident; whether somebody works here cannot.

Run: python -m unittest erpnext_enhancements.tests.test_hr_profile
"""

import ast
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
PROFILE = APP / "hr_enhancements/profile.py"
RUNTIME = APP / "api/training.py"
PLAYER = APP / "public/js/training/player.js"
PAGE = APP / "www/training.html"


def _text(path):
    return path.read_text(encoding="utf-8")


def _code(text):
    """Source with `//` line comments removed.

    Every *absence* assertion in this file has to run against this rather than the
    raw text, and the reason is worth stating: the comment explaining why a token
    is absent almost always names the token. "there is no is_self branch here to
    get wrong" contains `is_self`. A scan that reads the explanation as the code
    is the same mistake as a contract test that passes because the word appears in
    a docstring — which is exactly how the Phase-4 contract passed on a gate that
    did not gate.
    """
    return re.sub(r"//.*$", "", text, flags=re.M)


def _module():
    return ast.parse(_text(PROFILE))


def _fn(name, path=PROFILE):
    src = _text(path)
    lines = src.splitlines()
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            stmts = node.body
            if (
                stmts
                and isinstance(stmts[0], ast.Expr)
                and isinstance(stmts[0].value, ast.Constant)
                and isinstance(stmts[0].value.value, str)
            ):
                stmts = stmts[1:]
            return "\n".join(lines[stmts[0].lineno - 1 : node.end_lineno]) if stmts else ""
    raise AssertionError(f"{name} not found in {path.name}")


def _public_fields():
    for node in _module().body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "PUBLIC_FIELDS" for t in node.targets
        ):
            return set(ast.literal_eval(node.value))
    raise AssertionError("PUBLIC_FIELDS not found")


class TestTheColleagueViewIsBuiltAdditively(unittest.TestCase):
    def test_it_selects_public_fields_rather_than_deleting_private_ones(self):
        """A subtractive filter makes every future field public by default, and the
        person adding a field is never thinking about the directory."""
        body = _fn("colleague_profile")
        self.assertIn("for key in PUBLIC_FIELDS", body)
        self.assertNotIn("del ", body)
        self.assertNotIn(".pop(", body)

    def test_the_private_fields_are_not_even_in_the_shared_dict(self):
        """Belt and braces: the self-only panels are added by `my_profile` after
        `_shared` returns, so a colleague payload never holds them even for the
        instant before the selection."""
        shared = _fn("_shared")
        for private in ("assigned", "expiring", "devices", "manager"):
            with self.subTest(field=private):
                self.assertNotIn(f'"{private}"', shared)

    def test_nothing_private_is_on_the_public_list(self):
        public = _public_fields()
        for private in ("assigned", "expiring", "devices", "manager", "is_self"):
            with self.subTest(field=private):
                self.assertNotIn(private, public)

    def test_the_public_list_carries_what_a_directory_is_for(self):
        public = _public_fields()
        for expected in ("badges", "completed", "qualifications", "position", "department"):
            with self.subTest(field=expected):
                self.assertIn(expected, public)


class TestPerformanceDataStaysPrivate(unittest.TestCase):
    def test_completed_courses_carry_no_score(self):
        """A colleague may know somebody passed. What they got is between them and
        their supervisor."""
        body = _fn("_completed")
        self.assertNotIn("score", body)
        self.assertNotIn("attempt", body)
        self.assertNotIn("video_coverage", body)

    def test_completed_shows_only_currently_valid_certifications(self):
        """A lapsed certification is the holder's business and their supervisor's,
        and it already shows on their own profile under `expiring`."""
        self.assertIn('"status": "Valid"', _fn("_completed"))

    def test_qualifications_carry_no_numbers(self):
        """A colleague may know somebody holds a forklift ticket. The certificate
        number, the issuing body and the scan of the card are the personal
        document — what is on the wall, not what is in the wallet."""
        body = _fn("_qualifications")
        for private in ("credential_number", "issuing_body", "attachment", "notes"):
            with self.subTest(field=private):
                self.assertNotIn(private, body)

    def test_due_dates_are_self_only(self):
        """A due date is a to-do list, not a fact about somebody, and a colleague
        reading one is reading over their shoulder."""
        self.assertNotIn("assigned", _public_fields())
        self.assertIn('"assigned": _assigned(user)', _fn("my_profile"))


class TestCustomersAreKeptOut(unittest.TestCase):
    def test_staff_is_employment_not_a_role(self):
        """`Training Learner` is on every customer contact. A role can be granted
        by accident; whether somebody works here cannot."""
        body = _fn("_is_staff")
        self.assertIn('frappe.db.exists("Employee"', body)
        self.assertNotIn("get_roles", body)

    def test_the_colleague_view_refuses_a_non_employee_at_both_ends(self):
        body = _fn("colleague_profile")
        self.assertIn("if not _is_staff(viewer):", body)
        self.assertIn("if not _is_staff(user):", body)

    def test_the_directory_is_empty_rather_than_an_error_for_a_customer(self):
        """They were shown a page, not a door they kicked. An empty list said
        plainly beats a permission dialog."""
        body = _fn("directory")
        self.assertIn("if not _is_staff(viewer):", body)
        self.assertIn("return []", body)

    def test_the_directory_link_is_not_offered_to_a_customer(self):
        """A client should never be shown a link to a colleague list, even one that
        would come back empty."""
        self.assertIn("if (b.is_staff)", _text(PLAYER))
        self.assertIn('"is_staff": bool(', _text(RUNTIME))

    def test_is_staff_on_the_boot_payload_is_employment_too(self):
        src = _text(RUNTIME)
        at = src.index('"is_staff": bool(')
        self.assertIn('frappe.db.exists("Employee"', src[at : at + 200])


class TestItIsWiredUp(unittest.TestCase):
    """This module has shipped complete-but-uncalled features twice — six sign-off
    endpoints with no caller in v1.215.0, and a whole ask-the-author backend that
    the learner had no way to reach for six weeks."""

    def test_both_endpoints_exist(self):
        src = _text(RUNTIME)
        self.assertIn("def get_profile(", src)
        self.assertIn("def get_directory(", src)

    def test_the_player_can_dial_them(self):
        page = _text(PAGE)
        self.assertIn('profile: "get_profile"', page)
        self.assertIn('directory: "get_directory"', page)

    def test_the_player_actually_calls_them(self):
        player = _text(PLAYER)
        self.assertIn('call("profile"', player)
        self.assertIn('call("directory"', player)

    def test_the_views_are_routable(self):
        player = _text(PLAYER)
        self.assertIn('view === "people"', player)
        self.assertIn('view === "person"', player)

    def test_the_profile_panel_does_not_branch_on_whose_it_is(self):
        """The same markup renders both. It does not check `is_self` — a colleague
        payload simply has nothing to draw for the private panels, so the privacy
        boundary lives in the data rather than in an if-statement somebody later
        inverts."""
        player = _code(_text(PLAYER))
        at = player.index("function renderProfileInto(")
        body = player[at : player.index("function profileList(")]
        self.assertNotIn("is_self", body)

    def test_the_transcript_and_the_profile_do_not_clobber_each_other(self):
        """Both land on the same screen and resolve in either order; a shared
        `clear(main)` would wipe whichever arrived first."""
        player = _code(_text(PLAYER))
        at = player.index("function renderRecord(")
        body = player[at : player.index("function recordRow(")]
        self.assertIn('el("div", "tr-record-slot")', body)
        self.assertIn("clear(slot)", body)
        self.assertNotIn("clear(main)", body)


if __name__ == "__main__":
    unittest.main()
