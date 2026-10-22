"""Contract Builder: a contract filled in one step at a time, then signed or sent.

Nik, 2026-10-13: "the Maintenance Contract and all Contract generators should be easy
to fill out and preview … an interface that is a walkthrough of the contract fields
… so we can basically build it alone or even with the client themselves … with a
fully generated contract at the end and the digital esignature capture we can do
right there or send to a customer to have them sign at their pace."

The desk page (``project_enhancements/page/contract_builder``) is the walkthrough;
this module is everything it reads and writes. Three choices keep it from becoming
a second contract system beside the one that exists:

* **The steps are the doctype's own sections.** :func:`builder_steps` walks the
  Project Contract meta and keeps the sections that apply to the contract's type,
  exactly as the form's ``depends_on`` would show them. A field added to the
  doctype appears in the builder with no change here; only the friendly title and
  one line of guidance per step (:data:`STEP_COPY`) are written by hand.
* **It edits a real Project Contract draft.** :func:`save_builder` assigns the
  fields the steps expose and calls ``save()``, so validation, totals, the
  revision model and the form all see the same document. No parallel draft store.
* **Signing is the e-sign module's.** Signing with the client beside you is
  ``esign.api.sign_in_person`` (:func:`sign_here`), handing them another device is
  ``esign.api.start_in_person_signing``, and "Send to the client" is
  ``esign.api.send_for_signature``; this module only issues (submits) the contract
  first, because a contract must be submitted before it can be signed.

Nik, 2026-10-13, on the first version: "the interface is not good. Let's make the flow a
lot better looking than that and smoother transitions and highlighting where they are at
in the agreement process as they go through it and let them initial and sign as it gets
filled out with information." So the payload also says where each field prints in the
agreement (:func:`agreement_sections`), which the page uses to light up the part of the
agreement the current step is filling in, and how far through the agreement is
(:func:`progress`).

The preview is ``project_contract.get_contract_html`` with the builder's unsaved
values, the same renderer as the form's Preview button and the printed PDF.
"""

import html
import json
import re

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, flt

from erpnext_enhancements.feature_flags import (
	contract_esign_enabled,
	contract_esign_public_page_enabled,
	throw_if_contract_esign_disabled,
)

DOCTYPE = "Project Contract"

#: Fields the builder never shows: naming, workflow state, and values the controller
#: stamps (party name, title, revision) or the signing flow records (signed by/on).
SKIP_FIELDS = {
	"naming_series",
	"contract_template",
	"template_key",
	"status",
	"revision",
	"amended_from",
	"title",
	"party_display",
	"signed_on",
	"signed_by",
}

#: Sections the builder leaves to the review step: totals are computed, and the signing
#: block is filled in by signing, not by typing.
SKIP_SECTIONS = {"totals_section"}

#: Child tables whose rows are a fixed menu (one row per plan option or phase). The page
#: shows them as tick-and-price cards and never adds or removes rows.
FIXED_ROW_TABLES = {"service_options", "phases"}

LAYOUT_TYPES = {"Section Break", "Column Break", "Tab Break"}
NO_VALUE_TYPES = LAYOUT_TYPES | {"HTML", "Button", "Heading", "Image", "Fold"}

