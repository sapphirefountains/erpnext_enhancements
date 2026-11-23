"""Bench-free tests for the pre-migrate backup and the pinned upstream update (v1.839.0).

Two rules, both enforced by files that run nowhere but on a VM:

* every app deploy (infra/cloudbuild-deploy.yaml) checks that Redis answers and takes a
  database backup with bench's own `backup` command before `bench migrate`, and stops when
  either fails;
* an upstream update (infra/cloudbuild-upstream.yaml) moves Frappe and ERPNext only to two
  named release tags, never to a branch head, behind the same backup.

Both run infra/bench_update.sh. These tests read the two Cloud Build files (a small reader
for their one block scalar, cross-checked with PyYAML where it is installed), run the
upstream file's tag check with `sh`, and run the script itself against a scratch bench with
a stub `bench` (and a stub `df` that can report a chosen free figure) on PATH and real git
repositories standing in for frappe and erpnext. They also cover the rule that only one backup
runs at a time (an flock on the backup directory's lock file), the free-space rule (twice
the newest kept backup plus a margin) and the Redis check that comes before a migrate. That
check runs the bench's own Python: here a stand-in `env/bin/python` that loads a stub `redis`
module, and, in one class, the real Redis client against a small socket server. The script
tests need bash, git, gzip and flock, so they run on Linux; CI sets REQUIRE_DEPLOY_SCRIPT_TOOLS
so that a missing tool fails instead of skipping.

Also here, from a review of the first version: only the backup is written under a narrow file
mode (nginx serves what an update checks out and builds); a backup is written under an
unfinished name and renamed only when it is whole; one run at a time holds the bench's lock,
which the deploy's remote command is run for real to show, with stand-ins for sudo, git,
redis-cli and systemctl; and an update refuses a tag lower than what is installed.

Run: python -m unittest erpnext_enhancements.tests.test_pre_migrate_backup -v
"""

import importlib.util
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INFRA = REPO_ROOT / "infra"
SCRIPT = INFRA / "bench_update.sh"
DEPLOY_YAML = INFRA / "cloudbuild-deploy.yaml"
UPSTREAM_YAML = INFRA / "cloudbuild-upstream.yaml"
CLOUD_BUILD_TF = INFRA / "cloud_build.tf"

RUN_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")

#: The release pair every copy-paste example names: what production runs. Move it, and every
#: example with it, when production moves. An example left on an older pair is a downgrade
#: waiting to be pasted.
EXAMPLE_FRAPPE_TAG = "v16.60.2"
EXAMPLE_ERPNEXT_TAG = "v16.60.0"

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


def _script_lock_wait_default():
	match = re.search(r"wait_s=\$\{PRE_MIGRATE_LOCK_WAIT_S:-(\d+)\}", SCRIPT.read_text(encoding="utf-8"))
	return int(match.group(1))


def _script_keep():
	"""How many backups the script keeps."""
	return int(re.search(r"^readonly KEEP=(\d+)$", SCRIPT.read_text(encoding="utf-8"), re.M).group(1))


def _timeout_s(path):
	return int(re.search(r"^timeout: '(\d+)s'$", path.read_text(encoding="utf-8"), re.M).group(1))


class DeployYamlTests(unittest.TestCase):
	def setUp(self):
		self.block = _script_block(DEPLOY_YAML)

	def test_the_step_stops_on_the_first_failure(self):
		self.assertEqual(self.block.splitlines()[0], "set -e")

	def test_the_backup_comes_before_the_reset_and_migrate_in_one_and_chain(self):
		"""Fetch, Redis check, backup, reset: a failed check or backup leaves the app's code
		where it was. Only the script is taken from the fetched commit first, so the deploy
		that first carries it can run it."""
		self.assertEqual(
			_remote_commands(self.block),
			[
				r"exec 9>>\$$HOME/.frappe-bench-deploy.lock",
				"flock --verbose -w 900 9",
				"sudo -u frappe bash -c 'cd /home/frappe/frappe-bench",
				r"export PATH=\$$PATH:/home/frappe/.local/bin",
				"exec 9>>.deploy.lock",
				"flock --verbose -w 900 9",
				"git -C apps/erpnext_enhancements fetch upstream main 9>&-",
				"git -C apps/erpnext_enhancements checkout FETCH_HEAD -- infra/bench_update.sh 9>&-",
				"bash apps/erpnext_enhancements/infra/bench_update.sh redis 9>&-",
				"bash apps/erpnext_enhancements/infra/bench_update.sh backup 9>&-",
				"git -C apps/erpnext_enhancements reset --hard FETCH_HEAD 9>&-",
				"bench --site all migrate 9>&-",
				"bench build 9>&-",
				"redis-cli -p 13000 FLUSHDB 9>&-",
				"redis-cli -p 11000 FLUSHDB 9>&-'",
				"sudo systemctl restart frappe-bench",
				"exec 9>&-",
			],
		)

	def test_the_vm_receives_a_valid_command_with_the_backup_first(self):
		remote = _remote_string(self, self.block)
		self.assertIn(
			"export PATH=$PATH:/home/frappe/.local/bin && "
			"exec 9>>.deploy.lock && flock --verbose -w 900 9 && "
			"git -C apps/erpnext_enhancements fetch upstream main 9>&- && "
			"git -C apps/erpnext_enhancements checkout FETCH_HEAD -- infra/bench_update.sh 9>&- && "
			"bash apps/erpnext_enhancements/infra/bench_update.sh redis 9>&- && "
			"bash apps/erpnext_enhancements/infra/bench_update.sh backup 9>&- && "
			"git -C apps/erpnext_enhancements reset --hard FETCH_HEAD 9>&- && "
			"bench --site all migrate 9>&- && ",
			remote,
		)
		self.assertTrue(
			remote.startswith(
				"exec 9>>$HOME/.frappe-bench-deploy.lock && flock --verbose -w 900 9 && sudo -u frappe "
			),
			remote,
		)

	def test_redis_is_asked_before_the_backup_the_reset_and_the_migrate(self):
		"""A migrate started while Redis is down stops partway, so the deploy asks first, with
		the script it has just fetched, and a failure there leaves everything as it was."""
		commands = _remote_commands(self.block)
		check = commands.index("bash apps/erpnext_enhancements/infra/bench_update.sh redis 9>&-")
		fetched = "git -C apps/erpnext_enhancements checkout FETCH_HEAD -- infra/bench_update.sh 9>&-"
		self.assertEqual(commands[check - 1], fetched)
		for later in (
			"bash apps/erpnext_enhancements/infra/bench_update.sh backup 9>&-",
			"git -C apps/erpnext_enhancements reset --hard FETCH_HEAD 9>&-",
			"bench --site all migrate 9>&-",
		):
			self.assertLess(check, commands.index(later), later)

	def test_one_run_at_a_time_two_locks_in_the_one_chain(self):
		"""The deploy user's lock is taken first and held through the restart: the restart is
		not the remote shell's last command, because a shell runs its last command in its
		own place and the lock would go with it. The bench's lock is taken before the fetch,
		which replaces the script a run in progress is reading, and is held by the frappe
		user's shell alone: every command after it ends with `9>&-`, so nothing a command
		leaves running can keep the lock. Both waits are the script's."""
		commands = _remote_commands(self.block)
		wait = f"flock --verbose -w {_script_lock_wait_default()} 9"
		self.assertEqual(commands[:2], [r"exec 9>>\$$HOME/.frappe-bench-deploy.lock", wait])
		self.assertEqual(commands[-2:], ["sudo systemctl restart frappe-bench", "exec 9>&-"])
		opened = commands.index(f"exec 9>>{RUN_LOCK_NAME}")
		self.assertEqual(commands[opened + 1], wait)
		self.assertLess(opened, commands.index("git -C apps/erpnext_enhancements fetch upstream main 9>&-"))
		under_the_lock = commands[opened + 2 : -2]
		self.assertEqual(len(under_the_lock), 9)
		self.assertTrue(under_the_lock[-1].endswith(" 9>&-'"), under_the_lock[-1])
		for command in under_the_lock:
			self.assertTrue(command.rstrip("'").endswith(" 9>&-"), command)

	def test_nothing_after_the_backup_can_run_when_it_fails(self):
		# One `&&` chain: no `;` or `||` anywhere in the remote command could let migrate,
		# the redis flush or the restart run after a failed backup.
		start = self.block.index('--command="')
		command = self.block[start : self.block.index('"\n', start)]
		self.assertNotIn(";", command)
		self.assertNotIn("||", command)

	def test_no_new_substitutions_and_more_time(self):
		"""1200s for the deploy and its backup, plus the longest the backup may wait for
		another run's backup: a timeout ends the SSH session mid-migrate."""
		self.assertEqual(_top_level_block(DEPLOY_YAML, "substitutions"), {"_ALLOW_SKIP": "'false'"})
		self.assertEqual(_script_lock_wait_default(), 900)
		self.assertGreaterEqual(_timeout_s(DEPLOY_YAML), 1200 + _script_lock_wait_default())


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
			commands[-3:],
			[
				"bash apps/erpnext_enhancements/infra/bench_update.sh upstream $$FRAPPE_TAG $$ERPNEXT_TAG'",
				"sudo systemctl restart frappe-bench",
				"exec 9>&-",
			],
		)

	def test_it_takes_the_deploy_users_lock_first_and_the_script_takes_the_benchs(self):
		commands = _remote_commands(self.block)
		self.assertEqual(
			commands[:3],
			[
				r"exec 9>>\$$HOME/.frappe-bench-deploy.lock",
				f"flock --verbose -w {_script_lock_wait_default()} 9",
				"sudo -u frappe bash -c 'cd /home/frappe/frappe-bench",
			],
		)
		# The same file a deploy's remote shell holds.
		self.assertEqual(commands[:2], _remote_commands(_script_block(DEPLOY_YAML))[:2])
		self.assertNotIn(RUN_LOCK_NAME, self.block)
		self.assertIn("acquire_run_lock", _code(SCRIPT.read_text(encoding="utf-8")))

	def test_a_downgrade_cannot_be_allowed_from_the_trigger(self):
		self.assertNotIn("ALLOW_DOWNGRADE", _code(self.text))
		self.assertNotIn("ALLOW_DOWNGRADE", _code(DEPLOY_YAML.read_text(encoding="utf-8")))

	def test_the_vm_receives_a_valid_command_with_both_tags(self):
		remote = _remote_string(
			self, self.block, FRAPPE_TAG=EXAMPLE_FRAPPE_TAG, ERPNEXT_TAG=EXAMPLE_ERPNEXT_TAG
		)
		self.assertTrue(
			remote.endswith(
				"bash apps/erpnext_enhancements/infra/bench_update.sh upstream v16.60.2 v16.60.0' "
				"&& sudo systemctl restart frappe-bench && exec 9>&-"
			),
			remote,
		)

	def test_the_timeout_covers_the_lock_wait(self):
		self.assertGreaterEqual(_timeout_s(UPSTREAM_YAML), 2400 + _script_lock_wait_default())

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

		self.assertEqual(run(EXAMPLE_FRAPPE_TAG, EXAMPLE_ERPNEXT_TAG).returncode, 0)
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


