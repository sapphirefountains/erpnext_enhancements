# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Signature-provider registry.

Adding a provider is: a module here, one entry in ``_PROVIDERS``, and one more
option on the ``contract_esign_provider`` Select. Nothing else in the app changes.

**The correctness rule of this seam:** a request's provider is frozen on the row
at send time, and every later operation reads ``request.provider`` — never the
current Settings value. Flipping the setting must not orphan links already in
flight, whose signers are mid-signature against the provider that sent them.
"""

import frappe

from erpnext_enhancements.project_enhancements.esign.providers.base import SignatureProvider
from erpnext_enhancements.project_enhancements.esign.providers.inhouse import InHouseProvider

_PROVIDERS = {
	InHouseProvider.key: InHouseProvider,
}

DEFAULT_PROVIDER = InHouseProvider.key


def configured_provider_key():
	"""The provider a NEW request would use (Settings, defaulting to In-House)."""
	return (
		frappe.db.get_single_value("ERPNext Enhancements Settings", "contract_esign_provider")
		or DEFAULT_PROVIDER
	)


def get_provider(key=None):
	"""Resolve a provider by key.

	Pass ``request.provider`` for anything acting on an existing request; pass
	nothing only when creating a new one.
	"""
	key = key or configured_provider_key()
	provider = _PROVIDERS.get(key)
	if not provider:
		frappe.throw(
			frappe._("Unknown signature provider {0}.").format(key),
			title=frappe._("Signature Provider"),
		)
	return provider()


__all__ = ["DEFAULT_PROVIDER", "SignatureProvider", "configured_provider_key", "get_provider"]
