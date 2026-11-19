"""Bench-free tests for the pre-migrate backup and the pinned upstream update (v1.836.7).

Two rules, both enforced by files that run nowhere but on a VM:

* every app deploy (infra/cloudbuild-deploy.yaml) takes a database backup with bench's own
  `backup` command before `bench migrate`, and stops when the backup fails;
* an upstream update (infra/cloudbuild-upstream.yaml) moves Frappe and ERPNext only to two
  named release tags, never to a branch head, behind the same backup.

Both run infra/bench_update.sh. These tests read the two Cloud Build files (a small reader
for their one block scalar, cross-checked with PyYAML where it is installed), run the
upstream file's tag check with `sh`, and run the script itself against a scratch bench with
a stub `bench` on PATH and real git repositories standing in for frappe and erpnext. The
script tests need bash, git and gzip, so they run on Linux; CI sets REQUIRE_DEPLOY_SCRIPT_TOOLS
so that a missing tool fails instead of skipping.

Run: python -m unittest erpnext_enhancements.tests.test_pre_migrate_backup -v
"""

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INFRA = REPO_ROOT / "infra"
SCRIPT = INFRA / "bench_update.sh"
DEPLOY_YAML = INFRA / "cloudbuild-deploy.yaml"
UPSTREAM_YAML = INFRA / "cloudbuild-upstream.yaml"
CLOUD_BUILD_TF = INFRA / "cloud_build.tf"

RUN_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")

#: Set by the CI step: a missing tool then fails these tests instead of skipping them.
REQUIRE_TOOLS = os.environ.get("REQUIRE_DEPLOY_SCRIPT_TOOLS") == "1"


def _tool_or_skip(testcase, available, what):
	if available:
		return
	if REQUIRE_TOOLS:
		testcase.fail(f"{what} is required here (REQUIRE_DEPLOY_SCRIPT_TOOLS=1) but is not available")
	raise unittest.SkipTest(f"{what} is not available")


def _code(text):
	"""The text with comment lines dropped, so an absence check is not satisfied by prose."""
	return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _script_block(path):
	"""The step's `- |` block scalar, dedented, exactly as Cloud Build hands it to `sh -c`
	(before its own `$$` -> `$` substitution)."""
	lines = path.read_text(encoding="utf-8").splitlines()
	start = next(i for i, line in enumerate(lines) if line.strip() == "- |")
	base = len(lines[start]) - len(lines[start].lstrip())
	block = []
	for line in lines[start + 1 :]:
		if line.strip() and len(line) - len(line.lstrip()) <= base:
			break
		block.append(line)
	return textwrap.dedent("\n".join(block)).strip("\n") + "\n"


def _top_level_block(path, key):
	"""`key:`'s indented lines at the top level of the file, as {name: value}."""
	lines = path.read_text(encoding="utf-8").splitlines()
	start = lines.index(f"{key}:")
	out = {}
	for line in lines[start + 1 :]:
		if line and not line.startswith(" "):
			break
		if line.strip():
			name, _, value = line.strip().partition(":")
			out[name.strip()] = value.strip()
	return out


def _cloud_build(text):
	"""Cloud Build's own escape: `$$` reaches the shell as `$`."""
	return text.replace("$$", "$")


def _remote_commands(block):
	"""The `&&`-joined commands of the ssh --command, in order, whitespace collapsed."""
	start = block.index('--command="') + len('--command="')
	end = block.index('"\n', start)
	command = re.sub(r"\\\n\s*", " ", block[start:end])
	return [re.sub(r"\s+", " ", part).strip() for part in command.split("&&")]


