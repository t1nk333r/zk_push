"""Pull mode for devices without ADMS / Cloud Server settings.

Runs on an always-on machine on the device's LAN. Every --interval seconds it reads the attendance
log over the LAN (TCP 4370, read-only) and posts new punches to the site's /iclock/cdata endpoint,
exactly as a push device would. Everything on the site (de-duplication, checkins, unmatched users)
works the same; device commands (Reboot, Request Info, ...) do not, since the device never polls.

Usage (requires: pip install pyzk):
  python pull_from_device.py <device_ip> <site_url> [--since 2026] [--interval 120] [--comm-key N] [--state FILE] [--once]

The ZK Device must exist and be enabled on the site. On the first run every punch on the device is
sent, or only those from --since on (a year, month or day, e.g. --since 2026); after that only new
ones. Punches before --since are never sent, on any run. --state keeps
the last punch time and the punches sent in the day before it, so a punch stamped in the same
second, or after the device clock was moved back by less than a day, is still sent. The script reads
the device's whole log on every run, which takes longer on devices holding a large log.
"""

import argparse
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from backfill_from_device import CHUNK, accepted, attlog_lines, cdata_request, post, read_device, since_date

OVERLAP = timedelta(days=1)
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def log(message):
	print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}", flush=True)


def punch_key(record):
	return f"{record.user_id}\t{record.timestamp:{TIME_FORMAT}}"


def load_state(path):
	"""(last punch time, keys of the punches sent within OVERLAP of it)."""
	try:
		state = json.loads(path.read_text())
		return datetime.strptime(state["last_punch"], TIME_FORMAT), set(state["sent"])
	except FileNotFoundError:
		return None, set()
	except (ValueError, KeyError, TypeError):
		# Starting over only re-sends punches the site already has, which it ignores.
		path.replace(path.with_name(path.name + ".bad"))
		log(f"Unreadable state file, moved to {path.name}.bad; sending the full log again")
		return None, set()


def save_state(path, last_punch, records):
	keep_from = last_punch - OVERLAP
	sent = sorted(punch_key(r) for r in records if r.timestamp >= keep_from)
	tmp = path.with_name(path.name + ".tmp")
	tmp.write_text(json.dumps({"last_punch": f"{last_punch:{TIME_FORMAT}}", "sent": sent}))
	tmp.replace(path)


def heartbeat(site, serial):
	"""Handshake GET, so the site shows the device as online even when nobody punches."""
	return cdata_request(site, {"SN": serial, "options": "all"}, timeout=30)


def sync(args, state_path) -> bool:
	"""One pull. Returns False if the site did not accept it (retried next run)."""
	serial, records = read_device(args.device_ip, args.comm_key)
	status, text = heartbeat(args.site_url, serial)
	if status != 200:
		hint = text.strip()[:120] if 300 <= status < 400 else "is the ZK Device enabled?"
		log(f"Device {serial}: the site answered HTTP {status} ({hint})")
		return False

	last, sent = load_state(state_path)
	since = max(filter(None, (last - OVERLAP if last else None, args.since)), default=None)
	# Punches at or before the last sent time are still new if they were not sent: a punch in
	# the same second, or the device clock moved back (by less than OVERLAP).
	window = sorted((r for r in records if not since or r.timestamp >= since), key=lambda r: r.timestamp)
	new = [r for r in window if punch_key(r) not in sent]
	if not new:
		return True

	lines = attlog_lines(new)
	for start in range(0, len(lines), CHUNK):
		status, text = post(args.site_url, serial, lines[start : start + CHUNK])
		if not accepted(status, text):
			log(f"Device {serial}: upload rejected, HTTP {status} {text.strip()[:80]}; retrying next run")
			return False
	last = max(last, window[-1].timestamp) if last else window[-1].timestamp
	save_state(state_path, last, window)
	log(f"Device {serial}: sent {len(new)} new punch(es), last at {last}")
	return True


def main():
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("device_ip")
	parser.add_argument("site_url")
	parser.add_argument(
		"--since", type=since_date, help="Never send punches before this year or date (YYYY, YYYY-MM or YYYY-MM-DD)"
	)
	parser.add_argument("--interval", type=int, default=120, help="Seconds between pulls (default 120)")
	parser.add_argument("--comm-key", type=int, default=0, help="Device Comm Key (default 0)")
	parser.add_argument("--state", help="State file (default: zk_pull_<device_ip>.json next to this script)")
	parser.add_argument("--once", action="store_true", help="Pull once and exit (for cron)")
	args = parser.parse_args()

	state_path = Path(args.state or Path(__file__).with_name(f"zk_pull_{args.device_ip}.json"))
	if args.site_url.startswith("http://"):
		log("Warning: plain HTTP; the device serial number (its only credential) is sent in clear text")
	while True:
		try:
			ok = sync(args, state_path)
		except Exception as e:  # device offline, network down, ...: try again next run
			log(f"Pull failed: {e!r}")
			ok = False
		if args.once:
			raise SystemExit(0 if ok else 1)
		time.sleep(args.interval)


if __name__ == "__main__":
	main()
