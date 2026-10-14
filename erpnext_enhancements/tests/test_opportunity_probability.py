"""Bench-free tests for the default forecast probability per Opportunity stage (form kit P5.5).

Nik, 2026-10-13: Qualification 10, Needs Analysis 25, Proposal/Price Quote 50, Negotiation/Review
75; Sales can override any deal, and a hand-set number survives a stage change. Held here:

* the save rule (``crm_enhancements.opportunity_probability``): entering a stage takes its
  default; typing a probability marks the deal set by hand and is kept; unticking goes back to
  the default; a new deal takes the default unless it arrives with its own number; On Hold has no
  default; and with no field or no table it does nothing and never raises;
* the three patches: the field is created from the shared definition; the table is seeded only
  when empty and the Single is never saved; the backfill moves exactly the deals at ERPNext's
  default 100 in a stage with a default and marks the rest, twice-safe;
* the settings validation (one row per stage, 0–100), and the wiring in hooks, patches.txt and
  the Opportunity field order.

A minimal ``frappe`` stub is installed for the imports and removed afterwards.

Run: python -m unittest erpnext_enhancements.tests.test_opportunity_probability -v
"""

import importlib
import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
MODULES = (
    "erpnext_enhancements.crm_enhancements.opportunity_probability",
    "erpnext_enhancements.patches.add_opportunity_probability_by_hand",
    "erpnext_enhancements.patches.seed_opportunity_stage_probabilities",
    "erpnext_enhancements.patches.backfill_opportunity_probability",
    "erpnext_enhancements.crm_enhancements.doctype.sales_activity_settings.sales_activity_settings",
)
STUBBED = (
    "frappe",
    "frappe.utils",
    "frappe.model",
    "frappe.model.document",
    "frappe.custom",
    "frappe.custom.doctype",
    "frappe.custom.doctype.custom_field",
    "frappe.custom.doctype.custom_field.custom_field",
)

_saved = {}
prob = add_patch = seed_patch = backfill_patch = settings_module = None


class _Dict(dict):
    __getattr__ = dict.get


def _flt(value, precision=None):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _cint(value):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


class ValidationError(Exception):
    pass


class FakeMeta:
    def __init__(self, fields):
        self.fields = set(fields)

    def has_field(self, fieldname):
        return fieldname in self.fields


class FakeOpportunity:
    """Enough of a Document for the rule: get/set, is_new, get_doc_before_save, meta."""

    def __init__(self, before=None, with_field=True, **values):
        self.values = dict(values)
        self._before = before
        self.meta = FakeMeta({"status", "probability", *(["custom_probability_by_hand"] if with_field else [])})

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def is_new(self):
        return self._before is None

    def get_doc_before_save(self):
        return _Dict(self._before) if self._before is not None else None


def saved(before, **changes):
    """An existing deal: ``before`` as stored, then ``changes`` made in this save."""
    return FakeOpportunity(before=before, **{**before, **changes})


class FakeDB:
    def __init__(self, deals=None, rows=None, has_field=True):
        self.deals = {d["name"]: dict(d) for d in deals or []}
        self.rows = list(rows or [])
        self.has_field = has_field
        self.writes = []
        self.commits = 0

    def table_exists(self, doctype):
        return doctype == "Opportunity Stage Probability"

    def count(self, doctype, filters):
        return len(self.rows)

    def has_column(self, doctype, column):
        return self.has_field

    def set_value(self, doctype, name, field, value, update_modified=True):
        self.writes.append((name, field, value, update_modified))
        self.deals[name][field] = value

    def commit(self):
        self.commits += 1


