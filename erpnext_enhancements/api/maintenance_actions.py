"""Maintenance Planner Phase 6E: *Add visit here* (TASK-2026-02471).

The Maintenance Planner could move and reassign visits but never create one: every visit came from
a contract (the nightly scheduler, or ``move_projected`` drafting the next one) or from the
technician on site. Nik decided on 2026-10-09 that a scheduler may also add a draft visit for a site,
a day and a technician from the planner, **linked to the site's active contract when there is one, so
it behaves like a scheduled visit**, and a plain visit when there is not.

* :func:`get_visit_defaults` is what the *Add visit* form pre-fills from: the site's default
  technician, crew, length and full-day flag (its Maintenance Profile), the active contract and its
  fountains, the checklist template each would use. Read only.
* :func:`create_visit` makes the draft. **It reuses the scheduler's own builder**
  (``tasks._new_maintenance_record``, which ``tasks._draft_maintenance_record`` now calls too): the
  header, the default technician, ``tasks._apply_default_crew``, the workflow's initial state (the
  insert sets it) and ``tasks._assign_visit_people``. The planner then overrides only what the person
  changed. It is inserted **as the caller** (``check_permission("create")``), not as the system.
* :func:`remove_created_visit` is the Undo of a visit that was just added, and mirrors
  ``planner_actions.remove_created_task``: it deletes only while nothing has been attached since, and
  never with ``force``.
* :func:`sites_query` is the Link field's search: sites that have a Maintenance Profile.

Rules this keeps, because the page relies on them:

* **Overbooking warns and never blocks.** The same conflict check ``move_visit`` uses
  (``maintenance_planner._people_conflicts``, for the technician and every crew member) answers
  ``{"needs_reason": True, "conflicts"}`` and creates nothing; sent again with a ``reason`` it saves,
  and the reason goes on the visit's timeline.
* **A second open visit for the same fountain warns too.** Submitting two open visits for one fountain
  rolls the contract's next visit date forward twice, so when the site's active contract already has an
  open regular visit for the fountain (the lookup ``get_visit_defaults`` reports as ``open_visit``,
  :func:`_open_regular_visit`, not a second query) ``create_visit`` answers ``needs_reason`` like an
  overbooking, with the line folded into the same ``conflicts`` list, and creates nothing; sent again with
  a ``reason`` it is created and the reason goes on the visit's timeline. Nik's call (2026-10-09): warn,
  never block.
* **A visit linked to the contract is a scheduled visit.** When it is submitted it rolls the
  contract's next visit date forward (``maintenance_scheduling.update_next_visit_dates``), and while
  it is open the scheduler does not draft that fountain's regular visit again. An unlinked visit
  does neither. The fountain must be one the contract covers to link it.
* **The checklist template is what the visit form re-resolves anyway.** The form fills the checklist
  from the contract, fountain and project when the visit is first opened, so ``template`` here is
  recorded on the visit, not obeyed by it.
* **The planner's own role gate first**, then create permission as the user. Customer jobs only.
* **The customer date confirmation** (Phase 5, off) needs nothing here: a dated draft visit triggers
  it on insert, as it does for the scheduler's.
"""

import frappe
from frappe import _
from frappe.utils import cint, getdate, nowdate

from erpnext_enhancements.api import maintenance_planner as mp
from erpnext_enhancements.project_enhancements import crew_availability as engine

DOCTYPE = "Sapphire Maintenance Record"
PROFILE = "Sapphire Maintenance Profile"
CONTRACT = "Sapphire Maintenance Contract"
#: What the visit is called in a conflict check before it has a name.
NEW_REF = "New visit"
#: The ``conflicts`` key the duplicate-visit line sits under (the page prints each key in bold, then its lines).
DUPLICATE_KEY = "Duplicate visit"
#: The timeline reason when nothing was said (the planner is the origin).
ADDED_NOTE = "Added on the Maintenance Planner"
#: Child tables of the visit form. A row in any of them means someone has started filling it in.
FORM_TABLES = (
	"Sapphire Chemistry Reading",
	"Sapphire Cleaning Task",
	"Sapphire Maintenance Result",
	"Sapphire Maintenance Consumable",
)


# ---------------------------------------------------------------------- pure helpers


def given(value):
	"""None for an argument the caller did not set (``""``, ``"null"`` and ``"undefined"`` as a form-encoded
	request sends a JavaScript null). A deliberate ``0`` or ``False`` is kept."""
	if value is None or (isinstance(value, str) and value.strip() in ("", "null", "undefined")):
		return None
	return value


