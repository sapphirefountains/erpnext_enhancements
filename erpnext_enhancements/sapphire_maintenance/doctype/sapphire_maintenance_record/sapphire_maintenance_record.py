"""Controller for the Sapphire Maintenance Record doctype.

A Maintenance Record is the submittable field-service "visit" document at the
heart of the Sapphire Maintenance subsystem. Its content is instantiated from
a Sapphire Maintenance Template, which is *composed* of reusable Sapphire
Maintenance Sections; each section type lands in its own typed child table:

  * Equipment Inspection -> ``maintenance_results`` (Pass/Fail/Replace/Other)
  * Water Chemistry      -> ``chemistry_readings``  (value vs target range)
  * Cleaning Tasks       -> ``cleaning_tasks``      (done / not done)
  * Chemical Dosing      -> ``consumables``         (qty consumed -> stock issue)

Visit shapes: a record normally covers one water feature (``serial_no`` set).
When the project's Sapphire Maintenance Contract is "Per Site Visit", one
record covers every covered feature — ``serial_no`` stays empty and each child
row is tagged with its feature's ``serial_no`` instead.

Lifecycle / wiring:
  * ``crew`` / ``planned_hours`` / ``full_day`` (P1.8): everyone booked on the visit besides
    ``technician``, who stays the person filling it in. ``validate`` tidies the crew
    (``sapphire_maintenance.visit_crew.validate_crew``) and an ``on_update`` doc-event mirrors
    it into assignments (``visit_crew.on_record_update``).
  * ``validate`` computes per-reading ``out_of_range`` flags and the parent
    ``has_out_of_range_readings`` (drives the "Maintenance Reading Out of
    Range" Notification fixture on submit).
  * On the save that moves a visit into Pending Review, ``validate`` dates it
    (blank ``visit_date`` -> today) and closes its clock from the kiosk
    interval (``_finish_on_review``, v1.595.1), so an approval days later
    neither rolls the schedule from the approval day nor takes a later
    interval as the clock-out. Since v1.610.0 that save also needs the
    technician's signature (``technician_signature``), which ``validate``
    stamps with who signed and when (``_check_signatures``).
  * ``on_submit`` enqueues the background worker
    ``api.maintenance_workflow.process_maintenance_submission`` (Stock Entry,
    Timesheet, Warranty Claim, Sales Invoice). The next-visit scheduling
    update runs via the ``on_submit`` doc-event registered in hooks.py
    (``api.maintenance_scheduling.update_next_visit_dates``).
  * The doctype has a workflow (``workflow_state``) and the "Maintenance
    Review Needed" / "Maintenance Finalized" Notifications — see fixtures.
  * ``route`` is "maintenance-records": exposed on the customer portal and
    rendered by ``sapphire_maintenance_record.html`` (see ``get_context``).

Whitelisted helpers used by the desk form's JS: ``get_visit_payload``
(instantiate the form's child tables from the resolved template) and
``get_dashboard_context`` (technician on-site briefing).
"""

import datetime
import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, now_datetime, nowdate

from erpnext_enhancements.sapphire_maintenance.task_rules import task_answered
from erpnext_enhancements.sapphire_maintenance.visit_days import checklist_day, scheduled_on

#: A project name that can lead a visit's name. v16's make_autoname refuses a series outside
#: ``^[\w\- /.#{}]+$`` (``NamingSeries.validate``); ``.``, ``#`` and braces are series syntax
#: and a slash reads as a path in a link, so this is the safe part of that set. The length
#: keeps ``<project>-2026-V###`` and its tabSeries key well inside their columns. Every
#: project on prod is ``PRJ-#####``
#: (checked 2026-10-12), but Project allows rename, and an insert that raises here would
#: stop the nightly scheduler at that contract.
PROJECT_IN_NAME = re.compile(r"[\w\- ]{1,60}")

#: The workflow state a technician's Finish (or the form's Request Review) moves a visit into.
#: Kept in step with ``review_digest.REVIEW_STATE`` and the workflow fixture.
REVIEW_STATE = "Pending Review"

#: What a signature field may hold (v1.610.0). Both signatures are printed straight into an
#: ``<img src="...">`` on the customer's report and the portal page, unescaped, so a value is a
#: drawn image and nothing else: a data URI whose payload is base64, which has no quote or angle
#: bracket to break out of the attribute with. The wizard's pad and the Desk's Signature control
#: both draw PNG. The client's pad has been in use since before this check, so it also takes
#: the other raster types a browser can export rather than refusing a signature already drawn.
TECHNICIAN_SIGNATURE = re.compile(r"data:image/png;base64,[A-Za-z0-9+/]+={0,2}")
CLIENT_SIGNATURE = re.compile(r"data:image/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/]+={0,2}")

