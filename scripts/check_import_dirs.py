#!/usr/bin/env python3
"""Fail if a non-JSON file sits in an app-level directory Frappe imports from.

Frappe's ``frappe/model/sync.py`` holds a list of ``IMPORTABLE_DOCTYPES``. For each entry
it scans the matching **app-level** directory and calls ``orjson.loads()`` on *every file
it finds* — no ``*.json`` filter, no skip-list. One stray file aborts the entire schema
sync, and therefore the whole migrate.

This is not hypothetical. On 2026-07-31 production's migrate died on::

    bad json: .../erpnext_enhancements/workspace_sidebar/README.md
    orjson.JSONDecodeError: unexpected character: line 1 column 1 (char 0)

The README had been there since 2026-07-29 doing no harm. Frappe 16.29.0 added
``("desk", "workspace_sidebar")`` to the importable list, and a directory that had been
inert became an import target overnight. Nothing in this repo changed.

That is the trap worth guarding: the blast radius is the whole migrate, the failure is
remote (it happens on deploy, not in review), and the triggering change is in *someone
else's* dependency. Same shape as the hyphenated ``www/`` controller that
``check_www_controllers.py`` guards.

The directory list is derived from the installed Frappe when available, so this keeps
working when Frappe adds another importable doctype. It falls back to a checked-in list so
CI still runs without a bench.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "erpnext_enhancements"

# Fallback for a bench-free checkout: the list as of Frappe 16.29.0.
FALLBACK_DIRS = [
    "client_script", "custom_field", "dashboard_chart_source", "doctype", "form_tour",
    "module_onboarding", "notification", "onboarding_step", "page", "permission_type",
    "print_format", "print_style", "property_setter", "report", "server_script",
    "web_form", "web_page", "web_template", "website_theme", "workspace",
    "workspace_sidebar",
]


def importable_dirs():
    """Ask the installed Frappe; fall back to the pinned list when there is no bench."""
    try:
        from frappe.model.sync import IMPORTABLE_DOCTYPES

        # Entries are (module, scrubbed_doctype) pairs.
        return sorted({entry[1] for entry in IMPORTABLE_DOCTYPES}), "installed frappe"
    except Exception:
        return sorted(FALLBACK_DIRS), "pinned fallback (no bench)"


def main():
    dirs, source = importable_dirs()
    offenders = []

    for name in dirs:
        directory = APP_ROOT / name
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.suffix != ".json":
                offenders.append(path.relative_to(REPO_ROOT))

    if offenders:
        print(f"Frappe imports every file in these app-level directories ({source}).")
        print("A non-JSON file here aborts `bench migrate` for the whole site.\n")
        for path in offenders:
            print(f"  {path}")
        print("\nMove it out of that directory — docs/ is the usual home.")
        return 1

    print(f"ok: no non-JSON files in {len(dirs)} importable app-level directories ({source})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
