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


# ============================================================================
# WP-1 attribution pipeline (v1.241.0)
# ============================================================================
#
# The invariants below are the ones that fail SILENTLY if broken, which is the
# same class of bug the suite above was written for. In particular: writing a
# real campaign name into erpnext's own `utm_source` would not raise anything —
# it would quietly start minting duplicate Contacts, because Lead.before_insert
# only suppresses its automatic Contact when utm_source == "Existing Customer".

ATTRIBUTION_DOCTYPES = ("Lead", "Opportunity", "Customer")

#: Every raw capture field the pipeline ships, per doctype.
ATTRIBUTION_FIELDNAMES = (
	"custom_attribution_section",
	"custom_utm_source",
	"custom_utm_medium",
	"custom_utm_campaign",
	"custom_utm_content",
	"custom_utm_term",
	"custom_gclid",
	"custom_landing_page",
	"custom_first_referrer",
	"custom_attribution_captured_on",
)

#: erpnext's own UTM fields. Ours must never collide with these.
CORE_UTM_FIELDS = ("utm_source", "utm_medium", "utm_campaign", "utm_content")


def _attribution_source():
	return (ROOT / "crm_enhancements" / "attribution.py").read_text(encoding="utf-8")


def _non_docstring_strings(source):
	"""Every string constant in ``source`` that is not a docstring."""
	tree = ast.parse(source)
	docstrings = set()
	for node in ast.walk(tree):
		if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
			body = getattr(node, "body", None)
			if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
				if isinstance(body[0].value.value, str):
					docstrings.add(id(body[0].value))
	return [
		node.value
		for node in ast.walk(tree)
		if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
	]


def test_attribution_fields_exist_on_all_three_doctypes():
	"""Propagation is only as good as its weakest hop. A field missing on
	Customer means the value-stream dashboards — which group by Customer — never
	see attribution at all."""
	for doctype in ATTRIBUTION_DOCTYPES:
		declared = _field_names(doctype)
		for fieldname in ATTRIBUTION_FIELDNAMES:
			assert fieldname in declared, f"{doctype} is missing {fieldname}"


def test_attribution_section_is_collapsed():
	"""It is evidence, not something anyone edits. An expanded section pushes the
	fields people DO edit below the fold on every Lead."""
	for doctype in ATTRIBUTION_DOCTYPES:
		section = next(
			c for c in _custom_fields()
			if c.get("dt") == doctype and c.get("fieldname") == "custom_attribution_section"
		)
		assert section["fieldtype"] == "Section Break"
		assert section.get("collapsible"), f"{doctype} attribution section should be collapsible"


def test_capture_timestamp_is_read_only():
	"""Derived by the hook. A hand-edit would misdate the acquisition, which is
	the one thing the field exists to be trusted about."""
	for doctype in ATTRIBUTION_DOCTYPES:
		field = next(
			c for c in _custom_fields()
			if c.get("dt") == doctype and c.get("fieldname") == "custom_attribution_captured_on"
		)
		assert field["fieldtype"] == "Datetime"
		assert field.get("read_only"), f"{doctype}.custom_attribution_captured_on must be read-only"


def test_raw_utm_fields_are_data_not_link():
	"""erpnext's utm_medium/utm_campaign are Links into taxonomies. Raw capture
	has to accept whatever string is in the URL, so ours must be free text — a
	Link would either reject the submission or spawn junk taxonomy rows."""
	free_text = {"custom_utm_source", "custom_utm_medium", "custom_utm_campaign", "custom_utm_content", "custom_utm_term", "custom_gclid"}
	for doctype in ATTRIBUTION_DOCTYPES:
		for fieldname in free_text:
			field = next(
				c for c in _custom_fields()
				if c.get("dt") == doctype and c.get("fieldname") == fieldname
			)
			assert field["fieldtype"] == "Data", f"{doctype}.{fieldname} must be Data, got {field['fieldtype']}"
			assert not field.get("options"), f"{doctype}.{fieldname} must not carry Link options"


def test_no_fixture_shadows_an_erpnext_utm_field():
	"""A Custom Field named exactly `utm_source` would collide with the stock
	DocField. Ours are all `custom_`-prefixed; assert it rather than trusting it."""
	for doctype in ATTRIBUTION_DOCTYPES:
		declared = _field_names(doctype)
		for core in CORE_UTM_FIELDS:
			assert core not in declared, f"{doctype} declares a Custom Field shadowing stock {core}"


