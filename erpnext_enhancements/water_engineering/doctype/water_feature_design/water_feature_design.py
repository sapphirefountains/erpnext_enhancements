# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the Water Feature Design doctype (schema 2, the Fountain Designer).

The design lives in one JSON document, ``model_json`` (``docs/fountain-designer/PLAN.md`` 3.3).
On every save ``validate`` runs the whole automation ladder over it
(``water_engineering.derive.derive_model``, through ``api/designer.run_derive``, which adds the
house rules, the pump catalog, Item prices and the geocoded site) and writes back:

* the derived model (``auto`` sections filled, ``custom`` ones untouched) into ``model_json``;
* the results, per-row values, charts and outputs into ``results_json``;
* the typed issues and readiness into ``design_issues_json`` / ``readiness_json``;
* the rollups (flows, heads, pump, power, basin water, budget, O&M, counts) into their columns;
* the BOM into ``bom``: every ``Auto`` line rewritten, every ``Manual`` line kept.

The page (``api/designer.save_design``), the AI tools and a Desk save all go through here, so a
design reads the same wherever it was last saved. The nine schema-1 child tables left this
doctype in v1.768.0, when ``patches/convert_water_feature_designs_to_model.py`` turned the stored rows
into ``model_json``; the child DocTypes and their tables went in v1.771.0 (phase H,
``patches/retire_water_feature_child_doctypes.py``).
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from erpnext_enhancements.water_engineering import issues as design_issues
from erpnext_enhancements.water_engineering import prints
from erpnext_enhancements.water_engineering.engine import kits as kits_engine
from erpnext_enhancements.water_engineering.engine import model as model_engine

# results_json keeps every derive key beyond the ones stored in their own fields.
RESULT_KEYS = (
	"results",
	"values",
	"rollups",
	"charts",
	"outputs",
	"verdict",
	"electrical",
	"structural",
	"controls",
	# what the print formats read beyond the page's reply (phase G, water_engineering/prints.py)
	"paper",
)

# Rollup key (the API's) -> field. TDH keeps its v1 column, computed_tdh_ft, which the dashboard
# and the packet read.
ROLLUP_FIELDS = {
	"design_flow_gpm": "design_flow_gpm",
	"operating_flow_gpm": "operating_flow_gpm",
	"tdh_ft": "computed_tdh_ft",
	"operating_head_ft": "operating_head_ft",
	"pump_kw": "pump_kw",
	"total_basin_gallons": "total_basin_gallons",
	"required_circulation_gpm": "required_circulation_gpm",
	"budget_total": "budget_total",
	"om_cost_per_year": "om_cost_per_year",
}
LINE_FIELDS = (
	"section",
	"item",
	"description",
	"qty",
	"uom",
	"rate",
	"amount",
	"source",
	"catalog_source",
	"line_key",
)


