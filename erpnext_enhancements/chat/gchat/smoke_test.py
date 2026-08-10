# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Phase 1's checkpoint gate: one scripted round trip against **real** Google Chat.

    bench --site <site> execute erpnext_enhancements.chat.gchat.smoke_test.run \\
      --kwargs '{"as_user": "<a-real-employee>@sapphirefountains.com", \\
                 "with_user": "<second-employee>@sapphirefountains.com"}'

**THIS SCRIPT HAS NEVER BEEN RUN.** It was authored on 2026-08-08 in an environment with
no bench, no ``requests`` against Google, and no confirmation that
``RUNBOOK_google_cloud_setup.md`` was executed. Every assertion below describes what the
Chat API is *documented* to do; none of it has been observed. Nothing in this file may be
quoted as evidence until a human runs the command above and pastes the transcript into the
Phase 1 checkpoint. If a step's output surprises you, the reference is more likely right
than this file is — record what you saw and fix the script.

What it proves, and why each step is here
-----------------------------------------
Eleven steps, in the order Phase 1 §6 fixes them. Three of them are worth calling out
because they are the ones a lazier script would fake:

* **Step 3 re-runs the identical ``spaces:setup`` call** and asserts the *same* space name
  comes back. Idempotency is the entire justification for deriving ``requestId``
  deterministically; asserting it is cheap and assuming it is how you find out in Phase 2,
  from a room list with two of everything.
* **Step 5 stops and asks a human.** Whether a relayed message renders as the real person
  with no ``App`` badge is **not inferable from the API response** — the response looks
  identical either way. Getting this wrong means an auth-layer rewrite, so it is a
  human observation, typed into this script, recorded verbatim in the summary.
* **Step 8 prints post-delete resource behaviour verbatim** rather than asserting a shape.
  Whether ``deleteTime`` / ``deletionMetadata`` come back, and whether ``showDeleted``
  returns tombstones, was never read from the reference (ADR 0009 §G.6). Decision #12's
  retained audit trail depends on the answer, so the script's job is to *observe*, not to
  confirm a guess.

Safety properties, because this runs against the live Workspace
---------------------------------------------------------------
* **It refuses to run in dry-run mode.** The point is to touch Google; a green transcript
  produced by :mod:`erpnext_enhancements.chat.gchat.dryrun` would be worse than no
  transcript at all.
* **It never prints or logs a token.** Step 2 proves the token by asking Google what it is
  — ``tokeninfo`` returns the granted scopes, the expiry and the impersonated identity —
  and prints those. Everything printed out of an exception goes through
  :func:`~erpnext_enhancements.chat.gchat.client.scrub_secrets` first.
* **It leaves no orphan space.** Test spaces are visible to real employees, so every space
  name is written to a durable ledger in Redis *the instant Google returns it* — before
  any assertion that could raise — and step 11 deletes them all. A run that crashed
  between steps 3 and 11 is reaped by the *next* run's step 0, and if cleanup cannot
  delete (see the scope note on :func:`_delete_space`) the script prints the space names
  and the manual console steps in a block you cannot miss.
* **It is safe to re-run.** The space's ``requestId`` is derived from
  ``(site, as_user, with_user)`` with no clock in it, so a second run reuses it and Google
  returns the existing space rather than a second one. Message ids *do* carry a per-run
  nonce, because a client-assigned ``messageId`` must be unique within a space and a
  re-run into a surviving space would otherwise collide.

What it deliberately does not do
--------------------------------
No Pub/Sub consumer, no Workspace Events subscription, no relay. Step 9 is a
**connectivity proof**: it prints the exact commands a human runs and records what they
saw. Phase 2 owns the durable inbound leg, and building it here would be a non-goal
wearing a smoke test's clothes.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import frappe
from frappe.utils import cint, now

from erpnext_enhancements.chat.gchat import auth, ids
from erpnext_enhancements.chat.gchat.client import (
	CHAT_API_VERSION,
	AuthIdentity,
	GoogleCall,
	GoogleChatClient,
	SpaceType,
	ThreadReply,
	message_alias,
	message_ids_for,
	scrub_secrets,
	validate_space_name,
)

#: Named so nobody in the domain mistakes it for a real room, and stable across runs so a
#: deterministic ``requestId`` can return the same space instead of making a second one.
SMOKE_SPACE_DISPLAY_NAME: str = "ERPNext Chat Smoke Test — DO NOT USE"

#: Where a created space name is parked between "Google returned it" and "we deleted it".
#: The *cache* Redis, not the queue one: the deploy FLUSHDBs both, and a lost ledger entry
#: costs a manual cleanup rather than a correctness failure. TTL is a week so a forgotten
#: orphan still surfaces on the next run rather than living forever.
ORPHAN_LEDGER_KEY: str = "chat:gchat:smoke_test:orphan_spaces"
ORPHAN_LEDGER_TTL_SECONDS: int = 7 * 24 * 3600

#: Google's token introspection endpoint. The **only** non-Chat Google call this script
#: makes, and it earns its place: it is the one way to distinguish the scopes we
#: *requested* from the scopes domain-wide delegation actually *granted*, which is the
#: single most common DWD misconfiguration and otherwise surfaces as a 403 four steps
#: later that reads like a permissions bug.
#:
#: VERIFY (unexecuted): the reference documents ``GET /tokeninfo?access_token=…``. This
#: uses **POST with a form body** so the token never lands in a URL, a query log or a
#: proxy access log. Confirm POST is accepted; if it is not, do *not* fall back to the
#: query-string form without saying so in the transcript.
#:
#: **This is the one Google host named outside** :mod:`~erpnext_enhancements.chat.gchat.client`
#: **and** :mod:`~erpnext_enhancements.chat.gchat.auth`, and it is a named exception in
#: ``tests/test_chat_guardrails.py`` (``HOST_LITERAL_EXCEPTIONS``) rather than something the
#: containment guard cannot see. It is tolerated because this module is a ``bench execute``
#: script wired into no hook, no scheduler and no endpoint. The shape that would remove the
#: exception is a named introspection helper in ``auth.py``, which already owns
#: ``oauth2.googleapis.com``; if you touch this, do that instead of adding a second host here.
TOKENINFO_URL: str = "https://oauth2.googleapis.com/tokeninfo"