def _remote_string(testcase, block, **env):
	"""What the VM's shell receives: the --command="..." word as the build step's `sh` expands
	it, with bash -n run over the result."""
	_tool_or_skip(testcase, sys.platform != "win32" and shutil.which("sh") and shutil.which("bash"), "sh and bash")
	block = _cloud_build(block)
	start = block.index('--command="') + len("--command=")
	word = block[start : block.index('"\n', start) + 1]
	result = subprocess.run(
		["sh", "-c", f"printf '%s' {word}"],
		env={"PATH": os.environ.get("PATH", ""), **env},
		capture_output=True,
		text=True,
		check=True,
	)
	remote = result.stdout
	parsed = subprocess.run(["bash", "-n", "-c", remote], capture_output=True, text=True)
	testcase.assertEqual(parsed.returncode, 0, parsed.stderr)
	return re.sub(r"\s+", " ", remote)


def _script_tag_re():
	match = re.search(r"^readonly TAG_RE='([^']+)'$", SCRIPT.read_text(encoding="utf-8"), re.M)
	return match.group(1)


class DeployYamlTests(unittest.TestCase):
	def setUp(self):
		self.block = _script_block(DEPLOY_YAML)

	def test_the_step_stops_on_the_first_failure(self):
		self.assertEqual(self.block.splitlines()[0], "set -e")

	def test_the_backup_comes_before_migrate_in_one_and_chain(self):
		self.assertEqual(
			_remote_commands(self.block),
			[
				"sudo -u frappe bash -c 'cd /home/frappe/frappe-bench",
				"git -C apps/erpnext_enhancements fetch upstream main",
				"git -C apps/erpnext_enhancements reset --hard FETCH_HEAD",
				r"export PATH=\$$PATH:/home/frappe/.local/bin",
				"bash apps/erpnext_enhancements/infra/bench_update.sh backup",
				"bench --site all migrate",
				"bench build",
				"redis-cli -p 13000 FLUSHDB",
				"redis-cli -p 11000 FLUSHDB'",
				"sudo systemctl restart frappe-bench",
			],
		)

	def test_the_vm_receives_a_valid_command_with_the_backup_first(self):
		remote = _remote_string(self, self.block)
		self.assertIn(
			"export PATH=$PATH:/home/frappe/.local/bin && "
			"bash apps/erpnext_enhancements/infra/bench_update.sh backup && bench --site all migrate && ",
			remote,
		)

	def test_nothing_after_the_backup_can_run_when_it_fails(self):
		# One `&&` chain: no `;` or `||` anywhere in the remote command could let migrate,
		# the redis flush or the restart run after a failed backup.
		start = self.block.index('--command="')
		command = self.block[start : self.block.index('"\n', start)]
		self.assertNotIn(";", command)
		self.assertNotIn("||", command)

	def test_no_new_substitutions_and_more_time(self):
		self.assertEqual(_top_level_block(DEPLOY_YAML, "substitutions"), {"_ALLOW_SKIP": "'false'"})
		timeout = re.search(r"^timeout: '(\d+)s'$", DEPLOY_YAML.read_text(encoding="utf-8"), re.M)
		self.assertGreaterEqual(int(timeout.group(1)), 1200)


