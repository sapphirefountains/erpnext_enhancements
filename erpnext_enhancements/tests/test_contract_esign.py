"""Bench-free unit tests for contract e-signature foundations.

Stubs a minimal ``frappe`` (no site/bench) so the pure logic runs under plain
unittest. The stub is installed in ``setUpModule`` (execution time), not at
import, so it never fools the bench-only suites' ``import frappe`` skip-guards —
the same discipline as ``test_po_approval``.

The cases here are the ones where a mistake would be expensive and silent:

* the token shape guard must accept what the generator actually produces (the
  invite module's ``[A-Za-z0-9]`` guard would reject most base64url tokens,
  intermittently, with no error anyone could debug);
* the plaintext token must never be storable;
* ``sig()`` must degrade to exactly ``blank()`` so every existing contract
  template renders unchanged;
* the signature-block patch must be idempotent and must refuse to guess.

Run: python -m unittest erpnext_enhancements.tests.test_contract_esign
"""

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP_DIR = Path(__file__).resolve().parents[1]

# Mutable state the frappe stub reads at call time.
STATE = {"signature": None, "raises": False}

tokens = None
render = None
patch_mod = None


def _install_frappe_stub():
    frappe = types.ModuleType("frappe")

    def get_value(doctype, filters=None, fieldname=None, **kwargs):
        if STATE["raises"]:
            raise RuntimeError("db exploded")
        return STATE["signature"]

    frappe.db = types.SimpleNamespace(get_value=get_value, set_value=lambda *a, **k: None)
    frappe._ = lambda s: s
    frappe.throw = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("throw"))
    frappe.log_error = lambda *a, **k: None
    frappe.get_traceback = lambda: ""

    # ``erpnext_enhancements.project_enhancements.__init__`` decorates at import
    # time, so importing anything beneath it needs these present.
    def whitelist(*dargs, **dkwargs):
        def wrap(fn):
            return fn

        return wrap(dargs[0]) if dargs and callable(dargs[0]) else wrap

    frappe.whitelist = whitelist
    frappe.get_all = lambda *a, **k: []
    frappe.get_doc = lambda *a, **k: None
    frappe.session = types.SimpleNamespace(user="Administrator")

    utils = types.ModuleType("frappe.utils")
    utils.escape_html = lambda s: (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    frappe.utils = utils

    sys.modules["frappe"] = frappe
    sys.modules["frappe.utils"] = utils


def setUpModule():
    global tokens, render, patch_mod
    _install_frappe_stub()
    for mod in list(sys.modules):
        if mod.startswith("erpnext_enhancements.project_enhancements.esign") or mod.endswith(
            "patches.add_esign_signature_block"
        ):
            sys.modules.pop(mod, None)
    from erpnext_enhancements.patches import add_esign_signature_block as _patch
    from erpnext_enhancements.project_enhancements.esign import render as _render
    from erpnext_enhancements.project_enhancements.esign import tokens as _tokens

    tokens, render, patch_mod = _tokens, _render, _patch


def tearDownModule():
    sys.modules.pop("frappe", None)
    sys.modules.pop("frappe.utils", None)


def _blank(width=30):
    """The real ``_blank`` from project_contract, copied to avoid importing the
    controller (which pulls in far more of frappe than the stub provides)."""
    return f'<span class="ct-blank">{"&nbsp;" * width}</span>'


class TestToken(unittest.TestCase):
    def test_format_guard_accepts_what_the_generator_produces(self):
        """The bug this exists for: base64url contains ``-`` and ``_``, so the
        invite module's ``^[A-Za-z0-9]{8,64}$`` guard would reject most real
        tokens — and only sometimes, which is the worst kind of bug."""
        for _ in range(1000):
            token, digest = tokens.mint_token()
            self.assertIsNotNone(
                tokens.hash_token(token), f"generator produced a token the guard rejects: {token!r}"
            )
            self.assertEqual(digest, tokens.hash_token(token))

    def test_token_has_meaningful_entropy(self):
        token, _ = tokens.mint_token()
        self.assertGreaterEqual(len(token), 40)
        self.assertGreaterEqual(tokens.TOKEN_BYTES * 8, 128)

    def test_tokens_are_unique(self):
        self.assertEqual(len({tokens.mint_token()[0] for _ in range(500)}), 500)

    def test_malformed_tokens_are_rejected_before_any_db_hit(self):
        for bad in (None, "", "short", "x" * 200, "has space", "semi;colon", "../../etc/passwd", "%2e%2e"):
            self.assertIsNone(tokens.hash_token(bad), f"accepted {bad!r}")

    def test_hash_is_stable_and_full_length(self):
        token, digest = tokens.mint_token()
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, tokens.hash_token(token))
        self.assertNotIn(token, digest)

    def test_text_fingerprint_is_full_length(self):
        """Not truncated to 12 like ``consent_version``: this one is evidence for
        a contract body, where 48 bits would be forgeable."""
        self.assertEqual(len(tokens.text_fingerprint("hello")), 64)
        self.assertNotEqual(tokens.text_fingerprint("a"), tokens.text_fingerprint("b"))

    def test_stored_doctype_has_no_plaintext_token_field(self):
        import json

        path = (
            APP_DIR
            / "project_enhancements"
            / "doctype"
            / "contract_signature_request"
            / "contract_signature_request.json"
        )
        fields = {f["fieldname"] for f in json.loads(path.read_text(encoding="utf-8"))["fields"] if "fieldname" in f}
        self.assertIn("token_hash", fields)
        self.assertNotIn("token", fields)


