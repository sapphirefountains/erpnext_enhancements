#!/usr/bin/env bash
# The two bench operations that change the database, each behind a database backup.
#
#   bash bench_update.sh backup
#       A database-only backup of every site on the bench, taken with bench's own `backup`
#       command into a directory used only for these backups. infra/cloudbuild-deploy.yaml
#       runs it before `bench --site all migrate`; when it fails, the deploy stops there.
#
#   bash bench_update.sh upstream <frappe-tag> <erpnext-tag>
#       Moves frappe and erpnext to exactly those release tags (v16.MINOR.PATCH), never to a
#       branch head: every check first, then the same backup, then a checkout of each tag,
#       then bench's own `setup requirements`, `--site all migrate` and `build`. This is the
#       only supported way to update Frappe and ERPNext. infra/cloudbuild-upstream.yaml runs
#       exactly this, and so does a person in a shell. Restart afterwards with
#       `sudo systemctl restart frappe-bench` (the trigger does that itself).
#
#   bash bench_update.sh redis
#       Checks that both Redis instances the bench is configured for answer, and changes
#       nothing. A migrate started while one is down stops partway, with part of its work
#       already in the database. `upstream` runs this check before its backup and again just
#       before its migrate; infra/cloudbuild-deploy.yaml runs it before the deploy's backup.
#
# Run it as the bench's owner (sudo -iu frappe), never with sudo. The bench is the directory
# three levels above this file (<bench>/apps/erpnext_enhancements/infra/). Runbook:
# infra/README.md, section 13.
#
# Settings, from the environment:
#   PRE_MIGRATE_BACKUP_DIR   where the backups go; default <bench>/pre-migrate-backups. It may
#                            not be inside <bench>/sites, where the scheduled backups live.
#   PRE_MIGRATE_MIN_FREE_MB  the free-space margin in MB; default 5120. Before it starts, the
#                            backup's disk must have twice the newest kept backup plus this
#                            margin free, or the margin alone when no backup is kept yet.
#   PRE_MIGRATE_LOCK_WAIT_S  how long a backup waits, in seconds, for another one to finish;
#                            default 900. Only one backup runs at a time: each holds an
#                            exclusive flock(1) on <backup dir>/.bench_update.lock (flock ships
#                            in util-linux). A run that cannot get it in time fails and deletes
#                            nothing. The lock file itself is never deleted.
#
# What the update's own steps install and build (frappe at v16.60.2): `bench setup requirements`
# installs each app's Python packages and the JavaScript packages of each app's root
# package.json. `bench build` installs and builds the rest itself, on every run: frappe's
# studio/frontend and flow apps (esbuild/esbuild.js:143-149, :567-581) and its island toolchain
# (frappe/commands/utils.py:100, ui/vite/island/toolchain.js). Neither installs operating-system
# packages: a release that needs new ones lists them in its pyproject.toml under
# [deploy.dependencies.apt], and a person installs those as root before the update.
#
# What bench's backup command does (frappe at v16.60.2): one `<time>-<site>-database[-enc].sql.gz`
# and one copy of site_config.json per site into --backup-path, and a non-zero exit when any site
# fails. --ignore-backup-conf makes the dump whole even where site config narrows the scheduled
# one. It also deletes files, and not in --backup-path:
#   frappe/commands/site.py:881 calls scheduled_backup() for each site, which calls new_backup()
#   (frappe/utils/backups.py:569, 608), whose first line (:625) is delete_temp_backups(). That
#   (:651-662) removes every file in get_backup_path() whose ctime is at least N hours old, where
#   N is the site's keep_backups_for_hours config value, or 23 when it is unset or 0 (:655).
#   get_backup_path() (:686) is <site>/private/backups, or the site's `backup_path` config key;
#   the --backup-path option does not change it. So every run of this script, and so every
#   deploy, deletes the scheduled backups in <site>/private/backups that are 23 hours old or older
#   (N hours, when set). The CLI has no option to skip that. This script itself deletes nothing
#   outside its own directory.

set -euo pipefail
umask 077

readonly TAG_RE='^v16\.[0-9]+\.[0-9]+$'
readonly RUN_RE='^[0-9]{8}T[0-9]{6}Z$'
readonly KEEP=5
readonly LOCK_NAME=.bench_update.lock
readonly UPSTREAM_APPS=(frappe erpnext)

PROG=$(basename -- "${BASH_SOURCE[0]}")
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
BENCH_DIR=${BENCH_DIR:-$(cd -- "$SCRIPT_DIR/../../.." && pwd)}
LAST_BACKUP=""
declare -A TAGS=() WAS=()

say() { printf '%s: %s\n' "$PROG" "$*"; }
die() {
	printf '%s: %s\n' "$PROG" "$*" >&2
	exit 1
}

