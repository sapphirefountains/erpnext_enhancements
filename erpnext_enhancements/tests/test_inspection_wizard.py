# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Guards for the Inspection Wizard — WI-075 sub-phase H.

Bench-free and frappe-free: assertions about the contents of ``inspection_wizard.js`` and the
allowlists in ``api/quality_wizard.py``. unittest, not pytest, so it can ride an existing
``python -m unittest`` step in ci.yml — a pytest-style suite appended to a unittest module list
is silently collected as nothing, which is what left the QuickBooks suite running nowhere.

The failures guarded here are the ones that leave the screen looking right:

1. **A wizard that can write a frozen field.** The whole freeze rests on the server allowlist
   being short. A ``min_value`` in it would let somebody turn a failing measurement into a
   passing one from a phone, on site, with nothing in the diff to see.
2. **Escaped Text Editor markup.** ``frappe.utils.xss_sanitise`` escapes ``<``, ``>``, ``"``,
   ``'`` and ``/`` by default, so safety instructions render as visible tags. The panel still
   looks populated — which is why it shipped once on the maintenance side and survived review.
3. **A silent failed autosave.** On a bad signal this loses a whole inspection, and the
   inspector finds out after leaving the site.
4. **Required rows that are only revealed by the submit refusal.** ``is_mandatory`` and
   ``requires_photo`` reach the ``before_submit`` gate whether or not anybody saw them. A
   checklist that only says what it wanted once you try to finish is a checklist that lied.
5. **Raw control bytes**, which make git treat the file as binary and stop producing diffs.
6. **Back that skips every section, and a screen change that loses an answer.** Each section
   is a history entry; the phone's Back used to land on the list from any of them, and the
   list nulled the record under a pending autosave, which then threw and lost the answer. A
   queued answer keeps the lock it was given against, so reopening the inspection cannot send
   it over somebody else's save, and a save's repaint waits while an input is being typed into.
   ``scripts/test_inspection_wizard_nav.mjs`` asserts this by running the page; it is run from
   here when node is on PATH, and in CI it fails rather than skips without node.

