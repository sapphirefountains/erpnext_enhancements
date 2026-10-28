#!/bin/bash
# Interim nginx guard for a VM still on the development server, until it is cut over to
# gunicorn (infra/docs/production-web-tier.md, Step 0). It adds lines to the frappe server
# block's `location @webserver`, the one place nginx hands a request to the bench:
#
#   * requests for a Host the debugger would treat as local are refused (444);
#   * requests carrying the debugger's query parameter are refused (404), as defense in depth.
#
#   sudo bash nginx_webserver_guard.sh --check   which file holds the block, and its guard
#   sudo bash nginx_webserver_guard.sh           add or update the guard, nginx -t, reload
#   sudo bash nginx_webserver_guard.sh --undo    put the file back as it was before the first run
#
# Running it again is safe. The file is the one `nginx -T` shows holding the block, so a
# copy nginx does not load is never edited, and a symlink is resolved so the link stays a
# link. It is recorded, with a backup taken on the first run only, in /root, outside every
# directory nginx loads. Earlier guard lines, including an older version's, are removed
# before the current ones go in, and the file keeps its line endings. A rewrite that fails,
# or a result that fails `nginx -t`, is put back as it was, and nothing is left behind.
#
# Under gunicorn there is no debugger, so the guard does nothing after the cutover and can
# stay. The overrides (NGINX, NGINX_RELOAD, STATE_DIR, REQUIRE_ROOT) are for
# tests/test_production_web_tier.py; on a VM, run it bare.
set -euo pipefail

STATE_DIR=${STATE_DIR:-/root}
BACKUP="$STATE_DIR/frappe-nginx.pre-guard"
RECORD="$STATE_DIR/frappe-nginx.guard-file"
REQUIRE_ROOT=${REQUIRE_ROOT:-1}
read -ra NGINX_CMD <<<"${NGINX:-nginx}"
read -ra RELOAD_CMD <<<"${NGINX_RELOAD:-systemctl reload nginx}"

# The copy of the file taken before this run's edit. Removed on any exit unless putting it
# back failed, in which case it is the only good copy and is kept.
BEFORE=""
KEEP_BEFORE=0
cleanup() {
	if [ -n "$BEFORE" ] && [ "$KEEP_BEFORE" = 0 ]; then
		rm -f "$BEFORE"
	fi
}
trap cleanup EXIT

say() { printf '%s\n' "$*"; }
die() {
	printf 'nginx_webserver_guard: %s\n' "$*" >&2
	exit 1
}

# put_back FILE REASON: restore FILE from this run's copy, then stop.
put_back() {
	if cp -p "$BEFORE" "$1"; then
		die "$2; $1 is back as it was. Nothing was reloaded."
	fi
	KEEP_BEFORE=1
	die "$2, and putting $1 back failed: copy $BEFORE over it by hand"
}

