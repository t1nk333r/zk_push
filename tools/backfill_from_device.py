"""Copy a device's attendance log into an ERPNext site running zk_push.

Reads the log from the device over the LAN (pull protocol, TCP 4370; read-only, nothing on the
device is changed) and posts it to the site's /iclock/cdata endpoint exactly as the device's own
push would. Punches the site already has are ignored, so it is safe to run more than once.

Usage (requires: pip install pyzk):
  python backfill_from_device.py <device_ip> <site_url> [--since 2026 | 2026-03-15] [--comm-key N] [--dry-run]

The Comm Key can also come from the ZK_COMM_KEY environment variable, which keeps it out of process
listings.

The device must already exist and be enabled under ZK Device on the site (its serial number is
read from the device).
"""

import argparse
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from zk import ZK

CHUNK = 2000
READ_ATTEMPTS = 3

_decode_time = ZK._ZK__decode_time


def _decode_time_or_none(self, t):
	# The device packs dates with 31 days in every month, so a corrupt record can decode to 31
	# February. pyzk raises on it and the whole log would never be read; skip that record instead.
	try:
		return _decode_time(self, t)
	except ValueError:
		return None


ZK._ZK__decode_time = _decode_time_or_none


def get_attendance(conn):
	"""The device's attendance records, without records whose date cannot exist.

	pyzk works out the record format from a record count it reads before the log itself. A punch in
	between makes it parse the log in the wrong format, so the count is read again afterwards and
	the read repeated if it changed."""
	for _attempt in range(READ_ATTEMPTS):
		records = conn.get_attendance()
		count = conn.records
		conn.read_sizes()
		if conn.records == count:
			break
	else:
		raise RuntimeError("the device log kept changing while it was read; try again")
	good = [r for r in records if r.timestamp]
	if len(good) < len(records):
		print(f"Skipped {len(records) - len(good)} record(s) with an impossible date", file=sys.stderr, flush=True)
	return good


def comm_key_arg(parser):
	"""--comm-key, defaulting to the ZK_COMM_KEY environment variable (not visible in process lists)."""
	parser.add_argument(
		"--comm-key",
		type=int,
		default=int(os.environ.get("ZK_COMM_KEY") or 0),
		help="Device Comm Key (default: ZK_COMM_KEY environment variable, else 0)",
	)


def read_device(ip, comm_key):
	"""(serial number, attendance records, users) in one connection."""
	conn = ZK(ip, port=4370, timeout=30, password=comm_key, ommit_ping=True).connect()
	try:
		serial = conn.get_serialnumber()
		records = get_attendance(conn)
		users = conn.get_users()
	finally:
		conn.disconnect()
	return serial, records, users


def since_date(value):
	"""argparse type for --since: a year (2026), a month (2026-03) or a day (2026-03-15)."""
	for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
		try:
			return datetime.strptime(value, fmt)
		except ValueError:
			pass
	raise argparse.ArgumentTypeError(f"expected YYYY, YYYY-MM or YYYY-MM-DD, got {value!r}")


def attlog_lines(records):
	# ATTLOG line: PIN, time, state key (pyzk "punch"), verify type (pyzk "status"), workcode
	return [f"{r.user_id}\t{r.timestamp:%Y-%m-%d %H:%M:%S}\t{r.punch}\t{r.status}\t0\n" for r in records]


def user_lines(users):
	# OPERLOG USER lines, as a push device sends them: the site keeps the names typed on the device
	# so device user IDs are easy to map. Skips users whose id or name would split the line.
	return [
		f"USER PIN={u.user_id}\tName={u.name}\tPri={u.privilege}\tCard={u.card or ''}\n"
		for u in users
		if u.user_id and not any(ord(c) < 0x20 or c == "\x7f" for c in u.user_id + u.name)
	]


def post_users(site, serial, users):
	"""Send the device's users to the site. Returns (HTTP status, response text)."""
	lines = user_lines(users)
	if not lines:
		return 200, "OK"
	return cdata_request(site, {"SN": serial, "table": "OPERLOG"}, "".join(lines).encode())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
	# urllib would follow a redirect of the POST as a GET without the punches; the site answers that
	# GET with its handshake and HTTP 200, so the punches would look sent. Fail instead.
	def redirect_request(self, req, fp, code, msg, headers, newurl):
		return None


_opener = urllib.request.build_opener(_NoRedirect)


def cdata_request(site, params, body=None, timeout=120):
	"""GET or POST the site's /iclock/cdata. Returns (HTTP status, response text)."""
	req = urllib.request.Request(
		f"{site.rstrip('/')}/iclock/cdata?{urllib.parse.urlencode(params)}",
		data=body,
		method="POST" if body is not None else "GET",
		headers={"Content-Type": "text/plain"},
	)
	try:
		with _opener.open(req, timeout=timeout) as resp:
			return resp.status, resp.read().decode(errors="replace")
	except urllib.error.HTTPError as e:
		if 300 <= e.code < 400:
			return e.code, f"redirected to {e.headers.get('Location')}; use that address as the site URL"
		return e.code, e.read().decode(errors="replace")


def post(site, serial, lines):
	# No Stamp: the site would store it as the device's ATTLOGStamp and the device would resend its log.
	return cdata_request(site, {"SN": serial, "table": "ATTLOG"}, "".join(lines).encode())


def accepted(status, text):
	"""The site stored the upload: HTTP 200 and its 'OK: <n>' answer (anything else is not a cdata reply)."""
	return status == 200 and text.startswith("OK")


def main():
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("device_ip")
	parser.add_argument("site_url")
	parser.add_argument(
		"--since", type=since_date, help="Only punches on or after this year or date (YYYY, YYYY-MM or YYYY-MM-DD)"
	)
	comm_key_arg(parser)
	parser.add_argument("--dry-run", action="store_true", help="Read and summarise, do not post")
	args = parser.parse_args()

	serial, records, users = read_device(args.device_ip, args.comm_key)
	if not args.dry_run:
		status, text = post_users(args.site_url, serial, users)
		print(f"Device {serial}: {len(users)} users: HTTP {status} {text.strip()[:80]}")
	since = args.since
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
		if not accepted(status, text):
			raise SystemExit("Stopped: the site rejected the upload (is the ZK Device enabled?)")
	print("Done. The site turns them into checkins in the background (about a minute per 10,000).")


if __name__ == "__main__":
	main()
