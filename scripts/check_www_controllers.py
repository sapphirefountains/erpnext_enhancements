#!/usr/bin/env python3
"""Guard: every ``www/`` page controller must have an importable filename.

Frappe locates a web page's controller by taking the template's basename and
replacing hyphens with underscores
(``frappe/website/page_renderers/template_page.py``)::

    www/my-page.html   ->  looks for  www/my_page.py

A controller literally named ``my-page.py`` is therefore **never imported**, and
``get_context`` never runs. Nothing errors: the template still renders, just
without any of the context, cache headers or auth gates the controller was meant
to provide. That is a silent, total failure of the controller — the worst shape a
bug can take.

This has already happened once in this repo (``www/stripe-return.py``), which is
why the check exists rather than a comment.

Run: ``python scripts/check_www_controllers.py``  (exit 1 on failure)
"""

from __future__ import annotations

import pathlib
import sys

WWW = pathlib.Path(__file__).resolve().parents[1] / "erpnext_enhancements" / "www"

#: Pre-existing offenders, tracked so this check can be added without first
#: fixing them. Removing an entry here is the fix — do not add new ones.
#: stripe-return.py: its get_context has never executed. Fixing it changes the
#: Stripe return page's behaviour, so it belongs on its own branch.
KNOWN_BROKEN = {
	"stripe-return.py",
}


def main() -> int:
	if not WWW.is_dir():
		print(f"no www/ directory at {WWW}", file=sys.stderr)
		return 0

	broken: list[str] = []
	stale_exemptions: list[str] = []

	for path in sorted(WWW.glob("*.py")):
		if "-" not in path.stem:
			continue
		if path.name in KNOWN_BROKEN:
			stale_exemptions.append(path.name)
			continue
		broken.append(path.name)

	for name in sorted(KNOWN_BROKEN - set(stale_exemptions)):
		print(
			f"note: '{name}' is exempted in KNOWN_BROKEN but no longer exists — "
			"remove it from the list.",
			file=sys.stderr,
		)

	if broken:
		print("\nweb page controllers with un-importable filenames:\n", file=sys.stderr)
		for name in broken:
			fixed = name.replace("-", "_")
			print(f"  erpnext_enhancements/www/{name}", file=sys.stderr)
			print(f"    -> rename to {fixed} (keep the .html hyphenated; the route does not change)", file=sys.stderr)
		print(
			"\nFrappe maps a template's basename '-' to '_' when importing its "
			"controller, so these files are never loaded and their get_context() "
			"never runs.\n",
			file=sys.stderr,
		)
		return 1

	checked = len(list(WWW.glob("*.py")))
	print(f"www controller filenames OK ({checked} checked, {len(stale_exemptions)} known-broken)")
	return 0


if __name__ == "__main__":
	sys.exit(main())
