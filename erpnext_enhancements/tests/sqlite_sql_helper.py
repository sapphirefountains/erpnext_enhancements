# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A ``frappe.db.sql`` stand-in that runs a query for real, on an in-memory SQLite database.

Bench-free tests of a counting query used to assert on the SQL *text*, which passes whatever the
query counts. This runs it. The dialects agree on what these queries use (backtick-quoted table
names, ``coalesce``, ``count(distinct ...)``, correlated sub-selects, ``exists``); the one
difference is the parameter style, ``%(name)s`` here and ``:name`` in SQLite, which :func:`sql`
translates. Not a test file (no ``test_`` prefix), so unittest does not collect it.
"""

import re
import sqlite3


def connect(*ddl):
	conn = sqlite3.connect(":memory:")  # the caller closes it
	for statement in ddl:
		conn.execute(statement)
	return conn


def sql_for(conn, only=None, strict=False):
	"""A ``frappe.db.sql`` that runs on ``conn``.

	A query that mentions none of the names in ``only`` (a tuple of table names), or that SQLite
	cannot run because its table is not in this fixture (unless ``strict``, which raises), answers ``[(None,)]``: the same shape a
	one-cell aggregate over nothing has, so a caller that is not under test carries on.
	"""

	def sql(query, values=None, as_dict=False, **kwargs):
		if only and not any(name in query for name in only):
			return [(None,)]
		text = re.sub(r"%\((\w+)\)s", r":\1", query)
		try:
			return conn.execute(text, values or {}).fetchall()
		except sqlite3.OperationalError:
			if strict:
				raise
			return [(None,)]

	return sql
