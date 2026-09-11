"""Time off and onboarding: the scope decisions, and the two doctypes that scope.

Both were built from scratch because `hrms` is not installed and installing it
would collide with the `HR` module name and six `Training *` doctype names.

**Time off is request → approve → calendar, and nothing else.** No balances, no
accrual, no carryover. That is a decision rather than an omission: balances are
where the real complexity and every payroll argument live, and they are only worth
carrying if PTO is being tracked as a liability. The daily need is "can I have next
Thursday" and "who is out that week", and neither needs an allocation record.

**Approval authority is `reports_to`, not the Position ladder** — and this is the
one place in the whole release where those come apart on purpose. Time off is "who
plans your week", which is exactly what the reporting line means and exactly what a
competence tier does not: a Senior Technician outranks a Junior on whether they can
drain a basin and has no standing at all over their Thursday. The rest of WI-072
routes authority through the ladder precisely because the reporting tree could not
express competence; borrowing it back here would be granting it something nobody
gave it.

**Onboarding owners are plain words, not Links.** Half of a first week is done by
whoever is free that morning, and a required assignee is how a checklist stops
getting filled in. The record's job is showing what has not happened, not knowing
whose fault it is.

Run: python -m unittest erpnext_enhancements.tests.test_hr_timeoff_onboarding
"""

import ast
import json
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
MODULE = APP / "hr_enhancements"
TIMEOFF = MODULE / "timeoff.py"
TIMEOFF_JSON = MODULE / "doctype/time_off_request/time_off_request.json"
TIMEOFF_PY = MODULE / "doctype/time_off_request/time_off_request.py"
CALENDAR = MODULE / "doctype/time_off_request/time_off_request_calendar.js"
ONBOARDING = MODULE / "onboarding.py"
CHECKLIST_JSON = MODULE / "doctype/onboarding_checklist/onboarding_checklist.json"
CHECKLIST_PY = MODULE / "doctype/onboarding_checklist/onboarding_checklist.py"
ITEM_JSON = MODULE / "doctype/onboarding_checklist_item/onboarding_checklist_item.json"
PERMISSIONS = MODULE / "permissions.py"
HOOKS = APP / "hooks.py"


def _text(path):
    return path.read_text(encoding="utf-8")


def _code(text):
    """JS with `//` comments stripped -- an absence assertion must not read the
    comment explaining the absence."""
    import re

    return re.sub(r"//.*$", "", text, flags=re.M)


def _fields(path):
    return {f["fieldname"]: f for f in json.loads(_text(path))["fields"]}


def _strip_comments(text):
    """Python source minus `#` comments -- an absence assertion must not read the
    comment explaining the absence."""
    import re

    return re.sub(r"#.*$", "", text, flags=re.M)


def _fn(name, path):
    src = _text(path)
    lines = src.splitlines()
    for node in ast.walk(ast.parse(src)):
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


class TestTimeOffStaysInScope(unittest.TestCase):
    def test_there_are_no_balance_fields(self):
        """Balances are where the payroll arguments live, and they are only worth
        carrying if PTO is tracked as a liability. It is not, here."""
        fields = _fields(TIMEOFF_JSON)
        for absent in ("balance", "allocation", "carry_forward", "accrual", "entitlement"):
            with self.subTest(field=absent):
                self.assertNotIn(absent, fields)

    def test_days_are_counted_honestly(self):
        """Calendar days, not working days: there is no holiday calendar on this
        site to subtract, and a number quietly pretending otherwise would be wrong
        by an unpredictable amount, in the direction that shortens somebody's
        leave."""
        self.assertIn("date_diff", _fn("_compute_days", TIMEOFF_PY))

    def test_a_half_day_only_means_something_on_one_day(self):
        """Silently ignoring the tick on a week would leave the form claiming
        something it is not doing."""
        body = _fn("_compute_days", TIMEOFF_PY)
        self.assertIn("and days == 1", body)
        self.assertIn("self.half_day = 0", body)

    def test_canceled_is_spelled_with_one_l(self):
        """House style, and renaming a Select option later needs a data patch or
        every existing row refuses to save."""
        options = _fields(TIMEOFF_JSON)["status"]["options"]
        self.assertIn("Canceled", options)
        self.assertNotIn("Cancelled", options)

    def test_it_is_not_submittable(self):
        """"I put in for Thursday and then didn't" is a change of mind, not an
        amendment of a document."""
        self.assertNotIn("is_submittable", json.loads(_text(TIMEOFF_JSON)))


