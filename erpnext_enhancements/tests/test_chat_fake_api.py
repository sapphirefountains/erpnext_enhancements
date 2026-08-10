# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for the fake Google Chat API — **the harness proving itself**.

Every other Phase 2 test is written against ``chat/testing/fake_chat.py``. That makes it the
one module where a wrong behaviour is not merely a bug but a *silent* bug: a fake that is
too permissive turns every downstream suite green while the relay it certifies is wrong
against the real API. So the harness carries its own tests, and they assert the behaviours
it exists to hold still rather than the ones that are convenient.

Plain pytest functions, not ``TestCase`` classes — the same shape as
``test_chat_gchat_client.py``, and it needs its **own** ``python -m pytest`` step in
``ci.yml``. ``python -m unittest`` silently collects zero function-style tests and reports
success; this repo has already shipped a suite that ran nowhere for weeks because of exactly
that, so the style is not a preference.

**No ``frappe`` stub is installed, deliberately.** ``fake_chat``, ``fixtures`` and the four
``gchat`` modules under test import neither ``frappe`` nor ``requests`` at module scope, and
:func:`test_the_harness_imports_with_frappe_and_requests_unavailable` asserts that by
blocking both imports and re-importing from scratch. It is the load-bearing precondition for
the whole suite: the moment a module-scope ``import frappe`` appears in the harness, the only
CI tier with automatic regression protection stops being able to reach it.

What each block defends, in one line each:

* **Quota** — membership writes and ``spaces.setup`` consume no per-space budget; a fake that
  charged them would throttle provisioning ~300× harder than the API does, and the relay
  would be built around a limit that is not real.
* **Idempotency** — ``requestId`` and ``messageId`` are orthogonal. Conflating them is how a
  replayed relay job posts a second copy of a message.
* **Tombstones** — Google's delete keeps the metadata and destroys the content, which is the
  entire reason ERPNext keeps the body on its own row.
* **The race** — the Workspace Event can arrive before the HTTP response that names the
  resource. That is §4.D, it is the hardest thing in the design, and a harness that could not
  reproduce it would leave the design untested at its weakest point.