#: The first visit day the technician's signature is required for (v1.614.0). Signatures came
#: in with v1.610.0 on 2026-10-13, and a visit done before that day was done before anyone could
#: sign it: those still waiting to go for review (Nik: "exemption is fine") go without one. The
#: visit must also have been *made* before that day (``creation``): ``visit_date`` is editable, and
#: a visit made since and backdated is not one done before signatures existed. A
#: visit with **no** ``visit_date`` is not exempt. It is a blank scheduler draft, which the move
#: into review would date today (``_finish_on_review``), and on approval would roll its contract
#: forward and make Stock Entries, a Timesheet and a draft invoice for a visit that never
#: happened: thirteen of them, 0% complete, were sent for review in one bulk Request Review on
#: 2026-10-13 and correctly refused.
TECHNICIAN_SIGNATURE_FROM = datetime.date(2026, 10, 13)

#: ``client_sign_off_method``: drawn on the wizard's pad or the form, or signed from an emailed
#: link (``sapphire_maintenance/sign_off.py``, which writes "By email" with ``db_set``).
CLIENT_SIGNED_ON_SITE = "On site"


class SapphireMaintenanceRecord(Document):
	# This doctype has ``has_web_view`` set (its ``/maintenance-records`` portal
	# view, rendered by ``get_context`` below) but subclasses plain ``Document``
	# rather than ``WebsiteGenerator``. Frappe's website router
	# (``DocumentPage.get_condition_field``) dereferences
	# ``controller.website.condition_field`` for *every* web-view doctype while
	# resolving any unmatched path, so without an explicit ``website`` attribute
	# it raises ``AttributeError`` and takes down all website/portal rendering
	# (the lookup is Redis-cached, so a deploy's cache flush re-exposes it).
	# Mirror ``WebsiteGenerator``'s default ``frappe._dict()`` and gate web
	# routing to submitted visits (docstatus == 1) — drafts/cancelled records
	# are never public pages.
	website = frappe._dict(condition_field="docstatus")

	def autoname(self):
		"""``PRJ-00179-2026-V001``: the project, the visit's year, and its number that year.

		Lisa, reviewing the first customer report on 2026-10-12: the report number (a random
		ID such as ``2hkckpg374``) meant nothing; could it be the PRJ number? A project has
		many visits, so the project and the year lead and the visit's number follows (Nik:
		"add the year"). The year is the visit's own, its date or else the scheduled date, so
		a visit drafted in December for January counts in January. It is fixed at insert: a
		visit drafted for 2 January and done on 31 December keeps its 2027 name while the
		report's contract line, which reads the visit date, says 2026. That happens only in
		the scheduler's week around New Year, and renaming a visit after its review email has
		named it would be worse.

		A visit with no project keeps Frappe's random name, and so does one whose project
		name could not lead a naming series (:data:`PROJECT_IN_NAME`).

		Visits named before v1.597.0 were at first left with their random names, because
		review emails and links name them. Nik, 2026-10-13, still seeing ``4lvpg04h6h`` a day
		later (no visit had been created since, so every visit on screen was an old one), had
		them renamed: ``patches/rename_hash_named_visits.py`` numbers them into this scheme in
		the order they were done, keeps the old name in ``legacy_name``, and moves this series
		past them. The Visit Wizard and Visit Review pages, and the wizard's endpoints, still
		open a visit by its old name (``sapphire_maintenance.visit_names``); a link to the Desk
		form by an old name does not.
		"""
		project = self.get("project") or ""
		if not PROJECT_IN_NAME.fullmatch(project):
			return
		from frappe.model.naming import make_autoname

		year = getdate(self.get("visit_date") or self.get("scheduled_visit_date") or nowdate()).year
		self.name = make_autoname(f"{project}-{year}-V.###", doc=self)

	def validate(self):
		# The crew (P1.8): no row for the technician, no person twice, no negative hours. See
		# sapphire_maintenance/visit_crew.py; the on_update doc_event mirrors it into ToDos.
		from erpnext_enhancements.sapphire_maintenance.visit_crew import validate_crew

		validate_crew(self)
		self.has_out_of_range_readings = 1 if evaluate_reading_ranges(self.chemistry_readings) else 0
		self.completion_percent = compute_completion_percent(self)
		self._settle_task_states()
		# Follow-ups and parts needed (v1.600.0): tidied, and kept on a feature this visit covers.
		from erpnext_enhancements.sapphire_maintenance.follow_ups import check_follow_ups

		check_follow_ups(self)
		self._autofill_clock_in()
		self._check_signatures()
		self._finish_on_review()
		self._check_fountain_photo()

	def _check_signatures(self):
		"""Accept only a drawn signature, and stamp who signed for the technician and when.

		Nik, 2026-10-13: "we need the technicians to sign the maintenance visits once they
		complete it so the printed form has that." The technician signs on the wizard's
		Wrap-up (or in the form's field) and the report prints the signature with the signer's
		name and the time. The stamp is whoever's session saved the new signature, not the
		visit's ``technician``: a substitute covering the site signs as themselves. A
		signature left as it was keeps its stamp (a reviewer's later save is not a signing);
		a cleared one clears it.

		Both signature fields are printed unescaped into an ``<img src>``, so each must be a
		drawn image (:data:`TECHNICIAN_SIGNATURE`, :data:`CLIENT_SIGNATURE`). Only a changed
		value is checked, so a visit is never made unsaveable by what it already holds.
		"""
		client = self.get("client_sign_off") or ""
		if client and self.has_value_changed("client_sign_off") and not CLIENT_SIGNATURE.fullmatch(client):
			frappe.throw(
				_("The client's sign-off must be a signature drawn on the visit."),
				title=_("Signature not accepted"),
			)
		self._stamp_client_sign_off(client)
		signature = self.get("technician_signature") or ""
		if not signature:
			self.technician_signed_by = None
			self.technician_signed_on = None
			return
		if not self.has_value_changed("technician_signature"):
			before = self.get_doc_before_save()
			if before is not None:
				self.technician_signed_by = before.get("technician_signed_by")
				self.technician_signed_on = before.get("technician_signed_on")
			return
		if not TECHNICIAN_SIGNATURE.fullmatch(signature):
			frappe.throw(
				_("The technician's signature must be drawn on the visit."),
				title=_("Signature not accepted"),
			)
		self.technician_signed_by = frappe.session.user
		self.technician_signed_on = now_datetime()

	def _stamp_client_sign_off(self, client):
		"""A client signature drawn on site (the wizard's pad, or the form) is stamped "On site"
		and the time (v1.614.0). One signed from an emailed link never passes through here: the
		public page writes it with ``db_set`` (``sign_off.record_signature``), with the name the
		client typed. A signature left as it was keeps its stamp; a cleared one clears it."""
		if not self.has_value_changed("client_sign_off"):
			return
		if not client:
			self.client_signed_by = None
			self.client_signed_on = None
			self.client_sign_off_method = None
			return
		self.client_signed_by = None
		self.client_signed_on = now_datetime()
		self.client_sign_off_method = CLIENT_SIGNED_ON_SITE

	def _signature_exempt(self):
		"""Done before signatures existed (:data:`TECHNICIAN_SIGNATURE_FROM`): dated before that day
		**and** made before it. ``visit_date`` alone is editable, so a visit made since could be
		backdated past the gate (v1.614.0 review); ``creation`` cannot. Never a visit with no date:
		that is a blank draft, and the date going for review would give it is today."""
		visit_date = self.get("visit_date")
		created = self.get("creation")
		return (
			bool(visit_date)
			and bool(created)
			and getdate(visit_date) < TECHNICIAN_SIGNATURE_FROM
			and getdate(created) < TECHNICIAN_SIGNATURE_FROM
		)

	def _settle_task_states(self):
		"""A cleaning task is done, N/A, or neither, never both: done wins.

		N/A (v1.598.0) is for a task this visit does not need, such as the Myers heater check,
		which printed "Not done" on an October report. The wizard clears one when the other is
		set, but the Desk grid shows both boxes, and a task ticked both ways would print as
		done while counting as not needed.
		"""
		for row in self.get("cleaning_tasks") or []:
			if row.get("is_done") and row.get("not_applicable"):
				row.not_applicable = 0

	def _check_fountain_photo(self):
		"""The fountain photo must be one uploaded to this visit, or by the person saving it.

		The customer's report inlines it (``visit_report.image_data_uri``) only when it is a
		File attached to this visit. But frappe's ``attach_files_to_document`` hook attaches any
		*unattached* File whose URL an Attach field names, with no permission check, so naming
		someone else's loose private file would otherwise attach it here and print it. A Desk
		upload on an unsaved visit is attached to the form's temporary name until frappe
		relinks it after insert, so the uploader's own File is accepted wherever it hangs.
		"""
		url = self.get("fountain_photo") or ""
		if not url or not self.has_value_changed("fountain_photo"):
			return
		if url.startswith(("/files/", "/private/files/")):
			for row in frappe.get_all(
				"File",
				filters={"file_url": url},
				fields=["attached_to_doctype", "attached_to_name", "owner"],
			):
				if row.get("owner") == frappe.session.user:
					return
				if row.get("attached_to_doctype") == self.doctype and row.get("attached_to_name") == self.name:
					return
		frappe.throw(
			_("The fountain photo must be a photo taken on this visit. Take it again from the visit."),
			title=_("Photo not from this visit"),
		)

	def before_submit(self):
		# Where no workflow is active the technician's own submit is the finish. With the
		# workflow, the visit was closed out when it went for review (_finish_on_review), and a
		# later approval is a backfill by then, which this skips.
		self._autofill_clock_out()
		self._validate_mandatory_rows()

	def _finish_on_review(self):
		"""The technician has finished: stamp the day of the visit and close out its clock.

		Runs on the save that moves the visit into Pending Review, from the Visit Wizard's
		Finish or the form's Request Review. Until v1.595.1 nothing was settled until the
		reviewer's Approve & Submit, often days later, and with ``visit_date`` blank (the
		scheduler drafts visits without one, and the wizard's "Date of visit" is empty for
		"today"):

		* ``update_next_visit_dates`` rolled the contract forward from the **approval** day
		  (``visit_date or nowdate()``). State Capitol, sent for review on 2026-10-09 and
		  approved on 2026-10-12, had its next visit counted from the 12th.
		* ``_autofill_clock_out`` took the technician's latest kiosk interval on the project at
		  approval time, which on a weekly site can be the *next* visit's, giving a clock-out
		  days after the clock-in and a timesheet to match.

		So ``visit_date`` becomes today when it is blank, the day the technician finished,
		which is what the wizard's empty "Date of visit" already means. A date the technician
		chose (a backfill) is kept. The clock then closes out here, while the kiosk interval
		is still this visit's; at approval the visit is dated in the past and
		``_autofill_clock_out`` leaves it alone (:meth:`_is_backfill`). A visit sent back and
		sent again keeps the date it was first given.

		And since v1.610.0 the technician signs first (:meth:`_check_signatures`): this is the
		one save every way of finishing passes through, the wizard's Finish and the form's
		Request Review alike, so the check lives here and not in either. Only the move into
		review asks for it. A visit already waiting, a reviewer's Approve & Submit and a site
		with no workflow are not stopped. Since v1.614.0 a visit dated before signatures existed
		is not stopped either (:meth:`_signature_exempt`); an undated one still is.
		"""
		if self.docstatus != 0 or (self.get("workflow_state") or "") != REVIEW_STATE:
			return
		if not self.has_value_changed("workflow_state"):
			return
		if not self.get("technician_signature") and not self._signature_exempt():
			frappe.throw(
				_("The technician must sign the visit before it goes for review."),
				title=_("Technician signature needed"),
			)
		if not self.get("visit_date"):
			self.visit_date = getdate(nowdate())
		self._autofill_clock_out()

	def _validate_mandatory_rows(self):
		"""Block submit while template-mandated rows are unanswered.

		Rows carry ``is_mandatory`` copied from their template section item
		(``get_visit_payload``). "Answered" matches compute_completion_percent:
		a selection/answer, a non-zero reading, a task done, N/A or with a note
		(:func:`task_answered`). Consumables are exempt — an untouched qty-0
		dosing prefill is a legitimate "none used".
		"""
		def tag(row, label):
			if row.serial_no and not self.serial_no:
				return _("{0} ({1})").format(label, row.serial_no)
			return label

		missing = []
		for row in self.get("maintenance_results", []):
			if row.is_mandatory and not (row.selection or row.answer):
				missing.append(tag(row, row.question))
		for row in self.get("chemistry_readings", []):
			if row.is_mandatory and not flt(row.reading_value):
				missing.append(tag(row, row.reading))
		for row in self.get("cleaning_tasks", []):
			if row.is_mandatory and not task_answered(row):
				missing.append(tag(row, row.task))
		if missing:
			frappe.throw(
				_("These required items are still unanswered:")
				+ "<br>"
				+ "<br>".join(frappe.utils.escape_html(item) for item in missing),
				title=_("Required Items Missing"),
			)

	def _job_interval(self, statuses):
		"""Latest Job Interval for this record's technician + project in the given statuses."""
		if not self.technician or not self.project:
			return None
		employee = frappe.db.get_value("Employee", {"user_id": self.technician}, "name")
		if not employee:
			return None
		return frappe.db.get_value(
			"Job Interval",
			{"employee": employee, "project": self.project, "status": ["in", statuses]},
			["name", "start_time", "end_time", "total_paused_seconds"],
			order_by="start_time desc",
			as_dict=True,
		)

	def _is_backfill(self):
		"""True when this record documents work done on an earlier day.

		Set by ``api.maintenance_visit.create_visit`` when a technician logs a
		visit for a past date. It gates the kiosk clock autofill: a Job Interval
		running *now* belongs to today's job, not to the visit being written up
		for last Tuesday, and stamping it would bill the wrong labour onto the
		wrong day's timesheet.
		"""
		if not self.get("visit_date"):
			return False
		return getdate(self.visit_date) < getdate(nowdate())

	def _autofill_clock_in(self):
		"""Seed clock-in from the technician's running kiosk interval on this project.

		The kiosk (Job Interval) already knows when work started — don't make
		the tech enter it twice. Only fills a blank field; manual entries win.
		A backfilled visit is left alone (see :meth:`_is_backfill`) — the tech
		types the real times, or leaves them blank and no timesheet is made.
		"""
		if self.clock_in_time or self.docstatus != 0 or self._is_backfill():
			return
		interval = self._job_interval(["Open", "Paused"])
		if interval:
			self.clock_in_time = interval.start_time

	def _autofill_clock_out(self):
		"""When the visit is finished, close out blank clock fields from the kiosk interval.

		Called as the visit goes for review (:meth:`_finish_on_review`) and again on
		submit, which is the finish where no workflow is active. A still-running interval
		means the tech is finishing before clocking out — stamp "now" and carry the
		interval's pause time. A recently completed interval supplies its real end time.
		A backfilled visit is skipped entirely: "now" is the wrong day for it.
		"""
		if self._is_backfill():
			return
		if self.clock_in_time and not self.clock_out_time:
			interval = self._job_interval(["Open", "Paused"])
			if interval:
				self.clock_out_time = frappe.utils.now_datetime()
				if not self.paused_duration:
					self.paused_duration = interval.total_paused_seconds or 0
			else:
				interval = self._job_interval(["Completed"])
				if interval and interval.end_time and str(interval.end_time) >= str(self.clock_in_time):
					self.clock_out_time = interval.end_time
					if not self.paused_duration:
						self.paused_duration = interval.total_paused_seconds or 0

	def on_submit(self):
		"""Submit lifecycle hook: kick off downstream automation.

		Enqueues ``api.maintenance_workflow.process_maintenance_submission`` on
		the "default" queue to generate Stock Entry, Timesheet, Warranty Claim
		and a draft Sales Invoice in the background. Next-visit dates are
		updated by the ``on_submit`` doc-event in hooks.py
		(``api.maintenance_scheduling.update_next_visit_dates``) — not called
		here, so it runs exactly once.
		"""
		frappe.enqueue(
			"erpnext_enhancements.api.maintenance_workflow.process_maintenance_submission",
			record_name=self.name,
			queue="default"
		)

	def get_context(self, context):
		"""Populate the web/portal render context for ``/maintenance-records``.

		Called by Frappe's web-view machinery when the record is rendered via the
		``sapphire_maintenance_record.html`` template. Sets ``context.show_labor``
		from the parent Maintenance Sales Order's ``custom_display_labor_hours``
		flag (controls whether the Service Duration block is shown), and adds the
		portal breadcrumb back to the "Maintenance Records" listing.

		Args:
			context: The mutable web render context (modified in place).
		"""
		context.show_labor = False
		if self.project:
			show_labor = frappe.db.get_value("Sales Order",
				{"project": self.project, "order_type": "Maintenance", "docstatus": 1},
				"custom_display_labor_hours")
			context.show_labor = True if show_labor else False

		context.parents = [{"name": _("Maintenance Records"), "route": "maintenance-records"}]


