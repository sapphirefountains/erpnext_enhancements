"""Being given a course has to reach the person it is given to.

**The gap.** `notifications.notify_assigned` had exactly two callers — the
auto-assign engine and `api.training_author.assign_course` — and `hooks.py` named
`Training Assignment` only in its two permission hooks. So a Training Manager
pressing **New** on the list, or filling in a row by hand, produced no email, no
bell, no ToDo and no sign of any kind. The assignment existed, the learner was
never told, and the first anybody knew was the overdue sweep at 06:40 some days
later.

That shape — "one more path that forgot" — is why the notification is a
`doc_event` rather than a third explicit call, and why this module asserts there is
exactly **one** path rather than that the paths that exist are correct.

Run: python -m unittest erpnext_enhancements.tests.test_training_assignment_notice
"""

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]


def read(rel):
    return (APP / rel).read_text(encoding="utf-8")


def code(rel):
    """Source with docstrings and `#` comments removed.

    Every assertion below about a call being ABSENT is satisfied by prose otherwise,
    and each of these files now explains the removal in a comment that names the
    function it removed.
    """
    text = re.sub(r'"""(?:.|\n)*?"""', "", read(rel))
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))


class TestThereIsExactlyOnePath(unittest.TestCase):
    def test_the_doc_event_is_registered(self):
        hooks = code("hooks.py")
        self.assertIn('"Training Assignment": {', hooks)
        self.assertIn(
            "erpnext_enhancements.training.assignment.on_assignment_insert", hooks
        )

    def test_the_handler_exists_and_notifies(self):
        source = code("training/assignment.py")
        self.assertIn("def on_assignment_insert(", source)
        start = source.index("def on_assignment_insert(")
        self.assertIn("notify_assigned", source[start:])

    def test_nothing_else_calls_it(self):
        """Two explicit callers plus a doc_event would notify twice, and the second
        email is worse than the first was missing -- it teaches people the reminders
        are noise."""
        callers = []
        for rel in ("training/assignment.py", "api/training_author.py", "training/notifications.py"):
            source = code(rel)
            for match in re.finditer(r"notify_assigned\(", source):
                # The definition and the one call inside the handler are expected.
                callers.append((rel, match.start()))
        in_handler = code("training/assignment.py")
        handler_at = in_handler.index("def on_assignment_insert(")
        stray = [
            (rel, at)
            for rel, at in callers
            if not (rel == "training/assignment.py" and at > handler_at)
            and not (rel == "training/notifications.py")
        ]
        self.assertEqual(stray, [], f"notify_assigned still has explicit callers: {stray}")


class TestTheHandlerCannotBreakTheInsert(unittest.TestCase):
    """The row is the obligation; the email is only how somebody hears about it.
    Losing the assignment because the notification failed would be backwards."""

    def test_it_is_gated_and_guarded(self):
        source = code("training/assignment.py")
        body = source[source.index("def on_assignment_insert(") :]
        self.assertIn("_active()", body)
        self.assertIn("except Exception", body)
        self.assertIn("log_error", body)


class TestTheToDoIsRaisedFromTheJob(unittest.TestCase):
    """`assign_to.add` can `frappe.throw` -- if the assignee lacks read permission
    and `disable_document_sharing` is on it refuses with "Missing Permission" -- and
    a throw inside `after_insert` aborts the insert."""

    def test_it_lives_in_the_enqueued_sender(self):
        source = code("training/notifications.py")
        self.assertIn("def _raise_todo(", source)
        send_at = source.index("def send_assigned(")
        todo_at = source.index("def _raise_todo(")
        self.assertIn("_raise_todo(", source[send_at:todo_at])

    def test_the_handler_does_not_raise_it_directly(self):
        """It must reach the ToDo through the enqueued job, never inline."""
        self.assertNotIn("_raise_todo", code("training/assignment.py"))

    def test_it_mutes_messages(self):
        """`assign_to.add` msgprints on a duplicate or a share, and a msgprint raised
        in a background job still rides out to whatever client is listening as
        `_server_messages` -- so a manager assigning a course would have seen
        "Already in the following Users ToDo list" pop over the form."""
        body = code("training/notifications.py")
        body = body[body.index("def _raise_todo(") :]
        self.assertIn("mute_messages", body)
        self.assertIn("finally:", body)

    def test_it_cannot_take_the_email_with_it(self):
        """They share a job. An exception escaping here would lose the email too,
        which is the notification that actually reaches somebody."""
        body = code("training/notifications.py")
        body = body[body.index("def _raise_todo(") :]
        self.assertIn("except Exception", body)

    def test_the_import_is_local(self):
        """`frappe.desk.form.assign_to` at module scope would be imported by every
        bench-free suite that stubs frappe for this module."""
        source = code("training/notifications.py")
        self.assertNotRegex(source, r"(?m)^from frappe\.desk\.form\.assign_to import")


if __name__ == "__main__":
    unittest.main()
