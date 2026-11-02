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


if __name__ == "__main__":
	unittest.main()