Run: python -m unittest erpnext_enhancements.tests.test_inspection_wizard
"""

import io
import os
import re
import shutil
import subprocess
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIZARD = os.path.join(
    APP_DIR, "quality", "page", "inspection_wizard", "inspection_wizard.js"
)
PAGE_JSON = os.path.join(
    APP_DIR, "quality", "page", "inspection_wizard", "inspection_wizard.json"
)
API = os.path.join(APP_DIR, "api", "quality_wizard.py")

#: Frozen fields on ``Inspection Result``: the standard being inspected against, copied from the
#: master template at generation. None of these may ever appear in the server's row allowlist.
FROZEN_ROW_FIELDS = (
    "label",
    "acceptance_criteria",
    "source",
    "source_key",
    "section_title",
    "sequence",
    "check_type",
    "method",
    "is_mandatory",
    "requires_photo",
    "min_value",
    "max_value",
    "uom",
    "options",
    "location_note",
    "out_of_range",
    "non_conformance",
)


def _strip_comments(source):
    """Drop // comments so assertions about *code* are not fooled by prose.

    A comment explaining why a token is absent necessarily names that token — a trap this repo
    has walked into three times in one session.
    """
    return re.sub(r"^\s*//.*$", "", source, flags=re.MULTILINE)


class TestWizardAllowlist(unittest.TestCase):
    """The server half. This is the freeze's last line of defence."""

    @classmethod
    def setUpClass(cls):
        with io.open(API, encoding="utf-8") as handle:
            cls.source = handle.read()
        cls.code = re.sub(r'"""[\s\S]*?"""', "", cls.source)

    def test_the_row_allowlist_is_exactly_the_answer_fields(self):
        match = re.search(r"ALLOWED_ROW_FIELDS\s*=\s*\{([^}]*)\}", self.code)
        self.assertIsNotNone(match, "ALLOWED_ROW_FIELDS is missing from api/quality_wizard.py")
        fields = set(re.findall(r'"(\w+)"', match.group(1)))
        self.assertEqual(
            fields,
            {"outcome", "measured_value", "notes", "photo"},
            "The wizard may write an answer and its evidence, nothing else.",
        )

    def test_no_frozen_field_is_writable(self):
        match = re.search(r"ALLOWED_ROW_FIELDS\s*=\s*\{([^}]*)\}", self.code)
        allowed = set(re.findall(r'"(\w+)"', match.group(1)))
        leaked = sorted(allowed & set(FROZEN_ROW_FIELDS))
        self.assertEqual(
            leaked,
            [],
            "These frozen fields are writable from the wizard: %s. They are the standard being "
            "inspected against, copied at generation. A wizard that can edit a bound can turn a "
            "failing measurement into a passing one from a phone." % leaked,
        )

    def test_the_parent_allowlist_excludes_identity_and_provenance(self):
        match = re.search(r"ALLOWED_PARENT_FIELDS\s*=\s*\{([^}]*)\}", self.code)
        self.assertIsNotNone(match)
        allowed = set(re.findall(r'"(\w+)"', match.group(1)))
        for field in (
            "project",
            "milestone",
            "master_template",
            "master_template_revision",
            "snapshot_hash",
            "scope_of_work",
            "inspector",
            "status",
            "fail_count",
        ):
            self.assertNotIn(field, allowed, f"{field} must not be writable from the wizard")

    def test_there_is_no_append_path_for_result_rows(self):
        """Every row was frozen at generation. One that could be added could be a check nobody
        contracted for, or a quiet replacement for one the inspector could not answer."""
        self.assertNotIn(
            'doc.append("results"',
            self.code,
            "the wizard API appends result rows; the row list is frozen at generation",
        )

    def test_writes_are_refused_on_a_submitted_inspection(self):
        self.assertEqual(
            self.code.count("already submitted"),
            2,
            "both save_inspection and finish_inspection must refuse a submitted inspection",
        )

    def test_stale_writes_are_rejected_rather_than_merged(self):
        """Merging blind would silently overwrite an answer nobody knows was lost."""
        self.assertIn("def _check_not_stale", self.code)
        # Indented call sites only -- the `def` line contains the same text.
        calls = re.findall(r"^\s+_check_not_stale\(doc, modified\)", self.code, re.M)
        self.assertEqual(
            len(calls),
            2,
            "both the save and the finish path must check the optimistic lock",
        )

    def test_every_endpoint_checks_permission_explicitly(self):
        """A whitelisted function is reachable over HTTP by any logged-in user."""
        endpoints = re.findall(r"@frappe\.whitelist\(\)\s*\ndef (\w+)", self.source)
        self.assertEqual(
            sorted(endpoints),
            [
                "finish_inspection",
                "get_inspection_bootstrap",
                "get_open_inspections",
                "save_inspection",
            ],
        )
        # get_open_inspections is scoped to the session user by its own filter, so it needs no
        # separate has_permission call; the other three address a document by name.
        self.assertEqual(self.code.count("frappe.has_permission("), 3)


