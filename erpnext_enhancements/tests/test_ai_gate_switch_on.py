"""Bench-free tests for ``patches/enable_ai_write_gate`` (v1.525.0): the patch that switches the
AI write gate on, with the exemptions Nik chose on 2026-09-23.

What must hold, because a mistake here is either a deploy that aborts or a gate that is quietly
not what was decided:

- it never raises, since a raising patch aborts ``bench migrate``, which here is the deploy;
- it never saves the Single, whose validate() checks unrelated settings;
- it seeds only missing rows, never a NEVER_EXEMPT doctype and never one the site lacks;
- the flag goes on only after the rows are in, and stays off if seeding fails;
- the permanent list is exactly the one chosen. Changing it should be a deliberate edit here too.

Plain ``unittest`` under ``test_assistant_tools_schema.install_stubs``. Every frappe attribute is
patched per test and restored.

Run: python -m unittest erpnext_enhancements.tests.test_ai_gate_switch_on -v
"""

import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

patch_module = None
_gate = None

CHOSEN = (
    "Comment",
    "ToDo",
    "Sapphire Maintenance Template",
    "Sapphire Maintenance Section",
    "Serial No",
    "Training Lesson",
)


def setUpModule():
    global patch_module, _gate
    from erpnext_enhancements.tests.test_assistant_tools_schema import install_stubs

    install_stubs()
    import frappe

    if not hasattr(frappe, "whitelist"):
        frappe.whitelist = lambda *a, **k: (lambda f: f)

    from erpnext_enhancements.assistant_tools import _gate as gate_module
    from erpnext_enhancements.patches import enable_ai_write_gate

    _gate = gate_module
    patch_module = enable_ai_write_gate


class FakeSettings:
    def __init__(self, present=(), fail_on=None):
        self.rows = [types.SimpleNamespace(document_type=d) for d in present]
        self.inserted = []
        self.fail_on = fail_on

    def get(self, key):
        assert key == "ai_exempt_doctypes", key
        return self.rows

    def append(self, key, values):
        assert key == "ai_exempt_doctypes", key
        test = self

        class Row(types.SimpleNamespace):
            def db_insert(self):
                if self.document_type == test.fail_on:
                    raise RuntimeError("lock wait timeout")
                test.inserted.append(self.document_type)

        row = Row(**values)
        self.rows.append(row)
        return row

    def save(self, *a, **k):
        raise AssertionError("the patch must never save the Single")