#: ``(title, guidance)`` per section, by fieldname; translated when a payload is built, not
#: at import. A section with no entry here shows its own label and no guidance, so a new
#: section is never missing from the walk.
STEP_COPY = {
	"parties_section": (
		"Who it's with",
		"The client or subcontractor, how to reach them, and where the work is. Most of this "
		"comes from the project; check it reads right.",
	),
	"details_section": (
		"The job",
		"What the work is, in plain words, and about the property. This prints near the top of "
		"the agreement.",
	),
	"maintenance_section": (
		"Plan and schedule",
		"Tick the services they want and set each price. Standard Maintenance is the base rate "
		"per visit; chemicals are always billed on top.",
	),
	"maint_rates_section": (
		"Rates and renewal",
		"Emergency rates, business hours and the yearly increase at renewal. The standards are "
		"$250 an hour, $350 an hour after hours, and 12%.",
	),
	"maint_fees_section": (
		"Billing and payment",
		"How often they are invoiced and how they pay. A card is optional, but one payment "
		"method has to be on file.",
	),
	"rental_section": ("Rental dates and equipment", "When the rental runs and what goes out."),
	"rental_fees_section": ("Rental fees", "Every charge on the rental. The total adds itself up."),
	"owner_section": (
		"Services",
		"Which phases this contract covers, each with its fee and what is due at signing.",
	),
	"design_section": ("Design fees and timeline", ""),
	"construction_section": ("Construction schedule", ""),
	"milestones_section": ("Payment milestones", "When each payment falls due, and for what."),
	"msa_section": ("Subcontractor tier", ""),
	"section_msa_term": (
		"Term",
		"How long the agreement runs from the day it is signed: twelve months unless you set another term, or an "
		"end date of its own. It prints in the information table and Section 17.4.",
	),
	"section_msa_rates": (
		"Rate schedule",
		"The rates Statements of Work under this agreement are billed at (Exhibit D). Each SOW keeps its own copy "
		"of the rates on the day it is issued; a later change here never rewrites one.",
	),
	"sow_section": ("The engagement", "Which master agreement it sits under, and the dates."),
	"scope_section": (
		"Scope of work",
		"What the work covers. A Statement of Work starts from the project's requests and deliverables; edit "
		"freely.",
	),
	"architect_section": (
		"Notices and Statement of Work",
		"Who notices go to on each side, and the Statement of Work: what the Architect provides, its term, and "
		"how Sapphire is paid.",
	),
	"sow_services_section": ("Included services", "Tick each kind of work the subcontractor is doing on this job."),
	"sow_deliverables_section": (
		"Deliverables",
		"When each deliverable is due. Accepted By is signed by hand when each one is accepted.",
	),
	"sow_compensation_section": (
		"Compensation",
		"How the work is priced, the retention held back, and when invoices are due.",
	),
	"tm_section": ("Time and materials rates", "Only if any of the work is billed by the hour."),
	"sow_terms_section": (
		"Safety, quality and special terms",
		"Site safety rules, days to fix rejected work, the inspections when the project has no locked Scope of "
		"Work, and any terms that change the master agreement for this job.",
	),
	"supervisor_section": ("Site supervisor", ""),
}

_TEMPLATE_KEY_EQ = re.compile(r"""doc\.template_key\s*==\s*["']([a-z_]+)["']""")
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
_DOC_FIELD = re.compile(r"\bdoc\.([a-z_][a-z0-9_]*)")

#: Fields an agreement prints only when they are filled in, so an empty one is not a blank line: the
#: MSA's own end date (otherwise the term in months prints), a SOW's special terms.
PRINTED_ONLY_WHEN_SET = {"msa_expires_on", "sow_special_terms"}

#: Child tables a template reads through a render-context name rather than ``doc.<table>``
#: (``project_contract._render_context``): the maintenance plan and the owner phases.
CONTEXT_TABLES = {"service_options": re.compile(r"\bservice_options\b"), "phases": re.compile(r"\bphases\b")}

#: Roles that may issue a contract and take its signature (esign.api.SEND_ROLES). Since v1.615.0 the
#: Contract Manager role: anyone with Projects Manager, Sales Manager or Sales User still builds and saves
#: drafts, and asks a Contract Manager to issue them (:func:`ask_contract_manager`).
SIGN_ROLES = {"System Manager", "Contract Manager"}
CONTRACT_MANAGER = "Contract Manager"

#: What each agreement calls the other side, for the walk's wording ("With the subcontractor",
#: "the architect's signature"). Anything not listed is a client agreement.
PARTY_NOUNS = {
	"msa": "subcontractor",
	"sow": "subcontractor",
	"architect": "architect",
	"employee_contractor": "employee-contractor",
	"nda": "company",
}


def party_noun(template_key):
	return PARTY_NOUNS.get(template_key or "", "client")


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def section_applies(depends_on, template_key):
	"""Whether a section's ``depends_on`` shows it for this contract type.

	Every section condition on Project Contract is an OR of ``doc.template_key=="x"``
	tests, so that is all this reads; a condition that does not mention the template
	key at all is left for the page to evaluate field by field, i.e. the section stays.
	"""
	if not depends_on or "template_key" not in depends_on:
		return True
	return template_key in set(_TEMPLATE_KEY_EQ.findall(depends_on))