usage() {
	cat >&2 <<EOF
usage: bash $PROG backup
       bash $PROG upstream <frappe-tag> <erpnext-tag>    (release tags, for example v16.60.2 v16.60.0)
       bash $PROG redis
EOF
	exit 2
}

backup_failed() {
	printf '%s: PRE-MIGRATE BACKUP FAILED: %s\n' "$PROG" "$1" >&2
	printf '%s: this run migrated nothing and switched no app; stopping here.\n' "$PROG" >&2
	exit 1
}

check_bench() {
	[ "$(id -u)" -ne 0 ] || die "run this as the bench's owner (sudo -iu frappe), never as root"
	{ [ -d "$BENCH_DIR/sites" ] && [ -d "$BENCH_DIR/apps/frappe" ]; } ||
		die "$BENCH_DIR is not a bench (it has no sites/ or apps/frappe)"
	command -v bench >/dev/null || die "bench is not on PATH (as frappe: export PATH=\$PATH:\$HOME/.local/bin)"
	command -v flock >/dev/null ||
		backup_failed "flock is not installed; it ships in util-linux (sudo apt-get install util-linux)"
	cd -- "$BENCH_DIR"
}

# The sites `bench --site all` means: directories under sites/ with a site_config.json, not
# symlinks (frappe.utils.get_sites, frappe/utils/__init__.py:702-718 at v16.60.2).
list_sites() {
	local path
	for path in "$BENCH_DIR"/sites/*/; do
		path=${path%/}
		[ -L "$path" ] && continue
		[ -f "$path/site_config.json" ] || continue
		printf '%s\n' "${path##*/}"
	done
}

backup_root() {
	local root=${PRE_MIGRATE_BACKUP_DIR:-$BENCH_DIR/pre-migrate-backups}
	root=${root%/}
	case $root in
	/?*) ;;
	*) backup_failed "PRE_MIGRATE_BACKUP_DIR must be an absolute path below /, not '$root'" ;;
	esac
	case $root/ in
	"$BENCH_DIR"/sites/*) backup_failed "PRE_MIGRATE_BACKUP_DIR may not be inside $BENCH_DIR/sites" ;;
	esac
	printf '%s\n' "$root"
}

# The runs this script made, oldest first: directories named like 20261119T201500Z (UTC).
list_runs() {
	local path name
	for path in "$1"/*/; do
		path=${path%/}
		name=${path##*/}
		[ -L "$path" ] && continue
		[[ $name =~ $RUN_RE ]] || continue
		printf '%s\n' "$name"
	done | LC_ALL=C sort
}