class TestApprovalAuthorityIsTheReportingLine(unittest.TestCase):
    def test_the_approver_comes_from_reports_to(self):
        self.assertIn("reports_to", _fn("_resolve_approver", TIMEOFF_PY))

    def test_the_ladder_has_no_say(self):
        """The one place in WI-072 where the tier deliberately does not apply: a
        Senior Technician outranks a Junior on competence and has no standing over
        their Thursday.

        Asserted on **imports and calls** through the AST rather than on the text.
        These modules explain the decision in prose that necessarily names the
        thing being excluded — the docstring above this file contains the word
        `outranks` — and a substring scan reads the explanation as the code. Same
        mistake as a contract test passing because the word is in a comment.
        """
        for path in (TIMEOFF, TIMEOFF_PY):
            with self.subTest(path=path.name):
                tree = ast.parse(_text(path))
                imported = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        imported.add(node.module or "")
                        imported.update(alias.name for alias in node.names)
                    elif isinstance(node, ast.Import):
                        imported.update(alias.name for alias in node.names)
                called = {
                    n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
                    for n in ast.walk(tree)
                    if isinstance(n, ast.Call)
                }
                for token in ("outranks", "positions_outranked_by", "signable_learner_users"):
                    with self.subTest(token=token):
                        self.assertNotIn(token, imported)
                        self.assertNotIn(token, called)
                self.assertFalse(
                    any("position" in name.lower() for name in imported),
                    f"{path.name} imports from the Position ladder",
                )

    def test_the_row_filter_has_no_tier_arm_either(self):
        body = _fn("timeoff_query_conditions", PERMISSIONS)
        self.assertNotIn("signable_learner_users", body)
        self.assertIn("reports_to", body)

    def test_the_approver_is_frozen(self):
        """A reporting-line change between the request and the decision must not
        silently move who was allowed to decide — same doctrine as the sign-off."""
        body = _fn("_resolve_approver", TIMEOFF_PY)
        self.assertIn("if self.approver:", body)

    def test_you_cannot_decide_your_own(self):
        """First and unconditional, whatever roles you hold. The line an auditor
        reads out."""
        body = _fn("decide", TIMEOFF)
        self.assertIn("if doc.user == me:", body)
        self.assertLess(body.index("doc.user == me"), body.index("_may_decide"))

    def test_an_unroutable_request_says_so(self):
        """A request nobody owns is one nobody finds out about until it is too
        late — the same failure the training sign-off routing was built to avoid."""
        self.assertIn("if not doc.approver_user:", _fn("submit_request", TIMEOFF))

    def test_declining_needs_a_reason(self):
        self.assertIn("_require_decline_reason", _text(TIMEOFF_PY))

    def test_an_overlap_warns_and_never_refuses(self):
        """Two people off the same week is a scheduling conversation, not this
        record's business to prevent. Blocking would mean somebody who genuinely
        needs the day simply not asking."""
        body = _fn("_warn_on_overlap", TIMEOFF_PY)
        self.assertIn("msgprint", body)
        self.assertNotIn("throw", body)

    def test_an_approved_request_can_still_be_canceled(self):
        """Plans change, and a system that makes somebody keep a day off they no
        longer want is a system people route around."""
        body = _fn("cancel_request", TIMEOFF)
        self.assertIn("APPROVED", body)


