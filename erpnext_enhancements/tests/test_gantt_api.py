"""Pure-Python (no Frappe site) unit tests for the Gantt widget read API.

Plain pytest functions, not ``FrappeTestCase`` — same pattern as
``test_quickbooks_online.py``: a minimal fake ``frappe`` / ``frappe.model`` /
``frappe.utils`` is installed into ``sys.modules`` before importing
``erpnext_enhancements.api.gantt``, so the config-validation, row-shaping and
link-filtering logic runs deterministically without a bench.

The suite focuses on the security contract (the config is client-supplied):
unknown fieldnames throw everywhere they can appear (field map, filters,
order_by), display-only fields are rejected as bar attributes, permission
denial raises, limits clamp, and dependency links can never reference a row
the permission-checked main query did not return.
"""

import sys
import types
from datetime import datetime

import pytest


class Row(dict):
	"""frappe._dict stand-in: dict with attribute access."""

	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError as e:
			raise AttributeError(key) from e


class FakeMeta:
	def __init__(self, name, fields, istable=False, issingle=False):
		self.name = name
		self.fields = [types.SimpleNamespace(**f) for f in fields]
		self.istable = istable
		self.issingle = issingle

	def get_field(self, fieldname):
		for df in self.fields:
			if df.fieldname == fieldname:
				return df
		return None

	def has_field(self, fieldname):
		return self.get_field(fieldname) is not None


def _df(fieldname, fieldtype="Data", options=None):
	return {"fieldname": fieldname, "fieldtype": fieldtype, "options": options}


TASK_META = FakeMeta(
	"Task",
	[
		_df("subject"),
		_df("status", "Select"),
		_df("project", "Link", "Project"),
		_df("exp_start_date", "Datetime"),
		_df("exp_end_date", "Datetime"),
		_df("progress", "Percent"),
		_df("parent_task", "Link", "Task"),
		_df("depends_on", "Table", "Task Depends On"),
		_df("section_x", "Section Break"),
		_df("daily_time", "Time"),
	],
)

DEPENDS_META = FakeMeta(
	"Task Depends On",
	[_df("task", "Link", "Task"), _df("subject")],
	istable=True,
)

PROJECT_META = FakeMeta(
	"Project",
	[
		_df("project_name"),
		_df("expected_start_date", "Date"),
		_df("expected_end_date", "Date"),
		_df("percent_complete", "Percent"),
		_df("status", "Select"),
		_df("custom_master_project", "Link", "Master Project"),
		_df("project_type", "Link", "Project Type"),
	],
)


def install_frappe_stub():
	"""Install/refresh the fake frappe modules and return the ``frappe`` stub."""
	frappe = sys.modules.get("frappe") or types.ModuleType("frappe")

	class DoesNotExistError(Exception):
		pass

	class PermissionError_(Exception):
		pass

	def _throw(message=None, exc=None, **kwargs):
		raise (exc or Exception)(message if isinstance(message, str) else "frappe.throw")

	frappe.DoesNotExistError = DoesNotExistError
	frappe.PermissionError = PermissionError_
	frappe.throw = _throw
	frappe._ = lambda message=None, *args, **kwargs: message
	frappe.whitelist = lambda *args, **kwargs: (lambda fn: fn)

	def parse_json(value):
		import json

		if isinstance(value, str):
			return json.loads(value)
		return value

	frappe.parse_json = parse_json

	metas = {"Task": TASK_META, "Task Depends On": DEPENDS_META, "Project": PROJECT_META}

	def get_meta(doctype):
		if doctype in metas:
			return metas[doctype]
		raise DoesNotExistError(doctype)

	frappe.get_meta = get_meta
	frappe.has_permission = lambda *args, **kwargs: True
	frappe.get_list = lambda *args, **kwargs: []

	frappe_model = sys.modules.get("frappe.model") or types.ModuleType("frappe.model")
	frappe_model.default_fields = (
		"doctype",
		"name",
		"owner",
		"creation",
		"modified",
		"modified_by",
		"parent",
		"parentfield",
		"parenttype",
		"idx",
		"docstatus",
	)
	frappe_model.no_value_fields = (
		"Section Break",
		"Column Break",
		"Tab Break",
		"HTML",
		"Table",
		"Table MultiSelect",
		"Button",
		"Image",
		"Fold",
		"Heading",
	)
	frappe.model = frappe_model

	frappe_utils = sys.modules.get("frappe.utils") or types.ModuleType("frappe.utils")

	def get_datetime(value):
		if isinstance(value, datetime):
			return value
		return datetime.fromisoformat(str(value))

	def _flt(value=0, precision=None):
		try:
			number = float(value or 0)
		except (TypeError, ValueError):
			return 0.0
		return round(number, precision) if precision is not None else number

	frappe_utils.get_datetime = get_datetime
	frappe_utils.flt = _flt
	frappe_utils.cint = lambda value=0, *args, **kwargs: int(_flt(value))
	frappe_utils.cstr = lambda value=None: "" if value is None else str(value)
	frappe.utils = frappe_utils

	sys.modules["frappe"] = frappe
	sys.modules["frappe.model"] = frappe_model
	sys.modules["frappe.utils"] = frappe_utils
	return frappe


