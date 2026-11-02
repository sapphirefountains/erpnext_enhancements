# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Phase H of the Fountain Designer rebuild (v1.771.0): what was retired stays retired.

The rebuild's JSON design model replaced nine child DocTypes of Water Feature Design, and the
Fountain Designer page replaced the Water Engineering Wizard. This suite is the guard on both:

* **The sweep.** No code path names the nine DocTypes or the wizard any more: every ``.py``,
  ``.js``, ``.json`` and ``.html`` under the app and ``scripts/``, read with comments and docstrings
  stripped (the comments that say what was removed and why are the point of the change, not
  residue; ``test_chat_module_retirement`` explains the trap). The only files allowed to name them
  are the two patches that remove them, the conversion patch that read their rows before, the
  redirect, and this file.
* **The survivors.** The DocTypes the plan keeps (PLAN.md 3.2) are all still on disk, and Water
  Feature Design declares no table of a retired DocType.
* **The wizard.** Its folder is gone, neither workspace links it, the Design Hub shortcut is the
  Fountain Designer's, and ``/desk/water-engineering-wizard`` is sent on through ``frappe.re_route``.
* **The patches, dry.** ``retire_water_feature_child_doctypes`` against an in-memory site shaped
  like production on 2026-11-01 (13 rows, every one a child of the schema-2 WFD-2026-209315): it
  drops all nine tables and DocTypes, and never raises. Against a site with an unconverted design it
  keeps that design's rows and the table, deletes the rest, and says so in the Error Log. A failing
  DocType does not stop the others; a second run finds nothing to do. ``report()`` changes nothing.
  ``retire_water_engineering_wizard`` repoints the sidebar row production has and never raises.

Bench-free: filesystem, ``ast`` and stand-ins for frappe.

Run: python -m unittest erpnext_enhancements.tests.test_water_engineering_retirement -v
"""

import ast
import json
import re
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

WE = APP / "water_engineering"
DOCTYPES = WE / "doctype"
PATCHES_TXT = APP / "patches.txt"

RETIRED = (
    "Water Feature Basin",
    "Water Feature Tier",
    "Water Feature Nozzle",
    "Water Feature Pipe Segment",
    "Water Feature Pump",
    "Water Feature Electrical Load",
    "Water Venue Fixture",
    "Water Feature Equipment",
    "Water Feature Calc Result",
)
KEPT = (
    "Water Feature Design",
    "Water Feature Design Line",
    "Water Design Issue Ack",
    "Nozzle Profile",
    "Nozzle Performance Point",
    "Pump Curve",
    "Pump Curve Point",
    "Standard Note",
    "Standard Detail",
    "Submittal Revision",
    "Water Engineering Settings",
    "Control Panel Design",
    "Control Fuse",
    "Control Interlock",
    "Control IO Point",
    "Control Light",
    "Control Pump",
)


def _scrub(name):
    return name.lower().replace(" ", "_")


RETIRED_PATTERN = re.compile(
    "|".join([re.escape(n) for n in RETIRED] + [rf"\b{re.escape(_scrub(n))}\b" for n in RETIRED])
)
WIZARD_PATTERN = re.compile(r"water[-_]engineering[-_]wizard|Water Engineering Wizard")

#: The files that may name what was retired, because removing it (or having read it) is their job.
ALLOWED = {
    "patches/retire_water_feature_child_doctypes.py",
    "patches/convert_water_feature_designs_to_model.py",
    "patches/retire_water_engineering_wizard.py",
    "public/js/global_enhancements/retired_routes.js",
    "tests/test_water_engineering_retirement.py",
}


def _js(text):
    """JS/CSS/HTML with whole-line and block comments removed, and trailing // comments."""
    out, in_block = [], False
    for line in text.splitlines():
        stripped = line.strip()
        if in_block:
            if "*/" in stripped or "-->" in stripped:
                in_block = False
            continue
        if stripped.startswith("/*"):
            in_block = "*/" not in stripped
            continue
        if stripped.startswith("<!--"):
            in_block = "-->" not in stripped
            continue
        if stripped.startswith("//"):
            continue
        out.append(re.sub(r"\s//\s.*$", "", line))
    return "\n".join(out)


