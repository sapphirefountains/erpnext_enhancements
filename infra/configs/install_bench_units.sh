#!/bin/bash
# Install, check or roll back the bench's production process units: gunicorn instead of
# `bench serve`, plus socketio, the workers, the scheduler and both Redis instances, each a
# systemd unit PartOf frappe-bench.service. Runbook: infra/docs/production-web-tier.md.
#
#   sudo bash install_bench_units.sh --check      report what an install would do; change nothing
#   sudo bash install_bench_units.sh              install or refresh the units; start nothing
#   sudo bash install_bench_units.sh --rollback   put back the unit that was there before
#
# It never starts, stops or restarts the bench. Stopping the old unit before an install and
# starting the new ones after are steps a person takes (or startup_script.sh at boot), one at
# a time, so each can be watched. An install refuses while the OLD frappe-bench.service is
# running, because swapping a running unit's definition underneath it leaves systemd
# tracking processes its new definition does not describe.
#
# It never runs `bench`. Run as root, bench leaves root-owned files behind that break the
# web tier and the scheduler.
#
# The environment overrides (BENCH_DIR, SYSTEMD_DIR, BACKUP, SYSTEMCTL, UNIT_PATH,
# REQUIRE_ROOT) exist for tests/test_production_web_tier.py; on a VM, run it bare.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
UNITS_SRC="$HERE/systemd"
BENCH_DIR=${BENCH_DIR:-/home/frappe/frappe-bench}
SYSTEMD_DIR=${SYSTEMD_DIR:-/etc/systemd/system}
BACKUP=${BACKUP:-/var/backups/frappe-bench.service.pre-gunicorn}
SYSTEMCTL=${SYSTEMCTL:-systemctl}
REQUIRE_ROOT=${REQUIRE_ROOT:-1}
# The PATH the units run with, read from the web unit so the two cannot drift.
UNIT_PATH=${UNIT_PATH:-$(sed -n 's/^Environment=PATH=//p' "$UNITS_SRC/frappe-bench-frappe-web.service")}

MARKER="# erpnext_enhancements: production process units"
UMBRELLA="$SYSTEMD_DIR/frappe-bench.service"
WANTS="$SYSTEMD_DIR/frappe-bench.service.wants"
CHILDREN="frappe-bench-redis-cache.service frappe-bench-redis-queue.service frappe-bench-frappe-web.service frappe-bench-node-socketio.service frappe-bench-frappe-schedule.service"

say() { printf '%s\n' "$*"; }
die() {
	printf 'install_bench_units: %s\n' "$*" >&2
	exit 1
}

is_ours() { [ -f "$UMBRELLA" ] && grep -qF "$MARKER" "$UMBRELLA"; }

# What gunicorn.conf.py resolves to on this bench, and the worker count bench would use,
# as key=value lines. Asking the config file itself means --check shows exactly what
# gunicorn will run with.
settings() {
	SITES_PATH="$BENCH_DIR/sites" python3 - "$HERE/gunicorn.conf.py" "$BENCH_DIR/sites/common_site_config.json" <<'PY'
import json, runpy, sys

c = runpy.run_path(sys.argv[1])
try:
	with open(sys.argv[2], encoding="utf-8") as f:
		background_workers = json.load(f).get("background_workers")
except OSError:
	background_workers = None
print(f"bind={c['bind']}")
print(f"port={c['bind'].rsplit(':', 1)[1]}")
print(f"workers={c['workers']}")
print(f"timeout={c['timeout']}")
print(f"max_requests={c['max_requests']}")
print(f"background_workers={int(background_workers or 1)}")
PY
}

setting() { settings | sed -n "s/^$1=//p"; }

preflight() {
	local missing=0 f bin found
	[ -d "$BENCH_DIR" ] || die "no bench at $BENCH_DIR"
	for f in sites/common_site_config.json config/redis_cache.conf config/redis_queue.conf apps/frappe/socketio.js logs; do
		if [ -e "$BENCH_DIR/$f" ]; then say "ok       $BENCH_DIR/$f"; else
			say "MISSING  $BENCH_DIR/$f"
			missing=1
		fi
	done
	if [ -x "$BENCH_DIR/env/bin/gunicorn" ]; then say "ok       $BENCH_DIR/env/bin/gunicorn"; else
		say "MISSING  $BENCH_DIR/env/bin/gunicorn"
		missing=1
	fi
	for bin in bench node redis-server; do
		# Looked up on the units' PATH, not this shell's: that is where systemd will look.
		# The single quotes are deliberate; "$1" belongs to the inner sh.
		# shellcheck disable=SC2016
		if found=$(env PATH="$UNIT_PATH" /bin/sh -c 'command -v "$1"' sh "$bin"); then
			say "ok       $bin -> $found"
		else
			say "MISSING  $bin is not on the units' PATH ($UNIT_PATH)"
			missing=1
		fi
	done
	return "$missing"
}

