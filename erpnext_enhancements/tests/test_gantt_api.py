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

	metas = {"Task": TASK_META, "Task Depends On": DEPENDS_META}

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
