# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Subscription lifecycle — the component whose failure is silent, total and permanent.

	bench --site <site> execute erpnext_enhancements.chat.sync.subscriptions.renew_due_subscriptions

**Read this paragraph before changing anything below.** Google's Workspace Events API
*permanently deletes* a subscription the moment it expires. There is no expired state to
inspect, nothing to ``reactivate``, and no ``patch`` that brings it back — recovery is
``subscriptions.create`` of a brand-new subscription with a brand-new name. Nothing raises
when this happens. No job fails, no HTTP call 500s, no Error Log row appears. Inbound sync
simply stops, in full, and the first symptom is a coworker asking three days later why their
Chat reply never showed up in ERPNext. That is why this is a module with its own health
record, its own alerting and its own tests, rather than a five-line renewal helper.

Shape B, confirmed live 2026-08-09
-----------------------------------
One ``//chat.googleapis.com/spaces/-`` subscription **per coworker**, user authentication
only (domain-wide delegation), ``payloadOptions.includeResource: false``, and ``ttl``
**omitted** so Google grants its maximum. A ``validateOnly`` create in that exact shape
returned HTTP 200 from the production VM; the identical call with ``includeResource: true``
and a seven-day ttl was rejected ``INVALID_ARGUMENT — "The subscription expiration time
exceeds the maximum allowed."`` So the seven-day ceiling is real for this tenant, and
resource data genuinely costs TTL. Neither fact is re-litigable from inside this module.

The consequence that shapes every write here: **one subscription per coworker means one
independent revocation surface per coworker.** A single person clicking "remove access" in
their Google account silently kills inbound sync for every space that only they cover, and
nothing else in the system can tell that apart from a quiet week. ``USER_SCOPE_REVOKED``
therefore gets its own branch, its own alert, and the alert names the person — see
:func:`_handle_suspension`.

Never schedule a renewal from a constant
-----------------------------------------
``ttl`` is **input only**; unspecified means "the maximum possible duration". Every published
lifetime is a ceiling ("up to 7 days"), and nothing guarantees the server granted one. So
``expire_time`` is always read off the create/patch response —
:class:`~erpnext_enhancements.chat.gchat.events_client.Subscription` cannot even be
constructed without an ``expireTime`` — and ``renew_after`` is derived from **that**, never
from ``Chat Settings.subscription_ttl_seconds``, which is a request rather than a promise.

The lead time is then **clamped well under half the granted lifetime** by
:func:`renewal_lead_seconds`. The clamp is the part that is easy to leave out and expensive
to leave out: the shipped configuration asks for a 24-hour lead against a 7-day grant, which
is fine — but if Google ever grants four hours, an unclamped 24-hour lead puts ``renew_after``
a full 20 hours *in the past*, and the scheduler then renews the same subscription on every
single pass, forever, burning the 100-calls-per-minute-per-user bucket while looking busy.

Timezones: one conversion, stated once
---------------------------------------
Google returns RFC-3339 **UTC**. ``Chat Event Subscription.expire_time`` is a Frappe
``Datetime``, which is **site-local naive**, and ``chat/health.py`` compares it against
``frappe.utils.now_datetime()``, which is also site-local. So this module converts, and it
converts through :func:`local_datetime_from_epoch` — which derives the offset from the pair
``(now_epoch, now_local)`` rather than from any timezone API, and so cannot be wrong about
which convention Frappe is using on this site. **The stored value is site-local. That settles
the ``VERIFY:`` in chat/health.py's ``_collect_subscriptions``.** This app has already shipped
one bug from comparing a site-local ``now_datetime()`` against a UTC instant (the Turnstile
always-fail), which is why it is spelled out rather than assumed.

Idempotency, because the reminder fires twice
----------------------------------------------
``expirationReminder`` is delivered at **T−12h and again at T−1h**. The renewal path is
therefore written to be idempotent rather than merely correct: a reminder whose subscription
already has ``renew_after`` in the future is a no-op, and a short-lived Redis claim keeps two
workers from patching the same subscription in the same instant. Both are cheap; the
alternative is two ``subscriptions.patch`` calls per renewal for the entire roster.

Safe with the master switch off
--------------------------------
Every entry point gates on :func:`_gate` first. With ``Chat Settings.enabled = 0`` — the
current production state — nothing here reads Google, writes a row or raises an alert; it
returns a dict saying why. Dry-run mode is also a hard no-op, deliberately: a dry-run
subscription is born with an ``expireTime`` at the Unix epoch (``dryrun.py``'s rule that a
plausible timestamp is the field most likely to make a fake read as real), so a scheduler
that "ran" in dry-run would renew every subscription on every pass and write fiction into the
health record.

Nothing here logs a message body, a token or a query string. Errors leaving the Google
surface are re-raised ``from None`` — a bare re-raise out of a background job publishes frame
locals, including ``Authorization: Bearer``, into the Error Log.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Final

import frappe

from erpnext_enhancements.chat import seams
from erpnext_enhancements.chat.doctype.chat_event_subscription.chat_event_subscription import (
	NON_RETRYABLE_SUSPENSION_REASONS,
	STATE_ACTIVE,
	STATE_DELETED,
	STATE_SUSPENDED,
	STATE_UNSPECIFIED,
)
from erpnext_enhancements.chat.gchat import client as chat_client
from erpnext_enhancements.chat.gchat import events_client as events
from erpnext_enhancements.chat.sync import decisions

SUBSCRIPTION_DOCTYPE: Final[str] = "Chat Event Subscription"

# --- tuning ---------------------------------------------------------------------
#
# Every number here is a design fact with a stated consequence, not a preference.

#: Fallback for ``Chat Settings.subscription_renew_before_seconds`` (24 hours). Matches the
#: shipped default and ``chat/health.py``'s own fallback, so the two agree about what
#: "expiring soon" means when settings cannot be read.
DEFAULT_RENEW_BEFORE_SECONDS: Final[int] = 86_400

#: The renewal lead may never exceed this fraction of the **granted** lifetime. "Well under
#: half" — 0.4 leaves at least 60% of the lifetime elapsed before the first renewal attempt,
#: and 40% of it as retry runway afterwards. See the module docstring for what an unclamped
#: lead does when Google grants less than we asked for.
MAX_RENEW_LEAD_FRACTION: Final[float] = 0.4

#: …and never less than this, so a pathologically short grant still gets one attempt with
#: time to spare rather than a renewal scheduled one second before deletion.
MIN_RENEW_LEAD_SECONDS: Final[float] = 60.0

#: A grant this much smaller than the configured lead means Google is handing out far less
#: than the 7-day ceiling this design is budgeted against, which changes the renewal cadence
#: for the whole roster. Worth telling a human about once.
SHORT_GRANT_ALERT_RATIO: Final[float] = 0.5

#: How many rows one scheduler pass will touch. The roster is ~20; the cap exists so a
#: mis-seeded table cannot turn one tick into a thousand Workspace Events calls.
RENEW_BATCH_SIZE: Final[int] = 50

#: Lifetime of the per-subscription renewal claim. Long enough to cover a slow round trip,
#: short enough that a worker killed mid-patch (which a deploy does) does not lock the
#: subscription out of its next hourly pass.
RENEW_CLAIM_TTL_MS: Final[int] = 120_000

#: Consecutive failures at which a renewal problem stops being "retrying" and starts being
#: "nobody is going to fix this without being told". Matches ``chat/health.py``'s threshold.
ALERT_FAILURE_THRESHOLD: Final[int] = 3

#: An operator alert repeats at most this often per distinct key. A scheduled job that emails
#: "still broken" every hour is unsubscribed from within a week, and then the *next* real
#: outage is invisible — this repo has already lost a 40-day data-source outage that way.
ALERT_COOLDOWN_SECONDS: Final[int] = 6 * 3600

#: An ACTIVE subscription that has delivered nothing at all for this long, while the rest of
#: the fleet is delivering, is the failure mode ``event_count`` exists to expose: state
#: ACTIVE, expiry healthy, and no events — a revoked scope not yet reported, a Pub/Sub topic
#: whose IAM binding changed, a subscription created against the wrong target. It looks
#: exactly like a quiet week and is not one.
SILENT_SUBSCRIPTION_SECONDS: Final[int] = 3 * 86_400

#: ``last_error`` is a Small Text. Truncate rather than let a wall of Google JSON push the
#: useful first line out of the list view.
LAST_ERROR_MAX_CHARS: Final[int] = 480

ALERT_SEVERITY_ALARM: Final[str] = "ALARM"
ALERT_SEVERITY_WARN: Final[str] = "WARN"

#: The columns this module reads. Identifiers, states and timestamps — no content, ever.
_ROW_FIELDS: Final[tuple[str, ...]] = (
	"name",
	"subscription_uid",
	"target_resource",
	"target_user",
	"state",
	"suspension_reason",
	"event_types",
	"expire_time",
	"renew_after",
	"last_renewed",
	"consecutive_failures",
	"last_error",
	"event_count",
	"last_event_at",
	"creation",
)


# --------------------------------------------------------------------------------------
# Pure helpers — no I/O, unit-tested bench-free
# --------------------------------------------------------------------------------------


