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

import json
import multiprocessing
import os
import re
import runpy
import shlex
import shutil
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
		for name in (
			"frappe-bench-frappe-web.service",
			"frappe-bench-frappe-schedule.service",
			"frappe-bench-node-socketio.service",
			*TEMPLATES,
		):
			after = " ".join(self.units[name]["Unit"]["After"])
			self.assertIn("frappe-bench-redis-cache.service", after, name)


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
		for name in ("bench", "node", "redis-server"):
			self._exe(self.bin / name, "exit 0")
		# The stub systemctl logs each call. is-active answers from STUB_ACTIVE; list-units
		# prints STUB_RUNNING.
		self._exe(
			self.bin / "systemctl",
			f'echo "$*" >> {self.log}\n'
			'case "$1" in\n'
			'  is-active) [ "${STUB_ACTIVE:-0}" = 1 ] && { echo active; exit 0; }; echo inactive; exit 3 ;;\n'
			'  list-units) printf "%s" "${STUB_RUNNING:-}" ;;\n'
			"esac\n",
		)
		(self.bin / "python3").symlink_to(sys.executable)

	def _exe(self, path, body):
		path.write_text(f"#!/bin/sh\n{body}\n")
		path.chmod(0o755)

	def set_workers(self, n):
		(self.bench / "sites" / "common_site_config.json").write_text(json.dumps({"background_workers": n}))

	def run_installer(self, *args, active=False, running=""):
		env = {
			# No /usr/sbin: keeps a runner's own nginx out of the check.
			"PATH": f"{self.bin}:/usr/bin:/bin",
			"BENCH_DIR": str(self.bench),
			"SYSTEMD_DIR": str(self.systemd),
			"BACKUP": str(self.backup),
			"SYSTEMCTL": str(self.bin / "systemctl"),
			"UNIT_PATH": str(self.bin),
			"REQUIRE_ROOT": "0",
			"STUB_ACTIVE": "1" if active else "0",
			"STUB_RUNNING": running,
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

	def test_install_refuses_while_the_old_unit_runs(self):
		result = self.run_installer(active=True)
		self.assertNotEqual(result.returncode, 0)
		self.assertIn("Stop it first", result.stderr)
		self.assertEqual((self.systemd / UMBRELLA).read_text(), self.OLD_UNIT)
		self.assertEqual(sorted(p.name for p in self.systemd.iterdir()), [UMBRELLA])
		self.assertFalse(self.backup.exists())

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
		result = self.run_installer(active=True)  # the umbrella is ours now, so running is fine
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertEqual(self.backup.read_text(), self.OLD_UNIT)
		workers = [w for w in self.wants() if "worker@" in w]
		self.assertEqual(
			workers,
			["frappe-bench-frappe-long-worker@1.service", "frappe-bench-frappe-short-worker@1.service"],
		)
		self.assertIn("sudo systemctl restart frappe-bench", result.stdout)

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
		result = subprocess.run(["shellcheck", str(INSTALLER)], capture_output=True, text=True)
		self.assertEqual(result.returncode, 0, result.stdout)


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

	def test_nginx_is_generated_only_without_a_frappe_block(self):
		guard = self.script.index("if ! grep -rqsE '127\\.0\\.0\\.1:8000|@webserver'")
		self.assertLess(guard, self.script.index("bench setup nginx --yes"))


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


if __name__ == "__main__":
	unittest.main()
