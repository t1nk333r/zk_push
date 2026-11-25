#!/usr/bin/env bash
# Copyright (c) 2026 Abdulrahman Aloqayli
# SPDX-License-Identifier: MIT
# ZK Push upgrade agent: does the upgrades asked for on the site (page ZK Push Update).
#
# Runs every minute from the bench user's crontab (see README.md in this folder). On each run it marks
# itself as running, and if a site asked for an upgrade: backs up every site with zk_push, puts them in
# maintenance mode, checks out the release tag (only one signed with the release key), installs,
# migrates, restarts the bench, and writes the result and the log back to the site that asked. If the
# install or a migrate fails it goes back to the previous version, and restores the sites already
# migrated from the backups it just took.
#
# Settings (environment, e.g. on the crontab line):
#   ZK_SITES    the sites allowed to ask for an upgrade, space separated (default: every site). The
#               upgrade covers every site on the bench either way: this decides whose System Managers
#               may start it.
#   ZK_MIN_TAG  the oldest release it installs (default below); older ones are not signed, or lack
#               this agent's checks or security fixes
#   ZK_ALLOW_DOWNGRADE  set to 1 to allow a release older than the one installed (default: refused)
#   ZK_SIGNERS  the release signing key, as a git allowed signers file, copied at install time from a
#               release you trust (default: ~/zk-push-release-signers). Without it nothing is installed
#   ZK_REMOTE   the git remote of apps/zk_push to fetch from (default: upstream, as bench get-app names
#               it, or origin)
#   ZK_BENCH    the bench folder              (default: ~/frappe-bench)
#   ZK_RESTART  the restart command           (default: the supervisor groups of a production bench)
set -uo pipefail

BENCH="${ZK_BENCH:-$HOME/frappe-bench}"
RESTART="${ZK_RESTART:-sudo -n /usr/bin/supervisorctl restart frappe-bench-web: frappe-bench-workers:}"
MIN_TAG="${ZK_MIN_TAG:-v0.3.7}"
SIGNERS="${ZK_SIGNERS:-$HOME/zk-push-release-signers}"
ALLOWED_SITES="${ZK_SITES:-}"
APP="$BENCH/apps/zk_push"
SITES_DIR="$BENCH/sites"
# Set when an upgrade was interrupted (reboot, killed): no new one starts until an administrator has
# checked the bench and removed it.
ATTENTION="$SITES_DIR/.zk_push_upgrade.needs_attention"
LOG_KEEP_BYTES=262144
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
	release_sites
	write_status Failed "$1"
	exit 1
}

site_flag() {  # site, key: prints 1 if site_config.json has it set
	python3 - "$SITES_DIR/$1/site_config.json" "$2" <<'PY'
import json, sys
try:
	print(1 if json.load(open(sys.argv[1])).get(sys.argv[2]) else 0)
except (OSError, ValueError):
	print(0)
PY
}

# Maintenance mode and a paused scheduler while the code and the databases change, so no request or
# job runs the new code against a database not migrated yet. Only what was off before is turned back.
MAINTENANCE="" PAUSED=""
hold_sites() {
	local s
	for s in "$@"; do
		if [ "$(site_flag "$s" maintenance_mode)" = 0 ]; then
			bench --site "$s" set-maintenance-mode on && MAINTENANCE+=" $s"
		fi
		if [ "$(site_flag "$s" pause_scheduler)" = 0 ]; then
			bench --site "$s" scheduler pause && PAUSED+=" $s"
		fi
	done
}

release_sites() {
	local s
	for s in $PAUSED; do bench --site "$s" scheduler resume; done
	for s in $MAINTENANCE; do bench --site "$s" set-maintenance-mode off; done
	PAUSED="" MAINTENANCE=""
}

