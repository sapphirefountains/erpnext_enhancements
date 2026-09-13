# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""`frappe.db.get_value` returns **None**, not a row of Nones, when there is no row.

So ``a, b = frappe.db.get_value(dt, name, ["x", "y"])`` does not hand back ``(None, None)``
on a miss — it raises ``TypeError: cannot unpack non-iterable NoneType object``. Verified
against the running site on 2026-09-13::

    frappe.db.get_value("Contact", "NO-SUCH-CONTACT-XYZ", ["first_name", "last_name"])
    -> None
    a, b = <that>
    -> TypeError: cannot unpack non-iterable NoneType object

--------------------------------------------------------------------------------------
Why it is worth a guard
--------------------------------------------------------------------------------------

The unpack is not the bug; **where the exception lands** is. ``tasks.py`` did this inside
``generate_predictive_maintenance_records``, which ``scheduler_events`` reaches through
``predictive_maintenance_scheduling`` — both names verified against the running site, after
the first version of this docstring invented a third. An uncaught exception there ends the
whole job: every remaining item in the loop and every later step in the same task are
skipped, and a scheduled job that dies this way leaves nothing a user would ever see.
Nobody finds out until somebody asks why the visits stopped.

The second one fixed alongside it, ``api/telephony.log_call_details``, is a whitelist
endpoint taking ``reference_docname`` from its caller, so a deleted or mistyped Contact is
reachable from outside. The ``Customer`` and ``Lead`` branches on either side of it were
already None-safe with ``or display_name``; only the ``Contact`` branch unpacked. **The
safe idiom was already present in the very block that got it wrong** — the same shape as
the nullable-date filter in ``tasks.py``, where a sibling clause in the same filter
already carried the guard.

--------------------------------------------------------------------------------------
Why an allowlist rather than a blanket ban
--------------------------------------------------------------------------------------

A sweep on 2026-09-13 found nine of these. **Seven are fine**, and banning them outright
would be the same mistake the first draft of ``test_nullable_date_filters`` made when it
keyed on a field name and flagged three correct filters. A guard that cries wolf gets
switched off.

They are fine for three different reasons, and the reason is what is recorded below:
the name was resolved by a lookup earlier in the same call; it is a Link field the
framework validated on save; or the call sits inside a ``try`` that logs and moves on.
None of those is "it cannot happen" — they are arguments about *where the failure lands*,
which is the same question the two fixes answer differently.

So this file fails on a **new** unpack, not on the existing ones. Adding an entry is a
deliberate act that makes somebody write down why.

Two spellings make an unpack disappear from this scan, and both are real fixes rather than
ways to silence it: ``as_dict=True`` with an explicit ``if not row: ...``, or
``... or (None, None)``. Neither parses as a bare tuple-assignment from a call.

Bench-free: filesystem and `ast` only.

Run: python -m unittest erpnext_enhancements.tests.test_get_value_unpacking
"""

import ast
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"tests", "node_modules", "__pycache__"}
UNPACKED = {"get_value", "get_cached_value"}

#: (relative path, unpacked names, the name expression) -> why landing a TypeError here is
#: acceptable. Every one of these was read on 2026-09-13 before it was written down.
#:
#: The third element is load-bearing and was learned the hard way: the first version of
#: this file keyed on (path, names) alone, and `api/telephony.py` holds two ALLOWED
#: unpacks plus the one that was fixed -- two of which unpack into `(first, last)`. The
#: key could not tell the fixed site from the kept one. The identifier being looked up is
#: what actually names a site, and it is also the thing a reader needs in order to judge
#: whether the row can be missing.
ALLOWED = {
    (
        "api/telephony.py",
        "(first, last)",
        "contact_name",
    ): "contact_name was resolved by a lookup or an insert earlier in the same call",
    (
        "api/telephony.py",
        "(cur_first, cur_last)",
        "contact_name",
    ): "same — guarded by `if contact_name:` on a name this function just resolved",
    (
        "crm_enhancements/fountain_move/invites.py",
        "(status, opened)",
        "invite_name",
    ): "background job wrapped in try/except Exception + log_error; a deleted invite "
    "degrades to an Error Log and the invite stays at Sent",
    (
        "project_enhancements/doctype/project_contract/project_contract.py",
        "(template_key, template_party_type)",
        "self.contract_template",
    ): "a Link the framework validates on save",
    (
        "project_enhancements/esign/portal.py",
        "(party_type, party)",
        "request.project_contract",
    ): "a Link on the stored esign request, not a field off the POST",
    (
        "quickbooks_online/core/group_account_remap.py",
        "(lft, rgt)",
        "parent",
    ): "`if not parent: continue` sits immediately above it",
}

#: Fixed in v1.426.6, by full key. Listed so a revert fails rather than passing quietly.
FIXED = {
    (
        "tasks.py",
        "(so_status, so_project, so_customer)",
        "item.parent",
    ): "scheduler_events — an uncaught TypeError ends the whole nightly job",
    (
        "api/telephony.py",
        "(first, last)",
        "reference_docname",
    ): "whitelist endpoint; reference_docname comes from the caller",
}


def _shipped_files():
    for path in sorted(APP.rglob("*.py")):
        if SKIP_DIRS & set(path.parts):
            continue
        yield path


def _unpacked_get_values():
    """(relative path, unpacked names, the identifier looked up, line) for every bare
    tuple-assignment from a `get_value` call. A call wrapped in `or (...)` is a BoolOp,
    not a Call, so the guarded spelling does not appear here — which is the point."""
    for path in _shipped_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Tuple) or not isinstance(node.value, ast.Call):
                continue
            func = node.value.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name not in UNPACKED:
                continue
            if any(kw.arg in ("as_dict", "as_list") for kw in node.value.keywords):
                continue
            args = node.value.args
            looked_up = ast.unparse(args[1]) if len(args) > 1 else ""
            yield (
                str(path.relative_to(APP)).replace("\\", "/"),
                ast.unparse(target),
                looked_up,
                node.lineno,
            )


class TestNoNewUnguardedUnpack(unittest.TestCase):
    def test_every_unpack_is_one_somebody_signed_off(self):
        """A new one fails here, and the fix is either a guard or an entry with a reason."""
        offenders = [
            f"{path}:{line} — {names} = get_value(..., {looked_up}, ...) with no guard"
            for path, names, looked_up, line in _unpacked_get_values()
            if (path, names, looked_up) not in ALLOWED
        ]
        self.assertEqual(
            offenders,
            [],
            "`get_value` returns None, not a row of Nones, so this raises TypeError on a "
            "missing row:\n  " + "\n  ".join(offenders),
        )

    def test_the_two_fixed_sites_stay_fixed(self):
        """Both were reachable somewhere a TypeError is expensive: a scheduled job that
        dies silently, and a whitelist endpoint that loses a completed call's record."""
        found = {key[:3] for key in _unpacked_get_values()}
        for key, why in FIXED.items():
            with self.subTest(site=f"{key[0]}:{key[2]}"):
                self.assertNotIn(key, found, f"{key[0]} unpacks {key[2]} again — {why}")