def get_list_context(context):
	"""Portal list config for ``/maintenance-records``.

	Row scoping is enforced by ``permission_query_conditions`` (a customer sees
	only their own submitted records); this just sets the listing chrome.
	"""
	context.title = _("Maintenance Records")
	context.no_breadcrumbs = True
	context.order_by = "creation desc"


def compute_completion_percent(doc):
	"""How much of the visit form has been filled in, as 0–100.

	Counted as answered: inspection rows with a selection or answer, readings
	with a value, cleaning tasks done, N/A or with a note (:func:`task_answered`).
	Consumables don't count — an untouched qty-0 dosing prefill is a legitimate
	"none used".
	Returns 0 for a record with no section rows. Pure function — no DB access.
	"""
	answered = total = 0
	for row in doc.get("maintenance_results", []):
		total += 1
		if row.get("selection") or row.get("answer"):
			answered += 1
	for row in doc.get("chemistry_readings", []):
		total += 1
		if flt(row.get("reading_value")):
			answered += 1
	for row in doc.get("cleaning_tasks", []):
		total += 1
		if task_answered(row):
			answered += 1
	return round(answered / total * 100, 1) if total else 0



def evaluate_reading_ranges(rows):
	"""Set ``out_of_range`` on each chemistry-reading row; return flagged rows.

	A row is evaluated only when a value was actually entered — blank/zero
	means "not measured" (Float fields can't distinguish empty from 0; a true
	zero reading should be recorded in the row's notes). Out of range = value
	below ``min_value`` or above ``max_value``, considering only bounds that
	are set (non-zero). Pure function over the row objects — no DB access.
	"""
	flagged = []
	for row in rows or []:
		row.out_of_range = 0
		value = flt(row.reading_value)
		if not value:
			continue
		low, high = flt(row.min_value), flt(row.max_value)
		if (low and value < low) or (high and value > high):
			row.out_of_range = 1
			flagged.append(row)
	return flagged


