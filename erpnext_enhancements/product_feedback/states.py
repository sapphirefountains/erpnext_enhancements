# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The ``Enhancement Request`` state machine — **pure functions, no I/O, no Frappe**.

Stdlib only, which is what puts this module in the bench-free CI tier — the only tier in
this repo with automatic regression protection (``CLAUDE.md``: there is no Frappe
integration-test job). ``tests/test_feedback_states.py`` asserts this enum against the
DocType JSON's ``Select`` options, so the two cannot drift; renaming a Select option needs
a data patch or existing rows refuse to save, and that test is what makes the rename
visible before it ships.

Modelled on :mod:`erpnext_enhancements.chat.sync.states`, for the same reasons.

--------------------------------------------------------------------------------------
Why the lifecycle is a ``status`` field and not a Frappe Workflow
--------------------------------------------------------------------------------------

ADR 0002 makes a custom mechanism a defect where a native one suffices, so the deviation
is recorded rather than assumed. A Frappe Workflow transition is *a human action gated by
a role*. Three of this machine's transitions have no human behind them —
``Approved -> Breakdown Ready`` and ``Approved -> Breakdown Failed`` are written by a
background worker, and the terminal ``Breakdown Ready -> Tasks Created`` is an
accept-the-edited-proposal call, not a ``docstatus`` bump. Expressing that as a Workflow
means either a second status field for the machine states or transitions no human ever
performs. See ADR 0010.

``docstatus`` is wrong here for the reason ``chat_export_request.py`` already states: a
governance record that can be *cancelled* is a governance record with an undo button.

--------------------------------------------------------------------------------------
Two things the table encodes that are easy to miss
--------------------------------------------------------------------------------------

**Re-running a breakdown goes backwards to ``Approved``.** There is deliberately no
``Regenerating`` state. Both ``Breakdown Ready`` and ``Breakdown Failed`` may return to
``Approved``, which is the state the sweeper already looks for — so a re-run and a
deploy-lost job are recovered by exactly one query rather than two.

**The three terminal states are terminal.** A rejected request is not reopened; a new
request is the answer. This costs a reviewer a re-file when they misclick and buys the
property that ``Tasks Created`` cannot be walked back to a state where the same proposal
could be written to a Project twice.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Final


class RequestState(str, Enum):
	"""``Enhancement Request.status``, exactly as the shipped DocType declares it.

	``str`` mixin so a value compares equal to the string Frappe stores and can be handed
	straight to ``db.set_value`` without an ``.value`` dance at every call site.
	"""

	SUBMITTED = "Submitted"
	APPROVED = "Approved"
	BREAKDOWN_READY = "Breakdown Ready"
	BREAKDOWN_FAILED = "Breakdown Failed"
	TASKS_CREATED = "Tasks Created"
	REJECTED = "Rejected"
	DUPLICATE = "Duplicate"


#: Nothing leaves these. See the module docstring.
TERMINAL_STATES: Final[frozenset[str]] = frozenset(
	{RequestState.TASKS_CREATED, RequestState.REJECTED, RequestState.DUPLICATE}
)

#: States a reviewer may close from without ever asking for a breakdown. A request that is
#: obviously a duplicate, or obviously not happening, should not cost a model call.
_CLOSEABLE: Final[frozenset[str]] = frozenset({RequestState.REJECTED, RequestState.DUPLICATE})

LEGAL_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = {
	RequestState.SUBMITTED: frozenset({RequestState.APPROVED}) | _CLOSEABLE,
	# Machine-driven, both of them. This is the state the sweeper re-drives.
	RequestState.APPROVED: frozenset(
		{RequestState.BREAKDOWN_READY, RequestState.BREAKDOWN_FAILED}
	)
	| _CLOSEABLE,
	# `-> APPROVED` is "regenerate the proposal".
	RequestState.BREAKDOWN_READY: frozenset({RequestState.TASKS_CREATED, RequestState.APPROVED})
	| _CLOSEABLE,
	RequestState.BREAKDOWN_FAILED: frozenset({RequestState.APPROVED}) | _CLOSEABLE,
	RequestState.TASKS_CREATED: frozenset(),
	RequestState.REJECTED: frozenset(),
	RequestState.DUPLICATE: frozenset(),
}


class IllegalTransition(ValueError):
	"""Raised by :func:`assert_transition`. Callers inside Frappe translate to a throw."""


def is_legal(old: str, new: str) -> bool:
	"""Is ``old -> new`` a transition this machine allows?

	A no-op (``old == new``) is legal: saving a document without touching its status must
	not raise, and ``bench migrate``'s own resaves do exactly that.
	"""
	old = (old or RequestState.SUBMITTED).strip()
	new = (new or RequestState.SUBMITTED).strip()
	if old == new:
		return True
	return new in LEGAL_TRANSITIONS.get(old, frozenset())


def assert_transition(old: str, new: str) -> str:
	"""Return ``new`` if the transition is legal, else raise :class:`IllegalTransition`."""
	if not is_legal(old, new):
		raise IllegalTransition(
			f"An enhancement request cannot move from {old or '(unset)'} to {new or '(unset)'}."
		)
	return new


def is_open(status: str) -> bool:
	"""Is this request still something a human or a worker will act on?"""
	return (status or RequestState.SUBMITTED).strip() not in TERMINAL_STATES
