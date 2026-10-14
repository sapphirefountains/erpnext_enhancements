"""Bench-free unit tests for the Kanban ``update_order`` override (v1.639.1).

Stubs a minimal ``frappe`` -- including ``frappe.desk.doctype.kanban_board.kanban_board`` -- so
``erpnext_enhancements.kanban_order`` runs under plain unittest. The stub is installed in
``setUpModule`` (execution time), not at import, so it never fools the bench-only suites'
``import frappe`` skip-guards. Own CI step: the stub is process-wide.

What is worth pinning, all of it invisible until production:

* **Opening a board never writes a document.** frappe v16.50.0's ``update_order`` ran
  ``set_value`` on every card listed under a column that was not its stored value, and its
  client sends each column's *saved* order on every board open. CRM-OPP-2026-00178 sat in
  both "Needs Analysis" (stale, from before a form edit) and "Closed Won", so each open saved it
  into Needs Analysis and back, and each return to Closed Won emailed four group inboxes. The
  stub's ``set_value`` raises, so any write fails the suite.
* **The stale entry is pruned, not re-saved.** frappe kept every name it was sent, which is why
  the duplicate survived every open. Pruning is what makes the first open after the deploy heal
  the board.
* **Order is kept** for every card that stays, and a duplicate within a column keeps its first
  place: this endpoint's other job is remembering where people put their cards.
* **frappe's checks and side effects are mirrored**: read on the board, write on the doctype
  (and a user without write gets the board back unsaved), ``save(ignore_permissions=True)``,
  the realtime publish, and the ``(board, updated_cards)`` shape the client indexes into.

Run: python -m unittest erpnext_enhancements.tests.test_kanban_order
"""

import importlib
import inspect
import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
HOOKS = APP / "hooks.py"

# Mutable state the frappe stub reads at call time.
STATE = {}
kanban_order = None


class _Column:
    def __init__(self, column_name, order):
        self.column_name = column_name
        self.order = json.dumps(order)


class _Board:
    def __init__(self, columns, reference_doctype="Opportunity", field_name="status"):
        self.name = "Opportunity"
        self.reference_doctype = reference_doctype
        self.field_name = field_name
        self.columns = [_Column(name, order) for name, order in columns]

    def check_permission(self, ptype):
        STATE["board_checks"].append(ptype)
        if STATE["deny_board_read"]:
            raise PermissionError("no read on the board")

    def save(self, ignore_permissions=False):
        STATE["saves"].append(ignore_permissions)
        return self

    def order_of(self, column_name):
        column = next(c for c in self.columns if c.column_name == column_name)
        return json.loads(column.order)


def _install_frappe_stub():
    frappe = types.ModuleType("frappe")
    frappe.whitelist = lambda *a, **kw: (lambda fn: fn)

    def get_doc(doctype, name):
        assert doctype == "Kanban Board", doctype
        return STATE["board"]

    def has_permission(doctype, ptype, *args, **kwargs):
        STATE["doctype_checks"].append((doctype, ptype))
        return STATE["can_write"]

    def parse_json(value):
        return json.loads(value) if isinstance(value, str) else value

    def get_all(doctype, filters=None, fields=None, limit_page_length=None, **kwargs):
        STATE["queries"].append(
            {"doctype": doctype, "filters": filters, "fields": fields, "limit": limit_page_length}
        )
        operator, names = filters["name"]
        assert operator == "in", operator
        fieldname = fields[1]
        return [
            {"name": name, fieldname: STATE["stored"][name]}
            for name in names
            if name in STATE["stored"]
        ]

    def set_value(*args, **kwargs):
        STATE["writes"].append(args)
        raise AssertionError(f"update_order wrote a document: {args}")

    frappe.get_doc = get_doc
    frappe.has_permission = has_permission
    frappe.parse_json = parse_json
    frappe.get_all = get_all
    frappe.set_value = set_value

    desk = types.ModuleType("frappe.desk")
    doctype_pkg = types.ModuleType("frappe.desk.doctype")
    board_pkg = types.ModuleType("frappe.desk.doctype.kanban_board")
    board_module = types.ModuleType("frappe.desk.doctype.kanban_board.kanban_board")
    board_module.publish_kanban_board_update = lambda board: STATE["published"].append(board)
    board_pkg.kanban_board = board_module
    doctype_pkg.kanban_board = board_pkg
    desk.doctype = doctype_pkg
    frappe.desk = desk

    sys.modules["frappe"] = frappe
    sys.modules["frappe.desk"] = desk
    sys.modules["frappe.desk.doctype"] = doctype_pkg
    sys.modules["frappe.desk.doctype.kanban_board"] = board_pkg
    sys.modules["frappe.desk.doctype.kanban_board.kanban_board"] = board_module


def setUpModule():
    global kanban_order
    _install_frappe_stub()
    sys.modules.pop("erpnext_enhancements.kanban_order", None)
    # importlib, not `from erpnext_enhancements import kanban_order`: the `from` form can hand
    # back a module bound to an earlier frappe stub (see test_po_pdf_filename).
    kanban_order = importlib.import_module("erpnext_enhancements.kanban_order")


