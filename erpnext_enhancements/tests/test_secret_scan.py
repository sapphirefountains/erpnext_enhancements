"""Bench-free tests for ``scripts/check_no_committed_secrets.py``: the three modes and the cache.

The scanner is a hard CI gate whose known failure mode is passing because it never read
something. So every test here builds a throwaway git repository, plants credential-shaped
data in it, runs the real script against it as a subprocess, and reads the exit code:

- full history (no flag) still finds each of the three planted kinds — a service-account key
  file, an OAuth client secret, a base64 token beside a googleapis URL — after the file was
  deleted again, and still refuses a shallow clone;
- the PR range (``--since``) finds a key committed and removed *inside* the range, and leaves
  what the base already had and unreachable loose objects to the full walk, which finds both,
  and refuses an unknown base or a shallow clone rather than scanning an empty range;
- the clean-blob cache skips what it lists, is discarded whole when the rules change, and an
  entry recorded at a test-data path does not excuse the same blob anywhere else;
- ``--staged`` reads the index, not the file on disk.

The planted values are assembled at run time from fragments, so this file never contains a
credential-shaped literal of its own: the scanner reads this file too, and so may GitHub's.

Run: python -m unittest erpnext_enhancements.tests.test_secret_scan -v
"""

import base64
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_no_committed_secrets.py"

_PEM_BEGIN = "-----BEGIN " + "PRIVATE KEY-----"
_PEM_END = "-----END " + "PRIVATE KEY-----"
_FIELD_SECRET = "client_" + "secret"
_KEY = "private_" + "key"
_KEY_ID = _KEY + "_id"


def _service_account_key() -> str:
	body = base64.b64encode(bytes(range(7, 247))).decode()
	return (
		"{\n"
		'  "type": "service_' + 'account",\n'
		'  "project_id": "sapphire-test",\n'
		f'  "{_KEY_ID}": "{"3f9a" * 10}",\n'
		f'  "{_KEY}": "{_PEM_BEGIN}\\n{body}\\n{_PEM_END}\\n",\n'
		'  "client_email": "scanner@sapphire-test.iam.gserviceaccount.com"\n'
		"}\n"
	)


def _client_secret() -> str:
	value = "GOC" + "SPX-" + "k3Jd9Lm2Qp8Rx7Vt4Wn6Yb1Hs5"
	return f'OAUTH = {{"client_id": "1234.apps.example", "{_FIELD_SECRET}": "{value}"}}\n'


def _token_near_googleapis() -> str:
	token = base64.b64encode(bytes(range(31, 151))).decode()
	return 'URL = "https://oauth2.google' + f'apis.com/token"\nTOKEN = "{token}"\n'


PLANTS = {
	"service-account key file": ("svc.json", _service_account_key(), "pem-private-key"),
	"OAuth client secret": ("oauth.py", _client_secret(), "client_secret"),
	"base64 token beside googleapis": ("call.py", _token_near_googleapis(), "base64-near-googleapis"),
}


def _git(repo: Path, *args: str) -> str:
	result = subprocess.run(
		[
			"git",
			"-c",
			"user.name=Secret Scan Test",
			"-c",
			"user.email=scan@example.invalid",
			"-c",
			"commit.gpgsign=false",
			"-c",
			"core.autocrlf=false",
			"-C",
			str(repo),
			*args,
		],
		capture_output=True,
		text=True,
		check=True,
	)
	return result.stdout.strip()


class _Repo:
	def __init__(self, root: Path) -> None:
		self.root = root
		root.mkdir(parents=True)
		_git(root, "init", "-q", "-b", "main")
		self.commit({"README.md": "a clean repository\n"}, "initial")

	def write(self, files: dict[str, str | None]) -> None:
		for rel, text in files.items():
			path = self.root / rel
			if text is None:
				path.unlink()
				continue
			path.parent.mkdir(parents=True, exist_ok=True)
			path.write_text(text, encoding="utf-8", newline="\n")

	def commit(self, files: dict[str, str | None], message: str) -> str:
		self.write(files)
		_git(self.root, "add", "-A")
		_git(self.root, "commit", "-q", "-m", message)
		return _git(self.root, "rev-parse", "HEAD")

	def blob(self, rev_path: str) -> str:
		return _git(self.root, "rev-parse", rev_path)

	def pack(self) -> None:
		"""Pack everything reachable and drop the loose copies, as a fresh clone has it."""
		_git(self.root, "gc", "-q", "--prune=now")


