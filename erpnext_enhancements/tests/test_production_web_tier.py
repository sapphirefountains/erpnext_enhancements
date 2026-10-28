"""Bench-free tests for the bench's production process units (v1.753.3).

Until this release prod answered web requests with `bench serve`, the Werkzeug development
server with its debugger on: frappe-bench.service ran `honcho start` on the development
Procfile. The replacement is plain files under infra/configs/ -- systemd units, a gunicorn
config and the script that installs them -- which run nowhere in CI and are exercised on a
VM only when somebody cuts it over. These tests are what checks them in between:

* the units: every process PartOf frappe-bench.service, nothing left running `bench serve`,
  and the one name the deploy restarts unchanged;
* gunicorn.conf.py: it reads the bench's keys with bench's own defaults;
* install_bench_units.sh, run for real against a scratch directory with a stub systemctl:
  it refuses while the old unit runs, backs it up, builds the umbrella's .wants from
  background_workers, and puts the old unit back on --rollback (Linux only);
* startup_script.sh: a reboot refreshes cut-over units and never cuts a VM over by itself.

Run: python -m unittest erpnext_enhancements.tests.test_production_web_tier -v
"""

import http.client
import json
import multiprocessing
import os
import re
import runpy
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS = REPO_ROOT / "infra" / "configs"
UNITS_DIR = CONFIGS / "systemd"
GUNICORN_CONF = CONFIGS / "gunicorn.conf.py"
INSTALLER = CONFIGS / "install_bench_units.sh"
STARTUP_SCRIPT = CONFIGS / "startup_script.sh"
DEPLOY_YAML = REPO_ROOT / "infra" / "cloudbuild-deploy.yaml"
UPSTREAM_YAML = REPO_ROOT / "infra" / "cloudbuild-upstream.yaml"
VARIABLES_TF = REPO_ROOT / "infra" / "variables.tf"
RUNBOOK = REPO_ROOT / "infra" / "docs" / "production-web-tier.md"

MARKER = "# erpnext_enhancements: production process units"
UMBRELLA = "frappe-bench.service"
TEMPLATES = {"frappe-bench-frappe-short-worker@.service", "frappe-bench-frappe-long-worker@.service"}


def _code(text, comment="#"):
	"""The text with comment lines dropped, so an absence check is not satisfied by prose."""
	return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(comment))


def _unit(path):
	"""A unit file as {section: {key: [values]}}."""
	sections, current = {}, None
	for line in _code(path.read_text(encoding="utf-8")).splitlines():
		line = line.strip()
		if not line:
			continue
		if line.startswith("["):
			current = sections.setdefault(line.strip("[]"), {})
			continue
		key, _, value = line.partition("=")
		current.setdefault(key, []).append(value)
	return sections


def _units():
	return {path.name: _unit(path) for path in sorted(UNITS_DIR.glob("*.service"))}


