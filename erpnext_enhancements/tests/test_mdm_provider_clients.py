"""Bench-free tests of the requests the MDM provider clients send (mdm_integration/client.py).

Until v1.506.0 both Live adapters were written against guessed endpoints and none
of them existed. Miradore sync called a list endpoint API v2 does not have, and
failed 12,803 times; every remote action for both providers called an invented
path. Nothing noticed, because nothing ever compared a request with the vendor's
spec. This suite does. Each expectation below is quoted from:

* Miradore API v1.19 (PDF): ``GET {host}/{site}/API/Device?auth=&select=&options=rows=,page=``,
  XML under ``Content/Items``, the ``Client.ManagementType`` enum.
* ``online.miradore.com/swagger/v2/swagger.json``: ``POST /api/v2/Device/{id}/Lock``,
  ``/Wipe`` (WipeConfiguration, no selective field), ``GET /Location``, and
  ``DELETE /api/v2/Device/{id}`` (Retire).
* Action1's OpenAPI 3.1 document (app.action1.com/apidocs) and PSAction1:
  ``POST /automations/instances/{org}`` with the ``reboot`` / ``run_script`` /
  ``deploy_update`` templates, ``packages: [{package_id: version}]``.
* A real Action1 endpoint payload archived on production (field names).

What matters most is what must NOT be sent: a selective wipe that Miradore would
turn into a factory reset, an Action1 automation aimed at ``all``, a Miradore API
key inside an error message. Those are asserted by counting requests.

A stubbed ``frappe`` and a recording ``requests`` are installed in ``setUpModule``
and removed in ``tearDownModule``.

Run: python -m unittest erpnext_enhancements.tests.test_mdm_provider_clients -v
"""

import datetime
import sys
import types
import unittest
from pathlib import Path
from urllib.parse import quote_plus

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

API_KEY = "1_AaDf234sdf8!4"  # the spec's own example key; note the '!' encodes to %21
ORG = "29ccb73d-fd7f-4e06-afa9-28d637de8a34"
ENDPOINT = "bd7a6e9f-4658-4399-ac34-5d98caa81ac2"

STUBBED = ("frappe", "frappe.utils", "requests", "erpnext_enhancements.api.device_management")
OURS = (
	"erpnext_enhancements.mdm_integration.client",
	"erpnext_enhancements.mdm_integration.routing",
	"erpnext_enhancements.mdm_integration.utils",
	"erpnext_enhancements.mdm_integration.mapping",
	"erpnext_enhancements.mdm_integration.actions",
	"erpnext_enhancements.mdm_integration.sync",
	"erpnext_enhancements.utils.error_throttle",
)

client = None
mapping = None
actions = None
sync = None
#: Per-test state for the executor/sync stubs.
STATE = {}
_saved = {}
CALLS = []
RESPONSES = []


class FakeResponse:
	def __init__(self, status_code=200, text="", json_data=None):
		self.status_code = status_code
		self.text = text
		self._json = json_data

	def json(self):
		if self._json is None:
			raise ValueError("no json")
		return self._json


class RequestException(Exception):
	pass


class FakePermissionError(Exception):
	pass


class FakeDevice(dict):
	"""Enough of a Managed Device for the executor."""

	def __getattr__(self, key):
		return self.get(key)

	def as_dict(self):
		return dict(self)


class FakeSettings(dict):
	def get(self, key, default=None):
		return super().get(key, default)

	def get_password(self, fieldname, raise_exception=True):
		return super().get(fieldname)

	def set_password(self, fieldname, value):
		self[fieldname] = value

	def save(self, ignore_permissions=False):
		pass

	def __getattr__(self, key):  # a Document reads fields as attributes too
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


