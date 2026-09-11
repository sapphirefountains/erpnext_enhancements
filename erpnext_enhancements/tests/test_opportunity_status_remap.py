# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The v1.402.0 Opportunity status cleanup, pinned. Bench-free.

148 opportunities carried a `status` outside the field's own Select options — `Closed`
(144), `Prospecting` (3), `Value Proposition` (1) — which `_validate_selects` refuses to
save. They are retired to `Lost`.

**Everything asserted here is about HOW, not whether.** The mapping was a business call.
The mechanics were not, and each one below has a specific way of going wrong quietly:

* naming ``modified`` in the UPDATE drops all 144 rows into the 90-day window that
  ``snapshots.py`` uses for the lost leg of ``win_rate_90``, taking the reported win rate
  from 36.9% to 16.1% — with nothing in the diff to explain it later;
* reaching for ``doc.save()`` fires ~14 Opportunity handlers per row and throws in
  ``validate_close_reason``, which aborts ``bench migrate``, which on this repo is the
  deploy;
* repointing the four Dashboard Charts in a *later* release leaves "Opportunities Won"
  plotting zero rows instead of the wrong 144 — a regression dressed as a cleanup.

Note every absence assertion strips comments and docstrings first. The patch's own prose
names ``doc.save()``, ``modified`` and ``'Closed'`` many times over, precisely because it is
explaining why they are absent. This project has been bitten by that seven times.