nginx_check() {
	local port conf
	port=$(setting port)
	if ! command -v nginx >/dev/null 2>&1; then
		say "note     nginx is not installed here; skipped the nginx check"
		return 0
	fi
	conf=$(nginx -T 2>/dev/null || true)
	if grep -q "127.0.0.1:$port" <<<"$conf"; then say "ok       nginx proxies to 127.0.0.1:$port"; else
		say "WARNING  nginx -T has no upstream on 127.0.0.1:$port; gunicorn will listen there"
	fi
	if grep -q "location /assets" <<<"$conf"; then say "ok       nginx serves /assets itself"; else
		say "WARNING  nginx -T has no 'location /assets'. gunicorn does not serve /assets or /files;"
		say "         bench serve did. Without it the Desk loads with no CSS or JS."
	fi
}

report() {
	say "== bench: $BENCH_DIR"
	if is_ours; then
		say "== frappe-bench.service: the production umbrella (installed by this script)"
	elif [ -f "$UMBRELLA" ]; then
		say "== frappe-bench.service: the OLD unit ($(sed -n 's/^ExecStart=//p' "$UMBRELLA"))"
	else
		say "== frappe-bench.service: none installed"
	fi
	say "== active: $($SYSTEMCTL is-active frappe-bench.service 2>/dev/null || true)"
	say "== gunicorn: $(setting bind), $(setting workers) workers, timeout $(setting timeout) s, max_requests $(setting max_requests)"
	say "== RQ workers: $(setting background_workers) short + $(setting background_workers) long (background_workers)"
}

install_units() {
	local unit n i
	preflight || die "fix the MISSING items above first; nothing was changed"
	nginx_check
	if [ -f "$UMBRELLA" ] && ! is_ours; then
		if $SYSTEMCTL is-active --quiet frappe-bench.service; then
			die "the old frappe-bench.service is running. Stop it first (sudo systemctl stop frappe-bench), then run this again; nothing was changed"
		fi
		if [ -e "$BACKUP" ]; then
			say "kept     the existing backup $BACKUP"
		else
			install -D -m 0644 "$UMBRELLA" "$BACKUP"
			say "saved    the old unit to $BACKUP"
		fi
	fi
	for unit in "$UNITS_SRC"/*.service; do
		install -m 0644 "$unit" "$SYSTEMD_DIR/$(basename "$unit")"
	done
	install -m 0644 -o "$(stat -c %U "$BENCH_DIR")" -g "$(stat -c %G "$BENCH_DIR")" \
		"$HERE/gunicorn.conf.py" "$BENCH_DIR/config/gunicorn.conf.py"
	# The umbrella's .wants is exactly the set below, rebuilt every run, so a lower
	# background_workers drops the extra worker instances. These are the links
	# `systemctl enable` makes from each unit's WantedBy=frappe-bench.service.
	rm -rf "$WANTS"
	mkdir -p "$WANTS"
	for unit in $CHILDREN; do
		ln -s "$SYSTEMD_DIR/$unit" "$WANTS/$unit"
	done
	n=$(setting background_workers)
	for i in $(seq 1 "$n"); do
		ln -s "$SYSTEMD_DIR/frappe-bench-frappe-short-worker@.service" "$WANTS/frappe-bench-frappe-short-worker@$i.service"
		ln -s "$SYSTEMD_DIR/frappe-bench-frappe-long-worker@.service" "$WANTS/frappe-bench-frappe-long-worker@$i.service"
	done
	$SYSTEMCTL daemon-reload
	$SYSTEMCTL enable frappe-bench.service
	say "installed the production units; $n short + $n long RQ workers"
	if $SYSTEMCTL is-active --quiet frappe-bench.service; then
		say "next:    sudo systemctl restart frappe-bench   (the running units still have the old definitions)"
	else
		say "next:    sudo systemctl start frappe-bench"
	fi
}

rollback_units() {
	local unit running
	[ -f "$BACKUP" ] || die "no saved unit at $BACKUP, so there is nothing to put back"
	is_ours || die "frappe-bench.service is not the production umbrella; nothing to roll back"
	# activating too: a unit in its Restart= loop is not "active" but will be again in 5 s.
	running=$($SYSTEMCTL list-units 'frappe-bench*' --state=active,activating,reloading --no-legend --plain 2>/dev/null | awk '{print $1}' || true)
	if [ -n "$running" ]; then
		die "still running: $(echo "$running" | tr '\n' ' ')-- stop them first (sudo systemctl stop frappe-bench); nothing was changed"
	fi
	rm -rf "$WANTS"
	for unit in "$UNITS_SRC"/*.service; do
		[ "$(basename "$unit")" = frappe-bench.service ] && continue
		rm -f "$SYSTEMD_DIR/$(basename "$unit")"
	done
	install -m 0644 "$BACKUP" "$UMBRELLA"
	$SYSTEMCTL daemon-reload
	$SYSTEMCTL enable frappe-bench.service
	say "restored the old frappe-bench.service from $BACKUP"
	say "next:    sudo systemctl start frappe-bench"
}

if [ "$REQUIRE_ROOT" = 1 ] && [ "$(id -u)" -ne 0 ]; then
	die "run it with sudo"
fi

case "${1:-}" in
	--check)
		report
		preflight && nginx_check
		;;
	"")
		install_units
		;;
	--rollback)
		rollback_units
		;;
	*)
		die "usage: install_bench_units.sh [--check | --rollback]"
		;;
esac