class TestWizardMarkup(unittest.TestCase):
    """The client half."""

    @classmethod
    def setUpClass(cls):
        with io.open(WIZARD, "rb") as handle:
            cls.raw = handle.read()
        cls.source = cls.raw.decode("utf-8")
        cls.code = _strip_comments(cls.source)

    def test_rich_text_is_not_escaped(self):
        self.assertNotIn(
            "xss_sanitise(",
            self.code,
            "inspection_wizard.js calls frappe.utils.xss_sanitise, which escapes HTML by default "
            "and renders Text Editor content as visible tags. Use qw_rich_html() for Text Editor "
            "fields and qw_plain_html() for Small Text.",
        )

    def test_helpers_are_defined(self):
        for helper in ("function qw_rich_html(", "function qw_plain_html("):
            self.assertIn(helper, self.code, f"{helper} is missing from inspection_wizard.js")

    def test_rich_html_strips_executable_nodes(self):
        for needle in ("DOMParser", "script", "removeAttribute"):
            self.assertIn(needle, self.code, f"qw_rich_html no longer references {needle}")

    def test_plain_text_keeps_line_breaks(self):
        """A check label or a generation note is one item per line; collapsing them hides one."""
        self.assertIn("escape_html", self.code)
        self.assertRegex(
            self.code,
            r"qw_plain_html[\s\S]{0,400}?replace\(/\\n/g,\s*\"<br>\"\)",
            "qw_plain_html should escape and then convert newlines to <br>",
        )

    def test_autosave_failure_is_visible_and_retried(self):
        self.assertIn("set_save_state(", self.code)
        self.assertIn("schedule_retry(", self.code)
        self.assertRegex(
            self.code,
            r'set_save_state\("error"\)[\s\S]{0,120}?schedule_retry\(\)',
            "a failed save must both show an error and schedule a retry",
        )
        self.assertIn("beforeunload", self.code, "closing the tab must warn while edits are unsaved")

    def test_a_failed_save_puts_the_patch_back(self):
        """Dropping the patch on failure loses the answers silently, which is the whole failure.

        The queue is keyed by inspection (see TestWizardHistory), so the patch goes back under
        the name it was sent for -- ``this.pending[name] = {`` -- never into a shared buffer the
        next inspection's answers are already in.
        """
        self.assertRegex(
            self.code,
            r"catch\(\(\w*\) => \{[\s\S]{0,900}?this\.pending\[name\] = \{",
            "the catch branch must restore the pending patch, under its own inspection, before "
            "reporting the error",
        )

    def test_required_and_photo_rows_are_marked_before_submit(self):
        self.assertIn("function qw_required_chip(", self.code)
        for field in ("is_mandatory", "requires_photo"):
            self.assertRegex(
                self.code,
                rf"qw_required_chip[\s\S]{{0,400}}?row\.{field}",
                f"{field} rows are not marked in the wizard, so the submit refusal is the first "
                "time anybody hears about them",
            )

    def test_the_contracted_range_is_shown_on_a_measurement(self):
        """An inspector who cannot see the bound is guessing at what 'passes' means."""
        self.assertIn("function qw_range_text(", self.code)
        self.assertRegex(self.code, r"qw_range_text\(row\)")

    def test_out_of_range_comes_from_the_server(self):
        """Two implementations of the same arithmetic is one more than can stay correct."""
        self.assertRegex(
            self.code,
            r"row\.out_of_range = fresh\.out_of_range",
            "apply_state must take out_of_range from the server response",
        )
        self.assertNotRegex(
            self.code,
            r"(measured_value|value)\s*[<>]=?\s*row\.(min_value|max_value)",
            "the wizard re-derives out-of-range locally; it must display the server's answer",
        )

    def test_the_carried_and_contracted_sources_are_distinguishable(self):
        """A re-check and a contracted addendum row read differently from a template check, and
        an inspector who cannot tell them apart cannot tell what they are being asked."""
        for source in ("Carried Action", "Project Addendum"):
            self.assertIn(source, self.code, f"the wizard does not mark {source} rows")

    def test_no_raw_control_bytes(self):
        offenders = [
            i for i, byte in enumerate(self.raw) if byte < 9 or byte in (11, 12) or 14 <= byte < 32
        ]
        self.assertEqual(
            offenders,
            [],
            f"inspection_wizard.js contains raw control bytes at {offenders[:5]}. "
            "Write them as \\u0000-style escapes instead.",
        )


