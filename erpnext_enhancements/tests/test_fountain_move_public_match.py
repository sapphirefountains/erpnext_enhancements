"""Bench-free pytest suite: a public fountain-move submission that matches an existing party waits for staff.

Nik, 2026-11-19: when a request from the public form matches an existing customer (by email or
phone), the request is saved on its own and marked as a possible match; a staff member confirms
before it joins the customer's record, and nothing the visitor typed changes an existing Customer,
Contact or Address. A public request that matches nobody, and a request staff started themselves
(a Desk entry, or the first submission through a staff member's emailed link), convert as before.

These tests RUN ``conversion._convert``, the real ``matching.resolve_party`` and
``api.confirm_match`` over an in-memory stand-in for ``frappe``, and assert on what was inserted,
saved and written.

A plain pytest module with its own ``frappe`` stub, so CI runs it in a ``python -m pytest`` step of
its own (``python -m unittest`` cannot collect function tests).
"""

import importlib
import json
import re
import sys
import types

import pytest


class Thrown(Exception):
	pass


class Doc(dict):
	"""A dict that answers attribute reads, ``get``/``set`` and ``append``, like a Document."""

	def __getattr__(self, name):
		try:
			return self[name]
		except KeyError:
			raise AttributeError(name) from None

	def set(self, field, value):
		self[field] = value

	def append(self, field, row):
		row = Doc(row)
		self.setdefault(field, []).append(row)
		return row


EXISTING_CUSTOMER = "Existing Household Residence"
EXISTING_CONTACT = "Existing Person-Existing Household Residence"
SHARED_PHONE = "(801) 555-0142"


class World:
	"""Records and the writes made to them."""

	def __init__(self):
		self.records = {
			"Customer": {
				EXISTING_CUSTOMER: Doc(
					name=EXISTING_CUSTOMER,
					custom_accounts_phone_number="801-555-0142",
					custom_accounts_email_address="household@example.org",
				),
			},
			"Contact": {
				EXISTING_CONTACT: Doc(
					name=EXISTING_CONTACT,
					custom_phone_number="801.555.0142",
					custom_mobile_number="",
					custom_email="",
					links=[Doc(link_doctype="Customer", link_name=EXISTING_CUSTOMER)],
				),
			},
			"Fountain Move Invite": {"INV-0001": Doc(name="INV-0001", sent_by="staff@example.com")},
		}
		self.users = {
			"staff@example.com": {"user_type": "System User", "enabled": 1},
			"owner@example.com": {"user_type": "System User", "enabled": 1},
			"Administrator": {"user_type": "System User", "enabled": 1},
			"portal@example.com": {"user_type": "Website User", "enabled": 1},
		}
		self.requests = {}
		self.inserted = []  # (doctype, doc)
		self.saved_existing = []  # (doctype, name) of records that existed before the run
		self.foreign_writes = []  # db.set_value on anything but a Fountain Move Request
		self.enqueued = []
		self.photo_targets = []
		self.duplicate_alerts = []
		self.converted_alerts = []
		self.roles = ["Sales Manager"]
		self.counter = 0

	# -- requests -------------------------------------------------------------

	def add_request(self, **overrides):
		self.counter += 1
		name = overrides.pop("name", f"FMR-2026-{self.counter:05d}")
		base = dict(
			name=name,
			owner="Guest",
			creation=f"2026-11-19 09:{self.counter:02d}:00",
			status="Queued",
			source_channel="Public Form",
			invite=None,
			first_name="Visitor",
			last_name="Example",
			email="visitor@example.net",
			phone=SHARED_PHONE,
			property_type="Residential",
			address_line1="12 Elm St",
			address_line2="",
			city="Draper",
			state="UT",
			pincode="84020",
			country="United States",
			purchase_location="Cactus & Tropicals Draper",
			purchase_location_address="",
			fountain_weight_lbs=80,
			water_access=1,
			electricity_access=0,
			contact_consent=1,
			terms_accepted=1,
			terms_accepted_on=None,
			submitted_on=None,
			address_autocompleted=0,
			conversion_attempts=0,
			honeypot_tripped=0,
			turnstile_verdict="Passed",
			match_confirmed_by=None,
			match_confirmed_on=None,
			created_customer=None,
			created_address=None,
			created_contact=None,
			created_lead=None,
			created_opportunity=None,
		)
		req = Request({**base, **overrides})
		self.requests[name] = req
		return req

	# -- lookups ---------------------------------------------------------------

	def exists(self, doctype, name):
		if isinstance(name, dict) or not name:
			return None
		if doctype == "Fountain Move Request":
			return name if name in self.requests else None
		if name in self.records.get(doctype, {}):
			return name
		if any(dt == doctype and doc.name == name for dt, doc in self.inserted):
			return name
		return None

	def doc(self, doctype, name):
		if doctype == "Fountain Move Request":
			return self.requests[name]
		if name in self.records.get(doctype, {}):
			return Existing(self, doctype, self.records[doctype][name])
		for dt, doc in self.inserted:
			if dt == doctype and doc.name == name:
				return doc
		raise Thrown(f"{doctype} {name} not found")