class ExampleTagTests(unittest.TestCase):
	"""The examples are commands people paste. Each names the one release pair, and none of
	these files names any other v16 release, in an example or in a note on what was verified."""

	FILES = (SCRIPT, UPSTREAM_YAML, INFRA / "README.md", INFRA / "WORKFLOW.md")

	def test_every_example_names_the_one_release_pair(self):
		found = []
		for path in self.FILES:
			text = path.read_text(encoding="utf-8")
			found += re.findall(r"_FRAPPE_TAG=(v[0-9.]+),_ERPNEXT_TAG=(v[0-9.]+)", text)
			found += re.findall(r"bench_update\.sh upstream (v[0-9.]+) (v[0-9.]+)", text)
		self.assertGreaterEqual(len(found), 5)
		self.assertEqual(set(found), {(EXAMPLE_FRAPPE_TAG, EXAMPLE_ERPNEXT_TAG)})

	def test_no_file_names_another_release(self):
		pair = {EXAMPLE_FRAPPE_TAG, EXAMPLE_ERPNEXT_TAG}
		for path in self.FILES:
			named = set(re.findall(r"\bv16\.[0-9]+\.[0-9]+\b", path.read_text(encoding="utf-8")))
			self.assertLessEqual(named, pair, path.name)
			self.assertIn(EXAMPLE_FRAPPE_TAG, named, path.name)

	def test_the_pair_is_two_release_tags(self):
		for tag in (EXAMPLE_FRAPPE_TAG, EXAMPLE_ERPNEXT_TAG):
			self.assertRegex(tag, _script_tag_re())


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
if [ -n "${STUB_RUN_LOCK:-}" ]; then
	if flock -n "$STUB_RUN_LOCK" true; then echo run-lock-free; else echo run-lock-held; fi >> "$STUB_CALLS"
fi
if [ -n "${STUB_LEAVE_CHILD:-}" ] && [ "$*" = "$STUB_LEAVE_CHILD" ]; then
	# A process that outlives this command, holding every descriptor the command was given.
	sleep 60 </dev/null >/dev/null 2>&1 &
	printf '%s\n' "$!" > "$STUB_CHILD_PID"
fi
if [ "${1:-}" = "--site" ] && [ "${3:-}" = "backup" ]; then
	printf 'frappe-head-at-backup %s\n' "$(git -C apps/frappe rev-parse HEAD 2>/dev/null || echo none)" >> "$STUB_CALLS"
	dir=""
	args=("$@")
	for ((i = 0; i < ${#args[@]}; i++)); do
		if [ "${args[i]}" = "--backup-path" ]; then dir=${args[i + 1]}; fi
	done
	if [ -n "${STUB_BACKUP_SLEEP:-}" ]; then
		printf 'backup-begin\n' >> "$STUB_CALLS"
		sleep "$STUB_BACKUP_SLEEP"
	fi
	case "${STUB_BACKUP:-ok}" in
	fail) echo "Backup failed for Site x" >&2; exit 1 ;;
	fail-leave-child)
		# A process that outlives the failed backup, holding every descriptor bench was given.
		sleep 60 </dev/null >/dev/null 2>&1 &
		printf '%s\n' "$!" > "$STUB_CHILD_PID"
		echo "Backup failed for Site x" >&2
		exit 1
		;;
	empty) exit 0 ;;
	killed)
		# The whole run dies with the first dump half written, and no trap gets to run.
		printf 'a dump that is cut off' | gzip | head -c 12 > "$dir/20261119_000000-site1_test-database.sql.gz"
		kill -KILL "$PPID"
		exit 1
		;;
	esac
	for s in $STUB_SITES; do
		slug=${s//./_}
		case "${STUB_BACKUP:-ok}" in
		cut-off) printf 'a dump that is cut off' | gzip | head -c 12 > "$dir/20261119_000000-$slug-database.sql.gz" ;;
		encrypted) printf 'not gzip data' > "$dir/20261119_000000-$slug-database-enc.sql.gz" ;;
		*) printf 'dump' | gzip > "$dir/20261119_000000-$slug-database.sql.gz" ;;
		esac
		printf '{}' > "$dir/20261119_000000-$slug-site_config_backup.json"
	done
	if [ -n "${STUB_BACKUP_SLEEP:-}" ]; then printf 'backup-end\n' >> "$STUB_CALLS"; fi
	exit 0
fi
if [ -n "${STUB_LOCK:-}" ]; then
	if flock -n "$STUB_LOCK" true; then echo lock-free; else echo lock-held; fi >> "$STUB_CALLS"
fi
if [ -n "${STUB_WRITES:-}" ]; then
	# What an install or a build does: new directories and files, under the mask it was given.
	all="$*"
	made="sites/assets/made-by-${all// /-}"
	mkdir -p "$made"
	printf 'x' > "$made/file"
fi
if [ -n "${STUB_FAIL:-}" ] && [ "$*" = "$STUB_FAIL" ]; then exit 1; fi
exit 0
"""

#: A stand-in `df`: reports STUB_DF_AVAIL_KB as the free space when it is set, else runs the
#: real one (whose path replaces @REAL_DF@).
STUB_DF = r"""#!/usr/bin/env bash
if [ -n "${STUB_DF_AVAIL_KB:-}" ]; then
	printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\n'
	printf 'stub 99999999 1 %s 1%% /\n' "$STUB_DF_AVAIL_KB"
	exit 0
