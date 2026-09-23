"""Bench-free test: the publishing outbox (TASK-2026-01481, Marketing P2).

The outbox is the one piece of this module that decides whether something goes out in public,
so these pin its promises:

1. **A public post never goes out twice.** A job that may have sent (``dispatched_at`` set)
   never returns to Pending by itself: a lease running out, a 5xx or a timeout makes it
   Unconfirmed, and a person says which it was. Only a job that provably sent nothing retries.
2. **A scheduled post survives a deploy.** It waits in the table on ``available_at``; nothing
   depends on a queued RQ job surviving ``FLUSHDB``.
3. **Switches stop even approved posts,** at claim time and again at dispatch time.
4. **What goes out is what was approved,** with media cleared for social use and a second
   person as approver.
5. **Idempotency is structural:** the same network post is never recorded twice, and a
   collision reads as success.
6. **Every attempt stays on the row** (TASK-2026-01486): each outcome, and each person's answer,
   is one attempt-log entry, written in the same update as the state it records. Canceling a
   post stops what has not gone out and never touches what has.

The state machine runs against an in-memory store, so no frappe is needed. The sweeper's
frappe wiring is checked by AST: its whitelist, and the ``frappe.enqueue`` call, whose
``job_name`` parameter is Frappe's own and would silently swallow ours.

Run: python -m unittest erpnext_enhancements.tests.test_marketing_outbox
"""

import ast
import datetime
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.marketing.core.client import MarketingAPIError
from erpnext_enhancements.marketing.publish import constants as P
from erpnext_enhancements.marketing.publish import outbox as O
from erpnext_enhancements.marketing.publish.client import PublishViolation

APP = REPO_ROOT / "erpnext_enhancements"
DOCTYPES = APP / "marketing" / "doctype"
SWEEPER = APP / "marketing" / "publish" / "sweeper.py"
T0 = datetime.datetime(2026, 9, 22, 9, 0)


def schema(name):
	return json.loads((DOCTYPES / name / f"{name}.json").read_text(encoding="utf-8"))


def fields(name):
	return {f["fieldname"]: f for f in schema(name)["fields"]}


# ---------------------------------------------------------------- the in-memory store


class MemoryStore:
	def __init__(self, post=None, targets=None, media=None, accounts=None):
		self.post = {
			"name": "SPOST-00001",
			"status": O.POST_APPROVED,
			"owner": "writer@example.com",
			"approver": "boss@example.com",
			"approved_at": T0,
			"body": "A fountain",
			"scheduled_at": None,
			**(post or {}),
		}
		self.targets = (
			targets
			if targets is not None
			else [
				{"name": "row-fb", "social_account": "SACC-Facebook-101"},
				{"name": "row-li", "social_account": "SACC-LinkedIn-9"},
			]
		)
		self.media = media if media is not None else []
		self.account_rows = accounts or {
			"SACC-Facebook-101": {"enabled": 1, "network": "Facebook"},
			"SACC-LinkedIn-9": {"enabled": 1, "network": "LinkedIn"},
		}
		self.jobs = {}
		self.target_values = {}
		self.post_statuses = []
		self.notes = []
		self.seq = 0
		self.dispatched = []
		self.logs = {}

	def load_post(self, name):
		return dict(self.post), [dict(t) for t in self.targets], [dict(m) for m in self.media]

	def accounts(self, names):
		return {n: self.account_rows[n] for n in names if n in self.account_rows}

	def jobs_for_post(self, post):
		return [dict(j) for j in self.jobs.values() if j["social_post"] == post]

	def create_job(self, values):
		self.seq += 1
		name = f"SPJ-{self.seq:07d}"
		self.jobs[name] = {
			"name": name,
			"lease_id": None,
			"lease_expires_at": None,
			"dispatched_at": None,
			**values,
		}
		return name

	def update_target(self, post, row, values):
		self.target_values.setdefault(row, {}).update(values)

	def set_post_status(self, post, status):
		self.post_statuses.append(status)

	def expired_leases(self, now):
		return [dict(j) for j in self.jobs.values() if O.lease_expired(j, now)]

	def due_jobs(self, now, limit):
		due = [j for j in self.jobs.values() if j["state"] == O.PENDING and j["available_at"] <= now]
		return [dict(j) for j in sorted(due, key=lambda j: j["available_at"])[:limit]]

	def claim(self, name, lease_id, now, lease_until):
		job = self.jobs[name]
		if job["state"] != O.PENDING or job["available_at"] > now:
			return False
		job.update(state=O.IN_PROGRESS, lease_id=lease_id, lease_expires_at=lease_until, dispatched_at=None)
		job["attempts"] = job.get("attempts", 0) + 1
		return True

	def get_job(self, name):
		return dict(self.jobs[name]) if name in self.jobs else None

	def update_job(self, name, values, log=None):
		post_id = values.get("external_post_id")
		if post_id and any(j.get("external_post_id") == post_id for n, j in self.jobs.items() if n != name):
			raise O.DuplicatePostId(post_id)  # like the savepoint: neither the state nor the log lands
		self.jobs[name].update(values)
		if log:
			self.logs.setdefault(name, []).append(log)

	def mark_dispatched(self, name, when):
		self.jobs[name]["dispatched_at"] = when
		self.dispatched.append(name)

	def defer(self, name, until, note):
		self.jobs[name].update(available_at=until, last_error=note)

	def set_quota(self, account, remaining, when):
		self.quotas = getattr(self, "quotas", {})
		self.quotas[account] = remaining

	def notify(self, job, message):
		self.notes.append((job["name"], message))


