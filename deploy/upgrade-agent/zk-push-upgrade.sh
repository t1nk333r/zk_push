#!/usr/bin/env bash
# Copyright (c) 2026 Abdulrahman Aloqayli
# SPDX-License-Identifier: MIT
# ZK Push upgrade agent: does the upgrades asked for on the site (page ZK Push Update).
#
# Runs every minute from the bench user's crontab (see README.md in this folder). On each run it marks
# itself as running, and if a site asked for an upgrade: backs up every site with zk_push, checks out
# the release tag (only one signed with the release key), installs, migrates, restarts the bench, and
# writes the result and the log back to the site that asked. If the install or a migrate fails it goes
# back to the previous version, and restores the sites already migrated from the backups it just took.
#
# Settings (environment, e.g. on the crontab line):
#   ZK_SITES    the sites allowed to ask for an upgrade, space separated (default: every site). The
#               upgrade covers every site on the bench either way: this decides whose System Managers
#               may start it.
#   ZK_MIN_TAG  the oldest release it installs (default below); older ones are not signed, or lack
#               this agent's checks or security fixes
#   ZK_SIGNERS  the release signing key, as a git allowed signers file, copied at install time from a
#               release you trust (default: ~/zk-push-release-signers). Without it nothing is installed
#   ZK_BENCH    the bench folder              (default: ~/frappe-bench)
#   ZK_RESTART  the restart command           (default: the supervisor groups of a production bench)
set -uo pipefail

BENCH="${ZK_BENCH:-$HOME/frappe-bench}"
RESTART="${ZK_RESTART:-sudo -n /usr/bin/supervisorctl restart frappe-bench-web: frappe-bench-workers:}"
MIN_TAG="${ZK_MIN_TAG:-v0.3.6}"
SIGNERS="${ZK_SIGNERS:-$HOME/zk-push-release-signers}"
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

# Back to the previous code and, for the sites in $1 (the ones a migrate touched), the database from
# the backup taken just before. Writes since then are lost; devices resend punches after their stamp,
# which goes back with the database. Prints what could not be restored.
rollback() {
	local failed="" s
	echo "== going back to $PREV"
	git -C "$APP" checkout -q "$PREV" && "$BENCH/env/bin/python" -m pip install -q -e "$APP"
	for s in ${1:-}; do
		echo "== restore $s from ${BACKUP[$s]}"
		if gunzip -c "${BACKUP[$s]}" | bench --site "$s" mariadb; then
			bench --site "$s" clear-cache
		else
			failed+=" $s"
		fi
	done
	$RESTART
	ROLLBACK_FAILED="$failed"
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
	[ -s "$SIGNERS" ] || fail "the release signing key is not installed ($SIGNERS): see deploy/upgrade-agent/README.md"
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
	# Only a tag signed with the release key: a GitHub account or token alone cannot make one.
	git -C "$APP" -c gpg.format=ssh -c gpg.ssh.allowedSignersFile="$SIGNERS" verify-tag "$TAG" \
		|| fail "release $TAG is not signed with the release key; nothing was changed"

	local sites=() s d
	for d in "$SITES_DIR"/*/; do
		s="$(basename "$d")"
		[ -f "$d/site_config.json" ] || continue
		bench --site "$s" list-apps 2>/dev/null | grep -qw zk_push && sites+=("$s")
	done
	[ ${#sites[@]} -gt 0 ] || fail "no site has zk_push installed"
	echo "sites: ${sites[*]}"

	declare -gA BACKUP
	for s in "${sites[@]}"; do
		echo "== backup $s"
		bench --site "$s" backup || fail "backup of $s failed; nothing was changed"
		BACKUP[$s]="$(ls -t "$SITES_DIR/$s"/private/backups/*-database.sql.gz 2>/dev/null | head -1)"
		[ -n "${BACKUP[$s]}" ] || fail "backup of $s not found; nothing was changed"
	done

	echo "== $PREV -> $TAG"
	write_status Running "migrating"
	git -C "$APP" checkout -q "$TAG" || fail "checkout of $TAG failed; nothing was changed"
	"$BENCH/env/bin/python" -m pip install -q -e "$APP" || { rollback ""; fail "install failed; back on $PREV"; }
	if find "$APP/zk_push/public" -type f ! -name .gitkeep | grep -q .; then
		bench build --app zk_push || { rollback ""; fail "asset build failed; back on $PREV"; }
	fi
	local migrated=""
	for s in "${sites[@]}"; do
		echo "== migrate $s"
		migrated+=" $s"  # a failed migrate may have changed part of the database: restore it too
		bench --site "$s" migrate || {
			rollback "$migrated"
			[ -z "$ROLLBACK_FAILED" ] \
				|| fail "migrate of $s failed; back on $PREV, but restoring$ROLLBACK_FAILED failed: restore by hand"
			fail "migrate of $s failed; back on $PREV, databases restored from the backups"
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
