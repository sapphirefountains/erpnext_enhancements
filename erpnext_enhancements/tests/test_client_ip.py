"""Bench-free tests for the client-IP derivation guard (utils/client_ip.py).

The classifier is standard library only, so most of this runs with no stub. The
two database-reading functions import frappe lazily; their tests put a minimal
stub in ``sys.modules`` for the duration of the test and take it out again, so
nothing leaks into another suite sharing the process.

Also pins the three things that have to agree for the fix to hold, and that live
in three different places:

* the nginx file that does the work (infra/configs/nginx-realip.conf),
* the startup script that installs it on every boot, and which Terraform renders
  through ``templatefile`` -- so a stray ``${...}`` breaks the whole VM config,
* the address list this module classifies against.

Run: python -m unittest erpnext_enhancements.tests.test_client_ip -v
"""

import ast
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.utils import client_ip

MODULE_PATH = REPO_ROOT / "erpnext_enhancements" / "utils" / "client_ip.py"
REALIP_CONF = REPO_ROOT / "infra" / "configs" / "nginx-realip.conf"
STARTUP_SCRIPT = REPO_ROOT / "infra" / "configs" / "startup_script.sh"
HOOKS = REPO_ROOT / "erpnext_enhancements" / "hooks.py"

#: The only template variables compute.tf passes to startup_script.sh.
TEMPLATE_VARS = {"packages", "deploy_user", "deploy_user_sudo_command"}


def _directives(text):
	"""nginx directives as (name, args) pairs, comments stripped."""
	out = []
	for line in text.splitlines():
		line = line.split("#", 1)[0].strip()
		if not line:
			continue
		assert line.endswith(";"), f"unterminated nginx directive: {line!r}"
		name, _, args = line[:-1].partition(" ")
		out.append((name, args.strip()))
	return out


class ClassifyTests(unittest.TestCase):
	def test_google_front_end_ranges(self):
		self.assertEqual(client_ip.classify("35.191.12.34"), "gclb_proxy")
		self.assertEqual(client_ip.classify("35.191.255.255"), "gclb_proxy")
		self.assertEqual(client_ip.classify("130.211.0.1"), "gclb_proxy")
		self.assertEqual(client_ip.classify("130.211.3.254"), "gclb_proxy")

	def test_just_outside_the_front_end_ranges_is_a_client(self):
		# 130.211.0.0/22 ends at .3.255 -- a /16 guess would misfile these.
		self.assertEqual(client_ip.classify("130.211.4.1"), "public")
		self.assertEqual(client_ip.classify("35.192.0.1"), "public")

	def test_forwarding_rule_addresses(self):
		for address in client_ip.LB_FRONTEND_ADDRESSES:
			self.assertEqual(client_ip.classify(address), "load_balancer", address)

	def test_ipv4_mapped_ipv6_is_unwrapped(self):
		self.assertEqual(client_ip.classify("::ffff:35.191.1.1"), "gclb_proxy")
		self.assertEqual(client_ip.classify("::ffff:136.68.113.208"), "load_balancer")

	def test_ordinary_clients(self):
		self.assertEqual(client_ip.classify("216.162.1.2"), "public")
		self.assertEqual(client_ip.classify(" 8.8.8.8 "), "public")
		self.assertEqual(client_ip.classify("2001:4860:4860::8888"), "public")

	def test_loopback_and_private(self):
		# 127.0.0.1 is what a bench-local caller (a job, the MCP sandbox) records.
		self.assertEqual(client_ip.classify("127.0.0.1"), "loopback")
		self.assertEqual(client_ip.classify("::1"), "loopback")
		self.assertEqual(client_ip.classify("10.150.0.2"), "private")
		self.assertEqual(client_ip.classify("192.168.1.10"), "private")

	def test_missing_and_invalid(self):
		self.assertEqual(client_ip.classify(None), "missing")
		self.assertEqual(client_ip.classify(""), "missing")
		self.assertEqual(client_ip.classify("   "), "missing")
		self.assertEqual(client_ip.classify(12345), "missing")
		self.assertEqual(client_ip.classify("not-an-ip"), "invalid")
		# A whole header where one address belongs is a bug of its own, not a client.
		self.assertEqual(client_ip.classify("203.0.113.9, 35.191.1.1"), "invalid")

	def test_every_result_is_a_declared_category(self):
		for value in ("35.191.0.1", "136.68.113.208", "1.1.1.1", "10.0.0.1", "127.0.0.1", "", "x"):
			self.assertIn(client_ip.classify(value), client_ip.CATEGORIES)


