# Copyright (c) 2026 Abdulrahman Aloqayli
# SPDX-License-Identifier: MIT

import frappe
from frappe import _
from frappe.model.document import Document

from zk_push import timezones


class ZKPushSettings(Document):
	def validate(self):
		self.device_time_zone = timezones.check(self.device_time_zone)
		if (self.repeat_punch_seconds or 0) < 0:
			frappe.throw(_("Ignore Repeat Punches Within cannot be negative."))