def default_feature(features):
	"""The fountain to pre-fill: the covered feature due soonest (one with no date last), else None.

	``features`` is ``[{"serial_no", "next_visit_date"}]``. Nothing is picked for a contract that
	covers no fountain, and the page lets the person change it: this is only the starting point.
	"""
	rows = [row for row in features or [] if row.get("serial_no")]
	if not rows:
		return None
	dated = sorted(
		(row for row in rows if row.get("next_visit_date")), key=lambda row: str(row["next_visit_date"])
	)
	return (dated or rows)[0].get("serial_no")


def duplicate_visit_line(subject, record, day):
	"""The warning for a second open visit: ``subject`` is the fountain or site, ``record`` the open visit,
	``day`` its date (None for one not dated yet)."""
	when = f", on {day:%a %b} {day.day}" if day else ""
	return (
		f"{subject} already has an open visit, {record}{when}. "
		"Submitting both moves the contract's next visit date twice."
	)


def links_to_contract(contract_serials, serial_no):
	"""True when a visit for ``serial_no`` belongs to the contract: no fountain named (a site visit),
	or one the contract covers."""
	return not serial_no or serial_no in set(contract_serials or ())


# ---------------------------------------------------------------------- shared checks


def _require_create():
	if not frappe.has_permission(DOCTYPE, "create"):
		frappe.throw(_("You cannot create Maintenance Records."), frappe.PermissionError)


def _site(project):
	"""``(title, customer)`` of a maintenance site, or a refusal: it must exist, be a customer job and
	have a Maintenance Profile."""
	project = given(project)
	if not project or not frappe.db.exists("Project", project):
		frappe.throw(_("Pick the site the visit is for."))
	if project not in engine.planner_projects({project}):
		frappe.throw(
			_("{0} is not a customer job, so it is not on the planner.").format(
				frappe.utils.escape_html(project)
			)
		)
	profile = frappe.db.get_value(PROFILE, {"project": project}, ["name", "customer"], as_dict=True)
	if not profile:
		frappe.throw(
			_("{0} has no Maintenance Profile, so it is not a maintenance site.").format(
				frappe.utils.escape_html(project)
			)
		)
	title, customer = frappe.db.get_value("Project", project, ["project_name", "customer"]) or (None, None)
	return title or project, profile.get("customer") or customer


def _active_contract(project):
	from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract.sapphire_maintenance_contract import (
		get_active_contract,
	)

	return get_active_contract(project)


def _template_for(project, customer, contract, row=None):
	from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record.sapphire_maintenance_record import (
		resolve_template,
	)

	return resolve_template(project=project, customer=customer, contract=contract, feature_row=row)


def _names(users):
	return mp._full_names(users)


# ---------------------------------------------------------------------- the form's defaults


@frappe.whitelist()
def get_visit_defaults(project):
	"""What the *Add visit* form pre-fills for a site. Read only; the planner role gate.

	Returns ``{"project", "title", "customer", "technician", "crew": [{"user", "name", "hours"}],
	"hours", "full_day", "contract", "visit_shape", "features": [{"serial_no", "label",
	"next_visit_date", "template"}], "serial_no", "template", "open_visit"}``. ``technician`` and
	``crew`` are the Maintenance Profile's defaults (what the scheduler would copy), ``hours`` its visit
	hours (None: Settings' maintenance visit hours) and ``full_day`` its flag. ``contract`` is the site's
	active contract or None; ``features`` its covered fountains with the template each would use, and
	``serial_no`` / ``template`` the pre-fill (the fountain due soonest on a Per Feature contract, no
	fountain on a Per Site Visit one). ``open_visit`` is an open regular visit that already stands for
	that fountain, so the page can say so.
	"""
	mp._require_planner()
	from erpnext_enhancements.api.maintenance_dispatch import default_crew_for, default_technician_for

	title, customer = _site(project)
	crew = default_crew_for(project)
	technician = default_technician_for(project)
	names = _names([technician] + [row["user"] for row in crew.get("rows") or []])
	contract = _active_contract(project)
	features = []
	if contract:
		customer = contract.get("customer") or customer
		for row in contract.get("covered_features") or []:
			if not row.get("serial_no"):
				continue
			features.append(
				{
					"serial_no": row.get("serial_no"),
					"label": mp.feature_label(row.get("serial_no")),
					"next_visit_date": str(row.get("next_visit_date"))
					if row.get("next_visit_date")
					else None,
					"template": _template_for(project, customer, contract, row),
				}
			)
	per_site = bool(contract) and contract.get("visit_shape") == "Per Site Visit"
	serial_no = None if per_site else default_feature(features)
	template = None
	if contract:
		row = next((f for f in features if f["serial_no"] == serial_no), None)
		template = row["template"] if row else _template_for(project, customer, contract)
	else:
		template = _template_for(project, customer, None)
	return {
		"project": project,
		"title": mp.short_site_name(title),
		"customer": customer,
		"technician": technician,
		"technician_name": names.get(technician) if technician else None,
		"crew": [
			{"user": row["user"], "name": names.get(row["user"]) or row["user"], "hours": row.get("hours")}
			for row in crew.get("rows") or []
		],
		"hours": crew.get("hours"),
		"full_day": bool(crew.get("full_day")),
		"contract": contract.name if contract else None,
		"visit_shape": contract.get("visit_shape") if contract else None,
		"features": features,
		"serial_no": serial_no,
		"template": template,
		"open_visit": _open_regular_visit(project, contract, serial_no),
	}


