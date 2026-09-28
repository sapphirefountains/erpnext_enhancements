"""Shared by the three knowledge-base read tools (WI-080 PR 6a).

``search_company_knowledge``, ``fetch_knowledge_article`` and ``list_company_knowledge`` are thin: all
the work is in ``knowledge_base/ai_tools.py``, which each tool imports inside ``execute`` through
:func:`run`. This module holds what the three have in common: the ``kind`` and ``department``
properties of their input schemas, built from ``knowledge_base/constants.py`` (standard library only,
so importing it here costs nothing and needs no bench), and the failure path.

**Why a failure is a return and not a raise.** FAC 3.0.0's ``BaseTool._safe_execute`` catches an
exception from ``execute`` and writes an Error Log carrying ``Args: {arguments}`` and the full
traceback, and hands the model ``str(e)`` (WI-080, "Found while designing Slice 3", 5). Every outcome
``ai_tools`` expects (not found, an unknown filter) is already a normal return. Anything else becomes
``{"success": False, "error": FAILURE}``, which FAC reports as a ``ToolReportedError``, plus one
**deferred** Error Log naming the payload and the exception's **type** only: never its message, the
arguments or a traceback, and never re-raised, so no frame's locals are logged. Deferred, because v16
inserts a plain ``log_error`` in the request's own transaction (``utils/error.py:95-98``).

Imports frappe and ``knowledge_base.constants`` only, never ``frappe_assistant_core``, so it stays
importable in the bench-free contract tests.
"""

import frappe

from erpnext_enhancements.knowledge_base import constants

#: What a tool answers when something unexpected failed. Nothing these tools do writes, so the second
#: sentence is always true.
FAILURE = "The knowledge base could not be read just now. Nothing was changed."

#: The Error Log's title for such a failure.
LOG_TITLE = "Knowledge base AI tool"

#: ``kind`` on search and list: the three kinds, each described by ``constants.KIND_HELP``.
KIND_PROPERTY = {
    "type": "string",
    "enum": list(constants.ARTICLE_KINDS),
    "description": "Only articles of this kind. "
    + " ".join(f"{kind}: {constants.KIND_HELP[kind]}" for kind in constants.ARTICLE_KINDS),
}

#: ``department`` on search and list: the company document register's ten department blocks.
DEPARTMENT_PROPERTY = {
    "type": "string",
    "enum": list(constants.DEPARTMENT_BLOCK_OPTIONS),
    "description": "Only articles in this department (the document register's department blocks)",
}


def run(payload, arguments):
    """``ai_tools.<payload>(arguments)``, or :data:`FAILURE` if it raises. See the module docstring."""
    failed = None
    try:
        from erpnext_enhancements.knowledge_base import ai_tools

        return getattr(ai_tools, payload)(arguments if isinstance(arguments, dict) else {})
    except Exception as exc:
        failed = type(exc).__name__
    # Logged outside the except block, so no traceback is attached and nothing of the failed frame is
    # alive: the type's name is all that leaves.
    try:
        frappe.log_error(title=LOG_TITLE, message=f"{payload} raised {failed}", defer_insert=True)
    except Exception:
        pass
    return {"success": False, "error": FAILURE}
