#!/usr/bin/env python3
"""Guard: no credential may exist in this repository — at HEAD **or anywhere in history**.

ADR 0009 §G.2 and Phase 1 §4.4: the Google integration is deliberately **keyless**. The VM
mints its own tokens through IAM Credentials `signJwt`, so there is no service-account JSON
key to lose, and `Chat Settings` holds identifiers only — project ids, service-account
*emails*, topic names, the audience URL. That design removes the credential; this script is
what keeps it removed, because the design only holds until somebody in a hurry pastes a key
file into the tree to "test it locally".

**History, not just HEAD, and that distinction is the whole point.** `git rm` does not
un-publish anything. A key committed and reverted an hour later is still in the pack file,
still in every clone, and still on GitHub — and finding it is a *rotation incident*, not a
review comment. So this walks every blob reachable from every ref, plus loose objects the
refs no longer point at.

Why the patterns are value-shaped rather than keyword-shaped
------------------------------------------------------------

The obvious implementation — grep for `client_secret` — was written, run, and thrown away:
it matched 30 places in this repo, every one of them a *field name*
(`quickbooks_online_settings.json`, `mdm_integration/client.py`, a Terraform variable, the
chat module's own paste-detection marker list). A guard that cries wolf 30 times is a guard
people learn to skim past, which is the same rot as an advisory lint job nobody reads.

So a rule fires on a key **with a credential-shaped value attached**: `client_secret` next
to a colon or an equals sign and a quoted literal of real length that is not a placeholder.
`"type": "service_account"` fires only when the same blob also carries key material, because
the bare marker string appears in two legitimate places here (a test fixture, and
`chat/doctype/chat_settings/chat_settings_rules.py`, which lists it precisely so it can
*refuse* a pasted credential) — while a genuine service-account key file always carries the
PEM block and `private_key_id` alongside it.

A PEM private-key header is the one rule with no qualification. There is no benign reason
for one to be in this repository.

The known failure mode of a script like this is **passing because the pattern is wrong**, so:

  * it refuses to run on a shallow clone rather than reporting a clean history it never
    read (`actions/checkout` is depth-1 by default — the CI job must set `fetch-depth: 0`);
  * it prints how many blobs and working-tree files it actually examined, so "0 findings"
    can be distinguished from "0 files";
  * and it is proven by planting a fake key and watching it fire, not by reading it.

The PEM pattern is assembled from two fragments below. That is not obfuscation — it is so
this file does not match itself and need an allowlist entry, because an allowlist entry on
the scanner is a hole in the scanner.

Allowlisting
------------

One mechanism, content-based: a finding is suppressed when ``FAKE-DO-NOT-USE`` appears
within its window. Test fixtures that need credential-shaped data must say so in the data
itself, where a reviewer reading the fixture sees it — not in a list in this file that
nobody opens. The *whole-file* form of that marker only counts under ``tests/`` or
``fixtures/``; anywhere else the marker must sit beside the value it excuses, because a
header saying "fake" above a real key is the cheapest way to blind this script.
``ALLOWLISTED_PATHS`` exists for the case that mechanism cannot cover and ships empty;
see the note there before adding to it.

Run: ``python scripts/check_no_committed_secrets.py``  (exit 1 on a hit)
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Suppresses a finding when it appears beside the value, or in the file's opening lines.
#: The fixture declares its own fakeness, where a reviewer reading the fixture sees it.
FAKE_MARKER = "FAKE-DO-NOT-USE"

#: How many lines either side of a hit count as "beside the value".
WINDOW_RADIUS = 3

#: How many opening lines count as a file-level declaration (docstring or header comment).
HEADER_LINES = 8

#: Path segments where the whole-file form of :data:`FAKE_MARKER` is honoured. Everywhere
#: else only the beside-the-value form counts — see :func:`_has_file_level_marker`.
FILE_LEVEL_MARKER_DIRS = frozenset({"tests", "test", "fixtures"})

#: Paths exempted wholesale, each with the reason it earned an exemption.
#:
#: **Ships empty, and the bar for adding one is high.** Every exemption considered so far
#: was better solved by making the rule value-shaped, which is strictly safer: a path
#: exemption blinds the scanner to a *real* key committed to that path later. If you are
#: about to add one, check first whether the finding is a keyword match with no credential
#: value attached — if so, the rule is what needs fixing.
ALLOWLISTED_PATHS: dict[str, str] = {}

#: Blobs larger than this are reported as skipped rather than scanned. A credential is a
#: few kilobytes; the cap exists so one checked-in binary cannot turn a CI gate into a
#: timeout. Skips are counted and printed — never silent.
MAX_BLOB_BYTES = 4 * 1024 * 1024

# The PEM header, in two pieces, so this file is not its own first finding. Covers the
# flavours `openssl` and `ssh-keygen` actually emit.
_PEM_OPEN = "-----BEGIN "
_PEM_TAIL = "PRIVATE KEY-----"
PEM_RE = re.compile(re.escape(_PEM_OPEN) + r"(?:RSA |DSA |EC |OPENSSH |ENCRYPTED |PGP )?" + re.escape(_PEM_TAIL))

# `"type": "service_account"` in either spacing, and in the single-quoted form a Python
# fixture uses. Qualified by KEY_MATERIAL_RE below.
SERVICE_ACCOUNT_RE = re.compile(r"""["']type["']\s*:\s*["']service_account["']""")