def _open_regular_visit(project, contract, serial_no):
	"""An open regular draft (no label) that already stands for this site or fountain, or None: the
	scheduler's own de-dup (``tasks.generate_predictive_maintenance_records``)."""
	if contract and contract.get("visit_shape") == "Per Site Visit":
		filters = {"maintenance_contract": contract.name, "visit_label": ["is", "not set"], "docstatus": 0}
	else:
		filters = {"project": project, "visit_label": ["is", "not set"], "docstatus": 0}
		if serial_no:
			filters["serial_no"] = serial_no
	return frappe.db.get_value(DOCTYPE, filters, "name")


def _duplicate_visit(project, contract, serial_no, title):
	"""``{DUPLICATE_KEY: [line]}`` when ``contract`` already has an open regular visit for this fountain
	(or, on a Per Site Visit contract, for the site), else {}.

	Only a visit linked to the contract can move its next visit date, so a plain visit (no ``contract``)
	has nothing to double. The open visit is found by :func:`_open_regular_visit`, the lookup
	``get_visit_defaults`` reports as ``open_visit``; the date is read back by the name it returned. A
	submitted or cancelled visit is not open (``docstatus`` 0 only), so it never warns.
	"""
	if not contract:
		return {}
	found = _open_regular_visit(project, contract, serial_no)
	if not found:
		return {}
	day = frappe.db.get_value(DOCTYPE, found, "scheduled_visit_date")
	subject = mp.feature_label(serial_no) if serial_no else title
	return {DUPLICATE_KEY: [duplicate_visit_line(subject, found, getdate(day) if day else None)]}


