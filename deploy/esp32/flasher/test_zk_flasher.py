# Copyright (c) 2026 Abdulrahman Aloqayli
# SPDX-License-Identifier: MIT

"""Unit tests for the setup tool's logic (no board, no network, no window).

  python -m unittest -v test_zk_flasher     (in deploy/esp32/flasher, with requirements.txt installed)
"""

import struct
import unittest
from dataclasses import replace
from datetime import date

import zk_flasher as z

PEM = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
GOOD = z.Settings(
	site_url="https://erp.example.com",
	root_ca=PEM,
	wifi_ssid="office",
	wifi_password="password1",
	device_host="192.168.100.56",
	since=date(2026, 1, 1),
)


def table_bytes(*entries):
	"""A partition table as firmware/partitions.csv produces it, ended by an MD5 entry."""
	raw = b"".join(struct.pack("<2sBBII16sI", b"\xaa\x50", t, st, off, size, label.encode(), 0) for label, t, st, off, size in entries)
	return (raw + b"\xeb\xeb" + b"\xff" * 30).ljust(z.TABLE_SIZE, b"\xff")


TABLE = z.partition_table(
	table_bytes(
		("nvs", 1, 2, 0x9000, 0x5000),
		("otadata", 1, 0, 0xE000, 0x2000),
		("app0", 0, 0x10, 0x10000, 0x140000),
		("zkcfg", 1, 2, 0x290000, 0x8000),
	)
)


class PartitionTable(unittest.TestCase):
	def test_parses_until_checksum_entry(self):
		self.assertEqual(list(TABLE), ["nvs", "otadata", "app0", "zkcfg"])
		self.assertEqual(TABLE["zkcfg"], z.Partition(0x290000, 0x8000))
		self.assertEqual(TABLE["nvs"].end, 0xE000)

	def test_erased_flash_is_empty(self):
		self.assertEqual(z.partition_table(b"\xff" * z.TABLE_SIZE), {})


class FlashRegions(unittest.TestCase):
	image = bytes(range(256)) * (0x20000 // 256)  # stands in for a 128 KB merged image
	cfg = b"S" * 0x8000

	def test_install_skips_the_upload_position(self):
		regions = z.flash_regions(self.image, TABLE, self.cfg, resend=False)
		self.assertEqual([(o, len(d)) for o, d in regions], [(0, 0x9000), (0xE000, 0x20000 - 0xE000), (0x290000, 0x8000)])
		self.assertEqual(regions[1][1], self.image[0xE000:])

	def test_resend_clears_the_upload_position(self):
		regions = dict(z.flash_regions(self.image, TABLE, self.cfg, resend=True))
		self.assertEqual(regions[0x9000], b"\xff" * 0x5000)

	def test_settings_only(self):
		self.assertEqual(z.flash_regions(None, TABLE, self.cfg, resend=False), [(0x290000, self.cfg)])

	def test_old_firmware_without_settings_partition(self):
		old = {k: v for k, v in TABLE.items() if k != "zkcfg"}
		with self.assertRaisesRegex(ValueError, "no settings partition"):
			z.flash_regions(self.image, old, self.cfg, resend=False)

	def test_image_overlapping_settings(self):
		with self.assertRaisesRegex(ValueError, "overlaps"):
			z.flash_regions(b"\0" * 0x290001, TABLE, self.cfg, resend=False)


class Validate(unittest.TestCase):
	def test_good(self):
		self.assertEqual(z.validate(GOOD), [])

	def test_open_network_and_hex_key(self):
		self.assertEqual(z.validate(replace(GOOD, wifi_password="")), [])
		self.assertEqual(z.validate(replace(GOOD, wifi_password="ab" * 32)), [])

	def test_problems(self):
		cases = {
			"https://": replace(GOOD, site_url="http://erp.example.com"),
			"root certificate": replace(GOOD, root_ca=""),
			"1 to 32": replace(GOOD, wifi_ssid="x" * 33),
			"8 to 63": replace(GOOD, wifi_password="short"),
			"Device IP": replace(GOOD, device_host="192.168.1.1; rm"),
			"port": replace(GOOD, device_port=0),
			"Comm Key": replace(GOOD, comm_key=-1),
			"2000": replace(GOOD, since=date(1999, 12, 31)),
			"Interval": replace(GOOD, interval_seconds=5),
		}
		for expected, s in cases.items():
			with self.subTest(expected):
				problems = z.validate(s)
				self.assertEqual(len(problems), 1, problems)
				self.assertIn(expected, problems[0])


class SettingsImage(unittest.TestCase):
	def test_entries_and_size(self):
		data = z.settings_image(replace(GOOD, root_ca=PEM * 40), 0x8000)  # ~2 KB: spans NVS pages
		self.assertEqual(len(data), 0x8000)
		for key in (b"version", b"wifi_ssid", b"wifi_pass", b"dev_host", b"dev_port", b"comm_key", b"site_url", b"root_ca", b"since", b"interval"):
			self.assertIn(key.ljust(16, b"\0"), data, key)
		self.assertIn(b"https://erp.example.com\0", data)

	def test_trailing_slash_dropped(self):
		self.assertIn(b"https://erp.example.com\0", z.settings_image(replace(GOOD, site_url="https://erp.example.com/"), 0x8000))


class WifiScan(unittest.TestCase):
	def test_nmcli(self):
		out = "no:office:6\nyes:guest\\:5g:36\nno::11\nno:back\\\\slash:1\n"
		self.assertEqual(z.parse_nmcli(out), ([("office", True), ("guest:5g", False), ("back\\slash", True)], "guest:5g"))

	def test_netsh_connected(self):
		out = "    Name                   : Wi-Fi\n    SSID                   : office\n    BSSID                  : aa:bb:cc:dd:ee:ff\n"
		self.assertEqual(z.parse_netsh_connected(out), "office")
		self.assertEqual(z.parse_netsh_connected("    State : disconnected\n"), "")

	def test_netsh(self):
		out = """
Interface name : Wi-Fi
There are 2 networks currently visible.

SSID 1 : office
    Network type            : Infrastructure
    BSSID 1                 : aa:bb:cc:dd:ee:ff
         Signal             : 80%
         Band               : 2.4 GHz
         Channel            : 6

SSID 2 : office-5g
    BSSID 1                 : aa:bb:cc:dd:ee:00
         Band               : 5 GHz
         Channel            : 36
SSID 3 : old-windows
         Channel            : 11
"""
		self.assertEqual(z.parse_netsh(out), [("office", True), ("office-5g", False), ("old-windows", True)])


class UsbProblem(unittest.TestCase):
	def test_driver_missing(self):
		devices = [("USB Input Device", "USB\\VID_046D&PID_C52B\\5&1", 0), ("USB2.0-Serial", "USB\\VID_1A86&PID_7523\\5&2", 28)]
		message = z.usb_problem(devices)
		self.assertIn("USB2.0-Serial", message)
		self.assertIn("CH340", message)

	def test_no_com_port(self):
		self.assertIn("no COM port", z.usb_problem([("CP2102 USB to UART Bridge", "USB\\VID_10C4&PID_EA60\\0001", 0)]))

	def test_no_board(self):
		self.assertIn("another cable", z.usb_problem([("USB Input Device", "USB\\VID_046D&PID_C52B\\5&1", 0)]))
		self.assertIn("another cable", z.usb_problem([]))


if __name__ == "__main__":
	unittest.main()
