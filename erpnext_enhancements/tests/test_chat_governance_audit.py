"""The governance audit stream: its chain, and the first thing that writes to it.

**Bench-free, `unittest`, own `frappe` stub in `setUpModule`** — the house pattern, and its own
`ci.yml` step so no other suite's stubs are in `sys.modules`.

WHAT IS BEING GUARDED
=====================

`Chat Audit Log` shipped in v1.285.0 as a vault with nothing in it: four immutability layers and
no writer, which is §6 step 5's ordering ("build the vault before filling it"). This is the
first writer, and two things about it are worth a test rather than a comment.

**The two chains must not be one.** One chain per table: interleaving two writers into a single
chain makes every concurrent write a fork, and the verifier reports a fork as a permanent break
indistinguishable from tampering. Separate genesis strings, separate locks, separate payload
shapes — and a test that the payloads really are different, because "we meant them to be
separate" is not a property.

**A grant must be recorded, and a non-grant must not be.** The role hook runs on every save of
every `User` on the site. A profiled user's roles are rebuilt wholesale on every save, so the
diff has to be set-based; a row-based diff logs a grant and a revoke every time anybody with a
Role Profile saves, which is noise in the one table whose value is that it is quiet.
"""

from __future__ import annotations

import sys
import types
import unittest

_STUBBED: list[str] = []
_SETTINGS: dict[str, object] = {}


def setUpModule() -> None:
	for name in ("frappe", "frappe.utils", "frappe.model", "frappe.model.document"):
		if name not in sys.modules:
			sys.modules[name] = types.ModuleType(name)
			_STUBBED.append(name)

	frappe = sys.modules["frappe"]
	utils = sys.modules["frappe.utils"]

	def _cint(value: object) -> int:
		"""Frappe's `cint`, near enough — and `None` → 0 is the half that matters here.

		The real one coerces anything unparseable to 0. Getting that wrong in the stub would
		make `test_an_int_field_hashes_the_same_whether_null_or_zero` pass or fail for a
		reason that has nothing to do with the code under test.
		"""
		try:
			return int(float(value))  # type: ignore[arg-type]
		except (TypeError, ValueError):
			return 0

	utils.cint = _cint
	utils.now = lambda: "2026-08-14 12:00:00.000000"
	frappe.utils = utils
	frappe.cint = utils.cint

	frappe.session = types.SimpleNamespace(user="nikolas@example.com")
	frappe.log_error = lambda **_kw: None
	frappe._ = lambda text: text

	class _DB:
		def get_single_value(self, _doctype, field):
			return _SETTINGS.get(field)

		def sql(self, *_a, **_k):
			return []

	frappe.db = _DB()

	document = sys.modules["frappe.model.document"]
	if not hasattr(document, "Document"):
		document.Document = object


def tearDownModule() -> None:
	for name in _STUBBED:
		sys.modules.pop(name, None)


