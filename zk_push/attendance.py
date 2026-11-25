# Copyright (c) 2026 Abdulrahman Aloqayli
# SPDX-License-Identifier: MIT

"""One Punch on Attendance: the shift's auto attendance (after the shift end) marks a day with only a
clock in, or only a clock out, as Absent with no working hours. Such a day is flagged One Punch, with
a comment, so HR can find it (Attendance list, One Punch = Yes) and check it: approve an Attendance
Request if the employee worked, or leave it. An approved Attendance Request clears the flag."""

import frappe
from frappe import _
from frappe.utils import add_days, date_diff, getdate
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

FIELD = "zk_one_punch"
CUSTOM_FIELDS = {
	"Attendance": [
		{
			"fieldname": FIELD,
			"label": "One Punch",
			"fieldtype": "Check",
			"insert_after": "out_time",
			"read_only": 1,
			"allow_on_submit": 1,
			"in_standard_filter": 1,
			"no_copy": 1,
			"description": "Only a clock in or only a clock out: HR needs to check this day. "
			"An approved Attendance Request clears it.",
		}
	]
}


def setup_fields():
	create_custom_fields(CUSTOM_FIELDS, update=True)


def after_install():
	"""Flag the One Punch days already marked (on an update the patch does this)."""
	from zk_push.patches.flag_one_punch_attendance import execute

	execute()


def remove_fields():
	for doctype, fields in CUSTOM_FIELDS.items():
		for field in fields:
			frappe.delete_doc("Custom Field", f"{doctype}-{field['fieldname']}", ignore_missing=True, force=True)


def one_punch(doc) -> bool:
	return (
		bool(doc.in_time) != bool(doc.out_time)
		and doc.status != "On Leave"
		and not doc.get("attendance_request")
		and not doc.amended_from  # HR cancelled and amended it: already checked
	)


def validate(doc, method=None):
	doc.set(FIELD, 1 if one_punch(doc) else 0)


def on_submit(doc, method=None):
	if doc.get(FIELD):
		missing = _("clock out") if doc.in_time else _("clock in")
		doc.add_comment("Comment", _("One punch: no {0}. HR needs to check this day.").format(missing))


def request_days(doc) -> list:
	"""The days of an Attendance Request HRMS acts on (AttendanceRequest.should_mark_attendance): not a
	holiday unless the request includes holidays, and not on approved leave."""
	from erpnext.setup.doctype.employee.employee import is_holiday

	days = []
	for i in range(date_diff(doc.to_date, doc.from_date) + 1):
		day = getdate(add_days(doc.from_date, i))
		if not doc.include_holidays and is_holiday(doc.employee, day, raise_exception=False):
			continue
		if doc.has_leave_record(day):
			continue
		days.append(day)
	return days


def on_attendance_request_submit(doc, method=None):
	"""An approved request means HR checked those days. HRMS updates the Attendance with db_set (no
	validate), and leaves it alone when it already has the requested status: clear the flag here."""
	days = request_days(doc)
	if days:
		frappe.db.sql(
			f"""update `tabAttendance` set `{FIELD}` = 0
			where employee = %s and attendance_date in %s and docstatus = 1 and `{FIELD}` = 1""",
			(doc.employee, tuple(days)),
		)


def on_attendance_request_cancel(doc, method=None):
	"""Flag again the days the request cleared. HRMS cancels the Attendance it linked to the request;
	the other days are still the ones marked from the punches."""
	days = request_days(doc)
	if not days:
		return
	for name in frappe.get_all(
		"Attendance",
		filters={"employee": doc.employee, "attendance_date": ["in", days], "docstatus": 1, FIELD: 0},
		pluck="name",
	):
		attendance = frappe.get_doc("Attendance", name)
		if one_punch(attendance):
			attendance.db_set(FIELD, 1, update_modified=False)


def on_leave_application_submit(doc, method=None):
	"""Approved leave turns the day's Attendance into On Leave with db_set (no validate): clear the
	flag, as validate would. Cancelling the leave cancels that Attendance, so nothing to restore."""
	if doc.status != "Approved":
		return
	frappe.db.sql(
		f"""update `tabAttendance` set `{FIELD}` = 0
		where leave_application = %s and status = 'On Leave' and docstatus = 1 and `{FIELD}` = 1""",
		doc.name,
	)