class Request(Doc):
	is_spam = False

	def db_set(self, field, value=None, update_modified=True):
		self[field] = value

	def mark_converting(self):
		self["conversion_attempts"] = int(self.get("conversion_attempts") or 0) + 1
		self["status"] = "Converting"


class Existing(Doc):
	"""A record that existed before the run; saving it is recorded."""

	def __init__(self, world, doctype, data):
		super().__init__(data)
		self._world = world
		self._doctype = doctype

	def save(self, *a, **k):
		self._world.saved_existing.append((self._doctype, self["name"]))


class New(Doc):
	"""A record built by the run; inserting it is recorded."""

	def __init__(self, world, data):
		super().__init__(data)
		self.flags = types.SimpleNamespace()
		self._world = world

	def insert(self, *a, **k):
		self._world.counter += 1
		self["name"] = f"NEW-{self['doctype']}-{self._world.counter}"
		self._world.inserted.append((self["doctype"], self))
		return self

	def save(self, *a, **k):
		return self


def _digits(value):
	return re.sub(r"\D", "", str(value or ""))


def install_frappe_stub(world):
	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")

	def flt(value=0, precision=None):
		try:
			return float(value or 0)
		except (TypeError, ValueError):
			return 0.0

	def escape_html(text):
		mapping = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}
		return "".join(mapping.get(char, char) for char in str(text))

	utils.flt = flt
	utils.cint = lambda value=0, *a, **k: int(flt(value))
	utils.escape_html = escape_html
	utils.now_datetime = lambda: "2026-11-19 10:00:00"
	utils.nowdate = lambda: "2026-11-19"
	utils.add_to_date = lambda value=None, **k: value
	utils.get_url_to_form = lambda dt, dn: f"https://erp.example.com/desk/{dt}/{dn}"
	frappe.utils = utils

	def throw(message=None, *a, **k):
		raise Thrown(message)

	def whitelist(*a, **k):
		def register(fn):
			fn.whitelist_kwargs = k
			return fn

		return register

	def only_for(roles, *a, **k):
		if not set(roles) & set(world.roles):
			raise Thrown("Not permitted")

	def get_value(doctype, filters=None, fieldname=None, *a, **k):
		if isinstance(filters, dict):
			return None
		if doctype == "Fountain Move Request":
			return world.requests[filters].get(fieldname) if filters in world.requests else None
		if doctype == "User":
			return world.users.get(filters, {}).get(fieldname)
		if doctype == "Fountain Move Invite":
			invite = world.records["Fountain Move Invite"].get(filters)
			return invite.get(fieldname) if invite and isinstance(fieldname, str) else None
		if k.get("as_dict"):
			return {}
		return None

	def set_value(doctype, name, field, value=None, update_modified=True):
		updates = field if isinstance(field, dict) else {field: value}
		if doctype == "Fountain Move Request":
			world.requests[name].update(updates)
		else:
			world.foreign_writes.append((doctype, name, updates))

	def sql(query, params=None, *a, **k):
		doctype = re.search(r"FROM `tab(\w+)`", query).group(1)
		field = re.search(r"`(\w+)` IS NOT NULL", query).group(1)
		tail = params[0].lstrip("%")
		return [
			(name,)
			for name, doc in world.records.get(doctype, {}).items()
			if doc.get(field) and _digits(doc.get(field)).endswith(tail)
		]

	def get_all(doctype, filters=None, pluck=None, order_by=None, limit=None, **k):
		filters = filters or {}
		if doctype == "Dynamic Link":
			if "parent" in filters:
				contact = world.records["Contact"].get(filters["parent"]) or {}
				return [link["link_name"] for link in contact.get("links", []) if link["link_doctype"] == "Customer"]
			return [
				name
				for name, contact in world.records["Contact"].items()
				if any(link["link_name"] == filters.get("link_name") for link in contact.get("links", []))
			]
		if doctype == "Fountain Move Request":
			rows = sorted(
				(req for req in world.requests.values() if req.get("invite") == filters.get("invite")),
				key=lambda req: (req["creation"], req["name"]),
			)
			return [req["name"] for req in rows][: limit or None]
		if doctype in ("Contact", "Customer"):
			((field, value),) = filters.items()
			return [name for name, doc in world.records.get(doctype, {}).items() if doc.get(field) == value]
		return []

	def get_doc(arg, name=None, *a, **k):
		if isinstance(arg, dict):
			return New(world, arg)
		return world.doc(arg, name)

	frappe.throw = throw
	frappe._ = lambda message=None, *a, **k: message
	frappe.whitelist = whitelist
	frappe.only_for = only_for
	frappe.as_json = lambda value, **k: json.dumps(value, default=str)
	frappe.db = types.SimpleNamespace(
		get_value=get_value,
		get_single_value=lambda *a, **k: None,
		exists=lambda doctype, name=None, *a, **k: world.exists(doctype, name),
		has_column=lambda *a, **k: True,
		set_value=set_value,
		sql=sql,
		commit=lambda: None,
		rollback=lambda: None,
	)
	frappe.session = types.SimpleNamespace(user="Guest")
	frappe.set_user = lambda user: setattr(frappe.session, "user", user)
	frappe.get_all = get_all
	frappe.get_doc = get_doc
	frappe.get_cached_doc = lambda *a, **k: Doc(fmr_default_owner="owner@example.com")
	frappe.get_meta = lambda *a, **k: types.SimpleNamespace(has_field=lambda field: True)
	frappe.defaults = types.SimpleNamespace(get_defaults=lambda: {"company": "Example Co"})
	frappe.enqueue = lambda method, **kwargs: world.enqueued.append((method, kwargs))
	frappe.log_error = lambda *a, **k: None
	frappe.get_traceback = lambda *a, **k: "traceback"
	frappe.sendmail = lambda *a, **k: None
	frappe.DuplicateEntryError = type("DuplicateEntryError", (Exception,), {})
	frappe.DoesNotExistError = type("DoesNotExistError", (Exception,), {})

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	return frappe