#: Deleting a *space* is gated by ``chat.delete`` — which is **not** in
#: :data:`~erpnext_enhancements.chat.gchat.auth.RELAY_SCOPES`, because the relay never
#: deletes a space and Google's own DWD guidance is to authorise nothing spare. Cleanup
#: therefore mints its own token. If ``chat.delete`` was not authorised against the
#: delegation service account's OAuth client id in the Admin console, **this mint fails**
#: and the space survives — which is why :func:`_step_11_cleanup` degrades to printing
#: manual instructions rather than pretending.
#:
#: VERIFY (unexecuted): that ``spaces.delete`` under user auth requires exactly
#: ``chat.delete``, and that adding it to the DWD authorisation does not disturb the
#: relay's own scope grant (ADR 0009 §E.5.2).
CLEANUP_SCOPES: tuple[str, ...] = auth.RELAY_SCOPES + (auth.SCOPE_CHAT_DELETE,)

#: How many messages to ask for when looking for tombstones in step 8. Small: the space
#: contains two messages and the point is to read the shape, not to page.
TOMBSTONE_PAGE_SIZE: int = 25

#: The step numbers are Phase 1 §6's, kept literally so a transcript line and a line of the
#: prompt can be put side by side. **10 is absent on purpose** — it is the summary table
#: itself, which cannot be a row in the table it prints.
STEP_TITLES: dict[int, str] = {
	1: "Effective configuration (refuses to run in dry-run)",
	2: "Mint a DWD access token (token never printed)",
	3: "spaces:setup twice with one deterministic requestId",
	4: "Post a message with a client- prefixed messageId",
	5: "HUMAN: renders as the real person, no App badge",
	6: "Edit via messages.patch and prove lastUpdateTime moved",
	7: "Threaded reply, and the thread name matches",
	8: "Delete both, and print post-delete behaviour verbatim",
	9: "Inbound leg: the commands, and what the human saw",
	11: "Clean up: delete the test space(s)",
}

_RULE: str = "─" * 92


class SmokeAbort(Exception):
	"""A precondition failed and continuing would produce a misleading transcript."""


class StepFailed(Exception):
	"""One step did not do what it claims to prove. Recorded, then the run decides."""


@dataclass
class StepResult:
	"""One row of step 10's table."""

	index: int
	title: str
	status: str
	elapsed_ms: float
	detail: str = ""


@dataclass
class SmokeState:
	"""Everything one run learns, in one place so the steps do not read each other."""

	as_user: str
	with_user: str
	site: str
	nonce: str
	space_name: str = ""
	primary_name: str = ""
	primary_client_id: str = ""
	#: ``lastUpdateTime`` as it stood before step 6's edit. Step 6 asserts it moved, which
	#: is the half of "the edit landed" that a changed ``text`` does not prove.
	primary_baseline_update: str = ""
	thread_name: str = ""
	reply_name: str = ""
	reply_client_id: str = ""
	#: ``clientAssignedMessageId`` as returned by ``messages.get`` — **not** by ``create``.
	#: ``""`` means the field was absent from the fetch, and that is a finding rather than a
	#: failure; see :func:`_step_6_edit`. The summary reports it either way because Phase 2's
	#: entire inbound echo ladder reads this field off a ``get``.
	fetched_client_id: str = ""
	fetched_client_id_seen: bool = False
	human_answers: dict[str, str] = field(default_factory=dict)
	created_spaces: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Output helpers — every line the human pastes into the checkpoint comes from here
# ---------------------------------------------------------------------------


def _say(line: str = "") -> None:
	"""Print one transcript line, unbuffered.

	``print`` rather than ``frappe.logger``: the deliverable is a transcript a human
	pastes into a checkpoint report, and the site log is the wrong place for something
	nobody will read within the hour. The client logs its own structured line per attempt
	regardless.
	"""
	print(line, flush=True)


def _heading(index: int, title: str) -> None:
	_say("")
	_say(_RULE)
	_say(f"STEP {index:>2} — {title}")
	_say(_RULE)


def _dump(label: str, payload: Any) -> None:
	"""Print a Google response verbatim, scrubbed.

	Verbatim matters for step 8 specifically: an *interpretation* of post-delete
	behaviour is exactly the thing decision #12 cannot be designed against.
	"""
	rendered = json.dumps(payload, indent=2, sort_keys=True, default=str)
	_say(f"{label}:")
	for line in scrub_secrets(rendered).splitlines():
		_say(f"    {line}")


def _prompt(question: str) -> str:
	"""Ask the human, and never invent an answer.

	``bench execute`` runs attached to the operator's terminal, so ``input`` works. When
	it does not — a cron, a CI runner, a pipe — this raises rather than defaulting to
	"yes", because a fabricated human confirmation is the single most damaging line this
	script could emit.
	"""
	_say("")
	try:
		return input(question).strip()
	except (EOFError, KeyboardInterrupt, OSError) as exc:
		raise StepFailed(
			"no interactive terminal, so the human observation could not be taken "
			f"({type(exc).__name__}). Re-run from a terminal: this step cannot be inferred "
			"from the API response and must not be skipped."
		) from None


def _is_yes(answer: str) -> bool:
	return answer.strip().lower() in {"y", "yes"}


# ---------------------------------------------------------------------------
# The orphan-space ledger
# ---------------------------------------------------------------------------


def _ledger_read() -> list[str]:
	try:
		raw = frappe.cache().get_value(ORPHAN_LEDGER_KEY)
	except Exception:
		return []
	if isinstance(raw, (list, tuple)):
		return [str(name) for name in raw if name]
	if isinstance(raw, str) and raw.strip():
		try:
			parsed = json.loads(raw)
		except ValueError:
			return []
		return [str(name) for name in parsed if name] if isinstance(parsed, list) else []
	return []


