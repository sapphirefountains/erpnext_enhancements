"""Executable contract for the Phase 4 seams — written BEFORE the code.

Same discipline as Phase 3, which worked: four integration breaks reached main in
Phase 2, zero in Phase 3 after the joins were specified up front and every
assertion mutation-tested.

Phase 4 is certificates, recertification, portal access, supervisor sign-off,
ask-the-author Q&A and gamification. Its risks are different in kind from the
earlier phases — less "two halves that do not fit" and more **"a safeguard that
quietly stops being one"**. The assertions below are weighted accordingly:

* the compliance check must **warn and never throw**. It fires on a maintenance
  visit or a task assignment, by which time a truck is usually already at a site;
  a hard block there means the work happens with no record at all, which is worse
  than the uncertified assignment it was trying to prevent;
* the certificate print format must be registered **before**
  ``ensure_chrome_pdf_generator``, which is deliberately last in ``after_migrate``
  and has to see every format the hooks above it created;
* a certificate stores its **rendered** HTML, so editing a print format later
  never rewrites a document somebody already has;
* the public verify route leaks **no PII beyond initials**, and its controller
  filename must be underscored or Frappe never imports it;
* staff and customers never appear on the same leaderboard.

**How to read the skips.** Phase 4 does not exist yet, so its tests skip. That is
temporary and deliberate: they are the acceptance criteria for the build. When it
lands, nothing here may skip.

Run: python -m unittest erpnext_enhancements.tests.test_training_phase4_contracts
"""

import ast
import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"

HOOKS = APP / "hooks.py"
DOCTYPE_DIR = APP / "training/doctype"

# Phase 4, to be built.
COMPLIANCE = APP / "training/compliance.py"
CERTIFICATES = APP / "training/certificates.py"
GAMIFICATION = APP / "training/gamification.py"
CERT_PRINT = APP / "training/setup_print_formats.py"
VERIFY_WWW = APP / "www/training_certificate.py"
CERT_DOCTYPE = DOCTYPE_DIR / "training_certificate/training_certificate.json"
SIGNOFF_DOCTYPE = DOCTYPE_DIR / "training_signoff/training_signoff.json"

PHASE4_FILES = (COMPLIANCE, CERTIFICATES, CERT_DOCTYPE, SIGNOFF_DOCTYPE)


def _read(p):
    return p.read_text(encoding="utf-8")


def _hooks_value(name):
    tree = ast.parse(_read(HOOKS))
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id == name:
                try:
                    return ast.literal_eval(node.value)
                except Exception:
                    return None
    return None


def _fn_body(src, name):
    """Source of one top-level function, for scoping an assertion to it."""
    marker = f"def {name}"
    if marker not in src:
        return ""
    start = src.index(marker)
    nxt = src.find("\ndef ", start + 1)
    return src[start : nxt if nxt != -1 else len(src)]


phase4 = unittest.skipUnless(
    all(p.exists() for p in PHASE4_FILES),
    "Phase 4 not built yet — this is its acceptance criterion",
)


class TestPhase4Completeness(unittest.TestCase):
    def test_phase4_is_actually_built(self):
        """So a skipped suite is never mistaken for a passing one."""
        missing = [p.name for p in PHASE4_FILES if not p.exists()]
        if missing:
            self.skipTest(f"Phase 4 not built yet; missing {missing}")


# ------------------------------------------- the highest-stakes one: warn only


class TestComplianceWarnsAndNeverBlocks(unittest.TestCase):
    """The uncertified-technician check must never refuse a save.

    It fires on ``Sapphire Maintenance Record`` and ``Task`` validate. By the time
    it runs, a truck is usually already at a site. Blocking the form means the
    visit happens with no record at all — strictly worse than the uncertified
    assignment the check exists to flag. This was an explicit product decision
    ("warn only, don't block"), so it is pinned rather than trusted.
    """

    @phase4
    def test_the_module_never_throws(self):
        src = _read(COMPLIANCE)
        self.assertNotIn(
            "frappe.throw",
            src,
            "compliance must warn, never block — a hard stop here means the work happens unrecorded",
        )

    @phase4
    def test_it_warns_visibly(self):
        self.assertRegex(_read(COMPLIANCE), r"msgprint|add_comment|notif")

    @phase4
    def test_it_is_gated_on_the_settings_switch(self):
        self.assertIn("warn_on_uncertified_dispatch", _read(COMPLIANCE))

    @phase4
    def test_hooks_attach_it_to_validate_not_before_submit(self):
        """on validate so it is advisory; before_submit would read as a gate."""
        events = _hooks_value("doc_events") or {}
        for doctype in ("Sapphire Maintenance Record", "Task"):
            handlers = json.dumps(events.get(doctype, {}))
            if "training.compliance" in handlers:
                self.assertIn("validate", events[doctype])


# ------------------------------------------------- certificates and their PDF


