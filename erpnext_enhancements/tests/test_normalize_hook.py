"""Bench-free tests for normalize-on-save (form kit P2-B) and the consumers it touched.

``utils/normalize_hook.normalize_doc`` runs as ``doc_events["*"]["before_validate"]`` on every
save on the site, so what it must get right is mostly what it leaves alone:

* only a draft save (``docstatus`` 0, ``_action`` "save") is written — never a submit, an
  update-after-submit or a cancel;
* a value the rules cannot normalize is left exactly as typed;
* a doctype the registry does not cover is untouched and costs one set lookup;
* the kill switch and ``in_install`` turn it off;
* it never raises, and it logs at most once per request without the value.

Also here: the registry's child-table parents agree with the doctype JSON, ``hooks.py`` wires
the hook and the ``sf_phone`` Jinja global, boot honours the kill switch, the call-routing
extension guard, and the templates and pages switched to the display form.

A minimal ``frappe`` stub is installed in ``setUpModule`` and removed afterwards.

Run: python -m unittest erpnext_enhancements.tests.test_normalize_hook -v
"""

import ast
import importlib
import json
import sys
import time
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
HOOK_MODULE = "erpnext_enhancements.utils.normalize_hook"
FOUNTAIN_MOVE = "erpnext_enhancements.crm_enhancements.fountain_move"

_saved = {}
hook = None