def _scan(repo: Path, *args: str) -> tuple[int, str]:
	result = subprocess.run(
		[sys.executable, str(SCRIPT), "--repo", str(repo), *args],
		capture_output=True,
		text=True,
	)
	return result.returncode, result.stdout + result.stderr


class _TempTestCase(unittest.TestCase):
	def setUp(self) -> None:
		self.tmp = Path(tempfile.mkdtemp(prefix="secret-scan-"))

	def tearDown(self) -> None:
		def _writable(func, path, _exc):
			os.chmod(path, 0o700)
			func(path)

		shutil.rmtree(self.tmp, onerror=_writable)

	def repo(self, name: str = "repo") -> _Repo:
		return _Repo(self.tmp / name)

	def shallow_clone(self, source: _Repo) -> Path:
		target = self.tmp / "shallow"
		subprocess.run(
			["git", "clone", "-q", "--depth", "1", source.root.as_uri(), str(target)],
			capture_output=True,
			check=True,
		)
		return target


class FullHistoryTests(_TempTestCase):
	"""No flag: every blob in the object database. Unchanged by the range mode and the cache."""

	def test_clean_repository_passes_and_says_what_it_read(self):
		repo = self.repo()
		code, out = _scan(repo.root)
		self.assertEqual(code, 0, out)
		self.assertIn("mode: full history", out)
		self.assertRegex(out, r"\d+ working-tree files, [1-9]\d* history blobs scanned")

	def test_each_planted_kind_is_found_after_it_was_deleted(self):
		for kind, (rel, text, rule) in PLANTS.items():
			with self.subTest(kind):
				repo = self.repo(rel)
				repo.commit({rel: text}, "plant")
				repo.commit({rel: None}, "remove it again")
				code, out = _scan(repo.root)
				self.assertEqual(code, 1, out)
				self.assertIn(f"rule: {rule}", out)
				self.assertIn(f"{rel} (blob ", out)

	def test_refuses_a_shallow_clone(self):
		repo = self.repo()
		repo.commit({"more.txt": "x\n"}, "second")
		code, out = _scan(self.shallow_clone(repo))
		self.assertEqual(code, 1, out)
		self.assertIn("SHALLOW", out)


class RangeTests(_TempTestCase):
	"""``--since REF``: the working tree plus what HEAD has and REF does not."""

	def test_finds_a_key_committed_and_removed_inside_the_range(self):
		repo = self.repo()
		_git(repo.root, "checkout", "-q", "-b", "feature")
		rel, text, rule = PLANTS["service-account key file"]
		repo.commit({rel: text}, "plant")
		repo.commit({rel: None, "notes.txt": "tidy\n"}, "remove it again")
		repo.pack()
		code, out = _scan(repo.root, "--since", "main")
		self.assertEqual(code, 1, out)
		self.assertIn(f"rule: {rule}", out)
		self.assertIn("mode: PR range main..HEAD", out)
		self.assertIn("2 commits", out)

	def test_finds_each_planted_kind_in_a_new_commit(self):
		for kind, (rel, text, rule) in PLANTS.items():
			with self.subTest(kind):
				repo = self.repo(rel)
				_git(repo.root, "checkout", "-q", "-b", "feature")
				repo.commit({rel: text}, "plant")
				repo.pack()
				code, out = _scan(repo.root, "--since", "main")
				self.assertEqual(code, 1, out)
				self.assertIn(f"rule: {rule}", out)

	def test_leaves_the_base_history_to_the_full_walk(self):
		repo = self.repo()
		rel, text, _rule = PLANTS["OAuth client secret"]
		repo.commit({rel: text}, "plant on main")
		repo.commit({rel: None}, "remove on main")
		_git(repo.root, "checkout", "-q", "-b", "feature")
		repo.commit({"feature.txt": "harmless\n"}, "feature work")
		repo.pack()

		code, out = _scan(repo.root, "--since", "main")
		self.assertEqual(code, 0, out)
		self.assertIn(", 1 history blobs scanned", out)

		code, out = _scan(repo.root)
		self.assertEqual(code, 1, "the full walk is the backstop and must still see it:\n" + out)

	def test_leaves_unreachable_loose_objects_to_the_full_walk(self):
		# A push sends only what is reachable from the pushed commit, so a loose object no ref
		# names cannot reach the remote through this PR. The range skips them (they made a
		# long-lived local checkout's run take minutes); the full walk still reads them.
		repo = self.repo()
		_git(repo.root, "checkout", "-q", "-b", "feature")
		repo.commit({"feature.txt": "harmless\n"}, "feature work")
		repo.pack()
		rel, text, rule = PLANTS["service-account key file"]
		stray = self.tmp / rel
		stray.write_text(text, encoding="utf-8", newline="\n")
		_git(repo.root, "hash-object", "-w", str(stray))  # a loose object no ref names
		code, out = _scan(repo.root, "--since", "main")
		self.assertEqual(code, 0, out)
		self.assertIn(", 1 history blobs scanned", out)

		code, out = _scan(repo.root)
		self.assertEqual(code, 1, out)
		self.assertIn(f"rule: {rule}", out)
		self.assertIn("(unreachable blob ", out)

	def test_refuses_an_unknown_base(self):
		repo = self.repo()
		code, out = _scan(repo.root, "--since", "origin/main")
		self.assertEqual(code, 1, out)
		self.assertIn("does not name a commit", out)

	def test_refuses_a_shallow_clone(self):
		repo = self.repo()
		repo.commit({"more.txt": "x\n"}, "second")
		shallow = self.shallow_clone(repo)
		code, out = _scan(shallow, "--since", "HEAD")
		self.assertEqual(code, 1, out)
		self.assertIn("SHALLOW", out)