def test_attribution_module_never_writes_erpnext_utm_fields():
	"""The load-bearing one.

	`Lead.before_insert` mints a second, stray Contact for every new Lead unless
	`utm_source` is exactly "Existing Customer" and `customer` is set — which is
	how fountain_move/conversion.py suppresses it. Writing a campaign name into
	`utm_source` breaks that suppression and starts duplicating Contacts, with no
	error and no symptom until somebody notices the Contact list has doubled.
	"""
	literals = set(_non_docstring_strings(_attribution_source()))
	for core in CORE_UTM_FIELDS:
		assert core not in literals, (
			f"attribution.py references the bare erpnext field {core!r} outside a docstring — "
			"it must only ever write its own custom_ fields (see the module docstring)"
		)


def test_every_captured_field_is_custom_prefixed():
	"""ATTRIBUTION_FIELDS drives both the fixtures and the ingress allowlist. A
	bare fieldname sneaking in there is the same collision as above."""
	tree = ast.parse(_attribution_source())
	assignment = next(
		node for node in tree.body
		if isinstance(node, ast.Assign)
		and getattr(node.targets[0], "id", None) == "ATTRIBUTION_FIELDS"
	)
	names = [element.value for element in assignment.value.elts]
	assert names, "ATTRIBUTION_FIELDS is empty"
	for name in names:
		assert name.startswith("custom_"), f"{name} is not a custom field"


def test_fill_blanks_is_the_only_attribution_writer():
	"""First-touch-wins has to have exactly one implementation.

	Every write of an attribution field onto a document goes through
	`_fill_blanks`. If a second function starts calling `target.set(...)` in a
	loop over the field list, the "never overwrite" rule has two places to be got
	wrong and one of them will be.
	"""
	tree = ast.parse(_attribution_source())
	writers = set()
	for function in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
		for node in ast.walk(function):
			if not isinstance(node, ast.Call):
				continue
			func = node.func
			if isinstance(func, ast.Attribute) and func.attr == "set" and isinstance(func.value, ast.Name):
				writers.add(function.name)
	assert writers <= {"_fill_blanks", "stamp_capture_time"}, (
		f"unexpected attribution writers: {sorted(writers - {'_fill_blanks', 'stamp_capture_time'})} — "
		"every field copy must go through _fill_blanks so first-touch-wins has one implementation"
	)


def test_source_gate_is_not_a_reqd_flag():
	"""reqd=1 would break every API-created record and retroactively invalidate
	the historical backlog. The gate is a settings-toggled validate hook."""
	for doctype in ("Lead", "Opportunity"):
		field = next(
			c for c in _custom_fields()
			if c.get("dt") == doctype and c.get("fieldname") == "custom_lead_source"
		)
		assert not field.get("reqd"), f"{doctype}.custom_lead_source must not be reqd"
		assert not field.get("mandatory_depends_on"), (
			f"{doctype}.custom_lead_source must not use mandatory_depends_on — the gate is "
			"attribution.enforce_source, which exempts existing records and bulk contexts"
		)


def test_source_gate_exempts_existing_records_and_bulk_contexts():
	"""Two exemptions, both load-bearing: enforcing on every save would block
	edits to the ~1,000 historical records, and throwing during migrate/import
	would abort the migration itself."""
	source = _attribution_source()
	tree = ast.parse(source)
	gate = next(
		node for node in ast.walk(tree)
		if isinstance(node, ast.FunctionDef) and node.name == "enforce_source"
	)
	body = ast.dump(gate)
	assert "is_new" in body, "enforce_source must exempt existing records"
	assert "_bulk_context" in body, "enforce_source must exempt import/migrate/patch/install/test"


# --- settings toggles -------------------------------------------------------


def _settings_fieldnames():
	path = ROOT / "enhancements_core" / "doctype" / "erpnext_enhancements_settings" / "erpnext_enhancements_settings.json"
	with open(path, encoding="utf-8") as fh:
		return {f["fieldname"] for f in json.load(fh)["fields"]}


def test_every_settings_flag_the_pipeline_reads_exists():
	"""A hook gated on a field that does not exist reads as falsy forever — the
	feature ships permanently off and nothing says so."""
	declared = _settings_fieldnames()
	for flag in (
		"lead_attribution_enabled",
		"require_lead_source_on_lead",
		"require_lead_source_on_opportunity",
		"web_lead_ingress_enabled",
		"web_lead_default_owner",
		"web_lead_shared_secret",
	):
		assert flag in declared, f"{flag} is read by the attribution pipeline but not declared in Settings"


