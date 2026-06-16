"""Provider adapters for the MDM Integration.

A provider-agnostic interface (``MDMProvider`` → normalized ``ProviderDevice``)
with three concrete adapters:

* ``MiradoreProvider`` — mobile MDM. Miradore API v2, ``X-API-Key`` +
  ``X-Instance-Name`` headers (no OAuth). list/get + lock/wipe/locate.
* ``Action1Provider`` — computer RMM. Action1 REST ``api/3.0``, OAuth2
  client-credentials (token refresh mirrors ``quickbooks_online/client.py``).
  list/get + reboot/run_script/deploy_patch (no wipe — it is RMM).
* ``MockProvider`` — canned devices + recorded actions, so the whole sync /
  reconcile / action / audit pipeline runs and is testable with NO credentials.

``get_provider`` returns the Mock adapter whenever ``MDM Settings.provider_mode``
is "Mock" (the default), so the integration is exercisable before any keys are
pasted; flip to "Live" to hit the real APIs. ``get_provider_for(device)`` routes
a Managed Device to its provider by device class (see ``routing``).

NOTE: the Live adapters are scaffolded against the providers' *documented* REST
shapes; the exact JSON field names should be confirmed against each vendor's
Swagger (`online.miradore.com/swagger`, `app.action1.com/apidocs`) when real
credentials are added. Parsing is defensive (multi-key ``_first``) to tolerate
that. The Mock adapter is the path covered by tests.
"""

from __future__ import annotations

import dataclasses

import frappe
from frappe.utils import add_to_date, now_datetime

from erpnext_enhancements.mdm_integration.routing import (
	CAPABILITIES,
	is_retryable_status,
	provider_key_for_device,
	provider_supports,
)
from erpnext_enhancements.mdm_integration.utils import get_secret, get_settings, set_secret


class MDMProviderError(Exception):
	"""Raised on a provider API/transport error or an unsupported action.

	``status_code`` is the HTTP status when the error came from a provider
	response (``None`` for transport/timeout/local errors). ``retryable`` tells the
	sync layer whether re-running on a schedule could ever help: auth/permission/
	not-found/bad-request responses are permanent until the operator fixes the
	config, so they pause the provider instead of being retried every cycle. Pass
	``retryable=`` to override the status-based default (e.g. a missing-credential
	guard that has no HTTP status but is still permanent).
	"""

	def __init__(self, message, status_code=None, *, retryable=None):
		super().__init__(message)
		self.status_code = status_code
		self._retryable = retryable

	@property
	def retryable(self):
		if self._retryable is not None:
			return self._retryable
		return is_retryable_status(self.status_code)


@dataclasses.dataclass
class ProviderDevice:
	"""Provider-neutral device record produced by every adapter's ``_normalize``."""

	provider: str
	provider_id: str
	serial: str | None = None
	imei: str | None = None
	mac: str | None = None
	platform: str | None = None
	device_type: str | None = None
	model: str | None = None
	manufacturer: str | None = None
	os_version: str | None = None
	screen_lock: bool | None = None
	encryption: bool | None = None
	compliance_state: str | None = None  # "Compliant" / "Non-Compliant" / None
	last_seen: str | None = None
	assignee_hint: str | None = None  # email/UPN/username, if the provider exposes one
	ownership_hint: str | None = None  # "Company" / "BYOD" / None
	raw: dict = dataclasses.field(default_factory=dict)


def _first(data, *keys, default=None):
	"""Return the first present, non-None value among ``keys`` in ``data``."""
	for key in keys:
		if isinstance(data, dict) and data.get(key) is not None:
			return data.get(key)
	return default


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class MDMProvider:
	"""Abstract provider adapter. ``key`` is the provider name used for routing,
	capability checks and logging."""

	key = "Base"

	def __init__(self, settings=None):
		self.settings = settings or get_settings()

	def supports(self, action: str) -> bool:
		return provider_supports(self.key, action)

	# --- inventory (override) ---
	def list_devices(self):
		raise NotImplementedError

	def get_device(self, provider_id):
		raise NotImplementedError

	# --- actions (override the supported subset) ---
	def _unsupported(self, action):
		raise MDMProviderError(f"{self.key} does not support the '{action}' action.")

	def lock(self, provider_id):
		self._unsupported("lock")

	def wipe(self, provider_id, mode="selective"):
		self._unsupported("wipe")

	def locate(self, provider_id):
		self._unsupported("locate")

	def reboot(self, provider_id):
		self._unsupported("reboot")

	def run_script(self, provider_id, script):
		self._unsupported("run_script")

	def deploy_patch(self, provider_id, patch):
		self._unsupported("deploy_patch")


