"""Bench-free tests for the one-year Certificate of Warranty in the Contract Builder (v1.808.0).

Nikolas, 2026-11-12: "I need this added to the Contract Builder", attaching Sapphire's Word
certificate. It is a ninth Contract Template (``warranty``) with three rules of its own, and this
suite pins the ones that would otherwise fail without anyone seeing:

* **the wording** is the Word document's, paragraph for paragraph, and the certificate carries no
  ``sig(`` (a template with one can be sent to a customer to sign) and no example project;
* **the merge fields**: every blank prints the field it belongs to, an empty one prints a line,
  a typed value is escaped, and every ``doc.<field>`` the certificate prints has an input the
  Builder shows for this type;
* **the date rule**: the warranty ends one year after the work was completed, worked out and
  never typed, and the certificate does not print it;
* **only Sapphire signs**: issuing with a signer named is the signature (status Signed, the copy
  frozen), with none it waits for ink; the By line shows the name only once issued; nothing can
  send it to a customer for a signature;
* **email is one explicit call**: Contract Manager only, a signed certificate only, one message
  with the PDF, and nothing else in the feature sends mail;
* **one page, always**: a long project name or address picks a tighter layout step and is never
  cut, and where a Chrome is installed (CI's runner has one) the long cases are printed and the
  pages counted;
* **the date beside the signature** is the day it is issued, never a typed one, when it is signed
  by issuing; and a certificate starts from a Project or a client, enforced on the server;
* the seed and the patch (insert-only, one key, never raises, says what it did), the letterhead's
  eyebrow and footer, the print CSS, and the wiring.

Stubs a minimal ``frappe`` in ``setUpModule`` (never at import), like ``test_signed_copy`` and
``test_general_agreement_layout``. The render cases need ``jinja2``, which CI installs. Every name,
address and project below is made up.

Run: python -m unittest erpnext_enhancements.tests.test_warranty_certificate
"""

import contextlib
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
TEMPLATE = APP / "templates" / "contracts" / "certificate_of_warranty.html"
DOCTYPES = APP / "project_enhancements" / "doctype"
PC_JSON = DOCTYPES / "project_contract" / "project_contract.json"
PC_PY = DOCTYPES / "project_contract" / "project_contract.py"
PRINT_FORMATS = APP / "fixtures" / "print_format.json"

try:
    import jinja2
    import jinja2.sandbox
except ImportError:  # pragma: no cover - CI installs it
    jinja2 = None

#: The Word document's four paragraphs, as typed there (its double spaces after a full stop kept).
WORDING = [
    "The undersigned, Sapphire Fountains, LLC (Sapphire), warrants that workmanship, materials and "
    "equipment furnished under the Contract will be of good quality and new unless otherwise required or "
    "permitted by the Contract Documents, that the Work will be free from defects not inherent in the "
    "quality required or permitted, and that the Work will conform to the requirements of the Contract "
    "Documents.  The Warranty refers to the above-referenced project.  Work not conforming to these "
    "requirements, including substitutions not properly approved and authorized, may be considered "
    "non-conforming.  The warranty is for a period of one (1) year from the date of execution of the "
    "Certificate of Substantial Completion or Occupancy, as required by the Contract Documents for the "
    "project.   Specific manufacturer’s warranties may extend beyond that one-year period from initial "
    "installation, repair, or replacement date.",
    "Repair or replacement of work by Sapphire during the warranty period will only carry an extended "
    "warranty beyond the initial one year from the date of repair or replacement if it is provided by the "
    "manufacturer and/or Sapphire.",
    "Sapphire’s warranty excludes remedy for damage or defect caused by abuse, modifications not executed "
    "by Sapphire, improper or insufficient maintenance, improper operation, or normal wear and tear and "
    "normal usage.",
    "Owner or General Contractor should notify Sapphire in writing of any warranty issues and "
    "Subcontractor/Supplier shall have five (5) working days to initiate a remedy.",
]

CERT = "SF-WAR-2026-0001"
#: The day the stubbed site thinks it is. Deliberately not the date typed on the test certificates.
TODAY = "2026-11-20"

warranty = None
project_contract = None
builder = None
contract_style = None
seed = None
patch = None


class _DF(dict):
    def __getattr__(self, key):
        return self.get(key)


