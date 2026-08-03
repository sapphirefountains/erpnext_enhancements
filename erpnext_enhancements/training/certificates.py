# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Certificates — issue, revoke, verify, recertify.

The certificate is the only thing this module produces that leaves the company.
It goes to an insurer, to a client's safety officer, to whoever asks whether the
crew on their site had been trained. Three properties follow from that, and all
three are load-bearing:

**A certificate stores its own rendered HTML.** ``certificate_html`` is rendered
once, at issue, and served from then on. Editing the print format next year must
never rewrite a document somebody already holds — the same reasoning as
``project_enhancements/esign/lifecycle.py``, which builds its signed PDF from the
stored instrument rather than ``frappe.attach_print`` precisely so the customer
can never be handed something that differs from what they signed. If rendering
fails the certificate is still issued, with a plain fallback body: an artefact
with an ugly layout is recoverable, a completion with no artefact is not.

**Issuance happens on ``Training Completion.on_submit``, never in the learner
runtime.** A completion recorded by hand — the crew that did the course in a
classroom in 2019 — has to be treated identically to one earned through the
player, or the compliance report has two classes of truth in it. The same applies
in reverse on ``on_cancel``: a revoked pass that still reads as compliant is the
whole reason ``on_revoke`` exists.

**Every step of issuance is independently guarded.** A badge award that raises,
an unreachable mail server or a broken print format must not roll back a
completion somebody earned. The completion is the audit record; the certificate,
the badge and the email are consequences of it, and a consequence is never
allowed to undo its cause.

The public verify route is the other sharp edge. ``verify_certificate`` is
guest-reachable and rate-limited, and returns **only** validity, course title,
holder initials, the two dates and the status. A full name, an address or a staff
id would turn a proof-of-training into a disclosure, because the link is shared
with third parties by design.