def _field_def(df, template_party_type=None):
	"""The docfield as the page's FieldGroup wants it: only the keys that shape the control."""
	out = {
		key: df.get(key)
		for key in (
			"fieldname",
			"fieldtype",
			"label",
			"options",
			"description",
			"depends_on",
			"mandatory_depends_on",
			"read_only_depends_on",
			"reqd",
			"read_only",
			"precision",
			"bold",
		)
		if df.get(key) not in (None, "", 0)
	}
	# A contract type that fixes its party type (everything but NDA / employee-contractor)
	# shows it rather than offering a choice validate() would overwrite.
	if df.get("fieldname") == "party_type" and template_party_type and template_party_type != "Any Party":
		out["read_only"] = 1
	return out


def _child_fields(child_doctype):
	meta = frappe.get_meta(child_doctype)
	return [
		_field_def(df)
		| {"in_list_view": cint(df.get("in_list_view")), "hidden": cint(df.get("hidden"))}
		for df in meta.fields
		if df.fieldtype not in NO_VALUE_TYPES
	]


def builder_steps(template_key, template_party_type=None):
	"""The walk for one contract type: ``[{key, title, intro, fields}]``, then the review.

	Fields before the first section (the contract date) join the first step shown.
	"""
	meta = frappe.get_meta(DOCTYPE)
	sections = [{"key": "_header", "label": "", "depends_on": None, "fields": []}]
	for df in meta.fields:
		if df.fieldtype == "Section Break":
			sections.append({"key": df.fieldname, "label": df.label, "depends_on": df.depends_on, "fields": []})
			continue
		if df.fieldname in SKIP_FIELDS or cint(df.hidden) or df.fieldtype in ("Tab Break", "Button"):
			continue
		field = _field_def(df, template_party_type)
		if df.fieldtype == "Table":
			field["child_fields"] = _child_fields(df.options)
			field["fixed_rows"] = 1 if df.fieldname in FIXED_ROW_TABLES else 0
		sections[-1]["fields"].append(field)

	# Only the header's values join the first step; its column layout belonged to the fields
	# the builder skips.
	header = [f for f in sections.pop(0)["fields"] if f["fieldtype"] not in LAYOUT_TYPES]
	steps = []
	for section in sections:
		if section["key"] in SKIP_SECTIONS or not section_applies(section["depends_on"], template_key):
			continue
		if not any(
			f["fieldtype"] not in LAYOUT_TYPES and section_applies(f.get("depends_on"), template_key)
			for f in section["fields"]
		):
			continue
		title, intro = STEP_COPY.get(section["key"], (section["label"] or "Details", ""))
		steps.append(
			{
				"key": section["key"],
				"title": _(title),
				"intro": _(intro) if intro else "",
				"fields": _tidy_columns(section["fields"]),
			}
		)

	if steps and header:
		steps[0]["fields"] = header + [{"fieldtype": "Column Break", "fieldname": "_header_break"}] + steps[0]["fields"]
	return steps


def _tidy_columns(fields):
	"""Drop column breaks that would open an empty column: leading, trailing or doubled.

	Skipped fields leave their layout behind (the header's break once its fields are gone),
	and an empty column is a strange gap on a phone-width screen.
	"""
	out = []
	for field in fields:
		if field["fieldtype"] == "Column Break" and (not out or out[-1]["fieldtype"] == "Column Break"):
			continue
		out.append(field)
	while out and out[-1]["fieldtype"] == "Column Break":
		out.pop()
	return out


def _editable_fields(steps):
	"""``{fieldname: child allowed fields or None}`` — what save_builder may assign."""
	allowed = {}
	for step in steps:
		for field in step["fields"]:
			if field["fieldtype"] in NO_VALUE_TYPES or cint(field.get("read_only")):
				continue
			if field["fieldtype"] == "Table":
				allowed[field["fieldname"]] = [
					child["fieldname"] for child in field.get("child_fields") or []
				]
			else:
				allowed[field["fieldname"]] = None
	return allowed


# ---------------------------------------------------------------------------
# What will print blank
# ---------------------------------------------------------------------------