def always(_job):
	return True


def claimed_job(store=None, **job):
	"""A store holding one job already claimed (In Progress, lease L1)."""
	store = store or MemoryStore()
	name = store.create_job(
		{
			"social_post": "SPOST-00001",
			"target": "row-fb",
			"social_account": "SACC-Facebook-101",
			"network": "Facebook",
			"state": O.PENDING,
			"available_at": T0,
			"attempts": 0,
			**job,
		}
	)
	assert store.claim(name, "L1", T0, T0 + datetime.timedelta(minutes=30))
	return store, name


def dispatch(store, name, send=None, prepare=None, sendable=always, notify_auth=None):
	prepare = prepare or (lambda job: send)
	return O.dispatch(store, name, "L1", lambda: T0, sendable, prepare, notify_auth)


def outcomes(store, name):
	return [entry["outcome"] for entry in store.logs.get(name, [])]


def fails(status, cls=MarketingAPIError):
	def send():
		raise cls("Facebook", "boom", status=status)

	return send


# ---------------------------------------------------------------- pure


class ClassifyTests(unittest.TestCase):
	def test_the_table(self):
		cases = [
			(401, True, O.HOLD),
			(401, False, O.HOLD),
			(429, True, O.RETRY),
			(408, True, O.RETRY),
			(500, True, O.AMBIGUOUS),
			(502, True, O.AMBIGUOUS),
			(None, True, O.AMBIGUOUS),
			(400, True, O.REJECTED),
			(403, True, O.REJECTED),
			(500, False, O.RETRY),
			(None, False, O.RETRY),
			# A refusal while preparing is a refusal: an Instagram container Meta would not build
			# is not built on the fifth try either (v1.511.0).
			(400, False, O.REJECTED),
			(429, False, O.RETRY),
		]
		for status, dispatched, expected in cases:
			self.assertEqual(O.classify_failure(status, dispatched), expected, (status, dispatched))
		self.assertEqual(O.classify_failure(None, False, refused_by_allowlist=True), O.REJECTED)

	def test_backoff_grows_and_caps(self):
		self.assertEqual(O.retry_at(T0, 1) - T0, datetime.timedelta(minutes=1))
		self.assertEqual(O.retry_at(T0, 3) - T0, datetime.timedelta(minutes=15))
		self.assertEqual(O.retry_at(T0, 99) - T0, datetime.timedelta(minutes=O.BACKOFF_MINUTES[-1]))

	def test_lease_expiry_never_resends_what_was_dispatched(self):
		self.assertEqual(O.after_lease_expiry({"dispatched_at": None}), O.PENDING)
		self.assertEqual(O.after_lease_expiry({"dispatched_at": T0}), O.UNCONFIRMED)
		self.assertTrue(O.lease_expired({"state": O.IN_PROGRESS, "lease_expires_at": T0}, T0))
		self.assertFalse(O.lease_expired({"state": O.PENDING, "lease_expires_at": T0}, T0))

	def test_post_status_rollup(self):
		cases = [
			([O.PENDING, O.PENDING], O.POST_SCHEDULED),
			([O.IN_PROGRESS, O.PENDING], O.POST_PUBLISHING),
			([O.PUBLISHED, O.PENDING], O.POST_PUBLISHING),
			([O.PUBLISHED, O.PUBLISHED], O.POST_PUBLISHED),
			([O.PUBLISHED, O.CANCELED], O.POST_PUBLISHED),
			([O.PUBLISHED, O.FAILED], O.POST_PARTIAL),
			([O.PUBLISHED, O.UNCONFIRMED], O.POST_PARTIAL),
			([O.FAILED, O.UNCONFIRMED], O.POST_FAILED),
			([O.CANCELED], O.POST_CANCELED),
		]
		for states, expected in cases:
			self.assertEqual(O.post_status(states), expected, states)

	def test_every_rolled_up_status_is_a_social_post_option(self):
		options = set(fields("social_post")["status"]["options"].split("\n"))
		for status in (
			O.POST_DRAFT,
			O.POST_PENDING_APPROVAL,
			O.POST_APPROVED,
			O.POST_SCHEDULED,
			O.POST_PUBLISHING,
			O.POST_PUBLISHED,
			O.POST_PARTIAL,
			O.POST_FAILED,
			O.POST_CANCELED,
		):
			self.assertIn(status, options)

	def test_can_send(self):
		on = {"enabled": 1, "facebook_publishing_enabled": 1, "instagram_publishing_enabled": 1}
		self.assertTrue(O.can_send("Facebook", on, "Connected", False, 1, True))
		self.assertFalse(O.can_send("Facebook", {**on, "enabled": 0}, "Connected", False, 1, True))
		self.assertFalse(
			O.can_send("Facebook", {**on, "facebook_publishing_enabled": 0}, "Connected", False, 1, True)
		)
		self.assertFalse(O.can_send("Facebook", on, "Auth Failed", False, 1, True), "reconnect first")
		self.assertFalse(O.can_send("Facebook", on, "Connected", False, 0, True), "account switched off")
		self.assertFalse(
			O.can_send("Facebook", on, "Connected", False, 1, False), "no publisher installed yet"
		)
		self.assertFalse(O.can_send("Instagram", on, "Connected", False, 1, True), "no linked IG account")
		self.assertTrue(O.can_send("Instagram", on, "Connected", True, 1, True))


