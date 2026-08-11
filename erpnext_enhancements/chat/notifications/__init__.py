# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Notifications — the phase where every failure is invisible by construction.

Nobody reports the ping they did not get. A suppressed message produces no error, no log
line and no complaint until somebody says "I never got that" three days later, which is why
this package is built the way it is: the decision is one pure function with an exhaustive
table test, every decision carries a reason code, and there is a
:mod:`~erpnext_enhancements.chat.notifications.debug` endpoint whose only job is to answer
"why didn't Jane get this".

The modules, in the order they were built and the order they depend on each other:

* :mod:`policy` — the twelve-row suppression table as one pure function. **No Frappe, no
  database, no clock.** Everything else is wiring around it.
* :mod:`presence` — the Redis heartbeat store the policy reads its inputs from. A
  heartbeat with an expiry, never a flag set on connect and cleared on disconnect.
* :mod:`bell` — ``Notification Log`` rows, deduped per (user, room), with the email path
  structurally impossible rather than defaulted off.
* :mod:`webpush` — VAPID and ``aes128gcm``, hand-rolled. See that package's README before
  concluding it should use a library; the short version is that there is no library on the
  bench and the deploy has no step that could install one.
* :mod:`fanout` — Phase 2's ``seams.notify_new_message`` seam, finally implemented.
* :mod:`read_state` — the four-part cross-surface read sync, including the part everyone
  forgets: the data push that closes the OS notification.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""