def setUpModule():
    global prob, add_patch, seed_patch, backfill_patch, settings_module
    for name in (*STUBBED, *MODULES):
        _saved[name] = sys.modules.get(name)
        sys.modules.pop(name, None)
    frappe = types.ModuleType("frappe")
    frappe._dict = _Dict
    frappe._ = lambda text, *a, **k: text
    frappe.flags = _Dict()
    frappe.ValidationError = ValidationError

    def throw(message, *args, **kwargs):
        raise ValidationError(message)

    frappe.throw = throw
    utils = types.ModuleType("frappe.utils")
    utils.flt = _flt
    utils.cint = _cint
    frappe.utils = utils
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")

    class Document:
        def __init__(self, **values):
            self.__dict__.update(values)

        def get(self, key, default=None):
            return self.__dict__.get(key, default)

    document.Document = Document
    custom_field = types.ModuleType("frappe.custom.doctype.custom_field.custom_field")
    custom_field.create_custom_fields = lambda spec, update=False: STATE["created"].append((spec, update))
    for name, module in (
        ("frappe", frappe),
        ("frappe.utils", utils),
        ("frappe.model", model),
        ("frappe.model.document", document),
        ("frappe.custom", types.ModuleType("frappe.custom")),
        ("frappe.custom.doctype", types.ModuleType("frappe.custom.doctype")),
        ("frappe.custom.doctype.custom_field", types.ModuleType("frappe.custom.doctype.custom_field")),
        ("frappe.custom.doctype.custom_field.custom_field", custom_field),
    ):
        sys.modules[name] = module
    prob = importlib.import_module(MODULES[0])
    add_patch = importlib.import_module(MODULES[1])
    seed_patch = importlib.import_module(MODULES[2])
    backfill_patch = importlib.import_module(MODULES[3])
    settings_module = importlib.import_module(MODULES[4])


def tearDownModule():
    for name, module in _saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


STATE = {"created": []}


def install(table=None, db=None):
    """Point the stub at a settings table (``{stage: probability}``) and a fake db."""
    frappe = sys.modules["frappe"]
    frappe.flags = _Dict()
    rows = [_Dict(stage=s, probability=p) for s, p in (table or {}).items()]
    frappe.get_cached_doc = lambda doctype: _Dict({"stage_probabilities": rows})
    frappe.db = db or FakeDB()
    STATE["logged"] = []
    frappe.log_error = lambda title=None, message=None, **k: STATE["logged"].append((title, message))
    frappe.get_traceback = lambda: "traceback"
    STATE["inserted"] = []
    STATE["cleared"] = []
    frappe.clear_document_cache = lambda dt, name=None: STATE["cleared"].append((dt, name))

    class Inserted:
        def __init__(self, spec):
            self.spec = spec

        def db_insert(self):
            STATE["inserted"].append(self.spec)

    frappe.get_doc = lambda spec: Inserted(spec)

    def get_all(doctype, filters=None, fields=None, limit_page_length=None):
        stages = filters["status"][1]
        return [
            _Dict(d)
            for d in frappe.db.deals.values()
            if d.get("docstatus", 0) == 0 and d["status"] in stages
        ]

    frappe.get_all = get_all
    return frappe


DEFAULTS = {"Qualification": 10, "Needs Analysis": 25, "Proposal/Price Quote": 50, "Negotiation/Review": 75}