def import_gantt():
	"""(Re)import the module under test against the current stubs."""
	import importlib

	sys.modules.pop("erpnext_enhancements.api.gantt", None)
	return importlib.import_module("erpnext_enhancements.api.gantt")


@pytest.fixture()
def env():
	frappe = install_frappe_stub()
	return frappe, import_gantt()


VALID_FIELDS = {
	"text": "subject",
	"start": "exp_start_date",
	"end": "exp_end_date",
	"progress": "progress",
	"parent": "parent_task",
}


def base_config(**overrides):
	cfg = {"doctype": "Task", "fields": dict(VALID_FIELDS)}
	cfg.update(overrides)
	return cfg


# ---------------------------------------------------------------------------
# Field-map validation
# ---------------------------------------------------------------------------


def test_field_map_rejects_unknown_attribute(env):
	_, gantt = env
	with pytest.raises(Exception, match="Unknown Gantt field attribute"):
		gantt._parse_field_map(TASK_META, {**VALID_FIELDS, "color": "subject"})


def test_field_map_requires_text_and_start(env):
	_, gantt = env
	with pytest.raises(Exception, match="requires text"):
		gantt._parse_field_map(TASK_META, {"start": "exp_start_date"})
	with pytest.raises(Exception, match="requires start"):
		gantt._parse_field_map(TASK_META, {"text": "subject"})


def test_field_map_rejects_unknown_fieldname(env):
	_, gantt = env
	with pytest.raises(Exception, match="Unknown field"):
		gantt._parse_field_map(TASK_META, {**VALID_FIELDS, "text": "does_not_exist"})


def test_field_map_rejects_display_only_field(env):
	_, gantt = env
	with pytest.raises(Exception, match="does not hold a value"):
		gantt._parse_field_map(TASK_META, {**VALID_FIELDS, "text": "section_x"})


def test_field_map_optional_attrs_may_be_omitted(env):
	_, gantt = env
	fmap = gantt._parse_field_map(TASK_META, {"text": "subject", "start": "exp_start_date"})
	assert fmap == {"text": "subject", "start": "exp_start_date"}


def test_field_map_start_end_require_date_fieldtypes(env):
	"""Time/Data/standard-string columns as start/end would crash date coercion
	server-side (timedelta from TIME columns, ParserError from free text) —
	they must be rejected at validation, not at shaping."""
	_, gantt = env
	with pytest.raises(Exception, match="Date or Datetime"):
		gantt._parse_field_map(TASK_META, {"text": "subject", "start": "daily_time"})
	with pytest.raises(Exception, match="Date or Datetime"):
		gantt._parse_field_map(TASK_META, {"text": "subject", "start": "exp_start_date", "end": "subject"})
	# standard string columns pass _validate_fieldname but are not dates
	with pytest.raises(Exception, match="Date or Datetime"):
		gantt._parse_field_map(TASK_META, {"text": "subject", "start": "owner"})
	# the standard datetime columns are legitimate date sources
	fmap = gantt._parse_field_map(TASK_META, {"text": "subject", "start": "creation", "end": "modified"})
	assert fmap["start"] == "creation" and fmap["end"] == "modified"


# ---------------------------------------------------------------------------
# Filters / order_by sanitization
# ---------------------------------------------------------------------------


def test_filters_dict_validates_keys(env):
	_, gantt = env
	assert gantt._sanitize_filters(TASK_META, {"project": "PRJ-1"}) == {"project": "PRJ-1"}
	with pytest.raises(Exception, match="Unknown field"):
		gantt._sanitize_filters(TASK_META, {"evil_column": "x"})


def test_filters_list_three_element_ok_four_rejected(env):
	_, gantt = env
	ok = [["project", "=", "PRJ-1"]]
	assert gantt._sanitize_filters(TASK_META, ok) == ok
	with pytest.raises(Exception):
		gantt._sanitize_filters(TASK_META, [["Task Depends On", "task", "=", "T-1"]])
	with pytest.raises(Exception, match="Unknown field"):
		gantt._sanitize_filters(TASK_META, [["evil_column", "=", "x"]])


def test_filters_allow_standard_columns(env):
	_, gantt = env
	assert gantt._sanitize_filters(TASK_META, {"owner": "a@b.c"}) == {"owner": "a@b.c"}


