"""Integrations Health — one place to see whether every external service this
app depends on is configured, connected, and not failing.

This app talks to a lot of third parties (QuickBooks Online, Google Drive,
Twilio/"Triton", Vertex AI/Gemini, Google Analytics 4 / Search Console) and
each fails *quietly* in its own corner: a QuickBooks token lapses, the Drive
service account was never pasted in, an hourly sync errors into the Error Log.
``get_health`` rolls all of that — plus scheduler liveness and a 24 h error
digest — into one System-Manager-only payload the desk page renders as a
green/amber/red tile per integration.

Design: **cheap and DB-only on load** (no outbound API calls — those are
opt-in via ``run_drive_test``), per-section try/except so one missing Single
doctype can't blank the page, and secrets are only ever read as booleans
("configured?") — never returned. The pure tone helpers at the top carry no
frappe dependency so they unit-test bench-free (tests/test_integrations_health).
"""

import frappe
from frappe.utils import add_days, get_datetime, now_datetime, nowdate, time_diff_in_seconds

# ----------------------------------------------------------------- pure helpers
# (no frappe — unit-tested in tests/test_integrations_health.py)

# Severity ordering: a tile's overall tone is the worst of its metrics.
TONE_RANK = {"neutral": 0, "green": 1, "amber": 2, "red": 3}


def worst_tone(tones):
	"""The most severe tone in ``tones`` (red > amber > green > neutral)."""
	worst = "neutral"
	for tone in tones:
		if TONE_RANK.get(tone, 0) > TONE_RANK.get(worst, 0):
			worst = tone
	return worst


def humanize_age(seconds):
	"""'3 min ago' / '5 h ago' / '2 d ago' from an age in seconds (None=never)."""
	if seconds is None:
		return "never"
	seconds = int(seconds)
	if seconds < 60:
		return "just now"
	minutes = seconds // 60
	if minutes < 60:
		return f"{minutes} min ago"
	hours = minutes // 60
	if hours < 24:
		return f"{hours} h ago"
	return f"{hours // 24} d ago"


def age_tone(seconds, amber_after, red_after):
	"""Tone for "how long since X happened" — older is worse. None=neutral."""
	if seconds is None:
		return "neutral"
	if seconds >= red_after:
		return "red"
	if seconds >= amber_after:
		return "amber"
	return "green"


def countdown_tone(seconds_until, amber_within, red_within=0):
	"""Tone for "how long until X expires" — sooner is worse. ``seconds_until``
	may be negative (already expired → red). None=neutral."""
	if seconds_until is None:
		return "neutral"
	if seconds_until <= red_within:
		return "red"
	if seconds_until <= amber_within:
		return "amber"
	return "green"


# ------------------------------------------------------------- frappe helpers


def _seconds_since(value):
	if not value:
		return None
	try:
		return max(0, int(time_diff_in_seconds(now_datetime(), get_datetime(value))))
	except Exception:
		return None


def _seconds_until(value):
	if not value:
		return None
	try:
		return int(time_diff_in_seconds(get_datetime(value), now_datetime()))
	except Exception:
		return None


def _hours_ago(n):
	from frappe.utils import add_to_date

	return add_to_date(now_datetime(), hours=-n, as_datetime=True)


def _count(doctype, filters):
	try:
		return frappe.db.count(doctype, filters)
	except Exception:
		return 0


def _single(doctype, field):
	try:
		return frappe.db.get_single_value(doctype, field)
	except Exception:
		return None


def _metric(label, value, tone="neutral"):
	return {"label": label, "value": value, "tone": tone}


# --------------------------------------------------------- per-integration checks


def _check_quickbooks(key, label, route):
	settings = frappe.get_cached_doc("QuickBooks Online Settings")
	client_configured = bool(settings.get("client_id"))
	realm = settings.get("realm_id")
	status = settings.get("status") or "Not Connected"
	sync_enabled = bool(settings.get("sync_enabled"))

	links = [
		{"label": "Settings", "route": route},
		{"label": "Sync Log", "route": "/app/quickbooks-sync-log"},
	]

	if not client_configured and not realm:
		return _tile(key, label, "neutral", "Not configured", configured=False, links=links,
			notes=["Add the Intuit OAuth app credentials and connect to enable accounting sync."])

	tones, metrics = [], []

	status_tone = {"Connected": "green", "Syncing": "amber", "Failed": "red",
		"Not Connected": "amber"}.get(status, "neutral")
	tones.append(status_tone)
	metrics.append(_metric("Connection", status, status_tone))
	metrics.append(_metric("Environment", settings.get("environment") or "—"))

	if realm:
		secs = _seconds_until(settings.get("token_expires_at"))
		tok_tone = countdown_tone(secs, amber_within=15 * 60)
		tones.append(tok_tone)
		if secs is None:
			tok_value = "unknown"
		elif secs <= 0:
			tok_value = "expired"
		else:
			tok_value = f"in {humanize_age(secs).replace(' ago', '')}"
		metrics.append(_metric("OAuth token", tok_value, tok_tone))

	if sync_enabled:
		cdc_secs = _seconds_since(settings.get("last_cdc_sync"))
		cdc_tone = age_tone(cdc_secs, amber_after=2 * 3600, red_after=24 * 3600)
		tones.append(cdc_tone)
		metrics.append(_metric("Last CDC poll", humanize_age(cdc_secs), cdc_tone))
	else:
		metrics.append(_metric("Sync", "disabled", "amber"))
		tones.append("amber")

	failed = _count("QuickBooks Sync Log", {"status": "Failed", "creation": (">", add_days(nowdate(), -7))})
	fail_tone = "red" if failed else "green"
	tones.append(fail_tone)
	metrics.append(_metric("Failed syncs (7d)", failed, fail_tone))

	headline = status if sync_enabled else f"{status} · sync off"
	return _tile(key, label, worst_tone(tones), headline, configured=True, metrics=metrics, links=links)