# The production board on 2026-10-13, cut down: CRM-OPP-2026-00178 was Closed Won and still
# listed under Needs Analysis, where it had been before Brian moved it on the form.
PROD_COLUMNS = [
    ("Qualification", ["CRM-OPP-2026-00184"]),
    ("Needs Analysis", ["CRM-OPP-2026-00082", "CRM-OPP-2026-00178", "CRM-OPP-2026-00175"]),
    ("Negotiation/Review", ["CRM-OPP-2026-00180"]),
    ("Closed Won", ["CRM-OPP-2026-00178", "CRM-OPP-2026-00179", "CRM-OPP-2026-00177"]),
]
PROD_STORED = {
    "CRM-OPP-2026-00184": "Qualification",
    "CRM-OPP-2026-00082": "Needs Analysis",
    "CRM-OPP-2026-00175": "Needs Analysis",
    "CRM-OPP-2026-00180": "Negotiation/Review",
    "CRM-OPP-2026-00178": "Closed Won",
    "CRM-OPP-2026-00179": "Closed Won",
    "CRM-OPP-2026-00177": "Closed Won",
}


def _reset(columns=PROD_COLUMNS, stored=PROD_STORED, can_write=True, deny_board_read=False):
    STATE.clear()
    STATE.update(
        board=_Board(columns),
        stored=dict(stored),
        can_write=can_write,
        deny_board_read=deny_board_read,
        board_checks=[],
        doctype_checks=[],
        queries=[],
        saves=[],
        writes=[],
        published=[],
    )


def _client_order(columns=PROD_COLUMNS):
    """What 16.50's board init() sends: each active column's saved order, as a JSON string."""
    return json.dumps({name: list(order) for name, order in columns})