def test_order_by_validation(env):
	_, gantt = env
	assert gantt._sanitize_order_by(TASK_META, None) == "modified desc"
	assert gantt._sanitize_order_by(TASK_META, "exp_start_date asc") == "exp_start_date asc"
	with pytest.raises(Exception, match="Unknown field"):
		gantt._sanitize_order_by(TASK_META, "evil_column asc")
	with pytest.raises(Exception, match="direction"):
		gantt._sanitize_order_by(TASK_META, "subject sideways")
	# an injection payload has >2 whitespace-separated parts -> rejected outright
	with pytest.raises(Exception, match="Invalid Gantt order_by"):
		gantt._sanitize_order_by(TASK_META, "subject asc; drop table tabTask")


# ---------------------------------------------------------------------------
# Endpoint guards
# ---------------------------------------------------------------------------


def test_denies_without_read_permission(env):
	frappe, gantt = env
	frappe.has_permission = lambda *args, **kwargs: False
	with pytest.raises(frappe.PermissionError):
		gantt.get_gantt_data(base_config())


def test_rejects_unknown_doctype(env):
	_, gantt = env
	with pytest.raises(Exception, match="Unknown doctype"):
		gantt.get_gantt_data(base_config(doctype="Nope"))


def test_rejects_child_table_doctype(env):
	_, gantt = env
	with pytest.raises(Exception, match="regular doctype"):
		gantt.get_gantt_data(
			{"doctype": "Task Depends On", "fields": {"text": "subject", "start": "subject"}}
		)


def test_limit_is_clamped_and_fields_deduped(env):
	frappe, gantt = env
	captured = {}

	def get_list(doctype, **kwargs):
		captured.update(kwargs, doctype=doctype)
		return []

	frappe.get_list = get_list
	gantt.get_gantt_data(base_config(limit=999999, order_by="exp_start_date asc"))
	assert captured["doctype"] == "Task"
	assert captured["limit_page_length"] == gantt.MAX_ROWS
	assert captured["fields"][0] == "name"
	assert sorted(captured["fields"]) == sorted(set(captured["fields"]))
	assert captured["order_by"] == "exp_start_date asc"


# ---------------------------------------------------------------------------
# Row shaping (date semantics, progress, parent re-rooting)
# ---------------------------------------------------------------------------


def _shape(gantt, rows, fmap=None):
	return gantt._shape_tasks([Row(r) for r in rows], fmap or dict(VALID_FIELDS))


def test_shape_midnight_end_is_inclusive(env):
	_, gantt = env
	tasks, skipped = _shape(
		gantt,
		[
			{
				"name": "T1",
				"subject": "A",
				"exp_start_date": "2026-01-01 00:00:00",
				"exp_end_date": "2026-01-05 00:00:00",
				"progress": 50,
				"parent_task": None,
			}
		],
	)
	assert skipped == 0
	assert tasks[0]["start_date"] == "2026-01-01 00:00"
	# midnight end = "through Jan 5" -> exclusive end Jan 6 for DHTMLX
	assert tasks[0]["end_date"] == "2026-01-06 00:00"
	assert tasks[0]["progress"] == 0.5


def test_shape_explicit_time_end_is_untouched(env):
	_, gantt = env
	tasks, _ = _shape(
		gantt,
		[
			{
				"name": "T1",
				"subject": "A",
				"exp_start_date": "2026-01-01 09:00:00",
				"exp_end_date": "2026-01-01 17:30:00",
				"progress": 0,
				"parent_task": None,
			}
		],
	)
	assert tasks[0]["end_date"] == "2026-01-01 17:30"


def test_shape_date_fallbacks_and_unscheduled_count(env):
	_, gantt = env
	tasks, skipped = _shape(
		gantt,
		[
			# start only -> one-day bar
			{
				"name": "T1",
				"subject": "A",
				"exp_start_date": "2026-01-01 00:00:00",
				"exp_end_date": None,
				"progress": 0,
				"parent_task": None,
			},
			# end only -> one-day bar ending there
			{
				"name": "T2",
				"subject": "B",
				"exp_start_date": None,
				"exp_end_date": "2026-01-10 00:00:00",
				"progress": 0,
				"parent_task": None,
			},
			# no dates -> skipped but counted
			{
				"name": "T3",
				"subject": "C",
				"exp_start_date": None,
				"exp_end_date": None,
				"progress": 0,
				"parent_task": None,
			},
			# end before start -> clamped to one day from start
			{
				"name": "T4",
				"subject": "D",
				"exp_start_date": "2026-02-10 08:00:00",
				"exp_end_date": "2026-02-01 08:00:00",
				"progress": 0,
				"parent_task": None,
			},
		],
	)
	assert skipped == 1
	by_id = {t["id"]: t for t in tasks}
	assert by_id["T1"]["end_date"] == "2026-01-02 00:00"
	assert by_id["T2"]["start_date"] == "2026-01-10 00:00"
	assert by_id["T2"]["end_date"] == "2026-01-11 00:00"
	assert "T3" not in by_id
	assert by_id["T4"]["end_date"] == "2026-02-11 08:00"