class TheTwoChainsAreSeparate(unittest.TestCase):
	"""One chain per table. A shared chain forks on every concurrent write."""

	def setUp(self) -> None:
		from erpnext_enhancements.chat import audit

		self.audit = audit

	def test_the_genesis_strings_differ(self) -> None:
		"""Identical genesis strings would make two empty chains verify against each other's
		first row — which looks like it works right up until it does not."""
		self.assertNotEqual(self.audit._GENESIS, self.audit._GOVERNANCE_GENESIS)

	def test_the_locks_differ(self) -> None:
		"""A shared lock would serialise two independent streams against each other, turning
		every governance write into a wait on an unrelated retrieval read."""
		self.assertNotEqual(self.audit._CHAIN_LOCK, self.audit._GOVERNANCE_CHAIN_LOCK)

	def test_the_governance_hash_covers_the_governance_fields(self) -> None:
		row = {
			"event_type": "oversight_role_granted",
			"actor": "nikolas@example.com",
			"subject_user": "james@example.com",
			"recorded_at": "2026-08-14 12:00:00",
			"affected_count": 0,
		}
		first = self.audit.compute_governance_chain_hash(row, "genesis")
		self.assertEqual(len(first), 64, "not a sha256 hex digest")

		# Every signed field must move the hash. A field in the tuple that does not is a field
		# somebody can edit without breaking the chain.
		for field in self.audit._GOVERNANCE_CHAINED_FIELDS:
			with self.subTest(field=field):
				mutated = dict(row)
				mutated[field] = 99 if field in self.audit._GOVERNANCE_INT_FIELDS else "tampered"
				self.assertNotEqual(
					self.audit.compute_governance_chain_hash(mutated, "genesis"),
					first,
					f"{field} is in _GOVERNANCE_CHAINED_FIELDS but editing it does not break "
					f"the chain, so it is not actually signed",
				)

	def test_the_previous_hash_is_part_of_the_signature(self) -> None:
		"""Otherwise rows could be reordered or removed without detection — the chain would be
		a set of independent checksums rather than a chain."""
		row = {"event_type": "retention_run", "actor": "Administrator"}
		self.assertNotEqual(
			self.audit.compute_governance_chain_hash(row, "head-a"),
			self.audit.compute_governance_chain_hash(row, "head-b"),
		)

	def test_an_int_field_hashes_the_same_whether_null_or_zero(self) -> None:
		"""What makes adding an integer column backward-compatible.

		A row written before the column existed signed `None`; the verifier reads `0` back
		from MariaDB. Without the `cint` coercion those hash differently and **every**
		pre-existing row reports as tampered — an alarm firing on the whole log, for a schema
		change.
		"""
		base = {"event_type": "retention_run", "actor": "Administrator"}
		as_null = dict(base, affected_count=None)
		as_zero = dict(base, affected_count=0)
		self.assertEqual(
			self.audit.compute_governance_chain_hash(as_null, "g"),
			self.audit.compute_governance_chain_hash(as_zero, "g"),
		)


class EveryDataFieldIsSigned(unittest.TestCase):
	"""Set equality against the DocType JSON, and it closes a hole the other test cannot.

	`test_the_governance_hash_covers_the_governance_fields` iterates
	`_GOVERNANCE_CHAINED_FIELDS` and proves each entry moves the hash. That is necessary and
	**not sufficient**: deleting a field from the tuple makes the loop skip it, so the test
	goes green while the field becomes editable without breaking the chain — which is precisely
	the failure the chain exists to catch. Verified by deleting `subject_user` and watching the
	other test stay green.

	So this one reads the JSON instead: every data field on the DocType is signed, unless it is
	named here with a reason.
	"""

	#: Fields deliberately not signed, each with why. Keep this at one entry if you can.
	NOT_SIGNED = {
		"chain_hash": (
			"the signature itself. Including it would require knowing the hash before "
			"computing it."
		),
	}

	#: Fieldtypes that carry no data — pure layout.
	LAYOUT = {"Section Break", "Column Break", "Tab Break", "HTML", "Heading"}

	def test_every_data_field_is_in_the_signed_set(self) -> None:
		import json
		import pathlib

		from erpnext_enhancements.chat import audit

		path = (
			pathlib.Path(audit.__file__).resolve().parent
			/ "doctype"
			/ "chat_audit_log"
			/ "chat_audit_log.json"
		)
		fields = {
			f["fieldname"]
			for f in json.loads(path.read_text(encoding="utf-8"))["fields"]
			if f.get("fieldtype") not in self.LAYOUT
		}
		# Optionally-chained fields count as signed. They are omitted from the payload when
		# empty — which is what lets a column be added to an already-hashed log without
		# re-serialising every historical row and reporting the lot as tampered — and are
		# signed whenever they carry a value. Both directions of edit change the hash:
		# removing a category drops a key, adding one to a row that had none adds a key. So
		# the property this test protects holds. See `audit._OPTIONAL_CHAINED_FIELDS`.
		signed = set(audit._GOVERNANCE_CHAINED_FIELDS) | set(audit._OPTIONAL_CHAINED_FIELDS)

		unsigned = sorted(fields - signed - set(self.NOT_SIGNED))
		self.assertFalse(
			unsigned,
			"these Chat Audit Log fields are NOT covered by the chain, so they can be edited "
			"in the database without the verifier noticing:\n  " + "\n  ".join(unsigned),
		)

		stale = sorted(signed - fields)
		self.assertFalse(
			stale,
			"these are signed but no longer exist on the DocType. A signed field that is not "
			"stored hashes as None forever, which is a silent no-op in the signature:\n  "
			+ "\n  ".join(stale),
		)