def _ledger_write(names: Sequence[str]) -> None:
	try:
		frappe.cache().set_value(
			ORPHAN_LEDGER_KEY,
			json.dumps(sorted(set(names))),
			expires_in_sec=ORPHAN_LEDGER_TTL_SECONDS,
		)
	except Exception as exc:  # pragma: no cover - a ledger failure must not fail the run
		_say(f"    ! could not write the orphan ledger: {scrub_secrets(str(exc))}")


def _ledger_add(space_name: str) -> None:
	"""Record a space **before** anything that could raise.

	The window between "Google created the space" and "we asserted something about it" is
	the only window in which an orphan can be created, so it is the window the ledger has
	to cover.
	"""
	if not space_name:
		return
	_ledger_write([*_ledger_read(), space_name])


def _ledger_remove(space_name: str) -> None:
	_ledger_write([name for name in _ledger_read() if name != space_name])


# ---------------------------------------------------------------------------
# Google calls this script needs and the client does not expose
# ---------------------------------------------------------------------------


def _build_delete_space_call(space_name: str) -> GoogleCall:
	"""``DELETE /v1/{name=spaces/*}`` — the one call cleanup needs and the client lacks.

	:mod:`~erpnext_enhancements.chat.gchat.client` exposes the seven methods Phase 1 named
	and no more, and space deletion is not one of them: nothing in the product deletes a
	space, only this test does. Rather than widen the production client for a test, the
	call is described here as a :class:`~erpnext_enhancements.chat.gchat.client.GoogleCall`
	and handed to the client's own executor — so it still goes through the single
	``_request`` choke point, the retry loop, the structured logging and the dry-run guard.
	The API host is never named here; it comes from the client, which
	``tests/test_chat_guardrails.py`` requires to be its only mention in the app.

	If Phase 2 ever grows a real reason to delete a space, this belongs in the client with
	a test — not copied.
	"""
	name = validate_space_name(space_name)
	return GoogleCall(
		client_method="delete_space",
		google_method="spaces.delete",
		http_method="DELETE",
		path=f"/{CHAT_API_VERSION}/{name}",
		space_name=name,
		requires_identity=AuthIdentity.USER,
		dry_run_kind="EMPTY",
		dry_run_seed=(name,),
	)


def _delete_space(space_name: str, as_user: str) -> dict[str, Any]:
	"""Delete one space as ``as_user``, using a token that carries ``chat.delete``."""
	def provider(_identity: AuthIdentity, subject: str | None) -> str:
		return auth.get_delegated_token(subject or as_user, CLEANUP_SCOPES)

	client = GoogleChatClient(subject=as_user, identity=AuthIdentity.USER, token_provider=provider)
	# Reaching for `_execute` on purpose: see _build_delete_space_call. The public
	# surface is deliberately seven methods, and a test is not a reason to make it eight.
	return client._execute(_build_delete_space_call(space_name))


def _token_introspection(token: str, timeout: float) -> dict[str, Any]:
	"""Ask Google what the token it just issued actually is. The token itself never leaves.

	``requests`` is imported here rather than at module scope for the same reason the
	client does it: the bench-free CI tier installs neither ``frappe`` nor ``requests``,
	and a module that cannot be imported there is a module whose helpers cannot be tested
	there either.
	"""
	import requests

	response = requests.post(TOKENINFO_URL, data={"access_token": token}, timeout=timeout)
	body: dict[str, Any]
	try:
		parsed = response.json()
		body = parsed if isinstance(parsed, dict) else {"_value": parsed}
	except ValueError:
		body = {"_unparsed": scrub_secrets(response.text[:500])}
	if response.status_code != 200:
		raise StepFailed(
			f"tokeninfo returned HTTP {response.status_code}: "
			f"{scrub_secrets(json.dumps(body, sort_keys=True, default=str))[:500]}"
		)
	return body


# ---------------------------------------------------------------------------
# The eleven steps
# ---------------------------------------------------------------------------


def _step_1_configuration(state: SmokeState) -> str:
	"""Print the effective configuration, and refuse to run against a dry-run site."""
	settings = frappe.get_cached_doc("Chat Settings")

	def value(fieldname: str) -> str:
		return str(getattr(settings, fieldname, None) or "(unset)")

	def flag(fieldname: str) -> str:
		return "ON" if cint(getattr(settings, fieldname, 0)) else "off"

	_say(f"  site                          {state.site}")
	_say(f"  acting user (DWD subject)     {state.as_user}")
	_say(f"  second employee (membership)  {state.with_user}")
	_say("")
	_say(f"  google_project_id             {value('google_project_id')}")
	_say(f"  gcp_project_number            {value('gcp_project_number')}")
	_say(f"  workspace_domain              {value('workspace_domain')}")
	_say(f"  delegation_service_account    {value('delegation_service_account')}")
	_say(f"  vm_service_account_email      {value('vm_service_account_email')}")
	_say(f"  chat_app_service_account      {value('chat_app_service_account')}")
	_say(f"  interaction_endpoint_url      {value('interaction_endpoint_url')}")
	_say(f"  pubsub_topic                  {value('pubsub_topic')}")
	_say(f"  pubsub_events_subscription    {value('pubsub_events_subscription')}")
	_say("")
	_say(f"  enabled                       {flag('enabled')}")
	_say(f"  dry_run_mode                  {flag('dry_run_mode')}")
	_say(f"  restrict_to_whitelist         {flag('restrict_to_whitelist')}")
	_say(f"  relay_identity_mode           {value('relay_identity_mode')}")
	_say(f"  http_timeout_seconds          {value('http_timeout_seconds')}")
	_say(f"  relay_max_attempts            {value('relay_max_attempts')}")
	_say("")
	_say("  No credential is printed above and none exists to print — Chat Settings holds")
	_say("  identifiers only, and the keyless DWD path never materialises key material.")

	if cint(getattr(settings, "dry_run_mode", 0)):
		raise SmokeAbort(
			"dry_run_mode is ON. This script exists to touch real Google Chat; running it "
			"against chat.gchat.dryrun would print a green transcript that proves nothing. "
			"Turn dry_run_mode off in Chat Settings, run it, and turn it back on."
		)
	if not cint(getattr(settings, "enabled", 0)):
		raise SmokeAbort(
			"Chat Settings.enabled is off. auth.get_delegated_token refuses to mint a "
			"credential while the master switch is off (by design), so every later step "
			"would fail with the same misleading error."
		)

	return "configuration printed; dry_run_mode off; master switch on"