def test_shape_progress_clamped(env):
	_, gantt = env
	tasks, _ = _shape(
		gantt,
		[
			{
				"name": "T1",
				"subject": "A",
				"exp_start_date": "2026-01-01 00:00:00",
				"exp_end_date": None,
				"progress": 250,
				"parent_task": None,
			},
			{
				"name": "T2",
				"subject": "B",
				"exp_start_date": "2026-01-01 00:00:00",
				"exp_end_date": None,
				"progress": -5,
				"parent_task": None,
			},
		],
	)
	assert tasks[0]["progress"] == 1
	assert tasks[1]["progress"] == 0


def test_shape_child_of_unscheduled_parent_is_rerooted(env):
	"""A parent row that was FETCHED but skipped (no dates) must not leave its
	children pointing at a task absent from the response — DHTMLX silently
	drops such orphans and their whole subtree (undated ERPNext group Tasks
	with dated subtasks hit this constantly)."""
	_, gantt = env
	tasks, skipped = _shape(
		gantt,
		[
			{
				"name": "GROUP",
				"subject": "Phase 1",
				"exp_start_date": None,
				"exp_end_date": None,
				"progress": 0,
				"parent_task": None,
			},
			{
				"name": "T1",
				"subject": "Dig",
				"exp_start_date": "2026-01-01 00:00:00",
				"exp_end_date": None,
				"progress": 0,
				"parent_task": "GROUP",
			},
		],
	)
	assert skipped == 1
	assert [t["id"] for t in tasks] == ["T1"]
	assert tasks[0]["parent"] == 0


def test_shape_uncoercible_date_value_counts_unscheduled(env):
	"""Garbage in a date column must skip the row, never crash the request."""
	_, gantt = env
	tasks, skipped = _shape(
		gantt,
		[
			{
				"name": "T1",
				"subject": "Bad",
				"exp_start_date": "not a date",
				"exp_end_date": None,
				"progress": 0,
				"parent_task": None,
			},
			{
				"name": "T2",
				"subject": "Good",
				"exp_start_date": "2026-01-01 00:00:00",
				"exp_end_date": None,
				"progress": 0,
				"parent_task": None,
			},
		],
	)
	assert skipped == 1
	assert [t["id"] for t in tasks] == ["T2"]


def test_shape_parent_outside_set_is_rerooted(env):
	_, gantt = env
	tasks, _ = _shape(
		gantt,
		[
			{
				"name": "T1",
				"subject": "A",
				"exp_start_date": "2026-01-01 00:00:00",
				"exp_end_date": None,
				"progress": 0,
				"parent_task": None,
			},
			{
				"name": "T2",
				"subject": "B",
				"exp_start_date": "2026-01-02 00:00:00",
				"exp_end_date": None,
				"progress": 0,
				"parent_task": "T1",
			},
			{
				"name": "T3",
				"subject": "C",
				"exp_start_date": "2026-01-03 00:00:00",
				"exp_end_date": None,
				"progress": 0,
				"parent_task": "NOT-FETCHED",
			},
		],
	)
	by_id = {t["id"]: t for t in tasks}
	assert by_id["T2"]["parent"] == "T1"
	assert by_id["T3"]["parent"] == 0


def test_shape_blank_text_falls_back_to_name(env):
	_, gantt = env
	tasks, _ = _shape(
		gantt,
		[
			{
				"name": "T1",
				"subject": None,
				"exp_start_date": "2026-01-01 00:00:00",
				"exp_end_date": None,
				"progress": 0,
				"parent_task": None,
			}
		],
	)
	assert tasks[0]["text"] == "T1"


# ---------------------------------------------------------------------------
# Dependency links
# ---------------------------------------------------------------------------


def test_links_require_a_table_field(env):
	_, gantt = env
	with pytest.raises(Exception, match="Table field"):
		gantt._fetch_links(TASK_META, "subject", ["T1"])