class TheEventVocabularyIsClosed(unittest.TestCase):
	def test_the_constant_matches_the_doctype_select(self) -> None:
		"""Checked against the JSON, so adding an option without adding it here fails.

		The writer validates `event_type` itself rather than leaving it to Frappe's Select
		validation, because Frappe would raise at **save** time — after the act being recorded
		has already happened, and with nothing recorded.
		"""
		import json
		import pathlib

		from erpnext_enhancements.chat import audit

		path = (
			pathlib.Path(audit.__file__).resolve().parent
			/ "doctype"
			/ "chat_audit_log"
			/ "chat_audit_log.json"
		)
		field = next(
			f for f in json.loads(path.read_text(encoding="utf-8"))["fields"] if f["fieldname"] == "event_type"
		)
		self.assertEqual(
			set(field["options"].split("\n")),
			set(audit.GOVERNANCE_EVENTS),
			"GOVERNANCE_EVENTS and the Select options disagree; one of them will reject a "
			"value the other produces",
		)


class TheRoleGrantHook(unittest.TestCase):
	"""§4.A.4. Granting oversight is itself an oversight event."""

	def setUp(self) -> None:
		from erpnext_enhancements.chat import audit
		from erpnext_enhancements.chat.governance import role_grants

		self.role_grants = role_grants
		self.recorded: list[dict] = []
		self._real_writer = audit.record_governance_event
		audit.record_governance_event = lambda **kw: self.recorded.append(kw) or "LOG-1"
		self.audit = audit
		_SETTINGS.clear()
		_SETTINGS["admin_oversight_role"] = "Chat Auditor"

	def tearDown(self) -> None:
		self.audit.record_governance_event = self._real_writer
		_SETTINGS.clear()

	@staticmethod
	def _user(name: str, roles: list[str], before: list[str] | None):
		prior = None
		if before is not None:
			prior = types.SimpleNamespace(
				name=name, get=lambda key, _r=before: [{"role": r} for r in _r] if key == "roles" else None
			)
		return types.SimpleNamespace(
			name=name,
			get=lambda key, _r=roles: [{"role": r} for r in _r] if key == "roles" else None,
			get_doc_before_save=lambda: prior,
		)

	def test_granting_the_oversight_role_is_recorded(self) -> None:
		doc = self._user("james@example.com", ["Chat User", "Chat Auditor"], before=["Chat User"])
		self.role_grants.on_user_roles_changed(doc)
		self.assertEqual(len(self.recorded), 1)
		self.assertEqual(self.recorded[0]["event_type"], "oversight_role_granted")
		self.assertEqual(self.recorded[0]["subject_user"], "james@example.com")
		self.assertEqual(self.recorded[0]["actor"], "nikolas@example.com", "the grantER, not the grantEE")

	def test_revoking_it_is_recorded_too(self) -> None:
		doc = self._user("james@example.com", ["Chat User"], before=["Chat User", "Chat Auditor"])
		self.role_grants.on_user_roles_changed(doc)
		self.assertEqual(len(self.recorded), 1)
		self.assertEqual(self.recorded[0]["event_type"], "oversight_role_revoked")

	def test_AN_UNRELATED_SAVE_RECORDS_NOTHING(self) -> None:
		"""The one that keeps the table quiet, and the one a row-based diff would fail.

		A profiled user's roles are rebuilt wholesale on every save, so row identity churns
		while membership does not. Diffing rows would log a grant and a revoke every time
		anybody with a Role Profile saved anything.
		"""
		doc = self._user("james@example.com", ["Chat User", "Sales User"], before=["Sales User", "Chat User"])
		self.role_grants.on_user_roles_changed(doc)
		self.assertEqual(self.recorded, [], "an unrelated save wrote a governance row")

	def test_a_user_who_keeps_the_role_records_nothing(self) -> None:
		doc = self._user("james@example.com", ["Chat Auditor", "Sales User"], before=["Chat Auditor"])
		self.role_grants.on_user_roles_changed(doc)
		self.assertEqual(self.recorded, [], "holding the role is not granting it")

	def test_nothing_is_recorded_while_the_hatch_is_shut(self) -> None:
		"""`admin_oversight_role` ships blank and blank means nobody. With no role configured
		there is no grant to record, and the hook must cost nothing on every User save."""
		_SETTINGS["admin_oversight_role"] = ""
		doc = self._user("james@example.com", ["Chat Auditor"], before=[])
		self.role_grants.on_user_roles_changed(doc)
		self.assertEqual(self.recorded, [])

	def test_a_new_user_is_not_reported_as_a_mass_grant(self) -> None:
		"""No prior document means a save we cannot diff. Treating the whole set as newly
		granted would file a grant for every role the user has."""
		doc = self._user("new@example.com", ["Chat Auditor"], before=None)
		self.role_grants.on_user_roles_changed(doc)
		self.assertEqual(self.recorded, [])

	def test_the_hook_never_raises(self) -> None:
		"""It runs inside `User.on_update`. A record that cannot be written must not take down
		user administration with it — and the thing it guards against is somebody who already
		holds System Manager, who is not stopped by a veto they can remove."""
		broken = types.SimpleNamespace(
			name="x@example.com",
			get=lambda _key: (_ for _ in ()).throw(RuntimeError("boom")),
			get_doc_before_save=lambda: None,
		)
		self.role_grants.on_user_roles_changed(broken)  # must not raise

		exploding = types.SimpleNamespace(
			name="y@example.com",
			get_doc_before_save=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
		)
		self.role_grants.on_user_roles_changed(exploding)  # must not raise


