# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Attachment permission parity, against a real database. **THIS SUITE DOES NOT RUN IN CI.**

======================================================================================
NOT RUN IN CI. A HUMAN MUST RUN IT AND RECORD THE RESULT AT THE PHASE 2 CHECKPOINT.
======================================================================================

    bench --site <site> run-tests --app erpnext_enhancements \\
        --module erpnext_enhancements.tests.test_chat_attachments_bench

    # or, narrowed to the one the prompt names explicitly:
    bench --site <site> run-tests --app erpnext_enhancements \\
        --module erpnext_enhancements.tests.test_chat_attachments_bench \\
        --test test_private_file_url_is_forbidden_for_a_non_member

There is no Frappe integration-test job in this repo (``CLAUDE.md``), and everything here
needs a real ``File`` row on a real disk, a real ``DatabaseQuery`` and the real
``download_private_file`` route. A stubbed version of any of those asserts that the stub
works, which is precisely the reassurance §4.I says not to accept: *"do not assume it
works — there is a long tail of reported issues where Frappe private files are more
accessible than expected."*

The rest of §4.I — planning, sizing, sanitising, the payload shape, the two chaos-14 cases at
the transport level — is bench-free and runs in CI as
``erpnext_enhancements/tests/test_chat_attachments.py``. The parity table in
``chat/sync/attachments.py``'s module docstring names which file owns each row.

What this suite establishes, and why each one is here
-----------------------------------------------------

* **A non-member gets 403 on another room's attachment URL, authenticated *and*
  unauthenticated.** The explicit §4.I acceptance test. Both, because they fail for different
  reasons — Guest is refused by ``download_private_file``'s own first line, an authenticated
  stranger is refused four calls deeper by ``File.has_permission`` delegating to
  ``Chat Message``, and passing one proves nothing about the other.
* **The same 403 through :func:`~erpnext_enhancements.chat.sync.attachments.download`**, which
  is the *only* path that is supposed to work, so it is the only path where a mistake is
  reachable by a real user.
* **Identical bytes in two rooms still refuse a stranger.** ``File.save_file`` deduplicates on
  MD5 and reuses the existing row's ``file_url``, and ``find_file_by_url`` returns the first
  ``File`` for that URL the caller may download — so two rooms can share one URL. Reasoning
  says that is harmless (the bytes are the same, and each entitled reader was already
  entitled). This asserts it instead of reasoning about it.
* **The DocType invariants**, because they are the backstop under every other row: a public
  ``File`` cannot be recorded, a ``Drive Link`` cannot carry one, and the denormalised ``room``
  cannot contradict the message it hangs off.
* **Ingest is idempotent**, since a redelivered Workspace Event replays the whole message.

Fixtures are three users, two rooms, three messages and a handful of small files, all created
by the suite and all rolled back with the enclosing transaction. Nothing here reads production
data and the payloads are deliberately dull.
"""

from __future__ import annotations

import re
from typing import Any

import frappe

# v16 renamed the base class; `frappe.tests.utils.FrappeTestCase` is the compatibility shim
# and every other bench suite in this repo still imports it. Prefer the new name so this file
# does not need editing when the shim goes, and fall back so it runs on a bench that still
# predates it. Both give the per-test transaction rollback this suite relies on.
try:  # pragma: no cover - which branch runs is a property of the bench, not the code
	from frappe.tests import IntegrationTestCase as _ChatTestCase
except ImportError:  # pragma: no cover
	from frappe.tests.utils import FrappeTestCase as _ChatTestCase

from erpnext_enhancements.chat.gchat.client import AuthIdentity, GoogleChatClient
from erpnext_enhancements.chat.sync import attachments
from erpnext_enhancements.chat.testing.fake_chat import FakeChatSettings, FakeResponse

#: Three identities. ``STRANGER`` is the whole point: a real, enabled, logged-in employee who
#: simply is not in the room. The realistic attacker here is a colleague.
ALICE = "chat_att_alice@example.com"
BOB = "chat_att_bob@example.com"
STRANGER = "chat_att_stranger@example.com"

MEMBER_ROLE = "Chat User"

PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR-bench-fixture"
OTHER_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR-a-different-blob"

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def _ensure_role(role_name: str) -> None:
	if not frappe.db.exists("Role", role_name):
		frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1}).insert(
			ignore_permissions=True
		)


def _ensure_user(email: str, roles: list[str]) -> str:
	"""Create the user if absent and grant ``roles`` **directly**.

	Direct grants are correct here and wrong in production: a profiled user's roles are rebuilt
	from their Role Profiles on every save, so a direct grant is wiped. These test users hold
	no profile, so nothing rebuilds them.
	"""
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
				"enabled": 1,
			}
		).insert(ignore_permissions=True)
	user = frappe.get_doc("User", email)
	existing = {row.role for row in user.get("roles") or []}
	for role in roles:
		_ensure_role(role)
		if role not in existing:
			user.append("roles", {"role": role})
	user.save(ignore_permissions=True)
	return email


class _ForbiddenTransport:
	"""Answers every request with Google's AIP-193 403. Chaos test 14, first half."""

	body = (
		'{\n  "error": {\n    "code": 403,\n    "message": "The caller does not have permission",\n'
		'    "status": "PERMISSION_DENIED"\n  }\n}'
	)

	def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
		return FakeResponse(status_code=403, text=self.body, headers={"Content-Type": "application/json"})


