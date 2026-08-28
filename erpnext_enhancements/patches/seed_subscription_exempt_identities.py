"""Seed ``Chat Settings.subscription_exempt_users`` with the Triton group identity.

Why a patch and not a field default: ``subscription_exempt_users`` is a new field on a
**Single**, and a ``default`` on a new Single field never reaches the row that already
exists (the v1.277.3 lesson recorded in CLAUDE.md) — it would apply on a fresh install
and nowhere real. The value itself is also a site fact, not a schema fact: this site's
chat spaces contain ``triton@sapphirefountains.com``, which is a Google **Group**, not a
Workspace user. A group cannot be impersonated — the DWD token exchange refuses it with
``401 unauthorized_client`` — so the subscription roster demanding coverage for it
produced a ``subscription-missing`` alarm that had re-fired x128 by 2026-08-28 and a
repair command that could only ever fail.

Fill-blank-only: an operator's later edits to the field survive re-runs. Safe twice.
"""

import frappe

TRITON_GROUP = "triton@sapphirefountains.com"


def execute():
	if not frappe.db.exists("DocType", "Chat Settings"):
		return
	current = frappe.db.get_single_value("Chat Settings", "subscription_exempt_users")
	if (current or "").strip():
		return
	frappe.db.set_single_value("Chat Settings", "subscription_exempt_users", TRITON_GROUP)
