#!/usr/bin/env bash
# ZK Push upgrade agent: does the upgrades asked for on the site (page ZK Push Update).
#
# Runs every minute from the bench user's crontab (see README.md in this folder). On each run it marks
# itself as running, and if a site asked for an upgrade: backs up every site with zk_push, checks out
# the release tag, installs, migrates, restarts the bench, and writes the result and the log back to
# the site that asked. If the install or a migrate fails it goes back to the previous version.
#
# Settings (environment, or edit here):
#   ZK_BENCH    the bench folder              (default: ~/frappe-bench)
#   ZK_RESTART  the restart command           (default: the supervisor groups of a production bench)
set -uo pipefail

BENCH="${ZK_BENCH:-$HOME/frappe-bench}"
RESTART="${ZK_RESTART:-sudo -n /usr/bin/supervisorctl restart frappe-bench-web: frappe-bench-workers:}"
APP="$BENCH/apps/zk_push"
SITES_DIR="$BENCH/sites"
# cron starts with a short PATH; bench is usually installed with pip --user or pipx.
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"

write_status() {  # status, message
	python3 - "$SITE_DIR/zk_push_upgrade_status.json" "$1" "$TAG" "${2:-}" <<'PY'
import datetime, json, os, sys
path, status, tag, message = sys.argv[1:5]
with open(path + ".tmp", "w") as f:
	json.dump({"status": status, "tag": tag, "message": message,
		"at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}, f)
os.replace(path + ".tmp", path)
PY
}

fail() {
	echo "FAILED: $1"
	write_status Failed "$1"
	exit 1
}

rollback() {
	echo "== going back to $PREV"
	git -C "$APP" checkout -q "$PREV" && "$BENCH/env/bin/python" -m pip install -q -e "$APP"
	$RESTART
}

# One function, so bash has read all of it before the checkout replaces this file's copy in the app.
main() {
	cd "$BENCH" || exit 1
	exec 9>"$SITES_DIR/.zk_push_upgrade.lock"
	flock -n 9 || exit 0  # the previous run is still upgrading
	touch "$SITES_DIR/.zk_push_upgrade_agent"

	local request=""
	for f in "$SITES_DIR"/*/zk_push_upgrade_request.json; do
		[ -f "$f" ] && { request="$f"; break; }
	done
	[ -n "$request" ] || exit 0

	SITE_DIR="$(dirname "$request")"
	TAG="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("tag", ""))' "$request" 2>/dev/null)"
	rm -f "$request"
	exec >"$SITE_DIR/zk_push_upgrade.log" 2>&1

	[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "not a release tag: $TAG"
	write_status Running "backing up"
	echo "$(date '+%F %T') upgrade to $TAG, asked on $(basename "$SITE_DIR")"

	[ -z "$(git -C "$APP" status --porcelain --untracked-files=no)" ] \
		|| fail "apps/zk_push has local changes: upgrade by hand"
	PREV="$(git -C "$APP" describe --tags --exact-match 2>/dev/null || git -C "$APP" rev-parse HEAD)"
	git -C "$APP" fetch -q --tags --force origin || fail "git fetch failed"
	git -C "$APP" rev-parse -q --verify "refs/tags/$TAG^{commit}" >/dev/null || fail "release $TAG not found"

	local sites=() s d
	for d in "$SITES_DIR"/*/; do
		s="$(basename "$d")"
		[ -f "$d/site_config.json" ] || continue
		bench --site "$s" list-apps 2>/dev/null | grep -qw zk_push && sites+=("$s")
	done
	[ ${#sites[@]} -gt 0 ] || fail "no site has zk_push installed"
	echo "sites: ${sites[*]}"

	for s in "${sites[@]}"; do
		echo "== backup $s"
		bench --site "$s" backup || fail "backup of $s failed; nothing was changed"
	done

	echo "== $PREV -> $TAG"
	write_status Running "migrating"
	git -C "$APP" checkout -q "$TAG" || fail "checkout of $TAG failed; nothing was changed"
	"$BENCH/env/bin/python" -m pip install -q -e "$APP" || { rollback; fail "install failed; back on $PREV"; }
	if find "$APP/zk_push/public" -type f ! -name .gitkeep | grep -q .; then
		bench build --app zk_push || { rollback; fail "asset build failed; back on $PREV"; }
	fi
	for s in "${sites[@]}"; do
		echo "== migrate $s"
		bench --site "$s" migrate || {
			rollback
			fail "migrate of $s failed; back on $PREV (restore its backup if data looks wrong)"
		}
		bench --site "$s" clear-cache
	done

	echo "== restart"
	$RESTART || fail "upgraded to $TAG but the restart failed: restart the bench by hand"
	echo "$(date '+%F %T') done"
	write_status Done "from $PREV, sites: ${sites[*]}"
}

main "$@"
exit