@pytest.fixture()
def world(monkeypatch):
	world = World()
	install_frappe_stub(world)
	for name in list(sys.modules):
		if name.startswith("erpnext_enhancements."):
			del sys.modules[name]
	conversion = importlib.import_module("erpnext_enhancements.crm_enhancements.fountain_move.conversion")
	world.conversion = conversion
	world.api = importlib.import_module("erpnext_enhancements.crm_enhancements.fountain_move.api")
	world.photos = conversion.photos

	monkeypatch.setattr(
		conversion.contacts_ux,
		"_insert_customer_link_first",
		lambda doc, customer: doc.append("links", {"link_doctype": "Customer", "link_name": customer}),
	)
	monkeypatch.setattr(conversion.photos, "fan_out", lambda req, targets: world.photo_targets.append(targets))
	monkeypatch.setattr(
		conversion.notify, "notify_duplicate_review", lambda req, match: world.duplicate_alerts.append(match)
	)
	monkeypatch.setattr(
		conversion.notify, "notify_converted", lambda req, owner: world.converted_alerts.append(owner)
	)
	return world


def _inserted_doctypes(world):
	return sorted(doctype for doctype, _doc in world.inserted)


def _assert_existing_untouched(world):
	assert world.saved_existing == []
	assert [w for w in world.foreign_writes if w[1] in (EXISTING_CUSTOMER, EXISTING_CONTACT)] == []
	assert world.records["Contact"][EXISTING_CONTACT]["custom_email"] == ""