def _check_drive(key, label, route):
	configured = bool(_single("Project Folder Google Drive Settings", "service_account_json")) and bool(
		_single("Project Folder Google Drive Settings", "shared_drive_id")
	)
	attachment_sync = bool(_single("Project Folder Google Drive Settings", "attachment_sync_enabled"))
	links = [
		{"label": "Settings", "route": route},
		{"label": "Drive Sync Log", "route": "/app/drive-sync-log"},
	]

	if not configured:
		tone = "amber" if attachment_sync else "neutral"
		return _tile(key, label, tone, "Not configured", configured=False, links=links, actions=["drive_test"],
			notes=[
				"Paste the service-account JSON and Shared Drive ID in settings, then use Test "
				"Connection. Until then all Drive automation (folders, attachment sync, recording "
				"export) is dormant."
				+ (" Attachment Sync is ON but the service account is missing." if attachment_sync else "")
			])

	tones, metrics = [], []
	metrics.append(_metric("Service account", "configured", "green"))
	metrics.append(_metric("Attachment sync", "on" if attachment_sync else "off",
		"green" if attachment_sync else "neutral"))

	failed_24h = _count("Drive Sync Log", {"status": "Failed", "creation": (">", _hours_ago(24))})
	stale = _count("Drive Sync Log", {"status": "Stale"})
	fail_tone = "red" if failed_24h else "green"
	tones.append(fail_tone)
	metrics.append(_metric("Failed (24h)", failed_24h, fail_tone))
	if stale:
		metrics.append(_metric("Stale shadows", stale, "amber"))
		tones.append("amber")

	headline = "Connected" if not failed_24h else f"{failed_24h} failed in 24h"
	return _tile(key, label, worst_tone(tones) if tones else "green", headline, configured=True,
		metrics=metrics, links=links, actions=["drive_test"])


def _check_triton(key, label, route):
	gateway = bool(_single("Triton Settings", "gateway_url"))
	twilio = bool(_single("Triton Settings", "twilio_account_sid"))
	number = _single("Triton Settings", "primary_twilio_number")
	softphone_raw = _single("Triton Settings", "softphone_users") or ""
	softphone_count = len([u for u in softphone_raw.replace(",", "\n").split("\n") if u.strip()])
	links = [{"label": "Settings", "route": route}, {"label": "Call Log", "route": "/app/call-log"}]

	if not gateway and not twilio:
		return _tile(key, label, "neutral", "Not configured", configured=False, links=links,
			notes=["Set the Triton gateway URL and Twilio credentials to enable click-to-call and SMS."])

	tones, metrics = [], []
	metrics.append(_metric("Gateway URL", "set" if gateway else "missing", "green" if gateway else "red"))
	tones.append("green" if gateway else "red")
	metrics.append(_metric("Twilio", "set" if twilio else "missing", "green" if twilio else "amber"))
	tones.append("green" if twilio else "amber")
	metrics.append(_metric("Caller ID number", number or "—", "green" if number else "amber"))
	tones.append("green" if number else "amber")
	metrics.append(_metric("Softphone answerers", softphone_count or "everyone"))

	return _tile(key, label, worst_tone(tones), "Configured" if worst_tone(tones) != "red" else "Incomplete",
		configured=True, metrics=metrics, links=links)


def _check_gemini(key, label, route):
	configured = bool(_single("Triton Settings", "maps_api_key"))
	links = [{"label": "Settings", "route": route}]
	if not configured:
		return _tile(key, label, "neutral", "Not configured", configured=False, links=links,
			notes=["Add the Vertex AI / Gemini key in Triton Settings to enable AI email/SMS drafting."])
	return _tile(key, label, "green", "Key configured", configured=True,
		metrics=[_metric("API key", "configured", "green")], links=links)