"""

from __future__ import annotations

import builtins
import importlib
import json
import sys
from typing import Any

import pytest

from erpnext_enhancements.chat.gchat.client import (
	AuthIdentity,
	GoogleChatAPIError,
	GoogleChatClient,
	SpaceType,
	build_create_message_call,
	build_setup_space_call,
	message_alias,
)
from erpnext_enhancements.chat.testing import fixtures
from erpnext_enhancements.chat.testing.fake_chat import (
	APP_CALLER,
	ATTACHMENT_SOURCE_DRIVE_FILE,
	ATTACHMENT_SOURCE_UPLOADED_CONTENT,
	DEFAULT_CLOCK_START_MS,
	PROJECT_LIMITS,
	SPACE_WRITE_COST_MS,
	FakeChatAPI,
	FakeChatSettings,
	FakeClock,
	FakeReadTimeout,
	UnknownRoute,
	token_for,
)

ALICE = "alice@example.com"
BOB = "bob@example.com"
CAROL = "carol@example.com"


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _client(fake: FakeChatAPI, *, subject: str | None = ALICE, identity: Any = None) -> GoogleChatClient:
	"""A real ``GoogleChatClient`` wired to the fake. Real builders, real retry loop."""
	return GoogleChatClient(
		subject=subject,
		identity=identity or AuthIdentity.USER,
		dry_run=False,
		settings=FakeChatSettings(),
		token_provider=fake.token_provider,
		transport=fake,
	)


def _headers(subject: str | None = ALICE) -> dict[str, str]:
	return {"Authorization": f"Bearer {token_for(subject)}"}


def _url(path: str) -> str:
	"""A URL for a direct ``fake.request`` call.

	The host is deliberately a stand-in: the fake routes on **path and verb only**, exactly
	as the real service does, and this suite may not name the Chat API host (the guardrail
	confines it to ``gchat/client.py`` and ``gchat/auth.py``). Tests that need the real URL
	go through the client, which builds it.
	"""
	return f"https://{fixtures.CHAT_HOST_PLACEHOLDER}{path}"


def _client_id(n: int) -> str:
	"""A legal ``clientAssignedMessageId``: ``client-`` plus lowercase hex, well under 63."""
	return f"client-{n:032x}"


def _body(response: Any) -> dict[str, Any]:
	return json.loads(response.text) if response.text.strip() else {}


def _seeded_space(fake: FakeChatAPI) -> str:
	return fake.seed_space(display_name="Project Falcon", members=[ALICE, BOB])


def _create(
	fake: FakeChatAPI,
	space: str,
	*,
	n: int = 1,
	text: str = "hello",
	request_id: str = "",
	subject: str = ALICE,
) -> Any:
	"""One ``messages.create`` straight at the fake, bypassing the client's retry loop.

	Used wherever the assertion is about the *response* — the client would swallow a 429 into
	a retry and then an exception, which is right for production and useless for asserting a
	status code.
	"""
	return fake.request(
		"POST",
		_url(f"/v1/{space}/messages"),
		params={"messageId": _client_id(n), "requestId": request_id or f"req-{n}"},
		json={"text": text},
		headers=_headers(subject),
	)


# --------------------------------------------------------------------------------------
# the precondition
# --------------------------------------------------------------------------------------


def test_the_harness_imports_with_frappe_and_requests_unavailable() -> None:
	"""The bench-free tier is the only tier CI regression-tests. The harness must live in it.

	Asserted the only way that means anything: block both imports outright and re-import the
	modules from scratch. A module-scope ``import frappe`` in the fake would make every Phase
	2 test unrunnable in CI, and it would do it on the day somebody added a "quick" settings
	read to the harness.
	"""
	blocked = {"frappe", "requests"}
	real_import = builtins.__import__

	def guard(name: str, *args: Any, **kwargs: Any) -> Any:
		if name.split(".")[0] in blocked:
			raise ImportError(f"{name} is not installed in the bench-free tier")
		return real_import(name, *args, **kwargs)

	saved = {name: module for name, module in sys.modules.items() if name.split(".")[0] in blocked}
	for name in list(saved):
		del sys.modules[name]

	targets = [
		"erpnext_enhancements.chat.testing.fixtures",
		"erpnext_enhancements.chat.testing.fake_chat",
	]
	for name in targets:
		sys.modules.pop(name, None)

	builtins.__import__ = guard
	try:
		for name in targets:
			importlib.import_module(name)
	finally:
		builtins.__import__ = real_import
		sys.modules.update(saved)
		for name in targets:
			importlib.reload(importlib.import_module(name))


# --------------------------------------------------------------------------------------
# the transport contract
# --------------------------------------------------------------------------------------


def test_the_fake_is_a_drop_in_transport_for_the_real_client() -> None:
	"""Create, read back, edit, delete — through the real builders and the real ``_request``.

	This is the assertion that justifies the whole ``transport=`` design. Nothing here stubs
	the client: the URL, the query string, the idempotency keys and the ``client-`` alias are
	all produced by ``gchat/client.py``, so a builder that started emitting the wrong path
	would fail *here* rather than in production.
	"""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	client = _client(fake)

	created = client.create_message(space, _client_id(1), "req-1", "first")
	assert created["name"].startswith(f"{space}/messages/")
	assert created["text"] == "first"

	fetched = client.get_message(message_alias(space, _client_id(1)))
	assert fetched["name"] == created["name"]

	fake.clock.advance(SPACE_WRITE_COST_MS)
	edited = client.patch_message(message_alias(space, _client_id(1)), text="second")
	assert edited["text"] == "second"
	assert edited["lastUpdateTime"]

	fake.clock.advance(SPACE_WRITE_COST_MS)
	client.delete_message(message_alias(space, _client_id(1)))
	assert fake.message(created["name"])["deleteTime"]

	listed = client.list_messages(space)
	assert listed["messages"] == []


def test_the_response_object_exposes_exactly_what_the_client_reads() -> None:
	fake = FakeChatAPI()
	response = fake.request("GET", _url(f"/v1/{_seeded_space(fake)}"), headers=_headers())
	assert response.status_code == 200
	assert isinstance(response.text, str)
	assert dict(response.headers)
	assert response.content == response.text.encode("utf-8")


def test_an_unrouted_url_is_a_loud_harness_bug_not_a_404() -> None:
	"""A 404 would read as "Google says it does not exist". It means "the fake has no route"."""
	fake = FakeChatAPI()
	with pytest.raises(UnknownRoute):
		fake.request("GET", _url("/v1/spaces/AAAA/reactions"), headers=_headers())


def test_a_missing_bearer_token_is_401_before_any_validation() -> None:
	"""Auth is checked before request validation — observed on the live probe, where an
	invalid ``spaceType`` enum still came back 401. Order matters to whoever reads the
	failure: a 400 that is really a 401 sends them to the wrong file."""
	fake = FakeChatAPI()
	response = fake.request("POST", _url("/v1/spaces:setup"), json={"space": {"spaceType": "NONSENSE"}})
	assert response.status_code == 401
	assert _body(response)["error"]["status"] == "UNAUTHENTICATED"


# --------------------------------------------------------------------------------------
# quota
# --------------------------------------------------------------------------------------


def test_a_second_write_to_one_space_within_the_same_second_is_429() -> None:
	"""1 write per second per space — the binding limit of the entire outbound design."""
	fake = FakeChatAPI()
	space = _seeded_space(fake)

	assert _create(fake, space, n=1).status_code == 200

	fake.clock.advance(SPACE_WRITE_COST_MS - 1)
	assert _create(fake, space, n=2).status_code == 429

	fake.clock.advance(1)
	assert _create(fake, space, n=3).status_code == 200


def test_the_bucket_is_per_space_not_global() -> None:
	fake = FakeChatAPI()
	first = fake.seed_space(display_name="One")
	second = fake.seed_space(display_name="Two")
	assert _create(fake, first, n=1).status_code == 200
	assert _create(fake, second, n=2).status_code == 200


def test_the_429_is_the_aip_193_envelope_and_carries_no_retry_after() -> None:
	"""The byte shape, and the header that must **not** be there.

	``Retry-After`` appears zero times on the Chat quota documentation and zero times in
	``google-api-python-client``, which retries every 429 on pure exponential backoff. Phase
	1's ``parse_retry_after`` stays — harmless, and correct if one ever arrives — but nothing
	may depend on one, and a fake that supplied one would let something quietly start to.
	"""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	_create(fake, space, n=1)
	response = _create(fake, space, n=2)

	assert response.status_code == 429
	assert not any(key.lower() == "retry-after" for key in response.headers)

	# Two-space-indented pretty JSON, observed on the live probe. ?prettyPrint=false is
	# ignored on the error path, so this is not a formatting preference.
	assert '\n  "error"' in response.text

	error = _body(response)["error"]
	assert error["code"] == 429
	assert error["status"] == "RESOURCE_EXHAUSTED"
	detail = error["details"][0]
	assert detail["@type"] == "type.googleapis.com/google.rpc.ErrorInfo"
	assert detail["reason"] == "RATE_LIMIT_EXCEEDED"
	assert detail["domain"] == "googleapis.com"
	assert detail["metadata"]["method"] == "google.chat.v1.ChatService.CreateMessage"


def test_membership_writes_consume_no_per_space_budget() -> None:
	"""``PHASE2_VERIFIED.md`` §2 correction 1, and the reason it is worth its own test.

	``members.create`` / ``members.delete`` appear in no per-space row of the published quota
	table. Charging them against the space's single write per second would make membership
	reconciliation ~300× slower than the API allows and nothing would ever surface it as a
	bug — the sync would just be inexplicably slow.
	"""
	fake = FakeChatAPI()
	space = fake.seed_space(display_name="Team")

	for email in (BOB, CAROL, "dave@example.com", "erin@example.com"):
		response = fake.request(
			"POST",
			_url(f"/v1/{space}/members"),
			json={"member": {"name": f"users/{email}", "type": "HUMAN"}},
			headers=_headers(),
		)
		assert response.status_code == 200, f"{email} was throttled by a bucket it does not use"

	# And the space's write budget is untouched: a message still goes through immediately.
	assert _create(fake, space, n=1).status_code == 200


def test_setup_space_consumes_no_per_space_budget() -> None:
	"""Correction 2: there is no per-space bucket, because the space does not exist yet.

	A message into the freshly created space must therefore succeed in the same millisecond.
	"""
	fake = FakeChatAPI()
	call = build_setup_space_call(
		space_type=SpaceType.SPACE, request_id="req-setup", member_emails=[BOB], display_name="Falcon"
	)
	response = fake.request("POST", _url(call.path), json=dict(call.body or {}), headers=_headers())
	assert response.status_code == 200
	space = _body(response)["name"]
	assert _create(fake, space, n=1).status_code == 200


def test_the_project_space_write_bucket_is_sixty_per_minute() -> None:
	fake = FakeChatAPI()
	statuses = []
	for index in range(PROJECT_LIMITS["space_writes"] + 1):
		call = build_setup_space_call(
			space_type=SpaceType.SPACE,
			request_id=f"req-{index}",
			member_emails=[BOB],
			display_name=f"Space {index}",
		)
		statuses.append(
			fake.request("POST", _url(call.path), json=dict(call.body or {}), headers=_headers()).status_code
		)

	assert statuses[: PROJECT_LIMITS["space_writes"]] == [200] * PROJECT_LIMITS["space_writes"]
	assert statuses[-1] == 429


def test_a_project_window_reopens_after_sixty_seconds() -> None:
	"""Fixed windows, not a sliding one — which is what Google's "per 60 s" language means."""
	fake = FakeChatAPI()
	for index in range(PROJECT_LIMITS["space_writes"]):
		call = build_setup_space_call(
			space_type=SpaceType.SPACE,
			request_id=f"req-{index}",
			member_emails=[BOB],
			display_name=f"Space {index}",
		)
		fake.request("POST", _url(call.path), json=dict(call.body or {}), headers=_headers())

	fake.clock.advance(60_000)
	call = build_setup_space_call(
		space_type=SpaceType.SPACE, request_id="req-next", member_emails=[BOB], display_name="Next"
	)
	response = fake.request("POST", _url(call.path), json=dict(call.body or {}), headers=_headers())
	assert response.status_code == 200


def test_the_per_space_read_bucket_is_off_by_default_and_real_when_switched_on() -> None:
	"""15 reads/second/space is real; it defaults off so an ordinary arrange-act-assert with a
	frozen clock does not trip it for reasons unrelated to what it is testing. Inbound is the
	suite that must switch it on: one ``messages.get`` per event is exactly that throughput.
	"""
	relaxed = FakeChatAPI()
	space = relaxed.seed_space(display_name="Reads")
	for _ in range(20):
		assert relaxed.request("GET", _url(f"/v1/{space}"), headers=_headers()).status_code == 200

	strict = FakeChatAPI(enforce_space_read_quota=True)
	strict_space = strict.seed_space(display_name="Reads")
	assert strict.request("GET", _url(f"/v1/{strict_space}"), headers=_headers()).status_code == 200
	assert strict.request("GET", _url(f"/v1/{strict_space}"), headers=_headers()).status_code == 429


# --------------------------------------------------------------------------------------
# idempotency: requestId and messageId are orthogonal
# --------------------------------------------------------------------------------------