def renewal_lead_seconds(lifetime_seconds: float, configured_lead: float) -> float:
	"""How far *before* expiry to renew, given the lifetime Google actually granted.

	``configured_lead`` is ``Chat Settings.subscription_renew_before_seconds`` — a preference.
	The return value is that preference clamped into a range the granted lifetime can
	actually support:

	* never more than :data:`MAX_RENEW_LEAD_FRACTION` of the lifetime, so ``renew_after``
	  always lands in the future and the renewal cannot become a per-tick no-op loop;
	* never less than :data:`MIN_RENEW_LEAD_SECONDS` (itself capped by the lifetime), so a
	  short grant still leaves room for one retry.

	Pure and total: a zero or negative lifetime returns ``0.0`` rather than raising, because
	the caller for that case is "Google returned an expiry in the past", which is a data
	problem to alert on and not an exception to throw inside a scheduler.
	"""
	lifetime = float(lifetime_seconds or 0.0)
	if lifetime <= 0.0:
		return 0.0

	ceiling = lifetime * MAX_RENEW_LEAD_FRACTION
	floor = min(MIN_RENEW_LEAD_SECONDS, ceiling)
	lead = float(configured_lead or 0.0)
	return max(floor, min(lead, ceiling))


def local_datetime_from_epoch(target_epoch: float, *, now_epoch: float, now_local: datetime) -> datetime:
	"""An absolute instant, expressed as this site's naive local wall clock.

	Deliberately derived from the pair ``(now_epoch, now_local)`` instead of from
	``frappe.utils.convert_utc_to_system_timezone`` or a ``pytz`` lookup. The caller already
	holds both — ``time.time()`` and ``frappe.utils.now_datetime()`` read at the same instant —
	and their difference *is* the site's offset, whatever Frappe currently believes it to be.
	So this cannot disagree with the convention the rest of the site is using, and it needs no
	timezone API whose spelling changes between Frappe versions.

	The result is what goes into ``Chat Event Subscription.expire_time``, because
	``chat/health.py`` compares that field against ``now_datetime()``. Storing Google's UTC
	value unconverted would make every "time to expiry" wrong by the site's offset, in the
	direction that makes an expiring subscription look *healthier* than it is.
	"""
	return now_local + timedelta(seconds=float(target_epoch) - float(now_epoch))


def epoch_from_local_datetime(value: datetime, *, now_epoch: float, now_local: datetime) -> float:
	"""The inverse of :func:`local_datetime_from_epoch`, by the same offset-free reasoning."""
	return float(now_epoch) + (value - now_local).total_seconds()


def rfc3339_utc(epoch: float) -> str:
	"""``2026-08-09T12:00:00.000000Z`` — the only timestamp format Google's filters accept.

	Six fractional digits, always, and an explicit ``Z``.
	``client.build_message_filter`` refuses anything without an explicit offset, and the
	reconciliation sweep's whole correctness rests on comparing the right instant.
	"""
	stamp = datetime.fromtimestamp(float(epoch), tz=timezone.utc)
	return stamp.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def subscription_uid_of(resource_name: str) -> str:
	"""``subscriptions/abc123`` ➜ ``abc123``.

	**Which identifier goes in ``subscription_uid``, and why it is the name segment.**
	``Chat Event Subscription`` has exactly one identifier column and every Workspace Events
	call — ``patch``, ``reactivate``, ``get``, ``delete`` — addresses the resource by
	``subscriptions/{id}``, not by ``Subscription.uid``. Storing the uid alone would leave the
	renewal path unable to name the thing it is renewing, which is the one operation this
	table exists to make possible. So the **name segment** is stored, and
	:func:`resource_name_of` rebuilds the address from it.

	``VERIFY:`` that Google's ``Subscription.uid`` and the id segment of ``Subscription.name``
	are the same string for this API. They are for every response shape seen so far, and
	:func:`_apply_subscription` logs a warning if a create/patch ever returns two different
	values rather than silently preferring one. If they do diverge in production, the fix is a
	second column, not a guess here.
	"""
	return str(resource_name or "").strip().rsplit("/", 1)[-1]


def resource_name_of(uid: str) -> str:
	"""``abc123`` ➜ ``subscriptions/abc123``. Tolerates an already-qualified name."""
	candidate = str(uid or "").strip()
	if not candidate:
		return ""
	if candidate.startswith("subscriptions/"):
		return candidate
	return f"subscriptions/{candidate}"


# --------------------------------------------------------------------------------------
# Alerting
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SyncAlert:
	"""One thing a human has to be told about, with enough context to act on it.

	``key`` is the de-duplication identity, not a message: two alerts about the same
	subscription and the same problem share a key so the cooldown can suppress the repeat,
	while a *different* problem on the same subscription still gets through.
	"""

	key: str
	severity: str
	subject: str
	message: str
	user: str = ""
	subscription: str = ""


#: The alert sink signature. Injected in tests; :func:`raise_operator_alert` in production.
AlertSink = Callable[[SyncAlert], None]


def raise_operator_alert(alert: SyncAlert) -> None:
	"""Put an alert in front of a human. **Never raises.**

	A desk ``Notification Log`` rather than an email, following this app's precedent
	(``kpi_dashboards/snapshots.py``): it lands in the bell menu of people already in the
	desk, needs no outbound mail configuration to work, and cannot be filtered into a folder
	nobody reads.

	Suppressed to at most one delivery per :data:`ALERT_COOLDOWN_SECONDS` per ``key``. The
	cooldown lives in the cache Redis, which a deploy clears — so the first pass after a
	release re-announces anything still broken. That is the right way round: a stale alert
	costs one notification, a suppressed one costs the outage.

	**The ``Error Log`` row moved to the §4.H alert path in v1.292.0 and is now a fallback.**
	It existed so the alert survived the notification being dismissed; a ``Chat Ops Alert``
	row does that better and durably, and the governance path writes its own desk-visible
	``Error Log`` on notification steps carrying the alert name and key. Writing both would
	put two rows in the same table for one event — the duplication this consolidation exists
	to remove. So it is written here only when the governance path did **not** record, which
	is the one case where dropping it would lose the event entirely.
	"""
	recorded = _to_governance_alerts(alert)

	try:
		if not _claim(f"chat:alert:{alert.key}", ALERT_COOLDOWN_SECONDS * 1000):
			return

		# No counter bump here on purpose. `_record_failure` owns
		# `seams.COUNTER_SUBSCRIPTION_FAILURES`, and counting again at the sink would
		# double every renewal failure — a metric that over-reports is argued away as
		# "the alerting is noisy" exactly as fast as one that under-reports is trusted.
		body = f"<p>{frappe.utils.escape_html(alert.message)}</p>"
		if alert.subscription:
			row = frappe.utils.escape_html(alert.subscription)
			body += f"<p>Chat Event Subscription: <code>{row}</code></p>"
		for user in _notification_recipients():
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"for_user": user,
					"type": "Alert",
					"subject": f"[{alert.severity}] {alert.subject}",
					"email_content": body,
				}
			).insert(ignore_permissions=True)

		if not recorded:
			frappe.log_error(
				f"{alert.severity} {alert.key}\n{alert.subject}\n\n{alert.message}",
				"Chat subscriptions — operator alert",
			)
	except Exception:
		# Alerting must never be able to fail the renewal it is reporting on. A swallowed
		# notification is bad; a scheduler that dies before renewing the remaining nineteen
		# subscriptions is the outage this whole module exists to prevent.
		_log_debug("chat subscriptions: alert delivery failed for key=%s", alert.key)


def _to_governance_alerts(alert: SyncAlert) -> bool:
	"""Also record this on the §4.H alert board. Returns whether a row was written.

	**Additive — no channel is lost.** The bell menu stays, and the `Error Log` moves rather
	than disappearing: the caller writes its own only when this returns False.

	This function was the *sixth* private way to tell somebody in this package, and the one
	v1.291.0's inventory missed. It is also the best of the six: it already had a
	deduplication key, and the bell menu is a channel the governance path does not have. So
	it stays exactly as it is, and gains a second destination rather than being replaced.

	**The two cooldowns are not equivalent, which is the reason for both.** This one is a flat
	six-hour Redis claim: at hour seven it announces again, whatever has happened in between,
	and a deploy clearing the cache re-announces everything still broken. The governance path
	counts occurrences on one row and re-notifies on a doubling schedule, and — the part the
	cooldown structurally cannot do — it **resolves**, so a subscription that starts renewing
	again stops appearing on the board. Keeping only the cooldown loses the resolve; keeping
	only the board loses the bell.

	Above ``_claim`` on purpose: the six-hour suppression is a delivery decision about the
	desk notification, and the alert board does its own deduplication with its own lifecycle.
	Putting this below the claim would let one channel's cooldown silently govern the other's
	record — the coupling this consolidation exists to remove.
	"""
	try:
		from erpnext_enhancements.chat.governance import alert_rules, alerts

		# `SyncAlert.key` is already `problem:object`, which is exactly the shape
		# `alert_rules.dedup_key` wants — the problem half becomes the kind and the object
		# half becomes the scope, so the two deduplication schemes agree by construction
		# rather than by coincidence.
		raw = str(alert.key or "subscription")
		kind, _, scope = raw.partition(":")
		return bool(
			alerts.raise_alert(
				subsystem=_subsystem_for(kind),
				kind=kind or "subscription",
				scope=scope,
				severity=(
					alert_rules.SEVERITY_CRITICAL
					if alert.severity == ALERT_SEVERITY_ALARM
					else alert_rules.SEVERITY_WARNING
				),
				summary=str(alert.subject or "")[:255],
				detail=str(alert.message or ""),
				user=alert.user or "",
				subscription=alert.subscription or "",
			)
		)
	except Exception:  # noqa: BLE001 - the other channels must survive this one
		_log_debug("chat subscriptions: governance alert failed for key=%s", alert.key)
		return False