# What makes a service-account marker a real key file: an actual key, or the id of one.
KEY_MATERIAL_RE = re.compile(
	r"""["']private_key(?:_id)?["']\s*:\s*["'][^"']{8,}["']""" + "|" + PEM_RE.pattern
)

#: A secret-shaped value: long enough to be real, and not a reference to one.
#:
#: **Two alternatives, and the second one is the whole point.** The quoted form is what a
#: JSON key file or a Python fixture looks like. The unquoted form is what a `.env`, a
#: `docker-compose.yml`, a shell script or a Cloud Build substitution looks like —
#: ``CLIENT_SECRET=GOCSPX-...`` with no quotes anywhere, which the quoted-only pattern
#: matched not at all. A rule that only fires on quoted values is a rule that misses the
#: file formats credentials are most often pasted into.
#:
#: The value lands in group 1 (quoted) **or** group 2 (unquoted); :func:`_value_of` picks
#: whichever fired. Any new rule built on this must go through that helper.
_VALUE = r"""(?:["']([^"'\s]{16,})["']|([A-Za-z0-9_\-./+=]{16,}))"""

#: Rules whose finding is a key with a credential-shaped value attached. Each is
#: `(name, regex, why it matters)`; the regex must expose the value through :data:`_VALUE`
#: so :func:`_value_of` can read it and :func:`_is_placeholder` can wave through the
#: obvious non-secrets.
#: Output is ASCII on purpose: this prints into CI logs and Windows consoles, where a
#: mojibake em dash in a SECURITY finding is one more reason to not read it.
#:
#: All three are case-insensitive. The unquoted arm of :data:`_VALUE` exists for `.env`
#: files, shell scripts and Cloud Build substitutions — and in every one of those the key
#: is SHOUTED (``GOOGLE_CLIENT_SECRET=…``, ``_CLIENT_SECRET: …``). A lowercase-only key
#: pattern would have made the new arm reach almost none of the files it was added for.
VALUE_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
	(
		"private_key_id",
		re.compile(r"""["']?private_key_id["']?\s*[:=]\s*""" + _VALUE, re.IGNORECASE),
		"the key id from a service-account JSON; the file it came from is the incident",
	),
	(
		"private_key",
		re.compile(r"""["']?private_key["']?\s*[:=]\s*""" + _VALUE, re.IGNORECASE),
		"a private key, or the field that carries one",
	),
	(
		"client_secret",
		re.compile(r"""["']?[a-z0-9_]*client_secret["']?\s*[:=]\s*""" + _VALUE, re.IGNORECASE),
		"an OAuth client secret. Field NAMES are fine; a value is not",
	),
)