def resolve_template(project=None, customer=None, contract=None, feature_row=None, visit_label=None):
	"""Resolve which Sapphire Maintenance Template applies to a visit/feature.

	Precedence: seasonal visit row (matched by ``visit_label`` on the
	contract) -> contract feature row -> contract default -> Active template
	for the Project -> Active template for the Customer. Returns a template
	name or None.
	"""
	if visit_label and contract:
		from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract.sapphire_maintenance_contract import (
			iter_seasonal_visits,
		)
		for visit in iter_seasonal_visits(contract):
			if visit["label"] == visit_label and visit["template"]:
				return visit["template"]
	if feature_row is not None and feature_row.get("template"):
		return feature_row.get("template")
	if contract and contract.get("default_template"):
		return contract.default_template
	template = frappe.db.get_value(
		"Sapphire Maintenance Template", {"project": project, "status": "Active"}, "name"
	) if project else None
	if not template:
		if not customer and project:
			customer = frappe.db.get_value("Project", project, "customer")
		if customer:
			template = frappe.db.get_value(
				"Sapphire Maintenance Template", {"customer": customer, "status": "Active"}, "name"
			)
	return template


def _reading_overrides(serial_no):
	"""Per-feature chemistry range overrides, keyed by normalised reading label."""
	if not serial_no:
		return {}
	rows = frappe.get_all(
		"Sapphire Reading Range Override",
		filters={"parent": serial_no, "parenttype": "Serial No"},
		fields=["reading", "min_value", "max_value"],
	)
	return {(row.reading or "").strip().lower(): row for row in rows}


