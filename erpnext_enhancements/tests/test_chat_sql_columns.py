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

--------------------------------------------------------------------------------------
It silently skipped two thirds of the package, and that is how the same bug shipped twice
--------------------------------------------------------------------------------------

The first version resolved a table only when it was written a backticked ``tab{CONSTANT}`` — an
f-string interpolating a doctype *name*. ``chat/retrieval/gate.py`` writes ``{_MESSAGE_TABLE}``
instead, where the constant already holds a backticked ``tabChat Message``, and other modules write the
table literally. Neither form matched, so ``if not doctypes: continue`` skipped the statement —
**33 of 49**, including all eight in ``gate.py``.

So when ``gate._recent_messages`` selected the same nonexistent ``origin_timestamp``, this file
passed. Every ``@triton`` turn died in retrieval with ``OperationalError`` and the guard written
for exactly that bug never looked at the statement.

A checker that skips what it cannot parse reports the same green as one that verified
everything. **An unresolvable table is now a failure**, not a skip — the rule
``tests/test_chat_rawsql_guard.py`` already applies, and the reason it applies it.
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
#: The three ways this package names a table, all of which must resolve.
#:
#: Only the first was understood, which is why two thirds of the package went unchecked.
TABLE_REF = re.compile(r"`tab\{(\w+)\}`")  # `tab{MESSAGE_DOCTYPE}` — constant holds the NAME
TABLE_WHOLE = re.compile(r"\{(\w+)\}")  # {_MESSAGE_TABLE} — constant holds `tabChat Message`
TABLE_LITERAL = re.compile(r"`tab([A-Z][A-Za-z0-9 ]*)`")  # written out in full

BACKTICKED = re.compile(r"`([a-z_][a-z0-9_]*)`")
ALIAS = re.compile(r"\bas\s+`([a-z_][a-z0-9_]*)`", re.I)

#: A table's alias is the token right after it, and is not a column.
TABLE_ALIAS = re.compile(r"`tab[^`]+`\s+`([a-z_][a-z0-9_]*)`")

#: Columns of core Frappe doctypes this package joins to. Their JSONs live in ``frappe``, not
#: here, so the scan cannot read them — and without this every join to ``User`` or ``File``
#: reports its perfectly real columns as unknown. Listed explicitly rather than by disabling
#: the check on joins: a short visible list is a cost each addition has to pay, and the
#: alternative silently stops checking the *chat* columns in the same statement.
CORE_DOCTYPE_COLUMNS = frozenset({"enabled", "full_name", "user_image", "user_type", "file_url"})


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


def _tables_in(statement: str, module_source: str) -> set[str]:
    """Every DocType the statement reads from, by all three naming forms.

    Returns an empty set when nothing resolves, and the caller treats that as a **failure**.
    The previous version returned early on the same condition, which silently excused 33 of the
    package's 49 statements — including every one in ``retrieval/gate.py``.
    """
    doctypes = set(TABLE_LITERAL.findall(statement))
    for var in TABLE_REF.findall(statement):
        # `(?::[^=]+)?` for `NAME: Final[str] = "..."`. Without it the annotated constants —
        # which is most of them — did not resolve, and the statement was skipped.
        const = re.search(rf'^{var}\s*(?::[^=]+)?=\s*"([^"]+)"', module_source, re.M)
        if const:
            doctypes.add(const.group(1))
    for var in TABLE_WHOLE.findall(statement):
        const = re.search(rf'^{var}\s*(?::[^=]+)?=\s*"`tab([^`]+)`"', module_source, re.M)
        if const:
            doctypes.add(const.group(1))
    return doctypes