def test_links_filtered_to_returned_rows(env):
	frappe, gantt = env
	captured = {}

	def get_list(doctype, **kwargs):
		captured.update(kwargs, doctype=doctype)
		return [
			Row({"name": "d1", "parent": "T2", "task": "T1"}),  # both ends in set -> kept
			Row({"name": "d2", "parent": "T2", "task": "HIDDEN"}),  # predecessor not readable -> dropped
			Row({"name": "d3", "parent": "T1", "task": "T1"}),  # self-link -> dropped
		]

	frappe.get_list = get_list
	links = gantt._fetch_links(TASK_META, "depends_on", ["T1", "T2"])
	assert links == [{"id": "d1", "source": "T1", "target": "T2", "type": "0"}]
	# the child query is parent-permission checked and scoped to the fetched rows
	assert captured["doctype"] == "Task Depends On"
	assert captured["parent_doctype"] == "Task"
	assert captured["filters"]["parenttype"] == "Task"
	assert captured["filters"]["parentfield"] == "depends_on"
	assert captured["filters"]["parent"] == ["in", ["T1", "T2"]]


def test_links_skipped_when_no_tasks(env):
	frappe, gantt = env

	def boom(*args, **kwargs):
		raise AssertionError("should not query children with no parents")

	frappe.get_list = boom
	assert gantt._fetch_links(TASK_META, "depends_on", []) == []


# ---------------------------------------------------------------------------
# End-to-end through the stubbed get_list
# ---------------------------------------------------------------------------


def test_happy_path_shapes_tasks_and_links(env):
	frappe, gantt = env
	calls = []

	def get_list(doctype, **kwargs):
		calls.append((doctype, kwargs))
		if doctype == "Task":
			return [
				Row(
					{
						"name": "T1",
						"subject": "Dig",
						"exp_start_date": "2026-01-01 00:00:00",
						"exp_end_date": "2026-01-03 00:00:00",
						"progress": 100,
						"parent_task": None,
					}
				),
				Row(
					{
						"name": "T2",
						"subject": "Pour",
						"exp_start_date": "2026-01-04 00:00:00",
						"exp_end_date": "2026-01-06 00:00:00",
						"progress": 0,
						"parent_task": None,
					}
				),
			]
		return [Row({"name": "d1", "parent": "T2", "task": "T1"})]

	frappe.get_list = get_list
	out = gantt.get_gantt_data(base_config(filters={"project": "PRJ-1"}, dependencies="depends_on", limit=10))
	assert [t["id"] for t in out["tasks"]] == ["T1", "T2"]
	assert out["links"] == [{"id": "d1", "source": "T1", "target": "T2", "type": "0"}]
	assert out["meta"] == {"total_rows": 2, "unscheduled": 0}
	assert calls[0][1]["filters"] == {"project": "PRJ-1"}
	assert calls[0][1]["limit_page_length"] == 10


def test_config_accepts_json_string(env):
	frappe, gantt = env
	frappe.get_list = lambda *args, **kwargs: []
	import json

	out = gantt.get_gantt_data(json.dumps(base_config()))
	assert out["tasks"] == [] and out["links"] == []


# ---------------------------------------------------------------------------
# Composite mode (group_by roots + nested children)
# ---------------------------------------------------------------------------

COMPOSITE_CONFIG = {
	"doctype": "Project",
	"fields": {
		"text": "project_name",
		"start": "expected_start_date",
		"end": "expected_end_date",
		"progress": "percent_complete",
	},
	"group_by": "custom_master_project",
	"children": {
		"doctype": "Task",
		"link_field": "project",
		"fields": {
			"text": "subject",
			"start": "exp_start_date",
			"end": "exp_end_date",
			"progress": "progress",
			"parent": "parent_task",
		},
		"filters": {"status": ["not in", ["Canceled"]]},
		"dependencies": "depends_on",
	},
}


def composite_config(**overrides):
	import copy

	cfg = copy.deepcopy(COMPOSITE_CONFIG)
	cfg.update(overrides)
	return cfg