class TheHookIsActuallyRegistered(unittest.TestCase):
	"""A handler nothing calls is a handler that does nothing, silently."""

	def test_it_is_on_user_on_update_alongside_the_training_one(self) -> None:
		import pathlib

		from erpnext_enhancements.chat import audit

		hooks = (pathlib.Path(audit.__file__).resolve().parents[1] / "hooks.py").read_text(
			encoding="utf-8"
		)
		self.assertIn(
			"erpnext_enhancements.chat.governance.role_grants.on_user_roles_changed",
			hooks,
			"the role-grant audit hook is not registered, so granting oversight records nothing",
		)
		self.assertIn(
			"erpnext_enhancements.training.assignment.on_user_roles_changed",
			hooks,
			"the training handler was dropped while adding the chat one — User.on_update takes "
			"a LIST precisely so both can run",
		)

	def test_the_nightly_verifier_is_scheduled(self) -> None:
		import pathlib

		from erpnext_enhancements.chat import audit

		hooks = (pathlib.Path(audit.__file__).resolve().parents[1] / "hooks.py").read_text(
			encoding="utf-8"
		)
		self.assertIn(
			"erpnext_enhancements.chat.audit.verify_all_chains",
			hooks,
			"the chain is computed and nothing ever checks it — which is the state v1.285.0 "
			"shipped in and this release exists to end",
		)


