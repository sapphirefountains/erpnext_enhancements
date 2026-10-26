"""Bench-free tests for one row per number in a Contact's phone table (v1.745.1).

The bug: frappe v16.53.0's ``user.create_contact``, enqueued by every ``User.on_update``,
adds ``User.phone`` to the User's Contact when no row equals it as a raw string
(``user.py`` line 1500), and ``Contact.add_phone`` checks the database the same way. This app
stores every Contact Phone row as ``+1…`` (``utils/normalize_hook.py``) and leaves
``User.phone`` as typed, so each User save appended another copy, and each deploy saved the
Users holding a fixture Role Profile. Two staff Contacts reached 394 and 254 rows of one number.

``TestFrappeUserContactSync`` replays that sync against a Contact saved through the
``before_validate`` handlers ``hooks.py`` actually wires — ``doc_events["Contact"]`` and then
``doc_events["*"]``, the order v16's ``Document.hook`` composes them — so it fails when the
guard is missing from ``hooks.py`` as well as when the guard is wrong. The rest covers the
pure rule, the doc_event's contract (never raises, never logs the number) and the one-shot
cleanup ``patches/dedupe_contact_phone_rows`` against an in-memory ``tabContact Phone``.

A minimal ``frappe`` stub is installed in ``setUpModule`` and removed afterwards.

Run: python -m unittest erpnext_enhancements.tests.test_contact_phone_dedupe -v
"""

import ast
import contextlib
import copy
import importlib
import io
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
GUARD_MODULE = "erpnext_enhancements.utils.contact_phones"
HOOK_MODULE = "erpnext_enhancements.utils.normalize_hook"
PATCH_MODULE = "erpnext_enhancements.patches.dedupe_contact_phone_rows"
GUARD = f"{GUARD_MODULE}.dedupe_contact_phones"

STORED = "+18015550100"
RAW = "801-555-0100"
OTHER = "+18015550199"

_saved = {}
guard = None
patch = None


