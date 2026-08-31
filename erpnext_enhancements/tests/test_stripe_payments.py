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


def _stub_throw(message=None, *args, **kwargs):
	"""Stand-in for ``frappe.throw`` that raises a plain exception in tests."""
	raise Exception(message if isinstance(message, str) else "frappe.throw")


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
	sys.modules["frappe.utils.synchronization"] = sync_mod
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