_CONDITION_TOKEN = re.compile(
	r"""\s*(?:(?P<field>doc\.[a-z_][a-z0-9_]*)|(?P<text>"[^"\\]*"|'[^'\\]*')|(?P<op>===|!==|==|!=|&&|\|\||!|\(|\)))"""
)


class _Unreadable(Exception):
	pass


def shown(depends_on, doc):
	"""Whether a field's ``depends_on`` shows it for ``doc``, as the form and the builder would.

	Reads the shapes this doctype's conditions use (``doc.x=="v"``, ``!=``, ``&&``, ``||``,
	``!``, parentheses, a bare ``doc.x`` or fieldname) with a small parser rather than ``eval``;
	anything else counts as shown. So a field that only appears for one choice (Other Property
	Type, the liquidated-damages rate) is not counted as a blank, or against progress, while
	that choice is not made: the page would offer no place to fill it.
	"""
	if not depends_on:
		return True
	if not depends_on.startswith("eval:"):
		return bool(doc.get(depends_on))
	tokens = []
	text, pos = depends_on[5:], 0
	while text[pos:].strip():
		match = _CONDITION_TOKEN.match(text, pos)
		if not match:
			return True
		tokens.append(match)
		pos = match.end()

	def value(match):
		if match.group("field"):
			return doc.get(match.group("field")[4:])
		if match.group("text"):
			return match.group("text")[1:-1]
		raise _Unreadable

	def parse_or(i):
		result, i = parse_and(i)
		while i < len(tokens) and tokens[i].group("op") == "||":
			right, i = parse_and(i + 1)
			result = bool(result) or bool(right)
		return result, i

	def parse_and(i):
		result, i = parse_unary(i)
		while i < len(tokens) and tokens[i].group("op") == "&&":
			right, i = parse_unary(i + 1)
			result = bool(result) and bool(right)
		return result, i

	def parse_unary(i):
		if i < len(tokens) and tokens[i].group("op") == "!":
			result, i = parse_unary(i + 1)
			return not result, i
		if i < len(tokens) and tokens[i].group("op") == "(":
			result, i = parse_or(i + 1)
			if i >= len(tokens) or tokens[i].group("op") != ")":
				raise _Unreadable
			return result, i + 1
		if i >= len(tokens):
			raise _Unreadable
		left = value(tokens[i])
		i += 1
		if i < len(tokens) and tokens[i].group("op") in ("==", "===", "!=", "!=="):
			equal = tokens[i].group("op") in ("==", "===")
			if i + 1 >= len(tokens):
				raise _Unreadable
			right = value(tokens[i + 1])
			left_text = "" if left is None else str(left)
			return (left_text == str(right)) == equal, i + 2
		return left, i

	try:
		result, end = parse_or(0)
	except _Unreadable:
		return True
	return bool(result) if end == len(tokens) else True


def blanks(doc, steps):
	"""Fields the agreement prints that are still empty, in walk order.

	Read from the contract's own template body (``doc.<field>`` references), so it lists
	what *this* agreement will show as a blank line, not every empty field on the form.
	Advisory: the paper form still works with a blank, and some (a gate code) may
	rightly stay empty.
	"""
	body = frappe.db.get_value("Contract Template", doc.get("contract_template"), "body") or ""
	referenced = set(re.findall(r"\bdoc\.([a-z_][a-z0-9_]*)", body))
	optional = _printed_only_when_set(doc)
	out = []
	for index, step in enumerate(steps):
		for field in step["fields"]:
			name = field.get("fieldname")
			if field["fieldtype"] in NO_VALUE_TYPES or name not in referenced:
				continue
			if field["fieldtype"] == "Table" or not shown(field.get("depends_on"), doc):
				continue
			if name in optional:
				continue
			value = doc.get(name)
			if value in (None, "") or (field["fieldtype"] in ("Currency", "Float", "Int", "Percent") and not flt(value)):
				out.append({"fieldname": name, "label": _(field.get("label") or name), "step": index})
	if doc.get("template_key") == "maintenance":
		for row in doc.get("service_options") or []:
			if cint(row.get("included")) and not flt(row.get("price")):
				out.append(
					{
						"fieldname": "service_options",
						"label": _("{0}: ticked but no price").format(row.get("option_label") or row.get("option_key")),
						"step": _step_index(steps, "service_options"),
					}
				)
	if not doc.get("contact_email"):
		out.append(
			{
				"fieldname": "contact_email",
				"label": _("Contact Email (needed to sign online; the signed copy is sent there)"),
				"step": _step_index(steps, "contact_email"),
			}
		)
	return out