class TestTheCalendarIsHonest(unittest.TestCase):
    def test_only_approved_is_green(self):
        """A Requested day is not a day off yet, and a calendar showing it as one
        would have somebody scheduling around a request that later gets declined."""
        # Anchored on the definition, not the first mention: the comment above it
        # explains the choice and names `get_css_class` too.
        js = _text(CALENDAR)
        at = js.index("get_css_class: function")
        block = js[at : js.index("\n\t},", at)]
        self.assertIn('data.status === "Approved"', block)
        self.assertIn('"success"', block)
        self.assertIn('data.status === "Requested"', block)
        self.assertIn('"warning"', block)

    def test_it_uses_the_key_frappe_actually_reads(self):
        """`style_map` looks like the right key and is dead config in v16:
        `calendar.js`'s `prepare_colors()` branches only on `get_css_class` and
        otherwise falls back to `d.color`, and grepping `origin/version-16` finds
        `style_map` declared in two places and consumed in none. Shipping it would
        have coloured every status identically while the comment above the block
        claimed only Approved was green — a silent no-op of exactly the kind this
        repo keeps paying for. Caught by the branch review."""
        code = _code(_text(CALENDAR))
        self.assertIn("get_css_class", code)
        self.assertNotIn("style_map", code)

    def test_the_status_reaches_the_colour_function(self):
        """`get_css_class` receives the row, so `status` has to be in `field_map` or
        every day renders the same."""
        js = _text(CALENDAR)
        block = js[js.index("field_map") : js.index("get_events_method")]
        self.assertIn('status: "status"', block)

    def test_who_is_out_says_who_and_when_and_not_why(self):
        """A sick day is not something to publish to the crew."""
        body = _fn("who_is_out", TIMEOFF)
        self.assertIn("employee_name", body)
        self.assertNotIn("reason", body)
        self.assertNotIn("time_off_type", body)


class TestOnboardingRaisesItself(unittest.TestCase):
    def test_it_joins_the_existing_employee_hook(self):
        """Rather than adding a second after_insert whose ordering nobody
        declared."""
        hooks = _text(HOOKS)
        self.assertIn("erpnext_enhancements.hr_enhancements.onboarding.on_employee_insert", hooks)
        at = hooks.index("erpnext_enhancements.hr_enhancements.onboarding.on_employee_insert")
        window = hooks[max(0, at - 800) : at]
        self.assertIn("training.assignment.on_employee_insert", window)

    def test_it_cannot_block_the_insert(self):
        """An Employee record failing to save because a checklist could not be
        built would be the tail wagging the dog."""
        body = _fn("on_employee_insert", ONBOARDING)
        self.assertIn("except Exception:", body)
        self.assertIn("log_error", body)

    def test_it_is_idempotent(self):
        """Reachable from a doc_event, a patch and a button.

        Keyed on ``(employee, kind)`` since WI-073 added the leaving checklist: a
        person joins once and leaves once, and the joining record must not stop
        the leaving one being raised.
        """
        self.assertIn(
            'frappe.db.exists(CHECKLIST, {"employee": employee, "kind": kind})',
            _fn("ensure_checklist", ONBOARDING),
        )

    def test_owners_are_words_not_links(self):
        """Half of a first week is done by whoever is free, and a required assignee
        is how a checklist stops getting filled in."""
        field = _fields(ITEM_JSON)["owner_role"]
        self.assertEqual(field["fieldtype"], "Data")

    def test_progress_is_derived_not_stored_by_hand(self):
        """A stored percentage goes stale the moment somebody ticks a box."""
        src = _text(CHECKLIST_PY)
        self.assertIn("_derive_progress", src)
        for name in ("done_count", "total_count", "percent_done"):
            with self.subTest(field=name):
                self.assertEqual(_fields(CHECKLIST_JSON)[name].get("read_only"), 1)

    def test_a_tick_is_stamped_once(self):
        """Re-stamping every save would rewrite the date somebody did the thing to
        the date somebody else opened the form."""
        body = _fn("_stamp_ticks", CHECKLIST_PY)
        self.assertIn("and not row.done_on", body)

    def test_un_ticking_clears_the_stamp(self):
        """Otherwise the record claims it was done by somebody on a date while
        showing it as outstanding."""
        body = _fn("_stamp_ticks", CHECKLIST_PY)
        self.assertIn("row.done_on = None", body)

    def test_one_checklist_per_employee_per_kind(self):
        """The DocField `unique` came OFF `employee` when leaving was added, and
        that is the correct direction: uniqueness is (employee, kind), which a
        single-column unique index cannot express. It is enforced in
        `ensure_checklist` instead, and the test moved with it rather than being
        deleted — an unenforced invariant with no test is how the second checklist
        silently stops appearing."""
        self.assertNotEqual(_fields(CHECKLIST_JSON)["employee"].get("unique"), 1)
        body = _fn("ensure_checklist", ONBOARDING)
        self.assertIn('"kind": kind', body)
        self.assertIn("return existing", body)

    def test_the_default_list_covers_a_real_first_week(self):
        src = _text(ONBOARDING)
        for expected in ("PPE", "Payroll", "safety orientation", "ride-along"):
            with self.subTest(item=expected):
                self.assertIn(expected, src)


