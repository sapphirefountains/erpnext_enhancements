"""Bench-free tests for ``patches/set_desk_date_and_time_format`` (form kit P2-A).

What must hold, because a mistake here is either a deploy that aborts or a Desk that quietly
still shows the old format:

- a field still holding the old value moves to the new one, in System Settings **and** in the
  global default (the Desk's boot payload reads ``tabDefaultValue``, not the Single);
- an already-new value, a different custom format, or a field with no stored value is left alone;
- the two fields are independent: one moving does not drag the other;
- the Single is never saved through its controller (its validate() enforces unrelated rules);
- the cache is cleared only when something changed;
- nothing inside can propagate: a raising patch aborts ``bench migrate``, which here is the deploy;
- it is safe to run twice.

The module is loaded from its file under a private ``frappe`` stub that is put back afterwards, so
this suite cannot cross-talk with the others.

Run: python -m unittest erpnext_enhancements.tests.test_desk_date_format_patch -v
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path

PATCH_FILE = Path(__file__).resolve().parents[1] / "patches" / "set_desk_date_and_time_format.py"

OLD = {"date_format": "mm-dd-yyyy", "time_format": "HH:mm:ss"}


class FakeDb:
    def __init__(self, settings):
        self.settings = dict(settings)
        self.defaults = dict(settings)
        self.sql_calls = []
        self.set_value_calls = []
        self.raise_on = set()

    def _maybe_raise(self, name):
        if name in self.raise_on:
            raise RuntimeError(f"{name} blew up")

    def get_single_value(self, doctype, field, *a, **k):
        self._maybe_raise("get_single_value")
        assert doctype == "System Settings", doctype
        return self.settings.get(field)

    def set_single_value(self, doctype, field, value, *a, **k):
        self._maybe_raise("set_single_value")
        assert doctype == "System Settings", doctype
        self.settings[field] = value

    def set_default(self, key, value, *a, **k):
        self._maybe_raise("set_default")
        self.defaults[key] = value

    def sql(self, query, values=None, *a, **k):
        self._maybe_raise("sql")
        self.sql_calls.append((query, values))

    def set_value(self, doctype, filters, field, value, *a, **k):
        self._maybe_raise("set_value")
        self.set_value_calls.append((doctype, filters, field, value))


def load_patch(db):
    frappe = types.ModuleType("frappe")
    frappe.db = db
    frappe.cache_clears = 0
    frappe.logged = []

    def clear_cache(*a, **k):
        frappe.cache_clears += 1

    frappe.clear_cache = clear_cache
    frappe.log_error = lambda **k: frappe.logged.append(k)
    frappe.get_traceback = lambda: "traceback"

    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = frappe
    try:
        spec = importlib.util.spec_from_file_location("_set_desk_date_and_time_format_under_test", PATCH_FILE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved
    return module, frappe


class TestDeskDateFormatPatch(unittest.TestCase):
    def run_patch(self, settings):
        db = FakeDb(settings)
        module, frappe = load_patch(db)
        module.execute()
        return db, frappe

    def test_old_values_change_in_settings_and_in_the_global_default(self):
        db, frappe = self.run_patch(OLD)
        self.assertEqual(db.settings, {"date_format": "mm/dd/yyyy", "time_format": "HH:mm"})
        # The Desk reads tabDefaultValue, so a bare set_single_value would not be enough.
        self.assertEqual(db.defaults, {"date_format": "mm/dd/yyyy", "time_format": "HH:mm"})
        self.assertEqual(frappe.cache_clears, 1)

    def test_already_new_values_are_unchanged_and_the_cache_is_left_alone(self):
        new = {"date_format": "mm/dd/yyyy", "time_format": "HH:mm"}
        db, frappe = self.run_patch(new)
        self.assertEqual(db.settings, new)
        self.assertEqual(db.defaults, new)
        self.assertEqual(db.sql_calls, [])
        self.assertEqual(db.set_value_calls, [])
        self.assertEqual(frappe.cache_clears, 0)

    def test_a_different_custom_format_is_left_alone(self):
        custom = {"date_format": "dd/mm/yyyy", "time_format": "HH:mm"}
        db, frappe = self.run_patch(custom)
        self.assertEqual(db.settings, custom)
        self.assertEqual(db.defaults, custom)
        self.assertEqual(frappe.cache_clears, 0)

    def test_a_field_with_no_stored_value_is_left_alone(self):
        db, frappe = self.run_patch({})
        self.assertEqual(db.settings, {})
        self.assertEqual(db.defaults, {})
        self.assertEqual(frappe.cache_clears, 0)

    def test_the_two_fields_are_independent(self):
        db, frappe = self.run_patch({"date_format": "mm-dd-yyyy", "time_format": "HH:mm"})
        self.assertEqual(db.settings, {"date_format": "mm/dd/yyyy", "time_format": "HH:mm"})
        db, _ = self.run_patch({"date_format": "yyyy-mm-dd", "time_format": "HH:mm:ss"})
        self.assertEqual(db.settings, {"date_format": "yyyy-mm-dd", "time_format": "HH:mm"})

    def test_stragglers_are_swapped_only_where_they_hold_the_old_value(self):
        db, _ = self.run_patch(OLD)
        # Per-user default rows: parameterised, old value in the WHERE, global row excluded.
        self.assertEqual(len(db.sql_calls), 2)
        for query, values in db.sql_calls:
            self.assertIn("tabDefaultValue", query)
            self.assertIn("defvalue = %s", query.split("where")[1])
            self.assertEqual(values[-1], "__default")
        self.assertEqual(
            [call[1] for call in db.set_value_calls],
            [{"date_format": "mm-dd-yyyy"}, {"time_format": "HH:mm:ss"}],
        )
        self.assertTrue(all(call[0] == "Language" for call in db.set_value_calls))

    def test_safe_to_run_twice(self):
        db = FakeDb(OLD)
        module, frappe = load_patch(db)
        module.execute()
        calls_after_first = (len(db.sql_calls), len(db.set_value_calls), frappe.cache_clears)
        module.execute()
        self.assertEqual((len(db.sql_calls), len(db.set_value_calls), frappe.cache_clears), calls_after_first)
        self.assertEqual(db.settings, {"date_format": "mm/dd/yyyy", "time_format": "HH:mm"})

    def test_it_never_saves_the_single_through_its_controller(self):
        source = PATCH_FILE.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.split('"""')[2].splitlines() if not line.lstrip().startswith("#")
        )
        for forbidden in ("get_doc(", ".save(", "get_cached_doc(", "get_single("):
            self.assertNotIn(forbidden, code)

    def test_nothing_inside_can_propagate(self):
        for failing in ("get_single_value", "set_single_value", "set_default", "sql", "set_value"):
            with self.subTest(failing=failing):
                db = FakeDb(OLD)
                db.raise_on = {failing}
                module, frappe = load_patch(db)
                module.execute()  # must not raise
                self.assertTrue(frappe.logged, f"{failing} failed silently without a log row")

    def test_a_failing_cache_clear_does_not_propagate(self):
        db = FakeDb(OLD)
        module, frappe = load_patch(db)

        def boom(*a, **k):
            raise RuntimeError("redis is down")

        frappe.clear_cache = boom
        module.execute()
        self.assertEqual(db.settings["date_format"], "mm/dd/yyyy")

    def test_a_failing_logger_does_not_propagate(self):
        db = FakeDb(OLD)
        db.raise_on = {"get_single_value"}
        module, frappe = load_patch(db)

        def boom(**k):
            raise RuntimeError("log table is gone")

        frappe.log_error = boom
        module.execute()

    def test_the_patch_is_registered_after_model_sync(self):
        text = (PATCH_FILE.parents[1] / "patches.txt").read_text(encoding="utf-8")
        post = text.split("[post_model_sync]", 1)[1]
        self.assertIn("erpnext_enhancements.patches.set_desk_date_and_time_format", post.splitlines())


if __name__ == "__main__":
    unittest.main()
