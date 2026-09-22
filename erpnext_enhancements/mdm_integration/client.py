"""Provider adapters for the MDM Integration.

A provider-agnostic interface (``MDMProvider`` → normalized ``ProviderDevice``)
with three concrete adapters:

* ``MiradoreProvider`` — mobile MDM. Reads devices through Miradore **API v1**
  (XML, key in the ``auth`` query parameter) and acts through **API v2** (JSON,
  ``X-API-Key`` + ``X-Instance-Name`` headers): lock / wipe / locate. v2 cannot
  list or read devices at all; see the class docstring.
* ``Action1Provider`` — computer RMM. Action1 REST ``api/3.0``, OAuth2
  client-credentials (token refresh mirrors ``quickbooks_online/client.py``).
  list/get, and reboot / run_script / deploy_patch as Action1 *automations*
  (no wipe — it is RMM).
* ``MockProvider`` — canned devices + recorded actions, so the whole sync /
  reconcile / action / audit pipeline runs and is testable with NO credentials.

``get_provider`` returns the Mock adapter whenever ``MDM Settings.provider_mode``
is "Mock" (the default), so the integration is exercisable before any keys are
pasted; flip to "Live" to hit the real APIs. ``get_provider_for(device)`` routes
a Managed Device to its provider by device class (see ``routing``).

Until v1.506.0 both Live adapters were written against guessed endpoints, and
none of the guesses matched the vendors: Miradore sync called a list endpoint
v2 does not have (12,803 failed syncs, June 2026), and every remote action for
both providers called a path that does not exist. The shapes below are taken
from the vendors' own specs — Miradore API v1.19 (PDF) and
``online.miradore.com/swagger/v2/swagger.json``, Action1's OpenAPI 3.1 document
behind ``app.action1.com/apidocs`` and its PSAction1 client — and from a real
Action1 payload archived on production. ``tests/test_mdm_provider_clients.py``
pins the requests each method sends.
"""

from __future__ import annotations

import dataclasses
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote, quote_plus

import frappe
from frappe.utils import add_to_date, now_datetime

