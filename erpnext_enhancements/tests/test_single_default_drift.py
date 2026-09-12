# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Guards on `patches/restore_drifted_single_defaults.py`, and on the class of bug it fixes.

**A `default` on a field of a Single never reaches the row that already exists.** `bench
migrate` writes no `tabSingles` row for a newly declared field and `load_from_db` applies no
defaults — they fire in `new_doc()`, i.e. fresh install and never again. So the JSON says one
thing and every live site reads another.

A production sweep found **23 such fields**. Most were inert, because the read site coalesces
(`cint(...) or 120`). Eight were not, because their read site deliberately honours an explicit
zero, and those had real effects:

* `pipeline_stale_amber_days` / `pipeline_stale_red_days` — `_stale_level` is guarded
  `if amber_days > 0`, so every staleness colour on the Sales Pipeline board was dead while the
  board's caption printed the raw value and read "amber after 0 days" — the inverse of the truth.
* `geofence_radius_m` — 0 is a documented *disabled* sentinel, so the kiosk's nearby-visit
  suggestion returned before it ever queried a coordinate.
* five `fleet_*` intervals — `add_months(last, 0)` makes a vehicle's next-due date equal its
  last-done date, pinning it Overdue from the day after it is serviced.

**The thing this file exists to stop is the patch being wrong in the two ways it could be**, and
the second is subtle:

1. Writing over a value somebody deliberately chose. Prevented structurally: the table contains
   no `Check` field, so there is no "unticked on purpose" reading of a falsy value to get wrong.
2. Being a patch that matches nothing and logs itself as a success — `tabPatch Log` cannot tell
   that apart from a real run (v1.280.3). Prevented by keying on falsiness rather than on row
   absence, which is the OPPOSITE of `backfill_chat_settings_defaults` and only safe because of
   (1).

Bench-free: filesystem, `json` and `ast` only. No live values are asserted — this checks the
patch's shape, not production data.

