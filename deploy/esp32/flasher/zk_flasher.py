# Copyright (c) 2026 Abdulrahman Aloqayli
# SPDX-License-Identifier: MIT

"""Setup tool for the ESP32 pull agent: flash the firmware and the board's settings from a window.

Asks for the site, the Wi-Fi network (scanned from this computer, with the password filled in when
this computer has it saved), the attendance device and the first punch date. It fetches the site's
root certificate(s) itself. The settings go into the board's "zkcfg" partition, so the same firmware
image (zk-agent.bin, built by CI) serves every site.

  Install firmware + settings: a new board, or a firmware update. Keeps the upload position.
  Write settings only:         a board already running this firmware (new Wi-Fi password, ...).
  Send all punches again:      also clears the upload position, so everything since the date is sent
                               again (the site ignores punches it already has). Pointing a board at
                               another site does this by itself.

The app updates itself from the GitHub releases tagged esp32-setup-<VERSION> (Check for updates; it
also looks when it starts).

Usage (Python 3.13+; tkinter ships with Python on Windows, on Linux install python3-tk / tk):
  pip install -r requirements.txt
  python zk_flasher.py [zk-agent.bin]
"""

import contextlib
import hashlib
import io
import json
import os
import queue
import re
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

# The setup tool's release: GitHub release esp32-setup-<VERSION>. Bump it, update RELEASE.md and
# push the tag to publish one (CI checks that they match).
VERSION = 2
COPYRIGHT = "Copyright (c) 2026 Abdulrahman Aloqayli"
REPO = "t1nk333r/zk_push"
CHECKSUMS = "zk-agent-setup-SHA256SUMS"
# Must match SETTINGS_VERSION in firmware/src/settings.cpp.
SETTINGS_VERSION = 1
NAMESPACE = "zkcfg"
CHIP = "esp32"
BAUD = "460800"
TABLE_OFFSET, TABLE_SIZE = 0x8000, 0xC00
# USB-serial chips on ESP32 boards: Silicon Labs CP210x, WCH CH34x, FTDI, Espressif native USB.
USB_SERIAL_VIDS = {0x10C4, 0x1A86, 0x0403, 0x303A}
# Where Windows users get each chip's driver when Windows Update did not install it.
USB_DRIVERS = {
	"10C4": "the CP210x driver from https://www.silabs.com/developer-tools/usb-to-uart-bridge-vcp-drivers",
	"1A86": "the CH340 driver from https://www.wch-ic.com/downloads/CH341SER_EXE.html",
	"0403": "the FTDI VCP driver from https://ftdichip.com/drivers/vcp-drivers/",
	"303A": "the latest Windows updates (Windows has the driver for this board)",
}

# Let's Encrypt chains end at one of these; the site may move between them without notice, and a
# board without its root needs a USB visit, so a Let's Encrypt site gets all four. Pinned by SHA-256.
LETS_ENCRYPT_ROOTS = {
	"https://letsencrypt.org/certs/isrgrootx1.pem": "96bcec06264976f37460779acf28c5a7cfe8a3c0aae11a8ffcee05c0bddf08c6",
	"https://letsencrypt.org/certs/isrg-root-x2.pem": "69729b8e15a86efc177a57afb7171dfc64add28c2fca8cf1507e34453ccb1470",
	"https://letsencrypt.org/certs/gen-y/root-ye.pem": "e14ffcad5b0025731006caa43a121a22d8e9700f4fb9cf852f02a708aa5d5666",
	"https://letsencrypt.org/certs/gen-y/root-yr.pem": "e57b7e6f150c419102e8d5c055729ff967b9d1a829bf00cec89ca604ebf4a86f",
}
LETS_ENCRYPT_ORGS = {"Internet Security Research Group", "ISRG"}


@dataclass
class Settings:
	site_url: str
	root_ca: str  # PEM, one or more certificates
	wifi_ssid: str
	wifi_password: str
	device_host: str
	device_port: int = 4370
	comm_key: int = 0
	since: date = date(date.today().year, 1, 1)
	interval_seconds: int = 120


@dataclass
class Partition:
	offset: int
	size: int

	@property
	def end(self):
		return self.offset + self.size