class TestWizardHistory(unittest.TestCase):
    """Back and Forward walk the sections; a screen change never loses or misroutes an answer.

    The behaviour is asserted by ``scripts/test_inspection_wizard_nav.mjs``, which runs the real
    page against a fake of Frappe v16's router and history and presses Back and Forward (run
    below, when node is on PATH). These source guards are the cheap half: each names a mistake
    that was live in this file, and each one still reads as deliberate code in a diff.
    """

    @classmethod
    def setUpClass(cls):
        with io.open(WIZARD, encoding="utf-8") as handle:
            cls.code = _strip_comments(handle.read())

    def _method(self, name):
        """The body of one method of the page class, up to the next method."""
        match = re.search(
            r"^\t%s\([^)]*\) \{\n([\s\S]*?)^\t\}\n" % re.escape(name), self.code, re.M
        )
        self.assertIsNotNone(match, f"{name}() is missing from inspection_wizard.js")
        return match.group(1)

    def test_set_route_is_never_given_an_object(self):
        """v16 moves an object argument into ``route_options`` and pushes the bare path, so
        ``set_route("inspection-wizard", {inspection})`` pushed an entry identical to the
        list's own -- and Back from any section landed on the list."""
        self.assertNotRegex(self.code, r"set_route\([^)]*\{", "set_route must take route segments")

    def test_sections_change_through_the_router(self):
        """A tab or Previous/Next that repaints without routing is a section with no history
        entry, which is what made Back skip every section at once."""
        self.assertRegex(self._method("render_tabs"), r'on\("click", \(\) => this\.go_section\(index\)\)')
        nav = self._method("render_nav")
        self.assertIn("this.go_section(this.section_index - 1)", nav)
        self.assertIn("this.go_section(this.section_index + 1)", nav)
        self.assertNotRegex(
            nav + self._method("render_tabs"),
            r"this\.section_index =(?!=)",
            "a click that assigns the section itself changes the screen without a history entry",
        )
        self.assertIn("this.route_to(", self._method("go_section"))

    def test_the_route_is_read_from_the_router(self):
        handle = self._method("handle_route")
        self.assertIn("frappe.get_route()", handle)
        self.assertNotIn(
            "get_url_arg",
            handle,
            "the query string is only the legacy fallback, read in take_legacy_inspection",
        )
        self.assertIn('get_url_arg("inspection")', self._method("take_legacy_inspection"))

    def test_the_list_click_only_routes(self):
        """Loading the record from the click as well raced the list fetch the router's own
        "show" started; whichever landed last won the screen."""
        click = re.search(r'\.qw-list-item"\)\.on\("click"[\s\S]*?\}\);', self.code)
        self.assertIsNotNone(click)
        self.assertIn("this.route_to(", click.group(0))
        self.assertNotIn("this.load(", click.group(0))

    def test_no_hard_coded_desk_paths(self):
        """An ``/app/`` href is a full page reload in v16 (the router intercepts only /desk),
        and a hand-built path is a second copy of the router's own rules."""
        self.assertNotRegex(self.code, r"""["'`]/(app|desk)/""")

    def test_route_options_are_cleared_before_routing(self):
        """A leftover route_options makes v16's push_state push even a route to the current
        path, because it compares a query string it never writes."""
        self.assertRegex(
            self._method("route_to"),
            r"frappe\.route_options = null;[\s\S]*?frappe\.set_route\(parts\)",
        )

    def test_flush_never_reads_the_screen(self):
        """``flush()`` read ``this.doc.header.name`` and ``this.doc.state.modified``; the list
        nulls ``this.doc``, so a pending answer threw, was lost, and left ``saving`` stuck."""
        flush = self._method("flush")
        self.assertIn("inspection: name,", flush)
        self.assertIn("modified: queued.base,", flush)
        self.assertNotIn("this.doc.state.modified", flush)
        self.assertNotIn("this.doc.header.name,", flush)
        self.assertIn(
            "this.doc.header.name === name",
            flush,
            "a save's answer must be applied only to the inspection it was for",
        )

    def test_a_queued_answer_keeps_the_lock_it_was_given_against(self):
        """The lock travels with the buffer. ``flush()`` sent ``this.modified[name]``, which any
        reopen moved forward to the stamp it read -- so an answer queued before somebody else
        saved the inspection went out under THEIR stamp and overwrote them without a word. The
        course promises that save is refused, not merged."""
        self.assertIn("base: this.modified[name]", self._method("queue_for"))
        flush = self._method("flush")
        self.assertNotIn(
            "modified: this.modified[name]", flush, "the lock is the buffer's own, not the newest"
        )
        catch = flush[flush.index(".catch(") :]
        self.assertIn("base: queued.base,", catch, "a refused patch goes back under its own lock")
        load = self._method("load")
        self.assertRegex(
            load,
            r"if \(!queued \|\| String\(queued\.base\) === String\(this\.doc\.state\.modified\)\) \{"
            r"\s*this\.note_modified\(",
            "a reopen may move the lock, and lay queued answers over the copy, only when nothing "
            "was saved in between",
        )

    def test_a_save_never_repaints_over_typing(self):
        """A repaint replaces every input, and one with focus holds a value nothing has queued
        yet -- measurements and notes queue on "change", i.e. on blur. A save's answer landing
        mid-measurement wiped it."""
        flush = self._method("flush")
        self.assertIn("this.render_unless_typing();", flush)
        self.assertNotIn("this.render();", flush)
        self.assertIn("this.render_deferred = true;", self._method("render_unless_typing"))
        self.assertIn('.on("focusout",', self._method("bind_deferred_render"))

    def test_stale_responses_are_dropped(self):
        for method in ("render_list", "load"):
            self.assertGreaterEqual(
                self._method(method).count("ticket !== this.ticket"),
                2,
                f"{method}() must drop a response for a screen already left, before and after "
                "its fetch",
            )

    def test_the_nav_bar_goes_with_its_screen(self):
        """``.qw-nav`` is fixed to the viewport and lives outside the repainted wrap."""
        self.assertIn('this.page.main.find(".qw-nav").remove()', self._method("clear_screen"))
        self.assertIn('this.page.main.find(".qw-nav").remove()', self._method("render_submitted"))

    def test_the_constructor_does_not_route(self):
        """on_page_show always follows on_page_load; routing from both sent two fetches."""
        self.assertNotIn("this.handle_route()", self._method("constructor"))