def _printed_only_when_set(doc):
	"""``PRINTED_ONLY_WHEN_SET`` for this contract, plus the maintenance Annual Fee while it is blank and a
	Service Fee prints: the template then leaves the Annual Service Fee Total line out (v1.724.0)."""
	if doc.get("template_key") != "maintenance" or flt(doc.get("annual_maintenance_fee")):
		return PRINTED_ONLY_WHEN_SET
	from erpnext_enhancements.project_enhancements.maintenance_fee import period_fee_for

	if period_fee_for(doc):
		return PRINTED_ONLY_WHEN_SET | {"annual_maintenance_fee"}
	return PRINTED_ONLY_WHEN_SET


def _step_index(steps, fieldname):
	for index, step in enumerate(steps):
		if any(field.get("fieldname") == fieldname for field in step["fields"]):
			return index
	return 0


def progress(doc, steps, body):
	"""``{"filled", "total"}``: of the fields this agreement prints, how many have a value.

	The same reading of the template as :func:`blanks` (``doc.<field>`` in its body), so the
	bar fills as the blank lines in the preview disappear.
	"""
	referenced = set(_DOC_FIELD.findall(body or ""))
	optional = _printed_only_when_set(doc)
	total = filled = 0
	for step in steps:
		for field in step["fields"]:
			name = field.get("fieldname")
			if field["fieldtype"] in NO_VALUE_TYPES or field["fieldtype"] == "Table" or name not in referenced:
				continue
			if not shown(field.get("depends_on"), doc) or name in optional:
				continue
			total += 1
			value = doc.get(name)
			empty = value in (None, "") or (
				field["fieldtype"] in ("Currency", "Float", "Int", "Percent") and not flt(value)
			)
			filled += 0 if empty else 1
	return {"filled": filled, "total": total}


def _heading_text(raw):
	"""The words of an ``<h1>`` as the reader sees them; only the part before any Jinja.

	A heading that varies (``EXHIBIT B – {% if … %}``) is matched on its fixed start.
	"""
	text = re.split(r"\{[{%#]", raw or "", maxsplit=1)[0]
	text = re.sub(r"<[^>]+>", "", text)
	return " ".join(html.unescape(text).split())


def agreement_sections(body):
	"""``(headings, {fieldname: [heading, ...]})`` read from a template body.

	Each ``<h1>`` opens a part of the agreement ("CLIENT & PROPERTY INFORMATION",
	"8. EMERGENCY SERVICE"); a field belongs to every part whose text mentions it. The page
	matches the headings against its preview to highlight the parts the current step fills
	in and scroll to them, so this is read from the template the contract actually uses
	and needs no list kept by hand.
	"""
	parts = _H1.split(body or "")
	headings, fields = [], {}
	for index in range(1, len(parts), 2):
		heading = _heading_text(parts[index])
		if not heading:
			continue
		headings.append(heading)
		segment = parts[index] + (parts[index + 1] if index + 1 < len(parts) else "")
		names = set(_DOC_FIELD.findall(segment))
		names |= {name for name, pattern in CONTEXT_TABLES.items() if pattern.search(segment)}
		for name in sorted(names):
			fields.setdefault(name, [])
			if heading not in fields[name]:
				fields[name].append(heading)
	return headings, fields


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_builder_start():
	"""The start screen: the contract types on offer and the caller's open drafts."""
	frappe.has_permission(DOCTYPE, "create", throw=True)
	types = frappe.get_all(
		"Contract Template",
		filters={"enabled": 1},
		fields=["name", "template_key", "title", "party_type", "description", "requires_msa"],
		order_by="title asc",
	)
	drafts = frappe.get_list(
		DOCTYPE,
		filters={"docstatus": 0},
		fields=["name", "title", "template_key", "party_display", "project", "modified"],
		order_by="modified desc",
		limit_page_length=12,
	)
	issued = frappe.get_list(
		DOCTYPE,
		filters={"docstatus": 1, "status": "Out for Signature"},
		fields=["name", "title", "template_key", "party_display", "project", "modified"],
		order_by="modified desc",
		limit_page_length=12,
	)
	return {"types": types, "drafts": drafts, "awaiting": issued}


