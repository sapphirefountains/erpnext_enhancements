#!/usr/bin/env python3
"""Differential fuzz, half two: fail the build if the server is ever LOOSER than the browser.

    node scripts/fuzz_url_safety.mjs | python scripts/fuzz_url_safety_check.py

Reads JSONL of ``{"cp": [codepoints], "js": bool}`` and computes the server verdict for each.

**Only one direction is a failure.** The invariant the boundary rests on is::

    is_safe_url(x) is True   =>   isSafeUrl(x) is True

so a row where Python says safe and the browser does not is a hole, and the job exits 1. The
reverse — the server refusing something the browser would render — is *by design*: it costs a
link rendered as a plain label, and it is reported as information so a sudden jump is visible.

A pipe rather than a new dependency: node and python are both already installed in the job that
runs this, which is the same reason the repo's other cross-language guard
(``test_chat_source_rules.js``) is a plain script.
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from erpnext_enhancements.utils.url_safety import is_safe_url


def main() -> int:
	# The offending inputs are made of exactly the characters a Windows console cannot encode
	# (U+FF0F, U+FEFF, C0 controls), so a failing gate would die printing its own evidence and
	# report a traceback instead of the URL that broke it.
	try:
		sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
	except Exception:
		pass

	seed = "unknown"
	total = 0
	looser: list[str] = []
	stricter = 0

	for line in sys.stdin:
		line = line.strip()
		if not line:
			continue
		if line.startswith("#seed "):
			seed = line.split(" ", 1)[1]
			continue
		row = json.loads(line)
		value = "".join(chr(c) for c in row["cp"])
		total += 1
		py = is_safe_url(value)
		if py and not row["js"]:
			if len(looser) < 20:
				looser.append(repr(value))
		elif row["js"] and not py:
			stricter += 1

	if not total:
		print("fuzz: NO INPUT — the generator produced nothing, which is a broken gate, not a pass")
		return 2

	print(f"fuzz: seed={seed} inputs={total} stricter={stricter} looser={len(looser)}")

	if looser:
		print("")
		print("FAIL: the server accepted URLs the browser treats as unsafe.")
		print("This is the vulnerability direction — every one of these is a live href sink.")
		print(f"Reproduce with: node scripts/fuzz_url_safety.mjs {seed}")
		for value in looser:
			print(f"  {value}")
		return 1

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
