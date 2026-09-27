"""Pure-Python (no Frappe site) unit tests for the Stripe Payments integration.

Like ``test_quickbooks_online``, these are plain pytest functions. The Stripe
module must import without a running bench (the ``stripe`` SDK is lazily imported
inside call paths), so :func:`install_frappe_stub` installs a minimal fake
``frappe`` / ``frappe.utils`` into ``sys.modules``. Tests cover the pure logic:
minor-unit conversion, the sandbox key guard, payment-method selection, webhook
object parsing, role gating and the public API surface. The full insert/submit
reconciliation path is exercised at runtime on the dev site (see the module's
verification recipe), not here.
"""

import sys
import types


def _stub_throw(message=None, exc=None, *args, **kwargs):
	"""Stand-in for ``frappe.throw``: raises ``exc`` (frappe's second argument) when one is
	given, else a plain exception, so a test can tell ``PaymentBlocked`` from a decline."""
	cls = exc if isinstance(exc, type) and issubclass(exc, BaseException) else Exception
	raise cls(message if isinstance(message, str) else "frappe.throw")


def install_frappe_stub():
	"""Install a minimal fake ``frappe``/``frappe.utils`` into sys.modules for import."""
	frappe = sys.modules.get("frappe") or types.ModuleType("frappe")
	frappe_utils = sys.modules.get("frappe.utils") or types.ModuleType("frappe.utils")

	def _flt(value=0, precision=None):
		try:
			number = float(value or 0)
		except (TypeError, ValueError):
			return 0.0
		return round(number, precision) if precision is not None else number

	frappe_utils.flt = _flt
	frappe_utils.cint = lambda value=0, *args, **kwargs: int(_flt(value))
	frappe_utils.today = lambda: "2026-06-18"
	frappe_utils.nowdate = lambda: "2026-06-18"
	frappe_utils.now_datetime = lambda: None
	frappe_utils.get_datetime = lambda value=None, *args, **kwargs: value
	frappe_utils.add_to_date = lambda value=None, **kwargs: value
	frappe_utils.get_url = lambda path=None, *args, **kwargs: f"https://erp.example.com{path or ''}"
	frappe_utils.fmt_money = lambda value=0, currency=None, *a, **k: f"{currency or 'USD'} {_flt(value):,.2f}"

	import datetime as _dt

	def _getdate(value=None):
		if value is None:
			value = frappe_utils.today()
		if isinstance(value, _dt.datetime):
			return value.date()
		if isinstance(value, _dt.date):
			return value
		return _dt.date.fromisoformat(str(value)[:10])

	frappe_utils.getdate = _getdate
	frappe_utils.add_days = lambda value, days: _getdate(value) + _dt.timedelta(days=int(days))
	frappe.utils = frappe_utils

	frappe.throw = _stub_throw
	frappe._ = lambda message=None, *args, **kwargs: message
	frappe.whitelist = lambda *args, **kwargs: (lambda fn: fn)
	frappe.only_for = lambda roles, *args, **kwargs: None
	frappe.db = types.SimpleNamespace(get_value=lambda *a, **k: None, exists=lambda *a, **k: None)
	frappe.session = types.SimpleNamespace(user="operator@example.com")
	frappe.PermissionError = type("PermissionError", (Exception,), {})
	frappe.ValidationError = type("ValidationError", (Exception,), {})
	frappe.log_error = lambda *args, **kwargs: None
	frappe.get_traceback = lambda *args, **kwargs: ""
	sys.modules.setdefault("frappe", frappe)
	sys.modules.setdefault("frappe.utils", frappe_utils)
	# frappe.utils.synchronization.filelock — payouts.py serializes on the payout
	# id; a no-op context manager is enough for these single-threaded unit tests.
	import contextlib

	sync_mod = sys.modules.get("frappe.utils.synchronization") or types.ModuleType(
		"frappe.utils.synchronization"
	)

	@contextlib.contextmanager
	def _stub_filelock(*args, **kwargs):
		yield

	sync_mod.filelock = _stub_filelock
	# frappe v16's filelock raises this when the lock is held past its timeout.
	if not hasattr(sync_mod, "LockTimeoutError"):
		sync_mod.LockTimeoutError = type("LockTimeoutError", (Exception,), {})
	sys.modules["frappe.utils.synchronization"] = sync_mod
	frappe_utils.synchronization = sync_mod
	# The client imports `requests` at module top (like the QBO client); a stub
	# suffices because these tests never make a real HTTP call.
	sys.modules.setdefault("requests", types.ModuleType("requests"))
	return frappe


# --- minor-unit conversion --------------------------------------------------


def test_minor_unit_roundtrip_and_rounding():
	"""to_minor_units multiplies by 100 + rounds; from_minor_units inverts it."""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core.utils import from_minor_units, to_minor_units

	assert to_minor_units(50) == 5000
	assert to_minor_units(19.99) == 1999
	assert to_minor_units(0.10) == 10  # guards float drift (0.1*100 == 10.000000000000002)
	assert from_minor_units(5000) == 50.0
	assert from_minor_units(1999) == 19.99


# --- sandbox key guard ------------------------------------------------------


def test_get_api_key_refuses_live_key_in_test_environment():
	"""get_api_key rejects an sk_live_ key while Environment is Test (the sandbox guard)."""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import utils

	settings = types.SimpleNamespace(
		environment="Test",
		get_password=lambda fieldname, *a, **k: "sk_live_ABC123" if fieldname == "secret_key" else None,
	)
	try:
		utils.get_api_key(settings)
		raise AssertionError("expected get_api_key to refuse a live key in Test")
	except Exception as exc:
		assert "live" in str(exc).lower()


def test_get_api_key_refuses_test_key_in_live_environment():
	"""get_api_key rejects an sk_test_ key while Environment is Live."""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import utils

	settings = types.SimpleNamespace(
		environment="Live",
		get_password=lambda fieldname, *a, **k: "sk_test_XYZ" if fieldname == "secret_key" else None,
	)
	try:
		utils.get_api_key(settings)
		raise AssertionError("expected get_api_key to refuse a test key in Live")
	except Exception as exc:
		assert "test" in str(exc).lower()


def test_get_api_key_requires_a_key():
	"""get_api_key throws a clear error when no secret key is configured."""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import utils

	settings = types.SimpleNamespace(environment="Test", get_password=lambda *a, **k: None)
	try:
		utils.get_api_key(settings)
		raise AssertionError("expected get_api_key to require a key")
	except Exception as exc:
		assert "secret key" in str(exc).lower()


# --- payment-method selection ----------------------------------------------


def test_payment_method_types_follow_settings_toggles():
	"""Checkout offers card and/or ACH per settings, defaulting to card when both off."""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core.checkout import _payment_method_types

	assert _payment_method_types(types.SimpleNamespace(enable_card=1, enable_ach=0)) == ["card"]
	assert _payment_method_types(types.SimpleNamespace(enable_card=1, enable_ach=1)) == [
		"card",
		"us_bank_account",
	]
	assert _payment_method_types(types.SimpleNamespace(enable_card=0, enable_ach=1)) == ["us_bank_account"]
	assert _payment_method_types(types.SimpleNamespace(enable_card=0, enable_ach=0)) == ["card"]


# --- webhook object parsing -------------------------------------------------


def test_extract_payment_intent_handles_session_pi_and_charge():
	"""_extract_payment_intent pulls the PI id from session / PI / charge / expanded objects."""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core.reconcile import _extract_payment_intent

	assert _extract_payment_intent({"object": "checkout.session", "payment_intent": "pi_1"}) == "pi_1"
	assert _extract_payment_intent({"object": "payment_intent", "id": "pi_2"}) == "pi_2"
	assert _extract_payment_intent({"object": "charge", "payment_intent": "pi_3"}) == "pi_3"
	assert _extract_payment_intent({"payment_intent": {"id": "pi_4"}}) == "pi_4"  # expanded
	assert _extract_payment_intent({}) is None


def test_enrich_reads_charge_session_and_card_funding_without_api():
	"""_enrich derives (charge_id, method_type, card_funding) from the event object.

	Every case here is fully satisfied by the event payload, so no API round-trip is
	attempted — which is the point: the funding type rides along on the charge we
	already have.
	"""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core.reconcile import _enrich

	def card_charge(charge_id, funding):
		return {
			"object": "charge",
			"id": charge_id,
			"payment_method_details": {"type": "card", "card": {"funding": funding}},
		}

	assert _enrich(card_charge("ch_1", "credit"), "pi_1") == ("ch_1", "card", "credit")
	# Non-credit funding is carried through verbatim; the reconciler decides on it.
	assert _enrich(card_charge("ch_2", "debit"), "pi_2") == ("ch_2", "card", "debit")
	assert _enrich(card_charge("ch_3", "unknown"), "pi_3") == ("ch_3", "card", "unknown")

	# A bank debit has no funding type to find, so no lookup is triggered for one.
	ach = {"object": "charge", "id": "ch_4", "payment_method_details": {"type": "us_bank_account"}}
	assert _enrich(ach, "pi_4") == ("ch_4", "us_bank_account", None)

	# Session with pi_id=None so the optional API enrichment branch is skipped.
	session = {"object": "checkout.session", "payment_method_types": ["us_bank_account"]}
	assert _enrich(session, None) == (None, "us_bank_account", None)

	# An expanded PaymentIntent yields all three in one hop.
	pi = {
		"object": "payment_intent",
		"id": "pi_5",
		"latest_charge": card_charge("ch_5", "prepaid"),
	}
	assert _enrich(pi, "pi_5") == ("ch_5", "card", "prepaid")


def test_reconcile_elevates_guest_to_administrator_and_restores():
	"""The webhook is allow_guest; reconciliation must run elevated, then restore.

	Guards the fix for the guest-permission bug: without elevation get_payment_entry's
	permission-checked Sales Invoice read raises PermissionError for Guest.
	"""
	frappe = install_frappe_stub()
	frappe.session.user = "Guest"
	seen = []
	frappe.set_user = lambda user: (seen.append(user), setattr(frappe.session, "user", user))
	from erpnext_enhancements.stripe_payments.core.reconcile import _reconcile_as_system_user

	with _reconcile_as_system_user():
		assert frappe.session.user == "Administrator"  # elevated inside the block
	assert frappe.session.user == "Guest"  # caller's user restored
	assert seen == ["Administrator", "Guest"]


def test_reconcile_leaves_a_system_user_untouched():
	"""A non-guest caller (scheduled retry / manual reprocess) is never switched."""
	frappe = install_frappe_stub()
	frappe.session.user = "operator@example.com"
	seen = []
	frappe.set_user = lambda user: seen.append(user)
	from erpnext_enhancements.stripe_payments.core.reconcile import _reconcile_as_system_user

	with _reconcile_as_system_user():
		assert frappe.session.user == "operator@example.com"
	assert seen == []  # no elevation, no restore


# --- access control + API surface ------------------------------------------


def test_require_stripe_operator_enforces_operator_roles():
	"""_require_stripe_operator gates privileged RPCs on the accounting operator roles."""
	frappe = install_frappe_stub()
	captured = {}
	frappe.only_for = lambda roles, *args, **kwargs: captured.update(roles=roles)
	from erpnext_enhancements.stripe_payments.core import api

	api._require_stripe_operator()

	assert "System Manager" in captured["roles"]
	assert "Accounts Manager" in captured["roles"]


def test_api_exposes_expected_endpoints():
	"""The public api module re-exports the whitelisted RPCs, including the guest webhook."""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments import api

	for endpoint in (
		"create_invoice_payment",
		"create_adhoc_payment",
		"send_payment_link",
		"test_connection",
		"get_dashboard_status",
		"portal_create_payment",
		"stripe_webhook",
	):
		assert callable(getattr(api, endpoint))


def test_error_snippet_bounds_bodies():
	"""error_snippet truncates long bodies and tolerates an empty one."""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core.utils import error_snippet

	assert error_snippet("short") == "short"
	assert error_snippet(None) == ""
	long_body = "x" * 700
	snippet = error_snippet(long_body)
	assert snippet.endswith("(truncated)")
	assert len(snippet) < len(long_body)


# --- webhook signature verification (hand-rolled, no SDK) -------------------


def test_verify_and_parse_event_accepts_valid_and_rejects_tampered():
	"""verify_and_parse_event accepts a correct v1 HMAC-SHA256 and rejects a bad one."""
	install_frappe_stub()
	import hashlib
	import hmac
	import time

	from erpnext_enhancements.stripe_payments.core import client

	settings = types.SimpleNamespace(
		get_password=lambda fieldname, *a, **k: "whsec_test"
		if fieldname == "webhook_signing_secret"
		else None
	)
	payload = b'{"id":"evt_1","type":"checkout.session.completed"}'
	ts = str(int(time.time()))
	good = hmac.new(b"whsec_test", ts.encode() + b"." + payload, hashlib.sha256).hexdigest()

	event = client.verify_and_parse_event(payload, f"t={ts},v1={good}", settings)
	assert event["id"] == "evt_1"

	try:
		client.verify_and_parse_event(payload, f"t={ts},v1={'0' * len(good)}", settings)
		raise AssertionError("expected a tampered signature to be rejected")
	except Exception as exc:
		assert "signature" in str(exc).lower()


# --- surcharge / fee computation -------------------------------------------


def test_compute_surcharge_only_ever_charges_credit_cards():
	"""The full gate matrix: funding x payment method x master switch.

	Only a credit card ever produces a fee. Surcharging debit or prepaid is a flat
	card-network violation with no cost-of-acceptance exception; "unknown" means
	Stripe could not classify the card, so it fails toward not charging; and ACH has
	no fee path at all (there is no ACH fee setting left to misconfigure).
	"""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core.checkout import (
		_compute_surcharge,
		_method_hint,
		_methods_for,
	)

	on = types.SimpleNamespace(
		surcharge_enabled=1,
		card_surcharge_percent=2.9,
		card_surcharge_flat=0,
		enable_card=1,
		enable_ach=1,
	)

	# The one and only combination that produces a fee.
	assert _compute_surcharge(on, pm_type="card", funding="credit", base=100) == 2.9

	# Every other funding type on a card: zero.
	for funding in ("debit", "prepaid", "unknown", None):
		assert _compute_surcharge(on, pm_type="card", funding=funding, base=100) == 0.0, funding

	# ACH is zero regardless of what funding value is passed alongside it.
	for funding in ("credit", "debit", "prepaid", "unknown", None):
		assert (
			_compute_surcharge(on, pm_type="us_bank_account", funding=funding, base=100) == 0.0
		), funding

	# An absent/unrecognised method type is not a card, so it is not surchargeable.
	assert _compute_surcharge(on, pm_type=None, funding="credit", base=100) == 0.0
	assert _compute_surcharge(on, pm_type="link", funding="credit", base=100) == 0.0

	# Master switch off: even a credit card pays nothing.
	off = types.SimpleNamespace(surcharge_enabled=0, card_surcharge_percent=2.9, card_surcharge_flat=0)
	for funding in ("credit", "debit", "prepaid", "unknown", None):
		assert _compute_surcharge(off, pm_type="card", funding=funding, base=100) == 0.0, funding

	# Percent and flat compose, rounded to cents.
	both = types.SimpleNamespace(surcharge_enabled=1, card_surcharge_percent=2.9, card_surcharge_flat=0.30)
	assert _compute_surcharge(both, pm_type="card", funding="credit", base=100) == 3.20

	# Choosing a method still locks the Checkout Session to it.
	assert _methods_for(on, "card") == ["card"]
	assert _methods_for(on, "ach") == ["us_bank_account"]
	assert _method_hint("card") == "card"
	assert _method_hint("ach") == "us_bank_account"
	assert _method_hint(None) is None


def test_hosted_checkout_can_never_price_a_surcharge():
	"""Pinning the exact call create_payment makes: funding=None, therefore always 0.

	Hosted Checkout fixes its line items when the Session is created — before the
	payer's card, and so its funding type, exists. A fee here would be a guess, and a
	wrong guess against a debit card is a network violation, so the gate refuses.
	"""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core.checkout import _compute_surcharge, _method_hint

	settings = types.SimpleNamespace(
		surcharge_enabled=1, card_surcharge_percent=3, card_surcharge_flat=1
	)
	for method in ("card", "ach", None):
		assert (
			_compute_surcharge(settings, pm_type=_method_hint(method), funding=None, base=500) == 0.0
		), method


def test_surcharge_cap_is_cost_aware_not_just_three_percent():
	"""surcharge_cap_error enforces cost of acceptance as well as the 3% ceiling.

	A 3% surcharge against a 2.9% + $0.30 cost exceeds cost on any invoice over ~$300,
	so the network cap alone let a non-compliant configuration save.
	"""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core.checkout import surcharge_cap_error

	# Sapphire's real cost of acceptance.
	cost_pct, cost_flat = 2.9, 0.30

	# At or under cost on both components: allowed.
	assert surcharge_cap_error(2.9, 0, cost_pct, cost_flat) is None
	assert surcharge_cap_error(2.9, 0.30, cost_pct, cost_flat) is None
	assert surcharge_cap_error(2.0, 0.10, cost_pct, cost_flat) is None

	# The case the old validator waved through: within 3%, over cost.
	assert "cost of acceptance" in (surcharge_cap_error(3, 0, cost_pct, cost_flat) or "")

	# Over the hard network ceiling, whatever the cost is.
	assert "3%" in (surcharge_cap_error(3.5, 0, 4, 1) or "")

	# Flat component over cost, percent fine.
	assert "flat" in (surcharge_cap_error(2.9, 0.50, cost_pct, cost_flat) or "").lower()

	# Cost not configured at all: refuse rather than assume.
	assert "Cost of Acceptance" in (surcharge_cap_error(1, 0, 0, 0) or "")


def test_surcharge_je_legs_balance_and_route_to_income():
	"""The companion surcharge JE debits deposit / credits income for equal amounts.

	erpnext forbids received > paid on a same-currency Receive PE, so the surcharge is
	booked by this balanced JE instead of a PE deduction.
	"""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core.reconcile import _surcharge_je_legs

	legs = _surcharge_je_legs("Stripe Clearing - SF", "Stripe Surcharge Income - SF", 7.55, "Main - SF")
	assert len(legs) == 2
	deposit, income = legs
	assert deposit["account"] == "Stripe Clearing - SF"
	assert deposit["debit_in_account_currency"] == 7.55 and deposit["credit_in_account_currency"] == 0
	assert income["account"] == "Stripe Surcharge Income - SF"
	assert income["credit_in_account_currency"] == 7.55 and income["debit_in_account_currency"] == 0
	# Balances: total debit == total credit.
	total_debit = sum(leg["debit_in_account_currency"] for leg in legs)
	total_credit = sum(leg["credit_in_account_currency"] for leg in legs)
	assert total_debit == total_credit == 7.55
	assert all(leg["cost_center"] == "Main - SF" for leg in legs)


def test_confirmation_token_yields_the_funding_type_before_any_charge():
	"""_confirmation_token_method reads type + card funding from payment_method_preview.

	This is the whole reason the Payment Element flow exists: the ConfirmationToken is
	readable server-side *before* an amount is committed, so the surcharge can be
	priced from the real card instead of guessed from the chosen method.
	"""
	install_frappe_stub()

	from erpnext_enhancements.stripe_payments.core import card_element

	tokens = {
		"ct_credit": {"payment_method_preview": {"type": "card", "card": {"funding": "credit"}}},
		"ct_debit": {"payment_method_preview": {"type": "card", "card": {"funding": "debit"}}},
		"ct_prepaid": {"payment_method_preview": {"type": "card", "card": {"funding": "prepaid"}}},
		"ct_unknown": {"payment_method_preview": {"type": "card", "card": {"funding": "unknown"}}},
		"ct_bank": {"payment_method_preview": {"type": "us_bank_account", "us_bank_account": {}}},
	}
	# Patched on card_element, not client: the name is bound at import time there.
	original = card_element.retrieve_confirmation_token
	card_element.retrieve_confirmation_token = lambda token_id: tokens[token_id]
	try:
		assert card_element._confirmation_token_method("ct_credit") == ("card", "credit")
		assert card_element._confirmation_token_method("ct_debit") == ("card", "debit")
		assert card_element._confirmation_token_method("ct_prepaid") == ("card", "prepaid")
		assert card_element._confirmation_token_method("ct_unknown") == ("card", "unknown")
		assert card_element._confirmation_token_method("ct_bank") == ("us_bank_account", None)

		# No token at all must fail loudly rather than price as "no card, no fee".
		try:
			card_element._confirmation_token_method(None)
			raise AssertionError("expected a missing confirmation token to be rejected")
		except Exception as exc:
			assert "payment details" in str(exc).lower()
	finally:
		card_element.retrieve_confirmation_token = original


def test_card_element_prices_each_funding_type_correctly():
	"""End-to-end pricing: the funding type off the token drives the fee, credit only."""
	install_frappe_stub()

	from erpnext_enhancements.stripe_payments.core.checkout import _compute_surcharge

	settings = types.SimpleNamespace(
		surcharge_enabled=1, card_surcharge_percent=2.9, card_surcharge_flat=0
	)
	# What price_card_payment computes for a $200 invoice, per funding type.
	expected = {"credit": 5.8, "debit": 0.0, "prepaid": 0.0, "unknown": 0.0}
	for funding, fee in expected.items():
		assert _compute_surcharge(settings, pm_type="card", funding=funding, base=200) == fee, funding


def _fake_sp(**fields):
	"""A Stripe Payment stand-in whose ``db_set`` writes back onto itself, like the real doc."""
	sp = types.SimpleNamespace(name="STR-PAY-0001", currency="USD", customer="CUST-1", **fields)

	def db_set(field, value=None, **kwargs):
		for key, val in (field if isinstance(field, dict) else {field: value}).items():
			setattr(sp, key, val)

	sp.db_set = db_set
	sp.get = lambda key, default=None: getattr(sp, key, default)
	return sp


def test_confirm_card_payment_guards_against_a_swapped_token_or_replay():
	"""Confirming accepts only the exact card the quote was priced against.

	Without the binding a client could take a quote on a debit card (fee 0) and then
	pay with a credit card, or — the compliance-relevant direction — take a quote on
	credit and present a debit card to the charge. One token, one price, one charge.
	"""
	install_frappe_stub()
	import frappe as frappe_stub

	from erpnext_enhancements.stripe_payments.core import card_element

	original_get_settings = card_element.get_settings
	original_is_enabled = card_element.is_enabled
	card_element.get_settings = lambda: types.SimpleNamespace(enabled=1, enable_card=1)
	card_element.is_enabled = lambda settings=None: True
	try:

		def confirm(sp, token):
			frappe_stub.get_doc = lambda doctype, name=None: sp
			return card_element.confirm_card_payment(
				stripe_payment=sp.name, confirmation_token=token
			)

		# A different card than the one quoted.
		quoted_on_credit = _fake_sp(
			status="Draft", confirmation_token="ct_credit", amount=100, surcharge_amount=2.9
		)
		try:
			confirm(quoted_on_credit, "ct_debit")
			raise AssertionError("expected a swapped confirmation token to be rejected")
		except Exception as exc:
			assert "different card" in str(exc).lower()

		# A row that carries no quote at all cannot be charged through this path.
		unpriced = _fake_sp(status="Draft", confirmation_token=None, amount=100, surcharge_amount=0)
		try:
			confirm(unpriced, "ct_credit")
			raise AssertionError("expected an unpriced payment to be rejected")
		except Exception as exc:
			assert "different card" in str(exc).lower()

		# Replay of an already-settled payment must not start a second PaymentIntent.
		for status in ("Paid", "Processing"):
			already = _fake_sp(
				status=status, confirmation_token="ct_credit", amount=100, surcharge_amount=2.9
			)
			try:
				confirm(already, "ct_credit")
				raise AssertionError(f"expected a {status} payment to be rejected")
			except Exception as exc:
				assert "already" in str(exc).lower()
	finally:
		card_element.get_settings = original_get_settings
		card_element.is_enabled = original_is_enabled


def test_void_surcharge_fires_only_on_known_non_credit_funding():
	"""The backstop voids a collected fee for debit/prepaid/unknown — and only those.

	``None`` funding means *we* could not determine it (usually a failed API lookup),
	not that the card was non-credit. Refunding on missing data would claw back
	legitimate credit surcharges on every Stripe blip, so that case alerts instead.
	"""
	install_frappe_stub()
	import frappe as frappe_stub

	from erpnext_enhancements.stripe_payments.core import reconcile

	enqueued = []
	frappe_stub.enqueue = lambda method, **kwargs: enqueued.append(kwargs.get("stripe_payment"))

	for funding in ("debit", "prepaid", "unknown"):
		enqueued.clear()
		sp = _fake_sp(surcharge_amount=2.9, surcharge_voided=0)
		reconcile._void_surcharge_if_not_credit(sp, funding)
		assert sp.surcharge_voided == 1, funding
		assert enqueued == ["STR-PAY-0001"], funding

	# Credit: the fee stands and gets booked to income as normal.
	enqueued.clear()
	sp = _fake_sp(surcharge_amount=2.9, surcharge_voided=0)
	reconcile._void_surcharge_if_not_credit(sp, "credit")
	assert sp.surcharge_voided == 0 and enqueued == []

	# Funding we could not determine: alert, don't refund.
	enqueued.clear()
	sp = _fake_sp(surcharge_amount=2.9, surcharge_voided=0)
	reconcile._void_surcharge_if_not_credit(sp, None)
	assert sp.surcharge_voided == 0 and enqueued == []

	# Nothing was surcharged, so there is nothing to void.
	enqueued.clear()
	reconcile._void_surcharge_if_not_credit(_fake_sp(surcharge_amount=0, surcharge_voided=0), "debit")
	assert enqueued == []

	# Redelivered event on an already-voided row: no second refund is enqueued.
	enqueued.clear()
	reconcile._void_surcharge_if_not_credit(_fake_sp(surcharge_amount=2.9, surcharge_voided=1), "debit")
	assert enqueued == []


def test_redelivered_event_never_refunds_the_surcharge_twice():
	"""Webhook redelivery must not issue a second refund.

	Stripe's Idempotency-Key expires after 24 hours, so an event redelivered a day
	later would slip past it — the persisted ``surcharge_refund_id`` is what actually
	holds the line.
	"""
	install_frappe_stub()
	import frappe as frappe_stub

	from erpnext_enhancements.stripe_payments.core import client, reconcile

	frappe_stub.db.commit = lambda: None
	calls = []
	original_create_refund = client.create_refund
	client.create_refund = lambda pi, amount_minor=None, reason=None: (
		calls.append((pi, amount_minor)) or {"id": f"re_{len(calls)}"}
	)
	try:
		sp = _fake_sp(
			surcharge_amount=2.9,
			surcharge_voided=1,
			surcharge_refund_id=None,
			stripe_payment_intent="pi_1",
		)
		frappe_stub.get_doc = lambda doctype, name=None: sp

		reconcile.refund_voided_surcharge(sp.name)
		assert calls == [("pi_1", 290)]  # 2.90 USD in minor units
		assert sp.surcharge_refund_id == "re_1"

		reconcile.refund_voided_surcharge(sp.name)
		assert calls == [("pi_1", 290)], "a redelivered event issued a second refund"

		# A row that was never voided must never be refunded by this job at all.
		never_voided = _fake_sp(
			surcharge_amount=2.9,
			surcharge_voided=0,
			surcharge_refund_id=None,
			stripe_payment_intent="pi_2",
		)
		frappe_stub.get_doc = lambda doctype, name=None: never_voided
		reconcile.refund_voided_surcharge(never_voided.name)
		assert calls == [("pi_1", 290)]
	finally:
		client.create_refund = original_create_refund


# --- payout reconciliation (WI-040) -----------------------------------------


def test_auto_charge_skips_historical_invoices(monkeypatch):
	"""Auto-charge never fires on a back-dated (historical import) Sales Invoice (Final Review B4).

	auto_charge_on_invoice_submit prices from outstanding_amount at charge time, so
	submitting the 2009-2025 QBO backlog with Stripe live would silently charge customers
	for decade-old invoices. The global is_enabled()==0 switch is the belt; the posting-date
	guard is the suspenders. This proves the guard blocks the historical case and -- equally
	important -- leaves current billing untouched.
	"""
	frappe = install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import saved_methods

	# Get past the global switch so the age guard is what we are actually testing.
	monkeypatch.setattr(saved_methods, "is_enabled", lambda *args, **kwargs: True)
	enqueued = []
	monkeypatch.setattr(frappe, "enqueue", lambda *a, **k: enqueued.append((a, k)), raising=False)
	# An enrolled customer, positive balance, no existing charge -- everything a *current*
	# invoice needs, so only the posting_date decides whether it charges.
	monkeypatch.setattr(
		frappe.db,
		"get_value",
		lambda *a, **k: types.SimpleNamespace(
			custom_stripe_autopay_enabled=1, custom_stripe_default_payment_method="pm_1"
		),
		raising=False,
	)
	monkeypatch.setattr(frappe.db, "exists", lambda *a, **k: False, raising=False)

	def _inv(**fields):
		inv = types.SimpleNamespace(**fields)
		inv.get = lambda name: getattr(inv, name, None)
		return inv

	# A decade-old invoice (far past AUTO_CHARGE_MAX_AGE_DAYS; today stub = 2026-06-18) never charges.
	saved_methods.auto_charge_on_invoice_submit(
		_inv(name="SINV-X", customer="CUST-1", outstanding_amount=100.0, posting_date="2015-04-20")
	)
	assert enqueued == []

	# A current invoice still charges -- the guard does not break live billing.
	saved_methods.auto_charge_on_invoice_submit(
		_inv(name="SINV-Y", customer="CUST-1", outstanding_amount=100.0, posting_date="2026-06-18")
	)
	assert len(enqueued) == 1


def _charge_bt(amount_cents, fee_cents, txn="txn"):
	"""A charge balance-transaction in Stripe minor units."""
	return {
		"id": txn,
		"type": "charge",
		"reporting_category": "charge",
		"amount": amount_cents,
		"fee": fee_cents,
		"net": amount_cents - fee_cents,
	}


def test_payout_breakdown_charges_and_fees():
	"""Two $100 card charges: net = gross - fees, and net + fees == gross (no refunds)."""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core.payouts import compute_payout_breakdown

	bts = [_charge_bt(10000, 320, "txn_1"), _charge_bt(10000, 320, "txn_2")]
	payout = {"id": "po_1", "amount": 19360, "currency": "usd", "status": "paid"}
	b = compute_payout_breakdown(payout, bts)
	assert b["gross"] == 200.0
	assert b["fees"] == 6.40
	assert b["net"] == 193.60
	assert b["refunds"] == 0.0
	assert b["charge_count"] == 2
	assert b["other_count"] == 0
	# The clearing-zero identity the Journal Entry relies on.
	assert round(b["net"] + b["fees"], 2) == round(b["gross"] - b["refunds"], 2)


def test_payout_breakdown_with_refund():
	"""A refund in the payout reduces net; net + fees still equals gross - refunds."""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core.payouts import compute_payout_breakdown

	bts = [
		_charge_bt(10000, 320, "txn_1"),
		_charge_bt(10000, 320, "txn_2"),
		{"id": "txn_r", "type": "refund", "reporting_category": "refund", "amount": -3000, "fee": 0, "net": -3000},
	]
	payout = {"id": "po_2", "amount": 16360, "currency": "usd", "status": "paid"}
	b = compute_payout_breakdown(payout, bts)
	assert b["gross"] == 200.0
	assert b["refunds"] == 30.0
	assert b["fees"] == 6.40
	assert b["net"] == 163.60
	assert round(b["net"] + b["fees"], 2) == round(b["gross"] - b["refunds"], 2)  # 170.00


def test_payout_breakdown_excludes_payout_line_and_flags_other():
	"""The 'payout' line is ignored; a dispute is counted as 'other' for review."""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core.payouts import compute_payout_breakdown

	bts = [
		_charge_bt(10000, 320, "txn_1"),
		{"id": "txn_p", "type": "payout", "amount": -9680, "fee": 0, "net": -9680},
		{"id": "txn_d", "type": "dispute", "reporting_category": "dispute", "amount": -1500, "fee": 1500, "net": -3000},
	]
	payout = {"id": "po_3", "amount": 6680, "currency": "usd", "status": "paid"}
	b = compute_payout_breakdown(payout, bts)
	assert b["charge_count"] == 1  # payout line excluded
	assert b["other_count"] == 1  # dispute flagged for review


def test_fee_variance_flags_abnormal_and_passes_nominal():
	"""_fee_variance flags a >20% deviation from card-nominal and passes a normal fee."""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core.payouts import _fee_variance

	# nominal: expected 0.029*200 + 0.30*2 = 6.40; actual 6.40 -> no note
	assert _fee_variance({"gross": 200.0, "charge_count": 2, "fees": 6.40}) is None
	# double the fee -> flagged
	note = _fee_variance({"gross": 200.0, "charge_count": 2, "fees": 12.80})
	assert note and "variance" in note.lower()


def test_posting_date_from_epoch_arrival():
	"""arrival_date is a Unix epoch int; it must convert to the right calendar date."""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core.payouts import posting_date_from_arrival

	# 1721088000 = 2024-07-16 00:00:00 UTC (local tz may shift; assert the year/type).
	d = posting_date_from_arrival(1721088000)
	import datetime as _dt

	assert isinstance(d, _dt.date)
	assert d.year == 2024
	# Missing/garbage falls back to today() (the stub returns the fixed string).
	assert posting_date_from_arrival(None) == "2026-06-18"
	assert posting_date_from_arrival("not-a-number") == "2026-06-18"


def test_signed_legs_balance_and_sign_safety():
	"""Legs sum to zero; a negative (refund-heavy) payout flips bank/clearing sides."""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core.payouts import signed_legs

	# Normal payout: net 163.60, fees 6.40 -> Dr bank, Dr fees, Cr clearing.
	legs = dict((a, amt) for a, amt in signed_legs(163.60, 6.40, "BANK", "FEE", "CLR"))
	assert round(sum(legs.values()), 2) == 0.0
	assert legs["BANK"] > 0 and legs["FEE"] > 0 and legs["CLR"] < 0
	assert round(legs["CLR"], 2) == -170.00

	# Negative payout: net -50, fees 5 -> bank credited, clearing debited.
	neg = dict((a, amt) for a, amt in signed_legs(-50.0, 5.0, "BANK", "FEE", "CLR"))
	assert round(sum(neg.values()), 2) == 0.0
	assert neg["BANK"] < 0  # bank credited (money leaves the bank)
	assert neg["CLR"] > 0  # clearing debited
	assert round(neg["CLR"], 2) == 45.00


