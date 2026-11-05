"""ZKTeco ADMS / iclock push protocol helpers.

Pure functions only (no frappe imports) so they can be unit-tested anywhere.

Protocol summary (attendance push, "pushver 2.x" firmware such as ZMM510):

    GET  /iclock/cdata?SN=<sn>&options=all&pushver=...   -> handshake, reply with options
    POST /iclock/cdata?SN=<sn>&table=ATTLOG&Stamp=<n>     -> body: one punch per line
    GET  /iclock/getrequest?SN=<sn>[&INFO=...]            -> poll; reply "OK" or "C:<id>:<command>"
    POST /iclock/devicecmd?SN=<sn>                        -> command results "ID=..&Return=..&CMD=..", reply "OK"

ATTLOG line (tab separated):
    PIN  YYYY-MM-DD HH:MM:SS  status  verify  workcode  reserved...
"""

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qsl

SN_RE = re.compile(r"^[A-Za-z0-9]{6,32}$")
PIN_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
# Fallback for firmwares that send spaces instead of tabs.
LOOSE_LINE_RE = re.compile(
	r"^\s*(?P<pin>\S+)\s+(?P<time>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})(?P<rest>.*)$"
)

# Device "status" (state key pressed on the terminal) -> ERPNext log type.
# 0 Check-In, 1 Check-Out, 2 Break-Out, 3 Break-In, 4 OT-In, 5 OT-Out
STATUS_LOG_TYPE = {0: "IN", 1: "OUT", 2: "OUT", 3: "IN", 4: "IN", 5: "OUT"}

LOG_TYPE_FROM_DEVICE = "From Device Status"
LOG_TYPE_BLANK = "Leave Blank"


@dataclass(frozen=True)
class Punch:
	pin: str
	time: datetime
	status: int | None
	verify: int | None
	workcode: str
	raw: str


def is_valid_sn(sn: str | None) -> bool:
	return bool(sn) and bool(SN_RE.match(sn))


def _to_int(value: str | None) -> int | None:
	try:
		return int(value)
	except (TypeError, ValueError):
		return None


def parse_attlog_line(line: str) -> Punch | None:
	"""Parse a single ATTLOG line. Returns None if the line is not a valid punch."""
	raw = line.strip("\r\n")
	if not raw.strip():
		return None

	parts = raw.split("\t")
	if len(parts) >= 2:
		pin, time_str, rest = parts[0].strip(), parts[1].strip(), [p.strip() for p in parts[2:]]
	else:
		match = LOOSE_LINE_RE.match(raw)
		if not match:
			return None
		pin, time_str, rest = match["pin"], match["time"], match["rest"].split()

	if not PIN_RE.match(pin):
		return None
	try:
		time = datetime.strptime(re.sub(r"\s+", " ", time_str), TIME_FORMAT)
	except ValueError:
		return None

	return Punch(
		pin=pin,
		time=time,
		status=_to_int(rest[0]) if len(rest) > 0 else None,
		verify=_to_int(rest[1]) if len(rest) > 1 else None,
		workcode=rest[2] if len(rest) > 2 else "",
		raw=raw[:500],
	)


def parse_attlog(body: str) -> tuple[list[Punch], list[str]]:
	"""Parse an ATTLOG POST body. Returns (punches, invalid_lines)."""
	punches, invalid = [], []
	for line in (body or "").splitlines():
		if not line.strip():
			continue
		punch = parse_attlog_line(line)
		if punch:
			punches.append(punch)
		else:
			invalid.append(line[:500])
	return punches, invalid


def punch_name(sn: str, pin: str, time: datetime) -> str:
	"""Deterministic document name, so a re-sent punch is ignored instead of duplicated."""
	return f"{sn}-{pin}-{time:%Y%m%d%H%M%S}"


def map_log_type(status: int | None, mode: str | None) -> str | None:
	if mode != LOG_TYPE_FROM_DEVICE or status is None:
		return None
	return STATUS_LOG_TYPE.get(status)


def build_handshake(sn: str, att_stamp: str | None = None, delay: int = 10, error_delay: int = 30) -> str:
	"""Options returned to the device on GET /iclock/cdata.

	Only attendance logs are requested (TransFlag), in real time.
	ATTLOGStamp tells the device which logs we already have; "None" means send everything.
	"""
	lines = [
		f"GET OPTION FROM: {sn}",
		f"ATTLOGStamp={att_stamp or 'None'}",
		"OPERLOGStamp=9999",
		"ATTPHOTOStamp=None",
		f"ErrorDelay={error_delay}",
		f"Delay={delay}",
		"TransTimes=00:00;14:05",
		"TransInterval=1",
		"TransFlag=TransData AttLog",
		"Realtime=1",
		"Encrypt=None",
	]
	return "\n".join(lines) + "\n"


def format_command(command_id, command: str) -> str:
	"""Command line returned from getrequest, e.g. 'C:12:REBOOT'."""
	return f"C:{command_id}:{command}\n"


def parse_devicecmd(body: str) -> list[dict]:
	"""Parse a devicecmd POST body: one 'ID=12&Return=0&CMD=REBOOT' per command.

	Some commands (INFO) append extra key=value lines after the result line; they are
	attached to the preceding result as 'extra'.
	"""
	results = []
	for line in (body or "").splitlines():
		line = line.strip()
		if not line:
			continue
		if line.startswith("ID="):
			values = dict(parse_qsl(line, keep_blank_values=True))
			results.append(
				{
					"id": values.get("ID", "").strip(),
					"return": _to_int(values.get("Return")),
					"cmd": values.get("CMD", "").strip(),
					"extra": [],
				}
			)
		elif results:
			results[-1]["extra"].append(line[:500])
	return results


def parse_endpoint(path: str) -> str | None:
	"""'iclock/cdata.aspx' -> 'cdata'. Returns None if the path is not an iclock path."""
	parts = [p for p in (path or "").strip("/ ").split("/") if p]
	if len(parts) != 2 or parts[0].lower() != "iclock":
		return None
	endpoint = parts[1].lower()
	if endpoint.endswith(".aspx"):
		endpoint = endpoint[: -len(".aspx")]
	return endpoint or None