def _install_stubs():
	frappe = types.ModuleType("frappe")
	frappe.db = types.SimpleNamespace(
		commit=lambda: None,
		set_value=lambda *a, **k: STATE.setdefault("set_value", []).append(a),
	)
	frappe.get_single = lambda doctype: STATE.get("settings") or FakeSettings()
	frappe.log_error = lambda *a, **k: None
	frappe.local = types.SimpleNamespace(conf=types.SimpleNamespace(get=lambda key: "test"))
	frappe._ = lambda text: text
	frappe.PermissionError = FakePermissionError
	frappe.session = types.SimpleNamespace(user="manager@example.com")
	frappe.get_roles = lambda user=None: ["Device Manager"]
	frappe.get_traceback = lambda: "traceback"

	def throw(message, exc=Exception):
		raise exc(message)

	def get_doc(doctype, name=None):
		if isinstance(doctype, dict):  # a Device Action Log being written
			return types.SimpleNamespace(insert=lambda **k: STATE.setdefault("logs", []).append(doctype))
		return STATE["device"]

	def get_all(doctype, filters=None, fields=None, **kwargs):
		STATE.setdefault("get_all", []).append(filters)
		return []

	frappe.throw = throw
	frappe.get_doc = get_doc
	frappe.get_all = get_all
	utils = types.ModuleType("frappe.utils")
	now = datetime.datetime(2026, 9, 22, 12, 0, 0)
	utils.now_datetime = lambda: now
	utils.today = lambda: "2026-09-22"
	utils.get_datetime = lambda value: value
	utils.add_to_date = lambda base, seconds=0, as_datetime=True, **kw: base + datetime.timedelta(
		seconds=seconds
	)
	frappe.utils = utils

	requests = types.ModuleType("requests")
	requests.RequestException = RequestException

	def record(method, url, **kwargs):
		CALLS.append({"method": method, "url": url, **kwargs})
		if not RESPONSES:
			raise AssertionError(f"unexpected request: {method} {url}")
		response = RESPONSES.pop(0)
		if isinstance(response, Exception):
			raise response
		return response

	requests.request = record
	requests.get = lambda url, **kw: record("GET", url, **kw)
	requests.post = lambda url, **kw: record("POST", url, **kw)

	device_management = types.ModuleType("erpnext_enhancements.api.device_management")
	device_management.MANAGER_ROLES = {"Device Manager", "System Manager"}
	device_management._notify_device_managers = lambda **kw: STATE.setdefault("notified", []).append(kw)

	for name, module in (
		("frappe", frappe),
		("frappe.utils", utils),
		("requests", requests),
		("erpnext_enhancements.api.device_management", device_management),
	):
		sys.modules[name] = module


def setUpModule():
	global client, mapping, actions, sync
	for name in STUBBED + OURS:
		_saved[name] = sys.modules.pop(name, None)
	_install_stubs()
	from erpnext_enhancements.mdm_integration import actions as actions_module
	from erpnext_enhancements.mdm_integration import client as client_module
	from erpnext_enhancements.mdm_integration import mapping as mapping_module
	from erpnext_enhancements.mdm_integration import sync as sync_module

	client = client_module
	mapping = mapping_module
	actions = actions_module
	sync = sync_module


def tearDownModule():
	for name in STUBBED + OURS:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


def _reset(*responses):
	CALLS.clear()
	RESPONSES.clear()
	RESPONSES.extend(responses)


def _device_xml(
	device_id="1001",
	management="AndroidProfileOwner",
	status="Active",
	model="SM-S918U",
	marketing="Galaxy S23 Ultra",
	platform="Android",
):
	return f"""<Device>
	<ID>{device_id}</ID><Platform>{platform}</Platform><Status>{status}</Status>
	<OnlineStatus>Active</OnlineStatus><LastReported>22.09.2026 10.15.00</LastReported>
	<InvDevice><SerialNumber>R58N{device_id}</SerialNumber><IMEI>35000000000{device_id}</IMEI>
		<WiFiMAC>AA:BB:CC:DD:EE:01</WiFiMAC><Manufacturer>samsung</Manufacturer><Model>{model}</Model>
		<MarketingName>{marketing}</MarketingName><DeviceName>Crew phone {device_id}</DeviceName>
		<DeviceType>Phone</DeviceType></InvDevice>
	<InvOS><Version>14</Version></InvOS>
	<User><Email>tech{device_id}@sapphirefountains.com</Email></User>
	<Client><ManagementType>{management}</ManagementType></Client>
	<Security><PasscodeSet>Yes</PasscodeSet><EncryptionStatus>Enabled</EncryptionStatus></Security>
</Device>"""