def test_ingress_secret_is_a_password_field():
	"""A Data field would render the shared secret back onto the form in
	cleartext — the same exposure patches.encrypt_drive_service_account_json was
	written to undo."""
	path = ROOT / "enhancements_core" / "doctype" / "erpnext_enhancements_settings" / "erpnext_enhancements_settings.json"
	with open(path, encoding="utf-8") as fh:
		fields = {f["fieldname"]: f for f in json.load(fh)["fields"]}
	assert fields["web_lead_shared_secret"]["fieldtype"] == "Password"


# --- the public ingress endpoint --------------------------------------------


def _web_lead_source():
	return (ROOT / "crm_enhancements" / "web_lead.py").read_text(encoding="utf-8")


def test_ingress_is_rate_limited_and_guest_scoped():
	source = _web_lead_source()
	assert "allow_guest=True" in source
	assert 'methods=["POST"]' in source, "the endpoint must not accept GET"
	assert "@rate_limit(" in source, "an unauthenticated endpoint must be rate limited"


def test_ingress_fails_closed_without_a_secret():
	"""An unset secret must authorise nobody. The opposite — treating 'no secret
	configured' as 'no check required' — is how a half-configured site becomes
	world-writable."""
	tree = ast.parse(_web_lead_source())
	authorized = next(
		node for node in ast.walk(tree)
		if isinstance(node, ast.FunctionDef) and node.name == "_authorized"
	)
	assert "compare_digest" in ast.dump(authorized), "the Bearer check must be constant-time"
	assert "if not secret" in _web_lead_source(), "_authorized must return False when no secret is set"


def test_ingress_never_splats_the_payload_into_the_document():
	"""Guest inserts require ignore_permissions=True, under which frappe's
	permlevel check returns early — so `read_only` in a DocType JSON stops
	nothing and a splatted payload could set `status` or an owner link directly.
	The allowlist is the only real control."""
	source = _web_lead_source()
	assert "LEAD_FIELD_MAP" in source, "the field allowlist is missing"

	tree = ast.parse(source)
	handler = next(
		node for node in ast.walk(tree)
		if isinstance(node, ast.FunctionDef) and node.name == "submit_web_lead"
	)
	# `{**something}` parses as a Dict with a None key. Anything unpacked into a
	# document dict must be a value WE built from the allowlist, never the raw
	# inbound payload.
	for node in ast.walk(handler):
		if not isinstance(node, ast.Dict):
			continue
		for key, value in zip(node.keys, node.values):
			if key is not None:
				continue
			assert isinstance(value, ast.Name) and value.id != "payload", (
				"submit_web_lead unpacks the raw payload into a document — under "
				"ignore_permissions=True that is a mass-assignment hole"
			)


# --- report -----------------------------------------------------------------


def test_attribution_gaps_report_has_explicit_roles():
	"""Nothing defaults to All. A report over every unattributed deal in the
	pipeline is not something every logged-in user should be able to open."""
	path = ROOT / "crm_enhancements" / "report" / "attribution_gaps" / "attribution_gaps.json"
	with open(path, encoding="utf-8") as fh:
		report = json.load(fh)
	roles = {r["role"] for r in report.get("roles", [])}
	assert roles, "Attribution Gaps has no roles — it would default to All"
	assert "All" not in roles
	assert report["module"] == "CRM Enhancements"


# --- the backfill patch -----------------------------------------------------


def test_backfill_patch_is_registered():
	patches_txt = (ROOT / "patches.txt").read_text(encoding="utf-8")
	assert (ROOT / "patches" / "backfill_unknown_lead_source.py").exists()
	assert "erpnext_enhancements.patches.backfill_unknown_lead_source" in patches_txt


def test_backfill_patch_is_idempotent_and_bounded():
	"""Two properties that matter more than they look.

	*Idempotent*: the UPDATE is filtered to blank values, so a second run matches
	nothing. Patches are re-run on fresh installs and after failed migrates.

	*Bounded by the cutoff*: the bucket is labelled "pre-Aug 2026" and that has to
	stay true. Stamping it on a record created after capture went in would hide a
	live process failure behind a historical label.
	"""
	source = (ROOT / "patches" / "backfill_unknown_lead_source.py").read_text(encoding="utf-8")
	assert "custom_lead_source IS NULL OR custom_lead_source = ''" in source, (
		"the UPDATE must be filtered to blank values or it is not idempotent"
	)
	assert "creation < %(cutoff)s" in source, "the backfill must be bounded by the cutoff date"
	assert "CUTOFF" in source