#: A long unbroken base64 run — the shape of a key, a token or a signed blob.
BASE64_RUN_RE = re.compile(r"[A-Za-z0-9+/]{64,}={0,2}")

#: Substrings that mark a value as a reference or a placeholder rather than a credential.
#: Deliberately narrow: a real secret that happens to contain "test" is not a thing we can
#: rule out, so the list names only tokens a human writes *instead of* a secret.
PLACEHOLDER_TOKENS: tuple[str, ...] = (
	"fake",
	"example",
	"dummy",
	"placeholder",
	"changeme",
	"change_me",
	"redacted",
	"your-",
	"your_",
	"xxxxxxxx",
	"do-not-use",
	"do_not_use",
	"notasecret",
	"sample",
)

#: An expression that *refers* to a secret rather than being one: a dotted attribute
#: chain, which is how HCL, Python and JS all reach for a value held somewhere else
#: (``google_iap_client.iap_client.secret``, ``process.env.CLIENT_SECRET``). Only the
#: unquoted arm of :data:`_VALUE` can produce these, and it produces them constantly in
#: Terraform.
#:
#: Segments are length-capped because a JWT is dot-separated too, and a JWT is a
#: credential. Its segments run to hundreds of characters; an identifier does not.
_REFERENCE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}(?:\.[A-Za-z_][A-Za-z0-9_]{0,39})+$")

#: Extensions whose contents are compiled or minified and produce base64-shaped noise.
#:
#: ``.lock`` and ``.map`` were here and were removed. Neither is noise in the sense that
#: matters: a lockfile carries registry URLs, and a private registry URL carries a token;
#: a sourcemap embeds the original sources, so a secret in a source file is in the `.map`
#: too even after the source is fixed. Both are plain text a value rule reads correctly,
#: and skipping them meant a leak in either was never examined in the tree *or* in history.
NOISY_SUFFIXES = frozenset({".pyc", ".min.js", ".min.css", ".png", ".jpg", ".svg"})


@dataclass(frozen=True)
class Finding:
	"""One hit, with enough context for a human to judge it without opening the file."""

	rule: str
	why: str
	where: str
	line_no: int
	excerpt: str

	#: Set when identical findings elsewhere were collapsed into this one.
	also_at: tuple[str, ...] = ()

	def render(self) -> str:
		lines = [
			f"  {self.where}:{self.line_no}",
			f"    rule: {self.rule} - {self.why}",
			f"    line: {self.excerpt}",
		]
		if self.also_at:
			shown = list(self.also_at[:5])
			lines.append(f"    also at: {', '.join(shown)}")
			if len(self.also_at) > 5:
				lines.append(f"             ...and {len(self.also_at) - 5} more")
		return "\n".join(lines)


# --------------------------------------------------------------------------- scanning


def _is_placeholder(value: str, key_name: str = "") -> bool:
	"""Is this value a stand-in rather than a credential?

	Five ways to be one, and the last two were learned from this repository's own contents:

	1. it names itself a placeholder (:data:`PLACEHOLDER_TOKENS`);
	2. it is an interpolation or a template hole (``${…}``, ``{{…}}``, ``<…>``);
	3. it is one character repeated (``"xxxxxxxxxxxxxxxx"``);
	4. **it is a dotted reference to a value held elsewhere** (:data:`_REFERENCE_RE`).
	   ``oauth2_client_secret = google_iap_client.iap_client.secret`` in
	   ``modules/net-lb-app-ext/`` is Terraform wiring one resource to another, and it is
	   the shape every unquoted assignment in an HCL or JS file takes. Without this, the
	   unquoted arm of :data:`_VALUE` — which exists to catch ``CLIENT_SECRET=GOCSPX-…``
	   in files that have no quotes — would make this gate permanently red on config
	   that contains no credential at all;
	5. **it restates the name of the field it is assigned to.** A deleted
	   ``tests/test_integrations.py`` contains ``client_secret = "test_client_secret"``,
	   which is a *description of the field*, not a credential — and it lives in a git blob
	   nobody can edit, so a rule that flags it makes this gate permanently and
	   uncorrectably red. A gate that cannot go green gets deleted, which costs more than
	   the false positive. A real secret does not contain the string ``client_secret``.
	"""
	lowered = value.lower()
	if key_name and key_name.lower() in lowered:
		return True
	if any(token in lowered for token in PLACEHOLDER_TOKENS):
		return True
	if value.startswith(("${", "{{", "<", "$(")) or value.endswith(("}", ">")):
		return True
	if _REFERENCE_RE.match(value):
		return True
	return len(set(value)) <= 2