@frappe.whitelist()
def sites_query(doctype=None, txt="", searchfield="name", start=0, page_len=20, filters=None):
	"""The *Site* Link field's search: customer-job projects that have a Maintenance Profile.

	The profile is the site registry (one per project, ``project`` unique), and the scheduler, the
	visits and this planner all key on the Project, so the field stays a Link to Project and only its
	list is narrowed. Returns ``[(name, title)]``. The planner role gate.
	"""
	mp._require_planner()
	type_expr, stream = engine.planner_job_sql("proj")
	like = "%" + str(txt or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
	return frappe.db.sql(
		f"""
		SELECT proj.name, proj.project_name
		FROM `tabProject` proj
		WHERE proj.name IN (SELECT prof.project FROM `tabSapphire Maintenance Profile` prof
			WHERE IFNULL(prof.project, '') <> '')
			AND ({type_expr} IN %(planner_types)s OR {stream})
			AND (proj.name LIKE %(like)s OR IFNULL(proj.project_name, '') LIKE %(like)s)
		ORDER BY proj.project_name ASC, proj.name ASC
		LIMIT %(start)s, %(page_len)s
		""",
		{
			**engine.PLANNER_SQL_VALUES,
			"like": like,
			"start": cint(start),
			"page_len": min(cint(page_len) or 20, 50),
		},
	)


# ---------------------------------------------------------------------- create


def _insert_with_warnings(doc):
	"""``doc.insert()`` as the caller; the validate hooks' messages come back as text rather than popping up."""
	log = frappe.local.message_log if isinstance(getattr(frappe.local, "message_log", None), list) else None
	before = len(log) if log is not None else 0
	doc.insert()
	warnings = []
	if log is not None:
		warnings = [mp._message_text(message) for message in log[before:]]
		del log[before:]
	return [w for w in warnings if w]


def _stand_in(record):
	"""The unsaved visit as the conflict check reads a saved one (``maintenance_planner._people_conflicts``)."""
	return frappe._dict(
		name=NEW_REF,
		technician=record.get("technician"),
		crew=[{"user": row.get("user"), "hours": row.get("hours")} for row in record.get("crew") or []],
		planned_hours=record.get("planned_hours"),
		full_day=record.get("full_day"),
		scheduled_visit_date=record.get("scheduled_visit_date"),
	)


@frappe.whitelist(methods=["POST"])
def create_visit(
	project,
	date,
	technician=None,
	crew=None,
	hours=None,
	serial_no=None,
	template=None,
	reason=None,
	full_day=None,
):
	"""Draft a Sapphire Maintenance Record for ``project`` on ``date``, as the scheduler would.

	* ``technician``: None is the site's default technician; ``""`` leaves the visit with nobody.
	* ``crew``: None is the site's default crew; otherwise a JSON list of users or of ``{"user",
	  "hours"}`` rows that replaces it (as ``move_visit`` takes it). Nobody is picked for the planner.
	* ``hours``: None is the site's visit hours; a number is the length each person is booked for, and
	  0 is Settings' maintenance visit hours. A positive length clears Full day unless ``full_day`` says
	  otherwise; ``full_day`` None is the site's flag.
	* ``serial_no``: the fountain. ``template``: the checklist template (see the module note).
	* The visit is linked to the site's active contract (``maintenance_contract``) when it has one and
	  the fountain, if given, is one it covers; otherwise it is a plain visit and the answer says why in
	  ``warnings``. Always a draft, with no label.
	* Refused: not the planner's role, no create permission, a day that has passed, a site that is not a
	  customer job with a Maintenance Profile, an inactive technician or crew member.

	Overbooking warns and never blocks: a visit that creates a conflict for the technician or anyone on
	the crew answers ``{"needs_reason": True, "conflicts": {person: ["YYYY-MM-DD: Over by 2h"]}}`` and
	creates nothing; sent again with a ``reason`` it is created and the reason goes on its timeline.
	A second open visit for the same fountain under the contract warns the same way, as one more line in
	``conflicts`` (under ``"Duplicate visit"``): submitting both would roll the contract's next visit date
	forward twice. A visit that is not linked to a contract never warns about that.

	Returns what ``move_visit`` returns (``name``, ``date``, ``technician``, ``crew``, ``planned_hours``,
	``full_day``, ``modified``, ``warnings``) plus ``created: True``, ``contract``, ``serial_no`` and, when
	it went over a conflict, ``conflicts``.
	"""
	mp._require_planner()
	_require_create()
	title, customer = _site(project)
	if not given(date):
		frappe.throw(_("Pick the day."))
	day = getdate(date)
	if day < getdate(nowdate()):
		frappe.throw(_("Pick today or a later day."))
	serial_no = given(serial_no)
	if serial_no is not None:
		serial_no = str(serial_no).strip() or None

	warnings = []
	contract = _active_contract(project)
	serials = [row.get("serial_no") for row in (contract.get("covered_features") or [])] if contract else []
	linked = contract if contract and links_to_contract(serials, serial_no) else None
	if contract and not linked:
		warnings.append(
			_("{0} is not covered by the active contract {1}, so this visit is not linked to it.").format(
				serial_no, contract.name
			)
		)
	if linked:
		customer = linked.get("customer") or customer

	from erpnext_enhancements import tasks
	from erpnext_enhancements.sapphire_maintenance.visit_crew import set_crew

	record = tasks._new_maintenance_record(
		project, customer, contract=linked, serial_no=serial_no, scheduled_date=day, exact_date=True
	)
	if technician is not None:
		new = given(technician) or None
		if new:
			mp._require_active(new)
		record.technician = new
	if crew is not None:
		entries = mp._parse_crew(given(crew) or "[]")
		for entry in entries:
			mp._require_active(entry["user"])
		set_crew(record, entries)
	else:
		# The scheduler's defaults kept; only the technician may no longer be crew too.
		set_crew(record, mp._crew_entries(record))
	if hours is not None:
		value = mp._positive(hours) or 0
		record.planned_hours = value
		if value and full_day is None:
			record.full_day = 0
	if full_day is not None:
		record.full_day = 1 if mp._truthy(full_day) else 0
	wanted = given(template)
	if wanted:
		if not frappe.db.exists("Sapphire Maintenance Template", wanted):
			frappe.throw(_("Checklist template {0} was not found.").format(frappe.utils.escape_html(wanted)))
		record.template = wanted
	record.check_permission("create")

	stand_in = _stand_in(record)
	conflicts = _duplicate_visit(project, linked, serial_no, mp.short_site_name(title))
	for person, lines in mp._people_conflicts(stand_in, list(mp._hours_specs(stand_in))).items():
		conflicts.setdefault(person, []).extend(lines)
	reason = (given(reason) or "").strip()
	if conflicts and not reason:
		return {"needs_reason": True, "conflicts": conflicts}

	warnings.extend(_insert_with_warnings(record))
	tasks._assign_visit_people(record)
	record.add_comment("Comment", _(ADDED_NOTE) + ".")
	if conflicts:
		mp._comment_conflict(record, conflicts, reason)

	result = mp._visit_result(record, warnings)
	result.update(
		created=True, contract=record.get("maintenance_contract"), serial_no=record.get("serial_no")
	)
	if conflicts:
		result["conflicts"] = conflicts
	return result


# ---------------------------------------------------------------------- undo


def _removal_problem(doc, modified):
	"""Why the visit just added cannot simply be deleted again, as a phrase, or None.

	Anything that has happened to it since is a reason to stop: it is not the creator's, it was changed
	(a save by anyone moves ``modified``), it is no longer a draft, it was started (a visit date, a
	clock-in or out), its form has rows, a digest was texted for it, an invoice is linked, someone else
	commented, or a file is attached.
	"""
	from erpnext_enhancements.api.project_planner import same_moment

	user = frappe.session.user
	name = doc.name
	if doc.get("owner") and doc.get("owner") != user:
		return _("only the person who added it can undo it from the planner")
	if not same_moment(doc.get("modified"), modified):
		return _("it was changed after it was added")
	if cint(doc.get("docstatus")) != 0 or (doc.get("workflow_state") or "Draft") != "Draft":
		return _("it is no longer a draft")
	if doc.get("visit_date") or doc.get("clock_in_time") or doc.get("clock_out_time"):
		return _("the visit was started")
	if doc.get("sales_invoice"):
		return _("an invoice is linked to it")
	if doc.get("dispatch_digest_sent_on") or any(
		row.get("digest_sent_on") for row in (doc.get("crew") or [])
	):
		return _("a morning digest was sent for it")
	for table in FORM_TABLES:
		if frappe.db.exists(table, {"parent": name, "parenttype": DOCTYPE}):
			return _("its visit form has been filled in")
	if frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": DOCTYPE,
			"reference_name": name,
			"comment_type": "Comment",
			"owner": ["!=", user],
		},
		pluck="name",
		limit_page_length=1,
	):
		return _("someone else has commented on it")
	if frappe.db.exists("File", {"attached_to_doctype": DOCTYPE, "attached_to_name": name}):
		return _("a file is attached to it")
	return None


@frappe.whitelist(methods=["POST"])
def remove_created_visit(record, modified):
	"""Undo a visit :func:`create_visit` just made: delete the draft.

	Through ``frappe.delete_doc`` as the caller (delete permission; never ``force``, so a link to it
	still refuses, and it goes to Deleted Documents), and only while nothing has been attached
	(:func:`_removal_problem`); otherwise it refuses with the reason and changes nothing. Its
	assignments go with it. Returns ``{"removed": name}``.
	"""
	mp._require_planner()
	if not frappe.db.exists(DOCTYPE, record):
		frappe.throw(_("Visit {0} was not found.").format(frappe.utils.escape_html(record)))
	doc = frappe.get_doc(DOCTYPE, record)
	problem = _removal_problem(doc, modified)
	if problem:
		frappe.throw(
			_("{0} was not removed: {1}. Delete it on the visit form if it should go.").format(
				doc.name, problem
			),
			title=_("Not undone"),
		)
	if not frappe.has_permission(DOCTYPE, "delete", doc=doc):
		frappe.throw(
			_("You cannot delete Maintenance Records, so {0} was not removed.").format(doc.name),
			frappe.PermissionError,
		)
	frappe.delete_doc(DOCTYPE, doc.name)
	return {"removed": doc.name}