# Restores site $1 from backup $2. Tables a failed migrate created are not in the backup, so the dump
# alone would leave them (and what patches wrote into them): drop every table the backup does not have.
restore_site() {
	{
		echo "SET FOREIGN_KEY_CHECKS = 0; SET SESSION group_concat_max_len = 4294967295;"
		echo "CREATE TEMPORARY TABLE zk_keep (n varchar(255) PRIMARY KEY);"
		gunzip -c "$2" | grep -o '^CREATE TABLE `[^`]*`' | sed "s/^CREATE TABLE \`\(.*\)\`$/\1/; s/'/''/g; s/.*/INSERT IGNORE INTO zk_keep VALUES ('&');/"
		echo "SELECT GROUP_CONCAT(CONCAT('\`', REPLACE(table_name, '\`', '\`\`'), '\`')) INTO @zk_drop
			FROM information_schema.tables WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'
			AND table_name NOT IN (SELECT n FROM zk_keep);"
		echo "SET @zk_q = IF(@zk_drop IS NULL, 'DO 0', CONCAT('DROP TABLE ', @zk_drop));"
		echo "PREPARE zk_s FROM @zk_q; EXECUTE zk_s; DEALLOCATE PREPARE zk_s; DROP TEMPORARY TABLE zk_keep;"
		gunzip -c "$2"
	} | bench --site "$1" mariadb
}

# Back to the previous code and, for the sites in $1 (the ones a migrate touched), the database from
# the backup taken just before. Writes since then are lost; devices resend punches after their stamp,
# which goes back with the database. Sets ROLLBACK_FAILED to what could not be put back.
rollback() {
	local s
	ROLLBACK_FAILED=""
	echo "== going back to $PREV"
	git -C "$APP" checkout -q "$PREV" && "$BENCH/env/bin/python" -m pip install -q -e "$APP" \
		|| ROLLBACK_FAILED=" the code"
	if [ -n "${BUILT:-}" ] && [ -z "$ROLLBACK_FAILED" ]; then
		bench build --app zk_push || ROLLBACK_FAILED+=" the assets"
	fi
	for s in ${1:-}; do
		echo "== restore $s from ${BACKUP[$s]}"
		if restore_site "$s" "${BACKUP[$s]}"; then
			bench --site "$s" clear-cache
		else
			ROLLBACK_FAILED+=" $s"
		fi
	done
	$RESTART
}

# After rollback: $1 what failed, $2 what was restored besides the code.
fail_back() {
	[ -z "$ROLLBACK_FAILED" ] || fail "$1; going back to $PREV failed for$ROLLBACK_FAILED: fix by hand"
	fail "$1; back on $PREV${2:-}"
}