@frappe.whitelist()
def get_builder(name):
	"""Everything the walk needs for one contract: its values, its steps and what it can do next."""
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")
	return _payload(doc)


@frappe.whitelist(methods=["POST"])
def save_builder(name, values):
	"""Save the builder's values onto the draft and return the fresh payload.

	Only fields the walk shows for this contract type are assigned, one by one — never
	``doc.update(values)`` — so the payload cannot set a status, a signature or a field
	the step does not offer. Child tables are rebuilt from the rows sent, keeping the
	fields the child doctype defines. ``save()`` then runs the normal validation, so a
	missing required value comes back as the usual message and nothing is half-saved.
	"""
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(
			_("{0} has been issued, so it can no longer be edited here. Cancel and amend it from the form to change it.").format(name),
			title=_("Already Issued"),
		)
	if isinstance(values, str):
		values = json.loads(values or "{}")
	if not isinstance(values, dict):
		frappe.throw(_("Could not read the contract's values."))

	template = frappe.db.get_value("Contract Template", doc.contract_template, ["party_type"], as_dict=True) or {}
	allowed = _editable_fields(builder_steps(doc.get("template_key"), template.get("party_type")))
	for fieldname, child_fields in allowed.items():
		if fieldname not in values:
			continue
		if child_fields is None:
			doc.set(fieldname, values.get(fieldname))
			continue
		rows = values.get(fieldname) or []
		doc.set(fieldname, [])
		for row in rows if isinstance(rows, list) else []:
			if isinstance(row, dict):
				doc.append(fieldname, {child: row.get(child) for child in child_fields if child in row})
	doc.save()
	return _payload(doc)


@frappe.whitelist(methods=["POST"])
def issue_contract(name):
	"""Submit the draft so it can be signed. Returns the fresh payload.

	Submitting locks the contract's data (status moves to Out for Signature in
	``on_submit``); a change after this is a Cancel and Amend, which makes a new
	numbered revision. The page asks before it calls this.
	"""
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("submit")
	if doc.docstatus == 0:
		doc.submit()
	elif doc.docstatus == 2:
		frappe.throw(_("{0} was cancelled. Amend it to make a new revision.").format(name), title=_("Cancelled"))
	return _payload(doc)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=60, seconds=3600, methods=["POST"])
def sign_here(
	name,
	signer_email=None,
	signed_name=None,
	signed_title=None,
	signature_mode=None,
	signature_image=None,
	initials=None,
	agree=None,
):
	"""Issue the contract (if it is still a draft) and execute it with the client's signature.

	The client is beside the staff member, has initialed each clause in the builder's
	preview as it was filled in, and has signed in the builder: ``esign.api.sign_in_person``
	does the rest on the same evidence path as the signing page. Issuing and signing are
	one transaction, so a refusal (initials drawn beside wording that has since changed, a
	missing set) rolls the issue back too and the contract stays an editable draft.

	``initials`` is JSON: ``{key: {"image": PNG data URI, "clause": text it was drawn beside}}``.
	Returns ``{"ok": True, "payload", "autopay"}`` or ``{"ok": False, "error", "redo"}``.
	"""
	from erpnext_enhancements.project_enhancements.esign import api as esign_api
	from erpnext_enhancements.project_enhancements.esign import portal

	throw_if_contract_esign_disabled()
	frappe.only_for(list(SIGN_ROLES))
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("submit")
	if doc.docstatus == 2:
		frappe.throw(_("{0} was cancelled. Amend it to make a new revision.").format(name), title=_("Cancelled"))

	# The signer's own inputs, checked before anything is issued.
	if not cint(agree):
		frappe.throw(_("The client needs to tick the box to confirm they intend to sign."))
	mode = signature_mode if signature_mode in ("Drawn", "Typed") else None
	if not mode:
		frappe.throw(_("Choose whether the signature is typed or drawn."))
	signer = portal._clean_text(signed_name)
	if not signer:
		frappe.throw(_("Enter the client's full name as their signature."))
	image = ""
	if mode == "Drawn":
		image = portal._validated_signature_image(signature_image)
		if not image:
			frappe.throw(_("Draw the signature in the box, or switch to typing it."))
	recipient = esign_api._valid_single_email(signer_email or doc.get("contact_email"))
	if isinstance(initials, str):
		try:
			initials = json.loads(initials or "{}")
		except ValueError:
			initials = {}

	if doc.docstatus == 0:
		doc.submit()
		# Read back what submitting wrote (status included) rather than trusting the instance.
		doc = frappe.get_doc(DOCTYPE, name)
	result = esign_api.sign_in_person(
		doc,
		recipient,
		mode=mode,
		signed_name=signer,
		signed_title=portal._clean_text(signed_title),
		signature_image=image,
		initials=initials if isinstance(initials, dict) else {},
	)
	if not result.get("ok"):
		# Nothing signed: undo the issue made above with everything else in this request.
		frappe.db.rollback()
		return {
			"ok": False,
			"error": result.get("error"),
			"redo": result.get("redo") or [],
			"reload": 1 if result.get("reload") else 0,
		}

	request = frappe.get_doc("Contract Signature Request", result["request"])
	return {
		"ok": True,
		"autopay": portal._autopay_offer(request),
		"payload": _payload(frappe.get_doc(DOCTYPE, name)),
	}


