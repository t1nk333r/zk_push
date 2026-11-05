"""Simulate a ZKTeco ADMS push device against an ERPNext site running zk_push.

Stdlib only. Lets you test the server end to end before touching the real device.

Examples:
  python simulate_device.py http://localhost:8000 --sn TEST0000001
  python simulate_device.py https://erp.example.com --sn ABCD123456789 --pin 9 --time "2026-11-05 08:00:00"
"""

import argparse
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime


def call(base, path, sn, method="GET", body=None, **params):
	query = urllib.parse.urlencode({"SN": sn, **params})
	url = f"{base.rstrip('/')}/iclock/{path}?{query}"
	data = body.encode() if body is not None else None
	req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "text/plain"})
	try:
		with urllib.request.urlopen(req, timeout=30) as resp:
			status, text = resp.status, resp.read().decode()
	except urllib.error.HTTPError as e:
		status, text = e.code, e.read().decode(errors="replace")
	print(f"--> {method} /iclock/{path}?{query}")
	if body:
		print("    " + body.strip().replace("\n", "\n    "))
	print(f"<-- {status}\n    " + text.strip().replace("\n", "\n    ")[:800] + "\n")
	return status, text


def main():
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("base_url", help="Site URL, e.g. https://erp.example.com")
	parser.add_argument("--sn", default="TEST0000001", help="Device serial number")
	parser.add_argument("--pin", action="append", help="Device user ID (repeatable). Default: 1")
	parser.add_argument("--time", default=None, help='Punch time "YYYY-MM-DD HH:MM:SS". Default: now')
	parser.add_argument("--status", type=int, default=0, help="Device status (0 in, 1 out)")
	args = parser.parse_args()

	pins = args.pin or ["1"]
	time = args.time or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

	call(args.base_url, "cdata", args.sn, options="all", pushver="2.4.1", language="69")
	body = "".join(f"{pin}\t{time}\t{args.status}\t1\t0\t0\t0\n" for pin in pins)
	call(args.base_url, "cdata", args.sn, method="POST", body=body, table="ATTLOG", Stamp="9999")
	call(args.base_url, "getrequest", args.sn, INFO="Ver 6.60 May  8 2021,10,10,100,192.168.1.201,10,7,0,4")


if __name__ == "__main__":
	main()
