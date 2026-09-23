"""Bench-free tests: a confirmed AI action runs what was proposed, not the redacted card.

Before v1.524.1 ``confirm_action`` re-executed ``AI Pending Action.arguments``, the copy whose
credential-like keys ``sanitize_arguments`` had replaced with "***REDACTED***", so a
confirmation wrote the placeholder into a real record. FAC 3.0.0's key heuristic matches the
substring "auth", so `author` counts as a credential, and `draft_token` matches its token
rule. ``author_training_course`` could therefore never succeed through a confirmation.

The fix keeps the redacted copy for people and seals the replaced values in a Password field.
These tests pin each part:

- redaction and restoration are exact inverses across a JSON round trip, and restoration
  refuses rather than guesses;
- ``_propose`` stores the seal, refuses one too large to store, and hashes with a key;
- ``confirm_action`` executes the proposed values, reads the seal before the Confirmed
  transition, masks them out of what it stores, and refuses a card it cannot restore instead
  of running the placeholder;
- the controller deletes the seal on every save that leaves the action non-Pending.

Plain ``unittest`` under the stub set ``test_assistant_tools_schema.install_stubs`` provides
(no site, no FAC). Every frappe attribute a test needs is patched per test and restored.

Run: python -m unittest erpnext_enhancements.tests.test_ai_gate_sealed_arguments -v
"""

import hashlib
import hmac
import json
import re
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_gate = None
gating_api = None

# FAC 3.0.0's frappe_assistant_core/core/base_tool.py::_is_sensitive_key, copied so these tests
# exercise the predicate production actually uses. The stub env has no FAC, and the fallback
# heuristic has no "auth" and no token-word rule.
_FAC_ALWAYS_SENSITIVE = (
    "password", "secret", "api_key", "apikey", "auth", "bearer", "credential", "private_key",
)
_FAC_TOKEN_RE = re.compile(r"(?:^|[_\W])token(?:$|[_\W])", re.IGNORECASE)


def fac_is_sensitive_key(key):
    if not isinstance(key, str):
        return False
    lower = key.lower()
    if any(s in lower for s in _FAC_ALWAYS_SENSITIVE):
        return True
    return bool(_FAC_TOKEN_RE.search(lower))


def setUpModule():
    global _gate, gating_api
    from erpnext_enhancements.tests.test_assistant_tools_schema import install_stubs

    install_stubs()
    import frappe

    if not hasattr(frappe, "whitelist"):
        frappe.whitelist = lambda *a, **k: (lambda f: f)

    from erpnext_enhancements.assistant_tools import _gate as gate_module
    from erpnext_enhancements.assistant_tools import gating_api as api_module

    _gate = gate_module
    gating_api = api_module


def roundtrip(value):
    """What storage does to a value: JSON out, JSON back."""
    return json.loads(json.dumps(value, default=str))


class FacPredicateMixin:
    """Run the test under FAC 3.0.0's real key predicate."""

    def setUp(self):
        super().setUp()
        base_tool = sys.modules["frappe_assistant_core.core.base_tool"]
        patcher = mock.patch.object(base_tool, "_is_sensitive_key", fac_is_sensitive_key, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)


PROPOSED = {
    "doctype": "Help Article",
    "data": {
        "title": "Draining a basin",
        "author": "Jordan Rivers",
        "api_key": "sk_live_0123456789",
        "rows": [
            {"qty": 2, "password": "correct horse battery"},
            {"qty": 3, "auth": {"user": "svc", "pin": "44556677"}},
        ],
    },
    "draft_token": "dt_abcdef123456",
}


