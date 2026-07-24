"""Bench-free unit tests for the Purchase Order approval-threshold logic (WI-013).

Stubs a minimal ``frappe`` (no site/bench) so the pure decision logic in
``erpnext_enhancements.po_approval`` runs under plain unittest. The stub is
installed in ``setUpModule`` (execution time), not at import, so it never fools
the bench-only suites' ``import frappe`` skip-guards.

Run: python -m unittest erpnext_enhancements.tests.test_po_approval
"""

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Mutable state the frappe stub reads at call time.
STATE = {"roles": [], "user": "pm@example.com", "threshold": 500.0}
po_approval = None


class StubThrow(Exception):
    pass


def _install_frappe_stub():
    frappe = types.ModuleType("frappe")

    def throw(msg, title=None, exc=None):
        raise StubThrow(msg)

    frappe.throw = throw
    frappe.ValidationError = StubThrow
    frappe._ = lambda s: s
    frappe.session = types.SimpleNamespace(user=STATE["user"])
    frappe.get_roles = lambda user=None: list(STATE["roles"])

    class _Settings:
        def get(self, key):
            return STATE["threshold"] if key == "po_approval_threshold" else None

    frappe.get_cached_doc = lambda dt: _Settings()

    utils = types.ModuleType("frappe.utils")
    utils.flt = lambda v, precision=None: float(v or 0)
    utils.fmt_money = lambda v, currency=None: f"{(currency or '').strip()} {float(v or 0):,.2f}".strip()
    frappe.utils = utils

    sys.modules["frappe"] = frappe
    sys.modules["frappe.utils"] = utils


def setUpModule():
    global po_approval
    _install_frappe_stub()
    sys.modules.pop("erpnext_enhancements.po_approval", None)
    from erpnext_enhancements import po_approval as mod

    po_approval = mod


def _set(user="pm@example.com", roles=None, threshold=500.0):
    STATE.update(roles=list(roles or []), user=user, threshold=threshold)
    sys.modules["frappe"].session.user = user


class _Doc(dict):
    """Minimal PO stand-in: attribute access (doc.grand_total) + .get('currency')."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


def _po(total, currency="USD", project=None):
    return _Doc(grand_total=total, currency=currency, project=project)


class TestThresholdResolution(unittest.TestCase):
    def test_global_threshold(self):
        _set(threshold=500)
        self.assertEqual(po_approval.get_effective_threshold(_po(0)), 500.0)

    def test_zero_disables(self):
        _set(threshold=0)
        self.assertEqual(po_approval.get_effective_threshold(_po(0)), 0.0)


class TestApprovalAuthority(unittest.TestCase):
    def test_po_approver_has_authority(self):
        _set(user="ceo@example.com", roles=["Purchase User", "PO Approver"])
        self.assertTrue(po_approval.has_approval_authority())

    def test_non_approver_lacks_authority(self):
        _set(user="pm@example.com", roles=["Purchase User"])
        self.assertFalse(po_approval.has_approval_authority())

    def test_administrator_bypasses(self):
        _set(user="Administrator", roles=[])
        self.assertTrue(po_approval.has_approval_authority())


class TestEnforceThreshold(unittest.TestCase):
    def test_below_threshold_passes(self):
        _set(roles=["Purchase User"], threshold=500)
        po_approval.enforce_threshold(_po(400))  # no raise

    def test_at_threshold_passes(self):
        # boundary: only totals strictly above the threshold escalate
        _set(roles=["Purchase User"], threshold=500)
        po_approval.enforce_threshold(_po(500))  # no raise

    def test_above_threshold_blocks_non_approver(self):
        _set(roles=["Purchase User"], threshold=500)
        with self.assertRaises(StubThrow):
            po_approval.enforce_threshold(_po(600))

    def test_above_threshold_allows_approver(self):
        _set(user="ceo@example.com", roles=["Purchase User", "PO Approver"], threshold=500)
        po_approval.enforce_threshold(_po(600))  # no raise

    def test_disabled_threshold_allows_large_po(self):
        _set(roles=["Purchase User"], threshold=0)
        po_approval.enforce_threshold(_po(999999))  # no raise


if __name__ == "__main__":
    unittest.main()