class SummaryAndVerdictTests(unittest.TestCase):
	def test_summary_counts(self):
		summary = client_ip.summarize(["35.191.1.1", "136.68.113.208", "216.162.1.2", "216.162.1.2", None])
		self.assertEqual(summary["total"], 5)
		self.assertEqual(summary["proxy"], 2)
		self.assertEqual(summary["counts"]["public"], 2)
		self.assertEqual(summary["counts"]["missing"], 1)

	def test_a_working_chain_is_silent(self):
		self.assertIsNone(client_ip.regression_message(client_ip.summarize(["216.162.1.2", "71.1.2.3"])))

	def test_no_logins_is_not_evidence(self):
		self.assertIsNone(client_ip.regression_message(client_ip.summarize([])))

	def test_one_proxy_address_is_enough(self):
		message = client_ip.regression_message(client_ip.summarize(["216.162.1.2"] * 9 + ["35.191.7.7"]))
		self.assertIsNotNone(message)
		self.assertIn("1 of 10 logins", message)
		# The operator needs the file, the command and the audit, not just the symptom.
		self.assertIn("00-realip.conf", message)
		self.assertIn("infra/configs/nginx-realip.conf", message)
		self.assertIn("audit_login_ips", message)

	def test_load_balancer_collapse_is_reported_too(self):
		# The failure where the front-end ranges are trusted but the forwarding-rule
		# address is not: nginx stops on the load balancer and records it for everybody.
		message = client_ip.regression_message(client_ip.summarize(["136.68.113.208"] * 4))
		self.assertIn("0 front-end, 4 forwarding-rule", message)


class InfraAgreementTests(unittest.TestCase):
	"""The nginx file does the work; this module only watches for it failing. If
	the two lists drift, the guard either cries wolf or misses the regression."""

	def setUp(self):
		self.directives = _directives(REALIP_CONF.read_text(encoding="utf-8"))

	def test_trusted_set_matches_the_module(self):
		trusted = {args for name, args in self.directives if name == "set_real_ip_from"}
		expected = set(client_ip.GCLB_PROXY_NETWORKS) | set(client_ip.LB_FRONTEND_ADDRESSES)
		self.assertEqual(trusted, expected)

	def test_reads_forwarded_for_recursively(self):
		# Without recursion nginx takes the LAST entry -- the load balancer's own address.
		self.assertIn(("real_ip_header", "X-Forwarded-For"), self.directives)
		self.assertIn(("real_ip_recursive", "on"), self.directives)

	def test_nothing_but_realip_directives(self):
		# It is installed at http{} level into every server block; anything else in it
		# changes behaviour far from where anyone would look.
		allowed = {"set_real_ip_from", "real_ip_header", "real_ip_recursive"}
		self.assertEqual({name for name, _ in self.directives} - allowed, set())


class StartupScriptTests(unittest.TestCase):
	def setUp(self):
		self.script = STARTUP_SCRIPT.read_text(encoding="utf-8")

	def test_installs_the_repo_file_to_conf_d(self):
		self.assertIn("apps/erpnext_enhancements/infra/configs/nginx-realip.conf", self.script)
		self.assertIn("/etc/nginx/conf.d/00-realip.conf", self.script)
		self.assertTrue(REALIP_CONF.is_file())

	def test_install_is_tested_and_rolled_back(self):
		block = self.script[self.script.index("REALIP_SRC=") :]
		self.assertIn("nginx -t", block)
		self.assertIn("REALIP_DST.bak", block)

	def test_installs_before_bench_regenerates_nginx(self):
		# `bench setup production` reloads nginx; installing first means one reload
		# picks up both, and a boot never serves a window without the file.
		# The command itself, not the comment above the install block that names it.
		self.assertLess(
			self.script.index("REALIP_DST="), self.script.index("bench setup production frappe --yes")
		)

	def test_only_known_template_variables(self):
		# compute.tf renders this through templatefile(). Any other ${...} -- the
		# natural way to write a bash variable -- fails the render and with it the
		# VM's whole startup configuration.
		used = set(re.findall(r"\$\{([^}]*)\}", self.script))
		self.assertEqual(used - TEMPLATE_VARS, set())
		self.assertNotIn("%{", self.script)


