"""Reference ZKTeco TCP client for the ESP32 agent: only what the firmware has to do.

Standard library only, no pyzk. Every step maps to a small fixed-size operation, so the firmware
can be a line-by-line port. Verified against an MB20-VL (ZMM510_TFT, Ver 6.60 May 8 2021).

  python zk_reference.py <device_ip> [--comm-key N] [--from-record K]
  python zk_reference.py <device_ip> [--comm-key N] --sync <site_url> [--state FILE] [--since YYYY-MM-DD]

Prints the device's serial number and record count, then the ATTLOG lines (the format the site's
/iclock/cdata expects) for records K.. (default: the last 5). Read-only.

Protocol, as used here (all integers little-endian):

  frame     u16 0x5050, u16 0x7D82, u32 length, then `length` bytes of packet
  packet    u16 command, u16 checksum, u16 session_id, u16 reply_id, payload
  checksum  16-bit one's-complement-style sum over the packet with checksum=0 (see checksum())
  connect   CMD_CONNECT -> ACK_OK, or ACK_UNAUTH -> CMD_AUTH(comm_key(key, session)) -> ACK_OK;
            the reply's session_id is used for the rest of the connection
  sizes     CMD_GET_FREE_SIZES -> 80+ bytes of i32; [8] = records, [16] = record capacity
  log       CMD_PREPARE_BUFFER(u8 1, i16 CMD_ATTLOG_RRQ, i32 0, i32 0) -> payload[1:5] = buffer size;
            the buffer is u32 data length + records of RECORD_SIZE bytes, oldest first
  read      CMD_READ_BUFFER(i32 start, i32 size) -> CMD_DATA(bytes), or CMD_PREPARE_DATA then
            CMD_DATA packets then ACK_OK. start=0 reads from the length header (so record 0 is
            read as start=0, size=4+RECORD_SIZE, dropping 4 bytes); any other start is an offset
            into the records, so record k (k >= 1) is at start = k * RECORD_SIZE. Reading only
            new records therefore costs a few hundred bytes, not the whole log.
  record    u16 uid, char[24] user_id, u8 verify, u32 time, u8 state, 8 bytes reserved (40 bytes)
  time      seconds encoded as ((((y-2000)*12 + m-1)*31 + d-1)*24 + h)*60 + min)*60 + s
  finish    CMD_FREE_DATA, CMD_EXIT
"""

import argparse
import socket
import struct
from datetime import datetime

CMD_CONNECT, CMD_EXIT, CMD_AUTH = 1000, 1001, 1102
CMD_OPTIONS_RRQ, CMD_GET_FREE_SIZES, CMD_ATTLOG_RRQ = 11, 50, 13
CMD_PREPARE_BUFFER, CMD_READ_BUFFER, CMD_FREE_DATA = 1503, 1504, 1502
CMD_PREPARE_DATA, CMD_DATA = 1500, 1501
ACK_OK, ACK_UNAUTH = 2000, 2005
FRAME_MAGIC = (0x5050, 0x7D82)
USHRT_MAX = 65535
RECORD_SIZE = 40


def checksum(packet: bytes) -> int:
	if len(packet) % 2:
		packet += b"\0"
	total = sum(struct.unpack(f"<{len(packet) // 2}H", packet))
	while total > USHRT_MAX:
		total -= USHRT_MAX
	total = ~total
	while total < 0:
		total += USHRT_MAX
	return total


def comm_key(key: int, session_id: int, ticks: int = 50) -> bytes:
	"""Scrambled Comm Key for CMD_AUTH (commpro.c MakeKey)."""
	k = 0
	for i in range(32):  # bit-reverse the key
		k = (k << 1) | ((key >> i) & 1)
	b = struct.pack("<I", (k + session_id) & 0xFFFFFFFF)
	b = bytes([b[0] ^ ord("Z"), b[1] ^ ord("K"), b[2] ^ ord("S"), b[3] ^ ord("O")])
	b = b[2:4] + b[0:2]  # swap the two 16-bit halves
	return bytes([b[0] ^ ticks, b[1] ^ ticks, ticks, b[3] ^ ticks])


def decode_time(value: int) -> datetime:
	value, second = divmod(value, 60)
	value, minute = divmod(value, 60)
	value, hour = divmod(value, 24)
	value, day = divmod(value, 31)
	year, month = divmod(value, 12)
	return datetime(year + 2000, month + 1, day + 1, hour, minute, second)