class _BytesTransport:
	"""Answers ``media.download`` with a fixed body. Lets the size ceiling be tested cheaply.

	A real 200 MB fixture would make this suite unrunnable on a laptop, so the *ceiling* is
	lowered instead of the payload being raised — the branch under test is the comparison, and
	it does not care which side moved.
	"""

	def __init__(self, payload: bytes) -> None:
		self.payload = payload

	def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
		return FakeResponse(
			status_code=200,
			text=self.payload.decode("utf-8", "replace"),
			headers={"Content-Type": "image/png"},
			content=self.payload,
		)


def _client(transport: Any) -> GoogleChatClient:
	"""An app-identity client on a supplied transport. App, because inbound downloads are.

	``media.download``'s scope list includes ``chat.bot``; ``media.upload``'s does not. The
	ingest therefore never impersonates the sender, which matters because the sender of an
	inbound attachment is frequently somebody we hold no DWD mandate for.
	"""
	return GoogleChatClient(
		identity=AuthIdentity.APP,
		dry_run=False,
		settings=FakeChatSettings(),
		token_provider=lambda *a, **k: "bench-token",
		transport=transport,
	)


class ChatAttachmentFixture(_ChatTestCase):
	"""Two rooms, three users, three messages. ``STRANGER`` is in neither room."""

	def setUp(self) -> None:
		super().setUp()
		frappe.set_user("Administrator")

		_ensure_user(ALICE, [MEMBER_ROLE])
		_ensure_user(BOB, [MEMBER_ROLE])
		_ensure_user(STRANGER, [MEMBER_ROLE])

		# The oversight hatch ships blank and every "denied" assertion below depends on it
		# staying blank. Any test that wants it open opens it explicitly.
		settings = frappe.get_single("Chat Settings")
		settings.admin_oversight_role = ""
		settings.save(ignore_permissions=True)
		frappe.clear_document_cache("Chat Settings", "Chat Settings")
		if getattr(frappe.db, "value_cache", None):
			frappe.db.value_cache = {}

		self.room_a = self._make_room("Chat attachments - Alice's room")
		self.room_b = self._make_room("Chat attachments - Bob's room")
		self._add_member(self.room_a, ALICE)
		self._add_member(self.room_b, BOB)

		self.message_a = self._make_message(self.room_a, seq=1, sender=ALICE, text="here is the photo")
		self.message_b = self._make_message(self.room_b, seq=1, sender=BOB, text="and here is mine")

	def tearDown(self) -> None:
		frappe.set_user("Administrator")
		super().tearDown()

	# ------------------------------------------------------------------ fixture helpers

	def _make_room(self, title: str) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": "Chat Room",
					"room_type": "Group",
					"title": title,
					"provisioning_mode": "Not Mirrored",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _add_member(self, room: str, user: str) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": "Chat Room Member",
					"room": room,
					"user": user,
					"role": "Member",
					"is_active": 1,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _make_message(self, room: str, seq: int, sender: str, text: str) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": "Chat Message",
					"room": room,
					"seq": seq,
					"sender": sender,
					"sender_kind": "Human",
					"message_type": "Text",
					"text": text,
					"text_plain": text,
					"client_message_id": f"client-attbench-{room[:8]}-{seq}",
					"sync_origin": "ERPNext",
					"sync_state": "Not Mirrored",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _attach_private_file(self, message: str, file_name: str, content: bytes) -> Any:
		"""A private ``File`` on a ``Chat Message`` — the shape the SPA's upload produces."""
		return frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name,
				"attached_to_doctype": "Chat Message",
				"attached_to_name": message,
				"is_private": 1,
				"content": content,
			}
		).insert(ignore_permissions=True)

	def _record(self, message: str, room: str, file_doc: Any, source: str = "ERPNext") -> str:
		return (
			frappe.get_doc(
				{
					"doctype": "Chat Attachment",
					"message": message,
					"room": room,
					"source": source,
					"ingest_state": "Stored",
					"file": file_doc.name,
					"file_name": file_doc.file_name,
					"file_size": file_doc.file_size,
					"content_hash": attachments.content_hash(PNG_BYTES),
					"byte_size_verified": 1,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)


class TestPrivateFileUrlDelegation(ChatAttachmentFixture):
	"""§4.I's explicit acceptance test, and the reason it is written at all."""

	def test_private_file_url_is_forbidden_for_a_non_member(self) -> None:
		"""A stranger requests room B's attachment URL. **403, authenticated and not.**

		Exercised through ``frappe.utils.response.download_private_file`` — the function the
		``/private/files/…`` route actually calls (``frappe/app.py``) — rather than through a
		hand-rolled permission check, because the whole question is whether the *framework's*
		path agrees with ours.
		"""
		from werkzeug.exceptions import Forbidden

		file_doc = self._attach_private_file(self.message_b, "bobs-photo.png", PNG_BYTES)
		self.assertTrue(file_doc.file_url.startswith("/private/files/"), "the fixture is not private")

		frappe.set_user(STRANGER)
		with self.assertRaises(Forbidden):
			from frappe.utils.response import download_private_file

			download_private_file(file_doc.file_url)

		frappe.set_user("Guest")
		with self.assertRaises(Forbidden):
			from frappe.utils.response import download_private_file

			download_private_file(file_doc.file_url)

	def test_the_private_url_is_forbidden_for_the_room_member_too(self) -> None:
		"""Not a bug — the posture. ``Chat Message`` ships zero DocPerm, so the delegation
		``File.has_permission`` → ``ref_doc.has_permission("read")`` denies everybody but
		Administrator. The byte path is :func:`attachments.download`, and this test exists so
		that anybody who "fixes" the 403 by adding a DocPerm to ``Chat Message`` — which would
		open the desk's list and report views onto every message body in the company — finds
		out here rather than in production."""
		from werkzeug.exceptions import Forbidden

		file_doc = self._attach_private_file(self.message_b, "bobs-photo.png", PNG_BYTES)
		frappe.set_user(BOB)
		with self.assertRaises(Forbidden):
			from frappe.utils.response import download_private_file

			download_private_file(file_doc.file_url)

	def test_deduplicated_file_url_still_refuses_a_stranger(self) -> None:
		"""Identical bytes in two rooms share one ``file_url``. A stranger is still refused.

		``File.save_file`` dedupes on MD5 and reuses the existing row's ``file_url``, and
		``find_file_by_url`` walks *every* ``File`` at that URL and returns the first the
		caller may download. So the shared URL is a real thing and this is the assertion that
		it does not become a shared *permission*.
		"""
		from werkzeug.exceptions import Forbidden

		file_a = self._attach_private_file(self.message_a, "shared.png", PNG_BYTES)
		file_b = self._attach_private_file(self.message_b, "shared.png", PNG_BYTES)
		self.assertNotEqual(file_a.name, file_b.name, "two rows, one blob — that is the premise")

		frappe.set_user(STRANGER)
		for url in {file_a.file_url, file_b.file_url}:
			with self.assertRaises(Forbidden):
				from frappe.utils.response import download_private_file

				download_private_file(url)


class TestTheByteEndpoint(ChatAttachmentFixture):
	"""Parity rows 1 and 2, through the only path that is meant to work."""

	def test_parity_row_1_spa_upload_member_reads_the_bytes(self) -> None:
		file_doc = self._attach_private_file(self.message_a, "alices-photo.png", PNG_BYTES)
		name = self._record(self.message_a, self.room_a, file_doc)

		frappe.set_user(ALICE)
		frappe.local.response = frappe._dict()
		attachments.download(name)
		self.assertEqual(frappe.local.response.filecontent, PNG_BYTES)
		self.assertEqual(frappe.local.response.type, "download")
		self.assertEqual(frappe.local.response.filename, "alices-photo.png")

	def test_parity_row_1_spa_upload_non_member_is_refused(self) -> None:
		file_doc = self._attach_private_file(self.message_b, "bobs-photo.png", PNG_BYTES)
		name = self._record(self.message_b, self.room_b, file_doc)

		frappe.set_user(STRANGER)
		with self.assertRaises(frappe.PermissionError):
			attachments.download(name)

	def test_guest_is_refused_before_anything_is_looked_up(self) -> None:
		"""An unauthenticated request is not a permission question.

		Letting it reach a membership probe is how a ``Guest`` row in ``Chat Room Member``
		would one day become a public file server.
		"""
		file_doc = self._attach_private_file(self.message_a, "alices-photo.png", PNG_BYTES)
		name = self._record(self.message_a, self.room_a, file_doc)

		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			attachments.download(name)

	def test_a_missing_attachment_and_an_unreadable_one_answer_identically(self) -> None:
		"""Both are ``PermissionError``. Distinguishing them would answer *"does room B
		contain an attachment"* for somebody who is not in room B."""
		file_doc = self._attach_private_file(self.message_b, "bobs-photo.png", PNG_BYTES)
		unreadable = self._record(self.message_b, self.room_b, file_doc)

		frappe.set_user(STRANGER)
		with self.assertRaises(frappe.PermissionError):
			attachments.download(unreadable)
		with self.assertRaises(frappe.PermissionError):
			attachments.download("CHAT-ATTACHMENT-THAT-DOES-NOT-EXIST")


class TestInboundIngest(ChatAttachmentFixture):
	"""Parity row 2 and row 3, from a real ``Message`` resource through to real rows."""

	def _resource(self, *, source: str, data_ref: str = "CHAT-ATT-bench") -> dict[str, Any]:
		attachment: dict[str, Any] = {
			"name": f"{self.room_b}/attachments/ATT1",
			"contentName": "inbound-photo.png",
			"contentType": "image/png",
			"source": source,
		}
		if source == "UPLOADED_CONTENT":
			attachment["attachmentDataRef"] = {"resourceName": data_ref}
		else:
			attachment["driveDataRef"] = {"driveFileId": "1AbCdEfGhIjK"}
		return {"name": "spaces/AAAA1/messages/MMMM1", "text": "look", "attachment": [attachment]}

	def test_parity_row_2_chat_upload_is_stored_private_and_attached(self) -> None:
		names = attachments.ingest_message_attachments(
			self.message_a, self._resource(source="UPLOADED_CONTENT"), enqueue_downloads=False
		)["attachments"]
		self.assertEqual(len(names), 1)
		self.assertEqual(
			attachments.download_attachment(names[0], client=_client(_BytesTransport(PNG_BYTES))),
			"Stored",
		)

		row = frappe.get_doc("Chat Attachment", names[0])
		self.assertEqual(row.source, "Uploaded")
		self.assertEqual(row.room, self.room_a, "the denormalised room must follow the message")
		self.assertTrue(row.file)
		self.assertTrue(_SHA256_HEX.match(row.content_hash or ""), "content_hash must be SHA-256 hex")
		self.assertEqual(row.byte_size_verified, 1)

		file_doc = frappe.get_doc("File", row.file)
		self.assertEqual(file_doc.is_private, 1, "ADR §F.8: there is no safe public chat attachment")
		self.assertEqual(file_doc.attached_to_doctype, "Chat Message")
		self.assertEqual(file_doc.attached_to_name, self.message_a)
		self.assertEqual(
			frappe.db.get_value("Chat Message", self.message_a, "has_attachments"),
			1,
			"the message must advertise that it has attachments, or the SPA never asks",
		)

	def test_parity_row_2_chat_upload_non_member_is_refused(self) -> None:
		names = attachments.ingest_message_attachments(
			self.message_b, self._resource(source="UPLOADED_CONTENT"), enqueue_downloads=False
		)["attachments"]
		attachments.download_attachment(names[0], client=_client(_BytesTransport(PNG_BYTES)))

		frappe.set_user(STRANGER)
		with self.assertRaises(frappe.PermissionError):
			attachments.download(names[0])

	def test_parity_row_3_drive_link_stores_no_bytes(self) -> None:
		names = attachments.ingest_message_attachments(
			self.message_a, self._resource(source="DRIVE_FILE"), enqueue_downloads=False
		)["attachments"]
		row = frappe.get_doc("Chat Attachment", names[0])
		self.assertEqual(row.source, "Drive Link")
		self.assertEqual(row.ingest_state, "Linked")
		self.assertEqual(row.drive_file_id, "1AbCdEfGhIjK")
		self.assertFalse(row.file, "a Drive Link must never carry a local File")

	def test_parity_row_3_drive_link_non_member_sees_nothing(self) -> None:
		names = attachments.ingest_message_attachments(
			self.message_b, self._resource(source="DRIVE_FILE"), enqueue_downloads=False
		)["attachments"]
		frappe.set_user(STRANGER)
		with self.assertRaises(frappe.PermissionError):
			attachments.download(names[0])
		visible = frappe.get_list("Chat Attachment", filters={"name": names[0]}, ignore_permissions=False)
		self.assertEqual(visible, [], "a non-member must not even see that the row exists")

	def test_a_drive_link_has_no_bytes_for_a_member_either(self) -> None:
		"""The row is readable; the bytes do not exist here. Those are different answers and
		the endpoint must not conflate "no local copy" with "no permission" in the *reason*,
		even though both are a 403 to the transport."""
		names = attachments.ingest_message_attachments(
			self.message_a, self._resource(source="DRIVE_FILE"), enqueue_downloads=False
		)["attachments"]
		frappe.set_user(ALICE)
		with self.assertRaises(frappe.PermissionError) as caught:
			attachments.download(names[0])
		self.assertIn("Drive", str(caught.exception))

	def test_a_replayed_event_produces_exactly_one_row(self) -> None:
		"""Workspace Events are at-least-once, so the whole message is replayed. §G.8 Rule 2:
		a unique collision on re-ingest is **success**, not an error."""
		resource = self._resource(source="UPLOADED_CONTENT")
		first = attachments.ingest_message_attachments(self.message_a, resource, enqueue_downloads=False)
		second = attachments.ingest_message_attachments(self.message_a, resource, enqueue_downloads=False)
		self.assertEqual(first["attachments"], second["attachments"], "the same row, both times")
		self.assertEqual(first["created"], 1)
		self.assertEqual(second["created"], 0, "the replay must find the row, not write a second one")
		self.assertEqual(
			frappe.db.count("Chat Attachment", {"message": self.message_a}),
			1,
			"a redelivered event created a second attachment row",
		)

	def test_a_second_download_of_a_stored_row_is_a_no_op(self) -> None:
		names = attachments.ingest_message_attachments(
			self.message_a, self._resource(source="UPLOADED_CONTENT"), enqueue_downloads=False
		)["attachments"]
		attachments.download_attachment(names[0], client=_client(_BytesTransport(PNG_BYTES)))
		first_file = frappe.db.get_value("Chat Attachment", names[0], "file")

		# A transport that would fail if it were reached at all.
		self.assertEqual(
			attachments.download_attachment(names[0], client=_client(_ForbiddenTransport())), "Stored"
		)
		self.assertEqual(frappe.db.get_value("Chat Attachment", names[0], "file"), first_file)


class TestChaos14(ChatAttachmentFixture):
	"""Chaos list item 14, at the row level. The transport-level halves are bench-free."""

	def test_a_403_download_marks_the_row_failed_and_writes_no_file(self) -> None:
		names = attachments.ingest_message_attachments(
			self.message_a,
			{
				"attachment": [
					{
						"name": "spaces/A/messages/M/attachments/ATT1",
						"contentName": "forbidden.png",
						"contentType": "image/png",
						"source": "UPLOADED_CONTENT",
						"attachmentDataRef": {"resourceName": "CHAT-ATT-forbidden"},
					}
				]
			},
			enqueue_downloads=False,
		)["attachments"]
		state = attachments.download_attachment(names[0], client=_client(_ForbiddenTransport()))

		self.assertEqual(state, "Failed")
		row = frappe.get_doc("Chat Attachment", names[0])
		self.assertFalse(row.file, "a failed download must not leave a File row behind")
		self.assertIn("PERMISSION_DENIED", row.skip_reason)
		self.assertNotIn("Bearer", row.skip_reason.replace("Bearer [redacted]", ""))
		self.assertEqual(
			frappe.db.count("File", {"attached_to_name": self.message_a}),
			0,
			"no bytes were received, so no File may exist",
		)

	def test_an_oversized_download_is_skipped_and_writes_no_file(self) -> None:
		"""The ceiling is lowered rather than the payload raised — the branch under test is
		the comparison, and a 200 MB fixture would make this suite unrunnable."""
		names = attachments.ingest_message_attachments(
			self.message_a,
			{
				"attachment": [
					{
						"name": "spaces/A/messages/M/attachments/ATT2",
						"contentName": "huge.png",
						"contentType": "image/png",
						"source": "UPLOADED_CONTENT",
						"attachmentDataRef": {"resourceName": "CHAT-ATT-huge"},
					}
				]
			},
			enqueue_downloads=False,
		)["attachments"]

		settings = frappe.get_single("Chat Settings")
		original = settings.attachment_byte_limit
		settings.attachment_byte_limit = 4
		settings.save(ignore_permissions=True)
		frappe.clear_document_cache("Chat Settings", "Chat Settings")
		if getattr(frappe.db, "value_cache", None):
			frappe.db.value_cache = {}
		try:
			state = attachments.download_attachment(names[0], client=_client(_BytesTransport(PNG_BYTES)))
		finally:
			settings = frappe.get_single("Chat Settings")
			settings.attachment_byte_limit = original
			settings.save(ignore_permissions=True)
			frappe.clear_document_cache("Chat Settings", "Chat Settings")

		self.assertEqual(state, "Skipped", "over the ceiling is a policy decision, not a fault")
		row = frappe.get_doc("Chat Attachment", names[0])
		self.assertFalse(row.file)
		self.assertIn("exceeds", row.skip_reason)
		self.assertEqual(frappe.db.count("File", {"attached_to_name": self.message_a}), 0)


class TestDocTypeInvariants(ChatAttachmentFixture):
	"""The backstops under every parity row above."""

	def test_a_public_file_cannot_be_recorded_as_a_chat_attachment(self) -> None:
		"""A public file is served straight off disk with no auth at all. Nothing in Frappe,
		and nothing in this app, gets a chance to say no — so the row is refused."""
		public = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "public.png",
				"attached_to_doctype": "Chat Message",
				"attached_to_name": self.message_a,
				"is_private": 0,
				"content": OTHER_BYTES,
			}
		).insert(ignore_permissions=True)

		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Chat Attachment",
					"message": self.message_a,
					"room": self.room_a,
					"source": "ERPNext",
					"file": public.name,
					"file_name": "public.png",
				}
			).insert(ignore_permissions=True)

	def test_a_drive_link_cannot_carry_a_local_file(self) -> None:
		private = self._attach_private_file(self.message_a, "copy.png", PNG_BYTES)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Chat Attachment",
					"message": self.message_a,
					"room": self.room_a,
					"source": "Drive Link",
					"file": private.name,
					"drive_file_id": "1AbCdEfGhIjK",
				}
			).insert(ignore_permissions=True)

	def test_the_room_cannot_contradict_the_message(self) -> None:
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Chat Attachment",
					"message": self.message_a,
					"room": self.room_b,
					"source": "Uploaded",
				}
			).insert(ignore_permissions=True)

	def test_a_blank_room_is_filled_in_from_the_message(self) -> None:
		doc = frappe.get_doc(
			{"doctype": "Chat Attachment", "message": self.message_a, "source": "Uploaded"}
		).insert(ignore_permissions=True)
		self.assertEqual(doc.room, self.room_a)

	def test_a_malformed_content_hash_is_refused(self) -> None:
		"""An upper-cased or truncated digest compares unequal to a correct one, which turns
		re-download verification and cross-room dedupe into permanent silent misses."""
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Chat Attachment",
					"message": self.message_a,
					"room": self.room_a,
					"source": "Uploaded",
					"content_hash": attachments.content_hash(PNG_BYTES).upper(),
				}
			).insert(ignore_permissions=True)


class TestTheSweeper(ChatAttachmentFixture):
	"""Rule 4: catch-up is by sweeper, not by queue. The deploy ``FLUSHDB``s the queue."""

	def test_the_sweeper_ignores_a_row_that_was_just_touched(self) -> None:
		"""``Chat Attachment`` has no ``attempts`` column, so ``modified`` is the retry clock.

		Without the cooldown a permanently failing attachment — a revoked space, a deleted
		blob — is retried every sweep forever.
		"""
		attachments.ingest_message_attachments(
			self.message_a,
			{
				"attachment": [
					{
						"contentName": "fresh.png",
						"contentType": "image/png",
						"source": "UPLOADED_CONTENT",
						"attachmentDataRef": {"resourceName": "CHAT-ATT-fresh"},
					}
				]
			},
			enqueue_downloads=False,
		)
		self.assertEqual(attachments.sweep_pending_attachments()["considered"], 0)