def test_a_request_id_replay_returns_the_original_message() -> None:
	"""No duplicate, and the *same* resource name — which is what makes a network retry safe."""
	fake = FakeChatAPI()
	space = _seeded_space(fake)

	first = _create(fake, space, n=1, request_id="req-replay", text="once")
	fake.clock.advance(SPACE_WRITE_COST_MS)
	second = _create(fake, space, n=1, request_id="req-replay", text="once")

	assert second.status_code == 200
	assert _body(second)["name"] == _body(first)["name"]
	assert len(fake.messages_in(space)) == 1


def test_a_message_id_collision_is_409_already_exists() -> None:
	"""``messageId`` is unique within a space **forever** — a hard, server-enforced constraint.

	``VERIFY:`` the status code was never observed. 409 / ``ALREADY_EXISTS`` is the
	AIP-compliant answer and is what this pins; it is a stated assumption, not a fact, and the
	first real collision should confirm or correct it.
	"""
	fake = FakeChatAPI()
	space = _seeded_space(fake)

	_create(fake, space, n=1, request_id="req-a")
	fake.clock.advance(SPACE_WRITE_COST_MS)
	collision = _create(fake, space, n=1, request_id="req-b")

	assert collision.status_code == 409
	assert _body(collision)["error"]["status"] == "ALREADY_EXISTS"
	assert len(fake.messages_in(space)) == 1


def test_request_id_and_message_id_are_orthogonal() -> None:
	"""Same ``requestId`` replays; same ``messageId`` with a different ``requestId`` collides.

	They are not two spellings of idempotency. ``messageId`` is permanent and server-enforced;
	``requestId``'s window is undocumented and cannot be relied on past an immediate retry.
	Conflating them is how a relay job replayed an hour later posts a second copy.
	"""
	fake = FakeChatAPI()
	space = _seeded_space(fake)

	original = _create(fake, space, n=1, request_id="req-1")
	fake.clock.advance(SPACE_WRITE_COST_MS)

	replay = _create(fake, space, n=1, request_id="req-1")
	assert _body(replay)["name"] == _body(original)["name"]
	fake.clock.advance(SPACE_WRITE_COST_MS)

	same_message_new_request = _create(fake, space, n=1, request_id="req-2")
	assert same_message_new_request.status_code == 409
	fake.clock.advance(SPACE_WRITE_COST_MS)

	new_message_same_request_scope = _create(fake, space, n=2, request_id="req-3")
	assert new_message_same_request_scope.status_code == 200
	assert len(fake.messages_in(space)) == 2


def test_a_message_id_stays_taken_after_the_message_is_deleted() -> None:
	""" "Unique within a space" has no expiry, and a tombstone does not free the id."""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	_create(fake, space, n=1)

	fake.clock.advance(SPACE_WRITE_COST_MS)
	fake.request("DELETE", _url(f"/v1/{message_alias(space, _client_id(1))}"), headers=_headers())

	fake.clock.advance(SPACE_WRITE_COST_MS)
	assert _create(fake, space, n=1, request_id="req-after-delete").status_code == 409


def test_an_illegal_client_message_id_is_rejected_server_side() -> None:
	"""The builder validates too. The fake validates independently because it is the *server*,
	and a check that exists only on the client is a check the server never made."""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	response = fake.request(
		"POST",
		_url(f"/v1/{space}/messages"),
		params={"messageId": "NOT-A-CLIENT-ID", "requestId": "req-1"},
		json={"text": "x"},
		headers=_headers(),
	)
	assert response.status_code == 400
	assert _body(response)["error"]["status"] == "INVALID_ARGUMENT"


# --------------------------------------------------------------------------------------
# tombstones
# --------------------------------------------------------------------------------------


def test_a_delete_leaves_a_tombstone_with_metadata_and_no_content() -> None:
	"""Google's tombstone is rich in metadata and **empty of content**.

	Which is exactly why ERPNext keeps the body on its own row after a soft delete: if it did
	not, nobody would have the text at all and the Phase 6 oversight path would be reading an
	empty string.
	"""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	created = _body(_create(fake, space, n=1, text="delete me"))

	fake.clock.advance(SPACE_WRITE_COST_MS)
	fake.request("DELETE", _url(f"/v1/{created['name']}"), params={"force": "true"}, headers=_headers())

	tombstone = fake.message(created["name"])
	assert tombstone["deleteTime"]
	assert tombstone["deletionMetadata"]["deletionType"] == "CREATOR_VIA_APP"
	assert "text" not in tombstone
	assert "argumentText" not in tombstone


def test_a_tombstone_is_invisible_to_list_without_show_deleted() -> None:
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	created = _body(_create(fake, space, n=1))
	fake.clock.advance(SPACE_WRITE_COST_MS)
	fake.request("DELETE", _url(f"/v1/{created['name']}"), headers=_headers())

	hidden = _body(fake.request("GET", _url(f"/v1/{space}/messages"), headers=_headers()))
	assert hidden["messages"] == []

	shown = _body(
		fake.request("GET", _url(f"/v1/{space}/messages"), params={"showDeleted": "true"}, headers=_headers())
	)
	assert [m["name"] for m in shown["messages"]] == [created["name"]]
	assert "text" not in shown["messages"][0]


def test_deletion_type_distinguishes_the_author_from_a_manager() -> None:
	"""``deletionMetadata.deletionType`` maps straight onto ``Chat Message.deletion_source``,
	so attribution is clean without guessing. ``VERIFY:`` whether an impersonated *manager*
	may delete another member's message at all is unproven — that blocks a Phase 6 moderation
	action, not Phase 2."""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	created = _body(_create(fake, space, n=1, subject=BOB))

	fake.clock.advance(SPACE_WRITE_COST_MS)
	fake.request("DELETE", _url(f"/v1/{created['name']}"), headers=_headers(ALICE))

	assert fake.message(created["name"])["deletionMetadata"]["deletionType"] == "SPACE_OWNER_VIA_APP"


# --------------------------------------------------------------------------------------
# spaces
# --------------------------------------------------------------------------------------


def test_setup_returns_the_existing_direct_message_rather_than_a_second_one() -> None:
	"""Documented behaviour, and the reason ``find_direct_message`` is a saving rather than a
	precondition — a read does not spend the 60-writes-per-minute space budget."""
	fake = FakeChatAPI()
	call = build_setup_space_call(space_type=SpaceType.DIRECT_MESSAGE, request_id="dm-1", member_emails=[BOB])
	first = _body(fake.request("POST", _url(call.path), json=dict(call.body or {}), headers=_headers()))

	again = build_setup_space_call(
		space_type=SpaceType.DIRECT_MESSAGE, request_id="dm-2", member_emails=[BOB]
	)
	second = _body(fake.request("POST", _url(again.path), json=dict(again.body or {}), headers=_headers()))

	assert second["name"] == first["name"]

	found = _body(
		fake.request(
			"GET", _url("/v1/spaces:findDirectMessage"), params={"name": f"users/{BOB}"}, headers=_headers()
		)
	)
	assert found["name"] == first["name"]


def test_find_direct_message_is_404_when_there_is_none() -> None:
	fake = FakeChatAPI()
	response = fake.request(
		"GET", _url("/v1/spaces:findDirectMessage"), params={"name": f"users/{CAROL}"}, headers=_headers()
	)
	assert response.status_code == 404


