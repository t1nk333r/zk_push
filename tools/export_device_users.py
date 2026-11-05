"""Export users from a ZKTeco device (pull protocol, port 4370) to CSV.

Read-only. Use the output to fill "Attendance Device ID" on ERPNext Employees.
Usage: python export_device_users.py <device_ip> <output.csv>   (requires: pip install pyzk)
"""

import csv
import sys
from collections import Counter

from zk import ZK


def main(ip, out_path):
	conn = ZK(ip, port=4370, timeout=10, ommit_ping=True).connect()
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

	with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
		writer = csv.writer(f)
		writer.writerow(["device_user_id", "name", "privilege", "card", "punches", "last_punch", "erpnext_employee"])
		for u in sorted(users, key=lambda u: (len(u.user_id), u.user_id)):
			writer.writerow([
				u.user_id, u.name, "admin" if u.privilege == 14 else "user", u.card or "",
				punches.get(u.user_id, 0), last_punch.get(u.user_id, ""), "",
			])
	print(f"{len(users)} users written to {out_path}")


if __name__ == "__main__":
	main(sys.argv[1], sys.argv[2])