def validate(s):
	"""Returns a list of problems, empty when the settings can be written."""
	problems = []
	url = urlparse(s.site_url)
	if url.scheme != "https" or not url.hostname:
		problems.append("Site address must start with https:// (over http the device serial, its only credential, travels in clear text).")
	if "BEGIN CERTIFICATE" not in s.root_ca:
		problems.append("No root certificate: press Get certificate.")
	if not 1 <= len(s.wifi_ssid.encode()) <= 32:
		problems.append("Wi-Fi network name must be 1 to 32 bytes.")
	if s.wifi_password and not (8 <= len(s.wifi_password) <= 63 or re.fullmatch(r"[0-9a-fA-F]{64}", s.wifi_password)):
		problems.append("Wi-Fi password must be 8 to 63 characters (or empty for an open network).")
	if not s.device_host or not re.fullmatch(r"[A-Za-z0-9.-]+", s.device_host):
		problems.append("Device IP is missing or not an address.")
	if not 1 <= s.device_port <= 65535:
		problems.append("Device port must be 1 to 65535.")
	if not 0 <= s.comm_key <= 0xFFFFFFFF:
		problems.append("Comm Key must be a number (0 if none).")
	if s.since.year < 2000:
		problems.append("First punch date must be 2000 or later.")
	if not 10 <= s.interval_seconds <= 86400:
		problems.append("Interval must be 10 to 86400 seconds.")
	return problems


def partition_table(raw):
	"""Parses an ESP32 partition table (the TABLE_SIZE bytes at TABLE_OFFSET) into {label: Partition}."""
	table = {}
	for i in range(0, len(raw) - 31, 32):
		magic, _type, _subtype, offset, size, label, _flags = struct.unpack("<2sBBII16sI", raw[i : i + 32])
		if magic != b"\xaa\x50":
			break  # 0xEBEB (checksum) or erased flash ends the table
		table[label.rstrip(b"\0").decode(errors="replace")] = Partition(offset, size)
	return table


def settings_image(s, size):
	"""The settings as an NVS partition image of `size` bytes, read by firmware/src/settings.cpp."""
	from esp_idf_nvs_partition_gen import nvs_partition_gen as nvs

	out = io.BytesIO()
	# The generator keeps the last page free for NVS's own use: give it the size less that page.
	with contextlib.redirect_stdout(io.StringIO()), nvs.nvs_open(out, size - 0x1000, nvs.Page.VERSION2) as image:
		image.write_namespace(NAMESPACE)
		image.write_entry("version", str(SETTINGS_VERSION), "u8")
		image.write_entry("wifi_ssid", s.wifi_ssid, "string")
		image.write_entry("wifi_pass", s.wifi_password, "string")
		image.write_entry("dev_host", s.device_host, "string")
		image.write_entry("dev_port", str(s.device_port), "u16")
		image.write_entry("comm_key", str(s.comm_key), "u32")
		image.write_entry("site_url", s.site_url.rstrip("/"), "string")
		image.write_entry("root_ca", s.root_ca.encode() + b"\0", "binary")
		image.write_entry("since", s.since.strftime("%Y%m%d"), "u32")
		image.write_entry("interval", str(s.interval_seconds), "u32")
	data = out.getvalue()
	if len(data) != size:
		raise ValueError(f"settings image is {len(data)} bytes, the partition {size}")
	return data


def flash_regions(image, table, settings_bin, resend):
	"""[(offset, bytes)] to write. Skips the image's "nvs" range so the upload position survives,
	unless `resend` asks to clear it. `image` is the merged firmware (flashed at 0) or None."""
	nvs, zkcfg = table.get("nvs"), table.get(NAMESPACE)
	if not nvs or not zkcfg:
		raise ValueError("this firmware has no settings partition: use a zk-agent.bin from this release or later")
	regions = []
	if image:
		if len(image) > zkcfg.offset:
			raise ValueError("the firmware image overlaps the settings partition")
		regions.append((0, image[: nvs.offset]))
		if len(image) > nvs.end:
			regions.append((nvs.end, image[nvs.end :]))
	if resend:
		regions.append((nvs.offset, b"\xff" * nvs.size))
	regions.append((zkcfg.offset, settings_bin))
	return sorted(regions)


# --- Certificates -------------------------------------------------------------------------------


def _subject(cert, field):
	return next((v for rdn in cert.get("subject", ()) for k, v in rdn if k == field), "")


