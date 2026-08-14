"""Bench-free unit tests for the Purchase Order Order Stage field.

Stubs a minimal ``frappe`` (no site/bench) so the pure decision logic in
``erpnext_enhancements.po_order_stage`` runs under plain unittest. The stub is installed
in ``setUpModule`` (execution time), not at import, so it never fools the bench-only
suites' ``import frappe`` skip-guards.

What is actually worth pinning here, all of it invisible until production:

* **The backfill rule**, because the patch it drives cannot key on emptiness. The column's
  default is written into every existing row by the ALTER that adds it, so the predicate
  has to be the writer's own rule and a test is the only thing that keeps the two in step.
* **The Select options come from ``STAGES``**, so the field, the backfill and the hooks
  cannot drift onto different spellings. A stored value that is no longer a valid option
  makes the row refuse to save.
* **The hooks no-op when the column is missing**, because they fire during erpnext's own
  test bootstrap, before the patch that creates it has run on a fresh database.
* **Neither hook can raise**, because both hang off Purchase Receipt submission.

Run: python -m unittest erpnext_enhancements.tests.test_po_order_stage
"""

import ast
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
HOOKS = APP / "hooks.py"
PATCHES_TXT = APP / "patches.txt"
FIELD_PATCH = APP / "patches/add_po_order_stage_field.py"
BACKFILL_PATCH = APP / "patches/backfill_po_order_stage.py"

# Mutable state the frappe stub reads at call time.
STATE = {"has_column": True, "orders": {}, "comments": [], "errors": [], "raise_on": None}
po_order_stage = None


def _install_frappe_stub():
    frappe = types.ModuleType("frappe")

    def has_column(doctype, column):
        if STATE["raise_on"] == "has_column":
            raise RuntimeError("no database")
        return STATE["has_column"]

    def get_value(doctype, name, field):
        if STATE["raise_on"] == "get_value":
            raise RuntimeError("gone")
        return (STATE["orders"].get(name) or {}).get(field)

    def set_value(doctype, name, field, value, update_modified=True):
        STATE["orders"].setdefault(name, {})[field] = value
        STATE["orders"][name]["_update_modified"] = update_modified

    frappe.db = types.SimpleNamespace(
        has_column=has_column, get_value=get_value, set_value=set_value
    )

    class _PO:
        def __init__(self, name):
            self.name = name

        def add_comment(self, comment_type, text):
            STATE["comments"].append((self.name, comment_type, text))

    frappe.get_doc = lambda doctype, name: _PO(name)
    frappe.get_traceback = lambda: "traceback"
    frappe.log_error = lambda message, title=None: STATE["errors"].append(title)
    frappe.logger = lambda: types.SimpleNamespace(info=lambda msg: None)

    sys.modules["frappe"] = frappe


def setUpModule():
    global po_order_stage
    _install_frappe_stub()
    sys.modules.pop("erpnext_enhancements.po_order_stage", None)
    from erpnext_enhancements import po_order_stage as mod

    po_order_stage = mod


def _reset(has_column=True, orders=None, raise_on=None):
    STATE.update(
        has_column=has_column,
        orders=dict(orders or {}),
        comments=[],
        errors=[],
        raise_on=raise_on,
    )