class TestTheGuardCannotPassVacuously(unittest.TestCase):
    """An absence assertion over a walk: if the matcher stopped matching, it would report
    clean on a repo full of the bug."""

    def test_the_walk_still_finds_the_allowlisted_ones(self):
        found = {key[:3] for key in _unpacked_get_values()}
        for key in ALLOWED:
            with self.subTest(site=key):
                self.assertIn(key, found, "allowlist entry no longer matches anything")

    def test_it_would_catch_a_new_one(self):
        import tempfile

        source = "import frappe\n\n\ndef f(name):\n    a, b = frappe.db.get_value('X', name, ['p', 'q'])\n    return a, b\n"
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.py"
            probe.write_text(source, encoding="utf-8")
            tree = ast.parse(probe.read_text(encoding="utf-8"))
            hits = [
                ast.unparse(n.targets[0])
                for n in ast.walk(tree)
                if isinstance(n, ast.Assign)
                and isinstance(n.targets[0], ast.Tuple)
                and isinstance(n.value, ast.Call)
            ]
            self.assertEqual(hits, ["(a, b)"])

    def test_the_guarded_spellings_do_not_register(self):
        """Both real fixes must actually remove the call from the scan, or the guard would
        keep firing after somebody fixed it and they would allowlist it instead."""
        for source in (
            "a, b = frappe.db.get_value('X', n, ['p', 'q']) or (None, None)\n",
            "row = frappe.db.get_value('X', n, ['p', 'q'], as_dict=True)\n",
        ):
            with self.subTest(source=source.strip()):
                tree = ast.parse(source)
                bare = [
                    n
                    for n in ast.walk(tree)
                    if isinstance(n, ast.Assign)
                    and isinstance(n.targets[0], ast.Tuple)
                    and isinstance(n.value, ast.Call)
                    and not any(kw.arg == "as_dict" for kw in n.value.keywords)
                ]
                self.assertEqual(bare, [])

    def test_it_is_wired_into_ci(self):
        ci = (APP.parent / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("test_get_value_unpacking", ci)


class TestTheNamesThisFileCitesAreReal(unittest.TestCase):
    """A docstring that names a function nobody can find sends the next reader to the
    wrong place, and nothing ever fails.

    The first version of this file named ``generate_predictive_maintenance``. There is no
    such function: it is ``generate_predictive_maintenance_records``, and
    ``scheduler_events`` reaches it through ``predictive_maintenance_scheduling``. Both the
    changelog entry and this docstring shipped with the invented name in v1.426.6, and
    review did not catch it — calling it on the running site did, with an AttributeError
    whose message suggested the real one.
    """

    CITED = ("generate_predictive_maintenance_records", "predictive_maintenance_scheduling")

    def test_both_cited_functions_exist(self):
        source = (APP / "tasks.py").read_text(encoding="utf-8")
        for name in self.CITED:
            with self.subTest(function=name):
                self.assertIn(f"def {name}(", source)

    def test_the_scheduler_really_reaches_the_fixed_function(self):
        """The claim being made is about where an exception LANDS, so the wiring is the
        claim. If the scheduler stopped calling it, the whole rationale would be stale."""
        hooks = (APP / "hooks.py").read_text(encoding="utf-8")
        self.assertIn("erpnext_enhancements.tasks.predictive_maintenance_scheduling", hooks)

        tasks = (APP / "tasks.py").read_text(encoding="utf-8")
        tree = ast.parse(tasks)
        entry = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "predictive_maintenance_scheduling"
        )
        called = {
            n.func.id
            for n in ast.walk(entry)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        self.assertIn("generate_predictive_maintenance_records", called)

    def test_this_docstring_does_not_use_the_invented_name(self):
        doc = __doc__ or ""
        self.assertIn("generate_predictive_maintenance_records", doc)
        self.assertIsNone(
            re.search(r"generate_predictive_maintenance(?!_records)", doc),
            "the invented name is back in this file's docstring",
        )


if __name__ == "__main__":
    unittest.main()
