# Copyright (c) 2026 t1nk333r
# SPDX-License-Identifier: MIT

"""One Punch on Attendance: the shift's auto attendance (after the shift end) marks a day with only a
clock in, or only a clock out, as Absent with no working hours. Such a day is flagged One Punch, with
a comment, so HR can find it (Attendance list, One Punch = Yes) and check it: approve an Attendance
Request if the employee worked, or leave it. An approved Attendance Request clears the flag."""

import frappe
from frappe import _
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


def on_attendance_request_submit(doc, method=None):
	"""HRMS updates the day's Attendance with db_set (no validate): clear the flag here."""
	frappe.db.set_value("Attendance", {"attendance_request": doc.name, FIELD: 1}, FIELD, 0)