def _subsystem_for(kind: str) -> str:
	"""Which subsystem an alert on this sink belongs to.

	The sink is shared: ``reconcile.py`` injects it too, so a ``reconcile-…`` key filed under
	*subscriptions* would put the recovery sweep's alerts on the wrong shelf. Both land in
	subsystems that are self-delivering — an alert saying inbound is not arriving must not be
	posted into a chat space, because a chat space is the thing that is not arriving.
	"""
	return "inbound" if str(kind or "").startswith("reconcile") else "subscriptions"


def _notification_recipients() -> list[str]:
	"""System Managers, minus the two accounts nobody reads a bell menu on."""
	users = frappe.get_all(
		"Has Role",
		filters={"role": "System Manager", "parenttype": "User"},
		pluck="parent",
		limit=20,
	)
	return sorted({user for user in users if user not in ("Administrator", "Guest")})


# --------------------------------------------------------------------------------------
# Frappe plumbing, kept narrow on purpose
# --------------------------------------------------------------------------------------


def _log_debug(message: str, *args: Any) -> None:
	try:
		frappe.logger("chat").debug(message, *args)
	except Exception:
		pass


def _settings() -> Any:
	try:
		return frappe.get_cached_doc("Chat Settings")
	except Exception:
		return None


def _setting_int(settings: Any, field: str, default: int) -> int:
	"""``getattr(obj, field, None) or default`` — the house rule, and load-bearing here.

	These reads happen inside a scheduled job that runs during ``bench migrate`` windows and
	during ERPNext's own test bootstrap, when the field may not exist yet. A missing tuning
	value must degrade to a default, never crash the renewal loop.
	"""
	raw = getattr(settings, field, None) if settings is not None else None
	if raw in (None, ""):
		return int(default)
	try:
		return int(raw)
	except (TypeError, ValueError):
		return int(default)


def _gate() -> tuple[bool, str]:
	"""May this module talk to Google right now? Returns ``(ok, reason)``.

	Three no-ops, and the second is the interesting one:

	* ``enabled = 0`` — chat is off. Production is here today.
	* ``dry_run_mode = 1`` — a dry-run subscription's ``expireTime`` is the Unix epoch, so it
	  is born expired. A "dry run" of the renewal scheduler would therefore renew every
	  subscription on every pass and write synthetic expiries into the health record, which
	  is strictly worse than not running.
	* no ``Chat Settings`` at all — a half-installed site.

	Note what is deliberately **not** a gate: ``pause_inbound``. That kill switch stops
	*processing* inbound events; it must not stop renewing the subscriptions, because a
	subscription that lapses while inbound is paused cannot be resumed when somebody
	un-pauses it — it has to be recreated, and the events in between are gone.
	"""
	settings = _settings()
	if settings is None:
		return False, "Chat Settings is unreadable"
	if not _setting_int(settings, "enabled", 0):
		return False, "Chat Settings.enabled is 0"
	if _setting_int(settings, "dry_run_mode", 1):
		return False, "Chat Settings.dry_run_mode is 1"
	return True, ""


def _claim(key: str, ttl_ms: int) -> bool:
	"""Take a short-lived exclusive claim. ``True`` if this caller got it.

	``frappe.cache()`` is a ``redis.Redis`` subclass, so ``set(nx=…, px=…)`` is available
	directly. **Fails open**: if the cache is unreachable the claim is granted, because every
	operation this guards is independently idempotent (a second ``subscriptions.patch`` costs
	one quota unit and extends the same subscription) and refusing to renew because Redis is
	down would turn a cache outage into an inbound outage.
	"""
	try:
		site = getattr(frappe.local, "site", None) or "unknown-site"
		return bool(frappe.cache().set(f"{site}|{key}", "1", nx=True, px=int(ttl_ms)))
	except Exception:
		return True


def now_pair() -> tuple[float, datetime]:
	"""``(epoch_seconds, site_local_naive_now)``, read as close together as possible.

	Both, always, and from one place: every conversion in this module is expressed as an
	offset between the two, so reading them apart is how a renewal ends up scheduled a few
	milliseconds — or, once daylight saving moves, an hour — off.

	Public because ``sync/reconcile.py`` converts the same way and must not grow a second,
	subtly different clock.
	"""
	return time.time(), frappe.utils.now_datetime()


def _read(name: str) -> dict[str, Any] | None:
	"""One subscription row as a plain dict, or ``None``.

	``frappe.db.get_value`` rather than ``get_doc``: this is an operational health record, the
	columns are identifiers and timestamps, and a dict is what the rest of the module passes
	around. No content column exists on this table, so the membership rule
	(``chat/permissions.py``: never read chat *content* without a membership filter) has
	nothing to apply to.
	"""
	row = frappe.db.get_value(SUBSCRIPTION_DOCTYPE, name, list(_ROW_FIELDS), as_dict=True)
	return dict(row) if row else None


def _update(name: str, values: Mapping[str, Any]) -> None:
	"""Write a set of columns on one subscription row.

	``update_modified`` is left at its default here, unlike ``Chat Message.sync_state``: this
	table has no digest watermark hanging off ``modified``, and "when did this health record
	last change" is exactly what an operator wants from the list view.
	"""
	frappe.db.set_value(SUBSCRIPTION_DOCTYPE, name, dict(values), update_modified=True)


def _is_uid_collision(exc: BaseException) -> bool:
	"""Did this write bounce off the unique index on ``subscription_uid``?

	Matched on the exception **text** rather than its class, deliberately. The same collision
	arrives as a raw pymysql ``IntegrityError`` (1062) when it comes through
	``frappe.db.set_value``, and as ``frappe.exceptions.UniqueValidationError`` when it comes
	through the Document layer. Catching one class only would fail open on the other — the
	trap that made an earlier dedupe in this app silently useless — and the index name is the
	one thing both spellings carry.
	"""
	text = str(exc)
	if "subscription_uid" in text:
		return True
	unique_error = getattr(frappe, "UniqueValidationError", None)
	return bool(unique_error) and isinstance(exc, unique_error)


def _release_uid_claim(uid: str, *, keep: str) -> list[str]:
	"""Clear ``subscription_uid`` from every row holding ``uid`` except ``keep``.

	The uid identifies a *subscription*, and Workspace Events makes that identity permanent
	per ``(authorizing user, target resource)`` — so at most one row may claim it and it has
	to be the live one. A superseded row keeps its state, expiry and ``last_error``; it just
	stops claiming an identity that now belongs to its replacement.

	Returns the rows it released, for the debug log. Never raises on an empty uid.
	"""
	if not uid:
		return []
	holders = frappe.get_all(
		SUBSCRIPTION_DOCTYPE,
		filters={"subscription_uid": uid},
		fields=["name"],
		limit=RENEW_BATCH_SIZE,
	)
	released: list[str] = []
	for holder in holders:
		holder_name = str(holder.get("name") or "")
		if not holder_name or holder_name == keep:
			continue
		_update(holder_name, {"subscription_uid": None})
		released.append(holder_name)
	return released


def _as_datetime(value: Any) -> datetime | None:
	if not value:
		return None
	if isinstance(value, datetime):
		return value
	try:
		return frappe.utils.get_datetime(value)
	except Exception:
		return None


def _as_int(value: Any, default: int = 0) -> int:
	try:
		return int(value)
	except (TypeError, ValueError):
		return default


def error_text(exc: BaseException, action: str) -> str:
	"""A one-line, secret-scrubbed, length-bounded description of a failure.

	``client.scrub_secrets`` is applied because a Google error string can quote the request it
	rejected, and the request carried a bearer token. Truncated because ``last_error`` is a
	Small Text and the first line is the useful one.
	"""
	text = f"{action}: {type(exc).__name__}: {exc}"
	return chat_client.scrub_secrets(text)[:LAST_ERROR_MAX_CHARS]


def _events_client_for(subject: str) -> events.WorkspaceEventsClient:
	"""A Workspace Events client impersonating one coworker.

	``spaces/-`` is user-authentication only, so there is no app-identity fallback to reach
	for — a client built as the app identity is refused by ``execute``'s identity check before
	anything is sent, which is a better failure than a 403 that reads like a scope problem.
	"""
	return events.WorkspaceEventsClient(subject=subject, dry_run=False)


# --------------------------------------------------------------------------------------
# Reading and writing the health record
# --------------------------------------------------------------------------------------


