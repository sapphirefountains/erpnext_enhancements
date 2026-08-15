# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The unified access report's pure half — bench-free, unittest, one `frappe` stub.

Three properties are worth a test and the rest is SQL:

1. **A reason is graded, not merely counted.** The failure mode that matters is not an
   empty box; it is a reason that passes a length check and says nothing, because that one
   only appears once somebody has worked out the field is not read.
2. **The subject projection cannot leak the free-text reason.** It is written for a
   compliance reviewer and can name a third party or an open investigation.
3. **The category vocabulary has exactly one definition.** Two copies of a Select is the
   defect this module exists to remove one level up, and re-introducing it here would be
   funny rather than ironic.

The SQL half — the child-table join that makes the unit `(reader, room, occasion)` rather
than `(row)` — needs a real database and is asserted in the bench suite. Its acceptance bar
is G6-7: exactly one record per non-participant read through every path, zero for a
participant.
"""

import sys
import types
import unittest


def setUpModule():
	"""Install a `frappe` stub before importing the module under test.

	Same idiom as the other bench-free chat suites: the pure functions here touch no
	database, but `chat.audit` imports `frappe` at module scope and `access_report` imports
	`chat.audit` for the category vocabulary.
	"""
	if "frappe" in sys.modules and getattr(sys.modules["frappe"], "_ee_test_stub", False):
		return

	frappe = types.ModuleType("frappe")
	frappe._ee_test_stub = True
	frappe.db = types.SimpleNamespace(sql=lambda *a, **k: [])
	frappe.utils = types.ModuleType("frappe.utils")
	frappe.utils.now = lambda: "2026-08-14 00:00:00"
	frappe.utils.cint = lambda v: int(v or 0)
	frappe.cint = frappe.utils.cint
	frappe.get_all = lambda *a, **k: []
	frappe.new_doc = lambda *a, **k: None
	frappe.log_error = lambda *a, **k: None
	frappe.throw = lambda *a, **k: (_ for _ in ()).throw(Exception("throw"))
	frappe.ValidationError = Exception
	frappe._ = lambda s: s

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = frappe.utils


class ReasonQualityTest(unittest.TestCase):
	"""§4.D.2 asks for a reason. This grades what it got."""

	def setUp(self):
		from erpnext_enhancements.chat.governance import access_report

		self.mod = access_report

	def test_a_real_reason_passes(self):
		self.assertEqual(
			self.mod.reason_quality("HR asked me to review the Jones complaint"),
			self.mod.REASON_OK,
		)

	def test_nothing_at_all_is_missing_not_too_short(self):
		"""Three failure modes, kept distinct because they need three different fixes."""
		for value in (None, "", "   ", "\t\n"):
			self.assertEqual(self.mod.reason_quality(value), self.mod.REASON_MISSING, repr(value))

	def test_a_short_reason_is_too_short(self):
		self.assertEqual(self.mod.reason_quality("HR asked"), self.mod.REASON_TOO_SHORT)

	def test_the_boundary_is_inclusive(self):
		"""Twelve characters passes; eleven does not. Stated so the off-by-one is a decision."""
		self.assertEqual(len("investigate"), 11)
		self.assertEqual(self.mod.reason_quality("investigate"), self.mod.REASON_TOO_SHORT)
		self.assertEqual(len("investigateX"), 12)
		self.assertEqual(self.mod.reason_quality("investigateX"), self.mod.REASON_OK)

	def test_padding_a_short_placeholder_does_not_clear_the_length_bar(self):
		"""Whitespace is stripped before measuring, so this stays `too_short`.

		Worth pinning as a distinct case: if the length were measured before stripping, a
		seven-letter word plus five spaces would be graded a real reason.
		"""
		self.assertEqual(self.mod.reason_quality("testing     "), self.mod.REASON_TOO_SHORT)

	def test_a_long_enough_placeholder_is_caught(self):
		"""The one that matters, and the one the first version of this got wrong.

		Padding fails the length check, so the way to clear *both* bars is to **repeat** —
		which is what somebody who has worked out the field is unread would actually type.
		Grading the whole string as one token missed exactly that.
		"""
		for value in (
			"test test test",
			"n/a n/a n/a n/a",
			"tbd, tbd, tbd.",
			"............",
			"aaaaaaaaaaaa",
			"1234567890123",
		):
			self.assertEqual(
				self.mod.reason_quality(value),
				self.mod.REASON_PLACEHOLDER,
				f"{value!r} scored as a real reason",
			)

	def test_a_placeholder_word_inside_a_real_sentence_is_not_flagged(self):
		"""The guard must not punish a reason that happens to contain a flagged word.

		`investigation` is in the placeholder set on its own. A sentence using it is a
		perfectly good reason, and a checker that rejected it would train people to write
		worse ones.
		"""
		self.assertEqual(
			self.mod.reason_quality("investigation into the March invoices"),
			self.mod.REASON_OK,
		)

	def test_the_minimum_matches_the_audit_log_rule(self):
		"""One number, not two. `Chat Audit Log` already enforces 12 in its controller."""
		self.assertEqual(self.mod.REASON_MIN_LENGTH, 12)


class SubjectProjectionTest(unittest.TestCase):
	"""What an employee is shown about reads of their own messages."""

	def setUp(self):
		from erpnext_enhancements.chat.governance import access_report

		self.mod = access_report
		self.row = {
			"kind": access_report.KIND_CONTENT,
			"occurred_at": "2026-08-14 09:00:00",
			"actor": "manager@example.com",
			"actor_type": "Admin",
			"room": "ROOM-1",
			"messages_read": 42,
			"purpose": "oversight",
			"reason": "HR complaint 44 names Ada, reviewing the thread",
			"reason_category": "HR or personnel matter",
			"audit_name": "AUD-1",
			"request_id": "req-1",
			"query_text": "salary",
		}

	def test_the_free_text_reason_never_reaches_the_subject(self):
		"""D-3. The whole point: the compliance text can name a third party."""
		out = self.mod.redact_for_subject(self.row)
		self.assertNotIn("reason", out)
		self.assertNotIn("HR complaint 44", str(out))
		self.assertNotIn("Ada", str(out))

	def test_no_other_free_text_field_survives_either(self):
		"""A leak does not have to come through the field named `reason`.

		`query_text` is the admin's search terms, which are as revealing as the reason.
		Asserting the projection is a strict allowlist rather than a blocklist is what makes
		a new column on the audit table safe by default.
		"""
		allowed = {
			"kind",
			"occurred_at",
			"actor",
			"actor_type",
			"room",
			"messages_read",
			"reason_category",
			"purpose",
		}
		self.assertEqual(set(self.mod.redact_for_subject(self.row)), allowed)

	def test_the_admin_is_named(self):
		"""D-2. 'An administrator' is the version that generates a rumour."""
		self.assertEqual(self.mod.redact_for_subject(self.row)["actor"], "manager@example.com")

	def test_the_category_is_shown(self):
		self.assertEqual(self.mod.redact_for_subject(self.row)["reason_category"], "HR or personnel matter")

	def test_a_missing_category_is_none_not_empty_string(self):
		"""So the view renders 'Not recorded' rather than a category called nothing."""
		for value in (None, "", "   "):
			row = dict(self.row, reason_category=value)
			self.assertIsNone(self.mod.redact_for_subject(row)["reason_category"])

	def test_actor_type_survives_so_triton_can_have_its_own_tab(self):
		"""D-5: assistant reads are shown, and do not bury the admin reads."""
		row = dict(self.row, actor_type="Triton")
		self.assertEqual(self.mod.redact_for_subject(row)["actor_type"], "Triton")

	def test_the_input_row_is_not_mutated(self):
		"""One caller's redaction must not become another caller's compliance view."""
		before = dict(self.row)
		self.mod.redact_for_subject(self.row)
		self.assertEqual(self.row, before)


class VocabularyTest(unittest.TestCase):
	"""The category Select has exactly one definition."""

	def test_the_report_re_exports_rather_than_redefines(self):
		from erpnext_enhancements.chat import audit
		from erpnext_enhancements.chat.governance import access_report

		self.assertIs(access_report.REASON_CATEGORIES, audit.REASON_CATEGORIES)

	def test_an_unknown_category_is_dropped_rather_than_stored(self):
		"""Free text arriving through the category field is the leak D-3 prevents."""
		from erpnext_enhancements.chat import audit

		self.assertIsNone(audit.normalise_reason_category("Because I felt like it"))
		self.assertIsNone(audit.normalise_reason_category(""))
		self.assertIsNone(audit.normalise_reason_category(None))
		self.assertEqual(audit.normalise_reason_category("  Security investigation  "), "Security investigation")

	def test_the_doctype_select_matches_the_constant(self):
		"""Both audit doctypes offer exactly these options, plus a blank for 'not recorded'.

		A Select whose options have drifted from the constant that validates them accepts a
		value the form cannot offer, or offers one the writer will silently drop.
		"""
		import json
		import pathlib

		from erpnext_enhancements.chat import audit

		root = pathlib.Path(__file__).resolve().parents[1] / "chat" / "doctype"
		for name in ("chat_audit_log", "chat_retrieval_audit"):
			meta = json.loads((root / name / f"{name}.json").read_text(encoding="utf-8"))
			field = next(f for f in meta["fields"] if f["fieldname"] == "reason_category")
			options = [o for o in field["options"].split("\n") if o.strip()]
			self.assertEqual(
				options, list(audit.REASON_CATEGORIES), f"{name} Select has drifted"
			)
			self.assertTrue(
				field["options"].startswith("\n"),
				f"{name} must offer a blank option — 'not recorded' is a real state",
			)
			self.assertIsNone(
				field.get("default"),
				f"{name}.reason_category must have NO default: on a normal doctype MariaDB "
				"writes a column default into every existing row as part of the ALTER, which "
				"would claim a category for rows that never had one",
			)


class OptionalChainingTest(unittest.TestCase):
	"""`reason_category` is signed only on rows that carry one, and that buys both properties.

	The obvious implementation was to leave a new governance column unsigned, because adding a
	key to a chained tuple re-serialises every historical row and the verifier reports the
	whole log as tampered. That is true of a *mandatory* key. Making it optional — omitted
	from the payload when empty — means a row without a category hashes exactly as it did
	before the column existed, while a row with one is fully covered.

	Both halves need a test, because getting either wrong is silent: a broken chain looks like
	tampering, and an unsigned field looks like nothing at all.
	"""

	def setUp(self):
		from erpnext_enhancements.chat import audit

		self.audit = audit
		self.row = {
			"event_type": "oversight_role_granted",
			"actor": "admin@example.com",
			"subject_user": "ada@example.com",
			"room": None,
			"reference_doctype": "User",
			"reference_name": "ada@example.com",
			"reason": "granting oversight for the Q3 compliance review",
			"detail": None,
			"recorded_at": "2026-08-14 09:00:00",
			"affected_count": 1,
			"first_seq": 0,
			"last_seq": 0,
		}

	def _hash(self, row):
		return self.audit.compute_governance_chain_hash(row, "previous-hash")

	def test_a_row_without_a_category_hashes_as_it_did_before_the_column_existed(self):
		"""The backward-compatibility half. Absent, empty and whitespace are all 'no category'.

		If any of these produced a different hash from the others, rows written across the
		release boundary would verify inconsistently — which reads as tampering.
		"""
		baseline = self._hash(dict(self.row))
		for value in (None, "", "   "):
			self.assertEqual(
				self._hash(dict(self.row, reason_category=value)),
				baseline,
				f"reason_category={value!r} changed the hash of a row that has no category",
			)

	def test_setting_a_category_changes_the_hash(self):
		"""The tamper-evidence half, forwards."""
		self.assertNotEqual(
			self._hash(dict(self.row, reason_category="HR or personnel matter")),
			self._hash(dict(self.row)),
		)

	def test_reclassifying_a_read_is_detected(self):
		"""The edit this actually guards against.

		An operator with SQL access rewriting `reason_category` from 'HR or personnel matter' to
		'Technical maintenance' is re-labelling why they read somebody's messages. Two
		different categories must not hash alike.
		"""
		a = self._hash(dict(self.row, reason_category="HR or personnel matter"))
		b = self._hash(dict(self.row, reason_category="Technical maintenance"))
		self.assertNotEqual(a, b)

	def test_stripping_a_category_off_a_row_is_detected(self):
		"""And backwards: removing the category cannot restore the un-categorised hash."""
		with_category = self._hash(dict(self.row, reason_category="Security investigation"))
		without = self._hash(dict(self.row))
		self.assertNotEqual(with_category, without)

	def test_the_optional_set_is_not_also_in_the_mandatory_set(self):
		"""Belt and braces: a field in both tuples would be signed twice and shadowed once."""
		self.assertFalse(
			set(self.audit._OPTIONAL_CHAINED_FIELDS) & set(self.audit._GOVERNANCE_CHAINED_FIELDS)
		)
		self.assertFalse(
			set(self.audit._OPTIONAL_CHAINED_FIELDS) & set(self.audit._CHAINED_FIELDS)
		)


class AuditorPermissionBoundaryTest(unittest.TestCase):
	"""What `Chat Auditor` may read, and — more importantly — what it may not.

	**G6-2 and G6-7 pull in opposite directions, and this is where they are reconciled.**
	G6-2 asks for the oversight role to hold `read` on every chat DocType. G6-7 asks for
	exactly one audit record per non-participant read *through every path*. Granting
	`Chat Message` a read DocPerm satisfies the first and breaks the second: it opens
	`/api/resource`, the desk list view and the desk report view, none of which writes an
	audit row. The auditor could read every message in the company with no record of having
	done so — the precise outcome decision #12 trades away.

	The resolution: **the auditor reads the record, not the content.** DocPerm on the two
	audit tables so they can review the trail; message bodies only through the audited viewer.
	Written down here because it diverges from G6-2's literal wording, and the next person to
	read that line will otherwise "fix" it.
	"""

	CONTENT_DOCTYPES = (
		"chat_message",
		"chat_message_revision",
		"chat_room_member",
		"chat_context_chunk",
		"chat_room_digest",
		"chat_thread_digest",
		"chat_attachment",
		"chat_inbound_event",
	)
	AUDIT_DOCTYPES = ("chat_audit_log", "chat_retrieval_audit")

	def _perms(self, name):
		import json
		import pathlib

		root = pathlib.Path(__file__).resolve().parents[1] / "chat" / "doctype"
		meta = json.loads((root / name / f"{name}.json").read_text(encoding="utf-8"))
		return meta.get("permissions", [])

	def _auditor_row(self, name):
		return next((p for p in self._perms(name) if p.get("role") == "Chat Auditor"), None)

	def test_the_auditor_can_read_both_audit_tables(self):
		"""Otherwise they cannot review the trail they exist to review."""
		for name in self.AUDIT_DOCTYPES:
			row = self._auditor_row(name)
			self.assertIsNotNone(row, f"{name} grants Chat Auditor nothing")
			self.assertEqual(row.get("read"), 1, name)

	def test_that_grant_is_read_only(self):
		"""An auditor who can edit the record they are auditing is not an auditor."""
		forbidden = ("write", "create", "delete", "submit", "cancel", "amend", "share", "email")
		for name in self.AUDIT_DOCTYPES:
			row = self._auditor_row(name)
			for ptype in forbidden:
				self.assertFalse(
					row.get(ptype),
					f"{name} grants Chat Auditor `{ptype}` — the audit trail is append-only "
					"to everyone, including the person reading it",
				)

	def test_no_content_bearing_doctype_grants_the_auditor_anything(self):
		"""The G6-7 half. A DocPerm here is an unaudited path to message bodies."""
		offenders = [n for n in self.CONTENT_DOCTYPES if self._auditor_row(n) is not None]
		self.assertEqual(
			offenders,
			[],
			"these grant Chat Auditor a DocPerm, which opens /api/resource and the desk "
			f"report view — both unaudited paths to message content: {offenders}",
		)

	def test_the_content_doctypes_still_ship_zero_docperms(self):
		"""Layer 1, unchanged. Asserted here so this file notices if it is relaxed."""
		for name in self.CONTENT_DOCTYPES:
			self.assertEqual(
				self._perms(name), [], f"{name} gained a DocPerm; Layer 1 says it has none"
			)


class CategorySummaryTest(unittest.TestCase):
	"""The vocabulary is correctable by evidence, which requires measuring it."""

	def setUp(self):
		from erpnext_enhancements.chat.governance import access_report

		self.mod = access_report

	def test_it_separates_uncategorised_from_other(self):
		"""Different failures with different fixes.

		`Other` is a person saying "none of these fit" — information about the list.
		Uncategorised is a path that never collected one — a gap in the plumbing.
		"""
		rows = [
			{"reason_category": "Other"},
			{"reason_category": ""},
			{"reason_category": None},
			{"reason_category": "Security investigation"},
		]
		out = self.mod.category_summary(rows)
		self.assertEqual(out["counts"]["Other"], 1)
		self.assertEqual(out["uncategorised"], 2)
		self.assertEqual(out["categorised"], 2)

	def test_an_unknown_value_reconciles_into_other_rather_than_vanishing(self):
		"""A total that does not add up is worse than a value in the wrong bucket."""
		out = self.mod.category_summary([{"reason_category": "Wrangling"}])
		self.assertEqual(out["counts"]["Other"], 1)
		self.assertEqual(out["categorised"], 1)

	def test_a_high_other_share_asks_for_the_list_to_be_revisited(self):
		rows = [{"reason_category": "Other"}] * 3 + [{"reason_category": "Technical maintenance"}]
		self.assertTrue(self.mod.category_summary(rows)["review_vocabulary"])

	def test_a_healthy_spread_does_not(self):
		rows = [{"reason_category": "Technical maintenance"}] * 9 + [{"reason_category": "Other"}]
		self.assertFalse(self.mod.category_summary(rows)["review_vocabulary"])

	def test_no_rows_is_not_a_problem_to_report(self):
		"""Zero reads must not read as a vocabulary failure."""
		out = self.mod.category_summary([])
		self.assertEqual(out["other_share"], 0.0)
		self.assertFalse(out["review_vocabulary"])

	def test_every_category_is_phrased_for_the_employee_not_the_file(self):
		"""A weak check with a real purpose: these strings are read by the subject.

		Sentence case rather than Title Case is the tell that they were written as answers to
		"why?" rather than as filing labels.
		"""
		from erpnext_enhancements.chat import audit

		for name in audit.REASON_CATEGORIES:
			self.assertEqual(name, name[0].upper() + name[1:], name)
			words = [w for w in name.split() if w.isalpha() and len(w) > 3]
			capitalised = [w for w in words[1:] if w[0].isupper()]
			self.assertEqual(capitalised, [], f"{name!r} reads like a filing label")


class ComplianceSummaryTest(unittest.TestCase):
	def setUp(self):
		from erpnext_enhancements.chat.governance import access_report

		self.mod = access_report

	def test_it_counts_by_failure_mode(self):
		rows = [
			{"reason": "HR asked me to review the Jones complaint"},
			{"reason": ""},
			{"reason": "too short"},
			{"reason": "test test test"},
			{"reason": "reviewing the March invoice dispute"},
		]
		self.assertEqual(
			self.mod.compliance_summary(rows),
			{
				self.mod.REASON_OK: 2,
				self.mod.REASON_MISSING: 1,
				self.mod.REASON_TOO_SHORT: 1,
				self.mod.REASON_PLACEHOLDER: 1,
			},
		)

	def test_a_precomputed_grade_is_trusted(self):
		"""`access_rows` grades once, in SQL-row order; the summary must not re-grade."""
		rows = [{"reason": "ignored because the grade is already here", "reason_grade": "missing"}]
		self.assertEqual(self.mod.compliance_summary(rows)[self.mod.REASON_MISSING], 1)


class BodyReadSummaryTest(unittest.TestCase):
	"""The report used to imply a completeness it did not have.

	`_content_rows` selects from `Chat Retrieval Audit` and filters `r.was_participant = 0`.
	A tombstone expansion, an edit-history read and an export all return message **bodies** and
	are recorded on `Chat Audit Log`, which has no `was_participant` column to join on — so
	they cannot appear in that query by construction. A reader counting content rows was
	getting the non-participant reads that went through the *retrieval path*, and nothing said
	so.

	Decided 2026-08-15 (TASK-2026-01581): the rows stay where they are and the report stops
	implying otherwise. Moving them would change what `tombstone_expanded` means for rows
	already written, and `event_type` is signed.
	"""

	def setUp(self) -> None:
		from erpnext_enhancements.chat.governance import access_report

		self.mod = access_report

	def _rows(self):
		return [
			{"kind": self.mod.KIND_CONTENT, "audit_name": "CRA-1"},
			{"kind": self.mod.KIND_CONTENT, "audit_name": "CRA-2"},
			{"kind": self.mod.KIND_GOVERNANCE, "event_type": "tombstone_expanded"},
			{"kind": self.mod.KIND_GOVERNANCE, "event_type": "tombstone_expanded"},
			{"kind": self.mod.KIND_GOVERNANCE, "event_type": "revision_history_read"},
			{"kind": self.mod.KIND_GOVERNANCE, "event_type": "export_downloaded"},
			# Acts about the data rather than reads of it. These must NOT be counted.
			{"kind": self.mod.KIND_GOVERNANCE, "event_type": "oversight_role_granted"},
			{"kind": self.mod.KIND_GOVERNANCE, "event_type": "retention_run"},
			{"kind": self.mod.KIND_GOVERNANCE, "event_type": "chain_verification_failed"},
		]

	def test_the_two_chains_are_counted_separately(self) -> None:
		"""Never one number. They come from different tables with different guarantees — the
		retrieval side carries per-room participation and a seq range, the governance side an
		`affected_count` and no participation at all."""
		summary = self.mod.body_read_summary(self._rows())
		self.assertEqual(summary["via_retrieval_audit"], 2)
		self.assertEqual(summary["via_governance_log"], 4)
		self.assertNotIn("total", summary, "the two figures were merged into one")

	def test_each_governance_event_is_named_rather_than_lumped(self) -> None:
		"""So a request that was never downloaded reads as what it is."""
		by_event = self.mod.body_read_summary(self._rows())["via_governance_log_by_event"]
		self.assertEqual(by_event["tombstone_expanded"], 2)
		self.assertEqual(by_event["revision_history_read"], 1)
		self.assertEqual(by_event["export_downloaded"], 1)

	def test_acts_about_the_data_are_not_counted_as_reads_of_it(self) -> None:
		"""A role grant is not somebody reading a message."""
		by_event = self.mod.body_read_summary(self._rows())["via_governance_log_by_event"]
		for event in ("oversight_role_granted", "retention_run", "chain_verification_failed"):
			self.assertNotIn(event, by_event)

	def test_every_body_read_event_is_one_the_writer_accepts(self) -> None:
		"""The failure this catches is silent: rename an event type and this set stops matching
		anything, so the second figure quietly reads zero and the report is misleading again —
		in the same direction it was before."""
		from erpnext_enhancements.chat import audit

		unknown = sorted(self.mod.BODY_READ_EVENTS - set(audit.GOVERNANCE_EVENTS))
		self.assertFalse(
			unknown,
			"these are counted as body reads but are not event types the writer can produce, "
			f"so they will never match a row: {unknown}",
		)

	def test_an_empty_report_reports_zero_rather_than_nothing(self) -> None:
		summary = self.mod.body_read_summary([])
		self.assertEqual(summary["via_retrieval_audit"], 0)
		self.assertEqual(summary["via_governance_log"], 0)
		self.assertEqual(summary["via_governance_log_by_event"], {})


if __name__ == "__main__":
	unittest.main()