class _Dict(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class FakeDoc:
    """Just enough of a Document: get/set, docstatus, _action and child rows."""

    def __init__(self, doctype, docstatus=0, action="save", children=None, **values):
        self.doctype = doctype
        self.docstatus = docstatus
        if action is not None:
            self._action = action
        self._values = dict(values)
        self._children = children or []

    def get(self, key, default=None):
        if key == "docstatus":
            return self.docstatus
        return self._values.get(key, default)

    def set(self, key, value):
        self._values[key] = value

    def get_all_children(self):
        return list(self._children)


def _install_frappe():
    frappe = types.ModuleType("frappe")
    frappe._dict = _Dict
    frappe.conf = _Dict()
    frappe.flags = _Dict()
    frappe.logged = []
    frappe.log_error = lambda *a, **k: frappe.logged.append((a, k))
    frappe.defaults = types.SimpleNamespace(get_defaults=lambda: {"company": "Sapphire Fountains"})
    frappe.db = types.SimpleNamespace(get_value=lambda *a, **k: None)
    sys.modules["frappe"] = frappe
    return frappe


def setUpModule():
    global hook
    for name in ("frappe", HOOK_MODULE, FOUNTAIN_MOVE):
        _saved[name] = sys.modules.get(name)
        sys.modules.pop(name, None)
    _install_frappe()
    hook = importlib.import_module(HOOK_MODULE)


def tearDownModule():
    for name, module in _saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def _frappe():
    return sys.modules["frappe"]


class _Reset(unittest.TestCase):
    def setUp(self):
        frappe = _frappe()
        frappe.conf = _Dict()
        frappe.flags = _Dict()
        frappe.logged = []


class TestDraftSave(_Reset):
    def test_registered_fields_are_stored_in_their_one_form(self):
        doc = FakeDoc(
            "Contact",
            mobile_no="801-555-0100 x12",
            custom_phone_number="(801) 555-0101",
            email_id="  Nik@Example.COM ",
            first_name="  Nik  ",
            company_name="Untouched  Co",
        )
        hook.normalize_doc(doc, "before_validate")
        self.assertEqual(doc.get("mobile_no"), "+18015550100,12")
        self.assertEqual(doc.get("custom_phone_number"), "+18015550101")
        self.assertEqual(doc.get("email_id"), "nik@example.com")
        self.assertEqual(doc.get("first_name"), "Nik")
        # Not in the registry: never touched.
        self.assertEqual(doc.get("company_name"), "Untouched  Co")

    def test_the_save_that_creates_the_record_is_a_draft_save(self):
        doc = FakeDoc("Lead", action=None, mobile_no="8015550100")
        hook.normalize_doc(doc)
        self.assertEqual(doc.get("mobile_no"), "+18015550100")

    def test_a_value_that_cannot_be_normalized_is_left_exactly_as_typed(self):
        for typed in ("65-688-000-88", "555-0100", "1-800-FLOWERS", " call Brian ", "a@b.com; c@d.com"):
            field = "email_id" if "@" in typed else "mobile_no"
            doc = FakeDoc("Contact", **{field: typed})
            with self.subTest(typed=typed):
                hook.normalize_doc(doc)
                self.assertEqual(doc.get(field), typed)

    def test_empty_none_and_non_text_values_are_skipped(self):
        doc = FakeDoc("Contact", mobile_no=None, phone="", custom_phone_number=8015550100)
        hook.normalize_doc(doc)
        self.assertIsNone(doc.get("mobile_no"))
        self.assertEqual(doc.get("phone"), "")
        self.assertEqual(doc.get("custom_phone_number"), 8015550100)

    def test_the_production_rule_gaps_save_clean(self):
        doc = FakeDoc(
            "Contact",
            mobile_no="855‑444‑9477",
            phone="801.566.7272,x5115",
            custom_phone_number="tel:+1(770)277-1120",
        )
        hook.normalize_doc(doc)
        self.assertEqual(doc.get("mobile_no"), "+18554449477")
        self.assertEqual(doc.get("phone"), "+18015667272,5115")
        self.assertEqual(doc.get("custom_phone_number"), "+17702771120")


class TestNeverRewritesASubmittedRecord(_Reset):
    def test_submit_update_after_submit_and_cancel_are_skipped(self):
        for docstatus, action in ((1, "submit"), (1, "update_after_submit"), (2, "cancel"), (1, None)):
            doc = FakeDoc("Contact", docstatus=docstatus, action=action, mobile_no="801-555-0100")
            with self.subTest(docstatus=docstatus, action=action):
                hook.normalize_doc(doc)
                self.assertEqual(doc.get("mobile_no"), "801-555-0100")

    def test_a_draft_with_any_other_action_is_skipped(self):
        """v16 runs before_validate for save and submit only; anything else is not ours."""
        doc = FakeDoc("Contact", docstatus=0, action="discard", mobile_no="801-555-0100")
        hook.normalize_doc(doc)
        self.assertEqual(doc.get("mobile_no"), "801-555-0100")

    def test_frappe_v16_runs_before_validate_for_save_and_submit_only(self):
        """The evidence the predicate rests on, pinned where it can be read: the hook relies on
        docstatus because a submit reaches before_validate with docstatus already 1."""
        source = (APP / "utils" / "normalize_hook.py").read_text(encoding="utf-8")
        self.assertIn('``save`` and ``submit``', source)
        self.assertIn("update-after-submit", source.lower())


class TestChildRows(_Reset):
    def test_registered_child_rows_are_normalized_with_their_parent(self):
        phone_row = FakeDoc("Contact Phone", phone="801 555 0100")
        email_row = FakeDoc("Contact Email", email_id="NIK@EXAMPLE.COM")
        link_row = FakeDoc("Dynamic Link", link_name="  Acme  ")
        doc = FakeDoc("Contact", children=[phone_row, email_row, link_row])
        hook.normalize_doc(doc)
        self.assertEqual(phone_row.get("phone"), "+18015550100")
        self.assertEqual(email_row.get("email_id"), "nik@example.com")
        self.assertEqual(link_row.get("link_name"), "  Acme  ")

    def test_a_parent_outside_fields_still_reaches_its_registered_rows(self):
        """Call Routing Rule has no registered field of its own; its Call Routing Target rows do."""
        self.assertNotIn("Call Routing Rule", hook.FIELDS)
        row = FakeDoc("Call Routing Target", phone_number="801-555-0134")
        hook.normalize_doc(FakeDoc("Call Routing Rule", children=[row]))
        self.assertEqual(row.get("phone_number"), "+18015550134")

    def test_rows_of_a_submitted_parent_are_left_alone(self):
        row = FakeDoc("Contact Phone", phone="801 555 0100")
        hook.normalize_doc(FakeDoc("Contact", docstatus=1, action="submit", children=[row]))
        self.assertEqual(row.get("phone"), "801 555 0100")


class TestAddress(_Reset):
    def test_state_and_zip_follow_a_us_country(self):
        for country in ("United States", "", None):
            doc = FakeDoc("Address", country=country, state="utah", pincode="840101234", city="Bountiful, ")
            with self.subTest(country=country):
                hook.normalize_doc(doc)
                self.assertEqual(doc.get("state"), "UT")
                self.assertEqual(doc.get("pincode"), "84010-1234")
                self.assertEqual(doc.get("city"), "Bountiful")

    def test_outside_the_us_they_are_only_trimmed(self):
        doc = FakeDoc("Address", country="Canada", state="  Ontario ", pincode="M5V 3L9 ")
        hook.normalize_doc(doc)
        self.assertEqual(doc.get("state"), "Ontario")
        self.assertEqual(doc.get("pincode"), "M5V 3L9")

    def test_a_four_digit_zip_is_reported_not_padded(self):
        doc = FakeDoc("Address", country="United States", pincode="8401")
        hook.normalize_doc(doc)
        self.assertEqual(doc.get("pincode"), "8401")


class TestUnrelatedDoctypes(_Reset):
    def test_an_unregistered_doctype_is_untouched(self):
        class Exploding(FakeDoc):
            def get(self, key, default=None):
                raise AssertionError("an unregistered doctype must not be read")

        hook.normalize_doc(Exploding("Sales Invoice", contact_mobile="801-555-0100"))
        hook.normalize_doc(Exploding("Error Log"))
        self.assertEqual(_frappe().logged, [])

    def test_the_fast_path_is_cheap(self):
        doc = FakeDoc("ToDo", description="x")
        start = time.perf_counter()
        for _ in range(20000):
            hook.normalize_doc(doc)
        self.assertLess(time.perf_counter() - start, 0.5)

    def test_watched_doctypes_are_fields_plus_child_table_parents(self):
        self.assertEqual(
            hook.WATCHED_DOCTYPES,
            frozenset(hook.FIELDS) | {p for ps in hook.CHILD_TABLE_PARENTS.values() for p in ps},
        )


class TestSwitches(_Reset):
    def test_the_kill_switch_turns_it_off(self):
        _frappe().conf = _Dict({"disable_value_normalization": 1})
        doc = FakeDoc("Contact", mobile_no="801-555-0100")
        hook.normalize_doc(doc)
        self.assertEqual(doc.get("mobile_no"), "801-555-0100")

    def test_a_falsy_kill_switch_leaves_it_on(self):
        _frappe().conf = _Dict({"disable_value_normalization": 0})
        doc = FakeDoc("Contact", mobile_no="801-555-0100")
        hook.normalize_doc(doc)
        self.assertEqual(doc.get("mobile_no"), "+18015550100")

    def test_install_skips_but_migrate_and_import_normalize(self):
        _frappe().flags = _Dict({"in_install": "erpnext_enhancements"})
        doc = FakeDoc("Contact", mobile_no="801-555-0100")
        hook.normalize_doc(doc)
        self.assertEqual(doc.get("mobile_no"), "801-555-0100")
        for flag in ("in_migrate", "in_import", "in_patch"):
            _frappe().flags = _Dict({flag: True})
            doc = FakeDoc("Contact", mobile_no="801-555-0100")
            with self.subTest(flag=flag):
                hook.normalize_doc(doc)
                self.assertEqual(doc.get("mobile_no"), "+18015550100")


class TestNeverRaises(_Reset):
    def test_a_failing_rule_is_swallowed_and_logged_once_without_the_value(self):
        original = hook.normalize.normalize

        def boom(kind, raw, country=None):
            if kind == "email":
                raise RuntimeError(f"cannot read {raw}")
            return original(kind, raw, country)

        hook.normalize.normalize = boom
        try:
            doc = FakeDoc("Contact", email_id="secret@example.com", mobile_no="801-555-0100")
            hook.normalize_doc(doc)
            hook.normalize_doc(FakeDoc("Lead", email_id="other@example.com"))
        finally:
            hook.normalize.normalize = original
        # The failing field stays as typed; the others still normalize.
        self.assertEqual(doc.get("email_id"), "secret@example.com")
        self.assertEqual(doc.get("mobile_no"), "+18015550100")
        logged = _frappe().logged
        self.assertEqual(len(logged), 1, "at most one Error Log per request")
        args, kwargs = logged[0]
        self.assertEqual(args, (), "log_error takes keyword arguments")
        self.assertNotIn("secret@example.com", repr(kwargs))
        self.assertIn("RuntimeError", kwargs["message"])

    def test_a_broken_document_never_raises(self):
        class Broken(FakeDoc):
            def get_all_children(self):
                raise RuntimeError("no children for you")

        hook.normalize_doc(Broken("Contact", mobile_no="801-555-0100"))
        hook.normalize_doc(types.SimpleNamespace(doctype="Contact"))
        self.assertLessEqual(len(_frappe().logged), 1)

    def test_a_failing_log_error_is_swallowed_too(self):
        def failing_log(*a, **k):
            raise RuntimeError("log down")

        _frappe().log_error = failing_log
        try:
            hook._log_once("Contact", "mobile_no", ValueError("x"))
        finally:
            _frappe().log_error = lambda *a, **k: _frappe().logged.append((a, k))


class TestRegistryAgrees(unittest.TestCase):
    def test_every_child_table_has_its_parents(self):
        self.assertEqual(set(hook.CHILD_TABLE_PARENTS), set(hook.CHILD_TABLES))
        for child in hook.CHILD_TABLES:
            self.assertIn(child, hook.FIELDS, f"{child} is a registered child table with no fields")

    def test_parents_match_this_apps_table_fields(self):
        """Every Table field in this app's doctypes and Custom Field fixtures pointing at a
        registered child table must name a parent the hook watches."""
        found = {}
        for path in APP.rglob("doctype/*/*.json"):
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(meta, dict) or meta.get("doctype") != "DocType":
                continue
            for field in meta.get("fields") or []:
                if field.get("fieldtype") in ("Table", "Table MultiSelect") and field.get("options") in hook.CHILD_TABLES:
                    found.setdefault(field["options"], set()).add(meta["name"])
        custom = json.loads((APP / "fixtures" / "custom_field.json").read_text(encoding="utf-8"))
        for field in custom:
            if field.get("fieldtype") in ("Table", "Table MultiSelect") and field.get("options") in hook.CHILD_TABLES:
                found.setdefault(field["options"], set()).add(field["dt"])
        self.assertTrue(found, "expected this app's own registered child tables")
        for child, parents in found.items():
            with self.subTest(child=child):
                self.assertLessEqual(parents, set(hook.CHILD_TABLE_PARENTS[child]))


def _hooks_assignments():
    tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = node.value
    return out


class TestWiring(unittest.TestCase):
    def test_hooks_registers_it_under_star_before_validate(self):
        events = ast.literal_eval(_hooks_assignments()["doc_events"])
        self.assertEqual(
            events["*"]["before_validate"], "erpnext_enhancements.utils.normalize_hook.normalize_doc"
        )

    def test_hooks_registers_sf_phone_individually(self):
        jinja = ast.literal_eval(_hooks_assignments()["jinja"])
        self.assertIn("erpnext_enhancements.utils.value_display.sf_phone", jinja["methods"])
        self.assertNotIn("erpnext_enhancements.utils.value_display", jinja["methods"])

    def test_every_sf_global_a_notification_calls_is_registered(self):
        """Calling an undefined Jinja global raises inside Notification.send(), which logs it
        and sends nothing (CLAUDE.md)."""
        hooks_text = (APP / "hooks.py").read_text(encoding="utf-8")
        import re

        records = json.loads((APP / "fixtures" / "notification.json").read_text(encoding="utf-8"))
        called = set()
        for rec in records:
            called |= set(re.findall(r"\b(sf_\w+)\s*\(", rec.get("message") or ""))
        self.assertIn("sf_phone", called)
        for name in called:
            self.assertRegex(hooks_text, rf'"erpnext_enhancements\.utils\.\w+\.{name}"')

    def test_boot_ships_the_registry_only_when_switched_on(self):
        frappe = _frappe()
        frappe.conf = _Dict()
        registry = hook.desk_registry()
        self.assertEqual(set(registry), {"fields"})
        self.assertEqual(registry["fields"]["Contact"]["mobile_no"], "phone")
        self.assertNotIn("email_id", registry["fields"]["Contact"], "phones only")
        self.assertNotIn("Bank", registry["fields"], "a doctype with no phone is left out")
        frappe.conf = _Dict({"disable_value_normalization": 1})
        self.assertIsNone(hook.desk_registry())
        frappe.conf = _Dict()
        boot = (APP / "boot.py").read_text(encoding="utf-8")
        self.assertIn("normalize_registry = desk_registry()", boot)
        self.assertIn("\tif normalize_registry:\n\t\tbootinfo.sf_normalize = normalize_registry", boot)


class TestCallRoutingExtensionGuard(unittest.TestCase):
    def test_a_stored_extension_is_never_one_dialable_number(self):
        from erpnext_enhancements.ai_governance import call_routing_dial as dial
        from erpnext_enhancements.ai_governance import call_routing_match as match

        # The trap: the shared contract keeps every digit after a "+".
        self.assertEqual(match.to_e164("+18015550100,12"), "+1801555010012")
        self.assertEqual(dial.to_dialable("+18015550100,12"), "")
        self.assertEqual(dial.to_dialable("+18015550100"), "+18015550100")
        self.assertEqual(dial.to_dialable("801-555-0100"), "+18015550100")
        self.assertEqual(dial.to_dialable(None), "")

    def test_every_erpnext_side_dial_goes_through_the_guard(self):
        """Only the business-line comparison may call to_e164 directly."""
        gov = APP / "ai_governance"
        for path in (
            gov / "call_routing.py",
            gov / "doctype" / "call_routing_rule" / "call_routing_rule.py",
            gov / "doctype" / "call_routing_settings" / "call_routing_settings.py",
        ):
            lines = [
                line for line in path.read_text(encoding="utf-8").splitlines()
                if "match.to_e164(" in line and "primary_twilio_number" not in line
            ]
            with self.subTest(path=path.name):
                self.assertEqual(lines, [])


class TestDisplayFaces(_Reset):
    def test_sf_phone_and_the_stored_shape_helper(self):
        from erpnext_enhancements.utils import value_display as vd

        self.assertEqual(vd.sf_phone("+18015550100,123"), "(801) 555-0100 x123")
        self.assertEqual(vd.sf_phone("801-555-0100"), "(801) 555-0100")
        self.assertEqual(vd.sf_phone("65-688-000-88"), "65-688-000-88")
        self.assertEqual(vd.sf_phone(None), "")
        self.assertEqual(vd.shown_if_stored_phone("+18015550100"), "(801) 555-0100")
        # Only the exact stored shape: a typed number, a name or an amount is left as it is.
        for value in ("801-555-0100", "Nik Bradshaw", "+1 801 555 0100", 42, None):
            with self.subTest(value=value):
                self.assertEqual(vd.shown_if_stored_phone(value), value)

    def test_the_company_phone_on_customer_pages_is_shown_not_stored(self):
        frappe = _frappe()
        frappe.db = types.SimpleNamespace(get_value=lambda *a, **k: "+18018372199")
        sys.modules.pop(FOUNTAIN_MOVE, None)
        fountain_move = importlib.import_module(FOUNTAIN_MOVE)
        self.assertEqual(fountain_move.get_contact_phone(), "(801) 837-2199")
        frappe.db = types.SimpleNamespace(get_value=lambda *a, **k: None)
        self.assertEqual(fountain_move.get_contact_phone(), fountain_move.CONTACT_PHONE)

    def test_templates_print_registered_phones_through_sf_phone(self):
        for rel, needle in (
            ("package_dispatch/setup_print_formats.py", "sf_phone(doc.recipient_phone)"),
            ("templates/emails/crm_enhancements/fountain_move_duplicate.html", "sf_phone(req.phone)"),
            ("templates/emails/crm_enhancements/fountain_move_failed.html", "sf_phone(req.phone)"),
            ("fixtures/notification.json", 'sf_phone(doc.get(\\"mobile_no\\")'),
            ("water_engineering/packet.py", 'phone_display(s.get("engineer_phone"))'),
            ("water_engineering/packet.py", 'phone_display(s.get("contractor_phone"))'),
            ("asset_management/rental_logistics.py", "value = phone_display(value)"),
            ("asset_management/rental_portal.py", 'phone_display(values["site_contact_phone"])'),
            ("project_enhancements/customer_confirmations.py", "return phone_display(phone)"),
            ("project_enhancements/doctype/project_contract/project_contract.py", "shown_if_stored_phone(value)"),
        ):
            with self.subTest(file=rel):
                self.assertIn(needle, (APP / rel).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
