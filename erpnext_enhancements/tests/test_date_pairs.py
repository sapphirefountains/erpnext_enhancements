"""Bench-free tests for ``utils/date_pairs`` (form kit P2-A): the out-of-order date warning.

What must hold:

- it WARNS and never blocks: nothing here may call ``frappe.throw`` and nothing may propagate;
- an in-order (or equal) pair is silent; a reversed one speaks once per person per record, and a
  *different* problem on the same record speaks again;
- a sequence with gaps compares only the values that are set, in order;
- blank values are silent (``get_datetime(None)`` is *now*, so blanks must never reach it);
- imports, installs, migrations, patches, background jobs, guests and canceled documents are skipped;
- every doctype in the registry has a ``validate`` hook in hooks.py pointing at the function, every
  field in the registry exists on that doctype with the label the registry gives it, and nothing
  that ERPNext or a controller already validates (and throws on) is registered a second time.

Each test installs a private ``frappe`` stub and a fake ``popup_once`` and puts the real modules back.

Run: python -m unittest erpnext_enhancements.tests.test_date_pairs -v
"""

import ast
import datetime
import importlib
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MODULE_FILE = APP_ROOT / "utils" / "date_pairs.py"
HOOKS = APP_ROOT / "hooks.py"
HOOK = "erpnext_enhancements.utils.date_pairs.warn_out_of_order"

# Pairs another validator already refuses; registering them here would warn on top of an error.
VALIDATED_ELSEWHERE = {
    "Project": {"expected_start_date", "expected_end_date", "actual_start_date", "actual_end_date"},
    "Task": {"exp_start_date", "exp_end_date", "act_start_date", "act_end_date"},
}
VALIDATED_DOCTYPES = (
    "Rental Booking",
    "Travel Trip",
    "Time Off Request",
    "Work Restriction",
    "Asset Booking",
    "Asset Out of Service",
    "Time Correction Request",
    "Employee Credential",
)


class Doc:
    """A document with attributes, as validate sees one."""

    def __init__(self, doctype, name="OPP-1", docstatus=0, **fields):
        self.doctype = doctype
        self.name = name
        self.docstatus = docstatus
        self.__dict__.update(fields)


def _get_datetime(value):
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.date):
        return datetime.datetime.combine(value, datetime.time())
    if value is None:
        return datetime.datetime(2099, 1, 1)  # frappe.utils.get_datetime(None) is NOW
    return datetime.datetime.fromisoformat(value)


class Harness:
    """Installs the stubs, loads the module under test, restores everything."""

    def __init__(self):
        frappe = types.ModuleType("frappe")
        frappe._ = lambda text: text
        frappe.shown = []
        frappe.logged = []
        frappe.flags = types.SimpleNamespace(in_import=False, in_install=False, in_migrate=False, in_patch=False)
        frappe.local = types.SimpleNamespace(request=object())
        frappe.session = types.SimpleNamespace(user="lisa@example.com")
        frappe.utils = types.SimpleNamespace(
            get_datetime=_get_datetime, escape_html=lambda text: str(text).replace("<", "&lt;")
        )

        def msgprint(message, **kwargs):
            frappe.shown.append({"message": message, **kwargs})

        def throw(*args, **kwargs):
            raise AssertionError("a date-pair warning must never throw")

        frappe.msgprint = msgprint
        frappe.throw = throw
        frappe.log_error = lambda **kwargs: frappe.logged.append(kwargs)
        frappe.get_traceback = lambda: "traceback"
        self.frappe = frappe

        popup = types.ModuleType("erpnext_enhancements.utils.popup_once")
        self.seen = set()
        self.first_time_calls = []

        def first_time(*parts, ttl=None):
            self.first_time_calls.append(parts)
            if parts in self.seen:
                return False
            self.seen.add(parts)
            return True

        popup.first_time = first_time
        self.popup = popup

        self._saved = {name: sys.modules.get(name) for name in ("frappe", "erpnext_enhancements.utils.popup_once")}
        sys.modules["frappe"] = frappe
        sys.modules["erpnext_enhancements.utils.popup_once"] = popup
        spec = importlib.util.spec_from_file_location("_date_pairs_under_test", MODULE_FILE)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def restore(self):
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def _normalize_available():
    try:
        importlib.import_module("erpnext_enhancements.utils.normalize")
    except Exception:
        return False
    return True