def test_posting_date_from_arrival_reads_epoch_as_utc():
	"""arrival_date (a UTC Unix epoch int) -> the correct UTC date, host-tz-independent."""
	install_frappe_stub()
	import datetime as _dt

	from erpnext_enhancements.stripe_payments.core.payouts import posting_date_from_arrival

	# 1721088000 == 2024-07-16 00:00:00 UTC
	assert posting_date_from_arrival(1721088000) == _dt.date(2024, 7, 16)
	assert posting_date_from_arrival("1721088000") == _dt.date(2024, 7, 16)  # tolerate string epoch
	assert posting_date_from_arrival(None) == "2026-06-18"  # stub today()
	assert posting_date_from_arrival("garbage") == "2026-06-18"  # falls back, no raise


# --- one invoice, one payment: the guard, the lock, and outcomes nobody knows ---
#
# The fakes below are stateful: the ledger honours the filters it is queried with, and
# every write lands in it, so a test can run one request after another (two tabs, a
# timeout then a new quote) and see what the first one committed. Every read, write,
# lock, commit and call to Stripe is also recorded in ``writes["events"]``, in order.

import datetime as _dt

NOW = _dt.datetime(2026, 9, 24, 12, 0, 0)
#: Long past the 3-D Secure window.
OLD = NOW - _dt.timedelta(hours=2)
#: A payer who may still be approving in their bank's app.
FRESH = NOW - _dt.timedelta(minutes=5)


def _matches(row, filters):
	for key, want in (filters or {}).items():
		have = row.get(key)
		if isinstance(want, list | tuple):
			op, value = want
			if op == "in":
				ok = have in value
			elif op == "<":
				ok = have is not None and have < value
			elif op in (">", ">="):
				try:
					ok = have is not None and (have > value if op == ">" else have >= value)
				except TypeError:  # a datetime against a date: compare them as ISO text
					ok = have is not None and (str(have) > str(value) if op == ">" else str(have) >= str(value))
			elif op == "is":
				ok = bool(have) == (value == "set")
			else:
				raise AssertionError(f"filter {op!r} is not faked")
			if not ok:
				return False
		elif have != want:
			return False
	return True


def _invoice(**fields):
	values = dict(
		name="SINV-1",
		customer="CUST-1",
		docstatus=1,
		is_return=0,
		outstanding_amount=200.0,
		status="Unpaid",
		currency="USD",
	)
	values.update(fields)
	return types.SimpleNamespace(**values)


def _store_doc(writes, name):
	"""A Stripe Payment doc backed by the fake ledger: ``db_set`` writes through (and
	touches ``modified``, as Frappe does), ``reload`` re-reads."""
	store = writes["store"]
	doc = types.SimpleNamespace()

	def refresh():
		for key, value in store[name].items():
			setattr(doc, key, value)

	def db_set(field, value=None, **kwargs):
		values = dict(field) if isinstance(field, dict) else {field: value}
		store[name].update(values, modified=NOW)
		writes["events"].append(("set", name, values.get("status")))
		refresh()

	doc.db_set = db_set
	doc.reload = refresh
	doc.get = lambda key, default=None: store[name].get(key, default)
	refresh()
	return doc


def _ledger(monkeypatch, rows, pi_status=None, cancel=None, invoice=None, invoices=None):
	"""Point card_element at a fake Stripe Payment ledger and a fake Stripe.

	``rows`` seed the ledger. ``pi_status`` maps a PaymentIntent id to its status at
	Stripe (an id not in it raises, like a failed lookup; a ``StripeError`` value is raised
	as it is — a 404, say); ``cancel`` maps an id to what a cancel returns, and Stripe's
	record follows it (an id not in it raises, as Stripe does for a PaymentIntent that has
	already succeeded). ``invoice`` is what a Sales Invoice read returns, and ``invoices``
	maps names to their own records (an amendment chain). Returns the module and the
	recorded writes; a locking read (``for_update``) is recorded as ``("read", doctype,
	"for update")``.
	"""
	frappe_stub = install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import card_element

	store = {row["name"]: dict(row) for row in rows}
	pi_status = dict(pi_status or {})
	si = invoice or _invoice()
	si_by_name = dict(invoices or {})
	writes = {
		"store": store,
		"pi_status": pi_status,
		"invoice": si,
		"set_value": [],
		"stamps": [],
		"cancels": [],
		"lookups": [],
		"timeouts": [],
		"commits": 0,
		"rollbacks": 0,
		"filters": [],
		"events": [],
		"errors": [],
		"steps": [],
		"accounts_alerts": [],
		"emails": [],
		# (amount quoted, invoice_canceled) for each entry in "emails", in the same order.
		"email_details": [],
	}

	def event(*entry):
		writes["events"].append(entry)

	def found_rows(doctype, filters):
		if doctype == "Stripe Payment":
			return [row for row in store.values() if _matches(row, filters)]
		if doctype == "Sales Invoice":
			return [vars(record) for record in si_by_name.values() if _matches(vars(record), filters)]
		return []

	def get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
		writes["filters"].append((doctype, filters))
		event("read", doctype)
		found = found_rows(doctype, filters)
		if pluck:
			return [row.get(pluck) for row in found]
		# Like the real one: every requested field is present, None where unset.
		return [types.SimpleNamespace(**{field: row.get(field) for field in fields or row}) for row in found]

	def get_values(doctype, filters=None, fieldname="name", *args, for_update=False, **kwargs):
		writes["filters"].append((doctype, filters, "for update") if for_update else (doctype, filters))
		event(*(("read", doctype, "for update") if for_update else ("read", doctype)))
		fields = list(fieldname) if isinstance(fieldname, list | tuple) else [fieldname]
		return [
			types.SimpleNamespace(**{field: row.get(field) for field in fields})
			for row in found_rows(doctype, filters)
		]

	def get_value(doctype, name=None, fieldname=None, *args, **kwargs):
		if doctype == "Sales Invoice":
			event(*(("read", doctype, "for update") if kwargs.get("for_update") else ("read", doctype)))
			record = si_by_name.get(name, si)
			if kwargs.get("as_dict") or isinstance(fieldname, list | tuple):
				return record
			return getattr(record, fieldname, None)
		if doctype == "Stripe Payment" and name in store:
			row = store[name]
			if isinstance(fieldname, list | tuple):
				return types.SimpleNamespace(**{field: row.get(field) for field in fieldname})
			return row.get(fieldname)
		return None

	def set_value(doctype, name, values, value=None, *args, **kwargs):
		values = dict(values) if isinstance(values, dict) else {values: value}
		writes["set_value"].append((doctype, name, values))
		event("set", name, values.get("status"))
		if doctype == "Stripe Payment" and name in store:
			store[name].update(values, modified=NOW)

	def exists(doctype, filters=None, *args, **kwargs):
		if doctype != "Stripe Payment":
			return None
		if isinstance(filters, dict):
			return any(_matches(row, filters) for row in store.values()) or None
		return filters in store or None

	def commit():
		writes["commits"] += 1
		event("commit")

	def rollback():
		writes["rollbacks"] += 1
		event("rollback")

	def retrieve(pi_id, timeout=None):
		writes["lookups"].append(pi_id)
		writes["timeouts"].append(timeout)
		event("lookup", pi_id)
		if pi_id not in pi_status:
			raise Exception("Stripe unreachable")
		if isinstance(pi_status[pi_id], BaseException):
			raise pi_status[pi_id]
		return {"id": pi_id, "status": pi_status[pi_id]}

	def cancel_pi(pi_id):
		writes["cancels"].append(pi_id)
		event("cancel", pi_id)
		if pi_id not in (cancel or {}):
			raise Exception("This PaymentIntent's status is succeeded")
		pi_status[pi_id] = cancel[pi_id]
		return {"id": pi_id, "status": cancel[pi_id]}

	def get_doc(doctype_or_values, name=None, *args, **kwargs):
		if isinstance(doctype_or_values, dict):
			values = dict(doctype_or_values)

			def insert(**kw):
				new = values.get("name") or f"SP-NEW{len(store) + 1}"
				store[new] = dict(values, name=new, modified=NOW, creation=NOW)
				event("insert", new, values.get("status"))
				return _store_doc(writes, new)

			return types.SimpleNamespace(insert=insert)
		return _store_doc(writes, name)

	monkeypatch.setattr(frappe_stub, "get_all", get_all, raising=False)
	monkeypatch.setattr(frappe_stub, "get_doc", get_doc, raising=False)
	monkeypatch.setattr(frappe_stub, "log_error", lambda *a, **k: writes["errors"].append(a), raising=False)
	monkeypatch.setattr(
		frappe_stub,
		"db",
		types.SimpleNamespace(
			get_value=get_value,
			get_values=get_values,
			set_value=set_value,
			exists=exists,
			commit=commit,
			rollback=rollback,
		),
	)
	monkeypatch.setattr(card_element, "retrieve_payment_intent", retrieve)
	monkeypatch.setattr(card_element, "_cancel_payment_intent", cancel_pi)
	monkeypatch.setattr(
		card_element,
		"_stamp_invoice",
		lambda inv, status, *a: (writes["stamps"].append((inv, status)), event("stamp", inv, status)),
	)
	monkeypatch.setattr(card_element, "now_datetime", lambda: NOW)
	writes["alert_bodies"] = []

	def alert(subject, content, doctype=None, docname=None):
		writes["accounts_alerts"].append((subject, docname))
		writes["alert_bodies"].append(content)

	def email(sp, amount, what, invoice_canceled=False):
		writes["emails"].append((sp.name, sp.get("initiated_by"), what))
		writes["email_details"].append((amount, invoice_canceled))

	monkeypatch.setattr(card_element, "_alert_accounts", alert)
	monkeypatch.setattr(card_element, "_email_payer_not_charged", email)
	return card_element, writes


def _card_attempt(name="SP-OLD", pi="pi_old", status="Processing", invoice="SINV-1", modified=OLD):
	"""A Payment Element attempt: it carries its ConfirmationToken and a PaymentIntent."""
	return {
		"name": name,
		"sales_invoice": invoice,
		"status": status,
		"stripe_payment_intent": pi,
		"confirmation_token": "ct_old",
		"stripe_checkout_session": None,
		"stripe_customer_id": "cus_1",
		"customer": "CUST-1",
		"channel": "Portal",
		"amount": 200,
		"currency": "USD",
		"error_message": None,
		"modified": modified,
		"creation": modified,
	}


def _hosted_payment(name="SP-ACH", pi="pi_ach", status="Processing", invoice="SINV-1", modified=OLD):
	"""A hosted Checkout (bank or card) payment: no ConfirmationToken, and its session."""
	return dict(
		_card_attempt(name=name, pi=pi, status=status, invoice=invoice, modified=modified),
		confirmation_token=None,
		stripe_checkout_session="cs_ach",
	)


def _link_sent(name="SP-LINK", session="cs_link", invoice="SINV-1"):
	"""An emailed hosted Checkout link, still open (valid 24 h)."""
	return dict(
		_hosted_payment(name=name, pi=None, status="Link Sent", invoice=invoice, modified=FRESH),
		stripe_checkout_session=session,
	)


def _quote(name="SP-Q", **fields):
	"""A priced, never-sent card quote (what price_card_payment leaves)."""
	values = dict(
		_card_attempt(name=name, pi=None, status="Draft", modified=NOW),
		confirmation_token="ct_new",
		surcharge_amount=0,
		description="Invoice SINV-1",
	)
	values.update(fields)
	return values


def _charge_settings():
	return types.SimpleNamespace(enabled=1, enable_card=1, success_route=None, statement_descriptor=None)


def _payable_sp(**fields):
	values = dict(
		status="Draft",
		confirmation_token="ct_new",
		amount=200,
		surcharge_amount=0,
		sales_invoice="SINV-1",
		channel="Portal",
		description="Invoice SINV-1",
		stripe_customer_id="cus_1",
	)
	values.update(fields)
	sp = _fake_sp(**values)
	sp.reload = lambda: None
	return sp


def _script_charges(monkeypatch, module, writes, *answers):
	"""``module.create_payment_intent`` answers from a script: each item is returned, or
	raised when it is an exception; past the script, a 3-D Secure challenge. Every call is
	recorded with its params and idempotency key."""
	writes["charges"] = []
	queue = list(answers)

	def create(params, idempotency_key=None):
		writes["charges"].append((params, idempotency_key))
		writes["events"].append(("charge", idempotency_key))
		answer = (
			queue.pop(0) if queue else {"id": "pi_new", "status": "requires_action", "client_secret": "s"}
		)
		if isinstance(answer, BaseException):
			raise answer
		return dict(answer)

	monkeypatch.setattr(module, "create_payment_intent", create)


def _card_page(monkeypatch, rows, *answers, invoice=None, pi_status=None, cancel=None):
	"""The card page's charge, against the fake ledger and a scripted Stripe."""
	card_element, writes = _ledger(monkeypatch, rows, pi_status=pi_status, cancel=cancel, invoice=invoice)
	monkeypatch.setattr(card_element, "get_settings", _charge_settings)
	monkeypatch.setattr(card_element, "is_enabled", lambda s=None: True)
	_script_charges(monkeypatch, card_element, writes, *answers)
	return card_element, writes


def _record_locks(monkeypatch, writes, busy=()):
	"""Replace frappe's filelock with one that records when each lock is held — and, for a
	name in ``busy``, times out the way frappe v16's does."""
	import contextlib

	sync = sys.modules["frappe.utils.synchronization"]
	writes["locks"] = []

	@contextlib.contextmanager
	def filelock(name, timeout=30, is_global=False):
		if any(held in name for held in busy):
			raise sync.LockTimeoutError(f"Failed to aquire lock: {name}")
		writes["locks"].append((name, timeout))
		writes["events"].append(("lock", name))
		try:
			yield
		finally:
			writes["events"].append(("unlock", name))

	monkeypatch.setattr(sync, "filelock", filelock)
	return sync


def _checkout_sessions(monkeypatch, card_element, writes, sessions, expire=None):
	"""Fake Stripe's Checkout Sessions. ``sessions`` maps an id to its status (an id not in
	it cannot be read); ``expire`` maps an id to what an expire answers (an id not in it is
	refused, as Stripe refuses a completed session)."""
	writes["expires"] = []

	def expire_session(session_id, timeout=None):
		writes["expires"].append((session_id, timeout))
		writes["events"].append(("expire", session_id))
		if session_id not in (expire or {}):
			raise Exception("This Checkout Session is not open")
		sessions[session_id] = expire[session_id]
		return {"id": session_id, "status": expire[session_id]}

	def retrieve_session(session_id, expand=None, timeout=None):
		if session_id not in sessions:
			raise Exception("Stripe unreachable")
		return {"id": session_id, "status": sessions[session_id]}

	monkeypatch.setattr(card_element, "expire_checkout_session", expire_session)
	monkeypatch.setattr(card_element, "retrieve_checkout_session", retrieve_session)


def _raised(call, **kwargs):
	"""The exception ``call(**kwargs)`` raises; fails the test if it goes through."""
	try:
		call(**kwargs)
	except Exception as exc:
		return exc
	raise AssertionError(f"expected {getattr(call, '__name__', call)}({kwargs}) to be refused")


def _refusal(call, **kwargs):
	"""The message ``call(**kwargs)`` is refused with; fails the test if it goes through."""
	return str(_raised(call, **kwargs))


def _stripe_error(status_code=None, message="Stripe request failed: Read timed out.", stripe_message=None):
	from erpnext_enhancements.stripe_payments.core.client import StripeError

	return StripeError(message, status_code=status_code, stripe_message=stripe_message)


def _decline():
	return _stripe_error(
		402,
		'Stripe API error (402): {"error": {"code": "card_declined", "message": "Your card was declined."}}',
		"Your card was declined.",
	)


def _cancel_refused(monkeypatch, card_element, writes, now=None):
	"""Stripe refuses (or times out on) the cancel, and the PaymentIntent then reads back
	``now`` — or, with ``None``, cannot be read back either (Stripe unreachable)."""

	def refuse(pi_id):
		writes["cancels"].append(pi_id)
		writes["events"].append(("cancel", pi_id))
		if now is None:
			writes["pi_status"].pop(pi_id, None)
		else:
			writes["pi_status"][pi_id] = now
		raise Exception("Stripe request failed: Read timed out.")

	monkeypatch.setattr(card_element, "_cancel_payment_intent", refuse)


def _index(events, entry, start=0):
	"""Where ``entry`` (a tuple, or a kind like "charge") first appears at or after ``start``."""
	for i in range(start, len(events)):
		if events[i] == entry or (isinstance(entry, str) and events[i][0] == entry):
			return i
	raise AssertionError(f"{entry!r} never happened: {events}")


# --- the guard ------------------------------------------------------------------


def test_invoice_payment_block_reads_the_ledger_like_dunning(monkeypatch):
	"""Paid or Processing blocks, as in dunning / saved_methods; nothing else does."""
	card_element, writes = _ledger(monkeypatch, [])
	assert card_element.invoice_payment_block("SINV-1") is None
	assert writes["filters"] == [
		("Stripe Payment", {"sales_invoice": "SINV-1", "status": ["in", ["Processing", "Paid"]]})
	]
	assert card_element.invoice_payment_block(None) is None

	card_element, writes = _ledger(monkeypatch, [_card_attempt(status="Paid")])
	assert card_element.invoice_payment_block("SINV-1") == "Paid"
	assert writes["lookups"] == []  # Paid needs no question to Stripe

	# Hosted Checkout, ACH and off-session charges carry no ConfirmationToken: they settle on
	# their own and are in flight until they do. Stripe is not asked about them — an ACH
	# microdeposit verification sits in requires_action for days and must not be canceled.
	card_element, writes = _ledger(monkeypatch, [_hosted_payment()], pi_status={"pi_ach": "requires_action"})
	assert card_element.invoice_payment_block("SINV-1", release=True) == "Processing"
	assert writes["lookups"] == [] and writes["cancels"] == []

	# A card attempt whose charge got no answer has no PaymentIntent on record: it may have
	# charged, so it blocks — without a lookup, since there is nothing to look up. Nobody knows
	# yet whether it charged, so it is Unconfirmed, never "will show as paid once it clears".
	card_element, writes = _ledger(monkeypatch, [_card_attempt(pi=None)])
	assert card_element.invoice_payment_block("SINV-1", release=True) == "Unconfirmed"
	assert writes["lookups"] == []
	# Beside a payment known to be settling, the settling one decides.
	card_element, writes = _ledger(monkeypatch, [_card_attempt(pi=None), _hosted_payment()])
	assert card_element.invoice_payment_block("SINV-1", release=True) == "Processing"

	# Draft, Failed and Expired rows never block, and neither does an open Checkout link here:
	# the payment paths expire that one instead (_close_open_checkouts).
	rows = [_card_attempt(name=f"SP-{s}", status=s) for s in ("Draft", "Failed", "Expired")] + [_link_sent()]
	card_element, writes = _ledger(monkeypatch, rows)
	assert card_element.invoice_payment_block("SINV-1") is None

	# The row being confirmed never blocks itself.
	card_element, writes = _ledger(monkeypatch, [_card_attempt(name="SP-SELF")])
	assert card_element.invoice_payment_block("SINV-1", exclude="SP-SELF") is None


def test_a_card_attempt_blocks_until_stripe_proves_it_never_charged(monkeypatch):
	"""After 3-D Secure the PI is succeeded (or processing) while the row still says
	Processing until the webhook posts it — the Back-from-/stripe-return case — and it must
	block. A lookup that fails proves nothing, so it blocks too."""
	for pi_status in ("succeeded", "processing"):
		card_element, writes = _ledger(monkeypatch, [_card_attempt()], pi_status={"pi_old": pi_status})
		assert card_element.invoice_payment_block("SINV-1") == "Processing", pi_status
		assert card_element.invoice_payment_block("SINV-1", release=True) == "Processing", pi_status
		assert writes["cancels"] == [] and writes["set_value"] == [], pi_status

	card_element, writes = _ledger(monkeypatch, [_card_attempt()], pi_status={})
	assert card_element.invoice_payment_block("SINV-1", release=True) == "Unconfirmed"
	assert writes["cancels"] == []


def test_a_card_attempt_that_cannot_charge_never_strands_the_invoice(monkeypatch):
	"""Declined after 3-D Secure, never confirmed, canceled — or 3-D Secure left unfinished
	past its window (Back, a closed tab). It must not block the invoice for ever; and before
	anything new is charged it is canceled at Stripe, so the old attempt and the new one can
	never both charge."""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import card_element as module

	assert set(module.DEAD_PI_STATES) == {"requires_payment_method", "requires_confirmation", "canceled"}
	assert module.AWAITING_PAYER_PI_STATE == "requires_action"
	assert module.ABANDON_AFTER_MINUTES == 30
	for pi_status, modified in (
		("requires_payment_method", FRESH),  # released at once
		("requires_confirmation", FRESH),
		("requires_action", OLD),  # released once its window has passed
	):
		card_element, writes = _ledger(
			monkeypatch,
			[_card_attempt(modified=modified)],
			pi_status={"pi_old": pi_status},
			cancel={"pi_old": "canceled"},
		)
		# The page render asks, and changes nothing.
		assert card_element.invoice_payment_block("SINV-1") is None, pi_status
		assert writes["cancels"] == [] and writes["set_value"] == [] and writes["stamps"] == [], pi_status
		# The POSTs release it: cancel at Stripe, fail the row, un-stamp the invoice.
		assert card_element.invoice_payment_block("SINV-1", release=True) is None, pi_status
		assert writes["cancels"] == ["pi_old"], pi_status
		assert writes["set_value"][0][:2] == ("Stripe Payment", "SP-OLD"), pi_status
		assert writes["set_value"][0][2]["status"] == "Failed", pi_status
		assert "nothing was charged" in writes["set_value"][0][2]["error_message"], pi_status
		assert writes["stamps"] == [("SINV-1", "Unpaid")], pi_status
		assert writes["commits"] >= 1, pi_status

	# Already canceled at Stripe: nothing to cancel; the row is brought up to date.
	card_element, writes = _ledger(
		monkeypatch, [_card_attempt(modified=FRESH)], pi_status={"pi_old": "canceled"}
	)
	assert card_element.invoice_payment_block("SINV-1", release=True) is None
	assert writes["cancels"] == [] and writes["set_value"][0][2]["status"] == "Failed"

	# The cancel is refused and the PaymentIntent does not read back canceled, so it is not
	# provably dead: it keeps blocking. It still reads back requires_action — not charging
	# either — so it "could not be released just now", never "will show as paid".
	card_element, writes = _ledger(
		monkeypatch, [_card_attempt()], pi_status={"pi_old": "requires_action"}, cancel={}
	)
	assert card_element.invoice_payment_block("SINV-1", release=True) == "Unreleased"
	assert writes["set_value"] == [] and writes["stamps"] == []

	# A cancel that answers with anything but "canceled" is no proof either.
	card_element, writes = _ledger(
		monkeypatch,
		[_card_attempt()],
		pi_status={"pi_old": "requires_action"},
		cancel={"pi_old": "processing"},
	)
	assert card_element.invoice_payment_block("SINV-1", release=True) == "Processing"
	assert writes["set_value"] == []


def test_three_d_secure_the_payer_may_still_finish_is_never_cut_short(monkeypatch):
	"""Owner's decision: a requires_action attempt is canceled only once it has sat untouched
	for 30 minutes. Inside that window the payer may be approving in their bank's app, and the
	invoice is "being processed" on every path — the render, the quote, the bank link."""
	for minutes, left in ((0, 30), (5, 25), (29.5, 1)):
		modified = NOW - _dt.timedelta(minutes=minutes)
		card_element, writes = _ledger(
			monkeypatch,
			[_card_attempt(modified=modified)],
			pi_status={"pi_old": "requires_action"},
			cancel={"pi_old": "canceled"},
		)
		block = card_element.invoice_payment_block("SINV-1")
		assert block == "Awaiting", minutes
		assert block.release_at == modified + _dt.timedelta(minutes=30), minutes
		assert card_element.awaiting_minutes(block) == left, minutes
		assert card_element.invoice_payment_block("SINV-1", release=True) == "Awaiting", minutes
		assert writes["cancels"] == [] and writes["set_value"] == [], minutes

	for minutes in (30, 45, 600):
		modified = NOW - _dt.timedelta(minutes=minutes)
		card_element, writes = _ledger(
			monkeypatch,
			[_card_attempt(modified=modified)],
			pi_status={"pi_old": "requires_action"},
			cancel={"pi_old": "canceled"},
		)
		assert card_element.invoice_payment_block("SINV-1") is None, minutes
		assert card_element.invoice_payment_block("SINV-1", release=True) is None, minutes
		assert writes["cancels"] == ["pi_old"], minutes
		assert "30 minutes" in writes["set_value"][0][2]["error_message"], minutes

	# An age that cannot be read is never taken for an old one.
	card_element, writes = _ledger(
		monkeypatch,
		[_card_attempt(modified=None)],
		pi_status={"pi_old": "requires_action"},
		cancel={"pi_old": "canceled"},
	)
	assert card_element.invoice_payment_block("SINV-1", release=True) == "Awaiting"
	assert writes["cancels"] == []

	# Inside the window, the desk's "Pay with Stripe" cannot cut it short either.
	card_element, writes = _ledger(
		monkeypatch,
		[_card_attempt(modified=FRESH)],
		pi_status={"pi_old": "requires_action"},
		cancel={"pi_old": "canceled"},
	)
	checkout = _hosted_checkout(monkeypatch, writes)
	assert _refusal(
		checkout.create_payment, sales_invoice="SINV-1", channel="Desk"
	) == card_element.MSG_AWAITING_DESK.format(minutes="25 minutes")
	assert writes["cancels"] == [] and writes["steps"] == []


def test_the_cancel_goes_through_the_sdk_free_client(monkeypatch):
	"""No Stripe SDK: the cancel is one POST through client._request, with the short control
	timeout. Unkeyed: Stripe never cancels twice anyway, and a key would replay a transient
	error for a day."""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import card_element

	seen = []
	monkeypatch.setattr(
		card_element, "_request", lambda *a, **k: seen.append((a, k)) or {"status": "canceled"}
	)
	assert card_element._cancel_payment_intent("pi_1") == {"status": "canceled"}
	((args, kwargs),) = seen
	assert args == ("POST", "/payment_intents/pi_1/cancel")
	assert kwargs == {"data": {"cancellation_reason": "abandoned"}, "timeout": card_element.CONTROL_TIMEOUT}


def test_a_refused_cancel_is_settled_by_what_stripe_now_says(monkeypatch):
	"""A cancel that raises proves nothing by itself. Another request canceled it a moment
	earlier (Stripe refuses a second cancel): it reads back canceled, and is released. The
	payer finished 3-D Secure a moment ago: it reads back succeeded, and keeps blocking."""
	for now_status, expected in (("canceled", None), ("succeeded", "Processing")):
		card_element, writes = _ledger(
			monkeypatch, [_card_attempt()], pi_status={"pi_old": "requires_action"}
		)

		def cancel_raced(pi_id, _now=now_status, _writes=writes):
			_writes["cancels"].append(pi_id)
			_writes["pi_status"][pi_id] = _now
			raise Exception(f"You cannot cancel this PaymentIntent because it has a status of {_now}.")

		monkeypatch.setattr(card_element, "_cancel_payment_intent", cancel_raced)
		assert card_element.invoice_payment_block("SINV-1", release=True) == expected, now_status
		assert writes["cancels"] == ["pi_old"]
		assert bool(writes["set_value"]) == (expected is None), now_status


def test_price_card_payment_refuses_a_settling_invoice_before_reading_the_card(monkeypatch):
	"""The quote is where a second payment would start after Back from /stripe-return; it
	must refuse before the ConfirmationToken is even read."""
	for row, message in (
		(_card_attempt(), "MSG_IN_FLIGHT"),
		# A Paid row on an invoice that still shows a balance (_resolve_target has already
		# refused a paid one): received, never "paid".
		(_card_attempt(status="Paid"), "MSG_ALREADY_RECEIVED"),
	):
		card_element, writes = _ledger(monkeypatch, [row], pi_status={"pi_old": "succeeded"})
		monkeypatch.setattr(card_element, "get_settings", _charge_settings)
		monkeypatch.setattr(card_element, "is_enabled", lambda s=None: True)
		monkeypatch.setattr(
			card_element, "_resolve_target", lambda *a: ("CUST-1", 200.0, "USD", "Invoice SINV-1")
		)
		read = []
		monkeypatch.setattr(
			card_element, "_confirmation_token_method", lambda t: read.append(t) or ("card", "credit")
		)
		exc = _raised(card_element.price_card_payment, confirmation_token="ct_new", sales_invoice="SINV-1")
		assert str(exc) == getattr(card_element, message)
		assert isinstance(exc, card_element.PaymentBlocked)
		assert read == []


def test_confirm_card_payment_rechecks_the_invoice_where_the_money_moves(monkeypatch):
	"""Between the quote and Pay, another tab, a bank payment or Accounts may have paid the
	invoice, credited it, or canceled it. The charge re-reads the invoice and the ledger."""

	def refused(card_element, writes, expected):
		message = _refusal(
			card_element.confirm_card_payment, stripe_payment="SP-Q", confirmation_token="ct_new"
		)
		assert message == expected
		assert writes["charges"] == [] and writes["store"]["SP-Q"]["status"] == "Draft"

	# Another attempt is still settling: refused, nothing charged.
	card_element, writes = _card_page(
		monkeypatch, [_card_attempt(), _quote()], pi_status={"pi_old": "succeeded"}
	)
	refused(card_element, writes, card_element.MSG_IN_FLIGHT)

	# Nothing left to pay. "Paid" only when ERPNext says Paid; a credit note brought it to
	# zero without anyone paying it.
	for outstanding, status, expected in (
		(0, "Paid", "MSG_ALREADY_PAID"),
		(0, "Credit Note Issued", "MSG_NOTHING_TO_PAY"),
		(-15, "Credit Note Issued", "MSG_NOTHING_TO_PAY"),
	):
		card_element, writes = _card_page(
			monkeypatch, [_quote()], invoice=_invoice(outstanding_amount=outstanding, status=status)
		)
		refused(card_element, writes, getattr(card_element, expected))

	# Paid in part meanwhile (a $120 cheque against the $200 quote), or credited: the quote is
	# the whole outstanding as it stood, so charging it now would overpay. A balance that grew
	# is refused the same way.
	for outstanding in (80, 199.99, 250):
		card_element, writes = _card_page(
			monkeypatch, [_quote()], invoice=_invoice(outstanding_amount=outstanding)
		)
		refused(card_element, writes, card_element.MSG_AMOUNT_CHANGED)

	# Canceled since the quote: ERPNext leaves a canceled invoice's outstanding_amount as it was,
	# so the amount alone would still charge it. A draft or a return: the same neutral refusal.
	for fields in ({"docstatus": 2}, {"docstatus": 0}, {"is_return": 1}):
		card_element, writes = _card_page(monkeypatch, [_quote()], invoice=_invoice(**fields))
		refused(card_element, writes, card_element.MSG_INVOICE_CHANGED)

	# The same amount at a different precision is the same amount: past the amount check, to
	# the ledger (which still has the other attempt settling).
	card_element, writes = _card_page(
		monkeypatch,
		[_card_attempt(), _quote()],
		invoice=_invoice(outstanding_amount=200.001),
		pi_status={"pi_old": "succeeded"},
	)
	refused(card_element, writes, card_element.MSG_IN_FLIGHT)

	# The row being confirmed is excluded from its own guard, which may release.
	card_element, writes = _card_page(monkeypatch, [_quote()])
	seen = []
	monkeypatch.setattr(
		card_element,
		"invoice_payment_block",
		lambda inv, exclude=None, release=False: seen.append((inv, exclude, release)),
	)
	result = card_element.confirm_card_payment(stripe_payment="SP-Q", confirmation_token="ct_new")
	assert seen == [("SINV-1", "SP-Q", True)]
	assert result["requires_action"] is True and writes["store"]["SP-Q"]["status"] == "Processing"


def test_the_card_charge_names_the_payment_method_types_the_element_collected(monkeypatch):
	"""The first live portal payment (2026-09-25) failed on every try with Stripe's 400
	"Payment details were collected through Stripe Elements using payment_method_types and
	cannot be confirmed through the API configured with automatic payment methods". The
	Payment Element in www/pay-card.html is built with ``paymentMethodTypes: ["card"]``, so the
	PaymentIntent that confirms its ConfirmationToken must name the same types; left out, the
	intent defaults to automatic payment methods. Pin both halves, so they cannot drift apart."""
	import pathlib
	import re

	card_element, writes = _card_page(monkeypatch, [_quote()], {"id": "pi_ok", "status": "succeeded"})
	monkeypatch.setattr(card_element, "post_charged_payment", lambda *a, **k: None, raising=False)
	card_element.confirm_card_payment(stripe_payment="SP-Q", confirmation_token="ct_new")
	params = writes["charges"][0][0]
	assert params["confirmation_token"] == "ct_new" and params["confirm"] is True
	assert params["payment_method_types"] == ["card"]
	assert "automatic_payment_methods" not in params
	# The setting is a whole descriptor; as a suffix it overflows prefix + "* " + suffix <= 22.
	settings = _charge_settings()
	settings.statement_descriptor = "Sapphire Fountains LLC"
	assert "statement_descriptor_suffix" not in card_element._intent_params(
		types.SimpleNamespace(**_quote(stripe_customer_id="cus_1", currency="USD")), "ct_new", settings
	)

	page = (pathlib.Path(__file__).resolve().parents[1] / "www" / "pay-card.html").read_text(encoding="utf-8")
	element = re.search(r"stripe\.elements\(\{(.*?)\}\);", page, re.S)
	assert element, "the Payment Element's options were not found in pay-card.html"
	declared = re.search(r"paymentMethodTypes:\s*\[([^\]]*)\]", element.group(1))
	assert declared, "pay-card.html's Element must declare paymentMethodTypes; the intent pins them"
	assert re.findall(r"[\"']([a-z_]+)[\"']", declared.group(1)) == params["payment_method_types"]


