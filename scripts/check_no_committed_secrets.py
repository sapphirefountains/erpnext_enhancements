#!/usr/bin/env python3
"""Guard: no credential may exist in this repository — at HEAD **or anywhere in history**.

**No credential belongs in this repository, and one of them genuinely exists.** Google Drive
and Google Calendar share a service-account JSON key — the two modules build their clients from
`google.oauth2.service_account`, reading the key through
`drive_utils.get_service_account_info()`. It lives in a `Password` field on
`Project Folder Google Drive Settings`, encrypted at rest and never in the tree, which is
exactly the arrangement this script defends: the key is real, so "it is in the database, not
the repo" is a property somebody has to keep true.

(The retired Google Chat mirror was the opposite case — keyless by construction, with the VM
minting its own tokens through IAM Credentials `signJwt`. That is why it never appears in this
scanner's findings and why ADR 0011 could retire it with no credential to revoke. Do not
generalise it to the other Google integrations.)

This script is what keeps the rule enforced, because it only holds until somebody in a hurry
pastes a key file into the tree to "test it locally".

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

Three modes, one set of rules
-----------------------------

**Full history** (no flag) reads every blob in the object database: every ref, plus loose
objects no ref points at. It took ~22 s in August 2026 and 10-15 minutes by November,
because ``CHANGELOG.md`` is a 3.7 MB file with 1,400+ versions and every PR adds one — about
5 GB of text per run, nearly all of it read and found clean the day before.

**PR range** (``--since <ref>``) reads the working tree plus only the blobs reachable from
HEAD and not from ``<ref>`` (``git rev-list --objects <ref>..HEAD``). That is sound because
``main``'s history was gated as it landed: what a PR can add to the repository is exactly
what is in that range, intermediate commits included — a key committed and removed inside
the PR is still in it. The full walk stays as the backstop, on every push to ``main`` and
nightly. Both modes refuse a shallow clone, and the range mode refuses a ``<ref>`` it cannot
resolve rather than scanning an empty range.

The range deliberately leaves out **loose objects**, which the full walk reads. A push sends
only objects reachable from what is pushed, so a loose object a PR could publish is already
in the range; the rest are local leftovers (a reset, an amend) that never leave the machine,
and on the server they are the full walk's business. They are not cheap to include either: a
long-lived checkout here held ~13,000 loose objects (1.6 GB, mostly CHANGELOG versions), which
made a local ``--since origin/main`` take over 5 minutes against ~35 s on a fresh clone, for
no object a pull request could carry.

**Staged** (``--staged``) reads the index versions of the files about to be committed, and
nothing else. It is the pre-commit hook: the cheapest place to catch a key is before it is
a blob anyone else has.

The clean-blob cache
--------------------

``--cache [FILE]`` remembers the blobs already scanned clean and skips them next time. A
blob is content-addressed, so "clean once" is "clean for ever" — **under the same rules**.
The file therefore starts with a fingerprint of the rules (:func:`rules_fingerprint`: a hash
of this whole file, so any change to a pattern, the marker, the window or the code that
applies them invalidates it), and a file written under other rules is discarded whole.

One fact about a blob is not content: whether it sits under ``tests/`` or ``fixtures/``,
where a whole-file ``FAKE-DO-NOT-USE`` header is honoured. A blob found clean only there is
recorded as clean *there*, and is read again if it turns up anywhere else.

GitHub's own secret scanning
----------------------------

This script knows Sapphire's credentials; GitHub's secret scanning knows hundreds of
providers' token formats, and **push protection** refuses a push carrying one before it
reaches the remote at all, which nothing in CI can do. The repository is public, so both are
free. Both are repository settings, not code, and no change to this file turns them on: a
repository admin (Nikolas) enables them under Settings -> Advanced Security (called "Code
security" on older UIs) -> Secret Protection, then Push protection. As of 2026-11-02 both
were off. This script is not a substitute for them, nor they for it.

Run: ``python scripts/check_no_committed_secrets.py``  (exit 1 on a hit)
     ``python scripts/check_no_committed_secrets.py --since origin/main``  (what a PR adds)
     ``python scripts/check_no_committed_secrets.py --staged``  (the pre-commit hook)
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable, Iterator
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
#: few kilobytes; the cap exists so one pathological text file cannot turn a CI gate into a
#: timeout. Skips are counted and printed.
#:
#: **It was 4 MiB, and "printed" turned out to mean "unread".** ``CHANGELOG.md`` crossed
#: 4 MiB at v1.734.0 (2026-10-23) and from then on was skipped whole — in the working tree,
#: where the cap applies too, and in every later version in history — while the only trace
#: was a ``note:`` line nobody reads on a green job. The file every PR edits was the one
#: file this gate no longer examined. A binary is not the risk the cap guards against (it
#: is dropped on its first NUL byte), GitHub refuses any file over 100 MB, and the clean-blob
#: cache means a large blob is read once per rule set rather than on every run, so the cap
#: is set where only a genuinely absurd file reaches it.
MAX_BLOB_BYTES = 64 * 1024 * 1024

#: Bump by hand only if the cache file's *layout* changes. The rules need no version number
#: of their own: the fingerprint hashes this whole file (see :func:`rules_fingerprint`).
CACHE_FORMAT = "1"

#: Where ``--cache`` with no FILE keeps its list: inside the git directory, so it is never a
#: working-tree file this script would then scan, and never something ``git add .`` picks up.
DEFAULT_CACHE_NAME = "secret-scan-clean-blobs.txt"

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


def _blob_paths(*rev_list_args: str) -> dict[str, str]:
	"""``{sha: path}`` for every object ``git rev-list --objects`` names for these arguments.

	Only for reporting, and for the one path-dependent fact a scan has (test data or not). A
	blob that appears at several paths over its life is attributed to whichever git names
	first — good enough to send a human to the right file, and the sha is printed alongside
	so `git cat-file -p <sha>` always resolves the ambiguity.

	A blob found at a test-data path *and* somewhere else is attributed to the somewhere
	else. Test data is the one place a whole-file ``FAKE-DO-NOT-USE`` header is honoured, so
	a key that also sits under ``tests/`` must not borrow that excuse for its other copies.
	"""
	paths: dict[str, str] = {}
	for line in _git("rev-list", "--objects", *rev_list_args).splitlines():
		sha, _, rel = line.partition(" ")
		if not rel:
			continue
		if sha not in paths or (_is_test_data_path(paths[sha]) and not _is_test_data_path(rel)):
			paths[sha] = rel
	return paths


def _batch_check(shas: Iterable[str] | None) -> Iterator[tuple[str, str, int]]:
	"""``(sha, type, size)`` for each object named, or for every object when ``shas`` is None.

	Headers only: git reads no object contents to answer this, so filtering on it — by
	type, by size, against the cache — costs nothing for the blobs it rules out.
	"""
	for sha, kind, size, _ in _cat_file("--batch-check", shas):
		yield sha, kind, size


def _cat_file(batch: str, shas: Iterable[str] | None) -> Iterator[tuple[str, str, int, bytes]]:
	"""Stream ``git cat-file`` over the named objects (or all of them), in the order given.

	The object names go in through a **file**, not a pipe. Feeding a pipe while reading the
	other one deadlocks the moment git's output buffer fills and it stops reading its input;
	a file needs no writer at all. ``--batch-all-objects`` (``shas`` is None) reaches loose
	objects that no ref points at any more — the ones a force-push was supposed to erase.
	"""
	with tempfile.TemporaryFile() as feed:
		args = ["git", "-C", str(REPO), "cat-file", batch, "--buffer"]
		if shas is None:
			args += ["--batch-all-objects", "--unordered"]
		else:
			feed.write("".join(f"{sha}\n" for sha in shas).encode())
			feed.seek(0)
		proc = subprocess.Popen(args, stdin=feed, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
		assert proc.stdout is not None
		finished = False
		try:
			while True:
				header = proc.stdout.readline()
				if not header:
					break
				parts = header.decode("utf-8", errors="replace").split()
				if len(parts) != 3:
					continue  # "<sha> missing"
				sha, kind, size_text = parts
				size = int(size_text)
				payload = b""
				if batch == "--batch":
					payload = proc.stdout.read(size)
					proc.stdout.read(1)  # the trailing newline git writes after each object
				yield sha, kind, size, payload
			finished = True
		finally:
			proc.stdout.close()
			# A git that failed must never read as a clean scan of what it did not return.
			# (Only checked on a full read: a consumer that stops early closes the pipe, and
			# the SIGPIPE that follows is not an error.)
			if proc.wait() != 0 and finished:
				raise subprocess.CalledProcessError(proc.returncode, args)


def rules_fingerprint(source: bytes | None = None) -> str:
	"""The key a clean-blob cache is valid under: a hash of this whole file.

	The whole file, not a list of the constants, because what makes a blob "clean" is the
	patterns *and* the code that applies them. A change to :func:`scan_text`, to the
	placeholder logic or to the window is as much a rule change as a new regex, and a
	fingerprint built from the constants alone would carry a cache straight through it.
	Over-invalidating costs one slow run; under-invalidating is a blob a stronger rule never
	reads. Line endings are normalised so a Windows checkout agrees with CI.
	"""
	if source is None:
		source = Path(__file__).read_bytes()
	digest = hashlib.sha256(f"check_no_committed_secrets/cache-format-{CACHE_FORMAT}\n".encode())
	digest.update(source.replace(b"\r\n", b"\n"))
	return digest.hexdigest()[:16]


class CleanBlobCache:
	"""Blob shas already scanned clean under the current rules. See the module docstring.

	One entry per line: ``<sha>`` when the blob was clean read from an ordinary path, or
	``<sha> t`` when it was clean read from test data, where a whole-file marker is honoured.
	The second kind only excuses the blob at a test-data path. The first line carries the
	rules fingerprint, and a file whose first line does not match is ignored entirely.
	"""

	def __init__(self, path: Path | None, fingerprint: str) -> None:
		self.path = path
		self.fingerprint = fingerprint
		self.entries: set[str] = set()
		self.added: set[str] = set()
		self.status = "off"

	@property
	def header(self) -> str:
		return f"# check_no_committed_secrets clean blobs, rules {self.fingerprint}"

	def load(self) -> None:
		if self.path is None:
			return
		try:
			lines = self.path.read_text(encoding="ascii").splitlines()
		except FileNotFoundError:
			self.status = f"{self.path}: none yet"
			return
		except (OSError, UnicodeDecodeError) as exc:
			self.status = f"{self.path}: unreadable ({exc}), ignored"
			return
		if not lines or lines[0] != self.header:
			self.status = f"{self.path}: written under different rules, ignored (rules {self.fingerprint})"
			return
		self.entries = {line.strip() for line in lines[1:] if line.strip()}
		self.status = f"{self.path}: {len(self.entries)} clean blobs (rules {self.fingerprint})"

	def is_clean(self, sha: str, rel: str) -> bool:
		if sha in self.entries:
			return True
		return _is_test_data_path(rel) and f"{sha} t" in self.entries

	def mark_clean(self, sha: str, rel: str) -> None:
		if self.path is not None:
			self.added.add(f"{sha} t" if _is_test_data_path(rel) else sha)

	def save(self) -> int:
		"""Write the list back, atomically. Returns how many entries are new."""
		if self.path is None:
			return 0
		fresh = self.added - self.entries
		self.path.parent.mkdir(parents=True, exist_ok=True)
		tmp = self.path.with_name(self.path.name + ".tmp")
		body = "\n".join([self.header, *sorted(self.entries | self.added)]) + "\n"
		tmp.write_text(body, encoding="ascii", newline="\n")
		tmp.replace(self.path)
		return len(fresh)


@dataclass
class BlobCounts:
	"""What the history half of a run actually did, printed so "0 findings" can be told
	apart from "0 blobs"."""

	scanned: int = 0
	cached: int = 0
	oversize: int = 0


def iter_blobs(
	objects: Iterable[tuple[str, str, int]],
	paths: dict[str, str],
	cache: CleanBlobCache,
	counts: BlobCounts,
) -> Iterator[tuple[str, str, str, str]]:
	"""Decode the blobs among ``objects`` that need reading: ``(label, text, sha, rel)``.

	Everything that can rule a blob out is decided from its header first — not a blob, a
	noisy path, over the size cap, or already in the cache — so the contents of a skipped
	blob are never read. That, not the regexes, is where the time went.
	"""
	wanted: list[str] = []
	for sha, kind, size in objects:
		if kind != "blob":
			continue
		rel = paths.get(sha, "")
		if _is_noisy(rel):
			continue
		if size > MAX_BLOB_BYTES:
			counts.oversize += 1
			continue
		if cache.is_clean(sha, rel):
			counts.cached += 1
			continue
		wanted.append(sha)

	for sha, _kind, _size, payload in _cat_file("--batch", wanted):
		rel = paths.get(sha, "")
		if b"\0" in payload:
			cache.mark_clean(sha, rel)
			continue
		counts.scanned += 1
		label = f"{rel} (blob {sha[:12]})" if rel else f"(unreachable blob {sha[:12]})"
		yield label, payload.decode("utf-8", errors="replace"), sha, rel


def history_objects() -> tuple[Iterable[tuple[str, str, int]], dict[str, str]]:
	"""The full walk: every object in the database, named where any ref names it."""
	return list(_batch_check(None)), _blob_paths("--all")


def range_objects(since: str) -> tuple[Iterable[tuple[str, str, int]], dict[str, str]]:
	"""The PR range: objects reachable from HEAD and not from ``since``. No loose objects (see
	the module docstring): a push carries only what is reachable, and that is all in here."""
	paths = _blob_paths(f"{since}..HEAD")
	return list(_batch_check(paths)), paths


def iter_staged() -> Iterator[tuple[str, str]]:
	"""The index version of every file added or changed in the index, as it will be committed.

	The index, not the working tree: ``git add -p`` can stage a key while the file on disk
	no longer has it, and what is staged is what the commit will carry.
	"""
	raw = _git("diff", "--cached", "--raw", "-z", "--no-abbrev", "--no-renames", "--diff-filter=d")
	tokens = raw.split("\0")
	staged: list[tuple[str, str]] = []
	for meta, rel in zip(tokens[0::2], tokens[1::2], strict=False):
		fields = meta.split()
		if len(fields) < 5 or fields[1] == "160000":  # a submodule's entry names a commit
			continue
		if not _is_noisy(rel):
			staged.append((fields[3], rel))
	# One entry per *path*, not per blob: the same contents staged under tests/ and under
	# src/ are two files to judge, and only one of them may honour a whole-file marker.
	blobs = _cat_file("--batch", [sha for sha, _ in staged])
	for (sha, rel), (got, _kind, size, payload) in zip(staged, blobs, strict=True):
		if got != sha:
			raise RuntimeError(f"git cat-file returned {got} for staged blob {sha} ({rel})")
		if size > MAX_BLOB_BYTES or b"\0" in payload:
			continue
		yield rel, payload.decode("utf-8", errors="replace")


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
	The range mode refuses too: on a shallow clone ``<ref>..HEAD`` can stop at the graft
	instead of at ``<ref>``, which is a range nobody asked for and nobody can check.
	"""
	if _git("rev-parse", "--is-shallow-repository").strip() == "true":
		return (
			"this is a SHALLOW clone, so the history scan would examine one commit and "
			"report a clean result it never earned.\n"
			"    In CI, set `fetch-depth: 0` on actions/checkout for this job.\n"
			"    Locally, run `git fetch --unshallow`."
		)
	return None


