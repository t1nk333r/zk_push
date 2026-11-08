"""Pull mode for devices without ADMS / Cloud Server settings.

Runs on an always-on machine on the device's LAN. Every --interval seconds it reads the attendance
log over the LAN (TCP 4370, read-only) and posts new punches to the site's /iclock/cdata endpoint,
exactly as a push device would. Everything on the site (de-duplication, checkins, unmatched users)
works the same; device commands (Reboot, Request Info, ...) do not, since the device never polls.

Usage (requires: pip install pyzk):
  python pull_from_device.py <device_ip> <site_url> [--interval 120] [--comm-key N] [--state FILE] [--once]

The ZK Device must exist and be enabled on the site. On the first run every punch on the device is
sent (set "Ignore Punches Before" on the ZK Device first); after that only new ones. The last sent
punch time is kept in --state; each run re-sends the last day as well, which the site ignores, so
punches are not missed if the device clock is moved back a little.
"""

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from backfill_from_device import CHUNK, attlog_lines, post, read_device

OVERLAP = timedelta(days=1)
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def log(message):
	print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}", flush=True)


def load_state(path):
	try:
		return datetime.strptime(json.loads(path.read_text())["last_punch"], TIME_FORMAT)
	except FileNotFoundError:
		return None


def save_state(path, last_punch):
	tmp = path.with_suffix(".tmp")
	tmp.write_text(json.dumps({"last_punch": f"{last_punch:{TIME_FORMAT}}"}))
	tmp.replace(path)


def heartbeat(site, serial):
	"""Handshake GET, so the site shows the device as online even when nobody punches."""
	query = urllib.parse.urlencode({"SN": serial, "options": "all"})
	try:
		with urllib.request.urlopen(f"{site.rstrip('/')}/iclock/cdata?{query}", timeout=30) as resp:
			return resp.status
	except urllib.error.HTTPError as e:
		return e.code


def sync(args, state_path):
	serial, records = read_device(args.device_ip, args.comm_key)
	status = heartbeat(args.site_url, serial)
	if status != 200:
		log(f"Device {serial}: the site answered HTTP {status} (is the ZK Device enabled?)")
		return

	last = load_state(state_path)
	since = last - OVERLAP if last else None
	records = sorted((r for r in records if not since or r.timestamp >= since), key=lambda r: r.timestamp)
	new = sum(1 for r in records if not last or r.timestamp > last)
	if not new:
		return

	lines = attlog_lines(records)
	for start in range(0, len(lines), CHUNK):
		status, text = post(args.site_url, serial, lines[start : start + CHUNK])
		if status != 200:
			log(f"Device {serial}: upload rejected, HTTP {status} {text.strip()[:80]}; retrying next run")
			return
	save_state(state_path, records[-1].timestamp)
	log(f"Device {serial}: sent {new} new punch(es), last at {records[-1].timestamp}")


def main():
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("device_ip")
	parser.add_argument("site_url")
	parser.add_argument("--interval", type=int, default=120, help="Seconds between pulls (default 120)")
	parser.add_argument("--comm-key", type=int, default=0, help="Device Comm Key (default 0)")
	parser.add_argument("--state", help="State file (default: zk_pull_<device_ip>.json next to this script)")
	parser.add_argument("--once", action="store_true", help="Pull once and exit (for cron)")
	args = parser.parse_args()

	state_path = Path(args.state or Path(__file__).with_name(f"zk_pull_{args.device_ip}.json"))
	while True:
		try:
			sync(args, state_path)
		except Exception as e:  # device offline, network down, ...: try again next run
			log(f"Pull failed: {e!r}")
			if args.once:
				raise SystemExit(1) from e
		if args.once:
			return
		time.sleep(args.interval)


if __name__ == "__main__":
	main()