class UnitTests(unittest.TestCase):
	def setUp(self):
		self.units = _units()
		self.children = {name: unit for name, unit in self.units.items() if name != UMBRELLA}

	def test_the_expected_units_and_no_others(self):
		self.assertEqual(
			set(self.units),
			{
				UMBRELLA,
				"frappe-bench-redis-cache.service",
				"frappe-bench-redis-queue.service",
				"frappe-bench-frappe-web.service",
				"frappe-bench-node-socketio.service",
				"frappe-bench-frappe-schedule.service",
				*TEMPLATES,
			},
		)

	def test_the_installer_enables_every_child(self):
		# The installer's CHILDREN list plus the worker templates must be every unit but the
		# umbrella, or a unit gets installed and never started.
		listed = re.search(r'^CHILDREN="([^"]*)"', INSTALLER.read_text(encoding="utf-8"), re.M)
		self.assertIsNotNone(listed)
		self.assertEqual(set(listed.group(1).split()) | TEMPLATES, set(self.children))

	def test_every_unit_carries_the_marker_first(self):
		for name in self.units:
			first = (UNITS_DIR / name).read_text(encoding="utf-8").splitlines()[0]
			self.assertTrue(first.startswith(MARKER), name)

	def test_the_umbrella_runs_nothing_and_starts_at_boot(self):
		umbrella = self.units[UMBRELLA]
		self.assertEqual(umbrella["Service"]["Type"], ["oneshot"])
		self.assertEqual(umbrella["Service"]["RemainAfterExit"], ["yes"])
		self.assertEqual(umbrella["Service"]["ExecStart"], ["/bin/true"])
		self.assertEqual(umbrella["Install"]["WantedBy"], ["multi-user.target"])

	def test_every_child_is_part_of_the_umbrella(self):
		# PartOf is what makes `systemctl restart frappe-bench` -- the deploy's command --
		# restart each of them, and `stop` stop each of them.
		for name, unit in self.children.items():
			self.assertEqual(unit["Unit"].get("PartOf"), [UMBRELLA], name)
			self.assertEqual(unit["Install"].get("WantedBy"), [UMBRELLA], name)
			self.assertEqual(unit["Service"].get("User"), ["frappe"], name)
			self.assertEqual(unit["Service"].get("Group"), ["frappe"], name)
			self.assertEqual(unit["Service"].get("Restart"), ["always"], name)

	def test_one_path_for_every_child(self):
		paths = {name: unit["Service"].get("Environment") for name, unit in self.children.items()}
		self.assertEqual(len({tuple(p) for p in paths.values()}), 1, paths)

	def test_nothing_runs_the_development_server(self):
		for name in self.units:
			code = _code((UNITS_DIR / name).read_text(encoding="utf-8"))
			for banned in ("bench serve", "bench start", "honcho", "bench watch"):
				self.assertNotIn(banned, code, f"{name} runs {banned}")

	def test_web_is_gunicorn_on_frappe_app_with_the_installed_config(self):
		web = self.units["frappe-bench-frappe-web.service"]["Service"]
		(exec_start,) = web["ExecStart"]
		# sh only to compute --workers; exec hands the main PID to gunicorn, which
		# KillMode=mixed and the graceful stop depend on.
		self.assertTrue(
			exec_start.startswith("/bin/sh -c 'exec /home/frappe/frappe-bench/env/bin/gunicorn "), exec_start
		)
		self.assertIn("--config /home/frappe/frappe-bench/config/gunicorn.conf.py", exec_start)
		self.assertTrue(exec_start.endswith(" frappe.app:application'"), exec_start)
		self.assertEqual(web["WorkingDirectory"], ["/home/frappe/frappe-bench/sites"])

	def test_worker_count_is_on_the_command_line(self):
		# frappe.concurrency_limiter (16.53.0) reads `-w/--workers` from sys.argv to size every
		# @frappe.concurrent_limit() pool, download_pdf's included. Absent, it assumes ONE
		# worker and lets one call through at a time for the whole site.
		(exec_start,) = self.units["frappe-bench-frappe-web.service"]["Service"]["ExecStart"]
		self.assertIn(
			'--workers "$$(/home/frappe/frappe-bench/env/bin/python /home/frappe/frappe-bench/config/gunicorn.conf.py)"',
			exec_start,
		)
		self.assertNotIn("--threads", exec_start)

	def test_gunicorn_finishes_before_systemd_kills_it(self):
		# bench's supervisor template: graceful-timeout 30 below stopwaitsecs 40, or gunicorn
		# workers are orphaned mid-shutdown.
		web = self.units["frappe-bench-frappe-web.service"]["Service"]
		conf = runpy.run_path(str(GUNICORN_CONF))
		self.assertEqual(web["KillMode"], ["mixed"])
		self.assertLess(conf["graceful_timeout"], int(web["TimeoutStopSec"][0]))

	def test_workers_follow_bench_multi_queue_layout(self):
		short = self.units["frappe-bench-frappe-short-worker@.service"]["Service"]["ExecStart"][0]
		long_ = self.units["frappe-bench-frappe-long-worker@.service"]["Service"]["ExecStart"][0]
		self.assertIn("bench worker --queue short,default ", short)
		self.assertIn("bench worker --queue long,default,short ", long_)
		for exec_start in (short, long_):
			# The Procfile's own log files, written by the worker so they stay frappe's.
			self.assertIn(">>logs/worker.log 2>>logs/worker.error.log", exec_start)
			self.assertTrue(exec_start.startswith("/bin/sh -c 'exec bench worker"), exec_start)

	def test_processes_that_need_redis_start_after_it(self):
		# Every frappe process talks to both. socketio included: at 16.53.0 it subscribes
		# through get_redis_subscriber(), whose default is redis_queue.
		for name in (
			"frappe-bench-frappe-web.service",
			"frappe-bench-frappe-schedule.service",
			"frappe-bench-node-socketio.service",
			*TEMPLATES,
		):
			unit = self.units[name]["Unit"]
			for redis in ("frappe-bench-redis-cache.service", "frappe-bench-redis-queue.service"):
				self.assertIn(redis, " ".join(unit["After"]), name)
				self.assertIn(redis, " ".join(unit["Wants"]), name)