def _composite_get_list(calls):
	"""Stub get_list serving Project roots, Task children and dependency rows."""

	def get_list(doctype, **kwargs):
		calls.append((doctype, kwargs))
		if doctype == "Project":
			return [
				Row(
					{
						"name": "P1",
						"project_name": "Fountain A",
						"expected_start_date": "2026-01-01",
						"expected_end_date": "2026-01-31",
						"percent_complete": 50,
						"custom_master_project": "MP-A",
					}
				),
				Row(
					{
						"name": "P2",
						"project_name": "Fountain B",
						"expected_start_date": None,
						"expected_end_date": None,
						"percent_complete": 10,
						"custom_master_project": None,
					}
				),
				Row(
					{
						"name": "P3",
						"project_name": "Fountain C",
						"expected_start_date": None,
						"expected_end_date": None,
						"percent_complete": 0,
						"custom_master_project": "MP-A",
					}
				),
			]
		if doctype == "Task":
			return [
				Row(
					{
						"name": "T1",
						"subject": "Dig",
						"exp_start_date": "2026-01-02 00:00:00",
						"exp_end_date": "2026-01-05 00:00:00",
						"progress": 100,
						"parent_task": None,
						"project": "P1",
					}
				),
				Row(
					{
						"name": "T2",
						"subject": "Pour",
						"exp_start_date": "2026-01-06 00:00:00",
						"exp_end_date": "2026-01-09 00:00:00",
						"progress": 0,
						"parent_task": "T1",
						"project": "P1",
					}
				),
				Row(
					{
						"name": "T3",
						"subject": "Plumb",
						"exp_start_date": "2026-01-03 00:00:00",
						"exp_end_date": None,
						"progress": 0,
						"parent_task": None,
						"project": "P2",
					}
				),
				Row(
					{
						"name": "T4",
						"subject": "Orphan",
						"exp_start_date": "2026-01-03 00:00:00",
						"exp_end_date": None,
						"progress": 0,
						"parent_task": None,
						"project": "P-MISSING",
					}
				),
				Row(
					{
						"name": "T5",
						"subject": "Undated",
						"exp_start_date": None,
						"exp_end_date": None,
						"progress": 0,
						"parent_task": None,
						"project": "P1",
					}
				),
			]
		# Task Depends On (child dependency links)
		return [
			Row({"name": "d1", "parent": "T2", "task": "T1"}),  # kept: both ends kept
			Row({"name": "d2", "parent": "T2", "task": "T4"}),  # T4's root missing -> dropped
		]

	return get_list


def test_composite_groups_roots_and_children(env):
	frappe, gantt = env
	calls = []
	frappe.get_list = _composite_get_list(calls)
	out = gantt.get_gantt_data(composite_config())
	by_id = {t["id"]: t for t in out["tasks"]}

	# synthetic group row from the group_by Link value
	assert by_id["G::MP-A"]["type"] == "project"
	assert by_id["G::MP-A"]["ref_doctype"] == "Master Project"
	assert by_id["G::MP-A"]["parent"] == 0

	# dated root parents onto its group; ungrouped root parents onto 0
	assert by_id["P::P1"]["parent"] == "G::MP-A"
	assert by_id["P::P1"]["ref_doctype"] == "Project" and by_id["P::P1"]["ref_name"] == "P1"
	assert by_id["P::P2"]["parent"] == 0

	# undated root WITH children -> dateless "project" container, not skipped
	assert by_id["P::P2"]["type"] == "project"
	assert "start_date" not in by_id["P::P2"]
	# undated root WITHOUT children -> skipped and counted
	assert "P::P3" not in by_id

	# children: own emitted parent when present, else the root
	assert by_id["C::T1"]["parent"] == "P::P1"
	assert by_id["C::T2"]["parent"] == "C::T1"
	assert by_id["C::T3"]["parent"] == "P::P2"
	assert by_id["C::T1"]["ref_doctype"] == "Task" and by_id["C::T1"]["ref_name"] == "T1"

	# T4's root was never returned -> dropped; T5 undated -> unscheduled (with P3)
	assert "C::T4" not in by_id
	assert out["meta"] == {"total_rows": 3, "unscheduled": 2, "dropped_children": 1}

	# child dependency links are prefixed and constrained to kept children
	assert out["links"] == [{"id": "d1", "source": "C::T1", "target": "C::T2", "type": "0"}]

	# the child query was constrained to the returned roots and carried the
	# caller's child filters
	child_call = next(c for c in calls if c[0] == "Task")
	assert child_call[1]["filters"]["project"] == ["in", ["P1", "P2", "P3"]]
	assert child_call[1]["filters"]["status"] == ["not in", ["Canceled"]]
	assert "project" in child_call[1]["fields"]


def test_composite_rejects_root_parent_mapping(env):
	_, gantt = env
	cfg = composite_config()
	cfg["fields"]["parent"] = "project_name"  # any value-bearing field
	with pytest.raises(Exception, match="root 'parent' mapping"):
		gantt.get_gantt_data(cfg)


def test_children_link_field_must_point_at_root(env):
	_, gantt = env
	cfg = composite_config()
	cfg["children"]["link_field"] = "parent_task"  # Link, but points at Task
	with pytest.raises(Exception, match="link_field"):
		gantt.get_gantt_data(cfg)


def test_children_fieldnames_validated_against_child_meta(env):
	frappe, gantt = env
	frappe.get_list = _composite_get_list([])
	cfg = composite_config()
	cfg["children"]["fields"]["text"] = "evil_column"
	with pytest.raises(Exception, match="Unknown field"):
		gantt.get_gantt_data(cfg)


