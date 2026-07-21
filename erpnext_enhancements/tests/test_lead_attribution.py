"""Bench-free guards for Lead/Opportunity attribution fixtures and hooks.

The bug these cover is not "the code is wrong" but "the code writes to a field
that does not exist, and nothing says so". Frappe drops unknown attributes on
save, and a Property Setter for a missing field is silently ignored — so neither
failure produced an error, a log line, or a visible symptom for years.

A unit test cannot catch that on its own (the whole point is that the write
succeeds). What it CAN do is assert the two invariants that make the silence
impossible to reintroduce:

  * every field the hooks write is declared as a Custom Field in the fixtures;
  * no fixture targets ``source`` on Lead or Opportunity, which no longer exists.

Plain pytest, filesystem + AST only — no bench, no frappe import. Runs in CI.
"""

import ast
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def _custom_fields():
	with open(FIXTURES / "custom_field.json", encoding="utf-8") as fh:
		return json.load(fh)


def _property_setters():
	with open(FIXTURES / "property_setter.json", encoding="utf-8") as fh:
		return json.load(fh)


def _field_names(doctype):
	return {c["fieldname"] for c in _custom_fields() if c.get("dt") == doctype}


# --- the removed `source` field ---------------------------------------------


def test_no_fixture_targets_the_removed_source_field():
	"""ERPNext v15 renamed Lead/Opportunity `source` to `utm_source`.

	Anything still pointing at `source` is inert — it fails OPEN, with no error
	and no symptom, which is how three of these survived for years.
	"""
	offenders = [
		ps["name"]
		for ps in _property_setters()
		if ps.get("doc_type") in ("Lead", "Opportunity") and ps.get("field_name") == "source"
	]
	assert not offenders, (
		"property setters target the removed 'source' field (renamed utm_source in v15): "
		+ ", ".join(offenders)
	)


def test_no_custom_field_anchors_on_the_removed_source_field():
	"""A Custom Field whose insert_after names a missing field is stranded at the
	bottom of the last tab — the v1.159.0 bug."""
	offenders = [
		c["name"]
		for c in _custom_fields()
		if c.get("dt") in ("Lead", "Opportunity") and c.get("insert_after") == "source"
	]
	assert not offenders, "custom fields anchored on the removed 'source' field: " + ", ".join(offenders)


# --- the attribution fields exist -------------------------------------------


def test_attribution_fields_are_declared():
	"""The three fields the hooks and the patch depend on."""
	lead = _field_names("Lead")
	opportunity = _field_names("Opportunity")

	assert "custom_lead_source" in lead
	assert "custom_opportunity" in lead
	assert "custom_lead_source" in opportunity


def test_lead_source_fields_point_at_the_populated_taxonomy():
	"""Lead Source, not UTM Source.

	Both lists carry the same 22 members, but only Lead Source is populated on
	this site (~694 Customers via Customer.custom_lead_source). Pointing the new
	fields at UTM Source would have split attribution across two identical lists.
	"""
	for doctype in ("Lead", "Opportunity"):
		field = next(
			c for c in _custom_fields()
			if c.get("dt") == doctype and c.get("fieldname") == "custom_lead_source"
		)
		assert field["fieldtype"] == "Link", f"{doctype}.custom_lead_source should be a Link"
		assert field["options"] == "Lead Source", (
			f"{doctype}.custom_lead_source points at {field['options']!r}, expected 'Lead Source' "
			"(Customer.custom_lead_source uses Lead Source and is the populated one)"
		)


def test_lead_source_is_not_mandatory():
	"""The orphan setters intended reqd=1 but never took effect, so no existing
	record has a source. Making it mandatory now would block every save of the
	~200 existing Leads until someone backfilled them."""
	for doctype in ("Lead", "Opportunity"):
		field = next(
			c for c in _custom_fields()
			if c.get("dt") == doctype and c.get("fieldname") == "custom_lead_source"
		)
		assert not field.get("reqd"), f"{doctype}.custom_lead_source must not be mandatory yet"


