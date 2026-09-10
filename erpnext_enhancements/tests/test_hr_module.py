"""The HR Enhancements module: is it reachable, and does the ladder mean what it says?

Two very different kinds of assertion live here, and the first kind is the reason
the file exists.

**Reachability.** A Frappe workspace does not 403 when you cannot see it — it
silently is not there. `Workspace.__init__` raises `PermissionError` when
`doc.module not in allowed_modules` (frappe `origin/version-16:frappe/desk/desktop.py:38-44`)
and `get_workspace_sidebar_items` swallows that exception at `:421`. `allowed_modules`
is built **only** from DocPerms on non-child doctypes (`frappe/utils/user.py`), and
the `Workspace Manager` role bypasses the gate entirely — so whoever builds the
module cannot see the failure, and the person it was built for gets a blank sidebar
and a desk tile that routes into an error. That is exactly what happened to the one
person running HR on this site, for every release between v1.215.0 and v1.386.0.

`Position` is what holds the door open: it grants `read` to `Employee`, a role every
staff account carries. If that grant is ever dropped, the whole area disappears for
everyone who is not a manager and nothing anywhere reports it. `test_the_module_stays_reachable`
is that tripwire.

**The ladder.** `outranks()` is read by the sign-off endpoint, the controller's
`before_submit`, the row-level visibility filters and the sign-off queue, so it is
worth pinning behaviourally rather than by reading the source. Its three clauses are
each a real failure: cross-family would let a Senior Designer attest for a Junior
Technician; same-tier would let two Junior Technicians sign each other's basin
course; and a retired rung must outrank nobody.

The third kind of check here is cheap and catches a classic: a workspace `content`
blob naming a shortcut or card that does not exist renders as a blank space with no
error at all.

Run: python -m unittest erpnext_enhancements.tests.test_hr_module
"""

import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP = Path(__file__).resolve().parents[1]
MODULE_DIR = APP / "hr_enhancements"
POSITION_JSON = MODULE_DIR / "doctype/position/position.json"
WORKSPACE = MODULE_DIR / "workspace/hr/hr.json"
SIDEBAR = APP / "workspace_sidebar/hr.json"

MODULE_NAME = "HR Enhancements"

position = None


# ------------------------------------------------------------------ frappe stub


class _FakeDB:
    """Just enough of `frappe.db` for the ladder predicate.

    Installed at execution time in `setUpModule`, never at import, so it cannot
    fool a bench-only suite's `import frappe` skip-guard.
    """

    def __init__(self):
        self.positions = {}

    def get_value(self, doctype, name, fields, as_dict=False):
        row = self.positions.get(name)
        if not row:
            return None
        if as_dict:
            return types.SimpleNamespace(**{f: row.get(f) for f in fields})
        if isinstance(fields, list):
            return [row.get(f) for f in fields]
        return row.get(fields)

    def exists(self, doctype, name=None):
        return name in self.positions


class _FakeFrappe(types.ModuleType):
    pass


def _install_frappe_stub():
    fake = _FakeFrappe("frappe")
    fake.db = _FakeDB()

    def get_all(doctype, filters=None, pluck=None, **kwargs):
        rows = list(fake.db.positions.values())
        for field, cond in (filters or {}).items():
            if isinstance(cond, list):
                op, value = cond
                if op == "<":
                    rows = [r for r in rows if (r.get(field) or 0) < value]
                elif op == ">":
                    rows = [r for r in rows if (r.get(field) or 0) > value]
                else:
                    raise AssertionError(f"stub does not implement operator {op!r}")
            else:
                rows = [r for r in rows if r.get(field) == cond]
        return [r["name"] for r in rows] if pluck else rows

    fake.get_all = get_all
    fake.throw = lambda *a, **k: (_ for _ in ()).throw(AssertionError(a[0] if a else "throw"))
    fake._ = lambda text: text
    fake.get_doc = lambda *a, **k: None
    sys.modules["frappe"] = fake

    utils = types.ModuleType("frappe.utils")
    utils.cint = lambda v: int(v or 0)
    sys.modules["frappe.utils"] = utils
    fake.utils = utils

    nestedset = types.ModuleType("frappe.utils.nestedset")

    class NestedSet:
        pass

    nestedset.NestedSet = NestedSet
    sys.modules["frappe.utils.nestedset"] = nestedset
    utils.nestedset = nestedset

    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")

    class Document:
        pass

    document.Document = Document
    sys.modules["frappe.model"] = model
    sys.modules["frappe.model.document"] = document
    return fake