class _Dict(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class FakeRow:
    """A Contact Phone child Document: attribute access plus ``get``/``set``."""

    doctype = "Contact Phone"
    parentfield = "phone_nos"
    docstatus = 0

    def __init__(self, phone, is_primary_phone=0, is_primary_mobile_no=0, name=None, idx=None):
        self.phone = phone
        self.is_primary_phone = is_primary_phone
        self.is_primary_mobile_no = is_primary_mobile_no
        self.name = name
        self.idx = idx

    def get(self, key, default=None):
        return getattr(self, key, default)

    def set(self, key, value):
        setattr(self, key, value)


class FakeContact:
    """Just enough of a Contact Document for both before_validate handlers."""

    doctype = "Contact"
    docstatus = 0
    _action = "save"

    def __init__(self, rows=()):
        self.name = "CONTACT-0001"
        self.phone_nos = []
        for row in rows:
            self.append("phone_nos", row)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def set(self, key, value):
        setattr(self, key, value)

    def append(self, key, values):
        row = values if isinstance(values, FakeRow) else FakeRow(**values)
        row.idx = len(getattr(self, key)) + 1
        getattr(self, key).append(row)
        return row

    def remove(self, row):
        # frappe v16 Document.remove: take the row out, then renumber idx.
        table = self.get(row.parentfield)
        table.remove(row)
        for i, other in enumerate(table):
            other.idx = i + 1

    def get_all_children(self):
        return list(self.phone_nos)


def _install_frappe():
    frappe = types.ModuleType("frappe")
    frappe._dict = _Dict
    frappe.conf = _Dict()
    frappe.flags = _Dict()
    frappe.logged = []
    frappe.cleared = []
    frappe.log_error = lambda *a, **k: frappe.logged.append((a, k))
    frappe.get_traceback = lambda *a, **k: "Traceback (stub)"
    frappe.clear_document_cache = lambda doctype, name=None: frappe.cleared.append((doctype, name))
    frappe.db = types.SimpleNamespace()
    sys.modules["frappe"] = frappe
    return frappe


def setUpModule():
    global guard, patch
    for name in ("frappe", GUARD_MODULE, HOOK_MODULE, PATCH_MODULE):
        _saved[name] = sys.modules.get(name)
        sys.modules.pop(name, None)
    _install_frappe()
    guard = importlib.import_module(GUARD_MODULE)
    patch = importlib.import_module(PATCH_MODULE)


def tearDownModule():
    for name, module in _saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def _frappe():
    return sys.modules["frappe"]


def _hooks_doc_events():
    tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == "doc_events":
                return ast.literal_eval(node.value)
    raise AssertionError("hooks.py has no doc_events")


def _as_list(value):
    if not value:
        return []
    return [value] if isinstance(value, str) else list(value)


def _handlers(dotted_paths):
    out = []
    for path in dotted_paths:
        module, _, attr = path.rpartition(".")
        out.append(getattr(importlib.import_module(module), attr))
    return out


def wired_before_validate(doctype):
    """The handlers hooks.py wires, in frappe v16's order: the doctype's, then ``*``'s."""
    events = _hooks_doc_events()
    return _handlers(
        _as_list(events.get(doctype, {}).get("before_validate"))
        + _as_list(events.get("*", {}).get("before_validate"))
    )


class ContactTable:
    """``tabContact Phone`` for one Contact, and the parts of frappe that read or write it."""

    def __init__(self, rows, handlers):
        self.rows = [dict(row, name=f"row{i}") for i, row in enumerate(rows)]
        self.handlers = handlers
        self._next = len(self.rows)

    def load(self):
        """frappe.get_doc("Contact", ...): a fresh Document from the stored rows."""
        return FakeContact(FakeRow(**row) for row in self.rows)

    def save(self, contact):
        """What Document.save does that matters here: before_validate, Contact.set_primary's
        refusal of two primaries, then every listed row stored and every other one deleted."""
        for handler in self.handlers:
            handler(contact, "before_validate")
        for flag in guard.PRIMARY_FLAGS:
            primaries = [row for row in contact.phone_nos if guard.is_set(row.get(flag))]
            if len(primaries) > 1:
                raise AssertionError(f"Only one {flag} can be set as primary.")
        for row in contact.phone_nos:
            if not row.name:
                row.name = f"row{self._next}"
                self._next += 1
        self.rows = [
            {
                "phone": row.phone,
                "is_primary_phone": int(guard.is_set(row.is_primary_phone)),
                "is_primary_mobile_no": int(guard.is_set(row.is_primary_mobile_no)),
            }
            for row in contact.phone_nos
        ]

    def frappe_create_contact(self, user_phone):
        """frappe v16.53.0 ``user.create_contact`` for a User whose Contact exists (user.py lines
        1493-1521), with ``Contact.add_phone`` (contact.py lines 161-170). Raw comparisons both."""
        contact = self.load()
        if user_phone and not any(row.phone == user_phone for row in contact.phone_nos):
            if not any(row["phone"] == user_phone for row in self.rows):  # add_phone's db.exists
                contact.append(
                    "phone_nos",
                    {
                        "phone": user_phone,
                        "is_primary_phone": int(not any(row.is_primary_phone == 1 for row in contact.phone_nos)),
                    },
                )
        self.save(contact)


class _Reset(unittest.TestCase):
    def setUp(self):
        frappe = _frappe()
        frappe.conf = _Dict()
        frappe.flags = _Dict()
        frappe.logged = []
        frappe.cleared = []


class TestFrappeUserContactSync(_Reset):
    """The production bug, end to end: a User saved with a raw phone, twice."""

    def test_saving_twice_with_a_raw_number_leaves_one_row(self):
        table = ContactTable([{"phone": STORED, "is_primary_phone": 1}], wired_before_validate("Contact"))
        table.frappe_create_contact(RAW)
        table.frappe_create_contact(RAW)
        self.assertEqual(table.rows, [{"phone": STORED, "is_primary_phone": 1, "is_primary_mobile_no": 0}])

    def test_every_format_of_the_number_is_the_same_number(self):
        table = ContactTable([{"phone": STORED, "is_primary_phone": 1}], wired_before_validate("Contact"))
        for typed in (RAW, "(801) 555-0100", "801.555.0100", "1-801-555-0100", "+1 801 555 0100", "8015550100"):
            table.frappe_create_contact(typed)
        self.assertEqual(len(table.rows), 1)

    def test_a_raw_row_from_before_normalization_is_kept_once(self):
        # A Contact last saved before v1.618.0 still holds the raw row; the next save normalizes it.
        table = ContactTable([{"phone": RAW, "is_primary_phone": 1}], wired_before_validate("Contact"))
        table.frappe_create_contact(RAW)
        table.frappe_create_contact(RAW)
        self.assertEqual(table.rows, [{"phone": STORED, "is_primary_phone": 1, "is_primary_mobile_no": 0}])

    def test_a_new_number_is_still_added(self):
        table = ContactTable([{"phone": STORED, "is_primary_phone": 1}], wired_before_validate("Contact"))
        table.frappe_create_contact("801-555-0199")
        self.assertEqual([row["phone"] for row in table.rows], [STORED, OTHER])
        self.assertEqual([row["is_primary_phone"] for row in table.rows], [1, 0])

    def test_without_the_guard_the_sync_appends_a_copy_every_time(self):
        """The replay reproduces production, so the test above is not vacuous."""
        table = ContactTable([{"phone": STORED, "is_primary_phone": 1}], _handlers([f"{HOOK_MODULE}.normalize_doc"]))
        for _ in range(3):
            table.frappe_create_contact(RAW)
        self.assertEqual([row["phone"] for row in table.rows], [STORED] * 4)

    def test_a_new_contact_whose_phone_is_its_mobile_gets_one_row_with_both_flags(self):
        # create_contact's insert path adds User.phone and User.mobile_no as two rows.
        table = ContactTable([], wired_before_validate("Contact"))
        contact = table.load()
        contact.append("phone_nos", {"phone": RAW, "is_primary_phone": 1})
        contact.append("phone_nos", {"phone": "(801) 555-0100", "is_primary_mobile_no": 1})
        table.save(contact)
        self.assertEqual(table.rows, [{"phone": STORED, "is_primary_phone": 1, "is_primary_mobile_no": 1}])


class TestTheRule(unittest.TestCase):
    def test_phone_key_is_the_stored_form_or_the_trimmed_text(self):
        self.assertEqual(guard.phone_key("(801) 555-0100"), STORED)
        self.assertEqual(guard.phone_key("801-555-0100 x12"), "+18015550100,12")
        self.assertEqual(guard.phone_key(" 555-0100 "), "555-0100")  # no area code: as typed, trimmed
        self.assertEqual(guard.phone_key(None), "")
        self.assertEqual(guard.phone_key("   "), "")

    def test_the_first_row_of_each_number_is_kept(self):
        rows = [
            FakeRow(OTHER, name="a"),
            FakeRow(RAW, name="b"),
            FakeRow("801-555-0199", name="c"),
            FakeRow(STORED, name="d"),
            FakeRow(STORED + ",12", name="e"),  # an extension is another number
        ]
        groups = guard.duplicate_groups(rows)
        self.assertEqual([(kept.name, [row.name for row in dropped]) for kept, dropped in groups], [("a", ["c"]), ("b", ["d"])])

    def test_empty_and_distinct_rows_are_never_grouped(self):
        rows = [FakeRow("", name="a"), FakeRow(None, name="b"), FakeRow(STORED, name="c"), FakeRow(OTHER, name="d")]
        self.assertEqual(guard.duplicate_groups(rows), [])

    def test_unreadable_values_collapse_only_when_they_read_the_same(self):
        rows = [FakeRow("555-0100", name="a"), FakeRow(" 555-0100", name="b"), FakeRow("5550100", name="c")]
        groups = guard.duplicate_groups(rows)
        self.assertEqual([(kept.name, [row.name for row in dropped]) for kept, dropped in groups], [("a", ["b"])])

    def test_flags_fold_onto_the_kept_row(self):
        kept = FakeRow(STORED)
        dropped = [FakeRow(RAW, is_primary_phone="1"), FakeRow(RAW, is_primary_mobile_no=True)]
        self.assertEqual(guard.merged_flags(kept, dropped), {"is_primary_phone": 1, "is_primary_mobile_no": 1})
        self.assertEqual(
            guard.merged_flags(FakeRow(STORED, is_primary_phone=1), [FakeRow(RAW)]),
            {"is_primary_phone": 1, "is_primary_mobile_no": 0},
        )

    def test_is_set_reads_every_stored_shape(self):
        for value in (1, "1", True):
            self.assertTrue(guard.is_set(value))
        for value in (0, "0", None, "", False, "x"):
            self.assertFalse(guard.is_set(value))


class TestTheDocEvent(_Reset):
    def test_a_contact_with_distinct_numbers_is_untouched(self):
        contact = FakeContact([FakeRow(STORED, is_primary_phone=1, name="a"), FakeRow(OTHER, is_primary_mobile_no=1, name="b")])
        before = [(row.name, row.idx, row.phone, row.is_primary_phone, row.is_primary_mobile_no) for row in contact.phone_nos]
        guard.dedupe_contact_phones(contact, "before_validate")
        after = [(row.name, row.idx, row.phone, row.is_primary_phone, row.is_primary_mobile_no) for row in contact.phone_nos]
        self.assertEqual(before, after)

    def test_a_primary_copy_passes_its_flag_and_idx_is_renumbered(self):
        contact = FakeContact(
            [
                FakeRow(OTHER, name="a"),
                FakeRow(STORED, name="b"),
                FakeRow(RAW, is_primary_phone=1, name="c"),
                FakeRow("801-555-0123", name="d"),
            ]
        )
        guard.dedupe_contact_phones(contact)
        self.assertEqual([(row.name, row.idx) for row in contact.phone_nos], [("a", 1), ("b", 2), ("d", 3)])
        self.assertEqual(contact.phone_nos[1].is_primary_phone, 1)

    def test_it_runs_with_the_kill_switch_on(self):
        _frappe().conf = _Dict(disable_value_normalization=1)
        contact = FakeContact([FakeRow(STORED, name="a"), FakeRow(RAW)])
        guard.dedupe_contact_phones(contact)
        self.assertEqual(len(contact.phone_nos), 1)

    def test_it_never_raises_and_never_logs_the_number(self):
        class Broken(FakeContact):
            def remove(self, row):
                raise RuntimeError(f"cannot remove {row.phone}")

        guard.dedupe_contact_phones(Broken([FakeRow(STORED, name="a"), FakeRow(RAW)]))
        self.assertEqual(len(_frappe().logged), 1)
        self.assertNotIn("555", repr(_frappe().logged))
        # The Contact is named, so a logged skip can be traced to its record.
        self.assertIn("Contact CONTACT-0001:", _frappe().logged[0][1]["message"])

    @staticmethod
    def _two_repeated_numbers(row_class=FakeRow, contact_class=FakeContact):
        # STORED is held by a and b (b is the primary phone); OTHER by c and d (d the primary
        # mobile). A clean run keeps a and c and moves the flags onto them.
        return contact_class(
            [
                row_class(STORED, name="a"),
                FakeRow(RAW, is_primary_phone=1, name="b"),
                FakeRow(OTHER, name="c"),
                FakeRow("801.555.0199", is_primary_mobile_no=1, name="d"),
            ]
        )

    @staticmethod
    def _snapshot(contact):
        return [(row.name, row.idx, row.phone, row.is_primary_phone, row.is_primary_mobile_no) for row in contact.phone_nos]

    def _assert_left_as_it_was(self, contact, before, table):
        self.assertIs(contact.phone_nos, table)  # the same list, restored in place
        self.assertEqual(self._snapshot(contact), before)
        for flag in guard.PRIMARY_FLAGS:
            self.assertLessEqual(sum(guard.is_set(row.get(flag)) for row in contact.phone_nos), 1)
        self.assertEqual(len(_frappe().logged), 1)
        self.assertIn("Contact CONTACT-0001:", _frappe().logged[0][1]["message"])

    def test_a_clean_run_folds_both_flags(self):
        contact = self._two_repeated_numbers()
        guard.dedupe_contact_phones(contact)
        self.assertEqual(self._snapshot(contact), [("a", 1, STORED, 1, 0), ("c", 2, OTHER, 0, 1)])

    def test_a_removal_failing_partway_leaves_rows_and_flags_exactly_as_they_were(self):
        class SecondRemoveFails(FakeContact):
            removes = 0

            def remove(self, row):
                self.removes += 1
                if self.removes == 2:
                    raise RuntimeError("remove refused")
                super().remove(row)

        contact = self._two_repeated_numbers(contact_class=SecondRemoveFails)
        before, table = self._snapshot(contact), contact.phone_nos
        guard.dedupe_contact_phones(contact)
        self.assertEqual(contact.removes, 2)  # the first copy really was removed, then put back
        self._assert_left_as_it_was(contact, before, table)

    def test_a_flag_failing_after_the_removals_leaves_rows_and_flags_exactly_as_they_were(self):
        class FirstSetFails(FakeRow):
            sets = 0

            def set(self, key, value):
                self.sets += 1
                if self.sets == 1:
                    raise RuntimeError("set refused")
                super().set(key, value)

        contact = self._two_repeated_numbers(row_class=FirstSetFails)
        before, table = self._snapshot(contact), contact.phone_nos
        guard.dedupe_contact_phones(contact)
        self._assert_left_as_it_was(contact, before, table)

    def test_flags_are_set_only_after_every_copy_is_removed(self):
        events = []

        class Recording(FakeContact):
            def remove(self, row):
                events.append(("remove", row.name))
                super().remove(row)

        class RecordingRow(FakeRow):
            def set(self, key, value):
                events.append(("set", self.name))
                super().set(key, value)

        contact = self._two_repeated_numbers(row_class=RecordingRow, contact_class=Recording)
        guard.dedupe_contact_phones(contact)
        removals = [i for i, event in enumerate(events) if event[0] == "remove"]
        sets = [i for i, event in enumerate(events) if event[0] == "set"]
        self.assertEqual(len(removals), 2)
        self.assertTrue(sets and min(sets) > max(removals))

    def test_a_failing_log_error_is_swallowed_too(self):
        def boom(*a, **k):
            raise RuntimeError("log down")

        _frappe().log_error = boom
        try:
            guard.dedupe_contact_phones(types.SimpleNamespace(get=None))
        finally:
            _frappe().log_error = lambda *a, **k: _frappe().logged.append((a, k))


class FakeDB:
    """``tabContact Phone`` in memory, answering the three queries the patch sends."""

    def __init__(self, rows):
        self.rows = [
            dict({"parenttype": "Contact", "parentfield": "phone_nos", "is_primary_phone": 0, "is_primary_mobile_no": 0}, **row)
            for row in rows
        ]
        self.writes = []
        self.fail_delete_for = None
        self._savepoints = {}

    def _phone_rows(self):
        return [r for r in self.rows if r["parenttype"] == "Contact" and r["parentfield"] == "phone_nos"]

    def sql(self, query, params=None, as_dict=False):
        q = " ".join(query.split())
        if q.startswith("select count(*)"):
            return [(len(self._phone_rows()),)]
        if "group by parent having count(*) > 1" in q:
            counts = {}
            for r in self._phone_rows():
                counts[r["parent"]] = counts.get(r["parent"], 0) + 1
            return [(parent,) for parent, n in counts.items() if n > 1]
        if q.startswith("select name, idx, phone, is_primary_phone, is_primary_mobile_no"):
            parent = params[1]
            mine = sorted((r for r in self._phone_rows() if r["parent"] == parent), key=lambda r: (r["idx"], r["name"]))
            return [
                _Dict({k: r[k] for k in ("name", "idx", "phone", "is_primary_phone", "is_primary_mobile_no")})
                for r in mine
            ]
        raise AssertionError(f"unexpected query: {q}")

    def set_value(self, doctype, name, field, value=None, update_modified=True):
        assert doctype == "Contact Phone" and update_modified is False
        values = field if isinstance(field, dict) else {field: value}
        self.writes.append(("set_value", name, dict(values)))
        for r in self.rows:
            if r["name"] == name:
                r.update(values)

    def delete(self, doctype, filters):
        assert doctype == "Contact Phone"
        op, names = filters["name"]
        assert op == "in"
        if self.fail_delete_for and any(r["parent"] == self.fail_delete_for for r in self.rows if r["name"] in names):
            raise RuntimeError("delete refused")
        self.writes.append(("delete", sorted(names)))
        self.rows = [r for r in self.rows if r["name"] not in names]

    def savepoint(self, name):
        self._savepoints[name] = copy.deepcopy(self.rows)

    def rollback(self, save_point=None):
        self.rows = copy.deepcopy(self._savepoints[save_point])

    def of(self, parent):
        return sorted((r for r in self.rows if r["parent"] == parent), key=lambda r: r["idx"])


def _run_patch(db):
    _frappe().db = db
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = patch.dedupe_contact_phone_rows()
    return result, out.getvalue()


def _staff_and_customers():
    rows = [
        # Staff contact A: five copies of one number; the primary is the third.
        *({"name": f"a{i}", "parent": "A", "idx": i + 1, "phone": STORED} for i in range(5)),
        # Staff contact B: two numbers, each repeated, in mixed formats.
        {"name": "b0", "parent": "B", "idx": 1, "phone": STORED, "is_primary_phone": 1},
        {"name": "b1", "parent": "B", "idx": 2, "phone": OTHER},
        {"name": "b2", "parent": "B", "idx": 3, "phone": RAW},
        {"name": "b3", "parent": "B", "idx": 4, "phone": "801.555.0199", "is_primary_mobile_no": 1},
        # Customer C: two different numbers, untouched. Customer D: one row.
        {"name": "c0", "parent": "C", "idx": 1, "phone": STORED, "is_primary_phone": 1},
        {"name": "c1", "parent": "C", "idx": 2, "phone": OTHER, "is_primary_mobile_no": 1},
        {"name": "d0", "parent": "D", "idx": 1, "phone": OTHER, "is_primary_phone": 1},
    ]
    rows[2]["is_primary_phone"] = 1
    return rows


class TestCleanupPatch(_Reset):
    def test_one_row_per_number_with_the_primary_flags_kept(self):
        db = FakeDB(_staff_and_customers())
        result, printed = _run_patch(db)
        self.assertEqual(
            [(r["name"], r["idx"], r["is_primary_phone"], r["is_primary_mobile_no"]) for r in db.of("A")], [("a0", 1, 1, 0)]
        )
        self.assertEqual(
            [(r["name"], r["idx"], r["is_primary_phone"], r["is_primary_mobile_no"]) for r in db.of("B")],
            [("b0", 1, 1, 0), ("b1", 2, 0, 1)],
        )
        self.assertEqual(result, {"before": 12, "after": 6, "contacts": 2, "removed": 6, "failed": []})
        self.assertIn("Contact Phone rows 12 -> 6; removed 6 repeated rows from 2 Contacts", printed)
        self.assertEqual(sorted(_frappe().cleared), [("Contact", "A"), ("Contact", "B")])

    def test_contacts_without_a_repeated_number_are_not_written(self):
        db = FakeDB(_staff_and_customers())
        before = {p: copy.deepcopy(db.of(p)) for p in ("C", "D")}
        _run_patch(db)
        self.assertEqual({p: db.of(p) for p in ("C", "D")}, before)
        touched = {name for write in db.writes for name in ([write[1]] if write[0] == "set_value" else write[1])}
        self.assertFalse(touched & {"c0", "c1", "d0"})

    def test_safe_twice(self):
        db = FakeDB(_staff_and_customers())
        _run_patch(db)
        db.writes = []
        result, printed = _run_patch(db)
        self.assertEqual(db.writes, [])
        self.assertEqual(result["removed"], 0)
        self.assertIn("Contact Phone rows 6 -> 6", printed)

    def test_a_failing_contact_is_rolled_back_whole_and_the_rest_go_on(self):
        db = FakeDB(_staff_and_customers())
        db.fail_delete_for = "A"
        untouched = copy.deepcopy(db.of("A"))
        result, _printed = _run_patch(db)
        # The flag write on the kept row happened before the refused delete; the savepoint undoes it.
        self.assertEqual(db.of("A"), untouched)
        self.assertEqual(len(db.of("B")), 2)
        self.assertEqual(result["failed"], ["A: RuntimeError"])
        self.assertEqual(len(_frappe().logged), 1)

    def test_execute_never_raises(self):
        def broken(*a, **k):
            raise RuntimeError("database gone")

        _frappe().db = types.SimpleNamespace(sql=broken)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            patch.execute()
        self.assertIn("aborted early", out.getvalue())
        self.assertEqual(len(_frappe().logged), 1)


class TestWiring(unittest.TestCase):
    def test_hooks_wires_the_guard_on_contact_before_validate(self):
        self.assertIn(GUARD, _as_list(_hooks_doc_events()["Contact"].get("before_validate")))

    def test_patches_txt_runs_the_cleanup_after_model_sync(self):
        lines = [line.strip() for line in (APP / "patches.txt").read_text(encoding="utf-8").splitlines()]
        entry = "erpnext_enhancements.patches.dedupe_contact_phone_rows"
        self.assertIn(entry, lines)
        self.assertGreater(lines.index(entry), lines.index("[post_model_sync]"))