def _rows(*, include_deleted: bool = False, limit: int = RENEW_BATCH_SIZE) -> list[dict[str, Any]]:
	"""Every live subscription row, soonest expiry first.

	``frappe.get_all`` on an operational table with an explicit column list. The standing rule
	— ``get_all`` is ``get_list(ignore_permissions=True)`` wearing a friendlier name — is about
	chat **content**; this table holds no message text, no sender body and no room reference,
	and this runs in a background job with no session user to filter against.
	"""
	filters: dict[str, Any] = {}
	if not include_deleted:
		filters["state"] = ["!=", STATE_DELETED]
	rows = frappe.get_all(
		SUBSCRIPTION_DOCTYPE,
		filters=filters,
		fields=list(_ROW_FIELDS),
		order_by="expire_time asc",
		limit=max(1, int(limit or RENEW_BATCH_SIZE)),
	)
	return [dict(row) for row in rows]


def _orphan_placeholder_for(user: str) -> str:
	"""The newest committed-but-never-completed placeholder row for this coworker, or ``""``.

	A placeholder is a row whose Google call never landed: ``STATE_UNSPECIFIED``, no uid, no
	expiry. Reusing one is safe *because* it carries no identity — no lifecycle event can
	resolve to it, nothing links to it, and there is no expiry for the scheduler to act on.

	``["is", "not set"]`` rather than ``["in", [None, ""]]``: the DocField carries
	``unique: 1``, so frappe coerces the empty string to ``NULL`` on the way in, and
	``IN (NULL, '')`` never matches a ``NULL`` — the filter would match nothing, forever, and
	the leak it exists to stop would look fixed.
	"""
	if not user:
		return ""
	rows = frappe.get_all(
		SUBSCRIPTION_DOCTYPE,
		filters={
			"target_user": user,
			"state": STATE_UNSPECIFIED,
			"subscription_uid": ["is", "not set"],
		},
		fields=["name"],
		order_by="creation desc",
		limit=1,
	)
	return str(rows[0]["name"]) if rows else ""


def active_subscription_for(user: str) -> dict[str, Any] | None:
	"""This coworker's current subscription row, or ``None``.

	"Current" means not ``DELETED``: a lapsed subscription is marked ``DELETED`` and a fresh
	row is inserted for its replacement, so the record of the gap the reconciliation sweep has
	to cover survives instead of being overwritten.

	**The replacement is a new row but not a new subscription**, and the distinction cost a day
	of inbound sync. This docstring used to claim a recreated subscription is *genuinely a
	different subscription with a different name*; it is not. Workspace Events ids are
	deterministic per ``(authorizing user, target resource)``, so the replacement is handed back
	the same name — which is why the superseded row has to release its ``subscription_uid``
	(the column is unique table-wide) rather than keep it as history. See
	:func:`_recreate_one` and ``patches/release_superseded_subscription_uids``.
	"""
	rows = frappe.get_all(
		SUBSCRIPTION_DOCTYPE,
		filters={"target_user": user, "state": ["!=", STATE_DELETED]},
		fields=list(_ROW_FIELDS),
		order_by="creation desc",
		limit=1,
	)
	return dict(rows[0]) if rows else None


def _apply_subscription(
	name: str,
	subscription: events.Subscription,
	*,
	now_epoch: float,
	now_local: datetime,
	renewed: bool,
	alert: AlertSink,
	configured_lead: int,
) -> dict[str, Any]:
	"""Write a Google response onto the health record, and derive the next renewal from it.

	This is the only place ``expire_time`` and ``renew_after`` are written, and it always
	writes them **together, from the same response**. Splitting them is how a subscription
	ends up with an expiry Google granted and a renewal date somebody computed.
	"""
	uid_from_name = subscription_uid_of(subscription.name)
	if subscription.uid and subscription.uid != uid_from_name:
		# Not fatal, and not silently resolved: every call addresses the resource by name, so
		# the name segment is what gets stored. See subscription_uid_of's VERIFY.
		_log_debug(
			"chat subscriptions: Subscription.uid %s differs from the name segment %s; storing the name segment",
			subscription.uid,
			uid_from_name,
		)

	lifetime = float(subscription.expire_epoch) - float(now_epoch)
	lead = renewal_lead_seconds(lifetime, configured_lead)
	expire_local = local_datetime_from_epoch(
		subscription.expire_epoch, now_epoch=now_epoch, now_local=now_local
	)

	values: dict[str, Any] = {
		"subscription_uid": uid_from_name,
		"state": subscription.state or STATE_ACTIVE,
		"expire_time": expire_local,
		"renew_after": expire_local - timedelta(seconds=lead),
		"consecutive_failures": 0,
		"last_error": "",
	}
	if subscription.target_resource:
		values["target_resource"] = subscription.target_resource
	if subscription.event_types:
		values["event_types"] = "\n".join(subscription.event_types)
	if (subscription.state or STATE_ACTIVE) != STATE_SUSPENDED:
		values["suspension_reason"] = ""
	if renewed:
		values["last_renewed"] = now_local

	try:
		_update(name, values)
	except Exception as exc:
		if not _is_uid_collision(exc):
			raise
		# Another row is still claiming this uid — a superseded one that kept its claim. See
		# _recreate_one for why the id repeats at all. Release the stale claim and write once
		# more: raising here aborts the caller *before* its gap sweep, so the recreate fails
		# and the messages it exists to recover are never fetched.
		released = _release_uid_claim(uid_from_name, keep=name)
		_log_debug(
			"chat subscriptions: released a stale claim on %s held by %s",
			uid_from_name,
			", ".join(released) or "<nothing>",
		)
		_update(name, values)

	if lifetime <= 0:
		alert(
			SyncAlert(
				key=f"subscription-expiry-in-past:{name}",
				severity=ALERT_SEVERITY_ALARM,
				subject="A Chat subscription came back already expired",
				message=(
					f"Google returned expireTime {subscription.expire_time} for {subscription.name}, "
					"which is in the past. Inbound sync for this coworker is not running and a "
					"renewal cannot fix it — the subscription has to be recreated."
				),
				subscription=name,
			)
		)
	elif configured_lead and lead < configured_lead * SHORT_GRANT_ALERT_RATIO:
		alert(
			SyncAlert(
				key=f"subscription-short-grant:{name}",
				severity=ALERT_SEVERITY_WARN,
				subject="Google granted a much shorter subscription lifetime than requested",
				message=(
					f"{subscription.name} was granted {int(lifetime)}s, so the renewal lead was "
					f"clamped from {configured_lead}s to {int(lead)}s. The design is budgeted "
					"against a 7-day ceiling; a materially shorter grant means the whole roster "
					"needs renewing far more often than hourly-with-a-day-of-slack."
				),
				subscription=name,
			)
		)

	return values


def _record_failure(row: Mapping[str, Any], exc: BaseException, *, action: str, alert: AlertSink) -> None:
	"""Count a failed renewal/reactivation/create and tell somebody.

	The alert fires on the **first** failure, not only at the threshold. Severity escalates at
	:data:`ALERT_FAILURE_THRESHOLD`, but a renewal that failed once is already a subscription
	that will be deleted on schedule if nobody looks, and the cooldown in
	:func:`raise_operator_alert` is what stops the hourly repeat from becoming noise.
	"""
	name = str(row.get("name") or "")
	failures = _as_int(row.get("consecutive_failures"), 0) + 1
	user = str(row.get("target_user") or "")

	try:
		_update(name, {"consecutive_failures": failures, "last_error": error_text(exc, action)})
	except Exception:
		_log_debug("chat subscriptions: could not record a failure on %s", name)

	seams.bump(seams.COUNTER_SUBSCRIPTION_FAILURES)

	alert(
		SyncAlert(
			key=f"subscription-{action}-failed:{name}",
			severity=ALERT_SEVERITY_ALARM if failures >= ALERT_FAILURE_THRESHOLD else ALERT_SEVERITY_WARN,
			subject=f"Chat subscription {action} failed for {user or 'an unknown coworker'}",
			message=(
				f"{action} failed {failures} time(s) in a row for {user or '<no target_user>'} "
				f"({name}). An expired subscription is deleted by Google and cannot be renewed — "
				"only recreated — and inbound Chat sync for every space only this person covers "
				f"stops with no error anywhere. Detail: {error_text(exc, action)}"
			),
			user=user,
			subscription=name,
		)
	)


# --------------------------------------------------------------------------------------
# Creating a subscription
# --------------------------------------------------------------------------------------


