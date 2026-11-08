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

SN_RE = re.compile(r"[A-Za-z0-9]{6,32}")
PIN_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
# ATTLOGStamp is echoed into the handshake, so only plain numbers are accepted.
STAMP_RE = re.compile(r"[0-9]{1,20}")
# ASCII only and small enough for an Int column (int() alone accepts any Unicode digit).
INT_RE = re.compile(r"-?[0-9]{1,9}")
MAX_WORKCODE = 140
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
# Device bodies are split on CR/LF only; str.splitlines() also splits on \x0b, \x1c, \x85, ...
LINE_SPLIT_RE = re.compile(r"\r\n|\r|\n")
# Fallback for firmwares that send spaces instead of tabs.
LOOSE_LINE_RE = re.compile(
	r"^\s*(?P<pin>\S+)\s+(?P<time>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})(?P<rest>.*)$", re.ASCII
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
	return bool(sn) and bool(SN_RE.fullmatch(sn))


def is_valid_stamp(stamp: str | None) -> bool:
	return bool(stamp) and bool(STAMP_RE.fullmatch(stamp))


def _to_int(value: str | None) -> int | None:
	if value is None or not INT_RE.fullmatch(value.strip()):
		return None
	return int(value)


def _split_fields(raw: str) -> list[tuple[str, str, list[str]]]:
	"""Candidate (pin, time, rest) splits: tab separated first, then the space-separated fallback.

	The fallback also covers lines that mix the two, e.g. 'PIN TIME\\tstatus\\tverify'.
	"""
	candidates = []
	parts = raw.split("\t")
	if len(parts) >= 2:
		candidates.append((parts[0].strip(), parts[1].strip(), [p.strip() for p in parts[2:]]))
	match = LOOSE_LINE_RE.match(raw)
	if match:
		candidates.append((match["pin"], match["time"], match["rest"].split()))
	return candidates


def parse_attlog_line(line: str) -> Punch | None:
	"""Parse a single ATTLOG line. Returns None if the line is not a valid punch."""
	raw = line.strip("\r\n")
	if not raw.strip():
		return None

	for pin, time_str, rest in _split_fields(raw):
		if not PIN_RE.fullmatch(pin):
			continue
		try:
			time = datetime.strptime(re.sub(r"\s+", " ", time_str), TIME_FORMAT)
		except ValueError:
			continue

		return Punch(
			pin=pin,
			time=time,
			status=_to_int(rest[0]) if len(rest) > 0 else None,
			verify=_to_int(rest[1]) if len(rest) > 1 else None,
			workcode=rest[2][:MAX_WORKCODE] if len(rest) > 2 else "",
			raw=raw[:500],
		)
	return None


def parse_attlog(body: str) -> tuple[list[Punch], list[str]]:
	"""Parse an ATTLOG POST body. Returns (punches, invalid_lines)."""
	punches, invalid = [], []
	for line in LINE_SPLIT_RE.split(body or ""):
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
		f"ATTLOGStamp={att_stamp if is_valid_stamp(att_stamp) else 'None'}",
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
	if "\n" in command or "\r" in command:
		raise ValueError("command must be a single line")
	return f"C:{command_id}:{command}\n"


def parse_devicecmd(body: str) -> list[dict]:
	"""Parse a devicecmd POST body: one 'ID=12&Return=0&CMD=REBOOT' per command.

	Some commands (INFO) append extra key=value lines after the result line; they are
	attached to the preceding result as 'extra'.
	"""
	results = []
	for line in LINE_SPLIT_RE.split(body or ""):
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
