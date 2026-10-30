"""Bench-free pytest suite for the Design project template (``project_enhancements/design_projects.py``).

Nik, 2026-10-30: PRO-0206's four design stages (PD, SD, DD, CD) as four tasks, in order, with rough
lengths; the old "Design Project Template" disabled; the Create Project dialog prefilling the new one
for Design deals.

What fails expensively and quietly here, and so is pinned:

* a seed patch that **raises** (it aborts ``bench migrate``, which is the deploy) or that **re-seeds
  over Nik's edits** on a later migrate;
* the old template **disabled while the new one is missing**, which would leave Design projects with no
  template to pick, or anything about the old template changing **beyond its Disabled box**;
* stage dates that **overlap or leave gaps**, or a stage named like a build phase (``Phase 300``),
  which the planner would then treat as one;
* the dialog prefilling the template for a deal that is **not Design**, or for someone who cannot read
  the Opportunity, or **raising** instead of leaving the field empty;
* the dialog's prefill **beating a Fountain Move intake's** template.

A plain pytest module with its own ``frappe`` stub, so CI runs it in a ``python -m pytest`` step of
its own (``python -m unittest`` cannot collect function tests).
"""

import datetime
import importlib
import itertools
import sys
import types
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]


def _getdate(value=None):
	if value is None:
		return datetime.date(2026, 10, 30)
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	return datetime.date.fromisoformat(str(value)[:10])


def install_frappe_stub():
	"""Enough of frappe for ``project_enhancements/__init__`` (imported with the package) and this module."""
	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")

	def flt(value=0, precision=None):
		try:
			return float(value or 0)
		except (TypeError, ValueError):
			return 0.0

	utils.flt = flt
	utils.cint = lambda value=0, *a, **k: int(flt(value))
	utils.getdate = _getdate
	utils.nowdate = lambda: "2026-10-30"
	utils.get_fullname = lambda user: user
	frappe.utils = utils
	frappe._ = lambda message=None, *a, **k: message
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.db = types.SimpleNamespace(
		has_column=lambda *a, **k: True,
		exists=lambda *a, **k: None,
		get_value=lambda *a, **k: 0,
		set_value=lambda *a, **k: None,
		savepoint=lambda *a, **k: None,
		rollback=lambda *a, **k: None,
	)
	frappe.get_all = lambda *a, **k: []
	frappe.get_doc = lambda *a, **k: None
	frappe.has_permission = lambda *a, **k: True
	frappe.log_error = lambda *a, **k: None
	frappe.get_traceback = lambda *a, **k: "traceback"
	frappe.get_app_path = lambda *parts: str(APP.joinpath(*parts[1:]))
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	return frappe


@pytest.fixture()
def dp():
	frappe = install_frappe_stub()
	for name in list(sys.modules):
		if name.startswith("erpnext_enhancements.project_enhancements.design_projects") or name.startswith(
			"erpnext_enhancements.patches.seed_design_project_template"
		):
			del sys.modules[name]
	module = importlib.import_module("erpnext_enhancements.project_enhancements.design_projects")
	module.frappe = frappe
	return module


def _patch(dp):
	module = importlib.import_module("erpnext_enhancements.patches.seed_design_project_template")
	module.frappe = dp.frappe
	module.design_projects = dp
	return module


class Doc(dict):
	"""A dict that answers attribute reads and ``insert``, like a Document."""

	made = None
	#: When set, a Project Template insert also makes it exist on this Site.
	site = None

	def __getattr__(self, name):
		try:
			return self[name]
		except KeyError:
			raise AttributeError(name) from None

	def insert(self, ignore_permissions=False, set_name=None):
		self["name"] = set_name or f"TASK-{len(self.made) + 1}"
		self.made.append(dict(self))
		if self.get("doctype") == "Project Template" and Doc.site is not None:
			Doc.site.templates[self["name"]] = {"disabled": 0}
		return self


class Site:
	"""Which Project Templates exist and which are disabled; records every field write."""

	def __init__(self, dp, templates):
		self.templates = dict(templates)
		self.writes = []
		dp.frappe.db.exists = self.exists
		dp.frappe.db.get_value = self.get_value
		dp.frappe.db.set_value = self.set_value

	def exists(self, doctype, name=None, *a, **k):
		if doctype == "DocType":
			return True
		if doctype == "Project Type":
			return name == "Design"
		return doctype == "Project Template" and name in self.templates

	def get_value(self, doctype, name, field, *a, **k):
		return self.templates.get(name, {}).get(field)

	def set_value(self, doctype, name, field, value=None, *a, **k):
		self.writes.append((doctype, name, field, value))
		self.templates.setdefault(name, {})[field] = value


# ---------------------------------------------------------------------- the stages


def test_the_stages_are_pro_0206s_four_in_order(dp):
	assert [s["subject"] for s in dp.STAGES] == [
		"Predesign (PD)",
		"Schematic Design (SD)",
		"Design Development (DD)",
		"Construction Documents (CD)",
	]