class UpstreamYamlTests(unittest.TestCase):
	def setUp(self):
		self.text = UPSTREAM_YAML.read_text(encoding="utf-8")
		self.block = _script_block(UPSTREAM_YAML)

	def test_it_never_follows_a_branch(self):
		code = _code(self.text)
		for absent in ("--no-backup", "bench update", "--pull", "switch-to-branch", "version-16"):
			self.assertNotIn(absent, code, absent)

	def test_it_runs_the_script_with_both_tags_then_restarts(self):
		commands = _remote_commands(self.block)
		self.assertEqual(
			commands[-2:],
			[
				"bash apps/erpnext_enhancements/infra/bench_update.sh upstream $$FRAPPE_TAG $$ERPNEXT_TAG'",
				"sudo systemctl restart frappe-bench",
			],
		)

	def test_the_vm_receives_a_valid_command_with_both_tags(self):
		remote = _remote_string(self, self.block, FRAPPE_TAG="v16.56.0", ERPNEXT_TAG="v16.54.0")
		self.assertTrue(
			remote.endswith(
				"bash apps/erpnext_enhancements/infra/bench_update.sh upstream v16.56.0 v16.54.0' "
				"&& sudo systemctl restart frappe-bench"
			),
			remote,
		)

	def test_the_tags_arrive_as_environment_not_script_text(self):
		self.assertIn("      - 'FRAPPE_TAG=$_FRAPPE_TAG'\n", self.text)
		self.assertIn("      - 'ERPNEXT_TAG=$_ERPNEXT_TAG'\n", self.text)
		self.assertNotIn("$_FRAPPE_TAG", self.block)
		self.assertNotIn("$_ERPNEXT_TAG", self.block)
		subs = _top_level_block(UPSTREAM_YAML, "substitutions")
		self.assertEqual(subs.get("_FRAPPE_TAG"), "''")
		self.assertEqual(subs.get("_ERPNEXT_TAG"), "''")

	def test_the_tag_check_comes_before_anything_else(self):
		end = self.block.index("# tag check: end")
		self.assertLess(end, self.block.index("DEPLOY_SSH_KEY"))
		self.assertLess(end, self.block.index("gcloud compute"))
		self.assertEqual(self.block.splitlines()[1], "# tag check: begin")

	def test_the_yaml_pattern_is_the_scripts_pattern(self):
		match = re.search(r"grep -Eq '([^']+)'", _cloud_build(self.block))
		self.assertEqual(match.group(1), _script_tag_re())
		self.assertEqual(_script_tag_re(), r"^v16\.[0-9]+\.[0-9]+$")

	def test_the_tag_check_refuses_anything_but_two_release_tags(self):
		_tool_or_skip(self, sys.platform != "win32" and shutil.which("sh"), "a POSIX sh")
		block = _cloud_build(self.block)
		check = block[block.index("# tag check: begin") : block.index("# tag check: end")]

		def run(frappe_tag, erpnext_tag):
			env = {"PATH": os.environ.get("PATH", ""), "FRAPPE_TAG": frappe_tag, "ERPNEXT_TAG": erpnext_tag}
			return subprocess.run(["sh", "-c", "set -e\n" + check], env=env, capture_output=True, text=True)

		self.assertEqual(run("v16.56.0", "v16.54.0").returncode, 0)
		for pair in (
			("", ""),
			("v16.56.0", ""),
			("", "v16.54.0"),
			("version-16", "v16.54.0"),
			("v16.56.0", "develop"),
			("v15.90.1", "v16.54.0"),
			("v16.56", "v16.54.0"),
			("16.56.0", "v16.54.0"),
			("v16.56.0-beta.1", "v16.54.0"),
			("v16.56.0\nv16.56.0", "v16.54.0"),
			("v16.56.0; true", "v16.54.0"),
			("v16.56.0", "$(true)"),
		):
			result = run(*pair)
			self.assertEqual(result.returncode, 1, pair)
			self.assertIn("nothing was changed", result.stdout, pair)

	def test_both_files_parse_as_yaml(self):
		try:
			import yaml
		except ImportError:
			yaml = None
		_tool_or_skip(self, yaml, "PyYAML")
		for path in (DEPLOY_YAML, UPSTREAM_YAML):
			doc = yaml.safe_load(path.read_text(encoding="utf-8"))
			(step,) = doc["steps"]
			self.assertEqual(step["entrypoint"], "sh")
			self.assertEqual(step["args"][1], _script_block(path))
		doc = yaml.safe_load(self.text)
		self.assertEqual(doc["steps"][0]["env"], ["FRAPPE_TAG=$_FRAPPE_TAG", "ERPNEXT_TAG=$_ERPNEXT_TAG"])
		self.assertEqual(doc["substitutions"], {"_ALLOW_SKIP": "false", "_FRAPPE_TAG": "", "_ERPNEXT_TAG": ""})


