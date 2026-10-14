"""The information blocks: one definition per piece of information, for entry, reports and print.

Form kit P3.1 (ERPNext TASK-2026-02754). The same piece of information (who a job is for, where
it is, when it runs, what is in scope per pillar, what it is worth, who works on it) is entered,
shown in a report and printed the same way everywhere. This registry is where that is written
down once. For each block it names:

* **the fields that carry it on each DocType** (Opportunity and Project for now; P7 adds more);
* **its three faces**: the kit's entry component, the report face's column kind
  (``public/js/report_face/cells.js``) and the print helper (``print_style`` / ``print_lookup``).

It points at what already exists rather than copying it: the start/end pairs are
``utils/date_pairs.PAIRS`` (P2.2), the per-stream request tables are the Opportunity config's
``REQUEST_TABLES``, the pillar of a stream is ``print_style.STREAM_PILLAR``, money is Frappe's own
currency formatting. ``public/js/info_blocks/blocks.js`` is the JavaScript twin, :func:`resolved`
written out, and ``tests/test_form_kit_blocks.py`` holds the two equal, checks every face with
:func:`check`, and checks every field against the DocType's v16 meta plus this app's fixtures.

A face is never left out. An entry component that is not built yet is declared ``pending`` with
the task that builds it, and :func:`check` refuses a block with no entry face at all.

Pure Python: no frappe at import. Only :func:`resolved` / :func:`fields_for` on the Schedule
window import ``utils.date_pairs`` (which imports frappe), and only when called.
"""

import copy

from erpnext_enhancements.form_kit.config.opportunity import REQUEST_TABLES, STREAMS_FIELD

#: The task that builds the Party entry component (and the address half the Site block uses).
PARTY_TASK = "TASK-2026-02755"
#: The information-blocks phase itself: faces nobody has a narrower task for yet.
BLOCKS_TASK = "TASK-2026-02753"

#: Each stream's Deliverables table. Same fieldnames on Opportunity and Project; Products and
#: Delivery have neither a requests nor a deliverables table.
DELIVERABLE_TABLES = {
	"Design": "custom_design_deliverables",
	"Build": "custom_build_deliverables",
	"Service": "custom_service_deliverables",
	"Events": "custom_rent_deliverables",
}

_SCOPE_STREAMS = {
	stream: {"requests": REQUEST_TABLES[stream][0], "deliverables": DELIVERABLE_TABLES[stream]}
	for stream in REQUEST_TABLES
}

#: Print helpers that are Frappe's own, not this app's: ``{dotted name: why}``.
FRAMEWORK_HELPERS = {
	"frappe.format": (
		"Frappe's formatter, a Jinja global. Money has no ps_ helper because Frappe's currency "
		"formatting with the field's own currency is already the one answer; the report face's "
		"money cell calls the same default formatter."
	),
}

#: The keys a resolved per-DocType entry may carry, besides ``fields``.
DOCTYPE_KEYS = ("fields", "mirrors", "pairs", "core_pairs", "streams_field", "streams", "currency")