@frappe.whitelist()
def get_visit_payload(
	project=None, serial_no=None, maintenance_contract=None, technician=None, visit_label=None, visit_date=None
):
	"""Instantiate the visit form's child-table rows from the resolved template.

	Whitelisted; called by the desk form JS (``populate_from_template``).
	Resolves the covered features (one, or all of a Per Site Visit contract's),
	resolves each feature's template, and walks the template's sections in
	order, emitting rows typed by section:

	Every row carries ``section``, ``section_title`` and ``serial_no``; the
	template section item's ``is_mandatory`` rides along on results, readings
	and tasks (enforced in ``before_submit``), its custom ``options`` on
	results, and its ``default_qty``/``qty_step`` plus the Item's
	name/stock-UOM on consumables — one enrichment point serving the desk
	form, the visit wizard, print and portal renders alike.

	``visit_date`` is the day the checklist is for (v1.619.0; blank: today). A template line
	whose ``days`` names other weekdays is left off, so a daily site's Tuesday visit carries
	the agreement's Tuesday tasks and not Monday's (``visit_days``).

	A template ticked **Once per Site Visit** (``site_wide``, v1.633.0) is instantiated once on
	a Per Site Visit, its rows untagged (no ``serial_no``: the Whole site tab), however many
	water features resolve to it. Hardware's and Sanctuary's seasonal forms name each
	fountain's steps themselves, and every feature used to get the whole list: seven copies of
	the courtyard heaters and the East Wall drain at Hardware, thirteen at Sanctuary.

	Returns:
		dict: {
			"template":    the resolved template name (of the first feature),
			"results":     Equipment Inspection rows {question, options,
			               is_mandatory, section, section_title, serial_no},
			"readings":    Water Chemistry rows {reading, uom, min_value,
			               max_value, is_mandatory, section, section_title,
			               serial_no} (Serial No range overrides applied),
			"tasks":       Cleaning Tasks rows {task, is_mandatory, section,
			               section_title, serial_no},
			"consumables": Chemical Dosing rows {item, item_name, uom, qty: 0,
			               default_qty, qty_step, warehouse, section,
			               section_title, serial_no} (warehouse = the feature's
			               on-site chemical store only. Informational since
			               v1.594.0, when approval stopped issuing chemicals; uom
			               is the template's dose unit, else the stock unit),
		}
	"""
	contract = None
	if maintenance_contract:
		contract = frappe.get_doc("Sapphire Maintenance Contract", maintenance_contract)
		project = project or contract.project

	# Which water features does this visit cover?
	features = []
	per_site = bool(contract and contract.visit_shape == "Per Site Visit" and not serial_no)
	site_wide_done = set()
	if per_site:
		features = [
			{"serial_no": row.serial_no, "template": row.template, "default_warehouse": row.default_warehouse}
			for row in contract.covered_features
		]
	else:
		feature = {"serial_no": serial_no, "template": None, "default_warehouse": None}
		if contract and serial_no:
			for row in contract.covered_features:
				if row.serial_no == serial_no:
					feature.update({"template": row.template, "default_warehouse": row.default_warehouse})
					break
		features = [feature]

	customer = contract.customer if contract else None
	payload = {"template": None, "results": [], "readings": [], "tasks": [], "consumables": []}
	weekday = checklist_day(visit_date)
	template_cache = {}
	item_cache = {}

	def item_details(item_code):
		if item_code not in item_cache:
			item_cache[item_code] = frappe.db.get_value(
				"Item", item_code, ["item_name", "stock_uom"], as_dict=True
			) or frappe._dict()
		return item_cache[item_code]

	for feature in features:
		template_name = resolve_template(
			project=project,
			customer=customer,
			contract=contract,
			feature_row=feature,
			visit_label=visit_label,
		)
		if not template_name:
			continue
		payload["template"] = payload["template"] or template_name

		if template_name not in template_cache:
			template = frappe.get_doc("Sapphire Maintenance Template", template_name)
			template_cache[template_name] = (
				[frappe.get_doc("Sapphire Maintenance Section", row.section) for row in template.sections],
				bool(template.get("site_wide")),
			)
		sections, site_wide = template_cache[template_name]

		feature_serial = feature["serial_no"]
		# Once per Site Visit (v1.633.0): one untagged copy for the site, not one per feature.
		if site_wide and per_site:
			if template_name in site_wide_done:
				continue
			site_wide_done.add(template_name)
			feature_serial = None
		overrides = _reading_overrides(feature_serial)
		# Bake only the feature's on-site store (tech-independent). Since v1.594.0
		# no stock moves on approval, so this records where a chemical is kept
		# rather than where an issue will be drawn from.
		warehouse = feature.get("default_warehouse") if feature_serial else None

		for section in sections:
			if section.disabled:
				continue
			items = sorted(
				(row for row in section.items if scheduled_on(row.get("days"), weekday)),
				key=lambda row: (row.sequence or row.idx),
			)
			common = {
				"section": section.name,
				"section_title": section.section_title,
				"serial_no": feature_serial,
			}
			if section.section_type == "Equipment Inspection":
				for item in items:
					payload["results"].append(
						dict(
							common,
							question=item.label,
							options=item.options,
							is_mandatory=item.is_mandatory,
						)
					)
			elif section.section_type == "Water Chemistry":
				for item in items:
					override = overrides.get((item.label or "").strip().lower())
					payload["readings"].append(
						dict(
							common,
							reading=item.label,
							uom=item.uom,
							min_value=override.min_value if override else item.min_value,
							max_value=override.max_value if override else item.max_value,
							is_mandatory=item.is_mandatory,
						)
					)
			elif section.section_type == "Cleaning Tasks":
				for item in items:
					payload["tasks"].append(
						dict(common, task=item.label, is_mandatory=item.is_mandatory)
					)
			elif section.section_type == "Chemical Dosing":
				for item in items:
					details = item_details(item.item)
					payload["consumables"].append(
						dict(
							common,
							item=item.item,
							item_name=details.item_name,
							# The dose unit the template names (a tablet, a cap full), else the
							# stock unit. A visit records what went into the fountain, not what
							# left stock (v1.594.0), so the two need not match.
							uom=(item.uom or "").strip() or details.stock_uom,
							qty=0,
							default_qty=item.default_qty,
							qty_step=item.qty_step or 1,
							warehouse=warehouse,
						)
					)

	return payload