class TestEnableAiWriteGate(unittest.TestCase):
    def _run(self, settings, missing_doctypes=(), flag_raises=False):
        import frappe

        events = []

        def set_single_value(doctype, field, value):
            if flag_raises:
                raise RuntimeError("deadlock")
            events.append(("flag", doctype, field, value, list(settings.inserted)))

        db = types.SimpleNamespace(
            exists=lambda doctype, name: not (doctype == "DocType" and name in missing_doctypes),
            set_single_value=set_single_value,
            rollback=lambda: events.append(("rollback",)),
        )
        patches = [
            mock.patch.object(frappe, "db", db, create=True),
            mock.patch.object(frappe, "get_single", lambda doctype: settings, create=True),
            mock.patch.object(frappe, "clear_document_cache", lambda *a: events.append(("cache",)), create=True),
            mock.patch.object(frappe, "log_error", lambda *a, **k: events.append(("error", k.get("title"))), create=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        patch_module.execute()
        return events

    def test_the_chosen_list_is_seeded_and_then_the_gate_goes_on(self):
        settings = FakeSettings()
        events = self._run(settings)
        self.assertEqual(settings.inserted, list(CHOSEN))
        flag = [e for e in events if e[0] == "flag"]
        self.assertEqual(flag, [("flag", "ERPNext Enhancements Settings", "ai_write_gating_enabled", 1, list(CHOSEN))])

    def test_rows_already_there_are_left_alone(self):
        settings = FakeSettings(present=("Comment", "Item"))
        self._run(settings)
        self.assertNotIn("Comment", settings.inserted)
        self.assertEqual(len([r for r in settings.rows if r.document_type == "Comment"]), 1)
        self.assertIn("Item", [r.document_type for r in settings.rows])  # someone's own row survives

    def test_a_doctype_the_site_lacks_is_skipped(self):
        settings = FakeSettings()
        self._run(settings, missing_doctypes=("Sapphire Maintenance Section",))
        self.assertNotIn("Sapphire Maintenance Section", settings.inserted)
        self.assertIn("Comment", settings.inserted)

    def test_a_failed_seed_leaves_the_gate_off_and_does_not_raise(self):
        settings = FakeSettings(fail_on="Serial No")
        events = self._run(settings)
        self.assertEqual([e for e in events if e[0] == "flag"], [])
        self.assertIn(("rollback",), events)
        self.assertTrue(any(e[0] == "error" and "gate left off" in e[1] for e in events))

    def test_a_failed_flag_is_logged_not_raised(self):
        events = self._run(FakeSettings(), flag_raises=True)
        self.assertTrue(any(e[0] == "error" and "switch the gate on" in e[1] for e in events))

    def test_running_twice_adds_nothing_the_second_time(self):
        settings = FakeSettings()
        self._run(settings)
        first = list(settings.inserted)
        settings.inserted.clear()
        self._run(settings)
        self.assertEqual(settings.inserted, [])
        self.assertEqual(first, list(CHOSEN))


class TestTheChosenList(unittest.TestCase):
    def test_it_is_exactly_what_was_decided(self):
        self.assertEqual(tuple(patch_module.PERMANENT), CHOSEN)

    def test_none_of_it_is_never_exempt(self):
        self.assertFalse(set(CHOSEN) & _gate.NEVER_EXEMPT)

    def test_money_stock_contracts_and_items_are_not_in_it(self):
        for doctype in (
            "Item", "Item Price", "Price List", "Stock Reconciliation", "Stock Entry",
            "Sapphire Maintenance Contract", "Sapphire Maintenance Profile", "Project Contract",
            "Bank Account", "Bank",
            "User", "Role", "Company", "Training Course", "Enhancement Request",
        ):
            self.assertNotIn(doctype, CHOSEN)


class TestWiring(unittest.TestCase):
    def test_registered_after_post_model_sync(self):
        text = (REPO_ROOT / "erpnext_enhancements/patches.txt").read_text(encoding="utf-8")
        lines = [line.strip() for line in text.splitlines()]
        entry = "erpnext_enhancements.patches.enable_ai_write_gate"
        self.assertIn(entry, lines)
        self.assertGreater(lines.index(entry), lines.index("[post_model_sync]"))

    def test_the_exemption_table_has_the_window_column(self):
        path = REPO_ROOT / (
            "erpnext_enhancements/ai_governance/doctype/ai_confirmation_exempt_doctype/"
            "ai_confirmation_exempt_doctype.json"
        )
        meta = json.loads(path.read_text(encoding="utf-8"))
        field = next(f for f in meta["fields"] if f["fieldname"] == "exempt_until")
        self.assertEqual(field["fieldtype"], "Datetime")
        self.assertFalse(field.get("reqd"))
        self.assertIsNone(field.get("default"))  # empty means permanent; a default would change that

    def test_the_bench_suite_gates_a_doctype_that_is_not_exempt(self):
        # test_ai_gating_integration writes through the gate and expects a card. It used ToDo until
        # this release exempted ToDo, which would have made every "gated" case execute at once.
        import ast

        source = (REPO_ROOT / "erpnext_enhancements/tests/test_ai_gating_integration.py").read_text(encoding="utf-8")
        gated = None
        for node in ast.parse(source).body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "GATED_DOCTYPE" for t in node.targets):
                gated = ast.literal_eval(node.value)
        self.assertIsNotNone(gated, "test_ai_gating_integration must name its GATED_DOCTYPE")
        self.assertNotIn(gated, CHOSEN)
        self.assertNotIn('"ToDo"', source)

    def test_the_gates_own_records_are_never_exempt(self):
        for doctype in ("Task", "ERPNext Enhancements Settings", "AI Confirmation Exempt Doctype",
                        "AI Pending Action", "AI Action Log"):
            self.assertIn(doctype, _gate.NEVER_EXEMPT)


if __name__ == "__main__":
    unittest.main()