# Keep the newest $KEEP runs in this script's own directory. Nothing else is looked at.
prune() {
	local root=$1 count i
	local -a runs
	mapfile -t runs < <(list_runs "$root")
	count=${#runs[@]}
	for ((i = 0; i < count - KEEP; i++)); do
		say "removing older pre-migrate backup ${runs[i]}"
		rm -rf -- "${root:?}/${runs[i]:?}"
	done
}

# One backup at a time: an exclusive flock on <root>/$LOCK_NAME, held on file descriptor 9
# for the whole of take_backup. The lock file sits beside the run directories, never inside
# one, and prune never looks at it.
# shellcheck disable=SC2317,SC2329  # also called from the EXIT trap in take_backup
release_lock() {
	flock -u 9 2>/dev/null || true
	exec 9>&-
}

acquire_lock() {
	local root=$1 wait_s lock
	wait_s=${PRE_MIGRATE_LOCK_WAIT_S:-900}
	[[ $wait_s =~ ^[0-9]+$ ]] || backup_failed "PRE_MIGRATE_LOCK_WAIT_S must be a whole number of seconds"
	lock=$root/$LOCK_NAME
	exec 9>>"$lock" || backup_failed "could not open the lock file $lock"
	trap release_lock EXIT
	flock -n 9 && return 0
	if [ "$wait_s" -gt 0 ]; then
		say "another pre-migrate backup is running; waiting up to ${wait_s}s for $lock"
		flock -w "$wait_s" 9 && return 0
	fi
	backup_failed "another pre-migrate backup still held $lock after ${wait_s}s (PRE_MIGRATE_LOCK_WAIT_S); nothing was deleted"
}

take_backup() {
	local root margin_mb need_kb newest newest_kb avail_kb run_dir site slug f found rule
	local -a sites
	root=$(backup_root)
	mapfile -t sites < <(list_sites)
	[ "${#sites[@]}" -gt 0 ] || backup_failed "no sites found under $BENCH_DIR/sites"
	mkdir -p -- "$root" || backup_failed "could not create $root"
	chmod 700 -- "$root"
	acquire_lock "$root"

	# Free space: twice the newest kept backup plus the margin, or the margin alone.
	margin_mb=${PRE_MIGRATE_MIN_FREE_MB:-5120}
	[[ $margin_mb =~ ^[0-9]+$ ]] || backup_failed "PRE_MIGRATE_MIN_FREE_MB must be a whole number of MB"
	need_kb=$((margin_mb * 1024))
	rule="a margin of $margin_mb MB, with no kept backup yet"
	newest=$(list_runs "$root" | tail -n 1)
	if [ -n "$newest" ]; then
		newest_kb=$(du -sk -- "$root/$newest" | cut -f1)
		need_kb=$((newest_kb * 2 + need_kb))
		rule="twice the newest kept backup ($newest, $((newest_kb / 1024)) MB) plus a margin of $margin_mb MB"
	fi
	avail_kb=$(df -Pk -- "$root" | awk 'NR == 2 {print $4}')
	[[ $avail_kb =~ ^[0-9]+$ ]] || backup_failed "could not read the free space on the disk that holds $root"
	if [ "$avail_kb" -lt "$need_kb" ]; then
		backup_failed "only $((avail_kb / 1024)) MB free on the disk that holds $root, and $((need_kb / 1024)) MB is required: $rule. Free space there first"
	fi

	run_dir=$root/$(date -u +%Y%m%dT%H%M%SZ)
	mkdir -- "$run_dir" || backup_failed "could not create $run_dir"
	say "pre-migrate backup of ${sites[*]} into $run_dir ($((avail_kb / 1024)) MB free)"
	# 9>&-: bench and anything it starts do not inherit the lock.
	if ! bench --site all backup --backup-path "$run_dir" --ignore-backup-conf 9>&-; then
		rm -rf -- "${run_dir:?}"
		backup_failed "bench backup exited with an error"
	fi
	for site in "${sites[@]}"; do
		slug=${site//./_}
		found=""
		for f in "$run_dir"/*-"$slug"-database*.sql.gz; do
			if [ -s "$f" ]; then found=$f; fi
		done
		if [ -z "$found" ]; then
			rm -rf -- "${run_dir:?}"
			backup_failed "bench backup wrote no database file for $site"
		fi
	done
	say "pre-migrate backup written (name and size):"
	for f in "$run_dir"/*; do
		[ -f "$f" ] || continue
		printf '  %s  %s\n' "$(du -h -- "$f" | cut -f1)" "${f##*/}"
	done
	prune "$root"
	LAST_BACKUP=$run_dir
	trap - EXIT
	release_lock
}

check_tag() {
	[[ $1 =~ $TAG_RE ]] ||
		die "the $2 tag '$1' is not a release tag like v16.60.2 (it must match $TAG_RE); nothing was changed"
}

# Both Redis instances must answer before a migrate starts. frappe reads a cache that is down
# as an empty one instead of stopping, so a migrate started then fails partway through, after
# it has already changed the database. The addresses are the ones frappe itself uses: the
# redis_cache and redis_queue keys of sites/common_site_config.json, behind frappe's two
# environment overrides and before its two defaults (frappe/config.py:123-129 at v16.60.2).
# They are asked with the bench's own Python and Redis client, so a URL with TLS, a socket path
# or a password works as it does for frappe; a password is never printed. $1 says what state
# the bench is in when the check fails.
check_redis() {
	local python=$BENCH_DIR/env/bin/python status=0
	[ -x "$python" ] || die "cannot check Redis: the bench has no env/bin/python; $1"
	"$python" - "$BENCH_DIR/sites/common_site_config.json" "$PROG" <<'PY' || status=$?
import json
import os
import sys
from urllib.parse import urlsplit

path, prog = sys.argv[1:3]
INSTANCES = (
	("redis_cache", "FRAPPE_REDIS_CACHE", "redis://127.0.0.1:13311"),
	("redis_queue", "FRAPPE_REDIS_QUEUE", "redis://127.0.0.1:11311"),
)


def shown(url):
	parts = urlsplit(url)
	if parts.scheme == "unix":
		return f"unix://{parts.path}"
	return f"{parts.scheme}://{parts.hostname}:{parts.port or 6379}"


try:
	with open(path, encoding="utf-8") as handle:
		conf = json.load(handle)
	import redis
except Exception as exc:
	print(f"{prog}: could not check Redis: {type(exc).__name__}: {exc}", file=sys.stderr)
	sys.exit(2)

down = 0
for key, env, default in INSTANCES:
	if conf.get(f"{key}_sentinel_enabled"):
		print(f"{prog}: {key} is reached through Redis Sentinel; not checked here")
		continue
	url = os.environ.get(env) or conf.get(key) or default
	if not isinstance(url, str):
		down += 1
		print(f"{prog}: {key} in {path} is not a URL", file=sys.stderr)
		continue
	where, secret, refused = key, None, None
	try:
		where, secret = f"{key} at {shown(url)}", urlsplit(url).password
		redis.Redis.from_url(url, socket_connect_timeout=5, socket_timeout=5).ping()
	except (redis.exceptions.AuthenticationError, redis.exceptions.ResponseError) as exc:
		refused = exc
	except Exception as exc:
		down += 1
		reason = str(exc).replace(secret, "***") if secret else str(exc)
		print(f"{prog}: {where} did not answer: {type(exc).__name__}: {reason}", file=sys.stderr)
		continue
	if refused is None:
		print(f"{prog}: {where} answers")
	elif key == "redis_queue":
		# frappe can sign in to the queue with a user of its own (use_rq_auth), which this
		# check does not have: the instance is up, and that is all this can say.
		print(f"{prog}: {where} answers (it asks for a sign-in, which this check does not send)")
	else:
		# frappe has only this URL for the cache, so it would be refused the same way.
		down += 1
		print(f"{prog}: {where} refused the sign-in: {type(refused).__name__}", file=sys.stderr)
sys.exit(1 if down else 0)
PY
	case $status in
	0) ;;
	1) die "Redis is not ready, and a migrate started now would stop partway; $1. Start Redis (sudo systemctl start frappe-bench starts both instances), then run the same command again" ;;
	*) die "Redis could not be checked (the reason is above); $1" ;;
	esac
}