class CacheTests(_TempTestCase):
	"""``--cache FILE``: skip blobs already scanned clean, but only under the same rules."""

	def test_skips_a_blob_it_scanned_clean_before(self):
		repo = self.repo()
		repo.commit({"src/app.py": "print('hello')\n"}, "code")
		cache = self.tmp / "cache.txt"

		code, out = _scan(repo.root, "--cache", str(cache))
		self.assertEqual(code, 0, out)
		self.assertRegex(out, r"cache: [1-9]\d* newly clean blobs recorded")
		clean = repo.blob("HEAD:src/app.py")
		self.assertIn(clean, cache.read_text().splitlines())

		code, out = _scan(repo.root, "--cache", str(cache))
		self.assertEqual(code, 0, out)
		self.assertIn(" 0 history blobs scanned", out)
		self.assertRegex(out, r"[1-9]\d* skipped as already scanned clean")

	def _seeded(self, header_rules: str) -> tuple[_Repo, Path]:
		"""A key in history, and a cache file that claims its blob is clean."""
		repo = self.repo()
		rel, text, _rule = PLANTS["service-account key file"]
		repo.commit({rel: text}, "plant")
		blob = repo.blob(f"HEAD:{rel}")
		repo.commit({rel: None}, "remove it again")
		cache = self.tmp / "cache.txt"
		cache.write_text(f"# check_no_committed_secrets clean blobs, rules {header_rules}\n{blob}\n")
		return repo, cache

	def test_the_cache_is_trusted_under_the_same_rules(self):
		# The fingerprint is the only thing standing between a listed blob and a skip, which
		# is why the next test matters.
		rules = _scan(Path(REPO_ROOT), "--rules-hash")[1].strip()
		repo, cache = self._seeded(rules)
		code, out = _scan(repo.root, "--cache", str(cache))
		self.assertEqual(code, 0, out)
		self.assertRegex(out, r"[1-9]\d* skipped as already scanned clean")

	def test_a_cache_written_under_other_rules_is_ignored(self):
		repo, cache = self._seeded("0000000000000000")
		code, out = _scan(repo.root, "--cache", str(cache))
		self.assertEqual(code, 1, out)
		self.assertIn("written under different rules, ignored", out)
		self.assertIn("rule: pem-private-key", out)
		self.assertNotIn("0000000000000000", cache.read_text(), "the stale file must be replaced")

	def test_a_test_data_entry_does_not_excuse_the_blob_elsewhere(self):
		rules = _scan(Path(REPO_ROOT), "--rules-hash")[1].strip()
		repo = self.repo()
		rel, text, _rule = PLANTS["service-account key file"]
		repo.commit({f"config/{rel}": text}, "plant outside test data")
		blob = repo.blob(f"HEAD:config/{rel}")
		cache = self.tmp / "cache.txt"
		cache.write_text(f"# check_no_committed_secrets clean blobs, rules {rules}\n{blob} t\n")
		code, out = _scan(repo.root, "--cache", str(cache))
		self.assertEqual(code, 1, out)

	def test_any_rule_edit_changes_the_fingerprint(self):
		spec = importlib.util.spec_from_file_location("check_no_committed_secrets", SCRIPT)
		module = importlib.util.module_from_spec(spec)
		sys.modules[spec.name] = module  # @dataclass looks its module up while decorating
		try:
			spec.loader.exec_module(module)
		finally:
			sys.modules.pop(spec.name, None)
		source = SCRIPT.read_bytes().replace(b"\r\n", b"\n")
		base = module.rules_fingerprint(source)

		edits = {
			"the window": (b"WINDOW_RADIUS = 3", b"WINDOW_RADIUS = 2"),
			"the marker": (b'FAKE_MARKER = "FAKE-DO-NOT-USE"', b'FAKE_MARKER = "FAKE"'),
			"a pattern": (
				b'BASE64_RUN_RE = re.compile(r"[A-Za-z0-9+/]{64,}',
				b'BASE64_RUN_RE = re.compile(r"[A-Za-z0-9+/]{65,}',
			),
			"the logic": (b"if FAKE_MARKER in window:", b"if FAKE_MARKER in window.lower():"),
		}
		for what, (old, new) in edits.items():
			with self.subTest(what):
				self.assertIn(old, source, f"{what}: the test's anchor is stale")
				self.assertNotEqual(module.rules_fingerprint(source.replace(old, new, 1)), base)

		self.assertEqual(
			module.rules_fingerprint(source.replace(b"\n", b"\r\n")), base, "CRLF must not matter"
		)