class TestBothAreScoped(unittest.TestCase):
    """Both grant the `Employee` role, which every staff account on this site
    holds. DocPerms with no scoping hook is the one combination that leaks, and
    this branch has already found four instances of it."""

    LEAKY = ("Time Off Request", "Onboarding Checklist")

    def _hooks_dict(self, name):
        for node in ast.parse(_text(HOOKS)).body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets
            ):
                return ast.literal_eval(node.value)
        raise AssertionError(name)

    def test_both_have_a_query_condition(self):
        registered = self._hooks_dict("permission_query_conditions")
        for doctype in self.LEAKY:
            with self.subTest(doctype=doctype):
                self.assertIn(doctype, registered)

    def test_both_have_the_single_document_twin(self):
        registered = self._hooks_dict("has_permission")
        for doctype in self.LEAKY:
            with self.subTest(doctype=doctype):
                self.assertIn(doctype, registered)

    def test_an_approver_can_see_what_they_must_decide(self):
        """Even when the requester is not one of their direct reports — a stand-in
        approver would otherwise be asked to decide something they cannot open."""
        self.assertIn("approver_user", _fn("timeoff_query_conditions", PERMISSIONS))


class TestTheStatusCannotBeSelfApproved(unittest.TestCase):
    """The hole the branch review found, and it made the approval flow decorative.

    `status` was an ordinary editable Select and the `Employee` role holds write on
    this doctype, so anybody could open their own request in the Desk and set it to
    `Approved`. Nothing else in the flow would have noticed.

    `read_only` on the field is not the fix on its own — Frappe does not enforce
    read-only against the API — so the controller refuses any status change that did
    not come through `hr_enhancements/timeoff.py`.
    """

    def test_the_decision_fields_are_read_only(self):
        fields = _fields(TIMEOFF_JSON)
        for name in ("status", "decided_on", "decision_note"):
            with self.subTest(field=name):
                self.assertEqual(fields[name].get("read_only"), 1)

    def test_the_controller_refuses_a_status_change_from_anywhere_else(self):
        """read_only hides the field in the form and nothing more."""
        self.assertIn("_guard_status", _fn("validate", TIMEOFF_PY))
        body = _fn("_guard_status", TIMEOFF_PY)
        self.assertIn("get_doc_before_save", body)
        self.assertIn("frappe.throw", body)

    def test_the_endpoints_flag_their_own_transitions(self):
        """Or the guard would refuse the legitimate path too."""
        src = _text(TIMEOFF)
        self.assertIn("doc.flags.timeoff_transition = True", src)
        for fn in ("decide", "cancel_request"):
            with self.subTest(fn=fn):
                self.assertIn("timeoff_transition", _fn(fn, TIMEOFF))

    def test_submitting_uses_db_set_and_needs_no_flag(self):
        """`db_set` writes the column without running validate, so the guard never
        sees it — which is correct here: Draft to Requested is the requester's own
        move and the endpoint has already checked they own it."""
        self.assertIn('doc.db_set("status", REQUESTED)', _fn("submit_request", TIMEOFF))

    def test_who_is_out_is_staff_only(self):
        """It was authenticated-only, so a customer contact with a login could
        enumerate every staff member's absences -- a rough map of the company's
        week, and none of their business."""
        body = _fn("who_is_out", TIMEOFF)
        self.assertIn('frappe.db.exists("Employee"', body)
        self.assertIn("PermissionError", body)

    def test_creating_your_own_request_is_not_refused(self):
        """`user` is derived in validate(), so it is empty when the permission check
        runs on a NEW row -- which refused ordinary staff permission to create their
        own request until the review caught it."""
        for fn in ("timeoff_has_permission", "onboarding_has_permission"):
            with self.subTest(fn=fn):
                body = _fn(fn, PERMISSIONS)
                self.assertIn('doc.get("employee")', body)