class TerraformTests(unittest.TestCase):
	def _trigger(self, name):
		text = CLOUD_BUILD_TF.read_text(encoding="utf-8")
		start = text.index(f'"{name}" = {{')
		return text[start : text.index("tags =", start)]

	def test_the_upstream_triggers_carry_both_tag_substitutions_blank(self):
		for name in ("app-upstream-test", "app-upstream-prod"):
			trigger = self._trigger(name)
			self.assertRegex(trigger, r'_FRAPPE_TAG\s+= ""', name)
			self.assertRegex(trigger, r'_ERPNEXT_TAG\s+= ""', name)

	def test_the_deploy_triggers_gain_nothing(self):
		for name in ("app-deploy-test", "app-deploy-prod"):
			self.assertNotIn("_TAG", self._trigger(name), name)


STUB_BENCH = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_CALLS"
if [ "${1:-}" = "--site" ] && [ "${3:-}" = "backup" ]; then
	printf 'frappe-head-at-backup %s\n' "$(git -C apps/frappe rev-parse HEAD 2>/dev/null || echo none)" >> "$STUB_CALLS"
	case "${STUB_BACKUP:-ok}" in
	fail) echo "Backup failed for Site x" >&2; exit 1 ;;
	empty) exit 0 ;;
	esac
	dir=""
	while [ $# -gt 0 ]; do
		if [ "$1" = "--backup-path" ]; then dir=$2; fi
		shift
	done
	for s in $STUB_SITES; do
		slug=${s//./_}
		printf 'dump' | gzip > "$dir/20261119_000000-$slug-database.sql.gz"
		printf '{}' > "$dir/20261119_000000-$slug-site_config_backup.json"
	done
	exit 0
fi
if [ -n "${STUB_FAIL:-}" ] && [ "$*" = "$STUB_FAIL" ]; then exit 1; fi
exit 0
"""


class _ScratchBench(unittest.TestCase):
	SITES = ("site1.test", "site2.test")

	def setUp(self):
		_tool_or_skip(
			self,
			sys.platform.startswith("linux") and all(shutil.which(t) for t in ("bash", "git", "gzip")),
			"Linux with bash, git and gzip",
		)
		if os.geteuid() == 0:
			self.skipTest("the script refuses to run as root")
		self.tmp = Path(tempfile.mkdtemp())
		self.addCleanup(shutil.rmtree, self.tmp, True)
		self.bench = self.tmp / "frappe-bench"
		for site in self.SITES:
			(self.bench / "sites" / site).mkdir(parents=True)
			(self.bench / "sites" / site / "site_config.json").write_text("{}")
		(self.bench / "sites" / "assets").mkdir()
		(self.bench / "apps" / "frappe").mkdir(parents=True)
		self.stubs = self.tmp / "bin"
		self.stubs.mkdir()
		stub = self.stubs / "bench"
		stub.write_text(STUB_BENCH)
		stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
		self.calls = self.tmp / "calls.log"
		self.root = self.bench / "pre-migrate-backups"

	def run_script(self, *args, **env):
		full = {
			"PATH": f"{self.stubs}{os.pathsep}{os.environ.get('PATH', '')}",
			"HOME": str(self.tmp),
			"BENCH_DIR": str(self.bench),
			"PRE_MIGRATE_MIN_FREE_MB": "0",
			"STUB_CALLS": str(self.calls),
			"STUB_SITES": " ".join(self.SITES),
			"GIT_CONFIG_NOSYSTEM": "1",
		}
		full.update(env)
		return subprocess.run(
			["bash", str(SCRIPT), *args], env=full, capture_output=True, text=True, timeout=120
		)

	def call_log(self):
		return self.calls.read_text().splitlines() if self.calls.exists() else []

	def runs(self):
		if not self.root.is_dir():
			return []
		return sorted(p.name for p in self.root.iterdir() if p.is_dir() and not p.is_symlink() and RUN_RE.match(p.name))


class ScriptStaticTests(unittest.TestCase):
	def test_bash_n(self):
		_tool_or_skip(self, sys.platform != "win32" and shutil.which("bash"), "bash")
		result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
		self.assertEqual(result.returncode, 0, result.stderr)

	def test_shellcheck(self):
		_tool_or_skip(self, shutil.which("shellcheck"), "shellcheck")
		result = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True)
		self.assertEqual(result.returncode, 0, result.stdout)

	def test_strict_mode(self):
		self.assertIn("\nset -euo pipefail\n", SCRIPT.read_text(encoding="utf-8"))

	def test_the_only_deletions_are_inside_its_own_directory(self):
		code = _code(SCRIPT.read_text(encoding="utf-8"))
		removals = [line.strip() for line in code.splitlines() if re.search(r"\brm\b", line)]
		self.assertEqual(
			removals,
			[
				'rm -rf -- "${root:?}/${runs[i]:?}"',
				'rm -rf -- "${run_dir:?}"',
				'rm -rf -- "${run_dir:?}"',
			],
		)
		self.assertIn('run_dir=$root/$(date -u +%Y%m%dT%H%M%SZ)', code)


class BackupTests(_ScratchBench):
	def test_a_backup_is_written_and_listed_by_name_and_size(self):
		result = self.run_script("backup")
		self.assertEqual(result.returncode, 0, result.stderr)
		(run,) = self.runs()
		run_dir = self.root / run
		self.assertEqual(
			sorted(p.name for p in run_dir.iterdir()),
			sorted(
				f"20261119_000000-{s.replace('.', '_')}-{kind}"
				for s in self.SITES
				for kind in ("database.sql.gz", "site_config_backup.json")
			),
		)
		self.assertIn(f"--site all backup --backup-path {run_dir} --ignore-backup-conf", self.call_log())
		for site in self.SITES:
			self.assertRegex(result.stdout, rf"\n  \S+  20261119_000000-{site.replace('.', '_')}-database\.sql\.gz\n")
		self.assertNotIn("dump", result.stdout)
		self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)

	def test_a_failed_backup_stops_with_a_clear_message(self):
		result = self.run_script("backup", STUB_BACKUP="fail")
		self.assertEqual(result.returncode, 1)
		self.assertIn("PRE-MIGRATE BACKUP FAILED: bench backup exited with an error", result.stderr)
		self.assertIn("migrated nothing", result.stderr)
		self.assertEqual(self.runs(), [])

	def test_a_backup_that_writes_no_file_fails(self):
		result = self.run_script("backup", STUB_BACKUP="empty")
		self.assertEqual(result.returncode, 1)
		self.assertIn("wrote no database file for site1.test", result.stderr)
		self.assertEqual(self.runs(), [])

	def test_every_site_needs_its_own_file(self):
		result = self.run_script("backup", STUB_SITES="site1.test")
		self.assertEqual(result.returncode, 1)
		self.assertIn("wrote no database file for site2.test", result.stderr)

	def test_retention_keeps_the_newest_five_and_touches_nothing_else(self):
		self.root.mkdir()
		old = [f"20260101T00000{i}Z" for i in range(7)]
		for name in old:
			(self.root / name).mkdir()
			(self.root / name / "x-database.sql.gz").write_text("old")
		(self.root / "notes").mkdir()
		(self.root / "20260101T000000Z.txt").write_text("keep")
		outside = self.tmp / "elsewhere"
		outside.mkdir()
		(outside / "keep").write_text("keep")
		(self.root / "20250101T000000Z").symlink_to(outside)
		scheduled = self.bench / "sites" / "site1.test" / "private" / "backups"
		scheduled.mkdir(parents=True)
		(scheduled / "20250101_000000-site1_test-database.sql.gz").write_text("scheduled")

		result = self.run_script("backup")
		self.assertEqual(result.returncode, 0, result.stderr)
		runs = self.runs()
		self.assertEqual(len(runs), 5)
		self.assertEqual(runs[:4], old[3:])
		self.assertTrue((self.root / "notes").is_dir())
		self.assertTrue((self.root / "20260101T000000Z.txt").is_file())
		self.assertTrue((self.root / "20250101T000000Z").is_symlink())
		self.assertTrue((outside / "keep").is_file())
		self.assertEqual(sorted(p.name for p in scheduled.iterdir()), ["20250101_000000-site1_test-database.sql.gz"])

	def test_retention_runs_only_after_a_good_backup(self):
		self.root.mkdir()
		for i in range(7):
			(self.root / f"20260101T00000{i}Z").mkdir()
		self.assertEqual(self.run_script("backup", STUB_BACKUP="fail").returncode, 1)
		self.assertEqual(len(self.runs()), 7)

	def test_too_little_disk_stops_before_the_backup(self):
		result = self.run_script("backup", PRE_MIGRATE_MIN_FREE_MB=str(10**12))
		self.assertEqual(result.returncode, 1)
		self.assertIn("MB free on the disk that holds", result.stderr)
		self.assertIn("PRE-MIGRATE BACKUP FAILED", result.stderr)
		self.assertEqual(self.call_log(), [])

	def test_the_backup_directory_may_not_be_inside_sites(self):
		for bad in (str(self.bench / "sites" / "x"), str(self.bench / "sites"), "relative/dir", "/"):
			result = self.run_script("backup", PRE_MIGRATE_BACKUP_DIR=bad)
			self.assertEqual(result.returncode, 1, bad)
			self.assertIn("PRE_MIGRATE_BACKUP_DIR", result.stderr, bad)
		self.assertEqual(self.call_log(), [])

	def test_it_refuses_a_directory_that_is_not_a_bench(self):
		empty = self.tmp / "empty"
		empty.mkdir()
		result = self.run_script("backup", BENCH_DIR=str(empty))
		self.assertEqual(result.returncode, 1)
		self.assertIn("is not a bench", result.stderr)

	def test_unknown_modes_print_usage(self):
		for args in ((), ("migrate",), ("backup", "extra")):
			result = self.run_script(*args)
			self.assertEqual(result.returncode, 2, args)
			self.assertIn("usage:", result.stderr)
		self.assertEqual(self.call_log(), [])


class UpstreamTests(_ScratchBench):
	def git(self, cwd, *args):
		return subprocess.run(
			[
				"git",
				"-c",
				"user.name=Test",
				"-c",
				"user.email=test@example.invalid",
				"-c",
				"commit.gpgsign=false",
				"-c",
				"tag.gpgsign=false",
				"-c",
				"init.defaultBranch=version-16",
				*args,
			],
			cwd=cwd,
			check=True,
			capture_output=True,
			text=True,
			env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(self.tmp)},
		).stdout.strip()

	def setUp(self):
		super().setUp()
		shutil.rmtree(self.bench / "apps" / "frappe")
		self.old, self.new = {}, {}
		for app, annotated in (("frappe", False), ("erpnext", True)):
			src = self.tmp / "remotes" / app
			src.mkdir(parents=True)
			self.git(src, "init", "-q")
			(src / "version.txt").write_text("16.1.0\n")
			self.git(src, "add", "version.txt")
			self.git(src, "commit", "-q", "-m", "16.1.0")
			self.git(src, *(["tag", "-a", "-m", "v16.1.0"] if annotated else ["tag"]), "v16.1.0")
			self.git(self.bench / "apps", "clone", "-q", "-o", "upstream", str(src), app)
			(src / "version.txt").write_text("16.2.0\n")
			self.git(src, "commit", "-q", "-am", "16.2.0")
			self.git(src, *(["tag", "-a", "-m", "v16.2.0"] if annotated else ["tag"]), "v16.2.0")
			(src / "version.txt").write_text("16.3.0-dev\n")
			self.git(src, "commit", "-q", "-am", "branch head")
			self.old[app] = self.git(self.bench / "apps" / app, "rev-parse", "HEAD")
			self.new[app] = self.git(src, "rev-parse", "v16.2.0^{commit}")

	def head(self, app):
		return self.git(self.bench / "apps" / app, "rev-parse", "HEAD")

	def assertUntouched(self):
		self.assertEqual(self.call_log(), [])
		for app in ("frappe", "erpnext"):
			self.assertEqual(self.head(app), self.old[app])
		self.assertEqual(self.runs(), [])

	def test_it_refuses_without_both_tags(self):
		for args in ((), ("v16.2.0",)):
			result = self.run_script("upstream", *args)
			self.assertEqual(result.returncode, 2, args)
			self.assertIn("usage:", result.stderr)
		self.assertUntouched()

	def test_it_refuses_a_branch_name_or_anything_but_a_release_tag(self):
		for args in (
			("version-16", "v16.2.0"),
			("v16.2.0", "version-16"),
			("v16.2.0", "develop"),
			("HEAD", "v16.2.0"),
			("v16.2", "v16.2.0"),
			("v15.0.0", "v16.2.0"),
			("16.2.0", "v16.2.0"),
			("v16.2.0-beta.1", "v16.2.0"),
			("v16.2.0\n", "v16.2.0"),
			("", "v16.2.0"),
		):
			result = self.run_script("upstream", *args)
			self.assertEqual(result.returncode, 1, args)
			self.assertIn("is not a release tag", result.stderr, args)
		self.assertUntouched()
		# ...and nothing was even fetched.
		self.assertEqual(self.git(self.bench / "apps" / "frappe", "tag", "--list", "v16.2.0"), "")

	def test_a_tag_that_does_not_exist_stops_before_the_backup(self):
		result = self.run_script("upstream", "v16.9.0", "v16.2.0")
		self.assertEqual(result.returncode, 1)
		self.assertIn("could not fetch the tag v16.9.0 of frappe", result.stderr)
		self.assertUntouched()

	def test_local_changes_stop_it_before_anything_changes(self):
		(self.bench / "apps" / "erpnext" / "version.txt").write_text("edited\n")
		result = self.run_script("upstream", "v16.2.0", "v16.2.0")
		self.assertEqual(result.returncode, 1)
		self.assertIn("apps/erpnext has changes to tracked files", result.stderr)
		self.assertEqual(self.call_log(), [])
		self.assertEqual(self.runs(), [])

	def test_it_backs_up_then_checks_out_exactly_the_tags_then_runs_bench(self):
		result = self.run_script("upstream", "v16.2.0", "v16.2.0")
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertEqual(self.head("frappe"), self.new["frappe"])
		self.assertEqual(self.head("erpnext"), self.new["erpnext"])
		(run,) = self.runs()
		self.assertEqual(
			self.call_log(),
			[
				f"--site all backup --backup-path {self.root / run} --ignore-backup-conf",
				f"frappe-head-at-backup {self.old['frappe']}",
				"setup requirements",
				"--site all migrate",
				"build",
			],
		)
		self.assertIn("restart the bench now: sudo systemctl restart frappe-bench", result.stdout)

	def test_a_failed_backup_switches_nothing(self):
		result = self.run_script("upstream", "v16.2.0", "v16.2.0", STUB_BACKUP="fail")
		self.assertEqual(result.returncode, 1)
		self.assertIn("PRE-MIGRATE BACKUP FAILED", result.stderr)
		for app in ("frappe", "erpnext"):
			self.assertEqual(self.head(app), self.old[app])
		self.assertNotIn("setup requirements", self.call_log())

	def test_a_failure_after_the_switch_says_where_the_backup_is(self):
		result = self.run_script("upstream", "v16.2.0", "v16.2.0", STUB_FAIL="--site all migrate")
		self.assertEqual(result.returncode, 1)
		(run,) = self.runs()
		self.assertIn("UPSTREAM UPDATE FAILED", result.stderr)
		self.assertIn(str(self.root / run), result.stderr)
		self.assertIn(f"frappe was at commit {self.old['frappe']}", result.stderr)
		self.assertNotIn("build", self.call_log())


if __name__ == "__main__":
	unittest.main()