def ensure_subscription_for_user(
	user: str,
	*,
	client_factory: Callable[[str], Any] | None = None,
	alert: AlertSink | None = None,
	force_new: bool = False,
) -> dict[str, Any]:
	"""Make sure this coworker has a live subscription; create one if not. Idempotent.

	**The row is inserted and committed *before* the Google call, on purpose.** Both orders
	can lose, and they lose asymmetrically. A row with no ``subscription_uid`` is a visible
	failure: it sits in the health report as ``STATE_UNSPECIFIED`` and a reconciliation can
	adopt or delete it. A subscription created at Google with no row is **invisible** — it
	delivers events nobody is expecting, consumes the per-user cap, and nothing in ERPNext
	knows it exists. The production deploy kills workers mid-flight (``FLUSHDB`` plus a full
	honcho restart), so "the process died between the call and the write" is a normal
	occurrence here, not a thought experiment.
	"""
	alert = alert or raise_operator_alert
	summary: dict[str, Any] = {"status": "skipped", "user": user, "reason": "", "subscription": ""}

	ok, reason = _gate()
	if not ok:
		summary["reason"] = reason
		return summary

	if not force_new:
		existing = active_subscription_for(user)
		if existing and existing.get("state") == STATE_ACTIVE:
			summary.update(status="exists", subscription=existing["name"])
			return summary

	settings = _settings()
	topic = str(getattr(settings, "pubsub_topic", None) or "").strip()
	if not topic:
		summary["reason"] = "Chat Settings.pubsub_topic is empty"
		return summary

	target = chat_client.chat_target_resource()
	event_types = events.MESSAGE_EVENT_TYPES

	row_name = ""
	try:
		# Adopt an abandoned placeholder rather than adding to the pile. A create that fails
		# after its row is committed — the docstring above explains why the row goes first —
		# leaves a uid-less row behind, and a *repeating* failure leaked one per attempt: 27
		# per coworker in the uid collision this shipped with (v1.340.1). Reuse preserves the
		# insert-before-call guarantee exactly — the row is still there, still committed,
		# before Google is asked for anything.
		row_name = _orphan_placeholder_for(user)
		if row_name:
			_update(
				row_name,
				{"target_resource": target, "event_types": "\n".join(event_types), "last_error": ""},
			)
		else:
			doc = frappe.get_doc(
				{
					"doctype": SUBSCRIPTION_DOCTYPE,
					"target_resource": target,
					"target_user": user,
					"state": STATE_UNSPECIFIED,
					"event_types": "\n".join(event_types),
					"consecutive_failures": 0,
					"event_count": 0,
				}
			).insert(ignore_permissions=True)
			row_name = doc.name
		# Committed before the network call — see the docstring. A guarded commit: a failure
		# here means the placeholder may be lost, which degrades to the invisible-orphan case
		# rather than to a crash.
		try:
			frappe.db.commit()
		except Exception:
			_log_debug("chat subscriptions: could not commit the placeholder row for %s", user)
	except Exception as exc:
		summary["reason"] = error_text(exc, "create-placeholder")
		alert(
			SyncAlert(
				key=f"subscription-placeholder-failed:{user}",
				severity=ALERT_SEVERITY_ALARM,
				subject=f"Could not record a Chat subscription for {user}",
				message=(
					"The Chat Event Subscription row could not be inserted, so no subscription "
					f"was created for {user} and inbound sync will not cover their spaces. "
					f"Detail: {summary['reason']}"
				),
				user=user,
			)
		)
		return summary

	summary["subscription"] = row_name
	now_epoch, now_local = now_pair()
	try:
		client = (client_factory or _events_client_for)(user)
		subscription = client.create_subscription(
			target_resource=target,
			event_types=event_types,
			pubsub_topic=topic,
			include_resource=False,
			ttl=None,
		)
	except Exception as exc:
		placeholder = {"name": row_name, "target_user": user, "consecutive_failures": 0}
		_record_failure(placeholder, exc, action="create", alert=alert)
		summary["status"] = "failed"
		summary["reason"] = error_text(exc, "create")
		return summary

	if subscription.is_dry_run:
		# Belt and braces behind _gate(). A dry-run subscription is born expired; writing its
		# expiry into the health record would make the scheduler renew it forever.
		summary["status"] = "dry-run"
		summary["reason"] = "the transport answered in dry-run mode"
		return summary

	_apply_subscription(
		row_name,
		subscription,
		now_epoch=now_epoch,
		now_local=now_local,
		renewed=True,
		alert=alert,
		configured_lead=_setting_int(
			settings, "subscription_renew_before_seconds", DEFAULT_RENEW_BEFORE_SECONDS
		),
	)
	seams.bump(seams.COUNTER_SUBSCRIPTION_RENEWALS)
	summary["status"] = "created"
	return summary


# --------------------------------------------------------------------------------------
# The scheduler entry point
# --------------------------------------------------------------------------------------


def renew_due_subscriptions(
	limit: int = 0,
	*,
	client_factory: Callable[[str], Any] | None = None,
	alert: AlertSink | None = None,
) -> dict[str, Any]:
	"""Renew, reactivate and recreate whatever is due. **The scheduled entry point.**

		bench --site <site> execute \\
			erpnext_enhancements.chat.sync.subscriptions.renew_due_subscriptions

	Runs **hourly**, and a full renewal lead early. The ``expirationReminder`` CloudEvents are
	the backstop, not the trigger: a design whose renewal depends on receiving a push fails
	exactly when push is broken, which is the failure it exists to survive.

	Never raises. Each row is isolated, so one coworker's revoked grant cannot stop the other
	nineteen from being renewed — that is the single most important property of this loop and
	the reason it is written as a per-row ``try``.

	Returns a JSON-encodable summary; ``bench execute`` prints it.
	"""
	alert = alert or raise_operator_alert
	summary: dict[str, Any] = {
		"status": "ok",
		"reason": "",
		"checked": 0,
		"renewed": 0,
		"already_renewed": 0,
		"reactivated": 0,
		"recreated": 0,
		"revoked": 0,
		"failed": 0,
		"idle": 0,
		"alerts": 0,
	}

	ok, reason = _gate()
	if not ok:
		summary["status"] = "skipped"
		summary["reason"] = reason
		return summary

	settings = _settings()
	configured_lead = _setting_int(
		settings, "subscription_renew_before_seconds", DEFAULT_RENEW_BEFORE_SECONDS
	)
	batch = max(1, int(limit or RENEW_BATCH_SIZE))

	try:
		rows = _rows(limit=batch)
	except Exception as exc:
		summary["status"] = "failed"
		summary["reason"] = error_text(exc, "list-subscriptions")
		return summary

	now_epoch, now_local = now_pair()
	for row in rows:
		summary["checked"] += 1
		action = classify_due(row, now_local)
		try:
			outcome = _dispatch(
				action,
				row,
				now_epoch=now_epoch,
				now_local=now_local,
				configured_lead=configured_lead,
				client_factory=client_factory,
				alert=alert,
			)
		except Exception as exc:
			_record_failure(row, exc, action=action, alert=alert)
			summary["failed"] += 1
			continue
		summary[outcome] = summary.get(outcome, 0) + 1

	alerts = check_subscription_health(alert=alert, now_local=now_local)
	summary["alerts"] = len(alerts)
	return summary


def classify_due(row: Mapping[str, Any], now_local: datetime) -> str:
	"""What, if anything, this row needs. Pure given ``(row, now_local)``.

	Split out and named so the decision is testable without a transport, and so the ordering
	of the branches is visible: **suspension is checked before expiry**, because a suspended
	subscription still expires on its original schedule and a reactivate on an already-expired
	one is a 404 rather than a recovery.
	"""
	state = str(row.get("state") or STATE_UNSPECIFIED)
	if state == STATE_DELETED:
		return "idle"

	if state == STATE_SUSPENDED:
		reason = str(row.get("suspension_reason") or "")
		if reason in NON_RETRYABLE_SUSPENSION_REASONS:
			return "revoked"
		return "reactivate"

	expire = _as_datetime(row.get("expire_time"))
	if expire is not None and expire <= now_local:
		# Google deletes an expired subscription outright. There is nothing to patch.
		return "recreate"
	if expire is None:
		# No expiry was ever read back. A patch both extends it and returns the truth.
		return "renew"

	renew_after = _as_datetime(row.get("renew_after"))
	if renew_after is None or renew_after <= now_local:
		return "renew"
	return "idle"


def _dispatch(
	action: str,
	row: Mapping[str, Any],
	*,
	now_epoch: float,
	now_local: datetime,
	configured_lead: int,
	client_factory: Callable[[str], Any] | None,
	alert: AlertSink,
) -> str:
	if action == "renew":
		return _renew_one(
			row,
			now_epoch=now_epoch,
			now_local=now_local,
			configured_lead=configured_lead,
			client_factory=client_factory,
			alert=alert,
		)
	if action == "reactivate":
		return _reactivate_one(
			row,
			now_epoch=now_epoch,
			now_local=now_local,
			configured_lead=configured_lead,
			client_factory=client_factory,
			alert=alert,
		)
	if action == "recreate":
		return _recreate_one(row, now_local=now_local, client_factory=client_factory, alert=alert)
	if action == "revoked":
		_alert_scope_revoked(row, alert=alert)
		return "revoked"
	return "idle"


