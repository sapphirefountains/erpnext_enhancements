# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The bidirectional sync engine — outbox, relay worker, inbound ingest.

Deliberately empty, and for the same reason :mod:`erpnext_enhancements.chat` is: this
package marker performs **no import-time work**. A package ``__init__`` that imported its
own submodules would drag ``frappe`` into every one of them transitively, and four modules
in here — :mod:`.states`, :mod:`.decisions`, :mod:`.budget` and the arithmetic half of
:mod:`.ratelimit` — are *required* to import on a runner that has neither ``frappe`` nor
``requests`` installed. That bench-free tier is the only one in this repo with automatic
regression protection in CI (``CLAUDE.md``), so "importable with nothing but the standard
library" is a shipping constraint, not a preference. One re-export here would silently
delete the entire tier.

Import the submodule you want, by name.
"""
