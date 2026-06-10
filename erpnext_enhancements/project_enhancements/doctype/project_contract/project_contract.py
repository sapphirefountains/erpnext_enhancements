"""Project Contract — a generated, revision-tracked agreement (Phase 4).

One document per issued agreement, typed by its **Contract Template**
(``template_key``: msa / sow / owner / rental / maintenance). The printed
output is the template's Jinja HTML rendered over this document at print
time (``render_body()``, used by the "Project Contract" print format), so a
legal-text edit on the template flows into every not-yet-signed contract.

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
	("Rent", "custom_rent_customer_requests", "rent_customer_requests",
	 "custom_rent_deliverables", "rent_deliverables"),
]


class ProjectContract(Document):
	def autoname(self):
		key = self.template_key or frappe.db.get_value(
			"Contract Template", self.contract_template, "template_key"
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
		self.title = f"{frappe.db.get_value('Contract Template', self.contract_template, 'title')}: {self.party_display or self.party}"

	def _fetch_template_props(self):
		if not self.contract_template:
			return
		template_key, template_party_type = frappe.db.get_value(
			"Contract Template", self.contract_template, ["template_key", "party_type"]
		)
		self.template_key = template_key
		if template_party_type != FLEXIBLE_PARTY:
			self.party_type = template_party_type
		elif not self.party_type:
			frappe.throw(
				_("Select the Party Type (Customer / Supplier / Employee) for this agreement."),
				title=_("Party Type Required"),
			)

	def _resolve_party_display(self):
		if not self.party:
			return
		name_field = {
			"Customer": "customer_name",
			"Supplier": "supplier_name",
			"Employee": "employee_name",
		}.get(self.party_type)
		if name_field:
			self.party_display = (
				frappe.db.get_value(self.party_type, self.party, name_field) or self.party
			)

	def _stamp_revision(self):
		if self.amended_from and not cint(self.revision):
			self.revision = cint(frappe.db.get_value("Project Contract", self.amended_from, "revision")) + 1

	def validate_msa_gate(self):
		"""SOWs (and any template flagged requires_msa) need a Signed MSA for the party."""
		if not cint(frappe.db.get_value("Contract Template", self.contract_template, "requires_msa")):
			return
		msa = None
		if self.msa_contract:
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
		if msa.party != self.party:
			frappe.throw(
				_("MSA {0} belongs to {1}, not {2}.").format(msa.name, msa.party, self.party),
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
		self.milestones_total = sum(flt(row.amount) for row in (self.milestones or []))

		if self.template_key == "owner":
			included = [row for row in (self.phases or []) if cint(row.included)]
			self.total_contract_value = sum(flt(row.fee) for row in included)
			self.total_due_at_signing = sum(flt(row.retainer) for row in included)
			self.total_design_fee = (
				flt(self.concept_design_fee)
				+ flt(self.design_development_fee)
				+ flt(self.construction_documents_fee)
			)
		elif self.template_key == "rental":
			self.total_rental_amount = (
				flt(self.base_rental_fee)
				+ flt(self.delivery_setup_fee)
				+ flt(self.pickup_removal_fee)
				+ flt(self.chemicals_fee)
				+ flt(self.other_fee)
			)
			self.total_due_at_signing = self.total_rental_amount + flt(self.security_deposit)
		elif self.template_key == "maintenance":
			self.total_due_at_signing = flt(self.maintenance_deposit)

	def on_submit(self):
		if self.status == "Draft":
			self.status = "Out for Signature"

	def on_cancel(self):
		self.status = "Void"

	def render_body(self):
		"""Rendered agreement HTML — called by the 'Project Contract' print format."""
		body = frappe.db.get_value("Contract Template", self.contract_template, "body")
		if not body:
			frappe.throw(_("Contract Template {0} has no body.").format(self.contract_template))
		return frappe.render_template(body, _render_context(self))


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
	phases = {row.phase_key: row for row in (doc.get("phases") or [])}
	options = {row.option_key: row for row in (doc.get("service_options") or [])}
	return {
		"doc": doc,
		"fill": _fill,
		"blank": _blank,
		"cb": _cb,
		"money": _money,
		"dt": _dt,
		"multiline": _multiline,
		"phases": phases,
		"service_options": options,
		"frappe": frappe._dict(utils=frappe.utils),
	}


def _compose_scope(source):
	"""SOW scope HTML from a source doc's request/deliverable scope tables.

	Walks the four value streams (Design/Build/Service/Rent); for each stream
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
# Generation (the "Generate Contract" buttons)
# ---------------------------------------------------------------------------


def _seed_fixed_rows(doc):
	if doc.template_key == "owner" and not doc.get("phases"):
		for key, label in OWNER_PHASES:
			doc.append("phases", {"phase_key": key, "phase_label": label, "included": 0})
	if doc.template_key == "maintenance" and not doc.get("service_options"):
		for key, label in MAINTENANCE_OPTIONS:
			doc.append("service_options", {"option_key": key, "option_label": label, "included": 0})


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

	# Rent deliverables become rental equipment lines.
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
	template_doc = frappe.get_doc("Contract Template", template)
	if not cint(template_doc.enabled):
		frappe.throw(_("Contract Template {0} is disabled.").format(template))

	doc = frappe.new_doc("Project Contract")
	doc.contract_template = template_doc.name
	doc.template_key = template_doc.template_key
	if template_doc.party_type != FLEXIBLE_PARTY:
		doc.party_type = template_doc.party_type
	doc.contract_date = today()
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
