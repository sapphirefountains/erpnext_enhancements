# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""QR codes as inline SVG, for the warehouse labels. **No Frappe.**

The encoder is ``pyqrcode``, which frappe v16 already depends on (``PyQRCode~=1.2.1`` in
its ``pyproject.toml``; ``frappe/twofactor.py`` draws the 2FA enrolment code with it). The
production host is managed and cannot pip-install anything, so reusing a library the
framework guarantees is the only way to encode server-side without vendoring an encoder.

The drawing is ours rather than ``QRCode.svg()``, for three reasons:

* **One path, not one ``<rect>`` per module.** A Version 5 code is 37 x 37 modules, and a
  sheet of 30 labels would carry tens of thousands of elements. Runs of dark modules on
  each row become one ``h``/``v`` sub-path, so a label is a single short ``<path>``.
* **A ``viewBox`` and no fixed size.** The label's CSS decides the printed size, in inches,
  so the same markup serves a 1" Avery label and a 4" shelf sign without resampling.
* **``shape-rendering="crispEdges"``**, so a printer never anti-aliases the gap between two
  modules into grey, which is what makes a small printed code unreadable.

:func:`matrix_to_svg` is pure and takes any 0/1 matrix, so it is tested bench-free with a
hand-made matrix; only :func:`qr_matrix` needs the library.
"""

from xml.sax.saxutils import escape

#: Error correction. ``M`` (15%) survives a scuffed or partly covered shelf label, and
#: keeps a ~70-character label URL at Version 4-5, whose modules stay large enough to
#: read on a 1-inch label. ``Q``/``H`` would push it to Version 6-7 and shrink every module.
ERROR_LEVEL = "M"

#: The white border the QR standard requires around a code, in modules. Scanners find a
#: code by its finder patterns against a quiet background; four modules is the spec.
QUIET_ZONE = 4


def qr_matrix(content, error=ERROR_LEVEL):
	"""The module matrix for ``content``: a list of rows of 0/1, no quiet zone."""
	import pyqrcode

	code = pyqrcode.create(content, error=error, mode="binary")
	return [list(row) for row in code.code]


def matrix_to_svg(matrix, quiet_zone=QUIET_ZONE, title=None, css_class="qr"):
	"""Draw a 0/1 module matrix as a compact, scalable SVG string.

	Each row's runs of dark modules become one horizontal sub-path, so the output grows
	with the number of runs rather than the number of modules.
	"""
	rows = [list(row) for row in (matrix or [])]
	size = len(rows)
	span = size + 2 * quiet_zone
	commands = []
	for y, row in enumerate(rows):
		x = 0
		width = len(row)
		while x < width:
			if row[x]:
				start = x
				while x < width and row[x]:
					x += 1
				commands.append(f"M{start + quiet_zone},{y + quiet_zone}h{x - start}v1h-{x - start}z")
			else:
				x += 1
	title_tag = f"<title>{escape(title)}</title>" if title else ""
	return (
		f'<svg xmlns="http://www.w3.org/2000/svg" class="{escape(css_class)}" '
		f'viewBox="0 0 {span} {span}" shape-rendering="crispEdges" role="img">'
		f"{title_tag}"
		f'<rect width="{span}" height="{span}" fill="#fff"/>'
		f'<path fill="#000" d="{"".join(commands)}"/>'
		"</svg>"
	)


def qr_svg(content, title=None, css_class="qr"):
	"""``content`` encoded as a QR code and drawn as an SVG string."""
	return matrix_to_svg(qr_matrix(content), title=title, css_class=css_class)
