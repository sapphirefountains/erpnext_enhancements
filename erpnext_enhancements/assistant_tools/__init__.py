"""Custom MCP tools for Frappe Assistant Core (FAC).

This package is only ever imported by frappe_assistant_core's tool loader,
which reads the ``assistant_tools`` hook in hooks.py and imports each dotted
path. Nothing inside erpnext_enhancements may import this package — that
invariant keeps the app working on sites where FAC is not installed (the hook
entries are inert strings there). Enforced by a tripwire test in
tests/test_assistant_tools_schema.py.

Conventions (required by FAC's custom_tools plugin):
* one module per tool; the module filename must equal the tool's ``name``
* each module defines exactly one BaseTool subclass, listed in hooks.py
* all tools in this first batch are read-only
"""
