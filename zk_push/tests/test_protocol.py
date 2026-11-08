import unittest
from datetime import datetime

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


if __name__ == "__main__":
	unittest.main()