class TestEveryColumnInChatSqlExists(unittest.TestCase):
    def test_no_statement_names_a_column_that_exists_nowhere(self) -> None:
        """The regression guard for `origin_timestamp`: MariaDB 1054, every run, forever."""
        fields = _doctype_fields()
        offences: list[str] = []
        unresolved: list[str] = []

        for source in sorted(CHAT.rglob("*.py")):
            text = source.read_text(encoding="utf-8")
            for match in SQL_CALL.finditer(text):
                statement = match.group(1)

                doctypes = _tables_in(statement, text)
                if not doctypes:
                    unresolved.append(
                        f"{source.relative_to(APP)}: a statement whose table this scan cannot "
                        "name. It is skipped, which reads as verified — and that is how the "
                        "`origin_timestamp` bug shipped a second time."
                    )
                    continue

                known = set(STANDARD_COLUMNS) | set(CORE_DOCTYPE_COLUMNS)
                for doctype in doctypes:
                    known |= fields.get(doctype, set())
                known |= set(ALIAS.findall(statement))
                # A table's alias is a backticked lowercase token too, and is not a column.
                known |= set(TABLE_ALIAS.findall(statement))

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
        self.assertEqual(
            unresolved,
            [],
            "A statement this scan cannot attribute to a table is not checked, and an "
            "unchecked statement reports exactly the same green as a verified one. Add the "
            "naming form to `_tables_in` rather than letting the statement through:\n  "
            + "\n  ".join(unresolved),
        )

#: ``match(`col`) against (…)`` — a full-text search, which needs a FULLTEXT index on exactly
#: that column. ``frappe.db.add_index`` cannot emit one, so they are created by patch.
MATCH_COLUMN = re.compile(r"match\s*\(\s*`([a-z_][a-z0-9_]*)`\s*\)\s*against", re.I)


def _declared_fulltext_columns() -> set[str]:
    """Columns some chat patch declares a FULLTEXT index for.

    Read out of the patch *sources* rather than imported: importing a patch pulls in ``frappe``,
    and this suite deliberately runs without one.
    """
    columns: set[str] = set()
    for path in (APP / "patches").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        # ("Chat Context Chunk", "body", "chunk_body_fulltext")
        columns |= set(re.findall(r'\(\s*"[^"]+"\s*,\s*"([a-z_]+)"\s*,\s*"[a-z_]+"\s*\)', text))
        # COLUMN = "text_plain"
        columns |= set(re.findall(r'^COLUMN\s*=\s*"([a-z_]+)"', text, re.M))
    return columns


class TestEveryFulltextSearchHasAnIndex(unittest.TestCase):
    """A ``MATCH`` with no FULLTEXT index behind it is MariaDB 1191, every execution, forever.

    The same failure class as the missing column above, and it cost the same afternoon.
    ``gate._recent_chunks`` matches ``Chat Context Chunk.body``, which
    ``add_chat_phase5_indexes`` creates. ``gate._authored_by`` matches
    ``Chat Message.text_plain``, which nothing created — so exactly one of the gate's two
    full-text searches had an index behind it, and the other killed every ``@triton`` turn.

    ``chat/api/search.py`` had even written down that this column has no index and that adding
    one *"is a migration with its own rollout"*. Phase 5 then queried it as though the migration
    had happened. Two defensible decisions with no edge between them.
    """

    def test_every_matched_column_has_a_declared_fulltext_index(self) -> None:
        declared = _declared_fulltext_columns()
        self.assertTrue(
            declared, "no FULLTEXT declarations found at all — the scan has stopped matching"
        )

        missing: list[str] = []
        for source in sorted(CHAT.rglob("*.py")):
            text = source.read_text(encoding="utf-8")
            for match in SQL_CALL.finditer(text):
                for column in sorted(set(MATCH_COLUMN.findall(match.group(1)))):
                    if column not in declared:
                        missing.append(f"{source.relative_to(APP)}: match(`{column}`)")

        self.assertEqual(
            sorted(set(missing)),
            [],
            "SQL full-text-searches a column that no patch indexes. MariaDB answers 1191 on "
            "every execution, and no test that avoids a database can see it: "
            + "; ".join(sorted(set(missing))),
        )


if __name__ == "__main__":
    unittest.main()