def _renew_one(
	row: Mapping[str, Any],
	*,
	now_epoch: float,
	now_local: datetime,
	configured_lead: int,
	client_factory: Callable[[str], Any] | None,
	alert: AlertSink,
) -> str:
	"""``subscriptions.patch`` the ttl. Idempotent, because the reminder fires twice.

	Two independent guards, and both are needed:

	* a **re-read** of the row, then ``renew_after`` in the future ⇒ already renewed. This is
	  what makes the T−1h reminder free after the T−12h one succeeded, and what keeps the
	  T−1h reminder *working* when the T−12h one failed (``renew_after`` is then still in the
	  past, so the retry happens);
	* a short **Redis claim**, so the hourly scheduler and an inbound reminder landing in the
	  same second do not both patch.
	"""
	name = str(row.get("name") or "")
	if not _claim(f"chat:subrenew:{name}", RENEW_CLAIM_TTL_MS):
		return "already_renewed"

	fresh = _read(name) or dict(row)
	renew_after = _as_datetime(fresh.get("renew_after"))
	if renew_after is not None and renew_after > now_local:
		return "already_renewed"

	uid = str(fresh.get("subscription_uid") or "")
	if not uid:
		# A placeholder row whose create never completed. Recreating is the only way forward;
		# patching a subscription we cannot name is not.
		return _recreate_one(fresh, now_local=now_local, client_factory=client_factory, alert=alert)

	client = (client_factory or _events_client_for)(str(fresh.get("target_user") or ""))
	subscription = client.patch_subscription(resource_name_of(uid), ttl=None)
	if subscription.is_dry_run:
		return "idle"

	_apply_subscription(
		name,
		subscription,
		now_epoch=now_epoch,
		now_local=now_local,
		renewed=True,
		alert=alert,
		configured_lead=configured_lead,
	)
	seams.bump(seams.COUNTER_SUBSCRIPTION_RENEWALS)
	return "renewed"


def _reactivate_one(
	row: Mapping[str, Any],
	*,
	now_epoch: float,
	now_local: datetime,
	configured_lead: int,
	client_factory: Callable[[str], Any] | None,
	alert: AlertSink,
) -> str:
	"""``subscriptions.reactivate``, then renew if the clock says so.

	**Reactivating does not extend the expiry.** A suspension is a countdown, not a pause: the
	subscription keeps its original ``expireTime`` throughout, so a reactivate performed at
	T−2h buys two hours and nothing more. That is why this immediately re-classifies and
	renews when ``renew_after`` has already passed, instead of waiting for the next tick.
	"""
	name = str(row.get("name") or "")
	uid = str(row.get("subscription_uid") or "")
	if not uid:
		return _recreate_one(row, now_local=now_local, client_factory=client_factory, alert=alert)

	client = (client_factory or _events_client_for)(str(row.get("target_user") or ""))
	subscription = client.reactivate_subscription(resource_name_of(uid))
	if subscription.is_dry_run:
		return "idle"

	_apply_subscription(
		name,
		subscription,
		now_epoch=now_epoch,
		now_local=now_local,
		renewed=False,
		alert=alert,
		configured_lead=configured_lead,
	)

	refreshed = _read(name) or {}
	if classify_due(refreshed, now_local) == "renew":
		_renew_one(
			refreshed,
			now_epoch=now_epoch,
			now_local=now_local,
			configured_lead=configured_lead,
			client_factory=client_factory,
			alert=alert,
		)
	return "reactivated"


def _recreate_one(
	row: Mapping[str, Any],
	*,
	now_local: datetime,
	client_factory: Callable[[str], Any] | None,
	alert: AlertSink,
) -> str:
	"""The subscription is gone. Create a new one, then sweep the gap it left behind.

	Order matters. The new subscription is created **first** so the gap stops widening while
	the sweep runs, and only then is the reconciliation sweep started for the window between
	the old expiry and now. Sweeping first would leave a second, smaller hole.

	The sweep is the entire reason a missed renewal is recoverable at all: Workspace Events
	has **no replay**, so every event delivered while the subscription was dead is gone
	forever, and ``spaces.messages.list`` filtered on ``createTime`` is the only way to find
	out what was said.
	"""
	name = str(row.get("name") or "")
	user = str(row.get("target_user") or "")
	old_expiry = _as_datetime(row.get("expire_time"))

	# Idempotent for the same reason _renew_one is: the trigger is a Pub/Sub delivery, and
	# Pub/Sub redelivers. Without this, a redelivered `expired` for a subscription already
	# replaced supersedes the *replacement* and creates another, on every redelivery.
	fresh = _read(name) if name else None
	if fresh is not None:
		if str(fresh.get("state") or "") == STATE_DELETED:
			return "already_recreated"
		fresh_expiry = _as_datetime(fresh.get("expire_time"))
		if fresh_expiry is not None and fresh_expiry > now_local:
			return "already_recreated"

	if name:
		_update(
			name,
			{
				"state": STATE_DELETED,
				# Released, not kept. Workspace Events subscription ids are **deterministic**:
				# base64 of the id segment reads "s:-:<google user id>:<app>", so the same
				# coworker against the same spaces/- target is handed back the same name every
				# time. The replacement therefore arrives carrying *this* uid, and
				# subscription_uid is unique table-wide — so a history row that keeps its claim
				# makes every future recreate die on a 1062. It did, 27 times in a row, for two
				# coworkers, and the gap sweep below never ran once (v1.340.1). History is
				# state/expire_time/last_error; the identity belongs to whoever is live.
				"subscription_uid": None,
				"last_error": (
					"expired and permanently deleted by Google; recreated. Google does not keep an "
					"expired subscription in any state, so this row is history rather than a thing "
					"that can be renewed."
				)[:LAST_ERROR_MAX_CHARS],
			},
		)

	created = ensure_subscription_for_user(user, client_factory=client_factory, alert=alert, force_new=True)

	alert(
		SyncAlert(
			key=f"subscription-expired:{name or user}",
			severity=ALERT_SEVERITY_ALARM,
			subject=f"A Chat subscription expired for {user or 'an unknown coworker'}",
			message=(
				f"The Workspace Events subscription for {user or '<no target_user>'} expired and was "
				"deleted by Google. Inbound Chat sync for their spaces stopped at "
				f"{old_expiry or 'an unknown time'} and every event in that window is unrecoverable "
				"from Google — a reconciliation sweep over spaces.messages.list is running to "
				f"recover the messages. Replacement subscription: {created.get('status')}."
			),
			user=user,
			subscription=name,
		)
	)

	_sweep_gap(user, since=old_expiry, alert=alert)
	return "recreated"


def _sweep_gap(user: str, *, since: datetime | None, alert: AlertSink) -> None:
	"""Kick the reconciliation sweep for the window a dead subscription left uncovered.

	Imported inside the function: ``reconcile`` imports this module at module scope for its
	clock helpers, and a lazy import here is what keeps that a one-way dependency instead of a
	cycle.

	Guarded, and deliberately not fatal. Recreating the subscription is the urgent half —
	it stops the gap growing — and the sweep can be retried by the scheduler. A sweep failure
	that rolled back the recreate would be strictly worse.
	"""
	try:
		from erpnext_enhancements.chat.sync import reconcile

		reconcile.reconcile_rooms_for_user(user, since=since, reason="subscription-expired", alert=alert)
	except Exception as exc:
		alert(
			SyncAlert(
				key=f"subscription-gap-sweep-failed:{user}",
				severity=ALERT_SEVERITY_ALARM,
				subject=f"The catch-up sweep after a lapsed Chat subscription failed for {user}",
				message=(
					"The subscription was recreated, but the reconciliation sweep that recovers the "
					"messages sent while it was dead did not run. Those messages exist in Google Chat "
					"and not in ERPNext until a sweep succeeds. Detail: "
					f"{error_text(exc, 'gap-sweep')}"
				),
				user=user,
			)
		)


def _alert_scope_revoked(row: Mapping[str, Any], *, alert: AlertSink) -> None:
	"""``USER_SCOPE_REVOKED`` — a person, not a bug. Name them, and stop retrying.

	This branch is the reason ``Chat Event Subscription`` exists as a visible DocType rather
	than as a field on ``Chat Settings``. One coworker revoking their Google grant kills
	inbound sync for **every space that only they cover**, and the symptom is indistinguishable
	from those spaces being quiet. Retrying it burns the per-user quota, buries the signal, and
	cannot possibly succeed: nobody but that person can restore the grant.
	"""
	user = str(row.get("target_user") or "")
	alert(
		SyncAlert(
			key=f"subscription-scope-revoked:{row.get('name')}",
			severity=ALERT_SEVERITY_ALARM,
			subject=f"{user or 'A coworker'} revoked the Google Chat access this app syncs through",
			message=(
				f"Google suspended the Chat event subscription for {user or '<no target_user>'} with "
				"USER_SCOPE_REVOKED. This is a decision that person made in their own Google account, "
				"so no retry can fix it and none is being attempted. Until they re-grant access, "
				"messages sent in Google Chat spaces that only they are a member of will not appear "
				"in ERPNext, and nothing else in the system will look wrong. Ask them directly."
			),
			user=user,
			subscription=str(row.get("name") or ""),
		)
	)


# --------------------------------------------------------------------------------------
# Lifecycle events — they arrive on the same topic as message events
# --------------------------------------------------------------------------------------


