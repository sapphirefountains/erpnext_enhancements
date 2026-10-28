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

**Two-factor coverage (v1.755.0, daily).** Staff sign in with Google, and every
other route in is closed or behind 2FA. But 2FA in frappe is per role: an account
holding no role with "Two Factor Authentication" ticked is asked for no second
factor anywhere, and a new staff account starts that way. :func:`check_two_factor_coverage`
emails the same recipients every day while an enabled System User is in that
state, while 2FA is off site-wide, or while its restricted-IP bypass is on. It
asks frappe's own ``two_factor_is_enabled_for_`` per user instead of copying its
rule, so the answer follows the installed framework. Quiet when there is nothing
to report.
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


def _should_send_now(failed):
	"""Throttle gate. Successes always pass; failures pass once per window."""
	if not failed:
		return True
	key = "admin_login_failure_alert_sent"
	if frappe.cache.get_value(key):
		return False
	frappe.cache.set_value(key, 1, expires_in_sec=FAILURE_THROTTLE_SECONDS)
	return True


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
	"""``(site_wide, users)``: what lets a staff account sign in with no second factor.

	``site_wide`` holds a sentence per System Settings switch that turns 2FA off; ``users`` the
	enabled System Users frappe would not ask for one. The per-user answer is frappe's own
	``two_factor_is_enabled_for_`` (any role with ``two_factor_auth``), not a copy of its rule.
	"""
	from frappe.twofactor import two_factor_is_enabled_for_

	site_wide = []
	if not cint(frappe.get_system_settings("enable_two_factor_auth")):
		site_wide.append("Two-factor authentication is off for the whole site.")
	if cint(frappe.get_system_settings("bypass_2fa_for_retricted_ip_users")):
		site_wide.append(
			"Two-factor authentication is skipped for users signing in from their restricted IP addresses."
		)
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
		subject = "[Sapphire Security] Two-factor authentication is not enforced"

	body = [email_style.rich("Staff sign-in is meant to need a second factor. Today it does not, for:")]
	if site_wide:
		body.append(email_style.bullets([f"{line} (System Settings)" for line in site_wide]))
	if users:
		body.append(email_style.kv([("Account", user) for user in users]))
	body.append(email_style.kv([("Site", site_url or "unknown")]))
	steps = []
	if site_wide:
		steps.append("Set the System Settings above back so that two-factor authentication applies.")
	if users:
		steps.append(
			"Give each account above a role that has Two Factor Authentication ticked, or disable the "
			"account if nobody uses it."
		)
	steps.append("This email repeats every day until nothing is left to report.")
	body.append(email_style.callout(" ".join(steps), "warning"))
	return subject, email_style.wrap("".join(body), title=subject, eyebrow="Security")


def check_two_factor_coverage():
	"""Daily: email while 2FA is off site-wide or an enabled System User holds no 2FA role.

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