def test_confirm_card_payment_guards_the_quote_itself(monkeypatch):
	"""One quote, one charge: a quote already sent — charged, settling, or failed — is never
	sent again (a failed one would replay its first answer through the idempotency key)."""
	for status, expected in (
		("Paid", "already Paid"),
		("Processing", "already Processing"),
		("Failed", "cannot be sent again"),
	):
		card_element, writes = _card_page(monkeypatch, [_quote(status=status)])
		message = _refusal(
			card_element.confirm_card_payment, stripe_payment="SP-Q", confirmation_token="ct_new"
		)
		assert expected in message, status
		assert writes["charges"] == [], status


# --- the charge: written ahead, under the invoice lock -----------------------------


def test_a_charge_is_written_ahead_and_the_lock_held_until_it_is_committed(monkeypatch):
	"""Under the invoice lock, in order: a fresh read (the lock commits, ending the request's
	snapshot), the invoice and the ledger re-read, the row committed Processing, and only then
	the call to Stripe; the PaymentIntent is committed before the lock is released."""
	card_element, writes = _card_page(
		monkeypatch, [_quote()], {"id": "pi_new", "status": "requires_action", "client_secret": "cs_secret"}
	)
	_record_locks(monkeypatch, writes)
	result = card_element.confirm_card_payment(stripe_payment="SP-Q", confirmation_token="ct_new")

	events = writes["events"]
	name = card_element._invoice_lock_name("SINV-1")
	lock = _index(events, ("lock", name))
	fresh = _index(events, ("commit",), lock)
	first_read = _index(events, "read", lock)
	ahead = _index(events, ("set", "SP-Q", "Processing"), lock)
	ahead_commit = _index(events, ("commit",), ahead)
	charge = _index(events, "charge")
	recorded = _index(events, ("set", "SP-Q", None), charge)
	recorded_commit = _index(events, ("commit",), recorded)
	unlock = _index(events, ("unlock", name))
	assert lock < fresh < first_read < ahead < ahead_commit < charge < recorded < recorded_commit < unlock
	assert writes["locks"] == [(name, card_element.INVOICE_LOCK_TIMEOUT)]
	assert [key for _, key in writes["charges"]] == ["ee-element-SP-Q"]
	assert writes["stamps"] == [("SINV-1", "Processing")]
	assert writes["store"]["SP-Q"]["stripe_payment_intent"] == "pi_new"
	assert result == {
		"stripe_payment": "SP-Q",
		"status": "Processing",
		"payment_intent": "pi_new",
		"requires_action": True,
		"client_secret": "cs_secret",
		"outcome_unknown": False,
	}


def test_two_tabs_holding_quotes_can_never_both_charge(monkeypatch):
	"""Two tabs (or two portal contacts of one customer) each hold a quote for the invoice
	and tap Pay together. The second waits for the invoice lock, reads the ledger afresh, and
	finds the first one's 3-D Secure minutes old: refused, nothing charged."""
	card_element, writes = _card_page(
		monkeypatch,
		[_quote(name="SP-A", confirmation_token="ct_a"), _quote(name="SP-B", confirmation_token="ct_b")],
		{"id": "pi_a", "status": "requires_action", "client_secret": "s"},
		pi_status={"pi_a": "requires_action"},
		cancel={"pi_a": "canceled"},
	)
	_record_locks(monkeypatch, writes)
	first = card_element.confirm_card_payment(stripe_payment="SP-A", confirmation_token="ct_a")
	assert first["requires_action"] is True
	exc = _raised(card_element.confirm_card_payment, stripe_payment="SP-B", confirmation_token="ct_b")
	assert isinstance(exc, card_element.PaymentBlocked)
	assert str(exc) == card_element.MSG_AWAITING.format(minutes="30 minutes")
	assert [key for _, key in writes["charges"]] == ["ee-element-SP-A"]
	assert writes["store"]["SP-B"]["status"] == "Draft" and writes["cancels"] == []
	name = card_element._invoice_lock_name("SINV-1")
	assert [held for held, _ in writes["locks"]] == [name, name]

	# One lock per invoice, and a name that is always a safe file name.
	assert card_element._invoice_lock_name("SINV-1") != card_element._invoice_lock_name("SINV-2")
	assert "/" not in card_element._invoice_lock_name("ACC/SINV/1")


def test_a_request_that_cannot_get_the_invoice_lock_is_refused_without_charging(monkeypatch):
	"""Another request holds the invoice (a charge in flight): after INVOICE_LOCK_TIMEOUT
	this one is refused as "already being processed" — never charged, nothing written."""
	card_element, writes = _card_page(monkeypatch, [_quote()])
	_record_locks(monkeypatch, writes, busy=("SINV-1",))
	exc = _raised(card_element.confirm_card_payment, stripe_payment="SP-Q", confirmation_token="ct_new")
	# Worded as a payment being *started* — it may yet decline, so nothing is promised about it.
	assert isinstance(exc, card_element.PaymentBlocked) and str(exc) == card_element.MSG_BUSY
	assert "clears" not in card_element.MSG_BUSY
	assert writes["charges"] == [] and writes["set_value"] == [] and writes["stamps"] == []
	assert writes["store"]["SP-Q"]["status"] == "Draft"

	# The bank link waits on the same lock and is refused the same way.
	checkout = _hosted_checkout(monkeypatch, writes)
	exc = _raised(checkout.create_payment, sales_invoice="SINV-1", channel="Portal", method="ach")
	assert isinstance(exc, card_element.PaymentBlocked) and writes["steps"] == []


def test_the_invoice_lock_refuses_only_when_it_is_busy(monkeypatch):
	"""A LockTimeoutError raised inside the locked body (a nested lock) is not mistaken for
	this lock being busy; no invoice takes no lock."""
	card_element, writes = _ledger(monkeypatch, [])
	sync = _record_locks(monkeypatch, writes, busy=("SINV-9",))

	def enter(name):
		with card_element.invoice_lock(name):
			return "ran"

	assert str(_raised(enter, name="SINV-9")) == card_element.MSG_BUSY

	def nested():
		with card_element.invoice_lock("SINV-1"):
			raise sync.LockTimeoutError("inner")

	exc = _raised(nested)
	assert isinstance(exc, sync.LockTimeoutError) and not isinstance(exc, card_element.PaymentBlocked)
	assert enter(None) == "ran"
	assert [held for held, _ in writes["locks"]] == [card_element._invoice_lock_name("SINV-1")]


def test_every_path_that_starts_a_payment_holds_the_invoice_lock(monkeypatch):
	"""The bank link, the quote and the off-session charge hold the same per-invoice lock
	across their guard and the commit that makes the new attempt visible; an ad hoc payment
	(no invoice) takes none."""
	card_element, writes = _ledger(monkeypatch, [])
	checkout = _hosted_checkout(monkeypatch, writes)
	_record_locks(monkeypatch, writes)
	name = card_element._invoice_lock_name("SINV-1")

	checkout.create_payment(sales_invoice="SINV-1", channel="Portal", method="ach")
	events = writes["events"]
	lock = _index(events, ("lock", name))
	guard = _index(events, "read", lock)
	session = _index(events, ("step", "checkout session"), lock)
	committed = _index(events, ("commit",), session)
	assert lock < guard < session < committed < _index(events, ("unlock", name))

	writes["locks"].clear()
	checkout.create_payment(customer="CUST-1", amount=50, channel="Desk")
	assert writes["locks"] == []

	# The quote: its guard runs inside the lock.
	card_element, writes = _ledger(monkeypatch, [])
	_record_locks(monkeypatch, writes)
	monkeypatch.setattr(
		card_element,
		"get_settings",
		lambda: types.SimpleNamespace(enable_card=1, surcharge_label=None, surcharge_disclosure=None),
	)
	monkeypatch.setattr(card_element, "is_enabled", lambda s=None: True)
	monkeypatch.setattr(
		card_element, "_resolve_target", lambda *a: ("CUST-1", 200.0, "USD", "Invoice SINV-1")
	)
	monkeypatch.setattr(card_element, "_confirmation_token_method", lambda t: ("card", "debit"))
	monkeypatch.setattr(card_element, "_compute_surcharge", lambda *a, **k: 0.0)
	monkeypatch.setattr(card_element, "ensure_stripe_customer", lambda customer, s=None: "cus_1")
	card_element.price_card_payment(confirmation_token="ct_new", sales_invoice="SINV-1")
	events = writes["events"]
	assert _index(events, ("lock", name)) < _index(events, "read") < _index(events, ("unlock", name))

	# The off-session charge: its PaymentIntent is created inside the lock.
	card_element, writes = _ledger(monkeypatch, [])
	_record_locks(monkeypatch, writes)
	saved_methods = _off_session(monkeypatch, writes, {"id": "pi_off", "status": "processing"})
	saved_methods.charge_saved_method(customer="CUST-1", sales_invoice="SINV-1", channel="Desk")
	events = writes["events"]
	assert _index(events, ("lock", name)) < _index(events, "charge") < _index(events, ("unlock", name))


# --- outcomes nobody knows ---------------------------------------------------------


def test_a_charge_stripe_did_not_answer_is_retried_once_with_the_same_key(monkeypatch):
	"""A read timeout after Stripe charged the card: the retry carries the same idempotency
	key, so Stripe answers with the PaymentIntent it already made — never a second one."""
	install_frappe_stub()  # before any stripe_payments import, so it runs on its own too
	from erpnext_enhancements.stripe_payments.core import reconcile

	card_element, writes = _card_page(
		monkeypatch, [_quote()], _stripe_error(), {"id": "pi_new", "status": "succeeded"}
	)
	posted = []
	monkeypatch.setattr(reconcile, "finalize_payment", lambda sp, pi: posted.append(pi["id"]))
	result = card_element.confirm_card_payment(stripe_payment="SP-Q", confirmation_token="ct_new")
	(first, key1), (second, key2) = writes["charges"]
	assert key1 == key2 == "ee-element-SP-Q" and first == second
	assert writes["store"]["SP-Q"]["stripe_payment_intent"] == "pi_new" and posted == ["pi_new"]
	assert result["outcome_unknown"] is False


def test_an_unknown_outcome_holds_the_invoice_and_a_new_quote_is_refused(monkeypatch):
	"""The review's repro, in its shape: Stripe charges the card but our read times out, and
	the retry gets no answer either (409: the first request still executing). The row stays
	Processing — it may have charged — and the payer is sent to "being processed". Continue
	then prices nothing: every way to a second charge for the invoice is refused."""
	install_frappe_stub()  # before any stripe_payments import, so it runs on its own too
	card_element, writes = _card_page(
		monkeypatch,
		[_quote(name="SP-1")],
		_stripe_error(),
		_stripe_error(409, "Stripe API error (409): idempotency key in use"),
	)
	result = card_element.confirm_card_payment(stripe_payment="SP-1", confirmation_token="ct_new")
	assert result["status"] == "Processing" and result["outcome_unknown"] is True
	assert result["requires_action"] is False and result["payment_intent"] is None
	row = writes["store"]["SP-1"]
	assert row["status"] == "Processing" and not row["stripe_payment_intent"]
	assert row["error_message"] == card_element.NOTE_OUTCOME_UNKNOWN
	assert writes["stamps"] == [("SINV-1", "Processing")]

	# Continue: a new quote for the invoice is refused before the card is read.
	monkeypatch.setattr(
		card_element, "_resolve_target", lambda *a: ("CUST-1", 200.0, "USD", "Invoice SINV-1")
	)
	monkeypatch.setattr(card_element, "_confirmation_token_method", lambda t: ("card", "debit"))
	message = _refusal(card_element.price_card_payment, confirmation_token="ct_2", sales_invoice="SINV-1")
	assert message == card_element.MSG_UNCONFIRMED
	# A quote made earlier in another tab is refused where the money moves.
	writes["store"]["SP-2"] = _quote(name="SP-2", confirmation_token="ct_2")
	message = _refusal(card_element.confirm_card_payment, stripe_payment="SP-2", confirmation_token="ct_2")
	assert message == card_element.MSG_UNCONFIRMED
	# The unknown row itself can never be sent again.
	message = _refusal(card_element.confirm_card_payment, stripe_payment="SP-1", confirmation_token="ct_new")
	assert "already Processing" in message
	# Nor can a bank link start beside it.
	checkout = _hosted_checkout(monkeypatch, writes)
	message = _refusal(checkout.create_payment, sales_invoice="SINV-1", channel="Portal", method="ach")
	assert message == card_element.MSG_UNCONFIRMED and writes["steps"] == []
	# Two requests to Stripe in all, under one key: at most one PaymentIntent can exist.
	assert [key for _, key in writes["charges"]] == ["ee-element-SP-1", "ee-element-SP-1"]


def test_only_a_definite_stripe_answer_counts_as_a_decline(monkeypatch):
	"""A decline (402) or a refused request charged nothing: the row fails, the quote is
	spent, and the payer is told nothing was charged — in Stripe's words for a card error,
	never the raw response. Everything else is an unknown outcome and is never a decline."""
	install_frappe_stub()  # before any stripe_payments import, so it runs on its own too
	card_element, writes = _card_page(monkeypatch, [_quote()], _decline())
	message = _refusal(card_element.confirm_card_payment, stripe_payment="SP-Q", confirmation_token="ct_new")
	assert message == "The payment did not go through: Your card was declined. Nothing was charged."
	assert "{" not in message
	assert len(writes["charges"]) == 1  # Stripe answered: nothing to retry
	row = writes["store"]["SP-Q"]
	assert row["status"] == "Failed" and "402" in row["error_message"]
	assert writes["stamps"] == [("SINV-1", "Processing"), ("SINV-1", "Unpaid")]

	for status in (400, 401, 403, 404):
		card_element, writes = _card_page(
			monkeypatch, [_quote()], _stripe_error(status, f"Stripe API error ({status}): x")
		)
		message = _refusal(
			card_element.confirm_card_payment, stripe_payment="SP-Q", confirmation_token="ct_new"
		)
		assert message == card_element.MSG_NOT_CHARGED, status
		assert len(writes["charges"]) == 1 and writes["store"]["SP-Q"]["status"] == "Failed", status

	# Not answers: no response, a 5xx (Stripe replays it under the key), a 409, a 429, a 2xx
	# that would not parse, a bug. Retried once, then held as Processing.
	for first in (
		_stripe_error(),
		_stripe_error(500, "Stripe API error (500)"),
		_stripe_error(503, "Stripe API error (503)"),
		_stripe_error(409, "Stripe API error (409)"),
		_stripe_error(429, "Stripe API error (429)"),
		_stripe_error(None, "Stripe returned a response that could not be read."),
		RuntimeError("worker restarting"),
	):
		card_element, writes = _card_page(monkeypatch, [_quote()], first, first)
		result = card_element.confirm_card_payment(stripe_payment="SP-Q", confirmation_token="ct_new")
		assert (result["status"], result["outcome_unknown"]) == ("Processing", True), first
		assert len(writes["charges"]) == 2 and writes["store"]["SP-Q"]["status"] == "Processing", first

	# After an unknown first attempt only a decline on the retry is proof: any other refusal
	# could be about the first attempt having run.
	card_element, writes = _card_page(
		monkeypatch,
		[_quote()],
		_stripe_error(),
		_stripe_error(400, "Stripe API error (400): token already used"),
	)
	result = card_element.confirm_card_payment(stripe_payment="SP-Q", confirmation_token="ct_new")
	assert result["outcome_unknown"] is True and writes["store"]["SP-Q"]["status"] == "Processing"
	card_element, writes = _card_page(monkeypatch, [_quote()], _stripe_error(), _decline())
	message = _refusal(card_element.confirm_card_payment, stripe_payment="SP-Q", confirmation_token="ct_new")
	assert "Nothing was charged" in message and writes["store"]["SP-Q"]["status"] == "Failed"

	# A PaymentIntent that comes back already dead charged nothing, and says so.
	card_element, writes = _card_page(
		monkeypatch, [_quote()], {"id": "pi_x", "status": "requires_payment_method"}
	)
	message = _refusal(card_element.confirm_card_payment, stripe_payment="SP-Q", confirmation_token="ct_new")
	assert message == card_element.MSG_NOT_CHARGED
	assert (
		writes["store"]["SP-Q"]["status"] == "Failed"
		and writes["store"]["SP-Q"]["stripe_payment_intent"] == "pi_x"
	)


def test_every_request_pins_the_stripe_api_version(monkeypatch):
	"""Requests name the API version they were written against rather than riding the account
	default, so a Dashboard upgrade cannot change what they mean: the next major version drops
	`payment_method_types` from PaymentIntents, which every card charge sends. The version is the
	one the account's webhook events carry, and it is a real Stripe version string."""
	import re

	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import client

	monkeypatch.setattr(client, "get_api_key", lambda settings=None: "sk_placeholder")
	assert re.fullmatch(r"20\d\d-\d\d-\d\d\.[a-z]+", client.STRIPE_API_VERSION)
	assert client._headers(object())["Stripe-Version"] == client.STRIPE_API_VERSION
	assert client._headers(object(), "key-1") == {
		"Authorization": "Bearer sk_placeholder",
		"Stripe-Version": client.STRIPE_API_VERSION,
		"Idempotency-Key": "key-1",
	}

	seen = []

	def request(method, url, headers=None, data=None, params=None, timeout=None):
		seen.append(headers)
		response = types.SimpleNamespace(status_code=200, text="{}")
		response.json = lambda: {}
		return response

	# Replace the module, not one attribute: under CI `requests` is the empty stub module installed
	# above, which has no `request` to patch (a real `requests` is only there when some other import
	# pulled it in first). The next test does the same.
	monkeypatch.setattr(
		client, "requests", types.SimpleNamespace(request=request, RequestException=Exception)
	)
	monkeypatch.setattr(client, "get_settings", lambda: object())
	client._request("GET", "/payment_intents/pi_1")
	assert seen[0]["Stripe-Version"] == client.STRIPE_API_VERSION


def test_the_client_tells_a_decline_from_no_answer(monkeypatch):
	"""client._request gives every HTTP failure its status (a transport error has none), and
	Stripe's own message for a card error. Only 400/401/402/403/404 are definite."""
	import json

	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import client

	class RequestException(Exception):
		pass

	calls = []
	answers = []

	def respond(status, body):
		response = types.SimpleNamespace(status_code=status, text=json.dumps(body))
		response.json = lambda: body
		return response

	def request(method, url, headers=None, data=None, params=None, timeout=None):
		calls.append(
			{
				"method": method,
				"url": url,
				"headers": headers,
				"data": data,
				"params": params,
				"timeout": timeout,
			}
		)
		answer = answers.pop(0)
		if isinstance(answer, BaseException):
			raise answer
		return answer

	monkeypatch.setattr(
		client, "requests", types.SimpleNamespace(request=request, RequestException=RequestException)
	)
	monkeypatch.setattr(client, "get_settings", lambda: types.SimpleNamespace())
	monkeypatch.setattr(client, "get_api_key", lambda settings: "sk_test_x")

	answers.append(respond(402, {"error": {"code": "card_declined", "message": "Your card was declined."}}))
	exc = _raised(client.create_payment_intent, params={"amount": 100}, idempotency_key="ee-element-SP-1")
	assert (exc.status_code, exc.definite, exc.stripe_message) == (402, True, "Your card was declined.")
	assert calls[-1]["headers"]["Idempotency-Key"] == "ee-element-SP-1"
	assert calls[-1]["timeout"] == client.TIMEOUT
	for status in (400, 401, 403, 404):
		answers.append(respond(status, {"error": {"message": "no"}}))
		assert client.is_definite_failure(_raised(client.retrieve_account)), status
	for status in (409, 429, 500, 502, 503):
		answers.append(respond(status, {"error": {"message": "try later"}}))
		exc = _raised(client.retrieve_account)
		assert exc.status_code == status and not exc.definite, status

	# No answer at all: no status, and raised from None, so no traceback chains back into the
	# requests call and its Authorization header.
	answers.append(RequestException("Read timed out."))
	exc = _raised(client.create_payment_intent, params={"amount": 100}, idempotency_key="k")
	assert exc.status_code is None and not exc.definite and exc.__suppress_context__
	assert "sk_test_x" not in str(exc)

	# A 2xx that will not parse took effect all the same: unknown, never a failure.
	unreadable = respond(200, {})
	unreadable.json = lambda: (_ for _ in ()).throw(ValueError("Expecting value"))
	answers.append(unreadable)
	exc = _raised(client.create_payment_intent, params={"amount": 100}, idempotency_key="k")
	assert isinstance(exc, client.StripeError) and not exc.definite
	assert client.is_definite_failure(ValueError("x")) is False

	# The new endpoints: expire (POST, unkeyed), list (strongly consistent), short timeouts.
	answers.append(respond(200, {"id": "cs_1", "status": "expired"}))
	assert client.expire_checkout_session("cs_1", timeout=10)["status"] == "expired"
	assert (calls[-1]["method"], calls[-1]["url"], calls[-1]["timeout"]) == (
		"POST",
		client.API_BASE + "/checkout/sessions/cs_1/expire",
		10,
	)
	assert "Idempotency-Key" not in calls[-1]["headers"]
	answers.append(respond(200, {"data": [], "has_more": False}))
	client.list_payment_intents("cus_1", created_gte=1700000000, starting_after="pi_9", timeout=10)
	assert calls[-1]["url"] == client.API_BASE + "/payment_intents"
	assert sorted(calls[-1]["params"]) == sorted(
		[("customer", "cus_1"), ("limit", 100), ("created[gte]", 1700000000), ("starting_after", "pi_9")]
	)
	answers.append(respond(200, {"id": "pi_1", "status": "requires_action"}))
	client.retrieve_payment_intent("pi_1", timeout=4)
	assert calls[-1]["timeout"] == 4


def test_a_charged_card_whose_payment_entry_fails_is_processing_never_failed(monkeypatch):
	"""After the charge is committed Processing, a failure to post its Payment Entry (no
	deposit account, a closed period, the finalize lock held by the webhook) must never reach
	the customer as "failed". It is rolled back to the committed state, logged, and answered
	"Processing"; poll_pending and the webhook post it later."""
	install_frappe_stub()  # before any stripe_payments import, so it runs on its own too
	from erpnext_enhancements.stripe_payments.core import reconcile

	card_element, writes = _card_page(monkeypatch, [_quote()], {"id": "pi_ok", "status": "succeeded"})
	_record_locks(monkeypatch, writes)

	def finalize(doc, pi):
		writes["events"].append(("finalize", doc.status, doc.stripe_payment_intent))
		raise Exception("Stripe Deposit / Clearing Account is not set in Stripe Payments Settings.")

	monkeypatch.setattr(reconcile, "finalize_payment", finalize)
	result = card_element.confirm_card_payment(stripe_payment="SP-Q", confirmation_token="ct_new")
	assert (result["status"], result["requires_action"], result["outcome_unknown"]) == (
		"Processing",
		False,
		False,
	)
	assert "Deposit" not in repr(result)
	events = writes["events"]
	posted = _index(events, "finalize")
	# Posted only once the charge was committed Processing with its PaymentIntent, outside the lock.
	assert events[posted] == ("finalize", "Processing", "pi_ok")
	assert _index(events, "unlock") < posted < _index(events, ("rollback",), posted)
	assert any("Payment Entry not posted" in " ".join(map(str, args)) for args in writes["errors"])
	row = writes["store"]["SP-Q"]
	assert (row["status"], row["stripe_payment_intent"]) == ("Processing", "pi_ok")


# --- emailed Checkout links (owner's decision 3) ------------------------------------


def test_an_emailed_checkout_link_is_expired_before_a_card_charge(monkeypatch):
	"""A Link Sent Checkout Session is payable for 24 hours from the customer's inbox. Before
	a card charge it is expired at Stripe (through the REST client) and marked Expired — so
	the emailed link can never become a second payment."""
	card_element, writes = _card_page(
		monkeypatch,
		[_link_sent(), _quote()],
		{"id": "pi_new", "status": "requires_action", "client_secret": "s"},
	)
	_checkout_sessions(monkeypatch, card_element, writes, {"cs_link": "open"}, expire={"cs_link": "expired"})
	result = card_element.confirm_card_payment(stripe_payment="SP-Q", confirmation_token="ct_new")
	assert result["requires_action"] is True
	assert writes["expires"] == [("cs_link", card_element.CONTROL_TIMEOUT)]
	assert writes["store"]["SP-LINK"]["status"] == "Expired"
	events = writes["events"]
	expired = _index(events, ("set", "SP-LINK", "Expired"))
	assert (
		_index(events, "expire") < expired < _index(events, ("commit",), expired) < _index(events, "charge")
	)

	# A quote moves no money, so it leaves the link open.
	card_element, writes = _ledger(monkeypatch, [_link_sent()])
	_checkout_sessions(monkeypatch, card_element, writes, {"cs_link": "open"}, expire={"cs_link": "expired"})
	monkeypatch.setattr(
		card_element,
		"get_settings",
		lambda: types.SimpleNamespace(enable_card=1, surcharge_label=None, surcharge_disclosure=None),
	)
	monkeypatch.setattr(card_element, "is_enabled", lambda s=None: True)
	monkeypatch.setattr(
		card_element, "_resolve_target", lambda *a: ("CUST-1", 200.0, "USD", "Invoice SINV-1")
	)
	monkeypatch.setattr(card_element, "_confirmation_token_method", lambda t: ("card", "debit"))
	monkeypatch.setattr(card_element, "_compute_surcharge", lambda *a, **k: 0.0)
	monkeypatch.setattr(card_element, "ensure_stripe_customer", lambda customer, s=None: "cus_1")
	card_element.price_card_payment(confirmation_token="ct_new", sales_invoice="SINV-1")
	assert writes["expires"] == [] and writes["store"]["SP-LINK"]["status"] == "Link Sent"


def test_a_completed_checkout_link_refuses_the_card_charge(monkeypatch):
	"""Stripe refuses to expire a session already completed — paid, or an ACH debit submitted,
	the webhook not yet landed. The invoice is being paid: the card charge is refused, and the
	row is marked Processing so the next guard blocks without asking Stripe."""
	card_element, writes = _card_page(monkeypatch, [_link_sent(), _quote()])
	_checkout_sessions(monkeypatch, card_element, writes, {"cs_link": "complete"}, expire={})
	exc = _raised(card_element.confirm_card_payment, stripe_payment="SP-Q", confirmation_token="ct_new")
	assert isinstance(exc, card_element.PaymentBlocked) and str(exc) == card_element.MSG_IN_FLIGHT
	assert writes["charges"] == [] and writes["store"]["SP-Q"]["status"] == "Draft"
	assert writes["store"]["SP-LINK"]["status"] == "Processing"
	assert ("SINV-1", "Processing") in writes["stamps"]
	assert card_element.invoice_payment_block("SINV-1") == "Processing"


def test_a_link_stripe_will_neither_close_nor_show_refuses_without_charging(monkeypatch):
	"""When Stripe answers neither way the charge is refused (nothing charged; the next try
	asks again). A session that reads back expired — its 24 hours ran out, or an earlier
	expire's answer was lost — is simply marked Expired."""
	for sessions, expected in (({}, "MSG_LINK_STILL_OPEN"), ({"cs_link": "open"}, "MSG_LINK_STILL_OPEN")):
		card_element, writes = _card_page(monkeypatch, [_link_sent(), _quote()])
		_checkout_sessions(monkeypatch, card_element, writes, dict(sessions), expire={})
		exc = _raised(card_element.confirm_card_payment, stripe_payment="SP-Q", confirmation_token="ct_new")
		assert isinstance(exc, card_element.PaymentBlocked) and str(exc) == getattr(card_element, expected)
		assert writes["charges"] == [] and writes["store"]["SP-LINK"]["status"] == "Link Sent"

	card_element, writes = _card_page(monkeypatch, [_link_sent(), _quote()])
	_checkout_sessions(monkeypatch, card_element, writes, {"cs_link": "expired"}, expire={})
	card_element.confirm_card_payment(stripe_payment="SP-Q", confirmation_token="ct_new")
	assert writes["store"]["SP-LINK"]["status"] == "Expired" and len(writes["charges"]) == 1


def test_a_new_checkout_link_expires_the_one_already_sent(monkeypatch):
	"""Two open Checkout Sessions for one invoice are two payments waiting to happen: the desk
	link (or the Bank button) expires the one already sent before it creates its own."""
	card_element, writes = _ledger(monkeypatch, [_link_sent()])
	checkout = _hosted_checkout(monkeypatch, writes)
	_checkout_sessions(monkeypatch, card_element, writes, {"cs_link": "open"}, expire={"cs_link": "expired"})
	checkout.create_payment(sales_invoice="SINV-1", channel="Desk")
	assert writes["store"]["SP-LINK"]["status"] == "Expired"
	events = writes["events"]
	assert _index(events, "expire") < _index(events, ("step", "checkout session"))

	card_element, writes = _ledger(monkeypatch, [_link_sent()])
	checkout = _hosted_checkout(monkeypatch, writes)
	_checkout_sessions(monkeypatch, card_element, writes, {"cs_link": "complete"}, expire={})
	message = _refusal(checkout.create_payment, sales_invoice="SINV-1", channel="Desk")
	assert message == card_element.MSG_IN_FLIGHT and writes["steps"] == []


# --- the off-session charge (desk, autopay, dunning) --------------------------------


def _off_session(monkeypatch, writes, *answers):
	"""Point saved_methods at the fakes: an enrolled customer with a saved debit card."""
	import frappe as frappe_stub

	from erpnext_enhancements.stripe_payments.core import saved_methods

	monkeypatch.setattr(
		saved_methods,
		"get_settings",
		lambda: types.SimpleNamespace(enabled=1, surcharge_enabled=0, company="Sapphire Fountains"),
	)
	monkeypatch.setattr(saved_methods, "is_enabled", lambda s=None: True)
	monkeypatch.setattr(
		saved_methods,
		"_resolve_target",
		lambda si, customer, amount, description, settings: ("CUST-1", 200.0, "USD", f"Invoice {si}")
		if si
		else (customer, amount, "USD", description or "Payment"),
	)
	monkeypatch.setattr(saved_methods, "_payment_method_funding", lambda pm: ("card", "debit"))
	monkeypatch.setattr(
		saved_methods,
		"_stamp_invoice",
		lambda inv, status, *a: (
			writes["stamps"].append((inv, status)),
			writes["events"].append(("stamp", inv, status)),
		),
	)
	writes["alerts"] = []
	monkeypatch.setattr(
		saved_methods,
		"_alert_failed_autocharge",
		lambda sp, reason: writes["alerts"].append((sp.name, reason)),
	)
	base = frappe_stub.db.get_value

	def get_value(doctype, name=None, fieldname=None, *args, **kwargs):
		if doctype == "Customer":
			return {"custom_stripe_customer_id": "cus_1", "custom_stripe_default_payment_method": "pm_1"}.get(
				fieldname
			)
		return base(doctype, name, fieldname, *args, **kwargs)

	frappe_stub.db.get_value = get_value
	_script_charges(monkeypatch, saved_methods, writes, *answers)
	return saved_methods


def test_the_off_session_charge_runs_the_invoice_guard(monkeypatch):
	"""charge_saved_method (desk, autopay, dunning) now runs the rule every other path runs:
	refused while a payment is settling or received — as PaymentBlocked, which dunning
	reschedules on — with nothing written or charged; a dead card attempt canceled first; an
	emailed link expired first; and the row committed Processing before Stripe is called."""
	card_element, writes = _ledger(monkeypatch, [_card_attempt()], pi_status={"pi_old": "succeeded"})
	saved_methods = _off_session(monkeypatch, writes)
	for channel in ("Desk", "Auto", "Dunning"):
		exc = _raised(
			saved_methods.charge_saved_method, customer="CUST-1", sales_invoice="SINV-1", channel=channel
		)
		assert (
			isinstance(exc, card_element.PaymentBlocked) and str(exc) == card_element.MSG_IN_FLIGHT
		), channel
	assert writes["charges"] == [] and not [e for e in writes["events"] if e[0] == "insert"]

	card_element, writes = _ledger(monkeypatch, [_card_attempt(status="Paid")])
	saved_methods = _off_session(monkeypatch, writes)
	message = _refusal(
		saved_methods.charge_saved_method, customer="CUST-1", sales_invoice="SINV-1", channel="Desk"
	)
	assert message == card_element.MSG_ALREADY_RECEIVED_DESK

	# A card attempt abandoned hours ago, and an emailed link: both closed, then the charge.
	# (Accounts charging from the desk; autopay and dunning leave the link alone — see
	# test_autopay_and_dunning_never_expire_the_link_the_customer_was_sent.)
	card_element, writes = _ledger(
		monkeypatch,
		[_card_attempt(), _link_sent()],
		pi_status={"pi_old": "requires_action"},
		cancel={"pi_old": "canceled"},
	)
	saved_methods = _off_session(monkeypatch, writes, {"id": "pi_off", "status": "succeeded"})
	_checkout_sessions(monkeypatch, card_element, writes, {"cs_link": "open"}, expire={"cs_link": "expired"})
	posted = []
	monkeypatch.setattr(
		card_element, "post_charged_payment", lambda sp, pi: posted.append((sp.name, pi["id"]))
	)
	result = saved_methods.charge_saved_method(customer="CUST-1", sales_invoice="SINV-1", channel="Desk")
	events = writes["events"]
	inserted = _index(events, "insert")
	assert events[inserted][2] == "Processing"
	assert (
		_index(events, "cancel")
		< _index(events, "expire")
		< inserted
		< _index(events, ("commit",), inserted)
		< _index(events, "charge")
	)
	assert writes["store"]["SP-LINK"]["status"] == "Expired"
	new = events[inserted][1]
	assert posted == [(new, "pi_off")] and result["payment_intent"] == "pi_off"
	assert writes["charges"][0][1] == f"ee-offsession-{new}"