class WaterFeatureDesign(Document):
	def validate(self):
		self.recompute()
		self.completion_percent = compute_completion_percent(self)
		self._drop_stale_acks()
		self._enforce_status_gates()

	def before_submit(self):
		issues = self._live_issues()
		blockers = [i for i in issues if i.get("severity") == "blocker"]
		if blockers:
			frappe.throw(
				_("Resolve these blocking issues before issuing the design:") + "<br>" + _bullets(blockers),
				title=_("Blocking Issues"),
			)
		unacked = design_issues.open_warnings(issues)
		if unacked:
			frappe.throw(
				_("Sign off each open warning (or resolve it) before issuing the design:")
				+ "<br>"
				+ _bullets(unacked),
				title=_("Open Warnings"),
			)
		if not cint(self.issue_ready):
			frappe.throw(
				_("The design package is not complete enough to issue:") + "<br>" + _missing_bullets(self),
				title=_("Package Not Ready"),
			)

	def on_submit(self):
		self.db_set("status", "Issued")

	# --------------------------------------------------------------- gating
	def _live_issues(self):
		if getattr(self, "_issues_cache", None) is None:
			self._issues_cache = _json_list(self.get("design_issues_json"))
		return self._issues_cache

	def _drop_stale_acks(self):
		"""A stale acknowledgement must never grandfather a new problem: keep only the acks whose
		issue key still matches a live issue (the record's rows and ``model.acks`` both)."""
		live = {i.get("key") for i in self._live_issues()}
		rows = self.get("issue_acks") or []
		kept = [a for a in rows if a.issue_key in live]
		if len(kept) != len(rows):
			self.set("issue_acks", kept)

	def _enforce_status_gates(self):
		"""Reviewed and Issued are earned: no blockers, and Issued also needs the package gate. The
		gate fires on the status change only, so a design already Reviewed stays saveable and the
		sign-off endpoint's own save never trips the gate it remedies."""
		if self.status not in ("Reviewed", "Issued"):
			return
		before = self.get_doc_before_save()
		if before and (before.status or "") == (self.status or ""):
			return
		blockers = [i for i in self._live_issues() if i.get("severity") == "blocker"]
		if blockers:
			frappe.throw(
				_("Cannot set status {0} with blocking issues open:").format(_(self.status))
				+ "<br>"
				+ _bullets(blockers),
				title=_("Blocking Issues"),
			)
		if self.status == "Issued" and not cint(self.issue_ready):
			frappe.throw(
				_("The design package is not complete enough to issue:") + "<br>" + _missing_bullets(self),
				title=_("Package Not Ready"),
			)

	# ---------------------------------------------------------------- bridge
	def recompute(self):
		"""Run the ladder over ``model_json`` and write everything back. Never raises out of
		validate on an engine fault: the derive turns a failed stage into a blocker, and anything
		that escapes it becomes one CALC_ERROR blocker with the trace in the Error Log."""
		self._issues_cache = None
		model = stored_model(self.get("model_json"))
		if model is None:
			model = model_engine.new_model()
		if model_engine.is_legacy(model):
			write_issue_state(
				self, [_unconverted_issue()], {"sections": [], "calc_ready": False, "issue_ready": False}
			)
			return
		model["acks"] = acks_from_rows(self.get("issue_acks") or [], model.get("acks"))
		try:
			from erpnext_enhancements.water_engineering.api.designer import run_derive

			out = run_derive(model, geocode=True)
		except Exception:
			frappe.log_error(title="Water Feature Design derive", message=frappe.get_traceback())
			write_issue_state(
				self,
				[design_issues.calc_error_issue()],
				{"sections": [], "calc_ready": False, "issue_ready": False},
			)
			return
		write_derive(self, out)


# ------------------------------------------------------------- pure helpers
# (no frappe beyond the doc's get / set / append, so the tests run them on a fake doc)


def _json_list(text):
	if isinstance(text, list):
		return text
	try:
		data = json.loads(text or "[]")
	except (TypeError, ValueError):
		return []
	return data if isinstance(data, list) else []


def _json_dict(text):
	if isinstance(text, dict):
		return text
	try:
		data = json.loads(text or "{}")
	except (TypeError, ValueError):
		return {}
	return data if isinstance(data, dict) else {}


def stored_model(raw):
	"""``model_json`` as a dict (the JSON field reads back as text or a dict), or None."""
	data = _json_dict(raw) if raw else None
	return data or None


def _bullets(issues):
	return "<br>".join(f"• {frappe.utils.escape_html(i.get('title') or '')}" for i in issues)


def _missing_bullets(doc):
	readiness = _json_dict(doc.get("readiness_json"))
	missing = [m for s in readiness.get("sections") or [] for m in s.get("missing") or []]
	return "<br>".join(f"• {frappe.utils.escape_html(str(m))}" for m in missing if m)


def _unconverted_issue():
	return design_issues._issue(
		"MODEL_NOT_CONVERTED",
		design_issues.BLOCKER,
		"This design was made before the Fountain Designer and has not been converted",
		"features",
		detail="Its model is in the old shape; see the notes on the record.",
		fix_hint="Start the design again in the Fountain Designer.",
	)