@pytest.mark.parametrize(
	("space_body", "memberships", "why"),
	[
		({"spaceType": "SPACE"}, [BOB], "a SPACE requires displayName"),
		(
			{"spaceType": "GROUP_CHAT", "displayName": "nope"},
			[BOB, CAROL],
			"a GROUP_CHAT must not set displayName",
		),
		({"spaceType": "GROUP_CHAT"}, [BOB], "a GROUP_CHAT needs at least 2 memberships"),
		(
			{"spaceType": "DIRECT_MESSAGE", "displayName": "nope"},
			[BOB],
			"a DIRECT_MESSAGE must not set displayName",
		),
		({"spaceType": "DIRECT_MESSAGE"}, [BOB, CAROL], "a DIRECT_MESSAGE needs exactly 1 membership"),
		({"spaceType": "DIRECT_MESSAGE"}, [], "a DIRECT_MESSAGE needs exactly 1 membership"),
		({"spaceType": "SPACE", "displayName": "ok"}, [ALICE], "the caller is added implicitly"),
	],
)
def test_setup_validation_is_enforced_server_side(
	space_body: dict[str, Any], memberships: list[str], why: str
) -> None:
	"""The three space types are three mutually exclusive contracts.

	Enforced here as well as in ``build_setup_space_call`` on purpose: the builder's checks are
	what the relay is *supposed* to do, and a suite that only ever exercised them could not
	tell a correct builder from a permissive server.
	"""
	fake = FakeChatAPI()
	response = fake.request(
		"POST",
		_url("/v1/spaces:setup"),
		json={
			"space": space_body,
			"requestId": "req-invalid",
			"memberships": [{"member": {"name": f"users/{email}", "type": "HUMAN"}} for email in memberships],
		},
		headers=_headers(),
	)
	assert response.status_code == 400, why
	assert _body(response)["error"]["status"] == "INVALID_ARGUMENT"


def test_a_threaded_space_cannot_be_requested() -> None:
	"""``spaceThreadingState`` is **Output only**. There is no create-time decision to get
	wrong because there is no way to ask: the discovery doc marks it ``readOnly``,
	``spaces.setup`` says verbatim that spaces with threaded replies aren't supported, and
	``spaces.patch``'s updateMask does not enumerate it."""
	fake = FakeChatAPI()
	response = fake.request(
		"POST",
		_url("/v1/spaces:setup"),
		json={
			"space": {
				"spaceType": "SPACE",
				"displayName": "Threads",
				"spaceThreadingState": "THREADED_MESSAGES",
			},
			"requestId": "req-threaded",
			"memberships": [],
		},
		headers=_headers(),
	)
	assert response.status_code == 400
	assert "spaceThreadingState" in _body(response)["error"]["message"]


def test_a_created_named_space_reports_grouped_messages() -> None:
	"""Read the state back rather than assuming which one a new space lands in.
	``GROUPED_MESSAGES`` is the only plausible reading and no document states it — which is
	why ``Chat Room.gchat_threading_state`` stores what the API returned."""
	fake = FakeChatAPI()
	call = build_setup_space_call(
		space_type=SpaceType.SPACE, request_id="req-named", member_emails=[BOB], display_name="Named"
	)
	space = _body(fake.request("POST", _url(call.path), json=dict(call.body or {}), headers=_headers()))
	assert space["spaceThreadingState"] == "GROUPED_MESSAGES"


def test_space_delete_is_refused_by_default() -> None:
	"""``chat.delete`` is not granted in V1 and must not be: the call cascades and removes a
	company's conversation history in one request. The fake answers the way an ungranted scope
	answers, so a code path that reaches it fails in CI rather than in production."""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	response = fake.request("DELETE", _url(f"/v1/{space}"), headers=_headers())
	assert response.status_code == 403
	assert fake.space(space) is not None


def test_media_upload_refuses_the_app_identity() -> None:
	"""``media.upload``'s scopes are exactly ``chat.import``, ``chat.messages``,
	``chat.messages.create`` — ``chat.bot`` is absent, so **a Chat app cannot upload at all**
	and the relay must impersonate the sending author. Download is the mirror image and *is*
	app-auth capable; the asymmetry is the whole attachment design."""
	fake = FakeChatAPI()
	space = _seeded_space(fake)

	refused = fake.request(
		"POST",
		_url(f"/upload/v1/{space}/attachments:upload"),
		params={"uploadType": "multipart"},
		headers=_headers(None),
		data=b"bytes",
	)
	assert refused.status_code == 403
	assert fake.calls[-1].caller == APP_CALLER

	# Even the refused upload spent the space's write slot: quota is charged on the way in,
	# before the handler decides the scope is wrong. Conservative, and stated as an assumption
	# on `_handle_create_message` — the fake is never looser than the API, only stricter.
	fake.clock.advance(SPACE_WRITE_COST_MS)

	uploaded = _body(
		fake.request(
			"POST",
			_url(f"/upload/v1/{space}/attachments:upload"),
			params={"uploadType": "multipart"},
			headers=_headers(ALICE),
			data=b"bytes",
		)
	)
	resource = uploaded["attachmentDataRef"]["resourceName"]

	downloaded = fake.request(
		"GET", _url(f"/v1/media/{resource}"), params={"alt": "media"}, headers=_headers(None)
	)
	assert downloaded.status_code == 200
	assert downloaded.content == b"bytes"


def test_media_upload_shares_the_per_space_write_bucket_with_create() -> None:
	"""Relaying one message with an attachment costs the space **two** seconds, not one."""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	assert (
		fake.request(
			"POST",
			_url(f"/upload/v1/{space}/attachments:upload"),
			headers=_headers(),
			data=b"x",
		).status_code
		== 200
	)
	assert _create(fake, space, n=1).status_code == 429
	fake.clock.advance(SPACE_WRITE_COST_MS)
	assert _create(fake, space, n=1).status_code == 200


# --------------------------------------------------------------------------------------
# Message.attachment[] — the shape the inbound parser exists to read
# --------------------------------------------------------------------------------------
#
# This block is the harness paying off a debt. The fake emitted no `attachment` field at all,
# so an inbound attachment could only be expressed by decorating a fetched resource at the
# client seam — which reads as a gap in the pipeline's coverage when it is a gap in the
# harness. A fake that cannot produce the shape cannot test the parser.


def _upload(fake: FakeChatAPI, space: str, name: str = "site-photo.png", content: bytes = b"PNGDATA") -> str:
	"""Upload through the **real** client and return the ``attachmentUploadToken``.

	Through the client rather than a hand-built body, so the multipart the fake parses is the
	multipart ``build_upload_attachment_call`` produces. A hand-rolled fixture here would let
	the two drift and the drift would surface as an attachment with no ``contentName``.
	"""
	response = _client(fake).upload_attachment(space, name, content=content)
	return str(response["attachmentDataRef"]["attachmentUploadToken"])


def _create_with_attachment(fake: FakeChatAPI, space: str, token: str, *, n: int = 1) -> Any:
	return fake.request(
		"POST",
		_url(f"/v1/{space}/messages"),
		params={"messageId": _client_id(n), "requestId": f"req-{n}"},
		json={
			"text": "here is the drawing",
			"attachment": [{"attachmentDataRef": {"attachmentUploadToken": token}}],
		},
		headers=_headers(),
	)


def test_a_message_with_no_attachments_omits_the_field_entirely() -> None:
	"""``Message.attachment`` is ``Optional`` and Google omits it — it is never ``[]``.

	The distinction is not pedantry: ``plan_inbound_attachments`` reads
	``resource.get("attachment") or []``, and a fake that always emitted an empty list would
	make "a message with no attachments plans nothing" pass for the wrong reason.
	"""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	created = _body(_create(fake, space))
	assert "attachment" not in created
	fetched = fake.message(created["name"]) or {}
	assert "attachment" not in fetched