fi
exec @REAL_DF@ "$@"
"""

#: A stand-in for the bench's own interpreter, <bench>/env/bin/python: the real one, with a stub
#: `redis` module first on its path (@PYTHON@ and @STUB_PY@ are replaced). With STUB_REDIS_REAL
#: set it finds the real client instead: the interpreter's own, or, when the value is a
#: directory, the one installed there. A wrapper, not a symlink: run through a symlink outside
#: its environment, a virtualenv's python does not find that environment's packages.
STUB_PYTHON = r"""#!/usr/bin/env bash
case "${STUB_REDIS_REAL:-}" in
"") export PYTHONPATH=@STUB_PY@ ;;
/*) export PYTHONPATH=$STUB_REDIS_REAL ;;
esac
exec @PYTHON@ "$@"
"""

#: The stub `redis` module. Each ping is written to STUB_CALLS. An address that contains a word
#: of STUB_REDIS_DOWN does not answer, one that contains a word of STUB_REDIS_REFUSE refuses the
#: sign-in, and from ping number STUB_REDIS_DOWN_FROM of the run onward nothing answers.
STUB_REDIS = r"""import os

from . import exceptions


class Redis:
	def __init__(self, url, options):
		self.url = url
		self.options = options

	@classmethod
	def from_url(cls, url, **options):
		if not url.startswith(("redis://", "rediss://", "unix://")):
			raise ValueError("Redis URL must specify one of the following schemes")
		return cls(url, options)

	def ping(self):
		calls = os.environ["STUB_CALLS"]
		done = 0
		if os.path.exists(calls):
			with open(calls) as handle:
				done = sum(1 for line in handle if line.startswith("redis-ping "))
		with open(calls, "a") as handle:
			handle.write(
				"redis-ping %s connect=%s read=%s\n"
				% (self.url, self.options.get("socket_connect_timeout"), self.options.get("socket_timeout"))
			)
		down_from = int(os.environ.get("STUB_REDIS_DOWN_FROM") or 0)
		if down_from and done + 1 >= down_from:
			raise exceptions.ConnectionError("Error 111 connecting. Connection refused.")
		if any(word in self.url for word in os.environ.get("STUB_REDIS_DOWN", "").split()):
			raise exceptions.ConnectionError("Error 111 connecting to %s. Connection refused." % self.url)
		if any(word in self.url for word in os.environ.get("STUB_REDIS_REFUSE", "").split()):
			raise exceptions.AuthenticationError("Authentication required.")
		return True
"""

STUB_REDIS_EXCEPTIONS = r"""class RedisError(Exception):
	pass


class ConnectionError(RedisError):
	pass


class AuthenticationError(ConnectionError):
	pass


class ResponseError(RedisError):
	pass
"""

#: The two addresses the scratch bench is configured for.
CACHE_URL = "redis://127.0.0.1:13000"
QUEUE_URL = "redis://127.0.0.1:11000"

LOCK_NAME = ".bench_update.lock"
RUN_LOCK_NAME = ".deploy.lock"
PARTIAL_PREFIX = ".partial-"

#: The series of the Redis client frappe pins at the example release (`redis~=7.1.0`,
#: pyproject.toml:73 at v16.60.2): the client the check runs with on a bench.
FRAPPE_REDIS_SERIES = "7.1."


class _ScratchBench(unittest.TestCase):
	SITES = ("site1.test", "site2.test")

	def setUp(self):
		_tool_or_skip(
			self,
			sys.platform.startswith("linux") and all(shutil.which(t) for t in ("bash", "git", "gzip", "flock", "df")),
			"Linux with bash, git, gzip, flock and df",
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
		df = self.stubs / "df"
		df.write_text(STUB_DF.replace("@REAL_DF@", shutil.which("df")))
		df.chmod(df.stat().st_mode | stat.S_IXUSR)
		stub_py = self.tmp / "stub-python"
		(stub_py / "redis").mkdir(parents=True)
		(stub_py / "redis" / "__init__.py").write_text(STUB_REDIS)
		(stub_py / "redis" / "exceptions.py").write_text(STUB_REDIS_EXCEPTIONS)
		self.python = self.bench / "env" / "bin" / "python"
		self.python.parent.mkdir(parents=True)
		wrapper = STUB_PYTHON.replace("@PYTHON@", sys.executable).replace("@STUB_PY@", str(stub_py))
		self.python.write_text(wrapper)
		self.python.chmod(self.python.stat().st_mode | stat.S_IXUSR)
		self.common_config = self.bench / "sites" / "common_site_config.json"
		self.write_config(redis_cache=CACHE_URL, redis_queue=QUEUE_URL)
		self.calls = self.tmp / "calls.log"
		self.root = self.bench / "pre-migrate-backups"
		self.lock = self.root / LOCK_NAME
		self.run_lock = self.bench / RUN_LOCK_NAME

	def write_config(self, **config):
		self.common_config.write_text(json.dumps(config))

	def script_env(self, **env):
		"""The script's environment; a value of None removes that variable."""
		full = {
			"PATH": f"{self.stubs}{os.pathsep}{os.environ.get('PATH', '')}",
			"HOME": str(self.tmp),
			"BENCH_DIR": str(self.bench),
			"PRE_MIGRATE_MIN_FREE_MB": "0",
			"STUB_CALLS": str(self.calls),
			"STUB_SITES": " ".join(self.SITES),
			"GIT_CONFIG_NOSYSTEM": "1",
		}
		for name, value in env.items():
			if value is None:
				full.pop(name, None)
			else:
				full[name] = value
		return full

	def run_script(self, *args, umask=None, **env):
		"""Run the script; with `umask`, as a caller whose file mode mask is that."""
		command = ["bash", str(SCRIPT), *args]
		if umask is not None:
			command = ["bash", "-c", f'umask {umask}; exec bash "$0" "$@"', str(SCRIPT), *args]
		return subprocess.run(
			command, env=self.script_env(**env), capture_output=True, text=True, timeout=120
		)

	def start_script(self, *args, **env):
		"""The script started in the background; the caller waits for it."""
		proc = subprocess.Popen(
			["bash", str(SCRIPT), *args],
			env=self.script_env(**env),
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
		)
		self.addCleanup(proc.kill)
		return proc

	def call_log(self):
		return self.calls.read_text().splitlines() if self.calls.exists() else []

	def bench_calls(self):
		"""The call log without the Redis checks."""
		return [line for line in self.call_log() if not line.startswith("redis-ping ")]

	def pings(self):
		"""The addresses pinged, in order."""
		return [line.split()[1] for line in self.call_log() if line.startswith("redis-ping ")]

	def runs(self):
		if not self.root.is_dir():
			return []
		return sorted(p.name for p in self.root.iterdir() if p.is_dir() and not p.is_symlink() and RUN_RE.match(p.name))

	def partials(self):
		"""The unfinished backups in the backup directory."""
		if not self.root.is_dir():
			return []
		return sorted(p.name for p in self.root.iterdir() if p.name.startswith(PARTIAL_PREFIX))

	@staticmethod
	def mode(path):
		return stat.S_IMODE(os.lstat(path).st_mode)

	def hold_lock(self, lock=None):
		"""Hold a lock file (the backup's by default) as another run would, until the handle closes."""
		import fcntl

		lock = lock or self.lock
		lock.parent.mkdir(exist_ok=True)
		handle = open(lock, "a")
		self.addCleanup(handle.close)
		fcntl.flock(handle, fcntl.LOCK_EX)
		return handle

	def lock_is_free(self, lock=None):
		import fcntl

		with open(lock or self.lock, "a") as handle:
			try:
				fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
			except BlockingIOError:
				return False
			fcntl.flock(handle, fcntl.LOCK_UN)
			return True

	def wait_for(self, condition, what, seconds=30):
		deadline = time.monotonic() + seconds
		while not condition():
			if time.monotonic() > deadline:
				self.fail(f"timed out waiting for {what}")
			time.sleep(0.05)


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
				'rm -rf -- "${root:?}/${runs[i]:?}" ||',
				'rm -rf -- "${stale:?}" || backup_failed "could not remove the unfinished backup $stale"',
				'rm -rf -- "${PARTIAL:?}" || '
				'warn "could not remove the unfinished backup $PARTIAL; the next backup removes it"',
			],
		)
		# Both of the last two are directories named <backup dir>/.partial-<UTC time>.
		self.assertIn('for stale in "$root"/"$PARTIAL_PREFIX"*/; do', code)
		self.assertIn("PARTIAL=$root/$PARTIAL_PREFIX$stamp", code)
		self.assertIn(f"readonly PARTIAL_PREFIX={PARTIAL_PREFIX}", code)

	def test_an_unfinished_backup_can_never_be_taken_for_a_finished_one(self):
		"""Retention, the free-space rule and a person listing the directory all go by the
		name: a run's is a bare UTC time, an unfinished one's starts with a dot."""
		code = _code(SCRIPT.read_text(encoding="utf-8"))
		self.assertIn("readonly RUN_RE='^[0-9]{8}T[0-9]{6}Z$'", code)
		self.assertIsNone(RUN_RE.match(PARTIAL_PREFIX + "20261119T201500Z"))
		self.assertIn('mv -T -- "$PARTIAL" "$run_dir"', code)

	def test_the_narrow_file_mode_is_the_backups_alone(self):
		"""nginx serves what an update checks out and builds, as another user. The script's own
		mask is the ordinary one; the backup narrows it and puts it back."""
		code = _code(SCRIPT.read_text(encoding="utf-8"))
		masks = [line.strip() for line in code.splitlines() if line.strip().startswith("umask")]
		self.assertEqual(masks, ["umask 022", "umask 077", "umask 022"])
		backup = code[code.index("take_backup() {") : code.index("check_tag() {")]
		self.assertEqual(backup.count("umask 077"), 1)
		self.assertEqual(backup.count("umask 022"), 1)
		self.assertLess(backup.index("umask 077"), backup.index('mkdir -p -- "$root"'))

	def test_the_script_and_the_deploy_file_name_one_bench_lock(self):
		code = _code(SCRIPT.read_text(encoding="utf-8"))
		self.assertIn(f"readonly RUN_LOCK_NAME={RUN_LOCK_NAME}", code)
		self.assertIn("local wait_s lock=$BENCH_DIR/$RUN_LOCK_NAME", code)
		commands = _remote_commands(_script_block(DEPLOY_YAML))
		opened = commands.index(f"exec 9>>{RUN_LOCK_NAME}")
		self.assertEqual(commands[opened - 2], "sudo -u frappe bash -c 'cd /home/frappe/frappe-bench")


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
		# bench wrote into the unfinished name; the directory was renamed once it was whole.
		unfinished = self.root / (PARTIAL_PREFIX + run)
		self.assertIn(f"--site all backup --backup-path {unfinished} --ignore-backup-conf", self.call_log())
		self.assertEqual(self.partials(), [])
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

	def test_three_backups_are_kept_and_the_margin_is_5_gb(self):
		"""Sized from one measurement on production (2026-11-23): a backup is 681 MiB, so
		three kept are about 2 GB and the check wants about 6.4 GB free, on a disk that had
		14 GB free. Five kept would have left little room before deploys began to refuse."""
		self.assertEqual(_script_keep(), 3)
		self.assertIn("margin_mb=${PRE_MIGRATE_MIN_FREE_MB:-5120}", SCRIPT.read_text(encoding="utf-8"))

	def test_retention_keeps_the_newest_few_and_touches_nothing_else(self):
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
		keep = _script_keep()
		self.assertEqual(len(runs), keep)
		self.assertEqual(runs[:-1], old[len(old) - (keep - 1) :])
		self.assertNotIn(runs[-1], old)
		self.assertTrue((self.root / "notes").is_dir())
		self.assertTrue((self.root / "20260101T000000Z.txt").is_file())
		self.assertTrue((self.root / "20250101T000000Z").is_symlink())
		self.assertTrue((outside / "keep").is_file())
		self.assertEqual(sorted(p.name for p in scheduled.iterdir()), ["20250101_000000-site1_test-database.sql.gz"])
		# The lock file sits beside the runs and outlives the pruning.
		self.assertTrue(self.lock.is_file())
		self.assertEqual(stat.S_IMODE(self.lock.stat().st_mode), 0o600)

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

	def test_it_stops_clearly_without_flock(self):
		tools = self.tmp / "tools"
		tools.mkdir()
		for tool in ("bash", "basename", "dirname", "id", "env"):
			(tools / tool).symlink_to(shutil.which(tool))
		result = self.run_script("backup", PATH=f"{self.stubs}{os.pathsep}{tools}")
		self.assertEqual(result.returncode, 1, result.stderr)
		self.assertIn("PRE-MIGRATE BACKUP FAILED: flock is not installed; it ships in util-linux", result.stderr)
		self.assertEqual(self.call_log(), [])
		self.assertFalse(self.root.exists())

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
		for args in ((), ("migrate",), ("backup", "extra"), ("redis", "extra")):
			result = self.run_script(*args)
			self.assertEqual(result.returncode, 2, args)
			self.assertIn("usage:", result.stderr)
		self.assertEqual(self.call_log(), [])

	def test_only_the_owner_can_read_a_backup_whatever_mask_the_caller_has(self):
		result = self.run_script("backup", umask="022")
		self.assertEqual(result.returncode, 0, result.stderr)
		(run,) = self.runs()
		self.assertEqual(self.mode(self.root), 0o700)
		self.assertEqual(self.mode(self.root / run), 0o700)
		self.assertEqual(self.mode(self.lock), 0o600)
		files = list((self.root / run).iterdir())
		self.assertEqual(len(files), 2 * len(self.SITES))
		for path in files:
			self.assertEqual(self.mode(path), 0o600, path.name)

	def test_a_dump_that_is_cut_off_is_not_kept(self):
		"""bench exited cleanly and every site has a file, but the file is not whole."""
		result = self.run_script("backup", STUB_BACKUP="cut-off")
		self.assertEqual(result.returncode, 1)
		self.assertIn(
			"PRE-MIGRATE BACKUP FAILED: 20261119_000000-site1_test-database.sql.gz is not a whole gzip file",
			result.stderr,
		)
		self.assertEqual(self.runs(), [])
		self.assertEqual(self.partials(), [])

	def test_an_encrypted_dump_is_kept_without_the_gzip_test(self):
		result = self.run_script("backup", STUB_BACKUP="encrypted")
		self.assertEqual(result.returncode, 0, result.stderr)
		(run,) = self.runs()
		self.assertTrue((self.root / run / "20261119_000000-site1_test-database-enc.sql.gz").is_file())
		# ...and the log does not let it pass for a tested one.
		for site in self.SITES:
			name = f"20261119_000000-{site.replace('.', '_')}-database-enc.sql.gz"
			note = f"note: {name} is encrypted, so only its size was checked, not its content"
			self.assertIn(note, result.stdout)
		plain = self.run_script("backup", PRE_MIGRATE_BACKUP_DIR=str(self.tmp / "plain"))
		self.assertEqual(plain.returncode, 0, plain.stderr)
		self.assertNotIn("note:", plain.stdout)

	def test_a_folder_under_sites_without_a_site_config_is_not_a_site(self):
		"""frappe 16.60 keeps `sites/flow-server/` beside the real sites, and `sites/assets/`
		was always there. Neither has a site_config.json, so bench does not back it up
		(frappe.utils.get_sites) and the script must not wait for its dump. A symlink to a
		site is not a second site either."""
		flow = self.bench / "sites" / "flow-server"
		flow.mkdir()
		(flow / "flow.sqlite").write_text("x")
		self.assertTrue((self.bench / "sites" / "assets").is_dir())
		(self.bench / "sites" / "alias.test").symlink_to(self.bench / "sites" / self.SITES[0])
		result = self.run_script("backup")
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertIn(f"pre-migrate backup of {' '.join(self.SITES)} into ", result.stdout)
		for not_a_site in ("flow-server", "flow_server", "assets", "alias"):
			self.assertNotIn(not_a_site, result.stdout + result.stderr, not_a_site)
		(run,) = self.runs()
		self.assertEqual(len(list((self.root / run).iterdir())), 2 * len(self.SITES))

	def test_a_bench_with_no_site_at_all_is_refused(self):
		for site in self.SITES:
			(self.bench / "sites" / site / "site_config.json").unlink()
		(self.bench / "sites" / "flow-server").mkdir()
		result = self.run_script("backup")
		self.assertEqual(result.returncode, 1)
		self.assertIn(f"PRE-MIGRATE BACKUP FAILED: no sites found under {self.bench}/sites", result.stderr)
		self.assertEqual(self.call_log(), [])
		self.assertFalse(self.root.exists())

	def test_a_failed_backup_leaves_no_unfinished_directory(self):
		for mode in ("fail", "empty"):
			self.assertEqual(self.run_script("backup", STUB_BACKUP=mode).returncode, 1, mode)
			self.assertEqual(self.partials(), [], mode)
			self.assertEqual(self.runs(), [], mode)

	def test_a_killed_run_leaves_no_backup_name_and_the_next_run_clears_it(self):
		"""Killed outright, the script can remove nothing. What it leaves is named as
		unfinished, so retention, the free-space rule and a person restoring never take it
		for a backup; the next backup removes it, under the lock."""
		result = self.run_script("backup", STUB_BACKUP="killed")
		self.assertEqual(result.returncode, -9)
		self.assertEqual(self.runs(), [])
		(left,) = self.partials()
		self.assertTrue((self.root / left / "20261119_000000-site1_test-database.sql.gz").is_file())
		again = self.run_script("backup")
		self.assertEqual(again.returncode, 0, again.stderr)
		self.assertIn(f"removing the unfinished backup {left} that an earlier run left", again.stdout)
		self.assertEqual(len(self.runs()), 1)
		self.assertEqual(self.partials(), [])

	def test_a_stopped_run_waits_for_the_dump_keeps_its_lock_and_leaves_nothing(self):
		"""A TERM to the script alone: bench is still dumping, so the script keeps the lock
		until bench has returned, then removes the unfinished directory and ends as stopped."""
		proc = self.start_script("backup", STUB_BACKUP_SLEEP="2")
		self.wait_for(lambda: "backup-begin" in self.call_log(), "the backup to start")
		proc.terminate()
		time.sleep(0.5)
		self.assertIsNone(proc.poll())
		self.assertFalse(self.lock_is_free())
		proc.communicate(timeout=60)
		self.assertEqual(proc.returncode, 143)
		self.assertIn("backup-end", self.call_log())
		self.assertEqual(self.runs(), [])
		self.assertEqual(self.partials(), [])
		self.assertTrue(self.lock_is_free())

	def test_an_old_backup_that_cannot_be_removed_is_a_warning_not_a_failure(self):
		self.root.mkdir()
		old = [f"20260101T00000{i}Z" for i in range(6)]
		for name in old:
			(self.root / name).mkdir()
		held = self.root / old[0] / "held"
		held.mkdir()
		(held / "file").write_text("x")
		held.chmod(0o500)
		self.addCleanup(held.chmod, 0o700)
		result = self.run_script("backup")
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertIn(
			f"warning: could not remove the older pre-migrate backup {self.root / old[0]}; remove it by hand",
			result.stderr,
		)
		# The one that could not be removed is still there, beside the newest that are kept.
		keep = _script_keep()
		runs = self.runs()
		self.assertEqual(runs[:-1], [old[0], *old[len(old) - (keep - 1) :]])
		self.assertEqual(len(runs), keep + 1)