def test_children_permission_denied(env):
	frappe, gantt = env
	frappe.has_permission = lambda doctype, *args, **kwargs: doctype != "Task"
	frappe.get_list = _composite_get_list([])
	with pytest.raises(frappe.PermissionError):
		gantt.get_gantt_data(composite_config())


def test_group_by_without_children_still_composite(env):
	frappe, gantt = env
	calls = []
	frappe.get_list = _composite_get_list(calls)
	cfg = composite_config()
	del cfg["children"]
	out = gantt.get_gantt_data(cfg)
	by_id = {t["id"]: t for t in out["tasks"]}
	assert "G::MP-A" in by_id and by_id["P::P1"]["parent"] == "G::MP-A"
	# undated roots have no children to anchor -> both skipped
	assert "P::P2" not in by_id and "P::P3" not in by_id
	assert out["meta"]["unscheduled"] == 2
	# only the Project query ran
	assert [c[0] for c in calls] == ["Project"]


def test_group_rows_sorted_alphabetically(env):
	"""Group order must be stable A-Z (legacy portfolio behavior), not
	first-appearance order, which would reshuffle with the root order_by."""
	frappe, gantt = env

	def get_list(doctype, **kwargs):
		return [
			Row(
				{
					"name": "P1",
					"project_name": "One",
					"expected_start_date": "2026-01-01",
					"expected_end_date": "2026-01-05",
					"percent_complete": 0,
					"custom_master_project": "ZZ Program",
				}
			),
			Row(
				{
					"name": "P2",
					"project_name": "Two",
					"expected_start_date": "2026-02-01",
					"expected_end_date": "2026-02-05",
					"percent_complete": 0,
					"custom_master_project": "AA Program",
				}
			),
		]

	frappe.get_list = get_list
	cfg = composite_config()
	del cfg["children"]
	out = gantt.get_gantt_data(cfg)
	group_ids = [t["id"] for t in out["tasks"] if t["id"].startswith("G::")]
	assert group_ids == ["G::AA Program", "G::ZZ Program"]


# ---------------------------------------------------------------------------
# extra_fields / group_by coalescing / lazy children
# ---------------------------------------------------------------------------


def test_extra_fields_validated_and_passed_through(env):
	frappe, gantt = env
	captured = {}

	def get_list(doctype, **kwargs):
		captured.update(kwargs)
		return [
			Row(
				{
					"name": "P1",
					"project_name": "A",
					"expected_start_date": "2026-01-01",
					"expected_end_date": "2026-01-05",
					"percent_complete": 0,
					"custom_master_project": None,
					"project_type": "Build",
					"status": "Active",
				}
			)
		]

	frappe.get_list = get_list
	cfg = composite_config(extra_fields=["project_type", "status"])
	del cfg["children"]
	cfg["group_by"] = "project_type"
	out = gantt.get_gantt_data(cfg)
	root = next(t for t in out["tasks"] if t["id"].startswith("P::"))
	assert root["project_type"] == "Build" and root["status"] == "Active"
	# the extra columns are actually queried
	assert "project_type" in captured["fields"] and "status" in captured["fields"]


def test_extra_fields_rejects_unknown_reserved_and_overlong(env):
	_, gantt = env
	with pytest.raises(Exception, match="Unknown field"):
		gantt._parse_extra_fields(PROJECT_META, ["nope_not_a_field"])
	# "parent" is a real standard column AND a DHTMLX output key — passing it
	# through would silently re-root every row
	with pytest.raises(Exception, match="reserved name"):
		gantt._parse_extra_fields(PROJECT_META, ["parent"])
	with pytest.raises(Exception, match="Too many"):
		gantt._parse_extra_fields(PROJECT_META, ["status"] * (gantt.MAX_EXTRA_FIELDS + 1))
	# display-only fields are still refused
	with pytest.raises(Exception, match="does not hold a value"):
		gantt._parse_extra_fields(TASK_META, ["section_x"])


def test_extra_fields_work_on_single_source_responses(env):
	frappe, gantt = env
	frappe.get_list = lambda doctype, **kw: [
		Row(
			{
				"name": "T1",
				"subject": "A",
				"status": "Working",
				"exp_start_date": "2026-01-01 00:00:00",
				"exp_end_date": None,
				"progress": 0,
				"parent_task": None,
			}
		)
	]
	out = gantt.get_gantt_data(base_config(extra_fields=["status"]))
	assert out["tasks"][0]["status"] == "Working"


