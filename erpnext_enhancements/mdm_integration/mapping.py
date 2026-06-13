"""Reconcile a provider ``ProviderDevice`` into the native Managed Device registry.

Match precedence: the stored provider id (within the same provider) → serial →
IMEI. A match has its compliance posture overwritten from the live feed and
stamped ``compliance_source = "Provider"``; a provider device with no registry
match is created as a **Discovered** Managed Device for a human to confirm/assign
(never silently trusted). Registry devices the feed stops returning are flagged
**Unmanaged** by the sync, never deleted.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime, today

from erpnext_enhancements.device_management.compliance import derive_compliance


def upsert_device(pd):
	"""Create/update a Managed Device from a ProviderDevice. Returns a result dict
	with ``action`` in {created, updated} (counted by the sync log)."""
	name = _find_device(pd)
	if name:
		doc = frappe.get_doc("Managed Device", name)
		_apply_provider_fields(doc, pd)
		doc.save(ignore_permissions=True)
		return {"action": "updated", "device": doc.name, "provider_id": pd.provider_id}

	doc = _create_discovered(pd)
	return {"action": "created", "device": doc.name, "provider_id": pd.provider_id, "discovered": True}


def _find_device(pd):
	"""Locate the Managed Device this provider device maps to, or None."""
	if pd.provider_id:
		name = frappe.db.get_value(
			"Managed Device",
			{"mdm_provider": pd.provider, "mdm_provider_device_id": pd.provider_id},
			"name",
		)
		if name:
			return name
	if pd.serial:
		name = frappe.db.get_value("Managed Device", {"serial_number": pd.serial.strip().upper()}, "name")
		if name:
			return name
	if pd.imei:
		name = frappe.db.get_value("Managed Device", {"imei": pd.imei.strip().upper()}, "name")
		if name:
			return name
	return None


def _apply_provider_fields(doc, pd):
	"""Overwrite the provider-owned posture + link fields on an existing device."""
	doc.mdm_provider = pd.provider
	doc.mdm_provider_device_id = pd.provider_id
	doc.mdm_link_state = "Managed"
	doc.mdm_last_seen = now_datetime()
	if pd.os_version:
		doc.os_version = pd.os_version
	if pd.screen_lock is not None:
		doc.screen_lock_enabled = 1 if pd.screen_lock else 0
	if pd.encryption is not None:
		doc.encryption_enabled = 1 if pd.encryption else 0
	doc.compliance_status = _resolve_compliance(pd, doc)
	doc.compliance_source = "Provider"
	doc.last_checked_on = today()


def _create_discovered(pd):
	"""Insert a Discovered Managed Device from a provider device with no match."""
	doc = frappe.new_doc("Managed Device")
	doc.device_name = pd.model or f"Discovered {pd.serial or pd.provider_id}"
	doc.platform = _map_platform(pd.platform)
	doc.device_type = pd.device_type or _guess_type(doc.platform)
	doc.manufacturer = pd.manufacturer
	doc.model = pd.model
	doc.serial_number = pd.serial
	doc.imei = pd.imei
	doc.mac_address = pd.mac
	doc.ownership = pd.ownership_hint if pd.ownership_hint in ("Company", "BYOD") else "Company"
	doc.status = "In Stock"
	doc.os_version = pd.os_version
	doc.screen_lock_enabled = 1 if pd.screen_lock else 0
	doc.encryption_enabled = 1 if pd.encryption else 0
	doc.compliance_status = _resolve_compliance(pd, doc)
	doc.compliance_source = "Provider"
	doc.last_checked_on = today()
	doc.mdm_provider = pd.provider
	doc.mdm_provider_device_id = pd.provider_id
	doc.mdm_link_state = "Discovered"
	doc.mdm_last_seen = now_datetime()
	doc.insert(ignore_permissions=True)
	return doc


def _resolve_compliance(pd, doc):
	"""Provider compliance signal if given, else derive from the two booleans,
	else leave Unknown."""
	if pd.compliance_state in ("Compliant", "Non-Compliant"):
		return pd.compliance_state
	if pd.screen_lock is not None and pd.encryption is not None:
		return derive_compliance(pd.screen_lock, pd.encryption)
	return doc.compliance_status or "Unknown"


def _map_platform(platform):
	"""Coerce a provider platform string to a Managed Device platform option."""
	text = (platform or "").strip().lower()
	if "ipad" in text:
		return "iPadOS"
	if "ios" in text or "iphone" in text:
		return "iOS"
	if "android" in text:
		return "Android"
	if "win" in text:
		return "Windows"
	if "mac" in text or "osx" in text or "darwin" in text:
		return "macOS"
	if "linux" in text:
		return "Linux"
	return "Other"


def _guess_type(platform):
	"""Best-guess device type when the provider doesn't say (refined by a human)."""
	if platform in ("Android", "iOS"):
		return "Phone"
	if platform == "iPadOS":
		return "Tablet"
	if platform in ("Windows", "macOS", "Linux"):
		return "Laptop"
	return "Other"