def test_an_uploaded_attachment_appears_on_create_get_and_list() -> None:
	"""The documented upload→create binding, rendered back on all three read paths.

	``AttachmentDataRef.attachmentUploadToken`` is *"an opaque token containing a reference to
	an uploaded attachment… used to create or update Chat messages with attachments"*, and
	that pair is the only binding Google documents. What comes back is an ``Attachment`` whose
	``source`` is ``UPLOADED_CONTENT`` and whose ``attachmentDataRef.resourceName`` is the
	handle ``media.download`` accepts — which is exactly the ladder the inbound ingest walks.
	"""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	token = _upload(fake, space, "site-photo.png", b"PNGDATA")
	fake.clock.advance(SPACE_WRITE_COST_MS)

	created = _body(_create_with_attachment(fake, space, token))
	attachment = created["attachment"][0]
	assert attachment["source"] == ATTACHMENT_SOURCE_UPLOADED_CONTENT
	assert attachment["contentName"] == "site-photo.png", "the filename came off the multipart metadata"
	assert attachment["name"].startswith(created["name"] + "/attachments/")
	assert "driveDataRef" not in attachment

	data_ref = attachment["attachmentDataRef"]["resourceName"]
	assert fake.attachment(data_ref) == b"PNGDATA"

	assert (fake.message(created["name"]) or {})["attachment"] == created["attachment"]
	listed = _body(fake.request("GET", _url(f"/v1/{space}/messages"), headers=_headers()))
	assert listed["messages"][0]["attachment"] == created["attachment"]


def test_a_drive_attachment_is_seeded_and_carries_only_a_drive_data_ref() -> None:
	"""``DRIVE_FILE`` is not creatable through the API, so the fake seeds it instead.

	``Message.attachment`` is documented as *user-uploaded*: a Drive attachment is something a
	human's native client produces, and a fake that let ``messages.create`` mint one would
	teach a relay to make a call the real API refuses. Seeding says "a coworker attached a
	Drive file" without pretending the API can.

	The row it produces is the one with the sharp edge: **no data ref**, because
	``media.download`` cannot fetch a Drive file at all, which is what makes "link, never
	copy" a fact about the API and not only a policy of ours.
	"""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	message = fake.seed_message(space, text="the spec lives in Drive")
	name = fake.seed_attachment(
		message,
		source=ATTACHMENT_SOURCE_DRIVE_FILE,
		content_name="Pump schedule.xlsx",
		content_type="application/vnd.google-apps.spreadsheet",
		drive_file_id="1AbCdEfGhIjK",
	)

	attachment = (fake.message(message) or {})["attachment"][0]
	assert attachment["name"] == name
	assert attachment["source"] == ATTACHMENT_SOURCE_DRIVE_FILE
	assert attachment["driveDataRef"] == {"driveFileId": "1AbCdEfGhIjK"}
	assert "attachmentDataRef" not in attachment, "a Drive file has no media.download handle"


def test_every_attachment_carries_the_human_only_uris_the_parser_must_ignore() -> None:
	"""``downloadUri`` and ``thumbnailUri`` are emitted **on purpose**.

	They are human, browser-session URLs a bearer token cannot use, and the failure they
	produce is the worst kind — a download built on one works when a developer pastes it into
	a logged-in browser and 401s in every job. Omitting them from the fixture would let
	``test_the_planner_never_returns_a_download_uri_or_a_thumbnail_uri`` pass vacuously.
	"""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	message = fake.seed_message(space)
	fake.seed_attachment(message, content_name="scan.png", content_type="image/png", content=b"x")
	attachment = (fake.message(message) or {})["attachment"][0]
	assert attachment["downloadUri"] and attachment["thumbnailUri"]


def test_a_hand_written_attachment_on_create_is_refused() -> None:
	"""No upload token, no attachment. The real API takes user-uploaded content and nothing else."""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	refused = fake.request(
		"POST",
		_url(f"/v1/{space}/messages"),
		params={"messageId": _client_id(1), "requestId": "req-1"},
		json={"text": "hi", "attachment": [{"source": ATTACHMENT_SOURCE_DRIVE_FILE}]},
		headers=_headers(),
	)
	assert refused.status_code == 400
	assert "attachmentUploadToken" in _body(refused)["error"]["message"]


def test_an_upload_token_the_fake_never_issued_is_refused() -> None:
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	refused = _create_with_attachment(fake, space, "upload-not-a-real-token")
	assert refused.status_code == 400
	assert _body(refused)["error"]["status"] == "INVALID_ARGUMENT"


def test_the_upload_token_is_not_consumed_by_the_create() -> None:
	"""A retried relay job re-uploads and gets a fresh token; nothing here should punish reuse.

	``media.upload`` takes no ``requestId``, so idempotency on an attachment message rests
	entirely on ``messageId``. A fake that burned the token would make a retry fail for a
	reason the API does not have, and the relay would grow a workaround for a fiction.
	"""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	token = _upload(fake, space)
	fake.clock.advance(SPACE_WRITE_COST_MS)
	assert _create_with_attachment(fake, space, token, n=1).status_code == 200
	fake.clock.advance(SPACE_WRITE_COST_MS)
	second = _create_with_attachment(fake, space, token, n=2)
	assert second.status_code == 200
	assert _body(second)["attachment"][0]["attachmentDataRef"]["resourceName"]


def test_a_tombstone_drops_the_attachment_array_with_the_text() -> None:
	"""An attachment is content, and Google's tombstone keeps only metadata.

	Same fact as the missing ``text``, and the same consequence: after a delete, ERPNext is
	the only party that still knows what was attached.
	"""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	message = fake.seed_message(space, sender=ALICE, text="scan attached")
	fake.seed_attachment(message, content_name="scan.png", content_type="image/png", content=b"x")

	deleted = fake.request(
		"DELETE", _url(f"/v1/{message}"), params={"force": "true"}, headers=_headers(ALICE)
	)
	assert deleted.status_code == 200
	tombstone = fake.message(message) or {}
	assert "attachment" not in tombstone
	assert "text" not in tombstone
	assert tombstone["deletionMetadata"]["deletionType"] == "CREATOR_VIA_APP"


def test_attachment_names_are_scoped_to_their_own_message() -> None:
	"""``Chat Attachment.gchat_attachment_name`` is a **unique index**.

	A fake that minted a name not varying per message would make two coworkers' attachments
	collide on that index, and the second would be silently absorbed as a duplicate — the
	exact class of loss this phase's dedupe rules are supposed to make impossible.
	"""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	first = fake.seed_message(space, text="one")
	second = fake.seed_message(space, text="two")
	a = fake.seed_attachment(first, content_name="scan.png", content=b"x")
	b = fake.seed_attachment(second, content_name="scan.png", content=b"x")
	assert a != b
	assert a.startswith(first + "/attachments/") and b.startswith(second + "/attachments/")


def test_seeding_bytes_behind_a_drive_attachment_is_a_loud_harness_error() -> None:
	"""There is no ``media.download`` handle for a Drive file, so bytes here are a test bug.

	Raised rather than quietly ignored: a harness that accepted them would let somebody write
	an ingest that copies Drive bytes and watch the test go green.
	"""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	message = fake.seed_message(space)
	with pytest.raises(AssertionError):
		fake.seed_attachment(
			message, source=ATTACHMENT_SOURCE_DRIVE_FILE, drive_file_id="1AbC", content=b"nope"
		)


def test_seeding_an_attachment_on_a_message_that_does_not_exist_is_refused() -> None:
	fake = FakeChatAPI()
	with pytest.raises(AssertionError):
		fake.seed_attachment("spaces/AAAA1/messages/nope", content_name="x.png")


def test_an_uploaded_attachment_survives_an_edit() -> None:
	"""``updateMask=text`` is the only path user auth may set, so a patch cannot drop files."""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	token = _upload(fake, space)
	fake.clock.advance(SPACE_WRITE_COST_MS)
	created = _body(_create_with_attachment(fake, space, token))
	fake.clock.advance(SPACE_WRITE_COST_MS)

	patched = _body(
		fake.request(
			"PATCH",
			_url(f"/v1/{created['name']}"),
			params={"updateMask": "text"},
			json={"text": "here is the drawing (corrected)"},
			headers=_headers(),
		)
	)
	assert patched["text"].endswith("(corrected)")
	assert patched["attachment"] == created["attachment"]