class ApprovalTests(unittest.TestCase):
	def test_signature_covers_what_approval_vouches_for(self):
		post = {"body": "Hi", "link": "https://x", "scheduled_at": T0}
		targets = [{"social_account": "A", "variant_text": "", "first_comment": ""}]
		base = O.content_signature(post, targets, [{"asset": "MMA-1"}])
		self.assertNotEqual(base, O.content_signature({**post, "body": "Hi!"}, targets, [{"asset": "MMA-1"}]))
		self.assertNotEqual(
			base, O.content_signature(post, [{**targets[0], "variant_text": "x"}], [{"asset": "MMA-1"}])
		)
		self.assertNotEqual(
			base,
			O.content_signature(
				{**post, "scheduled_at": T0 + datetime.timedelta(hours=1)}, targets, [{"asset": "MMA-1"}]
			),
		)
		self.assertNotEqual(
			O.content_signature(post, targets, [{"asset": "MMA-1"}, {"asset": "MMA-2"}]),
			O.content_signature(post, targets, [{"asset": "MMA-2"}, {"asset": "MMA-1"}]),
			"carousel order is content",
		)
		self.assertEqual(
			base,
			O.content_signature(
				{**post, "status": "Published"}, [{**targets[0], "status": "Published"}], [{"asset": "MMA-1"}]
			),
			"what the outbox writes is not content",
		)

	def test_enqueue_problems(self):
		ok_accounts = {"A": {"enabled": 1}}
		post = {"status": O.POST_APPROVED, "approver": "b", "approved_at": T0, "owner": "w", "body": "x"}
		self.assertEqual(O.enqueue_problems(post, [{"social_account": "A"}], [], ok_accounts), [])
		cases = [
			({**post, "status": O.POST_DRAFT}, [{"social_account": "A"}], [], ok_accounts, "not Approved"),
			({**post, "approver": None}, [{"social_account": "A"}], [], ok_accounts, "no recorded approver"),
			({**post, "approver": "w"}, [{"social_account": "A"}], [], ok_accounts, "person who wrote it"),
			(post, [], [], ok_accounts, "no accounts"),
			(post, [{"social_account": "A"}, {"social_account": "A"}], [], ok_accounts, "listed twice"),
			(post, [{"social_account": "A"}], [], {"A": {"enabled": 0}}, "switched off"),
			(post, [{"social_account": "Z"}], [], ok_accounts, "does not exist"),
			(
				post,
				[{"social_account": "A"}],
				[{"asset": "M", "usage_rights": "Needs client approval"}],
				ok_accounts,
				"not cleared",
			),
			({**post, "body": ""}, [{"social_account": "A"}], [], ok_accounts, "neither text nor media"),
		]
		for p, targets, media, accounts, needle in cases:
			problems = O.enqueue_problems(p, targets, media, accounts)
			self.assertTrue(any(needle in x for x in problems), (needle, problems))


# ---------------------------------------------------------------- the state machine


