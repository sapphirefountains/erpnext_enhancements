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

STUBBED = (
	"frappe",
	"frappe.utils",
	"frappe.model",
	"frappe.model.document",
	"requests",
	"erpnext_enhancements.api.device_management",
)
OURS = (
	"erpnext_enhancements.mdm_integration.client",
	"erpnext_enhancements.mdm_integration.routing",
	"erpnext_enhancements.mdm_integration.utils",
	"erpnext_enhancements.mdm_integration.mapping",
	"erpnext_enhancements.mdm_integration.actions",
	"erpnext_enhancements.mdm_integration.sync",
	"erpnext_enhancements.mdm_integration.api",
	"erpnext_enhancements.device_management.compliance",
	"erpnext_enhancements.device_management.doctype.managed_device.managed_device",
	"erpnext_enhancements.utils.error_throttle",
)

client = None
mapping = None
actions = None
sync = None
api = None
controller = None
#: The stand-in ``frappe`` module, for tests that set its flags, roles or database.
fstub = None
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


class FakeValidationError(Exception):
	pass


class AttrDict(dict):
	"""``frappe._dict``: missing keys read as None."""

	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class StubDocument:
	"""Enough of ``frappe.model.document.Document`` to run a controller's save the way v16 does:
	the stored row is loaded as the doc before save, then ``validate``, then the row is stored,
	then ``on_update``. Rows live in ``STATE["devices"]``. A missing field reads as None."""

	doctype = "Managed Device"

	def __init__(self, fields=None):
		self.__dict__.update(fields or {})
		self.__dict__["flags"] = AttrDict()
		self.__dict__["_doc_before_save"] = None

	def __getattr__(self, key):
		if key.startswith("__"):
			raise AttributeError(key)
		return None

	def get(self, key, default=None):
		value = self.__dict__.get(key)
		return default if value is None else value

	def set(self, key, value):
		setattr(self, key, value)

	def as_dict(self):
		return self._fields()

	def is_new(self):
		return self.name not in STATE.get("devices", {})

	def get_doc_before_save(self):
		return self._doc_before_save

	def _fields(self):
		return {k: v for k, v in self.__dict__.items() if k not in ("flags", "_doc_before_save")}

	def _store(self):
		self.validate()
		STATE.setdefault("devices", {})[self.name] = self._fields()
		self.on_update()

	def save(self, ignore_permissions=None):
		if ignore_permissions is not None:
			self.flags.ignore_permissions = ignore_permissions
		stored = STATE.get("devices", {}).get(self.name)
		self.__dict__["_doc_before_save"] = type(self)(dict(stored)) if stored else None
		self._store()
		return self

	def insert(self, ignore_permissions=None):
		if ignore_permissions is not None:
			self.flags.ignore_permissions = ignore_permissions
		if not self.name:
			self.name = f"DEV-{len(STATE.get('devices', {})) + 1}"
		self._store()
		return self

	def validate(self):
		pass

	def on_update(self):
		pass

	def add_comment(self, comment_type, text=None, **kwargs):
		STATE.setdefault("comments", []).append((comment_type, text))