class TheVerifiersReadEveryFieldTheyVerify(unittest.TestCase):
	"""A field is only signed if the **verifier** reads it back. This is the missing half.

	`EveryDataFieldIsSigned` above asserts that every column is in a signed tuple, and it was
	green throughout the defect this class exists for. It had to be: it inspects the writer's
	tuples and the DocType, and never the verifier's SELECT.

	``verify_chain`` wrote its column list out by hand. ``reason_category`` was added to
	``_OPTIONAL_CHAINED_FIELDS``, the writer began signing it on any row that carried one, and
	the hand-written list was never touched — so the verifier recomputed a payload missing a key
	the writer would have signed. The two agreed only for as long as no caller supplied a value,
	which was eleven releases, and `viewer._stamp_category` was quietly supplying one the whole
	time by writing the column *after* the hash.

	**The failure mode is the reason this is worth a test rather than a convention.** A verifier
	reading fewer columns than the writer signs does not report a break — it verifies a
	*different payload* and reports success. What stops working is the alarm, and nothing else
	looks wrong.

	Both SELECTs are now derived from the tuples, so this asserts the derivation actually reaches
	the statement rather than that the helper exists.
	"""

	def _select_for(self, verifier: str) -> str:
		"""The SQL the verifier issues, captured through the module's own frappe stub."""
		from erpnext_enhancements.chat import audit

		captured: list[str] = []

		class _Recorder:
			def sql(self, query, values=None, as_dict=False, **_kw):
				captured.append(query)
				return []

			def get_single_value(self, *_a, **_k):
				return None

		frappe = sys.modules["frappe"]
		real = frappe.db
		frappe.db = _Recorder()
		try:
			getattr(audit, verifier)()
		finally:
			frappe.db = real
		self.assertTrue(captured, f"{verifier} issued no statement, so this asserts nothing")
		return captured[0]

	def test_the_retrieval_verifier_selects_every_field_the_writer_signs(self) -> None:
		from erpnext_enhancements.chat import audit

		sql = self._select_for("verify_chain")
		missing = [
			name
			for name in audit._CHAINED_FIELDS + audit._OPTIONAL_CHAINED_FIELDS
			if f"`{name}`" not in sql
		]
		self.assertFalse(
			missing,
			"verify_chain signs these fields but never reads them back, so it recomputes a "
			"payload the writer did not sign and reports success anyway:\n  "
			+ "\n  ".join(missing),
		)

	def test_the_governance_verifier_selects_every_field_the_writer_signs(self) -> None:
		"""The same trap was armed on this chain and was never fired.

		Its SELECT *was* derived — but from the mandatory tuple alone, which protects against a
		new mandatory field and not against a new optional one. It stayed quiet only because
		``record_governance_event`` has no ``reason_category`` parameter, so nothing populates
		the column. Adding one would have re-created the defect on the second chain.
		"""
		from erpnext_enhancements.chat import audit

		sql = self._select_for("verify_governance_chain")
		missing = [
			name
			for name in audit._GOVERNANCE_CHAINED_FIELDS + audit._OPTIONAL_CHAINED_FIELDS
			if f"`{name}`" not in sql
		]
		self.assertFalse(
			missing,
			"verify_governance_chain signs these fields but never reads them back:\n  "
			+ "\n  ".join(missing),
		)

	def test_the_column_helper_does_not_emit_a_name_twice(self) -> None:
		"""``recorded_at`` is both signed and quoted in the break report. Twice is a SQL error."""
		from erpnext_enhancements.chat import audit

		columns = audit._select_columns(
			audit._VERIFY_EXTRA_COLUMNS, audit._CHAINED_FIELDS, audit._OPTIONAL_CHAINED_FIELDS
		).split(", ")
		self.assertEqual(len(columns), len(set(columns)))