def _value_of(match: re.Match[str]) -> str:
	"""The credential-shaped value a :data:`_VALUE` match found, quoted form or not.

	:data:`_VALUE` is an alternation, so exactly one of its two groups is populated and the
	other is ``None``. Every consumer must come through here — reading ``group(1)`` directly
	silently ignores every unquoted hit, which is the blind spot the alternation was added
	to close.
	"""
	return match.group(1) or match.group(2) or ""


def _clip(text: str, width: int = 120) -> str:
	"""Never print a whole secret into a CI log. The point is to locate it, not to copy it."""
	collapsed = " ".join(text.split())
	return collapsed if len(collapsed) <= width else collapsed[:width] + "..."


def _window(lines: list[str], index: int, radius: int = WINDOW_RADIUS) -> str:
	low = max(0, index - radius)
	return "\n".join(lines[low : index + radius + 1])


def _is_test_data_path(where: str) -> bool:
	"""Is this path somewhere a file of deliberately fake credentials legitimately lives?

	``where`` may carry a ``" (blob <sha>)"`` suffix from the history walk, and may be the
	empty-path form for an unreachable blob — which is never test data, because nothing
	names it.
	"""
	path = where.split(" (blob ", 1)[0].lower().replace("\\", "/")
	if not path or path.startswith("("):
		return False
	segments = path.split("/")
	return any(segment in FILE_LEVEL_MARKER_DIRS for segment in segments)


def _has_file_level_marker(where: str, lines: list[str]) -> bool:
	"""Is the whole file declared fake by a marker in its opening lines?

	Two placements are honoured, and the split matters. A marker **beside the value**
	(within :data:`WINDOW_RADIUS` lines) exempts that value only, which is the precise form.
	A marker in the file's **first few lines** — a module docstring or a header comment —
	exempts the file, which is what a fixture file full of credential-shaped data actually
	needs; requiring the author to repeat it beside every literal is how a fixture ends up
	with the marker in the one place nobody put it, and a red gate they then work around.

	The header window is short on purpose: it has to be a *declaration*, not a mention
	buried at line 400 that silently blinds everything above and below it.

	**And the whole-file form is confined to test data** (:data:`FILE_LEVEL_MARKER_DIRS`),
	because it is the strongest suppression this script has: it returns before *any* rule
	runs, the PEM rule included. Anywhere else, a real key pasted below a header that says
	``FAKE-DO-NOT-USE`` would be invisible — and "put the marker at the top of the file"
	is exactly the advice a hurried person follows. Outside those directories the
	per-value window still applies, which is the precise form and requires the author to
	declare each literal where a reviewer reads it.
	"""
	if not _is_test_data_path(where):
		return False
	return FAKE_MARKER in "\n".join(lines[:HEADER_LINES])


