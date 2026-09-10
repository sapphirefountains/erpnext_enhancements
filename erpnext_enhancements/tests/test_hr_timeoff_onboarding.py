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


def _fields(path):
    return {f["fieldname"]: f for f in json.loads(_text(path))["fields"]}


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
        js = _text(CALENDAR)
        at = js.index("style_map")
        block = js[at : js.index("}", at)]
        self.assertIn('Approved: "success"', block)
        self.assertIn('Requested: "warning"', block)

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
        """Reachable from a doc_event, a patch and a button."""
        self.assertIn('frappe.db.exists(CHECKLIST, {"employee": employee})', _fn("ensure_checklist", ONBOARDING))

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

    def test_one_checklist_per_employee(self):
        self.assertEqual(_fields(CHECKLIST_JSON)["employee"].get("unique"), 1)

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


if __name__ == "__main__":
    unittest.main()