@frappe.whitelist()
def get_historical_visits(project, exclude=None):
	"""Last 5 submitted maintenance visits for a Project.

	Backs the read-only ``historical_visits`` HTML field on the desk form
	(rendered client-side by ``render_historical_visits`` in the form JS). This
	replaces the former virtual ``historical_visits`` child table — an
	``is_virtual`` doctype whose ``cached_property`` rows didn't shadow Frappe's
	child-table loader and crashed permission checks (``has_user_permission`` ->
	``get_all_children``) on a non-existent table.

	Uses ``get_list`` so the result respects the caller's read permissions and
	User Permissions on the Maintenance Record. Excludes the current record;
	returns ``[]`` when no Project is set.

	Args:
		project (str): Project name (docname).
		exclude (str, optional): Record name to omit (the open document).

	Returns:
		list[dict]: ``[{name, creation, technician}]`` newest-first, max 5.
	"""
	if not project:
		return []
	return frappe.get_list(
		"Sapphire Maintenance Record",
		filters={"project": project, "name": ["!=", exclude or ""], "docstatus": 1},
		fields=["name", "creation", "technician"],
		order_by="creation desc",
		limit=5,
	)


@frappe.whitelist()
def get_dashboard_context(project, serial_no=None, record=None):
	"""Return the on-site briefing data for the technician dashboard widget.

	Whitelisted; called by the desk form JS (``render_dashboard``) to build the
	in-form HTML panel shown before the safety gate is acknowledged.

	Args:
		project (str): Project name (docname).
		serial_no (str, optional): Serial No name. Omitted for Per Site Visit
			records, which have no header serial.
		record (str, optional): the saved visit on the form. With it, ``compared``
			holds each of its readings next to the value at the site's previous
			approved visit (``visit_compare``, v1.595.0), the same values the
			review email and the Visit Review page show, and ``last_visit`` that
			previous visit's readings, problems and notes for the Visit Wizard's
			"Last visit here" card (``visit_compare.last_visit``, v1.596.0).

	Returns:
		dict: {
			"profile": Sapphire Maintenance Profile safety_instructions/access_codes,
			"serial_no": Serial No custom_site_instructions/item_name (or {}),
			"visits": last 3 submitted Sapphire Maintenance Records for the project,
			"contract": Active Maintenance Contract context — gate code, key
				location, preferred days/time from the linked Project Contract,
			"agreement": what that agreement covers (``agreement_scope``, v1.619.0):
				schedule, chosen services, Exhibit A, what is not included; {} without one,
			"service_scope": the Project's Service stream Customer Requests and
				Deliverables (free-text rows from the contract scope model),
		}
	"""
	context = {}

	# 1. Profile Data
	profile = frappe.db.get_value(
		"Sapphire Maintenance Profile",
		{"project": project},
		["safety_instructions", "access_codes", "wrapup_instructions"],
		as_dict=True,
	)
	context['profile'] = profile or {}

	# 2. Serial No Data
	serial_no_data = frappe.db.get_value("Serial No", serial_no, ["custom_site_instructions", "item_name"], as_dict=True) if serial_no else None
	context['serial_no'] = serial_no_data or {}

	# 3. Last 3 Visits
	visits = frappe.get_all(
		"Sapphire Maintenance Record",
		filters={"project": project, "docstatus": 1},
		fields=["name", "creation", "technician"],
		order_by="creation desc",
		limit=3
	)
	context['visits'] = visits

	# 4. Contract-driven context: site access + cadence from the signed
	# Maintenance Services Agreement, via the Active operational contract.
	context['contract'] = {}
	context['agreement'] = {}
	contract = frappe.db.get_value(
		"Sapphire Maintenance Contract",
		{"project": project, "status": "Active"},
		["name", "project_contract", "visit_shape"],
		as_dict=True,
	)
	if contract:
		context['contract'] = {"name": contract.name, "visit_shape": contract.visit_shape}
		if contract.project_contract:
			legal = frappe.db.get_value(
				"Project Contract",
				contract.project_contract,
				["gate_code", "key_location", "preferred_days", "preferred_time"],
				as_dict=True,
			)
			if legal:
				context['contract'].update(legal)
			# What the agreement covers, for the Visit Wizard's card (v1.619.0). Never raises.
			from erpnext_enhancements.sapphire_maintenance.agreement_scope import agreement_scope

			context['agreement'] = agreement_scope(contract.project_contract)

	# 5. Service-stream scope from the Project (customer's words + our deliverables).
	context['service_scope'] = {
		"requests": frappe.get_all(
			"Service Customer Requests",
			filters={"parent": project, "parenttype": "Project"},
			pluck="service_customer_requests",
			order_by="idx",
		),
		"deliverables": frappe.get_all(
			"Service Deliverables",
			filters={"parent": project, "parenttype": "Project"},
			pluck="service_deliverables",
			order_by="idx",
		),
	} if project else {"requests": [], "deliverables": []}

	# 6. Chemistry trends — last 5 visits' readings, for the dashboard sparklines.
	context['trends'] = _chemistry_trends(project, serial_no)

	# 7. The site's previous approved visit, for a saved visit the user may read: this visit's
	# readings beside it (A3, the desk form) and what it found (C1, the Visit Wizard).
	doc = _readable_record(record)
	context['compared'] = _compared_with_last_visit(doc)
	context['last_visit'] = _last_visit_here(doc)

	return context