def _py(text):
    """Python with every docstring removed (comments vanish in ``ast`` for free)."""
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


def _code_files():
    roots = [APP, REPO / "scripts"]
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in (".py", ".js", ".mjs", ".json", ".html"):
                continue
            if "node_modules" in path.parts or "__pycache__" in path.parts:
                continue
            yield path


def _code(path, pattern=None):
    text = path.read_text(encoding="utf-8", errors="replace")
    if pattern is not None and not pattern.search(text):
        return ""  # cannot match once comments are stripped either; skip the parse
    if path.suffix == ".py":
        return _py(text)
    if path.suffix == ".json":
        return text
    return _js(text)


def _rel(path):
    try:
        return path.relative_to(APP).as_posix()
    except ValueError:
        return path.relative_to(REPO).as_posix()


class TestTheSweep(unittest.TestCase):
    def test_no_code_path_names_a_retired_doctype(self):
        hits = sorted(
            _rel(p) for p in _code_files() if _rel(p) not in ALLOWED and RETIRED_PATTERN.search(_code(p, RETIRED_PATTERN))
        )
        self.assertEqual(hits, [], "still naming a retired DocType (comments and docstrings excluded)")

    def test_no_code_path_names_the_wizard(self):
        hits = sorted(_rel(p) for p in _code_files() if _rel(p) not in ALLOWED and WIZARD_PATTERN.search(_code(p, WIZARD_PATTERN)))
        self.assertEqual(hits, [], "still naming the Water Engineering Wizard")

    def test_the_sweep_would_have_caught_them(self):
        """Guard the guard: the patterns match the shapes the old code used."""
        for sample in ('"options": "Water Feature Pump"', "doctype/water_feature_calc_result/", 'frappe.get_all("Water Venue Fixture")'):
            self.assertTrue(RETIRED_PATTERN.search(sample), sample)
        self.assertFalse(RETIRED_PATTERN.search("Water Feature Design Line"), "a survivor is not a match")
        self.assertTrue(WIZARD_PATTERN.search('frappe.set_route("water-engineering-wizard")'))


class TestTheSurvivors(unittest.TestCase):
    def test_the_retired_folders_are_gone_and_the_kept_doctypes_are_not(self):
        self.assertEqual([n for n in RETIRED if (DOCTYPES / _scrub(n)).exists()], [])
        for name in KEPT:
            with self.subTest(doctype=name):
                path = DOCTYPES / _scrub(name) / f"{_scrub(name)}.json"
                self.assertTrue(path.exists(), path)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["name"], name)

    def test_the_design_declares_no_retired_table(self):
        meta = json.loads((DOCTYPES / "water_feature_design" / "water_feature_design.json").read_text(encoding="utf-8"))
        tables = {f.get("options") for f in meta["fields"] if f.get("fieldtype") in ("Table", "Table MultiSelect")}
        self.assertIn("Water Feature Design Line", tables)
        self.assertFalse(tables & set(RETIRED))

    def test_the_schema_1_producers_are_gone(self):
        issues = (WE / "issues.py").read_text(encoding="utf-8")
        for name in ("def build_issues", "def build_readiness", "def we_fitting_schedule", "_calc_result_issues"):
            self.assertNotIn(name, issues)
        hooks = (APP / "hooks.py").read_text(encoding="utf-8")
        self.assertNotIn('"erpnext_enhancements.water_engineering.issues.we_fitting_schedule"', hooks)
        self.assertIn('"erpnext_enhancements.water_engineering.issues.we_design_issues"', hooks)