# ---------------------------------------------------------------------------
# Miradore (mobile MDM) — X-API-Key auth
# ---------------------------------------------------------------------------


class MiradoreProvider(MDMProvider):
	key = "Miradore"

	def _headers(self):
		api_key = get_secret(self.settings, "miradore_api_key")
		instance = self.settings.get("miradore_instance_name")
		if not api_key or not instance:
			raise MDMProviderError("Miradore instance name and API key are required.", retryable=False)
		return {"X-API-Key": api_key, "X-Instance-Name": instance, "Accept": "application/json"}

	def _request(self, method, path, **kwargs):
		import requests

		url = f"https://online.miradore.com/api/v2{path}"
		resp = requests.request(method, url, headers=self._headers(), timeout=kwargs.pop("timeout", 60), **kwargs)
		if resp.status_code >= 400:
			raise MDMProviderError(
				f"Miradore {method} {path} failed: {resp.status_code} {resp.text}",
				status_code=resp.status_code,
			)
		return resp.json() if resp.text else {}

	def list_devices(self):
		data = self._request("GET", "/devices")
		# Miradore returns either a bare list or an envelope ({"items": [...]}).
		rows = data if isinstance(data, list) else (data.get("items") or data.get("devices") or [])
		return [self._normalize(row) for row in rows]

	def get_device(self, provider_id):
		return self._normalize(self._request("GET", f"/devices/{provider_id}"))

	def lock(self, provider_id):
		return self._request("POST", f"/devices/{provider_id}/lock")

	def wipe(self, provider_id, mode="selective"):
		# selective = remove corporate data only; full = factory reset.
		return self._request("POST", f"/devices/{provider_id}/wipe", json={"selective": mode == "selective"})

	def locate(self, provider_id):
		return self._request("GET", f"/devices/{provider_id}/location")

	def _normalize(self, row):
		return ProviderDevice(
			provider=self.key,
			provider_id=str(_first(row, "id", "deviceId", "Id", default="")),
			serial=_first(row, "serialNumber", "serial", "SerialNumber"),
			imei=_first(row, "imei", "IMEI"),
			mac=_first(row, "wifiMacAddress", "macAddress"),
			platform=_first(row, "platform", "osType", "operatingSystem"),
			os_version=_first(row, "osVersion", "OSVersion"),
			model=_first(row, "model", "hardwareModel"),
			manufacturer=_first(row, "manufacturer", "vendor"),
			screen_lock=_first(row, "passcodePresent", "passcodeCompliant"),
			encryption=_first(row, "encrypted", "storageEncryption"),
			compliance_state=_normalize_compliance(_first(row, "compliant", "complianceStatus")),
			last_seen=_first(row, "lastSeen", "lastConnected"),
			assignee_hint=_first(row, "userEmail", "user", "ownerEmail"),
			raw=row,
		)


# ---------------------------------------------------------------------------
# Action1 (computer RMM) — OAuth2 client-credentials
# ---------------------------------------------------------------------------


