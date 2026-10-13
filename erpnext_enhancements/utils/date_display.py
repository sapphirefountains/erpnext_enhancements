# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""How a date or time looks on paper, in an email and in an SMS: ``10/14/2026 2:00 PM``.

The Desk shows ``10/14/2026`` and a 24-hour ``14:00`` (the site's System Settings, set by
``patches/set_desk_date_and_time_format``); Frappe v16 offers no 12-hour Desk time. Everything
a customer or a crew member reads outside the Desk uses one style instead, and the pure
formatting lives in ``utils/normalize.py`` (``format_date`` / ``format_datetime`` /
``format_time``, no frappe import).

This module is the thin door for Jinja. ``hooks.py`` registers these three as **individual**
globals, prefixed ``sf_``, for the reason ``ee_*`` and ``ps_*`` are: ``get_jinja_hooks`` exports
every function of a module-valued entry into the namespace of every Print Format and web template
on the site, and a bare ``date`` or ``time`` there is not a trade worth making. A print format or
a Notification body writes::

    {{ sf_date(doc.posting_date) }}          10/14/2026
    {{ sf_datetime(doc.delivery_datetime) }} 10/14/2026 2:00 PM
    {{ sf_time(doc.start_time) }}            2:00 PM

``normalize`` is imported inside each function, not at the top, so this module imports on its own
and a fault in the formatter cannot stop the app loading its hooks. A print never fails on a date:
a value the formatter cannot read comes back as it was written (blank for ``None``).
"""


def _fallback(value):
	return "" if value is None else str(value)


def sf_date(value):
	"""``10/14/2026`` (``normalize.format_date``)."""
	try:
		from erpnext_enhancements.utils.normalize import format_date

		return format_date(value)
	except Exception:
		return _fallback(value)


def sf_datetime(value):
	"""``10/14/2026 2:00 PM`` (``normalize.format_datetime``)."""
	try:
		from erpnext_enhancements.utils.normalize import format_datetime

		return format_datetime(value)
	except Exception:
		return _fallback(value)


def sf_time(value):
	"""``2:00 PM`` (``normalize.format_time``)."""
	try:
		from erpnext_enhancements.utils.normalize import format_time

		return format_time(value)
	except Exception:
		return _fallback(value)