BLOCKS = {
	"party": {
		"label": "Party",
		"what": "Who the record is for: the account, the person we deal with, and where invoices go.",
		"faces": {
			"entry": {
				"pending": PARTY_TASK,
				"builds": "One component that finds or creates the Account, Contact and Address (P3.2).",
			},
			"report": {"kinds": ["party"]},
			"print": {"helpers": ["erpnext_enhancements.print_lookup.ps_party"]},
		},
		"doctypes": {
			# The address here is the Billing Address (ERPNext's customer_address, shown since
			# v1.633.0). The job site is Primary Address, which is the Site block.
			"Opportunity": {
				"fields": ["opportunity_from", "party_name", "primary_contact", "customer_address"],
				# ERPNext's hidden contact_person, kept equal to the visible Primary Contact
				# because stock mappings (Opportunity -> Quotation, the Prospect row) read it.
				# crm_enhancements/party_mirrors.py applies it on save.
				"mirrors": {"primary_contact": "contact_person"},
			},
			"Project": {"fields": ["customer", "primary_contact", "custom_billing_address"]},
		},
	},
	"site": {
		"label": "Site",
		"what": "Where the work is: the job site address.",
		"faces": {
			"entry": {
				"pending": PARTY_TASK,
				"builds": "The Party component's address picker, offering the account's addresses (P3.2).",
			},
			"report": {"kinds": ["party"]},
			"print": {"helpers": ["erpnext_enhancements.print_style.ps_address"]},
		},
		"doctypes": {
			"Opportunity": {"fields": ["primary_address"]},
			"Project": {"fields": ["primary_address"]},
		},
	},
	"schedule_window": {
		"label": "Schedule window",
		"what": "When it runs: start and end, and on an event the delivery-to-take-down sequence.",
		"faces": {
			"entry": {
				"pending": BLOCKS_TASK,
				"builds": (
					"A start/end pair control. Until then the step renderer draws Frappe's Date and "
					"Datetime controls and utils/date_pairs warns on save."
				),
			},
			"report": {"kinds": ["date", "datetime"]},
			"print": {
				"helpers": [
					"erpnext_enhancements.print_style.ps_date",
					"erpnext_enhancements.print_style.ps_datetime",
				]
			},
		},
		"doctypes": {
			# "date_pairs": the doctype's own entry in utils/date_pairs.PAIRS (warned, never blocked).
			"Opportunity": {"pairs": "date_pairs"},
			"Project": {
				"pairs": "date_pairs",
				# ERPNext's own pairs, which it already validates (and throws on), so date_pairs
				# deliberately does not register them.
				"core_pairs": [
					["expected_start_date", "expected_end_date"],
					["actual_start_date", "actual_end_date"],
				],
			},
		},
	},
	"scope_by_pillar": {
		"label": "Scope by pillar",
		"what": "What is in scope for each chosen value stream: its Customer Requests and Deliverables.",
		"faces": {
			"entry": {"module": "public/js/form_kit/fields.js", "export": "text_list_column"},
			"report": {"kinds": ["pillar"]},
			"print": {
				"helpers": [
					"erpnext_enhancements.print_style.STREAM_PILLAR",
					"erpnext_enhancements.print_style.pillar",
				]
			},
		},
		"doctypes": {
			"Opportunity": {"streams": "value_streams"},
			"Project": {"streams": "value_streams"},
		},
	},
	"money": {
		"label": "Money",
		"what": "What the job is worth and what it may cost.",
		"faces": {
			"entry": {
				"pending": BLOCKS_TASK,
				"builds": (
					"A money input. Until then Frappe's Currency control, with the decimal keyboard "
					"form_kit/fields.js inputmode_for asks a phone for."
				),
			},
			"report": {"kinds": ["money"]},
			"print": {"helpers": ["frappe.format"]},
		},
		"doctypes": {
			"Opportunity": {
				"fields": ["opportunity_amount", "custom_materials_budget"],
				"currency": "currency",
			},
			# No currency field: Project's amounts are in the company's currency.
			"Project": {
				"fields": [
					"custom_project_dollar_amount",
					"estimated_costing",
					"custom_materials_budget",
					"custom_bid_cost",
				],
				"currency": None,
			},
		},
	},
	"crew": {
		"label": "Crew",
		"what": "Who works on it: the owner or manager, the technical lead and the scope contributors.",
		"faces": {
			"entry": {
				"pending": BLOCKS_TASK,
				"builds": "A people picker. Until then Frappe's Link controls and the contributors grid.",
			},
			"report": {"kinds": ["party"]},
			"print": {
				"pending": BLOCKS_TASK,
				"builds": "A print helper that names a person; no ps_ helper prints one today.",
			},
		},
		"doctypes": {
			"Opportunity": {"fields": ["opportunity_owner", "custom_scope_contributors"]},
			"Project": {
				"fields": ["custom_project_owner", "custom_technical_lead", "custom_scope_contributors"]
			},
		},
	},
}

FACES = ("entry", "report", "print")


def _resolve_doctype(doctype, spec):
	out = {}
	fields = list(spec.get("fields") or [])
	if spec.get("pairs") == "date_pairs":
		from erpnext_enhancements.utils.date_pairs import PAIRS

		out["pairs"] = [list(pair["fields"]) for pair in PAIRS.get(doctype, [])]
		out["core_pairs"] = [list(pair) for pair in spec.get("core_pairs") or []]
		for sequence in out["pairs"] + out["core_pairs"]:
			fields.extend(f for f in sequence if f not in fields)
	if spec.get("streams") == "value_streams":
		out["streams_field"] = STREAMS_FIELD
		out["streams"] = copy.deepcopy(_SCOPE_STREAMS)
		fields.append(STREAMS_FIELD)
		for tables in _SCOPE_STREAMS.values():
			fields.extend([tables["requests"], tables["deliverables"]])
	if spec.get("mirrors"):
		out["mirrors"] = dict(spec["mirrors"])
	if "currency" in spec:
		out["currency"] = spec["currency"]
		if spec["currency"] and spec["currency"] not in fields:
			fields.append(spec["currency"])
	out["fields"] = fields
	return {key: out[key] for key in DOCTYPE_KEYS if key in out}