class TestWarnOutOfOrder(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.restore)
        self.warn = self.h.module.warn_out_of_order

    def requested(self, start, end, **extra):
        return Doc("Opportunity", custom_project_start_date=start, custom_project_end_date=end, **extra)

    def test_an_in_order_pair_is_silent(self):
        self.warn(self.requested("2026-10-14", "2026-10-20"))
        self.assertEqual(self.h.frappe.shown, [])

    def test_equal_values_are_in_order(self):
        self.warn(self.requested("2026-10-14", "2026-10-14"))
        self.assertEqual(self.h.frappe.shown, [])

    def test_a_reversed_pair_warns_once_and_then_not_again(self):
        doc = self.requested("2026-10-20", "2026-10-14")
        self.warn(doc)
        self.assertEqual(len(self.h.frappe.shown), 1)
        shown = self.h.frappe.shown[0]
        self.assertEqual(shown["indicator"], "orange")
        self.assertEqual(shown["title"], "Check the dates")
        self.assertIn("Requested Start Date", shown["message"])
        self.assertIn("Requested End Date", shown["message"])
        # Autosaving pages re-validate constantly: the same save again says nothing.
        self.warn(doc)
        self.warn(doc)
        self.assertEqual(len(self.h.frappe.shown), 1)

    def test_the_memory_is_keyed_on_record_and_problem(self):
        self.warn(self.requested("2026-10-20", "2026-10-14"))
        self.warn(self.requested("2026-10-20", "2026-10-14", name="OPP-2"))  # another record
        self.assertEqual(len(self.h.frappe.shown), 2)
        self.warn(self.requested("2026-10-25", "2026-10-14"))  # a different problem, same record
        self.assertEqual(len(self.h.frappe.shown), 3)
        keys = self.h.first_time_calls[0]
        self.assertEqual(keys[:3], ("date-pair", "Opportunity", "OPP-1"))

    @unittest.skipUnless(_normalize_available(), "utils/normalize.py is not in this tree yet")
    def test_the_message_names_the_values_in_the_print_style(self):
        self.warn(
            Doc(
                "Opportunity",
                custom_delivery_date_time=datetime.datetime(2026, 10, 14, 14, 0),
                custom_setup_date_time=datetime.datetime(2026, 10, 13, 9, 30),
            )
        )
        message = self.h.frappe.shown[0]["message"]
        self.assertIn("10/14/2026 2:00 PM", message)
        self.assertIn("10/13/2026 9:30 AM", message)
        self.assertIn("Delivery Date Time", message)
        self.assertIn("Setup Date Time", message)

    @unittest.skipUnless(_normalize_available(), "utils/normalize.py is not in this tree yet")
    def test_date_fields_show_a_date_without_a_time(self):
        self.warn(self.requested("2026-10-20", "2026-10-14"))
        message = self.h.frappe.shown[0]["message"]
        self.assertIn("10/20/2026", message)
        self.assertIn("10/14/2026", message)
        self.assertNotIn("12:00 AM", message)

    def test_the_four_field_sequence_compares_only_the_values_that_are_set(self):
        def at(day, hour=9):
            return datetime.datetime(2026, 10, day, hour)

        # Gaps, in order: silent.
        self.warn(Doc("Opportunity", custom_delivery_date_time=at(10), custom_event_date_time=at(12)))
        self.warn(Doc("Opportunity", name="O2", custom_setup_date_time=at(10), custom_take_down_date_time=at(13)))
        self.assertEqual(self.h.frappe.shown, [])
        # The event before the delivery, with the setup between them blank: one problem, naming both.
        self.warn(Doc("Opportunity", name="O3", custom_delivery_date_time=at(12), custom_event_date_time=at(10)))
        self.assertEqual(len(self.h.frappe.shown), 1)
        message = self.h.frappe.shown[0]["message"]
        self.assertIn("Delivery Date Time", message)
        self.assertIn("Event Date Time", message)
        self.assertNotIn("Setup Date Time", message)

    def test_every_backwards_step_is_reported_in_one_pop_up(self):
        def at(day):
            return datetime.datetime(2026, 10, day, 9)

        self.warn(
            Doc(
                "Opportunity",
                custom_delivery_date_time=at(15),
                custom_setup_date_time=at(14),
                custom_event_date_time=at(13),
                custom_take_down_date_time=at(16),
            )
        )
        self.assertEqual(len(self.h.frappe.shown), 1)
        self.assertEqual(self.h.frappe.shown[0]["message"].count("<p>") - 1, 2)  # two steps + the footer

    def test_blank_values_are_silent_and_never_reach_get_datetime(self):
        for blank in (None, ""):
            self.warn(self.requested(blank, blank))
            self.warn(self.requested("2026-10-14", blank))
            self.warn(self.requested(blank, "2026-10-14"))
        self.warn(Doc("Opportunity"))  # fields absent altogether: doc_events fire before custom fields exist
        self.assertEqual(self.h.frappe.shown, [])
        self.assertEqual(self.h.first_time_calls, [])

    def test_an_unreadable_value_is_skipped_not_reported(self):
        self.warn(self.requested("not a date", "2026-10-14"))
        self.assertEqual(self.h.frappe.shown, [])
        self.assertEqual(self.h.frappe.logged, [])

    def test_date_and_datetime_objects_compare(self):
        self.warn(self.requested(datetime.date(2026, 10, 20), datetime.date(2026, 10, 14)))
        self.assertEqual(len(self.h.frappe.shown), 1)

    def test_a_doctype_outside_the_registry_is_silent(self):
        self.warn(Doc("Customer", custom_project_start_date="2026-10-20", custom_project_end_date="2026-10-14"))
        self.assertEqual(self.h.frappe.shown, [])

    def test_it_never_blocks_and_nothing_propagates(self):
        reversed_doc = self.requested("2026-10-20", "2026-10-14")

        def boom(*a, **k):
            raise RuntimeError("redis, the log table and the dialog are all down")

        for target in ("msgprint", "get_datetime", "first_time"):
            with self.subTest(failing=target):
                h = Harness()
                self.addCleanup(h.restore)
                if target == "msgprint":
                    h.frappe.msgprint = boom
                elif target == "get_datetime":
                    h.frappe.utils.get_datetime = boom
                else:
                    h.module.first_time = boom
                h.module.warn_out_of_order(reversed_doc)  # must not raise, and must not throw
                h.frappe.log_error = boom
                h.module.warn_out_of_order(reversed_doc)  # not even when logging fails

    def test_it_stays_quiet_where_nobody_can_read_it(self):
        reversed_doc = self.requested("2026-10-20", "2026-10-14")
        for flag in ("in_import", "in_install", "in_migrate", "in_patch"):
            setattr(self.h.frappe.flags, flag, True)
            self.warn(reversed_doc)
            setattr(self.h.frappe.flags, flag, False)
        self.h.frappe.local.request = None  # a background job or a script
        self.warn(reversed_doc)
        self.h.frappe.local.request = object()
        self.h.frappe.session.user = "Guest"
        self.warn(reversed_doc)
        self.h.frappe.session.user = "lisa@example.com"
        self.warn(self.requested("2026-10-20", "2026-10-14", docstatus=2))
        self.assertEqual(self.h.frappe.shown, [])
        # Skipping must not use up the one pop-up the next real save is owed.
        self.assertEqual(self.h.first_time_calls, [])
        self.warn(reversed_doc)
        self.assertEqual(len(self.h.frappe.shown), 1)

    def test_it_never_calls_throw(self):
        # frappe.throw in the stub raises AssertionError, which would be caught and logged; so the
        # source must not contain a call at all.
        source = MODULE_FILE.read_text(encoding="utf-8")
        code = ast.unparse(ast.parse(source))
        self.assertNotIn("frappe.throw", code)