def test_each_stage_starts_the_day_after_the_one_before_ends(dp):
	assert dp.STAGES[0]["start"] == 0
	for before, stage in itertools.pairwise(dp.STAGES):
		assert stage["start"] == before["start"] + before["duration"] + 1, stage["subject"]
	for stage in dp.STAGES:
		assert isinstance(stage["start"], int) and isinstance(stage["duration"], int)
		assert stage["duration"] > 0


def test_nik_rough_lengths_about_twelve_weeks(dp):
	# PD about 2 weeks, SD 3, DD 4, CD 3 (Nik, 2026-10-30).
	weeks = [round((s["duration"] + 1) / 7) for s in dp.STAGES]
	assert weeks == [2, 3, 4, 3]
	last = dp.STAGES[-1]
	assert last["start"] + last["duration"] == 83


def test_no_stage_reads_as_a_build_phase(dp):
	from erpnext_enhancements.project_enhancements import planner_templates

	for stage in dp.STAGES:
		assert planner_templates.phase_number(stage["subject"]) is None, stage["subject"]
		assert len(stage["subject"]) <= planner_templates.SUBJECT_MAX


def test_template_task_values_have_no_hours_and_point_at_pro_0206(dp):
	values = dp.template_task_values(dp.STAGES[1])
	assert values == {
		"subject": "Schematic Design (SD)",
		"description": dp.stage_description(dp.STAGES[1]),
		"start": 14,
		"duration": 20,
	}
	assert "expected_time" not in values
	for stage in dp.STAGES:
		assert dp.PROCESS_NOTE in dp.stage_description(stage)


# ---------------------------------------------------------------------- seeding


def test_seed_is_insert_only(dp, monkeypatch):
	Site(dp, {dp.TEMPLATE: {"disabled": 0}})
	inserted = []
	monkeypatch.setattr(dp.frappe, "get_doc", lambda *a, **k: inserted.append(a) or None)
	assert dp.seed_template() == "exists"
	assert inserted == []


def test_seed_creates_four_chained_stages_then_the_template(dp, monkeypatch):
	Site(dp, {})
	made = []
	Doc.made = made
	monkeypatch.setattr(dp.frappe, "get_doc", lambda values: Doc(values))
	assert dp.seed_template() == "created"
	assert [m["doctype"] for m in made] == ["Task"] * 4 + ["Project Template"]
	tasks = made[:4]
	assert all(t["is_template"] == 1 and t["status"] == "Template" for t in tasks)
	assert "depends_on" not in tasks[0]
	for before, task in itertools.pairwise(tasks):
		assert task["depends_on"] == [{"task": before["name"]}]
	template = made[4]
	assert template["name"] == dp.TEMPLATE
	assert template["project_type"] == "Design"
	assert [row["subject"] for row in template["tasks"]] == [s["subject"] for s in dp.STAGES]
	assert [row["task"] for row in template["tasks"]] == [t["name"] for t in tasks]


def test_old_template_is_disabled_only_once_the_new_one_exists(dp):
	site = Site(dp, {dp.OLD_TEMPLATE: {"disabled": 0}})
	assert dp.disable_old_template() == "kept"
	assert site.writes == []
	site.templates[dp.TEMPLATE] = {"disabled": 0}
	assert dp.disable_old_template() == "disabled"
	# Only the Disabled box: the old template's tasks and its projects are never written.
	assert site.writes == [("Project Template", dp.OLD_TEMPLATE, "disabled", 1)]
	assert dp.disable_old_template() == "already"
	assert len(site.writes) == 1


def test_no_old_template_is_fine(dp):
	site = Site(dp, {dp.TEMPLATE: {"disabled": 0}})
	assert dp.disable_old_template() == "missing"
	assert site.writes == []


def test_patch_seeds_then_disables_the_old_template(dp, monkeypatch):
	site = Site(dp, {dp.OLD_TEMPLATE: {"disabled": 0}})
	Doc.made = []
	monkeypatch.setattr(Doc, "site", site)
	monkeypatch.setattr(dp.frappe, "get_doc", lambda values: Doc(values))
	_patch(dp).execute()
	assert dp.TEMPLATE in site.templates
	assert site.templates[dp.OLD_TEMPLATE]["disabled"] == 1
	assert site.writes == [("Project Template", dp.OLD_TEMPLATE, "disabled", 1)]


def test_patch_never_raises_rolls_back_and_keeps_the_old_template(dp, monkeypatch):
	site = Site(dp, {dp.OLD_TEMPLATE: {"disabled": 0}})
	patch = _patch(dp)
	events = []
	monkeypatch.setattr(dp.frappe.db, "rollback", lambda **k: events.append(("rollback", k)))
	monkeypatch.setattr(dp.frappe, "log_error", lambda **k: events.append(("log", k["title"])))

	def boom():
		raise RuntimeError("ERPNext refused the template")

	monkeypatch.setattr(dp, "seed_template", boom)
	patch.execute()  # must not raise
	assert ("rollback", {"save_point": patch.SAVEPOINT}) in events
	assert ("log", dp.LOG_TITLE) in events
	assert site.writes == []
	assert site.templates[dp.OLD_TEMPLATE]["disabled"] == 0