class StagedTests(_TempTestCase):
	"""``--staged``: the pre-commit hook reads what will be committed."""

	def test_reads_the_index_not_the_file_on_disk(self):
		repo = self.repo()
		rel, text, rule = PLANTS["OAuth client secret"]
		repo.write({rel: text})
		_git(repo.root, "add", rel)
		repo.write({rel: "OAUTH = {}\n"})  # cleaned on disk, still staged with the secret
		code, out = _scan(repo.root, "--staged")
		self.assertEqual(code, 1, out)
		self.assertIn(f"rule: {rule}", out)

	def test_clean_staged_changes_pass(self):
		repo = self.repo()
		repo.write({"src/app.py": "print('hello')\n"})
		_git(repo.root, "add", "src/app.py")
		code, out = _scan(repo.root, "--staged")
		self.assertEqual(code, 0, out)
		self.assertIn("1 staged files scanned", out)


REVIEWED_LIST = "scripts/secret_scan_reviewed_blobs.txt"


class ReviewedBlobTests(_TempTestCase):
	"""``scripts/secret_scan_reviewed_blobs.txt``: old blobs a person has read, by content hash.

	The list exists so a new rule does not leave the full walk red for good over blobs nobody
	can edit. Every test here is about how little it excuses.
	"""

	def planted_and_removed(self, name: str = "repo") -> tuple[_Repo, str]:
		repo = self.repo(name)
		rel, text, _rule = PLANTS["OAuth client secret"]
		repo.commit({rel: text}, "plant")
		sha = repo.blob(f"HEAD:{rel}")
		repo.commit({rel: None}, "remove it again")
		return repo, sha

	def test_a_listed_historical_blob_is_excused_by_the_full_walk(self):
		repo, sha = self.planted_and_removed()
		code, out = _scan(repo.root)
		self.assertEqual(code, 1, out)

		repo.commit(
			{REVIEWED_LIST: f"# read by a person\n\n{sha}  an old example, read 2026-11-18\n"}, "list it"
		)
		code, out = _scan(repo.root)
		self.assertEqual(code, 0, out)
		self.assertIn(f"reviewed: 1 historical blob(s) with findings are listed in {REVIEWED_LIST}", out)

	def test_a_listed_blob_is_never_recorded_as_clean(self):
		repo, sha = self.planted_and_removed()
		repo.commit({REVIEWED_LIST: f"{sha}  an old example, read 2026-11-18\n"}, "list it")
		cache = self.tmp / "cache.txt"
		code, out = _scan(repo.root, "--cache", str(cache))
		self.assertEqual(code, 0, out)
		self.assertNotIn(sha, cache.read_text(encoding="ascii"))

		repo.commit({REVIEWED_LIST: "# nothing listed any more\n"}, "take the line out")
		code, out = _scan(repo.root, "--cache", str(cache))
		self.assertEqual(code, 1, "with its line gone the blob must be reported again:\n" + out)
		self.assertIn("rule: client_secret", out)

	def test_a_listed_blob_that_is_still_in_the_tree_is_not_excused(self):
		repo = self.repo()
		rel, text, rule = PLANTS["OAuth client secret"]
		repo.commit({rel: text}, "plant and keep")
		sha = repo.blob(f"HEAD:{rel}")
		repo.commit({REVIEWED_LIST: f"{sha}  listed although it is the current file\n"}, "list it")
		code, out = _scan(repo.root)
		self.assertEqual(code, 1, out)
		self.assertIn(f"rule: {rule}", out)
		self.assertIn("is in the current tree", out)

	def test_the_same_content_under_a_new_name_is_still_found_in_the_tree(self):
		repo, sha = self.planted_and_removed()
		repo.commit({REVIEWED_LIST: f"{sha}  an old example, read 2026-11-18\n"}, "list it")
		_rel, text, rule = PLANTS["OAuth client secret"]
		repo.commit({"elsewhere/copy.py": text}, "the same bytes come back under another name")
		code, out = _scan(repo.root)
		self.assertEqual(code, 1, out)
		self.assertIn(f"rule: {rule}", out)

	def test_the_pr_range_never_reads_the_list(self):
		repo = self.repo()
		_git(repo.root, "checkout", "-q", "-b", "feature")
		rel, text, rule = PLANTS["OAuth client secret"]
		repo.commit({rel: text}, "plant")
		sha = repo.blob(f"HEAD:{rel}")
		repo.commit(
			{rel: None, REVIEWED_LIST: f"{sha}  listed by the same pull request\n"}, "remove and list"
		)
		repo.pack()
		code, out = _scan(repo.root, "--since", "main")
		self.assertEqual(code, 1, out)
		self.assertIn(f"rule: {rule}", out)
		self.assertNotIn("reviewed:", out)

	def test_staged_files_never_read_the_list(self):
		repo, sha = self.planted_and_removed()
		repo.commit({REVIEWED_LIST: f"{sha}  an old example, read 2026-11-18\n"}, "list it")
		rel, text, rule = PLANTS["OAuth client secret"]
		repo.write({rel: text})
		_git(repo.root, "add", rel)
		code, out = _scan(repo.root, "--staged")
		self.assertEqual(code, 1, out)
		self.assertIn(f"rule: {rule}", out)

	def test_an_entry_that_excuses_nothing_fails_the_run(self):
		repo = self.repo()
		clean = repo.blob("HEAD:README.md")
		absent = "ab" * 20
		for sha in (clean, absent):
			with self.subTest(sha=sha[:8]):
				repo.commit({REVIEWED_LIST: f"{sha}  a line the walk has no use for\n"}, "list it")
				code, out = _scan(repo.root)
				self.assertEqual(code, 1, out)
				self.assertIn("LISTS BLOBS IT DOES NOT NEED TO", out)
				self.assertIn(sha, out)

	def test_a_line_that_is_not_a_full_hash_and_a_note_refuses_to_run(self):
		repo, sha = self.planted_and_removed()
		for line in (
			sha[:12] + "  a short hash",
			sha,
			sha + "  short",
			f"{sha}  a note\n{sha}  listed twice here",
		):
			with self.subTest(line=line[-24:]):
				repo.commit({REVIEWED_LIST: line + "\n"}, "a bad line")
				code, out = _scan(repo.root)
				self.assertEqual(code, 1, out)
				self.assertIn("cannot scan for committed secrets", out)

	def test_this_repositorys_own_list_is_well_formed_and_names_nothing_current(self):
		scanner = _load_scanner()
		reviewed = scanner.load_reviewed_blobs()
		self.assertTrue(reviewed, "the list is expected to exist while the blobs it names are in history")
		self.assertLessEqual(len(reviewed), 5, "the list is not a place to put new findings")
		self.assertEqual(sorted(set(reviewed) & scanner.head_blobs()), [])


