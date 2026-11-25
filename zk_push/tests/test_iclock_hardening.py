# Copyright (c) 2026 Abdulrahman Aloqayli
# SPDX-License-Identifier: MIT

"""Integration tests for hostile or odd device requests. Run on a bench with ERPNext + HRMS:
bench --site <site> run-tests --app zk_push --module zk_push.tests.test_iclock_hardening"""

from unittest.mock import patch
from urllib.parse import urlencode

import frappe
from frappe.tests.utils import FrappeTestCase
from redis.exceptions import ConnectionError as RedisConnectionError
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from zk_push import iclock, processing

SN = "ZKTEST000002"
ATTLOG = "9001\t2026-11-05 08:02:11\t0\t1\t0\n"


def device_request(endpoint, method="GET", body=None, remote_addr="127.0.0.1", **params):
	builder = EnvironBuilder(
		path=f"/iclock/{endpoint}",
		method=method,
		query_string=urlencode({"SN": SN, **params}),
		data=body,
		content_type="text/plain",
		environ_base={"REMOTE_ADDR": remote_addr},
	)
	response = iclock.handle(endpoint, Request(builder.get_environ()))
	return response.status_code, response.get_data(as_text=True)


class TestIclockHardening(FrappeTestCase):
	def setUp(self):
		for doctype in ("ZK Punch", "ZK Request Log", "ZK Device Command", "ZK Device User"):
			frappe.db.delete(doctype, {"device": SN})
		frappe.db.delete("ZK Device", {"name": SN})
		device_request("cdata")  # registers the device, disabled
		frappe.db.set_value("ZK Device", SN, "enabled", 1)

	def logs(self):
		return frappe.get_all(
			"ZK Request Log",
			filters={"device": SN},
			fields=["endpoint", "request_body", "response_body", "body_size", "response_status"],
			order_by="creation asc",
		)

	def test_refused_address_writes_nothing(self):
		frappe.db.set_value("ZK Device", SN, "allowed_ips", "10.9.9.9")
		body = ATTLOG * 1000
		status, _text = device_request("cdata", "POST", body, remote_addr="198.51.100.9", table="ATTLOG")
		self.assertEqual(status, 403)
		self.assertEqual(self.logs(), [])
		self.assertFalse(frappe.db.exists("ZK Punch", {"device": SN}))

		# With Debug Logging on it is logged, still without the body.
		frappe.db.set_value("ZK Device", SN, "debug_logging", 1)
		device_request("cdata", "POST", body, remote_addr="198.51.100.9", table="ATTLOG")
		(log,) = self.logs()
		self.assertEqual(log.response_status, 403)
		self.assertFalse(log.request_body)

	def test_command_reply_is_masked_in_the_log(self):
		frappe.db.set_value("ZK Device", SN, "debug_logging", 1)
		frappe.get_doc(
			{
				"doctype": "ZK Device Command",
				"device": SN,
				"command": "DATA UPDATE USERINFO PIN=1\tName=A\tPri=0\tPasswd=4321\tCard=998877",
			}
		).insert()
		status, text = device_request("getrequest")
		self.assertEqual(status, 200)
		self.assertIn("Passwd=4321", text)  # the device still gets it
		response = self.logs()[-1].response_body
		self.assertIn("Passwd=***", response)
		self.assertNotIn("4321", response)
		self.assertNotIn("998877", response)

	def test_photo_upload_is_logged_by_size_only(self):
		frappe.db.set_value("ZK Device", SN, "debug_logging", 1)
		body = "PIN=1\tSN=x\tsize=10\tCMD=uploadphoto\tContent=" + "A" * 5000
		status, _text = device_request("cdata", "POST", body, table="ATTPHOTO")
		self.assertEqual(status, 200)
		log = self.logs()[-1]
		self.assertFalse(log.request_body)
		self.assertEqual(log.body_size, len(body))

	def test_odd_command_ids_are_ignored(self):
		for body in ("ID=²&Return=0&CMD=INFO", "ID=" + "9" * 5000 + "&Return=0&CMD=INFO"):
			status, text = device_request("devicecmd", "POST", body)
			self.assertEqual((status, text), (200, "OK"))

	def test_queue_outage_keeps_the_upload(self):
		with patch.object(processing, "enqueue_processing", side_effect=RedisConnectionError("down")):
			status, text = device_request("cdata", "POST", ATTLOG, table="ATTLOG")
		self.assertEqual((status, text), (200, "OK: 1"))
		self.assertEqual(frappe.db.count("ZK Punch", {"device": SN}), 1)