class EnqueueTests(unittest.TestCase):
	def test_one_pending_job_per_target_and_idempotent(self):
		store = MemoryStore()
		created = O.enqueue(store, "SPOST-00001", T0)
		self.assertEqual(len(created), 2)
		self.assertEqual({j["state"] for j in store.jobs.values()}, {O.PENDING})
		self.assertEqual(store.target_values["row-fb"]["status"], "Queued")
		self.assertEqual(store.post_statuses[-1], O.POST_SCHEDULED)
		self.assertEqual(O.enqueue(store, "SPOST-00001", T0), [], "a second call writes nothing")

	def test_a_scheduled_post_waits_in_the_table(self):
		later = T0 + datetime.timedelta(days=1)
		store = MemoryStore(post={"scheduled_at": later})
		O.enqueue(store, "SPOST-00001", T0)
		self.assertEqual({j["available_at"] for j in store.jobs.values()}, {later})
		self.assertEqual(O.claim_due(store, T0, always, lambda j: "L"), [], "not due yet")
		self.assertEqual(len(O.claim_due(store, later, always, lambda j: "L")), 2)

	def test_refuses_what_was_not_properly_approved(self):
		store = MemoryStore(media=[{"asset": "MMA-1", "usage_rights": "Needs client approval"}])
		with self.assertRaises(ValueError):
			O.enqueue(store, "SPOST-00001", T0)
		self.assertEqual(store.jobs, {})


class SweepTests(unittest.TestCase):
	def test_switched_off_networks_are_left_pending_and_untouched(self):
		store = MemoryStore()
		O.enqueue(store, "SPOST-00001", T0)
		claimed = O.claim_due(store, T0, lambda job: job["network"] == "LinkedIn", lambda j: "L")
		self.assertEqual(len(claimed), 1)
		fb = next(j for j in store.jobs.values() if j["network"] == "Facebook")
		self.assertEqual((fb["state"], fb["attempts"]), (O.PENDING, 0))

	def test_the_rate_limiter_defers_instead_of_claiming(self):
		# TASK-2026-01482: a refused job stays Pending, moved to when quota returns, so the sweep
		# does not ask again every five minutes; remaining quota lands on the account.
		store = MemoryStore()
		O.enqueue(store, "SPOST-00001", T0)

		def admit(job):
			if job["network"] == "Facebook":
				return False, 3600, "Facebook paused by its rate limit", None
			return True, 0, "", 7

		claimed = O.claim_due(store, T0, always, lambda j: "L", admit=admit)
		self.assertEqual([store.jobs[n]["network"] for n, _ in claimed], ["LinkedIn"])
		fb = next(j for j in store.jobs.values() if j["network"] == "Facebook")
		self.assertEqual((fb["state"], fb["attempts"]), (O.PENDING, 0))
		self.assertEqual(fb["available_at"], T0 + datetime.timedelta(hours=1))
		self.assertIn("rate limit", fb["last_error"])
		self.assertEqual(store.quotas, {"SACC-LinkedIn-9": 7})

	def test_the_limiter_is_asked_only_about_sendable_jobs(self):
		# A yes spends quota, so a job that could not be sent anyway must never be asked about.
		store = MemoryStore()
		O.enqueue(store, "SPOST-00001", T0)
		asked = []
		O.claim_due(
			store,
			T0,
			lambda job: job["network"] == "LinkedIn",
			lambda j: "L",
			admit=lambda job: asked.append(job["network"]) or (True, 0, "", None),
		)
		self.assertEqual(asked, ["LinkedIn"])

	def test_a_claim_cannot_be_won_twice(self):
		store, name = claimed_job()
		self.assertFalse(store.claim(name, "L2", T0, T0))

	def test_expired_lease_never_dispatched_goes_back_to_pending(self):
		store, name = claimed_job()
		moved = O.reclaim_expired(store, T0 + datetime.timedelta(hours=1))
		self.assertEqual(moved[O.PENDING], [name])
		self.assertEqual(store.jobs[name]["state"], O.PENDING)
		self.assertEqual(store.notes, [])

	def test_expired_lease_after_dispatch_is_unconfirmed_never_pending(self):
		store, name = claimed_job()
		store.mark_dispatched(name, T0)
		moved = O.reclaim_expired(store, T0 + datetime.timedelta(hours=1))
		self.assertEqual(moved[O.UNCONFIRMED], [name])
		self.assertEqual(store.jobs[name]["state"], O.UNCONFIRMED)
		self.assertEqual(store.target_values["row-fb"]["status"], O.UNCONFIRMED)
		self.assertTrue(store.notes, "a person is told")