def _refuse_unknown_ref(since: str) -> str | None:
	"""The range mode needs its base. A ref that does not resolve is not an empty range."""
	try:
		_git("rev-parse", "--verify", "--quiet", f"{since}^{{commit}}")
	except subprocess.CalledProcessError:
		return (
			f"`{since}` does not name a commit here, so there is no range to scan.\n"
			"    In CI, fetch the base first: `git fetch --no-tags origin "
			"+refs/heads/main:refs/remotes/origin/main`.\n"
			"    Locally, run `git fetch origin`, or scan everything without `--since`."
		)
	return None


def _short(ref: str) -> str:
	return _git("rev-parse", "--short=12", f"{ref}^{{commit}}").strip()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Fail when credential material is in this repository: the working tree, "
		"its history, or the files staged for the next commit."
	)
	mode = parser.add_mutually_exclusive_group()
	mode.add_argument(
		"--since",
		metavar="REF",
		help="scan the working tree plus only the blobs reachable from HEAD and not from REF "
		"(what a pull request adds). Default: every blob in history.",
	)
	mode.add_argument(
		"--staged",
		action="store_true",
		help="scan only the staged versions of added or changed files (the pre-commit hook)",
	)
	parser.add_argument(
		"--cache",
		nargs="?",
		const="",
		metavar="FILE",
		help="skip blobs this FILE lists as already scanned clean under the same rules, and add "
		f"the ones found clean now. With no FILE: {DEFAULT_CACHE_NAME} in the git directory.",
	)
	parser.add_argument(
		"--rules-hash",
		action="store_true",
		help="print the rules fingerprint a cache is keyed on, and exit (CI uses it as the cache key)",
	)
	# For the tests, which build throwaway repositories. Not for everyday use.
	parser.add_argument("--repo", type=Path, help=argparse.SUPPRESS)
	return parser.parse_args(argv)


