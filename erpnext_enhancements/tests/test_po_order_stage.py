"""Bench-free unit tests for the Purchase Order Order Stage field.

Stubs a minimal ``frappe`` (no site/bench) so the pure decision logic in
``erpnext_enhancements.po_order_stage`` runs under plain unittest. The stub is installed
in ``setUpModule`` (execution time), not at import, so it never fools the bench-only
suites' ``import frappe`` skip-guards.

What is actually worth pinning here, all of it invisible until production:

* **The backfill rule**, because the patch it drives cannot key on emptiness. The column's
  default is written into every existing row by the ALTER that adds it, so the predicate
  has to be the writer's own rule and a test is the only thing that keeps the two in step.
* **The Select options come from ``STAGES``**, via the one ``field_definition()`` that
  both patches call,, so the field, the backfill and the hooks
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
OPTIONS_PATCH = APP / "patches/update_po_order_stage_options.py"

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

    def test_a_part_received_order_says_so(self):
        """Before v1.328.0 there was no such option and this fell through to `Awaiting
        Confirmation`, which is false the moment the first box arrives. Nothing was
        re-backfilled when the option was added: production had zero part-received orders
        at the time, so the old rule and the new one disagree on no stored row."""
        self.assertEqual(
            self.rule(1, "To Receive and Bill", 99.9), po_order_stage.PARTIALLY_FULFILLED
        )
        self.assertEqual(
            self.rule(1, "To Receive and Bill", 0.1), po_order_stage.PARTIALLY_FULFILLED
        )

    def test_nothing_received_is_still_only_awaiting_confirmation(self):
        self.assertEqual(
            self.rule(1, "To Receive and Bill", 0), po_order_stage.AWAITING_CONFIRMATION
        )

    def test_closed_beats_part_received(self):
        """Closed means we have stopped waiting, whatever turned up."""
        self.assertEqual(self.rule(1, "Closed", 40), po_order_stage.RECEIVED)

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

    def test_a_partial_receipt_marks_the_order_partially_fulfilled(self):
        """Left alone until v1.328.0, for want of an option that could say "some of it is
        here". `Partially Fulfilled` says more than the stage it replaces, not less."""
        _reset(orders={"PO-1": _order(po_order_stage.WAITING_FOR_DELIVERY, 40)})
        po_order_stage.advance_on_receipt(_Receipt("PR-1", ["PO-1"]))
        self.assertEqual(
            STATE["orders"]["PO-1"][po_order_stage.FIELD], po_order_stage.PARTIALLY_FULFILLED
        )

    def test_a_receipt_covering_nothing_changes_nothing(self):
        """Zero received is not a fact about this order, and overwriting a hand-set stage
        with `Partially Fulfilled` on the strength of it would be a lie."""
        _reset(orders={"PO-1": _order(po_order_stage.WAITING_FOR_PICKUP, 0)})
        po_order_stage.advance_on_receipt(_Receipt("PR-1", ["PO-1"]))
        self.assertEqual(
            STATE["orders"]["PO-1"][po_order_stage.FIELD], po_order_stage.WAITING_FOR_PICKUP
        )
        self.assertEqual(STATE["comments"], [])

    def test_a_partial_receipt_names_the_stage_it_replaced(self):
        """Moving off `Waiting for Pickup` loses the fact that the rest is a trip rather
        than a truck. The comment turns an unrecoverable overwrite into a one-click
        restore -- the same bargain the cancel path already makes."""
        _reset(orders={"PO-1": _order(po_order_stage.WAITING_FOR_PICKUP, 40)})
        po_order_stage.advance_on_receipt(_Receipt("PR-1", ["PO-1"]))
        self.assertEqual(len(STATE["comments"]), 1)
        body = STATE["comments"][0][2]
        self.assertIn(po_order_stage.WAITING_FOR_PICKUP, body)
        self.assertIn("PR-1", body)

    def test_no_comment_when_the_replaced_stage_said_less(self):
        """`Created` and `Awaiting Confirmation` record nothing `Partially Fulfilled` does
        not. A note on every partial receipt would be noise on the orders needing it
        least."""
        for stage in (po_order_stage.CREATED, po_order_stage.AWAITING_CONFIRMATION):
            with self.subTest(stage):
                _reset(orders={"PO-1": _order(stage, 40)})
                po_order_stage.advance_on_receipt(_Receipt("PR-1", ["PO-1"]))
                self.assertEqual(
                    STATE["orders"]["PO-1"][po_order_stage.FIELD],
                    po_order_stage.PARTIALLY_FULFILLED,
                )
                self.assertEqual(STATE["comments"], [])

    def test_a_second_partial_receipt_does_not_rewrite_the_same_stage(self):
        _reset(orders={"PO-1": _order(po_order_stage.PARTIALLY_FULFILLED, 60)})
        po_order_stage.advance_on_receipt(_Receipt("PR-2", ["PO-1"]))
        self.assertNotIn("_update_modified", STATE["orders"]["PO-1"])
        self.assertEqual(STATE["comments"], [])

    def test_a_received_order_is_never_walked_backwards(self):
        """A smaller receipt against a complete order does not make it less complete, and
        `per_received` can read under 100 mid-flight on an amended document."""
        _reset(orders={"PO-1": _order(po_order_stage.RECEIVED, 40)})
        po_order_stage.advance_on_receipt(_Receipt("PR-2", ["PO-1"]))
        self.assertEqual(STATE["orders"]["PO-1"][po_order_stage.FIELD], po_order_stage.RECEIVED)

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
            STATE["orders"]["PO-2"][po_order_stage.FIELD], po_order_stage.PARTIALLY_FULFILLED
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
        """One of several receipts canceled, and the rest still cover the order."""
        _reset(orders={"PO-1": _order(po_order_stage.RECEIVED, 100)})
        po_order_stage.revert_on_receipt_cancel(_Receipt("PR-1", ["PO-1"]))
        self.assertEqual(STATE["orders"]["PO-1"][po_order_stage.FIELD], po_order_stage.RECEIVED)

    def test_an_order_left_part_received_lands_on_partially_fulfilled(self):
        """Where it lands is read back off `per_received`, not remembered: cancelling one
        of several receipts can leave goods on site, and `Awaiting Confirmation` would
        then be as false as the `Received` it replaced."""
        _reset(orders={"PO-1": _order(po_order_stage.RECEIVED, 45)})
        po_order_stage.revert_on_receipt_cancel(_Receipt("PR-1", ["PO-1"]))
        self.assertEqual(
            STATE["orders"]["PO-1"][po_order_stage.FIELD], po_order_stage.PARTIALLY_FULFILLED
        )
        self.assertIn("PR-1", STATE["comments"][0][2])

    def test_partially_fulfilled_falls_back_when_nothing_is_left(self):
        _reset(orders={"PO-1": _order(po_order_stage.PARTIALLY_FULFILLED, 0)})
        po_order_stage.revert_on_receipt_cancel(_Receipt("PR-1", ["PO-1"]))
        self.assertEqual(
            STATE["orders"]["PO-1"][po_order_stage.FIELD], po_order_stage.AWAITING_CONFIRMATION
        )

    def test_partially_fulfilled_that_is_still_true_is_left_alone(self):
        _reset(orders={"PO-1": _order(po_order_stage.PARTIALLY_FULFILLED, 45)})
        po_order_stage.revert_on_receipt_cancel(_Receipt("PR-1", ["PO-1"]))
        self.assertNotIn("_update_modified", STATE["orders"]["PO-1"])
        self.assertEqual(STATE["comments"], [])

    def test_only_the_stages_this_module_writes_are_eligible(self):
        """A hand-set stage is somebody's own account of the order, and a canceled receipt
        is no reason to overrule it."""
        for stage in (
            po_order_stage.CREATED,
            po_order_stage.AWAITING_CONFIRMATION,
            po_order_stage.AWAITING_FULFILLMENT,
            po_order_stage.WAITING_FOR_DELIVERY,
            po_order_stage.WAITING_FOR_PICKUP,
        ):
            with self.subTest(stage):
                _reset(orders={"PO-1": _order(stage, 0)})
                po_order_stage.revert_on_receipt_cancel(_Receipt("PR-1", ["PO-1"]))
                self.assertEqual(STATE["orders"]["PO-1"][po_order_stage.FIELD], stage)


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
    def test_seven_stages_in_order(self):
        """The six ER-2026-256846 asked for, plus `Created` for the draft an order starts
        as. `Received` is deliberately NOT relabelled "Fully Received" as the request
        literally worded it: renaming an option makes every row holding the old value
        refuse to save, where adding one costs nothing. Adding and renaming look equally
        cheap from outside and are not."""
        self.assertEqual(
            po_order_stage.STAGES,
            (
                "Created",
                "Awaiting Confirmation",
                "Awaiting Fulfillment",
                "Waiting for Delivery",
                "Waiting for Pickup",
                "Partially Fulfilled",
                "Received",
            ),
        )

    def test_every_informative_stage_is_a_real_stage(self):
        for stage in po_order_stage.INFORMATIVE_STAGES:
            self.assertIn(stage, po_order_stage.STAGES)

    def test_the_stages_that_say_nothing_extra_are_not_informative(self):
        """Getting this set wrong is a comment on every partial receipt, or none."""
        self.assertNotIn(po_order_stage.CREATED, po_order_stage.INFORMATIVE_STAGES)
        self.assertNotIn(po_order_stage.AWAITING_CONFIRMATION, po_order_stage.INFORMATIVE_STAGES)
        self.assertNotIn(po_order_stage.PARTIALLY_FULFILLED, po_order_stage.INFORMATIVE_STAGES)

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

    def test_the_field_options_are_the_stages(self):
        """Hard-coded options anywhere would drift from the module the hooks use, and a
        stored value outside the Select makes the row refuse to save."""
        self.assertEqual(
            po_order_stage.field_definition()["options"],
            "\n".join(po_order_stage.STAGES),
        )

    def test_both_patches_build_the_field_from_the_one_spec(self):
        """Two patches touch this field -- one creates it, one widens it. A spec written
        out twice is two option lists waiting to disagree."""
        for patch in (FIELD_PATCH, OPTIONS_PATCH):
            with self.subTest(patch.name):
                source = patch.read_text(encoding="utf-8")
                self.assertIn(
                    "from erpnext_enhancements.po_order_stage import field_definition", source
                )
                self.assertIn("field_definition()", source)

    def test_the_field_allows_editing_after_submit(self):
        """Every transition the buyer cares about happens after submission."""
        self.assertEqual(po_order_stage.field_definition()["allow_on_submit"], 1)

    def test_the_field_is_visible_and_filterable_in_the_list(self):
        """The actual feature for a list of a hundred-odd orders: "what am I waiting on"
        is a filter, not a scroll."""
        spec = po_order_stage.field_definition()
        self.assertEqual(spec["in_list_view"], 1)
        self.assertEqual(spec["in_standard_filter"], 1)

    def test_the_options_patch_runs_after_the_field_exists(self):
        text = PATCHES_TXT.read_text(encoding="utf-8")
        create = text.index("erpnext_enhancements.patches.add_po_order_stage_field")
        widen = text.index("erpnext_enhancements.patches.update_po_order_stage_options")
        self.assertLess(create, widen, "the field must exist before its options are widened")

    def test_the_options_patch_carries_no_data_migration(self):
        """Purely additive: every stored value is still a valid option. A backfill here
        would be a predicate matching nothing, committing, and logging itself a success --
        the v1.280.3 shape of failure."""
        source = OPTIONS_PATCH.read_text(encoding="utf-8")
        self.assertNotIn("set_value", source)
        self.assertNotIn("frappe.db.sql", source)

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