class DispatchTests(unittest.TestCase):
	def test_success_records_the_post_and_rolls_up(self):
		store, name = claimed_job()
		state = dispatch(
			store, name, send=lambda: {"external_post_id": "101_555", "permalink": "https://fb/1"}
		)
		self.assertEqual(state, O.PUBLISHED)
		job = store.jobs[name]
		self.assertEqual((job["external_post_id"], job["lease_id"]), ("101_555", None))
		self.assertEqual(store.target_values["row-fb"]["permalink"], "https://fb/1")
		self.assertEqual(store.post_statuses[-1], O.POST_PUBLISHED)
		self.assertEqual(store.dispatched, [name], "dispatched_at recorded before the send")

	def test_ambiguous_failures_are_unconfirmed(self):
		for send in (fails(502), fails(None), lambda: (_ for _ in ()).throw(RuntimeError("bug"))):
			store, name = claimed_job()
			self.assertEqual(dispatch(store, name, send=send), O.UNCONFIRMED)
			self.assertTrue(store.notes)

	def test_throttled_retries_later_and_keeps_the_attempt(self):
		store, name = claimed_job()
		self.assertEqual(dispatch(store, name, send=fails(429)), O.PENDING)
		job = store.jobs[name]
		self.assertEqual((job["attempts"], job["dispatched_at"]), (1, None))
		self.assertEqual(job["available_at"], O.retry_at(T0, 1))

	def test_retries_run_out(self):
		store, name = claimed_job(attempts=O.MAX_ATTEMPTS - 1)
		self.assertEqual(dispatch(store, name, send=fails(429)), O.FAILED)
		self.assertIn("Gave up", store.jobs[name]["last_error"])

	def test_rejected_fails_at_once(self):
		store, name = claimed_job()
		self.assertEqual(dispatch(store, name, send=fails(400)), O.FAILED)

	def test_refused_credential_holds_and_gives_the_attempt_back(self):
		store, name = claimed_job()
		told = []
		state = dispatch(store, name, send=fails(401), notify_auth=lambda job, msg: told.append(job["name"]))
		self.assertEqual(state, O.PENDING)
		self.assertEqual(store.jobs[name]["attempts"], 0)
		self.assertEqual(told, [name], "the connection is marked for reconnecting")

	def test_a_token_server_outage_while_preparing_is_not_ambiguous(self):
		store, name = claimed_job()

		def prepare(job):
			raise MarketingAPIError("Facebook", "token server down", status=503)

		self.assertEqual(dispatch(store, name, prepare=prepare), O.PENDING)
		self.assertEqual(store.dispatched, [], "nothing was sent, so nothing is held as possibly live")

	def test_a_bug_while_preparing_counts_as_an_attempt(self):
		store, name = claimed_job(attempts=O.MAX_ATTEMPTS - 1)
		self.assertEqual(dispatch(store, name, prepare=lambda job: 1 / 0), O.FAILED)
		self.assertEqual(store.dispatched, [])

	def test_verified_not_published_retries_instead_of_unconfirmed(self):
		# A publisher that checked with the network after an ambiguous failure, and found the post
		# is not live (Instagram's container still FINISHED), may be retried (v1.511.0).
		from erpnext_enhancements.marketing.publish.client import NotPublished

		store, name = claimed_job()
		self.assertEqual(dispatch(store, name, send=fails(None, NotPublished)), O.PENDING)
		self.assertEqual(store.notes, [], "nothing ambiguous to tell anyone about")
		self.assertEqual(store.jobs[name]["dispatched_at"], None)

	def test_a_refusal_while_preparing_fails_without_retrying(self):
		store, name = claimed_job()

		def prepare(job):
			raise MarketingAPIError("Instagram", "container ERROR", status=400)

		self.assertEqual(dispatch(store, name, prepare=prepare), O.FAILED)
		self.assertEqual(store.dispatched, [])

	def test_a_publishers_warning_is_kept_on_a_success(self):
		store, name = claimed_job()
		state = dispatch(store, name, send=lambda: {"external_post_id": "1_2", "warning": "no first comment"})
		self.assertEqual(state, O.PUBLISHED)
		self.assertEqual(store.jobs[name]["last_error"], "no first comment")

	def test_an_allowlist_refusal_is_a_bug_not_a_retry(self):
		store, name = claimed_job()
		self.assertEqual(dispatch(store, name, send=fails(None, PublishViolation)), O.FAILED)

	def test_switched_off_after_the_claim_releases_the_job(self):
		store, name = claimed_job()
		sent = []
		state = dispatch(store, name, send=lambda: sent.append(1), sendable=lambda job: False)
		self.assertEqual((state, sent, store.jobs[name]["attempts"]), (O.PENDING, [], 0))

	def test_a_stale_lease_does_nothing(self):
		store, name = claimed_job()
		sent = []
		self.assertEqual(
			O.dispatch(store, name, "OTHER", lambda: T0, always, lambda job: lambda: sent.append(1)),
			O.IN_PROGRESS,
		)
		self.assertEqual(sent, [])

	def test_a_duplicate_post_id_is_success(self):
		store, first = claimed_job()
		dispatch(store, first, send=lambda: {"external_post_id": "101_555"})
		store, second = claimed_job(store, target="row-li", social_account="SACC-LinkedIn-9")
		state = O.dispatch(
			store, second, "L1", lambda: T0, always, lambda job: lambda: {"external_post_id": "101_555"}
		)
		self.assertEqual(state, O.PUBLISHED)
		self.assertIsNone(store.jobs[second].get("external_post_id"))
		self.assertIn("already recorded", store.jobs[second]["last_error"])


