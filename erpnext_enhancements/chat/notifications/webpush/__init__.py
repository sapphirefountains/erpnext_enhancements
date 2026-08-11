# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Web Push, hand-rolled. **Read this before concluding it should use a library.**

Frappe v16 ships ``frappe.push_notification.PushNotification`` and this site has
``Push Notification Settings.enable_push_notification_relay = 1`` with credentials filled in.
It looks configured. It is broken, and it is broken in the worst available way:
``push_relay_server_url`` is ``null`` in site config, while ``is_enabled()`` reads only the
DocType flag — so it returns **True**, and then hands a ``None`` URL to its HTTP client and
fails at request time. It is also Google-Cloud-Messaging only and needs a second service we
do not run. **Do not design chat push on it.**

The libraries that would normally do this — ``pywebpush``, ``py_vapid``, ``ecdsa``,
``http_ece`` — are all measurably absent from the production bench, and the deploy pipeline
is ``git fetch/reset → bench migrate → bench build → FLUSHDB → restart`` with **no ``pip
install`` step at all**. A dependency added to ``pyproject.toml`` would not be installed by a
deploy and would be lost on any VM rebuild from the startup script. ``cryptography`` and
``PyJWT`` are present and are the whole toolkit.

So this package is roughly 200 lines of authenticated crypto written against the RFCs, in the
same spirit — and for the same reason — as ``stripe_payments`` hand-rolling ``Stripe-Signature``
verification and ``quickbooks_online`` hand-rolling its OAuth client. That is a deliberate,
argued position rather than an oversight, which is why it is written down here: the next
person's instinct will be to fix it by adding a library, and the fix would not survive a
deploy.

The modules:

* :mod:`encrypt` — RFC 8188 ``aes128gcm`` and the RFC 8291 key schedule. Pinned against the
  RFC's published example byte for byte, because every mistake available in it is symmetric
  and a round-trip test would pass while no browser could read a word.
* :mod:`vapid` — RFC 8292 identity. The keypair lives in ``site_config.json``; that module
  explains why it is not on a DocType.
* :mod:`subscriptions` — the ``Chat Push Subscription`` registry, and the pruning without
  which a dead-subscription table grows forever and quietly fails every send.
* :mod:`sender` — the POST, and the status handling that decides whether a failure is
  transient, terminal, or the subscription's own fault.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""