def _step_2_token(state: SmokeState) -> str:
	"""Mint a DWD access token as the acting user and prove what it is — never print it."""
	timeout = float(getattr(frappe.get_cached_doc("Chat Settings"), "http_timeout_seconds", 30) or 30)

	# force_refresh so this step tests the mint, not the cache. A cached token would still
	# be valid and would still pass introspection, and would prove nothing about signJwt.
	token = auth.get_delegated_token(state.as_user, auth.RELAY_SCOPES, force_refresh=True)
	if not token:
		raise StepFailed("auth.get_delegated_token returned an empty token")
	_say(f"  token minted: {len(token)} characters. It is not printed, here or anywhere.")

	info = _token_introspection(token, timeout)
	granted = str(info.get("scope") or "")
	granted_set = set(granted.split())
	expires_in = info.get("expires_in", "(absent)")
	exp = info.get("exp", "(absent)")
	identity = str(info.get("email") or "(absent)")

	_say(f"  expires_in                    {expires_in} seconds")
	_say(f"  exp                           {exp}")
	_say(f"  granted scopes                {granted or '(none reported)'}")
	_say(f"  token identity (email claim)  {identity}")
	_say(f"  azp / aud                     {info.get('azp', '(absent)')} / {info.get('aud', '(absent)')}")

	missing = [scope for scope in auth.RELAY_SCOPES if scope not in granted_set]
	if missing:
		raise StepFailed(
			"domain-wide delegation did not grant every requested scope. Missing: "
			f"{missing}. The Admin console's scope list for the delegation service account's "
			"numeric OAuth client id must match auth.RELAY_SCOPES byte for byte — a silently "
			"truncated paste is the usual cause, and it surfaces later as a 403 on setup_space."
		)

	if identity.lower() != state.as_user.lower():
		# Not fatal: tokeninfo's `email` is documented for OIDC tokens and its behaviour on
		# a delegated access token was never read. Report it rather than assert on it.
		_say(
			f"  ! tokeninfo reports identity {identity!r}, not {state.as_user!r}. If step 5's "
			"human check passes anyway, this is a tokeninfo reporting quirk; if step 5 fails, "
			"this is the reason."
		)

	return f"token minted, expires_in={expires_in}, {len(granted_set)} scopes granted"


def _step_3_space(state: SmokeState, client: GoogleChatClient) -> str:
	"""Create the space via ``spaces:setup``, then re-run the identical call and compare."""
	request_id = ids.request_id(
		f"SMOKE-SPACE::{state.as_user}::{state.with_user}", "Space Setup", site=state.site
	)
	_say(f"  deterministic requestId       {request_id}")
	_say("  (no clock in the seed — a re-run reuses it, so Google returns the same space)")

	def setup() -> dict[str, Any]:
		created = client.setup_space(
			space_type=SpaceType.SPACE,
			request_id=request_id,
			member_emails=[state.with_user],
			display_name=SMOKE_SPACE_DISPLAY_NAME,
		)
		name = str(created.get("name") or "")
		if name:
			# Ledger first, assertions after. See _ledger_add.
			_ledger_add(name)
			if name not in state.created_spaces:
				state.created_spaces.append(name)
		return created

	first = setup()
	first_name = str(first.get("name") or "")
	_say(f"  first  call → {first_name or '(no name returned)'}")
	if not first_name:
		raise StepFailed(f"spaces:setup returned no resource name: {scrub_secrets(str(first))[:300]}")
	state.space_name = first_name

	second = setup()
	second_name = str(second.get("name") or "")
	_say(f"  second call → {second_name or '(no name returned)'}")

	if second_name != first_name:
		raise StepFailed(
			f"the same requestId produced two different spaces ({first_name} and {second_name}). "
			"Deterministic requestId is NOT giving exactly-once provisioning — Phase 2 must not "
			"rely on it, and unique(gchat_space_name) becomes the only defence. Both spaces are "
			"in the cleanup ledger."
		)

	_dump("  spaces:setup response (first call)", first)
	return f"{first_name}; identical name on re-run (idempotency observed, not assumed)"


def _step_4_post(state: SmokeState, client: GoogleChatClient) -> str:
	"""Post the first message with a ``client-`` prefixed messageId."""
	message_id, request_id = message_ids_for(f"SMOKE-{state.nonce}-PRIMARY", "Message Create")
	text = (
		"ERPNext Chat Phase 1 smoke test. This message was relayed by ERPNext as "
		f"{state.as_user} using domain-wide delegation. It will be edited and then deleted "
		"within a minute. Nothing here needs a reply."
	)
	_say(f"  messageId                     {message_id}")
	_say(f"  requestId                     {request_id}")

	created = client.create_message(state.space_name, message_id, request_id, text)
	state.primary_name = str(created.get("name") or "")
	state.primary_client_id = str(created.get("clientAssignedMessageId") or "")
	state.thread_name = str((created.get("thread") or {}).get("name") or "")

	_say(f"  name                          {state.primary_name or '(absent)'}")
	_say(f"  clientAssignedMessageId       {state.primary_client_id or '(absent)'}")
	_say(f"  createTime                    {created.get('createTime', '(absent)')}")
	_say(f"  lastUpdateTime                {created.get('lastUpdateTime', '(absent)')}")
	_say(f"  thread.name                   {state.thread_name or '(absent)'}")
	_dump("  messages.create response", created)

	if not state.primary_name:
		raise StepFailed("messages.create returned no resource name")
	if state.primary_client_id and state.primary_client_id != message_id:
		raise StepFailed(
			f"Google echoed clientAssignedMessageId={state.primary_client_id!r}, not the "
			f"{message_id!r} we assigned. Invariant I3's echo suppression keys on that id "
			"being ours, so this breaks Phase 2's layer-1 dedupe."
		)
	if not state.primary_client_id:
		_say(
			"  ! clientAssignedMessageId is absent from the create response. Phase 2's inbound "
			"echo check reads it off the *event*, not this response — confirm it is present "
			"there in step 9 before relying on layer 1."
		)

	state.primary_baseline_update = str(created.get("lastUpdateTime") or created.get("createTime") or "")
	return f"{state.primary_name} in thread {state.thread_name or '(none)'}"


