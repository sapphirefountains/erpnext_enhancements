# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The publishing outbox: one Social Publish Job per post per account (TASK-2026-01481).

**Approval writes an outbox row; a cron sweep drives it. Nothing publishes inline.** The prod
deploy runs ``redis-cli FLUSHDB``, destroying every queued-but-unrun job, and Frappe v16 wires no
RQ retries, so ``frappe.enqueue`` is not a timer. ``available_at`` plus the sweep
(``sweeper.py``) is the timer: a post scheduled for 9am Tuesday waits in the database, where a
deploy cannot reach it.

The states, and the one rule that differs from a message queue::

    Pending --claim--> In Progress --published--> Published
       ^                    |  |
       |                    |  +--rejected (4xx)--> Failed
       +--- retry later ----+  +--ambiguous (5xx, timeout)--> Unconfirmed
       +--- lease expired, never dispatched

**A public post must never go out twice.** A job records ``dispatched_at`` just before its
request leaves ERPNext. If it dies after that -- worker killed, deploy mid-request, a 502 on
the create -- the post may already be live, and a queue that "just retries" would publish it
again, in public. So such a job goes to **Unconfirmed**, and a person checks the network and
says which it was (``sweeper.resolve``). Only a job that provably never sent anything goes back
to Pending. (The task said lease-expired rows return to Pending, mirroring the chat relay. For a
chat message a duplicate is a nuisance; for a public post it is not, and this is why the
transport never retries a write on its own either.)

**Idempotency is structural.** ``external_post_id`` is unique, so the same network post can
never be recorded on two jobs. A collision is read as "already recorded" -- success -- rather
than an error. Frappe raises ``UniqueValidationError`` for a non-primary unique index, not
``DuplicateEntryError``, and the store catches both: catching only one fails open.

**Every attempt stays on the row** (TASK-2026-01486). Each outcome -- published, retried, held,
failed, unconfirmed, not sent, and a person's resolve or cancel -- adds one entry to the job's
attempt log, in the same write as the state change, so the log cannot disagree with the state.
``last_error`` still says only the latest.

