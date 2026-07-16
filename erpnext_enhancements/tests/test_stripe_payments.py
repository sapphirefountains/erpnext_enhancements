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


def test_enrich_reads_charge_and_session_without_api():
	"""_enrich derives (charge_id, method_type) from the event object, no API call needed."""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core.reconcile import _enrich

	charge = {"object": "charge", "id": "ch_1", "payment_method_details": {"type": "card"}}
	assert _enrich(charge, "pi_1") == ("ch_1", "card")

	# Session with pi_id=None so the optional API enrichment branch is skipped.
	session = {"object": "checkout.session", "payment_method_types": ["us_bank_account"]}
	assert _enrich(session, None) == (None, "us_bank_account")


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


def test_compute_surcharge_by_method():
	"""_compute_surcharge applies the per-method %/flat only when enabled + method chosen."""
	install_frappe_stub()
	from erpnext_enhancements.stripe_payments.core.checkout import (
		_compute_surcharge,
		_method_hint,
		_methods_for,
	)

	on = types.SimpleNamespace(
		surcharge_enabled=1,
		card_surcharge_percent=3,
		card_surcharge_flat=0,
		ach_fee_percent=0,
		ach_fee_flat=5,
		enable_card=1,
		enable_ach=1,
	)
	assert _compute_surcharge(on, "card", 100) == 3.0  # 3% of 100
	assert _compute_surcharge(on, "ach", 100) == 5.0  # flat $5
	assert _compute_surcharge(on, None, 100) == 0.0  # no method -> never surcharge

	off = types.SimpleNamespace(
		surcharge_enabled=0,
		card_surcharge_percent=3,
		card_surcharge_flat=0,
		ach_fee_percent=0,
		ach_fee_flat=0,
	)
	assert _compute_surcharge(off, "card", 100) == 0.0  # disabled -> zero

	# Choosing a method locks the Checkout Session to it.
	assert _methods_for(on, "card") == ["card"]
	assert _methods_for(on, "ach") == ["us_bank_account"]
	assert _method_hint("card") == "card"
	assert _method_hint("ach") == "us_bank_account"
	assert _method_hint(None) is None


# --- payout reconciliation (WI-040) -----------------------------------------


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