class Device:
	def __init__(self, ip: str, key: int = 0, port: int = 4370, timeout: float = 10):
		self.sock = socket.create_connection((ip, port), timeout=timeout)
		self.session_id, self.reply_id = 0, USHRT_MAX - 1
		try:
			code, payload = self.command(CMD_CONNECT)
			if code == ACK_UNAUTH:
				code, payload = self.command(CMD_AUTH, comm_key(key, self.session_id))
			if code != ACK_OK:
				raise ConnectionError(f"device refused the connection (code {code}; wrong Comm Key?)")
		except BaseException:
			self.sock.close()
			raise

	def _recv_exact(self, n: int) -> bytes:
		buf = b""
		while len(buf) < n:
			chunk = self.sock.recv(n - len(buf))
			if not chunk:
				raise ConnectionError("device closed the connection")
			buf += chunk
		return buf

	def _recv_packet(self) -> tuple[int, bytes]:
		magic1, magic2, length = struct.unpack("<HHI", self._recv_exact(8))
		if (magic1, magic2) != FRAME_MAGIC:
			raise ConnectionError("bad frame from device")
		packet = self._recv_exact(length)
		code, _checksum, self.session_id, self.reply_id = struct.unpack("<4H", packet[:8])
		return code, packet[8:]

	def command(self, command: int, payload: bytes = b"") -> tuple[int, bytes]:
		head = struct.pack("<4H", command, 0, self.session_id, self.reply_id) + payload
		self.reply_id = (self.reply_id + 1) % USHRT_MAX
		packet = struct.pack("<4H", command, checksum(head), self.session_id, self.reply_id) + payload
		self.sock.sendall(struct.pack("<HHI", *FRAME_MAGIC, len(packet)) + packet)
		return self._recv_packet()

	def serial_number(self) -> str:
		_code, payload = self.command(CMD_OPTIONS_RRQ, b"~SerialNumber\0")
		return payload.split(b"=", 1)[-1].split(b"\0")[0].decode()

	def record_count(self) -> tuple[int, int]:
		_code, payload = self.command(CMD_GET_FREE_SIZES)
		fields = struct.unpack("<20i", payload[:80])
		return fields[8], fields[16]  # records, capacity

	def prepare_log(self) -> int:
		"""Asks the device to build its log buffer (once per pull); returns 4 + records * RECORD_SIZE."""
		code, payload = self.command(CMD_PREPARE_BUFFER, struct.pack("<bhii", 1, CMD_ATTLOG_RRQ, 0, 0))
		if code != ACK_OK:
			raise ConnectionError(f"device cannot prepare the log (code {code})")
		return struct.unpack("<I", payload[1:5])[0]

	def free_log(self):
		self.command(CMD_FREE_DATA)

	def read_records(self, first: int, count: int) -> list[bytes]:
		"""Records first..first+count-1 (0-based, oldest first) of the prepared log, RECORD_SIZE bytes each."""
		data = b""
		while len(data) < count * RECORD_SIZE:  # 4000-byte chunks, as the firmware reads
			want = min(4000, count * RECORD_SIZE - len(data))
			start = first * RECORD_SIZE + len(data)
			if start == 0:  # start 0 means the u32 length header, not record 0
				data += self._read_chunk(0, 4 + RECORD_SIZE)[4:]
			else:
				data += self._read_chunk(start, want)
		return [data[i : i + RECORD_SIZE] for i in range(0, len(data), RECORD_SIZE)]

	def _read_chunk(self, start: int, size: int) -> bytes:
		code, payload = self.command(CMD_READ_BUFFER, struct.pack("<ii", start, size))
		if code == CMD_DATA:
			data = payload
		elif code == CMD_PREPARE_DATA:
			data = b""
			while True:
				code, payload = self._recv_packet()
				if code == CMD_DATA:
					data += payload
				elif code == ACK_OK:
					break
				else:
					raise ConnectionError(f"unexpected packet {code} while reading the log")
		else:
			raise ConnectionError(f"device cannot read the log (code {code})")
		if len(data) != size:
			raise ConnectionError(f"device returned {len(data)} of {size} bytes")
		return data

	def close(self):
		try:
			self.command(CMD_EXIT)
		finally:
			self.sock.close()


def attlog_line(record: bytes) -> str | None:
	"""One record as the site's ATTLOG line: PIN, time, state, verify, workcode. None if its date does
	not exist (a corrupt record: the time encoding allows day 31 in every month)."""
	_uid, user_id, verify, timestamp, state = struct.unpack("<H24sBIB", record[:32])
	pin = user_id.split(b"\0")[0].decode(errors="replace")
	try:
		when = decode_time(timestamp)
	except ValueError:
		return None
	return f"{pin}\t{when:%Y-%m-%d %H:%M:%S}\t{state}\t{verify}\t0"


def parse_record(record: bytes) -> tuple[str, int]:
	"""(pin, encoded time): the fingerprint the firmware stores for the last sent record."""
	_uid, user_id, _verify, timestamp, _state = struct.unpack("<H24sBIB", record[:32])
	return user_id.split(b"\0")[0].decode(errors="replace"), timestamp