# ---------------------------------------------------------------------- public form, a match


def test_a_public_request_with_a_phone_match_is_held_and_changes_nothing(world):
	req = world.add_request()
	world.conversion._convert(req.name, 0)

	assert req.status == "Duplicate Review"
	assert req.match_basis == "Phone"
	suggestion = json.loads(req.match_candidates)
	assert suggestion["held_for_confirmation"] == 1
	assert suggestion["customer"] == EXISTING_CUSTOMER
	assert {"type": "Customer", "name": EXISTING_CUSTOMER} in suggestion["candidates"]
	assert world.inserted == []
	assert world.photo_targets == []
	assert world.enqueued == []
	assert world.converted_alerts == []
	assert len(world.duplicate_alerts) == 1
	assert not req.created_customer and not req.created_opportunity
	_assert_existing_untouched(world)


def test_a_public_request_with_an_email_match_is_held_too(world):
	req = world.add_request(email="household@example.org", phone="(385) 555-0199")
	world.conversion._convert(req.name, 0)

	assert req.status == "Duplicate Review"
	assert req.match_basis == "Email"
	assert world.inserted == []
	_assert_existing_untouched(world)


def test_retrying_a_held_request_holds_it_again(world):
	req = world.add_request()
	world.conversion._convert(req.name, 0)
	req["status"] = "Queued"
	world.conversion._convert(req.name, 1)

	assert req.status == "Duplicate Review"
	assert world.inserted == []
	_assert_existing_untouched(world)


# ---------------------------------------------------------------------- public form, no match


def test_a_public_request_that_matches_nobody_converts_as_before(world):
	req = world.add_request(email="stranger@example.net", phone="(435) 555-0111")
	world.conversion._convert(req.name, 0)

	assert req.status == "Converted"
	assert _inserted_doctypes(world) == ["Address", "Contact", "Customer", "Lead", "Opportunity"]
	assert not req.get("reused_customer")
	assert world.converted_alerts == ["owner@example.com"]
	_assert_existing_untouched(world)


# ---------------------------------------------------------------------- staff-started requests


def test_a_desk_entry_with_a_match_reuses_the_account_as_before(world):
	req = world.add_request(owner="staff@example.com")
	world.conversion._convert(req.name, 0)

	assert req.status == "Converted"
	assert req.created_customer == EXISTING_CUSTOMER
	assert req.created_contact == EXISTING_CONTACT
	assert req.reused_customer == 1
	# Today's behavior for a staff-started request: the reused Contact's blank email is filled.
	assert ("Contact", EXISTING_CONTACT) in world.saved_existing
	assert _inserted_doctypes(world) == ["Address", "Lead", "Opportunity"]


def test_the_first_request_through_a_staff_invite_converts_as_before(world):
	req = world.add_request(invite="INV-0001", source_channel="Emailed Link")
	world.conversion._convert(req.name, 0)

	assert req.status == "Converted"
	assert req.created_customer == EXISTING_CUSTOMER
	assert world.converted_alerts == ["staff@example.com"]


# ---------------------------------------------------------------------- what a caller cannot claim


def test_a_later_request_through_the_same_invite_is_treated_as_public(world):
	world.add_request(invite="INV-0001", source_channel="Emailed Link", email="first@example.net")
	second = world.add_request(invite="INV-0001", source_channel="Emailed Link")
	world.conversion._convert(second.name, 0)

	assert second.status == "Duplicate Review"
	assert world.inserted == []
	_assert_existing_untouched(world)