def _step_5_human_authorship(state: SmokeState) -> str:
	"""The required human observation: does it render as the person, with no ``App`` badge?"""
	_say("  A human must now look at the native Google Chat client. The API response cannot")
	_say("  answer this — it looks identical whether the message is attributed to the person")
	_say("  or to a bot, and getting it wrong means rewriting the auth layer.")
	_say("")
	_say(f"    1. Sign in to Google Chat as {state.as_user} (or ask {state.with_user}).")
	_say(f"    2. Open the space named: {SMOKE_SPACE_DISPLAY_NAME}")
	_say("    3. Look at the message posted in step 4.")
	_say("")
	_say("  It must show the real person's name and avatar as the sender, with NO 'App'")
	_say("  badge next to the name. A 'via <app name>' attribution is expected and fine.")

	answer = _prompt(
		"  Does it render as the real human with NO 'App' badge? Type yes or no, then Enter: "
	)
	state.human_answers["authorship"] = answer
	_say(f"  human answered (verbatim): {answer!r}")

	if not _is_yes(answer):
		raise StepFailed(
			f"human observation was {answer!r}, not yes. If an App badge is present the relay "
			"is posting under app authentication, which contradicts CQ-1's human-attribution "
			"decision and is an auth-layer defect, not a rendering one."
		)
	return f"human confirmed: {answer!r} — real person, no App badge"


def _step_6_edit(state: SmokeState, client: GoogleChatClient) -> str:
	"""Edit via ``messages.patch``, then ``messages.get`` and prove both fields moved."""
	baseline = state.primary_baseline_update
	alias = message_alias(state.space_name, state.primary_client_id or "")
	new_text = (
		"ERPNext Chat Phase 1 smoke test — EDITED. If you can read this word, "
		"messages.patch with updateMask=text landed."
	)
	_say(f"  patch target (client- alias)  {alias}")
	_say(f"  baseline lastUpdateTime       {baseline or '(absent)'}")

	patched = client.patch_message(alias, text=new_text)
	_dump("  messages.patch response", patched)

	fetched = client.get_message(state.primary_name)
	observed_text = str(fetched.get("text") or "")
	observed_update = str(fetched.get("lastUpdateTime") or "")
	_say(f"  text after get                {observed_text[:80]!r}")
	_say(f"  lastUpdateTime after get      {observed_update or '(absent)'}")

	# --- the phase-blocking observation, and the reason this step is worth running ---
	#
	# Step 4 already read `clientAssignedMessageId` off the messages.CREATE response. That is
	# NOT the question. Phase 2's inbound echo ladder never sees a create response: the
	# subscription runs `payloadOptions.includeResource: false` (the only configuration with a
	# 7-day TTL), so an event carries a resource NAME and nothing else, and the pipeline
	# resolves it with `messages.get`. The client id is then checked against
	# `unique(room, client_message_id)` to decide "our own echo" versus "a coworker's message".
	#
	# So the field's presence ON THE FETCH is what the design rests on, and no Google document
	# or sample anywhere shows it populated in a response — the proto declares it a plain
	# read/write field, which is an argument, not evidence. This is the line that settles it.
	#
	# Absence is deliberately NOT a StepFailed. The smoke test's job is to report what the API
	# does; treating an unwelcome truth as a broken test is how a finding gets argued with
	# instead of acted on. The summary prints the verdict either way.
	state.fetched_client_id = str(fetched.get("clientAssignedMessageId") or "")
	state.fetched_client_id_seen = True
	_say(f"  clientAssignedMessageId (get) {state.fetched_client_id or '(ABSENT)'}")

	if state.fetched_client_id:
		if state.fetched_client_id != state.primary_client_id:
			raise StepFailed(
				f"messages.get returned clientAssignedMessageId={state.fetched_client_id!r} but "
				f"create returned {state.primary_client_id!r}. Echo suppression matches this "
				"value against an index; two different answers for one message would make it "
				"match neither."
			)
		_say("  -> layer 1 echo suppression is available: the id survives a round trip.")
	else:
		_say("  -> ABSENT. Phase 2's primary echo path cannot work as designed. The fallback is")
		_say("     ADR §G.3.2's bounded heuristic, which ships DISABLED (echo_fallback_enabled=0)")
		_say("     and refuses ambiguity. Raise this before Phase 3 builds on the engine.")

	if observed_text != new_text:
		raise StepFailed(f"text did not change; messages.get still returns {observed_text[:120]!r}")
	if not observed_update:
		raise StepFailed("messages.get returned no lastUpdateTime, so the edit cannot be proven")
	if baseline and observed_update == baseline:
		raise StepFailed(
			f"lastUpdateTime did not move ({observed_update}). The text changed but the "
			"timestamp did not, which would make Phase 2's edit reconciliation unable to tell "
			"a stale mirror from a current one."
		)
	return f"text changed and lastUpdateTime moved {baseline or '?'} → {observed_update}"


