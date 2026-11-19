"""Email alerts when the built-in ``Administrator`` account is used.

Added after the 2026-08-02 intrusion, in which an automated scanner logged in
as ``Administrator`` with a working password and planted a cryptominer dropper
and two sandbox-escape scripts. Nothing alerted; the break was found two days
later only because the failed payload was crashing every User save.

``Administrator`` was written up here as the one account ERPNext's own two-factor
authentication can never protect, because ``frappe.twofactor.two_factor_is_enabled_for_``
returned ``False`` for it unconditionally. frappe 16.53.0 no longer special-cases it
and judges it by its roles like anyone else, but it is still the shared, all-roles
account, so it is still the one account most worth watching. Once
it is reduced to break-glass use, a login is either a planned emergency or an
intrusion, and both are worth an email.

Hooked to ``Activity Log`` ``after_insert`` rather than ``on_session_creation``
because Frappe writes an authentication log row for **failed** attempts too, so
one code path covers both. The failures matter: the August break was preceded by
roughly thirty failed probes, hours ahead of the payload.

**Known gap — API-key auth raises no event.** Token-authenticated requests
(``Authorization: token <key>:<secret>``) create no Activity Log row and no
session, so they cannot alert from here. Catching those needs a ``before_request``
check on the hot path; deliberately out of scope. Rotating or clearing
``Administrator``'s API key closes the gap more cheaply than watching for it.

Recipients come from ``admin_alert_recipients`` in **site_config.json**, not from
a DocType, and that is the point: an attacker holding ``Administrator`` can
disable a Notification record or a Server Script from the Desk in seconds, but
cannot edit site_config without shell access. Set it to an address hosted
somewhere other than this system, so an alert about the mail server never has to
travel through the mail server. Falls back to enabled System Managers when unset.

Delivery is enqueued after commit and every failure is swallowed and logged — an
alert that cannot be sent must never be able to block a login.

**Two-factor coverage (v1.755.0, daily).** frappe asks for a second factor in
three places only: the password form (``LoginManager.login``, switched off here by
``disable_user_pass_login``), LDAP sign-in (which ignores that switch), and a
password reset by emailed key. Google, impersonation, an email link and a User
Invitation never ask, so this check is no backstop for those:
``portal_login.refuse_staff_email_link_login`` refuses staff on the last two
outright and must keep doing so. What it watches is the 2FA that exists. It is
per role, so an account holding no role with "Two Factor Authentication" ticked
is asked for nothing, and a new staff account starts that way.
:func:`check_two_factor_coverage` emails the same recipients every day while an
enabled System User is in that state, while 2FA is off site-wide or skipped by
its restricted-IP bypass, or while password or LDAP sign-in is on (staff are
Google-only, and those are the routes where 2FA is all that stands behind a
password). It asks frappe's own ``two_factor_is_enabled_for_`` per user instead
of copying its rule, so the answer follows the installed framework. Quiet when
there is nothing to report.

**Error Log masking (v1.826.6).** :func:`mask_error_log` runs on every Error Log
``before_insert`` and passes the row's ``error``, ``method`` and ``metadata`` through
``utils.log_masking.mask_secrets``, which replaces credential-shaped values with a
fixed marker and keeps the rest of the text. frappe masks a traceback variable by its
name; this masks by the shape of the value, including one printed inside another
object's repr. It is a ``doc_events`` hook, resolved by name when the row is inserted,
so it applies in background workers too, and to rows frappe defers and inserts later.
It never raises and never logs: a failure leaves the row as frappe built it, and a
masking step that wrote an Error Log of its own would feed itself.
:func:`mask_stored_error_logs` applies the same masking to rows already stored; an
administrator runs it by hand, dry run first.

**Site configuration (v1.826.6, daily).** :func:`check_site_configuration` emails the
same recipients while ``developer_mode``, ``server_script_enabled`` or ``allow_tests``
is on in the site configuration, at most once per setting per day. Password sign-in
being on is reported by :func:`check_two_factor_coverage` and is not repeated here.
"""