def handle_lifecycle_event(
	parsed: decisions.ParsedEvent,
	*,
	client_factory: Callable[[str], Any] | None = None,
	alert: AlertSink | None = None,
) -> str:
	"""Route one subscription lifecycle CloudEvent. Returns a short verdict string.

	**Lifecycle events share the Workspace Events topic with message events**, so the Pub/Sub
	consumer branches on ``ce-type`` and hands the three subscription types here. They are not
	subscribed to and cannot be: Google delivers them whether you ask or not, and naming one
	in ``eventTypes`` is a 400.

	The three are genuinely different failures and get genuinely different handling:

	``expirationReminder`` (T−12h **and** T−1h)
		``subscriptions.patch`` the ttl. Fires twice, so the path is idempotent by
		construction rather than by luck.

	``suspended``
		Fix the cause and ``subscriptions.reactivate`` — except for the scope-revoked
		reasons, which are a human decision and get an alert naming the human instead.

	``expired``
		Terminal. Nothing to reactivate; create a fresh subscription and immediately sweep
		the gap, because Workspace Events has no replay.

	Never raises: this runs inside the Pub/Sub consumer, and an exception here would nack a
	delivery that will be redelivered forever.
	"""
	alert = alert or raise_operator_alert

	if not getattr(parsed, "is_lifecycle", False):
		return "not-lifecycle"

	ok, reason = _gate()
	if not ok:
		_log_debug("chat subscriptions: lifecycle event ignored (%s)", reason)
		return "disabled"

	uid = subscription_uid_of(parsed.subscription_name or parsed.resource_name)
	row = _row_by_uid(uid)
	if row is None:
		alert(
			SyncAlert(
				key=f"subscription-unknown:{uid}",
				severity=ALERT_SEVERITY_WARN,
				subject="A Chat subscription lifecycle event arrived for a subscription we do not know",
				message=(
					f"{parsed.event_type} named subscriptions/{uid}, which has no Chat Event "
					"Subscription row. Either the row was lost after the subscription was created — "
					"the create commits its placeholder first precisely to make this rare — or "
					"somebody created a subscription outside ERPNext. It is delivering events into "
					"our topic either way."
				),
			)
		)
		return "unknown-subscription"

	settings = _settings()
	configured_lead = _setting_int(
		settings, "subscription_renew_before_seconds", DEFAULT_RENEW_BEFORE_SECONDS
	)
	now_epoch, now_local = now_pair()

	try:
		if parsed.event_type == decisions.EVENT_SUBSCRIPTION_EXPIRATION_REMINDER:
			return _renew_one(
				row,
				now_epoch=now_epoch,
				now_local=now_local,
				configured_lead=configured_lead,
				client_factory=client_factory,
				alert=alert,
			)
		if parsed.event_type == decisions.EVENT_SUBSCRIPTION_SUSPENDED:
			return _handle_suspension(
				row,
				parsed,
				now_epoch=now_epoch,
				now_local=now_local,
				configured_lead=configured_lead,
				client_factory=client_factory,
				alert=alert,
			)
		if parsed.event_type == decisions.EVENT_SUBSCRIPTION_EXPIRED:
			return _recreate_one(row, now_local=now_local, client_factory=client_factory, alert=alert)
	except Exception as exc:
		_record_failure(row, exc, action=_action_of(parsed.event_type), alert=alert)
		return "failed"

	return "ignored"


def _action_of(event_type: str) -> str:
	if event_type == decisions.EVENT_SUBSCRIPTION_SUSPENDED:
		return "reactivate"
	if event_type == decisions.EVENT_SUBSCRIPTION_EXPIRED:
		return "recreate"
	return "renew"


def _handle_suspension(
	row: Mapping[str, Any],
	parsed: decisions.ParsedEvent,
	*,
	now_epoch: float,
	now_local: datetime,
	configured_lead: int,
	client_factory: Callable[[str], Any] | None,
	alert: AlertSink,
) -> str:
	"""Record the suspension, then either alert by name or reactivate.

	The reason is read off the event payload, where it is **snake_case** — lifecycle payloads
	use snake_case inside the subscription object while every Chat resource event is
	camelCase, and reading the wrong one yields ``None``, which would silently downgrade
	``USER_SCOPE_REVOKED`` into "some transient suspension, retry it".
	"""
	reason = _suspension_reason_of(parsed)
	name = str(row.get("name") or "")
	_update(name, {"state": STATE_SUSPENDED, "suspension_reason": reason})

	updated = dict(row)
	updated["state"] = STATE_SUSPENDED
	updated["suspension_reason"] = reason

	if reason in NON_RETRYABLE_SUSPENSION_REASONS:
		_alert_scope_revoked(updated, alert=alert)
		return "revoked"

	alert(
		SyncAlert(
			key=f"subscription-suspended:{name}",
			severity=ALERT_SEVERITY_WARN,
			subject=f"Chat subscription suspended for {row.get('target_user') or 'a coworker'}",
			message=(
				f"Google suspended the subscription with reason {reason or 'unspecified'}. "
				"Reactivation is being attempted, but note that a suspension does not pause the "
				"expiry clock — the subscription still dies on its original schedule, so a "
				"suspension left unfixed becomes an expiry."
			),
			user=str(row.get("target_user") or ""),
			subscription=name,
		)
	)

	return _reactivate_one(
		updated,
		now_epoch=now_epoch,
		now_local=now_local,
		configured_lead=configured_lead,
		client_factory=client_factory,
		alert=alert,
	)


def _suspension_reason_of(parsed: decisions.ParsedEvent) -> str:
	"""Dig ``suspension_reason`` out of a lifecycle payload, tolerating either spelling.

	``PHASE2_VERIFIED.md`` §7 records the snake_case convention for lifecycle payloads, and
	the fixtures model it. Both spellings are accepted anyway: the cost of accepting one we
	will never see is nothing, and the cost of missing the one we do see is treating a revoked
	grant as retryable.
	"""
	payload = getattr(parsed, "raw", None) or {}
	container = payload.get("subscription") if isinstance(payload, Mapping) else None
	if not isinstance(container, Mapping):
		container = payload if isinstance(payload, Mapping) else {}
	for key in ("suspension_reason", "suspensionReason"):
		value = container.get(key)
		if value:
			return str(value)
	return ""


def _row_by_uid(uid: str) -> dict[str, Any] | None:
	"""The row claiming this uid — the **live** one, when the claim is somehow shared.

	A superseded row releases its uid (:func:`_release_uid_claim`), so this is normally
	unambiguous. The live-first ordering is for rows predating that release: routing a
	lifecycle event to a ``DELETED`` row is what turned one failed recreate into an unbounded
	loop, because :func:`_rows` skips ``DELETED`` and this lookup did not — the scheduler
	dropped the dead row while every redelivered event kept resurrecting it.

	Falls back to a superseded row rather than ``None``, so
	``handle_lifecycle_event``'s "a subscription we do not know" alert keeps meaning what it
	says. :func:`_recreate_one`'s own guard is what makes landing on a dead row harmless.
	"""
	if not uid:
		return None
	for filters in (
		{"subscription_uid": uid, "state": ["!=", STATE_DELETED]},
		{"subscription_uid": uid},
	):
		rows = frappe.get_all(
			SUBSCRIPTION_DOCTYPE,
			filters=filters,
			fields=list(_ROW_FIELDS),
			order_by="creation desc",
			limit=1,
		)
		if rows:
			return dict(rows[0])
	return None


# --------------------------------------------------------------------------------------
# Delivery bookkeeping
# --------------------------------------------------------------------------------------


def note_event_delivered(reference: str, *, when: datetime | None = None) -> None:
	"""One event arrived on this subscription. Called by the inbound consumer, per delivery.

	``event_count`` is the only available answer to "is this thing actually delivering?".
	A subscription can sit in ``state = ACTIVE`` with a healthy ``expire_time`` and deliver
	nothing at all — a scope revoked but not yet reported, a topic whose IAM binding changed,
	a subscription created against the wrong target — and that failure looks exactly like a
	quiet week. A per-coworker count that has stopped growing while the others keep growing is
	what tells the two apart.

	Written as a single SQL ``UPDATE`` rather than read-modify-write: this runs once per
	inbound event across the whole roster, and two workers incrementing a value they both read
	is how a counter quietly under-reports.

	Never raises. Bookkeeping may not fail an ingest.
	"""
	uid = subscription_uid_of(reference)
	if not uid:
		return
	try:
		frappe.db.sql(
			f"""update `tab{SUBSCRIPTION_DOCTYPE}`
				set event_count = ifnull(event_count, 0) + 1, last_event_at = %(when)s
				where subscription_uid = %(uid)s""",
			{"when": when or frappe.utils.now_datetime(), "uid": uid},
		)
	except Exception:
		_log_debug("chat subscriptions: could not record a delivery for %s", uid)


# --------------------------------------------------------------------------------------
# Health and alerting
# --------------------------------------------------------------------------------------