def _step_7_threaded_reply(state: SmokeState, client: GoogleChatClient) -> str:
	"""Reply in-thread with ``REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD`` and check the thread."""
	if not state.thread_name:
		raise StepFailed(
			"step 4 returned no thread.name, so there is nothing to reply into. In a space "
			"whose spaceThreadingState is GROUPED_MESSAGES this may be expected — record it "
			"and settle the open VERIFY on threading before Phase 5 depends on it."
		)

	message_id, request_id = message_ids_for(f"SMOKE-{state.nonce}-REPLY", "Message Create")
	_say(f"  messageId                     {message_id}")
	_say(f"  thread.name requested         {state.thread_name}")
	_say("  messageReplyOption            REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD")

	reply = client.create_message(
		state.space_name,
		message_id,
		request_id,
		"ERPNext Chat Phase 1 smoke test — threaded reply.",
		thread=ThreadReply(state.thread_name),
	)
	state.reply_name = str(reply.get("name") or "")
	state.reply_client_id = str(reply.get("clientAssignedMessageId") or message_id)
	observed_thread = str((reply.get("thread") or {}).get("name") or "")
	_say(f"  reply name                    {state.reply_name or '(absent)'}")
	_say(f"  reply thread.name             {observed_thread or '(absent)'}")
	_dump("  threaded messages.create response", reply)

	if observed_thread != state.thread_name:
		raise StepFailed(
			f"the reply landed in {observed_thread!r}, not {state.thread_name!r}. That is the "
			"documented silent failure of messageReplyOption — except we passed it, so this is "
			"the space's threading state, not a caller error. Record it against the open "
			"spaceThreadingState VERIFY."
		)
	return f"reply {state.reply_name} in thread {observed_thread}"


def _step_8_delete(state: SmokeState, client: GoogleChatClient) -> str:
	"""Delete both messages and print what Google says about a deleted resource, verbatim.

	No assertion on tombstone shape. ADR 0009 §G.6 carries post-delete behaviour as
	unresolved and decision #12's retained audit trail is designed against whatever this
	prints — an assertion here would be a guess with a green tick next to it.
	"""
	observations: list[str] = []

	if state.reply_name:
		# force=True even on the reply: it has no children, but the relay will always pass
		# force, and testing the flag the relay uses is worth more than testing the default.
		deleted_reply = client.delete_message(state.reply_name, force=True)
		_dump("  messages.delete (reply) response", deleted_reply)
		observations.append("reply deleted")

	deleted_primary = client.delete_message(state.primary_name, force=True)
	_dump("  messages.delete (original, force=true) response", deleted_primary)
	observations.append("original deleted")

	_say("")
	_say("  OBSERVED POST-DELETE BEHAVIOUR — quote this block into the checkpoint verbatim.")
	_say("  Decision #12's audit trail is designed against it, so do not summarise it.")

	for label, name in (("original", state.primary_name), ("reply", state.reply_name)):
		if not name:
			continue
		try:
			fetched = client.get_message(name)
			_dump(f"  messages.get after delete ({label})", fetched)
			has_delete_time = "deleteTime" in fetched
			has_metadata = "deletionMetadata" in fetched
			observations.append(
				f"get({label}) succeeded; deleteTime={has_delete_time} "
				f"deletionMetadata={has_metadata}"
			)
		except Exception as exc:
			rendered = scrub_secrets(f"{type(exc).__name__}: {exc}")
			_say(f"  messages.get after delete ({label}) raised:")
			_say(f"    {rendered}")
			observations.append(f"get({label}) raised {type(exc).__name__}")

	try:
		listed = client.list_messages(
			state.space_name, page_size=TOMBSTONE_PAGE_SIZE, show_deleted=True
		)
		_dump("  messages.list showDeleted=true", listed)
		tombstones = listed.get("messages") or []
		observations.append(f"showDeleted returned {len(tombstones)} message(s)")
	except Exception as exc:
		rendered = scrub_secrets(f"{type(exc).__name__}: {exc}")
		_say("  messages.list showDeleted=true raised:")
		_say(f"    {rendered}")
		observations.append(f"showDeleted raised {type(exc).__name__}")

	return "; ".join(observations)


def _step_9_inbound(state: SmokeState) -> str:
	"""Print the commands that prove the inbound leg, then record what the human saw.

	Phase 1 owns **no consumer**. The durable Pub/Sub worker and the Workspace Events
	subscription manager are Phase 2's, and building either here would be a non-goal. What
	this proves is connectivity: Google is emitting events for the actions above, and the
	webhook verifier accepts a real Google-issued token — which no unit test can do,
	because no unit test can make Google sign something.
	"""
	settings = frappe.get_cached_doc("Chat Settings")
	project = str(getattr(settings, "google_project_id", None) or "<PROJECT_ID>")
	subscription = str(getattr(settings, "pubsub_events_subscription", None) or "<EVENTS_SUBSCRIPTION>")
	endpoint = str(getattr(settings, "interaction_endpoint_url", None) or "<INTERACTION_ENDPOINT_URL>")

	_say("  Run these by hand. This script does not execute them — a consumer is Phase 2's.")
	_say("")
	_say("  (a) Drain the events subscription and read one payload:")
	_say("")
	_say(f"      gcloud pubsub subscriptions pull {subscription} \\")
	_say(f"        --auto-ack --limit=5 --project={project} --format=json")
	_say("")
	_say("      Decode the base64 `message.data`, and check three things:")
	_say("        • the event type (google.workspace.chat.message.v1.created / .updated / .deleted)")
	_say(f"        • the message resource name — it must match {state.primary_name or '<step 4 name>'}")
	_say("        • whether the event carries clientAssignedMessageId. Phase 2's layer-1 echo")
	_say("          suppression reads it off the event; if it is absent there, say so loudly.")
	_say("")
	_say("  (b) Prove the webhook rejects an unauthenticated caller (expect exactly 401):")
	_say("")
	_say("      curl -s -o /dev/null -w '%{http_code}\\n' -X POST \\")
	_say(f"        {endpoint} \\")
	_say("        -H 'Content-Type: application/json' -d '{}'")
	_say("")
	_say("  (c) Prove the verifier accepts a REAL Google-issued token: from the native Chat")
	_say("      client, DM the registered Chat app (or @mention it in a space it is in), then")
	_say("      read the site log for the verified event:")
	_say("")
	_say("      bench --site <site> console   # or:")
	_say("      tail -n 200 sites/<site>/logs/chat.gchat.webhook.log")
	_say("")
	_say("      Expect a line naming the event type and an HTTP 200. A 401 here with a real")
	_say("      Google token means the audience is not byte-identical to")
	_say(f"      Chat Settings.interaction_endpoint_url ({endpoint}) — trailing slash included.")
	_say("")
	_say("  If (a) is empty but the Pub/Sub topic IAM policy is correct, the fault is the Chat")
	_say("  app's Connection settings or the Workspace Events subscription — NOT Pub/Sub. Say")
	_say("  which in the checkpoint; they have different owners and different fixes.")

	answer = _prompt(
		"  Did (a) show an event matching step 4, and (b)+(c) behave as described? yes/no: "
	)
	state.human_answers["inbound"] = answer
	_say(f"  human answered (verbatim): {answer!r}")

	if not _is_yes(answer):
		raise StepFailed(
			f"human reported {answer!r}. The inbound leg is unproven; Phase 2 has nothing to "
			"build on until it is. Record which of (a), (b) or (c) failed — they have three "
			"different causes."
		)
	return f"human confirmed inbound connectivity: {answer!r}"