class TestRedactAndUnseal(FacPredicateMixin, unittest.TestCase):
    def test_the_real_predicate_catches_ordinary_names(self):
        # The false positives that made this a correctness bug rather than a secrecy nicety.
        self.assertTrue(fac_is_sensitive_key("author"))
        self.assertTrue(fac_is_sensitive_key("draft_token"))
        self.assertFalse(fac_is_sensitive_key("input_tokens"))
        sanitized = _gate.sanitize_arguments(PROPOSED)
        self.assertEqual(sanitized["data"]["author"], _gate.REDACTED)
        self.assertEqual(sanitized["draft_token"], _gate.REDACTED)

    def test_sanitize_is_redact_without_the_seal(self):
        sanitized, sealed = _gate.redact_arguments(PROPOSED)
        self.assertEqual(sanitized, _gate.sanitize_arguments(PROPOSED))
        self.assertEqual(len(sealed), 5)

    def test_unseal_restores_exactly_after_storage(self):
        sanitized, sealed = _gate.redact_arguments(PROPOSED)
        stored, stored_seal = roundtrip(sanitized), roundtrip(sealed)
        self.assertEqual(_gate.unseal_arguments(stored, stored_seal), PROPOSED)

    def test_a_dict_valued_secret_comes_back_whole(self):
        sanitized, sealed = _gate.redact_arguments(PROPOSED)
        restored = _gate.unseal_arguments(roundtrip(sanitized), roundtrip(sealed))
        self.assertEqual(restored["data"]["rows"][1]["auth"], {"user": "svc", "pin": "44556677"})

    def test_redaction_does_not_touch_the_input(self):
        original = roundtrip(PROPOSED)
        _gate.redact_arguments(PROPOSED)
        self.assertEqual(PROPOSED, original)

    def _refuses(self, arguments, sealed):
        with self.assertRaises(_gate.SealError):
            _gate.unseal_arguments(arguments, sealed)

    def test_unseal_refuses_a_path_that_does_not_end_at_the_placeholder(self):
        self._refuses({"password": "something else"}, [[["password"], "x"]])

    def test_unseal_refuses_a_missing_key(self):
        self._refuses({"data": {}}, [[["data", "password"], "x"]])

    def test_unseal_refuses_an_index_into_a_dict_and_a_key_into_a_list(self):
        self._refuses({"rows": {"0": {"password": _gate.REDACTED}}}, [[["rows", 0, "password"], "x"]])
        self._refuses({"rows": [{"password": _gate.REDACTED}]}, [[["rows", "0", "password"], "x"]])

    def test_unseal_refuses_a_bool_or_out_of_range_index(self):
        args = {"rows": [{"password": _gate.REDACTED}]}
        self._refuses(roundtrip(args), [[["rows", True, "password"], "x"]])
        self._refuses(roundtrip(args), [[["rows", 5, "password"], "x"]])

    def test_unseal_refuses_malformed_entries(self):
        self._refuses({"password": _gate.REDACTED}, [["password"]])
        self._refuses({"password": _gate.REDACTED}, [[[], "x"]])
        self._refuses({"password": _gate.REDACTED}, ["password"])

    def test_unrestored_redaction_is_the_placeholder_under_a_credential_key(self):
        sanitized, sealed = _gate.redact_arguments(PROPOSED)
        self.assertTrue(_gate.has_unrestored_redaction(roundtrip(sanitized)))
        restored = _gate.unseal_arguments(roundtrip(sanitized), roundtrip(sealed))
        self.assertFalse(_gate.has_unrestored_redaction(restored))
        # The sanitizer cannot put the placeholder under an ordinary key, so a literal one
        # there is somebody's actual value, not a lost one.
        self.assertFalse(_gate.has_unrestored_redaction({"data": {"notes": _gate.REDACTED}}))
        self.assertTrue(_gate.has_unrestored_redaction({"rows": [{"api_key": _gate.REDACTED}]}))