class FreeSpaceTests(_ScratchBench):
	"""Twice the newest kept backup plus the margin, or the margin alone. The stand-in `df`
	reports an exact free figure, so each rule is checked one KB either side of the line."""

	def kept_backup(self, kb=1024):
		run = self.root / "20260101T000000Z"
		run.mkdir(parents=True)
		(run / "x-database.sql.gz").write_bytes(os.urandom(kb * 1024))
		du = subprocess.run(["du", "-sk", "--", str(run)], capture_output=True, text=True, check=True)
		return int(du.stdout.split()[0])

	def test_with_no_kept_backup_the_margin_alone_applies(self):
		short = self.run_script("backup", PRE_MIGRATE_MIN_FREE_MB="3", STUB_DF_AVAIL_KB=str(3 * 1024 - 1))
		self.assertEqual(short.returncode, 1)
		self.assertIn(
			"only 2 MB free on the disk that holds "
			f"{self.root}, and 3 MB is required: a margin of 3 MB, with no kept backup yet",
			short.stderr,
		)
		self.assertEqual(self.call_log(), [])
		self.assertEqual(self.runs(), [])
		enough = self.run_script("backup", PRE_MIGRATE_MIN_FREE_MB="3", STUB_DF_AVAIL_KB=str(3 * 1024))
		self.assertEqual(enough.returncode, 0, enough.stderr)
		self.assertEqual(len(self.runs()), 1)

	def test_with_a_kept_backup_twice_its_size_is_added_to_the_margin(self):
		kept_kb = self.kept_backup()
		need_kb = 2 * kept_kb + 3 * 1024
		short = self.run_script("backup", PRE_MIGRATE_MIN_FREE_MB="3", STUB_DF_AVAIL_KB=str(need_kb - 1))
		self.assertEqual(short.returncode, 1)
		self.assertIn(
			f"MB is required: twice the newest kept backup (20260101T000000Z, {kept_kb // 1024} MB) "
			"plus a margin of 3 MB",
			short.stderr,
		)
		self.assertEqual(self.call_log(), [])
		self.assertEqual(self.runs(), ["20260101T000000Z"])
		enough = self.run_script("backup", PRE_MIGRATE_MIN_FREE_MB="3", STUB_DF_AVAIL_KB=str(need_kb))
		self.assertEqual(enough.returncode, 0, enough.stderr)
		self.assertEqual(len(self.runs()), 2)

	def test_the_default_margin_is_5_gb(self):
		short = self.run_script("backup", PRE_MIGRATE_MIN_FREE_MB=None, STUB_DF_AVAIL_KB=str(5120 * 1024 - 1))
		self.assertEqual(short.returncode, 1)
		self.assertIn("and 5120 MB is required: a margin of 5120 MB", short.stderr)
		enough = self.run_script("backup", PRE_MIGRATE_MIN_FREE_MB=None, STUB_DF_AVAIL_KB=str(5120 * 1024))
		self.assertEqual(enough.returncode, 0, enough.stderr)


