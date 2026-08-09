# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""``chat.gchat`` — everything that knows Google Chat exists.

Deliberately empty, for the same reason as the parent package: **no import-time
work**. No ``frappe`` import, no settings read, no credential fetch, no client
construction. Importing :mod:`erpnext_enhancements.chat.gchat` must be free,
because it happens during ``bench migrate`` and during ERPNext's own test
bootstrap, long before ``Chat Settings`` or any custom field exists.

The boundary this package draws is the point of it. Google-facing HTTP lives
here and nowhere else:

* :mod:`.auth` mints access tokens — the *only* module that talks to
  ``iamcredentials.googleapis.com`` and ``oauth2.googleapis.com``.
* :mod:`.client` is the only module that talks to ``chat.googleapis.com``
  (asserted by a guardrail test, not merely by convention).
* :mod:`.ids` is pure: the deterministic id derivations, no I/O at all.
"""