class TestSealPayloadAndMasking(unittest.TestCase):
    def test_nothing_redacted_means_no_seal(self):
        self.assertIsNone(_gate.seal_payload([]))

    def test_payload_is_the_json_of_the_entries(self):
        sealed = [[["data", "password"], "hunter22"]]
        self.assertEqual(json.loads(_gate.seal_payload(sealed)), sealed)

    def test_an_oversized_seal_is_refused_not_truncated(self):
        with self.assertRaises(_gate.SealError):
            _gate.seal_payload([[["password"], "x" * (_gate.SEALED_MAX_BYTES + 1)]])

    def test_mask_replaces_sealed_strings_everywhere(self):
        sealed = [[["api_key"], "sk_live_0123456789"], [["auth"], {"pin": "44556677"}]]
        result = {
            "message": "Saved sk_live_0123456789",
            "rows": ["pin 44556677 ok", 7],
        }
        masked = _gate.mask_secrets(result, sealed)
        self.assertEqual(masked["message"], f"Saved {_gate.REDACTED}")
        self.assertEqual(masked["rows"], [f"pin {_gate.REDACTED} ok", 7])
        self.assertEqual(_gate.mask_secrets("error: sk_live_0123456789", sealed), f"error: {_gate.REDACTED}")

    def test_mask_leaves_short_values_non_strings_and_empty_seals_alone(self):
        sealed = [[["author"], "Jo"], [["auth_required"], 1]]
        self.assertEqual(_gate.mask_secrets("Jo wrote 1 line", sealed), "Jo wrote 1 line")
        result = {"a": "b"}
        self.assertIs(_gate.mask_secrets(result, []), result)

    def test_mask_takes_the_longest_secret_first(self):
        sealed = [[["a"], "abcdef"], [["b"], "abcdefgh"]]
        self.assertEqual(_gate.mask_secrets("xabcdefghx", sealed), f"x{_gate.REDACTED}x")


class TestFingerprint(unittest.TestCase):
    def test_keyed_fingerprint_is_an_hmac(self):
        args = {"b": 2, "a": 1}
        canonical = json.dumps(args, sort_keys=True, default=str)
        expected = hmac.new(b"k", f"u@x|t|{canonical}".encode(), hashlib.sha256).hexdigest()
        self.assertEqual(_gate.args_fingerprint("u@x", "t", args, key="k"), expected)
        self.assertNotEqual(expected, _gate.args_fingerprint("u@x", "t", args))

    def test_a_different_secret_is_a_different_card(self):
        a = _gate.args_fingerprint("u@x", "t", {"password": "one-secret"}, key="k")
        b = _gate.args_fingerprint("u@x", "t", {"password": "two-secret"}, key="k")
        self.assertNotEqual(a, b)

    def test_keyed_fingerprint_is_stable_and_order_independent(self):
        a = _gate.args_fingerprint("u@x", "t", {"a": 1, "b": 2}, key="k")
        self.assertEqual(a, _gate.args_fingerprint("u@x", "t", {"b": 2, "a": 1}, key="k"))
        self.assertNotEqual(a, _gate.args_fingerprint("u@x", "t", {"a": 1, "b": 2}, key="other"))