def check_subscription_health(
	*, alert: AlertSink | None = None, now_local: datetime | None = None
) -> list[SyncAlert]:
	"""Every condition worth waking somebody for. Returns what it raised.

	Alerting is **required, not optional**, and this is the function that makes it so. Five
	conditions, and the last two are the ones a naive implementation misses:

	1. a subscription in any state other than ``ACTIVE``;
	2. a subscription inside its renewal window that has not been renewed;
	3. consecutive renewal failures;
	4. a coworker on the roster with **no** subscription row at all — the failure that leaves
	   no row to inspect, and therefore the one a table-driven check cannot see;
	5. an ``ACTIVE`` subscription that has delivered nothing while the rest of the fleet has.

	Never raises.
	"""
	alert = alert or raise_operator_alert
	raised: list[SyncAlert] = []

	def fire(item: SyncAlert) -> None:
		raised.append(item)
		alert(item)

	try:
		now_local = now_local or frappe.utils.now_datetime()
		settings = _settings()
		horizon = _setting_int(settings, "subscription_renew_before_seconds", DEFAULT_RENEW_BEFORE_SECONDS)
		rows = _rows(limit=200)
	except Exception:
		_log_debug("chat subscriptions: health check could not read its inputs")
		return raised

	covered: set[str] = set()
	fleet_events = sum(_as_int(row.get("event_count"), 0) for row in rows)

	for row in rows:
		name = str(row.get("name") or "")
		user = str(row.get("target_user") or "")
		state = str(row.get("state") or STATE_UNSPECIFIED)
		if state == STATE_ACTIVE:
			covered.add(user)

		if state == STATE_SUSPENDED:
			if str(row.get("suspension_reason") or "") in NON_RETRYABLE_SUSPENSION_REASONS:
				_alert_scope_revoked(row, alert=fire)
			continue

		if state not in (STATE_ACTIVE,):
			fire(
				SyncAlert(
					key=f"subscription-not-active:{name}",
					severity=ALERT_SEVERITY_WARN,
					subject=f"Chat subscription for {user or 'a coworker'} is not ACTIVE",
					message=(
						f"State is {state}. A subscription that never had its state read back from a "
						"create or patch response is a subscription nobody can prove is working."
					),
					user=user,
					subscription=name,
				)
			)
			continue

		expire = _as_datetime(row.get("expire_time"))
		if expire is None:
			fire(
				SyncAlert(
					key=f"subscription-no-expiry:{name}",
					severity=ALERT_SEVERITY_WARN,
					subject=f"Chat subscription for {user or 'a coworker'} has no recorded expiry",
					message=(
						"expire_time is empty, so the renewal scheduler has nothing to derive a period "
						"from and this subscription will lapse without anything noticing."
					),
					user=user,
					subscription=name,
				)
			)
		else:
			seconds_left = (expire - now_local).total_seconds()
			if seconds_left <= 0:
				fire(
					SyncAlert(
						key=f"subscription-lapsed:{name}",
						severity=ALERT_SEVERITY_ALARM,
						subject=f"Chat subscription for {user or 'a coworker'} has already expired",
						message=(
							"Google deletes an expired subscription permanently; it cannot be renewed, "
							"only recreated, and every event since it expired is gone. Inbound sync for "
							"this coworker's spaces has stopped."
						),
						user=user,
						subscription=name,
					)
				)
			elif seconds_left <= horizon:
				fire(
					SyncAlert(
						key=f"subscription-expiring:{name}",
						severity=ALERT_SEVERITY_WARN,
						subject=f"Chat subscription for {user or 'a coworker'} expires soon",
						message=(
							f"{int(seconds_left)}s left, which is inside the {horizon}s renewal window, "
							"and it has not been renewed. Check that the hourly renewal job is scheduled."
						),
						user=user,
						subscription=name,
					)
				)

		failures = _as_int(row.get("consecutive_failures"), 0)
		if failures:
			fire(
				SyncAlert(
					key=f"subscription-failing:{name}",
					severity=ALERT_SEVERITY_ALARM
					if failures >= ALERT_FAILURE_THRESHOLD
					else ALERT_SEVERITY_WARN,
					subject=f"Chat subscription renewal keeps failing for {user or 'a coworker'}",
					message=f"{failures} consecutive failure(s). Last error: {row.get('last_error') or 'none recorded'}",
					user=user,
					subscription=name,
				)
			)

		if fleet_events and not _as_int(row.get("event_count"), 0):
			created = _as_datetime(row.get("creation"))
			old_enough = (
				created is not None and (now_local - created).total_seconds() >= SILENT_SUBSCRIPTION_SECONDS
			)
			if old_enough:
				fire(
					SyncAlert(
						key=f"subscription-silent:{name}",
						severity=ALERT_SEVERITY_WARN,
						subject=f"Chat subscription for {user or 'a coworker'} has never delivered an event",
						message=(
							"State is ACTIVE and the expiry is healthy, but this subscription has "
							"delivered nothing while others have. That is what a revoked scope not yet "
							"reported, a changed Pub/Sub IAM binding, or a subscription created against "
							"the wrong target looks like — identical to a quiet week from the outside."
						),
						user=user,
						subscription=name,
					)
				)

	for user in _roster():
		if user in covered:
			continue
		fire(
			SyncAlert(
				key=f"subscription-missing:{user}",
				severity=ALERT_SEVERITY_ALARM,
				subject=f"{user} has no active Google Chat event subscription",
				message=(
					"There is no ACTIVE Chat Event Subscription for this coworker, so nothing is "
					"listening to their Chat spaces. This is the check that needs no row to fire, "
					"because the failure it catches is the absence of one."
				),
				user=user,
			)
		)

	return raised


def _last_known_expiry(user: str) -> datetime | None:
	"""When this coworker's most recent subscription actually lapsed, if we still know.

	Read from the superseded rows, which is the only place it survives — the whole reason a
	replacement gets a *new* row rather than reusing the old one. Without it a recovery sweep
	has no window and would either re-read everything or nothing.
	"""
	rows = frappe.get_all(
		SUBSCRIPTION_DOCTYPE,
		filters={"target_user": user, "expire_time": ["is", "set"]},
		fields=["expire_time"],
		order_by="expire_time desc",
		limit=1,
	)
	return _as_datetime(rows[0]["expire_time"]) if rows else None


def recover_subscription_for(
	user: str = "",
	*,
	client_factory: Callable[[str], Any] | None = None,
	alert: AlertSink | None = None,
) -> dict[str, Any]:
	"""Put a subscription back for a coworker who has none, and sweep the gap it left.

		bench --site <site> execute \\
			erpnext_enhancements.chat.sync.subscriptions.recover_subscription_for \\
			--kwargs "{'user': 'someone@example.com'}"

	Pass no ``user`` to recover the whole roster.

	**The gap this fills.** Nothing in this app *creates* a missing subscription.
	:func:`renew_due_subscriptions` iterates :func:`_rows`, which skips ``DELETED`` — so once a
	subscription lapses and its row is superseded, the coworker is uncovered and the hourly job
	will never look at them again. :func:`check_subscription_health` notices and *alerts*
	(``subscription-missing``), but an alert is not a repair, and the repair was a hand-typed
	``bench execute`` nobody would find under pressure. That was the state prod was left in on
	2026-08-20 after the uid collision (v1.340.1) marked both live rows ``DELETED``.

	Idempotent: a coworker with an ``ACTIVE`` subscription is skipped, not given a second one.
	The sweep window comes from the superseded row's ``expire_time``, so it covers exactly the
	dead interval; with no recorded expiry the sweep is skipped rather than run unbounded —
	``reconcile_rooms_for_user`` with ``since=None`` would re-read every room from the start.
	"""
	alert = alert or raise_operator_alert
	summary: dict[str, Any] = {"status": "ok", "reason": "", "recovered": [], "skipped": [], "failed": []}

	ok, reason = _gate()
	if not ok:
		summary["status"] = "skipped"
		summary["reason"] = reason
		return summary

	targets = [user] if user else _roster()
	for target in targets:
		existing = active_subscription_for(target)
		if existing and existing.get("state") == STATE_ACTIVE:
			summary["skipped"].append(target)
			continue

		# Read the window *before* creating: ensure_subscription_for_user writes a fresh row
		# whose expire_time would otherwise become the newest one and collapse the gap to zero.
		since = _last_known_expiry(target)
		created = ensure_subscription_for_user(target, client_factory=client_factory, alert=alert)
		if created.get("status") != "created":
			summary["failed"].append(
				{"user": target, "detail": created.get("reason") or created.get("status")}
			)
			continue

		if since is not None:
			_sweep_gap(target, since=since, alert=alert)
		summary["recovered"].append(
			{"user": target, "since": str(since) if since else "", "swept": since is not None}
		)

	if summary["failed"]:
		summary["status"] = "partial" if summary["recovered"] else "failed"
	return summary


def _roster() -> list[str]:
	"""The coworkers who are supposed to have a subscription each.

	``Chat Settings.allowed_users``, which is the same whitelist ``is_user_allowed`` enforces —
	so "who should be covered" and "who may use chat" cannot drift apart. An empty whitelist
	yields an empty roster and the missing-subscription check simply does not fire; inventing a
	roster from every enabled ``User`` would alarm about the entire company on day one.
	"""
	settings = _settings()
	rows: Sequence[Any] = getattr(settings, "allowed_users", None) or []
	out: list[str] = []
	for row in rows:
		user = (
			getattr(row, "user", None) or (row.get("user") if isinstance(row, Mapping) else "") or ""
		).strip()
		if user and user not in out:
			out.append(user)
	return out