def site_root_certificates(site_url):
	"""Returns (pem, names): the root(s) the board needs to check the site."""
	url = urlparse(site_url)
	if not url.hostname:
		raise ValueError("enter the site address first")
	ctx = ssl.create_default_context()
	with socket.create_connection((url.hostname, url.port or 443), timeout=15) as sock:
		with ctx.wrap_socket(sock, server_hostname=url.hostname) as tls:
			if not hasattr(tls, "get_verified_chain"):
				raise RuntimeError("fetching certificates needs Python 3.13 or later")
			root = tls.get_verified_chain()[-1]  # DER
	store = list(zip(ctx.get_ca_certs(binary_form=True), ctx.get_ca_certs()))
	info = next((c for der, c in store if der == root), {})
	org = _subject(info, "organizationName")

	if org in LETS_ENCRYPT_ORGS:
		pems = []
		for link, sha256 in LETS_ENCRYPT_ROOTS.items():
			with urllib.request.urlopen(link, timeout=15, context=ctx) as r:
				pem = r.read().decode()
			if hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest() != sha256:
				raise RuntimeError(f"{link} does not match its pinned fingerprint")
			pems.append(pem.strip())
		bundle = "\n".join(pems) + "\n"
		return bundle, pem_names(bundle)

	# Other CAs: the root the site uses now, plus its siblings from the same organisation in this
	# computer's trust store (where a CA's next root usually already is).
	siblings = [(der, c) for der, c in store if org and der != root and _subject(c, "organizationName") == org]
	pem = "".join(ssl.DER_cert_to_PEM_cert(d) for d in [root] + [der for der, _ in siblings])
	return pem, [_subject(info, "commonName") or "site root"] + [_subject(c, "commonName") for _, c in siblings]


def pem_names(pem):
	"""Common names of the CA certificates in a PEM bundle. Raises ssl.SSLError if it is not one."""
	ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
	ctx.load_verify_locations(cadata=pem)
	return [_subject(c, "commonName") or "certificate" for c in ctx.get_ca_certs()]


# --- Wi-Fi from this computer -------------------------------------------------------------------


def _run(cmd, timeout=15):
	flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flash from the Windows .exe
	try:
		return subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout, creationflags=flags).stdout
	except (OSError, subprocess.SubprocessError):
		return ""


def parse_nmcli(output):
	"""`nmcli -t -f ACTIVE,SSID,CHAN device wifi list` -> ([(ssid, is_2ghz)], connected ssid)."""
	networks, connected = [], ""
	for line in output.splitlines():
		fields = [f.replace("\\:", ":").replace("\\\\", "\\") for f in re.split(r"(?<!\\):", line)]
		if len(fields) >= 3 and fields[1]:
			networks.append((fields[1], fields[2].isdigit() and int(fields[2]) <= 14))
			if fields[0] == "yes":
				connected = fields[1]
	return networks, connected


def parse_netsh(output):
	"""`netsh wlan show networks mode=bssid` -> [(ssid, is_2ghz)]. Labels are translated on
	non-English Windows: the SSID line and the "GHz" band are not; the channel is a fallback."""
	networks, ssid, band = [], None, False
	for line in output.splitlines() + ["SSID 0 : "]:
		m = re.match(r"^SSID \d+ : (.*)$", line)
		if m:
			if ssid:
				networks.append((ssid, band))
			ssid, band = m.group(1).strip(), False
		elif re.search(r"\b2\.4\s*GHz", line) or re.match(r"^\s*Channel\s*:\s*([1-9]|1[0-4])\s*$", line):
			band = True
	return networks


def parse_netsh_connected(output):
	"""The SSID in `netsh wlan show interfaces` (the BSSID line is the access point's address)."""
	m = re.search(r"^\s*SSID\s*:\s*(.+)$", output, re.M)
	return m.group(1).strip() if m else ""


def scan_wifi():
	"""Returns ([(ssid, is_2ghz)], the network this computer is on). 2.4 GHz networks first (the
	ESP32 has no 5 GHz radio), otherwise in the order the OS lists them (strongest first)."""
	if sys.platform == "win32":
		networks = parse_netsh(_run(["netsh", "wlan", "show", "networks", "mode=bssid"]))
		connected = parse_netsh_connected(_run(["netsh", "wlan", "show", "interfaces"]))
	else:
		networks, connected = parse_nmcli(_run(["nmcli", "-t", "-f", "ACTIVE,SSID,CHAN", "device", "wifi", "list", "--rescan", "auto"]))
	seen, ordered = set(), []
	for ssid, is_2ghz in sorted(networks, key=lambda n: not n[1]):
		if ssid not in seen:
			seen.add(ssid)
			ordered.append((ssid, is_2ghz))
	return ordered, connected