# --------------------------------------------------------------------------------------
# clientAssignedMessageId — the unproven field
# --------------------------------------------------------------------------------------


def test_messages_get_returns_the_client_assigned_id() -> None:
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	created = _body(_create(fake, space, n=7))
	fetched = _body(fake.request("GET", _url(f"/v1/{created['name']}"), headers=_headers()))
	assert fetched["clientAssignedMessageId"] == _client_id(7)


def test_the_client_id_switch_makes_the_echo_fallback_path_testable() -> None:
	"""``VERIFY:`` whether the real API populates ``clientAssignedMessageId`` on a read is
	**unproven** — the proto marks it OPTIONAL rather than OUTPUT_ONLY, so the design is not
	inverted, but no Google document or sample shows it populated in a response. Echo
	suppression's primary path depends on it. ``return_client_ids=False`` models the bad
	answer, which is the only way the bounded fallback ladder can be tested before one live
	round trip settles it."""
	fake = FakeChatAPI(return_client_ids=False)
	space = _seeded_space(fake)
	created = _body(_create(fake, space, n=7))
	fetched = _body(fake.request("GET", _url(f"/v1/{created['name']}"), headers=_headers()))

	assert "clientAssignedMessageId" not in fetched
	# The alias must still resolve: the server knows the id, it just does not hand it back.
	aliased = fake.request("GET", _url(f"/v1/{message_alias(space, _client_id(7))}"), headers=_headers())
	assert _body(aliased)["name"] == created["name"]


def test_last_update_time_is_absent_until_the_message_is_edited() -> None:
	"""Rule 3 (LAST-WRITER-WINS by ``lastUpdateTime``) treats missing as "ERPNext wins", so a
	fake that always populated it would make the tie-break branch unreachable."""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	created = _body(_create(fake, space, n=1))
	assert "lastUpdateTime" not in created

	fake.clock.advance(SPACE_WRITE_COST_MS)
	edited = _body(
		fake.request(
			"PATCH",
			_url(f"/v1/{created['name']}"),
			params={"updateMask": "text", "allowMissing": "false"},
			json={"text": "edited"},
			headers=_headers(),
		)
	)
	assert edited["lastUpdateTime"]


def test_patch_with_allow_missing_upserts_and_emits_a_created_event() -> None:
	"""``allowMissing=true`` turns an out-of-order or replayed edit into a create rather than
	a 404 — the safety net under Rule 1 (CREATE-BEFORE-EDIT), not its mechanism."""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	response = fake.request(
		"PATCH",
		_url(f"/v1/{message_alias(space, _client_id(9))}"),
		params={"updateMask": "text", "allowMissing": "true"},
		json={"text": "upserted"},
		headers=_headers(),
	)
	assert response.status_code == 200
	assert _body(response)["text"] == "upserted"

	types = [event["message"]["attributes"]["ce-type"] for event in fake.drain_events()]
	assert types == [fixtures.EVENT_MESSAGE_CREATED]


# --------------------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------------------


def test_every_event_is_a_two_layer_envelope_carrying_a_name_and_no_body() -> None:
	"""``includeResource: false`` is what buys the 7-day subscription TTL, and its consequence
	shapes the whole inbound pipeline: an event carries only a resource name, so every inbound
	event costs one ``spaces.messages.get``."""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	_create(fake, space, n=1, text="secret contents")

	events = fake.drain_events()
	assert len(events) == 1
	envelope = events[0]

	assert set(envelope) == {"message"}
	assert set(envelope["message"]) == {"attributes", "data", "messageId", "orderingKey", "publishTime"}

	attributes = envelope["message"]["attributes"]
	assert attributes["ce-type"] == fixtures.EVENT_MESSAGE_CREATED
	assert attributes["ce-id"].startswith(f"{space}/spaceEvents/")
	assert attributes["ce-subject"] == space
	assert attributes["ce-specversion"] == fixtures.CE_SPECVERSION

	payload = fixtures.decode_data(envelope["message"]["data"])
	assert set(payload) == {"message"}
	assert set(payload["message"]) == {"name"}
	assert "secret contents" not in json.dumps(envelope)


def test_each_mutation_emits_its_own_event_type() -> None:
	fake = FakeChatAPI(emit_setup_membership_events=False)
	space = _seeded_space(fake)
	created = _body(_create(fake, space, n=1))

	fake.clock.advance(SPACE_WRITE_COST_MS)
	fake.request(
		"PATCH",
		_url(f"/v1/{created['name']}"),
		params={"updateMask": "text", "allowMissing": "false"},
		json={"text": "edited"},
		headers=_headers(),
	)
	fake.clock.advance(SPACE_WRITE_COST_MS)
	fake.request("DELETE", _url(f"/v1/{created['name']}"), headers=_headers())
	fake.request(
		"POST",
		_url(f"/v1/{space}/members"),
		json={"member": {"name": f"users/{CAROL}"}},
		headers=_headers(),
	)

	assert [event["message"]["attributes"]["ce-type"] for event in fake.drain_events()] == [
		fixtures.EVENT_MESSAGE_CREATED,
		fixtures.EVENT_MESSAGE_UPDATED,
		fixtures.EVENT_MESSAGE_DELETED,
		fixtures.EVENT_MEMBERSHIP_CREATED,
	]


def test_drain_consumes_and_pending_does_not() -> None:
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	_create(fake, space, n=1)

	assert len(fake.pending_events()) == 1
	assert len(fake.pending_events()) == 1
	assert len(fake.drain_events()) == 1
	assert fake.pending_events() == ()
	assert fake.event_count() == 0


def test_duplicate_delivery_repeats_the_identical_envelope() -> None:
	"""Pub/Sub at-least-once redelivers the **same** message. A redelivery with a fresh id
	would be caught by accident; structural dedupe on ``unique(gchat_message_name)`` has to be
	what catches this one."""
	fake = FakeChatAPI()
	fake.duplicate_events()
	space = _seeded_space(fake)
	_create(fake, space, n=1)

	events = fake.drain_events()
	assert len(events) == 2
	assert events[0] == events[1]
	assert events[0] is not events[1]


def test_duplicate_delivery_is_scoped_to_one_method() -> None:
	fake = FakeChatAPI()
	fake.duplicate_events("spaces.members.create")
	space = _seeded_space(fake)
	_create(fake, space, n=1)
	fake.request(
		"POST", _url(f"/v1/{space}/members"), json={"member": {"name": f"users/{CAROL}"}}, headers=_headers()
	)

	types = [event["message"]["attributes"]["ce-type"] for event in fake.drain_events()]
	assert types == [
		fixtures.EVENT_MESSAGE_CREATED,
		fixtures.EVENT_MEMBERSHIP_CREATED,
		fixtures.EVENT_MEMBERSHIP_CREATED,
	]


def test_out_of_order_delivery_releases_the_held_event_after_the_next_one() -> None:
	"""Pub/Sub does not guarantee ordering without an ordering key, so an edit can be delivered
	before the create it edits. Inbound must apply an ``updated`` event for an unknown resource
	name as a create from the fetched resource — Rule 1 on the inbound side."""
	fake = FakeChatAPI(emit_setup_membership_events=False)
	fake.deliver_out_of_order("spaces.messages.create")
	space = _seeded_space(fake)

	created = _body(_create(fake, space, n=1))
	assert fake.event_count() == 1

	fake.clock.advance(SPACE_WRITE_COST_MS)
	fake.request(
		"PATCH",
		_url(f"/v1/{created['name']}"),
		params={"updateMask": "text", "allowMissing": "false"},
		json={"text": "edited"},
		headers=_headers(),
	)

	assert [event["message"]["attributes"]["ce-type"] for event in fake.drain_events()] == [
		fixtures.EVENT_MESSAGE_UPDATED,
		fixtures.EVENT_MESSAGE_CREATED,
	]