class TestTheWizardIsRetired(unittest.TestCase):
    def test_its_page_is_gone(self):
        self.assertFalse((WE / "page" / "water_engineering_wizard").exists())

    def test_no_workspace_links_it_and_the_hub_links_the_designer(self):
        for path in (WE / "workspace" / "water_engineering" / "water_engineering.json",
                     APP / "enhancements_core" / "workspace" / "design_hub" / "design_hub.json"):
            with self.subTest(path.name):
                ws = json.loads(path.read_text(encoding="utf-8"))
                self.assertNotRegex(json.dumps(ws), WIZARD_PATTERN)
                self.assertIn("fountain-designer", [s.get("link_to") for s in ws.get("shortcuts") or []])
                names = {b["data"].get("shortcut_name") for b in json.loads(ws["content"]) if b.get("type") == "shortcut"}
                labels = {s.get("label") for s in ws["shortcuts"]}
                self.assertLessEqual(names, labels, "every shortcut block names a shortcut the workspace has")

    def test_the_old_address_is_sent_on_to_the_designer(self):
        routes = (APP / "public" / "js" / "global_enhancements" / "retired_routes.js").read_text(encoding="utf-8")
        self.assertRegex(_js(routes), r'"water-engineering-wizard":\s*"fountain-designer"')
        self.assertIn("frappe.re_route", _js(routes))
        bundle = (APP / "public" / "js" / "erpnext_enhancements.bundle.js").read_text(encoding="utf-8")
        self.assertIn('import "./global_enhancements/retired_routes.js";', bundle)

    def test_its_calculator_is_the_designers_drawer(self):
        review = (APP / "public" / "js" / "fountain_designer" / "steps" / "review.js").read_text(encoding="utf-8")
        self.assertIn("calculator.render(ctx)", _js(review))
        designer = _py((WE / "api" / "designer.py").read_text(encoding="utf-8"))
        self.assertIn("'calculator': calc_mod.catalog()", designer)
        self.assertIn("def run_calc(", designer)


class TestThePatchesAreRegistered(unittest.TestCase):
    def test_post_model_sync_after_the_conversion(self):
        lines = [ln.strip() for ln in PATCHES_TXT.read_text(encoding="utf-8").splitlines()]
        convert = lines.index("erpnext_enhancements.patches.convert_water_feature_designs_to_model")
        for entry in ("retire_water_feature_child_doctypes", "retire_water_engineering_wizard"):
            with self.subTest(entry):
                at = lines.index(f"erpnext_enhancements.patches.{entry}")
                self.assertGreater(at, lines.index("[post_model_sync]"))
                self.assertGreater(at, convert, "the rows are converted before their tables go")


# --------------------------------------------------------------------- the drop patch, dry