def _step_11_cleanup(state: SmokeState) -> str:
	"""Delete every space this run created. Orphans are visible to real employees."""
	targets = list(dict.fromkeys([*state.created_spaces, *_ledger_read()]))
	if not targets:
		_say("  nothing to clean up.")
		return "no spaces to delete"

	deleted: list[str] = []
	failed: list[tuple[str, str]] = []
	for name in targets:
		try:
			_delete_space(name, state.as_user)
			_ledger_remove(name)
			deleted.append(name)
			_say(f"  deleted {name}")
		except Exception as exc:
			rendered = scrub_secrets(f"{type(exc).__name__}: {exc}")
			failed.append((name, rendered))
			_say(f"  FAILED to delete {name}: {rendered}")

	if failed:
		_say("")
		_say("  " + "!" * 88)
		_say("  ORPHAN SPACES REMAIN IN THE WORKSPACE. They are visible to real employees.")
		_say("  Delete them by hand, now:")
		for name, reason in failed:
			_say(f"    • {name}   ({reason})")
		_say("")
		_say("  Most likely cause: the delegation service account is not authorised for")
		_say(f"    {auth.SCOPE_CHAT_DELETE}")
		_say("  in the Admin console, so the cleanup token could not be minted. Deleting a")
		_say("  SPACE needs that scope; the relay's own scopes do not include it on purpose.")
		_say("  Manual route: open the space in Google Chat as its manager → space name →")
		_say("  Space settings → Delete space. Or add the scope and re-run this script; it")
		_say("  reaps whatever is still in the ledger on its next start.")
		_say("  " + "!" * 88)
		raise StepFailed(f"{len(failed)} space(s) could not be deleted; see the block above")

	return f"deleted {len(deleted)} space(s): {', '.join(deleted)}"


def _reap_orphans(as_user: str) -> None:
	"""Step 0: delete anything a previous crashed run left behind, before making more."""
	orphans = _ledger_read()
	if not orphans:
		return
	_say("")
	_say(_RULE)
	_say("STEP  0 — reaping spaces left by a previous run")
	_say(_RULE)
	for name in orphans:
		try:
			_delete_space(name, as_user)
			_ledger_remove(name)
			_say(f"  reaped {name}")
		except Exception as exc:
			_say(f"  could not reap {name}: {scrub_secrets(f'{type(exc).__name__}: {exc}')}")
			_say("  it stays in the ledger and will be retried next run — or delete it by hand.")


# ---------------------------------------------------------------------------
# The summary table (§6 step 10)
# ---------------------------------------------------------------------------


def _print_summary(results: Sequence[StepResult], state: SmokeState) -> None:
	_say("")
	_say(_RULE)
	_say("STEP 10 — summary")
	_say(_RULE)
	_say("  Printed after step 11 on purpose, so the table can report the cleanup. §6 numbers")
	_say("  the table 10 and the cleanup 11; the ordering of the *work* is unchanged.")
	_say("")
	_say(f"  {'#':>2}  {'STATUS':<8} {'ELAPSED':>10}  STEP")
	_say(f"  {'':->2}  {'':-<8} {'':->10}  {'':-<58}")
	for result in results:
		_say(f"  {result.index:>2}  {result.status:<8} {result.elapsed_ms:>8.0f}ms  {result.title}")
		if result.detail:
			for line in _wrap(result.detail, 84):
				_say(f"          {line}")
	_say("")

	total = sum(result.elapsed_ms for result in results)
	failures = [result for result in results if result.status == "FAIL"]
	skipped = [result for result in results if result.status == "SKIP"]
	_say(f"  total elapsed {total:.0f}ms across {len(results)} step(s)")
	_say(f"  {len(results) - len(failures) - len(skipped)} PASS / {len(failures)} FAIL / {len(skipped)} SKIP")

	if state.fetched_client_id_seen:
		_say("")
		_say("  PHASE 2 ECHO SUPPRESSION — clientAssignedMessageId as returned by messages.get:")
		if state.fetched_client_id:
			_say(f"    PRESENT ({state.fetched_client_id})")
			_say("    Layer 1 works. An inbound event resolves to its client id and an index probe")
			_say("    decides echo-or-foreign, which is what the design assumes.")
		else:
			_say("    ABSENT")
			_say("    The design's primary echo path is unavailable. This is the single unproven")
			_say("    assumption Phase 2 shipped on; it is now disproven and needs a decision before")
			_say("    the relay is armed for real traffic.")

	if state.human_answers:
		_say("")
		_say("  Human observations, verbatim — quote these into the checkpoint:")
		for key, answer in state.human_answers.items():
			_say(f"    {key}: {answer!r}")

	_say("")
	if failures:
		_say("  RESULT: FAIL. Phase 1's checkpoint gate is not met.")
	else:
		_say("  RESULT: PASS. Now run it a second time — a re-run must not create a second space.")
	_say(_RULE)