import frappe

from frappe.utils import cint, get_url

from erpnext_enhancements import email_style

#: Minimum gap between *failure* emails. Successes are never throttled — there
#: should be almost none, and each one matters. Failures arrive in bursts (a
#: scanner sent ~30 in 24 seconds on 2026-08-02), and one email per burst is the
#: useful signal; thirty is a self-inflicted mail flood.
FAILURE_THROTTLE_SECONDS = 900

WATCHED_USER = "Administrator"


def _in_maintenance_context():
	"""True while migrating/installing/patching/importing — never alert from those."""
	flags = frappe.flags
	return bool(flags.in_migrate or flags.in_install or flags.in_patch or flags.in_import)


def is_watched_login(operation, user):
	"""True for an authentication event on the watched account.

	Pure, so the decision is unit-testable without a bench. Frappe writes
	``operation="Login"`` for both successes and failures; logouts and the many
	other Activity Log rows are not interesting here.
	"""
	return operation == "Login" and user == WATCHED_USER


def is_failure(status):
	"""True when the authentication log row records a failed attempt."""
	return (status or "").strip().lower() == "failed"


def format_alert(status, ip_address, user_agent, subject_line, when, site_url):
	"""Build ``(subject, html_body)`` for one alert. Pure — no frappe calls.

	Kept free of markup helpers so the body renders the same in a plain-text
	client; whoever reads this is likely reading it on a phone at an awkward hour.
	"""
	failed = is_failure(status)
	verb = "FAILED login attempt" if failed else "successful login"
	subject = f"[Sapphire Security] Administrator {verb}"

	rows = [
		("Result", "Failed" if failed else "Success"),
		("Account", WATCHED_USER),
		("Time", when or "unknown"),
		("Source IP", ip_address or "unknown"),
		("User agent", user_agent or "unknown"),
		("Site", site_url or "unknown"),
	]
	if subject_line:
		rows.append(("Logged as", subject_line))

	body = [
		email_style.rich("The <b>Administrator</b> account recorded a <b>%s</b>." % verb),
		email_style.kv([(label, str(value)) for label, value in rows]),
	]
	if not failed:
		body.append(
			email_style.callout(
				"If this was not you or another administrator acting deliberately, treat it as an "
				"intrusion: rotate the Administrator password immediately and review Activity Log "
				"and Server Script for anything created since this login.",
				"danger",
			)
		)
	return subject, email_style.wrap(
		"".join(body),
		title=subject,
		eyebrow="Security",
		# The tone is stated in the callout as well as carried by colour: Gmail's
		# forced dark mode is free to recolour it, and this is the one alert where
		# the reader must not miss the severity.
	)


def _recipients():
	"""site_config ``admin_alert_recipients`` first, enabled System Managers as fallback.

	Accepts a list or a comma-separated string so the site_config entry can be
	written either way without surprising whoever edits it next.
	"""
	configured = frappe.conf.get("admin_alert_recipients")
	if isinstance(configured, str):
		configured = [part.strip() for part in configured.split(",")]
	if configured:
		return [address for address in configured if address]

	managers = frappe.get_all(
		"Has Role",
		filters={"role": "System Manager", "parenttype": "User"},
		pluck="parent",
	)
	if not managers:
		return []
	return frappe.get_all(
		"User",
		filters={"name": ("in", managers), "enabled": 1, "user_type": "System User"},
		pluck="email",
	)


def _claim_once(key, seconds):
	"""True the first time ``key`` is claimed in a ``seconds`` window, False after that."""
	if frappe.cache.get_value(key):
		return False
	frappe.cache.set_value(key, 1, expires_in_sec=seconds)
	return True


def _should_send_now(failed):
	"""Throttle gate. Successes always pass; failures pass once per window."""
	if not failed:
		return True
	return _claim_once("admin_login_failure_alert_sent", FAILURE_THROTTLE_SECONDS)