class ResolveTests(unittest.TestCase):
	def unconfirmed(self):
		store, name = claimed_job()
		dispatch(store, name, send=fails(502))
		return store, name

	def test_published_records_who_said_so(self):
		store, name = self.unconfirmed()
		self.assertEqual(
			O.resolve(store, name, "published", "nik@example.com", T0, permalink="https://fb/9"), O.PUBLISHED
		)
		self.assertEqual(store.jobs[name]["resolved_by"], "nik@example.com")

	def test_retry_starts_over(self):
		store, name = self.unconfirmed()
		self.assertEqual(O.resolve(store, name, "retry", "nik@example.com", T0), O.PENDING)
		self.assertEqual((store.jobs[name]["attempts"], store.jobs[name]["dispatched_at"]), (0, None))

	def test_cancel_and_the_transitions_that_are_refused(self):
		store, name = self.unconfirmed()
		self.assertEqual(O.resolve(store, name, "cancel", "nik@example.com", T0), O.CANCELED)
		with self.assertRaises(ValueError):
			O.resolve(store, name, "retry", "nik@example.com", T0)
		store, name = claimed_job()
		with self.assertRaises(ValueError):
			O.resolve(store, name, "published", "nik@example.com", T0)


class AttemptLogTests(unittest.TestCase):
	"""Every publish attempt recorded on the outbox row (TASK-2026-01486)."""

	def test_each_dispatch_outcome_is_one_entry(self):
		cases = [
			(lambda: {"external_post_id": "1_2"}, O.LOG_PUBLISHED, None, 1),
			(fails(429), O.LOG_RETRY, 429, 1),
			(fails(401), O.LOG_HELD, 401, 1),
			(fails(400), O.LOG_FAILED, 400, 1),
			(fails(502), O.LOG_UNCONFIRMED, 502, 1),
			(fails(None), O.LOG_UNCONFIRMED, None, 1),
		]
		for send, outcome, status, sent in cases:
			store, name = claimed_job()
			dispatch(store, name, send=send)
			self.assertEqual(outcomes(store, name), [outcome], outcome)
			entry = store.logs[name][0]
			self.assertEqual(
				(entry["http_status"], entry["sent"], entry["attempt"]), (status, sent, 1), outcome
			)
			self.assertEqual(entry["at"], T0)
			self.assertIsNone(entry["by_user"], "the outbox decided, not a person")

	def test_a_failure_before_sending_is_logged_as_not_sent(self):
		store, name = claimed_job()

		def prepare(job):
			raise MarketingAPIError("Facebook", "token server down", status=503)

		dispatch(store, name, prepare=prepare)
		entry = store.logs[name][0]
		self.assertEqual((entry["outcome"], entry["sent"], entry["http_status"]), (O.LOG_RETRY, 0, 503))
		self.assertIn("token server down", entry["message"])

	def test_the_log_keeps_every_attempt_where_last_error_keeps_one(self):
		store, name = claimed_job()
		dispatch(store, name, send=fails(429))
		store.claim(name, "L1", O.retry_at(T0, 1), T0 + datetime.timedelta(hours=1))
		dispatch(store, name, send=lambda: {"external_post_id": "1_2", "permalink": "https://fb/1"})
		self.assertEqual(outcomes(store, name), [O.LOG_RETRY, O.LOG_PUBLISHED])
		self.assertEqual([e["attempt"] for e in store.logs[name]], [1, 2])
		self.assertIn("https://fb/1", store.logs[name][1]["message"])

	def test_retries_running_out_say_so(self):
		store, name = claimed_job(attempts=O.MAX_ATTEMPTS - 1)
		dispatch(store, name, send=fails(429))
		self.assertEqual(outcomes(store, name), [O.LOG_FAILED])
		self.assertIn("Gave up", store.logs[name][0]["message"])

	def test_a_duplicate_is_logged_once_as_published(self):
		store, first = claimed_job()
		dispatch(store, first, send=lambda: {"external_post_id": "101_555"})
		store, second = claimed_job(store, target="row-li", social_account="SACC-LinkedIn-9")
		dispatch(store, second, send=lambda: {"external_post_id": "101_555"})
		self.assertEqual(outcomes(store, second), [O.LOG_PUBLISHED])
		self.assertIn("already recorded", store.logs[second][0]["message"])

	def test_lease_expiry_and_switching_off_are_logged(self):
		store, name = claimed_job()
		O.reclaim_expired(store, T0 + datetime.timedelta(hours=1))
		self.assertEqual(outcomes(store, name), [O.LOG_NOT_SENT])
		store, name = claimed_job()
		store.mark_dispatched(name, T0)
		O.reclaim_expired(store, T0 + datetime.timedelta(hours=1))
		self.assertEqual(outcomes(store, name), [O.LOG_UNCONFIRMED])
		self.assertEqual(store.logs[name][0]["sent"], 1)
		store, name = claimed_job()
		dispatch(store, name, send=lambda: None, sendable=lambda job: False)
		self.assertEqual(outcomes(store, name), [O.LOG_NOT_SENT])

	def test_a_persons_answer_names_them(self):
		for outcome, logged in (
			("published", O.LOG_RESOLVED),
			("retry", O.LOG_RESOLVED),
			("cancel", O.LOG_CANCELED),
		):
			store, name = claimed_job()
			dispatch(store, name, send=fails(502))
			O.resolve(store, name, outcome, "nik@example.com", T0, permalink="https://fb/9")
			self.assertEqual(outcomes(store, name), [O.LOG_UNCONFIRMED, logged], outcome)
			self.assertEqual(store.logs[name][-1]["by_user"], "nik@example.com")
			if outcome == "published":
				self.assertIn("https://fb/9", store.logs[name][-1]["message"])

	def test_every_outcome_is_an_option_and_messages_are_capped(self):
		self.assertEqual(
			tuple(fields("social_publish_attempt")["outcome"]["options"].split("\n")[1:]), O.LOG_OUTCOMES
		)
		entry = O.log_entry(T0, {"attempts": 3}, O.LOG_FAILED, "x" * 5000, 400, True)
		self.assertEqual((len(entry["message"]), entry["sent"], entry["attempt"]), (1000, 1, 3))
		self.assertEqual(set(entry), set(fields("social_publish_attempt")) - {"column_break_attempt"})