Run: python -m unittest erpnext_enhancements.tests.test_opportunity_status_remap
"""

import ast
import io
import tokenize
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
PATCH = APP / "patches/remap_orphan_opportunity_statuses.py"
SNAPSHOTS = APP / "kpi_dashboards/snapshots.py"
VSP = APP / "kpi_dashboards/report/value_stream_performance/value_stream_performance.py"
PATCHES_TXT = APP / "patches.txt"


def _text(path):
    return path.read_text(encoding="utf-8")


def _tree(path):
    return ast.parse(_text(path))


def _code(path):
    """Source with comments and docstrings stripped."""
    kept = [
        t
        for t in tokenize.generate_tokens(io.StringIO(_text(path)).readline)
        if t.type != tokenize.COMMENT
    ]
    tree = ast.parse(tokenize.untokenize(kept))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body[0].value.value = ""
    return ast.unparse(tree)


def _const(path, name):
    """A module-level literal, read without importing (the patch imports frappe)."""
    for node in _tree(path).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path.name}")


def _sql_strings(path):
    """Every string constant in the module that looks like a SQL statement."""
    out = []
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "update `tab" in node.value.lower() or "select " in node.value.lower():
                out.append(node.value)
    return out


class TestTheMapping(unittest.TestCase):
    def test_all_three_retired_statuses_go_to_lost(self):
        """One rule, not two. The four Zoho stage names were folded in with the 144 so
        there is a single sentence to remember."""
        self.assertEqual(
            _const(PATCH, "RETIRED"),
            {"Closed": "Lost", "Prospecting": "Lost", "Value Proposition": "Lost"},
        )

    def test_the_targets_are_real_options(self):
        """Remapping onto another off-vocabulary value would leave the rows exactly as
        frozen as they started."""
        options = {
            "Qualification",
            "Needs Analysis",
            "Proposal/Price Quote",
            "Negotiation/Review",
            "Lost",
            "Closed Won",
            "On Hold",
        }
        for old, new in _const(PATCH, "RETIRED").items():
            with self.subTest(old=old):
                self.assertIn(new, options)
                self.assertNotIn(old, options)


class TestModifiedIsNeverTouched(unittest.TestCase):
    """The single most expensive thing this patch could get wrong.

    `snapshots.py` keys the lost leg of `win_rate_90` on `modified`, and these rows'
    `modified` is 13 months stale. Bumping it moves all 144 into the 90-day window and the
    headline win rate goes 36.9% -> 16.1% — with no failure, no error, and nothing in the
    diff that points at the cause.
    """

    def test_the_update_does_not_set_modified(self):
        updates = [s for s in _sql_strings(PATCH) if "update `tab" in s.lower()]
        self.assertTrue(updates, "the patch should carry a raw UPDATE")
        for sql in updates:
            with self.subTest(sql=sql[:40]):
                self.assertNotIn("modified", sql.lower())

    def test_every_set_value_call_passes_update_modified_false(self):
        """`frappe.db.set_value` bumps `modified` by DEFAULT. Asserted on parsed call
        nodes rather than on text, so a commented-out example cannot satisfy it."""
        found = 0
        for node in ast.walk(_tree(PATCH)):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Attribute) and node.func.attr == "set_value"):
                continue
            found += 1
            kwargs = {k.arg: k.value for k in node.keywords}
            self.assertIn("update_modified", kwargs, "set_value without update_modified")
            self.assertIs(ast.literal_eval(kwargs["update_modified"]), False)
        self.assertGreater(found, 0, "expected at least one set_value call to check")

    def test_the_patch_never_calls_save(self):
        """`doc.save()` always bumps `modified`, fires ~14 handlers per row, and throws in
        `validate_close_reason` — which aborts bench migrate, i.e. the deploy."""
        body = _code(PATCH)
        self.assertNotIn(".save(", body)
        self.assertNotIn("get_doc(", body)


class TestTheChartsShipWithIt(unittest.TestCase):
    """"Opportunities Won" filters status = "Closed", so it plots exactly the rows being
    retired. Fixing the data without it swaps a wrong chart for an empty one."""

    def test_the_misleading_chart_is_repointed(self):
        fixes = _const(PATCH, "CHART_FIXES")
        self.assertIn("Opportunities Won", fixes)
        expected, replacement = fixes["Opportunities Won"]
        self.assertIn("Closed", str(expected))
        self.assertIn("Closed Won", str(replacement))

    def test_no_fix_is_a_no_op(self):
        """A replacement identical to the expected value is a typo that reads as done."""
        for name, (expected, replacement) in _const(PATCH, "CHART_FIXES").items():
            with self.subTest(chart=name):
                self.assertNotEqual(expected, replacement)

    def test_no_replacement_targets_a_retired_status(self):
        retired = set(_const(PATCH, "RETIRED"))
        for name, (_expected, replacement) in _const(PATCH, "CHART_FIXES").items():
            for token in str(replacement).replace("'", '"').split('"'):
                if token in retired:
                    self.fail(f"{name} would be repointed at retired status {token!r}")

    def test_rewrites_are_guarded_on_the_current_value(self):
        """A chart somebody has since tuned by hand outranks this tidy-up."""
        body = _code(PATCH)
        self.assertIn("filters_json", body)
        self.assertIn("!= expected", body)


class TestTheTwoContradictingFilesAgreeNow(unittest.TestCase):
    """Before this release `value_stream_performance.py` documented "Closed" as neither won
    nor lost while `snapshots.py` counted it as lost. One of them had to change."""

    def test_snapshots_no_longer_queries_the_retired_status(self):
        """Asserted over string constants, not raw text — the comment above that query
        explains the removal and necessarily names the value it removed."""
        retired = set(_const(PATCH, "RETIRED"))
        for sql in _sql_strings(SNAPSHOTS):
            if "tabopportunity" not in sql.lower():
                continue
            for value in retired:
                with self.subTest(sql=sql[:50], value=value):
                    self.assertNotIn(f"'{value}'", sql)

    def test_snapshots_still_counts_lost(self):
        """Deleting the whole leg instead of the dead literal would silently make every
        loss vanish from the win rate."""
        joined = " ".join(_sql_strings(SNAPSHOTS)).lower()
        self.assertIn("'lost'", joined)

    def test_value_stream_buckets_are_unchanged_and_explained(self):
        self.assertEqual(_const(VSP, "WON_STATUSES"), ("Closed Won",))
        self.assertEqual(_const(VSP, "LOST_STATUSES"), ("Lost",))
        self.assertIn("remap_orphan_opportunity_statuses", _text(VSP))


class TestItActuallyRuns(unittest.TestCase):
    def test_registered_in_patches_txt(self):
        """An unregistered patch is a file, not a migration."""
        self.assertIn(
            "erpnext_enhancements.patches.remap_orphan_opportunity_statuses",
            _text(PATCHES_TXT),
        )

    def test_it_is_wired_into_ci(self):
        ci = (APP.parent / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("test_opportunity_status_remap", ci)

    def test_it_is_idempotent_by_construction(self):
        """Safe twice: the UPDATE is keyed on the retired value, so a second run matches
        nothing, and each chart rewrite compares before writing."""
        body = _code(PATCH)
        self.assertIn("if not before:", body)
        self.assertIn("continue", body)


if __name__ == "__main__":
    unittest.main()