class TestTheRule(unittest.TestCase):
    def setUp(self):
        install(DEFAULTS)

    def run_rule(self, doc):
        prob.apply_stage_probability(doc)
        return doc.values

    def test_entering_a_stage_takes_its_default(self):
        before = {"status": "Qualification", "probability": 10, "custom_probability_by_hand": 0}
        self.assertEqual(self.run_rule(saved(before, status="Needs Analysis"))["probability"], 25)
        self.assertEqual(self.run_rule(saved(before, status="Negotiation/Review"))["probability"], 75)

    def test_a_stage_with_no_default_leaves_the_probability(self):
        before = {"status": "Negotiation/Review", "probability": 75, "custom_probability_by_hand": 0}
        for status in ("On Hold", "Closed Won", "Lost"):
            with self.subTest(status=status):
                self.assertEqual(self.run_rule(saved(before, status=status))["probability"], 75)

    def test_typing_a_probability_marks_it_set_by_hand(self):
        before = {"status": "Qualification", "probability": 10, "custom_probability_by_hand": 0}
        values = self.run_rule(saved(before, probability=40))
        self.assertEqual((values["probability"], values["custom_probability_by_hand"]), (40, 1))

    def test_a_hand_set_probability_survives_a_stage_change(self):
        before = {"status": "Qualification", "probability": 40, "custom_probability_by_hand": 1}
        values = self.run_rule(saved(before, status="Proposal/Price Quote"))
        self.assertEqual((values["probability"], values["custom_probability_by_hand"]), (40, 1))

    def test_changing_stage_and_probability_together_keeps_the_typed_number(self):
        before = {"status": "Qualification", "probability": 10, "custom_probability_by_hand": 0}
        values = self.run_rule(saved(before, status="Proposal/Price Quote", probability=65))
        self.assertEqual((values["probability"], values["custom_probability_by_hand"]), (65, 1))

    def test_unticking_goes_back_to_the_stage_default(self):
        before = {"status": "Proposal/Price Quote", "probability": 65, "custom_probability_by_hand": 1}
        values = self.run_rule(saved(before, custom_probability_by_hand=0))
        self.assertEqual(values["probability"], 50)

    def test_a_save_that_changes_neither_leaves_both(self):
        before = {"status": "Needs Analysis", "probability": 25, "custom_probability_by_hand": 0}
        values = self.run_rule(saved(before))
        self.assertEqual((values["probability"], values["custom_probability_by_hand"]), (25, 0))

    def test_a_new_deal_takes_its_stage_default(self):
        doc = FakeOpportunity(status="Qualification", probability=100, custom_probability_by_hand=0)
        self.assertEqual(self.run_rule(doc)["probability"], 10)
        doc = FakeOpportunity(status="Qualification", probability=None, custom_probability_by_hand=0)
        self.assertEqual(self.run_rule(doc)["probability"], 10)

    def test_a_new_deal_with_its_own_number_keeps_it(self):
        doc = FakeOpportunity(status="Qualification", probability=60, custom_probability_by_hand=0)
        values = self.run_rule(doc)
        self.assertEqual((values["probability"], values["custom_probability_by_hand"]), (60, 1))

    def test_a_new_on_hold_deal_is_left(self):
        doc = FakeOpportunity(status="On Hold", probability=100, custom_probability_by_hand=0)
        self.assertEqual(self.run_rule(doc)["probability"], 100)


class TestDefensive(unittest.TestCase):
    def test_without_the_custom_field_it_does_nothing(self):
        install(DEFAULTS)
        before = {"status": "Qualification", "probability": 10}
        doc = FakeOpportunity(before=before, with_field=False, status="Needs Analysis", probability=10)
        prob.apply_stage_probability(doc)
        self.assertEqual(doc.values["probability"], 10)

    def test_an_empty_table_does_nothing(self):
        install({})
        before = {"status": "Qualification", "probability": 100, "custom_probability_by_hand": 0}
        doc = saved(before, status="Needs Analysis")
        prob.apply_stage_probability(doc)
        self.assertEqual(doc.values["probability"], 100)

    def test_an_unreadable_settings_doc_does_nothing(self):
        frappe = install(DEFAULTS)
        frappe.get_cached_doc = lambda doctype: (_ for _ in ()).throw(RuntimeError("no table"))
        self.assertEqual(prob.stage_defaults(), {})

    def test_it_never_raises_and_logs_once(self):
        install(DEFAULTS)

        class Broken(FakeOpportunity):
            def get_doc_before_save(self):
                raise RuntimeError("boom")

        for _ in range(3):
            prob.apply_stage_probability(Broken(before={}, status="Qualification", probability=10))
        self.assertEqual(len(STATE["logged"]), 1)


