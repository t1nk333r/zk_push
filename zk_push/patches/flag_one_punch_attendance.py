# Copyright (c) 2026 Abdulrahman Aloqayli
# SPDX-License-Identifier: MIT

import frappe

from zk_push.attendance import FIELD, setup_fields


def execute():
	"""Flag the One Punch days already marked, except those an approved Attendance Request covers."""
	setup_fields()
	frappe.db.sql(
		f"""update `tabAttendance` set `{FIELD}` = 1
		where docstatus = 1 and status != 'On Leave'
		and (attendance_request is null or attendance_request = '')
		and (amended_from is null or amended_from = '')
		and ((in_time is null) != (out_time is null))
		and not exists (
			select 1 from `tabAttendance Request` ar
			where ar.docstatus = 1 and ar.employee = `tabAttendance`.employee
			and `tabAttendance`.attendance_date between ar.from_date and ar.to_date
		)"""
	)
