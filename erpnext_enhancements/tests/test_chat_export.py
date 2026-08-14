# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The export bundle's decisions. Bench-free, pure — no `frappe` needed at all.

`chat/governance/export.py` imports nothing from Frappe on purpose: the four decisions worth
testing here are about bytes and redaction, and a module that needed a database to answer
"does a deleted message's text leave the building" would be a module nobody could answer it
about.

The two acceptance properties, both G6-9:

1. **The deleted body is in `revisions.jsonl` and not in `messages.jsonl`.** This is a
   decision rather than a filter, because on this schema an `is_deleted = 1` row *retains*
   its `text` (divergence D4). A naive dump hands over every deleted message in the file a
   reader opens first.
2. **A re-export is byte-identical apart from the timestamp and the export id.** That is
   what makes the manifest worth anything: if two exports of the same range differ, the
   difference has to mean something.
"""

import io
import json
import unittest
import zipfile

from erpnext_enhancements.chat.governance import export


def _msg(**over):
	row = {
		"name": "chatmsg-1",
		"room": "ROOM-1",
		"seq": 7,
		"sender": "Ada Lovelace",
		"sender_email": "ada@example.com",
		"text": "the invoice is wrong",
		"is_deleted": 0,
		"is_edited": 0,
		"creation": "2026-08-01 09:00:00",
	}
	row.update(over)
	return row


class DeletedBodyTest(unittest.TestCase):
	"""G6-9's first half, and the reason this module exists."""

	def test_a_deleted_message_loses_its_body_in_messages_jsonl(self):
		"""The row still HAS the text — that is the point. This is what drops it."""
		row = _msg(is_deleted=1, text="I will fire him on Friday")
		record = export.message_record(row)
		self.assertNotIn("I will fire him", json.dumps(record))
		self.assertEqual(record["text"], export.TOMBSTONE)

	def test_the_tombstone_is_a_marker_not_an_empty_string(self):
		"""Empty reads as 'they sent a blank message', which is a different claim."""
		record = export.message_record(_msg(is_deleted=1, text="x"))
		self.assertTrue(record["text"].strip())
		self.assertIn("revisions.jsonl", record["text"])

	def test_a_live_message_keeps_its_body(self):
		self.assertEqual(export.message_record(_msg())["text"], "the invoice is wrong")

	def test_the_body_survives_in_the_revision_record(self):
		"""Not lost — relocated. An export that could never say what was said is a gap."""
		revision = export.revision_record(
			{"message": "chatmsg-1", "change_type": "delete", "text_before": "I will fire him on Friday"}
		)
		self.assertEqual(revision["text_before"], "I will fire him on Friday")

	def test_revisions_are_never_gated_on_the_deleted_content_flag(self):
		"""revision_record takes no such argument, by design.

		Gating it too would produce an export that records THAT a message was deleted and can
		never say what it said. That is not a redaction, it is a gap — and it is the shape
		somebody would later have to explain in a hearing.
		"""
		import inspect

		params = inspect.signature(export.revision_record).parameters
		self.assertEqual(list(params), ["row"])

	def test_d9_off_by_default(self):
		"""Decision D-9. The caller must ask, and asking is itself audited."""
		import inspect

		default = inspect.signature(export.message_record).parameters["include_deleted_content"].default
		self.assertIs(default, False)

	def test_asking_deliberately_includes_the_body(self):
		record = export.message_record(_msg(is_deleted=1, text="kept"), include_deleted_content=True)
		self.assertEqual(record["text"], "kept")

	def test_every_record_states_whether_anything_was_withheld(self):
		"""Stamped on all records, not only the deleted ones.

		Without it, an export that withheld nothing and an export that had nothing to
		withhold produce identical files — and those are different facts.
		"""
		for flag in (True, False):
			record = export.message_record(_msg(), include_deleted_content=flag)
			self.assertEqual(record["deleted_content_included"], flag)


class DeterminismTest(unittest.TestCase):
	"""G6-9's second half: a re-export differs only where it should."""

	def test_jsonl_is_byte_identical_across_runs(self):
		rows = [_msg(seq=2), _msg(seq=1, name="chatmsg-0")]
		first = export.canonical_jsonl(export.message_record(r) for r in rows)
		second = export.canonical_jsonl(export.message_record(r) for r in rows)
		self.assertEqual(first, second)

	def test_key_order_does_not_change_the_bytes(self):
		"""Python dicts preserve insertion order, so two paths that build the same record
		differently would otherwise hash differently for no reason a reader could act on."""
		a = export.canonical_jsonl([{"b": 1, "a": 2}])
		b = export.canonical_jsonl([{"a": 2, "b": 1}])
		self.assertEqual(a, b)

	def test_each_record_is_one_line(self):
		"""JSONL is only greppable if a record cannot span lines."""
		payload = export.canonical_jsonl([_msg(), _msg(seq=8)]).decode("utf-8")
		self.assertEqual(len(payload.strip().split("\n")), 2)

	def test_non_ascii_survives_as_itself(self):
		payload = export.canonical_jsonl([{"sender": "Ana Muñoz"}]).decode("utf-8")
		self.assertIn("Muñoz", payload)

	def test_the_zip_is_byte_identical_across_runs(self):
		"""ZIP stores an mtime per entry, so this would drift without a fixed timestamp."""
		files = {"a.txt": b"one", "b.txt": b"two"}
		self.assertEqual(export.build_zip(files), export.build_zip(files))

	def test_the_zip_entry_order_does_not_depend_on_dict_order(self):
		one = export.build_zip({"a.txt": b"1", "b.txt": b"2"})
		two = export.build_zip({"b.txt": b"2", "a.txt": b"1"})
		self.assertEqual(one, two)

	def test_the_zip_actually_contains_what_it_was_given(self):
		blob = export.build_zip({"messages.jsonl": b"line\n"})
		with zipfile.ZipFile(io.BytesIO(blob)) as archive:
			self.assertEqual(archive.read("messages.jsonl"), b"line\n")


