"""Bench-free pytest suite for the job number prefixes (``project_enhancements/job_prefixes.py``, v1.831.0).

Nik, 2026-11-19 Ops Review: new Service jobs are ``SRV-#####``, new Events jobs ``EVT-#####``, the rest
keep ``PRJ-#####``, and no existing Project is renamed. Production names Projects by a Document Naming
Rule (``PRJ-``, priority 0, no conditions), so the change is two more rules with a ``project_type``
condition and a higher priority, seeded by ``patches/seed_job_prefix_naming_rules``.

What fails expensively and quietly here, and so is pinned:

* the seed patch **raising** (it aborts ``bench migrate``, which is the deploy), **re-creating** a rule
  somebody disabled, or starting a counter **at or below a name that exists** (a duplicate-name error
  on the next job, mid-hand-off);
* the patch **touching the PRJ- rule**, its counter, or **renaming** any Project;
* the conditional rules **not outranking** the PRJ- rule (every job stays PRJ- and nothing errors);
* the template fill **overwriting a type somebody chose**, raising, or running **before** the move-job
  hook that fills the template it reads.

A plain pytest module with its own ``frappe`` stub, so CI runs it in a ``python -m pytest`` step of its
own (``python -m unittest`` cannot collect function tests).
"""

import ast
import importlib
import io
import re
import sys
import tokenize
import types
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]


class Doc(dict):
	"""A dict that answers ``get`` and attribute reads and writes, like a Document."""

	def __getattr__(self, name):
		try:
			return self[name]
		except KeyError:
			raise AttributeError(name) from None

	def __setattr__(self, name, value):
		self[name] = value


class FakeRule:
	"""A new Document Naming Rule: records its fields, its condition rows and the insert."""

	def __init__(self, site):
		self._site = site
		self.conditions = []

	def append(self, table, row):
		assert table == "conditions"
		self.conditions.append(dict(row))

	def insert(self, ignore_permissions=False):
		if self._site.get("fail_on") == self.prefix:
			raise RuntimeError("insert failed")
		self._site["inserted"].append(self)


def install_frappe_stub(existing=(), highest=None, fail_on=None, template_types=None):
	"""``existing``: prefixes that already have a rule. ``highest``: {prefix: largest number in use}."""
	frappe = types.ModuleType("frappe")
	site = {"inserted": [], "errors": [], "exists_filters": [], "sql": [], "fail_on": fail_on}
	highest = highest or {}
	template_types = template_types or {}

	def exists(doctype, filters=None, *a, **k):
		site["exists_filters"].append((doctype, dict(filters or {})))
		if doctype == "Document Naming Rule" and (filters or {}).get("prefix") in existing:
			return "hash-" + filters["prefix"]
		return None

	def sql(query, values=None, *a, **k):
		site["sql"].append((query, dict(values or {})))
		prefix = (values or {}).get("pattern", "")[:-1]
		return [[highest.get(prefix)]]

	def get_value(doctype, name, field, *a, **k):
		if doctype == "Project Template" and field == "project_type":
			value = template_types.get(name)
			if isinstance(value, Exception):
				raise value
			return value
		return None

	frappe.db = types.SimpleNamespace(
		exists=exists, sql=sql, get_value=get_value, has_column=lambda *a, **k: True
	)
	frappe.new_doc = lambda doctype: FakeRule(site) if doctype == "Document Naming Rule" else None
	frappe.log_error = lambda title=None, message=None, **k: site["errors"].append(title)
	frappe.get_traceback = lambda *a, **k: "traceback"
	frappe.logger = lambda *a, **k: types.SimpleNamespace(info=lambda *a, **k: None)
	frappe.site = site
	# What the project_enhancements package __init__ (and the modules it imports) read at import time.
	utils = types.ModuleType("frappe.utils")
	utils.flt = lambda value=0, *a, **k: float(value or 0)
	utils.cint = lambda value=0, *a, **k: int(float(value or 0))
	utils.getdate = lambda value=None: value
	utils.nowdate = lambda: "2026-11-19"
	utils.get_fullname = lambda user: user
	frappe.utils = utils
	frappe._ = lambda message=None, *a, **k: message
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.get_all = lambda *a, **k: []
	frappe.get_doc = lambda *a, **k: None
	frappe.has_permission = lambda *a, **k: True
	frappe.get_app_path = lambda *parts: str(APP.joinpath(*parts[1:]))
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	return frappe


def _load(frappe):
	for name in list(sys.modules):
		if name.startswith("erpnext_enhancements.project_enhancements.job_prefixes") or name.startswith(
			"erpnext_enhancements.patches.seed_job_prefix_naming_rules"
		):
			del sys.modules[name]
	jp = importlib.import_module("erpnext_enhancements.project_enhancements.job_prefixes")
	patch = importlib.import_module("erpnext_enhancements.patches.seed_job_prefix_naming_rules")
	jp.frappe = frappe
	patch.frappe = frappe
	return jp, patch