def test_a_held_event_is_never_lost_even_with_nothing_behind_it() -> None:
	fake = FakeChatAPI()
	fake.deliver_out_of_order()
	space = _seeded_space(fake)
	_create(fake, space, n=1)
	assert len(fake.drain_events()) == 1


def test_global_out_of_order_swaps_pairs_rather_than_holding_everything() -> None:
	"""Holding every event would preserve the original order at drain time and reorder
	nothing — the mode would look armed and do nothing, which is the worst thing a fault
	injector can do."""
	fake = FakeChatAPI(emit_setup_membership_events=False)
	fake.deliver_out_of_order()
	first = fake.seed_space(display_name="One")
	second = fake.seed_space(display_name="Two")

	_create(fake, first, n=1)
	_create(fake, second, n=2)

	subjects = [event["message"]["attributes"]["ce-subject"] for event in fake.drain_events()]
	assert subjects == [second, first]


# --------------------------------------------------------------------------------------
# the §4.D race
# --------------------------------------------------------------------------------------


def test_race_on_create_publishes_the_event_before_the_response_returns() -> None:
	"""The hardest thing in the design, given a first-class API so that it gets tested.

	At the moment ``during`` runs, Google has committed the message and published the event,
	and the relay has **not** received the resource name — so it cannot have written
	``gchat_message_name``. Inbound therefore cannot recognise the message structurally and
	must survive on the ``client-`` id alone, or the mirror duplicates every message it sends
	whenever Google is faster than the response.
	"""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	observed: list[dict[str, Any]] = []

	def inbound_worker(api: FakeChatAPI) -> None:
		observed.extend(api.drain_events())

	fake.race_on_create(space, during=inbound_worker, delay_ms=250)

	response = _create(fake, space, n=1)

	assert observed, "the event must be visible to an inbound worker before the response returns"
	payload = fixtures.decode_data(observed[0]["message"]["data"])
	assert payload["message"]["name"] == _body(response)["name"]
	# The relay has the name only now, after the inbound worker already saw it.
	assert fake.drain_events() == []


def test_the_race_advances_the_clock_by_the_configured_delay() -> None:
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	before = fake.clock.now_ms()
	fake.race_on_create(space, delay_ms=750)
	_create(fake, space, n=1)
	assert fake.clock.now_ms() == before + 750


def test_the_race_is_armed_for_one_space_and_a_fixed_number_of_creates() -> None:
	fake = FakeChatAPI()
	raced = fake.seed_space(display_name="Raced")
	calm = fake.seed_space(display_name="Calm")
	hits: list[str] = []

	fake.race_on_create(raced, during=lambda _api: hits.append("raced"), times=1)

	_create(fake, raced, n=1)
	_create(fake, calm, n=2)
	fake.clock.advance(SPACE_WRITE_COST_MS)
	_create(fake, raced, n=3)

	assert hits == ["raced"]


# --------------------------------------------------------------------------------------
# fault injection, through the real retry loop
# --------------------------------------------------------------------------------------


def test_timeout_then_success_is_retried_by_the_real_retry_loop() -> None:
	"""The fault is raised as a ``TimeoutError`` subclass, so ``backoff.classify_error``
	recognises it through the same MRO scan it uses for ``requests``' exception tree — no
	special case and no allow-list entry to keep in sync."""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	fake.fail_with_timeout("spaces.messages.create", times=1)

	created = _client(fake).create_message(space, _client_id(1), "req-1", "eventually")
	assert created["text"] == "eventually"
	# The timed-out attempt is journalled with a `None` status, so "the relay tried twice" is
	# visible rather than inferred from the absence of a row.
	assert [call.status for call in fake.calls] == [None, 200]


def test_n_consecutive_5xx_then_success() -> None:
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	fake.fail_with_server_error("spaces.messages.create", times=2, status=503)

	created = _client(fake).create_message(space, _client_id(1), "req-1", "third time")
	assert created["text"] == "third time"
	assert [call.status for call in fake.calls] == [503, 503, 200]


def test_a_5xx_budget_that_outlasts_the_retry_budget_raises() -> None:
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	fake.fail_with_server_error("spaces.messages.create", times=99, status=500)

	with pytest.raises(GoogleChatAPIError) as caught:
		_client(fake).create_message(space, _client_id(1), "req-1", "never")
	assert caught.value.status == 500
	assert len(fake.calls) == FakeChatSettings().relay_max_attempts


def test_a_forced_429_is_scoped_to_one_method() -> None:
	"""Not redundant with the quota model. Google publishes two caveats no design can remove —
	the backend may 429 a request that is inside every published limit — so **staying under 1
	write/second does not guarantee no 429**. The bucket is an optimisation; backoff is the
	correctness mechanism."""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	fake.fail_with_rate_limit("spaces.messages.create", times=1)

	throttled = _create(fake, space, n=1)
	assert throttled.status_code == 429
	assert fake.request("GET", _url(f"/v1/{space}"), headers=_headers()).status_code == 200

	assert _create(fake, space, n=1).status_code == 200


def test_a_timeout_after_processing_leaves_the_message_created_and_the_retry_replays_it() -> None:
	"""The failure ``requestId`` exists for: the server did the work and the answer got lost.

	The retry must return the **original** message, not create a second one. This is the whole
	argument for sending both idempotency keys on every create.
	"""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	fake.fail_with_timeout("spaces.messages.create", times=1, after_processing=True)

	with pytest.raises(FakeReadTimeout):
		_create(fake, space, n=1, request_id="req-lost")

	# The message exists and the event was published; only the answer was lost.
	assert len(fake.messages_in(space)) == 1
	assert len(fake.pending_events()) == 1

	fake.clock.advance(SPACE_WRITE_COST_MS)
	retry = _create(fake, space, n=1, request_id="req-lost")
	assert retry.status_code == 200
	assert _body(retry)["name"] == fake.messages_in(space)[0]["name"]
	assert len(fake.messages_in(space)) == 1
	# And the replay published no second event — the relay's retry is invisible downstream.
	assert len(fake.pending_events()) == 1


def test_a_delayed_response_advances_the_clock_instead_of_sleeping() -> None:
	"""Which is what makes a 30-second stall free to test — and visible to the quota buckets
	exactly as real elapsed time would be."""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	fake.delay_response("spaces.messages.create", ms=30_000, times=1)

	before = fake.clock.now_ms()
	_create(fake, space, n=1)
	assert fake.clock.now_ms() == before + 30_000

	# 30 seconds later the space's write bucket has long since reopened.
	assert _create(fake, space, n=2).status_code == 200


def test_clear_faults_disarms_everything_and_touches_nothing_else() -> None:
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	_create(fake, space, n=1)
	fake.fail_with_rate_limit("spaces.messages.create", times=5)
	fake.duplicate_events()

	fake.clear_faults()
	fake.clock.advance(SPACE_WRITE_COST_MS)

	assert _create(fake, space, n=2).status_code == 200
	assert len(fake.messages_in(space)) == 2
	assert len(fake.drain_events()) == 2


# --------------------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------------------


def test_two_fakes_with_the_same_seed_produce_identical_names_and_events() -> None:
	"""A flaky harness is worse than no harness: it teaches people to re-run."""

	def run() -> tuple[list[str], list[dict[str, Any]]]:
		fake = FakeChatAPI()
		space = _seeded_space(fake)
		_create(fake, space, n=1)
		fake.clock.advance(SPACE_WRITE_COST_MS)
		_create(fake, space, n=2)
		return [m["name"] for m in fake.messages_in(space)], fake.drain_events()

	assert run() == run()


