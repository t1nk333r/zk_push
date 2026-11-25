# Copyright (c) 2026 t1nk333r
# SPDX-License-Identifier: MIT

"""Upgrade from the site (page ZK Push Update). The site never runs commands: the button only writes a
request file into the site folder. The upgrade agent (deploy/upgrade-agent/zk-push-upgrade.sh, run
by cron as the bench user) picks it up, backs up and migrates every site with zk_push, restarts the
bench, and writes the result back next to the request. All sites on a bench share the app code, so an
upgrade asked for on one site upgrades them all.

Files (none of them served by nginx, which only serves sites/<site>/public):
  sites/<site>/zk_push_upgrade_request.json   written here, removed by the agent when it starts
  sites/<site>/zk_push_upgrade_status.json    written by the agent
  sites/<site>/zk_push_upgrade.log            written by the agent
  sites/.zk_push_upgrade_agent                touched by the agent on every run (is it installed?)"""

import json
import os
import re
import time

import frappe
import requests
from frappe import _
from frappe.utils import now

import zk_push

REPO = "t1nk333r/zk_push"
TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")
# The first release with this page and the agent's checks. Going below it would remove them, and an
# older release lacks security fixes: the agent refuses it too (ZK_MIN_TAG).
MIN_TAG = "v0.3.5"
# The agent runs every minute: older than this, it is not running.
AGENT_STALE_SECONDS = 5 * 60
RELEASES_CACHE_SECONDS = 60 * 60
LOG_TAIL = 20000


def site_file(name):
	return frappe.get_site_path(name)


def read_json(path):
	try:
		with open(path) as f:
			return json.load(f)
	except (OSError, ValueError):
		return None


def agent_seen():
	"""Seconds since the agent last ran, or None if it never has."""
	try:
		return max(0, int(time.time() - os.path.getmtime(os.path.join(frappe.local.sites_path, ".zk_push_upgrade_agent"))))
	except OSError:
		return None


def version_key(tag):
	return tuple(int(x) for x in tag[1:].split("."))


def releases():
	"""The last releases on GitHub (newest first), cached for an hour."""
	cached = frappe.cache.get_value("zk_push_releases")
	if cached is not None:
		return cached
	try:
		r = requests.get(f"https://api.github.com/repos/{REPO}/releases", params={"per_page": 10}, timeout=10)
		r.raise_for_status()
		tags = [
			{"tag": x["tag_name"], "published": (x.get("published_at") or "")[:10], "notes": x.get("body") or ""}
			for x in r.json()
			if not x.get("draft")
			and not x.get("prerelease")
			and TAG_RE.match(x.get("tag_name") or "")
			and version_key(x["tag_name"]) >= version_key(MIN_TAG)
		]
	except (requests.RequestException, ValueError, KeyError, TypeError):
		return []  # not cached: try again next time
	frappe.cache.set_value("zk_push_releases", tags, expires_in_sec=RELEASES_CACHE_SECONDS)
	return tags


@frappe.whitelist()
def get_status(refresh: int | bool = 0) -> dict:
	frappe.only_for("System Manager")
	if int(refresh):
		frappe.cache.delete_value("zk_push_releases")
	log = ""
	try:
		with open(site_file("zk_push_upgrade.log"), errors="replace") as f:
			log = f.read()[-LOG_TAIL:]
	except OSError:
		pass
	return {
		"version": zk_push.__version__,
		"releases": releases(),
		"request": read_json(site_file("zk_push_upgrade_request.json")),
		"status": read_json(site_file("zk_push_upgrade_status.json")),
		"log": log,
		"agent_seen": agent_seen(),
		"agent_stale_seconds": AGENT_STALE_SECONDS,
	}


@frappe.whitelist(methods=["POST"])
def request_upgrade(tag: str) -> dict:
	frappe.only_for("System Manager")
	if not TAG_RE.match(tag or ""):
		frappe.throw(_("{0} is not a release tag").format(tag))
	if tag not in {r["tag"] for r in releases()}:
		frappe.throw(_("{0} is not one of the releases on GitHub from {1} on").format(tag, MIN_TAG))
	seen = agent_seen()
	if seen is None or seen > AGENT_STALE_SECONDS:
		frappe.throw(_("The upgrade agent is not running on the server; see deploy/upgrade-agent in the README"))
	status = read_json(site_file("zk_push_upgrade_status.json")) or {}
	if status.get("status") == "Running":
		frappe.throw(_("An upgrade is already running"))
	request = {"tag": tag, "requested_by": frappe.session.user, "requested_at": now()}
	path = site_file("zk_push_upgrade_request.json")
	tmp = path + ".tmp"
	with open(tmp, "w") as f:
		json.dump(request, f)
	os.replace(tmp, path)
	return request