Run: python -m unittest erpnext_enhancements.tests.test_single_default_drift
"""

import ast
import glob
import json
import os
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
PATCH = APP / "patches/restore_drifted_single_defaults.py"
PATCHES_TXT = APP / "patches.txt"

NO_VALUE = {"Section Break", "Column Break", "Tab Break", "Button", "HTML", "Heading", "Fold"}


def _singles():
    """Every Single doctype in the app: name -> {fieldname: field dict}."""
    out = {}
    for path in glob.glob(str(APP / "**/doctype/*/*.json"), recursive=True):
        if os.path.basename(path).replace(".json", "") != os.path.basename(os.path.dirname(path)):
            continue
        try:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            continue
        if doc.get("issingle"):
            out[doc["name"]] = {f["fieldname"]: f for f in doc.get("fields", [])}
    return out


def _table():
    """The patch's TABLE, read from source rather than imported.

    Importing would need `frappe`, which is not available bench-free. `ast.literal_eval`
    on the two dict literals gives the same answer without it.
    """
    tree = ast.parse(PATCH.read_text(encoding="utf-8"))
    consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and isinstance(node.value, ast.Dict):
                try:
                    consts[target.id] = ast.literal_eval(node.value)
                except ValueError:
                    pass
    return {
        "ERPNext Enhancements Settings": consts.get("ENHANCEMENTS", {}),
        "Training Settings": consts.get("TRAINING", {}),
    }


class TestTheTableMatchesTheDeclaredDefaults(unittest.TestCase):
    def test_every_field_exists_on_its_single(self):
        """A fieldname typo writes a `tabSingles` row nothing will ever read, and nothing
        anywhere would complain."""
        singles = _singles()
        for doctype, fields in _table().items():
            self.assertIn(doctype, singles, f"{doctype} is not a Single in this app")
            for fieldname in fields:
                with self.subTest(field=f"{doctype}.{fieldname}"):
                    self.assertIn(fieldname, singles[doctype])

    def test_every_value_is_the_declared_default(self):
        """The patch restores what the JSON always said. If the two disagree, one of them is
        a decision somebody made without writing it down."""
        singles = _singles()
        for doctype, fields in _table().items():
            for fieldname, want in fields.items():
                with self.subTest(field=f"{doctype}.{fieldname}"):
                    declared = singles[doctype][fieldname].get("default")
                    self.assertEqual(
                        str(want),
                        str(declared),
                        f"patch writes {want!r} but the JSON declares {declared!r}",
                    )

    def test_no_declared_default_is_falsy(self):
        """Restoring a falsy default over a falsy stored value is a no-op that still reports
        success — the failure mode this whole exercise is about."""
        singles = _singles()
        for doctype, fields in _table().items():
            for fieldname in fields:
                with self.subTest(field=f"{doctype}.{fieldname}"):
                    declared = str(singles[doctype][fieldname].get("default") or "")
                    self.assertNotEqual(declared, "")
                    self.assertNotEqual(declared, "0")


class TestItCannotUndoSomebodysDecision(unittest.TestCase):
    """The safety argument `backfill_chat_settings_defaults` makes with its predicate, this
    patch has to make with its contents — because its predicate is the opposite one."""

    def test_the_table_contains_no_check_field(self):
        """THE load-bearing assertion. An unticked checkbox and a phantom 0 are both falsy and
        are not the same fact. With no Check in the table, a falsiness predicate cannot
        re-enable anything anyone switched off — whatever anyone later assumes about it."""
        singles = _singles()
        offenders = [
            f"{doctype}.{fieldname}"
            for doctype, fields in _table().items()
            for fieldname in fields
            if singles[doctype][fieldname].get("fieldtype") == "Check"
        ]
        self.assertEqual(
            offenders,
            [],
            "A Check in this table makes the falsiness predicate unsafe: it would rewrite a "
            "box somebody deliberately unticked. Add it to a separate, explicitly-decided "
            f"patch instead. Offending: {offenders}",
        )

    def test_briefing_use_gemini_is_not_in_the_table(self):
        """Named explicitly because it is the one drifted Check, and because it is a SPEND
        decision — writing 1 starts a Vertex call per recipient every weekday morning. It needs
        a person to say yes, and a later edit that quietly folds it in should fail here."""
        for fields in _table().values():
            self.assertNotIn("briefing_use_gemini", fields)

    def test_it_is_not_a_restore_everything_sweep(self):
        """On a Single whose rows already exist, a sweep can only key on falsiness — and would
        re-tick `po_sod_enforcement_enabled`, `handoff_gate_enabled` and
        `fountain_move_auto_convert` over somebody's decision. The table must stay a strict
        subset of the declared non-falsy defaults, never all of them."""
        singles = _singles()
        for doctype, fields in _table().items():
            declared = {
                name
                for name, f in singles[doctype].items()
                if f.get("fieldtype") not in NO_VALUE and f.get("default") not in (None, "", "0")
            }
            self.assertTrue(set(fields) <= declared)
            self.assertLess(
                len(fields),
                len(declared),
                f"{doctype}: the table covers every declared default, which makes it a sweep",
            )


class TestTheMechanicsCannotAbortAMigrate(unittest.TestCase):
    """A patch that raises aborts `bench migrate`, which on this repo IS the deploy. One did,
    and left production schema-synced, half-patched, fixtureless and reporting the new version
    string (v1.395.0)."""

    def _source(self):
        """The patch's EXECUTABLE source: comments and docstrings stripped.

        Learned the hard way, one more time. The first version of
        `test_it_never_reads_the_singles_table_directly` read the file raw and failed
        against the fixed patch — because the patch's docstring says `get_value("Singles",
        ...)` while explaining why it must never be used. Twelfth occurrence of that trap
        in this project, and the first inside a test written about the trap itself.

        `ast` never retains comments, so unparsing drops them; docstrings are stripped
        explicitly."""
        tree = ast.parse(PATCH.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
        # Quotes normalised: `ast.unparse` always emits single quotes, so an assertion
        # written against the source's double quotes would fail on correct code.
        return ast.unparse(ast.fix_missing_locations(tree)).replace("'", '"')

    def test_it_writes_with_set_single_value_not_save(self):
        """`.save()` runs the settings controller's `validate`, which can throw on a field this
        patch never touches — `TrainingSettings._reject_absurd_runtime_values` guards four."""
        source = self._source()
        self.assertIn("frappe.db.set_single_value", source)
        tree = ast.parse(source)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("save", "insert", "submit")
        ]
        self.assertEqual(calls, [], "this patch must not call save()/insert()/submit()")

    def test_it_never_reads_the_singles_table_directly(self):
        """`tabSingles` has three columns and no `creation`, so `get_value("Singles", ...)`
        cannot succeed against the default `order_by` — it fails on every site, every time."""
        self.assertNotIn('"Singles"', self._source())
        self.assertIn("get_single_value", self._source())

    def test_it_guards_on_the_doctype_existing(self):
        self.assertIn('frappe.db.exists("DocType"', self._source())

    def test_every_write_is_wrapped(self):
        """One unexpected exception must not take the deploy with it."""
        source = self._source()
        tree = ast.parse(source)
        writes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "set_single_value"
        ]
        self.assertTrue(writes)
        handlers = [n for n in ast.walk(tree) if isinstance(n, ast.Try)]
        self.assertGreaterEqual(len(handlers), 2)

    def test_it_is_registered_in_patches_txt(self):
        """A patch not in `patches.txt` never runs, and nothing says so."""
        self.assertIn(
            "erpnext_enhancements.patches.restore_drifted_single_defaults",
            PATCHES_TXT.read_text(encoding="utf-8"),
        )

    def test_it_runs_post_model_sync(self):
        """The fields have to exist as columns before anything can be written to them."""
        text = PATCHES_TXT.read_text(encoding="utf-8")
        at = text.index("erpnext_enhancements.patches.restore_drifted_single_defaults")
        self.assertIn("[post_model_sync]", text[:at])


class TestTheAssertionsCannotPassVacuously(unittest.TestCase):
    """Most of the above iterate over `_table()`. If that parser silently returned nothing,
    every one of them would pass while checking an empty dict."""

    def test_the_table_parser_finds_the_entries(self):
        table = _table()
        self.assertEqual(sorted(table), ["ERPNext Enhancements Settings", "Training Settings"])
        self.assertGreaterEqual(len(table["ERPNext Enhancements Settings"]), 15)
        self.assertGreaterEqual(len(table["Training Settings"]), 2)

    def test_the_patch_source_stripper_keeps_the_code_and_drops_the_prose(self):
        """If `_source()` over-stripped, every absence assertion in
        TestTheMechanicsCannotAbortAMigrate would pass while reading almost nothing."""
        stripped = TestTheMechanicsCannotAbortAMigrate()._source()
        self.assertIn("def execute", stripped)
        self.assertIn("set_single_value", stripped)
        self.assertNotIn("OPPOSITE", stripped)
        self.assertNotIn("v1.280.3", stripped)

    def test_the_singles_scanner_finds_the_singles(self):
        singles = _singles()
        self.assertGreater(len(singles), 10)
        self.assertIn("Training Settings", singles)
        self.assertIn("ERPNext Enhancements Settings", singles)


# Runs LAST, deliberately — see tests/test_test_collection.py.
if __name__ == "__main__":
    unittest.main()
