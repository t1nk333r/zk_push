"""Integration tests. Run on a bench with ERPNext + HRMS:  bench --site <site> run-tests --app zk_push"""

from datetime import datetime
from urllib.parse import urlencode

import frappe
from erpnext.setup.doctype.employee.test_employee import make_employee
from frappe.tests.utils import FrappeTestCase
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from zk_push import iclock, processing

SN = "ZKTEST000001"


def device_request(endpoint, method="GET", body=None, **params):
	builder = EnvironBuilder(
		path=f"/iclock/{endpoint}",
		method=method,
		query_string=urlencode({"SN": SN, **params}),
		data=body,
		content_type="text/plain",
	)
	response = iclock.handle(endpoint, Request(builder.get_environ()))
	return response.status_code, response.get_data(as_text=True)


class TestIclock(FrappeTestCase):
	def setUp(self):
		frappe.db.delete("ZK Punch", {"device": SN})
		frappe.db.delete("ZK Request Log", {"device": SN})
		frappe.db.delete("ZK Device Command", {"device": SN})
		frappe.db.delete("ZK Device", {"name": SN})
		self.employee = make_employee("zk_push_test@example.com", company="_Test Company")
		frappe.db.set_value("Employee", self.employee, "attendance_device_id", "7001")
		frappe.db.delete("Employee Checkin", {"employee": self.employee})

	def enable_device(self, **values):
		frappe.db.set_value("ZK Device", SN, {"enabled": 1, **values})

	def test_unknown_device_is_registered_disabled(self):
		status, text = device_request("cdata", options="all")
		self.assertEqual(status, 403)
		device = frappe.get_doc("ZK Device", SN)
		self.assertFalse(device.enabled)
		self.assertTrue(device.auto_registered)
		self.assertTrue(device.last_seen)

	def test_invalid_sn_rejected(self):
		builder = EnvironBuilder(path="/iclock/cdata", query_string="SN=../x")
		self.assertEqual(iclock.handle("cdata", Request(builder.get_environ())).status_code, 400)

	def test_handshake(self):
		device_request("cdata")
		self.enable_device(att_stamp="555")
		status, text = device_request("cdata", options="all", pushver="2.4.1")
		self.assertEqual(status, 200)
		self.assertIn(f"GET OPTION FROM: {SN}", text)
		self.assertIn("ATTLOGStamp=555", text)
		self.assertEqual(frappe.db.get_value("ZK Device", SN, "push_version"), "2.4.1")

	def test_punch_creates_checkin_and_ignores_resend(self):
		device_request("cdata")
		self.enable_device()
		body = "7001\t2026-11-05 08:00:00\t0\t1\t0\n9999\t2026-11-05 08:01:00\t0\t1\t0\nbroken line\n"

		status, text = device_request("cdata", "POST", body, table="ATTLOG", Stamp="100")
		self.assertEqual((status, text), (200, "OK: 3"))
		self.assertEqual(frappe.db.get_value("ZK Device", SN, "att_stamp"), "100")

		processing.process_pending()

		created = frappe.get_doc("ZK Punch", f"{SN}-7001-20261105080000")
		self.assertEqual(created.status, "Created")
		checkin = frappe.get_doc("Employee Checkin", created.employee_checkin)
		self.assertEqual(checkin.employee, self.employee)
		self.assertEqual(checkin.time, datetime(2026, 11, 5, 8, 0, 0))
		self.assertEqual(checkin.device_id, SN)
		self.assertFalse(checkin.log_type)

		self.assertEqual(frappe.db.get_value("ZK Punch", f"{SN}-9999-20261105080100", "status"), "Unmatched")

		# Device resends the same batch: nothing new is created.
		device_request("cdata", "POST", body, table="ATTLOG", Stamp="100")
		processing.process_pending()
		self.assertEqual(frappe.db.count("Employee Checkin", {"employee": self.employee}), 1)
		self.assertEqual(frappe.db.count("ZK Punch", {"device": SN}), 2)

	def test_disabled_device_punches_rejected(self):
		device_request("cdata")
		status, _ = device_request("cdata", "POST", "7001\t2026-11-05 08:00:00\t0\t1\n", table="ATTLOG")
		self.assertEqual(status, 403)
		self.assertEqual(frappe.db.count("ZK Punch", {"device": SN}), 0)

	def test_import_from_and_log_type_mode(self):
		device_request("cdata")
		self.enable_device(import_from="2026-11-01 00:00:00", log_type_mode="From Device Status")
		body = "7001\t2026-10-01 08:00:00\t0\t1\n7001\t2026-11-05 17:00:00\t1\t1\n"
		device_request("cdata", "POST", body, table="ATTLOG")
		processing.process_pending()

		self.assertEqual(frappe.db.get_value("ZK Punch", f"{SN}-7001-20261001080000", "status"), "Skipped")
		punch = frappe.get_doc("ZK Punch", f"{SN}-7001-20261105170000")
		self.assertEqual(punch.status, "Created")
		self.assertEqual(frappe.db.get_value("Employee Checkin", punch.employee_checkin, "log_type"), "OUT")

	def test_discard_before_import(self):
		device_request("cdata")
		self.enable_device(import_from="2026-01-01 00:00:00", discard_before_import=1)
		body = "7001\t2024-06-01 08:00:00\t0\t1\n7001\t2026-01-01 00:00:00\t0\t1\n7001\t2026-11-05 08:00:00\t0\t1\n"
		# Every line is acknowledged, so the device does not resend the discarded ones.
		self.assertEqual(device_request("cdata", "POST", body, table="ATTLOG"), (200, "OK: 3"))
		self.assertFalse(frappe.db.exists("ZK Punch", f"{SN}-7001-20240601080000"))
		self.assertEqual(frappe.db.count("ZK Punch", {"device": SN}), 2)

		# Without the flag, older punches are kept as Skipped.
		frappe.db.set_value("ZK Device", SN, "discard_before_import", 0)
		device_request("cdata", "POST", body, table="ATTLOG")
		self.assertEqual(frappe.db.get_value("ZK Punch", f"{SN}-7001-20240601080000", "status"), "Skipped")

	def test_mapping_employee_later_requeues_unmatched(self):
		device_request("cdata")
		self.enable_device()
		device_request("cdata", "POST", "8001\t2026-11-05 08:00:00\t0\t1\n", table="ATTLOG")
		processing.process_pending()
		name = f"{SN}-8001-20261105080000"
		self.assertEqual(frappe.db.get_value("ZK Punch", name, "status"), "Unmatched")

		employee = frappe.get_doc("Employee", self.employee)
		employee.attendance_device_id = "8001"
		employee.save()  # on_update hook requeues (runs immediately in tests)

		self.assertEqual(frappe.db.get_value("ZK Punch", name, "status"), "Created")

	def test_getrequest_stores_info(self):
		device_request("cdata")
		self.enable_device()
		status, text = device_request("getrequest", INFO="Ver 6.60,27,30,12767")
		self.assertEqual((status, text), (200, "OK"))
		self.assertEqual(frappe.db.get_value("ZK Device", SN, "device_info"), "Ver 6.60,27,30,12767")

	def test_existing_checkin_is_duplicate_not_error(self):
		# Regression: blank log_type is stored as '' and must still be detected.
		frappe.get_doc(
			{"doctype": "Employee Checkin", "employee": self.employee, "time": "2026-11-05 09:00:00"}
		).insert(ignore_permissions=True)
		device_request("cdata")
		self.enable_device()
		device_request("cdata", "POST", "7001\t2026-11-05 09:00:00\t0\t1\n", table="ATTLOG")
		processing.process_pending()
		punch = frappe.get_doc("ZK Punch", f"{SN}-7001-20261105090000")
		self.assertEqual(punch.status, "Duplicate")
		self.assertEqual(punch.attempts, 1)
		self.assertTrue(punch.processed_at)
		self.assertTrue(punch.employee_name)

	def test_device_commands_round_trip(self):
		device_request("cdata")
		self.enable_device()
		from zk_push import api

		name = api.queue_command(SN, "INFO")
		status, text = device_request("getrequest")
		self.assertEqual(text, f"C:{name}:INFO\n")
		self.assertEqual(frappe.db.get_value("ZK Device Command", name, "status"), "Sent")
		self.assertEqual(device_request("getrequest")[1], "OK")  # nothing else queued

		body = f"ID={name}&Return=0&CMD=INFO\n~DeviceName=MB20-VL\nUserCount=27\n"
		self.assertEqual(device_request("devicecmd", "POST", body), (200, "OK"))
		command = frappe.get_doc("ZK Device Command", name)
		self.assertEqual((command.status, command.return_code), ("Done", 0))
		self.assertIn("UserCount=27", command.response)

		failed = api.queue_command(SN, "REBOOT")
		device_request("getrequest")
		device_request("devicecmd", "POST", f"ID={failed}&Return=-1002&CMD=REBOOT\n")
		self.assertEqual(frappe.db.get_value("ZK Device Command", failed, "status"), "Failed")

	def test_invalid_stamp_is_not_stored(self):
		device_request("cdata")
		self.enable_device(att_stamp="100")
		for stamp in ("1\nTransFlag=None", "x" * 200):
			status, _ = device_request("cdata", "POST", "7001\t2026-11-05 08:00:00\t0\t1\n", table="ATTLOG", Stamp=stamp)
			self.assertEqual(status, 200)
		self.assertEqual(frappe.db.get_value("ZK Device", SN, "att_stamp"), "100")

	def test_all_unreadable_lines_are_kept(self):
		device_request("cdata")
		self.enable_device()
		body = "".join(f"bad line {i}\n" for i in range(60))
		self.assertEqual(device_request("cdata", "POST", body, table="ATTLOG"), (200, "OK: 60"))
		log = frappe.get_last_doc("Error Log", filters={"method": ("like", f"%unreadable ATTLOG%{SN}%")})
		self.assertIn("bad line 59", log.error)

	def test_sent_command_cannot_be_reset(self):
		device_request("cdata")
		self.enable_device()
		from zk_push import api

		name = api.queue_command(SN, "REBOOT")
		device_request("getrequest")
		command = frappe.get_doc("ZK Device Command", name)
		command.status = "Pending"
		with self.assertRaises(frappe.ValidationError):
			command.save()
		self.assertEqual(device_request("getrequest")[1], "OK")

	def test_multiline_command_is_failed_not_sent(self):
		device_request("cdata")
		self.enable_device()
		name = frappe.get_doc({"doctype": "ZK Device Command", "device": SN, "command": "CHECK"}).insert().name
		frappe.db.set_value("ZK Device Command", name, "command", "CHECK\nC:99:CLEAR LOG")
		self.assertEqual(device_request("getrequest")[1], "OK")
		self.assertEqual(frappe.db.get_value("ZK Device Command", name, "status"), "Failed")

	def test_stale_form_save_keeps_server_fields(self):
		device_request("cdata")
		self.enable_device(att_stamp="100")
		stale = frappe.get_doc("ZK Device", SN)
		device_request("cdata", "POST", "7001\t2026-11-05 08:00:00\t0\t1\n", table="ATTLOG", Stamp="500")
		stale.device_name = "Front door"
		stale.save()
		self.assertEqual(frappe.db.get_value("ZK Device", SN, "att_stamp"), "500")

	def test_serial_number_cannot_change(self):
		# Frappe keeps a field:-named field in sync with the name; set_only_once also locks it in the form.
		device_request("cdata")
		device = frappe.get_doc("ZK Device", SN)
		device.serial_no = "ZKTEST000002"
		device.save()
		self.assertEqual(frappe.db.get_value("ZK Device", SN, "serial_no"), SN)
		self.assertFalse(frappe.db.exists("ZK Device", "ZKTEST000002"))

	def test_reset_log_stamp(self):
		device_request("cdata")
		self.enable_device(att_stamp="500")
		from zk_push import api

		name = api.reset_log_stamp(SN)
		self.assertFalse(frappe.db.get_value("ZK Device", SN, "att_stamp"))
		self.assertEqual(frappe.db.get_value("ZK Device Command", name, "command"), "CHECK")
		self.assertIn("ATTLOGStamp=None", device_request("cdata", options="all")[1])

	def test_custom_command_requires_system_manager(self):
		device_request("cdata")
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				frappe.get_doc({"doctype": "ZK Device Command", "device": SN, "command": "CLEAR LOG"}).insert()
		finally:
			frappe.set_user("Administrator")

	def test_request_log_only_when_debugging_or_failed(self):
		device_request("cdata")
		self.enable_device()
		device_request("getrequest")
		self.assertEqual(frappe.db.count("ZK Request Log", {"device": SN}), 0)

		frappe.db.set_value("ZK Device", SN, "debug_logging", 1)
		device_request("cdata", "POST", "7001\t2026-11-05 10:00:00\t0\t1\n", table="ATTLOG")
		log = frappe.get_last_doc("ZK Request Log", filters={"device": SN})
		self.assertEqual((log.method, log.endpoint, log.response_status, log.punches), ("POST", "cdata", 200, 1))
		self.assertIn("7001", log.request_body)
		self.assertEqual(log.response_body, "OK: 1")

	def test_map_pin(self):
		device_request("cdata")
		self.enable_device()
		device_request("cdata", "POST", "8002\t2026-11-05 08:00:00\t0\t1\n", table="ATTLOG")
		processing.process_pending()

		with self.assertRaises(frappe.ValidationError):  # employee already has 7001
			processing.map_pin(SN, "8002", self.employee)

		self.assertEqual(processing.map_pin(SN, "8002", self.employee, overwrite=True), 1)
		self.assertEqual(frappe.db.get_value("ZK Punch", f"{SN}-8002-20261105080000", "status"), "Created")

		other = make_employee("zk_push_test2@example.com", company="_Test Company")
		with self.assertRaises(frappe.ValidationError):  # pin already used by first employee
			processing.map_pin(SN, "8002", other)

	def test_summary_and_report(self):
		device_request("cdata")
		self.enable_device()
		device_request("cdata", "POST", "7001\t2026-11-05 08:00:00\t0\t1\n9101\t2026-11-05 08:00:00\t0\t1\n", table="ATTLOG")
		processing.process_pending()

		from zk_push import api
		from zk_push.zk_push.report.zk_unmatched_users.zk_unmatched_users import execute

		summary = api.device_summary(SN)
		self.assertTrue(summary["online"])
		self.assertEqual(summary["counts"], {"Created": 1, "Unmatched": 1})
		self.assertEqual(summary["unmatched_users"], 1)

		_columns, data = execute({"device": SN})
		self.assertEqual([(r.pin, r.punches) for r in data], [("9101", 1)])

	def test_retry_selected_and_skipped(self):
		device_request("cdata")
		self.enable_device(import_from="2026-11-01 00:00:00")
		device_request("cdata", "POST", "7001\t2026-10-01 08:00:00\t0\t1\n", table="ATTLOG")
		processing.process_pending()
		name = f"{SN}-7001-20261001080000"
		self.assertEqual(frappe.db.get_value("ZK Punch", name, "status"), "Skipped")

		frappe.db.set_value("ZK Device", SN, "import_from", None)
		from zk_push import api

		self.assertEqual(api.retry_punches(names=[name]), 1)
		self.assertEqual(frappe.db.get_value("ZK Punch", name, "status"), "Created")
		self.assertEqual(api.retry_punches(names=[name]), 0)  # Created punches are never redone