def resolved(blocks=None):
	"""Every block with its DocTypes' fields written out: what the JS twin holds, key for key."""
	blocks = BLOCKS if blocks is None else blocks
	out = {}
	for key, block in blocks.items():
		entry = {k: copy.deepcopy(v) for k, v in block.items() if k != "doctypes"}
		entry["doctypes"] = {
			dt: _resolve_doctype(dt, spec) for dt, spec in (block.get("doctypes") or {}).items()
		}
		out[key] = entry
	return out


def fields_for(block, doctype):
	"""The fields that carry ``block`` on ``doctype`` (mirrors excluded), or ``[]``."""
	spec = (BLOCKS.get(block) or {}).get("doctypes", {}).get(doctype)
	return list(_resolve_doctype(doctype, spec)["fields"]) if spec else []


def mirrors_for(doctype):
	"""``{visible field: hidden field}`` the Party block keeps equal on ``doctype``. No frappe."""
	out = {}
	for block in BLOCKS.values():
		out.update((block.get("doctypes", {}).get(doctype) or {}).get("mirrors") or {})
	return out


def all_fields(doctype, blocks=None):
	"""Every fieldname any block names on ``doctype``, hidden mirrors included."""
	names = []
	for entry in resolved(blocks).values():
		spec = entry["doctypes"].get(doctype)
		if not spec:
			continue
		for name in list(spec["fields"]) + list((spec.get("mirrors") or {}).values()):
			if name not in names:
				names.append(name)
	return names


def check(blocks=None, report_kinds=None):
	"""The problems with a registry, as a list of strings (empty when it is sound).

	Structural only: every block declares all three faces, each in a recognized shape, and a
	pending face names its task. Whether a module exports what an entry face names, a print
	helper exists and a field is on the DocType's meta is checked by the test, which can read the
	files and build the meta.
	"""
	import re

	blocks = BLOCKS if blocks is None else blocks
	problems = []
	task = re.compile(r"^TASK-\d{4}-\d{5}$")
	for key, block in blocks.items():
		faces = block.get("faces") or {}
		for face in FACES:
			value = faces.get(face)
			where = f"{key}.{face}"
			if not value:
				problems.append(f"{where}: no {face} face declared")
				continue
			if "pending" in value:
				if not task.match(str(value.get("pending") or "")):
					problems.append(f"{where}: a pending face must name the task that builds it")
				if not str(value.get("builds") or "").strip():
					problems.append(f"{where}: a pending face must say what will be built")
				continue
			if face == "entry" and not (value.get("module") and value.get("export")):
				problems.append(f"{where}: an entry face names a module and its export, or is pending")
			if face == "report":
				kinds = value.get("kinds") or []
				if not kinds:
					problems.append(f"{where}: a report face names at least one report-face kind")
				for kind in kinds:
					if report_kinds is not None and kind not in report_kinds:
						problems.append(f"{where}: {kind!r} is not a report-face kind")
			if face == "print" and not value.get("helpers"):
				problems.append(f"{where}: a print face names at least one helper, or is pending")
		if not block.get("label"):
			problems.append(f"{key}: no label")
		if not block.get("doctypes"):
			problems.append(f"{key}: names no DocType")
		for dt, spec in (block.get("doctypes") or {}).items():
			if not (spec.get("fields") or spec.get("pairs") or spec.get("streams")):
				problems.append(f"{key}.{dt}: names no field")
	return problems


def field_problems(metas, blocks=None):
	"""Every field a block names that its DocType's meta does not have, as strings.

	``metas`` is ``{doctype: collection of fieldnames}`` (a ``frappe.get_meta`` on a site, or the
	v16 JSON plus this app's fixtures in the bench-free test). A DocType missing from ``metas``
	is a problem too: a block that names it cannot be checked.
	"""
	problems = []
	for key, entry in resolved(blocks).items():
		for doctype, spec in entry["doctypes"].items():
			if doctype not in metas:
				problems.append(f"{key}.{doctype}: no meta to check against")
				continue
			names = set(metas[doctype])
			wanted = list(spec["fields"]) + list((spec.get("mirrors") or {}).values())
			for fieldname in wanted:
				if fieldname not in names:
					problems.append(f"{key}.{doctype}: {fieldname} is not a field of {doctype}")
	return problems