class TestTheApprovalFlowIsActuallyReachable(unittest.TestCase):
    """The defect the HR feature survey found, and it made the whole feature
    decorative.

    `timeoff.py` whitelists four correct endpoints and NOTHING called any of
    them: no client script on the doctype, no caller anywhere in the app. A Time
    Off Request could be created and then never move -- not in the Desk, not on a
    phone, not by HR.

    Third time this repo has shipped that exact shape: `record_signoff` had no
    caller until v1.334.0, and the visual editor had no entry point from any page
    until this release. The endpoint is the easy half; reachable is the half that
    gets forgotten.

    Note the interaction with the branch review's status guard -- making `status`
    read-only removed the one accidental workaround (editing the Select by hand),
    so the guard and this form have to ship together or time off gets *more*
    broken, not less.
    """

    FORM = MODULE / "doctype/time_off_request/time_off_request.js"

    def test_the_form_script_exists(self):
        self.assertTrue(self.FORM.exists(), "no client script -- the buttons do not exist")

    def test_every_endpoint_has_a_caller(self):
        """The generalisation, not just the three buttons: a whitelisted function
        in timeoff.py that nothing reaches is the bug itself."""
        import ast

        src = _text(TIMEOFF)
        tree = ast.parse(src)
        whitelisted = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if getattr(target, "attr", None) == "whitelist":
                    whitelisted.add(node.name)
        self.assertTrue(whitelisted, "expected whitelisted endpoints")

        callers = _code(_text(self.FORM))
        for extra in (MODULE / "doctype/time_off_request/time_off_request_calendar.js",):
            if extra.exists():
                callers += _code(_text(extra))
        player = APP / "public/js/training/player.js"
        if player.exists():
            callers += _code(_text(player))

        for name in sorted(whitelisted):
            with self.subTest(endpoint=name):
                self.assertIn(
                    f"timeoff.{name}",
                    callers,
                    f"{name} is whitelisted and nothing calls it",
                )

    def test_the_decision_buttons_are_drawn_only_for_a_decider(self):
        """A button that can only fail is worse than no button."""
        body = _code(_text(self.FORM))
        at = body.index('doc.status === "Requested"')
        branch = body[at : at + 200]
        self.assertIn("decider", branch)
        self.assertIn("!mine", branch)

    def test_declining_asks_for_the_reason_before_the_server_refuses(self):
        """The server requires it. Finding that out from a red modal after typing
        nothing is a worse way to learn it."""
        body = _code(_text(self.FORM))
        decline = body[body.index("ee_decline(frm)") :]
        decline = decline[: decline.index("ee_cancel(frm)")]
        self.assertIn("reqd: 1", decline)