The functions above the line are **pure** (the bench-free CI tier tests them). The operations
below take a *store* -- ``sweeper.FrappeStore`` in production, an in-memory one in tests -- so the
state machine is tested end to end without a database.
"""

import datetime

# ---------------------------------------------------------------- states

PENDING = "Pending"
IN_PROGRESS = "In Progress"
PUBLISHED = "Published"
FAILED = "Failed"
UNCONFIRMED = "Unconfirmed"
CANCELED = "Canceled"
JOB_STATES = (PENDING, IN_PROGRESS, PUBLISHED, FAILED, UNCONFIRMED, CANCELED)
#: States a job is finished in; nothing touches it again without a person.
FINAL = frozenset({PUBLISHED, FAILED, CANCELED})

#: Social Post statuses.
POST_DRAFT = "Draft"
POST_PENDING_APPROVAL = "Pending Approval"
POST_APPROVED = "Approved"
POST_SCHEDULED = "Scheduled"
POST_PUBLISHING = "Publishing"
POST_PUBLISHED = "Published"
POST_PARTIAL = "Partially Published"
POST_FAILED = "Failed"
POST_CANCELED = "Canceled"
#: Content may still change in these. Anything later is locked to what was approved.
EDITABLE_POST_STATUSES = frozenset({POST_DRAFT, POST_PENDING_APPROVAL})

#: What a failed attempt means for the job.
RETRY = "retry"  # the network definitely did not act; try again later
HOLD = "hold"  # the credential was refused; wait for a reconnect, costs no attempt
REJECTED = "rejected"  # the network refused this post; retrying will not change that
AMBIGUOUS = "ambiguous"  # it may have been published; a person decides

#: Minutes to wait before attempt n+1, by attempts made so far.
BACKOFF_MINUTES = (1, 5, 15, 60, 240)
MAX_ATTEMPTS = 5
#: How long a claimed job is held. Longer than the dispatch job's timeout, so a lease never
#: runs out under a request that is still going.
LEASE_MINUTES = 30
DISPATCH_TIMEOUT_SECONDS = 20 * 60
#: A held job (credential refused) is looked at again after this long at the earliest.
HOLD_MINUTES = 15
#: Jobs claimed per sweep.
SWEEP_BATCH = 20

#: Usage rights a Marketing Media Asset must have to be published.
CLEARED_FOR_SOCIAL = "Cleared for social"

#: What a job's attempt log (``Social Publish Attempt.outcome``, TASK-2026-01486) says happened.
#: One row per attempt and per person's answer, so the row keeps its history instead of only
#: the last error.
LOG_PUBLISHED = "Published"
LOG_RETRY = "Will retry"
LOG_HELD = "Held"
LOG_FAILED = "Failed"
LOG_UNCONFIRMED = "Unconfirmed"
LOG_NOT_SENT = "Not sent"
LOG_RESOLVED = "Resolved"
LOG_CANCELED = "Canceled"
LOG_OUTCOMES = (
	LOG_PUBLISHED,
	LOG_RETRY,
	LOG_HELD,
	LOG_FAILED,
	LOG_UNCONFIRMED,
	LOG_NOT_SENT,
	LOG_RESOLVED,
	LOG_CANCELED,
)


class DuplicatePostId(Exception):
	"""The network post ID is already recorded on another job (the unique index said so)."""


# ---------------------------------------------------------------- pure


def classify_failure(status, dispatched, refused_by_allowlist=False):
	"""What a failed attempt means. Pure.

	``status`` is the HTTP status (``None`` for a transport error such as a timeout) and
	``dispatched`` whether the request may have left ERPNext.
	"""
	if refused_by_allowlist:
		return REJECTED  # a bug: the request was never sent, and resending would be refused again
	if status == 401:
		return HOLD  # the credential was refused: the platform did nothing, a person reconnects
	if status is not None and 400 <= status < 500 and status not in (408, 429):
		# Refused, before or after dispatch: an Instagram container Meta would not build is not
		# built on the fifth try either, and each try leaves an orphan behind.
		return REJECTED
	if not dispatched:
		return RETRY  # nothing left the process, so trying again cannot duplicate anything
	if status in (408, 429):
		return RETRY  # timed out waiting for us / throttled: not processed
	if status is None or status >= 500:
		return AMBIGUOUS  # may have published
	return REJECTED  # any other 4xx: the network refused this post


def can_send(network, settings, connection_status, instagram_linked, account_enabled, has_publisher):
	"""Whether a job for ``network`` may be sent right now. Pure.

	Asked at claim time and again at dispatch time, so switching a network off, disconnecting
	it, disabling the account or having no publisher installed stops even an approved,
	scheduled post -- it waits, Pending, untouched.
	"""
	from erpnext_enhancements.marketing.publish import constants as P
	from erpnext_enhancements.marketing.publish import gate

	if not gate.network_enabled(network, settings):
		return False
	if not has_publisher or not gate._on(account_enabled):
		return False
	if connection_status != "Connected":
		return False
	if network == P.NETWORK_INSTAGRAM and not instagram_linked:
		return False
	return True


def retry_at(now, attempts):
	"""When to try again after ``attempts`` attempts. Pure."""
	index = min(max(attempts, 1), len(BACKOFF_MINUTES)) - 1
	return now + datetime.timedelta(minutes=BACKOFF_MINUTES[index])


def after_lease_expiry(job):
	"""Where an In Progress job whose lease ran out goes. Pure.

	Back to Pending only if its request never left ERPNext; otherwise it may be live.
	"""
	return UNCONFIRMED if job.get("dispatched_at") else PENDING


def lease_expired(job, now):
	expires = job.get("lease_expires_at")
	return job.get("state") == IN_PROGRESS and (expires is None or expires <= now)


def log_entry(at, job, outcome, message=None, status=None, sent=False, user=None):
	"""One row for a job's attempt log. Pure.

	``sent`` is whether the request may have left ERPNext; ``user`` is set only when a person,
	not the sweep, decided the outcome.
	"""
	return {
		"at": at,
		"attempt": int(job.get("attempts") or 0),
		"outcome": outcome,
		"http_status": status,
		"sent": 1 if sent else 0,
		"by_user": user,
		"message": (message or "")[:1000] or None,
	}


def post_status(job_states):
	"""A Social Post's status from its jobs' states. Pure.

	Unconfirmed counts as unfinished business, not as success: somebody still has to look.
	"""
	states = list(job_states)
	if not states:
		return POST_APPROVED
	if all(s == CANCELED for s in states):
		return POST_CANCELED
	live = [s for s in states if s != CANCELED]
	if all(s == PUBLISHED for s in live):
		return POST_PUBLISHED
	if any(s in (PENDING,) for s in live) and not any(s in (IN_PROGRESS, PUBLISHED) for s in live):
		return POST_SCHEDULED
	if any(s in (PENDING, IN_PROGRESS) for s in live):
		return POST_PUBLISHING
	if any(s == PUBLISHED for s in live):
		return POST_PARTIAL
	return POST_FAILED


def _text(value):
	return (value or "").strip()


def content_signature(post, targets, media):
	"""Everything approval vouches for, as one comparable value. Pure.

	Status fields and results are left out on purpose: the outbox writes those after approval.
	"""
	return (
		_text(post.get("body")),
		_text(post.get("link")),
		# The link preview LinkedIn shows is part of what goes out (v1.512.0).
		_text(post.get("link_title")),
		_text(post.get("link_description")),
		# So is everything YouTube shows (v1.513.0).
		_text(post.get("video_title")),
		_text(post.get("video_tags")),
		_text(post.get("video_thumbnail")),
		_text(post.get("youtube_playlist_id")),
		str(post.get("scheduled_at") or ""),
		tuple(_text(m.get("asset")) for m in media),
		tuple(
			sorted(
				(_text(t.get("social_account")), _text(t.get("variant_text")), _text(t.get("first_comment")))
				for t in targets
			)
		),
	)


def enqueue_problems(post, targets, media, accounts):
	"""Why a post may not be queued, as a list of sentences; empty means it may. Pure.

	``accounts`` maps Social Account name to a dict with at least ``enabled``.
	"""
	problems = []
	if post.get("status") != POST_APPROVED:
		problems.append(f"it is {post.get('status') or 'unsaved'}, not Approved")
	if not post.get("approver") or not post.get("approved_at"):
		problems.append("it has no recorded approver")
	elif post.get("approver") == post.get("owner"):
		problems.append("it was approved by the person who wrote it")
	return problems + content_problems(post, targets, media, accounts)


def content_problems(post, targets, media, accounts):
	"""What is wrong with the post itself, whoever approved it. Pure.

	Checked when it is submitted for approval (TASK-2026-01486), so an approver is never asked to
	approve something the outbox would refuse, and again when it is queued.
	"""
	problems = []
	if not targets:
		problems.append("it has no accounts to go to")
	seen = set()
	for t in targets:
		name = t.get("social_account")
		if name in seen:
			problems.append(f"{name} is listed twice")
		seen.add(name)
		account = accounts.get(name)
		if account is None:
			problems.append(f"{name} does not exist")
		elif not int(account.get("enabled") or 0):
			problems.append(f"{name} is switched off")
	for m in media:
		if (m.get("usage_rights") or "") != CLEARED_FOR_SOCIAL:
			problems.append(
				f"{m.get('asset')} is not cleared for social ({m.get('usage_rights') or 'no rights set'})"
			)
	if not _text(post.get("body")) and not media:
		problems.append("it has neither text nor media")
	# What each network would refuse (TASK-2026-01483): caught here as well as on the form, so a
	# post that slipped past the form still cannot reach the network.
	from erpnext_enhancements.marketing.publish import validation

	networks = {name: (account or {}).get("network") for name, account in accounts.items()}
	problems.extend(validation.post_problems(post, targets, media, networks))
	return problems


# ---------------------------------------------------------------- operations over a store


def enqueue(store, post_name, now):
	"""Write one Pending job per target of an approved post. Returns the new job names.

	Called by the approve action (TASK-2026-01486), never by the author. Safe to call twice:
	a target that already has a live job gets no second one.
	"""
	post, targets, media = store.load_post(post_name)
	accounts = store.accounts([t.get("social_account") for t in targets])
	problems = enqueue_problems(post, targets, media, accounts)
	if problems:
		raise ValueError(f"{post_name} cannot be queued: " + "; ".join(problems))
	available_at = max(post.get("scheduled_at") or now, now)
	existing = {j["target"] for j in store.jobs_for_post(post_name) if j["state"] not in (FAILED, CANCELED)}
	created = []
	for t in targets:
		if t["name"] in existing:
			continue
		created.append(
			store.create_job(
				{
					"social_post": post_name,
					"target": t["name"],
					"social_account": t["social_account"],
					"network": accounts[t["social_account"]].get("network"),
					"state": PENDING,
					"available_at": available_at,
					"attempts": 0,
				}
			)
		)
		store.update_target(post_name, t["name"], {"status": "Queued"})
	store.set_post_status(post_name, post_status(j["state"] for j in store.jobs_for_post(post_name)))
	return created


def reclaim_expired(store, now):
	"""Lease-expired In Progress jobs: back to Pending if never sent, else Unconfirmed."""
	moved = {PENDING: [], UNCONFIRMED: []}
	for job in store.expired_leases(now):
		target = after_lease_expiry(job)
		if target == PENDING:
			store.update_job(
				job["name"],
				{"state": PENDING, "lease_id": None, "lease_expires_at": None, "available_at": now},
				log=log_entry(now, job, LOG_NOT_SENT, "The lease ran out before anything was sent."),
			)
		else:
			message = "The worker stopped after the request was sent; the post may be live."
			_finish(
				store,
				job,
				UNCONFIRMED,
				{"last_error": message},
				log=log_entry(now, job, LOG_UNCONFIRMED, message, sent=True),
			)
			store.notify(job, "may already be published: check the network, then resolve it")
		moved[target].append(job["name"])
	return moved


def claim_due(store, now, sendable, lease_id_for, limit=SWEEP_BATCH, admit=None):
	"""Claim due Pending jobs whose network may send right now. Returns ``[(job, lease_id)]``.

	A job whose network is switched off, disconnected or has no publisher is left Pending and
	untouched: that is how a switch stops even approved, scheduled posts.

	``admit(job)`` is the rate limiter (``ratelimit.admit``, TASK-2026-01482), asked only once a
	job is otherwise sendable because a yes spends quota. It returns ``(ok, retry_after_seconds,
	note, quota_remaining)``. A no leaves the job Pending with ``available_at`` moved to when
	quota returns, so the sweep does not ask again every five minutes; the remaining quota is
	written to the account for the calendar to show.
	"""
	claimed = []
	lease_until = now + datetime.timedelta(minutes=LEASE_MINUTES)
	for job in store.due_jobs(now, limit):
		if not sendable(job):
			continue
		if admit is not None:
			ok, retry_after, note, remaining = admit(job)
			if remaining is not None:
				store.set_quota(job["social_account"], remaining, now)
			if not ok:
				store.defer(job["name"], now + datetime.timedelta(seconds=max(retry_after, 60)), note)
				continue
		lease_id = lease_id_for(job)
		if store.claim(job["name"], lease_id, now, lease_until):
			claimed.append((job["name"], lease_id))
	return claimed


def dispatch(store, job_name, lease_id, now_fn, sendable, prepare, notify_auth_failure=None):
	"""Publish one claimed job. The background job ``sweeper.run_dispatch`` calls this.

	``prepare(job)`` does everything that sends nothing public -- load the post, get a token --
	and returns ``send()``, which publishes and returns ``{"external_post_id", "permalink"}``.
	The split is what makes ``dispatched_at`` honest: a token server that is down fails
	*before* it is set, so the job is retried rather than wrongly held as possibly-live.
	Either raises ``MarketingAPIError`` (``status`` set) on a network failure. Returns the
	job's new state.
	"""
	from erpnext_enhancements.marketing.core.client import MarketingAPIError
	from erpnext_enhancements.marketing.publish.client import NotPublished, PublishViolation

	job = store.get_job(job_name)
	if not job or job.get("state") != IN_PROGRESS or job.get("lease_id") != lease_id:
		return job.get("state") if job else None  # someone else owns it now
	if not sendable(job):
		# Switched off between the claim and now: give the attempt back, leave it Pending.
		store.update_job(
			job_name,
			{
				"state": PENDING,
				"lease_id": None,
				"lease_expires_at": None,
				"attempts": max(int(job.get("attempts") or 1) - 1, 0),
			},
			log=log_entry(now_fn(), job, LOG_NOT_SENT, "Switched off before it was sent."),
		)
		return PENDING

	try:
		send = prepare(job)
	except MarketingAPIError as exc:
		kind = classify_failure(
			exc.status, dispatched=False, refused_by_allowlist=isinstance(exc, PublishViolation)
		)
		return _record_failure(store, job, kind, str(exc), now_fn(), notify_auth_failure, exc.status)
	except Exception as exc:
		# A bug before anything was sent. Counted as an attempt so it ends in Failed after
		# MAX_ATTEMPTS, instead of escaping and being reclaimed every lease, forever.
		return _record_failure(store, job, RETRY, f"{type(exc).__name__} while preparing", now_fn(), None)

	store.mark_dispatched(job_name, now_fn())  # durable before the request leaves
	try:
		result = send()
	except NotPublished as exc:
		# The publisher checked with the network after an ambiguous failure and it is NOT live
		# (an Instagram container still FINISHED, not PUBLISHED): safe to try again.
		return _record_failure(store, job, RETRY, str(exc), now_fn(), notify_auth_failure, exc.status, True)
	except MarketingAPIError as exc:
		kind = classify_failure(
			exc.status, dispatched=True, refused_by_allowlist=isinstance(exc, PublishViolation)
		)
		return _record_failure(store, job, kind, str(exc), now_fn(), notify_auth_failure, exc.status, True)
	except Exception as exc:
		# A bug in a publisher, after the request may have gone out: treat like a timeout.
		return _record_failure(
			store, job, AMBIGUOUS, f"{type(exc).__name__} in the publisher", now_fn(), None, sent=True
		)
	return record_success(store, job, result or {}, now_fn())


def record_success(store, job, result, now):
	values = {
		"external_post_id": result.get("external_post_id") or None,
		"permalink": result.get("permalink") or None,
		"published_at": now,
		# The post is live; a follow-up that failed (first comment, permalink) is said here,
		# never raised -- raising after the public step would turn success into Unconfirmed.
		"last_error": (result.get("warning") or "")[:1000] or None,
	}
	message = " ".join(filter(None, [result.get("permalink"), result.get("warning")]))
	try:
		_finish(store, job, PUBLISHED, values, log=log_entry(now, job, LOG_PUBLISHED, message, sent=True))
	except DuplicatePostId:
		# Already recorded on another job: the post exists, which is what success means.
		values.pop("external_post_id")
		values["last_error"] = f"Post {result.get('external_post_id')} was already recorded on another job."
		_finish(
			store,
			job,
			PUBLISHED,
			values,
			log=log_entry(now, job, LOG_PUBLISHED, values["last_error"], sent=True),
		)
	return PUBLISHED


def _record_failure(store, job, kind, message, now, notify_auth_failure, status=None, sent=False):
	attempts = int(job.get("attempts") or 1)
	if kind == HOLD:
		if notify_auth_failure:
			notify_auth_failure(job, message)
		store.update_job(
			job["name"],
			{
				"state": PENDING,
				"lease_id": None,
				"lease_expires_at": None,
				"dispatched_at": None,
				"attempts": max(attempts - 1, 0),
				"available_at": now + datetime.timedelta(minutes=HOLD_MINUTES),
				"last_error": message[:1000],
			},
			log=log_entry(now, job, LOG_HELD, message, status, sent),
		)
		return PENDING
	if kind == RETRY and attempts < MAX_ATTEMPTS:
		store.update_job(
			job["name"],
			{
				"state": PENDING,
				"lease_id": None,
				"lease_expires_at": None,
				"dispatched_at": None,
				"available_at": retry_at(now, attempts),
				"last_error": message[:1000],
			},
			log=log_entry(now, job, LOG_RETRY, message, status, sent),
		)
		return PENDING
	state = UNCONFIRMED if kind == AMBIGUOUS else FAILED
	if kind == RETRY:
		message = f"Gave up after {attempts} attempts: {message}"
	_finish(
		store,
		job,
		state,
		{"last_error": message[:1000]},
		log=log_entry(
			now, job, LOG_UNCONFIRMED if state == UNCONFIRMED else LOG_FAILED, message, status, sent
		),
	)
	store.notify(
		job,
		"may already be published: check the network, then resolve it"
		if state == UNCONFIRMED
		else f"failed: {message[:200]}",
	)
	return state


def _finish(store, job, state, values, log=None):
	"""Put a job in ``state`` and roll the result up to its target row and post."""
	store.update_job(
		job["name"], {"state": state, "lease_id": None, "lease_expires_at": None, **values}, log=log
	)
	target_values = {"status": state}
	for key in ("external_post_id", "permalink", "published_at"):
		if values.get(key):
			target_values[key] = values[key]
	store.update_target(job["social_post"], job["target"], target_values)
	store.set_post_status(
		job["social_post"], post_status(j["state"] for j in store.jobs_for_post(job["social_post"]))
	)


def resolve(store, job_name, outcome, user, now, external_post_id=None, permalink=None):
	"""A person's answer for an Unconfirmed (or Failed) job: ``published``, ``retry`` or ``cancel``."""
	job = store.get_job(job_name)
	if not job:
		raise ValueError(f"{job_name} does not exist")
	allowed = {
		"published": (UNCONFIRMED,),
		"retry": (UNCONFIRMED, FAILED),
		"cancel": (UNCONFIRMED, FAILED, PENDING),
	}
	if outcome not in allowed:
		raise ValueError(f"unknown outcome {outcome!r}")
	if job["state"] not in allowed[outcome]:
		raise ValueError(
			f"{job_name} is {job['state']}; '{outcome}' applies to {', '.join(allowed[outcome])}"
		)
	if outcome == "published":
		values = {
			"external_post_id": (external_post_id or "").strip() or None,
			"permalink": (permalink or "").strip() or None,
			"published_at": now,
			"resolved_by": user,
		}
		note = " ".join(
			filter(None, ["Recorded as published.", values["permalink"], values["external_post_id"]])
		)
		try:
			_finish(store, job, PUBLISHED, values, log=log_entry(now, job, LOG_RESOLVED, note, user=user))
		except DuplicatePostId:
			raise ValueError(f"post {external_post_id} is already recorded on another job") from None
		return PUBLISHED
	if outcome == "retry":
		store.update_job(
			job_name,
			{
				"state": PENDING,
				"attempts": 0,
				"available_at": now,
				"dispatched_at": None,
				"lease_id": None,
				"lease_expires_at": None,
				"resolved_by": user,
			},
			log=log_entry(now, job, LOG_RESOLVED, "Sent again.", user=user),
		)
		store.update_target(job["social_post"], job["target"], {"status": "Queued"})
		store.set_post_status(
			job["social_post"], post_status(j["state"] for j in store.jobs_for_post(job["social_post"]))
		)
		return PENDING
	_finish(
		store,
		job,
		CANCELED,
		{"resolved_by": user},
		log=log_entry(now, job, LOG_CANCELED, "Canceled.", user=user),
	)
	return CANCELED


def cancel_post(store, post_name, user, now):
	"""Stop a post: every Pending job is canceled. Returns the post's new status (TASK-2026-01486).

	Canceling stops what has not gone out; it never takes anything down. So it is refused while
	a job is In Progress -- that request may be leaving right now, and nothing here can call it
	back -- and it leaves Published jobs published and Unconfirmed or Failed ones for a person
	to resolve. A post with no jobs yet (a draft, or one waiting for approval) is simply Canceled.
	"""
	jobs = store.jobs_for_post(post_name)
	if any(j["state"] == IN_PROGRESS for j in jobs):
		raise ValueError(
			f"{post_name} is being published right now; wait for that to finish, then cancel what is left"
		)
	if not jobs:
		store.set_post_status(post_name, POST_CANCELED)
		return POST_CANCELED
	for row in jobs:
		if row["state"] != PENDING:
			continue
		job = store.get_job(row["name"])
		_finish(
			store,
			job,
			CANCELED,
			{"resolved_by": user},
			log=log_entry(now, job, LOG_CANCELED, "The post was canceled.", user=user),
		)
	return post_status(j["state"] for j in store.jobs_for_post(post_name))