class TestCertificateIntegrity(unittest.TestCase):
    @phase4
    def test_certificate_stores_its_rendered_html(self):
        """Editing a print format later must never rewrite a certificate somebody
        already holds. The snapshot is what makes it an artefact rather than a
        live query."""
        fields = {f["fieldname"] for f in json.loads(_read(CERT_DOCTYPE))["fields"]}
        self.assertIn("certificate_html", fields)

    @phase4
    def test_certificate_is_submittable_and_immutable(self):
        data = json.loads(_read(CERT_DOCTYPE))
        self.assertEqual(data.get("is_submittable"), 1)

    @phase4
    def test_certificate_carries_a_verification_code(self):
        fields = {f["fieldname"] for f in json.loads(_read(CERT_DOCTYPE))["fields"]}
        self.assertIn("verification_code", fields)

    @phase4
    def test_print_format_setup_runs_before_the_pdf_backend_hook(self):
        """ensure_chrome_pdf_generator is deliberately LAST in after_migrate and
        has to see every format the hooks above it created. Registered after it,
        the certificate silently uses the wrong backend."""
        hooks = _hooks_value("after_migrate") or []
        training = [i for i, h in enumerate(hooks) if "training.setup_print_formats" in h]
        chrome = [i for i, h in enumerate(hooks) if "ensure_chrome_pdf_generator" in h]
        if not training:
            self.skipTest("training print formats not registered yet")
        self.assertTrue(chrome, "ensure_chrome_pdf_generator missing from after_migrate")
        self.assertLess(max(training), min(chrome))


# ------------------------------------------------------- the public verify page


class TestPublicVerifyRoute(unittest.TestCase):
    @phase4
    def test_controller_filename_is_underscored(self):
        """A www/ controller whose filename contains a hyphen is NEVER imported by
        Frappe — get_context silently never runs. Already burned this repo once
        (stripe-return.py)."""
        self.assertTrue(VERIFY_WWW.exists(), "expected www/training_certificate.py, underscored")
        self.assertNotIn("-", VERIFY_WWW.stem)

    @phase4
    def test_verify_is_guest_reachable(self):
        src = _read(CERTIFICATES) + (_read(VERIFY_WWW) if VERIFY_WWW.exists() else "")
        self.assertRegex(src, r"allow_guest\s*=\s*True|no_cache")

    @phase4
    def test_verify_returns_no_personal_data_beyond_initials(self):
        """A verification link is shared with third parties. Returning a full name,
        an email or an employee id turns a proof-of-training into a disclosure."""
        body = _fn_body(_read(CERTIFICATES), "verify_certificate")
        if not body:
            self.skipTest("verify_certificate not implemented in certificates.py")
        for leaked in ("employee", "user", "email"):
            self.assertNotRegex(
                body,
                rf'["\']{leaked}["\']\s*:',
                f"verify_certificate returns {leaked!r}; initials only",
            )


# ----------------------------------------------------------------- gamification


class TestLeaderboardSeparatesStaffFromCustomers(unittest.TestCase):
    @phase4
    def test_customers_and_staff_are_not_ranked_together(self):
        """A customer taking a handover course must never appear on an internal
        leaderboard, nor staff on theirs."""
        src = _read(GAMIFICATION) if GAMIFICATION.exists() else ""
        if not src:
            self.skipTest("gamification not built yet")
        self.assertRegex(
            src,
            r"learner_type|customer|Website User",
            "leaderboard must separate staff from customer learners",
        )


# ------------------------------------------------- links deferred from Phase 1


class TestDeferredLinksAreRestored(unittest.TestCase):
    """Phase 1 omitted these because a Link to a non-existent DocType fails
    ``bench migrate``. Those doctypes exist as of Phase 4, so the links belong
    back — an Assignment with no route to its Completion cannot report anything.
    """

    @phase4
    def test_assignment_links_to_its_completion(self):
        data = json.loads(_read(DOCTYPE_DIR / "training_assignment/training_assignment.json"))
        fields = {f["fieldname"]: f for f in data["fields"]}
        self.assertIn("completion", fields)
        self.assertEqual(fields["completion"].get("options"), "Training Completion")

    @phase4
    def test_every_link_target_exists(self):
        """The Phase-1 lesson, generalised: a Link naming a DocType this app does
        not ship fails migrate outright."""
        shipped = {
            json.loads(_read(p))["name"]
            for p in DOCTYPE_DIR.glob("*/*.json")
            if json.loads(_read(p)).get("doctype") == "DocType"
        }
        core = {
            "User", "Employee", "Customer", "Role", "Print Format", "Contact",
            "Department", "Designation", "Role Profile", "File", "Task",
            "Employee Grade", "Employment Type",
        }
        for path in DOCTYPE_DIR.glob("*/*.json"):
            data = json.loads(_read(path))
            if data.get("doctype") != "DocType":
                continue
            for field in data.get("fields", []):
                if field.get("fieldtype") in ("Link", "Table", "Table MultiSelect"):
                    target = field.get("options")
                    if target and target not in core and target not in shipped:
                        self.fail(
                            f"{data['name']}.{field['fieldname']} links to {target!r}, "
                            "which this app does not ship — bench migrate would fail"
                        )


if __name__ == "__main__":
    unittest.main()