# One function, so bash has read all of it before the checkout replaces this file's copy in the app.
main() {
	cd "$BENCH" || exit 1
	exec 9>"$SITES_DIR/.zk_push_upgrade.lock"
	flock -n 9 || exit 0  # the previous run is still upgrading
	touch "$SITES_DIR/.zk_push_upgrade_agent"

	# Holding the lock, a status still Running is from a run that died (reboot, killed): the bench may be
	# half upgraded, so stop here until an administrator has looked.
	local f
	for f in "$SITES_DIR"/*/zk_push_upgrade_status.json; do
		[ -f "$f" ] && grep -q '"status": "Running"' "$f" || continue
		touch "$ATTENTION"
		sed -i 's/"status": "Running"/"status": "Failed"/; s|"message": "[^"]*"|"message": "interrupted: check the log and the site, then remove sites/.zk_push_upgrade.needs_attention on the server"|' "$f"
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
		if [ -e "$ATTENTION" ]; then
			rm -f "$f"
			SITE_DIR="$(dirname "$f")" TAG="" write_status Failed \
				"an earlier upgrade was interrupted: an administrator must check the bench, then remove sites/.zk_push_upgrade.needs_attention"
			continue
		fi
		request="$f"
		break
	done
	[ -n "$request" ] || exit 0

	SITE_DIR="$(dirname "$request")"
	TAG="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("tag", ""))' "$request" 2>/dev/null)"
	rm -f "$request"
	# Appended, so an earlier run's log is kept; only the tail is kept once it grows.
	local log="$SITE_DIR/zk_push_upgrade.log"
	if [ -f "$log" ] && [ "$(stat -c %s "$log")" -gt $((LOG_KEEP_BYTES * 2)) ]; then
		tail -c "$LOG_KEEP_BYTES" "$log" >"$log.tmp" && mv "$log.tmp" "$log"
	fi
	exec >>"$log" 2>&1
	echo
	echo "$(date '+%F %T') upgrade to $TAG, asked on $(basename "$SITE_DIR")"

	[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "not a release tag: $TAG"
	[ -s "$SIGNERS" ] || fail "the release signing key is not installed ($SIGNERS): see deploy/upgrade-agent/README.md"
	[ "$(printf '%s\n%s\n' "$MIN_TAG" "$TAG" | sort -V | head -1)" = "$MIN_TAG" ] \
		|| fail "$TAG is older than $MIN_TAG, the oldest release installed from the site"
	write_status Running "backing up"

	[ -z "$(git -C "$APP" status --porcelain --untracked-files=no)" ] \
		|| fail "apps/zk_push has local changes: upgrade by hand"
	PREV="$(git -C "$APP" describe --tags --exact-match 2>/dev/null || git -C "$APP" rev-parse HEAD)"
	local installed
	installed="v$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$APP/zk_push/__init__.py")"
	if [ "${ZK_ALLOW_DOWNGRADE:-0}" != 1 ] && [ "$installed" != "$TAG" ] \
		&& [ "$(printf '%s\n%s\n' "$installed" "$TAG" | sort -V | head -1)" = "$TAG" ]; then
		fail "$TAG is older than the installed $installed: going back needs ZK_ALLOW_DOWNGRADE=1 on the server"
	fi

	local remote="${ZK_REMOTE:-}"
	if [ -z "$remote" ]; then
		remote=origin
		git -C "$APP" remote | grep -qx upstream && remote=upstream
	fi
	# No --force: a release tag moved on GitHub makes the fetch fail instead of changing what it installs.
	git -C "$APP" fetch -q --tags "$remote" \
		|| fail "git fetch from $remote failed (no such remote, no network, or a release tag was moved upstream)"
	local commit
	commit="$(git -C "$APP" rev-parse -q --verify "refs/tags/$TAG^{commit}")" || fail "release $TAG not found"
	# Only a tag signed with the release key: a GitHub account or token alone cannot make one.
	git -C "$APP" -c gpg.format=ssh -c gpg.ssh.allowedSignersFile="$SIGNERS" verify-tag "refs/tags/$TAG" \
		|| fail "release $TAG is not signed with the release key; nothing was changed"
	# The signature covers the name written in the tag: a signed older release pushed again under a new
	# name (to pass as the latest) still says its own name.
	[ "$(git -C "$APP" for-each-ref --format='%(objecttype) %(tag)' "refs/tags/$TAG")" = "tag $TAG" ] \
		|| fail "the signed tag behind $TAG is not $TAG; nothing was changed"
	echo "verified $TAG: $commit"

	local sites=() s d
	for d in "$SITES_DIR"/*/; do
		s="$(basename "$d")"
		[ -f "$d/site_config.json" ] || continue
		bench --site "$s" list-apps 2>/dev/null | grep -qw zk_push && sites+=("$s")
	done
	[ ${#sites[@]} -gt 0 ] || fail "no site has zk_push installed"
	echo "sites: ${sites[*]}"

	declare -gA BACKUP
	local mark="$SITES_DIR/.zk_push_upgrade.backup"
	for s in "${sites[@]}"; do
		echo "== backup $s"
		touch "$mark"
		bench --site "$s" backup || fail "backup of $s failed; nothing was changed"
		# The one just made, not an older one (or one a scheduled backup made meanwhile, before it).
		BACKUP[$s]="$(find "$SITES_DIR/$s/private/backups" -maxdepth 1 -name '*-database.sql.gz' -newer "$mark" \
			-printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)"
		[ -n "${BACKUP[$s]}" ] || fail "backup of $s not found; nothing was changed"
	done

	echo "== $PREV -> $TAG"
	write_status Running "migrating"
	hold_sites "${sites[@]}"
	# The commit verified above, not the tag name again.
	git -C "$APP" checkout -q "$commit" || fail "checkout of $TAG failed; nothing was changed"
	"$BENCH/env/bin/python" -m pip install -q -e "$APP" || { rollback ""; fail_back "install failed"; }
	BUILT=""
	if find "$APP/zk_push/public" -type f ! -name .gitkeep 2>/dev/null | grep -q .; then
		BUILT=1
		bench build --app zk_push || { rollback ""; fail_back "asset build failed"; }
	fi
	local migrated=""
	for s in "${sites[@]}"; do
		echo "== migrate $s"
		migrated+=" $s"  # a failed migrate may have changed part of the database: restore it too
		bench --site "$s" migrate || { rollback "$migrated"; fail_back "migrate of $s failed" ", databases restored from the backups"; }
		bench --site "$s" clear-cache
	done

	echo "== restart"
	$RESTART || fail "upgraded to $TAG but the restart failed: restart the bench by hand"
	release_sites
	echo "$(date '+%F %T') done"
	write_status Done "from $PREV, sites: ${sites[*]}"
}

main "$@"
exit