class FakeDevice(dict):
	"""Enough of a Managed Device for the executor."""

	def __getattr__(self, key):
		return self.get(key)

	def as_dict(self):
		return dict(self)

	def add_comment(self, comment_type, text=None, **kwargs):
		if STATE.get("comment_fails"):
			raise RuntimeError("comment insert failed")
		STATE.setdefault("comments", []).append((comment_type, text))


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

	def get_value(doctype, filters, fieldname=None, **kwargs):
		# Only the sync's lookups of a Managed Device by its fields reach this.
		for name, row in STATE.get("devices", {}).items():
			if all(row.get(key) == value for key, value in filters.items()):
				return name
		return None

	frappe.db = types.SimpleNamespace(
		commit=lambda: None,
		set_value=lambda *a, **k: STATE.setdefault("set_value", []).append(a),
		get_value=get_value,
	)
	frappe.get_single = lambda doctype: STATE.get("settings") or FakeSettings()
	frappe.log_error = lambda *a, **k: None
	frappe.local = types.SimpleNamespace(conf=types.SimpleNamespace(get=lambda key: "test"))
	frappe._ = lambda text: text
	frappe.PermissionError = FakePermissionError
	frappe.ValidationError = FakeValidationError
	frappe.session = types.SimpleNamespace(user="manager@example.com")
	frappe.flags = AttrDict()
	frappe.get_roles = lambda user=None: list(STATE.get("roles") or ["Device Manager"])
	frappe.get_traceback = lambda: "traceback"
	frappe.whitelist = lambda *a, **k: (lambda f: f)

	def throw(message, exc=None, title=None, **kwargs):
		raise (exc or FakeValidationError)(message)

	def get_doc(doctype, name=None):
		if isinstance(doctype, dict):  # a Device Action Log being written
			return types.SimpleNamespace(insert=lambda **k: STATE.setdefault("logs", []).append(doctype))
		if doctype == "Managed Device" and "devices" in STATE:
			return controller.ManagedDevice(dict(STATE["devices"][name]))
		return STATE["device"]

	def new_doc(doctype):
		return controller.ManagedDevice({})

	frappe.new_doc = new_doc

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
	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = StubDocument
	model.document = document
	frappe.model = model

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
		("frappe.model", model),
		("frappe.model.document", document),
		("requests", requests),
		("erpnext_enhancements.api.device_management", device_management),
	):
		sys.modules[name] = module


def setUpModule():
	global client, mapping, actions, sync, api, controller, fstub
	for name in STUBBED + OURS:
		_saved[name] = sys.modules.pop(name, None)
	_install_stubs()
	from erpnext_enhancements.device_management.doctype.managed_device import managed_device
	from erpnext_enhancements.mdm_integration import actions as actions_module
	from erpnext_enhancements.mdm_integration import api as api_module
	from erpnext_enhancements.mdm_integration import client as client_module
	from erpnext_enhancements.mdm_integration import mapping as mapping_module
	from erpnext_enhancements.mdm_integration import sync as sync_module

	client = client_module
	mapping = mapping_module
	actions = actions_module
	sync = sync_module
	api = api_module
	controller = managed_device
	fstub = sys.modules["frappe"]


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
	"""A stand-in provider that records every call. No network, no vendor API."""

	key = "Miradore"

	def supports(self, action):
		return True

	def _record(self, action, *args):
		STATE.setdefault("provider_calls", []).append((action,) + args)
		return {"recorded": action}

	def wipe(self, provider_id, mode="selective"):
		STATE.setdefault("wiped", []).append((provider_id, mode))
		return {"miradore_action": "Retire" if mode == "selective" else "Wipe"}

	def lock(self, provider_id):
		return self._record("lock", provider_id)

	def locate(self, provider_id):
		return self._record("locate", provider_id)

	def reboot(self, provider_id):
		return self._record("reboot", provider_id)

	def run_script(self, provider_id, script):
		return self._record("run_script", provider_id, script)

	def deploy_patch(self, provider_id, patch):
		return self._record("deploy_patch", provider_id, patch)


def _device_state(link_state, ownership, **extra):
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
	STATE.update(extra)
	actions.get_provider_for = lambda device, settings: (RecordingProvider(), "Miradore")


