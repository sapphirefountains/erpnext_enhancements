"""Per-DocType flow configuration: the registry and the schema every config is held to.

``CONFIGS`` is also the allowlist. ``form_kit.api`` serves only the DocTypes named here, so a
DocType reaches the guided flow by a reviewed PR that adds its module, never by a request.

A config is a plain dict (see ``opportunity.py`` for a full one):

``doctype``
    The DocType it walks.
``skip_fields``  ``{fieldname: reason}``
    Fields the flow never shows. Meant for HTML fields whose content the form's JavaScript
    draws. Every HTML field without static content must be listed, with its reason.
``show_hidden``  ``{fieldname: reason}``
    Fields the form hides that the flow shows anyway (a Link the form sets through a widget).
``read_only_fields``  ``{fieldname: reason}``
    Shown, never written by the flow.
``site_only_fields``  ``{fieldname}``
    Fieldnames above that exist on the production site but are not created by this repo.
``create``  ``{"fields": [...], "filled_on_create": {fieldname: reason}, "prefill_fields": {fieldname: reason}}``
    What step 1 asks for before the record exists. Any other required field without a default
    is added to step 1 at run time unless ``filled_on_create`` says how the new record fills it.
    A record started somewhere else (``frappe.new_doc``'s options, or a mapped document such as
    ERPNext's *Create > Opportunity* on a Customer) arrives with values of its own. The new
    record takes those for every field the flow lets the user write, and for the fields in
    ``prefill_fields``: fields the flow does not show that such a start fills (a hidden Link).
``lost``  ``{"status", "status_field"?, "method", "reasons": {"doctype", "field"}, "competitors": {...}}``
    Choosing ``status`` (in ``status_field``, default ``status``) opens a *Set as Lost* dialog
    and runs the DocType's own ``method`` with the reasons and competitors as rows of the given
    child DocTypes (``field`` is the row's link column), as the Desk form does.
``distinct``  ``[{"fields": [...], "message"}]``
    Fields that should not share a value: the page warns, as the form's script does, without
    blocking.
``clears``  ``{fieldname: [fieldnames]}``
    Changing the first empties the others on the page, as the form's script does (a party whose
    type changed).
``mine``  ``{"owner_field", "title_field", "detail_fields"?, "open"?, "hide_flag"?, "label"}``
    The guided page's start screen lists the caller's records: ``owner_field`` is the caller,
    ``open`` is ``{"field", "not_in": [...]}`` (closed records are left out), and a record whose
    ``hide_flag`` Check is set is left out too. ``label`` heads the list.
``steps``  ``[{key, title, intro, sections, fields?, applies?, read_only?, final?, link?}]``
    In order. ``sections`` are Section Break fieldnames (``_top`` for the fields before the first
    one; a tab's fieldname for the fields that open a tab before any Section Break). ``fields``
    moves single fields into the step from wherever they sit. ``applies`` is
    ``{"field", "row_field"?, "in": [values]}``: the step is offered while the field's value (or,
    for a table, any row's ``row_field``) is one of ``in``. ``final`` keeps the step last, after
    any section no step claims (those are walked as steps of their own, so nothing the form
    shows is ever lost). ``link`` points at a part of the Desk form the flow cannot draw.
``stages``  ``{"field", "default", "lists": {stage: [requirement, ...]}}``
    Completeness per stage; see ``form_kit.completeness``.
"""

from erpnext_enhancements.form_kit.config import opportunity

CONFIGS = {"Opportunity": opportunity.CONFIG}

STEP_KEYS = {"key", "title", "intro", "sections", "fields", "applies", "read_only", "final", "link"}
CONFIG_KEYS = {
	"doctype",
	"skip_fields",
	"show_hidden",
	"read_only_fields",
	"site_only_fields",
	"create",
	"steps",
	"stages",
	"mine",
	"lost",
	"distinct",
	"clears",
}
CREATE_KEYS = {"fields", "filled_on_create", "prefill_fields"}
MINE_KEYS = {"owner_field", "title_field", "detail_fields", "open", "hide_flag", "label"}
REQUIREMENT_KINDS = ("field", "any", "chosen_rows")


def get_config(doctype):
	"""The config for ``doctype``, or None when the flow does not serve it."""
	return CONFIGS.get(doctype)


def requirement_fieldnames(requirement):
	"""Every fieldname a completeness requirement reads, the tables' row columns excluded."""
	if "field" in requirement:
		return [requirement["field"]]
	if "any" in requirement:
		return list(requirement["any"])
	chosen = requirement["chosen_rows"]
	return [chosen["field"]] + [table for table, _column in chosen["tables"].values()]