class ManifestTest(unittest.TestCase):
	def setUp(self):
		self.files = {"messages.jsonl": b"a\n", "revisions.jsonl": b"b\n"}
		self.manifest = export.build_manifest(
			self.files,
			app_version="1.289.4",
			git_commit="abc1234",
			export_id="EXP-1",
			generated_at="2026-08-14 12:00:00",
			rooms=["ROOM-2", "ROOM-1"],
			include_deleted_content=False,
			message_count=2,
			revision_count=1,
		)

	def test_it_hashes_every_file(self):
		self.assertEqual(set(self.manifest["files"]), set(self.files))
		self.assertEqual(
			self.manifest["files"]["messages.jsonl"], export.sha256_hex(b"a\n")
		)

	def test_it_names_the_code_that_produced_the_bundle(self):
		"""A hash proves the bytes, not the rules.

		Two exports of the same range can legitimately differ if the redaction rule changed
		between them. Without the version, the only visible fact is that the hashes disagree
		— which looks exactly like tampering.
		"""
		self.assertEqual(self.manifest["app_version"], "1.289.4")
		self.assertEqual(self.manifest["git_commit"], "abc1234")

	def test_the_manifest_does_not_hash_itself(self):
		"""A file cannot carry its own hash, and pretending otherwise verifies nothing."""
		self.assertNotIn("manifest.json", self.manifest["files"])

	def test_rooms_are_sorted_so_two_exports_of_the_same_set_match(self):
		self.assertEqual(self.manifest["rooms"], ["ROOM-1", "ROOM-2"])

	def test_a_clean_bundle_verifies(self):
		self.assertEqual(export.verify_bundle(self.files, self.manifest), [])

	def test_an_altered_file_is_caught(self):
		tampered = dict(self.files, **{"messages.jsonl": b"a changed\n"})
		problems = export.verify_bundle(tampered, self.manifest)
		self.assertEqual(len(problems), 1)
		self.assertIn("messages.jsonl", problems[0])

	def test_a_removed_file_is_caught(self):
		"""The file most worth removing is the one that contradicts the story."""
		problems = export.verify_bundle({"messages.jsonl": b"a\n"}, self.manifest)
		self.assertTrue(any("revisions.jsonl" in p and "missing" in p for p in problems))

	def test_an_added_file_is_caught(self):
		extra = dict(self.files, **{"note.txt": b"trust me\n"})
		problems = export.verify_bundle(extra, self.manifest)
		self.assertTrue(any("note.txt" in p for p in problems))


class ReadmeTest(unittest.TestCase):
	"""The README is a control, not decoration."""

	def _readme(self, **over):
		kwargs = {
			"export_id": "EXP-1",
			"rooms": ["ROOM-1"],
			"include_deleted_content": False,
			"app_version": "1.289.4",
		}
		kwargs.update(over)
		return export.readme_text(**kwargs)

	def test_it_names_the_misreading_it_exists_to_prevent(self):
		"""That messages.jsonl is 'the conversation'. It is the conversation AS IT STANDS."""
		text = self._readme()
		self.assertIn("AS IT STANDS", text)
		self.assertIn("revisions.jsonl", text)

	def test_it_says_where_deleted_text_went(self):
		text = self._readme()
		self.assertIn("NOT missing", text)

	def test_it_changes_its_story_when_deleted_content_is_included(self):
		"""A reader must not have to diff two bundles to learn this one is different."""
		text = self._readme(include_deleted_content=True)
		self.assertIn("INCLUDES", text)

	def test_it_tells_the_reader_how_to_verify(self):
		self.assertIn("sha256sum", self._readme())


class TranscriptTest(unittest.TestCase):
	def test_message_text_is_escaped(self):
		"""This file is opened in a browser by somebody handed it, and its text was typed by
		the people under investigation."""
		html = export.transcript_html(
			[{"seq": 1, "sender_email": "a@b.c", "text": "<script>alert(1)</script>"}],
			export_id="EXP-1",
		)
		self.assertNotIn("<script>alert", html)
		self.assertIn("&lt;script&gt;", html)

	def test_the_sender_is_escaped_too(self):
		html = export.transcript_html(
			[{"seq": 1, "sender_email": "<img src=x onerror=1>", "text": "hi"}], export_id="E"
		)
		self.assertNotIn("<img src=x", html)

	def test_the_export_id_is_escaped(self):
		html = export.transcript_html([], export_id="<b>x</b>")
		self.assertNotIn("<b>x</b>", html)

	def test_it_says_it_is_not_the_record(self):
		self.assertIn("messages.jsonl is the record", export.transcript_html([], export_id="E"))


class FieldAllowlistTest(unittest.TestCase):
	def test_message_fields_are_an_allowlist_not_a_dump(self):
		"""A column added next year is not automatically part of a legal disclosure.

		The opposite fails silently: the new field simply appears in the next export.
		"""
		record = export.message_record(_msg(secret_new_column="do not disclose"))
		self.assertNotIn("secret_new_column", record)

	def test_text_plain_is_not_exported(self):
		"""The search-index copy of the body. Exporting both would double every message and
		invite a reader to treat a mismatch as significant."""
		self.assertNotIn("text_plain", export.MESSAGE_FIELDS)

	def test_the_revision_allowlist_carries_both_sides_of_an_edit(self):
		for field in ("text_before", "text_after", "actor", "change_type"):
			self.assertIn(field, export.REVISION_FIELDS)


if __name__ == "__main__":
	unittest.main()
