#!/usr/bin/env bash
# ZK Push upgrade agent: does the upgrades asked for on the site (page ZK Push Update).
#
# Runs every minute from the bench user's crontab (see README.md in this folder). On each run it marks
# itself as running, and if a site asked for an upgrade: backs up every site with zk_push, checks out
# the release tag, installs, migrates, restarts the bench, and writes the result and the log back to
# the site that asked. If the install or a migrate fails it goes back to the previous version.
#
# Settings (environment, e.g. on the crontab line):
#   ZK_SITES    the sites allowed to ask for an upgrade, space separated (default: every site). The
#               upgrade covers every site on the bench either way: this decides whose System Managers
#               may start it.
#   ZK_MIN_TAG  the oldest release it installs (default below); older ones lack this agent's checks or
#               security fixes
#   ZK_BENCH    the bench folder              (default: ~/frappe-bench)
#   ZK_RESTART  the restart command           (default: the supervisor groups of a production bench)
set -uo pipefail

BENCH="${ZK_BENCH:-$HOME/frappe-bench}"
RESTART="${ZK_RESTART:-sudo -n /usr/bin/supervisorctl restart frappe-bench-web: frappe-bench-workers:}"
MIN_TAG="${ZK_MIN_TAG:-v0.3.5}"
ALLOWED_SITES="${ZK_SITES:-}"
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

	# Holding the lock, a status still Running is from a run that died (reboot, killed).
	local f
	for f in "$SITES_DIR"/*/zk_push_upgrade_status.json; do
		[ -f "$f" ] && grep -q '"status": "Running"' "$f" \
			&& sed -i 's/"status": "Running"/"status": "Failed"/; s/"message": "[^"]*"/"message": "interrupted: check the log and the site"/' "$f"
	done

	local request="" site
	for f in "$SITES_DIR"/*/zk_push_upgrade_request.json; do
		[ -f "$f" ] || continue
		site="$(basename "$(dirname "$f")")"
		if [ -n "$ALLOWED_SITES" ] && [[ " $ALLOWED_SITES " != *" $site "* ]]; then
			rm -f "$f"  # not allowed to ask: drop it, and say so on that site
			SITE_DIR="$(dirname "$f")" TAG="" write_status Failed "upgrades are not started from this site"
			continue
		fi
		request="$f"
		break
	done
	[ -n "$request" ] || exit 0

	SITE_DIR="$(dirname "$request")"
	TAG="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("tag", ""))' "$request" 2>/dev/null)"
	rm -f "$request"
	exec >"$SITE_DIR/zk_push_upgrade.log" 2>&1

	[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "not a release tag: $TAG"
	[ "$(printf '%s\n%s\n' "$MIN_TAG" "$TAG" | sort -V | head -1)" = "$MIN_TAG" ] \
		|| fail "$TAG is older than $MIN_TAG, the oldest release installed from the site"
	write_status Running "backing up"
	echo "$(date '+%F %T') upgrade to $TAG, asked on $(basename "$SITE_DIR")"

	[ -z "$(git -C "$APP" status --porcelain --untracked-files=no)" ] \
		|| fail "apps/zk_push has local changes: upgrade by hand"
	PREV="$(git -C "$APP" describe --tags --exact-match 2>/dev/null || git -C "$APP" rev-parse HEAD)"
	# No --force: a release tag moved on GitHub makes the fetch fail instead of changing what it installs.
	git -C "$APP" fetch -q --tags origin || fail "git fetch failed (a release tag may have been moved upstream)"
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