def _load_scanner():
	spec = importlib.util.spec_from_file_location("check_no_committed_secrets_rules", SCRIPT)
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module  # @dataclass looks its module up while decorating
	try:
		spec.loader.exec_module(module)
	finally:
		sys.modules.pop(spec.name, None)
	return module


class ProviderAndPasswordRuleTests(unittest.TestCase):
	"""``scan_text`` against shaped credentials, password literals in docs, and the PEM rule's marker immunity.

	Every planted value is assembled from fragments, as above, so this file stays clean.
	"""

	@classmethod
	def setUpClass(cls):
		cls.scanner = _load_scanner()

	def rules(self, where, text):
		return {finding.rule for finding in self.scanner.scan_text(where, text)}

	# -- shaped credentials -------------------------------------------------------------------

	def test_provider_shaped_credentials_fire(self):
		mixed = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
		plants = {
			"stripe-live-key": "KEY = '" + "sk_" + "live_" + mixed[:24] + "'",
			"stripe-webhook-secret": "export HOOK=" + "whsec" + "_" + mixed[:24],
			"google-api-key": "key=" + "AI" + "za" + mixed[:35],
			"aws-access-key": "id: " + "AK" + "IA" + "7Q2M5X8B1C4D6E9F",
			"github-token": "token " + "gh" + "p_" + mixed,
			"slack-token": "SLACK=" + "xo" + "xb-" + "1234567890-AbCdEfGhIjKl",
			"jwt": "Bearer " + "ey" + "JhbGciOiJIUzI1NiJ9." + "ey" + "JzdWIiOiJ4eXoifQ." + "Qm9ndXNTaWduYXR1cmU",
			"token-key-secret-pair": "Authorization: token " + "k3Jd9Lm2Qp8Rx7V" + ":" + "b1Hs5Yw4Tn6Zc0A",
			"url-with-password": "DATABASE_URL=postgres://app:" + "Zk3Pq9Lm" + "@db.internal:5432/app",
			"twilio-auth-token": "TWILIO_AUTH_TOKEN=" + "9f3a7c1e5b2d4086" + "ae1c3b5d7f902468",
			"encryption_key": 'encryption_' + 'key = "' + "Zk3Pq9LmXv2Wb7Nd4Rt8Yh1Gs6Jc5Ae0Uo3Ik9Pl2Q=" + '"',
		}
		for rule, text in plants.items():
			with self.subTest(rule):
				self.assertIn(rule, self.rules("config/notes.txt", text + "\n"))

	def test_placeholders_do_not_fire(self):
		clean = [
			"KEY = '" + "sk_" + "live_" + "0" * 24 + "'",
			"postgres://user:" + "pass" + "@host/db",
			"postgres://user:${DB_PASSWORD}@host/db",
			"TWILIO_AUTH_TOKEN=<stored in the password manager>",
			"Authorization: token " + "api_key" + ":" + "api_secret",
			'encryption_' + 'key = module.kms.keys.key-regional.id',
		]
		for text in clean:
			with self.subTest(text):
				self.assertEqual(self.rules("config/notes.txt", text + "\n"), set())

	# -- the quick triggers ---------------------------------------------------------------------

	def without_triggers(self, where, text):
		"""The same scan with every rule's regex run on every line, as it was before the triggers."""
		saved = dict(self.scanner._QUICK_TRIGGERS)
		self.scanner._QUICK_TRIGGERS.clear()
		try:
			return [(f.rule, f.line_no) for f in self.scanner.scan_text(where, text)]
		finally:
			self.scanner._QUICK_TRIGGERS.update(saved)

	def test_every_trigger_names_a_rule_and_is_lower_case(self):
		names = {
			name
			for rules in (self.scanner.VALUE_RULES, self.scanner.TOKEN_RULES, self.scanner.PASSWORD_RULES)
			for name, _pattern, _why in rules
		}
		for rule, triggers in self.scanner._QUICK_TRIGGERS.items():
			with self.subTest(rule):
				self.assertIn(rule, names)
				self.assertTrue(triggers)
				for trigger in triggers:
					self.assertEqual(trigger, trigger.lower())
		# The provider and password rules all have one: a rule added without one is slow, not wrong.
		for name, _pattern, _why in (*self.scanner.TOKEN_RULES, *self.scanner.PASSWORD_RULES):
			self.assertIn(name, self.scanner._QUICK_TRIGGERS)

	def test_the_triggers_change_no_finding(self):
		"""A trigger only skips a regex that could not have matched. Held on shaped lines in both
		letter cases and on the largest real files in the repository."""
		mixed = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
		value = "Zk3Pq9Lm"
		lines = [
			"KEY = '" + "sk_" + "live_" + mixed[:24] + "'",
			"KEY = '" + "rk_" + "live_" + mixed[:24] + "'",
			"export HOOK=" + "whsec" + "_" + mixed[:24],
			"key=" + "AI" + "za" + mixed[:35],
			"id: " + "AK" + "IA" + "7Q2M5X8B1C4D6E9F",
			"id: " + "AS" + "IA" + "7Q2M5X8B1C4D6E9F",
			*("token " + "gh" + letter + "_" + mixed for letter in "pousr"),
			"pat " + "github" + "_pat_" + mixed + mixed,
			*("SLACK=" + "xo" + "x" + letter + "-" + "8317025946-Qw7Zr2Lp9Xv4" for letter in "abprs"),
			"Bearer " + "ey" + "JhbGciOiJIUzI1NiJ9." + "ey" + "JzdWIiOiJ4eXoifQ." + "Qm9ndXNTaWduYXR1cmU",
			"Authorization: token " + "k3Jd9Lm2Qp8Rx7V" + ":" + "b1Hs5Yw4Tn6Zc0A",
			"Authorization: TOKEN " + "k3Jd9Lm2Qp8Rx7V" + ":" + "b1Hs5Yw4Tn6Zc0A",
			"DATABASE_URL=postgres://app:" + value + "@db.internal:5432/app",
			"TWILIO_AUTH_TOKEN=" + "9f3a7c1e5b2d4086" + "ae1c3b5d7f902468",
			"Twilio token " + "9f3a7c1e5b2d4086" + "ae1c3b5d7f902468",
			"encryption_" + 'key = "' + "Zk3Pq9LmXv2Wb7Nd4Rt8Yh1Gs6Jc5Ae0Uo3Ik9Pl2Q=" + '"',
			"SITE_ENCRYPTION_" + "KEY=" + "Zk3Pq9LmXv2Wb7Nd4Rt8Yh1Gs6Jc5Ae0Uo3Ik9Pl2Q=",
			"db_" + "password: " + value,
			"DB_" + "PASSWORD=" + value,
			"MYSQL_ROOT_" + "PASSWD: " + value,
			"| MariaDB root pass" + "word | `" + value + "` |",
			"| ROOT PASS" + "WORD | `" + value + "` |",
			"bench new-site --admin-" + "password " + value,
			"bench new-site --ADMIN-" + "PASSWORD=" + value,
			"mysql -u root -p'" + value + "'",
			"MYSQLDUMP -u root -p" + value + " db",
			"mariadb-dump -u root -p" + value + " db",
			"nothing to see on this line",
		]
		for line in lines:
			for where in ("docs/setup.md", "app/settings.py"):
				with self.subTest(line=line[:18], where=where):
					fast = [(f.rule, f.line_no) for f in self.scanner.scan_text(where, line + "\n")]
					self.assertEqual(fast, self.without_triggers(where, line + "\n"))
		together = "\n".join(lines) + "\n"
		fast = [(f.rule, f.line_no) for f in self.scanner.scan_text("docs/setup.md", together)]
		self.assertEqual(fast, self.without_triggers("docs/setup.md", together))
		self.assertGreater(len({rule for rule, _line in fast}), 12, "the lines are meant to fire most rules")

		for rel in ("CHANGELOG.md", "README.md", "infra/README.md", "erpnext_enhancements/hooks.py"):
			path = REPO_ROOT / rel
			if not path.exists():
				continue
			with self.subTest(rel):
				text = path.read_text(encoding="utf-8", errors="replace")
				fast = [(f.rule, f.line_no) for f in self.scanner.scan_text(rel, text)]
				self.assertEqual(fast, self.without_triggers(rel, text))

	def test_a_typed_out_run_is_a_stand_in(self):
		typed = "1234567890abcdefghijklmnopqrstuvwxyz"
		for text in ('default = "' + "gh" + "p_" + typed + '"', "token " + "gh" + "p_" + typed.upper()):
			with self.subTest(text[:12]):
				self.assertEqual(self.rules("infra/variables.tf", text + "\n"), set())
		# A short run inside an otherwise mixed value is not enough to wave it through.
		mixed = "A1b2C3d4E5f6G7h8I9j0K1l2" + "abcdefghijkl"
		self.assertIn("github-token", self.rules("infra/variables.tf", "token " + "gh" + "p_" + mixed + "\n"))

	# -- password literals in docs and config ---------------------------------------------------

	def test_password_literals_in_docs_and_config_fire(self):
		value = "Zk3Pq9Lm"
		plants = {
			"password-assignment": ["db_" + "password: " + value, '"db_' + 'password": "' + value + '"',
									"- **Root pass" + "word:** `" + value + "`"],
			"password-option": ["bench new-site --admin-" + "password " + value],
			"database-client-password": ["mysql -u root -p'" + value + "'"],
			"password-table-cell": ["| MariaDB root pass" + "word | `" + value + "` |"],
		}
		for rule, lines in plants.items():
			for line in lines:
				with self.subTest(line.replace(value, "<value>")):
					self.assertIn(rule, self.rules("docs/setup.md", line + "\n"))

	def test_password_placeholders_and_prose_do_not_fire(self):
		word = "pass" + "word"
		clean = [
			f"{word}: <stored in the password manager>",
			f'"db_{word}": "<stored in the password manager>"',
			f"{word}: required",
			f"{word} = ${{DB_{word.upper()}}}",
			f"mysql -u root -p'<stored in the password manager>'",
			f"the field stored behind the {word}: present, absent, short or long",
			f'"Pass' + f'word: case-sensitive." is prose',
			f"decodes to `user:{word}`: letters and slashes",
		]
		for text in clean:
			with self.subTest(text):
				self.assertEqual(self.rules("docs/setup.md", text + "\n"), set())

	def test_the_sudoers_tag_is_not_a_password(self):
		tag = "NOPASS" + "WD"
		for text in (
			f"deploy ALL=(ALL) {tag}: /usr/bin/systemctl restart frappe-bench",
			f"echo 'deploy ALL=(ALL) {tag}: /usr/bin/supervisorctl restart all' | sudo tee /etc/sudoers.d/deploy",
		):
			with self.subTest(text[:24]):
				self.assertEqual(self.rules("infra/configs/startup_script.sh", text + "\n"), set())
		# Any other name ending in the word is still an assignment.
		self.assertIn("password-assignment", self.rules("docs/setup.md", "db_" + "passwd: Zk3Pq9Lm\n"))
		self.assertIn("password-assignment", self.rules("docs/setup.md", tag.lower() + ": Zk3Pq9Lm\n"))

	def test_password_rules_leave_source_code_to_review(self):
		line = "db_" + "password = 'Zk3Pq9Lm'\n"
		self.assertEqual(self.rules("app/settings.py", line), set())
		self.assertIn("password-assignment", self.rules("app/settings.sh", line))
		self.assertIn("password-assignment", self.rules("(unreachable blob abc123)", line))

	# -- the fixture marker ---------------------------------------------------------------------

	def test_marker_beside_the_value_still_excuses_the_other_rules(self):
		text = "# FAKE-DO-NOT-USE\nKEY = '" + "sk_" + "live_" + "A1b2C3d4E5f6G7h8I9j0K1l2" + "'\n"
		self.assertEqual(self.rules("docs/example.md", text), set())
		self.assertEqual(self.rules("erpnext_enhancements/tests/test_x.py", text), set())

	def test_the_pem_rule_cannot_be_excused_by_the_marker(self):
		pem = "-----BEGIN " + "PRIVATE KEY-----"
		beside = f"# FAKE-DO-NOT-USE\nKEY = '{pem}'\n"
		header = f"# FAKE-DO-NOT-USE\n{pem}\nbody\n"
		for where, text in (
			("docs/example.md", beside),
			("erpnext_enhancements/tests/test_x.py", beside),
			("erpnext_enhancements/tests/fixtures/key.pem", header),
			("fixtures/key.pem", header),
		):
			with self.subTest(where):
				self.assertEqual(self.rules(where, text), {"pem-private-key"})


if __name__ == "__main__":
	unittest.main()
