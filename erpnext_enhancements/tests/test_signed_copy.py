"""Bench-free tests for the "Signed copy" button's server side (``project_enhancements.signed_copy``).

Stubs a minimal ``frappe`` and a fake site (contracts, signature requests, files, who may read and
write) in ``setUpModule``, never at import, so the bench-only suites' ``import frappe`` guards are
not fooled. The same discipline as ``test_contract_esign``.

What a mistake here costs, and why each case exists:

* **Opening the wrong file as "the signed copy".** A drawing or an unsigned draft attached to the
  contract must never be offered as what the customer signed. Only the signature request's
  ``signed_document`` and files named by the signed-copy convention qualify. The name check must
  not let SF-MAINT-2026-0001 claim SF-MAINT-2026-00010's PDF.
* **A link that answers 403.** A file is offered only when frappe's own download check
  (``find_file_by_url``) would serve it. When it would not, the reader falls through to the
  executed copy rather than to an error page.
* **A paper contract with nothing kept.** SF-MAINT-2026-0001 was signed on paper with no scan
  attached. It must open its frozen executed copy, say no scan is on file, and offer Attach scan
  only to a Contract Manager.
* **Filing a scan onto the wrong record.** ``record_signed_scan`` refuses a file attached
  elsewhere, a non-PDF/picture, an unsigned or e-signed contract, and anyone who could not mark a
  contract signed.
* **A form that will not open.** ``signed_copy`` runs in two forms' onload, so a failed lookup is
  logged and answered, never raised.

Run: python -m unittest erpnext_enhancements.tests.test_signed_copy -v
"""

import ast
import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP_DIR = Path(__file__).resolve().parents[1]

signed_copy = None
project_contract = None
maintenance_contract = None

MANAGER = "System Manager", "Contract Manager"


class Site:
    """The fake site every stub reads. Reset before each test."""

    def reset(self):
        self.contracts = {}
        self.requests = []
        self.files = []
        self.downloadable = set()
        self.roles = {"Contract Manager"}
        self.perms = {"read", "write"}
        self.comments = []
        self.saved_files = []
        self.errors = []
        self.calls = []
        self.fail_get_all = False


SITE = Site()