class CancelPostTests(unittest.TestCase):
	"""Canceling stops what has not gone out; it never touches what has (TASK-2026-01486)."""

	def queued(self):
		store = MemoryStore()
		O.enqueue(store, "SPOST-00001", T0)
		return store, {j["network"]: n for n, j in store.jobs.items()}

	def test_a_post_with_nothing_queued_is_simply_canceled(self):
		store = MemoryStore(post={"status": O.POST_DRAFT})
		self.assertEqual(O.cancel_post(store, "SPOST-00001", "nik@example.com", T0), O.POST_CANCELED)
		self.assertEqual(store.post_statuses, [O.POST_CANCELED])

	def test_pending_jobs_are_canceled_with_who_and_a_log_entry(self):
		store, jobs = self.queued()
		self.assertEqual(O.cancel_post(store, "SPOST-00001", "nik@example.com", T0), O.POST_CANCELED)
		for name in jobs.values():
			self.assertEqual(
				(store.jobs[name]["state"], store.jobs[name]["resolved_by"]), (O.CANCELED, "nik@example.com")
			)
			self.assertEqual(outcomes(store, name), [O.LOG_CANCELED])
		self.assertEqual(store.target_values["row-fb"]["status"], O.CANCELED)
		self.assertEqual(store.post_statuses[-1], O.POST_CANCELED)

	def test_what_went_out_or_may_have_stays_as_it_is(self):
		store, jobs = self.queued()
		fb, li = jobs["Facebook"], jobs["LinkedIn"]
		store.claim(fb, "L1", T0, T0 + datetime.timedelta(minutes=30))
		dispatch(store, fb, send=lambda: {"external_post_id": "1_2"})
		self.assertEqual(O.cancel_post(store, "SPOST-00001", "nik@example.com", T0), O.POST_PUBLISHED)
		self.assertEqual((store.jobs[fb]["state"], store.jobs[li]["state"]), (O.PUBLISHED, O.CANCELED))
		store, jobs = self.queued()
		fb = jobs["Facebook"]
		store.claim(fb, "L1", T0, T0 + datetime.timedelta(minutes=30))
		dispatch(store, fb, send=fails(502))
		O.cancel_post(store, "SPOST-00001", "nik@example.com", T0)
		self.assertEqual(store.jobs[fb]["state"], O.UNCONFIRMED, "a person still has to look")

	def test_refused_while_a_request_may_be_leaving(self):
		store, jobs = self.queued()
		store.claim(jobs["Facebook"], "L1", T0, T0 + datetime.timedelta(minutes=30))
		with self.assertRaises(ValueError):
			O.cancel_post(store, "SPOST-00001", "nik@example.com", T0)
		self.assertEqual(store.jobs[jobs["LinkedIn"]]["state"], O.PENDING, "nothing half-canceled")


