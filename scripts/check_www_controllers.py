#!/usr/bin/env python3
"""Guard: every ``www/`` page controller must have an importable filename.

Frappe locates a web page's controller by taking the TEMPLATE's basename and
replacing hyphens with underscores
(``frappe/website/page_renderers/template_page.py``)::

    www/stripe-return.html   ->  frappe imports  www/stripe_return.py

A controller literally named ``stripe-return.py`` is therefore **never imported**,
and its ``get_context()`` never runs. Nothing errors: the template still renders,
just with every context variable undefined, silently taking whichever branch that
implies. That is the worst shape a bug can take — no exception, no log line, and a
page that looks fine to whoever ships it.

This is why the check exists rather than a comment. ``www/stripe-return.py`` sat
broken from the day it was written until v1.159.10, and for that whole time every
Stripe Checkout return — including cancellations — rendered "Thank you! Your
payment is being processed."

Note the route is unaffected: it comes from the template, so a hyphenated URL is
fine. Only the ``.py`` needs underscores.

Run: ``python scripts/check_www_controllers.py``  (exit 1 on failure)
"""

from __future__ import annotations

import pathlib
import sys

WWW = pathlib.Path(__file__).resolve().parents[1] / "erpnext_enhancements" / "www"


def main() -> int:
	if not WWW.is_dir():
		print(f"no www/ directory at {WWW}", file=sys.stderr)
		return 0

	controllers = sorted(WWW.glob("*.py"))
	broken = [path for path in controllers if "-" in path.stem]

	if broken:
		print("\nweb page controllers with un-importable filenames:\n", file=sys.stderr)
		for path in broken:
			print(f"  erpnext_enhancements/www/{path.name}", file=sys.stderr)
			print(
				f"    -> rename to {path.stem.replace('-', '_')}.py "
				"(leave the .html hyphenated; the public route does not change)",
				file=sys.stderr,
			)
		print(
			"\nFrappe maps a template's basename '-' to '_' when importing its "
			"controller, so these files are never loaded and their get_context() "
			"never runs — silently, with no error.\n",
			file=sys.stderr,
		)
		return 1

	print(f"www controller filenames OK ({len(controllers)} checked)")
	return 0


if __name__ == "__main__":
	sys.exit(main())