def _wrap(text: str, width: int) -> list[str]:
	"""Wrap a detail string without pulling in ``textwrap``'s paragraph semantics."""
	words = str(text).split()
	lines: list[str] = []
	current = ""
	for word in words:
		candidate = f"{current} {word}".strip()
		if len(candidate) > width and current:
			lines.append(current)
			current = word
		else:
			current = candidate
	if current:
		lines.append(current)
	return lines


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(as_user: str = "", with_user: str = "", **_ignored: Any) -> dict[str, Any]:
	"""Run the eleven steps against real Google Chat and return a machine-readable summary.

	Args:
		as_user: Workspace email of the employee the relay impersonates. Every Chat call
			below is authored by this person, which is the whole thing step 5 checks.
		with_user: A second employee, added to the test space as a membership.
			``spaces:setup`` excludes the caller from ``memberships[]``, so this must not
			be the same address as ``as_user``.

	Returns:
		dict: ``{"result": "PASS"|"FAIL", "steps": [...], "human_answers": {...},
		"space": "..."}`` — bench prints it, and it is also what a future wrapper would
		assert on.

	Never raises for a *step* failure: a run that aborts halfway leaves an orphan space,
	so failures are recorded and the run walks to cleanup. It does raise for a bad
	argument, because there is nothing to clean up yet.
	"""
	as_user = (as_user or "").strip()
	with_user = (with_user or "").strip()
	if not as_user or not with_user:
		raise ValueError(
			"Both as_user and with_user are required:\n"
			"  bench --site <site> execute erpnext_enhancements.chat.gchat.smoke_test.run \\\n"
			'    --kwargs \'{"as_user": "a@domain", "with_user": "b@domain"}\''
		)
	if as_user.lower() == with_user.lower():
		raise ValueError(
			"as_user and with_user must differ — spaces:setup rejects a memberships[] entry "
			"for the calling user, and a SPACE with no other member proves nothing about "
			"membership anyway."
		)

	state = SmokeState(
		as_user=as_user,
		with_user=with_user,
		site=frappe.local.site,
		nonce=uuid.uuid4().hex[:8],
	)
	results: list[StepResult] = []

	def record(index: int, status: str, elapsed_ms: float, detail: str = "") -> None:
		results.append(StepResult(index, STEP_TITLES[index], status, elapsed_ms, detail))

	def skip(*indexes: int, reason: str) -> None:
		for index in indexes:
			if not any(result.index == index for result in results):
				record(index, "SKIP", 0.0, reason)

	def step(index: int, function: Callable[[], str]) -> bool:
		"""Run one step, time it, record it. ``True`` when it passed."""
		_heading(index, STEP_TITLES[index])
		started = time.perf_counter()
		try:
			detail = function()
		except SmokeAbort:
			raise
		except Exception as exc:
			elapsed = (time.perf_counter() - started) * 1000.0
			rendered = scrub_secrets(f"{type(exc).__name__}: {exc}")
			_say("")
			_say(f"  FAIL — {rendered}")
			record(index, "FAIL", elapsed, rendered)
			return False
		elapsed = (time.perf_counter() - started) * 1000.0
		_say("")
		_say(f"  PASS — {detail}")
		record(index, "PASS", elapsed, detail)
		return True

	_say("")
	_say(_RULE)
	_say("ERPNext ⇄ Google Chat — Phase 1 smoke test")
	_say(f"started {now()}  site={state.site}  run nonce={state.nonce}")
	_say(_RULE)

	client: GoogleChatClient | None = None
	aborted = ""

	try:
		if not step(1, lambda: _step_1_configuration(state)):
			raise SmokeAbort("configuration step failed; every later step would fail the same way")

		_reap_orphans(as_user)

		client = GoogleChatClient(subject=as_user, identity=AuthIdentity.USER)
		if client.dry_run:
			raise SmokeAbort(
				"the client resolved dry_run=True even though Chat Settings says otherwise. "
				"Refusing to continue: the transcript would be synthetic."
			)

		if not step(2, lambda: _step_2_token(state)):
			raise SmokeAbort("no credential, so nothing downstream can run")

		if not step(3, lambda: _step_3_space(state, client)):
			raise SmokeAbort("no space, so nothing downstream can run")

		if step(4, lambda: _step_4_post(state, client)):
			step(5, lambda: _step_5_human_authorship(state))
			step(6, lambda: _step_6_edit(state, client))
			step(7, lambda: _step_7_threaded_reply(state, client))
			step(8, lambda: _step_8_delete(state, client))
		else:
			skip(5, 6, 7, 8, reason="no message was posted")

		step(9, lambda: _step_9_inbound(state))

	except SmokeAbort as exc:
		aborted = scrub_secrets(str(exc))
		_say("")
		_say(f"  ABORTED — {aborted}")
		skip(2, 3, 4, 5, 6, 7, 8, 9, reason="run aborted before this step")

	finally:
		# Always. An orphan space in the domain is the one side effect this script must
		# never leave behind, and "the run crashed" is precisely when it would.
		step(11, lambda: _step_11_cleanup(state))
		results.sort(key=lambda result: result.index)
		_print_summary(results, state)

	failed = [result for result in results if result.status == "FAIL"]
	return {
		"result": "FAIL" if (failed or aborted) else "PASS",
		"aborted": aborted,
		"space": state.space_name,
		"human_answers": state.human_answers,
		"steps": [
			{
				"index": result.index,
				"title": result.title,
				"status": result.status,
				"elapsed_ms": round(result.elapsed_ms, 1),
				"detail": result.detail,
			}
			for result in results
		],
	}