def test_group_by_list_coalesces_first_non_empty(env):
	frappe, gantt = env
	frappe.get_list = lambda doctype, **kw: [
		# has a master project -> grouped by it
		Row(
			{
				"name": "P1",
				"project_name": "A",
				"expected_start_date": "2026-01-01",
				"expected_end_date": "2026-01-05",
				"percent_complete": 0,
				"custom_master_project": "MP-A",
				"project_type": "Build",
			}
		),
		# no master project -> falls back to project_type
		Row(
			{
				"name": "P2",
				"project_name": "B",
				"expected_start_date": "2026-02-01",
				"expected_end_date": "2026-02-05",
				"percent_complete": 0,
				"custom_master_project": None,
				"project_type": "Design",
			}
		),
		# neither -> stays at root
		Row(
			{
				"name": "P3",
				"project_name": "C",
				"expected_start_date": "2026-03-01",
				"expected_end_date": "2026-03-05",
				"percent_complete": 0,
				"custom_master_project": None,
				"project_type": None,
			}
		),
	]
	cfg = composite_config(group_by=["custom_master_project", "project_type"])
	del cfg["children"]
	out = gantt.get_gantt_data(cfg)
	by_id = {t["id"]: t for t in out["tasks"]}
	assert by_id["P::P1"]["parent"] == "G::MP-A"
	assert by_id["P::P2"]["parent"] == "G::Design"
	assert by_id["P::P3"]["parent"] == 0
	# each group row references the doctype of the field that produced it
	assert by_id["G::MP-A"]["ref_doctype"] == "Master Project"
	assert by_id["G::Design"]["ref_doctype"] == "Project Type"


def test_lazy_children_return_counts_not_rows(env):
	frappe, gantt = env
	calls = []

	def get_list(doctype, **kwargs):
		calls.append((doctype, kwargs))
		if doctype == "Project":
			return [
				Row(
					{
						"name": "P1",
						"project_name": "A",
						"expected_start_date": "2026-01-01",
						"expected_end_date": "2026-01-05",
						"percent_complete": 0,
						"custom_master_project": None,
					}
				),
				# undated, but has children -> must survive as a container
				Row(
					{
						"name": "P2",
						"project_name": "B",
						"expected_start_date": None,
						"expected_end_date": None,
						"percent_complete": 0,
						"custom_master_project": None,
					}
				),
				# undated with no children -> skipped
				Row(
					{
						"name": "P3",
						"project_name": "C",
						"expected_start_date": None,
						"expected_end_date": None,
						"percent_complete": 0,
						"custom_master_project": None,
					}
				),
			]
		# shape Frappe v16 actually returns for fields=[link, {"COUNT": "*"}]
		return [Row({"project": "P1", "COUNT(*)": 7}), Row({"project": "P2", "COUNT(*)": 2})]

	frappe.get_list = get_list
	cfg = composite_config()
	cfg["children"]["lazy"] = True
	out = gantt.get_gantt_data(cfg)
	by_id = {t["id"]: t for t in out["tasks"]}

	# no child rows at all, just the caret markers
	assert not [t for t in out["tasks"] if t["id"].startswith("C::")]
	assert by_id["P::P1"]["$has_child"] is True
	assert by_id["P::P1"]["ee_child_count"] == 7
	assert by_id["P::P1"]["open"] is False
	assert by_id["P::P2"]["$has_child"] is True  # undated container kept
	assert "P::P3" not in by_id

	# the child query was a grouped COUNT, not a row fetch — and the aggregate
	# MUST be dict syntax: Frappe v16 throws "SQL functions are not allowed as
	# strings in SELECT" for the "count(name) as x" form
	child_call = next(c for c in calls if c[0] == "Task")
	assert child_call[1]["group_by"] == "project"
	assert {"COUNT": "*"} in child_call[1]["fields"]
	assert not [f for f in child_call[1]["fields"] if isinstance(f, str) and "(" in f]


def test_lazy_child_count_survives_a_renamed_aggregate_alias(env):
	"""The COUNT alias is Frappe's, not ours. If a future version renames it,
	counts must still be read (a zero would drop every caret silently)."""
	frappe, gantt = env

	def get_list(doctype, **kwargs):
		if doctype == "Project":
			return [
				Row(
					{
						"name": "P1",
						"project_name": "A",
						"expected_start_date": "2026-01-01",
						"expected_end_date": "2026-01-05",
						"percent_complete": 0,
						"custom_master_project": None,
					}
				)
			]
		return [Row({"project": "P1", "count_of_rows": 4})]

	frappe.get_list = get_list
	cfg = composite_config()
	cfg["children"]["lazy"] = True
	out = gantt.get_gantt_data(cfg)
	root = next(t for t in out["tasks"] if t["id"] == "P::P1")
	assert root["$has_child"] is True and root["ee_child_count"] == 4


def test_non_lazy_children_have_no_caret_markers(env):
	frappe, gantt = env
	frappe.get_list = _composite_get_list([])
	out = gantt.get_gantt_data(composite_config())
	assert not [t for t in out["tasks"] if t.get("$has_child")]
