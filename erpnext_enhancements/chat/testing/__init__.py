# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""``chat.testing`` — the harness Phase 2's tests are written against. Ships in the app.

Deliberately empty, for the same reason as every other package marker under ``chat/``:
**no import-time work**. No ``frappe`` import, no settings read, no client construction.

Two rules govern everything in this package and both are load-bearing:

**Stdlib only.** ``fake_chat`` and ``fixtures`` import neither ``frappe`` nor ``requests``
at any scope. This repo has no Frappe integration-test job (``CLAUDE.md``), so the
bench-free tier is the only tier with automatic regression protection in CI — and a
bench-free test can only reach code that is importable without either package. A harness
that needed a bench would protect nothing.

**Nothing under ``chat/sync/`` may import from here.** The fake is a test double; a
production module that reached for it would ship the double. The dependency runs one way:
tests import both, production imports neither.

Why it ships inside the app rather than under ``tests/``: ``fake_chat`` is a *transport*
double, injected as ``GoogleChatClient(transport=…)``, and the smoke test and the bench
console both want it — ``bench execute`` cannot import from a ``tests/`` directory that is
not a package on the app's path.
"""
