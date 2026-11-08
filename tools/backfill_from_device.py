"""Copy a device's attendance log into an ERPNext site running zk_push.

Reads the log from the device over the LAN (pull protocol, TCP 4370; read-only, nothing on the
device is changed) and posts it to the site's /iclock/cdata endpoint exactly as the device's own
push would. Punches the site already has are ignored, so it is safe to run more than once.

Usage (requires: pip install pyzk):
  python backfill_from_device.py <device_ip> <site_url> [--since YYYY-MM-DD] [--comm-key N] [--dry-run]

The device must already exist and be enabled under ZK Device on the site (its serial number is
read from the device).
"""

import argparse
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from zk import ZK

CHUNK = 2000


def read_device(ip, comm_key):
	conn = ZK(ip, port=4370, timeout=30, password=comm_key, ommit_ping=True).connect()
	try:
		serial = conn.get_serialnumber()
		records = conn.get_attendance()
	finally:
		conn.disconnect()
	return serial, records


def attlog_lines(records):
	# ATTLOG line: PIN, time, state key (pyzk "punch"), verify type (pyzk "status"), workcode
	return [f"{r.user_id}\t{r.timestamp:%Y-%m-%d %H:%M:%S}\t{r.punch}\t{r.status}\t0\n" for r in records]


def post(site, serial, lines):
	# No Stamp: the site would store it as the device's ATTLOGStamp and the device would resend its log.
	query = urllib.parse.urlencode({"SN": serial, "table": "ATTLOG"})
	req = urllib.request.Request(
		f"{site.rstrip('/')}/iclock/cdata?{query}",
		data="".join(lines).encode(),
		method="POST",
		headers={"Content-Type": "text/plain"},
	)
	try:
		with urllib.request.urlopen(req, timeout=120) as resp:
			return resp.status, resp.read().decode()
	except urllib.error.HTTPError as e:
		return e.code, e.read().decode(errors="replace")


def main():
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("device_ip")
	parser.add_argument("site_url")
	parser.add_argument("--since", help="Only punches on or after this date (YYYY-MM-DD)")
	parser.add_argument("--comm-key", type=int, default=0, help="Device Comm Key (default 0)")
	parser.add_argument("--dry-run", action="store_true", help="Read and summarise, do not post")
	args = parser.parse_args()

	serial, records = read_device(args.device_ip, args.comm_key)
	since = datetime.strptime(args.since, "%Y-%m-%d") if args.since else None
	records = sorted((r for r in records if not since or r.timestamp >= since), key=lambda r: r.timestamp)
	if not records:
		print(f"Device {serial}: no punches to send")
		return

	print(
		f"Device {serial}: {len(records)} punches from {records[0].timestamp} to {records[-1].timestamp}, "
		f"{len({r.user_id for r in records})} users"
	)
	if args.dry_run:
		return

	lines = attlog_lines(records)
	for start in range(0, len(lines), CHUNK):
		status, text = post(args.site_url, serial, lines[start : start + CHUNK])
		print(f"  punches {start + 1}-{start + len(lines[start:start + CHUNK])}: HTTP {status} {text.strip()[:80]}")
		if status != 200:
			raise SystemExit("Stopped: the site rejected the upload (is the ZK Device enabled?)")
	print("Done. The site turns them into checkins in the background (about a minute per 10,000).")


if __name__ == "__main__":
	main()