def saved_wifi_password(ssid):
	"""The password this computer stored for `ssid`, if it will tell us."""
	if sys.platform == "win32":
		if '"' in ssid:
			return ""
		out = _run(f'netsh wlan show profile name="{ssid}" key=clear')
		m = re.search(r"Key Content\s*:\s*(.*)", out)
		return m.group(1).strip() if m else ""
	return _run(["nmcli", "-s", "-g", "802-11-wireless-security.psk", "connection", "show", "id", ssid], timeout=5).strip()


# --- Board --------------------------------------------------------------------------------------


def serial_ports():
	"""[(device, description, is_usb)]: USB serial ports (the chips used on ESP32 boards first), then
	the others, such as the PC's own COM1 / ttyS0, in case a driver does not report its USB IDs."""
	from serial.tools import list_ports

	ports = sorted(list_ports.comports(), key=lambda p: (p.vid is None, p.vid not in USB_SERIAL_VIDS, p.device))
	return [(p.device, p.description or p.device, p.vid is not None) for p in ports]


def windows_usb_devices():
	"""[(name, device_id, error_code)] of the USB devices plugged into this Windows PC."""
	out = _run(
		[
			"powershell", "-NoProfile", "-NonInteractive", "-Command",
			"Get-CimInstance Win32_PnPEntity | Where-Object { $_.DeviceID -like 'USB\\VID_*' } | "
			"Select-Object Name,DeviceID,ConfigManagerErrorCode | ConvertTo-Json -Compress",
		],
		timeout=60,
	)
	try:
		rows = json.loads(out or "[]")
	except ValueError:
		return []
	rows = [rows] if isinstance(rows, dict) else rows
	return [(r.get("Name") or "USB device", r.get("DeviceID") or "", r.get("ConfigManagerErrorCode") or 0) for r in rows]


def usb_problem(devices):
	"""Why no serial port showed up for the board, from windows_usb_devices()."""
	boards = [d for d in devices if re.search(r"VID_(10C4|1A86|0403|303A)", d[1], re.I)]
	missing = [d for d in boards if d[2]]
	if missing:
		name, device_id, code = missing[0]
		vid = re.search(r"VID_([0-9A-F]{4})", device_id, re.I).group(1).upper()
		return f"The board is plugged in ({name}) but Windows has no driver for it (error {code}). Install {USB_DRIVERS[vid]}, then unplug and replug the board."
	if boards:
		return f"Windows sees the board ({boards[0][0]}) but gives it no COM port: unplug and replug it, or reinstall its driver."
	return "Windows sees no ESP32 board on USB: try another cable (many are charge-only) or another USB socket."


class _LogWriter(io.TextIOBase):
	"""Turns esptool's output into log lines; a carriage return redraws the last line (progress)."""

	def __init__(self, log):
		self.log, self.buf = log, ""

	def write(self, text):
		self.buf += text
		while True:
			m = re.search(r"[\r\n]", self.buf)
			if not m:
				break
			line, sep, self.buf = self.buf[: m.start()], m.group(), self.buf[m.end() :]
			if line.strip():
				self.log(line, replace=sep == "\r")
		return len(text)

	def isatty(self):
		return False


def esptool_run(port, args, log):
	import esptool

	argv = ["--chip", CHIP, "--port", port, "--baud", BAUD, *args]
	writer = _LogWriter(log)
	with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
		try:
			esptool.main(argv)
		except SystemExit as e:
			if e.code:
				raise RuntimeError(f"esptool stopped (exit {e.code})") from None
		except Exception as e:  # esptool's FatalError and serial errors: show them, not a traceback
			raise RuntimeError(str(e)) from None
		finally:
			writer.write("\n")


def install(port, image_path, s, resend, log):
	"""Flashes the firmware (when image_path is set) and the settings. Raises on any problem."""
	problems = validate(s)
	if problems:
		raise ValueError("\n".join(problems))
	with tempfile.TemporaryDirectory() as tmp:
		image = Path(image_path).read_bytes() if image_path else None
		if image:
			table = partition_table(image[TABLE_OFFSET : TABLE_OFFSET + TABLE_SIZE])
		else:
			log("Reading the board's partition table")
			esptool_run(port, ["read-flash", hex(TABLE_OFFSET), hex(TABLE_SIZE), f"{tmp}/table.bin"], log)
			table = partition_table(Path(tmp, "table.bin").read_bytes())
		zkcfg = table.get(NAMESPACE)
		regions = flash_regions(image, table, settings_image(s, zkcfg.size) if zkcfg else b"", resend)
		args = ["write-flash"]
		for i, (offset, data) in enumerate(regions):
			path = Path(tmp, f"region{i}.bin")
			path.write_bytes(data)
			args += [hex(offset), str(path)]
		log("Writing " + ", ".join(f"{len(d) // 1024} KB at {hex(o)}" for o, d in regions))
		esptool_run(port, args, log)