def send_admin_login_alert(subject, message, recipients):
	"""Background sender. Never raises — a dead mail server must not surface here."""
	try:
		frappe.sendmail(recipients=recipients, subject=subject, message=message)
	except Exception:
		frappe.log_error(title="Administrator login alert failed to send")


def notify_administrator_login(doc, method=None):
	"""``Activity Log`` ``after_insert`` — email when Administrator authenticates.

	Wrapped whole in try/except on purpose. This runs inside the login request:
	an exception here would turn a monitoring feature into an outage, and the one
	account it watches is the one used to fix outages.
	"""
	try:
		if _in_maintenance_context():
			return
		if not is_watched_login(getattr(doc, "operation", None), getattr(doc, "user", None)):
			return

		failed = is_failure(getattr(doc, "status", None))
		if not _should_send_now(failed):
			return

		recipients = _recipients()
		if not recipients:
			frappe.log_error(
				title="Administrator login alert has no recipients",
				message="Set `admin_alert_recipients` in site_config.json, or grant System Manager to a user with an email address.",
			)
			return

		user_agent = ""
		if getattr(frappe.local, "request", None):
			user_agent = frappe.get_request_header("User-Agent") or ""

		subject, message = format_alert(
			status=getattr(doc, "status", None),
			ip_address=getattr(doc, "ip_address", None),
			user_agent=user_agent,
			subject_line=getattr(doc, "subject", None),
			when=str(getattr(doc, "creation", "") or ""),
			site_url=get_url(),
		)
		frappe.enqueue(
			send_admin_login_alert,
			queue="short",
			enqueue_after_commit=True,
			subject=subject,
			message=message,
			recipients=recipients,
		)
	except Exception:
		frappe.log_error(title="Administrator login alert failed")


# ---------------------------------------------------------------- two-factor coverage


def two_factor_gaps():
	"""``(site_wide, users)``: what leaves staff sign-in short of Google-only with 2FA behind it.

	``site_wide`` holds a sentence, naming its settings page, per switch that turns 2FA off or opens
	a password route (password form, LDAP); ``users`` the enabled System Users frappe would not ask
	for a second factor. The per-user answer is frappe's own ``two_factor_is_enabled_for_`` (any role
	with ``two_factor_auth``), not a copy of its rule.
	"""
	from frappe.twofactor import two_factor_is_enabled_for_

	site_wide = []
	if not cint(frappe.get_system_settings("enable_two_factor_auth")):
		site_wide.append("Two-factor authentication is off for the whole site (System Settings).")
	if cint(frappe.get_system_settings("bypass_2fa_for_retricted_ip_users")):
		site_wide.append(
			"Two-factor authentication is skipped for users signing in from their restricted IP "
			"addresses (System Settings)."
		)
	if not cint(frappe.get_system_settings("disable_user_pass_login")):
		site_wide.append("Sign-in with a username and password is on (System Settings).")
	if cint(frappe.db.get_single_value("LDAP Settings", "enabled")):
		site_wide.append("LDAP sign-in is on, and it ignores the password switch (LDAP Settings).")
	users = frappe.get_all(
		"User", filters={"enabled": 1, "user_type": "System User"}, pluck="name", order_by="name asc"
	)
	return site_wide, [user for user in users if not two_factor_is_enabled_for_(user)]


def format_two_factor_alert(site_wide, users, site_url):
	"""Build ``(subject, html_body)`` for the daily coverage alert. Pure — no frappe calls."""
	count = len(users)
	if count:
		noun = "account" if count == 1 else "accounts"
		subject = f"[Sapphire Security] {count} staff {noun} can sign in without two-factor"
	else:
		subject = "[Sapphire Security] Staff sign-in settings need attention"

	body = [
		email_style.rich(
			"Staff are meant to sign in with Google only, with two-factor authentication behind any "
			"other way in that takes a password. Today:"
		)
	]
	if site_wide:
		body.append(email_style.bullets(site_wide))
	if users:
		body.append(email_style.rich("These staff accounts hold no role that asks for two-factor:"))
		body.append(email_style.kv([("Account", user) for user in users]))
	body.append(email_style.kv([("Site", site_url or "unknown")]))
	steps = []
	if site_wide:
		steps.append("Put the settings above back.")
	if users:
		steps.append(
			"Give each account above a role that has Two Factor Authentication ticked, or disable the "
			"account if nobody uses it."
		)
	steps.append("This email repeats every day until nothing is left to report.")
	body.append(email_style.callout(" ".join(steps), "warning"))
	return subject, email_style.wrap("".join(body), title=subject, eyebrow="Security")