class TestWorkAnniversariesRecur(unittest.TestCase):
    """The feed has always known how to RENDER a work anniversary, and the only
    thing that ever minted one was the one-shot backfill patch -- so the feed
    would have opened with sixteen and produced not one more, ever.
    """

    TASKS = MODULE / "tasks.py"
    HOOKS = APP / "hooks.py"

    def test_there_is_a_recurring_producer(self):
        self.assertIn("def mint_work_anniversaries", _text(self.TASKS))

    def test_it_is_scheduled_daily(self):
        import ast

        tree = ast.parse(_text(self.HOOKS))
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "scheduler_events":
                events = ast.literal_eval(node.value)
                for cron, jobs in (events.get("cron") or {}).items():
                    if any("mint_work_anniversaries" in j for j in jobs):
                        found = True
                        # Daily, not weekly or monthly: an anniversary is one day.
                        self.assertEqual(cron.split()[2:], ["*", "*", "*"])
        self.assertTrue(found, "mint_work_anniversaries is not on the scheduler")

    def test_it_does_not_force_past_the_dormancy_guard(self):
        """The backfill passes `force=True` because it runs inside a migrate. An
        ordinary scheduled job must NOT: staying dormant during a migrate is the
        point of that guard."""
        body = _fn("mint_work_anniversaries", self.TASKS)
        self.assertIn("social.record(", body)
        self.assertNotIn("force=True", _strip_comments(body))

    def test_the_leap_day_rule_matches_the_backfill(self):
        """Both mark 29 February joiners on the 28th rather than skipping three
        years in four. Kept identical so the two cannot disagree about somebody."""
        self.assertIn("28", _fn("_is_the_day", self.TASKS))
        self.assertIn("(2, 29)", _fn("_is_the_day", self.TASKS))

class TestTheLeavingChecklistIsGenerated(unittest.TestCase):
    """WI-073 F. ERPNext disables a departing employee's login by itself and does
    nothing else — the device in their van, the credential the insurer asked
    about, the four jobs assigned to them and the two people who report to them
    are all invisible the day after.
    """

    ONBOARDING_JSON = MODULE / "doctype/onboarding_checklist/onboarding_checklist.json"

    def test_one_record_shape_serves_both_ends(self):
        fields = _fields(self.ONBOARDING_JSON)
        self.assertEqual(fields["kind"]["options"].splitlines(), ["Joining", "Leaving"])

    def test_employee_is_no_longer_singly_unique(self):
        """A person joins once and leaves once, so uniqueness is (employee, kind)
        — which a single-column DocField `unique` cannot express."""
        fields = _fields(self.ONBOARDING_JSON)
        self.assertNotEqual(fields["employee"].get("unique"), 1)
        body = _fn("ensure_checklist", ONBOARDING)
        self.assertIn('{"employee": employee, "kind": kind}', body)

    def test_the_list_is_derived_from_what_they_hold(self):
        """A fixed checklist cannot know any of it, so it gets filled in from
        memory — the failure this exists to remove."""
        body = _fn("_derived_leaving_rows", ONBOARDING)
        for source in ("_devices_held", "_credentials_held", "_open_work", "_direct_reports", "_vehicles_held"):
            with self.subTest(source=source):
                self.assertIn(source, body)

    def test_direct_reports_are_re_pointed(self):
        """Somebody whose manager has left has no manager, and nothing says so:
        time-off requests route to an empty approver and never move."""
        body = _fn("_direct_reports", ONBOARDING)
        self.assertIn("reports_to", body)

    def test_open_training_is_closed_or_reassigned(self):
        """An assignment against somebody who has left sits in the compliance
        figures for ever and quietly makes the numbers wrong."""
        self.assertIn("Training Assignment", _fn("_open_work", ONBOARDING))

    def test_every_derived_lookup_is_best_effort(self):
        """A missing module means fewer rows, never an exception — the fixed list
        alone is still worth raising."""
        src = _text(ONBOARDING)
        for fn in ("_devices_held", "_credentials_held", "_open_work", "_direct_reports", "_vehicles_held"):
            with self.subTest(fn=fn):
                self.assertIn("except Exception", _fn(fn, ONBOARDING))

    def test_it_fires_on_the_transition_not_the_value(self):
        """Employee is saved often, and an unguarded check would try on every
        save."""
        body = _fn("on_employee_update", ONBOARDING)
        self.assertIn("get_doc_before_save", body)
        self.assertIn('before.status or "") == "Left"', body)

    def test_it_cannot_fail_an_employee_save(self):
        body = _fn("on_employee_update", ONBOARDING)
        self.assertIn("except Exception", body)
        self.assertIn("log_error", body)

    def test_owners_are_still_plain_words(self):
        """Half of a last week is done by whoever is free that morning, and a
        required assignee is how a checklist stops getting filled in."""
        import ast as _ast

        # Parsed, not sliced. `src.index(")", at)` stops at the close of the FIRST
        # inner tuple, so the assertion was reading one row and reporting on seven.
        owners = set()
        for node in _ast.parse(_text(ONBOARDING)).body:
            if isinstance(node, _ast.Assign) and getattr(node.targets[0], "id", "") == "LEAVING_ITEMS":
                owners = {row[1] for row in _ast.literal_eval(node.value)}
        self.assertTrue(owners, "LEAVING_ITEMS not found")
        self.assertEqual(owners, {"Shop", "Supervisor", "HR", "Finance"})
        # And they are strings, not Links.
        self.assertEqual(_fields(ITEM_JSON)["owner_role"]["fieldtype"], "Data")