class _Dict(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class Site:
    """The fake site every stub reads. Reset before each test."""

    def reset(self):
        self.templates = {}
        self.contracts = {}
        self.projects = {}
        self.addresses = {}
        self.files = []
        self.downloadable = set()
        self.settings = {}
        self.roles = {"Contract Manager"}
        self.perms = {"read", "write", "create", "submit", "email"}
        self.esign = True
        self.sent = []
        self.prints = []
        self.comments = []
        self.errors = []
        self.inserted = []
        self.fail_insert = False
        self.fail_address = False


SITE = Site()


def _body():
    return TEMPLATE.read_text(encoding="utf-8")


def _meta(path):
    return types.SimpleNamespace(fields=[_DF(f) for f in json.loads(path.read_text(encoding="utf-8"))["fields"]])


def _formatdate(value, fmt=None):
    day = value if isinstance(value, datetime.date) else datetime.date.fromisoformat(str(value)[:10])
    if fmt == "MMMM d, yyyy":
        return f"{day.strftime('%B')} {day.day}, {day.year}"
    return day.isoformat()


def _install_frappe_stub():
    frappe = types.ModuleType("frappe")
    frappe._ = lambda s, *a, **k: s
    frappe._dict = _Dict
    frappe.session = types.SimpleNamespace(user="manager@example.test")
    frappe.flags = _Dict()
    frappe.local = types.SimpleNamespace(message_log=[])

    def whitelist(*dargs, **dkwargs):
        def wrap(fn):
            return fn

        return wrap(dargs[0]) if dargs and callable(dargs[0]) else wrap

    frappe.whitelist = whitelist

    class ValidationError(Exception):
        pass

    class PermissionError(Exception):
        pass

    frappe.ValidationError = ValidationError
    frappe.PermissionError = PermissionError

    def throw(msg, exc=ValidationError, title=None):
        raise exc(msg)

    frappe.throw = throw
    frappe.log_error = lambda *a, **k: SITE.errors.append((a, k))
    frappe.get_traceback = lambda: "traceback"
    frappe.get_app_path = lambda app, *parts: str(APP.joinpath(*parts))
    frappe.get_roles = lambda *a: list(SITE.roles)
    frappe.has_permission = lambda doctype=None, ptype="read", doc=None, **k: ptype in SITE.perms

    def only_for(roles, message=False):
        if not set(roles) & SITE.roles:
            raise PermissionError("only_for")

    frappe.only_for = only_for
    frappe.sendmail = lambda **kwargs: SITE.sent.append(kwargs)

    def get_print(doctype=None, name=None, print_format=None, as_pdf=False, **kwargs):
        # What the print format prints: the executed copy, i.e. the one frozen at issue.
        doc = project_contract.ProjectContract(**SITE.contracts[name])
        SITE.prints.append((doctype, name, print_format, as_pdf))
        return b"%PDF-stub " + doc.executed_body().encode("utf-8")

    frappe.get_print = get_print

    metas = {"Project Contract": _meta(PC_JSON)}
    for child in (
        "Contract Service Option",
        "Contract Phase Item",
        "Contract Milestone",
        "Contract Equipment Item",
        "Contract Rate Line",
    ):
        slug = child.lower().replace(" ", "_")
        metas[child] = _meta(DOCTYPES / slug / f"{slug}.json")
    frappe.get_meta = lambda doctype: metas[doctype]

    def render_template(body, context):
        env = jinja2.sandbox.SandboxedEnvironment(undefined=jinja2.StrictUndefined)
        return env.from_string(body).render(**context)

    frappe.render_template = render_template

    # ------------------------------------------------------------------ db
    def _pick(row, fieldname, as_dict):
        if row is None:
            return None
        if isinstance(fieldname, (list, tuple)):
            return _Dict({f: row.get(f) for f in fieldname}) if as_dict else tuple(row.get(f) for f in fieldname)
        return row.get(fieldname)

    def get_value(doctype, filters=None, fieldname="name", as_dict=False, order_by=None, **kwargs):
        if doctype == "Contract Template":
            return _pick(SITE.templates.get(filters), fieldname, as_dict)
        if doctype == "Project Contract":
            return _pick(SITE.contracts.get(filters), fieldname, as_dict)
        if doctype == "Address":
            if isinstance(filters, dict):
                # The party's billing address, by link: this suite has none on file.
                return None
            if SITE.fail_address:
                raise RuntimeError("address lookup failed")
            return _pick(SITE.addresses.get(filters), fieldname, as_dict)
        # No Contract Signature Request, no Sapphire Maintenance Contract: nothing e-signed here.
        return None

    def exists(doctype, name=None):
        if doctype == "Contract Template":
            return name in SITE.templates
        return False

    frappe.db = types.SimpleNamespace(
        get_value=get_value,
        exists=exists,
        get_single_value=lambda doctype, field: SITE.settings.get(field),
        set_value=lambda *a, **k: None,
        commit=lambda: None,
        rollback=lambda: None,
    )

    def get_all(doctype, filters=None, fields=None, **kwargs):
        if doctype == "File":
            return [_Dict(row) for row in SITE.files if row.get("attached_to_name") == (filters or {}).get("attached_to_name")]
        return []

    frappe.get_all = get_all
    frappe.get_list = lambda *a, **k: []
    frappe.defaults = types.SimpleNamespace(get_global_default=lambda key: "USD")

    # ------------------------------------------------------------------ documents
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")

    class Document:
        doctype = "Project Contract"

        def __init__(self, **fields):
            self.__dict__.update(fields)
            self.__dict__.setdefault("flags", _Dict())

        def __getattr__(self, key):
            # A real new_doc() carries every field as None; an unset one never raises here.
            if key.startswith("__"):
                raise AttributeError(key)
            return None

        def get(self, key, default=None):
            value = self.__dict__.get(key, default)
            return default if value is None else value

        def set(self, key, value):
            self.__dict__[key] = value

        def as_dict(self, **kwargs):
            return {k: v for k, v in self.__dict__.items() if k != "flags"}

        def db_set(self, fieldname, value=None, update_modified=True):
            values = fieldname if isinstance(fieldname, dict) else {fieldname: value}
            self.__dict__.update(values)
            if self.get("name") in SITE.contracts:
                SITE.contracts[self.get("name")].update(values)

        def has_permission(self, ptype="read"):
            return ptype in SITE.perms

        def check_permission(self, ptype="read"):
            if ptype not in SITE.perms:
                raise PermissionError(ptype)

        def add_comment(self, comment_type, text=None):
            SITE.comments.append((self.get("name"), comment_type, text))

        def get_doc_before_save(self):
            return None

        def insert(self, ignore_permissions=False):
            if SITE.fail_insert:
                raise RuntimeError("insert failed")
            if self.get("doctype_name") == "Contract Template":
                SITE.templates[self.template_key] = {
                    k: v for k, v in self.__dict__.items() if k not in ("flags", "doctype_name")
                }
                SITE.inserted.append(self.template_key)
            return self

    document.Document = Document
    frappe.model = model
    model.document = document

    def new_doc(doctype):
        if doctype == "Project Contract":
            return project_contract.ProjectContract()
        return Document(doctype_name=doctype)

    frappe.new_doc = new_doc

    def get_doc(doctype, name=None):
        if doctype == "Project Contract":
            return project_contract.ProjectContract(**SITE.contracts[name])
        if doctype == "Project":
            return Document(**SITE.projects[name])
        if doctype == "Contract Template":
            return Document(**SITE.templates[name])
        raise AssertionError(f"unexpected get_doc on {doctype}")

    frappe.get_doc = get_doc

    # ------------------------------------------------------------------ utils
    utils = types.ModuleType("frappe.utils")
    utils.escape_html = lambda s: (
        str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
    utils.cstr = lambda s: "" if s is None else str(s)

    def flt(value, precision=None):
        try:
            number = float(value or 0)
        except (TypeError, ValueError):
            number = 0.0
        return round(number, precision) if precision is not None else number

    utils.flt = flt
    utils.cint = lambda value: int(float(value or 0))
    utils.fmt_money = lambda value, currency=None: f"${flt(value):,.2f}"
    utils.formatdate = _formatdate
    utils.today = lambda: TODAY
    utils.get_fullname = lambda user=None: "Morgan Manager"
    for name in ("add_days", "add_months", "add_years", "getdate", "nowdate"):
        setattr(utils, name, lambda *a, **k: None)
    frappe.utils = utils

    limiter = types.ModuleType("frappe.rate_limiter")
    limiter.rate_limit = lambda *a, **k: (lambda fn: fn)
    frappe.rate_limiter = limiter

    file_manager = types.ModuleType("frappe.utils.file_manager")
    file_manager.get_file = lambda url: (url.rsplit("/", 1)[-1], b"SCAN " + url.encode("utf-8"))
    file_utils = types.ModuleType("frappe.core.doctype.file.utils")
    file_utils.find_file_by_url = lambda path, name=None: path if path in SITE.downloadable else None

    sys.modules.update(
        {
            "frappe": frappe,
            "frappe.utils": utils,
            "frappe.utils.file_manager": file_manager,
            "frappe.rate_limiter": limiter,
            "frappe.model": model,
            "frappe.model.document": document,
            "frappe.core": types.ModuleType("frappe.core"),
            "frappe.core.doctype": types.ModuleType("frappe.core.doctype"),
            "frappe.core.doctype.file": types.ModuleType("frappe.core.doctype.file"),
            "frappe.core.doctype.file.utils": file_utils,
        }
    )

    # Imports the code under test makes that this suite has no use for: replaced rather than
    # imported, so the stub above does not have to grow to fit them.
    flags = types.ModuleType("erpnext_enhancements.feature_flags")
    flags.throw_if_process_automation_disabled = lambda *a, **k: None
    flags.throw_if_contract_esign_disabled = lambda *a, **k: None
    flags.contract_esign_enabled = lambda: SITE.esign
    flags.contract_esign_public_page_enabled = lambda: SITE.esign
    display = types.ModuleType("erpnext_enhancements.utils.value_display")
    display.shown_if_stored_phone = lambda value: value
    esign_api = types.ModuleType("erpnext_enhancements.project_enhancements.esign.api")

    def _valid_single_email(value):
        value = (value or "").strip()
        if "@" not in value or "," in value or " " in value:
            raise ValidationError("email")
        return value

    esign_api._valid_single_email = _valid_single_email
    sys.modules["erpnext_enhancements.feature_flags"] = flags
    sys.modules["erpnext_enhancements.utils.value_display"] = display
    sys.modules["erpnext_enhancements.project_enhancements.esign.api"] = esign_api


STUBBED = (
    "erpnext_enhancements.feature_flags",
    "erpnext_enhancements.utils.value_display",
    "erpnext_enhancements.project_enhancements.esign.api",
)

RELOADED = (
    "erpnext_enhancements.project_enhancements",
    "erpnext_enhancements.patches.seed_contract_templates",
    "erpnext_enhancements.patches.seed_warranty_certificate_template",
    "erpnext_enhancements.email_style",
    "erpnext_enhancements.print_style",
)


def _forget():
    for mod in list(sys.modules):
        if mod.startswith(RELOADED) or mod in STUBBED:
            sys.modules.pop(mod, None)


def setUpModule():
    global warranty, project_contract, builder, contract_style, seed, patch
    _forget()
    _install_frappe_stub()
    from erpnext_enhancements.patches import seed_contract_templates as _seed
    from erpnext_enhancements.patches import seed_warranty_certificate_template as _patch
    from erpnext_enhancements.project_enhancements import contract_builder as _builder
    from erpnext_enhancements.project_enhancements import contract_style as _style
    from erpnext_enhancements.project_enhancements import warranty_certificate as _warranty
    from erpnext_enhancements.project_enhancements.doctype.project_contract import project_contract as _pc

    warranty, project_contract, builder, contract_style, seed, patch = _warranty, _pc, _builder, _style, _seed, _patch


def tearDownModule():
    _forget()
    for mod in list(sys.modules):
        if mod == "frappe" or mod.startswith("frappe."):
            sys.modules.pop(mod, None)


def _contract(**fields):
    """A Certificate of Warranty row on the fake site, and its document."""
    row = {
        "name": CERT,
        "contract_template": "warranty",
        "template_key": "warranty",
        "docstatus": 0,
        "status": "Draft",
        "contract_date": "2026-11-12",
        "project": "PRJ-00999",
        "project_title": "Example Plaza Water Wall",
        "site_address": "12 Example Way\nSpringfield, UT 84000",
        "warranty_start_date": "2026-10-30",
        "warranty_signer": "Jordan Example",
        "warranty_signer_title": "Operations Manager",
        "contact_person": "Riley Sample",
        "contact_email": "riley@example.test",
    }
    row.update(fields)
    SITE.contracts[row["name"]] = row
    return project_contract.ProjectContract(**row)


def _issue(**fields):
    """Issue a certificate the way a submit does: validate's own rules, then ``on_submit``."""
    doc = _contract(docstatus=1, **fields)
    doc._compute_warranty_term()
    doc._date_certificate_signed_here()
    SITE.contracts[doc.name].update(contract_date=doc.contract_date, warranty_end_date=doc.warranty_end_date)
    doc.on_submit()
    return doc


def _site_with_template():
    SITE.reset()
    SITE.templates["warranty"] = {
        "name": "warranty",
        "template_key": "warranty",
        "title": "Certificate of Warranty (One Year)",
        "party_type": "Customer",
        "requires_msa": 0,
        "enabled": 1,
        "description": "",
        "body": _body(),
    }


def _text(html):
    """The words of a fragment: tags out, entities back, whitespace as typed."""
    text = re.sub(r"<[^>]+>", "", html)
    return text.replace("&nbsp;", " ").replace("&amp;", "&")


# --------------------------------------------------------------------------- the date rule


class TestDateRule(unittest.TestCase):
    def test_one_year_on_is_the_same_day_next_year(self):
        self.assertEqual(warranty.warranty_end_date("2026-10-30"), datetime.date(2027, 10, 30))
        self.assertEqual(warranty.warranty_end_date("2026-01-01"), datetime.date(2027, 1, 1))
        self.assertEqual(warranty.warranty_end_date("2026-12-31"), datetime.date(2027, 12, 31))

    def test_february_29_ends_on_february_28(self):
        self.assertEqual(warranty.warranty_end_date("2028-02-29"), datetime.date(2029, 2, 28))
        # The day before a leap day is untouched, and a year that lands on one keeps its own day.
        self.assertEqual(warranty.warranty_end_date("2027-02-28"), datetime.date(2028, 2, 28))

    def test_dates_datetimes_and_text_all_read(self):
        self.assertEqual(warranty.warranty_end_date(datetime.date(2026, 3, 15)), datetime.date(2027, 3, 15))
        self.assertEqual(
            warranty.warranty_end_date(datetime.datetime(2026, 3, 15, 16, 45)), datetime.date(2027, 3, 15)
        )
        self.assertEqual(warranty.warranty_end_date("2026-03-15 16:45:00"), datetime.date(2027, 3, 15))

    def test_no_start_means_no_end(self):
        for nothing in (None, "", "   ", "not a date", 0):
            self.assertIsNone(warranty.warranty_end_date(nothing), nothing)

    def test_the_term_is_one_year(self):
        self.assertEqual(warranty.TERM_YEARS, 1)
        self.assertIn("one (1) year", _body())

    def test_the_controller_works_the_end_out_on_every_save(self):
        doc = project_contract.ProjectContract(template_key="warranty", warranty_start_date="2026-10-30")
        doc._compute_warranty_term()
        self.assertEqual(doc.warranty_end_date, datetime.date(2027, 10, 30))
        # A changed start moves it, a typed end is overwritten, a cleared start clears it.
        doc.warranty_start_date = "2026-11-02"
        doc.warranty_end_date = "2031-01-01"
        doc._compute_warranty_term()
        self.assertEqual(doc.warranty_end_date, datetime.date(2027, 11, 2))
        doc.warranty_start_date = None
        doc._compute_warranty_term()
        self.assertIsNone(doc.warranty_end_date)

    def test_other_contract_types_are_left_alone(self):
        doc = project_contract.ProjectContract(
            template_key="maintenance", warranty_start_date="2026-10-30", warranty_end_date="kept"
        )
        doc._compute_warranty_term()
        self.assertEqual(doc.warranty_end_date, "kept")

    def test_the_start_dates_label_and_help_say_what_the_certificate_says(self):
        fields = {f["fieldname"]: f for f in json.loads(PC_JSON.read_text(encoding="utf-8"))["fields"]}
        start = fields["warranty_start_date"]
        body = _body()
        # The label is the certificate's own line, so the person sees which blank it fills.
        self.assertEqual(start["label"], "Work Completed or Material Supplied by Sapphire On")
        self.assertIn("Work Completed or Material Supplied by Sapphire on:", body)
        # The help quotes the sentence the term is counted by, word for word from the certificate.
        quoted = re.search(r'"([^"]+)"', start["description"]).group(1)
        self.assertEqual(
            quoted,
            "from the date of execution of the Certificate of Substantial Completion or Occupancy, "
            "as required by the Contract Documents",
        )
        self.assertIn(quoted, " ".join(_text(body).split()))
        self.assertIn("enter its date", start["description"])
        self.assertIn("When it has none", start["description"])
        # The end date says it is a record, not the term.
        self.assertIn("certificate's own wording decides the term", fields["warranty_end_date"]["description"])
        self.assertIn("Certificate of Substantial Completion or Occupancy", builder.STEP_COPY["warranty_section"][1])

    def test_validate_runs_it_and_the_end_is_never_typed(self):
        source = PC_PY.read_text(encoding="utf-8")
        validate = source[source.index("\tdef validate(self):") : source.index("\tdef _fetch_template_props(self):")]
        self.assertIn("self._compute_warranty_term()", validate)
        fields = {f["fieldname"]: f for f in json.loads(PC_JSON.read_text(encoding="utf-8"))["fields"]}
        self.assertEqual(fields["warranty_end_date"]["read_only"], 1)
        self.assertEqual(fields["warranty_start_date"]["fieldtype"], "Date")
        # The Word certificate has no end date on it, so the certificate does not print one.
        self.assertNotIn("warranty_end_date", _body())


# --------------------------------------------------------------------------- the address rule


class TestSiteAddress(unittest.TestCase):
    def test_a_one_line_address_breaks_before_city_state_zip(self):
        self.assertEqual(
            warranty.site_address_lines("12 Example Way, Springfield, UT 84000"),
            "12 Example Way\nSpringfield, UT 84000",
        )
        self.assertEqual(
            warranty.site_address_lines("12 Example Way, Suite 4, Springfield, Utah 84000-1234"),
            "12 Example Way, Suite 4\nSpringfield, Utah 84000-1234",
        )

    def test_anything_it_cannot_read_is_left_as_it_is(self):
        for value in (
            "12 Example Way\nSpringfield, UT 84000",  # already on two lines
            "12 Example Way, Springfield",  # no state and ZIP
            "Springfield, UT 84000",  # no street to split from
            "North lot behind the library",
        ):
            self.assertEqual(warranty.site_address_lines(value), value)
        self.assertEqual(warranty.site_address_lines(None), "")
        self.assertEqual(warranty.site_address_lines("  "), "")

    def test_an_address_record_becomes_two_lines(self):
        record = {"address_line1": "12 Example Way", "address_line2": "Suite 4", "city": "Springfield", "state": "UT", "pincode": "84000"}
        self.assertEqual(warranty.address_from_record(record), "12 Example Way, Suite 4\nSpringfield, UT 84000")
        self.assertEqual(warranty.address_from_record({"address_line1": "12 Example Way"}), "12 Example Way")
        self.assertEqual(warranty.address_from_record({"city": "Springfield"}), "")
        self.assertEqual(warranty.address_from_record(None), "")


# --------------------------------------------------------------------------- the wording


class TestWording(unittest.TestCase):
    def test_the_four_paragraphs_are_the_word_documents(self):
        body = _body()
        start = body.index('<h3 class="ct-warranty-title">')
        end = body.index('<div class="ct-warranty-sign">')
        paragraphs = [_text(p) for p in re.findall(r"<p>(.*?)</p>", body[start:end], re.S)]
        self.assertEqual(paragraphs, WORDING)

    def test_the_company_name_is_bold_where_the_word_document_sets_it_bold(self):
        self.assertIn("The undersigned, <b>Sapphire Fountains, LLC (Sapphire)</b>, warrants", _body())

    def test_the_title_and_the_signature_block_read_as_the_word_document_does(self):
        body = _body()
        self.assertIn("<b>ONE YEAR<br>CERTIFICATE OF WARRANTY</b>", body)
        for label in (
            "<b>Sapphire Fountains, LLC:</b>",
            ">By:</span>",
            ">Its:</span>",
            ">Date:</span>",
            "Work Completed or Material Supplied by Sapphire on: ",
            "Sapphire Project/Number: ",
        ):
            self.assertIn(label, body, label)
        # In the Word document's order.
        order = [body.index(label) for label in (">By:<", ">Its:<", ">Date:<", "Work Completed", "Sapphire Project/Number")]
        self.assertEqual(order, sorted(order))
        # The project block: the project, its address, then Sapphire and its phone.
        block = body[body.index('<div class="ct-warranty-project">') : body.index('<h3 class="ct-warranty-title">')]
        self.assertLess(block.index("doc.project_title"), block.index("doc.site_address"))
        self.assertLess(block.index("doc.site_address"), block.index("Sapphire Fountains, LLC"))
        self.assertLess(block.index("Sapphire Fountains, LLC"), block.index("801-837-2199"))

    def test_only_sapphire_signs_it(self):
        body = _body()
        # sig( in a body is what lets a contract be sent to a customer to sign (esign.api).
        for helper in ("sig(", "initial(", "summary(", "signature."):
            self.assertNotIn(helper, body.replace("sapphire_sig(", ""), helper)
        self.assertIn("{{ sapphire_sig() }}", body)

    def test_no_example_project_is_left_in_the_blank(self):
        # The Word blank still carried the last job it was filled in for. The line holds merge
        # fields and nothing else.
        line = re.search(r"Sapphire Project/Number: (.*?)</p>", _body(), re.S).group(1)
        left = re.sub(r"\{%.*?%\}|\{\{.*?\}\}|<[^>]+>", "", line).strip()
        self.assertEqual(left, "—")
        self.assertNotIn("PJT", _body())


# --------------------------------------------------------------------------- the merge fields


def _render(doc):
    body = _body()
    return project_contract.frappe.render_template(body, project_contract._render_context(doc, body))


@unittest.skipUnless(jinja2, "jinja2 is not installed")
class TestRender(unittest.TestCase):
    def setUp(self):
        _site_with_template()

    def test_a_filled_issued_certificate_prints_every_blank(self):
        html = _render(_contract(docstatus=1, status="Signed"))
        self.assertNotIn("{{", html)
        self.assertNotIn("{%", html)
        self.assertNotIn("None", html)
        self.assertNotIn("ct-blank", html)
        self.assertIn('<p class="ct-warranty-project-name"><span class="ct-val">Example Plaza Water Wall</span></p>', html)
        self.assertIn("12 Example Way<br>Springfield, UT 84000", html)
        self.assertIn('<span class="ct-warranty-label">Its:</span><span class="ct-warranty-line"><span class="ct-val">Operations Manager</span></span>', html)
        self.assertIn('<span class="ct-warranty-label">Date:</span><span class="ct-warranty-line"><span class="ct-val">November 12, 2026</span></span>', html)
        self.assertIn('on: <span class="ct-warranty-line ct-warranty-short"><span class="ct-val">October 30, 2026</span></span>', html)
        words = _text(html)
        self.assertIn("Sapphire Project/Number: Example Plaza Water Wall — PRJ-00999", words)
        self.assertIn('<span class="ct-warranty-number"><span class="ct-val">PRJ-00999</span></span>', html)

    def test_the_by_line_carries_the_name_only_once_it_is_issued(self):
        by = '<span class="ct-warranty-label">By:</span><span class="ct-warranty-line">'
        issued = _render(_contract(docstatus=1, status="Signed"))
        self.assertIn(by + '<span class="ct-sig-typed"', issued)
        self.assertRegex(issued, r'ct-sig-typed"[^>]*>Jordan Example</span>')
        # A draft is not signed, and neither is a cancelled certificate: an empty line.
        for docstatus in (0, 2):
            html = _render(_contract(docstatus=docstatus))
            self.assertIn(by + "&nbsp;</span>", html, docstatus)
            self.assertNotIn("Jordan Example", html, docstatus)
        # Issued with no signer named: left for ink.
        ink = _render(_contract(docstatus=1, status="Out for Signature", warranty_signer=""))
        self.assertIn(by + "&nbsp;</span>", ink)
        self.assertNotIn("ct-sig-typed", ink)

    def test_an_empty_draft_prints_lines_to_fill_in_by_hand(self):
        doc = project_contract.ProjectContract(name="", template_key="warranty", docstatus=0)
        html = _render(doc)
        self.assertNotIn("{{", html)
        self.assertNotIn("None", html)
        # Project name, two address lines and the project/number line are blanks ...
        self.assertEqual(html.count('class="ct-blank"'), 4)
        # ... and the four ruled lines (By, Its, Date, work completed) are empty, not doubled.
        self.assertEqual(html.count('<span class="ct-warranty-line">&nbsp;</span>'), 3)
        self.assertIn('<span class="ct-warranty-line ct-warranty-short">&nbsp;</span>', html)
        self.assertEqual(_text(html).count("—"), 0)

    def test_the_project_line_takes_whichever_half_it_has(self):
        only_name = _text(_render(_contract(project=None)))
        self.assertIn("Sapphire Project/Number: Example Plaza Water Wall", only_name)
        self.assertNotIn("—", only_name)
        only_number = _text(_render(_contract(project_title=None)))
        self.assertIn("Sapphire Project/Number: PRJ-00999", only_number)
        self.assertNotIn("—", only_number)

    def test_typed_values_are_escaped(self):
        html = _render(
            _contract(
                docstatus=1,
                status="Signed",
                project_title="<script>x</script> & Co",
                site_address="1 <b>Way</b>",
                warranty_signer='Jo "J" <i>Example</i>',
                warranty_signer_title="<u>Owner</u>",
            )
        )
        for raw in ("<script>", "<b>Way</b>", "<i>Example</i>", "<u>Owner</u>"):
            self.assertNotIn(raw, html, raw)
        self.assertIn("&lt;script&gt;x&lt;/script&gt; &amp; Co", html)

    def test_the_end_date_never_prints(self):
        doc = _contract(docstatus=1, status="Signed", warranty_end_date="2027-10-30")
        html = _render(doc)
        self.assertNotIn("2027", html)
        self.assertNotIn("October 30, 2027", html)

    def test_the_wording_survives_the_render(self):
        words = " ".join(_text(_render(_contract())).split())
        for paragraph in WORDING:
            self.assertIn(" ".join(paragraph.split()), words)


class TestEveryPrintedFieldHasAnInput(unittest.TestCase):
    def setUp(self):
        _site_with_template()

    def test_every_printed_field_is_shown_for_a_certificate(self):
        fields = {f["fieldname"]: f for f in json.loads(PC_JSON.read_text(encoding="utf-8"))["fields"]}
        section_of, section = {}, None
        for f in json.loads(PC_JSON.read_text(encoding="utf-8"))["fields"]:
            if f["fieldtype"] == "Section Break":
                section = f
            section_of[f["fieldname"]] = section
        printed = set(re.findall(r"\bdoc\.([a-z_][a-z0-9_]*)", _body())) | {"warranty_signer"}
        self.assertEqual(
            printed,
            {"project_title", "site_address", "project", "contract_date", "warranty_start_date", "warranty_signer", "warranty_signer_title"},
        )
        for name in printed:
            self.assertIn(name, fields, name)
            self.assertTrue(builder.section_applies(fields[name].get("depends_on"), "warranty"), name)
            if section_of[name]:
                self.assertTrue(builder.section_applies(section_of[name].get("depends_on"), "warranty"), name)

    def test_the_builder_walk_has_a_step_for_the_dates_and_the_signer(self):
        steps = builder.builder_steps("warranty", "Customer")
        keys = [step["key"] for step in steps]
        self.assertIn("warranty_section", keys)
        # No other contract type's step: no plan, no rental, no subcontract.
        self.assertEqual(keys, ["parties_section", "details_section", "warranty_section"])
        step = steps[keys.index("warranty_section")]
        self.assertEqual(step["title"], "Dates and who signs")
        self.assertTrue(step["intro"])
        names = [f.get("fieldname") for f in step["fields"]]
        for name in ("warranty_start_date", "warranty_end_date", "warranty_signer", "warranty_signer_title"):
            self.assertIn(name, names)
        editable = builder._editable_fields(steps)
        self.assertIn("warranty_start_date", editable)
        self.assertIn("warranty_signer", editable)
        # The end date is shown and never asked for.
        self.assertNotIn("warranty_end_date", editable)
        # The certificate's date rides in on the first step, as on every contract.
        self.assertIn("contract_date", [f.get("fieldname") for f in steps[0]["fields"]])

    def test_the_new_fields_show_for_a_certificate_only(self):
        fields = {f["fieldname"]: f for f in json.loads(PC_JSON.read_text(encoding="utf-8"))["fields"]}
        for name in ("warranty_section", "warranty_start_date", "warranty_end_date", "warranty_signer", "warranty_signer_title"):
            self.assertEqual(fields[name]["depends_on"], 'eval:doc.template_key=="warranty"', name)
            for other in ("maintenance", "owner", "rental", "sow", "msa", "nda", "architect", "employee_contractor"):
                self.assertFalse(builder.section_applies(fields[name]["depends_on"], other), (name, other))
        order = json.loads(PC_JSON.read_text(encoding="utf-8"))["field_order"]
        self.assertEqual(len(order), len(set(order)))
        self.assertEqual(order, [f["fieldname"] for f in json.loads(PC_JSON.read_text(encoding="utf-8"))["fields"]])

    def test_what_is_still_blank_is_listed_and_an_email_address_is_not_called_a_signing_need(self):
        doc = _contract(project_title=None, warranty_start_date=None, contact_email=None)
        steps = builder.builder_steps("warranty", "Customer")
        blanks = builder.blanks(doc, steps)
        names = [row["fieldname"] for row in blanks]
        self.assertIn("project_title", names)
        self.assertIn("warranty_start_date", names)
        email = next(row for row in blanks if row["fieldname"] == "contact_email")
        self.assertNotIn("sign online", email["label"])
        self.assertIn("emailed", email["label"])
        progress = builder.progress(_contract(), steps, _body())
        self.assertEqual(progress["filled"], progress["total"])


# --------------------------------------------------------------------------- only Sapphire signs


class TestIssuingSignsIt(unittest.TestCase):
    def setUp(self):
        _site_with_template()

    def test_only_a_certificate_is_one_sided(self):
        self.assertTrue(warranty.is_one_sided("warranty"))
        for key in ("maintenance", "owner", "rental", "sow", "msa", "nda", "architect", "employee_contractor", "", None):
            self.assertFalse(warranty.is_one_sided(key), key)

    @unittest.skipUnless(jinja2, "jinja2 is not installed")
    def test_issuing_with_a_signer_named_signs_it_and_freezes_the_copy(self):
        _issue()
        row = SITE.contracts[CERT]
        self.assertEqual(row["status"], "Signed")
        self.assertEqual(row["signed_by"], "Jordan Example")
        # Signed the day it is issued, whatever date the draft carried (2026-11-12).
        self.assertEqual(row["signed_on"], TODAY)
        self.assertEqual(row["contract_date"], TODAY)
        self.assertIn("November 20, 2026", row["executed_html"])
        self.assertNotIn("November 12, 2026", row["executed_html"])
        # The copy as issued, signature in place: a later template edit cannot change it.
        self.assertIn("Jordan Example", row["executed_html"])
        self.assertIn("ct-sig-typed", row["executed_html"])
        self.assertIn("Example Plaza Water Wall", row["executed_html"])
        SITE.templates["warranty"]["body"] = "<p>reworded later</p>"
        self.assertEqual(project_contract.ProjectContract(**row).executed_body(), row["executed_html"])
        # The timeline says who pressed the button, because the name on the By line is typed.
        self.assertEqual(len(SITE.comments), 1)
        self.assertIn("Morgan Manager", SITE.comments[0][2])
        self.assertIn("Jordan Example, Operations Manager", SITE.comments[0][2])
        # Issuing sends nothing to anyone.
        self.assertEqual(SITE.sent, [])

    def test_issuing_with_no_signer_waits_for_ink(self):
        for empty in ("", "   ", None):
            _site_with_template()
            doc = _contract(docstatus=1, warranty_signer=empty)
            doc.on_submit()
            row = SITE.contracts[CERT]
            self.assertEqual(row["status"], "Out for Signature", repr(empty))
            self.assertIsNone(row.get("signed_by"))
            self.assertIsNone(row.get("executed_html"))
            self.assertEqual(SITE.comments, [])

    def test_a_certificate_signed_here_is_dated_the_day_it_is_issued(self):
        # A back-dated draft, a post-dated one and an empty one all become today.
        for typed in ("2026-01-05", "2027-03-01", None, TODAY):
            doc = _contract(contract_date=typed)
            doc._date_certificate_signed_here()
            self.assertEqual(doc.contract_date, TODAY, typed)
        # The work-completed date is what carries an earlier job, and it is left alone.
        doc = _contract(contract_date="2026-01-05", warranty_start_date="2025-06-30")
        doc._date_certificate_signed_here()
        self.assertEqual(doc.warranty_start_date, "2025-06-30")

    def test_an_ink_signed_certificate_keeps_its_typed_date(self):
        for empty in ("", "   ", None):
            doc = _contract(contract_date="2026-01-05", warranty_signer=empty)
            doc._date_certificate_signed_here()
            self.assertEqual(doc.contract_date, "2026-01-05", repr(empty))

    def test_no_other_contract_is_re_dated(self):
        doc = _contract(contract_date="2026-01-05", template_key="maintenance", contract_template="maintenance")
        doc._date_certificate_signed_here()
        self.assertEqual(doc.contract_date, "2026-01-05")

    def test_the_date_rule_runs_in_validate_and_the_field_is_read_only_while_a_signer_is_named(self):
        source = PC_PY.read_text(encoding="utf-8")
        validate = source[source.index("\tdef validate(self):") : source.index("\tdef _fetch_template_props(self):")]
        self.assertIn("self._date_certificate_signed_here()", validate)
        signing = source[source.index("\tdef _sign_for_sapphire(self):") : source.index("\tdef on_cancel(self):")]
        self.assertIn('"signed_on": today()', signing)
        self.assertNotIn('self.get("contract_date")', signing)
        fields = {f["fieldname"]: f for f in json.loads(PC_JSON.read_text(encoding="utf-8"))["fields"]}
        condition = fields["contract_date"]["read_only_depends_on"]
        self.assertEqual(condition, 'eval:doc.template_key=="warranty" && doc.warranty_signer')
        # Editable for every other contract, and for a certificate left for ink.
        for doc, read_only in (
            ({"template_key": "warranty", "warranty_signer": "Jordan Example"}, True),
            ({"template_key": "warranty", "warranty_signer": ""}, False),
            ({"template_key": "maintenance", "warranty_signer": "Jordan Example"}, False),
        ):
            self.assertEqual(bool(builder.shown(condition, doc)), read_only, doc)
        # The builder page evaluates the condition, as the form does.
        page = (APP / "project_enhancements" / "page" / "contract_builder" / "contract_builder.js").read_text(encoding="utf-8")
        self.assertIn('["depends_on", "mandatory_depends_on", "read_only_depends_on"]', page)

    def test_no_other_contract_is_signed_by_issuing_it(self):
        doc = _contract(docstatus=1, template_key="maintenance", contract_template="maintenance")
        doc.on_submit()
        self.assertEqual(SITE.contracts[CERT]["status"], "Out for Signature")
        self.assertIsNone(SITE.contracts[CERT].get("signed_by"))

    def test_a_status_already_moved_on_is_left_alone(self):
        doc = _contract(docstatus=1, status="Void")
        doc.on_submit()
        self.assertEqual(SITE.contracts[CERT]["status"], "Void")

    def test_a_failed_timeline_note_does_not_fail_the_issue(self):
        doc = _contract(docstatus=1)
        doc.add_comment = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("comment failed"))
        doc.on_submit()
        self.assertEqual(SITE.contracts[CERT]["status"], "Signed")

    def test_it_has_its_own_series(self):
        self.assertEqual(project_contract.SERIES_BY_KEY["warranty"], "SF-WAR-.YYYY.-.####")
        fields = {f["fieldname"]: f for f in json.loads(PC_JSON.read_text(encoding="utf-8"))["fields"]}
        self.assertIn("SF-WAR-.YYYY.-.####", fields["naming_series"]["options"].split("\n"))
        prefixes = [series.split("-.")[0] for series in project_contract.SERIES_BY_KEY.values()]
        self.assertEqual(len(prefixes), len(set(prefixes)))


class TestNeverSentForACustomerSignature(unittest.TestCase):
    def setUp(self):
        _site_with_template()

    def test_the_builder_offers_no_signing_and_says_it_is_one_sided(self):
        payload = builder._payload(_contract())
        self.assertEqual(payload["one_sided"], 1)
        self.assertEqual(payload["can_sign_here"], 0)
        self.assertEqual(payload["can_send"], 0)
        self.assertEqual(payload["signing"], {})
        self.assertEqual(payload["can_issue"], 1)
        self.assertEqual(payload["can_email"], 0)  # a draft is not emailed

    def test_an_agreement_is_unchanged(self):
        SITE.templates["maintenance"] = dict(SITE.templates["warranty"], name="maintenance", template_key="maintenance")
        SITE.settings.update(contract_esign_consent_text="c", contract_esign_disclosure_text="d")
        project_contract.frappe.get_cached_doc = lambda doctype: _Dict(SITE.settings)
        payload = builder._payload(_contract(template_key="maintenance", contract_template="maintenance"))
        self.assertEqual(payload["one_sided"], 0)
        self.assertEqual(payload["can_sign_here"], 1)
        self.assertEqual(payload["can_send"], 1)
        self.assertEqual(payload["can_email"], 0)

    def test_signing_at_the_table_is_refused(self):
        frappe = project_contract.frappe
        with self.assertRaises(frappe.ValidationError):
            builder._refuse_customer_signature(_contract())
        builder._refuse_customer_signature(_contract(template_key="maintenance"))
        source = (APP / "project_enhancements" / "contract_builder.py").read_text(encoding="utf-8")
        sign_here = source[source.index("def sign_here(") : source.index("def _refuse_customer_signature(")]
        # Before the draft is issued and before any signature input is read.
        self.assertLess(sign_here.index("_refuse_customer_signature(doc)"), sign_here.index("doc.submit()"))

    def test_the_send_paths_refuse_it_before_anything_else(self):
        source = (APP / "project_enhancements" / "esign" / "api.py").read_text(encoding="utf-8")
        guard = source[source.index("def _assert_sendable(contract):") : source.index("def _assert_template_can_show_a_signature(")]
        self.assertIn('is_one_sided(contract.get("template_key"))', guard)
        self.assertLess(guard.index("is_one_sided("), guard.index("contract.docstatus != 1"))
        # Every way a signing link is made goes through that guard.
        self.assertEqual(source.count("\t_assert_sendable(contract)\n"), 3)


# --------------------------------------------------------------------------- email


class TestEmail(unittest.TestCase):
    def setUp(self):
        _site_with_template()
        from erpnext_enhancements import email_style

        self._render = email_style.render
        self.rendered = []

        def render(template, context=None, **kwargs):
            self.rendered.append((template, context, kwargs))
            return "<wrapped email>"

        email_style.render = render
        self.addCleanup(setattr, email_style, "render", self._render)

    def _signed(self, **fields):
        doc = _contract(docstatus=1, **fields)
        doc.on_submit()
        SITE.comments.clear()
        return doc

    @unittest.skipUnless(jinja2, "jinja2 is not installed")
    def test_one_email_to_the_confirmed_address_with_the_frozen_copy_as_a_pdf(self):
        self._signed()
        out = builder.email_certificate(CERT, recipient_email=" owner@example.test ", note="  See you at the walk-through.  ")
        self.assertEqual(out, {"to": "owner@example.test"})
        self.assertEqual(len(SITE.sent), 1)
        mail = SITE.sent[0]
        self.assertEqual(mail["recipients"], ["owner@example.test"])
        self.assertEqual(mail["subject"], "Certificate of Warranty (One Year): Example Plaza Water Wall")
        self.assertEqual(mail["message"], "<wrapped email>")
        self.assertEqual((mail["reference_doctype"], mail["reference_name"]), ("Project Contract", CERT))
        self.assertEqual(len(mail["attachments"]), 1)
        attachment = mail["attachments"][0]
        self.assertEqual(attachment["fname"], f"{CERT}-signed.pdf")
        # The PDF that Print / PDF gives: the contract print format, which prints the copy
        # frozen when the certificate was issued.
        self.assertEqual(SITE.prints, [("Project Contract", CERT, "Project Contract Print", True)])
        self.assertEqual(attachment["fcontent"], b"%PDF-stub " + SITE.contracts[CERT]["executed_html"].encode("utf-8"))
        template, context, kwargs = self.rendered[0]
        self.assertEqual(template, builder.CERTIFICATE_EMAIL_TEMPLATE)
        self.assertEqual(context["recipient_name"], "Riley Sample")
        self.assertEqual(context["number"], CERT)
        self.assertEqual(context["work_completed"], "October 30, 2026")
        self.assertEqual(context["note"], "See you at the walk-through.")
        self.assertEqual(kwargs["pillar"], "build")
        # On the certificate's timeline: who sent it, to whom, with what.
        self.assertEqual(len(SITE.comments), 1)
        self.assertIn("owner@example.test", SITE.comments[0][2])
        self.assertIn("Morgan Manager", SITE.comments[0][2])

    @unittest.skipUnless(jinja2, "jinja2 is not installed")
    def test_the_address_on_the_certificate_is_the_default(self):
        self._signed()
        self.assertEqual(builder.email_certificate(CERT), {"to": "riley@example.test"})
        frappe = project_contract.frappe
        SITE.contracts[CERT]["contact_email"] = None
        with self.assertRaises(frappe.ValidationError):
            builder.email_certificate(CERT)
        self.assertEqual(len(SITE.sent), 1)

    @unittest.skipUnless(jinja2, "jinja2 is not installed")
    def test_only_a_contract_manager_sends_it(self):
        self._signed()
        frappe = project_contract.frappe
        SITE.roles = {"Projects Manager", "Sales User"}
        with self.assertRaises(frappe.PermissionError):
            builder.email_certificate(CERT)
        SITE.roles = {"Contract Manager"}
        SITE.perms.discard("email")
        with self.assertRaises(frappe.PermissionError):
            builder.email_certificate(CERT)
        self.assertEqual(SITE.sent, [])

    def test_a_draft_or_an_unsigned_certificate_is_not_sent(self):
        frappe = project_contract.frappe
        _contract()
        with self.assertRaises(frappe.ValidationError):
            builder.email_certificate(CERT)
        _contract(docstatus=1, status="Out for Signature", warranty_signer="")
        with self.assertRaises(frappe.ValidationError):
            builder.email_certificate(CERT)
        _contract(docstatus=2, status="Void")
        with self.assertRaises(frappe.ValidationError):
            builder.email_certificate(CERT)
        self.assertEqual(SITE.sent, [])

    def test_an_agreement_is_not_sent_this_way(self):
        frappe = project_contract.frappe
        _contract(docstatus=1, status="Signed", template_key="maintenance")
        with self.assertRaises(frappe.ValidationError):
            builder.email_certificate(CERT)
        self.assertEqual(SITE.sent, [])

    def test_an_ink_signed_certificate_needs_its_scan_on_file(self):
        frappe = project_contract.frappe
        # Marked signed on paper: the copy in the system has no signature on it.
        _contract(docstatus=1, status="Signed", warranty_signer="", signed_by="Jordan Example", executed_html="<p>frozen</p>")
        with self.assertRaises(frappe.ValidationError):
            builder.email_certificate(CERT)
        self.assertEqual(SITE.sent, [])
        # With the scan filed (Signed copy > Attach scan), that scan is what goes out.
        url = f"/private/files/{CERT}-signed-scan.pdf"
        SITE.files.append({"name": "FILE-1", "file_name": f"{CERT}-signed-scan.pdf", "file_url": url, "attached_to_name": CERT})
        SITE.downloadable.add(url)
        builder.email_certificate(CERT)
        self.assertEqual(len(SITE.sent), 1)
        attachment = SITE.sent[0]["attachments"][0]
        self.assertEqual(attachment["fname"], f"{CERT}-signed-scan.pdf")
        self.assertEqual(attachment["fcontent"], b"SCAN " + url.encode("utf-8"))
        self.assertEqual(SITE.prints, [])

    @unittest.skipUnless(jinja2, "jinja2 is not installed")
    def test_the_builder_offers_email_only_for_a_signed_certificate_and_a_manager(self):
        self._signed()
        doc = project_contract.frappe.get_doc("Project Contract", CERT)
        self.assertEqual(builder._payload(doc)["can_email"], 1)
        SITE.roles = {"Projects Manager"}
        self.assertEqual(builder._payload(doc)["can_email"], 0)

    def test_nothing_else_in_the_feature_sends_mail(self):
        # Emailing a customer only ever happens from a person's explicit click: one sendmail,
        # inside the endpoint the dialog's Send button calls.
        builder_source = (APP / "project_enhancements" / "contract_builder.py").read_text(encoding="utf-8")
        self.assertEqual(builder_source.count("frappe.sendmail("), 1)
        endpoint = builder_source[builder_source.index("def email_certificate(") : builder_source.index("def _certificate_attachment(")]
        self.assertIn("frappe.sendmail(", endpoint)
        self.assertIn("frappe.only_for(list(SIGN_ROLES))", endpoint)
        self.assertIn('@frappe.whitelist(methods=["POST"])', builder_source[: builder_source.index("def email_certificate(")].rsplit("\n\n", 1)[-1])
        for path in (PC_PY, APP / "project_enhancements" / "warranty_certificate.py", APP / "patches" / "seed_warranty_certificate_template.py"):
            self.assertNotIn("sendmail", path.read_text(encoding="utf-8"), path.name)
        hooks = (APP / "hooks.py").read_text(encoding="utf-8")
        self.assertNotIn("email_certificate", hooks)
        page = (APP / "project_enhancements" / "page" / "contract_builder" / "contract_builder.js").read_text(encoding="utf-8")
        self.assertEqual(page.count("email_certificate`"), 1)
        dialog = page[page.index("\temail_certificate_dialog() {") : page.index("\trender_send_options($main) {")]
        # Called from the dialog's Send button and nowhere else.
        self.assertIn("primary_action: (values) => {", dialog)
        self.assertLess(dialog.index("primary_action: (values) => {"), dialog.index("email_certificate`"))
        self.assertEqual(page.count("this.email_certificate_dialog()"), 1)

    def test_the_email_body_is_a_fragment_that_names_the_attachment(self):
        text = (APP / "templates" / "emails" / "project_enhancements" / "warranty_certificate_customer.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('{% import "erpnext_enhancements/templates/emails/_components.html" as ee %}', text)
        self.assertIn("attached to this email as a PDF", text)
        self.assertNotIn("max-width", text)


# --------------------------------------------------------------------------- filling it from a project


class TestPrefill(unittest.TestCase):
    def setUp(self):
        _site_with_template()
        SITE.settings.update(
            contract_esign_countersigner_name="Jordan Example", contract_esign_countersigner_title="Operations Manager"
        )
        SITE.projects["PRJ-00999"] = {
            "name": "PRJ-00999",
            "project_name": "Example Plaza Water Wall",
            "customer": "Example Plaza LLC",
            "custom_project_address": "12 Example Way, Springfield, UT 84000",
            "custom_customer_name": "Riley Sample",
            "custom_customer_email": "riley@example.test",
            "expected_end_date": "2026-09-01",
        }

    def _create(self):
        created = {}
        original = project_contract.ProjectContract.insert

        def insert(self, ignore_permissions=False):
            self.name = CERT
            created["doc"] = self
            return self

        project_contract.ProjectContract.insert = insert
        self.addCleanup(setattr, project_contract.ProjectContract, "insert", original)
        self.assertEqual(project_contract.create_contract("warranty", "Project", "PRJ-00999"), CERT)
        return created["doc"]

    def test_a_certificate_takes_the_project_the_customer_the_site_and_the_signer(self):
        doc = self._create()
        self.assertEqual(doc.template_key, "warranty")
        self.assertEqual(doc.party_type, "Customer")
        self.assertEqual(doc.party, "Example Plaza LLC")
        self.assertEqual(doc.project, "PRJ-00999")
        self.assertEqual(doc.project_title, "Example Plaza Water Wall")
        # On two lines, as the certificate's project block prints it.
        self.assertEqual(doc.site_address, "12 Example Way\nSpringfield, UT 84000")
        self.assertEqual(doc.contact_email, "riley@example.test")
        self.assertEqual(doc.contract_date, TODAY)
        self.assertEqual(doc.warranty_signer, "Jordan Example")
        self.assertEqual(doc.warranty_signer_title, "Operations Manager")

    def test_the_server_refuses_any_source_but_a_project(self):
        frappe = project_contract.frappe
        self.assertTrue(warranty.source_allowed("Project"))
        for nothing in (None, ""):
            self.assertTrue(warranty.source_allowed(nothing))
        for other in ("Opportunity", "Supplier", "Customer", "Sales Invoice", "Lead"):
            self.assertFalse(warranty.source_allowed(other), other)
            with self.assertRaises(frappe.ValidationError):
                project_contract.create_contract("warranty", other, "ANY-0001")
        # Refused before a draft is made or the source is even read.
        self.assertNotIn(CERT, SITE.contracts)
        source = PC_PY.read_text(encoding="utf-8")
        create = source[source.index("def create_contract(") : source.index("def get_signed_msa(")]
        self.assertLess(create.index("source_allowed("), create.index('frappe.new_doc("Project Contract")'))

    def test_a_certificate_can_start_from_a_client_alone(self):
        original = project_contract.ProjectContract.insert
        made = {}
        project_contract.ProjectContract.insert = lambda self, ignore_permissions=False: made.setdefault("doc", self)
        self.addCleanup(setattr, project_contract.ProjectContract, "insert", original)
        project_contract.create_contract("warranty", None, None, party="Example Plaza LLC")
        self.assertEqual(made["doc"].party, "Example Plaza LLC")
        self.assertIsNone(made["doc"].project)

    def test_the_other_types_still_start_from_an_opportunity(self):
        source = PC_PY.read_text(encoding="utf-8")
        create = source[source.index("def create_contract(") : source.index("def get_signed_msa(")]
        guard = create[create.index("if template_doc.template_key == warranty_certificate.TEMPLATE_KEY and not") :]
        self.assertTrue(guard.startswith("if template_doc.template_key == warranty_certificate.TEMPLATE_KEY and not warranty_certificate.source_allowed("))
        self.assertIn('if source_doctype == "Opportunity" and source_name:', create)

    def test_the_completion_date_is_never_a_plan(self):
        # Only an expected end date on the project: the builder asks rather than guess.
        self.assertIsNone(self._create().warranty_start_date)
        SITE.projects["PRJ-00999"]["actual_end_date"] = "2026-10-28"
        self.assertEqual(self._create().warranty_start_date, "2026-10-28")
        SITE.projects["PRJ-00999"]["custom_production_completed"] = "2026-10-30"
        self.assertEqual(self._create().warranty_start_date, "2026-10-30")

    def test_the_primary_address_fills_in_when_the_project_has_no_address_line(self):
        SITE.projects["PRJ-00999"]["custom_project_address"] = None
        SITE.projects["PRJ-00999"]["primary_address"] = "ADDR-1"
        SITE.addresses["ADDR-1"] = {"address_line1": "40 Sample Road", "city": "Shelbyville", "state": "UT", "pincode": "84001"}
        self.assertEqual(self._create().site_address, "40 Sample Road\nShelbyville, UT 84001")
        SITE.projects["PRJ-00999"]["primary_address"] = None
        self.assertIsNone(self._create().site_address)

    def test_a_failed_lookup_still_makes_the_draft(self):
        SITE.projects["PRJ-00999"]["custom_project_address"] = None
        SITE.projects["PRJ-00999"]["primary_address"] = "ADDR-1"
        SITE.fail_address = True
        doc = self._create()
        self.assertIsNone(doc.site_address)
        self.assertEqual(len(SITE.errors), 1)

    def test_no_other_contract_type_gets_the_split_or_the_signer(self):
        SITE.templates["maintenance"] = dict(SITE.templates["warranty"], name="maintenance", template_key="maintenance")
        original = project_contract.ProjectContract.insert
        made = {}
        project_contract.ProjectContract.insert = lambda self, ignore_permissions=False: made.setdefault("doc", self)
        self.addCleanup(setattr, project_contract.ProjectContract, "insert", original)
        project_contract._seed_fixed_rows = lambda doc: None
        project_contract.create_contract("maintenance", "Project", "PRJ-00999")
        self.assertEqual(made["doc"].site_address, "12 Example Way, Springfield, UT 84000")
        self.assertIsNone(made["doc"].warranty_signer)


# --------------------------------------------------------------------------- the seed and the patch


class TestSeedAndPatch(unittest.TestCase):
    def setUp(self):
        SITE.reset()

    def test_the_seed_lists_the_certificate(self):
        spec = next(s for s in seed.TEMPLATES if s["template_key"] == "warranty")
        self.assertEqual(spec["title"], "Certificate of Warranty (One Year)")
        self.assertEqual(spec["party_type"], "Customer")
        self.assertEqual(spec["requires_msa"], 0)
        self.assertEqual(spec.get("enabled", 1), 1)
        self.assertEqual((APP / "templates" / "contracts" / spec["file"]), TEMPLATE)
        keys = [s["template_key"] for s in seed.TEMPLATES]
        self.assertEqual(len(keys), len(set(keys)))
        for key in keys:
            self.assertIn(key, project_contract.SERIES_BY_KEY)

    def test_a_fresh_site_gets_all_of_them_from_the_seed(self):
        seed.execute()
        self.assertEqual(sorted(SITE.templates), sorted(s["template_key"] for s in seed.TEMPLATES))
        self.assertEqual(SITE.templates["warranty"]["body"], _body())

    def test_the_patch_inserts_that_one_template_and_says_so(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            patch.execute()
        # Only the certificate, on a site missing every template: it never brings back another.
        self.assertEqual(SITE.inserted, ["warranty"])
        row = SITE.templates["warranty"]
        self.assertEqual(row["body"], _body())
        self.assertEqual(row["title"], "Certificate of Warranty (One Year)")
        self.assertEqual(row["enabled"], 1)
        self.assertIn("inserted Contract Template 'warranty'", out.getvalue())

    def test_run_twice_it_leaves_the_record_as_it_is(self):
        with contextlib.redirect_stdout(io.StringIO()):
            patch.execute()
        SITE.templates["warranty"]["body"] = "<p>edited on the site</p>"
        SITE.templates["warranty"]["enabled"] = 0
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            patch.execute()
        self.assertEqual(SITE.inserted, ["warranty"])
        self.assertEqual(SITE.templates["warranty"]["body"], "<p>edited on the site</p>")
        self.assertEqual(SITE.templates["warranty"]["enabled"], 0)
        self.assertIn("already there", out.getvalue())

    def test_it_never_raises(self):
        SITE.fail_insert = True
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            patch.execute()
        self.assertNotIn("warranty", SITE.templates)
        self.assertIn("not inserted", out.getvalue())
        self.assertEqual(len(SITE.errors), 1)
        # Even when the Error Log itself cannot be written.
        project_contract.frappe.log_error = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no log"))
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                patch.execute()
        finally:
            project_contract.frappe.log_error = lambda *a, **k: SITE.errors.append((a, k))

    def test_it_is_registered_after_the_seed(self):
        lines = [
            line.strip()
            for line in (APP / "patches.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        name = "erpnext_enhancements.patches.seed_warranty_certificate_template"
        self.assertEqual(lines.count(name), 1)
        self.assertLess(lines.index("erpnext_enhancements.patches.seed_contract_templates"), lines.index(name))
        self.assertGreater(lines.index(name), lines.index("[post_model_sync]"))


# --------------------------------------------------------------------------- how it looks


class TestLook(unittest.TestCase):
    def test_the_letterhead_says_warranty_and_agreements_still_say_agreement(self):
        self.assertEqual(contract_style.PILLAR_BY_TEMPLATE["warranty"], "build")
        self.assertEqual(contract_style.eyebrow_for(_Dict(template_key="warranty")), "WARRANTY")
        for key in ("maintenance", "owner", "rental", "sow", "", None):
            self.assertEqual(contract_style.eyebrow_for(_Dict(template_key=key)), "AGREEMENT", key)
        self.assertEqual(contract_style.eyebrow_for(None), "AGREEMENT")
        wrapped = contract_style.wrap("<p>body</p>", _Dict(name=CERT, template_key="warranty"))
        self.assertIn("BUILD &middot; WARRANTY", wrapped)
        self.assertNotIn("AGREEMENT", wrapped)
        self.assertIn("SERVICE &middot; AGREEMENT", contract_style.wrap("", _Dict(name="X", template_key="maintenance")))
        self.assertIn("BUILD &middot; AGREEMENT", contract_style.letterhead_html("build"))

    def test_the_footer_names_the_certificate_and_does_not_call_it_confidential(self):
        footer = contract_style.footer_html(_Dict(name=CERT, template_key="warranty"))
        self.assertIn(f"Sapphire Fountains, LLC  |  {CERT}", footer)
        self.assertNotIn("Confidential", footer)
        self.assertIn("Confidential", contract_style.footer_html(_Dict(name="SF-MAINT-2026-0001", template_key="maintenance")))
        self.assertIn("Confidential", contract_style.footer_html(None))

    def test_the_print_format_styles_every_class_the_certificate_uses(self):
        css = next(
            row for row in json.loads(PRINT_FORMATS.read_text(encoding="utf-8")) if row["name"] == "Project Contract Print"
        )["css"]
        static = re.sub(r"\{%.*?%\}|\{\{.*?\}\}", "", _body())
        used = set(re.findall(r'class="([^"]+)"', static))
        classes = {name for value in used for name in value.split()}
        self.assertEqual({name for name in classes if not name.startswith("ct-warranty")}, set())
        rules = [line for line in css.splitlines() if line.startswith(".contract-doc .ct-warranty")]
        for name in classes - {"ct-warranty"}:
            self.assertTrue(
                any(re.search(rf"\.{re.escape(name)}(?![\w-])", rule.split("{")[0]) for rule in rules), name
            )
        # Every rule is under the certificate's own wrapper, so no agreement is restyled.
        for line in css.splitlines():
            if "ct-warranty" in line and "{" in line:
                self.assertRegex(line, r"^\.contract-doc \.ct-warranty(\.ct-warranty-fit-[123])? ", line)
        self.assertTrue(
            _body().startswith('<div class="ct-warranty{% if warranty_fit %} {{ warranty_fit }}{% endif %}">')
        )
        # The title clears the floated project block, so a long address cannot run under it.
        self.assertRegex(css, r"h3\.ct-warranty-title \{ clear: both;")

    def test_the_signed_copy_viewer_does_not_ask_for_a_scan_that_never_existed(self):
        from erpnext_enhancements.project_enhancements import signed_copy

        _site_with_template()
        doc = _contract(docstatus=1, status="Signed", signed_by="Jordan Example", executed_html="<p>frozen</p>")
        info = signed_copy.signed_copy(doc)
        self.assertEqual(info["route"], "issued")
        self.assertEqual(info["executed"], 1)
        self.assertEqual(info["can_attach"], 0)
        # Signed in ink: still the paper route, with Attach scan for a manager.
        ink = signed_copy.signed_copy(
            _contract(docstatus=1, status="Signed", warranty_signer="", signed_by="Jordan Example", executed_html="<p>x</p>")
        )
        self.assertEqual(ink["route"], "paper")
        self.assertEqual(ink["can_attach"], 1)
        viewer = (APP / "public" / "js" / "project_enhancements" / "contract_viewer.js").read_text(encoding="utf-8")
        self.assertIn('info.route === "issued"', viewer)


# --------------------------------------------------------------------------- one page, always

#: Made-up long cases. 140 characters is the longest a project's name can be (a Data field).
LONG_WORDS = (
    "Example Plaza Phase Two Interactive Fountain and Water Wall Renovation at the North Courtyard "
    "of the Springfield Civic Center Annex Building"
)
ADDRESS_2 = "12 Example Way\nSpringfield, UT 84000"
ADDRESS_3 = "1200 North Example Parkway, Building C\nSuite 410, North Courtyard Entrance\nSouth Springfield Heights, UT 84000-1234"
ADDRESS_4 = "Springfield Civic Center Annex\n" + ADDRESS_3


class TestFitsOnePage(unittest.TestCase):
    def setUp(self):
        _site_with_template()

    def test_an_ordinary_certificate_keeps_the_ordinary_layout(self):
        for name, address in (
            ("Example Plaza Water Wall", ADDRESS_2),
            (LONG_WORDS[:40].strip(), ADDRESS_2),
            ("", ""),
            (None, None),
        ):
            self.assertEqual(warranty.fit_level(name, address), 0, name)
            self.assertEqual(warranty.fit_class(name, address), "", name)

    def test_longer_names_and_addresses_take_tighter_steps(self):
        levels = [
            warranty.fit_level(LONG_WORDS[:length].strip(), address)
            for length, address in ((25, ADDRESS_2), (60, ADDRESS_2), (90, ADDRESS_3), (120, ADDRESS_3), (140, ADDRESS_4))
        ]
        self.assertEqual(levels, sorted(levels))
        self.assertEqual(levels[0], 0)
        self.assertEqual(levels[-1], warranty.MAX_FIT)
        # The reviewer's case: about ninety characters of name over a three-line address.
        self.assertGreaterEqual(warranty.fit_level(LONG_WORDS[:90].strip(), ADDRESS_3), 2)
        # Never past the steps the stylesheet has, however long.
        self.assertEqual(warranty.fit_level("W" * 2000, "\n".join(["x" * 200] * 12)), warranty.MAX_FIT)
        self.assertEqual(warranty.fit_class("W" * 2000, ""), f"ct-warranty-fit-{warranty.MAX_FIT}")
        # A longer name never gets a looser step.
        previous = 0
        for length in range(0, 141, 5):
            level = warranty.fit_level(LONG_WORDS[:length], ADDRESS_3)
            self.assertGreaterEqual(level, previous, length)
            previous = level

    def test_the_stylesheet_has_every_step_and_only_those(self):
        css = next(
            row for row in json.loads(PRINT_FORMATS.read_text(encoding="utf-8")) if row["name"] == "Project Contract Print"
        )["css"]
        steps = sorted(set(re.findall(r"\.ct-warranty-fit-(\d+)", css)))
        self.assertEqual(steps, [str(n) for n in range(1, warranty.MAX_FIT + 1)])
        sizes = [float(re.search(rf"\.ct-warranty\.ct-warranty-fit-{n} p \{{ font-size: ([\d.]+)pt", css).group(1)) for n in steps]
        base = float(re.search(r"\.contract-doc \.ct-warranty p \{ font-size: ([\d.]+)pt", css).group(1))
        # Each step is smaller than the one before, and none is too small to read.
        self.assertEqual([base, *sizes], sorted([base, *sizes], reverse=True))
        self.assertGreaterEqual(sizes[-1], 9)
        # A word with no space in it wraps inside the project block instead of running out of it.
        self.assertRegex(css, r"\.ct-warranty-project \{[^}]*overflow-wrap: break-word")

    @unittest.skipUnless(jinja2, "jinja2 is not installed")
    def test_the_step_is_on_the_wrapper_and_nothing_is_cut(self):
        ordinary = _render(_contract())
        self.assertTrue(ordinary.startswith('<div class="ct-warranty">'))
        name = LONG_WORDS[:140].strip()
        html = _render(_contract(project_title=name, site_address=ADDRESS_4))
        self.assertTrue(html.startswith(f'<div class="ct-warranty ct-warranty-fit-{warranty.MAX_FIT}">'))
        # In full, both places it prints, and every line of the address.
        self.assertEqual(html.count(name), 2)
        for line in ADDRESS_4.splitlines():
            self.assertIn(line, html)
        self.assertNotIn("\u2026", html)
        self.assertNotIn("...", _text(html))


def _find_chrome():
    """A Chrome or Chromium that can print to PDF, or None. CI's ubuntu runner ships google-chrome."""
    for candidate in (
        os.environ.get("CHROME_BIN"),
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("chrome"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ):
        if candidate and os.path.exists(candidate):
            return candidate
    return None


#: A deliberately wide face for the second pass: the server that prints the PDF may have neither
#: Lato nor Helvetica, and a fallback such as DejaVu Sans or Verdana sets about a tenth wider.
WIDE_FACE = '"DejaVu Sans", Verdana, sans-serif'


def _page_html(doc, face=None):
    """The certificate as the print format lays it out: the real body, letterhead and print CSS.

    The page box is Letter with 15mm on every side. Production gives the body the same 15mm at
    the top and the sides and, at the foot, the height of the running footer (one 8pt line, about
    5mm), so this box is the tighter of the two: a certificate that fits here fits there.
    """
    css = next(
        row for row in json.loads(PRINT_FORMATS.read_text(encoding="utf-8")) if row["name"] == "Project Contract Print"
    )["css"]
    wide = f".contract-doc, .contract-doc * {{ font-family: {face} !important; }}" if face else ""
    return (
        "<!doctype html><html><head><meta charset='utf-8'><style>@page { size: Letter; margin: 15mm; } "
        f"body {{ margin: 0; }}{css}{wide}</style></head><body>"
        f'<div class="contract-doc contract-warranty">{contract_style.wrap(_render(doc), doc)}</div></body></html>'
    )


def _pdf_pages(chrome, html, folder, stem):
    """Print ``html`` with headless Chrome and count the pages, or None when Chrome did not print."""
    source = Path(folder) / f"{stem}.html"
    target = Path(folder) / f"{stem}.pdf"
    source.write_text(html, encoding="utf-8")
    try:
        subprocess.run(
            [
                chrome,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--user-data-dir={Path(folder) / 'profile'}",
                f"--print-to-pdf={target}",
                source.as_uri(),
            ],
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if not target.exists() or not target.stat().st_size:
        return None
    return len(re.findall(rb"/Type\s*/Page(?![s\w])", target.read_bytes()))


@unittest.skipUnless(jinja2, "jinja2 is not installed")
class TestPrintsOnOnePage(unittest.TestCase):
    """The long cases printed for real. Skipped where no Chrome is installed; CI's runner has one."""

    CASES = {
        "ordinary": ("Example Plaza Water Wall", ADDRESS_2),
        "name_90_address_3": (LONG_WORDS[:90].strip(), ADDRESS_3),
        "name_120_address_3": (LONG_WORDS[:120].strip(), ADDRESS_3),
        "name_140_address_4": (LONG_WORDS[:140].strip(), ADDRESS_4),
        "one_unbroken_word": ("X" * 70 + " " + "W" * 69, ADDRESS_4),
    }

    @classmethod
    def setUpClass(cls):
        cls.chrome = _find_chrome()
        if not cls.chrome:
            raise unittest.SkipTest("no Chrome or Chromium is installed")
        cls.folder = tempfile.mkdtemp(prefix="warranty-certificate-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(getattr(cls, "folder", ""), ignore_errors=True)

    def setUp(self):
        _site_with_template()

    def _pages(self, key, face=None):
        name, address = self.CASES[key]
        doc = _contract(
            docstatus=1,
            status="Signed",
            project_title=name,
            site_address=address,
            warranty_signer="Jordan Alexander Example-Longname",
            warranty_signer_title="Director of Operations and Field Services",
        )
        pages = _pdf_pages(self.chrome, _page_html(doc, face), self.folder, f"{key}-{'wide' if face else 'plain'}")
        if pages is None:
            self.skipTest("Chrome is installed but did not print a PDF here")
        return pages

    def test_every_case_is_one_page(self):
        for key in self.CASES:
            with self.subTest(case=key):
                self.assertEqual(self._pages(key), 1)

    def test_every_case_is_one_page_in_a_wide_face(self):
        for key in self.CASES:
            with self.subTest(case=key):
                self.assertEqual(self._pages(key, WIDE_FACE), 1)

    def test_the_test_can_see_a_second_page(self):
        # The counter is not blind: the same certificate with its body tripled runs over.
        name, address = self.CASES["ordinary"]
        doc = _contract(docstatus=1, status="Signed", project_title=name, site_address=address)
        html = _page_html(doc)
        body = contract_style.wrap(_render(doc), doc)
        pages = _pdf_pages(self.chrome, html.replace(body, body * 3), self.folder, "three-bodies")
        if pages is None:
            self.skipTest("Chrome is installed but did not print a PDF here")
        self.assertGreater(pages, 1)


# --------------------------------------------------------------------------- wiring


class TestWiring(unittest.TestCase):
    def test_the_builder_page_ends_a_certificate_in_issue_not_sign(self):
        page = (APP / "project_enhancements" / "page" / "contract_builder" / "contract_builder.js").read_text(encoding="utf-8")
        review = page[page.index("\trender_review(dir) {") : page.index("\trender_review_checks() {")]
        self.assertIn("} else if (data.one_sided) this.render_certificate_options($main);", review)
        self.assertLess(review.index("data.one_sided) this.render_certificate_options"), review.index("this.render_signbox($main)"))
        options = page[page.index("\trender_certificate_options($main) {") : page.index("\tissue_certificate(signer, title) {")]
        for gone in ("send_dialog", "sign_here_dialog", "set_mode("):
            self.assertNotIn(gone, options, gone)
        self.assertIn('__("Sign and issue")', options)
        self.assertIn('__("Issue for an ink signature")', options)
        signed = page[page.index("\trender_signed($main) {") : page.index("\tcard_on_file($button) {")]
        self.assertIn("if (data.can_email) {", signed)
        self.assertIn("It has not been emailed to anyone.", signed)
        # Routes only: Back and Forward keep working.
        for block in (options, signed):
            self.assertNotIn("window.open", block)
            self.assertNotIn("location.href", block)

    def test_a_certificate_starts_from_a_project_never_an_opportunity(self):
        page = (APP / "project_enhancements" / "page" / "contract_builder" / "contract_builder.js").read_text(encoding="utf-8")
        self.assertIn('if (!supplier && !any_party && type.template_key !== "warranty") {', page)
        actions = (APP / "public" / "js" / "global_enhancements" / "record_actions.js").read_text(encoding="utf-8")
        block = actions[actions.index("export function contract_types(") : actions.index("function enabled_contract_types(")]
        self.assertIn('if (doctype === "Project") {', block)
        self.assertLess(block.index('if (doctype === "Project") {'), block.index('value: "warranty"'))
        self.assertEqual(block.count('value: "warranty"'), 1)

    def test_the_form_does_not_offer_send_for_signature(self):
        form = (DOCTYPES / "project_contract" / "project_contract.js").read_text(encoding="utf-8")
        self.assertIn('const one_sided = frm.doc.template_key === "warranty";', form)
        self.assertIn("if (signable && frappe.boot.ee_contract_esign && !one_sided) {", form)

    def test_has_its_own_ci_step(self):
        ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("run: python -m unittest erpnext_enhancements.tests.test_warranty_certificate -v", ci)

    def test_the_readme_says_how_it_works(self):
        readme = (APP / "project_enhancements" / "README.md").read_text(encoding="utf-8")
        self.assertIn("Certificate of Warranty", readme)
        self.assertIn("warranty_certificate.py", readme)


if __name__ == "__main__":
    unittest.main()