class Action1Provider(MDMProvider):
	key = "Action1"
	BASE = "https://app.action1.com/api/3.0"

	def _org(self):
		org = self.settings.get("action1_org_id")
		if not org:
			raise MDMProviderError("Action1 Organization ID is required.", retryable=False)
		return org

	def _ensure_token(self):
		"""Return a valid bearer token, refreshing via client-credentials if needed."""
		token = get_secret(self.settings, "action1_access_token")
		expires = self.settings.get("action1_token_expires_at")
		from frappe.utils import get_datetime

		fresh = token and expires and get_datetime(expires) > now_datetime()
		if fresh:
			return token
		return self._refresh_token()

	def _refresh_token(self):
		import requests

		client_id = self.settings.get("action1_client_id")
		client_secret = get_secret(self.settings, "action1_client_secret")
		if not client_id or not client_secret:
			raise MDMProviderError("Action1 Client ID and Client Secret are required.", retryable=False)
		resp = requests.post(
			f"{self.BASE}/oauth2/token",
			data={"client_id": client_id, "client_secret": client_secret},
			headers={"Accept": "application/json"},
			timeout=60,
		)
		if resp.status_code >= 400:
			raise MDMProviderError(
				f"Action1 token request failed: {resp.status_code} {resp.text}",
				status_code=resp.status_code,
			)
		data = resp.json()
		token = data.get("access_token")
		if not token:
			raise MDMProviderError("Action1 token response had no access_token.")
		set_secret(self.settings, "action1_access_token", token)
		# Backdate the stored expiry by 5 min so callers refresh slightly early.
		self.settings.action1_token_expires_at = add_to_date(
			now_datetime(), seconds=int(data.get("expires_in") or 3600) - 300, as_datetime=True
		)
		self.settings.save(ignore_permissions=True)
		frappe.db.commit()
		return token

	def _request(self, method, path, *, _retry=True, **kwargs):
		import requests

		token = self._ensure_token()
		resp = requests.request(
			method,
			f"{self.BASE}{path}",
			headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
			timeout=kwargs.pop("timeout", 60),
			**kwargs,
		)
		if resp.status_code == 401 and _retry:
			self._refresh_token()
			return self._request(method, path, _retry=False, **kwargs)
		if resp.status_code >= 400:
			raise MDMProviderError(
				f"Action1 {method} {path} failed: {resp.status_code} {resp.text}",
				status_code=resp.status_code,
			)
		return resp.json() if resp.text else {}

	def list_devices(self):
		data = self._request("GET", f"/endpoints/managed/{self._org()}")
		rows = data if isinstance(data, list) else (data.get("items") or data.get("data") or [])
		return [self._normalize(row) for row in rows]

	def get_device(self, provider_id):
		return self._normalize(self._request("GET", f"/endpoints/managed/{self._org()}/{provider_id}"))

	def reboot(self, provider_id):
		return self._request("POST", f"/endpoints/managed/{self._org()}/{provider_id}/reboot", json={})

	def run_script(self, provider_id, script):
		return self._request(
			"POST", f"/endpoints/managed/{self._org()}/{provider_id}/scripts", json={"script": script}
		)

	def deploy_patch(self, provider_id, patch):
		return self._request(
			"POST", f"/endpoints/managed/{self._org()}/{provider_id}/updates", json={"update_id": patch}
		)

	def _normalize(self, row):
		return ProviderDevice(
			provider=self.key,
			provider_id=str(_first(row, "id", "endpoint_id", "ID", default="")),
			serial=_first(row, "serial_number", "serial", "SerialNumber"),
			mac=_first(row, "mac_address", "MAC"),
			platform=_first(row, "platform", "os_family", "OS") or "Windows",
			device_type="Laptop",
			os_version=_first(row, "os_version", "OSVersion", "os"),
			model=_first(row, "model", "computer_model"),
			manufacturer=_first(row, "manufacturer", "vendor"),
			encryption=_first(row, "disk_encryption", "bitlocker", "encrypted"),
			compliance_state=_normalize_compliance(_first(row, "compliance_status", "compliant", "patch_status")),
			last_seen=_first(row, "last_seen", "last_contact", "last_online"),
			assignee_hint=_first(row, "logged_in_user", "user", "assigned_user"),
			raw=row,
		)


# ---------------------------------------------------------------------------
# Mock (no credentials) — canned data + recorded actions
# ---------------------------------------------------------------------------

# Module-level action recorder so tests can assert what was dispatched.
MOCK_ACTIONS = []