def _cache_path(arg: str | None) -> Path | None:
	if arg is None:
		return None
	if arg:
		return Path(arg).expanduser()
	common = Path(_git("rev-parse", "--git-common-dir").strip())
	return (common if common.is_absolute() else REPO / common) / DEFAULT_CACHE_NAME


def _report(findings: list[Finding]) -> None:
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


# --------------------------------------------------------------------------- entrypoint


def main(argv: list[str] | None = None) -> int:
	global REPO
	args = _parse_args(argv)
	if args.repo is not None:
		REPO = args.repo.resolve()

	fingerprint = rules_fingerprint()
	if args.rules_hash:
		print(fingerprint)
		return 0

	started = time.monotonic()
	findings: list[Finding] = []

	if args.staged:
		staged_count = 0
		for where, text in iter_staged():
			staged_count += 1
			findings.extend(scan_text(where, text))
		if findings:
			_report(findings)
			return 1
		print(f"no secrets staged ({staged_count} staged files scanned)")
		return 0

	refusal = _refuse_shallow() or (args.since and _refuse_unknown_ref(args.since))
	if refusal:
		print(f"\ncannot scan for committed secrets: {refusal}\n", file=sys.stderr)
		return 1

	cache = CleanBlobCache(_cache_path(args.cache), fingerprint)
	cache.load()

	if args.since:
		commits = _git("rev-list", "--count", f"{args.since}..HEAD").strip()
		print(
			f"mode: PR range {args.since}..HEAD ({_short(args.since)}..{_short('HEAD')}, "
			f"{commits} commits) + working tree"
		)
		objects, paths = range_objects(args.since)
	else:
		print("mode: full history (every object in the database: all refs + loose) + working tree")
		objects, paths = history_objects()
	print(f"cache: {cache.status}")

	tree_count = 0
	for where, text in iter_working_tree():
		tree_count += 1
		findings.extend(scan_text(where, text))

	counts = BlobCounts()
	for where, text, sha, rel in iter_blobs(objects, paths, cache, counts):
		blob_findings = scan_text(where, text)
		if blob_findings:
			findings.extend(blob_findings)
		else:
			cache.mark_clean(sha, rel)

	new_entries = cache.save()
	if cache.path is not None:
		print(f"cache: {new_entries} newly clean blobs recorded in {cache.path}")
	if counts.oversize:
		print(f"note: {counts.oversize} blob(s) over {MAX_BLOB_BYTES} bytes were not scanned")

	elapsed = time.monotonic() - started
	if findings:
		_report(findings)
		return 1

	print(
		f"no committed secrets ({tree_count} working-tree files, {counts.scanned} history blobs scanned, "
		f"{counts.cached} skipped as already scanned clean; {elapsed:.1f}s)"
	)
	return 0


if __name__ == "__main__":
	sys.exit(main())
