# Copyright (c) 2026 Abdulrahman Aloqayli
# SPDX-License-Identifier: MIT

import frappe


def execute():
	"""Enabled devices with no Allowed IP Addresses keep accepting any address, now an explicit setting;
	checkins already made show the device's name instead of its serial number."""
	frappe.db.sql(
		"""update `tabZK Device` set accept_any_ip = 1
		where enabled = 1 and (allowed_ips is null or trim(allowed_ips) = '')"""
	)
	frappe.db.sql(
		"""update `tabEmployee Checkin` c join `tabZK Device` d on c.device_id = d.name
		set c.device_id = d.device_name
		where d.device_name is not null and d.device_name != '' and d.device_name != d.name"""
	)
