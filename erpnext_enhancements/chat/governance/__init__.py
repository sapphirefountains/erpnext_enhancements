"""Phase 6's governance surface: who may read chat they are not in, and the record of it.

Everything here is *about* chat rather than *part of* it. Nothing in this package serves a
message to a coworker; it serves the auditor, the compliance query and the operator.

The package exists because Appendix B §8 names it as the home for the oversight viewer, the
export, the access report, retention and drift. It opens with :mod:`role_grants` because §6's
ordering is deliberate — the vault (`Chat Audit Log` and its four immutability layers, v1.285.0)
comes before any writer, and the first writer is the one that records who was handed the key.

Indentation is tabs, per ``CLAUDE.md``.
"""