def check_two_factor_coverage():
	"""Daily: email while :func:`two_factor_gaps` finds anything (see the module docstring).

	Quiet when nothing is wrong. Never raises: a scheduler job that raises is retried by nobody, and
	logging is the useful outcome either way.
	"""
	try:
		site_wide, users = two_factor_gaps()
		if not site_wide and not users:
			return
		recipients = _recipients()
		if not recipients:
			frappe.log_error(
				title="Two-factor coverage alert has no recipients",
				message="Set `admin_alert_recipients` in site_config.json, or grant System Manager to a user with an email address.",
			)
			return
		subject, message = format_two_factor_alert(site_wide, users, get_url())
		frappe.sendmail(recipients=recipients, subject=subject, message=message)
	except Exception:
		frappe.log_error(title="Two-factor coverage check failed")


# ---------------------------------------------------------------- Error Log masking

#: Error Log fields that carry free text worth masking. ``metadata`` is the request's
#: form values or the job's arguments as JSON text, and is masked by structure
#: (``log_masking.mask_json_text``) so it stays valid JSON.
MASKED_ERROR_LOG_FIELDS = ("error", "method", "metadata")

#: The fields of :data:`MASKED_ERROR_LOG_FIELDS` that hold a JSON document.
JSON_ERROR_LOG_FIELDS = ("metadata",)

#: Rows read per query by :func:`mask_stored_error_logs`. One row can be hundreds of
#: kilobytes, so batches stay small.
MASK_BATCH_SIZE = 50

#: Most rows one call of :func:`mask_stored_error_logs` will examine.
MASK_MAX_LIMIT = 5000


def _masked_fields(values):
	"""``{field: masked text}`` for each field of ``values`` that masking changes. Pure."""
	from erpnext_enhancements.utils.log_masking import mask_json_text, mask_secrets

	changed = {}
	for field in MASKED_ERROR_LOG_FIELDS:
		value = values.get(field)
		if isinstance(value, str) and value:
			masked = mask_json_text(value) if field in JSON_ERROR_LOG_FIELDS else mask_secrets(value)
			if masked != value:
				changed[field] = masked
	return changed


def _stored_masked_fields():
	"""The masked fields this site's Error Log table has, in order (``metadata`` is
	newer than the other two, so the backfill asks before selecting it)."""
	meta = frappe.get_meta("Error Log")
	return [field for field in MASKED_ERROR_LOG_FIELDS if meta.has_field(field)]


def mask_error_log(doc, method=None):
	"""``Error Log`` ``before_insert``: mask credential-shaped values in ``error``,
	``method`` and ``metadata``.

	Never raises and never logs (see the module docstring): any failure leaves the row as it
	was built, because losing the row would be worse than storing it unmasked.
	"""
	try:
		values = {field: getattr(doc, field, None) for field in MASKED_ERROR_LOG_FIELDS}
		for field, masked in _masked_fields(values).items():
			setattr(doc, field, masked)
	except Exception:
		pass