Recertification (``expire_and_recertify``, daily) expires completions past their
``expires_on`` and raises the next assignment. It re-dates an existing open
assignment rather than inserting a second one — the same collision a
Material-Change retake has, handled the same way as
``api.training_author._raise_retake``, because two open rows for one person and
one course means two reminder emails and an ambiguous answer to "is this person
compliant".
"""

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import add_days, add_months, cint, escape_html, get_url, getdate, nowdate, today

from erpnext_enhancements.training.doctype.training_settings.training_settings import is_enabled

CERTIFICATE = "Training Certificate"
COMPLETION = "Training Completion"
ASSIGNMENT = "Training Assignment"
COURSE = "Training Course"

# The format created by training/setup_print_formats.py. A course may name its
# own in `certificate_print_format`; this is what it falls back to.
DEFAULT_PRINT_FORMAT = "Training Certificate - Sapphire"

# Statuses a certificate can hold, mirroring the doctype's Select. Expired and
# Revoked are both "not valid" but they answer very different questions, so they
# are never collapsed into one.
VALID = "Valid"
EXPIRED = "Expired"
REVOKED = "Revoked"


# ----------------------------------------------------------------- lifecycle


def after_completion(doc, method=None):
	"""``Training Completion`` ``on_submit`` — certificate, badges, pass email.

	Three independent steps. Each one is caught on its own, and the order is
	deliberate: the certificate first, because it is the only one of the three
	that anybody outside the company will ever ask to see.
	"""
	if _in_maintenance_context():
		return

	certificate = None
	try:
		certificate = issue_certificate(doc)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Training certificate issue ({doc.name})")

	badges = []
	try:
		badges = _award_badges(doc) or []
	except Exception:
		# Includes ImportError while gamification is still being built. A missing
		# leaderboard is not a reason to lose a certificate.
		frappe.log_error(frappe.get_traceback(), f"Training badge award ({doc.name})")

	try:
		_send_pass_email(doc, certificate, badges)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Training pass email ({doc.name})")


def on_revoke(doc, method=None):
	"""``Training Completion`` ``on_cancel`` — withdraw the certificate and put
	the course back on the learner's list.

	Both halves matter. A cancelled completion whose certificate still reads
	Active is a document in somebody's hands that says they are trained when the
	company has decided they are not; and a withdrawal that leaves no assignment
	behind quietly removes the obligation instead of restoring it.
	"""
	if _in_maintenance_context():
		return

	try:
		_revoke_certificates_for(doc)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Training certificate revoke ({doc.name})")

	try:
		_reopen_assignment(doc)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Training assignment reopen ({doc.name})")


# ----------------------------------------------------------------- issuance


def issue_certificate(completion):
	"""Issue the certificate for a submitted completion, or return None.

	Returns the existing certificate unchanged when there already is one. That is
	not just an optimisation: an amended completion re-submitting must not mint a
	second code for the same pass, or two documents in circulation both claim to
	be *the* certificate.
	"""
	if not frappe.db.exists("DocType", CERTIFICATE):
		# Mid-deploy on a site that has not migrated the Phase 4 doctypes yet.
		return None

	course = frappe.db.get_value(
		COURSE,
		completion.course,
		["issues_certificate", "certificate_valid_months", "recertify_months", "certificate_print_format"],
		as_dict=True,
	)
	if not course or not cint(course.issues_certificate):
		return None

	existing = frappe.db.get_value(
		CERTIFICATE, {"completion": completion.name, "docstatus": ["<", 2]}, "name"
	)
	if existing:
		return existing

	# certificate_valid_months is the certificate's own life; recertify_months is
	# the retraining cycle. When only the latter is set the two are the same thing,
	# and issuing a certificate that outlives the training it attests to would be
	# worse than issuing none. The controller defaults from certificate_valid_months
	# alone, so this fallback has to be computed here and passed in.
	valid_months = cint(course.certificate_valid_months) or cint(course.recertify_months)
	issued_on = today()
	expires_on = add_months(issued_on, valid_months) if valid_months else None

	# Deliberately thin. Course title, version, holder, employee, customer, score,
	# status and the verification code are all snapshotted by the certificate's own
	# controller from the completion — two modules writing the same field is how
	# they end up disagreeing. This says only what the *issuer* knows: which
	# completion, and how long the company is prepared to stand behind it.
	certificate = frappe.get_doc(
		{
			"doctype": CERTIFICATE,
			"completion": completion.name,
			"course": completion.course,
			"issued_on": issued_on,
			"expires_on": expires_on,
			"print_format": course.certificate_print_format or DEFAULT_PRINT_FORMAT,
		}
	)
	certificate.insert(ignore_permissions=True)

	# Rendered here and never again — see the module docstring. The document must
	# exist before it can be printed, hence insert → render → submit.
	certificate.certificate_html = _render_certificate(
		certificate, course.certificate_print_format
	)
	certificate.submit()

	# Mirror the expiry onto the completion so the recertification sweep has one
	# place to look, whether or not the course issues a certificate at all.
	if expires_on and not completion.expires_on:
		completion.db_set("expires_on", expires_on, update_modified=False)

	# The back-reference. `Training Completion.certificate` is allow_on_submit and
	# nothing else writes it — the issuer is the only party that knows. Column-
	# guarded because doc_events fire on benches that have not migrated the Phase 4
	# schema yet, and a missing column here would cost the certificate.
	if frappe.db.has_column(COMPLETION, "certificate"):
		completion.db_set("certificate", certificate.name, update_modified=False)

	return certificate.name


def _render_certificate(certificate, print_format=None):
	"""The snapshot. Falls back to a plain body rather than leaving it empty."""
	name = print_format or DEFAULT_PRINT_FORMAT
	try:
		if not frappe.db.exists("Print Format", name):
			name = DEFAULT_PRINT_FORMAT if frappe.db.exists("Print Format", DEFAULT_PRINT_FORMAT) else None
		if name:
			html = frappe.get_print(CERTIFICATE, certificate.name, print_format=name)
			if (html or "").strip():
				return html
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Training certificate render ({certificate.name})")
	return _fallback_html(certificate)


def _fallback_html(certificate):
	"""A certificate whose print format was missing or broken at issue.

	Deliberately unstyled and deliberately complete: every fact a verifier needs
	is here, so an operator can fix the format and reissue later while the holder
	still has something valid in the meantime.
	"""
	expires = certificate.get("expires_on")
	return (
		'<div style="font-family:Helvetica,Arial,sans-serif; color:#222;">'
		"<h2>Certificate of Completion</h2>"
		f"<p><b>{escape_html(certificate.get('holder_name') or '')}</b></p>"
		f"<p>{escape_html(certificate.get('course_title') or '')}</p>"
		f"<p>Issued {escape_html(str(certificate.get('issued_on') or ''))}"
		+ (f" &middot; valid until {escape_html(str(expires))}" if expires else "")
		+ "</p>"
		f"<p>Verification code: {escape_html(certificate.get('verification_code') or '')}</p>"
		"</div>"
	)


# ------------------------------------------------------------------- verify


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=60, seconds=3600)
def verify_certificate(code):
	"""Public verification. Initials only — this link is shared with third parties.

	A revoked or expired certificate is reported as such rather than as "not
	found". Hiding a withdrawal behind a not-found lets the holder argue the
	verifier is broken, which is the opposite of what a revocation is for.
	"""
	answer = {
		"valid": False,
		"status": None,
		"course_title": None,
		"holder_initials": None,
		"issued_on": None,
		"expires_on": None,
	}

	normalized = _normalize_code(code)
	if not normalized or not frappe.db.exists("DocType", CERTIFICATE):
		return answer

	row = frappe.db.get_value(
		CERTIFICATE,
		{"verification_code": normalized},
		["docstatus", "status", "course_title", "holder_name", "issued_on", "expires_on"],
		as_dict=True,
	)
	if not row:
		return answer

	status = _effective_status(row)
	answer["status"] = status
	answer["valid"] = status == VALID and cint(row.docstatus) == 1
	answer["course_title"] = row.course_title
	answer["holder_initials"] = _initials(row.holder_name)
	# ISO strings, not date objects. Frappe's own encoder copes with dates, so this
	# was never broken over HTTP -- but this is a public endpoint that third parties
	# and our own scripts consume, and one that survives a plain ``json.dumps`` is a
	# meaningfully better contract than one that only works through the response
	# layer. `_effective_status` above still compares the real dates.
	answer["issued_on"] = str(row.issued_on) if row.issued_on else None
	answer["expires_on"] = str(row.expires_on) if row.expires_on else None
	return answer


def _effective_status(row):
	"""Status as of today, without waiting for the nightly sweep.

	The sweep is what writes it down, but a verifier asking at 09:00 about a
	certificate that lapsed at midnight must not be told it is still valid.
	"""
	if cint(row.get("docstatus")) == 2:
		return REVOKED
	status = row.get("status") or VALID
	if status == VALID and row.get("expires_on") and getdate(row["expires_on"]) < getdate(nowdate()):
		return EXPIRED
	return status


def _initials(full_name):
	"""``Nikolas Bradshaw`` → ``N.B.`` — enough to confirm the person in front of
	you is the person on the certificate, not enough to identify a stranger."""
	parts = [p for p in (full_name or "").replace(".", " ").split() if p]
	return "".join(f"{p[0].upper()}." for p in parts[:3])


# --------------------------------------------------- expiry and recertification


def expire_and_recertify():
	"""Daily. Expire what has lapsed and raise the next assignment.

	Certificates and completions are expired separately because they can carry
	different dates: a course may recertify yearly while its certificate is
	printed as valid for three. The *completion* is what drives reassignment.
	"""
	if _in_maintenance_context() or not is_enabled():
		return

	expired_certificates = _expire_certificates()
	reassigned = _expire_completions_and_reassign()

	frappe.db.commit()
	if expired_certificates or reassigned:
		frappe.logger().info(
			f"Training recertification: {expired_certificates} certificates expired, "
			f"{reassigned} assignments raised"
		)


def _expire_certificates():
	if not frappe.db.exists("DocType", CERTIFICATE):
		return 0
	names = frappe.get_all(
		CERTIFICATE,
		filters={"docstatus": 1, "status": VALID, "expires_on": ["<", nowdate()]},
		pluck="name",
	)
	for name in names:
		frappe.db.set_value(CERTIFICATE, name, "status", EXPIRED, update_modified=False)
	return len(names)


def _expire_completions_and_reassign():
	rows = frappe.get_all(
		COMPLETION,
		filters={"docstatus": 1, "status": "Valid", "expires_on": ["<", nowdate()]},
		fields=["name", "course", "user"],
	)
	raised = 0
	for row in rows:
		frappe.db.set_value(COMPLETION, row["name"], "status", EXPIRED, update_modified=False)
		if _raise_recertification(row["course"], row["user"]):
			raised += 1
	return raised


def _raise_recertification(course, user):
	"""Put the course back on somebody's list, once.

	Mirrors ``api.training_author._raise_retake``: an already-open assignment is
	**re-dated**, never duplicated. Training Assignment.validate refuses a second
	open row for the same pair, so inserting one would throw here and abort the
	whole nightly sweep for everybody after it.
	"""
	from erpnext_enhancements.training.doctype.training_assignment.training_assignment import (
		OPEN_STATUSES,
	)

	details = frappe.db.get_value(COURSE, course, ["status", "due_days"], as_dict=True)
	if not details or details.status == "Retired":
		# Nobody is chased for a course that has been withdrawn.
		return False
	if not cint(frappe.db.get_value("User", user, "enabled")):
		# Somebody who has left. Their completion still expires — the record has to
		# stay honest — but reassigning training to a disabled account only produces
		# an overdue row nobody can ever close.
		return False

	due_days = cint(details.due_days) or cint(
		frappe.db.get_single_value("Training Settings", "default_due_days")
	) or 14

	existing = frappe.db.exists(
		ASSIGNMENT, {"course": course, "user": user, "status": ["in", OPEN_STATUSES]}
	)
	if existing:
		frappe.db.set_value(
			ASSIGNMENT,
			existing,
			{"due_date": add_days(today(), due_days), "assignment_source": "Recertification"},
			update_modified=False,
		)
		return True

	frappe.get_doc(
		{
			"doctype": ASSIGNMENT,
			"course": course,
			"user": user,
			"status": "Not Started",
			"assigned_on": today(),
			"due_date": add_days(today(), due_days),
			"assignment_source": "Recertification",
		}
	).insert(ignore_permissions=True)
	return True


# ------------------------------------------------------------------ revocation


def _revoke_certificates_for(completion):
	"""Cancel the certificate and mark it Revoked.

	``Training Certificate.on_cancel`` already writes the status, so the write
	below is a backstop rather than the mechanism — and it is not redundant. A
	cancel can fail for reasons that have nothing to do with this decision (a link
	the framework refuses to release, say), and a certificate left reading Valid
	after the company decided otherwise is the one outcome that must not happen.
	``training/compliance.py`` reads exactly this status.
	"""
	if not frappe.db.exists("DocType", CERTIFICATE):
		return
	for name in frappe.get_all(
		CERTIFICATE, filters={"completion": completion.name, "docstatus": ["<", 2]}, pluck="name"
	):
		try:
			frappe.get_doc(CERTIFICATE, name).cancel()
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Training certificate cancel ({name})")
		frappe.db.set_value(
			CERTIFICATE,
			name,
			{"status": REVOKED, "expires_on": today()},
			update_modified=False,
		)


def _reopen_assignment(completion):
	"""Put the withdrawn course back on the learner's list.

	Skipped when something is already open for the pair — a withdrawal during a
	recertification cycle would otherwise create the duplicate open row that
	Training Assignment.validate exists to prevent.
	"""
	from erpnext_enhancements.training.doctype.training_assignment.training_assignment import (
		OPEN_STATUSES,
	)

	if frappe.db.exists(
		ASSIGNMENT,
		{"course": completion.course, "user": completion.user, "status": ["in", OPEN_STATUSES]},
	):
		return

	due_days = cint(frappe.db.get_value(COURSE, completion.course, "due_days")) or cint(
		frappe.db.get_single_value("Training Settings", "default_due_days")
	) or 14
	due_date = add_days(today(), due_days)

	# Prefer the assignment this completion closed, when the link is there — Phase 1
	# shipped without it, so a site mid-upgrade falls back to the pair.
	closed = None
	if frappe.db.has_column(ASSIGNMENT, "completion"):
		closed = frappe.db.get_value(ASSIGNMENT, {"completion": completion.name}, "name")
	if not closed:
		closed = frappe.db.get_value(
			ASSIGNMENT,
			{"course": completion.course, "user": completion.user, "status": "Completed"},
			"name",
		)

	if closed:
		frappe.db.set_value(
			ASSIGNMENT,
			closed,
			{"status": "Not Started", "due_date": due_date, "assignment_source": "Recertification"},
			update_modified=False,
		)
		return

	frappe.get_doc(
		{
			"doctype": ASSIGNMENT,
			"course": completion.course,
			"user": completion.user,
			"status": "Not Started",
			"assigned_on": today(),
			"due_date": due_date,
			"assignment_source": "Recertification",
		}
	).insert(ignore_permissions=True)


# ------------------------------------------------------------------- helpers


def _in_maintenance_context():
	"""True while migrating/installing/patching/importing — never act then."""
	flags = frappe.flags
	return bool(flags.in_migrate or flags.in_install or flags.in_patch or flags.in_import)


def _normalize_code(code):
	"""Accept what a human types and rebuild the stored form.

	The code itself is minted by ``Training Certificate.before_naming`` and stored
	upper-case with no separators; this module deliberately does not mint one of
	its own, because two mechanisms writing one field is how the two formats end up
	in the same column and half of them stop verifying. All that is needed here is
	tolerance at the input: somebody reading a code off paper adds spaces and
	dashes and types it in lower case, and none of that should read as not-found.
	"""
	return "".join(c for c in (code or "").upper() if c.isalnum())


def _award_badges(completion):
	"""Gamification is a separate module and a soft dependency on purpose: it is
	off by default, and it must never be able to take a certificate down with it."""
	if not is_enabled("gamification_enabled"):
		return []
	from erpnext_enhancements.training import gamification

	return gamification.award_for_completion(completion) or []


def _send_pass_email(completion, certificate=None, badges=None):
	"""The "you passed" email.

	Goes through ``notifications`` rather than ``frappe.sendmail`` directly so it
	inherits the one address-resolution policy (Employee preferred address before
	login address) and lands in the Notification Log like every other training
	email. Gated on the same Send Notifications switch as the rest.
	"""
	if not is_enabled("notifications_enabled"):
		return

	from erpnext_enhancements.training import notifications

	recipient = notifications._recipient(completion.user)
	if not recipient:
		return

	title = completion.course_title_snapshot or completion.course
	body = [
		f"<p>Hi {escape_html(recipient.full_name)},</p>",
		f"<p>You have completed <b>{escape_html(title)}</b>. Nice work.</p>",
	]
	if certificate:
		code = frappe.db.get_value(CERTIFICATE, certificate, "verification_code") or ""
		expires = frappe.db.get_value(CERTIFICATE, certificate, "expires_on")
		link = get_url(f"/training_certificate?certificate={certificate}")
		body.append(f'<p><a href="{link}">View your certificate</a></p>')
		if code:
			body.append(
				f"<p>Verification code: <b>{escape_html(code)}</b> &mdash; anyone can check it at "
				f'<a href="{get_url("/training_certificate")}">{get_url("/training_certificate")}</a>.</p>'
			)
		if expires:
			body.append(
				f"<p>It is valid until {frappe.utils.formatdate(expires)}. "
				"We will reassign the course before then.</p>"
			)

	if badges:
		earned = ", ".join(escape_html(str(b)) for b in badges)
		body.append(f"<p>You also earned: <b>{earned}</b>.</p>")

	notifications._send(recipient, _("Training completed: {0}").format(title), "".join(body))