class FakeSite:
    """The handful of frappe.db calls the drop patch makes, over in-memory tables."""

    DESIGN = "Water Feature Design"

    def __init__(self, tables, designs, doctypes=None, raise_for=(), ddl_fails=(), delete_doc_fails=()):
        self.tables = {dt: [dict(r) for r in rows] for dt, rows in tables.items()}
        self.designs = dict(designs)
        self.doctypes = set(RETIRED if doctypes is None else doctypes)
        self.raise_for = set(raise_for)
        self.ddl_fails, self.delete_doc_fails = set(ddl_fails), set(delete_doc_fails)
        self.errors, self.printed, self.ddl, self.deleted_doctypes, self.cleared = [], [], [], [], []

    def frappe(self):
        site = self
        db = types.SimpleNamespace()

        def table_exists(doctype, cached=True):
            return doctype in site.tables

        def sql(query, values=None, as_dict=False):
            table = re.search(r"`tab([^`]+)`", query).group(1)
            if table in site.raise_for:
                raise RuntimeError(f"lock wait timeout on {table}")
            if query.startswith("select parent, parenttype, count(*)"):
                groups = {}
                for r in site.tables[table]:
                    key = (r.get("parent"), r.get("parenttype"))
                    groups[key] = groups.get(key, 0) + 1
                return [{"parent": p, "parenttype": t, "n": n} for (p, t), n in groups.items()]
            if query.startswith("delete from") and "coalesce(parent" in query:
                site.tables[table] = [r for r in site.tables[table] if r.get("parent")]
                return []
            if query.startswith("delete from"):
                doomed = set(values["parents"])
                site.tables[table] = [r for r in site.tables[table] if r.get("parent") not in doomed]
                return []
            raise AssertionError(query)

        def sql_ddl(query):
            table = re.search(r"`tab([^`]+)`", query).group(1)
            if table in site.ddl_fails:
                raise RuntimeError(f"cannot drop tab{table}")
            site.ddl.append(query)
            site.tables.pop(table, None)

        def exists(doctype, name=None):
            if doctype == "DocType":
                return name in site.doctypes
            if doctype == site.DESIGN:
                return name in site.designs
            raise AssertionError(doctype)

        def get_value(doctype, name, field):
            assert (doctype, field) == (site.DESIGN, "model_json")
            return site.designs.get(name)

        def delete(doctype, filters):
            site.cleared.append((doctype, filters))

        db.table_exists, db.sql, db.sql_ddl, db.exists, db.get_value, db.delete = (
            table_exists, sql, sql_ddl, exists, get_value, delete)
        db.commit = lambda: None

        def delete_doc(doctype, name, force=False, ignore_missing=False):
            assert doctype == "DocType" and force and ignore_missing
            if name in site.delete_doc_fails:
                raise RuntimeError(f"cannot delete DocType {name}")
            site.doctypes.discard(name)
            site.deleted_doctypes.append(name)

        fr = types.ModuleType("frappe")
        fr.db = db
        fr.delete_doc = delete_doc
        fr.log_error = lambda title=None, message=None: site.errors.append((title, message))
        fr.get_traceback = lambda: "Traceback"
        fr.clear_cache = lambda: None
        return fr


SCHEMA_2 = json.dumps({"schema": 2, "features": [], "basins": []})
SCHEMA_1 = json.dumps({"basins": [{"shape": "Rectangular"}], "pipe_segments": []})


def _rows(parent, n, parenttype="Water Feature Design"):
    return [{"parent": parent, "parenttype": parenttype} for _ in range(n)]


def production():
    """Production on 2026-11-01, read-only through the ERPNext MCP: all nine tables, 13 rows."""
    counts = {"Water Feature Basin": 1, "Water Feature Nozzle": 1, "Water Feature Pipe Segment": 1,
              "Water Feature Pump": 2, "Water Feature Calc Result": 8}
    tables = {dt: _rows("WFD-2026-209315", counts.get(dt, 0)) for dt in RETIRED}
    return FakeSite(tables, {"WFD-2026-209315": SCHEMA_2})


class TestTheDropPatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.modules.setdefault("frappe", types.ModuleType("frappe"))
        from erpnext_enhancements.patches import retire_water_feature_child_doctypes as patch

        cls.patch = patch

    def _on(self, site):
        p = mock.patch.object(self.patch, "frappe", site.frappe())
        p.start()
        self.addCleanup(p.stop)
        out = mock.patch("builtins.print", lambda *a, **k: site.printed.append(" ".join(str(x) for x in a)))
        out.start()
        self.addCleanup(out.stop)
        return site

    def test_it_lists_the_nine_doctypes(self):
        self.assertEqual(self.patch.RETIRED, RETIRED)

    def test_the_dry_run_on_production_changes_nothing_and_reports_13_rows(self):
        site = self._on(production())
        report = {r["doctype"]: r for r in self.patch.report()}
        self.assertEqual(sum(r["rows"] for r in report.values()), 13)
        self.assertEqual({k: v["rows"] for k, v in report.items() if v["rows"]}, {
            "Water Feature Basin": 1, "Water Feature Nozzle": 1, "Water Feature Pipe Segment": 1,
            "Water Feature Pump": 2, "Water Feature Calc Result": 8})
        self.assertTrue(all(r["table"] and r["drop_table"] and not r["keep_rows"] for r in report.values()))
        self.assertEqual(report["Water Feature Pump"]["parents"], {"WFD-2026-209315": "converted"})
        self.assertEqual((site.ddl, site.deleted_doctypes, site.cleared), ([], [], []), "a dry run is dry")
        self.assertEqual(sum(len(rows) for rows in site.tables.values()), 13)

    def test_on_production_it_drops_all_nine_and_says_so(self):
        site = self._on(production())
        self.patch.execute()
        self.assertEqual(site.tables, {})
        self.assertEqual(sorted(site.deleted_doctypes), sorted(RETIRED))
        self.assertEqual(len(site.ddl), 9)
        self.assertEqual(site.errors, [])
        self.assertEqual(len([ln for ln in site.printed if "dropped its table" in ln]), 9)
        self.assertIn(("Custom Field", {"dt": "Water Feature Pump"}), site.cleared)
        self.assertIn(("Property Setter", {"doc_type": "Water Feature Pump"}), site.cleared)

    def test_an_unconverted_design_keeps_its_rows_and_the_table(self):
        pump = _rows("WFD-NEW", 2) + _rows("WFD-OLD", 3) + _rows("WFD-GONE", 1) + _rows("CPD-1", 1, "Control Panel Design")
        site = self._on(FakeSite({"Water Feature Pump": pump}, {"WFD-NEW": SCHEMA_2, "WFD-OLD": SCHEMA_1},
                                 doctypes={"Water Feature Pump"}))
        plan = self.patch.plan("Water Feature Pump")
        self.assertEqual(plan["parents"], {"WFD-NEW": "converted", "WFD-OLD": "unconverted", "WFD-GONE": "orphan",
                                           "CPD-1": "unconverted"})
        self.assertEqual((plan["rows"], plan["delete_rows"], plan["keep_rows"], plan["drop_table"]), (7, 3, 4, False))
        self.patch.execute()
        self.assertEqual(sorted(r["parent"] for r in site.tables["Water Feature Pump"]), ["CPD-1"] + ["WFD-OLD"] * 3)
        self.assertEqual(site.ddl, [], "a table holding the only copy of a design is never dropped")
        self.assertEqual(site.deleted_doctypes, [], "a kept table keeps its DocType")
        self.assertNotIn(("Custom Field", {"dt": "Water Feature Pump"}), site.cleared, "and its customizations")
        self.assertEqual(len(site.errors), 1)
        self.assertIn("WFD-OLD", site.errors[0][1])
        self.assertIn("KEPT the table with 4", " ".join(site.printed))
        self.assertIn("DocType kept with its table", " ".join(site.printed))
        self.assertIn("convert_water_feature_designs_to_model", site.errors[0][1])

    def test_the_tables_the_conversion_never_read_keep_any_rows(self):
        """Electrical Load, Venue Fixture and Equipment were never copied into a model: even a
        schema-2 design's rows stay, with the table and the DocType; an empty one still goes."""
        site = self._on(FakeSite({"Water Feature Equipment": _rows("WFD-NEW", 2) + _rows("WFD-GONE", 1),
                                  "Water Venue Fixture": []},
                                 {"WFD-NEW": SCHEMA_2}, doctypes={"Water Feature Equipment", "Water Venue Fixture"}))
        plan = self.patch.plan("Water Feature Equipment")
        self.assertEqual((plan["parents"], plan["keep_rows"], plan["drop_table"]),
                         ({"WFD-NEW": "not copied", "WFD-GONE": "orphan"}, 2, False))
        self.patch.execute()
        self.assertEqual([r["parent"] for r in site.tables["Water Feature Equipment"]], ["WFD-NEW"] * 2,
                         "its orphan goes; a live design's rows stay")
        self.assertNotIn("Water Venue Fixture", site.tables)
        self.assertEqual(site.deleted_doctypes, ["Water Venue Fixture"])
        self.assertIn("never copied", site.errors[0][1])
        self.assertIn("then delete those rows", site.errors[0][1])

    def test_a_failed_drop_keeps_the_doctype_and_the_rest_still_go(self):
        site = production()
        site.ddl_fails = {"Water Feature Pump"}
        self._on(site)
        self.patch.execute()  # must not raise
        self.assertEqual(list(site.tables), ["Water Feature Pump"])
        self.assertEqual(sorted(site.deleted_doctypes), sorted(set(RETIRED) - {"Water Feature Pump"}))
        self.assertEqual(len(site.errors), 1)
        site.ddl_fails = set()
        self.patch.execute()
        self.assertEqual((site.tables, len(site.deleted_doctypes)), ({}, 9), "a re-run finishes it")

    def test_a_failed_delete_doc_after_the_drop_is_logged_and_a_rerun_finishes(self):
        site = production()
        site.delete_doc_fails = {"Water Feature Basin"}
        self._on(site)
        self.patch.execute()  # must not raise
        self.assertEqual(site.tables, {})
        self.assertIn("Water Feature Basin", site.doctypes)
        self.assertEqual(len(site.errors), 1)
        site.delete_doc_fails = set()
        self.patch.execute()
        self.assertEqual(site.doctypes, set())

    def test_rows_with_no_parent_go_and_a_shared_name_is_counted_once(self):
        rows = _rows("WFD-OLD", 2) + [{"parent": None, "parenttype": None}] + _rows("WFD-OLD", 1, "Control Panel Design")
        site = self._on(FakeSite({"Water Feature Tier": rows}, {"WFD-OLD": SCHEMA_2}, doctypes={"Water Feature Tier"}))
        plan = self.patch.plan("Water Feature Tier")
        self.assertEqual(plan["parents"], {"WFD-OLD": "unconverted", "": "orphan"})
        self.assertEqual((plan["delete_rows"], plan["keep_rows"]), (1, 3), "every row of WFD-OLD is kept")
        self.patch.execute()
        self.assertEqual([r["parent"] for r in site.tables["Water Feature Tier"]], ["WFD-OLD"] * 3)

    def test_a_design_with_no_model_is_unconverted(self):
        site = self._on(FakeSite({"Water Feature Basin": _rows("WFD-1", 1)}, {"WFD-1": None}))
        self.assertEqual(self.patch.plan("Water Feature Basin")["parents"], {"WFD-1": "unconverted"})
        self.assertEqual(site.ddl, [])

    def test_a_failing_doctype_never_raises_and_the_others_still_go(self):
        site = production()
        site.raise_for = {"Water Feature Pump"}
        self._on(site)
        self.patch.execute()  # must not raise: a raising patch aborts the deploy
        self.assertEqual(list(site.tables), ["Water Feature Pump"])
        self.assertEqual(len(site.deleted_doctypes), 8)
        self.assertEqual(len(site.errors), 1)
        self.assertIn("Water Feature Pump", site.errors[0][1])

    def test_a_second_run_finds_nothing(self):
        site = self._on(production())
        self.patch.execute()
        site.printed.clear()
        self.patch.execute()
        self.assertEqual(len(site.ddl), 9, "nothing more dropped")
        self.assertEqual(len(site.printed), 9)
        self.assertTrue(all("no table; DocType already gone" in ln for ln in site.printed))
        self.assertEqual(site.errors, [])