class ExecutorTests(unittest.TestCase):
	def _run(self, link_state, ownership, mode, action="wipe", **kwargs):
		_device_state(link_state, ownership)
		return actions.execute_device_action("DEV-1", action, mode=mode, source="UI", **kwargs)

	def _provider_was_called(self):
		return bool(STATE.get("wiped") or STATE.get("provider_calls"))

	def test_full_wipe_of_an_unconfirmed_device_is_refused_before_the_provider(self):
		# A discovered device's ownership defaults to Company, so the BYOD guard alone
		# would have let a personal phone through to a factory reset.
		with self.assertRaises(client.MDMProviderError) as ctx:
			self._run("Discovered", "Company", "full")
		self.assertIn("nobody has confirmed", str(ctx.exception))
		self.assertNotIn("wiped", STATE)
		(log,) = STATE["logs"]
		self.assertEqual((log["action"], log["success"]), ("wipe", 0))

	def test_selective_wipe_of_an_unconfirmed_device_is_refused_too(self):
		with self.assertRaises(client.MDMProviderError) as ctx:
			self._run("Discovered", "Company", "selective")
		self.assertIn("Confirm the device", str(ctx.exception))
		self.assertFalse(self._provider_was_called())

	def test_full_wipe_of_a_confirmed_company_device_goes_through(self):
		self._run("Managed", "Company", "full")
		self.assertEqual(STATE["wiped"], [("1001", "full")])

	def test_selective_wipe_of_a_confirmed_device_goes_through(self):
		self._run("Managed", "Company", "selective")
		self.assertEqual(STATE["wiped"], [("1001", "selective")])

	def test_byod_is_never_fully_wiped(self):
		with self.assertRaises(client.MDMProviderError):
			self._run("Managed", "BYOD", "full")
		self.assertNotIn("wiped", STATE)

	def test_a_personal_device_accepts_locate_and_lock(self):
		for action in ("locate", "lock"):
			with self.subTest(action=action):
				result = self._run("Managed", "BYOD", None, action=action)
				self.assertTrue(result["ok"])
				self.assertEqual(STATE["provider_calls"][0][0], action)
				(log,) = STATE["logs"]
				self.assertEqual((log["action"], log["success"]), (action, 1))

	def test_every_other_action_is_refused_on_a_personal_device_before_any_provider_call(self):
		for action, mode, extra in (
			("wipe", "selective", {}),
			("wipe", "full", {}),
			("wipe", None, {}),
			("reboot", None, {}),
			("run_script", None, {"script": "Get-Date"}),
			("deploy_patch", None, {"patch": "pkg-1"}),
		):
			with self.subTest(action=action, mode=mode):
				with self.assertRaises(client.MDMProviderError) as ctx:
					self._run("Managed", "BYOD", mode, action=action, **extra)
				self.assertIn("personally owned", str(ctx.exception))
				self.assertIn(f"'{action}'", str(ctx.exception))
				self.assertFalse(self._provider_was_called())
				self.assertNotIn("comments", STATE)
				self.assertNotIn("notified", STATE)
				(log,) = STATE["logs"]
				self.assertEqual((log["action"], log["success"]), (action, 0))

	def test_the_personal_device_list_is_an_allowlist(self):
		"""An action nobody has listed is refused on a personal device, whatever the provider
		says it supports, and the wipe settings do not widen it."""
		self.assertEqual(actions.BYOD_ACTIONS, frozenset({"locate", "lock"}))
		with self.assertRaises(client.MDMProviderError) as ctx:
			self._run("Managed", "BYOD", None, action="enable_lost_mode")
		self.assertIn("personally owned", str(ctx.exception))
		self.assertFalse(self._provider_was_called())

		_device_state("Managed", "BYOD")
		STATE["settings"] = FakeSettings(block_full_wipe_byod=0, allow_full_wipe_corporate=1)
		with self.assertRaises(client.MDMProviderError):
			actions.execute_device_action("DEV-1", "wipe", mode="full", source="UI")
		self.assertFalse(self._provider_was_called())

	def test_a_company_device_is_not_held_to_the_personal_device_list(self):
		self._run("Managed", "Company", "selective")
		self.assertEqual(STATE["wiped"], [("1001", "selective")])

	def test_the_form_draws_only_locate_and_lock_for_a_personal_device(self):
		source = (
			Path(__file__).resolve().parents[1]
			/ "device_management" / "doctype" / "managed_device" / "managed_device.js"
		).read_text(encoding="utf-8")
		self.assertIn("const personal = frm.doc.ownership === 'BYOD';", source)
		self.assertIn("if (!personal) remote(__('Remote Wipe'), 'wipe', {", source)
		self.assertIn("frm.doc.mdm_provider === 'Action1' && !personal", source)
		# Lock and Locate are drawn whoever owns the device.
		self.assertIn("\t\t\t\tremote(__('Remote Lock'), 'lock',", source)
		self.assertIn("\t\t\t\tremote(__('Locate'), 'locate');", source)

	def test_every_action_is_refused_on_an_unconfirmed_device_before_any_provider_call(self):
		for action, mode, extra in (
			("wipe", "full", {}),
			("wipe", "selective", {}),
			("lock", None, {}),
			("locate", None, {}),
			("reboot", None, {}),
			("run_script", None, {"script": "Get-Date"}),
			("deploy_patch", None, {"patch": "pkg-1"}),
		):
			with self.subTest(action=action, mode=mode):
				with self.assertRaises(client.MDMProviderError) as ctx:
					self._run("Discovered", "Company", mode, action=action, **extra)
				self.assertIn("nobody has confirmed", str(ctx.exception))
				self.assertIn(f"'{action}'", str(ctx.exception))
				self.assertFalse(self._provider_was_called())
				self.assertNotIn("comments", STATE)
				self.assertNotIn("notified", STATE)
				(log,) = STATE["logs"]
				self.assertEqual((log["action"], log["success"]), (action, 0))

	def test_every_action_runs_on_a_confirmed_company_device(self):
		for action, extra in (
			("lock", {}),
			("locate", {}),
			("reboot", {}),
			("run_script", {"script": "Get-Date"}),
			("deploy_patch", {"patch": "pkg-1"}),
		):
			with self.subTest(action=action):
				result = self._run("Managed", "Company", None, action=action, **extra)
				self.assertTrue(result["ok"])
				self.assertEqual(STATE["provider_calls"][0][0], action)

	def test_locate_writes_a_comment_naming_who_asked_and_when_then_notifies(self):
		self._run("Managed", "Company", None, action="locate")
		((comment_type, text),) = STATE["comments"]
		self.assertEqual(comment_type, "Info")
		self.assertIn("manager@example.com", text)
		self.assertIn("2026-09-22 12:00:00", text)
		(note,) = STATE["notified"]
		self.assertEqual(note["device"], "DEV-1")
		self.assertIn("locate", note["subject"])
		self.assertIn("manager@example.com", note["message"])

	def test_lock_and_wipe_still_notify_and_reboot_still_does_not(self):
		self._run("Managed", "Company", None, action="lock")
		self.assertEqual(len(STATE["notified"]), 1)
		self._run("Managed", "Company", "selective", action="wipe")
		self.assertEqual(len(STATE["notified"]), 1)
		self._run("Managed", "Company", None, action="reboot")
		self.assertNotIn("notified", STATE)

	def test_only_locate_writes_a_timeline_comment(self):
		self._run("Managed", "Company", None, action="lock")
		self.assertNotIn("comments", STATE)

	def test_a_locate_that_cannot_be_recorded_sends_nothing(self):
		_device_state("Managed", "Company", comment_fails=True)
		with self.assertRaises(client.MDMProviderError):
			actions.execute_device_action("DEV-1", "locate", source="UI")
		self.assertFalse(self._provider_was_called())


