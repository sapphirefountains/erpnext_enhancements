"""Field help for the guided flow and the Desk form (form kit P4.3).

Every field gets two pieces of help, and they live in the repository, one file per DocType in
``form_kit/help_text/`` (``opportunity.json``), changed by PR like the layout. Nik decided
(2026-10-13) that there is no Desk-editable Field Help:

``hint``
    One short line, always visible under the field: what to put in it. It is also the field's
    own ``description`` (the custom field in ``fixtures/custom_field.json``, or a
    ``description`` Property Setter for an ERPNext field), so the Desk form, Quick Entry and
    every grid show the same line. ``tests/test_form_kit.py`` fails when the two drift.
``help``
    The longer explanation behind the (i) bubble: why the field matters, who reads it next,
    what it sets off. Shown by the guided flow and, through ``public/js/form_kit/field_help.js``,
    on the standard Desk form.

``article`` names the Knowledge Article the help's *Learn more* link opens (P5.4); each field
links to the section of it anchored at its step's key. ``null`` until that article exists.

Frappe-free, so the engine's tests and the review tooling read it with no bench.
"""

import functools
import json
from pathlib import Path

HELP_DIR = Path(__file__).resolve().parent / "help_text"


def help_file(doctype):
	"""The help file for ``doctype``: ``Opportunity`` → ``help_text/opportunity.json``."""
	return HELP_DIR / (str(doctype).strip().lower().replace(" ", "_") + ".json")


@functools.cache
def load(doctype):
	"""``{"doctype", "article", "fields": {fieldname: {"hint", "help"}}}``; empty when there is none."""
	path = help_file(doctype)
	if not path.is_file():
		return {"doctype": doctype, "article": None, "fields": {}}
	data = json.loads(path.read_text(encoding="utf-8"))
	return {
		"doctype": doctype,
		"article": data.get("article") or None,
		"fields": {name: dict(entry or {}) for name, entry in (data.get("fields") or {}).items()},
	}


def for_field(doctype, fieldname):
	"""``{"hint", "help"}`` for one field; empty strings when the file says nothing about it."""
	entry = load(doctype)["fields"].get(fieldname) or {}
	return {"hint": entry.get("hint") or "", "help": entry.get("help") or ""}


def has_help(doctype):
	return help_file(doctype).is_file()