class TestTheConversionReadsATableWithNoDocType(unittest.TestCase):
    """``remove_orphan_doctypes`` deletes a retired DocType in the same migrate, so the rows the drop
    patch kept for an unconverted design must stay convertible: ``_rows`` reads them with SQL."""

    def test_rows_are_read_with_sql_when_the_doctype_is_gone(self):
        utils = types.ModuleType("frappe.utils")
        utils.cint = lambda v=0: int(float(v or 0))
        utils.flt = lambda v=0, precision=None: float(v or 0)
        with mock.patch.dict(sys.modules, {"frappe": sys.modules.get("frappe") or types.ModuleType("frappe"),
                                           "frappe.utils": sys.modules.get("frappe.utils") or utils}):
            sys.modules.pop("erpnext_enhancements.patches.convert_water_feature_designs_to_model", None)
            from erpnext_enhancements.patches import convert_water_feature_designs_to_model as conv

        calls = []
        fr = types.ModuleType("frappe")
        fr.db = types.SimpleNamespace(
            table_exists=lambda child: True,
            exists=lambda doctype, name=None: False,
            sql=lambda q, values=None, as_dict=False: calls.append((q, values)) or [{"segment_label": "S"}],
        )
        fr.get_all = lambda *a, **k: (_ for _ in ()).throw(AssertionError("get_all needs the DocType"))
        with mock.patch.object(conv, "frappe", fr):
            rows = conv._rows("WFD-OLD", "pipe_segments", "Water Feature Pipe Segment")
        self.assertEqual(rows, [{"segment_label": "S"}])
        self.assertIn("`tabWater Feature Pipe Segment`", calls[0][0])
        self.assertEqual(calls[0][1], ("WFD-OLD", "Water Feature Design", "pipe_segments"))