class LockTests(_ScratchBench):
	"""One backup at a time, through an flock on <backup dir>/.bench_update.lock."""

	def assertComplete(self, run):
		self.assertEqual(
			sorted(p.name for p in (self.root / run).iterdir()),
			sorted(
				f"20261119_000000-{s.replace('.', '_')}-{kind}"
				for s in self.SITES
				for kind in ("database.sql.gz", "site_config_backup.json")
			),
		)

	def test_two_overlapping_runs_take_two_complete_backups_one_after_the_other(self):
		first = self.start_script("backup", STUB_BACKUP_SLEEP="2")
		self.wait_for(lambda: "backup-begin" in self.call_log(), "the first backup to start")
		second = self.start_script("backup", STUB_BACKUP_SLEEP="2")
		out1, err1 = first.communicate(timeout=60)
		out2, err2 = second.communicate(timeout=60)
		self.assertEqual(first.returncode, 0, err1)
		self.assertEqual(second.returncode, 0, err2)
		self.assertIn("another pre-migrate backup is running; waiting up to 900s", out2)
		self.assertNotIn("waiting", out1)
		markers = [line for line in self.call_log() if line in ("backup-begin", "backup-end")]
		self.assertEqual(markers, ["backup-begin", "backup-end", "backup-begin", "backup-end"])
		runs = self.runs()
		self.assertEqual(len(runs), 2)
		for run in runs:
			self.assertComplete(run)
		self.assertTrue(self.lock_is_free())

	def test_a_backup_waits_for_a_held_lock_and_runs_once_it_is_released(self):
		handle = self.hold_lock()
		proc = self.start_script("backup", PRE_MIGRATE_LOCK_WAIT_S="30")
		time.sleep(1)
		self.assertIsNone(proc.poll())
		self.assertEqual(self.call_log(), [])
		self.assertEqual(self.runs(), [])
		handle.close()
		out, err = proc.communicate(timeout=60)
		self.assertEqual(proc.returncode, 0, err)
		self.assertIn("waiting up to 30s", out)
		(run,) = self.runs()
		self.assertComplete(run)

	def test_a_run_that_cannot_get_the_lock_in_time_fails_and_deletes_nothing(self):
		self.root.mkdir()
		old = [f"20260101T00000{i}Z" for i in range(7)]
		for name in old:
			(self.root / name).mkdir()
		self.hold_lock()
		for wait in ("1", "0"):
			result = self.run_script("backup", PRE_MIGRATE_LOCK_WAIT_S=wait)
			self.assertEqual(result.returncode, 1, wait)
			self.assertIn(
				f"PRE-MIGRATE BACKUP FAILED: another pre-migrate backup still held {self.lock} after {wait}s "
				"(PRE_MIGRATE_LOCK_WAIT_S); nothing was deleted",
				result.stderr,
			)
			self.assertIn("migrated nothing", result.stderr)
		self.assertEqual(self.runs(), old)
		self.assertEqual(self.call_log(), [])
		self.assertTrue(self.lock.is_file())

	def test_the_lock_wait_must_be_a_number(self):
		result = self.run_script("backup", PRE_MIGRATE_LOCK_WAIT_S="soon")
		self.assertEqual(result.returncode, 1)
		self.assertIn("PRE_MIGRATE_LOCK_WAIT_S must be a whole number of seconds", result.stderr)
		self.assertEqual(self.call_log(), [])

	def test_the_lock_is_released_after_a_failed_backup(self):
		"""Even when a process bench started outlives it: bench is not handed the lock."""
		pid_file = self.tmp / "child.pid"
		result = self.run_script("backup", STUB_BACKUP="fail-leave-child", STUB_CHILD_PID=str(pid_file))
		pid = int(pid_file.read_text())
		self.addCleanup(lambda: subprocess.run(["kill", str(pid)], capture_output=True))
		self.assertEqual(result.returncode, 1)
		self.assertIn("PRE-MIGRATE BACKUP FAILED: bench backup exited with an error", result.stderr)
		os.kill(pid, 0)  # the leftover process is still running
		self.assertTrue(self.lock_is_free())
		again = self.run_script("backup", PRE_MIGRATE_LOCK_WAIT_S="0")
		self.assertEqual(again.returncode, 0, again.stderr)
		self.assertEqual(len(self.runs()), 1)


class RedisCheckTests(_ScratchBench):
	"""`bench_update.sh redis`: both instances the bench is configured for must answer."""

	def test_both_answering_passes_and_changes_nothing(self):
		result = self.run_script("redis")
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertEqual(
			result.stdout.splitlines(),
			[
				f"bench_update.sh: redis_cache at {CACHE_URL} answers",
				f"bench_update.sh: redis_queue at {QUEUE_URL} answers",
			],
		)
		self.assertEqual(
			self.call_log(), [f"redis-ping {url} connect=5 read=5" for url in (CACHE_URL, QUEUE_URL)]
		)
		self.assertFalse(self.root.exists())

	def test_either_one_down_fails_and_both_are_reported(self):
		for down, key, url in (("13000", "redis_cache", CACHE_URL), ("11000", "redis_queue", QUEUE_URL)):
			self.calls.unlink(missing_ok=True)
			result = self.run_script("redis", STUB_REDIS_DOWN=down)
			self.assertEqual(result.returncode, 1, down)
			self.assertIn(
				f"bench_update.sh: {key} at {url} did not answer: ConnectionError: Error 111", result.stderr
			)
			self.assertIn(
				"bench_update.sh: Redis is not ready, and a migrate started now would stop partway; "
				"nothing was changed. Start Redis (sudo systemctl start frappe-bench starts both instances), "
				"then run the same command again",
				result.stderr,
			)
			self.assertEqual(result.stdout.count(" answers"), 1)
			self.assertEqual(self.pings(), [CACHE_URL, QUEUE_URL])

	def test_the_addresses_are_the_ones_frappe_uses(self):
		"""The environment first, then the bench's common config, then frappe's defaults."""
		self.write_config(redis_cache="redis://cache.test:1", redis_queue="redis://queue.test:2")
		self.assertEqual(self.run_script("redis").returncode, 0)
		self.assertEqual(self.pings(), ["redis://cache.test:1", "redis://queue.test:2"])
		self.calls.unlink()
		env = {
			"FRAPPE_REDIS_CACHE": "redis://env-cache.test:3",
			"FRAPPE_REDIS_QUEUE": "redis://env-queue.test:4",
		}
		self.assertEqual(self.run_script("redis", **env).returncode, 0)
		self.assertEqual(self.pings(), ["redis://env-cache.test:3", "redis://env-queue.test:4"])
		self.calls.unlink()
		self.write_config()
		self.assertEqual(self.run_script("redis").returncode, 0)
		self.assertEqual(self.pings(), ["redis://127.0.0.1:13311", "redis://127.0.0.1:11311"])

	def test_a_password_in_the_address_is_never_printed(self):
		secret = "not-a-real-password-0001"
		self.write_config(redis_cache=f"redis://user:{secret}@127.0.0.1:13000/2", redis_queue=QUEUE_URL)
		for env in ({}, {"STUB_REDIS_DOWN": "13000"}, {"STUB_REDIS_REFUSE": "13000"}):
			result = self.run_script("redis", **env)
			self.assertNotIn(secret, result.stdout + result.stderr, env)
			self.assertIn("redis_cache at redis://127.0.0.1:13000", result.stdout + result.stderr, env)
		# The stub's refusal message carried the whole address, as a client's might.
		down = self.run_script("redis", STUB_REDIS_DOWN="13000")
		self.assertIn(
			"did not answer: ConnectionError: Error 111 connecting to redis://user:***@127.0.0.1", down.stderr
		)

	def test_a_cache_that_refuses_the_sign_in_is_not_ready(self):
		result = self.run_script("redis", STUB_REDIS_REFUSE="13000")
		self.assertEqual(result.returncode, 1)
		self.assertIn(f"redis_cache at {CACHE_URL} refused the sign-in: AuthenticationError", result.stderr)
		self.assertIn("Redis is not ready", result.stderr)

	def test_a_queue_that_asks_for_a_sign_in_is_up(self):
		result = self.run_script("redis", STUB_REDIS_REFUSE="11000")
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertIn(
			f"redis_queue at {QUEUE_URL} answers (it asks for a sign-in, which this check does not send)",
			result.stdout,
		)

	def test_an_instance_behind_sentinel_is_said_to_be_unchecked(self):
		self.write_config(redis_cache=CACHE_URL, redis_cache_sentinel_enabled=1, redis_queue=QUEUE_URL)
		result = self.run_script("redis", STUB_REDIS_DOWN="13000")
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertIn("redis_cache is reached through Redis Sentinel; not checked here", result.stdout)
		self.assertEqual(self.pings(), [QUEUE_URL])

	def test_an_address_that_is_not_a_url_fails(self):
		self.write_config(redis_cache=13000, redis_queue="http://127.0.0.1:11000")
		result = self.run_script("redis")
		self.assertEqual(result.returncode, 1)
		self.assertIn(f"redis_cache in {self.common_config} is not a URL", result.stderr)
		self.assertIn("redis_queue at http://127.0.0.1:11000 did not answer: ValueError", result.stderr)
		self.assertEqual(self.pings(), [])

	def test_a_check_that_cannot_run_fails_without_telling_anyone_to_start_redis(self):
		self.common_config.write_text("not json")
		unreadable = self.run_script("redis")
		self.common_config.unlink()
		missing = self.run_script("redis")
		for result, reason in ((unreadable, "JSONDecodeError"), (missing, "FileNotFoundError")):
			self.assertEqual(result.returncode, 1, reason)
			self.assertIn(f"could not check Redis: {reason}", result.stderr)
			self.assertIn(
				"Redis could not be checked (the reason is above); nothing was changed", result.stderr
			)
			self.assertNotIn("Start Redis", result.stderr)
		self.write_config(redis_cache=CACHE_URL, redis_queue=QUEUE_URL)
		self.python.unlink()
		result = self.run_script("redis")
		self.assertEqual(result.returncode, 1)
		self.assertIn(
			"cannot check Redis: the bench has no env/bin/python; nothing was changed", result.stderr
		)
		self.assertEqual(self.call_log(), [])

	def test_it_needs_no_flock(self):
		tools = self.tmp / "tools"
		tools.mkdir()
		for tool in ("bash", "basename", "dirname", "id", "env"):
			(tools / tool).symlink_to(shutil.which(tool))
		result = self.run_script("redis", PATH=f"{self.stubs}{os.pathsep}{tools}")
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertEqual(self.pings(), [CACHE_URL, QUEUE_URL])