class TheVerifiersFailureIsItsOwnIncident(unittest.TestCase):
	"""A verifier that cannot run must not look like a chain that verifies.

	Deriving the SELECT from the tuples moves one mistake — a name in a tuple that is not a
	column — from import-time nothing to a query-time error. Unguarded it leaves the scheduled
	job before ``alerts.check`` runs, and no alert is exactly what health looks like.
	"""

	def _run_with_broken_db(self) -> tuple[dict, list[dict]]:
		"""``verify_all_chains`` against a database that raises, with the alert path faked.

		The two governance modules are stubbed rather than imported: the real ``alerts`` pulls
		half of ``frappe.utils`` at module scope, and growing this suite's stub to carry it
		would be adding surface for a test that is about exception handling. Faking them also
		makes the alert calls assertable, which is the half that matters.
		"""
		import types

		from erpnext_enhancements.chat import audit, governance

		calls: list[dict] = []

		alerts = types.ModuleType("erpnext_enhancements.chat.governance.alerts")
		alerts.check = lambda ok, **kw: calls.append({"ok": bool(ok), **kw})
		alert_rules = types.ModuleType("erpnext_enhancements.chat.governance.alert_rules")
		alert_rules.SEVERITY_CRITICAL = "Critical"

		class _Broken:
			"""A database where the *verifier's* statement fails and nothing else does.

			That is the hazard being defended against, and the fidelity matters. Deriving the
			SELECT from the signed tuples means a name that is not a column becomes an error on
			that one statement — the connection is fine, the alert path still works. Failing
			every statement instead would model a dead connection, which is a different
			incident with a different fix, and it would make this test pass or fail for reasons
			that have nothing to do with the code under test.
			"""

			def sql(self, query, *_a, **_k):
				if "tabChat Retrieval Audit" in query or "tabChat Audit Log" in query:
					raise RuntimeError("Unknown column 'nope' in 'field list'")
				return []

			def get_single_value(self, *_a, **_k):
				return None

		frappe = sys.modules["frappe"]
		real_db = frappe.db
		saved = {n: sys.modules.get(f"erpnext_enhancements.chat.governance.{n}") for n in ("alerts", "alert_rules")}
		sys.modules["erpnext_enhancements.chat.governance.alerts"] = alerts
		sys.modules["erpnext_enhancements.chat.governance.alert_rules"] = alert_rules
		governance.alerts = alerts
		governance.alert_rules = alert_rules
		frappe.db = _Broken()
		try:
			result = audit.verify_all_chains()
		finally:
			frappe.db = real_db
			for name, module in saved.items():
				if module is None:
					sys.modules.pop(f"erpnext_enhancements.chat.governance.{name}", None)
				else:
					sys.modules[f"erpnext_enhancements.chat.governance.{name}"] = module
		return result, calls

	def test_a_verifier_that_raises_becomes_a_result_rather_than_an_escape(self) -> None:
		try:
			result, _ = self._run_with_broken_db()
		except Exception as exc:  # pragma: no cover - the regression this test names
			self.fail(
				"verify_all_chains let the verifier's own failure escape the scheduled job, "
				"so no alert is raised at all — which is indistinguishable from a chain that "
				f"verifies: {exc!r}"
			)
		self.assertFalse(result["ok"])
		for scope in ("retrieval", "governance"):
			self.assertTrue(result["results"][scope].get("errored"))
			self.assertIn("could not run", result["results"][scope].get("detail", ""))

	def test_a_broken_verifier_raises_its_own_kind_and_clears_nothing(self) -> None:
		"""The distinction that keeps the alert board honest.

		"The chain is broken" and "the check did not happen" are different incidents with
		different fixes. And ``chain_verification_failed`` must not be *cleared* on a run that
		learned nothing — clearing asserts health, and nobody asserted it.
		"""
		_, calls = self._run_with_broken_db()
		kinds = {c["kind"] for c in calls}
		self.assertIn("chain_verification_errored", kinds)
		self.assertTrue(all(c["ok"] is False for c in calls if c["kind"] == "chain_verification_errored"))
		self.assertNotIn(
			"chain_verification_failed",
			kinds,
			"a run whose verifier could not execute touched the chain's own alert; clearing "
			"it would claim health nobody established, and raising it would blame the chain "
			"for the verifier's failure",
		)


if __name__ == "__main__":
	unittest.main()