def test_patch_never_raises_when_disabling_fails(dp, monkeypatch):
	Site(dp, {dp.TEMPLATE: {"disabled": 0}, dp.OLD_TEMPLATE: {"disabled": 0}})
	logged = []
	monkeypatch.setattr(dp.frappe, "log_error", lambda **k: logged.append(k["title"]))

	def boom(*a, **k):
		raise RuntimeError("database went away")

	monkeypatch.setattr(dp.frappe.db, "set_value", boom)
	_patch(dp).execute()  # must not raise
	assert logged == [dp.LOG_TITLE]


def test_patch_is_registered_post_model_sync():
	lines = [line.strip() for line in (APP / "patches.txt").read_text(encoding="utf-8").splitlines()]
	assert "erpnext_enhancements.patches.seed_design_project_template" in lines
	assert lines.index("erpnext_enhancements.patches.seed_design_project_template") > lines.index(
		"[post_model_sync]"
	)


# ---------------------------------------------------------------------- the Create Project dialog


def test_a_design_stream_in_either_column_is_a_design_deal(dp):
	assert dp.is_design_deal([{"value_stream": "Design"}])
	assert dp.is_design_deal([{"value_stream": None, "value_streams": "Design"}])
	assert dp.is_design_deal([{"value_stream": "Build"}, {"value_stream": "Design "}])
	assert not dp.is_design_deal([{"value_stream": "Build"}, {"value_streams": "Products"}])
	assert not dp.is_design_deal([])
	assert not dp.is_design_deal(None)


def _streams(dp, monkeypatch, rows):
	seen = []

	def get_all(doctype, filters=None, fields=None, **k):
		seen.append((doctype, filters, fields))
		return rows

	monkeypatch.setattr(dp.frappe, "get_all", get_all)
	return seen


def test_dialog_prefills_the_template_for_a_design_deal(dp, monkeypatch):
	Site(dp, {dp.TEMPLATE: {"disabled": 0}})
	seen = _streams(dp, monkeypatch, [{"value_stream": "Build"}, {"value_stream": "Design"}])
	assert dp.opportunity_defaults("CRM-OPP-1") == {"template": dp.TEMPLATE}
	assert seen[0][0] == "Value Stream"
	assert seen[0][1] == {"parenttype": "Opportunity", "parent": "CRM-OPP-1"}
	assert seen[0][2] == ["value_stream", "value_streams"]


def test_dialog_prefills_nothing_for_other_deals(dp, monkeypatch):
	Site(dp, {dp.TEMPLATE: {"disabled": 0}})
	_streams(dp, monkeypatch, [{"value_stream": "Service"}])
	assert dp.opportunity_defaults("CRM-OPP-1") == {"template": None}


def test_dialog_prefills_nothing_without_read_permission(dp, monkeypatch):
	Site(dp, {dp.TEMPLATE: {"disabled": 0}})
	seen = _streams(dp, monkeypatch, [{"value_stream": "Design"}])
	monkeypatch.setattr(dp.frappe, "has_permission", lambda *a, **k: False)
	assert dp.opportunity_defaults("CRM-OPP-1") == {"template": None}
	assert seen == []


def test_dialog_prefills_nothing_when_the_template_is_missing_or_disabled(dp, monkeypatch):
	site = Site(dp, {})
	_streams(dp, monkeypatch, [{"value_stream": "Design"}])
	assert dp.opportunity_defaults("CRM-OPP-1") == {"template": None}
	site.templates[dp.TEMPLATE] = {"disabled": 1}
	assert dp.opportunity_defaults("CRM-OPP-1") == {"template": None}


def test_dialog_defaults_never_raise(dp, monkeypatch):
	Site(dp, {dp.TEMPLATE: {"disabled": 0}})
	logged = []
	monkeypatch.setattr(dp.frappe, "log_error", lambda **k: logged.append(k["title"]))

	def boom(*a, **k):
		raise RuntimeError("database went away")

	monkeypatch.setattr(dp.frappe, "get_all", boom)
	assert dp.opportunity_defaults("CRM-OPP-1") == {"template": None}
	assert logged == [dp.LOG_TITLE]


def test_dialog_asks_for_the_design_default_and_a_move_intake_wins():
	js = (APP / "public" / "js" / "crm_enhancements" / "create_project_prompt.js").read_text(encoding="utf-8")
	assert "erpnext_enhancements.project_enhancements.design_projects.opportunity_defaults" in js
	assert "default: move_defaults.template || design_defaults.template || undefined," in js
