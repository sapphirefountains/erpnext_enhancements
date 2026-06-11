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
* tools are read-only unless they go through the write gate below

Write gate (v1.14.0): importing this package applies the AI
write-confirmation gate — a class-level wrap of ``BaseTool._safe_execute``
(see ``_gate.py``). FAC imports this package on every MCP request before
dispatch, so the gate is in place before any tool executes in a fresh worker.
``apply_gate()`` is idempotent and no-ops when FAC (or the seam) is absent —
including under the bench-free test stubs.
"""

from erpnext_enhancements.assistant_tools._gate import apply_gate

apply_gate()