class TestSignatureMarkup(unittest.TestCase):
    """``sig()`` must be invisible until a contract is actually signed."""

    def test_degrades_to_exactly_blank_when_unsigned(self):
        for party in ("client", "provider"):
            self.assertEqual(
                render.signature_markup(None, party=party, width=30, blank=_blank), _blank(30)
            )

    def test_typed_signature_renders_the_adopted_name(self):
        sig = {"signature_mode": "Typed", "signed_name": "Jane Q. Customer"}
        out = render.signature_markup(sig, party="client", blank=_blank)
        self.assertIn("Jane Q. Customer", out)
        self.assertIn("ct-sig-typed", out)
        self.assertNotIn("ct-blank", out)

    def test_drawn_signature_renders_the_image(self):
        sig = {"signature_mode": "Drawn", "signature_image": "data:image/png;base64,AAA"}
        out = render.signature_markup(sig, party="client", blank=_blank)
        self.assertIn("<img", out)
        self.assertIn("ct-sig", out)

    def test_drawn_mode_without_an_image_falls_back_to_the_name(self):
        sig = {"signature_mode": "Drawn", "signature_image": None, "signed_name": "Jane"}
        out = render.signature_markup(sig, party="client", blank=_blank)
        self.assertIn("Jane", out)

    def test_typed_name_is_escaped(self):
        """The name arrives from an anonymous request and is rendered into a
        document staff read in the desk and wkhtmltopdf turns into a PDF."""
        sig = {"signature_mode": "Typed", "signed_name": '<img src=x onerror=alert(1)>'}
        out = render.signature_markup(sig, party="client", blank=_blank)
        self.assertNotIn("<img src=x", out)
        self.assertIn("&lt;img", out)

    def test_provider_party_uses_the_countersigner_and_blanks_without_one(self):
        signed_no_counter = {"signature_mode": "Typed", "signed_name": "Jane"}
        self.assertEqual(
            render.signature_markup(signed_no_counter, party="provider", width=30, blank=_blank),
            _blank(30),
        )
        with_counter = dict(signed_no_counter, countersigned_by="Sapphire Fountains")
        out = render.signature_markup(with_counter, party="provider", blank=_blank)
        self.assertIn("Sapphire Fountains", out)
        self.assertNotIn("Jane", out)

    def test_lookup_never_raises_on_the_print_path(self):
        STATE["raises"] = True
        try:
            doc = types.SimpleNamespace(get=lambda k: "SF-MAINT-0001")
            self.assertIsNone(render.signed_signature_for(doc))
        finally:
            STATE["raises"] = False

    def test_lookup_skips_unsaved_documents(self):
        doc = types.SimpleNamespace(get=lambda k: None)
        self.assertIsNone(render.signed_signature_for(doc))