class _FakeRedis(threading.Thread):
	"""A socket server that speaks enough of the Redis protocol for a client to connect and
	ping: +PONG to PING, the server's description to HELLO (newer clients open with it, to
	choose the protocol version) and +OK to anything else, or one fixed error to every command."""

	def __init__(self, error=None):
		super().__init__(daemon=True)
		self.error = error
		self.listener = socket.socket()
		self.listener.bind(("127.0.0.1", 0))
		self.listener.listen()
		self.port = self.listener.getsockname()[1]
		self.commands = []

	def run(self):
		while True:
			try:
				conn, _ = self.listener.accept()
			except OSError:
				return
			threading.Thread(target=self.serve, args=(conn,), daemon=True).start()

	@staticmethod
	def take_command(buffer):
		"""(words, rest) for one whole command at the head of the buffer, else (None, buffer)."""
		head, sep, rest = buffer.partition(b"\r\n")
		if not sep:
			return None, buffer
		words = []
		for _ in range(int(head[1:])):
			size, sep, rest = rest.partition(b"\r\n")
			if not sep or len(rest) < int(size[1:]) + 2:
				return None, buffer
			words.append(rest[: int(size[1:])])
			rest = rest[int(size[1:]) + 2 :]
		return words, rest

	@staticmethod
	def hello(words):
		"""The reply to HELLO [version]: seven fields, as a map in protocol 3 and a flat list in 2."""
		version = words[1] if len(words) > 1 else b"2"
		head = b"%7" if version == b"3" else b"*14"
		fields = (
			b"$6\r\nserver\r\n$5\r\nredis\r\n",
			b"$7\r\nversion\r\n$5\r\n7.0.0\r\n",
			b"$5\r\nproto\r\n:" + version + b"\r\n",
			b"$2\r\nid\r\n:1\r\n",
			b"$4\r\nmode\r\n$10\r\nstandalone\r\n",
			b"$4\r\nrole\r\n$6\r\nmaster\r\n",
			b"$7\r\nmodules\r\n*0\r\n",
		)
		return head + b"\r\n" + b"".join(fields)

	def serve(self, conn):
		buffer = b""
		with conn:
			while True:
				try:
					data = conn.recv(4096)
				except OSError:
					return
				if not data:
					return
				buffer += data
				while buffer:
					words, buffer = self.take_command(buffer)
					if words is None:
						break
					command = words[0].upper()
					self.commands.append(command)
					if self.error:
						conn.sendall(b"-" + self.error + b"\r\n")
					elif command == b"HELLO":
						conn.sendall(self.hello(words))
					else:
						conn.sendall(b"+PONG\r\n" if command == b"PING" else b"+OK\r\n")

	def stop(self):
		self.listener.close()


class RedisCheckRealClientTests(_ScratchBench):
	"""The same check with the real Redis client, against sockets: what the stub cannot show is
	which error the client raises for an instance that is down, and for one that wants a
	sign-in."""

	#: What STUB_REDIS_REAL is set to: "1" for the interpreter's own client.
	REAL = "1"

	def setUp(self):
		super().setUp()
		_tool_or_skip(self, importlib.util.find_spec("redis"), "the redis Python client")

	def server(self, error=None):
		fake = _FakeRedis(error)
		fake.start()
		self.addCleanup(fake.stop)
		return fake

	def closed_port(self):
		with socket.socket() as probe:
			probe.bind(("127.0.0.1", 0))
			return probe.getsockname()[1]

	def run_real(self, cache_port, queue_port):
		self.write_config(
			redis_cache=f"redis://127.0.0.1:{cache_port}", redis_queue=f"redis://127.0.0.1:{queue_port}/1"
		)
		return self.run_script("redis", STUB_REDIS_REAL=self.REAL)

	def test_two_instances_that_answer(self):
		cache, queue = self.server(), self.server()
		result = self.run_real(cache.port, queue.port)
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertEqual(result.stdout.count(" answers\n"), 2)
		self.assertIn(b"PING", cache.commands)
		self.assertIn(b"PING", queue.commands)
		self.assertEqual(self.call_log(), [])

	def test_one_that_is_down(self):
		cache = self.server()
		for ports, key in (
			((self.closed_port(), cache.port), "redis_cache"),
			((cache.port, self.closed_port()), "redis_queue"),
		):
			result = self.run_real(*ports)
			self.assertEqual(result.returncode, 1, key)
			self.assertRegex(
				result.stderr, rf"{key} at redis://127\.0\.0\.1:\d+ did not answer: ConnectionError"
			)
			self.assertIn("Redis is not ready", result.stderr)

	def test_one_that_wants_a_sign_in(self):
		plain, locked = self.server(), self.server(b"NOAUTH Authentication required.")
		cache_locked = self.run_real(locked.port, plain.port)
		self.assertEqual(cache_locked.returncode, 1)
		self.assertRegex(cache_locked.stderr, r"redis_cache at \S+ refused the sign-in: AuthenticationError")
		queue_locked = self.run_real(plain.port, locked.port)
		self.assertEqual(queue_locked.returncode, 0, queue_locked.stderr)
		self.assertIn("answers (it asks for a sign-in, which this check does not send)", queue_locked.stdout)