def test_the_clock_never_moves_on_its_own() -> None:
	fake = FakeChatAPI()
	start = fake.clock.now_ms()
	space = _seeded_space(fake)
	_create(fake, space, n=1)
	fake.request("GET", _url(f"/v1/{space}"), headers=_headers())
	assert fake.clock.now_ms() == start


def test_request_latency_advances_the_clock_when_a_test_asks_for_it() -> None:
	fake = FakeChatAPI(request_latency_ms=400)
	start = fake.clock.now_ms()
	space = _seeded_space(fake)
	fake.request("GET", _url(f"/v1/{space}"), headers=_headers())
	fake.request("GET", _url(f"/v1/{space}"), headers=_headers())
	assert fake.clock.now_ms() == start + 800


def test_the_clock_refuses_to_run_backwards() -> None:
	"""A rewound clock silently refunds spent quota, which would make every bucket assertion
	meaningless in a way nobody would notice."""
	with pytest.raises(ValueError):
		FakeClock().advance(-1)


def test_the_call_journal_records_no_message_body() -> None:
	"""Same discipline as ``client.build_log_record``: length and a truncated hash, never the
	text. A harness that made it convenient to assert on bodies would make it convenient to log
	them, and chat content is employee-private."""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	_create(fake, space, n=1, text="employee-private contents")

	entry = fake.calls[-1]
	assert entry.google_method == "spaces.messages.create"
	assert entry.text_bytes == len(b"employee-private contents")
	assert "employee" not in json.dumps(entry.__dict__, default=str)


def test_the_bearer_token_never_reaches_the_journal() -> None:
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	_create(fake, space, n=1)
	assert "fake-token" not in json.dumps([call.__dict__ for call in fake.calls], default=str)


# --------------------------------------------------------------------------------------
# fixtures — what the inbound parser will be tested against
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("event_type", sorted(fixtures.ALL_EVENT_FACTORIES))
def test_every_fixture_is_a_well_formed_two_layer_envelope(event_type: str) -> None:
	envelope = fixtures.ALL_EVENT_FACTORIES[event_type]()

	assert set(envelope) == {"message"}
	assert set(envelope["message"]) == {"attributes", "data", "messageId", "orderingKey", "publishTime"}

	attributes = envelope["message"]["attributes"]
	assert attributes["ce-type"] == event_type
	assert attributes["ce-source"] == (
		f"//{fixtures.EVENTS_HOST}/subscriptions/{fixtures.DEFAULT_SUBSCRIPTION_ID}"
	)
	assert attributes["ce-specversion"] == "1.0"
	assert attributes["ce-datacontenttype"] == "application/json"

	payload = fixtures.decode_data(envelope["message"]["data"])
	assert isinstance(payload, dict) and payload


@pytest.mark.parametrize(
	"factory",
	[
		fixtures.message_created_event,
		fixtures.message_updated_event,
		fixtures.message_deleted_event,
		fixtures.membership_created_event,
		fixtures.membership_deleted_event,
		fixtures.space_updated_event,
	],
)
def test_a_chat_resource_payload_carries_a_name_and_nothing_else(factory: Any) -> None:
	payload = fixtures.decode_data(factory()["message"]["data"])
	(resource,) = payload.values()
	assert set(resource) == {"name"}
	assert resource["name"].startswith("spaces/")


@pytest.mark.parametrize(
	"factory",
	[
		fixtures.subscription_expiration_reminder_event,
		fixtures.subscription_suspended_event,
		fixtures.subscription_expired_event,
	],
)
def test_lifecycle_payloads_are_snake_case_and_name_their_subscription(factory: Any) -> None:
	"""Lifecycle payloads use **snake_case** inside the subscription object while every Chat
	resource event uses camelCase. A parser that normalises one convention reads ``None`` from
	the other — and the field it reads ``None`` from is ``expire_time``, which is what the
	renewal scheduler derives its period from."""
	envelope = factory()
	assert envelope["message"]["attributes"]["ce-type"] in fixtures.LIFECYCLE_EVENT_TYPES

	subscription = fixtures.decode_data(envelope["message"]["data"])["subscription"]
	assert subscription["name"].startswith("subscriptions/")
	assert "expire_time" in subscription
	assert "notification_endpoint" in subscription
	assert not any(key != key.lower() for key in subscription), "lifecycle payloads are snake_case"


def test_a_lifecycle_event_is_not_addressed_to_a_space() -> None:
	envelope = fixtures.subscription_expired_event()
	assert envelope["message"]["attributes"]["ce-subject"].startswith("subscriptions/")


def test_the_malformed_fixtures_are_genuinely_malformed() -> None:
	"""Every one of these must reach the parser's named-failure path.

	The consequence of getting it wrong is disproportionate: the Pub/Sub consumer acks or
	nacks per message, and an unhandled exception on one poison payload nacks it forever.
	"""
	for why, envelope in fixtures.malformed_events():
		message = envelope.get("message") or {}
		attributes = message.get("attributes") or {}
		data = message.get("data")

		broken = False
		if not message or data is None:
			broken = True
		elif attributes.get("ce-type") not in fixtures.CHAT_EVENT_TYPES:
			broken = True
		else:
			try:
				payload = fixtures.decode_data(str(data))
			except ValueError:
				broken = True
			else:
				resource = next(iter(payload.values()), {})
				broken = not isinstance(resource, dict) or not resource.get("name")

		assert broken, f"the fixture for {why!r} is not actually malformed"


def test_the_envelope_helper_round_trips_any_inner_payload() -> None:
	inner = {"anything": {"name": "spaces/X/messages/Y", "nested": [1, 2, 3]}}
	envelope = fixtures.pubsub_envelope(
		event_type=fixtures.EVENT_MESSAGE_CREATED,
		data=inner,
		subject="spaces/X",
		event_id=fixtures.space_event_id("spaces/X", "evt1"),
	)
	assert fixtures.decode_data(envelope["message"]["data"]) == inner


def test_the_fake_and_the_fixtures_agree_on_the_envelope_shape() -> None:
	"""One construction of the envelope in the repo, used by both. Two constructions is how a
	parser starts passing its unit tests and failing on the harness."""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	_create(fake, space, n=1)
	emitted = fake.drain_events()[0]
	reference = fixtures.message_created_event()

	assert set(emitted["message"]) == set(reference["message"])
	assert set(emitted["message"]["attributes"]) == set(reference["message"]["attributes"])
	assert set(fixtures.decode_data(emitted["message"]["data"])) == set(
		fixtures.decode_data(reference["message"]["data"])
	)


def test_rfc3339_formatting_is_derived_from_the_clock_and_never_from_now() -> None:
	assert fixtures.rfc3339_from_epoch_ms(0) == "1970-01-01T00:00:00.000000Z"
	assert FakeClock(DEFAULT_CLOCK_START_MS).rfc3339() == "2026-08-09T00:00:00.000000Z"
	assert FakeClock(DEFAULT_CLOCK_START_MS).advance(1) == DEFAULT_CLOCK_START_MS + 1


def test_build_create_message_call_and_the_fake_agree_on_the_wire_format() -> None:
	"""A last belt-and-braces check that the two halves have not drifted: the builder's path
	and query, fed straight to the fake, must route and succeed."""
	fake = FakeChatAPI()
	space = _seeded_space(fake)
	call = build_create_message_call(space, _client_id(1), "req-1", "hello")
	response = fake.request(
		call.http_method,
		_url(call.path),
		params=dict(call.query),
		json=dict(call.body or {}),
		headers=_headers(),
	)
	assert response.status_code == 200
	assert _body(response)["clientAssignedMessageId"] == _client_id(1)