def _content(*devices, count=None, namespace=False):
	xmlns = ' xmlns="http://www.online.miradore.com/xmlns/api/1.0"' if namespace else ""
	count = len(devices) if count is None else count
	return f'<Content{xmlns}><Items count="{count}">{"".join(devices)}</Items></Content>'


def miradore():
	return client.MiradoreProvider(
		FakeSettings(miradore_instance_name="sapphirefountains", miradore_api_key=API_KEY)
	)


def action1():
	return client.Action1Provider(
		FakeSettings(
			action1_org_id=ORG,
			action1_access_token="tok",
			action1_token_expires_at=datetime.datetime(2026, 9, 22, 13, 0, 0),
		)
	)


class MiradoreReadTests(unittest.TestCase):
	def test_list_uses_api_v1_with_the_key_in_auth(self):
		_reset(FakeResponse(text=_content(_device_xml())))
		devices = miradore().list_devices()
		(call,) = CALLS
		self.assertEqual(call["method"], "GET")
		self.assertEqual(call["url"], "https://online.miradore.com/sapphirefountains/API/Device")
		self.assertEqual(call["params"]["auth"], API_KEY)
		self.assertEqual(call["params"]["options"], "rows=100,page=1")
		self.assertIn("Client.ManagementType", call["params"]["select"].split(","))
		self.assertEqual(len(devices), 1)

	def test_the_old_v2_list_path_is_gone(self):
		# GET /api/v2/devices does not exist; the 12,803 failures came from it.
		_reset(FakeResponse(text=_content()))
		miradore().list_devices()
		self.assertNotIn("/api/v2", CALLS[0]["url"])

	def test_normalizes_the_xml_tree(self):
		_reset(FakeResponse(text=_content(_device_xml())))
		(device,) = miradore().list_devices()
		self.assertEqual(device.provider, "Miradore")
		self.assertEqual(device.provider_id, "1001")
		self.assertEqual(device.serial, "R58N1001")
		self.assertEqual(device.imei, "350000000001001")
		self.assertEqual(device.platform, "Android")
		self.assertEqual(device.device_type, "Phone")
		self.assertEqual(device.device_name, "Crew phone 1001")
		self.assertEqual(device.model, "Galaxy S23 Ultra")
		self.assertEqual(device.os_version, "14")
		self.assertIs(device.screen_lock, True)
		self.assertIs(device.encryption, True)
		self.assertEqual(device.assignee_hint, "tech1001@sapphirefountains.com")
		self.assertEqual(device.raw["Client"]["ManagementType"], "AndroidProfileOwner")

	def test_ipad_is_a_tablet_on_ipados(self):
		_reset(
			FakeResponse(text=_content(_device_xml(platform="iOS", model="iPad13,1", marketing="iPad Air")))
		)
		(device,) = miradore().list_devices()
		self.assertEqual((device.platform, device.device_type), ("iPadOS", "Tablet"))

	def test_namespaced_response_and_count_on_content(self):
		body = _content(_device_xml(), namespace=True).replace('<Items count="1">', "<Items>")
		body = body.replace("<Content ", '<Content count="1" ')
		_reset(FakeResponse(text=body))
		(device,) = miradore().list_devices()
		self.assertEqual(device.provider_id, "1001")

	def test_pages_until_the_count_is_read(self):
		first = _content(*[_device_xml(str(1000 + i)) for i in range(100)], count=150)
		second = _content(*[_device_xml(str(2000 + i)) for i in range(50)], count=150)
		_reset(FakeResponse(text=first), FakeResponse(text=second))
		devices = miradore().list_devices()
		self.assertEqual(len(devices), 150)
		self.assertEqual([c["params"]["options"] for c in CALLS], ["rows=100,page=1", "rows=100,page=2"])

	def test_deleted_and_unmanaged_devices_are_not_synced(self):
		_reset(
			FakeResponse(
				text=_content(
					_device_xml("1"), _device_xml("2", status="Deleted"), _device_xml("3", status="Unmanaged")
				)
			)
		)
		self.assertEqual([d.provider_id for d in miradore().list_devices()], ["1"])

	def test_http_error_keeps_the_status_and_never_the_key(self):
		_reset(FakeResponse(status_code=401, text=f"Invalid auth {API_KEY} / {quote_plus(API_KEY)}"))
		with self.assertRaises(client.MDMProviderError) as ctx:
			miradore().list_devices()
		self.assertEqual(ctx.exception.status_code, 401)
		self.assertFalse(ctx.exception.retryable)
		self.assertNotIn(API_KEY, str(ctx.exception))
		self.assertNotIn(quote_plus(API_KEY), str(ctx.exception))

	def test_transport_error_is_redacted_and_unchained(self):
		# requests puts the whole URL -- auth= included -- in its exception text, and
		# the sync stores error text on the Sync Log and in the Error Log.
		url = f"https://online.miradore.com/sapphirefountains/API/Device?auth={quote_plus(API_KEY)}&select=ID"
		_reset(RequestException(f"Max retries exceeded with url: {url}"))
		with self.assertRaises(client.MDMProviderError) as ctx:
			miradore().list_devices()
		self.assertNotIn(quote_plus(API_KEY), str(ctx.exception))
		self.assertTrue(
			ctx.exception.__suppress_context__, "raise ... from None, or the traceback shows the URL"
		)
		self.assertIsNone(ctx.exception.__cause__)

	def test_site_name_must_be_a_bare_name(self):
		provider = client.MiradoreProvider(
			FakeSettings(miradore_instance_name="evil.example/x", miradore_api_key=API_KEY)
		)
		_reset()
		with self.assertRaises(client.MDMProviderError):
			provider.list_devices()
		self.assertEqual(CALLS, [])


