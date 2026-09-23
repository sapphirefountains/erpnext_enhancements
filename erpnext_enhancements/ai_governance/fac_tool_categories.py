"""Keep FAC's category for each of this app's assistant tools in step with what the tool declares.

WHY THIS EXISTS. Every tool in ``assistant_tools/`` sets MCP annotations from ``_gate.py``'s
classification (``readOnlyHint`` / ``destructiveHint`` / ``x-ee-*``). Since FAC 2.5.0 those are
not what a client sees: ``fac_endpoint._build_tool_registry`` merges hints derived from the
tool's *FAC category* **over** them, ``{**tool_annotations, **category_hints}``, and FAC seeds
every external tool's ``FAC Tool Configuration`` row as ``read_write`` -- which it maps to
``readOnlyHint: False``. So on prod all of this app's read tools (``water_calc``,
``training_compliance_status``, ...) were advertised to every MCP client as writes -- and
clients pick default approval behaviour from exactly these hints (Claude's apps do; FAC 3.0.0's
Chat settings page defaults a non-read-only tool to "Ask"). Triton was
spared only because it reads ``x-ee-mutation`` before ``readOnlyHint`` and FAC never touches
the ``x-ee-*`` keys.

FAC never re-detects an external tool (it cannot: its detector hunts the class source for
``perm_type="..."`` keywords and ours use none), so nothing on FAC's side will ever correct the
row. This module writes the category that reproduces the tool's own hints, marked
``category_override = 1`` so FAC's re-detect patches and its ``validate`` both leave it alone.
``_gate.py`` stays the single source of truth; the FAC admin page's category for these tools is
now a projection of it, and a change made there is reverted on the next migrate.

TWO ENTRY POINTS, because of install order. On prod ``erpnext_enhancements`` is installed
BEFORE ``frappe_assistant_core``, so our ``after_migrate`` runs before FAC's -- and FAC's is the
one that inserts the row for a tool added in that deploy. ``sync_fac_tool_categories`` alone
would therefore leave every new tool miscategorised until the migrate after next. So:

* ``set_category_before_insert`` -- ``doc_events["FAC Tool Configuration"]["before_insert"]``.
  Stamps the category on a row as FAC creates it, whatever the ordering.
* ``sync_fac_tool_categories`` -- ``after_migrate``. Repairs the rows that already exist, and
  any a human has since edited.

THE FAC-OPTIONAL INVARIANT HOLDS. Nothing here imports ``assistant_tools`` or
``frappe_assistant_core``: tools are resolved from the ``assistant_tools`` hook by dotted path,
exactly as FAC's own loader does, and each returns before touching anything when the FAC table
is absent. Both entry points must never raise -- an ``after_migrate`` hook that raises aborts
``bench migrate``, which on this repo is the deploy, and a raising ``before_insert`` would abort
FAC's own tool sync.
"""

import frappe

APP_NAME = "erpnext_enhancements"
FAC_TOOL_CONFIG = "FAC Tool Configuration"


def category_for_annotations(annotations):
	"""The FAC tool category whose hints reproduce ``annotations``, or ``None``.

	The inverse of FAC's ``tool_category_detector.category_to_annotations``::

	    readOnlyHint True                        -> "read_only"   {"readOnlyHint": True}
	    readOnlyHint False + destructiveHint     -> "privileged"  {"readOnlyHint": False, "destructiveHint": True}
	    readOnlyHint False                       -> "write"       {"readOnlyHint": False}
	    no readOnlyHint at all                   -> None          (the tool asserts nothing; leave FAC's row alone)

	``write`` rather than ``read_write`` for a plain write: both map to the same hints, but
	``write`` is the one that says what the tool is. ``privileged`` also moves the gate's
	``classify_risk`` to High, which is correct by construction -- a tool only declares
	``destructiveHint`` when ``_gate.HIGH_RISK`` names it.
	"""
	if not isinstance(annotations, dict) or "readOnlyHint" not in annotations:
		return None
	if annotations.get("readOnlyHint"):
		return "read_only"
	if annotations.get("destructiveHint"):
		return "privileged"
	return "write"


def _our_tool_paths():
	"""``{tool_name: dotted_path}`` for every tool this app registers with FAC."""
	paths = {}
	for path in frappe.get_hooks("assistant_tools", app_name=APP_NAME) or []:
		# The module filename IS the tool name (enforced by test_assistant_tools_schema),
		# so the row can be matched without instantiating anything.
		module_path = path.rsplit(".", 1)[0]
		paths[module_path.rsplit(".", 1)[-1]] = path
	return paths


def _declared_category(path):
	"""The category a tool's own annotations imply. Instantiates it, as FAC's loader does."""
	tool = frappe.get_attr(path)()
	return category_for_annotations(getattr(tool, "annotations", None))


def set_category_before_insert(doc, method=None):
	"""doc_event: stamp our category onto a ``FAC Tool Configuration`` row as FAC creates it."""
	try:
		path = _our_tool_paths().get(doc.get("tool_name"))
		if not path:
			return  # FAC's own tool, or another app's -- not ours to classify
		category = _declared_category(path)
		if not category:
			return
		doc.tool_category = category
		doc.category_override = 1
	except Exception:
		# Never abort FAC's tool sync over a hint; the after_migrate pass will retry.
		frappe.log_error(
			f"Could not set the FAC category for {doc.get('tool_name')}\n{frappe.get_traceback()}",
			"AI Governance",
		)


def sync_fac_tool_categories():
	"""after_migrate: align every existing row for this app's tools. Idempotent; never raises.

	Saves through the document rather than ``db.set_value`` so FAC's own ``on_update`` clears
	its tool-configuration caches -- ``tools/list`` itself reads the table directly, but the
	gate's ``_tool_category`` goes through ``ToolRegistry._get_tool_configurations``, which is
	cached. Rows already correct are skipped, so after the first run this is one indexed read.
	"""
	try:
		if not frappe.db.table_exists(FAC_TOOL_CONFIG):
			return  # FAC not installed on this site
		paths = _our_tool_paths()
		if not paths:
			return
		rows = frappe.get_all(
			FAC_TOOL_CONFIG,
			filters={"tool_name": ["in", list(paths)]},
			fields=["name", "tool_name", "tool_category", "category_override"],
		)
	except Exception:
		frappe.log_error(f"FAC tool category sync could not start\n{frappe.get_traceback()}", "AI Governance")
		return

	failed = []
	for row in rows:
		try:
			category = _declared_category(paths[row.tool_name])
			if not category:
				continue
			if row.tool_category == category and int(row.category_override or 0) == 1:
				continue
			doc = frappe.get_doc(FAC_TOOL_CONFIG, row.name)
			doc.tool_category = category
			doc.category_override = 1
			doc.save(ignore_permissions=True)
		except Exception:
			failed.append(f"{row.tool_name}\n{frappe.get_traceback()}")

	if failed:
		frappe.log_error(
			"FAC tool category sync skipped some tools:\n\n" + "\n\n".join(failed), "AI Governance"
		)