def _readable_record(record):
	"""The saved visit ``record`` when the user may read it, else None."""
	if not record or not frappe.db.exists("Sapphire Maintenance Record", record):
		return None
	doc = frappe.get_doc("Sapphire Maintenance Record", record)
	return doc if doc.has_permission("read") else None


def _last_visit_here(doc):
	"""``visit_compare.last_visit(doc)``, or None without a visit. A failure is logged and
	leaves the rest of the dashboard (and the wizard's Safety step) standing."""
	if doc is None:
		return None
	try:
		from erpnext_enhancements.sapphire_maintenance import visit_compare

		return visit_compare.last_visit(doc)
	except Exception:
		frappe.log_error(title="Last visit here not shown", message=frappe.get_traceback())
		return None


def _compared_with_last_visit(doc):
	"""``{first_visit, lines, features}`` for a saved visit the user may read, else None.

	A failure is logged and leaves the rest of the dashboard standing.
	"""
	if doc is None:
		return None
	try:
		from erpnext_enhancements.sapphire_maintenance import visit_compare

		result = visit_compare.compare(doc)
		lines = visit_compare.reading_comparisons(doc, result)
		serials = sorted({line["serial_no"] for line in lines if line["serial_no"]})
		features = {}
		if serials:
			features = {
				row.name: row.item_name
				for row in frappe.get_all(
					"Serial No", filters={"name": ["in", serials]}, fields=["name", "item_name"]
				)
				if row.item_name
			}
		return {"first_visit": result["first_visit"], "lines": lines, "features": features}
	except Exception:
		frappe.log_error(title="Compared with last visit not shown", message=frappe.get_traceback())
		return None