class RedisCheckFrappePinTests(RedisCheckRealClientTests):
	"""The same again with the client frappe pins, which is the one the check runs with on a
	bench. CI installs it into a directory of its own (REDIS_CLIENT_AT_FRAPPE_PIN), because the
	job's other steps need the version they pin."""

	REAL = os.environ.get("REDIS_CLIENT_AT_FRAPPE_PIN", "")

	def setUp(self):
		_ScratchBench.setUp(self)
		_tool_or_skip(
			self,
			self.REAL.startswith("/") and (Path(self.REAL) / "redis").is_dir(),
			"the redis client at frappe's pin, in the directory REDIS_CLIENT_AT_FRAPPE_PIN names",
		)

	def test_it_is_the_series_frappe_pins(self):
		version = subprocess.run(
			[str(self.python), "-c", "import redis; print(redis.__version__)"],
			env=self.script_env(STUB_REDIS_REAL=self.REAL),
			capture_output=True,
			text=True,
			check=True,
		).stdout.strip()
		self.assertTrue(version.startswith(FRAPPE_REDIS_SERIES), version)


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
			(src / app).mkdir()
			(src / app / "__init__.py").write_text('__version__ = "16.1.0"\n')
			self.git(src, "add", "version.txt", app)
			self.git(src, "commit", "-q", "-m", "16.1.0")
			self.git(src, *(["tag", "-a", "-m", "v16.1.0"] if annotated else ["tag"]), "v16.1.0")
			self.git(self.bench / "apps", "clone", "-q", "-o", "upstream", str(src), app)
			(src / "version.txt").write_text("16.2.0\n")
			(src / app / "__init__.py").write_text('__version__ = "16.2.0"\n')
			self.git(src, "commit", "-q", "-am", "16.2.0")
			self.git(src, *(["tag", "-a", "-m", "v16.2.0"] if annotated else ["tag"]), "v16.2.0")
			# The branch head: past the release, and still declaring its version.
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
		checked = [f"redis-ping {url} connect=5 read=5" for url in (CACHE_URL, QUEUE_URL)]
		self.assertEqual(
			self.call_log(),
			[
				*checked,
				f"--site all backup --backup-path {self.root / (PARTIAL_PREFIX + run)} --ignore-backup-conf",
				f"frappe-head-at-backup {self.old['frappe']}",
				"setup requirements",
				*checked,
				"--site all migrate",
				"build",
			],
		)
		self.assertIn("restart the bench now: sudo systemctl restart frappe-bench", result.stdout)

	def test_the_lock_covers_the_backup_only(self):
		result = self.run_script("upstream", "v16.2.0", "v16.2.0", STUB_LOCK=str(self.lock))
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertEqual(
			self.bench_calls()[2:],
			["setup requirements", "lock-free", "--site all migrate", "lock-free", "build", "lock-free"],
		)

	def test_redis_down_stops_it_before_the_backup(self):
		for down in ("13000", "11000"):
			self.calls.unlink(missing_ok=True)
			result = self.run_script("upstream", "v16.2.0", "v16.2.0", STUB_REDIS_DOWN=down)
			self.assertEqual(result.returncode, 1, down)
			self.assertIn(
				"Redis is not ready, and a migrate started now would stop partway; nothing was changed. "
				"Start Redis",
				result.stderr,
			)
			self.assertNotIn("UPSTREAM UPDATE FAILED", result.stderr)
			# Both were asked, and nothing else ran: no backup, no checkout, no bench command.
			self.assertEqual(self.pings(), [CACHE_URL, QUEUE_URL])
			self.assertEqual(self.bench_calls(), [])
			self.assertEqual(self.runs(), [])
			for app in ("frappe", "erpnext"):
				self.assertEqual(self.head(app), self.old[app])

	def test_redis_going_down_after_the_switch_stops_it_before_the_migrate(self):
		result = self.run_script("upstream", "v16.2.0", "v16.2.0", STUB_REDIS_DOWN_FROM="3")
		self.assertEqual(result.returncode, 1)
		(run,) = self.runs()
		self.assertEqual(
			self.bench_calls(),
			[
				f"--site all backup --backup-path {self.root / (PARTIAL_PREFIX + run)} --ignore-backup-conf",
				f"frappe-head-at-backup {self.old['frappe']}",
				"setup requirements",
			],
		)
		self.assertEqual(self.pings(), [CACHE_URL, QUEUE_URL, CACHE_URL, QUEUE_URL])
		self.assertIn("the code was switched but nothing was migrated. Start Redis", result.stderr)
		self.assertIn("then run the same command again", result.stderr)
		self.assertIn("UPSTREAM UPDATE FAILED", result.stderr)
		self.assertIn(str(self.root / run), result.stderr)
		self.assertIn(f"frappe was at commit {self.old['frappe']}", result.stderr)
		for app in ("frappe", "erpnext"):
			self.assertEqual(self.head(app), self.new[app])
		# The same command again, with Redis back, finishes the update (a second later: a run's
		# directory is named to the second).
		self.calls.unlink()
		time.sleep(1.1)
		again = self.run_script("upstream", "v16.2.0", "v16.2.0")
		self.assertEqual(again.returncode, 0, again.stderr)
		self.assertEqual(self.bench_calls()[-2:], ["--site all migrate", "build"])

	def test_what_an_update_writes_can_be_read_by_other_users(self):
		"""nginx serves the built assets and each app's public files from disk as another
		user. Whatever mask the caller has, the checkout, the installs, the migrate and the
		build write 0644 files in 0755 directories; the backup alone is private."""
		result = self.run_script("upstream", "v16.2.0", "v16.2.0", umask="077", STUB_WRITES="1")
		self.assertEqual(result.returncode, 0, result.stderr)
		assets = self.bench / "sites" / "assets"
		for made in ("made-by-setup-requirements", "made-by---site-all-migrate", "made-by-build"):
			self.assertEqual(self.mode(assets / made), 0o755, made)
			self.assertEqual(self.mode(assets / made / "file"), 0o644, made)
		for app in ("frappe", "erpnext"):
			# Both files differ between the two releases, so the checkout wrote them anew.
			self.assertEqual(self.mode(self.bench / "apps" / app / "version.txt"), 0o644, app)
			self.assertEqual(self.mode(self.bench / "apps" / app / app / "__init__.py"), 0o644, app)
		(run,) = self.runs()
		self.assertEqual(self.mode(self.root), 0o700)
		self.assertEqual(self.mode(self.root / run), 0o700)
		for path in (self.root / run).iterdir():
			self.assertEqual(self.mode(path), 0o600, path.name)

	def test_one_run_at_a_time_from_the_first_check_to_the_end(self):
		result = self.run_script("upstream", "v16.2.0", "v16.2.0", STUB_RUN_LOCK=str(self.run_lock))
		self.assertEqual(result.returncode, 0, result.stderr)
		calls = self.bench_calls()
		self.assertEqual(
			[
				calls[i + 1]
				for i, call in enumerate(calls)
				if call in ("setup requirements", "--site all migrate", "build")
			],
			["run-lock-held"] * 3,
		)
		backup = next(i for i, call in enumerate(calls) if call.startswith("--site all backup "))
		self.assertEqual(calls[backup + 1], "run-lock-held")
		self.assertTrue(self.lock_is_free(self.run_lock))

	def test_it_waits_for_a_deploy_or_another_update_and_gives_up_with_nothing_changed(self):
		handle = self.hold_lock(self.run_lock)
		for wait in ("1", "0"):
			result = self.run_script("upstream", "v16.2.0", "v16.2.0", PRE_MIGRATE_LOCK_WAIT_S=wait)
			self.assertEqual(result.returncode, 1, wait)
			self.assertIn(
				f"a deploy or another update still held {self.run_lock} after {wait}s "
				"(PRE_MIGRATE_LOCK_WAIT_S); nothing was changed",
				result.stderr,
			)
		self.assertUntouched()
		# ...not even a tag was fetched.
		self.assertEqual(self.git(self.bench / "apps" / "frappe", "tag", "--list", "v16.2.0"), "")
		proc = self.start_script("upstream", "v16.2.0", "v16.2.0", PRE_MIGRATE_LOCK_WAIT_S="30")
		time.sleep(1)
		self.assertIsNone(proc.poll())
		handle.close()
		out, err = proc.communicate(timeout=60)
		self.assertEqual(proc.returncode, 0, err)
		self.assertIn("a deploy or another update is running; waiting up to 30s", out)
		self.assertEqual(self.head("frappe"), self.new["frappe"])

	def test_a_process_the_update_leaves_running_keeps_neither_lock(self):
		"""Nothing the script starts is handed a lock: a process that a hook, or git's
		background gc, leaves behind would otherwise hold it after the script has ended."""
		pid_file = self.tmp / "child.pid"
		result = self.run_script(
			"upstream",
			"v16.2.0",
			"v16.2.0",
			STUB_LEAVE_CHILD="--site all migrate",
			STUB_CHILD_PID=str(pid_file),
		)
		pid = int(pid_file.read_text())
		self.addCleanup(lambda: subprocess.run(["kill", str(pid)], capture_output=True))
		self.assertEqual(result.returncode, 0, result.stderr)
		os.kill(pid, 0)  # the leftover process is still running
		self.assertTrue(self.lock_is_free(self.run_lock))
		self.assertTrue(self.lock_is_free())

	def test_it_refuses_a_tag_lower_than_what_is_installed(self):
		"""The script installs exactly what it is given, so an old command pasted again would
		take an app back. Either app being lower is enough, and nothing is touched."""
		self.assertEqual(self.run_script("upstream", "v16.2.0", "v16.2.0").returncode, 0)
		self.calls.unlink()
		for args, app in (
			(("v16.1.0", "v16.2.0"), "frappe"),
			(("v16.2.0", "v16.1.0"), "erpnext"),
			(("v16.1.0", "v16.1.0"), "frappe"),
		):
			result = self.run_script("upstream", *args)
			self.assertEqual(result.returncode, 1, args)
			self.assertIn(
				f"v16.1.0 is lower than the {app} that is installed (16.2.0); nothing was changed",
				result.stderr,
			)
			self.assertIn("set ALLOW_DOWNGRADE=1 to go back on purpose", result.stderr)
			self.assertNotIn("UPSTREAM UPDATE FAILED", result.stderr)
		self.assertEqual(self.call_log(), [])
		self.assertEqual(len(self.runs()), 1)
		for app in ("frappe", "erpnext"):
			self.assertEqual(self.head(app), self.new[app])

	def test_a_person_can_go_back_on_purpose(self):
		self.assertEqual(self.run_script("upstream", "v16.2.0", "v16.2.0").returncode, 0)
		time.sleep(1.1)  # a run's directory is named to the second
		result = self.run_script("upstream", "v16.1.0", "v16.1.0", ALLOW_DOWNGRADE="1")
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertIn("ALLOW_DOWNGRADE=1: frappe is not checked for a downgrade", result.stdout)
		for app in ("frappe", "erpnext"):
			self.assertEqual(self.head(app), self.old[app])
		self.assertEqual(len(self.runs()), 2)

	def test_versions_are_compared_as_versions_not_as_text(self):
		"""16.10.0 is higher than 16.9.0, though it sorts lower as text."""
		for app in ("frappe", "erpnext"):
			src = self.tmp / "remotes" / app
			for version in ("16.9.0", "16.10.0"):
				(src / app / "__init__.py").write_text(f'__version__ = "{version}"\n')
				self.git(src, "commit", "-q", "-am", version)
				self.git(src, "tag", f"v{version}")
		self.assertEqual(self.run_script("upstream", "v16.9.0", "v16.9.0").returncode, 0)
		time.sleep(1.1)
		up = self.run_script("upstream", "v16.10.0", "v16.10.0")
		self.assertEqual(up.returncode, 0, up.stderr)
		down = self.run_script("upstream", "v16.9.0", "v16.9.0")
		self.assertEqual(down.returncode, 1)
		self.assertIn("v16.9.0 is lower than the frappe that is installed (16.10.0)", down.stderr)

	def test_a_version_it_cannot_read_is_not_waved_through(self):
		app_dir = self.bench / "apps" / "frappe"
		(app_dir / "frappe" / "__init__.py").write_text("# no version here\n")
		self.git(app_dir, "commit", "-q", "-am", "no version")
		self.old["frappe"] = self.head("frappe")
		result = self.run_script("upstream", "v16.2.0", "v16.2.0")
		self.assertEqual(result.returncode, 1)
		self.assertIn(
			"could not read the version of frappe that is installed (apps/frappe/frappe/__init__.py), "
			"so a downgrade cannot be ruled out; nothing was changed",
			result.stderr,
		)
		self.assertUntouched()
		allowed = self.run_script("upstream", "v16.2.0", "v16.2.0", ALLOW_DOWNGRADE="1")
		self.assertEqual(allowed.returncode, 0, allowed.stderr)
		self.assertEqual(self.head("frappe"), self.new["frappe"])

	def test_the_same_version_at_another_commit_is_said_not_refused(self):
		"""A bench on the branch head declares the last release's version while holding
		commits the tag does not. The version check cannot see that, so the run says it."""
		for app in ("frappe", "erpnext"):
			app_dir = self.bench / "apps" / app
			self.git(app_dir, "fetch", "-q", "upstream", "version-16")
			self.git(app_dir, "checkout", "-q", "--detach", "FETCH_HEAD")
		result = self.run_script("upstream", "v16.2.0", "v16.2.0")
		self.assertEqual(result.returncode, 0, result.stderr)
		for app in ("frappe", "erpnext"):
			self.assertRegex(
				result.stdout,
				rf"note: {app} declares 16\.2\.0, the same version as v16\.2\.0, but is at another commit "
				r"\([0-9a-f]+\); the checkout moves it to the tag",
			)
			self.assertEqual(self.head(app), self.new[app])
		# The same command again is the same commit: nothing to say.
		time.sleep(1.1)
		again = self.run_script("upstream", "v16.2.0", "v16.2.0")
		self.assertEqual(again.returncode, 0, again.stderr)
		self.assertNotIn("note:", again.stdout)

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