def sync(device: Device, site: str, state_path: str, since: datetime, body_max: int = 8192) -> None:
	"""One pull, mirroring firmware/src/sync.cpp step by step (state in a JSON file instead of NVS)."""
	import json
	import os
	import urllib.error
	import urllib.parse
	import urllib.request

	serial = device.serial_number()
	records, _capacity = device.record_count()

	def request(query: dict, body: bytes | None = None) -> int:
		url = f"{site.rstrip('/')}/iclock/cdata?{urllib.parse.urlencode({'SN': serial, **query})}"
		req = urllib.request.Request(url, data=body, headers={"Content-Type": "text/plain"})
		try:
			with urllib.request.urlopen(req, timeout=30) as resp:
				return resp.status
		except urllib.error.HTTPError as e:
			return e.code

	def save(state: dict):
		tmp = state_path + ".tmp"
		with open(tmp, "w") as f:
			json.dump(state, f)
		os.replace(tmp, state_path)

	if request({"options": "all"}) != 200:
		raise SystemExit("the site rejected the heartbeat (is the ZK Device enabled?)")
	try:
		with open(state_path) as f:
			saved = json.load(f)
	except FileNotFoundError:
		saved = {"next": 0, "fp": None}

	buffer_size = device.prepare_log()
	try:
		in_buffer = (buffer_size - 4) // RECORD_SIZE  # a punch between count and prepare adds one
		if (buffer_size - 4) % RECORD_SIZE or not 0 <= in_buffer - records <= 100:
			raise SystemExit(f"unsupported log format ({buffer_size} bytes for {records} records)")
		records = in_buffer

		if saved["next"] > 0:  # locate(): the last sent record, possibly shifted down by up to 1000
			top = min(saved["next"], records)
			bottom = max(top - 1000, 0)
			found = None
			while top > bottom and found is None:
				n = min(100, top - bottom)
				for k, record in reversed(list(enumerate(device.read_records(top - n, n)))):
					if list(parse_record(record)) == saved["fp"]:
						found = top - n + k + 1
						break
				top -= n
			if found is None:
				print("log cleared or rewritten, starting from record 0")
				saved = {"next": 0, "fp": None}
				save(saved)
			elif found != saved["next"]:
				print(f"the device dropped {saved['next'] - found} old record(s)")
				saved["next"] = found
				save(saved)

		since_value = (((((since.year - 2000) * 12 + since.month - 1) * 31 + since.day - 1) * 24) * 60) * 60
		body, lines, pending = b"", 0, dict(saved)

		def flush():
			nonlocal body, lines, saved
			if body:
				status = request({"table": "ATTLOG"}, body)
				if status != 200:
					raise SystemExit(f"upload rejected (HTTP {status}), retrying next run")
				print(f"sent {lines} punch(es)")
			if pending["next"] != saved["next"]:
				saved = dict(pending)
				save(saved)
			body, lines = b"", 0

		for index in range(saved["next"], records, 100):
			for k, record in enumerate(device.read_records(index, min(100, records - index))):
				pin, timestamp = parse_record(record)
				line = attlog_line(record) if pin and timestamp >= since_value else None
				if line:
					encoded = (line + "\n").encode()
					if len(body) + len(encoded) > body_max:
						flush()
					body += encoded
					lines += 1
				pending = {"next": index + k + 1, "fp": [pin, timestamp]}
		flush()
		print(f"done: next record {saved['next']} of {records}")
	finally:
		device.free_log()


def main():
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("device_ip")
	parser.add_argument("--comm-key", type=int, default=0)
	parser.add_argument("--from-record", type=int, help="First record to print (default: the last 5)")
	parser.add_argument("--sync", metavar="SITE_URL", help="Run one firmware-style pull to this site instead")
	parser.add_argument("--state", default="zk_reference_state.json", help="State file for --sync")
	parser.add_argument("--since", default="2000-01-01", help="For --sync: never send punches before (YYYY-MM-DD)")
	args = parser.parse_args()

	device = Device(args.device_ip, args.comm_key)
	try:
		if args.sync:
			sync(device, args.sync, args.state, datetime.strptime(args.since, "%Y-%m-%d"))
			return
		records, capacity = device.record_count()
		print(f"serial {device.serial_number()}, {records} of {capacity} records")
		records = (device.prepare_log() - 4) // RECORD_SIZE
		try:
			first = args.from_record if args.from_record is not None else max(records - 5, 0)
			for record in device.read_records(first, records - first):
				print(attlog_line(record) or "(record with an impossible date)")
		finally:
			device.free_log()
	finally:
		device.close()


if __name__ == "__main__":
	main()