def _chemistry_trends(project, serial_no=None):
	"""Reading history for the dashboard sparklines.

	Last 5 submitted visits for the Project (filtered to the water feature when
	``serial_no`` is given — matching either the record header or the tagged
	row). Returns [{reading, uom, min_value, max_value, points: [{date, value,
	out_of_range}]}] with points oldest-first, readings in the current form's
	order of appearance.
	"""
	if not project:
		return []

	filters = {"project": project, "docstatus": 1}
	if serial_no:
		filters["serial_no"] = ["in", [serial_no, ""]]
	records = frappe.get_all(
		"Sapphire Maintenance Record",
		filters=filters,
		fields=["name", "creation"],
		order_by="creation desc",
		limit=5,
	)
	if not records:
		return []

	created = {r.name: r.creation for r in records}
	row_filters = {"parent": ["in", list(created)], "parenttype": "Sapphire Maintenance Record"}
	if serial_no:
		row_filters["serial_no"] = ["in", [serial_no, ""]]
	rows = frappe.get_all(
		"Sapphire Chemistry Reading",
		filters=row_filters,
		fields=["parent", "reading", "reading_value", "uom", "min_value", "max_value", "out_of_range"],
	)

	trends = {}
	for row in sorted(rows, key=lambda r: str(created[r.parent])):
		if not flt(row.reading_value):
			continue  # blank/zero = not measured (see evaluate_reading_ranges)
		trend = trends.setdefault(row.reading, {
			"reading": row.reading,
			"uom": row.uom,
			"min_value": row.min_value,
			"max_value": row.max_value,
			"points": [],
		})
		trend["points"].append({
			"date": str(created[row.parent].date()),
			"value": row.reading_value,
			"out_of_range": row.out_of_range,
		})
	return list(trends.values())
