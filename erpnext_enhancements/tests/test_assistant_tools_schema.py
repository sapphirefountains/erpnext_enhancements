"""Bench-free contract tests for the FAC assistant tools + skills.

Plain ``unittest`` (pytest-compatible), no Frappe site required. Mirrors the
``test_quickbooks_online.py`` approach: a minimal ``frappe`` plus a stub
``frappe_assistant_core.core.base_tool.BaseTool`` are installed into
``sys.modules`` so the tool modules import cleanly, then every tool declared in
the ``assistant_tools`` hook is instantiated and checked against FAC's tool
contract (name == module filename, no collision with FAC built-ins, valid
inputSchema, ...). hooks.py is read via ``ast`` — importing it for real would
run its monkeypatches, which need a live frappe.

Also enforces the FAC-optional invariant: no module outside assistant_tools/
and tests/ may import assistant_tools or frappe_assistant_core.

Run: python -m pytest erpnext_enhancements/tests/test_assistant_tools_schema.py
"""

import ast
import json
import re
import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]  # the erpnext_enhancements package dir
REPO_ROOT = APP_DIR.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Tool names shipped by FAC's bundled plugins (core, data_science,
# visualization) as of v2.4.3, plus the search/fetch aliases. External tool
# names must not collide with any of these.
FAC_BUILTIN_TOOL_NAMES = {
    "create_document", "get_document", "update_document", "list_documents",
    "delete_document", "submit_document", "search_documents", "search_doctype",
    "search_link", "chatgpt_search", "chatgpt_fetch", "search", "fetch",
    "get_doctype_info", "generate_report", "report_list", "report_requirements",
    "run_workflow", "get_pending_approvals", "run_python_code",
    "analyze_business_data", "run_database_query", "extract_file_content",
    "create_dashboard", "create_dashboard_chart", "list_user_dashboards",
}

SKILL_ID_RE = re.compile(r"^[a-z0-9_-]+$")
SKILL_TYPES = {"Tool Usage", "Workflow"}
SKILL_STATUSES = {"Draft", "Published", "Deprecated"}
SKILL_VISIBILITIES = {"Private", "Shared", "Public"}

# Actual import statements only — dotted strings in hooks.py's hook lists and
# comments must not trip this.
_FORBIDDEN_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+(?:erpnext_enhancements\.assistant_tools|frappe_assistant_core)",
    re.MULTILINE,
)


def install_stubs():
    """Install minimal frappe + frappe_assistant_core stubs into sys.modules.

    Cooperates with test_quickbooks_online.py's stub when both run in one
    pytest session: reuses whatever module object is already registered and
    only adds the attributes the assistant tool modules need at import time.
    """
    frappe = sys.modules.get("frappe") or types.ModuleType("frappe")
    frappe._ = getattr(frappe, "_", None) or (lambda msg, *a, **k: msg)
    frappe_utils = sys.modules.get("frappe.utils") or types.ModuleType("frappe.utils")
    for name in (
        "add_days", "date_diff", "nowdate", "now", "get_datetime",
        "now_datetime", "time_diff_in_seconds",
    ):
        if not hasattr(frappe_utils, name):
            setattr(frappe_utils, name, lambda *a, **k: None)
    frappe.utils = frappe_utils
    sys.modules["frappe"] = frappe
    sys.modules["frappe.utils"] = frappe_utils

    base_tool = sys.modules.get("frappe_assistant_core.core.base_tool") or types.ModuleType(
        "frappe_assistant_core.core.base_tool"
    )
    if not hasattr(base_tool, "BaseTool"):

        class BaseTool:
            """Attribute contract of frappe_assistant_core.core.base_tool.BaseTool."""

            def __init__(self):
                self.name = ""
                self.description = ""
                self.inputSchema = {}
                self.requires_permission = None
                self.category = "Custom"
                self.source_app = "frappe_assistant_core"
                self.dependencies = []
                self.default_config = {}

            def execute(self, arguments):
                raise NotImplementedError

            def get_config(self):
                return dict(self.default_config)

        base_tool.BaseTool = BaseTool

    fac = sys.modules.get("frappe_assistant_core") or types.ModuleType("frappe_assistant_core")
    fac_core = sys.modules.get("frappe_assistant_core.core") or types.ModuleType(
        "frappe_assistant_core.core"
    )
    fac.core = fac_core
    fac_core.base_tool = base_tool
    sys.modules["frappe_assistant_core"] = fac
    sys.modules["frappe_assistant_core.core"] = fac_core
    sys.modules["frappe_assistant_core.core.base_tool"] = base_tool
    return base_tool.BaseTool