@frappe.whitelist(methods=["POST"])
def mask_stored_error_logs(dry_run=1, limit=500, after=None):
	"""Apply :func:`mask_error_log`'s masking to Error Log rows already stored.

	System Manager only. Examines up to ``limit`` rows (at most :data:`MASK_MAX_LIMIT`) in name
	order, starting after the name ``after``, and returns counts and the cursor for the next call;
	never row content. A dry run (the default) only counts. Rows are rewritten in place without
	touching ``modified``, and each batch is committed. Idempotent: a masked row is left alone.
	"""
	frappe.only_for("System Manager")

	dry_run = bool(cint(dry_run))
	limit = min(max(cint(limit) or 500, 1), MASK_MAX_LIMIT)
	cursor = after or ""
	scanned = changed = 0
	exhausted = False
	# Column names come from MASKED_ERROR_LOG_FIELDS (a constant), never from the request.
	columns = ", ".join(f"`{field}`" for field in _stored_masked_fields())

	while scanned < limit:
		rows = frappe.db.sql(
			f"""select name, {columns} from `tabError Log`
			where name > %s order by name asc limit %s""",
			(cursor, min(MASK_BATCH_SIZE, limit - scanned)),
			as_dict=True,
		)
		if not rows:
			exhausted = True
			break
		for row in rows:
			scanned += 1
			cursor = row["name"]
			updates = _masked_fields(row)
			if not updates:
				continue
			changed += 1
			if not dry_run:
				frappe.db.set_value("Error Log", row["name"], updates, update_modified=False)
		if not dry_run:
			frappe.db.commit()

	return {
		"dry_run": dry_run,
		"scanned": scanned,
		"changed": changed,
		"next_after": None if exhausted else (cursor or None),
		"done": exhausted,
	}


# ---------------------------------------------------------------- site configuration

#: A configuration alert repeats at most this often per setting: under a day, so the next
#: daily run reports a setting that is still on.
SITE_CONFIGURATION_THROTTLE_SECONDS = 20 * 3600

#: ``(site_config key, sentence)`` for each setting this check reports while it is on.
SITE_CONFIGURATION_CHECKS = (
	(
		"developer_mode",
		"Developer mode is on (developer_mode in the site configuration). It is meant for "
		"development sites only.",
	),
	(
		"server_script_enabled",
		"Server Scripts are enabled (server_script_enabled in the site configuration).",
	),
	(
		"allow_tests",
		"Running tests is allowed (allow_tests in the site configuration). It is meant for "
		"development sites only.",
	),
)


def site_configuration_findings(conf):
	"""``[(key, sentence)]`` for each :data:`SITE_CONFIGURATION_CHECKS` setting on in ``conf``. Pure."""
	return [(key, sentence) for key, sentence in SITE_CONFIGURATION_CHECKS if cint(conf.get(key))]


def format_site_configuration_alert(sentences, site_url):
	"""Build ``(subject, html_body)`` for the daily configuration alert. Pure — no frappe calls."""
	count = len(sentences)
	if count == 1:
		subject = "[Sapphire Security] 1 site configuration setting needs attention"
	else:
		subject = f"[Sapphire Security] {count} site configuration settings need attention"
	body = [
		email_style.rich("The site is running with:"),
		email_style.bullets(sentences),
		email_style.kv([("Site", site_url or "unknown")]),
		email_style.callout(
			"Turn each setting above off in the site configuration unless it is on deliberately. "
			"This email repeats every day while a setting stays on.",
			"warning",
		),
	]
	return subject, email_style.wrap("".join(body), title=subject, eyebrow="Security")


def check_site_configuration():
	"""Daily: email while a :data:`SITE_CONFIGURATION_CHECKS` setting is on.

	At most one email per setting per day (:func:`_claim_once`); quiet when nothing is on. Never
	raises, for the same reason as :func:`check_two_factor_coverage`.
	"""
	try:
		findings = site_configuration_findings(frappe.conf)
		if not findings:
			return
		recipients = _recipients()
		if not recipients:
			frappe.log_error(
				title="Site configuration alert has no recipients",
				message="Set `admin_alert_recipients` in site_config.json, or grant System Manager to a user with an email address.",
			)
			return
		due = [
			sentence
			for key, sentence in findings
			if _claim_once(f"site_configuration_alert_sent:{key}", SITE_CONFIGURATION_THROTTLE_SECONDS)
		]
		if not due:
			return
		subject, message = format_site_configuration_alert(due, get_url())
		frappe.sendmail(recipients=recipients, subject=subject, message=message)
	except Exception:
		frappe.log_error(title="Site configuration check failed")
