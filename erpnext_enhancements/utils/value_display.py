# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""How a stored contact value looks on paper, in an email and in an SMS: ``(801) 555-0100``.

From form kit P2-B a phone is stored ``+18015550100`` (``utils/normalize_hook.py``), which is
what a dialer wants and not what a person reads. The Desk formats it in the browser
(``public/js/normalize/desk_display.js``); a template has no browser, so it asks here. The rule
is ``normalize.phone_display`` (no frappe import): North American numbers grouped, an extension
as ``x123``, an international number as its ``+`` digits, and anything that cannot be read as a
phone returned exactly as stored.

The Jinja door, beside ``date_display``'s ``sf_date`` family and for the same reasons:
``hooks.py`` registers ``sf_phone`` **individually**, prefixed, because a module-valued entry
would export every function here into the namespace of every Print Format and web template on
the site. A Notification body or a print format writes::

    {{ sf_phone(doc.get("mobile_no")) }}     (801) 555-0100

``print_style.format_phone`` (the ``ps_`` print formats) reaches the same rule on its own.
Never raises: a template never fails on a phone.
"""

import re

#: Exactly what ``normalize.phone`` stores for a North American number, with or without an
#: extension — the same pattern ``public/js/normalize/desk_display.js`` calls ``STORED_NANP``.
STORED_NANP = re.compile(r"\+1[2-9][0-9]{9}(?:,[0-9]{1,6})?")


def shown_if_stored_phone(value):
	"""``value`` shown as a phone when it is exactly a stored North American number, else as is.

	For a renderer that prints many fields through one helper and cannot know which of them is a
	phone (the contract templates' ``fill``): only the stored shape is touched, so a name, an
	amount or a free-text note never is. Never raises.
	"""
	try:
		if isinstance(value, str) and STORED_NANP.fullmatch(value):
			return sf_phone(value)
	except Exception:
		pass
	return value


def sf_phone(value):
	"""``(801) 555-0100 x123`` (``normalize.phone_display``); blank for ``None``."""
	try:
		from erpnext_enhancements.utils.normalize import phone_display

		return phone_display(value)
	except Exception:
		return "" if value is None else str(value)