class GunicornConfTests(unittest.TestCase):
	def _conf(self, common_site_config):
		with tempfile.TemporaryDirectory() as sites:
			if common_site_config is not None:
				Path(sites, "common_site_config.json").write_text(json.dumps(common_site_config))
			old = os.environ.get("SITES_PATH")
			os.environ["SITES_PATH"] = sites
			try:
				return runpy.run_path(str(GUNICORN_CONF))
			finally:
				if old is None:
					del os.environ["SITES_PATH"]
				else:
					os.environ["SITES_PATH"] = old

	def test_bench_defaults_without_the_keys(self):
		for config in (None, {}):
			conf = self._conf(config)
			workers = multiprocessing.cpu_count() * 2 + 1
			self.assertEqual(conf["bind"], "127.0.0.1:8000")
			self.assertEqual(conf["workers"], workers)
			self.assertEqual(conf["timeout"], 120)
			self.assertEqual(conf["max_requests"], 5000 if workers > 1 else 0)
			self.assertEqual(conf["max_requests_jitter"], conf["max_requests"] // 10)
			self.assertTrue(conf["preload_app"])

	def test_reads_the_bench_keys(self):
		conf = self._conf(
			{
				"webserver_port": 8001,
				"gunicorn_workers": 17,
				"http_timeout": 300,
				"gunicorn_max_requests": 900,
			}
		)
		self.assertEqual(conf["bind"], "127.0.0.1:8001")
		self.assertEqual(conf["workers"], 17)
		self.assertEqual(conf["timeout"], 300)
		self.assertEqual(conf["max_requests"], 900)
		self.assertEqual(conf["max_requests_jitter"], 90)

	def test_a_single_worker_is_never_recycled(self):
		# bench's get_default_max_requests: one worker restarting at random is an outage.
		self.assertEqual(self._conf({"gunicorn_workers": 1})["max_requests"], 0)

	def test_loopback_only(self):
		self.assertTrue(self._conf({})["bind"].startswith("127.0.0.1:"))

	def test_request_line_matches_nginx_not_gunicorns_default(self):
		# gunicorn caps the setting at 8190; its default 4094 would refuse request lines that
		# nginx (8 KB buffers) and the development server (64 KB) both accept today.
		self.assertEqual(self._conf({})["limit_request_line"], 8190)

	def test_default_worker_class(self):
		# sync, as bench leaves it: the class under which a request past `timeout` is killed,
		# which gating_api.BATCH_TIME_BUDGET_SECONDS is written against.
		self.assertNotIn("worker_class", self._conf({}))
		self.assertNotIn("threads", self._conf({}))

	def test_run_as_a_script_it_prints_the_worker_count(self):
		# How the unit puts --workers on gunicorn's command line. Loaded by gunicorn (or
		# runpy) it prints nothing.
		with tempfile.TemporaryDirectory() as sites:
			Path(sites, "common_site_config.json").write_text(json.dumps({"gunicorn_workers": 9}))
			result = subprocess.run(
				[sys.executable, str(GUNICORN_CONF)], cwd=sites, capture_output=True, text=True, timeout=60
			)
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertEqual(result.stdout, "9\n")


@unittest.skipUnless(sys.platform.startswith("linux"), "runs the unit's /bin/sh command line")
class WebExecStartTests(unittest.TestCase):
	def test_the_command_line_gunicorn_receives(self):
		# The web unit's ExecStart, run the way systemd would (its `$$` is a literal `$`)
		# against a scratch bench whose gunicorn prints its arguments.
		(exec_start,) = _units()["frappe-bench-frappe-web.service"]["Service"]["ExecStart"]
		with tempfile.TemporaryDirectory() as tmp:
			bench = Path(tmp) / "frappe-bench"
			for d in ("sites", "config", "env/bin"):
				(bench / d).mkdir(parents=True)
			(bench / "sites" / "common_site_config.json").write_text(json.dumps({"gunicorn_workers": 9}))
			shutil.copy(GUNICORN_CONF, bench / "config" / "gunicorn.conf.py")
			(bench / "env" / "bin" / "python").symlink_to(sys.executable)
			gunicorn = bench / "env" / "bin" / "gunicorn"
			gunicorn.write_text('#!/bin/sh\necho "$@"\n')
			gunicorn.chmod(0o755)
			argv = shlex.split(exec_start.replace("$$", "$").replace("/home/frappe/frappe-bench", str(bench)))
			result = subprocess.run(argv, cwd=bench / "sites", capture_output=True, text=True, timeout=60)
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertEqual(
			result.stdout.split(),
			["--workers", "9", "--config", f"{bench}/config/gunicorn.conf.py", "frappe.app:application"],
		)


@unittest.skipUnless(
	sys.platform.startswith("linux") and shutil.which("bash"), "the installer is a Linux script"
)
class InstallerTests(unittest.TestCase):
	OLD_UNIT = "[Service]\nExecStart=/usr/local/bin/honcho start\n"

	def setUp(self):
		self.tmp = Path(tempfile.mkdtemp())
		self.addCleanup(shutil.rmtree, self.tmp)
		self.bench = self.tmp / "frappe-bench"
		self.systemd = self.tmp / "systemd"
		self.backup = self.tmp / "backups" / "frappe-bench.service.pre-gunicorn"
		self.bin = self.tmp / "bin"
		self.log = self.tmp / "systemctl.log"
		for d in ("sites", "config", "apps/frappe", "logs", "env/bin"):
			(self.bench / d).mkdir(parents=True)
		self.set_workers(2)
		for f in ("config/redis_cache.conf", "config/redis_queue.conf", "apps/frappe/socketio.js"):
			(self.bench / f).write_text("")
		self.systemd.mkdir()
		(self.systemd / UMBRELLA).write_text(self.OLD_UNIT)
		self.bin.mkdir()
		self._exe(self.bench / "env" / "bin" / "gunicorn", "exit 0")
		for name in ("bench", "redis-server"):
			self._exe(self.bin / name, "exit 0")
		self._exe(self.bin / "node", '[ "$1" = --version ] && echo "${STUB_NODE_VERSION:-v24.21.0}"\nexit 0')
		(self.bench / "apps" / "frappe" / "package.json").write_text(
			json.dumps({"engines": {"node": ">=24"}})
		)
		# The stub systemctl logs each call. is-active reports STUB_STATE (exit 0 only for
		# "active", printing nothing under --quiet); list-units prints STUB_RUNNING.
		self._exe(
			self.bin / "systemctl",
			f'echo "$*" >> {self.log}\n'
			'case "$1" in\n'
			'  is-active) s=${STUB_STATE:-inactive}; [ "$2" = --quiet ] || echo "$s"; [ "$s" = active ]; exit $? ;;\n'
			'  list-units) printf "%s" "${STUB_RUNNING:-}" ;;\n'
			"esac\n",
		)
		(self.bin / "python3").symlink_to(sys.executable)

	def _exe(self, path, body):
		path.write_text(f"#!/bin/sh\n{body}\n")
		path.chmod(0o755)

	def set_workers(self, n):
		(self.bench / "sites" / "common_site_config.json").write_text(json.dumps({"background_workers": n}))

	def run_installer(self, *args, state="inactive", running="", node="v24.21.0"):
		env = {
			# No /usr/sbin: keeps a runner's own nginx out of the check.
			"PATH": f"{self.bin}:/usr/bin:/bin",
			"BENCH_DIR": str(self.bench),
			"SYSTEMD_DIR": str(self.systemd),
			"BACKUP": str(self.backup),
			"SYSTEMCTL": str(self.bin / "systemctl"),
			"UNIT_PATH": str(self.bin),
			"REQUIRE_ROOT": "0",
			"STUB_STATE": state,
			"STUB_RUNNING": running,
			"STUB_NODE_VERSION": node,
		}
		return subprocess.run(
			["bash", str(INSTALLER), *args], env=env, capture_output=True, text=True, timeout=60
		)

	def wants(self):
		return sorted(p.name for p in (self.systemd / "frappe-bench.service.wants").iterdir())

	def test_check_reports_and_changes_nothing(self):
		result = self.run_installer("--check")
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertIn("the OLD unit (/usr/local/bin/honcho start)", result.stdout)
		self.assertIn("gunicorn: 127.0.0.1:8000", result.stdout)
		self.assertIn("RQ workers: 2 short + 2 long", result.stdout)
		self.assertEqual(sorted(p.name for p in self.systemd.iterdir()), [UMBRELLA])
		self.assertFalse(self.backup.exists())
		# It asks systemd one question and tells it nothing.
		self.assertEqual(set(self.log.read_text().splitlines()), {"is-active frappe-bench.service"})

	def test_check_fails_on_a_missing_binary(self):
		(self.bin / "node").unlink()
		result = self.run_installer("--check")
		self.assertNotEqual(result.returncode, 0)
		self.assertIn("MISSING  node", result.stdout)
		# The nginx lines still print: a blocker must not hide them.
		self.assertIn("nginx", result.stdout.split("MISSING  node", 1)[1])

	def test_install_refuses_unless_the_old_unit_is_plainly_stopped(self):
		# "activating" is Restart='s wait: honcho comes back seconds later.
		for state in ("active", "activating", "reloading", "deactivating"):
			result = self.run_installer(state=state)
			self.assertNotEqual(result.returncode, 0, state)
			self.assertIn("Stop it first", result.stderr)
			self.assertEqual((self.systemd / UMBRELLA).read_text(), self.OLD_UNIT)
			self.assertEqual(sorted(p.name for p in self.systemd.iterdir()), [UMBRELLA])
			self.assertFalse(self.backup.exists())

	def test_install_after_a_failed_old_unit(self):
		self.assertEqual(self.run_installer(state="failed").returncode, 0)
		self.assertIn(MARKER, (self.systemd / UMBRELLA).read_text())

	def test_an_env_file_blocks_and_only_its_names_are_shown(self):
		(self.bench / ".env").write_text("export FOO_TOKEN=s3cr3t-value\nBAR=1\n# comment\n")
		result = self.run_installer()
		self.assertNotEqual(result.returncode, 0)
		self.assertIn("BLOCKER", result.stdout)
		self.assertIn("FOO_TOKEN BAR", result.stdout)
		self.assertNotIn("s3cr3t-value", result.stdout + result.stderr)
		self.assertEqual((self.systemd / UMBRELLA).read_text(), self.OLD_UNIT)

	def test_procfile_environment_prefixes_block(self):
		(self.bench / "Procfile").write_text(
			"redis_cache: redis-server config/redis_cache.conf\n"
			"web: bench serve --port 8000\n"
			"worker: NO_PROXY=* LANG=C.UTF-8 bench worker 1>> logs/worker.log 2>> logs/worker.error.log\n"
		)
		result = self.run_installer("--check")
		self.assertNotEqual(result.returncode, 0)
		self.assertIn("the Procfile's worker line sets NO_PROXY LANG", result.stdout)
		self.assertNotIn("web line sets", result.stdout)

	def test_a_plain_procfile_passes(self):
		(self.bench / "Procfile").write_text(
			"web: bench serve --port 8000\nworker:  bench worker 1>> logs/worker.log 2>> logs/worker.error.log\n"
		)
		result = self.run_installer("--check")
		self.assertEqual(result.returncode, 0, result.stdout)

	def test_drop_ins_block(self):
		dropins = self.systemd / "frappe-bench.service.d"
		dropins.mkdir()
		(dropins / "override.conf").write_text("[Service]\nEnvironment=FOO=1\n")
		result = self.run_installer()
		self.assertNotEqual(result.returncode, 0)
		self.assertIn("override.conf", result.stdout)
		self.assertEqual((self.systemd / UMBRELLA).read_text(), self.OLD_UNIT)

	def test_old_node_blocks(self):
		result = self.run_installer("--check", node="v22.11.0")
		self.assertNotEqual(result.returncode, 0)
		self.assertIn("frappe needs node >= 24", result.stdout)

	def test_a_different_node_than_the_procfiles_is_flagged(self):
		(self.bench / "Procfile").write_text("socketio: /opt/node24/bin/node apps/frappe/socketio.js\n")
		result = self.run_installer("--check")
		self.assertEqual(result.returncode, 0, result.stdout)
		self.assertIn(
			f"WARNING  the Procfile runs socketio with /opt/node24/bin/node; the unit would run {self.bin}/node",
			result.stdout,
		)

	def test_install_refuses_with_a_binary_missing(self):
		(self.bin / "redis-server").unlink()
		result = self.run_installer()
		self.assertNotEqual(result.returncode, 0)
		self.assertEqual((self.systemd / UMBRELLA).read_text(), self.OLD_UNIT)

	def test_install_backs_up_and_installs(self):
		result = self.run_installer()
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertEqual(self.backup.read_text(), self.OLD_UNIT)
		for unit in UNITS_DIR.glob("*.service"):
			self.assertEqual((self.systemd / unit.name).read_text(), unit.read_text(), unit.name)
		self.assertEqual((self.bench / "config" / "gunicorn.conf.py").read_text(), GUNICORN_CONF.read_text())
		self.assertEqual(
			self.wants(),
			sorted(
				[
					"frappe-bench-frappe-schedule.service",
					"frappe-bench-frappe-web.service",
					"frappe-bench-node-socketio.service",
					"frappe-bench-redis-cache.service",
					"frappe-bench-redis-queue.service",
					"frappe-bench-frappe-short-worker@1.service",
					"frappe-bench-frappe-short-worker@2.service",
					"frappe-bench-frappe-long-worker@1.service",
					"frappe-bench-frappe-long-worker@2.service",
				]
			),
		)
		link = self.systemd / "frappe-bench.service.wants" / "frappe-bench-frappe-long-worker@2.service"
		self.assertEqual(os.readlink(link), str(self.systemd / "frappe-bench-frappe-long-worker@.service"))
		calls = self.log.read_text().splitlines()
		self.assertIn("daemon-reload", calls)
		self.assertIn("enable frappe-bench.service", calls)
		self.assertFalse(any(c.split()[0] in ("start", "stop", "restart") for c in calls), calls)
		self.assertIn("next:    sudo systemctl start frappe-bench", result.stdout)

	def test_refresh_rebuilds_wants_and_keeps_the_first_backup(self):
		self.assertEqual(self.run_installer().returncode, 0)
		self.set_workers(1)
		result = self.run_installer(state="active")  # the umbrella is ours now, so running is fine
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertEqual(self.backup.read_text(), self.OLD_UNIT)
		workers = [w for w in self.wants() if "worker@" in w]
		self.assertEqual(
			workers,
			["frappe-bench-frappe-long-worker@1.service", "frappe-bench-frappe-short-worker@1.service"],
		)
		# A restart would keep @2 running through PartOf, so the hint is stop, then start.
		self.assertIn("sudo systemctl stop frappe-bench && sudo systemctl start frappe-bench", result.stdout)
		self.assertIn("frappe-bench-frappe-short-worker@2.service", result.stdout)

	def test_refresh_with_the_same_workers_says_restart(self):
		self.assertEqual(self.run_installer().returncode, 0)
		result = self.run_installer(state="active")
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertIn("next:    sudo systemctl restart frappe-bench", result.stdout)

	def test_refresh_ignores_the_procfile_and_env(self):
		# After the cutover nothing reads them, so they must not block a boot-time refresh.
		self.assertEqual(self.run_installer().returncode, 0)
		(self.bench / ".env").write_text("FOO=1\n")
		self.assertEqual(self.run_installer(state="active").returncode, 0)

	def test_rollback_refuses_while_anything_runs(self):
		self.assertEqual(self.run_installer().returncode, 0)
		result = self.run_installer(
			"--rollback", running="frappe-bench-frappe-web.service loaded active running\n"
		)
		self.assertNotEqual(result.returncode, 0)
		self.assertIn("frappe-bench-frappe-web.service", result.stderr)
		self.assertIn(MARKER, (self.systemd / UMBRELLA).read_text())

	def test_rollback_puts_the_old_unit_back(self):
		self.assertEqual(self.run_installer().returncode, 0)
		result = self.run_installer("--rollback")
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertEqual((self.systemd / UMBRELLA).read_text(), self.OLD_UNIT)
		self.assertEqual(sorted(p.name for p in self.systemd.iterdir()), [UMBRELLA])
		self.assertIn("next:    sudo systemctl start frappe-bench", result.stdout)

	def test_rollback_without_a_backup(self):
		result = self.run_installer("--rollback")
		self.assertNotEqual(result.returncode, 0)
		self.assertIn("nothing to put back", result.stderr)

	@unittest.skipUnless(shutil.which("shellcheck"), "shellcheck is not installed")
	def test_shellcheck(self):
		for script in (INSTALLER, CONFIGS / "nginx_webserver_guard.sh"):
			result = subprocess.run(["shellcheck", str(script)], capture_output=True, text=True)
			self.assertEqual(result.returncode, 0, f"{script.name}: {result.stdout}")


def _nginx_binary():
	return shutil.which("nginx") or next((p for p in ("/usr/sbin/nginx",) if os.path.exists(p)), None)


def _free_port():
	with socket.socket() as s:
		s.bind(("127.0.0.1", 0))
		return s.getsockname()[1]


# bench's generated server block, cut down to what the guard touches (bench v5.31.1
# templates/nginx.conf). PORT, UPSTREAM and ROOT are filled in per test.
BENCH_BLOCK = """upstream frappe-bench-frappe {
	server 127.0.0.1:UPSTREAM fail_timeout=0;
}

server {
	listen 127.0.0.1:PORT;
	server_name
		erp.sapphirefountains.com
		;

	root ROOT;

	location /assets {
		try_files $uri =404;
	}

	location / {
		try_files /erp.sapphirefountains.com/public/$uri @webserver;
	}

	location @webserver {
		proxy_http_version 1.1;
		proxy_set_header X-Forwarded-For $remote_addr;
		proxy_set_header Host $host;
		proxy_read_timeout 120;
		proxy_redirect off;

		proxy_pass  http://frappe-bench-frappe;
	}
}
"""

# What the first version of Step 0 inserted (v1.753.3's runbook before review).
EARLIER_GUARD = (
	"        if ($arg___debugger__) { return 404; }\n"
	'        if ($host ~* "^(localhost|127\\.0\\.0\\.1)$|\\.localhost$") { return 444; }\n'
)


@unittest.skipUnless(sys.platform.startswith("linux") and _nginx_binary(), "needs Linux and an nginx binary")
class NginxGuardTests(unittest.TestCase):
	"""nginx_webserver_guard.sh run against a real nginx (the CI runner ships one).

	The frappe block is reached through a symlink, as bench links it, and a stray copy of it
	that nginx does not load sits beside the config; the guard must edit only the loaded file.
	nginx serves on a free loopback port with nothing behind the upstream, so a request the
	guard lets through comes back 502, a refused parameter 404, and a refused Host as a closed
	connection (444).
	"""

	def setUp(self):
		self.root = Path(tempfile.mkdtemp())
		self.addCleanup(shutil.rmtree, self.root)
		self.prefix = self.root / "nginx"
		(self.prefix / "conf.d").mkdir(parents=True)
		self.port = _free_port()
		self.original = (
			BENCH_BLOCK.replace("UPSTREAM", str(_free_port()))
			.replace("PORT", str(self.port))
			.replace("ROOT", str(self.root / "sites"))
		)
		self.real = self.root / "bench" / "config" / "nginx.conf"
		self.real.parent.mkdir(parents=True)
		self.real.write_text(self.original)
		self.link = self.prefix / "conf.d" / "frappe-bench.conf"
		self.link.symlink_to(self.real)
		self.stray = self.prefix / "frappe-bench.conf.old"  # not matched by the include
		self.stray.write_text(self.original)
		(self.prefix / "nginx.conf").write_text(
			f"pid {self.prefix}/nginx.pid;\nerror_log {self.prefix}/error.log;\nevents {{}}\n"
			f"http {{\n\taccess_log off;\n\tinclude {self.prefix}/conf.d/*.conf;\n}}\n"
		)
		self.state = self.root / "state"
		self.nginx = [
			_nginx_binary(),
			"-p",
			f"{self.prefix}/",
			"-c",
			f"{self.prefix}/nginx.conf",
			"-e",
			f"{self.prefix}/error.log",
		]

	def guard(self, *args, nginx=None):
		env = {
			"PATH": f"{Path(sys.executable).parent}:/usr/sbin:/usr/bin:/bin",
			"NGINX": nginx or " ".join(self.nginx),
			"NGINX_RELOAD": "true",
			"STATE_DIR": str(self.state),
			"REQUIRE_ROOT": "0",
		}
		return subprocess.run(
			["bash", str(CONFIGS / "nginx_webserver_guard.sh"), *args],
			env=env,
			capture_output=True,
			text=True,
			timeout=60,
		)

	def serve(self):
		subprocess.run(self.nginx, check=True, capture_output=True, timeout=30)
		self.addCleanup(subprocess.run, [*self.nginx, "-s", "stop"], capture_output=True, timeout=30)

	def status(self, host, path="/api/method/ping"):
		conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
		try:
			conn.request("GET", path, headers={"Host": host})
			return conn.getresponse().status
		except (http.client.RemoteDisconnected, ConnectionResetError):
			return None  # 444: nginx closed the connection without a response
		finally:
			conn.close()

	def test_refuses_local_hosts_and_the_parameter_and_passes_the_rest(self):
		result = self.guard()
		self.assertEqual(result.returncode, 0, result.stderr)
		self.serve()
		self.assertEqual(self.status("erp.sapphirefountains.com"), 502)
		self.assertEqual(self.status("erp.sapphirefountains.com", "/api/method/ping?__debugger__=yes"), 404)
		for host in (
			"localhost",
			"LOCALHOST",
			"x.localhost",
			"127.0.0.1",
			"127.0.0.1:8000",
			"0.0.0.0",
			"0.0.0.0:8000",
		):
			self.assertIsNone(self.status(host), host)
		# The load balancer's health check sends the VM's address as Host.
		self.assertEqual(self.status("10.150.0.2"), 502)

	def test_only_the_loaded_file_changes_and_the_link_stays_a_link(self):
		self.assertEqual(self.guard().returncode, 0)
		self.assertTrue(self.link.is_symlink())
		self.assertEqual(self.stray.read_text(), self.original)
		self.assertEqual(self.real.read_text().count("erpnext_enhancements: debugger guard"), 1)
		self.assertEqual((self.state / "frappe-nginx.pre-guard").read_text(), self.original)
		self.assertEqual(
			(self.state / "frappe-nginx.guard-file").read_text().strip(), str(self.real.resolve())
		)

	def test_a_second_run_changes_nothing(self):
		self.assertEqual(self.guard().returncode, 0)
		once = self.real.read_text()
		self.assertEqual(self.guard().returncode, 0)
		self.assertEqual(self.real.read_text(), once)
		self.assertEqual((self.state / "frappe-nginx.pre-guard").read_text(), self.original)

	def test_an_earlier_guard_is_replaced(self):
		self.real.write_text(
			self.original.replace("\tlocation @webserver {\n", "\tlocation @webserver {\n" + EARLIER_GUARD)
		)
		self.assertIn("an earlier version", self.guard("--check").stdout)
		self.assertEqual(self.guard().returncode, 0)
		text = self.real.read_text()
		self.assertEqual(text.count("arg___debugger__"), 1)
		self.assertEqual(text.count("return 444;"), 1)
		self.assertNotIn("127\\.0\\.0\\.1)$|", text)
		self.assertIn("guard:   current", self.guard("--check").stdout)

	def test_undo_restores_the_first_backup(self):
		self.assertEqual(self.guard().returncode, 0)
		self.assertEqual(self.guard().returncode, 0)
		result = self.guard("--undo")
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertEqual(self.real.read_text(), self.original)
		self.assertTrue(self.link.is_symlink())
		self.assertEqual(list(self.state.iterdir()), [])
		self.assertNotEqual(self.guard("--undo").returncode, 0)

	def test_check_changes_nothing(self):
		result = self.guard("--check")
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertIn(f"file:    {self.real.resolve()}", result.stdout)
		self.assertIn("guard:   none", result.stdout)
		self.assertEqual(self.real.read_text(), self.original)
		self.assertFalse(self.state.exists())

	def test_two_loaded_blocks_are_refused(self):
		(self.prefix / "conf.d" / "other.conf").write_text(
			f"server {{\n\tlisten 127.0.0.1:{_free_port()};\n\tserver_name other;\n"
			"\tlocation / {\n\t\ttry_files $uri @webserver;\n\t}\n"
			"\tlocation @webserver {\n\t\treturn 200;\n\t}\n}\n"
		)
		result = self.guard()
		self.assertNotEqual(result.returncode, 0)
		self.assertIn("more than one", result.stderr)
		self.assertEqual(self.real.read_text(), self.original)

	def test_a_result_that_fails_nginx_t_is_put_back(self):
		# An nginx that fails -t whenever the guard is in the file.
		wrapper = self.root / "nginx-wrapper"
		wrapper.write_text(
			"#!/bin/sh\n"
			f'if [ "$1" = -t ] && grep -q "debugger guard" {self.real}; then exit 1; fi\n'
			f'exec {" ".join(self.nginx)} "$@"\n'
		)
		wrapper.chmod(0o755)
		result = self.guard(nginx=str(wrapper))
		self.assertNotEqual(result.returncode, 0)
		self.assertIn("back as it was", result.stderr)
		self.assertEqual(self.real.read_text(), self.original)


class StartupScriptTests(unittest.TestCase):
	def setUp(self):
		self.script = STARTUP_SCRIPT.read_text(encoding="utf-8")
		self.code = _code(self.script)

	def test_runs_the_repo_installer_and_knows_the_marker(self):
		self.assertIn(
			"UNITS_INSTALLER=/home/frappe/frappe-bench/apps/erpnext_enhancements/infra/configs/install_bench_units.sh",
			self.script,
		)
		self.assertIn(f'UNITS_MARKER="{MARKER}"', self.script)
		self.assertIn(f'MARKER="{MARKER}"', INSTALLER.read_text(encoding="utf-8"))

	def test_no_development_server_and_no_supervisor(self):
		# A reboot used to (re)create the honcho unit and run `bench setup production`, which
		# installs supervisor and starts a second copy of every process when it gets that far.
		for banned in ("honcho start", "bench setup production", "SERVICEEOF", "bench serve"):
			self.assertNotIn(banned, self.code)

	def test_an_uncut_vm_is_left_on_its_unit(self):
		# The branch for an existing unit without the marker must not run the installer.
		branch = self.script[
			self.script.index("elif [ -f /etc/systemd/system/frappe-bench.service ]; then") :
		]
		branch = branch[: branch.index('elif [ -f "$UNITS_INSTALLER" ]')]
		self.assertNotIn("UNITS_INSTALLER", branch)

	def test_nginx_guard_asks_nginx_not_grep(self):
		# `grep -r` skips the symlinks the frappe block lives behind, so it reports no block
		# on a VM that has one, and a second block is generated: duplicate upstreams, and
		# nginx will not start on the next boot. nginx -T dumps what is actually loaded.
		self.assertIn("nginx -T 2>/dev/null | grep -qE '127\\.0\\.0\\.1:8000|@webserver'", self.code)
		self.assertNotRegex(self.code, r"grep -r\w* .*/etc/nginx")
		self.assertLess(self.code.index("nginx -T 2>/dev/null"), self.code.index("bench setup nginx --yes"))


@unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("bash"), "runs a shell block")
class StartupNginxBlockTests(unittest.TestCase):
	"""The startup script's nginx section, run for real against a scratch /etc/nginx.

	The section is cut out of startup_script.sh (the whole file is a Terraform template and
	mounts disks), its absolute paths moved under a scratch root, and nginx, sudo and
	systemctl replaced by stubs. The stub nginx dumps NGINX_DUMP for -T, and fails -t when
	NGINX_T_FAIL is set or, with NGINX_T_FAIL_IF_LINK, once the generated link exists.
	"""

	def setUp(self):
		self.root = Path(tempfile.mkdtemp())
		self.addCleanup(shutil.rmtree, self.root)
		self.bin = self.root / "bin"
		self.bin.mkdir()
		self.log = self.root / "calls.log"
		self.etc = self.root / "etc" / "nginx"
		for d in ("conf.d", "sites-enabled"):
			(self.etc / d).mkdir(parents=True)
		(self.etc / "sites-enabled" / "default").write_text("server { listen 80 default_server; }\n")
		self.bench = self.root / "home" / "frappe" / "frappe-bench"
		(self.bench / "config").mkdir(parents=True)
		self.link = self.etc / "conf.d" / "frappe-bench.conf"
		self.dump = self.root / "dump.conf"
		self.dump.write_text("server { listen 80; }\n")

		script = STARTUP_SCRIPT.read_text(encoding="utf-8")
		start = script.index("NGINX_FRAPPE_LINK=")
		end = script.index('echo "[$(date)] Startup script completed"')
		block = (
			script[start:end]
			.replace("/etc/nginx", str(self.etc))
			.replace("/home/frappe", str(self.root / "home" / "frappe"))
		)
		self.block = self.root / "block.sh"
		self.block.write_text(block)

		self._exe(
			"nginx",
			f'echo "nginx $*" >> {self.log}\n'
			'if [ "$1" = -T ]; then cat "$NGINX_DUMP"; exit 0; fi\n'
			'[ -n "${NGINX_T_FAIL:-}" ] && exit 1\n'
			f'[ -n "${{NGINX_T_FAIL_IF_LINK:-}}" ] && [ -L {self.link} ] && exit 1\n'
			"exit 0",
		)
		self._exe(
			"sudo",
			f'echo "sudo $*" >> {self.log}\necho "server {{ location @webserver {{ }} }}" > {self.bench}/config/nginx.conf',
		)
		self._exe("systemctl", f'echo "systemctl $*" >> {self.log}')

	def _exe(self, name, body):
		path = self.bin / name
		path.write_text(f"#!/bin/sh\n{body}\n")
		path.chmod(0o755)

	def run_block(self, **env):
		full = {"PATH": f"{self.bin}:/usr/bin:/bin", "NGINX_DUMP": str(self.dump), **env}
		result = subprocess.run(
			["bash", str(self.block)], env=full, capture_output=True, text=True, timeout=60
		)
		self.assertEqual(result.returncode, 0, result.stderr)
		return self.log.read_text() if self.log.exists() else ""

	def test_a_loaded_frappe_block_behind_a_symlink_is_left_alone(self):
		real = self.root / "sites-available-frappe-bench"
		real.write_text("upstream frappe-bench-frappe { server 127.0.0.1:8000 fail_timeout=0; }\n")
		(self.etc / "sites-enabled" / "frappe-bench").symlink_to(real)
		self.dump.write_text(real.read_text())  # what nginx -T reports: it follows the link
		calls = self.run_block()
		self.assertNotIn("sudo", calls)
		self.assertFalse(self.link.is_symlink())
		self.assertTrue((self.etc / "sites-enabled" / "default").exists())
		self.assertIn("systemctl reload nginx", calls)

	def test_no_frappe_block_generates_one(self):
		calls = self.run_block()
		self.assertIn("bench setup nginx --yes", calls)
		self.assertEqual(os.readlink(self.link), f"{self.bench}/config/nginx.conf")
		self.assertFalse((self.etc / "sites-enabled" / "default").exists())

	def test_a_generated_block_that_fails_is_unlinked(self):
		calls = self.run_block(NGINX_T_FAIL_IF_LINK="1")
		self.assertIn("bench setup nginx --yes", calls)
		self.assertFalse(self.link.is_symlink())
		self.assertTrue((self.etc / "sites-enabled" / "default").exists())
		# With the bad block gone the config passes again, so the reload still picks up
		# the realip file; what matters is that nginx never loads the failing block.
		self.assertIn("systemctl reload nginx", calls)

	def test_a_broken_nginx_is_not_touched(self):
		calls = self.run_block(NGINX_T_FAIL="1")
		self.assertNotIn("sudo", calls)
		self.assertFalse(self.link.is_symlink())
		self.assertNotIn("systemctl reload nginx", calls)