@frappe.whitelist(methods=["POST"])
def start_card_on_file(name):
	"""After signing in the builder: open the card-on-file page the agreement promises.

	The same enrolment the signing page offers (``esign.portal.begin_autopay``), for the
	contract's latest signed request; every condition is re-checked there. Only for a contract
	signed in person, and never over the client's own "No thanks" (``autopay_outcome``
	Declined): this is the offer made at the table, not a way round an answer.
	"""
	from erpnext_enhancements.project_enhancements.esign import portal

	throw_if_contract_esign_disabled()
	frappe.only_for(list(SIGN_ROLES))
	frappe.get_doc(DOCTYPE, name).check_permission("read")
	request = frappe.db.get_value(
		"Contract Signature Request",
		{"project_contract": name, "status": "Signed"},
		["name", "signing_channel", "autopay_outcome"],
		order_by="signed_on desc",
		as_dict=True,
	)
	if not request or request.signing_channel != "In Person" or request.autopay_outcome == "Declined":
		return {"ok": False}
	return portal.begin_autopay(frappe.get_doc("Contract Signature Request", request.name))


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=20, seconds=3600, methods=["POST"])
def ask_contract_manager(name, note=None):
	"""Tell the Contract Managers a draft is ready to issue: a bell notification each.

	For whoever built the draft without the role to issue it. Needs write on the draft, so only
	someone working on it can send this; the note is escaped. Returns ``{"notified": n}``.
	"""
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(_("{0} has already been issued.").format(name))
	managers = _contract_managers()
	if not managers:
		frappe.throw(
			_("Nobody has the Contract Manager role yet. Ask a System Manager to give it to whoever issues contracts.")
		)
	recipients = [user for user in managers if user != frappe.session.user]
	if not recipients:
		frappe.throw(_("You are the only Contract Manager, so there is nobody else to tell."))
	who = frappe.utils.get_fullname(frappe.session.user)
	# Escaped: the bell renders a Notification Log's title (copied from the subject) as HTML.
	subject = _("Contract ready to issue: {0}").format(frappe.utils.escape_html(doc.get("title") or name))
	content = _("{0} has finished {1} and asks a Contract Manager to review and issue it.").format(
		frappe.utils.escape_html(who), frappe.utils.escape_html(name)
	)
	if note:
		content += "<br><br>" + frappe.utils.escape_html(str(note)[:1000])
	for user in recipients:
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"subject": subject,
				"email_content": content,
				"document_type": DOCTYPE,
				"document_name": name,
				"for_user": user,
				"from_user": frappe.session.user,
				"type": "Alert",
			}
		).insert(ignore_permissions=True)
	doc.add_comment("Comment", _("Asked a Contract Manager to issue it."))
	return {"notified": len(recipients)}