class TestPatches(unittest.TestCase):
    def test_the_field_is_created_from_the_shared_definition(self):
        install(DEFAULTS)
        STATE["created"].clear()
        add_patch.execute()
        ((spec, update),) = STATE["created"]
        self.assertTrue(update)
        self.assertEqual(spec, {"Opportunity": [prob.field_definition()]})
        field = prob.field_definition()
        self.assertEqual(
            (field["fieldname"], field["fieldtype"], field["insert_after"], field["default"]),
            ("custom_probability_by_hand", "Check", "probability", "0"),
        )

    def test_the_seed_fills_an_empty_table_with_nik_s_numbers(self):
        install({}, FakeDB(rows=[]))
        self.assertEqual(seed_patch.seed(), 4)
        self.assertEqual({r["stage"]: r["probability"] for r in STATE["inserted"]}, DEFAULTS)
        self.assertEqual({r["parent"] for r in STATE["inserted"]}, {"Sales Activity Settings"})
        self.assertEqual({r["parentfield"] for r in STATE["inserted"]}, {"stage_probabilities"})
        self.assertEqual([r["idx"] for r in STATE["inserted"]], [1, 2, 3, 4])
        self.assertIn(("Sales Activity Settings", "Sales Activity Settings"), STATE["cleared"])

    def test_the_seed_leaves_rows_someone_entered(self):
        install({}, FakeDB(rows=[{"stage": "Qualification", "probability": 5}]))
        self.assertEqual(seed_patch.seed(), 0)
        self.assertEqual(STATE["inserted"], [])

    def test_the_seed_never_saves_the_single(self):
        source = (APP / "patches" / "seed_opportunity_stage_probabilities.py").read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn(".save(", code)
        self.assertNotIn("set_single_value", code)
        self.assertNotIn("get_single(", code)

    def _prod_like(self, has_field=True):
        deals = [
            {"name": "Q-1", "status": "Qualification", "probability": 100},
            {"name": "Q-2", "status": "Qualification", "probability": 45},
            {"name": "N-1", "status": "Needs Analysis", "probability": 100},
            {"name": "P-1", "status": "Proposal/Price Quote", "probability": 100},
            {"name": "R-1", "status": "Negotiation/Review", "probability": 100},
            {"name": "H-1", "status": "On Hold", "probability": 100},
            {"name": "H-2", "status": "On Hold", "probability": 50},
            {"name": "W-1", "status": "Closed Won", "probability": 100},
            {"name": "L-1", "status": "Lost", "probability": 30},
        ]
        return FakeDB(deals=deals, has_field=has_field)

    def test_the_backfill_follows_the_approved_dry_run(self):
        db = self._prod_like()
        install(DEFAULTS, db)
        self.assertEqual(backfill_patch.backfill(), {"moved": 4, "marked": 2})
        probability = {name: d["probability"] for name, d in db.deals.items()}
        self.assertEqual(
            probability,
            {"Q-1": 10, "Q-2": 45, "N-1": 25, "P-1": 50, "R-1": 75, "H-1": 100, "H-2": 50, "W-1": 100, "L-1": 30},
        )
        marked = {name for name, d in db.deals.items() if d.get("custom_probability_by_hand")}
        self.assertEqual(marked, {"Q-2", "H-2"})
        self.assertEqual({w[3] for w in db.writes}, {False}, "update_modified=False")

    def test_the_backfill_is_safe_twice(self):
        db = self._prod_like()
        install(DEFAULTS, db)
        backfill_patch.backfill()
        writes = [w for w in db.writes if w[1] == "probability"]
        backfill_patch.backfill()
        self.assertEqual([w for w in db.writes if w[1] == "probability"], writes, "no deal moves twice")

    def test_the_backfill_with_no_table_changes_nothing(self):
        db = self._prod_like()
        install({}, db)
        self.assertEqual(backfill_patch.backfill(), {"moved": 0, "marked": 0})
        self.assertEqual(db.writes, [])

    def test_the_backfill_without_the_field_still_moves_but_marks_nothing(self):
        db = self._prod_like(has_field=False)
        install(DEFAULTS, db)
        self.assertEqual(backfill_patch.backfill(), {"moved": 4, "marked": 0})

    def test_no_patch_can_raise(self):
        frappe = install(DEFAULTS)
        frappe.get_all = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        frappe.db.table_exists = lambda dt: (_ for _ in ()).throw(RuntimeError("boom"))
        sys.modules["frappe.custom.doctype.custom_field.custom_field"].create_custom_fields = (
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        try:
            for patch in (add_patch, seed_patch, backfill_patch):
                with self.subTest(patch=patch.__name__):
                    patch.execute()
        finally:
            sys.modules["frappe.custom.doctype.custom_field.custom_field"].create_custom_fields = (
                lambda spec, update=False: STATE["created"].append((spec, update))
            )
        self.assertEqual(len(STATE["logged"]), 3)


class TestSettings(unittest.TestCase):
    def make(self, rows):
        return settings_module.SalesActivitySettings(
            stage_probabilities=[_Dict(idx=i, stage=s, probability=p) for i, (s, p) in enumerate(rows, 1)]
        )

    def test_one_row_per_stage(self):
        with self.assertRaises(ValidationError):
            self.make([("Qualification", 10), ("Qualification", 20)]).validate()

    def test_a_probability_is_between_0_and_100(self):
        with self.assertRaises(ValidationError):
            self.make([("Qualification", 120)]).validate()
        self.make([("Qualification", 0), ("Negotiation/Review", 100)]).validate()


class TestWiring(unittest.TestCase):
    def test_nik_s_numbers(self):
        self.assertEqual(prob.DEFAULTS, DEFAULTS)
        self.assertNotIn("On Hold", prob.DEFAULTS, "On Hold stays out of the forecast")

    def test_the_hook_runs_on_opportunity_validate(self):
        hooks = (APP / "hooks.py").read_text(encoding="utf-8")
        block = hooks[hooks.index('doc_events = {') :]
        opportunity = block[block.index('"Opportunity": {') :]
        start = opportunity.index('"validate": [')
        validate = opportunity[start : opportunity.index("\n\t\t],", start)]
        self.assertIn('"erpnext_enhancements.crm_enhancements.opportunity_probability.apply_stage_probability"', validate)

    def test_the_patches_run_field_then_seed_then_backfill(self):
        lines = [l.strip() for l in (APP / "patches.txt").read_text(encoding="utf-8").splitlines()]
        order = [
            lines.index("erpnext_enhancements.patches.add_opportunity_probability_by_hand"),
            lines.index("erpnext_enhancements.patches.seed_opportunity_stage_probabilities"),
            lines.index("erpnext_enhancements.patches.backfill_opportunity_probability"),
        ]
        self.assertEqual(order, sorted(order))
        self.assertGreater(order[0], lines.index("[post_model_sync]"), "after the child doctype syncs")

    def test_the_field_sits_after_probability_on_the_form(self):
        setters = json.loads((APP / "fixtures" / "property_setter.json").read_text(encoding="utf-8"))
        (field_order,) = [s for s in setters if s.get("name") == "Opportunity-main-field_order"]
        order = json.loads(field_order["value"])
        self.assertEqual(order[order.index("probability") + 1], "custom_probability_by_hand")

    def test_the_field_is_code_owned_not_a_fixture(self):
        customs = json.loads((APP / "fixtures" / "custom_field.json").read_text(encoding="utf-8"))
        self.assertFalse([c for c in customs if c.get("fieldname") == "custom_probability_by_hand"])

    def test_the_row_stages_are_the_opportunity_statuses(self):
        setters = json.loads((APP / "fixtures" / "property_setter.json").read_text(encoding="utf-8"))
        (status,) = [
            s for s in setters
            if s.get("doc_type") == "Opportunity" and s.get("field_name") == "status" and s.get("property") == "options"
        ]
        row = json.loads(
            (APP / "crm_enhancements" / "doctype" / "opportunity_stage_probability" / "opportunity_stage_probability.json")
            .read_text(encoding="utf-8")
        )
        (stage,) = [f for f in row["fields"] if f["fieldname"] == "stage"]
        self.assertEqual(set(stage["options"].split("\n")), set(status["value"].split("\n")))
        self.assertTrue(set(DEFAULTS) <= set(stage["options"].split("\n")))


if __name__ == "__main__":
    unittest.main()