class _Row(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _Receipt:
    """Minimal Purchase Receipt stand-in: `.name` plus `.get("items")`."""

    def __init__(self, name, purchase_orders):
        self.name = name
        self._items = [_Row(purchase_order=po) for po in purchase_orders]

    def get(self, key):
        return self._items if key == "items" else None


def _order(stage, per_received):
    return {po_order_stage.FIELD: stage, "per_received": per_received}


class TestTheBackfillRule(unittest.TestCase):
    """The patch this drives cannot key on emptiness -- the ALTER that adds the column
    writes its default into every existing row -- so this rule IS the predicate."""

    def rule(self, docstatus, status, per_received):
        return po_order_stage.backfill_stage_for(docstatus, status, per_received)

    def test_a_draft_keeps_the_default(self):
        self.assertEqual(self.rule(0, "Draft", 0), po_order_stage.CREATED)

    def test_a_cancelled_order_is_never_called_received(self):
        """It is not waiting on anyone, but goods did not arrive either."""
        self.assertEqual(self.rule(2, "Cancelled", 0), po_order_stage.CREATED)

    def test_a_fully_received_order_is_received(self):
        self.assertEqual(self.rule(1, "To Bill", 100), po_order_stage.RECEIVED)

    def test_over_receipt_still_counts_as_received(self):
        self.assertEqual(self.rule(1, "To Bill", 120), po_order_stage.RECEIVED)

    def test_closed_and_completed_are_terminal(self):
        """81 of 157 historical orders are Closed having never been receipted. Anywhere
        else leaves them in the waiting-on list forever."""
        self.assertEqual(self.rule(1, "Closed", 0), po_order_stage.RECEIVED)
        self.assertEqual(self.rule(1, "Completed", 100), po_order_stage.RECEIVED)

    def test_a_submitted_open_order_gets_the_weakest_true_statement(self):
        """Committed, not here yet. Delivery vs pickup is recorded nowhere, so the
        backfill does not invent one."""
        self.assertEqual(
            self.rule(1, "To Receive and Bill", 0), po_order_stage.AWAITING_CONFIRMATION
        )

    def test_a_partial_receipt_is_still_waiting(self):
        self.assertEqual(
            self.rule(1, "To Receive and Bill", 99.9), po_order_stage.AWAITING_CONFIRMATION
        )

    def test_none_per_received_is_not_a_crash(self):
        self.assertEqual(
            self.rule(1, "To Receive and Bill", None), po_order_stage.AWAITING_CONFIRMATION
        )

    def test_every_result_is_a_real_option(self):
        """A stage outside the Select makes the row refuse to save."""
        for docstatus in (0, 1, 2):
            for status in ("Draft", "To Receive and Bill", "Closed", "Completed", "Cancelled"):
                for received in (0, 50, 100):
                    with self.subTest(f"{docstatus}/{status}/{received}"):
                        self.assertIn(self.rule(docstatus, status, received), po_order_stage.STAGES)


class TestAdvanceOnReceipt(unittest.TestCase):
    def test_a_completing_receipt_marks_the_order_received(self):
        _reset(orders={"PO-1": _order(po_order_stage.WAITING_FOR_PICKUP, 100)})
        po_order_stage.advance_on_receipt(_Receipt("PR-1", ["PO-1"]))
        self.assertEqual(STATE["orders"]["PO-1"][po_order_stage.FIELD], po_order_stage.RECEIVED)

    def test_a_partial_receipt_leaves_the_stage_alone(self):
        """Still waiting on the rest of the goods; the stage should keep saying so."""
        _reset(orders={"PO-1": _order(po_order_stage.WAITING_FOR_DELIVERY, 40)})
        po_order_stage.advance_on_receipt(_Receipt("PR-1", ["PO-1"]))
        self.assertEqual(
            STATE["orders"]["PO-1"][po_order_stage.FIELD], po_order_stage.WAITING_FOR_DELIVERY
        )

    def test_it_advances_from_any_stage_including_created(self):
        _reset(orders={"PO-1": _order(po_order_stage.CREATED, 100)})
        po_order_stage.advance_on_receipt(_Receipt("PR-1", ["PO-1"]))
        self.assertEqual(STATE["orders"]["PO-1"][po_order_stage.FIELD], po_order_stage.RECEIVED)

    def test_an_already_received_order_is_not_rewritten(self):
        _reset(orders={"PO-1": _order(po_order_stage.RECEIVED, 100)})
        po_order_stage.advance_on_receipt(_Receipt("PR-1", ["PO-1"]))
        self.assertNotIn("_update_modified", STATE["orders"]["PO-1"])

    def test_it_handles_a_receipt_against_several_orders(self):
        _reset(
            orders={
                "PO-1": _order(po_order_stage.WAITING_FOR_DELIVERY, 100),
                "PO-2": _order(po_order_stage.WAITING_FOR_DELIVERY, 10),
            }
        )
        po_order_stage.advance_on_receipt(_Receipt("PR-1", ["PO-1", "PO-2", "PO-1"]))
        self.assertEqual(STATE["orders"]["PO-1"][po_order_stage.FIELD], po_order_stage.RECEIVED)
        self.assertEqual(
            STATE["orders"]["PO-2"][po_order_stage.FIELD], po_order_stage.WAITING_FOR_DELIVERY
        )

    def test_a_receipt_with_no_purchase_order_does_nothing(self):
        _reset(orders={})
        po_order_stage.advance_on_receipt(_Receipt("PR-1", [None]))
        self.assertEqual(STATE["orders"], {})

    def test_it_does_not_bump_modified(self):
        """Somebody may have the submitted order open; bumping `modified` from another
        document's hook hands them a TimestampMismatch for a field they never touched."""
        _reset(orders={"PO-1": _order(po_order_stage.WAITING_FOR_PICKUP, 100)})
        po_order_stage.advance_on_receipt(_Receipt("PR-1", ["PO-1"]))
        self.assertIs(STATE["orders"]["PO-1"]["_update_modified"], False)


class TestRevertOnCancel(unittest.TestCase):
    def test_it_takes_back_a_received_that_is_no_longer_true(self):
        _reset(orders={"PO-1": _order(po_order_stage.RECEIVED, 0)})
        po_order_stage.revert_on_receipt_cancel(_Receipt("PR-1", ["PO-1"]))
        self.assertEqual(
            STATE["orders"]["PO-1"][po_order_stage.FIELD], po_order_stage.AWAITING_CONFIRMATION
        )

    def test_it_does_not_guess_a_waiting_stage(self):
        """147 of 157 orders carry a shipping address, so inferring delivery-vs-pickup
        from one is a coin flip. Reverting to the earlier stage says strictly less."""
        _reset(orders={"PO-1": _order(po_order_stage.RECEIVED, 0)})
        po_order_stage.revert_on_receipt_cancel(_Receipt("PR-1", ["PO-1"]))
        self.assertNotIn(
            STATE["orders"]["PO-1"][po_order_stage.FIELD],
            (po_order_stage.WAITING_FOR_DELIVERY, po_order_stage.WAITING_FOR_PICKUP),
        )

    def test_it_leaves_a_comment_naming_the_receipt(self):
        _reset(orders={"PO-1": _order(po_order_stage.RECEIVED, 0)})
        po_order_stage.revert_on_receipt_cancel(_Receipt("PR-1", ["PO-1"]))
        self.assertEqual(len(STATE["comments"]), 1)
        self.assertIn("PR-1", STATE["comments"][0][2])

    def test_a_hand_set_stage_other_than_received_is_untouched(self):
        _reset(orders={"PO-1": _order(po_order_stage.WAITING_FOR_PICKUP, 0)})
        po_order_stage.revert_on_receipt_cancel(_Receipt("PR-1", ["PO-1"]))
        self.assertEqual(
            STATE["orders"]["PO-1"][po_order_stage.FIELD], po_order_stage.WAITING_FOR_PICKUP
        )
        self.assertEqual(STATE["comments"], [])

    def test_an_order_still_fully_received_keeps_received(self):
        """One of several receipts cancelled, and the rest still cover the order."""
        _reset(orders={"PO-1": _order(po_order_stage.RECEIVED, 100)})
        po_order_stage.revert_on_receipt_cancel(_Receipt("PR-1", ["PO-1"]))
        self.assertEqual(STATE["orders"]["PO-1"][po_order_stage.FIELD], po_order_stage.RECEIVED)


class TestTheHooksAreSafeToFire(unittest.TestCase):
    """Both hang off Purchase Receipt submission. Neither may raise, and neither may
    assume the custom field exists -- they fire during erpnext's own test bootstrap,
    before the patch that creates it has run."""

    def test_no_op_when_the_column_is_missing(self):
        _reset(has_column=False, orders={"PO-1": _order(po_order_stage.CREATED, 100)})
        po_order_stage.advance_on_receipt(_Receipt("PR-1", ["PO-1"]))
        po_order_stage.revert_on_receipt_cancel(_Receipt("PR-1", ["PO-1"]))
        self.assertEqual(STATE["orders"]["PO-1"][po_order_stage.FIELD], po_order_stage.CREATED)
        self.assertEqual(STATE["errors"], [])

    def test_a_dead_database_does_not_raise_out_of_the_guard(self):
        _reset(raise_on="has_column")
        po_order_stage.advance_on_receipt(_Receipt("PR-1", ["PO-1"]))
        po_order_stage.revert_on_receipt_cancel(_Receipt("PR-1", ["PO-1"]))

    def test_a_failure_mid_flight_is_logged_not_raised(self):
        _reset(orders={"PO-1": _order(po_order_stage.CREATED, 100)}, raise_on="get_value")
        po_order_stage.advance_on_receipt(_Receipt("PR-1", ["PO-1"]))
        self.assertEqual(STATE["errors"], ["Purchase Order stage advance"])

        _reset(orders={"PO-1": _order(po_order_stage.RECEIVED, 0)}, raise_on="get_value")
        po_order_stage.revert_on_receipt_cancel(_Receipt("PR-1", ["PO-1"]))
        self.assertEqual(STATE["errors"], ["Purchase Order stage revert"])


class TestTheStagesAreTheOnesAsked(unittest.TestCase):
    def test_five_stages_in_order(self):
        self.assertEqual(
            po_order_stage.STAGES,
            (
                "Created",
                "Awaiting Confirmation",
                "Waiting for Delivery",
                "Waiting for Pickup",
                "Received",
            ),
        )

    def test_the_default_is_the_first_stage(self):
        self.assertEqual(po_order_stage.DEFAULT_STAGE, po_order_stage.STAGES[0])

    def test_the_field_is_not_erpnexts_status(self):
        """The whole reason this module exists: `status` is recomputed on every save."""
        self.assertEqual(po_order_stage.FIELD, "custom_order_stage")
        self.assertNotEqual(po_order_stage.FIELD, "status")


class TestItIsWiredUp(unittest.TestCase):
    def test_both_patches_are_registered_in_order(self):
        text = PATCHES_TXT.read_text(encoding="utf-8")
        create = text.index("erpnext_enhancements.patches.add_po_order_stage_field")
        backfill = text.index("erpnext_enhancements.patches.backfill_po_order_stage")
        self.assertLess(create, backfill, "the field must exist before the backfill runs")

    def test_the_receipt_hooks_are_registered(self):
        hooks = HOOKS.read_text(encoding="utf-8")
        self.assertIn("erpnext_enhancements.po_order_stage.advance_on_receipt", hooks)
        self.assertIn("erpnext_enhancements.po_order_stage.revert_on_receipt_cancel", hooks)

    def test_nothing_hangs_off_purchase_order_submit(self):
        """Submitting is approval, not the act of placing the order. If the stage ever
        advances on submit, "Awaiting Confirmation" starts lying."""
        hooks = HOOKS.read_text(encoding="utf-8")
        start = hooks.index('"Purchase Order": {')
        # To the brace that closes the block, NOT to the next doctype key -- the comment
        # introducing the Purchase Receipt hooks names this module and sits between them.
        end = hooks.index("\n\t},", start)
        self.assertNotIn("po_order_stage", hooks[start:end])

    def test_the_field_patch_builds_its_options_from_stages(self):
        """Hard-coded options in the patch would drift from the module the hooks use,
        and a stored value outside the Select makes the row refuse to save."""
        source = FIELD_PATCH.read_text(encoding="utf-8")
        self.assertIn('"\\n".join(STAGES)', source)
        self.assertIn("from erpnext_enhancements.po_order_stage import", source)

    def test_the_field_allows_editing_after_submit(self):
        """Every transition the buyer cares about happens after submission."""
        self.assertIn('"allow_on_submit": 1', FIELD_PATCH.read_text(encoding="utf-8"))

    def test_the_backfill_never_keys_on_emptiness(self):
        """Purchase Order is a normal doctype: the ALTER that adds the column writes the
        default into every row, so an emptiness predicate matches nothing and logs itself
        a success. This is the v1.280.3 failure, pinned."""
        source = BACKFILL_PATCH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        execute = next(
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "execute"
        )
        body = ast.get_source_segment(source, execute) or ""
        self.assertNotIn("coalesce", body.lower())
        self.assertIn("backfill_stage_for", body)

    def test_the_backfill_is_loud_when_the_column_is_missing(self):
        source = BACKFILL_PATCH.read_text(encoding="utf-8")
        self.assertIn("frappe.log_error", source)
        self.assertIn("has_column", source)


if __name__ == "__main__":
    unittest.main()