def test_an_unanswered_off_session_charge_is_processing_never_a_decline(monkeypatch):
	"""A timeout after an autopay charge may have taken the money. The row is held Processing
	(committed before the call), Accounts get no "declined" alert, dunning waits instead of
	retrying — and the next charge for the invoice is blocked until poll_pending settles it."""
	card_element, writes = _ledger(monkeypatch, [])
	saved_methods = _off_session(
		monkeypatch, writes, _stripe_error(), _stripe_error(500, "Stripe API error (500)")
	)
	result = saved_methods.charge_saved_method(customer="CUST-1", sales_invoice="SINV-1", channel="Auto")
	assert (result["status"], result["payment_intent"]) == ("Processing", None)
	assert writes["alerts"] == []
	(row,) = writes["store"].values()
	assert row["status"] == "Processing" and row["error_message"] == card_element.NOTE_OUTCOME_UNKNOWN
	assert len({key for _, key in writes["charges"]}) == 1 and len(writes["charges"]) == 2
	events = writes["events"]
	assert _index(events, ("commit",), _index(events, "insert")) < _index(events, "charge")
	assert card_element.invoice_payment_block("SINV-1") == "Unconfirmed"

	# A decline: failed, alerted (autopay), raised — and not PaymentBlocked.
	card_element, writes = _ledger(monkeypatch, [])
	saved_methods = _off_session(monkeypatch, writes, _decline())
	exc = _raised(
		saved_methods.charge_saved_method, customer="CUST-1", sales_invoice="SINV-1", channel="Auto"
	)
	assert "Off-session charge failed" in str(exc) and not isinstance(exc, card_element.PaymentBlocked)
	(row,) = writes["store"].values()
	assert row["status"] == "Failed" and len(writes["alerts"]) == 1 and len(writes["charges"]) == 1
	assert writes["stamps"][-1] == ("SINV-1", "Failed")


def test_dunning_reads_the_shared_rule_and_never_counts_a_refusal_as_a_decline(monkeypatch):
	"""dunning used a raw count of Processing rows, which also counted a portal card attempt
	whose 3-D Secure was abandoned: the case was pushed back two days at a time. It now asks
	card_element.invoice_payment_block (read-only). A guard refusal at charge time is not a
	declined card: no attempt counted, no customer email."""
	import datetime as dt

	frappe_stub = install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import card_element, dunning, saved_methods

	today = dt.date(2026, 6, 18)
	stamps, emails, verdicts, charges = [], [], [], []
	invoice = types.SimpleNamespace(
		name="SINV-1",
		customer="CUST-1",
		outstanding_amount=200.0,
		custom_dunning_attempts=1,
		custom_dunning_opened_on=dt.date(2026, 6, 10),
		currency="USD",
		company="Sapphire Fountains",
	)
	monkeypatch.setattr(frappe_stub, "get_doc", lambda doctype, name=None: invoice, raising=False)
	monkeypatch.setattr(
		frappe_stub,
		"db",
		types.SimpleNamespace(
			get_value=lambda *a, **k: 200.0,
			exists=lambda *a, **k: None,
			set_value=lambda doctype, name, values, *a, **k: stamps.append(values),
			commit=lambda: None,
		),
	)
	monkeypatch.setattr(dunning, "_can_autocharge", lambda customer: True)
	monkeypatch.setattr(dunning, "_email_customer", lambda *a, **k: emails.append(a))

	def run(verdict, charge):
		stamps.clear(), emails.clear(), verdicts.clear(), charges.clear()
		monkeypatch.setattr(
			card_element,
			"invoice_payment_block",
			lambda inv, exclude=None, release=False: verdicts.append((inv, release)) or verdict,
		)
		monkeypatch.setattr(
			saved_methods, "charge_saved_method", lambda **k: charges.append(k) or charge(**k)
		)
		dunning._process_case("SINV-1", today, [2, 4, 7])

	# A payment settling: rescheduled, nothing charged — asked read-only.
	run("Processing", lambda **k: {"status": "Paid"})
	assert verdicts == [("SINV-1", False)] and charges == []
	assert stamps == [{"custom_dunning_next_retry": today + dt.timedelta(days=2)}]

	# Nothing in flight (say, a 3-D Secure abandoned hours ago): the saved card is charged.
	run(None, lambda **k: {"status": "Processing"})
	assert charges == [{"customer": "CUST-1", "sales_invoice": "SINV-1", "channel": "Dunning"}]

	# Refused at charge time (another payment started since): not a decline.
	def blocked(**k):
		raise card_element.PaymentBlocked(card_element.MSG_IN_FLIGHT)

	run(None, blocked)
	assert stamps == [{"custom_dunning_next_retry": today + dt.timedelta(days=2)}] and emails == []

	# A real decline still counts, and still emails.
	def declined(**k):
		raise Exception("Off-session charge failed: Your card was declined.")

	run(None, declined)
	assert {"custom_dunning_attempts": 2}.items() <= stamps[0].items() and len(emails) == 1


# --- poll_pending: nothing stays "being processed" for good ------------------------


def _poll(monkeypatch, rows, pi_status=None, cancel=None, listing=None, sessions=None, invoice=None):
	"""poll_pending against the fake ledger. A ``pi_status`` or ``sessions`` value that is an
	exception is raised (a ``StripeError`` 404, say), as is a ``listing`` page that is one."""
	card_element, writes = _ledger(monkeypatch, rows, pi_status=pi_status, cancel=cancel, invoice=invoice)
	from erpnext_enhancements.stripe_payments.core import client, reconcile, tasks

	monkeypatch.setattr(tasks, "get_settings", lambda: types.SimpleNamespace(enabled=1))
	monkeypatch.setattr(tasks, "is_enabled", lambda s=None: True)
	monkeypatch.setattr(tasks, "now_datetime", lambda: NOW)
	monkeypatch.setattr(
		tasks, "add_to_date", lambda value, minutes=0, **k: value + _dt.timedelta(minutes=minutes)
	)

	def retrieve(pi_id, timeout=None):
		if pi_id not in writes["pi_status"]:
			raise Exception("Stripe unreachable")
		if isinstance(writes["pi_status"][pi_id], BaseException):
			raise writes["pi_status"][pi_id]
		return {"id": pi_id, "status": writes["pi_status"][pi_id]}

	monkeypatch.setattr(client, "retrieve_payment_intent", retrieve)
	writes["sessions"] = dict(sessions or {})

	def retrieve_session(session_id, expand=None, timeout=None):
		answer = writes["sessions"].get(session_id)
		if answer is None:
			raise Exception("Stripe unreachable")
		if isinstance(answer, BaseException):
			raise answer
		return {"id": session_id, "status": answer, "payment_status": "unpaid"}

	monkeypatch.setattr(client, "retrieve_checkout_session", retrieve_session)
	writes["finalized"] = []
	monkeypatch.setattr(
		reconcile, "finalize_payment", lambda sp, obj: writes["finalized"].append((sp.name, obj.get("id")))
	)
	writes["listed"] = []
	pages = listing or [{"data": [], "has_more": False}]

	def list_pis(customer, created_gte=None, starting_after=None, timeout=None):
		writes["listed"].append((customer, created_gte, starting_after, timeout))
		page = pages[min(len(writes["listed"]), len(pages)) - 1]
		if isinstance(page, BaseException):
			raise page
		return page

	monkeypatch.setattr(card_element, "list_payment_intents", list_pis)
	_record_locks(monkeypatch, writes)
	return tasks, card_element, writes


def test_poll_pending_finds_a_charge_stripe_never_answered(monkeypatch):
	"""A row held Processing with no PaymentIntent is looked for among the customer's
	PaymentIntents (the strongly consistent list, not search) by its name in the metadata.
	Found: recorded and settled — a succeeded one is posted. Provably absent: nothing
	reached Stripe, so the row fails and the invoice is payable again. Not provable (the
	list ran past its pages, or no Stripe customer): left blocking."""
	import calendar

	unknown = dict(_card_attempt(name="SP-U", pi=None, modified=NOW - _dt.timedelta(minutes=20)))
	tasks, card_element, writes = _poll(
		monkeypatch,
		[unknown],
		listing=[
			{
				"data": [
					{"id": "pi_other", "status": "succeeded", "metadata": {"stripe_payment": "SP-X"}},
					{"id": "pi_found", "status": "succeeded", "metadata": {"stripe_payment": "SP-U"}},
				],
				"has_more": False,
			}
		],
	)
	tasks.poll_pending()
	assert writes["store"]["SP-U"]["stripe_payment_intent"] == "pi_found"
	assert writes["finalized"] == [("SP-U", "pi_found")]
	day_before = calendar.timegm(unknown["creation"].timetuple()) - 86400
	assert writes["listed"] == [("cus_1", day_before, None, card_element.CONTROL_TIMEOUT)]

	tasks, card_element, writes = _poll(monkeypatch, [dict(unknown, initiated_by="jane@example.com")])
	tasks.poll_pending()
	row = writes["store"]["SP-U"]
	# Expired, never Failed: it never reached a card, so it is not a decline (dunning enrols
	# Failed autopay rows and emails "declined"), and autopay's sweep may charge again.
	assert row["status"] == "Expired" and "nothing was charged" in row["error_message"]
	assert writes["stamps"] == [("SINV-1", "Unpaid")] and writes["finalized"] == []
	# The card page's payer was told "being processed": they hear it did not go through, and
	# Accounts hear of it.
	assert writes["emails"] == [("SP-U", "jane@example.com", "invoice SINV-1")]
	assert [docname for _, docname in writes["accounts_alerts"]] == ["SP-U"]

	# Found, but declined after authentication: released like any dead attempt.
	tasks, card_element, writes = _poll(
		monkeypatch,
		[dict(unknown)],
		cancel={"pi_found": "canceled"},
		listing=[
			{
				"data": [
					{
						"id": "pi_found",
						"status": "requires_payment_method",
						"metadata": {"stripe_payment": "SP-U"},
					}
				],
				"has_more": False,
			}
		],
	)
	tasks.poll_pending()
	assert writes["cancels"] == ["pi_found"] and writes["store"]["SP-U"]["status"] == "Failed"

	page = {
		"data": [{"id": "pi_1", "status": "succeeded", "metadata": {"stripe_payment": "SP-X"}}],
		"has_more": True,
	}
	tasks, card_element, writes = _poll(monkeypatch, [dict(unknown)], listing=[page])
	tasks.poll_pending()
	assert len(writes["listed"]) == card_element.PAYMENT_INTENT_LIST_PAGES
	assert [after for _, _, after, _ in writes["listed"][1:]] == ["pi_1"] * (
		card_element.PAYMENT_INTENT_LIST_PAGES - 1
	)
	assert writes["store"]["SP-U"]["status"] == "Processing"

	tasks, card_element, writes = _poll(monkeypatch, [dict(unknown, stripe_customer_id=None)])
	tasks.poll_pending()
	assert writes["listed"] == [] and writes["store"]["SP-U"]["status"] == "Processing"


def test_poll_pending_settles_3ds_left_past_its_window_and_never_touches_ach(monkeypatch):
	"""3-D Secure untouched for 30 minutes, or a card declined after it, is canceled and
	failed, so its invoice is not "being processed" for good. 3-D Secure inside its window is
	left alone, and so is a hosted Checkout ACH debit in requires_action (a payment)."""
	rows = [
		_card_attempt(
			name="SP-STALE", pi="pi_stale", invoice="SINV-1", modified=NOW - _dt.timedelta(minutes=40)
		),
		_card_attempt(
			name="SP-WAIT", pi="pi_wait", invoice="SINV-2", modified=NOW - _dt.timedelta(minutes=20)
		),
		_card_attempt(
			name="SP-DECLINED", pi="pi_declined", invoice="SINV-3", modified=NOW - _dt.timedelta(minutes=16)
		),
		_hosted_payment(name="SP-ACH", pi="pi_ach", invoice="SINV-4"),
	]
	tasks, card_element, writes = _poll(
		monkeypatch,
		rows,
		pi_status={
			"pi_stale": "requires_action",
			"pi_wait": "requires_action",
			"pi_declined": "requires_payment_method",
			"pi_ach": "requires_action",
		},
		cancel={pid: "canceled" for pid in ("pi_stale", "pi_wait", "pi_declined", "pi_ach")},
	)
	tasks.poll_pending()
	store = writes["store"]
	assert sorted(writes["cancels"]) == ["pi_declined", "pi_stale"]
	assert [store[name]["status"] for name in ("SP-STALE", "SP-WAIT", "SP-DECLINED", "SP-ACH")] == [
		"Failed",
		"Processing",
		"Failed",
		"Processing",
	]
	assert "30 minutes" in store["SP-STALE"]["error_message"]
	assert sorted(writes["stamps"]) == [("SINV-1", "Unpaid"), ("SINV-3", "Unpaid")]
	assert sorted(held for held, _ in writes["locks"]) == sorted(
		card_element._invoice_lock_name(inv) for inv in ("SINV-1", "SINV-3")
	)


# --- page renders never wait long on Stripe -----------------------------------------


def test_page_renders_never_wait_long_on_stripe(monkeypatch):
	"""/pay and /pay-card read the verdict with a few seconds per lookup, never the client's
	30; a lookup that fails is "being processed" (safe) and logs nothing per render; and once
	one fails, a list does not try the rest — each would be another timeout."""
	install_frappe_stub()  # before any stripe_payments import, so it runs on its own too
	from erpnext_enhancements.stripe_payments.core import client

	card_element, writes = _ledger(monkeypatch, [_card_attempt()], pi_status={"pi_old": "succeeded"})
	card_element.invoice_payment_block("SINV-1")
	assert writes["timeouts"] == [card_element.READ_TIMEOUT] and card_element.READ_TIMEOUT <= 5
	writes["timeouts"].clear()
	card_element.invoice_payment_block("SINV-1", release=True)
	assert (
		writes["timeouts"] == [card_element.CONTROL_TIMEOUT] and card_element.CONTROL_TIMEOUT < client.TIMEOUT
	)

	card_element, writes = _ledger(monkeypatch, [_card_attempt()], pi_status={})
	assert card_element.invoice_payment_block("SINV-1") == "Unconfirmed" and writes["errors"] == []
	assert card_element.invoice_payment_block("SINV-1", release=True) == "Unconfirmed" and writes["errors"]

	rows = [_card_attempt(name=f"SP-{i}", pi=f"pi_{i}", invoice=f"SINV-{i}") for i in range(5)]
	card_element, writes = _ledger(monkeypatch, rows, pi_status={})
	names = [f"SINV-{i}" for i in range(5)]
	assert card_element.invoice_payment_blocks(names) == {name: "Unconfirmed" for name in names}
	assert len(writes["lookups"]) == 1 and writes["errors"] == []