class MiradoreActionTests(unittest.TestCase):
	def _v2(self, call):
		self.assertTrue(call["url"].startswith("https://online.miradore.com/api/v2/"), call["url"])
		self.assertEqual(call["headers"]["X-API-Key"], API_KEY)
		self.assertEqual(call["headers"]["X-Instance-Name"], "sapphirefountains")

	def test_lock(self):
		_reset(FakeResponse(text=""))
		miradore().lock("1001")
		(call,) = CALLS
		self._v2(call)
		self.assertEqual(
			(call["method"], call["url"]), ("POST", "https://online.miradore.com/api/v2/Device/1001/Lock")
		)

	def test_locate(self):
		_reset(FakeResponse(text="[]", json_data=[]))
		miradore().locate("1001")
		self.assertEqual(
			(CALLS[0]["method"], CALLS[0]["url"]),
			("GET", "https://online.miradore.com/api/v2/Device/1001/Location"),
		)

	def test_a_non_numeric_id_never_reaches_a_url(self):
		for bad in ("1001/../User", "", None, "MIR-1001"):
			_reset()
			with self.assertRaises(client.MDMProviderError):
				miradore().lock(bad)
			self.assertEqual(CALLS, [], bad)

	def test_full_wipe_is_miradore_wipe_with_defaults(self):
		_reset(
			FakeResponse(text=_content(_device_xml(management="AndroidDeviceOwner"))), FakeResponse(text="")
		)
		result = miradore().wipe("1001", mode="full")
		read, wipe = CALLS
		self.assertEqual(read["url"], "https://online.miradore.com/sapphirefountains/API/Device/1001")
		self._v2(wipe)
		self.assertEqual(
			(wipe["method"], wipe["url"]), ("POST", "https://online.miradore.com/api/v2/Device/1001/Wipe")
		)
		# WipeConfiguration has additionalProperties: false and no selective field.
		self.assertEqual(wipe["json"], {})
		self.assertEqual(result["miradore_action"], "Wipe")
		self.assertIn("Factory reset", result["effect"])

	def test_full_wipe_of_a_work_profile_reports_what_really_happened(self):
		_reset(FakeResponse(text=_content(_device_xml())), FakeResponse(text=""))
		result = miradore().wipe("1001", mode="full")
		self.assertIn("Work profile", result["effect"])

	def test_selective_wipe_is_a_retire(self):
		_reset(
			FakeResponse(text=_content(_device_xml(management="AndroidProfileOwner"))), FakeResponse(text="")
		)
		result = miradore().wipe("1001", mode="selective")
		_read, retire = CALLS
		self._v2(retire)
		self.assertEqual(
			(retire["method"], retire["url"]), ("DELETE", "https://online.miradore.com/api/v2/Device/1001")
		)
		self.assertEqual(result["miradore_action"], "Retire")

	def test_selective_wipe_never_sends_wipe(self):
		# The old client sent {"selective": true} to .../wipe. Miradore's Wipe has no
		# such field; had it been ignored rather than rejected, that was a factory reset.
		_reset(
			FakeResponse(text=_content(_device_xml(management="iOSUnsupervised", platform="iOS"))),
			FakeResponse(text=""),
		)
		miradore().wipe("1001", mode="selective")
		self.assertFalse([c for c in CALLS if c["url"].endswith("/Wipe")])

	def test_selective_wipe_refused_where_retire_resets(self):
		for management, model in (
			("AndroidDeviceOwner", "Pixel 8"),  # Retire factory-resets a fully managed Android
			("iOSSupervised", "iPad13,1"),  # may be a Shared iPad, which Retire resets
			("Unknown", "Pixel 8"),
			("", "Pixel 8"),
		):
			_reset(
				FakeResponse(text=_content(_device_xml(management=management, model=model, marketing=model)))
			)
			with self.assertRaises(client.MDMProviderError) as ctx:
				miradore().wipe("1001", mode="selective")
			self.assertFalse(ctx.exception.retryable)
			self.assertEqual(len(CALLS), 1, f"{management}: only the read may be sent")

	def test_unknown_mode_is_refused(self):
		_reset(FakeResponse(text=_content(_device_xml())))
		with self.assertRaises(client.MDMProviderError):
			miradore().wipe("1001", mode="everything")
		self.assertEqual(len(CALLS), 1)

	def test_missing_device_is_a_404(self):
		_reset(FakeResponse(text=_content()))
		with self.assertRaises(client.MDMProviderError) as ctx:
			miradore().wipe("1001", mode="full")
		self.assertEqual(ctx.exception.status_code, 404)
		self.assertEqual(len(CALLS), 1)


