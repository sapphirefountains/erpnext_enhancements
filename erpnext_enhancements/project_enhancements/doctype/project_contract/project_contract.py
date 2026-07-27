"""Project Contract — a generated, revision-tracked agreement (Phase 4).

One document per issued agreement, typed by its **Contract Template**
(``template_key``: msa / sow / owner / rental / maintenance). The printed
output is the template's Jinja HTML rendered over this document at print
time (``render_body()``, used by the "Project Contract" print format), so a
legal-text edit on the template flows into every not-yet-signed contract.

Reading it: :func:`get_contract_html` returns that same rendered agreement —
the full legal language with this contract's data filled in — for the
**Preview** button on the form (which renders the values on screen, unsaved
edits included, so the finished document can be read while it is being written)
and for the **Contracts tab** on the Project and Customer forms, which lists
every contract belonging to that record (:func:`get_contracts` — agreements
*and* the operational maintenance contracts beside them) and opens any of them
in place.

Revision model (the meeting's estimate-revision convention, natively):
the doctype is **submittable** — Draft = editable working copy; Submit =
issued (data locked, ``status`` workflow continues via allow-on-submit:
Out for Signature → Signed, with ``signed_on``/``signed_by``); Cancel +
Amend = a new numbered revision (``revision`` increments, ``amended_from``
preserves the full lineage) — and ``track_changes`` keeps field-level
history while drafting.

Sequencing rule (per the Jun 9 follow-up): a **SOW** can only be created
under a **Signed** Master Subcontractor Agreement for the same Supplier —
:meth:`ProjectContract.validate_msa_gate` enforces it and stamps the MSA
effective date into the SOW header.

Generation: :func:`create_contract` (whitelisted) is called by the
"Generate Contract" buttons on Opportunity / Project / Supplier
(``public/js/contracts.js``); it prefils party, contacts, addresses,
description, value-stream phase selection, rental dates and equipment from
the source document, seeds the fixed phase/service-option rows, and returns
the new draft's name for routing.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, today

from erpnext_enhancements.feature_flags import throw_if_process_automation_disabled

# Series include the generation year (SF-OC-2026-0001); frappe keys the
# counter on the resolved prefix, so numbering restarts at 0001 each year.
SERIES_BY_KEY = {
	"msa": "SF-MSA-.YYYY.-.####",
	"sow": "SF-SOW-.YYYY.-.####",
	"owner": "SF-OC-.YYYY.-.####",
	"rental": "SF-RA-.YYYY.-.####",
	"maintenance": "SF-MAINT-.YYYY.-.####",
	# retained originals (Contract Comparison Report: no replacement in the
	# revised suite, still in active use)
	"nda": "SF-NDA-.YYYY.-.####",
	"architect": "SF-ARCH-.YYYY.-.####",
	"employee_contractor": "SF-EC-.YYYY.-.####",
}

# Templates whose party type is fixed get it stamped from the template; "Any
# Party" templates (NDA, Employee-Contractor) let the user pick the
# counterparty's record type per contract (Customer / Supplier / Employee).
FLEXIBLE_PARTY = "Any Party"

OWNER_PHASES = [
	("design", "Phase 1 — Design & Engineering"),
	("construction", "Phase 2 — Construction & Installation"),
	("maintenance", "Phase 3 — Ongoing Maintenance"),
]

MAINTENANCE_OPTIONS = [
	("standard", "Standard Maintenance Plan"),
	("startup", "Seasonal Startup (Spring)"),
	("winterization", "Winterization (Fall)"),
	("package", "Seasonal Startup + Winterization Package"),
]

# Per-option unit printed on the plan; standard's price seeds from the
# maintenance fee Item Price, the rest are entered per contract.
MAINTENANCE_OPTION_UNITS = {
	"standard": "visit",
	"startup": "event",
	"winterization": "event",
	"package": "year",
}

# Visits per year by Project Contract visit_frequency (Custom/blank -> unknown).
VISITS_PER_YEAR = {"Weekly": 52, "Bi-Weekly": 26, "Monthly": 12, "Quarterly": 4}

# Value Stream name -> owner-contract phase key (preselects the checkboxes).
VALUE_STREAM_PHASE = {"Design": "design", "Build": "construction", "Service": "maintenance"}

# The Jun 9 meeting's scope model, mirrored on Opportunity AND Project:
# per value stream, "Customer Requests" (the customer's words, entered by
# Sales) and "Deliverables" (the internal breakdown, entered by PM/Design).
# (stream label, parent table fieldname, child column) pairs per stream.
SCOPE_STREAMS = [
	("Design", "custom_design_customer_requests", "design_customer_requests",
	 "custom_design_deliverables", "design_deliverables"),
	("Build", "custom_build_customer_requests", "build_customer_requests",
	 "custom_build_deliverables", "build_deliverables"),
	("Service", "custom_service_customer_requests", "service_customer_requests",
	 "custom_service_deliverables", "service_deliverables"),
	("Events", "custom_rent_customer_requests", "rent_customer_requests",
	 "custom_rent_deliverables", "rent_deliverables"),
]


class ProjectContract(Document):
	# NOTE on field access: every read in the validate/autoname path uses
	# ``self.get(...)`` rather than bare attributes. A NEW document arriving
	# from the desk omits every empty field (the client strips nulls before
	# POSTing), and BaseDocument raises AttributeError for unset attributes —
	# bare reads crashed the very first save from the UI (production report,
	# Jun 10: ``'ProjectContract' object has no attribute 'amended_from'``).
	def autoname(self):
		key = self.get("template_key") or frappe.db.get_value(
			"Contract Template", self.get("contract_template"), "template_key"
		)
		series = SERIES_BY_KEY.get(key)
		if not series:
			frappe.throw(_("Unknown contract template key: {0}").format(key))
		self.naming_series = series
		from frappe.model.naming import make_autoname

		self.name = make_autoname(series + ".", doc=self)

	def validate(self):
		self._fetch_template_props()
		self._resolve_party_display()
		self._stamp_revision()
		self.validate_msa_gate()
		self._compute_totals()
		template_title = (
			frappe.db.get_value("Contract Template", self.get("contract_template"), "title")
			or self.get("template_key")
			or ""
		)
		self.title = f"{template_title}: {self.get('party_display') or self.get('party') or ''}"

	def _fetch_template_props(self):
		if not self.get("contract_template"):
			return
		template_key, template_party_type = frappe.db.get_value(
			"Contract Template", self.contract_template, ["template_key", "party_type"]
		)
		self.template_key = template_key
		if template_party_type != FLEXIBLE_PARTY:
			self.party_type = template_party_type
		elif not self.get("party_type"):
			frappe.throw(
				_("Select the Party Type (Customer / Supplier / Employee) for this agreement."),
				title=_("Party Type Required"),
			)

	def _resolve_party_display(self):
		if not self.get("party"):
			return
		name_field = {
			"Customer": "customer_name",
			"Supplier": "supplier_name",
			"Employee": "employee_name",
		}.get(self.get("party_type"))
		if name_field:
			self.party_display = (
				frappe.db.get_value(self.party_type, self.party, name_field) or self.party
			)

	def _stamp_revision(self):
		if self.get("amended_from") and not cint(self.get("revision")):
			self.revision = cint(frappe.db.get_value("Project Contract", self.amended_from, "revision")) + 1

	def validate_msa_gate(self):
		"""SOWs (and any template flagged requires_msa) need a Signed MSA for the party."""
		if not self.get("contract_template"):
			return
		if not cint(frappe.db.get_value("Contract Template", self.contract_template, "requires_msa")):
			return
		msa = None
		if self.get("msa_contract"):
			msa = frappe.db.get_value(
				"Project Contract",
				self.msa_contract,
				["name", "party", "status", "docstatus", "signed_on", "contract_date", "template_key"],
				as_dict=True,
			)
		if not msa or msa.template_key != "msa":
			frappe.throw(
				_("Select the Master Subcontractor Agreement this SOW is issued under."),
				title=_("MSA Required"),
			)
		if msa.party != self.get("party"):
			frappe.throw(
				_("MSA {0} belongs to {1}, not {2}.").format(msa.name, msa.party, self.get("party")),
				title=_("MSA Mismatch"),
			)
		if msa.docstatus != 1 or msa.status != "Signed":
			frappe.throw(
				_(
					"MSA {0} is not signed yet (status: {1}). Complete and sign the Master "
					"Subcontractor Agreement before issuing a Statement of Work."
				).format(msa.name, msa.status),
				title=_("MSA Not Signed"),
			)
		self.msa_effective_date = msa.signed_on or msa.contract_date

	def _compute_totals(self):
		self.milestones_total = sum(flt(row.amount) for row in (self.get("milestones") or []))

		if self.get("template_key") == "owner":
			included = [row for row in (self.get("phases") or []) if cint(row.included)]
			self.total_contract_value = sum(flt(row.fee) for row in included)
			self.total_due_at_signing = sum(flt(row.retainer) for row in included)
			self.total_design_fee = (
				flt(self.get("concept_design_fee"))
				+ flt(self.get("design_development_fee"))
				+ flt(self.get("construction_documents_fee"))
			)
		elif self.get("template_key") == "rental":
			self.total_rental_amount = (
				flt(self.get("base_rental_fee"))
				+ flt(self.get("delivery_setup_fee"))
				+ flt(self.get("pickup_removal_fee"))
				+ flt(self.get("chemicals_fee"))
				+ flt(self.get("other_fee"))
			)
			self.total_due_at_signing = self.total_rental_amount + flt(self.get("security_deposit"))
		elif self.get("template_key") == "maintenance":
			self.total_due_at_signing = flt(self.get("maintenance_deposit"))
			self._compute_maintenance_annual_fee()

	def _compute_maintenance_annual_fee(self):
		"""Auto-fill Annual Maintenance Fee from the included, priced options.

		Fills the field while it is blank or still holds the value the previous
		inputs auto-derived (compared at currency precision) — a manual override
		is never clobbered. When the total is not computable (a per-visit
		Standard plan on a Custom/blank frequency) it clears the field to a
		fillable blank rather than printing an understated or stale number.
		"""
		current = flt(self.get("annual_maintenance_fee"))
		derivable = not current
		if not derivable:
			before = self.get_doc_before_save()
			if before is not None:
				prev = _annualize_options(before.get("service_options"), before.get("visit_frequency"))
				derivable = prev is not None and flt(current, 2) == flt(prev, 2)
		if not derivable:
			return
		computed = _annualize_options(self.get("service_options"), self.get("visit_frequency"))
		self.annual_maintenance_fee = computed if computed is not None else 0

	def on_submit(self):
		if self.get("status") == "Draft":
			self.status = "Out for Signature"

	def on_cancel(self):
		self.status = "Void"
		self._void_live_signature_requests()

	def on_update_after_submit(self):
		"""Retire any live signing link once the contract is Signed.

		Signing online already leaves its own request at ``Signed``, so this exists
		for the paper route: "Mark as Signed" flips the status and would otherwise
		leave a request sitting at ``Sent``, which the reminder sweep would keep
		chasing and eventually report as "we sent every reminder and it is still
		unsigned" about an agreement that was signed a fortnight ago.

		``_void_live_signature_requests`` only touches Sent/Viewed rows, so the
		e-signature path is unaffected.
		"""
		if self.get("status") == "Signed":
			self._void_live_signature_requests()

	def _void_live_signature_requests(self):
		"""Kill any signing link still in flight.

		A customer must never be able to execute an agreement that has been voided
		or superseded by an amendment, and a link left live after a paper signature
		is a request the reminder sweep would keep chasing. The token is cleared as
		well as the status, so the link stops resolving immediately rather than
		waiting for the daily expiry sweep.

		Only Sent/Viewed rows are touched, so a request that executed the contract
		itself keeps its own record. Best-effort: neither a cancel nor a status
		change must fail over this.
		"""
		try:
			live = frappe.get_all(
				"Contract Signature Request",
				filters={"project_contract": self.get("name"), "status": ["in", ["Sent", "Viewed"]]},
				pluck="name",
			)
			for name in live:
				frappe.db.set_value(
					"Contract Signature Request",
					name,
					{
						"status": "Void",
						"token_hash": None,
						"voided_on": frappe.utils.now_datetime(),
						"voided_by": frappe.session.user,
						"void_reason": _("Contract cancelled or amended."),
					},
					update_modified=False,
				)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Project Contract: voiding signature requests failed")

	def executed_body(self):
		"""The document to print: the executed instrument if signed, else a live render.

		Once a contract has been signed electronically the stored
		``agreement_html`` on its Contract Signature Request is the executed
		instrument — the agreement as the customer saw it, with their signature in
		place and the completion certificate appended. Printing that rather than
		re-rendering means the desk print, the customer's emailed copy and the PDF
		attached to the contract are one identical document, and a later edit to
		the (site-editable) Contract Template can never change what a signed
		contract says.

		Falls back to :meth:`render_body` for unsigned contracts and for anything
		signed on paper.
		"""
		return _executed_html(self.get("name")) or self.render_body()

	def render_body(self):
		"""Rendered agreement HTML — called by the 'Project Contract' print format."""
		body = frappe.db.get_value("Contract Template", self.get("contract_template"), "body")
		if not body:
			frappe.throw(_("Contract Template {0} has no body.").format(self.get("contract_template")))
		return frappe.render_template(body, _render_context(self))


def _executed_html(name):
	"""The stored executed instrument for a contract, or None.

	Never raises — every caller is on a read path (print, preview, the Project
	form's Contracts tab) and none of them may 500 because an evidence lookup
	hiccupped; they fall back to a live render of the template instead.
	"""
	if not name:
		return None
	try:
		return (
			frappe.db.get_value(
				"Contract Signature Request",
				{"project_contract": name, "status": "Signed"},
				"agreement_html",
				order_by="signed_on desc",
			)
			or None
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Project Contract: executed body lookup failed")
		return None


# ---------------------------------------------------------------------------
# Render helpers (passed into the template context)
# ---------------------------------------------------------------------------


def _blank(width=30):
	return f'<span class="ct-blank">{"&nbsp;" * width}</span>'


def _fill(value, width=30):
	"""A value, or a fillable blank line when empty — paper fallback stays usable."""
	if value in (None, ""):
		return _blank(width)
	return frappe.utils.escape_html(str(value))


def _cb(checked):
	return '<span class="ct-cb">&#9746;</span>' if checked else '<span class="ct-cb">&#9744;</span>'


def _money(value, width=14):
	if value in (None, "", 0):
		return _blank(width)
	return frappe.utils.fmt_money(flt(value), currency=frappe.defaults.get_global_default("currency") or "USD")


def _dt(value, width=18):
	if not value:
		return _blank(width)
	return frappe.utils.formatdate(value)


def _multiline(value, width=80, lines=3):
	"""Long-text value, or several blank writing lines."""
	if value:
		return frappe.utils.escape_html(str(value)).replace("\n", "<br>")
	return "<br>".join(_blank(width) for _ in range(lines))


def _render_context(doc):
	from erpnext_enhancements.project_enhancements.esign.render import (
		signature_markup,
		signed_signature_for,
	)

	phases = {row.phase_key: row for row in (doc.get("phases") or [])}
	options = {row.option_key: row for row in (doc.get("service_options") or [])}

	# Resolved once — the SIGNATURES block calls sig() several times per render.
	# When the contract has no signature (the paper flow, and every unsigned
	# contract) sig() returns exactly what blank() does, so existing output is
	# unchanged.
	signature = signed_signature_for(doc)

	def _sig(party="client", width=30):
		return signature_markup(signature, party=party, width=width, blank=_blank)

	return {
		"doc": doc,
		"fill": _fill,
		"blank": _blank,
		"cb": _cb,
		"money": _money,
		"dt": _dt,
		"multiline": _multiline,
		"sig": _sig,
		# The countersignature (recorded, never required) lives on the signature
		# record, not the contract — templates read it from here. Always a dict,
		# so an unsigned render resolves to empty values and prints blanks.
		"signature": signature or frappe._dict(),
		"phases": phases,
		"service_options": options,
		"frappe": frappe._dict(utils=frappe.utils),
	}


def _compose_scope(source):
	"""SOW scope HTML from a source doc's request/deliverable scope tables.

	Walks the four value streams (Design/Build/Service/Events); for each stream
	with content, emits the Customer Requests (the customer's ask, verbatim)
	and the Deliverables (the PM/Design breakdown — PRO-0204 Step 6) as
	lists. Streams with no rows are omitted entirely.
	"""

	def lines(table_field, column):
		rows = source.get(table_field) or []
		texts = []
		for row in rows:
			text = (row.get(column) or "").strip()
			if text:
				texts.append(frappe.utils.escape_html(text).replace("\n", "<br>"))
		return texts

	sections = []
	for label, req_field, req_col, del_field, del_col in SCOPE_STREAMS:
		requests = lines(req_field, req_col)
		deliverables = lines(del_field, del_col)
		if not requests and not deliverables:
			continue
		part = [f"<h4>{label}</h4>"]
		if requests:
			part.append(
				"<p><b>Customer Requests</b></p><ul>"
				+ "".join(f"<li>{text}</li>" for text in requests)
				+ "</ul>"
			)
		if deliverables:
			part.append(
				"<p><b>Deliverables</b></p><ul>"
				+ "".join(f"<li>{text}</li>" for text in deliverables)
				+ "</ul>"
			)
		sections.append("".join(part))
	return "".join(sections)


@frappe.whitelist()
def compose_scope_of_work(source_doctype, source_name):
	"""Scope HTML for an SOW from a Project or Opportunity (form button / auto-pull).

	"Depending on which stage the contract is in": the form pulls from the
	linked Project once one exists, else from the Opportunity — both carry
	the same scope tables.
	"""
	if source_doctype not in ("Opportunity", "Project"):
		frappe.throw(_("Scope can only be pulled from an Opportunity or a Project."))
	source = frappe.get_doc(source_doctype, source_name)
	source.check_permission("read")
	return _compose_scope(source)


# ---------------------------------------------------------------------------
# Reading the agreement (form preview + the Project form's Contracts tab)
# ---------------------------------------------------------------------------

PRINT_FORMAT = "Project Contract Print"

# The one figure worth showing per agreement type in the Contracts list; each
# is computed by _compute_totals. Types with no single headline number (MSA,
# NDA, architect, employee-contractor) are absent and print no amount.
CONTRACT_VALUE_FIELD = {
	"owner": ("total_contract_value", "Contract Value"),
	"rental": ("total_rental_amount", "Rental Total"),
	"maintenance": ("annual_maintenance_fee", "Annual Fee"),
	"sow": ("not_to_exceed", "Not to Exceed"),
}


def _contract_css():
	"""The print format's CSS, so screen and paper render the same document.

	Read from the Print Format record rather than duplicated in JS: a site edit
	to the print styling reaches the on-screen viewer too, and the two can never
	drift into showing the customer's agreement two different ways.
	"""
	try:
		return frappe.db.get_value("Print Format", PRINT_FORMAT, "css") or ""
	except Exception:
		return ""


def _preview_doc(payload):
	"""A Project Contract built from the form's current values — never saved.

	The doctype is forced, so the endpoint cannot be talked into instantiating
	something else, and the agreement's language always comes from a site-owned
	Contract Template — never from the payload.
	"""
	if isinstance(payload, str):
		try:
			payload = frappe.parse_json(payload)
		except Exception:
			payload = None
	if not isinstance(payload, dict):
		frappe.throw(_("Could not read the contract to preview."))
	payload = dict(payload)
	payload["doctype"] = "Project Contract"
	doc = frappe.get_doc(payload)

	# template_key is stamped on save, so a draft whose Contract Type was just
	# changed still carries the old one — and it picks the template's whole
	# layout. Resolved here directly rather than through _fetch_template_props,
	# which throws for an unset party type: a preview must render a half-filled
	# draft, not refuse it.
	if doc.get("contract_template"):
		doc.template_key = (
			frappe.db.get_value("Contract Template", doc.contract_template, "template_key")
			or doc.get("template_key")
		)

	# Totals and the party's display name are also stamped on save, so an
	# unsaved draft still carries the previous save's numbers (or none at all).
	# Redoing them here is what makes the preview show what the agreement WILL
	# say — the whole point of reading it before saving.
	messages = list(getattr(frappe.local, "message_log", None) or [])
	for step in (doc._resolve_party_display, doc._compute_totals):
		try:
			step()
		except Exception:
			continue
	# Nothing a swallowed step queued may reach the client: the preview
	# succeeded, and popping a validation complaint over a rendered agreement
	# would misreport what happened.
	frappe.local.message_log = messages
	return doc


@frappe.whitelist()
def get_contract_html(name=None, doc=None):
	"""The complete agreement — the template's language with this data filled in.

	Two callers, one renderer:

	* the **Preview** button on the Project Contract form passes ``doc`` (the
	  form's current values, unsaved edits and brand-new drafts included), so
	  the whole agreement can be read while it is being filled in rather than
	  saved-and-printed to find out what it says;
	* the **Contracts tab** on the Project form passes ``name``.

	A signed contract returns its executed instrument — the document the
	customer actually signed — rather than a fresh render, exactly as the print
	format does; ``executed`` says which one came back. The CSS travels with the
	HTML (see :func:`_contract_css`) so the viewer and the printed PDF are the
	same document.
	"""
	if doc:
		contract = _preview_doc(doc)
	elif name:
		contract = frappe.get_doc("Project Contract", name)
	else:
		frappe.throw(_("Nothing to show: pass a contract name or a document to render."))

	contract.check_permission("read")

	if not contract.get("contract_template"):
		frappe.throw(
			_("Choose a Contract Type first — the agreement's language comes from its template."),
			title=_("No Contract Type"),
		)

	executed = _executed_html(contract.get("name"))
	return {
		"name": contract.get("name"),
		"title": contract.get("title") or contract.get("name"),
		"html": executed or contract.render_body(),
		"css": _contract_css(),
		"executed": 1 if executed else 0,
	}


# How each host form finds the contracts that belong to it. A Project owns the
# agreements issued on the job; a Customer owns the ones they are a party to.
CONTRACT_SOURCES = {
	"Project": {
		"Project Contract": lambda name: {"project": name},
		"Sapphire Maintenance Contract": lambda name: {"project": name},
	},
	"Customer": {
		# Only agreements this customer is actually a party to — a subcontractor
		# SOW issued on their job is our commitment to a supplier, not theirs.
		"Project Contract": lambda name: {"party_type": "Customer", "party": name},
		"Sapphire Maintenance Contract": lambda name: {"customer": name},
	},
}


@frappe.whitelist()
def get_contracts(source_doctype, source_name):
	"""Every contract belonging to a Project or a Customer — the Contracts tab.

	Two kinds of document, deliberately in one list, because "my contracts" is
	one question:

	* **Project Contract** — the signed agreement, carrying the legal language.
	  Opens in place to its full text.
	* **Sapphire Maintenance Contract** — the operational schedule (visits,
	  features, billing). It carries no legal language of its own; when it was
	  mapped from a signed Maintenance Services Agreement it points at one, and
	  the row opens *that* agreement. When it was created directly, the row says
	  so rather than pretending an agreement exists.

	Metadata only — each body is fetched by :func:`get_contract_html` when the
	reader opens a row, so a busy customer still lists in two small queries.

	Permission-filtered via ``get_list``; a doctype the user cannot read is
	skipped rather than raising, because this tab is an aside on someone else's
	form, not a page they asked for.
	"""
	sources = CONTRACT_SOURCES.get(source_doctype)
	if not sources:
		frappe.throw(_("Contracts cannot be listed for {0}.").format(_(source_doctype)))
	frappe.get_doc(source_doctype, source_name).check_permission("read")

	rows = _project_contract_rows(sources["Project Contract"](source_name))
	rows += _maintenance_contract_rows(sources["Sapphire Maintenance Contract"](source_name))
	# Newest first across both kinds; undated drafts sort last rather than first.
	rows.sort(key=lambda row: (row.get("sort_date") or "", row.get("name") or ""), reverse=True)
	return rows


def _project_contract_rows(filters):
	if not frappe.has_permission("Project Contract", "read"):
		return []

	rows = frappe.get_list(
		"Project Contract",
		filters=filters,
		fields=[
			"name",
			"template_key",
			"contract_template",
			"status",
			"docstatus",
			"party",
			"party_type",
			"party_display",
			"contract_date",
			"signed_on",
			"revision",
			"amended_from",
			*[field for field, _label in CONTRACT_VALUE_FIELD.values()],
		],
		order_by="contract_date desc, creation desc",
	)
	if not rows:
		return []

	titles = dict(
		frappe.get_all(
			"Contract Template",
			filters={"name": ["in", list({row.contract_template for row in rows if row.contract_template})]},
			fields=["name", "title"],
			as_list=True,
		)
	)
	# Which of these carry an executed instrument — one query, so the list can
	# say "signed copy on file" without rendering a single body.
	signed = set()
	try:
		signed = {
			row.project_contract
			for row in frappe.get_all(
				"Contract Signature Request",
				filters={"project_contract": ["in", [row.name for row in rows]], "status": "Signed"},
				fields=["project_contract"],
			)
		}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Project Contract: signed-request lookup failed")

	for row in rows:
		field, label = CONTRACT_VALUE_FIELD.get(row.template_key, (None, None))
		row.value = flt(row.get(field)) if field else 0
		row.value_label = _(label) if label else None
		row.type_label = titles.get(row.contract_template) or row.contract_template
		row.executed = 1 if row.name in signed else 0
		# Subcontractor paper (MSA/SOW) is listed alongside the customer's
		# agreements but grouped apart — they are commitments in opposite
		# directions and reading them as one list invites a costly mix-up.
		row.is_subcontract = 1 if row.party_type == "Supplier" else 0
		row.doctype_name = "Project Contract"
		row.readable = 1  # has legal language of its own
		row.sort_date = str(row.get("contract_date") or "")
	return rows


def _maintenance_contract_rows(filters):
	"""The operational maintenance contracts — schedule, not legal language.

	Listed beside the agreements because "what maintenance are we committed to
	on this job" is answered by this document, not by the signed agreement. Its
	``project_contract`` link (when it has one) is what the row opens.
	"""
	if not frappe.db.exists("DocType", "Sapphire Maintenance Contract"):
		return []
	if not frappe.has_permission("Sapphire Maintenance Contract", "read"):
		return []

	rows = frappe.get_list(
		"Sapphire Maintenance Contract",
		filters=filters,
		fields=[
			"name",
			"status",
			"docstatus",
			"customer",
			"project",
			"project_contract",
			"start_date",
			"end_date",
			"default_frequency",
			"invoicing_frequency",
			"recurring_amount",
		],
		order_by="start_date desc, creation desc",
	)
	for row in rows:
		row.doctype_name = "Sapphire Maintenance Contract"
		row.type_label = _("Maintenance Contract (operational)")
		row.party_display = row.customer
		row.value = flt(row.get("recurring_amount"))
		row.value_label = _("Recurring") if row.value else None
		row.is_subcontract = 0
		# The agreement it was mapped from, if any: that is what the row opens.
		# Without one there is nothing to read, and the row says so rather than
		# rendering legal text nobody signed.
		row.agreement = row.project_contract
		row.readable = 1 if row.project_contract else 0
		row.sort_date = str(row.get("start_date") or "")
	return rows


# ---------------------------------------------------------------------------
# Generation (the "Generate Contract" buttons)
# ---------------------------------------------------------------------------


def _maintenance_fee_rate():
	"""Standard per-visit rate from the maintenance fee Item's Standard Selling price."""
	item = frappe.db.get_single_value("ERPNext Enhancements Settings", "maintenance_fee_item")
	if not item:
		return None
	return frappe.db.get_value(
		"Item Price",
		{"item_code": item, "price_list": "Standard Selling", "selling": 1},
		"price_list_rate",
		order_by="valid_from desc",
	)


def _annualize_options(service_options, visit_frequency):
	"""Annual total of the included, priced service options, or None if not computable.

	standard is per visit (x visits/year); startup/winterization are per event
	(once/year); package is per year and, being the Startup+Winterization
	bundle, supersedes those two individual options when all are selected.
	Returns None when the Standard plan is included and priced but the visit
	frequency is Custom/blank (visits/year unknown) — the caller then leaves the
	fee blank rather than deriving an understated figure.
	"""
	rows = {
		row.get("option_key"): row
		for row in (service_options or [])
		if row.get("included") and flt(row.get("price"))
	}
	visits = VISITS_PER_YEAR.get(visit_frequency)
	total = 0.0
	if "standard" in rows:
		if not visits:
			return None
		total += flt(rows["standard"].get("price")) * visits
	if "package" in rows:  # the bundle supersedes the individual seasonal options
		total += flt(rows["package"].get("price"))
	else:
		for key in ("startup", "winterization"):
			if key in rows:
				total += flt(rows[key].get("price"))
	return flt(total, 2)


def _seed_fixed_rows(doc):
	if doc.template_key == "owner" and not doc.get("phases"):
		for key, label in OWNER_PHASES:
			doc.append("phases", {"phase_key": key, "phase_label": label, "included": 0})
	if doc.template_key == "maintenance" and not doc.get("service_options"):
		standard_rate = _maintenance_fee_rate()
		for key, label in MAINTENANCE_OPTIONS:
			doc.append(
				"service_options",
				{
					"option_key": key,
					"option_label": label,
					"included": 0,
					"unit": MAINTENANCE_OPTION_UNITS.get(key),
					"price": standard_rate if key == "standard" else None,
				},
			)


def _prefill_from_opportunity(doc, opportunity):
	opp = frappe.get_doc("Opportunity", opportunity)
	doc.opportunity = opp.name
	if opp.get("custom_created_project"):
		doc.project = opp.custom_created_project
	if doc.party_type == "Customer" and not doc.party and opp.opportunity_from == "Customer":
		doc.party = opp.party_name
	doc.contact_person = doc.contact_person or opp.get("contact_person") or opp.get("contact_display")
	doc.contact_phone = doc.contact_phone or opp.get("contact_mobile") or opp.get("phone")
	doc.contact_email = doc.contact_email or opp.get("contact_email")
	doc.project_title = doc.project_title or opp.get("custom_opportunity_name") or opp.get("title")
	doc.project_description = doc.project_description or opp.get("custom_opportunity_summary")
	doc.rental_start_date = doc.rental_start_date or opp.get("custom_delivery_date_time")
	doc.rental_end_date = doc.rental_end_date or opp.get("custom_take_down_date_time")

	# Value streams preselect the Owner Contract phases.
	streams = {row.value_stream for row in (opp.get("custom_value_stream") or [])}
	wanted = {VALUE_STREAM_PHASE[s] for s in streams if s in VALUE_STREAM_PHASE}
	for row in doc.get("phases") or []:
		if row.phase_key in wanted:
			row.included = 1

	# Events deliverables become rental equipment lines.
	if doc.template_key == "rental" and not doc.get("equipment_items"):
		for row in opp.get("custom_rent_deliverables") or []:
			text = (row.get("rent_deliverables") or "").strip()
			if text:
				doc.append("equipment_items", {"description": text[:140]})

	# SOW scope from the opportunity's request/deliverable tables (only when
	# nothing filled it yet — a Project source takes precedence, see
	# _prefill_from_project).
	if doc.template_key == "sow" and not doc.scope_of_work:
		doc.scope_of_work = _compose_scope(opp) or None


def _prefill_from_project(doc, project):
	proj = frappe.get_doc("Project", project)
	doc.project = proj.name
	if doc.party_type == "Customer" and not doc.party and proj.get("customer"):
		doc.party = proj.customer
	doc.project_title = doc.project_title or proj.get("project_name")
	doc.project_description = doc.project_description or proj.get("custom_project_description")
	doc.site_address = doc.site_address or proj.get("custom_project_address")
	doc.contact_person = doc.contact_person or proj.get("custom_customer_name")
	doc.contact_phone = doc.contact_phone or proj.get("custom_customer_phone") or proj.get("custom_contact_phone")
	doc.contact_email = doc.contact_email or proj.get("custom_customer_email")
	# SOW scope: the project's tables carry the PM/Design breakdown once the
	# engagement reaches project stage, so they win; the opportunity prefill
	# below only fills scope if the project had none.
	if doc.template_key == "sow" and not doc.scope_of_work:
		doc.scope_of_work = _compose_scope(proj) or None
	if proj.get("custom_opportunity"):
		_prefill_from_opportunity(doc, proj.custom_opportunity)


def _prefill_from_supplier(doc, supplier):
	doc.party = supplier
	if not doc.party_type or doc.party_type == FLEXIBLE_PARTY:
		doc.party_type = "Supplier"
	address = frappe.db.get_value(
		"Address",
		{"link_doctype": "Supplier", "link_name": supplier},
		"custom_full_address",
	)
	if address:
		doc.billing_address = address


@frappe.whitelist()
def create_contract(template, source_doctype=None, source_name=None, party=None):
	"""Create a prefilled draft Project Contract; returns its name for routing.

	Called from the Generate Contract buttons (public/js/contracts.js).
	Respects the caller's permissions (no ignore_permissions): the user needs
	create rights on Project Contract and read rights on the source.
	"""
	throw_if_process_automation_disabled()
	template_doc = frappe.get_doc("Contract Template", template)
	if not cint(template_doc.enabled):
		frappe.throw(_("Contract Template {0} is disabled.").format(template))

	doc = frappe.new_doc("Project Contract")
	doc.contract_template = template_doc.name
	doc.template_key = template_doc.template_key
	if template_doc.party_type != FLEXIBLE_PARTY:
		doc.party_type = template_doc.party_type
	doc.contract_date = today()
	if template_doc.template_key == "maintenance":
		# Default the agreement start to today so the term/End Date and the
		# printed §9.1 clause populate without a manual entry (editable after).
		doc.agreement_start_date = today()
	if party:
		doc.party = party

	_seed_fixed_rows(doc)

	if source_doctype == "Opportunity" and source_name:
		frappe.get_doc("Opportunity", source_name).check_permission("read")
		_prefill_from_opportunity(doc, source_name)
	elif source_doctype == "Project" and source_name:
		frappe.get_doc("Project", source_name).check_permission("read")
		_prefill_from_project(doc, source_name)
	elif source_doctype == "Supplier" and source_name:
		frappe.get_doc("Supplier", source_name).check_permission("read")
		_prefill_from_supplier(doc, source_name)

	# Party billing address when a party is known (Customer types from the
	# source; Supplier for an SOW generated off a Project/Opportunity with the
	# supplier picked in the dialog).
	if doc.party and not doc.billing_address:
		doc.billing_address = frappe.db.get_value(
			"Address",
			{"link_doctype": doc.party_type, "link_name": doc.party},
			"custom_full_address",
		)

	doc.insert()
	return doc.name


@frappe.whitelist()
def get_signed_msa(supplier):
	"""The Signed MSA for a supplier, if any — used by the SOW button to gate early."""
	return frappe.db.get_value(
		"Project Contract",
		{
			"template_key": "msa",
			"party": supplier,
			"docstatus": 1,
			"status": "Signed",
		},
		"name",
	)