def check(config):
	"""Structural problems with a config, as a list of sentences (empty when it is sound).

	Checks shape only; ``tests/test_form_kit.py`` also checks every fieldname against the
	DocType's merged meta.
	"""
	problems = []
	unknown = set(config) - CONFIG_KEYS
	if unknown:
		problems.append(f"unknown config keys {sorted(unknown)}")
	for key in ("doctype", "steps", "create", "stages"):
		if not config.get(key):
			problems.append(f"missing {key}")
	for key in ("skip_fields", "show_hidden", "read_only_fields"):
		for fieldname, reason in (config.get(key) or {}).items():
			if not str(reason or "").strip():
				problems.append(f"{key}.{fieldname} has no reason")
	create = config.get("create") or {}
	unknown = set(create) - CREATE_KEYS
	if unknown:
		problems.append(f"unknown create keys {sorted(unknown)}")
	for key in ("filled_on_create", "prefill_fields"):
		for fieldname, reason in (create.get(key) or {}).items():
			if not str(reason or "").strip():
				problems.append(f"create.{key}.{fieldname} has no reason")
	for index, rule in enumerate(config.get("distinct") or []):
		if len(rule.get("fields") or []) < 2 or not str(rule.get("message") or "").strip():
			problems.append(f"distinct rule {index} needs two or more fields and a message")
	for fieldname, cleared in (config.get("clears") or {}).items():
		if not isinstance(cleared, list) or not cleared:
			problems.append(f"clears.{fieldname} needs a list of fields")
	lost = config.get("lost")
	if lost is not None:
		for key in ("status", "method"):
			if not str(lost.get(key) or "").strip():
				problems.append(f"lost needs {key}")
		for key in ("reasons", "competitors"):
			table = lost.get(key) or {}
			if not table.get("doctype") or not table.get("field"):
				problems.append(f"lost.{key} needs a doctype and a field")
	mine = config.get("mine")
	if mine is not None:
		unknown = set(mine) - MINE_KEYS
		if unknown:
			problems.append(f"unknown mine keys {sorted(unknown)}")
		for key in ("owner_field", "title_field", "label"):
			if not str(mine.get(key) or "").strip():
				problems.append(f"mine needs {key}")
		open_rule = mine.get("open")
		if open_rule is not None and (not open_rule.get("field") or not isinstance(open_rule.get("not_in"), list)):
			problems.append("mine.open needs a field and a 'not_in' list")
	seen_keys, seen_sections, seen_fields = set(), set(), set()
	for index, step in enumerate(config.get("steps") or []):
		key = step.get("key")
		if not key:
			problems.append(f"step {index} has no key")
		elif key in seen_keys:
			problems.append(f"step key {key} is used twice")
		seen_keys.add(key)
		extra = set(step) - STEP_KEYS
		if extra:
			problems.append(f"step {key} has unknown keys {sorted(extra)}")
		if not str(step.get("title") or "").strip():
			problems.append(f"step {key} has no title")
		if not str(step.get("intro") or "").strip():
			problems.append(f"step {key} has no intro")
		if not step.get("sections") and not step.get("fields"):
			problems.append(f"step {key} has neither sections nor fields")
		for section in step.get("sections") or []:
			if section in seen_sections:
				problems.append(f"section {section} is claimed by two steps")
			seen_sections.add(section)
		for fieldname in step.get("fields") or []:
			if fieldname in seen_fields:
				problems.append(f"field {fieldname} is moved by two steps")
			seen_fields.add(fieldname)
		applies = step.get("applies")
		if applies is not None and (not applies.get("field") or not isinstance(applies.get("in"), list)):
			problems.append(f"step {key} applies rule needs a field and an 'in' list")
	finals = [step.get("key") for step in config.get("steps") or [] if step.get("final")]
	if finals and finals != [(config.get("steps") or [{}])[-1].get("key")]:
		problems.append("only the last step may be final")
	stages = config.get("stages") or {}
	if not stages.get("field") or not stages.get("lists"):
		problems.append("stages needs a field and lists")
	if stages.get("default") and stages.get("default") not in (stages.get("lists") or {}):
		problems.append("stages.default is not one of the lists")
	for stage, requirements in (stages.get("lists") or {}).items():
		for requirement in requirements:
			kinds = [kind for kind in REQUIREMENT_KINDS if kind in requirement]
			if len(kinds) != 1:
				problems.append(f"{stage}: a requirement needs exactly one of {REQUIREMENT_KINDS}")
			if not str(requirement.get("label") or "").strip():
				problems.append(f"{stage}: a requirement has no label")
	return problems