class TestRegistry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        h = Harness()
        cls.pairs = h.module.PAIRS
        cls.hook = h.module.HOOK
        h.restore()

    @staticmethod
    def doc_events():
        tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "doc_events":
                return ast.literal_eval(node.value)
        raise AssertionError("doc_events not found in hooks.py")

    def test_the_hook_constant_is_the_function_hooks_py_registers(self):
        self.assertEqual(self.hook, HOOK)

    def test_every_registered_doctype_has_a_validate_hook_pointing_at_the_function(self):
        events = self.doc_events()
        for doctype in self.pairs:
            with self.subTest(doctype=doctype):
                self.assertIn(doctype, events, f"{doctype} has no doc_events block")
                validate = events[doctype].get("validate")
                hooks = [validate] if isinstance(validate, str) else list(validate or [])
                self.assertIn(HOOK, hooks)

    def test_no_other_doctype_is_hooked_to_it(self):
        for doctype, handlers in self.doc_events().items():
            hooked = any(
                HOOK in ([v] if isinstance(v, str) else v)
                for v in handlers.values()
                if isinstance(v, str | list)
            )
            if hooked:
                self.assertIn(doctype, self.pairs, f"{doctype} is hooked but not in the registry")

    def test_existing_validate_hooks_survive(self):
        events = self.doc_events()
        task = events["Task"]["validate"]
        self.assertEqual(task[0], "erpnext_enhancements.training.compliance.warn_uncertified_assignee")
        self.assertIn("erpnext_enhancements.project_enhancements.crew_sync.validate_crew", task)
        self.assertIn(
            "erpnext_enhancements.project_enhancements.budget_rollup.on_project_validate",
            events["Project"]["validate"],
        )
        self.assertIn(
            "erpnext_enhancements.quality.msa_enforcement.on_contract_validate",
            events["Project Contract"]["validate"],
        )
        self.assertIn(
            "erpnext_enhancements.crm_enhancements.attribution.enforce_source", events["Opportunity"]["validate"]
        )

    def test_shape_of_every_pair(self):
        for doctype, pairs in self.pairs.items():
            for pair in pairs:
                with self.subTest(doctype=doctype, fields=pair["fields"]):
                    self.assertGreaterEqual(len(pair["fields"]), 2)
                    self.assertEqual(len(pair["fields"]), len(pair["labels"]))
                    self.assertEqual(len(set(pair["fields"])), len(pair["fields"]))
                    self.assertTrue(pair["message"].endswith("."))

    def test_the_required_pairs_are_present(self):
        opportunity = {tuple(p["fields"]) for p in self.pairs["Opportunity"]}
        self.assertIn(("custom_project_start_date", "custom_project_end_date"), opportunity)
        self.assertIn(
            (
                "custom_delivery_date_time",
                "custom_setup_date_time",
                "custom_event_date_time",
                "custom_take_down_date_time",
            ),
            opportunity,
        )

    def test_nothing_another_validator_already_throws_on_is_registered(self):
        for doctype, banned in VALIDATED_ELSEWHERE.items():
            registered = {f for p in self.pairs.get(doctype, []) for f in p["fields"]}
            self.assertEqual(registered & banned, set(), f"{doctype}: core already validates these")
        for doctype in VALIDATED_DOCTYPES:
            self.assertNotIn(doctype, self.pairs, f"{doctype}'s own controller already throws on its dates")

    def test_every_field_exists_with_the_registry_label_and_is_a_date(self):
        known = {}
        for field in json.loads((APP_ROOT / "fixtures" / "custom_field.json").read_text(encoding="utf-8")):
            known[(field["dt"], field["fieldname"])] = (field.get("label"), field.get("fieldtype"))
        for path in APP_ROOT.glob("**/doctype/*/*.json"):
            if path.stem != path.parent.name:
                continue
            try:
                doctype = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            for field in doctype.get("fields", []):
                known.setdefault((doctype.get("name"), field["fieldname"]), (field.get("label"), field.get("fieldtype")))
        for doctype, pairs in self.pairs.items():
            for pair in pairs:
                for fieldname, label in zip(pair["fields"], pair["labels"], strict=True):
                    with self.subTest(doctype=doctype, field=fieldname):
                        self.assertIn((doctype, fieldname), known, f"{doctype}.{fieldname} does not exist")
                        real_label, fieldtype = known[(doctype, fieldname)]
                        self.assertIn(fieldtype, ("Date", "Datetime"))
                        self.assertEqual(label, real_label)