def test_staff_status_comes_from_the_server_record_not_the_submission(world):
	is_staff = world.conversion.is_staff_started
	assert not is_staff(world.add_request(source_channel="Staff Entry"))
	assert not is_staff(world.add_request(owner="portal@example.com"))
	assert not is_staff(world.add_request(invite="INV-9999"))
	assert is_staff(world.add_request(owner="staff@example.com"))

	held = world.add_request(source_channel="Staff Entry")
	world.conversion._convert(held.name, 0)
	assert held.status == "Duplicate Review"
	assert world.inserted == []


def test_the_guest_allowlist_reaches_no_field_that_decides_staff_or_confirmation(world):
	from erpnext_enhancements.crm_enhancements.fountain_move import INTAKE_FIELD_MAP

	server_set = {
		"owner",
		"source_channel",
		"invite",
		"status",
		"created_customer",
		"reused_customer",
		"match_candidates",
		"match_confirmed_by",
		"match_confirmed_on",
	}
	assert not server_set & set(INTAKE_FIELD_MAP.values())
	assert not server_set & set(INTAKE_FIELD_MAP)


# ---------------------------------------------------------------------- staff confirm the match


def _held(world):
	req = world.add_request()
	world.conversion._convert(req.name, 0)
	assert req.status == "Duplicate Review"
	return req


def test_confirm_match_requires_post_and_a_triage_role(world):
	assert world.api.confirm_match.whitelist_kwargs == {"methods": ["POST"]}

	req = _held(world)
	world.roles = ["Sales User"]
	with pytest.raises(Thrown):
		world.api.confirm_match(req.name, EXISTING_CUSTOMER)
	assert req.status == "Duplicate Review"
	assert not req.match_confirmed_by


def test_confirm_match_refuses_a_request_that_is_not_held(world):
	req = world.add_request(status="New")
	with pytest.raises(Thrown):
		world.api.confirm_match(req.name, EXISTING_CUSTOMER)
	assert not req.match_confirmed_by

	held = _held(world)
	with pytest.raises(Thrown):
		world.api.confirm_match(held.name, "No Such Customer")
	assert held.status == "Duplicate Review"


def test_confirming_links_and_creates_only_the_opportunity(world):
	req = _held(world)
	world.conversion.frappe.session.user = "manager@example.com"
	result = world.api.confirm_match(req.name, EXISTING_CUSTOMER)

	assert result["queued"] is True
	assert req.status == "Queued"
	assert req.match_confirmed_by == "manager@example.com"
	assert req.match_confirmed_on
	method, kwargs = world.enqueued[-1]
	assert method.endswith("fountain_move.conversion.run_conversion")
	assert kwargs["docname"] == req.name

	world.conversion._convert(req.name, 1)

	assert req.status == "Converted"
	assert req.created_customer == EXISTING_CUSTOMER
	assert req.reused_customer == 1
	assert _inserted_doctypes(world) == ["Opportunity"]
	opportunity = world.inserted[0][1]
	assert opportunity["party_name"] == EXISTING_CUSTOMER
	assert opportunity["opportunity_from"] == "Customer"
	assert opportunity["contact_person"] is None
	assert opportunity["primary_address"] is None
	assert "visitor@example.net" in opportunity["custom_general_scope_description"]
	assert req.created_opportunity == opportunity["name"]
	assert not req.created_contact and not req.created_address and not req.created_lead
	assert world.photo_targets == [[("Opportunity", opportunity["name"])]]
	assert world.photos._target_for(req, "Customer") is None
	assert world.photos._target_for(req, "Opportunity") == opportunity["name"]
	_assert_existing_untouched(world)


def test_a_confirmed_request_skips_the_matcher_even_with_several_candidates(world):
	world.records["Customer"]["Second Household"] = Doc(
		name="Second Household", custom_accounts_phone_number="8015550142"
	)
	req = world.add_request()
	world.conversion._convert(req.name, 0)
	assert req.status == "Duplicate Review"
	assert "held_for_confirmation" not in json.loads(req.match_candidates)

	world.api.confirm_match(req.name, "Second Household")
	world.conversion._convert(req.name, 1)

	assert req.status == "Converted"
	assert _inserted_doctypes(world) == ["Opportunity"]
	assert world.inserted[0][1]["party_name"] == "Second Household"
	assert world.saved_existing == []
	assert world.foreign_writes == []