def _check_ga4(key, label, route):
	prop = bool(_single("GA4 Settings", "ga4_property_id"))
	creds = bool(_single("GA4 Settings", "credentials_json"))
	gsc = bool(_single("GA4 Settings", "gsc_property_url"))
	links = [{"label": "Settings", "route": route}, {"label": "Dashboard", "route": "/app/ga4-dashboard"}]
	if not prop and not creds:
		return _tile(key, label, "neutral", "Not configured", configured=False, links=links,
			notes=["Add the GA4 property ID and service-account credentials to power the marketing dashboard."])
	tones, metrics = [], []
	metrics.append(_metric("GA4 property", "set" if prop else "missing", "green" if prop else "amber"))
	tones.append("green" if prop else "amber")
	metrics.append(_metric("Credentials", "set" if creds else "missing", "green" if creds else "red"))
	tones.append("green" if creds else "red")
	metrics.append(_metric("Search Console", "set" if gsc else "off", "green" if gsc else "neutral"))
	return _tile(key, label, worst_tone(tones), "Configured" if worst_tone(tones) != "red" else "Incomplete",
		configured=True, metrics=metrics, links=links)


# ----------------------------------------------------------- scheduler & errors


def _check_scheduler():
	enabled = bool(_single("System Settings", "enable_scheduler"))
	jobs = frappe.get_all(
		"Scheduled Job Type",
		filters={"method": ("like", "erpnext_enhancements.%")},
		fields=["name", "method", "last_execution", "stopped", "frequency"],
		limit_page_length=0,
	)
	failed_24h = _count("Scheduled Job Log", {
		"status": "Failed",
		"scheduled_job_type": ("like", "erpnext_enhancements.%"),
		"creation": (">", _hours_ago(24)),
	})
	recent_failures = frappe.get_all(
		"Scheduled Job Log",
		filters={"status": "Failed", "scheduled_job_type": ("like", "erpnext_enhancements.%"),
			"creation": (">", _hours_ago(24))},
		fields=["scheduled_job_type", "creation"],
		order_by="creation desc",
		limit_page_length=8,
	)
	tones = ["red" if not enabled else "green", "red" if failed_24h else "green"]
	jobs_out = [
		{
			"name": job.method,
			"last_run": humanize_age(_seconds_since(job.last_execution)),
			"stopped": bool(job.stopped),
			"frequency": job.frequency,
		}
		for job in jobs
	]
	return {
		"status": worst_tone(tones),
		"enabled": enabled,
		"app_job_count": len(jobs),
		"failed_24h": failed_24h,
		"recent_failures": [
			{"job": row.scheduled_job_type, "when": humanize_age(_seconds_since(row.creation))}
			for row in recent_failures
		],
		"jobs": jobs_out,
	}


def _check_errors():
	rows = frappe.get_all(
		"Error Log",
		filters={"creation": (">", _hours_ago(24))},
		fields=["method"],
		limit_page_length=0,
	)
	total = len(rows)
	counts = {}
	for row in rows:
		title = (row.method or "Unknown").split("\n")[0][:80]
		counts[title] = counts.get(title, 0) + 1
	top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:6]
	tone = "red" if total > 50 else "amber" if total else "green"
	return {
		"status": tone,
		"total_24h": total,
		"top": [{"title": title, "count": count} for title, count in top],
	}


# ----------------------------------------------------------------- assembly


def _tile(key, label, status, headline, *, configured, metrics=None, links=None, notes=None, actions=None):
	return {
		"key": key,
		"label": label,
		"status": status,
		"headline": headline,
		"configured": configured,
		"metrics": metrics or [],
		"links": links or [],
		"notes": notes or [],
		"actions": actions or [],
	}


_INTEGRATIONS = [
	("quickbooks", "QuickBooks Online", "/app/quickbooks-online-settings", _check_quickbooks),
	("drive", "Google Drive", "/app/project-folder-google-drive-settings", _check_drive),
	("triton", "Telephony (Triton / Twilio)", "/app/triton-settings", _check_triton),
	("gemini", "AI Drafting (Vertex / Gemini)", "/app/triton-settings", _check_gemini),
	("ga4", "Analytics (GA4 / Search Console)", "/app/ga4-settings", _check_ga4),
]


@frappe.whitelist()
def get_health():
	"""System-Manager-only health snapshot of every external integration plus
	scheduler liveness and a 24 h error digest. DB-only (no outbound calls)."""
	frappe.only_for("System Manager")

	integrations = []
	for key, label, route, fn in _INTEGRATIONS:
		try:
			integrations.append(fn(key, label, route))
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Integrations Health: {key}")
			integrations.append(_tile(key, label, "neutral", "Status unavailable", configured=False,
				links=[{"label": "Settings", "route": route}],
				notes=["Could not read this integration's settings — see the Error Log."]))

	def _safe(fn, fallback):
		try:
			return fn()
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Integrations Health: section")
			return fallback

	return {
		"generated_at": str(now_datetime()),
		"integrations": integrations,
		"scheduler": _safe(_check_scheduler, {"status": "neutral", "enabled": None, "jobs": [],
			"failed_24h": 0, "recent_failures": [], "app_job_count": 0}),
		"errors": _safe(_check_errors, {"status": "neutral", "total_24h": 0, "top": []}),
	}


@frappe.whitelist()
def run_drive_test():
	"""On-demand live Drive validation (the one outbound check), proxied to the
	existing settings-form button so the dashboard can verify access without the
	user leaving the page. System-Manager-only (the proxied call re-checks too)."""
	frappe.only_for("System Manager")
	from erpnext_enhancements.crm_enhancements.drive_sync import test_connection

	return test_connection()