def scan_text(where: str, text: str) -> list[Finding]:
	"""Every rule, against one file's or one blob's contents.

	``where`` is only ever used for reporting and for :data:`ALLOWLISTED_PATHS`; the rules
	themselves are content-based, so a key hidden under an innocent filename is still found.
	"""
	if where in ALLOWLISTED_PATHS:
		return []

	findings: list[Finding] = []
	lines = text.splitlines()
	if _has_file_level_marker(where, lines):
		return []
	has_key_material = bool(KEY_MATERIAL_RE.search(text))

	for index, line in enumerate(lines):
		window = _window(lines, index)
		if FAKE_MARKER in window:
			continue
		line_no = index + 1

		if PEM_RE.search(line):
			findings.append(
				Finding(
					"pem-private-key",
					"a PEM private-key block. There is no benign reason for one to be here",
					where,
					line_no,
					_clip(line),
				)
			)

		if SERVICE_ACCOUNT_RE.search(line) and has_key_material:
			findings.append(
				Finding(
					"service-account-key-file",
					"a service-account JSON marker alongside actual key material",
					where,
					line_no,
					_clip(line),
				)
			)

		for name, pattern, why in VALUE_RULES:
			match = pattern.search(line)
			if match and not _is_placeholder(_value_of(match), name):
				findings.append(Finding(name, why, where, line_no, _clip(line)))

		# A long base64 run next to a Google endpoint: a signed JWT, an access token or a
		# raw key, pasted into a request example that was then committed.
		if "googleapis" in window.lower() and BASE64_RUN_RE.search(line):
			findings.append(
				Finding(
					"base64-near-googleapis",
					"a long base64 blob within two lines of a googleapis reference",
					where,
					line_no,
					_clip(line),
				)
			)

	return findings


def _is_noisy(path: str) -> bool:
	lowered = path.lower()
	return any(lowered.endswith(suffix) for suffix in NOISY_SUFFIXES)


def _git(*args: str) -> str:
	"""Run git in the repo, returning stdout. Raises on a non-zero exit — a broken git
	invocation must never read as a clean scan."""
	result = subprocess.run(
		["git", "-C", str(REPO), *args],
		capture_output=True,
		check=True,
	)
	return result.stdout.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- sources


def iter_working_tree() -> Iterator[tuple[str, str]]:
	"""Tracked files **and** untracked-but-not-ignored ones.

	Untracked files are not committed yet, which is exactly why they are worth scanning: a
	key file sitting in the tree is a hit this script can still catch *before* it becomes a
	rotation incident. Ignored files are excluded — that is what ignoring means, and
	`.gitignore` carries the key-file filename patterns as belt and braces.
	"""
	listing = _git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
	for rel in listing.split("\0"):
		if not rel or _is_noisy(rel):
			continue
		path = REPO / rel
		try:
			if not path.is_file() or path.stat().st_size > MAX_BLOB_BYTES:
				continue
			data = path.read_bytes()
		except OSError:
			continue
		if b"\0" in data:
			continue
		yield rel, data.decode("utf-8", errors="replace")


def _blob_paths() -> dict[str, str]:
	"""``{sha: path}`` for every blob reachable from any ref.

	Only for reporting. A blob that appears at several paths over its life is attributed to
	whichever git names first — good enough to send a human to the right file, and the sha
	is printed alongside so `git cat-file -p <sha>` always resolves the ambiguity.
	"""
	paths: dict[str, str] = {}
	for line in _git("rev-list", "--objects", "--all").splitlines():
		sha, _, rel = line.partition(" ")
		if rel and sha not in paths:
			paths[sha] = rel
	return paths