@pytest.fixture()
def fresh():
	frappe = install_frappe_stub()
	jp, patch = _load(frappe)
	return frappe, jp, patch


# ---------------------------------------------------------------------- the rules


def test_the_rules_are_service_and_events_only(fresh):
	_frappe, jp, _patch = fresh
	assert dict(jp.RULES) == {"SRV-": "Service", "EVT-": "Events"}
	assert jp.DEFAULT_PREFIX == "PRJ-"
	assert jp.DIGITS == 5, "the same width as PRJ-#####"
	assert jp.PRIORITY > 0, "the PRJ- rule is priority 0; Frappe tries rules highest priority first"


def test_prefix_for_each_type(fresh):
	_frappe, jp, _patch = fresh
	assert jp.prefix_for("Service") == "SRV-"
	assert jp.prefix_for(" Events ") == "EVT-"
	for other in ("Design", "Build", "Products", "Internal", "Overhead", "", None):
		assert jp.prefix_for(other) == "PRJ-", other


# ---------------------------------------------------------------------- the seed patch


def test_patch_creates_both_rules_with_one_condition_each(fresh):
	frappe, jp, patch = fresh
	patch.execute()
	rules = {r.prefix: r for r in frappe.site["inserted"]}
	assert set(rules) == {"SRV-", "EVT-"}
	for prefix, project_type in jp.RULES:
		rule = rules[prefix]
		assert rule.document_type == "Project"
		assert rule.prefix_digits == 5
		assert rule.priority == jp.PRIORITY
		assert rule.disabled == 0
		assert rule.counter == 0, "no SRV-/EVT- project exists, so the first is 00001"
		assert rule.conditions == [{"field": "project_type", "condition": "=", "value": project_type}]
	assert frappe.site["errors"] == []


def test_patch_leaves_an_existing_or_disabled_rule_alone():
	frappe = install_frappe_stub(existing=("SRV-",))
	_jp, patch = _load(frappe)
	patch.execute()
	assert [r.prefix for r in frappe.site["inserted"]] == ["EVT-"]
	# Keyed on the prefix alone, never on `disabled`: a rule somebody switched off is not re-created.
	for doctype, filters in frappe.site["exists_filters"]:
		assert doctype == "Document Naming Rule"
		assert filters == {"document_type": "Project", "prefix": filters["prefix"]}


def test_patch_is_safe_twice():
	frappe = install_frappe_stub(existing=("SRV-", "EVT-"))
	_jp, patch = _load(frappe)
	patch.execute()
	patch.execute()
	assert frappe.site["inserted"] == []


def test_counter_starts_after_the_highest_existing_name():
	frappe = install_frappe_stub(highest={"SRV-": 7})
	_jp, patch = _load(frappe)
	patch.execute()
	rules = {r.prefix: r for r in frappe.site["inserted"]}
	assert rules["SRV-"].counter == 7, "the next SRV- job is SRV-00008, never a name in use"
	assert rules["EVT-"].counter == 0
	query, values = frappe.site["sql"][0]
	assert "%(pattern)s" in query and "%(start)s" in query, "bound, never built into the SQL"
	assert values == {"start": 5, "pattern": "SRV-%"}


def test_patch_cannot_raise_and_still_seeds_the_other_rule():
	frappe = install_frappe_stub(fail_on="SRV-")
	_jp, patch = _load(frappe)
	patch.execute()  # must not raise: a raising patch aborts bench migrate, which is the deploy
	assert [r.prefix for r in frappe.site["inserted"]] == ["EVT-"]
	assert frappe.site["errors"] == ["Job prefixes: could not seed the SRV- naming rule"]


def _code(path):
	"""Source with comments and docstrings removed, so a mention in prose cannot pass or fail a check."""
	src = path.read_text(encoding="utf-8")
	tree = ast.parse(src)
	docstrings = set()
	for node in ast.walk(tree):
		if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and ast.get_docstring(node) is not None:
			docstrings.add(node.body[0].lineno)
	out = []
	for tok in tokenize.generate_tokens(io.StringIO(src).readline):
		if tok.type == tokenize.COMMENT:
			continue
		if tok.type == tokenize.STRING and tok.start[0] in docstrings:
			continue
		out.append(tok.string)
	return " ".join(out)


def test_patch_never_renames_retypes_or_touches_the_prj_rule():
	code = _code(APP / "patches" / "seed_job_prefix_naming_rules.py")
	for forbidden in ("rename_doc", "rename", "set_value", "db_set", "UPDATE", "DELETE", "delete_doc", "PRJ"):
		assert forbidden not in code, forbidden
	assert "SELECT" in code, "the only SQL is the read of the highest existing number"


# ---------------------------------------------------------------------- the naming order


