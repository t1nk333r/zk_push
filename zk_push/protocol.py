"""ZKTeco ADMS / iclock push protocol helpers.

Pure functions only (no frappe imports) so they can be unit-tested anywhere.

Protocol summary (attendance push, "pushver 2.x" firmware such as ZMM510):

    GET  /iclock/cdata?SN=<sn>&options=all&pushver=...   -> handshake, reply with options
    POST /iclock/cdata?SN=<sn>&table=ATTLOG&Stamp=<n>     -> body: one punch per line
    POST /iclock/cdata?SN=<sn>&table=OPERLOG              -> body: "USER PIN=..\tName=.." lines (others skipped)
    GET  /iclock/getrequest?SN=<sn>[&INFO=...]            -> poll; reply "OK" or "C:<id>:<command>"
    POST /iclock/devicecmd?SN=<sn>                        -> command results "ID=..&Return=..&CMD=..", reply "OK"

ATTLOG line (tab separated):
    PIN  YYYY-MM-DD HH:MM:SS  status  verify  workcode  reserved...
"""

import html
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
MAX_USER_NAME = 140
PARTIAL_ENTITY_RE = re.compile(r"&[#\w]*$")
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


def safe_text(value: str, limit: int | None = None) -> str:
	"""Device-supplied text stored with db.set_value / bulk_insert, which skip Frappe's sanitizer.
	Read-only Data and Small Text fields are shown as HTML, so escape it; then cut to `limit`
	characters without leaving half an entity (every "&" is escaped, so a trailing "&..." is one)."""
	text = html.escape(value)
	if limit is not None and len(text) > limit:
		text = PARTIAL_ENTITY_RE.sub("", text[:limit])
	return text


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
			workcode=safe_text(rest[2], MAX_WORKCODE) if len(rest) > 2 else "",
			raw=safe_text(raw[:500]),
		)
	return None


@dataclass(frozen=True)
class DeviceUser:
	pin: str
	name: str
	is_admin: bool
	card: str


def parse_operlog_users(body: str) -> list[DeviceUser]:
	"""The USER lines of an OPERLOG POST body ("USER PIN=1\\tName=Esra\\tPri=14\\tCard=..."); a push
	device sends them when users change, the pull agents send the whole list. Other lines (OPLOG,
	FP, ...) are skipped. A pin listed twice keeps its last line."""
	users = {}
	for line in LINE_SPLIT_RE.split(body or ""):
		if not line.startswith("USER "):
			continue
		fields = dict(part.split("=", 1) for part in line[5:].split("\t") if "=" in part)
		pin = fields.get("PIN", "").strip()
		if not PIN_RE.fullmatch(pin):
			continue
		users[pin] = DeviceUser(
			pin=pin,
			name=safe_text(fields.get("Name", "").strip(), MAX_USER_NAME),
			is_admin=fields.get("Pri", "").strip() == "14",  # 14: device administrator, 0: user
			card=safe_text(fields.get("Card", "").strip(), MAX_USER_NAME),
		)
	return list(users.values())


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


# Common push commands, by the action name the device form uses. Those without values:
SIMPLE_COMMANDS = {
	"info": "INFO",  # report device info (firmware, user / punch counts)
	"check": "CHECK",  # re-read the server options and upload new data
	"log": "LOG",  # upload new punches now
	"reboot": "REBOOT",
	"open_door": "AC_UNLOCK",  # access-control models: release the door lock
	"clear_log": "CLEAR LOG",  # delete every punch stored on the device
}
# Actions that change or delete data on the device, or open a door.
ADMIN_ACTIONS = ("set_user", "delete_user", "enroll_fingerprint", "open_door", "clear_log")
DEVICE_PIN_RE = re.compile(r"[A-Za-z0-9]{1,24}")
DIGITS_RE = re.compile(r"[0-9]{0,10}")
DEVICE_NAME_MAX = 24
ADMIN_PRIVILEGE = 14


def device_command(action: str, **values) -> str:
	"""The command line for a device-form action, built from checked values: tabs separate the
	fields of a command and line breaks separate commands, so neither may come from a value.
	Raises ValueError for an unknown action or a bad value."""

	def pin():
		value = str(values.get("pin") or "").strip()
		if not DEVICE_PIN_RE.fullmatch(value):
			raise ValueError("Device user ID: 1 to 24 letters or digits")
		return value

	def digits(key, label, limit=10):
		value = str(values.get(key) or "").strip()
		if not DIGITS_RE.fullmatch(value) or len(value) > limit:
			raise ValueError(f"{label}: digits only, at most {limit}")
		return value

	def stamp(key):
		value = values.get(key)
		try:
			value = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
		except ValueError:
			raise ValueError("Give both a start and an end date and time") from None
		return value.strftime(TIME_FORMAT)

	if action in SIMPLE_COMMANDS:
		return SIMPLE_COMMANDS[action]
	if action == "reupload":
		start, end = stamp("from_time"), stamp("to_time")
		if end < start:
			raise ValueError("The end is before the start")
		return f"DATA QUERY ATTLOG StartTime={start}\tEndTime={end}"
	if action == "set_user":
		name = str(values.get("name") or "").strip()
		if not name or len(name) > DEVICE_NAME_MAX or any(c in name for c in "\t\r\n"):
			raise ValueError(f"Name: 1 to {DEVICE_NAME_MAX} characters")
		privilege = ADMIN_PRIVILEGE if str(values.get("admin") or "0") in ("1", "True", "true") else 0
		password = digits("password", "Password", 8)
		card = digits("card", "Card number")
		return (
			f"DATA UPDATE USERINFO PIN={pin()}\tName={name}\tPri={privilege}\tPasswd={password}"
			f"\tCard={card}\tGrp=1\tTZ=0000000100000000\tVerify=0"
		)
	if action == "delete_user":
		return f"DATA DELETE USERINFO PIN={pin()}"
	if action == "enroll_fingerprint":
		finger = digits("finger", "Finger", 1) or "0"
		return f"ENROLL_FP PIN={pin()}\tFID={finger}\tRETRY=3\tOVERWRITE=0"
	raise ValueError(f"Unknown device command: {action}")


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
