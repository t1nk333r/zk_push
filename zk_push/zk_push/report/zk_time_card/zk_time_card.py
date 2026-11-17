"""Attendance laid out like the ZKTeco software's Time Card, with overtime.

One row per submitted Attendance. The figures come from the Attendance's in / out time and its
Shift Type (start, end, late entry / early exit grace periods); see zk_push.time_card for the rules.
"""

import frappe
from frappe import _
from frappe.utils import cint

from zk_push.time_card import Day, compute_day, hhmm, shift_bounds

# Statuses where the employee was there; the others (Absent, On Leave) get no figures.
WORKED = ("Present", "Half Day", "Work From Home")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	overtime_after = cint(filters.overtime_after) if filters.overtime_after not in (None, "") else 60
	missing_punch = cint(filters.missing_punch) if filters.missing_punch not in (None, "") else 60

	conditions = ["a.docstatus = 1", "a.attendance_date between %(from_date)s and %(to_date)s"]
	if filters.employee:
		conditions.append("a.employee = %(employee)s")
	if filters.company:
		conditions.append("a.company = %(company)s")

	rows = frappe.db.sql(
		f"""
		select a.name as attendance, a.employee, a.employee_name, a.attendance_date, a.status,
			a.shift, a.in_time, a.out_time,
			st.start_time, st.end_time, st.late_entry_grace_period, st.early_exit_grace_period
		from `tabAttendance` a
		left join `tabShift Type` st on st.name = a.shift
		where {" and ".join(conditions)}
		order by a.employee, a.attendance_date
		""",
		filters,
		as_dict=True,
	)

	data, totals = [], Day()
	for r in rows:
		day = Day()
		row = {
			"attendance": r.attendance,
			"employee": r.employee,
			"employee_name": r.employee_name,
			"attendance_date": r.attendance_date,
			"day": _(r.attendance_date.strftime("%A")),
			"status": r.status,
			"shift": r.shift,
			"clock_in": r.in_time.strftime("%H:%M") if r.in_time else "",
			"clock_out": r.out_time.strftime("%H:%M") if r.out_time else "",
		}
		if r.start_time is not None and r.end_time is not None:
			on_duty, off_duty = shift_bounds(r.attendance_date, r.start_time, r.end_time)
			row["on_duty"], row["off_duty"] = on_duty.strftime("%H:%M"), off_duty.strftime("%H:%M")
			if r.status in WORKED:
				day = compute_day(
					on_duty,
					off_duty,
					r.in_time,
					r.out_time,
					late_grace=cint(r.late_entry_grace_period),
					early_grace=cint(r.early_exit_grace_period),
					overtime_after=overtime_after,
					missing_punch=missing_punch,
				)
		for field in ("late", "early", "overtime", "work", "attended", "actual_attended"):
			setattr(totals, field, getattr(totals, field) + getattr(day, field))
			row[field] = hhmm(getattr(day, field))
		data.append(row)

	return columns(), data, None, None, summary(rows, totals)


def columns():
	def duration(fieldname, label):
		return {"fieldname": fieldname, "label": label, "fieldtype": "Data", "width": 80}

	return [
		{"fieldname": "employee", "label": _("Employee"), "fieldtype": "Link", "options": "Employee", "width": 130},
		{"fieldname": "employee_name", "label": _("Name"), "fieldtype": "Data", "width": 150},
		{"fieldname": "attendance_date", "label": _("Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "day", "label": _("Day"), "fieldtype": "Data", "width": 95},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 90},
		{"fieldname": "shift", "label": _("Shift"), "fieldtype": "Link", "options": "Shift Type", "width": 120},
		duration("on_duty", _("On Duty")),
		duration("off_duty", _("Off Duty")),
		duration("clock_in", _("Clock In")),
		duration("clock_out", _("Clock Out")),
		duration("late", _("Late")),
		duration("early", _("Early")),
		duration("overtime", _("OT Time")),
		duration("work", _("Work Time")),
		duration("attended", _("ATT Time")),
		duration("actual_attended", _("Actual ATT")),
		{"fieldname": "attendance", "label": _("Attendance"), "fieldtype": "Link", "options": "Attendance", "width": 150},
	]


def summary(rows, totals):
	statuses = [r.status for r in rows]
	return [
		{"label": _("Actual ATT"), "value": hhmm(totals.actual_attended) or "00:00", "datatype": "Data", "indicator": "Blue"},
		{"label": _("OT Time"), "value": hhmm(totals.overtime) or "00:00", "datatype": "Data"},
		{"label": _("Work Time"), "value": hhmm(totals.work) or "00:00", "datatype": "Data"},
		{"label": _("Late"), "value": hhmm(totals.late) or "00:00", "datatype": "Data", "indicator": "Red"},
		{"label": _("Early"), "value": hhmm(totals.early) or "00:00", "datatype": "Data", "indicator": "Red"},
		{"label": _("Present"), "value": sum(s in WORKED for s in statuses), "datatype": "Int", "indicator": "Green"},
		{"label": _("Absent"), "value": statuses.count("Absent"), "datatype": "Int", "indicator": "Red"},
	]
