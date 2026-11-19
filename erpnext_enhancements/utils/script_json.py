"""Print a value inside an inline ``<script>`` as data.

Pages in ``www/`` hand their boot payload to the front end as ``window.X = {{ boot_json | safe }}``
or as a ``<script type="application/json">`` data island. ``frappe.as_json`` leaves ``<``, ``>`` and
``&`` alone, so a record value containing ``</script>`` would be read by the HTML parser rather than
by the script. :func:`script_json` writes those three characters as ``\\u`` escapes, which read back
as the same characters, and changes nothing else: outside a string JSON has none of the three.
"""

import frappe


def script_json(value):
	"""``frappe.as_json`` made safe to print inside an inline ``<script>``.

	``<``, ``>`` and ``&`` become ``\\u`` escapes, which ``JSON.parse`` and a JavaScript object
	literal both read back as the same characters.
	"""
	return frappe.as_json(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
