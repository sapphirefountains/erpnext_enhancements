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
# Run it as the bench's owner (sudo -iu frappe), never with sudo. The bench is the directory
# three levels above this file (<bench>/apps/erpnext_enhancements/infra/). Runbook:
# infra/README.md, section 13.
#
# Settings, from the environment:
#   PRE_MIGRATE_BACKUP_DIR   where the backups go; default <bench>/pre-migrate-backups. It may
#                            not be inside <bench>/sites, where the scheduled backups live.
#   PRE_MIGRATE_MIN_FREE_MB  free space the backup's disk must have before it starts; default
#                            2048. The larger of this and twice the newest kept backup applies.
#
# What bench's backup command does (frappe/commands/site.py `backup` and
# frappe/utils/backups.py at v16.56.0): one `<time>-<site>-database[-enc].sql.gz` and one copy
# of site_config.json per site into --backup-path, and a non-zero exit when any site fails.
# --ignore-backup-conf makes the dump whole even where site config narrows the scheduled one.
# Like every scheduled backup, the command also applies the site's own keep_backups_for_hours
# rule to <site>/private/backups; this script itself deletes nothing outside its own directory.

set -euo pipefail
umask 077

readonly TAG_RE='^v16\.[0-9]+\.[0-9]+$'
readonly RUN_RE='^[0-9]{8}T[0-9]{6}Z$'
readonly KEEP=5
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
       bash $PROG upstream <frappe-tag> <erpnext-tag>    (release tags, for example v16.56.0)
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
	cd -- "$BENCH_DIR"
}

# The sites `bench --site all` means: directories under sites/ with a site_config.json, not
# symlinks (frappe.utils.get_sites at v16.56.0).
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

take_backup() {
	local root min_mb need_kb newest newest_kb avail_kb run_dir site slug f found
	local -a sites
	root=$(backup_root)
	mapfile -t sites < <(list_sites)
	[ "${#sites[@]}" -gt 0 ] || backup_failed "no sites found under $BENCH_DIR/sites"
	mkdir -p -- "$root" || backup_failed "could not create $root"
	chmod 700 -- "$root"

	min_mb=${PRE_MIGRATE_MIN_FREE_MB:-2048}
	[[ $min_mb =~ ^[0-9]+$ ]] || backup_failed "PRE_MIGRATE_MIN_FREE_MB must be a whole number of MB"
	need_kb=$((min_mb * 1024))
	newest=$(list_runs "$root" | tail -n 1)
	if [ -n "$newest" ]; then
		newest_kb=$(du -sk -- "$root/$newest" | cut -f1)
		if [ $((newest_kb * 2)) -gt "$need_kb" ]; then need_kb=$((newest_kb * 2)); fi
	fi
	avail_kb=$(df -Pk -- "$root" | awk 'NR == 2 {print $4}')
	[[ $avail_kb =~ ^[0-9]+$ ]] || backup_failed "could not read the free space on the disk that holds $root"
	if [ "$avail_kb" -lt "$need_kb" ]; then
		backup_failed "only $((avail_kb / 1024)) MB free on the disk that holds $root, and $((need_kb / 1024)) MB is required. Free space there first"
	fi

	run_dir=$root/$(date -u +%Y%m%dT%H%M%SZ)
	mkdir -- "$run_dir" || backup_failed "could not create $run_dir"
	say "pre-migrate backup of ${sites[*]} into $run_dir ($((avail_kb / 1024)) MB free)"
	if ! bench --site all backup --backup-path "$run_dir" --ignore-backup-conf; then
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
}

check_tag() {
	[[ $1 =~ $TAG_RE ]] ||
		die "the $2 tag '$1' is not a release tag like v16.56.0 (it must match $TAG_RE); nothing was changed"
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

	# Every check before anything changes: a clean checkout, and the tag fetched and found.
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

	take_backup

	trap 'upstream_failed $?' EXIT
	for app in "${UPSTREAM_APPS[@]}"; do
		tag=${TAGS[$app]}
		git -C "apps/$app" -c advice.detachedHead=false checkout --quiet --detach "refs/tags/$tag"
		say "$app is now at $tag (was ${WAS[$app]})"
	done
	bench setup requirements
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
	*) usage ;;
	esac
}

main "$@"