class DeployTests(unittest.TestCase):
	def test_the_deploy_restarts_the_umbrella_by_name(self):
		# Renaming frappe-bench.service would break every deploy: the yaml restarts it by
		# name, and the deploy user may sudo exactly that command and nothing else.
		for yaml in (DEPLOY_YAML, UPSTREAM_YAML):
			self.assertIn('sudo systemctl restart frappe-bench"', yaml.read_text(encoding="utf-8"), yaml.name)
		self.assertIn(
			'default     = "/usr/bin/systemctl restart frappe-bench"',
			VARIABLES_TF.read_text(encoding="utf-8"),
		)
		self.assertTrue((UNITS_DIR / UMBRELLA).is_file())

	def test_the_runbook_exists_and_covers_rollback(self):
		text = RUNBOOK.read_text(encoding="utf-8")
		self.assertIn("install_bench_units.sh --check", text)
		self.assertIn("install_bench_units.sh --rollback", text)
		self.assertIn("--tunnel-through-iap", text)
		# Step 0 runs the tested script, not a hand-typed edit.
		self.assertIn("nginx_webserver_guard.sh --check", text)
		self.assertIn("nginx_webserver_guard.sh --undo", text)
		self.assertNotIn("sed -i", text)
		self.assertTrue((CONFIGS / "nginx_webserver_guard.sh").is_file())


if __name__ == "__main__":
	unittest.main()