class TestOpeningABoardWritesNothing(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_a_card_listed_in_two_columns_is_not_saved_into_either(self):
        """The production failure: two writes per board open, the second one a fresh
        transition into Closed Won and so a fresh hand-off email."""
        kanban_order.update_order("Opportunity", _client_order())
        self.assertEqual(STATE["writes"], [])

    def test_the_stale_entry_is_pruned_so_the_next_open_is_clean(self):
        board, _ = kanban_order.update_order("Opportunity", _client_order())
        self.assertNotIn("CRM-OPP-2026-00178", board.order_of("Needs Analysis"))
        self.assertIn("CRM-OPP-2026-00178", board.order_of("Closed Won"))

    def test_the_card_stays_in_its_own_column_in_the_place_it_was(self):
        board, _ = kanban_order.update_order("Opportunity", _client_order())
        self.assertEqual(
            board.order_of("Closed Won"),
            ["CRM-OPP-2026-00178", "CRM-OPP-2026-00179", "CRM-OPP-2026-00177"],
        )
        self.assertEqual(board.order_of("Needs Analysis"), ["CRM-OPP-2026-00082", "CRM-OPP-2026-00175"])

    def test_a_card_moved_backwards_on_the_form_is_not_put_back(self):
        """frappe's loop lets the column listed last win. A deal moved from Negotiation/Review
        back to Needs Analysis on the form, still in Negotiation/Review's saved order, would
        have been returned to Negotiation/Review by the next board open."""
        columns = [
            ("Needs Analysis", ["CRM-OPP-2026-00180"]),
            ("Negotiation/Review", ["CRM-OPP-2026-00180"]),
        ]
        _reset(columns=columns, stored={"CRM-OPP-2026-00180": "Needs Analysis"})
        board, updated = kanban_order.update_order("Opportunity", _client_order(columns))
        self.assertEqual(STATE["writes"], [])
        self.assertEqual(updated, [])
        self.assertEqual(board.order_of("Negotiation/Review"), [])
        self.assertEqual(board.order_of("Needs Analysis"), ["CRM-OPP-2026-00180"])

    def test_nothing_is_reported_as_moved(self):
        """The client moves every card in updated_cards to the column named there; an empty
        list leaves every card where its stored value puts it."""
        _, updated = kanban_order.update_order("Opportunity", _client_order())
        self.assertEqual(updated, [])


class TestTheSavedOrderIsCleaned(unittest.TestCase):
    def test_a_duplicate_within_a_column_keeps_its_first_place(self):
        columns = [("Closed Won", ["A", "B", "A", "C"])]
        _reset(columns=columns, stored={"A": "Closed Won", "B": "Closed Won", "C": "Closed Won"})
        board, _ = kanban_order.update_order("Opportunity", _client_order(columns))
        self.assertEqual(board.order_of("Closed Won"), ["A", "B", "C"])

    def test_a_deleted_document_is_dropped(self):
        """frappe 16.50 drops these too; the classic board's saved order can list them."""
        columns = [("Lost", ["A", "GONE", "B"])]
        _reset(columns=columns, stored={"A": "Lost", "B": "Lost"})
        board, _ = kanban_order.update_order("Opportunity", _client_order(columns))
        self.assertEqual(board.order_of("Lost"), ["A", "B"])

    def test_a_column_the_client_did_not_send_is_left_as_it_was(self):
        """16.50's client sends active columns only; an archived column's order is not ours
        to rewrite."""
        columns = [*PROD_COLUMNS, ("Archived", ["CRM-OPP-2026-00178"])]
        _reset(columns=columns)
        kanban_order.update_order("Opportunity", _client_order(PROD_COLUMNS))
        self.assertEqual(STATE["board"].order_of("Archived"), ["CRM-OPP-2026-00178"])

    def test_an_order_sent_as_a_dict_works_too(self):
        """frappe's signature takes str | dict; an in-process caller may pass the dict."""
        _reset()
        board, _ = kanban_order.update_order(
            "Opportunity", {name: list(order) for name, order in PROD_COLUMNS}
        )
        self.assertNotIn("CRM-OPP-2026-00178", board.order_of("Needs Analysis"))

    def test_the_stored_values_are_read_in_one_query(self):
        """frappe read each card with its own get_value; the Opportunity board sends 800+
        names (Lost and Closed Won included) on every open."""
        _reset()
        kanban_order.update_order("Opportunity", _client_order())
        self.assertEqual(len(STATE["queries"]), 1)
        query = STATE["queries"][0]
        self.assertEqual(query["doctype"], "Opportunity")
        self.assertEqual(query["fields"], ["name", "status"])
        self.assertEqual(query["limit"], 0)
        self.assertEqual(set(query["filters"]["name"][1]), set(PROD_STORED))

    def test_an_empty_board_does_not_query(self):
        columns = [("Qualification", [])]
        _reset(columns=columns, stored={})
        board, _ = kanban_order.update_order("Opportunity", _client_order(columns))
        self.assertEqual(STATE["queries"], [])
        self.assertEqual(board.order_of("Qualification"), [])


class TestFrappesChecksAndSideEffectsAreKept(unittest.TestCase):
    def test_it_checks_read_on_the_board_and_write_on_the_doctype(self):
        _reset()
        kanban_order.update_order("Opportunity", _client_order())
        self.assertEqual(STATE["board_checks"], ["read"])
        self.assertEqual(STATE["doctype_checks"], [("Opportunity", "write")])

    def test_no_board_read_raises(self):
        _reset(deny_board_read=True)
        with self.assertRaises(PermissionError):
            kanban_order.update_order("Opportunity", _client_order())
        self.assertEqual(STATE["saves"], [])

    def test_without_write_on_the_doctype_the_board_comes_back_unsaved(self):
        _reset(can_write=False)
        board, updated = kanban_order.update_order("Opportunity", _client_order())
        self.assertIs(board, STATE["board"])
        self.assertEqual(updated, [])
        self.assertEqual(STATE["saves"], [])
        self.assertEqual(STATE["queries"], [])
        self.assertEqual(STATE["published"], [])
        self.assertIn("CRM-OPP-2026-00178", board.order_of("Needs Analysis"))

    def test_the_board_is_saved_ignoring_permissions_and_published(self):
        """As frappe does: board write is if_owner, and teammates who are not the owner still
        reorder a shared board. Other open tabs refresh from the publish."""
        _reset()
        board, _ = kanban_order.update_order("Opportunity", _client_order())
        self.assertEqual(STATE["saves"], [True])
        self.assertEqual(STATE["published"], [board])

    def test_a_frappe_without_the_publish_helper_still_saves(self):
        _reset()
        module = sys.modules["frappe.desk.doctype.kanban_board.kanban_board"]
        publish = module.publish_kanban_board_update
        del module.publish_kanban_board_update
        try:
            kanban_order.update_order("Opportunity", _client_order())
        finally:
            module.publish_kanban_board_update = publish
        self.assertEqual(STATE["saves"], [True])

    def test_the_signature_mirrors_frappes(self):
        """frappe.call matches the request's form_dict against this signature: a parameter
        missing here would be dropped from the request."""
        self.assertEqual(list(inspect.signature(kanban_order.update_order).parameters), ["board_name", "order"])


class TestItIsWiredUp(unittest.TestCase):
    def test_the_override_is_registered_against_frappes_method(self):
        """The board calls method_prefix + "update_order", with method_prefix
        "frappe.desk.doctype.kanban_board.kanban_board.". Without this line the module is
        dead code and every board open keeps writing."""
        hooks = HOOKS.read_text(encoding="utf-8")
        self.assertIn(
            '"frappe.desk.doctype.kanban_board.kanban_board.update_order": '
            '"erpnext_enhancements.kanban_order.update_order"',
            hooks,
        )

    def test_the_single_card_drag_is_left_to_frappe(self):
        """A drag is the one place a board SHOULD change a document, and it goes through
        update_order_for_single_card. Overriding that is scope this fix does not need."""
        self.assertNotIn("kanban_board.update_order_for_single_card", HOOKS.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