def watch_board(port, log, stop, seconds=120):
	"""Resets the board and shows its serial log until `stop` is set or `seconds` pass."""
	import serial

	ser = serial.Serial()
	ser.port, ser.baudrate, ser.timeout = port, 115200, 0.5
	ser.dtr = ser.rts = False  # both released: the board runs instead of waiting in its bootloader
	ser.open()
	try:
		ser.rts = True  # EN low: reset, so the log starts at boot
		time.sleep(0.1)
		ser.rts = False
		end = time.monotonic() + seconds
		while not stop.is_set() and time.monotonic() < end:
			line = ser.readline().decode(errors="replace").rstrip()
			if line:
				log("board: " + line)
	finally:
		ser.close()


# --- Updates ------------------------------------------------------------------------------------


def _get(url, timeout=30):
	request = urllib.request.Request(url, headers={"User-Agent": f"zk-agent-setup/{VERSION}"})
	return urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context())


def release_asset():
	return "zk-agent-setup-windows.zip" if sys.platform == "win32" else "zk-agent-setup-linux.zip"


def newest_release(releases, current=VERSION):
	"""From GitHub's release list: (version, {asset name: download link}) of the newest setup tool
	release after `current`, or None."""
	best = None
	for r in releases:
		m = re.fullmatch(r"esp32-setup-(\d+)", r.get("tag_name") or "")
		if not m or r.get("draft") or r.get("prerelease"):
			continue
		version = int(m.group(1))
		if version > current and (best is None or version > best[0]):
			best = (version, {a["name"]: a["browser_download_url"] for a in r.get("assets", [])})
	return best


def check_for_update():
	with _get(f"https://api.github.com/repos/{REPO}/releases?per_page=30", timeout=15) as r:
		return newest_release(json.load(r))


def checksum_of(sums, name):
	"""The SHA-256 for `name` in a sha256sum file, or None."""
	for line in sums.splitlines():
		parts = line.split()
		if len(parts) == 2 and parts[1].lstrip("*") == name:
			return parts[0].lower()
	return None


def _download(url, log, label):
	chunks, done = [], 0
	log(f"Downloading {label}")
	with _get(url, timeout=60) as r:
		total = int(r.headers.get("Content-Length") or 0)
		while chunk := r.read(1 << 16):
			chunks.append(chunk)
			done += len(chunk)
			if total:
				log(f"Downloading {label}: {done * 100 // total}%", replace=True)
	return b"".join(chunks)


def apply_update(archive, exe):
	"""Puts the files of a release zip (bytes) beside `exe`, the app among them in place of `exe`.
	Windows cannot overwrite a running .exe but can rename it: the old app becomes <name>.old, which
	the next start deletes."""
	folder = exe.parent
	with zipfile.ZipFile(io.BytesIO(archive)) as z:
		# Top-level files only, so nothing lands outside the app's folder.
		names = [n for n in z.namelist() if n == Path(n).name and n not in ("", ".", "..")]
		app = next((n for n in names if n.startswith("zk-agent-setup")), None)
		if not app:
			raise ValueError("the update has no app in it")
		for name in sorted(names, key=lambda n: n == app):  # the app last: a failure before leaves it working
			target = exe if name == app else folder / name
			new = target.with_name(target.name + ".new")
			new.write_bytes(z.read(name))
			if name != app:
				os.replace(new, target)
				continue
			new.chmod(0o755)
			old = exe.with_name(exe.name + ".old")
			old.unlink(missing_ok=True)
			exe.rename(old)
			try:
				os.replace(new, exe)
			except OSError:
				old.rename(exe)
				raise


def update_app(release, log):
	"""Downloads release (from check_for_update), checks it against its published SHA-256 and
	installs it over the running app. Returns the app's path, to start it."""
	version, assets = release
	name = release_asset()
	if name not in assets or CHECKSUMS not in assets:
		raise RuntimeError(f"release {version} has no {name} or {CHECKSUMS}")
	expected = checksum_of(_download(assets[CHECKSUMS], log, CHECKSUMS).decode(), name)
	archive = _download(assets[name], log, name)
	if not expected or hashlib.sha256(archive).hexdigest() != expected:
		raise RuntimeError(f"{name} does not match its checksum: not installed")
	exe = Path(sys.executable).resolve()
	apply_update(archive, exe)
	return exe


