"""gunicorn settings for the bench's web process (frappe-bench-frappe-web.service).

The repo copy is the source of truth. install_bench_units.sh copies it to
/home/frappe/frappe-bench/config/gunicorn.conf.py, and the unit points gunicorn there.

Every value comes from the bench's common_site_config.json with the default bench itself
uses, so this file and bench's production templates (bench v5.31.1:
config/templates/supervisor.conf, systemd/frappe-bench-frappe-web.service and
config/common_site_config.py) agree on what a key means:

=========================  =======================  ==========================================
key                        gunicorn setting         default when the key is absent
=========================  =======================  ==========================================
``webserver_port``         ``bind`` (127.0.0.1)     8000
``gunicorn_workers``       ``workers``              2 x CPUs + 1 (17 on prod's 8 vCPUs)
``http_timeout``           ``timeout``              120 s -- also nginx's proxy_read_timeout
``gunicorn_max_requests``  ``max_requests``         5000, or 0 with a single worker
=========================  =======================  ==========================================

So ``bench set-config -g gunicorn_workers 9`` and a restart change the worker count with no
edit here. The file is read when gunicorn starts, from the sites directory the unit runs in.

Two settings are deliberate departures from a bare bench:

* ``limit_request_line`` is 8190, gunicorn's maximum, instead of its default 4094. nginx in
  front accepts a request line up to its 8 KB ``large_client_header_buffers``, and the
  development server it replaces accepted 64 KB, so at 4094 a long GET (a list view with many
  filters, a whitelisted method called with JSON in its query string) that works today would
  start failing with 400 on the day of the cutover.
* The worker class is gunicorn's default ``sync``, as bench's templates leave it. A worker
  serves one request at a time, and a request that runs past ``timeout`` has its worker
  killed and its transaction rolled back. Under the development server nothing was killed:
  nginx gave up on the client at 120 s and the request ran on to the end. Frappe Cloud runs
  ``gthread`` when ``gunicorn_threads_per_worker`` is set; this bench never set it, and with
  threads a long request is NOT killed at the timeout.
"""

import json
import multiprocessing
import os


def _common_site_config():
	"""The bench's common_site_config.json, or {} when there is none.

	frappe resolves the sites directory as SITES_PATH or the working directory, and the unit
	starts gunicorn in it. A malformed file is left to raise: frappe could not serve a request
	from it either, and a gunicorn that will not start says so in the journal at once.
	"""
	sites = os.environ.get("SITES_PATH") or os.getcwd()
	try:
		with open(os.path.join(sites, "common_site_config.json"), encoding="utf-8") as f:
			return json.load(f)
	except OSError:
		return {}


_conf = _common_site_config()

bind = f"127.0.0.1:{int(_conf.get('webserver_port') or 8000)}"
workers = int(_conf.get("gunicorn_workers") or multiprocessing.cpu_count() * 2 + 1)
timeout = int(_conf.get("http_timeout") or 120)
graceful_timeout = 30
max_requests = int(_conf.get("gunicorn_max_requests", 5000 if workers > 1 else 0) or 0)
max_requests_jitter = int(max_requests * 0.1)
preload_app = True
limit_request_line = 8190
proc_name = "frappe-bench-web"

if __name__ == "__main__":
	# The unit runs this file as a script to put `--workers N` on gunicorn's command line,
	# where frappe.concurrency_limiter reads the web tier's size (see the unit's header).
	print(workers)
