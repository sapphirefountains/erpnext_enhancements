# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Chat retrieval. **Two public symbols, and everything else is private.**

``retrieve`` is the gated read that runs as the asking person and derives their room set
itself. ``retrieve_for_oversight`` is the deliberately separate, role-gated, reason-requiring
read of rooms the reader is *not* in.

Two functions rather than one with a flag, because a boolean is one typo from being ``True``.
A distinctly named function cannot be reached by a caller who did not mean to reach it, and it
shows up as itself in a stack trace and in a grep.

``__all__`` is asserted by ``tests/test_chat_gate_source_scan.py`` — by **equality**, not
containment — so a third public symbol fails the build. The rule it protects is that there is
exactly one place the room set is derived, and a second entry point is a second place it might
not be.

The submodules are:

| module | what it is |
|---|---|
| ``gate`` | the only module in the app that may run SQL against the chat index |
| ``rank`` | **pure.** Reciprocal Rank Fusion, recency decay, boosts |
| ``budget`` | **pure.** The token ceiling and the ordered degradation ladder |
| ``assemble`` | **pure.** S0–S5 in prompt-cache order. No clock read above S5 |
| ``lexical`` | **pure.** The BOOLEAN MODE query builder and its input stripping |
| ``vectors`` | the ``VectorBackend`` adapter and the numpy cosine implementation |
| ``citations`` | the manifest, and server-side URL resolution |

Five of the seven are pure and import nothing but the stdlib, which is not tidiness: this repo
has no Frappe integration-test job, so anything needing a bench is verified by a human or not
at all. Keeping the judgement in import-free modules puts it in the tier that runs on every
push.

The two public symbols are re-exported **lazily**, through a module ``__getattr__``, and that
is load-bearing rather than clever. An eager ``from .gate import retrieve`` would run
``import frappe`` the moment anything in this package was imported — including
``chat.retrieval.budget``, which imports nothing and exists precisely so its arithmetic can be
tested without a bench. One eager import at the top of this file would move five pure modules
out of the tier CI can run and into the tier nothing runs.
"""

from typing import TYPE_CHECKING, Any

__all__ = ["retrieve", "retrieve_for_oversight"]

if TYPE_CHECKING:  # pragma: no cover — for type checkers only; never executed
	from erpnext_enhancements.chat.retrieval.gate import retrieve, retrieve_for_oversight


def __getattr__(name: str) -> Any:
	"""Resolve ``retrieve`` / ``retrieve_for_oversight`` on first access.

	Anything else raises ``AttributeError`` rather than falling through to the submodule,
	so ``from chat.retrieval import _visible_room_ids`` fails loudly. The package's whole
	claim is that it has two public symbols; a lazy loader that happily resolved a private
	one would make that claim false while looking like it enforced it.
	"""
	if name not in __all__:
		raise AttributeError(
			f"chat.retrieval exports only {__all__}; {name!r} is private. Retrieval has one "
			"door on purpose — a second entry point is a second place the room set might not "
			"be derived."
		)
	from erpnext_enhancements.chat.retrieval import gate

	return getattr(gate, name)