def _stored_device(ownership, link_state="Managed", roles=("Device Manager",), **extra):
	"""One stored Managed Device, DEV-1, for the real controller to load and save."""
	STATE.clear()
	STATE["roles"] = list(roles)
	STATE["devices"] = {
		"DEV-1": {
			"name": "DEV-1",
			"device_name": "Crew phone",
			"device_type": "Phone",
			"platform": "Android",
			"status": "In Stock",
			"ownership": ownership,
			"mdm_provider": "Miradore",
			"mdm_provider_device_id": "1001",
			"mdm_link_state": link_state,
			**extra,
		}
	}
	fstub.flags = AttrDict()


def _stored(field):
	return STATE["devices"]["DEV-1"][field]


class OwnershipRuleTests(unittest.TestCase):
	"""``ManagedDevice._guard_ownership_change``, run over the real controller's save."""

	def _change(self, old, new, roles, **save_kwargs):
		_stored_device(old, roles=roles)
		doc = fstub.get_doc("Managed Device", "DEV-1")
		doc.ownership = new
		return doc.save(**save_kwargs)

	def test_byod_to_company_by_a_device_manager_is_refused_and_says_who_can(self):
		with self.assertRaises(FakePermissionError) as ctx:
			self._change("BYOD", "Company", ("Device Manager",))
		self.assertIn("Only a System Manager", str(ctx.exception))
		self.assertEqual(_stored("ownership"), "BYOD")
		self.assertNotIn("comments", STATE)

	def test_byod_to_company_by_a_system_manager_is_saved_and_noted(self):
		self._change("BYOD", "Company", ("System Manager",))
		self.assertEqual(_stored("ownership"), "Company")
		((comment_type, text),) = STATE["comments"]
		self.assertEqual(comment_type, "Info")
		self.assertIn("from BYOD to Company", text)
		self.assertIn("manager@example.com", text)
		self.assertIn("2026-09-22 12:00:00", text)

	def test_company_to_byod_is_open_to_a_device_manager_and_a_system_manager_and_noted(self):
		for roles in (("Device Manager",), ("System Manager",)):
			with self.subTest(roles=roles):
				self._change("Company", "BYOD", roles)
				self.assertEqual(_stored("ownership"), "BYOD")
				((_type, text),) = STATE["comments"]
				self.assertIn("from Company to BYOD", text)

	def test_a_save_that_leaves_ownership_alone_writes_no_comment(self):
		for roles in (("Device Manager",), ("System Manager",)):
			for ownership in ("Company", "BYOD"):
				with self.subTest(roles=roles, ownership=ownership):
					_stored_device(ownership, roles=roles)
					doc = fstub.get_doc("Managed Device", "DEV-1")
					doc.notes = "moved to the north shelf"
					doc.save()
					self.assertEqual(_stored("notes"), "moved to the north shelf")
					self.assertNotIn("comments", STATE)

	def test_a_save_with_ignore_permissions_never_turns_byod_into_company(self):
		# Server code runs as Administrator, who holds System Manager; the role alone is not enough.
		with self.assertRaises(FakePermissionError):
			self._change("BYOD", "Company", ("Administrator", "System Manager"), ignore_permissions=True)
		self.assertEqual(_stored("ownership"), "BYOD")
		self.assertNotIn("comments", STATE)

	def test_a_new_device_may_start_as_either(self):
		for ownership in ("Company", "BYOD"):
			with self.subTest(ownership=ownership):
				STATE.clear()
				doc = fstub.new_doc("Managed Device")
				doc.device_name = "New phone"
				doc.status = "In Stock"
				doc.ownership = ownership
				doc.insert()
				self.assertEqual(STATE["devices"][doc.name]["ownership"], ownership)
				self.assertNotIn("comments", STATE)

	def test_confirm_device_lets_a_device_manager_set_company_on_a_discovered_device(self):
		# The provider hinted BYOD when the sync created it; the person confirming says Company.
		_stored_device("BYOD", link_state="Discovered")
		result = api.confirm_device("DEV-1", "Company")
		self.assertEqual(result["ownership"], "Company")
		self.assertEqual((_stored("ownership"), _stored("mdm_link_state")), ("Company", "Managed"))
		texts = [text for _type, text in STATE["comments"]]
		self.assertEqual(len(texts), 2)
		self.assertIn("from BYOD to Company", texts[0])
		self.assertIn("Confirmed as a Company device", texts[1])
		self.assertIsNone(getattr(fstub.flags, controller.CONFIRMING_DISCOVERED_FLAG, None))

	def test_confirm_device_on_a_discovered_device_either_way(self):
		for stored, chosen, comments in (("Company", "BYOD", 2), ("Company", "Company", 1), ("BYOD", "BYOD", 1)):
			with self.subTest(stored=stored, chosen=chosen):
				_stored_device(stored, link_state="Discovered")
				api.confirm_device("DEV-1", chosen)
				self.assertEqual(_stored("ownership"), chosen)
				self.assertEqual(len(STATE["comments"]), comments)

	def test_confirm_device_on_a_device_no_longer_discovered_is_refused(self):
		_stored_device("BYOD", link_state="Managed")
		with self.assertRaises(FakeValidationError) as ctx:
			api.confirm_device("DEV-1", "Company")
		self.assertIn("not awaiting confirmation", str(ctx.exception))
		self.assertEqual(_stored("ownership"), "BYOD")
		self.assertNotIn("comments", STATE)

	def test_the_confirm_flag_opens_only_its_own_device_while_stored_as_discovered(self):
		flag = controller.CONFIRMING_DISCOVERED_FLAG
		for link_state, named in (("Managed", "DEV-1"), ("Unmanaged", "DEV-1"), ("Discovered", "DEV-2")):
			with self.subTest(link_state=link_state, named=named):
				_stored_device("BYOD", link_state=link_state)
				setattr(fstub.flags, flag, named)
				doc = fstub.get_doc("Managed Device", "DEV-1")
				doc.ownership = "Company"
				with self.assertRaises(FakePermissionError):
					doc.save()
				self.assertEqual(_stored("ownership"), "BYOD")

	def test_confirm_device_clears_its_flag_when_the_save_fails(self):
		# An Assigned device with no assignee fails another rule in validate.
		_stored_device("BYOD", link_state="Discovered", status="Assigned")
		with self.assertRaises(FakeValidationError):
			api.confirm_device("DEV-1", "Company")
		self.assertIsNone(getattr(fstub.flags, controller.CONFIRMING_DISCOVERED_FLAG, None))
		self.assertEqual(_stored("ownership"), "BYOD")

	def test_the_sync_never_changes_ownership_on_an_existing_device(self):
		# Run as Administrator (System Manager) with a provider hint of Company.
		_stored_device("BYOD", roles=("Administrator", "System Manager"))
		pd = client.ProviderDevice(provider="Miradore", provider_id="1001", os_version="15", ownership_hint="Company")
		result = mapping.upsert_device(pd)
		self.assertEqual((result["action"], result["device"]), ("updated", "DEV-1"))
		self.assertEqual(_stored("ownership"), "BYOD")
		self.assertEqual(_stored("os_version"), "15")
		self.assertNotIn("comments", STATE)

	def test_a_provider_hint_still_sets_ownership_on_a_device_the_sync_creates(self):
		for hint, expected in (("BYOD", "BYOD"), ("Company", "Company"), (None, "Company"), ("Personal", "Company")):
			with self.subTest(hint=hint):
				STATE.clear()
				STATE["devices"] = {}
				pd = client.ProviderDevice(provider="Miradore", provider_id="2002", platform="Android", ownership_hint=hint)
				result = mapping.upsert_device(pd)
				self.assertEqual(result["action"], "created")
				row = STATE["devices"][result["device"]]
				self.assertEqual((row["ownership"], row["mdm_link_state"]), (expected, "Discovered"))

	def test_the_confirm_prompt_preselects_nothing_and_requires_a_choice(self):
		source = (
			Path(__file__).resolve().parents[1]
			/ "device_management" / "doctype" / "managed_device" / "managed_device.js"
		).read_text(encoding="utf-8")
		start = source.index("if (frm.doc.mdm_link_state === 'Discovered') {")
		block = source[start : source.index("mdm_integration.api.confirm_device", start)]
		self.assertIn("options: '\\nCompany\\nBYOD',", block)
		self.assertIn("reqd: 1,", block)
		self.assertNotIn("default:", block)


class SyncTests(unittest.TestCase):
	def test_only_managed_devices_are_flagged_unmanaged(self):
		# A Discovered device that went Unmanaged would come back Managed, unconfirmed.
		STATE.clear()
		sync.flag_unmanaged("Miradore", {"1001"})
		(filters,) = STATE["get_all"]
		self.assertEqual(filters, {"mdm_provider": "Miradore", "mdm_link_state": "Managed"})


if __name__ == "__main__":
	unittest.main()