class TestProposeStoresTheSeal(FacPredicateMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        import frappe

        self.inserted = []
        test = self

        class FakeDoc:
            def __init__(self, values):
                self.__dict__.update(values)
                self.name = "AI-PA-2026-00001"

            def insert(self, ignore_permissions=False):
                test.inserted.append(dict(self.__dict__))

        db = types.SimpleNamespace(
            get_value=lambda *a, **k: None,
            get_single_value=lambda *a, **k: 1,
            commit=lambda: None,
        )
        patches = [
            mock.patch.object(frappe, "session", types.SimpleNamespace(user="nik@x"), create=True),
            mock.patch.object(frappe, "db", db, create=True),
            mock.patch.object(frappe, "get_doc", lambda values: FakeDoc(values), create=True),
            mock.patch.object(frappe.utils, "cint", lambda v: int(v or 0), create=True),
            mock.patch.object(frappe.utils, "add_to_date", lambda *a, **k: "2026-09-23 19:00:00", create=True),
            mock.patch.object(_gate, "_tool_category", lambda tool: "write"),
            mock.patch.object(_gate, "_notify_requester", lambda action: None),
            mock.patch.object(_gate, "_fingerprint_key", lambda: "site-key"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.tool = types.SimpleNamespace(name="author_training_course")

    def test_the_card_is_redacted_and_the_seal_holds_what_it_hides(self):
        args = {"draft_token": "dt_abcdef123456", "spec": {"title": "Pump basics"}}
        response = _gate._propose(self.tool, args)
        self.assertTrue(response["success"])
        (row,) = self.inserted
        self.assertEqual(json.loads(row["arguments"])["draft_token"], _gate.REDACTED)
        self.assertNotIn("dt_abcdef123456", row["arguments"])
        self.assertEqual(json.loads(row["sealed_arguments"]), [[["draft_token"], "dt_abcdef123456"]])
        self.assertEqual(row["args_hash"], _gate.args_fingerprint("nik@x", "author_training_course", args, key="site-key"))

    def test_no_credential_keys_means_no_seal(self):
        _gate._propose(self.tool, {"spec": {"title": "Pump basics"}})
        (row,) = self.inserted
        self.assertIsNone(row["sealed_arguments"])

    def test_an_unstorable_seal_is_refused_before_anything_is_written(self):
        response = _gate._propose(self.tool, {"draft_token": "x" * (_gate.SEALED_MAX_BYTES + 10)})
        self.assertFalse(response["success"])
        self.assertIn("not queued", response["error"])
        self.assertEqual(self.inserted, [])


class FakeThrow(Exception):
    pass


class FakeAction:
    """An AI Pending Action as confirm_action sees it."""

    def __init__(self, arguments, sealed_arguments=None, password=None, tool_name="author_training_course"):
        self.name = "AI-PA-2026-00001"
        self.status = "Pending"
        self.requested_by = "nik@x"
        self.expires_at = None
        self.tool_name = tool_name
        self.risk = "Medium"
        self.summary = "Author a training course"
        self.target_name = None
        self.arguments = arguments
        self.sealed_arguments = sealed_arguments
        self.result = None
        self.error = None
        self.action_log = None
        self.decided_by = None
        self.decided_at = None
        self._password = password
        self.saves = []
        self.password_read_while = []

    def get(self, key):
        return getattr(self, key, None)

    def set(self, key, value):
        setattr(self, key, value)

    def get_password(self, fieldname, raise_exception=True):
        self.password_read_while.append(self.status)
        return self._password

    def save(self, ignore_permissions=False):
        self.saves.append(self.status)


class TestConfirmActionRunsWhatWasProposed(FacPredicateMixin, unittest.TestCase):
    PROPOSAL = {"draft_token": "dt_abcdef123456", "spec": {"title": "Pump basics"}}

    def setUp(self):
        super().setUp()
        import frappe

        self.executed = []
        self.logs = []
        self.events = []
        self.execute_result = {"success": True, "name": "TC-0001", "message": "used dt_abcdef123456"}
        self.execute_error = None
        test = self

        class Registry:
            def execute_tool(self, tool_name, arguments):
                test.events.append(("execute", test.action.status))
                test.executed.append((tool_name, roundtrip(arguments)))
                if test.execute_error:
                    raise test.execute_error
                return test.execute_result

        registry_module = types.ModuleType("frappe_assistant_core.core.tool_registry")
        registry_module.get_tool_registry = lambda: Registry()

        def throw(message, exc=None):
            raise FakeThrow(message)

        db = types.SimpleNamespace(commit=lambda: test.events.append(("commit", test.action.status)), rollback=lambda: test.events.append(("rollback", None)))
        patches = [
            mock.patch.dict(sys.modules, {"frappe_assistant_core.core.tool_registry": registry_module}),
            mock.patch.object(frappe, "session", types.SimpleNamespace(user="nik@x"), create=True),
            mock.patch.object(frappe, "get_roles", lambda *a: ["System Manager"], create=True),
            mock.patch.object(frappe, "get_doc", lambda doctype, name: test.action, create=True),
            mock.patch.object(frappe, "throw", throw, create=True),
            mock.patch.object(frappe, "PermissionError", PermissionError, create=True),
            mock.patch.object(frappe, "db", db, create=True),
            mock.patch.object(
                frappe, "flags",
                types.SimpleNamespace(ai_gate_bypass=False, ai_gate_pending=None, ai_action_transition=False),
                create=True,
            ),
            mock.patch.object(gating_api, "insert_action_log", lambda **kw: test.logs.append(kw) or "AI-LOG-1"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _sealed_action(self, proposal=None):
        sanitized, sealed = _gate.redact_arguments(proposal or self.PROPOSAL)
        payload = _gate.seal_payload(sealed)
        return FakeAction(json.dumps(sanitized), "*" * len(payload) if payload else None, payload)

    def test_the_tool_receives_the_proposed_values(self):
        self.action = self._sealed_action()
        self.assertEqual(gating_api.confirm_action(self.action.name)["status"], "Executed")
        self.assertEqual(self.executed, [("author_training_course", self.PROPOSAL)])
        self.assertEqual(self.action.status, "Executed")

    def test_the_seal_is_read_while_pending_and_confirmed_is_committed_before_execution(self):
        self.action = self._sealed_action()
        gating_api.confirm_action(self.action.name)
        self.assertEqual(self.action.password_read_while, ["Pending"])
        self.assertEqual(self.action.saves[0], "Confirmed")
        self.assertLess(self.events.index(("commit", "Confirmed")), self.events.index(("execute", "Confirmed")))

    def test_the_stored_result_and_log_do_not_carry_the_value(self):
        self.action = self._sealed_action()
        gating_api.confirm_action(self.action.name)
        self.assertNotIn("dt_abcdef123456", self.action.result)
        self.assertIn(_gate.REDACTED, self.action.result)
        self.assertNotIn("dt_abcdef123456", json.dumps(self.logs[-1]["result"]))
        self.assertEqual(self.action.target_name, "TC-0001")

    def test_a_failure_message_that_quotes_the_value_is_masked_everywhere(self):
        self.action = self._sealed_action()
        self.execute_error = ValueError("Invalid draft token dt_abcdef123456")
        with self.assertRaises(FakeThrow) as raised:
            gating_api.confirm_action(self.action.name)
        self.assertNotIn("dt_abcdef123456", str(raised.exception))
        self.assertNotIn("dt_abcdef123456", self.action.error)
        self.assertNotIn("dt_abcdef123456", self.logs[-1]["error"])
        self.assertEqual(self.action.status, "Failed")
        self.assertIn(("rollback", None), self.events)

    def test_a_seal_that_cannot_be_decrypted_fails_without_running(self):
        self.action = self._sealed_action()
        self.action._password = None
        with self.assertRaises(FakeThrow) as raised:
            gating_api.confirm_action(self.action.name)
        self.assertEqual(self.executed, [])
        self.assertEqual(self.action.status, "Failed")
        self.assertEqual(self.action.decided_by, "nik@x")
        self.assertIn("Not executed", str(raised.exception))
        self.assertEqual(self.logs[-1]["error_type"], "SealError")

    def test_a_card_from_before_the_seal_fails_without_running(self):
        # What every pre-v1.524.1 proposal with a credential-like key looks like.
        sanitized = _gate.sanitize_arguments(self.PROPOSAL)
        self.action = FakeAction(json.dumps(sanitized))
        with self.assertRaises(FakeThrow):
            gating_api.confirm_action(self.action.name)
        self.assertEqual(self.executed, [])
        self.assertEqual(self.action.status, "Failed")

    def test_a_seal_that_disagrees_with_the_card_fails_without_running(self):
        self.action = self._sealed_action()
        self.action._password = json.dumps([[["spec", "title"], "swapped"]])
        with self.assertRaises(FakeThrow):
            gating_api.confirm_action(self.action.name)
        self.assertEqual(self.executed, [])

    def test_nothing_sealed_runs_the_stored_arguments_unchanged(self):
        proposal = {"doctype": "ToDo", "data": {"description": "Call the pump vendor", "notes": _gate.REDACTED}}
        self.action = FakeAction(json.dumps(proposal), tool_name="create_document")
        self.execute_result = {"success": True, "name": "TODO-1", "message": "Call the pump vendor"}
        gating_api.confirm_action(self.action.name)
        self.assertEqual(self.executed, [("create_document", proposal)])
        self.assertEqual(json.loads(self.action.result), self.execute_result)


class TestControllerDeletesTheSeal(unittest.TestCase):
    """The controller clears the Password field on every non-Pending save.

    Frappe runs validate() before _save_passwords(), so clearing the field here is what
    deletes the __Auth row, in the same save.
    """

    @classmethod
    def setUpClass(cls):
        import frappe

        document_module = types.ModuleType("frappe.model.document")
        document_module.Document = type("Document", (), {})
        model_module = types.ModuleType("frappe.model")
        model_module.document = document_module
        cls._modules = mock.patch.dict(
            sys.modules, {"frappe.model": model_module, "frappe.model.document": document_module}
        )
        cls._modules.start()
        cls._flags = mock.patch.object(
            frappe, "flags", types.SimpleNamespace(ai_action_transition=True), create=True
        )
        cls._flags.start()
        from erpnext_enhancements.ai_governance.doctype.ai_pending_action import ai_pending_action

        cls.controller = ai_pending_action.AIPendingAction

    @classmethod
    def tearDownClass(cls):
        cls._flags.stop()
        cls._modules.stop()
        sys.modules.pop("erpnext_enhancements.ai_governance.doctype.ai_pending_action.ai_pending_action", None)

    def _doc(self, status, new=False):
        doc = types.SimpleNamespace(status=status, sealed_arguments="*" * 40)
        doc.get = lambda key: getattr(doc, key, None)
        doc.is_new = lambda: new
        doc.get_doc_before_save = lambda: types.SimpleNamespace(status="Pending")
        return doc

    def test_every_decided_status_deletes_the_seal(self):
        for status in ("Confirmed", "Executed", "Failed", "Cancelled", "Expired"):
            doc = self._doc(status)
            self.controller.validate(doc)
            self.assertIsNone(doc.sealed_arguments, status)

    def test_a_pending_action_keeps_it(self):
        for new in (True, False):
            doc = self._doc("Pending", new=new)
            self.controller.validate(doc)
            self.assertEqual(doc.sealed_arguments, "*" * 40)


class TestDoctypeDeclaresTheSeal(unittest.TestCase):
    def test_sealed_arguments_is_a_hidden_password_field(self):
        path = REPO_ROOT / "erpnext_enhancements/ai_governance/doctype/ai_pending_action/ai_pending_action.json"
        meta = json.loads(path.read_text(encoding="utf-8"))
        field = next(f for f in meta["fields"] if f["fieldname"] == "sealed_arguments")
        self.assertEqual(field["fieldtype"], "Password")
        self.assertEqual(field.get("hidden"), 1)
        self.assertEqual(field.get("no_copy"), 1)
        self.assertIn("sealed_arguments", meta["field_order"])
        self.assertTrue(meta.get("track_changes"))  # why it must be a Password field: see below
        # track_changes writes a Version row with the old and new value of every changed field.
        # A Password field's column only ever holds asterisks, so the Version row does too; a
        # Long Text holding ciphertext or plaintext would copy it into tabVersion on every save.


if __name__ == "__main__":
    unittest.main()
