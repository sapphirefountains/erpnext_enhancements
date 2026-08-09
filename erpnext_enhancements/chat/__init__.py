# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The ``Chat`` module — employee chat inside ERPNext, mirrored to Google Chat.

Deliberately empty. This package marker performs **no import-time work**: no
``frappe`` import, no settings read, no client construction, no registration.

The reason is specific rather than stylistic. ``hooks.py`` already carries a
module-scope side effect and it is documented in this repo as a hazard — a
module that does work at import time does that work during ``bench migrate``,
during ERPNext's own test bootstrap, and inside every worker that happens to
touch any symbol beneath it, in each case before the custom fields and settings
it might want exist. A second one would be a second thing to remember. Anything
this module needs at runtime is imported by the caller that needs it.
"""