# ------------------------------------------------------------------ the wizard patch, dry


class TestTheWizardPatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.modules.setdefault("frappe", types.ModuleType("frappe"))
        from erpnext_enhancements.patches import retire_water_engineering_wizard as patch

        cls.patch = patch

    def _site(self, sidebar, import_fails=False):
        calls = {"imported": [], "deleted": [], "set": [], "errors": []}
        rows = {"Workspace Sidebar Item": sidebar, "Workspace Link": [], "Workspace Shortcut": []}

        def import_file_by_path(path, force=False, reset_permissions=True):
            if import_fails:
                raise RuntimeError("import failed")
            calls["imported"].append(Path(path).name)

        fr = types.ModuleType("frappe")
        fr.get_app_path = lambda app, *parts: str(APP.joinpath(*parts))
        fr.db = types.SimpleNamespace(
            exists=lambda doctype, name=None: doctype == "Page",
            table_exists=lambda doctype: True,
            has_column=lambda doctype, column: True,
            set_value=lambda dt, name, values, update_modified=True: calls["set"].append((dt, name, values)),
        )

        def get_all(doctype, filters=None, fields=None, pluck=None):
            if doctype == "Desktop Icon":
                return []
            return [r for r in rows[doctype] if r.get("link_to") == filters["link_to"]]

        fr.get_all = get_all
        fr.delete_doc = lambda doctype, name, **k: calls["deleted"].append((doctype, name))
        fr.log_error = lambda title=None, message=None: calls["errors"].append(message)
        fr.get_traceback = lambda: "Traceback"
        fr.clear_cache = lambda: None
        import_file = types.ModuleType("frappe.modules.import_file")
        import_file.import_file_by_path = import_file_by_path
        modules = types.ModuleType("frappe.modules")
        modules.import_file = import_file
        for p in (mock.patch.object(self.patch, "frappe", fr),
                  mock.patch.dict(sys.modules, {"frappe.modules": modules, "frappe.modules.import_file": import_file}),
                  mock.patch("builtins.print", lambda *a, **k: None)):
            p.start()
            self.addCleanup(p.stop)
        return calls

    def test_on_production_it_reimports_deletes_the_page_and_repoints_the_sidebar_row(self):
        calls = self._site([{"name": "vuajoo3ps8", "label": "Water Engineering Wizard", "link_to": "water-engineering-wizard"}])
        self.patch.execute()
        self.assertEqual(calls["imported"], ["water_engineering.json", "design_hub.json"])
        self.assertEqual(calls["deleted"], [("Page", "water-engineering-wizard")])
        self.assertEqual(calls["set"], [("Workspace Sidebar Item", "vuajoo3ps8",
                                         {"link_to": "fountain-designer", "label": "Fountain Designer"})])
        self.assertEqual(calls["errors"], [])

    def test_a_failed_import_is_logged_and_the_rest_still_runs(self):
        calls = self._site([], import_fails=True)
        self.patch.execute()  # must not raise
        self.assertEqual(len(calls["errors"]), 2)
        self.assertEqual(calls["deleted"], [("Page", "water-engineering-wizard")])


if __name__ == "__main__":
    unittest.main()