#: `sudo` as the deploy's remote command uses it: `-u <user>` is dropped and, like the real one,
#: it hands the command none of the caller's descriptors above 2.
STUB_SUDO = r"""#!/usr/bin/env bash
if [ "${1:-}" = "-u" ]; then shift 2; fi
exec "$@" 9>&-
"""

#: A stand-in for a command of the chain (git on the app's checkout, redis-cli, systemctl): one
#: line in the call log, with which of the two locks is held at that moment.
STUB_CHAIN_TOOL = r"""#!/usr/bin/env bash
name=$(basename "$0")
if [ "$name" = git ] && [[ "$*" != *apps/erpnext_enhancements* ]]; then exit 1; fi
state() { if flock -n "$1" true 2>/dev/null; then echo free; else echo held; fi; }
printf '%s %s [bench lock %s, deploy user lock %s]\n' \
	"$name" "$*" "$(state "$STUB_RUN_LOCK")" "$(state "$STUB_OUTER_LOCK")" >> "$STUB_CALLS"
"""


class DeployChainRunTests(_ScratchBench):
	"""The deploy's remote command, run for real: the text the VM's shell receives, with the
	bench's path pointed at a scratch bench, the real script in it, and stand-ins for sudo,
	git, bench, redis-cli and systemctl. It is the only place the two locks are exercised as
	the build file writes them."""

	BOTH = "[bench lock held, deploy user lock held]"
	RESTART = "systemctl restart frappe-bench [bench lock free, deploy user lock held]"

	def setUp(self):
		super().setUp()
		for name, text in (
			("sudo", STUB_SUDO),
			("git", STUB_CHAIN_TOOL),
			("redis-cli", STUB_CHAIN_TOOL),
			("systemctl", STUB_CHAIN_TOOL),
		):
			stub = self.stubs / name
			stub.write_text(text)
			stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
		infra = self.bench / "apps" / "erpnext_enhancements" / "infra"
		infra.mkdir(parents=True)
		shutil.copy(SCRIPT, infra / "bench_update.sh")
		self.outer_lock = self.tmp / ".frappe-bench-deploy.lock"
		remote = _remote_string(self, _script_block(DEPLOY_YAML))
		self.assertEqual(remote.count("/home/frappe/frappe-bench "), 1)
		self.assertEqual(remote.count(" -w 900 "), 2)
		self.remote = remote.replace("/home/frappe/frappe-bench ", f"{self.bench} ")

	def chain_env(self, **env):
		return self.script_env(STUB_RUN_LOCK=str(self.run_lock), STUB_OUTER_LOCK=str(self.outer_lock), **env)

	def run_chain(self, wait="900", **env):
		return subprocess.run(
			["bash", "-c", self.remote.replace(" -w 900 ", f" -w {wait} ")],
			env=self.chain_env(**env),
			capture_output=True,
			text=True,
			timeout=120,
		)

	def test_a_whole_deploy_holds_both_locks_and_frees_them(self):
		result = self.run_chain()
		self.assertEqual(result.returncode, 0, result.stderr)
		(run,) = self.runs()
		app = "git -C apps/erpnext_enhancements"
		self.assertEqual(
			self.call_log(),
			[
				f"{app} fetch upstream main {self.BOTH}",
				f"{app} checkout FETCH_HEAD -- infra/bench_update.sh {self.BOTH}",
				f"redis-ping {CACHE_URL} connect=5 read=5",
				f"redis-ping {QUEUE_URL} connect=5 read=5",
				f"--site all backup --backup-path {self.root / (PARTIAL_PREFIX + run)} --ignore-backup-conf",
				"run-lock-held",
				"frappe-head-at-backup none",
				f"{app} reset --hard FETCH_HEAD {self.BOTH}",
				"--site all migrate",
				"run-lock-held",
				"build",
				"run-lock-held",
				f"redis-cli -p 13000 FLUSHDB {self.BOTH}",
				f"redis-cli -p 11000 FLUSHDB {self.BOTH}",
				# The restart: the frappe user's shell has ended, the deploy user's has not.
				self.RESTART,
			],
		)
		self.assertEqual(result.stdout.count("flock: getting lock took"), 2)
		self.assertTrue(self.lock_is_free(self.run_lock))
		self.assertTrue(self.lock_is_free(self.outer_lock))

	def test_a_deploy_waits_for_the_run_that_holds_either_lock_then_stops_with_nothing_run(self):
		for lock in (self.run_lock, self.outer_lock):
			handle = self.hold_lock(lock)
			result = self.run_chain(wait="1")
			handle.close()
			self.assertNotEqual(result.returncode, 0, lock.name)
			self.assertIn("flock: timeout while waiting to get lock", result.stderr, lock.name)
			self.assertEqual(self.call_log(), [], lock.name)
			self.assertEqual(self.runs(), [])

	def test_a_deploy_goes_ahead_once_the_other_run_ends(self):
		handle = self.hold_lock(self.run_lock)
		proc = subprocess.Popen(
			["bash", "-c", self.remote.replace(" -w 900 ", " -w 30 ")],
			env=self.chain_env(),
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
		)
		self.addCleanup(proc.kill)
		time.sleep(1)
		self.assertIsNone(proc.poll())
		self.assertEqual(self.call_log(), [])
		handle.close()
		_, err = proc.communicate(timeout=60)
		self.assertEqual(proc.returncode, 0, err)
		self.assertEqual(self.call_log()[-1], self.RESTART)

	def test_a_process_a_command_leaves_running_keeps_neither_lock(self):
		pid_file = self.tmp / "child.pid"
		result = self.run_chain(STUB_LEAVE_CHILD="--site all migrate", STUB_CHILD_PID=str(pid_file))
		pid = int(pid_file.read_text())
		self.addCleanup(lambda: subprocess.run(["kill", str(pid)], capture_output=True))
		self.assertEqual(result.returncode, 0, result.stderr)
		os.kill(pid, 0)  # the leftover process is still running
		self.assertTrue(self.lock_is_free(self.run_lock))
		self.assertTrue(self.lock_is_free(self.outer_lock))

	def test_a_failed_step_stops_the_chain_and_frees_both_locks(self):
		for env, last in (
			({"STUB_REDIS_DOWN": "13000"}, f"redis-ping {QUEUE_URL} connect=5 read=5"),
			({"STUB_BACKUP": "fail"}, "frappe-head-at-backup none"),
			({"STUB_FAIL": "--site all migrate"}, "run-lock-held"),
		):
			self.calls.unlink(missing_ok=True)
			result = self.run_chain(**env)
			self.assertNotEqual(result.returncode, 0, env)
			calls = self.call_log()
			self.assertEqual(calls[-1], last, env)
			later = ("systemctl", "redis-cli", "build")
			self.assertFalse([call for call in calls if call.startswith(later)], env)
			if "STUB_FAIL" not in env:
				self.assertFalse([call for call in calls if " reset --hard " in call], env)
			self.assertTrue(self.lock_is_free(self.run_lock))
			self.assertTrue(self.lock_is_free(self.outer_lock))


if __name__ == "__main__":
	unittest.main()
