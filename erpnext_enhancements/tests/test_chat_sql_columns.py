# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Every column named in the chat package's SQL must exist on a DocType the query names.

--------------------------------------------------------------------------------------
Why this exists
--------------------------------------------------------------------------------------

``indexer._messages_after`` selected ``origin_timestamp`` from ``Chat Message``. There is no
such field — the real one is ``gchat_create_time``. MariaDB answers that with **1054, Unknown
column**, which the driver raises as ``OperationalError``, on every execution, forever.

It reached production and ran for two hours because **nothing had ever executed that
statement**. The pure suites cover ``chunker.chunk_messages`` thoroughly, and it is a good
chunker; the SQL that *feeds* it needs a database, so it lived in the one gap between "tested
without a bench" and "tested with one". A wrong column name is invisible to every test that
does not connect to a database, and it is not the sort of mistake review catches either —
``origin_timestamp`` is an entirely plausible name for the field it was reaching for.

This closes that gap without a bench: the DocType JSONs declare the columns, the source
declares the queries, and both are files.

--------------------------------------------------------------------------------------
What it deliberately does not do
--------------------------------------------------------------------------------------

It is not a SQL parser, and trying to be one would make it fragile and eventually ignored. It
extracts backticked identifiers and checks them against the fields of the doctypes the same
statement names, plus Frappe's standard columns and the aliases the statement itself defines
with ``as``. That is enough to catch a name that exists nowhere, which is the whole failure
class. It cannot catch a column that exists on the *wrong* table in a join, and it is not
claimed to.
"""

from __future__ import annotations

import json
import pathlib
import re
import unittest

APP = pathlib.Path(__file__).resolve().parent.parent
CHAT = APP / "chat"

#: Columns Frappe puts on every table, which no DocType JSON declares.
STANDARD_COLUMNS = frozenset(
    {
        "name",
        "creation",
        "modified",
        "owner",
        "modified_by",
        "idx",
        "docstatus",
        "parent",
        "parenttype",
        "parentfield",
        "_assign",
        "_comments",
        "_liked_by",
        "_user_tags",
    }
)

#: `frappe.db.sql(""" … """)` — the only shape the chat package uses.
SQL_CALL = re.compile(r'frappe\.db\.sql(?:_ddl)?\(\s*f?"""(.*?)"""', re.S)
TABLE_REF = re.compile(r"`tab\{(\w+)\}`")
BACKTICKED = re.compile(r"`([a-z_][a-z0-9_]*)`")
ALIAS = re.compile(r"\bas\s+`([a-z_][a-z0-9_]*)`", re.I)


def _doctype_fields() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path in APP.rglob("*/doctype/*/*.json"):
        if path.stem != path.parent.name:
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        name = doc.get("name")
        if name:
            out[name] = {f["fieldname"] for f in doc.get("fields", []) if f.get("fieldname")}
    return out


class TestEveryColumnInChatSqlExists(unittest.TestCase):
    def test_no_statement_names_a_column_that_exists_nowhere(self) -> None:
        """The regression guard for `origin_timestamp`: MariaDB 1054, every run, forever."""
        fields = _doctype_fields()
        offences: list[str] = []

        for source in sorted(CHAT.rglob("*.py")):
            text = source.read_text(encoding="utf-8")
            for match in SQL_CALL.finditer(text):
                statement = match.group(1)

                # `tab{MESSAGE_DOCTYPE}` -> the module constant -> the doctype name.
                doctypes = []
                for var in TABLE_REF.findall(statement):
                    const = re.search(rf'^{var}\s*=\s*"([^"]+)"', text, re.M)
                    if const:
                        doctypes.append(const.group(1))
                if not doctypes:
                    continue

                known = set(STANDARD_COLUMNS)
                for doctype in doctypes:
                    known |= fields.get(doctype, set())
                known |= set(ALIAS.findall(statement))

                for column in sorted(set(BACKTICKED.findall(statement))):
                    if column not in known:
                        offences.append(
                            f"{source.relative_to(APP)}: `{column}` is not a field of "
                            f"{', '.join(sorted(set(doctypes)))} (nor a standard column or an "
                            f"alias this statement defines)"
                        )

        self.assertEqual(
            offences,
            [],
            "SQL names a column that no DocType declares. MariaDB answers this with 1054 on "
            "every execution — deterministic, and invisible to every test that does not "
            "connect to a database:\n  " + "\n  ".join(offences),
        )


if __name__ == "__main__":
    unittest.main()