HARNESS = os.path.join(
    os.path.dirname(APP_DIR), "scripts", "test_inspection_wizard_nav.mjs"
)


#: GitHub Actions sets CI=true on every runner.
IN_CI = os.environ.get("CI", "").lower() in ("1", "true", "yes")


@unittest.skipUnless(shutil.which("node") or IN_CI, "node is not on PATH")
class TestWizardHistoryExecuted(unittest.TestCase):
    """Runs the node harness from this suite, so it rides the existing CI step.

    Skipped on a laptop without node; FAILED, not skipped, in CI. A skip there still reports
    OK, and the QuickBooks suite once ran nowhere for weeks behind exactly that kind of green.
    """

    def test_back_and_forward_walk_the_sections(self):
        node = shutil.which("node")
        self.assertTrue(node, "CI must have node on PATH: the Back/Forward harness runs on it")
        result = subprocess.run(
            [node, HARNESS],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"\n(\d+)/\1 passed")


class TestWizardPage(unittest.TestCase):
    def test_the_page_is_not_named_after_a_workspace(self):
        """The router resolves the first path segment against workspaces before pages, so a Page
        named `quality` or `quality-control` would never render."""
        import json

        with io.open(PAGE_JSON, encoding="utf-8") as handle:
            page = json.load(handle)
        self.assertEqual(page["name"], "inspection-wizard")
        self.assertNotIn(page["name"], ("quality", "quality-control"))
        self.assertEqual(page["module"], "Quality")

    def test_the_page_grants_the_roles_that_do_inspections(self):
        import json

        with io.open(PAGE_JSON, encoding="utf-8") as handle:
            page = json.load(handle)
        roles = {row["role"] for row in page.get("roles", [])}
        self.assertIn("Quality Inspector", roles)
        self.assertIn("System Manager", roles)
        self.assertTrue(roles, "a Page with no roles is visible to everybody")