_MOCK_DEVICES = {
	"Miradore": [
		{"id": "MIR-1001", "serialNumber": "MIRSN1001", "imei": "350000000000001", "platform": "Android",
		 "osVersion": "14", "model": "Pixel 8", "manufacturer": "Google", "passcodePresent": True,
		 "encrypted": True, "compliant": True, "userEmail": "tech1@sapphirefountains.com"},
		{"id": "MIR-1002", "serialNumber": "MIRSN1002", "imei": "350000000000002", "platform": "iOS",
		 "osVersion": "18.1", "model": "iPhone 14", "manufacturer": "Apple", "passcodePresent": True,
		 "encrypted": False, "compliant": False, "userEmail": "tech2@sapphirefountains.com"},
	],
	"Action1": [
		{"id": "A1-2001", "serial_number": "A1SN2001", "platform": "Windows", "os_version": "11 23H2",
		 "model": "Latitude 5440", "manufacturer": "Dell", "disk_encryption": True,
		 "compliance_status": "compliant", "logged_in_user": "office1@sapphirefountains.com"},
		{"id": "A1-2002", "serial_number": "A1SN2002", "platform": "macOS", "os_version": "15.1",
		 "model": "MacBook Air", "manufacturer": "Apple", "disk_encryption": True,
		 "compliance_status": "noncompliant", "logged_in_user": "office2@sapphirefountains.com"},
	],
}


class MockProvider(MDMProvider):
	"""Stand-in for either provider. ``key`` carries the impersonated provider so
	routing, normalization and logging behave as in Live mode."""

	def __init__(self, key, settings=None):
		super().__init__(settings)
		self.key = key

	def supports(self, action):
		return action in CAPABILITIES["Mock"]

	def list_devices(self):
		real = MiradoreProvider if self.key == "Miradore" else Action1Provider
		return [real._normalize(self, row) for row in _MOCK_DEVICES.get(self.key, [])]

	def get_device(self, provider_id):
		for device in self.list_devices():
			if device.provider_id == provider_id:
				return device
		raise MDMProviderError(f"Mock {self.key} device {provider_id} not found.")

	def _record(self, action, provider_id, **extra):
		entry = {"provider": self.key, "action": action, "provider_id": provider_id, **extra}
		MOCK_ACTIONS.append(entry)
		return {"ok": True, "mock": True, **entry}

	def lock(self, provider_id):
		return self._record("lock", provider_id)

	def wipe(self, provider_id, mode="selective"):
		return self._record("wipe", provider_id, mode=mode)

	def locate(self, provider_id):
		return self._record("locate", provider_id, location={"lat": 40.76, "lng": -111.89})

	def reboot(self, provider_id):
		return self._record("reboot", provider_id)

	def run_script(self, provider_id, script):
		return self._record("run_script", provider_id, script=script)

	def deploy_patch(self, provider_id, patch):
		return self._record("deploy_patch", provider_id, patch=patch)


# ---------------------------------------------------------------------------
# Factory + routing
# ---------------------------------------------------------------------------


def _normalize_compliance(value):
	"""Map a provider's compliance signal to 'Compliant' / 'Non-Compliant' / None."""
	if value is None:
		return None
	if isinstance(value, bool):
		return "Compliant" if value else "Non-Compliant"
	text = str(value).strip().lower()
	if text in ("compliant", "true", "yes", "ok", "1"):
		return "Compliant"
	if text in ("noncompliant", "non-compliant", "false", "no", "0"):
		return "Non-Compliant"
	return None


def get_provider(provider_key, settings=None):
	"""Return the adapter for ``provider_key`` — the Mock adapter when Settings'
	provider_mode is 'Mock', else the real Miradore/Action1 adapter."""
	settings = settings or get_settings()
	if (settings.get("provider_mode") or "Mock") == "Mock":
		return MockProvider(provider_key, settings)
	if provider_key == "Miradore":
		return MiradoreProvider(settings)
	if provider_key == "Action1":
		return Action1Provider(settings)
	raise MDMProviderError(f"Unknown provider: {provider_key}")


def get_provider_for(device, settings=None):
	"""Return ``(provider, provider_key)`` for a Managed Device, or ``(None, key)``
	when the device class maps to no provider."""
	settings = settings or get_settings()
	key = provider_key_for_device(device.get("device_type"), device.get("platform"))
	if not key:
		return None, None
	return get_provider(key, settings), key