# The one loaded file whose text holds `location @webserver {`, resolved past symlinks.
# nginx -T prints every file it loaded under a "# configuration file <path>:" header. Read
# as bytes: a config file in another encoding must not stop the search.
loaded_file() {
	local dump found
	dump=$("${NGINX_CMD[@]}" -T 2>/dev/null) || die "nginx -T failed"
	found=$(python3 -c '
import re, sys
current, hits = None, []
for line in sys.stdin.buffer.read().decode("utf-8", "surrogateescape").split("\n"):
	header = re.match(r"^# configuration file (.+):\r?$", line)
	if header:
		current = header.group(1)
	elif current and re.match(r"^\s*location\s+@webserver\s*\{", line) and current not in hits:
		hits.append(current)
sys.stdout.buffer.write("\n".join(hits).encode("utf-8", "surrogateescape"))
' <<<"$dump")
	[ -n "$found" ] || die "no loaded nginx file has 'location @webserver {'"
	[ "$(wc -l <<<"$found")" -eq 1 ] || die "more than one loaded file has 'location @webserver {': $(tr '\n' ' ' <<<"$found")"
	readlink -f "$found"
}

# Rewrites FILE: drops every guard line an earlier run (of any version) left, then puts the
# current ones right after `location @webserver {`. Exactly one such line is required.
# Line endings are kept as they are (newline=""), and the new lines take the file's own.
# Every failure is a one-line message and a non-zero exit; the caller puts the file back.
edit() {
	python3 - "$1" <<'PY'
import re, sys

path = sys.argv[1]
MARK = "# erpnext_enhancements: debugger guard (infra/docs/production-web-tier.md, Step 0)"
GUARD = [
	MARK,
	'if ($host ~* "^(localhost|127\\.0\\.0\\.1|0\\.0\\.0\\.0)$|\\.localhost$") { return 444; }',
	"if ($arg___debugger__) { return 404; }",
]
earlier = re.compile(
	r"^\s*(# erpnext_enhancements: debugger guard.*"
	r"|if \(\$arg___debugger__\) \{ return 404; \}"
	r'|if \(\$host ~\* "\^\(localhost\|[^"]*"\) \{ return 444; \})\s*$'
)
try:
	with open(path, encoding="utf-8", newline="") as f:
		text = f.read()
except UnicodeDecodeError:
	sys.exit(f"{path} is not UTF-8 text; it was not changed. Send it to Claude.")
except OSError as e:
	sys.exit(f"could not read {path}: {e.strerror}")
cr = "\r" if "\r\n" in text else ""
out, blocks = [], 0
for line in text.split("\n"):
	if earlier.match(line):
		continue
	out.append(line)
	if re.match(r"^\s*location\s+@webserver\s*\{\s*$", line):
		indent = re.match(r"^[ \t]*", line).group(0)
		out += [indent + "\t" + g + cr for g in GUARD]
		blocks += 1
if blocks != 1:
	sys.exit(f"expected one 'location @webserver {{' line in {path}, found {blocks}")
try:
	with open(path, "w", encoding="utf-8", newline="") as f:
		f.write("\n".join(out))
except OSError as e:
	sys.exit(f"could not write {path}: {e.strerror}")
PY
}

# Whether the @webserver block forwards the client's Host (bench's template does). The
# guard reads nginx's $host; the bench must be judging the same name.
forwards_host() {
	python3 - "$1" <<'PY'
import re, sys

with open(sys.argv[1], encoding="utf-8", errors="surrogateescape", newline="") as f:
	text = f.read()
start = re.search(r"^[ \t]*location\s+@webserver\s*\{", text, re.M)
if not start:
	sys.exit(1)
depth, block = 0, None
for i in range(start.end() - 1, len(text)):
	if text[i] == "{":
		depth += 1
	elif text[i] == "}":
		depth -= 1
		if depth == 0:
			block = text[start.end() : i]
			break
ok = block is not None and re.search(r"^\s*proxy_set_header\s+Host\s+\$host\s*;", block, re.M)
sys.exit(0 if ok else 1)
PY
}

state_of() {
	if grep -qF '|0\.0\.0\.0)$|' "$1" && grep -qF 'erpnext_enhancements: debugger guard' "$1"; then
		echo "current"
	elif grep -qE 'arg___debugger__|return 444; \}' "$1"; then
		echo "an earlier version (run this script to update it)"
	else
		echo "none"
	fi
}

check() {
	local file
	"${NGINX_CMD[@]}" -t >/dev/null 2>&1 || say "WARNING  nginx -t fails as things stand"
	file=$(loaded_file)
	say "file:    $file"
	say "guard:   $(state_of "$file")"
	if [ -e "$BACKUP" ]; then say "backup:  $BACKUP (taken on the first run)"; else say "backup:  none yet"; fi
	if ! forwards_host "$file"; then
		say "WARNING  the @webserver block has no 'proxy_set_header Host \$host;'. The guard"
		say "         judges nginx's \$host; send this block to Claude before applying it."
	fi
	grep -n -A4 'location @webserver {' "$file"
}

apply() {
	local file
	"${NGINX_CMD[@]}" -t >/dev/null 2>&1 || die "nginx -t fails before any change; fix that first. Nothing was changed."
	file=$(loaded_file)
	if [ -e "$RECORD" ] && [ "$(cat "$RECORD")" != "$file" ]; then
		die "the guard was applied to $(cat "$RECORD"), but nginx now loads the block from $file. Run --undo first."
	fi
	mkdir -p "$STATE_DIR"
	if [ ! -e "$BACKUP" ]; then
		cp -p "$file" "$BACKUP"
		printf '%s\n' "$file" >"$RECORD"
		say "saved   $file to $BACKUP"
	fi
	BEFORE=$(mktemp "$STATE_DIR/frappe-nginx.before.XXXXXX")
	cp -p "$file" "$BEFORE"
	# The rewrite truncates the file before it writes, so a failure part-way (a full disk)
	# must not leave it short: whatever goes wrong, the copy goes back.
	edit "$file" || put_back "$file" "rewriting it failed"
	"${NGINX_CMD[@]}" -t || put_back "$file" "nginx -t failed with the guard in"
	"${RELOAD_CMD[@]}"
	say "guarded $file and reloaded nginx:"
	grep -n -A4 'location @webserver {' "$file"
}

undo() {
	local file
	if [ ! -e "$RECORD" ] || [ ! -e "$BACKUP" ]; then
		die "nothing to undo: no record of an earlier run in $STATE_DIR"
	fi
	file=$(cat "$RECORD")
	cp -p "$BACKUP" "$file"
	"${NGINX_CMD[@]}" -t || die "nginx -t fails after restoring $file from $BACKUP; both are left in place"
	"${RELOAD_CMD[@]}"
	rm -f "$BACKUP" "$RECORD"
	say "restored $file and reloaded nginx"
}

if [ "$REQUIRE_ROOT" = 1 ] && [ "$(id -u)" -ne 0 ]; then
	die "run it with sudo"
fi

case "${1:-}" in
	--check) check ;;
	"") apply ;;
	--undo) undo ;;
	*) die "usage: nginx_webserver_guard.sh [--check | --undo]" ;;
esac