class _StubbedFrappe:
	"""Installs a minimal frappe in sys.modules for one test, then removes it."""

	def __init__(self, rows):
		self.rows = rows
		self.logged = []
		self.filters = None

	def __enter__(self):
		self._saved = {k: sys.modules.get(k) for k in ("frappe", "frappe.utils")}
		frappe = types.ModuleType("frappe")
		utils = types.ModuleType("frappe.utils")

		def get_all(doctype, filters=None, fields=None, order_by=None, limit_page_length=None):
			assert doctype == "Activity Log"
			self.filters = filters
			return [dict(r) for r in self.rows]

		frappe.get_all = get_all
		frappe.log_error = lambda message, title: self.logged.append((title, message))
		utils.now_datetime = lambda: "NOW"
		utils.add_days = lambda value, days: f"{value}{days:+d}d"
		frappe.utils = utils
		sys.modules["frappe"] = frappe
		sys.modules["frappe.utils"] = utils
		return self

	def __exit__(self, *exc):
		for name, module in self._saved.items():
			if module is None:
				sys.modules.pop(name, None)
			else:
				sys.modules[name] = module
		return False


class FrappeSideTests(unittest.TestCase):
	def test_daily_check_logs_a_regression(self):
		rows = [{"ip_address": "216.162.1.2"}, {"ip_address": "35.191.9.9"}]
		with _StubbedFrappe(rows) as stub:
			summary = client_ip.check_client_ip_derivation()
		self.assertEqual(summary["proxy"], 1)
		self.assertEqual(len(stub.logged), 1)
		self.assertEqual(stub.logged[0][0], client_ip.ALERT_TITLE)

	def test_daily_check_is_silent_when_healthy(self):
		with _StubbedFrappe([{"ip_address": "216.162.1.2"}]) as stub:
			client_ip.check_client_ip_derivation()
		self.assertEqual(stub.logged, [])

	def test_daily_check_reads_only_successful_logins_in_the_window(self):
		with _StubbedFrappe([]) as stub:
			client_ip.check_client_ip_derivation()
		self.assertEqual(stub.filters["operation"], "Login")
		self.assertEqual(stub.filters["status"], "Success")
		self.assertEqual(stub.filters["creation"], [">=", f"NOW-{client_ip.LOOKBACK_DAYS}d"])

	def test_audit_reports_before_and_after(self):
		rows = [
			{"creation": "2026-07-20 06:37:29", "ip_address": "35.191.1.1"},
			{"creation": "2026-08-02 18:28:37", "ip_address": "35.191.2.2"},
			{"creation": "2026-08-03 07:46:07", "ip_address": "216.162.1.2"},
			{"creation": "2026-09-01 09:00:00", "ip_address": "71.1.2.3"},
		]
		with _StubbedFrappe(rows):
			audit = client_ip.audit_login_ips()
		self.assertEqual(sorted(audit["by_month"]), ["2026-07", "2026-08", "2026-09"])
		self.assertEqual(audit["by_month"]["2026-08"]["proxy"], 1)
		self.assertEqual(audit["last_proxy_login"], "2026-08-02 18:28:37")
		self.assertEqual(audit["since_last_proxy_login"]["total"], 2)
		self.assertEqual(audit["since_last_proxy_login"]["proxy"], 0)


class WiringTests(unittest.TestCase):
	def test_no_module_level_frappe_import(self):
		# The classifier must stay importable with no bench; the frappe reads are lazy.
		tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
		for node in tree.body:
			if isinstance(node, ast.Import):
				self.assertNotIn("frappe", [a.name.split(".")[0] for a in node.names])
			if isinstance(node, ast.ImportFrom):
				self.assertNotEqual((node.module or "").split(".")[0], "frappe")

	def test_daily_check_is_scheduled(self):
		self.assertIn(
			'"erpnext_enhancements.utils.client_ip.check_client_ip_derivation"',
			HOOKS.read_text(encoding="utf-8"),
		)
		self.assertTrue(callable(client_ip.check_client_ip_derivation))


if __name__ == "__main__":
	unittest.main()
