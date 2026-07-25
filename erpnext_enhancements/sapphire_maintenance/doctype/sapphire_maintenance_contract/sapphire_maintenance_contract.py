"""Controller for the Sapphire Maintenance Contract doctype.

The *operational* maintenance contract: which water features get serviced, how
often, with which modular form template, and from which warehouse their
chemicals are drawn. It sits between two existing documents:

  * **Project Contract** (Maintenance Services Agreement) — the signed legal
    terms. ``make_contract_from_project_contract`` maps one in, pulling visit
    frequency, invoicing frequency, the agreement start date and the included
    seasonal service options (startup/winterization) into ``seasonal_visits``.
  * **Sales Order** (order_type Maintenance) — the commercial source per-visit
    invoices are drawn against. ``make_contract_from_sales_order`` maps its
    water-feature items into ``covered_features``.

The daily scheduler (``tasks.generate_predictive_maintenance_records``) reads
Active contracts' feature rows (``next_visit_date``) and seasonal visits
(:func:`iter_seasonal_visits`) to draft Sapphire Maintenance Records; record
submission writes the rolling dates back (``api.maintenance_scheduling``).

Seasonal visits live in two places, unified by :func:`iter_seasonal_visits`:
the standard startup/winterization pair are flat checkbox+month fields on the
contract (one click to enable), while unusual annual visits go in the
``seasonal_visits`` child table ("Custom Seasonal Visits", in a collapsed
section). A Sapphire Service Plan stamps the standard pair, frequency,
template and visit shape onto the contract in one pick (form JS).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, add_months, add_years, flt, getdate, nowdate

# Project Contract uses a slightly different frequency vocabulary; "Custom"
# has no fixed interval, so it maps to blank (per-feature manual choice).
PROJECT_CONTRACT_FREQUENCY_MAP = {
	"Weekly": "Weekly",
	"Bi-Weekly": "Bi-Weekly",
	"Monthly": "Monthly",
	"Quarterly": "Quarterly",
}

MONTHS = [
	"January", "February", "March", "April", "May", "June",
	"July", "August", "September", "October", "November", "December",
]

# The scheduler's draft dedup and the cadence-skip in maintenance_scheduling
# both key on these exact visit_label strings — never reword them.
STARTUP_LABEL = "Seasonal Startup"
WINTERIZATION_LABEL = "Winterization"

# Fixed-term labels (§9.1) that derive an End Date; Month-to-Month / Other /
# blank leave End Date manual, and a blank End Date never auto-expires.
INITIAL_TERM_YEARS = {"One (1) Year": 1, "Two (2) Years": 2}


class SapphireMaintenanceContract(Document):
	def validate(self):
		self._check_single_active_contract()
		self._materialize_feature_defaults()
		self._derive_end_date()
		self._renewal_rate_housekeeping()
		self._recurring_billing_setup()
		if self.status == "Active" and not self.sales_order:
			frappe.msgprint(
				_("No Sales Order is linked — per-visit invoicing will fall back to the project's Maintenance Sales Order."),
				indicator="orange",
				alert=True,
			)

	def _renewal_rate_housekeeping(self):
		"""Keep the renewal/rate fields consistent (§4.5 / §9.2).

		Clears the rate-notice stamp when the scheduled rate or effective date
		changes (so a re-scheduled change re-notifies Accounts); stamps the
		non-renewal notice date when that flag is set and clears it when unset,
		so a later re-check records the date it was actually re-given.
		"""
		before = self.get_doc_before_save()
		if before:
			prev_eff = before.get("rate_effective_date")
			cur_eff = self.get("rate_effective_date")
			if flt(before.get("scheduled_rate")) != flt(self.get("scheduled_rate")) or (
				(getdate(prev_eff) if prev_eff else None) != (getdate(cur_eff) if cur_eff else None)
			):
				self.rate_notice_sent = None
		if self.get("non_renewal_notice"):
			if not self.get("non_renewal_notice_date"):
				self.non_renewal_notice_date = nowdate()
		else:
			self.non_renewal_notice_date = None

	def _recurring_billing_setup(self):
		"""Initialise recurring-billing fields for a non-Per-Visit contract (§4.2).

		Auto-fills Recurring Amount from the linked agreement's Annual Fee ÷
		cadence (derive-unless-overridden: re-derives on a cadence change while
		the stored value still matches the previous cadence's figure), and
		anchors Next Billing Date to the first period boundary AFTER today — never
		in the past, so switching an old contract onto recurring billing does not
		generate a backlog of catch-up drafts. Only Monthly/Quarterly/Annually.
		"""
		from erpnext_enhancements.api.maintenance_billing import PERIODS_PER_YEAR, add_period

		freq = self.get("invoicing_frequency")
		periods = PERIODS_PER_YEAR.get(freq)
		if not periods:
			return

		before = self.get_doc_before_save()
		cadence_changed = bool(before and before.get("invoicing_frequency") != freq)
		annual = (
			flt(frappe.db.get_value("Project Contract", self.project_contract, "annual_maintenance_fee"))
			if self.get("project_contract")
			else 0
		)
		computed = flt(annual / periods, 2) if annual else 0

		# Recurring Amount: fill when blank, or re-derive on a cadence change while
		# the stored value still equals what the previous cadence would derive.
		current = flt(self.get("recurring_amount"))
		derivable = not current
		if not derivable and cadence_changed:
			prev_periods = PERIODS_PER_YEAR.get(before.get("invoicing_frequency"))
			prev_computed = flt(annual / prev_periods, 2) if (prev_periods and annual) else None
			derivable = prev_computed is not None and current == prev_computed
		if derivable and computed:
			self.recurring_amount = computed

		# Next Billing Date: seed/re-anchor to the first period boundary after
		# today (bounded roll-forward from the last billed date or start date).
		if (not self.get("next_billing_date") or cadence_changed) and self.get("start_date"):
			today = getdate(nowdate())
			nb = add_period(getdate(self.get("last_billed_on") or self.start_date), freq)
			guard = 0
			while nb <= today and guard < 600:
				nb = add_period(nb, freq)
				guard += 1
			self.next_billing_date = nb

		# Nudge when a recurring contract has no amount to bill.
		if not flt(self.get("recurring_amount")):
			frappe.msgprint(
				_("Set a Recurring Amount — this contract bills {0} but has no per-period amount, so it will not be invoiced.").format(freq),
				indicator="orange",
				alert=True,
			)

	def _materialize_feature_defaults(self):
		"""Fill blanks on feature rows so the scheduler always sees real values.

		Frequency inherits the contract-level default (the value the scheduler
		uses stays visible on the row, nothing to debug at runtime); a blank
		``next_visit_date`` would silently never be scheduled, so it anchors to
		the contract start (or today). Covers rows from every path — form,
		mappers, API.
		"""
		for row in self.get("covered_features", []):
			if not row.frequency:
				row.frequency = self.default_frequency
			if not row.next_visit_date:
				row.next_visit_date = self.start_date or nowdate()

	def _derive_end_date(self):
		"""Derive End Date from a fixed year term (§9.1).

		A one/two-year term ends the day before the anniversary of Start Date,
		so the next term begins on the anniversary (matching the paper
		agreement's successive one-year renewals). Month-to-Month, Other, or a
		blank term leave End Date untouched — the scheduler's auto-expire keys
		on End Date, so a blank one simply never expires.

		Re-derives when Start Date or the term changes, but never clobbers a
		manual override (e.g. a forward renewal): End Date is only recomputed
		while it still equals what the previous inputs would have derived.
		"""
		years = INITIAL_TERM_YEARS.get(self.get("initial_term"))
		if not (years and self.start_date):
			return
		derived = add_days(add_years(getdate(self.start_date), years), -1)
		if not self.end_date:
			self.end_date = derived
			return
		before = self.get_doc_before_save()
		if not before:
			return
		prev_years = INITIAL_TERM_YEARS.get(before.get("initial_term"))
		if not (prev_years and before.get("start_date")):
			return
		prev_derived = add_days(add_years(getdate(before.get("start_date")), prev_years), -1)
		if getdate(self.end_date) == getdate(prev_derived):
			self.end_date = derived

	def _check_single_active_contract(self):
		"""The scheduler and visit forms resolve "the" Active contract for a
		project, so two Active contracts on one project would be ambiguous."""
		if self.status != "Active" or not self.project:
			return
		other = frappe.db.exists(
			"Sapphire Maintenance Contract",
			{"project": self.project, "status": "Active", "name": ["!=", self.name]},
		)
		if other:
			frappe.throw(
				_("Project {0} already has an Active Maintenance Contract ({1}). Expire or cancel it first.").format(
					self.project, other
				)
			)

	def on_update(self):
		self._ensure_maintenance_profile()

	def _ensure_maintenance_profile(self):
		"""On activation, stub the site's Maintenance Profile if it is missing.

		The Profile (safety notes, access codes, and the site coordinates that
		power the Time Kiosk's geofenced clock-in) is otherwise created by hand
		and easily forgotten. Creates it once per project (the doctype is unique
		on project), prefilling access codes from the signed agreement, and
		raising a ToDo to fill the safety notes and coordinates. Never touches an
		existing (possibly hand-edited) Profile.
		"""
		if self.status != "Active" or not self.project:
			return
		if frappe.db.exists("Sapphire Maintenance Profile", {"project": self.project}):
			return

		access_codes = None
		if self.project_contract:
			legal = frappe.db.get_value(
				"Project Contract", self.project_contract, ["gate_code", "key_location"], as_dict=True
			)
			if legal:
				parts = [
					f"Gate/Entry: {legal.gate_code}" if legal.gate_code else None,
					f"Key: {legal.key_location}" if legal.key_location else None,
				]
				access_codes = " | ".join(p for p in parts if p) or None
				if access_codes:
					access_codes = access_codes[:140]  # Profile.access_codes is Data(140)

		try:
			profile = frappe.new_doc("Sapphire Maintenance Profile")
			profile.project = self.project
			profile.access_codes = access_codes
			profile.insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Auto-stub Maintenance Profile failed")
			return

		try:
			frappe.get_doc(
				{
					"doctype": "ToDo",
					"allocated_to": self.get("owner") or frappe.session.user,
					"reference_type": "Sapphire Maintenance Profile",
					"reference_name": profile.name,
					"description": _(
						"New maintenance site {0}: add safety instructions and site coordinates "
						"(coordinates power the Time Kiosk's clock-in suggestion)."
					).format(self.project),
				}
			).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Maintenance Profile ToDo failed")

		frappe.msgprint(
			_("Stubbed a Maintenance Profile for {0} — add its safety notes and coordinates.").format(self.project),
			indicator="blue",
			alert=True,
		)


def get_active_contract(project):
	"""Return the Active Sapphire Maintenance Contract doc for a Project, or None."""
	if not project:
		return None
	name = frappe.db.get_value(
		"Sapphire Maintenance Contract", {"project": project, "status": "Active"}, "name"
	)
	return frappe.get_doc("Sapphire Maintenance Contract", name) if name else None


def iter_seasonal_visits(contract):
	"""Yield every seasonal visit on a contract in one uniform shape.

	The single code path over both storages: the flat startup/winterization
	checkbox fields and the "Custom Seasonal Visits" child rows. Consumers
	(``tasks.generate_predictive_maintenance_records``, ``resolve_template``)
	never need to know which one a visit came from.

	Yields:
		dict: {label, target_month, template, last_generated_year,
		stamp(year)} — ``stamp`` persists the generated-year dedup mark on
		whichever storage backs the visit.
	"""
	flat = [
		("seasonal_startup", STARTUP_LABEL, "startup_month", "startup_template", "startup_last_generated_year"),
		("winterization", WINTERIZATION_LABEL, "winterization_month", "winterization_template", "winterization_last_generated_year"),
	]
	for check_field, label, month_field, template_field, year_field in flat:
		if not contract.get(check_field):
			continue
		yield {
			"label": label,
			"target_month": contract.get(month_field),
			"template": contract.get(template_field),
			"last_generated_year": contract.get(year_field),
			"stamp": lambda year, field=year_field: frappe.db.set_value(
				"Sapphire Maintenance Contract", contract.name, field, year
			),
		}
	for row in contract.get("seasonal_visits", []):
		yield {
			"label": row.visit_label,
			"target_month": row.target_month,
			"template": row.template,
			"last_generated_year": row.last_generated_year,
			"stamp": lambda year, name=row.name: frappe.db.set_value(
				"Sapphire Seasonal Visit", name, "last_generated_year", year
			),
		}


@frappe.whitelist()
def get_project_water_features(project):
	"""The project's water features, for the contract form's batch-add dialog.

	Serial Nos assigned to the Project (``custom_project``), filtered to the
	configured water-feature Item when one is set — the same filter the
	mapper's :func:`_append_features_from_project_serials` uses. ``get_list``
	so the caller's Serial No read permissions apply.

	Returns:
		list[dict]: [{value, description}] shaped for a MultiSelectList.
	"""
	if not project:
		return []
	filters = {"custom_project": project}
	water_feature_item = frappe.db.get_single_value("ERPNext Enhancements Settings", "water_feature_item")
	if water_feature_item:
		filters["item_code"] = water_feature_item
	return [
		{"value": row.name, "description": row.item_name or ""}
		for row in frappe.get_list(
			"Serial No", filters=filters, fields=["name", "item_name"], order_by="name"
		)
	]


def _month_or_default(value, default):
	"""Normalise a free-text month (Project Contract stores Data) to a Select option."""
	value = (value or "").strip().capitalize()
	return value if value in MONTHS else default


def _append_features_from_sales_order(doc, so_name):
	"""Add one covered-feature row per Sales Order Item carrying a water feature."""
	existing = {row.serial_no for row in doc.get("covered_features", [])}
	items = frappe.get_all(
		"Sales Order Item",
		filters={"parent": so_name, "custom_serial_no": ["is", "set"]},
		fields=["name", "custom_serial_no", "custom_maintenance_frequency", "custom_next_predictive_visit"],
		order_by="idx",
	)
	for item in items:
		if item.custom_serial_no in existing:
			continue
		doc.append(
			"covered_features",
			{
				"serial_no": item.custom_serial_no,
				"frequency": item.custom_maintenance_frequency,
				"next_visit_date": item.custom_next_predictive_visit or nowdate(),
				"sales_order_item": item.name,
			},
		)
		existing.add(item.custom_serial_no)


@frappe.whitelist()
def make_contract_from_sales_order(source_name):
	"""Map a submitted Sales Order into a draft Maintenance Contract.

	Whitelisted; called by the Sales Order form's "Create > Maintenance
	Contract" button via ``frappe.model.open_mapped_doc``. Water-feature item
	rows become ``covered_features``; a Signed maintenance-type Project
	Contract for the same project is linked when one exists.
	"""
	so = frappe.get_doc("Sales Order", source_name)
	doc = frappe.new_doc("Sapphire Maintenance Contract")
	doc.customer = so.customer
	doc.project = so.project
	doc.sales_order = so.name
	doc.start_date = so.transaction_date
	_append_features_from_sales_order(doc, so.name)

	if so.project:
		project_contract = frappe.db.get_value(
			"Project Contract",
			{"project": so.project, "template_key": "maintenance", "status": "Signed", "docstatus": 1},
			"name",
		)
		if project_contract:
			_apply_project_contract(doc, frappe.get_doc("Project Contract", project_contract))

	return doc


@frappe.whitelist()
def make_contract_from_project_contract(source_name):
	"""Map a Signed Maintenance Services Agreement into a draft Maintenance Contract.

	Whitelisted; called by the Project Contract form's "Create > Maintenance
	Contract" button via ``frappe.model.open_mapped_doc``. Pulls the legal
	terms (frequency, invoicing cadence, start date, seasonal options) and, if
	the project has a submitted Maintenance Sales Order, links it and maps its
	water-feature items into ``covered_features``.
	"""
	contract = frappe.get_doc("Project Contract", source_name)
	if contract.template_key != "maintenance":
		frappe.throw(_("{0} is not a Maintenance Services Agreement.").format(source_name))

	doc = frappe.new_doc("Sapphire Maintenance Contract")
	if contract.party_type == "Customer":
		doc.customer = contract.party
	doc.project = contract.project
	_apply_project_contract(doc, contract)

	if contract.project:
		so_name = frappe.db.get_value(
			"Sales Order",
			{"project": contract.project, "order_type": "Maintenance", "docstatus": 1},
			"name",
		)
		if so_name:
			doc.sales_order = so_name
			_append_features_from_sales_order(doc, so_name)

		# No Maintenance Sales Order? Fall back to the project's own water-feature
		# Serial Nos so the covered-feature grid arrives filled (the same source
		# the verbal-arrangement path uses) instead of forcing a manual "Add Water
		# Features" run on every signed-agreement handoff.
		if not doc.get("covered_features"):
			_append_features_from_project_serials(doc, contract.project)

	if not doc.default_template and (doc.project or doc.customer):
		from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record.sapphire_maintenance_record import (
			resolve_template,
		)

		doc.default_template = resolve_template(project=doc.project, customer=doc.customer)

	return doc


@frappe.whitelist()
def make_contract_from_project(source_name):
	"""Map a bare Project into a draft Maintenance Contract.

	Whitelisted; called by the Project form's "Create > Maintenance Contract"
	button via ``frappe.model.open_mapped_doc``. This is the entry point for
	**verbal/legacy arrangements** that have no Sales Order or written
	agreement: covered features prefill from the project's own Serial Nos
	(``Serial No.custom_project``, filtered to the configured water-feature
	Item when one is set). A submitted Maintenance Sales Order and a Signed
	maintenance-type Project Contract are linked when they happen to exist,
	but neither is required.
	"""
	project = frappe.get_doc("Project", source_name)
	if not project.customer:
		frappe.throw(_("Set a Customer on Project {0} first — the Maintenance Contract needs one.").format(source_name))

	doc = frappe.new_doc("Sapphire Maintenance Contract")
	doc.customer = project.customer
	doc.project = source_name

	so_name = frappe.db.get_value(
		"Sales Order",
		{"project": source_name, "order_type": "Maintenance", "docstatus": 1},
		"name",
	)
	if so_name:
		doc.sales_order = so_name
		_append_features_from_sales_order(doc, so_name)

	_append_features_from_project_serials(doc, source_name)

	project_contract = frappe.db.get_value(
		"Project Contract",
		{"project": source_name, "template_key": "maintenance", "status": "Signed", "docstatus": 1},
		"name",
	)
	if project_contract:
		_apply_project_contract(doc, frappe.get_doc("Project Contract", project_contract))

	if not doc.default_template:
		from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record.sapphire_maintenance_record import (
			resolve_template,
		)
		doc.default_template = resolve_template(project=source_name, customer=project.customer)

	return doc


def _append_features_from_project_serials(doc, project):
	"""Add covered-feature rows for the project's water features (Serial Nos)."""
	existing = {row.serial_no for row in doc.get("covered_features", [])}
	filters = {"custom_project": project}
	water_feature_item = frappe.db.get_single_value("ERPNext Enhancements Settings", "water_feature_item")
	if water_feature_item:
		filters["item_code"] = water_feature_item
	for serial in frappe.get_all("Serial No", filters=filters, pluck="name", order_by="name"):
		if serial in existing:
			continue
		doc.append("covered_features", {"serial_no": serial, "next_visit_date": nowdate()})
		existing.add(serial)


def _apply_project_contract(doc, contract):
	"""Copy the legal agreement's operational terms onto the contract doc."""
	doc.project_contract = contract.name
	doc.start_date = doc.start_date or contract.agreement_start_date or contract.contract_date
	doc.initial_term = doc.initial_term or contract.get("initial_term")
	doc.invoicing_frequency = contract.invoicing_frequency or "Per Visit"

	default_frequency = PROJECT_CONTRACT_FREQUENCY_MAP.get(contract.visit_frequency)
	if default_frequency:
		doc.default_frequency = doc.default_frequency or default_frequency
		# validate() materializes the default on save, but the mapped doc is
		# shown unsaved first — prefill so the user sees the cadence rows.
		for row in doc.get("covered_features", []):
			if not row.frequency:
				row.frequency = doc.default_frequency

	included = {row.option_key for row in contract.get("service_options", []) if row.included}
	if "startup" in included or "package" in included:
		doc.seasonal_startup = 1
		doc.startup_month = _month_or_default(contract.startup_month, "April")
	if "winterization" in included or "package" in included:
		doc.winterization = 1
		doc.winterization_month = _month_or_default(contract.winterization_month, "October")


def autocreate_maintenance_contract_on_signed(doc, method=None):
	"""Draft the operational Maintenance Contract when its agreement is Signed.

	Project Contract ``on_submit`` + ``on_update_after_submit`` hook, covering
	both signing paths: submitting an already-"Signed" draft, and the
	post-submit "Mark as Signed" button. Fires only for a maintenance agreement
	that has a project and no live (Draft/Active) Maintenance Contract, so
	re-saves, amendments, cancelled/expired predecessors and non-maintenance
	contracts never double-create. The draft is left for a human to review and
	set Active; activation stays the gate.
	"""
	if doc.get("template_key") != "maintenance" or doc.get("status") != "Signed" or not doc.get("project"):
		return
	# Post-submit updates fire on every save — act only on the edge into Signed.
	# The submit event (docstatus 0->1) is itself one-shot, so it needs no guard.
	if method == "on_update_after_submit":
		before = doc.get_doc_before_save()
		if before and before.get("status") == "Signed":
			return
	if frappe.db.exists(
		"Sapphire Maintenance Contract", {"project": doc.project, "status": ["in", ["Draft", "Active"]]}
	):
		return
	# The signing path may be a Guest (online signature), where a msgprint would
	# render internal desk instructions into a customer's response. Staff get the
	# toast; a Guest-path failure is escalated to the contract owner instead, so
	# it still reaches a human.
	is_desk_user = frappe.session.user not in ("Guest", None)

	# A savepoint so a failed mapping rolls back cleanly instead of poisoning the
	# caller's transaction — the online-signing path commits straight afterwards.
	frappe.db.savepoint("esign_autocreate")
	muted = frappe.flags.mute_messages
	try:
		# The mapped contract's own validate() can msgprint (e.g. the recurring
		# -amount nudge), which would land in an anonymous signer's response just
		# as the messages below would. Mute for the guest path only, and restore
		# the previous value afterwards so this never leaks into the rest of the
		# request.
		if not is_desk_user:
			frappe.flags.mute_messages = True
		operational = make_contract_from_project_contract(doc.name)
		operational.insert(ignore_permissions=True)
	except Exception:
		frappe.db.rollback(save_point="esign_autocreate")
		frappe.log_error(frappe.get_traceback(), "Auto-create Maintenance Contract on Signed failed")
		if is_desk_user:
			frappe.msgprint(
				_(
					"Could not auto-draft the operational Maintenance Contract for this agreement — "
					"create it manually via Create > Maintenance Contract. (Details logged.)"
				),
				indicator="red",
				alert=True,
			)
		else:
			_notify_owner_autocreate_failed(doc)
		return
	finally:
		frappe.flags.mute_messages = muted

	if is_desk_user:
		frappe.msgprint(
			_("Drafted Maintenance Contract {0} from this signed agreement — review it and set it Active.").format(
				frappe.get_link_to_form("Sapphire Maintenance Contract", operational.name)
			),
			indicator="green",
			alert=True,
		)


def _notify_owner_autocreate_failed(doc):
	"""Tell the contract owner that the operational contract needs creating by hand.

	Used when the agreement was signed online: there is no desk session to show a
	message to, and a silently missing Maintenance Contract would stall every
	downstream step (scheduling, dispatch, billing).
	"""
	try:
		owner = doc.get("owner")
		if not owner:
			return
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"subject": _("Action needed: draft the Maintenance Contract for {0}").format(doc.name),
				"email_content": _(
					"{0} was signed online, but the operational Maintenance Contract could not be "
					"drafted automatically. Open the agreement and use Create &gt; Maintenance Contract. "
					"(Details are in the Error Log.)"
				).format(doc.name),
				"document_type": "Project Contract",
				"document_name": doc.name,
				"for_user": owner,
				"type": "Alert",
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Auto-create failure notification failed")