def _contract_managers():
	"""Enabled users who hold the Contract Manager role, directly or through a profile."""
	holders = frappe.get_all(
		"Has Role", filters={"role": CONTRACT_MANAGER, "parenttype": "User"}, pluck="parent"
	)
	if not holders:
		return []
	return frappe.get_all(
		"User", filters={"name": ["in", holders], "enabled": 1, "user_type": "System User"}, pluck="name"
	)


@frappe.whitelist()
def get_signing_status(name):
	"""Polled while the client signs on a device beside the page: has it been signed yet?"""
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")
	row = frappe.db.get_value(
		"Contract Signature Request",
		{"project_contract": name},
		["name", "status", "signing_channel", "signed_on", "signed_name", "signer_email", "expires_on", "view_count"],
		order_by="creation desc",
		as_dict=True,
	)
	return {"contract_status": doc.status, "docstatus": doc.docstatus, "request": row or {}}


def _payload(doc):
	template = frappe.db.get_value(
		"Contract Template", doc.contract_template, ["title", "party_type", "description"], as_dict=True
	) or frappe._dict()
	steps = builder_steps(doc.get("template_key"), template.get("party_type"))
	body = frappe.db.get_value("Contract Template", doc.get("contract_template"), "body") or ""
	headings, field_sections = agreement_sections(body)
	esign_on = contract_esign_enabled()
	# Signing here needs only e-signing switched on; handing the client another device or
	# emailing them a link also needs the public signing page.
	can_sign_here = bool(
		esign_on
		and set(frappe.get_roles()) & SIGN_ROLES
		and frappe.has_permission(DOCTYPE, "submit", doc=doc)
	)
	can_send = bool(can_sign_here and contract_esign_public_page_enabled())
	signing = {}
	if can_sign_here and doc.get("status") != "Signed":
		settings = frappe.get_cached_doc("ERPNext Enhancements Settings")
		signing = {
			"consent_text": settings.get("contract_esign_consent_text") or "",
			"disclosure_text": settings.get("contract_esign_disclosure_text") or "",
			"presenter": frappe.utils.get_fullname(frappe.session.user),
		}
	maintenance_contract = None
	if doc.get("template_key") == "maintenance" and doc.get("status") == "Signed":
		maintenance_contract = frappe.db.get_value(
			"Sapphire Maintenance Contract", {"project_contract": doc.name, "docstatus": ["<", 2]}, "name"
		)
	# The latest signing request, so the page can say a link is out and warn before replacing an
	# emailed one with an in-person one (which retires the emailed link).
	signature = {}
	if doc.docstatus == 1:
		signature = (
			frappe.db.get_value(
				"Contract Signature Request",
				{"project_contract": doc.name},
				["status", "signing_channel", "signer_email", "view_count"],
				order_by="creation desc",
				as_dict=True,
			)
			or {}
		)
	return {
		"name": doc.name,
		"title": doc.get("title") or doc.name,
		"signature": signature,
		"template_key": doc.get("template_key"),
		"party_noun": _(party_noun(doc.get("template_key"))),
		"template_title": template.get("title") or doc.get("contract_template"),
		"docstatus": doc.docstatus,
		"status": doc.get("status"),
		"doc": doc.as_dict(convert_dates_to_str=True, no_nulls=False),
		"steps": steps,
		"blanks": blanks(doc, steps),
		"progress": progress(doc, steps, body),
		"headings": headings,
		"field_sections": field_sections,
		"editable": 1 if doc.docstatus == 0 and frappe.has_permission(DOCTYPE, "write", doc=doc) else 0,
		"can_issue": 1 if doc.docstatus == 0 and frappe.has_permission(DOCTYPE, "submit", doc=doc) else 0,
		# Can build but not issue: offered "Let a Contract Manager know".
		"can_ask": 1
		if doc.docstatus == 0
		and not frappe.has_permission(DOCTYPE, "submit", doc=doc)
		and frappe.has_permission(DOCTYPE, "write", doc=doc)
		else 0,
		"can_sign_here": 1 if can_sign_here else 0,
		"can_send": 1 if can_send else 0,
		"signing": signing,
		"esign_enabled": 1 if esign_on else 0,
		"maintenance_contract": maintenance_contract,
	}