class TestSignatureBlockPatch(unittest.TestCase):
    """The patch must apply cleanly, be idempotent, and never guess."""

    def _template(self):
        return (APP_DIR / "templates" / "contracts" / "maintenance_services_agreement.html").read_text(
            encoding="utf-8"
        )

    def test_shipped_template_is_already_signature_aware(self):
        self.assertIn("sig('client')", self._template())
        self.assertIn("sig('provider')", self._template())

    def test_patch_is_idempotent_on_an_already_patched_body(self):
        _, changed = patch_mod.rewrite_signature_block(self._template())
        self.assertFalse(changed)

    def test_patch_rewrites_the_unpatched_block(self):
        original = self._template().replace(patch_mod.NEW_BLOCK, patch_mod.OLD_BLOCK)
        self.assertNotIn("sig(", original)
        new_body, changed = patch_mod.rewrite_signature_block(original)
        self.assertTrue(changed)
        self.assertEqual(new_body, self._template())

    def test_patch_refuses_a_diverged_body(self):
        """A site whose legal team rewrote the signature block must be left alone
        rather than silently overwritten."""
        original = self._template().replace(patch_mod.NEW_BLOCK, patch_mod.OLD_BLOCK)
        diverged = original.replace("Print Name: {{ blank(30) }}", "Printed Name: {{ blank(30) }}", 1)
        _, changed = patch_mod.rewrite_signature_block(diverged)
        self.assertFalse(changed)

    def test_patch_handles_an_empty_body(self):
        self.assertEqual(patch_mod.rewrite_signature_block(""), ("", False))
        self.assertEqual(patch_mod.rewrite_signature_block(None), (None, False))


class TestDoctypeContract(unittest.TestCase):
    """Shape invariants on the evidence doctype, asserted from the JSON."""

    def setUp(self):
        import json

        path = (
            APP_DIR
            / "project_enhancements"
            / "doctype"
            / "contract_signature_request"
            / "contract_signature_request.json"
        )
        self.doc = json.loads(path.read_text(encoding="utf-8"))

    def test_no_guest_or_all_permission(self):
        """A Guest DocPerm would expose every signature through /api/resource."""
        roles = {p.get("role") for p in self.doc["permissions"]}
        self.assertNotIn("Guest", roles)
        self.assertNotIn("All", roles)

    def test_nobody_can_delete_evidence(self):
        for perm in self.doc["permissions"]:
            self.assertFalse(perm.get("delete"), f"{perm.get('role')} can delete signature evidence")

    def test_evidence_fields_are_read_only(self):
        skip = {"Section Break", "Column Break"}
        for field in self.doc["fields"]:
            if field.get("fieldtype") in skip:
                continue
            self.assertTrue(
                field.get("read_only"), f"{field.get('fieldname')} is writable on an append-only record"
            )

    def test_both_ip_addresses_are_recorded_and_the_claimed_one_is_labelled_untrusted(self):
        by_name = {f.get("fieldname"): f for f in self.doc["fields"]}
        self.assertIn("signer_ip_peer", by_name)
        self.assertIn("UNTRUSTED", by_name["signer_ip_claimed"].get("description", ""))

    def test_esign_disclosure_is_captured_separately_from_consent(self):
        """§16.6 of the agreement is the contract asserting its own
        enforceability — not the consumer disclosure E-SIGN requires."""
        names = {f.get("fieldname") for f in self.doc["fields"]}
        for required in (
            "consent_text",
            "consent_version",
            "esign_disclosure_text",
            "esign_disclosure_version",
        ):
            self.assertIn(required, names)


class TestSettingsFlags(unittest.TestCase):
    def setUp(self):
        import json

        path = (
            APP_DIR
            / "enhancements_core"
            / "doctype"
            / "erpnext_enhancements_settings"
            / "erpnext_enhancements_settings.json"
        )
        self.doc = json.loads(path.read_text(encoding="utf-8"))
        self.by_name = {f.get("fieldname"): f for f in self.doc["fields"]}

    def test_both_switches_exist_and_default_off(self):
        for name in ("contract_esign_enabled", "contract_esign_public_page_enabled"):
            self.assertIn(name, self.by_name)
            self.assertEqual(self.by_name[name].get("default"), "0", f"{name} must ship dormant")

    def test_public_page_switch_depends_on_the_master_switch(self):
        self.assertEqual(
            self.by_name["contract_esign_public_page_enabled"].get("depends_on"),
            "contract_esign_enabled",
        )

    def test_secret_key_is_a_password_field(self):
        self.assertEqual(self.by_name["contract_esign_turnstile_secret_key"]["fieldtype"], "Password")

    def test_field_order_and_fields_agree(self):
        order = self.doc["field_order"]
        names = {f["fieldname"] for f in self.doc["fields"] if "fieldname" in f}
        self.assertEqual(len(order), len(set(order)), "duplicate entries in field_order")
        self.assertEqual(set(order), names)


if __name__ == "__main__":
    unittest.main()
