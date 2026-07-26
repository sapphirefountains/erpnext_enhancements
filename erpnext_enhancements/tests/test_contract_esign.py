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


class TestExecutionOrdering(unittest.TestCase):
    """Source-level guards for ordering bugs that are invisible at runtime until
    a customer opens their PDF."""

    def setUp(self):
        self.src = (
            APP_DIR / "project_enhancements" / "esign" / "lifecycle.py"
        ).read_text(encoding="utf-8")

    def test_contract_signature_fields_are_stamped_before_the_executed_render(self):
        """The SIGNATURES block reads fill(doc.signed_by) / dt(doc.signed_on) off
        the contract. Rendering before stamping froze a blank Print Name and Date
        into the document the customer receives — permanently, because everything
        downstream prefers the stored copy."""
        stamp = self.src.index("contract.signed_by = evidence.signed_name")
        render = self.src.index("body = contract.render_body()", stamp - 4000)
        self.assertLess(stamp, render, "contract must be stamped before the executed render")

    def test_delivery_is_enqueued_before_the_commit_it_rides_on(self):
        """enqueue_after_commit attaches the job to the NEXT commit; committing
        first hands delivery to whatever commit happens to come later."""
        enqueue = self.src.index("enqueue_after_commit=True")
        commit = self.src.index("frappe.db.commit()", enqueue)
        self.assertLess(enqueue, commit)

    def test_customer_email_is_not_gated_on_the_pdf(self):
        """A wkhtmltopdf failure must not mean the customer gets nothing — the
        page has already told them a copy is on its way."""
        self.assertNotIn("if pdf_url:\n\t\t_email_signed_copy", self.src)
        self.assertIn("_email_signed_copy(request, pdf_url)", self.src)

    def test_delivery_is_idempotent_on_delivered_on(self):
        self.assertIn('request.get("delivered_on")', self.src)

    def test_staff_alert_and_timeline_note_have_their_own_one_shot_marker(self):
        """The daily retry sweep only exits when the customer email succeeds, so
        an unguarded staff alert would re-fire — and append another
        'Signed electronically by...' comment to a legal document — every day."""
        self.assertIn('request.get("staff_notified_on")', self.src)
        block = self.src[self.src.index('if not request.get("staff_notified_on")') :]
        stamp = block.index('db_set("staff_notified_on"')
        for call in ("_alert_staff_signed(request)", "_comment_evidence(request)"):
            self.assertLess(
                block.index(call), stamp, f"{call} is not inside the one-shot guard"
            )
        # And the guard must precede them in the function at all.
        self.assertLess(
            self.src.index('if not request.get("staff_notified_on")'),
            self.src.index("_alert_staff_signed(request)"),
        )


class TestLockoutRecovery(unittest.TestCase):
    """A lockout the customer can never recover from is a lost contract."""

    def setUp(self):
        base = APP_DIR / "project_enhancements" / "esign"
        self.api = (base / "api.py").read_text(encoding="utf-8")
        self.portal = (base / "portal.py").read_text(encoding="utf-8")

    def test_staff_resend_clears_the_persisted_attempt_counter(self):
        """Otherwise the lockout notice's own instruction — 're-send it if the
        customer needs another try' — is false, and the field is read_only so
        nobody could clear it from the desk either."""
        self.assertIn("reset_attempts", self.api)
        self.assertIn("request.email_attempts = 0", self.api)

    def test_self_service_resend_does_not_clear_the_counter(self):
        """Otherwise someone guessing at the address could refresh their own
        budget by asking for a new link."""
        block = self.portal[self.portal.index("def send_fresh_link") :]
        self.assertIn("reset_attempts=False", block)

    def test_self_service_resend_refuses_a_locked_request(self):
        block = self.portal[self.portal.index("def request_fresh_link") :]
        self.assertIn('"Locked"', block)