class TestDateDisplay(unittest.TestCase):
    """``utils/date_display``: the Jinja doors onto ``normalize.format_*`` (no frappe import)."""

    NORMALIZE = "erpnext_enhancements.utils.normalize"

    def setUp(self):
        self._saved = sys.modules.get(self.NORMALIZE)
        self.addCleanup(self._restore)
        spec = importlib.util.spec_from_file_location("_date_display_under_test", APP_ROOT / "utils" / "date_display.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def _restore(self):
        if self._saved is None:
            sys.modules.pop(self.NORMALIZE, None)
        else:
            sys.modules[self.NORMALIZE] = self._saved

    def _install(self, **functions):
        fake = types.ModuleType(self.NORMALIZE)
        for name, fn in functions.items():
            setattr(fake, name, fn)
        sys.modules[self.NORMALIZE] = fake

    def test_each_global_delegates_to_its_normalize_function(self):
        self._install(
            format_date=lambda v: f"date:{v}",
            format_datetime=lambda v: f"datetime:{v}",
            format_time=lambda v: f"time:{v}",
        )
        self.assertEqual(self.module.sf_date("x"), "date:x")
        self.assertEqual(self.module.sf_datetime("x"), "datetime:x")
        self.assertEqual(self.module.sf_time("x"), "time:x")

    def test_a_print_never_fails_on_a_date(self):
        def boom(value):
            raise ValueError("cannot read this")

        self._install(format_date=boom, format_datetime=boom, format_time=boom)
        for fn in (self.module.sf_date, self.module.sf_datetime, self.module.sf_time):
            self.assertEqual(fn("2026-10-14"), "2026-10-14")
            self.assertEqual(fn(None), "")

    def test_the_module_imports_without_normalize_or_frappe(self):
        source = (APP_ROOT / "utils" / "date_display.py").read_text(encoding="utf-8")
        top_level = [n for n in ast.parse(source).body if isinstance(n, ast.Import | ast.ImportFrom)]
        self.assertEqual(top_level, [])

    def test_the_three_globals_are_registered_individually_and_prefixed(self):
        tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
        jinja = next(
            ast.literal_eval(n.value)
            for n in tree.body
            if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "jinja"
        )
        for name in ("sf_date", "sf_datetime", "sf_time"):
            self.assertIn(f"erpnext_enhancements.utils.date_display.{name}", jinja["methods"])
            self.assertTrue(callable(getattr(self.module, name)))
        # The module itself is never registered: that would export every function it has.
        self.assertNotIn("erpnext_enhancements.utils.date_display", jinja["methods"])


if __name__ == "__main__":
    unittest.main()
