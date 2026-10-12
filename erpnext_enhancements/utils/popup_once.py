# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Show a save-time warning once per person per record, not once per save.

A ``validate`` hook that calls ``frappe.msgprint`` speaks on **every** save, and some
forms save far more often than a person thinks of themselves as saving. The Visit
Wizard autosaves the visit as a technician moves through it, so the uncertified-
dispatch advisory (``training/compliance.py``) put its "Training not current" pop-up
on every page of one visit, and the availability advisory ("Check who is going",
``hr_enhancements/availability.py``) does the same. A warning repeated on every page
stops being read by the second page, which is worse than one shown once.

:func:`first_time` answers "has this person already been shown this, here?" from
Redis. Each caller keys on the record **and on what it is saying** (the findings),
so a *new* finding on the same visit is shown again: the point is to stop repeating
a warning, never to hide a different one.

Two things here are deliberate:

* **Per person.** The technician filling a visit in and the reviewer approving it
  later are told once each. Neither one's dismissal silences the other.
* **It fails open.** A cache that cannot answer returns ``True`` and the warning
  shows. A duplicate pop-up is a nuisance; a safety warning that vanished because
  Redis hiccupped is not. This also means a deploy, which flushes Redis, can show a
  warning once more to someone who had already seen it.

The modules that call this keep their own settings. This shares only the memory of
what was shown, so neither advisory can be switched off through the other.
"""

import hashlib

import frappe

#: How long "already shown" lasts. A visit is filled in over a morning and reviewed
#: days later, so a week covers its working life without making the warning once-ever.
DEFAULT_TTL = 7 * 24 * 3600

_PREFIX = "ee_popup_once::"


def _key(user, parts):
	raw = "\x1f".join(str(part) for part in (user, *parts))
	return _PREFIX + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def first_time(*parts, ttl=DEFAULT_TTL):
	"""True the first time the session user reaches ``parts``; False for ``ttl`` after.

	``parts`` should name the warning, the record and what the warning says, e.g.
	``first_time("training-compliance", doc.doctype, doc.name, fingerprint)``.
	Never raises: any failure returns ``True`` (see the module docstring).
	"""
	try:
		key = _key(frappe.session.user, parts)
		if frappe.cache.get_value(key):
			return False
		frappe.cache.set_value(key, 1, expires_in_sec=ttl)
	except Exception:
		return True
	return True
