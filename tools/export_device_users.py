"""Export users from a ZKTeco device (pull protocol, port 4370) to CSV.

Read-only. Use the output to fill "Attendance Device ID" on ERPNext Employees.
Usage: python export_device_users.py <device_ip> [output.csv] [--comm-key N]   (requires: pip install pyzk)
Without output.csv the CSV is written to standard output.
"""

import argparse
import csv
import sys
from collections import Counter

from zk import ZK


def main():
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("device_ip")
	parser.add_argument("output", nargs="?", help="CSV file (default: standard output)")
	parser.add_argument("--comm-key", type=int, default=0, help="Device Comm Key (default 0)")
	args = parser.parse_args()

	conn = ZK(args.device_ip, port=4370, timeout=10, password=args.comm_key, ommit_ping=True).connect()
	try:
		users = conn.get_users()
		last_punch = {}
		punches = Counter()
		for att in conn.get_attendance():
			punches[att.user_id] += 1
			if att.user_id not in last_punch or att.timestamp > last_punch[att.user_id]:
				last_punch[att.user_id] = att.timestamp
	finally:
		conn.disconnect()

	out = open(args.output, "w", newline="", encoding="utf-8-sig") if args.output else sys.stdout
	try:
		writer = csv.writer(out)
		writer.writerow(["device_user_id", "name", "privilege", "card", "punches", "last_punch", "erpnext_employee"])
		for u in sorted(users, key=lambda u: (len(u.user_id), u.user_id)):
			writer.writerow([
				u.user_id, _cell(u.name), "admin" if u.privilege == 14 else "user", u.card or "",
				punches.get(u.user_id, 0), last_punch.get(u.user_id, ""), "",
			])
	finally:
		if args.output:
			out.close()
	print(f"{len(users)} users written to {args.output or 'standard output'}", file=sys.stderr)


def _cell(value):
	# Names are typed on the device; stop spreadsheets from running one that starts like a formula.
	return "'" + value if value[:1] in ("=", "+", "-", "@") else value


if __name__ == "__main__":
	main()