class TestPortalGuards(unittest.TestCase):
    def setUp(self):
        self.src = (APP_DIR / "project_enhancements" / "esign" / "portal.py").read_text(
            encoding="utf-8"
        )

    def _whitelisted(self):
        """Every @frappe.whitelist function in the module, as AST nodes."""
        import ast

        tree = ast.parse(self.src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for deco in node.decorator_list:
                target = deco.func if isinstance(deco, ast.Call) else deco
                if isinstance(target, ast.Attribute) and target.attr == "whitelist":
                    yield node
                    break

    def test_no_endpoint_parameter_is_named_sid(self):
        """frappe pops `sid` during auth — from the query string, the form body
        and JSON alike. That cost a full outage on the intake form."""
        for node in self._whitelisted():
            names = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
            self.assertNotIn("sid", names, f"{node.name} takes a parameter named sid")

    def test_never_splats_a_payload_onto_a_document(self):
        """read_only is a UI hint; under ignore_permissions a splatted payload
        could set status directly. Checked over the AST so the module docstring's
        own description of the rule doesn't trip it."""
        import ast

        for node in ast.walk(ast.parse(self.src)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "update"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in ("doc", "request", "contract")
            ):
                self.fail(f"mass assignment via {node.func.value.id}.update() at line {node.lineno}")

    def test_email_comparison_is_constant_time_over_bytes(self):
        """compare_digest only accepts str operands that are pure ASCII, so a
        pasted address with an accent or a curly quote would raise instead of
        returning the uniform failure."""
        self.assertIn("hmac.compare_digest", self.src)
        self.assertIn('.encode("utf-8")', self.src)

    def test_blank_email_never_costs_an_attempt(self):
        """The decline dialog never asks for the address; charging for a blank
        let it silently spend the signer's whole hourly budget."""
        body = self.src[self.src.index("def _email_matches") :]
        guard = body.index('if not str(typed or "").strip():')
        counter = body.index("_bump_counter")
        self.assertLess(guard, counter)

    def test_lifetime_attempt_counter_is_persisted_not_only_cached(self):
        """The deploy pipeline flushes Redis on every release; a cache-only
        lifetime limit would reset and the lockout would never fire."""
        self.assertIn('"email_attempts"', self.src)
        self.assertIn("frappe.db.set_value(", self.src)

    def test_session_pins_the_token_so_a_resend_invalidates_an_open_tab(self):
        self.assertIn('"token_hash": request.token_hash', self.src)
        self.assertIn("_session_token_current", self.src)

    def test_every_guest_endpoint_is_post_only_and_rate_limited(self):
        import re

        endpoints = re.findall(
            r"@frappe\.whitelist\(allow_guest=True[^)]*\)\s*\n@rate_limit\([^)]*\)\s*\ndef (\w+)", self.src
        )
        declared = re.findall(r"@frappe\.whitelist\(allow_guest=True", self.src)
        self.assertEqual(
            len(endpoints), len(declared), "a guest endpoint is missing its rate limiter"
        )
        for name in endpoints:
            self.assertIn(f"def {name}", self.src)

    def test_guest_endpoints_all_gate_on_the_public_flag(self):
        import re

        for match in re.finditer(r"@frappe\.whitelist\(allow_guest=True.*?\ndef (\w+)\(.*?\n(.*?)(?=\n@|\Z)", self.src, re.S):
            name, body = match.group(1), match.group(2)
            self.assertIn("_require_public_page()", body, f"{name} does not 404 when switched off")


class TestAutopayOffer(unittest.TestCase):
    """The offer must be structurally incapable of affecting the signature."""

    def setUp(self):
        self.src = (APP_DIR / "project_enhancements" / "esign" / "portal.py").read_text(
            encoding="utf-8"
        )

    def test_autopay_requires_an_already_signed_session(self):
        """It is only reachable once the contract is executed and committed, so
        declining it cannot influence the signature."""
        for fn in ("start_autopay", "decline_autopay"):
            body = self.src[self.src.index(f"def {fn}") :]
            body = body[: body.index("\n@") if "\n@" in body else len(body)]
            self.assertIn("_signed_session(", body, f"{fn} does not require a signed session")

    def test_signed_marker_is_bound_to_the_request_it_signed(self):
        """The session id is caller-supplied and begin_signing will repoint an
        existing session at any resolvable request. A bare "signed" marker would
        therefore let someone who signed their own agreement open a card-enrolment
        session against ANOTHER customer's already-signed contract."""
        gate = self.src[self.src.index("def _signed_session") :]
        gate = gate[: gate.index("def _session_token_current")]
        self.assertIn('session.get("signed_request")', gate)
        self.assertIn('session.get("request")', gate)
        # And the marker must actually be written with that binding.
        self.assertIn('"signed_request": request.name', self.src)

    def test_repointing_a_session_drops_stale_signature_markers(self):
        begin = self.src[self.src.index("def begin_signing") :]
        begin = begin[: begin.index("def sign_contract")]
        self.assertIn('session.get("request") != request.name', begin)
        for marker in ("signed", "signed_request", "contract"):
            self.assertIn(f'"{marker}"', begin, f"{marker} is not cleared when repointing")

    def test_start_autopay_rederives_the_offer_preconditions(self):
        """The endpoint is reachable by direct POST, so it must not trust that the
        client only shows the button when the offer said yes."""
        body = self.src[self.src.index("def start_autopay") :]
        body = body[: body.index("def decline_autopay")]
        self.assertIn("request.autopay_offered", body)
        self.assertIn("custom_stripe_default_payment_method", body)

    def test_decline_does_not_overwrite_a_terminal_outcome(self):
        body = self.src[self.src.index("def decline_autopay") :]
        self.assertIn('("Started", "Enrolled")', body)

    def test_autopay_target_is_resolved_server_side(self):
        """Nothing about which customer gets a card on file comes from the caller."""
        body = self.src[self.src.index("def start_autopay") :]
        self.assertIn('"Project Contract", request.project_contract', body)
        self.assertIn("customer=party", body)

    def test_start_autopay_is_idempotent(self):
        body = self.src[self.src.index("def start_autopay") :]
        self.assertIn('("Started", "Enrolled")', body)

    def test_autopay_failure_is_soft(self):
        """A payment convenience must never surface as an error on a page that
        has just told someone their contract is executed."""
        body = self.src[self.src.index("def start_autopay") :]
        body = body[: body.index("def decline_autopay")]
        self.assertIn("except Exception:", body)
        self.assertIn('return {"ok": False}', body)
        self.assertNotIn("frappe.throw", body)

    def test_offer_check_never_raises(self):
        body = self.src[self.src.index("def _autopay_offer") :]
        body = body[: body.index("@frappe.whitelist")]
        self.assertIn("except Exception:", body)
        self.assertIn('return {"offer": False}', body)

    def test_consent_records_a_distinct_signing_channel(self):
        """So the authorization record says where it came from, rather than being
        indistinguishable from a logged-in portal enrolment."""
        import json

        path = (
            APP_DIR
            / "stripe_payments"
            / "doctype"
            / "stripe_autopay_consent"
            / "stripe_autopay_consent.json"
        )
        doc = json.loads(path.read_text(encoding="utf-8"))
        channel = next(f for f in doc["fields"] if f.get("fieldname") == "channel")
        self.assertIn("Contract Signing", channel["options"])
        self.assertIn('channel="Contract Signing"', self.src)


class TestReminders(unittest.TestCase):
    """Chasing a customer costs goodwill if it misfires — these pin the
    at-most-once discipline and the stop condition."""

    def setUp(self):
        self.src = (APP_DIR / "project_enhancements" / "esign" / "tasks.py").read_text(
            encoding="utf-8"
        )
        self.api = (APP_DIR / "project_enhancements" / "esign" / "api.py").read_text(
            encoding="utf-8"
        )

    def test_counter_is_stamped_before_the_nudge_is_sent(self):
        """A scheduler window evaluated twice must not double-chase a customer —
        the same discipline as maintenance_nudge_sent."""
        body = self.src[self.src.index("def send_signature_reminders") :]
        body = body[: body.index("def _send_reminder")]
        stamp = body.index('"reminders_sent": sent + 1')
        send = body.index("_send_reminder(")
        self.assertLess(stamp, send, "the nudge is sent before its counter is stamped")

    def test_reminders_stop_after_the_last_offset(self):
        body = self.src[self.src.index("def send_signature_reminders") :]
        self.assertIn("if sent >= len(offsets):", body)

    def test_stale_alert_fires_exactly_once(self):
        body = self.src[self.src.index("def _alert_unanswered") :]
        body = body[: body.index("def digest_awaiting_signature")]
        self.assertIn('"stale_alerted"', body)

    def test_blank_cadence_means_never_chase(self):
        """An operator who empties the field has made a choice; it must not fall
        back to the default."""
        body = self.src[self.src.index("def reminder_offsets") :]
        body = body[: body.index("def send_signature_reminders")]
        self.assertIn("if raw is None:", body)
        self.assertNotIn('or DEFAULT_REMINDER_DAYS', body.split("if raw is None:")[0])

    def test_offsets_tolerate_garbage(self):
        import re

        # Mirror the parse so a change to it fails here rather than in production.
        parse = lambda raw: sorted(  # noqa: E731
            {int(t) for t in str(raw).split(",") if t.strip().isdigit() and int(t) > 0}
        )
        self.assertEqual(parse("3,7"), [3, 7])
        self.assertEqual(parse("7, 3, 3"), [3, 7])
        self.assertEqual(parse(""), [])
        self.assertEqual(parse("nonsense; drop table"), [])
        self.assertEqual(parse("0,-1,2"), [2])
        self.assertTrue(re.search(r"isdigit\(\)", self.src))

    def test_reminders_do_not_unlock_a_locked_signer(self):
        """An automated chase is not a staff decision to clear the lockout."""
        body = self.src[self.src.index("def _send_reminder") :]
        body = body[: body.index("def _alert_unanswered")]
        self.assertIn("reset_attempts=False", body)

    def test_staff_resend_restarts_the_chase(self):
        block = self.api[self.api.index("if frappe.utils.cint(reset_attempts):") :]
        block = block[: block.index("request.save(")]
        for field in ("email_attempts", "reminders_sent", "stale_alerted"):
            self.assertIn(field, block, f"a staff resend does not reset {field}")

    def test_digest_is_silent_when_nothing_is_outstanding(self):
        body = self.src[self.src.index("def digest_awaiting_signature") :]
        self.assertIn("if not rows:", body)
        self.assertIn("if not recipients:", body)

    def test_digest_escapes_customer_supplied_values(self):
        """frappe's Jinja has no autoescape and this is built by string join."""
        body = self.src[self.src.index("def digest_awaiting_signature") :]
        self.assertIn("escape_html(r.project_contract", body)
        self.assertIn("escape_html(r.signer_email", body)


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