def _name(rules, counters, doc):
	"""What Frappe v16.56.0 does: enabled rules ``priority desc``; the first whose conditions all
	match names the document (``set_naming_from_document_naming_rule``, ``DocumentNamingRule.apply``).
	Only the ``=`` condition is used here."""
	for rule in sorted(rules, key=lambda r: -r["priority"]):
		if all(doc.get(c["field"]) == c["value"] for c in rule["conditions"]):
			counters[rule["prefix"]] += 1
			return rule["prefix"] + ("%0" + str(rule["digits"]) + "d") % counters[rule["prefix"]]
	return None


def test_service_and_events_jobs_take_their_prefix_and_the_rest_stay_prj(fresh):
	frappe, _jp, patch = fresh
	patch.execute()
	rules = [{"prefix": "PRJ-", "priority": 0, "digits": 5, "conditions": []}]
	counters = {"PRJ-": 787}
	for r in frappe.site["inserted"]:
		rules.append({"prefix": r.prefix, "priority": r.priority, "digits": r.prefix_digits, "conditions": r.conditions})
		counters[r.prefix] = r.counter
	names = [
		_name(rules, counters, {"project_type": t})
		for t in ("Service", "Events", "Build", None, "Design", "Service", "Products")
	]
	assert names == ["SRV-00001", "EVT-00001", "PRJ-00788", "PRJ-00789", "PRJ-00790", "SRV-00002", "PRJ-00791"]


# ---------------------------------------------------------------------- the template fill


def test_a_blank_type_takes_the_templates_type():
	frappe = install_frappe_stub(template_types={"Service Project Template": "Service", "Rent Project Template": "Events"})
	jp, _patch = _load(frappe)
	doc = Doc(project_template="Service Project Template", project_type=None)
	jp.fill_type_from_template(doc)
	assert doc["project_type"] == "Service"
	doc = Doc(project_template="Rent Project Template", project_type="")
	jp.fill_type_from_template(doc)
	assert doc["project_type"] == "Events"


def test_a_chosen_type_is_never_overwritten():
	frappe = install_frappe_stub(template_types={"Service Project Template": "Service"})
	jp, _patch = _load(frappe)
	doc = Doc(project_template="Service Project Template", project_type="Build")
	jp.fill_type_from_template(doc)
	assert doc["project_type"] == "Build"


def test_no_template_or_an_untyped_template_changes_nothing():
	frappe = install_frappe_stub(template_types={"Project Launch": None})
	jp, _patch = _load(frappe)
	for doc in (Doc(project_template=None, project_type=None), Doc(project_template="Project Launch", project_type=None)):
		jp.fill_type_from_template(doc)
		assert doc["project_type"] is None


def test_a_failed_read_never_stops_the_insert():
	frappe = install_frappe_stub(template_types={"Broken": RuntimeError("db gone")})
	jp, _patch = _load(frappe)
	doc = Doc(project_template="Broken", project_type=None)
	jp.fill_type_from_template(doc)  # must not raise
	assert doc["project_type"] is None
	assert frappe.site["errors"] == ["Job prefixes: could not read the project template's type"]


# ---------------------------------------------------------------------- wiring


def _source(*parts):
	return APP.joinpath(*parts).read_text(encoding="utf-8")


def test_the_fill_runs_before_naming_and_after_the_move_job_default():
	block = _source("hooks.py").split('"Project": {', 1)[1].split('"after_insert"', 1)[0]
	fill = "erpnext_enhancements.project_enhancements.job_prefixes.fill_type_from_template"
	move = "erpnext_enhancements.project_enhancements.fountain_move_jobs.apply_on_insert"
	assert fill in block, "before_insert: Frappe names the Project right after these hooks"
	assert block.index(move) < block.index(fill), "the move-job hook may fill the template the fill reads"


def test_the_patch_runs_after_the_prj_rule_is_seeded():
	text = _source("patches.txt")
	lines = [line.strip() for line in text.splitlines()]
	prj = lines.index("erpnext_enhancements.patches.seed_project_naming_rule")
	ours = lines.index("erpnext_enhancements.patches.seed_job_prefix_naming_rules")
	assert prj < ours
	assert lines.index("[post_model_sync]") < ours


def test_the_closed_won_handoff_types_the_project_before_it_inserts():
	"""The hand-off creates most Projects. Its type must be set before ``project.insert`` or every new
	Service job from a deal would be PRJ-."""
	src = _source("crm_enhancements", "api.py")
	body = src.split("def create_project_from_opportunity_background", 1)[1]
	typed = body.index("project.project_type = project_type_value")
	inserted = body.index("project.insert(ignore_permissions=True)")
	assert typed < inserted


def test_service_calls_and_rentals_insert_with_their_type():
	calls = _source("sapphire_maintenance", "service_calls.py")
	assert re.search(r'"project_type":\s*PROJECT_TYPE', calls)
	assert re.search(r'^PROJECT_TYPE\s*=\s*"Service"', calls, re.M)
	rentals = _source("asset_management", "rental_planner.py")
	body = rentals.split("project.project_type = EVENTS", 1)
	assert len(body) == 2 and "project.insert()" in body[1], "typed before the insert"
	assert re.search(r'^EVENTS\s*=\s*"Events"', rentals, re.M)