REAL_ACTION1_ROW = {
	# Field names from a payload archived on production 2026-06-24 (values changed).
	"id": ENDPOINT,
	"name": "Rosewell",
	"device_name": "Rosewell",
	"serial": "5PKJNB4",
	"MAC": "80:C0:1E:5E:21:3A",
	"OS": "Windows 11 (25H2)",
	"platform": "Windows",
	"manufacturer": "Alienware",
	"user": "ROSEWELL\\user",
	"last_seen": "2026-06-24_08-51-12",
	"status": "Connected",
}


class Action1Tests(unittest.TestCase):
	def test_list_pages_with_from_and_limit(self):
		page = [dict(REAL_ACTION1_ROW, id=f"{ENDPOINT[:-4]}{i:04d}") for i in range(200)]
		_reset(
			FakeResponse(text="x", json_data={"items": page}),
			FakeResponse(text="x", json_data={"items": page[:3]}),
		)
		devices = action1().list_devices()
		self.assertEqual(len(devices), 203)
		self.assertEqual(
			[c["params"] for c in CALLS], [{"limit": 200, "from": 0}, {"limit": 200, "from": 200}]
		)
		self.assertEqual(CALLS[0]["url"], f"https://app.action1.com/api/3.0/endpoints/managed/{ORG}")

	def test_normalizes_the_real_field_names(self):
		_reset(FakeResponse(text="x", json_data={"items": [REAL_ACTION1_ROW]}))
		(device,) = action1().list_devices()
		self.assertEqual(device.serial, "5PKJNB4")
		self.assertEqual(device.os_version, "Windows 11 (25H2)")  # was None: the old code read os_version
		self.assertEqual(device.device_name, "Rosewell")  # was "Discovered 5PKJNB4"
		self.assertEqual(device.mac, "80:C0:1E:5E:21:3A")
		self.assertEqual(device.assignee_hint, "ROSEWELL\\user")

	def _automation(self):
		(call,) = [c for c in CALLS if c["method"] == "POST"]
		self.assertEqual(call["url"], f"https://app.action1.com/api/3.0/automations/instances/{ORG}")
		payload = call["json"]
		self.assertEqual(payload["endpoints"], [{"id": ENDPOINT, "type": "Endpoint"}])
		(action,) = payload["actions"]
		return payload, action

	def test_reboot_is_the_reboot_template(self):
		_reset(FakeResponse(text="x", json_data={"id": "inst-1"}))
		result = action1().reboot(ENDPOINT)
		payload, action = self._automation()
		self.assertEqual(action["template_id"], "reboot")
		self.assertEqual(payload["retry_minutes"], "60")
		options = action["params"]["reboot_options"]
		self.assertEqual((options["auto_reboot"], options["show_message"]), ("yes", "yes"))
		self.assertGreater(options["timeout"], 0, "the user gets a grace period")
		self.assertEqual(result["automation_instance"], "inst-1")

	def test_the_old_invented_paths_are_gone(self):
		_reset(FakeResponse(text="x", json_data={}))
		action1().reboot(ENDPOINT)
		self.assertFalse([c for c in CALLS if c["url"].endswith("/reboot")])

	def test_all_and_malformed_ids_never_reach_an_automation(self):
		# {"id": "all"} is Action1's every-endpoint pseudo-group.
		for bad in ("all", "ALL", "", None, "A1-2001", f"{ENDPOINT}/../x"):
			_reset()
			with self.assertRaises(client.MDMProviderError):
				action1().reboot(bad)
			self.assertEqual(CALLS, [], bad)

	def test_run_script_on_windows_is_powershell(self):
		_reset(
			FakeResponse(text="x", json_data=REAL_ACTION1_ROW), FakeResponse(text="x", json_data={"id": "i"})
		)
		action1().run_script(ENDPOINT, "Get-Service")
		_payload, action = self._automation()
		self.assertEqual(action["template_id"], "run_script")
		params = action["params"]
		self.assertEqual(
			(params["platform"], params["run_script_language"], params["run_script_text"]),
			("Windows", "PowerShell", "Get-Service"),
		)

	def test_run_script_on_a_mac_is_bash(self):
		mac = dict(REAL_ACTION1_ROW, platform="Mac", OS="macOS 15.1")
		_reset(FakeResponse(text="x", json_data=mac), FakeResponse(text="x", json_data={}))
		action1().run_script(ENDPOINT, "uptime")
		_payload, action = self._automation()
		self.assertEqual(
			(action["params"]["platform"], action["params"]["run_script_language"]), ("Mac", "Bash")
		)

	def test_deploy_patch_uses_the_version_action1_offers(self):
		missing = {
			"items": [
				{"id": "Google_Chrome_1", "name": "Google Chrome", "versions": [{"version": "126.0.1"}]}
			]
		}
		_reset(FakeResponse(text="x", json_data=missing), FakeResponse(text="x", json_data={"id": "i"}))
		result = action1().deploy_patch(ENDPOINT, "Google_Chrome_1")
		self.assertEqual(
			CALLS[0]["url"],
			f"https://app.action1.com/api/3.0/endpoints/managed/{ORG}/{ENDPOINT}/missing-updates",
		)
		payload, action = self._automation()
		self.assertEqual(action["template_id"], "deploy_update")
		self.assertEqual(action["params"]["scope"], "Specified")
		self.assertEqual(action["params"]["packages"], [{"Google_Chrome_1": "126.0.1"}])
		self.assertEqual(action["params"]["reboot_options"], {"auto_reboot": "no"})
		self.assertEqual(payload["retry_minutes"], "1440")
		self.assertEqual((result["package"], result["version"]), ("Google_Chrome_1", "126.0.1"))

	def test_deploy_patch_refuses_a_package_the_endpoint_is_not_missing(self):
		_reset(FakeResponse(text="x", json_data={"items": [{"id": "Other", "versions": [{"version": "1"}]}]}))
		with self.assertRaises(client.MDMProviderError):
			action1().deploy_patch(ENDPOINT, "Google_Chrome_1")
		self.assertFalse([c for c in CALLS if c["method"] == "POST"])