def acks_from_rows(rows, model_acks=None):
	"""``model.acks`` rebuilt from the record's Water Design Issue Ack rows (the record is the
	source of truth; a row deleted in the grid un-acknowledges the warning)."""
	by_key = {a.get("issue_key"): a for a in model_acks or [] if isinstance(a, dict)}
	out = []
	for row in rows:
		key = getattr(row, "issue_key", None)
		if not key:
			continue
		prior = by_key.get(key) or {}
		out.append(
			{
				"issue_key": key,
				"note": getattr(row, "note", None) or prior.get("note") or "",
				"by": getattr(row, "acknowledged_by", None) or prior.get("by") or "",
				"on": str(getattr(row, "acknowledged_on", None) or prior.get("on") or ""),
			}
		)
	return out


def write_issue_state(doc, issues, readiness):
	counts = design_issues.summarize(issues)
	doc.design_issues_json = json.dumps(issues)
	doc.readiness_json = json.dumps(readiness)
	doc.blocker_count = counts["blocker_count"]
	doc.warning_count = counts["warning_count"]
	doc.issue_summary = counts["summary"]
	doc.issue_ready = 1 if readiness.get("issue_ready") else 0
	doc.has_warnings = 1 if (counts["blocker_count"] or counts["warning_count"]) else 0
	doc.next_inputs_needed = "\n".join(
		str(m) for s in readiness.get("sections") or [] for m in s.get("missing") or [] if m
	)
	doc._issues_cache = issues


def write_bom(doc, lines):
	"""Rewrite the ``Auto`` lines from the derive; keep every ``Manual`` line, after them."""
	manual = [r for r in doc.get("bom") or [] if (getattr(r, "source", None) or "Manual") == "Manual"]
	kept = [{f: getattr(r, f, None) for f in LINE_FIELDS} for r in manual]
	doc.set("bom", [])
	for line in lines or []:
		doc.append("bom", {f: line.get(f) for f in LINE_FIELDS} | {"source": "Auto"})
	for row in kept:
		row["amount"] = flt(row.get("qty")) * flt(row.get("rate"))
		doc.append("bom", row | {"source": "Manual"})


def write_derive(doc, out):
	"""Everything one derive produced, onto the record."""
	model = out.get("model") or {}
	rollups = out.get("rollups") or {}
	doc.model_json = json.dumps(model)
	doc.results_json = json.dumps({k: out.get(k) for k in RESULT_KEYS})
	for key, field in ROLLUP_FIELDS.items():
		doc.set(field, rollups.get(key) or 0)
	doc.selected_pump = rollups.get("selected_pump") or None
	features = model.get("features") or []
	site = model.get("site") or {}
	doc.family = (features[0].get("family") if features else None) or ""
	doc.venue_type = site.get("venue_type") or ""
	doc.governing_code = site.get("governing_code") or ""
	doc.kit = kits_engine.match_kit(model) if model.get("kit") else ""
	doc.mode = "Full" if str(model.get("mode") or "").lower() == "full" else "Quick"
	write_bom(doc, out.get("bom") or [])
	# The BOM's sum with the Manual lines, and the budget the Budget print shows: that sum plus the
	# house-rule contingency (prints.budget_summary, so the record and the paper are one figure).
	rows = [
		{f: (r.get(f) if isinstance(r, dict) else getattr(r, f, None)) for f in LINE_FIELDS}
		for r in doc.get("bom") or []
	]
	summary = prints.budget_summary(rows, (out.get("paper") or {}).get("budget_rules") or {})
	doc.bom_subtotal = summary["subtotal"]
	doc.budget_total = summary["total"]
	readiness = out.get("readiness") or {}
	write_issue_state(doc, out.get("issues") or [], readiness)
	if (doc.get("status") or "Draft") in ("Draft", "Derived"):
		doc.status = "Derived" if readiness.get("calc_ready") else "Draft"


def compute_completion_percent(doc):
	"""0-100 from the readiness the last derive wrote: the share of applicable sections done."""
	return design_issues.completion_from_readiness(_json_dict(doc.get("readiness_json")))