# The remote bench itself pulls from: `upstream` when there is one, else the first
# (bench.utils.app.get_remote, bench v5.31.1).
app_remote() {
	local remotes
	remotes=$(git -C "$1" remote)
	if [[ $'\n'$remotes$'\n' == *$'\n'upstream$'\n'* ]]; then
		printf 'upstream\n'
	elif [ -n "$remotes" ]; then
		printf '%s\n' "${remotes%%$'\n'*}"
	else
		return 1
	fi
}

# shellcheck disable=SC2317,SC2329  # called from the EXIT trap below
upstream_failed() {
	local status=$1 app
	[ "$status" -ne 0 ] || return 0
	{
		say "UPSTREAM UPDATE FAILED after the code was switched; the bench was not restarted."
		say "the database backup taken before the switch is in $LAST_BACKUP"
		for app in "${UPSTREAM_APPS[@]}"; do
			say "$app was at commit ${WAS[$app]:-unknown} before this run"
		done
		say "to go back, follow 'Restore a pre-migrate backup' in infra/README.md"
	} >&2
}

upstream() {
	[ "$#" -eq 2 ] || usage
	local app dir tag remote
	check_tag "$1" frappe
	check_tag "$2" erpnext
	TAGS=([frappe]=$1 [erpnext]=$2)
	check_bench

	# Every check before anything changes: a clean checkout, the tag fetched and found, and
	# both Redis instances answering.
	for app in "${UPSTREAM_APPS[@]}"; do
		dir=apps/$app
		tag=${TAGS[$app]}
		git -C "$dir" rev-parse --git-dir >/dev/null 2>&1 || die "$dir is not a git checkout; nothing was changed"
		if [ -n "$(git -C "$dir" status --porcelain --untracked-files=no)" ]; then
			die "$dir has changes to tracked files; nothing was changed. See them with: git -C $BENCH_DIR/$dir status"
		fi
		remote=$(app_remote "$dir") || die "$dir has no git remote; nothing was changed"
		say "fetching $tag of $app from $remote"
		git -C "$dir" fetch --quiet --no-tags "$remote" "refs/tags/$tag:refs/tags/$tag" ||
			die "could not fetch the tag $tag of $app from $remote; nothing was changed"
		git -C "$dir" rev-parse --verify --quiet "refs/tags/$tag^{commit}" >/dev/null ||
			die "the tag $tag of $app does not name a commit; nothing was changed"
		WAS[$app]=$(git -C "$dir" rev-parse HEAD)
	done
	check_redis "nothing was changed"

	take_backup

	trap 'upstream_failed $?' EXIT
	for app in "${UPSTREAM_APPS[@]}"; do
		tag=${TAGS[$app]}
		git -C "apps/$app" -c advice.detachedHead=false checkout --quiet --detach "refs/tags/$tag"
		say "$app is now at $tag (was ${WAS[$app]})"
	done
	bench setup requirements
	# Again, now that the backup and the installs have taken their time.
	check_redis "the code was switched but nothing was migrated"
	bench --site all migrate
	bench build
	trap - EXIT
	say "frappe ${TAGS[frappe]} and erpnext ${TAGS[erpnext]} are installed and migrated."
	say "restart the bench now: sudo systemctl restart frappe-bench"
}

main() {
	case ${1:-} in
	backup)
		[ "$#" -eq 1 ] || usage
		check_bench
		take_backup
		;;
	upstream)
		shift
		upstream "$@"
		;;
	redis)
		[ "$#" -eq 1 ] || usage
		check_bench
		check_redis "nothing was changed"
		;;
	*) usage ;;
	esac
}

main "$@"