def hook_value(name):
    """Extract a top-level literal assignment from hooks.py without importing it."""
    tree = ast.parse((APP_DIR / "hooks.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    return None


class TestAssistantToolsContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.BaseTool = install_stubs()
        cls.tool_paths = hook_value("assistant_tools") or []
        cls.tools = []  # (hook_path, module_name, class, instance)
        for path in cls.tool_paths:
            module_path, class_name = path.rsplit(".", 1)
            module = __import__(module_path, fromlist=[class_name])
            tool_cls = getattr(module, class_name)
            cls.tools.append((path, module_path.rsplit(".", 1)[-1], tool_cls, tool_cls()))

    def test_hook_declares_tools(self):
        self.assertTrue(self.tool_paths, "assistant_tools hook is empty or missing")

    def test_tool_name_matches_module_filename(self):
        # FAC's custom_tools plugin derives tool identifiers from the module
        # path (parts[-2]); a mismatch creates phantom names in its admin UI.
        for path, module_name, _cls, tool in self.tools:
            self.assertEqual(
                tool.name, module_name,
                f"{path}: tool.name {tool.name!r} != module filename {module_name!r}",
            )

    def test_tool_names_unique_and_no_fac_collision(self):
        names = [tool.name for *_rest, tool in self.tools]
        self.assertEqual(len(names), len(set(names)), f"duplicate tool names: {names}")
        collisions = set(names) & FAC_BUILTIN_TOOL_NAMES
        self.assertFalse(collisions, f"names collide with FAC built-ins: {collisions}")

    def test_tool_metadata(self):
        for path, _module_name, _cls, tool in self.tools:
            self.assertIsInstance(tool, self.BaseTool, path)
            self.assertEqual(tool.source_app, "erpnext_enhancements", path)
            self.assertTrue(tool.requires_permission, f"{path}: requires_permission must be set")
            self.assertIsInstance(tool.requires_permission, str, path)
            self.assertGreater(
                len(tool.description or ""), 50,
                f"{path}: description too short to guide an LLM",
            )

    def test_execute_overridden(self):
        for path, _module_name, cls, _tool in self.tools:
            self.assertIn("execute", cls.__dict__, f"{path}: execute() not overridden")

    def test_input_schema_valid(self):
        for path, _module_name, _cls, tool in self.tools:
            schema = tool.inputSchema
            self.assertIsInstance(schema, dict, path)
            self.assertEqual(schema.get("type"), "object", path)
            properties = schema.get("properties", {})
            self.assertIsInstance(properties, dict, path)
            for prop, spec in properties.items():
                self.assertIn("type", spec, f"{path}: property {prop!r} has no type")
                self.assertTrue(
                    spec.get("description") or spec.get("enum"),
                    f"{path}: property {prop!r} needs a description (or enum)",
                )
            for required in schema.get("required", []):
                self.assertIn(
                    required, properties,
                    f"{path}: required field {required!r} missing from properties",
                )

    def test_mutating_tools_advertise_mutation_annotations(self):
        # Device-safety fix (v1.71.0): every mutating app tool advertises MCP
        # ToolAnnotations derived from _gate, so an MCP client (Triton) reads its
        # mutation/risk from tools/list instead of guessing from the verb (which
        # mis-classified the oddly-named device tools as read-only). Enforce it so
        # a future write tool can't silently ship without the metadata.
        from erpnext_enhancements.assistant_tools._gate import APP_MUTATING

        for path, _module_name, _cls, tool in self.tools:
            if tool.name not in APP_MUTATING:
                continue
            ann = getattr(tool, "annotations", None)
            self.assertIsInstance(ann, dict, f"{path}: mutating tool must set .annotations")
            self.assertIs(ann.get("readOnlyHint"), False, f"{path}: annotations.readOnlyHint must be False")
            self.assertIs(ann.get("x-ee-mutation"), True, f"{path}: annotations must mark x-ee-mutation True")
            self.assertIn(
                ann.get("x-ee-risk"), {"low", "medium", "high"},
                f"{path}: annotations.x-ee-risk must be low/medium/high",
            )

    def test_annotations_well_formed(self):
        # Any tool that sets annotations must use a JSON-serialisable dict with a
        # boolean readOnlyHint and a valid risk band (FAC forwards it verbatim).
        for path, _module_name, _cls, tool in self.tools:
            ann = getattr(tool, "annotations", None)
            if ann is None:
                continue
            self.assertIsInstance(ann, dict, path)
            if "readOnlyHint" in ann:
                self.assertIsInstance(ann["readOnlyHint"], bool, path)
            if "x-ee-risk" in ann:
                self.assertIn(ann["x-ee-risk"], {"low", "medium", "high"}, path)


class TestAssistantSkillsManifest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hook_entries = hook_value("assistant_skills") or []

    def test_hook_entry_shape(self):
        self.assertTrue(self.hook_entries, "assistant_skills hook is empty or missing")
        for entry in self.hook_entries:
            self.assertEqual(entry.get("app"), "erpnext_enhancements")
            self.assertTrue(entry.get("manifest"))
            self.assertTrue(entry.get("content_dir"))
            # FAC resolves both relative to frappe.get_app_path(app) == APP_DIR.
            self.assertTrue((APP_DIR / entry["manifest"]).is_file(), entry["manifest"])
            self.assertTrue((APP_DIR / entry["content_dir"]).is_dir(), entry["content_dir"])

    def test_manifest_entries(self):
        for entry in self.hook_entries:
            manifest = json.loads((APP_DIR / entry["manifest"]).read_text(encoding="utf-8"))
            self.assertTrue(manifest, "skills manifest is empty")
            ids = [s.get("skill_id") for s in manifest]
            self.assertEqual(len(ids), len(set(ids)), f"duplicate skill_ids: {ids}")
            for skill in manifest:
                skill_id = skill.get("skill_id") or ""
                self.assertRegex(skill_id, SKILL_ID_RE)
                self.assertTrue(skill.get("title"), skill_id)
                self.assertTrue(skill.get("description"), skill_id)
                self.assertLessEqual(len(skill["description"]), 250, skill_id)
                self.assertIn(skill.get("skill_type"), SKILL_TYPES, skill_id)
                self.assertIn(skill.get("status"), SKILL_STATUSES, skill_id)
                self.assertIn(skill.get("visibility"), SKILL_VISIBILITIES, skill_id)
                content_path = APP_DIR / entry["content_dir"] / skill.get("content_file", "")
                self.assertTrue(content_path.is_file(), f"{skill_id}: missing {content_path}")
                content = content_path.read_text(encoding="utf-8")
                self.assertGreater(len(content), 200, f"{skill_id}: content too thin")
                self.assertLess(len(content), 10_000, f"{skill_id}: content too large")


class TestFacOptionalInvariant(unittest.TestCase):
    def test_no_app_code_imports_assistant_tools_or_fac(self):
        """erpnext_enhancements must keep working on sites without FAC: only
        FAC itself may import assistant_tools/*, and nothing in the app may
        import frappe_assistant_core."""
        offenders = []
        for py_file in APP_DIR.rglob("*.py"):
            relative = py_file.relative_to(APP_DIR)
            if relative.parts[0] in ("assistant_tools", "tests"):
                continue
            if "node_modules" in relative.parts:
                continue
            if _FORBIDDEN_IMPORT_RE.search(py_file.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(str(relative))
        self.assertFalse(
            offenders,
            f"FAC-optional invariant violated — these modules import "
            f"assistant_tools or frappe_assistant_core: {offenders}",
        )


if __name__ == "__main__":
    unittest.main()