def setUpModule():
    global position
    fake = _install_frappe_stub()
    from erpnext_enhancements.hr_enhancements.doctype.position import position as module

    position = module
    position.frappe = fake


def _place(name, family, tier, is_active=1, is_group=0):
    sys.modules["frappe"].db.positions[name] = {
        "name": name,
        "job_family": family,
        "tier": tier,
        "is_active": is_active,
        "is_group": is_group,
    }


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ reachability


class TestTheModuleStaysReachable(unittest.TestCase):
    def test_the_module_is_registered_and_its_directory_matches(self):
        registered = {
            line.strip()
            for line in (APP / "modules.txt").read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        self.assertIn(MODULE_NAME, registered)
        self.assertTrue(MODULE_DIR.is_dir(), "the module directory must be scrub(module)")

    def test_the_module_is_not_called_hr(self):
        """`hrms` ships a module literally called `HR`. Claiming it is the Plaid
        Settings collision again — our record overwrites the native one through a
        newer `modified` and breaks both."""
        registered = (APP / "modules.txt").read_text(encoding="utf-8").splitlines()
        self.assertNotIn("HR", [line.strip() for line in registered])

    def test_the_module_owns_a_readable_non_child_doctype(self):
        """**The tripwire.** Without this the whole area silently vanishes for
        anyone who is not a Workspace Manager, and nothing reports it."""
        everyone = {"Employee", "All", "HR User"}
        openers = []
        for path in sorted(MODULE_DIR.glob("doctype/*/*.json")):
            data = _read(path)
            if data.get("doctype") != "DocType" or data.get("istable"):
                continue
            for perm in data.get("permissions", []):
                if perm.get("read") and perm.get("role") in everyone:
                    openers.append(f"{data['name']} -> {perm['role']}")
        self.assertTrue(
            openers,
            "no non-child DocType in HR Enhancements grants read to a role ordinary "
            "staff hold, so `allow_modules` will not contain the module and the HR "
            "workspace will be silently absent from every non-manager's sidebar",
        )

    def test_the_workspace_is_owned_by_this_module(self):
        """A workspace declaring a module nobody can read is the same failure with
        an extra step."""
        self.assertEqual(_read(WORKSPACE)["module"], MODULE_NAME)

    def test_the_workspace_is_public_and_visible(self):
        ws = _read(WORKSPACE)
        self.assertEqual(ws["public"], 1)
        self.assertEqual(ws["is_hidden"], 0)

    def test_the_desk_tile_exists_with_artwork(self):
        from erpnext_enhancements.setup.desktop_icon_map import TILES

        self.assertIn("HR", TILES)
        slug = TILES["HR"][0]
        self.assertTrue(
            (APP / f"public/desktop_icons/{slug}.svg").exists(),
            "the HR tile has no generated artwork; run scripts/build_desktop_icons.py",
        )

    def test_the_sidebar_opens_on_the_workspace(self):
        """The desk tile routes to the sidebar's FIRST Link item, so row order here
        decides where clicking the tile lands. A doctype list is the wrong answer."""
        items = _read(SIDEBAR)["items"]
        first = next(i for i in items if i.get("type") == "Link")
        self.assertEqual(first["link_type"], "Workspace")
        self.assertEqual(first["link_to"], _read(WORKSPACE)["name"])

    def test_the_sidebar_is_newer_than_the_orphan_it_replaces(self):
        """Workspace Sidebar is TIMESTAMP-gated on import, unlike a DocType (which is
        hash-gated). Prod carries an orphaned `standard = 0` "HR" sidebar from
        2026-02-08 pointing at a workspace that does not exist; a file older than
        that would never import and nobody would be told."""
        self.assertGreater(_read(SIDEBAR)["modified"], "2026-02-08 10:52:20.227777")


class TestTheWorkspaceHasNoDeadEnds(unittest.TestCase):
    """A `content` blob naming a shortcut or card that does not exist renders as a
    blank space, with no error anywhere."""

    def setUp(self):
        self.ws = _read(WORKSPACE)
        self.content = json.loads(self.ws["content"])

    def test_every_referenced_shortcut_exists(self):
        declared = {s["label"] for s in self.ws["shortcuts"]}
        used = {
            b["data"]["shortcut_name"] for b in self.content if b.get("type") == "shortcut"
        }
        self.assertEqual(sorted(used - declared), [])

    def test_every_referenced_card_exists(self):
        declared = {
            link["label"] for link in self.ws["links"] if link.get("type") == "Card Break"
        }
        used = {b["data"]["card_name"] for b in self.content if b.get("type") == "card"}
        self.assertEqual(sorted(used - declared), [])

    def test_every_shortcut_is_placed_on_the_page(self):
        """The other direction. A declared shortcut absent from `content` is invisible
        — which is how a workspace ends up looking half-built."""
        declared = {s["label"] for s in self.ws["shortcuts"]}
        used = {
            b["data"]["shortcut_name"] for b in self.content if b.get("type") == "shortcut"
        }
        self.assertEqual(sorted(declared - used), [])

    def test_card_break_counts_match_their_links(self):
        """`link_count` on a Card Break is how many of the following Links belong to
        it. Wrong and the card renders the wrong rows."""
        links = self.ws["links"]
        i = 0
        while i < len(links):
            row = links[i]
            if row.get("type") != "Card Break":
                i += 1
                continue
            declared = row.get("link_count", 0)
            actual = 0
            j = i + 1
            while j < len(links) and links[j].get("type") == "Link":
                actual += 1
                j += 1
            self.assertEqual(
                actual, declared, f"card {row['label']!r} declares {declared} links, has {actual}"
            )
            i = j

    def test_the_learner_surface_is_a_url_not_a_doctype(self):
        """`/training` is a website page and must stay one — Training Learner has
        `desk_access = 0` because customer contacts hold it, and desk access would
        move the licensed-user count. Workspace Links cannot be URLs, so this has to
        be a shortcut."""
        urls = [s for s in self.ws["shortcuts"] if s.get("type") == "URL"]
        self.assertIn("/training", [s.get("url") for s in urls])


# ------------------------------------------------------------------ the ladder


class TestOutranks(unittest.TestCase):
    def setUp(self):
        sys.modules["frappe"].db.positions.clear()
        _place("Junior Technician", "Technician", 1)
        _place("Senior Technician", "Technician", 2)
        _place("Master Technician", "Technician", 3)
        _place("Junior Designer", "Design", 1)
        _place("Senior Designer", "Design", 2)
        _place("Technician", "Technician", 0, is_group=1)
        _place("Retired Rung", "Technician", 2, is_active=0)

    def test_higher_tier_outranks_lower_on_the_same_ladder(self):
        self.assertTrue(position.outranks("Senior Technician", "Junior Technician"))
        self.assertTrue(position.outranks("Master Technician", "Senior Technician"))
        self.assertTrue(position.outranks("Master Technician", "Junior Technician"))

    def test_lower_never_outranks_higher(self):
        self.assertFalse(position.outranks("Junior Technician", "Senior Technician"))

    def test_a_peer_never_outranks_a_peer(self):
        """Same tier is the one that matters most: without it two Junior Technicians
        sign each other's basin course and the gate attests to nothing."""
        _place("Junior Technician B", "Technician", 1)
        self.assertFalse(position.outranks("Junior Technician", "Junior Technician B"))

    def test_nobody_outranks_themselves(self):
        self.assertFalse(position.outranks("Senior Technician", "Senior Technician"))

    def test_authority_does_not_cross_job_families(self):
        """A Senior Designer has no standing over a Junior Technician, whatever the
        integers say. This is why `tier` alone is never the predicate."""
        self.assertFalse(position.outranks("Senior Designer", "Junior Technician"))
        self.assertFalse(position.outranks("Master Technician", "Junior Designer"))

    def test_a_retired_rung_outranks_nobody(self):
        self.assertFalse(position.outranks("Retired Rung", "Junior Technician"))

    def test_a_retired_learner_position_is_not_signable_by_rank_either(self):
        _place("Senior Technician", "Technician", 2)
        self.assertFalse(position.outranks("Senior Technician", "Retired Rung"))

    def test_a_job_family_is_not_a_rung(self):
        """"Technician outranks Junior Technician" is not a sentence anybody means."""
        self.assertFalse(position.outranks("Technician", "Junior Technician"))
        self.assertFalse(position.outranks("Master Technician", "Technician"))

    def test_an_unknown_position_fails_closed(self):
        self.assertFalse(position.outranks("Nonexistent", "Junior Technician"))
        self.assertFalse(position.outranks("Senior Technician", "Nonexistent"))
        self.assertFalse(position.outranks(None, "Junior Technician"))
        self.assertFalse(position.outranks("Senior Technician", None))


class TestPositionsOutrankedBy(unittest.TestCase):
    """The IN-list form, used by the row-level filters. It must agree with the
    predicate exactly, or a supervisor sees rows they may not action — or, worse,
    does not see the ones they must."""

    def setUp(self):
        sys.modules["frappe"].db.positions.clear()
        _place("Junior Technician", "Technician", 1)
        _place("Senior Technician", "Technician", 2)
        _place("Master Technician", "Technician", 3)
        _place("Junior Designer", "Design", 1)
        _place("Technician", "Technician", 0, is_group=1)

    def test_it_lists_exactly_what_outranks_agrees_with(self):
        for holder in ("Junior Technician", "Senior Technician", "Master Technician"):
            with self.subTest(holder=holder):
                listed = set(position.positions_outranked_by(holder))
                agreed = {
                    other
                    for other in sys.modules["frappe"].db.positions
                    if position.outranks(holder, other)
                }
                self.assertEqual(listed, agreed)

    def test_the_bottom_rung_outranks_nobody(self):
        self.assertEqual(position.positions_outranked_by("Junior Technician"), [])

    def test_a_group_outranks_nobody(self):
        self.assertEqual(position.positions_outranked_by("Technician"), [])

    def test_no_position_at_all_outranks_nobody(self):
        self.assertEqual(position.positions_outranked_by(None), [])
        self.assertEqual(position.positions_outranked_by("Nonexistent"), [])


if __name__ == "__main__":
    unittest.main()


class TestAssignmentCanActuallyReachPeople(unittest.TestCase):
    """The precondition for everything else in WI-072.

    Prod reached v1.385.0 with zero `Training Assignment Rule` rows, five
    assignments in total ever, and two of sixteen employees holding any training
    record. The engine was complete and had never been aimed at anything, for two
    compounding reasons: there was no way to assign a *group* except by hand-adding
    a child row and republishing the course, and `sync_course` had exactly one
    caller — `publish_version` — which fires only if `auto_assign` was already set
    at the moment of publishing.

    So a profile page, a badge, a leaderboard and a skills matrix all render empty
    until this works, and none of them would report why.
    """

    HOOKS = APP / "hooks.py"
    AUTHOR = APP / "api/training_author.py"
    ASSIGNMENT = APP / "training/assignment.py"
    TASKS = APP / "training/tasks.py"
    COURSE_JS = APP / "public/js/training/training_course.js"

    def test_a_group_can_be_assigned_in_one_action(self):
        src = self.AUTHOR.read_text(encoding="utf-8")
        self.assertIn("def assign_course_to_group(", src)
        self.assertIn("def resolve_assignment_group(", src)

    def test_the_group_assigner_is_not_a_second_assignment_path(self):
        """`run_bulk_assign` is where the already-open check, the per-target
        isolation and the notification live. A group assigner writing its own rows
        would be a second place for all three to be got wrong."""
        src = self.AUTHOR.read_text(encoding="utf-8")
        body = src[src.index("def assign_course_to_group") : src.index("def _group_users")]
        self.assertIn("assign_course(", body)
        self.assertNotIn("frappe.get_doc(", body)

    def test_group_resolution_reuses_the_engine_map(self):
        """One mapping, or the dialog's preview and the auto-assign engine come to
        disagree about what 'every Junior Technician' means."""
        src = self.AUTHOR.read_text(encoding="utf-8")
        body = src[src.index("def _group_users") : src.index("def run_bulk_assign")]
        self.assertIn("EMPLOYEE_FIELD_FOR_RULE", body)

    def test_the_ladder_is_an_assignment_target(self):
        """'Every Junior Technician' is a rule about competence; 'every Designation'
        is a rule about job titles that happen to line up today."""
        self.assertIn('"Position": "custom_position"', self.ASSIGNMENT.read_text(encoding="utf-8"))

    def test_the_employee_row_carries_the_column_the_rule_reads(self):
        """The match is `employee.get(field) == value`, so a field the row was never
        fetched with can never match — and would fail silently, forever."""
        src = self.ASSIGNMENT.read_text(encoding="utf-8")
        body = src[src.index("def _matching_rule") : src.index("def _has_role_profile")]
        self.assertIn("custom_position", body)

    def test_a_promotion_re_evaluates_what_you_owe(self):
        self.assertIn("custom_position", self.ASSIGNMENT.read_text(encoding="utf-8").split("EMPLOYEE_FIELD_FOR_RULE")[0])

    def test_there_is_a_sweep_and_it_is_scheduled(self):
        """Without it, turning auto-assign on for a course that is already live does
        nothing at all, and neither does adding a rule to one."""
        self.assertIn("def sweep_auto_assignments(", self.TASKS.read_text(encoding="utf-8"))
        self.assertIn(
            "erpnext_enhancements.training.tasks.sweep_auto_assignments",
            self.HOOKS.read_text(encoding="utf-8"),
        )

    def test_the_sweep_runs_before_the_reminder_digest(self):
        """Anything raised today should be in this morning's email, not tomorrow's.

        Compared as times, not as strings. `"40 6 * * *"` sorts *after*
        `"15 7 * * *"` lexically while 06:40 is genuinely earlier than 07:15 — the
        kind of assertion that would pass on a wrong schedule and fail on a right
        one.
        """
        import re

        hooks = self.HOOKS.read_text(encoding="utf-8")

        def minutes_before(job):
            at = hooks.index(job)
            cron = re.findall(r'"(\d+ \d+ \* \* \*)"', hooks[:at])[-1]
            minute, hour = cron.split()[:2]
            return int(hour) * 60 + int(minute)

        self.assertLess(
            minutes_before("tasks.sweep_auto_assignments"),
            minutes_before("tasks.send_due_reminders"),
            "the sweep runs after the digest, so a newly raised assignment waits a day",
        )

    def test_the_sweep_cannot_lose_every_course_to_one_bad_one(self):
        src = self.TASKS.read_text(encoding="utf-8")
        body = src[src.index("def sweep_auto_assignments") :]
        self.assertIn("except Exception:", body)
        self.assertIn("log_error", body)

    def test_every_rule_target_is_mapped_in_all_four_places(self):
        """The vocabulary lives in four files — the child DocType's Select, the
        course controller's target map, the engine's Employee-field map, and the
        form script that stamps `applies_to_doctype` before Frappe validates the
        link. A target present in the Select and missing anywhere else fails for
        that option only, and nothing else notices."""
        options = [
            o
            for o in _read(APP / "training/doctype/training_assignment_rule/training_assignment_rule.json")[
                "fields"
            ][1]["options"].split("\n")
            if o.strip()
        ]
        controller = (APP / "training/doctype/training_course/training_course.py").read_text(
            encoding="utf-8"
        )
        engine = self.ASSIGNMENT.read_text(encoding="utf-8")
        script = self.COURSE_JS.read_text(encoding="utf-8")
        for option in options:
            with self.subTest(option=option):
                self.assertIn(f'"{option}"', controller)
                if option not in ("All Employees", "Role", "Role Profile"):
                    self.assertIn(f'"{option}"', engine)
                bare = option.replace(" ", "")
                self.assertTrue(
                    f'"{option}"' in script or f"{bare}:" in script,
                    f"{option!r} is not mapped in the course form script",
                )
