# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Receipt / Expense posting path, which could never run.

`Create Expense Claim` was the only action extraction ever proposed for a
`Receipt / Expense`, and `Expense Claim` is an **hrms** doctype that is not
installed on this site. So `frappe.new_doc("Expense Claim")` raised a bare
`DoesNotExistError`, the dispatcher's broad `except` turned it into a generic
*Failed* with a truncated traceback, and every pass burned a retry against
`retry_limit`. It had never fired: `tabDocument Intake` was empty when this was
found, which is the only reason nobody had met it.

Most of what is asserted here is about **not matching Suppliers by name**. The
seven reimbursement Suppliers on prod use three naming shapes, two do not contain
their Employee's name as stored, and there are 1,180 Suppliers on the site — so a
near-match does not fail, it silently bills somebody's receipt to a real vendor
and looks entirely ordinary until it is paid.

Run: python -m unittest erpnext_enhancements.tests.test_intake_reimbursement
"""

import ast
import io
import json
import tokenize
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
INTAKE = APP / "accounting_intake"
RECEIPT = INTAKE / "actions/receipt_expense.py"
VENDOR = INTAKE / "actions/vendor_bill.py"
EXTRACTION = INTAKE / "extraction.py"
INTAKE_JSON = INTAKE / "doctype/document_intake/document_intake.json"
PATCH = APP / "patches/link_reimbursement_suppliers.py"
FIXTURES = APP / "fixtures/custom_field.json"
HOOKS = APP / "hooks.py"
TRAVEL = APP / "travel_management/api.py"
REVIEW = INTAKE / "review.py"


def _text(path):
    return path.read_text(encoding="utf-8")


def _src(path):
    """Source with comments and docstrings stripped.

    Every absence assertion below is about what the code DOES, and this module's
    prose necessarily names the things being excluded -- the docstring explaining
    why there is no name matching contains the words "supplier_name" and "like".
    Reading those as code is how five assertions in a recent release passed on
    broken code.
    """
    kept = [
        t
        for t in tokenize.generate_tokens(io.StringIO(_text(path)).readline)
        if t.type != tokenize.COMMENT
    ]
    tree = ast.parse(tokenize.untokenize(kept))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body[0].value.value = ""
    return ast.unparse(tree)


def _matcher():
    """The patch's real `_normalise` and `_NOISE`, behind a minimal frappe stub.

    Imported rather than reimplemented -- a copy of the matcher in the test is a
    test of the copy. The module needs only `import frappe` at import time; nothing
    it does at module scope touches the database.
    """
    import sys
    import types

    if "frappe" not in sys.modules:
        stub = types.ModuleType("frappe")
        stub.db = types.SimpleNamespace()
        sys.modules["frappe"] = stub
    from erpnext_enhancements.patches import link_reimbursement_suppliers as mod

    return mod._normalise, mod._NOISE


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


class TestTheExpenseClaimPathIsGuarded(unittest.TestCase):
    """The minimal half of the fix: say the real cause."""

    def test_the_handler_guards_before_touching_the_doctype(self):
        body = _fn("post_expense_claim", RECEIPT)
        self.assertIn("_require_expense_claims()", body)
        # And the guard must come BEFORE new_doc, or it guards nothing.
        self.assertLess(body.index("_require_expense_claims()"), body.index("frappe.new_doc"))

    def test_the_guard_names_the_cause_and_the_way_out(self):
        body = _fn("_require_expense_claims", RECEIPT)
        self.assertIn("hrms", body)
        self.assertIn("title=", body)
        self.assertIn("REIMBURSEMENT_BILL", body)

    def test_it_matches_the_shape_travel_already_established(self):
        """`travel_management/api.py::_require_hrms` has guarded this exact
        situation for releases, with a docstring saying it exists so
        `frappe.new_doc("Expense Claim")` does not raise a raw DoesNotExistError.
        The intake handler never got the same treatment."""
        travel = _fn("_require_hrms", TRAVEL)
        self.assertIn('frappe.db.exists("DocType"', travel)
        guard = _fn("_require_expense_claims", RECEIPT)
        self.assertIn("expense_claims_available()", guard)
        self.assertIn('frappe.db.exists("DocType", "Expense Claim")', _fn("expense_claims_available", RECEIPT))


class TestTheProposalIsReachableBeforeItIsOffered(unittest.TestCase):
    """Fail at the point of choice, not the point of posting."""

    def test_extraction_asks_what_this_site_can_post(self):
        body = _fn("_action_for", EXTRACTION)
        self.assertIn("expense_claims_available", body)
        self.assertIn("Create Reimbursement Bill", body)

    def test_the_dict_is_no_longer_read_directly_for_the_proposal(self):
        src = _src(EXTRACTION)
        self.assertIn("_action_for(doc.document_type)", src)
        self.assertNotIn("_ACTION_BY_DOC.get(doc.document_type)", src)

    def test_the_check_is_live_not_resolved_at_import(self):
        """A site can gain hrms between two extractions, and a module constant
        resolved at import would keep proposing the wrong action until the workers
        were restarted."""
        src = _src(EXTRACTION)
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, ast.Assign):
                with self.subTest(line=node.lineno):
                    self.assertNotIn("expense_claims_available", ast.unparse(node.value))

    def test_the_new_action_is_a_selectable_option(self):
        fields = {f["fieldname"]: f for f in json.loads(_text(INTAKE_JSON))["fields"]}
        options = fields["proposed_action"]["options"].splitlines()
        self.assertIn("Create Reimbursement Bill", options)
        # The hrms one stays -- a site WITH hrms should still be offered it.
        self.assertIn("Create Expense Claim", options)

    def test_every_selectable_action_has_a_handler(self):
        """The generalisation. An option with no registered handler is silently
        skipped by the dispatcher -- it logs "No handler for X" and the document
        sits Approved forever, which is a quieter version of the same bug."""
        fields = {f["fieldname"]: f for f in json.loads(_text(INTAKE_JSON))["fields"]}
        options = {
            o
            for o in fields["proposed_action"]["options"].splitlines()
            # "" is the unset default; the other two are terminal choices that
            # deliberately post nothing.
            if o and o not in ("Standalone (no match)", "Ignore")
        }
        self.assertGreaterEqual(len(options), 5, "the option scan found almost nothing")
        registered = set()
        for path in INTAKE.glob("actions/*.py"):
            for node in ast.walk(ast.parse(_text(path))):
                if not isinstance(node, ast.FunctionDef):
                    continue
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and getattr(dec.func, "id", None) == "register":
                        arg = dec.args[0]
                        if isinstance(arg, ast.Constant):
                            registered.add(arg.value)
                        elif isinstance(arg, ast.Name):
                            # A module constant -- resolve it from the same file.
                            for stmt in ast.parse(_text(path)).body:
                                if (
                                    isinstance(stmt, ast.Assign)
                                    and getattr(stmt.targets[0], "id", None) == arg.id
                                    and isinstance(stmt.value, ast.Constant)
                                ):
                                    registered.add(stmt.value.value)
        self.assertEqual(options - registered, set(), "selectable actions with no handler")


class TestTheSupplierIsNeverMatchedByName(unittest.TestCase):
    """The decision this whole change turns on.

    Prod: three naming shapes across seven Suppliers, two that do not contain
    their Employee's name as stored (`Danny Rosser` is Daniel Rosser, `Lian Silva`
    is Lian Jentz Da Silva), nine staff with none at all, and 1,180 Suppliers to
    collide with.
    """

    def test_the_handler_reads_an_explicit_link(self):
        body = _fn("reimbursement_supplier", RECEIPT)
        self.assertIn("custom_reimbursement_supplier", body)

    def test_the_handler_does_no_name_matching_at_all(self):
        """Asserted on the code with comments and docstrings stripped, because the
        prose here necessarily explains what is being avoided."""
        src = _src(RECEIPT)
        at = src.index("def reimbursement_supplier")
        block = src[at : src.index("def post_expense_claim")]
        for token in ("supplier_name", "like", "LIKE", "Reimbursement"):
            with self.subTest(token=token):
                self.assertNotIn(token, block)

    def test_the_field_exists_as_a_fixture(self):
        names = {f.get("name") for f in json.loads(_text(FIXTURES))}
        self.assertIn("Employee-custom_reimbursement_supplier", names)
        field = next(
            f for f in json.loads(_text(FIXTURES)) if f.get("name") == "Employee-custom_reimbursement_supplier"
        )
        self.assertEqual(field["fieldtype"], "Link")
        self.assertEqual(field["options"], "Supplier")

    def test_a_dead_or_disabled_link_is_treated_as_absent(self):
        """A link can outlive its target, and posting against a deleted or disabled
        Supplier fails deep inside the Purchase Invoice controller with a message
        about nothing in particular."""
        body = _fn("reimbursement_supplier", RECEIPT)
        self.assertIn("disabled", body)

    def test_the_missing_link_refuses_with_a_usable_message(self):
        body = _fn("post_reimbursement_bill", RECEIPT)
        at = body.index("if not supplier:")
        branch = body[at : at + 700]
        self.assertIn("frappe.throw", branch)
        self.assertIn("Reimbursement Supplier", branch)
        self.assertIn("Create Purchase Invoice", branch)

    def test_the_patch_links_only_unambiguous_matches(self):
        body = _fn("link_reimbursement_suppliers", PATCH)
        self.assertIn("len(candidates) != 1", body)
        self.assertIn("continue", body)

    def test_the_patch_never_overwrites_a_human(self):
        body = _fn("link_reimbursement_suppliers", PATCH)
        self.assertIn("if employee.get(FIELD):", body)

    def test_the_matcher_links_exactly_the_five_it_should(self):
        """**Behavioural, not a token blacklist.**

        This was `assertNotIn("difflib", ...)` and four more names, which the
        adversarial review correctly called a five-name denylist that any
        hand-rolled matcher walks straight past -- a token-subset check written
        inline would have passed it while linking `Lian Silva` to somebody else.

        So it drives the real `_normalise` and `_NOISE` against the seven Supplier
        names and sixteen Employee names that are actually on prod, and pins the
        pairing. They are pure functions over strings; only the module's
        `import frappe` stands between them and a bench-free test, and that import
        is satisfied by the stub below.
        """
        normalise, noise = _matcher()

        suppliers = [
            "Jesse Griffin Reimbursement",
            "Danny Rosser Reimbursement",
            "Employee Clegg Mabey Reimbursement",
            "Nathan Cox Reimbursement",
            "Lisa Symanski Reimbursement",
            "Lian Silva Reimbursement",
            "Logan Penrod Employee Reimbursement",
        ]
        employees = [
            "Cedrik Del Rosario", "Logan Penrod", "Korben Jessop", "Lian Jentz Da Silva",
            "Nathan Cox", "Daniel Rosser", "Lisa Symanski", "Clegg Mabey", "Parker Bailey",
            "Jesse Griffin", "Brian Morisseau", "Austin Healey", "Daniel Blass",
            "Richard Hansen", "Nikolas Bradshaw", "James Harris",
        ]
        by_name = {}
        for e in employees:
            by_name.setdefault(normalise(e), []).append(e)

        linked = {}
        for supplier in suppliers:
            candidates = by_name.get(normalise(noise.sub(" ", supplier))) or []
            if len(candidates) == 1:
                linked[supplier] = candidates[0]

        self.assertEqual(
            linked,
            {
                "Jesse Griffin Reimbursement": "Jesse Griffin",
                "Employee Clegg Mabey Reimbursement": "Clegg Mabey",
                "Nathan Cox Reimbursement": "Nathan Cox",
                "Lisa Symanski Reimbursement": "Lisa Symanski",
                "Logan Penrod Employee Reimbursement": "Logan Penrod",
            },
        )

    def test_the_two_nicknames_are_left_for_a_human(self):
        """`Danny Rosser` is Employee *Daniel Rosser* and `Lian Silva` is *Lian
        Jentz Da Silva*. Both are obvious to a person and neither is safe for a
        matcher: the same leniency that catches them reaches other names too, and
        there are 1,180 Suppliers to reach."""
        normalise, noise = _matcher()
        employees = {normalise(e) for e in ("Daniel Rosser", "Lian Jentz Da Silva")}
        for supplier in ("Danny Rosser Reimbursement", "Lian Silva Reimbursement"):
            with self.subTest(supplier=supplier):
                self.assertNotIn(normalise(noise.sub(" ", supplier)), employees)

    def test_the_matcher_does_not_collapse_two_different_people(self):
        """The failure that matters is not a miss, it is a wrong hit."""
        normalise, noise = _matcher()
        keys = [normalise(noise.sub(" ", s)) for s in (
            "Daniel Blass Reimbursement", "Daniel Rosser Reimbursement",
            "James Harris Reimbursement", "Jesse Griffin Reimbursement",
        )]
        self.assertEqual(len(set(keys)), len(keys), "two different people share a key")


class TestTheBillGoesToThePersonNotTheShop(unittest.TestCase):
    def test_the_builder_takes_an_explicit_supplier_override(self):
        """On a reimbursement the intake's party is the MERCHANT. Defaulting to it
        and letting the caller forget is how a receipt becomes a bill payable to
        the hardware store instead of to the person who paid for it."""
        body = _fn("build_standalone_pi", VENDOR)
        self.assertIn("supplier or doc.party", body)

    def test_the_handler_passes_the_reimbursement_supplier(self):
        body = _fn("post_reimbursement_bill", RECEIPT)
        self.assertIn("supplier=supplier", body)

    def test_the_merchant_is_preserved_where_a_human_reads_it(self):
        body = _fn("post_reimbursement_bill", RECEIPT)
        self.assertIn("merchant", body)
        self.assertIn("remarks", body)

    def test_the_shared_builder_is_not_duplicated(self):
        """Two copies of this means two places to fix the next time the
        expense-account fallback or the no-lines case changes."""
        self.assertIn("from erpnext_enhancements.accounting_intake.actions import vendor_bill", _text(RECEIPT))
        self.assertNotIn("frappe.new_doc(\"Purchase Invoice\")", _text(RECEIPT))


class TestNobodyElseGetsReimbursedByAccident(unittest.TestCase):
    """The second latent bug, found while reading the first."""

    def test_the_payer_is_read_from_the_record_and_inferred_from_nothing(self):
        """**The defect the adversarial review found in this very change.**

        `_employee_for` resolved `doc.reviewed_by or frappe.session.user`, and
        `reviewed_by` is stamped by `approve_document`, which is gated on
        Accounts Manager / System Manager. The approver is by role design NOT the
        claimant — so a technician's receipt, approved by the accountant, produced
        a draft Purchase Invoice payable to the *accountant's* reimbursement
        Supplier, with remarks naming them as the person owed the money. On every
        receipt, not as an edge case.

        The payer now comes from an explicit field and nowhere else.
        """
        body = _src(RECEIPT)
        at = body.index("def payer")
        block = body[at : body.index("def _default_expense_claim_type")]
        self.assertIn("paid_by_employee", block)
        for inferred in ("reviewed_by", "session.user", "owner"):
            with self.subTest(token=inferred):
                self.assertNotIn(inferred, block)

    def test_no_handler_infers_a_payer_either(self):
        """The generalisation: nothing in this module may reach for the session
        user or the reviewer when deciding who gets the money."""
        body = _src(RECEIPT)
        for inferred in ("reviewed_by", "frappe.session.user"):
            with self.subTest(token=inferred):
                self.assertNotIn(inferred, body)

    def test_an_inactive_employee_is_not_a_payer(self):
        """A link can outlive its target, and reimbursing somebody who has left
        through this route is at best a surprise."""
        self.assertIn("status", _fn("payer", RECEIPT))

    def test_approval_refuses_a_reimbursement_with_no_payer(self):
        """Checked at the gate, not only in the handler. The handler runs in a
        BACKGROUND job, so a problem it finds surfaces as a Failed document with a
        traceback rather than as a sentence beside the button somebody just
        pressed -- and burns a retry attempt on the way."""
        body = _fn("_reimbursement_issues", REVIEW)
        self.assertIn("Create Reimbursement Bill", body)
        self.assertIn("payer(doc)", body)
        self.assertIn("reimbursement_supplier", body)
        self.assertIn("_reimbursement_issues(doc)", _fn("_validate_for_approval", REVIEW))

    def test_the_field_is_mandatory_on_the_form_as_well(self):
        """`mandatory_depends_on` is the human-facing half; the gate above is the
        API-side twin, because Frappe does not enforce it against a direct write
        and Document Intake is writable by Accounts User."""
        fields = {f["fieldname"]: f for f in json.loads(_text(INTAKE_JSON))["fields"]}
        paid_by = fields["paid_by_employee"]
        self.assertEqual(paid_by["options"], "Employee")
        self.assertIn("Create Reimbursement Bill", paid_by["mandatory_depends_on"])

    def test_the_email_sender_is_a_suggestion_not_a_decision(self):
        """Filled into a field the reviewer can see and change. The handler still
        refuses to post without it, so a wrong suggestion is corrected in front of
        somebody rather than discovered in the ledger."""
        body = _fn("_suggest_payer", EXTRACTION)
        self.assertIn('doc.source_channel != "Email"', body)
        self.assertIn("source_reference", body)
        # Only Email carries a person. Guessing on the other three is the bug this
        # replaced: Upload and Mobile are role-gated to accounting staff, and
        # Drive rows are owned by the scheduler.
        self.assertNotIn("doc.owner", body)

    def test_both_handlers_refuse_when_there_is_nobody(self):
        for fn in ("post_reimbursement_bill", "post_expense_claim"):
            with self.subTest(fn=fn):
                body = _fn(fn, RECEIPT)
                at = body.index("if not employee:")
                self.assertIn("frappe.throw", body[at : at + 500])


class TestTheMigrateOrderingIsRespected(unittest.TestCase):
    def test_the_patch_guards_the_fixture_column(self):
        """`custom_reimbursement_supplier` is a FIXTURE Custom Field and
        `sync_fixtures()` runs AFTER the post-model-sync patches, so on the migrate
        that introduces it the column does not exist when the patch runs."""
        body = _fn("execute", PATCH)
        self.assertIn("has_column", body)
        self.assertIn("except Exception", body)

    def test_has_column_is_given_a_doctype_not_a_table(self):
        """It prefixes `tab` itself and RAISES TableMissingError on an unknown
        table -- it never returns False. Five call sites got this wrong in
        v1.386.0."""
        for path in (PATCH, RECEIPT):
            with self.subTest(path=path.name):
                self.assertNotIn('has_column("tab', _text(path))

    def test_there_is_an_after_migrate_twin(self):
        """A patch that returns having done nothing still records itself in Patch
        Log and never runs again. That trap has bitten this app three times."""
        hooks = _text(HOOKS)
        self.assertIn(
            "erpnext_enhancements.patches.link_reimbursement_suppliers.link_reimbursement_suppliers",
            hooks,
        )

    def test_the_patch_is_registered(self):
        self.assertIn(
            "erpnext_enhancements.patches.link_reimbursement_suppliers",
            _text(APP / "patches.txt"),
        )


class TestTheOrphanStubIsGone(unittest.TestCase):
    """A controller for a doctype that does not exist, in a module that does not
    claim it.

    `load_doctype_module` resolves through the DocType's own `module` field
    (`frappe origin/version-16:frappe/modules/utils.py:291-299`), so this
    controller was only ever loadable if a DocType named "Expense Claim Type"
    declared module "Enhancements Core". Nothing did -- there was no JSON beside
    it -- and hrms's own copy declares module "HR". `MODULE_PLAN.md` recorded that
    finding in v1.47.0 and left the folder in place.
    """

    def test_the_stub_directory_is_removed(self):
        self.assertFalse((APP / "enhancements_core/doctype/expense_claim_type").exists())

    def test_the_module_readme_no_longer_advertises_it(self):
        self.assertNotIn("expense_claim_type", _text(APP / "enhancements_core/README.md"))


if __name__ == "__main__":
    unittest.main()