class _Dict(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


def _install_frappe_stub():
    frappe = types.ModuleType("frappe")
    frappe._ = lambda s: s
    frappe._dict = _Dict

    def whitelist(*dargs, **dkwargs):
        def wrap(fn):
            return fn

        return wrap(dargs[0]) if dargs and callable(dargs[0]) else wrap

    frappe.whitelist = whitelist

    class ValidationError(Exception):
        pass

    class PermissionError(Exception):
        pass

    class DoesNotExistError(Exception):
        pass

    frappe.ValidationError = ValidationError
    frappe.PermissionError = PermissionError
    frappe.DoesNotExistError = DoesNotExistError

    def throw(msg, exc=ValidationError, title=None):
        raise exc(msg)

    frappe.throw = throw
    frappe.get_traceback = lambda *a, **k: "traceback"
    frappe.log_error = lambda message=None, title=None, **k: SITE.errors.append(title)
    frappe.get_roles = lambda user=None: list(SITE.roles)
    frappe.session = _Dict(user="manager@example.com")
    frappe.flags = _Dict()

    def get_value(doctype, filters=None, fieldname=None, as_dict=False, order_by=None, **kwargs):
        SITE.calls.append(("get_value", doctype, fieldname))
        if doctype == "Contract Signature Request":
            rows = [
                r
                for r in SITE.requests
                if r["project_contract"] == filters["project_contract"] and r["status"] == filters["status"]
            ]
            rows.sort(key=lambda r: r.get("signed_on") or "", reverse=True)
            if not rows:
                return None
            if isinstance(fieldname, (list, tuple)):
                picked = _Dict({f: rows[0].get(f) for f in fieldname})
                return picked if as_dict else tuple(picked.values())
            return rows[0].get(fieldname)
        if doctype == "Project Contract":
            row = SITE.contracts.get(filters)
            return row.get(fieldname) if row else None
        raise AssertionError(f"unexpected get_value on {doctype}")

    frappe.db = types.SimpleNamespace(get_value=get_value)

    def get_all(doctype, filters=None, fields=None, order_by=None, **kwargs):
        SITE.calls.append(("get_all", doctype))
        if SITE.fail_get_all:
            raise RuntimeError("database went away")
        assert doctype == "File", doctype
        rows = [
            r
            for r in SITE.files
            if r["attached_to_doctype"] == filters["attached_to_doctype"]
            and r["attached_to_name"] == filters["attached_to_name"]
            and not r.get("is_folder")
        ]
        rows.sort(key=lambda r: r["creation"], reverse=True)
        return [_Dict({f: r.get(f) for f in fields}) for r in rows]

    frappe.get_all = get_all
    frappe.has_permission = lambda doctype=None, ptype="read", doc=None, **k: ptype in SITE.perms

    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")

    class Document:
        def __init__(self, **fields):
            self.__dict__.update(fields)

        def get(self, key, default=None):
            return self.__dict__.get(key, default)

        def set_onload(self, key, value):
            self.__dict__.setdefault("__onload", {})[key] = value

        def get_onload(self, key):
            return self.__dict__.get("__onload", {}).get(key)

        def has_permission(self, ptype="read"):
            return ptype in SITE.perms

        def check_permission(self, ptype="read"):
            if ptype not in SITE.perms:
                raise PermissionError(ptype)

        def add_comment(self, comment_type, text=None):
            SITE.comments.append((self.get("name"), comment_type, text))

    document.Document = Document
    frappe.model = model
    model.document = document

    class File(Document):
        doctype = "File"

        def save(self, ignore_permissions=False):
            for row in SITE.files:
                if row["name"] == self.name:
                    row.update(file_name=self.file_name, is_private=self.is_private)
            SITE.saved_files.append((self.name, ignore_permissions))

    def get_doc(doctype, name=None):
        if doctype == "Project Contract":
            if name not in SITE.contracts:
                raise DoesNotExistError(name)
            return project_contract.ProjectContract(**SITE.contracts[name])
        if doctype == "File":
            row = next((r for r in SITE.files if r["name"] == name), None)
            if not row:
                raise DoesNotExistError(name)
            return File(**row)
        raise AssertionError(f"unexpected get_doc on {doctype}")

    frappe.get_doc = get_doc

    utils = types.ModuleType("frappe.utils")
    utils.escape_html = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    utils.cstr = lambda s: "" if s is None else str(s)
    utils.cint = lambda s: int(s or 0)
    utils.flt = lambda s, *a: float(s or 0)
    for name in ("today", "nowdate", "getdate", "add_days", "add_months", "add_years"):
        setattr(utils, name, lambda *a, **k: None)
    frappe.utils = utils

    file_utils = types.ModuleType("frappe.core.doctype.file.utils")

    def find_file_by_url(path, name=None):
        row = next((r for r in SITE.files if r["file_url"] == path), None)
        return File(**row) if row and path in SITE.downloadable else None

    file_utils.find_file_by_url = find_file_by_url

    modules = {
        "frappe": frappe,
        "frappe.utils": utils,
        "frappe.model": model,
        "frappe.model.document": document,
        "frappe.core": types.ModuleType("frappe.core"),
        "frappe.core.doctype": types.ModuleType("frappe.core.doctype"),
        "frappe.core.doctype.file": types.ModuleType("frappe.core.doctype.file"),
        "frappe.core.doctype.file.utils": file_utils,
    }
    sys.modules.update(modules)

    # The controller's own imports, which this suite has no use for: replaced rather than
    # imported, so the stub above does not have to grow to fit them.
    flags = types.ModuleType("erpnext_enhancements.feature_flags")
    flags.throw_if_process_automation_disabled = lambda *a, **k: None
    style = types.ModuleType("erpnext_enhancements.project_enhancements.contract_style")
    display = types.ModuleType("erpnext_enhancements.utils.value_display")
    display.shown_if_stored_phone = lambda value: value
    sys.modules["erpnext_enhancements.feature_flags"] = flags
    sys.modules["erpnext_enhancements.project_enhancements.contract_style"] = style
    sys.modules["erpnext_enhancements.utils.value_display"] = display


STUBBED = (
    "erpnext_enhancements.feature_flags",
    "erpnext_enhancements.project_enhancements.contract_style",
    "erpnext_enhancements.utils.value_display",
)


def setUpModule():
    global signed_copy, project_contract, maintenance_contract
    _install_frappe_stub()
    for mod in (
        "erpnext_enhancements.project_enhancements.signed_copy",
        "erpnext_enhancements.project_enhancements.doctype.project_contract.project_contract",
        "erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract."
        "sapphire_maintenance_contract",
    ):
        sys.modules.pop(mod, None)
    from erpnext_enhancements.project_enhancements import signed_copy as _signed
    from erpnext_enhancements.project_enhancements.doctype.project_contract import project_contract as _pc
    from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract import (
        sapphire_maintenance_contract as _smc,
    )

    signed_copy, project_contract, maintenance_contract = _signed, _pc, _smc


def tearDownModule():
    for mod in list(sys.modules):
        if mod == "frappe" or mod.startswith("frappe.") or mod in STUBBED:
            sys.modules.pop(mod, None)


# Shaped like production on 2026-10-26: one e-signed agreement with its PDF attached, one marked
# signed on paper with an executed copy frozen and no scan. Signer names are made up.
ESIGNED = "SF-MAINT-2026-0010"
PAPER = "SF-MAINT-2026-0001"
ESIGNED_PDF = f"/private/files/{ESIGNED}-signed.pdf"


def _contract(name, status="Signed", **fields):
    row = {
        "name": name,
        "doctype": "Project Contract",
        "status": status,
        "docstatus": 1,
        "template_key": "maintenance",
        "signed_by": "Pat Example",
        "signed_on": "2026-08-03",
        "executed_html": None,
    }
    row.update(fields)
    SITE.contracts[name] = row
    return row


def _file(name, file_name, contract, creation, file_url=None, downloadable=True, is_private=1):
    url = file_url or f"/private/files/{file_name}"
    SITE.files.append(
        {
            "name": name,
            "file_name": file_name,
            "file_url": url,
            "attached_to_doctype": "Project Contract",
            "attached_to_name": contract,
            "is_private": is_private,
            "creation": creation,
        }
    )
    if downloadable:
        SITE.downloadable.add(url)
    return url


def _esigned_site():
    _contract(ESIGNED, signed_by="Sam Example", signed_on="2026-10-26")
    SITE.requests.append(
        {
            "name": "CSR-0001",
            "project_contract": ESIGNED,
            "status": "Signed",
            "signed_on": "2026-10-26 09:00:00",
            "signed_document": ESIGNED_PDF,
            "agreement_html": "<p>executed online</p>",
        }
    )
    _file("f-esigned", f"{ESIGNED}-signed.pdf", ESIGNED, "2026-10-26 09:00:01")


def _paper_site(**fields):
    fields.setdefault("executed_html", "<p>frozen when marked signed</p>")
    _contract(PAPER, **fields)


class Base(unittest.TestCase):
    def setUp(self):
        SITE.reset()


class TestNaming(Base):
    def test_the_esign_pdf_and_a_filed_scan_are_signed_copies(self):
        self.assertTrue(signed_copy.is_signed_copy_name(f"{ESIGNED}-signed.pdf", ESIGNED))
        self.assertTrue(signed_copy.is_signed_copy_name(f"{PAPER}-signed-scan.jpg", PAPER))

    def test_case_is_ignored(self):
        self.assertTrue(signed_copy.is_signed_copy_name(f"{PAPER.lower()}-SIGNED-scan.PDF", PAPER))

    def test_a_longer_contract_number_is_not_this_contract(self):
        # SF-MAINT-2026-0001 must not claim SF-MAINT-2026-00010's signed PDF.
        self.assertFalse(signed_copy.is_signed_copy_name("SF-MAINT-2026-00010-signed.pdf", PAPER))

    def test_other_names_and_types_are_not(self):
        for file_name in ("drawing.pdf", f"{PAPER}.pdf", f"{PAPER}-signed.docx", "", None):
            self.assertFalse(signed_copy.is_signed_copy_name(file_name, PAPER), file_name)

    def test_a_slash_in_the_name_becomes_a_dash_as_the_esign_pdf_does(self):
        self.assertEqual(signed_copy.file_stem("SF/OC/1"), "SF-OC-1-signed")
        self.assertTrue(signed_copy.is_signed_copy_name("SF-OC-1-signed.pdf", "SF/OC/1"))


class TestPick(Base):
    def _row(self, file_name, url=None):
        return {"file_name": file_name, "file_url": url or f"/private/files/{file_name}"}

    def test_the_signature_request_pdf_wins(self):
        rows = [self._row(f"{ESIGNED}-signed-scan.pdf")]
        picked = signed_copy.pick_signed_file(ESIGNED_PDF, rows, ESIGNED, lambda url: True)
        self.assertEqual(picked["file_url"], ESIGNED_PDF)
        self.assertEqual(picked["source"], "signature_request")

    def test_an_undownloadable_request_pdf_falls_through_to_the_attachment(self):
        rows = [self._row(f"{ESIGNED}-signed.pdf", "/private/files/copy.pdf")]
        picked = signed_copy.pick_signed_file(
            ESIGNED_PDF, rows, ESIGNED, lambda url: url != ESIGNED_PDF
        )
        self.assertEqual(picked, {
            "file_url": "/private/files/copy.pdf",
            "file_name": f"{ESIGNED}-signed.pdf",
            "source": "attachment",
        })

    def test_an_unrelated_attachment_is_never_the_signed_copy(self):
        rows = [self._row("drawing.pdf"), self._row("draft for review.pdf")]
        self.assertIsNone(signed_copy.pick_signed_file(None, rows, PAPER, lambda url: True))

    def test_the_newest_signed_attachment_comes_first(self):
        rows = [self._row(f"{PAPER}-signed-scan.pdf", "/new.pdf"), self._row(f"{PAPER}-signed-scan.pdf", "/old.pdf")]
        picked = signed_copy.pick_signed_file(None, rows, PAPER, lambda url: True)
        self.assertEqual(picked["file_url"], "/new.pdf")

    def test_nothing_downloadable_is_nothing(self):
        rows = [self._row(f"{PAPER}-signed-scan.pdf")]
        self.assertIsNone(signed_copy.pick_signed_file(None, rows, PAPER, lambda url: False))
        self.assertIsNone(signed_copy.pick_signed_file(None, [], PAPER, lambda url: True))


class TestSignedCopy(Base):
    def _answer(self, name):
        return signed_copy.signed_copy(project_contract.ProjectContract(**SITE.contracts[name]))

    def test_an_unsigned_contract_reads_nothing(self):
        _contract("SF-MAINT-2026-0099", status="Out for Signature")
        info = self._answer("SF-MAINT-2026-0099")
        self.assertEqual(info["signed"], 0)
        self.assertIsNone(info["file_url"])
        self.assertEqual(SITE.calls, [])

    def test_esigned_opens_its_pdf(self):
        _esigned_site()
        info = self._answer(ESIGNED)
        self.assertEqual(info["route"], "esign")
        self.assertEqual(info["file_url"], ESIGNED_PDF)
        self.assertEqual(info["source"], "signature_request")
        self.assertEqual(info["can_attach"], 0)

    def test_esigned_pdf_the_reader_cannot_download_falls_back_to_the_executed_copy(self):
        _esigned_site()
        SITE.downloadable.clear()
        info = self._answer(ESIGNED)
        self.assertIsNone(info["file_url"])
        self.assertEqual(info["executed"], 1)
        # A scan never replaces an online signature, so no Attach scan even for a manager.
        self.assertEqual(info["can_attach"], 0)

    def test_esigned_request_that_lost_its_link_still_finds_the_attachment(self):
        _esigned_site()
        SITE.requests[0]["signed_document"] = None
        info = self._answer(ESIGNED)
        self.assertEqual(info["file_url"], ESIGNED_PDF)
        self.assertEqual(info["source"], "attachment")

    def test_paper_with_no_scan_reads_the_frozen_copy_and_offers_attach_to_a_manager(self):
        _paper_site()
        info = self._answer(PAPER)
        self.assertEqual(info["route"], "paper")
        self.assertIsNone(info["file_url"])
        self.assertEqual(info["executed"], 1)
        self.assertEqual(info["can_attach"], 1)
        self.assertEqual(info["signed_by"], "Pat Example")
        self.assertEqual(info["signed_on"], "2026-08-03")

    def test_attach_scan_is_only_for_those_who_can_mark_a_contract_signed(self):
        _paper_site()
        SITE.roles = {"Projects Manager", "Sales Manager"}
        self.assertEqual(self._answer(PAPER)["can_attach"], 0)
        SITE.roles = {"Contract Manager"}
        SITE.perms = {"read"}
        self.assertEqual(self._answer(PAPER)["can_attach"], 0)

    def test_paper_with_nothing_frozen_says_so(self):
        _paper_site(executed_html=None)
        info = self._answer(PAPER)
        self.assertEqual(info["executed"], 0)
        self.assertIsNone(info["file_url"])

    def test_paper_with_a_filed_scan_opens_it(self):
        _paper_site()
        url = _file("f-scan", f"{PAPER}-signed-scan.pdf", PAPER, "2026-10-26 10:00:00")
        info = self._answer(PAPER)
        self.assertEqual(info["file_url"], url)
        self.assertEqual(info["source"], "attachment")

    def test_unfiled_attachments_are_offered_to_file_pdfs_and_pictures_only(self):
        _paper_site()
        _file("f-pdf", "Signed agreement.pdf", PAPER, "2026-10-26 10:00:00")
        _file("f-jpg", "IMG_0042.JPG", PAPER, "2026-10-26 10:01:00")
        _file("f-doc", "notes.docx", PAPER, "2026-10-26 10:02:00")
        _file("f-hidden", "other.pdf", PAPER, "2026-10-26 10:03:00", downloadable=False)
        info = self._answer(PAPER)
        self.assertIsNone(info["file_url"])
        self.assertEqual([a["name"] for a in info["attachments"]], ["f-jpg", "f-pdf"])

    def test_a_reader_who_cannot_file_is_not_shown_the_attachments(self):
        _paper_site()
        _file("f-pdf", "Signed agreement.pdf", PAPER, "2026-10-26 10:00:00")
        SITE.roles = {"Projects Manager"}
        self.assertEqual(self._answer(PAPER)["attachments"], [])

    def test_a_failed_lookup_is_logged_never_raised(self):
        _paper_site()
        SITE.fail_get_all = True
        info = self._answer(PAPER)
        self.assertEqual(info["signed"], 1)
        self.assertIsNone(info["file_url"])
        self.assertEqual(SITE.errors, ["Project Contract: signed copy lookup failed"])


class TestEndpoints(Base):
    def test_get_signed_copy_needs_read(self):
        _esigned_site()
        self.assertEqual(signed_copy.get_signed_copy(ESIGNED)["file_url"], ESIGNED_PDF)
        SITE.perms = set()
        with self.assertRaises(sys.modules["frappe"].PermissionError):
            signed_copy.get_signed_copy(ESIGNED)

    def test_signed_copy_for_answers_only_a_signed_readable_contract(self):
        self.assertIsNone(signed_copy.signed_copy_for(None))
        _contract("SF-MAINT-2026-0099", status="Out for Signature")
        self.assertIsNone(signed_copy.signed_copy_for("SF-MAINT-2026-0099"))
        _esigned_site()
        self.assertEqual(signed_copy.signed_copy_for(ESIGNED)["file_url"], ESIGNED_PDF)
        SITE.perms = set()
        self.assertIsNone(signed_copy.signed_copy_for(ESIGNED))


class TestRecordSignedScan(Base):
    def setUp(self):
        super().setUp()
        _paper_site()
        _file("f-up", "IMG_0042.JPG", PAPER, "2026-10-26 10:00:00", is_private=0)

    def test_files_renames_makes_private_and_leaves_a_comment(self):
        out = signed_copy.record_signed_scan(PAPER, "f-up")
        self.assertEqual(out["file_name"], f"{PAPER}-signed-scan.jpg")
        row = SITE.files[0]
        self.assertEqual(row["file_name"], f"{PAPER}-signed-scan.jpg")
        self.assertEqual(row["is_private"], 1)
        self.assertEqual(SITE.saved_files, [("f-up", True)])
        self.assertEqual(len(SITE.comments), 1)
        self.assertIn(f"{PAPER}-signed-scan.jpg", SITE.comments[0][2])
        self.assertIn("manager@example.com", SITE.comments[0][2])
        # And Signed copy now opens it.
        info = signed_copy.signed_copy(project_contract.ProjectContract(**SITE.contracts[PAPER]))
        self.assertEqual(info["file_url"], row["file_url"])

    def test_refused_to_anyone_who_cannot_mark_a_contract_signed(self):
        SITE.roles = {"Projects Manager"}
        with self.assertRaises(sys.modules["frappe"].PermissionError):
            signed_copy.record_signed_scan(PAPER, "f-up")
        SITE.roles = {"Contract Manager"}
        SITE.perms = {"read"}
        with self.assertRaises(sys.modules["frappe"].PermissionError):
            signed_copy.record_signed_scan(PAPER, "f-up")
        self.assertEqual(SITE.saved_files, [])

    def test_refused_for_an_unsigned_or_esigned_contract(self):
        SITE.contracts[PAPER]["status"] = "Out for Signature"
        with self.assertRaises(sys.modules["frappe"].ValidationError):
            signed_copy.record_signed_scan(PAPER, "f-up")
        SITE.contracts[PAPER]["status"] = "Signed"
        SITE.requests.append({"project_contract": PAPER, "status": "Signed", "signed_on": "x", "name": "CSR-9"})
        with self.assertRaises(sys.modules["frappe"].ValidationError):
            signed_copy.record_signed_scan(PAPER, "f-up")
        self.assertEqual(SITE.saved_files, [])

    def test_refused_for_a_file_on_another_record_or_of_another_type(self):
        _file("f-elsewhere", "scan.pdf", "SF-MAINT-2026-0002", "2026-10-26 10:00:00")
        _file("f-doc", "notes.docx", PAPER, "2026-10-26 10:00:00")
        for file in ("f-elsewhere", "f-doc"):
            with self.assertRaises(sys.modules["frappe"].ValidationError, msg=file):
                signed_copy.record_signed_scan(PAPER, file)
        self.assertEqual(SITE.saved_files, [])


class TestOnload(Base):
    def test_project_contract_sends_the_answer_only_when_signed(self):
        _esigned_site()
        doc = project_contract.ProjectContract(**SITE.contracts[ESIGNED])
        doc.onload()
        self.assertEqual(doc.get_onload("signed_copy")["file_url"], ESIGNED_PDF)

        _contract("SF-MAINT-2026-0099", status="Out for Signature")
        draft = project_contract.ProjectContract(**SITE.contracts["SF-MAINT-2026-0099"])
        draft.onload()
        self.assertIsNone(draft.get_onload("signed_copy"))

    def test_maintenance_contract_sends_its_agreements_answer(self):
        _esigned_site()
        smc = maintenance_contract.SapphireMaintenanceContract(name="MNT-1", project_contract=ESIGNED)
        smc.onload()
        self.assertEqual(smc.get_onload("signed_copy")["file_url"], ESIGNED_PDF)

    def test_maintenance_contract_sends_nothing_without_a_readable_signed_agreement(self):
        smc = maintenance_contract.SapphireMaintenanceContract(name="MNT-1", project_contract=None)
        smc.onload()
        self.assertIsNone(smc.get_onload("signed_copy"))
        _esigned_site()
        SITE.perms = set()
        smc = maintenance_contract.SapphireMaintenanceContract(name="MNT-1", project_contract=ESIGNED)
        smc.onload()
        self.assertIsNone(smc.get_onload("signed_copy"))


class TestWiring(Base):
    """The Desk side, read as text: a button that calls a method nobody exports does nothing."""

    def _read(self, *parts):
        return APP_DIR.joinpath(*parts).read_text(encoding="utf-8")

    def test_viewer_exports_the_opener_and_calls_the_real_endpoints(self):
        js = self._read("public", "js", "project_enhancements", "contract_viewer.js")
        for name in ("open_signed_copy", "fetch_signed_copy", "attach_scan"):
            self.assertRegex(js, rf"Object\.assign\(erpnext_enhancements\.contract_viewer, \{{[^}}]*\b{name}\b")
        self.assertIn('"erpnext_enhancements.project_enhancements.signed_copy"', js)
        for method in ("get_signed_copy", "record_signed_scan"):
            self.assertIn(f"${{SIGNED}}.{method}", js)
            self.assertTrue(callable(getattr(signed_copy, method)))

    def test_both_forms_open_their_onload_answer(self):
        for parts in (
            ("project_enhancements", "doctype", "project_contract", "project_contract.js"),
            (
                "sapphire_maintenance",
                "doctype",
                "sapphire_maintenance_contract",
                "sapphire_maintenance_contract.js",
            ),
        ):
            js = self._read(*parts)
            self.assertIn("__onload.signed_copy", js, parts[-1])
            self.assertIn("contract_viewer.open_signed_copy(signed_copy", js, parts[-1])

    def test_the_list_button_and_the_viewer_it_needs_both_load(self):
        js = self._read("project_enhancements", "doctype", "project_contract", "project_contract_list.js")
        self.assertIn('frappe.listview_settings["Project Contract"]', js)
        self.assertIn('doc.status === "Signed"', js)
        self.assertIn("fetch_signed_copy(doc.name)", js)

        tree = ast.parse(self._read("hooks.py"))
        list_js = next(
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(getattr(t, "id", None) == "doctype_list_js" for t in node.targets)
        )
        entry = list_js["Project Contract"]
        entries = entry if isinstance(entry, list) else [entry]
        self.assertIn("public/js/project_enhancements/contract_viewer.js", entries)
        # frappe loads the doctype's own *_list.js unasked; listing it as well loads it twice.
        self.assertNotIn("project_enhancements/doctype/project_contract/project_contract_list.js", entries)

    def test_the_esign_pdf_name_is_the_convention_this_looks_for(self):
        lifecycle = self._read("project_enhancements", "esign", "lifecycle.py")
        self.assertIn('"file_name": f"{safe_contract}-signed.pdf"', lifecycle)
        self.assertIn('.replace("/", "-")', lifecycle)

    def test_project_contract_json_still_has_the_fields_read_here(self):
        meta = json.loads(self._read("project_enhancements", "doctype", "project_contract", "project_contract.json"))
        fields = {f["fieldname"] for f in meta["fields"]}
        self.assertLessEqual({"status", "signed_by", "signed_on", "executed_html", "template_key"}, fields)
        csr = json.loads(
            self._read(
                "project_enhancements",
                "doctype",
                "contract_signature_request",
                "contract_signature_request.json",
            )
        )
        self.assertIn("signed_document", {f["fieldname"] for f in csr["fields"]})


if __name__ == "__main__":
    unittest.main()