class MappingTests(unittest.TestCase):
	def _pd(self):
		return client.ProviderDevice(provider="Miradore", provider_id="1001", os_version="14")

	def test_a_discovered_device_stays_discovered(self):
		# Until confirmed, a full wipe is refused; the old code promoted it within the hour.
		doc = types.SimpleNamespace(mdm_link_state="Discovered", compliance_status="Unknown")
		mapping._apply_provider_fields(doc, self._pd())
		self.assertEqual(doc.mdm_link_state, "Discovered")

	def test_other_states_become_managed(self):
		for state in ("Managed", "Unmanaged", None):
			doc = types.SimpleNamespace(mdm_link_state=state, compliance_status="Unknown")
			mapping._apply_provider_fields(doc, self._pd())
			self.assertEqual(doc.mdm_link_state, "Managed", state)


class RecordingProvider:
	key = "Miradore"

	def supports(self, action):
		return True

	def wipe(self, provider_id, mode="selective"):
		STATE.setdefault("wiped", []).append((provider_id, mode))
		return {"miradore_action": "Retire" if mode == "selective" else "Wipe"}


class ExecutorTests(unittest.TestCase):
	def _run(self, link_state, ownership, mode):
		STATE.clear()
		STATE["device"] = FakeDevice(
			name="DEV-1",
			device_name="Crew phone",
			device_type="Phone",
			platform="Android",
			ownership=ownership,
			mdm_provider="Miradore",
			mdm_provider_device_id="1001",
			mdm_link_state=link_state,
		)
		STATE["settings"] = FakeSettings(block_full_wipe_byod=1, allow_full_wipe_corporate=1)
		actions.get_provider_for = lambda device, settings: (RecordingProvider(), "Miradore")
		return actions.execute_device_action("DEV-1", "wipe", mode=mode, source="UI")

	def test_full_wipe_of_an_unconfirmed_device_is_refused_before_the_provider(self):
		# A discovered device's ownership defaults to Company, so the BYOD guard alone
		# would have let a personal phone through to a factory reset.
		with self.assertRaises(client.MDMProviderError) as ctx:
			self._run("Discovered", "Company", "full")
		self.assertIn("nobody has confirmed", str(ctx.exception))
		self.assertNotIn("wiped", STATE)
		(log,) = STATE["logs"]
		self.assertEqual((log["action"], log["success"]), ("wipe", 0))

	def test_selective_wipe_of_an_unconfirmed_device_goes_through(self):
		self._run("Discovered", "Company", "selective")
		self.assertEqual(STATE["wiped"], [("1001", "selective")])

	def test_full_wipe_of_a_confirmed_company_device_goes_through(self):
		self._run("Managed", "Company", "full")
		self.assertEqual(STATE["wiped"], [("1001", "full")])

	def test_byod_is_never_fully_wiped(self):
		with self.assertRaises(client.MDMProviderError):
			self._run("Managed", "BYOD", "full")
		self.assertNotIn("wiped", STATE)


class SyncTests(unittest.TestCase):
	def test_only_managed_devices_are_flagged_unmanaged(self):
		# A Discovered device that went Unmanaged would come back Managed, unconfirmed.
		STATE.clear()
		sync.flag_unmanaged("Miradore", {"1001"})
		(filters,) = STATE["get_all"]
		self.assertEqual(filters, {"mdm_provider": "Miradore", "mdm_link_state": "Managed"})


if __name__ == "__main__":
	unittest.main()