from erpnext_enhancements.mdm_integration.routing import (
	CAPABILITIES,
	action1_script_target,
	is_retryable_status,
	miradore_selective_wipe_refusal,
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
	device_name: str | None = None  # the provider's own name for the device (hostname, phone name)
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


def _child(data, key):
	"""``data[key]`` when it is a dict, else ``{}`` (an absent or empty XML child)."""
	value = data.get(key) if isinstance(data, dict) else None
	return value if isinstance(value, dict) else {}


def _tristate(value, yes, no):
	"""True / False for a provider enum value in ``yes`` / ``no``; None otherwise."""
	if value in yes:
		return True
	if value in no:
		return False
	return None


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
# Miradore (mobile MDM) — API v1 to read, API v2 to act
# ---------------------------------------------------------------------------

MIRADORE_HOST = "https://online.miradore.com"

#: Device attributes the sync reads, as Miradore API v1 ``select`` paths (API
#: spec v1.19, Appendix 2). ``Client.ManagementType`` is what the selective-wipe
#: guard turns on; ``Security`` is the MobileSecurity child item.
MIRADORE_DEVICE_SELECT = (
	"ID",
	"Platform",
	"Status",
	"OnlineStatus",
	"LastReported",
	"InvDevice.SerialNumber",
	"InvDevice.IMEI",
	"InvDevice.WiFiMAC",
	"InvDevice.Manufacturer",
	"InvDevice.Model",
	"InvDevice.MarketingName",
	"InvDevice.DeviceName",
	"InvDevice.DeviceType",
	"InvOS.Version",
	"User.Email",
	"Client.ManagementType",
	"Security.PasscodeSet",
	"Security.EncryptionStatus",
)
MIRADORE_PAGE_ROWS = 100  # API v1's own default page size
MIRADORE_MAX_PAGES = 50  # 5,000 devices. Past that, fail loudly rather than sync a partial list.

#: ``Device.Status`` values that mean the device is no longer a live managed
#: device. Skipped by the sync, so the registry flags them Unmanaged.
_MIRADORE_GONE_STATUSES = frozenset({"Deleted", "Unmanaged"})

#: What Miradore's Wipe actually does, by enrollment (Miradore KB, "Wipe for
#: Android devices" / "Remotely wiping a device"). Recorded on the Device Action
#: Log, because a "full" wipe of a work-profile device is not a factory reset.
_MIRADORE_WIPE_EFFECT = {
	"AndroidDeviceOwner": "Factory reset (fully managed Android).",
	"AndroidProfileOwner": "Work profile and its data removed; personal data kept. "
	"Miradore cannot factory-reset a work-profile device.",
	"iOSSupervised": "Device erased.",
	"iOSUnsupervised": "Device erased.",
}

_SITE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _local_tag(tag):
	"""An XML tag without its ``{namespace}`` prefix (``options=usenamespace``)."""
	return tag.rsplit("}", 1)[-1]


def _xml_to_value(element):
	"""An XML element as a dict of its children (lists for repeats), or its text."""
	children = list(element)
	if not children:
		return (element.text or "").strip() or None
	out = {}
	for child in children:
		tag = _local_tag(child.tag)
		value = _xml_to_value(child)
		if tag in out:
			if not isinstance(out[tag], list):
				out[tag] = [out[tag]]
			out[tag].append(value)
		else:
			out[tag] = value
	return out


def parse_miradore_items(text, item="Device"):
	"""``(rows, total)`` from a Miradore API v1 response document.

	The root is ``Content``, and items sit under ``Items``. The spec shows the total
	match count as ``count`` on ``Items`` in one example and on ``Content`` in
	another, so both are read. ``total`` is None when neither carries it.
	"""
	root = ET.fromstring(text)
	items = next((el for el in root.iter() if _local_tag(el.tag) == "Items"), None)
	if items is None:
		return [], 0
	count = items.get("count") or root.get("count")
	rows = [_xml_to_value(el) for el in items if _local_tag(el.tag) == item]
	rows = [row if isinstance(row, dict) else {} for row in rows]
	return rows, (int(count) if count and str(count).isdigit() else None)


class MiradoreProvider(MDMProvider):
	"""Miradore, read through API v1 and driven through API v2.

	**Reading.** API v2 has no way to list or read devices — ``/api/v2/Device`` is
	POST-only (create an "Other Device"), and ``/api/v2/Device/{id}`` takes only
	PATCH and DELETE. Miradore's own v2 docs say device IDs come from API v1:
	``GET {host}/{site}/API/Device?auth=<key>&select=...``, XML, paged with
	``options=rows=N,page=N``. The same key serves both versions.

	**The key is in the v1 URL**, so every error message built here goes through
	``_redact``, and a transport error is re-raised ``from None``: a
	``requests`` exception's text contains the full URL, and the sync stores its
	error text on the Sync Log and in the Error Log.

	**Acting.** v2, ``POST /Device/{id}/Lock``, ``GET /Device/{id}/Location``, and
	for a wipe either ``POST /Device/{id}/Wipe`` (full) or a Retire,
	``DELETE /Device/{id}`` (selective). Miradore has no selective option on Wipe;
	see ``routing.miradore_selective_wipe_refusal`` for why a selective wipe is a
	Retire, and when even that is refused.
	"""

	key = "Miradore"

	# --- credentials + transport ---------------------------------------------

	def _site(self):
		site = (self.settings.get("miradore_instance_name") or "").strip()
		if not site:
			raise MDMProviderError("Miradore instance name and API key are required.", retryable=False)
		if not _SITE_NAME.match(site):
			raise MDMProviderError(
				"Miradore instance name must be the site name only (as in online.miradore.com/<site>).",
				retryable=False,
			)
		return site

	def _api_key(self):
		api_key = get_secret(self.settings, "miradore_api_key")
		if not api_key:
			raise MDMProviderError("Miradore instance name and API key are required.", retryable=False)
		return api_key

	def _redact(self, text):
		"""``text`` with the API key removed in every form it can take in a URL."""
		text = str(text or "")
		try:
			api_key = get_secret(self.settings, "miradore_api_key")
		except Exception:
			api_key = None
		if api_key:
			for form in {api_key, quote_plus(api_key), quote(api_key, safe="")}:
				text = text.replace(form, "***")
		return text

	def _v1_get(self, path, params):
		"""GET ``{host}/{site}/API/{path}`` (API v1); returns the response text."""
		import requests

		site, api_key = self._site(), self._api_key()
		label = f"Miradore GET API/{path.split('?')[0]}"
		try:
			resp = requests.get(
				f"{MIRADORE_HOST}/{site}/API/{path}",
				params={"auth": api_key, **params},
				headers={"Accept": "application/xml"},
				timeout=60,
			)
		except requests.RequestException as exc:
			raise MDMProviderError(f"{label} failed: {self._redact(exc)[:300]}") from None
		if resp.status_code >= 400:
			raise MDMProviderError(
				f"{label} failed: {resp.status_code} {self._redact(resp.text)[:500]}",
				status_code=resp.status_code,
			)
		return resp.text

	def _v2(self, method, path, **kwargs):
		"""Call ``{host}/api/v2{path}`` (API v2) with the site in ``X-Instance-Name``."""
		import requests

		headers = {
			"X-API-Key": self._api_key(),
			"X-Instance-Name": self._site(),
			"Accept": "application/json",
		}
		label = f"Miradore {method} api/v2{path}"
		try:
			resp = requests.request(
				method, f"{MIRADORE_HOST}/api/v2{path}", headers=headers, timeout=60, **kwargs
			)
		except requests.RequestException as exc:
			raise MDMProviderError(f"{label} failed: {self._redact(exc)[:300]}") from None
		if resp.status_code >= 400:
			raise MDMProviderError(
				f"{label} failed: {resp.status_code} {self._redact(resp.text)[:500]}",
				status_code=resp.status_code,
			)
		if not resp.text:
			return {}
		try:
			return resp.json()
		except ValueError:
			return {"response": resp.text[:2000]}

	@staticmethod
	def _device_id(provider_id):
		"""The Miradore device ID as digits (v2 declares it int32). Anything else is
		refused before it can reach a URL path."""
		value = str(provider_id or "").strip()
		if not value.isdigit():
			raise MDMProviderError(f"'{value}' is not a Miradore device ID.", retryable=False)
		return value

	# --- inventory -------------------------------------------------------------

	def list_devices(self):
		select = ",".join(MIRADORE_DEVICE_SELECT)
		devices, fetched = [], 0
		for page in range(1, MIRADORE_MAX_PAGES + 1):
			text = self._v1_get(
				"Device", {"select": select, "options": f"rows={MIRADORE_PAGE_ROWS},page={page}"}
			)
			rows, total = parse_miradore_items(text)
			fetched += len(rows)
			devices.extend(
				self._normalize(row) for row in rows if row.get("Status") not in _MIRADORE_GONE_STATUSES
			)
			if len(rows) < MIRADORE_PAGE_ROWS or (total is not None and fetched >= total):
				return devices
		# Returning what we have would mark every device on the unread pages Unmanaged.
		raise MDMProviderError(
			f"Miradore returned more than {MIRADORE_MAX_PAGES * MIRADORE_PAGE_ROWS} devices; "
			"raise MIRADORE_MAX_PAGES before syncing a partial list."
		)

	def _read_device(self, provider_id):
		"""One device's attribute tree from API v1, or MDMProviderError (404) if absent."""
		device_id = self._device_id(provider_id)
		rows, _total = parse_miradore_items(
			self._v1_get(f"Device/{device_id}", {"select": ",".join(MIRADORE_DEVICE_SELECT)})
		)
		if not rows:
			raise MDMProviderError(f"Miradore has no device {device_id}.", status_code=404)
		return rows[0]

	def get_device(self, provider_id):
		return self._normalize(self._read_device(provider_id))

	# --- actions ----------------------------------------------------------------

	def lock(self, provider_id):
		return self._v2("POST", f"/Device/{self._device_id(provider_id)}/Lock")

	def locate(self, provider_id):
		return self._v2("GET", f"/Device/{self._device_id(provider_id)}/Location")

	def wipe(self, provider_id, mode="selective"):
		"""Full: Miradore Wipe. Selective: Miradore Retire, only where it keeps
		personal data. The device's enrollment is read fresh from Miradore first —
		the registry's copy may be a day old, and this is not a call to guess on."""
		device_id = self._device_id(provider_id)
		row = self._read_device(device_id)
		kind = _child(row, "Client").get("ManagementType")
		inv = _child(row, "InvDevice")
		model = " ".join(filter(None, (inv.get("Model"), inv.get("MarketingName"))))

		if mode == "full":
			# An empty body takes Miradore's documented defaults — the same as its console.
			response = self._v2("POST", f"/Device/{device_id}/Wipe", json={})
			return {
				"miradore_action": "Wipe",
				"management_type": kind,
				"effect": _MIRADORE_WIPE_EFFECT.get(
					kind, "Wipe sent; the effect depends on how the device is enrolled."
				),
				"response": response,
			}
		if mode == "selective":
			refusal = miradore_selective_wipe_refusal(kind, model)
			if refusal:
				raise MDMProviderError(refusal, retryable=False)
			response = self._v2("DELETE", f"/Device/{device_id}")
			return {
				"miradore_action": "Retire",
				"management_type": kind,
				"effect": "Company data (managed apps, settings, profiles) removed and the device unenrolled "
				"from Miradore; personal data kept. It can no longer be locked or located.",
				"response": response,
			}
		raise MDMProviderError(f"Unknown wipe mode: {mode}", retryable=False)

	# --- normalization -------------------------------------------------------------

	def _normalize(self, row):
		inv = _child(row, "InvDevice")
		os_info = _child(row, "InvOS")
		security = _child(row, "Security")
		platform = row.get("Platform") or os_info.get("Platform")
		described = " ".join(
			filter(None, (inv.get("Model"), inv.get("MarketingName"), inv.get("DeviceType")))
		)
		is_tablet = "ipad" in described.lower() or "tablet" in (inv.get("DeviceType") or "").lower()
		if platform == "iOS" and "ipad" in described.lower():
			platform = "iPadOS"
		return ProviderDevice(
			provider=self.key,
			provider_id=str(row.get("ID") or ""),
			serial=inv.get("SerialNumber"),
			imei=inv.get("IMEI"),
			mac=inv.get("WiFiMAC"),
			platform=platform,
			device_type="Tablet" if is_tablet else ("Phone" if platform in ("Android", "iOS") else None),
			device_name=inv.get("DeviceName"),
			model=inv.get("MarketingName") or inv.get("Model"),
			manufacturer=inv.get("Manufacturer"),
			os_version=os_info.get("Version"),
			screen_lock=_tristate(security.get("PasscodeSet"), {"Yes"}, {"No"}),
			encryption=_tristate(security.get("EncryptionStatus"), {"Enabled"}, {"Disabled"}),
			compliance_state=None,  # derived from screen lock + encryption by mapping
			last_seen=row.get("LastReported"),
			assignee_hint=_child(row, "User").get("Email"),
			raw=row,
		)


# ---------------------------------------------------------------------------
# Action1 (computer RMM) — OAuth2 client-credentials; actions are automations
# ---------------------------------------------------------------------------

#: Action1 endpoint IDs are UUIDs. Checked before an ID goes into a URL or an
#: automation payload, because Action1 treats ``{"id": "all"}`` as every endpoint
#: in the organization.
_ACTION1_ENDPOINT_ID = re.compile(
	r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

#: Minutes Action1 keeps trying an automation on an endpoint that is offline.
#: Short for reboot and scripts — an hour-old request should not fire tomorrow
#: morning on somebody's laptop — and a day for patches, Action1's own default.
_ACTION1_RETRY_MINUTES = {"reboot": "60", "run_script": "60", "deploy_patch": "1440"}

#: The warning the user sees, and the minutes they get, before a reboot.
_ACTION1_REBOOT_MESSAGE = (
	"IT has requested a restart of this computer. Please save your work; it will restart automatically."
)
_ACTION1_REBOOT_GRACE_MINUTES = 10


class Action1Provider(MDMProvider):
	"""Action1, over ``api/3.0``.

	**Inventory.** ``GET /endpoints/managed/{org}``, paged with ``from``/``limit``.
	Field names are the ones Action1 actually returns (``serial``, ``OS``,
	``name``, ``MAC``, ``user``), checked against a payload archived on production.
	The previous normalizer read ``os_version``/``model``/``serial_number`` and got
	nothing, which is why the first four laptops synced as "Discovered <serial>"
	with no OS.

	**Actions.** Action1 has no per-endpoint reboot/script/update endpoints. Each
	action runs as an automation, ``POST /automations/instances/{org}``, with one
	action template (``reboot`` / ``run_script`` / ``deploy_update``) and the one
	endpoint as its target. The payloads follow Action1's OpenAPI document and its
	PSAction1 client's templates.
	"""

	key = "Action1"
	BASE = "https://app.action1.com/api/3.0"
	PAGE_LIMIT = 200  # PSAction1's default page size
	MAX_PAGES = 50

	def _org(self):
		org = self.settings.get("action1_org_id")
		if not org:
			raise MDMProviderError("Action1 Organization ID is required.", retryable=False)
		return org

	@staticmethod
	def _endpoint_id(provider_id):
		value = str(provider_id or "").strip()
		if not _ACTION1_ENDPOINT_ID.match(value):
			raise MDMProviderError(f"'{value}' is not an Action1 endpoint ID.", retryable=False)
		return value

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
		try:
			resp = requests.post(
				f"{self.BASE}/oauth2/token",
				data={"client_id": client_id, "client_secret": client_secret},
				headers={"Accept": "application/json"},
				timeout=60,
			)
		except requests.RequestException as exc:
			raise MDMProviderError(f"Action1 token request failed: {type(exc).__name__}") from None
		if resp.status_code >= 400:
			raise MDMProviderError(
				f"Action1 token request failed: {resp.status_code} {resp.text[:500]}",
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
		try:
			resp = requests.request(
				method,
				f"{self.BASE}{path}",
				headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
				timeout=kwargs.pop("timeout", 60),
				**kwargs,
			)
		except requests.RequestException as exc:
			raise MDMProviderError(f"Action1 {method} {path} failed: {str(exc)[:300]}") from None
		if resp.status_code == 401 and _retry:
			self._refresh_token()
			return self._request(method, path, _retry=False, **kwargs)
		if resp.status_code >= 400:
			raise MDMProviderError(
				f"Action1 {method} {path} failed: {resp.status_code} {resp.text[:500]}",
				status_code=resp.status_code,
			)
		return resp.json() if resp.text else {}

	@staticmethod
	def _items(data):
		return data if isinstance(data, list) else (data.get("items") or []) if isinstance(data, dict) else []

	# --- inventory -------------------------------------------------------------

	def list_devices(self):
		org = self._org()
		devices = []
		for page in range(self.MAX_PAGES):
			rows = self._items(
				self._request(
					"GET",
					f"/endpoints/managed/{org}",
					params={"limit": self.PAGE_LIMIT, "from": page * self.PAGE_LIMIT},
				)
			)
			devices.extend(self._normalize(row) for row in rows)
			if len(rows) < self.PAGE_LIMIT:
				return devices
		raise MDMProviderError(
			f"Action1 returned more than {self.MAX_PAGES * self.PAGE_LIMIT} endpoints; "
			"raise MAX_PAGES before syncing a partial list."
		)

	def get_device(self, provider_id):
		endpoint_id = self._endpoint_id(provider_id)
		return self._normalize(self._request("GET", f"/endpoints/managed/{self._org()}/{endpoint_id}"))

	# --- actions (automations) ---------------------------------------------------

	def _run_automation(self, provider_id, kind, label, action):
		"""Run one action template on one endpoint, now. Returns Action1's instance."""
		endpoint_id = self._endpoint_id(provider_id)
		payload = {
			"name": f"ERPNext: {label}"[:120],
			"retry_minutes": _ACTION1_RETRY_MINUTES[kind],
			"endpoints": [{"id": endpoint_id, "type": "Endpoint"}],
			"actions": [action],
		}
		instance = self._request("POST", f"/automations/instances/{self._org()}", json=payload)
		return {
			"automation_instance": _first(instance, "id") if isinstance(instance, dict) else None,
			"response": instance,
		}

	def reboot(self, provider_id):
		reboot_options = {
			"auto_reboot": "yes",
			"show_message": "yes",
			"message_text": _ACTION1_REBOOT_MESSAGE,
			"timeout": _ACTION1_REBOOT_GRACE_MINUTES,
		}
		return self._run_automation(
			provider_id,
			"reboot",
			"Reboot",
			{
				"template_id": "reboot",
				"name": "Reboot",
				# The schema lists show_message as required at this level as well as
				# inside reboot_options; send it in both places.
				"params": {
					"display_summary": "Reboot requested from ERPNext",
					"show_message": "yes",
					"reboot_options": reboot_options,
				},
			},
		)

	def run_script(self, provider_id, script):
		if not (script or "").strip():
			raise MDMProviderError("A 'script' is required for run_script.", retryable=False)
		platform, language = action1_script_target(self.get_device(provider_id).platform)
		if not platform:
			raise MDMProviderError(
				"Action1 can only run scripts on Windows, Mac and Linux endpoints.", retryable=False
			)
		return self._run_automation(
			provider_id,
			"run_script",
			"Run script",
			{
				"template_id": "run_script",
				"name": "Run Script",
				"params": {
					"display_summary": "Script run from ERPNext",
					"platform": platform,
					"run_script_language": language,
					"run_script_text": script,
				},
			},
		)

	def deploy_patch(self, provider_id, patch):
		"""Deploy one update, by Action1 package ID, at the version Action1 offers
		for this endpoint. Refused unless the package is in the endpoint's own
		missing-updates list, so a typo cannot become a deployment."""
		endpoint_id = self._endpoint_id(provider_id)
		package_id = (patch or "").strip()
		if not package_id:
			raise MDMProviderError("A 'patch' (Action1 update package ID) is required.", retryable=False)
		missing = self._items(
			self._request(
				"GET",
				f"/endpoints/managed/{self._org()}/{endpoint_id}/missing-updates",
				params={"limit": self.PAGE_LIMIT},
			)
		)
		package = next(
			(item for item in missing if isinstance(item, dict) and item.get("id") == package_id), None
		)
		if not package:
			raise MDMProviderError(
				f"'{package_id}' is not among this endpoint's {len(missing)} missing updates in Action1. "
				"Use the package ID Action1 lists for the device.",
				retryable=False,
			)
		versions = [v for v in (package.get("versions") or []) if isinstance(v, dict) and v.get("version")]
		if not versions:
			raise MDMProviderError(f"Action1 lists no version of '{package_id}' to deploy.", retryable=False)
		version = versions[0]["version"]
		result = self._run_automation(
			provider_id,
			"deploy_patch",
			f"Deploy {package.get('name') or package_id}",
			{
				"template_id": "deploy_update",
				"name": "Deploy Update",
				"params": {
					"display_summary": f"Deploy {package.get('name') or package_id} {version} from ERPNext",
					"scope": "Specified",
					"packages": [{package_id: version}],
					# No automatic restart: a reboot is its own action, taken deliberately.
					"reboot_options": {"auto_reboot": "no"},
				},
			},
		)
		return {**result, "package": package_id, "version": version}

	# --- normalization -------------------------------------------------------------

	def _normalize(self, row):
		return ProviderDevice(
			provider=self.key,
			provider_id=str(_first(row, "id", default="")),
			serial=_first(row, "serial"),
			mac=_first(row, "MAC"),
			platform=_first(row, "platform") or "Windows",
			device_type="Laptop",
			device_name=_first(row, "name", "device_name"),
			os_version=_first(row, "OS"),
			manufacturer=_first(row, "manufacturer"),
			last_seen=_first(row, "last_seen"),
			assignee_hint=_first(row, "user"),
			raw=row,
		)


# ---------------------------------------------------------------------------
# Mock (no credentials) — canned data + recorded actions
# ---------------------------------------------------------------------------

# Module-level action recorder so tests can assert what was dispatched.
MOCK_ACTIONS = []

# Shaped like each provider's real records (Miradore's parsed API v1 XML,
# Action1's endpoint JSON), so Mock mode exercises the real normalizers.
_MOCK_DEVICES = {
	"Miradore": [
		{
			"ID": "1001",
			"Platform": "Android",
			"Status": "Active",
			"OnlineStatus": "Active",
			"InvDevice": {
				"SerialNumber": "MIRSN1001",
				"IMEI": "350000000000001",
				"Manufacturer": "Google",
				"Model": "Pixel 8",
				"DeviceName": "Tech 1 phone",
			},
			"InvOS": {"Version": "14"},
			"User": {"Email": "tech1@sapphirefountains.com"},
			"Client": {"ManagementType": "AndroidProfileOwner"},
			"Security": {"PasscodeSet": "Yes", "EncryptionStatus": "Enabled"},
		},
		{
			"ID": "1002",
			"Platform": "iOS",
			"Status": "Active",
			"OnlineStatus": "Active",
			"InvDevice": {
				"SerialNumber": "MIRSN1002",
				"IMEI": "350000000000002",
				"Manufacturer": "Apple",
				"Model": "iPhone15,2",
				"MarketingName": "iPhone 14 Pro",
				"DeviceName": "Tech 2 phone",
			},
			"InvOS": {"Version": "18.1"},
			"User": {"Email": "tech2@sapphirefountains.com"},
			"Client": {"ManagementType": "iOSUnsupervised"},
			"Security": {"PasscodeSet": "Yes", "EncryptionStatus": "Disabled"},
		},
	],
	"Action1": [
		{
			"id": "0f6c1c9e-0000-4000-8000-000000002001",
			"name": "OFFICE-1",
			"serial": "A1SN2001",
			"platform": "Windows",
			"OS": "Windows 11 (23H2)",
			"manufacturer": "Dell Inc.",
			"MAC": "00:11:22:33:44:01",
			"user": "SAPPHIRE\\office1",
			"last_seen": "2026-06-24_08-51-12",
		},
		{
			"id": "0f6c1c9e-0000-4000-8000-000000002002",
			"name": "OFFICE-2",
			"serial": "A1SN2002",
			"platform": "Mac",
			"OS": "macOS 15.1",
			"manufacturer": "Apple",
			"MAC": "00:11:22:33:44:02",
			"user": "office2",
			"last_seen": "2026-06-24_08-51-12",
		},
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
