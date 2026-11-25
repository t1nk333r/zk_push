# Copyright (c) 2026 t1nk333r
# SPDX-License-Identifier: MIT

import unittest
from datetime import datetime, timedelta

from zk_push import protocol


class TestProtocol(unittest.TestCase):
	def test_parse_tab_separated(self):
		body = "1001\t2026-11-05 08:02:11\t0\t1\t0\t0\t0\n1002\t2026-11-05 17:30:00\t1\t15\t\t0\n"
		punches, invalid = protocol.parse_attlog(body)
		self.assertEqual(invalid, [])
		self.assertEqual(len(punches), 2)
		self.assertEqual(punches[0].pin, "1001")
		self.assertEqual(punches[0].time, datetime(2026, 11, 5, 8, 2, 11))
		self.assertEqual(punches[0].status, 0)
		self.assertEqual(punches[0].verify, 1)
		self.assertEqual(punches[1].status, 1)
		self.assertEqual(punches[1].verify, 15)

	def test_parse_space_separated_and_crlf(self):
		punches, invalid = protocol.parse_attlog("7 2026-01-02 03:04:05 0 1\r\n")
		self.assertEqual(invalid, [])
		self.assertEqual(punches[0].pin, "7")
		self.assertEqual(punches[0].time, datetime(2026, 1, 2, 3, 4, 5))

	def test_parse_minimal_line(self):
		punches, _ = protocol.parse_attlog("5\t2026-01-02 03:04:05")
		self.assertIsNone(punches[0].status)
		self.assertIsNone(punches[0].verify)

	def test_invalid_lines_are_reported_not_raised(self):
		body = "garbage\n1\tnot-a-date\t0\nbad pin!\t2026-01-02 03:04:05\t0\n\n9\t2026-01-02 03:04:05\t0\n"
		punches, invalid = protocol.parse_attlog(body)
		self.assertEqual([p.pin for p in punches], ["9"])
		self.assertEqual(len(invalid), 3)

	def test_parse_mixed_space_and_tab(self):
		punches, invalid = protocol.parse_attlog("1001 2026-11-05 08:02:11\t0\t1\n")
		self.assertEqual(invalid, [])
		self.assertEqual(punches[0].pin, "1001")
		self.assertEqual(punches[0].status, 0)
		self.assertEqual(punches[0].verify, 1)

	def test_only_cr_lf_split_lines(self):
		punches, invalid = protocol.parse_attlog("1\t2026-11-05 08:02:11\t0\t1\tab\x1ccd\r9\t2026-11-05 09:00:00\n")
		self.assertEqual(invalid, [])
		self.assertEqual([p.pin for p in punches], ["1", "9"])
		self.assertEqual(punches[0].workcode, "ab\x1ccd")

	def test_fields_are_bounded(self):
		punches, _ = protocol.parse_attlog(f"1\t2026-11-05 08:02:11\t99999999999\t١\t{'w' * 300}\n")
		self.assertIsNone(punches[0].status)
		self.assertIsNone(punches[0].verify)
		self.assertEqual(len(punches[0].workcode), protocol.MAX_WORKCODE)

	def test_device_text_is_escaped(self):
		punches, _ = protocol.parse_attlog("1\t2026-11-05 08:02:11\t0\t1\t<b>x</b>\n")
		self.assertEqual(punches[0].workcode, "&lt;b&gt;x&lt;/b&gt;")
		self.assertNotIn("<", punches[0].raw)

	def test_parse_operlog_users(self):
		body = (
			"USER PIN=1\tName=4 Esra\tPri=14\tCard=10648000\r\n"
			"OPLOG 4\t0\t2026-11-16 08:00:00\t0\t0\t0\t0\n"  # not a user line
			"USER PIN=22\tName=<b>Abd</b>\tPri=0\tPasswd=\tCard=\tGrp=1\n"
			"USER PIN=bad pin\tName=x\n"  # invalid pin
			"USER PIN=22\tName=Abdurahm\tPri=0\n"  # repeated pin: last line wins
		)
		users = protocol.parse_operlog_users(body)
		self.assertEqual(
			[(u.pin, u.name, u.is_admin, u.card) for u in users],
			[("1", "4 Esra", True, "10648000"), ("22", "Abdurahm", False, "")],
		)
		escaped = protocol.parse_operlog_users("USER PIN=5\tName=<b>A</b>\n")[0]
		self.assertEqual(escaped.name, "&lt;b&gt;A&lt;/b&gt;")

	def test_safe_text_never_cuts_an_entity(self):
		self.assertEqual(protocol.safe_text("ab<", 4), "ab")  # "ab&lt;" cut at 4 would leave "ab&l"
		self.assertEqual(protocol.safe_text("ab<", 6), "ab&lt;")
		self.assertEqual(protocol.safe_text("a&b", 10), "a&amp;b")
		punches, _ = protocol.parse_attlog(f"1\t2026-11-05 08:02:11\t0\t1\t{'w' * 138}<<\n")
		self.assertEqual(punches[0].workcode, "w" * 138)

	def test_trailing_newline_sn_and_pin_rejected(self):
		self.assertFalse(protocol.is_valid_sn("ABCDEF\n"))
		self.assertEqual(protocol.parse_attlog("١\t2026-11-05 08:02:11\n")[0], [])

	def test_punch_name_is_deterministic(self):
		t = datetime(2026, 11, 5, 8, 2, 11)
		self.assertEqual(protocol.punch_name("ABCD123456789", "1001", t), "ABCD123456789-1001-20261105080211")

	def test_map_log_type(self):
		mode = protocol.LOG_TYPE_FROM_DEVICE
		self.assertEqual(protocol.map_log_type(0, mode), "IN")
		self.assertEqual(protocol.map_log_type(1, mode), "OUT")
		self.assertEqual(protocol.map_log_type(2, mode), "OUT")
		self.assertIsNone(protocol.map_log_type(99, mode))
		self.assertIsNone(protocol.map_log_type(None, mode))
		self.assertIsNone(protocol.map_log_type(1, protocol.LOG_TYPE_BLANK))

	def test_valid_sn(self):
		self.assertTrue(protocol.is_valid_sn("ABCD123456789"))
		self.assertFalse(protocol.is_valid_sn(""))
		self.assertFalse(protocol.is_valid_sn(None))
		self.assertFalse(protocol.is_valid_sn("abc"))
		self.assertFalse(protocol.is_valid_sn("../../etc"))

	def test_handshake(self):
		text = protocol.build_handshake("ABCD123456789", "12345")
		self.assertTrue(text.startswith("GET OPTION FROM: ABCD123456789\n"))
		self.assertIn("ATTLOGStamp=12345\n", text)
		self.assertIn("TransFlag=TransData AttLog\n", text)
		self.assertIn("ATTLOGStamp=None\n", protocol.build_handshake("X1234567"))

	def test_handshake_ignores_invalid_stamp(self):
		for stamp in ("1\nTransFlag=None", "abc", "1" * 21, "١"):
			text = protocol.build_handshake("ABCD123456789", stamp)
			self.assertIn("ATTLOGStamp=None\n", text)
			self.assertEqual(text.count("TransFlag="), 1)
		self.assertTrue(protocol.is_valid_stamp("9999"))
		self.assertFalse(protocol.is_valid_stamp(None))

	def test_format_command(self):
		self.assertEqual(protocol.format_command(12, "REBOOT"), "C:12:REBOOT\n")
		with self.assertRaises(ValueError):
			protocol.format_command(13, "CHECK\nC:14:CLEAR LOG")

	def test_parse_devicecmd(self):
		body = "ID=3&Return=0&CMD=CHECK\nID=4&Return=0&CMD=INFO\n~DeviceName=MB20-VL\nUserCount=27\nID=5&Return=-1002&CMD=XYZ\n"
		results = protocol.parse_devicecmd(body)
		self.assertEqual([r["id"] for r in results], ["3", "4", "5"])
		self.assertEqual(results[0]["return"], 0)
		self.assertEqual(results[1]["extra"], ["~DeviceName=MB20-VL", "UserCount=27"])
		self.assertEqual(results[2]["return"], -1002)
		self.assertEqual(protocol.parse_devicecmd("junk\n"), [])

	def test_parse_endpoint(self):
		self.assertEqual(protocol.parse_endpoint("/iclock/cdata"), "cdata")
		self.assertEqual(protocol.parse_endpoint("iclock/cdata.aspx"), "cdata")
		self.assertEqual(protocol.parse_endpoint("iclock/getrequest/"), "getrequest")
		self.assertEqual(protocol.parse_endpoint("ICLOCK/DeviceCmd"), "devicecmd")
		self.assertIsNone(protocol.parse_endpoint("iclock"))
		self.assertIsNone(protocol.parse_endpoint("app/employee"))
		self.assertIsNone(protocol.parse_endpoint("iclock/a/b"))
		self.assertIsNone(protocol.parse_endpoint("iclock/<script>"))
		self.assertIsNone(protocol.parse_endpoint("iclock/" + "x" * 40))

	def test_ip_allowed(self):
		allowed = "10.0.0.0/24, 192.0.2.9\n2001:db8::/32 junk"
		self.assertTrue(protocol.ip_allowed("10.0.0.200", allowed))
		self.assertTrue(protocol.ip_allowed("192.0.2.9", allowed))
		self.assertTrue(protocol.ip_allowed("2001:db8::1", allowed))
		self.assertFalse(protocol.ip_allowed("10.0.1.1", allowed))
		self.assertFalse(protocol.ip_allowed("not an ip", allowed))
		self.assertFalse(protocol.ip_allowed(None, allowed))
		self.assertTrue(protocol.ip_allowed("203.0.113.5", ""))
		self.assertTrue(protocol.ip_allowed(None, None))
		self.assertFalse(protocol.ip_allowed("203.0.113.5", "junk"))  # a typo must not open it to all

	def test_mask_secrets(self):
		body = "USER PIN=5\tName=Card Holder\tPasswd=1234\tCard=99\r\nFP PIN=5\tTMP=QUJD\n"
		masked = protocol.mask_secrets(body)
		self.assertEqual(masked, "USER PIN=5\tName=Card Holder\tPasswd=***\tCard=***\r\nFP PIN=5\tTMP=***\n")
		self.assertEqual(protocol.mask_secrets(""), "")
		# Push 3.x field names, any letter case, and photos.
		body = "user uid=1\tcardno=12345\tpin=1\tpassword=999\nbiodata pin=1\ttmp=QUJD\nBIOPHOTO PIN=1\tContent=/9j/4A"
		self.assertEqual(
			protocol.mask_secrets(body),
			"user uid=1\tcardno=***\tpin=1\tpassword=***\nbiodata pin=1\ttmp=***\nBIOPHOTO PIN=1\tContent=***",
		)

	def test_time_suffix_is_not_shifted_into_status(self):
		for line in ("1 2024-01-01 08:00:00.5 1 15 0", "1 2024-01-01 08:00:001\t0", "1\t2024-01-01 08:00:00+03:00\t1"):
			self.assertIsNone(protocol.parse_attlog_line(line), line)
		punch = protocol.parse_attlog_line("1 2024-01-01 08:00:00\t1\t15")
		self.assertEqual((punch.status, punch.verify), (1, 15))

	def test_implausible_punch_years(self):
		self.assertIsNone(protocol.parse_attlog_line("1\t0001-01-01 08:00:00\t0"))
		self.assertIsNone(protocol.parse_attlog_line("1\t1999-12-31 23:59:59\t0"))
		later = (datetime.now() + timedelta(days=3)).strftime(protocol.TIME_FORMAT)
		self.assertIsNone(protocol.parse_attlog_line(f"1\t{later}\t0"))
		self.assertIsNotNone(protocol.parse_attlog_line("1\t2000-01-01 00:00:00\t0"))

	def test_command_ids(self):
		self.assertTrue(protocol.is_command_id("12"))
		for value in ("²", "", None, "1" * 19, "-1", "1 "):
			self.assertFalse(protocol.is_command_id(value), value)

	def test_reupload_command_is_exact(self):
		command = protocol.device_command("reupload", from_time="2026-10-01 00:00:00", to_time="2026-10-02 00:00:00")
		self.assertTrue(protocol.is_reupload_command(command))
		self.assertFalse(protocol.is_reupload_command(command + "\tPIN=1"))
		self.assertFalse(protocol.is_reupload_command("DATA QUERY ATTLOG "))
		self.assertFalse(protocol.is_reupload_command(None))

	def test_parse_devicecmd_is_bounded(self):
		body = "".join(f"ID={i}&Return=0&CMD=INFO\n" for i in range(150)) + "x\n" * 500
		results = protocol.parse_devicecmd(body, limit=100)
		self.assertEqual(len(results), 100)
		results = protocol.parse_devicecmd("ID=1&Return=0&CMD=INFO\n" + "x\n" * 500)
		self.assertEqual(len(results[0]["extra"]), protocol.MAX_RESULT_EXTRA_LINES)

	def test_device_commands(self):
		cmd = protocol.device_command
		self.assertEqual(cmd("log"), "LOG")
		self.assertEqual(cmd("clear_log"), "CLEAR LOG")
		self.assertEqual(
			cmd("reupload", from_time="2026-10-01 00:00:00", to_time=datetime(2026, 10, 31, 23, 59, 59)),
			"DATA QUERY ATTLOG StartTime=2026-10-01 00:00:00\tEndTime=2026-10-31 23:59:59",
		)
		self.assertEqual(
			cmd("set_user", pin="22", name="Abdulrahman", admin=1, card="123", password=""),
			"DATA UPDATE USERINFO PIN=22\tName=Abdulrahman\tPri=14\tPasswd=\tCard=123\tGrp=1"
			"\tTZ=0000000100000000\tVerify=0",
		)
		self.assertEqual(cmd("delete_user", pin="22"), "DATA DELETE USERINFO PIN=22")
		self.assertEqual(cmd("enroll_fingerprint", pin="22", finger="6"), "ENROLL_FP PIN=22\tFID=6\tRETRY=3\tOVERWRITE=0")

	def test_device_command_values_cannot_inject(self):
		cmd = protocol.device_command
		for bad in (
			lambda: cmd("delete_user", pin="22\nCLEAR DATA"),
			lambda: cmd("delete_user", pin=""),
			lambda: cmd("set_user", pin="22", name="A\tPri=14"),
			lambda: cmd("set_user", pin="22", name="Ali", card="12\t3"),
			lambda: cmd("reupload", from_time="2026-10-31", to_time="2026-10-01"),
			lambda: cmd("reupload", from_time="yesterday", to_time="2026-10-01"),
			lambda: cmd("enroll_fingerprint", pin="22", finger="10"),
			lambda: cmd("SHELL rm"),
		):
			with self.assertRaises(ValueError):
				bad()
		for action in protocol.ADMIN_ACTIONS:
			self.assertNotIn(action, ("info", "check", "log", "reboot", "reupload"))


if __name__ == "__main__":
	unittest.main()