class TestTheCheckInsAreAPromptNotAForm(unittest.TestCase):
    """The value is "go and ask them how it is going". A form attached to it turns
    a two-minute conversation into an admin task, which is how the conversation
    stops happening.
    """

    def test_it_stores_nothing(self):
        body = _fn("nudge_new_hire_check_ins", ONBOARDING)
        self.assertNotIn("insert(", body)
        self.assertNotIn("new_doc", body)
        self.assertNotIn("db_set", body)

    def test_it_fires_on_the_three_days(self):
        self.assertIn("CHECK_IN_DAYS = (30, 60, 90)", _text(ONBOARDING))
        self.assertIn("days not in CHECK_IN_DAYS", _fn("nudge_new_hire_check_ins", ONBOARDING))

    def test_it_goes_to_the_supervisor(self):
        body = _fn("nudge_new_hire_check_ins", ONBOARDING)
        self.assertIn("reports_to", body)

    def test_it_says_there_is_nothing_to_fill_in(self):
        self.assertIn("nothing to fill in", _fn("nudge_new_hire_check_ins", ONBOARDING))

    def test_it_never_takes_the_scheduler_down(self):
        body = _fn("nudge_new_hire_check_ins", ONBOARDING)
        self.assertIn("except Exception", body)

    def test_both_daily_jobs_share_their_slot_safely(self):
        """Parsed, not string-matched: a repeated cron key REPLACES the earlier
        one, which silently disabled four chat sweeps earlier in this release."""
        import ast as _ast

        tree = _ast.parse(_text(HOOKS))
        events = {}
        for node in tree.body:
            if isinstance(node, _ast.Assign) and getattr(node.targets[0], "id", "") == "scheduler_events":
                events = _ast.literal_eval(node.value)
        jobs = (events.get("cron") or {}).get("10 6 * * *") or []
        self.assertIn("erpnext_enhancements.hr_enhancements.onboarding.nudge_new_hire_check_ins", jobs)
        self.assertIn("erpnext_enhancements.hr_enhancements.tasks.mint_work_anniversaries", jobs)



if __name__ == "__main__":
    unittest.main()