def iter_history_blobs() -> Iterator[tuple[str, str]]:
	"""Every blob in the object database, decoded, with a best-effort path.

	``--batch-all-objects`` rather than feeding shas back in: it needs no stdin, so there is
	no writer-thread deadlock when git's output buffer fills, and it reaches loose objects
	that no ref points at any more — the ones a force-push was supposed to erase.
	"""
	paths = _blob_paths()
	proc = subprocess.Popen(
		["git", "-C", str(REPO), "cat-file", "--batch-all-objects", "--batch", "--buffer", "--unordered"],
		stdout=subprocess.PIPE,
		stderr=subprocess.DEVNULL,
	)
	assert proc.stdout is not None
	skipped = 0
	try:
		while True:
			header = proc.stdout.readline()
			if not header:
				break
			parts = header.decode("utf-8", errors="replace").split()
			if len(parts) != 3:
				continue
			sha, kind, size_text = parts
			size = int(size_text)
			payload = proc.stdout.read(size)
			proc.stdout.read(1)  # the trailing newline git writes after each object
			if kind != "blob":
				continue
			rel = paths.get(sha, "")
			if _is_noisy(rel):
				continue
			if size > MAX_BLOB_BYTES:
				skipped += 1
				continue
			if b"\0" in payload:
				continue
			label = f"{rel} (blob {sha[:12]})" if rel else f"(unreachable blob {sha[:12]})"
			yield label, payload.decode("utf-8", errors="replace")
	finally:
		proc.stdout.close()
		proc.wait()
	if skipped:
		print(f"note: {skipped} blob(s) over {MAX_BLOB_BYTES} bytes were not scanned")


def collapse(findings: list[Finding]) -> list[Finding]:
	"""One entry per distinct offending line, with the other locations listed under it.

	A file that survived 700 commits unchanged is one blob, but a line edited around stays
	the same across a dozen. Printing the identical line a dozen times buries how many
	*distinct* problems there are, which is the number a human is actually triaging.
	"""
	grouped: dict[tuple[str, str], Finding] = {}
	extras: dict[tuple[str, str], list[str]] = {}
	for finding in findings:
		key = (finding.rule, finding.excerpt)
		if key not in grouped:
			grouped[key] = finding
			extras[key] = []
		else:
			extras[key].append(f"{finding.where}:{finding.line_no}")
	return [
		Finding(f.rule, f.why, f.where, f.line_no, f.excerpt, tuple(extras[key]))
		for key, f in grouped.items()
	]


def _refuse_shallow() -> str | None:
	"""A shallow clone cannot answer the question this script exists to ask.

	``actions/checkout`` clones at depth 1 by default. Scanning "history" there reads one
	commit and reports success — the exact failure this script is supposed to be immune to.
	"""
	if _git("rev-parse", "--is-shallow-repository").strip() == "true":
		return (
			"this is a SHALLOW clone, so the history scan would examine one commit and "
			"report a clean result it never earned.\n"
			"    In CI, set `fetch-depth: 0` on actions/checkout for this job.\n"
			"    Locally, run `git fetch --unshallow`."
		)
	return None


# --------------------------------------------------------------------------- entrypoint


def main() -> int:
	shallow = _refuse_shallow()
	if shallow:
		print(f"\ncannot scan for committed secrets: {shallow}\n", file=sys.stderr)
		return 1

	findings: list[Finding] = []

	tree_count = 0
	for where, text in iter_working_tree():
		tree_count += 1
		findings.extend(scan_text(where, text))

	blob_count = 0
	for where, text in iter_history_blobs():
		blob_count += 1
		findings.extend(scan_text(where, text))

	if findings:
		print("\nCREDENTIAL MATERIAL FOUND IN THIS REPOSITORY:\n", file=sys.stderr)
		for finding in collapse(findings):
			print(finding.render(), file=sys.stderr)
			print("", file=sys.stderr)
		print(
			"A hit inside `(blob ...)` means the credential is IN GIT HISTORY. Deleting the\n"
			"file does not help - it is in every clone and on the remote. Treat it as a\n"
			"ROTATION INCIDENT: revoke and re-issue the credential first, then decide what\n"
			"to do about the history.\n"
			"\n"
			"A hit in the working tree with no blob suffix is not committed yet. Remove it\n"
			"before committing. If it is a deliberately fake test fixture, put "
			f"`{FAKE_MARKER}`\n"
			"in the fixture itself.\n",
			file=sys.stderr,
		)
		return 1

	print(
		f"no committed secrets ({tree_count} working-tree files, {blob_count} history blobs scanned)"
	)
	return 0


if __name__ == "__main__":
	sys.exit(main())