def _load_pay_card_page():
	import importlib.util
	from pathlib import Path

	path = Path(__file__).resolve().parents[1] / "www" / "pay_card.py"
	spec = importlib.util.spec_from_file_location("ee_test_pay_card_page", path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def test_pay_card_page_shows_paid_or_processing_instead_of_a_form(monkeypatch):
	"""Back from /stripe-return re-downloads /pay-card (no-store). A paid invoice, or one
	whose payment is still settling, gets "paid / being processed — back to invoices" and
	never a working card form; and only its owner learns which."""
	frappe_stub = install_frappe_stub()
	page = _load_pay_card_page()
	settings = types.SimpleNamespace(enabled=1, enable_card=1, enable_ach=0, publishable_key="pk_test_x")
	monkeypatch.setattr(page, "get_settings", lambda: settings)
	monkeypatch.setattr(page, "is_enabled", lambda s=None: True)
	monkeypatch.setattr(page, "get_portal_customers", lambda: ["CUST-1"])
	monkeypatch.setattr(frappe_stub, "session", types.SimpleNamespace(user="jane@example.com"))
	monkeypatch.setattr(
		frappe_stub, "sessions", types.SimpleNamespace(get_csrf_token=lambda: "tok"), raising=False
	)
	monkeypatch.setattr(frappe_stub, "form_dict", {"invoice": "SINV-1"}, raising=False)
	asked = []

	def render(customer="CUST-1", outstanding=200.0, block=None, is_return=0, status=None):
		invoice = types.SimpleNamespace(
			name="SINV-1",
			customer=customer,
			outstanding_amount=outstanding,
			currency="USD",
			docstatus=1,
			is_return=is_return,
			status=status or ("Paid" if outstanding <= 0 else "Unpaid"),
		)
		monkeypatch.setattr(
			frappe_stub,
			"db",
			types.SimpleNamespace(exists=lambda *a, **k: True, get_value=lambda *a, **k: invoice),
		)
		monkeypatch.setattr(page, "invoice_payment_block", lambda name: asked.append(name) or block)
		context = types.SimpleNamespace()
		page.get_context(context)
		return context

	ctx = render()
	assert ctx.settled is None and ctx.invoice.name == "SINV-1"  # payable: the form
	ctx = render(outstanding=0)
	assert (ctx.settled, ctx.invoice, ctx.settled_invoice) == ("paid", None, "SINV-1")
	ctx = render(outstanding=-15)  # overpaid is paid
	assert (ctx.settled, ctx.invoice) == ("paid", None)
	# "Paid" is ERPNext's word: a zero balance from a credit note was credited, not paid.
	for status in ("Credit Note Issued", "Unpaid", "Overdue"):
		ctx = render(outstanding=0, status=status)
		assert (ctx.settled, ctx.invoice, ctx.settled_invoice) == ("nothing", None, "SINV-1"), status
	ctx = render(block="Processing")
	assert (ctx.settled, ctx.invoice) == ("processing", None)
	# A Paid Stripe row on an invoice that still shows a balance (its Payment Entry canceled,
	# say): still blocked, but "paid" would be untrue, so it says the payment was received.
	ctx = render(block="Paid")
	assert (ctx.settled, ctx.invoice, ctx.settled_invoice) == ("received", None, "SINV-1")

	# A return (credit note) is not the customer's to pay, and its negative outstanding is
	# not "paid": "not available", as before this page learned about settled invoices.
	asked.clear()
	ctx = render(outstanding=-200, is_return=1)
	assert (ctx.settled, ctx.invoice) == (None, None)
	assert asked == []

	# Someone else's invoice: "not available", as ever, and the ledger is never asked.
	ctx = render(customer="CUST-OTHER", outstanding=0, block="Paid")
	assert (ctx.settled, ctx.invoice) == (None, None)
	assert asked == []


def test_pay_card_template_renders_the_settled_states():
	"""Every settled state links back to the invoice list and loads no card-form script."""
	from pathlib import Path

	html = (Path(__file__).resolve().parents[1] / "www" / "pay-card.html").read_text(encoding="utf-8")
	received = html.split('{% elif settled == "received" %}', 1)[1].split("{% elif", 1)[0]
	assert "is paid" not in received and "already received" in received
	nothing = html.split('{% elif settled == "nothing" %}', 1)[1].split("{% elif", 1)[0]
	assert "paid" not in nothing and "nothing left to pay" in nothing
	for state in ("paid", "nothing", "received", "processing"):
		marker = '{% elif settled == "' + state + '" %}'
		assert marker in html, state
		branch = html.split(marker, 1)[1].split("{% elif", 1)[0]
		assert 'href="/pay"' in branch, state
		assert "<script" not in branch, state
	# Ahead of the "not available" branch: a settled render carries no invoice.
	assert html.index('settled == "paid"') < html.index("{% elif not invoice %}")


def test_pay_card_back_and_forward_harness():
	"""scripts/test_web_flow_history.js drives the page's real inline script through Back,
	Forward, a charge in flight and 3-D Secure, over a fake session history."""
	import shutil
	import subprocess
	from pathlib import Path

	import pytest

	node = shutil.which("node")
	if not node:
		pytest.skip("node is not on PATH")
	harness = Path(__file__).resolve().parents[2] / "scripts" / "test_web_flow_history.js"
	result = subprocess.run(
		[node, str(harness), "pay-card"], capture_output=True, text=True, timeout=120, check=False
	)
	assert result.returncode == 0, result.stdout + result.stderr


def test_pay_card_never_uses_the_website_frappe_call():
	"""The website build of ``frappe.call`` (frappe v16, ``website/js/website.js``) calls back on
	an HTTP 200 only and never calls ``error()``. /pay-card relied on ``error()`` for every
	refusal, so a declined card — a frappe.throw, HTTP 417 — left Pay on "Please wait…" with Back
	held by the charge, and nothing said; the harness faked frappe.call *with* an error() callback
	and passed. The page reaches the server over fetch instead, at frappe's REST route with the
	session's CSRF token, and both calls go through the one helper that answers every outcome.
	Runs without node, so it holds even where the harness is skipped."""
	import re
	from pathlib import Path

	html = (Path(__file__).resolve().parents[1] / "www" / "pay-card.html").read_text(encoding="utf-8")
	blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
	script = next(block for block in blocks if "frappe.csrf_token" in block)
	code = re.sub(r"(?m)(^|[^:\"'])//.*$", r"\1", re.sub(r"/\*.*?\*/", "", script, flags=re.S))
	assert not re.search(r"\bfrappe\s*\.\s*x?call\b", code), "pay-card.html must not use frappe.call"
	assert 'fetch("/api/method/" + method' in code
	assert '"X-Frappe-CSRF-Token": frappe.csrf_token' in code
	assert 'credentials: "same-origin"' in code
	assert code.count("rpc(") == 3, "one helper, and exactly the two calls (Continue, Pay) through it"
	# Pay charges the quote its review was drawn for, and says so when there is none.
	assert "const charge = shown;" in code and 'id="review-error"' in html
	# Both places a refusal is written are announced to a screen reader as it is written.
	for element in ("card-error", "review-error"):
		assert re.search(rf'<div id="{element}"[^>]*\brole="alert"', html), f"#{element} must be role=alert"


def test_pay_buttons_are_given_back_after_any_refusal():
	"""/pay still uses the website frappe.call, which never calls ``error()``: its Bank, Set up
	autopay and Cancel autopay buttons re-enabled themselves only there, so any refusal, 5xx or
	dropped connection left them disabled until a reload. Each call now gives its button back in
	``always`` (which the website frappe.call does run, for every outcome) unless it succeeded."""
	import re
	from pathlib import Path

	html = (Path(__file__).resolve().parents[1] / "www" / "pay.html").read_text(encoding="utf-8")
	script = html.split("<script>", 1)[1]
	calls = re.findall(r"frappe\.call\(\{(.*?)\n\t\t\t\}\);", script, re.S)
	assert len(calls) == 3, "the Bank, Set up autopay and Cancel autopay calls"
	for call in calls:
		assert "always: function (data)" in call and "succeeded(data)" in call, call[:80]
		assert "error:" not in call, "the website frappe.call never calls error()"
	assert "return !!(data && !data.exc && !data.exc_type && data.message);" in script


# --- the bank path (hosted Checkout) and /pay run the same guard --------------


def _hosted_checkout(monkeypatch, writes):
	"""Fake everything ``checkout.create_payment`` touches after its guard.

	Each step records ``(step, PaymentIntents canceled so far)`` in ``writes["steps"]``, so
	a test sees both whether anything was created and whether a release came first; it is
	also placed in the ordered ``writes["events"]``, beside the ledger's reads and locks.
	"""
	import frappe as frappe_stub

	from erpnext_enhancements.stripe_payments.core import checkout

	writes["steps"] = []

	def step(name):
		writes["steps"].append((name, list(writes["cancels"])))
		writes["events"].append(("step", name))

	settings = types.SimpleNamespace(
		enabled=1,
		enable_card=1,
		enable_ach=1,
		surcharge_enabled=0,
		surcharge_label=None,
		surcharge_disclosure=None,
		statement_descriptor=None,
		success_route=None,
		cancel_route=None,
		company="Sapphire Fountains",
	)

	def resolve(sales_invoice, customer, amount, description, settings):
		if sales_invoice:
			return "CUST-1", 200.0, "USD", f"Invoice {sales_invoice}"
		return customer, amount, "USD", description or "Payment"

	def get_doc(values, *args, **kwargs):
		step("ledger row")
		sp = _fake_sp(status=values["status"], sales_invoice=values.get("sales_invoice"))
		sp.insert = lambda **kw: sp
		return sp

	monkeypatch.setattr(checkout, "get_settings", lambda: settings)
	monkeypatch.setattr(checkout, "is_enabled", lambda s=None: True)
	monkeypatch.setattr(checkout, "_resolve_target", resolve)
	monkeypatch.setattr(
		checkout, "ensure_stripe_customer", lambda customer, s=None: step("stripe customer") or "cus_1"
	)
	monkeypatch.setattr(
		checkout,
		"create_checkout_session",
		lambda params, idempotency_key=None: step("checkout session")
		or {"id": "cs_1", "url": "https://checkout.stripe.com/c/cs_1"},
	)
	monkeypatch.setattr(
		checkout, "_stamp_invoice", lambda inv, status, *a: writes["stamps"].append((inv, status))
	)
	monkeypatch.setattr(frappe_stub, "get_doc", get_doc, raising=False)
	return checkout


def test_a_bank_checkout_cannot_start_while_a_card_payment_settles(monkeypatch):
	"""The review's sequence: /pay loaded in one tab; the invoice paid by card in another,
	3-D Secure done and the row Processing until the webhook lands; then Bank in the first
	tab. Hosted Checkout used to check only the outstanding amount, so the payer could go on
	to complete an ACH debit for an invoice the card had already paid: two payments. The
	desk's "Pay with Stripe" link is the same function and is refused the same way."""
	for pi_status in ("succeeded", "processing"):
		card_element, writes = _ledger(monkeypatch, [_card_attempt()], pi_status={"pi_old": pi_status})
		checkout = _hosted_checkout(monkeypatch, writes)
		for channel, method in (("Portal", "ach"), ("Portal", None), ("Desk", None)):
			message = _refusal(
				checkout.create_payment, sales_invoice="SINV-1", channel=channel, method=method
			)
			assert message == card_element.MSG_IN_FLIGHT, (pi_status, channel, method)
		# Refused before anything exists at Stripe or on the ledger, and nothing canceled.
		assert writes["steps"] == [], pi_status
		assert writes["cancels"] == [] and writes["set_value"] == [] and writes["stamps"] == [], pi_status

	# A Stripe payment already received on an invoice that still shows a balance: refused
	# too — worded for the customer on the portal, and for Accounts on the desk.
	card_element, writes = _ledger(monkeypatch, [_card_attempt(status="Paid")])
	checkout = _hosted_checkout(monkeypatch, writes)
	assert (
		_refusal(checkout.create_payment, sales_invoice="SINV-1", channel="Portal", method="ach")
		== card_element.MSG_ALREADY_RECEIVED
	)
	desk = _refusal(checkout.create_payment, sales_invoice="SINV-1", channel="Desk")
	assert desk == card_element.MSG_ALREADY_RECEIVED_DESK and "contact us" not in desk
	assert writes["steps"] == []

	# An ad hoc payment has no invoice to guard: only the customer's open payments are read
	# (test_the_desk_ad_hoc_link_waits_for_payments_in_flight), and a Paid one is not open.
	card_element, writes = _ledger(monkeypatch, [_card_attempt(status="Paid")])
	checkout = _hosted_checkout(monkeypatch, writes)
	result = checkout.create_payment(customer="CUST-1", amount=50, channel="Desk")
	assert result["checkout_url"] and writes["filters"] == [
		("Stripe Payment", {"customer": "CUST-1", "status": ["in", ["Processing", "Link Sent"]]})
	]


def test_a_bank_checkout_releases_an_abandoned_card_attempt_first(monkeypatch):
	"""A card attempt whose 3-D Secure was abandoned does not strand the invoice on the bank
	path either — and its PaymentIntent is canceled at Stripe before the Checkout Session
	exists, so the card attempt and the bank debit can never both charge."""
	card_element, writes = _ledger(
		monkeypatch,
		[_card_attempt()],
		pi_status={"pi_old": "requires_action"},
		cancel={"pi_old": "canceled"},
	)
	checkout = _hosted_checkout(monkeypatch, writes)
	result = checkout.create_payment(sales_invoice="SINV-1", channel="Portal", method="ach")
	assert result["checkout_url"] == "https://checkout.stripe.com/c/cs_1"
	assert writes["cancels"] == ["pi_old"]
	assert [name for name, _ in writes["steps"]] == ["stripe customer", "ledger row", "checkout session"]
	assert all(canceled == ["pi_old"] for _, canceled in writes["steps"])
	assert writes["set_value"][0][:2] == ("Stripe Payment", "SP-OLD")
	assert writes["set_value"][0][2]["status"] == "Failed"
	assert writes["stamps"] == [("SINV-1", "Unpaid"), ("SINV-1", "Link Sent")]

	# The payer finished 3-D Secure in the other tab a moment ago: the cancel is refused and the
	# card attempt reads back succeeded — it charged, it will clear — and no bank Checkout starts.
	card_element, writes = _ledger(monkeypatch, [_card_attempt()], pi_status={"pi_old": "requires_action"})
	_cancel_refused(monkeypatch, card_element, writes, now="succeeded")
	checkout = _hosted_checkout(monkeypatch, writes)
	message = _refusal(checkout.create_payment, sales_invoice="SINV-1", channel="Portal", method="ach")
	assert message == card_element.MSG_IN_FLIGHT
	assert writes["steps"] == [] and writes["set_value"] == [] and writes["stamps"] == []


def test_a_pending_ach_debit_is_waited_on_and_never_canceled(monkeypatch):
	"""An ACH debit waiting on microdeposit verification sits in requires_action for days —
	one of the states that marks an abandoned *card* attempt. It must block every new payment
	for the invoice, bank or card, on every path, and never be looked up or canceled: it
	carries no ConfirmationToken, so nothing releases it."""
	card_element, writes = _ledger(
		monkeypatch,
		[_hosted_payment()],
		pi_status={"pi_ach": "requires_action"},
		cancel={"pi_ach": "canceled"},
	)
	checkout = _hosted_checkout(monkeypatch, writes)
	for channel, method in (("Portal", "ach"), ("Desk", None)):
		message = _refusal(checkout.create_payment, sales_invoice="SINV-1", channel=channel, method=method)
		assert message == card_element.MSG_IN_FLIGHT, channel

	monkeypatch.setattr(card_element, "get_settings", _charge_settings)
	monkeypatch.setattr(card_element, "is_enabled", lambda s=None: True)
	monkeypatch.setattr(
		card_element, "_resolve_target", lambda *a: ("CUST-1", 200.0, "USD", "Invoice SINV-1")
	)
	message = _refusal(card_element.price_card_payment, confirmation_token="ct_new", sales_invoice="SINV-1")
	assert message == card_element.MSG_IN_FLIGHT

	# /pay's list and /pay-card's render read the same verdict.
	assert card_element.invoice_payment_blocks(["SINV-1"]) == {"SINV-1": "Processing"}
	assert card_element.invoice_payment_block("SINV-1") == "Processing"

	assert writes["lookups"] == [] and writes["cancels"] == []
	assert writes["set_value"] == [] and writes["stamps"] == [] and writes["steps"] == []

	# Beside an abandoned card attempt, whichever comes first on the ledger: the debit in
	# flight decides, before Stripe is asked anything, so the card attempt is not released
	# either — nothing new may start, so there is nothing to release it for.
	for rows in (
		[_card_attempt(), _hosted_payment()],
		[_hosted_payment(), _card_attempt()],
	):
		card_element, writes = _ledger(
			monkeypatch,
			rows,
			pi_status={"pi_old": "requires_action", "pi_ach": "requires_action"},
			cancel={"pi_old": "canceled", "pi_ach": "canceled"},
		)
		checkout = _hosted_checkout(monkeypatch, writes)
		message = _refusal(checkout.create_payment, sales_invoice="SINV-1", channel="Portal", method="ach")
		assert message == card_element.MSG_IN_FLIGHT
		assert card_element.invoice_payment_block("SINV-1", release=True) == "Processing"
		assert card_element.invoice_payment_blocks(["SINV-1"]) == {"SINV-1": "Processing"}
		assert writes["lookups"] == [] and writes["cancels"] == [] and writes["set_value"] == []
		assert writes["stamps"] == [] and writes["steps"] == []


def test_invoice_payment_blocks_is_the_same_verdict_for_a_list(monkeypatch):
	"""/pay asks for every invoice it lists at once: one ledger query, Stripe asked only about
	card attempts, and a render never cancels or re-stamps anything."""
	rows = [
		_card_attempt(name="SP-A", status="Paid", invoice="SINV-A"),
		_hosted_payment(name="SP-B", pi="pi_b", invoice="SINV-B"),
		_card_attempt(name="SP-C", pi="pi_c", invoice="SINV-C"),
		_card_attempt(name="SP-D", pi="pi_d", invoice="SINV-D"),
		# One dead attempt and one still settling on the same invoice: settling wins.
		_card_attempt(name="SP-F1", pi="pi_f1", invoice="SINV-F"),
		_card_attempt(name="SP-F2", pi="pi_f2", invoice="SINV-F"),
		# Last: after a failed lookup the list asks Stripe nothing more (see
		# test_page_renders_never_wait_long_on_stripe).
		_card_attempt(name="SP-E", pi="pi_e", invoice="SINV-E"),
	]
	card_element, writes = _ledger(
		monkeypatch,
		rows,
		pi_status={
			"pi_b": "requires_action",
			"pi_c": "requires_action",  # 3-D Secure untouched for hours: payable
			"pi_d": "succeeded",  # the webhook has not posted it yet
			"pi_f1": "canceled",
			"pi_f2": "processing",
			# pi_e is missing: the lookup fails, which proves nothing
		},
		cancel={"pi_c": "canceled", "pi_f1": "canceled"},
	)
	# SINV-E last: once its lookup fails the list asks Stripe nothing more (in page order).
	names = ["SINV-A", "SINV-B", "SINV-C", "SINV-D", "SINV-F", "SINV-G", "SINV-E"]
	assert card_element.invoice_payment_blocks(names) == {
		"SINV-A": "Paid",
		"SINV-B": "Processing",
		"SINV-D": "Processing",
		"SINV-E": "Unconfirmed",
		"SINV-F": "Processing",
	}
	# One query for which listed invoices are amendments (none here), one for the ledger.
	assert writes["filters"] == [
		("Sales Invoice", {"name": ["in", names], "amended_from": ["is", "set"]}),
		("Stripe Payment", {"sales_invoice": ["in", names], "status": ["in", ["Processing", "Paid"]]}),
	]
	# Each card attempt once; the paid invoice and the bank debit need no question to Stripe.
	assert sorted(writes["lookups"]) == ["pi_c", "pi_d", "pi_e", "pi_f1", "pi_f2"]
	assert writes["cancels"] == [] and writes["set_value"] == [] and writes["stamps"] == []
	assert writes["commits"] == 0

	card_element, writes = _ledger(monkeypatch, rows)
	assert card_element.invoice_payment_blocks([]) == {} and card_element.invoice_payment_blocks(None) == {}
	assert writes["filters"] == []


def _load_www_page(filename, module_name):
	import importlib.util
	from pathlib import Path

	path = Path(__file__).resolve().parents[1] / "www" / filename
	spec = importlib.util.spec_from_file_location(module_name, path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def test_every_path_that_starts_a_payment_runs_the_guard_exactly_once(monkeypatch):
	"""Each request that can start a payment for an invoice consults
	``invoice_payment_block`` once, releasing; each page render once, never releasing; an ad
	hoc payment never. Twice would cancel and re-check in one request for no gain; never is
	the gap the hosted path had."""
	card_element, writes = _ledger(monkeypatch, [])
	_hosted_checkout(monkeypatch, writes)
	import frappe as frappe_stub
	import frappe.utils as frappe_utils

	from erpnext_enhancements.stripe_payments.core import api

	calls = []
	monkeypatch.setattr(
		card_element,
		"invoice_payment_block",
		lambda inv, exclude=None, release=False: calls.append((inv, exclude, release)),
	)
	monkeypatch.setattr(
		card_element, "invoice_payment_blocks", lambda names: calls.append(("list", list(names))) or {}
	)

	invoice = _invoice()

	def get_value(doctype, name=None, fieldname=None, *args, **kwargs):
		if kwargs.get("as_dict"):
			return invoice
		return {"customer": "CUST-1", "outstanding_amount": 200}.get(fieldname)

	quoted = _payable_sp()
	quoted.name = "SP-Q"

	def get_doc(doctype_or_values, name=None, *args, **kwargs):
		if isinstance(doctype_or_values, dict):
			sp = _fake_sp(status="Draft", sales_invoice=doctype_or_values.get("sales_invoice"))
			sp.insert = lambda **kw: sp
			sp.reload = lambda: None
			return sp
		return quoted

	frappe_stub.db.get_value = get_value
	frappe_stub.db.exists = lambda *a, **k: True
	monkeypatch.setattr(frappe_stub, "get_doc", get_doc, raising=False)
	monkeypatch.setattr(api, "get_portal_customers", lambda user=None: ["CUST-1"])
	monkeypatch.setattr(card_element, "get_settings", _charge_settings)
	monkeypatch.setattr(card_element, "is_enabled", lambda s=None: True)
	monkeypatch.setattr(
		card_element, "_resolve_target", lambda *a: ("CUST-1", 200.0, "USD", "Invoice SINV-1")
	)
	monkeypatch.setattr(card_element, "_confirmation_token_method", lambda token: ("card", "debit"))
	monkeypatch.setattr(card_element, "_compute_surcharge", lambda *a, **k: 0.0)
	monkeypatch.setattr(card_element, "ensure_stripe_customer", lambda customer, s=None: "cus_1")
	monkeypatch.setattr(
		card_element,
		"create_payment_intent",
		lambda *a, **k: {"id": "pi_q", "status": "requires_action", "client_secret": "s"},
	)

	_off_session(monkeypatch, writes, {"id": "pi_off", "status": "processing"})

	def once(label, call, expected):
		calls.clear()
		call()
		assert calls == expected, (label, calls)

	# The POSTs: desk link, portal Bank, card quote, card charge, desk off-session charge.
	once("desk link", lambda: api.create_invoice_payment("SINV-1"), [("SINV-1", None, True)])
	once("portal bank", lambda: api.portal_create_payment("SINV-1", method="ach"), [("SINV-1", None, True)])
	once(
		"card quote",
		lambda: api.portal_price_card_payment("SINV-1", "ct_new"),
		[("SINV-1", None, True)],
	)
	once(
		"card charge",
		lambda: api.portal_confirm_card_payment("SP-Q", "ct_new"),
		[("SINV-1", "SP-Q", True)],
	)
	once(
		"desk off-session charge",
		lambda: api.charge_saved_method("CUST-1", sales_invoice="SINV-1"),
		[("SINV-1", None, True)],
	)
	once("ad hoc", lambda: api.create_adhoc_payment("CUST-1", 50), [])
	once("ad hoc off-session charge", lambda: api.charge_saved_method("CUST-1", amount=50), [])

	# The page renders: read-only.
	monkeypatch.setattr(frappe_stub, "session", types.SimpleNamespace(user="jane@example.com"))
	monkeypatch.setattr(
		frappe_stub, "sessions", types.SimpleNamespace(get_csrf_token=lambda: "tok"), raising=False
	)
	monkeypatch.setattr(frappe_stub, "form_dict", {"invoice": "SINV-1"}, raising=False)
	monkeypatch.setattr(frappe_utils, "formatdate", lambda value=None, *a, **k: str(value), raising=False)
	pages = {
		"pay_card": _load_www_page("pay_card.py", "ee_test_pay_card_page_once"),
		"pay": _load_www_page("pay.py", "ee_test_pay_page_once"),
	}
	settings = types.SimpleNamespace(
		enabled=1, enable_card=1, enable_ach=1, publishable_key="pk_test_x", company="Sapphire Fountains"
	)
	for page in pages.values():
		monkeypatch.setattr(page, "get_settings", lambda: settings)
		monkeypatch.setattr(page, "is_enabled", lambda s=None: True)
		monkeypatch.setattr(page, "get_portal_customers", lambda: ["CUST-1"])
	monkeypatch.setattr(pages["pay"], "autopay_consent_text", lambda s: "")
	monkeypatch.setattr(
		frappe_stub,
		"get_all",
		lambda doctype, **kwargs: [
			{
				"name": name,
				"posting_date": None,
				"due_date": None,
				"outstanding_amount": 200,
				"currency": "USD",
			}
			for name in ("SINV-1", "SINV-2")
		],
		raising=False,
	)
	once(
		"/pay-card render",
		lambda: pages["pay_card"].get_context(types.SimpleNamespace()),
		[("SINV-1", None, False)],
	)
	once(
		"/pay render",
		lambda: pages["pay"].get_context(types.SimpleNamespace()),
		[("list", ["SINV-1", "SINV-2"])],
	)


def test_pay_page_offers_what_the_endpoints_accept_not_what_the_stamp_says(monkeypatch):
	"""/pay hid Card and Bank whenever the invoice's stamp said Processing — for good after
	an abandoned or failed 3-D Secure, since nothing un-stamps those — and otherwise offered
	both, even for an invoice with a Stripe payment already received. It now shows exactly
	what the endpoints behind the buttons accept: the verdict of invoice_payment_block."""
	from pathlib import Path

	import pytest

	jinja2 = pytest.importorskip("jinja2")  # CI installs it
	frappe_stub = install_frappe_stub()
	import frappe.utils as frappe_utils

	monkeypatch.setattr(frappe_utils, "formatdate", lambda value=None, *a, **k: str(value), raising=False)
	page = _load_www_page("pay.py", "ee_test_pay_page")
	settings = types.SimpleNamespace(enabled=1, enable_card=1, enable_ach=1, company="Sapphire Fountains")
	monkeypatch.setattr(page, "get_settings", lambda: settings)
	monkeypatch.setattr(page, "is_enabled", lambda s=None: True)
	monkeypatch.setattr(page, "get_portal_customers", lambda: ["CUST-1"])
	monkeypatch.setattr(page, "autopay_consent_text", lambda s: "")
	monkeypatch.setattr(frappe_stub, "session", types.SimpleNamespace(user="jane@example.com"))
	monkeypatch.setattr(
		frappe_stub, "sessions", types.SimpleNamespace(get_csrf_token=lambda: "tok"), raising=False
	)
	listed = [
		# 3-D Secure abandoned: the stamp still says Processing, the verdict says payable.
		("SINV-ABANDONED", "Processing", None),
		("SINV-SETTLING", "Processing", "Processing"),
		# A bank debit settling on an invoice whose stamp says nothing of it.
		("SINV-ACH", None, "Processing"),
		("SINV-RECEIVED", "Paid", "Paid"),
		("SINV-OPEN", None, None),
	]
	asked = {}

	def get_all(doctype, **kwargs):
		asked["fields"] = kwargs.get("fields")
		# No stamp on the rows: the page no longer asks for it.
		return [
			{
				"name": name,
				"posting_date": None,
				"due_date": None,
				"outstanding_amount": 200,
				"currency": "USD",
			}
			for name, _stamp, _verdict in listed
		]

	monkeypatch.setattr(frappe_stub, "get_all", get_all, raising=False)
	monkeypatch.setattr(
		page,
		"invoice_payment_blocks",
		lambda names: {name: verdict for name, _stamp, verdict in listed if verdict and name in names},
	)
	context = page.get_context(types.SimpleNamespace())
	assert "custom_stripe_payment_status" not in asked["fields"]
	assert {inv["name"]: inv["payment_block"] for inv in context.invoices} == {
		name: verdict for name, _stamp, verdict in listed
	}

	source = (Path(__file__).resolve().parents[1] / "www" / "pay.html").read_text(encoding="utf-8")
	assert "custom_stripe_payment_status" not in source
	env = jinja2.Environment(
		loader=jinja2.DictLoader(
			{"templates/web.html": "{% block page_content %}{% endblock %}", "pay.html": source}
		)
	)
	env.globals["_"] = lambda text, *a, **k: text
	html = env.get_template("pay.html").render(
		enabled=True,
		invoices=[
			# Rendered with the stale stamp put back on each row: it must change nothing.
			dict(inv, custom_stripe_payment_status=stamp)
			for inv, (_name, stamp, _verdict) in zip(context.invoices, listed, strict=True)
		],
		enable_card=True,
		enable_ach=True,
		autopay_consent="",
		autopay_enrolled=False,
		csrf_token="tok",
	)
	rows = {}
	for chunk in html.split("<tr>")[2:]:  # past the header row
		# The Invoice cell: the name, then its "View invoice (PDF)" link beneath it.
		name = chunk.split("<td>", 1)[1].split("<", 1)[0].strip()
		rows[name] = chunk.split("</tr>", 1)[0]

	def offers(name):
		row = rows[name]
		card = f"/pay-card?invoice={name}" in row
		bank = f'data-invoice="{name}"' in row
		assert card == bank, name
		return card

	assert offers("SINV-ABANDONED") and offers("SINV-OPEN")
	for name in ("SINV-SETTLING", "SINV-ACH"):
		assert not offers(name) and "Processing…" in rows[name], name
	assert not offers("SINV-RECEIVED")
	assert "Payment received" in rows["SINV-RECEIVED"] and "Processing" not in rows["SINV-RECEIVED"]


# --- canceling an invoice: cancel-and-amend must not get past the guard ------------------
#
# Frappe blocks a cancel only for *submitted* linked documents, and a Stripe Payment is never
# submitted. An invoice canceled while a payment for it was in flight came back amended under a
# new name that no rule keyed on, and autopay charged the copy again on submit.


def _cancel(card_element, name="SINV-1"):
	"""The Sales Invoice ``before_cancel`` doc_event, as Frappe calls it."""
	return card_element.before_invoice_cancel(types.SimpleNamespace(name=name), "before_cancel")


def _enabled(monkeypatch, card_element):
	monkeypatch.setattr(card_element, "is_enabled", lambda s=None: True)


def _amended_pair():
	"""SINV-1 canceled and amended to SINV-1-1."""
	return {
		"SINV-1-1": _invoice(name="SINV-1-1", amended_from="SINV-1"),
		"SINV-1": _invoice(name="SINV-1", docstatus=2),
	}


def test_an_invoice_is_never_canceled_while_a_stripe_payment_is_in_flight(monkeypatch):
	"""An ACH debit settling for days, a charged card whose Payment Entry could not post, 3-D
	Secure under way, a charge nobody has an answer for: the cancel is refused, with why. The
	ledger is read with a locking read (the latest committed rows, not the cancel's old snapshot)
	and nothing is committed inside the cancel's transaction."""
	card_element, writes = _ledger(monkeypatch, [_hosted_payment()], pi_status={"pi_ach": "processing"})
	_enabled(monkeypatch, card_element)
	exc = _raised(_cancel, card_element=card_element)
	assert isinstance(exc, card_element.PaymentBlocked)
	assert "Stripe Payment SP-ACH" in str(exc) and card_element.CANCEL_DETAIL_PROCESSING in str(exc)
	assert writes["filters"] == [
		("Stripe Payment", {"sales_invoice": "SINV-1", "status": "Processing"}, "for update")
	]
	assert writes["commits"] == 0 and writes["lookups"] == [] and writes["cancels"] == []

	# A card charged, its Payment Entry not posted (a closed period): Processing, succeeded.
	card_element, writes = _ledger(monkeypatch, [_card_attempt()], pi_status={"pi_old": "succeeded"})
	_enabled(monkeypatch, card_element)
	assert card_element.CANCEL_DETAIL_PROCESSING in str(_raised(_cancel, card_element=card_element))

	# 3-D Secure the customer may still be approving: refused, and says for how long.
	card_element, writes = _ledger(
		monkeypatch, [_card_attempt(modified=FRESH)], pi_status={"pi_old": "requires_action"}
	)
	_enabled(monkeypatch, card_element)
	message = str(_raised(_cancel, card_element=card_element))
	assert card_element.CANCEL_DETAIL_AWAITING.format(minutes="25 minutes") in message
	assert writes["cancels"] == []

	# A charge Stripe never answered.
	card_element, writes = _ledger(monkeypatch, [_card_attempt(pi=None)])
	_enabled(monkeypatch, card_element)
	assert card_element.CANCEL_DETAIL_UNCONFIRMED in str(_raised(_cancel, card_element=card_element))

	# A card attempt that can no longer charge is released as every POST releases one — canceled
	# at Stripe, the row failed — but inside the cancel's transaction: nothing committed.
	card_element, writes = _ledger(
		monkeypatch,
		[_card_attempt()],
		pi_status={"pi_old": "requires_action"},
		cancel={"pi_old": "canceled"},
	)
	_enabled(monkeypatch, card_element)
	assert _cancel(card_element) is None
	assert writes["cancels"] == ["pi_old"] and writes["store"]["SP-OLD"]["status"] == "Failed"
	assert writes["commits"] == 0

	# A Paid row does not refuse the cancel: ERPNext's own linked-Payment-Entry check governs
	# that, and the amended copy's guard reads the original's rows.
	card_element, writes = _ledger(monkeypatch, [_card_attempt(status="Paid")])
	_enabled(monkeypatch, card_element)
	assert _cancel(card_element) is None and writes["commits"] == 0

	# Off, or not installed yet (ERPNext's own test bootstrap cancels invoices): a no-op.
	card_element, writes = _ledger(monkeypatch, [_hosted_payment()])
	monkeypatch.setattr(card_element, "is_enabled", lambda s=None: False)
	assert _cancel(card_element) is None and writes["filters"] == []

	def not_installed(settings=None):
		raise Exception("DocType Stripe Payments Settings not found")

	monkeypatch.setattr(card_element, "is_enabled", not_installed)
	assert _cancel(card_element) is None and writes["filters"] == []


def test_canceling_an_invoice_expires_its_emailed_links_first(monkeypatch):
	"""A Link Sent Checkout Session outlives its invoice by up to 24 hours. It is expired at
	Stripe before the cancel (inside the cancel's transaction); a link the customer has just
	completed refuses the cancel; one Stripe cannot be asked about refuses it too; one Stripe
	does not have (404) is marked Expired, Accounts are told, and the cancel goes on."""
	card_element, writes = _ledger(monkeypatch, [_link_sent()])
	_enabled(monkeypatch, card_element)
	_checkout_sessions(monkeypatch, card_element, writes, {"cs_link": "open"}, expire={"cs_link": "expired"})
	assert _cancel(card_element) is None
	row = writes["store"]["SP-LINK"]
	assert row["status"] == "Expired" and row["error_message"] == card_element.NOTE_LINK_EXPIRED_CANCEL
	assert writes["expires"] == [("cs_link", card_element.CONTROL_TIMEOUT)] and writes["commits"] == 0
	assert ("Stripe Payment", {"sales_invoice": "SINV-1", "status": "Link Sent"}, "for update") in writes[
		"filters"
	]

	card_element, writes = _ledger(monkeypatch, [_link_sent()])
	_enabled(monkeypatch, card_element)
	_checkout_sessions(monkeypatch, card_element, writes, {"cs_link": "complete"}, expire={})
	exc = _raised(_cancel, card_element=card_element)
	assert isinstance(exc, card_element.PaymentBlocked) and str(exc) == card_element.MSG_CANCEL_LINK_USED
	assert writes["commits"] == 0

	card_element, writes = _ledger(monkeypatch, [_link_sent()])
	_enabled(monkeypatch, card_element)
	_checkout_sessions(monkeypatch, card_element, writes, {}, expire={})
	exc = _raised(_cancel, card_element=card_element)
	assert isinstance(exc, card_element.LinkStillOpen) and str(exc) == card_element.MSG_LINK_STILL_OPEN_DESK

	card_element, writes = _ledger(monkeypatch, [_link_sent()])
	_enabled(monkeypatch, card_element)
	gone = _stripe_error(404, "Stripe API error (404): No such checkout.session: 'cs_link'")
	monkeypatch.setattr(card_element, "expire_checkout_session", lambda sid, timeout=None: (_ for _ in ()).throw(gone))
	monkeypatch.setattr(
		card_element, "retrieve_checkout_session", lambda sid, expand=None, timeout=None: (_ for _ in ()).throw(gone)
	)
	assert _cancel(card_element) is None
	assert writes["store"]["SP-LINK"]["status"] == "Expired"
	assert [docname for _, docname in writes["accounts_alerts"]] == ["SP-LINK"]


def test_the_cancel_hook_is_registered_and_resolves():
	"""hooks.py wires it, on the one Sales Invoice doc_events entry, to a function that exists."""
	import ast
	import importlib
	from pathlib import Path

	install_frappe_stub()
	tree = ast.parse((Path(__file__).resolve().parents[1] / "hooks.py").read_text(encoding="utf-8"))
	doc_events = next(
		node.value
		for node in tree.body
		if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "doc_events"
	)
	(sales_invoice,) = [
		value for key, value in zip(doc_events.keys, doc_events.values, strict=True) if key.value == "Sales Invoice"
	]
	events = ast.literal_eval(sales_invoice)
	assert events["before_cancel"] == "erpnext_enhancements.stripe_payments.core.card_element.before_invoice_cancel"
	module, _, name = events["before_cancel"].rpartition(".")
	assert callable(getattr(importlib.import_module(module), name))


def test_an_amended_invoice_is_guarded_by_the_payments_of_the_one_it_replaced(monkeypatch):
	"""The review's sequence (a): autopay's ACH debit for SINV-1 settles for four days; Accounts
	cancel SINV-1 and amend it to SINV-1-1. On submit, autopay would charge SINV-1-1 again. Every
	rule reads the original's rows: the copy's guard, the charge, the /pay list and autopay."""
	rows = [_hosted_payment(invoice="SINV-1")]
	card_element, writes = _ledger(monkeypatch, rows, invoices=_amended_pair())
	assert card_element.invoice_payment_block("SINV-1-1") == "Processing"
	assert writes["filters"] == [
		(
			"Stripe Payment",
			{"sales_invoice": ["in", ["SINV-1-1", "SINV-1"]], "status": ["in", ["Processing", "Paid"]]},
		)
	]
	assert card_element.invoice_payment_blocks(["SINV-1-1", "SINV-9"]) == {"SINV-1-1": "Processing"}

	saved_methods = _off_session(monkeypatch, writes, {"id": "pi_twice", "status": "succeeded"})
	exc = _raised(saved_methods.charge_saved_method, customer="CUST-1", sales_invoice="SINV-1-1", channel="Auto")
	assert isinstance(exc, card_element.PaymentBlocked) and str(exc) == card_element.MSG_IN_FLIGHT
	assert writes["charges"] == [] and not [e for e in writes["events"] if e[0] == "insert"]

	# Autopay on submit does not even enqueue the doomed charge.
	import frappe as frappe_stub

	base = frappe_stub.db.get_value
	enrolled = types.SimpleNamespace(custom_stripe_autopay_enabled=1, custom_stripe_default_payment_method="pm_1")
	frappe_stub.db.get_value = lambda doctype, *a, **k: enrolled if doctype == "Customer" else base(doctype, *a, **k)
	monkeypatch.setattr(saved_methods, "is_enabled", lambda s=None: True)
	enqueued = []
	monkeypatch.setattr(frappe_stub, "enqueue", lambda *a, **k: enqueued.append(k), raising=False)
	copy = types.SimpleNamespace(
		name="SINV-1-1", customer="CUST-1", outstanding_amount=200.0, posting_date="2026-06-18", amended_from="SINV-1"
	)
	copy.get = lambda key, default=None: getattr(copy, key, default)
	saved_methods.auto_charge_on_invoice_submit(copy)
	assert enqueued == []
	# ...and with nothing on the original, it does.
	writes["store"]["SP-ACH"]["status"] = "Failed"
	saved_methods.auto_charge_on_invoice_submit(copy)
	assert len(enqueued) == 1

	# A payment received on the original blocks the copy as "received — contact us": Accounts
	# allocate that money, never a second charge.
	card_element, writes = _ledger(
		monkeypatch, [_card_attempt(status="Paid", invoice="SINV-1")], invoices=_amended_pair()
	)
	monkeypatch.setattr(card_element, "get_settings", _charge_settings)
	monkeypatch.setattr(card_element, "is_enabled", lambda s=None: True)
	monkeypatch.setattr(card_element, "_resolve_target", lambda *a: ("CUST-1", 200.0, "USD", "Invoice SINV-1-1"))
	message = _refusal(card_element.price_card_payment, confirmation_token="ct_new", sales_invoice="SINV-1-1")
	assert message == card_element.MSG_ALREADY_RECEIVED

	# The sweep's own SQL skips an amended invoice whose original is still being paid.
	from pathlib import Path

	tasks_source = (Path(__file__).resolve().parents[1] / "stripe_payments" / "core" / "tasks.py").read_text(
		encoding="utf-8"
	)
	assert "sp.sales_invoice = si.amended_from" in tasks_source


def test_an_emailed_link_on_the_replaced_invoice_is_expired_before_the_copy_is_charged(monkeypatch):
	"""The review's sequence (b): a link emailed for SINV-1, which is then canceled and amended to
	SINV-1-1; the customer pays SINV-1-1 by card. The original's link is expired first, so it can
	never become a second payment."""
	card_element, writes = _card_page(
		monkeypatch,
		[_link_sent(invoice="SINV-1"), _quote(sales_invoice="SINV-1-1", description="Invoice SINV-1-1")],
		{"id": "pi_new", "status": "requires_action", "client_secret": "s"},
		invoice=_invoice(name="SINV-1-1", amended_from="SINV-1"),
	)
	writes_invoices = _amended_pair()
	import frappe as frappe_stub

	base = frappe_stub.db.get_value

	def get_value(doctype, name=None, fieldname=None, *a, **k):
		if doctype == "Sales Invoice" and name in writes_invoices and fieldname == "amended_from":
			return writes_invoices[name].amended_from if hasattr(writes_invoices[name], "amended_from") else None
		return base(doctype, name, fieldname, *a, **k)

	frappe_stub.db.get_value = get_value
	_checkout_sessions(monkeypatch, card_element, writes, {"cs_link": "open"}, expire={"cs_link": "expired"})
	result = card_element.confirm_card_payment(stripe_payment="SP-Q", confirmation_token="ct_new")
	assert result["requires_action"] is True
	assert writes["store"]["SP-LINK"]["status"] == "Expired"
	events = writes["events"]
	assert _index(events, "expire") < _index(events, "charge")


def test_the_invoice_row_lock_is_the_last_read_before_a_payment_is_committed(monkeypatch):
	"""Each path that commits a payment in flight takes the Sales Invoice row lock (SELECT … FOR
	UPDATE) as its last read before that commit, with no commit in between — the lock a cancel
	holds from its first write to its commit. So a payment that waits on a cancel then reads the
	invoice canceled, and is refused with nothing charged or written."""

	def last_read_is_locked(events, written):
		locked = max(i for i, e in enumerate(events) if e == ("read", "Sales Invoice", "for update"))
		assert locked < written
		assert ("commit",) not in events[locked:written]

	# The card charge.
	card_element, writes = _card_page(monkeypatch, [_link_sent(), _quote()])
	_checkout_sessions(monkeypatch, card_element, writes, {"cs_link": "open"}, expire={"cs_link": "expired"})
	card_element.confirm_card_payment(stripe_payment="SP-Q", confirmation_token="ct_new")
	events = writes["events"]
	last_read_is_locked(events, _index(events, ("set", "SP-Q", "Processing")))
	assert _index(events, "expire") < _index(events, ("read", "Sales Invoice", "for update"))

	import frappe as frappe_stub

	def canceled_meanwhile():
		base = frappe_stub.db.get_value

		def get_value(doctype, name=None, fieldname=None, *a, **k):
			if doctype == "Sales Invoice" and k.get("for_update"):
				return _invoice(docstatus=2)
			return base(doctype, name, fieldname, *a, **k)

		frappe_stub.db.get_value = get_value

	card_element, writes = _card_page(monkeypatch, [_quote()])
	canceled_meanwhile()
	exc = _raised(card_element.confirm_card_payment, stripe_payment="SP-Q", confirmation_token="ct_new")
	assert str(exc) == card_element.MSG_INVOICE_CHANGED
	assert writes["charges"] == [] and writes["store"]["SP-Q"]["status"] == "Draft"

	# The off-session charge: after the saved method's lookup (a Stripe call), before the insert.
	card_element, writes = _ledger(monkeypatch, [])
	saved_methods = _off_session(monkeypatch, writes, {"id": "pi_off", "status": "processing"})
	saved_methods.charge_saved_method(customer="CUST-1", sales_invoice="SINV-1", channel="Auto")
	events = writes["events"]
	last_read_is_locked(events, _index(events, "insert"))

	card_element, writes = _ledger(monkeypatch, [])
	saved_methods = _off_session(monkeypatch, writes, {"id": "pi_off", "status": "processing"})
	canceled_meanwhile()
	exc = _raised(saved_methods.charge_saved_method, customer="CUST-1", sales_invoice="SINV-1", channel="Dunning")
	assert isinstance(exc, card_element.PaymentBlocked)  # dunning reschedules; never a "decline"
	assert writes["charges"] == [] and not [e for e in writes["events"] if e[0] == "insert"]

	# Hosted Checkout: before the ledger row is inserted, held across the Session call to the
	# Link Sent commit — so a cancel never waits on an uncommitted insert while this waits on it.
	card_element, writes = _ledger(monkeypatch, [])
	checkout = _hosted_checkout(monkeypatch, writes)
	checkout.create_payment(sales_invoice="SINV-1", channel="Portal", method="ach")
	events = writes["events"]
	last_read_is_locked(events, _index(events, ("step", "ledger row")))
	assert ("commit",) not in events[_index(events, ("read", "Sales Invoice", "for update")) : _index(
		events, ("step", "checkout session")
	)]

	card_element, writes = _ledger(monkeypatch, [])
	checkout = _hosted_checkout(monkeypatch, writes)
	canceled_meanwhile()
	exc = _raised(checkout.create_payment, sales_invoice="SINV-1", channel="Desk")
	assert isinstance(exc, card_element.PaymentBlocked) and str(exc) == card_element.MSG_INVOICE_CHANGED_DESK
	assert [name for name, _ in writes["steps"]] == ["stripe customer"]


# --- Stripe's 404 is an answer: never "try again" for ever ----------------------------------


def _not_found(kind="checkout.session"):
	return _stripe_error(404, f"Stripe API error (404): No such {kind}")


def test_a_link_stripe_does_not_have_never_blocks_the_invoice(monkeypatch):
	"""Link Sent rows made while the site pointed at Test (or another account), then switched to
	Live: expire and read-back both answer 404. That is Stripe's definite answer, not an outage —
	so the row is marked Expired, Accounts are told, and the card payment goes on. It used to be
	refused as "could not be closed just now" on every attempt, for good."""
	install_frappe_stub()
	for expire_answer in (_not_found(), Exception("Read timed out.")):
		card_element, writes = _card_page(
			monkeypatch, [_link_sent(), _quote()], {"id": "pi_new", "status": "requires_action", "client_secret": "s"}
		)
		monkeypatch.setattr(
			card_element,
			"expire_checkout_session",
			lambda sid, timeout=None, _e=expire_answer: (_ for _ in ()).throw(_e),
		)
		monkeypatch.setattr(
			card_element,
			"retrieve_checkout_session",
			lambda sid, expand=None, timeout=None: (_ for _ in ()).throw(_not_found()),
		)
		result = card_element.confirm_card_payment(stripe_payment="SP-Q", confirmation_token="ct_new")
		assert result["requires_action"] is True and len(writes["charges"]) == 1
		row = writes["store"]["SP-LINK"]
		assert row["status"] == "Expired" and row["error_message"] == card_element.NOTE_MISSING_SESSION
		assert [docname for _, docname in writes["accounts_alerts"]] == ["SP-LINK"]

	# Expire said 404 and the read-back got no answer: still Stripe's definite answer.
	card_element, writes = _card_page(monkeypatch, [_link_sent(), _quote()])
	monkeypatch.setattr(
		card_element, "expire_checkout_session", lambda sid, timeout=None: (_ for _ in ()).throw(_not_found())
	)
	monkeypatch.setattr(
		card_element,
		"retrieve_checkout_session",
		lambda sid, expand=None, timeout=None: (_ for _ in ()).throw(Exception("Read timed out.")),
	)
	card_element.confirm_card_payment(stripe_payment="SP-Q", confirmation_token="ct_new")
	assert writes["store"]["SP-LINK"]["status"] == "Expired"


def test_a_card_attempt_stripe_does_not_have_is_released(monkeypatch):
	"""A card attempt whose PaymentIntent Stripe answers 404 for can never charge through this
	account: the render does not block on it, and the POSTs mark it Expired (nothing to cancel)
	and tell Accounts, instead of reading it as "being processed" for good."""
	install_frappe_stub()
	card_element, writes = _ledger(
		monkeypatch, [_card_attempt()], pi_status={"pi_old": _not_found("payment_intent")}
	)
	assert card_element.invoice_payment_block("SINV-1") is None
	assert writes["set_value"] == [] and writes["accounts_alerts"] == []
	assert card_element.invoice_payment_block("SINV-1", release=True) is None
	assert writes["cancels"] == []
	row = writes["store"]["SP-OLD"]
	assert row["status"] == "Expired" and row["error_message"] == card_element.NOTE_MISSING_PI
	assert [docname for _, docname in writes["accounts_alerts"]] == ["SP-OLD"]
	assert writes["stamps"] == [("SINV-1", "Unpaid")]
	# A 404 is an answer, not an outage: the next lookup in the same request is still made.
	rows = [_card_attempt(name="SP-A", pi="pi_a", invoice="SINV-A"), _card_attempt(name="SP-B", pi="pi_b", invoice="SINV-B")]
	card_element, writes = _ledger(monkeypatch, rows, pi_status={"pi_a": _not_found(), "pi_b": "succeeded"})
	assert card_element.invoice_payment_blocks(["SINV-A", "SINV-B"]) == {"SINV-B": "Processing"}
	assert sorted(writes["lookups"]) == ["pi_a", "pi_b"]


def test_poll_pending_settles_what_stripe_answers_404_for(monkeypatch):
	"""poll_pending used to re-raise on the same rows every hour, for good. A PaymentIntent or a
	Checkout Session Stripe does not have is marked Expired under the invoice lock and Accounts
	are told; a Stripe Customer Stripe does not have proves an unknown charge absent."""
	install_frappe_stub()
	rows = [
		_card_attempt(name="SP-C", pi="pi_c", invoice="SINV-C"),
		dict(_link_sent(name="SP-L", session="cs_l", invoice="SINV-L"), modified=OLD),
		dict(_card_attempt(name="SP-U", pi=None, invoice="SINV-U"), channel="Auto"),
	]
	tasks, card_element, writes = _poll(
		monkeypatch,
		rows,
		pi_status={"pi_c": _not_found("payment_intent")},
		sessions={"cs_l": _not_found()},
		listing=[_not_found("customer")],
	)
	tasks.poll_pending()
	store = writes["store"]
	assert [store[name]["status"] for name in ("SP-C", "SP-L", "SP-U")] == ["Expired"] * 3
	assert store["SP-C"]["error_message"] == card_element.NOTE_MISSING_PI
	assert store["SP-L"]["error_message"] == card_element.NOTE_MISSING_SESSION
	assert store["SP-U"]["error_message"] == card_element.NOTE_NEVER_REACHED
	assert sorted(docname for _, docname in writes["accounts_alerts"]) == ["SP-C", "SP-L", "SP-U"]
	assert writes["errors"] == []  # nothing left to fail every hour
	assert sorted(held for held, _ in writes["locks"]) == sorted(
		card_element._invoice_lock_name(inv) for inv in ("SINV-C", "SINV-L")
	)

	# The next run leaves them alone.
	alerts = len(writes["accounts_alerts"])
	tasks.poll_pending()
	assert len(writes["accounts_alerts"]) == alerts


# --- a charge that never reached Stripe is not a declined card --------------------------------


def test_a_charge_that_never_reached_stripe_is_not_a_declined_card(monkeypatch):
	"""An autopay job killed by a deploy before its Stripe call: poll_pending proves there is no
	PaymentIntent. The row is Expired, not Failed, so dunning never enrols it and never emails the
	customer that a card declined which was never even tried; the customer (autopay) is not
	emailed at all, Accounts are told, and autopay's sweep may charge again."""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import dunning

	auto = dict(_card_attempt(name="SP-A", pi=None, modified=NOW - _dt.timedelta(minutes=20)), channel="Auto")
	tasks, card_element, writes = _poll(monkeypatch, [auto])
	tasks.poll_pending()
	assert writes["store"]["SP-A"]["status"] == "Expired"
	assert writes["emails"] == [] and [d for _, d in writes["accounts_alerts"]] == ["SP-A"]

	import frappe as frappe_stub

	today = _dt.date(2026, 9, 24)
	assert dunning._discover_new_failures(today) == []
	# The contrast: a real decline is enrolled.
	writes["store"]["SP-A"]["status"] = "Failed"
	monkeypatch.setattr(
		frappe_stub,
		"get_all",
		lambda doctype, **kw: ["SINV-1"]
		if doctype == "Sales Invoice"
		else [
			row["sales_invoice"]
			for row in writes["store"].values()
			if _matches(row, kw.get("filters"))
		],
		raising=False,
	)
	assert dunning._discover_new_failures(today) == ["SINV-1"]

	# autopay's sweep does not count an Expired row as the invoice being handled.
	from pathlib import Path

	source = (Path(__file__).resolve().parents[1] / "stripe_payments" / "core" / "tasks.py").read_text(
		encoding="utf-8"
	)
	excluded = source.split("sp.sales_invoice = si.name", 1)[1].split(")", 1)[0]
	assert "'Failed'" in excluded and "'Expired'" not in excluded


# --- the desk's "Charge Saved Method" --------------------------------------------------------


def test_the_desk_charge_is_for_an_invoice_and_an_ad_hoc_one_waits_for_payments_in_flight(monkeypatch):
	"""The Customer form's button sent only an amount, so every desk charge took the ad hoc path —
	no invoice lock, no guard, no link expired — and a $500 "charge" beside a settling $500 ACH
	debit charged the customer twice. The prompt now offers the customer's outstanding invoices
	(the guarded path); a bare amount is refused while the customer has a payment settling or a
	link open, and an invoice must be the customer's own."""
	for open_row in (_hosted_payment(), _link_sent()):
		card_element, writes = _ledger(monkeypatch, [open_row])
		saved_methods = _off_session(monkeypatch, writes, {"id": "pi_x", "status": "succeeded"})
		exc = _raised(saved_methods.charge_saved_method, customer="CUST-1", amount=500, channel="Desk")
		assert isinstance(exc, card_element.PaymentBlocked) and open_row["name"] in str(exc)
		assert writes["charges"] == [] and not [e for e in writes["events"] if e[0] == "insert"]

	card_element, writes = _ledger(monkeypatch, [_hosted_payment(status="Paid")])
	saved_methods = _off_session(monkeypatch, writes, {"id": "pi_x", "status": "processing"})
	result = saved_methods.charge_saved_method(customer="CUST-1", amount=75, channel="Desk")
	assert result["status"] == "Processing" and len(writes["charges"]) == 1

	card_element, writes = _ledger(monkeypatch, [])
	saved_methods = _off_session(monkeypatch, writes, {"id": "pi_x", "status": "succeeded"})
	message = _refusal(saved_methods.charge_saved_method, customer="CUST-OTHER", sales_invoice="SINV-1", channel="Desk")
	assert "belongs to CUST-1" in message and writes["charges"] == []

	from pathlib import Path

	js = (
		Path(__file__).resolve().parents[1] / "public" / "js" / "stripe_payments" / "customer_autopay.js"
	).read_text(encoding="utf-8")
	assert 'fieldname: "sales_invoice"' in js and 'outstanding_amount: [">", 0]' in js
	assert "charge({ sales_invoice: v.sales_invoice })" in js


# --- closing a link never overwrites what the webhook just recorded ---------------------------


def test_closing_a_link_never_overwrites_a_payment_the_webhook_just_recorded(monkeypatch):
	"""The customer completes the emailed link while a card charge for the same invoice is
	starting: expire is refused, and between the read-back and the write the webhook posts the
	Payment Entry and marks the row Paid. The write is conditional on the row still being Link
	Sent (read under its row lock), so Paid stays Paid, its Payment Entry linked, and the invoice
	is not re-stamped Processing. The card charge is refused either way."""
	for session_state in ("complete", "expired"):
		card_element, writes = _card_page(monkeypatch, [_link_sent(), _quote()])

		def webhook_lands_meanwhile(sid, expand=None, timeout=None, _state=session_state, _w=writes):
			_w["store"]["SP-LINK"].update(status="Paid", payment_entry="PE-1")
			return {"id": sid, "status": _state}

		monkeypatch.setattr(
			card_element,
			"expire_checkout_session",
			lambda sid, timeout=None: (_ for _ in ()).throw(Exception("This Checkout Session is not open")),
		)
		monkeypatch.setattr(card_element, "retrieve_checkout_session", webhook_lands_meanwhile)
		exc = _raised(card_element.confirm_card_payment, stripe_payment="SP-Q", confirmation_token="ct_new")
		assert isinstance(exc, card_element.PaymentBlocked) and str(exc) == card_element.MSG_IN_FLIGHT
		row = writes["store"]["SP-LINK"]
		assert (row["status"], row["payment_entry"]) == ("Paid", "PE-1"), session_state
		assert ("SINV-1", "Processing") not in writes["stamps"], session_state
		assert writes["charges"] == [], session_state


# --- autopay and dunning leave the customer's link alone -------------------------------------


def test_autopay_and_dunning_never_expire_the_link_the_customer_was_sent(monkeypatch):
	"""A dunning retry re-charges a card that already declined; Accounts often email a link
	because of that decline. Expiring the link first left the invoice with no way to be paid
	when the retry declined again. Autopay and dunning are refused while a link is open
	(PaymentBlocked, which dunning reschedules on) and charge nothing; a link Stripe shows expired
	is marked so and the charge goes on; a completed one refuses it."""
	for channel in ("Auto", "Dunning"):
		card_element, writes = _ledger(monkeypatch, [_link_sent()])
		saved_methods = _off_session(monkeypatch, writes, {"id": "pi_x", "status": "succeeded"})
		_checkout_sessions(monkeypatch, card_element, writes, {"cs_link": "open"}, expire={"cs_link": "expired"})
		exc = _raised(saved_methods.charge_saved_method, customer="CUST-1", sales_invoice="SINV-1", channel=channel)
		assert isinstance(exc, card_element.PaymentBlocked), channel
		assert str(exc) == card_element.MSG_LINK_OPEN_OFF_SESSION, channel
		assert writes["expires"] == [] and writes["charges"] == [], channel
		assert writes["store"]["SP-LINK"]["status"] == "Link Sent", channel

	card_element, writes = _ledger(monkeypatch, [_link_sent()])
	saved_methods = _off_session(monkeypatch, writes, {"id": "pi_x", "status": "processing"})
	_checkout_sessions(monkeypatch, card_element, writes, {"cs_link": "expired"}, expire={})
	saved_methods.charge_saved_method(customer="CUST-1", sales_invoice="SINV-1", channel="Dunning")
	assert writes["store"]["SP-LINK"]["status"] == "Expired" and len(writes["charges"]) == 1
	assert writes["expires"] == []

	card_element, writes = _ledger(monkeypatch, [_link_sent()])
	saved_methods = _off_session(monkeypatch, writes)
	_checkout_sessions(monkeypatch, card_element, writes, {"cs_link": "complete"}, expire={})
	exc = _raised(saved_methods.charge_saved_method, customer="CUST-1", sales_invoice="SINV-1", channel="Auto")
	assert str(exc) == card_element.MSG_IN_FLIGHT and writes["charges"] == []

	# Accounts charging from the desk still close it first (decision 3).
	card_element, writes = _ledger(monkeypatch, [_link_sent()])
	saved_methods = _off_session(monkeypatch, writes, {"id": "pi_x", "status": "processing"})
	_checkout_sessions(monkeypatch, card_element, writes, {"cs_link": "open"}, expire={"cs_link": "expired"})
	saved_methods.charge_saved_method(customer="CUST-1", sales_invoice="SINV-1", channel="Desk")
	assert writes["expires"] == [("cs_link", card_element.CONTROL_TIMEOUT)] and len(writes["charges"]) == 1


# --- what the customer is told ---------------------------------------------------------------


def test_waiting_on_the_bank_is_never_promised_to_clear(monkeypatch):
	"""3-D Secure inside its window may never be finished: "It will show as paid once it clears"
	was untrue for it. /pay-card says it is waiting for the bank and when it can be paid again;
	a charge nobody has an answer for says so; only a payment known to be settling keeps the
	promise. The refusals from the POSTs say the same."""
	from pathlib import Path

	html = (Path(__file__).resolve().parents[1] / "www" / "pay-card.html").read_text(encoding="utf-8")

	def branch(state):
		return html.split('{% elif settled == "' + state + '" %}', 1)[1].split("{% elif", 1)[0]

	assert "will show as paid" in branch("processing")
	for state in ("awaiting", "unconfirmed"):
		assert "will show as paid" not in branch(state) and "clears" not in branch(state), state
		assert 'href="/pay"' in branch(state) and "<script" not in branch(state), state
	assert "{{ awaiting_minutes }}" in branch("awaiting")
	assert html.index('settled == "unconfirmed"') < html.index("{% elif not invoice %}")

	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import card_element

	for message in (card_element.MSG_AWAITING, card_element.MSG_AWAITING_DESK, card_element.MSG_UNCONFIRMED):
		assert "clears" not in message and "show as paid" not in message
	assert "in about {minutes}" in card_element.MSG_AWAITING

	# /pay's list: no verdict reaches the buttons, whatever it is called.
	pay = (Path(__file__).resolve().parents[1] / "www" / "pay.html").read_text(encoding="utf-8")
	assert '{% elif inv.payment_block %}' in pay and 'inv.payment_block == "Awaiting"' in pay


def test_pay_card_page_says_waiting_or_unconfirmed(monkeypatch):
	"""The render maps each verdict to its own state, with the minutes left for 3-D Secure."""
	frappe_stub = install_frappe_stub()
	page = _load_pay_card_page()
	settings = types.SimpleNamespace(enabled=1, enable_card=1, enable_ach=0, publishable_key="pk_test_x")
	monkeypatch.setattr(page, "get_settings", lambda: settings)
	monkeypatch.setattr(page, "is_enabled", lambda s=None: True)
	monkeypatch.setattr(page, "get_portal_customers", lambda: ["CUST-1"])
	monkeypatch.setattr(frappe_stub, "session", types.SimpleNamespace(user="jane@example.com"))
	monkeypatch.setattr(frappe_stub, "sessions", types.SimpleNamespace(get_csrf_token=lambda: "tok"), raising=False)
	monkeypatch.setattr(frappe_stub, "form_dict", {"invoice": "SINV-1"}, raising=False)
	invoice = types.SimpleNamespace(
		name="SINV-1", customer="CUST-1", outstanding_amount=200.0, currency="USD", docstatus=1, is_return=0, status="Unpaid"
	)
	monkeypatch.setattr(
		frappe_stub, "db", types.SimpleNamespace(exists=lambda *a, **k: True, get_value=lambda *a, **k: invoice)
	)
	from erpnext_enhancements.stripe_payments.core import card_element

	monkeypatch.setattr(card_element, "now_datetime", lambda: NOW)
	waiting = card_element._awaiting(NOW + _dt.timedelta(minutes=12, seconds=10))
	for verdict, settled in ((waiting, "awaiting"), ("Unconfirmed", "unconfirmed"), ("Processing", "processing")):
		monkeypatch.setattr(page, "invoice_payment_block", lambda name, _v=verdict: _v)
		context = types.SimpleNamespace()
		page.get_context(context)
		assert (context.settled, context.invoice, context.settled_invoice) == (settled, None, "SINV-1")
	monkeypatch.setattr(page, "invoice_payment_block", lambda name: waiting)
	context = types.SimpleNamespace()
	page.get_context(context)
	assert context.awaiting_minutes == 13


def test_stripe_return_tells_the_truth_about_the_row_as_it_is_now(monkeypatch):
	"""The card page sends an unknown outcome to /stripe-return, which said "Thank you! Your
	payment is being processed". It now reads the row: an outcome nobody knows says "we could
	not confirm it yet — do not pay again, we'll email you"; a row that did not go through (Failed,
	or Expired: proven never charged) says nothing was charged; Paid says received."""
	from pathlib import Path

	import pytest

	frappe_stub = install_frappe_stub()
	page = _load_www_page("stripe_return.py", "ee_test_stripe_return")
	rows = {
		"SP-U": types.SimpleNamespace(status="Processing", stripe_payment_intent=None, stripe_checkout_session=None),
		"SP-P": types.SimpleNamespace(status="Processing", stripe_payment_intent="pi_1", stripe_checkout_session=None),
		"SP-X": types.SimpleNamespace(status="Expired", stripe_payment_intent=None, stripe_checkout_session=None),
		"SP-OK": types.SimpleNamespace(status="Paid", stripe_payment_intent="pi_2", stripe_checkout_session=None),
	}
	monkeypatch.setattr(
		frappe_stub,
		"db",
		types.SimpleNamespace(
			exists=lambda doctype, name=None, *a, **k: name in rows,
			get_value=lambda doctype, name=None, *a, **k: rows.get(name),
		),
	)
	contexts = {}
	for name in rows:
		monkeypatch.setattr(frappe_stub, "form_dict", {"status": "success", "sp": name}, raising=False)
		context = types.SimpleNamespace()
		page.get_context(context)
		contexts[name] = (context.payment_status, context.unconfirmed)
	assert contexts == {
		"SP-U": ("Processing", True),
		"SP-P": ("Processing", False),
		"SP-X": ("Expired", False),
		"SP-OK": ("Paid", False),
	}

	jinja2 = pytest.importorskip("jinja2")
	source = (Path(__file__).resolve().parents[1] / "www" / "stripe-return.html").read_text(encoding="utf-8")
	env = jinja2.Environment(
		loader=jinja2.DictLoader({"templates/web.html": "{% block page_content %}{% endblock %}", "r.html": source})
	)
	env.globals["_"] = lambda text, *a, **k: text

	def render(**context):
		return env.get_template("r.html").render(outcome="success", **context)

	unknown = render(payment_status="Processing", unconfirmed=True)
	assert "do not pay again" in unknown and "being processed" not in unknown and "Thank you" not in unknown
	not_charged = render(payment_status="Expired", unconfirmed=False)
	assert "nothing was charged" in not_charged and "Thank you" not in not_charged
	assert "nothing was charged" in render(payment_status="Failed", unconfirmed=False)
	assert "was received" in render(payment_status="Paid", unconfirmed=False)
	assert "being processed" in render(payment_status="Processing", unconfirmed=False)
	assert "cancelled" in env.get_template("r.html").render(outcome="cancel", payment_status=None, unconfirmed=False)


def test_the_payer_told_being_processed_is_emailed_when_it_never_charged(monkeypatch):
	"""The email itself: to the portal user who started it, through the email design system's
	wrap, saying nothing was charged. Never to Administrator or a guest; never raises."""
	import erpnext_enhancements

	frappe_stub = install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import card_element

	sent = []
	fake_style = types.SimpleNamespace(
		p=lambda text: f"<p>{text}</p>",
		button=lambda url, label, tone="primary": f"<a href='{url}'>{label}</a>",
		button_fallback=lambda url: "",
		wrap=lambda body, **k: f"[{k.get('title')}]{body}",
	)
	monkeypatch.setattr(erpnext_enhancements, "email_style", fake_style, raising=False)
	monkeypatch.setattr(frappe_stub, "sendmail", lambda **k: sent.append(k), raising=False)
	monkeypatch.setattr(
		frappe_stub, "db", types.SimpleNamespace(get_value=lambda doctype, name=None, field=None, **k: name)
	)

	def sp(user):
		return types.SimpleNamespace(name="SP-U", get=lambda key, default=None: {"initiated_by": user}.get(key, default))

	card_element._email_payer_not_charged(sp("jane@example.com"), "USD 200.00", "invoice SINV-1")
	((mail,),) = [(m,) for m in sent]
	assert mail["recipients"] == ["jane@example.com"]
	assert "did not go through" in mail["subject"] and "Nothing was charged" in mail["message"]
	for user in ("Administrator", "Guest", None):
		sent.clear()
		card_element._email_payer_not_charged(sp(user), "USD 200.00", "invoice SINV-1")
		assert sent == [], user
	monkeypatch.setattr(frappe_stub, "sendmail", lambda **k: (_ for _ in ()).throw(Exception("SMTP down")), raising=False)
	card_element._email_payer_not_charged(sp("jane@example.com"), "USD 200.00", "invoice SINV-1")  # logged, not raised


def test_a_refusal_for_the_invoice_is_payment_blocked_so_the_page_shows_why(monkeypatch):
	"""The card page reloads on PaymentBlocked (the render shows paid / nothing left / being
	processed) and goes back to its card step on anything else. So: paid, credited, or a quote
	already sent are PaymentBlocked; a changed amount or a canceled invoice on the card page are
	not (its quote is spent); the quote refuses a canceled invoice as PaymentBlocked (the render
	says it is not available)."""
	for outstanding, status in ((0, "Paid"), (0, "Credit Note Issued")):
		card_element, writes = _card_page(monkeypatch, [_quote()], invoice=_invoice(outstanding_amount=outstanding, status=status))
		exc = _raised(card_element.confirm_card_payment, stripe_payment="SP-Q", confirmation_token="ct_new")
		assert isinstance(exc, card_element.PaymentBlocked), status
	for fields in ({"outstanding_amount": 150}, {"docstatus": 2}):
		card_element, writes = _card_page(monkeypatch, [_quote()], invoice=_invoice(**fields))
		exc = _raised(card_element.confirm_card_payment, stripe_payment="SP-Q", confirmation_token="ct_new")
		assert not isinstance(exc, card_element.PaymentBlocked), fields
	card_element, writes = _card_page(monkeypatch, [_quote(status="Processing")])
	exc = _raised(card_element.confirm_card_payment, stripe_payment="SP-Q", confirmation_token="ct_new")
	assert isinstance(exc, card_element.PaymentBlocked) and "already Processing" in str(exc)
	card_element, writes = _card_page(monkeypatch, [_quote(status="Failed")])
	exc = _raised(card_element.confirm_card_payment, stripe_payment="SP-Q", confirmation_token="ct_new")
	assert not isinstance(exc, card_element.PaymentBlocked)

	card_element, writes = _card_page(monkeypatch, [], invoice=_invoice(docstatus=2))
	read = []
	monkeypatch.setattr(card_element, "_confirmation_token_method", lambda t: read.append(t) or ("card", "debit"))
	exc = _raised(card_element.price_card_payment, confirmation_token="ct_new", sales_invoice="SINV-1")
	assert isinstance(exc, card_element.PaymentBlocked) and read == []
	# An emailed link Stripe could not be asked to close is not something a render can show.
	assert issubclass(card_element.LinkStillOpen, card_element.PaymentBlocked)


# --- a link that outlived its invoice --------------------------------------------------------


def test_a_link_that_outlived_its_invoice_is_never_sent_and_is_expired(monkeypatch):
	"""Settled another way (a cheque, a credit note) or canceled while a link is open: the desk
	refuses to (re)send it, and poll_pending expires it at Stripe. A completed one is left to the
	webhook, and Accounts are told the money needs a refund or reallocating."""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import api

	link = dict(
		_link_sent(),
		checkout_url="https://checkout.stripe.com/c/cs_link",
		description="Invoice SINV-1",
		modified=OLD,
	)
	for fields in ({"outstanding_amount": 0, "status": "Paid"}, {"outstanding_amount": 120}, {"docstatus": 2}):
		card_element, writes = _ledger(monkeypatch, [dict(link)], invoice=_invoice(**fields))
		message = _refusal(api.send_payment_link, stripe_payment="SP-LINK")
		assert "must not be sent" in message, fields

	card_element, writes = _ledger(monkeypatch, [dict(link)])
	sent = []
	import frappe as frappe_stub

	monkeypatch.setattr(frappe_stub, "sendmail", lambda **k: sent.append(k), raising=False)
	monkeypatch.setattr(
		api,
		"email_style",
		types.SimpleNamespace(
			p=lambda t: t, button=lambda u, label: u, button_fallback=lambda u: "", wrap=lambda b, **k: b
		),
	)
	monkeypatch.setattr(api, "_customer_email", lambda customer: "jane@example.com")
	assert api.send_payment_link("SP-LINK")["sent"] is True and len(sent) == 1

	for invoice, state, expected in (
		(_invoice(outstanding_amount=0, status="Paid"), "expired", "Expired"),
		(_invoice(docstatus=2), "expired", "Expired"),
		(_invoice(), "expired", "Link Sent"),  # still owed exactly: left alone
	):
		tasks, card_element, writes = _poll(monkeypatch, [dict(link)], sessions={"cs_link": "open"}, invoice=invoice)
		_checkout_sessions(monkeypatch, card_element, writes, {"cs_link": "open"}, expire={"cs_link": state})
		tasks.poll_pending()
		assert writes["store"]["SP-LINK"]["status"] == expected, invoice
		if expected == "Expired":
			assert writes["store"]["SP-LINK"]["error_message"] == card_element.NOTE_LINK_STALE

	tasks, card_element, writes = _poll(
		monkeypatch, [dict(link)], sessions={"cs_link": "open"}, invoice=_invoice(docstatus=2)
	)
	_checkout_sessions(monkeypatch, card_element, writes, {"cs_link": "complete"}, expire={})
	tasks.poll_pending()
	assert writes["store"]["SP-LINK"]["status"] == "Processing"
	assert [d for _, d in writes["accounts_alerts"]] == ["SP-LINK"]


# --- off-session requires_action ------------------------------------------------------------


def _off_session_row(name, pi, kind, modified=OLD, invoice="SINV-1"):
	"""An off-session charge (autopay): no ConfirmationToken, and its method's kind."""
	return dict(
		_card_attempt(name=name, pi=pi, invoice=invoice, modified=modified),
		confirmation_token=None,
		payment_method_type=kind,
		channel="Auto",
	)


def test_an_off_session_requires_action_is_never_left_live_behind_a_failed_row(monkeypatch):
	"""A saved bank account still waiting on microdeposit verification answers requires_action.
	It was marked Failed with its PaymentIntent left live: invisible to the guard, while dunning
	charged again — two debits once the customer verified. Now a bank debit stays Processing (it
	blocks, and is never canceled); a card's challenge, which nobody is present to finish, is
	canceled at Stripe at once and failed only once that is proven."""
	card_element, writes = _ledger(monkeypatch, [])
	saved_methods = _off_session(monkeypatch, writes, {"id": "pi_ach", "status": "requires_action"})
	monkeypatch.setattr(saved_methods, "_payment_method_funding", lambda pm: ("us_bank_account", None))
	result = saved_methods.charge_saved_method(customer="CUST-1", sales_invoice="SINV-1", channel="Auto")
	assert result["status"] == "Processing" and writes["cancels"] == [] and writes["alerts"] == []
	(row,) = writes["store"].values()
	assert row["error_message"] == card_element.NOTE_ACH_VERIFYING
	# Blocking, as waiting on the customer's verification — never promised to clear
	# (test_off_session_holds_are_worded_for_what_they_are).
	assert card_element.invoice_payment_block("SINV-1") == card_element.VERIFYING

	# The saved method's lookup failed: the PaymentIntent says it is a bank account.
	card_element, writes = _ledger(monkeypatch, [])
	saved_methods = _off_session(
		monkeypatch,
		writes,
		{"id": "pi_ach", "status": "requires_action", "payment_method_types": ["us_bank_account"]},
	)
	monkeypatch.setattr(saved_methods, "_payment_method_funding", lambda pm: (None, None))
	assert saved_methods.charge_saved_method(customer="CUST-1", sales_invoice="SINV-1")["status"] == "Processing"
	(row,) = writes["store"].values()
	assert row["payment_method_type"] == "us_bank_account" and writes["cancels"] == []

	# A card: canceled at once, then Failed (and alerted, as a decline, for autopay).
	card_element, writes = _ledger(monkeypatch, [], cancel={"pi_card": "canceled"})
	saved_methods = _off_session(monkeypatch, writes, {"id": "pi_card", "status": "requires_action"})
	result = saved_methods.charge_saved_method(customer="CUST-1", sales_invoice="SINV-1", channel="Auto")
	assert result["status"] == "Failed" and writes["cancels"] == ["pi_card"] and len(writes["alerts"]) == 1
	assert card_element.invoice_payment_block("SINV-1") is None
	assert writes["stamps"][-1] == ("SINV-1", "Failed")

	# The cancel cannot be proven: held Processing, blocking, for poll_pending — as an attempt
	# that could not be released, never as a payment that will clear.
	card_element, writes = _ledger(monkeypatch, [], pi_status={"pi_card": "requires_action"}, cancel={})
	saved_methods = _off_session(monkeypatch, writes, {"id": "pi_card", "status": "requires_action"})
	result = saved_methods.charge_saved_method(customer="CUST-1", sales_invoice="SINV-1", channel="Auto")
	assert result["status"] == "Processing" and writes["alerts"] == []
	(row,) = writes["store"].values()
	assert row["error_message"] == card_element.NOTE_CANCEL_UNPROVEN
	assert card_element.invoice_payment_block("SINV-1") == card_element.UNRELEASED


def test_poll_pending_never_cancels_an_off_session_bank_debit_awaiting_verification(monkeypatch):
	"""The 30-minute 3-D Secure rule is the card page's: an off-session bank debit in
	requires_action is verification in progress and is never canceled, however old; an off-session
	card challenge is canceled on the next run (nobody will finish it); a card-page attempt still
	gets its 30 minutes."""
	rows = [
		_off_session_row("SP-ACH", "pi_ach", "us_bank_account", invoice="SINV-1"),
		_off_session_row("SP-CARD", "pi_card", "card", modified=NOW - _dt.timedelta(minutes=20), invoice="SINV-2"),
		_card_attempt(name="SP-PAGE", pi="pi_page", invoice="SINV-3", modified=NOW - _dt.timedelta(minutes=20)),
	]
	tasks, card_element, writes = _poll(
		monkeypatch,
		rows,
		pi_status={"pi_ach": "requires_action", "pi_card": "requires_action", "pi_page": "requires_action"},
		cancel={"pi_ach": "canceled", "pi_card": "canceled", "pi_page": "canceled"},
	)
	tasks.poll_pending()
	store = writes["store"]
	assert writes["cancels"] == ["pi_card"]
	assert [store[name]["status"] for name in ("SP-ACH", "SP-CARD", "SP-PAGE")] == [
		"Processing",
		"Failed",
		"Processing",
	]


# --- webhooks never move a row backwards --------------------------------------------------


def _webhooks(monkeypatch, writes, events=()):
	"""reconcile's event handlers against the fake ledger. ``events`` seeds the Stripe Event log
	(dicts of its fields), which ``frappe.db.exists`` answers from. Invoice stamps land in
	``writes["stamps"]``, finalizes in ``writes["finalized"]``, autopay's decline alerts in
	``writes["auto_alerts"]``."""
	import frappe as frappe_stub

	from erpnext_enhancements.stripe_payments.core import reconcile, saved_methods

	ledger_exists = frappe_stub.db.exists

	def exists(doctype, filters=None, *args, **kwargs):
		if doctype == "Stripe Event":
			return any(_matches(event, filters) for event in events) or None
		return ledger_exists(doctype, filters, *args, **kwargs)

	monkeypatch.setattr(frappe_stub.db, "exists", exists)
	monkeypatch.setattr(
		reconcile, "_stamp_invoice", lambda inv, status, *a: writes["stamps"].append((inv, status))
	)
	writes["finalized"] = []
	monkeypatch.setattr(
		reconcile, "finalize_payment", lambda sp, obj: writes["finalized"].append((sp.name, obj.get("id")))
	)
	writes["auto_alerts"] = []
	monkeypatch.setattr(
		saved_methods, "_alert_failed_autocharge", lambda sp, err: writes["auto_alerts"].append((sp.name, err))
	)
	return reconcile


def _pi_failed(pi="pi_link", sp="SP-LINK", message="Your card was declined."):
	"""A ``payment_intent.payment_failed`` event object."""
	return {
		"object": "payment_intent",
		"id": pi,
		"status": "requires_payment_method",
		"metadata": {"stripe_payment": sp},
		"last_payment_error": {"message": message},
	}


def _session_event(sp="SP-ACH", session="cs_ach", pi="pi_ach", payment_status="unpaid"):
	"""A ``checkout.session.*`` event object."""
	return {
		"object": "checkout.session",
		"id": session,
		"payment_status": payment_status,
		"payment_intent": pi,
		"metadata": {"stripe_payment": sp},
	}


def _render_stripe_return(**context):
	import pytest

	jinja2 = pytest.importorskip("jinja2")
	from pathlib import Path

	source = (Path(__file__).resolve().parents[1] / "www" / "stripe-return.html").read_text(encoding="utf-8")
	env = jinja2.Environment(
		loader=jinja2.DictLoader({"templates/web.html": "{% block page_content %}{% endblock %}", "r.html": source})
	)
	env.globals["_"] = lambda text, *a, **k: text
	return env.get_template("r.html").render(**context)


def test_a_declined_attempt_inside_a_checkout_link_never_hides_the_link(monkeypatch):
	"""Stripe sends payment_intent.payment_failed for each declined card inside a Checkout
	Session, and the Session stays payable for the rest of its 24 hours. That event used to fail
	the emailed link's row, which no guard reads (they read Processing / Paid, and link-closing
	reads Link Sent): a card payment on /pay-card was charged beside the still-open link, and the
	link could then be paid as well. Now the decline is recorded on the open link and nothing else
	changes — so the card charge expires the link before charging."""
	card_element, writes = _card_page(monkeypatch, [_link_sent(), _quote()])
	reconcile = _webhooks(monkeypatch, writes)
	assert reconcile._on_payment_intent_failed(_pi_failed()).name == "SP-LINK"
	link = writes["store"]["SP-LINK"]
	assert link["status"] == "Link Sent"
	assert link["error_message"].startswith(reconcile.NOTE_CHECKOUT_ATTEMPT_FAILED)
	assert "Your card was declined." in link["error_message"]
	# The Session's PaymentIntent is not recorded on the link: poll_pending keeps reading the
	# Session (its expiry, its stale-link check), not a PaymentIntent that looks dead.
	assert link["stripe_payment_intent"] is None
	assert writes["stamps"] == [] and writes["auto_alerts"] == [] and writes["accounts_alerts"] == []
	assert card_element.invoice_payment_block("SINV-1") is None  # a link blocks nothing; it is closed

	# The customer then pays by card on /pay-card: the still-open link is expired first.
	_checkout_sessions(monkeypatch, card_element, writes, {"cs_link": "open"}, expire={"cs_link": "expired"})
	card_element.confirm_card_payment(stripe_payment="SP-Q", confirmation_token="ct_new")
	assert writes["expires"] == [("cs_link", card_element.CONTROL_TIMEOUT)]
	assert writes["store"]["SP-LINK"]["status"] == "Expired"
	assert _index(writes["events"], ("expire", "cs_link")) < _index(writes["events"], "charge")
	assert len(writes["charges"]) == 1

	# Had the customer completed the link meanwhile, the card charge is refused instead.
	card_element, writes = _card_page(monkeypatch, [_link_sent(), _quote()])
	reconcile = _webhooks(monkeypatch, writes)
	reconcile._on_payment_intent_failed(_pi_failed())
	_checkout_sessions(monkeypatch, card_element, writes, {"cs_link": "complete"}, expire={})
	exc = _raised(card_element.confirm_card_payment, stripe_payment="SP-Q", confirmation_token="ct_new")
	assert isinstance(exc, card_element.PaymentBlocked) and writes["charges"] == []

	# A Draft hosted row (its Session still being created) is not failed either.
	card_element, writes = _ledger(monkeypatch, [dict(_link_sent(), status="Draft", stripe_checkout_session=None)])
	reconcile = _webhooks(monkeypatch, writes)
	reconcile._on_payment_intent_failed(_pi_failed())
	assert writes["store"]["SP-LINK"]["status"] == "Draft"


def test_stripe_return_says_being_processed_after_a_declined_attempt_inside_checkout(monkeypatch):
	"""A customer declined once inside Checkout who then paid with another card lands on
	/stripe-return, often before checkout.session.completed is processed (or after a deploy's
	FLUSHDB lost it until retry_failed). It said "This payment did not go through, and nothing
	was charged", and /pay offered Card again beside it. The row stays Link Sent, so it says the
	payment is being processed."""
	card_element, writes = _ledger(monkeypatch, [_link_sent()])
	reconcile = _webhooks(monkeypatch, writes)
	reconcile._on_payment_intent_failed(_pi_failed())

	import frappe as frappe_stub

	page = _load_www_page("stripe_return.py", "ee_test_stripe_return_hosted")
	monkeypatch.setattr(
		frappe_stub, "form_dict", {"status": "success", "sp": "SP-LINK", "session_id": "cs_link"}, raising=False
	)
	context = types.SimpleNamespace()
	page.get_context(context)
	assert (context.payment_status, context.unconfirmed) == ("Link Sent", False)
	shown = _render_stripe_return(
		outcome=context.outcome, payment_status=context.payment_status, unconfirmed=context.unconfirmed
	)
	assert "being processed" in shown and "Thank you" in shown
	assert "nothing was charged" not in shown and "did not go through" not in shown


def test_no_webhook_moves_a_row_backwards(monkeypatch):
	"""Stripe does not guarantee event order, and retry_failed re-runs an event up to an hour
	late. A late decline for an earlier card in a Session no longer fails an ACH debit still
	settling (which unblocked its invoice); the Session's own events still decide a hosted row;
	a Paid or Refunded row is never re-opened; and a late checkout.session.completed leaves a
	debit Stripe already reported failed alone, but makes a row the old rule failed block again."""
	card_element, writes = _ledger(monkeypatch, [_hosted_payment()])
	reconcile = _webhooks(monkeypatch, writes)
	reconcile._on_payment_intent_failed(_pi_failed(pi="pi_ach", sp="SP-ACH"))
	row = writes["store"]["SP-ACH"]
	assert (row["status"], row["error_message"]) == ("Processing", None)
	assert card_element.invoice_payment_block("SINV-1") == "Processing"
	# The Session's own final events still decide it: the debit failed at the bank.
	reconcile._on_session_async_failed(_session_event())
	assert row["status"] == "Failed" and writes["stamps"] == [("SINV-1", "Failed")]

	# A link with a declined attempt still expires with its Session.
	card_element, writes = _ledger(monkeypatch, [_link_sent()])
	reconcile = _webhooks(monkeypatch, writes)
	reconcile._on_payment_intent_failed(_pi_failed())
	reconcile._on_session_expired(_session_event(sp="SP-LINK", session="cs_link", pi=None))
	assert writes["store"]["SP-LINK"]["status"] == "Expired"

	# The card page and off-session charges never confirm a PaymentIntent again: the event is
	# the outcome there, and autopay's own decline alert fires.
	card_element, writes = _ledger(
		monkeypatch, [_card_attempt(), _off_session_row("SP-OFF", "pi_off", "card", invoice="SINV-2")]
	)
	reconcile = _webhooks(monkeypatch, writes)
	reconcile._on_payment_intent_failed(_pi_failed(pi="pi_old", sp="SP-OLD"))
	reconcile._on_payment_intent_failed(_pi_failed(pi="pi_off", sp="SP-OFF"))
	assert [writes["store"][name]["status"] for name in ("SP-OLD", "SP-OFF")] == ["Failed", "Failed"]
	assert writes["auto_alerts"] == [("SP-OFF", "Your card was declined.")]
	assert writes["accounts_alerts"] == [] and writes["emails"] == []

	# Paid and Refunded are never re-opened, by any event, hosted or not.
	for settled in ("Paid", "Refunded"):
		card_element, writes = _ledger(
			monkeypatch, [_hosted_payment(status=settled), _card_attempt(status=settled, invoice="SINV-2")]
		)
		reconcile = _webhooks(monkeypatch, writes)
		reconcile._on_session_completed(_session_event())
		reconcile._on_session_async_failed(_session_event())
		reconcile._on_payment_intent_failed(_pi_failed(pi="pi_ach", sp="SP-ACH"))
		reconcile._on_payment_intent_failed(_pi_failed(pi="pi_old", sp="SP-OLD"))
		for name, pi in (("SP-ACH", "pi_ach"), ("SP-OLD", "pi_old")):
			reconcile._on_payment_intent_succeeded(
				{"object": "payment_intent", "id": pi, "status": "succeeded", "metadata": {"stripe_payment": name}}
			)
			assert writes["store"][name]["status"] == settled, (settled, name)
		assert writes["stamps"] == [] and writes["finalized"] == [], settled

	# A late completed for a debit Stripe has already reported failed: it stays failed (nothing
	# would ever settle a hosted row whose debit is dead).
	failed_debit = [
		{
			"stripe_payment": "SP-ACH",
			"event_type": "checkout.session.async_payment_failed",
			"process_status": "Processed",
		}
	]
	card_element, writes = _ledger(monkeypatch, [_hosted_payment(status="Failed")])
	reconcile = _webhooks(monkeypatch, writes, events=failed_debit)
	reconcile._on_session_completed(_session_event())
	assert writes["store"]["SP-ACH"]["status"] == "Failed" and writes["stamps"] == []

	# A row the old declined-attempt rule failed, whose debit is on its way: it blocks again.
	# An open link that completes as a debit becomes Processing, as it always did.
	for status in ("Failed", "Link Sent"):
		card_element, writes = _ledger(monkeypatch, [_hosted_payment(status=status)])
		reconcile = _webhooks(monkeypatch, writes)
		reconcile._on_session_completed(_session_event())
		assert writes["store"]["SP-ACH"]["status"] == "Processing", status
		assert writes["stamps"] == [("SINV-1", "Processing")], status
		assert card_element.invoice_payment_block("SINV-1") == "Processing", status


def test_re_finalizing_a_refunded_payment_never_turns_it_back_into_paid(monkeypatch):
	"""A late success event, or poll_pending, finalizing a row that was posted and then refunded
	returns its Payment Entry and changes nothing."""
	frappe_stub = install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import reconcile

	class Row(dict):
		__getattr__ = dict.get

	for status in ("Paid", "Refunded"):
		touched = []
		monkeypatch.setattr(
			frappe_stub,
			"db",
			types.SimpleNamespace(
				get_value=lambda *a, _s=status, **k: Row(status=_s, payment_entry="PE-1"),
				commit=lambda: touched.append("commit"),
			),
		)
		monkeypatch.setattr(reconcile, "_mark_paid", lambda *a, **k: touched.append("mark paid"))
		monkeypatch.setattr(reconcile, "_create_payment_entry", lambda *a, **k: touched.append("create"))
		sp = types.SimpleNamespace(name="SP-R", stripe_payment_intent="pi_r")
		assert reconcile.finalize_payment(sp, {"object": "payment_intent", "id": "pi_r"}) == "PE-1", status
		assert touched == [], status


# --- the payer told "we will email you" is emailed whichever way it resolves ----------------


def test_a_charge_held_unknown_is_emailed_whichever_way_it_is_released(monkeypatch):
	"""/stripe-return tells the payer of a charge Stripe never answered "please do not pay again:
	we will email you if it did not go through". Only proof that no PaymentIntent existed sent
	that email; a PaymentIntent found declined, or found in 3-D Secure the payer never saw (the
	answer carrying it was lost) and released 30 minutes later, was failed in silence while the
	invoice quietly became payable again. Now each of those emails the payer and alerts Accounts
	— poll_pending, the next payment someone starts, or the payment_failed webhook — unless it
	is the payer's own new payment that releases it."""
	unknown = dict(
		_card_attempt(name="SP-U", pi=None, modified=NOW - _dt.timedelta(minutes=20)),
		initiated_by="jane@example.com",
	)

	def listing(status):
		return [
			{
				"data": [{"id": "pi_found", "status": status, "metadata": {"stripe_payment": "SP-U"}}],
				"has_more": False,
			}
		]

	# Found declined: released at once, and the payer hears.
	tasks, card_element, writes = _poll(
		monkeypatch, [dict(unknown)], cancel={"pi_found": "canceled"}, listing=listing("requires_payment_method")
	)
	tasks.poll_pending()
	row = writes["store"]["SP-U"]
	assert row["status"] == "Failed" and "nothing was charged" in row["error_message"]
	assert writes["emails"] == [("SP-U", "jane@example.com", "invoice SINV-1")]
	assert [docname for _, docname in writes["accounts_alerts"]] == ["SP-U"]

	# Found in 3-D Secure nobody can finish: it waits out its 30 minutes, marked as found...
	tasks, card_element, writes = _poll(
		monkeypatch,
		[dict(unknown)],
		pi_status={"pi_found": "requires_action"},
		cancel={"pi_found": "canceled"},
		listing=listing("requires_action"),
	)
	tasks.poll_pending()
	row = writes["store"]["SP-U"]
	assert (row["status"], row["stripe_payment_intent"]) == ("Processing", "pi_found")
	assert row["error_message"] == card_element.NOTE_OUTCOME_FOUND
	assert writes["cancels"] == [] and writes["emails"] == [] and writes["accounts_alerts"] == []
	# ...and once they pass untouched, the next run releases it and the payer hears.
	row["modified"] = NOW - _dt.timedelta(minutes=45)
	tasks.poll_pending()
	assert row["status"] == "Failed" and "30 minutes" in row["error_message"]
	assert writes["cancels"] == ["pi_found"]
	assert writes["emails"] == [("SP-U", "jane@example.com", "invoice SINV-1")]
	assert [docname for _, docname in writes["accounts_alerts"]] == ["SP-U"]

	# Released instead by the next payment someone else starts for the invoice: the payer hears.
	import frappe as frappe_stub

	found = dict(_card_attempt(name="SP-U"), initiated_by="jane@example.com")
	card_element, writes = _ledger(
		monkeypatch, [found], pi_status={"pi_old": "requires_payment_method"}, cancel={"pi_old": "canceled"}
	)
	writes["store"]["SP-U"]["error_message"] = card_element.NOTE_OUTCOME_FOUND
	assert card_element.invoice_payment_block("SINV-1", release=True) is None
	assert writes["emails"] == [("SP-U", "jane@example.com", "invoice SINV-1")]
	assert [docname for _, docname in writes["accounts_alerts"]] == ["SP-U"]

	# By the payer's own new payment: Accounts hear, and no "did not go through" email lands as
	# the payer pays again.
	card_element, writes = _ledger(
		monkeypatch, [dict(found)], pi_status={"pi_old": "requires_payment_method"}, cancel={"pi_old": "canceled"}
	)
	writes["store"]["SP-U"]["error_message"] = card_element.NOTE_OUTCOME_FOUND
	monkeypatch.setattr(frappe_stub, "session", types.SimpleNamespace(user="jane@example.com"))
	assert card_element.invoice_payment_block("SINV-1", release=True) is None
	assert writes["emails"] == [] and [docname for _, docname in writes["accounts_alerts"]] == ["SP-U"]
	monkeypatch.setattr(frappe_stub, "session", types.SimpleNamespace(user="operator@example.com"))

	# An ordinary abandoned attempt — its payer was promised nothing — is released quietly.
	card_element, writes = _ledger(
		monkeypatch,
		[dict(found, error_message=None)],
		pi_status={"pi_old": "requires_payment_method"},
		cancel={"pi_old": "canceled"},
	)
	assert card_element.invoice_payment_block("SINV-1", release=True) is None
	assert writes["emails"] == [] and writes["accounts_alerts"] == []

	# Stripe's payment_failed webhook reports it declined before poll_pending looks: failed, its
	# PaymentIntent recorded, and the payer hears — once, however often the event is delivered.
	card_element, writes = _ledger(monkeypatch, [dict(unknown)])
	reconcile = _webhooks(monkeypatch, writes)
	for _ in range(2):
		reconcile._on_payment_intent_failed(_pi_failed(pi="pi_found", sp="SP-U"))
	row = writes["store"]["SP-U"]
	assert (row["status"], row["stripe_payment_intent"]) == ("Failed", "pi_found")
	assert writes["emails"] == [("SP-U", "jane@example.com", "invoice SINV-1")]
	assert [docname for _, docname in writes["accounts_alerts"]] == ["SP-U"]

	# Autopay has its own decline alert, and no payer waiting on an email.
	card_element, writes = _ledger(
		monkeypatch, [dict(unknown, name="SP-A", channel="Auto", confirmation_token=None)]
	)
	reconcile = _webhooks(monkeypatch, writes)
	reconcile._on_payment_intent_failed(_pi_failed(pi="pi_a", sp="SP-A"))
	assert writes["auto_alerts"] == [("SP-A", "Your card was declined.")]
	assert writes["accounts_alerts"] == [] and writes["emails"] == []


# --- a release Stripe will not confirm promises nothing ------------------------------------


def test_a_release_stripe_will_not_confirm_never_promises_to_clear(monkeypatch):
	"""An attempt that can no longer charge (declined, or 3-D Secure abandoned past its window)
	whose cancel Stripe will not confirm — the cancel timed out, and the read-back failed or still
	shows a state that cannot charge — keeps blocking. It used to be told "It will show as paid
	once it clears" (customers) and "Wait for it to settle" (Accounts canceling the invoice), about
	an attempt that will never clear. It is now "could not be released just now; try again in a
	few minutes". Only an attempt that reads back charging after all keeps the promise."""
	for now in (None, "requires_payment_method"):
		card_element, writes = _card_page(
			monkeypatch, [_card_attempt(), _quote()], pi_status={"pi_old": "requires_payment_method"}
		)
		_cancel_refused(monkeypatch, card_element, writes, now=now)
		exc = _raised(card_element.confirm_card_payment, stripe_payment="SP-Q", confirmation_token="ct_new")
		assert isinstance(exc, card_element.PaymentBlocked), now
		assert str(exc) == card_element.MSG_UNRELEASED, now
		assert writes["cancels"] == ["pi_old"] and writes["charges"] == [], now
		assert writes["store"]["SP-OLD"]["status"] == "Processing", now

		card_element, writes = _ledger(monkeypatch, [_card_attempt()], pi_status={"pi_old": "requires_payment_method"})
		_cancel_refused(monkeypatch, card_element, writes, now=now)
		checkout = _hosted_checkout(monkeypatch, writes)
		message = _refusal(checkout.create_payment, sales_invoice="SINV-1", channel="Desk")
		assert message == card_element.MSG_UNRELEASED_DESK and writes["steps"] == [], now

		card_element, writes = _ledger(monkeypatch, [_card_attempt()], pi_status={"pi_old": "requires_payment_method"})
		_cancel_refused(monkeypatch, card_element, writes, now=now)
		_enabled(monkeypatch, card_element)
		exc = _raised(_cancel, card_element=card_element)
		assert isinstance(exc, card_element.PaymentBlocked), now
		assert str(exc) == card_element.MSG_CANCEL_UNRELEASED.format(payments="Stripe Payment SP-OLD"), now
		assert writes["commits"] == 0, now

	# 3-D Secure past its window whose cancel timed out, still reading requires_action.
	card_element, writes = _ledger(monkeypatch, [_card_attempt()], pi_status={"pi_old": "requires_action"})
	_cancel_refused(monkeypatch, card_element, writes, now="requires_action")
	assert card_element.invoice_payment_block("SINV-1", release=True) == card_element.UNRELEASED

	# It charged after all (the payer finished 3-D Secure a moment ago): settling, and it clears.
	for now in ("succeeded", "processing"):
		card_element, writes = _ledger(monkeypatch, [_card_attempt()], pi_status={"pi_old": "requires_action"})
		_cancel_refused(monkeypatch, card_element, writes, now=now)
		assert card_element.invoice_payment_block("SINV-1", release=True) == "Processing", now
		card_element, writes = _ledger(monkeypatch, [_card_attempt()], pi_status={"pi_old": "requires_action"})
		_cancel_refused(monkeypatch, card_element, writes, now=now)
		_enabled(monkeypatch, card_element)
		assert card_element.CANCEL_DETAIL_PROCESSING in str(_raised(_cancel, card_element=card_element)), now

	for message in (
		card_element.MSG_UNRELEASED,
		card_element.MSG_UNRELEASED_DESK,
		card_element.MSG_CANCEL_UNRELEASED,
	):
		assert "clears" not in message and "show as paid" not in message and "settle" not in message
	assert card_element.block_message(card_element.UNRELEASED) == card_element.MSG_UNRELEASED
	assert card_element.block_message(card_element.UNRELEASED, desk=True) == card_element.MSG_UNRELEASED_DESK


# --- the README says what the customer is shown ---------------------------------------------


def test_the_readme_quotes_what_the_customer_sees_while_3ds_is_open():
	"""Decision 2's window: the README said the invoice "shows 'being processed'", while the pages
	say it is waiting for the bank's approval (and keep "being processed" for a payment known to
	be settling). The README quotes what the pages actually show."""
	from pathlib import Path

	root = Path(__file__).resolve().parents[1]

	def text(*parts):
		return " ".join((root.joinpath(*parts)).read_text(encoding="utf-8").split())

	readme = text("stripe_payments", "README.md")
	guard = readme.split("**2. The guard.**", 1)[1].split("**Stripe's 404 is an answer.**", 1)[0]
	assert 'shows "being processed" meanwhile' not in guard
	for shown, page in (
		("Waiting for bank approval…", text("www", "pay.html")),
		(
			"is waiting for your bank's approval. If you did not finish approving it, you can pay again in about",
			text("www", "pay-card.html"),
		),
	):
		assert shown in page and shown in guard, shown

	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import card_element

	assert "waiting for your bank's approval" in card_element.MSG_AWAITING


# --- a refunded payment is never posted, Payment Entry or not ---------------------------------


def test_a_refunded_payment_is_never_posted_whether_or_not_it_has_a_payment_entry(monkeypatch):
	"""Posting failed (no deposit account, a closed period), then Accounts refunded the payment in
	Stripe: the row is Refunded with no Payment Entry. A late or retried checkout.session.completed
	(paid) or async_payment_succeeded still reached finalize_payment — neither handler looked for a
	settled row, and finalize returned early only when a Payment Entry existed — which posted a
	Payment Entry for money given back and marked the row Paid. Both handlers now skip a settled
	row, as payment_intent.succeeded did; and finalize itself, under its lock, refuses a Refunded
	row, Payment Entry or not, and alerts Accounts when there is none
	(test_finalize_refuses_a_refunded_row_and_alerts_when_it_was_never_posted)."""
	for status in ("Paid", "Refunded"):
		for payment_entry in (None, "PE-1"):
			row = dict(_hosted_payment(status=status), payment_entry=payment_entry)
			card_element, writes = _ledger(monkeypatch, [row])
			reconcile = _webhooks(monkeypatch, writes)
			reconcile._on_session_completed(_session_event(payment_status="paid"))
			reconcile._on_session_async_succeeded(_session_event(payment_status="paid"))
			assert writes["finalized"] == [], (status, payment_entry)
			assert writes["store"]["SP-ACH"]["status"] == status, (status, payment_entry)
			assert writes["stamps"] == [], (status, payment_entry)
	# A debit still settling, and a card link just paid, are finalized as before.
	card_element, writes = _ledger(monkeypatch, [_hosted_payment(), _link_sent()])
	reconcile = _webhooks(monkeypatch, writes)
	reconcile._on_session_async_succeeded(_session_event(payment_status="paid"))
	reconcile._on_session_completed(_session_event(sp="SP-LINK", session="cs_link", pi=None, payment_status="paid"))
	assert writes["finalized"] == [("SP-ACH", "cs_ach"), ("SP-LINK", "cs_link")]


def test_finalize_refuses_a_refunded_row_and_alerts_when_it_was_never_posted(monkeypatch):
	"""finalize_payment reads the row again under its lock — the handlers read it earlier,
	unlocked, and a refund can land in between — and returned early for a Refunded row only when
	it had a Payment Entry. Now a Refunded row is never posted and never marked Paid, Payment Entry
	or not; one with none alerts Accounts. A Payment Entry found by the PaymentIntent (the other
	way in to _mark_paid) must not mark it Paid either, so that lookup answers one here."""
	from pathlib import Path

	frappe_stub = install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import reconcile

	class Row(dict):
		__getattr__ = dict.get

	for payment_entry in (None, "PE-1"):
		touched, alerts = [], []

		def get_value(doctype, *a, _pe=payment_entry, _t=touched, **k):
			_t.append(("read", doctype))
			if doctype == "Stripe Payment":
				return Row(status="Refunded", payment_entry=_pe)
			return "PE-FOUND-BY-PI"

		monkeypatch.setattr(
			frappe_stub,
			"db",
			types.SimpleNamespace(get_value=get_value, commit=lambda _t=touched: _t.append("commit")),
		)
		monkeypatch.setattr(reconcile, "_mark_paid", lambda *a, _t=touched, **k: _t.append("mark paid"))
		monkeypatch.setattr(reconcile, "_create_payment_entry", lambda *a, _t=touched, **k: _t.append("create"))
		monkeypatch.setattr(reconcile, "_enrich", lambda *a, _t=touched, **k: _t.append("enrich") or (None, None, None))
		monkeypatch.setattr(
			reconcile,
			"_accounts_notify",
			lambda subject, content, doctype=None, docname=None, _a=alerts: _a.append((subject, content, docname)),
		)
		sp = types.SimpleNamespace(name="SP-R", stripe_payment_intent="pi_r", sales_invoice="SINV-1", customer="CUST-1")
		assert reconcile.finalize_payment(sp, {"object": "payment_intent", "id": "pi_r"}) == payment_entry
		if payment_entry:
			# Posted, then refunded: nothing to do and nothing to say.
			assert touched == [("read", "Stripe Payment")] and alerts == []
		else:
			assert touched == [("read", "Stripe Payment"), "commit"]
			((subject, content, docname),) = alerts
			assert docname == "SP-R" and "SP-R" in subject
			assert "Refunded" in content and "Nothing was posted" in content and "SINV-1" in content

	readme = (Path(__file__).resolve().parents[1] / "stripe_payments" / "README.md").read_text(encoding="utf-8")
	bullet = " ".join(readme.split("- **A `Paid` or `Refunded` row is never re-opened.**", 1)[1].split("\n- ", 1)[0].split())
	assert "Payment Entry or not" in bullet and "alerts Accounts" in bullet


# --- an attempt that could not be released: the card page keeps its form ----------------------


def test_an_unreleased_refusal_keeps_the_card_form_and_says_why(monkeypatch):
	"""An earlier card attempt that cannot charge, whose cancel Stripe would not confirm, was
	refused as plain PaymentBlocked, which the card page answers by reloading. A render never
	releases, so it read that attempt as not blocking and brought the card form back with no
	message: each tap looped until Stripe answered the cancel. The refusal is now
	AttemptUnreleased — still PaymentBlocked, which dunning reschedules on — and the page keeps
	its form and shows MSG_UNRELEASED under it, as for LinkStillOpen
	(scripts/test_web_flow_history.js drives the page through it)."""
	from pathlib import Path

	card_element, writes = _card_page(
		monkeypatch, [_card_attempt(), _quote()], pi_status={"pi_old": "requires_payment_method"}
	)
	_cancel_refused(monkeypatch, card_element, writes, now="requires_payment_method")
	exc = _raised(card_element.confirm_card_payment, stripe_payment="SP-Q", confirmation_token="ct_new")
	assert type(exc) is card_element.AttemptUnreleased and isinstance(exc, card_element.PaymentBlocked)
	assert str(exc) == card_element.MSG_UNRELEASED and writes["charges"] == []
	# The render the page would have reloaded onto: the card form, since a render never releases.
	assert card_element.invoice_payment_block("SINV-1") is None

	# At Continue, the quote: the same refusal, before the card is read.
	card_element, writes = _ledger(monkeypatch, [_card_attempt()], pi_status={"pi_old": "requires_payment_method"})
	_cancel_refused(monkeypatch, card_element, writes, now=None)
	monkeypatch.setattr(card_element, "get_settings", _charge_settings)
	monkeypatch.setattr(card_element, "is_enabled", lambda s=None: True)
	monkeypatch.setattr(card_element, "_resolve_target", lambda *a: ("CUST-1", 200.0, "USD", "Invoice SINV-1"))
	read = []
	monkeypatch.setattr(card_element, "_confirmation_token_method", lambda t: read.append(t) or ("card", "credit"))
	exc = _raised(card_element.price_card_payment, confirmation_token="ct_new", sales_invoice="SINV-1")
	assert type(exc) is card_element.AttemptUnreleased and str(exc) == card_element.MSG_UNRELEASED
	assert read == []

	# Every other verdict is still plain PaymentBlocked: the page reloads, and the render says why.
	card_element, writes = _card_page(monkeypatch, [_card_attempt(), _quote()], pi_status={"pi_old": "succeeded"})
	exc = _raised(card_element.confirm_card_payment, stripe_payment="SP-Q", confirmation_token="ct_new")
	assert type(exc) is card_element.PaymentBlocked and str(exc) == card_element.MSG_IN_FLIGHT

	# The page reloads on the class itself only; frappe names the raised class in exc_type.
	html = (Path(__file__).resolve().parents[1] / "www" / "pay-card.html").read_text(encoding="utf-8")
	assert 'r.exc_type === "PaymentBlocked"' in html
	harness = (Path(__file__).resolve().parents[2] / "scripts" / "test_web_flow_history.js").read_text(encoding="utf-8")
	assert 'exc_type: "AttemptUnreleased"' in harness


# --- off-session holds are worded for what they are -------------------------------------------


def test_off_session_holds_are_worded_for_what_they_are(monkeypatch):
	"""Two off-session rows are held Processing, and so read PROCESSING — "It will show as paid once
	it clears" to the customer, "Wait for it to settle" to Accounts canceling the invoice — though
	neither clears by itself: a card challenge whose cancel could not be proven (poll_pending
	cancels it within the hour) and a bank debit waiting on the customer to verify their account
	(it goes ahead only once they do). The first is now UNRELEASED ("could not be released just
	now"), the second VERIFYING ("waiting for your bank account to be verified")."""
	from pathlib import Path

	unproven = dict(
		_off_session_row("SP-OFF", "pi_off", "card"), error_message=_card_element_note("NOTE_CANCEL_UNPROVEN")
	)
	verifying = dict(
		_off_session_row("SP-ACH", "pi_ach", "us_bank_account"), error_message=_card_element_note("NOTE_ACH_VERIFYING")
	)
	promise = ("clears", "show as paid", "settle")

	card_element, writes = _ledger(monkeypatch, [dict(unproven)])
	verdict = card_element.invoice_payment_block("SINV-1")
	assert verdict == card_element.UNRELEASED and writes["lookups"] == [] and writes["cancels"] == []
	assert card_element.block_message(verdict) == card_element.MSG_UNRELEASED
	assert card_element.block_message(verdict, desk=True) == card_element.MSG_UNRELEASED_DESK
	# A POST refuses it the same way, and the card page keeps its form (AttemptUnreleased).
	card_element, writes = _card_page(monkeypatch, [dict(unproven), _quote()])
	exc = _raised(card_element.confirm_card_payment, stripe_payment="SP-Q", confirmation_token="ct_new")
	assert type(exc) is card_element.AttemptUnreleased and writes["charges"] == []
	# Accounts canceling the invoice: never "wait for it to settle".
	card_element, writes = _ledger(monkeypatch, [dict(unproven)])
	_enabled(monkeypatch, card_element)
	message = str(_raised(_cancel, card_element=card_element))
	assert message == card_element.MSG_CANCEL_UNRELEASED.format(payments="Stripe Payment SP-OFF")
	assert card_element.CANCEL_DETAIL_PROCESSING not in message and writes["commits"] == 0

	card_element, writes = _ledger(monkeypatch, [dict(verifying)])
	verdict = card_element.invoice_payment_block("SINV-1")
	assert verdict == card_element.VERIFYING and writes["lookups"] == [] and writes["cancels"] == []
	for desk in (False, True):
		shown = card_element.block_message(verdict, desk=desk)
		assert shown == (card_element.MSG_VERIFYING_DESK if desk else card_element.MSG_VERIFYING)
		assert "verif" in shown and not any(word in shown for word in promise), desk
	assert "waiting on the customer to verify their bank account" in card_element.MSG_VERIFYING_DESK
	_enabled(monkeypatch, card_element)
	message = str(_raised(_cancel, card_element=card_element))
	assert card_element.CANCEL_DETAIL_VERIFYING in message and card_element.CANCEL_DETAIL_PROCESSING not in message
	assert writes["cancels"] == []  # a pending bank debit is never canceled
	for text in (card_element.MSG_UNRELEASED, card_element.MSG_CANCEL_UNRELEASED, card_element.CANCEL_DETAIL_VERIFYING):
		assert not any(word in text for word in ("clears", "show as paid")), text

	# Beside a payment that is settling, the promise holds for that one, so it is the verdict.
	for row in (unproven, verifying):
		card_element, writes = _ledger(monkeypatch, [dict(row), _hosted_payment(name="SP-HOSTED")])
		assert card_element.invoice_payment_block("SINV-1") == card_element.PROCESSING, row["name"]
	# Beside a charge nobody has an answer for: "do not pay again" wins over "try again shortly".
	card_element, writes = _ledger(monkeypatch, [dict(unproven), _card_attempt(name="SP-U", pi=None)])
	assert card_element.invoice_payment_block("SINV-1") == card_element.UNCONFIRMED
	# The marker on anything but a card: poll_pending never cancels it, so no "try again shortly".
	card_element, writes = _ledger(monkeypatch, [dict(unproven, payment_method_type=None)])
	assert card_element.invoice_payment_block("SINV-1") == card_element.PROCESSING

	# /pay-card renders each as its own state, with a way back and no card form.
	frappe_stub = install_frappe_stub()
	page = _load_pay_card_page()
	settings = types.SimpleNamespace(enabled=1, enable_card=1, enable_ach=0, publishable_key="pk_test_x")
	monkeypatch.setattr(page, "get_settings", lambda: settings)
	monkeypatch.setattr(page, "is_enabled", lambda s=None: True)
	monkeypatch.setattr(page, "get_portal_customers", lambda: ["CUST-1"])
	monkeypatch.setattr(frappe_stub, "session", types.SimpleNamespace(user="jane@example.com"))
	monkeypatch.setattr(frappe_stub, "sessions", types.SimpleNamespace(get_csrf_token=lambda: "tok"), raising=False)
	monkeypatch.setattr(frappe_stub, "form_dict", {"invoice": "SINV-1"}, raising=False)
	invoice = types.SimpleNamespace(
		name="SINV-1", customer="CUST-1", outstanding_amount=200.0, currency="USD", docstatus=1, is_return=0, status="Unpaid"
	)
	monkeypatch.setattr(
		frappe_stub, "db", types.SimpleNamespace(exists=lambda *a, **k: True, get_value=lambda *a, **k: invoice)
	)
	for verdict, settled in (("Verifying", "verifying"), ("Unreleased", "unreleased")):
		monkeypatch.setattr(page, "invoice_payment_block", lambda name, _v=verdict: _v)
		context = types.SimpleNamespace()
		page.get_context(context)
		assert (context.settled, context.invoice, context.settled_invoice) == (settled, None, "SINV-1")
	html = (Path(__file__).resolve().parents[1] / "www" / "pay-card.html").read_text(encoding="utf-8")

	def branch(state):
		return html.split('{% elif settled == "' + state + '" %}', 1)[1].split("{% elif", 1)[0]

	for state in ("verifying", "unreleased"):
		assert not any(word in branch(state) for word in ("clears", "show as paid")), state
		assert 'href="/pay"' in branch(state) and "<script" not in branch(state), state
		assert html.index('settled == "' + state + '"') < html.index("{% elif not invoice %}"), state
	assert "waiting for your bank account to be verified" in branch("verifying")
	assert "could not be released just now" in branch("unreleased")


def _card_element_note(name):
	"""A marker constant from card_element, imported against a fresh stub."""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import card_element

	return getattr(card_element, name)


# --- /stripe-return: an outcome found but not seen to complete is still "checking" -------------


def test_stripe_return_keeps_checking_a_found_attempt_and_its_promise_is_true(monkeypatch):
	"""/stripe-return used its own test of an unknown outcome — Processing with no PaymentIntent
	and no Session — so once poll_pending found the PaymentIntent (3-D Secure the payer never saw,
	say) and recorded it with NOTE_OUTCOME_FOUND, a revisit switched from "Checking your payment"
	to "Thank you! Your payment is being processed", about an attempt released half an hour later
	with an email that nothing was charged. It now asks card_element.held_unknown while the row is
	Processing. And its promise is worded for both ways the payer can learn it did not go through:
	an email, or — when it is their own new payment that releases it, which emails nobody — the
	invoice they found ready to pay again."""
	frappe_stub = install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import card_element

	found = card_element.NOTE_OUTCOME_FOUND
	page = _load_www_page("stripe_return.py", "ee_test_stripe_return_found")
	rows = {
		"SP-FOUND": types.SimpleNamespace(
			status="Processing", stripe_payment_intent="pi_found", stripe_checkout_session=None, error_message=found
		),
		"SP-U": types.SimpleNamespace(
			status="Processing", stripe_payment_intent=None, stripe_checkout_session=None, error_message=None
		),
		"SP-P": types.SimpleNamespace(
			status="Processing", stripe_payment_intent="pi_1", stripe_checkout_session=None, error_message=None
		),
		"SP-ACH": types.SimpleNamespace(
			status="Processing", stripe_payment_intent="pi_2", stripe_checkout_session="cs_2", error_message=None
		),
		"SP-GONE": types.SimpleNamespace(
			status="Failed", stripe_payment_intent="pi_3", stripe_checkout_session=None, error_message=found
		),
	}
	fields = []

	def get_value(doctype, name=None, fieldname=None, *a, **k):
		fields.append(fieldname)
		return rows.get(name)

	monkeypatch.setattr(
		frappe_stub,
		"db",
		types.SimpleNamespace(exists=lambda doctype, name=None, *a, **k: name in rows, get_value=get_value),
	)
	contexts = {}
	for name in rows:
		monkeypatch.setattr(frappe_stub, "form_dict", {"status": "success", "sp": name}, raising=False)
		context = types.SimpleNamespace()
		page.get_context(context)
		contexts[name] = (context.payment_status, context.unconfirmed)
	assert contexts == {
		"SP-FOUND": ("Processing", True),
		"SP-U": ("Processing", True),
		"SP-P": ("Processing", False),
		"SP-ACH": ("Processing", False),
		"SP-GONE": ("Failed", False),
	}
	assert all("error_message" in f for f in fields)

	checking = _render_stripe_return(outcome="success", payment_status="Processing", unconfirmed=True)
	assert "Checking your payment" in checking and "do not pay again" in checking
	assert "we will email you" in checking and "ready to pay again" in checking
	assert "being processed" not in checking and "Thank you" not in checking


# --- the cancel hook's not-charged notice ---------------------------------------------------


def test_the_not_charged_notice_from_a_cancel_is_worded_for_a_canceled_invoice(monkeypatch):
	"""Canceling an invoice releases a dead card attempt, and when that attempt had been held with
	an unknown outcome its payer is emailed and Accounts alerted. That notice told the payer "You can
	pay it from your invoices" and Accounts "the invoice can be paid again" — about the invoice being
	canceled. It is now worded for a canceled invoice; everywhere else it is as it was. And the email
	quoted the invoice amount without the surcharge, so it could differ from the total the payer
	approved: it now quotes amount plus surcharge_amount."""
	held = dict(
		_card_attempt(name="SP-U"),
		initiated_by="jane@example.com",
		error_message=_card_element_note("NOTE_OUTCOME_FOUND"),
		surcharge_amount=6.0,
	)
	card_element, writes = _ledger(
		monkeypatch, [dict(held)], pi_status={"pi_old": "requires_payment_method"}, cancel={"pi_old": "canceled"}
	)
	_enabled(monkeypatch, card_element)
	assert _cancel(card_element) is None
	assert writes["store"]["SP-U"]["status"] == "Failed" and writes["commits"] == 0
	assert writes["emails"] == [("SP-U", "jane@example.com", "invoice SINV-1")]
	assert writes["email_details"] == [("USD 206.00", True)]
	(body,) = writes["alert_bodies"]
	assert "released as the invoice was canceled" in body and "can be paid again" not in body
	assert "USD 206.00" in body

	# Released by the next payment someone else starts: as it was, and the total quoted.
	card_element, writes = _ledger(
		monkeypatch, [dict(held)], pi_status={"pi_old": "requires_payment_method"}, cancel={"pi_old": "canceled"}
	)
	assert card_element.invoice_payment_block("SINV-1", release=True) is None
	assert writes["email_details"] == [("USD 206.00", False)]
	(body,) = writes["alert_bodies"]
	assert "can be paid again" in body and "canceled" not in body


def test_the_not_charged_email_is_worded_for_a_canceled_invoice(monkeypatch):
	"""The email itself: after a cancel it says the invoice was canceled and no payment is due —
	never "You can pay it from your invoices" — and quotes the amount it is given (the total the
	payer approved, surcharge included: see the test above)."""
	import erpnext_enhancements

	frappe_stub = install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import card_element

	sent = []
	fake_style = types.SimpleNamespace(
		p=lambda text: f"<p>{text}</p>",
		button=lambda url, label, tone="primary": f"<a href='{url}'>{label}</a>",
		button_fallback=lambda url: "",
		wrap=lambda body, **k: f"[{k.get('title')}]{body}",
	)
	monkeypatch.setattr(erpnext_enhancements, "email_style", fake_style, raising=False)
	monkeypatch.setattr(frappe_stub, "sendmail", lambda **k: sent.append(k), raising=False)
	monkeypatch.setattr(frappe_stub, "db", types.SimpleNamespace(get_value=lambda doctype, name=None, field=None, **k: name))
	sp = types.SimpleNamespace(name="SP-U", get=lambda key, default=None: {"initiated_by": "jane@example.com"}.get(key, default))
	for canceled in (True, False):
		sent.clear()
		card_element._email_payer_not_charged(sp, "USD 206.00", "invoice SINV-1", invoice_canceled=canceled)
		((mail,),) = [(m,) for m in sent]
		assert "USD 206.00" in mail["message"] and "Nothing was charged" in mail["message"]
		if canceled:
			assert "canceled" in mail["message"] and "no payment is due" in mail["message"]
			assert "pay it from your invoices" not in mail["message"] and "paid again" not in mail["message"]
		else:
			assert "pay it from your invoices" in mail["message"] and "canceled" not in mail["message"]


# --- the desk's ad hoc Checkout link -----------------------------------------------------------


def test_the_desk_ad_hoc_link_waits_for_payments_in_flight(monkeypatch):
	"""The ad hoc off-session charge was refused while the customer had a payment settling or a link
	open, because Accounts reach for it to pay a bill already being paid; the desk's ad hoc Checkout
	link for an amount (api.create_adhoc_payment) has the same failure mode — it is applied to no
	invoice — and had no guard at all. It now runs the same rule, before anything exists on the
	ledger or at Stripe, and the README's table of payment paths lists it."""
	from pathlib import Path

	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import api

	for open_row in (_hosted_payment(), _link_sent()):
		card_element, writes = _ledger(monkeypatch, [open_row])
		checkout = _hosted_checkout(monkeypatch, writes)
		for call in (
			lambda: checkout.create_payment(customer="CUST-1", amount=500, channel="Desk"),
			lambda: api.create_adhoc_payment("CUST-1", 500),
		):
			exc = _raised(call)
			assert isinstance(exc, card_element.PaymentBlocked) and open_row["name"] in str(exc), open_row["name"]
		assert writes["steps"] == [] and writes["cancels"] == [] and writes["set_value"] == []
	assert "link is paid or expires" in card_element.MSG_AD_HOC_BESIDE_OPEN

	# Nothing in flight (a Paid or Failed row is not): the link goes ahead.
	card_element, writes = _ledger(monkeypatch, [_hosted_payment(status="Paid"), _card_attempt(name="SP-F", status="Failed")])
	checkout = _hosted_checkout(monkeypatch, writes)
	result = checkout.create_payment(customer="CUST-1", amount=50, channel="Desk")
	assert result["checkout_url"] and "checkout session" in [name for name, _ in writes["steps"]]

	readme = (Path(__file__).resolve().parents[1] / "stripe_payments" / "README.md").read_text(encoding="utf-8")
	table = readme.split("| Path | Entry point | Starts |", 1)[1].split("\n\n", 1)[0]
	(row,) = [line for line in table.splitlines() if "create_adhoc_payment" in line]
	assert "`checkout.create_payment`" in row and "refused" in row


# --- the invoice PDF on /pay and /pay-card ------------------------------------------------------


class _AttrDict(dict):
	"""``frappe._dict``: a dict whose keys read as attributes, and None for a key it lacks."""

	def __getattr__(self, key):
		if key.startswith("__"):
			raise AttributeError(key)
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


def _refuse_set_user(*args, **kwargs):
	"""``frappe.set_user`` on a web request replaces the session: the customer's next request
	finds itself logged out. Nothing on the invoice PDF path may call it."""
	raise AssertionError("frappe.set_user must never be called for a portal PDF")


def _pdf_request(monkeypatch, frappe_stub, **form):
	"""A ``frappe.local`` for an HTTP request whose query string is ``form``."""
	local = types.SimpleNamespace(flags=_AttrDict(), form_dict=_AttrDict(form), response=_AttrDict())
	monkeypatch.setattr(frappe_stub, "local", local, raising=False)
	monkeypatch.setattr(frappe_stub, "_dict", _AttrDict, raising=False)
	monkeypatch.setattr(frappe_stub, "set_user", _refuse_set_user, raising=False)
	return local


def _pdf_links(html):
	"""Every "View invoice (PDF)" anchor in ``html``, as its attribute dict."""
	from html.parser import HTMLParser

	class Links(HTMLParser):
		def __init__(self):
			super().__init__()
			self.found, self._open = [], None

		def handle_starttag(self, tag, attrs):
			if tag == "a":
				self._open = dict(attrs)

		def handle_data(self, data):
			if self._open is not None and data.strip() == "View invoice (PDF)":
				self.found.append(self._open)

		def handle_endtag(self, tag):
			if tag == "a":
				self._open = None

	parser = Links()
	parser.feed(html)
	return parser.found


def test_the_invoice_pdf_is_only_ever_the_customers_own_submitted_invoice(monkeypatch):
	"""Nik: "On the /pay page they should see a PDF copy of their invoice available to review."
	portal_invoice_pdf serves an invoice only when the signed-in user's Customer owns it (the rule
	/pay and /pay-card use: one of get_portal_customers()) and it is submitted — a draft is not yet
	the customer's bill and a canceled one no longer is. Someone else's, a draft, a canceled one, a
	missing name and a Guest all get the very same page, which never echoes the name, so the
	answer is no oracle for which invoices exist. A credit note that is theirs is theirs to read.

	The same answer must also cost the same work, or the response time is the oracle. The first
	cut looked the invoice up by name and ran get_portal_customers() (Contact, Dynamic Link,
	sometimes Contact Email) only when it existed and was submitted, so a missing or draft name
	came back faster than someone else's submitted invoice. Now the user's customers come first,
	whatever was asked for, and the invoice is one query carrying all three conditions: every name
	makes the very same calls."""
	frappe_stub = install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import api, invoice_pdf

	rows = [
		dict(name="SINV-OWN", customer="CUST-1", docstatus=1),
		dict(name="SINV-RETURN", customer="CUST-1", docstatus=1),
		dict(name="SINV-OTHER", customer="CUST-2", docstatus=1),
		dict(name="SINV-DRAFT", customer="CUST-1", docstatus=0),
		dict(name="SINV-CANCELED", customer="CUST-1", docstatus=2),
		dict(name="SINV-NOBODY", customer=None, docstatus=1),
	]
	calls = []

	def get_value(doctype, filters=None, fieldname=None, *args, **kwargs):
		calls.append(("get_value", doctype, sorted(filters), fieldname))
		assert doctype == "Sales Invoice" and not kwargs.get("as_dict")
		assert filters["docstatus"] == 1 and filters["customer"][0] == "in"
		customers = filters["customer"][1]
		for row in rows:
			# MariaDB matches a name case-insensitively; the stored name comes back.
			if (
				row["name"].lower() == filters["name"].lower()
				and row["customer"] in customers
				and row["docstatus"] == filters["docstatus"]
			):
				return row["name"]
		return None

	portal_customers = {"jane@example.com": ["CUST-1"], "no-customer@example.com": []}

	def get_portal_customers(user=None):
		calls.append(("get_portal_customers",))
		return list(portal_customers[frappe_stub.session.user])

	monkeypatch.setattr(frappe_stub, "db", types.SimpleNamespace(get_value=get_value))
	monkeypatch.setattr(api, "get_portal_customers", get_portal_customers)
	monkeypatch.setattr(frappe_stub, "session", types.SimpleNamespace(user="jane@example.com"))
	_pdf_request(monkeypatch, frappe_stub)
	served, pages = [], []
	monkeypatch.setattr(invoice_pdf, "respond_with_pdf", lambda name: served.append(name))
	monkeypatch.setattr(
		frappe_stub, "respond_as_web_page", lambda *a, **k: pages.append((a, k)), raising=False
	)

	def call(invoice):
		served.clear()
		pages.clear()
		calls.clear()
		# None: a return value would become a JSON body in place of the PDF or the page.
		assert api.portal_invoice_pdf(invoice) is None
		return list(served), list(pages)

	one_shape = [
		("get_portal_customers",),
		("get_value", "Sales Invoice", ["customer", "docstatus", "name"], "name"),
	]
	assert call("SINV-OWN") == (["SINV-OWN"], [])
	assert calls == one_shape
	assert call("sinv-own") == (["SINV-OWN"], [])  # rendered under its stored name
	assert call("SINV-RETURN") == (["SINV-RETURN"], [])

	refusals = {}
	for invoice in ("SINV-OTHER", "SINV-DRAFT", "SINV-CANCELED", "SINV-NOBODY", "SINV-MISSING"):
		refused_served, refused_pages = call(invoice)
		assert refused_served == [], invoice
		# The work does not depend on the answer: the same two calls as for the customer's own.
		assert calls == one_shape, invoice
		refusals[repr(invoice)] = refused_pages
	# Not a name at all: refused on the shape of the request, before any lookup.
	for invoice in ("", None, ["SINV-OWN"]):
		refused_served, refused_pages = call(invoice)
		assert refused_served == [] and calls == [], invoice
		refusals[repr(invoice)] = refused_pages
	assert len({repr(page) for page in refusals.values()}) == 1, refusals
	((args, kwargs),) = refusals["'SINV-OTHER'"]
	assert kwargs["http_status_code"] == 404 and kwargs["primary_action"] == "/pay"
	assert "not available" in args[1] and not any("SINV" in str(part) for part in args)

	# A signed-in user no Customer is linked to: refused once the (empty) customers are known.
	monkeypatch.setattr(frappe_stub, "session", types.SimpleNamespace(user="no-customer@example.com"))
	assert call("SINV-OWN") == ([], refusals["'SINV-OTHER'"])
	assert calls == [("get_portal_customers",)]

	# A Guest is refused before anything is looked up.
	monkeypatch.setattr(frappe_stub, "session", types.SimpleNamespace(user="Guest"))
	assert call("SINV-OWN") == ([], refusals["'SINV-OTHER'"])
	assert calls == []


def test_the_invoice_pdf_endpoint_is_a_signed_in_get():
	"""A link, so GET (no CSRF token; Frappe rolls a GET's transaction back), never allow_guest,
	and the ownership helper above it is not itself exposed. Both pages point at it. The render's
	concurrency limit is explicit and small: Frappe's limiter keys its pool by the wrapped
	function and defaults each pool to half the web tier, so a defaulted limit here would sit
	beside download_pdf's half and the two together could hold every worker."""
	import ast
	from pathlib import Path

	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import invoice_pdf

	def functions(path):
		tree = ast.parse(Path(path).read_text(encoding="utf-8"))
		return {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}

	api_defs = functions(Path(__file__).resolve().parents[1] / "stripe_payments" / "core" / "api.py")
	(decorator,) = api_defs["portal_invoice_pdf"].decorator_list
	assert ast.unparse(decorator.func) == "frappe.whitelist"
	assert {kw.arg: ast.literal_eval(kw.value) for kw in decorator.keywords} == {"methods": ["GET"]}
	assert api_defs["_own_submitted_invoice"].decorator_list == []
	assert (
		invoice_pdf.PDF_ROUTE
		== "/api/method/erpnext_enhancements.stripe_payments.core.api.portal_invoice_pdf"
	)
	render = functions(invoice_pdf.__file__)["render_invoice_pdf"]
	assert [ast.unparse(d) for d in render.decorator_list] == [
		"_concurrent_limit(limit=RENDER_CONCURRENCY, wait_timeout=RENDER_WAIT_SECONDS)"
	]
	assert invoice_pdf.RENDER_CONCURRENCY == 2 and invoice_pdf.RENDER_WAIT_SECONDS == 3


def test_the_invoice_pdf_is_always_the_customer_facing_format_and_never_standard(monkeypatch):
	"""The portal renders SALES_INVOICE_FORMAT ("Sales Invoice - Sapphire", which
	enhancements_core.setup_sales_print_formats writes on every migrate) and nothing else. Not the
	DocType's default_print_format, which a staffer can point at any format, an internal one
	included; and never Standard, printview's last resort, which on v16 prints every permlevel-0
	field with a value and no print_hide (cost_center, amount_eligible_for_commission,
	is_internal_customer, any custom field made on the site). The first cut followed the default
	and fell back to Standard, which fails open on a page customers see. If the Sapphire format is
	missing, disabled or made for another doctype, the render raises, the customer gets the logged
	500 page, and nothing is printed at all.

	The generator is the format's own, else Print Settings', else wkhtmltopdf: the Desk's Download
	PDF order (print.js get_pdf_generator), not get_print's, which skips Print Settings and would
	send the render to the wkhtmltopdf that segfaults on this host. Print Settings is a Single,
	read with get_single_value — never db.get_value("Singles", ...)."""
	import json
	from pathlib import Path

	import pytest

	frappe_stub = install_frappe_stub()
	from erpnext_enhancements.enhancements_core import setup_sales_print_formats
	from erpnext_enhancements.stripe_payments.core import invoice_pdf

	sapphire = "Sales Invoice - Sapphire"
	assert invoice_pdf.SALES_INVOICE_FORMAT == setup_sales_print_formats.SALES_INVOICE_FORMAT == sapphire
	monkeypatch.setattr(invoice_pdf, "render_language", lambda: "en")
	good = dict(doc_type="Sales Invoice", disabled=0, pdf_generator="chrome")
	broken_rows = {
		"missing": None,
		"disabled": dict(good, disabled=1),
		"another doctype's": dict(good, doc_type="Quotation"),
	}
	formats = {}
	state = {"settings": None}
	read = []

	def get_value(doctype, name=None, fields=None, as_dict=False, **kwargs):
		assert doctype == "Print Format", doctype
		read.append(name)
		row = formats.get(name)
		if row is None:
			return None
		return _AttrDict({f: row[f] for f in fields}) if as_dict else row[fields]

	def get_single_value(doctype, field, *args, **kwargs):
		assert (doctype, field) == ("Print Settings", "pdf_generator")
		return state["settings"]

	def get_meta(doctype):
		raise AssertionError("the DocType's default print format must not decide what customers see")

	monkeypatch.setattr(
		frappe_stub, "db", types.SimpleNamespace(get_value=get_value, get_single_value=get_single_value)
	)
	monkeypatch.setattr(frappe_stub, "get_meta", get_meta, raising=False)

	def install(row):
		formats.clear()
		if row is not None:
			formats[sapphire] = row

	def resolve(row, settings=None):
		install(row)
		state["settings"] = settings
		read.clear()
		print_format = invoice_pdf.invoice_print_format()
		return print_format, invoice_pdf.pdf_generator_for(print_format)

	assert resolve(good, settings="wkhtmltopdf") == (sapphire, "chrome")
	assert set(read) == {sapphire}  # no other format, and never "Standard", is even looked at
	assert resolve(dict(good, pdf_generator=None), settings="chrome") == (sapphire, "chrome")
	assert resolve(dict(good, pdf_generator=None)) == (sapphire, "wkhtmltopdf")
	for why, row in broken_rows.items():
		with pytest.raises(invoice_pdf.PrintFormatUnavailable):
			resolve(row, settings="chrome")
		assert set(read) == {sapphire}, why

	# Through the response: the logged 500 page, and get_print — which would render Standard for a
	# format it cannot find — is never reached.
	local = _pdf_request(monkeypatch, frappe_stub)
	pages, logged, printed = [], [], []
	monkeypatch.setattr(
		frappe_stub, "respond_as_web_page", lambda *a, **k: pages.append((a, k)), raising=False
	)
	monkeypatch.setattr(frappe_stub, "log_error", lambda *a, **k: logged.append(k))
	monkeypatch.setattr(
		frappe_stub, "get_print", lambda *a, **k: printed.append(k) or b"%PDF-1.7", raising=False
	)
	for why, row in broken_rows.items():
		install(row)
		pages.clear()
		logged.clear()
		invoice_pdf.respond_with_pdf("SINV-1")
		assert printed == [], why
		assert "type" not in local.response and "filecontent" not in local.response, why
		((args, kwargs),) = pages
		assert kwargs["http_status_code"] == 500 and "try again" in args[1], why
		assert len(logged) == 1 and logged[0]["reference_name"] == "SINV-1", why

	install(good)
	pages.clear()
	invoice_pdf.respond_with_pdf("SINV-1")
	(kwargs,) = printed
	assert kwargs["print_format"] == sapphire and pages == []
	assert local.response.type == "pdf"

	# On this site the Desk's default is the same format (the fixture's Property Setter), so the
	# customer sees what the Desk prints.
	app = Path(__file__).resolve().parents[1]
	setters = json.loads((app / "fixtures" / "property_setter.json").read_text(encoding="utf-8"))
	(setter,) = [
		row
		for row in setters
		if row.get("doc_type") == "Sales Invoice" and row.get("property") == "default_print_format"
	]
	assert setter["value"] == sapphire

	assert invoice_pdf.pdf_filename("ACC-SINV-2026-00001") == "ACC-SINV-2026-00001.pdf"
	assert invoice_pdf.pdf_filename("SINV 7/A") == "SINV-7-A.pdf"  # Frappe's own download_pdf rule


def test_the_invoice_pdf_asks_for_no_letter_head_because_the_format_never_prints_one():
	"""The Desk's print view sends the invoice's own letter head if it is enabled, else the default
	enabled one, with no_letterhead=0 (print.js set_default_letterhead / render_page, version-16;
	Print Settings.with_letterhead is never read there). The first cut's docstring said otherwise.
	What makes it moot: the Sapphire format is a custom format, so printview hands a letter head
	only to the template, as letter_head and footer, and the template never reads either — it
	draws its own. Nor does it call _(), so the Desk's print language (doc.language first) makes
	no difference either. The render asks for no letter head and the page is the Desk's. If the
	template ever starts reading one of these, the render has to start matching print.js."""
	import re

	install_frappe_stub()
	from erpnext_enhancements.enhancements_core import setup_sales_print_formats

	((template,),) = [
		(html,)
		for name, doctype, html in setup_sales_print_formats.FORMATS
		if name == setup_sales_print_formats.SALES_INVOICE_FORMAT
	]
	assert "{{ doc.name }}" in template  # the real template, not an empty string
	for pattern in (r"\bletter_head\b", r"\bno_letterhead\b", r"\bfooter\b", r"(?<![\w.])_\("):
		assert not re.search(pattern, template), pattern


def test_the_invoice_pdf_render_waives_print_permission_for_that_render_only(monkeypatch):
	"""A Website User has no Read or Print on Sales Invoice, and ERPNext's portal rule looks for the
	user in the Customer's Portal Users table, not in a Contact's links as this portal does — so
	printview would refuse customers their own invoices. After the endpoint's ownership check the
	render runs with flags.ignore_print_permissions (what Frappe's attach_print sets), never
	frappe.set_user, and puts the flag back even when the render fails. The request's own query
	string never reaches printview, which reads form_dict: ?pdf_generator= would pick the
	generator, ?settings= would reach Print Settings (allow_print_for_draft) and ?key= a share key.
	?_lang= is read into frappe.local.lang before the endpoint runs, and printview renders in it
	(right to left for ar), so the render runs in the user's own language and puts the request's back."""
	import pytest

	frappe_stub = install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import invoice_pdf

	local = _pdf_request(
		monkeypatch,
		frappe_stub,
		cmd="erpnext_enhancements.stripe_payments.core.api.portal_invoice_pdf",
		invoice="SINV-1",
		pdf_generator="wkhtmltopdf",
		settings='{"allow_print_for_draft": 1}',
		key="shared-key",
		format="Standard",
		_lang="ar",
	)
	local.lang = "ar"  # what frappe.auth set from ?_lang= when the request began
	request_form = local.form_dict
	monkeypatch.setattr(invoice_pdf, "invoice_print_format", lambda: "Sales Invoice - Sapphire")
	monkeypatch.setattr(invoice_pdf, "pdf_generator_for", lambda print_format: "chrome")
	real_render_language = invoice_pdf.render_language
	monkeypatch.setattr(invoice_pdf, "render_language", lambda: "en")
	seen = []

	def get_print(doctype, name, **kwargs):
		seen.append(
			dict(
				doctype=doctype,
				name=name,
				kwargs=kwargs,
				waived=local.flags.get("ignore_print_permissions"),
				form=dict(local.form_dict),
				lang=local.lang,
			)
		)
		return b"%PDF-1.7 SINV-1"

	monkeypatch.setattr(frappe_stub, "get_print", get_print, raising=False)
	assert invoice_pdf.render_invoice_pdf("SINV-1") == b"%PDF-1.7 SINV-1"
	(call,) = seen
	assert (call["doctype"], call["name"]) == ("Sales Invoice", "SINV-1")
	# no_letterhead=1: the format draws its own letterhead and never reads Frappe's (the test
	# above), so the page is the Desk's without one.
	assert call["kwargs"] == dict(
		print_format="Sales Invoice - Sapphire", as_pdf=True, no_letterhead=1, pdf_generator="chrome"
	)
	assert call["waived"] is True and call["form"] == {} and call["lang"] == "en"
	assert local.flags.get("ignore_print_permissions") is None
	assert local.form_dict is request_form and request_form.pdf_generator == "wkhtmltopdf"
	assert local.lang == "ar"

	# A failed render puts both back, and leaves a waiver someone else set as it was.
	local.flags.ignore_print_permissions = "outer"

	def chrome_died(*args, **kwargs):
		raise RuntimeError("Chromium took too long to start.")

	monkeypatch.setattr(frappe_stub, "get_print", chrome_died, raising=False)
	with pytest.raises(RuntimeError):
		invoice_pdf.render_invoice_pdf("SINV-1")
	assert local.flags.ignore_print_permissions == "outer" and local.form_dict is request_form
	assert local.lang == "ar"

	# The language is the user's own, as Frappe picks it when a session begins, never ?_lang=.
	translate = types.ModuleType("frappe.translate")
	asked = []
	translate.get_user_lang = lambda user=None: asked.append(user) or "de"
	monkeypatch.setitem(sys.modules, "frappe.translate", translate)
	monkeypatch.setattr(frappe_stub, "translate", translate, raising=False)
	monkeypatch.setattr(frappe_stub, "session", types.SimpleNamespace(user="customer@example.com"), raising=False)
	assert real_render_language() == "de" and asked == ["customer@example.com"]

	# The limiter is Frappe's, given our limits, where there is one, and nothing where there is not.
	monkeypatch.delattr(frappe_stub, "concurrent_limit", raising=False)
	assert invoice_pdf._concurrent_limit(limit=2, wait_timeout=3)(chrome_died) is chrome_died
	monkeypatch.setattr(
		frappe_stub, "concurrent_limit", lambda **kw: (lambda fn: ("limited", kw, fn)), raising=False
	)
	assert invoice_pdf._concurrent_limit(limit=2, wait_timeout=3)(chrome_died) == (
		"limited",
		{"limit": 2, "wait_timeout": 3},
		chrome_died,
	)


def test_the_invoice_pdf_is_capped_per_customer_before_it_waits_for_a_slot(monkeypatch):
	"""Nothing stopped one portal customer looping on the link: the render's pool was half the web
	tier, and a refused caller held a worker for up to 10 seconds first. Now the pool is small (the
	test above) and each signed-in user may start USER_RENDER_LIMIT renders per USER_RENDER_WINDOW
	seconds: a counter in the Redis cache keyed on the session user, since frappe.rate_limit keys
	only on the client IP or a request value. It is counted before the render and its semaphore,
	so a refused request never waits for a slot. The refusal is the busy page (503, Retry-After),
	not logged. Another user's count is their own, the next window starts afresh, and outside an
	HTTP request it is a no-op, like Frappe's limiter."""
	import pytest

	frappe_stub = install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import invoice_pdf

	class Cache:
		def __init__(self):
			self.counts, self.ttl = {}, {}

		def make_key(self, key, user=None, shared=False):
			return (f"site|user:{user}:{key}" if user else f"site|{key}").encode()

		def incrby(self, key, amount=1):
			self.counts[key] = self.counts.get(key, 0) + amount
			return self.counts[key]

		def expire(self, key, seconds):
			self.ttl[key] = seconds

	class Headers(dict):
		def set(self, key, value):
			self[key] = value

	cache = Cache()
	monkeypatch.setattr(frappe_stub, "cache", cache, raising=False)
	local = _pdf_request(monkeypatch, frappe_stub)
	local.response_headers = Headers()
	window = invoice_pdf.USER_RENDER_WINDOW
	clock = {"now": 29_000_000 * window + 15}  # 15 seconds into a window
	monkeypatch.setattr(invoice_pdf, "time", types.SimpleNamespace(time=lambda: clock["now"]))

	def as_user(user):
		monkeypatch.setattr(frappe_stub, "session", types.SimpleNamespace(user=user))

	as_user("jane@example.com")
	# Not an HTTP request (a job, the console): nothing is counted.
	invoice_pdf.count_render_for_user()
	assert cache.counts == {}

	local.request = object()
	for _ in range(invoice_pdf.USER_RENDER_LIMIT):
		invoice_pdf.count_render_for_user()
	with pytest.raises(invoice_pdf.RenderLimitReached) as refused:
		invoice_pdf.count_render_for_user()
	assert refused.value.retry_after == window - 15
	assert local.response_headers == {"Retry-After": str(window - 15)}
	(key,) = cache.counts
	assert b"user:jane@example.com:" in key and cache.ttl[key] == window
	assert 1 < invoice_pdf.USER_RENDER_LIMIT <= 20 and window == 60

	as_user("joe@example.com")
	invoice_pdf.count_render_for_user()  # a cap of his own

	as_user("jane@example.com")
	clock["now"] += window
	invoice_pdf.count_render_for_user()  # the next window starts afresh
	clock["now"] -= window

	# Through the response, back in the spent window: the busy page, not logged, and the render —
	# with its semaphore — never entered.
	pages, logged, rendered = [], [], []
	monkeypatch.setattr(
		frappe_stub, "respond_as_web_page", lambda *a, **k: pages.append((a, k)), raising=False
	)
	monkeypatch.setattr(frappe_stub, "log_error", lambda *a, **k: logged.append(k))
	monkeypatch.setattr(invoice_pdf, "render_invoice_pdf", lambda name: rendered.append(name) or b"%PDF")
	invoice_pdf.respond_with_pdf("SINV-1")
	assert rendered == [] and logged == []
	((args, kwargs),) = pages
	assert kwargs["http_status_code"] == 503 and "try again" in args[1]
	assert "type" not in local.response and "filecontent" not in local.response


def test_the_invoice_pdf_opens_inline_or_says_it_could_not(monkeypatch):
	"""Frappe's type "pdf" response is Content-Disposition: inline (utils/response.py as_pdf,
	version-16) — the phone's PDF viewer, not a download — named for the invoice. A render that
	fails is a page saying to try again, with the traceback in the Error Log through defer_insert
	(a GET's transaction is rolled back, and an ordinary insert with it); a refusal from either
	limiter (both carry retry_after) is the same page with a 503, and is not logged."""
	frappe_stub = install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import invoice_pdf

	local = _pdf_request(monkeypatch, frappe_stub)
	pages, logged = [], []
	monkeypatch.setattr(
		frappe_stub, "respond_as_web_page", lambda *a, **k: pages.append((a, k)), raising=False
	)
	monkeypatch.setattr(frappe_stub, "log_error", lambda *a, **k: logged.append(k))
	monkeypatch.setattr(invoice_pdf, "render_invoice_pdf", lambda name: b"%PDF-1.7 " + name.encode())

	invoice_pdf.respond_with_pdf("ACC-SINV-2026-00001")
	assert local.response == {
		"filename": "ACC-SINV-2026-00001.pdf",
		"filecontent": b"%PDF-1.7 ACC-SINV-2026-00001",
		"type": "pdf",
	}
	assert pages == [] and logged == []

	class Busy(Exception):
		retry_after = 10

	def fails_with(exc):
		def render(name):
			raise exc

		return render

	for render, status, logs in (
		(fails_with(RuntimeError("Chromium took too long to start.")), 500, True),
		(lambda name: b"", 500, True),  # an empty PDF is no PDF
		(fails_with(invoice_pdf.PrintFormatUnavailable("gone")), 500, True),
		(fails_with(Busy("Server is busy. Please try again in a few seconds.")), 503, False),
		(fails_with(invoice_pdf.RenderLimitReached(42)), 503, False),
	):
		local.response = _AttrDict()
		pages.clear()
		logged.clear()
		monkeypatch.setattr(invoice_pdf, "render_invoice_pdf", render)
		invoice_pdf.respond_with_pdf("SINV-<b>1</b>")
		assert "type" not in local.response and "filecontent" not in local.response
		((args, kwargs),) = pages
		assert kwargs["http_status_code"] == status and kwargs["primary_action"] == "/pay"
		assert "try again" in args[1] and "SINV-&lt;b&gt;1&lt;/b&gt;" in args[1]
		if logs:
			((log,),) = [(entry,) for entry in logged]
			assert log["defer_insert"] is True
			assert (log["reference_doctype"], log["reference_name"]) == ("Sales Invoice", "SINV-<b>1</b>")
		else:
			assert logged == []


def test_pay_offers_the_invoice_pdf_on_every_row():
	"""/pay: "View invoice (PDF)" under every invoice's name, whatever its payment state — payable,
	processing, waiting on the bank, a payment received — in a new tab (the phone's PDF viewer, and
	the list stays where it was), with rel=noopener."""
	from pathlib import Path

	import pytest

	jinja2 = pytest.importorskip("jinja2")
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import invoice_pdf

	source = (Path(__file__).resolve().parents[1] / "www" / "pay.html").read_text(encoding="utf-8")
	env = jinja2.Environment(
		loader=jinja2.DictLoader(
			{"templates/web.html": "{% block page_content %}{% endblock %}", "pay.html": source}
		)
	)
	env.globals["_"] = lambda text, *a, **k: text
	states = {
		"SINV-OPEN": None,
		"SINV-SETTLING": "Processing",
		"SINV-3DS": "Awaiting",
		"SINV-RECEIVED": "Paid",
		"SINV 7": None,
	}
	html = env.get_template("pay.html").render(
		enabled=True,
		invoices=[
			dict(name=name, due_display="—", amount_display="USD 200.00", payment_block=block)
			for name, block in states.items()
		],
		enable_card=True,
		enable_ach=True,
		autopay_consent="",
		autopay_enrolled=False,
		csrf_token="tok",
	)
	links = _pdf_links(html)
	assert [link["href"] for link in links] == [
		invoice_pdf.PDF_ROUTE + "?invoice=" + name.replace(" ", "%20") for name in states
	]
	for link in links:
		assert (link["target"], link["rel"]) == ("_blank", "noopener"), link
	for chunk in html.split("<tr>")[2:]:
		assert chunk.split("</tr>", 1)[0].count("View invoice (PDF)") == 1

	# No invoices, or payments switched off: no list, so no link.
	for context in (dict(enabled=True, invoices=[]), dict(enabled=False, invoices=[])):
		assert _pdf_links(env.get_template("pay.html").render(**context)) == []


def test_pay_card_offers_the_invoice_pdf_in_every_state_that_names_an_invoice():
	"""/pay-card: the same link beside the invoice header on the card form, and on every settled
	state (paid, nothing left to pay, received, processing, awaiting, unconfirmed, verifying,
	unreleased), each of which names an invoice the controller has already proven is this
	customer's and submitted. Not on "not available", which names none, nor with card payments off."""
	from pathlib import Path

	import pytest

	jinja2 = pytest.importorskip("jinja2")
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core import invoice_pdf

	source = (Path(__file__).resolve().parents[1] / "www" / "pay-card.html").read_text(encoding="utf-8")
	env = jinja2.Environment(
		loader=jinja2.DictLoader(
			{"templates/web.html": "{% block page_content %}{% endblock %}", "r.html": source}
		)
	)
	env.globals["_"] = lambda text, *a, **k: text

	def render(**context):
		return env.get_template("r.html").render(csrf_token="tok", enable_ach=True, **context)

	expected = [{"href": invoice_pdf.PDF_ROUTE + "?invoice=SINV-9", "target": "_blank", "rel": "noopener"}]
	settled = (
		"paid",
		"nothing",
		"received",
		"processing",
		"awaiting",
		"unconfirmed",
		"verifying",
		"unreleased",
	)
	for state in settled:
		assert f'settled == "{state}"' in source, state
		html = render(enabled=True, settled=state, settled_invoice="SINV-9", awaiting_minutes=5)
		links = [{k: v for k, v in link.items() if k != "class"} for link in _pdf_links(html)]
		assert links == expected, state
		assert 'href="/pay"' in html, state

	form = render(
		enabled=True,
		settled=None,
		invoice=types.SimpleNamespace(name="SINV-9"),
		amount_display="USD 200.00",
		currency="USD",
		publishable_key="pk_test_x",
		amount_minor=20000,
	)
	assert _pdf_links(form) == expected
	# Beside the header, above the card: visible on the card step and the review step alike.
	assert form.index("<b>SINV-9</b>") < form.index("View invoice (PDF)") < form.index('id="step-card"')

	assert _pdf_links(render(enabled=True, settled=None, invoice=None)) == []
	assert _pdf_links(render(enabled=False, settled=None, invoice=None)) == []