class TestOrderByIsAlwaysAPlainField(unittest.TestCase):
    """No ``order_by`` anywhere in this app may carry a SQL expression.

    Frappe v16 splits ``order_by`` on commas and validates each segment against a simple-field
    pattern (``frappe/database/query.py``), so ``coalesce(a, b, c) asc`` is rejected outright.
    Because the comma split happens first, the error names half a function::

        Invalid field format in Order By: coalesce(scheduled_date.
        Use 'field', 'link_field.field', or 'child_table.field'.

    which reads like a missing field rather than a forbidden expression.

    This shipped in the wizard's own open-inspection list and surfaced only when somebody opened
    the page. There is no bench test tier to catch it, so this source guard is the only thing
    between a SQL expression and a field tool that will not load.

    **Ordering by the fields separately is not an equivalent rewrite.** MariaDB sorts NULLs first
    on an ascending sort, so a row with an empty first field jumps ahead of a populated one. Sort
    in Python instead, as ``api/quality_wizard._due_key`` does.
    """

    #: An order_by string literal containing a bracket -- i.e. a function call.
    EXPRESSION = re.compile(r"""order_by\s*=\s*\(?\s*['"]([^'"]*[()][^'"]*)['"]""")

    def test_no_order_by_carries_a_sql_expression(self):
        offenders = []
        for root, dirs, files in os.walk(APP_DIR):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", "tests", "node_modules")]
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                with io.open(path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                for match in self.EXPRESSION.finditer(text):
                    offenders.append(
                        "%s: %s" % (os.path.relpath(path, APP_DIR), match.group(1))
                    )
        self.assertEqual(
            offenders, [], "order_by must be plain fields; sort in Python instead"
        )


class TestDueKeySorting(unittest.TestCase):
    """The open-inspection list sorts in Python, so the sort must not raise.

    `scheduled_date` and `inspection_date` are Date fields and come back as `datetime.date`;
    `creation` is a Datetime and comes back as `datetime.datetime`. Python refuses to compare
    them, so a list mixing an inspection that has a scheduled date with one that does not would
    raise inside `sort` -- taking the field tool down a second time, for a different reason than
    the `coalesce` order_by did.

    Behavioural rather than a source grep, for the reason this session learned the hard way:
    a grep sees the text, not the control flow.
    """

    @classmethod
    def setUpClass(cls):
        import datetime
        import sys
        import types

        def _getdate(value=None):
            if isinstance(value, datetime.datetime):
                return value.date()
            if isinstance(value, datetime.date):
                return value
            return datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d").date()

        utils = types.ModuleType("frappe.utils")
        utils.getdate = _getdate
        cls._getdate = staticmethod(_getdate)
        cls._datetime = datetime

        # Read the function out of the source rather than importing the module, which would drag
        # in frappe, the quality package and Quality Settings. The behaviour under test is the
        # comparison, and this exercises the real code.
        with io.open(API, encoding="utf-8") as fh:
            source = fh.read()
        start = source.index("def _due_key(")
        end = source.index("\n@frappe.whitelist()", start)
        namespace = {"getdate": _getdate}
        exec(compile(source[start:end], API, "exec"), namespace)
        cls.due_key = staticmethod(namespace["_due_key"])

    def test_a_date_and_a_datetime_can_be_sorted_together(self):
        """The exact mix the live query returns."""
        rows = [
            {"name": "B", "scheduled_date": None, "inspection_date": None,
             "creation": self._datetime.datetime(2026, 9, 14, 10, 0)},
            {"name": "A", "scheduled_date": self._datetime.date(2026, 9, 12),
             "inspection_date": None, "creation": self._datetime.datetime(2026, 9, 1, 8, 0)},
        ]
        rows.sort(key=self.due_key)
        self.assertEqual([r["name"] for r in rows], ["A", "B"])

    def test_scheduled_date_wins_over_creation(self):
        """Ordering by the three fields separately would put the unscheduled row first, because
        MariaDB sorts NULLs first on an ascending sort. That is the bug this replaces."""
        scheduled = {"scheduled_date": self._datetime.date(2026, 9, 20),
                     "inspection_date": None,
                     "creation": self._datetime.datetime(2026, 1, 1, 0, 0)}
        unscheduled = {"scheduled_date": None, "inspection_date": None,
                       "creation": self._datetime.datetime(2026, 9, 25, 0, 0)}
        self.assertLess(self.due_key(scheduled), self.due_key(unscheduled))

    def test_inspection_date_is_the_middle_fallback(self):
        row = {"scheduled_date": None,
               "inspection_date": self._datetime.date(2026, 3, 3),
               "creation": self._datetime.datetime(2026, 9, 9, 0, 0)}
        self.assertEqual(self.due_key(row), self._datetime.date(2026, 3, 3))

    def test_a_row_with_no_date_at_all_sorts_last(self):
        empty = {"scheduled_date": None, "inspection_date": None, "creation": None}
        dated = {"scheduled_date": self._datetime.date(2030, 1, 1),
                 "inspection_date": None, "creation": None}
        self.assertGreater(self.due_key(empty), self.due_key(dated))


if __name__ == "__main__":
    unittest.main()