def start_app(exe):
	env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")  # a new app, not a child of this one
	if sys.platform == "win32":
		subprocess.Popen([str(exe)], env=env, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
	else:
		subprocess.Popen([str(exe)], env=env, start_new_session=True)


def remove_old_app():
	if getattr(sys, "frozen", False):
		with contextlib.suppress(OSError):
			Path(sys.executable + ".old").unlink(missing_ok=True)


# --- Window -------------------------------------------------------------------------------------


def default_image():
	here = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
	if len(sys.argv) > 1:
		return sys.argv[1]
	found = sorted(here.glob("zk-agent*.bin"))
	return str(found[-1]) if found else ""


def main():
	import tkinter as tk
	from tkinter import filedialog, messagebox, ttk

	remove_old_app()
	root = tk.Tk()
	root.title(f"ZK pull agent setup {VERSION}")
	events = queue.Queue()
	state = {"root_ca": "", "busy": False, "stop": threading.Event(), "usb_ports": set()}

	def log(text, replace=False):
		events.put(("log", text, replace))

	frm = ttk.Frame(root, padding=10)
	frm.grid(sticky="nsew")
	root.columnconfigure(0, weight=1)
	root.rowconfigure(0, weight=1)
	frm.columnconfigure(1, weight=1)
	row = 0

	def section(title):
		nonlocal row
		ttk.Label(frm, text=title, font=("TkDefaultFont", 10, "bold")).grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 2))
		row += 1

	def field(label, var, widget=None, button=None):
		nonlocal row
		ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8))
		w = widget or ttk.Entry(frm, textvariable=var)
		w.grid(row=row, column=1, sticky="ew", pady=2)
		if button:
			ttk.Button(frm, text=button[0], command=button[1]).grid(row=row, column=2, sticky="ew", padx=(6, 0))
		row += 1
		return w

	port_var, image_var = tk.StringVar(), tk.StringVar(value=default_image())
	site_var, ca_var = tk.StringVar(value="https://"), tk.StringVar(value="none yet")
	ssid_var, pass_var, show_var = tk.StringVar(), tk.StringVar(), tk.BooleanVar()
	host_var, dport_var, key_var = tk.StringVar(), tk.StringVar(value="4370"), tk.StringVar(value="0")
	since_var = tk.StringVar(value=f"{date.today().year}-01-01")
	interval_var, resend_var = tk.StringVar(value="120"), tk.BooleanVar()

	section("Board")
	port_box = field("USB port", port_var, ttk.Combobox(frm, textvariable=port_var), ("Refresh", lambda: refresh_ports()))

	def browse():
		path = filedialog.askopenfilename(filetypes=[("Firmware", "*.bin"), ("All files", "*")])
		if path:
			image_var.set(path)

	field("Firmware (zk-agent.bin)", image_var, button=("Browse...", browse))

	section("Site")
	field("Site address", site_var, button=("Get certificate", lambda: background(fetch_ca)))

	def load_pem():
		path = filedialog.askopenfilename(filetypes=[("Certificates", "*.pem *.crt"), ("All files", "*")])
		if not path:
			return
		pem = Path(path).read_text(errors="replace")
		try:
			set_ca(pem, pem_names(pem))
		except (ssl.SSLError, ValueError) as e:
			messagebox.showerror("Root certificate", f"Not a PEM certificate file.\n\n{e}")

	field("Root certificate", ca_var, ttk.Label(frm, textvariable=ca_var, foreground="gray25"), ("Load file...", load_pem))

	section("Wi-Fi (2.4 GHz)")
	ssid_box = field("Network", ssid_var, ttk.Combobox(frm, textvariable=ssid_var), ("Scan", lambda: background(scan)))
	pass_entry = field("Password", pass_var, ttk.Entry(frm, textvariable=pass_var, show="•"))
	ttk.Checkbutton(frm, text="show", variable=show_var, command=lambda: pass_entry.config(show="" if show_var.get() else "•")).grid(
		row=row - 1, column=2, sticky="w", padx=(6, 0)
	)

	section("Attendance device (Menu > COMM. > Ethernet)")
	field("Device IP", host_var)
	field("Port", dport_var)
	field("Comm Key (0 if none)", key_var)

	section("Upload")
	field("Send punches from (YYYY-MM-DD)", since_var)
	field("Every (seconds)", interval_var)
	ttk.Checkbutton(frm, text="Send all punches again (clears the board's upload position)", variable=resend_var).grid(
		row=row, column=0, columnspan=3, sticky="w", pady=2
	)
	row += 1

	buttons = ttk.Frame(frm)
	buttons.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(10, 6))
	row += 1
	install_btn = ttk.Button(buttons, text="Install firmware + settings", command=lambda: start_install(True))
	settings_btn = ttk.Button(buttons, text="Write settings only", command=lambda: start_install(False))
	watch_btn = ttk.Button(buttons, text="Show board log", command=lambda: start_watch())
	stop_btn = ttk.Button(buttons, text="Stop log", command=lambda: state["stop"].set())
	for i, b in enumerate((install_btn, settings_btn, watch_btn, stop_btn)):
		b.grid(row=0, column=i, padx=(0, 6))

	text = tk.Text(frm, height=14, width=90, wrap="word", state="disabled", font=("TkFixedFont", 9))
	text.grid(row=row, column=0, columnspan=3, sticky="nsew")
	bar = ttk.Scrollbar(frm, command=text.yview)
	bar.grid(row=row, column=3, sticky="ns")
	text.config(yscrollcommand=bar.set)
	frm.rowconfigure(row, weight=1)
	row += 1

	footer = ttk.Frame(frm)
	footer.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(6, 0))
	footer.columnconfigure(0, weight=1)
	ttk.Label(footer, text=f"Version {VERSION} · {COPYRIGHT} · MIT License", foreground="gray35").grid(row=0, column=0, sticky="w")
	update_btn = ttk.Button(footer, text="Check for updates", command=lambda: background(check_updates, False))
	update_btn.grid(row=0, column=1, sticky="e")

	def set_ca(pem, names):
		state["root_ca"] = pem
		ca_var.set(f"{len(names)}: " + ", ".join(names) if names else "none yet")

	def refresh_ports(quiet=False):
		ports = serial_ports()
		usb = [(p, d) for p, d, is_usb in ports if is_usb]
		new = [(p, d) for p, d in usb if p not in state["usb_ports"]]
		state["usb_ports"] = {p for p, _ in usb}
		port_box["values"] = [p for p, _, _ in ports]
		if new and not state["busy"]:
			port_var.set(new[0][0])
			log(f"Board found: {new[0][0]} ({new[0][1]})")
		elif port_var.get() and port_var.get() not in port_box["values"]:
			port_var.set("")
		if not usb and not quiet:
			log("No USB serial port found: plug the board in with a data cable (many are charge-only).")
			if sys.platform == "win32":
				background(lambda: log(usb_problem(windows_usb_devices())))

	def poll_ports():  # a board plugged in after the window opened shows up by itself
		refresh_ports(quiet=True)
		root.after(2000, poll_ports)

	def background(fn, *args):
		threading.Thread(target=fn, args=args, daemon=True).start()

	def fetch_ca():
		log(f"Fetching the root certificate of {site_var.get()} ...")
		try:
			pem, names = site_root_certificates(site_var.get().strip())
		except Exception as e:
			log(f"Certificate: {e}")
			return
		events.put(("ca", pem, names))
		log("Certificate: " + ", ".join(names))

	def scan():
		log("Scanning Wi-Fi from this computer ...")
		networks, connected = scan_wifi()
		events.put(("wifi", networks, connected))
		if not networks:
			log("No networks found (no Wi-Fi here, or Windows needs Location turned on): type the name.")
		elif any(not g for _, g in networks):
			log("Networks marked (5 GHz) will not work: the ESP32 only has a 2.4 GHz radio.")

	def on_ssid(_event=None):
		if ssid_var.get().endswith(" (5 GHz)"):
			log("That network is 5 GHz only here: the board cannot join it. Pick its 2.4 GHz network.")
		name = ssid_var.get().removesuffix(" (5 GHz)")
		ssid_var.set(name)
		if not pass_var.get():
			background(lambda: events.put(("password", saved_wifi_password(name))))

	ssid_box.bind("<<ComboboxSelected>>", on_ssid)

	def read_settings():
		try:
			return Settings(
				site_url=site_var.get().strip(),
				root_ca=state["root_ca"],
				wifi_ssid=ssid_var.get(),
				wifi_password=pass_var.get(),
				device_host=host_var.get().strip(),
				device_port=int(dport_var.get()),
				comm_key=int(key_var.get()),
				since=date.fromisoformat(since_var.get().strip()),
				interval_seconds=int(interval_var.get()),
			)
		except ValueError as e:
			messagebox.showerror("Settings", f"Port, Comm Key and interval are numbers; the date is YYYY-MM-DD.\n\n{e}")
			return None

	def set_busy(busy):
		state["busy"] = busy
		for b in (install_btn, settings_btn, watch_btn, update_btn):
			b.state(["disabled"] if busy else ["!disabled"])

	def start_install(with_firmware):
		s = read_settings()
		if not s:
			return
		# A missing certificate is fetched in the background below, before writing.
		problems = [p for p in validate(s) if s.root_ca or not p.startswith("No root certificate")]
		if problems:
			messagebox.showerror("Settings", "\n".join(problems))
			return
		image = image_var.get().strip() if with_firmware else ""
		if with_firmware and not Path(image).is_file():
			messagebox.showerror("Firmware", "Choose the firmware file (zk-agent.bin).")
			return
		if not port_var.get():
			messagebox.showerror("Board", "No USB port: plug the board in and press Refresh.")
			return
		set_busy(True)
		state["stop"].clear()

		def work():
			try:
				if not s.root_ca:
					log(f"Fetching the root certificate of {s.site_url} ...")
					s.root_ca, names = site_root_certificates(s.site_url)
					events.put(("ca", s.root_ca, names))
					log("Certificate: " + ", ".join(names))
				install(port_var.get(), image, s, resend_var.get(), log)
				log("Done. Board log follows (Stop log to end it); expect 'wifi: connected' and 'sync:' lines.")
				watch_board(port_var.get(), log, state["stop"])
			except Exception as e:
				log(f"Failed: {e}")
				log("If it hangs on 'Connecting...', hold the board's BOOT button until writing starts.")
			events.put(("idle",))

		background(work)

	def start_watch():
		if not port_var.get():
			return
		set_busy(True)
		state["stop"].clear()

		def work():
			try:
				watch_board(port_var.get(), log, state["stop"])
			except Exception as e:
				log(f"Board log: {e}")
			events.put(("idle",))

		background(work)

	def check_updates(quiet):
		try:
			release = check_for_update()
		except Exception as e:
			if not quiet:
				log(f"Update check: {e}")
			return
		if release:
			events.put(("update", release))
		elif not quiet:
			log(f"Version {VERSION} is the newest.")

	def offer_update(release):
		version = release[0]
		if not getattr(sys, "frozen", False):
			log(f"Version {version} of the setup tool is out: update this copy of the source (git pull).")
			return
		if state["busy"]:
			log(f"Version {version} of the setup tool is out: press Check for updates when the board is done.")
			return
		if not messagebox.askyesno("Update", f"Version {version} of the setup tool is out (this is {VERSION}).\n\nUpdate now? The app restarts."):
			return
		set_busy(True)

		def work():
			try:
				events.put(("restart", update_app(release, log)))
			except Exception as e:
				log(f"Update failed: {e}")
				events.put(("idle",))

		background(work)

	def pump():
		while not events.empty():
			ev = events.get()
			if ev[0] == "log":
				_, line, replace = ev
				text.config(state="normal")
				if replace and text.get("end-2l", "end-1c").strip():
					text.delete("end-2l", "end-1c")
				text.insert("end", line + "\n")
				text.see("end")
				text.config(state="disabled")
			elif ev[0] == "ca":
				set_ca(ev[1], ev[2])
			elif ev[0] == "wifi":
				ssid_box["values"] = [n if g else f"{n} (5 GHz)" for n, g in ev[1]]
				bands = dict(ev[1])
				if ev[2] and not ssid_var.get():  # most likely the office network the board will use
					if bands.get(ev[2], True):
						ssid_var.set(ev[2])
						on_ssid()
					else:
						log(f"This computer is on {ev[2]}, which is 5 GHz only here: pick the office's 2.4 GHz network.")
			elif ev[0] == "password" and ev[1] and not pass_var.get():
				pass_var.set(ev[1])
				log("Wi-Fi password: filled in from this computer's saved networks.")
			elif ev[0] == "idle":
				set_busy(False)
			elif ev[0] == "update":
				offer_update(ev[1])
			elif ev[0] == "restart":
				start_app(ev[1])
				root.destroy()
				return
		root.after(100, pump)

	refresh_ports()
	background(scan)
	background(check_updates, True)
	pump()
	root.after(2000, poll_ports)
	root.mainloop()


if __name__ == "__main__":
	main()