def test_lead_opportunity_backlink_is_read_only():
	"""It is derived from Opportunity.party_name by a hook — hand-editing it
	would desynchronise the two directions."""
	field = next(
		c for c in _custom_fields()
		if c.get("dt") == "Lead" and c.get("fieldname") == "custom_opportunity"
	)
	assert field["fieldtype"] == "Link"
	assert field["options"] == "Opportunity"
	assert field.get("read_only"), "Lead.custom_opportunity should be read-only"


def test_every_new_field_anchors_on_something_that_exists():
	"""insert_after must resolve, or the field is stranded at the bottom of the
	last tab. Anchors on OUR custom fields are checkable here; stock anchors are
	verified against a real bench."""
	fields = _custom_fields()
	for doctype, fieldname in (
		("Lead", "custom_lead_source"),
		("Lead", "custom_opportunity"),
		("Opportunity", "custom_lead_source"),
	):
		field = next(c for c in fields if c["dt"] == doctype and c["fieldname"] == fieldname)
		anchor = field["insert_after"]
		if not anchor.startswith("custom_"):
			continue
		assert any(c["dt"] == doctype and c["fieldname"] == anchor for c in fields), (
			f"{doctype}-{fieldname} anchors on {anchor!r}, which is not a declared custom field"
		)


# --- the hook writes the field that exists ----------------------------------


def _assigned_attributes(source, function_name, target):
	"""Attribute names assigned on ``target`` inside ``function_name``."""
	tree = ast.parse(source)
	function = next(
		node for node in ast.walk(tree)
		if isinstance(node, ast.FunctionDef) and node.name == function_name
	)
	found = set()
	for node in ast.walk(function):
		if not isinstance(node, ast.Assign):
			continue
		for lhs in node.targets:
			if (
				isinstance(lhs, ast.Attribute)
				and isinstance(lhs.value, ast.Name)
				and lhs.value.id == target
			):
				found.add(lhs.attr)
	return found


def test_update_lead_status_writes_a_field_that_exists():
	"""The original bug, made impossible to reintroduce silently.

	`lead_doc.opportunity = doc.name` raised nothing and persisted nothing.
	Anything this hook assigns to the Lead must be a real field — either stock
	(`status`) or one we ship.
	"""
	source = (ROOT / "script_migrations" / "opportunity.py").read_text(encoding="utf-8")
	assigned = _assigned_attributes(source, "update_lead_status", "lead_doc")

	assert "custom_opportunity" in assigned, "the back-link is no longer being written"
	assert "opportunity" not in assigned, (
		"update_lead_status assigns lead_doc.opportunity — Lead has no such field, "
		"and frappe drops unknown attributes silently"
	)

	stock_lead_fields = {"status"}
	declared = _field_names("Lead")
	for attribute in assigned:
		assert attribute in stock_lead_fields or attribute in declared, (
			f"update_lead_status writes lead_doc.{attribute}, which is neither a known stock "
			"field nor a Custom Field in the fixtures — it would be silently dropped"
		)


# --- the patches are registered ---------------------------------------------


def test_patches_are_registered_and_documented():
	"""A patch file that is not in patches.txt never runs."""
	patches_txt = (ROOT / "patches.txt").read_text(encoding="utf-8")
	readme = (ROOT / "patches" / "README.md").read_text(encoding="utf-8")

	for patch in ("drop_orphan_source_property_setters", "backfill_lead_opportunity_link"):
		assert (ROOT / "patches" / f"{patch}.py").exists(), f"{patch}.py missing"
		assert f"erpnext_enhancements.patches.{patch}" in patches_txt, (
			f"{patch} is not registered in patches.txt — it would never run"
		)
		assert patch in readme, f"{patch} missing from the patches README index"


def test_orphan_patch_targets_exactly_the_three_dead_setters():
	source = (ROOT / "patches" / "drop_orphan_source_property_setters.py").read_text(encoding="utf-8")
	for name in ("Lead-source-reqd", "Opportunity-source-reqd", "Lead-source-label"):
		assert name in source, f"{name} not handled by the cleanup patch"