# ---------------------------------------------------------------- schema and wiring


class SchemaTests(unittest.TestCase):
	def test_job_states_match_the_doctype(self):
		self.assertEqual(tuple(fields("social_publish_job")["state"]["options"].split("\n")), O.JOB_STATES)
		targets = set(fields("social_post_target")["status"]["options"].split("\n")) - {""}
		self.assertTrue({O.PUBLISHED, O.FAILED, O.UNCONFIRMED, O.CANCELED, "Queued"} <= targets)

	def test_the_post_id_is_unique_and_the_identities_are_fixed(self):
		self.assertEqual(fields("social_publish_job")["external_post_id"].get("unique"), 1)
		for name, identity in {
			"social_account": ("network", "external_id"),
			"social_post_metric": ("publish_job", "metric_date"),
			"social_publish_job": ("social_post", "target", "social_account"),
		}.items():
			f = fields(name)
			for fieldname in identity:
				self.assertEqual(f[fieldname].get("set_only_once"), 1, f"{name}.{fieldname}")
		self.assertEqual(schema("social_account")["autoname"], "format:SACC-{network}-{external_id}")

	def test_networks_and_media_rights(self):
		self.assertEqual(
			tuple(fields("social_account")["network"]["options"].split("\n")), P.PUBLISH_NETWORKS
		)
		rights = fields("marketing_media_asset")["usage_rights"]
		self.assertIn(O.CLEARED_FOR_SOCIAL, rights["options"].split("\n"))
		self.assertNotEqual(rights["default"], O.CLEARED_FOR_SOCIAL, "nothing is cleared by default")

	def test_american_spelling(self):
		for path in DOCTYPES.rglob("*.json"):
			self.assertNotIn("Cancelled", path.read_text(encoding="utf-8"), path.name)

	def test_approved_content_is_tracked(self):
		self.assertEqual(schema("social_post").get("track_changes"), 1)


class WiringTests(unittest.TestCase):
	def setUp(self):
		self.tree = ast.parse(SWEEPER.read_text(encoding="utf-8"))

	def test_resolve_is_post_only_gated_and_not_guest(self):
		fn = next(n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == "resolve_job")
		decorators = [ast.unparse(d) for d in fn.decorator_list]
		self.assertEqual(decorators, ["frappe.whitelist(methods=['POST'])"])
		body = ast.unparse(fn)
		# TASK-2026-01486: Marketing Manager or System Manager, from a signed-in browser.
		self.assertIn("workflow.resolve_problems(frappe.get_roles(), approval.browser_request())", body)
		self.assertLess(body.index("resolve_problems"), body.index("outbox.resolve"), "checked first")
		whitelisted = [
			n.name
			for n in self.tree.body
			if isinstance(n, ast.FunctionDef) and any("whitelist" in ast.unparse(d) for d in n.decorator_list)
		]
		self.assertEqual(whitelisted, ["resolve_job"], "nothing else here is callable over HTTP")

	def test_enqueue_passes_what_run_dispatch_takes(self):
		run = next(n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_dispatch")
		params = {a.arg for a in run.args.args}
		calls = [
			n
			for n in ast.walk(self.tree)
			if isinstance(n, ast.Call) and ast.unparse(n.func) == "frappe.enqueue"
		]
		self.assertEqual(len(calls), 1)
		keywords = {k.arg for k in calls[0].keywords}
		self.assertNotIn("job_name", keywords, "job_name is frappe.enqueue's own; it never reaches the job")
		reserved = {"queue", "timeout", "job_id", "deduplicate", "is_async", "now", "at_front", "event"}
		self.assertEqual(keywords - reserved, params)

	def test_the_sweep_is_scheduled(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		self.assertIn(
			'"2-59/5 * * * *": ["erpnext_enhancements.marketing.publish.sweeper.sweep_publish_jobs"]', hooks
		)


if __name__ == "__main__":
	unittest.main()
